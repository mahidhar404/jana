"""The four Goethe B1 exam modules: Lesen, Hören, Schreiben, Sprechen.

Each module is a thin composition of parts that already exist — the scheduler
picks what to practise, jana/llm.py produces material, jana/lexicon.py refuses
anything out of syllabus, and jana/grader.py marks what can be marked without a
model. Nothing here re-implements those.

Audio without an audio stack
----------------------------
Hören and Sprechen normally need a speech pipeline: ffmpeg, an ASR model, a TTS
voice. None of that is installed, and installing it is a multi-hour detour that
would have delayed every other module.

The browser already ships both. `SpeechSynthesis` has German voices on macOS,
and `SpeechRecognition` does German dictation. So the server sends *text* and
the client speaks it; the client transcribes speech and sends *text* back. The
audio never crosses the wire and the server needs no audio stack at all.

The trade is honest and worth naming: browser ASR is weaker than whisper
large-v3 and will mis-hear a beginner's accent, so Sprechen scoring here is
indicative rather than exam-grade. It is the right call anyway, because a
mediocre speaking loop used daily beats an excellent one that ships in November.
The interface is text either way, so swapping whisper in later changes one
adapter and nothing else.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from jana import events, grader, lexicon, llm, scheduler

MAX_GENERATION_RETRIES = 2


@dataclass
class Material:
    """Something to read, hear, write about or say."""
    text: str
    translation: str = ""
    questions: list[dict[str, Any]] = field(default_factory=list)
    item_ids: list[int] = field(default_factory=list)
    provenance: str = "template"
    validated: bool = False
    coverage: float = 0.0
    new_words: list[str] = field(default_factory=list)


def _story_words(conn: sqlite3.Connection, day: int, n: int) -> list[sqlite3.Row]:
    """Vocabulary from a day's conversation, newest scenes first."""
    return conn.execute(
        """SELECT i.id AS item_id, 'text' AS modality, 1 AS rung, NULL AS due_at,
                  i.lemma, i.gender, i.pos, i.sense_gloss_en
           FROM story_vocab v
           JOIN story_day d ON d.id = v.day_id
           JOIN item i      ON i.id = v.item_id
           WHERE d.day_number = ? AND i.sense_gloss_en NOT LIKE '[%'
           ORDER BY random() LIMIT ?""", (day, n)).fetchall()


def _due_words(conn: sqlite3.Connection, n: int = 6,
               day: int | None = None) -> list[sqlite3.Row]:
    """What this exercise should be built around.

    When a day is given, the day's own conversation comes first. That is the
    whole point of anchoring practice to the story: meeting `Postleitzahl` again
    in a reading passage, an hour after the border officer asked for it, is a
    retrieval with a cue attached. The same word drawn at random from 2,000
    items is a cue-free retrieval, which is harder in the useless way.

    The FSRS queue fills whatever the day cannot, so a thin conversation never
    starves an exercise.
    """
    chosen: list[sqlite3.Row] = []
    if day is not None:
        chosen = list(_story_words(conn, day, n))
    if len(chosen) < n:
        reviews, fresh = scheduler.plan(conn)
        rows = list(reviews) + list(fresh)
        glossed = [r for r in rows if not r["sense_gloss_en"].startswith("[")]
        seen = {r["item_id"] for r in chosen}
        chosen += [r for r in (glossed or rows) if r["item_id"] not in seen]
    return chosen[:n]


def _vocab_line(rows: list[sqlite3.Row]) -> str:
    return ", ".join(
        f"{r['gender'] + ' ' if r['gender'] else ''}{r['lemma']}" for r in rows)


