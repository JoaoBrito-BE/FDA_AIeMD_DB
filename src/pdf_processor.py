import os
import requests
import pdfplumber
from io import BytesIO
import logging
from typing import Optional, Dict

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = os.path.join(os.path.dirname(__file__), '..', 'data', 'text_cache')


class PDFProcessor:
    """Stream and extract text from FDA submission PDFs.

    Text is cached to disk on first fetch so subsequent calls (ethics analysis,
    re-classification, etc.) require no network round-trips.
    """

    def __init__(self, timeout: int = 30, cache_dir: str = DEFAULT_CACHE_DIR):
        self.timeout   = timeout
        self.cache_dir = os.path.abspath(cache_dir)
        os.makedirs(self.cache_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # URL construction
    # ------------------------------------------------------------------

    def construct_pdf_url(self, submission_number: str) -> Optional[str]:
        """Primary URL for a submission (main decision document)."""
        base = submission_number.split('/')[0].strip()
        if base.startswith('K'):
            yr = base[1:3]
            return f"https://www.accessdata.fda.gov/cdrh_docs/pdf{yr}/{base}.pdf"
        elif base.startswith('DEN'):
            return f"https://www.accessdata.fda.gov/cdrh_docs/reviews/{base}.pdf"
        elif base.startswith('P'):
            yr = base[1:3]
            return f"https://www.accessdata.fda.gov/cdrh_docs/pdf{yr}/{base}a.pdf"
        else:
            logger.error(f"Unknown submission type: {submission_number}")
            return None

    def _fallback_urls(self, submission_number: str) -> list:
        """Alternative URLs to try when the primary fetch returns no text."""
        base = submission_number.split('/')[0].strip()
        urls = []

        if base.startswith('K'):
            yr = base[1:3]
            # 510(k) summary document (manufacturers must submit a separate summary)
            urls.append(
                f"https://www.accessdata.fda.gov/cdrh_docs/pdf{yr}/{base}S.pdf"
            )
            # Occasional alternate casing / path for very old submissions
            urls.append(
                f"https://www.accessdata.fda.gov/cdrh_docs/pdf{yr}/{base}s.pdf"
            )
        elif base.startswith('DEN'):
            yr = base[3:5]
            # Some De Novo summaries live under the year-based path
            urls.append(
                f"https://www.accessdata.fda.gov/cdrh_docs/pdf{yr}/{base}.pdf"
            )
        elif base.startswith('P'):
            yr = base[1:3]
            supplement = submission_number.split('/')[1].strip() if '/' in submission_number else None
            letters = list('bcdefg')
            if supplement:
                sup = supplement.strip()
                for ltr in letters:
                    urls.append(
                        f"https://www.accessdata.fda.gov/cdrh_docs/pdf{yr}/{base}{sup}{ltr}.pdf"
                    )
                urls.append(
                    f"https://www.accessdata.fda.gov/cdrh_docs/pdf{yr}/{base}{sup}.pdf"
                )
            else:
                for ltr in letters:
                    urls.append(
                        f"https://www.accessdata.fda.gov/cdrh_docs/pdf{yr}/{base}{ltr}.pdf"
                    )

        return urls

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    def _cache_path(self, submission_number: str) -> str:
        safe = submission_number.replace('/', '_').replace('\\', '_')
        return os.path.join(self.cache_dir, f"{safe}.txt")

    def _read_cache(self, submission_number: str) -> Optional[str]:
        path = self._cache_path(submission_number)
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                text = f.read()
            return text if text.strip() else None
        return None

    def _write_cache(self, submission_number: str, text: str) -> None:
        path = self._cache_path(submission_number)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)

    # ------------------------------------------------------------------
    # PDF fetching
    # ------------------------------------------------------------------

    def _fetch_url(self, url: str) -> Optional[str]:
        """Fetch a single URL and extract text. Returns None on any failure."""
        try:
            response = requests.get(url, timeout=self.timeout, stream=True)
            if response.status_code != 200:
                return None
            with pdfplumber.open(BytesIO(response.content)) as pdf:
                pages = [page.extract_text() for page in pdf.pages]
                text  = '\n'.join(p for p in pages if p)
            return text if text.strip() else None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_text(self, submission_number: str) -> Optional[str]:
        """Return full document text, using disk cache when available.

        On a cache miss, tries the primary URL then each fallback URL in order.
        Successful text is always written to cache before returning.
        """
        cached = self._read_cache(submission_number)
        if cached:
            logger.info(f"{submission_number}: served from cache")
            return cached

        primary = self.construct_pdf_url(submission_number)
        urls    = ([primary] if primary else []) + self._fallback_urls(submission_number)

        for url in urls:
            text = self._fetch_url(url)
            if text:
                logger.info(f"{submission_number}: fetched from {url.split('/')[-1]}"
                            f" ({len(text):,} chars)")
                self._write_cache(submission_number, text)
                return text

        logger.warning(f"{submission_number}: no text retrieved from any URL")
        return None

    def extract_with_metadata(self, submission_number: str) -> Dict:
        text = self.extract_text(submission_number)
        return {
            'k_number':   submission_number,
            'text':       text,
            'success':    text is not None,
            'char_count': len(text) if text else 0,
            'from_cache': self._read_cache(submission_number) == text,
        }
