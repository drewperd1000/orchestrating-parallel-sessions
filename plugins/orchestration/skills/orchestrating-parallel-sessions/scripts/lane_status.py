"""Which headless lanes are alive right now, and what each has produced.

<!-- subject: orchestration -->

  1. the PROCESS   - its ORCHDOC_LANE marker on the command line
  2. the WORKTREE  - a checkout named for it
  3. the BRANCH    - local or pushed

<!-- route-tags: lane status running finished wait watch wake orchestrator background ended
     headless blocked notify re-arm watcher -->

It polls the process list and exits the moment ANY listed lane stops running,
printing where to read what that lane left. The watcher lives on the
orchestrator's side, not inside the lane, because a lane that crashes cannot
report that it crashed.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
try:
    from no_window import NO_WINDOW
except ImportError:  # a partial delivery must not take the tool down
    NO_WINDOW = 0x08000000 if os.name == "nt" else 0
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from workspace_paths import MAIN_CHECKOUT as _MAIN
    WORKSPACE = str(_MAIN).replace("\\", "/")
except Exception:  # a partial delivery: the checkout under the home folder
    WORKSPACE = os.path.join(os.path.expanduser("~"), "<your-workspace>").replace("\\", "/")
DEFAULT_REPO = WORKSPACE if os.path.isdir(WORKSPACE) else os.getcwd()
PS = ["powershell", "-NoProfile", "-NonInteractive", "-Command"]

# The command orchestrators are told to run. Always the SHARED checkout's copy: a
# worktree copy disappears when its worktree is removed, and a watcher command
# pointing at it would then fail to start.
WATCH_SCRIPT = WORKSPACE + "/.shared/scripts/lane_status.py"
# Where a lane's worktree and branch can be. A lane works in whichever repo its
# task is in, so --wait looks in all of them rather than guessing one.
WAIT_REPOS = [DEFAULT_REPO] + [r for r in os.environ.get("LANE_STATUS_REPOS", "").split(os.pathsep) if r]

EXIT_ENDED = 0
EXIT_NEVER_RUNNING = 3
EXIT_MAX_HOURS = 4
EXIT_SCAN_FAILED = 5

LANE_FULL = re.compile(r"o\d+L\d+(?:\.\d+)*")
LANE_MARK = re.compile(r"ORCHDOC_LANE=(o\d+L\d+(?:\.\d+)*)\b")
# A lane id standing alone in a command-line token.
LANE_TOKEN = re.compile(r"(?<![0-9A-Za-z])(o\d+L\d+(?:\.\d+)*)(?![0-9A-Za-z])")
# `lane_status.py ... --wait <ids>`, with the ids captured as the rest of the line.
WATCH_ARG = re.compile(r"lane_status(?:\.py)?\b.*?\s--wait(?:=|\s+)(.*)", re.S)


class ScanError(RuntimeError):
    """The process list could not be read. Never to be treated as "nothing is running"."""


def _ps():
    # LANE_STATUS_PS_EXE exists so a test can make the scan fail on purpose.
    return [os.environ.get("LANE_STATUS_PS_EXE") or PS[0]] + PS[1:]


def _on_windows():
    return os.name == "nt"


def _proc_rows(needles, strict):
    """Live processes whose command line holds every needle, read from /proc, as dicts.
    """
    root = os.environ.get("LANE_STATUS_PROC_ROOT") or "/proc"
    try:
        names = os.listdir(root)
    except OSError as e:
        if strict:
            raise ScanError("could not list %s: %s" % (root, e))
        return []
    boot = None
    try:
        with open(os.path.join(root, "stat"), encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("btime "):
                    boot = int(line.split()[1])
    except (OSError, ValueError):
        boot = None
    try:
        ticks = os.sysconf("SC_CLK_TCK")
    except (AttributeError, ValueError, OSError):
        ticks = 100
    rows = []
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open(os.path.join(root, name, "cmdline"), "rb") as f:
                cmd = f.read().replace(b"\x00", b" ").decode("utf-8", "replace").strip()
        except OSError:
            continue
        if not cmd or not all(n in cmd for n in needles):
            continue
        started_ms = 0
        try:
            with open(os.path.join(root, name, "stat"), encoding="utf-8", errors="replace") as f:
                after_comm = f.read().rsplit(")", 1)[1].split()
            start_ticks = int(after_comm[19])      # field 22 of stat; 'state' is after_comm[0]
            if boot:
                started_ms = int((boot + start_ticks / float(ticks)) * 1000)
        except (OSError, IndexError, ValueError):
            pass
        rows.append({"ProcessId": int(name), "CreationDate": "/Date(%d)/" % started_ms,
                     "CommandLine": cmd})
    return rows


def _cim_rows(where, strict):
    """Live processes matching a PowerShell Where-Object clause, as dicts.

    Off Windows (a Linux cloud VM) the same rows come from /proc, matched on the substrings the
    clause tests for with -like '*text*' (_proc_rows).
    """
    if not _on_windows() and not os.environ.get("LANE_STATUS_PS_EXE"):
        return _proc_rows(re.findall(r"-like '\*(.*?)\*'", where), strict)
    script = ("try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}; "
              "Get-CimInstance Win32_Process | Where-Object { %s } | "
              "Select-Object ProcessId, CreationDate, CommandLine | ConvertTo-Json -Depth 3"
              % where)
    try:
        r = subprocess.run(_ps() + [script], capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=120 if strict else None,
                           creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as e:
        if strict:
            raise ScanError("could not run PowerShell: %s" % e)
        raise
    if strict and r.returncode != 0:
        raise ScanError("PowerShell exited %d: %s"
                        % (r.returncode, (r.stderr or "").strip()[:300]))
    out = (r.stdout or "").lstrip("\ufeff").strip()
    if not out:
        return []
    try:
        rows = json.loads(out, strict=False)
    except ValueError as e:
        if strict:
            raise ScanError("could not parse the process list: %s" % e)
        raise
    if isinstance(rows, dict):
        rows = [rows]
    return rows


def watched_ids_in(cmd):
    """The lane ids a `lane_status.py --wait` command line is watching, or an empty set.
    """
    m = WATCH_ARG.search(cmd or "")
    if not m:
        return set()
    ids = set()
    for tok in m.group(1).split():
        tok = tok.strip("\"'")
        if tok.startswith("-"):
            break
        parts = re.split(r"[;&|<>()`]", tok, 1)
        found = LANE_TOKEN.findall(parts[0])
        if not found:
            break
        ids.update(found)
        if len(parts) > 1:
            break
    return ids


def watched_lanes():
    """{lane_id} named by a live `lane_status.py --wait` process.

    Raises ScanError when the process list cannot be read. It never returns an
    empty set for a failed scan: an empty set means "looked, and no watcher".
    """
    rows = _cim_rows("$_.CommandLine -like '*lane_status*' -and "
                     "$_.CommandLine -like '*--wait*'", strict=True)
    out = set()
    for r in rows:
        out |= watched_ids_in(r.get("CommandLine") or "")
    return out


def cim_date(v):
    """ConvertTo-Json renders a CIM datetime as /Date(<epoch-ms>)/."""
    s = str(v or "")
    m = re.search(r"/Date\((\d+)", s)
    if not m:
        return s[:19]
    t = datetime.datetime.fromtimestamp(int(m.group(1)) / 1000)
    return f"{t:%Y-%m-%d %H:%M:%S}"


def running_lanes(strict=False, full=False):
    """{lane_id: {pid, started}} from the ORCHDOC_LANE marker on live processes.

    --wait passes strict=True (a failed scan raises ScanError) and full=True
    (a sublane keeps its own dotted id, and a process that is itself a
    `lane_status.py --wait` is skipped). The skip matters when one shell command
    both launches a lane and starts the watcher: that shell carries the marker
    and stays alive for as long as the watcher does, so without the skip the
    watcher would count its own shell as the lane and never exit.
    """
    rows = _cim_rows("$_.CommandLine -like '*ORCHDOC_LANE=*'", strict)

    lanes = {}
    for r in rows:
        cmd = r.get("CommandLine") or ""
        m = LANE_MARK.search(cmd)
        if not m:
            continue
        if full and watched_ids_in(cmd):
            continue
        lane = m.group(1) if full else m.group(1).split(".")[0]
        # env.exe and the bash wrapper both carry the marker; keep the oldest,
        # which is the one that was actually launched
        started = cim_date(r.get("CreationDate"))
        prev = lanes.get(lane)
        if prev is None or started < prev["started"]:
            lanes[lane] = {"pid": r.get("ProcessId"), "started": started}
    return lanes


AGENT_MAX_AGE = 30 * 60
AGENT_DONE_STOPS = ("end_turn", "stop_sequence")
AGENT_INTERRUPTED = "[Request interrupted"
_AGENT_FILE = re.compile(r"^agent-(.+?)(\.meta\.json|\.jsonl)$")
_TAIL_CHUNK = 64 * 1024
_TAIL_BUDGET = 32 * 1024 * 1024        # a tool result can be megabytes; past this, give up


def session_dir(transcript_path):
    """A session's folder - where its subagents are - from the Stop payload's transcript_path.

    The transcript is <projects dir>/<session id>.jsonl and the folder is that path without
    ".jsonl".
    """
    p = str(transcript_path)
    return p[:-len(".jsonl")] if p.lower().endswith(".jsonl") else p


def _read_or_none(path, limit=None):
    """A file's bytes (at most `limit`), or None when it does not exist. Any other OSError is a
    failed scan: an unreadable file must not read as an absent one."""
    try:
        with open(path, "rb") as f:
            return f.read() if limit is None else f.readline(limit)
    except FileNotFoundError:
        return None
    except OSError as e:
        raise ScanError("could not read %s: %s" % (path, e))


def _json_or_none(raw):
    try:
        val = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return val if isinstance(val, dict) else None


def _record_text(rec):
    """The text of a transcript record's message: a string content, or its text blocks joined."""
    msg = rec.get("message") if isinstance(rec.get("message"), dict) else {}
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(b.get("text") or "" for b in c
                         if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _agent_lane_id(meta_path, transcript_path):
    """The lane id an Agent-tool subagent was started as, or None when it names none.
    """
    meta = _json_or_none(_read_or_none(meta_path) or b"")
    if meta:
        m = LANE_TOKEN.search(str(meta.get("description") or ""))
        if m:
            return m.group(1)
    first = _json_or_none(_read_or_none(transcript_path, limit=8 * 1024 * 1024) or b"")
    if not first or first.get("type") != "user":
        return None
    line = next((ln for ln in _record_text(first).splitlines() if ln.strip()), "")
    m = LANE_TOKEN.search(line)
    return m.group(1) if m else None


def _last_message(path, chunk=_TAIL_CHUNK, budget=_TAIL_BUDGET):
    """The transcript's last user or assistant record, read backward from the end; None when it
    has none yet. Attachments, system records and unparseable lines (one being written as this
    reads) are skipped. Each line is joined once however many chunks it spans."""
    def message(raw):
        rec = _json_or_none(raw.strip()) if raw.strip() else None
        return rec if rec and rec.get("type") in ("user", "assistant") else None

    try:
        f = open(path, "rb")
    except FileNotFoundError:
        return None
    except OSError as e:
        raise ScanError("could not read %s: %s" % (path, e))
    with f:
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        pieces = []                    # the newest unfinished line, as chunks, newest first
        spent = 0
        while pos > 0:
            if spent >= budget:
                raise ScanError("no user or assistant record in the last %d bytes of %s"
                                % (spent, path))
            step = min(chunk, pos)
            pos -= step
            f.seek(pos)
            block = f.read(step)
            spent += step
            end = len(block)
            while True:
                nl = block.rfind(b"\n", 0, end)
                if nl < 0:
                    pieces.append(block[:end])
                    break
                pieces.append(block[nl + 1:end])
                rec = message(b"".join(reversed(pieces)))
                pieces = []
                if rec:
                    return rec
                end = nl
        return message(b"".join(reversed(pieces)))


def agent_finished(rec):
    """True when a transcript's last user or assistant record ends the subagent's work."""
    if not rec:
        return False                   # nothing but its setup records yet: it has just started
    if rec.get("type") == "assistant":
        msg = rec.get("message") if isinstance(rec.get("message"), dict) else {}
        return bool(rec.get("isApiErrorMessage")) or msg.get("stop_reason") in AGENT_DONE_STOPS
    return _record_text(rec).lstrip().startswith(AGENT_INTERRUPTED)


def running_agent_lanes(folder, now=None, max_age=AGENT_MAX_AGE):
    """{lane_id} of the Agent-tool subagents of the session in `folder` that are still running.

    A subagent counts when its meta file's description, or failing that the first line of its
    prompt, names a lane id; its transcript does not show it finished (agent_finished); and its
    transcript was written in the last `max_age` seconds. Sublanes keep their dotted id.
    """
    now = time.time() if now is None else now
    sub = os.path.join(str(folder), "subagents")
    try:
        names = os.listdir(sub)
    except FileNotFoundError:
        return set()
    except OSError as e:
        raise ScanError("could not list %s: %s" % (sub, e))
    ids = sorted({m.group(1) for m in map(_AGENT_FILE.match, names) if m})
    lanes = set()
    for aid in ids:
        meta = os.path.join(sub, "agent-%s.meta.json" % aid)
        transcript = os.path.join(sub, "agent-%s.jsonl" % aid)
        written = None
        for p in (transcript, meta):
            try:
                written = os.path.getmtime(p)
                break
            except FileNotFoundError:
                continue
            except OSError as e:
                raise ScanError("could not stat %s: %s" % (p, e))
        if written is None or now - written > max_age:
            continue
        lane = _agent_lane_id(meta, transcript)
        if lane and not agent_finished(_last_message(transcript)):
            lanes.add(lane)
    return lanes


def git(args, cwd):
    if not os.path.isdir(cwd):  # a repo that is not on this computer has nothing to report
        return ""
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True,
                          text=True, errors="replace", creationflags=NO_WINDOW).stdout


