"""

<!-- subject: orchestration -->
"""

#!/usr/bin/env python3
"""
Stop-hook guard: did this orchestrator session update its OrchDoc this turn?

Static instructions did not fix this. The rule was already written at the top of
the OrchDoc itself, in the skill, and in CLAUDE.md - and was still broken, because
it is an attention-decay problem, not a knowledge problem. So this is the
mechanical backstop: a check that runs when the session finishes a turn.

DESIGN NOTES
------------
- Fails OPEN, always. Any unexpected error exits 0 silently. A broken guard must
  never break a session.
- ASCII-only output (Windows console encoding rule).
- Cheap on the ordinary path: a few stat() calls and one small JSON read/write. No network,
  no git. The duplicate-ask check (below) also reads the Stop payload from stdin on a thread
  that gives up after STDIN_WAIT seconds and, only for a reply that cites another group's id,
  parses that group's OrchDoc once. Its text is capped (DUP_MAX_TEXT) before any regex runs.
- Not a nag: it only fires for a session that has ALREADY shown it owns a doc, and
  only when that doc went untouched for a whole turn.
"""

import re
import subprocess
try:
    from no_window import flags
except ImportError:  # a partial delivery must not take the tool down
    def flags(extra=0):
        return extra
import hashlib
import datetime
import json
import os
import sys
import time
import pathlib
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Where the OrchDocs live. `$ORCHDOC_STOP_WORKSPACE` points a TEST at a throwaway workspace;
# unset, this is the shared checkout, as it has always been.
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from workspace_paths import MAIN_CHECKOUT as _MAIN_CHECKOUT
except Exception:  # a partial delivery: the historical default under the home folder
    _MAIN_CHECKOUT = Path.home() / "<your-workspace>"
def _plugin_workspace():
    """The workspace of a plugin install, where the path module cannot know it: $ORCHDOC_WORKSPACE,
    else the nearest folder above the session's folder that holds the settings file or a decision
    document. None when this is the default layout or nothing is found."""
    if (Path(_MAIN_CHECKOUT) / ".shared" / "scripts").is_dir():
        return None
    env = os.environ.get("ORCHDOC_WORKSPACE", "").strip()
    if env and Path(env).is_dir():
        return Path(env)
    start = Path.cwd().resolve()
    for cand in [start] + list(start.parents):
        if (cand / ".orchdoc-config.json").exists() or any(cand.glob("ORCHESTRATOR-DECISIONS-*.md")):
            return cand
    return None


PROJECTS = Path(os.environ.get("ORCHDOC_STOP_WORKSPACE") or _plugin_workspace() or _MAIN_CHECKOUT)
try:
    from shared_state import home_state_dir as _home_state_dir
except ImportError:  # a hook must never die on a missing helper
    def _home_state_dir():
        return Path.home() / ".claude" / "state"
STATE_DIR = _home_state_dir() / "orchdoc-stop"

MAX_CONSECUTIVE_BLOCKS = 3
# Sessions older than this are pruned so the state dir cannot grow without bound.
STALE_STATE_DAYS = 14


def _session_id() -> str:
    for key in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID", "CLAUDE_CODE_HOST_SESSION_ID"):
        val = os.environ.get(key)
        if val:
            return "".join(c for c in val if c.isalnum() or c in "-_")[:80]
    return ""


def _orchdocs():
    """Every OrchDoc in the workspace root, with its mtime."""
    out = {}
    try:
        for p in PROJECTS.glob("ORCHESTRATOR-DECISIONS-*.md"):
            try:
                out[str(p)] = p.stat().st_mtime
            except OSError:
                continue
    except Exception:
        pass
    return out


