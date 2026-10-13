"""Record each spelling word once with Gemini, then play the saved file.

The style instruction stays out of the transcript. Gemini reads the transcript
aloud, so a stage direction in the text would be spoken to the child.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from app.spelling import normalise
from app.tts import SAMPLE_LINE, VoiceError

log = logging.getLogger("spellbot.tts")

MODEL = "gemini-3.8-flash-tts"
VOICE = "Sulafat"
STYLE_ID = "british-school-v1"
STYLE_PROMPT = (
    "Calm southern British primary-school teacher. Clear Received Pronunciation, "
    "not American. Slow and even, so each sound in this one spelling word is distinct. "
    "Neutral falling tone. Say only the transcript."
)
_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/interactions"
_RETRYABLE_HTTP = {429, 500, 502, 503, 504}
# Tier 1 allows 10 recordings a minute. This gap keeps a full list under that.
REQUEST_GAP = 7.0
_RETRY_IN = re.compile(r"retry in (?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?", re.I)


class GeminiSpeaker:
    def __init__(
        self,
        api_key: str,
        cache_dir: Path,
        *,
        voice: str = VOICE,
        model: str = MODEL,
        transport=None,
        pause: float = 1.0,
    ) -> None:
        self.api_key = api_key.strip()
        self.cache_dir = cache_dir
        self.voice = voice.strip() or VOICE
        self.model = model.strip() or MODEL
        self.pause = pause
        self._transport = transport or _http_transport
        self._lock = threading.Lock()

    def available(self) -> bool:
        return bool(self.api_key)

    def cached(self, text: str) -> bytes | None:
        path = self._path(text)
        if path.is_file() and path.stat().st_size > 44:
            return path.read_bytes()
        return None

    def record(self, text: str) -> bytes:
        with self._lock:
            existing = self.cached(text)
            if existing is not None:
                return existing
            data = self._synthesize(text)
            if not _looks_like_wav(data):
                raise VoiceError("The voice service did not return a recording.")
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            path = self._path(text)
            temporary = path.with_suffix(".tmp")
            temporary.write_bytes(data)
            temporary.replace(path)
            return data

    def _synthesize(self, text: str) -> bytes:
        payload = {
            "model": self.model,
            "input": [
                {
                    "type": "user_input",
                    "content": [
                        {
                            "type": "text",
                            "text": _spoken(text),
                            "annotations": [
                                {"type": "speech_metadata", "style": STYLE_PROMPT},
                            ],
                        }
                    ],
                }
            ],
            "response_format": {"type": "audio", "mime_type": "audio/wav"},
            "generation_config": {"speech_config": [{"voice": self.voice}]},
        }
        last = VoiceError("The voice service did not answer.", retryable=True)
        for attempt in range(3):
            try:
                return audio_from_response(self._transport(payload, self.api_key))
            except VoiceError as exc:
                last = exc
                # A rate limit says how long to wait. Retrying immediately spends
                # the remaining allowance and makes the next word fail too.
                if exc.retry_after or not exc.retryable or attempt == 2:
                    raise
                if self.pause:
                    time.sleep(self.pause * (attempt + 1))
        raise last

    def _path(self, text: str) -> Path:
        label = f"{STYLE_ID}|{self.model}|{self.voice}|{normalise(text)}"
        key = hashlib.sha256(label.encode()).hexdigest()
        return self.cache_dir / f"{key}.wav"


class WordRecorder:
    """Save missing words in the background. One recording at a time."""

    def __init__(
        self,
        speaker: GeminiSpeaker,
        *,
        pause: float = REQUEST_GAP,
        sleeper=None,
    ) -> None:
        self.speaker = speaker
        self.pause = pause
        self.error: str | None = None
        self.waiting_seconds = 0
        self._sleeper = sleeper or time.sleep
        self._lock = threading.Lock()
        self._pending: list[str] = []
        self._running = False
        self._thread: threading.Thread | None = None

    def submit(self, texts: list[str]) -> None:
        queued = 0
        with self._lock:
            waiting = {normalise(item) for item in self._pending}
            for text in texts:
                cleaned = " ".join(text.split())
                if not cleaned:
                    continue
                key = normalise(cleaned)
                if key in waiting or self.speaker.cached(cleaned) is not None:
                    continue
                self._pending.append(cleaned)
                waiting.add(key)
                queued += 1
            if queued and not self._running:
                self._running = True
                self._thread = threading.Thread(
                    target=self._loop, name="spellbot-record", daemon=True
                )
                self._thread.start()
        if queued:
            log.info("Recording %s spelling words.", queued)

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def wait(self, timeout: float = 5) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _loop(self) -> None:
        failures = 0
        while True:
            with self._lock:
                if not self._pending:
                    self._running = False
                    self.waiting_seconds = 0
                    return
                text = self._pending.pop(0)
            try:
                self.speaker.record(text)
            except VoiceError as exc:
                if exc.retryable:
                    failures = 0
                    delay = _wait_seconds(exc)
                    with self._lock:
                        self._pending.insert(0, text)
                        self.waiting_seconds = delay
                        still_queued = len(self._pending)
                    log.warning(
                        "Voice service asked us to wait %.0f seconds. %s spellings are still queued.",
                        delay,
                        still_queued,
                    )
                    self._sleeper(delay)
                    with self._lock:
                        self.waiting_seconds = 0
                    continue
                self._give_up_or_continue(text, failures)
                failures += 1
                if failures >= 3:
                    return
            except Exception:
                self._give_up_or_continue(text, failures)
                failures += 1
                if failures >= 3:
                    return
            else:
                failures = 0
                log.info("Recorded %s.", text)
                if self.pause:
                    self._sleeper(self.pause)

    def _give_up_or_continue(self, text: str, failures: int) -> None:
        log.exception("Could not record %r", text)
        with self._lock:
            self.error = "Some words could not be recorded."
            if failures + 1 >= 3:
                self._pending.clear()
                self._running = False
                self.waiting_seconds = 0
                log.error("Stopped recording after repeated failures.")


class LibraryVoice:
    """Play a saved Gemini recording. Until it exists, use the offline voice."""

    def __init__(self, fallback, gemini: GeminiSpeaker | None) -> None:
        self.fallback = fallback
        self.gemini = gemini
        self._words = None
        self._recorder: WordRecorder | None = None

    def available(self) -> bool:
        if self.gemini is not None and self.gemini.available():
            return True
        return bool(self.fallback.available())

    def bind_words(self, words) -> None:
        if self.gemini is None or not self.gemini.available():
            return
        self._words = words
        self._recorder = WordRecorder(self.gemini)

    def note(self, texts: list[str]) -> None:
        if self._recorder is not None:
            self._recorder.submit(list(texts))

    def warm(self) -> None:
        self.fallback.warm()
        if self._recorder is None or self._words is None:
            return
        try:
            words = list(self._words())
        except Exception:
            log.exception("Could not list spelling words to record")
            words = []
        self._recorder.submit([SAMPLE_LINE, *words])

    def preview(self, text: str) -> bytes:
        if self.gemini is not None and self.gemini.available():
            return self.gemini.record(text)
        return self.wav_for(text)

    def wav_for(self, text: str) -> bytes:
        if self.gemini is not None and self.gemini.available():
            cached = self.gemini.cached(text)
            if cached is not None:
                return cached
        if self.fallback.available():
            return self.fallback.wav_for(text)
        if self.gemini is not None and self.gemini.available():
            return self.gemini.record(text)
        raise VoiceError("The voice is not available.")

    def status_line(self) -> str | None:
        if self.gemini is None or not self.gemini.available():
            return None
        if self._words is None:
            return "The new voice records each spelling word once."
        try:
            words = [word for word in self._words() if word.strip()]
        except Exception:
            log.exception("Could not count recorded words")
            return "The new voice records each spelling word once."
        ready = sum(1 for word in words if self.gemini.cached(word) is not None)
        total = len(words)
        pending = self._recorder.pending_count() if self._recorder is not None else 0
        waiting = self._recorder.waiting_seconds if self._recorder is not None else 0
        if total == 0:
            return "Add a word list and the new voice will record it."
        if waiting:
            return (
                f"Recorded {ready} of {total} spelling words. "
                "The voice service allows 10 recordings a minute, so the rest continue slowly."
            )
        if ready >= total and pending == 0:
            return f"All {total} spelling words use the new voice."
        if self._recorder is not None and self._recorder.error and pending == 0:
            return (
                f"Recorded {ready} of {total} spelling words. "
                "The rest will be tried again the next time spellbot starts."
            )
        return (
            f"Recorded {ready} of {total} spelling words. "
            "Practice uses the new voice as each one is saved."
        )


def audio_from_response(payload: dict) -> bytes:
    found: list[bytes] = []
    steps = payload.get("steps") if isinstance(payload, dict) else None
    if isinstance(steps, list):
        for step in steps:
            content = step.get("content") if isinstance(step, dict) else None
            if not isinstance(content, list):
                continue
            for block in content:
                data = _audio_block(block)
                if data:
                    found.append(data)
    output = payload.get("output_audio") if isinstance(payload, dict) else None
    data = _audio_block(output)
    if data:
        found.append(data)
    if not found:
        raise VoiceError("The voice service did not return a recording.")
    return found[-1]


def _audio_block(block) -> bytes | None:
    if not isinstance(block, dict):
        return None
    kind = str(block.get("type") or "")
    mime = str(block.get("mime_type") or block.get("mimeType") or "")
    if kind != "audio" and not mime.startswith("audio/"):
        return None
    raw = block.get("data")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return base64.b64decode(raw)
    except Exception:
        return None


def _spoken(text: str) -> str:
    spoken = " ".join(text.split())
    if spoken and spoken[-1] not in ".!?":
        spoken += "."
    return spoken


def _retry_after_seconds(detail: str) -> float | None:
    match = _RETRY_IN.search(detail)
    if match is None or not any(match.groups()):
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = float(match.group(3) or 0)
    total = hours * 3600 + minutes * 60 + seconds
    if total <= 0:
        return None
    return total


def _wait_seconds(exc: VoiceError) -> float:
    delay = exc.retry_after if exc.retry_after else 60.0
    return min(delay + 1, 24 * 60 * 60)


def _looks_like_wav(data: bytes) -> bool:
    return len(data) > 44 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"


def _http_transport(payload: dict, api_key: str) -> dict:
    body = json.dumps(payload).encode()
    request = urllib.request.Request(
        _ENDPOINT,
        data=body,
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        log.warning("Gemini voice HTTP %s: %s", exc.code, detail)
        raise VoiceError(
            "The voice service rejected the recording.",
            retryable=exc.code in _RETRYABLE_HTTP,
            retry_after=_retry_after_seconds(detail) if exc.code == 429 else None,
        ) from exc
    except urllib.error.URLError as exc:
        log.warning("Gemini voice request failed: %s", exc.reason)
        raise VoiceError("The voice service did not answer.", retryable=True) from exc
    try:
        parsed = json.loads(raw.decode())
    except json.JSONDecodeError as exc:
        raise VoiceError("The voice service did not return a recording.", retryable=True) from exc
    if not isinstance(parsed, dict):
        raise VoiceError("The voice service did not return a recording.", retryable=True)
    return parsed
