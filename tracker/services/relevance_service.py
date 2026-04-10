from __future__ import annotations

import re

from tracker.config import get_keywords


class RelevanceService:
    def __init__(self) -> None:
        self.terms = get_keywords().get("taiwan_relevance_terms", [])

    def score_text(self, text: str) -> tuple[float, list[str]]:
        # TODO: Replace or augment this rule-based stage with an AI relevance classifier.
        if self.is_taiwan_time_only_reference(text):
            return 0.0, []
        lowered = text.lower()
        hits = [term for term in self.terms if term.lower() in lowered]
        score = min(1.0, len(hits) * 0.15)
        return score, hits

    @staticmethod
    def is_taiwan_time_only_reference(text: str) -> bool:
        content = str(text or "")
        if not content:
            return False
        lowered = content.lower()

        has_time_phrase = any(
            phrase in content or phrase in lowered
            for phrase in [
                "台灣時間",
                "臺灣時間",
                "台北時間",
                "臺北時間",
                "台灣時區",
                "臺灣時區",
                "taiwan time",
                "taipei time",
                "taiwan timezone",
                "taipei timezone",
                "taiwan standard time",
                "taipei standard time",
            ]
        )
        if not has_time_phrase:
            return False

        # If text contains Taiwan/Taipei in non-time contexts, keep it.
        has_non_time_taiwan_zh = re.search(r"(台灣|臺灣)(?!時間|時區|當地時間)", content) is not None
        has_non_time_taiwan_en = re.search(r"\btaiwan\b(?!\s*(time|timezone|standard time|local time))", lowered) is not None
        has_non_time_taipei_zh = re.search(r"台北(?!時間|時區|當地時間)", content) is not None
        has_non_time_taipei_en = re.search(r"\btaipei\b(?!\s*(time|timezone|standard time|local time))", lowered) is not None
        if has_non_time_taiwan_zh or has_non_time_taiwan_en or has_non_time_taipei_zh or has_non_time_taipei_en:
            return False

        strong_context_terms = [
            "台海",
            "台灣海峽",
            "對台",
            "援台",
            "訪台",
            "台美",
            "台積電",
            "taiwan strait",
            "cross-strait",
            "cross strait",
            "taiwan relations act",
            "u.s.-taiwan",
            "us-taiwan",
        ]
        if any(term in content or term in lowered for term in strong_context_terms):
            return False
        return True
