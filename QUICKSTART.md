# Regex review console — reviewer quickstart

You need this folder plus the corpus parquet (sent separately, ~200 MB).
Runs fully offline on your machine. Nothing is uploaded anywhere.

## 1. Install uv (once)

macOS / Linux:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

## 2. Set up

Put the parquet file in this folder, then:

```bash
uv sync                      # installs python 3.12 + chdb, streamlit, pandas
uv run build_review_db.py    # picks up the parquet automatically; ~10s
```

That creates `chdb_review/` (~380 MB of local index). One-time.

## 3. Review

```bash
REVIEWER="your name" uv run streamlit run review_app.py
```

Opens http://localhost:8501. On Windows use:
`set REVIEWER=your name` then `uv run streamlit run review_app.py`.

## Using it

**Sidebar filters** — tag combination (exact tagset) or individual tags with
any-of / all-of, transaction type, category, regex substring search, review
status, and **transactions per regex** (default 5, so a regex repeated 2 M times
still shows you only 5 rows). "Distinct notes only" makes those 5 rows 5
*different* notes.

**Review tab** — pick a regex from the left grid (or ◀ Prev / Next ▶), see its
sample transactions, the extracted-field fill rates (which features are never
populated = feature_map gaps), then fill the feedback form. Category and
sub-category accept new values you type here.

**Browse samples tab** — all filtered regexes in one transactions table with
`R1, R2, …` ids, and directly below it an editable feedback grid with the same
ids. Edit inline across many regexes, then hit **Save N edited row(s)**.

Feedback fields per regex: `regex correction required`, `regex corrected`,
`feature_map gaps`, `updated_category`, `updated_subcategory`, notes, status.

**Feedback store tab** — everything you have recorded, per-regex history, CSV /
JSON export.

## When you're done

Send back **`review_feedback.db`** — that single file holds all your review work.
(Everything else regenerates from the parquet.)

## Gotchas

- Only one process may use `chdb_review/` at a time. If you see
  `Cannot lock file .../chdb_review/status`, another copy of the app is still
  running — close it.
- Re-running `uv run build_review_db.py --rebuild` reloads the corpus from
  scratch. It never touches `review_feedback.db`.
- Category / sub-category dropdowns in the Browse grid only offer existing
  values; to record a brand-new category name, use the Review tab form.
