"""The exercise bank, and grammar riding the same scheduler as vocabulary."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from jana import bank, grammar, modules
from jana.db import connect, init_db
from jana.ingest.grammar import load as load_curriculum


def fresh() -> sqlite3.Connection:
    conn = connect(Path(tempfile.mkdtemp()) / "b.db")
    init_db(conn)
    return conn


TASK = {"modul": "lesen", "teil": 4, "day": 1, "provenance": "remote",
        "validated": True, "instruction_de": "Dafür oder dagegen?",
        "body": {"question": "Handys in der Schule?",
                 "opinions": [{"name": "Lena", "text": "Gute Idee.", "stance": "dafür"}]}}


class TestNothingIsDiscarded(unittest.TestCase):
    """Every generated question is kept. That was the whole point of the bank."""

    def test_a_task_survives_a_round_trip(self) -> None:
        conn = fresh()
        exercise_id = bank.save(conn, TASK)
        revived = bank.get(conn, exercise_id)
        self.assertEqual(revived["body"], TASK["body"])
        self.assertEqual(revived["teil"], 4)
        self.assertTrue(revived["from_bank"])

    def test_saving_the_same_task_twice_does_not_duplicate(self) -> None:
        conn = fresh()
        first = bank.save(conn, TASK)
        second = bank.save(conn, dict(TASK))
        self.assertEqual(first, second)
        self.assertEqual(conn.execute("SELECT count(*) FROM exercise").fetchone()[0], 1)

    def test_an_unseen_task_is_served_before_a_seen_one(self) -> None:
        conn = fresh()
        bank.save(conn, TASK)
        second = dict(TASK, body={**TASK["body"], "question": "Autos in der Stadt?"})
        bank.save(conn, second)
        first = bank.unseen(conn, "lesen", 4)
        bank.mark_seen(conn, first["exercise_id"])
        nxt = bank.unseen(conn, "lesen", 4)
        self.assertIsNotNone(nxt)
        self.assertNotEqual(nxt["exercise_id"], first["exercise_id"])

    def test_the_bank_reports_empty_when_everything_is_seen(self) -> None:
        conn = fresh()
        exercise_id = bank.save(conn, TASK)
        bank.mark_seen(conn, exercise_id)
        self.assertIsNone(bank.unseen(conn, "lesen", 4))
        self.assertEqual(bank.count(conn, "lesen", 4), 0)


class TestEnglishFieldsAreNotValidatedAsGerman(unittest.TestCase):
    """`why` is an English explanation. Checking it against a German syllabus
    rejects the exercise for containing English, which is what it is for —
    and that is exactly why grammar generation returned None at first."""

    def test_why_is_excluded(self) -> None:
        payload = {"sentence": "Ich fahre ___ dem Zug.",
                   "why": "mit always takes the dative case"}
        german = modules._german_strings(payload)
        self.assertIn("Ich fahre ___ dem Zug.", german)
        self.assertNotIn("mit always takes the dative case", german)

    def test_instruction_en_is_excluded(self) -> None:
        payload = {"instruction_de": "Schreiben Sie im Perfekt.",
                   "instruction_en": "Rewrite in the perfect tense."}
        german = modules._german_strings(payload)
        self.assertEqual(german, ["Schreiben Sie im Perfekt."])


class TestGrammarScheduling(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = fresh()
        load_curriculum(self.conn, None)

    def test_prerequisites_gate_what_is_offered(self) -> None:
        """Adjective endings before cases is not a harder lesson, it is an
        impossible one. The scheduler must not offer it."""
        offered = {p["name"] for p in grammar.due(self.conn, 40)}
        self.assertIn("Artikel und Genus", offered)
        self.assertNotIn("Adjektivdeklination", offered)

    def test_a_point_unlocks_once_its_prerequisite_is_practised(self) -> None:
        points = {p["name"]: p["id"] for p in grammar.progress(self.conn)}
        locked = {p["name"] for p in grammar.progress(self.conn) if p["locked"]}
        self.assertIn("Nominativ", locked)
        for _ in range(3):
            grammar.record(self.conn, points["Artikel und Genus"], True, "cloze")
        from jana.project import rebuild
        rebuild(self.conn)
        still_locked = {p["name"] for p in grammar.progress(self.conn) if p["locked"]}
        self.assertNotIn("Nominativ", still_locked)

    def test_word_order_errors_are_named_as_such(self) -> None:
        result = grammar.check("order", "Berlin nach fahre ich",
                               "Ich fahre nach Berlin")
        self.assertFalse(result["correct"])
        self.assertIn("order", result["note"].lower())

    def test_a_genuinely_wrong_answer_gets_no_consolation(self) -> None:
        result = grammar.check("order", "Ich esse Brot", "Ich fahre nach Berlin")
        self.assertEqual(result["note"], "")

    def test_grammar_attempts_do_not_pollute_item_state(self) -> None:
        """Grammar attempts share the `attempt` kind but carry no item_id."""
        points = {p["name"]: p["id"] for p in grammar.progress(self.conn)}
        grammar.record(self.conn, points["Modalverben"], True, "cloze")
        from jana.project import rebuild
        rebuild(self.conn)
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM item_state").fetchone()[0], 0)
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM grammar_state").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
