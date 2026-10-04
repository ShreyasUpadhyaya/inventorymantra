"""Voice input (C8.1): push-to-talk audio in, editable transcript out.

GET  /voice/status       {"server": bool}: is a server-side provider configured?
POST /voice/transcribe?lang=auto|en|hi   raw audio (webm, ogg, mp4, mpeg, wav) -> {text, provider}

The transcript is never sent to the chat from here: the browser puts it in the message box,
the user checks or edits it, and sends it like typed text (so a misheard word can't trigger a
change without the user seeing it). Signed-in users only; rate-limited per user.
"""

import logging

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.api.deps import CurrentUser
from app.api.routes.chat import PerUserLimit
from app.services.speech import (
    AUDIO_TYPES,
    MAX_AUDIO_BYTES,
    SpeechError,
    SpokenLanguage,
    get_stt,
)

router = APIRouter(prefix="/voice", tags=["voice"])
logger = logging.getLogger("inventorymantra.voice")
voice_limit = PerUserLimit("voice_requests_per_minute")


class VoiceStatus(BaseModel):
    server: bool


class TranscriptOut(BaseModel):
    text: str
    provider: str


@router.get("/status", response_model=VoiceStatus)
def voice_status(_: CurrentUser) -> VoiceStatus:
    return VoiceStatus(server=get_stt() is not None)


@router.post("/transcribe", response_model=TranscriptOut)
async def transcribe(
    request: Request, user: CurrentUser, lang: SpokenLanguage = "auto"
) -> TranscriptOut:
    stt = get_stt()
    if stt is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Voice input is not set up.")
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type not in AUDIO_TYPES:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Send audio, please.")
    if not voice_limit.allow(user.id):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many recordings. Please wait.")
    audio = bytearray()
    async for chunk in request.stream():  # stop reading as soon as it's too big
        audio += chunk
        if len(audio) > MAX_AUDIO_BYTES:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "That recording is too long.")
    if not audio:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The recording was empty.")
    try:  # the provider call blocks: keep it off the event loop
        result = await run_in_threadpool(stt.transcribe, bytes(audio), content_type, lang)
    except SpeechError as exc:
        logger.warning("transcription failed (%s)", stt.name)  # no audio, no key, no text
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    if not result.text:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "I couldn't hear any words.")
    return TranscriptOut(text=result.text[:2000], provider=result.provider)
