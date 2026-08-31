"""Schema and connection handling.

Two invariants this module exists to protect (arch doc D2):

1. `event` is append-only. Triggers below make UPDATE/DELETE raise, so the
   source of truth cannot be corrupted by a stray query.
2. `item_state` and `confusion_edge` are *derived*. They may be dropped and
   rebuilt from `event` alone at any time. Only jana/project.py writes them.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

# Overridable so tests (and a second profile) never touch the real history.
DB_PATH = Path(os.environ.get(
    "JANA_DB", Path(__file__).resolve().parent.parent / "data" / "jana.db"))

# Exam date. The scheduler becomes deadline-aware at the Phase 2 gate; until
# then this is recorded but unused.
EXAM_DATE = "2027-01-07"

SCHEMA = """
-- ---------------------------------------------------------------- source of truth
CREATE TABLE IF NOT EXISTS event (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT    NOT NULL,
    kind         TEXT    NOT NULL,
    payload_json TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS event_kind_idx ON event(kind, id);

CREATE TRIGGER IF NOT EXISTS event_immutable_update
BEFORE UPDATE ON event
BEGIN SELECT RAISE(ABORT, 'event log is append-only (D2)'); END;

CREATE TRIGGER IF NOT EXISTS event_immutable_delete
BEFORE DELETE ON event
BEGIN SELECT RAISE(ABORT, 'event log is append-only (D2)'); END;

-- ---------------------------------------------------------------- item universe
-- Items are word *senses*, not words. laufen[to run] and laufen[to be showing]
-- are separate rows. Gender is part of the item, not an attribute of it.
CREATE TABLE IF NOT EXISTS item (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    lemma          TEXT NOT NULL,
    pos            TEXT,
    gender         TEXT CHECK (gender IN ('der','die','das') OR gender IS NULL),
    plural         TEXT,
    sense_gloss_en TEXT NOT NULL,
    cefr_level     TEXT,
    wortliste_ref  TEXT,
    principal_parts TEXT,                   -- verb conjugation: '3sg, prät, aux part'
    item_type      TEXT NOT NULL DEFAULT 'vocab',
    source         TEXT NOT NULL,          -- provenance, for the D4 audit
    gloss_source   TEXT,                   -- where the ENGLISH came from
    UNIQUE (lemma, sense_gloss_en)
);

-- Every word form attested in an official syllabus. Distinct from `item`:
-- an item is something Jana *teaches* and schedules; a token is merely
-- something a model is *permitted* to use. Adjectives and adverbs live here
-- because the structured parser only recognises nouns and verbs, yet "groß"
-- must not make correct German fail validation.
CREATE TABLE IF NOT EXISTS syllabus_token (
    token      TEXT NOT NULL,
    cefr_level TEXT,
    source     TEXT NOT NULL,
    PRIMARY KEY (token)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS grammar_point (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT NOT NULL UNIQUE,
    cefr_level       TEXT,
    description_en   TEXT,
    prerequisite_ids TEXT
);

-- ---------------------------------------------------------------- corpus
CREATE TABLE IF NOT EXISTS sentence (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    de         TEXT NOT NULL,
    en         TEXT,
    source     TEXT NOT NULL,              -- must trace to a human-authored file
    cefr_hint  TEXT,
    lesson_ord INTEGER,
    UNIQUE (de, source)
);

CREATE VIRTUAL TABLE IF NOT EXISTS sentence_fts
    USING fts5(de, content='sentence', content_rowid='id');

CREATE TRIGGER IF NOT EXISTS sentence_fts_ins AFTER INSERT ON sentence BEGIN
    INSERT INTO sentence_fts(rowid, de) VALUES (new.id, new.de);
END;

-- ---------------------------------------------------------------- narrative
-- One day, one episode. The learner walks somewhere, meets someone, and they
-- talk. Everything he practises that day is drawn from this conversation.
--
-- Why a story at all: the architecture doc listed a narrative engine as an
-- explicit non-goal, on the grounds that a fixed exam format rewards format
-- drilling per minute more than immersion does. That reasoning is sound about
-- *retrieval* and wrong about *encoding*. A word met once in a scene the
-- learner was part of has an episodic hook; the same word on a flashcard has
-- none, and encoding specificity says the retrieval cue should resemble the
-- encoding context. So the story is where vocabulary is met, and the drills
-- stay exam-shaped for where it is retrieved. Neither replaces the other.
CREATE TABLE IF NOT EXISTS story_day (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    day_number   INTEGER NOT NULL UNIQUE,
    date         TEXT NOT NULL,
    title        TEXT NOT NULL,
    setting_de   TEXT,
    setting_en   TEXT,
    npc_name     TEXT,
    npc_role     TEXT,
    theme        TEXT,                     -- the Goethe B1 topic this covers
    target_ids   TEXT,                     -- JSON: FSRS items due this day
    status       TEXT NOT NULL DEFAULT 'open',
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS story_turn (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    day_id       INTEGER NOT NULL REFERENCES story_day(id),
    ord          INTEGER NOT NULL,
    speaker      TEXT NOT NULL CHECK (speaker IN ('npc','learner')),
    de           TEXT NOT NULL,
    en           TEXT,
    learner_input TEXT,                    -- exactly what he typed
    input_lang   TEXT,                     -- 'en' (translate me) or 'de' (correct me)
    correction   TEXT,
    provenance   TEXT,
    validated    INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS story_turn_day_idx ON story_turn(day_id, ord);

-- Which vocabulary appeared on which day. This is the join that makes the
-- story an anchor rather than decoration: a word met on day 9 can be brought
-- back on days 10, 12, 16, 25 and 44, and the scene it came from can be named
-- when it returns ("at the Bürgeramt, remember").
CREATE TABLE IF NOT EXISTS story_vocab (
    day_id     INTEGER NOT NULL REFERENCES story_day(id),
    item_id    INTEGER NOT NULL REFERENCES item(id),
    first_seen TEXT NOT NULL,
    PRIMARY KEY (day_id, item_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS story_vocab_item_idx ON story_vocab(item_id);

-- Grammar is scheduled exactly like vocabulary: derived from the event log,
-- droppable, rebuilt by jana/project.py. A rule is a thing you forget on the
-- same curve a word is, so it gets the same machinery rather than a parallel
-- one that would drift.
CREATE TABLE IF NOT EXISTS grammar_state (
    point_id     INTEGER NOT NULL REFERENCES grammar_point(id),
    stability    REAL,
    difficulty   REAL,
    due_at       TEXT,
    reps         INTEGER NOT NULL DEFAULT 0,
    lapses       INTEGER NOT NULL DEFAULT 0,
    last_seen_at TEXT,
    PRIMARY KEY (point_id)
) WITHOUT ROWID;

-- A real human saying one German sentence, cut out of the corpus or the Goethe
-- exam audio. This is what Hören plays instead of the operating system's
-- synthesiser wherever a recording exists — see jana/audio.py for why recorded
-- speech is worth the alignment cost that synthesis avoids.
CREATE TABLE IF NOT EXISTS clip (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    source     TEXT NOT NULL,          -- which video or exam file it came from
    start_s    REAL NOT NULL,
    end_s      REAL NOT NULL,
    text       TEXT NOT NULL,          -- German, transcribed with -l de
    path       TEXT NOT NULL UNIQUE,   -- the cut mp3, served to the browser
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS clip_source_idx ON clip(source);

-- ------------------------------------------------------------ semantic memory
-- Vectors live beside the rows they describe. See jana/memory.py for why this
-- is not a separate vector database, and for the threshold at which it should
-- become Postgres + pgvector. The shape is deliberately what pgvector wants.
CREATE TABLE IF NOT EXISTS embedding (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,          -- item | story_turn | sentence
    ref_id     INTEGER NOT NULL,       -- row id in that kind's table
    text       TEXT NOT NULL,          -- exactly what was embedded
    model      TEXT NOT NULL,          -- so a model change is a re-index, not a wipe
    dim        INTEGER NOT NULL,
    vector     BLOB NOT NULL,          -- float32, unit length
    created_at TEXT NOT NULL,
    UNIQUE (kind, ref_id, model)
);
CREATE INDEX IF NOT EXISTS embedding_kind_idx ON embedding(kind, model);

-- ---------------------------------------------------------------- derived
-- Rung is per-item-per-modality (open decision 12.1, resolved):
--   text  track -> rungs 1-4      audio track -> rungs 5-6
CREATE TABLE IF NOT EXISTS item_state (
    item_id      INTEGER NOT NULL REFERENCES item(id),
    modality     TEXT    NOT NULL CHECK (modality IN ('text','audio')),
    stability    REAL,
    difficulty   REAL,
    due_at       TEXT,
    rung         INTEGER NOT NULL,
    reps         INTEGER NOT NULL DEFAULT 0,
    lapses       INTEGER NOT NULL DEFAULT 0,
    last_seen_at TEXT,
    PRIMARY KEY (item_id, modality)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS item_state_due_idx ON item_state(modality, due_at);

CREATE TABLE IF NOT EXISTS confusion_edge (
    item_a           INTEGER NOT NULL REFERENCES item(id),
    item_b           INTEGER NOT NULL REFERENCES item(id),
    weight           REAL    NOT NULL DEFAULT 0,
    last_observed_at TEXT,
    PRIMARY KEY (item_a, item_b)
) WITHOUT ROWID;

-- ---------------------------------------------------------------- generated content
CREATE TABLE IF NOT EXISTS exercise (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    item_ids         TEXT NOT NULL,
    exam_module      TEXT,
    task_type        TEXT NOT NULL,
    prompt           TEXT NOT NULL,
    reference_answer TEXT NOT NULL,
    distractors_json TEXT,
    source           TEXT NOT NULL,
    generated_by     TEXT NOT NULL,
    validated        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS session (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    date         TEXT NOT NULL,
    planned_json TEXT,
    started_at   TEXT,
    ended_at     TEXT
);

CREATE TABLE IF NOT EXISTS mock_exam (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    date          TEXT NOT NULL,
    module        TEXT NOT NULL,
    raw_score     REAL,
    max_score     REAL,
    predicted     REAL,                    -- level 2 calibration (arch doc 7)
    per_task_json TEXT
);
"""

# Tables project.py owns outright. Dropping these must always be safe.
DERIVED_TABLES = ("item_state", "confusion_edge", "grammar_state")


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(path) if path is not None else Path(
        os.environ.get("JANA_DB", DB_PATH))
    path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False is required, not a shortcut.
    #
    # FastAPI runs a sync generator dependency in one threadpool thread and the
    # sync endpoint that consumes it in another, so a connection opened in
    # `get_db` is legitimately *used* on a different thread than it was created
    # on. SQLite's default guard rejects that, and because the threadpool often
    # reuses a thread it rejects it only sometimes — an endpoint that passes by
    # hand and fails under load.
    #
    # This is safe here precisely because connections are never shared: one is
    # opened and closed per request, and only one thread ever touches it. It
    # would not be safe the moment a connection is cached at module scope.
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


# Columns added after the first databases were written. SQLite has no
# "ADD COLUMN IF NOT EXISTS", and CREATE TABLE IF NOT EXISTS silently skips an
# existing table — so a column added to SCHEMA alone reaches new databases only.
# Every late column must be listed here as well or it will be missing exactly
# where the data already is.
LATE_COLUMNS = (
    ("item", "gloss_source", "TEXT"),
    ("item", "principal_parts", "TEXT"),
    # The whole generated task, verbatim. `prompt` and `reference_answer` were
    # shaped for single-answer drills and cannot hold a Lesen Teil 3 with six
    # ads and five situations, so the full structure is kept alongside them.
    ("exercise", "body_json", "TEXT"),
    ("exercise", "teil", "INTEGER"),
    ("exercise", "day_number", "INTEGER"),
    ("exercise", "grammar_point_id", "INTEGER"),
    ("exercise", "created_at", "TEXT"),
    ("exercise", "seen_count", "INTEGER DEFAULT 0"),
    ("exercise", "last_seen_at", "TEXT"),
)


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    for table, column, decl in LATE_COLUMNS:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    conn.commit()


def main() -> None:
    conn = connect()
    init_db(conn)
    n = conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    print(f"initialised {DB_PATH} ({n} tables)")


if __name__ == "__main__":
    main()
