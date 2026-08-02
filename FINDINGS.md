# Regex & Feature-Map Review — Findings

Review of model-generated regexes and `group_feature_map` output over a corpus of
**6,811,926 transactions / 1,832 distinct regexes**, extracting 30 feature
columns per row.

Two review passes:

| Pass | Transactions | Regexes |
|---|---|---|
| **DEBIT** | 4,762,821 | 829 |
| **CREDIT** | 2,049,105 | 1,201 |

35 raw observations, consolidated into **30 distinct defects** across 4 themes.
Each has the observed behaviour, why it matters, and the recommended fix.

**Four defects were raised independently in both passes** — marked `BOTH` below.
Those are the systemic ones: they aren't a quirk of how debits or credits are
written, they're how the model builds patterns. Fix those first.

> Examples below are drawn from real transaction notes with account, reference
> and counterparty values masked. Unmasked examples stay in the internal
> review DB.

**Legend**
Scope: `DEBIT` · `CREDIT` · `BOTH` (found in both passes)
Impact: `CLUSTER` inflates or fragments regex clusters ·
`EXTRACT` produces a wrong or missing field value ·
`RULES` needs a business rule, not a regex change

---

## A. Regex structure & clustering

### A1. Missing start/end anchors — `BOTH` `CLUSTER`
Many regexes lack `^` and/or `$`. Without them a pattern matches a *fragment*
of a note, so unrelated transactions collapse into one cluster and genuinely
matching ones land elsewhere.

**Fix:** add anchors wherever the pattern is meant to describe the whole note.

### A2. Duplicate regexes for the same format — `BOTH` `CLUSTER`
The same structure gets multiple regexes that differ only in a quantifier or a
minor variation, doubling the cluster count and the maintenance surface.

```
(?-i:(ATM))/CWRR/(?-i:(\d{12}))/(?-i:(X{4,}\d{2,}))
(?-i:(ATM))/CWRR/(?-i:(\d{1,4}))/(?-i:(X{4,}\d{2,}))
```

**Fix:** generalise the quantifier and merge into one regex.

### A3. Delimiters splitting one transaction across clusters — `BOTH` `CLUSTER`
`/`, `-`, `.`, `:` and spaces are treated as cluster boundaries, so a single
transaction format is spread over several clusters.

**Fix:** decide grouping by the *major* delimiter of the format; minor
delimiters inside a field must not break the group.

### A4. A field split by its own delimiter spawns extra regexes — `CREDIT` `CLUSTER`
When a value legitimately contains a delimiter — e.g. a `cp_vpa` containing
`-` — the split produces a separate regex for what is one format.

**Fix:** allow in-field delimiters inside the capture group rather than
splitting on them.

### A5. `[ ]` used as the delimiter class — `CREDIT` `CLUSTER` `EXTRACT`
Literal `[ ]` is used where the separator is actually variable whitespace or
punctuation, so any real-world spacing variation fails to match.

**Fix:** use `[\s\/\-]` (or the appropriate class) instead of `[ ]`.

### A6. Clusters generated with no valid features — `DEBIT` `CLUSTER`
Some groups are emitted even though no meaningful feature was extracted — they
carry no information and pollute the cluster list.

**Fix:** emit a cluster only when a minimum set of identifying features is
successfully extracted.

---

## B. `group_feature_map` integrity

*All three raised in the CREDIT pass.*

### B1. The same feature appears multiple times in one map — `CREDIT` `EXTRACT`
A single regex maps more than one group to the same feature — repeated
`cp_vpa`, `ref_num`, `mode`, `counter_party`.

**Fix:** enforce one group per feature per regex at map-construction time.

### B2. Group names are not in order of occurrence — `CREDIT` `EXTRACT`
`group_feature_map` ordering does not follow the left-to-right order of the
capture groups in the pattern, so groups and features can be mismatched.

**Fix:** build the map in positional order of the groups.

### B3. Not all feature columns are present in the map — `CREDIT` `EXTRACT`
The map omits feature columns that the schema expects, leaving them
permanently unpopulated with no signal that they were skipped.

