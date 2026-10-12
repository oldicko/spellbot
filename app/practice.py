"""Turn the stored sitting into the JSON the practice page speaks and shows."""

from __future__ import annotations

from datetime import datetime

from app.spelling import colour_attempt, sentence_parts
from app.store import AnswerResult, Sitting, Word


def snapshot(sitting: Sitting, word: Word, child_name: str, now: datetime) -> dict:
    phase = "retry" if sitting.pending_try else "spell"
    hint = None
    if sitting.pending_try:
        letters, note = colour_attempt(sitting.pending_try, word.word_key)
        hint = _hint(letters, note)
    return {
        "phase": phase,
        "parts": sentence_parts(word.sentence, word.word_key, reveal=False),
        "audioUrl": _audio(sitting.token, sitting.attempt_count, word.id, pending=phase == "retry"),
        "hint": hint,
        "prefill": sitting.pending_try or "",
        "remainingSeconds": max(0, int((sitting.ends_at - now).total_seconds())),
        "answerUrl": f"/play/{sitting.token}/answer",
        "finishUrl": f"/play/{sitting.token}/finish",
        "resultsUrl": f"/play/{sitting.token}/results",
        "childName": child_name,
    }


def public_answer(result: AnswerResult) -> dict:
    if result.kind == "empty":
        return {"error": "empty"}
    if result.kind == "retry":
        return {
            "phase": "retry",
            "message": result.message,
            "hint": _hint(result.letters, result.note),
            "prefill": result.prefill,
            "remainingSeconds": result.remaining_seconds,
        }
    body: dict = {
        "phase": "done" if result.kind == "done" else "next",
        "message": result.message,
        "pauseMs": result.pause_ms,
        "resultsUrl": f"/play/{result.token}/results",
        "remainingSeconds": result.remaining_seconds,
    }
    if result.reveal_sentence is not None and result.reveal_key is not None:
        body["reveal"] = {
            "word": result.reveal_word,
            "parts": sentence_parts(
                result.reveal_sentence,
                result.reveal_key,
                reveal=True,
                tone=result.reveal_tone,
            ),
            "showWord": result.reveal_tone != "good",
        }
    if result.next_word is not None:
        body["next"] = {
            "parts": sentence_parts(result.next_word.sentence, result.next_word.word_key, reveal=False),
            "audioUrl": _audio(result.token, result.attempt_count, result.next_word.id, pending=False),
        }
    return body


def _hint(letters, note: str | None) -> dict:
    return {
        "letters": [{"ch": letter.ch, "ok": letter.ok} for letter in letters],
        "note": note,
    }


def _audio(token: str, attempt_count: int, word_id: int, pending: bool) -> str:
    flag = 1 if pending else 0
    return f"/play/{token}/audio?n={attempt_count}-{word_id}-{flag}"
