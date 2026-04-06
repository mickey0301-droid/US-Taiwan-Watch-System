from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import select

from tracker.db import session_scope
from tracker.models import NotificationLog


def render(lang: str, labels: dict[str, str]) -> None:
    st.header(labels["notifications"])
    with session_scope() as session:
        rows = session.execute(
            select(
                NotificationLog.channel,
                NotificationLog.event_type,
                NotificationLog.status,
                NotificationLog.target_identifier,
                NotificationLog.sent_at,
                NotificationLog.error_message,
            )
            .order_by(NotificationLog.sent_at.desc())
            .limit(100)
        ).all()
    st.dataframe(pd.DataFrame(rows, columns=["channel", "event_type", "status", "target_identifier", "sent_at", "error"]), use_container_width=True)
