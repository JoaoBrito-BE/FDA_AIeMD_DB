# FDA AI Medical Device Evidence Analysis - Complete Project Context

## Project Mission
Systematically analyze FDA regulatory submissions for 1,430 AI/ML-enabled medical devices to understand how manufacturers report clinical performance metrics and evidence transparency. This serves dual purposes:
1. **Research**: Help a biomedical collaborator understand regulatory evidence patterns
2. **Portfolio**: Demonstrate data engineering + domain expertise for job applications

---

## Critical Achievement - Batch Processing Complete ✅

**Results Summary:**
Total devices: 1,430
Successfully extracted: 1,407 (98.4%)
Failed (empty/scanned PDFs): 23 (1.6%)
Evidence Classification:

Category A (Quantitative metrics):     441 devices (30.8%)
Category B (Qualitative mentions):     730 devices (51.0%)
Category C (Technical/bench only):     195 devices (13.6%)
Category D (Equivalence only):          41 devices (2.9%)

**Key Finding**: 31% of AI devices actually report extractable numeric performance data - significantly higher than initial small-sample testing suggested.

---

## Technical Architecture

### Project Structure

```
FDA_AIeMD_DB/
├── data/
│   ├── fda_ai_devices.csv              # Source: 1,430 AI devices from FDA official list
│   ├── fda_classifications.db          # SQLite database with all results
│   └── metric_search_results.csv       # Pilot study (20 devices)
├── src/
│   ├── __init__.py
│   ├── pdf_processor.py                # PDF streaming & text extraction
│   ├── keyword_analyzer.py             # Performance metric regex detection
│   └── evidence_classifier.py          # Evidence categorization logic
├── scripts/
│   ├── batch_classify_all_devices.py   # COMPLETED - Main processing pipeline
│   ├── check_ai_list.py
│   ├── classify_evidence.py
│   ├── extract_promising_devices.py
│   ├── inspect_pdf_content.py          # PDF content inspection utility
│   ├── search_for_metrics.py
│   ├── test_extraction.py
│   ├── test_multiple_ai_devices.py
│   └── test_single_pdf.py
├── reports/                            # To be created
├── config.py
├── requirements.txt
├── .gitignore
└── README.md
```

> **Note**: `config.py` still references `data/results.db` — update `DATABASE_PATH` to `data/fda_classifications.db` to match the actual file.

### Database Schema

> **Note**: The actual schema differs from the original design — all fields are combined in one `classifications` table.

```sql
CREATE TABLE classifications (
    k_number             TEXT PRIMARY KEY,   -- K######, DEN######, P######
    device               TEXT,
    company              TEXT,
    panel                TEXT,               -- Medical specialty
    product_code         TEXT,
    decision_date        TEXT,
    decision_year        INTEGER,
    category             TEXT,               -- 'A', 'B', 'C', 'D' or NULL (failed)
    label                TEXT,               -- Human-readable category name
    confidence           TEXT,               -- 'high', 'medium', 'low'
    metric_count         INTEGER,            -- # of numeric metric pattern matches
    dataset_size         INTEGER,            -- n= from study size extraction
    dataset_unit         TEXT,               -- 'patients', 'images', etc.
    study_type           TEXT,               -- 'retrospective', 'prospective', etc.
    qualitative_signals  TEXT,               -- matched clinical language (semicolon-sep)
    technical_signals    TEXT,               -- matched bench/software terms
    equivalence_signals  TEXT,               -- matched predicate language
    fetch_status         TEXT NOT NULL,      -- 'ok' or 'pdf_empty'
    error_message        TEXT,
    processed_at         TEXT NOT NULL
);

CREATE TABLE quantitative_metrics (         -- populated by extract_quantitative_metrics.py
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    k_number     TEXT NOT NULL,
    metric_type  TEXT NOT NULL,             -- 'sensitivity', 'specificity', 'auc', etc.
    metric_value REAL NOT NULL,
    ci_lower     REAL,                      -- 95% CI lower bound (if found)
    ci_upper     REAL,                      -- 95% CI upper bound (if found)
    context      TEXT,                      -- surrounding text snippet
    FOREIGN KEY (k_number) REFERENCES classifications(k_number)
);

CREATE TABLE run_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    total        INTEGER,
    processed    INTEGER,
    succeeded    INTEGER,
    failed       INTEGER
);
```

