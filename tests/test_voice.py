import base64
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.gemini_tts import GeminiSpeaker, LibraryVoice, WordRecorder, audio_from_response
from app.main import create_app
from app.spelling import ParsedWord
from app.store import Store
from app.tts import SAMPLE_LINE, FakeVoice, VoiceError, _silent_wav


def _payload_of(calls, text):
    assert calls
    body = calls[-1]
    spoken = body["input"][0]["content"][0]["text"]
    assert spoken == text
    assert "British" not in spoken
    assert body["input"][0]["content"][0]["annotations"][0]["style"]
    return body


def _speaker(tmp_path, transport, voice="Sulafat"):
    return GeminiSpeaker(
        "test-key",
        tmp_path / "gemini",
        voice=voice,
        transport=transport,
        pause=0,
    )


def _wav_transport(calls):
    wav = _silent_wav()

    def transport(payload, api_key):
        assert api_key == "test-key"
        calls.append(payload)
        encoded = base64.b64encode(wav).decode()
        return {"steps": [{"type": "model_output", "content": [{"type": "audio", "data": encoded}]}]}

    return transport, wav


def test_a_word_is_recorded_once_and_the_instruction_is_not_spoken(tmp_path):
    calls = []
    transport, wav = _wav_transport(calls)
    speaker = _speaker(tmp_path, transport)
    assert speaker.record("quartz") == wav
    assert speaker.record("Quartz") == wav
    assert len(calls) == 1
    _payload_of(calls, "quartz.")
    assert speaker.cached("quartz.") == wav


def test_a_different_voice_is_a_different_recording(tmp_path):
    calls = []
    transport, _wav = _wav_transport(calls)
    first = _speaker(tmp_path, transport, voice="Sulafat")
    second = _speaker(tmp_path, transport, voice="Kore")
    first.record("quartz")
    second.record("quartz")
    assert len(calls) == 2
    assert calls[1]["generation_config"]["speech_config"][0]["voice"] == "Kore"


def test_a_retry_then_saves_the_recording(tmp_path):
    wav = _silent_wav()
    calls = {"n": 0}

    def transport(payload, api_key):
        calls["n"] += 1
        if calls["n"] < 3:
            raise VoiceError("busy", retryable=True)
        encoded = base64.b64encode(wav).decode()
        return {"output_audio": {"type": "audio", "mime_type": "audio/wav", "data": encoded}}

    speaker = _speaker(tmp_path, transport)
    assert speaker.record("there") == wav
    assert calls["n"] == 3
    assert speaker.cached("there") == wav


def test_bad_audio_is_not_saved(tmp_path):
    def transport(payload, api_key):
        return {"steps": [{"type": "model_output", "content": [{"type": "audio", "data": base64.b64encode(b"nope").decode()}]}]}

    speaker = _speaker(tmp_path, transport)
    try:
        speaker.record("where")
    except VoiceError:
        pass
    else:
        raise AssertionError("expected a voice error")
    assert speaker.cached("where") is None


def test_practice_uses_the_recording_and_piper_until_then(tmp_path):
    calls = []
    transport, wav = _wav_transport(calls)
    speaker = _speaker(tmp_path, transport)
    speaker.record("quartz")
    fallback = FakeVoice()
    voice = LibraryVoice(fallback, speaker)
    assert voice.wav_for("quartz") == wav
    assert fallback.spoken == []
    voice.wav_for("xylophone")
    assert fallback.spoken == ["xylophone"]
    assert len(calls) == 1


def test_preview_records_with_gemini(tmp_path):
    calls = []
    transport, wav = _wav_transport(calls)
    voice = LibraryVoice(FakeVoice(), _speaker(tmp_path, transport))
    assert voice.preview(SAMPLE_LINE) == wav
    _payload_of(calls, SAMPLE_LINE)
    assert voice.fallback.spoken == []


