from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
import csv
from typing import Any
from urllib.parse import urljoin
import json

import httpx
from bs4 import BeautifulSoup, Tag

from tracker.collectors.base import BaseCollector, CollectorRunResult
from tracker.config import get_settings, get_source_registry
from tracker.db import session_scope
from tracker.logging_utils import get_logger
from tracker.models import SyncRun
from tracker.services.officials_service import InvalidPersonNameError, OfficialsService
from tracker.utils.text import compact_whitespace


logger = get_logger(__name__)


class StateLegislaturesCollector(BaseCollector):
    collector_name = "state_legislatures"
    source_name = "State legislature official directories"

    def __init__(self) -> None:
        self.settings = get_settings()

    def fetch(self) -> list[dict[str, Any]]:
        return get_source_registry().get("state_legislature_sources", [])

    def parse(self, payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for source in payload:
            parser_identity = source.get("parser_identity")
            if parser_identity == "azleg_member_roster_v1":
                try:
                    html = self._fetch_html(source["source_url"])
                    records.extend(self._parse_azleg_roster(source, html))
                except Exception as exc:
                    logger.exception("Arizona legislature parse failed for %s", source.get("source_url"))
                    records.append(
                        {
                            "_error": {
                                "state": source.get("state"),
                                "source_url": source.get("source_url"),
                                "message": str(exc),
                            }
                        }
                    )
            elif parser_identity == "ca_senate_roster_v1":
                try:
                    html = self._fetch_html(source["source_url"])
                    records.extend(self._parse_ca_senate_roster(source, html))
                except Exception as exc:
                    logger.exception("California senate parse failed for %s", source.get("source_url"))
                    records.append(
                        {
                            "_error": {
                                "state": source.get("state"),
                                "source_url": source.get("source_url"),
                                "message": str(exc),
                            }
                        }
                    )
            elif parser_identity == "ca_assembly_roster_v1":
                try:
                    html = self._fetch_html(source["source_url"])
                    records.extend(self._parse_ca_assembly_roster(source, html))
                except Exception as exc:
                    logger.exception("California assembly parse failed for %s", source.get("source_url"))
                    records.append(
                        {
                            "_error": {
                                "state": source.get("state"),
                                "source_url": source.get("source_url"),
                                "message": str(exc),
                            }
                        }
                    )
            elif parser_identity == "fl_senate_roster_v1":
                try:
                    html = self._fetch_html(source["source_url"])
                    records.extend(self._parse_fl_senate_roster(source, html))
                except Exception as exc:
                    logger.exception("Florida senate parse failed for %s", source.get("source_url"))
                    records.append(
                        {
                            "_error": {
                                "state": source.get("state"),
                                "source_url": source.get("source_url"),
                                "message": str(exc),
                            }
                        }
                    )
            elif parser_identity == "co_legislators_table_v1":
                try:
                    html = self._fetch_html(source["source_url"])
                    records.extend(self._parse_co_legislators_table(source, html))
                except Exception as exc:
                    logger.exception("Colorado legislators parse failed for %s", source.get("source_url"))
                    records.append(
                        {
                            "_error": {
                                "state": source.get("state"),
                                "source_url": source.get("source_url"),
                                "message": str(exc),
                            }
                        }
                    )
            elif parser_identity == "ia_legislators_table_v1":
                try:
                    html = self._fetch_html(source["source_url"])
                    records.extend(self._parse_ia_legislators_table(source, html))
                except Exception as exc:
                    logger.exception("Iowa legislators parse failed for %s", source.get("source_url"))
                    records.append(
                        {
                            "_error": {
                                "state": source.get("state"),
                                "source_url": source.get("source_url"),
                                "message": str(exc),
                            }
                        }
                    )
            elif parser_identity == "ut_legislators_json_v1":
                try:
                    payload_json = self._fetch_json(source["source_url"], encoding="cp1252")
                    records.extend(self._parse_ut_legislators_json(source, payload_json))
                except Exception as exc:
                    logger.exception("Utah legislators parse failed for %s", source.get("source_url"))
                    records.append(
                        {
                            "_error": {
                                "state": source.get("state"),
                                "source_url": source.get("source_url"),
                                "message": str(exc),
                            }
                        }
                    )
            elif parser_identity == "vt_legislators_json_v1":
                try:
                    payload_json = self._fetch_json(source["source_url"])
                    records.extend(self._parse_vt_legislators_json(source, payload_json))
                except Exception as exc:
                    logger.exception("Vermont legislators parse failed for %s", source.get("source_url"))
                    records.append(
                        {
                            "_error": {
                                "state": source.get("state"),
                                "source_url": source.get("source_url"),
                                "message": str(exc),
                            }
                        }
                    )
            elif parser_identity == "nh_house_dropdown_v1":
                try:
                    html = self._fetch_html(source["source_url"])
                    records.extend(self._parse_nh_house_dropdown(source, html))
                except Exception as exc:
                    logger.exception("New Hampshire house parse failed for %s", source.get("source_url"))
                    records.append(
                        {
                            "_error": {
                                "state": source.get("state"),
                                "source_url": source.get("source_url"),
                                "message": str(exc),
                            }
                        }
                    )
            elif parser_identity == "tx_senate_members_v1":
                try:
                    html = self._fetch_html(source["source_url"])
                    records.extend(self._parse_tx_senate_roster(source, html))
                except Exception as exc:
                    logger.exception("Texas senate parse failed for %s", source.get("source_url"))
                    records.append(
                        {
                            "_error": {
                                "state": source.get("state"),
                                "source_url": source.get("source_url"),
                                "message": str(exc),
                            }
                        }
                    )
            elif parser_identity == "tx_house_members_v1":
                try:
                    html = self._fetch_html(source["source_url"])
                    records.extend(self._parse_tx_house_roster(source, html))
                except Exception as exc:
                    logger.exception("Texas house parse failed for %s", source.get("source_url"))
                    records.append(
                        {
                            "_error": {
                                "state": source.get("state"),
                                "source_url": source.get("source_url"),
                                "message": str(exc),
                            }
                        }
                    )
            elif parser_identity == "ny_senate_members_v1":
                try:
                    html = self._fetch_html(source["source_url"])
                    records.extend(self._parse_ny_senate_roster(source, html))
                except Exception as exc:
                    logger.exception("New York senate parse failed for %s", source.get("source_url"))
                    records.append(
                        {
                            "_error": {
                                "state": source.get("state"),
                                "source_url": source.get("source_url"),
                                "message": str(exc),
                            }
                        }
                    )
            elif parser_identity == "ny_assembly_members_v1":
                try:
                    html = self._fetch_html(source["source_url"])
                    records.extend(self._parse_ny_assembly_roster(source, html))
                except Exception as exc:
                    logger.exception("New York assembly parse failed for %s", source.get("source_url"))
                    records.append(
                        {
                            "_error": {
                                "state": source.get("state"),
                                "source_url": source.get("source_url"),
                                "message": str(exc),
                            }
                        }
                    )
            elif parser_identity == "me_senate_members_v1":
                try:
                    html = self._fetch_html(source["source_url"])
                    records.extend(self._parse_me_senate_roster(source, html))
                except Exception as exc:
                    logger.exception("Maine senate parse failed for %s", source.get("source_url"))
                    records.append(
                        {
                            "_error": {
                                "state": source.get("state"),
                                "source_url": source.get("source_url"),
                                "message": str(exc),
                            }
                        }
                    )
            elif parser_identity == "me_house_members_v1":
                try:
                    html = self._fetch_html(source["source_url"])
                    records.extend(self._parse_me_house_roster(source, html))
                except Exception as exc:
                    logger.exception("Maine house parse failed for %s", source.get("source_url"))
                    records.append(
                        {
                            "_error": {
                                "state": source.get("state"),
                                "source_url": source.get("source_url"),
                                "message": str(exc),
                            }
                        }
                    )
            elif parser_identity == "md_members_index_v1":
                try:
                    html = self._fetch_html(source["source_url"])
                    records.extend(self._parse_md_members_index(source, html))
                except Exception as exc:
                    logger.exception("Maryland members parse failed for %s", source.get("source_url"))
                    records.append(
                        {
                            "_error": {
                                "state": source.get("state"),
                                "source_url": source.get("source_url"),
                                "message": str(exc),
                            }
                        }
                    )
            elif parser_identity == "de_legislators_json_v1":
                try:
                    payload_json = self._post_json(source["source_url"], {"page": 1, "pageSize": 500})
                    records.extend(self._parse_de_legislators_json(source, payload_json))
                except Exception as exc:
                    logger.exception("Delaware legislators parse failed for %s", source.get("source_url"))
                    records.append(
                        {
                            "_error": {
                                "state": source.get("state"),
                                "source_url": source.get("source_url"),
                                "message": str(exc),
                            }
                        }
                    )
            elif parser_identity == "va_house_members_v1":
                try:
                    html = self._fetch_html(source["source_url"])
                    records.extend(self._parse_va_house_members(source, html))
                except Exception as exc:
                    logger.exception("Virginia house parse failed for %s", source.get("source_url"))
                    records.append(
                        {
                            "_error": {
                                "state": source.get("state"),
                                "source_url": source.get("source_url"),
                                "message": str(exc),
                            }
                        }
                    )
            elif parser_identity == "va_senate_members_v1":
                try:
                    html = self._fetch_html(source["source_url"])
                    records.extend(self._parse_va_senate_members(source, html))
                except Exception as exc:
                    logger.exception("Virginia senate parse failed for %s", source.get("source_url"))
                    records.append(
                        {
                            "_error": {
                                "state": source.get("state"),
                                "source_url": source.get("source_url"),
                                "message": str(exc),
                            }
                        }
                    )
            elif parser_identity == "mn_house_members_v1":
                try:
                    html = self._fetch_html(source["source_url"])
                    records.extend(self._parse_mn_house_members(source, html))
                except Exception as exc:
                    logger.exception("Minnesota house parse failed for %s", source.get("source_url"))
                    records.append(
                        {
                            "_error": {
                                "state": source.get("state"),
                                "source_url": source.get("source_url"),
                                "message": str(exc),
                            }
                        }
                    )
            elif parser_identity == "mn_senate_members_api_v1":
                try:
                    payload_json = self._fetch_json(source["source_url"])
                    records.extend(self._parse_mn_senate_members_json(source, payload_json))
                except Exception as exc:
                    logger.exception("Minnesota senate parse failed for %s", source.get("source_url"))
                    records.append(
                        {
                            "_error": {
                                "state": source.get("state"),
                                "source_url": source.get("source_url"),
                                "message": str(exc),
                            }
                        }
                    )
            elif parser_identity == "sc_members_page_v1":
                try:
                    html = self._fetch_html(source["source_url"])
                    records.extend(self._parse_sc_members_page(source, html))
                except Exception as exc:
                    logger.exception("South Carolina members parse failed for %s", source.get("source_url"))
                    records.append(
                        {
                            "_error": {
                                "state": source.get("state"),
                                "source_url": source.get("source_url"),
                                "message": str(exc),
                            }
                        }
                    )
            elif parser_identity == "ok_senate_members_v1":
                try:
                    html = self._fetch_html(source["source_url"])
                    records.extend(self._parse_ok_senate_members(source, html))
                except Exception as exc:
                    logger.exception("Oklahoma senate parse failed for %s", source.get("source_url"))
                    records.append(
                        {
                            "_error": {
                                "state": source.get("state"),
                                "source_url": source.get("source_url"),
                                "message": str(exc),
                            }
                        }
                    )
            elif parser_identity == "ok_house_members_v1":
                try:
                    html = self._fetch_html(source["source_url"])
                    records.extend(self._parse_ok_house_members(source, html))
                except Exception as exc:
                    logger.exception("Oklahoma house parse failed for %s", source.get("source_url"))
                    records.append(
                        {
                            "_error": {
                                "state": source.get("state"),
                                "source_url": source.get("source_url"),
                                "message": str(exc),
                            }
                        }
                    )
            elif parser_identity == "al_legislators_graphql_v1":
                try:
                    payload_json = self._post_graphql_json(
                        "https://alison.legislature.state.al.us/graphql",
                        """
                        query {
                          legislativeMembers(where: { active: { eq: true } }, order: "lastName") {
                            data {
                              id
                              firstName
                              lastName
                              fullName
                              body
                              district
                              affiliation
                              active
                              leadershipTitle
                              honorific
                            }
                          }
                        }
                        """,
                    )
                    records.extend(self._parse_al_legislators_graphql(source, payload_json))
                except Exception as exc:
                    logger.exception("Alabama legislators parse failed for %s", source.get("source_url"))
                    records.append(
                        {
                            "_error": {
                                "state": source.get("state"),
                                "source_url": source.get("source_url"),
                                "message": str(exc),
                            }
                        }
                    )
            elif parser_identity == "wv_roster_table_v1":
                try:
                    html = self._fetch_html(source["source_url"])
                    records.extend(self._parse_wv_roster_table(source, html))
                except Exception as exc:
                    logger.exception("West Virginia roster parse failed for %s", source.get("source_url"))
                    records.append(
                        {
                            "_error": {
                                "state": source.get("state"),
                                "source_url": source.get("source_url"),
                                "message": str(exc),
                            }
                        }
                    )
            elif parser_identity == "ks_roster_table_v1":
                try:
                    html = self._fetch_html(source["source_url"])
                    records.extend(self._parse_ks_roster_table(source, html))
                except Exception as exc:
                    logger.exception("Kansas roster parse failed for %s", source.get("source_url"))
                    records.append(
                        {
                            "_error": {
                                "state": source.get("state"),
                                "source_url": source.get("source_url"),
                                "message": str(exc),
                            }
                        }
                    )
            elif parser_identity == "ar_senate_members_v1":
                try:
                    html = self._fetch_html_lenient(source["source_url"])
                    records.extend(self._parse_ar_senate_members(source, html))
                except Exception as exc:
                    logger.exception("Arkansas senate parse failed for %s", source.get("source_url"))
                    records.append(
                        {
                            "_error": {
                                "state": source.get("state"),
                                "source_url": source.get("source_url"),
                                "message": str(exc),
                            }
                        }
                    )
            elif parser_identity == "ar_house_members_v1":
                try:
                    html = self._fetch_html(source["source_url"])
                    records.extend(self._parse_ar_house_members(source, html))
                except Exception as exc:
                    logger.exception("Arkansas house parse failed for %s", source.get("source_url"))
                    records.append(
                        {
                            "_error": {
                                "state": source.get("state"),
                                "source_url": source.get("source_url"),
                                "message": str(exc),
                            }
                        }
                    )
            elif parser_identity == "sd_session_members_csv_v1":
                try:
                    csv_text = self._fetch_text(source["source_url"])
                    records.extend(self._parse_sd_session_members_csv(source, csv_text))
                except Exception as exc:
                    logger.exception("South Dakota session members parse failed for %s", source.get("source_url"))
                    records.append(
                        {
                            "_error": {
                                "state": source.get("state"),
                                "source_url": source.get("source_url"),
                                "message": str(exc),
                            }
                        }
                    )
            else:
                records.append(
                    {
                        "_error": {
                            "state": source.get("state"),
                            "source_url": source.get("source_url"),
                            "message": f"Unsupported parser_identity: {parser_identity}",
                        }
                    }
                )
        return records

    def sync(self) -> CollectorRunResult:
        result = CollectorRunResult(job_name=self.collector_name, source_name=self.source_name, started_at=datetime.utcnow())
        with session_scope() as session:
            sync_run = SyncRun(job_name=self.collector_name, job_type="collector", source_name=self.source_name)
            session.add(sync_run)
            session.flush()
            service: OfficialsService | None = None
            seen_keys_by_parser: dict[str, set[tuple[int, int, int | None, str]]] = {}
            try:
                parsed = self.parse(self.fetch())
                service = OfficialsService(session)
                usa = service.get_or_create_jurisdiction("United States", "country", code="US")
                for record in parsed:
                    if "_error" in record:
                        result.errors.append(str(record["_error"]))
                        continue
                    result.records_found += 1
                    state = service.get_or_create_jurisdiction(
                        record["jurisdiction"]["name"],
                        record["jurisdiction"]["type"],
                        code=record["jurisdiction"].get("code"),
                        parent_id=usa.id,
                    )
                    office = service.get_or_create_office(
                        record["office"]["office_name"],
                        record["office"]["level"],
                        record["office"].get("branch"),
                        record["office"].get("chamber"),
                        state.id,
                        record["office"]["source_url"],
                        record["office"]["source_type"],
                    )
                    try:
                        person, created = service.upsert_person(record["person"])
                    except InvalidPersonNameError as exc:
                        result.errors.append(str(exc))
                        continue
                    result.records_created += 1 if created else 0
                    result.records_updated += 0 if created else 1
                    for alias in record.get("aliases", []):
                        service.ensure_alias(person.id, alias, record["person"]["source_url"], record["person"]["source_type"])
                    if service.upsert_appointment(person, office, state.id, record["appointment"]):
                        result.records_created += 1
                    parser_identity = record["appointment"].get("parser_identity") or "state_legislatures"
                    seen_keys_by_parser.setdefault(parser_identity, set()).add(
                        (person.id, office.id, state.id, record["appointment"]["role_title"])
                    )

                for parser_identity, seen_keys in seen_keys_by_parser.items():
                    result.records_deactivated += service.reconcile_current_appointments(parser_identity, seen_keys)
                sync_run.status = "success"
            except Exception as exc:
                logger.exception("State legislatures collector failed.")
                result.errors.append(str(exc))
                sync_run.status = "failed"
                sync_run.error_message = str(exc)
            finally:
                result.ended_at = datetime.utcnow()
                sync_run.started_at = result.started_at
                sync_run.ended_at = result.ended_at
                sync_run.records_found = result.records_found
                sync_run.records_created = result.records_created
                sync_run.records_updated = result.records_updated
                sync_run.records_deactivated = result.records_deactivated
                validation_log = service.validation_log if service else []
                result.metadata["validation_log"] = validation_log
                result.metadata["validation_count"] = len(validation_log)
                sync_run.meta = {
                    "errors": result.errors,
                    "validation_log": validation_log,
                    "validation_count": len(validation_log),
                }
        return result

    def _fetch_html(self, url: str) -> str:
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
        if self.settings.snapshot_raw_responses:
            snapshot_dir = Path(self.settings.snapshots_dir)
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            slug = url.rstrip("/").split("/")[-1].replace(":", "_").replace("?", "_").replace("&", "_").replace("=", "_")
            (snapshot_dir / f"{slug}_{datetime.utcnow():%Y%m%d%H%M%S}.html").write_text(response.text, encoding="utf-8")
        return response.text

    def _fetch_html_lenient(self, url: str) -> str:
        response = httpx.get(
            url,
            timeout=30.0,
            follow_redirects=True,
            trust_env=False,
            verify=False,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        if response.status_code >= 500:
            response.raise_for_status()
        return response.text

    def _fetch_json(self, url: str, encoding: str = "utf-8") -> dict[str, Any] | list[Any]:
        response = httpx.get(
            url,
            timeout=30.0,
            follow_redirects=True,
            trust_env=False,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json,text/plain,*/*",
            },
        )
        response.raise_for_status()
        text = response.content.decode(encoding)
        return json.loads(text)

    def _fetch_text(self, url: str, encoding: str = "utf-8") -> str:
        response = httpx.get(
            url,
            timeout=30.0,
            follow_redirects=True,
            trust_env=False,
            verify=False,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "text/csv,text/plain,*/*",
            },
        )
        response.raise_for_status()
        return response.content.decode(encoding, errors="replace")

    def _post_json(self, url: str, form_data: dict[str, Any]) -> dict[str, Any] | list[Any]:
        response = httpx.post(
            url,
            data=form_data,
            timeout=30.0,
            follow_redirects=True,
            trust_env=False,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json,text/plain,*/*",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        response.raise_for_status()
        return response.json()

    def _post_graphql_json(self, url: str, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        response = httpx.post(
            url,
            json={"query": query, "variables": variables or {}},
            timeout=30.0,
            follow_redirects=True,
            trust_env=False,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json,text/plain,*/*",
                "Content-Type": "application/json",
            },
        )
        response.raise_for_status()
        return response.json()

    def _parse_azleg_roster(self, source: dict[str, Any], html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        roster_text = " ".join(soup.get_text("\n", strip=True).split())
        if "Senate Members" not in roster_text and "House Members" not in roster_text:
            return []

        chamber = source.get("chamber")
        role_title = "State Senator" if chamber == "senate" else "State Representative"
        office_name = "Arizona State Senate" if chamber == "senate" else "Arizona House of Representatives"
        subdepartment_name = "State Senate" if chamber == "senate" else "House of Representatives"

        records: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        for anchor in soup.find_all("a", href=True):
            full_name = self._extract_azleg_member_name(anchor)
            if not full_name or full_name in seen_names:
                continue
            parent_text = compact_whitespace(anchor.parent.get_text(" ", strip=True)) if isinstance(anchor.parent, Tag) else ""
            district = self._extract_azleg_district(parent_text)
            if district is None:
                district = self._extract_azleg_district(compact_whitespace(" ".join(anchor.stripped_strings)))
            member_url = urljoin(source["source_url"], anchor["href"])
            aliases = [
                f"Sen. {full_name}" if chamber == "senate" else f"Rep. {full_name}",
                f"Arizona {role_title.lower()} {full_name}",
            ]
            raw_payload = {
                "state": "Arizona",
                "district": district,
                "office_title": role_title,
                "department_name": "Arizona",
                "top_department_name": "Arizona",
                "subdepartment_name": subdepartment_name,
                "unit_name": district,
                "official_roster_url": source["source_url"],
            }
            records.append(
                {
                    "person": {
                        "full_name": full_name,
                        "source_url": member_url,
                        "source_type": "official",
                        "seed_source_type": "official",
                        "profile_status": "officially_enriched",
                        "canonical_official_url": member_url,
                        "parser_identity": source["parser_identity"],
                        "verification_status": "official_link",
                        "raw_payload": raw_payload,
                    },
                    "jurisdiction": {"name": "Arizona", "type": "state", "code": "Arizona"},
                    "office": {
                        "office_name": office_name,
                        "level": "state",
                        "branch": "legislative",
                        "chamber": chamber,
                        "source_url": source["source_url"],
                        "source_type": "official",
                    },
                    "appointment": {
                        "role_title": role_title,
                        "district": district,
                        "status": "current",
                        "source_url": member_url,
                        "source_type": "official",
                        "parser_identity": source["parser_identity"],
                        "is_current": True,
                        "raw_payload": raw_payload,
                    },
                    "aliases": aliases,
                }
            )
            seen_names.add(full_name)
        return records

    def _extract_azleg_member_name(self, anchor: Tag) -> str | None:
        href = anchor.get("href", "")
        title = compact_whitespace(anchor.get("title") or "")
        text = compact_whitespace(anchor.get_text(" ", strip=True))
        candidate = title or text
        if not href or "/memberroster/" in href.lower():
            return None
        if "legislator" not in href.lower() and "member" not in href.lower():
            return None
        if not candidate:
            return None
        lowered = candidate.lower()
        if any(token in lowered for token in ["email", "district", "legislature", "roster", "home"]):
            return None
        if re.search(r"\d", candidate):
            return None
        parts = candidate.replace(",", " ").split()
        if len(parts) < 2 or len(parts) > 5:
            return None
        return candidate

    def _extract_azleg_district(self, text: str) -> str | None:
        match = re.search(r"District\s+(\d+)", text, re.IGNORECASE)
        if match:
            return match.group(1)
        return None

    def _looks_like_person_name(self, value: str) -> bool:
        if not value:
            return False
        if re.search(r"\d", value):
            return False
        parts = value.replace(",", " ").split()
        if len(parts) < 2 or len(parts) > 6:
            return False
        return any(re.fullmatch(r"[A-Z][A-Za-z.\-']+", part) for part in parts)

    def _parse_ca_senate_roster(self, source: dict[str, Any], html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        records: list[dict[str, Any]] = []
        for member in soup.select("div.page-members__member"):
            name_tag = member.select_one("h3.member__name")
            district_tag = member.select_one("span.member__district")
            homepage = None
            for link in member.select("a.member__link[href]"):
                if "homepage" in compact_whitespace(link.get_text(" ", strip=True)).lower():
                    homepage = link.get("href")
                    break
            full_name = compact_whitespace(name_tag.get_text(" ", strip=True)) if name_tag else ""
            if not self._looks_like_person_name(full_name):
                continue
            district = self._extract_azleg_district(compact_whitespace(district_tag.get_text(" ", strip=True)) if district_tag else "")
            party = compact_whitespace(member.select_one("span.member__party").get_text(" ", strip=True)).strip("()") if member.select_one("span.member__party") else None
            profile_url = homepage or source["source_url"]
            records.append(self._build_state_legislator_record(source, full_name, profile_url, district, party))
        return records

    def _parse_ca_assembly_roster(self, source: dict[str, Any], html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        records: list[dict[str, Any]] = []
        for member in soup.select("li.members-list__member"):
            name_tag = member.select_one("h2.members-list__name")
            details = member.select_one("a.members-list__details[href]")
            district_tag = member.select_one("p.members-list__district")
            party_tag = member.select_one("p.members-list__party")
            full_name = compact_whitespace(name_tag.get_text(" ", strip=True)) if name_tag else ""
            if "," in full_name:
                family, given = [part.strip() for part in full_name.split(",", 1)]
                full_name = f"{given} {family}"
            if not self._looks_like_person_name(full_name):
                continue
            district = self._extract_azleg_district(compact_whitespace(district_tag.get_text(" ", strip=True)) if district_tag else "")
            party = compact_whitespace(party_tag.get_text(" ", strip=True)) if party_tag else None
            profile_url = urljoin(source["source_url"], details.get("href")) if details else source["source_url"]
            records.append(self._build_state_legislator_record(source, full_name, profile_url, district, party))
        return records

    def _parse_fl_senate_roster(self, source: dict[str, Any], html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        records: list[dict[str, Any]] = []
        rows = soup.find_all("tr")
        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) < 4:
                continue
            name_cell = compact_whitespace(cells[0].get_text(" ", strip=True))
            district = compact_whitespace(cells[1].get_text(" ", strip=True)) or None
            party = compact_whitespace(cells[2].get_text(" ", strip=True)) or None
            full_name = self._clean_florida_senate_name(name_cell)
            if not self._looks_like_person_name(full_name):
                continue
            link = row.find("a", href=True)
            profile_url = urljoin(source["source_url"], link["href"]) if link else source["source_url"]
            records.append(self._build_state_legislator_record(source, full_name, profile_url, district, party))
        return records

    def _parse_co_legislators_table(self, source: dict[str, Any], html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        table = soup.select_one("table.leg-data")
        if table is None:
            return []
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        requested_chamber = source.get("chamber")
        for row in table.select("tbody tr"):
            title_text = compact_whitespace(row.select_one('[data-label="Title"]').get_text(" ", strip=True)) if row.select_one('[data-label="Title"]') else ""
            chamber = "senate" if "senator" in title_text.lower() else "house" if "representative" in title_text.lower() else None
            if requested_chamber and chamber != requested_chamber:
                continue
            name_anchor = row.select_one('[data-label="Name"] a[href]')
            if name_anchor is None:
                continue
            full_name = compact_whitespace(name_anchor.get_text(" ", strip=True))
            if "," in full_name:
                family, given = [part.strip() for part in full_name.split(",", 1)]
                full_name = f"{given} {family}"
            if not self._looks_like_person_name(full_name) or full_name in seen:
                continue
            district = compact_whitespace(row.select_one('[data-label="District"]').get_text(" ", strip=True)) if row.select_one('[data-label="District"]') else None
            party = compact_whitespace(row.select_one('[data-label="Party"]').get_text(" ", strip=True)) if row.select_one('[data-label="Party"]') else None
            profile_url = urljoin(source["source_url"], name_anchor.get("href", ""))
            records.append(self._build_state_legislator_record(source, full_name, profile_url, district, party))
            seen.add(full_name)
        return records

    def _parse_ia_legislators_table(self, source: dict[str, Any], html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        table = soup.select_one("table#sortableTable")
        if table is None:
            return []
        requested_chamber = source.get("chamber")
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in table.select("tbody tr"):
            cells = row.find_all("td")
            if len(cells) < 6:
                continue
            chamber_text = compact_whitespace(cells[0].get_text(" ", strip=True)).lower()
            chamber = "senate" if chamber_text == "senate" else "house" if chamber_text == "house" else None
            if requested_chamber and chamber != requested_chamber:
                continue
            name_anchor = cells[1].find("a", href=True)
            if name_anchor is None:
                continue
            full_name = compact_whitespace(name_anchor.get_text(" ", strip=True))
            full_name = re.sub(r"\s*\([^)]*\)$", "", full_name).strip()
            if not self._looks_like_person_name(full_name) or full_name in seen:
                continue
            district = compact_whitespace(cells[2].get_text(" ", strip=True)) or None
            party = compact_whitespace(cells[3].get_text(" ", strip=True)) or None
            profile_url = urljoin(source["source_url"], name_anchor.get("href", ""))
            records.append(self._build_state_legislator_record(source, full_name, profile_url, district, party))
            seen.add(full_name)
        return records

    def _parse_ut_legislators_json(self, source: dict[str, Any], payload_json: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
        legislators = payload_json.get("legislators", []) if isinstance(payload_json, dict) else payload_json
        requested_chamber = source.get("chamber")
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in legislators:
            if not isinstance(item, dict):
                continue
            chamber_code = compact_whitespace(str(item.get("house", ""))).upper()
            chamber = "senate" if chamber_code == "S" else "house" if chamber_code == "H" else None
            if requested_chamber and chamber != requested_chamber:
                continue
            full_name = compact_whitespace(str(item.get("formatName") or item.get("fullName") or ""))
            if not full_name:
                raw_name = compact_whitespace(str(item.get("fullName", "")))
                if "," in raw_name:
                    family, given = [part.strip() for part in raw_name.split(",", 1)]
                    full_name = f"{given} {family}"
            if not self._looks_like_person_name(full_name) or full_name in seen:
                continue
            district = compact_whitespace(str(item.get("district") or "")) or None
            party = "Republican" if str(item.get("party", "")).upper() == "R" else "Democratic" if str(item.get("party", "")).upper() == "D" else compact_whitespace(str(item.get("party") or "")) or None
            role_path = f"/rep/{item.get('id')}" if chamber == "house" else f"/sen/{item.get('id')}"
            profile_base = "https://house.utleg.gov" if chamber == "house" else "https://senate.utah.gov"
            profile_url = urljoin(profile_base, role_path)
            records.append(self._build_state_legislator_record(source, full_name, profile_url, district, party))
            seen.add(full_name)
        return records

    def _parse_vt_legislators_json(self, source: dict[str, Any], payload_json: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
        legislators = payload_json.get("data", []) if isinstance(payload_json, dict) else payload_json
        requested_chamber = source.get("chamber")
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in legislators:
            if not isinstance(item, dict):
                continue
            title = compact_whitespace(str(item.get("Title") or ""))
            chamber = "senate" if title.lower() == "senator" else "house" if title.lower() == "representative" else None
            if requested_chamber and chamber != requested_chamber:
                continue
            first_name = compact_whitespace(str(item.get("FirstName") or ""))
            middle = compact_whitespace(str(item.get("MI") or ""))
            last_name = compact_whitespace(str(item.get("LastName") or ""))
            suffix = compact_whitespace(str(item.get("NameSuffix") or ""))
            full_name = compact_whitespace(" ".join(part for part in [first_name, middle, last_name, suffix] if part))
            if not self._looks_like_person_name(full_name) or full_name in seen:
                continue
            district = compact_whitespace(str(item.get("District") or "")) or None
            party = compact_whitespace(str(item.get("Party") or "")) or None
            person_id = compact_whitespace(str(item.get("PersonID") or ""))
            profile_url = urljoin(source["source_url"], f"/people/single/2026/{person_id}") if person_id else source["source_url"]
            records.append(self._build_state_legislator_record(source, full_name, profile_url, district, party))
            seen.add(full_name)
        return records

    def _parse_nh_house_dropdown(self, source: dict[str, Any], html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        select = soup.find("select", {"name": "ctl00$pageBody$ddlReps"})
        if select is None:
            return []
        records: list[dict[str, Any]] = []
        for option in select.find_all("option"):
            value = compact_whitespace(option.get("value", ""))
            if not value:
                continue
            raw_name = compact_whitespace(option.get_text(" ", strip=True))
            if "," in raw_name:
                family, given = [part.strip() for part in raw_name.split(",", 1)]
                full_name = f"{given} {family}"
            else:
                full_name = raw_name
            if not self._looks_like_person_name(full_name):
                continue
            profile_url = urljoin(source["source_url"], f"/house/members/member.aspx?pid={value}")
            district, party = self._parse_nh_house_member_profile(profile_url)
            records.append(self._build_state_legislator_record(source, full_name, profile_url, district, party))
        return records

    def _parse_nh_house_member_profile(self, profile_url: str) -> tuple[str | None, str | None]:
        try:
            html = self._fetch_html(profile_url)
        except Exception:
            return None, None
        soup = BeautifulSoup(html, "html.parser")
        heading = compact_whitespace(soup.find("h1").get_text(" ", strip=True)) if soup.find("h1") else ""
        subheading = compact_whitespace(soup.find("h3").get_text(" ", strip=True)) if soup.find("h3") else ""
        party = None
        party_match = re.search(r"\(([RDI])\)", heading)
        if party_match:
            party = {"R": "Republican", "D": "Democratic", "I": "Independent"}.get(party_match.group(1))
        district = None
        district_match = re.search(r"District\s+(.+)$", subheading, re.IGNORECASE)
        if district_match:
            district = compact_whitespace(district_match.group(1))
        return district, party

    def _parse_tx_senate_roster(self, source: dict[str, Any], html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for anchor in soup.select("a[href]"):
            href = anchor.get("href", "")
            if "member.php" not in href.lower():
                continue
            full_name = compact_whitespace(anchor.get_text(" ", strip=True))
            if not self._looks_like_person_name(full_name) or full_name in seen:
                continue
            context = compact_whitespace(anchor.parent.get_text(" ", strip=True)) if isinstance(anchor.parent, Tag) else ""
            district = self._extract_numeric_marker(context, r"District\s+(\d+)")
            party = self._extract_party(context)
            profile_url = urljoin(source["source_url"], href)
            records.append(self._build_state_legislator_record(source, full_name, profile_url, district, party))
            seen.add(full_name)
        return records

    def _parse_tx_house_roster(self, source: dict[str, Any], html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for anchor in soup.select("a[href]"):
            href = anchor.get("href", "")
            if "/members/member-page/" not in href.lower():
                continue
            full_name = compact_whitespace(anchor.get_text(" ", strip=True))
            if not self._looks_like_person_name(full_name) or full_name in seen:
                continue
            context = compact_whitespace(anchor.parent.get_text(" ", strip=True)) if isinstance(anchor.parent, Tag) else ""
            district = self._extract_numeric_marker(context, r"District\s+(\d+)")
            party = self._extract_party(context)
            profile_url = urljoin(source["source_url"], href)
            records.append(self._build_state_legislator_record(source, full_name, profile_url, district, party))
            seen.add(full_name)
        return records

    def _parse_ny_senate_roster(self, source: dict[str, Any], html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for anchor in soup.select("a[href]"):
            href = anchor.get("href", "")
            if "/senators/" not in href.lower():
                continue
            full_name = compact_whitespace(anchor.get_text(" ", strip=True))
            if not self._looks_like_person_name(full_name) or full_name in seen:
                continue
            context = compact_whitespace(anchor.parent.get_text(" ", strip=True)) if isinstance(anchor.parent, Tag) else ""
            district = self._extract_numeric_marker(context, r"District\s+(\d+)")
            party = self._extract_party(context)
            profile_url = urljoin(source["source_url"], href)
            records.append(self._build_state_legislator_record(source, full_name, profile_url, district, party))
            seen.add(full_name)
        return records

    def _parse_ny_assembly_roster(self, source: dict[str, Any], html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for anchor in soup.select("a[href]"):
            href = anchor.get("href", "")
            if "/mem/" not in href.lower():
                continue
            full_name = compact_whitespace(anchor.get_text(" ", strip=True))
            if not self._looks_like_person_name(full_name) or full_name in seen:
                continue
            context = compact_whitespace(anchor.parent.get_text(" ", strip=True)) if isinstance(anchor.parent, Tag) else ""
            district = self._extract_numeric_marker(context, r"District\s+(\d+)")
            party = self._extract_party(context)
            profile_url = urljoin(source["source_url"], href)
            records.append(self._build_state_legislator_record(source, full_name, profile_url, district, party))
            seen.add(full_name)
        return records

    def _parse_me_senate_roster(self, source: dict[str, Any], html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for anchor in soup.select("a[href]"):
            href = anchor.get("href", "")
            if not href.lower().startswith("/district"):
                continue
            full_name = compact_whitespace(anchor.get_text(" ", strip=True))
            if not full_name:
                continue
            if not self._looks_like_person_name(full_name) or full_name in seen:
                continue
            context = compact_whitespace(anchor.parent.get_text(" ", strip=True)) if isinstance(anchor.parent, Tag) else full_name
            _, party, district = self._parse_me_senate_line(context)
            profile_url = urljoin(source["source_url"], href)
            records.append(self._build_state_legislator_record(source, full_name, profile_url, district, party))
            seen.add(full_name)
        return records

    def _parse_me_house_roster(self, source: dict[str, Any], html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for anchor in soup.select("a[href]"):
            href = anchor.get("href", "")
            if "memberprofiles" not in href.lower() and "profileid=" not in href.lower():
                continue
            text = compact_whitespace(anchor.get_text(" ", strip=True))
            if not text:
                continue
            context = compact_whitespace(anchor.get_text(" ", strip=True))
            full_name, party, district = self._parse_me_house_line(context)
            if not full_name or not self._looks_like_person_name(full_name) or full_name in seen:
                continue
            profile_url = urljoin(source["source_url"], href)
            records.append(self._build_state_legislator_record(source, full_name, profile_url, district, party))
            seen.add(full_name)
        return records

    def _parse_me_senate_line(self, text: str) -> tuple[str | None, str | None, str | None]:
        cleaned = compact_whitespace(text)
        match = re.search(r"^(.*?)\s*\(([DRI])\s*-\s*([^)]+)\)$", cleaned)
        if not match:
            return None, None, None
        full_name = compact_whitespace(match.group(1).replace("Senator", "").strip(" -,"))
        party = {"D": "Democratic", "R": "Republican", "I": "Independent"}.get(match.group(2).upper())
        district = compact_whitespace(match.group(3))
        return full_name, party, district

    def _parse_me_house_line(self, text: str) -> tuple[str | None, str | None, str | None]:
        cleaned = compact_whitespace(re.sub(r"\s*Profile\s+\d+\s*$", "", text))
        match = re.search(r"^Dist\s+(\d+)\s+(.*?)\s+State Representative\s+\(([DRIU])\s*-\s*([^)]+)\)$", cleaned)
        if not match:
            return None, None, None
        district = match.group(1)
        full_name = compact_whitespace(match.group(2))
        party = {
            "D": "Democratic",
            "R": "Republican",
            "I": "Independent",
            "U": "Unenrolled",
        }.get(match.group(3).upper())
        return full_name, party, district

    def _parse_md_members_index(self, source: dict[str, Any], html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for card in soup.select("div.member-index-cell"):
            dd = card.find("dd")
            detail_links = dd.select("a[href*='/mgawebsite/Members/Details/']") if isinstance(dd, Tag) else []
            if not detail_links:
                detail_links = card.select("a[href*='/mgawebsite/Members/Details/']")
            if not detail_links:
                continue
            details_link = next(
                (link for link in detail_links if compact_whitespace(link.get_text(" ", strip=True))),
                detail_links[0],
            )
            detail_href = details_link.get("href", "")
            context = compact_whitespace(dd.get_text(" ", strip=True)) if isinstance(dd, Tag) else compact_whitespace(card.get_text(" ", strip=True))
            full_name = compact_whitespace(details_link.get_text(" ", strip=True))
            if not full_name:
                image = card.find("img", alt=True)
                full_name = compact_whitespace(str(image.get("alt", ""))) if image else ""
            if "," in full_name:
                family, given = [part.strip() for part in full_name.split(",", 1)]
                full_name = f"{given} {family}"
            if not self._looks_like_person_name(full_name) or full_name in seen:
                continue
            district = self._extract_numeric_marker(context, r"District\s+(\d+)")
            party = self._extract_party(context)
            profile_url = urljoin(source["source_url"], detail_href)
            records.append(self._build_state_legislator_record(source, full_name, profile_url, district, party))
            seen.add(full_name)
        return records

    def _parse_de_legislators_json(self, source: dict[str, Any], payload_json: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
        legislators = payload_json.get("Data", []) if isinstance(payload_json, dict) else payload_json
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in legislators:
            if not isinstance(item, dict):
                continue
            full_name = compact_whitespace(str(item.get("PersonFullName") or ""))
            if not self._looks_like_person_name(full_name) or full_name in seen:
                continue
            district = compact_whitespace(str(item.get("DistrictNumber") or "")) or None
            party_code = compact_whitespace(str(item.get("PartyCode") or "")).upper()
            party = {"D": "Democratic", "R": "Republican", "I": "Independent"}.get(party_code) or None
            profile_url = compact_whitespace(str(item.get("LegislatorDetailLink") or "")) or source["source_url"]
            records.append(self._build_state_legislator_record(source, full_name, profile_url, district, party))
            seen.add(full_name)
        return records

    def _parse_va_house_members(self, source: dict[str, Any], html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for anchor in soup.select("a[href^='/members/H']"):
            full_name = compact_whitespace(anchor.get_text(" ", strip=True))
            if not self._looks_like_person_name(full_name) or full_name in seen:
                continue
            card_text = compact_whitespace(anchor.parent.parent.get_text(" ", strip=True)) if isinstance(anchor.parent, Tag) and isinstance(anchor.parent.parent, Tag) else full_name
            district = self._extract_numeric_marker(card_text, r"District:\s*([0-9A-Z]+)")
            profile_url = urljoin(source["source_url"], anchor.get("href", ""))
            records.append(self._build_state_legislator_record(source, full_name, profile_url, district, None))
            seen.add(full_name)
        return records

    def _parse_va_senate_members(self, source: dict[str, Any], html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for anchor in soup.select("a[href*='memberpage.php?id=S']"):
            full_name = compact_whitespace(anchor.get_text(" ", strip=True))
            if full_name.lower() == "view profile":
                continue
            if not self._looks_like_person_name(full_name) or full_name in seen:
                continue
            card = anchor
            for _ in range(10):
                classes = " ".join(card.get("class", [])) if isinstance(card, Tag) else ""
                if isinstance(card, Tag) and ("senator-card" in classes or "member-card" in classes):
                    break
                card = card.parent
            card_text = compact_whitespace(card.get_text(" ", strip=True)) if isinstance(card, Tag) else full_name
            district = self._extract_numeric_marker(card_text, r"District\s+([0-9A-Z]+)")
            party = self._extract_party(card_text)
            profile_url = urljoin(source["source_url"], anchor.get("href", "").strip())
            records.append(self._build_state_legislator_record(source, full_name, profile_url, district, party))
            seen.add(full_name)
        return records

    def _parse_mn_house_members(self, source: dict[str, Any], html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for media in soup.select("div.media.my-3"):
            anchors = media.select("a[href*='/members/profile/']")
            if not anchors:
                continue
            anchor = next((a for a in anchors if compact_whitespace(a.get_text(" ", strip=True))), anchors[0])
            href = anchor.get("href", "")
            link_text = compact_whitespace(anchor.get_text(" ", strip=True))
            full_name = compact_whitespace(re.sub(r"\s*\([^)]+\)\s*$", "", link_text))
            if not self._looks_like_person_name(full_name) or full_name in seen:
                continue
            context = compact_whitespace(media.get_text(" ", strip=True))
            match = re.search(r"\(([\dA-Z]+),\s*([A-Z]+)\)", context)
            district = match.group(1) if match else None
            party_code = match.group(2) if match else ""
            party = {"R": "Republican", "DFL": "Democratic", "D": "Democratic", "I": "Independent"}.get(party_code.upper(), None)
            profile_url = urljoin(source["source_url"], href)
            records.append(self._build_state_legislator_record(source, full_name, profile_url, district, party))
            seen.add(full_name)
        return records

    def _parse_mn_senate_members_json(
        self, source: dict[str, Any], payload_json: dict[str, Any] | list[Any]
    ) -> list[dict[str, Any]]:
        members = payload_json.get("members", []) if isinstance(payload_json, dict) else payload_json
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in members:
            if not isinstance(item, dict):
                continue
            full_name = compact_whitespace(str(item.get("preferred_full_name") or ""))
            if not self._looks_like_person_name(full_name) or full_name in seen:
                continue
            district = compact_whitespace(str(item.get("dist") or "")) or None
            party_code = compact_whitespace(str(item.get("party") or "")).upper()
            party = {
                "R": "Republican",
                "DFL": "Democratic",
                "D": "Democratic",
                "I": "Independent",
            }.get(party_code, None)
            mem_id = compact_whitespace(str(item.get("mem_id") or ""))
            profile_url = (
                f"https://www.senate.mn/members/member_bio.html?mem_id={mem_id}"
                if mem_id and mem_id != "0000"
                else source["source_url"]
            )
            records.append(self._build_state_legislator_record(source, full_name, profile_url, district, party))
            seen.add(full_name)
        return records

    def _parse_sc_members_page(self, source: dict[str, Any], html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        chamber = source.get("chamber")
        title_prefix = "Senator" if chamber == "senate" else "Representative"
        for member in soup.select("div.member"):
            anchor = member.select_one("a.membername[href]")
            if not anchor:
                continue
            raw_name = compact_whitespace(anchor.get_text(" ", strip=True))
            full_name = compact_whitespace(re.sub(rf"^{title_prefix}\s+", "", raw_name))
            if not self._looks_like_person_name(full_name) or full_name in seen:
                continue
            context = compact_whitespace(member.get_text(" ", strip=True))
            district = self._extract_numeric_marker(context, r"District\s+([0-9A-Z]+)")
            party = self._extract_party(context)
            profile_url = urljoin(source["source_url"], anchor.get("href", "").strip())
            records.append(self._build_state_legislator_record(source, full_name, profile_url, district, party))
            seen.add(full_name)
        return records

    def _parse_ok_senate_members(self, source: dict[str, Any], html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for anchor in soup.select("a[href^='/senators/']"):
            href = anchor.get("href", "").strip()
            if href == "/senators":
                continue
            context = compact_whitespace(anchor.get_text(" ", strip=True))
            match = re.search(r"\b([rdi])\b\s+District\s+([0-9A-Z]+)\s+(.+)$", context, re.IGNORECASE)
            if not match:
                continue
            party_code = match.group(1).upper()
            district = match.group(2)
            full_name = compact_whitespace(match.group(3))
            if not self._looks_like_person_name(full_name) or full_name in seen:
                continue
            party = {"R": "Republican", "D": "Democratic", "I": "Independent"}.get(party_code)
            profile_url = urljoin(source["source_url"], href)
            records.append(self._build_state_legislator_record(source, full_name, profile_url, district, party))
            seen.add(full_name)
        return records

    def _parse_ok_house_members(self, source: dict[str, Any], html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        next_data = soup.find("script", id="__NEXT_DATA__")
        if not next_data or not next_data.string:
            return []
        payload = json.loads(next_data.string)
        members = (
            payload.get("props", {})
            .get("pageProps", {})
            .get("members", {})
            .get("legislatureMembers", [])
        )
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in members:
            if not isinstance(item, dict):
                continue
            attrs = item.get("attributes", {})
            if not isinstance(attrs, dict):
                continue
            first_name = compact_whitespace(str(attrs.get("firstName") or ""))
            last_name = compact_whitespace(str(attrs.get("lastName") or ""))
            suffix = compact_whitespace(str(attrs.get("suffix") or ""))
            full_name = compact_whitespace(" ".join(part for part in [first_name, last_name, suffix] if part))
            if not self._looks_like_person_name(full_name) or full_name in seen:
                continue
            district_data = attrs.get("district", {}).get("data", {}).get("attributes", {})
            district = compact_whitespace(str(district_data.get("number") or "")) or None
            party_data = attrs.get("party", {}).get("data", {}).get("attributes", {})
            party_name = compact_whitespace(str(party_data.get("name") or ""))
            party = {"Democrat": "Democratic", "Republican": "Republican", "Independent": "Independent"}.get(party_name, party_name or None)
            slug = compact_whitespace(str(attrs.get("slug") or ""))
            profile_url = urljoin(source["source_url"], f"/representatives/{slug}") if slug else source["source_url"]
            records.append(self._build_state_legislator_record(source, full_name, profile_url, district, party))
            seen.add(full_name)
        return records

    def _parse_al_legislators_graphql(
        self, source: dict[str, Any], payload_json: dict[str, Any] | list[Any]
    ) -> list[dict[str, Any]]:
        members = (
            payload_json.get("data", {})
            .get("legislativeMembers", {})
            .get("data", [])
            if isinstance(payload_json, dict)
            else []
        )
        chamber = source.get("chamber")
        wanted_body = "Senate" if chamber == "senate" else "House"
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in members:
            if not isinstance(item, dict):
                continue
            body = compact_whitespace(str(item.get("body") or ""))
            if body != wanted_body:
                continue
            honorific = compact_whitespace(str(item.get("honorific") or ""))
            if chamber == "senate" and honorific.lower() == "lieutenant governor":
                continue
            first_name = compact_whitespace(str(item.get("firstName") or ""))
            last_name = compact_whitespace(str(item.get("lastName") or ""))
            full_name = compact_whitespace(str(item.get("fullName") or "")) or compact_whitespace(
                " ".join(part for part in [first_name, last_name] if part)
            )
            if not self._looks_like_person_name(full_name) or full_name in seen:
                continue
            district_label = compact_whitespace(str(item.get("district") or ""))
            district = self._extract_numeric_marker(district_label, r"(?:Senate|House)\s+District\s+([0-9A-Z]+)")
            party_code = compact_whitespace(str(item.get("affiliation") or "")).upper()
            party = {"R": "Republican", "D": "Democratic", "I": "Independent"}.get(party_code, None)
            records.append(self._build_state_legislator_record(source, full_name, source["source_url"], district, party))
            seen.add(full_name)
        return records

    def _parse_wv_roster_table(self, source: dict[str, Any], html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        chamber = source.get("chamber")
        for row in soup.select("table tr"):
            cells = [compact_whitespace(cell.get_text(" ", strip=True)) for cell in row.select("td")]
            if chamber == "senate":
                if len(cells) < 6:
                    continue
                full_name, party, district_cell = cells[0], cells[1], cells[2]
            else:
                if len(cells) < 5:
                    continue
                full_name, party, district_cell = cells[0], cells[1], cells[2]
            if not self._looks_like_person_name(full_name) or full_name in seen:
                continue
            district = self._extract_numeric_marker(district_cell, r"([0-9A-Z]+)")
            normalized_party = (
                "Republican" if party.lower().startswith("rep") else
                "Democratic" if party.lower().startswith("dem") else
                "Independent" if party.lower().startswith("ind") else
                None
            )
            records.append(self._build_state_legislator_record(source, full_name, source["source_url"], district, normalized_party))
            seen.add(full_name)
        return records

    def _parse_ks_roster_table(self, source: dict[str, Any], html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in soup.select("table tr"):
            cells = [compact_whitespace(cell.get_text(" ", strip=True)) for cell in row.select("td")]
            if len(cells) < 2:
                continue
            link = row.select_one("a[href*='/li/b2025_26/members/']")
            if not link:
                continue
            full_name = re.sub(r"^(Sen|Rep)\.\s+", "", cells[0]).strip()
            district = self._extract_numeric_marker(cells[1], r"([0-9A-Z]+)")
            profile_url = urljoin(source["source_url"], link.get("href", ""))
            try:
                detail_html = self._fetch_html_lenient(profile_url)
                detail_soup = BeautifulSoup(detail_html, "html.parser")
                title_text = compact_whitespace(detail_soup.title.get_text(" ", strip=True)) if detail_soup.title else ""
                title_match = re.match(r"^(?:Senator|Representative)\s+(.+?)\s+\|", title_text)
                if title_match:
                    full_name = compact_whitespace(title_match.group(1))
            except Exception:
                pass
            if not self._looks_like_person_name(full_name) or full_name in seen:
                continue
            records.append(self._build_state_legislator_record(source, full_name, profile_url, district, None))
            seen.add(full_name)
        return records

    def _parse_ar_senate_members(self, source: dict[str, Any], html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        records: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        for link in soup.select("a[href*='senate.arkansas.gov/senators/']"):
            href = link.get("href", "")
            if not re.search(r"/senators/\d+/?$", href):
                continue
            profile_url = href
            if profile_url in seen_urls:
                continue
            card = link
            for _ in range(6):
                if getattr(card, "parent", None) is None:
                    break
                card = card.parent
            card_text = compact_whitespace(card.get_text(" ", strip=True))
            match = re.match(r"(?P<name>.+?)\s+District\s+(?P<district>\d+)\b", card_text)
            if not match:
                continue
            full_name = compact_whitespace(match.group("name"))
            district = match.group("district")
            party = None
            try:
                detail_html = self._fetch_html_lenient(profile_url)
                detail_text = compact_whitespace(BeautifulSoup(detail_html, "html.parser").get_text(" ", strip=True))
                party_match = re.search(r"Party:\s*(Republican|Democrat|Independent)\b", detail_text, re.I)
                if party_match:
                    party = party_match.group(1).title()
            except Exception:
                party = None
            if not self._looks_like_person_name(full_name):
                continue
            records.append(self._build_state_legislator_record(source, full_name, profile_url, district, party))
            seen_urls.add(profile_url)
        return records

    def _parse_ar_house_members(self, source: dict[str, Any], html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        records: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        for link in soup.select("a[href*='arkansashouse.org/district/']"):
            profile_url = link.get("href", "")
            if not profile_url or profile_url in seen_urls:
                continue
            text = compact_whitespace(link.get_text(" ", strip=True))
            match = re.match(
                r"(?P<name>.+?)\s+(?P<party>Republican|Democrat|Independent)\s+(?P<district>\d+)(?:st|nd|rd|th)\s+District",
                text,
                re.I,
            )
            if not match:
                continue
            full_name = compact_whitespace(match.group("name"))
            district = match.group("district")
            party = match.group("party").title()
            if not self._looks_like_person_name(full_name):
                continue
            records.append(self._build_state_legislator_record(source, full_name, profile_url, district, party))
            seen_urls.add(profile_url)
        return records

    def _parse_sd_session_members_csv(self, source: dict[str, Any], csv_text: str) -> list[dict[str, Any]]:
        requested_chamber = source.get("chamber")
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in csv.DictReader(csv_text.splitlines()):
            body = compact_whitespace(str(row.get("Body") or "")).upper()
            chamber = "senate" if body == "S" else "house" if body == "H" else None
            if requested_chamber and chamber != requested_chamber:
                continue
            last_name = compact_whitespace(str(row.get("LastName") or ""))
            first_name = compact_whitespace(str(row.get("FirstName") or ""))
            initial = compact_whitespace(str(row.get("Initial") or ""))
            parts = [first_name]
            if initial:
                parts.append(initial)
            parts.append(last_name)
            full_name = compact_whitespace(" ".join(part for part in parts if part))
            if not self._looks_like_person_name(full_name) or full_name in seen:
                continue
            district = compact_whitespace(str(row.get("District") or "")) or None
            party_code = compact_whitespace(str(row.get("Politics") or "")).upper()
            party = "Republican" if party_code == "R" else "Democratic" if party_code == "D" else party_code or None
            profile_url = source.get("profile_base_url", source["source_url"])
            records.append(self._build_state_legislator_record(source, full_name, profile_url, district, party))
            seen.add(full_name)
        return records

    def _build_state_legislator_record(
        self,
        source: dict[str, Any],
        full_name: str,
        profile_url: str,
        district: str | None,
        party: str | None,
    ) -> dict[str, Any]:
        state = source["state"]
        chamber = source.get("chamber")
        role_title = source.get("role_title") or ("State Senator" if chamber == "senate" else "State Representative")
        office_name = source.get("office_name") or (f"{state} State Senate" if chamber == "senate" else f"{state} House of Representatives")
        subdepartment_name = source.get("subdepartment_name") or ("State Senate" if chamber == "senate" else "House of Representatives")
        aliases = [
            f"Sen. {full_name}" if chamber == "senate" else f"Rep. {full_name}",
            f"{state} {role_title.lower()} {full_name}",
        ]
        raw_payload = {
            "state": state,
            "district": district,
            "party": party,
            "office_title": role_title,
            "department_name": state,
            "top_department_name": state,
            "subdepartment_name": subdepartment_name,
            "unit_name": district,
            "official_roster_url": source["source_url"],
        }
        return {
            "person": {
                "full_name": full_name,
                "source_url": profile_url,
                "source_type": "official",
                "seed_source_type": "official",
                "profile_status": "officially_enriched",
                "canonical_official_url": profile_url,
                "parser_identity": source["parser_identity"],
                "verification_status": "official_link",
                "raw_payload": raw_payload,
            },
            "jurisdiction": {"name": state, "type": "state", "code": state},
            "office": {
                "office_name": office_name,
                "level": "state",
                "branch": "legislative",
                "chamber": chamber,
                "source_url": source["source_url"],
                "source_type": "official",
            },
            "appointment": {
                "role_title": role_title,
                "district": district,
                "status": "current",
                "source_url": profile_url,
                "source_type": "official",
                "parser_identity": source["parser_identity"],
                "is_current": True,
                "raw_payload": raw_payload,
            },
            "aliases": aliases,
        }

    def _extract_numeric_marker(self, text: str, pattern: str) -> str | None:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
        return None

    def _extract_party(self, text: str) -> str | None:
        cleaned = text.lower()
        if "(r)" in cleaned or " republican" in cleaned:
            return "Republican"
        if "(d)" in cleaned or " democratic" in cleaned or " democrat" in cleaned:
            return "Democratic"
        if "(i)" in cleaned or " independent" in cleaned:
            return "Independent"
        return None

    def _clean_florida_senate_name(self, value: str) -> str:
        cleaned = re.sub(
            r"\b(President|President Pro Tempore|Minority \(Democratic\) Leader|Majority \(Republican\) Leader|Minority Leader|Majority Leader)\b",
            "",
            value,
            flags=re.IGNORECASE,
        )
        cleaned = compact_whitespace(cleaned.replace(" ,", ",")).strip(", ")
        if "," in cleaned:
            family, given = [part.strip() for part in cleaned.split(",", 1)]
            cleaned = f"{given} {family}"
        return cleaned

