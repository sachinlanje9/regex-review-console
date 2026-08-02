"""Regex review console: chdb (read) + SQLite (feedback).

    uv run build_review_db.py                 # once, builds ./chdb_review/
    uv run streamlit run review_app.py

Per regex the reviewer records: correction required?, corrected regex,
feature_map gaps, updated category / sub-category. Everything lands in
review_feedback.db (see feedback_store.py).
"""

import os
import threading

import pandas as pd
import streamlit as st
from chdb import session as chs

import feedback_store as fb
from build_review_db import CONTEXT_COLS, DB_DIR, FEATURE_COLS

st.set_page_config(page_title="Regex Review", layout="wide", page_icon="🔍")

SAMPLE_CAP = 5000  # per-regex scan cap before de-duping notes; keeps memory bounded


# --------------------------------------------------------------------------- data
@st.cache_resource
def _sess():
    fb.init()
    return chs.Session(DB_DIR)


_QLOCK = threading.Lock()  # one chdb session, many browser sessions -> serialize queries


def q(sql: str) -> pd.DataFrame:
    with _QLOCK:
        return _sess().query(sql, "DataFrame")


def lit(s: str) -> str:
    return "'" + str(s).replace("\\", "\\\\").replace("'", "\\'") + "'"


def in_list(vals) -> str:
    return "(" + ",".join(lit(v) for v in vals) + ")"


@st.cache_data(show_spinner=False)
def load_summary() -> pd.DataFrame:
    return q("SELECT * FROM review.regex_summary ORDER BY txn_count DESC")


@st.cache_data(show_spinner=False)
def load_facets() -> pd.DataFrame:
    return q("SELECT * FROM review.regex_facets")


@st.cache_data(show_spinner=False)
def taxonomy() -> tuple[list[str], list[str]]:
    c = q("SELECT DISTINCT category FROM review.regex_facets ORDER BY category")
    s = q("SELECT DISTINCT sub_category FROM review.regex_facets ORDER BY sub_category")
    return (
        [x for x in c["category"].tolist() if x],
        [x for x in s["sub_category"].tolist() if x],
    )


def samples(hashes, tagsets, ttypes, cats, n: int, distinct_notes: bool) -> pd.DataFrame:
    """Up to `n` transactions per regex, honouring the sidebar filters."""
    if not len(hashes):
        return pd.DataFrame()
    where = [f"regex_hash IN ({','.join(str(h) for h in hashes)})"]
    if tagsets:
        where.append(f"tags IN {in_list(tagsets)}")
    if ttypes:
        where.append(f"transaction_type IN {in_list(ttypes)}")
    if cats:
        where.append(f"category IN {in_list(cats)}")
    cols = ", ".join(["regex_hash", "row_id", *CONTEXT_COLS, *FEATURE_COLS])
    w = " AND ".join(where)
    if distinct_notes:
        # one LIMIT BY per SELECT -> nest: cap scan, de-dupe notes, then take n.
        scan = f"SELECT {cols} FROM review.corpus WHERE {w} LIMIT {SAMPLE_CAP} BY regex_hash"
        uniq = f"SELECT * FROM ({scan}) LIMIT 1 BY regex_hash, transaction_note"
        sql = f"SELECT * FROM ({uniq}) LIMIT {n} BY regex_hash"
    else:
        sql = f"SELECT {cols} FROM review.corpus WHERE {w} LIMIT {n} BY regex_hash"
    df = q(sql)
    # uint64 hashes overflow arrow's int64 on display
    df["regex_hash"] = df["regex_hash"].astype(str)
    return df


def fill_rates(regex_hash: int, tagsets, ttypes) -> pd.DataFrame:
    """Non-empty % per feature column for one regex, under the current filters."""
    where = [f"regex_hash = {regex_hash}"]
    if tagsets:
        where.append(f"tags IN {in_list(tagsets)}")
    if ttypes:
        where.append(f"transaction_type IN {in_list(ttypes)}")
    sel = ", ".join(f"round(100*avg(if({c} != '', 1, 0)), 1) AS {c}" for c in FEATURE_COLS)
    df = q(f"SELECT {sel} FROM review.corpus WHERE {' AND '.join(where)}")
    out = df.T.reset_index()
    out.columns = ["feature", "filled_pct"]
    return out.sort_values("filled_pct", ascending=False, ignore_index=True)


