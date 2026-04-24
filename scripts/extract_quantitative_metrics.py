"""
Extract per-metric performance values from Category A FDA 510(k) submissions.

Creates:  quantitative_metrics table  (one row per metric occurrence)
Updates:  classifications.dataset_size / dataset_unit where currently NULL
          classifications.study_type (new column, added on first run)
Exports:  data/quantitative_metrics_extracted.csv

Run from the FDA_AIeMD_DB directory:
    python scripts/extract_quantitative_metrics.py
"""
import csv
import os
import re
import sqlite3
import sys
import time
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.keyword_analyzer import PerformanceMetricsExtractor
from src.pdf_processor import PDFProcessor

DB_PATH = 'data/fda_classifications.db'
CSV_OUT = 'data/quantitative_metrics_extracted.csv'
RATE_LIMIT_S = 2.0

# Plausible value ranges per metric type (used to filter junk extractions).
# Lower bounds exclude exact-zero and near-zero false positives; AUC < 0.5
# is worse than chance and impossible for a cleared device.
_VALUE_RANGES = {
    'sensitivity': (1, 100),
    'specificity': (1, 100),
    'accuracy':    (1, 100),
    'precision':   (1, 100),
    'ppv':         (1, 100),
    'npv':         (1, 100),
    'auc':         (0.5, 1),  # <0.5 worse than chance → extraction artifact
}

_STUDY_TYPE_PATTERNS = [
    (r'\bprospective\b',    'prospective'),
    (r'\bretrospective\b',  'retrospective'),
    (r'\breader[- ]study\b', 'reader_study'),
    (r'\bobserver[- ]study\b', 'observer_study'),
    (r'\bcross[- ]sectional\b', 'cross_sectional'),
    (r'\brandomized\b',     'randomized'),
]

# Matches "95% CI: X–Y" or ranges like "(X, Y)" or "[X, Y]" or "X–Y"
_CI_RANGE_RE = re.compile(
    r'(?:95\s*%\s*(?:CI|confidence interval)[:\s,]+)?'
    r'[\[\(]?\s*([0-9]+\.?[0-9]*)\s*[,\-–]\s*([0-9]+\.?[0-9]*)\s*[\]\)]?',
    re.IGNORECASE,
)


def detect_study_type(text: str) -> Optional[str]:
    text_lower = text.lower()
    for pattern, label in _STUDY_TYPE_PATTERNS:
        if re.search(pattern, text_lower):
            return label
    return None


def _value_is_plausible(metric_type: str, value: float) -> bool:
    lo, hi = _VALUE_RANGES.get(metric_type, (0, 100))
    return lo <= value <= hi


def extract_ci(context: str, metric_value: float) -> Tuple[Optional[float], Optional[float]]:
    """
    Search context snippet for a confidence interval surrounding metric_value.
    Returns (ci_lower, ci_upper) or (None, None).
    """
    for m in _CI_RANGE_RE.finditer(context):
        try:
            v1, v2 = float(m.group(1)), float(m.group(2))
        except (ValueError, IndexError):
            continue
        lo, hi = min(v1, v2), max(v1, v2)
        # Accept as CI only if metric_value lies within or very close to [lo, hi]
        if lo <= metric_value <= hi or (metric_value - hi) < 2 or (lo - metric_value) < 2:
            if lo != hi and (hi - lo) < 50:   # sanity: interval width < 50 pp
                return lo, hi
    return None, None


def setup_db(conn: sqlite3.Connection) -> None:
    conn.execute('''
        CREATE TABLE IF NOT EXISTS quantitative_metrics (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            k_number     TEXT NOT NULL,
            metric_type  TEXT NOT NULL,
            metric_value REAL NOT NULL,
            ci_lower     REAL,
            ci_upper     REAL,
            context      TEXT,
            FOREIGN KEY (k_number) REFERENCES classifications(k_number)
        )
    ''')
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_qm_k ON quantitative_metrics(k_number)'
    )
    # Add study_type column to classifications if missing
    existing = {row[1] for row in conn.execute('PRAGMA table_info(classifications)')}
    if 'study_type' not in existing:
        conn.execute('ALTER TABLE classifications ADD COLUMN study_type TEXT')
    conn.commit()


def already_processed(conn: sqlite3.Connection, k_number: str) -> bool:
    cur = conn.execute(
        'SELECT 1 FROM quantitative_metrics WHERE k_number = ? LIMIT 1', (k_number,)
    )
    return cur.fetchone() is not None


def store_metrics(
    conn: sqlite3.Connection,
    k_number: str,
    findings: List[Dict],
) -> int:
    inserted = 0
    for f in findings:
        mtype = f['metric_type']
        mval  = f['metric_value']
        ctx   = f['context']

        if not _value_is_plausible(mtype, mval):
            # AUC sometimes expressed as percentage (e.g. 0.92 written as 92).
            # Rescale only if the converted value would also be plausible (≥ 0.5).
            if mtype == 'auc' and 50 <= mval <= 100:
                mval = mval / 100.0
            else:
                continue

        ci_lower, ci_upper = extract_ci(ctx, mval)

        conn.execute(
            '''INSERT INTO quantitative_metrics
               (k_number, metric_type, metric_value, ci_lower, ci_upper, context)
               VALUES (?, ?, ?, ?, ?, ?)''',
            (k_number, mtype, mval, ci_lower, ci_upper, ctx[:500]),
        )
        inserted += 1
    conn.commit()
    return inserted


