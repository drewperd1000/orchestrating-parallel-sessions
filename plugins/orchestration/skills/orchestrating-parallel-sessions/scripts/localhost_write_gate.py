#!/usr/bin/env python3
"""The four checks a localhost page must pass before it is allowed to WRITE anything.

<!-- subject: orchestration -->
<!-- route-tags: localhost server write gate csrf token origin json content-type 127.0.0.1 -->

⛔ `127.0.0.1` IS NOT A SECURITY BOUNDARY.

⭐ SO THE POSTURE IS FOUR INDEPENDENT CHECKS AND A REQUEST HAS TO PASS ALL OF THEM. It was
written for `serve_daily_dashboard.py`, it has a test suite behind it, and this module is that
same definition rather than a second copy - because a second copy is how one of them ends up
one check short and nobody notices which.

  1. **bound to 127.0.0.1**, never `0.0.0.0` - nothing off the machine can reach it at all.
     Enforced by the SERVER (there is no --host flag to rebind it), not by this module.
  2. **a random token**, minted per run, embedded in the page and required in every body. A
     page that cannot READ our page cannot learn it - no CORS headers are sent, so a
     cross-origin fetch of the page is refused by the browser before script sees a byte.
  3. **the `Origin` header must be one of ours.** Browsers attach it to every POST, including
     same-origin ones, and a page cannot forge it.
  4. **`Content-Type: application/json` is required.** This is the one that closes the classic
     hole: an HTML form can POST cross-origin with no preflight and no script, but it can only
     send three content types, and `application/json` is not among them. Demanding JSON forces
     a preflight, and the preflight fails because we send no CORS headers.

⚠️ CHECK 4 ONLY BITES IF THE SERVER SENDS NO `Access-Control-Allow-*` HEADER, EVER. That is a
property of the response, so it cannot live here - each server's `_send` owns it, and each
server's tests assert it.

⛔ AND A GET MUST NEVER MUTATE ANYTHING A CLIENT NAMED. A GET can pass none of these checks,
which is exactly why every write is a POST through this gate.
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

    `localhost` and `127.0.0.1` on our port ARE this server; anything else is another site.
    Allowing both costs nothing - a page on any other origin still fails.
    """
    return {"http://%s:%d" % (host, port), "http://localhost:%d" % port}


def check_request(headers, rfile, token, port, max_body=MAX_BODY, host=HOST, browser_only=False):
    """Run every check on one action request. Returns (payload, None) or (None, (code, why)).

    Order is cheapest-and-most-decisive first, and each failure names only its own reason - a
    caller learning WHICH check it failed learns nothing useful, and the human reading the terminal
    learns what to fix.
    """
    # ⛔ A REFUSAL BEFORE THE BODY IS READ LEAVES THE BODY IN THE SOCKET, and on HTTP/1.1
    # keep-alive the server then reads those bytes as the START OF THE NEXT REQUEST. The
    # visible symptom is an aborted connection at a random later moment - measured: a test
    # whose every assertion passed exited non-zero roughly one run in three, on the call
    # AFTER the refused one. Nothing in the failure points at the refusal that caused it.
    #
    # ⭐ SO A REFUSAL DRAINS FIRST. Bounded by the Content-Length the client declared and by
    # MAX_BODY, so a refused request cannot make us read more than an accepted one would.
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

    # A browser sends `Sec-Fetch-Site: same-origin` on a fetch from the server's own page, and
    # no page script can set or change that header. A script on this computer (curl,
    # Invoke-WebRequest, requests) sends no such header unless it is written to, so this stops
    # the plain local POST; it is not a wall against a process that forges every header, which
    # is why block_live_reader_write.py refuses the command too.
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
    # ⛔ compare_digest, not ==. A timing-distinguishable comparison on a per-run secret is a
    # small hole, and closing it costs one function call.
    if not isinstance(got, str) or not hmac.compare_digest(got, token):
        return None, (403, "refused: bad or missing token")
    return payload, None


# The headers every response from a write-capable localhost server carries. Collected here so
# a second server cannot ship with three of them.
#
# ⛔ NO Access-Control-Allow-* HEADER IS EVER INCLUDED. Their absence is what makes check 4
# bite: the preflight a JSON POST triggers has nothing to succeed against.
SAFE_HEADERS = (
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
)
