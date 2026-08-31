"""The append-only event log — the source of truth (arch doc D2).

Everything else in Jana is a projection over this table. The scheduler will be
wrong at first; when it improves the log is replayed and the learner model is
re-derived from day one. That only works if the log records *everything FSRS
will eventually need*, so `attempt` carries an FSRS-shaped grade and a latency
from the very first session, even though Phase 0's projector ignores both.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterator

# FSRS grade scale, recorded from day 1 so no log migration is needed on Day 3.
AGAIN, HARD, GOOD, EASY = 1, 2, 3, 4

KINDS = {
    "session_started",   # {session_id, date}
    "session_ended",     # {session_id, n_attempts}
    "item_introduced",   # {item_id, modality, rung}
    "exposure",          # {item_id, modality, rung, exercise_id}
    "attempt",           # {item_id, modality, rung, exercise_id, response,
                         #  correct, grade, latency_ms}
    "grader_override",   # {attempt_event_id, item_id, machine_correct,
                         #  learner_correct, response, reference}
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def append(conn: sqlite3.Connection, kind: str, payload: dict[str, Any],
           ts: str | None = None) -> int:
    if kind not in KINDS:
        raise ValueError(f"unknown event kind {kind!r}; add it to events.KINDS")
    cur = conn.execute(
        "INSERT INTO event (ts, kind, payload_json) VALUES (?, ?, ?)",
        (ts or now(), kind, json.dumps(payload, ensure_ascii=False, sort_keys=True)),
    )
    conn.commit()
    return int(cur.lastrowid)


def replay(conn: sqlite3.Connection, kinds: tuple[str, ...] | None = None,
           since_id: int = 0) -> Iterator[tuple[int, str, str, dict[str, Any]]]:
    """Yield (id, ts, kind, payload) in log order."""
    sql = "SELECT id, ts, kind, payload_json FROM event WHERE id > ?"
    args: list[Any] = [since_id]
    if kinds:
        sql += f" AND kind IN ({','.join('?' * len(kinds))})"
        args.extend(kinds)
    sql += " ORDER BY id"
    for row in conn.execute(sql, args):
        yield row["id"], row["ts"], row["kind"], json.loads(row["payload_json"])


def count(conn: sqlite3.Connection, kind: str | None = None) -> int:
    if kind:
        return conn.execute("SELECT count(*) FROM event WHERE kind = ?", (kind,)).fetchone()[0]
    return conn.execute("SELECT count(*) FROM event").fetchone()[0]
