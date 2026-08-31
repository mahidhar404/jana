"""Paths to the learner's corpus. Nothing here is written to; read-only."""

from __future__ import annotations

import os
from pathlib import Path

CORPUS_ROOT = Path(os.environ.get(
    "JANA_CORPUS", Path.home() / "Desktop" / "German"
))

# The one course carrying human-authored, sentence-aligned, gender-annotated
# German (see the Phase 0 plan, Finding 2).
LESSON_PDF_GLOB = "German Course 1*/**/*Translation-and-Grammar.pdf"

# Goethe vocabulary lists with answer keys — one DE-EN pair per <p> block.
# Matched by normalized substring, not by glob: macOS stores filenames in NFD,
# so a literal "Lösungen" (NFC) in a glob pattern matches nothing.
GLOSS_NAME_SUBSTRINGS = ("losungen", "vokabeln")


def fold(name: str) -> str:
    """Normalize a filename for matching: NFC, casefold, umlauts stripped."""
    import unicodedata
    n = unicodedata.normalize("NFD", name).casefold()
    return "".join(c for c in n if not unicodedata.combining(c))