def _prune(now: float) -> None:
    cutoff = now - (STALE_STATE_DAYS * 86400)
    try:
        for f in STATE_DIR.glob("*.json"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass
    except Exception:
        pass


WORKTREES = Path(_MAIN_CHECKOUT).parent / (Path(_MAIN_CHECKOUT).name + "-worktrees")


def _git(ws, args):
    try:
        r = subprocess.run(["git", "-C", str(ws)] + args,
                           capture_output=True, text=True, timeout=20, creationflags=flags())
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def _worktree_roots():
    """Folders that hold orchestrator worktrees: the live `.worktrees` under the workspace, then
    the older sibling folder."""
    return [PROJECTS / ".worktrees", WORKTREES]


_WT_FOLDER = re.compile(r"^(o\d+)(?:-|$)")


def _group_of_folder(name):
    m = _WT_FOLDER.match(name or "")
    return m.group(1) if m else None


def _worktree_for(orch):
    """The worktree folder for group `orch`, or None. Several matches: the one whose OrchDoc was
    modified most recently (a folder without the OrchDoc counts as oldest)."""
    best, best_t = None, -1.0
    for root in _worktree_roots():
        try:
            kids = [k for k in root.iterdir() if k.is_dir() and _group_of_folder(k.name) == orch]
        except OSError:
            continue
        for k in kids:
            try:
                t = (k / ("ORCHESTRATOR-DECISIONS-%s.md" % orch)).stat().st_mtime
            except OSError:
                t = 0.0
            if t > best_t:
                best, best_t = k, t
    return best


def _doc_path(orch, ws):
    """The OrchDoc to read: the worktree's own copy when `ws` is that worktree and holds one,
    else the main checkout's."""
    name = "ORCHESTRATOR-DECISIONS-%s.md" % orch
    try:
        if ws and _group_of_folder(Path(ws).name) == orch and (Path(ws) / name).exists():
            return Path(ws) / name
    except OSError:
        pass
    return PROJECTS / name


def _whose_session():
    """(orch_id, worktree) from IDENTITY, never from which file moved most recently.
    """
    for var in ("ORCHDOC_ME", "CLAUDE_ORCH_ID"):
        v = (os.environ.get(var) or "").strip().lower()
        m = re.match(r"^(o\d+)$", v)
        if m:
            wt = _worktree_for(m.group(1))
            return m.group(1), (wt if wt else Path.cwd())
    try:
        cwd = Path.cwd().resolve()
    except Exception:
        return None, None
    for root in _worktree_roots():
        try:
            rel = cwd.relative_to(root.resolve())
            orch = _group_of_folder(rel.parts[0])
            if orch:
                return orch, root / rel.parts[0]
        except Exception:
            continue
    return _bound_orchestrator(), PROJECTS


def _session_store_roots():
    """Every folder the Claude desktop app may keep its per-session JSON files in.
    """
    roots = []
    try:
        roaming = os.environ.get("APPDATA")
        base = Path(roaming) if roaming else Path.home() / "AppData" / "Roaming"
        roots.append(base / "Claude" / "claude-code-sessions")
        local = os.environ.get("LOCALAPPDATA")
        lbase = Path(local) if local else Path.home() / "AppData" / "Local"
        roots.extend(sorted((lbase / "Claude-Data").glob("claude_*/claude-code-sessions")))
    except Exception:
        pass                          # fail open: no store found is "not an orchestrator"
    return [r for r in roots if r.is_dir()]


TITLE_ID = re.compile(r"^[^0-9A-Za-z]*(o\d+)\s*(?:[:\-\u2012-\u2015\u2022|/,]|\s|$)", re.I)


_MISS_TTL = 600     # seconds a "no session file for this id" answer is trusted


def _bound_orchestrator():
    """Which orchestrator this session IS, from its name. Static, permanent, nothing learned.
    """
    sid = _session_id()
    if not sid:
        return None
    want = sid.replace("local_", "")

    def _from(f):
        """Derive the binding from ONE session file, or None if it is not ours.
        """
        try:
            with open(f, "rb") as fh:
                if want not in fh.read(16384).decode("utf-8", "replace"):
                    return None
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return None
        if want not in (str(d.get("cliSessionId") or ""),
                        str(d.get("sessionId") or "").replace("local_", "")):
            return None
        m = TITLE_ID.match((d.get("title") or "").strip())
        return (m.group(1).lower() if m else "", True)

    cache = STATE_DIR / ("bind-%s.json" % want)
    try:
        if cache.exists():
            c = json.loads(cache.read_text(encoding="utf-8"))
            # NEGATIVE CACHE: a session with no app file (a headless run) would otherwise rescan
            # the whole store at every Stop. A miss is trusted for _MISS_TTL seconds.
            if c.get("miss") and time.time() - float(c["miss"]) < _MISS_TTL:
                return None
            p = pathlib.Path(c.get("path") or "")
            if p.is_file():
                got = _from(p)
                if got is not None:
                    return got[0] or None
    except Exception:
        pass                      # a bad cache is a slow turn, never a wrong answer

    try:
        for root in _session_store_roots():
            for f in root.rglob("local_*.json"):
                got = _from(f)
                if got is None:
                    continue
                try:
                    STATE_DIR.mkdir(parents=True, exist_ok=True)
                    cache.write_text(json.dumps({"path": str(f)}), encoding="utf-8")
                except Exception:
                    pass          # failing to cache costs a slow turn, nothing else
                return got[0] or None
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"miss": time.time()}), encoding="utf-8")
    except Exception:
        pass
    return None



