
import os
import shutil
import json
import pytest
from fastapi.testclient import TestClient
import main

import utils

# Helper to clear persistence directory
@pytest.fixture(autouse=True)
def clean_persistence(monkeypatch):
    # Setup env vars for auth (for verify_credentials runtime check if it used os.getenv again, but it uses globals)
    # Patch the globals in utils module since they are read at import time
    monkeypatch.setattr(utils, "ADMIN_USERNAME", "admin")
    monkeypatch.setattr(utils, "ADMIN_PASSWORD", "secret")
    
    # Setup persistence
    if os.path.exists(main.PERSISTENCE_DIR):
        shutil.rmtree(main.PERSISTENCE_DIR)
    main.requests_history = []
    
    yield
    
    # Teardown
    if os.path.exists(main.PERSISTENCE_DIR):
        shutil.rmtree(main.PERSISTENCE_DIR)

def test_persistence_lifecycle():
    with TestClient(main.app) as client:
        auth = ("admin", "secret")
        # 1. Configure endpoint
        resp = client.post("/___configure", data={"endpoint": "https://httpbin.org"}, auth=auth)
        assert resp.status_code == 200
        
        # 2. Make a request
        # The catch_all endpoint does NOT require auth, but configure does.
        # Wait, let's double check catch_all in main.py.
        # @app.api_route("/{path:path}"...)
        # async def catch_all(request: Request, path: str):
        # DOES NOT have Depends(verify_credentials).
        
        response = client.get("/get?foo=bar")
        # If catch_all forwarded to httpbin.org, it should be 200.
        # But wait, TestClient won't actually hit external httpbin.org unless we mock httpx.AsyncClient or allow it.
        # The code executes: client = httpx.AsyncClient(...) -> client.stream(...)
        # This will try to make a real network request.
        # Since we just want to test persistence, we should probably mock the httpx client request or expect it to work if network is allowed 
        # (but using real httpbin.org is flaky).
        # However, verifying persistence happens BEFORE the response returns (partially).
        # Actually save_request is called:
        # 1. After request append (line 329)
        # 2. After response append (line 401)
        
        # If the external request fails (502), persistence of the REQUEST still happens.
        # Let's handle generic response, maybe 502 if network fails, or 200 if it works.
        # We just assert that persistence file exists.
        
        assert response.status_code in [200, 502]
        
        # 3. Verify file is created
        expected_file = os.path.join(main.PERSISTENCE_DIR, "0.json")
        assert os.path.exists(expected_file)
        
        with open(expected_file) as f:
            data = json.load(f)
            assert data["path"] == "/get"
            assert data["query_params"]["foo"] == "bar"

def test_load_history_on_startup():
    # 1. Create some dummy history files manually
    if not os.path.exists(main.PERSISTENCE_DIR):
        os.makedirs(main.PERSISTENCE_DIR)
    
    dummy_data = {"path": "/loaded-from-disk", "timestamp": "2023-01-01"}
    with open(os.path.join(main.PERSISTENCE_DIR, "0.json"), "w") as f:
        json.dump(dummy_data, f)
        
    # 2. Reset in-memory history
    main.requests_history = []
    
    # 3. Simulate app startup (lifespan)
    # We can invoke load_history directly for testing since lifespan is async generator
    # and TestClient handles lifespan but we want to be explicit or trust TestClient
    
    with TestClient(main.app) as client:
        # TestClient with lifespan should verify startup
        assert len(main.requests_history) == 1
        assert main.requests_history[0]["path"] == "/loaded-from-disk"
