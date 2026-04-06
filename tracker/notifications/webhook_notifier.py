from __future__ import annotations

import httpx

from tracker.logging_utils import get_logger
from tracker.notifications.base import BaseNotifier, NotificationMessage


logger = get_logger(__name__)


class WebhookNotifier(BaseNotifier):
    channel_name = "webhook"

    def __init__(self, webhook_url: str | None, enabled: bool = False, timeout: int = 15) -> None:
        self.webhook_url = webhook_url
        self.enabled = enabled
        self.timeout = timeout

    def send(self, message: NotificationMessage) -> tuple[bool, str | None]:
        if not self.enabled or not self.webhook_url:
            return False, "Webhook notifier is not configured."
        try:
            response = httpx.post(
                self.webhook_url,
                json={
                    "title": message.title,
                    "body": message.body,
                    "event_type": message.event_type,
                    "payload": message.payload,
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
        except Exception as exc:
            logger.exception("Webhook send failed.")
            return False, str(exc)
        return True, None
