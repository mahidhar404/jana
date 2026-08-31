"""Goethe B1 Wortliste -> items (the curriculum spine, D1).

The Wortliste is monolingual: it gives gender, plural and verb principal parts,
but no English. That shapes what we can schedule from it without inventing
anything:

  * every noun carries a gender, so it can be drilled as an article task
    ("___ Abbildung" -> der/die/das) with no gloss and no model involvement.
    Article errors are the highest-yield German failure mode, so this is not a
    consolation prize;
  * DE->EN recognition needs a gloss, so those items stay unschedulable until a
    gloss arrives from the corpus or from the authoring tier.

The alternative — having a local model translate 2,400 headwords — is exactly
what D4 forbids, and it would be invisible when wrong.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
from pathlib import Path

from jana.db import connect, init_db
from jana.ingest.pdftext import extract

# die Abbildung, -en   |   das Abenteuer, -   |   der Abfall, ¨-e
NOUN = re.compile(
    r"\b(der|die|das)\s+([A-ZÄÖÜ][A-Za-zÄÖÜäöüß-]{1,30})\s*,\s*(¨?-[a-zäöüß]{0,3}|-)(?=\s|$)")
# abbiegen, biegt ab, bog ab, ist abgebogen
#
# The leading group captures the Wortliste's optional-prefix notation:
#   "(herunter-)fahren, fährt herunter, fuhr herunter, hat heruntergefahren"
# means *herunterfahren*. Without it the regex matched the bare `fahren` after
# the closing bracket and handed the simple verb the prefixed verb's forms —
# so clicking `fährt` in a sentence about driving showed the principal parts of
# "to shut down". Wrong grammar presented confidently is worse than none.
VERB = re.compile(
    r"(?:\(([a-zäöüß]{2,12})-\)\s*)?"
    r"\b([a-zäöüß]{3,20}(?:en|ern|eln))\s*,\s*"
    r"([a-zäöüß]+(?:\s+[a-zäöüß]+)?)\s*,\s*"
    r"([a-zäöüß]+(?:\s+[a-zäöüß]+)?)\s*,\s*"
    r"(ist|hat)\s+([a-zäöüß]{3,25})")

# Particles that split off a separable verb and appear after the finite form.
PARTICLES = ("ab", "an", "auf", "aus", "bei", "ein", "her", "hin", "los", "mit",
             "nach", "vor", "weg", "zu", "zurück", "herunter", "hinunter",
             "herauf", "hinauf", "heraus", "hinaus", "herein", "hinein", "um",
             "über", "unter", "durch", "fest", "frei", "statt", "teil", "wieder")


def parts_match(lemma: str, parts: str) -> bool:
    """Do these principal parts actually belong to this lemma?

    The decisive signal is the separable particle, not the stem. A substring
    test on the stem is useless here — `fah` occurs inside *heruntergefahren*,
    so `fahren` happily accepted *herunterfahren*'s forms and displayed "to
    shut down" conjugations for "to drive".

    The rule that actually holds: a verb states its forms split, so the particle
    in the finite form must be exactly the particle in the lemma. A simple verb
    has none, and a simple verb showing a particled form is a mis-parse.
    """
    finite = parts.split(",")[0].strip().split()
    parts_particle = finite[1].lower() if len(finite) > 1 else ""
    lemma_particle = ""
    low = lemma.lower()
    for particle in sorted(PARTICLES, key=len, reverse=True):
        if low.startswith(particle) and len(low) - len(particle) >= 3:
            lemma_particle = particle
            break
    if parts_particle != lemma_particle:
        return False
    # With the particle agreed, require the bare stem to appear in the forms.
    base = low[len(lemma_particle):]
    base = base[:-2] if base.endswith("en") else base.rstrip("n")
    if len(base) < 3:
        return True
    body = parts.lower()
    # Strong verbs change the stem vowel, so accept the umlaut-folded form too.
    folded = base.translate(str.maketrans({"ä": "a", "ö": "o", "ü": "u"}))
    return base[:3] in body or folded[:3] in body

# Abgase (Pl.)
PLURAL_ONLY = re.compile(r"\b([A-ZÄÖÜ][A-Za-zÄÖÜäöüß-]{2,30})\s*\(Pl\.\)")
SECTION = re.compile(r"\b(\d+\.\d+(?:\.\d+)?)\s+([A-ZÄÖÜ][A-ZÄÖÜß /-]{3,40})")

# A German sentence in the example column: starts capital, ends in punctuation,
# and contains a function word so headword runs are not mistaken for prose.
SENTENCE = re.compile(r"[A-ZÄÖÜ][^.!?]{14,160}[.!?]")
FUNCTION_WORDS = re.compile(
    r"\b(der|die|das|und|ist|sind|nicht|ich|du|er|sie|wir|ihr|ein|eine|"
    r"mit|für|auf|zu|von|hat|habe|kann|muss|war|im|in|den|dem)\b")

# The front matter is imprint, copyright and legal text — German, but not the
# German a B1 candidate needs. It ends where the thematic list begins.
CONTENT_START = re.compile(r"\b1\.1\s+ABK")
BOILERPLATE = re.compile(
    r"§|©|urheberrechtl|Einwilligung|Herausgeber|Auflage|Verwertung|"
    r"Goethe-Institut|ÖSD|Druck|ISBN|Verlag|Redaktion|Layout|Copyright",
    re.I)

# The headword columns also scan as capital-to-full-stop runs. They are told
# apart from prose by what a word list contains and a sentence does not:
# plural markers, equals signs, and a high density of capitalised tokens.
WORDLIST_MARKERS = re.compile(r"=|,\s*¨?-|\(Pl\.\)|\betc\b|\busw\b")


def _looks_like_prose(s: str) -> bool:
    if WORDLIST_MARKERS.search(s):
        return False
    tokens = s.split()
    if len(tokens) < 4:
        return False
    capitalised = sum(1 for t in tokens[1:] if t[:1].isupper())
    return capitalised / len(tokens) < 0.34


def sections(text: str) -> list[tuple[int, str]]:
    return [(m.start(), f"{m.group(1)} {m.group(2).strip()}")
            for m in SECTION.finditer(text)]


def _ref_at(marks: list[tuple[int, str]], pos: int) -> str | None:
    ref = None
    for start, name in marks:
        if start > pos:
            break
        ref = name
    return ref


def parse_items(text: str, cefr: str) -> list[dict]:
    marks = sections(text)
    items: dict[tuple[str, str], dict] = {}

    def add(lemma, pos_tag, gender, plural, extra, at):
        key = (lemma.lower(), pos_tag)
        items.setdefault(key, {
            "lemma": lemma, "pos": pos_tag, "gender": gender, "plural": plural,
            "principal_parts": extra, "cefr_level": cefr,
            "wortliste_ref": _ref_at(marks, at),
        })

    for m in NOUN.finditer(text):
        add(m.group(2), "noun", m.group(1), m.group(3), None, m.start())
    for m in VERB.finditer(text):
        prefix, stem = m.group(1), m.group(2)
        lemma = f"{prefix}{stem}" if prefix else stem
        parts = f"{m.group(3)}, {m.group(4)}, {m.group(5)} {m.group(6)}"
        add(lemma, "verb", None, None,
            parts if parts_match(lemma, parts) else None, m.start())
    for m in PLURAL_ONLY.finditer(text):
        add(m.group(1), "noun", None, "Pl.", None, m.start())
    return list(items.values())


TOKEN = re.compile(r"[A-Za-zÄÖÜäöüß]{2,}(?:-[A-Za-zÄÖÜäöüß]+)*")


def parse_tokens(text: str) -> set[str]:
    """Every word form the syllabus attests, verbatim.

    The item parsers recognise nouns and verbs by their published notation
    (`die Lösung, -en`), but adjectives, adverbs and particles appear as bare
    words with no notation to match on. Rather than guess at their part of
    speech, admit every attested token to the permitted lexicon and leave the
    teaching decision to the structured items. Front matter is excluded so the
    imprint's legal vocabulary does not become B1 syllabus.
    """
    start = CONTENT_START.search(text)
    body = text[start.start():] if start else text
    return {t.casefold() for t in TOKEN.findall(body) if len(t) > 1}


def parse_sentences(text: str) -> list[str]:
    """Example sentences from the Wortliste. Disabled by default — see below.

    The Wortliste is typeset in two columns and this extractor reads content
    streams in emission order, not layout order, so the two columns interleave:

        "Kreuzen Sie bitte auf dem kr euzt an, Antwortbogen an."
         ^-- column A ------------  ^-- column B  ^-- column A

    The item regexes survive that because they match local patterns, but prose
    does not. Loading these would teach corrupted German, which is precisely the
    failure D4 exists to prevent. Unlocking them needs positional extraction
    (tracking Tm/Td text-matrix operators and bucketing by x-coordinate), which
    is also what the Modellsatz parser will need for the mock exams.
    """
    start = CONTENT_START.search(text)
    text = text[start.start():] if start else text
    out, seen = [], set()
    for m in SENTENCE.finditer(text):
        s = m.group(0).strip()
        if len(FUNCTION_WORDS.findall(s)) < 2 or BOILERPLATE.search(s):
            continue
        if not _looks_like_prose(s):
            continue
        if s.lower() in seen:
            continue
        seen.add(s.lower())
        out.append(s)
    return out


def load_tokens(conn: sqlite3.Connection, tokens: set[str], cefr: str,
                source: str) -> int:
    n = 0
    for token in tokens:
        n += conn.execute(
            "INSERT OR IGNORE INTO syllabus_token (token, cefr_level, source)"
            " VALUES (?, ?, ?)", (token, cefr, source)).rowcount
    conn.commit()
    return n


def load(conn: sqlite3.Connection, items: list[dict], sentences: list[str],
         source: str) -> tuple[int, int]:
    n_items = 0
    for it in items:
        # No gloss available: the article drill is what this item can teach.
        gloss = f"[{it['pos']}]"
        cur = conn.execute(
            """INSERT INTO item
               (lemma, pos, gender, plural, sense_gloss_en, cefr_level,
                wortliste_ref, principal_parts, item_type, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'vocab', ?)
               ON CONFLICT(lemma, sense_gloss_en) DO UPDATE SET
                   principal_parts = coalesce(excluded.principal_parts, item.principal_parts),
                   wortliste_ref = coalesce(excluded.wortliste_ref, item.wortliste_ref)""",
            (it["lemma"], it["pos"], it["gender"], it["plural"], gloss,
             it["cefr_level"], it["wortliste_ref"], it.get("principal_parts"),
             source))
        n_items += cur.rowcount
    n_sent = 0
    for s in sentences:
        cur = conn.execute(
            "INSERT OR IGNORE INTO sentence (de, en, source, cefr_hint) "
            "VALUES (?, NULL, ?, 'B1')", (s, source))
        n_sent += cur.rowcount
    conn.commit()
    return n_items, n_sent


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pdf", nargs="?",
                    default="data/raw/Goethe-Zertifikat_B1_Wortliste.pdf")
    ap.add_argument("--cefr", default="B1")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--with-sentences", action="store_true",
                    help="load example sentences (BROKEN: two-column interleave)")
    args = ap.parse_args()

    path = Path(args.pdf)
    text = extract(path)
    items = parse_items(text, args.cefr)
    sents = parse_sentences(text) if args.with_sentences else []

    nouns = [i for i in items if i["pos"] == "noun" and i["gender"]]
    verbs = [i for i in items if i["pos"] == "verb"]
    print(f"{path.name}: {len(text):,} chars")
    print(f"  {len(items)} items  ({len(nouns)} gendered nouns, {len(verbs)} verbs)")
    print(f"  {len(sents)} example sentences"
          f"{'' if args.with_sentences else '  (disabled: two-column interleave)'}")

    if args.dry_run:
        for i in items[:6]:
            print(f"    {i['gender'] or ''} {i['lemma']} {i['plural'] or ''}"
                  f" {i['principal_parts'] or ''}  <{i['wortliste_ref']}>")
        for s in sents[:4]:
            print(f"    “{s}”")
        return

    conn = connect()
    init_db(conn)
    ni, ns = load(conn, items, sents, f"goethe:{path.name}")
    print(f"  inserted {ni} items, {ns} sentences")


if __name__ == "__main__":
    main()