# --------------------------------------------------------------------------- filters
summary, facets = load_summary(), load_facets()
CATS, SUBCATS = taxonomy()
status = fb.status_map()

st.sidebar.header("Filters")
reviewer = st.sidebar.text_input(
    "Reviewer", value=st.session_state.get("reviewer", os.environ.get("REVIEWER", "")),
    help="stamped on every feedback row; set REVIEWER env var to pre-fill",
)
st.session_state["reviewer"] = reviewer

tag_mode = st.sidebar.radio(
    "Tag filter mode", ["tag combination (exact)", "individual tags"], horizontal=False
)
all_tagsets = sorted(facets["tags"].unique())
all_tags = sorted({t.strip() for ts in all_tagsets for t in ts.split(",") if t.strip()})

if tag_mode.startswith("tag combination"):
    picked_sets = st.sidebar.multiselect("Tag combination", all_tagsets)
    tagsets = picked_sets
else:
    picked_tags = st.sidebar.multiselect("Tags", all_tags)
    how = st.sidebar.radio("Match", ["any of", "all of"], horizontal=True)
    if picked_tags:
        def hit(ts: str) -> bool:
            toks = {t.strip() for t in ts.split(",")}
            return set(picked_tags) <= toks if how == "all of" else bool(set(picked_tags) & toks)
        tagsets = [ts for ts in all_tagsets if hit(ts)]
    else:
        tagsets = []

ttypes = st.sidebar.multiselect("Transaction type", sorted(facets["transaction_type"].unique()))
cats = st.sidebar.multiselect("Category", CATS)
regex_search = st.sidebar.text_input("Regex contains", placeholder="e.g. UPI/ or \\d{12}")
review_filter = st.sidebar.selectbox(
    "Review status", ["all", "unreviewed", "reviewed", "correction required"]
)
per_regex = st.sidebar.slider("Transactions per regex", 1, 50, 5)
distinct_notes = st.sidebar.checkbox("Distinct notes only", value=True)
st.sidebar.caption(f"scan cap {SAMPLE_CAP:,}/regex when de-duping notes")

# regex-level filtering, done on the small facet cube
f = facets
if tagsets:
    f = f[f["tags"].isin(tagsets)]
if ttypes:
    f = f[f["transaction_type"].isin(ttypes)]
if cats:
    f = f[f["category"].isin(cats)]
matched = f.groupby("regex_hash", as_index=False)["txn_count"].sum()

view = summary.merge(matched.rename(columns={"txn_count": "matched_txns"}), on="regex_hash")
if regex_search:
    view = view[view["regex"].str.contains(regex_search, case=False, regex=False, na=False)]
view["regex_key"] = view["regex"].map(fb.key_of)
view["status"] = view["regex_key"].map(lambda k: status.get(k, ("unreviewed", 0))[0])
view["fix?"] = view["regex_key"].map(lambda k: bool(status.get(k, ("", 0))[1]))

if review_filter == "unreviewed":
    view = view[view["status"] == "unreviewed"]
elif review_filter == "reviewed":
    view = view[view["status"] != "unreviewed"]
elif review_filter == "correction required":
    view = view[view["fix?"]]
view = view.sort_values("matched_txns", ascending=False, ignore_index=True)

# --------------------------------------------------------------------------- header
c1, c2, c3, c4 = st.columns(4)
c1.metric("Regexes (filtered)", f"{len(view):,}")
c2.metric("Transactions (filtered)", f"{int(view['matched_txns'].sum()):,}")
c3.metric("Reviewed", f"{len(status):,} / {len(summary):,}")
c4.metric("Marked for correction", f"{sum(1 for _, cr in status.values() if cr):,}")

tab_review, tab_browse, tab_fb = st.tabs(["Review", "Browse samples", "Feedback store"])

