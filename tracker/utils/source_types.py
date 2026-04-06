from __future__ import annotations

from urllib.parse import urlparse


def source_domain(url: str | None) -> str:
    if not url:
        return ""
    return (urlparse(url).netloc or "").lower()


def is_government_url(url: str | None) -> bool:
    domain = source_domain(url)
    if not domain:
        return False
    if domain.endswith(".gov"):
        return True
    if domain.endswith(".mil"):
        return True
    if domain.endswith(".state.nj.us"):
        return True
    if domain.endswith(".leg.wa.gov"):
        return True
    if domain.endswith(".senate.ca.gov") or domain.endswith(".assembly.ca.gov"):
        return True
    if domain.endswith(".house.gov") or domain.endswith(".senate.gov") or domain.endswith(".congress.gov") or domain.endswith(".whitehouse.gov"):
        return True
    return False


def source_bucket(source_type: str | None, source_url: str | None) -> str:
    normalized = (source_type or "").strip().lower()
    domain = source_domain(source_url)
    if "wikipedia.org" in domain or normalized == "wikipedia":
        return "wikipedia"
    if normalized == "social":
        return "social"
    if normalized == "cspan":
        return "cspan"
    if normalized == "media":
        return "media"
    if normalized == "official" and is_government_url(source_url):
        return "official"
    if is_government_url(source_url):
        return "official"
    if normalized in {"secondary", "secondary_video"}:
        return "media"
    return normalized or "other"


def source_bucket_label(source_type: str | None, source_url: str | None, lang: str) -> str:
    bucket = source_bucket(source_type, source_url)
    labels_zh = {
        "official": "官方（政府）",
        "wikipedia": "維基百科",
        "media": "媒體",
        "social": "社群",
        "cspan": "C-SPAN",
        "other": "其他",
        "seed": "種子來源",
    }
    labels_en = {
        "official": "Official (government)",
        "wikipedia": "Wikipedia",
        "media": "Media",
        "social": "Social",
        "cspan": "C-SPAN",
        "other": "Other",
        "seed": "Seed source",
    }
    mapping = labels_zh if lang == "zh-TW" else labels_en
    return mapping.get(bucket, mapping["other"])


def source_priority_key(source_type: str | None, source_url: str | None) -> tuple[int, str]:
    bucket = source_bucket(source_type, source_url)
    priority = {
        "official": 0,
        "social": 1,
        "cspan": 2,
        "media": 3,
        "wikipedia": 4,
        "other": 5,
        "seed": 6,
    }
    return priority.get(bucket, 9), bucket
