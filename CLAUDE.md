# FDA AI Medical Device Evidence Analysis — Project Notes

Systematic analysis of 1,430 FDA-cleared AI/ML medical devices, examining evidence transparency in regulatory submissions. Built for a biomedical research collaborator and as a data engineering portfolio project.

---

## Pipeline (run in order)

| Script | What it does | Runtime |
|---|---|---|
| `batch_classify_all_devices.py` | Fetches each device's PDF from FDA servers, extracts text, classifies into A/B/C/D | ~1 hr |
| `extract_quantitative_metrics.py` | Pulls numeric metric values (sensitivity, AUC, etc.) from Category A devices | ~15 min |
| `extract_ai_ethics_signals.py` | Scans all cached text for ethics, fairness, privacy, and XAI language | ~2 min |
| `analyze_fairness_depth.py` | Re-classifies fairness mentions to separate AI fairness from statistical false positives | ~1 min |
| `analyze_trends.py` | Generates all 11 figures in `reports/` and summary CSVs in `data/` | ~30 sec |

All scripts are resumable — they skip already-processed devices. PDF text is cached in `data/text_cache/` after first fetch so steps 3–5 need no network access.

---

## Database Schema

**`fda_classifications.db`** — four tables:

```sql
classifications          -- one row per device, primary results table
    k_number TEXT PK     -- submission ID: K######, DEN######, or P######
    device, company, panel, product_code
    decision_date, decision_year
    category TEXT        -- 'A', 'B', 'C', 'D' (NULL = failed extraction)
    label TEXT           -- human-readable category name
    confidence TEXT      -- 'high', 'medium', 'low'
    metric_count INTEGER
    dataset_size, dataset_unit, study_type
    qualitative_signals, technical_signals, equivalence_signals  -- semicolon-sep matched terms
    fetch_status TEXT    -- 'ok' or 'pdf_empty'
    processed_at TEXT

quantitative_metrics     -- one row per metric occurrence, Category A devices only
    k_number TEXT FK
    metric_type TEXT     -- 'sensitivity', 'specificity', 'auc', 'accuracy', 'ppv', 'npv'
    metric_value REAL
    ci_lower, ci_upper REAL   -- 95% CI bounds if found
    context TEXT         -- surrounding text snippet

ai_ethics_signals        -- one row per device, ethics/fairness/XAI keyword flags
    k_number TEXT PK FK
    has_fairness_bias, has_privacy, has_xai_general, has_xai_method,
    has_data_provenance, has_ethics_general  INTEGER (0/1)
    has_shap, has_lime, has_saliency, has_probability_map,
    has_tornado_plot, has_grad_cam  INTEGER (0/1)
    count_* INTEGER, matched_*_terms TEXT
    text_source TEXT     -- 'cache', 'fetched', or 'unavailable'

fairness_depth           -- corrected fairness classification, one row per device
    k_number TEXT PK FK
    fairness_type TEXT   -- 'algorithmic', 'statistical_only', 'mixed', 'none'
    fairness_depth TEXT  -- 'quantified_subgroup', 'tested_subgroup', 'acknowledged',
                         --  'statistical_only', 'absent'
    has_algorithmic_fairness, has_statistical_bias, has_subgroup_numbers INTEGER
    mentions_race, mentions_sex, mentions_age, mentions_skin_tone,
    mentions_geography, mentions_socioeconomic  INTEGER
    algorithmic_terms, statistical_terms TEXT
    context_snippets TEXT   -- pipe-separated 150-char windows around matches
```

---

## Evidence Categories

| Cat | Label | Description |
|---|---|---|
| A | Quantitative | Reports specific numeric metrics (sensitivity %, AUC, etc.) |
| B | Qualitative | Mentions clinical testing occurred, no numbers given |
| C | Technical Only | Bench/phantom/software tests only, no patient outcome data |
| D | Equivalence Only | Relies entirely on a predicate device, no new testing |

Classification priority: A > B > C > D. Confidence scoring ('high'/'medium'/'low') reflects how many signals were matched.

---

## Key Design Decisions

- **No local PDF storage**: PDFs are streamed via `requests` + `BytesIO` and immediately extracted with `pdfplumber`. Only the text is cached.
- **Fairness false positives**: The raw `has_fairness_bias` flag in `ai_ethics_signals` is inflated (~41%) because "bias" is a clinical chemistry/epidemiology term. Use `fairness_depth.has_algorithmic_fairness` (3.9%) for accurate counts.
- **Submission types**: URLs differ by prefix — K (510k), DEN (De Novo), P (PMA). `pdf_processor.py` handles all three with fallback URL patterns.

---

## Limitations

- 22 devices (1.5%) have scanned PDFs with no extractable text
- Classification is regex/keyword-based — unusual phrasing may be missed
- All performance data is manufacturer-reported, not independently validated
- Only the primary submission document is analyzed; supplemental appendices are not captured
- The `has_fairness_bias` flag in `ai_ethics_signals` conflates statistical and algorithmic bias — always prefer `fairness_depth` table for fairness analysis

---

## Possible Extensions

- Human factors analysis: IEC 62366, formative/summative usability testing mentions
- MAUDE linkage: connect to FDA adverse event reports by device
- PubMed linkage: match devices to published validation studies
- Monthly pipeline: automate updates as new devices are cleared
- EU MDR/IVDR comparison: equivalent analysis on European regulatory submissions
