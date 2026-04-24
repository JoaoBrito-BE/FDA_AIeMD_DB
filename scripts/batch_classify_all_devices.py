"""
Batch classify all FDA AI devices by evidence type (A/B/C/D).

Reads  : data/fda_ai_devices.csv
Writes : data/fda_classifications.db  (SQLite)

Resumes automatically — devices already in the DB are skipped.
Run with --reset to drop and rebuild from scratch.

Usage
-----
    python scripts/batch_classify_all_devices.py
    python scripts/batch_classify_all_devices.py --limit 100
    python scripts/batch_classify_all_devices.py --reset
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evidence_classifier import EvidenceClassifier
from src.pdf_processor import PDFProcessor

# ── Constants ────────────────────────────────────────────────────────────────

CSV_PATH = Path("data/fda_ai_devices.csv")
DB_PATH  = Path("data/fda_classifications.db")

RATE_LIMIT_SECONDS = 2.0   # delay between HTTP requests to FDA servers

FETCH_OK         = "ok"
FETCH_FAILED     = "fetch_failed"
FETCH_EMPTY      = "pdf_empty"
CLASSIFY_ERROR   = "classify_error"

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.WARNING,          # suppress pdfplumber/requests noise
    format="%(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("batch_classify")

# ── Database ─────────────────────────────────────────────────────────────────

_DDL_CLASSIFICATIONS = """
CREATE TABLE IF NOT EXISTS classifications (
    k_number          TEXT PRIMARY KEY,
    device            TEXT,
    company           TEXT,
    panel             TEXT,
    product_code      TEXT,
    decision_date     TEXT,
    decision_year     INTEGER,
    category          TEXT,
    label             TEXT,
    confidence        TEXT,
    metric_count      INTEGER,
    dataset_size      INTEGER,
    dataset_unit      TEXT,
    qualitative_signals  TEXT,
    technical_signals    TEXT,
    equivalence_signals  TEXT,
    fetch_status      TEXT NOT NULL,
    error_message     TEXT,
    processed_at      TEXT NOT NULL
);
"""

_DDL_RUN_LOG = """
CREATE TABLE IF NOT EXISTS run_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    total        INTEGER,
    processed    INTEGER,
    succeeded    INTEGER,
    failed       INTEGER
);
"""

_DDL_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_category ON classifications(category);",
    "CREATE INDEX IF NOT EXISTS idx_year     ON classifications(decision_year);",
    "CREATE INDEX IF NOT EXISTS idx_status   ON classifications(fetch_status);",
]


def open_db(db_path: Path, reset: bool = False) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if reset and db_path.exists():
        db_path.unlink()
        logger.info("Existing database removed (--reset).")

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL;")   # safe concurrent reads
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute(_DDL_CLASSIFICATIONS)
    conn.execute(_DDL_RUN_LOG)
    for idx in _DDL_INDEXES:
        conn.execute(idx)
    conn.commit()
    return conn


def get_processed_k_numbers(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT k_number FROM classifications").fetchall()
    return {r[0] for r in rows}


def insert_result(conn: sqlite3.Connection, row: dict) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO classifications (
            k_number, device, company, panel, product_code,
            decision_date, decision_year,
            category, label, confidence, metric_count,
            dataset_size, dataset_unit,
            qualitative_signals, technical_signals, equivalence_signals,
            fetch_status, error_message, processed_at
        ) VALUES (
            :k_number, :device, :company, :panel, :product_code,
            :decision_date, :decision_year,
            :category, :label, :confidence, :metric_count,
            :dataset_size, :dataset_unit,
            :qualitative_signals, :technical_signals, :equivalence_signals,
            :fetch_status, :error_message, :processed_at
        )
        """,
        row,
    )
    conn.commit()


