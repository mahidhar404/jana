"""The daily episode: one day, one scene, one conversation.

The shape
---------
Each day the learner walks somewhere and meets someone. That person speaks
German. The learner answers — and here is the part that makes this teach rather
than entertain: he may answer in *either* language, and the system reads which
one he chose as a statement about how much help he needs.

  * He types **English**. His character says it in German, and he sees the
    sentence he meant, correctly formed. This is modelling: comprehensible
    input aimed exactly at what he was already trying to express, which is the
    most receptive moment there is.
  * He types **German**. It is corrected, the rule is named, and the attempt is
    logged at a higher rung. This is pushed output.

Nobody chooses a difficulty setting. The language he types *is* the difficulty
setting, it can change mid-conversation, and it costs him nothing to slide back
down on a sentence he cannot manage yet.

Why the story exists at all
---------------------------
The architecture doc ruled a narrative engine out of scope: a fixed exam format
rewards format drilling per minute more than immersion does. That is right about
retrieval and wrong about encoding. Encoding specificity says a memory is
easiest to reach through a cue resembling the one it was laid down with — so a
word met while haggling over a flat in a scene the learner was *in* has a hook
that the same word on a flashcard does not.

The resolution is not to pick a side. The story is where vocabulary is met; the
exam modules are where it is retrieved, and they keep their exam shape. Each
day's Lesen, Hören, Schreiben and Sprechen draw their material from that day's
conversation, so the drilling is exam-format *and* context-anchored.

The arc is human-authored
-------------------------
`ARC` below is written by hand, not generated: sixty days from landing at the
airport to sitting the exam, each mapped to a Goethe B1 topic. The model writes
dialogue inside a scene it is given; it does not decide what the learner's life
looks like or which topics the exam covers. That keeps the curriculum under
human control (D1) and leaves the model doing the thing it is good at.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date as date_type
from typing import Any

from jana import events, explain, lexicon, llm, scheduler

TURNS_PER_DAY = 50          # per speaker; the day is "full" at this many
MAX_RETRIES = 2

# How many days after first meeting a word the story brings it back.
#
# Expanding rather than uniform, because the forgetting curve is a curve: the
# gap that is useful right after learning is useless a month later, and a fixed
# weekly review spends most of its effort on words that were not going to be
# forgotten this week. These are the classic expanding intervals, capped at 35
# days so that a word met in September is still revisited before January.
#
# FSRS already schedules the *drills*. This is a different job: it decides which
# old words the conversation should walk past again, so the review happens in
# context rather than on a card. The two are complementary and both run.
REVIEW_OFFSETS = (1, 3, 7, 16, 35)

# Sixty days, following one person from arrival to exam. Each entry is
# (title, setting, who they meet, Goethe B1 topic).
ARC: list[tuple[str, str, str, str]] = [
    ("Ankunft", "Flughafen Frankfurt, früher Morgen", "Grenzbeamtin Frau Weber", "Reisen"),
    ("Die erste Fahrt", "Zug nach Berlin", "ein Mitreisender, Tobias", "Reisen"),
    ("Das Zimmer", "eine kleine Wohnung in Neukölln", "Vermieter Herr Krause", "Wohnen"),
    ("Der Supermarkt", "Supermarkt am Hermannplatz", "eine Kassiererin", "Einkaufen"),
    ("Das Bürgeramt", "Bürgeramt, Warteraum", "Sachbearbeiterin Frau Yilmaz", "Behörden"),
    ("Die Bank", "Sparkasse, Schalter", "ein Bankberater", "Geld"),
    ("Der Nachbar", "Treppenhaus", "Nachbarin Frau Schulz", "Wohnen"),
    ("Das Handy", "Handyladen", "ein Verkäufer", "Technik"),
    ("Der erste Arbeitstag", "Büro in Mitte", "Kollegin Anna", "Arbeit"),
    ("Die Mittagspause", "Kantine", "Kollege Markus", "Essen und Trinken"),
    ("Der Arzt", "Arztpraxis", "Dr. Hoffmann", "Gesundheit"),
    ("Die Apotheke", "Apotheke an der Ecke", "eine Apothekerin", "Gesundheit"),
    ("Der Sportverein", "Sporthalle", "Trainer Stefan", "Freizeit"),
    ("Das Café", "Café am Kanal", "eine Kellnerin", "Essen und Trinken"),
    ("Der Deutschkurs", "Volkshochschule", "Lehrerin Frau Bauer", "Bildung"),
    ("Die Post", "Postfiliale", "ein Postbeamter", "Alltag"),
    ("Der Fahrradladen", "Fahrradladen", "ein Mechaniker", "Verkehr"),
    ("Die Verspätung", "S-Bahnhof", "ein wartender Fahrgast", "Verkehr"),
    ("Die Einladung", "vor der Haustür", "Nachbar Herr Lehmann", "Soziales"),
    ("Die Party", "Wohnung von Anna", "Annas Freundin Lisa", "Soziales"),
    ("Der Umzug", "vor dem alten Haus", "ein Umzugshelfer", "Wohnen"),
    ("Das Möbelhaus", "Möbelhaus am Stadtrand", "eine Verkäuferin", "Einkaufen"),
    ("Die Reparatur", "Wohnung, Küche", "ein Handwerker", "Wohnen"),
    ("Das Wetter", "Park im Regen", "eine Hundebesitzerin", "Umwelt"),
    ("Der Ausflug", "Bahnhof, Gleis 7", "Kollegin Anna", "Reisen"),
    ("Das Museum", "Museumsinsel", "eine Museumsführerin", "Kultur"),
    ("Die Rechnung", "Restaurant", "ein Kellner", "Essen und Trinken"),
    ("Der Streit", "Treppenhaus, abends", "Nachbarin Frau Schulz", "Konflikte"),
    ("Die Entschuldigung", "vor Frau Schulz' Tür", "Nachbarin Frau Schulz", "Konflikte"),
    ("Das Vorstellungsgespräch", "Konferenzraum", "Personalchefin Frau Richter", "Arbeit"),
    ("Der Vertrag", "Büro der Personalabteilung", "Frau Richter", "Arbeit"),
    ("Die Versicherung", "Versicherungsbüro", "ein Berater", "Behörden"),
    ("Der Zahnarzt", "Zahnarztpraxis", "eine Zahnärztin", "Gesundheit"),
    ("Die Bibliothek", "Stadtbibliothek", "eine Bibliothekarin", "Bildung"),
    ("Der Flohmarkt", "Mauerpark", "ein Händler", "Freizeit"),
    ("Das Konzert", "Konzerthalle, Pause", "eine Besucherin", "Kultur"),
    ("Die Wohnungssuche", "Besichtigung in Kreuzberg", "eine Maklerin", "Wohnen"),
    ("Der Mietvertrag", "Maklerbüro", "die Maklerin", "Wohnen"),
    ("Das Internet", "Wohnung, Telefon", "eine Hotline-Mitarbeiterin", "Technik"),
    ("Die Steuererklärung", "Steuerberatungsbüro", "ein Steuerberater", "Geld"),
    ("Der Urlaub", "Reisebüro", "eine Reiseberaterin", "Reisen"),
    ("Die Familie", "Videoanruf nach Indien", "die Mutter des Lernenden", "Familie"),
    ("Das Heimweh", "Küche, spät abends", "Kollegin Anna", "Gefühle"),
    ("Das Fest", "Straßenfest", "ein Nachbar", "Kultur"),
    ("Der Sprachtest", "Prüfungszentrum, Anmeldung", "eine Mitarbeiterin", "Bildung"),
    ("Die Kündigung", "Büro", "Chef Herr Brandt", "Arbeit"),
    ("Die neue Stelle", "Startup in Kreuzberg", "Gründerin Frau Neumann", "Arbeit"),
    ("Der Kollege", "Kaffeeküche", "Kollege Jonas", "Arbeit"),
    ("Das Projekt", "Besprechungsraum", "das Team", "Arbeit"),
    ("Die Präsentation", "Konferenzraum", "Frau Neumann", "Arbeit"),
    ("Der Unfall", "Straßenkreuzung", "ein Ersthelfer", "Gesundheit"),
    ("Die Polizei", "Polizeiwache", "ein Polizist", "Behörden"),
    ("Der Umweltschutz", "Recyclinghof", "ein Mitarbeiter", "Umwelt"),
    ("Die Debatte", "Kneipe", "Freund Tobias", "Meinungen"),
    ("Die Zeitung", "Kiosk", "der Kioskbesitzer", "Medien"),
    ("Das Fernsehen", "Wohnzimmer", "Mitbewohner Paul", "Medien"),
    ("Der Plan", "Café am Kanal", "Kollegin Anna", "Zukunft"),
    ("Die Prüfungsangst", "Volkshochschule", "Lehrerin Frau Bauer", "Gefühle"),
    ("Die Wiederholung", "Bibliothek", "Lernpartnerin Sofia", "Bildung"),
    ("Der Tag davor", "Wohnung, Abend", "Kollegin Anna", "Zukunft"),
]

# A reply that is mostly ASCII words and has no German function words is the
# learner asking to be translated rather than corrected.
GERMAN_MARKERS = re.compile(
    r"\b(ich|du|er|sie|es|wir|ihr|der|die|das|ein|eine|und|ist|sind|nicht|"
    r"mit|für|auf|zu|von|habe|hat|bin|kann|möchte|nach|aus|im|am|bei|"
    r"sehr|auch|noch|schon|wie|was|wo|wann|warum|guten|danke|bitte)\b", re.I)
ENGLISH_MARKERS = re.compile(
    r"\b(i|you|he|she|it|we|they|the|a|an|and|is|are|not|with|for|on|to|"
    r"from|have|has|am|can|would|want|my|your|what|where|when|why|how|"
    r"please|thanks|hello|sorry)\b", re.I)


def detect_language(text: str) -> str:
    """'de' if he attempted German, 'en' if he wants it translated.

    Counting marker words beats any single heuristic here because the two
    languages share so much short vocabulary — "was", "hat", "die", "in", "so"
    are all valid in both — and a beginner's German is full of English anyway.
    Ties go to English, because translating German by mistake shows him a
    correct sentence, while correcting English produces nonsense.
    """
    german = len(GERMAN_MARKERS.findall(text))
    english = len(ENGLISH_MARKERS.findall(text))
    if not re.search(r"[a-zA-ZäöüßÄÖÜ]", text):
        return "en"
    return "de" if german > english else "en"


@dataclass
class Turn:
    speaker: str
    de: str
    en: str = ""
    correction: str = ""
    learner_input: str = ""
    input_lang: str = ""
    ord: int = 0
    provenance: str = "template"
    validated: bool = False
    new_words: list[str] = field(default_factory=list)


@dataclass
class Day:
    id: int
    day_number: int
    title: str
    setting_de: str
    setting_en: str
    npc_name: str
    npc_role: str
    theme: str
    status: str
    turns: list[Turn] = field(default_factory=list)

    @property
    def learner_turns(self) -> int:
        return sum(1 for t in self.turns if t.speaker == "learner")

    @property
    def complete(self) -> bool:
        return self.learner_turns >= TURNS_PER_DAY


def _scene(day_number: int) -> tuple[str, str, str, str]:
    return ARC[(day_number - 1) % len(ARC)]


def revisit_days(day_number: int) -> list[int]:
    """Earlier days whose vocabulary is due to reappear today."""
    return [day_number - offset for offset in REVIEW_OFFSETS
            if day_number - offset >= 1]


def recall_targets(conn: sqlite3.Connection, day_number: int,
                   limit: int = 4) -> list[sqlite3.Row]:
    """Words from earlier episodes that today's conversation should walk past.

    Ordered by how long ago they were met, oldest first, because the oldest is
    the closest to being lost.
    """
    days = revisit_days(day_number)
    if not days:
        return []
    placeholders = ",".join("?" * len(days))
    return conn.execute(
        f"""SELECT i.id AS item_id, 'text' AS modality, 1 AS rung, NULL AS due_at,
                   i.lemma, i.gender, i.pos, i.sense_gloss_en, d.day_number,
                   d.title AS from_scene
            FROM story_vocab v
            JOIN story_day d ON d.id = v.day_id
            JOIN item i      ON i.id = v.item_id
            WHERE d.day_number IN ({placeholders})
              AND i.sense_gloss_en NOT LIKE '[%'
            ORDER BY d.day_number ASC, random()
            LIMIT ?""", (*days, limit)).fetchall()


def _targets(conn: sqlite3.Connection, limit: int = 8,
             day_number: int | None = None) -> list[sqlite3.Row]:
    """Today's word list: what FSRS says is due, plus what the story owes back.

    Blending the two is the point. FSRS alone would drill the right words with
    no context; the story alone would introduce new vocabulary and never return
    to it. Together, a word is met in a scene, drilled on a card, and then met
    again in a later scene at an expanding interval.
    """
    reviews, fresh = scheduler.plan(conn)
    rows = list(reviews) + list(fresh)
    glossed = [r for r in rows if not r["sense_gloss_en"].startswith("[")]
    due = (glossed or rows)[:limit]

    if day_number is None:
        return due
    recalled = recall_targets(conn, day_number)
    seen = {r["item_id"] for r in due}
    return due[: max(0, limit - len(recalled))] + [
        r for r in recalled if r["item_id"] not in seen]


def link_vocabulary(conn: sqlite3.Connection, day_id: int, german: str) -> int:
    """Record which items a turn used, so the day can be revisited later.

    Written on every turn rather than computed on demand: the transcript is
    append-only and the link is what later days read, so paying once at write
    time is right. Deliberately tolerant — a word that cannot be resolved is
    skipped, not guessed at.
    """
    from jana import wordlookup

    index = wordlookup.build_index(conn)
    ids: set[int] = set()
    for token in wordlookup.WORD.findall(german):
        entry = wordlookup.lookup(conn, token, index)
        if not entry.found or not entry.gloss:
            continue
        row = conn.execute("SELECT id FROM item WHERE lemma = ? LIMIT 1",
                           (entry.lemma,)).fetchone()
        if row:
            ids.add(int(row["id"]))
    for item_id in ids:
        conn.execute(
            "INSERT OR IGNORE INTO story_vocab (day_id, item_id, first_seen)"
            " VALUES (?, ?, ?)", (day_id, item_id, events.now()))
    conn.commit()
    return len(ids)


def _vocab_line(rows: list[sqlite3.Row]) -> str:
    parts = []
    for row in rows:
        if row["sense_gloss_en"].startswith("["):
            continue
        surface = f"{(row['gender'] + ' ') if row['gender'] else ''}{row['lemma']}"
        try:
            scene = row["from_scene"]
        except (IndexError, KeyError):
            scene = None
        recall = f" [he met this in '{scene}' — bring it back]" if scene else ""
        parts.append(f"{surface} ({row['sense_gloss_en']}){recall}")
    return ", ".join(parts)


def _generate(conn: sqlite3.Connection, system: str, user: str,
              keys_with_german: tuple[str, ...]) -> tuple[dict | None, str, list[str]]:
    """Generate, then refuse anything that leaves the syllabus. Retry, then give up."""
    lex = lexicon.build(conn)
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]
    for _ in range(MAX_RETRIES + 1):
        reply = llm.authored(messages, temperature=0.75, max_tokens=500)
        if not reply.ok:
            break
        match = re.search(r"\{.*\}", reply.text, re.S)
        if not match:
            messages.append({"role": "user", "content": "JSON only."})
            continue
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            messages.append({"role": "user", "content": "Valid JSON only."})
            continue
        german = " ".join(str(parsed.get(k, "")) for k in keys_with_german)
        report = lexicon.check(german, lex)
        if report.ok:
            return parsed, reply.tier, report.new_words
        messages.append({"role": "user", "content":
                         f"Too advanced: {', '.join(report.unknown[:6])}. "
                         "Rewrite with simple A1-A2 words only."})
    return None, "none", []


NPC_SYSTEM = """You are writing dialogue for a German learning story.

