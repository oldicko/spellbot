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

# Slower and steadier than conversation, so each sound in a short spelling word
# is held instead of running into the next one. The style and these numbers are
# part of the wav cache key, so a change does not replay an older recording.
SPEECH_STYLE = "clear-v1"
LENGTH_SCALE = 1.65
NOISE_SCALE = 0.333
NOISE_W_SCALE = 0.333


class VoiceError(Exception):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after


# The grown-up preview and the recording queue must use the same sentence,
# so the saved file is the one Play the voice serves.
SAMPLE_LINE = "Your spelling voice is ready."


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
        key = hashlib.sha256(self._cache_label(text).encode()).hexdigest()
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

            # A final stop lets the last sound finish instead of being clipped.
            spoken = text.strip()
            if spoken and spoken[-1] not in ".!?":
                spoken = f"{spoken}."
            config = SynthesisConfig(
                length_scale=LENGTH_SCALE,
                noise_scale=NOISE_SCALE,
                noise_w_scale=NOISE_W_SCALE,
                normalize_audio=True,
            )
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as wav_file:
                voice.synthesize_wav(spoken, wav_file, syn_config=config)
            return buffer.getvalue()

    def _cache_label(self, text: str) -> str:
        return (
            f"{SPEECH_STYLE}|{LENGTH_SCALE}|{NOISE_SCALE}|{NOISE_W_SCALE}"
            f"|{self.model_path.name}|{normalise(text)}"
        )

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
