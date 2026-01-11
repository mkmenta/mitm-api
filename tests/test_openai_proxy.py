import os
import httpx
import pytest
import main

# Constants
OPENAI_BASE_URL = "https://api.openai.com"
OPENAI_URL = f"{OPENAI_BASE_URL}/v1/chat/completions"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

def _openai_headers() -> dict[str, str]:
    assert OPENAI_API_KEY, "OPENAI_API_KEY must be set for integration tests"
    return {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

def _normalize_headers(headers: httpx.Headers) -> dict[str, str]:
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

def _extract_all_keys(obj, prefix: str = "") -> set:
    """Recursively extract all keys from a JSON object (including nested ones)."""
    keys = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            full_key = f"{prefix}.{key}" if prefix else key
            keys.add(full_key)
            keys.update(_extract_all_keys(value, full_key))
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            keys.update(_extract_all_keys(item, f"{prefix}[{idx}]"))
    return keys

async def _extract_stream_keys(response: httpx.Response, *, max_events: int = 50) -> list[set]:
    """Extract keys from JSON objects in SSE stream events."""
    import json
    keys_list = []
    seen_done = False
    async for line in response.aiter_lines():
        if not line:
            continue
        if line.strip() == "data: [DONE]":
            seen_done = True
            break
        if line.startswith("data: "):
            try:
                json_str = line[6:].strip()  # Remove "data: " prefix
                if json_str:
                    data = json.loads(json_str)
                    if isinstance(data, dict):
                        keys_list.append(set(data.keys()))
            except (json.JSONDecodeError, ValueError):
                pass
        max_events -= 1
        if max_events <= 0:
            break
    if not seen_done:
        # Ensure the stream is exhausted before closing
        await response.aclose()
    return keys_list


@pytest.fixture(autouse=True)
def configure_proxy_target(monkeypatch):
    monkeypatch.setattr(main, "redirect_endpoint", OPENAI_BASE_URL)
    yield


@pytest.mark.asyncio
@pytest.mark.skipif(not OPENAI_API_KEY, reason="OPENAI_API_KEY not set")
async def test_non_streaming_response_metadata_matches_direct_call(async_client):
    """Ensure proxied non-streaming responses match headers and structure."""
    payload = {
        "model": "gpt-5-mini",
        "messages": [{"role": "user", "content": "Ping test through proxy"}],
        "max_completion_tokens": 1,
    }
    headers = _openai_headers()

    async with httpx.AsyncClient(timeout=60.0) as api_client:
        direct_resp = await api_client.post(OPENAI_URL, json=payload, headers=headers)
        direct_json = direct_resp.json()
        direct_top_keys = set(direct_json.keys())
        direct_all_keys = _extract_all_keys(direct_json)
        direct_status = direct_resp.status_code
        direct_headers = _normalize_headers(direct_resp.headers)

    # Use the async_client fixture for the proxy
    proxy_resp = await async_client.post("/v1/chat/completions", json=payload, headers=headers)
    proxy_json = proxy_resp.json()
    proxy_top_keys = set(proxy_json.keys())
    proxy_all_keys = _extract_all_keys(proxy_json)
    proxy_status = proxy_resp.status_code
    proxy_headers = _normalize_headers(proxy_resp.headers)

    assert proxy_status == direct_status
    assert proxy_headers == direct_headers
    assert proxy_top_keys == direct_top_keys, "Top-level keys should match"
    assert proxy_all_keys == direct_all_keys, "All keys (including nested) should match"


@pytest.mark.asyncio
@pytest.mark.skipif(not OPENAI_API_KEY, reason="OPENAI_API_KEY not set")
async def test_streaming_response_metadata_matches_direct_call(async_client):
    """Compare headers/status and content keys for streaming responses to OpenAI."""
    payload = {
        "model": "gpt-5-mini",
        "messages": [{"role": "user", "content": "Streaming ping"}],
        "max_completion_tokens": 1,
        "stream": True,
    }
    headers = _openai_headers()

    async with httpx.AsyncClient(timeout=60.0) as api_client:
        async with api_client.stream("POST", OPENAI_URL, json=payload, headers=headers) as direct_stream:
            direct_status = direct_stream.status_code
            direct_headers = _normalize_headers(direct_stream.headers)
            direct_keys_list = await _extract_stream_keys(direct_stream)

    async with async_client.stream("POST", "/v1/chat/completions", json=payload, headers=headers) as proxy_stream:
        proxy_status = proxy_stream.status_code
        proxy_headers = _normalize_headers(proxy_stream.headers)
        proxy_keys_list = await _extract_stream_keys(proxy_stream)

    assert proxy_status == direct_status
    assert proxy_headers == direct_headers
    assert len(proxy_keys_list) == len(direct_keys_list), "Number of events should match"
    for proxy_keys, direct_keys in zip(proxy_keys_list, direct_keys_list):
        assert proxy_keys == direct_keys, "Keys in each event should match"