NOTE_MARK = "ORCHDOC:NOTE"

SEEN_MARK = "\U0001f440"

NOTE_LINE_RE = re.compile(
    r"^\s*>\s*(?:" + SEEN_MARK + r"\s*)?"
    r"\*\*(?P<kind>NOTE|INSTRUCTION) from (?P<who>[^*<>\n]{1,40})\*\*\s*"
    r"\((?P<when>[^)<>\n]{1,40})\)\s*:\s*"
    r"(?P<text>.*?)\s*<!--\s*" + NOTE_MARK + r"\s+"
    r"(?P<nid>(?:[A-Z]{1,3}\d{1,4}[a-z]?\.)?n\d{1,4})"
    r"(?P<enc>\s+ESC)?"
    r"(?:\s+ACK\s+(?P<ack>[A-Za-z0-9][A-Za-z0-9:_.\- ]{0,38}[A-Za-z0-9]))?"
    r"\s*-->\s*$")

_DECORATION_RE = re.compile(r"^(?:[\s*_`~]+|[^\w\s*_`~]+)*")
_HEAD_ID = re.compile(r"^#{1,6}\s+(?:[\s*_`~]+|[^\w\s*_`~]+)*"
                      r"([A-Z]{1,3}(?:\d+[a-z]?|-[A-Z][A-Z0-9]*\d*))\b")

ARCHIVED_ID_RE = re.compile(r"_\(was\s+([A-Z]+\d+)\s*[-–—]\s*[A-Z]+\)_")
_HEADING = re.compile(r"^(#{1,6})\s")


def _pending_instructions(doc, want_ack=False):
    """[(entry_id, nid, when, text)] for every INSTRUCTION in `doc` with no ACK.
    """
    try:
        text = doc.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    out, eid, in_fence, arch = [], None, False, None
    for raw in text.split("\n"):
        if raw.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        flat = re.sub(r"\s+", " ", raw).strip()
        hd = _HEADING.match(flat)
        a = ARCHIVED_ID_RE.search(flat) if hd else None
        if a:
            eid, arch = a.group(1), len(hd.group(1))
            continue
        h = _HEAD_ID.match(flat)
        if h:
            eid, arch = h.group(1), None
            continue
        if hd and arch is not None and len(hd.group(1)) <= arch:
            eid, arch = None, None                    # the archived entry ended here
            continue
        m = NOTE_LINE_RE.match(raw)
        if m and m.group("kind") == "INSTRUCTION" and bool(m.group("ack")) == want_ack:
            t = m.group("text").strip()
            if m.group("enc"):
                t = re.sub(r"\\([\\n])", lambda k: "\n" if k.group(1) == "n" else "\\", t)
            out.append((eid or "?", m.group("nid"), m.group("when").strip(), t))
    return out


def _pending_both(doc, orch):
    """Unacknowledged instructions across the worktree copy `doc` AND the main checkout's copy:
    every one unacknowledged in either, minus every note id acknowledged in either."""
    main_doc = PROJECTS / ("ORCHESTRATOR-DECISIONS-%s.md" % orch)
    docs = [doc] if main_doc == doc else [doc, main_doc]
    acked = set()
    for d in docs:
        acked.update(x[1] for x in _pending_instructions(d, want_ack=True))
    out, seen = [], set()
    for d in docs:
        for item in _pending_instructions(d):
            if item[1] in acked or item[1] in seen:
                continue
            seen.add(item[1])
            out.append(item)
    return out


