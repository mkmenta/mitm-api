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
from utils import decompress_body, verify_credentials, redact_sensitive_data

import glob
from contextlib import asynccontextmanager
import time
import uuid
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

PERSISTENCE_DIR = "requests_data"
METADATA_FILE = os.path.join(PERSISTENCE_DIR, "metadata.json")

# Analysis metadata structure: {"id": str, "title": str, "endpoint": str, "created_at": str}
analyses: dict = {}  # id -> analysis metadata
current_analysis_id: Optional[str] = None
redirect_endpoint: Optional[str] = os.getenv("DEFAULT_REDIRECT_ENDPOINT", None)

# Lock for synchronizing access to global state
state_lock = asyncio.Lock()

async def load_analyses_metadata():
    """Load analyses metadata from persistence directory."""
    global analyses, current_analysis_id, redirect_endpoint
    async with state_lock:
        if not os.path.exists(PERSISTENCE_DIR):
            os.makedirs(PERSISTENCE_DIR)
            return
        
        if os.path.exists(METADATA_FILE):
            try:
                with open(METADATA_FILE, "r") as f:
                    metadata = json.load(f)
                    analyses.update(metadata.get("analyses", {}))
                    
                    # Load current_analysis_id if present
                    if "current_analysis_id" in metadata:
                        current_analysis_id = metadata["current_analysis_id"]
                        # Sync redirect endpoint from the current analysis
                        if current_analysis_id in analyses:
                            redirect_endpoint = analyses[current_analysis_id].get("endpoint")
            except Exception as e:
                logger.error(f"Error loading metadata: {e}", exc_info=True)