---

## Core Code Components

### 1. PDF Processor (`src/pdf_processor.py`)

**Purpose**: Stream PDFs from FDA servers without local storage

**Key Method - Fixed to Handle All Submission Types**:
```python
def construct_pdf_url(self, submission_number: str) -> str:
    """
    Constructs PDF URL for K######, DEN######, or P###### submissions.

    Examples:
    K253532   → https://accessdata.fda.gov/cdrh_docs/pdf25/K253532.pdf
    DEN200070 → https://accessdata.fda.gov/cdrh_docs/pdf20/DEN200070.pdf
    P210014   → https://accessdata.fda.gov/cdrh_docs/pdf21/P210014.pdf
    """
    if submission_number.startswith('K'):
        year_suffix = submission_number[1:3]
    elif submission_number.startswith('DEN'):
        year_suffix = submission_number[3:5]
    elif submission_number.startswith('P'):
        year_suffix = submission_number[1:3]
    else:
        logger.error(f"Unknown submission type: {submission_number}")
        return None

    return f"https://www.accessdata.fda.gov/cdrh_docs/pdf{year_suffix}/{submission_number}.pdf"
```

**Critical Details**:
- Uses `pdfplumber` for text extraction
- Streams via `requests` with BytesIO (no disk writes)
- 30-second timeout per PDF
- Returns None for failures (scanned PDFs, 404s, timeouts)

---

### 2. Evidence Classifier (`src/evidence_classifier.py`)

**Purpose**: Categorize devices by type of evidence provided

**Classification Logic**:
```python
class EvidenceClassifier:
    """
    Categories:
    A - Quantitative: Reports numeric metrics (sensitivity: 95%, AUC: 0.92)
    B - Qualitative: Mentions testing but no numbers
    C - Technical_Only: Bench/phantom testing, no clinical outcomes
    D - Equivalence_Only: Pure substantial equivalence claim
    """

    QUANTITATIVE_PATTERNS = [
        r'sensitivity[:\s]+(of\s+)?([0-9]+\.?[0-9]*)\s*%',
        r'specificity[:\s]+(of\s+)?([0-9]+\.?[0-9]*)\s*%',
        r'AUC[:\s]+(of\s+)?([0-9]\.[0-9]+)',
        r'accuracy[:\s]+(of\s+)?([0-9]+\.?[0-9]*)\s*%',
        # ... more patterns
    ]

    CLINICAL_KEYWORDS = [
        'clinical study', 'patient data', 'retrospective',
        'prospective', 'clinical validation', 'reader study'
    ]

    EQUIVALENCE_PHRASES = [
        'substantially equivalent', 'predicate device',
        'same technological characteristics'
    ]
```

**Key Decision Points**:
1. If numeric metrics found → Category A
2. Else if clinical keywords present → Category B
3. Else if only technical terms (phantom, bench) → Category C
4. Else if only equivalence language → Category D

---

### 3. Batch Processing Pipeline (`scripts/batch_classify_all_devices.py`)

**What It Does**:
- Reads all 1,430 devices from CSV
- For each device:
  1. Fetch PDF via PDFProcessor
  2. Extract text
  3. Classify evidence via EvidenceClassifier
  4. Store results in SQLite
- Rate limiting: 2-second delay between requests
- Progress tracking with status updates
- Error handling for missing/scanned PDFs

**Performance**:
- ~1 hour runtime for 1,430 devices (2.5s per device average)
- 98.4% success rate

---

## Research Findings Context

### What We Learned Through Pilot Testing

**Initial Hypothesis**: Most AI devices report detailed performance metrics
**Reality**: Mixed - depends heavily on regulatory pathway and device type

**Pilot Study (20 random devices)**:
- Only 15% mentioned "sensitivity"
- 0% mentioned "specificity" or "AUC"
- Most used vague language: "performance was assessed"

**Example from K242583 (ECG AI)**:
> "Performance was assessed based on Sensitivity, Positive Predictive Value (PPV), and False Positive Rate (FPR), validated against reference data."

