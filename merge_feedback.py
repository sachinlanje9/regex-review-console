"""Merge reviewer feedback DBs into one.

Use when reviewers each ran the app locally and mailed back their
review_feedback.db. Latest updated_at wins per regex; every version from every
input is kept in feedback_history, so nothing is lost and conflicts stay visible.

    uv run merge_feedback.py merged.db alice.db bob.db carol.db
    uv run merge_feedback.py merged.db reviews/*.db --csv merged.csv
"""

import os
import sqlite3
import sys

import feedback_store as fb


def merge(out: str, inputs: list[str], csv_path: str = "") -> None:
    fb.DB_PATH = out
    fb.init()
    conflicts: dict[str, set[str]] = {}
    latest: dict[str, tuple[str, sqlite3.Row]] = {}
    hist_rows = 0

    for path in inputs:
        if not os.path.exists(path):
            print(f"  skip (missing): {path}")
            continue
        src = sqlite3.connect(path)
        src.row_factory = sqlite3.Row
        cur = src.execute("SELECT * FROM regex_feedback")
        n = 0
        for r in cur:
            n += 1
            k = r["regex_key"]
            who = r["reviewer"] or os.path.basename(path)
            conflicts.setdefault(k, set()).add(who)
            if k not in latest or r["updated_at"] > latest[k][1]["updated_at"]:
                latest[k] = (path, r)
        try:
            hist = src.execute("SELECT * FROM feedback_history").fetchall()
        except sqlite3.OperationalError:
            hist = []
        with sqlite3.connect(out) as dst:
            for h in hist:
                dst.execute(
                    f"""INSERT INTO feedback_history
                        (regex_key, regex, {', '.join(fb.FIELDS)}, saved_at)
                        VALUES (?, ?, {', '.join('?' * len(fb.FIELDS))}, ?)""",
                    [h["regex_key"], h["regex"], *[h[f] for f in fb.FIELDS], h["saved_at"]],
                )
                hist_rows += 1
        src.close()
        print(f"  {path}: {n} feedback rows, {len(hist)} history rows")

    for k, (path, r) in latest.items():
        # history already copied verbatim above; don't double-log the winner
        fb.save(r["regex"], audit=False, **{f: r[f] for f in fb.FIELDS})

    dup = {k: v for k, v in conflicts.items() if len(v) > 1}
    print(f"\nmerged -> {out}: {len(latest)} regexes, {hist_rows} history rows")
    if dup:
        print(f"{len(dup)} regex(es) reviewed by more than one person "
              f"(newest kept, all versions in feedback_history):")
        for k, who in list(dup.items())[:20]:
            print(f"  {k}: {', '.join(sorted(who))}")

    if csv_path:
        fb.all_feedback().to_csv(csv_path, index=False)
        print(f"csv -> {csv_path}")


if __name__ == "__main__":
    argv = sys.argv[1:]
    csv_path = ""
    if "--csv" in argv:
        i = argv.index("--csv")
        csv_path = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    if len(argv) < 2:
        sys.exit(__doc__)
    merge(argv[0], argv[1:], csv_path)