def _generate_validated(conn: sqlite3.Connection, instruction: str,
                        contract: str
                        ) -> tuple[dict | None, str, float, list[str], list[str]]:
    """Generate German, refuse it if it leaves the syllabus, ask again.

    Returns (parsed, provenance, coverage, rejected). The rejected list is the
    interesting output: it is a running measurement of how often each model
    stays inside the syllabus, which is the model bake-off running in production
    rather than once in a notebook.
    """
    lex = lexicon.build(conn)
    messages = [{"role": "system", "content": instruction + "\n\n" + contract},
                {"role": "user", "content": "Produce it now."}]
    rejected: list[str] = []

    for _ in range(MAX_GENERATION_RETRIES + 1):
        response = llm.authored(messages, temperature=0.6, max_tokens=600)
        if not response.ok:
            break
        match = re.search(r"\{.*\}", response.text, re.S)
        if not match:
            messages.append({"role": "user", "content": "JSON only."})
            continue
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            messages.append({"role": "user", "content": "Valid JSON only."})
            continue

        german = " ".join(_german_strings(parsed))
        report = lexicon.check(german, lex)
        if report.ok:
            return (parsed, response.tier, report.coverage, rejected,
                    report.new_words)
        rejected.extend(report.unknown)
        messages.append({"role": "user", "content":
                         f"Too advanced: {', '.join(report.unknown[:6])}. "
                         "Rewrite with simple A1-A2 words only."})
    return None, "none", 0.0, rejected, []


# Fields that carry structure rather than language: option labels, answer keys,
# speaker ids. Validating them as German rejects a perfectly good exercise
# because "a" is not in the Wortliste — which is how Lesen Teil 3 ended up
# falling back to a template on every attempt.
STRUCTURAL_KEYS = frozenset({
    # English by design — checking these against a German syllabus rejects the
    # exercise for containing English, which is what they are for.
    "en", "english", "translation", "explanation", "hint_en", "why",
    "instruction_en", "note", "rule", "feedback", "good", "fix",
    # Structure rather than language: labels, keys, speaker ids.
    "key", "answer", "stance", "who", "speaker", "id", "correct", "true",
})


def _german_strings(parsed: Any) -> list[str]:
    """Every German-bearing string in a response, for validation."""
    out: list[str] = []
    if isinstance(parsed, dict):
        for key, value in parsed.items():
            if key in STRUCTURAL_KEYS:
                continue
            out.extend(_german_strings(value))
    elif isinstance(parsed, list):
        for value in parsed:
            out.extend(_german_strings(value))
    elif isinstance(parsed, str):
        # A one- or two-letter token is a label, not a word.
        if len(parsed.strip()) > 2:
            out.append(parsed)
    return out


def _scene_hint(conn: sqlite3.Connection, day: int | None) -> str:
    """Tell the generator which scene today's practice belongs to.

    Without this the exercises are set in a generic everyday-life nowhere. With
    it, the reading passage happens in the place the learner was this morning,
    which is the difference between reviewing a word and remembering a moment.
    """
    if day is None:
        return ""
    row = conn.execute(
        "SELECT title, setting_de, npc_name FROM story_day WHERE day_number = ?",
        (day,)).fetchone()
    if row is None:
        return ""
    return (f"Set it in the same scene he lived today: \"{row['title']}\" — "
            f"{row['setting_de']}, where he met {row['npc_name']}.\n")


# --------------------------------------------------------------------- Lesen
LESEN_CONTRACT = """Reply with JSON only:
{"text": "<German passage>", "en": "<English translation>",
 "questions": [{"q": "<question in German>", "options": ["<a>","<b>","<c>"],
                "answer": "<the correct option, copied exactly>"}]}"""


def lesen(conn: sqlite3.Connection, day: int | None = None) -> Material:
    words = _due_words(conn, day=day)
    scene = _scene_hint(conn, day)
    instruction = (
        "You write reading-comprehension material for a Goethe B1 candidate who "
        "is currently a beginner.\n"
        "Write a SHORT German passage of 3-4 sentences about everyday life — "
        "an Indian software engineer preparing to move to Germany.\n"
        "Use ONLY simple A1-A2 vocabulary. Short sentences.\n"
        f"{scene}"
        f"Work in these words if you can: {_vocab_line(words)}.\n"
        "Then write exactly 2 comprehension questions in German, each with 3 "
        "options, one correct.")
    parsed, tier, coverage, _, new_words = _generate_validated(
        conn, instruction, LESEN_CONTRACT)
    if parsed is None:
        return Material(
            text="Ich wohne in Indien. Ich lerne Deutsch. Ich möchte nach "
                 "Deutschland fahren.",
            translation="I live in India. I am learning German. I want to go to "
                        "Germany.",
            questions=[{"q": "Wo wohne ich?",
                        "options": ["in Indien", "in Deutschland", "in Berlin"],
                        "answer": "in Indien"}],
            item_ids=[r["item_id"] for r in words], provenance="template",
            validated=True, coverage=1.0)
    return Material(
        text=str(parsed.get("text", "")), translation=str(parsed.get("en", "")),
        questions=list(parsed.get("questions", [])),
        item_ids=[r["item_id"] for r in words], provenance=tier,
        validated=True, coverage=coverage, new_words=new_words)


