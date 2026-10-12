"""Application wiring."""

from __future__ import annotations

import logging
import os
import random
import threading
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.auth import ADMIN_COOKIE, LoginGuard, admin_cookie_valid, load_secret
from app.routes_admin import router as admin_router
from app.routes_child import router as child_router
from app.store import Store
from app.ui import ROOT, message

log = logging.getLogger("spellbot")
STATIC = ROOT / "static"


def create_app(
    *,
    data_dir: Path,
    admin_password: str,
    voice,
    now=None,
    zone=None,
    rng_factory=None,
    secure_cookies: bool = False,
    warm_voice: bool = False,
) -> FastAPI:
    if len(admin_password) < 4:
        raise ValueError("ADMIN_PASSWORD must be at least 4 characters.")
    data_dir.mkdir(parents=True, exist_ok=True)
    app = FastAPI(title="spellbot")
    app.state.store = Store(data_dir / "spellbot.sqlite")
    app.state.voice = voice
    app.state.password = admin_password
    app.state.secret = load_secret(data_dir / "secret")
    app.state.now = now or (lambda: datetime.now(timezone.utc))
    app.state.zone = zone or _default_zone()
    app.state.rng_factory = rng_factory or (lambda: random.Random())
    app.state.secure_cookies = secure_cookies
    app.state.login_guard = LoginGuard()

    @app.middleware("http")
    async def admin_gate(request: Request, call_next):
        path = request.url.path
        if path == "/admin" or path.startswith("/admin/"):
            if path != "/admin/login" and not _admin_ok(request):
                return RedirectResponse("/admin/login", status_code=303)
        return await call_next(request)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self'; style-src 'self'; "
            "script-src 'self'; media-src 'self'; base-uri 'self'; form-action 'self'"
        )
        return response

    app.include_router(child_router)
    app.include_router(admin_router)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return FileResponse(STATIC / "favicon.svg", media_type="image/svg+xml")

    @app.exception_handler(404)
    async def not_found(request: Request, _exc: HTTPException):
        return message(request, "That page is not here.", "Go back to the start.", status_code=404)

    if warm_voice:
        threading.Thread(target=voice.warm, name="spellbot-voice", daemon=True).start()
    return app


def _admin_ok(request: Request) -> bool:
    return admin_cookie_valid(
        request.app.state.secret,
        request.app.state.password,
        request.cookies.get(ADMIN_COOKIE, ""),
        request.app.state.now().timestamp(),
    )


def _default_zone():
    try:
        return ZoneInfo(os.environ.get("TZ", "Europe/London"))
    except Exception:
        log.warning("Could not load the time zone. Using UTC.")
        return timezone.utc
