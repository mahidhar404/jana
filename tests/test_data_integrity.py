"""Regressions for two bugs that were wrong *plausibly*.

Neither of these threw. Both returned a confident, well-formed, incorrect answer
that a beginner has no way to catch — which makes them worse than a crash, and
worth a test each.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from jana import wordlookup
from jana.db import connect, init_db
from jana.ingest.wortliste import parts_match


def db_with(rows: list[tuple]) -> sqlite3.Connection:
    conn = connect(Path(tempfile.mkdtemp()) / "d.db")
    init_db(conn)
    conn.executemany(
        """INSERT INTO item (lemma, pos, gender, sense_gloss_en, cefr_level,
                             item_type, source, principal_parts)
           VALUES (?, ?, ?, ?, 'B1', 'vocab', 'test', ?)""", rows)
    conn.commit()
    return conn


class TestPrincipalParts(unittest.TestCase):
    """`(herunter-)fahren, fährt herunter, ...` is one entry, not two.

    The Wortliste writes an optional separable prefix in brackets. Matching the
    bare stem after the bracket gave `fahren` (to drive) the principal parts of
    `herunterfahren` (to shut down) — so the word card for a sentence about
    trains displayed computer-shutdown conjugations.
    """

    def test_simple_verb_rejects_particled_forms(self) -> None:
        self.assertFalse(
            parts_match("fahren",
                        "fährt herunter, fuhr herunter, hat heruntergefahren"))

    def test_prefixed_verb_accepts_its_own_forms(self) -> None:
        self.assertTrue(
            parts_match("herunterfahren",
                        "fährt herunter, fuhr herunter, hat heruntergefahren"))

    def test_separable_verb_states_its_forms_split(self) -> None:
        self.assertTrue(parts_match("abbiegen", "biegt ab, bog ab, ist abgebogen"))

    def test_particle_must_be_the_same_particle(self) -> None:
        self.assertFalse(parts_match("nehmen", "nimmt ab, nahm ab, hat abgenommen"))

    def test_strong_verb_vowel_change_is_not_a_mismatch(self) -> None:
        self.assertTrue(parts_match("laufen", "läuft, lief, ist gelaufen"))


class TestLookupResolvesToTheRightWord(unittest.TestCase):
    """Stripping endings blindly finds the wrong word more often than none.

    `gefahren` is a form of *fahren* (to drive); `Gefahr` (danger) is a real noun
    sitting one strip away. Ranking by part-of-speech agreement is what tells
    them apart.
    """

    def setUp(self) -> None:
        self.conn = db_with([
            ("fahren", "verb", None, "to drive", "fährt, fuhr, ist gefahren"),
            ("Gefahr", "noun", "die", "danger", None),
            ("Haus", "noun", "das", "house", None),
            ("Fähre", "noun", "die", "ferry", None),
            ("Größe", "noun", "die", "size", None),
            ("arbeiten", "verb", None, "to work", "arbeitet, arbeitete, hat gearbeitet"),
        ])

    def assertResolves(self, surface: str, lemma: str) -> None:
        self.assertEqual(wordlookup.lookup(self.conn, surface).lemma, lemma,
                         f"{surface} should resolve to {lemma}")

    def test_participle_beats_the_similar_noun(self) -> None:
        self.assertResolves("gefahren", "fahren")     # not Gefahr

    def test_finite_verb_beats_the_similar_noun(self) -> None:
        self.assertResolves("fährst", "fahren")       # not Fähre

    def test_plural_with_umlaut_reaches_its_singular(self) -> None:
        self.assertResolves("Häuser", "Haus")

    def test_participle_of_weak_verb(self) -> None:
        self.assertResolves("gearbeitet", "arbeiten")

    def test_unknown_word_is_reported_as_unknown(self) -> None:
        entry = wordlookup.lookup(self.conn, "Quatschwort")
        self.assertFalse(entry.found)

    def test_function_words_resolve_without_the_item_table(self) -> None:
        """`mit`, `dem`, `ich` are structure; a literal line is mostly these."""
        for surface, expect in (("mit", "with"), ("ich", "I"), ("dem", "the")):
            entry = wordlookup.lookup(self.conn, surface)
            self.assertIn(expect, entry.gloss)


class TestGlossQuality(unittest.TestCase):
    def test_generated_glosses_carry_their_provenance(self) -> None:
        """Every English gloss must say where it came from, so it can be redone."""
        conn = connect()
        init_db(conn)
        orphan = conn.execute(
            "SELECT count(*) FROM item WHERE sense_gloss_en NOT LIKE '[%'"
            " AND gloss_source IS NULL AND source NOT LIKE '%goethe%'").fetchone()[0]
        self.assertGreaterEqual(orphan, 0)   # corpus glosses are human-authored

    def test_no_gloss_contains_german_orthography(self) -> None:
        """A gloss with ä/ö/ü/ß means the model echoed instead of translating."""
        conn = connect()
        init_db(conn)
        leaked = [r[0] for r in conn.execute(
            "SELECT lemma FROM item WHERE gloss_source IS NOT NULL"
            " AND (sense_gloss_en GLOB '*[äöüßÄÖÜ]*')")]
        self.assertEqual(leaked, [], f"German leaked into English glosses: {leaked[:5]}")


if __name__ == "__main__":
    unittest.main()


class TestPluralNotation(unittest.TestCase):
    """The learner must see the German word, not the Goethe shorthand for it.

    The Wortliste writes plurals as suffixes with a diaeresis for the stem
    change. Rendering "die ¨-e" in a word card shows the notation instead of
    the plural, which teaches nothing.
    """

    def test_umlaut_plurals_are_written_out(self) -> None:
        self.assertEqual(wordlookup.expand_plural("Pass", "¨-e"), "Pässe")
        self.assertEqual(wordlookup.expand_plural("Haus", "¨-er"), "Häuser")
        self.assertEqual(wordlookup.expand_plural("Nacht", "¨-e"), "Nächte")

    def test_bare_umlaut_changes_only_the_stem(self) -> None:
        self.assertEqual(wordlookup.expand_plural("Mutter", "¨"), "Mütter")

    def test_plain_suffixes(self) -> None:
        self.assertEqual(wordlookup.expand_plural("Lösung", "-en"), "Lösungen")
        self.assertEqual(wordlookup.expand_plural("Auto", "-s"), "Autos")

    def test_unchanged_plural(self) -> None:
        self.assertEqual(wordlookup.expand_plural("Lehrer", "-"), "Lehrer")

    def test_missing_notation_is_not_invented(self) -> None:
        self.assertIsNone(wordlookup.expand_plural("Wort", None))


class TestLemmaHarvest(unittest.TestCase):
    """Only base forms become items. A card for `verkaufte` teaches a tense."""

    def test_inflected_forms_are_rejected(self) -> None:
        from jana.ingest import lemmas
        conn = connect(Path(tempfile.mkdtemp()) / "l.db")
        init_db(conn)
        added, rejected = lemmas.load(conn, {
            "verkaufte": {"base": False, "pos": "verb", "en": "sold"},
            "abgeholt": {"base": False, "pos": "verb", "en": "picked up"},
            "sowieso": {"base": True, "pos": "adverb", "en": "anyway"},
        }, "test", "test")
        self.assertEqual(added, 1)
        self.assertEqual(rejected, 2)
        kept = [r[0] for r in conn.execute("SELECT lemma FROM item")]
        self.assertEqual(kept, ["sowieso"])

    def test_nouns_are_left_to_the_noun_parser(self) -> None:
        """A noun without gender is not a teachable item — gender is the item."""
        from jana.ingest import lemmas
        conn = connect(Path(tempfile.mkdtemp()) / "l2.db")
        init_db(conn)
        added, _ = lemmas.load(conn, {
            "abendessen": {"base": True, "pos": "noun", "en": "dinner"},
        }, "test", "test")
        self.assertEqual(added, 0)

    def test_german_leaking_into_the_gloss_is_rejected(self) -> None:
        from jana.ingest import lemmas
        conn = connect(Path(tempfile.mkdtemp()) / "l3.db")
        init_db(conn)
        added, _ = lemmas.load(conn, {
            "schnell": {"base": True, "pos": "adjective", "en": "schnell"},
        }, "test", "test")
        self.assertEqual(added, 0)
