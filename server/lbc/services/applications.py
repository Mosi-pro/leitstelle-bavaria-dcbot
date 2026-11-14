"""Bewerbungsmanagement inkl. konfigurierbarer Formulare, Bewertungen, Verlauf und Aufbewahrung."""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Any, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..core.models import (
    Application,
    ApplicationAnswer,
    ApplicationHistory,
    ApplicationNote,
    ApplicationQuestion,
    ApplicationRating,
    ApplicationType,
    BetaTester,
    DiscordUser,
    TeamMember,
    utcnow,
)
from . import settings_service
from .base import Actor, Conflict, Invalid, NotFound, audit, emit, enqueue, notify, upsert_discord_user

STATUSES = ("new", "in_review", "inquiry", "accepted", "rejected")
STATUS_LABELS = {"new": "Neu", "in_review": "In Prüfung", "inquiry": "Rückfragen", "accepted": "Angenommen",
                 "rejected": "Abgelehnt"}
FIELD_TYPES = ("text", "textarea", "select", "multiselect", "yesno")
OPEN_STATUSES = ("new", "in_review", "inquiry")
ANON_DISCORD_ID = "0"

DEFAULT_TYPES = [
    {
        "key": "beta", "name": "Beta-Tester-Bewerbung", "create_beta_tester": True,
        "description": "Bewirb dich als Beta-Tester für Leitstelle Bavaria.",
        "accept_message": "Glückwunsch! Deine Bewerbung als Beta-Tester wurde angenommen.",
        "reject_message": "Vielen Dank für deine Bewerbung. Leider können wir dich aktuell nicht berücksichtigen.",
        "questions": [
            {"label": "Wie alt bist du?", "field_type": "text", "max_length": 3},
            {"label": "Welche Erfahrung hast du mit Spieletests?", "field_type": "textarea"},
            {"label": "Welches Betriebssystem nutzt du?", "field_type": "select",
             "options": ["Windows 10", "Windows 11", "Linux", "macOS"]},
            {"label": "Wie viele Stunden pro Woche kannst du testen?", "field_type": "select",
             "options": ["unter 2", "2–5", "5–10", "über 10"]},
            {"label": "Bist du bereit, Fehler ausführlich zu dokumentieren?", "field_type": "yesno"},
        ],
    },
    {
        "key": "team", "name": "Team-Bewerbung",
        "description": "Bewirb dich für das Team von Leitstelle Bavaria.",
        "accept_message": "Glückwunsch! Deine Team-Bewerbung wurde angenommen. Wir melden uns mit den nächsten Schritten.",
        "reject_message": "Vielen Dank für deine Bewerbung. Leider haben wir uns für andere Bewerber entschieden.",
        "questions": [
            {"label": "Für welchen Bereich bewirbst du dich?", "field_type": "multiselect",
             "options": ["Support", "Moderation", "Entwicklung", "Grafik", "Beta-Betreuung"]},
            {"label": "Erzähl uns etwas über dich", "field_type": "textarea"},
            {"label": "Welche Erfahrungen bringst du mit?", "field_type": "textarea"},
            {"label": "Wie viel Zeit kannst du pro Woche investieren?", "field_type": "text", "max_length": 100},
        ],
    },
]


def seed_default_types(session: Session) -> None:
    if session.scalar(select(func.count(ApplicationType.id))):
        return
    for spec in DEFAULT_TYPES:
        t = ApplicationType(key=spec["key"], name=spec["name"], description=spec["description"],
                            create_beta_tester=spec.get("create_beta_tester", False),
                            accept_message=spec["accept_message"], reject_message=spec["reject_message"])
        t.questions = [ApplicationQuestion(label=q["label"], field_type=q["field_type"], options=q.get("options"),
                                           max_length=q.get("max_length", 1000), position=i, required=True)
                       for i, q in enumerate(spec["questions"])]
        session.add(t)
    session.flush()


