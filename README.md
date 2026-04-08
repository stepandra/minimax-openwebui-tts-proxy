# MiniMax OpenWebUI TTS Proxy

Small OpenAI-compatible TTS adapter for using **MiniMax TTS** inside **Open WebUI**.

## Why this instead of Open WebUI Functions?
Because Open WebUI TTS integration expects an external HTTP service. The Open WebUI custom-TTS discussion explicitly describes these endpoints:

- `GET /models`
- `GET /audio/voices`
- `POST /audio/speech`

A standalone proxy is the correct shape here. A Function can help with middleware logic, but it is not the right primitive for a real TTS backend.

## What this proxy does
- Exposes OpenAI-style TTS endpoints for Open WebUI
- Calls MiniMax synchronous TTS (`/v1/t2a_v2`) for normal chat-size text
- Falls back to MiniMax async long TTS (`/v1/t2a_async_v2`) for long text, polls task status, then downloads the audio
- Fetches available voices from MiniMax `POST /v1/get_voice`

## Quick start

```bash
cd /home/moonadmin/minimax-openwebui-tts-proxy
cp .env.example .env
# edit .env and set MINIMAX_API_KEY
uv sync
uv run uvicorn app.main:app --host 0.0.0.0 --port 8099
```

`app.main` now auto-loads `.env` from the project directory via `python-dotenv`, so a plain `uvicorn` launch is enough. If you prefer being explicit, this also works:

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8099 --env-file .env
```

## Test

```bash
cd /home/moonadmin/minimax-openwebui-tts-proxy
uv run pytest
```

## Open WebUI config

If your Open WebUI supports **Custom TTS**:
- Engine: `Custom TTS`
- Base URL: `http://<host>:8099/v1`
- API Key: any non-empty string for Open WebUI UI validation, or leave empty if your build allows it
- Model: `speech-2.8-turbo`
- Voice: e.g. `English_expressive_narrator`

If your build only supports **OpenAI** TTS-compatible servers:
- Engine: `OpenAI`
- API Base URL: `http://<host>:8099/v1`
- API Key: any dummy non-empty string if Open WebUI insists
- Model: `speech-2.8-turbo`
- Voice: `English_expressive_narrator`

## Example curl

```bash
curl -X POST http://127.0.0.1:8099/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "speech-2.8-turbo",
    "voice": "English_expressive_narrator",
    "input": "Привет, это проверка MiniMax TTS через Open WebUI proxy.",
    "response_format": "mp3"
  }' \
  --output sample.mp3
```

## Important
Your MiniMax API key was pasted into chat. Treat it as compromised and rotate/revoke it in MiniMax after you deploy the new key via environment variables.
