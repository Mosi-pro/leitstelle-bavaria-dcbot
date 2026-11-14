"""Community-Management: Verifizierung, Moderation, Auto-Moderation, Ankündigungen, Vorlagen, Umfragen."""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..core.models import (
    Announcement,
    AutoModRule,
    DiscordUser,
    MemberEvent,
    MemberSnapshot,
    MessageTemplate,
    ModerationAction,
    Poll,
    PollOption,
    PollVote,
    Warning,
    utcnow,
)
from . import settings_service
from .base import Actor, Conflict, Invalid, NotFound, audit, emit, enqueue, upsert_discord_user

MOD_ACTIONS = ("warn", "timeout", "untimeout", "kick", "ban", "unban")
MOD_LABELS = {"warn": "Verwarnung", "timeout": "Timeout", "untimeout": "Timeout aufgehoben", "kick": "Kick",
              "ban": "Bann", "unban": "Entbannung", "message_delete": "Nachricht gelöscht"}
RULE_TYPES = ("word_filter", "invite_links", "mention_spam", "caps")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
INVITE_RE = re.compile(r"(discord\.gg/|discord(?:app)?\.com/invite/)\S+", re.IGNORECASE)


def _snowflake(v) -> str:
    v = str(v or "").strip()
    if not (v.isdigit() and 15 <= len(v) <= 21):
        raise Invalid("Ungültige Discord-ID (15–21 Ziffern).")
    return v


# ---------------------------------------------------------------------------
# Mitglieder & Verifizierung
# ---------------------------------------------------------------------------
def member_joined(session: Session, discord_id: str, username: str, avatar_url: Optional[str]) -> DiscordUser:
    user = upsert_discord_user(session, discord_id, username=username, avatar_url=avatar_url)
    user.is_member = True
    user.joined_guild_at = utcnow()
    user.left_guild_at = None
    session.add(MemberEvent(discord_id=str(discord_id), event="join"))
    emit(session, "community")
    return user


def member_left(session: Session, discord_id: str) -> None:
    user = session.scalar(select(DiscordUser).where(DiscordUser.discord_id == str(discord_id)))
    if user:
        user.is_member = False
        user.left_guild_at = utcnow()
    session.add(MemberEvent(discord_id=str(discord_id), event="leave"))
    emit(session, "community")


def verify(session: Session, discord_id: str, username: str) -> tuple[DiscordUser, bool]:
    user = upsert_discord_user(session, discord_id, username=username)
    if user.verified:
        return user, False
    user.verified = True
    user.verified_at = utcnow()
    session.add(MemberEvent(discord_id=str(discord_id), event="verify"))
    audit(session, Actor(name=username, discord_id=str(discord_id), source="discord"), "community.verified",
          "discord_user", discord_id)
    emit(session, "community")
    return user, True


def record_snapshot(session: Session, member_count: int) -> None:
    today = date.today()
    snap = session.scalar(select(MemberSnapshot).where(MemberSnapshot.day == today))
    if snap is None:
        session.add(MemberSnapshot(day=today, member_count=member_count))
    else:
        snap.member_count = member_count


