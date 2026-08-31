"""The session engine — all of Jana's teaching logic, none of its I/O.

Why this module exists
----------------------
The terminal loop called `input()` in the middle of its scheduling logic. That
is fine for one caller and fatal for two: a web UI cannot block on stdin, so the
logic had to come out of the loop. What is left here has no printing, no
prompting and no HTTP — it answers two questions, `next_question()` and
`submit()`, and everything else is an adapter.

The consequence worth understanding
-----------------------------------
The engine holds *no* mutable session state. Progress is re-derived from the
event log on every call. That costs one indexed query per question and buys
three things:

  * the web tier is stateless, so it survives a restart mid-session and would
    scale horizontally without sticky sessions;
  * a session interrupted anywhere resumes exactly where it stopped, because
    the log already recorded it — no separate crash-recovery path exists to be
    wrong;
  * the terminal and the browser are the same session. Answer five questions in
    one, finish in the other.

This is the event-sourcing trade from DDIA ch. 11 taken to its conclusion: pay a
read cost on every request, and delete a whole category of state-synchronisation
bug in exchange.

The question set — prompts, answers, and shuffled options — is built once when
the session starts and stored on the session row. That is D5 in miniature:
generate ahead of time, serve from cache, keep the interactive path free of any
work that could miss the latency budget.
"""

from __future__ import annotations

import json
import random
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from jana import events, grader, scheduler
from jana.project import rebuild

NEW_REPS = 2        # correct answers needed to retire a new item today
REVIEW_REPS = 1
MAX_ASKS_PER_ITEM = 4   # a missed item must not eat the whole session
REQUEUE_GAP = 3         # slots to wait before re-testing a missed item

ARTICLES = ("der", "die", "das")

# A Wortliste item has gender but no English gloss; a corpus item has both.
# The placeholder gloss is written by jana/ingest/wortliste.py.
def has_gloss(gloss: str | None) -> bool:
    return bool(gloss) and not gloss.startswith("[")


def choose_task(gloss: str | None, gender: str | None, rung: int,
                principal_parts: str | None = None) -> str | None:
    """Which task this item can support. None means it is not yet schedulable.

    Three drill paths, each self-contained in German:
      * Items with a gloss climb the recognition → recall → production ladder.
      * Nouns without a gloss drill their article — the single highest-yield
        German failure mode — so 1,400 gender-annotated Wortliste nouns are
        useful on day one without translating a word of it.
      * Verbs without a gloss drill their principal parts (3rd person present,
        Präteritum, Perfekt) — conjugation is heavily tested at B1.
    """
    if not has_gloss(gloss):
        if gender:
            return "article"
        if principal_parts:
            return "conjugation"
        return None
    return {1: "recognise", 2: "cued_recall", 3: "production"}.get(rung, "production")


@dataclass(frozen=True)
class Question:
    item_id: int
    modality: str
    rung: int
    task_type: str
    prompt: str
    answer: str
    options: list[str] | None
    hint: str | None
    reps_needed: int


@dataclass(frozen=True)
class Progress:
    asked: int
    correct: int
    remaining: int
    planned: int


@dataclass(frozen=True)
class Outcome:
    correct: bool
    expected: str
    note: str
    attempt_event_id: int
    retired: bool


def _surface(row: sqlite3.Row) -> str:
    return f"{row['gender']} {row['lemma']}" if row["gender"] else row["lemma"]


# Labels for the three principal-part slots.
CONJUGATION_LABELS = ("Präsens (er/sie)", "Präteritum", "Perfekt")


def _build_question(row: sqlite3.Row, task: str, rung: int, reps: int,
                    gloss_pool: list[str], rng: random.Random) -> Question:
    lemma, gloss = row["lemma"], row["sense_gloss_en"]
    en_hint = gloss if has_gloss(gloss) else None
    if task == "article":
        hint = f"Meaning: {en_hint}" if en_hint else None
        return Question(row["item_id"], row["modality"], rung, task,
                        f"___ {lemma}", row["gender"], list(ARTICLES), hint, reps)
    if task == "conjugation":
        parts = [p.strip() for p in row["principal_parts"].split(",")]
        idx = rng.randrange(len(parts))
        label = CONJUGATION_LABELS[idx] if idx < len(CONJUGATION_LABELS) else f"Teil {idx + 1}"
        meaning_str = f" · ({en_hint})" if en_hint else ""
        first_letter_hint = f"{parts[idx][0]}{'·' * (len(parts[idx]) - 1)}"
        return Question(row["item_id"], row["modality"], rung, task,
                        f"{label} von {lemma}?", parts[idx], None,
                        f"{first_letter_hint}{meaning_str}", reps)
    if task == "recognise":
        distractors = [g for g in rng.sample(gloss_pool, min(12, len(gloss_pool)))
                       if g != gloss][:3]
        options = distractors + [gloss]
        rng.shuffle(options)
        return Question(row["item_id"], row["modality"], rung, task,
                        _surface(row), gloss, options, None, reps)
    hint = f"{lemma[0]}{'·' * (len(lemma) - 1)}" if task == "cued_recall" else None
    answer = _surface(row) if row["gender"] else lemma
    return Question(row["item_id"], row["modality"], rung, task,
                    gloss, answer, None, hint, reps)


