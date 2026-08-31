"""The exercise bank: every question ever generated, kept.

Why this exists
---------------
Until now every call to /api/teil generated a fresh task, showed it once, and
threw it away. Three things were lost each time:

  * **The question itself.** A Lesen Teil 3 that took four seconds and a remote
    call to build is gone the moment the page changes. There is no way to revisit
    the one he got wrong, and no way to build a mock exam out of material he has
    already been graded on.
  * **The money and the wait.** Regenerating an equivalent task is a paid call
    and a multi-second pause on a screen that should feel instant. D5 said
    precompute and serve from cache; this is the cache that was missing.
  * **The evidence.** Which formats produce bad German, which prompts drift,
    which model was serving when quality dipped — none of that is answerable if
    the artefacts are discarded.

So every generated task is written to `exercise`, embedded into the semantic
store, and reused. Retrieval prefers what he has not seen; only when the bank is
exhausted for a (module, Teil, day) does it generate something new.

The bank is content, not learner state — the same distinction as `sentence`. His
*attempts* at these exercises are events, and they reference `exercise_id`, so
replaying the log can still say exactly which question he was looking at.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from jana import events

# How many unseen exercises to keep per slot before the generator stops adding.
TARGET_PER_SLOT = 8


def save(conn: sqlite3.Connection, task: dict[str, Any],
         item_ids: list[int] | None = None,
         grammar_point_id: int | None = None) -> int:
    """Persist a generated task and return its id. Idempotent on identical bodies."""
    body = json.dumps(task.get("body", {}), ensure_ascii=False, sort_keys=True)
    modul = task.get("modul") or task.get("exam_module") or "unknown"
    teil = task.get("teil")

    existing = conn.execute(
        "SELECT id FROM exercise WHERE exam_module = ? AND coalesce(teil,-1) = ?"
        " AND body_json = ?", (modul, teil if teil is not None else -1, body)
    ).fetchone()
    if existing:
        return int(existing["id"])

    cursor = conn.execute(
        """INSERT INTO exercise
               (item_ids, exam_module, task_type, prompt, reference_answer,
                distractors_json, source, generated_by, validated,
                body_json, teil, day_number, grammar_point_id, created_at,
                seen_count)
           VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
        (json.dumps(item_ids or []), modul,
         f"{modul}_teil{teil}" if teil else modul,
         task.get("instruction_de", ""), "",
         "generated", task.get("provenance", "unknown"),
         int(bool(task.get("validated"))), body, teil, task.get("day"),
         grammar_point_id, events.now()))
    conn.commit()
    exercise_id = int(cursor.lastrowid)
    _remember(conn, exercise_id, modul, teil, task)
    return exercise_id


def _remember(conn: sqlite3.Connection, exercise_id: int, modul: str,
              teil: int | None, task: dict[str, Any]) -> None:
    """Embed the task so it can be found by meaning, not just by module and Teil.

    Wrapped: a missing embedder must cost searchability, never the exercise.
    """
    try:
        from jana import memory, modules

        german = " ".join(modules._german_strings(task.get("body", {})))[:2000]
        if not german.strip():
            return
        vectors = memory.embed([f"{modul} Teil {teil}: {german}"])
        if not vectors:
            return
        conn.execute(
            """INSERT OR REPLACE INTO embedding
                   (kind, ref_id, text, model, dim, vector, created_at)
               VALUES ('exercise', ?, ?, ?, ?, ?, datetime('now'))""",
            (exercise_id, german[:400], memory.EMBED_MODEL, len(vectors[0]),
             vectors[0].tobytes()))
        conn.commit()
    except Exception:
        pass


def unseen(conn: sqlite3.Connection, modul: str, teil: int | None,
           day: int | None = None) -> dict[str, Any] | None:
    """An exercise he has not been shown, preferring today's story if there is one."""
    rows = conn.execute(
        """SELECT * FROM exercise
           WHERE exam_module = ? AND coalesce(teil,-1) = ?
           ORDER BY (day_number IS NOT ? ), seen_count, id""",
        (modul, teil if teil is not None else -1, day)).fetchall()
    for row in rows:
        if (row["seen_count"] or 0) == 0:
            return _revive(row)
    return None


def count(conn: sqlite3.Connection, modul: str, teil: int | None,
          unseen_only: bool = True) -> int:
    clause = " AND coalesce(seen_count,0) = 0" if unseen_only else ""
    return conn.execute(
        f"""SELECT count(*) FROM exercise
            WHERE exam_module = ? AND coalesce(teil,-1) = ?{clause}""",
        (modul, teil if teil is not None else -1)).fetchone()[0]


def get(conn: sqlite3.Connection, exercise_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM exercise WHERE id = ?",
                       (exercise_id,)).fetchone()
    return _revive(row) if row else None


def mark_seen(conn: sqlite3.Connection, exercise_id: int) -> None:
    conn.execute(
        "UPDATE exercise SET seen_count = coalesce(seen_count,0) + 1,"
        " last_seen_at = ? WHERE id = ?", (events.now(), exercise_id))
    conn.commit()


def _revive(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "exercise_id": row["id"],
        "modul": row["exam_module"],
        "teil": row["teil"],
        "instruction_de": row["prompt"],
        "instruction_en": "",
        "body": json.loads(row["body_json"] or "{}"),
        "provenance": row["generated_by"],
        "validated": bool(row["validated"]),
        "day": row["day_number"],
        "from_bank": True,
    }


def stats(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """SELECT exam_module, teil, count(*) n,
                  sum(coalesce(seen_count,0) = 0) fresh
           FROM exercise GROUP BY exam_module, teil
           ORDER BY exam_module, teil""").fetchall()
    return {
        "total": conn.execute("SELECT count(*) FROM exercise").fetchone()[0],
        "slots": [{"modul": r["exam_module"], "teil": r["teil"],
                   "stored": r["n"], "unseen": r["fresh"]} for r in rows],
    }
