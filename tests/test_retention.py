"""Retention machinery: story-anchored review, and the semantic store.

The exam is four months out, so the only question that matters about any of
this is whether a word met in September is still there in January. These pin
the two mechanisms that decide that.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from jana import memory, modules, story
from jana.db import connect, init_db


def seeded() -> sqlite3.Connection:
    conn = connect(Path(tempfile.mkdtemp()) / "r.db")
    init_db(conn)
    conn.executemany(
        """INSERT INTO item (id, lemma, pos, gender, sense_gloss_en, cefr_level,
                             item_type, source)
           VALUES (?, ?, 'noun', 'der', ?, 'A1', 'vocab', 'test')""",
        [(i, f"Wort{i}", f"word{i}") for i in range(1, 9)])
    for number in (1, 2, 4, 8, 17, 36):
        conn.execute(
            """INSERT INTO story_day (day_number, date, title, setting_de,
                   setting_en, npc_name, npc_role, theme, target_ids, created_at)
               VALUES (?, '2026-08-29', ?, 'Ort', 'Place', 'Anna', 'Frau',
                       'Reisen', '[]', 'now')""", (number, f"Tag {number}"))
    conn.commit()
    return conn


class TestExpandingReview(unittest.TestCase):
    """A word must come back at widening gaps, not on a fixed weekly loop."""

    def test_intervals_expand(self) -> None:
        gaps = story.REVIEW_OFFSETS
        self.assertEqual(list(gaps), sorted(gaps))
        for earlier, later in zip(gaps, gaps[1:]):
            self.assertGreater(later, earlier)

    def test_last_interval_still_lands_before_the_exam(self) -> None:
        """A word met on day 1 must be revisited inside a 120-day runway."""
        self.assertLessEqual(max(story.REVIEW_OFFSETS), 60)

    def test_day_37_looks_back_at_the_right_days(self) -> None:
        self.assertEqual(story.revisit_days(37), [36, 34, 30, 21, 2])

    def test_early_days_do_not_look_before_day_one(self) -> None:
        self.assertEqual(story.revisit_days(2), [1])
        self.assertEqual(story.revisit_days(1), [])

    def test_words_from_revisited_days_are_offered_again(self) -> None:
        conn = seeded()
        day_one = conn.execute(
            "SELECT id FROM story_day WHERE day_number = 1").fetchone()["id"]
        conn.execute("INSERT INTO story_vocab VALUES (?, 1, 'now')", (day_one,))
        conn.commit()
        recalled = story.recall_targets(conn, 2)
        self.assertIn("Wort1", [r["lemma"] for r in recalled])

    def test_recall_names_the_scene_it_came_from(self) -> None:
        """The generator is told where the learner met the word, so it can refer to it."""
        conn = seeded()
        day_one = conn.execute(
            "SELECT id FROM story_day WHERE day_number = 1").fetchone()["id"]
        conn.execute("INSERT INTO story_vocab VALUES (?, 1, 'now')", (day_one,))
        conn.commit()
        self.assertEqual(story.recall_targets(conn, 2)[0]["from_scene"], "Tag 1")


class TestPracticeIsAnchored(unittest.TestCase):
    def test_day_vocabulary_is_preferred_over_the_global_pool(self) -> None:
        conn = seeded()
        day_one = conn.execute(
            "SELECT id FROM story_day WHERE day_number = 1").fetchone()["id"]
        for item_id in (3, 4, 5):
            conn.execute("INSERT INTO story_vocab VALUES (?, ?, 'now')",
                         (day_one, item_id))
        conn.commit()
        chosen = [r["lemma"] for r in modules._due_words(conn, 3, day=1)]
        self.assertEqual(set(chosen), {"Wort3", "Wort4", "Wort5"})

    def test_a_thin_day_is_topped_up_rather_than_starved(self) -> None:
        conn = seeded()
        day_one = conn.execute(
            "SELECT id FROM story_day WHERE day_number = 1").fetchone()["id"]
        conn.execute("INSERT INTO story_vocab VALUES (?, 2, 'now')", (day_one,))
        conn.commit()
        self.assertEqual(len(modules._due_words(conn, 5, day=1)), 5)

    def test_no_day_still_works(self) -> None:
        self.assertTrue(modules._due_words(seeded(), 3, day=None))


class TestSemanticStore(unittest.TestCase):
    def test_vectors_are_unit_length(self) -> None:
        """Cosine similarity is a dot product only if the vectors are normalised."""
        import numpy as np
        vector = memory._normalise(np.array([3.0, 4.0], dtype=np.float32))
        self.assertAlmostEqual(float(np.linalg.norm(vector)), 1.0, places=5)

    def test_a_zero_vector_does_not_divide_by_zero(self) -> None:
        import numpy as np
        vector = memory._normalise(np.zeros(4, dtype=np.float32))
        self.assertEqual(float(np.linalg.norm(vector)), 0.0)

    def test_model_is_part_of_the_key(self) -> None:
        """Changing embedding model must be a re-index, not a silent mixed store."""
        conn = seeded()
        columns = {r[1] for r in conn.execute("PRAGMA table_info(embedding)")}
        self.assertIn("model", columns)
        indexes = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='embedding'").fetchone()[0]
        self.assertIn("UNIQUE (kind, ref_id, model)", indexes)


if __name__ == "__main__":
    unittest.main()
