"""Load every corpus source into a fresh database.

One entry point, run in dependency order, so rebuilding the item universe is a
single command rather than a sequence someone has to remember correctly.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from jana.db import connect, init_db
from jana.ingest import html_gloss, wortliste

RAW = Path("data/raw")

# The Wortlisten are the curriculum spine (D1). Loaded most-advanced first so
# that a lemma appearing at several levels keeps its highest CEFR label.
WORTLISTEN = [
    ("Goethe-Zertifikat_B1_Wortliste.pdf", "B1"),
    ("Goethe-Zertifikat_A2_Wortliste.pdf", "A2"),
    ("A1_SD1_Wortliste_02.pdf", "A1"),
]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.parse_args()

    conn = connect()
    init_db(conn)

    rows = []
    for path in html_gloss.find_files():
        rows.extend(html_gloss.parse(path))
    print(f"corpus glossaries: {html_gloss.load(conn, rows)} items "
          f"from {len(rows)} pairs")

    for filename, cefr in WORTLISTEN:
        path = RAW / filename
        if not path.exists():
            print(f"  MISSING {path} — skipped")
            continue
        text = wortliste.extract(path)
        items = wortliste.parse_items(text, cefr)
        n_items, _ = wortliste.load(conn, items, [], f"goethe:{filename}")
        n_tokens = wortliste.load_tokens(
            conn, wortliste.parse_tokens(text), cefr, f"goethe:{filename}")
        print(f"{cefr} Wortliste: {n_items} new items, "
              f"{n_tokens} new permitted tokens")

    summary = conn.execute(
        """SELECT count(*) AS total,
                  sum(sense_gloss_en NOT LIKE '[%') AS glossed,
                  sum(gender IS NOT NULL) AS gendered,
                  sum(principal_parts IS NOT NULL) AS with_parts,
                  sum(sense_gloss_en LIKE '[%' AND gender IS NULL AND principal_parts IS NULL) AS unteachable
           FROM item""").fetchone()
    print(f"\n  {summary['total']} items · {summary['glossed']} glossed "
          f"· {summary['gendered']} with gender "
          f"· {summary['with_parts']} with verb conjugations "
          f"· {summary['unteachable']} not yet teachable")


if __name__ == "__main__":
    main()