# ---------------------------------------------------------------------------
def serialize_type(t: ApplicationType) -> dict:
    return {
        "id": t.id, "key": t.key, "name": t.name, "description": t.description, "active": t.active,
        "accept_role_id": t.accept_role_id, "auto_assign_role": t.auto_assign_role,
        "create_beta_tester": t.create_beta_tester, "accept_message": t.accept_message,
        "reject_message": t.reject_message, "retention_days": t.retention_days,
        "questions": [{"id": q.id, "label": q.label, "field_type": q.field_type, "required": q.required,
                       "options": q.options or [], "max_length": q.max_length, "position": q.position}
                      for q in t.questions],
    }


def save_type(session: Session, actor: Actor, data: dict, type_id: Optional[int] = None) -> ApplicationType:
    name = (data.get("name") or "").strip()
    key = (data.get("key") or "").strip().lower()
    if not name:
        raise Invalid("Name der Bewerbungsart fehlt.")
    if not re.match(r"^[a-z0-9_-]{2,40}$", key):
        raise Invalid("Der Schlüssel darf nur a–z, 0–9, _ und - enthalten (2–40 Zeichen).")
    dup = session.scalar(select(ApplicationType).where(ApplicationType.key == key, ApplicationType.id != (type_id or 0)))
    if dup:
        raise Conflict("Eine Bewerbungsart mit diesem Schlüssel existiert bereits.")
    questions = data.get("questions") or []
    if not questions:
        raise Invalid("Mindestens eine Frage ist erforderlich.")
    if len(questions) > 25:
        raise Invalid("Maximal 25 Fragen pro Formular.")
    role_id = data.get("accept_role_id") or None
    if role_id and not (str(role_id).isdigit() and 15 <= len(str(role_id)) <= 21):
        raise Invalid("Die Rollen-ID muss eine Discord-ID sein (15–21 Ziffern).")
    if type_id:
        t = session.get(ApplicationType, type_id)
        if t is None:
            raise NotFound("Bewerbungsart nicht gefunden.")
        action = "applications.type_updated"
    else:
        t = ApplicationType(key=key, name=name)
        session.add(t)
        action = "applications.type_created"
    t.key, t.name = key, name[:80]
    t.description = data.get("description") or None
    t.active = bool(data.get("active", True))
    t.accept_role_id = role_id
    t.auto_assign_role = bool(data.get("auto_assign_role", False))
    t.create_beta_tester = bool(data.get("create_beta_tester", False))
    t.accept_message = data.get("accept_message") or None
    t.reject_message = data.get("reject_message") or None
    t.retention_days = max(1, int(data.get("retention_days") or 180))

    existing = {q.id: q for q in t.questions}
    new_list: list[ApplicationQuestion] = []
    for i, q in enumerate(questions):
        label = (q.get("label") or "").strip()
        ftype = q.get("field_type") or "text"
        if not label:
            raise Invalid(f"Frage {i + 1}: Text fehlt.")
        if len(label) > 45 and ftype in ("text", "textarea"):
            pass  # Discord-Labels werden im Bot automatisch gekürzt, der volle Text wird gespeichert
        if ftype not in FIELD_TYPES:
            raise Invalid(f"Frage {i + 1}: ungültiger Feldtyp.")
        opts = [str(o).strip()[:100] for o in (q.get("options") or []) if str(o).strip()]
        if ftype in ("select", "multiselect") and len(opts) < 2:
            raise Invalid(f"Frage {i + 1}: Auswahlfelder benötigen mindestens zwei Optionen.")
        if len(opts) > 25:
            raise Invalid(f"Frage {i + 1}: maximal 25 Optionen.")
        obj = existing.get(q.get("id")) if q.get("id") else None
        if obj is None:
            obj = ApplicationQuestion()
        obj.label = label[:200]
        obj.field_type = ftype
        obj.required = bool(q.get("required", True))
        obj.options = opts if ftype in ("select", "multiselect") else None
        obj.max_length = max(1, min(int(q.get("max_length") or (4000 if ftype == "textarea" else 300)), 4000))
        obj.position = i
        new_list.append(obj)
    t.questions = new_list
    session.flush()
    audit(session, actor, action, "application_type", t.id, {"key": key, "questions": len(new_list)})
    emit(session, "application_types")
    enqueue(session, "applications.refresh_panel", {}, idempotency_key="applications-panel-refresh")
    return t


