import re
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.main import create_app
from app.tts import FakeVoice


class Clock:
    def __init__(self) -> None:
        self.current = datetime(2026, 10, 12, 15, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **kwargs) -> None:
        self.current += timedelta(**kwargs)


def csrf_of(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


def make_client(tmp_path):
    voice = FakeVoice()
    clock = Clock()
    app = create_app(
        data_dir=tmp_path,
        admin_password="test-secret",
        voice=voice,
        now=clock,
        warm_voice=False,
    )
    client = TestClient(app, follow_redirects=False)
    return client, clock, voice


def login(client: TestClient) -> None:
    page = client.get("/admin/login")
    token = csrf_of(page.text)
    response = client.post("/admin/login", data={"password": "test-secret", "csrf": token})
    assert response.status_code == 303
    assert "test-secret" not in response.headers.get("set-cookie", "")
    assert "httponly" in response.headers.get("set-cookie", "").lower()


def add_child(client: TestClient, name: str = "Grace", minutes: int = 5) -> None:
    page = client.get("/admin")
    response = client.post(
        "/admin/children",
        data={"csrf": csrf_of(page.text), "name": name, "minutes": str(minutes)},
    )
    assert response.status_code == 303


def add_list(client: TestClient, name: str, paste: str, child_id: str | None) -> str:
    page = client.get("/admin/lists")
    created = client.post("/admin/lists", data={"csrf": csrf_of(page.text), "name": name})
    assert created.status_code == 303
    path = created.headers["location"]
    if child_id is not None:
        editor = client.get(path)
        saved = client.post(
            path,
            data={"csrf": csrf_of(editor.text), "name": name, "child": child_id},
        )
        assert saved.status_code == 303
    editor = client.get(path)
    pasted = client.post(
        path + "/words",
        data={"csrf": csrf_of(editor.text), "paste": paste},
    )
    assert pasted.status_code == 303
    return path


def start(client: TestClient, child_id: int = 1) -> str:
    response = client.post(f"/c/{child_id}/start")
    assert response.status_code == 303
    return response.headers["location"].rstrip("/").rsplit("/", 1)[-1]


def test_admin_password_and_hidden_stats(tmp_path):
    client, _clock, voice = make_client(tmp_path)
    hidden = client.get("/admin/children/1/stats")
    assert hidden.status_code == 303
    assert hidden.headers["location"] == "/admin/login"

    page = client.get("/admin/login")
    wrong = client.post("/admin/login", data={"password": "nope", "csrf": csrf_of(page.text)})
    assert wrong.status_code == 401
    assert "spellbot_admin" not in wrong.headers.get("set-cookie", "")

    login(client)
    sample = client.get("/admin/voice")
    assert sample.status_code == 200
    assert sample.headers["content-type"].startswith("audio/wav")
    assert voice.spoken == ["Your spelling voice is ready."]


def test_practice_hides_the_spelling_and_scores_the_first_try(tmp_path):
    client, _clock, voice = make_client(tmp_path)
    login(client)
    add_child(client)
    add_list(client, "Sounds", "quartz | A quartz crystal is pink.", "1")
    add_list(client, "Spare", "xylophone | A xylophone is loud.", None)

    home = client.get("/")
    assert "Grace" in home.text
    assert "xylophone" not in home.text

    token = start(client)
    page = client.get(f"/play/{token}")
    assert page.status_code == 200
    lowered = page.text.casefold()
    assert "quartz" not in lowered
    assert "xylophone" not in lowered
    assert "crystal is pink" in lowered
    assert 'writingsuggestions="false"' in page.text
    assert 'autocorrect="off"' in page.text
    assert 'spellcheck="false"' in page.text
    assert 'id="letter-keys"' in page.text
    assert 'class="key-hint">Space' in page.text
    assert 'class="key-hint">Enter' in page.text

    audio = client.get(f"/play/{token}/audio")
    assert audio.status_code == 200
    assert voice.spoken[-1] == "quartz"

    wrong = client.post(f"/play/{token}/answer", json={"spelling": "xxxxx"})
    assert wrong.status_code == 200
    body = wrong.json()
    assert body["phase"] == "retry"
    assert "quartz" not in wrong.text
    assert body["hint"]["letters"][0]["ok"] is False

    fixed = client.post(f"/play/{token}/answer", json={"spelling": "Quartz"})
    assert fixed.json()["phase"] == "next"
    assert fixed.json()["message"] == "Got it."
    assert "quartz" in fixed.text.casefold()

    client.post(f"/play/{token}/finish")
    results = client.get(f"/play/{token}/results")
    assert results.status_code == 200
    assert "0%" in results.text
    assert "0 of 1 on the first try." in results.text
    assert "You fixed 1 after a second look." in results.text
    assert "xylophone" not in results.text


def test_three_in_a_row_is_learned_until_a_miss(tmp_path):
    client, _clock, _voice = make_client(tmp_path)
    login(client)
    add_child(client, "Julian", 5)
    add_list(client, "List", "quartz | A quartz crystal is pink.", "1")
    token = start(client)
    for _ in range(3):
        body = client.post(f"/play/{token}/answer", json={"spelling": "quartz"}).json()
        assert body["message"] == "Yes."
    client.post(f"/play/{token}/finish")
    stats = client.get("/admin/children/1/stats")
    assert "Learned" in stats.text
    assert "3 in a row" in stats.text

    token = start(client)
    client.post(f"/play/{token}/answer", json={"spelling": "kwarts"})
    client.post(f"/play/{token}/finish")
    stats = client.get("/admin/children/1/stats")
    assert "Practising" in stats.text
    assert "Learned" not in stats.text
    assert "Last try: kwarts" in stats.text


def test_curly_apostrophe_matches_and_expiry_records_a_miss(tmp_path):
    client, clock, voice = make_client(tmp_path)
    login(client)
    add_child(client, "Grace", 1)
    add_list(client, "H", "they're | They're going to the park.", "1")
    token = start(client)
    page = client.get(f"/play/{token}")
    assert "they're" not in page.text.casefold()
    assert "going to the park" in page.text
    audio = client.get(f"/play/{token}/audio")
    assert audio.status_code == 200
    assert voice.spoken[-1] == "they're"
    heard = client.post(f"/play/{token}/answer", json={"spelling": "They\u2019re"})
    assert heard.json()["message"] == "Yes."

    client.post(f"/play/{token}/finish")
    token = start(client)
    client.post(f"/play/{token}/answer", json={"spelling": "theyre"})
    clock.advance(minutes=2)
    expired = client.get(f"/play/{token}")
    assert expired.status_code == 303
    assert expired.headers["location"].endswith("/results")
    results = client.get(f"/play/{token}/results")
    assert "0 of 1 on the first try." in results.text


def test_bad_paste_is_rejected_whole(tmp_path):
    client, _clock, _voice = make_client(tmp_path)
    login(client)
    created = client.post(
        "/admin/lists",
        data={"csrf": csrf_of(client.get("/admin/lists").text), "name": "Draft"},
    )
    path = created.headers["location"]
    rejected = client.post(
        path + "/words",
        data={"csrf": csrf_of(client.get(path).text), "paste": "no separator here"},
    )
    assert rejected.status_code == 400
    assert "separated by |" in rejected.text
    assert "No words yet." in rejected.text
