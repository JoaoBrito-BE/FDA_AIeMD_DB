# Configuration for FDA device analysis

# PDF URL pattern
PDF_URL_TEMPLATE = "https://www.accessdata.fda.gov/cdrh_docs/pdf{year}/{k_number}.pdf"

# Keywords to search for
KEYWORDS = {
    'usability': ['usability', 'human factors', 'use error', 'user interface'],
    'standards': ['IEC 62366', 'IEC 60601', 'ISO 14971'],
    'testing': ['formative evaluation', 'summative evaluation', 'validation study'],
    'metrics': ['sensitivity', 'specificity', 'AUC', 'accuracy', 'precision', 'recall']
}

# Database settings
DATABASE_PATH = 'data/results.db'

# Processing limits (for testing)
TEST_BATCH_SIZE = 50
MAX_RETRIES = 3
TIMEOUT_SECONDS = 30
