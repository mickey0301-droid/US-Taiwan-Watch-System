from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from tracker.models import Person
from tracker.services.social_target_service import SocialTargetService
from tracker.utils.social import normalize_social_profiles


class XCandidateConfirmationService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.social_target_service = SocialTargetService(session)

    def confirm_candidate(self, person_id: int, profile_url: str, source_reason: str = "x_candidate_confirmed") -> bool:
        person = self.session.get(Person, person_id)
        if not person or not profile_url:
            return False

        profiles = normalize_social_profiles(person.social_profiles)
        if not profiles.get("x"):
            profiles["x"] = profile_url
        person.social_profiles = profiles
        person.last_seen_at = datetime.utcnow()

        raw_payload = dict(person.raw_payload or {})
        x_links = dict(raw_payload.get("x_candidate_links") or {})
        confirmed_profiles = x_links.get("confirmed_profiles") or []
        if not isinstance(confirmed_profiles, list):
            confirmed_profiles = []
        if not any(isinstance(item, dict) and item.get("profile_url") == profile_url for item in confirmed_profiles):
            confirmed_profiles.append(
                {
                    "profile_url": profile_url,
                    "source_reason": source_reason,
                    "confirmed_at": datetime.utcnow().isoformat(),
                }
            )
        x_links["confirmed_profiles"] = confirmed_profiles
        x_links["confirmed_profile_url"] = confirmed_profiles[0]["profile_url"] if confirmed_profiles else profile_url
        x_links["confirmed_source"] = source_reason
        x_links["confirmed_at"] = datetime.utcnow().isoformat()
        raw_payload["x_candidate_links"] = x_links
        person.raw_payload = raw_payload

        self.social_target_service.ensure_valid_social_targets_for_person(
            person.id,
            {"x": profile_url},
            parser_identity=source_reason,
        )
        return True