**No actual numbers provided** - just confirmation testing occurred.

**Full Dataset (1,430 devices)**:
- 31% DO report quantitative metrics (Category A)
- Suggests certain specialties/pathways require more disclosure
- Radiology AI likely over-represented in quantitative category

---

## Evidence Category Definitions (For Reference)

### Category A - Quantitative (441 devices, 30.8%)
**Characteristics**:
- Reports specific numeric performance values
- Examples: "sensitivity: 94.2%", "AUC: 0.89", "n=500 patients"
- May include confidence intervals, p-values
- Indicates clinical validation study was performed with results disclosed

**Example Language**:
> "The algorithm achieved a sensitivity of 92.3% (95% CI: 89.1-94.8%) and specificity of 87.6% (95% CI: 84.2-90.3%) in a retrospective study of 1,247 cases."

### Category B - Qualitative (730 devices, 51.0%)
**Characteristics**:
- Mentions clinical testing/validation occurred
- No specific performance numbers
- Vague statements like "met performance specifications"
- May reference studies but doesn't report results

**Example Language**:
> "Clinical performance was evaluated in a multi-site study. The device met all pre-specified performance criteria and demonstrated substantial equivalence to the predicate."

### Category C - Technical Only (195 devices, 13.6%)
**Characteristics**:
- Only reports technical/bench testing
- Phantom studies, image quality metrics
- Software verification/validation
- No patient outcome data

**Example Language**:
> "Performance testing was conducted using NEMA phantoms. Image quality metrics including CNR, MTF, and noise power spectra met specifications."

### Category D - Equivalence Only (41 devices, 2.9%)
**Characteristics**:
- Pure substantial equivalence claim
- No new testing described
- References predicate device performance
- Minimal submission

**Example Language**:
> "The subject device has the same intended use and technological characteristics as the predicate device (K123456). Therefore, it is substantially equivalent."

---

## Next Steps - Analysis Phase

### Priority 1: Validate Classification Accuracy
**Goal**: Ensure classifier is working correctly before drawing conclusions

```python
# Manually review 5-10 devices from each category
# Check if classification matches actual PDF content
# Adjust classifier logic if systematic errors found
```

### Priority 2: Extract Detailed Metrics from Category A
**Goal**: For the 441 quantitative devices, extract actual metric values

**Create**: `scripts/extract_quantitative_metrics.py`

**What to Extract**:
- Sensitivity value + confidence interval
- Specificity value + confidence interval
- AUC / ROC curve data
- PPV / NPV
- Accuracy
- Dataset size (n = X patients/images)
- Study design (retrospective/prospective/reader study)
- Reference standard used

**New Database Table**:
```sql
CREATE TABLE quantitative_metrics (
    submission_number TEXT PRIMARY KEY,
    sensitivity REAL,
    sensitivity_ci_lower REAL,
    sensitivity_ci_upper REAL,
    specificity REAL,
    specificity_ci_lower REAL,
    specificity_ci_upper REAL,
    auc REAL,
    accuracy REAL,
    ppv REAL,
    npv REAL,
    dataset_size INTEGER,
    study_type TEXT,
    reference_standard TEXT,
    context_snippet TEXT,
    FOREIGN KEY (submission_number) REFERENCES devices
);
```

### Priority 3: Temporal & Specialty Analysis
**Goal**: Understand transparency trends

**Analyses**:
1. **Over Time**: Are 2024-2025 devices more transparent than 2018-2019?
2. **By Panel**: Which specialties report more data? (Radiology vs Cardiology vs Pathology)
3. **By Manufacturer**: Which companies are most transparent?
4. **By Pathway**: 510(k) vs De Novo vs PMA - differences in reporting?

**Create**: `scripts/analyze_trends.py`

### Priority 4: Human Factors Analysis
**Goal**: Understand usability testing reporting

**Extract from all categories**:
- IEC 62366 mentions
- Formative/summative testing mentions
- Use error analysis
- Task analysis
- Usability study descriptions