def _instruction_report(doc, orch, pending):
    """The stderr block. The human's prose is an ARGUMENT to % in every line, never a template.
    """
    head = (
        "STOP BLOCKED - the human left %d instruction(s) on %s that you have not acknowledged.\n\n"
        "⛔ They wrote %s in the OrchDoc reader, addressed to YOU. It is not a note to read\n"
        "later - it is work they are asking for, and it is in your document right now.\n\n"
        % (len(pending), doc.name, "these" if len(pending) > 1 else "this"))
    body = []
    for eid, nid, when, text in pending[:10]:
        body.append("  %s  (%s, left %s)\n" % (eid, nid, when))
        for para in text.split("\n"):
            body.append(("      %s\n" % para) if para else "\n")
        body.append("\n")
    if len(pending) > 10:
        body.append("  ...and %d more in the same document.\n\n" % (len(pending) - 10))
    show = "python <scripts>/orchdoc.py note --doc %s --show %s" % (doc.name, pending[0][1])
    tail = (
        "WHAT TO DO\n"
        "  1. Read the entry it is on and act on what the human asked.\n"
        "  1b. For copy, a script or any text where line breaks matter, get it EXACTLY as the human\n"
        "      typed it (this block indents each line by 6 spaces; the file holds it escaped):\n\n"
        "       %s\n\n"
        "  2. Acknowledge it, which is what stops this block:\n\n"
        "       python <scripts>/orchdoc.py note --doc %s --note %s --ack\n\n"
        "⛔ ACKNOWLEDGED MEANS SEEN, NOT DONE. Acking is not a way to close the item - what\n"
        "you actually do about it belongs in the entry, in prose, like any other work. Ack\n"
        "first if you are about to work on it; the block is not a place to think.\n"
        % (show, doc.name, pending[0][1]))
    return head + "".join(body) + tail


_PROSE_BLOCK = 200


