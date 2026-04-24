import re
from typing import Dict, List, Optional

class PerformanceMetricsExtractor:
    """Extract AI performance metrics from 510(k) summary text."""
    
    # Patterns require either a % sign OR a decimal point to distinguish metric
    # values from counts/sample-sizes (e.g. "sensitivity of 50 patients" must
    # NOT match as sensitivity = 50).  The AUC pattern additionally demands a
    # digit before the decimal so it won't absorb section numbers like "3.2".
    METRIC_PATTERNS = {
        'sensitivity': r'sensitivity[:\s]+(of\s+)?([0-9]+\.?[0-9]*)\s*%',
        'specificity': r'specificity[:\s]+(of\s+)?([0-9]+\.?[0-9]*)\s*%',
        'accuracy':    r'accuracy[:\s]+(of\s+)?([0-9]+\.?[0-9]*)\s*%',
        'auc':         r'(?:AUC|area under (?:the )?(?:ROC )?curve)[:\s]+(of\s+)?(0\.[0-9]+)',
        'precision':   r'precision[:\s]+(of\s+)?([0-9]+\.?[0-9]*)\s*%',
        'ppv':         r'(?:PPV|positive predictive value)[:\s]+(of\s+)?([0-9]+\.?[0-9]*)\s*%',
        'npv':         r'(?:NPV|negative predictive value)[:\s]+(of\s+)?([0-9]+\.?[0-9]*)\s*%',
    }
    
    STUDY_SIZE_PATTERN = r'(?:n\s*=\s*|sample size[:\s]+|cohort of\s+)([0-9,]+)\s*(patients?|images?|studies?|cases?|subjects?)'
    
    HUMAN_FACTORS_KEYWORDS = {
        'standards': ['IEC 62366', 'IEC 60601', 'ISO 14971', 'human factors'],
        'testing': ['formative evaluation', 'summative evaluation', 'usability test'],
        'analysis': ['use error', 'use-related risk', 'task analysis']
    }
    
    def extract_metrics(self, text: str, k_number: str) -> List[Dict]:
        """Extract all performance metrics with context."""
        findings = []
        text_lower = text.lower()
        
        for metric_name, pattern in self.METRIC_PATTERNS.items():
            matches = re.finditer(pattern, text_lower, re.IGNORECASE)
            
            for match in matches:
                value_str = match.group(2) if match.lastindex >= 2 else match.group(1)
                
                try:
                    value = float(value_str)
                    start = max(0, match.start() - 100)
                    end = min(len(text), match.end() + 100)
                    context = text[start:end]
                    
                    findings.append({
                        'k_number': k_number,
                        'metric_type': metric_name,
                        'metric_value': value,
                        'context': context.strip()
                    })
                except ValueError:
                    continue
        
        return findings
    
    def extract_study_size(self, text: str) -> Optional[Dict]:
        """Extract dataset size used for validation."""
        match = re.search(self.STUDY_SIZE_PATTERN, text, re.IGNORECASE)
        
        if match:
            size_str = match.group(1).replace(',', '')
            unit = match.group(2)
            
            try:
                return {
                    'dataset_size': int(size_str),
                    'unit': unit,
                    'context': text[max(0, match.start()-50):match.end()+50]
                }
            except ValueError:
                return None
        
        return None
    
    def check_human_factors(self, text: str) -> Dict:
        """Check for human factors mentions."""
        text_lower = text.lower()
        
        findings = {
            'standards_mentioned': [],
            'testing_mentioned': [],
            'has_use_error_analysis': False
        }
        
        for standard in self.HUMAN_FACTORS_KEYWORDS['standards']:
            if standard.lower() in text_lower:
                findings['standards_mentioned'].append(standard)
        
        for test_type in self.HUMAN_FACTORS_KEYWORDS['testing']:
            if test_type.lower() in text_lower:
                findings['testing_mentioned'].append(test_type)
        
        for error_term in self.HUMAN_FACTORS_KEYWORDS['analysis']:
            if error_term.lower() in text_lower:
                findings['has_use_error_analysis'] = True
                break
        
        return findings
    
    def analyze_document(self, text: str, k_number: str) -> Dict:
        """Complete analysis of a 510(k) document."""
        return {
            'k_number': k_number,
            'metrics': self.extract_metrics(text, k_number),
            'study_info': self.extract_study_size(text),
            'human_factors': self.check_human_factors(text)
        }