def artifacts(lane, repo):
    """Worktrees and branches whose name mentions this lane."""
    key = lane.lower()
    wts = [ln.split()[0] for ln in git(["worktree", "list"], repo).splitlines()
           if key in ln.lower()]
    brs = [ln.strip().lstrip("* ") for ln in git(["branch", "-a"], repo).splitlines()
           if key in ln.lower()]
    return wts, brs


def newest_file_mtime(paths):
    newest = None
    for p in paths:
        for dirpath, dirnames, filenames in os.walk(p):
            dirnames[:] = [d for d in dirnames if d not in ("node_modules", ".git")]
            for fn in filenames:
                try:
                    t = os.path.getmtime(os.path.join(dirpath, fn))
                except OSError:
                    continue
                if newest is None or t > newest:
                    newest = t
    return newest


LOG_DIR = os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp"
REPORT_FLOOR_BYTES = 1500


def finished_lanes(live):
    """Lanes with a log on disk and no live process, newest first."""
    out = []
    try:
        names = os.listdir(LOG_DIR)
    except OSError:
        return out
    for name in names:
        if not name.endswith(".log"):
            continue
        lane = name[:-4]
        if not re.fullmatch(r"o\d+L\d+", lane) or lane in live:
            continue
        p = os.path.join(LOG_DIR, name)
        try:
            out.append((os.path.getmtime(p), lane, os.path.getsize(p)))
        except OSError:
            continue
    out.sort(reverse=True)
    return out