def delete_type(session: Session, actor: Actor, type_id: int) -> None:
    t = session.get(ApplicationType, type_id)
    if t is None:
        raise NotFound("Bewerbungsart nicht gefunden.")
    used = session.scalar(select(func.count(Application.id)).where(Application.type_id == t.id))
    if used:
        t.active = False
        audit(session, actor, "applications.type_deactivated", "application_type", t.id, {"key": t.key})
    else:
        audit(session, actor, "applications.type_deleted", "application_type", t.id, {"key": t.key})
        session.delete(t)
    emit(session, "application_types")
    enqueue(session, "applications.refresh_panel", {}, idempotency_key="applications-panel-refresh")


# ---------------------------------------------------------------------------
def validate_answers(t: ApplicationType, answers: dict[Any, Any]) -> list[tuple[ApplicationQuestion, str]]:
    out = []
    for q in t.questions:
        raw = answers.get(q.id, answers.get(str(q.id)))
        if q.field_type == "multiselect":
            values = raw if isinstance(raw, list) else ([raw] if raw else [])
            values = [str(v) for v in values if str(v)]
            bad = [v for v in values if v not in (q.options or [])]
            if bad:
                raise Invalid(f"„{q.label}“: ungültige Auswahl.")
            value = ", ".join(values)
        elif q.field_type == "select":
            value = str(raw or "")
            if value and value not in (q.options or []):
                raise Invalid(f"„{q.label}“: ungültige Auswahl.")
        elif q.field_type == "yesno":
            if isinstance(raw, bool):
                value = "Ja" if raw else "Nein"
            else:
                value = {"ja": "Ja", "yes": "Ja", "true": "Ja", "nein": "Nein", "no": "Nein", "false": "Nein"}.get(
                    str(raw or "").strip().lower(), "")
        else:
            value = str(raw or "").strip()
            if len(value) > q.max_length:
                raise Invalid(f"„{q.label}“: maximal {q.max_length} Zeichen.")
        if q.required and not value:
            raise Invalid(f"„{q.label}“ ist ein Pflichtfeld.")
        out.append((q, value))
    return out


def submit(session: Session, type_id: int, applicant_discord_id: str, applicant_name: str,
           answers: dict[Any, Any], avatar_url: Optional[str] = None) -> Application:
    t = session.get(ApplicationType, type_id)
    if t is None or not t.active:
        raise Invalid("Diese Bewerbungsart ist derzeit nicht verfügbar.")
    user = upsert_discord_user(session, applicant_discord_id, username=applicant_name, avatar_url=avatar_url)
    session.flush()
    open_app = session.scalar(select(Application).where(
        Application.type_id == t.id, Application.applicant_id == user.id, Application.status.in_(OPEN_STATUSES)))
    if open_app:
        raise Conflict("Du hast bereits eine offene Bewerbung dieser Art.")
    validated = validate_answers(t, answers)
    app = Application(type_id=t.id, applicant_id=user.id, status="new")
    app.answers = [ApplicationAnswer(question_id=q.id, question_label=q.label, value=v, position=i)
                   for i, (q, v) in enumerate(validated)]
    session.add(app)
    session.flush()
    session.add(ApplicationHistory(application_id=app.id, actor_name=applicant_name[:100], action="submitted",
                                   to_status="new"))
    actor = Actor(name=applicant_name, discord_id=str(applicant_discord_id), source="discord")
    audit(session, actor, "applications.submitted", "application", app.id, {"type": t.key})
    notify(session, "application", f"Neue {t.name}", f"von {applicant_name}", link=f"/applications/{app.id}",
           permission="applications.view")
    if settings_service.get_value(session, "notifications.new_application_ping"):
        enqueue(session, "applications.notify_team", {"application_id": app.id},
                idempotency_key=f"application-notify-{app.id}")
    emit(session, "applications", app.id)
    return app


def get_application(session: Session, app_id: int) -> Application:
    app = session.get(Application, app_id)
    if app is None:
        raise NotFound("Bewerbung nicht gefunden.")
    return app


def _history(session: Session, app: Application, actor: Actor, action: str, from_s=None, to_s=None, comment=None):
    session.add(ApplicationHistory(application_id=app.id, actor_name=actor.name[:100], action=action,
                                   from_status=from_s, to_status=to_s, comment=comment))


