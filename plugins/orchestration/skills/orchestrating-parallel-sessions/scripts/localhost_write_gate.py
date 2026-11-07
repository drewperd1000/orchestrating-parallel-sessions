#!/usr/bin/env python3
"""The four checks a localhost page must pass before it is allowed to WRITE anything.

<!-- subject: orchestration -->
<!-- route-tags: localhost server write gate csrf token origin json content-type 127.0.0.1 -->
"""
import hmac
import json
import secrets

HOST = "127.0.0.1"                       # ⛔ NEVER 0.0.0.0
JSON_CT = "application/json"
# An unbounded body is its own denial of service on a single-threaded-ish local server. 200KB
# is far more than any note or action payload and far less than anything worth streaming.
MAX_BODY = 200_000


def mint_token():
    """A fresh token per RUN. Restarting the server invalidates every page still open, which
    is the correct direction: a stale page's write is refused rather than acted on."""
    return secrets.token_urlsafe(32)


def allowed_origins(port, host=HOST):
    """The server's own origin, spelled both ways a browser might.
    """
    return {"http://%s:%d" % (host, port), "http://localhost:%d" % port}


def check_request(headers, rfile, token, port, max_body=MAX_BODY, host=HOST, browser_only=False):
    """Run every check on one action request. Returns (payload, None) or (None, (code, why)).
    """
    def _drain():
        try:
            n = int(headers.get("Content-Length", "0"))
        except ValueError:
            return
        if 0 < n <= max_body:
            try:
                rfile.read(n)
            except OSError:
                pass

    ctype = (headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if ctype != JSON_CT:
        _drain()
        return None, (415, "this endpoint requires Content-Type: application/json")

    origin = (headers.get("Origin") or "").strip()
    if origin not in allowed_origins(port, host):
        _drain()
        return None, (403, "refused: the request did not come from this page")

    if browser_only:
        site = (headers.get("Sec-Fetch-Site") or "").strip().lower()
        if site != "same-origin":
            _drain()
            return None, (403, "refused: the request did not come from this page in a "
                               "browser (Sec-Fetch-Site is %r, not 'same-origin')"
                               % (site or "missing"))

    try:
        length = int(headers.get("Content-Length", "0"))
    except ValueError:
        return None, (400, "bad Content-Length")
    if length <= 0 or length > max_body:
        return None, (400, "empty or oversized body")
    try:
        payload = json.loads(rfile.read(length).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None, (400, "body is not JSON")
    if not isinstance(payload, dict):
        return None, (400, "body is not an object")

    got = payload.get("token")
    if not isinstance(got, str) or not hmac.compare_digest(got, token):
        return None, (403, "refused: bad or missing token")
    return payload, None


SAFE_HEADERS = (
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
)