class Engine:
    """One study session. Construct via `start` or `resume`, never directly."""

    def __init__(self, conn: sqlite3.Connection, session_id: int,
                 questions: list[Question]) -> None:
        self.conn = conn
        self.session_id = session_id
        self.questions = questions
        self._by_id = {q.item_id: q for q in questions}

    # ---------------------------------------------------------------- lifecycle
    @classmethod
    def start(cls, conn: sqlite3.Connection, modality: str = "text",
              seed: int | None = None) -> "Engine | None":
        rebuild(conn)          # recover anything a previous crash left in the log
        rng = random.Random(seed)
        reviews, fresh = scheduler.plan(conn, modality)
        rows = list(reviews) + list(fresh)
        if not rows:
            return None

        gloss_pool = [r[0] for r in conn.execute(
            "SELECT sense_gloss_en FROM item WHERE sense_gloss_en NOT LIKE '[%'")]

        questions: list[Question] = []
        for i, row in enumerate(rows):
            task = choose_task(row["sense_gloss_en"], row["gender"], row["rung"],
                               row["principal_parts"])
            if task is None:
                continue
            reps = REVIEW_REPS if i < len(reviews) else NEW_REPS
            questions.append(
                _build_question(row, task, row["rung"], reps, gloss_pool, rng))
        if not questions:
            return None

        cur = conn.execute(
            "INSERT INTO session (date, planned_json, started_at) VALUES (?, ?, ?)",
            (date.today().isoformat(),
             json.dumps([asdict(q) for q in questions], ensure_ascii=False),
             events.now()))
        session_id = int(cur.lastrowid)
        conn.commit()

        events.append(conn, "session_started",
                      {"session_id": session_id, "date": date.today().isoformat()})
        seen = {r["item_id"] for r in reviews}
        for q in questions:
            if q.item_id not in seen:
                events.append(conn, "item_introduced", {
                    "item_id": q.item_id, "modality": q.modality, "rung": q.rung})
        return cls(conn, session_id, questions)

    @classmethod
    def resume(cls, conn: sqlite3.Connection, session_id: int) -> "Engine":
        row = conn.execute("SELECT planned_json FROM session WHERE id = ?",
                           (session_id,)).fetchone()
        if row is None:
            raise KeyError(f"no session {session_id}")
        return cls(conn, session_id,
                   [Question(**q) for q in json.loads(row["planned_json"])])

    @classmethod
    def open_session(cls, conn: sqlite3.Connection) -> "Engine | None":
        """Today's unfinished session, if there is one."""
        row = conn.execute(
            "SELECT id FROM session WHERE ended_at IS NULL AND date = ?"
            " ORDER BY id DESC LIMIT 1", (date.today().isoformat(),)).fetchone()
        return cls.resume(conn, row["id"]) if row else None

    def finish(self) -> None:
        self.conn.execute("UPDATE session SET ended_at = ? WHERE id = ?",
                          (events.now(), self.session_id))
        self.conn.commit()
        events.append(self.conn, "session_ended", {
            "session_id": self.session_id, "n_attempts": self._tally()["asked"]})
        rebuild(self.conn)

    # ---------------------------------------------------------------- derivation
    def _tally(self) -> dict[str, Any]:
        """Re-derive this session's progress from the log. No cached state."""
        per_item: dict[int, dict[str, int]] = {}
        asked = correct = 0
        order = 0
        for _id, _ts, _kind, p in events.replay(self.conn, kinds=("attempt",)):
            if p.get("session_id") != self.session_id:
                continue
            order += 1
            counters = per_item.setdefault(p["item_id"], {"asks": 0, "hits": 0,
                                                          "last": 0})
            counters["asks"] += 1
            counters["last"] = order
            asked += 1
            if p["correct"]:
                counters["hits"] += 1
                correct += 1
        return {"per_item": per_item, "asked": asked, "correct": correct,
                "order": order}

    def _outstanding(self, tally: dict[str, Any]) -> list[Question]:
        out = []
        for q in self.questions:
            c = tally["per_item"].get(q.item_id, {"asks": 0, "hits": 0})
            if c["hits"] >= q.reps_needed or c["asks"] >= MAX_ASKS_PER_ITEM:
                continue
            out.append(q)
        return out

    def progress(self) -> Progress:
        tally = self._tally()
        return Progress(asked=tally["asked"], correct=tally["correct"],
                        remaining=len(self._outstanding(tally)),
                        planned=len(self.questions))

    def next_question(self) -> Question | None:
        tally = self._tally()
        outstanding = self._outstanding(tally)
        if not outstanding:
            return None
        # Prefer something not just asked, so a miss is re-tested rather than
        # echoed straight back at the learner.
        def staleness(q: Question) -> int:
            return tally["order"] - tally["per_item"].get(q.item_id, {}).get("last", 0)
        ready = [q for q in outstanding if staleness(q) >= REQUEUE_GAP]
        return max(ready or outstanding, key=staleness)

    # ---------------------------------------------------------------- mutation
    def submit(self, item_id: int, response: str,
               latency_ms: int | None = None) -> Outcome:
        q = self._by_id[item_id]
        verdict = grader.grade(q.task_type, response, q.answer)
        event_id = events.append(self.conn, "attempt", {
            "session_id": self.session_id,
            "item_id": item_id, "modality": q.modality, "rung": q.rung,
            "task_type": q.task_type, "exercise_id": None,
            "response": response, "correct": verdict.correct,
            "grade": events.GOOD if verdict.correct else events.AGAIN,
            "latency_ms": latency_ms,
        })
        tally = self._tally()
        counters = tally["per_item"].get(item_id, {"asks": 0, "hits": 0})
        retired = (counters["hits"] >= q.reps_needed
                   or counters["asks"] >= MAX_ASKS_PER_ITEM)
        return Outcome(verdict.correct, verdict.expected, verdict.note,
                       event_id, retired)

    def override(self, attempt_event_id: int, item_id: int,
                 learner_correct: bool) -> int:
        """The learner disagreeing with the grader. This log is the eval set."""
        q = self._by_id[item_id]
        return events.append(self.conn, "grader_override", {
            "attempt_event_id": attempt_event_id, "item_id": item_id,
            "machine_correct": not learner_correct,
            "learner_correct": learner_correct, "reference": q.answer,
        })