**Fix:** emit the full feature set, with explicit nulls for what a given regex
cannot extract — a declared null is auditable, a missing key is not.

---

## C. Field-level extraction

### C1. Incomplete transaction captured before field extraction — `DEBIT` `EXTRACT`
Extraction runs against a fragment rather than the full note.

```
LIEN MARKING FOR NACH/BARBXXXXXXXXXXXXXXX LIENREV
→ counter_party / remark = "MARKING"
```

**Fix:** capture the complete transaction span first, then extract fields from it.

### C2. Counterparty truncated by spaces and special characters — `DEBIT` `EXTRACT`
CP names stop at the first space or symbol the character class doesn't cover.

**Fix:** widen the CP character class to the spaces and special characters that
legitimately occur in names.

### C3. Counterparty split across groups by delimiters — `DEBIT` `EXTRACT`
Delimiters that are part of the actual name break CP into several groups.

**Fix:** keep the full CP in one group when the delimiter belongs to the name.

### C4. Remark not captured in full — `DEBIT` `EXTRACT`
Truncated remarks propagate into wrong categorisation and tagging downstream.

**Fix:** retain the complete descriptive text.

### C5. Remark not retained for second-level categorisation — `CREDIT` `EXTRACT`
Text needed by L2 rules is dropped at L1.

```
REJECT00000101Funds insufficient    → reason text lost before L2 sees it
```

**Fix:** persist the remark through to the L2 stage.

### C6. Ambiguous values forced into a typed field — `DEBIT` `EXTRACT`
When a token could be either a counterparty or a reference number, it is
assigned to one of them anyway.

**Fix:** when confidence is low, put the value in `remark` rather than guessing
a typed field. A wrong CP is worse than an unparsed remark.

### C7. Multiple reference numbers from one transaction — `DEBIT` `EXTRACT`
**Fix:** one `ref_num` per transaction unless the format genuinely carries more.

### C8. Reference number matched as a single digit — `CREDIT` `EXTRACT`
`\d{1,4}`-style groups accept 1-digit values that cannot be real references.

**Fix:** enforce a realistic minimum length for `ref_num`.

### C9. Multiple modes from one transaction — `DEBIT` `EXTRACT`
**Fix:** exactly one `mode` per transaction.

### C10. Invalid datetime patterns — `CREDIT` `EXTRACT`
`\d{6}` and similar are used where a structured date is expected, so no valid
date format is actually enforced.

**Fix:** match explicit date formats instead of bare digit runs.

### C11. Date value classified as a reference number — `CREDIT` `EXTRACT`
```
220626   → ref_num   (should be datetime, 22/06/26)
```
**Fix:** test date patterns before the generic numeric-reference pattern.

### C12. `txn_period` taking nonsensical values — `BOTH` `EXTRACT`
```
txn_period = "26/2459"
```
**Fix:** validate extracted periods against real date/month/year ranges and
reject what doesn't parse.

---

## D. Keyword, flow & business-rule mapping

### D1. Unhandled keywords — `DEBIT` `RULES`
Not recognised, so they fall through and generate spurious clusters:

`CMS` · `CASH` · `SMS CHARGES` · `UPILITE` · `PRCR`

**Fix:** add to the keyword list.

### D2. `CW` mapped to the wrong field — `DEBIT` `RULES`
`CW` denotes **flow**, but is extracted as counterparty or another feature.

**Fix:** map `CW` → `payment_flow`.

### D3. `CR` / `DR` not treated as flow in AEPS transactions — `CREDIT` `RULES`
**Fix:** map CR/DR to flow for AEPS.

### D4. `DR` extracted as a standalone feature — `DEBIT` `RULES`
**Fix:** `DR` always belongs in `remark`.

### D5. Business keywords hardcoded out of counterparty and remark — `CREDIT` `EXTRACT` `RULES`
Words that occur *inside* a legitimate CP or remark are being lifted out as
features — `CR`, `debit`, `inv`, `gst`, `payout`, `deposit`, `refund`, `sal`,
`ret`, and others.

