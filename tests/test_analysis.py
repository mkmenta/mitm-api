import os
import json
import main
from fastapi.testclient import TestClient

def test_create_analysis(client, auth_tuple):
    """Test creating a new analysis."""
    # Create an analysis
    response = client.post(
        "/___configure",
        data={
            "action": "create",
            "title": "Test Analysis",
            "endpoint": "https://httpbin.org"
        },
        auth=auth_tuple
    )
    
    assert response.status_code == 200
    
    # Verify analysis was created in memory
    assert len(main.analyses) == 1
    analysis = list(main.analyses.values())[0]
    assert analysis["title"] == "Test Analysis"
    assert analysis["endpoint"] == "https://httpbin.org"
    assert "id" in analysis
    assert "created_at" in analysis
    
    # Verify it's set as current
    assert main.current_analysis_id == analysis["id"]
    assert main.redirect_endpoint == "https://httpbin.org"
    
    # Verify metadata file was created
    assert os.path.exists(main.METADATA_FILE)
    with open(main.METADATA_FILE) as f:
        metadata = json.load(f)
        assert analysis["id"] in metadata["analyses"]
        assert metadata["analyses"][analysis["id"]]["title"] == "Test Analysis"
    
    # Verify analysis folder was created
    analysis_dir = main.get_analysis_dir(analysis["id"])
    assert os.path.exists(analysis_dir)


def test_create_analysis_validation(client, auth_tuple):
    """Test analysis creation with invalid inputs."""
    # Missing title
    response = client.post(
        "/___configure",
        data={
            "action": "create",
            "endpoint": "https://httpbin.org"
        },
        auth=auth_tuple
    )
    assert response.status_code == 400
    
    # Missing endpoint
    response = client.post(
        "/___configure",
        data={
            "action": "create",
            "title": "Test"
        },
        auth=auth_tuple
    )
    assert response.status_code == 400


def test_switch_analysis(client, auth_tuple):
    """Test switching between analyses."""
    # Create first analysis
    client.post(
        "/___configure",
        data={
            "action": "create",
            "title": "Analysis 1",
            "endpoint": "https://httpbin.org"
        },
        auth=auth_tuple
    )
    analysis1_id = main.current_analysis_id
    
    # Create second analysis
    client.post(
        "/___configure",
        data={
            "action": "create",
            "title": "Analysis 2",
            "endpoint": "https://api.example.com"
        },
        auth=auth_tuple
    )
    analysis2_id = main.current_analysis_id
    
    # Verify we're currently on analysis 2
    assert main.current_analysis_id == analysis2_id
    assert main.redirect_endpoint == "https://api.example.com"
    
    # Switch back to analysis 1
    response = client.post(
        "/___configure",
        data={
            "action": "switch",
            "analysis_id": analysis1_id
        },
        auth=auth_tuple
    )
    assert response.status_code == 200
    
    # Verify switch was successful
    assert main.current_analysis_id == analysis1_id
    assert main.redirect_endpoint == "https://httpbin.org"


def test_switch_to_nonexistent_analysis(client, auth_tuple):
    """Test switching to an analysis that doesn't exist."""
    response = client.post(
        "/___configure",
        data={
            "action": "switch",
            "analysis_id": "nonexistent-id"
        },
        auth=auth_tuple
    )
    assert response.status_code == 404


def test_request_persistence_per_analysis(client, auth_tuple):
    """Test that requests are saved to the correct analysis folder."""
    # Create an analysis
    client.post(
        "/___configure",
        data={
            "action": "create",
            "title": "Test Analysis",
            "endpoint": "https://httpbin.org"
        },
        auth=auth_tuple
    )
    analysis_id = main.current_analysis_id
    
    # Make a request (will try to forward to httpbin.org)
    response = client.get("/get?key=value")
    # May be 200 if network works, or 502 if it fails
    assert response.status_code in [200, 502]
    
    # Verify request was saved in analysis folder
    analysis_dir = main.get_analysis_dir(analysis_id)
    files = [f for f in os.listdir(analysis_dir) if f.endswith(".json")]
    assert len(files) == 1
    request_file = os.path.join(analysis_dir, files[0])
    assert os.path.exists(request_file)
    
    # Verify content
    with open(request_file) as f:
        data = json.load(f)
        assert data["path"] == "/get"
        assert data["query_params"]["key"] == "value"
        assert data["method"] == "GET"


