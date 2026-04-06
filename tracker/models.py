from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tracker.db import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class Jurisdiction(Base, TimestampMixin):
    __tablename__ = "jurisdictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    code: Mapped[Optional[str]] = mapped_column(String(50))
    parent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("jurisdictions.id"))
    country: Mapped[Optional[str]] = mapped_column(String(100))
    source_url: Mapped[Optional[str]] = mapped_column(String(1024))
    source_type: Mapped[Optional[str]] = mapped_column(String(50))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    parent: Mapped[Optional["Jurisdiction"]] = relationship(remote_side=[id])


class Person(Base, TimestampMixin):
    __tablename__ = "persons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    given_name: Mapped[Optional[str]] = mapped_column(String(100))
    family_name: Mapped[Optional[str]] = mapped_column(String(100))
    honorific: Mapped[Optional[str]] = mapped_column(String(100))
    date_of_birth: Mapped[Optional[date]] = mapped_column(Date)
    place_of_birth: Mapped[Optional[str]] = mapped_column(String(255))
    ethnicity: Mapped[Optional[str]] = mapped_column(String(255))
    religion: Mapped[Optional[str]] = mapped_column(String(255))
    education: Mapped[Optional[str]] = mapped_column(Text)
    career_history: Mapped[Optional[str]] = mapped_column(Text)
    bio: Mapped[Optional[str]] = mapped_column(Text)
    official_slug: Mapped[Optional[str]] = mapped_column(String(255), unique=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(1024))
    source_type: Mapped[Optional[str]] = mapped_column(String(50))
    seed_source_type: Mapped[Optional[str]] = mapped_column(String(50))
    profile_status: Mapped[Optional[str]] = mapped_column(String(50), default="seeded")
    canonical_official_url: Mapped[Optional[str]] = mapped_column(String(1024))
    portrait_url: Mapped[Optional[str]] = mapped_column(String(1024))
    portrait_source_url: Mapped[Optional[str]] = mapped_column(String(1024))
    portrait_source_type: Mapped[Optional[str]] = mapped_column(String(50))
    social_profiles: Mapped[Optional[dict]] = mapped_column(JSON)
    parser_identity: Mapped[Optional[str]] = mapped_column(String(255))
    verification_status: Mapped[Optional[str]] = mapped_column(String(50), default="unverified")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    raw_payload: Mapped[Optional[dict]] = mapped_column(JSON)

    aliases: Mapped[list["Alias"]] = relationship(back_populates="person")
    appointments: Mapped[list["Appointment"]] = relationship(back_populates="person")
    trackers: Mapped[list["Tracker"]] = relationship(back_populates="person")
    statements: Mapped[list["Statement"]] = relationship(back_populates="person")
    statement_participants: Mapped[list["StatementParticipant"]] = relationship(back_populates="person")


class Alias(Base, TimestampMixin):
    __tablename__ = "aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id"), nullable=False, index=True)
    alias: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    alias_type: Mapped[str] = mapped_column(String(50), default="alternate_name", nullable=False)
    source_url: Mapped[Optional[str]] = mapped_column(String(1024))
    source_type: Mapped[Optional[str]] = mapped_column(String(50))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    person: Mapped["Person"] = relationship(back_populates="aliases")


class Office(Base, TimestampMixin):
    __tablename__ = "offices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    office_name: Mapped[str] = mapped_column(String(255), nullable=False)
    level: Mapped[str] = mapped_column(String(50), nullable=False)
    branch: Mapped[Optional[str]] = mapped_column(String(50))
    chamber: Mapped[Optional[str]] = mapped_column(String(50))
    jurisdiction_id: Mapped[Optional[int]] = mapped_column(ForeignKey("jurisdictions.id"))
    source_url: Mapped[Optional[str]] = mapped_column(String(1024))
    source_type: Mapped[Optional[str]] = mapped_column(String(50))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    jurisdiction: Mapped[Optional["Jurisdiction"]] = relationship()
    appointments: Mapped[list["Appointment"]] = relationship(back_populates="office")


