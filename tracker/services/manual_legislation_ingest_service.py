from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from tracker.models import Legislation, LegislationSource
from tracker.services.ai_assist_service import AIAssistService
from tracker.services.congress_bill_details_service import CongressBillDetailsService
from tracker.services.legislation_ai_enrichment_service import LegislationAIEnrichmentService
from tracker.services.legislation_service import LegislationService
from tracker.services.manual_url_import_service import ManualUrlImportService
from tracker.services.postgres_sequence_service import sync_postgres_id_sequences
from tracker.utils.congress_bills import canonical_congress_bill_page, congress_bill_url
from tracker.utils.web import parse_datetime


PARSER_IDENTITY = "manual_legislation_batch_v1"

_URL_TOKEN_RE = re.compile(r"(?:https?://|www\.)[^\s<>'\"]+", re.I)
_CONGRESS_BILL_PATH_RE = re.compile(
    r"/bill/(?P<congress>\d+)(?:st|nd|rd|th)-congress/(?P<slug>[^/]+)/(?P<number>\d+)",
    re.I,
)
_CONGRESS_TEXT_PATH_RE = re.compile(
    r"/congress/bills/(?P<congress>\d+)/(?P<code>[a-z]+)(?P<number>\d+)/",
    re.I,
)

_BILL_SLUG_INFO: dict[str, tuple[str, str, str]] = {
    "house-bill": ("hr", "house", "bill"),
    "senate-bill": ("s", "senate", "bill"),
    "house-resolution": ("hres", "house", "resolution"),
    "senate-resolution": ("sres", "senate", "resolution"),
    "house-concurrent-resolution": ("hconres", "house", "concurrent_resolution"),
    "senate-concurrent-resolution": ("sconres", "senate", "concurrent_resolution"),
    "house-joint-resolution": ("hjres", "house", "joint_resolution"),
    "senate-joint-resolution": ("sjres", "senate", "joint_resolution"),
}
_BILL_CODE_TO_SLUG = {code: slug for slug, (code, _, _) in _BILL_SLUG_INFO.items()}


@dataclass(frozen=True)
class ParsedCongressBillUrl:
    input_url: str
    official_url: str
    congress: int
    bill_type: str
    number: str
    chamber: str
    legislation_type: str

    @property
    def bill_number(self) -> str:
        return f"{self.bill_type.upper()} {self.number}".strip()

    @property
    def bill_slug(self) -> str:
        return f"us-{self.congress}-{self.bill_type}-{self.number}"


@dataclass
class ManualLegislationBatchResult:
    congress_urls: int = 0
    other_urls: int = 0
    created: int = 0
    updated: int = 0
    detail_ok: int = 0
    detail_failed: int = 0
    ai_detail_ok: int = 0
    gemini_detail_ok: int = 0
    gemini_detail_failed: int = 0
    sponsors_added: int = 0
    cosponsors_added: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    items: list[dict[str, object]] = field(default_factory=list)


