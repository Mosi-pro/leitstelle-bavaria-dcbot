"""Automatische Wochenreports."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.models import Application, ApplicationType, Bug, BugAssignment, Report, Ticket, utcnow
from . import settings_service
from .base import Actor, Conflict, NotFound, audit, emit, enqueue


def week_bounds(ref: datetime) -> tuple[datetime, datetime]:
    """Vorherige abgeschlossene Kalenderwoche (Montag 00:00 bis Montag 00:00, UTC)."""
    monday = (ref - timedelta(days=ref.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return monday - timedelta(days=7), monday


def collect(session: Session, start: datetime, end: datetime) -> dict:
    def cnt(model_col, *where):
        return session.scalar(select(func.count(model_col)).where(*where)) or 0

    beta_type_ids = list(session.scalars(select(ApplicationType.id).where(ApplicationType.create_beta_tester.is_(True))))
    return {
        "bugs_new": cnt(Bug.id, Bug.created_at >= start, Bug.created_at < end),
        "bugs_claimed": cnt(BugAssignment.id, BugAssignment.kind == "claim", BugAssignment.assigned_at >= start,
                            BugAssignment.assigned_at < end),
        "bugs_fixed": cnt(Bug.id, Bug.status == "fixed", Bug.resolved_at >= start, Bug.resolved_at < end),
        "bugs_open": cnt(Bug.id, Bug.status.in_(("new", "claimed", "in_progress"))),
        "applications_new": cnt(Application.id, Application.submitted_at >= start, Application.submitted_at < end),
        "beta_applications_new": cnt(Application.id, Application.type_id.in_(beta_type_ids),
                                     Application.submitted_at >= start, Application.submitted_at < end)
        if beta_type_ids else 0,
        "applications_accepted": cnt(Application.id, Application.status == "accepted", Application.decided_at >= start,
                                     Application.decided_at < end),
        "tickets_new": cnt(Ticket.id, Ticket.created_at >= start, Ticket.created_at < end),
        "tickets_closed": cnt(Ticket.id, Ticket.status == "closed", Ticket.closed_at >= start, Ticket.closed_at < end),
        "tickets_open": cnt(Ticket.id, Ticket.status != "closed"),
    }


def render(data: dict, start: datetime, end: datetime) -> str:
    last_day = end - timedelta(days=1)
    lines = [
        "LEITSTELLE BAVARIA – WOCHENREPORT",
        f"Zeitraum: {start:%d.%m.%Y} – {last_day:%d.%m.%Y}",
        "",
        f"{data['bugs_new']} neue Bugs eingegangen.",
        f"{data['bugs_claimed']} Bugs übernommen.",
        f"{data['bugs_fixed']} Bugs behoben.",
        f"{data['bugs_open']} Bugs aktuell offen.",
        f"{data['beta_applications_new']} neue Beta-Bewerbungen.",
        f"{data['applications_new']} Bewerbungen insgesamt, {data['applications_accepted']} angenommen.",
        f"{data['tickets_new']} Tickets eröffnet.",
        f"{data['tickets_closed']} Tickets geschlossen.",
        f"{data['tickets_open']} Tickets aktuell offen.",
    ]
    return "\n".join(lines)


def generate(session: Session, actor: Actor, start: Optional[datetime] = None, end: Optional[datetime] = None,
             kind: str = "weekly", post: bool = True) -> Report:
    if start is None or end is None:
        start, end = week_bounds(utcnow())
    existing = session.scalar(select(Report).where(Report.kind == kind, Report.period_start == start))
    if existing is not None and kind == "weekly":
        raise Conflict("Für diesen Zeitraum existiert bereits ein Wochenreport.")
    data = collect(session, start, end)
    report = Report(kind=kind, period_start=start, period_end=end, content=render(data, start, end), data=data,
                    created_by=actor.name)
    session.add(report)
    session.flush()
    audit(session, actor, "reports.generated", "report", report.id, {"kind": kind, "start": start.isoformat()})
    if post:
        post_report(session, actor, report.id)
    emit(session, "reports")
    return report


def post_report(session: Session, actor: Actor, report_id: int) -> None:
    report = session.get(Report, report_id)
    if report is None:
        raise NotFound("Report nicht gefunden.")
    channel = settings_service.get_value(session, "channels.reports")
    if not channel:
        raise Conflict("Kein Report-Channel konfiguriert (Systemeinstellungen → Channels).")
    enqueue(session, "report.post", {"report_id": report.id, "channel_id": channel},
            idempotency_key=f"report-post-{report.id}")
    audit(session, actor, "reports.posted", "report", report.id, {"channel_id": channel})


def weekly_due(session: Session, now: Optional[datetime] = None) -> bool:
    now = now or utcnow()
    if not settings_service.get_value(session, "reports.weekly_enabled"):
        return False
    weekday = int(settings_service.get_value(session, "reports.weekday") or 0)
    hour = int(settings_service.get_value(session, "reports.hour") or 8)
    if now.weekday() != weekday or now.hour < hour:
        return False
    start, _ = week_bounds(now)
    return session.scalar(select(Report).where(Report.kind == "weekly", Report.period_start == start)) is None


def serialize(r: Report) -> dict:
    return {"id": r.id, "kind": r.kind, "period_start": r.period_start.isoformat(), "period_end": r.period_end.isoformat(),
            "content": r.content, "data": r.data, "posted": r.posted, "created_by": r.created_by,
            "created_at": r.created_at.isoformat()}
