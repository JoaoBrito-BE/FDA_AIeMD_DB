"""
Extract AI-ethics, fairness, privacy, and explainability signals from all
FDA submissions and store them in the ai_ethics_signals table.

Devices with cached text are processed without any network requests.
Devices not yet cached are fetched (and cached) before analysis.
Devices whose PDFs were unrecoverable get a placeholder row so they remain
fully represented in the database.

Run from the FDA_AIeMD_DB directory:
    python scripts/extract_ai_ethics_signals.py
    python scripts/extract_ai_ethics_signals.py --reset   # drop and rebuild table
"""
import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.pdf_processor import PDFProcessor
from src.ai_ethics_analyzer import AIEthicsAnalyzer

DB_PATH      = 'data/fda_classifications.db'
RATE_LIMIT_S = 2.0

_DDL = """
CREATE TABLE IF NOT EXISTS ai_ethics_signals (
    k_number               TEXT PRIMARY KEY,
    has_fairness_bias      INTEGER DEFAULT 0,
    has_privacy            INTEGER DEFAULT 0,
    has_xai_general        INTEGER DEFAULT 0,
    has_xai_method         INTEGER DEFAULT 0,
    has_data_provenance    INTEGER DEFAULT 0,
    has_ethics_general     INTEGER DEFAULT 0,
    has_shap               INTEGER DEFAULT 0,
    has_lime               INTEGER DEFAULT 0,
    has_saliency           INTEGER DEFAULT 0,
    has_probability_map    INTEGER DEFAULT 0,
    has_tornado_plot       INTEGER DEFAULT 0,
    has_grad_cam           INTEGER DEFAULT 0,
    count_fairness_bias    INTEGER DEFAULT 0,
    count_privacy          INTEGER DEFAULT 0,
    count_xai_general      INTEGER DEFAULT 0,
    count_xai_methods      INTEGER DEFAULT 0,
    count_data_provenance  INTEGER DEFAULT 0,
    count_ethics_general   INTEGER DEFAULT 0,
    matched_fairness_terms TEXT,
    matched_privacy_terms  TEXT,
    matched_xai_terms      TEXT,
    matched_data_terms     TEXT,
    matched_ethics_terms   TEXT,
    total_signal_count     INTEGER DEFAULT 0,
    text_source            TEXT,
    analyzed_at            TEXT NOT NULL,
    FOREIGN KEY (k_number) REFERENCES classifications(k_number)
);
"""


def setup_db(conn: sqlite3.Connection, reset: bool) -> None:
    if reset:
        conn.execute('DROP TABLE IF EXISTS ai_ethics_signals')
        print("Table dropped — rebuilding from scratch.")
    conn.execute(_DDL)
    conn.commit()


def already_done(conn: sqlite3.Connection, k_number: str) -> bool:
    row = conn.execute(
        'SELECT 1 FROM ai_ethics_signals WHERE k_number = ? LIMIT 1', (k_number,)
    ).fetchone()
    return row is not None


def insert(conn: sqlite3.Connection, result_dict: dict) -> None:
    result_dict['analyzed_at'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
    cols   = ', '.join(result_dict.keys())
    placeholders = ', '.join('?' * len(result_dict))
    conn.execute(
        f'INSERT OR REPLACE INTO ai_ethics_signals ({cols}) VALUES ({placeholders})',
        list(result_dict.values()),
    )
    conn.commit()


def print_summary(conn: sqlite3.Connection) -> None:
    total = conn.execute('SELECT COUNT(*) FROM ai_ethics_signals').fetchone()[0]
    print(f"\n{'='*60}")
    print(f"Total rows: {total}")

    fields = [
        ('has_fairness_bias',   'Fairness / bias'),
        ('has_privacy',         'Privacy / HIPAA'),
        ('has_xai_general',     'XAI (general)'),
        ('has_xai_method',      'XAI (named method)'),
        ('has_data_provenance', 'Data provenance'),
        ('has_ethics_general',  'Ethics (general)'),
        ('has_shap',            '  └─ SHAP'),
        ('has_lime',            '  └─ LIME'),
        ('has_saliency',        '  └─ Saliency maps'),
        ('has_tornado_plot',    '  └─ Tornado plots'),
        ('has_probability_map', '  └─ Probability maps'),
    ]

    print(f"\n{'Concept':<30} {'N':>5}  {'%':>6}")
    print('-' * 45)
    for col, label in fields:
        n = conn.execute(
            f'SELECT COUNT(*) FROM ai_ethics_signals WHERE {col} = 1'
        ).fetchone()[0]
        pct = 100 * n / total if total else 0
        print(f"{label:<30} {n:>5}  {pct:>5.1f}%")

    unavail = conn.execute(
        "SELECT COUNT(*) FROM ai_ethics_signals WHERE text_source = 'unavailable'"
    ).fetchone()[0]
    print(f"\nDevices with no recoverable text: {unavail}")
    print('='*60)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--reset', action='store_true',
                        help='Drop and rebuild ai_ethics_signals table')
    args = parser.parse_args()

    processor = PDFProcessor()
    analyzer  = AIEthicsAnalyzer()
    conn      = sqlite3.connect(DB_PATH)
    setup_db(conn, args.reset)

    devices = conn.execute(
        'SELECT k_number, fetch_status FROM classifications ORDER BY k_number'
    ).fetchall()

    total     = len(devices)
    cached_n  = fetched_n = unavail_n = skipped_n = 0

    print(f"Devices to process: {total}")
    print(f"Cache dir: {processor.cache_dir}\n")

    for i, (k_number, fetch_status) in enumerate(devices, 1):
        if already_done(conn, k_number) and not args.reset:
            skipped_n += 1
            continue

        # Check if text is already cached (free — no network call)
        cached_text = processor._read_cache(k_number)

        if cached_text:
            result = analyzer.analyze(cached_text, k_number, text_source='cache')
            cached_n += 1
        elif fetch_status == 'pdf_empty':
            # Previously failed — try again with fallback URLs now available
            text = processor.extract_text(k_number)
            if text:
                result = analyzer.analyze(text, k_number, text_source='fetched')
                fetched_n += 1
                # Update classification status since we now have text
                conn.execute(
                    "UPDATE classifications SET fetch_status='ok' WHERE k_number=?",
                    (k_number,)
                )
                conn.commit()
            else:
                result = analyzer.empty_result(k_number)
                unavail_n += 1
        else:
            # Should have been cached by the batch run, but fetch anyway
            text = processor.extract_text(k_number)
            if text:
                result = analyzer.analyze(text, k_number, text_source='fetched')
                fetched_n += 1
                time.sleep(RATE_LIMIT_S)
            else:
                result = analyzer.empty_result(k_number)
                unavail_n += 1

        insert(conn, result.to_dict())

        if i % 100 == 0 or i == total:
            print(f"  [{i:>4}/{total}]  cached={cached_n}  fetched={fetched_n}"
                  f"  unavailable={unavail_n}  skipped={skipped_n}")

    print_summary(conn)
    conn.close()
    print('\nDone. Run analyze_trends.py to regenerate figures.')


if __name__ == '__main__':
    main()
