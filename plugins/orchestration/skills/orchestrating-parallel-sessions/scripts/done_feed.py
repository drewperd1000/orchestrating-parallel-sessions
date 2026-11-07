#!/usr/bin/env python3
"""What got done, read from git history of the OrchDocs. No model calls; deterministic.

<!-- subject: orchestration -->

It costs no Claude usage to run: every item is computed from `git log` and the doc text.

WHAT COUNTS AS AN ITEM. Every non-merge commit reachable from the ref that changed an
`ORCHESTRATOR-DECISIONS-o<N>.md` is diffed against its parent, entry by entry:

    ticked    a `- [x]` box that was not ticked in that entry before (text = its first sentence)
    resolved  status moved to RESOLVED / ANSWERED / SUPERSEDED (text = "OLD -> NEW")
    done      status moved to DONE / SHIPPED
    new       an entry id that did not exist in that doc before

KEYED BY (entry id, box text) ACROSS THE WHOLE DOC, never by position. Entries move between
sections (the archive into `## §99.N`, the resolved sections) byte-identically, so a move
changes nothing that is keyed and adds no item. A box ticked in commit A and moved in commit B
appears once, at A. A box whose text is re-worded after it was already ticked (a continuation
line added, a typo fixed) is paired with its old ticked version and adds nothing.

THE PARSER IS orchdoc.py's, like the Reader's.

WHICH REF: `origin/main`, with NO fetch. The shared checkout's local `main` received the same
commits with a median 35 s but a 90th percentile of 58 minutes and a maximum of 22 hours,
because only `deliver.py` advances it and the Reader's own script saves bypass it (it was 3
commits behind when measured). A `git fetch` costs about 1.7 s and writes into the shared .git;
it would only add commits pushed from another machine, and none of the 276 arrived that way.

CACHE. A commit's diff never changes, so its items are cached per commit hash in
`$DONE_FEED_STATE_DIR/cache.json` (default `~/.claude/state/done-feed/`). A refresh only
parses commits it has not seen. Inside one process the cache is also held in memory and
filled commit by commit, so `feed(block=False)` can answer at once with what is ready plus
`loading: true`, while `warm()` fills the rest in a background thread. The Reader warms the
48h window that way when it starts.
"""
import argparse
import bisect
import difflib
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from no_window import flags
except ImportError:  # a partial delivery must not take the tool down
    def flags(extra=0):
        return extra

import orchdoc  # noqa: E402

STATE_ENV = "DONE_FEED_STATE_DIR"
DEFAULT_STATE_DIR = Path.home() / ".claude" / "state" / "done-feed"
DEFAULT_REF = "origin/main"
DEFAULT_WINDOW = 48 * 3600
# Bump when the item rules change, so an old cache is discarded instead of mixed in.
PARSER_VERSION = 1
# Cached commits older than this (or than the window asked for, if longer) are dropped.
KEEP_SECONDS = 8 * 86400

DOC_PATHSPEC = "ORCHESTRATOR-DECISIONS-o*.md"
DOC_NAME_RE = re.compile(r"^ORCHESTRATOR-DECISIONS-(o\d+)\.md$")
KINDS = ("ticked", "resolved", "done", "new")
CLOSE_KIND = {"RESOLVED": "resolved", "ANSWERED": "resolved", "SUPERSEDED": "resolved",
              "DONE": "done", "SHIPPED": "done"}
ZERO_SHA = "0" * 40

SUFFIX_RE = re.compile(r"\s*_\(was\s+[A-Z]+\d+[a-z]?\s*[-–—]\s*([A-Z]+)\)_\s*$")
LEAD_ID_RE = re.compile(r"^[A-Z]{1,3}\d+[a-z]?\b\s*(?:[-–—:]\s*)?")
BOX_RE = re.compile(r"^(\s*)- \[[xX]\]\s+(.*)$")
TEXT_MAX = 200
TITLE_MAX = 120
# A ticked box text at least this long that was ticked ANYWHERE in the previous version of
# the doc is a box that moved between entries, not new work. Shorter texts ("Done.") are too
# generic to match across entries.
MOVED_MIN = 20

_LOCK = threading.Lock()


def state_dir():
    return Path(os.environ.get(STATE_ENV) or DEFAULT_STATE_DIR)