# --------------------------------------------------------------------------- review
with tab_review:
    if view.empty:
        st.warning("No regex matches these filters.")
        st.stop()

    left, right = st.columns([1, 1.35], gap="medium")

    with left:
        st.subheader("Regexes")
        grid = view[["regex", "matched_txns", "txn_count", "status", "fix?", "n_tagsets", "tagsets"]]
        ev = st.dataframe(
            grid,
            width='stretch',
            height=420,
            hide_index=False,
            on_select="rerun",
            selection_mode="single-row",
            column_config={
                "regex": st.column_config.TextColumn("regex", width="large"),
                "matched_txns": st.column_config.NumberColumn("txns (filtered)", format="%d"),
                "txn_count": st.column_config.NumberColumn("txns (total)", format="%d"),
            },
        )
        rows = ev.selection.rows if ev and ev.selection else []
        idx = rows[0] if rows else min(st.session_state.get("row_idx", 0), len(view) - 1)
        nav1, nav2, _ = st.columns([1, 1, 3])
        if nav1.button("◀ Prev", width='stretch'):
            idx = max(0, idx - 1)
        if nav2.button("Next ▶", width='stretch'):
            idx = min(len(view) - 1, idx + 1)
        st.session_state["row_idx"] = idx

    row = view.iloc[idx]
    rx, rhash, rkey = row["regex"], int(row["regex_hash"]), row["regex_key"]

    with right:
        st.subheader(f"Regex #{idx + 1} of {len(view)}")
        st.code(rx, language=None, wrap_lines=True)
        m1, m2, m3 = st.columns(3)
        m1.metric("txns (filtered)", f"{int(row['matched_txns']):,}")
        m2.metric("txns (total)", f"{int(row['txn_count']):,}")
        m3.metric("tag combos", int(row["n_tagsets"]))
        st.caption(
            f"**tags:** {row['tagsets']}  \n**types:** {row['txn_types']}  \n"
            f"**category:** {row['categories']}  ·  **sub:** {row['sub_categories']}"
        )

    st.markdown("#### Sample transactions")
    smp = samples([rhash], tagsets, ttypes, cats, per_regex, distinct_notes)
    if smp.empty:
        st.info("No transactions for this regex under the current filters.")
    else:
        show_all = st.checkbox("Show all feature columns (incl. always-empty)", value=False)
        feats = FEATURE_COLS if show_all else [c for c in FEATURE_COLS if (smp[c] != "").any()]
        cols = ["transaction_note", "transaction_type", "amount", "tags", "category",
                "sub_category", "transaction_channel", *feats]
        st.dataframe(smp[cols], width='stretch', height=min(60 + 35 * len(smp), 420),
                     column_config={"transaction_note": st.column_config.TextColumn(width="large")})
        with st.expander("Row detail (all 41 fields)"):
            i = st.number_input("row", 0, len(smp) - 1, 0, key=f"det_{rhash}")
            st.dataframe(smp.iloc[int(i)].astype(str).to_frame("value"),
                         width='stretch', height=600)

    gap_col, form_col = st.columns([1, 1.35], gap="medium")
    with gap_col:
        st.markdown("#### Feature fill rate (this regex, filtered)")
        fr = fill_rates(rhash, tagsets, ttypes)
        empty_feats = fr[fr["filled_pct"] == 0]["feature"].tolist()
        st.dataframe(fr[fr["filled_pct"] > 0], width='stretch', height=260,
                     column_config={"filled_pct": st.column_config.ProgressColumn(
                         "filled %", min_value=0, max_value=100, format="%.1f%%")})
        st.caption(f"never populated ({len(empty_feats)}): " + ", ".join(empty_feats[:12]) +
                   ("…" if len(empty_feats) > 12 else ""))

    prev = fb.get(regex=rx) or {}
    with form_col:
        st.markdown("#### Feedback")
        if prev:
            st.caption(f"last saved {prev['updated_at']} by {prev['reviewer'] or '—'}")
        with st.form(f"fbform_{rkey}", clear_on_submit=False):
            correction_required = st.checkbox(
                "Regex correction required",
                value=bool(prev.get("correction_required", 0)), key=f"cr_{rkey}",
            )
            regex_corrected = st.text_area(
                "Regex corrected (paste corrected pattern)",
                value=prev.get("regex_corrected", ""), height=110, key=f"rc_{rkey}",
            )
            gaps = st.multiselect(
                "feature_map gaps",
                options=FEATURE_COLS,
                default=[g for g in (prev.get("feature_map_gaps") or "").split(", ") if g in FEATURE_COLS],
                key=f"gap_{rkey}",
                help="fields the regex should capture but does not",
            )
            # keep previously-typed custom values selectable
            cat_opts = [""] + CATS + ([prev["updated_category"]]
                                      if prev.get("updated_category") and prev["updated_category"] not in CATS else [])
            sub_opts = [""] + SUBCATS + ([prev["updated_subcategory"]]
                                         if prev.get("updated_subcategory") and prev["updated_subcategory"] not in SUBCATS else [])
            cc1, cc2 = st.columns(2)
            upd_cat = cc1.selectbox(
                "updated_category", cat_opts, accept_new_options=True,
                index=cat_opts.index(prev.get("updated_category") or ""), key=f"uc_{rkey}",
            )
            upd_sub = cc2.selectbox(
                "updated_subcategory", sub_opts, accept_new_options=True,
                index=sub_opts.index(prev.get("updated_subcategory") or ""), key=f"us_{rkey}",
            )
            notes = st.text_area("Notes", value=prev.get("notes", ""), height=80, key=f"nt_{rkey}")
            stat = st.radio("Status", ["pending", "done", "skipped"], horizontal=True,
                            index=["pending", "done", "skipped"].index(prev.get("status", "pending")),
                            key=f"st_{rkey}")
            saved = st.form_submit_button("💾 Save feedback", type="primary", width='stretch')
        if saved:
            fb.save(
                regex=rx, correction_required=correction_required, regex_corrected=regex_corrected,
                feature_map_gaps=gaps, updated_category=upd_cat, updated_subcategory=upd_sub,
                notes=notes, status=stat, reviewer=reviewer,
            )
            st.success("Saved.")
            st.rerun()

