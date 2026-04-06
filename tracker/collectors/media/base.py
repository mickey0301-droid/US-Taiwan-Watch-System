from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseMediaCollector(ABC):
    @abstractmethod
    def search(self, query: str) -> list[dict[str, Any]]:
        raise NotImplementedError
