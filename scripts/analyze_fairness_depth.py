"""
Analyze depth and authenticity of fairness/bias language in FDA AI submissions.

Distinguishes true AI-fairness language from statistical/measurement bias
(e.g. CLSI "estimation of bias", analytical chemistry bias) which inflates
the raw has_fairness_bias flag in ai_ethics_signals.

Works entirely from the local text cache — no network calls needed.

Creates:  fairness_depth table in data/fda_classifications.db
Exports:  data/fairness_depth.csv

Run from FDA_AIeMD_DB/:
    python scripts/analyze_fairness_depth.py
    python scripts/analyze_fairness_depth.py --reset
"""
import argparse
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from src.pdf_processor import PDFProcessor

DB_PATH = 'data/fda_classifications.db'
SNIPPET_WINDOW = 150   # chars on each side of a match


# ---------------------------------------------------------------------------
# Pattern catalogue
# ---------------------------------------------------------------------------

# Statistical / measurement bias — false positives for AI fairness.
# These are domain-specific uses of "bias" from clinical chemistry, epidemiology,
# and study-design literature that have nothing to do with algorithmic fairness.
STATISTICAL_BIAS_PATS = [
    r'\bclsi\b',
    r'estimation of bias',
    r'precision and estimation',
    r'mean score shift',
    r'analytical bias',
    r'lot.to.lot bias',
    r'calibration bias',
    r'lead.time bias',
    r'length bias',
    r'spectrum bias',
    r'verification bias',
    r'ascertainment bias',
    r'workup bias',
    r'incorporation bias',
    r'observer bias',
    r'publication bias',
    r'information bias',
    r'recall bias',
    r'reporting bias',
    r'sources of bias',
    r'accounting for bias',
    r'\d+\.?\d*\s*%\s*bias',      # "2.3% bias" — measurement context
    r'percent(?:age)?\s+bias',
    r'inter.site bias',
    r'center.induced bias',
    r'site.specific bias',
    r'\bconfound',
]

# Genuine AI / algorithmic fairness language.
# Ordered from most specific to least specific.
ALGORITHMIC_FAIRNESS_PATS = [
    r'fairness methodology',
    r'algorithmic fairness',
    r'algorithmic bias',
    r'model fairness',
    r'\bai fairness\b',
    r'fairness metric',
    r'fairness criterion',
    r'fairness analysis',
    r'subgroup fairness',
    r'subgroup bias',
    r'demographic bias',
    r'demographic fairness',
    r'racial bias',
    r'ethnic bias',
    r'gender bias',
    r'sex.based bias',
    r'age.related bias',
    r'skin.tone',
    r'fitzpatrick',
    r'health equity',
    r'health disparit',
    r'racial disparit',
    r'ethnic disparit',
    r'health inequit',
    r'protected class',
    r'protected attribute',
    r'protected group',
    r'sensitive attribute',
    r'bias mitigation',
    r'\bdebiasing\b',
    r'\bdebiased\b',
    r'disparate impact',
    r'demographic parity',
    r'equalized odds',
    r'fairness constraint',
    r'subgroup performance',
    r'performance.{0,40}by.{0,30}(?:race|sex|gender|ethnicit|age\s*group)',
    r'stratif.{0,40}by.{0,30}(?:race|sex|gender|ethnicit|age\s*group)',
]

# Demographic factors — more specific patterns to reduce clinical noise.
# Avoids bare "male"/"female"/"age"/"sex" which appear in almost all submissions.
DEMOGRAPHIC_PATS: Dict[str, List[str]] = {
    'race': [
        r'\brace\b',
        r'\bracial\b',
        r'\bethnicit',
        r'\bethnic group',
        r'african.american',
        r'\bcaucasian\b',
        r'\bhispanic\b',
        r'\blatino\b',
        r'asian population',
    ],
    'sex': [
        r'by sex\b',
        r'by gender\b',
        r'sex.based',
        r'gender.based',
        r'gender.specific',
        r'sex.specific',
        r'\bgender gap\b',
        r'male.female',
        r'female.male',
    ],
    'age': [
        r'age group',
        r'age.stratif',
        r'age cohort',
        r'by age\b',
        r'age.based',
        r'\bpediatric\b',
        r'\bgeriatric\b',
    ],
    'skin_tone': [
        r'skin tone',
        r'skin type',
        r'skin colo',
        r'fitzpatrick',
        r'dark skin',
        r'light skin',
        r'\bmelanin\b',
    ],
    'geography': [
        r'geographic.{0,20}(?:region|variation|bias|diversit)',
        r'country.specific',
        r'multi.national.{0,20}site',
    ],
    'socioeconomic': [
        r'socioeconomic',
        r'income level',
        r'\bpoverty\b',
        r'insurance status',
    ],
}

