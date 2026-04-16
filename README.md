# FDA_AIeMD_DB
Systematic analysis of FDA 510(k) submissions for AI/ML-enabled medical devices, focusing on usability testing practices and validation metrics.

## Project Goal
Analyze regulatory submissions to understand:
- What usability testing is required for AI medical devices
- Types of evidence provided (formative, summative, validation studies)
- Performance metrics reported (sensitivity, specificity, AUC)
- Compliance with human factors standards (IEC 62366)

## Technical approach
FDA openFDA API → Get k_numbers
↓
Construct PDF URLs
↓
Stream PDF (no local save) → Extract text
↓
Keyword analysis in memory
↓
SQLite database (results only)


