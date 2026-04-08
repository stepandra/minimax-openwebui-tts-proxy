import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app, split_text_for_models, _minimax_int_param


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/models", "/v1/audio/models"])
async def test_models_endpoint(path: str):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(path)
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "list"
    assert any(model["id"] == "speech-2.8-turbo" for model in body["data"])


@pytest.mark.asyncio
async def test_invalid_response_format_rejected():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/v1/audio/speech",
            json={"input": "hello", "response_format": "ogg"},
        )
    assert resp.status_code == 422
    assert "response_format" in resp.text


def test_split_text_for_models_short_text():
    assert split_text_for_models("hello", limit=10) == ["hello"]


def test_split_text_for_models_long_text():
    text = "One. Two. Three. Four. Five."
    parts = split_text_for_models(text, limit=10)
    assert len(parts) >= 2
    assert all(len(part) <= 10 for part in parts)


def test_minimax_int_param_accepts_integer_like_float():
    assert _minimax_int_param(1.0, name="speed") == 1


def test_minimax_int_param_rejects_non_integer_float():
    with pytest.raises(Exception):
        _minimax_int_param(1.25, name="speed")