# --------------------------------------------------------------------- Hören
def lesen_teil(conn: sqlite3.Connection, teil: int = 2,
               day: int | None = None) -> Material:
    if teil == 2:
        return lesen(conn, day=day)
    elif teil == 1:
        words = _due_words(conn, day=day)
        instruction = (
            "You write reading-comprehension material for a Goethe B1 candidate who "
            "is currently a beginner.\n"
            "Write a SHORT German blog post or email of 3-4 sentences.\n"
            "Use ONLY simple A1-A2 vocabulary.\n"
            f"Work in these words if you can: {_vocab_line(words)}.\n"
            "Then write exactly 2 true/false (richtig/falsch) statements in German based on the text.")
        contract = 'Reply with JSON only:\n{"text": "<German text>", "en": "<English>", "questions": [{"q": "<statement>", "options": ["richtig", "falsch"], "answer": "<richtig or falsch>"}]}'
        parsed, tier, coverage, _, new_words = _generate_validated(conn, instruction, contract)
        if parsed is None:
            return lesen(conn)
        return Material(
            text=str(parsed.get("text", "")), translation=str(parsed.get("en", "")),
            questions=list(parsed.get("questions", [])),
            item_ids=[r["item_id"] for r in words], provenance=tier,
            validated=True, coverage=coverage, new_words=new_words)
    else: # teil 3
        words = _due_words(conn)
        instruction = (
            "You write reading-comprehension material for a Goethe B1 candidate who is a beginner.\n"
            "Write 3 short German classified ads or notices (Anzeigen). Use simple A1-A2 vocabulary.\n"
            f"Work in these words if you can: {_vocab_line(words)}.\n"
            "Then write 3 matching questions, e.g. 'Who is looking for a bike?' with options matching the ads.")
        contract = 'Reply with JSON only:\n{"text": "<Ad 1>\n\n<Ad 2>\n\n<Ad 3>", "en": "<English translations>", "questions": [{"q": "<question>", "options": ["<Ad 1 person>", "<Ad 2 person>", "<Ad 3 person>"], "answer": "<correct option>"}]}'
        parsed, tier, coverage, _, new_words = _generate_validated(conn, instruction, contract)
        if parsed is None:
            return lesen(conn)
        return Material(
            text=str(parsed.get("text", "")), translation=str(parsed.get("en", "")),
            questions=list(parsed.get("questions", [])),
            item_ids=[r["item_id"] for r in words], provenance=tier,
            validated=True, coverage=coverage, new_words=new_words)

def hoeren(conn: sqlite3.Connection, day: int | None = None) -> Material:
    """A sentence for the browser to speak. The learner types what he heard."""
    words = _due_words(conn, 3, day=day)
    scene = _scene_hint(conn, day)
    instruction = (
        "You write listening practice for a German beginner.\n"
        "Write ONE short German sentence, 6-10 words, simple A1-A2 vocabulary, "
        "about everyday life.\n"
        f"{scene}"
        f"Use one of these words: {_vocab_line(words)}.")
    contract = 'Reply with JSON only: {"de": "<the sentence>", "en": "<English>"}'
    parsed, tier, coverage, _, new_words = _generate_validated(
        conn, instruction, contract)
    if parsed is None:
        return Material(text="Ich fahre morgen nach Berlin.",
                        translation="I am going to Berlin tomorrow.",
                        item_ids=[r["item_id"] for r in words],
                        provenance="template", validated=True, coverage=1.0)
    return Material(text=str(parsed.get("de", "")),
                    translation=str(parsed.get("en", "")),
                    item_ids=[r["item_id"] for r in words], provenance=tier,
                    validated=True, coverage=coverage, new_words=new_words)


