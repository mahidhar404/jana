"""Second pass over harvested vocabulary: is this a word, or is it debris?

The harvest in jana/ingest/lemmas.py reads every attested token in the
Wortliste's alphabetical section. That section contains the headword column
*and* the example sentences beside it, and the two-column layout cannot be
separated by this extractor (see jana/ingest/wortliste.py). So example prose
leaks in — the names of the people in the examples, the streets they live on,
numbers written out, and the occasional English loan.

Measured: about a fifth of the harvest. Which is exactly the kind of number that
would never surface if nobody looked, and would quietly spend review capacity
on `bachstrasse` for four months.

Regex cannot fix it — `absolut` and `alternativ` are real German that look like
English, while `amira` and `ammann` are names that look like German. The
distinction is lexical knowledge, so it is asked as a question, in English,
about German: does this belong in a B1 vocabulary list? Names, places, numbers,
fragments and foreign words are struck out.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time

from jana import llm
from jana.db import connect, init_db

BATCH = 50
SOURCE = "goethe:wortliste-bare"

INSTRUCTION = """You are auditing a German vocabulary list for a Goethe B1 exam app.

For each word decide: does this belong on a B1 vocabulary list a learner should
memorise?

KEEP: ordinary German words — adjectives, adverbs, verbs, particles,
conjunctions, common nouns. Keep loanwords that are genuinely used in German
(Computer, Job, Handy).

DROP: personal names (Christina, Amira), place and street names (Bachstrasse,
Berlin), numbers written as words (achtzehnhundert...), fragments that are not
standalone words (dar, ge), misspellings or words with a missing umlaut
(bernachten for übernachten), and English words that are not used in German.

Reply with JSON only, mapping each word to true (keep) or false (drop):
{"sauer": true, "christina": false, "automatisch": true, "bachstrasse": false}"""


def harvested(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT lemma FROM item WHERE source = ? ORDER BY lemma", (SOURCE,))]


def verdicts(words: list[str]) -> dict[str, bool]:
    reply = llm.authored(
        [{"role": "system", "content": INSTRUCTION},
         {"role": "user", "content": json.dumps(words, ensure_ascii=False)}],
        temperature=0.0, max_tokens=1800)
    if not reply.ok:
        return {}
    match = re.search(r"\{.*\}", reply.text, re.S)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return {k: bool(v) for k, v in parsed.items()} if isinstance(parsed, dict) else {}


def drop(conn: sqlite3.Connection, words: list[str]) -> int:
    """Remove debris — but never an item the learner has already studied.

    An item with review history is referenced by the event log, and the log is
    append-only (D2). Deleting the item would leave attempts pointing at
    nothing. Those are left in place and reported instead.
    """
    removed = 0
    for word in words:
        row = conn.execute(
            "SELECT id FROM item WHERE lemma = ? AND source = ?",
            (word, SOURCE)).fetchone()
        if row is None:
            continue
        studied = conn.execute(
            "SELECT 1 FROM item_state WHERE item_id = ? LIMIT 1",
            (row["id"],)).fetchone()
        if studied:
            continue
        conn.execute("DELETE FROM embedding WHERE kind='item' AND ref_id = ?",
                     (row["id"],))
        conn.execute("DELETE FROM story_vocab WHERE item_id = ?", (row["id"],))
        conn.execute("DELETE FROM item WHERE id = ?", (row["id"],))
        removed += 1
    conn.commit()
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = connect()
    init_db(conn)
    words = harvested(conn)
    print(f"auditing {len(words)} harvested lemmas")

    rejected: list[str] = []
    started = time.perf_counter()
    for offset in range(0, len(words), BATCH):
        chunk = words[offset:offset + BATCH]
        result = verdicts(chunk)
        bad = [w for w in chunk if result.get(w) is False]
        rejected += bad
        if not args.dry_run and bad:
            drop(conn, bad)
        print(f"  {offset + len(chunk):5d}/{len(words)}  dropped={len(rejected)}",
              flush=True)

    total = conn.execute("SELECT count(*) FROM item").fetchone()[0]
    print(f"\n{'would drop' if args.dry_run else 'dropped'} {len(rejected)} "
          f"in {time.perf_counter() - started:.0f}s · {total} items remain")
    print("  sample:", rejected[:20])


if __name__ == "__main__":
    main()
