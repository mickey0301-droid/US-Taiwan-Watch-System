from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from tracker.models import Legislation
from tracker.services.ai_assist_service import AIAssistService
from tracker.services.legislation_service import LegislationService
from tracker.services.manual_url_import_service import ManualUrlImportService
from tracker.utils.web import parse_datetime


@dataclass
class LegislationAIEnrichmentResult:
    ok: bool = False
    message: str = ""
    updated_fields: list[str] = field(default_factory=list)
    sponsors_linked: int = 0
    cosponsors_linked: int = 0
    skipped_sponsors: list[dict[str, Any]] = field(default_factory=list)
    sources: list[dict[str, str]] = field(default_factory=list)


class LegislationAIEnrichmentService:
    """Use Gemini grounding to enrich an existing legislation record."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.legislation_service = LegislationService(session)
        self.manual_url_import_service = ManualUrlImportService(session)
        self.ai_assist_service = AIAssistService()

    def enrich_with_gemini(self, legislation_id: int) -> LegislationAIEnrichmentResult:
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

        if not metadata and self.ai_assist_service.enabled:
            try:
                metadata = self.ai_assist_service.research_legislation_metadata_with_openai(
                    current=current_payload,
                    page_title=page_title,
                    page_body=page_body,
                    source_url=source_url,
                )
                if metadata:
                    provider_used = "openai"
            except Exception as exc:
                if not provider_error:
                    provider_error = f"{type(exc).__name__}: {exc}"

        if not metadata:
            if provider_error:
                return LegislationAIEnrichmentResult(ok=False, message=f"AI 補資料失敗：{provider_error}")
            return LegislationAIEnrichmentResult(ok=False, message="AI 沒有回傳可用的法案資料。")

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
                "Gemini 已完成查詢並更新法案資料。"
                if provider_used == "gemini"
                else "OpenAI 已完成查詢並更新法案資料。"
            ),
            updated_fields=updated_fields,
            sponsors_linked=sponsors_linked,
            cosponsors_linked=cosponsors_linked,
            skipped_sponsors=skipped_sponsors,
            sources=list(metadata.get("sources") or []),
        )


def _date_from_ai(value: object):
    text = str(value or "").strip()
    if not text:
        return None
    parsed = parse_datetime(text)
    return parsed.date() if parsed else None
