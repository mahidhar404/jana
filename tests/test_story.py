"""The daily episode, and the one decision that makes it teach.

The learner may answer in English or in German, and the system reads which he
chose. Getting that detection wrong is not cosmetic: German mistaken for English
gets "translated" into nonsense, and English mistaken for German gets
"corrected" into nonsense. Both destroy the turn.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from jana import story
from jana.db import connect, init_db


class TestLanguageDetection(unittest.TestCase):
    def test_plain_english_is_english(self) -> None:
        for text in ("I would like a coffee please",
                     "Where is the train station?",
                     "My name is Mahidhar and I am from India",
                     "Can you help me with the apartment"):
            self.assertEqual(story.detect_language(text), "en", text)

    def test_attempted_german_is_german(self) -> None:
        for text in ("Ich möchte einen Kaffee bitte",
                     "Wo ist der Bahnhof?",
                     "Ich komme aus Indien und ich bin müde",
                     "Können Sie mir bitte helfen"):
            self.assertEqual(story.detect_language(text), "de", text)

    def test_broken_beginner_german_still_counts_as_german(self) -> None:
        """His German will be wrong. That is the point of correcting it."""
        self.assertEqual(
            story.detect_language("Ich habe gestern mit meine Bruder gesprochen"),
            "de")

    def test_ties_fall_back_to_english(self) -> None:
        """Translating by mistake shows a correct sentence; correcting does not."""
        self.assertEqual(story.detect_language("ok"), "en")
        self.assertEqual(story.detect_language("hmm"), "en")

    def test_empty_input_does_not_crash(self) -> None:
        self.assertEqual(story.detect_language(""), "en")
        self.assertEqual(story.detect_language("123 !!!"), "en")


class TestArc(unittest.TestCase):
    def test_arc_is_long_enough_to_reach_the_exam(self) -> None:
        """One episode a day must cover the runway without repeating early."""
        self.assertGreaterEqual(len(story.ARC), 60)

    def test_every_episode_is_fully_specified(self) -> None:
        for title, setting, npc, theme in story.ARC:
            self.assertTrue(title and setting and npc and theme)

    def test_arc_covers_the_core_goethe_topics(self) -> None:
        themes = {theme for _, _, _, theme in story.ARC}
        for required in ("Wohnen", "Arbeit", "Gesundheit", "Reisen",
                         "Einkaufen", "Behörden", "Bildung"):
            self.assertIn(required, themes)

    def test_days_wrap_rather_than_running_out(self) -> None:
        first = story._scene(1)
        self.assertEqual(story._scene(len(story.ARC) + 1), first)


class TestDayPersistence(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = connect(Path(tempfile.mkdtemp()) / "story.db")
        init_db(self.conn)

    def test_turns_are_stored_in_order(self) -> None:
        self.conn.execute(
            """INSERT INTO story_day (day_number, date, title, setting_de,
                   setting_en, npc_name, npc_role, theme, target_ids, created_at)
               VALUES (1, '2026-08-28', 'Ankunft', 'Flughafen', 'Airport',
                       'Frau Weber', 'Beamtin', 'Reisen', '[]', 'now')""")
        self.conn.commit()
        for ordinal, (speaker, text) in enumerate(
                [("npc", "Guten Morgen."), ("learner", "Hallo."),
                 ("npc", "Ihren Pass, bitte.")], start=1):
            story._save_turn(self.conn, 1, ordinal,
                             story.Turn(speaker, text, validated=True))
        row = self.conn.execute("SELECT * FROM story_day WHERE id = 1").fetchone()
        day = story._load(self.conn, row)
        self.assertEqual([t.de for t in day.turns],
                         ["Guten Morgen.", "Hallo.", "Ihren Pass, bitte."])
        self.assertEqual(day.learner_turns, 1)
        self.assertFalse(day.complete)


if __name__ == "__main__":
    unittest.main()


class TestExchangeIsTwoPhases(unittest.TestCase):
    """The learner's line and the reply are separate calls, on purpose.

    Doing both model calls before responding meant four to five seconds where
    he had spoken and the scene showed nothing, then both lines appeared at
    once. Conversation does not work that way and neither does an animation
    that has to know who is talking. If these ever merge back into one request,
    the flow breaks again — hence the test.
    """

    def test_speak_and_reply_exist_separately(self) -> None:
        self.assertTrue(callable(story.speak))
        self.assertTrue(callable(story.reply))

    def test_speak_returns_only_the_learner(self) -> None:
        import inspect
        source = inspect.getsource(story.speak)
        self.assertNotIn("_npc_turn", source,
                         "speak() must not generate the reply as well")

    def test_reply_returns_only_the_other_character(self) -> None:
        import inspect
        source = inspect.getsource(story.reply)
        self.assertNotIn("_render_learner", source)
        self.assertNotIn("_check_learner", source)

    def test_combined_say_still_works_for_scripts(self) -> None:
        import inspect
        source = inspect.getsource(story.say)
        self.assertIn("speak(", source)
        self.assertIn("reply(", source)
