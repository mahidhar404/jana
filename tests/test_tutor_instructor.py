"""Tests for the Jarvis-style AI German Instructor features.

Verifies:
  * Token lookup (lemma, gender, English definition, plural, inflection resolution).
  * Upgraded tutor Turn model (literal_en, grammar_tip, quick_replies, corrections).
  * Web API endpoints /api/lookup and /api/explain.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from jana.db import connect, init_db
from jana.lexicon import lookup_token
from jana.llm import Reply
from jana.tutor import Turn, _fallback


class TestInstructorLookup(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = connect(":memory:")
        init_db(self.conn)
        self.conn.execute(
            """INSERT INTO item (lemma, pos, gender, plural, sense_gloss_en, cefr_level, principal_parts, item_type, source)
               VALUES ('Hund', 'noun', 'der', 'Hunde', 'dog', 'A1', NULL, 'vocab', 'test')""")
        self.conn.execute(
            """INSERT INTO item (lemma, pos, gender, plural, sense_gloss_en, cefr_level, principal_parts, item_type, source)
               VALUES ('fahren', 'verb', NULL, NULL, 'to drive / to travel', 'A1', 'fährt, fuhr, ist gefahren', 'vocab', 'test')""")
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    def test_lookup_common_function_word(self) -> None:
        d = lookup_token(self.conn, "mit")
        self.assertEqual(d["lemma"], "mit")
        # Meaning and grammar are separate fields now: "en" is what the word
        # means, "note" is how it is behaving here. Packing both into "en" made
        # the word-by-word line read "with (Dativ)" for a single token.
        self.assertIn("with", d["en"])
        self.assertIn("dative", (d["note"] or "").lower())

    def test_lookup_exact_noun(self) -> None:
        d = lookup_token(self.conn, "Hund")
        self.assertEqual(d["lemma"], "Hund")
        self.assertEqual(d["gender"], "der")
        self.assertEqual(d["en"], "dog")
        self.assertEqual(d["plural"], "Hunde")

    def test_lookup_inflected_verb(self) -> None:
        d = lookup_token(self.conn, "fährst")
        self.assertEqual(d["lemma"], "fahren")
        self.assertIn("drive", d["en"])
        self.assertEqual(d["principal_parts"], "fährt, fuhr, ist gefahren")

    def test_lookup_past_participle(self) -> None:
        d = lookup_token(self.conn, "gefahren")
        self.assertEqual(d["lemma"], "fahren")
        self.assertIn("drive", d["en"].lower())
        self.assertIn("form of", (d["note"] or "").lower())


class TestInstructorTurnStructure(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = connect(":memory:")
        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()

    def test_fallback_has_literal_and_tips(self) -> None:
        turn = _fallback([])
        self.assertTrue(turn.de)
        self.assertTrue(turn.en)
        self.assertTrue(turn.literal_en)
        self.assertTrue(turn.grammar_tip)
        self.assertTrue(len(turn.quick_replies) > 0)


class TestInstructorWebApi(unittest.TestCase):
    def setUp(self) -> None:
        self._previous = os.environ.get("JANA_DB")
        os.environ["JANA_DB"] = str(Path(tempfile.mkdtemp()) / "inst_test.db")
        conn = connect()
        init_db(conn)
        conn.execute(
            """INSERT INTO item (lemma, pos, gender, plural, sense_gloss_en, cefr_level, principal_parts, item_type, source)
               VALUES ('Buch', 'noun', 'das', 'Bücher', 'book', 'A1', NULL, 'vocab', 'test')""")
        conn.commit()
        conn.close()

        from jana.web import app
        self.client = TestClient(app)

    def tearDown(self) -> None:
        if self._previous is None:
            os.environ.pop("JANA_DB", None)
        else:
            os.environ["JANA_DB"] = self._previous

    def test_api_lookup_endpoint(self) -> None:
        res = self.client.get("/api/lookup?word=Buch").json()
        self.assertEqual(res["lemma"], "Buch")
        self.assertEqual(res["gender"], "das")
        self.assertEqual(res["en"], "book")

    def test_api_explain_endpoint(self) -> None:
        mock_reply = Reply(
            text='{"literal": "I | read | a | book", "grammar_breakdown": "Akkusativ neuter: ein Buch.", "key_rule": "Direct object takes Akkusativ."}',
            model="mock", tier="local", latency_ms=10, ok=True
        )
        with patch("jana.llm.authored", return_value=mock_reply):
            res = self.client.post("/api/explain", json={"text": "Ich lese ein Buch."}).json()
            self.assertIn("literal", res)
            self.assertIn("grammar_breakdown", res)
            self.assertIn("key_rule", res)
            self.assertTrue(len(res["words"]) > 0)


if __name__ == "__main__":
    unittest.main()
