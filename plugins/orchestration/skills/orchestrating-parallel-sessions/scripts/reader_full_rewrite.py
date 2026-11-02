#!/usr/bin/env python3
"""The Reader's "00 - Full Rewrite Scripts" list (o9 W248, lane o9L1456).

<!-- subject: orchestration -->

The human: a list of the full rewrites where he marks each script reviewed or not reviewed and sees
the mark at a glance. This module is the data half: which scripts are on the list (from SOURCES
below), which of them are filed, the newest version and review file of each, and the reviewed
marks. The page half (rows, toggle, counts) is in render_orchdoc_reader.py.

SOURCES: one dict per orchestrator whose rewrites are listed. Adding a source is adding a dict.
  id, title, tag      the list key, the heading, and the author tag in a file name (" o5.md")
  order               where the run order comes from:
      {"json": [paths]}                     a list of {"label", "title"?, "record"?}
                                            (o5's ORDER.json, read from the workspace only)
      {"waves": rel, "first": [labels]}     o3's WAVES.json (waves of items) after `first`
  filed               how "filed" is decided:
      "record" (o5) the label's folder holds a "v<N> <tag>.md" whose N is above the version in
              the item's ORDER.json "record" file name (the version before the rewrite); the
              newest such file is the row's link. An item with no record version: any one.
      "disk"  the label's folder holds a "<LABEL> <Title> v<N> <tag>.md"
  review              where a script's review note is:
      "library"  versions/"<LABEL> <Title> v<N> <tag>-REVIEW.md" for the linked version N
      {"dir": "orch-work-summaries/o3-cloud-waves/wave-{wave:02d}/{label}/REVIEW.md"}
A source with no readable order and nothing filed shows a "list pending" note instead.

THE MARK is keyed "<source id>:<label>", the script's stable label, NOT a file name or folder:
a new version keeps the mark, and a folder that moves (approve moves 1-drafts to 3-final) keeps
it too. The version that was newest when the human ticked it is stored with the mark, so a row can say
"newer than the one you reviewed".

WHERE IT IS STORED: `<home state dir>/reader-state/full-rewrite-reviewed.json`
(`shared_state.home_state_dir()`, `~/.claude/state` unless CLAUDE_HOME_STATE_DIR names another).
That is outside every worktree, so a pull, a checkout or a Reader restart cannot touch it; it is
per machine, like the Reader's other per-machine state. Not in git: one commit per click would be
hundreds of commits on main.
"""
import json
import os
import re
import threading
import time
from pathlib import Path

try:
    import shared_state
except Exception:  # pragma: no cover
    shared_state = None