class Appointment(Base, TimestampMixin):
    __tablename__ = "appointments"
    __table_args__ = (
        UniqueConstraint("person_id", "office_id", "jurisdiction_id", "role_title", "start_date", name="uq_appointment_identity"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id"), nullable=False, index=True)
    office_id: Mapped[int] = mapped_column(ForeignKey("offices.id"), nullable=False, index=True)
    jurisdiction_id: Mapped[Optional[int]] = mapped_column(ForeignKey("jurisdictions.id"))
    role_title: Mapped[str] = mapped_column(String(255), nullable=False)
    district: Mapped[Optional[str]] = mapped_column(String(100))
    party: Mapped[Optional[str]] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(50), default="current", nullable=False)
    start_date: Mapped[Optional[date]] = mapped_column(Date)
    end_date: Mapped[Optional[date]] = mapped_column(Date)
    source_url: Mapped[Optional[str]] = mapped_column(String(1024))
    source_type: Mapped[Optional[str]] = mapped_column(String(50))
    parser_identity: Mapped[Optional[str]] = mapped_column(String(255))
    verification_status: Mapped[Optional[str]] = mapped_column(String(50), default="unverified")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    raw_payload: Mapped[Optional[dict]] = mapped_column(JSON)

    person: Mapped["Person"] = relationship(back_populates="appointments")
    office: Mapped["Office"] = relationship(back_populates="appointments")
    jurisdiction: Mapped[Optional["Jurisdiction"]] = relationship()


class Source(Base, TimestampMixin):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    parser_identity: Mapped[Optional[str]] = mapped_column(String(255))
    checksum: Mapped[Optional[str]] = mapped_column(String(255))
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    raw_payload: Mapped[Optional[dict]] = mapped_column(JSON)


class HistoricalRoster(Base, TimestampMixin):
    __tablename__ = "historical_rosters"
    __table_args__ = (
        UniqueConstraint("roster_type", "roster_key", name="uq_historical_roster_type_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    roster_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    roster_key: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    ordinal_number: Mapped[Optional[int]] = mapped_column(Integer)
    president_name: Mapped[Optional[str]] = mapped_column(String(255))
    start_date: Mapped[Optional[date]] = mapped_column(Date)
    end_date: Mapped[Optional[date]] = mapped_column(Date)
    source_url: Mapped[Optional[str]] = mapped_column(String(1024))
    source_type: Mapped[Optional[str]] = mapped_column(String(50))
    parser_identity: Mapped[Optional[str]] = mapped_column(String(255))
    raw_payload: Mapped[Optional[dict]] = mapped_column(JSON)

    memberships: Mapped[list["RosterMembership"]] = relationship(back_populates="roster")


class RosterMembership(Base, TimestampMixin):
    __tablename__ = "roster_memberships"
    __table_args__ = (
        UniqueConstraint("roster_id", "person_id", "office_id", "role_title", name="uq_roster_membership_identity"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    roster_id: Mapped[int] = mapped_column(ForeignKey("historical_rosters.id"), nullable=False, index=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id"), nullable=False, index=True)
    office_id: Mapped[Optional[int]] = mapped_column(ForeignKey("offices.id"))
    jurisdiction_id: Mapped[Optional[int]] = mapped_column(ForeignKey("jurisdictions.id"))
    role_title: Mapped[str] = mapped_column(String(255), nullable=False)
    party: Mapped[Optional[str]] = mapped_column(String(50))
    status: Mapped[Optional[str]] = mapped_column(String(50))
    source_url: Mapped[Optional[str]] = mapped_column(String(1024))
    source_type: Mapped[Optional[str]] = mapped_column(String(50))
    parser_identity: Mapped[Optional[str]] = mapped_column(String(255))
    raw_payload: Mapped[Optional[dict]] = mapped_column(JSON)

    roster: Mapped["HistoricalRoster"] = relationship(back_populates="memberships")
    person: Mapped["Person"] = relationship()
    office: Mapped[Optional["Office"]] = relationship()
    jurisdiction: Mapped[Optional["Jurisdiction"]] = relationship()


class SyncRun(Base, TimestampMixin):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    job_type: Mapped[str] = mapped_column(String(50), nullable=False)
    source_name: Mapped[Optional[str]] = mapped_column(String(255))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(50), default="running", nullable=False)
    records_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_created: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_updated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_deactivated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    meta: Mapped[Optional[dict]] = mapped_column(JSON)


class Tracker(Base, TimestampMixin):
    __tablename__ = "trackers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="active", nullable=False)
    include_primary_sources: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    include_media_reports: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    schedule_cron: Mapped[Optional[str]] = mapped_column(String(100))
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_run_status: Mapped[Optional[str]] = mapped_column(String(50))
    last_error_message: Mapped[Optional[str]] = mapped_column(Text)
    source_url: Mapped[Optional[str]] = mapped_column(String(1024))
    source_type: Mapped[Optional[str]] = mapped_column(String(50))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    person: Mapped["Person"] = relationship(back_populates="trackers")
    targets: Mapped[list["TrackerTarget"]] = relationship(back_populates="tracker")


class TrackerTarget(Base, TimestampMixin):
    __tablename__ = "tracker_targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tracker_id: Mapped[int] = mapped_column(ForeignKey("trackers.id"), nullable=False, index=True)
    target_name: Mapped[Optional[str]] = mapped_column(String(255))
    target_type: Mapped[str] = mapped_column(String(50), nullable=False)
    target_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    parser_identity: Mapped[Optional[str]] = mapped_column(String(255))
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_status: Mapped[Optional[str]] = mapped_column(String(50))
    last_error_message: Mapped[Optional[str]] = mapped_column(Text)

    tracker: Mapped["Tracker"] = relationship(back_populates="targets")


class Statement(Base, TimestampMixin):
    __tablename__ = "statements"
    __table_args__ = (
        UniqueConstraint("canonical_event_key", name="uq_statements_canonical_event_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[Optional[int]] = mapped_column(ForeignKey("persons.id"), index=True)
    tracker_id: Mapped[Optional[int]] = mapped_column(ForeignKey("trackers.id"), index=True)
    tracker_target_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tracker_targets.id"), index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    canonical_event_key: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    date_published: Mapped[Optional[datetime]] = mapped_column(DateTime)
    date_collected: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    source_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_domain: Mapped[Optional[str]] = mapped_column(String(255))
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    event_source_preference: Mapped[Optional[str]] = mapped_column(String(50))
    statement_type: Mapped[Optional[str]] = mapped_column(String(100))
    excerpt: Mapped[Optional[str]] = mapped_column(Text)
    full_text: Mapped[Optional[str]] = mapped_column(Text)
    raw_text: Mapped[Optional[str]] = mapped_column(Text)
    relevance_score: Mapped[Optional[float]] = mapped_column(Float)
    review_status: Mapped[str] = mapped_column(String(50), default="pending", nullable=False)
    dedupe_hash: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    is_primary_source: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    matched_keywords: Mapped[Optional[dict]] = mapped_column(JSON)
    raw_payload: Mapped[Optional[dict]] = mapped_column(JSON)

    person: Mapped[Optional["Person"]] = relationship(back_populates="statements")
    participants: Mapped[list["StatementParticipant"]] = relationship(back_populates="statement")


class StatementMention(Base, TimestampMixin):
    __tablename__ = "statement_mentions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    statement_id: Mapped[int] = mapped_column(ForeignKey("statements.id"), nullable=False, index=True)
    mention_text: Mapped[str] = mapped_column(Text, nullable=False)
    context_snippet: Mapped[Optional[str]] = mapped_column(Text)
    keyword: Mapped[Optional[str]] = mapped_column(String(255))


class StatementParticipant(Base, TimestampMixin):
    __tablename__ = "statement_participants"
    __table_args__ = (
        UniqueConstraint("statement_id", "person_id", name="uq_statement_participants_per_statement_person"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    statement_id: Mapped[int] = mapped_column(ForeignKey("statements.id"), nullable=False, index=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id"), nullable=False, index=True)
    role: Mapped[Optional[str]] = mapped_column(String(100))
    source_url: Mapped[Optional[str]] = mapped_column(String(1024))
    source_type: Mapped[Optional[str]] = mapped_column(String(50))

    statement: Mapped["Statement"] = relationship(back_populates="participants")
    person: Mapped["Person"] = relationship(back_populates="statement_participants")


class StatementSource(Base, TimestampMixin):
    __tablename__ = "statement_sources"
    __table_args__ = (
        UniqueConstraint("statement_id", "source_url", name="uq_statement_sources_per_statement_url"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    statement_id: Mapped[int] = mapped_column(ForeignKey("statements.id"), nullable=False, index=True)
    source_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    source_title: Mapped[Optional[str]] = mapped_column(String(500))
    parser_identity: Mapped[Optional[str]] = mapped_column(String(255))
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    raw_payload: Mapped[Optional[dict]] = mapped_column(JSON)


class NotificationLog(Base, TimestampMixin):
    __tablename__ = "notifications_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel: Mapped[str] = mapped_column(String(50), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    target_identifier: Mapped[Optional[str]] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[Optional[dict]] = mapped_column(JSON)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class Legislation(Base, TimestampMixin):
    __tablename__ = "legislation"
    __table_args__ = (
        UniqueConstraint("bill_slug", name="uq_legislation_bill_slug"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    bill_number: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    bill_slug: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    legislation_type: Mapped[Optional[str]] = mapped_column(String(100))
    level: Mapped[str] = mapped_column(String(50), nullable=False)
    jurisdiction_name: Mapped[Optional[str]] = mapped_column(String(255))
    jurisdiction_id: Mapped[Optional[int]] = mapped_column(ForeignKey("jurisdictions.id"))
    chamber: Mapped[Optional[str]] = mapped_column(String(50))
    summary: Mapped[Optional[str]] = mapped_column(Text)
    status_text: Mapped[Optional[str]] = mapped_column(String(255))
    introduced_date: Mapped[Optional[date]] = mapped_column(Date)
    last_action_date: Mapped[Optional[date]] = mapped_column(Date)
    source_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    parser_identity: Mapped[Optional[str]] = mapped_column(String(255))
    relevance_score: Mapped[Optional[float]] = mapped_column(Float)
    is_taiwan_related: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    raw_payload: Mapped[Optional[dict]] = mapped_column(JSON)

    jurisdiction: Mapped[Optional["Jurisdiction"]] = relationship()
    sponsors: Mapped[list["LegislationSponsor"]] = relationship(back_populates="legislation")
    sources: Mapped[list["LegislationSource"]] = relationship(back_populates="legislation")


class LegislationSponsor(Base, TimestampMixin):
    __tablename__ = "legislation_sponsors"
    __table_args__ = (
        UniqueConstraint("legislation_id", "person_id", "role", name="uq_legislation_sponsor_person_role"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    legislation_id: Mapped[int] = mapped_column(ForeignKey("legislation.id"), nullable=False, index=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(100), default="sponsor", nullable=False)
    source_url: Mapped[Optional[str]] = mapped_column(String(1024))
    source_type: Mapped[Optional[str]] = mapped_column(String(50))

    legislation: Mapped["Legislation"] = relationship(back_populates="sponsors")
    person: Mapped["Person"] = relationship()


class LegislationSource(Base, TimestampMixin):
    __tablename__ = "legislation_sources"
    __table_args__ = (
        UniqueConstraint("legislation_id", "source_url", name="uq_legislation_source_url"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    legislation_id: Mapped[int] = mapped_column(ForeignKey("legislation.id"), nullable=False, index=True)
    source_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    source_title: Mapped[Optional[str]] = mapped_column(String(500))
    parser_identity: Mapped[Optional[str]] = mapped_column(String(255))
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    raw_payload: Mapped[Optional[dict]] = mapped_column(JSON)

    legislation: Mapped["Legislation"] = relationship(back_populates="sources")