def test_requests_separate_by_analysis(client, auth_tuple):
    """Test that requests are kept separate between different analyses."""
    # Create first analysis
    client.post(
        "/___configure",
        data={
            "action": "create",
            "title": "Analysis 1",
            "endpoint": "https://httpbin.org"
        },
        auth=auth_tuple
    )
    analysis1_id = main.current_analysis_id
    
    # Make a request for analysis 1
    client.get("/request1")
    
    # Create second analysis
    client.post(
        "/___configure",
        data={
            "action": "create",
            "title": "Analysis 2",
            "endpoint": "https://httpbin.org"
        },
        auth=auth_tuple
    )
    analysis2_id = main.current_analysis_id
    
    # Make a request for analysis 2
    client.get("/request2")
    
    # Verify analysis 1 folder has only 1 request
    analysis1_dir = main.get_analysis_dir(analysis1_id)
    analysis1_files = [f for f in os.listdir(analysis1_dir) if f.endswith('.json')]
    assert len(analysis1_files) == 1
    
    # Verify analysis 2 folder has only 1 request
    analysis2_dir = main.get_analysis_dir(analysis2_id)
    analysis2_files = [f for f in os.listdir(analysis2_dir) if f.endswith('.json')]
    assert len(analysis2_files) == 1
    
    # Verify the requests are different
    file1 = os.listdir(analysis1_dir)[0]
    with open(os.path.join(analysis1_dir, file1)) as f:
        data1 = json.load(f)
        assert data1["path"] == "/request1"
    
    file2 = os.listdir(analysis2_dir)[0]
    with open(os.path.join(analysis2_dir, file2)) as f:
        data2 = json.load(f)
        assert data2["path"] == "/request2"


def test_load_analyses_on_startup():
    """Test that analyses are loaded from metadata on app startup."""
    # Create metadata file manually
    os.makedirs(main.PERSISTENCE_DIR, exist_ok=True)
    
    test_analysis = {
        "id": "test-123",
        "title": "Loaded Analysis",
        "endpoint": "https://example.com",
        "created_at": "2024-01-01T00:00:00"
    }
    
    metadata = {
        "analyses": {
            "test-123": test_analysis
        },
        "current_analysis_id": "test-123"
    }
    
    with open(main.METADATA_FILE, "w") as f:
        json.dump(metadata, f)
    
    # Also create the analysis directory
    os.makedirs(main.get_analysis_dir("test-123"), exist_ok=True)
    
    # Create a test request file in the analysis folder
    test_request = {
        "path": "/test",
        "method": "GET",
        "timestamp": "2024-01-01T00:00:00"
    }
    with open(os.path.join(main.get_analysis_dir("test-123"), "test-uuid.json"), "w") as f:
        json.dump(test_request, f)
    
    # Reset state
    main.analyses = {}
    main.current_analysis_id = None
    main.requests_history = []
    
    # Trigger load by creating a new client
    with TestClient(main.app):
        # Verify analysis was loaded
        assert "test-123" in main.analyses
        assert main.analyses["test-123"]["title"] == "Loaded Analysis"
        assert main.current_analysis_id == "test-123"
        
        # Verify history was loaded
        assert len(main.requests_history) == 1
        assert main.requests_history[0]["path"] == "/test"


def test_configure_page_shows_analyses(client, auth_tuple):
    """Test that the configure page displays all analyses."""
    # Create two analyses
    client.post(
        "/___configure",
        data={
            "action": "create",
            "title": "Analysis A",
            "endpoint": "https://a.com"
        },
        auth=auth_tuple
    )
    
    client.post(
        "/___configure",
        data={
            "action": "create",
            "title": "Analysis B",
            "endpoint": "https://b.com"
        },
        auth=auth_tuple
    )
    
    # Get configure page
    response = client.get("/___configure", auth=auth_tuple)
    assert response.status_code == 200
    
    # Verify both analyses are in the response
    html = response.text
    assert "Analysis A" in html
    assert "Analysis B" in html
    assert "https://a.com" in html
    assert "https://b.com" in html


def test_no_analysis_selected_prevents_requests(client):
    """Test that requests fail gracefully when no analysis is selected."""
    # Don't create or select any analysis (clean_env fixture ensures this)
    assert main.current_analysis_id is None
    
    # Try to make a request
    response = client.get("/test")
    assert response.status_code == 400
    assert "No redirect endpoint configured" in response.text


def test_view_request_shows_analysis_name(client, auth_tuple):
    """Test that viewing a request shows the analysis name."""
    # Create an analysis
    client.post(
        "/___configure",
        data={
            "action": "create",
            "title": "My Analysis",
            "endpoint": "https://httpbin.org"
        },
        auth=auth_tuple
    )
    
    # Make a request
    client.get("/test")
    
    # View the request
    response = client.get("/___view_last/1", auth=auth_tuple)
    assert response.status_code == 200
    assert "My Analysis" in response.text
