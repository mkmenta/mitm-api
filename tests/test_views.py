import base64
import importlib
import os
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main  # isort:skip
import utils  # isort:skip


ADMIN_USERNAME = "testuser"
ADMIN_PASSWORD = "testpass"


def _basic_auth_header() -> str:
    token = base64.b64encode(f"{ADMIN_USERNAME}:{ADMIN_PASSWORD}".encode()).decode()
    return f"Basic {token}"


@pytest.fixture(autouse=True)
def auth_setup(monkeypatch):
    """Ensure basic auth values are consistent and reset global state."""
    monkeypatch.setattr(utils, "ADMIN_USERNAME", ADMIN_USERNAME)
    monkeypatch.setattr(utils, "ADMIN_PASSWORD", ADMIN_PASSWORD)
    main.redirect_endpoint = None
    main.requests_history.clear()
    yield
    main.redirect_endpoint = None
    main.requests_history.clear()


@pytest.mark.asyncio
async def test_get_configure_page():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app),
        base_url="http://testserver",
        timeout=10.0,
    ) as client:
        response = await client.get("/___configure", headers={"Authorization": _basic_auth_header()})
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_post_configure_page_stores_endpoint():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app),
        base_url="http://testserver",
        timeout=10.0,
    ) as client:
        response = await client.post(
            "/___configure",
            data={"endpoint": "https://example.com"},
            headers={"Authorization": _basic_auth_header()},
        )
    assert response.status_code == 200
    assert main.redirect_endpoint == "https://example.com"


@pytest.mark.asyncio
async def test_view_last_without_history_returns_404():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app),
        base_url="http://testserver",
        timeout=10.0,
    ) as client:
        response = await client.get("/___view_last/1", headers={"Authorization": _basic_auth_header()})
    assert response.status_code == 404

