
import os
import sys
import shutil
import pytest
from pathlib import Path
from typing import AsyncGenerator, Generator
from fastapi.testclient import TestClient
import httpx
import base64

# Add root directory to sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main
import utils

# Constants for testing
TEST_USERNAME = "admin"
TEST_PASSWORD = "secret"
TEST_PERSISTENCE_DIR = "test_data"

@pytest.fixture(scope="session", autouse=True)
def setup_test_env():
    """Set up the test environment variables globally."""
    # We patch global variables in modules, but environment variables are also good to set
    os.environ["ADMIN_USERNAME"] = TEST_USERNAME
    os.environ["ADMIN_PASSWORD"] = TEST_PASSWORD
    yield

@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Reset application state before each test."""
    # Patch auth credentials
    monkeypatch.setattr(utils, "ADMIN_USERNAME", TEST_USERNAME)
    monkeypatch.setattr(utils, "ADMIN_PASSWORD", TEST_PASSWORD)
    
    # Use a separate persistence directory for tests
    monkeypatch.setattr(main, "PERSISTENCE_DIR", TEST_PERSISTENCE_DIR)
    monkeypatch.setattr(main, "METADATA_FILE", os.path.join(TEST_PERSISTENCE_DIR, "metadata.json"))

    # Reset in-memory state
    main.requests_history = []
    main.analyses = {}
    main.current_analysis_id = None
    main.redirect_endpoint = None
    
    # Clean up file system
    if os.path.exists(TEST_PERSISTENCE_DIR):
        shutil.rmtree(TEST_PERSISTENCE_DIR)
        
    yield
    
    # Teardown
    if os.path.exists(TEST_PERSISTENCE_DIR):
        shutil.rmtree(TEST_PERSISTENCE_DIR)
    
    main.requests_history = []
    main.analyses = {}
    main.current_analysis_id = None
    main.redirect_endpoint = None

@pytest.fixture
def auth_headers() -> dict[str, str]:
    """Return Basic Auth headers."""
    token = base64.b64encode(f"{TEST_USERNAME}:{TEST_PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {token}"}

@pytest.fixture
def auth_tuple() -> tuple[str, str]:
    """Return Basic Auth tuple for TestClient."""
    return (TEST_USERNAME, TEST_PASSWORD)

@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    """Return a synchronous TestClient."""
    with TestClient(main.app) as c:
        yield c

@pytest.fixture
async def async_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """Return an asynchronous httpx client."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app),
        base_url="http://testserver",
        timeout=10.0,
    ) as c:
        yield c
