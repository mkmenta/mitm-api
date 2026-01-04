import importlib
import os
import sys
from pathlib import Path
from typing import Dict

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

main = importlib.import_module("main")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = "https://api.openai.com"
OPENAI_URL = f"{OPENAI_BASE_URL}/v1/chat/completions"


def _auth_headers() -> Dict[str, str]:
    assert OPENAI_API_KEY, "OPENAI_API_KEY must be set for integration tests"
    return {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }


def _normalize_headers(headers: httpx.Headers) -> Dict[str, str]:
    """Lowercase header names and strip headers that the proxy removes."""
    excluded = {
        "content-length",
        "transfer-encoding",
        "content-encoding",
        "date",
        "cf-ray",
        "cf-request-id",
        "set-cookie",
        "x-request-id",
        "x-envoy-upstream-service-time",
        "openai-processing-ms",
        "x-ratelimit-remaining-tokens",
    }
    return {
        name.lower(): value
        for name, value in headers.items()
        if name.lower() not in excluded
    }


async def _drain_stream(response: httpx.Response, *, max_events: int = 50) -> None:
    """Pull enough streaming SSE events to reach the terminal marker."""
    seen_done = False
    async for line in response.aiter_lines():
        if not line:
            continue
        if line.strip() == "data: [DONE]":
            seen_done = True
            break
        max_events -= 1
        if max_events <= 0:
            break
    if not seen_done:
        # Ensure the stream is exhausted before closing
        await response.aclose()


@pytest.fixture(autouse=True)
def configure_proxy_target(monkeypatch):
    if not OPENAI_API_KEY:
        pytest.skip("OPENAI_API_KEY not set; skipping OpenAI integration tests")
    monkeypatch.setattr(main, "redirect_endpoint", OPENAI_BASE_URL, raising=False)
    main.requests_history.clear()
    yield
    main.requests_history.clear()


@pytest.mark.asyncio
async def test_non_streaming_response_metadata_matches_direct_call():
    """Ensure proxied non- streaming responses match headers and structure."""
    payload = {
        "model": "gpt-5-mini",
        "messages": [{"role": "user", "content": "Ping test through proxy"}],
        "max_completion_tokens": 1,
    }
    headers = _auth_headers()

    async with httpx.AsyncClient(timeout=60.0) as api_client:
        direct_resp = await api_client.post(OPENAI_URL, json=payload, headers=headers)
        direct_keys = set(direct_resp.json().keys())
        direct_status = direct_resp.status_code
        direct_headers = _normalize_headers(direct_resp.headers)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app),
        base_url="http://testserver",
        timeout=60.0,
    ) as proxy_client:
        proxy_resp = await proxy_client.post("/v1/chat/completions", json=payload, headers=headers)
        proxy_keys = set(proxy_resp.json().keys())
        proxy_status = proxy_resp.status_code
        proxy_headers = _normalize_headers(proxy_resp.headers)

    assert proxy_status == direct_status
    assert proxy_headers == direct_headers
    assert proxy_keys == direct_keys


@pytest.mark.asyncio
async def test_streaming_response_metadata_matches_direct_call():
    """Compare headers/status for streaming responses to OpenAI."""
    payload = {
        "model": "gpt-5-mini",
        "messages": [{"role": "user", "content": "Streaming ping"}],
        "max_completion_tokens": 1,
        "stream": True,
    }
    headers = _auth_headers()

    async with httpx.AsyncClient(timeout=60.0) as api_client:
        async with api_client.stream("POST", OPENAI_URL, json=payload, headers=headers) as direct_stream:
            direct_status = direct_stream.status_code
            direct_headers = _normalize_headers(direct_stream.headers)
            await _drain_stream(direct_stream)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app),
        base_url="http://testserver",
        timeout=60.0,
    ) as proxy_client:
        async with proxy_client.stream("POST", "/v1/chat/completions", json=payload, headers=headers) as proxy_stream:
            proxy_status = proxy_stream.status_code
            proxy_headers = _normalize_headers(proxy_stream.headers)
            await _drain_stream(proxy_stream)

    assert proxy_status == direct_status
    assert proxy_headers == direct_headers

