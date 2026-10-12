"""What a child sees: pick a name, hear a word, spell it."""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from app.chart import score_chart
from app.practice import public_answer, snapshot
from app.spelling import percent
from app.stats import build_report
from app.ui import format_when, json_for_script, render

log = logging.getLogger("spellbot")
router = APIRouter()
_TOKEN = re.compile(r"[A-Za-z0-9_-]{8,80}")


def _store(request: Request):
    return request.app.state.store


def _now(request: Request):
    return request.app.state.now()


def _rng(request: Request):
    return request.app.state.rng_factory()


@router.get("/")
def home(request: Request):
    store = _store(request)
    store.close_expired(_now(request))
    return render(request, "home.html", {"children": store.list_children()})


@router.get("/c/{child_id}")
def lobby(request: Request, child_id: int):
    store = _store(request)
    now = _now(request)
    store.close_expired(now, child_id)
    child = store.get_child(child_id)
    if child is None:
        raise HTTPException(status_code=404)
    latest = store.latest_score(child_id)
    latest_percent = percent(latest.correct_count, latest.attempt_count) if latest else None
    return render(
        request,
        "lobby.html",
        {
            "child": child,
            "word_count": store.assigned_word_count(child_id),
            "running": store.open_sitting(child_id),
            "latest_percent": latest_percent,
            "error": None,
        },
    )


@router.post("/c/{child_id}/start")
def start(request: Request, child_id: int):
    store = _store(request)
    now = _now(request)
    child = store.get_child(child_id)
    if child is None:
        raise HTTPException(status_code=404)
    token = store.create_sitting(child_id, now, _rng(request))
    if token is None:
        return render(
            request,
            "lobby.html",
            {
                "child": child,
                "word_count": 0,
                "running": None,
                "latest_percent": None,
                "error": "Add a word list for this child first.",
            },
            status_code=400,
        )
    return RedirectResponse(f"/play/{token}", status_code=303)


@router.get("/play/{token}")
def play(request: Request, token: str):
    if not _TOKEN.fullmatch(token):
        raise HTTPException(status_code=404)
    store = _store(request)
    now = _now(request)
    store.close_expired(now)
    sitting = store.repair_sitting(token, now, _rng(request))
    if sitting is None:
        raise HTTPException(status_code=404)
    if sitting.finished_at is not None:
        return RedirectResponse(f"/play/{token}/results", status_code=303)
    word = store.get_word(sitting.current_word_id)
    child = store.get_child(sitting.child_id)
    if word is None or child is None:
        raise HTTPException(status_code=404)
    boot = snapshot(sitting, word, child.name, now)
    return render(
        request,
        "practice.html",
        {"child": child, "boot_json": json_for_script(boot)},
    )


@router.post("/play/{token}/answer")
async def answer(request: Request, token: str):
    if not _TOKEN.fullmatch(token):
        return JSONResponse({"error": "missing"}, status_code=404)
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "empty"}, status_code=400)
    spelling = payload.get("spelling", "") if isinstance(payload, dict) else ""
    result = _store(request).apply_answer(token, str(spelling), _now(request), _rng(request))
    if result is None:
        return JSONResponse({"error": "missing"}, status_code=404)
    return JSONResponse(public_answer(result))


@router.post("/play/{token}/finish")
def finish(request: Request, token: str):
    if not _TOKEN.fullmatch(token):
        return JSONResponse({"error": "missing"}, status_code=404)
    sitting = _store(request).finish(token, _now(request))
    if sitting is None:
        return JSONResponse({"error": "missing"}, status_code=404)
    return JSONResponse({"phase": "done", "resultsUrl": f"/play/{token}/results"})


@router.get("/play/{token}/audio")
def audio(request: Request, token: str):
    if not _TOKEN.fullmatch(token):
        raise HTTPException(status_code=404)
    store = _store(request)
    sitting = store.get_sitting(token)
    if sitting is None or sitting.finished_at is not None:
        raise HTTPException(status_code=404)
    word = store.get_word(sitting.current_word_id)
    if word is None:
        raise HTTPException(status_code=404)
    try:
        data = request.app.state.voice.wav_for(word.word)
    except Exception:
        log.exception("Could not speak the current word")
        return Response("The voice is not available.", status_code=503, media_type="text/plain")
    return Response(content=data, media_type="audio/wav")


@router.get("/play/{token}/results")
def results(request: Request, token: str):
    if not _TOKEN.fullmatch(token):
        raise HTTPException(status_code=404)
    store = _store(request)
    now = _now(request)
    store.close_expired(now)
    sitting = store.get_sitting(token)
    if sitting is None:
        raise HTTPException(status_code=404)
    if sitting.finished_at is None:
        return RedirectResponse(f"/play/{token}", status_code=303)
    child = store.get_child(sitting.child_id)
    if child is None:
        raise HTTPException(status_code=404)
    report = build_report(store, child.id, now)
    zone = request.app.state.zone
    points = [
        {
            "label": format_when(row.started_at, zone),
            "percent": percent(row.correct_count, row.attempt_count),
            "current": row.id == sitting.id,
        }
        for row in report.scores
    ]
    score = percent(sitting.correct_count, sitting.attempt_count) if sitting.attempt_count else None
    return render(
        request,
        "results.html",
        {
            "child": child,
            "sitting": sitting,
            "score": score,
            "fixes": store.fix_count(sitting.id),
            "points": points,
            "chart_svg": score_chart([point["percent"] for point in points]),
        },
    )
