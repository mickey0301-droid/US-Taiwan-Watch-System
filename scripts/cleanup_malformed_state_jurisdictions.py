from __future__ import annotations

import json
import re
from datetime import datetime

from sqlalchemy import select

from tracker.db import session_scope
from tracker.models import Appointment, Jurisdiction, Legislation, Office, Person, RosterMembership
from tracker.services.officials_service import OfficialsService


US_STATES = {
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut", "Delaware",
    "Florida", "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky",
    "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota", "Mississippi", "Missouri",
    "Montana", "Nebraska", "Nevada", "New Hampshire", "New Jersey", "New Mexico", "New York",
    "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island",
    "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington",
    "West Virginia", "Wisconsin", "Wyoming",
}

TERRITORIES = {
    "American Samoa",
    "Guam",
    "Northern Mariana Islands",
    "Puerto Rico",
    "District of Columbia",
    "U.S. Virgin Islands",
    "United States Virgin Islands",
    "Virgin Islands",
}

VALID_REGION_PREFIXES = sorted(US_STATES | TERRITORIES, key=len, reverse=True)
BAD_STATE_NAMES = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ") | {"Democratic", "Republican"}


def _infer_region_from_district(district: str | None) -> str | None:
    text = re.sub(r"\s+", " ", str(district or "").strip())
    if not text:
        return None
    lowered = text.lower()
    for region in VALID_REGION_PREFIXES:
        rl = region.lower()
        if lowered == rl or lowered.startswith(rl + " "):
            if region in {"United States Virgin Islands", "Virgin Islands"}:
                return "U.S. Virgin Islands"
            return region
    return None


def _infer_region_from_person_name(full_name: str | None) -> str | None:
    text = re.sub(r"\s+", " ", str(full_name or "").strip())
    if not text:
        return None
    lowered = text.lower()
    for region in VALID_REGION_PREFIXES:
        rl = region.lower()
        if lowered == rl or lowered.startswith(rl + " "):
            if region in {"United States Virgin Islands", "Virgin Islands"}:
                return "U.S. Virgin Islands"
            return region
    return None


def main() -> None:
    moved_appointments = 0
    created_offices = 0
    deleted_offices = 0
    deleted_jurisdictions = 0
    unresolved: list[dict[str, str]] = []

    with session_scope() as session:
        service = OfficialsService(session)

        bad_jurisdictions = session.execute(
            select(Jurisdiction).where(
                Jurisdiction.type == "state",
                Jurisdiction.name.in_(sorted(BAD_STATE_NAMES)),
            )
        ).scalars().all()
        bad_by_id = {item.id: item for item in bad_jurisdictions}

        appts = session.execute(
            select(Appointment).where(Appointment.jurisdiction_id.in_(list(bad_by_id.keys())))
        ).scalars().all()

        for appt in appts:
            target_name = _infer_region_from_district(appt.district)
            if not target_name:
                person = session.execute(select(Person).where(Person.id == appt.person_id)).scalar_one_or_none()
                target_name = _infer_region_from_person_name(person.full_name if person else None)
            if not target_name:
                unresolved.append({"appointment_id": str(appt.id), "district": str(appt.district or "")})
                continue

            target_jurisdiction = session.execute(
                select(Jurisdiction).where(Jurisdiction.type == "state", Jurisdiction.name == target_name)
            ).scalar_one_or_none()
            if not target_jurisdiction:
                unresolved.append({
                    "appointment_id": str(appt.id),
                    "district": str(appt.district or ""),
                    "reason": f"target jurisdiction not found: {target_name}",
                })
                continue

            old_office = session.execute(select(Office).where(Office.id == appt.office_id)).scalar_one_or_none()
            if not old_office:
                unresolved.append({"appointment_id": str(appt.id), "district": str(appt.district or ""), "reason": "office missing"})
                continue

            target_office = session.execute(
                select(Office).where(
                    Office.office_name == old_office.office_name,
                    Office.level == old_office.level,
                    Office.chamber == old_office.chamber,
                    Office.jurisdiction_id == target_jurisdiction.id,
                )
            ).scalar_one_or_none()
            if not target_office:
                target_office = Office(
                    office_name=old_office.office_name,
                    level=old_office.level,
                    branch=old_office.branch,
                    chamber=old_office.chamber,
                    jurisdiction_id=target_jurisdiction.id,
                    source_url=old_office.source_url,
                    source_type=old_office.source_type,
                )
                session.add(target_office)
                session.flush()
                created_offices += 1

            appt.jurisdiction_id = target_jurisdiction.id
            appt.office_id = target_office.id
            appt.last_seen_at = datetime.utcnow()
            moved_appointments += 1

        bad_offices = session.execute(
            select(Office).where(Office.jurisdiction_id.in_(list(bad_by_id.keys())))
        ).scalars().all()
        for office in bad_offices:
            has_appt = session.execute(select(Appointment.id).where(Appointment.office_id == office.id).limit(1)).scalar_one_or_none()
            has_roster = session.execute(select(RosterMembership.id).where(RosterMembership.office_id == office.id).limit(1)).scalar_one_or_none()
            if has_appt or has_roster:
                continue
            session.delete(office)
            deleted_offices += 1
        session.flush()

        for jurisdiction in bad_jurisdictions:
            has_office = session.execute(select(Office.id).where(Office.jurisdiction_id == jurisdiction.id).limit(1)).scalar_one_or_none()
            has_appt = session.execute(select(Appointment.id).where(Appointment.jurisdiction_id == jurisdiction.id).limit(1)).scalar_one_or_none()
            has_roster = session.execute(select(RosterMembership.id).where(RosterMembership.jurisdiction_id == jurisdiction.id).limit(1)).scalar_one_or_none()
            has_leg = session.execute(select(Legislation.id).where(Legislation.jurisdiction_id == jurisdiction.id).limit(1)).scalar_one_or_none()
            if has_office or has_appt or has_roster or has_leg:
                continue
            session.delete(jurisdiction)
            deleted_jurisdictions += 1

    result = {
        "status": "success",
        "moved_appointments": moved_appointments,
        "created_offices": created_offices,
        "deleted_offices": deleted_offices,
        "deleted_jurisdictions": deleted_jurisdictions,
        "unresolved_count": len(unresolved),
        "unresolved_sample": unresolved[:20],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