The learner is an Indian software engineer who has moved to Germany and is
preparing for the Goethe B1 exam. He is a beginner. You play ONE character who
speaks to him in German.

Rules, all absolute:
- Speak ONLY German in the "de" field. No English there, not even in brackets.
- Simple A1-A2 vocabulary. Sentences of at most 12 words.
- Say one or two sentences, then ask him ONE question so he must reply.
- Stay in character and in the scene. Be a real person, warm and a bit funny.
- Never break character to explain grammar."""

NPC_CONTRACT = """Reply with JSON only:
{"de": "<what your character says, German only>",
 "en": "<the same, in natural English>"}"""

RENDER_SYSTEM = """You are the learner's own voice in a German conversation.

He will give you, in English, what he wants to say. Render it as the German HE
would say in this scene — natural, simple, A1-A2 vocabulary, short. This is what
his character says out loud, so it must sound like a person, not a textbook.

Do not answer him. Do not add anything he did not mean. Just say his sentence
in German."""

RENDER_CONTRACT = """Reply with JSON only:
{"de": "<his sentence in German>",
 "note": "<one short English tip about a word or ending you chose, or \\"\\">"}"""


def open_day(conn: sqlite3.Connection, day_number: int | None = None) -> Day:
    """Today's episode, created with its opening line if it does not exist."""
    row = conn.execute(
        "SELECT * FROM story_day WHERE status = 'open' ORDER BY day_number DESC"
        " LIMIT 1").fetchone() if day_number is None else conn.execute(
        "SELECT * FROM story_day WHERE day_number = ?", (day_number,)).fetchone()

    if row is None:
        highest = conn.execute(
            "SELECT coalesce(max(day_number), 0) FROM story_day").fetchone()[0]
        number = day_number or highest + 1
        title, setting, npc, theme = _scene(number)
        targets = _targets(conn, day_number=number)
        cur = conn.execute(
            """INSERT INTO story_day (day_number, date, title, setting_de,
                   setting_en, npc_name, npc_role, theme, target_ids,
                   status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)""",
            (number, date_type.today().isoformat(), title, setting, setting,
             npc, npc, theme,
             json.dumps([r["item_id"] for r in targets]), events.now()))
        conn.commit()
        row = conn.execute("SELECT * FROM story_day WHERE id = ?",
                           (cur.lastrowid,)).fetchone()

    day = _load(conn, row)
    if not day.turns:
        opening = _npc_turn(conn, day, [])
        _save_turn(conn, day.id, 1, opening)
        day = _load(conn, conn.execute("SELECT * FROM story_day WHERE id = ?",
                                       (day.id,)).fetchone())
    return day


