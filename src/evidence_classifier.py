import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .keyword_analyzer import PerformanceMetricsExtractor


CATEGORY_LABELS = {
    'A': 'Quantitative',
    'B': 'Qualitative',
    'C': 'Technical_Only',
    'D': 'Equivalence_Only',
}

# Additional numeric metric patterns not covered by PerformanceMetricsExtractor.
# Kept specific to performance/agreement metrics — generic statistical terms
# (p-value, kappa, CI) were removed because they appear in bench/tech documents
# and would falsely promote non-clinical submissions to Category A.
_EXTRA_NUMERIC_PATTERNS = [
    r'(?:f1|f-1|f1[- ]score|f[- ]measure)[:\s=]+([0-9]+\.?[0-9]*)',
    r'(?:dice|iou|jaccard)[:\s=]+([0-9]+\.?[0-9]*)',
    r'(?:mae|rmse|mse)\b[:\s=]+([0-9]+\.?[0-9]*)',
    r'(?:positive percent agreement|negative percent agreement|ppa|npa)[:\s]+(of\s+)?([0-9]+\.?[0-9]*)\s*%',
    r'(?:overall agreement|percent agreement|concordance)[:\s]+(of\s+)?([0-9]+\.?[0-9]*)\s*%',
]

_QUALITATIVE_SIGNALS = [
    'clinical study', 'clinical evaluation', 'clinical performance',
    'reader study', 'clinical validation', 'clinical testing',
    'retrospective study', 'prospective study', 'retrospective analysis',
    'patient population', 'patient cohort', 'patient data',
    'clinical trial', 'clinical investigation',
    'performance was evaluated', 'performance was tested', 'performance was assessed',
    'testing was performed', 'validation was performed', 'study was performed',
    'evaluation was performed', 'testing was conducted', 'study was conducted',
    'validation study', 'performance study', 'observer study',
    'reader performance', 'stand-alone mode',
    'clinical performance testing', 'predicate performance',
    'patients were enrolled', 'subjects were enrolled',
]

_TECHNICAL_SIGNALS = [
    'bench test', 'benchtop test', 'bench-top test',
    'biocompatibility', 'biocompatible',
    'electrical safety', 'electromagnetic compatibility', 'emc test',
    'software validation', 'software verification',
    'iec 62304', 'iec 60601-1', 'iso 13485',
    'unit test', 'regression test', 'stress test',
    'analytical validation', 'analytical performance',
    'in vitro', 'laboratory testing',
    'phantom study', 'phantom testing',
    'mechanical testing', 'shelf life', 'sterilization',
    'cybersecurity testing', 'penetration test',
]

_EQUIVALENCE_SIGNALS = [
    'substantially equivalent', 'substantial equivalence',
    'predicate device',
    'legally marketed device',
    'same intended use',
    'same technological characteristics',
    'no new issues of safety',
    'compared to predicate',
    'equivalent to the predicate',
    'predicate submission',
    'predicate 510(k)',
]


@dataclass
class ClassificationResult:
    k_number: str
    category: str
    label: str
    confidence: str  # 'high', 'medium', 'low'
    metric_count: int
    signals: Dict[str, List[str]] = field(default_factory=dict)
    study_info: Optional[Dict] = None

    def to_dict(self) -> Dict:
        return {
            'k_number': self.k_number,
            'category': self.category,
            'label': self.label,
            'confidence': self.confidence,
            'metric_count': self.metric_count,
            'qualitative_signals': '; '.join(self.signals.get('qualitative', [])),
            'technical_signals': '; '.join(self.signals.get('technical', [])),
            'equivalence_signals': '; '.join(self.signals.get('equivalence', [])),
            'dataset_size': self.study_info.get('dataset_size') if self.study_info else None,
            'dataset_unit': self.study_info.get('unit') if self.study_info else None,
        }


class EvidenceClassifier:
    """
    Classify FDA 510(k) submissions by evidence type.

    Priority (highest wins):
      A  Quantitative  — reports named numeric performance metrics
      B  Qualitative   — clinical testing described but no numeric values
      C  Technical_Only — bench/software testing only, no clinical language
      D  Equivalence_Only — predicate reliance, no new testing
    """

    def __init__(self):
        self._extractor = PerformanceMetricsExtractor()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def classify(self, text: str, k_number: str) -> ClassificationResult:
        text_lower = text.lower()

        named_metrics = self._extractor.extract_metrics(text, k_number)
        extra_metrics = self._count_extra_numeric_patterns(text_lower)
        metric_count = len(named_metrics) + extra_metrics

        qual_hits = self._match_signals(text_lower, _QUALITATIVE_SIGNALS)
        tech_hits = self._match_signals(text_lower, _TECHNICAL_SIGNALS)
        equiv_hits = self._match_signals(text_lower, _EQUIVALENCE_SIGNALS)

        study_info = self._extractor.extract_study_size(text)

        signals = {
            'qualitative': qual_hits,
            'technical': tech_hits,
            'equivalence': equiv_hits,
        }

        category, confidence = self._decide(
            metric_count, qual_hits, tech_hits, equiv_hits, text_lower
        )

        return ClassificationResult(
            k_number=k_number,
            category=category,
            label=CATEGORY_LABELS[category],
            confidence=confidence,
            metric_count=metric_count,
            signals=signals,
            study_info=study_info,
        )

    def classify_batch(self, items: List[Dict]) -> List[ClassificationResult]:
        """Classify a list of {'k_number': ..., 'text': ...} dicts."""
        results = []
        for item in items:
            if item.get('text'):
                results.append(self.classify(item['text'], item['k_number']))
        return results

    # ------------------------------------------------------------------
    # Classification logic
    # ------------------------------------------------------------------

    def _decide(
        self,
        metric_count: int,
        qual_hits: List[str],
        tech_hits: List[str],
        equiv_hits: List[str],
        text_lower: str,
    ):
        has_clinical = len(qual_hits) > 0
        has_technical = len(tech_hits) > 0
        has_equivalence = len(equiv_hits) > 0

        # --- Category A: quantitative metrics found ---
        if metric_count > 0:
            if metric_count >= 3 or (metric_count >= 1 and has_clinical):
                confidence = 'high'
            else:
                confidence = 'medium'
            return 'A', confidence

        # --- Category B: clinical language without numbers ---
        if has_clinical:
            if len(qual_hits) >= 3:
                confidence = 'high'
            elif len(qual_hits) >= 1:
                confidence = 'medium'
            else:
                confidence = 'low'
            return 'B', confidence

        # --- Category C: bench/technical testing only ---
        if has_technical and not has_clinical:
            confidence = 'high' if len(tech_hits) >= 2 else 'medium'
            return 'C', confidence

        # --- Category D: equivalence / nothing ---
        if has_equivalence:
            confidence = 'high' if len(equiv_hits) >= 2 else 'medium'
        else:
            # No signals at all — treat as D with low confidence
            confidence = 'low'
        return 'D', confidence

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _match_signals(text_lower: str, signal_list: List[str]) -> List[str]:
        return [s for s in signal_list if s in text_lower]

    @staticmethod
    def _count_extra_numeric_patterns(text_lower: str) -> int:
        count = 0
        for pattern in _EXTRA_NUMERIC_PATTERNS:
            if re.search(pattern, text_lower):
                count += 1
        return count
