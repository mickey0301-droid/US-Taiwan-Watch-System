from __future__ import annotations

from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from tracker.models import Tracker, TrackerTarget
from tracker.utils.social import SOCIAL_DISPLAY_NAMES, SOCIAL_DOMAINS


DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


class SocialTargetService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self._validation_cache: dict[str, bool] = {}

    def ensure_valid_social_targets_for_person(
        self,
        person_id: int,
        social_profiles: dict[str, str] | None,
        parser_identity: str,
    ) -> int:
        if not social_profiles:
            return 0
        tracker_ids = self.session.execute(select(Tracker.id).where(Tracker.person_id == person_id)).scalars().all()
        if not tracker_ids:
            return 0

        created = 0
        for tracker_id in tracker_ids:
            created += self.ensure_valid_social_targets_for_tracker(tracker_id, social_profiles, parser_identity)
        return created

    def ensure_valid_social_targets_for_tracker(
        self,
        tracker_id: int,
        social_profiles: dict[str, str] | None,
        parser_identity: str,
    ) -> int:
        if not social_profiles:
            return 0

        existing_targets = self.session.execute(select(TrackerTarget).where(TrackerTarget.tracker_id == tracker_id)).scalars().all()
        existing_urls = {target.target_url for target in existing_targets}
        created = 0

        for platform, url in social_profiles.items():
            if not url or url in existing_urls:
                continue
            if not self.is_valid_social_profile(platform, url):
                continue
            self.session.add(
                TrackerTarget(
                    tracker_id=tracker_id,
                    target_name=f"{SOCIAL_DISPLAY_NAMES.get(platform, platform)} profile",
                    target_type="social_page",
                    target_url=url,
                    parser_identity=parser_identity,
                    is_active=True,
                )
            )
            existing_urls.add(url)
            created += 1
        return created

    def is_valid_social_profile(self, platform: str, url: str) -> bool:
        cache_key = f"{platform}:{url}"
        if cache_key in self._validation_cache:
            return self._validation_cache[cache_key]

        parsed = urlparse(url)
        hostname = (parsed.netloc or "").lower()
        allowed_domains = SOCIAL_DOMAINS.get(platform, [])
        if not any(hostname == domain or hostname.endswith(f".{domain}") for domain in allowed_domains):
            self._validation_cache[cache_key] = False
            return False

        path = (parsed.path or "").lower()
        invalid_path_tokens = ["share", "intent", "sharer", "home", "search", "hashtag"]
        if any(token in path for token in invalid_path_tokens):
            self._validation_cache[cache_key] = False
            return False

        try:
            response = httpx.get(
                url,
                timeout=15.0,
                follow_redirects=True,
                trust_env=False,
                headers=DEFAULT_HEADERS,
            )
        except Exception:
            self._validation_cache[cache_key] = False
            return False

        final_url = str(response.url)
        final_parsed = urlparse(final_url)
        final_hostname = (final_parsed.netloc or "").lower()
        final_path = (final_parsed.path or "").lower()
        if response.status_code >= 400:
            self._validation_cache[cache_key] = False
            return False
        if not any(final_hostname == domain or final_hostname.endswith(f".{domain}") for domain in allowed_domains):
            self._validation_cache[cache_key] = False
            return False
        if any(token in final_path for token in ["login", "signup", "auth", "oauth", "recover"]):
            self._validation_cache[cache_key] = False
            return False

        body = response.text.lower()
        invalid_body_tokens = [
            "page isn't available",
            "content isn't available",
            "this account doesn't exist",
            "sorry, this page isn't available",
            "account suspended",
            "user not found",
        ]
        is_valid = not any(token in body for token in invalid_body_tokens)
        self._validation_cache[cache_key] = is_valid
        return is_valid
