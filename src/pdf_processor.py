import requests
import pdfplumber
from io import BytesIO
import logging
from typing import Optional, Dict

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class PDFProcessor:
    """Stream and extract text from FDA 510(k) PDFs without local storage."""
    
    def __init__(self, timeout: int = 30):
        self.timeout = timeout
    
    def construct_pdf_url(self, k_number: str) -> str:
        """
        Construct PDF URL from k_number.
        Example: K101273 -> https://www.accessdata.fda.gov/cdrh_docs/pdf10/K101273.pdf
        """
        # Extract year from k_number (first 2 digits after K)
        year_suffix = k_number[1:3]
        url = f"https://www.accessdata.fda.gov/cdrh_docs/pdf{year_suffix}/{k_number}.pdf"
        return url
    
    def extract_text(self, k_number: str) -> Optional[str]:
        """
        Stream PDF and extract all text without saving locally.
        Returns None if PDF cannot be accessed or processed.
        """
        url = self.construct_pdf_url(k_number)
        
        try:
            logger.info(f"Fetching {k_number} from {url}")
            response = requests.get(url, timeout=self.timeout, stream=True)
            
            if response.status_code != 200:
                logger.warning(f"Failed to fetch {k_number}: HTTP {response.status_code}")
                return None
            
            # Extract text from PDF in memory
            with pdfplumber.open(BytesIO(response.content)) as pdf:
                full_text = ""
                for page in pdf.pages:
                    text = page.extract_text()
                    if text:
                        full_text += text + "\n"
                
                if not full_text.strip():
                    logger.warning(f"{k_number}: PDF appears to be scanned (no extractable text)")
                    return None
                
                logger.info(f"✓ Successfully extracted {len(full_text)} characters from {k_number}")
                return full_text
                
        except requests.exceptions.Timeout:
            logger.error(f"{k_number}: Timeout after {self.timeout}s")
            return None
        except Exception as e:
            logger.error(f"{k_number}: Error - {str(e)}")
            return None
    
    def extract_with_metadata(self, k_number: str) -> Dict:
        """Extract text and return with metadata."""
        text = self.extract_text(k_number)
        
        return {
            'k_number': k_number,
            'text': text,
            'success': text is not None,
            'char_count': len(text) if text else 0
        }
