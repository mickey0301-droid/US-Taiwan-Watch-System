from __future__ import annotations

import re
from datetime import datetime

from tracker.utils.hashing import sha256_text


class DedupeService:
    def build_statement_hash(self, title: str, source_url: str, raw_text: str | None) -> str:
        return sha256_text(f"{source_url}|{title}|{raw_text or ''}")

    def build_event_key(
        self,
        person_id: int | None,
        title: str,
        raw_text: str | None,
        date_published: datetime | None,
        statement_type: str | None,
    ) -> str:
        normalized_title = self._normalize_text(title)
        normalized_text = self._normalize_text((raw_text or "")[:500])
        event_date = (date_published.date().isoformat() if date_published else "unknown-date")
        return sha256_text(f"{statement_type or 'unknown'}|{event_date}|{normalized_title}|{normalized_text}")

    def _normalize_text(self, value: str) -> str:
        lowered = value.lower()
        lowered = re.sub(r"https?://\S+", " ", lowered)
        lowered = re.sub(r"[^a-z0-9\s]", " ", lowered)
        lowered = re.sub(r"\s+", " ", lowered).strip()
        return lowered[:240]
