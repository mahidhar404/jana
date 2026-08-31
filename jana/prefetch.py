"""Fill the exercise bank ahead of time (arch doc D5).

The interactive path must not wait on a model. Every task the learner opens
should already exist, generated while nobody was looking — which is the whole
point of D5, and the reason the §7 latency budget was ever meetable.

Run it when the machine is idle:

    uv run python -m jana.prefetch --want 4

Safe to interrupt, safe to repeat, and it stops filling a slot the moment the
generator starts returning templates, because stocking the bank with fallbacks
would be worse than leaving it empty — the learner would get the same two
canned exercises forever and no signal that anything was wrong.
"""

from __future__ import annotations

import argparse
import time

from jana import bank, teile
from jana.db import connect, init_db

SLOTS = [("lesen", t) for t in (3, 4, 5)] + \
        [("hoeren", t) for t in (2, 4)] + \
        [("schreiben", 3), ("sprechen", 3)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--want", type=int, default=4,
                        help="unseen exercises to keep per slot")
    parser.add_argument("--day", type=int, default=None,
                        help="anchor generated tasks to this story day")
    args = parser.parse_args()

    conn = connect()
    init_db(conn)
    started = time.perf_counter()
    made = 0
    for modul, teil in SLOTS:
        have = bank.count(conn, modul, teil)
        if have >= args.want:
            print(f"  {modul} Teil {teil}: {have} unseen — full")
            continue
        added = teile.stock(conn, modul, teil, args.day, args.want)
        made += added
        state = "stalled" if added == 0 else "ok"
        print(f"  {modul} Teil {teil}: +{added} → {bank.count(conn, modul, teil)} "
              f"unseen  ({state})", flush=True)

    summary = bank.stats(conn)
    print(f"\ngenerated {made} in {time.perf_counter() - started:.0f}s · "
          f"bank holds {summary['total']}")


if __name__ == "__main__":
    main()
