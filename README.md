# Jana

A German instructor with one measurable goal: pass all four modules of
**Goethe-Zertifikat B1 on 7 January 2027**. The exam is the spec.

## Run it

```bash
uv run uvicorn jana.web:app --port 8420
```

The UI is a React app in `web/`. After any change to it:

```bash
cd web && npm run build
```

FastAPI serves the built output, so an unbuilt change will not appear. For
hot reload during UI work, `npm run dev` on port 5180 proxies the API to 8420.

Open <http://127.0.0.1:8420>. Six modes, with **Die Geschichte** as the front door: **Die Geschichte** (one animated episode a day), **Vokabeln**, **Lesen**,
**Hören**, **Schreiben**, **Sprechen**.

In the story, type **English** and your character says it in German; type
**German** and it gets corrected with the rule named. The language you type is
the difficulty setting.

Needs `ollama serve` running. Copy `.env.example` to `.env` and add
`DEEPSEEK_API_KEY` (a second key in `DEEPSEEK_API_KEY_2` is used for failover on
429/401) to route authored German and Schreiben grading to the stronger tier.

The terminal client drives the same session — start in one, finish in the other:

```bash
uv run jana
```

## Rebuild from source data

The repository carries code, not data. `data/jana.db`, `data/clips/` and the
110 GB course corpus are all absent by design: the database is derived state
that replays from the event log, and the clips are cut from purchased course
video that is not ours to redistribute. A fresh clone therefore starts empty and
bootstraps itself:


```bash
uv run python -m jana.ingest             # corpus glossaries + Goethe Wortlisten
uv run python -m jana.ingest.glosses     # English for every item
uv run python -m jana.ingest.lemmas      # adjectives/adverbs the parsers missed
uv run python -m jana.ingest.audit_vocab # strike names/numbers out of the harvest
uv run python -m jana.ingest.grammar     # grammar spine, linked to corpus lessons
uv run python -m jana.project rebuild    # re-derive the learner model
uv run python -m jana.memory index       # embed anything not yet embedded
uv run python -m jana.prefetch --want 4  # fill the exercise bank ahead of time
uv run python -m jana.ingest.clips --exam      # Goethe listening audio -> clips
uv run python -m jana.ingest.clips --courses   # German-dense corpus -> clips
uv run python -m jana.memory search --query "renting a flat"
uv run python -m jana.project forecast   # predicted recall on exam day
uv run python -m unittest discover -s tests
```

## The shape of it

```
event log  ──replay──▶  item_state  ──▶  scheduler  ──▶  engine  ──▶  adapters
(truth)                 (derived)        (FSRS)         (core)       (web, terminal)
   ▲                                                                      │
   └──────────────────────── attempts, overrides ◀────────────────────────┘
```

| Module | Responsibility |
|---|---|
| `jana/events.py` | Append-only log. The only thing that is true. |
| `jana/project.py` | Rebuilds `item_state` from the log. Drop it any time. |
| `jana/fsrs.py` | FSRS-5 forgetting curve. Answers "will this survive to January?" |
| `jana/scheduler.py` | What to study today. Pure code, no model calls. |
| `jana/core.py` | Session logic. No I/O — the reason two front ends exist. |
| `jana/grader.py` | Deterministic grading; disagreements become the eval set. |
| `jana/llm.py` | Three-tier model routing, with call telemetry. |
| `jana/lexicon.py` | Refuses model German that leaves the syllabus. |
| `jana/story.py` | The daily episode. Hand-written 60-day arc, generated dialogue. |
| `web/src/components/Scene.jsx` | The animated stage. One `phase` + `speaker` drives everything. |
| `jana/memory.py` | Semantic memory. Embeddings in SQLite, exact cosine. |
| `jana/teile.py` | The seven Goethe B1 formats `modules.py` did not cover. |
| `jana/bank.py` | Every generated question, kept and reused. 8 ms vs 11.7 s. |
| `jana/grammar.py` | Grammar drills, FSRS-scheduled, prerequisites enforced. |
| `jana/prefetch.py` | The D5 batch: fill the bank so the loop never waits. |
| `jana/audio.py` | Whisper-aligned real German. Recordings first, synthesis last. |
| `jana/ingest/grammar.py` | 34-point grammar spine, matched to the learner's own videos. |
| `jana/tutor.py` | The conversational tutor. Scheduled, not free-floating. |
| `jana/modules.py` | Lesen, Hören, Schreiben, Sprechen. |
| `jana/web.py`, `jana/session.py` | Adapters. No teaching logic in either. |

