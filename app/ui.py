"""Shared page rendering."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.auth import CSRF_COOKIE, csrf_matches, new_csrf_token, valid_csrf_token

ROOT = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(ROOT / "templates"))

_NOTICES = {
    "saved": "Saved.",
    "child-added": "Child added.",
    "list-added": "List added.",
    "deleted": "Deleted.",
}


def json_for_script(data: object) -> str:
    return json.dumps(data, ensure_ascii=True).replace("<", "\\u003c")


def format_when(moment: datetime, zone) -> str:
    local = moment.astimezone(zone)
    return f"{local.day} {local.strftime('%b %H:%M')}"


def csrf_token(request: Request) -> str:
    existing = request.cookies.get(CSRF_COOKIE)
    if valid_csrf_token(existing):
        return existing or ""
    return new_csrf_token()


def notice_text(request: Request) -> str | None:
    code = request.query_params.get("notice", "")
    if code == "words":
        try:
            added = int(request.query_params.get("added", "0"))
            updated = int(request.query_params.get("updated", "0"))
        except ValueError:
            return None
        added = max(0, min(added, 10000))
        updated = max(0, min(updated, 10000))
        if added == 0 and updated == 0:
            return "No new words in that paste."
        return f"Added {added} words. Updated {updated}."
    return _NOTICES.get(code)


def render(request: Request, name: str, context: dict | None = None, status_code: int = 200):
    payload = dict(context or {})
    token = csrf_token(request)
    payload["request"] = request
    payload["csrf"] = token
    payload.setdefault("notice", notice_text(request))
    response = templates.TemplateResponse(request, name, payload, status_code=status_code)
    if request.cookies.get(CSRF_COOKIE) != token:
        response.set_cookie(
            CSRF_COOKIE,
            token,
            httponly=True,
            samesite="lax",
            max_age=60 * 60 * 24 * 14,
            path="/",
            secure=bool(request.app.state.secure_cookies),
        )
    return response


def message(request: Request, heading: str, text: str, *, home: str = "/", status_code: int = 400):
    return render(
        request,
        "message.html",
        {"heading": heading, "text": text, "home": home},
        status_code=status_code,
    )


async def form_or_reject(request: Request):
    form = await request.form()
    if not csrf_matches(request.cookies.get(CSRF_COOKIE), str(form.get("csrf", ""))):
        return None, message(request, "That form expired.", "Go back and try again.", home="/admin")
    return form, None