def hoeren_teil(conn: sqlite3.Connection, teil: int = 1,
                day: int | None = None) -> Material:
    if teil == 3:  # dictation
        return hoeren(conn, day=day)
    elif teil == 1:
        words = _due_words(conn, 3, day=day)
        instruction = (
            "You write listening practice for a German beginner.\n"
            "Write a short German announcement (Ansage) of 3-4 sentences.\n"
            "Use ONLY simple A1-A2 vocabulary.\n"
            f"Use some of these words: {_vocab_line(words)}.\n"
            "Then write exactly 2 true/false (richtig/falsch) comprehension questions in German.")
        contract = 'Reply with JSON only:\n{"de": "<German text>", "en": "<English>", "questions": [{"q": "<statement>", "options": ["richtig", "falsch"], "answer": "<richtig or falsch>"}]}'
        parsed, tier, coverage, _, new_words = _generate_validated(conn, instruction, contract)
        if parsed is None:
            return hoeren(conn)
        return Material(
            text=str(parsed.get("de", "")), translation=str(parsed.get("en", "")),
            questions=list(parsed.get("questions", [])),
            item_ids=[r["item_id"] for r in words], provenance=tier,
            validated=True, coverage=coverage, new_words=new_words)
    else: # teil 2
        words = _due_words(conn, 3)
        instruction = (
            "You write listening practice for a German beginner.\n"
            "Write a short German dialogue (4-6 lines) between two speakers.\n"
            "Use ONLY simple A1-A2 vocabulary.\n"
            f"Use some of these words: {_vocab_line(words)}.\n"
            "Then write exactly 2 multiple-choice comprehension questions in German, each with 3 options.")
        contract = 'Reply with JSON only:\n{"de": "<German dialogue>", "en": "<English>", "questions": [{"q": "<question>", "options": ["<a>", "<b>", "<c>"], "answer": "<correct>"}]}'
        parsed, tier, coverage, _, new_words = _generate_validated(conn, instruction, contract)
        if parsed is None:
            return hoeren(conn)
        return Material(
            text=str(parsed.get("de", "")), translation=str(parsed.get("en", "")),
            questions=list(parsed.get("questions", [])),
            item_ids=[r["item_id"] for r in words], provenance=tier,
            validated=True, coverage=coverage, new_words=new_words)

def grade_hoeren(heard: str, original: str) -> dict[str, Any]:
    """Word-level comparison. Deterministic — no model needed to diff two strings."""
    said = grader.normalize(heard).split()
    want = grader.normalize(original).split()
    matched = sum(1 for word in want if word in said)
    accuracy = matched / len(want) if want else 0.0
    return {
        "accuracy": round(accuracy, 2),
        "correct": accuracy >= 0.8,
        "missed": [w for w in want if w not in said],
        "expected": original,
    }


# ------------------------------------------------------------------ Schreiben
SCHREIBEN_TEIL1_TASKS = [
    ("Schreiben Sie eine E-Mail an Ihren Freund. Sie ziehen nach Deutschland.",
     "Write an email to your friend. You are moving to Germany."),
    ("Schreiben Sie eine E-Mail an Ihren Chef. Sie sind krank.",
     "Write an email to your boss. You are ill."),
    ("Schreiben Sie eine E-Mail an Ihren Vermieter. Die Heizung ist kaputt.",
     "Write an email to your landlord. The heating is broken."),
    ("Schreiben Sie eine E-Mail an einen Freund. Sie laden ihn zum Essen ein.",
     "Write an email to a friend. You are inviting him to dinner."),
]

