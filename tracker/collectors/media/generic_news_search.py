from __future__ import annotations

from typing import Any

from tracker.collectors.media.base import BaseMediaCollector


class GenericNewsSearchCollector(BaseMediaCollector):
    def search(self, query: str) -> list[dict[str, Any]]:
        return [{"query": query, "status": "todo", "message": "Implement RSS / site-search driven media ingestion later."}]
