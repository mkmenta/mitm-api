
import pytest
from unittest.mock import MagicMock, AsyncMock
import httpx
import main
from fastapi.testclient import TestClient

@pytest.mark.asyncio
async def test_redirection_v1_chat_completions(async_client, monkeypatch):
    # Mock the redirect endpoint
    monkeypatch.setattr(main, "redirect_endpoint", "http://mock-upstream.com")
    
    # Mock httpx.AsyncClient.stream
    # We need to mock the context manager returned by stream
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.headers = httpx.Headers({"content-type": "application/json"})
    
    async def mock_aiter_bytes():
        yield b'{"status": "ok"}'
        return
    
    mock_response.aiter_bytes = mock_aiter_bytes
    
    # Mock stream_ctx
    mock_stream_ctx = AsyncMock()
    mock_stream_ctx.__aenter__.return_value = mock_response
    
    # Patch httpx.AsyncClient.stream
    mock_stream = MagicMock(return_value=mock_stream_ctx)
    monkeypatch.setattr(httpx.AsyncClient, "stream", mock_stream)
    
    # Patch decompress_body to avoid issues
    monkeypatch.setattr(main, "decompress_body", lambda x, y: x)
    
    # We also need to mock save_request to avoid file system ops during test
    monkeypatch.setattr(main, "save_request", AsyncMock())

    # Perform the request
    response = await async_client.post("/v1/chat/completions", json={"test": "data"})
    
    # Verify that it was redirected to /v1/responses
    assert mock_stream.called
    args, kwargs = mock_stream.call_args
    assert kwargs["url"] == "http://mock-upstream.com/v1/responses"

@pytest.mark.asyncio
async def test_no_redirection_other_paths(async_client, monkeypatch):
    # Mock the redirect endpoint
    monkeypatch.setattr(main, "redirect_endpoint", "http://mock-upstream.com")
    
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.headers = httpx.Headers({"content-type": "application/json"})
    
    async def mock_aiter_bytes():
        yield b'{"status": "ok"}'
        return
    
    mock_response.aiter_bytes = mock_aiter_bytes
    
    mock_stream_ctx = AsyncMock()
    mock_stream_ctx.__aenter__.return_value = mock_response
    
    mock_stream = MagicMock(return_value=mock_stream_ctx)
    monkeypatch.setattr(httpx.AsyncClient, "stream", mock_stream)
    monkeypatch.setattr(main, "save_request", AsyncMock())

    # Perform a request to another path
    await async_client.post("/v1/other", json={"test": "data"})
    
    # Verify that it was NOT redirected
    assert mock_stream.called
    args, kwargs = mock_stream.call_args
    assert kwargs["url"] == "http://mock-upstream.com/v1/other"