SCHREIBEN_TEIL2_TASKS = [
    ("In einem Forum lesen Sie einen Beitrag zum Thema 'Sollen Kinder Handys haben?'. Schreiben Sie Ihre Meinung dazu.",
     "In a forum you read a post about 'Should children have phones?'. Write your opinion."),
    ("In einem Forum lesen Sie einen Beitrag zum Thema 'Soziale Medien'. Schreiben Sie Ihre Meinung dazu.",
     "In a forum you read a post about 'Social media'. Write your opinion."),
    ("In einem Forum lesen Sie einen Beitrag zum Thema 'Online lernen'. Schreiben Sie Ihre Meinung dazu.",
     "In a forum you read a post about 'Learning online'. Write your opinion."),
    ("In einem Forum lesen Sie einen Beitrag zum Thema 'Gesundes Essen'. Schreiben Sie Ihre Meinung dazu.",
     "In a forum you read a post about 'Healthy eating'. Write your opinion."),
]

SCHREIBEN_TEIL3_TASKS = [
    ("Schreiben Sie einen formellen Brief an ein Sprachinstitut. Sie möchten einen Deutschkurs besuchen.",
     "Write a formal letter to a language institute. You would like to attend a German course."),
    ("Schreiben Sie eine formelle E-Mail an ein Hotel. Sie möchten sich beschweren.",
     "Write a formal email to a hotel. You would like to complain."),
    ("Schreiben Sie eine formelle E-Mail für eine Bewerbung. Sie suchen einen Job.",
     "Write a formal email for an application. You are looking for a job."),
    ("Schreiben Sie eine formelle E-Mail an Ihren Vermieter. Sie haben eine Bitte.",
     "Write a formal email to your landlord. You have a request."),
]

def schreiben_prompt(teil: int | None = None) -> tuple[str, str, int]:
    """Returns (task_de, task_en, teil_number). Cycles across all 3 Teile if teil is None."""
    import random
    if teil is None:
        teil = random.choice([1, 2, 3])
    if teil == 1:
        t = random.choice(SCHREIBEN_TEIL1_TASKS)
    elif teil == 2:
        t = random.choice(SCHREIBEN_TEIL2_TASKS)
    else:
        t = random.choice(SCHREIBEN_TEIL3_TASKS)
    return (t[0], t[1], teil)

SCHREIBEN_RUBRIC = """You are a Goethe B1 examiner marking a written task.

Mark on the four official criteria, each 0-5:
  erfuellung  — did the writing do what the task asked?
  kohaerenz   — does it hang together, with connectors?
  wortschatz  — range and accuracy of vocabulary
  strukturen  — grammar: cases, verb position, endings

Be encouraging but honest. The student is a beginner aiming at B1.
List at most 4 concrete corrections, each showing the original and the fix.

Reply with JSON only:
{"erfuellung": 0, "kohaerenz": 0, "wortschatz": 0, "strukturen": 0,
 "corrections": [{"was": "<what he wrote>", "should_be": "<corrected>",
                  "why": "<short English reason>"}],
 "feedback": "<two warm sentences in English>",
 "better_version": "<his text rewritten correctly, same simple level>"}"""


def schreiben_task(conn: sqlite3.Connection, day: int | None = None,
                   teil: int = 1) -> dict[str, Any]:
    """A writing task about today's scene, falling back to the fixed Teil bank.

    Anchoring matters more here than anywhere else: writing about the flat he
    was shown this morning recruits the vocabulary he met this morning, while a
    generic "write to your landlord" recruits whatever he happens to remember.
    """
    import random
    row = conn.execute(
        "SELECT title, setting_de, npc_name, theme FROM story_day"
        " WHERE day_number = ?", (day,)).fetchone() if day else None
    if row is None:
        task, english, resolved = schreiben_prompt(teil)
        return {"task": task, "en": english, "min_words": 40, "teil": resolved}

    words = _vocab_line(_due_words(conn, 6, day=day))
    instruction = (
        "You set writing tasks for a Goethe B1 candidate.\n"
        f"He spent today in this scene: \"{row['title']}\" — {row['setting_de']}, "
        f"where he met {row['npc_name']}. Topic: {row['theme']}.\n"
        "Write ONE short email task in German that follows on from that scene. "
        "Simple A1-A2 wording. Two sentences at most.\n"
        f"He should be able to use: {words}.")
    contract = ('Reply with JSON only: {"task": "<the task, in German>", '
                '"en": "<the same in English>"}')
    parsed, _tier, _cov, _rej, _new = _generate_validated(conn, instruction, contract)
    if parsed is None:
        task, english, resolved = schreiben_prompt(teil)
        return {"task": task, "en": english, "min_words": 40, "teil": resolved}
    return {"task": str(parsed.get("task", "")), "en": str(parsed.get("en", "")),
            "min_words": 40, "teil": teil, "day": day}


