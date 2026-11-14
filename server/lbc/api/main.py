"""FastAPI-Anwendung (Backend des Webpanels)."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from ..core.config import get_settings
from ..core.crypto import SecretKeyMissing
from ..core.db import ping, session_scope
from ..services.applications import seed_default_types
from ..services.automations import scheduler_loop
from ..services.base import ServiceError
from ..services.team import bootstrap
from . import auth, routes_bugs, routes_community, routes_people, routes_system, routes_tickets, ws

log = logging.getLogger("lbc.api")


def initialize() -> None:
    """Prüft Konfiguration und Datenbank und legt Stammdaten an. Bricht mit klarer Meldung ab."""
    settings = get_settings()
    if not settings.app_secret_key or len(settings.app_secret_key) < 32:
        raise SecretKeyMissing("APP_SECRET_KEY fehlt oder ist zu kurz – bitte ./install.sh ausführen.")
    with session_scope() as s:
        bootstrap(s, settings.owner_discord_id or None)
        seed_default_types(s)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=get_settings().log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    await asyncio.to_thread(initialize)
    stop = asyncio.Event()
    tasks = [asyncio.create_task(ws.hub.run(stop))]
    if get_settings().run_scheduler:
        tasks.append(asyncio.create_task(scheduler_loop(stop)))
    log.info("Leitstelle Bavaria Control API gestartet")
    try:
        yield
    finally:
        stop.set()
        for t in tasks:
            t.cancel()


def create_app(with_lifespan: bool = True) -> FastAPI:
    app = FastAPI(title="Leitstelle Bavaria Control", version="1.0.0", lifespan=lifespan if with_lifespan else None,
                  docs_url=None, redoc_url=None, openapi_url="/api/openapi.json")

    @app.exception_handler(ServiceError)
    async def service_error(_req: Request, exc: ServiceError):
        return JSONResponse({"detail": exc.message}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_req: Request, exc: RequestValidationError):
        return JSONResponse({"detail": "Ungültige Eingabe.", "errors": exc.errors()[:10]}, status_code=422)

    @app.exception_handler(OperationalError)
    async def db_error(_req: Request, exc: OperationalError):
        log.error("Datenbankfehler: %s", exc)
        return JSONResponse({"detail": "Die STRATO-Datenbank ist derzeit nicht erreichbar. Bitte später erneut versuchen."},
                            status_code=503)

    @app.exception_handler(SQLAlchemyError)
    async def sa_error(_req: Request, exc: SQLAlchemyError):
        log.exception("Datenbankfehler", exc_info=exc)
        return JSONResponse({"detail": "Datenbankfehler. Die Aktion wurde nicht gespeichert."}, status_code=500)

    @app.exception_handler(ValueError)
    async def value_error(_req: Request, exc: ValueError):
        return JSONResponse({"detail": f"Ungültiger Wert: {exc}"}, status_code=422)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.get("/api/health")
    async def health():
        ok = await asyncio.to_thread(ping)
        if not ok:
            raise HTTPException(503, "Datenbank nicht erreichbar")
        return {"status": "ok"}

    for r in (auth.router, routes_tickets.router, routes_bugs.router, routes_people.router, routes_community.router,
              routes_system.router, ws.router):
        app.include_router(r)
    return app


app = create_app()
