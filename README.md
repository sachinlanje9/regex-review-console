# Regex Review Console

**Checking a machine-generated bank-statement parser across 6.8 million transactions.**

📋 **[The findings — 30 defects](FINDINGS.md)**

---

## What this is about

When money moves in or out of a bank account, the statement records a short,
cryptic line of text. Something like:

```
IMPS/P2A/6058XXXXXXXX/INSTANT CASH FZ/DA Vostro F
```

A human can just about read that: it's an IMPS transfer, to a company called
Instant Cash FZ. Software can't — not until someone teaches it where the parts
are. So a model reads millions of these notes, works out the recurring shapes,
and writes a **regex** (a pattern-matching rule) for each shape. Each regex pulls
the line apart into ~30 labelled fields: who was paid, the reference number, the
date, the payment method, and so on.

That's the system. Checking its work was a job for our data analytics team — I
was part of that review, and I built the tooling we used to do it.

## The scale of the problem

| | Transactions | Patterns |
|---|---|---|
| Money out (debit) | 4,762,821 | 829 |
| Money in (credit) | 2,049,105 | 1,201 |
| **Total** | **6,811,926** | **1,832** |

Each pattern tries to extract 30 fields. Debit and credit were reviewed as two
separate passes.

The model's output had errors in it. Nobody knew which of the 1,832 patterns
were wrong, or how.

And you can't find out by looking. A correct pattern and a broken one are both
just a wall of symbols. The largest single pattern matches **2.3 million
transactions** — reading its matches to check them is not a thing a person can
do. Manual spot-checking finds the loud problems and misses everything else.

## How the errors were found

**The trick was to stop looking at transactions and start looking at patterns.**
6.8 million rows is unreviewable. 1,832 patterns is a to-do list.

So every transaction gets grouped under the pattern that matched it, and each
pattern is measured on one thing: **how often does each of the 30 fields actually
come out filled in?**

That single number does the work. If a pattern matches 40,000 transactions and
the "who was paid" field comes back empty **every single time**, that pattern is
broken — it isn't finding the name. You'd never notice reading rows one by one.
It's impossible to miss in a summary. That turned "review 1,832 patterns" into
"review the ones with suspicious gaps" — which is a job a team can finish.

**A second problem showed up in the arithmetic.** Transactions per pattern:

```
largest    2,344,138
average        3,718
middle             4      ← half the patterns match 4 transactions or fewer
```

Half of all patterns cover four transactions each. That isn't how real
transaction data behaves — bank statements have a few very common shapes and a
tail of rare ones, not a thousand near-identical one-offs. It meant the model was
**splitting one real transaction shape into many patterns**, usually tripping
over a punctuation mark inside the text. The numbers proved that before anyone
read a single pattern.

## What the review found

30 distinct problems, written up in **[FINDINGS.md](FINDINGS.md)**. A few
examples of what "wrong" looked like in practice:

| Transaction text | What the parser did | What it should do |
|---|---|---|
| `…/INSTANT CASH FZ/…` | Saw the word "CASH", treated it as a payment type, and split the company name in half | Keep "Instant Cash FZ" together — it's a name |
| `220626` | Called it a reference number | It's a date — 22 June 2026 |
| `LIEN MARKING FOR NACH/…` | Recorded the counterparty as "MARKING" | Read the whole line before picking out parts |
| Two patterns identical except one digit-count | Created two separate groups | One pattern covers both |
| `txn_period = 26/2459` | Accepted it | There's no month 24 — reject it |

**Four problems were flagged independently in both the debit and credit passes** —
missing anchors, duplicate patterns, punctuation-driven splitting, and garbage
date periods. Two separate reviews, different transaction types, largely
different patterns, same four complaints. That makes those four properties of how
the model writes patterns, not quirks of the data it was reading — and it's the
strongest signal in the whole report about what to fix first.

Underneath the 30, there are **4 root causes**. Punctuation handling alone
explains five of the symptoms. That turns "here are 30 bugs" into "fix these four
things, in this order" — the difference between a complaint and a plan.

## My role

The review was a data analytics team effort. My contribution:

- **Built the review console in this repo** — the profiling that produced the
  fill-rate scorecards and the per-pattern distribution, and the app the team
  used to work through the patterns.
- **Reviewed my share of the patterns** and contributed findings to the report.
- **Consolidated the raw observations** into the 30 distinct defects and 4 root
  causes in [FINDINGS.md](FINDINGS.md).

The code here is mine; the review itself was shared, which is why the tool
supports several reviewers working on different slices and merging their results.

## How the tool works

You pick a pattern from a list, and it shows you:
- real example transactions that pattern matched,
- the fill-rate scorecard for all 30 fields, so gaps are visible at a glance,
- a form to record the verdict — what's wrong, what the fix is.

Everything reviewers type is saved with full history, so nothing gets
overwritten. Reviewers can each run their own copy and have their results merged
afterwards, with conflicts flagged where two people touched the same pattern.

## Challenges worth mentioning

**There was no answer key.** Nothing told us which patterns were correct. Every
error had to be argued from the data itself — impossible values, fields that
never fill, two patterns that are obviously the same shape.

**The data is wildly lopsided.** One pattern has 2.3 million transactions, most
have four. Random sampling would have surfaced the same few common formats over
and over and never reached the rare ones — which is exactly where the errors
were. So the tool samples a fixed number of examples *per pattern*: a
2.3-million-row pattern gets the same five slots as a four-row one.

**6.8 million rows was too slow to click through.** Ordinary Python tools took
seconds to respond to every filter change, which kills a review session. I loaded
the data into an embedded analytics database (ClickHouse, running inside the
Python process — no server to set up) and pre-computed the summaries. After a
one-time 10-second build, everything is instant.

**Several reviewers, one set of conclusions.** Reviewing 1,832 patterns across a
team generates the same underlying bug described a dozen different ways.
Consolidating 35 raw observations into 30 distinct defects and 4 root causes was
a real part of the work — an unmerged list would have sent the model owners
chasing symptoms.

## Tools used

Python · pandas · numpy · [chdb](https://github.com/chdb-io/chdb) (embedded
ClickHouse) · Streamlit · SQLite · Parquet · uv

## Running it

```bash
uv sync
uv run build_review_db.py                  # put the corpus parquet here first
REVIEWER="your name" uv run streamlit run review_app.py
```

[QUICKSTART.md](QUICKSTART.md) for the reviewer walkthrough, [SHARING.md](SHARING.md)
for running it across a team.

## A note on the data

The transaction corpus is real customer data and is **not** in this repository —
it's excluded, along with the database built from it. Transaction examples shown
here and in the findings have identifying values masked.
