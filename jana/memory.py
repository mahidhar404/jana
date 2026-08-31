"""Semantic memory: everything the learner has met, searchable by meaning.

Why this is vector search and not a vector database
---------------------------------------------------
The ask was a vector DB from day one so nothing is lost and the system can grow
beyond one user. The need behind it is real — "find the conversation where I
talked about renting a flat" cannot be answered by keyword search, because the
learner will not remember which words he used.

But Pinecone, Qdrant or Chroma would add a network service, a second store to
keep consistent with SQLite, and an operational surface, in exchange for
approximate-nearest-neighbour indexing that matters at ten million vectors and
is pure overhead at ten thousand. Four months of daily conversation is roughly
40k rows. Brute-force cosine over 40k × 768 float32 is about 120 MB of maths —
single-digit milliseconds, and exact rather than approximate.

So vectors live in SQLite next to the data they describe. One store, one backup,
one transaction boundary, and no service to run.

**When to change this:** past roughly 100k vectors, or the first time a second
process needs concurrent writes, move to Postgres + pgvector. The table shape
here is deliberately the same one pgvector wants, so that migration is a copy
and a change of driver — not a redesign. Do not pre-emptively move; measure the
search latency first, the same discipline §6 applies to retrieval generally.
"""

from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np

from jana import llm

EMBED_MODEL = "nomic-embed-text"
EMBED_DIM = 768
BATCH = 32

# Kinds of thing worth remembering. Each row points back at its source table, so
# a hit can always be traced to the conversation or item it came from.
KINDS = ("item", "story_turn", "sentence")


@dataclass(frozen=True)
class Hit:
    kind: str
    ref_id: int
    text: str
    score: float
    meta: dict[str, Any]


def embed(texts: list[str], model: str = EMBED_MODEL) -> list[np.ndarray] | None:
    """Embed a batch locally. Returns None if the embedding model is unavailable."""
    if not texts:
        return []
    try:
        request = urllib.request.Request(
            f"{llm.OLLAMA_URL}/api/embed",
            data=json.dumps({"model": model, "input": texts}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=120) as response:
            body = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None
    vectors = body.get("embeddings")
    if not vectors:
        return None
    return [_normalise(np.asarray(v, dtype=np.float32)) for v in vectors]


def _normalise(vector: np.ndarray) -> np.ndarray:
    """Unit length, so cosine similarity is a plain dot product."""
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm else vector


def available() -> bool:
    return embed(["ping"]) is not None


# ------------------------------------------------------------------ indexing
def _pending(conn: sqlite3.Connection, kind: str) -> list[tuple[int, str]]:
    """Rows of this kind that have no embedding yet."""
    sources = {
        "item": """SELECT i.id,
                          i.lemma || ' — ' || i.sense_gloss_en AS text
                   FROM item i
                   WHERE i.sense_gloss_en NOT LIKE '[%'""",
        "story_turn": """SELECT t.id, t.de || ' — ' || coalesce(t.en, '') AS text
                         FROM story_turn t""",
        "sentence": "SELECT s.id, s.de AS text FROM sentence s",
    }
    rows = conn.execute(f"""
        SELECT src.id, src.text FROM ({sources[kind]}) AS src
        WHERE NOT EXISTS (SELECT 1 FROM embedding e
                          WHERE e.kind = ? AND e.ref_id = src.id
                            AND e.model = ?)""", (kind, EMBED_MODEL)).fetchall()
    return [(r[0], r[1]) for r in rows if r[1] and r[1].strip()]


def index(conn: sqlite3.Connection, kinds: Iterable[str] = KINDS,
          progress: bool = False) -> dict[str, int]:
    """Embed everything not yet embedded. Safe to run repeatedly."""
    written: dict[str, int] = {}
    for kind in kinds:
        todo = _pending(conn, kind)
        count = 0
        for start in range(0, len(todo), BATCH):
            chunk = todo[start:start + BATCH]
            vectors = embed([text for _, text in chunk])
            if vectors is None:
                break
            conn.executemany(
                """INSERT OR REPLACE INTO embedding
                   (kind, ref_id, text, model, dim, vector, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, datetime('now'))""",
                [(kind, ref_id, text, EMBED_MODEL, len(vector),
                  vector.astype(np.float32).tobytes())
                 for (ref_id, text), vector in zip(chunk, vectors)])
            conn.commit()
            count += len(chunk)
            if progress:
                print(f"  {kind}: {count}/{len(todo)}", flush=True)
        written[kind] = count
    return written


# ------------------------------------------------------------------ retrieval
def _matrix(conn: sqlite3.Connection, kinds: tuple[str, ...]
            ) -> tuple[np.ndarray, list[sqlite3.Row]]:
    placeholders = ",".join("?" * len(kinds))
    rows = conn.execute(
        f"""SELECT kind, ref_id, text, vector FROM embedding
            WHERE kind IN ({placeholders}) AND model = ?""",
        (*kinds, EMBED_MODEL)).fetchall()
    if not rows:
        return np.empty((0, EMBED_DIM), dtype=np.float32), []
    matrix = np.vstack([np.frombuffer(r["vector"], dtype=np.float32) for r in rows])
    return matrix, rows


def search(conn: sqlite3.Connection, query: str, *, kinds: tuple[str, ...] = KINDS,
           limit: int = 8) -> list[Hit]:
    """Nearest neighbours by meaning. Exact, not approximate — the corpus is small."""
    vectors = embed([query])
    if not vectors:
        return []
    matrix, rows = _matrix(conn, kinds)
    if not len(rows):
        return []
    scores = matrix @ vectors[0]
    order = np.argsort(-scores)[:limit]
    return [Hit(kind=rows[i]["kind"], ref_id=int(rows[i]["ref_id"]),
                text=rows[i]["text"], score=round(float(scores[i]), 4), meta={})
            for i in order]


def related_vocabulary(conn: sqlite3.Connection, text: str,
                       limit: int = 10) -> list[dict[str, Any]]:
    """Items whose meaning is close to a passage. Used to widen a day's practice."""
    hits = search(conn, text, kinds=("item",), limit=limit)
    if not hits:
        return []
    ids = [h.ref_id for h in hits]
    placeholders = ",".join("?" * len(ids))
    rows = {r["id"]: dict(r) for r in conn.execute(
        f"""SELECT id, lemma, gender, sense_gloss_en, pos, cefr_level
            FROM item WHERE id IN ({placeholders})""", ids)}
    return [{**rows[h.ref_id], "score": h.score} for h in hits if h.ref_id in rows]


def stats(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT kind, count(*) n FROM embedding WHERE model = ? GROUP BY kind",
        (EMBED_MODEL,)).fetchall()
    return {
        "model": EMBED_MODEL,
        "available": available(),
        "by_kind": {r["kind"]: r["n"] for r in rows},
        "total": sum(r["n"] for r in rows),
    }


def main() -> None:
    import argparse
    from jana.db import connect, init_db

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["index", "search", "stats"])
    parser.add_argument("--query", default="")
    args = parser.parse_args()

    conn = connect()
    init_db(conn)
    if args.command == "index":
        if not available():
            raise SystemExit(f"embedding model {EMBED_MODEL} unavailable — "
                             f"run: ollama pull {EMBED_MODEL}")
        print(index(conn, progress=True))
    elif args.command == "stats":
        print(json.dumps(stats(conn), indent=2))
    else:
        for hit in search(conn, args.query):
            print(f"  {hit.score:.3f}  [{hit.kind}] {hit.text[:88]}")


if __name__ == "__main__":
    main()
