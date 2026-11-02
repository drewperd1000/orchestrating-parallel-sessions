#!/usr/bin/env python3
"""The Reader's link to o10's library runner (o9 W202).

<!-- subject: orchestration -->

After the Reader lands an approval that moved a script folder, it starts `library_sync.py run
--trigger reader` detached (o10L458-REPORT.md section 6) and later shows the runner's status line
and the app PR link from `.shared/state/library-sync/status.json`.

Rules this file keeps:
* Starting never raises and never waits. A failure comes back as {"started": False, "error": ...}
  for the page to show.
* The process is started with no console window (no_window.flags()), stdio to DEVNULL.
* Reading the status file never raises: a missing, unreadable or malformed file gives
  {"known": False}. Only plain strings are passed on, and the PR link only when it is an
  https://github.com/ address.
* Tests replace `Runner.popen` with a fake so no real run starts.
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

try:
    from no_window import flags as _flags
except ImportError:                      # pragma: no cover
    def _flags(extra=0):
        return extra

#: The main checkout holds the runner and the status folder (section 6). Overridable for tests.
from workspace_paths import MAIN_CHECKOUT as _MAIN  # noqa: E402

MAIN_CHECKOUT = Path(os.environ.get("READER_LIBRARY_SYNC_ROOT") or _MAIN)
STATUS_REL = ".shared/state/library-sync/status.json"
RUNNER_REL = ".shared/scripts/library_sync.py"
STATUS_MAX_BYTES = 200_000
_TEXT_CAP = 600
_FINAL = ("in-sync", "on-staging", "FAILED")
_SHA = re.compile(r"[0-9a-f]{7,64}\Z")


def _text(v, cap=_TEXT_CAP):
    return v.strip()[:cap] if isinstance(v, str) else ""


class Runner:
    def __init__(self, root=None, status_path=None, runner_path=None, popen=None):
        self.root = Path(root) if root else MAIN_CHECKOUT
        self.status_path = Path(status_path) if status_path else self.root / STATUS_REL
        self.runner_path = Path(runner_path) if runner_path else self.root / RUNNER_REL
        self.popen = popen or subprocess.Popen   # a test sets a fake

    def start(self):
        """Start one run detached. -> {"started": bool, "error": str, "at": epoch seconds}.
        Always safe to call: a run already active takes it as a `pending` mark (section 6)."""
        at = time.time()
        try:
            if not self.runner_path.is_file():
                return {"started": False, "at": at,
                        "error": "the library runner was not found at %s" % self.runner_path}
            self.popen([sys.executable, str(self.runner_path), "run", "--trigger", "reader"],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, close_fds=True, creationflags=_flags(),
                       cwd=str(self.root))
            return {"started": True, "error": "", "at": at}
        except Exception as ex:          # never fail the approval
            return {"started": False, "at": at,
                    "error": "the library runner could not be started (%s)" % type(ex).__name__}

    def status(self):
        """The status file as plain, bounded fields, or {"known": False}."""
        try:
            p = self.status_path
            if not p.is_file() or p.stat().st_size > STATUS_MAX_BYTES:
                return {"known": False}
            d = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(d, dict):
                return {"known": False}
        except Exception:
            return {"known": False}
        state = _text(d.get("state"), 40)
        if not state:
            return {"known": False}
        line = _text(d.get("message"))
        if not line:
            line = "App library: " + state
            reason = _text(d.get("reason"))
            if reason:
                line += ", " + reason
        pr = _text(d.get("pr_url"), 300)
        if not pr.startswith("https://github.com/"):
            pr = ""
        num = d.get("pr_number")
        return {"known": True, "state": state, "final": state in _FINAL, "line": line,
                "pr_url": pr, "pr_number": num if (isinstance(num, int) and not isinstance(num, bool)
                                    and num > 0) else None,
                "workspace_sha": _text(d.get("workspace_sha"), 64),
                # o10: the newest trigger-path commit at run start; `library_commits` (newest
                # first, capped at 200) is coming from o10L464 and is read when present.
                "library_commits": [c for c in [_text(d.get("library_commit"), 64)] + [
                    _text(x, 64) for x in (d.get("library_commits")
                                           if isinstance(d.get("library_commits"), list)
                                           else [])[:200]] if c],
                "updated_at": _text(d.get("updated_at"), 40)}

    def covers(self, landed_sha, workspace_sha, library_commits=()):
        """Does the run's workspace commit include the landed commit? True / False, or None when
        git cannot say (a commit not fetched here). A landed commit named in the run's
        `library_commit` / `library_commits` covers it with no git call (o10's answer to o9)."""
        if isinstance(landed_sha, str) and landed_sha and landed_sha in (library_commits or ()):
            return True
        # Both values reach a git command: only a plain hex commit id gets there, so a value
        # read from the status file can never be taken as a git option.
        if not (isinstance(landed_sha, str) and _SHA.match(landed_sha)
                and isinstance(workspace_sha, str) and _SHA.match(workspace_sha)):
            return None
        try:
            p = subprocess.run(["git", "-C", str(self.root), "merge-base", "--is-ancestor",
                                landed_sha, workspace_sha], capture_output=True, timeout=30,
                               creationflags=_flags())
        except Exception:
            return None
        return True if p.returncode == 0 else (False if p.returncode == 1 else None)


def status_line_html(st, esc):
    """The Scripts page line: nothing when the status is unknown."""
    if not st or not st.get("known"):
        return ""
    h = '<div class="libsync" data-lib-sync>%s' % esc(st["line"])
    if st.get("pr_url"):
        h += ' <a href="%s" target="_blank" rel="noopener">App PR%s</a>' % (
            esc(st["pr_url"]), (" #%d" % st["pr_number"]) if st.get("pr_number") else "")
    return h + "</div>"
