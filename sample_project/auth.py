"""JWT authentication helpers."""

import hmac
import hashlib
import json
import time
import base64
import calendar


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def create_token(payload: dict, secret: str) -> str:
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = _b64(json.dumps(payload).encode())
    sig = _b64(
        hmac.new(secret.encode(), f"{header}.{body}".encode(), hashlib.sha256).digest()
    )
    return f"{header}.{body}.{sig}"


def verify_token(token: str, secret: str) -> dict:
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("malformed token")
    header, body, sig = parts
    expected = _b64(
        hmac.new(secret.encode(), f"{header}.{body}".encode(), hashlib.sha256).digest()
    )
    if sig != expected:
        raise ValueError("invalid signature")
    payload = json.loads(base64.urlsafe_b64decode(body + "=="))
    # FIX: compare against UTC epoch, not local time
    now_utc = calendar.timegm(time.gmtime())
    if "exp" in payload and payload["exp"] < now_utc:
        raise ValueError("token expired")
    return payload
