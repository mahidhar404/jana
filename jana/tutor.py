"""The conversational tutor.

Why conversation rather than a quiz
-----------------------------------
A quiz delivers retrieval practice and spacing, which are the two best-evidenced
effects in the learning literature. It delivers almost nothing else. Four further
effects need a conversation, and together they are worth more than the quiz:

  * **Pushed output** (Swain). Producing a sentence forces a decision about case,
    gender and word order that recognising one lets you skip. It reveals gaps
    that multiple choice hides — a learner can score 90% on recognition and be
    unable to order a coffee.
  * **Comprehensible input at i+1** (Krashen). Language slightly above current
    level, understood from context, is what actually builds a mental grammar.
    A word list has no context to build from.
  * **Contextual variation.** A word met in six different sentences is retrieved
    from six different cues. A word met six times on the same flashcard is
    retrieved from one, and that cue is not available in the exam hall.
  * **Immediate corrective recast.** Being shown the correct form *at the moment
    of the error*, in the sentence you were trying to say, is worth far more
    than the same correction a day later.

So the quiz is not replaced — it is where an item starts. Conversation is where
it graduates.

The design decision that makes this more than a chatbot
-------------------------------------------------------
**The conversation is scheduled.** The tutor is handed today's due items by the
same FSRS scheduler that drives the quiz, and instructed to work them into what
it says. Every learner reply is then scanned — deterministically, no model
involved — for which target items were actually produced, and each one is logged
as a rung-3 attempt against the event log.

That closes the loop. Talking to Jana *is* studying: the spaced-repetition state
advances from conversation exactly as it would from drilling, except the retrieval
happened in context and under production pressure, which is the harder and more
transferable version of the same recall.

Safety
------
Everything the tutor says in German passes jana/lexicon.py before the learner
sees it, and is retried if it does not. The tutor's *English* — explanations,
corrections, encouragement — is unconstrained, because a beginner can audit bad
English instantly and cannot audit bad German at all. That asymmetry is the
whole basis of the tier split in jana/llm.py.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from jana import events, lexicon, llm, scheduler

MAX_VALIDATION_RETRIES = 2
TARGETS_PER_TURN = 4

PERSONA = """You are Jana, a smart, encouraging, Jarvis-style German AI instructor.

Your student is an Indian software engineer preparing for the Goethe-Zertifikat B1 exam (target: 7 Jan 2027).
He is a beginner. He learns by doing and needs:
1. Immediate, crystal-clear correction whenever he makes a grammar or vocabulary mistake.
2. Literal word-for-word translation alongside natural English so he understands sentence structure.
3. Concise grammar tips explaining WHY a rule applies (cases, endings, word order).
4. A warm, witty, intelligent instructor tone (with occasional fun culture bridges like chai vs Kaffee, Berlin vs Hyderabad).

RULES:
1. Every German sentence must use clear A1-A2 vocabulary (short sentences, <= 14 words).
2. The 'de' field contains German ONLY.
3. The 'literal_en' field gives a word-for-word breakdown (e.g. 'I | have | today | German | learned').
4. The 'correction' field: If the student made an error, explain it clearly: '❌ [Mistake] → ✅ [Correction] (Rule explanation)'. If no mistake, leave as empty string.
5. The 'grammar_tip' field: One brief, high-yield grammar insight about a word, case, or ending used.
6. The 'quick_replies' field: 2-3 short starter German sentences the student could say back.
7. Ask exactly ONE engaging question at the end of 'de'."""

RESPONSE_CONTRACT = """Reply with JSON only, no other text:
{"de": "<German message with 1 question at end>",
 "en": "<natural English translation>",
 "literal_en": "<word-for-word literal translation separated by | or spaces>",
 "correction": "<❌ mistake → ✅ fix (rule) or empty string>",
 "grammar_tip": "<short grammar rule/insight in English>",
 "encouragement": "<warm one-line encouragement in English>",
 "quick_replies": ["<German starter 1>", "<German starter 2>", "<German starter 3>"]}"""


@dataclass
class Turn:
    de: str
    en: str
    correction: str
    encouragement: str
    literal_en: str = ""
    grammar_tip: str = ""
    quick_replies: list[str] = field(default_factory=list)
    targets_used: list[str] = field(default_factory=list)
    provenance: str = "template"
    validated: bool = False
    coverage: float = 0.0
    latency_ms: int = 0
    rejected: list[str] = field(default_factory=list)
    new_words: list[str] = field(default_factory=list)


def _target_items(conn: sqlite3.Connection, limit: int = TARGETS_PER_TURN
                  ) -> list[sqlite3.Row]:
    """Today's due items — the same queue the quiz draws from."""
    reviews, fresh = scheduler.plan(conn)
    rows = list(reviews) + list(fresh)
    glossed = [r for r in rows if not r["sense_gloss_en"].startswith("[")]
    return (glossed or rows)[:limit]


def _describe(rows: list[sqlite3.Row]) -> str:
    parts = []
    for row in rows:
        surface = f"{row['gender']} {row['lemma']}" if row["gender"] else row["lemma"]
        gloss = row["sense_gloss_en"]
        parts.append(surface if gloss.startswith("[") else f"{surface} ({gloss})")
    return ", ".join(parts)


