# Sharing the regex review console

Two ways. Pick by whether reviewers should hold a copy of the corpus.

| | Option A — you host, they browse | Option B — everyone runs locally |
|---|---|---|
| corpus copies | 1 (yours) | 1 per reviewer (200 MB parquet each) |
| setup per reviewer | open a URL | install deps + build DB (~5 min) |
| feedback | already in one DB | one DB each, merge later |
| works off your LAN / VPN | no | yes |

Both keep the data inside your infrastructure. Do **not** put this on Streamlit
Community Cloud or a public ngrok URL — it is real customer transaction data.

---

## Option A — one host, many reviewers (recommended)

On your machine:

```bash
uv run streamlit run review_app.py --server.address 0.0.0.0 --server.port 8501
```

Send reviewers: `http://192.168.21.153:8501` (your current LAN IP — recheck with
`ipconfig getifaddr en0`, it changes between networks).

- Everyone writes into the same `review_feedback.db`. No merging.
- Each reviewer types their name in the sidebar **Reviewer** box, or you set
  `REVIEWER=` per person — it is stamped on every row.
- Two people editing the *same* regex: last save wins in `regex_feedback`; both
  versions stay in `feedback_history`. Split regexes between reviewers (e.g. by
  the tag or category filter) to avoid it.
- Your laptop must stay awake and on the same network/VPN:
  `caffeinate -s uv run streamlit run review_app.py --server.address 0.0.0.0`.
- Firewall: macOS may prompt to allow incoming connections — allow it. If it
  still fails, System Settings → Network → Firewall → Options → allow `python`.

## Option B — each reviewer runs their own copy

Ship them the code (git repo, or a zip of the `.py` files + `pyproject.toml` +
`uv.lock`) and the parquet **separately** — it is 200 MB, too big for plain git.
Put the parquet on internal S3 / Drive / a network share and send a link.

Reviewer steps:

```bash
# 1. install uv once:  curl -LsSf https://astral.sh/uv/install.sh | sh
git clone <repo> && cd regex-testing
uv sync                                            # installs chdb, streamlit, pandas
# 2. drop the parquet in this folder, then:
uv run build_review_db.py "baroda_corpus_processed (2).parquet"   # ~5s, builds ./chdb_review/ (379 MB)
# 3. review:
REVIEWER=alice uv run streamlit run review_app.py  # opens http://localhost:8501
```

Do **not** ship `chdb_review/` itself — it is 379 MB of derived data and rebuilds
in seconds from the parquet.

Collecting the results — each reviewer sends back their `review_feedback.db`:

```bash
uv run merge_feedback.py merged.db reviews/alice.db reviews/bob.db --csv merged.csv
```

Newest `updated_at` wins per regex; every version from every input is preserved
in `feedback_history`; regexes touched by more than one reviewer are printed as
conflicts.

---

## Gotchas

- **One process per `chdb_review/`.** chdb takes an exclusive lock on the
  directory. Running the builder while the app is up fails with
  `Cannot lock file .../chdb_review/status`. Stop the app first. (Many *browser*
  sessions against one running app are fine — queries are serialized.)
- `review_feedback.db` is the only file that holds review work. Back it up /
  commit it periodically; everything else regenerates.
- Reviewers on Windows: same commands, `uv` handles the Python; use
  `ipconfig` instead of `ipconfig getifaddr en0` for Option A.
