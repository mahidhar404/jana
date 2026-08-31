"""Deterministic scheduler (arch doc §4). No LLM, ever.

Reads the derived projections and returns today's queue. Two knobs matter now;
the rest of the design (interference-aware ordering, deadline-aware intervals)
lands in Phase 2 and is deliberately absent here.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

NEW_PER_SESSION = 15      # new items introduced per day
TARGET_ITEMS = 40         # total distinct items in a session

# An item is teachable if it has an English gloss (recognition, recall,
# production), a gender (article drill), or principal parts (conjugation drill).
# Wortliste verbs now carry principal parts, making them drillable in German
# without translating a word of English.
TEACHABLE = "(i.sense_gloss_en NOT LIKE '[%' OR i.gender IS NOT NULL OR i.principal_parts IS NOT NULL)"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def due(conn: sqlite3.Connection, modality: str = "text",
        limit: int = TARGET_ITEMS, at: str | None = None) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT s.item_id, s.modality, s.rung, s.due_at,
                  i.lemma, i.gender, i.pos, i.sense_gloss_en,
                  i.principal_parts
           FROM item_state s JOIN item i ON i.id = s.item_id
           WHERE s.modality = ? AND s.due_at <= ?
           ORDER BY s.due_at LIMIT ?""",
        (modality, at or _now(), limit),
    ).fetchall()


def unseen(conn: sqlite3.Connection, modality: str = "text",
           limit: int = NEW_PER_SESSION) -> list[sqlite3.Row]:
    """Items with no state on this modality yet, easiest level first."""
    return conn.execute(
        """SELECT i.id AS item_id, ? AS modality, 1 AS rung, NULL AS due_at,
                  i.lemma, i.gender, i.pos, i.sense_gloss_en,
                  i.principal_parts
           FROM item i
           WHERE NOT EXISTS (SELECT 1 FROM item_state s
                             WHERE s.item_id = i.id AND s.modality = ?)
             AND """ + TEACHABLE + """
           ORDER BY CASE i.cefr_level WHEN 'A1' THEN 1 WHEN 'A2' THEN 2
                                      WHEN 'B1' THEN 3 ELSE 4 END,
                    (i.sense_gloss_en LIKE '[%'), i.id
           LIMIT ?""",
        (modality, modality, limit),
    ).fetchall()


def plan(conn: sqlite3.Connection, modality: str = "text",
         at: str | None = None) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
    """-> (reviews, new). Reviews always take priority over new material."""
    reviews = due(conn, modality, TARGET_ITEMS, at)
    room = max(0, TARGET_ITEMS - len(reviews))
    fresh = unseen(conn, modality, min(NEW_PER_SESSION, room))
    return reviews, fresh