def detect_targets(text: str, rows: list[sqlite3.Row],
                   lex: frozenset[str]) -> list[str]:
    """Which target words the learner actually produced. Pure code, no model.

    Uses the same morphology as the validator, so `läuft` counts as `laufen`.
    """
    words = {w.casefold() for w in lexicon.WORD.findall(text)}
    used = []
    for row in rows:
        lemma = row["lemma"].casefold()
        if lemma in words:
            used.append(row["lemma"])
            continue
        # An inflected form of the target still counts as having produced it.
        single = frozenset({lemma})
        if any(lexicon._known(word, single) for word in words):
            used.append(row["lemma"])
    return used


def _parse(raw: str) -> dict[str, str] | None:
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) and "de" in data else None


def _fallback(targets: list[sqlite3.Row]) -> Turn:
    """When generation fails or will not validate, say something true and simple."""
    if targets:
        row = targets[0]
        word = f"{row['gender']} {row['lemma']}" if row["gender"] else row["lemma"]
        gloss = row["sense_gloss_en"] if not row["sense_gloss_en"].startswith("[") else row["lemma"]
        return Turn(
            de=f"Wie sagt man „{gloss}“ auf Deutsch?",
            en=f'How do you say "{gloss}" in German?',
            literal_en="How | says | one | [word] | in | German?",
            correction="",
            grammar_tip=f"In questions with 'Wie', the conjugated verb comes in position 2 ('sagt').",
            encouragement=f"Hint: it is {word}.",
            quick_replies=[f"Das ist {word}.", f"Man sagt {word}.", "Ich weiß es nicht."],
            provenance="template", validated=True, coverage=1.0)
    return Turn(
        de="Hallo! Wie geht es dir heute?",
        en="Hello! How are you today?",
        literal_en="Hello! | How | goes | it | to-you | today?",
        correction="",
        grammar_tip="'Wie geht es dir?' uses the Dativ pronoun 'dir' (to you).",
        encouragement="Tell me in German: 'Mir geht es gut' (I am doing well).",
        quick_replies=["Mir geht es gut, danke!", "Es geht so.", "Ich lerne gerade Deutsch."],
        provenance="template", validated=True, coverage=1.0)


def reply(conn: sqlite3.Connection, history: list[dict[str, str]],
          learner_said: str, session_id: int | None = None) -> Turn:
    lex = lexicon.build(conn)
    targets = _target_items(conn)

    used = detect_targets(learner_said, targets, lex) if learner_said else []
    if session_id is not None:
        _log_production(conn, session_id, targets, used, learner_said)

    system = (f"{PERSONA}\n\nWork these words into your reply naturally: "
              f"{_describe(targets)}\n\n{RESPONSE_CONTRACT}")
    messages = [{"role": "system", "content": system}, *history[-8:]]
    if learner_said:
        messages.append({"role": "user", "content": learner_said})
    else:
        messages.append({"role": "user",
                         "content": "Greet me warmly as my German instructor and start today's lesson."})

    rejected: list[str] = []
    for attempt in range(MAX_VALIDATION_RETRIES + 1):
        response = llm.authored(messages, temperature=0.7, max_tokens=450)
        if not response.ok:
            break
        parsed = _parse(response.text)
        if parsed is None:
            messages.append({"role": "user", "content": "Reply with JSON only."})
            continue

        german = str(parsed.get("de", ""))
        report = lexicon.check(german, lex)
        if report.ok:
            qr = parsed.get("quick_replies", [])
            if isinstance(qr, list):
                qr = [str(x) for x in qr[:3]]
            else:
                qr = []
            return Turn(
                de=german,
                en=str(parsed.get("en", "")),
                correction=str(parsed.get("correction", "")),
                encouragement=str(parsed.get("encouragement", "")),
                literal_en=str(parsed.get("literal_en", "")),
                grammar_tip=str(parsed.get("grammar_tip", "")),
                quick_replies=qr,
                targets_used=used, provenance=response.tier, validated=True,
                coverage=report.coverage, latency_ms=response.latency_ms,
                rejected=rejected, new_words=report.new_words)

        rejected.extend(report.unknown)
        messages.append({"role": "user", "content":
                         "Those words are too advanced: "
                         f"{', '.join(report.unknown[:6])}. "
                         "Rewrite using only simple A1 words."})

    fallback = _fallback(targets)
    fallback.targets_used = used
    fallback.rejected = rejected
    return fallback


def _log_production(conn: sqlite3.Connection, session_id: int,
                    targets: list[sqlite3.Row], used: list[str],
                    text: str) -> None:
    """Producing a target word in free conversation is a rung-3 success.

    This is the join between the chat and the spaced-repetition model. Without
    it the conversation would be pleasant and the scheduler would learn nothing
    from it.
    """
    for row in targets:
        if row["lemma"] not in used:
            continue
        events.append(conn, "attempt", {
            "session_id": session_id, "item_id": row["item_id"],
            "modality": row["modality"], "rung": 3, "task_type": "conversation",
            "exercise_id": None, "response": text[:200], "correct": True,
            "grade": events.GOOD, "latency_ms": None,
        })