def parse_window(s):
    """'48h', '7d', '90m' or a bare number of hours -> seconds. None for anything else."""
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*([mhd]?)\s*$", str(s or ""), re.I)
    if not m:
        return None
    n = float(m.group(1))
    unit = (m.group(2) or "h").lower()
    return int(n * {"m": 60, "h": 3600, "d": 86400}[unit])


# ------------------------------------------------------------------------------------------
# git
# ------------------------------------------------------------------------------------------

def _git(repo, *args):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                       creationflags=flags())
    return r.returncode, r.stdout.decode("utf-8", errors="replace")


class _Blobs:
    """One `git cat-file --batch` process for the whole run, instead of two per commit."""

    def __init__(self, repo):
        self.p = subprocess.Popen(["git", "-C", str(repo), "cat-file", "--batch"],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, creationflags=flags())

    def text(self, sha):
        self.p.stdin.write(sha.encode("ascii") + b"\n")
        self.p.stdin.flush()
        head = self.p.stdout.readline().split()
        if len(head) != 3:
            return None
        size = int(head[2])
        data = self.p.stdout.read(size)
        self.p.stdout.read(1)
        # A byte that is not UTF-8 must not stop the feed; it becomes U+FFFD.
        return data.decode("utf-8", errors="replace")

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=10)
        except Exception:
            try:
                self.p.kill()
            except Exception:
                pass


def list_commits(repo, ref, since_ts):
    """[(sha, commit_time, [(doc_slug, old_blob, new_blob)])], newest first."""
    rc, out = _git(repo, "log", "--no-merges", "--full-history", "--raw", "--no-abbrev",
                   "--no-renames", "--since=@%d" % int(since_ts), "--format=%x1e%H %ct",
                   ref, "--", DOC_PATHSPEC)
    if rc != 0:
        return None
    commits = []
    for rec in out.split("\x1e"):
        lines = [ln for ln in rec.split("\n") if ln.strip()]
        if not lines:
            continue
        sha, ct = lines[0].split()
        files = []
        for ln in lines[1:]:
            # :100644 100644 <old> <new> M<TAB>path
            if not ln.startswith(":") or "\t" not in ln:
                continue
            meta, path = ln.split("\t", 1)
            parts = meta.split()
            m = DOC_NAME_RE.match(path.strip())
            if m and len(parts) >= 4:
                files.append((m.group(1), parts[2], parts[3]))
        commits.append((sha, int(ct), files))
    return commits


# ------------------------------------------------------------------------------------------
# one version of one doc -> entries
# ------------------------------------------------------------------------------------------

def strip_md(s):
    s = re.sub(r"<!--.*?-->", " ", s)
    s = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"<[^>\n]+>", " ", s)
    s = s.replace("**", "").replace("__", "").replace("~~", "").replace("`", "")
    return re.sub(r"\s+", " ", s).strip()


