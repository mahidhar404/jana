"""Tests for the verb conjugation drill pipeline.

Verifies that:
  * Verbs with principal_parts are now teachable (not rejected by the scheduler).
  * choose_task returns 'conjugation' for verbs without gloss but with parts.
  * grade_conjugation handles exact matches, reversed separable prefixes,
    umlaut folding, and typos.
  * The wortliste parser extracts principal parts from verb entries.
"""

from __future__ import annotations

import sqlite3
import unittest

from jana import grader
from jana.core import choose_task
from jana.db import init_db


class TestConjugationGrading(unittest.TestCase):

    def test_exact_match(self):
        v = grader.grade_conjugation("bog ab", "bog ab")
        self.assertTrue(v.correct)

    def test_perfekt_with_auxiliary(self):
        v = grader.grade_conjugation("ist abgebogen", "ist abgebogen")
        self.assertTrue(v.correct)

    def test_reversed_separable_prefix(self):
        v = grader.grade_conjugation("ab bog", "bog ab")
        self.assertTrue(v.correct)
        self.assertIn("word order", v.note)

    def test_umlaut_fold(self):
        v = grader.grade_conjugation("laeuft", "läuft")
        self.assertTrue(v.correct)
        self.assertIn("umlauts", v.note)

    def test_wrong_answer(self):
        v = grader.grade_conjugation("ging", "bog ab")
        self.assertFalse(v.correct)

    def test_one_letter_off(self):
        v = grader.grade_conjugation("bof ab", "bog ab")
        self.assertFalse(v.correct)
        self.assertIn("one letter", v.note)

    def test_empty_answer(self):
        v = grader.grade_conjugation("", "läuft")
        self.assertFalse(v.correct)
        self.assertIn("no answer", v.note)

    def test_grade_dispatcher(self):
        """The 'conjugation' key is registered in GRADERS."""
        v = grader.grade("conjugation", "fährt", "fährt")
        self.assertTrue(v.correct)


class TestChooseTaskVerbs(unittest.TestCase):

    def test_verb_with_principal_parts(self):
        task = choose_task("[verb]", None, 1, "biegt ab, bog ab, ist abgebogen")
        self.assertEqual(task, "conjugation")

    def test_verb_without_parts_or_gloss(self):
        task = choose_task("[verb]", None, 1, None)
        self.assertIsNone(task)

    def test_noun_with_gender_no_gloss(self):
        task = choose_task("[noun]", "die", 1, None)
        self.assertEqual(task, "article")

    def test_glossed_item_unaffected(self):
        task = choose_task("to run", None, 1, "läuft, lief, ist gelaufen")
        self.assertEqual(task, "recognise")

    def test_glossed_item_rung2(self):
        task = choose_task("to run", None, 2, "läuft, lief, ist gelaufen")
        self.assertEqual(task, "cued_recall")


class TestVerbTeachability(unittest.TestCase):
    """Verbs with principal_parts should pass the TEACHABLE predicate."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_verb_with_parts_is_teachable(self):
        self.conn.execute(
            """INSERT INTO item
               (lemma, pos, gender, plural, sense_gloss_en, cefr_level,
                principal_parts, item_type, source)
               VALUES ('abbiegen', 'verb', NULL, NULL, '[verb]', 'B1',
                       'biegt ab, bog ab, ist abgebogen', 'vocab', 'test')""")
        self.conn.commit()

        from jana.scheduler import TEACHABLE
        row = self.conn.execute(
            f"SELECT * FROM item i WHERE {TEACHABLE}").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["lemma"], "abbiegen")

    def test_verb_without_parts_is_not_teachable(self):
        self.conn.execute(
            """INSERT INTO item
               (lemma, pos, gender, plural, sense_gloss_en, cefr_level,
                principal_parts, item_type, source)
               VALUES ('machen', 'verb', NULL, NULL, '[verb]', 'A1',
                       NULL, 'vocab', 'test')""")
        self.conn.commit()

        from jana.scheduler import TEACHABLE
        row = self.conn.execute(
            f"SELECT * FROM item i WHERE {TEACHABLE}").fetchone()
        self.assertIsNone(row)


class TestWortlistePrincipalParts(unittest.TestCase):
    """Verify the regex extracts principal parts from verb entries."""

    def test_parse_separable_verb(self):
        from jana.ingest.wortliste import parse_items
        text = "abbiegen, biegt ab, bog ab, ist abgebogen"
        items = parse_items(text, "B1")
        verbs = [i for i in items if i["pos"] == "verb"]
        self.assertTrue(len(verbs) >= 1)
        self.assertIsNotNone(verbs[0]["principal_parts"])
        self.assertIn("biegt ab", verbs[0]["principal_parts"])

    def test_parse_simple_verb(self):
        from jana.ingest.wortliste import parse_items
        text = "laufen, läuft, lief, ist gelaufen"
        items = parse_items(text, "B1")
        verbs = [i for i in items if i["pos"] == "verb"]
        self.assertTrue(len(verbs) >= 1)
        self.assertIn("läuft", verbs[0]["principal_parts"])


if __name__ == "__main__":
    unittest.main()
