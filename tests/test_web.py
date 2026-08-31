"""The HTTP adapter, and the one property it must never lose."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path


class TestApi(unittest.TestCase):
    """Each test gets its own database — the API is stateful by design."""

    def setUp(self) -> None:
        from fastapi.testclient import TestClient

        self._previous = os.environ.get("JANA_DB")
        os.environ["JANA_DB"] = str(Path(tempfile.mkdtemp()) / "web.db")

        from jana.db import connect, init_db
        from jana.ingest.html_gloss import find_files, load, parse
        conn = connect()
        init_db(conn)
        rows = []
        for path in find_files():
            rows.extend(parse(path))
        load(conn, rows)
        conn.close()

        from jana.web import app
        self.client = TestClient(app)

    def tearDown(self) -> None:
        if self._previous is None:
            os.environ.pop("JANA_DB", None)
        else:
            os.environ["JANA_DB"] = self._previous

    def test_typed_question_never_carries_its_answer(self) -> None:
        """A browser is untrusted. Grading stays server-side."""
        body = self.client.post("/api/session", json={}).json()
        seen = 0
        for _ in range(40):
            q = body.get("question")
            if q is None:
                break
            self.assertNotIn("answer", q, f"answer leaked in {q['task_type']}")
            seen += 1
            # Answer wrong on purpose so the session does not retire early.
            response = "definitely-not-the-answer"
            body = self.client.post("/api/answer", json={
                "session_id": body["session_id"], "item_id": q["item_id"],
                "response": response, "latency_ms": 900}).json()
        self.assertGreater(seen, 0)

    def test_state_survives_a_client_restart(self) -> None:
        """The web tier holds no session state; a fresh client resumes."""
        started = self.client.post("/api/session", json={}).json()
        first = started["question"]["item_id"]
        self.client.post("/api/answer", json={
            "session_id": started["session_id"], "item_id": first,
            "response": "x", "latency_ms": 100})

        from fastapi.testclient import TestClient
        from jana.web import app
        resumed = TestClient(app).get("/api/state").json()
        self.assertEqual(resumed["session_id"], started["session_id"])
        self.assertEqual(resumed["progress"]["asked"], 1)

    def test_override_is_logged(self) -> None:
        body = self.client.post("/api/session", json={}).json()
        q = body["question"]
        answered = self.client.post("/api/answer", json={
            "session_id": body["session_id"], "item_id": q["item_id"],
            "response": "wrong", "latency_ms": 500}).json()
        r = self.client.post("/api/override", json={
            "session_id": body["session_id"], "item_id": q["item_id"],
            "attempt_event_id": answered["outcome"]["attempt_event_id"],
            "learner_correct": True})
        self.assertEqual(r.status_code, 200)
        self.assertGreater(r.json()["logged"], 0)

    def test_schreiben_teile(self) -> None:
        for teil in (1, 2, 3):
            res = self.client.get(f"/api/schreiben?teil={teil}").json()
            self.assertEqual(res["teil"], teil)
            self.assertTrue(res["task"])
            self.assertTrue(res["en"])

    def test_sprechen_teile(self) -> None:
        for teil in (1, 2, 3):
            res = self.client.get(f"/api/sprechen?teil={teil}").json()
            self.assertEqual(res["teil"], teil)
            self.assertTrue("data" in res)

    def test_lesen_and_hoeren_teile(self) -> None:
        from unittest.mock import patch
        from jana.llm import Reply
        mock_reply = Reply(text="", model="none", tier="none", latency_ms=0, ok=False, error="offline")
        with patch("jana.llm.authored", return_value=mock_reply):
            l_res = self.client.get("/api/lesen?teil=1").json()
            self.assertTrue(l_res.get("text"))
            h_res = self.client.get("/api/hoeren?teil=3").json()
            self.assertTrue(h_res.get("text"))


if __name__ == "__main__":
    unittest.main()
