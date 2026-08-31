"""Model bake-off (arch doc §9.1), run against this learner's own syllabus.

Reputation and public benchmarks are the wrong instrument here. A model that
tops a multilingual leaderboard may still reach for C1 vocabulary when asked for
A2, and that failure is invisible to every benchmark and fatal to this project.

So the measure is the one that matters for Jana: given a scene and a target
word list, how often does the model produce German that stays inside the Goethe
syllabus, first try? That number comes from jana/lexicon.py, which already runs
on every generation in production — so this script is not a separate eval, it is
the production check pointed at a fixed prompt set.

Usage:
    uv run python -m jana.eval.bakeoff --models gemma4:12b-mlx,mistral-nemo
    uv run python -m jana.eval.bakeoff --models deepseek --remote
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import time
from pathlib import Path

from jana import lexicon, llm
from jana.db import connect, init_db

EVAL_SET = Path(__file__).resolve().parent.parent.parent / "evals" / "bakeoff_v1.jsonl"

# Fixed prompts. Frozen so a score today is comparable with a score in December.
PROMPTS = [
    "Write one short German sentence about arriving at an airport.",
    "Write one short German sentence about renting a flat.",
    "Write one short German sentence about a doctor's appointment.",
    "Write one short German sentence about buying groceries.",
    "Write one short German sentence about taking the train to work.",
    "Write one short German sentence about the weather in winter.",
    "Write one short German sentence about calling your family.",
    "Write one short German sentence about a problem with the heating.",
    "Write one short German sentence about learning a language.",
    "Write one short German sentence about a job interview.",
]

SYSTEM = ("You write German for a beginner preparing for the Goethe B1 exam.\n"
          "Use ONLY simple A1-A2 vocabulary. At most 12 words.\n"
          'Reply with JSON only: {"de": "<the sentence>"}')


def _german(text: str) -> str:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return text
    try:
        return str(json.loads(match.group(0)).get("de", ""))
    except json.JSONDecodeError:
        return text


def score(model: str, remote: bool, lex: frozenset[str],
          repeats: int) -> dict:
    passed = total = 0
    latencies: list[int] = []
    failures: list[dict] = []

    for _ in range(repeats):
        for prompt in PROMPTS:
            messages = [{"role": "system", "content": SYSTEM},
                        {"role": "user", "content": prompt}]
            reply = (llm.remote(messages, temperature=0.7, max_tokens=120,
                                model=model) if remote
                     else llm.local(messages, temperature=0.7, max_tokens=120,
                                    model=model))
            total += 1
            if not reply.ok:
                failures.append({"prompt": prompt, "error": reply.error})
                continue
            latencies.append(reply.latency_ms)
            german = _german(reply.text)
            report = lexicon.check(german, lex)
            if report.ok:
                passed += 1
            else:
                failures.append({"prompt": prompt, "de": german,
                                 "outside_syllabus": report.unknown[:6]})

    return {
        "model": model,
        "tier": "remote" if remote else "local",
        "n": total,
        "in_syllabus": passed,
        "pass_rate": round(passed / total, 3) if total else 0.0,
        "p50_ms": int(statistics.median(latencies)) if latencies else 0,
        "failures": failures[:8],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--models", required=True,
                    help="comma-separated model names as Ollama/DeepSeek knows them")
    ap.add_argument("--remote", action="store_true", help="score via DeepSeek")
    ap.add_argument("--repeats", type=int, default=1)
    args = ap.parse_args()

    conn = connect()
    init_db(conn)
    lex = lexicon.build(conn)
    print(f"syllabus: {len(lex)} permitted forms · "
          f"{len(PROMPTS) * args.repeats} generations per model\n")

    results = []
    for model in [m.strip() for m in args.models.split(",") if m.strip()]:
        started = time.perf_counter()
        result = score(model, args.remote, lex, args.repeats)
        result["wall_s"] = round(time.perf_counter() - started, 1)
        results.append(result)
        print(f"  {result['model']:26s} {result['in_syllabus']:3d}/{result['n']:<3d} "
              f"in syllabus  ({result['pass_rate']:.0%})  p50 {result['p50_ms']:>5d} ms")

    EVAL_SET.parent.mkdir(parents=True, exist_ok=True)
    with EVAL_SET.open("a") as handle:
        for result in results:
            handle.write(json.dumps({**result, "at": time.time()}) + "\n")

    best = max(results, key=lambda r: (r["pass_rate"], -r["p50_ms"]))
    print(f"\n  winner: {best['model']} "
          f"({best['pass_rate']:.0%} in syllabus, p50 {best['p50_ms']} ms)")
    print(f"  appended to {EVAL_SET.relative_to(Path.cwd())}")

    for result in results:
        if result["failures"]:
            print(f"\n  {result['model']} — sample rejections:")
            for failure in result["failures"][:3]:
                if "outside_syllabus" in failure:
                    print(f"    {failure['de'][:70]}")
                    print(f"      outside syllabus: {failure['outside_syllabus']}")


if __name__ == "__main__":
    main()
