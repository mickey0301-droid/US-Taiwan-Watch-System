from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from tracker.models import Person, Tracker, TrackerTarget


class TrackerService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_trackers(self) -> list[Tracker]:
        return self.session.execute(select(Tracker).order_by(Tracker.updated_at.desc())).scalars().all()

    def list_people(self) -> list[Person]:
        return self.session.execute(select(Person).order_by(Person.full_name.asc())).scalars().all()

    def get_tracker(self, tracker_id: int) -> Tracker | None:
        return self.session.get(Tracker, tracker_id)

    def create_or_update_tracker(
        self,
        tracker_id: int | None,
        person_id: int,
        name: str,
        status: str,
        include_primary_sources: bool,
        include_media_reports: bool,
        schedule_cron: str | None,
        targets: list[dict[str, str]],
    ) -> Tracker:
        tracker = self.session.get(Tracker, tracker_id) if tracker_id else None
        if not tracker:
            tracker = Tracker(
                person_id=person_id,
                name=name,
                status=status,
                include_primary_sources=include_primary_sources,
                include_media_reports=include_media_reports,
                schedule_cron=schedule_cron,
            )
            self.session.add(tracker)
            self.session.flush()
        else:
            tracker.person_id = person_id
            tracker.name = name
            tracker.status = status
            tracker.include_primary_sources = include_primary_sources
            tracker.include_media_reports = include_media_reports
            tracker.schedule_cron = schedule_cron
            tracker.last_seen_at = datetime.utcnow()

        normalized_targets = {(item["target_type"], item["target_url"]): item for item in targets if item.get("target_url")}
        existing_targets = self.session.execute(select(TrackerTarget).where(TrackerTarget.tracker_id == tracker.id)).scalars().all()
        existing_map = {(item.target_type, item.target_url): item for item in existing_targets}

        for key, item in normalized_targets.items():
            target = existing_map.get(key)
            if not target:
                self.session.add(
                    TrackerTarget(
                        tracker_id=tracker.id,
                        target_name=item.get("target_name"),
                        target_type=item["target_type"],
                        target_url=item["target_url"],
                        parser_identity=item.get("parser_identity"),
                        is_active=True,
                    )
                )
                continue
            target.target_name = item.get("target_name") or target.target_name
            target.is_active = True
            target.parser_identity = item.get("parser_identity") or target.parser_identity

        for target in existing_targets:
            if (target.target_type, target.target_url) not in normalized_targets:
                target.is_active = False

        return tracker