## Decisions worth knowing

**The event log is the product; scheduler state is a projection.** The
scheduling algorithm is wrong today and will be replaced. When it is, the log
replays and the entire learner model re-derives from day one. Storing only
current state would destroy that option permanently and silently. This is
already paid for itself twice: a crashed session recovered with no recovery
code, and the placeholder scheduler was swapped for FSRS without migrating a row.

**Conversation is scheduled.** The tutor is handed today's due items by the same
FSRS scheduler that drives the drill, and every target word the learner actually
produces is logged as a rung-3 review. Talking to Jana *is* studying. A quiz
gives retrieval practice and spacing; conversation adds pushed output,
comprehensible input, contextual variation and immediate recast — and those four
need a conversation to exist at all.

**Local models do not author German — they are checked.** A 12B model emits subtly wrong German
often enough to teach errors that take years to unlearn, and a beginner cannot
detect them until roughly B1 — exactly too late. So nothing is trusted: every
German sentence a model emits is checked word-by-word against a permitted
lexicon of ~7,800 forms before the learner sees it, and regenerated if it fails.
One unfamiliar word is allowed through *and labelled* — that is comprehensible
input at i+1, and hiding it would mean only ever meeting known words.

This catches vocabulary, not grammar, so it is a filter and not a proof. The UI
therefore labels the provenance of every sentence. The validator's pass rate is
also the model bake-off, running continuously in production rather than once in
a notebook.

**A Wortliste noun with no English gloss is drilled on its article, not
translated.** 1,529 items became teachable on day one without inventing a word
of English. Items with neither gloss nor gender are refused rather than faked.

**The browser never receives the answer to a typed question.** The terminal
could keep it in-process; a browser cannot. Grading is server-side. Enforced by
a test.

## What is deliberately absent

No vector database, no agent framework, no Postgres, no Docker, no fine-tuning,
and no LLM anywhere in the scheduling path or the deterministic grading path. Each is a real technology
with a real use; none of them earns its place here yet. Adding one before the
need is measurable would teach the wrong lesson.

## Known gaps

- **Sentence extraction from the Wortliste is disabled.** The PDF is
  two-column and this extractor reads emission order, so columns interleave.
  Needs positional extraction — which the Modellsatz parser will need anyway.
- **Hören now plays real humans.** whisper.cpp (`-l de`) aligned the Goethe
  listening material and the German-dense courses into **4,938 clips / 378
  minutes** of timestamped German (772 of them genuine exam audio).
  `find_clip()` serves a recording only on a near-exact match — the bar is
  0.95, measured: an exact match scores 1.000 and a merely *related* sentence
  tops out at 0.87, so the threshold sits in the empty gap between them. Below
  it, browser synthesis. A robot saying the right sentence beats a human saying
  the wrong one. **Sprechen** still uses browser ASR, so its scoring is
  indicative rather than exam-grade. The 718 corpus subtitle files remain
  unusable — they are English machine-ASR, which is what made this alignment
  necessary.
- **Only 114 items carry English glosses.** The rest are drilled on article (nouns)
  or principal parts (verbs) without translating a word of English.

## License

[MIT](LICENSE).

That covers the code in this repository. It does not and cannot cover what the
ingest pipeline reads: the Goethe-Institut Wortliste and Modellsätze, and the
course video the clips are cut from, remain under their own terms. None of that
material is distributed here — `data/` is empty by design, and anyone rebuilding
the database supplies their own copies.
