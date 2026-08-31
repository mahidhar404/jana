"""Grading. Deterministic first; an LLM only for the residual question.

The split matters (arch doc §4). Most answers are decidable by string
comparison, and code that decides them is free, instant, and identical every
time it runs. An LLM is needed for exactly one question — *is this an acceptable
alternative to the reference?* — and Phase 0 does not answer that question at
all: it records the disagreement instead. Every time the learner overrides a
verdict, that override is logged, and that log becomes the eval set the LLM
grader must later pass. Building the eval set before the component is the whole
point of doing it in this order.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

UMLAUT_FOLD = str.maketrans({"ä": "a", "ö": "o", "ü": "u", "ß": "s"})
ARTICLES = ("der", "die", "das")


@dataclass(frozen=True)
class Verdict:
    correct: bool
    expected: str
    note: str = ""


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text).strip().casefold()
    text = re.sub(r"[^\w\s/-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _fold(text: str) -> str:
    """Umlaut-insensitive form, for tolerating keyboard limitations."""
    text = normalize(text)
    text = text.replace("ae", "a").replace("oe", "o").replace("ue", "u")
    return text.translate(UMLAUT_FOLD)


def _alternatives(reference: str) -> set[str]:
    """A gloss like "clock /watch" or "form/sheet/bow" lists synonyms."""
    parts = re.split(r"[/,;]| or ", reference)
    return {normalize(p) for p in parts if normalize(p)}


def grade_choice(response: str, reference: str) -> Verdict:
    return Verdict(normalize(response) == normalize(reference), reference)


def grade_article(response: str, reference: str) -> Verdict:
    got = normalize(response)
    if got == normalize(reference):
        return Verdict(True, reference)
    note = "" if got in ARTICLES else "answer with der, die or das"
    return Verdict(False, reference, note)


def grade_written(response: str, reference: str) -> Verdict:
    """Typed free text against a reference, with the tolerances that are safe.

    Umlaut folding is a deliberate concession: a learner who types `Woerterbuch`
    knows the word, and failing them teaches keyboard layout rather than German.
    It is flagged in the note so the spelling is still corrected.
    """
    got, want = normalize(response), normalize(reference)
    if not got:
        return Verdict(False, reference, "no answer")
    if got == want or got in _alternatives(reference):
        return Verdict(True, reference)
    if _fold(got) == _fold(want):
        return Verdict(True, reference, f"umlauts: {reference}")
    if _fold(got) in {_fold(a) for a in _alternatives(reference)}:
        return Verdict(True, reference, f"umlauts: {reference}")
    if _distance(got, want) == 1:
        return Verdict(False, reference, "one letter off")
    return Verdict(False, reference)


def _distance(a: str, b: str) -> int:
    """Levenshtein, used only to say *how* wrong an answer was."""
    if abs(len(a) - len(b)) > 2:
        return 99
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def grade_conjugation(response: str, reference: str) -> Verdict:
    """Grade a single principal-part answer (e.g. 'bog ab', 'ist abgebogen').

    Tolerates reversed separable prefixes ('ab bog' == 'bog ab') and umlaut
    folding, since the learner knows the form — the keyboard just disagrees.
    """
    got, want = normalize(response), normalize(reference)
    if not got:
        return Verdict(False, reference, "no answer")
    if got == want:
        return Verdict(True, reference)
    # Try reversed word order for separable verbs: 'ab bog' → 'bog ab'
    got_parts = got.split()
    want_parts = want.split()
    if len(got_parts) == len(want_parts) == 2 and got_parts[::-1] == want_parts:
        return Verdict(True, reference, "word order")
    if _fold(got) == _fold(want):
        return Verdict(True, reference, f"umlauts: {reference}")
    if _distance(got, want) == 1:
        return Verdict(False, reference, "one letter off")
    return Verdict(False, reference)


GRADERS = {
    "recognise": grade_choice,
    "article": grade_article,
    "cued_recall": grade_written,
    "production": grade_written,
    "conjugation": grade_conjugation,
}


def grade(task_type: str, response: str, reference: str) -> Verdict:
    return GRADERS[task_type](response, reference)
