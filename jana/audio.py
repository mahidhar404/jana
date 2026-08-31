"""Real recorded German: transcribe it, index it, serve it instead of synthesis.

The problem this solves
-----------------------
Hören has been read aloud by the operating system's German voice. That voice is
a synthesiser: it will say any sentence, and it will say all of them with the
same flat prosody, the same speaker, and the same speed. The exam plays
announcements, dialogues and discussions spoken by different people at natural
pace, and no amount of pitch-shifting one synthetic voice prepares a learner for
that.

Meanwhile the corpus already contains real humans speaking German — measured at
roughly 25-40 dense hours across eight courses, plus 76 minutes of genuine
Goethe exam listening material. None of it was reachable, because the only
transcripts were English machine-ASR run over German audio, which is garbage
(see the corpus notes). There was no index: no way to know which four seconds of
which file contain a given sentence.

Whisper with `-l de` builds that index. It produces German text *with
timestamps*, which turns 25 hours of opaque video into a searchable library of
real utterances.

The asymmetry worth naming: a TTS model is a permanent approximation that never
improves. Alignment is a one-time cost that unlocks material already downloaded,
and recorded speech is strictly better than anything synthesis produces — real
stress, real intonation, many voices, real listening conditions.

What this does not solve
------------------------
Only sentences that were actually spoken exist as recordings. Jana generates
fresh German constantly, and for that there is no clip. So the runtime is a
hybrid: look for a real recording first, fall back to synthesis when there is
none. `find_clip()` is the lookup; the fallback lives in the browser.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path

WHISPER_HOME = Path.home() / ".cache" / "jana" / "whisper.cpp"
WHISPER_BIN_CANDIDATES = (
    WHISPER_HOME / "build" / "bin" / "whisper-cli",
    WHISPER_HOME / "build" / "bin" / "main",
)
MODEL = WHISPER_HOME / "models" / "ggml-large-v3-turbo.bin"

CLIP_DIR = Path("data/clips")
WORK_DIR = Path("data/work")

# Whisper wants 16 kHz mono PCM; giving it anything else means it resamples
# internally and we lose control over the conversion.
SAMPLE_RATE = 16_000

# Courses measured as densely German-spoken. The proportion of non-English
# tokens in the corpus's own English-ASR subtitles is a usable proxy: English
# speech transcribes cleanly, German speech comes out as phonetic junk.
GERMAN_DENSE = (
    "business-german-esther-hartwig",
    "read german like a native",
    "german-for-job-interviews",
    "Best Way to Learn German Language_ Advancing Beginner (A2.2)",
    "Best Way to Learn German Language_ Beginner Level 2 (A1.2)",
    "german pronunciation",
    "Best Way to Learn German Language_ Full Beginner (A1.1)",
    "perfect your german",
)

# A usable listening clip is a whole utterance: long enough to carry prosody,
# short enough to hold in working memory and repeat back.
MIN_CLIP_S = 1.4
MAX_CLIP_S = 14.0
MIN_WORDS = 3


@dataclass(frozen=True)
class Clip:
    source: str
    start_s: float
    end_s: float
    text: str
    path: str | None = None


def whisper_bin() -> Path | None:
    for candidate in WHISPER_BIN_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def ready() -> dict[str, bool]:
    return {
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "whisper": whisper_bin() is not None,
        "model": MODEL.exists(),
    }


# --------------------------------------------------------------- extraction
def to_wav(source: Path, destination: Path) -> bool:
    """Extract 16 kHz mono PCM. Returns False rather than raising on bad input."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(source),
         "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le",
         str(destination)],
        capture_output=True)
    return result.returncode == 0 and destination.exists()


