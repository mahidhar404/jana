"""HTTP adapter.

This module contains no teaching logic. It translates JSON into calls on
jana/core.py and back, which is the whole reason the core was extracted.

Two things here are not incidental:

*Answers never leave the server for a typed task.* The terminal could hold the
expected answer in the same process as the learner; a browser cannot. The
question sent to the client carries options (for multiple choice, where the
answer is one of four and revealing the set reveals nothing) but never the
`answer` field for a typed task. Grading stays server-side. This is the ordinary
rule that any client is untrusted, arriving the moment the UI moved.

*Every response carries the next question.* One round trip per answer instead of
two. The interactive budget is p99 < 200 ms end to end, and a second round trip
would spend a third of it on network for no reason.

A connection is opened per request rather than shared. SQLite connections are
not safe to move between threads, and FastAPI runs sync endpoints in a
threadpool — this is the cheap correct answer for a single-user local app, and
the thing that would have to change first if this ever served more than one
learner.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from jana import audio, bank, explain, grammar, llm, memory, modules, story, teile, tutor, wordlookup
from jana import events
from jana.core import Engine, Question
from jana.db import EXAM_DATE, connect, init_db
from jana.project import retrievability_on

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Jana", docs_url="/api/docs")


def get_db():
    conn = connect()
    init_db(conn)
    try:
        yield conn
    finally:
        conn.close()


def db() -> sqlite3.Connection:
    conn = connect()
    init_db(conn)
    return conn


class Answer(BaseModel):
    session_id: int
    item_id: int
    response: str
    latency_ms: int | None = None


class ChatTurn(BaseModel):
    message: str = ""
    history: list[dict[str, str]] = []
    session_id: int | None = None


class StorySay(BaseModel):
    text: str
    session_id: int | None = None
    day: int | None = None


class GrammarAnswer(BaseModel):
    point_id: int
    shape: str
    response: str
    answer: str
    exercise_id: int | None = None
    session_id: int | None = None


class SelfGrade(BaseModel):
    item_id: int
    grade: int          # FSRS scale: 1 again, 2 hard, 3 good, 4 easy
    modality: str = "text"
    rung: int = 1
    latency_ms: int | None = None
    session_id: int | None = None


class StoryReply(BaseModel):
    """The reply request carries no text — the other character supplies it."""
    day: int | None = None


class Text(BaseModel):
    text: str


class ModuleAnswer(BaseModel):
    response: str
    reference: str = ""
    item_ids: list[int] = []
    prompt: str = ""
    session_id: int | None = None
    teil: int | None = None


class Override(BaseModel):
    session_id: int
    item_id: int
    attempt_event_id: int
    learner_correct: bool


def public(q: Question | None) -> dict[str, Any] | None:
    """The client's view of a question — deliberately missing `answer`."""
    if q is None:
        return None
    data = asdict(q)
    if not q.options:
        data.pop("answer")
    else:
        data.pop("answer")          # the answer is among the options anyway
    return data


def snapshot(engine: Engine | None) -> dict[str, Any]:
    if engine is None:
        return {"session_id": None, "question": None, "progress": None,
                "done": True}
    progress = engine.progress()
    question = engine.next_question()
    return {
        "session_id": engine.session_id,
        "question": public(question),
        "progress": asdict(progress),
        "done": question is None,
    }


APP_DIR = STATIC / "app"

# The React build is served from the same origin as the API, so there is one
# process in production and no CORS surface. `npm run dev` proxies /api to this
# server, so the same code runs in both modes with no build-time switch.
if (APP_DIR / "assets").is_dir():
    app.mount("/app/assets", StaticFiles(directory=APP_DIR / "assets"),
              name="app-assets")


@app.get("/")
def index() -> FileResponse:
    built = APP_DIR / "index.html"
    return FileResponse(built if built.exists() else STATIC / "index.html")


@app.get("/app")
@app.get("/app/")
def app_index() -> FileResponse:
    return FileResponse(APP_DIR / "index.html")