def _clip(s, n):
    if len(s) <= n:
        return s
    cut = s[:n].rsplit(" ", 1)[0]
    return (cut if len(cut) > n // 2 else s[:n]).rstrip(" ,;:-") + " ..."


def first_sentence(s, n=TEXT_MAX):
    s = strip_md(s)
    m = re.search(r"[.!?](?=\s+[-*A-Z0-9(\"'\[])", s[15:])
    if m:
        s = s[:15 + m.end()]
    return _clip(s, n)


def _norm(s):
    return re.sub(r"\s+", " ", s).strip()


def _ticked_boxes(lines):
    """[(0-based line index, box text)] for every `- [x]` box outside a code fence, with its
    continuation lines joined on."""
    # The `fenced` column of orchdoc.scan_structure, from the same is_fence_line toggle.
    # Calling scan_structure itself a second time was a third of the cold-run time.
    fenced, inside = [], False
    for raw in lines:
        if orchdoc.is_fence_line(raw):
            inside = not inside
            fenced.append(True)
        else:
            fenced.append(inside)
    out = []
    n = len(lines)
    for i, raw in enumerate(lines):
        if fenced[i] or "[" not in raw:
            continue
        b = BOX_RE.match(raw)
        if not b:
            continue
        ind = len(b.group(1))
        parts = [b.group(2).strip()]
        j = i + 1
        while j < n:
            nx = lines[j]
            if not nx.strip() or nx.startswith("#") or fenced[j]:
                break
            lead = len(nx) - len(nx.lstrip())
            if nx.lstrip().startswith("- [") and lead <= ind + 2:
                break
            if lead <= ind:
                break
            parts.append(nx.strip())
            j += 1
        out.append((i, _norm(" ".join(parts))))
    return out


def parse_doc(text):
    """({entry_id: {"title", "status", "ticked": [box text, ...]}}, set of every ticked text
    in the doc). The set includes boxes that sit under no entry heading, so a box that is
    moved INTO an entry from anywhere in the previous version is recognised as not new."""
    lines = text.split("\n")
    entries, _ = orchdoc.parse_entries(lines)
    boxes_all = _ticked_boxes(lines)
    box_lines = [i for i, _t in boxes_all]
    every = {t for _i, t in boxes_all}
    starts = sorted(e["line"] for e in entries)
    model = {}
    for e in entries:
        ln = e["line"]
        end = ln - 1 + e["body"].count("\n") + 1          # last line of the body, 1-based
        k = bisect.bisect_right(starts, ln)
        own_end = min(end, starts[k] - 1) if k < len(starts) else end  # nested entry: own boxes
        demoted = e.get("id_demoted")
        status = None
        if demoted:
            sm = SUFFIX_RE.search(e["title"])
            status = sm.group(1).upper() if sm else None
        if not status:
            status = orchdoc.status_of("\n".join(lines[ln - 1:own_end]))
        title = SUFFIX_RE.sub("", e["title"]) if demoted else e["title"]
        title = orchdoc.strip_decoration(title)
        if not demoted:
            title = LEAD_ID_RE.sub("", title, count=1)
        # own lines are 0-based indices ln-1 .. own_end-1
        lo = bisect.bisect_left(box_lines, ln - 1)
        hi = bisect.bisect_left(box_lines, own_end)
        boxes = [t for _i, t in boxes_all[lo:hi]]
        cur = model.get(e["id"])
        if cur is None:
            cur = model[e["id"]] = {"title": _clip(strip_md(title), TITLE_MAX),
                                    "status": status, "ticked": [], "archived": e["archived"]}
        elif cur["archived"] and not e["archived"]:
            # A live copy of an id outranks an archived one for title and status.
            cur.update(title=_clip(strip_md(title), TITLE_MAX), status=status,
                       archived=False)
        for bx in boxes:
            if bx not in cur["ticked"]:
                cur["ticked"].append(bx)
    return model, every


def _reworded(box, vanished):
    """The old ticked box this one is a re-wording of, or None."""
    for v in vanished:
        if box.startswith(v) or v.startswith(box):
            return v
    for v in vanished:
        sm = difflib.SequenceMatcher(None, v[:400], box[:400], autojunk=False)
        if sm.real_quick_ratio() >= 0.6 and sm.quick_ratio() >= 0.6 and sm.ratio() >= 0.6:
            return v
    return None


def diff_docs(old, new, doc):
    """Items for one doc in one commit. `old` is None when the commit created the file."""
    items = []
    new_model, _ = new
    if old is None:
        # A doc created in this commit: its entries are new, and nothing in it was ticked
        # "in this commit" in any sense that can be told apart from copying.
        for eid, ne in new_model.items():
            items.append({"doc": doc, "id": eid, "title": ne["title"], "kind": "new",
                          "text": "New entry" + (" - status %s" % ne["status"]
                                                 if ne["status"] else "")})
        return items
    old_model, old_every = old
    for eid, ne in new_model.items():
        oe = old_model.get(eid)
        base = {"doc": doc, "id": eid, "title": ne["title"]}
        if oe is None:
            items.append(dict(base, kind="new",
                              text="New entry" + (" - status %s" % ne["status"]
                                                  if ne["status"] else "")))
            old_ticked = set()
            vanished = []
        else:
            ns, os_ = ne["status"], oe["status"]
            if ns != os_ and ns in CLOSE_KIND and os_ not in CLOSE_KIND:
                items.append(dict(base, kind=CLOSE_KIND[ns],
                                  text="%s -> %s" % (os_ or "no status", ns)))
            old_ticked = set(oe["ticked"])
            new_set = set(ne["ticked"])
            vanished = [b for b in oe["ticked"] if b not in new_set]
        for bx in ne["ticked"]:
            if bx in old_ticked:
                continue
            if len(bx) >= MOVED_MIN and bx in old_every:
                continue
            was = _reworded(bx, vanished)
            if was is not None:
                vanished.remove(was)
                continue
            items.append(dict(base, kind="ticked", text=first_sentence(bx)))
    return items


# ------------------------------------------------------------------------------------------
# the feed
# ------------------------------------------------------------------------------------------

def _load_cache(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") == PARSER_VERSION and isinstance(data.get("commits"), dict):
            return data["commits"]
    except Exception:
        pass
    return {}


def _save_cache(path, commits):
    tmp = path.with_name("%s.%d.%d.tmp" % (path.name, os.getpid(), threading.get_ident()))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps({"version": PARSER_VERSION, "commits": commits},
                                  ensure_ascii=False), encoding="utf-8")
        os.replace(str(tmp), str(path))
        return True
    except Exception:
        try:
            tmp.unlink()
        except Exception:
            pass
        return False


def _compute(repo, commits, store, failed):
    """Diff the commits given, writing each result into `store` (sha -> {"t", "items"}) under
    _MEM_LOCK AS IT IS DONE, so a caller that does not wait sees partial progress. A commit
    that cannot be read goes into `failed` (sha -> message) instead. Returns the count done."""
    done = 0
    blobs = _Blobs(repo)
    memo = {}          # blob sha -> parsed model; small, because versions arrive in order
    order = []

    def model(sha):
        if sha in memo:
            return memo[sha]
        text = blobs.text(sha)
        m = parse_doc(text) if text is not None else ({}, set())
        memo[sha] = m
        order.append(sha)
        if len(order) > 6:
            memo.pop(order.pop(0), None)
        return m

    try:
        # Oldest first, so a commit's old version is usually the previous commit's new one.
        for sha, ct, files in sorted(commits, key=lambda c: c[1]):
            items = []
            try:
                for doc, old_b, new_b in files:
                    if new_b == ZERO_SHA:
                        continue           # the doc was deleted; nothing was done in it
                    old = None if old_b == ZERO_SHA else model(old_b)
                    items.extend(diff_docs(old, model(new_b), doc))
            except Exception as e:  # one bad commit must not stop the feed
                with _MEM_LOCK:
                    failed[sha] = "%s: %s" % (sha[:10], e)
                continue
            with _MEM_LOCK:
                store[sha] = {"t": ct, "items": items}
            done += 1
    finally:
        blobs.close()
    return done


# ⭐ ONE IN-MEMORY COPY OF THE CACHE PER PROCESS, filled commit by commit. The Reader serves
# /done/data from it without waiting (block=False), while a background thread fills it; the
# disk file is only how the copy survives a restart. Keyed by the cache path, so a test that
# points DONE_FEED_STATE_DIR somewhere else gets a fresh copy.
# A commit that failed is remembered for this process only (never saved), so it is reported
# rather than retried on every poll, and a restart tries it again.
_MEM = {"path": None, "commits": {}, "failed": {}}
_MEM_LOCK = threading.Lock()      # guards _MEM; held only for dict reads and writes
_WORK = {"thread": None}          # the one background filler, if any


def _store():
    cpath = state_dir() / "cache.json"
    with _MEM_LOCK:
        if _MEM["path"] != str(cpath):
            _MEM["path"] = str(cpath)
            _MEM["commits"] = _load_cache(cpath)
            _MEM["failed"] = {}
        return cpath, _MEM["commits"], _MEM["failed"]


def _reset_memory():
    """Forget the in-memory copy, so the next call reads the disk cache (for tests)."""
    with _MEM_LOCK:
        _MEM["path"] = None
        _MEM["commits"] = {}
        _MEM["failed"] = {}


def _fill(repo, commits, now, window):
    """Compute whatever in `commits` is not yet stored, then save. One filler at a time."""
    cpath, store, failed = _store()
    with _LOCK:
        with _MEM_LOCK:
            todo = [c for c in commits if c[0] not in store and c[0] not in failed]
        if not todo:
            return 0, None
        n = _compute(repo, todo, store, failed)
        keep_after = now - max(KEEP_SECONDS, int(window) + 86400)
        with _MEM_LOCK:
            disk = _load_cache(cpath)          # keep what another process saved meanwhile
            disk.update(store)
            for k in [k for k, v in disk.items() if v.get("t", 0) < keep_after]:
                disk.pop(k, None)
                store.pop(k, None)
            snapshot = dict(disk)
        return n, _save_cache(cpath, snapshot)


def warm(repo, ref=DEFAULT_REF, window=DEFAULT_WINDOW):
    """Start filling the cache for a window in a daemon thread and return at once.
    If a filler is already running, returns that thread instead of starting a second."""
    with _MEM_LOCK:
        t = _WORK["thread"]
        if t is not None and t.is_alive():
            return t
        t = threading.Thread(target=feed, args=(repo, ref, window), name="done-feed-warm",
                             daemon=True)
        _WORK["thread"] = t
    t.start()
    return t


def warming():
    """True while a background filler is running."""
    with _MEM_LOCK:
        t = _WORK["thread"]
    return t is not None and t.is_alive()


def feed(repo, ref=DEFAULT_REF, window=DEFAULT_WINDOW, now=None, block=True):
    """The completed-work items in the window, newest first, with counts and timings.

    block=True computes every commit it has not seen before returning. block=False returns
    what is already computed at once, sets "loading" when commits are still missing, and
    starts a background filler for them - the Reader's page uses this so the human is never left
    looking at a request that has not answered."""
    t0 = time.time()
    now = int(now if now is not None else time.time())
    since = now - int(window)
    rc, head = _git(repo, "rev-parse", "--verify", "--quiet", ref + "^{commit}")
    if rc != 0:
        return {"ok": False, "error": "ref %s does not resolve in %s" % (ref, repo)}
    commits = list_commits(repo, ref, since)
    if commits is None:
        return {"ok": False, "error": "git log failed on %s" % ref}
    computed, saved = 0, None
    if block:
        computed, saved = _fill(repo, commits, now, window)
    _cpath, store, failed = _store()
    items, missing, errors = [], 0, []
    with _MEM_LOCK:
        for sha, ct, _files in commits:
            got = store.get(sha)
            if got is None:
                if sha in failed:
                    errors.append(failed[sha])
                else:
                    missing += 1
                continue
            for it in got["items"]:
                items.append(dict(it, time=ct, commit=sha))
    if missing and not block:
        warm(repo, ref, window)
    items.sort(key=lambda x: -x["time"])        # stable: within a commit, doc order holds
    counts = {}
    for it in items:
        counts[it["doc"]] = counts.get(it["doc"], 0) + 1
    return {"ok": True, "ref": ref, "head": head.strip(), "window": int(window),
            "since": since, "generated": now, "commits": len(commits),
            "computed": computed, "cache_saved": saved, "errors": errors,
            "loading": bool(missing), "pending": missing,
            "elapsed": round(time.time() - t0, 3), "counts": counts, "items": items}


def _slug_key(s):
    m = re.match(r"o(\d+)$", s)
    return int(m.group(1)) if m else 10 ** 6


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="List completed OrchDoc work from git history.")
    ap.add_argument("--since", default="48h", help="window: 6h, 48h, 7d ... (default 48h)")
    ap.add_argument("--ref", default=DEFAULT_REF, help="ref to read (default origin/main)")
    ap.add_argument("--repo", default=str(orchdoc.PROJECTS), help="the workspace repo")
    ap.add_argument("--json", action="store_true", help="print the whole result as JSON")
    a = ap.parse_args(argv)
    window = parse_window(a.since)
    if window is None:
        print("[FAIL] --since takes 6h, 48h, 7d, 90m or a number of hours")
        return 2
    r = feed(a.repo, a.ref, window)
    if not r["ok"]:
        print("[FAIL] " + r["error"])
        return 1
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0
    for it in r["items"]:
        print("%s  %-4s %-6s %-8s %s" % (time.strftime("%m-%d %H:%M", time.localtime(it["time"])),
                                         it["doc"], it["id"], it["kind"], it["text"]))
    print("")
    print("done_feed: %d items from %d commits (%d computed, %d from cache) in %.2fs, "
          "ref %s @ %s" % (len(r["items"]), r["commits"], r["computed"],
                           r["commits"] - r["computed"], r["elapsed"], r["ref"], r["head"][:8]))
    print("per orchestrator: " + ", ".join(
        "%s %d" % (k, r["counts"][k]) for k in sorted(r["counts"], key=_slug_key)))
    kinds = {}
    for it in r["items"]:
        kinds[it["kind"]] = kinds.get(it["kind"], 0) + 1
    print("per kind: " + ", ".join("%s %d" % (k, kinds.get(k, 0)) for k in KINDS))
    if r["cache_saved"] is False:
        print("[WARN] the cache could not be written to %s" % state_dir())
    for e in r["errors"]:
        print("[WARN] skipped commit %s" % e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
