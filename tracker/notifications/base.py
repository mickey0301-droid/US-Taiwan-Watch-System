from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class NotificationMessage:
    title: str
    body: str
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)


class BaseNotifier(ABC):
    channel_name = "base"

    @abstractmethod
    def send(self, message: NotificationMessage) -> tuple[bool, str | None]:
        raise NotImplementedError
