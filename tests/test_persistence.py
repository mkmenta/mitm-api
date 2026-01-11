
import os
import json
import main

def test_persistence_lifecycle(client, auth_tuple):
    # 1. Configure endpoint
    resp = client.post(
        "/___configure", 
        data={
            "endpoint": "https://httpbin.org",
            "action": "create",
            "title": "Persistence Test"
        }, 
        auth=auth_tuple
    )
    assert resp.status_code == 200
    
    # 2. Make a request
    response = client.get("/get?foo=bar")
    
    # Check status (200 or 502 depending on network)
    assert response.status_code in [200, 502]
    
    # 3. Verify file is created
    # Analysis ID is generated in configure step.
    # We need to get it from main.current_analysis_id
    analysis_id = main.current_analysis_id
    assert analysis_id is not None
    
    expected_file = os.path.join(main.get_analysis_dir(analysis_id), "0.json")
    assert os.path.exists(expected_file)
    
    with open(expected_file) as f:
        data = json.load(f)
        assert data["path"] == "/get"
        assert data["query_params"]["foo"] == "bar"

def test_load_history_on_startup():
    # 1. Create some dummy history files manually
    # Note: We need a valid analysis first or just inject into history loading logic.
    # main.load_history() checks current_analysis_id.
    
    # Let's set up a fake analysis
    analysis_id = "test-analysis-load"
    main.analyses[analysis_id] = {"id": analysis_id, "title": "Test", "endpoint": "http://example.com"}
    main.current_analysis_id = analysis_id
    
    analysis_dir = main.get_analysis_dir(analysis_id)
    if not os.path.exists(analysis_dir):
        os.makedirs(analysis_dir)
    
    dummy_data = {"path": "/loaded-from-disk", "timestamp": "2023-01-01"}
    with open(os.path.join(analysis_dir, "0.json"), "w") as f:
        json.dump(dummy_data, f)
        
    # 2. Reset in-memory history
    main.requests_history = []
    
    # 3. Simulate app startup (lifespan)
    # We use a new TestClient to trigger lifespan
    from fastapi.testclient import TestClient
    
    # We need to make sure the NEW client uses the SAME patched persistence dir.
    # Our conftest patch applies to 'main' module, which is shared.
    
    with TestClient(main.app) as client:
        # TestClient with lifespan should verify startup
        assert len(main.requests_history) == 1
        assert main.requests_history[0]["path"] == "/loaded-from-disk"