```
IMPS/P2A/5219XXXXXXXX/<MERCHANT NAME>/Paid via CR
→ "CR" pulled out as a feature; the remark loses its meaning
```

**Fix:** only treat these as keywords in a keyword *position*, never mid-name.

### D6. `CASH` hardcoded out of a counterparty name — `CREDIT` `EXTRACT` `RULES`
Same failure as D5, with a concrete cost — a real company name is destroyed:

```
IMPS/P2A/6058XXXXXXXX/INSTANT CASH FZ/DA Vostro F
→ "CASH" extracted as a keyword, CP split around it
```

**Fix:** the CASH keyword rule must not fire inside a counterparty span.

Note this pairs with D1, where `CASH` is listed as an *unhandled* keyword in the
DEBIT pass. Both are true and they are not in conflict: `CASH` needs to be
recognised in a keyword position and ignored inside a name. That distinction is
the actual fix.

### D7. `SALARY` in keyword position — `DEBIT` `RULES`
Should not automatically become a keyword feature.

**Fix:** handle explicitly in L2 with a business rule.

### D8. Reversal keyword at the start of a note not captured — `CREDIT` `EXTRACT` `RULES`
A special keyword leading the note is ignored, so reversals aren't identified.

```
REV 03/02/26 YBDXXXXXXXXXXX
```
Pattern in play:
```
([A-Za-z0-9._@]+(?:[\s,&]+[A-Za-z0-9._@]+)*)/(?-i:(\d{1,4}))/(?-i:(\d{1,4}))[ ]+(?-i:([A-Z]{2,6}\d[A-Z0-9]{5,}))
```

**Fix:** recognise leading special keywords (`REV`, etc.) before generic field
matching.

### D9. Fixed deposits not tagged — `CREDIT` `RULES`
```
FDR 03/9604 CLOSURE
FD CLOSURE03/2808 CREDITED
```
Neither is tagged as a fixed-deposit transaction.

**Fix:** add FD/FDR recognition, tolerant of the missing space in `CLOSURE03`.

---

## Summary

By theme:

| Theme | Defects | Dominant impact |
|---|---|---|
| A. Regex structure & clustering | 6 | Cluster count inflated / fragmented |
| B. `group_feature_map` integrity | 3 | Features silently mismapped or dropped |
| C. Field-level extraction | 12 | Wrong or partial values in typed fields |
| D. Keyword, flow & business rules | 9 | Real names and flows destroyed by keyword rules |
| **Total** | **30** | |

By scope:

| Scope | Defects | |
|---|---|---|
| `BOTH` | 4 | A1, A2, A3, C12 |
| `DEBIT` only | 12 | A6, C1, C2, C3, C4, C6, C7, C9, D1, D2, D4, D7 |
| `CREDIT` only | 14 | A4, A5, B1, B2, B3, C5, C8, C10, C11, D3, D5, D6, D8, D9 |

The `BOTH` group is the most informative. Two independent passes, over different
transaction types and largely different regexes, independently flagged missing
anchors, duplicate patterns, delimiter-driven splitting and broken `txn_period`
values. Those four are properties of how the model generates patterns, not of
the data it was reading.

The DEBIT pass skewed toward *extraction quality* — counterparties and remarks
coming out truncated or mislabelled. The CREDIT pass skewed toward *structural*
problems — `group_feature_map` integrity and keyword rules firing inside field
spans. Worth noting that the CREDIT set has more regexes (1,201) covering fewer
transactions (2.0 M), so it contains more of the fragmented long tail.

## Root causes

Cross-cutting causes, in the order to fix them:

1. **Delimiter handling** — drives A3, A4, A5, C3 and part of A2. One fix,
   five symptoms, and it spans both passes.
2. **Extract-then-classify ordering** — C1, C11, D8: fields are being typed
   before the full span is known.
3. **Keyword rules firing inside spans** — D5, D6: keyword matching needs
   positional context, not substring search.
4. **No validation on extracted values** — C8, C10, C12: nothing checks that a
   date parses or a reference is plausible.
