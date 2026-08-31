"""The exam formats, and the validator bug that hid inside them."""

from __future__ import annotations

import unittest

from jana import modules, teile


class TestCoverage(unittest.TestCase):
    """All fifteen Teile must be reachable, or the exam has blind spots."""

    OFFICIAL = {"lesen": 5, "hoeren": 4, "schreiben": 3, "sprechen": 3}

    def test_every_teil_has_a_builder_or_a_fallback(self) -> None:
        for modul, count in self.OFFICIAL.items():
            for teil in range(1, count + 1):
                built = (modul, teil) in teile.BUILDERS
                legacy = hasattr(modules, f"{modul}_teil") or hasattr(
                    modules, f"{modul}_prompt") or hasattr(modules, f"{modul}_task")
                self.assertTrue(built or legacy, f"{modul} Teil {teil} unreachable")

    def test_the_seven_that_were_missing_are_present(self) -> None:
        for key in (("lesen", 3), ("lesen", 4), ("lesen", 5), ("hoeren", 2),
                    ("hoeren", 4), ("schreiben", 3), ("sprechen", 3)):
            self.assertIn(key, teile.BUILDERS)


class TestStructuralFieldsAreNotGerman(unittest.TestCase):
    """Option labels are not vocabulary, and validating them rejects good work.

    Lesen Teil 3 labels its ads a–f. Those single letters were being checked
    against the Wortliste, found absent, and the whole exercise regenerated —
    every time, until it gave up and served a template. The exercise was fine;
    the validator was reading its scaffolding as prose.
    """

    def test_answer_keys_are_skipped(self) -> None:
        payload = {"ads": [{"key": "a", "text": "Zimmer frei."}],
                   "situations": [{"text": "Anna sucht ein Zimmer.", "answer": "a"}]}
        german = modules._german_strings(payload)
        # Check the extracted list, not a joined string: "a" occurs inside
        # "Anna" and a substring assertion would pass for the wrong reason.
        self.assertNotIn("a", german)
        self.assertIn("Zimmer frei.", german)

    def test_stance_and_speaker_labels_are_skipped(self) -> None:
        payload = {"opinions": [{"name": "Lena", "text": "Gute Idee.",
                                 "stance": "dafür"}],
                   "turns": [{"speaker": "a", "text": "Ich fahre gern."}]}
        german = modules._german_strings(payload)
        self.assertNotIn("dafür", german)
        self.assertNotIn("a", german)

    def test_short_tokens_are_not_treated_as_words(self) -> None:
        self.assertEqual(modules._german_strings({"x": "ab"}), [])

    def test_real_german_still_reaches_the_validator(self) -> None:
        payload = {"text": "Der Zug fährt nach Berlin.", "en": "The train goes to Berlin."}
        german = modules._german_strings(payload)
        self.assertEqual(german, ["Der Zug fährt nach Berlin."])


class TestFormatShapes(unittest.TestCase):
    def test_matching_teil_offers_a_none_of_these_option(self) -> None:
        """Ruling out is the skill Teil 3 tests; without x it is pure matching."""
        import inspect
        source = inspect.getsource(teile.lesen_teil3)
        self.assertIn('"x"', source)

    def test_single_listen_is_stated_in_the_instruction(self) -> None:
        import inspect
        source = inspect.getsource(teile.hoeren_teil2)
        self.assertIn("EINMAL", source)

    def test_formal_register_is_required_in_schreiben_teil3(self) -> None:
        import inspect
        source = inspect.getsource(teile.schreiben_teil3)
        self.assertIn("'Sie'", source)


if __name__ == "__main__":
    unittest.main()
