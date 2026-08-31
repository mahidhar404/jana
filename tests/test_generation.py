"""The safety property that lets a model talk to a beginner at all.

D4 forbids local models from authoring German the learner learns from, because
a beginner cannot detect a subtle error. The conversational tutor needs to
produce German anyway. What makes that safe is not trust — it is that every
sentence is checked against the syllabus before it is shown, and regenerated if
it fails. These tests pin that check down.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from jana import lexicon
from jana.db import connect, init_db


def lexicon_db() -> sqlite3.Connection:
    conn = connect(Path(tempfile.mkdtemp()) / "lex.db")
    init_db(conn)
    conn.executemany(
        "INSERT INTO syllabus_token (token, cefr_level, source) VALUES (?, 'B1', 't')",
        [(w,) for w in ("hund", "groß", "park", "laufen", "gehen", "morgen",
                        "zug", "berlin", "wohnung", "putzen", "freitag",
                        "telefonieren", "gestern", "freundin", "fahren")])
    conn.commit()
    return conn


class TestLexicon(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = lexicon_db()
        self.lex = lexicon.build(self.conn)

    def tearDown(self) -> None:
        self.conn.close()

    def test_inflected_forms_of_known_words_pass(self) -> None:
        """laufen -> läuft: German changes the stem, not only the ending."""
        for word in ("läuft", "geht", "gefahren", "telefoniert", "größer"):
            self.assertTrue(lexicon._known(word, self.lex), word)

    def test_out_of_syllabus_vocabulary_is_caught(self) -> None:
        report = lexicon.check(
            "Die Quantenverschränkung ist epistemologisch unzugänglich.", self.lex)
        self.assertFalse(report.ok)
        self.assertIn("epistemologisch", report.unknown)

    def test_one_new_word_is_allowed_and_surfaced(self) -> None:
        """A single unknown word in context is i+1, not a failure."""
        report = lexicon.check("Der Hund ist im Park und bellt.", self.lex)
        self.assertTrue(report.ok)
        self.assertEqual(report.new_words, ["bellt"])

    def test_two_new_words_is_refused(self) -> None:
        report = lexicon.check(
            "Der Hund bellt und knurrt im Park.", self.lex)
        self.assertFalse(report.ok)
        self.assertEqual(report.new_words, [])

    def test_function_words_never_count_against_a_sentence(self) -> None:
        report = lexicon.check(
            "Ich möchte mit dem Zug nach Berlin fahren, weil es nicht "
            "teuer ist.", self.lex)
        self.assertTrue(report.ok, report.unknown)

    def test_names_are_not_syllabus_violations(self) -> None:
        report = lexicon.check("Der Hund von Mahidhar ist groß.", self.lex)
        self.assertTrue(report.ok)
        self.assertIn("Mahidhar", report.proper_nouns)


class TestTargetDetection(unittest.TestCase):
    """Producing a target word in free conversation must count as a review."""

    def setUp(self) -> None:
        self.conn = lexicon_db()
        self.conn.execute(
            """INSERT INTO item (id, lemma, pos, gender, sense_gloss_en,
                                 cefr_level, item_type, source)
               VALUES (1, 'laufen', 'verb', NULL, 'to run', 'A1', 'vocab', 't')""")
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    def test_inflected_use_of_a_target_counts(self) -> None:
        from jana import tutor
        rows = self.conn.execute(
            "SELECT id AS item_id, lemma, gender, sense_gloss_en, 'text' AS modality"
            " FROM item").fetchall()
        lex = lexicon.build(self.conn)
        used = tutor.detect_targets("Ich läuft jeden Tag im Park.", rows, lex)
        self.assertEqual(used, ["laufen"])

    def test_unused_target_is_not_credited(self) -> None:
        from jana import tutor
        rows = self.conn.execute(
            "SELECT id AS item_id, lemma, gender, sense_gloss_en, 'text' AS modality"
            " FROM item").fetchall()
        lex = lexicon.build(self.conn)
        self.assertEqual(tutor.detect_targets("Ich esse Brot.", rows, lex), [])


if __name__ == "__main__":
    unittest.main()
