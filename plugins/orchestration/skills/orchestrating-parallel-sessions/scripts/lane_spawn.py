#!/usr/bin/env python3
"""Launch a sublane: allocate its id, set the marker, hand it a working credential.

<!-- subject: orchestration -->

<!-- route-tags: lane spawn sublane launch claude -p marker credential shell bash PATH -->

    python <scripts>/lane_spawn.py "<seed>" --model haiku --allowedTools Bash

    env -u CLAUDE_CODE_OAUTH_TOKEN claude -p "Reply with exactly: OK" --model haiku
      -> Failed to authenticate: OAuth session expired and could not be refreshed
    env CLAUDE_CODE_OAUTH_TOKEN=<file token> claude -p "..." --model haiku
      -> OK

**The env token is what lets a lane RUN AT ALL. Withhold it from a leaf lane and there is no
leaf lane.** So the token cannot be the thing that distinguishes a spawner from a leaf.

| capability | where it comes from |
|---|---|
| the lane authenticates ITSELF | the `CLAUDE_CODE_OAUTH_TOKEN` this script injects |
| the lane SPAWNS children | `read_token()` re-reading the file FROM DISK, via Bash |

✅ **WHAT IS BUILT INSTEAD, and it is a GUARD, not a boundary.** Spawning now requires an
explicit grant: `--may-spawn` sets `ORCHDOC_LANE_MAY_SPAWN` in the child, and a lane without it
is refused here. **Default is OFF**, so a lane is a leaf unless somebody said otherwise.
"""
import argparse
import datetime
import hashlib
import io
import json
import os
import pathlib
import subprocess
try:
    from no_window import NO_WINDOW
except ImportError:  # a partial delivery must not take the tool down
    NO_WINDOW = 0x08000000 if os.name == "nt" else 0
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from lane_id import allocate, LANE_RE                       # noqa: E402

try:
    from workspace_paths import MAIN_CHECKOUT as _MAIN, SECRETS_DIR as _SECRETS_DIR  # noqa: E402
except Exception:  # a partial delivery: the usual layout under the home folder
    _MAIN = pathlib.Path.home() / "<your-workspace>"
    _SECRETS_DIR = _MAIN / ".shared" / "secrets"
TOKEN_FILE = _SECRETS_DIR / "claude-code-oauth-token.txt"

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import shared_state as _shared_state  # noqa: E402

LAUNCH_LOG = _shared_state.state_dir() / "lane-launches.jsonl"

MAY_SPAWN_ENV = "ORCHDOC_LANE_MAY_SPAWN"

# The watcher printed after every launch. The shared checkout's copy, which outlives worktrees.
WATCH_SCRIPT = (_MAIN / ".shared" / "scripts" / "lane_status.py").as_posix()


def fingerprint(secret):
    """First 8 hex of SHA-256 - identifies WHICH credential, discloses none of it.

    #: failure-value: ''   # what an empty secret yields - the same as "no token was injected",
    #                        which is why the log records `token_sha8` AND `token_injected`
    #                        separately rather than inferring one from the other.
    """
    if not secret:
        return ""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:8]


def log_launch(row):
    """Append one JSON row. A logging failure must never kill a launch - it is reported instead.
    """
    try:
        LAUNCH_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LAUNCH_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
        return True
    except Exception:
        return False


def read_token():
    """Read the OAuth token, stripping ALL whitespace - not just the ends.
    """
    try:
        return "".join(TOKEN_FILE.read_text(encoding="utf-8").split())
    except Exception:
        return ""


