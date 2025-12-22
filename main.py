from fastapi import FastAPI, Request, Form, Depends, HTTPException, status, WebSocket, WebSocketDisconnect
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
import httpx
import json
from typing import Optional
from datetime import datetime
import os
import asyncio
import websockets
from urllib.parse import urlparse

# Get credentials from environment
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

# HTTP Basic Authentication
security = HTTPBasic()

app = FastAPI()

# In-memory storage for debugging
requests_history = []
redirect_endpoint: Optional[str] = None


def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    """Verify HTTP Basic Auth credentials against environment variables."""
    if not ADMIN_USERNAME or not ADMIN_PASSWORD:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication not configured. Please set ADMIN_USERNAME and ADMIN_PASSWORD as environment variables",
            headers={"WWW-Authenticate": "Basic"},
        )
    
    if credentials.username != ADMIN_USERNAME or credentials.password != ADMIN_PASSWORD:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    
    return credentials.username


@app.get("/___configure", response_class=HTMLResponse)
async def configure(username: str = Depends(verify_credentials)):
    """Show HTML form to configure the redirect endpoint."""
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>MITM Debugger - Configure</title>
        <style>
            body { font-family: Arial, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; }
            input[type="text"] { width: 100%; padding: 8px; margin: 10px 0; }
            button { padding: 10px 20px; background: #007bff; color: white; border: none; cursor: pointer; }
            button:hover { background: #0056b3; }
            .status { margin-top: 20px; padding: 10px; background: #f0f0f0; border-radius: 4px; }
        </style>
    </head>
    <body>
        <h1>MITM Debugger Configuration</h1>
        <form method="post" action="/___configure">
            <label for="endpoint">Redirect Endpoint URL:</label>
            <input type="text" id="endpoint" name="endpoint" placeholder="https://example.com/api" value="">
            <button type="submit">Save Configuration</button>
        </form>
        <div class="status">
            <strong>Current endpoint:</strong> <span id="current">{current}</span>
        </div>
        <div style="margin-top: 20px;">
            <a href="/___view_last/1">View Last Request</a>
        </div>
    </body>
    </html>
    """
    current = redirect_endpoint or "Not configured"
    return html_content.replace("{current}", current)


@app.post("/___configure")
async def configure_post(endpoint: str = Form(...), username: str = Depends(verify_credentials)):
    """Save the redirect endpoint configuration."""
    global redirect_endpoint
    redirect_endpoint = endpoint.strip()
    return HTMLResponse(
        f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>MITM Debugger - Configure</title>
            <meta http-equiv="refresh" content="2;url=/___configure">
            <style>
                body {{ font-family: Arial, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; }}
                .success {{ padding: 15px; background: #d4edda; color: #155724; border-radius: 4px; }}
            </style>
        </head>
        <body>
            <div class="success">
                <strong>Configuration saved!</strong> Redirecting to {redirect_endpoint}
            </div>
            <p><a href="/___configure">Back to configuration</a></p>
        </body>
        </html>
        """
    )


@app.get("/___view_last/{x}")
async def view_last(x: int, username: str = Depends(verify_credentials)):
    """View the last request at index x."""
    if not requests_history:
        return HTMLResponse(
            """
            <!DOCTYPE html>
            <html>
            <head>
                <title>MITM Debugger - No Requests</title>
                <style>
                    body { font-family: Arial, sans-serif; max-width: 1200px; margin: 50px auto; padding: 20px; }
                    .error { padding: 15px; background: #f8d7da; color: #721c24; border-radius: 4px; }
                </style>
            </head>
            <body>
                <div class="error">No requests recorded yet</div>
                <p><a href="/___configure">Back to configuration</a></p>
            </body>
            </html>
            """,
            status_code=404
        )
    
    if x < 1 or x > len(requests_history):
        return HTMLResponse(
            f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>MITM Debugger - Error</title>
                <style>
                    body {{ font-family: Arial, sans-serif; max-width: 1200px; margin: 50px auto; padding: 20px; }}
                    .error {{ padding: 15px; background: #f8d7da; color: #721c24; border-radius: 4px; }}
                </style>
            </head>
            <body>
                <div class="error">Index {x} out of range. Available indices: 1-{len(requests_history)}</div>
                <p><a href="/___configure">Back to configuration</a></p>
            </body>
            </html>
            """,
            status_code=404
        )
    
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
        
        # Format WebSocket messages
        messages_html = ""
        if messages:
            for msg in messages:
                direction = msg.get("direction", "unknown")
                msg_timestamp = msg.get("timestamp", "N/A")
                content = msg.get("content", "")
                msg_type = msg.get("type", "text")
                direction_color = "#007bff" if "client->server" in direction else "#28a745"
                type_badge_color = "#6c757d" if msg_type == "text" else "#ff9800"
                messages_html += f"""
                <div style="margin: 10px 0; padding: 10px; border-left: 4px solid {direction_color}; background: #f8f9fa;">
                    <div style="font-weight: bold; color: {direction_color}; margin-bottom: 5px;">
                        {direction} 
                        <span style="font-size: 0.85em; color: #6c757d;">({msg_timestamp})</span>
                        <span style="font-size: 0.85em; background: {type_badge_color}; color: white; padding: 2px 6px; border-radius: 3px; margin-left: 5px;">{msg_type}</span>
                    </div>
                    <pre style="margin: 0; white-space: pre-wrap; word-wrap: break-word;">{content}</pre>
                </div>
                """
        else:
            messages_html = "<em>No messages exchanged</em>"
        
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>MITM Debugger - WebSocket #{x}</title>
            <style>
                body {{ font-family: Arial, sans-serif; max-width: 1200px; margin: 50px auto; padding: 20px; }}
                h1 {{ color: #333; }}
                .metadata-table {{ width: 100%; border-collapse: collapse; margin: 20px 0; background: white; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
                .metadata-table th {{ background: #9c27b0; color: white; padding: 12px; text-align: left; }}
                .metadata-table td {{ padding: 10px; border-bottom: 1px solid #ddd; }}
                .metadata-table tr:last-child td {{ border-bottom: none; }}
                .metadata-table tr:nth-child(even) {{ background: #f8f9fa; }}
                .messages-section {{ margin-top: 30px; }}
                .messages-header {{ background: #9c27b0; color: white; padding: 10px; border-radius: 4px 4px 0 0; }}
                .messages-content {{ background: #f8f9fa; padding: 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px; }}
                .navigation {{ margin: 20px 0; }}
                .navigation a {{ margin-right: 15px; padding: 8px 15px; background: #007bff; color: white; text-decoration: none; border-radius: 4px; }}
                .navigation a:hover {{ background: #0056b3; }}
                .info-badge {{ display: inline-block; padding: 4px 8px; background: #17a2b8; color: white; border-radius: 3px; font-size: 0.85em; margin-left: 10px; }}
                .error {{ padding: 15px; background: #f8d7da; color: #721c24; border-radius: 4px; margin: 20px 0; }}
            </style>
        </head>
        <body>
            <h1>WebSocket Connection <span class="info-badge">#{x} of {len(requests_history)}</span></h1>
            
            <div class="navigation">
                {' | '.join(nav_links)}
            </div>
            
            <table class="metadata-table">
                <tr>
                    <th colspan="2">WebSocket Metadata</th>
                </tr>
                <tr>
                    <td><strong>Timestamp</strong></td>
                    <td>{timestamp}</td>
                </tr>
                <tr>
                    <td><strong>Type</strong></td>
                    <td><span style="background: #9c27b0; color: white; padding: 4px 8px; border-radius: 3px;">WebSocket</span></td>
                </tr>
                <tr>
                    <td><strong>Path</strong></td>
                    <td><code>{path}</code></td>
                </tr>
                <tr>
                    <td><strong>Upstream URL</strong></td>
                    <td><code>{ws_url}</code></td>
                </tr>
                <tr>
                    <td><strong>Total Messages</strong></td>
                    <td>{len(messages)}</td>
                </tr>
            </table>
            
            {f'<div class="error"><strong>Error:</strong> {error}</div>' if error else ''}
            
            <div class="messages-section">
                <div class="messages-header">
                    <strong>WebSocket Messages</strong>
                </div>
                <div class="messages-content">
                    {messages_html}
                </div>
            </div>
            
            <div class="navigation" style="margin-top: 30px;">
                {' | '.join(nav_links)}
            </div>
        </body>
        </html>
        """
        
        return HTMLResponse(html_content)
    
    # Extract metadata for HTTP requests
    timestamp = request_data.get("timestamp", "N/A")
    method = request_data.get("method", "N/A")
    path = request_data.get("path", "N/A")
    query_params = request_data.get("query_params", {})
    headers = request_data.get("headers", {})
    body = request_data.get("body", "")
    body_json = request_data.get("body_json")
    
    # Format query params
    query_params_str = "&".join([f"{k}={v}" for k, v in query_params.items()]) if query_params else "None"
    
    # Format headers as HTML table rows
    headers_rows = "".join([
        f"<tr><td><strong>{k}</strong></td><td>{v}</td></tr>"
        for k, v in headers.items()
    ]) if headers else "<tr><td colspan='2'>No headers</td></tr>"
    
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
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>MITM Debugger - Request #{x}</title>
        <style>
            body {{ font-family: Arial, sans-serif; max-width: 1200px; margin: 50px auto; padding: 20px; }}
            h1 {{ color: #333; }}
            .metadata-table {{ width: 100%; border-collapse: collapse; margin: 20px 0; background: white; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
            .metadata-table th {{ background: #007bff; color: white; padding: 12px; text-align: left; }}
            .metadata-table td {{ padding: 10px; border-bottom: 1px solid #ddd; }}
            .metadata-table tr:last-child td {{ border-bottom: none; }}
            .metadata-table tr:nth-child(even) {{ background: #f8f9fa; }}
            .headers-table {{ width: 100%; border-collapse: collapse; margin: 10px 0; }}
            .headers-table th {{ background: #6c757d; color: white; padding: 10px; text-align: left; }}
            .headers-table td {{ padding: 8px; border-bottom: 1px solid #ddd; }}
            .headers-table tr:nth-child(even) {{ background: #f8f9fa; }}
            .body-section {{ margin-top: 30px; }}
            .body-header {{ background: #28a745; color: white; padding: 10px; border-radius: 4px 4px 0 0; }}
            .body-content {{ background: #f8f9fa; padding: 15px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 4px 4px; }}
            .body-content pre {{ margin: 0; white-space: pre-wrap; word-wrap: break-word; }}
            .navigation {{ margin: 20px 0; }}
            .navigation a {{ margin-right: 15px; padding: 8px 15px; background: #007bff; color: white; text-decoration: none; border-radius: 4px; }}
            .navigation a:hover {{ background: #0056b3; }}
            .info-badge {{ display: inline-block; padding: 4px 8px; background: #17a2b8; color: white; border-radius: 3px; font-size: 0.85em; margin-left: 10px; }}
        </style>
    </head>
    <body>
        <h1>Request Details <span class="info-badge">#{x} of {len(requests_history)}</span></h1>
        
        <div class="navigation">
            {' | '.join(nav_links)}
        </div>
        
        <table class="metadata-table">
            <tr>
                <th colspan="2">Request Metadata</th>
            </tr>
            <tr>
                <td><strong>Timestamp</strong></td>
                <td>{timestamp}</td>
            </tr>
            <tr>
                <td><strong>Method</strong></td>
                <td><span style="background: #007bff; color: white; padding: 4px 8px; border-radius: 3px;">{method}</span></td>
            </tr>
            <tr>
                <td><strong>Path</strong></td>
                <td><code>{path}</code></td>
            </tr>
            <tr>
                <td><strong>Query Parameters</strong></td>
                <td><code>{query_params_str}</code></td>
            </tr>
        </table>
        
        <h2>Headers</h2>
        <table class="headers-table">
            <tr>
                <th>Header Name</th>
                <th>Value</th>
            </tr>
            {headers_rows}
        </table>
        
        <div class="body-section">
            <div class="body-header">
                <strong>Request Body</strong> <span style="font-size: 0.9em; opacity: 0.9;">({body_type})</span>
            </div>
            <div class="body-content">
                {body_content}
            </div>
        </div>
        
        <div class="navigation" style="margin-top: 30px;">
            {' | '.join(nav_links)}
        </div>
    </body>
    </html>
    """
    
    return HTMLResponse(html_content)


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
    
    # Save to history
    requests_history.append(request_data)
    
    # Forward request to configured endpoint
    target_url = f"{redirect_endpoint.rstrip('/')}/{path}" if path else redirect_endpoint.rstrip('/')
    if request.query_params:
        target_url += f"?{request.query_params}"
    
    # Check if this is likely a streaming request (OpenAI chat completions with stream: true)
    is_streaming_request = False
    if body:
        try:
            body_json = json.loads(body.decode("utf-8"))
            if body_json.get("stream", False):
                is_streaming_request = True
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
    
    try:
        client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0))
        
        if is_streaming_request:
            # Handle streaming response (SSE)
            async def stream_response():
                try:
                    async with client.stream(
                        method=request.method,
                        url=target_url,
                        headers={k: v for k, v in headers.items() if k.lower() not in ["host", "content-length"]},
                        content=body if body else None,
                    ) as response:
                        async for chunk in response.aiter_bytes():
                            yield chunk
                finally:
                    await client.aclose()
            
            # Make initial request to get headers and status code
            async with client.stream(
                method=request.method,
                url=target_url,
                headers={k: v for k, v in headers.items() if k.lower() not in ["host", "content-length"]},
                content=body if body else None,
            ) as initial_response:
                response_headers = dict(initial_response.headers)
                # Remove headers that might cause issues with streaming
                response_headers.pop("content-length", None)
                response_headers.pop("transfer-encoding", None)
                
                async def generate():
                    try:
                        async for chunk in initial_response.aiter_bytes():
                            yield chunk
                    finally:
                        await client.aclose()
                
                return StreamingResponse(
                    generate(),
                    status_code=initial_response.status_code,
                    headers=response_headers,
                    media_type=response_headers.get("content-type", "text/event-stream")
                )
        else:
            # Handle non-streaming response
            response = await client.request(
                method=request.method,
                url=target_url,
                headers={k: v for k, v in headers.items() if k.lower() not in ["host", "content-length"]},
                content=body if body else None,
            )
            await client.aclose()
            
            return Response(
                content=response.content,
                status_code=response.status_code,
                headers=dict(response.headers)
            )
    except Exception as e:
        return JSONResponse(
            {"error": f"Failed to forward request: {str(e)}"},
            status_code=502
        )

