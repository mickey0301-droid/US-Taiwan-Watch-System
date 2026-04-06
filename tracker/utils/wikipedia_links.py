from __future__ import annotations

from urllib.parse import quote_plus


def build_wikipedia_search_url(full_name: str, office_name: str | None = None) -> str:
    pieces = [f'"{full_name}"']
    if office_name:
        pieces.append(f'"{office_name}"')
    query = " ".join(piece for piece in pieces if piece)
    return f"https://en.wikipedia.org/w/index.php?search={quote_plus(query)}"


def resolve_wikipedia_url(
    source_url: str | None,
    raw_payload: dict | None,
) -> str | None:
    payload = raw_payload or {}
    wikipedia_url = payload.get("wikipedia_url")
    if wikipedia_url:
        return wikipedia_url
    if source_url and "wikipedia.org" in source_url.lower():
        return source_url
    return None