def test_recording_stops_after_repeated_failures(tmp_path):
    calls = []

    def transport(payload, api_key):
        calls.append(payload["input"][0]["content"][0]["text"])
        raise VoiceError("no")

    recorder = WordRecorder(_speaker(tmp_path, transport), pause=0)
    recorder.submit(["one", "two", "three", "four", "five"])
    recorder.wait()
    assert calls == ["one.", "two.", "three."]
    assert recorder.pending_count() == 0
    assert recorder.error


def test_spellings_are_one_per_word_across_lists(tmp_path):
    store = Store(tmp_path / "spellbot.sqlite")
    now = datetime(2026, 10, 13, tzinfo=timezone.utc)
    first = store.add_word_list("Year 3", now)
    second = store.add_word_list("Homophones", now)
    store.add_words(
        first.id,
        [ParsedWord("they're", "they're", "They're going to the park.")],
    )
    store.add_words(
        second.id,
        [
            ParsedWord("They're", "they're", "They're going to the park."),
            ParsedWord("there", "there", "The book is over there."),
        ],
    )
    assert store.spellings() == ["they're", "there"]


def test_paste_queues_the_new_words_and_the_children_page_shows_progress(tmp_path):
    noted = []

    class Voice(FakeVoice):
        def note(self, texts):
            noted.extend(texts)

        def status_line(self):
            return "Recorded 1 of 4 spelling words."

    app = create_app(
        data_dir=tmp_path,
        admin_password="test-secret",
        voice=Voice(),
        warm_voice=False,
    )
    client = TestClient(app, follow_redirects=False)
    page = client.get("/admin/login")
    token = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    assert client.post("/admin/login", data={"password": "test-secret", "csrf": token}).status_code == 303
    home = client.get("/admin")
    assert "Recorded 1 of 4 spelling words." in home.text
    lists = client.get("/admin/lists")
    token = lists.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    created = client.post("/admin/lists", data={"csrf": token, "name": "Sounds"})
    path = created.headers["location"]
    editor = client.get(path)
    token = editor.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    pasted = client.post(
        path + "/words",
        data={"csrf": token, "paste": "quartz | A quartz crystal is pink."},
    )
    assert pasted.status_code == 303
    assert noted == ["quartz"]


def test_startup_records_every_list_and_then_reports_them_ready(tmp_path):
    calls = []
    transport, _wav = _wav_transport(calls)
    voice = LibraryVoice(FakeVoice(), _speaker(tmp_path, transport))
    voice.bind_words(lambda: ["quartz", "there"])
    voice._recorder.pause = 0
    assert voice.status_line() == (
        "Recorded 0 of 2 spelling words. "
        "Practice uses the new voice as each one is saved."
    )
    voice.warm()
    voice._recorder.wait()
    spoken = [call["input"][0]["content"][0]["text"] for call in calls]
    assert spoken == [SAMPLE_LINE, "quartz.", "there."]
    assert voice.status_line() == "All 2 spelling words use the new voice."
    voice.warm()
    voice._recorder.wait()
    assert len(calls) == 3


def test_gemini_is_built_only_when_a_key_is_set(tmp_path, monkeypatch):
    from app.__main__ import _gemini

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert _gemini(tmp_path) is None
    monkeypatch.setenv("GEMINI_API_KEY", "abc")
    monkeypatch.setenv("GEMINI_TTS_VOICE", "Kore")
    speaker = _gemini(tmp_path)
    assert speaker is not None
    assert speaker.api_key == "abc"
    assert speaker.voice == "Kore"
    assert speaker.cache_dir == tmp_path / "tts-cache" / "gemini"


def test_audio_block_from_the_response_steps():
    wav = _silent_wav()
    payload = {
        "steps": [
            {
                "type": "model_output",
                "content": [{"type": "audio", "data": base64.b64encode(wav).decode()}],
            }
        ]
    }
    assert audio_from_response(payload) == wav