def _doc_hash(doc):
    """A hash of what the doc MEANS, not of the bytes it happens to contain.
    """
    try:
        text = doc.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    parts = []
    status, opened, settled, stamps, prose = None, 0, 0, 0, 0
    eid = None

    def flush():
        if eid is not None:
            parts.append("%s|%s|%d|%d|%d|%d"
                         % (eid, status or "?", opened, settled, stamps,
                            prose // _PROSE_BLOCK))

    for raw in text.split("\n"):
        ln = re.sub(r"\s+", " ", raw).strip()
        head = re.match(r"^#{1,6} \S*\s*([A-Z]{1,3}\d+[a-z]?)\b", ln)
        if head:
            flush()
            eid, status, opened, settled, stamps, prose = head.group(1), None, 0, 0, 0, 0
            continue
        if not ln:
            continue
        m = re.search(r"\*\*Status:\*\*\s*([A-Za-z-]+)", ln)
        if m and status is None:
            status = m.group(1).upper()
        if re.match(r"^[-*+] \[ \]", ln):
            opened += 1
        elif re.match(r"^[-*+] \[[xX]\]", ln):
            settled += 1
        stamps += len(re.findall(r"\*\*(?:Reviewed|Attested-by):\*\*", ln))
        prose += len(ln)
    flush()
    if not parts:
        body = re.sub(r"\s+", " ", text).strip()
        return hashlib.sha1(("legacy|%d" % (len(body) // _PROSE_BLOCK)).encode("utf-8")).hexdigest()
    return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()



def _bump(state_file, new_state, prev, delta):
    """Count consecutive blocks into the state ABOUT TO BE WRITTEN, never the old one.
    """
    try:
        new_state["consecutive_blocks"] = (
            int((prev or {}).get("consecutive_blocks") or 0) + 1 if delta > 0 else 0)
        state_file.write_text(json.dumps(new_state), encoding="utf-8")
    except Exception:
        pass


def _reader_example(doc_name, orch, eid="D9901"):
    try:
        import orchdoc
        url = orchdoc.reader_link(doc_name, eid)
    except Exception:
        url = ""
    return "[%s](%s)" % (eid, url or "http://127.0.0.1:8787/#%s/%s" % (orch, eid))


DUP_HOOK = "orchdoc_stop_check_duplicate_ask"
# "YYYY-MM-DD": from that local date the default mode is block. None: warn only.
DUPLICATE_ASK_BLOCK_FROM = None
STDIN_WAIT = 3.0             # seconds to wait for the Stop payload before giving up on it

DUP_MAX_TEXT = 100000        # characters of a reply the check reads (the END of it is kept)
DUP_MAX_LINE = 4000          # characters of one line
DUP_MAX_SENT = 600           # characters of one sentence; the ask patterns are quadratic in it
DUP_TAIL_BYTES = 4 << 20     # bytes read from the end of a transcript when the payload has no text
_DUP_ID = re.compile(
    r"\bo(?P<g1>\d+)(?:\s*:\s*|\s+)(?P<e1>[DTQ]\d+)\b|#o(?P<g2>\d+)/(?P<e2>[DTQ]\d+)\b"
    r"|\bo(?P<g3>\d+)(?:'s|’s)\s+(?P<e3>[DTQ]\d+)\b", re.I)
_FENCE = re.compile(r"```.*?(?:```|\Z)", re.S)
_INLINE = re.compile(r"`[^`\n]*`")
_ASKING = re.compile(
    r"\byour (?:yes|ok|okay|approval|answer|call|ruling|decision|go-ahead|sign-off)\b"
    r"|\bsay (?:yes|ok|okay|the word)\b|\bneeds? (?:you|your)\b"
    r"|\bwaiting (?:on|for) (?:you|your)\b"
    r"|\b(?:please|pls)\s+(?:confirm|approve|answer|decide|rule|say|reply|tell|let)\b"
    r"|\b(?:can|could|would|will|do) you\b[^.?!]*\b(?:confirm|approve|answer|decide|say|rule|ok|okay)\b"
    r"|\b(?:need|needs|want|wants|asking|ask) you to\b"
    r"|\byou (?:need|have|must|should) to (?:confirm|approve|answer|decide|rule)\b"
    r"|\bdo you want\b|^\W*(?:confirm|approve|answer|decide)\b", re.I)
_WAIT_BEFORE_ID = re.compile(
    r"(?:\bwaits? on|\bwaiting on|\bawaiting|\bblocked (?:on|by))\W*"
    r"(?:(?:the|an?|your|the human's|his|its|answer|ruling|reply|decision|yes|approval|response|call|"
    r"to|on|for|of)\W*)*$", re.I)
_WAIT_AFTER_ID = re.compile(
    r"\b(?:still open|pending|unanswered|awaiting|waits? on (?:you|your|the human)|waiting on (?:you|your|the human)"
    r"|waiting on (?:a |an |the )?(?:ruling|answer|reply|decision|yes|approval|response))\b", re.I)
_NEEDS_YOU_HEAD = re.compile(
    r"^[#>*_\s-]*(?:needs you|needs your[a-z ]{0,20}|waiting on you"
    r"|your to-?dos?(?: for [a-z]+)?|to-?dos? for you)[*_:\s]*$", re.I)
_NEG = re.compile(r"\b(?:nothing|nobody|no one|none|not|never|no longer|no)\b|n't", re.I)
_REPORT_VERB = re.compile(
    r"\b(?:cites?|cited|citing|reflects?|reflected|records?|recorded|applies|applied|applying|"
    r"follows?|followed|uses?|used|using|per|since|from|given|honou?rs?|honou?red)\b"
    r"(?:\W+\w+){0,2}\W*$", re.I)
_BOLD_ONLY = re.compile(r"^\s*(?:\*\*|__)[^*_]+(?:\*\*|__):?\s*$")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")
_CLOSED_STATUS = {"RESOLVED", "ANSWERED", "DONE", "SUPERSEDED", "ARCHIVED", "RETRACTED"}
_STATUS_RE = re.compile(r"\*\*Status:\*\*\s*([A-Za-z-]+)")
_RESOLVED_AT = re.compile(r"\*\*(?:Resolved|RULED)\s+(\d{4}-\d{2}-\d{2}(?:T[0-9:+\-]+)?)\s*:\*\*", re.I)


def _own_group(orch):
    """None when neither is known.
    """
    if orch:
        return orch
    m = re.match(r"^\s*(o\d+)L\d+\b", os.environ.get("ORCHDOC_LANE") or "", re.I)
    return m.group(1).lower() if m else None


def _dup_default_mode(today=None):
    """warn, or block from DUPLICATE_ASK_BLOCK_FROM."""
    when = DUPLICATE_ASK_BLOCK_FROM
    if not when:
        return "warn"
    try:
        today = today or datetime.date.today()
        return "block" if today >= datetime.date.fromisoformat(when) else "warn"
    except Exception:
        return "warn"


def _dup_mode():
    try:
        import hook_mode
        return hook_mode.mode(DUP_HOOK, default=_dup_default_mode())
    except Exception:
        return _dup_default_mode()


def _read_payload():
    """The Stop payload, or {}. Read on a thread with a time limit: this hook has never read
    stdin because a caller that neither writes nor closes the pipe made it hang (see main). A
    late or missing payload costs this check its text and nothing else."""
    import threading
    box = {}

    def go():
        try:
            box["p"] = json.load(sys.stdin)
        except Exception:
            pass

    try:
        t = threading.Thread(target=go, daemon=True)
        t.start()
        t.join(STDIN_WAIT)
    except Exception:
        return {}
    p = box.get("p")
    return p if isinstance(p, dict) else {}


def _reply_text(payload):
    msg = payload.get("last_assistant_message")
    if isinstance(msg, str):
        return msg
    # Bounded: read at most DUP_TAIL_BYTES from the end of the transcript. The shared
    # last_assistant_text falls back to reading the whole file, which can be hundreds of MB.
    try:
        import stop_check_unfinished as scu
        path = payload.get("transcript_path")
        if not isinstance(path, str) or not path or not os.path.isfile(path):
            return None
        got = scu._last_assistant_text_tail(path, budget=DUP_TAIL_BYTES)
        return None if got is scu._MISS else got
    except Exception:
        return None


def _cap(text):
    """The last DUP_MAX_TEXT characters of the reply: the asks sit at its end."""
    if len(text) > DUP_MAX_TEXT:
        return text[-DUP_MAX_TEXT:]
    return text


def _negated(sent, pos):
    """True when negating wording sits in the clause just before `pos` ("Nothing needs you")."""
    head = sent[max(0, pos - 40):pos]
    head = re.split(r"[;:,—]", head)[-1]
    return bool(_NEG.search(head))


def _asks(sent):
    """True when `sent` asks the human for something, ignoring negated and reporting wording."""
    for m in _ASKING.finditer(sent):
        if _negated(sent, m.start()):
            continue
        if m.group(0).lower().startswith("your ") and \
                _REPORT_VERB.search(sent[max(0, m.start() - 30):m.start()]):
            continue
        return True
    return False


def _asking_sentences(text):
    """[(sentence, asks)] for the reply, leaving out code, fences and quoted lines. `asks` is
    true when the sentence asks the human for something, or sits under a "Needs you" heading."""
    text = _INLINE.sub(" ", _FENCE.sub(" ", _cap(text)))
    out, in_needs, blank = [], False, False
    for line in text.split("\n"):
        if line.lstrip().startswith(">"):
            continue
        flat = line.strip()[:DUP_MAX_LINE]
        if not flat:
            blank = True
            continue
        if _NEEDS_YOU_HEAD.match(flat):
            in_needs, blank = True, False
            continue
        if in_needs and (flat.startswith("#") or _BOLD_ONLY.match(flat) or flat.startswith("---")
                         or (blank and not _LIST_ITEM.match(line))):
            in_needs = False
        blank = False
        for sent in re.split(r"(?<=[.!?])\s+", flat):
            if sent:
                sent = sent[:DUP_MAX_SENT]
                out.append((sent, in_needs or _asks(sent)))
    return out


def _doc_states(group):
    """{entry id: ("open" or "closed", resolved-at or "")} for every entry in group's OrchDoc,
    from one parse of the file. {} when it cannot be read. Closed means an archived heading"""
    try:
        text = (PROJECTS / ("ORCHESTRATOR-DECISIONS-%s.md" % group)).read_text(
            encoding="utf-8", errors="replace")
    except Exception:
        return {}
    cur, in_fence, found = None, False, {}
    for raw in text.split("\n"):
        if raw.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        flat = re.sub(r"\s+", " ", raw).strip()
        if _HEADING.match(flat):
            a = ARCHIVED_ID_RE.search(flat)
            h = _HEAD_ID.match(flat)
            if a:
                cur = {"closed": True, "when": ""}
                found.setdefault(a.group(1), []).append(cur)
            elif h:
                cur = {"closed": False, "when": ""}
                found.setdefault(h.group(1), []).append(cur)
            else:
                cur = None
            continue
        if cur is None:
            continue
        m = _STATUS_RE.search(flat)
        if m and m.group(1).upper() in _CLOSED_STATUS:
            cur["closed"] = True
        r = _RESOLVED_AT.search(flat)
        if r and not cur["when"]:
            cur["when"] = r.group(1)
    out = {}
    for eid, entries in found.items():
        live = [f for f in entries if not f["closed"]]
        pick = live[0] if live else entries[0]
        out[eid] = ("closed" if pick["closed"] else "open", pick["when"])
    return out


def _entry_state(group, eid, cache=None):
    """("open" or "closed", resolved-at or "") for entry `eid` in group's OrchDoc, else None.
    With a `cache` dict the doc is parsed once however many ids the reply cites."""
    if cache is None:
        cache = {}
    if group not in cache:
        cache[group] = _doc_states(group)
    return cache[group].get(eid)


def _waits_on_entry(sent, m):
    """True when `sent` says the entry cited at match `m` is still waiting or open."""
    before = sent[max(0, m.start() - 60):m.start()].rstrip("[( ")
    w = _WAIT_BEFORE_ID.search(before + " ")
    if w and not _negated(sent, max(0, m.start() - 60) + w.start()):
        return True
    after = re.split(r"[,;:]", sent[m.end():m.end() + 45])[0]
    w = _WAIT_AFTER_ID.search(after)
    return bool(w and not _negated(after, w.start()))


def duplicate_ask_findings(text, own):
    """[(group, eid, state, when, sentence)] for each id of another group than `own` that the
    reply asks the human about (open entry) or raises as unanswered (closed entry)."""
    found, seen, cache = [], set(), {}
    for sent, asks in _asking_sentences(text or ""):
        for m in _DUP_ID.finditer(sent):
            group = "o" + (m.group("g1") or m.group("g2") or m.group("g3"))
            eid = (m.group("e1") or m.group("e2") or m.group("e3")).upper()
            if group == own or (group, eid) in seen:
                continue
            st = _entry_state(group, eid, cache)
            if st is None:
                continue
            state, when = st
            if state == "open" and asks:
                pass
            elif state == "closed" and (asks or _waits_on_entry(sent, m)):
                pass
            else:
                continue
            seen.add((group, eid))
            found.append((group, eid, state, when, sent))
    return found


def _dup_message(findings):
    lines = []
    for group, eid, state, when, sent in findings:
        link = "http://127.0.0.1:8787/#%s/%s" % (group, eid)
        if state == "open":
            lines.append("  %s %s is owned by %s and is still open: only %s asks it. Link it "
                         "without asking, or leave it out.\n    %s\n    in: %s\n"
                         % (group, eid, group, group, link, sent[:160]))
        else:
            lines.append("  %s %s was answered%s: do not raise it. Read the resolved entry "
                         "instead.\n    %s\n    in: %s\n"
                         % (group, eid, (" at " + when) if when else "", link, sent[:160]))
    return ("DUPLICATE ASK - this reply puts to the human a question another group owns.\n\n"
            + "".join(lines)
            + "\nOne question has one owner and one id. Others link it as \"waits on [o99 Q999](...)\""
              " and never ask for the yes.\n")


def _duplicate_ask(orch):
    """0 to go on, 2 to block this stop. Never raises."""
    try:
        own = _own_group(orch)
        mode = _dup_mode()
        if not own or mode == "off":
            return 0
        payload = _read_payload()
        text = _reply_text(payload)
        if not isinstance(text, str) or not text:
            return 0
        text = _cap(text)
        if not _DUP_ID.search(text):
            return 0
        found = duplicate_ask_findings(text, own)
        if not found:
            return 0
        msg = _dup_message(found)
        if mode == "warn":
            sys.stderr.write("[orchdoc-stop-check] WARN (not blocking): " + msg)
            try:
                import hook_mode
                hook_mode.warn(DUP_HOOK, {"session_id": payload.get("session_id"),
                                          "transcript_path": payload.get("transcript_path"),
                                          "would_exit": 2, "message": msg})
            except Exception:
                pass
            return 0
        sid = payload.get("session_id")
        if not isinstance(sid, str) or not sid.strip():
            sid = "%s:%s" % (own, _session_id() or "none")
        bounded = dict(payload, session_id=sid, stop_hook_active=True)
        import stop_check_unfinished as scu
        return scu.bounded_exit(DUP_HOOK, bounded, text, 2, "STOP BLOCKED - " + msg, "block")
    except Exception:
        return 0


def main() -> int:
    orch, ws = _whose_session()
    dup = _duplicate_ask(orch)
    if dup:
        return dup
    if not orch or ws is None:
        return 0                      # not an orchestrator session - stay silent, always

    sid = _session_id() or orch
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state_file = STATE_DIR / ("%s.json" % sid)
    now = time.time()
    _prune(now)

    doc = _doc_path(orch, ws)
    if not doc.exists():
        return 0

    state = {}
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            state = {}

    _blocks = int(state.get("consecutive_blocks") or 0)
    if _blocks >= MAX_CONSECUTIVE_BLOCKS:
        state["consecutive_blocks"] = 0
        try:
            state_file.write_text(json.dumps(state), encoding="utf-8")
        except Exception:
            pass
        sys.stderr.write(
            "\n[orchdoc-stop-check] STANDING DOWN after %d consecutive blocks.\n\n"
            "    \u26d4 THIS IS NOT \"nothing was owed\". What it asked for is STILL OWED - an\n"
            "    OrchDoc update, or an instruction from the human waiting to be acknowledged.\n\n"
            "    The guard could not be satisfied %d times running, so something is WRONG WITH\n"
            "    THE ESCAPE, not with you repeating it. TELL THE HUMAN what is failing rather than\n"
            "    trying it again.\n\n" % (_blocks, _blocks))
        return 0

    pending = _pending_both(doc, orch)
    if pending:
        sys.stderr.write(_instruction_report(doc, orch, pending))
        _bump(state_file, dict(state, orch=orch), state, +1)
        return 2

    cur = _doc_hash(doc)
    last_hash = state.get("hash")
    last_iso = state.get("last_iso")
    blocked_on = state.get("blocked_hash")
    new_state = {"orch": orch, "hash": cur, "blocked_hash": blocked_on, "last_iso":
                 datetime.datetime.now().astimezone().isoformat(timespec="seconds")}
    try:
        state_file.write_text(json.dumps(new_state), encoding="utf-8")
    except Exception:
        pass

    if last_hash is None:
        return 0                      # first turn seen - nothing to compare against yet
    if cur != last_hash:
        new_state["blocked_hash"] = None
        try:
            state_file.write_text(json.dumps(new_state), encoding="utf-8")
        except Exception:
            pass
        return 0

    if blocked_on == cur:
        return 0                      # already said so once for this exact state - do not wedge
    new_state["blocked_hash"] = cur
    try:
        state_file.write_text(json.dumps(new_state), encoding="utf-8")
    except Exception:
        pass

    sys.stderr.write(
        "STOP BLOCKED - you finished a round and %s did not change.\n\n"
        "\u26d4 The OrchDoc is the human's instrument, not a file you tidy later. If anything this\n"
        "turn became a decision, question or to-do for the human, or a finding worth keeping, it\n"
        "belongs in the doc NOW - chat is ephemeral and the human cannot search it under load.\n\n"
        "  orchdoc.py add     --doc %s --kind decision|todo|work|finding \"<title>\"\n"
        "  orchdoc.py resolve --doc %s <ID> --ruling \"<what was decided>\"\n"
        "  orchdoc.py strike  --doc %s        # settled sub-items to - [x]\n"
        "  orchdoc.py plate   --doc %s        # regenerate the index the human reads\n"
        "  orchdoc.py check   --doc %s\n\n"
        "Then give the human each entry as a Reader link to click, e.g. %s -\n"
        "`add` and `plate` print that line for you; paste it.\n"
        "\u23e9 If this round genuinely produced nothing for the doc, say so in ONE LINE and\n"
        "stop again - it will let you through. Saying it out loud is the whole cost.\n"
        % (doc.name, orch, orch, orch, orch, orch, _reader_example(doc.name, orch))
    )
    _bump(state_file, new_state, state, +1)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)  # fail open, always - a broken guard must never wedge every orchestrator
