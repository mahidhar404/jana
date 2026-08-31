"""Terminal adapter.

Everything this module knows how to do is print a question and read a line. All
teaching logic lives in jana/core.py, which is why the browser adapter in
jana/web.py could be written without touching any of it.
"""

from __future__ import annotations

import argparse
import sys
import time

from jana.core import Engine, Question
from jana.db import connect, init_db

_TTY = sys.stdout.isatty()

MAX_ASKS_PER_ITEM = __import__("jana.core", fromlist=["core"]).MAX_ASKS_PER_ITEM


def c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _TTY else text


def render(q: Question, progress) -> None:
    print()
    print(c(f"  #{progress.asked + 1}  ·  {progress.remaining} left  ·  "
            f"rung {q.rung} · {q.task_type}", "2"))
    print(f"  {c(q.prompt, '1;36')}")
    if q.hint:
        print(c(f"  hint: {q.hint}", "2"))
    print()
    if q.options:
        for i, opt in enumerate(q.options, 1):
            print(f"    {c(str(i), '1')}) {opt}")
        print()


def read_answer(q: Question) -> str:
    while True:
        raw = input("  > ").strip()
        if not q.options:
            return raw
        if raw.isdigit() and 1 <= int(raw) <= len(q.options):
            return q.options[int(raw) - 1]
        if raw in q.options:
            return raw
        print(c(f"  enter 1-{len(q.options)}", "33"))


def run(conn, seed: int | None = None) -> None:
    engine = Engine.open_session(conn) or Engine.start(conn, seed=seed)
    if engine is None:
        print(c("\n  nothing due.\n", "32"))
        return

    print(c(f"\n  session {engine.session_id} · {len(engine.questions)} items\n", "2"))
    try:
        while (q := engine.next_question()) is not None:
            render(q, engine.progress())
            started = time.perf_counter()
            answer = read_answer(q)
            latency = int((time.perf_counter() - started) * 1000)
            outcome = engine.submit(q.item_id, answer, latency)
            if outcome.correct:
                print(c(f"  richtig{'  · ' + outcome.note if outcome.note else ''}", "32"))
            else:
                print(c(f"  falsch — {q.prompt} = {outcome.expected}"
                        f"{'  (' + outcome.note + ')' if outcome.note else ''}", "31"))
    except (EOFError, KeyboardInterrupt):
        print(c("\n\n  interrupted — every answer is already logged\n", "33"))

    p = engine.progress()
    engine.finish()
    pct = (100 * p.correct / p.asked) if p.asked else 0
    print(c(f"\n  {p.correct}/{p.asked} correct ({pct:.0f}%)\n", "1"))


def main() -> None:
    ap = argparse.ArgumentParser(description="Jana — daily German session")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    conn = connect()
    init_db(conn)
    run(conn, seed=args.seed)


if __name__ == "__main__":
    main()
