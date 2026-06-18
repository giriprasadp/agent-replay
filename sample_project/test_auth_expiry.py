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
