import os
import json
import main
from utils import redact_sensitive_data

def test_redact_sensitive_data_unit():
    """Unit tests for the redaction utility function."""
    # Test headers
    headers = {
        "Authorization": "Bearer sk-1234567890abcdef",
        "Content-Type": "application/json",
        "X-API-Key": "my-secret-key"
    }
    redacted = redact_sensitive_data(headers)
    assert redacted["Authorization"] == "Bearer [REDACTED]"
    assert redacted["X-API-Key"] == "[REDACTED]"
    assert redacted["Content-Type"] == "application/json"

def test_redact_sensitive_data_avoid_tokens():
    """Verify that 'tokens' (like in OpenAI usage) is not redacted, but 'token' is."""
    data = {
        "prompt_tokens": 10,
        "token": "sensitive-value"
    }
    redacted = redact_sensitive_data(data)
    assert redacted["prompt_tokens"] == 10
    assert redacted["token"] == "[REDACTED]"

def test_integration_redaction_headers_only(client, auth_tuple):
    """Test that only headers are redacted when the option is enabled, and bodies are ignored."""
    # Create analysis with redaction ENABLED
    client.post(
        "/___configure",
        data={
            "action": "create",
            "title": "Secure Analysis",
            "endpoint": "https://httpbin.org",
            "redact_sensitive": "true"
        },
        auth=auth_tuple
    )
    
    # Send a request with sensitive data in headers and body
    headers = {
        "Authorization": "Bearer my-secret-token",
        "X-Api-Key": "secret-key"
    }
    payload = {"password": "confidential", "username": "mike", "prompt_tokens": 50}
    
    client.post("/post", headers=headers, json=payload)
    
    # Check history
    assert len(main.requests_history) == 1
    recorded = main.requests_history[0]
    
    # Verify request headers recorded are redacted
    # Note: FastAPI/httpx might lowercase headers
    headers_keys = {k.lower(): v for k, v in recorded["headers"].items()}
    assert headers_keys["authorization"] == "Bearer [REDACTED]"
    assert headers_keys["x-api-key"] == "[REDACTED]"
    
    # CRITICAL: Verify request body recorded is NOT redacted anymore as requested
    assert recorded["body_json"]["password"] == "confidential"
    assert recorded["body_json"]["username"] == "mike"
    assert recorded["body_json"]["prompt_tokens"] == 50

def test_integration_redaction_disabled(client, auth_tuple):
    """Test that requests are NOT redacted when the option is disabled."""
    # Create analysis with redaction DISABLED (default)
    client.post(
        "/___configure",
        data={
            "action": "create",
            "title": "Unsecure Analysis",
            "endpoint": "https://httpbin.org",
            "redact_sensitive": "false"
        },
        auth=auth_tuple
    )
    
    # Send a request with sensitive data
    headers = {"Authorization": "Bearer my-secret-token"}
    payload = {"password": "confidential"}
    
    client.post("/post", headers=headers, json=payload)
    
    # Check history
    assert len(main.requests_history) == 1
    recorded = main.requests_history[0]
    
    # Verify data is preserved
    auth_header = next((v for k, v in recorded["headers"].items() if k.lower() == "authorization"), None)
    assert auth_header == "Bearer my-secret-token"
    assert recorded["body_json"]["password"] == "confidential"
