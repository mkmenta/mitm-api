from fastapi import FastAPI, Request, Form, Depends, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
import httpx
import json
from typing import Optional
from datetime import datetime
import os
import asyncio
import websockets
from urllib.parse import urlparse
from utils import decompress_body, verify_credentials

app = FastAPI()

# Jinja2 templates
templates = Jinja2Templates(directory="templates")

# In-memory storage for debugging
requests_history = []
redirect_endpoint: Optional[str] = os.getenv("DEFAULT_REDIRECT_ENDPOINT", None)


@app.get("/___configure", response_class=HTMLResponse)
async def configure(request: Request, username: str = Depends(verify_credentials)):
    """Show HTML form to configure the redirect endpoint."""
    current_endpoint = redirect_endpoint or "Not configured"
    return templates.TemplateResponse("configure.html", {
        "request": request,
        "current_endpoint": current_endpoint
    })


@app.post("/___configure")
async def configure_post(request: Request, endpoint: str = Form(...), username: str = Depends(verify_credentials)):
    """Save the redirect endpoint configuration."""
    global redirect_endpoint
    redirect_endpoint = endpoint.strip()
    return templates.TemplateResponse("configure_success.html", {
        "request": request,
        "redirect_endpoint": redirect_endpoint
    }, headers={"Refresh": "2;url=/___configure"})


@app.get("/___view_last/{x}")
async def view_last(request: Request, x: int, username: str = Depends(verify_credentials)):
    """View the last request at index x."""
    if not requests_history:
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error_title": "No Requests",
            "error_message": "No requests recorded yet"
        }, status_code=404)
    
    if x < 1 or x > len(requests_history):
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error_title": "Error",
            "error_message": f"Index {x} out of range. Available indices: 1-{len(requests_history)}"
        }, status_code=404)
    
    # Convert 1-based index to 0-based for array access
    index = x - 1
    request_data = requests_history[index].copy()
    
    # Build navigation links (using 1-based indexing)
    nav_links = []
    if x > 1:
        nav_links.append(f'<a href="/___view_last/{x-1}">← Previous</a>')
    nav_links.append('<a href="/___configure">Configuration</a>')
    if x < len(requests_history):
        nav_links.append(f'<a href="/___view_last/{x+1}">Next →</a>')
    
    # Check if this is a WebSocket connection
    is_websocket = request_data.get("type") == "websocket"
    
    if is_websocket:
        # Handle WebSocket data
        timestamp = request_data.get("timestamp", "N/A")
        path = request_data.get("path", "N/A")
        ws_url = request_data.get("url", "N/A")
        messages = request_data.get("messages", [])
        error = request_data.get("error")
        
        return templates.TemplateResponse("view_websocket.html", {
            "request": request,
            "index": x,
            "total_count": len(requests_history),
            "nav_links": " | ".join(nav_links),
            "timestamp": timestamp,
            "path": path,
            "ws_url": ws_url,
            "messages": messages,
            "error": error
        })
    
    # Extract metadata for HTTP requests
    timestamp = request_data.get("timestamp", "N/A")
    method = request_data.get("method", "N/A")
    path = request_data.get("path", "N/A")
    query_params = request_data.get("query_params", {})
    headers = request_data.get("headers", {})
    body = request_data.get("body", "")
    body_json = request_data.get("body_json")
    
    # Extract response data (if available)
    response_data = request_data.get("response")
    
    # Format query params
    query_params_str = "&".join([f"{k}={v}" for k, v in query_params.items()]) if query_params else "None"
    
    # Format body content
    if body_json:
        body_content = f"<pre>{json.dumps(body_json, indent=2)}</pre>"
        body_type = "JSON"
    elif body:
        body_content = f"<pre>{body}</pre>"
        body_type = "Plain Text"
    else:
        body_content = "<em>No body content</em>"
        body_type = "None"
    
    # Prepare response data for template
    response_template_data = None
    resp_body_content = None
    resp_body_type = "None"
    status_color = "#6c757d"
    
    if response_data:
        resp_status = response_data.get("status_code", "N/A")
        resp_body = response_data.get("body", "")
        resp_body_json = response_data.get("body_json")
        
        # Format response body
        if resp_body_json:
            resp_body_content = f"<pre>{json.dumps(resp_body_json, indent=2)}</pre>"
            resp_body_type = "JSON"
        elif resp_body:
            resp_body_content = f"<pre>{resp_body}</pre>"
            resp_body_type = "Plain Text"
        else:
            resp_body_content = "<em>No body content</em>"
            resp_body_type = "None"
        
        # Determine status color
        if resp_status < 300:
            status_color = "#28a745"
        elif resp_status < 400:
            status_color = "#17a2b8"
        elif resp_status < 500:
            status_color = "#ffc107"
        else:
            status_color = "#dc3545"
        
        response_template_data = response_data
    
    return templates.TemplateResponse("view_request.html", {
        "request": request,
        "index": x,
        "total_count": len(requests_history),
        "nav_links": " | ".join(nav_links),
        "timestamp": timestamp,
        "method": method,
        "path": path,
        "query_params_str": query_params_str,
        "headers": headers,
        "body_content": body_content,
        "body_type": body_type,
        "response_data": response_template_data,
        "resp_body_content": resp_body_content,
        "resp_body_type": resp_body_type,
        "status_color": status_color
    })