def print_finished(live, now):
    rows = finished_lanes(live)
    if not rows:
        return
    print()
    print("FINISHED - a log on disk with no live process, newest first:")
    for mtime, lane, size in rows[:8]:
        t = datetime.datetime.fromtimestamp(mtime)
        age = (now - t).total_seconds() / 60
        state = "REPORTED" if size >= REPORT_FLOOR_BYTES else "NO REPORT - it may have died"
        print(f"  {lane:<10} {state:<28} {age:6.0f} min ago  {size:>8} bytes")
    print()
    print("  \u26d4 A finished lane is NOT a blocker. If a closing block still")
    print("     names one, the blocker is stale - read the report.")


# ---------------------------------------------------------------------------
# --wait
# ---------------------------------------------------------------------------

def _names_lane(name, lane):
    return re.search(r"(?<![0-9A-Za-z])%s(?![0-9])" % re.escape(lane), name, re.I) is not None


def _p(path):
    """A path for display - one separator style, not the mix os.path.join makes."""
    return str(path).replace("\\", "/")


def _say(msg=""):
    print(msg, flush=True)


def watch_command(lanes):
    """The exact command to (re)arm a watcher for these lanes."""
    return "python %s --wait %s" % (WATCH_SCRIPT, ",".join(lanes))


def parse_wait_ids(values):
    """Lane ids from `--wait a,b c`, in order, de-duplicated. ValueError on a bad id."""
    ids = []
    for v in values:
        for part in re.split(r"[,\s]+", v.strip()):
            if not part:
                continue
            if not LANE_FULL.fullmatch(part):
                raise ValueError(part)
            if part not in ids:
                ids.append(part)
    return ids