def _load(conn: sqlite3.Connection, row: sqlite3.Row) -> Day:
    turns = [Turn(speaker=t["speaker"], de=t["de"], en=t["en"] or "",
                  correction=t["correction"] or "",
                  learner_input=t["learner_input"] or "",
                  input_lang=t["input_lang"] or "", ord=t["ord"],
                  provenance=t["provenance"] or "", validated=bool(t["validated"]))
             for t in conn.execute(
                 "SELECT * FROM story_turn WHERE day_id = ? ORDER BY ord",
                 (row["id"],))]
    return Day(id=row["id"], day_number=row["day_number"], title=row["title"],
               setting_de=row["setting_de"], setting_en=row["setting_en"],
               npc_name=row["npc_name"], npc_role=row["npc_role"],
               theme=row["theme"], status=row["status"], turns=turns)


def _save_turn(conn: sqlite3.Connection, day_id: int, ord_: int, turn: Turn) -> None:
    cursor = conn.execute(
        """INSERT INTO story_turn (day_id, ord, speaker, de, en, learner_input,
               input_lang, correction, provenance, validated, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (day_id, ord_, turn.speaker, turn.de, turn.en, turn.learner_input,
         turn.input_lang, turn.correction, turn.provenance,
         int(turn.validated), events.now()))
    conn.commit()
    link_vocabulary(conn, day_id, turn.de)
    _remember(conn, int(cursor.lastrowid), turn)


def _remember(conn: sqlite3.Connection, turn_id: int, turn: Turn) -> None:
    """Embed the turn so it is searchable by meaning, for good.

    Done inline rather than in a nightly batch because the point of the store is
    that nothing is ever lost — a turn that only becomes searchable tomorrow is
    a turn that is missing today. It costs about 40 ms on a request that already
    takes a second, and it is wrapped so that a failing embedder degrades
    search rather than breaking the conversation.
    """
    try:
        from jana import memory

        vectors = memory.embed([f"{turn.de} — {turn.en}".strip(" —")])
        if not vectors:
            return
        conn.execute(
            """INSERT OR REPLACE INTO embedding
               (kind, ref_id, text, model, dim, vector, created_at)
               VALUES ('story_turn', ?, ?, ?, ?, ?, datetime('now'))""",
            (turn_id, turn.de, memory.EMBED_MODEL, len(vectors[0]),
             vectors[0].tobytes()))
        conn.commit()
    except Exception:
        pass


def _history(day: Day, limit: int = 10) -> str:
    lines = []
    for turn in day.turns[-limit:]:
        who = day.npc_name if turn.speaker == "npc" else "Er"
        lines.append(f"{who}: {turn.de}")
    return "\n".join(lines)


def _npc_turn(conn: sqlite3.Connection, day: Day, targets: list[sqlite3.Row]) -> Turn:
    vocab = _vocab_line(targets)
    system = (f"{NPC_SYSTEM}\n\nScene: {day.setting_de}\n"
              f"Your character: {day.npc_name}\nTopic: {day.theme}\n"
              + (f"Work these words in naturally if you can: {vocab}\n" if vocab else "")
              + f"\n{NPC_CONTRACT}")
    user = (f"So far:\n{_history(day)}\n\nSay your next line."
            if day.turns else "Open the scene. Greet him and ask him something.")
    parsed, tier, new_words = _generate(conn, system, user, ("de",))
    if parsed is None:
        return Turn("npc", "Entschuldigung, wie bitte?", "Sorry, what was that?",
                    provenance="template", validated=True)
    return Turn("npc", str(parsed.get("de", "")), str(parsed.get("en", "")),
                provenance=tier, validated=True, new_words=new_words)


def _render_learner(conn: sqlite3.Connection, day: Day, english: str) -> Turn:
    """He said it in English; his character says it in German."""
    system = (f"{RENDER_SYSTEM}\n\nScene: {day.setting_de}\n"
              f"He is speaking to: {day.npc_name}\n\n{RENDER_CONTRACT}")
    parsed, tier, new_words = _generate(
        conn, system, f'He wants to say: "{english}"', ("de",))
    if parsed is None:
        return Turn("learner", english, english, learner_input=english,
                    input_lang="en", provenance="template",
                    correction="Could not render that — try shorter English.")
    return Turn("learner", str(parsed.get("de", "")), english,
                correction=str(parsed.get("note", "")), learner_input=english,
                input_lang="en", provenance=tier, validated=True,
                new_words=new_words)


def _check_learner(conn: sqlite3.Connection, german: str) -> Turn:
    """He attempted German; correct it and name the rule."""
    result = explain.correct(german)
    corrected = result.get("corrected") or german
    errors = result.get("errors") or []
    note = " · ".join(
        f"{e.get('wrong')} → {e.get('right')} ({e.get('rule')})" for e in errors[:2])
    return Turn("learner", corrected, "", correction=note,
                learner_input=german, input_lang="de",
                provenance=result.get("tier", "local"), validated=True)


def speak(conn: sqlite3.Connection, text: str, session_id: int | None = None,
          day_number: int | None = None) -> dict[str, Any]:
    """The learner's half of an exchange, and nothing else.

    Split from the reply on purpose. Doing both in one request meant two model
    calls before anything reached the screen — four or five seconds where the
    learner had spoken and the scene showed nothing, and then his line and the
    answer appeared together. Conversation does not work like that, and neither
    does an animation that has to know who is talking.

    Now his sentence lands as soon as it exists, the scene can play it, and the
    other character is fetched separately while it does.
    """
    day = open_day(conn, day_number)
    language = detect_language(text)
    learner = (_check_learner(conn, text) if language == "de"
               else _render_learner(conn, day, text))

    _save_turn(conn, day.id, len(day.turns) + 1, learner)
    _log_production(conn, session_id, _targets(conn, day_number=day.day_number),
                    learner)

    return {
        "turn": learner.__dict__, "day_number": day.day_number,
        "turns_used": day.learner_turns + 1, "turns_target": TURNS_PER_DAY,
    }


def reply(conn: sqlite3.Connection,
          day_number: int | None = None) -> dict[str, Any]:
    """The other character's answer. Fetched after the learner's line is on screen."""
    day = open_day(conn, day_number)
    npc = _npc_turn(conn, day, _targets(conn, day_number=day.day_number))
    _save_turn(conn, day.id, len(day.turns) + 1, npc)

    return {
        "turn": npc.__dict__, "day_number": day.day_number,
        "turns_used": day.learner_turns, "turns_target": TURNS_PER_DAY,
        "complete": day.complete,
    }


def say(conn: sqlite3.Connection, text: str, session_id: int | None = None,
        day_number: int | None = None) -> dict[str, Any]:
    """Both halves in one call. Kept for scripts and tests; the UI uses the split."""
    spoken = speak(conn, text, session_id, day_number)
    answered = reply(conn, day_number)
    return {
        "learner": spoken["turn"], "npc": answered["turn"],
        "turns_used": answered["turns_used"], "turns_target": TURNS_PER_DAY,
        "complete": answered["complete"], "day_number": answered["day_number"],
    }


def _log_production(conn: sqlite3.Connection, session_id: int | None,
                    targets: list[sqlite3.Row], turn: Turn) -> None:
    """Credit target words the learner's own sentence used.

    Rung 3 when he wrote the German himself; rung 2 when he wrote English and
    read the German back. Both are real exposure, and they are not worth the
    same, so they are not recorded as if they were.
    """
    if not targets:
        return
    lex = lexicon.build(conn)
    from jana.tutor import detect_targets
    used = detect_targets(turn.de, targets, lex)
    rung = 3 if turn.input_lang == "de" else 2
    for row in targets:
        if row["lemma"] not in used:
            continue
        events.append(conn, "attempt", {
            "session_id": session_id, "item_id": row["item_id"],
            "modality": row["modality"], "rung": rung,
            "task_type": f"story_{turn.input_lang or 'en'}",
            "exercise_id": None, "response": turn.de[:200],
            "correct": not turn.correction, "grade": events.GOOD,
            "latency_ms": None,
        })


def day_vocabulary(conn: sqlite3.Connection, day_number: int) -> list[dict]:
    """Every word used in a day's conversation, with its entry.

    This is the join that makes the other modules context-anchored: Lesen,
    Hören, Schreiben and Sprechen for a given day draw from here rather than
    from the global item pool, so the learner meets the same words again in a
    different task while the scene is still vivid.
    """
    rows = conn.execute(
        """SELECT t.de FROM story_turn t JOIN story_day d ON d.id = t.day_id
           WHERE d.day_number = ?""", (day_number,)).fetchall()
    seen: dict[str, dict] = {}
    for row in rows:
        for word in explain.literal(conn, row["de"]):
            if word["found"] and word["lemma"] not in seen:
                seen[word["lemma"]] = word
    return list(seen.values())


def transcript(conn: sqlite3.Connection, day_number: int) -> str:
    day_row = conn.execute("SELECT id FROM story_day WHERE day_number = ?",
                           (day_number,)).fetchone()
    if day_row is None:
        return ""
    return "\n".join(
        r["de"] for r in conn.execute(
            "SELECT de FROM story_turn WHERE day_id = ? ORDER BY ord",
            (day_row["id"],)))