# --------------------------------------------------------------------------- browse
with tab_browse:
    st.caption(f"{per_regex} transaction(s) per regex across {len(view):,} filtered regexes")
    b1, b2 = st.columns([1, 3])
    top = b1.number_input("Max regexes to pull", 1, 500, 50, key="browse_n")
    show_rx = b2.checkbox("Show full regex text in the transactions table", value=False)
    hashes = view["regex_hash"].head(int(top)).tolist()
    bulk = samples(hashes, tagsets, ttypes, cats, per_regex, distinct_notes)
    if bulk.empty:
        st.info("Nothing to show.")
    else:
        # R1, R2, ... ties every transaction row to its row in the feedback editor below
        order = view.head(int(top))[["regex_hash", "regex", "matched_txns"]].reset_index(drop=True)
        order["R"] = ["R" + str(i + 1) for i in order.index]
        order["hash_str"] = order["regex_hash"].astype(str)
        rid = dict(zip(order["hash_str"], order["R"]))
        bulk["R"] = bulk["regex_hash"].map(rid)
        bulk = bulk.sort_values("R", key=lambda s: s.str[1:].astype(int), ignore_index=True)

        feats = [c for c in FEATURE_COLS if (bulk[c] != "").any()]
        cols = ["R", *(["regex"] if show_rx else []), "transaction_note", "transaction_type",
                "amount", "tags", "category", "sub_category", *feats]
        st.dataframe(bulk[cols], width="stretch", height=430,
                     column_config={"R": st.column_config.TextColumn(width="small")})
        st.download_button("⬇ CSV (transactions)", bulk.to_csv(index=False).encode(),
                           "regex_samples.csv", "text/csv")

        # ----------------------------------------------------------- inline feedback
        st.markdown("#### Feedback — one row per regex, edit inline then save")
        st.caption("`feature_map gaps`: comma-separated feature names (validated on save). "
                   "Need a category that is not in the list? Use the Review tab (free-text allowed).")
        ed = order[["R", "regex", "matched_txns"]].rename(columns={"matched_txns": "txns"})
        prior = [fb.get(regex=rx) or {} for rx in ed["regex"]]
        ed["correction_required"] = [bool(p.get("correction_required", 0)) for p in prior]
        ed["regex_corrected"] = [p.get("regex_corrected", "") for p in prior]
        ed["feature_map_gaps"] = [p.get("feature_map_gaps", "") for p in prior]
        ed["updated_category"] = [p.get("updated_category", "") for p in prior]
        ed["updated_subcategory"] = [p.get("updated_subcategory", "") for p in prior]
        ed["notes"] = [p.get("notes", "") for p in prior]
        ed["status"] = [p.get("status", "pending") for p in prior]

        # editor state must reset when the regex set changes, else edits land on wrong rows
        ekey = f"editor_{len(ed)}_{hash(tuple(ed['regex']))}"
        cat_opts = sorted({"", *CATS, *(x for x in ed["updated_category"] if x)})
        sub_opts = sorted({"", *SUBCATS, *(x for x in ed["updated_subcategory"] if x)})
        edited = st.data_editor(
            ed, key=ekey, width="stretch", height=430, hide_index=True,
            disabled=["R", "regex", "txns"],
            column_config={
                "R": st.column_config.TextColumn(width="small"),
                "regex": st.column_config.TextColumn("regex", width="large"),
                "txns": st.column_config.NumberColumn(format="%d", width="small"),
                "correction_required": st.column_config.CheckboxColumn("fix?", width="small"),
                "regex_corrected": st.column_config.TextColumn("regex corrected", width="medium"),
                "feature_map_gaps": st.column_config.TextColumn(
                    "feature_map gaps", width="medium",
                    help="comma-separated, e.g. counter_party, rrn"),
                "updated_category": st.column_config.SelectboxColumn(options=cat_opts, width="medium"),
                "updated_subcategory": st.column_config.SelectboxColumn(options=sub_opts, width="medium"),
                "notes": st.column_config.TextColumn(width="medium"),
                "status": st.column_config.SelectboxColumn(
                    options=["pending", "done", "skipped"], width="small"),
            },
        )

        changed = edited.compare(ed) if len(edited) == len(ed) else edited
        dirty = sorted(set(changed.index.tolist()))
        s1, s2 = st.columns([1, 3])
        save_all = s1.button(f"💾 Save {len(dirty)} edited row(s)", type="primary",
                             disabled=not dirty, width="stretch")
        s2.caption("edited: " + (", ".join(edited.loc[dirty, "R"]) if dirty else "nothing yet"))
        if save_all:
            rows, bad = [], []
            for i in dirty:
                r = edited.loc[i]
                gaps = [g.strip() for g in str(r["feature_map_gaps"]).split(",") if g.strip()]
                bad += [g for g in gaps if g not in FEATURE_COLS]
                rows.append({
                    "regex": r["regex"], "correction_required": bool(r["correction_required"]),
                    "regex_corrected": r["regex_corrected"], "feature_map_gaps": gaps,
                    "updated_category": r["updated_category"],
                    "updated_subcategory": r["updated_subcategory"],
                    "notes": r["notes"], "status": r["status"], "reviewer": reviewer,
                })
            n = fb.save_many(rows)
            if bad:
                st.warning("saved, but these gap names are not feature columns: " +
                           ", ".join(sorted(set(bad))))
            st.success(f"Saved {n} regex row(s).")
            st.rerun()

# --------------------------------------------------------------------------- store
with tab_fb:
    df = fb.all_feedback()
    st.caption(f"{len(df)} regexes reviewed · {fb.DB_PATH}")
    if df.empty:
        st.info("No feedback yet.")
    else:
        st.dataframe(df, width='stretch', height=520)
        d1, d2 = st.columns(2)
        d1.download_button("⬇ CSV", df.to_csv(index=False).encode(),
                           "regex_feedback.csv", "text/csv", width='stretch')
        d2.download_button("⬇ JSON", df.to_json(orient="records", indent=2).encode(),
                           "regex_feedback.json", "application/json", width='stretch')
        pick = st.selectbox("Inspect history for", df["regex"].tolist())
        if pick:
            st.dataframe(fb.history(regex=pick), width='stretch', height=240)
            if st.button("🗑 Delete this feedback row"):
                fb.delete(regex=pick)
                st.rerun()
