"""Kennzahlen für Dashboard und Controlling – ausschließlich echte Daten aus MySQL."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import func, literal_column, select
from sqlalchemy.orm import Session

from ..core.db import last_db_status
from ..core.models import (
    Application,
    ApplicationType,
    AuditLog,
    BetaTester,
    Bug,
    BugAssignment,
    DiscordOutbox,
    DiscordUser,
    ErrorLog,
    MemberEvent,
    MemberSnapshot,
    SystemStatus,
    TeamMember,
    TesterGroup,
    TestGroup,
    Ticket,
    utcnow,
)

SECOND = literal_column("SECOND")


def _seconds(a, b):
    return func.timestampdiff(SECOND, a, b)


def _avg_hours(session: Session, expr, *where) -> Optional[float]:
    val = session.scalar(select(func.avg(expr)).where(*where))
    return round(float(val) / 3600, 2) if val is not None else None


def _count_by(session: Session, col, *where) -> dict:
    return {k: v for k, v in session.execute(select(col, func.count()).where(*where).group_by(col)).all()}


def system_status(session: Session) -> dict:
    now = utcnow()
    rows = {r.component: r for r in session.scalars(select(SystemStatus))}
    bot = rows.get("bot")
    bot_online = bool(bot and bot.status == "online" and bot.last_seen > now - timedelta(seconds=90))
    errors = session.scalars(select(ErrorLog).order_by(ErrorLog.created_at.desc()).limit(10)).all()
    outbox = _count_by(session, DiscordOutbox.status, DiscordOutbox.status.in_(("pending", "processing", "failed")))
    db = last_db_status()
    return {
        "bot": {"online": bot_online, "status": bot.status if bot else "unbekannt",
                "last_seen": bot.last_seen.isoformat() if bot else None, "details": bot.details if bot else None},
        "database": {"connected": True, "last_success": db["last_success"], "last_error": db["last_error"]},
        "api": {"online": True, "time": now.isoformat()},
        "outbox": {"pending": outbox.get("pending", 0) + outbox.get("processing", 0), "failed": outbox.get("failed", 0)},
        "errors": [{"id": e.id, "component": e.component, "message": e.message[:300], "context": e.context,
                    "created_at": e.created_at.isoformat()} for e in errors],
        "errors_24h": session.scalar(select(func.count(ErrorLog.id)).where(ErrorLog.created_at > now - timedelta(hours=24))),
    }


def community_stats(session: Session, days: int = 30) -> dict:
    now = utcnow()
    since = now - timedelta(days=days)
    bot = session.get(SystemStatus, "bot")
    member_count = (bot.details or {}).get("member_count") if bot and bot.details else None
    if member_count is None:
        member_count = session.scalar(select(func.count(DiscordUser.id)).where(DiscordUser.is_member.is_(True)))
    joins = session.scalar(select(func.count(MemberEvent.id)).where(MemberEvent.event == "join", MemberEvent.created_at > since))
    leaves = session.scalar(select(func.count(MemberEvent.id)).where(MemberEvent.event == "leave", MemberEvent.created_at > since))
    snaps = session.execute(select(MemberSnapshot.day, MemberSnapshot.member_count)
                            .where(MemberSnapshot.day >= (date.today() - timedelta(days=days)))
                            .order_by(MemberSnapshot.day)).all()
    verified = session.scalar(select(func.count(DiscordUser.id)).where(DiscordUser.verified.is_(True), DiscordUser.is_member.is_(True)))
    return {"member_count": member_count, "joins": joins, "leaves": leaves, "growth": joins - leaves,
            "verified": verified, "period_days": days,
            "history": [{"day": d.isoformat(), "count": c} for d, c in snaps]}


def ticket_stats(session: Session, since: Optional[datetime] = None) -> dict:
    by_status = _count_by(session, Ticket.status)
    where_period = [Ticket.created_at >= since] if since else []
    closed_where = [Ticket.status == "closed", Ticket.closed_at.is_not(None)] + (
        [Ticket.closed_at >= since] if since else [])
    per_member = session.execute(
        select(DiscordUser.username, func.count(Ticket.id),
               func.sum(func.if_(Ticket.status == "closed", 1, 0)))
        .join(TeamMember, TeamMember.id == Ticket.assignee_id).join(DiscordUser, DiscordUser.id == TeamMember.user_id)
        .where(*where_period).group_by(DiscordUser.username).order_by(func.count(Ticket.id).desc())
    ).all()
    return {
        "open": by_status.get("open", 0), "in_progress": by_status.get("in_progress", 0),
        "waiting": by_status.get("waiting", 0), "closed": by_status.get("closed", 0),
        "created_in_period": session.scalar(select(func.count(Ticket.id)).where(*where_period)) if since else None,
        "closed_in_period": session.scalar(select(func.count(Ticket.id)).where(*closed_where)) if since else None,
        "avg_resolution_hours": _avg_hours(session, _seconds(Ticket.created_at, Ticket.closed_at), *closed_where),
        "avg_first_response_hours": _avg_hours(session, _seconds(Ticket.created_at, Ticket.first_response_at),
                                               Ticket.first_response_at.is_not(None), *where_period),
        "per_member": [{"name": n, "total": int(t), "closed": int(c or 0)} for n, t, c in per_member],
    }


def application_stats(session: Session, since: Optional[datetime] = None) -> dict:
    where = [Application.submitted_at >= since] if since else []
    by_status = _count_by(session, Application.status, *where)
    decided = by_status.get("accepted", 0) + by_status.get("rejected", 0)
    by_type = session.execute(select(ApplicationType.name, Application.status, func.count(Application.id))
                              .join(ApplicationType, ApplicationType.id == Application.type_id).where(*where)
                              .group_by(ApplicationType.name, Application.status)).all()
    types: dict = {}
    for name, status, cnt in by_type:
        types.setdefault(name, {"name": name, "total": 0})
        types[name][status] = cnt
        types[name]["total"] += cnt
    return {
        "new": by_status.get("new", 0), "in_review": by_status.get("in_review", 0),
        "inquiry": by_status.get("inquiry", 0), "accepted": by_status.get("accepted", 0),
        "rejected": by_status.get("rejected", 0), "total": sum(by_status.values()),
        "acceptance_rate": round(100 * by_status.get("accepted", 0) / decided, 1) if decided else None,
        "avg_decision_hours": _avg_hours(session, _seconds(Application.submitted_at, Application.decided_at),
                                         Application.decided_at.is_not(None), *where),
        "by_type": list(types.values()),
    }


def beta_stats(session: Session) -> dict:
    by_status = _count_by(session, BetaTester.status)
    groups = session.execute(
        select(TestGroup.name, func.count(TesterGroup.id))
        .outerjoin(TesterGroup, TesterGroup.group_id == TestGroup.id).group_by(TestGroup.name).order_by(TestGroup.name)
    ).all()
    return {"active": by_status.get("active", 0), "inactive": by_status.get("inactive", 0),
            "blocked": by_status.get("blocked", 0), "retired": by_status.get("retired", 0),
            "groups": session.scalar(select(func.count(TestGroup.id))),
            "per_group": [{"name": n, "count": c} for n, c in groups]}


def bug_stats(session: Session, since: Optional[datetime] = None) -> dict:
    by_status = _count_by(session, Bug.status)
    per_dev = session.execute(
        select(BugAssignment.developer_name, func.count(func.distinct(BugAssignment.bug_id)))
        .where(*([BugAssignment.assigned_at >= since] if since else [])).group_by(BugAssignment.developer_name)
        .order_by(func.count(func.distinct(BugAssignment.bug_id)).desc())
    ).all()
    resolved = dict(session.execute(
        select(Bug.assignee_name, func.count(Bug.id))
        .where(Bug.status == "fixed", Bug.assignee_name.is_not(None), *([Bug.resolved_at >= since] if since else []))
        .group_by(Bug.assignee_name)).all())
    data = {
        "new": by_status.get("new", 0), "claimed": by_status.get("claimed", 0),
        "in_progress": by_status.get("in_progress", 0), "fixed": by_status.get("fixed", 0),
        "not_reproducible": by_status.get("not_reproducible", 0), "total": sum(by_status.values()),
        "avg_resolution_hours": _avg_hours(session, _seconds(Bug.created_at, Bug.resolved_at),
                                           Bug.resolved_at.is_not(None), *([Bug.resolved_at >= since] if since else [])),
        "avg_claim_hours": _avg_hours(session, _seconds(Bug.created_at, Bug.claimed_at), Bug.claimed_at.is_not(None)),
        "per_developer": [{"name": n, "claimed": c, "fixed": resolved.get(n, 0)} for n, c in per_dev],
    }
    if since:
        data["reported_in_period"] = session.scalar(select(func.count(Bug.id)).where(Bug.created_at >= since))
        data["claimed_in_period"] = session.scalar(select(func.count(BugAssignment.id)).where(
            BugAssignment.assigned_at >= since, BugAssignment.kind == "claim"))
        data["fixed_in_period"] = session.scalar(select(func.count(Bug.id)).where(
            Bug.status == "fixed", Bug.resolved_at >= since))
    return data


def team_stats(session: Session, since: Optional[datetime] = None) -> dict:
    since = since or (utcnow() - timedelta(days=30))
    members = session.scalars(select(TeamMember).where(TeamMember.status == "active")).all()
    out = []
    for m in members:
        did = m.user.discord_id
        tickets_handled = session.scalar(select(func.count(Ticket.id)).where(
            Ticket.assignee_id == m.id, Ticket.updated_at >= since))
        tickets_closed = session.scalar(select(func.count(Ticket.id)).where(
            Ticket.assignee_id == m.id, Ticket.status == "closed", Ticket.closed_at >= since))
        bugs_claimed = session.scalar(select(func.count(BugAssignment.id)).where(
            BugAssignment.developer_discord_id == did, BugAssignment.assigned_at >= since))
        bugs_fixed = session.scalar(select(func.count(Bug.id)).where(
            Bug.assignee_discord_id == did, Bug.status == "fixed", Bug.resolved_at >= since))
        actions = session.scalar(select(func.count(AuditLog.id)).where(
            AuditLog.actor_discord_id == did, AuditLog.created_at >= since))
        out.append({"member_id": m.id, "name": m.user.display_name or m.user.username,
                    "role": m.role.name if m.role else None, "actions": actions, "tickets_handled": tickets_handled,
                    "tickets_closed": tickets_closed, "bugs_claimed": bugs_claimed, "bugs_fixed": bugs_fixed,
                    "last_activity_at": m.last_activity_at.isoformat() if m.last_activity_at else None})
    out.sort(key=lambda r: r["actions"], reverse=True)
    return {"active_members": len(members), "members": out, "since": since.isoformat()}


def dashboard(session: Session) -> dict:
    week = utcnow() - timedelta(days=7)
    tickets = ticket_stats(session)
    team = team_stats(session, week)
    return {
        "community": community_stats(session),
        "tickets": tickets,
        "applications": application_stats(session),
        "beta": beta_stats(session),
        "bugs": bug_stats(session),
        "team": {
            "active_members": team["active_members"],
            "tickets_handled_7d": sum(m["tickets_handled"] for m in team["members"]),
            "tickets_closed_7d": sum(m["tickets_closed"] for m in team["members"]),
            "bugs_claimed_7d": sum(m["bugs_claimed"] for m in team["members"]),
        },
        "recent_bugs": [{"id": b.id, "title": b.title, "status": b.status, "assignee": b.assignee_name,
                         "created_at": b.created_at.isoformat()}
                        for b in session.scalars(select(Bug).order_by(Bug.created_at.desc()).limit(6))],
        "recent_tickets": [{"id": t.id, "number": t.number, "subject": t.subject, "status": t.status,
                            "created_at": t.created_at.isoformat()}
                           for t in session.scalars(select(Ticket).where(Ticket.status != "closed")
                                                    .order_by(Ticket.last_activity_at.desc()).limit(6))],
        "system": system_status(session),
    }


def controlling(session: Session, days: int = 30) -> dict:
    since = utcnow() - timedelta(days=days)
    return {
        "period_days": days, "since": since.isoformat(),
        "tickets": ticket_stats(session, since),
        "applications": application_stats(session, since),
        "beta": beta_stats(session),
        "bugs": bug_stats(session, since),
        "team": team_stats(session, since),
        "community": community_stats(session, days),
        "timeline": timeline(session, days),
    }


def timeline(session: Session, days: int) -> list[dict]:
    """Tagesreihe: neue Tickets, neue Bugs, behobene Bugs, Bewerbungen."""
    start = (utcnow() - timedelta(days=days - 1)).date()

    def per_day(col, *where):
        return {d: c for d, c in session.execute(
            select(func.date(col), func.count()).where(col >= start, *where).group_by(func.date(col))).all()}

    t = per_day(Ticket.created_at)
    b = per_day(Bug.created_at)
    f = per_day(Bug.resolved_at, Bug.status == "fixed")
    a = per_day(Application.submitted_at)
    out = []
    for i in range(days):
        d = start + timedelta(days=i)
        key_variants = (d, d.isoformat())
        def g(m):
            for k in key_variants:
                if k in m:
                    return m[k]
            return 0
        out.append({"day": d.isoformat(), "tickets": g(t), "bugs": g(b), "bugs_fixed": g(f), "applications": g(a)})
    return out