LIST_NAME = "00 - Full Rewrite Scripts"
STATE_FILE = "full-rewrite-reviewed.json"
KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,40}:[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$")

SOURCES = [
    {"id": "o5-sc", "title": "o5 SC rewrites", "tag": "o5",
     # o5: read ORDER.json from the workspace (the main checkout) only, never from the o5
     # worktree, which is reset before each filing and can be mid-change.
     "order": {"json": ["orch-work-summaries/o5-sc-corpus-rewrite-2026-10-22/ORDER.json"]},
     "filed": "record",
     "review": "library"},
    {"id": "o3-at", "title": "o3 AT rewrites", "tag": "o3",
     "order": {"waves": "orch-work-summaries/o3-cloud-waves/WAVES.json",
               "first": ["AT-Sl-N-07-008"]},
     "filed": "disk",
     "review": {"dir": "orch-work-summaries/o3-cloud-waves/wave-{wave:02d}/{label}/REVIEW.md"}},
]

#: Test hook: a path (or list) that replaces every "json" order path of every source.
ORDER_ENV = "READER_FR_ORDER_JSON"


def state_path():
    base = shared_state.home_state_dir() if shared_state else Path.home() / ".claude" / "state"
    return Path(base) / "reader-state" / STATE_FILE


class Marks:
    """The reviewed marks. Re-read from disk on every call (cheap; the file is small), written
    atomically under a lock, so a second Reader process or a restart sees the same marks."""

    def __init__(self, path=None):
        self.path = Path(path) if path else state_path()
        self._lock = threading.Lock()

    def load(self):
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            m = d.get("marks") if isinstance(d, dict) else None
            return m if isinstance(m, dict) else {}
        except (OSError, ValueError):
            return {}

    def set(self, key, reviewed, ver=None):
        """(ok, error). Sets or clears one mark."""
        if not isinstance(key, str) or not KEY_RE.match(key):
            return False, "bad key"
        with self._lock:
            marks = self.load()
            if reviewed:
                marks[key] = {"reviewed": True,
                              "ver": ver if isinstance(ver, int) and 0 <= ver < 10000 else None,
                              "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
            else:
                marks.pop(key, None)
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_name(self.path.name + ".tmp")
                tmp.write_text(json.dumps({"marks": marks}, indent=1, sort_keys=True),
                               encoding="utf-8")
                os.replace(str(tmp), str(self.path))
            except OSError as ex:
                return False, "could not save the mark (%s)" % ex
        return True, ""


# ---- order -----------------------------------------------------------------------------------

def _read_json(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _order_paths(src, workspace):
    env = os.environ.get(ORDER_ENV)
    if env and "json" in src["order"]:
        return [Path(x) for x in env.split(os.pathsep) if x]
    out = []
    for p in src["order"].get("json", []):
        out.append(Path(workspace) / p)
    return out


def order_of(src, workspace):
    """[{"label","title","wave"?}] in run order, or [] when the source's order is unreadable."""
    spec = src["order"]
    items = []
    if "json" in spec:
        for p in _order_paths(src, workspace):
            d = _read_json(p) if p.is_file() else None
            if isinstance(d, list):
                for it in d:
                    if isinstance(it, dict) and it.get("label"):
                        items.append({"label": str(it["label"]), "title": it.get("title") or "",
                                      "record_ver": record_ver(it.get("record"))})
                break
    elif "waves" in spec:
        d = _read_json(Path(workspace) / spec["waves"])
        if isinstance(d, list):
            for lab in spec.get("first", []):
                items.append({"label": lab, "title": "", "wave": None})
            for w in d:
                for it in (w.get("items") or []) if isinstance(w, dict) else []:
                    if isinstance(it, dict) and it.get("label"):
                        items.append({"label": str(it["label"]), "title": it.get("title") or "",
                                      "wave": w.get("wave")})
    seen, out = set(), []
    for it in items:
        if it["label"] not in seen:
            seen.add(it["label"])
            out.append(it)
    return out


# ---- filed -----------------------------------------------------------------------------------

def record_ver(record):
    """The N of a "... v<N> <tag>[ (in app)].md" record file name, or None."""
    m = re.search(r" v(\d+) ", record) if isinstance(record, str) else None
    return int(m.group(1)) if m else None


def known_key(key, workspace, sources=None):
    """True when `key` is "<source id>:<label>" for a source in SOURCES and a label that source
    lists (its order file or its pilot labels). The POST route refuses to set a mark on any
    other key."""
    if not isinstance(key, str) or not KEY_RE.match(key):
        return False
    sid, lab = key.split(":", 1)
    src = next((s for s in (sources if sources is not None else SOURCES) if s["id"] == sid), None)
    if src is None:
        return False
    return lab in src["order"].get("first", []) or lab in {o["label"]
                                                           for o in order_of(src, workspace)}


def _name_label(name):
    return name.split(" ", 1)[0]


def index_files(listing):
    """label -> list of file dicts of the listing (a script's folder and its versions/ folder)."""
    idx = {}
    for fo in listing.get("folders") or []:
        for f in fo.get("files") or []:
            idx.setdefault(_name_label(f["name"]), []).append(f)
    return idx


def _ver_re(tag):
    return re.compile(r" v(\d+) %s(?: \(in app\))?\.md$" % re.escape(tag))


def newest_text(files):
    """The newest text version of one script (any author): draft or rewrite with a version."""
    cand = [f for f in files if f.get("kind") in ("draft", "rewrite") and f.get("ver") is not None
            and not f.get("older_draft")]
    return max(cand, key=lambda f: (f["ver"], bool(f.get("in_app")), f["name"].casefold()),
               default=None)


def library_review(files, tag, ver=None, above=None):
    """The '<label> <Title> v<N> <tag>-REVIEW.md' of one script: the one for version `ver` when
    it exists, else the newest whose N is above `above` (any N when `above` is None), or None."""
    rx = re.compile(r" v(\d+) %s-REVIEW\.md$" % re.escape(tag))
    cand = [(int(m.group(1)), f) for f in files for m in [rx.search(f["name"])] if m]
    exact = [t for t in cand if ver is not None and t[0] == ver]
    if exact:
        return max(exact, key=lambda t: t[1]["name"])[1]
    cand = [t for t in cand if above is None or t[0] > above]
    return max(cand, key=lambda t: (t[0], t[1]["name"]))[1] if cand else None


def record_newest(files, rx, above):
    """The newest '<label> <Title> v<N> <tag>[ (in app)].md' whose N is above `above` (any N when
    `above` is None), as (N, file dict), or None. This is o5's "filed" rule."""
    cand = [(int(m.group(1)), f) for f in files for m in [rx.search(f["name"])] if m
            and (above is None or int(m.group(1)) > above)]
    return max(cand, key=lambda t: (t[0], "(in app)" in t[1]["name"], t[1]["name"].casefold()),
               default=None)


def title_from_name(name, label):
    m = re.match(r"^%s (.+?) v\d+ " % re.escape(label), name)
    return m.group(1).replace("-", " ") if m else ""


def build(listing, workspace, marks=None, sources=None):
    """The list for the page: [{"id","title","rows":[...],"pending":str|None,"reviewed":n,
    "total":n}]. A row: label, key, title, filed, file (listing dict or None), ver, review
    (listing dict, or {"rel": workspace path} for a note outside the library), reviewed,
    newer (a version newer than the reviewed one exists)."""
    marks = marks if marks is not None else {}
    idx = index_files(listing)
    out = []
    for src in (sources if sources is not None else SOURCES):
        order = order_of(src, workspace)
        tag = src["tag"]
        rx = _ver_re(tag)
        rows = []
        for it in order:
            lab = it["label"]
            files = idx.get(lab, [])
            if src["filed"] == "record":
                hit = record_newest(files, rx, it.get("record_ver"))
                f = hit[1] if hit else None
                if f is not None and f.get("ver") is None:
                    f = dict(f, ver=hit[0])
                filed = f is not None
            else:
                filed = any(rx.search(fl["name"]) for fl in files)
                f = newest_text(files) if filed else None
            key = "%s:%s" % (src["id"], lab)
            mk = marks.get(key) or {}
            review = None
            if filed:
                if src["review"] == "library":
                    review = library_review(files, tag, f.get("ver") if f else None,
                                            it.get("record_ver"))
                elif isinstance(src["review"], dict) and it.get("wave") is not None:
                    rel = src["review"]["dir"].format(wave=int(it["wave"]), label=lab)
                    if (Path(workspace) / rel).is_file():
                        review = {"rel": rel}
            title = (it.get("title") or (title_from_name(f["name"], lab) if f else "")
                     or lab)
            rows.append({"label": lab, "key": key, "title": title, "filed": filed, "file": f,
                         "ver": f.get("ver") if f else None, "review": review,
                         "reviewed": bool(mk.get("reviewed")),
                         "newer": bool(mk.get("reviewed") and f is not None
                                       and isinstance(mk.get("ver"), int)
                                       and f.get("ver") is not None and f["ver"] > mk["ver"])})
        pending = None
        if not rows:
            pending = "%s (list pending from %s)" % (src["title"], tag)
        out.append({"id": src["id"], "title": src["title"], "rows": rows, "pending": pending,
                    "reviewed": sum(1 for r in rows if r["reviewed"]), "total": len(rows)})
    return out
