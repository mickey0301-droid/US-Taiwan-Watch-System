from __future__ import annotations

from tracker.logging_utils import get_logger
from tracker.notifications.base import BaseNotifier, NotificationMessage


logger = get_logger(__name__)


class EmailNotifier(BaseNotifier):
    channel_name = "email"

    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled

    def send(self, message: NotificationMessage) -> tuple[bool, str | None]:
        if not self.enabled:
            return False, "Email notifier is disabled."
        logger.info("Email notifier placeholder sent message: %s", message.title)
        return True, None
