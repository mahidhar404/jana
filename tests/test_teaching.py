"""What Jana is willing to teach, and what it refuses to."""

from __future__ import annotations

import unittest

from jana import fsrs
from jana.core import choose_task, has_gloss
from jana.grader import grade


class TestTaskSelection(unittest.TestCase):
    def test_wortliste_noun_becomes_an_article_drill(self) -> None:
        self.assertEqual(choose_task("[noun]", "die", 1), "article")

    def test_item_with_neither_gloss_nor_gender_is_refused(self) -> None:
        """Better to teach nothing than to invent an English gloss (D4)."""
        self.assertIsNone(choose_task("[verb]", None, 1))

    def test_glossed_item_climbs_the_rung_ladder(self) -> None:
        self.assertEqual(choose_task("job", "der", 1), "recognise")
        self.assertEqual(choose_task("job", "der", 2), "cued_recall")
        self.assertEqual(choose_task("job", "der", 3), "production")

    def test_placeholder_gloss_is_not_mistaken_for_english(self) -> None:
        self.assertFalse(has_gloss("[noun]"))
        self.assertTrue(has_gloss("job"))


class TestGrading(unittest.TestCase):
    def test_umlaut_typing_is_forgiven_but_corrected(self) -> None:
        v = grade("cued_recall", "Woerterbuch", "Wörterbuch")
        self.assertTrue(v.correct)
        self.assertIn("Wörterbuch", v.note)

    def test_near_miss_is_still_wrong(self) -> None:
        self.assertFalse(grade("cued_recall", "Wörterbush", "Wörterbuch").correct)

    def test_any_listed_synonym_is_accepted(self) -> None:
        self.assertTrue(grade("production", "pub", "restaurant/pub").correct)

    def test_wrong_article_fails(self) -> None:
        self.assertFalse(grade("article", "der", "die").correct)


class TestForgettingCurve(unittest.TestCase):
    def test_success_lengthens_and_lapse_shortens_the_interval(self) -> None:
        m = None
        for _ in range(3):
            m = fsrs.review(m, fsrs.GOOD, fsrs.next_interval(m) if m else 0)
        grown = fsrs.next_interval(m)
        lapsed = fsrs.next_interval(fsrs.review(m, fsrs.AGAIN, grown))
        self.assertGreater(grown, 10)
        self.assertLess(lapsed, grown)

    def test_a_lapse_never_strengthens_a_memory(self) -> None:
        m = fsrs.Memory(stability=40.0, difficulty=5.0)
        self.assertLessEqual(fsrs.review(m, fsrs.AGAIN, 40.0).stability, m.stability)

    def test_retrievability_is_the_target_at_one_stability(self) -> None:
        self.assertAlmostEqual(fsrs.retrievability(10.0, 10.0), 0.9, places=6)


if __name__ == "__main__":
    unittest.main()
