"""Word-by-word breakdown and grammar explanation.

Two different jobs, deliberately routed differently.

**Literal breakdown** is mostly a dictionary problem, not a language-model
problem. `jana/wordlookup.py` already resolves any inflected form back to its
entry, so the alignment is computed locally, in order, with no model call and no
latency. That matters because the learner will press it on almost every line.

**Grammar explanation** is the opposite: "why dative here?" needs reasoning
about a specific sentence, and there is no lookup table for it. That goes to a
model — but it answers in *English*, which is the safe direction (D4), because
the learner can audit an English explanation and cannot audit German.

The split is the same one that runs through the whole system: anything a table
can answer should never cost a model call.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from jana import llm, wordlookup

GRAMMAR_PROMPT = """You are a German teacher explaining ONE sentence to a beginner
preparing for Goethe B1. He is an Indian software engineer. He learns from
precise, concrete rules, not vague encouragement.

Explain in ENGLISH:
- why each case (Nominativ/Akkusativ/Dativ/Genitiv) is what it is
- why the verb sits where it sits
- any ending that is not obvious

Be specific and short. Name the rule. Do not pad.

Reply with JSON only:
{"summary": "<one sentence: what this sentence is doing grammatically>",
 "points": [{"part": "<the German words involved>",
             "rule": "<the rule, named>",
             "why": "<why it applies here, one sentence>"}],
 "word_order": "<one sentence on why the words are in this order>"}"""

CORRECTION_PROMPT = """You are a German teacher correcting ONE sentence a beginner
wrote or said. He is preparing for Goethe B1.

If the German is already correct, say so and return an empty errors list.
Otherwise find the real errors — cases, gender, verb position, endings, wrong
word. Ignore missing capitalisation and punctuation unless it changes meaning.

For each error name the rule so he learns the pattern, not just the fix.

Reply with JSON only:
{"correct": true,
 "corrected": "<his sentence, fixed; unchanged if already correct>",
 "errors": [{"wrong": "<exact text he wrote>", "right": "<the fix>",
             "rule": "<the grammar rule, named>",
             "why": "<one short sentence in English>"}],
 "praise": "<one specific thing he got right, in English>"}"""


def literal(conn: sqlite3.Connection, german: str) -> list[dict[str, Any]]:
    """Word-for-word alignment. Pure lookup — no model, no network."""
    return wordlookup.annotate(conn, german)


def _json_reply(system: str, user: str, max_tokens: int = 700) -> dict | None:
    response = llm.authored(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.2, max_tokens=max_tokens)
    if not response.ok:
        return None
    match = re.search(r"\{.*\}", response.text, re.S)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    parsed["tier"] = response.tier
    return parsed


def grammar(german: str) -> dict[str, Any]:
    result = _json_reply(GRAMMAR_PROMPT, german)
    return result or {"error": "explanation unavailable",
                      "summary": "", "points": [], "word_order": ""}


def correct(german: str) -> dict[str, Any]:
    """Check what the learner produced. Runs on every turn he writes German."""
    if not german.strip():
        return {"correct": True, "corrected": "", "errors": [], "praise": ""}
    result = _json_reply(CORRECTION_PROMPT, german)
    if result is None:
        return {"correct": True, "corrected": german, "errors": [],
                "praise": "", "error": "correction unavailable"}
    result.setdefault("errors", [])
    result.setdefault("corrected", german)
    result["correct"] = not result["errors"]
    return result