def set_status(session: Session, actor: Actor, app_id: int, status: str, comment: Optional[str] = None,
               message_to_applicant: Optional[str] = None) -> Application:
    if status not in STATUSES:
        raise Invalid("Ungültiger Bewerbungsstatus.")
    app = get_application(session, app_id)
    if app.anonymized_at:
        raise Conflict("Diese Bewerbung wurde bereits anonymisiert.")
    if app.status == status and status != "inquiry":
        return app
    old = app.status
    if old in ("accepted", "rejected") and status in ("accepted", "rejected"):
        raise Conflict("Über diese Bewerbung wurde bereits entschieden.")
    app.status = status
    if actor.member_id and app.reviewer_id is None:
        app.reviewer_id = actor.member_id
    if status in ("accepted", "rejected"):
        app.decided_at = utcnow()
        app.decision_reason = comment
    else:
        app.decided_at = None
    _history(session, app, actor, "status_changed", old, status, comment)
    audit(session, actor, f"applications.{status}" if status in ("accepted", "rejected") else "applications.status_changed",
          "application", app.id, {"from": old, "to": status, "type": app.type.key})
    discord_id = app.applicant.discord_id
    if status == "inquiry":
        if not message_to_applicant:
            raise Invalid("Bitte die Rückfrage an den Bewerber angeben.")
        enqueue(session, "dm.send", {"discord_id": discord_id, "title": f"Rückfrage zu deiner {app.type.name}",
                                     "content": message_to_applicant[:1800], "context": f"application:{app.id}"})
        _history(session, app, actor, "inquiry_sent", comment=message_to_applicant)
    if status in ("accepted", "rejected"):
        if settings_service.get_value(session, "notifications.dm_on_application_decision"):
            text = message_to_applicant or (app.type.accept_message if status == "accepted" else app.type.reject_message)
            if text:
                enqueue(session, "dm.send", {
                    "discord_id": discord_id, "content": text[:1800], "context": f"application:{app.id}",
                    "title": f"{app.type.name}: {STATUS_LABELS[status]}"},
                    idempotency_key=f"application-decision-{app.id}-{status}")
        if status == "accepted":
            if app.type.auto_assign_role and app.type.accept_role_id:
                enqueue(session, "member.add_role", {"discord_id": discord_id, "role_id": app.type.accept_role_id,
                                                     "reason": f"Bewerbung #{app.id} angenommen"},
                        idempotency_key=f"application-role-{app.id}")
            if app.type.create_beta_tester:
                from .beta import ensure_tester  # Zirkularimport vermeiden
                ensure_tester(session, actor, discord_id, app.applicant.username, application_id=app.id)
    emit(session, "applications", app.id)
    return app


def assign_reviewer(session: Session, actor: Actor, app_id: int, member_id: Optional[int]) -> Application:
    app = get_application(session, app_id)
    app.reviewer_id = member_id
    if app.status == "new" and member_id:
        app.status = "in_review"
        _history(session, app, actor, "status_changed", "new", "in_review")
    _history(session, app, actor, "reviewer_assigned", comment=str(member_id) if member_id else None)
    audit(session, actor, "applications.reviewer_assigned", "application", app.id, {"member_id": member_id})
    emit(session, "applications", app.id)
    return app


def add_note(session: Session, actor: Actor, app_id: int, content: str) -> ApplicationNote:
    content = (content or "").strip()
    if not content:
        raise Invalid("Die Notiz ist leer.")
    app = get_application(session, app_id)
    note = ApplicationNote(application_id=app.id, author_id=actor.member_id, author_name=actor.name,
                           content=content[:5000])
    session.add(note)
    _history(session, app, actor, "note_added")
    audit(session, actor, "applications.note_added", "application", app.id)
    emit(session, "applications", app.id)
    return note