def scan_with_retry(tries=3, delay=5.0):
    """running_lanes(strict, full), retried. Raises ScanError after the last try."""
    last = None
    for i in range(tries):
        try:
            return running_lanes(strict=True, full=True)
        except Exception as e:          # ScanError, or anything the parse threw
            last = e
            if i + 1 < tries:
                time.sleep(delay)
    raise ScanError(str(last))


def _age(t, now):
    mins = (now - t) / 60.0
    return "%.0f min ago" % mins if mins < 120 else "%.1f h ago" % (mins / 60.0)


def _worktrees(lane, repos):
    """[(path, branch_or_None, repo)] for worktrees whose path or branch names the lane."""
    out = []
    for repo in repos:
        path, branch = None, None
        for ln in git(["worktree", "list", "--porcelain"], repo).splitlines() + [""]:
            if ln.startswith("worktree "):
                path, branch = ln[len("worktree "):].strip(), None
            elif ln.startswith("branch "):
                branch = ln[len("branch "):].strip().replace("refs/heads/", "", 1)
            elif not ln.strip() and path:
                if _names_lane(os.path.basename(path.rstrip("/\\")), lane) or \
                        (branch and _names_lane(branch, lane)):
                    out.append((path, branch, repo))
                path, branch = None, None
    return out


