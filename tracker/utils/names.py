from __future__ import annotations

import re


def slugify_name(name: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", name.lower()).strip("-")
    return normalized or "unknown"


def normalize_person_name(name: str) -> str:
    normalized = " ".join((name or "").split()).strip(" ,")
    if not normalized:
        return normalized
    if "," in normalized:
        parts = [part.strip() for part in normalized.split(",") if part.strip()]
        if len(parts) >= 2:
            suffixes = {"jr.", "sr.", "ii", "iii", "iv", "v"}
            leading = parts[0]
            trailing = parts[1]
            remainder = ", ".join(parts[2:]).strip()
            trailing_bits = trailing.split()
            suffix = ""
            if trailing_bits and trailing_bits[-1].lower().rstrip(".") in {item.rstrip(".") for item in suffixes}:
                suffix = trailing_bits[-1]
                trailing = " ".join(trailing_bits[:-1]).strip()
            ordered = " ".join(bit for bit in [trailing, leading, suffix, remainder] if bit).strip()
            if ordered:
                return ordered
    return normalized


def split_person_name(name: str) -> tuple[str | None, str | None]:
    normalized = normalize_person_name(name)
    if not normalized:
        return None, None
    parts = normalized.split()
    if len(parts) < 2:
        return None, None
    return " ".join(parts[:-1]), parts[-1]


def display_person_name(full_name: str, given_name: str | None = None, family_name: str | None = None) -> str:
    if given_name and family_name:
        return " ".join(part for part in [given_name.strip(), family_name.strip()] if part).strip()
    return normalize_person_name(full_name)