# Numeric metric near a demographic — strongest signal of actual subgroup reporting
METRIC_RE = re.compile(
    r'\b(?:\d{1,3}\.?\d*\s*%|'
    r'auc\s*[=:of ]+0\.\d+|'
    r'0\.\d{2,}\s+(?:sensitivity|specificity|accuracy))\b',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------

def _hit(patterns: List[str], text: str) -> Tuple[bool, List[str]]:
    """Return (any_match, list_of_readable_matched_terms)."""
    found = []
    for pat in patterns:
        if re.search(pat, text, re.IGNORECASE):
            label = re.sub(r'[\\()\[\]^+*?.|{}]', '', pat).strip()
            found.append(label)
    return bool(found), found


def _snippets(patterns: List[str], text: str, max_n: int = 3) -> List[str]:
    """Return up to max_n context windows (±SNIPPET_WINDOW chars) around pattern matches."""
    out = []
    for pat in patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            s = max(0, m.start() - SNIPPET_WINDOW)
            e = min(len(text), m.end() + SNIPPET_WINDOW)
            out.append(text[s:e].replace('\n', ' ').strip())
            if len(out) >= max_n:
                return out
    return out


def _metric_near_demo(text: str) -> bool:
    """True if any demographic mention appears within SNIPPET_WINDOW chars of a numeric metric."""
    all_demo = [p for plist in DEMOGRAPHIC_PATS.values() for p in plist]
    for dpat in all_demo:
        for m in re.finditer(dpat, text, re.IGNORECASE):
            window = text[max(0, m.start() - SNIPPET_WINDOW): m.end() + SNIPPET_WINDOW]
            if METRIC_RE.search(window):
                return True
    return False


def analyze(text: str, k_number: str) -> dict:
    algo_hit, algo_terms = _hit(ALGORITHMIC_FAIRNESS_PATS, text)
    stat_hit, stat_terms = _hit(STATISTICAL_BIAS_PATS, text)

    demo_hits: Dict[str, int] = {}
    any_demo = False
    for demo, pats in DEMOGRAPHIC_PATS.items():
        hit, _ = _hit(pats, text)
        demo_hits[demo] = int(hit)
        if hit:
            any_demo = True

    has_numbers = _metric_near_demo(text) if any_demo else False

    # Fairness type
    if algo_hit and stat_hit:
        fairness_type = 'mixed'
    elif algo_hit:
        fairness_type = 'algorithmic'
    elif stat_hit:
        fairness_type = 'statistical_only'
    else:
        fairness_type = 'none'

    # Depth tier
    if algo_hit and any_demo and has_numbers:
        depth = 'quantified_subgroup'
    elif algo_hit and any_demo:
        depth = 'tested_subgroup'
    elif algo_hit:
        depth = 'acknowledged'
    elif stat_hit:
        depth = 'statistical_only'
    else:
        depth = 'absent'

    snip_pats = ALGORITHMIC_FAIRNESS_PATS if algo_hit else STATISTICAL_BIAS_PATS[:6]
    snippets  = _snippets(snip_pats, text)

    return {
        'k_number':                k_number,
        'fairness_type':           fairness_type,
        'fairness_depth':          depth,
        'has_algorithmic_fairness': int(algo_hit),
        'has_statistical_bias':    int(stat_hit),
        'has_subgroup_numbers':    int(has_numbers),
        'mentions_race':           demo_hits.get('race', 0),
        'mentions_sex':            demo_hits.get('sex', 0),
        'mentions_age':            demo_hits.get('age', 0),
        'mentions_skin_tone':      demo_hits.get('skin_tone', 0),
        'mentions_geography':      demo_hits.get('geography', 0),
        'mentions_socioeconomic':  demo_hits.get('socioeconomic', 0),
        'algorithmic_terms':       '; '.join(algo_terms[:10]),
        'statistical_terms':       '; '.join(stat_terms[:10]),
        'context_snippets':        ' ||| '.join(snippets),
        'analyzed_at':             datetime.now(timezone.utc).isoformat(timespec='seconds'),
    }


def _empty(k_number: str) -> dict:
    return {
        'k_number': k_number, 'fairness_type': 'none', 'fairness_depth': 'absent',
        'has_algorithmic_fairness': 0, 'has_statistical_bias': 0, 'has_subgroup_numbers': 0,
        'mentions_race': 0, 'mentions_sex': 0, 'mentions_age': 0,
        'mentions_skin_tone': 0, 'mentions_geography': 0, 'mentions_socioeconomic': 0,
        'algorithmic_terms': '', 'statistical_terms': '', 'context_snippets': '',
        'analyzed_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
    }


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

DDL = """
CREATE TABLE IF NOT EXISTS fairness_depth (
    k_number                 TEXT PRIMARY KEY,
    fairness_type            TEXT NOT NULL,
    fairness_depth           TEXT NOT NULL,
    has_algorithmic_fairness INTEGER DEFAULT 0,
    has_statistical_bias     INTEGER DEFAULT 0,
    has_subgroup_numbers     INTEGER DEFAULT 0,
    mentions_race            INTEGER DEFAULT 0,
    mentions_sex             INTEGER DEFAULT 0,
    mentions_age             INTEGER DEFAULT 0,
    mentions_skin_tone       INTEGER DEFAULT 0,
    mentions_geography       INTEGER DEFAULT 0,
    mentions_socioeconomic   INTEGER DEFAULT 0,
    algorithmic_terms        TEXT,
    statistical_terms        TEXT,
    context_snippets         TEXT,
    analyzed_at              TEXT NOT NULL,
    FOREIGN KEY (k_number) REFERENCES classifications(k_number)
);
"""


def setup_db(conn: sqlite3.Connection, reset: bool) -> None:
    if reset:
        conn.execute('DROP TABLE IF EXISTS fairness_depth')
        print('Table dropped — rebuilding from scratch.')
    conn.execute(DDL)
    conn.commit()


def already_done(conn: sqlite3.Connection, k_number: str) -> bool:
    return conn.execute(
        'SELECT 1 FROM fairness_depth WHERE k_number = ? LIMIT 1', (k_number,)
    ).fetchone() is not None


def insert(conn: sqlite3.Connection, row: dict) -> None:
    cols = ', '.join(row.keys())
    phds = ', '.join(['?'] * len(row))
    conn.execute(
        f'INSERT OR REPLACE INTO fairness_depth ({cols}) VALUES ({phds})',
        list(row.values()),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Summary + CSV export
# ---------------------------------------------------------------------------

def print_summary(conn: sqlite3.Connection) -> None:
    total = conn.execute('SELECT COUNT(*) FROM fairness_depth').fetchone()[0]
    print(f'\n{"=" * 60}')
    print(f'FAIRNESS DEPTH SUMMARY  (n={total} devices)')
    print('=' * 60)

    print('\n--- Depth tier ---')
    order = ['quantified_subgroup', 'tested_subgroup', 'acknowledged',
             'statistical_only', 'absent']
    for tier in order:
        n = conn.execute(
            'SELECT COUNT(*) FROM fairness_depth WHERE fairness_depth = ?', (tier,)
        ).fetchone()[0]
        print(f'  {tier:<25} {n:>5}  ({100*n/total:.1f}%)')

    print('\n--- Fairness type ---')
    rows = conn.execute('''
        SELECT fairness_type, COUNT(*) n
        FROM fairness_depth GROUP BY fairness_type ORDER BY n DESC
    ''').fetchall()
    for ftype, n in rows:
        print(f'  {ftype:<25} {n:>5}  ({100*n/total:.1f}%)')

    algo_n = conn.execute(
        'SELECT COUNT(*) FROM fairness_depth WHERE has_algorithmic_fairness = 1'
    ).fetchone()[0]
    print(f'\n--- Demographics (among {algo_n} algorithmic-fairness devices) ---')
    for demo in ['race', 'sex', 'age', 'skin_tone', 'geography', 'socioeconomic']:
        n = conn.execute(
            f'SELECT COUNT(*) FROM fairness_depth '
            f'WHERE mentions_{demo} = 1 AND has_algorithmic_fairness = 1'
        ).fetchone()[0]
        pct = 100 * n / algo_n if algo_n else 0
        print(f'  {demo:<20} {n:>5}  ({pct:.1f}%)')

    print('=' * 60)


def export_csv(conn: sqlite3.Connection) -> None:
    import pandas as pd
    df = pd.read_sql('''
        SELECT fd.*, c.panel, c.decision_year, c.category
        FROM fairness_depth fd
        JOIN classifications c ON fd.k_number = c.k_number
        ORDER BY fd.fairness_depth, c.decision_year
    ''', conn)
    df.to_csv('data/fairness_depth.csv', index=False)
    print('  saved -> data/fairness_depth.csv')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--reset', action='store_true',
                        help='Drop and rebuild the fairness_depth table')
    args = parser.parse_args()

    proc = PDFProcessor()
    conn = sqlite3.connect(DB_PATH)
    setup_db(conn, args.reset)

    devices = conn.execute(
        'SELECT k_number FROM classifications ORDER BY k_number'
    ).fetchall()

    total = len(devices)
    cached_n = skipped_n = unavail_n = 0

    print(f'Analyzing {total} devices (cache-only, no network calls)...')

    for i, (k_number,) in enumerate(devices, 1):
        if already_done(conn, k_number):
            skipped_n += 1
            continue

        text = proc._read_cache(k_number)
        if text:
            row = analyze(text, k_number)
            cached_n += 1
        else:
            row = _empty(k_number)
            unavail_n += 1

        insert(conn, row)

        if i % 200 == 0 or i == total:
            print(f'  [{i:>4}/{total}]  analyzed={cached_n}  '
                  f'unavailable={unavail_n}  skipped={skipped_n}')

    print_summary(conn)
    export_csv(conn)
    conn.close()
    print('\nDone. Add fig_fairness_depth() to analyze_trends.py to visualize.')


if __name__ == '__main__':
    main()
