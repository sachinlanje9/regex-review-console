"""SQLite store for regex review feedback.

One row per regex in `regex_feedback` (current state) plus an append-only
`feedback_history` audit trail, so nothing a reviewer typed is ever lost.

    import feedback_store as fb
    fb.init()
    fb.save(regex="...", correction_required=True, regex_corrected="...", ...)
    fb.get(regex="...")        -> dict | None
    fb.all_feedback()          -> pandas.DataFrame
"""

import hashlib
import os
import sqlite3
from datetime import datetime, timezone

import pandas as pd

DB_PATH = os.environ.get("REVIEW_FEEDBACK_DB", "review_feedback.db")

FIELDS = [
    "correction_required",
    "regex_corrected",
    "feature_map_gaps",
    "updated_category",
    "updated_subcategory",
    "notes",
    "status",
    "reviewer",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS regex_feedback (
    regex_key           TEXT PRIMARY KEY,
    regex               TEXT NOT NULL,
    correction_required INTEGER DEFAULT 0,
    regex_corrected     TEXT DEFAULT '',
    feature_map_gaps    TEXT DEFAULT '',
    updated_category    TEXT DEFAULT '',
    updated_subcategory TEXT DEFAULT '',
    notes               TEXT DEFAULT '',
    status              TEXT DEFAULT 'pending',
    reviewer            TEXT DEFAULT '',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback_history (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    regex_key           TEXT NOT NULL,
    regex               TEXT NOT NULL,
    correction_required INTEGER,
    regex_corrected     TEXT,
    feature_map_gaps    TEXT,
    updated_category    TEXT,
    updated_subcategory TEXT,
    notes               TEXT,
    status              TEXT,
    reviewer            TEXT,
    saved_at            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_hist_key ON feedback_history(regex_key);
CREATE INDEX IF NOT EXISTS ix_fb_status ON regex_feedback(status);
"""


def key_of(regex: str) -> str:
    """Stable id for a regex string (regexes are long and full of backslashes)."""
    return hashlib.sha1((regex or "").encode("utf-8")).hexdigest()[:16]


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init() -> None:
    with _conn() as c:
        c.executescript(SCHEMA)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save(regex: str, audit: bool = True, **vals) -> str:
    """Upsert feedback for `regex`; appends a history row unless audit=False. Returns the key."""
    k, now = key_of(regex), _now()
    old = get(regex_key=k) or {}
    row = {f: vals.get(f) for f in FIELDS}
    if isinstance(row.get("feature_map_gaps"), (list, tuple)):
        row["feature_map_gaps"] = ", ".join(row["feature_map_gaps"])
    for f in FIELDS:  # omitted field -> keep what is already stored, never blank it
        if row[f] is None:
            row[f] = old.get(f, 0 if f == "correction_required" else "")
    row["correction_required"] = int(bool(row["correction_required"]))
    sets = ", ".join(f"{f}=excluded.{f}" for f in FIELDS)
    with _conn() as c:
        c.execute(
            f"""INSERT INTO regex_feedback
                (regex_key, regex, {', '.join(FIELDS)}, created_at, updated_at)
                VALUES (?, ?, {', '.join('?' * len(FIELDS))}, ?, ?)
                ON CONFLICT(regex_key) DO UPDATE SET {sets}, updated_at=excluded.updated_at""",
            [k, regex, *[row[f] for f in FIELDS], now, now],
        )
        if audit:
            c.execute(
                f"""INSERT INTO feedback_history
                    (regex_key, regex, {', '.join(FIELDS)}, saved_at)
                    VALUES (?, ?, {', '.join('?' * len(FIELDS))}, ?)""",
                [k, regex, *[row[f] for f in FIELDS], now],
            )
    return k


def save_many(rows: list[dict]) -> int:
    """Bulk upsert. Each row: {"regex": ..., <any of FIELDS>}. Returns rows written."""
    n = 0
    for r in rows:
        rx = r.get("regex")
        if not rx:
            continue
        save(rx, **{f: r[f] for f in FIELDS if f in r})
        n += 1
    return n


def get(regex: str = "", regex_key: str = "") -> dict | None:
    k = regex_key or key_of(regex)
    with _conn() as c:
        r = c.execute("SELECT * FROM regex_feedback WHERE regex_key=?", (k,)).fetchone()
    return dict(r) if r else None


def delete(regex: str = "", regex_key: str = "") -> None:
    k = regex_key or key_of(regex)
    with _conn() as c:
        c.execute("DELETE FROM regex_feedback WHERE regex_key=?", (k,))


def all_feedback() -> pd.DataFrame:
    with _conn() as c:
        return pd.read_sql_query("SELECT * FROM regex_feedback ORDER BY updated_at DESC", c)


def history(regex: str = "", regex_key: str = "") -> pd.DataFrame:
    k = regex_key or key_of(regex)
    with _conn() as c:
        return pd.read_sql_query(
            "SELECT * FROM feedback_history WHERE regex_key=? ORDER BY id DESC", c, params=(k,)
        )


def status_map() -> dict:
    """regex_key -> (status, correction_required) for badging the regex list."""
    with _conn() as c:
        rows = c.execute("SELECT regex_key, status, correction_required FROM regex_feedback").fetchall()
    return {r["regex_key"]: (r["status"], r["correction_required"]) for r in rows}
