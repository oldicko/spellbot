"""Run spellbot. The admin password comes from the environment."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import uvicorn

from app.gemini_tts import MODEL, VOICE, GeminiSpeaker, LibraryVoice
from app.main import create_app
from app.tts import PiperVoiceEngine


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    password = os.environ.get("ADMIN_PASSWORD", "")
    if len(password) < 4:
        print("Set ADMIN_PASSWORD to at least 4 characters.", file=sys.stderr)
        raise SystemExit(1)
    data_dir = Path(os.environ.get("DATA_DIR", "data"))
    voice_path = Path(os.environ.get("VOICE_PATH", "voices/en_GB-cori-high.onnx"))
    piper = PiperVoiceEngine(voice_path, data_dir / "tts-cache")
    gemini = _gemini(data_dir)
    voice = LibraryVoice(piper, gemini)
    app = create_app(
        data_dir=data_dir,
        admin_password=password,
        voice=voice,
        secure_cookies=os.environ.get("SPELLBOT_SECURE_COOKIES") == "1",
        warm_voice=True,
    )
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)


def _gemini(data_dir: Path) -> GeminiSpeaker | None:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return None
    voice_name = os.environ.get("GEMINI_TTS_VOICE", VOICE).strip() or VOICE
    model = os.environ.get("GEMINI_TTS_MODEL", "").strip() or MODEL
    log_voice = logging.getLogger("spellbot.tts")
    log_voice.info("Spelling words will be recorded with Gemini voice %s.", voice_name)
    return GeminiSpeaker(
        key,
        data_dir / "tts-cache" / "gemini",
        voice=voice_name,
        model=model,
    )


if __name__ == "__main__":
    main()
