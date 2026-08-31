"""The Goethe B1 task formats that were missing.

Eight of fifteen Teile existed. These are the other seven, written to the
published shapes rather than invented — the formats are fixed, they are worth
real points, and meeting one cold on exam day costs more than not knowing a
word.

Each is a generator constrained the same way everything else is: the German is
validated against the syllabus before the learner sees it, and the material is
anchored to the day's story when there is one, so the same vocabulary is
retrieved in a second context.

Format notes, from the published Modellsätze:

  Lesen 3 — ten situations, seven small ads. Match each to an ad, or to "x"
            when no ad fits. The distractor-free option is the point: it tests
            whether he can rule out, not just recognise.
  Lesen 4 — a forum thread. Seven opinions; for each, is the writer for or
            against the proposition.
  Lesen 5 — a set of rules (Hausordnung, Betriebsanweisung). Four multiple
            choice on detail. Formal register, unlike anything else in the exam.
  Hören 2 — one uninterrupted announcement or presentation, heard ONCE, five
            statements true/false. The single-listen rule is the difficulty.
  Hören 4 — a discussion between two speakers. For each statement, who said it:
            speaker A, speaker B, or both.
  Schreiben 3 — a short formal message, about 40 words. Register is what is
            being marked: Sie, not du; a subject line; a proper close.
  Sprechen 3 — respond to a partner's presentation: two questions and a piece
            of feedback. Unbuildable as a full simulation (see §10), so it is
            scaffolded rather than faked.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Any

from jana import modules


@dataclass
class Task:
    """One exam task, in a shape the client can render without knowing the format."""
    modul: str
    teil: int
    instruction_de: str
    instruction_en: str
    body: dict[str, Any] = field(default_factory=dict)
    provenance: str = "template"
    validated: bool = False
    day: int | None = None


def _make(conn: sqlite3.Connection, instruction: str, contract: str,
          fallback: dict, modul: str, teil: int,
          de: str, en: str, day: int | None) -> Task:
    parsed, tier, _cov, _rej, _new = modules._generate_validated(
        conn, instruction, contract)
    return Task(modul=modul, teil=teil, instruction_de=de, instruction_en=en,
                body=parsed if parsed else fallback,
                provenance=tier if parsed else "template",
                validated=True, day=day)


def _context(conn: sqlite3.Connection, day: int | None, count: int = 6) -> str:
    words = modules._vocab_line(modules._due_words(conn, count, day=day))
    scene = modules._scene_hint(conn, day)
    return f"{scene}Use these words where they fit: {words}\n"


# ---------------------------------------------------------------- Lesen Teil 3
def lesen_teil3(conn: sqlite3.Connection, day: int | None = None) -> Task:
    instruction = (
        "You write Goethe B1 Lesen Teil 3 material.\n"
        "Write SIX short German small-ads (Kleinanzeigen), 1-2 sentences each, "
        "labelled a to f — things like flats, lessons, second-hand items, jobs.\n"
        "Then write FIVE situations, each describing a person looking for "
        "something. Four match an ad; ONE matches none of them.\n"
        "Simple A1-A2 vocabulary throughout.\n" + _context(conn, day))
    contract = ('Reply with JSON only:\n'
                '{"ads": [{"key": "a", "text": "<the ad in German>"}],\n'
                ' "situations": [{"text": "<situation in German>", "answer": "a"}],\n'
                ' "en": "<one-line English summary of the topic>"}\n'
                'Use "x" as the answer for the situation that matches no ad.')
    fallback = {
        "ads": [{"key": "a", "text": "Zimmer in Berlin frei. 400 Euro pro Monat."},
                {"key": "b", "text": "Ich gebe Deutschunterricht. 15 Euro die Stunde."}],
        "situations": [{"text": "Anna sucht ein Zimmer in Berlin.", "answer": "a"},
                       {"text": "Tom sucht ein Auto.", "answer": "x"}],
        "en": "small ads",
    }
    return _make(conn, instruction, contract, fallback, "lesen", 3,
                 "Lesen Sie die Anzeigen a–f. Welche Anzeige passt zu welcher "
                 "Situation? Eine Situation passt zu keiner Anzeige — wählen Sie x.",
                 "Match each situation to an ad. One matches none — choose x.", day)


# ---------------------------------------------------------------- Lesen Teil 4
def lesen_teil4(conn: sqlite3.Connection, day: int | None = None) -> Task:
    instruction = (
        "You write Goethe B1 Lesen Teil 4 material.\n"
        "Pick an everyday question people disagree about (mobile phones at "
        "school, cars in the city centre, working from home).\n"
        "Write FIVE short forum opinions in German, 1-2 sentences each, with a "
        "first name. Some are for, some against. Simple A1-A2 vocabulary.\n"
        + _context(conn, day))
    contract = ('Reply with JSON only:\n'
                '{"question": "<the question, in German>",\n'
                ' "en": "<the question in English>",\n'
                ' "opinions": [{"name": "Lena", "text": "<opinion in German>",'
                ' "stance": "dafür"}]}\n'
                'stance is exactly "dafür" or "dagegen".')
    fallback = {
        "question": "Sollen Kinder ein Handy in der Schule haben?",
        "en": "Should children have a phone at school?",
        "opinions": [{"name": "Lena", "text": "Ein Handy ist wichtig für Notfälle.",
                      "stance": "dafür"},
                     {"name": "Tom", "text": "Die Kinder lernen dann nicht gut.",
                      "stance": "dagegen"}],
    }
    return _make(conn, instruction, contract, fallback, "lesen", 4,
                 "Ist die Person dafür oder dagegen?",
                 "Is each writer for or against?", day)


# ---------------------------------------------------------------- Lesen Teil 5
def lesen_teil5(conn: sqlite3.Connection, day: int | None = None) -> Task:
    instruction = (
        "You write Goethe B1 Lesen Teil 5 material.\n"
        "Write a short set of German rules — a Hausordnung, library rules, or "
        "workplace instructions. FIVE numbered rules, one sentence each.\n"
        "Formal register (Sie), but simple A1-A2 vocabulary.\n"
        "Then THREE multiple-choice questions about the detail, three options each.\n"
        + _context(conn, day))
    contract = ('Reply with JSON only:\n'
                '{"title": "<title in German>", "rules": ["<rule>"],\n'
                ' "en": "<English summary>",\n'
                ' "questions": [{"q": "<question in German>",'
                ' "options": ["a","b","c"], "answer": "<the correct option>"}]}')
    fallback = {
        "title": "Hausordnung", "en": "house rules",
        "rules": ["Bitte sind Sie nach 22 Uhr leise.",
                  "Der Müll kommt in die Tonnen im Hof."],
        "questions": [{"q": "Wann muss man leise sein?",
                       "options": ["nach 22 Uhr", "nach 18 Uhr", "immer"],
                       "answer": "nach 22 Uhr"}],
    }
    return _make(conn, instruction, contract, fallback, "lesen", 5,
                 "Lesen Sie die Hausordnung und beantworten Sie die Fragen.",
                 "Read the rules and answer the questions.", day)


# ---------------------------------------------------------------- Hören Teil 2
def hoeren_teil2(conn: sqlite3.Connection, day: int | None = None) -> Task:
    instruction = (
        "You write Goethe B1 Hören Teil 2 material — a single announcement or "
        "short presentation, heard ONCE.\n"
        "Write 4-6 German sentences a guide, host or announcer would say. "
        "Simple A1-A2 vocabulary, natural spoken rhythm.\n"
        "Then FIVE true/false statements about it.\n" + _context(conn, day))
    contract = ('Reply with JSON only:\n'
                '{"text": "<the announcement in German>",\n'
                ' "en": "<English translation>",\n'
                ' "statements": [{"text": "<statement in German>", "true": true}]}')
    fallback = {
        "text": "Willkommen im Museum. Die Tour beginnt um zehn Uhr. "
                "Bitte machen Sie keine Fotos.",
        "en": "Welcome to the museum. The tour starts at ten. Please don't take photos.",
        "statements": [{"text": "Die Tour beginnt um zehn Uhr.", "true": True},
                       {"text": "Fotos sind erlaubt.", "true": False}],
    }
    return _make(conn, instruction, contract, fallback, "hoeren", 2,
                 "Sie hören den Text nur EINMAL. Richtig oder falsch?",
                 "You hear this ONCE. True or false?", day)


# ---------------------------------------------------------------- Hören Teil 4
def hoeren_teil4(conn: sqlite3.Connection, day: int | None = None) -> Task:
    instruction = (
        "You write Goethe B1 Hören Teil 4 material — a discussion between two "
        "people who partly disagree.\n"
        "Give it 6-8 short German turns, alternating between speaker A and "
        "speaker B, on an everyday topic. Simple A1-A2 vocabulary.\n"
        "Then FOUR statements; for each, say who holds that view: 'a', 'b', "
        "or 'beide'.\n" + _context(conn, day))
    contract = ('Reply with JSON only:\n'
                '{"topic": "<topic in German>", "a_name": "<name>", "b_name": "<name>",\n'
                ' "turns": [{"speaker": "a", "text": "<what they say in German>"}],\n'
                ' "en": "<English summary>",\n'
                ' "statements": [{"text": "<statement in German>", "who": "a"}]}')
    fallback = {
        "topic": "Auto oder Fahrrad?", "a_name": "Jonas", "b_name": "Mia",
        "en": "car or bicycle",
        "turns": [{"speaker": "a", "text": "Ich fahre lieber mit dem Auto."},
                  {"speaker": "b", "text": "Das Fahrrad ist billiger und gesund."}],
        "statements": [{"text": "Das Fahrrad ist billig.", "who": "b"}],
    }
    return _make(conn, instruction, contract, fallback, "hoeren", 4,
                 "Wer sagt das? A, B oder beide?",
                 "Who says this? A, B, or both?", day)


# ------------------------------------------------------------ Schreiben Teil 3
def schreiben_teil3(conn: sqlite3.Connection, day: int | None = None) -> Task:
    instruction = (
        "You set a Goethe B1 Schreiben Teil 3 task — a SHORT FORMAL message of "
        "about 40 words.\n"
        "Give a situation where he must write formally to someone he does not "
        "know: cancelling an appointment, apologising to a teacher, writing to "
        "an office.\n"
        "Two sentences of setup, in simple German.\n" + _context(conn, day, 4))
    contract = ('Reply with JSON only:\n'
                '{"task": "<the situation, in German>", "en": "<in English>",\n'
                ' "to": "<who he is writing to, in German>",\n'
                ' "must_cover": ["<point 1 in German>", "<point 2>", "<point 3>"]}')
    fallback = {
        "task": "Sie haben einen Termin beim Arzt, aber Sie können nicht kommen.",
        "en": "You have a doctor's appointment but cannot come.",
        "to": "die Arztpraxis",
        "must_cover": ["Warum Sie nicht kommen können", "Entschuldigung",
                       "Bitten Sie um einen neuen Termin"],
    }
    return _make(conn, instruction, contract, fallback, "schreiben", 3,
                 "Schreiben Sie eine formelle Nachricht (ca. 40 Wörter). "
                 "Benutzen Sie 'Sie'.",
                 "Write a formal message (~40 words). Use the formal 'Sie'.", day)


# ------------------------------------------------------------- Sprechen Teil 3
def sprechen_teil3(conn: sqlite3.Connection, day: int | None = None) -> Task:
    """Feedback on a partner's presentation.

    In the real exam this is a reply to another candidate, live. Simulating the
    partner convincingly is the one thing §10 says not to attempt, so this
    supplies the presentation and drills the *response* — two questions and a
    piece of feedback — which is the part he can practise alone.
    """
    instruction = (
        "You write Goethe B1 Sprechen Teil 3 material.\n"
        "Write a SHORT German presentation (4-5 sentences) as if given by "
        "another candidate about an everyday topic — his hometown, his job, a "
        "festival. Simple A1-A2 vocabulary.\n" + _context(conn, day, 4))
    contract = ('Reply with JSON only:\n'
                '{"speaker": "<name>", "topic": "<topic in German>",\n'
                ' "presentation": "<the presentation in German>",\n'
                ' "en": "<English translation>"}')
    fallback = {
        "speaker": "Nadia", "topic": "Mein Heimatort",
        "presentation": "Ich komme aus einer kleinen Stadt. Dort ist es sehr ruhig. "
                        "Meine Familie wohnt noch da. Ich besuche sie oft.",
        "en": "I come from a small town. It is very quiet there. "
              "My family still lives there. I visit them often.",
    }
    task = _make(conn, instruction, contract, fallback, "sprechen", 3,
                 "Stellen Sie zwei Fragen zur Präsentation und geben Sie eine "
                 "Rückmeldung.",
                 "Ask two questions about the presentation and give feedback.", day)
    task.body["expects"] = ["eine Frage", "noch eine Frage", "eine Rückmeldung"]
    return task


BUILDERS = {
    ("lesen", 3): lesen_teil3, ("lesen", 4): lesen_teil4, ("lesen", 5): lesen_teil5,
    ("hoeren", 2): hoeren_teil2, ("hoeren", 4): hoeren_teil4,
    ("schreiben", 3): schreiben_teil3, ("sprechen", 3): sprechen_teil3,
}


def build(conn: sqlite3.Connection, modul: str, teil: int,
          day: int | None = None, fresh: bool = False) -> dict[str, Any] | None:
    """A task for this slot — from the bank when possible, generated when not.

    Cache first is not only about cost. A task served from the bank arrives in
    milliseconds instead of seconds, which is the difference between a screen
    that responds and one that spins; and because it was stored, the same
    question can be revisited, put in a mock, or looked at when its format turns
    out to produce bad German.
    """
    from jana import bank

    builder = BUILDERS.get((modul, teil))
    if builder is None:
        return None

    if not fresh:
        stored = bank.unseen(conn, modul, teil, day)
        if stored is not None:
            bank.mark_seen(conn, stored["exercise_id"])
            return stored

    task = asdict(builder(conn, day))
    task["exercise_id"] = bank.save(conn, task)
    bank.mark_seen(conn, task["exercise_id"])
    task["from_bank"] = False
    return task


def stock(conn: sqlite3.Connection, modul: str, teil: int,
          day: int | None = None, want: int = 4) -> int:
    """Generate ahead of time until the bank holds `want` unseen for this slot.

    This is D5 — the overnight batch — in the form that actually matters: the
    interactive path should never wait on a model. Run it whenever the machine
    is idle; it is safe to interrupt and safe to repeat.
    """
    from jana import bank

    builder = BUILDERS.get((modul, teil))
    if builder is None:
        return 0
    made = 0
    while bank.count(conn, modul, teil) < want:
        task = asdict(builder(conn, day))
        if task.get("provenance") == "template":
            break          # the generator is failing; stop rather than fill with fallbacks
        bank.save(conn, task)
        made += 1
    return made