def update_study_info(
    conn: sqlite3.Connection, k_number: str, text: str, extractor: PerformanceMetricsExtractor
) -> None:
    study_type = detect_study_type(text)
    conn.execute(
        'UPDATE classifications SET study_type = ? WHERE k_number = ?',
        (study_type, k_number),
    )

    cur = conn.execute(
        'SELECT dataset_size FROM classifications WHERE k_number = ?', (k_number,)
    )
    row = cur.fetchone()
    if row and row[0] is None:
        study = extractor.extract_study_size(text)
        if study:
            conn.execute(
                'UPDATE classifications SET dataset_size = ?, dataset_unit = ? WHERE k_number = ?',
                (study['dataset_size'], study['unit'], k_number),
            )
    conn.commit()


def export_csv(conn: sqlite3.Connection, out_path: str) -> None:
    cur = conn.execute('''
        SELECT
            qm.k_number,
            c.device,
            c.company,
            c.panel,
            c.decision_year,
            c.study_type,
            qm.metric_type,
            qm.metric_value,
            qm.ci_lower,
            qm.ci_upper,
            c.dataset_size,
            c.dataset_unit,
            qm.context
        FROM quantitative_metrics qm
        JOIN classifications c ON qm.k_number = c.k_number
        ORDER BY c.panel, qm.k_number, qm.metric_type
    ''')
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    with open(out_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(cols)
        writer.writerows(rows)
    print(f"\nExported {len(rows)} metric rows → {out_path}")


def print_summary(conn: sqlite3.Connection) -> None:
    cur = conn.execute(
        'SELECT COUNT(*), COUNT(DISTINCT k_number) FROM quantitative_metrics'
    )
    total_rows, devices_with_data = cur.fetchone()
    print(f"\nTotal metric rows: {total_rows}  |  Devices with data: {devices_with_data}/441")

    cur = conn.execute('''
        SELECT metric_type,
               COUNT(*) as n,
               ROUND(AVG(metric_value), 2) as avg_val,
               ROUND(MIN(metric_value), 2) as min_val,
               ROUND(MAX(metric_value), 2) as max_val,
               SUM(CASE WHEN ci_lower IS NOT NULL THEN 1 ELSE 0 END) as with_ci
        FROM quantitative_metrics
        GROUP BY metric_type
        ORDER BY n DESC
    ''')
    print(f"\n{'Metric':<14} {'N':>5} {'Avg':>7} {'Min':>7} {'Max':>7} {'w/CI':>6}")
    print("-" * 50)
    for row in cur.fetchall():
        print(f"{row[0]:<14} {row[1]:>5} {row[2]:>7} {row[3]:>7} {row[4]:>7} {row[5]:>6}")

    cur = conn.execute('''
        SELECT c.panel, COUNT(DISTINCT qm.k_number) as devices
        FROM quantitative_metrics qm
        JOIN classifications c ON qm.k_number = c.k_number
        GROUP BY c.panel
        ORDER BY devices DESC
        LIMIT 10
    ''')
    print(f"\nTop panels by devices with extracted metrics:")
    for row in cur.fetchall():
        print(f"  {row[0]:<30} {row[1]} devices")


def main() -> None:
    processor = PDFProcessor()
    extractor = PerformanceMetricsExtractor()

    conn = sqlite3.connect(DB_PATH)
    setup_db(conn)

    cur = conn.execute(
        'SELECT k_number, device, panel FROM classifications WHERE category = ? ORDER BY k_number',
        ('A',),
    )
    devices = cur.fetchall()
    total = len(devices)
    print(f"Category A devices to process: {total}")
    print("-" * 60)

    success, failed, skipped = 0, 0, 0

    for i, (k_number, device, panel) in enumerate(devices, 1):
        if already_processed(conn, k_number):
            skipped += 1
            continue

        text = processor.extract_text(k_number)

        if text is None:
            failed += 1
            print(f"[{i:>3}/{total}] {k_number:<12} FAILED (PDF unavailable)")
            time.sleep(RATE_LIMIT_S)
            continue

        findings = extractor.extract_metrics(text, k_number)
        n_inserted = store_metrics(conn, k_number, findings)
        update_study_info(conn, k_number, text, extractor)
        success += 1

        label = device[:35] if device else ''
        print(f"[{i:>3}/{total}] {k_number:<12} {panel:<20} {n_inserted:>2} metrics  {label}")

        time.sleep(RATE_LIMIT_S)

    print()
    print("=" * 60)
    print(f"Finished — success: {success}  failed: {failed}  skipped: {skipped}")
    print_summary(conn)
    export_csv(conn, CSV_OUT)
    conn.close()


if __name__ == '__main__':
    main()