# ---------------------------------------------------------------------------
# Moderation
# ---------------------------------------------------------------------------
def moderate(session: Session, actor: Actor, *, target_discord_id: str, action: str, reason: str,
             duration_minutes: Optional[int] = None, target_name: Optional[str] = None,
             executed: bool = False, delete_message_days: int = 0) -> ModerationAction:
    """Legt eine Moderationsaktion an. Aus dem Panel → Ausführung durch den Bot (Outbox).

    executed=True bedeutet: die Aktion wurde bereits in Discord ausgeführt (Slash-Befehl, Auto-Moderation).
    """
    if action not in MOD_ACTIONS:
        raise Invalid("Ungültige Moderationsaktion.")
    reason = (reason or "").strip()
    if not reason:
        raise Invalid("Bitte einen Grund angeben.")
    target_discord_id = _snowflake(target_discord_id)
    if action == "timeout":
        if not duration_minutes or not 1 <= int(duration_minutes) <= 40320:
            raise Invalid("Timeout-Dauer muss zwischen 1 Minute und 28 Tagen liegen.")
    target = upsert_discord_user(session, target_discord_id, username=target_name)
    session.flush()
    expires = utcnow() + timedelta(minutes=int(duration_minutes)) if action == "timeout" else None
    ma = ModerationAction(action=action, target_id=target.id, moderator_discord_id=actor.discord_id,
                          moderator_name=actor.name[:100], reason=reason[:2000],
                          duration_minutes=int(duration_minutes) if duration_minutes else None, expires_at=expires,
                          source=actor.source if actor.source in ("panel", "discord") else "automod",
                          status="executed" if (executed or action == "warn") else "pending")
    session.add(ma)
    session.flush()
    if action == "warn":
        session.add(Warning(user_id=target.id, moderation_action_id=ma.id, reason=reason[:2000],
                            moderator_name=actor.name[:100]))
        enqueue(session, "dm.send", {"discord_id": target_discord_id, "title": "Verwarnung – Leitstelle Bavaria",
                                     "content": f"Du wurdest verwarnt.\nGrund: {reason[:1500]}",
                                     "context": f"moderation:{ma.id}"})
    elif not executed:
        enqueue(session, "moderation.execute", {"action_id": ma.id, "delete_message_days": delete_message_days},
                idempotency_key=f"moderation-{ma.id}")
    enqueue(session, "moderation.log", {"action_id": ma.id}, idempotency_key=f"moderation-log-{ma.id}")
    audit(session, actor, f"community.{action}", "discord_user", target_discord_id,
          {"reason": reason, "duration_minutes": duration_minutes, "moderation_id": ma.id})
    emit(session, "moderation", ma.id)
    if action == "warn":
        _check_warn_threshold(session, target, ma)
    return ma


def _check_warn_threshold(session: Session, target: DiscordUser, source_action: ModerationAction) -> None:
    threshold = int(settings_service.get_value(session, "community.warn_threshold_timeout") or 0)
    if threshold <= 0:
        return
    active = session.scalar(select(func.count(Warning.id)).where(Warning.user_id == target.id, Warning.active.is_(True)))
    if active and active % threshold == 0:
        moderate(session, Actor.system("Auto-Moderation"), target_discord_id=target.discord_id, action="timeout",
                 reason=f"Automatischer Timeout: {active} aktive Verwarnungen", duration_minutes=60)


def finish_moderation(session: Session, action_id: int, error: Optional[str]) -> None:
    ma = session.get(ModerationAction, action_id)
    if ma:
        ma.status = "failed" if error else "executed"
        ma.error = (error or None) and error[:300]
        emit(session, "moderation", ma.id)


def revoke_warning(session: Session, actor: Actor, warning_id: int) -> Warning:
    w = session.get(Warning, warning_id)
    if w is None:
        raise NotFound("Verwarnung nicht gefunden.")
    if not w.active:
        raise Conflict("Die Verwarnung ist bereits aufgehoben.")
    w.active = False
    w.revoked_by = actor.name[:100]
    w.revoked_at = utcnow()
    audit(session, actor, "community.warning_revoked", "warning", w.id, {"user": w.user.discord_id})
    emit(session, "moderation")
    return w


def serialize_mod(ma: ModerationAction) -> dict:
    return {"id": ma.id, "action": ma.action, "label": MOD_LABELS.get(ma.action, ma.action),
            "target": {"discord_id": ma.target.discord_id, "username": ma.target.username},
            "moderator_name": ma.moderator_name, "reason": ma.reason, "duration_minutes": ma.duration_minutes,
            "expires_at": ma.expires_at.isoformat() if ma.expires_at else None, "source": ma.source,
            "status": ma.status, "error": ma.error, "created_at": ma.created_at.isoformat()}


def serialize_warning(w: Warning) -> dict:
    return {"id": w.id, "user": {"discord_id": w.user.discord_id, "username": w.user.username},
            "reason": w.reason, "moderator_name": w.moderator_name, "active": w.active, "revoked_by": w.revoked_by,
            "revoked_at": w.revoked_at.isoformat() if w.revoked_at else None, "created_at": w.created_at.isoformat()}