def _branches(lane, repo):
    """[(name, pushed_state)] for branches naming the lane. autosave/* is skipped as noise."""
    refs = git(["for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes/origin"],
               repo).split()
    local = [r[len("refs/heads/"):] for r in refs if r.startswith("refs/heads/")]
    remote = [r[len("refs/remotes/origin/"):] for r in refs
              if r.startswith("refs/remotes/origin/") and not r.endswith("/HEAD")]
    out = []
    for b in local:
        if b.startswith("autosave/") or not _names_lane(b, lane):
            continue
        if b not in remote:
            out.append((b, "NOT PUSHED - no origin/%s" % b))
            continue
        counts = git(["rev-list", "--left-right", "--count", "%s...origin/%s" % (b, b)],
                     repo).split()
        if len(counts) == 2 and counts == ["0", "0"]:
            out.append((b, "pushed - origin/%s matches" % b))
        elif len(counts) == 2:
            out.append((b, "local is %s ahead, %s behind origin/%s (as of the last fetch)"
                        % (counts[0], counts[1], b)))
        else:
            out.append((b, "pushed state unknown"))
    for b in remote:
        if b in local or b.startswith("autosave/") or not _names_lane(b, lane):
            continue
        out.append(("origin/" + b, "pushed - no local branch"))
    return out


def _newest_file(paths):
    """(mtime, path) of the newest file under these directories, or None."""
    best = None
    for p in paths:
        for dirpath, dirnames, filenames in os.walk(p):
            dirnames[:] = [d for d in dirnames
                           if d not in ("node_modules", ".git", "dist", "build", ".next")]
            for fn in filenames:
                fp = os.path.join(dirpath, fn)
                try:
                    t = os.path.getmtime(fp)
                except OSError:
                    continue
                if best is None or t > best[0]:
                    best = (t, fp)
    return best


def _workspace_files(lane, skip):
    """[(kind, mtime, path)] in the workspace, two levels deep, whose name names the lane.

    Two levels covers mailboxes/<lane>.md, <group-dir>/<lane>-REPORT.md and the
    root-level <LANE>-REPORT-*.md files lanes have written so far.
    """
    found = []

    def consider(path, parent):
        name = os.path.basename(path)
        if not _names_lane(name, lane) or os.path.normcase(os.path.abspath(path)) in skip:
            return
        if os.path.basename(parent).lower() == "mailboxes":
            kind = "mailbox"
        elif "report" in name.lower():
            kind = "report"
        elif name.upper().startswith("LANE-"):
            kind = "seed"
        elif os.path.isdir(path):
            kind = "dir"
        else:
            kind = "file"
        try:
            found.append((kind, os.path.getmtime(path), path))
        except OSError:
            pass

    try:
        top = os.listdir(WORKSPACE)
    except OSError:
        return found
    for n in top:
        if n.startswith(".") or n == "node_modules":
            continue
        p = os.path.join(WORKSPACE, n)
        consider(p, WORKSPACE)
        if os.path.isdir(p) and not _names_lane(n, lane):
            try:
                kids = os.listdir(p)
            except OSError:
                continue
            for k in kids:
                if not k.startswith("."):
                    consider(os.path.join(p, k), p)
    order = {"report": 0, "mailbox": 1, "file": 2, "seed": 3, "dir": 4}
    found.sort(key=lambda r: (order[r[0]], -r[1]))
    return found


