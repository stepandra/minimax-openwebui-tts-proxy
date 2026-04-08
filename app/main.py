from __future__ import annotations

import asyncio
import binascii
import os
import re
from typing import Any, Literal

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field

load_dotenv()

MINIMAX_API_BASE = os.getenv("MINIMAX_API_BASE", "https://api.minimax.io/v1")
MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "")
DEFAULT_MODEL = os.getenv("MINIMAX_TTS_MODEL", "speech-2.8-turbo")
DEFAULT_VOICE = os.getenv("MINIMAX_TTS_VOICE", "English_expressive_narrator")
DEFAULT_FORMAT = os.getenv("MINIMAX_TTS_FORMAT", "mp3")
DEFAULT_SAMPLE_RATE = int(os.getenv("MINIMAX_TTS_SAMPLE_RATE", "32000"))
DEFAULT_BITRATE = int(os.getenv("MINIMAX_TTS_BITRATE", "128000"))
DEFAULT_CHANNELS = int(os.getenv("MINIMAX_TTS_CHANNELS", "1"))
DEFAULT_SPEED = float(os.getenv("MINIMAX_TTS_SPEED", "1.0"))
DEFAULT_VOLUME = float(os.getenv("MINIMAX_TTS_VOLUME", "1.0"))
DEFAULT_PITCH = float(os.getenv("MINIMAX_TTS_PITCH", "0.0"))
SYNC_CHAR_LIMIT = int(os.getenv("MINIMAX_SYNC_CHAR_LIMIT", "9500"))
ASYNC_POLL_INTERVAL = float(os.getenv("MINIMAX_ASYNC_POLL_INTERVAL", "1.5"))
ASYNC_TIMEOUT_SECONDS = float(os.getenv("MINIMAX_ASYNC_TIMEOUT_SECONDS", "180"))
VOICE_TYPE = os.getenv("MINIMAX_VOICE_TYPE", "all")
REQUEST_TIMEOUT = float(os.getenv("MINIMAX_HTTP_TIMEOUT", "120"))

AUDIO_MIME_TYPES = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "flac": "audio/flac",
    "pcm": "audio/L16",
}

SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。！？\n])\s+")


class SpeechRequest(BaseModel):
    model: str | None = None
    input: str = Field(min_length=1)
    voice: str | None = None
    response_format: Literal["mp3", "wav", "flac", "pcm"] | None = None
    speed: float | None = None