def sprechen_task(conn: sqlite3.Connection, day: int | None = None,
                  teil: int = 3) -> dict[str, Any]:
    """A speaking prompt about today's scene."""
    import random
    row = conn.execute(
        "SELECT title, setting_de, npc_name FROM story_day WHERE day_number = ?",
        (day,)).fetchone() if day else None
    if row is None:
        prompt, english = random.choice(SPRECHEN_PROMPTS)
        return {"prompt": prompt, "en": english, "teil": teil}

    instruction = (
        "You set speaking prompts for a Goethe B1 candidate.\n"
        f"He spent today in this scene: \"{row['title']}\" — {row['setting_de']}, "
        f"where he met {row['npc_name']}.\n"
        "Write ONE short German question asking him to talk about it. "
        "Simple A1-A2 wording, one sentence.")
    contract = ('Reply with JSON only: {"prompt": "<the question in German>", '
                '"en": "<the same in English>"}')
    parsed, _tier, _cov, _rej, _new = _generate_validated(conn, instruction, contract)
    if parsed is None:
        prompt, english = random.choice(SPRECHEN_PROMPTS)
        return {"prompt": prompt, "en": english, "teil": teil}
    return {"prompt": str(parsed.get("prompt", "")), "en": str(parsed.get("en", "")),
            "teil": teil, "day": day}


def grade_schreiben(text: str, teil: int = 1) -> dict[str, Any]:
    """Graded on the remote tier when available: this needs real German judgement."""
    rubric = SCHREIBEN_RUBRIC
    if teil == 2:
        rubric += "\n\nFor Teil 2, check that the student states their opinion clearly and provides an argument structure."
    elif teil == 3:
        rubric += "\n\nFor Teil 3, check for formal register (Sie, formal greeting and sign-off)."

    response = llm.authored(
        [{"role": "system", "content": rubric},
         {"role": "user", "content": f"The student wrote:\n\n{text}"}],
        temperature=0.3, max_tokens=800)
    if not response.ok:
        return {"error": "grading unavailable", "tier": response.tier}
    match = re.search(r"\{.*\}", response.text, re.S)
    if not match:
        return {"error": "grader returned no JSON", "tier": response.tier}
    try:
        result = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {"error": "grader returned invalid JSON", "tier": response.tier}

    scores = [int(result.get(k, 0) or 0)
              for k in ("erfuellung", "kohaerenz", "wortschatz", "strukturen")]
    result["total"] = sum(scores)
    result["max"] = 20
    result["tier"] = response.tier
    result["words"] = len(text.split())
    return result


# ------------------------------------------------------------------- Sprechen
SPRECHEN_TEIL1_TASKS = [
    {"scenario": "Sie und Ihr Freund möchten zusammen eine Geburtstagsparty organisieren.",
     "en": "You and your friend want to organize a birthday party together.",
     "points": ["Wann?", "Wo?", "Essen und Trinken?", "Musik?", "Gäste?"]},
    {"scenario": "Sie und Ihr Kollege möchten einen Ausflug machen.",
     "en": "You and your colleague want to take a trip.",
     "points": ["Wohin?", "Wann?", "Verkehrsmittel?", "Aktivitäten?", "Kosten?"]},
    {"scenario": "Sie und ein Freund möchten eine Lerngruppe gründen.",
     "en": "You and a friend want to start a study group.",
     "points": ["Welches Fach?", "Wie oft?", "Wo?", "Wer noch?", "Materialien?"]},
    {"scenario": "Sie ziehen um und brauchen Hilfe von einem Freund.",
     "en": "You are moving and need help from a friend.",
     "points": ["Wann?", "Was transportieren?", "Wer hilft noch?", "Essen für Helfer?", "Fahrzeug?"]},
]

