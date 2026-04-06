from __future__ import annotations

import os
from typing import Any

from sqlalchemy.orm import Session

from tracker.config import get_settings
from tracker.models import NotificationLog
from tracker.notifications.base import NotificationMessage
from tracker.notifications.email_notifier import EmailNotifier
from tracker.notifications.webhook_notifier import WebhookNotifier


class NotificationService:
    def __init__(self, session: Session) -> None:
        self.session = session
        settings = get_settings()
        webhook_config = settings.notifications.get("webhook", {})
        email_config = settings.notifications.get("email", {})
        webhook_url = os.getenv("TRACKER_WEBHOOK_URL") or webhook_config.get("webhook_url")
        self.notifiers = [
            WebhookNotifier(webhook_url=webhook_url, enabled=webhook_config.get("enabled", False)),
            EmailNotifier(enabled=email_config.get("enabled", False)),
        ]

    def notify(self, event_type: str, title: str, body: str, target_identifier: str | None = None, payload: dict[str, Any] | None = None) -> None:
        message = NotificationMessage(title=title, body=body, event_type=event_type, payload=payload or {})
        for notifier in self.notifiers:
            success, error = notifier.send(message)
            self.session.add(
                NotificationLog(
                    channel=notifier.channel_name,
                    event_type=event_type,
                    target_identifier=target_identifier,
                    status="sent" if success else "skipped" if error and "disabled" in error.lower() else "failed",
                    payload=payload or {},
                    error_message=error,
                )
            )
