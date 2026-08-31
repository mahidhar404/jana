"""The grammar curriculum, and its link to the learner's own course videos.

Where this comes from
---------------------
Scraping the 19 courses shows two teaching patterns, and they differ by level:

  * **A1 courses are organised by theme** — Familie und Freunde, Essen und
    trinken, Haus und wohnen, Mein Tag. Grammar arrives as whatever the
    situation needs.
  * **The B1 course is organised by grammar point** — Modalpartikel,
    Kausaladverbien, Konjunktionen, Pronomen, Passiv, Konjunktiv II —
    interleaved with usage and culture lessons (Redewendungen, Deutsch am
    Arbeitsplatz).

That is the standard CEFR progression and it is worth copying: theme-first while
there is no grammar to hang anything on, grammar-first once there is.

The spine below is **hand-written**, not scraped. Scraped headings are noisy
("repitionen - only if needed"), unordered, and cover B2 material that is out of
scope. What the corpus is good for is the *second* column: each grammar point is
matched to the lesson in his own courses that teaches it, so a rule he fails can
send him to eight minutes of video he already owns rather than to a generated
explanation.

Ordering is by `rank`, and `prerequisite` names the point that must come first —
adjective endings are unlearnable before cases, and the Perfekt is unlearnable
before participles.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from pathlib import Path

from jana.config import CORPUS_ROOT
from jana.db import connect, init_db

# (name, level, rank, prerequisite, English description, search terms for the corpus)
CURRICULUM: list[tuple[str, str, int, str | None, str, str]] = [
    ("Präsens regelmäßig", "A1", 10, None,
     "Present tense of regular verbs: the -e/-st/-t/-en endings.", "präsens verb conjugation"),
    ("Personalpronomen", "A1", 20, None,
     "ich, du, er/sie/es, wir, ihr, sie/Sie — and why Sie is capitalised.", "pronomen personal"),
    ("sein und haben", "A1", 30, None,
     "The two verbs everything else is built on. Both irregular, both unavoidable.", "sein haben"),
    ("Artikel und Genus", "A1", 40, None,
     "der/die/das. The single highest-yield German failure mode.", "artikel genus der die das"),
    ("Nominativ", "A1", 50, "Artikel und Genus",
     "The subject case — the form the dictionary gives you.", "nominativ"),
    ("Akkusativ", "A1", 60, "Nominativ",
     "The direct-object case. Only masculine changes: der → den.", "akkusativ"),
    ("Verbstellung V2", "A1", 70, None,
     "The conjugated verb is the second element in a main clause. Not the second word.", "wortstellung verbstellung satzbau"),
    ("Negation", "A1", 80, None,
     "nicht versus kein, and where nicht goes in the sentence.", "negation nicht kein"),
    ("Plural", "A1", 90, "Artikel und Genus",
     "Five plural patterns and no reliable rule — learn it with the noun.", "plural"),
    ("Possessivartikel", "A1", 100, "Akkusativ",
     "mein, dein, sein — declined like ein.", "possessiv"),
    ("Trennbare Verben", "A1", 110, "Verbstellung V2",
     "aufstehen → ich stehe auf. The prefix goes to the end.", "trennbare verben"),
    ("Modalverben", "A1", 120, "Präsens regelmäßig",
     "können, müssen, wollen, dürfen, sollen, mögen — and the second verb at the end.", "modalverben"),
    ("Imperativ", "A2", 130, "Verbstellung V2",
     "Giving instructions: Geh! Gehen Sie! Geht!", "imperativ"),
    ("Dativ", "A2", 140, "Akkusativ",
     "The indirect-object case, and the prepositions that always take it.", "dativ"),
    ("Wechselpräpositionen", "A2", 150, "Dativ",
     "in, an, auf — accusative for movement, dative for position.", "wechselpräposition präposition"),
    ("Perfekt", "A2", 160, "Modalverben",
     "The spoken past: haben/sein + Partizip II.", "perfekt partizip"),
    ("Präteritum", "A2", 170, "Perfekt",
     "The written past, and the handful of verbs that use it in speech.", "präteritum imperfekt"),
    ("Nebensätze mit weil/dass", "A2", 180, "Verbstellung V2",
     "Subordinate clauses send the verb to the end.", "nebensatz weil dass konjunktion"),
    ("Adjektivdeklination", "A2", 190, "Dativ",
     "Adjective endings after der-, ein- and no article. The hardest table in German.", "adjektivdeklination adjektiv"),
    ("Komparativ und Superlativ", "A2", 200, "Adjektivdeklination",
     "größer, am größten — and the irregulars gut/besser/am besten.", "komparativ superlativ"),
    ("Reflexive Verben", "A2", 210, "Akkusativ",
     "sich freuen, sich waschen — and when the reflexive is dative.", "reflexiv"),
    ("Konjunktionen", "B1", 220, "Nebensätze mit weil/dass",
     "aber, denn, sondern, oder versus weil, obwohl, damit — and what each does to word order.", "konjunktionen"),
    ("Relativsätze", "B1", 230, "Nebensätze mit weil/dass",
     "der Mann, der… — relative pronouns take their case from their own clause.", "relativsatz relativpronomen"),
    ("Genitiv", "B1", 240, "Dativ",
     "Possession in writing, and the prepositions wegen/trotz/während.", "genitiv"),
    ("Passiv", "B1", 250, "Perfekt",
     "werden + Partizip II. Common in notices, instructions and news.", "passiv vorgangspassiv"),
    ("Konjunktiv II", "B1", 260, "Präteritum",
     "würde, hätte, wäre, könnte — politeness, hypotheticals, advice.", "konjunktiv"),
    ("Infinitiv mit zu", "B1", 270, "Modalverben",
     "Ich habe vor, nach Berlin zu fahren.", "infinitiv zu"),
    ("Indirekte Fragen", "B1", 280, "Nebensätze mit weil/dass",
     "Können Sie mir sagen, wo der Bahnhof ist?", "indirekte fragen"),
    ("Temporale Nebensätze", "B1", 290, "Nebensätze mit weil/dass",
     "wenn, als, während, bevor, nachdem, seit — and which past tense each wants.", "temporal wenn als"),
    ("Finalsätze", "B1", 300, "Nebensätze mit weil/dass",
     "um…zu versus damit — expressing purpose.", "final damit um zu"),
    ("Kausaladverbien", "B1", 310, "Konjunktionen",
     "deshalb, deswegen, darum — because, without a subordinate clause.", "kausaladverbien"),
    ("Verben mit Präposition", "B1", 320, "Wechselpräpositionen",
     "warten auf, sich freuen über — the preposition is part of the verb.", "verben mit präposition"),
    ("Partizip als Adjektiv", "B1", 330, "Adjektivdeklination",
     "das gekochte Ei, die lachenden Kinder.", "partizip adjektiv"),
    ("Modalpartikel", "B1", 340, "Konjunktionen",
     "doch, mal, ja, eben — untranslatable, and everywhere in real speech.", "modalpartikel"),
]

STOP = {"und", "der", "die", "das", "mit", "im", "von", "zu", "learn", "german",
        "ii", "i", "nicht", "verb", "verben", "class", "part", "teil"}

# Terms that identify a grammar topic on their own. Everything else needs a
# second word to agree, because one common word in common matches almost
# anything — "Negation" matched a lesson about the board game
# *Mensch ärgere dich nicht* on the strength of "nicht" alone.
DISTINCTIVE = {
    "akkusativ", "dativ", "genitiv", "nominativ", "präteritum", "perfekt",
    "konjunktiv", "passiv", "vorgangspassiv", "imperativ", "modalverben",
    "reflexiv", "relativsatz", "relativpronomen", "nebensatz", "adjektivdeklination",
    "komparativ", "superlativ", "präposition", "wechselpräposition", "konjunktionen",
    "artikel", "pronomen", "plural", "partizip", "infinitiv", "wortstellung",
    "verbstellung", "satzbau", "trennbare", "possessiv", "modalpartikel",
    "kausaladverbien", "negation", "genus", "temporal", "damit",  # "final" omitted: it is English too
}


def lesson_index(root: Path) -> list[tuple[str, str]]:
    """(course, section) for every numbered section directory in the corpus."""
    out: list[tuple[str, str]] = []
    for parent, directories, _files in os.walk(root):
        relative = os.path.relpath(parent, root)
        if relative == ".":
            continue
        parts = relative.split(os.sep)
        if len(parts) < 2:
            continue
        if re.match(r"^\d+[\s.\-_]", parts[-1]):
            out.append((parts[0], parts[-1]))
    return out


def match_lesson(terms: str, lessons: list[tuple[str, str]]) -> dict | None:
    """Best corpus lesson for a grammar point, by term overlap on the heading."""
    wanted = {t for t in re.split(r"\W+", terms.casefold()) if t and t not in STOP}
    best, best_score = None, 0
    for course, section in lessons:
        words = {w for w in re.split(r"\W+", section.casefold()) if w and w not in STOP}
        overlap = wanted & words
        if not overlap:
            continue
        # One distinctive term is proof; otherwise two words must agree.
        if not (overlap & DISTINCTIVE) and len(overlap) < 2:
            continue
        score = len(overlap) + (2 if overlap & DISTINCTIVE else 0)
        if score > best_score:
            best, best_score = (course, section), score
    if best is None:
        return None
    return {"course": best[0], "section": best[1], "score": best_score}


def load(conn: sqlite3.Connection, corpus: Path | None = None) -> tuple[int, int]:
    lessons = lesson_index(corpus) if corpus and corpus.is_dir() else []
    written = linked = 0
    for name, level, rank, prerequisite, description, terms in CURRICULUM:
        lesson = match_lesson(terms, lessons) if lessons else None
        linked += 1 if lesson else 0
        cursor = conn.execute(
            """INSERT INTO grammar_point
                   (name, cefr_level, description_en, prerequisite_ids)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET
                   cefr_level = excluded.cefr_level,
                   description_en = excluded.description_en,
                   prerequisite_ids = excluded.prerequisite_ids""",
            (name, level, description,
             json.dumps({"rank": rank, "requires": prerequisite, "lesson": lesson},
                        ensure_ascii=False)))
        written += max(cursor.rowcount, 0)
    conn.commit()
    return written, linked


def curriculum(conn: sqlite3.Connection, level: str | None = None) -> list[dict]:
    rows = conn.execute("SELECT * FROM grammar_point").fetchall()
    points = []
    for row in rows:
        meta = json.loads(row["prerequisite_ids"] or "{}")
        if level and row["cefr_level"] != level:
            continue
        points.append({
            "id": row["id"], "name": row["name"], "level": row["cefr_level"],
            "description": row["description_en"], "rank": meta.get("rank", 999),
            "requires": meta.get("requires"), "lesson": meta.get("lesson"),
        })
    return sorted(points, key=lambda p: p["rank"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    conn = connect()
    init_db(conn)
    written, linked = load(conn, CORPUS_ROOT)
    print(f"{len(CURRICULUM)} grammar points · {written} written · "
          f"{linked} matched to a lesson in the corpus")
    for point in curriculum(conn)[:8]:
        lesson = point["lesson"]
        where = f"→ {lesson['section'][:46]}" if lesson else "(no corpus lesson)"
        print(f"   {point['level']}  {point['name']:28s} {where}")


if __name__ == "__main__":
    main()