class MiniMaxProxy:
    def __init__(self) -> None:
        self.timeout = httpx.Timeout(REQUEST_TIMEOUT)

    def _headers(self) -> dict[str, str]:
        if not MINIMAX_API_KEY:
            raise HTTPException(status_code=500, detail="MINIMAX_API_KEY is not configured")
        return {
            "Authorization": f"Bearer {MINIMAX_API_KEY}",
            "Content-Type": "application/json",
        }

    async def list_voices(self) -> list[str]:
        url = f"{MINIMAX_API_BASE}/get_voice"
        payload = {"voice_type": VOICE_TYPE}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, headers=self._headers(), json=payload)
        if resp.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"MiniMax get_voice failed: {resp.text}")
        data = resp.json()
        voices: list[str] = []
        for key in ("system_voice", "voice_cloning", "voice_generation"):
            for voice in data.get(key, []) or []:
                voice_id = voice.get("voice_id")
                if voice_id:
                    voices.append(voice_id)
        if not voices and DEFAULT_VOICE:
            voices = [DEFAULT_VOICE]
        return sorted(set(voices))

    async def sync_tts(self, text: str, *, model: str, voice: str, audio_format: str, speed: float | None) -> bytes:
        url = f"{MINIMAX_API_BASE}/t2a_v2"
        payload: dict[str, Any] = {
            "model": model,
            "text": text,
            "stream": False,
            "language_boost": "auto",
            "voice_setting": {
                "voice_id": voice,
                "speed": speed if speed is not None else DEFAULT_SPEED,
                "vol": DEFAULT_VOLUME,
                "pitch": DEFAULT_PITCH,
            },
            "audio_setting": {
                "sample_rate": DEFAULT_SAMPLE_RATE,
                "bitrate": DEFAULT_BITRATE,
                "format": audio_format,
                "channel": DEFAULT_CHANNELS,
            },
            "output_format": "hex",
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, headers=self._headers(), json=payload)
        if resp.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"MiniMax sync TTS failed: {resp.text}")
        body = resp.json()
        status_code = body.get("base_resp", {}).get("status_code", 0)
        if status_code != 0:
            raise HTTPException(status_code=502, detail=f"MiniMax sync TTS error: {body}")
        audio_hex = (body.get("data") or {}).get("audio")
        if not audio_hex:
            raise HTTPException(status_code=502, detail=f"MiniMax sync TTS returned no audio: {body}")
        try:
            return binascii.unhexlify(audio_hex)
        except binascii.Error as exc:
            raise HTTPException(status_code=502, detail=f"Invalid audio payload from MiniMax: {exc}") from exc

    async def async_tts(self, text: str, *, model: str, voice: str, audio_format: str) -> bytes:
        create_url = f"{MINIMAX_API_BASE}/t2a_async_v2"
        payload: dict[str, Any] = {
            "model": model,
            "text": text,
            "language_boost": "auto",
            "voice_setting": {
                "voice_id": voice,
                "speed": DEFAULT_SPEED,
                "vol": DEFAULT_VOLUME,
                "pitch": DEFAULT_PITCH,
            },
            "audio_setting": {
                "audio_sample_rate": DEFAULT_SAMPLE_RATE,
                "bitrate": DEFAULT_BITRATE,
                "format": audio_format,
                "channel": DEFAULT_CHANNELS,
            },
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            create_resp = await client.post(create_url, headers=self._headers(), json=payload)
            if create_resp.status_code >= 400:
                raise HTTPException(status_code=502, detail=f"MiniMax async create failed: {create_resp.text}")
            create_body = create_resp.json()
            task_id = (create_body.get("data") or {}).get("task_id")
            if not task_id:
                raise HTTPException(status_code=502, detail=f"MiniMax async create returned no task_id: {create_body}")

            deadline = asyncio.get_running_loop().time() + ASYNC_TIMEOUT_SECONDS
            query_url = f"{MINIMAX_API_BASE}/query/t2a_async_query_v2"
            file_id: str | None = None
            last_body: dict[str, Any] | None = None
            while asyncio.get_running_loop().time() < deadline:
                query_resp = await client.get(query_url, headers=self._headers(), params={"task_id": task_id})
                if query_resp.status_code >= 400:
                    raise HTTPException(status_code=502, detail=f"MiniMax async query failed: {query_resp.text}")
                last_body = query_resp.json()
                data = last_body.get("data") or {}
                status = data.get("status")
                file_id = data.get("file_id") or file_id
                if status in ("Success", "success", 2) and file_id:
                    break
                if status in ("Fail", "fail", "failed", 3):
                    raise HTTPException(status_code=502, detail=f"MiniMax async task failed: {last_body}")
                await asyncio.sleep(ASYNC_POLL_INTERVAL)

            if not file_id:
                raise HTTPException(status_code=504, detail=f"MiniMax async task timed out: {last_body}")

            retrieve_url = f"{MINIMAX_API_BASE}/files/retrieve_content"
            retrieve_resp = await client.get(retrieve_url, headers={"Authorization": self._headers()["Authorization"]}, params={"file_id": file_id})
            if retrieve_resp.status_code >= 400:
                raise HTTPException(status_code=502, detail=f"MiniMax file retrieve failed: {retrieve_resp.text}")
            return retrieve_resp.content

    async def tts(self, text: str, *, model: str, voice: str, audio_format: str, speed: float | None) -> bytes:
        if len(text) <= SYNC_CHAR_LIMIT:
            return await self.sync_tts(text, model=model, voice=voice, audio_format=audio_format, speed=speed)
        return await self.async_tts(text, model=model, voice=voice, audio_format=audio_format)


def split_text_for_models(text: str, limit: int = 10000) -> list[str]:
    text = text.strip()
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    current = ""
    for sentence in SENTENCE_SPLIT_RE.split(text):
        sentence = sentence.strip()
        if not sentence:
            continue
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            parts.append(current)
            current = ""
        while len(sentence) > limit:
            parts.append(sentence[:limit])
            sentence = sentence[limit:]
        current = sentence
    if current:
        parts.append(current)
    return parts or [text[:limit]]


proxy = MiniMaxProxy()
app = FastAPI(title="MiniMax OpenAI-compatible TTS Proxy", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/models")
@app.get("/v1/models")
@app.get("/audio/models")
@app.get("/v1/audio/models")
async def models() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {"id": "speech-2.8-hd", "object": "model", "owned_by": "minimax"},
            {"id": "speech-2.8-turbo", "object": "model", "owned_by": "minimax"},
            {"id": "speech-2.6-hd", "object": "model", "owned_by": "minimax"},
            {"id": "speech-2.6-turbo", "object": "model", "owned_by": "minimax"},
            {"id": "speech-02-hd", "object": "model", "owned_by": "minimax"},
            {"id": "speech-02-turbo", "object": "model", "owned_by": "minimax"},
        ],
    }


@app.get("/audio/voices")
@app.get("/v1/audio/voices")
async def audio_voices() -> dict[str, list[str]]:
    voices = await proxy.list_voices()
    return {"voices": voices}


@app.post("/audio/speech")
@app.post("/v1/audio/speech")
async def audio_speech(req: SpeechRequest) -> Response:
    model = req.model or DEFAULT_MODEL
    voice = req.voice or DEFAULT_VOICE
    audio_format = req.response_format or DEFAULT_FORMAT
    if audio_format not in AUDIO_MIME_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported response_format: {audio_format}")

    chunks = split_text_for_models(req.input, limit=10000)
    audio_parts: list[bytes] = []
    for chunk in chunks:
        audio_parts.append(
            await proxy.tts(chunk, model=model, voice=voice, audio_format=audio_format, speed=req.speed)
        )

    return Response(content=b"".join(audio_parts), media_type=AUDIO_MIME_TYPES[audio_format])