**New Table**:
```sql
CREATE TABLE human_factors_evidence (
    submission_number TEXT PRIMARY KEY,
    mentions_iec62366 BOOLEAN,
    mentions_formative BOOLEAN,
    mentions_summative BOOLEAN,
    mentions_use_errors BOOLEAN,
    hf_evidence_quality TEXT,  -- 'detailed', 'mentioned', 'absent'
    context_snippet TEXT,
    FOREIGN KEY (submission_number) REFERENCES devices
);
```

### Priority 5: Generate Final Deliverables

**For Collaborator**:
- `data/complete_device_classification.csv` - All devices with categories
- `data/quantitative_metrics_extracted.csv` - Detailed metrics from Category A
- `reports/evidence_transparency_report.pdf` - Executive summary with visualizations
- `reports/methodology.md` - How analysis was conducted

**For GitHub**:
- Update README.md with key findings
- Add visualizations (charts, graphs)
- Document methodology
- Add limitations section
- Cite FDA guidance documents
- Include sample queries for database

---

## Known Limitations & Caveats

1. **Text Extraction Quality**:
   - 23 PDFs (1.6%) were scanned images - no extractable text
   - Some PDFs have tables/figures with data not captured in text
   - OCR could recover scanned documents but adds complexity

2. **Classification Accuracy**:
   - Regex-based approach may miss creatively worded metrics
   - Some devices may report metrics in supplemental documents not in main 510(k)
   - Confidence scoring helps identify uncertain classifications

3. **Temporal Bias**:
   - FDA AI list may be incomplete for very recent devices
   - Older devices (pre-2018) underrepresented

4. **Regulatory Pathway Differences**:
   - PMA submissions typically more detailed than 510(k)
   - De Novo submissions have different requirements
   - Direct comparison across pathways may not be fair

5. **Specialty Variations**:
   - Different medical specialties have different norms
   - Imaging AI may report differently than diagnostic AI
   - Some metrics more relevant for certain applications

---

## Technical Dependencies

**Python Environment**: 3.9 (confirmed via `__pycache__` artifacts; 3.6 also tested)

**Current `requirements.txt`**:
```
requests==2.26.0
pdfplumber==0.11.8
pandas==1.3.4
tqdm>=4.64.0
```

**To Add for Analysis Phase**:
```
matplotlib
seaborn
```

**Optional Enhancements**:
```
spacy        # NLP for better text analysis
nltk         # Alternative NLP toolkit
pytesseract  # OCR for scanned PDFs
plotly       # Interactive visualizations
```

---

## Sample Queries for Analysis

### Most Transparent Panels
```sql
SELECT
    d.panel,
    COUNT(*) as total_devices,
    SUM(CASE WHEN e.category = 'A' THEN 1 ELSE 0 END) as quantitative,
    ROUND(100.0 * SUM(CASE WHEN e.category = 'A' THEN 1 ELSE 0 END) / COUNT(*), 1) as pct_quantitative
FROM devices d
JOIN evidence_classification e ON d.submission_number = e.submission_number
WHERE e.category IS NOT NULL
GROUP BY d.panel
ORDER BY pct_quantitative DESC;
```

### Temporal Trends
```sql
SELECT
    strftime('%Y', d.decision_date) as year,
    COUNT(*) as total,
    SUM(CASE WHEN e.category = 'A' THEN 1 ELSE 0 END) as quantitative,
    ROUND(100.0 * SUM(CASE WHEN e.category = 'A' THEN 1 ELSE 0 END) / COUNT(*), 1) as pct
FROM devices d
JOIN evidence_classification e ON d.submission_number = e.submission_number
WHERE e.category IS NOT NULL
GROUP BY year
ORDER BY year;
```

### Top Manufacturers by Transparency
```sql
SELECT
    d.company,
    COUNT(*) as devices,
    SUM(CASE WHEN e.category = 'A' THEN 1 ELSE 0 END) as quantitative,
    ROUND(100.0 * SUM(CASE WHEN e.category = 'A' THEN 1 ELSE 0 END) / COUNT(*), 1) as pct
FROM devices d
JOIN evidence_classification e ON d.submission_number = e.submission_number
WHERE e.category IS NOT NULL
GROUP BY d.company
HAVING COUNT(*) >= 5
ORDER BY pct DESC
LIMIT 20;
```

