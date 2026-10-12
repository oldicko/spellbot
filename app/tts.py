"""Offline British voice. Piper stays loaded so the next word is quick."""

from __future__ import annotations

import hashlib
import io
import logging
import threading
import wave
from pathlib import Path

from app.spelling import normalise

log = logging.getLogger("spellbot.tts")


class VoiceError(Exception):
    pass


class FakeVoice:
    """Test double. Records the text it was asked to say."""

    def __init__(self) -> None:
        self.spoken: list[str] = []

    def available(self) -> bool:
        return True

    def warm(self) -> None:
        return None

    def wav_for(self, text: str) -> bytes:
        self.spoken.append(text)
        return _silent_wav()


class PiperVoiceEngine:
    def __init__(self, model_path: Path, cache_dir: Path) -> None:
        self.model_path = model_path
        self.cache_dir = cache_dir
        self._voice = None
        self._lock = threading.Lock()
        self.error: str | None = None

    def available(self) -> bool:
        return self.model_path.is_file()

    def warm(self) -> None:
        if not self.available():
            self.error = "The voice file is not in the container."
            log.error("Voice model missing at %s", self.model_path)
            return
        try:
            with self._lock:
                self._ensure_loaded()
            self.error = None
            log.info("Voice ready: %s", self.model_path.name)
        except Exception:
            self.error = "The voice did not start."
            log.exception("Voice failed to load")

    def wav_for(self, text: str) -> bytes:
        if not self.available():
            raise VoiceError("The voice file is not in the container.")
        key = hashlib.sha256(normalise(text).encode()).hexdigest()
        path = self.cache_dir / f"{key}.wav"
        if path.is_file() and path.stat().st_size > 44:
            return path.read_bytes()
        data = self._synthesize(text)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(data)
        temporary.replace(path)
        return data

    def _synthesize(self, text: str) -> bytes:
        with self._lock:
            voice = self._ensure_loaded()
            from piper.config import SynthesisConfig

            # A single spelling word is easier to hear a little slower than conversation.
            config = SynthesisConfig(length_scale=1.12, normalize_audio=True)
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as wav_file:
                voice.synthesize_wav(text, wav_file, syn_config=config)
            return buffer.getvalue()

    def _ensure_loaded(self):
        if self._voice is None:
            from piper import PiperVoice

            self._voice = PiperVoice.load(self.model_path)
        return self._voice


def _silent_wav() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(22050)
        wav_file.writeframes(b"\x00\x00" * 220)
    return buffer.getvalue()
