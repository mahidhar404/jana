"""Vocabulary validation for model-authored German.

The problem this solves
-----------------------
D4 forbids local models from authoring German the learner learns from, because
a beginner cannot detect a subtle error until roughly B1 — exactly too late.
But a conversational tutor has to produce German, and a tutor that can only
recite canned sentences is a quiz wearing a costume.

The resolution is not to trust the model. It is to *check* it. Every German
sentence a model produces is tokenised and every content word looked up in a
permitted lexicon built from the Goethe Wortliste and the learner's own corpus.
Out-of-syllabus vocabulary is caught deterministically, before the learner sees
it, and the generator is asked again.

What this does and does not catch
---------------------------------
It catches *vocabulary* outside the syllabus: the model reaching for a C1 word,
or inventing one. It does **not** catch grammar. "Der Hund ist gross" passes —
every word is known — while the case, the spelling of `groß`, and the article
could all be wrong.

So this is a filter, not a proof, and the system treats it as one:
  * the UI labels every model-authored sentence with its provenance, so the
    learner always knows whether he is reading Goethe's German or a model's;
  * grammar-sensitive material still routes to the remote tier;
  * the pass rate is recorded per model, which turns the one-off model bake-off
    into a metric that runs continuously in production.

That last point is the part worth internalising: an eval you run once is a
snapshot, an eval wired into the request path is a monitor.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

WORD = re.compile(r"[A-Za-zÄÖÜäöüß]+(?:-[A-Za-zÄÖÜäöüß]+)*")

# Closed-class German: articles, pronouns, prepositions, conjunctions, auxiliaries,
# and the highest-frequency adverbs. These are structural rather than lexical, so
# they are always permitted regardless of the learner's level.
FUNCTION_WORDS = set("""
der die das den dem des ein eine einen einem einer eines kein keine keinen keinem
keiner keines ich du er sie es wir ihr mich dich sich uns euch mir dir ihm ihn ihnen
mein meine meinen meinem meiner dein deine seinen seine sein ihre ihren unser euer
und oder aber denn sondern doch also dass weil wenn als ob damit obwohl während
bevor nachdem bis seit sobald falls indem sodass zwar
in an auf über unter vor hinter neben zwischen bei mit nach zu von aus seit
im am ins ans zum zur beim vom aufs fürs durchs ums übers unters hinters
gegenüber durch für gegen ohne um entlang trotz statt wegen innerhalb
bin bist ist sind seid war warst waren wart gewesen
habe hast hat haben habt hatte hattest hatten hattet gehabt
werde wirst wird werden werdet wurde wurden geworden worden
kann kannst können könnt konnte konnten gekonnt könnte könnten
muss musst müssen müsst musste mussten gemusst müsste
will willst wollen wollt wollte wollten gewollt
soll sollst sollen sollt sollte sollten darf darfst dürfen dürft durfte durften
mag magst mögen mögt mochte möchte möchten möchtest
nicht nichts kein nie niemals immer oft manchmal selten schon noch nur auch
sehr ganz mehr weniger viel viele wenig etwas alles jeder jede jedes alle
hier dort da wo wann warum wie was wer wen wem wessen welche welcher welches
heute morgen gestern jetzt dann danach vorher später bald gleich
ja nein bitte danke okay
ist's gibt es gibt
""".split())

# Endings German inflection adds to a lemma. Stripping these is crude, but it is
# a *permissive* crudeness — it can only let a real word through, never invent one.
SUFFIXES = ("ern", "est", "end", "en", "em", "er", "es", "st", "te", "et",
            "n", "e", "s", "t", "r", "m")

# German inflection changes the stem vowel as well as the ending
# (laufen -> läuft, Buch -> Bücher). Folding the umlaut before lookup turns a
# stem change back into a plain suffix strip.
UMLAUT_FOLD = str.maketrans({"ä": "a", "ö": "o", "ü": "u"})


# How many out-of-syllabus words a passage may contain and still be shown.
#
# Zero would be the obvious choice and it is the wrong one. A single unfamiliar
# word, understood from surrounding context, is precisely Krashen's i+1 — it is
# how vocabulary is actually acquired, and refusing it means the learner only
# ever meets words he already knows. So one is allowed *and surfaced*: the UI
# labels it as new and shows what it means, turning a validation miss into the
# most valuable moment in the passage.
#
# Two or more is a different thing: the passage stops being comprehensible
# input and becomes noise, so it is regenerated.
TOLERATED_NEW_WORDS = 1


@dataclass(frozen=True)
class Report:
    ok: bool
    unknown: list[str]
    coverage: float
    proper_nouns: list[str]

    @property
    def new_words(self) -> list[str]:
        """Out-of-syllabus words the learner should be shown, not hidden from."""
        return self.unknown if len(self.unknown) <= TOLERATED_NEW_WORDS else []

    @property
    def summary(self) -> str:
        if not self.unknown:
            return "all vocabulary in syllabus"
        if self.ok:
            return "one new word: " + self.unknown[0]
        return "outside syllabus: " + ", ".join(self.unknown[:6])


def _fold(word: str) -> str:
    """One normalisation, used on both sides of every lookup.

    `casefold` expands ß to ss, so `groß` written into the lexicon raw will
    never match `größer` folded on the way in. Everything is folded once, at
    the boundary, and compared folded — the usual fix for a mismatch that only
    shows up on a handful of words.
    """
    return unicodedata.normalize("NFC", word).casefold()


@lru_cache(maxsize=1)
def _cached_lexicon_key() -> None:
    return None


def build(conn: sqlite3.Connection, max_level: str = "B1") -> frozenset[str]:
    """Every word form the learner is allowed to meet, at or below `max_level`."""
    levels = {"A1": ("A1",), "A2": ("A1", "A2"), "B1": ("A1", "A2", "B1")}
    allowed = levels.get(max_level, levels["B1"])
    placeholders = ",".join("?" * len(allowed))
    rows = conn.execute(
        f"""SELECT lemma FROM item
            WHERE cefr_level IN ({placeholders}) OR cefr_level IS NULL""",
        allowed).fetchall()

    lexicon: set[str] = {_fold(w) for w in FUNCTION_WORDS}
    lexicon |= _syllabus_tokens(conn, max_level)
    for (lemma,) in rows:
        base = _fold(lemma)
        lexicon.add(base)
        # Separable verbs appear split in a main clause: "steht ... auf".
        for part in base.split():
            lexicon.add(part)
    return frozenset(lexicon)


def _stem_is_known(stem: str, lexicon: frozenset[str]) -> bool:
    """A stem counts as known if any plausible lemma built from it is."""
    if len(stem) < 3:
        return False
    candidates = (stem, stem + "e", stem + "en", stem + "n")
    if any(c in lexicon for c in candidates):
        return True
    # Retry with the umlaut folded out: läuf- -> lauf- -> laufen.
    plain = stem.translate(UMLAUT_FOLD)
    if plain == stem:
        return False
    return any(c in lexicon for c in (plain, plain + "e", plain + "en", plain + "n"))


def _syllabus_tokens(conn: sqlite3.Connection, max_level: str = "B1") -> set[str]:
    """Every word attested anywhere in the sentence bank and item glosses.

    The structured parser only recognises nouns and verbs, so adjectives and
    adverbs — `groß`, `schnell`, `gern` — were absent from the lexicon and made
    correct German fail validation. Anything attested in a human-authored source
    is by definition inside the syllabus, so it is admitted verbatim.
    """
    levels = {"A1": ("A1",), "A2": ("A1", "A2"), "B1": ("A1", "A2", "B1")}
    allowed = levels.get(max_level, levels["B1"])
    placeholders = ",".join("?" * len(allowed))
    tokens = {_fold(row[0]) for row in conn.execute(
        f"SELECT token FROM syllabus_token WHERE cefr_level IN ({placeholders})",
        allowed)}
    for (text,) in conn.execute("SELECT de FROM sentence"):
        tokens.update(_fold(w) for w in WORD.findall(text))
    return tokens


def _known(word: str, lexicon: frozenset[str]) -> bool:
    folded = _fold(word)
    if folded in lexicon or folded.translate(UMLAUT_FOLD) in lexicon:
        return True
    # Inflected form of a known lemma.
    for suffix in SUFFIXES:
        if folded.endswith(suffix) and _stem_is_known(folded[: -len(suffix)], lexicon):
            return True
    # Past participle: ge-...-t / ge-...-en
    if folded.startswith("ge") and len(folded) > 5:
        inner = folded[2:]
        for suffix in ("t", "en", "et"):
            if inner.endswith(suffix) and _stem_is_known(inner[: -len(suffix)], lexicon):
                return True
    # Verbs in -ieren form their participle without ge-: telefoniert.
    if folded.endswith("iert") and _stem_is_known(folded[:-1] + "en", lexicon):
        return True
    # Compounds: German builds them freely, and both halves being known is the
    # best signal available without a morphological analyser.
    if len(folded) > 7:
        for split in range(4, len(folded) - 3):
            if folded[:split] in lexicon and folded[split:] in lexicon:
                return True
    return False


def check(text: str, lexicon: frozenset[str]) -> Report:
    words = WORD.findall(text)
    if not words:
        return Report(True, [], 1.0, [])

    unknown: list[str] = []
    proper: list[str] = []
    for index, word in enumerate(words):
        if _known(word, lexicon):
            continue
        # A capitalised word mid-sentence that is not a known noun is most
        # likely a name. Names are not syllabus violations.
        if word[0].isupper() and index > 0:
            proper.append(word)
            continue
        unknown.append(word)

    distinct = sorted(set(unknown))
    coverage = 1 - len(unknown) / len(words)
    return Report(len(distinct) <= TOLERATED_NEW_WORDS, distinct,
                  round(coverage, 3), sorted(set(proper)))


COMMON_GLOSSES: dict[str, tuple[str, str, str]] = {
    # Pronouns & Articles
    "ich": ("ich", "pronoun", "I (subject / Nominativ)"),
    "du": ("du", "pronoun", "you (informal singular / Nominativ)"),
    "er": ("er", "pronoun", "he (Nominativ)"),
    "sie": ("sie", "pronoun", "she / they / you (formal)"),
    "es": ("es", "pronoun", "it (Nominativ/Akkusativ)"),
    "wir": ("wir", "pronoun", "we (Nominativ)"),
    "ihr": ("ihr", "pronoun", "you all (informal plural) / to her"),
    "mich": ("ich", "pronoun", "me (Akkusativ)"),
    "dich": ("du", "pronoun", "you (Akkusativ)"),
    "ihn": ("er", "pronoun", "him (Akkusativ)"),
    "mir": ("ich", "pronoun", "to me (Dativ)"),
    "dir": ("du", "pronoun", "to you (Dativ)"),
    "ihm": ("er/es", "pronoun", "to him / to it (Dativ)"),
    "uns": ("wir", "pronoun", "us (Akkusativ/Dativ)"),
    "euch": ("ihr", "pronoun", "you all (Akkusativ/Dativ)"),
    "ihnen": ("sie", "pronoun", "to them (Dativ)"),
    "Ihnen": ("Sie", "pronoun", "to you formal (Dativ)"),
    "mein": ("mein", "possessive", "my"),
    "meine": ("mein", "possessive", "my (feminine/plural)"),
    "meinem": ("mein", "possessive", "my (Dativ masculine/neuter)"),
    "meinen": ("mein", "possessive", "my (Akkusativ masculine / Dativ plural)"),
    "dein": ("dein", "possessive", "your"),
    "sein": ("sein", "possessive / verb", "his / to be"),
    "ihre": ("ihr", "possessive", "her / their / your (formal)"),
    "der": ("der", "article", "the (masculine Nominativ / feminine Dativ)"),
    "die": ("die", "article", "the (feminine / plural Nominativ & Akkusativ)"),
    "das": ("das", "article", "the (neuter Nominativ & Akkusativ)"),
    "den": ("der", "article", "the (masculine Akkusativ / plural Dativ)"),
    "dem": ("der/das", "article", "the (masculine/neuter Dativ)"),
    "des": ("der/das", "article", "of the (Genitiv)"),
    "ein": ("ein", "article", "a / an (masculine/neuter Nominativ)"),
    "eine": ("eine", "article", "a / an (feminine Nominativ/Akkusativ)"),
    "einen": ("ein", "article", "a / an (masculine Akkusativ)"),
    "einem": ("ein", "article", "a / an (masculine/neuter Dativ)"),
    "einer": ("ein", "article", "a / an (feminine Dativ)"),
    "kein": ("kein", "determiner", "no / not a (negation)"),
    "keine": ("kein", "determiner", "no / not a (feminine/plural)"),
    # Prepositions
    "mit": ("mit", "preposition", "with (always + Dativ)"),
    "nach": ("nach", "preposition", "to (cities/countries) / after (always + Dativ)"),
    "zu": ("zu", "preposition / adv", "to / closed / too (always + Dativ)"),
    "von": ("von", "preposition", "from / of (always + Dativ)"),
    "aus": ("aus", "preposition", "from / out of (always + Dativ)"),
    "bei": ("bei", "preposition", "at / with / near (always + Dativ)"),
    "seit": ("seit", "preposition", "since / for (time) (always + Dativ)"),
    "in": ("in", "preposition", "in / into (+ Dativ location, + Akkusativ direction)"),
    "an": ("an", "preposition", "at / on (vertical) (+ Dativ/Akkusativ)"),
    "auf": ("auf", "preposition", "on (horizontal) / upon (+ Dativ/Akkusativ)"),
    "unter": ("unter", "preposition", "under / below (+ Dativ/Akkusativ)"),
    "über": ("über", "preposition", "over / above / about (+ Dativ/Akkusativ)"),
    "vor": ("vor", "preposition", "in front of / before (+ Dativ/Akkusativ)"),
    "hinter": ("hinter", "preposition", "behind (+ Dativ/Akkusativ)"),
    "neben": ("neben", "preposition", "next to (+ Dativ/Akkusativ)"),
    "zwischen": ("zwischen", "preposition", "between (+ Dativ/Akkusativ)"),
    "für": ("für", "preposition", "for (always + Akkusativ)"),
    "durch": ("durch", "preposition", "through (always + Akkusativ)"),
    "ohne": ("ohne", "preposition", "without (always + Akkusativ)"),
    "um": ("um", "preposition", "around / at (time) (always + Akkusativ)"),
    "gegen": ("gegen", "preposition", "against / around (time) (always + Akkusativ)"),
    # Conjunctions & Adverbs
    "und": ("und", "conjunction", "and"),
    "oder": ("oder", "conjunction", "or"),
    "aber": ("aber", "conjunction", "but"),
    "denn": ("denn", "conjunction", "because / for"),
    "weil": ("weil", "conjunction", "because (subordinating: verb goes to end)"),
    "dass": ("dass", "conjunction", "that (subordinating: verb goes to end)"),
    "wenn": ("wenn", "conjunction", "if / when (subordinating: verb goes to end)"),
    "als": ("als", "conjunction", "when (past) / than (comparison) / as"),
    "ob": ("ob", "conjunction", "whether / if"),
    "obwohl": ("obwohl", "conjunction", "although / even though"),
    "damit": ("damit", "conjunction", "so that / in order that"),
    "nicht": ("nicht", "adverb", "not"),
    "sehr": ("sehr", "adverb", "very"),
    "auch": ("auch", "adverb", "also / too"),
    "noch": ("noch", "adverb", "still / yet"),
    "schon": ("schon", "adverb", "already"),
    "oft": ("oft", "adverb", "often"),
    "immer": ("immer", "adverb", "always"),
    "nie": ("nie", "adverb", "never"),
    "heute": ("heute", "adverb", "today"),
    "morgen": ("morgen", "adverb", "tomorrow"),
    "gestern": ("gestern", "adverb", "yesterday"),
    "hier": ("hier", "adverb", "here"),
    "da": ("da", "adverb", "there / because"),
    "dort": ("dort", "adverb", "there"),
    "wie": ("wie", "question word", "how / like / as"),
    "was": ("was", "question word", "what"),
    "wer": ("wer", "question word", "who"),
    "wo": ("wo", "question word", "where"),
    "wohin": ("wohin", "question word", "where to"),
    "woher": ("woher", "question word", "where from"),
    "wann": ("wann", "question word", "when"),
    "warum": ("warum", "question word", "why"),
    # Common verbs
    "ist": ("sein", "verb", "is (3rd person singular present of sein)"),
    "sind": ("sein", "verb", "are (plural present of sein)"),
    "bin": ("sein", "verb", "am (1st person singular present of sein)"),
    "bist": ("sein", "verb", "are (2nd person singular present of sein)"),
    "war": ("sein", "verb", "was (past tense / Präteritum of sein)"),
    "hat": ("haben", "verb", "has (3rd person singular present of haben)"),
    "habe": ("haben", "verb", "have (1st person singular present of haben)"),
    "hast": ("haben", "verb", "have (2nd person singular present of haben)"),
    "hatte": ("haben", "verb", "had (past tense / Präteritum of haben)"),
    "kann": ("können", "modal verb", "can / able to (können)"),
    "muss": ("müssen", "modal verb", "must / have to (müssen)"),
    "will": ("wollen", "modal verb", "want to (wollen)"),
    "soll": ("sollen", "modal verb", "should / supposed to (sollen)"),
    "darf": ("dürfen", "modal verb", "may / allowed to (dürfen)"),
    "möchte": ("mögen", "modal verb", "would like to (möchten)"),
}


def lookup_token(conn: sqlite3.Connection, token: str) -> dict[str, Any]:
    """Word lookup, in the dict shape this module's callers expect.

    The implementation lives in jana/wordlookup.py. There were briefly two
    lookups in this codebase — this one, and a second with morphological
    ranking — and on a shared test set they scored 5/8 and 8/8. The failures
    were the dangerous kind: `gefahren` (a form of *fahren*, to drive) resolved
    to `Gefahr`, danger, and `Häuser` never reduced to `Haus` at all. A learner
    who cannot yet read German cannot catch a confident wrong answer.

    Rather than keep two, this is now an adapter. One behaviour, one place to
    fix it, and every existing caller keeps its response shape.
    """
    from jana import wordlookup

    entry = wordlookup.lookup(conn, token)
    cefr = conn.execute("SELECT cefr_level FROM item WHERE lemma = ? LIMIT 1",
                        (entry.lemma,)).fetchone()
    return {
        "cefr": cefr[0] if cefr else None,
        "word": entry.surface,
        "lemma": entry.lemma,
        "gender": entry.gender,
        "pos": entry.pos,
        "en": entry.gloss or entry.display,
        "plural": entry.plural,
        "principal_parts": entry.principal_parts,
        "note": entry.note,
        "found": entry.found and bool(entry.gloss),
    }

