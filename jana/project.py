"""Derived projections over the event log (arch doc D2, §4).

`item_state` is rebuilt here and nowhere else. It must always be safe to drop
this table and recompute it from `event` alone — that property is what lets the
scheduler be replaced and the whole learner model re-derived from day one.

Two rules keep it true:
  * the projector reads only `event`;
  * every interval is computed from the *event's* timestamp, never wall clock,
    so a replay produces byte-identical state.

The placeholder Leitner ladder this module started with has been replaced by
FSRS without touching a single stored row, because the log recorded an
FSRS-shaped grade and a latency from the first session. That is the payoff the
event log was bought for, collected earlier than expected.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timedelta

from jana import fsrs
from jana.db import DERIVED_TABLES, connect, init_db
from jana.events import replay

# Highest rung the text track currently tests: 1 recognise, 2 cued recall,
# 3 cold production. Rungs 4-6 arrive with the cloze and audio work.
MAX_RUNG_TEXT = 4

# Consecutive correct answers at a rung before the item climbs to the next.
PROMOTION_STREAK = 3

# Deadline-aware scheduling (arch doc §3). Off until the Phase 2 gate: turning
# it on early only compresses intervals and spends review capacity for nothing.
DEADLINE_AWARE = False


def _elapsed_days(previous: str | None, current: str) -> float:
    if not previous:
        return 0.0
    delta = datetime.fromisoformat(current) - datetime.fromisoformat(previous)
    return max(0.0, delta.total_seconds() / 86400.0)


def _horizon_days(ts: str) -> float | None:
    if not DEADLINE_AWARE:
        return None
    from jana.db import EXAM_DATE
    exam = datetime.fromisoformat(f"{EXAM_DATE}T00:00:00+00:00")
    return max(0.0, (exam - datetime.fromisoformat(ts)).total_seconds() / 86400.0)


def _blank(rung: int, ts: str) -> dict:
    return {"rung": rung, "streak": 0, "reps": 0, "lapses": 0,
            "memory": None, "due_at": ts, "last_seen_at": None}


def rebuild(conn: sqlite3.Connection) -> int:
    """Drop and recompute every derived table. Returns rows written."""
    for table in DERIVED_TABLES:
        conn.execute(f"DELETE FROM {table}")

    state: dict[tuple[int, str], dict] = {}

    for _id, ts, kind, p in replay(conn, kinds=("item_introduced", "attempt")):
        # Grammar attempts share the `attempt` kind but have no item — they are
        # projected separately by _project_grammar. Keying on a null item_id
        # here produced a row that item_state cannot hold.
        if p.get("item_id") is None:
            continue
        key = (p["item_id"], p["modality"])
        if kind == "item_introduced":
            state.setdefault(key, _blank(p.get("rung", 1), ts))
            continue

        s = state.setdefault(key, _blank(p.get("rung", 1), ts))
        grade = p.get("grade") or (fsrs.GOOD if p["correct"] else fsrs.AGAIN)
        s["memory"] = fsrs.review(
            s["memory"], grade, _elapsed_days(s["last_seen_at"], ts))
        s["reps"] += 1
        s["last_seen_at"] = ts

        if p["correct"]:
            s["streak"] += 1
            if s["streak"] >= PROMOTION_STREAK and s["rung"] < MAX_RUNG_TEXT:
                s["rung"] += 1
                s["streak"] = 0
        else:
            s["streak"] = 0
            s["lapses"] += 1

        days = fsrs.next_interval(s["memory"], horizon_days=_horizon_days(ts))
        s["due_at"] = (datetime.fromisoformat(ts)
                       + timedelta(days=days)).isoformat(timespec="milliseconds")

    _project_grammar(conn)

    conn.executemany(
        """INSERT INTO item_state
           (item_id, modality, stability, difficulty, due_at, rung,
            reps, lapses, last_seen_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [(item_id, modality,
          s["memory"].stability if s["memory"] else None,
          s["memory"].difficulty if s["memory"] else None,
          s["due_at"], s["rung"], s["reps"], s["lapses"], s["last_seen_at"])
         for (item_id, modality), s in state.items()],
    )
    conn.commit()
    return len(state)


def _project_grammar(conn: sqlite3.Connection) -> None:
    """Grammar points get the same FSRS treatment as items, from the same log."""
    state: dict[int, dict] = {}
    for _id, ts, _kind, p in replay(conn, kinds=("attempt",)):
        point_id = p.get("grammar_point_id")
        if not point_id:
            continue
        s = state.setdefault(point_id, {"memory": None, "reps": 0, "lapses": 0,
                                        "last_seen_at": None, "due_at": ts})
        grade = p.get("grade") or (fsrs.GOOD if p["correct"] else fsrs.AGAIN)
        s["memory"] = fsrs.review(s["memory"], grade,
                                  _elapsed_days(s["last_seen_at"], ts))
        s["reps"] += 1
        s["lapses"] += 0 if p["correct"] else 1
        s["last_seen_at"] = ts
        days = fsrs.next_interval(s["memory"], horizon_days=_horizon_days(ts))
        s["due_at"] = (datetime.fromisoformat(ts)
                       + timedelta(days=days)).isoformat(timespec="milliseconds")

    conn.executemany(
        """INSERT INTO grammar_state
               (point_id, stability, difficulty, due_at, reps, lapses, last_seen_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [(point_id, s["memory"].stability if s["memory"] else None,
          s["memory"].difficulty if s["memory"] else None,
          s["due_at"], s["reps"], s["lapses"], s["last_seen_at"])
         for point_id, s in state.items()])


def retrievability_on(conn: sqlite3.Connection, when: str) -> list[tuple[int, float]]:
    """Predicted recall probability per item on a given date.

    This is what makes the exam date schedulable and it is also the input to the
    §7 Level 2 calibration check: sum these and you have Jana's prediction of how
    much German the learner will actually have on the day.
    """
    target = datetime.fromisoformat(when)
    out = []
    for row in conn.execute(
            "SELECT item_id, stability, last_seen_at FROM item_state"
            " WHERE stability IS NOT NULL AND last_seen_at IS NOT NULL"):
        elapsed = (target - datetime.fromisoformat(row["last_seen_at"])
                   ).total_seconds() / 86400.0
        out.append((row["item_id"], fsrs.retrievability(elapsed, row["stability"])))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["rebuild", "forecast"])
    ap.add_argument("--on", default=None, help="date for forecast, ISO")
    args = ap.parse_args()
    conn = connect()
    init_db(conn)
    if args.command == "rebuild":
        print(f"rebuilt item_state: {rebuild(conn)} rows")
        return
    from jana.db import EXAM_DATE
    when = args.on or f"{EXAM_DATE}T09:00:00+00:00"
    scores = retrievability_on(conn, when)
    known = sum(1 for _, r in scores if r >= 0.9)
    print(f"forecast for {when}: {len(scores)} tracked, "
          f"{known} at >=90% recall, expected {sum(r for _, r in scores):.0f} items")


if __name__ == "__main__":
    main()
