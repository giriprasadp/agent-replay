#!/usr/bin/env python3
"""Demo: what a Claude Desktop coding session looks like in the replay UI.

This script simulates a realistic task — fixing a JWT expiry bug — using the
agentreplay SDK directly. Run it while collector.py is running to populate the
UI with real spans. No API keys or Claude Desktop needed.

Scenario: user asks Claude to fix a bug where tokens are always rejected
as expired. Claude reads the auth module and config, searches for the cause,
patches the file, runs tests, and adds a regression test.
"""

import os
from pathlib import Path
from agentreplay import init

replay = init()

AUTH_PATH = "sample_project/auth.py"
CONFIG_PATH = "sample_project/config.py"
TEST_PATH = "sample_project/test_auth_expiry.py"

FIXED_AUTH = '''\
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
'''

REGRESSION_TEST = '''\
"""Regression test: token expiry check must use UTC, not local time."""

import time
import calendar
from auth import create_token, verify_token

SECRET = "test-secret"


def test_valid_token_accepted():
    exp = calendar.timegm(time.gmtime()) + 3600
    token = create_token({"sub": "user1", "exp": exp}, SECRET)
    payload = verify_token(token, SECRET)
    assert payload["sub"] == "user1"


def test_expired_token_rejected():
    exp = calendar.timegm(time.gmtime()) - 1
    token = create_token({"sub": "user1", "exp": exp}, SECRET)
    try:
        verify_token(token, SECRET)
        assert False, "should have raised"
    except ValueError as e:
        assert "expired" in str(e)
'''


if __name__ == "__main__":
    with replay.session(
        "Fix JWT token expiry bug",
        input={"user": "dev-42", "message": "Tokens are always rejected as expired even for fresh logins"},
    ):
        # 1. Read the source files to understand the code
        auth_src = replay.read_text(AUTH_PATH)
        config_src = replay.read_text(CONFIG_PATH)

        # 2. Search for the known root cause
        replay.web_search(
            "python jwt token expiry time.time() utc local timezone bug",
            results=[
                {
                    "title": "JWT exp claim must use UTC — common Python pitfall",
                    "url": "https://pyjwt.readthedocs.io/en/stable/usage.html#expiration-time-claim-exp",
                    "snippet": "The exp claim must be a UTC Unix timestamp. time.time() returns local epoch on some systems — use calendar.timegm(time.gmtime()) for portability.",
                },
                {
                    "title": "Stack Overflow: Python JWT always expired",
                    "url": "https://stackoverflow.com/questions/39926567",
                    "snippet": "Root cause is comparing payload['exp'] against time.time() when the token was minted with a UTC-based exp. Use calendar.timegm(time.gmtime()) instead.",
                },
                {
                    "title": "RFC 7519 §4.1.4 — Expiration Time Claim",
                    "url": "https://www.rfc-editor.org/rfc/rfc7519#section-4.1.4",
                    "snippet": "The exp value MUST be a number containing a NumericDate value (seconds since 1970-01-01T00:00:00Z UTC).",
                },
            ],
        )

        # 3. Patch auth.py with the fix
        replay.file_write(AUTH_PATH, FIXED_AUTH)

        # 4. Run the existing test suite
        replay.bash(["python3", "-m", "pytest", "sample_project/", "-q", "--tb=short"])

        # 5. Add a regression test so this can't regress silently
        # Remove stale test file if it exists from a previous demo run
        if Path(TEST_PATH).exists():
            os.remove(TEST_PATH)
        replay.file_create(TEST_PATH, REGRESSION_TEST)

        # 6. Confirm tests pass with the new regression test included
        replay.bash(["python3", "-m", "pytest", "sample_project/", "-v", "--tb=short"])

    print("Session captured. Open http://127.0.0.1:8787")