@app.get("/legacy")
def legacy() -> FileResponse:
    """The pre-React page, kept until the new one has been used for a week."""
    return FileResponse(STATIC / "index.html")


@app.get("/api/state")
def state(conn: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    engine = Engine.open_session(conn)
    if engine is None:
        return {"session_id": None, "question": None, "progress": None,
                "done": True, "stats": stats_for(conn)}
    return {**snapshot(engine), "stats": stats_for(conn)}


@app.post("/api/session")
def new_session(conn: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    engine = Engine.open_session(conn) or Engine.start(conn)
    if engine is None:
        raise HTTPException(404, "nothing due and no new items available")
    return {**snapshot(engine), "stats": stats_for(conn)}


@app.post("/api/answer")
def answer(payload: Answer, conn: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    engine = Engine.resume(conn, payload.session_id)
    outcome = engine.submit(payload.item_id, payload.response, payload.latency_ms)
    body = {**snapshot(engine), "outcome": asdict(outcome)}
    if body["done"]:
        engine.finish()
        body["stats"] = stats_for(conn)
    return body


@app.post("/api/override")
def override(payload: Override, conn: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    """The learner overruling the grader. This log is the §7 eval set."""
    engine = Engine.resume(conn, payload.session_id)
    event_id = engine.override(payload.attempt_event_id, payload.item_id,
                               payload.learner_correct)
    return {"logged": event_id}


@app.get("/api/stats")
def stats(conn: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    return stats_for(conn)


def stats_for(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute(
        """SELECT (SELECT count(*) FROM item) AS items,
                  (SELECT count(*) FROM item_state) AS tracked,
                  (SELECT count(*) FROM item_state
                    WHERE due_at <= datetime('now')) AS due,
                  (SELECT count(*) FROM event WHERE kind='attempt') AS attempts,
                  (SELECT count(*) FROM session WHERE ended_at IS NOT NULL) AS sessions
        """).fetchone()
    forecast = retrievability_on(conn, f"{EXAM_DATE}T09:00:00+00:00")
    return {
        **dict(row),
        "exam_date": EXAM_DATE,
        "predicted_items_on_exam_day": round(sum(r for _, r in forecast)),
        "strong_on_exam_day": sum(1 for _, r in forecast if r >= 0.9),
    }


def ensure_session(conn: sqlite3.Connection) -> int | None:
    """Today's session, starting one if needed.

    Every mode writes into the same session. Without this the tutor detected
    which target words the learner produced and then dropped them, because
    logging was gated on a session id the chat client never had — the
    conversation looked like studying and changed nothing.
    """
    engine = Engine.open_session(conn) or Engine.start(conn)
    return engine.session_id if engine else None


# ------------------------------------------------------------------ Gespräch
@app.post("/api/chat")
def chat(payload: ChatTurn, conn: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    """The conversational tutor. Scheduled: it is handed today's due items."""
    session_id = payload.session_id or ensure_session(conn)
    turn = tutor.reply(conn, payload.history, payload.message, session_id)
    return {**asdict(turn), "session_id": session_id, "stats": stats_for(conn)}


# --------------------------------------------------------------------- Lesen
@app.get("/api/lesen")
def lesen(teil: int | None = None, day: int | None = None,
          conn: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    if teil is not None:
        return asdict(modules.lesen_teil(conn, teil, day=day))
    return asdict(modules.lesen(conn, day=day))


@app.post("/api/lesen/answer")
def lesen_answer(payload: ModuleAnswer, conn: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    correct = payload.response.strip() == payload.reference.strip()
    modules.log_module_attempt(conn, payload.session_id or ensure_session(conn),
                               payload.item_ids, "lesen", correct, payload.response)
    return {"correct": correct, "expected": payload.reference}


# --------------------------------------------------------------------- Hören
@app.get("/api/hoeren")
def hoeren(teil: int | None = None, day: int | None = None,
           conn: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    """Returns text. The browser speaks it — see the note in jana/modules.py."""
    if teil is not None:
        return asdict(modules.hoeren_teil(conn, teil, day=day))
    return asdict(modules.hoeren(conn, day=day))


@app.post("/api/hoeren/answer")
def hoeren_answer(payload: ModuleAnswer, conn: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    if payload.teil in (1, 2):
        correct = payload.response.strip() == payload.reference.strip()
        result = {"correct": correct, "expected": payload.reference}
    else:
        result = modules.grade_hoeren(payload.response, payload.reference)
        
    modules.log_module_attempt(conn, payload.session_id or ensure_session(conn),
                               payload.item_ids, "hoeren", result["correct"],
                               payload.response)
    return result


# ----------------------------------------------------------------- Schreiben
@app.get("/api/schreiben")
def schreiben(teil: int | None = None, day: int | None = None,
              conn: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    """Anchored to the day's scene when there is one; the fixed Teil bank otherwise."""
    if day is not None:
        return modules.schreiben_task(conn, day=day, teil=teil or 1)
    task, english, t = modules.schreiben_prompt(teil)
    return {"task": task, "en": english, "min_words": 40, "teil": t}


@app.post("/api/schreiben/grade")
def schreiben_grade(payload: ModuleAnswer) -> dict[str, Any]:
    teil = payload.teil if payload.teil is not None else 1
    return modules.grade_schreiben(payload.response, teil=teil)


# ------------------------------------------------------------------ Sprechen
@app.get("/api/sprechen")
def sprechen(teil: int | None = None, day: int | None = None,
             conn: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    if day is not None and (teil or 3) == 3:
        return {"teil": 3, "data": modules.sprechen_task(conn, day=day, teil=3)}
    return modules.sprechen_prompt(teil)


@app.post("/api/sprechen/grade")
def sprechen_grade(payload: ModuleAnswer) -> dict[str, Any]:
    teil = payload.teil if payload.teil is not None else 3
    return modules.grade_sprechen(payload.response, payload.prompt, teil=teil)


# -------------------------------------------------------------------- Dictionary & Grammar Explanation
class ExplainRequest(BaseModel):
    text: str


@app.get("/api/lookup")
def lookup(word: str, conn: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    """Instant lookup of any German token (lemma, gender, English, grammar note)."""
    from jana.lexicon import lookup_token
    return lookup_token(conn, word)


@app.post("/api/explain")
def explain_sentence(payload: ExplainRequest,
                     conn: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    """On-demand pedagogical grammar breakdown of a German sentence."""
    from jana.lexicon import WORD, lookup_token
    tokens = WORD.findall(payload.text)
    word_cards = [lookup_token(conn, t) for t in tokens[:12]]
    # Computed locally, always — the alignment is a dictionary walk, and a
    # model asked for it is slower and occasionally wrong about its own input.
    aligned = " | ".join(w["gloss"] or w["surface"]
                         for w in explain.literal(conn, payload.text))
    
    prompt = (
        f"You are a friendly German instructor. Explain this German sentence for an A1-B1 learner:\n\n"
        f"\"{payload.text}\"\n\n"
        f"Reply with JSON only:\n"
        f"{{\"literal\": \"<word-by-word literal translation separated by |>\",\n"
        f" \"grammar_breakdown\": \"<2-3 bullet points explaining case, word order, or verb tense>\",\n"
        f" \"key_rule\": \"<one high-yield German grammar rule to remember>\"}}"
    )
    
    resp = llm.authored([{"role": "user", "content": prompt}], temperature=0.3, max_tokens=300)
    if resp.ok:
        import re, json
        m = re.search(r"\{.*\}", resp.text, re.S)
        if m:
            try:
                parsed = json.loads(m.group(0))
                parsed["literal"] = aligned
                return {"text": payload.text, "words": word_cards, **parsed,
                        "tier": resp.tier}
            except Exception:
                pass
    
    return {
        "text": payload.text,
        "words": word_cards,
        "literal": aligned,
        "grammar_breakdown": "Review word cases and conjugated verb position.",
        "key_rule": "In main clauses, the conjugated verb is in position 2.",
        "tier": "fallback",
    }


# ------------------------------------------------------------------- story
@app.get("/api/story")
def story_today(day: int | None = None,
                conn: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    """Today's episode, opened with its first line if it is new."""
    episode = story.open_day(conn, day)
    return {
        "day_number": episode.day_number, "title": episode.title,
        "setting": episode.setting_de, "npc": episode.npc_name,
        "theme": episode.theme, "status": episode.status,
        "turns": [t.__dict__ for t in episode.turns],
        "turns_used": episode.learner_turns, "turns_target": story.TURNS_PER_DAY,
        "complete": episode.complete,
    }


@app.post("/api/story/say")
def story_say(payload: StorySay,
              conn: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    """The learner's line only. Type English to be translated, German to be corrected.

    Returns as soon as his sentence exists so the scene can play it. The other
    character is a separate request — see /api/story/reply.
    """
    session_id = payload.session_id or ensure_session(conn)
    return story.speak(conn, payload.text, session_id, payload.day)


@app.post("/api/story/reply")
def story_reply(payload: StoryReply,
                conn: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    """The other character's answer, fetched while the learner's line is playing."""
    return story.reply(conn, payload.day)


@app.get("/api/story/{day}/vocabulary")
def story_vocabulary(day: int,
                     conn: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    """Every word that appeared in a day's conversation.

    This is what anchors the other modules to the story: they practise these
    words rather than the global pool, so the same vocabulary is retrieved in a
    second context while the scene is still vivid.
    """
    words = story.day_vocabulary(conn, day)
    return {"day": day, "count": len(words), "words": words}


@app.get("/api/teil/{modul}/{teil}")
def exam_teil(modul: str, teil: int, day: int | None = None, fresh: bool = False,
              conn: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    """One Goethe B1 task in its published format.

    The seven Teile that jana/modules.py did not cover live in jana/teile.py.
    Anything it does not build falls through to the older generators, so the
    client can ask for any (modul, teil) without knowing which file owns it.
    """
    built = teile.build(conn, modul, teil, day, fresh=fresh)
    if built is not None:
        return built

    # Formats still owned by jana/modules.py go through the bank the same way,
    # so no generated question escapes being stored.
    if not fresh:
        stored = bank.unseen(conn, modul, teil, day)
        if stored is not None:
            bank.mark_seen(conn, stored["exercise_id"])
            return stored

    if modul == "lesen":
        body = asdict(modules.lesen_teil(conn, teil, day=day))
    elif modul == "hoeren":
        body = asdict(modules.hoeren_teil(conn, teil, day=day))
    elif modul == "schreiben":
        body = modules.schreiben_task(conn, day=day, teil=teil)
    else:
        body = modules.sprechen_prompt(teil)
    task = {"modul": modul, "teil": teil, "body": body, "day": day,
            "provenance": body.get("provenance", "unknown") if isinstance(body, dict) else "unknown",
            "validated": True, "instruction_de": "", "instruction_en": ""}
    task["exercise_id"] = bank.save(conn, task)
    bank.mark_seen(conn, task["exercise_id"])
    return task


# Cut clips are served straight off disk. They are content, not user data, and
# a StaticFiles mount is the right amount of machinery for that.
if audio.CLIP_DIR.is_dir():
    app.mount("/clips", StaticFiles(directory=audio.CLIP_DIR), name="clips")


@app.get("/api/audio/for")
def audio_for(text: str,
              conn: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    """A real recording of this sentence, if one exists.

    The threshold in audio.find_clip matters more than the search does: a loose
    match returns a human saying something *else*, which is worse than
    synthesis, because the learner is asked to transcribe one sentence and hears
    another. Below the bar this returns null and the browser speaks it instead.
    """
    found = audio.find_clip(conn, text)
    if found is None:
        return {"clip": None, "fallback": "speech-synthesis"}
    return {"clip": {**found, "url": f"/clips/{Path(found['path']).name}"},
            "fallback": None}


@app.get("/api/audio/random")
def audio_random(conn: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    """A real utterance, for dictation practice with a human voice."""
    found = audio.random_clip(conn)
    if found is None:
        raise HTTPException(404, "no clips indexed yet")
    return {**found, "url": f"/clips/{Path(found['path']).name}"}


@app.get("/api/hoeren/real")
def hoeren_real(conn: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    """Dictation built from a real recording rather than from generated text.

    This is the right way round. Generating a sentence and then hoping a
    recording of it exists gets synthesis most of the time; starting from a
    clip guarantees a human voice, and the clips come from the Goethe listening
    material and the German-dense courses — which is the register the exam
    actually uses.
    """
    clip = audio.random_clip(conn, max_seconds=11.0)
    if clip is None:
        raise HTTPException(404, "no clips indexed — run jana.ingest.clips")
    exam = clip["source"].startswith("pruefungstraining")
    return {
        "modul": "hoeren", "teil": 0,
        "instruction_de": "Hören Sie und schreiben Sie, was Sie hören.",
        "instruction_en": "Listen and write down what you hear.",
        "body": {
            "url": f"/clips/{Path(clip['path']).name}",
            "text": clip["text"],
            "seconds": clip["seconds"],
            "origin": "Goethe-Prüfungsmaterial" if exam else clip["source"],
            "is_exam_audio": exam,
        },
        "provenance": "recording", "validated": True,
    }


@app.get("/api/audio/stats")
def audio_stats(conn: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    return audio.stats(conn)


@app.get("/api/bank")
def bank_stats(conn: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    """What the exercise bank holds, per slot. Nothing generated is discarded."""
    return bank.stats(conn)


@app.get("/api/exercise/{exercise_id}")
def exercise(exercise_id: int,
             conn: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    found = bank.get(conn, exercise_id)
    if found is None:
        raise HTTPException(404, "no such exercise")
    return found


@app.get("/api/grammar/next")
def grammar_next(day: int | None = None,
                 conn: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    """The next grammar point due, with four exercises for it."""
    candidates = grammar.due(conn, 1)
    if not candidates:
        return {"point": None, "body": None}
    built = grammar.build(conn, candidates[0]["id"], day)
    if built is None:
        raise HTTPException(503, "grammar generator unavailable")
    return built


@app.get("/api/grammar/point/{point_id}")
def grammar_point(point_id: int, day: int | None = None,
                  conn: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    built = grammar.build(conn, point_id, day)
    if built is None:
        raise HTTPException(503, "grammar generator unavailable")
    return built


@app.post("/api/grammar/answer")
def grammar_answer(payload: GrammarAnswer,
                   conn: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    """Mark one grammar answer and schedule the point on the same curve as a word."""
    result = grammar.check(payload.shape, payload.response, payload.answer)
    grammar.record(conn, payload.point_id, result["correct"], payload.shape,
                   payload.session_id or ensure_session(conn), payload.exercise_id)
    from jana.project import rebuild
    rebuild(conn)
    state = conn.execute(
        "SELECT due_at, stability, reps FROM grammar_state WHERE point_id = ?",
        (payload.point_id,)).fetchone()
    return {**result,
            "due_at": state["due_at"] if state else None,
            "reps": state["reps"] if state else 0,
            "stability_days": round(state["stability"], 1)
                if state and state["stability"] else None}


@app.get("/api/grammar/progress")
def grammar_progress(conn: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    """The whole curriculum with the learner's state — the map view."""
    return {"points": grammar.progress(conn)}


@app.get("/api/grammar/curriculum")
def grammar_curriculum(level: str | None = None,
                       conn: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    """The grammar spine, with the learner's own course lesson for each point."""
    from jana.ingest.grammar import curriculum
    return {"points": curriculum(conn, level)}


@app.post("/api/grade")
def self_grade(payload: SelfGrade,
               conn: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    """A learner-graded recall, written straight to the event log.

    Self-grading is not a weaker signal than machine marking here — it is a
    different and better one. A multiple-choice tick says only "recognised";
    FSRS wants to know how *hard* the retrieval was, and the only instrument
    that can measure that is the person doing it. The four buttons are the FSRS
    grades, so "hard" and "easy" produce genuinely different intervals.
    """
    grade = max(1, min(4, int(payload.grade)))
    session_id = payload.session_id or ensure_session(conn)
    event_id = events.append(conn, "attempt", {
        "session_id": session_id, "item_id": payload.item_id,
        "modality": payload.modality, "rung": payload.rung,
        "task_type": "flashcard", "exercise_id": None, "response": "",
        "correct": grade >= 3, "grade": grade, "latency_ms": payload.latency_ms,
    })
    from jana.project import rebuild
    rebuild(conn)
    state = conn.execute(
        "SELECT due_at, stability, rung FROM item_state WHERE item_id = ?"
        " AND modality = ?", (payload.item_id, payload.modality)).fetchone()
    return {"logged": event_id,
            "due_at": state["due_at"] if state else None,
            "stability_days": round(state["stability"], 1) if state and state["stability"] else None,
            "rung": state["rung"] if state else payload.rung}


@app.get("/api/flashcards")
def flashcards(day: int | None = None, limit: int = 20,
               conn: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    """Today's cards, each with the sentence from the story where it was met.

    The context sentence is the point. A card showing only `die Postleitzahl —
    postal code` is a cue-free retrieval; the same card showing the line the
    border officer actually said gives the memory something to hang on.
    """
    rows = modules._due_words(conn, limit, day=day)
    cards = []
    for row in rows:
        context = conn.execute(
            """SELECT t.de, t.en FROM story_turn t
               JOIN story_vocab v ON v.day_id = t.day_id
               WHERE v.item_id = ? AND t.de LIKE '%' || ? || '%'
               ORDER BY t.id DESC LIMIT 1""",
            (row["item_id"], row["lemma"][:6])).fetchone()
        cards.append({
            "item_id": row["item_id"], "lemma": row["lemma"],
            "gender": row["gender"], "pos": row["pos"],
            "en": row["sense_gloss_en"], "rung": row["rung"],
            "context_de": context["de"] if context else None,
            "context_en": context["en"] if context else None,
        })
    return {"day": day, "cards": cards}


@app.get("/api/recall")
def recall(q: str, limit: int = 8,
           conn: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    """Search everything by meaning — vocabulary and past conversations alike."""
    hits = memory.search(conn, q, limit=limit)
    return {"query": q, "hits": [h.__dict__ for h in hits]}


@app.get("/api/memory")
def memory_stats(conn: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
    return memory.stats(conn)


# --------------------------------------------------------- instructor tools
@app.get("/api/word/{word}")
def word(word: str) -> dict[str, Any]:
    """Click-to-translate. Resolves an inflected form back to its entry."""
    return asdict(wordlookup.lookup(db(), word))


@app.post("/api/literal")
def literal(payload: Text) -> dict[str, Any]:
    """Word-for-word alignment. Local lookup only — no model, no latency."""
    return {"words": explain.literal(db(), payload.text)}


@app.post("/api/grammar")
def grammar_notes(payload: Text) -> dict[str, Any]:
    """Why this case, this ending, this word order. Answered in English."""
    return explain.grammar(payload.text)


@app.post("/api/correct")
def correct(payload: Text) -> dict[str, Any]:
    """Check the learner's own German and name the rule he broke."""
    return explain.correct(payload.text)


# -------------------------------------------------------------------- health
@app.get("/api/health")
def health() -> dict[str, Any]:
    """Model availability and call telemetry. A model is a dependency."""
    return llm.health()


def main() -> None:
    import uvicorn
    uvicorn.run("jana.web:app", host="127.0.0.1", port=8420, reload=False)


if __name__ == "__main__":
    main()