def user_history(session: Session, discord_id: str) -> dict:
    user = session.scalar(select(DiscordUser).where(DiscordUser.discord_id == str(discord_id)))
    if user is None:
        raise NotFound("Für diese Discord-ID liegen keine Daten vor.")
    actions = session.scalars(select(ModerationAction).where(ModerationAction.target_id == user.id)
                              .order_by(ModerationAction.created_at.desc())).all()
    warnings = session.scalars(select(Warning).where(Warning.user_id == user.id).order_by(Warning.created_at.desc())).all()
    return {"user": {"discord_id": user.discord_id, "username": user.username, "is_member": user.is_member,
                     "verified": user.verified,
                     "joined_guild_at": user.joined_guild_at.isoformat() if user.joined_guild_at else None},
            "actions": [serialize_mod(a) for a in actions], "warnings": [serialize_warning(w) for w in warnings]}


# ---------------------------------------------------------------------------
# Auto-Moderation
# ---------------------------------------------------------------------------
def save_rule(session: Session, actor: Actor, data: dict, rule_id: Optional[int] = None) -> AutoModRule:
    name = (data.get("name") or "").strip()
    rtype = data.get("rule_type")
    if not name:
        raise Invalid("Name der Regel fehlt.")
    if rtype not in RULE_TYPES:
        raise Invalid("Ungültiger Regeltyp.")
    action = data.get("action") or "delete"
    if action not in ("delete", "warn", "timeout"):
        raise Invalid("Ungültige Aktion.")
    config = data.get("config") or {}
    if rtype == "word_filter":
        words = [w.strip().lower() for w in config.get("words", []) if str(w).strip()]
        if not words:
            raise Invalid("Der Wortfilter benötigt mindestens ein Wort.")
        config = {"words": words[:500]}
    elif rtype == "mention_spam":
        config = {"max_mentions": max(1, int(config.get("max_mentions") or 5))}
    elif rtype == "caps":
        config = {"min_length": max(5, int(config.get("min_length") or 15)),
                  "max_ratio": min(1.0, max(0.3, float(config.get("max_ratio") or 0.8)))}
    else:
        config = {}
    if rule_id:
        rule = session.get(AutoModRule, rule_id)
        if rule is None:
            raise NotFound("Regel nicht gefunden.")
    else:
        rule = AutoModRule(name=name, rule_type=rtype)
        session.add(rule)
    rule.name, rule.rule_type, rule.config, rule.action = name[:80], rtype, config, action
    rule.timeout_minutes = max(1, int(data.get("timeout_minutes") or 10))
    rule.enabled = bool(data.get("enabled", True))
    session.flush()
    audit(session, actor, "community.automod_rule_saved", "automod_rule", rule.id, {"name": rule.name, "type": rtype})
    emit(session, "automod")
    return rule


def delete_rule(session: Session, actor: Actor, rule_id: int) -> None:
    rule = session.get(AutoModRule, rule_id)
    if rule is None:
        raise NotFound("Regel nicht gefunden.")
    audit(session, actor, "community.automod_rule_deleted", "automod_rule", rule.id, {"name": rule.name})
    session.delete(rule)
    emit(session, "automod")


