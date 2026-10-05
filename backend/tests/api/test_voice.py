"""Voice input (C8.1): the transcript comes back for the user to check; nothing is sent to the
chat from here. No real provider calls: a stand-in provider, or a mocked HTTP response."""

from collections.abc import Iterator
from typing import Any

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from app.api.routes import voice
from app.auth.tokens import create_access_token
from app.core import config
from app.main import app
from app.services import speech
from app.services.speech import DeepgramSTT, SpeechError, Transcript
from scripts.fabricate import demo_id

WEBM = {"Content-Type": "audio/webm;codecs=opus"}


class StandIn:
    name = "stand-in"

    def __init__(self, text: str = "Which POs are overdue?", fail: bool = False) -> None:
        self.text, self.fail, self.calls = text, fail, 0

    def transcribe(self, audio: bytes, content_type: str, language: str = "auto") -> Transcript:
        self.calls += 1
        self.language = language
        if self.fail:
            raise SpeechError("The speech service could not transcribe that.")
        return Transcript(text=self.text, provider=self.name)


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    config.get_settings.cache_clear()
    try:
        config.get_settings().jwt_secret()
    except RuntimeError:
        pytest.skip("JWT_SECRET_KEY not set")
    voice.voice_limit.clear()
    yield
    config.get_settings.cache_clear()


def client(db: psycopg.Connection) -> TestClient:
    user_id = demo_id("user", "inventory@demo.inventorymantra")
    row = db.execute("SELECT token_version FROM users WHERE id = %s", (user_id,)).fetchone()
    assert row is not None, "run make test-db"
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {create_access_token(user_id, row[0])}"
    return c


def use(monkeypatch: pytest.MonkeyPatch, provider: Any) -> None:
    monkeypatch.setattr(voice, "get_stt", lambda: provider)


def test_signed_out_users_get_nothing() -> None:
    assert (
        TestClient(app).post("/api/voice/transcribe", content=b"x", headers=WEBM).status_code == 401
    )
    assert TestClient(app).get("/voice/status").status_code == 401


