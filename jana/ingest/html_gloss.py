"""Goethe vocabulary answer keys (HTML) -> item rows.

The corpus's two `Lösungen.html` files are human-authored DE-EN glossaries laid
out one pair per <p> block:

    <p>der Job - job</p><p><br></p><p>die Lösung - solution</p>

They satisfy D4 (human-authored) and carry noun gender, which is the highest
yield German failure mode and must be schedulable. This is the Day 1 seed.
"""

from __future__ import annotations

import argparse
import html as htmllib
import re
import sqlite3
import unicodedata
from pathlib import Path

from jana.config import CORPUS_ROOT, GLOSS_NAME_SUBSTRINGS, fold
from jana.db import connect, init_db

BLOCK_SPLIT = re.compile(r"</p>|</li>|<br\s*/?>|</div>", re.I)
TAGS = re.compile(r"<[^>]+>")
PAIR = re.compile(r"^(.{1,60}?)\s+[-–—]\s+(.{1,80})$")
ARTICLE = re.compile(r"^(der|die|das)(?:/(?:der|die|das))*\s+(.+)$", re.I)
GERMANISH = re.compile(r"^[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß\s/().-]*$")


def blocks(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    raw = re.sub(r"<script.*?</script>|<style.*?</style>", "", raw, flags=re.S | re.I)
    out = []
    for chunk in BLOCK_SPLIT.split(raw):
        text = htmllib.unescape(TAGS.sub(" ", chunk))
        text = unicodedata.normalize("NFC", re.sub(r"\s+", " ", text)).strip()
        if text:
            out.append(text)
    return out


# The A2 answer key lists pairs in both directions — "der Job - job" but also
# "topic - das Thema", because its test asks both ways. Taking the left side as
# German unconditionally produces English "items" with German glosses, which
# then leak into multiple-choice distractors and teach nothing.
GERMAN_SIDE = re.compile(r"^(der|die|das)\s+[A-ZÄÖÜ]", re.I)


def orient(left: str, right: str) -> tuple[str, str]:
    """-> (german, english). Detects and repairs reversed pairs."""
    if GERMAN_SIDE.match(right) and not GERMAN_SIDE.match(left):
        return right, left
    if right.lower().startswith("to ") and not left.lower().startswith("to "):
        return left, right           # already DE - "to do"
    if left.lower().startswith("to ") and not right.lower().startswith("to "):
        return right, left           # "to do" - DE, reversed
    return left, right


def classify(de: str, en: str) -> tuple[str | None, str, str]:
    """-> (gender, lemma, pos). Gender is None for non-nouns."""
    m = ARTICLE.match(de)
    if m:
        return m.group(1).lower(), m.group(2).strip(), "noun"
    if re.search(r"(en|ern|eln|n)$", de) and en.lower().startswith("to "):
        return None, de, "verb"
    return None, de, "other"


def parse(path: Path) -> list[dict]:
    rows, seen = [], set()
    for block in blocks(path):
        m = PAIR.match(block)
        if not m:
            continue
        de, en = orient(m.group(1).strip(), m.group(2).strip())
        # Reject sentences and junk: glossary heads are short and word-like.
        if len(de.split()) > 4 or not GERMANISH.match(de):
            continue
        gender, lemma, pos = classify(de, en)
        key = (lemma.lower(), en.lower())
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "lemma": lemma, "pos": pos, "gender": gender,
            "sense_gloss_en": en, "source": str(path.relative_to(CORPUS_ROOT)),
        })
    return rows


def find_files() -> list[Path]:
    return sorted(p for p in CORPUS_ROOT.rglob("*.html")
                  if any(s in fold(p.name) for s in GLOSS_NAME_SUBSTRINGS))


def load(conn: sqlite3.Connection, rows: list[dict], cefr: str | None = None) -> int:
    n = 0
    for r in rows:
        cur = conn.execute(
            """INSERT OR IGNORE INTO item
               (lemma, pos, gender, sense_gloss_en, cefr_level, item_type, source)
               VALUES (?, ?, ?, ?, ?, 'vocab', ?)""",
            (r["lemma"], r["pos"], r["gender"], r["sense_gloss_en"],
             cefr or _cefr_from_source(r["source"]), r["source"]),
        )
        n += cur.rowcount
    conn.commit()
    return n


def _cefr_from_source(source: str) -> str | None:
    low = fold(source)
    for level in ("a1", "a2", "b1", "b2"):
        if level in low:
            return level.upper()
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    files = find_files()
    if not files:
        raise SystemExit(f"no glossary HTML under {CORPUS_ROOT}")

    all_rows: list[dict] = []
    for f in files:
        rows = parse(f)
        print(f"  {len(rows):4d} pairs  {f.relative_to(CORPUS_ROOT)}")
        all_rows.extend(rows)

    nouns = sum(1 for r in all_rows if r["gender"])
    verbs = sum(1 for r in all_rows if r["pos"] == "verb")
    print(f"\n{len(all_rows)} pairs from {len(files)} files "
          f"({nouns} gendered nouns, {verbs} verbs)")

    if args.dry_run:
        for r in all_rows[:12]:
            art = f"{r['gender']} " if r["gender"] else ""
            print(f"    {art}{r['lemma']} = {r['sense_gloss_en']}  [{r['pos']}]")
        return

    conn = connect()
    init_db(conn)
    print(f"inserted {load(conn, all_rows)} new items")


if __name__ == "__main__":
    main()