@app.websocket("/{path:path}")
async def websocket_endpoint(websocket: WebSocket, path: str):
    """Handle WebSocket connections and forward them to the configured endpoint with streaming support."""
    global redirect_endpoint
    
    if not redirect_endpoint:
        await websocket.close(code=1008, reason="No redirect endpoint configured")
        return
    
    # Accept the WebSocket connection
    await websocket.accept()
    
    # Parse the redirect endpoint to get WebSocket URL
    parsed = urlparse(redirect_endpoint)
    ws_scheme = "wss" if parsed.scheme == "https" else "ws"
    ws_host = parsed.netloc
    ws_path = f"{parsed.path.rstrip('/')}/{path}" if path else parsed.path.rstrip('/')
    if parsed.query:
        ws_path += f"?{parsed.query}"
    
    # Build WebSocket URL
    ws_url = f"{ws_scheme}://{ws_host}{ws_path}"
    
    # Capture WebSocket connection details
    ws_data = {
        "timestamp": datetime.now().isoformat(),
        "type": "websocket",
        "path": f"/{path}" if path else "/",
        "url": ws_url,
        "messages": []
    }
    
    try:
        # Connect to upstream WebSocket server
        async with websockets.connect(
            ws_url,
            extra_headers=dict(websocket.headers)
        ) as upstream_ws:
            
            # Task to forward messages from client to upstream
            async def forward_to_upstream():
                try:
                    while True:
                        # Use low-level receive to handle both text and binary
                        data = await websocket.receive()
                        
                        if data["type"] == "websocket.disconnect":
                            break
                        
                        if "text" in data:
                            message = data["text"]
                            message_type = "text"
                            ws_data["messages"].append({
                                "direction": "client->server",
                                "timestamp": datetime.now().isoformat(),
                                "content": message,
                                "type": message_type
                            })
                            await upstream_ws.send(message)
                        elif "bytes" in data:
                            message_bytes = data["bytes"]
                            message = message_bytes.decode("utf-8", errors="replace")
                            message_type = "binary"
                            ws_data["messages"].append({
                                "direction": "client->server",
                                "timestamp": datetime.now().isoformat(),
                                "content": message,
                                "type": message_type
                            })
                            await upstream_ws.send(message_bytes)
                except WebSocketDisconnect:
                    pass
                except Exception as e:
                    print(f"Error forwarding to upstream: {e}")
            
            # Task to forward messages from upstream to client (streaming)
            async def forward_from_upstream():
                try:
                    while True:
                        message = await upstream_ws.recv()
                        is_binary = isinstance(message, bytes)
                        message_content = message if isinstance(message, str) else message.decode("utf-8", errors="replace")
                        
                        ws_data["messages"].append({
                            "direction": "server->client",
                            "timestamp": datetime.now().isoformat(),
                            "content": message_content,
                            "type": "binary" if is_binary else "text"
                        })
                        
                        if is_binary:
                            await websocket.send_bytes(message)
                        else:
                            await websocket.send_text(message)
                except websockets.exceptions.ConnectionClosed:
                    pass
                except Exception as e:
                    print(f"Error forwarding from upstream: {e}")
            
            # Run both forwarding tasks concurrently
            try:
                await asyncio.gather(
                    forward_to_upstream(),
                    forward_from_upstream()
                )
            except Exception as e:
                print(f"WebSocket error: {e}")
    
    except Exception as e:
        ws_data["error"] = str(e)
        await websocket.close(code=1011, reason=f"Upstream connection failed: {str(e)}")
    
    finally:
        # Save WebSocket session to history
        requests_history.append(ws_data)
        try:
            await websocket.close()
        except Exception:
            pass


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
async def catch_all(request: Request, path: str):
    """Catch all requests, save them, and forward to configured endpoint."""
    global redirect_endpoint

    if path in ('favicon.ico', ):
        return Response(status_code=200)
    
    if not redirect_endpoint:
        return JSONResponse(
            {"error": "No redirect endpoint configured. Please configure at /___configure"},
            status_code=400
        )
    
    # Capture request details
    body = await request.body()
    headers = dict(request.headers)
    # Remove host header to avoid issues
    headers.pop("host", None)
    
    request_data = {
        "timestamp": datetime.now().isoformat(),
        "method": request.method,
        "path": f"/{path}" if path else "/",
        "query_params": dict(request.query_params),
        "headers": headers,
        "body": body.decode("utf-8") if body else None,
    }
    
    # Try to parse as JSON if possible
    if body:
        try:
            request_data["body_json"] = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
    
    # Save to history (response will be updated after streaming completes)
    requests_history.append(request_data)
    request_data_index = len(requests_history) - 1
    
    # Forward request to configured endpoint
    target_url = f"{redirect_endpoint.rstrip('/')}/{path}" if path else redirect_endpoint.rstrip('/')
    if request.query_params:
        target_url += f"?{request.query_params}"
    
    try:
        client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0))
        
        # Open stream connection to get response metadata
        stream_ctx = client.stream(
            method=request.method,
            url=target_url,
            headers={k: v for k, v in headers.items() if k.lower() not in ["host", "content-length"]},
            content=body if body else None,
        )
        
        response = await stream_ctx.__aenter__()
        
        # Get response headers and status (available immediately after connection)
        response_headers = dict(response.headers)
        # Remove headers that might cause issues with streaming
        response_headers.pop("content-length", None)
        response_headers.pop("transfer-encoding", None)
        response_headers.pop("content-encoding", None)  # Remove encoding since we're re-streaming
        
        # Get content type from upstream response
        content_type = response_headers.get("content-type", "application/octet-stream")
        status_code = response.status_code
        
        # Check if content is encoded (compressed)
        content_encoding = response.headers.get("content-encoding", "").lower()
        needs_decompression = content_encoding in ["gzip", "br", "deflate"]
        
        # Create generator that yields chunks from the already-open stream
        # and accumulates them for later storage
        async def generate():
            response_chunks = []
            try:
                if needs_decompression:
                    # For compressed responses, we need to collect all chunks first,
                    # decompress, then yield the complete decompressed body
                    async for chunk in response.aiter_bytes():
                        response_chunks.append(chunk)
                    
                    # Combine and decompress
                    response_body = b"".join(response_chunks)
                    response_body = decompress_body(response_body, content_encoding)
                    
                    # Yield the complete decompressed body
                    yield response_body
                else:
                    # For non-compressed responses (like SSE streams), 
                    # stream chunks through immediately
                    async for chunk in response.aiter_bytes():
                        response_chunks.append(chunk)
                        yield chunk
                
            finally:
                # After streaming completes, save the response data
                try:
                    response_body_final = b"".join(response_chunks)
                    
                    # Decompress if needed (will only happen if needs_decompression was True)
                    if needs_decompression:
                        response_body_final = decompress_body(response_body_final, content_encoding)
                    
                    requests_history[request_data_index]["response"] = {
                        "status_code": status_code,
                        "headers": dict(response.headers),
                        "body": response_body_final.decode("utf-8", errors="replace") if response_body_final else None,
                    }
                    
                    # Try to parse response body as JSON if possible
                    if response_body_final:
                        try:
                            requests_history[request_data_index]["response"]["body_json"] = json.loads(response_body_final.decode("utf-8"))
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            pass
                except Exception as e:
                    # Don't let response capture errors affect the streaming
                    print(f"Error capturing response: {e}")
                finally:
                    # Clean up: exit stream context and close client
                    await stream_ctx.__aexit__(None, None, None)
                    await client.aclose()
        
        # Return streaming response with proper headers and status code from upstream
        # This works for both streaming (SSE, chunked) and non-streaming responses
        return StreamingResponse(
            generate(),
            status_code=status_code,
            headers=response_headers,
            media_type=content_type
        )
    except Exception as e:
        return JSONResponse(
            {"error": f"Failed to forward request: {str(e)}"},
            status_code=502
        )

