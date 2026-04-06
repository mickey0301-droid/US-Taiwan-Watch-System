from __future__ import annotations

from tracker.config import get_keywords


class RelevanceService:
    def __init__(self) -> None:
        self.terms = get_keywords().get("taiwan_relevance_terms", [])

    def score_text(self, text: str) -> tuple[float, list[str]]:
        # TODO: Replace or augment this rule-based stage with an AI relevance classifier.
        lowered = text.lower()
        hits = [term for term in self.terms if term.lower() in lowered]
        score = min(1.0, len(hits) * 0.15)
        return score, hits