def evaluate_rules(rules: list[dict], content: str, mention_count: int) -> Optional[dict]:
    """Prüft eine Nachricht gegen die Regeln. Rückgabe: erste verletzte Regel oder None. (rein funktional)"""
    text = content or ""
    low = text.lower()
    for r in rules:
        if not r.get("enabled", True):
            continue
        cfg = r.get("config") or {}
        t = r["rule_type"]
        hit = False
        if t == "word_filter":
            hit = any(re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", low) for w in cfg.get("words", []))
        elif t == "invite_links":
            hit = bool(INVITE_RE.search(text))
        elif t == "mention_spam":
            hit = mention_count > int(cfg.get("max_mentions", 5))
        elif t == "caps":
            letters = [c for c in text if c.isalpha()]
            if len(letters) >= int(cfg.get("min_length", 15)):
                ratio = sum(1 for c in letters if c.isupper()) / len(letters)
                hit = ratio > float(cfg.get("max_ratio", 0.8))
        if hit:
            return r
    return None


def serialize_rule(r: AutoModRule) -> dict:
    return {"id": r.id, "name": r.name, "rule_type": r.rule_type, "config": r.config, "action": r.action,
            "timeout_minutes": r.timeout_minutes, "enabled": r.enabled}


# ---------------------------------------------------------------------------
# Ankündigungen & Vorlagen
# ---------------------------------------------------------------------------
def _validate_message(data: dict) -> None:
    if not (data.get("content") or "").strip():
        raise Invalid("Der Nachrichtentext ist leer.")
    if len(data.get("content") or "") > (4000 if data.get("as_embed", True) else 2000):
        raise Invalid("Der Nachrichtentext ist zu lang.")
    if data.get("color") and not COLOR_RE.match(data["color"]):
        raise Invalid("Farbe im Format #RRGGBB angeben.")


def save_template(session: Session, actor: Actor, data: dict, template_id: Optional[int] = None) -> MessageTemplate:
    name = (data.get("name") or "").strip()
    if not name:
        raise Invalid("Name der Vorlage fehlt.")
    _validate_message(data)
    if session.scalar(select(MessageTemplate).where(MessageTemplate.name == name,
                                                    MessageTemplate.id != (template_id or 0))):
        raise Conflict("Eine Vorlage mit diesem Namen existiert bereits.")
    if template_id:
        tpl = session.get(MessageTemplate, template_id)
        if tpl is None:
            raise NotFound("Vorlage nicht gefunden.")
    else:
        tpl = MessageTemplate(name=name, content="")
        session.add(tpl)
    tpl.name, tpl.title, tpl.content = name[:80], data.get("title") or None, data["content"]
    tpl.color, tpl.as_embed = data.get("color") or None, bool(data.get("as_embed", True))
    session.flush()
    audit(session, actor, "community.template_saved", "message_template", tpl.id, {"name": tpl.name})
    emit(session, "announcements")
    return tpl


def delete_template(session: Session, actor: Actor, template_id: int) -> None:
    tpl = session.get(MessageTemplate, template_id)
    if tpl is None:
        raise NotFound("Vorlage nicht gefunden.")
    audit(session, actor, "community.template_deleted", "message_template", tpl.id, {"name": tpl.name})
    session.delete(tpl)
    emit(session, "announcements")


def create_announcement(session: Session, actor: Actor, data: dict, publish: bool) -> Announcement:
    _validate_message(data)
    channel = data.get("channel_id") or settings_service.get_value(session, "channels.announcements")
    if not channel:
        raise Invalid("Kein Zielchannel angegeben und kein Ankündigungs-Channel konfiguriert.")
    channel = _snowflake(channel)
    role = data.get("mention_role_id") or None
    if role:
        role = _snowflake(role)
    ann = Announcement(channel_id=channel, title=(data.get("title") or None), content=data["content"],
                       as_embed=bool(data.get("as_embed", True)), color=data.get("color") or None,
                       footer=data.get("footer") or None, image_url=data.get("image_url") or None,
                       mention_role_id=role, status="draft", created_by=actor.name)
    session.add(ann)
    session.flush()
    audit(session, actor, "community.announcement_created", "announcement", ann.id, {"channel_id": channel})
    if publish:
        publish_announcement(session, actor, ann.id)
    emit(session, "announcements")
    return ann


def update_announcement(session: Session, actor: Actor, ann_id: int, data: dict) -> Announcement:
    ann = session.get(Announcement, ann_id)
    if ann is None:
        raise NotFound("Ankündigung nicht gefunden.")
    if ann.status not in ("draft", "failed"):
        raise Conflict("Nur Entwürfe können bearbeitet werden.")
    _validate_message(data)
    ann.channel_id = _snowflake(data.get("channel_id") or ann.channel_id)
    ann.title, ann.content = data.get("title") or None, data["content"]
    ann.as_embed, ann.color = bool(data.get("as_embed", True)), data.get("color") or None
    ann.footer, ann.image_url = data.get("footer") or None, data.get("image_url") or None
    ann.mention_role_id = _snowflake(data["mention_role_id"]) if data.get("mention_role_id") else None
    audit(session, actor, "community.announcement_updated", "announcement", ann.id)
    emit(session, "announcements")
    return ann


def publish_announcement(session: Session, actor: Actor, ann_id: int) -> Announcement:
    ann = session.get(Announcement, ann_id)
    if ann is None:
        raise NotFound("Ankündigung nicht gefunden.")
    if ann.status in ("queued", "sent"):
        raise Conflict("Die Ankündigung wurde bereits veröffentlicht.")
    ann.status = "queued"
    ann.error = None
    enqueue(session, "announcement.publish", {"announcement_id": ann.id}, idempotency_key=f"announcement-{ann.id}")
    audit(session, actor, "community.announcement_published", "announcement", ann.id, {"channel_id": ann.channel_id})
    emit(session, "announcements")
    return ann


def delete_announcement(session: Session, actor: Actor, ann_id: int) -> None:
    ann = session.get(Announcement, ann_id)
    if ann is None:
        raise NotFound("Ankündigung nicht gefunden.")
    if ann.status == "queued":
        raise Conflict("Die Ankündigung wird gerade veröffentlicht.")
    if ann.status == "sent" and ann.message_id:
        enqueue(session, "message.delete", {"channel_id": ann.channel_id, "message_id": ann.message_id})
    audit(session, actor, "community.announcement_deleted", "announcement", ann.id)
    session.delete(ann)
    emit(session, "announcements")


def serialize_announcement(a: Announcement) -> dict:
    return {"id": a.id, "channel_id": a.channel_id, "title": a.title, "content": a.content, "as_embed": a.as_embed,
            "color": a.color, "footer": a.footer, "image_url": a.image_url, "mention_role_id": a.mention_role_id,
            "status": a.status, "message_id": a.message_id, "error": a.error, "created_by": a.created_by,
            "created_at": a.created_at.isoformat(), "sent_at": a.sent_at.isoformat() if a.sent_at else None}


# ---------------------------------------------------------------------------
# Umfragen
# ---------------------------------------------------------------------------
def create_poll(session: Session, actor: Actor, data: dict, publish: bool) -> Poll:
    question = (data.get("question") or "").strip()
    options = [str(o).strip()[:80] for o in data.get("options") or [] if str(o).strip()]
    if not question:
        raise Invalid("Die Frage fehlt.")
    if not 2 <= len(options) <= 20:
        raise Invalid("Eine Umfrage benötigt 2 bis 20 Antwortmöglichkeiten.")
    if len(set(options)) != len(options):
        raise Invalid("Antwortmöglichkeiten müssen eindeutig sein.")
    channel = data.get("channel_id") or settings_service.get_value(session, "channels.announcements")
    if not channel:
        raise Invalid("Kein Zielchannel angegeben.")
    ends_at = None
    if data.get("duration_hours"):
        hours = int(data["duration_hours"])
        if not 1 <= hours <= 24 * 30:
            raise Invalid("Laufzeit zwischen 1 Stunde und 30 Tagen.")
        ends_at = utcnow() + timedelta(hours=hours)
    poll = Poll(question=question[:300], description=data.get("description") or None, channel_id=_snowflake(channel),
                multiple_choice=bool(data.get("multiple_choice")), status="draft", ends_at=ends_at,
                created_by=actor.name)
    poll.options = [PollOption(label=o, position=i) for i, o in enumerate(options)]
    session.add(poll)
    session.flush()
    audit(session, actor, "community.poll_created", "poll", poll.id, {"question": poll.question})
    if publish:
        publish_poll(session, actor, poll.id)
    emit(session, "polls")
    return poll


def publish_poll(session: Session, actor: Actor, poll_id: int) -> Poll:
    poll = session.get(Poll, poll_id)
    if poll is None:
        raise NotFound("Umfrage nicht gefunden.")
    if poll.status not in ("draft", "failed"):
        raise Conflict("Die Umfrage wurde bereits veröffentlicht.")
    poll.status = "queued"
    enqueue(session, "poll.publish", {"poll_id": poll.id}, idempotency_key=f"poll-publish-{poll.id}")
    audit(session, actor, "community.poll_published", "poll", poll.id)
    emit(session, "polls")
    return poll


def vote(session: Session, poll_id: int, option_id: int, voter_discord_id: str) -> tuple[bool, str]:
    """Stimmabgabe (Umschalten). Rückgabe (erfolgreich, Meldung)."""
    poll = session.get(Poll, poll_id)
    if poll is None or poll.status != "open":
        return False, "Diese Umfrage ist nicht mehr geöffnet."
    if poll.ends_at and poll.ends_at <= utcnow():
        return False, "Diese Umfrage ist bereits beendet."
    if option_id not in {o.id for o in poll.options}:
        return False, "Ungültige Antwort."
    existing = session.scalar(select(PollVote).where(PollVote.poll_id == poll.id, PollVote.option_id == option_id,
                                                     PollVote.voter_discord_id == str(voter_discord_id)))
    label = next(o.label for o in poll.options if o.id == option_id)
    if existing:
        session.delete(existing)
        msg = f"Deine Stimme für „{label}“ wurde zurückgenommen."
    else:
        if not poll.multiple_choice:
            session.execute(delete(PollVote).where(PollVote.poll_id == poll.id,
                                                   PollVote.voter_discord_id == str(voter_discord_id)))
        session.add(PollVote(poll_id=poll.id, option_id=option_id, voter_discord_id=str(voter_discord_id)))
        msg = f"Deine Stimme für „{label}“ wurde gespeichert."
    session.flush()
    emit(session, "polls", poll.id)
    return True, msg


def close_poll(session: Session, actor: Actor, poll_id: int) -> Poll:
    poll = session.get(Poll, poll_id)
    if poll is None:
        raise NotFound("Umfrage nicht gefunden.")
    if poll.status != "open":
        raise Conflict("Die Umfrage ist nicht geöffnet.")
    poll.status = "closed"
    poll.ends_at = min(poll.ends_at, utcnow()) if poll.ends_at else utcnow()
    enqueue(session, "poll.update", {"poll_id": poll.id}, idempotency_key=f"poll-update-{poll.id}")
    audit(session, actor, "community.poll_closed", "poll", poll.id)
    emit(session, "polls", poll.id)
    return poll


def close_expired_polls(session: Session) -> int:
    n = 0
    for poll in session.scalars(select(Poll).where(Poll.status == "open", Poll.ends_at.is_not(None),
                                                   Poll.ends_at <= utcnow())):
        close_poll(session, Actor.system(), poll.id)
        n += 1
    return n


def poll_results(session: Session, poll: Poll) -> dict:
    counts = dict(session.execute(select(PollVote.option_id, func.count(PollVote.id)).where(PollVote.poll_id == poll.id)
                                  .group_by(PollVote.option_id)).all())
    voters = session.scalar(select(func.count(func.distinct(PollVote.voter_discord_id))).where(PollVote.poll_id == poll.id))
    total = sum(counts.values())
    return {
        "total_votes": total, "voters": voters or 0,
        "options": [{"id": o.id, "label": o.label, "votes": counts.get(o.id, 0),
                     "percent": round(100 * counts.get(o.id, 0) / total, 1) if total else 0.0} for o in poll.options],
    }


def serialize_poll(session: Session, p: Poll) -> dict:
    return {"id": p.id, "question": p.question, "description": p.description, "channel_id": p.channel_id,
            "multiple_choice": p.multiple_choice, "status": p.status,
            "ends_at": p.ends_at.isoformat() if p.ends_at else None, "message_id": p.message_id,
            "created_by": p.created_by, "created_at": p.created_at.isoformat(), "results": poll_results(session, p)}
