"""Resolve any German word form the learner clicks back to a dictionary entry.

The hard part is that a learner clicks what he *sees*, and what he sees is
inflected: `fährst`, `Häuser`, `gefahren`, `größten`. None of those are in the
item table — `fahren`, `Haus`, `groß` are. So this walks the same morphology the
lexicon validator uses, in reverse, and returns the entry a dictionary would.

Deterministic and local. A model is asked only when nothing matches, because
inventing a plausible gloss for a word we do not have is worse than admitting
we do not have it — and the miss list is a useful record of what the syllabus
is missing.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from jana import lexicon

WORD = re.compile(r"[A-Za-zÄÖÜäöüß]+(?:-[A-Za-zÄÖÜäöüß]+)*")

# Endings peeled off, longest first, so `größten` loses `ten` before `n`.
STRIP = ("esten", "sten", "eren", "ern", "est", "ten", "en", "em",
         "er", "es", "st", "te", "et", "n", "e", "s", "t", "r", "m")

# Candidate rankings. Higher wins.
#
# Ranking exists because stripping endings blindly finds the wrong word far more
# often than it finds none at all: `fährst` reduces to `fähr`, and `fähre` (the
# ferry) is a real noun sitting right there, so a first-match lookup confidently
# answers "ferry" for a form of `fahren`. Likewise `gefahren` -> `Gefahr`
# (danger) and `größten` -> `Größe` (size). Every one of those is a plausible
# lie, which is the worst kind for a learner who cannot yet tell.
#
# So each derivation records how it was reached, and part-of-speech agreement
# decides: a `-st` ending is verb morphology, `ge-...-en` is a participle, and a
# capitalised surface is a noun. The reading that agrees with the evidence wins.
EXACT, PARTICIPLE, VERB_FORM, NOUN_FORM, ADJ_FORM, LOOSE = 100, 80, 70, 60, 50, 10

VERB_ENDINGS = ("st", "t", "en", "e", "te", "ten", "est", "et")
ADJ_ENDINGS = ("esten", "sten", "ste", "er", "es", "em", "en", "e")

POS_LABEL = {"noun": "noun", "verb": "verb", "other": "adj / adv"}

# Closed-class words, glossed by hand.
#
# These never appear in the Wortliste as entries and never will — they are
# structure, not vocabulary. But a word-for-word translation is mostly made of
# them, and a breakdown that renders "Ich fahre mit dem Zug" as "· · · · Zug"
# is useless. The set is finite and stable, so writing it out once is both
# cheaper and more accurate than any generated alternative, and it is
# human-authored, which keeps the literal view free of model output entirely.
FUNCTION_GLOSS: dict[str, tuple[str, str]] = {
    # articles and determiners — case is the interesting part for a learner
    "der": ("the", "nom. masc. / dat. fem."), "die": ("the", "fem. / plural"),
    "das": ("the", "neuter"), "den": ("the", "acc. masc. / dat. pl."),
    "dem": ("the", "dative"), "des": ("the", "genitive"),
    "ein": ("a", "masc./neut."), "eine": ("a", "feminine"),
    "einen": ("a", "accusative masc."), "einem": ("a", "dative"),
    "einer": ("a", "dat./gen. fem."), "eines": ("a", "genitive"),
    "kein": ("no / not a", ""), "keine": ("no / not a", ""),
    "keinen": ("no / not a", "accusative"), "keinem": ("no / not a", "dative"),
    # pronouns
    "ich": ("I", ""), "du": ("you", "informal sg."), "er": ("he", ""),
    "sie": ("she / they / you", "formal when capitalised"), "es": ("it", ""),
    "wir": ("we", ""), "ihr": ("you", "plural informal"),
    "mich": ("me", "accusative"), "mir": ("me / to me", "dative"),
    "dich": ("you", "accusative"), "dir": ("you / to you", "dative"),
    "sich": ("himself / herself / itself", "reflexive"),
    "uns": ("us", ""), "euch": ("you all", ""), "ihm": ("him / to him", "dative"),
    "ihn": ("him", "accusative"), "ihnen": ("them / to them", "dative"),
    "mein": ("my", ""), "meine": ("my", ""), "meiner": ("my", "dat. fem."),
    "meinen": ("my", "accusative"), "meinem": ("my", "dative"),
    "dein": ("your", ""), "deine": ("your", ""), "sein": ("his / its", ""),
    "seine": ("his", ""), "seinen": ("his", "accusative"),
    "ihre": ("her / their", ""), "ihren": ("her / their", "accusative"),
    "unser": ("our", ""), "euer": ("your", "plural"),
    # conjunctions
    "und": ("and", ""), "oder": ("or", ""), "aber": ("but", ""),
    "denn": ("because", "main clause"), "sondern": ("but rather", ""),
    "dass": ("that", "sends verb to the end"),
    "weil": ("because", "sends verb to the end"),
    "wenn": ("if / when", "sends verb to the end"),
    "als": ("when / than", ""), "ob": ("whether", ""), "damit": ("so that", ""),
    "obwohl": ("although", ""), "während": ("while / during", ""),
    "bevor": ("before", ""), "nachdem": ("after", ""), "falls": ("in case", ""),
    "also": ("so / therefore", ""), "doch": ("but / yes-it-is", "no English equivalent"),
    # prepositions
    "in": ("in / into", ""), "an": ("at / on", ""), "auf": ("on / onto", ""),
    "über": ("over / about", ""), "unter": ("under", ""), "vor": ("before / in front of", ""),
    "hinter": ("behind", ""), "neben": ("next to", ""), "zwischen": ("between", ""),
    "bei": ("at / with", "always dative"), "mit": ("with", "always dative"),
    "nach": ("to / after", "always dative"), "zu": ("to", "always dative"),
    "von": ("from / of", "always dative"), "aus": ("out of / from", "always dative"),
    "seit": ("since / for", "always dative"), "durch": ("through", "always accusative"),
    "für": ("for", "always accusative"), "gegen": ("against", "always accusative"),
    "ohne": ("without", "always accusative"), "um": ("around / at", "always accusative"),
    "wegen": ("because of", "genitive"), "trotz": ("despite", "genitive"),
    "im": ("in the", "in + dem"), "am": ("at the / on the", "an + dem"),
    "ins": ("into the", "in + das"), "ans": ("to the", "an + das"),
    "zum": ("to the", "zu + dem"), "zur": ("to the", "zu + der"),
    "beim": ("at the", "bei + dem"), "vom": ("from the", "von + dem"),
    # verbs, auxiliary and modal
    "bin": ("am", "sein"), "bist": ("are", "sein"), "ist": ("is", "sein"),
    "sind": ("are", "sein"), "seid": ("are", "sein"), "war": ("was", "sein"),
    "waren": ("were", "sein"), "gewesen": ("been", "sein"),
    "habe": ("have", "haben"), "hast": ("have", "haben"), "hat": ("has", "haben"),
    "haben": ("have", ""), "habt": ("have", "haben"), "hatte": ("had", "haben"),
    "hatten": ("had", "haben"), "gehabt": ("had", "past participle"),
    "werde": ("will / become", "werden"), "wird": ("will / becomes", "werden"),
    "werden": ("will / become", ""), "wurde": ("became / was", "passive"),
    "worden": ("been", "passive"),
    "kann": ("can", "können"), "kannst": ("can", "können"), "können": ("can", ""),
    "könnte": ("could", "Konjunktiv II"), "muss": ("must", "müssen"),
    "musst": ("must", "müssen"), "müssen": ("must", ""), "musste": ("had to", ""),
    "will": ("want", "wollen — NOT 'will'"), "willst": ("want", "wollen"),
    "wollen": ("want", ""), "wollte": ("wanted", ""),
    "soll": ("should", "sollen"), "sollte": ("should", "Konjunktiv II"),
    "darf": ("may / am allowed", "dürfen"), "dürfen": ("may", ""),
    "mag": ("like", "mögen"), "möchte": ("would like", "Konjunktiv II of mögen"),
    "möchten": ("would like", ""), "möchtest": ("would like", ""),
    # negation, quantity, adverbs
    "nicht": ("not", ""), "nichts": ("nothing", ""), "nie": ("never", ""),
    "immer": ("always", ""), "oft": ("often", ""), "manchmal": ("sometimes", ""),
    "selten": ("rarely", ""), "schon": ("already", ""), "noch": ("still / yet", ""),
    "nur": ("only", ""), "auch": ("also", ""), "sehr": ("very", ""),
    "ganz": ("quite / whole", ""), "mehr": ("more", ""), "weniger": ("less", ""),
    "viel": ("much", ""), "viele": ("many", ""), "wenig": ("little", ""),
    "etwas": ("something / a bit", ""), "alles": ("everything", ""),
    "alle": ("all", ""), "jeder": ("every / each", ""),
    # question words and place/time
    "hier": ("here", ""), "dort": ("there", ""), "da": ("there / since", ""),
    "wo": ("where", ""), "wann": ("when", ""), "warum": ("why", ""),
    "wie": ("how", ""), "was": ("what", ""), "wer": ("who", ""),
    "wen": ("whom", "accusative"), "wem": ("to whom", "dative"),
    "welche": ("which", ""), "welcher": ("which", ""), "welches": ("which", ""),
    "heute": ("today", ""), "morgen": ("tomorrow / morning", ""),
    "gestern": ("yesterday", ""), "jetzt": ("now", ""), "dann": ("then", ""),
    "später": ("later", ""), "bald": ("soon", ""), "gleich": ("right away", ""),
    "ja": ("yes", ""), "nein": ("no", ""), "bitte": ("please", ""),
    "danke": ("thank you", ""), "gern": ("gladly", "shows you like doing it"),
}


def expand_plural(lemma: str, notation: str | None) -> str | None:
    """Turn the Wortliste's plural notation into the actual word.

    Goethe writes plurals as suffixes with a diaeresis mark for the stem change:
    `Pass, ¨-e` means *Pässe*, `Haus, ¨-er` means *Häuser*, `Lösung, -en` means
    *Lösungen*. Showing "die ¨-e" to a learner is showing him the notation
    instead of the German, which is the opposite of the job.
    """
    if not notation:
        return None
    notation = notation.strip()
    if notation in {"-", "", "Pl."}:
        return lemma
    # Some rows already hold the finished plural rather than a suffix. Concatenating
    # those produced "HundHunde"; a notation is a suffix only if it says so.
    if not notation.startswith(("-", "¨")):
        return notation
    stem = lemma
    if notation.startswith("¨"):
        # Umlaut the last back vowel in the stem: a→ä, o→ö, u→ü (au→äu).
        for source, target in (("au", "äu"), ("a", "ä"), ("o", "ö"), ("u", "ü")):
            index = stem.rfind(source)
            if index != -1:
                stem = stem[:index] + target + stem[index + len(source):]
                break
        notation = notation[1:]
    return stem + notation.lstrip("-") if notation.startswith("-") else stem + notation


@dataclass(frozen=True)
class Entry:
    surface: str
    lemma: str
    gloss: str
    pos: str
    gender: str | None = None
    plural: str | None = None
    principal_parts: str | None = None
    note: str = ""
    found: bool = True

    @property
    def display(self) -> str:
        return f"{self.gender} {self.lemma}" if self.gender else self.lemma


def build_index(conn: sqlite3.Connection) -> dict[str, dict]:
    """lemma (folded) -> row. Built once per request; the table is small."""
    index: dict[str, dict] = {}
    for row in conn.execute(
            "SELECT lemma, sense_gloss_en, pos, gender, plural, principal_parts"
            " FROM item"):
        index.setdefault(row["lemma"].casefold(), dict(row))
    return index


def _stems(word: str) -> list[str]:
    """A stem and its umlaut-folded twin: `fähr` also yields `fahr`."""
    plain = word.translate(lexicon.UMLAUT_FOLD)
    return [word] if plain == word else [word, plain]


def _candidates(word: str) -> list[tuple[str, int, str]]:
    """(lemma candidate, rank, expected part of speech) for one surface form."""
    folded = word.casefold()
    out: list[tuple[str, int, str]] = [(folded, EXACT, "")]
    for stem in _stems(folded):
        if stem != folded:
            out.append((stem, EXACT - 1, ""))

    # Participle, checked first: ge- is unambiguous verb morphology.
    if folded.startswith("ge") and len(folded) > 5:
        inner = folded[2:]
        for suffix in ("en", "et", "t"):
            if not inner.endswith(suffix):
                continue
            for stem in _stems(inner[: -len(suffix)]):
                out += [(stem + "en", PARTICIPLE, "verb"),
                        (stem + "n", PARTICIPLE - 1, "verb")]
        for stem in _stems(inner):
            out.append((stem, PARTICIPLE - 2, "verb"))

    for suffix in STRIP:
        if not folded.endswith(suffix) or len(folded) - len(suffix) < 3:
            continue
        body = folded[: -len(suffix)]
        verbish = suffix in VERB_ENDINGS
        adjish = suffix in ADJ_ENDINGS
        for stem in _stems(body):
            if verbish:
                out += [(stem + "en", VERB_FORM, "verb"),
                        (stem + "n", VERB_FORM - 1, "verb")]
            if adjish:
                out.append((stem, ADJ_FORM, "other"))
            out += [(stem, NOUN_FORM if word[:1].isupper() else LOOSE, "noun"),
                    (stem + "e", NOUN_FORM - 1 if word[:1].isupper() else LOOSE,
                     "noun")]
    return out


def lookup(conn: sqlite3.Connection, word: str,
           index: dict[str, dict] | None = None) -> Entry:
    index = index if index is not None else build_index(conn)
    clean = word.strip(".,!?;:„“\"'()")
    if not clean:
        return Entry(word, word, "", "", found=False)

    best: tuple[int, str, dict] | None = None
    # Closed-class words are structure, not vocabulary; answer them from the
    # hand-written table before touching the item index.
    fixed = FUNCTION_GLOSS.get(clean.casefold())
    if fixed is not None:
        gloss, note = fixed
        return Entry(surface=clean, lemma=clean.casefold(), gloss=gloss,
                     pos="function", note=note)

    for candidate, rank, expect_pos in _candidates(clean):
        row = index.get(candidate)
        if row is None:
            continue
        # Agreement between the morphology and the entry's part of speech is the
        # signal that separates `fahren` from `Fähre`.
        score = rank + (25 if expect_pos and row["pos"] == expect_pos else 0)
        score -= 30 if expect_pos and row["pos"] != expect_pos else 0
        if best is None or score > best[0]:
            best = (score, candidate, row)

    if best is not None:
        _, candidate, row = best
        gloss = row["sense_gloss_en"]
        return Entry(
            surface=clean, lemma=row["lemma"],
            gloss="" if gloss.startswith("[") else gloss,
            pos=POS_LABEL.get(row["pos"], row["pos"] or ""),
            gender=row["gender"],
            plural=expand_plural(row["lemma"], row["plural"]),
            principal_parts=row["principal_parts"],
            note="" if candidate == clean.casefold() else f"form of {row['lemma']}")

    known = clean.casefold() in lexicon.build(conn)
    return Entry(clean, clean, "", "", found=False,
                 note="in syllabus, no entry yet" if known else "not in B1 syllabus")


def annotate(conn: sqlite3.Connection, text: str) -> list[dict]:
    """Every word in a sentence, with its dictionary entry. Powers word-by-word."""
    index = build_index(conn)
    out = []
    for token in WORD.findall(text):
        entry = lookup(conn, token, index)
        out.append({
            "surface": token, "lemma": entry.lemma, "gloss": entry.gloss,
            "pos": entry.pos, "gender": entry.gender, "plural": entry.plural,
            "principal_parts": entry.principal_parts,
            "note": entry.note, "found": entry.found and bool(entry.gloss),
        })
    return out
