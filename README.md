# Regex Review Console

**Auditing a model-generated bank-statement parser across 6.8 million transactions — a review harness, and what it found.**

A model generates regexes that parse raw bank transaction notes into ~30
structured fields. This project is the **review layer on top of that output**:
the analysis that located the anomalies, the tooling that made the review
repeatable, and the defect report that came out of it.

📋 **[Findings — 23 defects across 4 themes](FINDINGS.md)** ← the deliverable

---

## Scale

| | |
|---|---|
| Transactions reviewed | **6,811,926** |
| — debit / credit | 4,762,821 · 2,049,105 |
| Distinct regexes | **1,832** (829 debit · 1,201 credit) |
| Distinct transaction notes | 6,146,322 |
| Extracted feature columns per row | 30 (41 columns total) |
| Tagsets · categories · sub-categories | 467 · 27 · 28 |
| Corpus size | ~200 MB parquet → 380 MB local index |

**The distribution is the first finding.** Transactions per regex:

```
max     2,344,138     ← one regex covers a third of the corpus
mean        3,718
median          4     ← half of all regexes cover 4 transactions or fewer
```

A median of 4 against a max of 2.3 M is not a natural long tail — it is
over-fragmentation. Hundreds of regexes exist because one transaction format got
split on a delimiter, or because near-identical patterns were emitted separately.
That single distribution motivated findings A2, A3 and A4.

## Objective

Take the model's generated output and answer three questions:

1. **Which regexes are wrong?** — out of 1,832, with no labels and no ground truth.
2. **What is each one failing to extract?** — which of the 30 feature columns
   stay empty, and why.
3. **What should the model change?** — actionable, deduplicated, with evidence,
   not a list of one-off complaints.

The constraint that shapes everything: at 6.8 M rows a bad regex and a good one
look identical in a spreadsheet, and a regex matching 2.3 M transactions cannot
be verified by scrolling its matches. The review had to work **per regex**, not
per transaction.

## Approach

**1. Profiled the generated output** — pandas / numpy over the corpus: per-regex
aggregation, tagset and category distributions, value-frequency counts on
extracted fields (`remark` alone had ~14 k distinct values), and **non-empty fill
rates for all 30 feature columns**.

Fill rates are the workhorse of the whole review. A regex matching 40,000
transactions that populates `counter_party` in 0 % of them is a feature-map gap
that is invisible row-by-row and unmissable in a rollup. That one metric turned
"read 1,832 regexes" into "look at the ones with suspicious zeros".

**2. Built this console** so the review was reproducible and splittable across
reviewers instead of a one-off notebook — sample transactions under every regex,
fill-rate panel, and a structured verdict form writing to an audited store.

**3. Reviewed and wrote it up** — [FINDINGS.md](FINDINGS.md).

## What was achieved

- **6.8 M transactions reduced to 1,832 reviewable units**, each with sample
  evidence and a fill-rate profile — a corpus that could not be read manually
  became a work queue that could.
- **23 distinct defects documented** with examples and recommended fixes, from
  ~30 raw observations after deduplication, grouped into clustering /
  feature-map integrity / field extraction / business rules.
- **Four cross-cutting root causes identified** that explain most of the 23 —
  delimiter handling alone accounts for five separate symptoms. This is what
  turned a bug list into a fix order.
- **A reusable harness**, not a one-time analysis: the same tool runs against the
  next model revision, and multi-reviewer feedback merges with conflict
  reporting.
- **Complete audit trail** — every verdict is attributed and versioned; nothing a
  reviewer typed is silently overwritten.

A sample of what surfaced:

| | Observed | Should be |
|---|---|---|
| `IMPS/P2A/…/INSTANT CASH FZ/…` | `CASH` lifted out as a keyword, counterparty split around it | CP preserved whole |
| `220626` | `ref_num` | `datetime` |
| `LIEN MARKING FOR NACH/…` | counterparty = `MARKING` | full span captured first |
| `txn_period` | `26/2459` | rejected as unparseable |
| Two `ATM/CWRR` patterns differing only in `\d{12}` vs `\d{1,4}` | two clusters | one generalised regex |

## Challenges

