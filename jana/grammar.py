"""Grammar practice, scheduled on the same forgetting curve as vocabulary.

A rule is forgotten on the same curve a word is, so it gets the same machinery
rather than a parallel scheduler that would drift out of step. Attempts go to
the event log with kind `attempt` and a `grammar_point_id`; jana/project.py
projects them into `grammar_state` with the same FSRS used for items.

What a grammar exercise is
--------------------------
Four shapes, because a rule tested one way is a rule half-learned:

  cloze     — the gap is exactly the thing the rule decides (an ending, an
              article, a preposition). Nothing else is missing.
  transform — rewrite the sentence into the target structure. This is the one
              that catches a rule known only as a table.
  choose    — three near-identical sentences, one correct. Trains the
              discrimination that the exam actually asks for.
  order     — reassemble a scrambled sentence. The only honest way to drill
              German word order, which no amount of gap-filling reaches.

Everything is validated against the syllabus like the rest, banked like the
rest, and anchored to the day's story where the vocabulary allows.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from jana import events, modules
from jana.ingest.grammar import curriculum

SHAPES = ("cloze", "transform", "choose", "order")

INSTRUCTION = """You write grammar exercises for a Goethe B1 candidate.

Write FOUR exercises about ONE grammar point, all using simple A1-A2 vocabulary,
in this order:

1. cloze — a German sentence with ONE gap written as ___ . The gap must be
   exactly what this rule decides (an ending, an article, a preposition).
2. transform — a German sentence, plus an instruction telling him what to change
   it into. Give the expected result.
3. choose — a German sentence in three near-identical versions, exactly one
   correct. The wrong ones must be wrong for THIS rule, not some other one.
4. order — a correct German sentence, given also as its words shuffled.

Every "why" is one short sentence of ENGLISH naming the rule."""

CONTRACT = """Reply with JSON only:
{"cloze": {"sentence": "Ich fahre ___ dem Zug.", "answer": "mit",
           "why": "mit always takes the dative."},
 "transform": {"sentence": "Er kauft ein Auto.", "instruction_de": "Schreiben Sie im Perfekt.",
               "instruction_en": "Rewrite in the perfect tense.",
               "answer": "Er hat ein Auto gekauft.", "why": "kaufen is a weak verb with haben."},
 "choose": {"options": ["Ich helfe dir.", "Ich helfe dich.", "Ich helfe du."],
            "answer": "Ich helfe dir.", "why": "helfen takes the dative."},
 "order": {"words": ["morgen", "fahre", "ich", "nach", "Berlin"],
           "answer": "Ich fahre morgen nach Berlin.",
           "why": "The conjugated verb is the second element."}}"""


def point(conn: sqlite3.Connection, point_id: int) -> dict | None:
    for entry in curriculum(conn):
        if entry["id"] == point_id:
            return entry
    return None


def due(conn: sqlite3.Connection, limit: int = 5) -> list[dict]:
    """Grammar points to practise: overdue first, then the next unlearned one.

    Prerequisites are enforced here rather than suggested. Adjective endings
    before cases is not a harder lesson, it is an impossible one, and letting
    the scheduler offer it because it happens to be due would waste the session.
    """
    points = curriculum(conn)
    state = {r["point_id"]: r for r in conn.execute("SELECT * FROM grammar_state")}
    known = {p["name"] for p in points
             if state.get(p["id"]) and (state[p["id"]]["reps"] or 0) >= 3}

    ready = [p for p in points
             if not p["requires"] or p["requires"] in known]

    overdue = [p for p in ready
               if p["id"] in state and (state[p["id"]]["due_at"] or "") <= events.now()]
    fresh = [p for p in ready if p["id"] not in state]

    out = []
    for entry in overdue + fresh:
        row = state.get(entry["id"])
        out.append({**entry,
                    "reps": row["reps"] if row else 0,
                    "due_at": row["due_at"] if row else None})
        if len(out) >= limit:
            break
    return out


def build(conn: sqlite3.Connection, point_id: int,
          day: int | None = None) -> dict[str, Any] | None:
    """Four exercises for one grammar point, from the bank or newly generated."""
    from jana import bank

    entry = point(conn, point_id)
    if entry is None:
        return None

    stored = conn.execute(
        """SELECT * FROM exercise
           WHERE grammar_point_id = ? AND coalesce(seen_count,0) = 0
           ORDER BY id LIMIT 1""", (point_id,)).fetchone()
    if stored is not None:
        task = bank._revive(stored)
        bank.mark_seen(conn, task["exercise_id"])
        return {**task, "point": entry}

    words = modules._vocab_line(modules._due_words(conn, 5, day=day))
    instruction = (f"{INSTRUCTION}\n\nThe grammar point is: {entry['name']} "
                   f"({entry['level']}) — {entry['description']}\n"
                   f"Use these words where they fit: {words}\n")
    parsed, tier, _cov, _rej, _new = modules._generate_validated(
        conn, instruction, CONTRACT)
    if parsed is None:
        return None

    task = {"modul": "grammatik", "teil": None, "day": day,
            "instruction_de": entry["name"], "instruction_en": entry["description"],
            "provenance": tier, "validated": True, "body": parsed}
    task["exercise_id"] = bank.save(conn, task, grammar_point_id=point_id)
    bank.mark_seen(conn, task["exercise_id"])
    return {**task, "point": entry}


def _normalise(text: str) -> str:
    text = re.sub(r"[^\w\sÄÖÜäöüß]", " ", text or "")
    return re.sub(r"\s+", " ", text).strip().casefold()


def check(shape: str, response: str, answer: str) -> dict[str, Any]:
    """Mark one grammar answer. Deterministic — no model needed to compare two strings."""
    got, want = _normalise(response), _normalise(answer)
    correct = got == want
    note = ""
    if not correct and shape in ("transform", "order"):
        # Word order is what these test, so say whether the words were right
        # and only the order wrong — that is a different mistake and a smaller one.
        if sorted(got.split()) == sorted(want.split()):
            note = "Right words, wrong order."
    return {"correct": correct, "expected": answer, "note": note}


def record(conn: sqlite3.Connection, point_id: int, correct: bool,
           shape: str, session_id: int | None = None,
           exercise_id: int | None = None) -> int:
    """Log a grammar attempt. Same event kind as vocabulary; same scheduler."""
    return events.append(conn, "attempt", {
        "session_id": session_id, "item_id": None, "grammar_point_id": point_id,
        "modality": "grammar", "rung": SHAPES.index(shape) + 1 if shape in SHAPES else 1,
        "task_type": f"grammar_{shape}", "exercise_id": exercise_id,
        "response": "", "correct": correct,
        "grade": events.GOOD if correct else events.AGAIN, "latency_ms": None,
    })


def progress(conn: sqlite3.Connection) -> list[dict]:
    """The curriculum with the learner's state on each point, for the map view."""
    points = curriculum(conn)
    state = {r["point_id"]: r for r in conn.execute("SELECT * FROM grammar_state")}
    known = {p["name"] for p in points
             if state.get(p["id"]) and (state[p["id"]]["reps"] or 0) >= 3}
    out = []
    for entry in points:
        row = state.get(entry["id"])
        locked = bool(entry["requires"]) and entry["requires"] not in known
        out.append({**entry,
                    "reps": row["reps"] if row else 0,
                    "lapses": row["lapses"] if row else 0,
                    "stability_days": round(row["stability"], 1)
                        if row and row["stability"] else None,
                    "due_at": row["due_at"] if row else None,
                    "locked": locked,
                    "started": row is not None})
    return out
