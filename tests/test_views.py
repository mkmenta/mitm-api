
import pytest
import main

@pytest.mark.asyncio
async def test_get_configure_page(async_client, auth_headers):
    response = await async_client.get("/___configure", headers=auth_headers)
    assert response.status_code == 200

@pytest.mark.asyncio
async def test_post_configure_page_stores_endpoint(async_client, auth_headers):
    response = await async_client.post(
        "/___configure",
        data={"endpoint": "https://example.com", "action": "create", "title": "Test"}, # Added required fields for create action
        headers=auth_headers,
    )
    # The original test used data={"endpoint": ...} but didn't specify action.
    # main.py configure_post requires 'action'.
    # If action is missing, it returns 400 or 422? Form(...) requires it.
    # The original test seemed to assume just endpoint.
    # Let's look at main.py: action: str = Form(...) is required.
    # So the original test was likely broken or I misread it.
    # Wait, the original test code was:
    # response = await client.post("/___configure", data={"endpoint": "https://example.com"}, headers=...)
    # This would fail validation. Fix it.
    
    assert response.status_code == 200
    assert main.redirect_endpoint == "https://example.com"

@pytest.mark.asyncio
async def test_view_last_without_history_returns_404(async_client, auth_headers):
    # Ensure logged in and no history
    response = await async_client.get("/___view_last/1", headers=auth_headers)
    assert response.status_code == 404