---

## Context for Future Development

### Why This Project Matters

**Regulatory Science Gap**:
- FDA publishes AI device list but no systematic analysis exists
- Researchers study individual devices, not population-level patterns
- Policy discussions about AI regulation lack empirical evidence base

**Patient Safety Implications**:
- Transparency correlates with accountability
- Clinicians need performance data to make informed decisions
- Regulators need benchmarks for future submissions

**Academic/Career Value**:
- First systematic analysis of its kind
- Demonstrates data engineering + domain expertise
- Publishable in regulatory science journals
- Strong portfolio piece for medtech/health AI roles

### Potential Extensions (Phase 2)

1. **Link to Clinical Literature**: Search PubMed for validation studies; many manufacturers publish separately from FDA submission
2. **MAUDE Integration**: Link to adverse event reports — do more transparent devices have fewer incidents?
3. **International Comparison**: Compare FDA submissions to EU MDR/IVDR
4. **Longitudinal Tracking**: Monitor new AI device clearances monthly; automated pipeline for updates
5. **Web Interface**: Public searchable database filtered by specialty, metrics reported, manufacturer

---

## Files Status Reference

### Completed ✅
- `src/pdf_processor.py` - Working for all submission types (K, DEN, P)
- `src/keyword_analyzer.py` - Metric extraction + study size + human factors
- `src/evidence_classifier.py` - Four-category classification
- `scripts/batch_classify_all_devices.py` - Full pipeline (1,430 devices)
- `scripts/inspect_pdf_content.py` - PDF content inspection utility
- `scripts/extract_quantitative_metrics.py` - **NEW** Per-metric extraction for Category A (441 devices); creates `quantitative_metrics` table + exports CSV
- `data/fda_classifications.db` - Complete classification results for 1,430 devices

### To Run Next
- `python scripts/extract_quantitative_metrics.py` — populates `quantitative_metrics` table and exports `data/quantitative_metrics_extracted.csv` (~15 min runtime at 2s/device for 441 devices)

### To Be Created
- `scripts/analyze_trends.py` - Temporal/specialty/pathway analysis
- `scripts/extract_human_factors.py` - Usability evidence (IEC 62366, formative/summative)
- `scripts/generate_final_report.py` - Visualization & export
- `reports/evidence_transparency_report.pdf`
- `reports/methodology.md`
- Enhanced README.md with findings

### To Be Updated
- `config.py` - Fix `DATABASE_PATH` from `data/results.db` to `data/fda_classifications.db`
- `README.md` - Add key findings, visualizations, methodology
- `requirements.txt` - Add matplotlib, seaborn
- `.gitignore` - Verify database and large CSVs are excluded

---

## Immediate Next Action

Run the quantitative metrics extractor (~15 min, resumable if interrupted):

```bash
cd FDA_AIeMD_DB
python scripts/extract_quantitative_metrics.py
```

This fetches the 441 Category A PDFs, extracts per-metric rows (sensitivity %, AUC, etc. with CI bounds), and exports `data/quantitative_metrics_extracted.csv`.

Once complete, check panel-level transparency:

```python
import sqlite3, pandas as pd

conn = sqlite3.connect('data/fda_classifications.db')
transparency = pd.read_sql("""
    SELECT
        panel,
        COUNT(*) as total,
        SUM(CASE WHEN category = 'A' THEN 1 ELSE 0 END) as quantitative,
        SUM(CASE WHEN category = 'B' THEN 1 ELSE 0 END) as qualitative,
        ROUND(100.0 * SUM(CASE WHEN category = 'A' THEN 1 ELSE 0 END) / COUNT(*), 1) as pct_quantitative
    FROM classifications
    WHERE category IS NOT NULL
    GROUP BY panel
    ORDER BY pct_quantitative DESC
""", conn)
print(transparency)
conn.close()
```

All the code will be reviewed by Codex

---

*Last Updated: 2026-04-22*
*Current Phase: Analysis & deliverable generation*
*Priority: Validate classification accuracy, then extract quantitative metrics from Category A devices*
