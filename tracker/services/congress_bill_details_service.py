from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from tracker.config import get_settings
from tracker.models import Legislation
from tracker.services.legislation_service import LegislationService
from tracker.utils.congress_bills import canonical_congress_bill_page, congress_bill_tab_url, parse_bill_number_parts
from tracker.utils.names import normalize_person_name


@dataclass
class CongressBillEnrichmentResult:
    legislation_id: int
    bill_number: str
    official_url: str
    updated_fields: list[str] = field(default_factory=list)
    sponsors_added: int = 0
    cosponsors_added: int = 0
    errors: list[str] = field(default_factory=list)


class CongressBillDetailsService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.legislation_service = LegislationService(session)
        self.settings = get_settings()

    def enrich_legislation(self, legislation: Legislation) -> CongressBillEnrichmentResult:
        official_url = canonical_congress_bill_page(
            (legislation.raw_payload or {}).get("congress_gov_url") or legislation.source_url
        )
        result = CongressBillEnrichmentResult(
            legislation_id=legislation.id,
            bill_number=legislation.bill_number or legislation.title,
            official_url=official_url or "",
        )
        if not official_url:
            result.errors.append("Missing Congress.gov bill URL")
            return result

        try:
            api_details = self._fetch_api_details(legislation)
            if api_details:
                overview = api_details["overview"]
                text_details = api_details["text_details"]
                actions = api_details["actions"]
                cosponsors = api_details["cosponsors"]
            else:
                overview = self._parse_overview(self._fetch_soup(official_url), official_url)
                text_details = self._parse_text_page(self._fetch_soup(overview["text_page_url"]), overview["text_page_url"])
                actions = self._parse_actions_page(self._fetch_soup(overview["actions_page_url"]), overview["actions_page_url"])
                cosponsors = []
                if overview.get("cosponsor_count", 0) > 0 and overview.get("cosponsors_page_url"):
                    cosponsors = self._parse_cosponsors_page(
                        self._fetch_soup(overview["cosponsors_page_url"]),
                        overview["cosponsors_page_url"],
                    )
        except Exception as exc:
            result.errors.append(str(exc))
            return result

        payload = dict(legislation.raw_payload or {})
        payload.update(
            {
                "congress_gov_url": official_url,
                "source_priority": "official",
                "sponsor_name": overview.get("sponsor_name"),
                "sponsor_member_url": overview.get("sponsor_member_url"),
                "introduced_on_congress": overview.get("introduced_date").isoformat() if overview.get("introduced_date") else None,
                "committee_assignments": overview.get("committees", []),
                "policy_area": overview.get("policy_area"),
                "latest_action_text": overview.get("latest_action_text"),
                "latest_action_date": overview.get("latest_action_date").isoformat() if overview.get("latest_action_date") else None,
                "status_timeline": overview.get("status_steps", []),
                "summary_count": overview.get("summary_count"),
                "text_version_count": overview.get("text_version_count"),
                "actions_count": overview.get("actions_count"),
                "titles_count": overview.get("titles_count"),
                "amendments_count": overview.get("amendments_count"),
                "cosponsor_count": overview.get("cosponsor_count"),
                "committee_count": overview.get("committee_count"),
                "related_bill_count": overview.get("related_bill_count"),
                "all_information_url": official_url,
                "text_page_url": overview.get("text_page_url"),
                "actions_page_url": overview.get("actions_page_url"),
                "cosponsors_page_url": overview.get("cosponsors_page_url"),
                "committees_page_url": overview.get("committees_page_url"),
                "official_text_download_url": text_details.get("text_download_url"),
                "official_text_versions": text_details.get("text_versions", []),
                "official_text_label": text_details.get("shown_text_label"),
                "action_history": actions,
                "congress_gov_enriched_at": datetime.utcnow().isoformat(),
            }
        )
        legislation.raw_payload = payload

        if overview.get("status_text"):
            legislation.status_text = overview["status_text"]
            result.updated_fields.append("status_text")
        if overview.get("summary") and not legislation.summary:
            legislation.summary = overview["summary"]
            result.updated_fields.append("summary")
        if overview.get("latest_action_date"):
            legislation.last_action_date = overview["latest_action_date"]
            result.updated_fields.append("last_action_date")
        legislation.source_url = official_url
        legislation.source_type = "official"

        self.legislation_service.ensure_legislation_source(
            legislation.id,
            {
                "source_url": official_url,
                "source_type": "official",
                "source_title": f"Congress.gov | {legislation.bill_number or legislation.title}",
                "parser_identity": "congress_bill_details_enrichment_v1",
                "raw_payload": {"page_type": "overview"},
            },
        )
        for key, title in [
            ("text_page_url", "Text"),
            ("actions_page_url", "Actions"),
            ("cosponsors_page_url", "Cosponsors"),
            ("committees_page_url", "Committees"),
        ]:
            url = payload.get(key)
            if url:
                self.legislation_service.ensure_legislation_source(
                    legislation.id,
                    {
                        "source_url": url,
                        "source_type": "official",
                        "source_title": f"Congress.gov {title} | {legislation.bill_number or legislation.title}",
                        "parser_identity": "congress_bill_details_enrichment_v1",
                        "raw_payload": {"page_type": key},
                    },
                )

        sponsor_name = overview.get("sponsor_name")
        if sponsor_name:
            before = len(self.legislation_service.list_sponsors(legislation.id))
            self.legislation_service.ensure_legislation_sponsor(
                legislation.id,
                {
                    "full_name": sponsor_name,
                    "role": "sponsor",
                    "source_url": official_url,
                    "source_type": "official",
                },
                {
                    "level": legislation.level,
                    "jurisdiction_name": legislation.jurisdiction_name,
                    "source_url": official_url,
                    "source_type": "official",
                    "parser_identity": "congress_bill_details_enrichment_v1",
                },
            )
            after = len(self.legislation_service.list_sponsors(legislation.id))
            result.sponsors_added += max(0, after - before)

        for item in cosponsors:
            before = len(self.legislation_service.list_sponsors(legislation.id))
            self.legislation_service.ensure_legislation_sponsor(
                legislation.id,
                {
                    "full_name": item["full_name"],
                    "role": "cosponsor",
                    "source_url": overview["cosponsors_page_url"],
                    "source_type": "official",
                },
                {
                    "level": legislation.level,
                    "jurisdiction_name": legislation.jurisdiction_name,
                    "source_url": official_url,
                    "source_type": "official",
                    "parser_identity": "congress_bill_details_enrichment_v1",
                },
            )
            after = len(self.legislation_service.list_sponsors(legislation.id))
            result.cosponsors_added += max(0, after - before)

        result.updated_fields.extend(
            [
                "committee_assignments",
                "policy_area",
                "latest_action_text",
                "status_timeline",
                "text_page_url",
                "action_history",
            ]
        )
        return result

    def _fetch_api_details(self, legislation: Legislation) -> dict[str, Any] | None:
        if not self.settings.congress_api_key:
            return None
        raw_payload = legislation.raw_payload or {}
        congress = raw_payload.get("congress")
        bill_parts = parse_bill_number_parts(legislation.bill_number)
        if not congress or not bill_parts:
            return None
        bill_type, bill_number = bill_parts
        api_root = f"https://api.congress.gov/v3/bill/{int(congress)}/{bill_type.lower()}/{bill_number}"
        overview_data = self._fetch_api_json(api_root)
        bill = overview_data.get("bill") or {}
        if not bill:
            return None

        actions = self._fetch_api_results(bill.get("actions", {}).get("url"))
        cosponsors = self._fetch_api_results(bill.get("cosponsors", {}).get("url"))
        summaries = self._fetch_api_results(bill.get("summaries", {}).get("url"))
        text_versions = self._fetch_api_results(bill.get("textVersions", {}).get("url"))
        committees = self._fetch_api_results(bill.get("committees", {}).get("url"))

        sponsor_name = None
        sponsors_data = bill.get("sponsors") or []
        if sponsors_data:
            sponsor_name = self._join_name_parts(sponsors_data[0])

        official_url = canonical_congress_bill_page(raw_payload.get("congress_gov_url") or legislation.source_url)
        overview = {
            "title": bill.get("title"),
            "sponsor_name": sponsor_name,
            "sponsor_member_url": self._member_page_from_api_url((sponsors_data[0].get("url") if sponsors_data else None)),
            "introduced_date": self._parse_iso_date(bill.get("introducedDate")),
            "latest_action_date": self._parse_iso_date((bill.get("latestAction") or {}).get("actionDate")),
            "latest_action_text": (bill.get("latestAction") or {}).get("text"),
            "status_text": (bill.get("latestAction") or {}).get("text"),
            "committees": self._committee_labels_from_api(committees),
            "policy_area": ((bill.get("policyArea") or {}).get("name")),
            "summary": self._latest_summary_text(summaries),
            "status_steps": self._status_steps_from_api(actions),
            "summary_count": int((bill.get("summaries") or {}).get("count") or len(summaries)),
            "text_version_count": int((bill.get("textVersions") or {}).get("count") or len(text_versions)),
            "actions_count": int((bill.get("actions") or {}).get("count") or len(actions)),
            "titles_count": int((bill.get("titles") or {}).get("count") or 0),
            "amendments_count": int((bill.get("amendments") or {}).get("count") or 0),
            "cosponsor_count": int((bill.get("cosponsors") or {}).get("count") or len(cosponsors)),
            "committee_count": int((bill.get("committees") or {}).get("count") or len(committees)),
            "related_bill_count": int((bill.get("relatedBills") or {}).get("count") or 0),
            "text_page_url": congress_bill_tab_url(official_url, "text"),
            "actions_page_url": congress_bill_tab_url(official_url, "all-actions"),
            "cosponsors_page_url": congress_bill_tab_url(official_url, "cosponsors"),
            "committees_page_url": congress_bill_tab_url(official_url, "committees"),
        }
        text_details = {
            "text_versions": self._text_version_labels_from_api(text_versions),
            "text_version_count": len(text_versions),
            "shown_text_label": self._latest_text_label(text_versions),
            "text_download_url": self._latest_text_download_url(text_versions),
        }
        cosponsor_rows = []
        for item in cosponsors:
            name = self._join_name_parts(item)
            if name:
                cosponsor_rows.append(
                    {
                        "full_name": name,
                        "cosponsored_on": item.get("date"),
                        "source_url": overview["cosponsors_page_url"] or official_url,
                    }
                )

        return {
            "overview": overview,
            "text_details": text_details,
            "actions": self._actions_from_api(actions),
            "cosponsors": cosponsor_rows,
        }

    def _fetch_api_json(self, url: str) -> dict[str, Any]:
        response = httpx.get(
            url,
            params={"format": "json", "api_key": self.settings.congress_api_key},
            timeout=30.0,
            follow_redirects=True,
            trust_env=False,
            headers={"Accept": "application/json", "User-Agent": "US-Taiwan-Watch/1.0"},
        )
        response.raise_for_status()
        return response.json()

    def _fetch_api_results(self, url: str | None) -> list[dict[str, Any]]:
        if not url:
            return []
        data = self._fetch_api_json(url)
        for key in ("actions", "cosponsors", "summaries", "textVersions", "committees"):
            value = data.get(key)
            if isinstance(value, list):
                return value
        return []

    def _fetch_soup(self, url: str) -> BeautifulSoup:
        response = httpx.get(
            url,
            timeout=30.0,
            follow_redirects=True,
            trust_env=False,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        response.raise_for_status()
        return BeautifulSoup(response.text, "html.parser")

    def _parse_overview(self, soup: BeautifulSoup, url: str) -> dict[str, Any]:
        text = self._normalized_text(soup)
        title = self._extract_first_group(text, r"#?\s*(.+?)\s+\d{2,3}(?:st|nd|rd|th) Congress")
        sponsor_line = self._extract_first_group(text, r"Sponsor:\s*(.+?)\s+\(Introduced\s+(\d{2}/\d{2}/\d{4})\)", group=1)
        introduced_raw = self._extract_first_group(text, r"Sponsor:\s*(.+?)\s+\(Introduced\s+(\d{2}/\d{2}/\d{4})\)", group=2)
        sponsor_name = self._clean_member_label(sponsor_line)

        sponsor_anchor = None
        sponsor_pattern = sponsor_line.split("[", 1)[0].strip() if sponsor_line else ""
        if sponsor_pattern:
            for anchor in soup.find_all("a", href=True):
                anchor_text = " ".join(anchor.get_text(" ", strip=True).split())
                if anchor_text and sponsor_pattern in anchor_text and "/member/" in anchor["href"]:
                    sponsor_anchor = anchor
                    break

        latest_action_raw = self._extract_first_group(
            text,
            r"Latest Action:\s*(\d{2}/\d{2}/\d{4})\s+(.+?)\s+Tracker:",
            group=0,
        )
        latest_action_date = self._extract_first_group(text, r"Latest Action:\s*(\d{2}/\d{2}/\d{4})\s+(.+?)\s+Tracker:", group=1)
        latest_action_text = self._extract_first_group(text, r"Latest Action:\s*(\d{2}/\d{2}/\d{4})\s+(.+?)\s+Tracker:", group=2)
        status_text = self._extract_first_group(text, r"This bill has the status\s+(.+?)\s+Here are the steps for Status of Legislation:")
        committees_text = self._extract_first_group(text, r"Committees:\s*(.+?)\s+Committee Meetings:")
        policy_area = self._extract_first_group(text, r"Subject\s+[â€”-]\s+Policy Area:\s*(.+?)\s+View subjects")
        summary = self._extract_first_group(
            text,
            r"Shown Here:\s+.+?\s+(.+?)(?:\s+# Image: Congress\.gov|\s+Site Content)",
        )

        status_steps = []
        for match in re.finditer(
            r"Array\s+\(\s+\[actionDate\]\s+=>\s+([0-9-]+)\s+\[displayText\]\s+=>\s+(.+?)\s+\[externalActionCode\]\s+=>\s+(\d+)\s+\[description\]\s+=>\s+(.+?)\s+\[chamberOfAction\]\s+=>\s*(.*?)\s+\)",
            text,
        ):
            status_steps.append(
                {
                    "action_date": match.group(1),
                    "display_text": match.group(2).strip(),
                    "external_action_code": match.group(3),
                    "description": match.group(4).strip(),
                    "chamber": match.group(5).strip(),
                }
            )

        return {
            "title": title,
            "sponsor_name": sponsor_name,
            "sponsor_member_url": self._absolute_url(url, sponsor_anchor["href"]) if sponsor_anchor else None,
            "introduced_date": self._parse_mmddyyyy(introduced_raw),
            "latest_action_raw": latest_action_raw,
            "latest_action_date": self._parse_mmddyyyy(latest_action_date),
            "latest_action_text": latest_action_text.strip() if latest_action_text else None,
            "status_text": status_text.strip() if status_text else None,
            "committees": [item.strip() for item in (committees_text or "").split("|") if item.strip()],
            "policy_area": policy_area.strip() if policy_area else None,
            "summary": self._clean_summary(summary),
            "status_steps": status_steps,
            "summary_count": self._extract_count(text, "Summary"),
            "text_version_count": self._extract_count(text, "Text"),
            "actions_count": self._extract_count(text, "Actions"),
            "titles_count": self._extract_count(text, "Titles"),
            "amendments_count": self._extract_count(text, "Amendments"),
            "cosponsor_count": self._extract_count(text, "Cosponsors"),
            "committee_count": self._extract_count(text, "Committees"),
            "related_bill_count": self._extract_count(text, "Related Bills"),
            "text_page_url": congress_bill_tab_url(url, "text"),
            "actions_page_url": congress_bill_tab_url(url, "all-actions"),
            "cosponsors_page_url": congress_bill_tab_url(url, "cosponsors"),
            "committees_page_url": congress_bill_tab_url(url, "committees"),
        }

    def _parse_text_page(self, soup: BeautifulSoup, url: str) -> dict[str, Any]:
        text = self._normalized_text(soup)
        versions = self._extract_first_group(text, r"There are\s+(\d+)\s+versions:")
        shown_text_label = self._extract_first_group(
            text,
            r"Shown Here:\s+(.+?)\s+Share This",
        )
        text_anchor = None
        for anchor in soup.find_all("a", href=True):
            if "TXT" in anchor.get_text(" ", strip=True).upper():
                text_anchor = anchor
                break
        return {
            "text_versions": [item.strip() for item in re.split(r"\s{2,}", shown_text_label or "") if item.strip()],
            "text_version_count": int(versions) if versions and versions.isdigit() else None,
            "shown_text_label": shown_text_label.strip() if shown_text_label else None,
            "text_download_url": self._absolute_url(url, text_anchor["href"]) if text_anchor else None,
        }

    def _parse_actions_page(self, soup: BeautifulSoup, url: str) -> list[dict[str, str]]:
        text = self._normalized_text(soup)
        anchor = "Date Chamber All Actions"
        if anchor in text:
            action_block = text.split(anchor, 1)[1]
        else:
            action_block = text
        action_block = action_block.split("* * *", 1)[0]
        actions: list[dict[str, str]] = []
        for match in re.finditer(
            r"(\d{2}/\d{2}/\d{4}(?:-\d{1,2}:\d{2}(?:am|pm))?)\s+(House|Senate|Executive Branch)?\s*(.+?)(?=(?:\d{2}/\d{2}/\d{4}(?:-\d{1,2}:\d{2}(?:am|pm))?)|$)",
            action_block,
        ):
            actions.append(
                {
                    "action_date": match.group(1).strip(),
                    "chamber": (match.group(2) or "").strip(),
                    "text": " ".join(match.group(3).split()).strip(),
                }
            )
        return actions[:100]

    def _parse_cosponsors_page(self, soup: BeautifulSoup, url: str) -> list[dict[str, str]]:
        text = self._normalized_text(soup)
        if "* = Original cosponsor" in text:
            cosponsor_block = text.split("* = Original cosponsor", 1)[1]
        else:
            cosponsor_block = text
        results: list[dict[str, str]] = []
        seen: set[str] = set()
        for match in re.finditer(
            r"(Rep\.|Sen\.|Del\.|Resident Comm\.)\s+(.+?)\s+\[[^\]]+\]\*?\s+(\d{2}/\d{2}/\d{4})",
            cosponsor_block,
        ):
            name = self._clean_member_label(match.group(2))
            if not name or name in seen:
                continue
            seen.add(name)
            results.append({"full_name": name, "cosponsored_on": match.group(3), "source_url": url})
        return results

    def _normalized_text(self, soup: BeautifulSoup) -> str:
        return "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())

    def _extract_first_group(self, text: str, pattern: str, group: int = 1) -> str | None:
        match = re.search(pattern, text, flags=re.DOTALL)
        if not match:
            return None
        return match.group(group).strip()

    def _extract_count(self, text: str, label: str) -> int:
        match = re.search(rf"{re.escape(label)}\s+\((\d+)\)", text)
        return int(match.group(1)) if match else 0

    def _absolute_url(self, base_url: str, href: str) -> str:
        if href.startswith("http"):
            return href
        if href.startswith("/"):
            return f"https://www.congress.gov{href}"
        return f"{base_url.rstrip('/')}/{href.lstrip('/')}"

    def _parse_mmddyyyy(self, value: str | None) -> date | None:
        if not value:
            return None
        try:
            return datetime.strptime(value, "%m/%d/%Y").date()
        except ValueError:
            return None

    def _clean_member_label(self, value: str | None) -> str | None:
        if not value:
            return None
        cleaned = re.sub(r"^(Rep\.|Sen\.|Del\.|Resident Comm\.)\s+", "", value).strip()
        cleaned = re.sub(r"\[[^\]]+\]", "", cleaned).replace("*", "").strip()
        cleaned = normalize_person_name(cleaned)
        return cleaned or None

    def _clean_summary(self, value: str | None) -> str | None:
        if not value:
            return None
        cleaned = re.sub(r"\s+# Image: Congress\.gov.*$", "", value, flags=re.DOTALL).strip()
        cleaned = re.sub(r"\s{2,}", " ", cleaned)
        return cleaned or None

    def _parse_iso_date(self, value: str | None) -> date | None:
        if not value:
            return None
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").date()
        except ValueError:
            return None

    def _join_name_parts(self, payload: dict[str, Any]) -> str | None:
        if not isinstance(payload, dict):
            return None
        parts = [payload.get("firstName"), payload.get("middleName"), payload.get("lastName"), payload.get("suffixName")]
        joined = " ".join(str(part).strip() for part in parts if str(part or "").strip())
        if joined:
            return normalize_person_name(joined)
        direct = payload.get("fullName") or payload.get("name")
        if isinstance(direct, str) and direct.strip():
            cleaned = re.sub(r"^(Rep\.|Sen\.|Del\.|Resident Comm\.)\s+", "", direct).strip()
            cleaned = re.sub(r"\[[^\]]+\]", "", cleaned).strip()
            return normalize_person_name(cleaned)
        return None

    def _member_page_from_api_url(self, url: str | None) -> str | None:
        if not url:
            return None
        match = re.search(r"/member/([^/]+)/([^/?]+)", url)
        if not match:
            return None
        return f"https://www.congress.gov/member/{match.group(1)}/{match.group(2)}"

    def _committee_labels_from_api(self, committees: list[dict[str, Any]]) -> list[str]:
        labels = []
        for item in committees:
            name = item.get("name")
            chamber = item.get("chamber")
            text = " - ".join(part for part in [chamber, name] if part)
            if text:
                labels.append(text)
        return labels

    def _latest_summary_text(self, summaries: list[dict[str, Any]]) -> str | None:
        if not summaries:
            return None
        latest = summaries[0]
        for item in summaries:
            if (item.get("updateDate") or "") > (latest.get("updateDate") or ""):
                latest = item
        return latest.get("text")

    def _status_steps_from_api(self, actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "action_date": item.get("actionDate"),
                "display_text": item.get("text"),
                "description": item.get("type"),
                "chamber": item.get("actionCode"),
            }
            for item in actions[:50]
        ]

    def _actions_from_api(self, actions: list[dict[str, Any]]) -> list[dict[str, str]]:
        return [
            {
                "action_date": item.get("actionDate") or "",
                "chamber": item.get("actionCode") or "",
                "text": item.get("text") or "",
            }
            for item in actions[:100]
        ]

    def _text_version_labels_from_api(self, text_versions: list[dict[str, Any]]) -> list[str]:
        labels: list[str] = []
        for item in text_versions:
            type_text = item.get("type")
            date_text = item.get("date")
            label = " | ".join(part for part in [type_text, date_text] if part)
            if label:
                labels.append(label)
        return labels

    def _latest_text_label(self, text_versions: list[dict[str, Any]]) -> str | None:
        labels = self._text_version_labels_from_api(text_versions)
        return labels[0] if labels else None

    def _latest_text_download_url(self, text_versions: list[dict[str, Any]]) -> str | None:
        if not text_versions:
            return None
        latest = text_versions[0]
        formats = latest.get("formats") or []
        if isinstance(formats, list):
            for item in formats:
                if str((item or {}).get("type") or "").upper() == "FORMATTED TEXT":
                    return item.get("url")
            for item in formats:
                if str((item or {}).get("type") or "").upper() == "PDF":
                    return item.get("url")
            for item in formats:
                if (item or {}).get("url"):
                    return item.get("url")
        return None

