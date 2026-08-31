"""FSRS-5 scheduling.

Chosen over SM-2 because SM-2 tracks one number (an ease factor) and adjusts it
with hand-tuned constants, while FSRS tracks two — *stability* (how long the
memory lasts) and *difficulty* (how hard this item is for this learner) — and
derives the interval from an explicit forgetting curve. That matters here for a
reason specific to this project: the exam is on a fixed date, so we eventually
need to ask "what is the probability this item is retrievable on 7 January?".
SM-2 cannot answer that question at all. FSRS answers it directly, because
retrievability is a function it exposes.

Deadline-aware scheduling is written here but switched off until the Phase 2
gate — see `next_interval(..., horizon_days=...)`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

AGAIN, HARD, GOOD, EASY = 1, 2, 3, 4

# FSRS-5 published defaults. These are the population priors; they get refit
# against this learner's own log once there is enough history (arch doc §7).
DEFAULT_W = (
    0.40255, 1.18385, 3.173, 15.69105, 7.1949, 0.5345, 1.4604, 0.0046,
    1.54575, 0.1192, 1.01925, 1.9395, 0.11, 0.29605, 2.2698, 0.2315,
    2.9898, 0.51655, 0.6621,
)

DECAY = -0.5
FACTOR = 19.0 / 81.0
MIN_STABILITY = 0.01
MAX_INTERVAL_DAYS = 365.0
DESIRED_RETENTION = 0.9


@dataclass(frozen=True)
class Memory:
    stability: float
    difficulty: float


def retrievability(elapsed_days: float, stability: float) -> float:
    """Probability of recall after `elapsed_days`. The forgetting curve."""
    if stability <= 0:
        return 0.0
    return (1 + FACTOR * elapsed_days / stability) ** DECAY


def interval_for(stability: float, retention: float = DESIRED_RETENTION) -> float:
    """Days until retrievability decays to `retention`."""
    return (stability / FACTOR) * (retention ** (1 / DECAY) - 1)


def _clamp_difficulty(d: float) -> float:
    return min(10.0, max(1.0, d))


def first_review(grade: int, w=DEFAULT_W) -> Memory:
    stability = max(w[grade - 1], MIN_STABILITY)
    difficulty = _clamp_difficulty(w[4] - math.exp(w[5] * (grade - 1)) + 1)
    return Memory(stability, difficulty)


def _next_difficulty(d: float, grade: int, w=DEFAULT_W) -> float:
    delta = -w[6] * (grade - 3)
    # Linear damping: difficulty moves less the closer it already is to the edge.
    damped = d + delta * (10 - d) / 9
    target = w[4] - math.exp(w[5] * 3) + 1          # difficulty after an EASY
    return _clamp_difficulty(w[7] * target + (1 - w[7]) * damped)


def _stability_on_recall(m: Memory, r: float, grade: int, w=DEFAULT_W) -> float:
    hard_penalty = w[15] if grade == HARD else 1.0
    easy_bonus = w[16] if grade == EASY else 1.0
    growth = (
        math.exp(w[8])
        * (11 - m.difficulty)
        * (m.stability ** -w[9])
        * (math.exp(w[10] * (1 - r)) - 1)
        * hard_penalty
        * easy_bonus
    )
    return m.stability * (1 + growth)


def _stability_on_lapse(m: Memory, r: float, w=DEFAULT_W) -> float:
    lapsed = (
        w[11]
        * (m.difficulty ** -w[12])
        * (((m.stability + 1) ** w[13]) - 1)
        * math.exp(w[14] * (1 - r))
    )
    # A lapse must never make a memory look stronger than it already was.
    return max(MIN_STABILITY, min(lapsed, m.stability))


def review(m: Memory | None, grade: int, elapsed_days: float,
           w=DEFAULT_W) -> Memory:
    """Advance memory state by one graded review."""
    if m is None:
        return first_review(grade, w)
    r = retrievability(elapsed_days, m.stability)
    difficulty = _next_difficulty(m.difficulty, grade, w)
    if grade == AGAIN:
        stability = _stability_on_lapse(m, r, w)
    else:
        stability = _stability_on_recall(m, r, grade, w)
    return Memory(max(MIN_STABILITY, stability), difficulty)


def next_interval(m: Memory, retention: float = DESIRED_RETENTION,
                  horizon_days: float | None = None) -> float:
    """Days until this item should be seen again.

    `horizon_days` is the deadline-aware modification (arch doc §3). Standard
    FSRS optimises for indefinite retention; Jana optimises for retrievability
    on one specific morning. An interval that would land the next review *after*
    the exam is worthless, so it is pulled back inside the horizon. Switched on
    at the Phase 2 gate, not before — turning it on early just compresses
    intervals and costs review capacity for no benefit.
    """
    days = min(max(interval_for(m.stability, retention), 0.007), MAX_INTERVAL_DAYS)
    if horizon_days is not None and days > horizon_days > 0:
        days = horizon_days
    return days
