from __future__ import annotations

from collections import OrderedDict
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from tracker.utils.web import absolute_url


SOCIAL_DOMAINS = OrderedDict(
    [
        ("x", ["x.com", "twitter.com"]),
        ("facebook", ["facebook.com"]),
        ("instagram", ["instagram.com"]),
        ("truth_social", ["truthsocial.com"]),
        ("youtube", ["youtube.com", "youtu.be"]),
        ("linkedin", ["linkedin.com"]),
    ]
)

SOCIAL_BUTTON_LABELS = {
    "x": "X",
    "facebook": "FB",
    "instagram": "IG",
    "truth_social": "TS",
    "youtube": "YT",
    "linkedin": "in",
}

SOCIAL_DISPLAY_NAMES = {
    "x": "X / Twitter",
    "facebook": "Facebook",
    "instagram": "Instagram",
    "truth_social": "Truth Social",
    "youtube": "YouTube",
    "linkedin": "LinkedIn",
}


def discover_social_profiles(base_url: str, soup: BeautifulSoup) -> dict[str, str]:
    profiles: dict[str, str] = {}
    containers = []
    for selector in [".infobox", ".official-website", ".plainlist", ".navbar", ".vcard", ".sidebar"]:
        containers.extend(soup.select(selector))
    search_space = containers or [soup]

    for container in search_space:
        anchors = container.find_all("a", href=True)
        for anchor in anchors:
            href = anchor["href"].strip()
            if not href:
                continue
            absolute = absolute_url(base_url, href)
            platform = detect_social_platform(absolute)
            if not platform or platform in profiles:
                continue
            profiles[platform] = absolute
    return profiles


def detect_social_platform(url: str) -> str | None:
    hostname = (urlparse(url).netloc or "").lower()
    for platform, domains in SOCIAL_DOMAINS.items():
        if any(hostname == domain or hostname.endswith(f".{domain}") for domain in domains):
            return platform
    return None


def normalize_social_profiles(profiles: dict[str, str] | None) -> dict[str, str]:
    normalized: dict[str, str] = {}
    if not profiles:
        return normalized

    for key, value in profiles.items():
        if not value:
            continue
        detected = detect_social_platform(value)
        if detected is None:
            continue
        platform = detected or key
        if platform == "twitter":
            platform = "x"
        if platform not in SOCIAL_DOMAINS:
            continue
        normalized[platform] = value
    return normalized


def social_button_label(platform: str) -> str:
    return SOCIAL_BUTTON_LABELS.get(platform, platform[:2].upper())


def social_display_name(platform: str) -> str:
    return SOCIAL_DISPLAY_NAMES.get(platform, platform.replace("_", " ").title())
