"""
Keyword-based detector for AI ethics, fairness, privacy, explainability,
and data-provenance language in FDA submission text.

Returns per-group counts and matched terms — no numeric value extraction,
just presence/frequency of concepts.
"""
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Keyword catalogue
# ---------------------------------------------------------------------------

# Each group maps a concept label to a list of search terms.
# Terms are matched case-insensitively as substrings unless they contain
# a space (phrase) or are marked with \b for word-boundary matching.
# Short ambiguous terms (e.g. "LIME") use word boundaries to avoid false
# positives (e.g. "sublime", "time").

KEYWORD_GROUPS: Dict[str, List[str]] = {
    'fairness_bias': [
        r'\bbias\b',
        r'\bbiased\b',
        r'\bunbiased\b',
        r'\bfairness\b',
        r'\bfair\b',
        r'\bequit',          # equity, equitable
        r'\bdisparit',       # disparity, disparities
        r'\bsubgroup',
        r'\bdemographic',
        r'underrepresent',
        r'racial',
        r'gender bias',
        r'protected attribute',
    ],
    'privacy': [
        r'\bhipaa\b',
        r'k-anonymity',
        r'\bde.identif',     # de-identification, de-identified
        r'deidentif',
        r'\bprivacy\b',
        r'\banonymiz',       # anonymization, anonymized
        r'\banonymis',       # British spelling
        r'data protection',
        r'personally identifiable',
        r'\bpii\b',
        r'informed consent',
        r'data governance',
    ],
    'xai_general': [
        r'explainabilit',    # explainability
        r'\bexplainable\b',
        r'interpretabilit',
        r'\binterpretable\b',
        r'black.box',        # black box, black-box
        r'blackbox',
        r'\btransparency\b',
        r'\btransparent\b',
        r'model explanation',
        r'audit trail',
        r'model interpretab',
        r'human.in.the.loop',
    ],
    'xai_methods': [
        r'\bshap\b',
        r'\blime\b',
        r'saliency map',
        r'saliency plot',
        r'probability map',
        r'probability plot',
        r'tornado plot',
        r'tornado diagram',
        r'\bgrad.cam\b',
        r'class activation',
        r'attention map',
        r'feature importance',
        r'feature attribution',
        r'partial dependence',
    ],
    'data_provenance': [
        r'\bprospective\b',
        r'\bretrospective\b',
        r'synthetic data',
        r'simulated data',
        r'real.world data',
        r'real.world evidence',
        r'\brwe\b',
        r'federated learning',
        r'\bfederated\b',
        r'multi.site',
        r'multi.center',
        r'multicenter',
        r'multisite',
        r'external validation',
        r'independent test',
    ],
    'ethics_general': [
        r'\bethic',          # ethics, ethical
        r'responsible ai',
        r'trustworthy ai',
        r'\baccountab',      # accountability, accountable
        r'algorithmic fairness',
        r'ai safety',
        r'clinical oversight',
        r'human oversight',
        r'intended use population',
    ],
}

# Convenience: which xai_methods terms map to specific named methods
NAMED_XAI_METHODS = {
    'shap':            r'\bshap\b',
    'lime':            r'\blime\b',
    'saliency':        r'saliency map|saliency plot',
    'probability_map': r'probability map|probability plot',
    'tornado_plot':    r'tornado plot|tornado diagram',
    'grad_cam':        r'\bgrad.cam\b',
}


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class EthicsResult:
    k_number: str

    # Group-level presence flags
    has_fairness_bias:   bool = False
    has_privacy:         bool = False
    has_xai_general:     bool = False
    has_xai_method:      bool = False
    has_data_provenance: bool = False
    has_ethics_general:  bool = False

    # Named XAI method flags
    has_shap:            bool = False
    has_lime:            bool = False
    has_saliency:        bool = False
    has_probability_map: bool = False
    has_tornado_plot:    bool = False
    has_grad_cam:        bool = False

    # Group-level match counts (number of distinct terms found)
    count_fairness_bias:   int = 0
    count_privacy:         int = 0
    count_xai_general:     int = 0
    count_xai_methods:     int = 0
    count_data_provenance: int = 0
    count_ethics_general:  int = 0

    # Matched term lists (semicolon-joined for DB storage)
    matched_fairness_terms:   str = ''
    matched_privacy_terms:    str = ''
    matched_xai_terms:        str = ''
    matched_data_terms:       str = ''
    matched_ethics_terms:     str = ''

    total_signal_count: int = 0
    text_source:        str = 'unavailable'   # 'cache', 'fetched', 'unavailable'

    def to_dict(self) -> Dict:
        return {
            'k_number':              self.k_number,
            'has_fairness_bias':     int(self.has_fairness_bias),
            'has_privacy':           int(self.has_privacy),
            'has_xai_general':       int(self.has_xai_general),
            'has_xai_method':        int(self.has_xai_method),
            'has_data_provenance':   int(self.has_data_provenance),
            'has_ethics_general':    int(self.has_ethics_general),
            'has_shap':              int(self.has_shap),
            'has_lime':              int(self.has_lime),
            'has_saliency':          int(self.has_saliency),
            'has_probability_map':   int(self.has_probability_map),
            'has_tornado_plot':      int(self.has_tornado_plot),
            'has_grad_cam':          int(self.has_grad_cam),
            'count_fairness_bias':   self.count_fairness_bias,
            'count_privacy':         self.count_privacy,
            'count_xai_general':     self.count_xai_general,
            'count_xai_methods':     self.count_xai_methods,
            'count_data_provenance': self.count_data_provenance,
            'count_ethics_general':  self.count_ethics_general,
            'matched_fairness_terms':self.matched_fairness_terms,
            'matched_privacy_terms': self.matched_privacy_terms,
            'matched_xai_terms':     self.matched_xai_terms,
            'matched_data_terms':    self.matched_data_terms,
            'matched_ethics_terms':  self.matched_ethics_terms,
            'total_signal_count':    self.total_signal_count,
            'text_source':           self.text_source,
        }


