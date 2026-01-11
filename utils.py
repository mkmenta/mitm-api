import gzip
import brotli
import zlib
import os
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

# Get credentials from environment
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

# HTTP Basic Authentication
security = HTTPBasic()


def decompress_body(body: bytes, encoding: str) -> bytes:
    """Decompress response body based on content-encoding."""
    if not body:
        return body
    
    try:
        if encoding == "gzip":
            return gzip.decompress(body)
        elif encoding == "br":
            return brotli.decompress(body)
        elif encoding == "deflate":
            return zlib.decompress(body)
        else:
            return body
    except Exception as e:
        print(f"Error decompressing body with {encoding}: {e}")
        return body


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


def redact_sensitive_data(data: any) -> any:
    """
    Recursively redact sensitive information from dictionaries and lists.
    Targets common keys like 'authorization', 'password', 'api-key', etc.
    """
    sensitive_keys = {
        "authorization", "proxy-authorization", "cookie", "set-cookie",
        "api-key", "apikey", "x-api-key", "access_token", "refresh_token",
        "password", "secret", "token", "key", "auth"
    }

    if isinstance(data, dict):
        redacted = {}
        for k, v in data.items():
            k_lower = k.lower()
            # Use exact match for keys like 'token' to avoid matching 'tokens'
            is_sensitive = k_lower in sensitive_keys or any(sk in k_lower for sk in ["authorization", "secret", "password", "key"])
            
            if is_sensitive:
                if isinstance(v, str) and v.lower().startswith("bearer "):
                    redacted[k] = "Bearer [REDACTED]"
                else:
                    redacted[k] = "[REDACTED]"
            else:
                redacted[k] = redact_sensitive_data(v)
        return redacted
    elif isinstance(data, list):
        return [redact_sensitive_data(item) for item in data]
    else:
        return data