def rate(session: Session, actor: Actor, app_id: int, score: int, comment: Optional[str]) -> ApplicationRating:
    if actor.member_id is None:
        raise Invalid("Nur Teammitglieder können bewerten.")
    if not 1 <= int(score) <= 5:
        raise Invalid("Die Bewertung muss zwischen 1 und 5 liegen.")
    app = get_application(session, app_id)
    rating = session.scalar(select(ApplicationRating).where(
        ApplicationRating.application_id == app.id, ApplicationRating.member_id == actor.member_id))
    if rating is None:
        rating = ApplicationRating(application_id=app.id, member_id=actor.member_id, score=int(score))
        session.add(rating)
    rating.score = int(score)
    rating.comment = comment or None
    rating.created_at = utcnow()
    _history(session, app, actor, "rated", comment=f"{score}/5")
    audit(session, actor, "applications.rated", "application", app.id, {"score": int(score)})
    emit(session, "applications", app.id)
    return rating


# ---------------------------------------------------------------------------
def anonymize_expired(session: Session) -> int:
    """Datenschutz: entschiedene Bewerbungen nach Ablauf der Aufbewahrungsfrist anonymisieren."""
    now = utcnow()
    anon_user = upsert_discord_user(session, ANON_DISCORD_ID, username="Anonymisiert")
    session.flush()
    count = 0
    apps = session.scalars(select(Application).where(
        Application.anonymized_at.is_(None), Application.status.in_(("accepted", "rejected")),
        Application.decided_at.is_not(None))).all()
    for app in apps:
        if app.decided_at > now - timedelta(days=app.type.retention_days):
            continue
        tester = session.scalar(select(BetaTester).where(BetaTester.application_id == app.id))
        if tester:
            tester.application_id = None
        session.execute(delete(ApplicationAnswer).where(ApplicationAnswer.application_id == app.id))
        session.execute(delete(ApplicationNote).where(ApplicationNote.application_id == app.id))
        session.execute(delete(ApplicationRating).where(ApplicationRating.application_id == app.id))
        session.execute(delete(ApplicationHistory).where(ApplicationHistory.application_id == app.id))
        app.applicant_id = anon_user.id
        app.decision_reason = None
        app.anonymized_at = now
        count += 1
    if count:
        audit(session, Actor.system("Datenschutz"), "applications.anonymized", "application", None, {"count": count})
        emit(session, "applications")
    session.flush()
    session.expire_all()
    return count


def serialize(app: Application, detail: bool = False, session: Optional[Session] = None) -> dict:
    data = {
        "id": app.id, "status": app.status,
        "type": {"id": app.type.id, "key": app.type.key, "name": app.type.name},
        "applicant": {"discord_id": app.applicant.discord_id, "username": app.applicant.username,
                      "display_name": app.applicant.display_name, "avatar_url": app.applicant.avatar_url},
        "reviewer": {"id": app.reviewer.id, "username": app.reviewer.user.username} if app.reviewer else None,
        "submitted_at": app.submitted_at.isoformat(),
        "decided_at": app.decided_at.isoformat() if app.decided_at else None,
        "decision_reason": app.decision_reason,
        "anonymized": app.anonymized_at is not None,
    }
    if detail and session is not None:
        data["answers"] = [{"question": a.question_label, "value": a.value} for a in app.answers]
        notes = session.scalars(select(ApplicationNote).where(ApplicationNote.application_id == app.id)
                                .order_by(ApplicationNote.created_at)).all()
        data["notes"] = [{"id": n.id, "author_name": n.author_name, "content": n.content,
                          "created_at": n.created_at.isoformat()} for n in notes]
        rows = session.execute(
            select(ApplicationRating, DiscordUser.username)
            .join(TeamMember, TeamMember.id == ApplicationRating.member_id)
            .join(DiscordUser, DiscordUser.id == TeamMember.user_id)
            .where(ApplicationRating.application_id == app.id)
        ).all()
        ratings = [{"member_id": r.member_id, "username": u, "score": r.score, "comment": r.comment,
                    "created_at": r.created_at.isoformat()} for r, u in rows]
        data["ratings"] = ratings
        data["average_score"] = round(sum(r["score"] for r in ratings) / len(ratings), 2) if ratings else None
        hist = session.scalars(select(ApplicationHistory).where(ApplicationHistory.application_id == app.id)
                               .order_by(ApplicationHistory.created_at, ApplicationHistory.id)).all()
        data["history"] = [{"actor_name": h.actor_name, "action": h.action, "from_status": h.from_status,
                            "to_status": h.to_status, "comment": h.comment, "created_at": h.created_at.isoformat()}
                           for h in hist]
    return data
