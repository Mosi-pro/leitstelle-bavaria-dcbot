"""WebSocket-Live-Aktualisierung.

Bot und Backend sind getrennte Prozesse; Änderungen werden in der Tabelle change_events abgelegt.
Ein Broadcaster liest neue Einträge (monoton steigende ID) und verteilt die betroffenen Themen an
alle verbundenen Panel-Clients. Bei Verbindungsabbruch lädt der Client nach dem Reconnect neu.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import func, select

from ..core.db import session_scope
from ..core.models import ChangeEvent
from .deps import SESSION_COOKIE, load_principal

log = logging.getLogger("lbc.ws")
router = APIRouter()


class Hub:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self.last_id: Optional[int] = None

    async def broadcast(self, message: dict) -> None:
        dead = []
        data = json.dumps(message)
        for ws in list(self.clients):
            try:
                await ws.send_text(data)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    def _poll(self) -> tuple[Optional[int], list[str]]:
        with session_scope() as s:
            if self.last_id is None:
                return s.scalar(select(func.max(ChangeEvent.id))) or 0, []
            rows = s.execute(select(ChangeEvent.id, ChangeEvent.topic).where(ChangeEvent.id > self.last_id)
                             .order_by(ChangeEvent.id).limit(1000)).all()
            if not rows:
                return self.last_id, []
            return rows[-1][0], sorted({r[1] for r in rows})

    async def run(self, stop: asyncio.Event, interval: float = 1.5) -> None:
        while not stop.is_set():
            try:
                last, topics = await asyncio.to_thread(self._poll)
                self.last_id = last
                if topics and self.clients:
                    await self.broadcast({"type": "change", "topics": topics, "last_id": last})
            except Exception as exc:  # noqa: BLE001
                log.warning("WebSocket-Broadcaster: %s", exc)
                await self.broadcast({"type": "status", "database": "error"})
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass


hub = Hub()


def _origin_ok(ws: WebSocket) -> bool:
    origin = ws.headers.get("origin")
    if not origin:
        return True
    host = ws.headers.get("x-forwarded-host") or ws.headers.get("host") or ""
    return urlparse(origin).netloc == host


@router.websocket("/api/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    token = ws.cookies.get(SESSION_COOKIE)
    if not _origin_ok(ws) or not token:
        await ws.close(code=4401)
        return
    principal = await asyncio.to_thread(load_principal, token)
    if principal is None:
        await ws.close(code=4401)
        return
    await ws.accept()
    hub.clients.add(ws)
    await ws.send_text(json.dumps({"type": "hello", "last_id": hub.last_id}))
    try:
        while True:
            try:
                msg = await asyncio.wait_for(ws.receive_text(), timeout=60)
                if msg == "ping":
                    await ws.send_text(json.dumps({"type": "pong"}))
            except asyncio.TimeoutError:
                # Sitzung regelmäßig erneut prüfen (Logout, Sperre, Ablauf)
                if await asyncio.to_thread(load_principal, token) is None:
                    await ws.close(code=4401)
                    break
    except WebSocketDisconnect:
        pass
    finally:
        hub.clients.discard(ws)