**No ground truth.** Nothing said which regexes were correct. Every defect had to
be established from the data itself — anomalous fill rates, implausible values
(`txn_period = 26/2459`), and structural comparison between near-duplicate
patterns.

**Skew.** One regex covers 2.3 M transactions, the median covers 4. Uniform
sampling would have shown the same handful of high-volume formats over and over
and never reached the fragmented tail — where most of the defects live. The tool
caps samples *per regex* and can force distinct notes, so a 2.3 M-row regex
contributes five rows, same as a 4-row one.

**Query latency at 6.8 M rows.** pandas scans were too slow for an interactive
loop where every filter change re-queries. Solved by loading into an embedded
ClickHouse (chdb) MergeTree ordered by `(regex_hash, tags, transaction_type)` and
precomputing the per-regex summary and a facet cube — filters became instant, and
the parquet is never read again after the one-time ~10 s build.

**Distinguishing a regex defect from a business-rule defect.** "`CW` extracted as
counterparty" and "`CW` should map to flow" look the same in the data but need
different fixes in different layers. The findings tag each defect
`CLUSTER` / `EXTRACT` / `RULES` so the model owner knows where it lands.

**Overlapping observations.** Reviewing 1,832 regexes produces the same
underlying bug described a dozen different ways. Consolidating ~30 raw notes into
23 distinct defects and 4 root causes was a substantial part of the work — an
unmerged list would have sent someone chasing symptoms.

**Real customer data.** The corpus cannot leave the environment, which ruled out
hosted dashboards and shaped the deployment model: fully offline, LAN-only
hosting, and every data artefact excluded from version control.

## Tools

| | |
|---|---|
| **Analysis** | Python 3.12, pandas, numpy |
| **Query engine** | [chdb](https://github.com/chdb-io/chdb) — embedded ClickHouse (MergeTree, `uniqExact`, `groupUniqArray`, facet rollups) |
| **UI** | Streamlit (multi-tab, `st.data_editor` inline grids, cached queries) |
| **Feedback store** | SQLite — upsert + append-only history, WAL mode |
| **Storage format** | Apache Parquet (via pyarrow) |
| **Env / packaging** | uv, pyproject.toml, locked deps |

## Architecture

```
corpus.parquet  ──build_review_db.py──▶  chdb_review/   ──review_app.py──▶  Streamlit UI
 (model output)     (one-time, ~10s)      review.corpus                          │
   6.8M rows                              review.regex_summary                   ▼
                                          review.regex_facets            review_feedback.db
                                                                          (SQLite + audit log)
```

- **`build_review_db.py`** — loads the parquet into a persistent chdb MergeTree
  ordered by `(regex_hash, tags, transaction_type)`, then precomputes a per-regex
  summary (counts, tagsets, categories, fill rate of every feature column) and a
  facet cube for the sidebar filters.
- **`review_app.py`** — three tabs. *Review*: pick a regex, see representative
  samples, see which feature columns are never populated, record a verdict.
  *Browse samples*: an editable grid for triaging many regexes inline.
  *Feedback store*: everything recorded, per-regex history, CSV / JSON export.
  Filters on tag combination, transaction type, category, regex substring, review
  status, and a per-regex sample cap with a distinct-notes option.
- **`feedback_store.py`** — SQLite. `regex_feedback` holds current state keyed by
  a hash of the regex string; `feedback_history` is an append-only audit trail.
- **`merge_feedback.py`** — reconciles DBs from reviewers who each ran their own
  copy. Newest write wins per regex, all versions preserved, conflicts reported.

## Quick start

```bash
uv sync                                    # python 3.12 + chdb, streamlit, pandas
uv run build_review_db.py                  # drop the corpus parquet here first
REVIEWER="your name" uv run streamlit run review_app.py
```

[QUICKSTART.md](QUICKSTART.md) is the reviewer-facing walkthrough.
[SHARING.md](SHARING.md) covers the two deployment shapes — one LAN host, or one
copy per reviewer plus a merge.

## Data

**No data is in this repo, by design.** The corpus parquet, the built
`chdb_review/` index and `review_feedback.db` are gitignored — the corpus is real
customer transaction data. Examples in [FINDINGS.md](FINDINGS.md) are masked.
Everything except the parquet regenerates from it.