def log_run(
    conn: sqlite3.Connection,
    started_at: str,
    total: int,
    processed: int,
    succeeded: int,
    failed: int,
) -> None:
    conn.execute(
        """
        INSERT INTO run_log (started_at, finished_at, total, processed, succeeded, failed)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (started_at, _utcnow(), total, processed, succeeded, failed),
    )
    conn.commit()

# ── Processing ────────────────────────────────────────────────────────────────

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_year(date_str: str) -> Optional[int]:
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(str(date_str).strip(), fmt).year
        except ValueError:
            continue
    return None


def process_device(
    k_number: str,
    meta: dict,
    processor: PDFProcessor,
    classifier: EvidenceClassifier,
) -> dict:
    """Fetch PDF, classify, return a DB-ready dict. Never raises."""
    base = {
        "k_number":       k_number,
        "device":         meta.get("device", ""),
        "company":        meta.get("company", ""),
        "panel":          meta.get("panel", ""),
        "product_code":   meta.get("product_code", ""),
        "decision_date":  meta.get("decision_date", ""),
        "decision_year":  meta.get("decision_year"),
        "category":       None,
        "label":          None,
        "confidence":     None,
        "metric_count":   None,
        "dataset_size":   None,
        "dataset_unit":   None,
        "qualitative_signals": None,
        "technical_signals":   None,
        "equivalence_signals": None,
        "fetch_status":   None,
        "error_message":  None,
        "processed_at":   _utcnow(),
    }

    try:
        extracted = processor.extract_with_metadata(k_number)
    except Exception as exc:
        return {**base, "fetch_status": FETCH_FAILED, "error_message": str(exc)}

    if not extracted["success"]:
        status = FETCH_EMPTY if extracted["char_count"] == 0 else FETCH_FAILED
        return {**base, "fetch_status": status}

    try:
        clf = classifier.classify(extracted["text"], k_number)
    except Exception as exc:
        return {**base, "fetch_status": CLASSIFY_ERROR, "error_message": str(exc)}

    clf_dict = clf.to_dict()
    return {
        **base,
        "category":             clf_dict["category"],
        "label":                clf_dict["label"],
        "confidence":           clf_dict["confidence"],
        "metric_count":         clf_dict["metric_count"],
        "dataset_size":         clf_dict["dataset_size"],
        "dataset_unit":         clf_dict["dataset_unit"],
        "qualitative_signals":  clf_dict["qualitative_signals"],
        "technical_signals":    clf_dict["technical_signals"],
        "equivalence_signals":  clf_dict["equivalence_signals"],
        "fetch_status":         FETCH_OK,
    }

# ── Summary ───────────────────────────────────────────────────────────────────

def print_summary(conn: sqlite3.Connection) -> None:
    total = conn.execute("SELECT COUNT(*) FROM classifications").fetchone()[0]
    print(f"\n{'='*60}")
    print(f"Total rows in DB : {total}")

    print("\nBy fetch status:")
    for status, n in conn.execute(
        "SELECT fetch_status, COUNT(*) FROM classifications GROUP BY fetch_status ORDER BY 2 DESC"
    ):
        print(f"  {status:<20} {n:5d}")

    print("\nBy evidence category (successful only):")
    for cat, label, n in conn.execute(
        """
        SELECT category, label, COUNT(*)
        FROM   classifications
        WHERE  fetch_status = 'ok'
        GROUP  BY category
        ORDER  BY category
        """
    ):
        pct = 100 * n / total if total else 0
        print(f"  {cat}  {label:<20} {n:5d}  ({pct:.1f}%)")

    print(f"{'='*60}")

# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=None, metavar="N",
                        help="Stop after processing N devices (useful for testing).")
    parser.add_argument("--reset", action="store_true",
                        help="Drop existing DB and start from scratch.")
    args = parser.parse_args()

    if not CSV_PATH.exists():
        sys.exit(f"ERROR: {CSV_PATH} not found. Run from the project root.")

    df = pd.read_csv(CSV_PATH)
    df.columns = df.columns.str.strip()

    conn = open_db(DB_PATH, reset=args.reset)
    already_done = get_processed_k_numbers(conn)

    pending = df[~df["Submission Number"].isin(already_done)].copy()
    if args.limit:
        pending = pending.head(args.limit)

    total_in_csv = len(df)
    skipped      = len(df) - len(df[~df["Submission Number"].isin(already_done)])
    to_process   = len(pending)

    print(f"FDA AI devices  : {total_in_csv}")
    print(f"Already in DB   : {skipped}")
    print(f"To process now  : {to_process}")
    print(f"Output DB       : {DB_PATH}\n")

    if to_process == 0:
        print("Nothing to do — all devices are already classified.")
        print_summary(conn)
        conn.close()
        return

    processor  = PDFProcessor(timeout=30)
    classifier = EvidenceClassifier()

    started_at = _utcnow()
    succeeded = failed = 0
    last_fetch_time: float = 0.0

    with tqdm(total=to_process, unit="device", desc="Classifying", dynamic_ncols=True) as pbar:
        for _, row in pending.iterrows():
            k_number = str(row["Submission Number"]).strip()

            meta = {
                "device":        str(row.get("Device", "")),
                "company":       str(row.get("Company", "")),
                "panel":         str(row.get("Panel (Lead)", "")),
                "product_code":  str(row.get("Primary Product Code", "")),
                "decision_date": str(row.get("Date of Final Decision", "")),
                "decision_year": _parse_year(str(row.get("Date of Final Decision", ""))),
            }

            # Enforce rate limit only between actual HTTP fetches
            elapsed = time.monotonic() - last_fetch_time
            if elapsed < RATE_LIMIT_SECONDS:
                time.sleep(RATE_LIMIT_SECONDS - elapsed)

            result = process_device(k_number, meta, processor, classifier)
            last_fetch_time = time.monotonic()

            insert_result(conn, result)

            if result["fetch_status"] == FETCH_OK:
                succeeded += 1
                pbar.set_postfix(
                    cat=result["category"],
                    conf=result["confidence"],
                    ok=succeeded,
                    fail=failed,
                )
            else:
                failed += 1
                pbar.set_postfix(
                    status=result["fetch_status"],
                    ok=succeeded,
                    fail=failed,
                )

            pbar.update(1)

    log_run(conn, started_at, to_process, succeeded + failed, succeeded, failed)

    print(f"\nDone. Succeeded: {succeeded}  |  Failed: {failed}")
    print_summary(conn)
    conn.close()


if __name__ == "__main__":
    main()