def main(argv=None):
    ap = argparse.ArgumentParser(description="Launch a marked sublane.")
    ap.add_argument("seed", help="the sublane's prompt")
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--allowedTools", default="Bash",
                    help="tools the lane may use. Bash alone is arbitrary execution.")
    ap.add_argument("--permission-mode", default="dontAsk",
                    choices=["dontAsk", "acceptEdits", "auto", "bypassPermissions", "plan"],
                    help="default dontAsk: measured to run a lane's work without the maximal grant")
    ap.add_argument("--log", default=None, help="file to capture the sublane's output into")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--wait", action="store_true",
                    help="block until the sublane exits (default: return once it is running)")
    ap.add_argument("--id", default=None,
                    help="use this id instead of allocating one - for a re-run of a known child")
    ap.add_argument("--may-spawn", action="store_true",
                    help="let this child spawn children of its own. Default OFF: a lane is a "
                         "leaf unless you say otherwise.")
    args = ap.parse_args(argv)

    parent = (os.environ.get("ORCHDOC_LANE") or "").strip()
    if not parent:
        print("lane_spawn: no ORCHDOC_LANE in this session's environment.\n"
              "    Only a marked lane spawns sublanes. If you are an orchestrator starting a\n"
              "    TOP-LEVEL lane, pick its id (<m> is the next number you have not used) and launch with\n"
              "      env ORCHDOC_LANE=o<N>L<m> claude -p \"<seed>\" ...", file=sys.stderr)
        return 2
    if not LANE_RE.match(parent):
        print("lane_spawn: ORCHDOC_LANE=%r is not a lane id" % parent, file=sys.stderr)
        return 2

    if not (os.environ.get(MAY_SPAWN_ENV) or "").strip():
        print("lane_spawn: %s was not granted spawn capability, so it may not start a sublane.\n"
              "    A lane is a LEAF by default. If this lane genuinely needs\n"
              "    to fan out, the lane that launched it must pass --may-spawn.\n"
              "\n"
              "    FINISH WHAT NEEDS NO SUBLANE, then report to your orchestrator with the exact\n"
              "    seed you still need run. A fresh lane can be launched with the grant." % parent,
              file=sys.stderr)
        return 3

    child = args.id or allocate(parent)

    env = dict(os.environ)
    env["ORCHDOC_LANE"] = child
    token = read_token()
    if token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token

    env.pop(MAY_SPAWN_ENV, None)
    if args.may_spawn:
        env[MAY_SPAWN_ENV] = "1"

    cmd = ["claude", "-p", args.seed, "--model", args.model,
           "--allowedTools", args.allowedTools, "--permission-mode", args.permission_mode,
           "--name", "ORCHDOC_LANE=%s" % child]

    out = None
    if args.log:
        pathlib.Path(args.log).parent.mkdir(parents=True, exist_ok=True)
        out = open(args.log, "w", encoding="utf-8", errors="replace")
    try:
        proc = subprocess.Popen(cmd, env=env, stdout=out or subprocess.DEVNULL,
                                stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
        ok = log_launch({
            "at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "parent": parent,
            "child": child,
            "pid": proc.pid,
            "model": args.model,
            "allowed_tools": args.allowedTools,
            "permission_mode": args.permission_mode,
            "may_spawn": bool(args.may_spawn),
            "token_injected": bool(token),
            "token_sha8": fingerprint(token),      # WHICH credential, never the credential
            "seed_sha8": fingerprint(args.seed),   # a seed can quote untrusted text - never log it
            "seed_len": len(args.seed),
            "log": args.log or "",
        })
        if not ok:
            print("lane_spawn: WARNING - could not write %s. This launch is UNLOGGED, so a "
                  "credential use is not attributable to it." % LAUNCH_LOG, file=sys.stderr)
        print(child)                                  # the id, so the caller can record it
        if not args.wait:
            # ON STDERR, so stdout stays the bare id a caller captures with $(...).
            # Nothing else tells the launcher when this child ends: a headless lane has no
            # session row to subscribe to. A background command that exits re-invokes the
            # session that started it, so this is the command to start.
            print("lane_spawn: launched %s. To be woken when it ends, run this with "
                  "run_in_background now:\n    python %s --wait %s\n"
                  "  On waking, read the report it points at, then re-arm for any lane still "
                  "running." % (child, WATCH_SCRIPT, child), file=sys.stderr)
        if args.wait:
            try:
                proc.wait(timeout=args.timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                print("lane_spawn: %s timed out after %ds" % (child, args.timeout),
                      file=sys.stderr)
                return 1
            return proc.returncode
    finally:
        if out:
            out.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
