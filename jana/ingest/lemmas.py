"""Harvest the vocabulary the structured parsers could not see.

The noun and verb parsers key off published notation — `die Lösung, -en`,
`abbiegen, biegt ab, ...`. Adjectives, adverbs and particles have no notation:
in the Wortliste's headword column they are bare lowercase words sitting between
the entries that do (`soviel | sowieso | sozial | spannend`). Nothing local
distinguishes them from an inflected form that happens to appear nearby.

So the candidates are gathered mechanically — every attested syllabus token that
is not already an item, not a function word, and not an inflection of a lemma we
hold — and then classified by the remote tier, which is asked two questions:
what does it mean, and **is this a base form?** Anything that comes back as an
inflection is discarded rather than added, because a flashcard for `verkaufte`
teaches a conjugation as if it were a word.

Classification is English-language judgement about German, not authored German,
so it sits on the safe side of D4 for the same reason the glosses do.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time

from jana import lexicon, llm, wordlookup
from jana.db import connect, init_db

BATCH = 40
MIN_LENGTH = 3

INSTRUCTION = """You are a German lexicographer cleaning a vocabulary list.

For each German word, decide whether it is a BASE FORM (dictionary headword) or
an inflected form of another word, and give its English meaning.

Base forms: adjectives in their uninflected form (schnell, gemütlich), adverbs
(sowieso, deshalb), particles, and infinitives.
NOT base forms: conjugated verbs (verkaufte, liebt), participles (gelobt),
declined adjectives (kreative, guten), plural or case forms of nouns (Gerichte).

Reply with JSON only, mapping each word to an object:
{"sowieso": {"base": true, "pos": "adverb", "en": "anyway"},
 "verkaufte": {"base": false, "pos": "verb", "en": "sold"}}"""


def candidates(conn: sqlite3.Connection, limit: int | None = None) -> list[str]:
    """Attested syllabus words that are not yet schedulable and not inflections."""
    known = {row[0].casefold() for row in conn.execute("SELECT lemma FROM item")}
    index = wordlookup.build_index(conn)
    function_words = {lexicon._fold(w) for w in lexicon.FUNCTION_WORDS}

    out: list[str] = []
    for (token,) in conn.execute(
            "SELECT token FROM syllabus_token ORDER BY token"):
        if len(token) < MIN_LENGTH or token in known or token in function_words:
            continue
        if not re.fullmatch(r"[a-zäöüß]+", token):
            continue          # capitalised words are nouns the parser already had
        # An inflection of something we hold is not new vocabulary.
        if wordlookup.lookup(conn, token, index).found:
            continue
        out.append(token)
        if limit and len(out) >= limit:
            break
    return out


def classify(words: list[str]) -> dict[str, dict]:
    reply = llm.authored(
        [{"role": "system", "content": INSTRUCTION},
         {"role": "user", "content": json.dumps(words, ensure_ascii=False)}],
        temperature=0.0, max_tokens=2000)
    if not reply.ok:
        return {}
    match = re.search(r"\{.*\}", reply.text, re.S)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


GERMAN_CHARS = re.compile(r"[äöüßÄÖÜ]")
POS_MAP = {"adjective": "other", "adverb": "other", "particle": "other",
           "conjunction": "other", "preposition": "other", "pronoun": "other",
           "verb": "verb", "noun": "noun", "numeral": "other"}


def load(conn: sqlite3.Connection, verdicts: dict[str, dict], source: str,
         tier: str) -> tuple[int, int]:
    added = rejected = 0
    for word, verdict in verdicts.items():
        if not isinstance(verdict, dict):
            continue
        gloss = str(verdict.get("en", "")).strip()
        # A gloss equal to the word is the model echoing the prompt, not
        # translating it — and umlauts alone do not catch that, because plenty
        # of German words have none.
        if (not verdict.get("base") or not gloss
                or GERMAN_CHARS.search(gloss)
                or gloss.casefold() == word.casefold()):
            rejected += 1
            continue
        pos = POS_MAP.get(str(verdict.get("pos", "")).lower(), "other")
        if pos == "noun":
            rejected += 1        # nouns need gender; the noun parser owns those
            continue
        cursor = conn.execute(
            """INSERT OR IGNORE INTO item
               (lemma, pos, gender, sense_gloss_en, cefr_level, item_type,
                source, gloss_source)
               VALUES (?, ?, NULL, ?, 'B1', 'vocab', ?, ?)""",
            (word, pos, gloss, source, tier))
        added += cursor.rowcount
    conn.commit()
    return added, rejected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = connect()
    init_db(conn)
    todo = candidates(conn, args.limit)
    tier = "remote:deepseek" if llm.deepseek_available() else f"local:{llm.LOCAL_MODEL}"
    print(f"{len(todo)} candidate words · classifying via {tier}")

    if args.dry_run:
        verdicts = classify(todo[:BATCH])
        for word in todo[:20]:
            verdict = verdicts.get(word, {})
            mark = "keep" if verdict.get("base") else "drop"
            print(f"    {mark}  {word:20s} {verdict.get('pos', '?'):10s} "
                  f"{verdict.get('en', '')}")
        return

    added = rejected = 0
    started = time.perf_counter()
    for offset in range(0, len(todo), BATCH):
        chunk = todo[offset:offset + BATCH]
        got, lost = load(conn, classify(chunk), "goethe:wortliste-bare", tier)
        added += got
        rejected += lost
        print(f"  {offset + len(chunk):5d}/{len(todo)}  added={added} "
              f"not-base={rejected}", flush=True)

    total = conn.execute("SELECT count(*) FROM item").fetchone()[0]
    print(f"\ndone in {time.perf_counter() - started:.0f}s · "
          f"{added} new lemmas · {total} items total")


if __name__ == "__main__":
    main()