SPRECHEN_TEIL2_TASKS = [
    {"topic": "Haustiere",
     "en": "Pets",
     "keywords": ["Vorteile", "Nachteile", "persönliche Erfahrung", "Situation in Ihrem Land"]},
    {"topic": "Sport",
     "en": "Sport",
     "keywords": ["Warum wichtig?", "Welche Sportarten?", "persönliche Erfahrung", "Situation in Ihrem Land"]},
    {"topic": "Sprachen lernen",
     "en": "Learning languages",
     "keywords": ["Vorteile", "Wie am besten?", "persönliche Erfahrung", "Situation in Ihrem Land"]},
    {"topic": "Stadt oder Land",
     "en": "City or Countryside",
     "keywords": ["Vorteile Stadt", "Vorteile Land", "persönliche Erfahrung", "Situation in Ihrem Land"]},
]

SPRECHEN_TEIL3_PROMPTS = [
    ("Stellen Sie sich vor. Wie heißen Sie? Woher kommen Sie?",
     "Introduce yourself. What is your name? Where are you from?"),
    ("Erzählen Sie über Ihren Tag. Was machen Sie heute?",
     "Talk about your day. What are you doing today?"),
    ("Warum lernen Sie Deutsch?", "Why are you learning German?"),
    ("Beschreiben Sie Ihre Familie.", "Describe your family."),
    ("Was essen Sie gern? Warum?", "What do you like to eat? Why?"),
]

def sprechen_prompt(teil: int | None = None) -> dict:
    """Returns the prompt data for the given Teil."""
    import random
    if teil is None:
        teil = random.choice([1, 2, 3])
    if teil == 1:
        return {"teil": 1, "data": random.choice(SPRECHEN_TEIL1_TASKS)}
    elif teil == 2:
        return {"teil": 2, "data": random.choice(SPRECHEN_TEIL2_TASKS)}
    else:
        prompt, en = random.choice(SPRECHEN_TEIL3_PROMPTS)
        return {"teil": 3, "data": {"prompt": prompt, "en": en}}

SPRECHEN_RUBRIC = """You are a friendly Goethe B1 speaking examiner.

The text below is a browser's transcription of a beginner speaking German, so
expect transcription noise. Judge the German, not the audio quality, and say so
if the transcript looks garbled.

Reply with JSON only:
{"score": 0, "max": 10,
 "good": "<one specific thing he did well, in English>",
 "fix": "<the single most useful correction, in English>",
 "model_answer": "<a short, simple, correct German answer he could have given>"}"""


def grade_sprechen(transcript: str, prompt: str, teil: int = 3) -> dict[str, Any]:
    rubric = SPRECHEN_RUBRIC
    if teil == 1:
        rubric += "\n\nFor Teil 1, grade on interaction, negotiation, making suggestions."
    elif teil == 2:
        rubric += "\n\nFor Teil 2, grade on structure, coherence, covering all keywords."
    elif teil == 3:
        rubric += "\n\nFor Teil 3, grade on spontaneous reaction."

    response = llm.authored(
        [{"role": "system", "content": rubric},
         {"role": "user",
          "content": f"Question: {prompt}\n\nHe said: {transcript}"}],
        temperature=0.3, max_tokens=500)
    if not response.ok:
        return {"error": "grading unavailable", "tier": response.tier}
    match = re.search(r"\{.*\}", response.text, re.S)
    if not match:
        return {"error": "grader returned no JSON", "tier": response.tier}
    try:
        result = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {"error": "grader returned invalid JSON", "tier": response.tier}
    result["tier"] = response.tier
    result["transcript"] = transcript
    return result


def log_module_attempt(conn: sqlite3.Connection, session_id: int | None,
                       item_ids: list[int], module: str, correct: bool,
                       response: str) -> None:
    """Exam-module work feeds the same learner model as the drills."""
    for item_id in item_ids:
        events.append(conn, "attempt", {
            "session_id": session_id, "item_id": item_id, "modality": "text",
            "rung": 4, "task_type": module, "exercise_id": None,
            "response": response[:200], "correct": correct,
            "grade": events.GOOD if correct else events.AGAIN, "latency_ms": None,
        })