class ManualLegislationIngestService:
    """Batch-import legislation URLs from the legislation page.

    Congress.gov bill URLs are converted into canonical legislation records and
    immediately enriched through CongressBillDetailsService. State legislature
    and other URLs delegate to the existing generic URL importer.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.legislation_service = LegislationService(session)
        self.details_service = CongressBillDetailsService(session)
        self.generic_url_import_service = ManualUrlImportService(session)
        self.gemini_enrichment_service = LegislationAIEnrichmentService(session)
        self.ai_assist_service = AIAssistService()

    def import_from_urls(self, raw_urls: str | Iterable[str]) -> ManualLegislationBatchResult:
        urls = self.parse_urls(raw_urls)
        result = ManualLegislationBatchResult()
        sync_postgres_id_sequences(
            self.session,
            (
                "legislation",
                "legislation_sources",
                "legislation_sponsors",
                "persons",
                "aliases",
            ),
        )
        other_urls: list[str] = []

        for url in urls:
            parsed = parse_congress_bill_url(url)
            if parsed is None:
                other_urls.append(url)
                continue
            result.congress_urls += 1
            self._import_congress_bill(parsed, result)

        if other_urls:
            result.other_urls += len(other_urls)
            generic_result = self.generic_url_import_service.import_legislation_from_urls("\n".join(other_urls))
            result.created += int(generic_result.created or 0)
            result.updated += int(generic_result.updated or 0)
            result.failed += int(generic_result.failed or 0)
            for item in generic_result.items or []:
                if item.get("ai_details_used"):
                    result.ai_detail_ok += 1
                result.items.append({"kind": "state_or_other", **item})
                if item.get("status") == "failed" and item.get("error"):
                    result.errors.append(str(item["error"]))

        self._run_gemini_background_enrichment(result)
        return result

    def _run_gemini_background_enrichment(self, result: ManualLegislationBatchResult) -> None:
        if not self.ai_assist_service.gemini_enabled:
            return
        for item in result.items:
            if str(item.get("status") or "") != "ok":
                continue
            legislation_id = item.get("legislation_id")
            if not legislation_id:
                continue
            transaction = self.session.begin_nested()
            try:
                enrichment = self.gemini_enrichment_service.enrich_with_gemini(int(legislation_id))
                if enrichment.ok:
                    transaction.commit()
                    result.gemini_detail_ok += int(enrichment.ok)
                    result.sponsors_added += int(enrichment.sponsors_linked or 0)
                    result.cosponsors_added += int(enrichment.cosponsors_linked or 0)
                    item["gemini_enriched"] = True
                    item["gemini_updated_fields"] = list(enrichment.updated_fields or [])
                    item["gemini_sources"] = list(enrichment.sources or [])
                    if enrichment.skipped_sponsors:
                        item["gemini_skipped_sponsors"] = list(enrichment.skipped_sponsors)
                else:
                    transaction.rollback()
                    result.gemini_detail_failed += 1
                    item["gemini_enriched"] = False
                    item["gemini_error"] = enrichment.message
                    if enrichment.message:
                        result.errors.append(f"Gemini legislation {legislation_id}: {enrichment.message}")
            except Exception as exc:
                transaction.rollback()
                result.gemini_detail_failed += 1
                item["gemini_enriched"] = False
                item["gemini_error"] = f"{type(exc).__name__}: {exc}"
                result.errors.append(f"Gemini legislation {legislation_id}: {type(exc).__name__}: {exc}")

    def parse_urls(self, raw_urls: str | Iterable[str]) -> list[str]:
        if isinstance(raw_urls, str):
            candidates = _URL_TOKEN_RE.findall(raw_urls)
            for token in re.split(r"[\n,\s]+", raw_urls):
                token = token.strip()
                if token and "." in token and "/" in token and token not in candidates:
                    candidates.append(token)
            if not candidates:
                candidates = [item for item in re.split(r"[\n,\s]+", raw_urls) if item.strip()]
        else:
            candidates = [str(item or "") for item in raw_urls]

        urls: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            normalized = _normalize_url(candidate)
            if not normalized:
                continue
            key = normalized.casefold()
            if key in seen:
                continue
            seen.add(key)
            urls.append(normalized)
        return urls

    def _import_congress_bill(self, parsed: ParsedCongressBillUrl, result: ManualLegislationBatchResult) -> None:
        last_exc: Exception | None = None
        for attempt in range(2):
            transaction = self.session.begin_nested()
            try:
                self._import_single_congress_bill(parsed, result)
                transaction.commit()
                return
            except Exception as exc:
                transaction.rollback()
                last_exc = exc
                if attempt == 0 and _looks_like_primary_key_collision(exc):
                    sync_postgres_id_sequences(
                        self.session,
                        (
                            "legislation",
                            "legislation_sources",
                            "legislation_sponsors",
                            "persons",
                            "aliases",
                        ),
                    )
                    continue
                break

        if last_exc is not None:
            result.failed += 1
            error = f"{parsed.input_url}: {type(last_exc).__name__}: {last_exc}"
            result.errors.append(error)
            result.items.append({"kind": "congress", "status": "failed", "url": parsed.input_url, "error": error})

    def _import_single_congress_bill(self, parsed: ParsedCongressBillUrl, result: ManualLegislationBatchResult) -> None:
        existing = self._find_existing_congress_bill(parsed)
        payload = self._congress_payload(parsed, existing)
        legislation, created = self.legislation_service.upsert_legislation(payload)
        self.session.flush()

        if created:
            result.created += 1
        else:
            result.updated += 1

        enrichment = self.details_service.enrich_legislation(legislation)
        detail_errors = list(enrichment.errors or [])
        ai_applied = False
        ai_sponsors_added = 0
        ai_cosponsors_added = 0
        if detail_errors:
            ai_applied, ai_sponsors_added, ai_cosponsors_added = self._apply_ai_fallback(legislation, parsed)
            if ai_applied:
                result.ai_detail_ok += 1
                result.sponsors_added += ai_sponsors_added
                result.cosponsors_added += ai_cosponsors_added
            else:
                result.detail_failed += 1
                result.errors.extend(f"{parsed.bill_number}: {error}" for error in detail_errors)
        else:
            result.detail_ok += 1
            result.sponsors_added += int(enrichment.sponsors_added or 0)
            result.cosponsors_added += int(enrichment.cosponsors_added or 0)

        result.items.append(
            {
                "kind": "congress",
                "status": "ok",
                "url": parsed.input_url,
                "official_url": parsed.official_url,
                "legislation_id": int(legislation.id),
                "bill_number": legislation.bill_number,
                "title": legislation.title,
                "created": bool(created),
                "detail_ok": not detail_errors,
                "ai_details_used": bool(ai_applied),
                "detail_errors": detail_errors,
                "sponsors_added": int(enrichment.sponsors_added or 0) + ai_sponsors_added,
                "cosponsors_added": int(enrichment.cosponsors_added or 0) + ai_cosponsors_added,
            }
        )

    def _congress_payload(self, parsed: ParsedCongressBillUrl, existing: Legislation | None) -> dict[str, object]:
        title = f"{parsed.bill_number} ({parsed.congress}th Congress)"
        return {
            "title": title,
            "bill_number": parsed.bill_number,
            "bill_slug": existing.bill_slug if existing else parsed.bill_slug,
            "legislation_type": parsed.bill_type.upper(),
            "level": "federal",
            "jurisdiction_name": "United States",
            "chamber": parsed.chamber,
            "summary": None,
            "status_text": None,
            "introduced_date": None,
            "last_action_date": None,
            "source_url": parsed.official_url,
            "source_type": "official",
            "parser_identity": PARSER_IDENTITY,
            "relevance_score": 1.0,
            "is_taiwan_related": True,
            "raw_payload": {
                "seeded_from": PARSER_IDENTITY,
                "manual_input_url": parsed.input_url,
                "congress_gov_url": parsed.official_url,
                "congress": parsed.congress,
                "bill_type": parsed.bill_type,
                "bill_number": parsed.number,
            },
            "sources": [
                {
                    "source_url": parsed.official_url,
                    "source_type": "official",
                    "source_title": f"Congress.gov | {parsed.bill_number}",
                    "parser_identity": PARSER_IDENTITY,
                    "raw_payload": {"manual_input_url": parsed.input_url, "congress": parsed.congress},
                }
            ],
            "sponsors": [],
        }

    def _apply_ai_fallback(self, legislation: Legislation, parsed: ParsedCongressBillUrl) -> tuple[bool, int, int]:
        try:
            page = self.generic_url_import_service._fetch_page(parsed.official_url)
        except Exception:
            return False, 0, 0
        title = str(page.get("title") or legislation.title or parsed.official_url)
        body = str(page.get("body") or "")
        ai_details = self.generic_url_import_service.ai_assist_service.extract_legislation_metadata(
            title=title,
            body=body,
            source_url=parsed.official_url,
        )
        if not ai_details:
            return False, 0, 0

        if ai_details.get("title"):
            legislation.title = str(ai_details["title"])
        if ai_details.get("summary") and not legislation.summary:
            legislation.summary = str(ai_details["summary"])
        if ai_details.get("status_text"):
            legislation.status_text = str(ai_details["status_text"])
        introduced_date = _date_from_ai(ai_details.get("introduced_date"))
        last_action_date = _date_from_ai(ai_details.get("last_action_date"))
        if introduced_date:
            legislation.introduced_date = introduced_date
        if last_action_date:
            legislation.last_action_date = last_action_date

        payload = dict(legislation.raw_payload or {})
        payload["ai_fallback_metadata"] = ai_details
        legislation.raw_payload = payload

        sponsors_added = 0
        cosponsors_added = 0
        for role, key in (("sponsor", "sponsor_names"), ("cosponsor", "cosponsor_names")):
            for full_name in _person_names(ai_details.get(key)):
                before = len(self.legislation_service.list_sponsors(legislation.id))
                self.legislation_service.ensure_legislation_sponsor(
                    legislation.id,
                    {
                        "full_name": full_name,
                        "role": role,
                        "source_url": parsed.official_url,
                        "source_type": "official",
                    },
                    {
                        "level": legislation.level,
                        "jurisdiction_name": legislation.jurisdiction_name,
                        "source_url": parsed.official_url,
                        "source_type": "official",
                        "parser_identity": PARSER_IDENTITY,
                    },
                )
                after = len(self.legislation_service.list_sponsors(legislation.id))
                added = max(0, after - before)
                if role == "sponsor":
                    sponsors_added += added
                else:
                    cosponsors_added += added
        return True, sponsors_added, cosponsors_added

    def _find_existing_congress_bill(self, parsed: ParsedCongressBillUrl) -> Legislation | None:
        direct = self.session.execute(
            select(Legislation).where(
                (Legislation.bill_slug == parsed.bill_slug) | (Legislation.source_url == parsed.official_url)
            )
        ).scalars().first()
        if direct:
            return direct

        source = self.session.execute(
            select(LegislationSource).where(LegislationSource.source_url == parsed.official_url)
        ).scalars().first()
        if source:
            return self.session.get(Legislation, source.legislation_id)
        return None


def parse_congress_bill_url(url: str) -> ParsedCongressBillUrl | None:
    normalized = _normalize_url(url)
    if not normalized:
        return None

    official_url = canonical_congress_bill_page(normalized)
    match = _CONGRESS_BILL_PATH_RE.search(official_url or normalized)
    if match:
        congress = int(match.group("congress"))
        slug = match.group("slug").lower()
        number = match.group("number")
        if slug not in _BILL_SLUG_INFO:
            return None
        bill_type, chamber, legislation_type = _BILL_SLUG_INFO[slug]
        official_url = official_url or congress_bill_url(congress, f"{bill_type.upper()} {number}") or normalized
        return ParsedCongressBillUrl(
            input_url=normalized,
            official_url=official_url,
            congress=congress,
            bill_type=bill_type,
            number=number,
            chamber=chamber,
            legislation_type=legislation_type,
        )

    text_match = _CONGRESS_TEXT_PATH_RE.search(normalized)
    if text_match:
        congress = int(text_match.group("congress"))
        code = text_match.group("code").lower()
        number = text_match.group("number")
        slug = _BILL_CODE_TO_SLUG.get(code)
        if not slug:
            return None
        bill_type, chamber, legislation_type = _BILL_SLUG_INFO[slug]
        official_url = congress_bill_url(congress, f"{bill_type.upper()} {number}")
        if not official_url:
            return None
        return ParsedCongressBillUrl(
            input_url=normalized,
            official_url=official_url,
            congress=congress,
            bill_type=bill_type,
            number=number,
            chamber=chamber,
            legislation_type=legislation_type,
        )

    return None



def _looks_like_primary_key_collision(exc: Exception) -> bool:
    message = str(exc).lower()
    return "duplicate key value" in message and "_pkey" in message

def _normalize_url(value: str) -> str:
    text = str(value or "").strip()
    text = text.strip("<>()[]{}\"'「」『』（）")
    text = text.rstrip(".,;。；，、")
    if not text:
        return ""
    if text.startswith("www."):
        text = f"https://{text}"
    parsed = urlparse(text)
    if not parsed.scheme:
        text = f"https://{text}"
        parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    if parsed.netloc.lower() == "congress.gov":
        parsed = parsed._replace(netloc="www.congress.gov", scheme="https")
        text = parsed.geturl()
    return text


def _date_from_ai(value: object):
    text = str(value or "").strip()
    if not text:
        return None
    parsed = parse_datetime(text)
    return parsed.date() if parsed else None


def _person_names(value: object) -> list[str]:
    if isinstance(value, str):
        raw_items = re.split(r"[,;\n]+", value)
    elif isinstance(value, list):
        raw_items = value
    else:
        return []
    results: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        name = str(item or "").strip()
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        results.append(name)
    return results