def print_lane_report(lane, repos, first_seen=None):
    """Everything to read about a lane that is no longer running."""
    now = time.time()
    _say("%s" % lane)
    if first_seen:
        _say("   ran       first seen running at %s" % first_seen)
    log = os.path.join(LOG_DIR, lane + ".log")
    if os.path.isfile(log):
        size = os.path.getsize(log)
        state = "REPORTED" if size >= REPORT_FLOOR_BYTES else "NO REPORT - it may have died"
        _say("   log       %s  %d bytes  %s  (%s)"
             % (_p(log), size, state, _age(os.path.getmtime(log), now)))
    else:
        _say("   log       none at %s" % _p(log))

    wts = _worktrees(lane, repos)
    for path, branch, repo in wts:
        _say("   worktree  %s  (repo %s, branch %s)"
             % (_p(path), os.path.basename(repo.rstrip("/")), branch or "detached"))
    if not wts:
        _say("   worktree  none found in %s" % ", ".join(os.path.basename(r.rstrip("/"))
                                                       for r in repos))
    any_branch = False
    for repo in repos:
        for name, state in _branches(lane, repo):
            any_branch = True
            _say("   branch    %s  [%s]  %s" % (name, os.path.basename(repo.rstrip("/")), state))
    if not any_branch:
        _say("   branch    none found")

    nf = _newest_file([p for p, _, _ in wts if os.path.isdir(p)])
    if nf:
        _say("   newest    %s  (%s)" % (_p(nf[1]), _age(nf[0], now)))

    skip = {os.path.normcase(os.path.abspath(p)) for p, _, _ in wts}
    files = _workspace_files(lane, skip)
    for kind, mtime, path in files[:12]:
        _say("   %-9s %s  (%s)" % (kind, _p(path), _age(mtime, now)))
    if len(files) > 12:
        _say("   ... and %d more files naming %s in the workspace" % (len(files) - 12, lane))
    if not any(k in ("report", "mailbox") for k, _, _ in files):
        _say("   report    no report or mailbox file names %s - read the log above" % lane)
    _say()


