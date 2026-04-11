from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from sqlalchemy.orm import Session

from tracker.models import Legislation, Person
from tracker.services.ai_assist_service import AIAssistService
from tracker.services.legislation_service import LegislationService
from tracker.services.manual_url_import_service import ManualUrlImportService
from tracker.utils.web import parse_datetime


@dataclass
class LegislationAIEnrichmentResult:
    ok: bool = False
    message: str = ""
    provider: str = ""
    placeholders_removed: int = 0
    updated_fields: list[str] = field(default_factory=list)
    sponsors_linked: int = 0
    cosponsors_linked: int = 0
    skipped_sponsors: list[dict[str, Any]] = field(default_factory=list)
    sources: list[dict[str, str]] = field(default_factory=list)


class LegislationAIEnrichmentService:
    """Refresh an existing legislation record with the best available AI provider."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.legislation_service = LegislationService(session)
        self.manual_url_import_service = ManualUrlImportService(session)
        self.ai_assist_service = AIAssistService()

    def refresh_with_ai(self, legislation_id: int) -> LegislationAIEnrichmentResult:
        legislation = self.session.get(Legislation, legislation_id)
        if not legislation:
            return LegislationAIEnrichmentResult(ok=False, message="找不到法案。")
        if not self.ai_assist_service.gemini_enabled and not self.ai_assist_service.enabled:
            return LegislationAIEnrichmentResult(ok=False, message="尚未設定可用的 AI API Key（Gemini/OpenAI）。")

        source_url = str(legislation.source_url or "").strip()
        page_title = ""
        page_body = ""
        if source_url:
            try:
                page = self.manual_url_import_service._fetch_page(source_url)
                page_title = str(page.get("title") or "")
                page_body = str(page.get("body") or "")
                source_url = str(page.get("final_url") or source_url)
            except Exception:
                page_title = str(legislation.title or "")
                page_body = str(legislation.summary or "")

        current_payload = {
            "title": legislation.title,
            "bill_number": legislation.bill_number,
            "level": legislation.level,
            "jurisdiction_name": legislation.jurisdiction_name,
            "chamber": legislation.chamber,
            "legislation_type": legislation.legislation_type,
            "summary": legislation.summary,
            "status_text": legislation.status_text,
            "introduced_date": legislation.introduced_date.isoformat() if legislation.introduced_date else None,
            "last_action_date": legislation.last_action_date.isoformat() if legislation.last_action_date else None,
            "source_url": source_url,
        }

        metadata: dict[str, Any] | None = None
        provider_used = ""
        provider_error = ""

        if self.ai_assist_service.gemini_enabled:
            try:
                metadata = self.ai_assist_service.research_legislation_metadata_with_gemini(
                    current=current_payload,
                    page_title=page_title,
                    page_body=page_body,
                    source_url=source_url,
                )
                if metadata:
                    provider_used = "gemini"
            except Exception as exc:
                provider_error = f"{type(exc).__name__}: {exc}"

        should_openai_topup = _needs_openai_topup(metadata, str(legislation.title or ""))
        if self.ai_assist_service.enabled and (not metadata or should_openai_topup):
            try:
                openai_metadata = self.ai_assist_service.research_legislation_metadata_with_openai(
                    current=current_payload,
                    page_title=page_title,
                    page_body=page_body,
                    source_url=source_url,
                )
                if openai_metadata:
                    if metadata:
                        metadata = _merge_metadata_for_quality(metadata, openai_metadata, str(legislation.title or ""))
                        if provider_used != "openai":
                            provider_used = "hybrid"
                    else:
                        metadata = openai_metadata
                        provider_used = "openai"
            except Exception as exc:
                if not provider_error:
                    provider_error = f"{type(exc).__name__}: {exc}"

        if not metadata:
            if provider_error:
                return LegislationAIEnrichmentResult(ok=False, message=f"AI 補資料失敗：{provider_error}")
            return LegislationAIEnrichmentResult(ok=False, message="AI 沒有回傳可用的法案資料。")

        guessed_sponsors, guessed_cosponsors = _extract_sponsor_names_from_page_body(page_body)
        if not list(metadata.get("sponsor_names") or []) and guessed_sponsors:
            metadata["sponsor_names"] = guessed_sponsors
        if not list(metadata.get("cosponsor_names") or []) and guessed_cosponsors:
            metadata["cosponsor_names"] = guessed_cosponsors

        if _summary_quality_low(str(metadata.get("summary") or ""), str(legislation.title or "")) and self.ai_assist_service.enabled:
            ai_summary = self.ai_assist_service.summarize_legislation(
                bill_number=str(legislation.bill_number or ""),
                title=str(legislation.title or ""),
                summary=page_body[:3500],
                latest_action=str(legislation.status_text or ""),
            )
            if ai_summary and not _summary_quality_low(ai_summary, str(legislation.title or "")):
                metadata["summary"] = ai_summary

        updated_fields: list[str] = []
        for attr, key in (
            ("title", "title"),
            ("bill_number", "bill_number"),
            ("legislation_type", "legislation_type"),
            ("level", "level"),
            ("jurisdiction_name", "jurisdiction_name"),
            ("chamber", "chamber"),
            ("summary", "summary"),
            ("status_text", "status_text"),
        ):
            value = metadata.get(key)
            if value and str(value).strip() and getattr(legislation, attr) != value:
                setattr(legislation, attr, value)
                updated_fields.append(attr)

        for attr, key in (("introduced_date", "introduced_date"), ("last_action_date", "last_action_date")):
            parsed = _date_from_ai(metadata.get(key))
            if parsed and getattr(legislation, attr) != parsed:
                setattr(legislation, attr, parsed)
                updated_fields.append(attr)

        if isinstance(metadata.get("is_taiwan_related"), bool):
            legislation.is_taiwan_related = bool(metadata["is_taiwan_related"])
        if isinstance(metadata.get("relevance_score"), (int, float)):
            legislation.relevance_score = float(metadata["relevance_score"])

        placeholders_removed = _remove_placeholder_sponsor_links(
            self.session,
            self.legislation_service,
            legislation.id,
        )

        raw_payload = dict(legislation.raw_payload or {})
        raw_payload["ai_enrichment"] = {
            "provider": provider_used or "unknown",
            "metadata": metadata,
            "sources": metadata.get("sources") or [],
            "source_url": source_url,
        }
        legislation.raw_payload = raw_payload

        sponsors_linked = 0
        cosponsors_linked = 0
        sponsor_payload = {
            "level": legislation.level,
            "jurisdiction_name": legislation.jurisdiction_name,
            "source_url": source_url or legislation.source_url,
            "source_type": legislation.source_type,
            "parser_identity": f"{provider_used or 'ai'}_legislation_enrichment_v1",
        }
        before_skipped = len(sponsor_payload.get("skipped_sponsors") or [])
        for role, key in (("sponsor", "sponsor_names"), ("cosponsor", "cosponsor_names")):
            for full_name in metadata.get(key) or []:
                before = len(self.legislation_service.list_sponsors(legislation.id))
                self.legislation_service.ensure_legislation_sponsor(
                    legislation.id,
                    {
                        "full_name": full_name,
                        "role": role,
                        "source_url": source_url or legislation.source_url,
                        "source_type": legislation.source_type,
                        "allow_seed_person": False,
                    },
                    sponsor_payload,
                )
                self.session.flush()
                after = len(self.legislation_service.list_sponsors(legislation.id))
                added = max(0, after - before)
                if role == "sponsor":
                    sponsors_linked += added
                else:
                    cosponsors_linked += added

        skipped_sponsors = list(sponsor_payload.get("skipped_sponsors") or [])[before_skipped:]
        if skipped_sponsors:
            raw_payload = dict(legislation.raw_payload or {})
            existing_skipped = list(raw_payload.get("skipped_sponsors") or [])
            raw_payload["skipped_sponsors"] = [*existing_skipped, *skipped_sponsors]
            legislation.raw_payload = raw_payload

        self.session.flush()
        return LegislationAIEnrichmentResult(
            ok=True,
            message=(
                "已重新整理法案資料（Gemini）。"
                if provider_used == "gemini"
                else "已重新整理法案資料（OpenAI）。"
                if provider_used == "openai"
                else "已重新整理法案資料（Gemini + OpenAI）。"
            ),
            provider=provider_used,
            placeholders_removed=placeholders_removed,
            updated_fields=updated_fields,
            sponsors_linked=sponsors_linked,
            cosponsors_linked=cosponsors_linked,
            skipped_sponsors=skipped_sponsors,
            sources=list(metadata.get("sources") or []),
        )

    def enrich_with_gemini(self, legislation_id: int) -> LegislationAIEnrichmentResult:
        """Backward-compatible alias."""
        return self.refresh_with_ai(legislation_id)


def _normalize_text_for_compare(value: str) -> str:
    text = str(value or "").lower()
    text = re.sub(r"[^a-z0-9一-鿿]+", "", text)
    return text


def _summary_quality_low(summary: str, title: str) -> bool:
    summary_text = str(summary or "").strip()
    if not summary_text:
        return True
    if len(summary_text) < 18:
        return True
    summary_norm = _normalize_text_for_compare(summary_text)
    title_norm = _normalize_text_for_compare(str(title or ""))
    if summary_norm and title_norm and (summary_norm == title_norm or summary_norm in title_norm or title_norm in summary_norm):
        return True
    return False


def _needs_openai_topup(metadata: dict[str, Any] | None, title: str) -> bool:
    if not isinstance(metadata, dict):
        return True
    sponsors = list(metadata.get("sponsor_names") or [])
    cosponsors = list(metadata.get("cosponsor_names") or [])
    if not sponsors and not cosponsors:
        return True
    if _summary_quality_low(str(metadata.get("summary") or ""), title):
        return True
    return False


def _merge_metadata_for_quality(primary: dict[str, Any], fallback: dict[str, Any], title: str) -> dict[str, Any]:
    merged = dict(primary or {})
    if not merged.get("sponsor_names") and fallback.get("sponsor_names"):
        merged["sponsor_names"] = list(fallback.get("sponsor_names") or [])
    if not merged.get("cosponsor_names") and fallback.get("cosponsor_names"):
        merged["cosponsor_names"] = list(fallback.get("cosponsor_names") or [])

    if _summary_quality_low(str(merged.get("summary") or ""), title):
        replacement = str(fallback.get("summary") or "").strip()
        if replacement:
            merged["summary"] = replacement

    for key in ("status_text", "introduced_date", "last_action_date", "chamber", "level", "legislation_type"):
        if not str(merged.get(key) or "").strip() and str(fallback.get(key) or "").strip():
            merged[key] = fallback.get(key)

    primary_sources = list(merged.get("sources") or [])
    fallback_sources = list(fallback.get("sources") or [])
    if not primary_sources and fallback_sources:
        merged["sources"] = fallback_sources
    return merged


def _clean_sponsor_token(value: str) -> str | None:
    token = str(value or "").strip()
    if not token:
        return None
    token = re.sub(r"\([^)]*\)", " ", token)
    token = re.sub(r"\[[^\]]*\]", " ", token)
    token = re.sub(r"^(?:senator|rep\.?|representative|assemblymember|delegate|member)\s+", "", token, flags=re.I)
    token = re.sub(r"\s+", " ", token).strip(" ,;:-")
    if len(token.split()) < 2:
        return None
    if any(ch.isdigit() for ch in token):
        return None
    return token[:160]


def _split_names_blob(blob: str) -> list[str]:
    parts = re.split(r"\s*(?:,|;|\band\b|\&|\/)\s*", str(blob or ""), flags=re.I)
    results: list[str] = []
    seen: set[str] = set()
    for part in parts:
        cleaned = _clean_sponsor_token(part)
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        results.append(cleaned)
    return results


def _extract_sponsor_names_from_page_body(page_body: str) -> tuple[list[str], list[str]]:
    text = str(page_body or "")
    if not text:
        return [], []

    sponsor_patterns = [
        r"(?:sponsor|primary sponsor|chief sponsor|introduced by|by)\s*[:：]\s*([^\n]{3,220})",
        r"(?:sponsors?)\s*[-–—]\s*([^\n]{3,220})",
    ]
    cosponsor_patterns = [
        r"(?:co-?sponsors?|coauthors?)\s*[:：]\s*([^\n]{3,300})",
    ]

    sponsors: list[str] = []
    cosponsors: list[str] = []

    for pattern in sponsor_patterns:
        for match in re.finditer(pattern, text, flags=re.I):
            sponsors.extend(_split_names_blob(match.group(1)))
    for pattern in cosponsor_patterns:
        for match in re.finditer(pattern, text, flags=re.I):
            cosponsors.extend(_split_names_blob(match.group(1)))

    dedupe = lambda values: list(dict.fromkeys([v for v in values if v]))
    sponsors = dedupe(sponsors)
    cosponsors = [name for name in dedupe(cosponsors) if name.casefold() not in {s.casefold() for s in sponsors}]
    return sponsors[:20], cosponsors[:100]


def _is_placeholder_legislator_name(name: str) -> bool:
    text = str(name or "").strip()
    if not text:
        return False
    lowered = text.casefold()
    if lowered.startswith(("senator ", "representative ", "rep ", "rep. ", "member ")):
        without_title = text.split(maxsplit=1)[1].strip() if len(text.split(maxsplit=1)) > 1 else ""
        return len(without_title.split()) < 2
    return False


def _remove_placeholder_sponsor_links(session: Session, service: LegislationService, legislation_id: int) -> int:
    removed = 0
    for sponsor in list(service.list_sponsors(legislation_id)):
        person = session.get(Person, int(getattr(sponsor, "person_id", 0) or 0))
        if not person:
            continue
        if _is_placeholder_legislator_name(str(person.full_name or "")):
            session.delete(sponsor)
            removed += 1
    if removed:
        session.flush()
    return removed


def _date_from_ai(value: object):
    text = str(value or "").strip()
    if not text:
        return None
    parsed = parse_datetime(text)
    return parsed.date() if parsed else None
