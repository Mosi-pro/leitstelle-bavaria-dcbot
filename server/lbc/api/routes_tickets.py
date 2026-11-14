from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy import or_, select

from ..core.db import session_scope
from ..core.models import DiscordUser, Ticket, TicketMessage, TicketNote, TicketTranscript
from ..services import tickets as svc
from ..services.base import NotFound
from .deps import Principal, paginate, require

router = APIRouter(prefix="/api/tickets", tags=["tickets"])


@router.get("")
def list_tickets(status: Optional[str] = None, category_id: Optional[int] = None, assignee: Optional[str] = None,
                 priority: Optional[str] = None, q: Optional[str] = None, page: int = 1, page_size: int = 50,
                 p: Principal = Depends(require("tickets.view"))) -> dict:
    with session_scope() as s:
        query = select(Ticket)
        if status == "active":
            query = query.where(Ticket.status != "closed")
        elif status:
            query = query.where(Ticket.status == status)
        if category_id:
            query = query.where(Ticket.category_id == category_id)
        if priority:
            query = query.where(Ticket.priority == priority)
        if assignee == "me":
            query = query.where(Ticket.assignee_id == p.member_id)
        elif assignee == "none":
            query = query.where(Ticket.assignee_id.is_(None))
        elif assignee and assignee.isdigit():
            query = query.where(Ticket.assignee_id == int(assignee))
        if q:
            like = f"%{q}%"
            conds = [Ticket.subject.like(like), Ticket.creator.has(DiscordUser.username.like(like))]
            if q.lstrip("#").isdigit():
                conds.append(Ticket.number == int(q.lstrip("#")))
            query = query.where(or_(*conds))
        query = query.order_by(Ticket.status == "closed", Ticket.last_activity_at.desc())
        return paginate(s, query, page, page_size, svc.serialize)


@router.get("/categories")
def categories(p: Principal = Depends(require("tickets.view"))) -> list[dict]:
    with session_scope() as s:
        return [svc.serialize_category(c) for c in svc.list_categories(s)]


@router.post("/categories")
def create_category(data: dict = Body(...), p: Principal = Depends(require("tickets.configure"))) -> dict:
    with session_scope() as s:
        return svc.serialize_category(svc.save_category(s, p.actor(), data))


@router.put("/categories/{cid}")
def update_category(cid: int, data: dict = Body(...), p: Principal = Depends(require("tickets.configure"))) -> dict:
    with session_scope() as s:
        return svc.serialize_category(svc.save_category(s, p.actor(), data, cid))


@router.delete("/categories/{cid}")
def delete_category(cid: int, p: Principal = Depends(require("tickets.configure"))) -> dict:
    with session_scope() as s:
        svc.delete_category(s, p.actor(), cid)
    return {"ok": True}


@router.get("/{tid}")
def get_ticket(tid: int, p: Principal = Depends(require("tickets.view"))) -> dict:
    with session_scope() as s:
        t = svc.get_ticket(s, tid)
        data = svc.serialize(t, detail=True)
        data["messages"] = [svc.serialize_message(m) for m in s.scalars(
            select(TicketMessage).where(TicketMessage.ticket_id == t.id).order_by(TicketMessage.created_at, TicketMessage.id))]
        # Interne Notizen nur für berechtigte Teammitglieder (tickets.view ist bereits geprüft)
        data["notes"] = [svc.serialize_note(n) for n in s.scalars(
            select(TicketNote).where(TicketNote.ticket_id == t.id).order_by(TicketNote.created_at))]
        data["transcripts"] = [{"id": tr.id, "message_count": tr.message_count, "created_at": tr.created_at.isoformat()}
                               for tr in s.scalars(select(TicketTranscript).where(TicketTranscript.ticket_id == t.id)
                                                   .order_by(TicketTranscript.created_at.desc()))]
        return data


@router.post("/{tid}/claim")
def claim(tid: int, p: Principal = Depends(require("tickets.assign"))) -> dict:
    with session_scope() as s:
        return svc.serialize(svc.claim(s, p.actor(), tid, p.member_id))


@router.post("/{tid}/assign")
def assign(tid: int, data: dict = Body(...), p: Principal = Depends(require("tickets.assign"))) -> dict:
    with session_scope() as s:
        return svc.serialize(svc.assign(s, p.actor(), tid, data.get("member_id")))


@router.post("/{tid}/status")
def set_status(tid: int, data: dict = Body(...), p: Principal = Depends(require("tickets.manage"))) -> dict:
    with session_scope() as s:
        if data.get("status") == "closed":
            return svc.serialize(svc.close(s, p.actor(), tid, data.get("reason")))
        return svc.serialize(svc.set_status(s, p.actor(), tid, data.get("status", "")))


@router.post("/{tid}/priority")
def set_priority(tid: int, data: dict = Body(...), p: Principal = Depends(require("tickets.manage"))) -> dict:
    with session_scope() as s:
        return svc.serialize(svc.set_priority(s, p.actor(), tid, data.get("priority", "")))


@router.post("/{tid}/notes")
def add_note(tid: int, data: dict = Body(...), p: Principal = Depends(require("tickets.manage"))) -> dict:
    with session_scope() as s:
        return svc.serialize_note(svc.add_note(s, p.actor(), tid, data.get("content", "")))


@router.post("/{tid}/reply")
def reply(tid: int, data: dict = Body(...), p: Principal = Depends(require("tickets.manage"))) -> dict:
    with session_scope() as s:
        svc.reply_from_panel(s, p.actor(), tid, data.get("content", ""))
    return {"ok": True}


@router.post("/{tid}/reopen")
def reopen(tid: int, p: Principal = Depends(require("tickets.manage"))) -> dict:
    with session_scope() as s:
        return svc.serialize(svc.reopen(s, p.actor(), tid))


@router.get("/{tid}/transcripts/{trid}", response_class=PlainTextResponse)
def transcript(tid: int, trid: int, p: Principal = Depends(require("tickets.view"))) -> PlainTextResponse:
    with session_scope() as s:
        tr = s.get(TicketTranscript, trid)
        if tr is None or tr.ticket_id != tid:
            raise NotFound("Transkript nicht gefunden.")
        t = svc.get_ticket(s, tid)
        return PlainTextResponse(tr.content, headers={
            "Content-Disposition": f'attachment; filename="ticket-{t.number:04d}-transkript-{tr.id}.txt"'})


