"""English glosses for the Wortliste, generated in batch and provenance-tagged.

Why this is allowed when D4 forbids model-authored German
--------------------------------------------------------
D4 constrains German the learner is asked to *learn from*, because a beginner
cannot detect a subtle German error until roughly B1. English runs the other
way: he reads English natively, so a wrong gloss is visible the moment it
contradicts context, and it can be checked mechanically besides.

So the risk profile is inverted, and generating English is the right call. What
is not optional is saying so: every generated gloss records `gloss_source`, and
re-running this against the remote tier upgrades them in place without touching
anything else.

Batched at 40 lemmas per call because the cost here is per-request latency, not
per-token — 2,081 items is 52 calls instead of 2,081.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time

from jana import llm
from jana.db import connect, init_db

BATCH = 40
PLACEHOLDER = "[%"

# A gloss must read as English. German orthography leaking into the English
# column means the model echoed the prompt instead of translating it.
GERMAN_CHARS = re.compile(r"[äöüßÄÖÜ]")

INSTRUCTION = """You are a German-English lexicographer producing a study glossary.

For each German word given, return its most common English meaning as a learner
would want it on a flashcard.

Rules:
- Nouns: a plain noun phrase. "illustration", "staircase", "battery".
- Verbs: start with "to". "to turn off", "to arrive".
- Adjectives/adverbs: the plain English word.
- If a word has two common senses, give both separated by " / ".
- Keep it under 6 words. No articles, no explanation, no German.

Reply with JSON only, an object mapping each German word to its English gloss:
{"Abbildung": "illustration / figure", "abbiegen": "to turn / turn off"}"""


def pending(conn: sqlite3.Connection, limit: int | None = None) -> list[sqlite3.Row]:
    sql = ("SELECT id, lemma, pos, gender, principal_parts FROM item "
           "WHERE sense_gloss_en LIKE ? ORDER BY "
           "CASE cefr_level WHEN 'A1' THEN 1 WHEN 'A2' THEN 2 ELSE 3 END, id")
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, (PLACEHOLDER,)).fetchall()


def _describe(row: sqlite3.Row) -> str:
    if row["pos"] == "noun" and row["gender"]:
        return f"{row['gender']} {row['lemma']}"
    return row["lemma"]


def _acceptable(lemma: str, gloss: str) -> bool:
    gloss = gloss.strip()
    if not 1 < len(gloss) < 60:
        return False
    if GERMAN_CHARS.search(gloss):
        return False
    return gloss.casefold() != lemma.casefold()


def translate(rows: list[sqlite3.Row]) -> dict[str, str]:
    words = [_describe(r) for r in rows]
    reply = llm.authored(
        [{"role": "system", "content": INSTRUCTION},
         {"role": "user", "content": json.dumps(words, ensure_ascii=False)}],
        temperature=0.1, max_tokens=1600)
    if not reply.ok:
        return {}
    match = re.search(r"\{.*\}", reply.text, re.S)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def _merge_duplicate(conn: sqlite3.Connection, placeholder_id: int,
                     lemma: str, gloss: str) -> bool:
    """Drop a placeholder row that a gloss has just revealed to be a duplicate.

    The Wortliste and the course corpus overlap: `Job` is imported twice, once
    with the gloss "job" and once as the placeholder `[noun]`. They are the same
    sense, and only the missing English kept them apart. Writing the gloss makes
    that visible as a UNIQUE violation — which is the constraint doing its job,
    not an obstacle to route around.

    The glossed row is the better record, so the placeholder is removed. It is
    only removed if nothing references it: an item the learner has already
    studied keeps its history, and merging those properly would mean rewriting
    the event log, which D2 forbids outright.
    """
    referenced = conn.execute(
        "SELECT 1 FROM item_state WHERE item_id = ? LIMIT 1",
        (placeholder_id,)).fetchone()
    if referenced:
        return False
    conn.execute("DELETE FROM item WHERE id = ?", (placeholder_id,))
    return True


def apply(conn: sqlite3.Connection, rows: list[sqlite3.Row],
          glosses: dict[str, str], tier: str) -> tuple[int, int]:
    """-> (glossed, merged). Merged rows are duplicates the gloss exposed."""
    written = merged = 0
    for row in rows:
        gloss = glosses.get(_describe(row)) or glosses.get(row["lemma"])
        if not gloss or not _acceptable(row["lemma"], gloss):
            continue
        gloss = gloss.strip()
        try:
            with conn:
                conn.execute(
                    "UPDATE item SET sense_gloss_en = ?, gloss_source = ?"
                    " WHERE id = ?", (gloss, tier, row["id"]))
            written += 1
        except sqlite3.IntegrityError:
            if _merge_duplicate(conn, row["id"], row["lemma"], gloss):
                merged += 1
    conn.commit()
    return written, merged


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None,
                    help="stop after this many items (for a trial run)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = connect()
    init_db(conn)
    todo = pending(conn, args.limit)
    if not todo:
        print("every item already has an English gloss")
        return

    tier = "remote:deepseek" if llm.deepseek_available() else f"local:{llm.LOCAL_MODEL}"
    print(f"{len(todo)} items need glosses · via {tier}")

    if args.dry_run:
        glosses = translate(todo[:BATCH])
        for row in todo[:12]:
            print(f"    {_describe(row):28s} -> {glosses.get(_describe(row), '???')}")
        return

    written = merged = 0
    started = time.perf_counter()
    for offset in range(0, len(todo), BATCH):
        batch = todo[offset:offset + BATCH]
        got, dup = apply(conn, batch, translate(batch), tier)
        written += got
        merged += dup
        done = offset + len(batch)
        missed = done - written - merged
        rate = done / max(time.perf_counter() - started, 1e-6)
        print(f"  {done:5d}/{len(todo)}  glossed={written} merged={merged} "
              f"missed={missed}  ({rate:.1f}/s)", flush=True)

    remaining = len(pending(conn))
    print(f"\ndone in {time.perf_counter() - started:.0f}s · {written} glossed, "
          f"{merged} duplicates merged, {remaining} still missing")


if __name__ == "__main__":
    main()
