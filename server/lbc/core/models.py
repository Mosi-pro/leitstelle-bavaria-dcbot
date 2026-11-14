"""SQLAlchemy-Datenmodell der zentralen STRATO-MySQL-Datenbank.

Konventionen:
- Discord-IDs (Snowflakes) werden als VARCHAR(20) gespeichert, damit sie verlustfrei bis ins
  Frontend (JavaScript) übertragen werden können.
- Alle Zeitstempel sind UTC (naiv gespeichert).
- Jede Tabelle hat eine eindeutige, automatisch vergebene ID.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

SNOWFLAKE = String(20)
LongText = Text().with_variant(MEDIUMTEXT(), "mysql")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


# ---------------------------------------------------------------------------
# Discord-Benutzer, Team, Rollen und Berechtigungen
# ---------------------------------------------------------------------------
class DiscordUser(TimestampMixin, Base):
    """Jede Person, die mit dem System interagiert (Mitglied, Bewerber, Melder, Tester, Team)."""

    __tablename__ = "discord_users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    discord_id: Mapped[str] = mapped_column(SNOWFLAKE, unique=True, nullable=False)
    username: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    display_name: Mapped[Optional[str]] = mapped_column(String(100))
    avatar_url: Mapped[Optional[str]] = mapped_column(String(300))
    is_member: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    joined_guild_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    left_guild_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class TeamRole(TimestampMixin, Base):
    __tablename__ = "team_roles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(255))
    position: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    discord_role_id: Mapped[Optional[str]] = mapped_column(SNOWFLAKE)
    permissions: Mapped[list["RolePermission"]] = relationship(
        back_populates="role", cascade="all, delete-orphan", lazy="selectin"
    )


class RolePermission(Base):
    __tablename__ = "role_permissions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("team_roles.id", ondelete="CASCADE"), nullable=False)
    permission: Mapped[str] = mapped_column(String(60), nullable=False)
    role: Mapped[TeamRole] = relationship(back_populates="permissions")
    __table_args__ = (UniqueConstraint("role_id", "permission", name="uq_role_permission"),)


class TeamMember(TimestampMixin, Base):
    __tablename__ = "team_members"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("discord_users.id", ondelete="RESTRICT"), unique=True, nullable=False)
    role_id: Mapped[Optional[int]] = mapped_column(ForeignKey("team_roles.id", ondelete="SET NULL"))
    # active | inactive | suspended
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False, index=True)
    joined_team_at: Mapped[date] = mapped_column(Date, default=lambda: utcnow().date(), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_activity_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    user: Mapped[DiscordUser] = relationship(lazy="joined")
    role: Mapped[Optional[TeamRole]] = relationship(lazy="joined")
    overrides: Mapped[list["MemberPermissionOverride"]] = relationship(
        back_populates="member", cascade="all, delete-orphan", lazy="selectin"
    )


class MemberPermissionOverride(Base):
    """Individuelle Rechte unabhängig von der Teamrolle (allow=True erteilt, allow=False entzieht)."""

    __tablename__ = "member_permission_overrides"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("team_members.id", ondelete="CASCADE"), nullable=False)
    permission: Mapped[str] = mapped_column(String(60), nullable=False)
    allow: Mapped[bool] = mapped_column(Boolean, nullable=False)
    member: Mapped[TeamMember] = relationship(back_populates="overrides")
    __table_args__ = (UniqueConstraint("member_id", "permission", name="uq_member_permission"),)


class WebSession(Base):
    __tablename__ = "web_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    member_id: Mapped[int] = mapped_column(ForeignKey("team_members.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    ip_address: Mapped[Optional[str]] = mapped_column(String(64))
    user_agent: Mapped[Optional[str]] = mapped_column(String(255))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------
class TicketCategory(TimestampMixin, Base):
    __tablename__ = "ticket_categories"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(200))
    emoji: Mapped[Optional[str]] = mapped_column(String(40))
    discord_category_id: Mapped[Optional[str]] = mapped_column(SNOWFLAKE)
    support_role_id: Mapped[Optional[str]] = mapped_column(SNOWFLAKE)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=100, nullable=False)


class Ticket(TimestampMixin, Base):
    __tablename__ = "tickets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    number: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    category_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ticket_categories.id", ondelete="SET NULL"))
    creator_id: Mapped[int] = mapped_column(ForeignKey("discord_users.id", ondelete="RESTRICT"), nullable=False, index=True)
    assignee_id: Mapped[Optional[int]] = mapped_column(ForeignKey("team_members.id", ondelete="SET NULL"), index=True)
    subject: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    # open | in_progress | waiting | closed
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False, index=True)
    # low | normal | high | urgent
    priority: Mapped[str] = mapped_column(String(10), default="normal", nullable=False)
    channel_id: Mapped[Optional[str]] = mapped_column(SNOWFLAKE, unique=True)
    first_response_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    closed_by: Mapped[Optional[str]] = mapped_column(String(100))
    close_reason: Mapped[Optional[str]] = mapped_column(String(300))
    auto_close_warned_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    reopen_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    category: Mapped[Optional[TicketCategory]] = relationship(lazy="joined")
    creator: Mapped[DiscordUser] = relationship(lazy="joined")
    assignee: Mapped[Optional[TeamMember]] = relationship(lazy="joined")


class TicketMessage(Base):
    __tablename__ = "ticket_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True)
    discord_message_id: Mapped[Optional[str]] = mapped_column(SNOWFLAKE, unique=True)
    author_discord_id: Mapped[str] = mapped_column(SNOWFLAKE, nullable=False)
    author_name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_staff: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    attachments: Mapped[Optional[list[Any]]] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(10), default="discord", nullable=False)  # discord | panel | system
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    edited_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class TicketNote(Base):
    """Interne Notiz – niemals für den Ticket-Ersteller sichtbar."""

    __tablename__ = "ticket_notes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True)
    author_id: Mapped[Optional[int]] = mapped_column(ForeignKey("team_members.id", ondelete="SET NULL"))
    author_name: Mapped[str] = mapped_column(String(100), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class TicketTranscript(Base):
    __tablename__ = "ticket_transcripts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True)
    content: Mapped[str] = mapped_column(LongText, nullable=False)
    message_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


# ---------------------------------------------------------------------------
# Bewerbungen
# ---------------------------------------------------------------------------
class ApplicationType(TimestampMixin, Base):
    __tablename__ = "application_types"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(300))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    accept_role_id: Mapped[Optional[str]] = mapped_column(SNOWFLAKE)
    auto_assign_role: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    create_beta_tester: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    accept_message: Mapped[Optional[str]] = mapped_column(Text)
    reject_message: Mapped[Optional[str]] = mapped_column(Text)
    retention_days: Mapped[int] = mapped_column(Integer, default=180, nullable=False)
    questions: Mapped[list["ApplicationQuestion"]] = relationship(
        back_populates="type", cascade="all, delete-orphan", order_by="ApplicationQuestion.position", lazy="selectin"
    )


class ApplicationQuestion(Base):
    __tablename__ = "application_questions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type_id: Mapped[int] = mapped_column(ForeignKey("application_types.id", ondelete="CASCADE"), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    # text | textarea | select | multiselect | yesno
    field_type: Mapped[str] = mapped_column(String(20), nullable=False, default="text")
    required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    options: Mapped[Optional[list[str]]] = mapped_column(JSON)
    max_length: Mapped[int] = mapped_column(Integer, default=1000, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    type: Mapped[ApplicationType] = relationship(back_populates="questions")


class Application(TimestampMixin, Base):
    __tablename__ = "applications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type_id: Mapped[int] = mapped_column(ForeignKey("application_types.id", ondelete="RESTRICT"), nullable=False, index=True)
    applicant_id: Mapped[int] = mapped_column(ForeignKey("discord_users.id", ondelete="RESTRICT"), nullable=False, index=True)
    # new | in_review | inquiry | accepted | rejected
    status: Mapped[str] = mapped_column(String(20), default="new", nullable=False, index=True)
    reviewer_id: Mapped[Optional[int]] = mapped_column(ForeignKey("team_members.id", ondelete="SET NULL"))
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    decision_reason: Mapped[Optional[str]] = mapped_column(Text)
    anonymized_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    type: Mapped[ApplicationType] = relationship(lazy="joined")
    applicant: Mapped[DiscordUser] = relationship(lazy="joined")
    reviewer: Mapped[Optional[TeamMember]] = relationship(lazy="joined")
    answers: Mapped[list["ApplicationAnswer"]] = relationship(
        cascade="all, delete-orphan", order_by="ApplicationAnswer.position", lazy="selectin"
    )


class ApplicationAnswer(Base):
    __tablename__ = "application_answers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True)
    question_id: Mapped[Optional[int]] = mapped_column(ForeignKey("application_questions.id", ondelete="SET NULL"))
    question_label: Mapped[str] = mapped_column(String(200), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class ApplicationNote(Base):
    __tablename__ = "application_notes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True)
    author_id: Mapped[Optional[int]] = mapped_column(ForeignKey("team_members.id", ondelete="SET NULL"))
    author_name: Mapped[str] = mapped_column(String(100), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class ApplicationRating(Base):
    __tablename__ = "application_ratings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), nullable=False)
    member_id: Mapped[int] = mapped_column(ForeignKey("team_members.id", ondelete="CASCADE"), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    __table_args__ = (UniqueConstraint("application_id", "member_id", name="uq_rating_member"),)


class ApplicationHistory(Base):
    __tablename__ = "application_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True)
    actor_name: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    from_status: Mapped[Optional[str]] = mapped_column(String(20))
    to_status: Mapped[Optional[str]] = mapped_column(String(20))
    comment: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


# ---------------------------------------------------------------------------
# Spielversionen
# ---------------------------------------------------------------------------
class GameVersion(TimestampMixin, Base):
    __tablename__ = "game_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    name: Mapped[Optional[str]] = mapped_column(String(100))
    release_date: Mapped[Optional[date]] = mapped_column(Date)
    # development | internal_test | beta | released | archived
    status: Mapped[str] = mapped_column(String(20), default="development", nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text)
    changelog: Mapped[Optional[str]] = mapped_column(LongText)


class VersionDeveloper(Base):
    __tablename__ = "version_developers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("game_versions.id", ondelete="CASCADE"), nullable=False)
    member_id: Mapped[int] = mapped_column(ForeignKey("team_members.id", ondelete="CASCADE"), nullable=False)
    __table_args__ = (UniqueConstraint("version_id", "member_id", name="uq_version_dev"),)


# ---------------------------------------------------------------------------
# Beta-Tester
# ---------------------------------------------------------------------------
class TestGroup(TimestampMixin, Base):
    __tablename__ = "test_groups"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(300))
    discord_role_id: Mapped[Optional[str]] = mapped_column(SNOWFLAKE)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class VersionTestGroup(Base):
    __tablename__ = "version_test_groups"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("game_versions.id", ondelete="CASCADE"), nullable=False)
    group_id: Mapped[int] = mapped_column(ForeignKey("test_groups.id", ondelete="CASCADE"), nullable=False)
    __table_args__ = (UniqueConstraint("version_id", "group_id", name="uq_version_group"),)


class BetaTester(TimestampMixin, Base):
    __tablename__ = "beta_testers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("discord_users.id", ondelete="RESTRICT"), unique=True, nullable=False)
    # active | inactive | blocked | retired
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False, index=True)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    left_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_activity_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    application_id: Mapped[Optional[int]] = mapped_column(ForeignKey("applications.id", ondelete="SET NULL"))
    user: Mapped[DiscordUser] = relationship(lazy="joined")


class TesterGroup(Base):
    __tablename__ = "tester_groups"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tester_id: Mapped[int] = mapped_column(ForeignKey("beta_testers.id", ondelete="CASCADE"), nullable=False)
    group_id: Mapped[int] = mapped_column(ForeignKey("test_groups.id", ondelete="CASCADE"), nullable=False, index=True)
    __table_args__ = (UniqueConstraint("tester_id", "group_id", name="uq_tester_group"),)


class TesterVersion(Base):
    __tablename__ = "tester_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tester_id: Mapped[int] = mapped_column(ForeignKey("beta_testers.id", ondelete="CASCADE"), nullable=False)
    version_id: Mapped[int] = mapped_column(ForeignKey("game_versions.id", ondelete="CASCADE"), nullable=False)
    __table_args__ = (UniqueConstraint("tester_id", "version_id", name="uq_tester_version"),)


class TesterHistory(Base):
    __tablename__ = "tester_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tester_id: Mapped[int] = mapped_column(ForeignKey("beta_testers.id", ondelete="CASCADE"), nullable=False, index=True)
    actor_name: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    details: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class BetaInvitation(Base):
    __tablename__ = "beta_invitations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(150), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("game_versions.id", ondelete="SET NULL"))
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class BetaInvitationRecipient(Base):
    __tablename__ = "beta_invitation_recipients"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invitation_id: Mapped[int] = mapped_column(ForeignKey("beta_invitations.id", ondelete="CASCADE"), nullable=False, index=True)
    tester_id: Mapped[int] = mapped_column(ForeignKey("beta_testers.id", ondelete="CASCADE"), nullable=False)
    # pending | sent | failed
    status: Mapped[str] = mapped_column(String(10), default="pending", nullable=False)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    error: Mapped[Optional[str]] = mapped_column(String(300))
    __table_args__ = (UniqueConstraint("invitation_id", "tester_id", name="uq_invitation_tester"),)


# ---------------------------------------------------------------------------
# Bugs
# ---------------------------------------------------------------------------
class Bug(TimestampMixin, Base):
    __tablename__ = "bugs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # Bug-ID (#0042)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reproduction_steps: Mapped[Optional[str]] = mapped_column(Text)
    reporter_id: Mapped[Optional[int]] = mapped_column(ForeignKey("discord_users.id", ondelete="SET NULL"), index=True)
    reporter_discord_id: Mapped[Optional[str]] = mapped_column(SNOWFLAKE)
    reporter_name: Mapped[Optional[str]] = mapped_column(String(100))
    forum_channel_id: Mapped[Optional[str]] = mapped_column(SNOWFLAKE)
    forum_thread_id: Mapped[Optional[str]] = mapped_column(SNOWFLAKE, unique=True)  # verhindert Doppelerfassung
    version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("game_versions.id", ondelete="SET NULL"), index=True)
    version_text: Mapped[Optional[str]] = mapped_column(String(30))
    fixed_in_version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("game_versions.id", ondelete="SET NULL"))
    # new | claimed | in_progress | fixed | not_reproducible
    status: Mapped[str] = mapped_column(String(20), default="new", nullable=False, index=True)
    # low | normal | high | critical
    priority: Mapped[str] = mapped_column(String(10), default="normal", nullable=False)
    assignee_discord_id: Mapped[Optional[str]] = mapped_column(SNOWFLAKE, index=True)
    assignee_name: Mapped[Optional[str]] = mapped_column(String(100))
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    dev_channel_id: Mapped[Optional[str]] = mapped_column(SNOWFLAKE)
    dev_message_id: Mapped[Optional[str]] = mapped_column(SNOWFLAKE)
    source: Mapped[str] = mapped_column(String(10), default="discord", nullable=False)  # discord | panel
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    version: Mapped[Optional[GameVersion]] = relationship(foreign_keys=[version_id], lazy="joined")
    fixed_in_version: Mapped[Optional[GameVersion]] = relationship(foreign_keys=[fixed_in_version_id], lazy="joined")


class BugAttachment(Base):
    __tablename__ = "bug_attachments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bug_id: Mapped[int] = mapped_column(ForeignKey("bugs.id", ondelete="CASCADE"), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(String(700), nullable=False)
    content_type: Mapped[Optional[str]] = mapped_column(String(100))
    size: Mapped[Optional[int]] = mapped_column(Integer)


class BugComment(Base):
    __tablename__ = "bug_comments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bug_id: Mapped[int] = mapped_column(ForeignKey("bugs.id", ondelete="CASCADE"), nullable=False, index=True)
    author_discord_id: Mapped[Optional[str]] = mapped_column(SNOWFLAKE)
    author_name: Mapped[str] = mapped_column(String(100), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    internal: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class BugAssignment(Base):
    """Verlauf der Entwickler-Zuweisungen eines Bugs."""

    __tablename__ = "bug_assignments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bug_id: Mapped[int] = mapped_column(ForeignKey("bugs.id", ondelete="CASCADE"), nullable=False, index=True)
    developer_discord_id: Mapped[str] = mapped_column(SNOWFLAKE, nullable=False, index=True)
    developer_name: Mapped[str] = mapped_column(String(100), nullable=False)
    assigned_by: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # claim | handover | assign
    assigned_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    note: Mapped[Optional[str]] = mapped_column(String(300))


# ---------------------------------------------------------------------------
# Community: Moderation, Ankündigungen, Umfragen
# ---------------------------------------------------------------------------
class ModerationAction(Base):
    __tablename__ = "moderation_actions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # warn | timeout | kick | ban | unban | untimeout | message_delete
    action: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("discord_users.id", ondelete="RESTRICT"), nullable=False, index=True)
    moderator_discord_id: Mapped[Optional[str]] = mapped_column(SNOWFLAKE)
    moderator_name: Mapped[str] = mapped_column(String(100), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    duration_minutes: Mapped[Optional[int]] = mapped_column(Integer)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    source: Mapped[str] = mapped_column(String(10), nullable=False)  # panel | discord | automod
    # pending | executed | failed
    status: Mapped[str] = mapped_column(String(10), default="pending", nullable=False)
    error: Mapped[Optional[str]] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)
    target: Mapped[DiscordUser] = relationship(lazy="joined")


class Warning(Base):
    __tablename__ = "warnings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("discord_users.id", ondelete="RESTRICT"), nullable=False, index=True)
    moderation_action_id: Mapped[Optional[int]] = mapped_column(ForeignKey("moderation_actions.id", ondelete="SET NULL"))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    moderator_name: Mapped[str] = mapped_column(String(100), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    revoked_by: Mapped[Optional[str]] = mapped_column(String(100))
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    user: Mapped[DiscordUser] = relationship(lazy="joined")


class AutoModRule(TimestampMixin, Base):
    __tablename__ = "automod_rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    # word_filter | invite_links | mention_spam | caps
    rule_type: Mapped[str] = mapped_column(String(20), nullable=False)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # delete | warn | timeout
    action: Mapped[str] = mapped_column(String(10), nullable=False, default="delete")
    timeout_minutes: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class MessageTemplate(TimestampMixin, Base):
    __tablename__ = "message_templates"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    title: Mapped[Optional[str]] = mapped_column(String(256))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    color: Mapped[Optional[str]] = mapped_column(String(7))
    as_embed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Announcement(TimestampMixin, Base):
    __tablename__ = "announcements"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[str] = mapped_column(SNOWFLAKE, nullable=False)
    title: Mapped[Optional[str]] = mapped_column(String(256))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    as_embed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    color: Mapped[Optional[str]] = mapped_column(String(7))
    footer: Mapped[Optional[str]] = mapped_column(String(200))
    image_url: Mapped[Optional[str]] = mapped_column(String(500))
    mention_role_id: Mapped[Optional[str]] = mapped_column(SNOWFLAKE)
    # draft | queued | sent | failed
    status: Mapped[str] = mapped_column(String(10), default="draft", nullable=False, index=True)
    message_id: Mapped[Optional[str]] = mapped_column(SNOWFLAKE)
    error: Mapped[Optional[str]] = mapped_column(String(300))
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class Poll(TimestampMixin, Base):
    __tablename__ = "polls"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    channel_id: Mapped[str] = mapped_column(SNOWFLAKE, nullable=False)
    multiple_choice: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # draft | queued | open | closed | failed
    status: Mapped[str] = mapped_column(String(10), default="draft", nullable=False, index=True)
    ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    message_id: Mapped[Optional[str]] = mapped_column(SNOWFLAKE)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    options: Mapped[list["PollOption"]] = relationship(
        cascade="all, delete-orphan", order_by="PollOption.position", lazy="selectin"
    )


class PollOption(Base):
    __tablename__ = "poll_options"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    poll_id: Mapped[int] = mapped_column(ForeignKey("polls.id", ondelete="CASCADE"), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class PollVote(Base):
    __tablename__ = "poll_votes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    poll_id: Mapped[int] = mapped_column(ForeignKey("polls.id", ondelete="CASCADE"), nullable=False, index=True)
    option_id: Mapped[int] = mapped_column(ForeignKey("poll_options.id", ondelete="CASCADE"), nullable=False)
    voter_discord_id: Mapped[str] = mapped_column(SNOWFLAKE, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    __table_args__ = (UniqueConstraint("poll_id", "option_id", "voter_discord_id", name="uq_poll_vote"),)


class MemberEvent(Base):
    __tablename__ = "member_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    discord_id: Mapped[str] = mapped_column(SNOWFLAKE, nullable=False)
    event: Mapped[str] = mapped_column(String(10), nullable=False)  # join | leave | verify
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)


class MemberSnapshot(Base):
    __tablename__ = "member_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day: Mapped[date] = mapped_column(Date, unique=True, nullable=False)
    member_count: Mapped[int] = mapped_column(Integer, nullable=False)


# ---------------------------------------------------------------------------
# System: Audit, Einstellungen, Automatisierungen, Benachrichtigungen, Synchronisierung
# ---------------------------------------------------------------------------
class AuditLog(Base):
    """Unveränderliches Protokoll – es existieren keine Änderungs- oder Löschendpunkte."""

    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    actor_discord_id: Mapped[Optional[str]] = mapped_column(SNOWFLAKE, index=True)
    actor_name: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    object_type: Mapped[Optional[str]] = mapped_column(String(40), index=True)
    object_id: Mapped[Optional[str]] = mapped_column(String(40))
    details: Mapped[Optional[dict]] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(10), default="panel", nullable=False)
    ip_address: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)
    __table_args__ = (Index("ix_audit_object", "object_type", "object_id"),)


class SystemSetting(Base):
    __tablename__ = "system_settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[Optional[str]] = mapped_column(Text)
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    updated_by: Mapped[Optional[str]] = mapped_column(String(100))


class Automation(Base):
    __tablename__ = "automations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(300))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    config: Mapped[Optional[dict]] = mapped_column(JSON)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    next_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_status: Mapped[Optional[str]] = mapped_column(String(10))
    last_message: Mapped[Optional[str]] = mapped_column(String(500))


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    member_id: Mapped[Optional[int]] = mapped_column(ForeignKey("team_members.id", ondelete="CASCADE"), index=True)
    permission: Mapped[Optional[str]] = mapped_column(String(60))  # an alle mit dieser Berechtigung
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[Optional[str]] = mapped_column(Text)
    link: Mapped[Optional[str]] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)


class NotificationRead(Base):
    __tablename__ = "notification_reads"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    notification_id: Mapped[int] = mapped_column(ForeignKey("notifications.id", ondelete="CASCADE"), nullable=False)
    member_id: Mapped[int] = mapped_column(ForeignKey("team_members.id", ondelete="CASCADE"), nullable=False)
    read_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    __table_args__ = (UniqueConstraint("notification_id", "member_id", name="uq_notification_read"),)


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), default="weekly", nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    posted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)
    __table_args__ = (UniqueConstraint("kind", "period_start", name="uq_report_period"),)


class DiscordOutbox(Base):
    """Auftragswarteschlange Webpanel → Discord-Bot (mit Wiederholungen und Idempotenz)."""

    __tablename__ = "discord_outbox"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(120), unique=True)
    # pending | processing | done | failed
    status: Mapped[str] = mapped_column(String(12), default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=8, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    locked_by: Mapped[Optional[str]] = mapped_column(String(64))
    locked_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    __table_args__ = (Index("ix_outbox_due", "status", "next_attempt_at"),)


class ProcessedEvent(Base):
    """Schutz gegen doppelt zugestellte Discord-Ereignisse."""

    __tablename__ = "processed_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    event_key: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)


class ChangeEvent(Base):
    """Änderungsstrom für WebSocket-Live-Aktualisierung (prozessübergreifend über die DB)."""

    __tablename__ = "change_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    topic: Mapped[str] = mapped_column(String(40), nullable=False)
    object_id: Mapped[Optional[str]] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)


class SystemStatus(Base):
    __tablename__ = "system_status"
    component: Mapped[str] = mapped_column(String(30), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    details: Mapped[Optional[dict]] = mapped_column(JSON)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class ErrorLog(Base):
    __tablename__ = "error_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    component: Mapped[str] = mapped_column(String(30), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[Optional[str]] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)


class Counter(Base):
    """Fortlaufende Nummern (z. B. Ticketnummern), atomar per SELECT … FOR UPDATE vergeben."""

    __tablename__ = "counters"
    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


__all__ = [name for name in dir() if not name.startswith("_")]

# Alle Tabellen explizit als InnoDB mit utf8mb4 anlegen (STRATO-Datenbanken können latin1 als Standard haben)
for _table in Base.metadata.tables.values():
    _table.kwargs.update(mysql_engine="InnoDB", mysql_charset="utf8mb4", mysql_collate="utf8mb4_unicode_ci")
