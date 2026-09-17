#!/usr/bin/env python3
"""Intercept READING a browse page when the session meant to SEARCH.

<!-- subject: knowledge-routing -->
<!-- route-tags: browse index cornerstone read hook pretooluse route instead of search -->

\1The human, 2026-08-17: *"If every cornerstone page opens with 'this page is a browse view; to find
one thing, run route.py', then it allows the agent to ignore it as a suggestion, not hard-wired
law."*

He is right, and it is the same lesson three times over now: a banner at the top of a page is
PROSE, and prose is optional. The router trigger in CLAUDE.md failed for the orchestrator who
had it loaded. A note authored that morning did not fire for its own author that afternoon.

⭐ WHAT MAKES THIS ONE DIFFERENT: reading a page is a TOOL CALL, so it passes a chokepoint. o1's
line - *a guard that fires on an ARTIFACT is mechanisable; a guard that fires on a CLAIM is
not* - applies here in the agent's favour. "Deciding to browse" is unguardable. "Opening the
file" is not.

⛔ IT BLOCKS ONCE, THEN GETS OUT OF THE WAY. Browsing is legitimate - surveying a subject,
answering "what do we know about X", a human reading. So a refusal that stood forever would be
wrong, and would be disabled within a day. The first reach is intercepted with the router
command spelled out; a second reach at the same page passes. The cost is one round trip, paid
exactly when the session is about to substitute browsing for searching.

Exit 2 blocks and shows stderr to the model. Exit 0 allows. Fails OPEN.
"""
import json
import os
import re
import sys
import tempfile
import time

WINDOW = 1800          # a wave-through lasts 30 min, so the speed bump stays live in long runs
MARK = os.path.join(tempfile.gettempdir(), "claude-browse-waved.json")

# Generated BROWSE views - pages whose job is "what exists about X", not "where is X".
BROWSE = re.compile(r"docs[/\\]knowledge[/\\](?!CREATE\.md)[a-z0-9-]+\.md$", re.I)


def _waved():
    try:
        with open(MARK, encoding="utf-8") as fh:
            d = json.load(fh)
    except Exception:
        return {}
    now = time.time()
    return {k: v for k, v in d.items() if now - v < WINDOW}


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    path = (payload.get("tool_input") or {}).get("file_path") or ""
    if not path or not BROWSE.search(path.replace("\\", "/")):
        return 0

    key = os.path.basename(path).lower()
    waved = _waved()
    if key in waved:
        return 0                       # second reach - they meant it. Get out of the way.

    waved[key] = time.time()
    try:
        with open(MARK, "w", encoding="utf-8") as fh:
            json.dump(waved, fh)
    except Exception:
        return 0                       # cannot record the wave-through: never block twice

    print("HOLD: that is a BROWSE view, not a search.\n", file=sys.stderr)
    print("  These pages answer \"what do we know about X\" - they are generated, and they",
          file=sys.stderr)
    print("  list what each subject OWNS. They are not how you find one specific thing.\n",
          file=sys.stderr)
    print("  If you are looking for something specific, this is faster and searches the",
          file=sys.stderr)
    print("  WHOLE corpus rather than one branch:\n", file=sys.stderr)
    print("      python .shared/scripts/route.py <the words you would have grepped for>\n",
          file=sys.stderr)
    print("  ⛔ Why it matters: a subject tree can only tell you about the branch you picked.",
          file=sys.stderr)
    print("  Guess the wrong branch and an existing doc reads as absent - which is the exact",
          file=sys.stderr)
    print("  failure this whole system was built to stop. The router never has a branch to",
          file=sys.stderr)
    print("  guess wrong.\n", file=sys.stderr)
    print("  If you genuinely want to browse, re-issue this Read and it will pass.",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