async def save_analyses_metadata():
    """Save analyses metadata to file."""
    async with state_lock:
        if not os.path.exists(PERSISTENCE_DIR):
            os.makedirs(PERSISTENCE_DIR)
        
        try:
            with open(METADATA_FILE, "w") as f:
                json.dump({"analyses": analyses, "current_analysis_id": current_analysis_id}, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving metadata: {e}", exc_info=True)

async def create_analysis(title: str, endpoint: str, redact_sensitive: bool = False) -> dict:
    """Create a new analysis with unique ID."""
    analysis_id = str(uuid.uuid4())
    analysis = {
        "id": analysis_id,
        "title": title,
        "endpoint": endpoint,
        "redact_sensitive": redact_sensitive,
        "created_at": datetime.now().isoformat()
    }
    
    async with state_lock:
        analyses[analysis_id] = analysis
        
        # Create folder for this analysis
        analysis_dir = os.path.join(PERSISTENCE_DIR, analysis_id)
        os.makedirs(analysis_dir, exist_ok=True)
    
    await save_analyses_metadata()
    return analysis

def get_analysis(analysis_id: str) -> Optional[dict]:
    """Get analysis details by ID."""
    return analyses.get(analysis_id)

def get_analysis_dir(analysis_id: str) -> str:
    """Get the directory path for an analysis."""
    return os.path.join(PERSISTENCE_DIR, analysis_id)

async def load_history():
    """Load requests history from current analysis directory."""
    global current_analysis_id
    
    async with state_lock:
        if not current_analysis_id:
            return  # No analysis selected
        
        analysis_dir = get_analysis_dir(current_analysis_id)
        if not os.path.exists(analysis_dir):
            os.makedirs(analysis_dir)
            return
        
        files = glob.glob(os.path.join(analysis_dir, "*.json"))
        
        loaded_history = []
        for f_path in files:
            try:
                with open(f_path, "r") as f:
                    data = json.load(f)
                    # Ensure the data has an ID for our tracking
                    if "id" not in data:
                        # Try to get ID from filename if it's the new format, 
                        # but we'll mostly rely on internal data
                        pass
                    loaded_history.append(data)
            except Exception as e:
                logger.error(f"Error loading {f_path}: {e}")
        
        # Sort by timestamp (most robust) or fallback to filename if timestamp missing
        # This fixes the "Unsafe Sorting" issue
        def sort_key(item):
            ts = item.get("timestamp", "")
            if not ts:
                # If no timestamp, try to use ID or a very old date
                return item.get("id", "0000-00-00T00:00:00")
            return ts

        loaded_history.sort(key=sort_key)
        
        requests_history.clear()
        requests_history.extend(loaded_history)

async def save_request(request_id: str, data: dict):
    """Save a request to the current analysis directory using its unique ID."""
    async with state_lock:
        if not current_analysis_id:
            logger.warning("No analysis selected, skipping save")
            return
        
        analysis_dir = get_analysis_dir(current_analysis_id)
        if not os.path.exists(analysis_dir):
            os.makedirs(analysis_dir)
        
        file_path = os.path.join(analysis_dir, f"{request_id}.json")
        try:
            with open(file_path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving request {request_id}: {e}", exc_info=True)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load analyses metadata
    await load_analyses_metadata()
    # Load history for current analysis (if selected)
    if current_analysis_id:
        await load_history()
    yield

app = FastAPI(lifespan=lifespan)


# Jinja2 templates
templates = Jinja2Templates(directory="templates")

# In-memory storage for debugging
requests_history = []


@app.get("/___configure", response_class=HTMLResponse)
async def configure(request: Request, username: str = Depends(verify_credentials)):
    """Show HTML form to configure the redirect endpoint."""
    async with state_lock:
        current_analysis = None
        if current_analysis_id:
            current_analysis = get_analysis(current_analysis_id)
        
        # Sort analyses by created_at for consistent display
        sorted_analyses = sorted(analyses.values(), key=lambda a: a.get("created_at", ""), reverse=True)
        
        num_requests = len(requests_history)

    return templates.TemplateResponse(request, "configure.html", {
        "current_analysis": current_analysis,
        "analyses": sorted_analyses,
        "num_requests": num_requests
    })


@app.post("/___configure")
async def configure_post(
    request: Request, 
    username: str = Depends(verify_credentials),
    action: str = Form(...),
    title: Optional[str] = Form(None),
    endpoint: Optional[str] = Form(None),
    redact_sensitive: bool = Form(False),
    analysis_id: Optional[str] = Form(None)
):
    """Handle analysis creation and switching."""
    global current_analysis_id, redirect_endpoint
    
    if action == "create":
        # Create new analysis
        if not title or not endpoint:
            return templates.TemplateResponse(request, "error.html", {
                "error_title": "Invalid Input",
                "error_message": "Title and endpoint are required to create an analysis"
            }, status_code=400)
        
        analysis = await create_analysis(title.strip(), endpoint.strip(), redact_sensitive)
        async with state_lock:
            current_analysis_id = analysis["id"]
            redirect_endpoint = analysis["endpoint"]
        
        # Load history for new analysis (will be empty)
        await load_history()
        
        return templates.TemplateResponse(request, "configure_success.html", {
            "message": f"Analysis '{title}' created successfully!",
            "redirect_url": "/___configure"
        }, headers={"Refresh": "2;url=/___configure"})
    
    elif action == "switch":
        # Switch to existing analysis
        if not analysis_id:
            return templates.TemplateResponse(request, "error.html", {
                "error_title": "Invalid Input",
                "error_message": "Analysis ID is required to switch"
            }, status_code=400)
        
        async with state_lock:
            analysis = get_analysis(analysis_id)
            if not analysis:
                return templates.TemplateResponse(request, "error.html", {
                    "error_title": "Not Found",
                    "error_message": f"Analysis with ID '{analysis_id}' not found"
                }, status_code=404)
            
            current_analysis_id = analysis["id"]
            redirect_endpoint = analysis["endpoint"]
        
        # Load history for selected analysis
        await load_history()
        
        return templates.TemplateResponse(request, "configure_success.html", {
            "message": f"Switched to analysis '{analysis['title']}'",
            "redirect_url": "/___configure"
        }, headers={"Refresh": "2;url=/___configure"})
    
    else:
        return templates.TemplateResponse(request, "error.html", {
            "error_title": "Invalid Action",
            "error_message": f"Unknown action: {action}"
        }, status_code=400)


@app.get("/___view_last/{x}")
async def view_last(request: Request, x: int, username: str = Depends(verify_credentials)):
    """View the last request at index x."""
    async with state_lock:
        current_analysis = None
        if current_analysis_id:
            current_analysis = get_analysis(current_analysis_id)
        
        if not requests_history:
            return templates.TemplateResponse(request, "error.html", {
                "error_title": "No Requests",
                "error_message": "No requests recorded yet"
            }, status_code=404)
        
        if x < 1 or x > len(requests_history):
            return templates.TemplateResponse(request, "error.html", {
                "error_title": "Error",
                "error_message": f"Index {x} out of range. Available indices: 1-{len(requests_history)}"
            }, status_code=404)
        
        # Convert 1-based index to 0-based for array access
        index = x - 1
        request_data = requests_history[index].copy()
        total_count = len(requests_history)
    
    # Build navigation links (using 1-based indexing)
    nav_links = []
    if x > 1:
        nav_links.append(f'<a href="/___view_last/{x-1}">← Previous</a>')
    nav_links.append('<a href="/___configure">Configuration</a>')
    if x < total_count:
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
        
        return templates.TemplateResponse(request, "view_websocket.html", {
            "index": x,
            "total_count": total_count,
            "nav_links": " | ".join(nav_links),
            "timestamp": timestamp,
            "path": path,
            "ws_url": ws_url,
            "messages": messages,
            "error": error,
            "current_analysis": current_analysis
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
    
    return templates.TemplateResponse(request, "view_request.html", {
        "index": x,
        "total_count": total_count,
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
        "status_color": status_color,
        "current_analysis": current_analysis
    })


@app.websocket("/{path:path}")
async def websocket_endpoint(websocket: WebSocket, path: str):
    """Handle WebSocket connections and forward them to the configured endpoint with streaming support."""
    async with state_lock:
        if not redirect_endpoint:
            await websocket.close(code=1008, reason="No redirect endpoint configured")
            return
        
        # Use a local variable to avoid race conditions if redirect_endpoint changes
        local_redirect_endpoint = redirect_endpoint
    
    # Accept the WebSocket connection
    await websocket.accept()
    
    # Parse the redirect endpoint to get WebSocket URL
    parsed = urlparse(local_redirect_endpoint)
    ws_scheme = "wss" if parsed.scheme == "https" else "ws"
    ws_host = parsed.netloc
    ws_path = f"{parsed.path.rstrip('/')}/{path}" if path else parsed.path.rstrip('/')
    if parsed.query:
        ws_path += f"?{parsed.query}"
    
    # Build WebSocket URL
    ws_url = f"{ws_scheme}://{ws_host}{ws_path}"
    
    # Capture WebSocket connection details
    request_id = str(uuid.uuid4())
    ws_data = {
        "id": request_id,
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
                    logger.error(f"Error forwarding to upstream: {e}")
            
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
                    logger.error(f"Error forwarding from upstream: {e}")
            
            # Run both forwarding tasks concurrently
            try:
                await asyncio.gather(
                    forward_to_upstream(),
                    forward_from_upstream()
                )
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
    
    except Exception as e:
        ws_data["error"] = str(e)
        await websocket.close(code=1011, reason=f"Upstream connection failed: {str(e)}")
    
    finally:
        # Save WebSocket session to history
        # We no longer redact WebSocket messages as they are considered 'body stuff'
        async with state_lock:
            requests_history.append(ws_data)
        
        await save_request(ws_data["id"], ws_data)
        try:
            await websocket.close()
        except Exception:
            pass


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
async def catch_all(request: Request, path: str):
    """Catch all requests, save them, and forward to configured endpoint."""
    if path in ('favicon.ico', ):
        return Response(status_code=200)
    
    async with state_lock:
        if not redirect_endpoint:
            return JSONResponse(
                {"error": "No redirect endpoint configured. Please configure at /___configure"},
                status_code=400
            )
        local_redirect_endpoint = redirect_endpoint
    
    # Capture request details
    body = await request.body()
    headers = dict(request.headers)
    # Remove host header to avoid issues
    headers.pop("host", None)
    
    request_id = str(uuid.uuid4())
    request_data = {
        "id": request_id,
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
    async with state_lock:
        current_analysis = get_analysis(current_analysis_id) if current_analysis_id else None
        if current_analysis and current_analysis.get("redact_sensitive"):
            request_data["headers"] = redact_sensitive_data(request_data["headers"])
        
        requests_history.append(request_data)
        request_data_index = len(requests_history) - 1
    
    await save_request(request_data["id"], request_data)
    
    # Forward request to configured endpoint
    target_url = f"{local_redirect_endpoint.rstrip('/')}/{path}" if path else local_redirect_endpoint.rstrip('/')
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
                    
                    async with state_lock:
                        current_analysis = get_analysis(current_analysis_id) if current_analysis_id else None
                        should_redact = current_analysis and current_analysis.get("redact_sensitive")
                        
                        headers = dict(response.headers)
                        if should_redact:
                            headers = redact_sensitive_data(headers)

                        resp_data = {
                            "status_code": status_code,
                            "headers": headers,
                            "body": response_body_final.decode("utf-8", errors="replace") if response_body_final else None,
                        }
                            
                        requests_history[request_data_index]["response"] = resp_data
                    await save_request(requests_history[request_data_index]["id"], requests_history[request_data_index])
                    
                    if response_body_final:
                        try:
                            async with state_lock:
                                body_json = json.loads(response_body_final.decode("utf-8"))
                                # Body is no longer redacted
                                requests_history[request_data_index]["response"]["body_json"] = body_json
                            await save_request(requests_history[request_data_index]["id"], requests_history[request_data_index])
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            pass
                except Exception as e:
                    # Don't let response capture errors affect the streaming
                    logger.error(f"Error capturing response: {e}")
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

