"""The invariants Phase 0 exists to protect.

Test 1 is the important one. D2 says the learner model is a projection that can
be re-derived from the log; if that ever stops being true, the option to fix the
scheduler in November and replay history is gone permanently and silently.
"""

from __future__ import annotations

import builtins
import contextlib
import io
import random
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jana import core, events, scheduler, session
from jana.db import connect, init_db
from jana.project import rebuild


def fresh_db() -> sqlite3.Connection:
    tmp = Path(tempfile.mkdtemp()) / "t.db"
    conn = connect(tmp)
    init_db(conn)
    return conn


def seed_items(conn: sqlite3.Connection, n: int = 40) -> None:
    conn.executemany(
        """INSERT INTO item (lemma, pos, gender, sense_gloss_en, cefr_level,
                             item_type, source)
           VALUES (?, 'noun', ?, ?, 'A1', 'vocab', 'test')""",
        [(f"Wort{i}", ("der", "die", "das")[i % 3], f"word{i}") for i in range(n)],
    )
    conn.commit()


def synthetic_history(conn: sqlite3.Connection, n_items: int, days: int,
                      rng: random.Random) -> None:
    """Write a plausible log: introductions then attempts across several days."""
    t0 = datetime(2026, 8, 28, tzinfo=timezone.utc)
    for item_id in range(1, n_items + 1):
        events.append(conn, "item_introduced",
                      {"item_id": item_id, "modality": "text", "rung": 1},
                      ts=t0.isoformat(timespec="milliseconds"))
    for d in range(days):
        for item_id in range(1, n_items + 1):
            ok = rng.random() < 0.7
            ts = (t0 + timedelta(days=d, seconds=item_id)).isoformat(
                timespec="milliseconds")
            events.append(conn, "attempt", {
                "item_id": item_id, "modality": "text", "rung": 1,
                "exercise_id": None, "response": "x", "correct": ok,
                "grade": events.GOOD if ok else events.AGAIN,
                "latency_ms": rng.randint(400, 9000),
            }, ts=ts)


def dump_state(conn: sqlite3.Connection) -> list[tuple]:
    return conn.execute(
        "SELECT * FROM item_state ORDER BY item_id, modality").fetchall()


class TestEventLogIsAppendOnly(unittest.TestCase):
    def test_update_and_delete_raise(self) -> None:
        conn = fresh_db()
        try:
            eid = events.append(conn, "session_started", {"session_id": 1, "date": "x"})
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE event SET kind = 'z' WHERE id = ?", (eid,))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM event WHERE id = ?", (eid,))
        finally:
            conn.close()


class TestProjectionIsDerived(unittest.TestCase):
    """D2: item_state must be reproducible from the event log alone."""

    def test_rebuild_is_deterministic(self) -> None:
        conn = fresh_db()
        try:
            seed_items(conn, 20)
            synthetic_history(conn, 20, days=5, rng=random.Random(1))

            rebuild(conn)
            first = [tuple(r) for r in dump_state(conn)]
            self.assertTrue(first, "projection produced no rows")

            # Drop it entirely and rebuild from the log — must be byte-identical.
            conn.execute("DELETE FROM item_state")
            conn.commit()
            self.assertEqual(dump_state(conn), [])
            rebuild(conn)
            second = [tuple(r) for r in dump_state(conn)]
            self.assertEqual(first, second)
        finally:
            conn.close()

    def test_rebuild_uses_event_time_not_wall_clock(self) -> None:
        """Intervals must derive from the event ts, or replay is not stable."""
        conn = fresh_db()
        try:
            seed_items(conn, 5)
            synthetic_history(conn, 5, days=3, rng=random.Random(2))
            rebuild(conn)
            before = [tuple(r) for r in dump_state(conn)]
            time.sleep(0.05)
            rebuild(conn)
            self.assertEqual(before, [tuple(r) for r in dump_state(conn)])
        finally:
            conn.close()


class TestSessionLoop(unittest.TestCase):
    def _drive(self, conn: sqlite3.Connection, answers) -> None:
        it = iter(answers)
        real = builtins.input
        builtins.input = lambda *a, **k: next(it)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                session.run(conn, seed=7)
        except StopIteration:
            self.fail("session asked more questions than the script supplied")
        finally:
            builtins.input = real

    def test_terminates_when_every_answer_is_wrong(self) -> None:
        """A persistently-missed item must not re-queue forever."""
        conn = fresh_db()
        try:
            seed_items(conn, 40)
            n_new = scheduler.NEW_PER_SESSION
            # Generous budget; the cap should stop us well short of it.
            self._drive(conn, ["1"] * (n_new * core.MAX_ASKS_PER_ITEM + 5))
            asked = events.count(conn, "attempt")
            self.assertLessEqual(asked, n_new * core.MAX_ASKS_PER_ITEM)
            self.assertGreater(asked, 0)
        finally:
            conn.close()

    def test_every_answer_writes_exactly_one_attempt(self) -> None:
        conn = fresh_db()
        try:
            seed_items(conn, 40)
            self._drive(conn, ["1"] * 200)
            n_attempts = events.count(conn, "attempt")
            from jana.project import rebuild
            rebuild(conn)
            rows = conn.execute(
                "SELECT sum(reps) FROM item_state").fetchone()[0]
            self.assertEqual(n_attempts, rows)
        finally:
            conn.close()

    def test_new_items_are_introduced_once(self) -> None:
        conn = fresh_db()
        try:
            seed_items(conn, 40)
            self._drive(conn, ["1"] * 200)
            n = events.count(conn, "item_introduced")
            self.assertEqual(n, scheduler.NEW_PER_SESSION)
        finally:
            conn.close()


class TestLatencyBudget(unittest.TestCase):
    """Arch doc §7: p99 of the interactive loop under 200 ms."""

    def test_next_question_p99(self) -> None:
        """The real interactive path: derive the next question from the log."""
        conn = fresh_db()
        try:
            seed_items(conn, 300)
            engine = core.Engine.start(conn, seed=3)
            assert engine is not None
            samples = []
            for _ in range(200):
                t = time.perf_counter()
                q = engine.next_question()
                samples.append((time.perf_counter() - t) * 1000)
                if q is None:
                    break
                engine.submit(q.item_id, q.answer, 800)
            samples.sort()
            p99 = samples[min(int(len(samples) * 0.99), len(samples) - 1)]
            self.assertLess(p99, 200.0, f"p99 {p99:.1f} ms exceeds the §7 budget")
        finally:
            conn.close()


class TestCorpusProvenance(unittest.TestCase):
    """D4: nothing the learner studies may be model-authored in Phase 0."""

    def test_every_item_has_a_source(self) -> None:
        conn = connect()
        try:
            init_db(conn)
            bad = conn.execute(
                "SELECT count(*) FROM item WHERE source IS NULL OR source = ''"
            ).fetchone()[0]
            self.assertEqual(bad, 0)
            generated = conn.execute(
                "SELECT count(*) FROM item WHERE source LIKE '%ollama%'"
                " OR source LIKE '%deepseek%' OR source LIKE '%gemma%'"
            ).fetchone()[0]
            self.assertEqual(generated, 0, "model-authored German in Phase 0 (D4)")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
