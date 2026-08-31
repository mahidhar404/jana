"""Text extraction from the PDFs in this project's corpus.

Deliberately not a general PDF library. Every PDF we need — the Goethe
Wortliste, the Modellsätze, the lesson sheets — stores its text as
FlateDecode'd content streams with plain `Tj`/`TJ` show operators, so ~40 lines
of stdlib does the job and the project keeps a zero-dependency ingest path.

The escapes it repairs are the ones these files actually contain, found by
inspection: octal escapes, backspace artefacts (`A\\bend`), and the intra-word
spaces PDF kerning leaves behind (`Diens tag`, `s tündlich`).
"""

from __future__ import annotations

import re
import unicodedata
import zlib
from pathlib import Path

_SHOW_OP = re.compile(rb"\[((?:[^\[\]\\]|\\.)*)\]\s*TJ|\((?:\\.|[^()\\])*\)\s*Tj")
_LITERAL = re.compile(rb"\((?:\\.|[^()\\])*\)")
_OCTAL = re.compile(r"\\([0-7]{1,3})")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def raw_text(path: Path) -> str:
    """Concatenate every text-showing operator in the file, in stream order."""
    data = path.read_bytes()
    chunks: list[bytes] = []
    for match in re.finditer(rb"stream\r?\n", data):
        start = match.end()
        end = data.find(b"endstream", start)
        if end < 0:
            continue
        try:
            stream = zlib.decompress(data[start:end])
        except zlib.error:
            continue
        if b"Tj" not in stream and b"TJ" not in stream:
            continue
        for op in _SHOW_OP.finditer(stream):
            literals = _LITERAL.findall(op.group(0))
            chunks.append(b"".join(lit[1:-1] for lit in literals))
    return b" ".join(chunks).decode("latin-1", "replace")


def clean(text: str) -> str:
    text = _OCTAL.sub(lambda m: chr(int(m.group(1), 8)), text)
    text = text.replace("\\(", "(").replace("\\)", ")").replace("\\\\", "\\")
    text = _CONTROL.sub("", text)
    return unicodedata.normalize("NFC", re.sub(r"[ \t]+", " ", text))


def extract(path: Path) -> str:
    return clean(raw_text(path))


def repair_kerning(text: str, vocabulary: set[str]) -> str:
    """Rejoin words PDF kerning split in two (`Diens tag` -> `Dienstag`).

    Only joins when the result is a word we already know, so it cannot invent
    German that was never in the source — which is the same D4 discipline the
    rest of the pipeline follows.
    """
    tokens = text.split(" ")
    out: list[str] = []
    i = 0
    while i < len(tokens):
        if i + 1 < len(tokens):
            joined = tokens[i] + tokens[i + 1]
            if joined in vocabulary and tokens[i] not in vocabulary:
                out.append(joined)
                i += 2
                continue
        out.append(tokens[i])
        i += 1
    return " ".join(out)