def transcribe(wav: Path, threads: int = 8) -> list[dict]:
    """German transcription with timestamps. Returns [] if anything goes wrong."""
    binary = whisper_bin()
    if binary is None or not MODEL.exists():
        return []
    stem = wav.with_suffix("")
    result = subprocess.run(
        [str(binary), "-m", str(MODEL), "-f", str(wav),
         "-l", "de",                 # the whole point: German, not auto-detect
         "-oj", "-of", str(stem),
         # No -nt: that flag suppresses timestamps and makes whisper emit
         # coarse 30-second chunks instead of utterances. The timestamps are
         # the entire reason for running this.
         "-t", str(threads), "-np"],
        capture_output=True)
    output = stem.with_suffix(".json")
    if result.returncode != 0 or not output.exists():
        return []
    try:
        data = json.loads(output.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    finally:
        output.unlink(missing_ok=True)
    return data.get("transcription", [])


def _seconds(offsets: dict) -> tuple[float, float]:
    return offsets.get("from", 0) / 1000.0, offsets.get("to", 0) / 1000.0


GERMAN_HINT = re.compile(
    r"\b(der|die|das|und|ist|nicht|ich|sie|wir|ein|eine|mit|für|auf|zu|von|"
    r"haben|hat|sind|war|kann|wird|dem|den|im|am|sich|aber|auch|noch|schon|"
    r"wenn|dass|weil|man|es|er|ihr|mir|mich|sehr|hier|jetzt)\b", re.I)

# The German-dense courses are dense, not pure — the instructor still explains
# in English, and an English sentence that *quotes* German passed a
# "contains one German word" test. Clips like
#   something "ohne Angabe von Gründen". So that means "without giving...
# are English with a German fragment in them, and putting one in a dictation
# exercise asks the learner to transcribe the wrong language.
ENGLISH_HINT = re.compile(
    r"\b(the|and|is|of|to|that|this|you|we|it|for|with|so|means|word|"
    r"because|about|would|there|here|what|when|which|they|have|has|"
    r"english|german|sentence|example|means)\b", re.I)


def looks_german(text: str) -> bool:
    """Predominantly German, not merely containing German.

    Compares marker counts rather than looking for any single signal, because
    the two languages share too many short words for one test to separate them.
    """
    german = len(GERMAN_HINT.findall(text))
    english = len(ENGLISH_HINT.findall(text))
    if german == 0:
        return False
    return german > english


def segments_to_clips(segments: list[dict], source: str) -> list[Clip]:
    """Keep whole utterances that actually look like German.

    Whisper forced into German will hallucinate German-shaped text over English
    or silence, so a segment must contain real function words to be kept. That
    filter is cheap and removes most of the damage.
    """
    clips: list[Clip] = []
    for segment in segments:
        text = (segment.get("text") or "").strip()
        start, end = _seconds(segment.get("offsets", {}))
        if not text or end <= start:
            continue
        if not (MIN_CLIP_S <= end - start <= MAX_CLIP_S):
            continue
        if len(text.split()) < MIN_WORDS:
            continue
        if not looks_german(text):
            continue
        clips.append(Clip(source=source, start_s=start, end_s=end, text=text))
    return clips


def cut(wav: Path, clip: Clip, destination: Path) -> bool:
    """Cut one utterance out of the extracted audio, as mp3 for the browser."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-y",
         "-ss", f"{clip.start_s:.2f}", "-to", f"{clip.end_s:.2f}",
         "-i", str(wav), "-ac", "1", "-ar", "22050", "-b:a", "64k",
         str(destination)],
        capture_output=True)
    return result.returncode == 0 and destination.exists()


# ------------------------------------------------------------------ storage
def save(conn: sqlite3.Connection, clip: Clip, path: str) -> int:
    cursor = conn.execute(
        """INSERT OR IGNORE INTO clip
               (source, start_s, end_s, text, path, created_at)
           VALUES (?, ?, ?, ?, ?, datetime('now'))""",
        (clip.source, clip.start_s, clip.end_s, clip.text, path))
    conn.commit()
    if cursor.rowcount:
        clip_id = int(cursor.lastrowid)
        _embed(conn, clip_id, clip.text)
        return clip_id
    row = conn.execute("SELECT id FROM clip WHERE path = ?", (path,)).fetchone()
    return int(row["id"]) if row else 0


def _embed(conn: sqlite3.Connection, clip_id: int, text: str) -> None:
    """Index the utterance so a sentence can find the recording that says it."""
    try:
        from jana import memory

        vectors = memory.embed([text])
        if not vectors:
            return
        conn.execute(
            """INSERT OR REPLACE INTO embedding
                   (kind, ref_id, text, model, dim, vector, created_at)
               VALUES ('clip', ?, ?, ?, ?, ?, datetime('now'))""",
            (clip_id, text[:400], memory.EMBED_MODEL, len(vectors[0]),
             vectors[0].tobytes()))
        conn.commit()
    except Exception:
        pass


# Where the threshold comes from, measured against this corpus rather than
# guessed: an exact match scores 1.000, while a semantically *related* sentence
# tops out around 0.87. The gap is wide and empty, so the bar sits inside it.
#
# 0.72 was the first guess and it was wrong in the dangerous direction. Asking
# for "Wo ist die Touristeninformation?" returned a clip saying
# "...bei vielen touristischen Attraktionen weniger bezahlen" at 0.834 — a real
# human saying something else. The learner is told to write down what he hears
# and hears a different sentence, which is worse than a robot reading the right
# one. When in doubt, fall back to synthesis.
CLIP_MATCH_THRESHOLD = 0.95


def find_clip(conn: sqlite3.Connection, sentence: str,
              threshold: float = CLIP_MATCH_THRESHOLD) -> dict | None:
    """The recording of this sentence, if one exists. Not a similar one."""
    try:
        from jana import memory

        hits = memory.search(conn, sentence, kinds=("clip",), limit=1)
    except Exception:
        return None
    if not hits or hits[0].score < threshold:
        return None
    row = conn.execute("SELECT * FROM clip WHERE id = ?",
                       (hits[0].ref_id,)).fetchone()
    if row is None:
        return None
    return {"clip_id": row["id"], "text": row["text"], "path": row["path"],
            "source": row["source"], "score": hits[0].score,
            "seconds": round(row["end_s"] - row["start_s"], 1)}


def random_clip(conn: sqlite3.Connection, max_seconds: float = 12.0) -> dict | None:
    row = conn.execute(
        "SELECT * FROM clip WHERE (end_s - start_s) <= ? ORDER BY random() LIMIT 1",
        (max_seconds,)).fetchone()
    if row is None:
        return None
    return {"clip_id": row["id"], "text": row["text"], "path": row["path"],
            "source": row["source"], "seconds": round(row["end_s"] - row["start_s"], 1)}


def prune(conn: sqlite3.Connection) -> int:
    """Drop stored clips that fail the stricter language test.

    Cheaper than re-transcribing: the expensive step is whisper, and the filter
    runs on text we already have. Removes the cut file too, so the disk does
    not keep audio nothing references.
    """
    removed = 0
    for row in conn.execute("SELECT id, text, path FROM clip").fetchall():
        if looks_german(row["text"]):
            continue
        Path(row["path"]).unlink(missing_ok=True)
        conn.execute("DELETE FROM embedding WHERE kind='clip' AND ref_id = ?",
                     (row["id"],))
        conn.execute("DELETE FROM clip WHERE id = ?", (row["id"],))
        removed += 1
    conn.commit()
    return removed


def stats(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT count(*) n, coalesce(sum(end_s - start_s), 0) s FROM clip"
    ).fetchone()
    by_source = conn.execute(
        """SELECT source, count(*) n FROM clip
           GROUP BY source ORDER BY 2 DESC LIMIT 12""").fetchall()
    return {
        "clips": row["n"],
        "minutes": round(row["s"] / 60.0, 1),
        "ready": ready(),
        "by_source": [{"source": r["source"], "clips": r["n"]} for r in by_source],
    }
