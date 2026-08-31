"""Real recorded German: the filters that decide what a learner is asked to hear."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from jana import audio
from jana.db import connect, init_db


def segment(text: str, start_ms: int, end_ms: int) -> dict:
    return {"text": text, "offsets": {"from": start_ms, "to": end_ms}}


class TestLanguageFilter(unittest.TestCase):
    """The German-dense courses are dense, not pure.

    An English sentence that quotes German passed a "contains one German word"
    test, and putting one in a dictation exercise asks the learner to transcribe
    the wrong language. 83 such clips were already stored before this tightened.
    """

    def test_real_german_passes(self) -> None:
        for text in ("Wir wollten doch im Sommer eine Woche ans Meer fahren.",
                     "Der Zug nach Berlin fährt um acht Uhr ab.",
                     "Ich habe gestern mit meiner Freundin gesprochen."):
            self.assertTrue(audio.looks_german(text), text)

    def test_english_quoting_german_is_rejected(self) -> None:
        self.assertFalse(audio.looks_german(
            'something "ohne Angabe von Gründen". So that means "without giving"'))

    def test_plain_english_is_rejected(self) -> None:
        self.assertFalse(audio.looks_german(
            "In this lesson we will look at the genitive case and how it works."))

    def test_text_with_no_german_at_all_is_rejected(self) -> None:
        self.assertFalse(audio.looks_german("Okay. Right. Next."))


class TestClipSelection(unittest.TestCase):
    """A usable clip is a whole utterance — long enough to carry prosody, short
    enough to hold in memory and write down."""

    def test_thirty_second_chunks_are_rejected(self) -> None:
        """Whisper with -nt emits 30s blocks. That flag is gone; the guard stays."""
        clips = audio.segments_to_clips(
            [segment("Der Zug nach Berlin fährt um acht Uhr ab.", 0, 30000)], "x.mp4")
        self.assertEqual(clips, [])

    def test_fragments_are_rejected(self) -> None:
        self.assertEqual(
            audio.segments_to_clips([segment("Ja.", 0, 900)], "x.mp4"), [])

    def test_an_utterance_is_kept_with_its_timing(self) -> None:
        clips = audio.segments_to_clips(
            [segment(" Wir fahren morgen nach Berlin. ", 4200, 9800)], "exam.mp4")
        self.assertEqual(len(clips), 1)
        self.assertEqual(clips[0].start_s, 4.2)
        self.assertEqual(clips[0].end_s, 9.8)
        self.assertEqual(clips[0].text, "Wir fahren morgen nach Berlin.")
        self.assertEqual(clips[0].source, "exam.mp4")

    def test_zero_length_segments_do_not_crash(self) -> None:
        self.assertEqual(
            audio.segments_to_clips([segment("Hallo hier ist Frank", 500, 500)],
                                    "x.mp4"), [])


class TestFallbackDiscipline(unittest.TestCase):
    """A loose match returns a human saying something else, which is worse than
    synthesis: the learner is asked to transcribe one sentence and hears another."""

    def setUp(self) -> None:
        self.conn = connect(Path(tempfile.mkdtemp()) / "a.db")
        init_db(self.conn)

    def test_no_clips_means_no_match_rather_than_a_wrong_one(self) -> None:
        self.assertIsNone(audio.find_clip(self.conn, "Der Zug fährt nach Berlin."))

    def test_threshold_sits_above_the_paraphrase_band(self) -> None:
        """Measured on this corpus: exact matches score 1.000, related-but-
        different sentences top out near 0.87. The bar belongs in that gap.

        At the original 0.72, asking for "Wo ist die Touristeninformation?"
        returned a clip about touristic attractions at 0.834 — a human saying
        something else, which is worse than a robot saying the right thing."""
        import inspect
        default = inspect.signature(audio.find_clip).parameters["threshold"].default
        self.assertGreaterEqual(default, 0.90)
        self.assertEqual(default, audio.CLIP_MATCH_THRESHOLD)

    def test_stats_report_toolchain_state(self) -> None:
        state = audio.stats(self.conn)
        self.assertIn("ready", state)
        self.assertEqual(set(state["ready"]), {"ffmpeg", "whisper", "model"})


if __name__ == "__main__":
    unittest.main()