def cmd_wait(args):
    try:
        ids = parse_wait_ids(args.wait)
    except ValueError as e:
        print("lane_status --wait: %r is not a lane id" % str(e),
              file=sys.stderr)
        return 2
    if not ids:
        print("lane_status --wait: no lane ids given", file=sys.stderr)
        return 2
    repos = [args.repo] if args.repo else [r for r in WAIT_REPOS if os.path.exists(r + "/.git")]
    poll = max(0.5, float(args.poll))
    retry_delay = min(5.0, poll)
    t0 = time.time()
    deadline = t0 + float(args.max_hours) * 3600.0

    def scan():
        return scan_with_retry(tries=3, delay=retry_delay)

    def scan_failed(e):
        _say("LANE WATCHER ERROR  %s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        _say("Could not read the process list after 3 tries: %s" % e)
        _say("Nothing is watching %s now. Check them by hand:" % ", ".join(ids))
        _say("   python %s" % WATCH_SCRIPT)
        _say("then re-arm in the background:")
        _say("   " + watch_command(ids))
        return EXIT_SCAN_FAILED

    try:
        live = scan()
        absent = [l for l in ids if l not in live]
        if absent:
            # A lane launched a moment ago may not have its process up yet - look once more.
            time.sleep(min(3.0, poll))
            live = scan()
            absent = [l for l in ids if l not in live]
    except ScanError as e:
        return scan_failed(e)

    first_seen = {l: live[l]["started"] for l in ids if l in live}
    if absent:
        running = [l for l in ids if l in live]
        _say("LANE NOT RUNNING  %s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        _say("=" * 74)
        for l in absent:
            _say("%s was NOT RUNNING when this wait started - no process carries "
                 "ORCHDOC_LANE=%s." % (l, l))
        _say("It either finished before the watcher was armed or never launched. "
             "Nothing was waited on.")
        _say()
        for l in absent:
            print_lane_report(l, repos)
        if running:
            _say("STILL RUNNING: %s" % ", ".join(running))
            _say("   arm the watcher for them, in the background:")
            _say("   " + watch_command(running))
            _say()
        _say("READ ITS REPORT before anything else.")
        return EXIT_NEVER_RUNNING

    _say("WATCHING %s - exits when any of them stops running (poll %gs, cap %g h)"
         % (", ".join(ids), poll, float(args.max_hours)))

    while True:
        time.sleep(max(0.0, min(poll, deadline - time.time())))
        try:
            live = scan()
            gone = [l for l in ids if l not in live]
            if gone:
                # Confirm before waking anyone: one missed row must not read as an ended lane.
                time.sleep(min(3.0, poll))
                live = scan()
                gone = [l for l in gone if l not in live]
        except ScanError as e:
            return scan_failed(e)

        if gone:
            running = [l for l in ids if l not in gone]
            waited = (time.time() - t0) / 60.0
            _say()
            _say("LANE ENDED  %s   (watched %s for %.0f min)"
                 % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), ", ".join(ids), waited))
            _say("=" * 74)
            _say("No process carries ORCHDOC_LANE=%s any more. Ended does not mean done - a"
                 % ", ".join(gone))
            _say("lane that died leaves a short log and no report. Here is what it left:")
            _say()
            for l in gone:
                print_lane_report(l, repos, first_seen.get(l))
            if running:
                _say("STILL RUNNING: %s" % ", ".join(running))
                _say("   re-arm the watcher for them, in the background:")
                _say("   " + watch_command(running))
                _say()
            _say("READ ITS REPORT before anything else.")
            return EXIT_ENDED

        if time.time() >= deadline:
            _say()
            _say("LANE WATCHER CAP  %s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            _say("=" * 74)
            for l in ids:
                wts = _worktrees(l, repos)
                nf = _newest_file([p for p, _, _ in wts if os.path.isdir(p)])
                last = ("last wrote a file %s" % _age(nf[0], time.time())) if nf \
                    else "no worktree file to date it by"
                _say("%s still running after %g h - check whether it is stuck (%s)"
                     % (l, float(args.max_hours), last))
            _say()
            _say("If they are working, re-arm the watcher in the background:")
            _say("   " + watch_command(ids))
            return EXIT_MAX_HOURS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=None,
                    help="repo to look for lane worktrees/branches in (status view default: "
                         "the workspace; --wait default: the workspace and "
                         "each repo in $LANE_STATUS_REPOS)")
    ap.add_argument("--wait", nargs="+", metavar="LANE",
                    help="block until ANY of these lanes stops running, then print what it "
                         "left. Run it with run_in_background so its exit wakes the session.")
    ap.add_argument("--poll", type=float, default=20.0, help="--wait: seconds between scans")
    ap.add_argument("--max-hours", type=float, default=6.0,
                    help="--wait: give up after this long with every lane still running")
    args = ap.parse_args()

    if args.wait:
        return cmd_wait(args)
    args.repo = args.repo or DEFAULT_REPO

    lanes = running_lanes()
    now = datetime.datetime.now()

    print(f"LANE STATUS  {now:%Y-%m-%d %H:%M:%S}")
    print("=" * 74)
    if not lanes:
        print("No headless lane is running.")
        print()
        print("That is a real answer, not a failure to look: it reads the")
        print("ORCHDOC_LANE marker off live processes, which every lane launch")
        print("is required to carry.")
        print_finished(lanes, now)
        return

    for lane in sorted(lanes):
        info = lanes[lane]
        wts, brs = artifacts(lane, args.repo)
        print(f"\n{lane}   pid {info['pid']}   started {info['started'][:19]}")
        if wts:
            for w in wts:
                print(f"   worktree  {w}")
        else:
            print("   worktree  none yet")
        if brs:
            for b in brs:
                pushed = " (pushed)" if b.startswith("remotes/origin/") else ""
                print(f"   branch    {b}{pushed}")
        else:
            print("   branch    none yet")

        mt = newest_file_mtime(wts) if wts else None
        if mt:
            age = (now - datetime.datetime.fromtimestamp(mt)).total_seconds() / 60
            print(f"   last wrote a file {age:.0f} min ago")
        elif wts:
            print("   no files written yet")

    print_finished(lanes, now)
    print()
    print(f"{len(lanes)} lane(s) alive. None of them appear in the Claude Desktop")
    print("session list - a headless `claude -p` run has no session row.")


if __name__ == "__main__":
    sys.exit(main())
