"""One-time loader: corpus parquet -> local chdb MergeTree, for fast review queries.

    uv run build_review_db.py                             # default parquet
    uv run build_review_db.py "some_other.parquet"        # explicit path
    uv run build_review_db.py --rebuild                   # drop + reload

Creates ./chdb_review/ (a persistent chdb data dir) holding:
    review.corpus         -- every transaction, ORDER BY (regex_hash, tags, transaction_type)
    review.regex_summary  -- one row per regex: counts, tagsets, categories, field fill-rates

The app (review_app.py) only reads these two tables; it never touches the parquet.
"""

import os
import sys
import time

from chdb import session as chs

PARQUET = "baroda_corpus_processed (2).parquet"
DB_DIR = "chdb_review"

# Feature-map columns: everything the regex is supposed to extract.
FEATURE_COLS = [
    "ref_num", "rrn", "utr", "counter_party", "same_party", "cp_vpa", "cp_bank",
    "cp_acc_num", "sp_acc_num", "card_num", "chq_num", "txn_time", "txn_date",
    "txn_period", "datetime", "amount_extracted", "location", "remark", "keyword",
    "payment_flow", "mode", "gl_ref_from", "gl_ref_to", "sol_id_from", "sol_id_to",
    "txn_date_from", "txn_date_to", "txn_period_from", "txn_period_to", "psp",
]

CONTEXT_COLS = [
    "bank_name", "transaction_type", "transaction_note", "amount", "tags",
    "account_type", "category_group", "category", "sub_category",
    "transaction_channel", "regex",
]

ALL_COLS = CONTEXT_COLS + FEATURE_COLS


def build(parquet: str, rebuild: bool = False) -> None:
    sess = chs.Session(DB_DIR)
    sess.query("CREATE DATABASE IF NOT EXISTS review")

    if rebuild:
        sess.query("DROP TABLE IF EXISTS review.corpus")
        sess.query("DROP TABLE IF EXISTS review.regex_summary")

    src = f"file('{parquet}', Parquet)"
    # Nulls -> '' so sorting keys are non-nullable and the UI never renders None.
    cols = ", ".join(f"coalesce({c}, '') AS {c}" for c in ALL_COLS)

    t0 = time.time()
    print(f"loading {parquet} -> review.corpus ...", flush=True)
    sess.query(f"""
        CREATE TABLE IF NOT EXISTS review.corpus
        ENGINE = MergeTree
        ORDER BY (regex_hash, tags, transaction_type)
        SETTINGS index_granularity = 8192
        AS SELECT
            cityHash64(assumeNotNull(regex)) AS regex_hash,
            rowNumberInAllBlocks()           AS row_id,
            {cols}
        FROM {src}
    """)
    n = sess.query("SELECT count() FROM review.corpus", "CSV").bytes().decode().strip()
    print(f"  {n} rows in {time.time() - t0:.1f}s", flush=True)

    # Per-regex rollup, incl. non-empty rate of every feature column -> feature-map gaps.
    fill = ",\n            ".join(
        f"round(100 * avg(if(coalesce({c}, '') != '', 1, 0)), 1) AS fill_{c}"
        for c in FEATURE_COLS
    )
    print("building review.regex_summary ...", flush=True)
    sess.query(f"""
        CREATE TABLE IF NOT EXISTS review.regex_summary
        ENGINE = MergeTree ORDER BY regex_hash
        AS SELECT
            regex_hash,
            any(regex)                                     AS regex,
            count()                                        AS txn_count,
            uniqExact(tags)                                AS n_tagsets,
            arrayStringConcat(arraySort(groupUniqArray(20)(tags)), ' | ')             AS tagsets,
            arrayStringConcat(arraySort(groupUniqArray(transaction_type)), ' | ')     AS txn_types,
            arrayStringConcat(arraySort(groupUniqArray(10)(category)), ' | ')         AS categories,
            arrayStringConcat(arraySort(groupUniqArray(10)(sub_category)), ' | ')     AS sub_categories,
            {fill}
        FROM review.corpus
        GROUP BY regex_hash
    """)
    r = sess.query("SELECT count() FROM review.regex_summary", "CSV").bytes().decode().strip()
    print(f"  {r} regexes", flush=True)

    # Small filter cube: every (regex, tags, txn_type, category) combo the app filters on.
    print("building review.regex_facets ...", flush=True)
    sess.query("""
        CREATE TABLE IF NOT EXISTS review.regex_facets
        ENGINE = MergeTree ORDER BY (regex_hash, tags, transaction_type)
        AS SELECT
            regex_hash, tags, transaction_type, category, sub_category, count() AS txn_count
        FROM review.corpus
        GROUP BY regex_hash, tags, transaction_type, category, sub_category
    """)
    fc = sess.query("SELECT count() FROM review.regex_facets", "CSV").bytes().decode().strip()
    print(f"  {fc} facet rows\ndone in {time.time() - t0:.1f}s -> ./{DB_DIR}/")
    sess.close()


if __name__ == "__main__":
    import glob

    args = [a for a in sys.argv[1:] if a != "--rebuild"]
    path = args[0] if args else PARQUET
    if not os.path.exists(path) and not args:
        found = sorted(glob.glob("*.parquet"))  # reviewers often rename the corpus
        if len(found) == 1:
            path = found[0]
            print(f"using {path}")
        elif found:
            sys.exit("several parquet files here, pass one:\n  " + "\n  ".join(found))
    if not os.path.exists(path):
        sys.exit(f"parquet not found: {path}")
    build(path, rebuild="--rebuild" in sys.argv)
