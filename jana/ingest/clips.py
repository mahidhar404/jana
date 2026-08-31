"""Turn video into a searchable library of real German utterances.

    uv run python -m jana.ingest.clips --exam        # 76 min of Goethe audio
    uv run python -m jana.ingest.clips --courses --limit 40

For each source: extract 16 kHz mono audio, transcribe it with whisper forced to
German, keep the segments that look like real utterances, cut each to its own
mp3, and index the text so a sentence can find the recording that says it.

Resumable by design. Transcribing hours of video is a long job that will be
interrupted, so a source already present in `clip` is skipped and the work
directory is cleaned as it goes rather than at the end.
"""

from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path

from jana import audio
from jana.config import CORPUS_ROOT
from jana.db import connect, init_db


def sources_exam() -> list[Path]:
    return sorted(Path("data/raw/audio").glob("*.mp4"))


def sources_courses(limit: int | None) -> list[Path]:
    """Videos from the courses measured as densely German-spoken."""
    found: list[Path] = []
    for course in audio.GERMAN_DENSE:
        root = CORPUS_ROOT / course
        if not root.is_dir():
            continue
        found.extend(sorted(root.rglob("*.mp4")))
    return found[:limit] if limit else found


def already_done(conn, source: str) -> bool:
    return conn.execute("SELECT 1 FROM clip WHERE source = ? LIMIT 1",
                        (source,)).fetchone() is not None


def process(conn, video: Path, threads: int) -> int:
    source = str(video.name)
    if already_done(conn, source):
        return 0

    audio.WORK_DIR.mkdir(parents=True, exist_ok=True)
    wav = audio.WORK_DIR / f"{abs(hash(source)) % 10**10}.wav"
    if not audio.to_wav(video, wav):
        return 0

    try:
        segments = audio.transcribe(wav, threads=threads)
        clips = audio.segments_to_clips(segments, source)
        written = 0
        for index, clip in enumerate(clips):
            name = f"{abs(hash(source)) % 10**10}_{index:04d}.mp3"
            destination = audio.CLIP_DIR / name
            if not audio.cut(wav, clip, destination):
                continue
            audio.save(conn, clip, str(destination))
            written += 1
        return written
    finally:
        wav.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exam", action="store_true",
                        help="the downloaded Goethe listening material")
    parser.add_argument("--courses", action="store_true",
                        help="the German-dense courses in the corpus")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--threads", type=int, default=8)
    args = parser.parse_args()

    state = audio.ready()
    missing = [name for name, ok in state.items() if not ok]
    if missing:
        raise SystemExit(f"not ready: {', '.join(missing)} missing")

    conn = connect()
    init_db(conn)

    videos: list[Path] = []
    if args.exam:
        videos += sources_exam()
    if args.courses:
        videos += sources_courses(args.limit)
    if not videos:
        raise SystemExit("nothing to do — pass --exam and/or --courses")

    print(f"{len(videos)} source files")
    started = time.perf_counter()
    total = 0
    for index, video in enumerate(videos, 1):
        elapsed = time.perf_counter() - started
        written = process(conn, video, args.threads)
        total += written
        print(f"  [{index}/{len(videos)}] +{written:4d} clips  "
              f"({total} total, {elapsed / 60:.1f} min elapsed)  {video.name[:52]}",
              flush=True)

    shutil.rmtree(audio.WORK_DIR, ignore_errors=True)
    summary = audio.stats(conn)
    print(f"\n{summary['clips']} clips · {summary['minutes']} minutes of real "
          f"German · {time.perf_counter() - started:.0f}s")


if __name__ == "__main__":
    main()
