"""Grown-up pages: children, word lists, and progress."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from app.auth import (
    ADMIN_COOKIE,
    COOKIE_TTL,
    admin_cookie_valid,
    make_admin_cookie,
    password_matches,
)
from app.chart import score_chart
from app.spelling import parse_word_list
from app.stats import STATUS_LABELS, TREND_LABELS, build_report
from app.store import StoreError
from app.tts import SAMPLE_LINE
from app.ui import format_when, form_or_reject, message, render

log = logging.getLogger("spellbot")
router = APIRouter(prefix="/admin")


def _store(request: Request):
    return request.app.state.store


def _now(request: Request):
    return request.app.state.now()


def _minutes(value: object) -> int | None:
    text = str(value).strip()
    if not text.isdigit():
        return None
    number = int(text)
    if 1 <= number <= 60:
        return number
    return None


def _child_cards(request: Request):
    store = _store(request)
    now = _now(request)
    cards = []
    for child in store.list_children():
        report = build_report(store, child.id, now)
        cards.append({"child": child, "report": report, "trend_label": TREND_LABELS[report.trend]})
    return cards


@router.get("/login")
def login_form(request: Request):
    if _logged_in(request):
        return RedirectResponse("/admin", status_code=303)
    return render(request, "admin_login.html", {"error": None})


@router.post("/login")
async def login_post(request: Request):
    form, rejected = await form_or_reject(request)
    if rejected is not None:
        return rejected
    assert form is not None
    ip = request.client.host if request.client else "local"
    guard = request.app.state.login_guard
    if guard.blocked(ip):
        return render(
            request,
            "admin_login.html",
            {"error": "Try again in a few minutes."},
            status_code=429,
        )
    password = str(form.get("password", ""))
    if not password_matches(request.app.state.password, password):
        guard.fail(ip)
        return render(
            request,
            "admin_login.html",
            {"error": "That password is not right."},
            status_code=401,
        )
    guard.clear(ip)
    response = RedirectResponse("/admin", status_code=303)
    response.set_cookie(
        ADMIN_COOKIE,
        make_admin_cookie(request.app.state.secret, request.app.state.password, _now(request).timestamp()),
        httponly=True,
        samesite="lax",
        max_age=COOKIE_TTL,
        path="/",
        secure=bool(request.app.state.secure_cookies),
    )
    return response


@router.post("/logout")
async def logout(request: Request):
    _form, rejected = await form_or_reject(request)
    if rejected is not None:
        return rejected
    response = RedirectResponse("/admin/login", status_code=303)
    response.delete_cookie(ADMIN_COOKIE, path="/")
    return response


@router.get("")
def children_page(request: Request):
    return render(
        request,
        "admin_children.html",
        {
            "cards": _child_cards(request),
            "error": None,
            "draft_name": "",
            "draft_minutes": 5,
            **_voice_fields(request),
        },
    )


@router.post("/children")
async def add_child(request: Request):
    form, rejected = await form_or_reject(request)
    if rejected is not None:
        return rejected
    assert form is not None
    name = str(form.get("name", ""))
    minutes = _minutes(form.get("minutes", ""))
    if minutes is None:
        return _children_again(request, "Minutes need to be between 1 and 60.", name, 5)
    try:
        _store(request).add_child(name, minutes, _now(request))
    except StoreError as exc:
        return _children_again(request, str(exc), name, minutes)
    return RedirectResponse("/admin?notice=child-added", status_code=303)


@router.post("/children/{child_id}")
async def update_child(request: Request, child_id: int):
    form, rejected = await form_or_reject(request)
    if rejected is not None:
        return rejected
    assert form is not None
    if _store(request).get_child(child_id) is None:
        raise HTTPException(status_code=404)
    minutes = _minutes(form.get("minutes", ""))
    if minutes is None:
        return _children_again(request, "Minutes need to be between 1 and 60.", "", 5)
    try:
        _store(request).update_child(child_id, str(form.get("name", "")), minutes)
    except StoreError as exc:
        return _children_again(request, str(exc), str(form.get("name", "")), minutes)
    return RedirectResponse("/admin?notice=saved", status_code=303)


@router.get("/children/{child_id}/delete")
def delete_child_confirm(request: Request, child_id: int):
    child = _store(request).get_child(child_id)
    if child is None:
        raise HTTPException(status_code=404)
    return render(request, "admin_delete.html", {"kind": "child", "target": child})


@router.post("/children/{child_id}/delete")
async def delete_child(request: Request, child_id: int):
    _form, rejected = await form_or_reject(request)
    if rejected is not None:
        return rejected
    _store(request).delete_child(child_id)
    return RedirectResponse("/admin?notice=deleted", status_code=303)


@router.get("/children/{child_id}/stats")
def child_stats(request: Request, child_id: int):
    store = _store(request)
    child = store.get_child(child_id)
    if child is None:
        raise HTTPException(status_code=404)
    now = _now(request)
    report = build_report(store, child_id, now)
    zone = request.app.state.zone
    points = [
        {
            "label": format_when(row.started_at, zone),
            "points": row.points,
        }
        for row in report.scores
    ]
    return render(
        request,
        "admin_stats.html",
        {
            "child": child,
            "report": report,
            "trend_label": TREND_LABELS[report.trend],
            "status_labels": STATUS_LABELS,
            "points": points,
            "chart_svg": score_chart([point["points"] for point in points]),
        },
    )


@router.get("/lists")
def lists_page(request: Request):
    return render(
        request,
        "admin_lists.html",
        {"lists": _store(request).list_word_lists(), "error": None, "draft_name": ""},
    )


@router.post("/lists")
async def add_list(request: Request):
    form, rejected = await form_or_reject(request)
    if rejected is not None:
        return rejected
    assert form is not None
    name = str(form.get("name", ""))
    try:
        created = _store(request).add_word_list(name, _now(request))
    except StoreError as exc:
        return render(
            request,
            "admin_lists.html",
            {"lists": _store(request).list_word_lists(), "error": str(exc), "draft_name": name},
            status_code=400,
        )
    return RedirectResponse(f"/admin/lists/{created.id}", status_code=303)


@router.get("/lists/{list_id}")
def edit_list(request: Request, list_id: int):
    word_list = _store(request).get_word_list(list_id)
    if word_list is None:
        raise HTTPException(status_code=404)
    return _list_page(request, word_list)


@router.post("/lists/{list_id}")
async def save_list(request: Request, list_id: int):
    form, rejected = await form_or_reject(request)
    if rejected is not None:
        return rejected
    assert form is not None
    store = _store(request)
    if store.get_word_list(list_id) is None:
        raise HTTPException(status_code=404)
    child_ids = [int(value) for value in form.getlist("child") if str(value).isdigit()]
    try:
        store.rename_word_list(list_id, str(form.get("name", "")))
        store.set_assignments(list_id, child_ids)
    except StoreError as exc:
        word_list = store.get_word_list(list_id)
        return _list_page(request, word_list, errors=[str(exc)])
    return RedirectResponse(f"/admin/lists/{list_id}?notice=saved", status_code=303)


@router.post("/lists/{list_id}/words")
async def paste_words(request: Request, list_id: int):
    form, rejected = await form_or_reject(request)
    if rejected is not None:
        return rejected
    assert form is not None
    store = _store(request)
    word_list = store.get_word_list(list_id)
    if word_list is None:
        raise HTTPException(status_code=404)
    draft = str(form.get("paste", ""))
    parsed, errors = parse_word_list(draft)
    if errors:
        return _list_page(request, word_list, errors=errors, draft=draft)
    if not parsed:
        return _list_page(request, word_list, errors=["Paste at least one word."], draft=draft)
    try:
        added, updated = store.add_words(list_id, parsed)
    except StoreError as exc:
        return _list_page(request, word_list, errors=[str(exc)], draft=draft)
    note = getattr(request.app.state.voice, "note", None)
    if callable(note):
        note([item.word for item in parsed])
    return RedirectResponse(
        f"/admin/lists/{list_id}?notice=words&added={added}&updated={updated}",
        status_code=303,
    )


@router.post("/words/{word_id}")
async def save_sentence(request: Request, word_id: int):
    form, rejected = await form_or_reject(request)
    if rejected is not None:
        return rejected
    assert form is not None
    store = _store(request)
    word = store.get_word(word_id)
    if word is None:
        raise HTTPException(status_code=404)
    try:
        store.update_sentence(word_id, str(form.get("sentence", "")))
    except StoreError as exc:
        word_list = store.get_word_list(word.list_id)
        return _list_page(request, word_list, errors=[str(exc)])
    return RedirectResponse(f"/admin/lists/{word.list_id}?notice=saved", status_code=303)


@router.post("/words/{word_id}/delete")
async def delete_word(request: Request, word_id: int):
    _form, rejected = await form_or_reject(request)
    if rejected is not None:
        return rejected
    store = _store(request)
    word = store.get_word(word_id)
    if word is None:
        return RedirectResponse("/admin/lists", status_code=303)
    store.delete_word(word_id)
    return RedirectResponse(f"/admin/lists/{word.list_id}?notice=deleted", status_code=303)


@router.get("/lists/{list_id}/delete")
def delete_list_confirm(request: Request, list_id: int):
    word_list = _store(request).get_word_list(list_id)
    if word_list is None:
        raise HTTPException(status_code=404)
    return render(request, "admin_delete.html", {"kind": "list", "target": word_list})


@router.post("/lists/{list_id}/delete")
async def delete_list(request: Request, list_id: int):
    _form, rejected = await form_or_reject(request)
    if rejected is not None:
        return rejected
    _store(request).delete_word_list(list_id)
    return RedirectResponse("/admin/lists?notice=deleted", status_code=303)


@router.get("/voice")
def voice_sample(request: Request):
    voice = request.app.state.voice
    speak = getattr(voice, "preview", None)
    if not callable(speak):
        speak = voice.wav_for
    try:
        data = speak(SAMPLE_LINE)
    except Exception:
        log.exception("Voice sample failed")
        return Response("The voice is not available.", status_code=503, media_type="text/plain")
    return Response(content=data, media_type="audio/wav")


def _logged_in(request: Request) -> bool:
    cookie = request.cookies.get(ADMIN_COOKIE, "")
    return admin_cookie_valid(
        request.app.state.secret,
        request.app.state.password,
        cookie,
        _now(request).timestamp(),
    )


def _voice_fields(request: Request) -> dict:
    voice = request.app.state.voice
    status = getattr(voice, "status_line", None)
    return {
        "voice_ready": voice.available(),
        "voice_note": status() if callable(status) else None,
    }


def _children_again(request: Request, error: str, name: str, minutes: int):
    return render(
        request,
        "admin_children.html",
        {
            "cards": _child_cards(request),
            "error": error,
            "draft_name": name,
            "draft_minutes": minutes,
            **_voice_fields(request),
        },
        status_code=400,
    )


def _list_page(request: Request, word_list, errors: list[str] | None = None, draft: str = ""):
    store = _store(request)
    return render(
        request,
        "admin_list.html",
        {
            "word_list": word_list,
            "words": store.words_in_list(word_list.id),
            "children": store.list_children(),
            "assigned": store.assignment_ids(word_list.id),
            "errors": errors or [],
            "draft": draft,
        },
        status_code=400 if errors else 200,
    )
