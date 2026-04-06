from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class CollectorRunResult:
    job_name: str
    source_name: str
    started_at: datetime
    ended_at: datetime | None = None
    records_found: int = 0
    records_created: int = 0
    records_updated: int = 0
    records_deactivated: int = 0
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseCollector(ABC):
    collector_name = "base"
    source_name = "unknown"

    @abstractmethod
    def fetch(self) -> Any:
        raise NotImplementedError

    @abstractmethod
    def parse(self, payload: Any) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def sync(self) -> CollectorRunResult:
        raise NotImplementedError