# ---------------------------------------------------------------------------
# Analyser
# ---------------------------------------------------------------------------

class AIEthicsAnalyzer:

    def analyze(self, text: str, k_number: str,
                text_source: str = 'fetched') -> EthicsResult:
        result = EthicsResult(k_number=k_number, text_source=text_source)
        t = text.lower()

        def _find(patterns: List[str]) -> Tuple[int, List[str]]:
            """Return (count of matched patterns, list of matched readable terms)."""
            found = []
            for pat in patterns:
                if re.search(pat, t):
                    # Strip regex metacharacters for display
                    label = re.sub(r'[\\b\(\)\.\+\*\?\[\]\^]', '', pat).strip()
                    found.append(label)
            return len(found), found

        # Fairness / bias
        n, terms = _find(KEYWORD_GROUPS['fairness_bias'])
        result.count_fairness_bias   = n
        result.has_fairness_bias     = n > 0
        result.matched_fairness_terms = '; '.join(terms)

        # Privacy
        n, terms = _find(KEYWORD_GROUPS['privacy'])
        result.count_privacy   = n
        result.has_privacy     = n > 0
        result.matched_privacy_terms = '; '.join(terms)

        # XAI general
        n, terms = _find(KEYWORD_GROUPS['xai_general'])
        result.count_xai_general = n
        result.has_xai_general   = n > 0

        # XAI methods
        n, terms = _find(KEYWORD_GROUPS['xai_methods'])
        result.count_xai_methods = n
        result.has_xai_method    = n > 0
        result.matched_xai_terms = '; '.join(terms)

        # Named methods
        result.has_shap            = bool(re.search(NAMED_XAI_METHODS['shap'],            t))
        result.has_lime            = bool(re.search(NAMED_XAI_METHODS['lime'],            t))
        result.has_saliency        = bool(re.search(NAMED_XAI_METHODS['saliency'],        t))
        result.has_probability_map = bool(re.search(NAMED_XAI_METHODS['probability_map'], t))
        result.has_tornado_plot    = bool(re.search(NAMED_XAI_METHODS['tornado_plot'],    t))
        result.has_grad_cam        = bool(re.search(NAMED_XAI_METHODS['grad_cam'],        t))

        # Data provenance
        n, terms = _find(KEYWORD_GROUPS['data_provenance'])
        result.count_data_provenance = n
        result.has_data_provenance   = n > 0
        result.matched_data_terms    = '; '.join(terms)

        # General ethics
        n, terms = _find(KEYWORD_GROUPS['ethics_general'])
        result.count_ethics_general = n
        result.has_ethics_general   = n > 0
        result.matched_ethics_terms = '; '.join(terms)

        result.total_signal_count = (
            int(result.has_fairness_bias) + int(result.has_privacy) +
            int(result.has_xai_general)   + int(result.has_xai_method) +
            int(result.has_data_provenance) + int(result.has_ethics_general)
        )

        return result

    def empty_result(self, k_number: str) -> EthicsResult:
        """Return a placeholder row for devices with no retrievable text."""
        return EthicsResult(k_number=k_number, text_source='unavailable')
