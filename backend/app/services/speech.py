"""Speech to text for push-to-talk (C8.1, DECISION D-055).

`SpeechToText` is the only interface the API sees, so the provider can change without
touching the chat: the transcript goes back to the browser, the user can edit it, and only
then is it sent through the normal /chat pipeline (same RBAC, clarification and charts).

Provider: Deepgram (`DEEPGRAM_API_KEY`; model and language from settings, default nova-3 with
`multi` for English / Hindi code-switching). Without a key the server says voice is not
available and the browser falls back to its own speech recognition (Web Speech API).
Audio is never stored or logged; neither is the key.
"""

import re
from dataclasses import dataclass
from typing import Literal, Protocol

import httpx

from app.core.config import get_settings

DEEPGRAM_URL = "https://api.deepgram.com/v1/listen"
MAX_AUDIO_BYTES = 5 * 1024 * 1024  # about 5 minutes of opus; a question takes seconds
# Domain words the recogniser should prefer (Nova-3 "keyterm" prompting). A test on the
# user's account showed "132-1" heard as "one three two dash one" without it (2026-10-05).
KEYTERMS = (
    "PO",
    "purchase order",
    "overdue",
    "vendor",
    "stock",
    "reorder",
    "warehouse",
    "margin",
    "132-1",
    "132-2",
    "132-3",
    "AC Contactor",
    "MS Bush",
    "SS sheet",
    "MS Tube",
    "Relay",
    "Sterling Steels",
    "Shakti Steels",
    "C84 FM assembly",
    "kaun se",
    "kitna stock",
    "dikhaiye",
)
AUDIO_TYPES = ("audio/webm", "audio/ogg", "audio/mp4", "audio/mpeg", "audio/wav", "audio/x-wav")


# What the user picks next to the mic. "auto" keeps the configured language (multi).
SpokenLanguage = Literal["auto", "en", "hi"]
DEEPGRAM_LANGUAGES: dict[str, str] = {"en": "en-IN", "hi": "hi"}

_UNITS = {"zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
          "seven": 7, "eight": 8, "nine": 9}  # fmt: skip
_TEENS = {"ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
          "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}  # fmt: skip
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
         "eighty": 80, "ninety": 90}  # fmt: skip
_NUMBER_WORD = "|".join([*_UNITS, *_TEENS, *_TENS])
_RUN = re.compile(rf"\b(?:{_NUMBER_WORD})(?:[\s-]+(?:{_NUMBER_WORD}))*\b", re.I)
_DASH = re.compile(r"(\d)\s+(?:dash|hyphen|minus)\s+(\d)", re.I)


def _digits(run: str) -> str:
    """'one thirty two' -> '132', 'one three two' -> '132', 'forty two' -> '42'."""
    words = [w.lower() for w in re.split(r"[\s-]+", run) if w]
    out, i = "", 0
    while i < len(words):
        word = words[i]
        if word in _TENS:
            nxt = words[i + 1] if i + 1 < len(words) else ""
            if nxt in _UNITS and _UNITS[nxt]:
                out, i = out + str(_TENS[word] + _UNITS[nxt]), i + 2
                continue
            out += str(_TENS[word])
        else:
            out += str(_TEENS.get(word, _UNITS.get(word, 0)))
        i += 1
    return out


def tidy_transcript(text: str) -> str:
    """Spoken codes as written: "one thirty two dash one" -> "132-1" (voice test 2026-10-05:
    the warehouse wasn't found). A lone number word stays ("which one do you mean")."""

    def convert(m: re.Match[str]) -> str:
        run = m.group(0)
        after = text[m.end() : m.end() + 8].lower()
        before = text[max(0, m.start() - 8) : m.start()].lower()
        near_dash = any(w in after or w in before for w in ("dash", "hyphen"))
        many = len(re.split(r"[\s-]+", run.strip())) > 1
        return _digits(run) if many or near_dash else run

    return _DASH.sub(r"\1-\2", _RUN.sub(convert, text))


class SpeechError(RuntimeError):
    """The provider failed or returned nothing usable (the message is safe to show)."""


@dataclass(frozen=True)
class Transcript:
    text: str
    provider: str


class SpeechToText(Protocol):
    name: str

    def transcribe(
        self, audio: bytes, content_type: str, language: SpokenLanguage = "auto"
    ) -> Transcript: ...


class DeepgramSTT:
    name = "deepgram"

    def __init__(self, api_key: str, model: str, language: str, timeout: float = 20.0) -> None:
        self._key, self._model, self._language, self._timeout = api_key, model, language, timeout

    def transcribe(
        self, audio: bytes, content_type: str, language: SpokenLanguage = "auto"
    ) -> Transcript:
        params: list[tuple[str, str | int | float | bool | None]] = [
            ("model", self._model),
            ("language", DEEPGRAM_LANGUAGES.get(language, self._language)),
            ("smart_format", "true"), ("punctuate", "true"),
        ]  # fmt: skip
        if self._model.startswith("nova-3"):
            params += [("keyterm", term) for term in KEYTERMS]
        try:
            response = httpx.post(
                DEEPGRAM_URL, params=params, content=audio, timeout=self._timeout,
                headers={"Authorization": f"Token {self._key}", "Content-Type": content_type},
            )  # fmt: skip
        except httpx.HTTPError as exc:
            raise SpeechError("The speech service could not be reached.") from exc
        if response.status_code != 200:
            raise SpeechError("The speech service could not transcribe that.")
        try:
            alt = response.json()["results"]["channels"][0]["alternatives"][0]
        except (KeyError, IndexError, ValueError) as exc:
            raise SpeechError("The speech service could not transcribe that.") from exc
        text = tidy_transcript(str(alt.get("transcript", "")).strip())
        return Transcript(text=text, provider=self.name)


def get_stt() -> SpeechToText | None:
    """The configured provider, or None (the browser then uses its own recognition)."""
    settings = get_settings()
    if settings.deepgram_api_key is None or not settings.deepgram_api_key.get_secret_value():
        return None
    return DeepgramSTT(settings.deepgram_api_key.get_secret_value(), settings.deepgram_model,
                       settings.deepgram_language)  # fmt: skip