def test_without_a_key_the_browser_is_told_to_use_its_own_recognition(
    db: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPGRAM_API_KEY", "")
    config.get_settings.cache_clear()
    c = client(db)
    assert c.get("/api/voice/status").json() == {"server": False}
    response = c.post("/api/voice/transcribe", content=b"audio", headers=WEBM)
    assert response.status_code == 503


def test_the_transcript_comes_back_and_nothing_is_sent_to_the_chat(
    db: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = StandIn("कौन से आइटम खत्म होने वाले हैं?")
    use(monkeypatch, provider)
    c = client(db)
    threads_before = db.execute("SELECT count(*) FROM chat_threads").fetchone()
    assert c.get("/api/voice/status").json() == {"server": True}
    response = c.post("/api/voice/transcribe", content=b"opus-bytes", headers=WEBM)
    assert response.status_code == 200
    assert response.json() == {"text": "कौन से आइटम खत्म होने वाले हैं?", "provider": "stand-in"}
    assert db.execute("SELECT count(*) FROM chat_threads").fetchone() == threads_before


@pytest.mark.parametrize(
    ("body", "headers", "code"),
    [
        (b"x", {"Content-Type": "application/json"}, 415),
        (b"", WEBM, 400),
        (b"x" * (speech.MAX_AUDIO_BYTES + 1), WEBM, 413),
    ],
    ids=["not-audio", "empty", "too-long"],
)
def test_bad_uploads_are_refused_before_the_provider_is_called(
    db: psycopg.Connection, monkeypatch: pytest.MonkeyPatch, body: bytes,
    headers: dict[str, str], code: int,
) -> None:  # fmt: skip
    provider = StandIn()
    use(monkeypatch, provider)
    assert (
        client(db).post("/api/voice/transcribe", content=body, headers=headers).status_code == code
    )
    assert provider.calls == 0


def test_provider_failures_and_silence_get_a_safe_message(
    db: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    use(monkeypatch, StandIn(fail=True))
    c = client(db)
    failed = c.post("/api/voice/transcribe", content=b"x", headers=WEBM)
    assert failed.status_code == 502 and "could not" in failed.json()["detail"]
    use(monkeypatch, StandIn(text=""))
    assert c.post("/api/voice/transcribe", content=b"x", headers=WEBM).status_code == 422


def test_recordings_are_rate_limited_per_user(
    db: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VOICE_REQUESTS_PER_MINUTE", "2")
    config.get_settings.cache_clear()
    use(monkeypatch, StandIn())
    c = client(db)
    codes = [c.post("/api/voice/transcribe", content=b"x", headers=WEBM).status_code
             for _ in range(3)]  # fmt: skip
    assert codes == [200, 200, 429]


def test_deepgram_request_and_response_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_post(url: str, **kw: Any) -> httpx.Response:
        seen.update(url=url, **kw)
        body = {
            "results": {"channels": [{"alternatives": [{"transcript": " kaun se PO late hain "}]}]}
        }
        return httpx.Response(200, json=body)

    monkeypatch.setattr(speech.httpx, "post", fake_post)
    out = DeepgramSTT("test-key", "nova-3", "multi").transcribe(b"opus", "audio/webm")
    assert out == Transcript(text="kaun se PO late hain", provider="deepgram")
    params = dict(seen["params"])  # the last keyterm wins in a dict; check them separately
    assert params["model"] == "nova-3" and params["language"] == "multi"
    keyterms = [v for k, v in seen["params"] if k == "keyterm"]
    assert "132-1" in keyterms and "AC Contactor" in keyterms  # domain words for Nova-3
    assert seen["headers"]["Authorization"] == "Token test-key"


def test_deepgram_errors_become_speech_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(speech.httpx, "post", lambda url, **kw: httpx.Response(401, json={}))
    with pytest.raises(SpeechError):
        DeepgramSTT("bad", "nova-3", "multi").transcribe(b"opus", "audio/webm")

    def down(url: str, **kw: Any) -> httpx.Response:
        raise httpx.ConnectError("down")

    monkeypatch.setattr(speech.httpx, "post", down)
    with pytest.raises(SpeechError):
        DeepgramSTT("k", "nova-3", "multi").transcribe(b"opus", "audio/webm")


def test_the_language_picked_next_to_the_mic_is_passed_on(
    db: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = StandIn()
    use(monkeypatch, provider)
    c = client(db)
    assert c.post("/api/voice/transcribe?lang=hi", content=b"x", headers=WEBM).status_code == 200
    assert provider.language == "hi"
    assert c.post("/api/voice/transcribe?lang=fr", content=b"x", headers=WEBM).status_code == 422


def test_deepgram_gets_indian_english_or_hindi_when_picked(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def fake_post(url: str, **kw: Any) -> httpx.Response:
        seen.append(dict(kw["params"])["language"])
        body = {"results": {"channels": [{"alternatives": [{"transcript": "ok"}]}]}}
        return httpx.Response(200, json=body)

    monkeypatch.setattr(speech.httpx, "post", fake_post)
    stt = DeepgramSTT("k", "nova-3", "multi")
    for lang in ("auto", "en", "hi"):
        stt.transcribe(b"opus", "audio/webm", lang)  # type: ignore[arg-type]
    assert seen == ["multi", "en-IN", "hi"]


@pytest.mark.parametrize(
    ("heard", "shown"),
    [("How many MS Bush in one thirty two dash one", "How many MS Bush in 132-1"),
     ("stock in one three two dash two?", "stock in 132-2?"),
     ("Add three AC Contactor to 132 dash 2", "Add three AC Contactor to 132-2"),
     ("Which one do you mean?", "Which one do you mean?"),
     ("forty two items", "42 items"), ("How is everyone", "How is everyone")],
)  # fmt: skip
def test_spoken_codes_are_written_as_codes(heard: str, shown: str) -> None:
    """Voice test 2026-10-05: "one thirty two dash one" was not found as a warehouse."""
    assert speech.tidy_transcript(heard) == shown
