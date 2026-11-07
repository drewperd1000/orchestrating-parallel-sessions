#!/usr/bin/env python3
"""A reader for the OrchDocs. Three columns, served over HTTP, never scrolled by hand.

⛔ THE PROBLEM IT SOLVES, AND THE ONE IT MUST NOT CREATE.

The human reads these docs to find ONE entry. The generated plate table at the top links to each
one, and those links work on GitHub and nowhere else - Obsidian matches a heading's TEXT
where GitHub matches its SLUG, and one markdown link cannot satisfy both. The anchors are
CORRECT; the two readers simply disagree about what an anchor is. So this does not touch
them. It renders the document somewhere navigation is a real feature instead of a link.

⚠️ AND THE FAILURE THAT WOULD LOOK LIKE SUCCESS: a reader that silently drops an entry.
`test_orchdoc_reader.py` asserts per-doc that the number of entries rendered equals the number
`orchdoc.parse_entries` returns, with a deliberately-dropped fixture as the control proving the
assertion can fail.

⭐ THE PARSER IS `orchdoc.py`'s, NOT A SECOND COPY. Every question about what an entry is -
where it begins, what its id and status and owner are, whether it is archived, whether it
is on the human's plate - is answered by importing orchdoc and calling its functions. A reader
that disagreed with the checker about where an entry begins would show the human a different
document than the tool validates, which is worse than having no reader.

⛔ FILTERS HIDE, THEY DO NOT REMOVE. Every entry is in the DOM always; a filter toggles a
CSS class. That is deliberate - it makes it structurally impossible for a filter bug to
drop an entry, which is the same failure as a parser bug and much harder to notice.

⭐ CROSS-DOC REFERENCES BECOME LINKS - AND ONLY WHEN THEY RESOLVE. They are now anchors whose
href is `#<doc>/<ID>` - the hash `route()` has always understood, the one clicking an entry
already writes - so there is no second navigation path and no second idea of what an entry id
is.

⛔ A REFERENCE THE INDEX CANNOT RESOLVE DOES NOT BECOME A LINK.

This module writes NOTHING. It never calls `orchdoc.py plate` (that command REGENERATES the
index inside the doc); it calls `build_plate_block`, the same builder `plate` writes with,
and reads the rows back.
"""
import argparse
import copy
import html
import json
import os
import posixpath
import queue
import re
import subprocess
import time
try:
    from no_window import NO_WINDOW
except ImportError:  # a partial delivery must not take the tool down
    NO_WINDOW = 0x08000000 if os.name == "nt" else 0
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import localhost_write_gate as GATE  # noqa: E402


def _reader_gate(*args, **kw):
    """The Reader writes notes as the human, so a write must carry the header only a browser sends
    for a fetch from this page (`Sec-Fetch-Site: same-origin`); a curl or a script on this
    computer without it is refused. The human's saves from the page send it."""
    kw.setdefault("browser_only", True)
    return GATE.check_request(*args, **kw)


import orchdoc  # noqa: E402
import reader_text as TEXT  # noqa: E402 - search matching and the line diff, needed by every Reader
import secret_patterns  # noqa: E402
try:
    # ⭐ THE SCRIPT FEATURES ARE ONE OPTIONAL GROUP. A setup with no document library does not
    # carry these files (the published copy does not), and the Reader then starts without the
    # Scripts view, the Compare view, script notes, the library runner and the rule boxes.
    # All-or-nothing on purpose: a half-imported group would leave routes that reach a module
    # that is not there. `HAS_SCRIPTS` is the one switch the rest of this file reads.
    import reader_scripts as SCRIPTS  # noqa: E402
    import reader_full_rewrite as FRW
    import script_note_store as SNOTES
    import reader_library_runner as RUNNER  # noqa: E402
    import reader_compare as COMPARE
    from script_notes import strike_spans
    HAS_SCRIPTS = True
except ModuleNotFoundError as _missing:  # the script files are not installed
    # Only a file of the group being absent counts. Any other failure, in a file that IS here,
    # still stops the Reader with its own message, as it did before the group was optional.
    _SCRIPT_GROUP = ("reader_scripts", "reader_full_rewrite", "script_note_store",
                     "reader_library_runner", "reader_compare", "script_notes", "training_header",
                     "file_script", "check_script", "script_git_dates", "pause_cues")
    if (_missing.name or "") not in set(_SCRIPT_GROUP):
        raise
    # A tree with none of the group's files is the public copy and starts without the features.
    # A tree where any of them IS present and another is missing is a broken install: stop.
    if any(os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), n + ".py"))
           for n in _SCRIPT_GROUP):
        raise
    SCRIPTS = FRW = SNOTES = RUNNER = COMPARE = None
    HAS_SCRIPTS = False

    def strike_spans(s):
        """Without script_notes: every `~~text~~` on one line is struck."""
        return [m.span() for m in re.finditer(r"~~[^~\n]+~~", s)]
RULECODES = None
if HAS_SCRIPTS:
    try:
        import rule_codes_index as RULECODES  # noqa: E402
    except Exception as _e:  # a partial delivery must not take the reader down, but must say so
        RULECODES = None
        sys.stderr.write("render_orchdoc_reader: rule_codes_index did not load (%s: %s); the "
                         "Writing Rules button is off\n" % (type(_e).__name__, _e))
try:
    import reader_docs as DOCS  # noqa: E402 - the folders the user chose for document review
except ImportError:  # a partial delivery must not take the reader down: no Review documents link
    DOCS = None


def documents_folders():
    """The folders named in the `doc_folders` setting, or [] (then no link and no page)."""
    return DOCS.folders() if DOCS is not None else []


try:
    import done_feed as DONE  # noqa: E402  - the /done page; read-only, from git history
except Exception:  # a partial delivery must not take the reader down; /done says so instead
    DONE = None
try:
    import reader_landing as LANDING
except Exception:  # a partial delivery must not take the reader down: saves then land inline
    LANDING = None
try:
    import reader_restart as RESTART
except Exception:  # a partial delivery must not take the reader down: it then never restarts
    RESTART = None

# ⛔ 127.0.0.1 AND NOTHING ELSE, AND THERE IS NO FLAG TO CHANGE IT. This server could only
# READ until notes shipped; it can now edit a document that eleven sessions depend on, so the
# `--host` argument came out. A bind address that an invocation can move is a bind address
# that will eventually be moved, and `0.0.0.0` on a machine on a cafe network is the whole
# workspace's decision log, writable. `serve_daily_dashboard.py` has never had the flag for
# the same reason, and its test asserts the absence.
HOST = GATE.HOST

DOC_GLOB = "ORCHESTRATOR-DECISIONS-o*.md"
DOC_RE = re.compile(r"^ORCHESTRATOR-DECISIONS-(o\d+)$")


# ==========================================================================================
# THE MODEL - every fact about an entry comes from orchdoc, none of it from here.
# ==========================================================================================

# Status -> the dot's meaning. Grouped by what the reader needs to DO about it, which is not
# the same as orchdoc's VALID_STATUS grouping: CONFIRMED/RECORDED/ADOPTED are informational
# records, not closed work, and colouring them the same green as RESOLVED would tell the human
# something finished when nothing ever started.
STATUS_CLASS = {
    "OPEN": "open",
    # \u26d4 BLOCKED AND SEQUENCED ARE NOT "OPEN".
    #
    # \u26a0\ufe0f SEQUENCED IS NOT LISTED HERE, AND THAT IS ON PURPOSE. Present in the source,
    # never executed, which is the exact defect this colour change exists to make visible.
    #
    # It stays "hold" on the merits too: its VALID_STATUS comment says it is "not BLOCKED
    # (nothing broke) and not DEFERRED (it will happen)". PAUSED and DEFERRED are amber and
    # also mean live-but-not-now, so SEQUENCED already read distinctly from OPEN's red. BLOCKED
    # was the one wearing OPEN's colour, and BLOCKED is what moved.
    "BLOCKED": "blocked",
    # \u26d4 ONLY THE NORMALIZED SPELLING BELONGS HERE. It never did: the renderer computes
    # `status = orchdoc.status_of(e["body"])` and passes THAT here, and status_of normalizes all
    # three to "IN PROGRESS" first. The two extra keys were unreachable and are gone.
    #
    # That is why the check below is written against the NORMALIZED vocabulary: it is the only
    # form status_class is ever given.
    #
    # PAUSED, DEFERRED and SEQUENCED stay "hold". STATUS_GROUPS (below) names each class for the
    # legend at the top of a doc.
    "IN PROGRESS": "prog", "PAUSED": "hold", "DEFERRED": "hold",
    "RESOLVED": "done", "ANSWERED": "done", "DONE": "done",
    "SHIPPED": "done", "SUPERSEDED": "done", "ARCHIVED": "done",
    "CONFIRMED": "info", "RECORDED": "info", "ADOPTED": "info",
    # \u26d4 THIS MAP WAS HAND-KEPT AND HAD ALREADY DRIFTED: RETRACTED existed in orchdoc's
    # VALID_STATUS and was missing here, so a retracted entry - one that says it was WRONG -
    # fell through to the default class. Added with SEQUENCED - and the claim that once stood
    # here, that "the test below now fails when the two go out of step", was FALSE for as long
    # as it stood: the function below returns a list and nothing called it. It is wired now, to
    # a real test and to render time.
    "SEQUENCED": "hold", "RETRACTED": "info",
}


def _status_class_covers_vocabulary():
    """Every status orchdoc accepts must have a colour here.

    \u2b50 The map is a VIEW of orchdoc's vocabulary, so the only safe hand-kept part is the
    JUDGEMENT of which colour - never the membership. RETRACTED proved that by going missing.

    A function returning a list is not a check until something reads the list. It now has a
    test (test_status_vocabulary.py) and is called at render time.

    \u26a0\ufe0f IT COMPARES THE NORMALIZED FORM ON PURPOSE. The renderer passes
    `orchdoc.status_of(body)` to status_class, so the normalized spelling is the only one that
    is ever looked up. Comparing raw VALID_STATUS members instead would demand keys the code
    can never reach - which is exactly the dead entry this check's own author added and then
    removed.
    """
    return sorted({orchdoc.status_of("**Status:** %s" % s) for s in orchdoc.VALID_STATUS}
                  - set(STATUS_CLASS))

KIND_LABEL = {"D": "Decisions", "Q": "Questions", "T": "To-Dos", "A": "Actions",
              "F": "Findings", "S": "Findings", "W": "Claude's plate"}


def discover_docs(workspace):
    """
    """
    out = []
    for p in Path(workspace).glob(DOC_GLOB):
        m = DOC_RE.match(p.stem)
        if m:
            out.append((m.group(1), p))
    out.sort(key=lambda t: int(t[0][1:]))
    return out


def status_class(status):
    return STATUS_CLASS.get(status or "", "unk")


# The classes status_class() can return, in the order the legend shows them, each with the
# words for its pill and for its hover text. This is the one place the colour GROUPS are
# named. The pills in the legend are the entries' own `.tag.<class>` pills, so the legend
# cannot drift from what an entry shows, and the member statuses in the hover text are read
# from STATUS_CLASS rather than typed again. "unk" is the grey pill: no Status line, or a word
# orchdoc does not accept.
STATUS_GROUPS = (
    ("open", "OPEN", "needs work or a decision"),
    ("prog", "IN PROGRESS", "being worked on now"),
    ("blocked", "BLOCKED", "waiting on something outside the item"),
    ("hold", "PAUSED / DEFERRED", "on hold by a decision, or queued for later"),
    ("done", "DONE", "finished, answered, shipped or replaced"),
    ("info", "ON RECORD", "confirmed, recorded or adopted: information, not work"),
    ("unk", "NO STATUS", "no Status line, or a status word the reader does not know"),
)


def _status_groups_cover_classes():
    """Every class status_class() can return must have a legend row, and no row may name a
    class nothing returns. Returns the sorted differences (empty when they agree)."""
    produced = set(STATUS_CLASS.values()) | {"unk"}
    named = [g[0] for g in STATUS_GROUPS]
    return sorted((produced - set(named)) | (set(named) - produced)
                  | {c for c in named if named.count(c) > 1})


def kind_of(eid):
    m = re.match(r"^([A-Z]+)", eid or "")
    return m.group(1)[0] if m else "?"


def on_humans_plate(entry):
    """The plate predicate, EXACTLY as `build_plate_block` applies it.

    ⛔ Three conditions, and all three are orchdoc's own functions. `test_orchdoc_reader.py`
    cross-checks the result against `build_plate_block`'s rendered rows per doc, so this
    predicate cannot drift from the generator without a test failing.
    """
    return (not entry.get("archived")
            and orchdoc.status_of(entry["body"]) in orchdoc.PLATE_STATUS
            and orchdoc.owner_is_human(entry["body"])
            and orchdoc.today_on_plate(entry))


def entry_body_markdown(entry):
    """(content without the heading, [notes]) - the two halves rendered separately.

    `parse_entries` includes the heading in `body` (body = lines[line-1:end]); the reader
    renders the title itself, so repeating it would show every entry's name twice.

    ⛔ AND THE HUMAN'S NOTES COME OUT OF THE PROSE. They live IN the entry - that is what
    makes them durable and what puts them in front of the orchestrator who owns it - but
    rendering one inline would drop a blockquote into the middle of that orchestrator's
    writing, where it reads as part of their argument.
    """
    body = entry.get("body") or ""
    nl = body.find("\n")
    return orchdoc.strip_notes(body[nl + 1:] if nl != -1 else "")


# ==========================================================================================
# §1 LINKS AND DOCS
#
# ⛔ EVERY ORCHDOC HAS ONE AND THE READER SHOWED NONE OF THEM. It is not an entry - it has no
# `### <ID>` heading and no status - so `parse_entries` never yields it, and a reader built
# entirely from entries dropped the section the human opens a doc to find a URL in.
#
# ⭐ IT IS CUT OUT OF THE FILE BY ITS HEADING, NOT PARSED. The section is free-form - tables of
# URLs, bullets, paths in backticks - and the renderer already handles all of that. What this
# has to get right is only WHERE it ends: at the next level-2 heading, and never at one quoted
# inside a ``` fence, which is the same rule the entry parser follows.
# ==========================================================================================

_LINKS_HEAD_RE = re.compile(r"^##\s+§\s*1\s+LINKS\s+AND\s+DOCS\b(.*)$", re.I)
_H2_RE = re.compile(r"^##\s")


def links_section(lines):
    """{"heading", "suffix", "md", "line"} for the doc's §1, or None when it has none."""
    fence = False
    start = None
    for n, ln in enumerate(lines):
        if orchdoc.is_fence_line(ln):
            fence = not fence
            continue
        if not fence and _LINKS_HEAD_RE.match(ln):
            start = n
            break
    if start is None:
        return None
    body, fence = [], False
    for ln in lines[start + 1:]:
        if orchdoc.is_fence_line(ln):
            fence = not fence
        elif not fence and _H2_RE.match(ln):
            break
        body.append(ln)
    suffix = _LINKS_HEAD_RE.match(lines[start]).group(1).strip().lstrip("-–— ").strip()
    return {"heading": lines[start].lstrip("#").strip(), "suffix": suffix,
            "md": "\n".join(body).strip("\n"), "line": start + 1}


# The tag is an ordinary one-off §2.0 item, so the Do-today list, its Done button and its
# add/stop writes are the ones that already exist. Removing it stops that item (it moves to §99
# as Stopped).
TODAY_TAG_RE = re.compile(r"#([\w-]+/[A-Z]{1,3}\d{1,4}[a-z]?)\)")


def today_tag_text(slug, eid, title):
    """The text of the §2.0 item that tags `eid` on Do today."""
    t = re.sub(r"\s+", " ", re.sub(r"[\[\]]", "", title or "")).strip()
    return "Do [%s](http://127.0.0.1:8787/#%s/%s): %s" % (eid, slug, eid, t)


def _choice_of(body):
    import priority as _p
    return _p.choice_of(body)


def _class_of(body):
    import priority as _p
    return _p.class_of(body)


def entry_hash(i):
    """`/revs/<doc>` gives one per entry and `/entry/<doc>/<id>` gives that entry's, so the page
    can tell that an entry it just showed is the one a later `/revs` answer is about (see
    entryPatch).

    The page does not show it, and a save adds lines, so with `line` in the hash every entry
    below a save had a new hash."""
    import hashlib
    shown = dict((k, v) for k, v in i.items() if k != "line")
    return hashlib.sha1(json.dumps(shown, sort_keys=True, default=str)
                        .encode("utf-8")).hexdigest()[:12]


def build_doc_model(slug, path):
    """One doc, fully modelled. Pure - reads the file, writes nothing."""
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    entries, sections = orchdoc.parse_entries(lines)

    # ⛔ ASK THE GENERATOR, DO NOT RE-DERIVE. That distinction already exists and is already
    # correct; re-deriving it here would be a second copy of a judgement orchdoc makes well.
    try:
        block = orchdoc.build_plate_block(entries, lines)
    except Exception:
        block = []
    unreadable = any("CANNOT DETERMINE" in b for b in block)
    plate_rows = [b for b in block if b.startswith("| **[")]

    # Every LIVE entry's body, so the referrer count sees section 3 as well as the plate -
    # an unruled decision with three of the orchestrator's own work items behind it is
    # exactly the thing that should rise.
    _live_bodies = [x["body"] for x in entries if not x.get("archived")]
    # ⛔ ONCE PER DOCUMENT, NOT ONCE PER ENTRY. Computed per entry this walked every sibling
    # body again and the index build took 23.6s - which the server pays PER REQUEST, so
    # the human sat on "loading" at every click. Profiled, not guessed: 446,138 regex searches.
    try:
        import priority as _prio_mod
        _refcounts = _prio_mod.referrer_counts(_live_bodies)
        # See priority.reason.
        _blockers = _prio_mod.blocker_counts(_live_bodies)
    except Exception:
        _refcounts = _blockers = None
    # The page draws "Delete this instruction" from this flag alone and never re-derives the
    # rule in JavaScript.
    try:
        _ins = orchdoc.instruction_items(lines, entries)
    except Exception:
        _ins = {}
    seen = {}
    items = []
    for e in entries:
        eid = e["id"]
        # An id is unique per doc today (measured: zero duplicates across all eleven). If
        # that ever stops being true, BOTH entries must still render - a deduplicating
        # reader is exactly the silent drop this file exists to prevent - so the DOM id
        # gets a suffix and the entry stays.
        seen[eid] = seen.get(eid, 0) + 1
        dom = "entry-%s-%s" % (slug, eid)
        if seen[eid] > 1:
            dom += "--%d" % seen[eid]
        md, notes = entry_body_markdown(e)
        status = orchdoc.status_of(e["body"])
        # ⭐ PRIORITY, COMPUTED FROM THE SAME FUNCTION THE PLATE USES. A reader that
        # ranked entries by its own rule would be a second opinion about what the human should
        # look at first, and the two would disagree the moment either changed.
        try:
            import priority as _prio
            _rank = list(_prio.rank_key(eid, e["body"], _live_bodies, counts=_refcounts))
            _why = _prio.reason(eid, e["body"], _live_bodies, counts=_refcounts,
                                blockers=_blockers)
            _mine = bool(_prio.explicit_of(e["body"])[0]
                         and not _prio.explicit_of(e["body"])[1])
        except Exception:
            _rank, _why, _mine = [9, 0, 0, eid], "", False
        title = orchdoc.strip_decoration(e["title"])
        title = re.sub(r"^%s\s*[-:\u2013\u2014]\s*" % re.escape(eid), "", title).strip()
        items.append({
            "id": eid,
            "dom": dom,
            "doc": slug,
            "title": title,
            "raw_title": e["title"],
            "status": status,
            "sclass": status_class(status),
            "owner": orchdoc.owner_text(e["body"]) or "",
            "plate": on_humans_plate(e),
            "archived": bool(e.get("archived")),
            "section": e.get("section") or "(no section)",
            "kind": kind_of(eid),
            "line": e["line"],
            "md": md,
            "notes": notes,
            "prio_rank": _rank,
            "prio_why": _why,
            "prio_mine": _mine,
            # §2.0 Do today: the schedule and whether it is due, or None for any other entry.
            "today": (orchdoc.today_state(e["body"])
                      if eid.startswith("N") and not e.get("archived") else None),
            # A live instruction item, and how many INSTRUCTION notes it holds (see `_ins`).
            "ins_item": eid in _ins and not e.get("archived"),
            "ins_count": len(_ins.get(eid) or []),
            "prio_choice": _choice_of(e["body"]) or "",
            "prio_class": _class_of(e["body"]) or "",
            "today_tag": "",
        })
    _tagged = {}
    for it in items:
        if it.get("today"):
            for tid in TODAY_TAG_RE.findall(it["raw_title"] + "\n" + it["md"]):
                if tid.split("/", 1)[0] == slug:
                    _tagged.setdefault(tid.split("/", 1)[1], it["id"])
    for it in items:
        it["today_tag"] = _tagged.get(it["id"], "")

    return {
        "slug": slug,
        "path": str(path),
        "entries": items,
        "parsed_count": len(entries),
        "plate_count": sum(1 for i in items if i["plate"]),
        "plate_rows": len(plate_rows),
        "unreadable": unreadable,
        "sections": [s["title"] for s in sections],
        "links": links_section(lines),
    }


# ⛔ ONE MODEL PER DOCUMENT, REUSED UNTIL THAT DOCUMENT CHANGES.
#
# The human felt it as "almost all actions taking 5-6 seconds".
#
# ⭐ THE KEY IS WHAT ACTUALLY MAKES A MODEL STALE - the file's mtime and size. Nothing
# changed, reuse it; anything changed, re-parse that ONE file. Eleven sessions write these
# documents concurrently, so a cache keyed on anything weaker (a timer, a request count, a
# process lifetime) would serve a stale entry to whoever asked next, which is worse than slow.
#
# ⛔ AND NOT THE OTHER FIX THAT WAS ON THE TABLE. A hand-kept "directory" section inside
# each document would be an index someone maintains by hand - the one shape the workspace rule
# forbids - and it would speed up the note lookup only, while the cost is the whole-corpus
# model the rail is built from.
_MODEL_CACHE = {}


def build_doc_model_cached(slug, path):
    try:
        st = path.stat()
        # The date is in the key: a §2.0 routine falls due at midnight without the file moving.
        key = (str(path), st.st_mtime_ns, st.st_size, orchdoc._dt.date.today().isoformat())
    except OSError:
        return build_doc_model(slug, path)      # cannot stat it -> never serve a cached model
    hit = _MODEL_CACHE.get(str(path))
    if hit and hit[0] == key:
        return hit[1]
    model = build_doc_model(slug, path)
    _MODEL_CACHE[str(path)] = (key, model)
    return model


SEARCH_MAX_HITS = 200
SEARCH_MAX_RULES = 60
SEARCH_MAX_SCRIPTS = 60

# : id(doc model) -> (the model, [(entry text, the same as `TEXT.norm_hay`) per entry]). Handler
# threads share it, so a value is read : into a local once and a key is removed with `pop(k,
# None)`.
_HAY = {}


def _doc_hays(d):
    got = _HAY.get(id(d))
    if got is not None and got[0] is d:
        return got[1]
    hays = []
    for i in d["entries"]:
        notes = " ".join(orchdoc.note_display(n) for n in i.get("notes") or ())
        hay = re.sub(r"\s+", " ", "%s %s %s %s" % (i["id"], i["title"], i["md"], notes))
        hays.append((hay, TEXT.norm_hay(hay)))
    _HAY[id(d)] = (d, hays)
    return hays


def search_index(idx, term):
    """Hits across every doc: id, title, body and notes. -> [dict], best first.

    It only finds header text." The page only carries ids and titles for every doc, so the
    body search runs here, over the same models the page is rendered from.

    Ranked (`rank` in each hit): 0 the id is exactly the term, 1 the id contains it, 2 the
    title contains it, 3 only the text does. Within a rank: the whole phrase before
    all-the-words, then entries on the human's plate, then document order."""
    t = TEXT.norm_term(term)
    if len(t) < TEXT.SEARCH_MIN:
        return []
    aw = TEXT.and_words(term)
    found = []
    n = 0
    live = {id(d) for d in idx}
    for k in [k for k in list(_HAY) if k not in live]:
        _HAY.pop(k, None)
    for d in idx:
        for i, (hay, lc) in zip(d["entries"], _doc_hays(d)):
            n += 1
            got = TEXT.find_term(lc, t, aw)
            if got is None:
                continue
            at, phrase = got
            idl, tl = TEXT.norm_hay(i["id"]), TEXT.norm_hay(i["title"])
            if idl == t:
                rank = 0
            elif t in idl:
                rank = 1
            elif t in tl:
                rank = 2
            else:
                rank = 3
            ln = len(t) if phrase else len(aw[0])
            snip = TEXT.make_snippet(hay, at, ln).strip()
            found.append(((rank, 0 if phrase else 1, 0 if i["plate"] else 1, n),
                          {"d": d["slug"], "i": i["id"], "t": i["title"], "c": i["sclass"],
                           "p": 1 if i["plate"] else 0, "s": snip, "rank": rank,
                           "ex": bool(rank < 3 or phrase)}))
    found.sort(key=lambda x: x[0])
    return [h for _, h in found[:SEARCH_MAX_HITS]]


def search_rules(term):
    """Rule boxes (`#rule/<CODE>`) whose code, title or full text holds the term, best first."""
    t = TEXT.norm_term(term)
    if len(t) < TEXT.SEARCH_MIN:
        return []
    aw = TEXT.and_words(term)
    found = []
    for code, b in rule_codes().items():
        text = re.sub(r"\s+", " ", b.get("text") or "")
        hay = "%s %s %s" % (code, b["title"], text)
        got = TEXT.find_term(TEXT.norm_hay(hay), t, aw)
        if got is None:
            continue
        cl, tl = TEXT.norm_hay(code), TEXT.norm_hay(b["title"])
        rank = 0 if cl == t else 1 if t in cl else 2 if t in tl else 3
        ln = len(t) if got[1] else len(aw[0])
        found.append(((rank, 0 if got[1] else 1, code[:2], int(code[2:] or 0)),
                      {"k": "rule", "c": code, "t": b["title"], "rank": rank,
                       "ex": bool(rank < 3 or got[1]),
                       "s": TEXT.make_snippet(hay, got[0], ln).strip()}))
    found.sort(key=lambda x: x[0])
    return [h for _, h in found[:SEARCH_MAX_RULES]]


def search_all(reader, term):
    """`loading` is true while the script text is still being read for the first time; the page
    asks again a moment later."""
    if not HAS_SCRIPTS:
        return {"ok": True, "hits": search_index(reader.index(), term), "scripts": [],
                "scripts_total": 0, "rules": [], "loading": False}
    scripts, loading = reader.script_text.search(term, SEARCH_MAX_SCRIPTS)
    return {"ok": True, "hits": search_index(reader.index(), term), "scripts": scripts["hits"],
            "scripts_total": scripts["total"], "rules": search_rules(term), "loading": loading}


def build_index(workspace):
    """Every doc, modelled. This is what the cross-doc view is computed from."""
    return [build_doc_model_cached(slug, path) for slug, path in discover_docs(workspace)]


# ==========================================================================================
# MARKDOWN -> HTML
#
# ⛔ EVERYTHING IS ESCAPED FIRST, THEN A FIXED ALLOWLIST IS RESTORED. OrchDoc content is
# first-party, so this is not a trust boundary - but a doc that QUOTES a `<script>` tag (and
# several of them discuss HTML) must render that tag as text, not run it. Escaping wholesale
# and restoring a handful of exact, attribute-free constructs is the only version of this
# that cannot be got subtly wrong: there is no attribute passthrough to audit, because there
# are no attributes except a `color:` token matched against a strict pattern.
#
# The restored set is exactly what the docs use for structure a reader would lose otherwise:
# <details>/<summary> folds (archived entries live in them) and grey <span>s (settled
# sub-items). Anything else stays escaped and shows as text, which is the honest outcome.
# ==========================================================================================

_ALLOWED = [
    (re.compile(r"&lt;(/?)(details|summary)&gt;", re.I), r"<\1\2>"),
    (re.compile(r"&lt;br\s*/?&gt;", re.I), "<br>"),
    (re.compile(r"&lt;span style=&quot;color:\s*(#[0-9a-fA-F]{3,6}|[a-zA-Z]{3,12})\s*&quot;&gt;"),
     r'<span style="color:\1">'),
    (re.compile(r"&lt;/span&gt;", re.I), "</span>"),
]


def _restore_allowed(s):
    for rx, rep in _ALLOWED:
        s = rx.sub(rep, s)
    return s


_LINK_RE = re.compile(r"\[([^\]\n]*)\]\(([^)\s]+)(?:\s+&quot;[^&]*&quot;)?\)")
# ⛔ MUST BE APPLIED BEFORE `_LINK_RE`, WHICH WOULD OTHERWISE EAT THE `[alt](src)` HALF and leave
# a stray `!` in front of a link.
_IMG_RE = re.compile(r"!\[([^\]\n]*)\]\(([^)\s]+)(?:\s+&quot;[^&]*&quot;)?\)")
_BOLD_RE = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", re.S)
_ITAL_RE = re.compile(r"(?<![\w*])\*(?=[^\s*])([^*\n]+?)(?<=[^\s*])\*(?![\w*])")
_ITAL2_RE = re.compile(r"(?<![\w_])_(?=[^\s_])([^_\n]+?)(?<=[^\s_])_(?![\w_])")
# Struck text `~~...~~` is shown with a line through it. WHAT IS A STRIKE is not decided here: script_notes.strike_spans()
# is the one rule, the same as the app build's strip_strikes() (within one line, spaces inside the tildes allowed, a `~~`
def _strike_html(s):
    spans = strike_spans(s)
    if not spans:
        return s
    out, pos = [], 0
    for a, b in spans:
        out.append(s[pos:a])
        out.append("<del>%s</del>" % s[a + 2:b - 2])
        pos = b
    out.append(s[pos:])
    return "".join(out)
_CODE_RE = re.compile(r"(`+)(.+?)\1", re.S)
# The bold pass then matched across it and put `</strong>` INSIDE the href, where the browser
# reads it as attribute text. Trailing `.,;:!?` go back outside the link.
_AUTOLINK_RE = re.compile(r"(?<![\"'=(\w])(https?://[^\s<>\"')\]*]+)")


def _autolink(m):
    url, tail = m.group(1), ""
    while url and url[-1] in ".,;:!?":
        url, tail = url[:-1], url[-1] + tail
    return '<a href="%s" target="_blank" rel="noopener">%s</a>%s' % (
        html.escape(url, quote=True), url, tail)


# ==========================================================================================
# CROSS-DOC REFERENCES
#
# ⭐ THE DESTINATION ALREADY EXISTED. `route()` loads a doc and jumps to an entry for any
# `#<doc>/<ID>` hash, and `mark()` writes exactly that hash as the human scrolls. So a reference
# is made navigable by being an anchor with that href and nothing else - no new routing, no
# second notion of what an entry id is. Which ids exist comes from the SAME index the reader
# renders from, so the linker and the page cannot disagree about what is there.
#
# ⛔ FOUR OUTCOMES, FOUR APPEARANCES, AND TWO OF THEM ARE LINKS:
#   resolved, live                  -> a real <a>
#                                      that the entry is closed rather than live
#                                      entries exist; the parser is what cannot see them
#   no such entry                   -> marked as unresolved
#
# ⛔ AN ARCHIVED ENTRY IS A CORRECT DESTINATION, NOT A BROKEN ONE.
#
# ⭐ WHICH IDS ARE ARCHIVED COMES FROM orchdoc, NOT FROM A SECOND MATCHER HERE. `parse_entries`
# already reads the demoted id with `orchdoc.ARCHIVED_ID_RE` and sets `archived` - and it gets
# the hard parts right for free, which a regex written here would not: it looks at HEADING
# lines only, so prose that merely quotes the marker is not an entry; it tracks ``` fences and
# `ORCHDOC:OVERRIDE` attestation spans, so a doc that explains the form does not register the
# explanation as an instance. Measured over the eleven docs: 307 archived headings, 307
# indexed, and the four prose mentions correctly not among them.
# ==========================================================================================

_XREF_ID = r"[A-Z]{1,2}[0-9]{1,3}[a-z]?"

# The shapes measured in the corpus: 219 short, 14 long, and 3 written with the
# filename in backticks and the id outside them. All three, and nothing that merely
# resembles them.
#
# ⛔ AND MOST OF THEM ARE INSIDE BACKTICKS - which is why the hole branches below are not an edge
# case. A scanner that only saw bare prose would have linked 178 of 181 and reported itself
# working, while the `Depends:` lines - the references most worth clicking - stayed flat.
_XREF_SCAN = re.compile(
    # Consumed WHOLE and handed back untouched. A reference inside a markdown link or a URL
    # is already spoken for; rewriting it would nest an <a> in an <a> or corrupt an href.
    r"(?P<skip>\[[^\]\n]*\]\([^)\s]+(?:\s+&quot;[^&]*&quot;)?\)"
    r"|https?://[^\s<>)\]]+)"
    # \x00n\x00 is a stashed code span - the docs write the filename in backticks and the
    # id outside them, so the reference straddles the span and the hole has to be a
    # first-class alternative here rather than an afterthought.
    r"|(?P<docref>(?:ORCHESTRATOR-DECISIONS-(?P<ldoc>o[0-9]+)\.md|\x00(?P<hole>[0-9]+)\x00)"
    r"(?:\#|[`:,;]?[ \t]+)(?P<lid>" + _XREF_ID + r")(?![\w-]))"
    # A code span that is ENTIRELY one reference. Tried after <docref> so the two-part
    # form (filename in backticks, id outside) still wins where it applies.
    r"|(?P<holeref>\x00(?P<href>[0-9]+)\x00)"
    # The lookbehind is what stops a path segment or a longer token from being read as one.
    r"|(?P<short>(?<![\w/.:-])(?P<sdoc>o[0-9]+):(?P<sid>" + _XREF_ID + r")(?![\w-]))"
)

_XREF_HOLE_DOC = re.compile(r"^ORCHESTRATOR-DECISIONS-(o[0-9]+)\.md$")

# A code span is a reference only if it is one ENTIRELY - anchored both ends. `git log`
# is not a reference, and neither is a sentence that happens to contain one.
_XREF_WHOLE_CODE = re.compile(
    r"^(?:ORCHESTRATOR-DECISIONS-(?P<cdoc>o[0-9]+)\.md\#|(?P<cdoc2>o[0-9]+):)"
    r"(?P<cid>" + _XREF_ID + r")$")


class XrefTargets:
    """What a reference is allowed to resolve TO, read out of the reader's own index."""

    # ⭐ TWO OPTIONAL PASSENGERS, both off by default so an entry body renders exactly as it
    # always has. `files` turns workspace paths into links to the read-only viewer - used for
    # §1 and for the viewer's own markdown, never for entry bodies. `href_prefix` is "/" on the
    # viewer page, which is not the reader, so a reference there has to go back to it.
    files = None
    href_prefix = ""

    def __init__(self, index=None, record=False):
        self.ids = {}
        self.archived = {}
        self.blind = set()
        for d in index or ():
            self.ids[d["slug"]] = set(i["id"] for i in d["entries"])
            # orchdoc's own verdict, carried through build_doc_model - NOT re-derived here.
            # `.get` so a hand-built test fixture can name an id without restating every
            # field; build_doc_model always sets the key, so the real index never relies
            # on the default.
            self.archived[d["slug"]] = set(i["id"] for i in d["entries"]
                                           if i.get("archived"))
            if d["unreadable"]:
                self.blind.add(d["slug"])
        # Recording is opt-in: the server builds one of these per request and must not
        # accumulate a row per reference for the life of the process.
        self.record = [] if record else None

    def resolve(self, doc, eid):
        """'ok' | 'archived' | 'blind' | 'dead'.

        ⛔ AND 'archived' IS A KIND OF RESOLVED, NOT A KIND OF FAILURE. It is returned from
        inside the same branch as 'ok' so the two can never drift apart: any future change
        that decides an id exists decides it once, for both.
        """
        if eid in self.ids.get(doc, ()):
            return "archived" if eid in self.archived.get(doc, ()) else "ok"
        if doc in self.blind:
            return "blind"
        return "dead"


def _xref_markup(text, doc, eid, targets):
    """`text` is ALREADY-ESCAPED source - it is reused verbatim and never escaped again.

    `doc` and `eid` come out of the scanner's own character classes, so neither can carry a
    markup character; they are escaped anyway rather than relying on a future reader
    noticing that.
    """
    verdict = targets.resolve(doc, eid)
    if targets.record is not None:
        targets.record.append((verdict, doc, eid))
    d, e = html.escape(doc, quote=True), html.escape(eid, quote=True)
    pre = html.escape(getattr(targets, "href_prefix", "") or "", quote=True)
    if verdict == "ok":
        return ('<a class="xref" href="%s#%s/%s" title="Go to %s in %s">%s</a>'
                % (pre, d, e, e, d, text))
    # ⭐ SAME href, SAME route, SAME anchor - only the appearance differs. There is no second
    # navigation path for an archived entry, because there is no second kind of entry.
    if verdict == "archived":
        return ('<a class="xref xref-archived" href="%s#%s/%s" title="Go to %s in %s. This '
                "entry is ARCHIVED - it was closed and its id now sits in a "
                '_(was %s - ...)_ marker on the heading. The reference is correct.">%s</a>'
                % (pre, d, e, e, d, e, text))
    if verdict == "blind":
        return ('<span class="xref xref-blind" title="%s does not write its entries in a '
                "form the parser can read, so %s cannot be located here. This is NOT a "
                'claim that it is missing - open ORCHESTRATOR-DECISIONS-%s.md directly.">'
                "%s</span>" % (d, e, d, text))
    return ('<span class="xref xref-dead" title="No entry %s in %s - this reference does '
            'not resolve, so it is not a link.">%s</span>' % (e, d, text))


def _hole(holes, n):
    """The stashed code span `n` refers to, or None if there is no such span."""
    try:
        return holes[int(n)]
    except (TypeError, ValueError, IndexError):
        return None


def _xref_sub(s, targets, holes, consumed=None):
    """Rewrite every resolvable reference in ONE left-to-right pass.

    One pass, with the constructs to leave alone as alternatives of the same regex, so a
    reference sitting inside a link or a URL is consumed as part of that construct and can
    never be matched separately.

    `consumed`, when given, collects the index of every code span that ended up INSIDE a
    reference link - so the path pass (§1 and the file viewer) never wraps a second `<a>`
    around a span that is already inside one.
    """
    if targets is None:
        return s

    def _took(n):
        if consumed is not None:
            try:
                consumed.add(int(n))
            except (TypeError, ValueError):
                pass

    def one(m):
        if m.group("skip") is not None:
            return m.group(0)
        if m.group("short") is not None:
            return _xref_markup(m.group(0), m.group("sdoc"), m.group("sid"), targets)
        if m.group("holeref") is not None:
            inner = _hole(holes, m.group("href"))
            cm = _XREF_WHOLE_CODE.match(inner or "")
            if not cm:
                return m.group(0)
            # The <code> stays intact and the LINK goes around it. Nothing is injected
            # into the span, so a code span still renders exactly its own text.
            _took(m.group("href"))
            return _xref_markup(m.group(0), cm.group("cdoc") or cm.group("cdoc2"),
                                cm.group("cid"), targets)
        doc = m.group("ldoc")
        if doc is None:
            hm = _XREF_HOLE_DOC.match(_hole(holes, m.group("hole")) or "")
            if not hm:
                return m.group(0)
            doc = hm.group(1)
            _took(m.group("hole"))
        return _xref_markup(m.group(0), doc, m.group("lid"), targets)

    return _XREF_SCAN.sub(one, s)


# The data is rule_codes_index.load(), the same parse that writes RULE-CODES-INDEX.md, so a code
# links only if the generator found its box. A code inside a markdown link, a URL or an HTML tag
# the xref pass already wrote is consumed whole and left alone.
#
# A code after `/` or `-` links only when the token before that separator is itself a standalone
# code, so both sides of a range or pair link (`RC1-RC7`, `AR7/AR9`) and a path segment does not
# (`foo/AB9`, `rule-AB9.jpg`).
_RULE_LEAD = r"(?<![\w/.:#\\-])[A-Z]{2}"
_RULE_SCAN = re.compile(
    r"(?P<skip>\[[^\]\n]*\]\([^)\s]+(?:\s+&quot;[^&]*&quot;)?\)"
    r"|https?://[^\s<>)\]]+|<a\b[^>]*>.*?</a>|<[^>]+>)"
    r"|(?:(?<![\w/.:#\\-])"
    r"|(?<=" + _RULE_LEAD + r"[0-9][/-])"
    r"|(?<=" + _RULE_LEAD + r"[0-9]{2}[/-])"
    r"|(?<=" + _RULE_LEAD + r"[0-9]{3}[/-]))"
    r"(?P<code>[A-Z]{2}[0-9]{1,3})(?!\w)(?!\.[A-Za-z])")


_RULE_HELD = [None, 0.0]
#: Seconds a parse is reused before the checklists' mtimes are looked at again. `_inline` runs
#: once per paragraph, thousands of times per view, so it must not stat twelve files each time.
_RULE_TTL = 5.0


def rule_codes():
    """{code: box} from the generator, or {} when it cannot be read."""
    if RULECODES is None:
        return {}
    now = time.monotonic()
    if _RULE_HELD[0] is not None and now - _RULE_HELD[1] < _RULE_TTL:
        return _RULE_HELD[0]
    try:
        codes = RULECODES.load()["codes"]
    except Exception:
        codes = {}
    _RULE_HELD[0], _RULE_HELD[1] = codes, now
    return codes


def _rule_sub(s, keep, prefix=""):
    codes = rule_codes()
    if not codes:
        return s

    def one(m):
        c = m.group("code")
        if c is None or c not in codes:
            return m.group(0)
        b = codes[c]
        return keep('<a class="xref rulelink" href="%s#rule/%s" title="%s: %s (%s)">%s</a>'
                    % (html.escape(prefix, quote=True), c, c,
                       html.escape(b["title"], quote=True), html.escape(b["file"], quote=True),
                       c))
    return _RULE_SCAN.sub(one, s)


def render_rules_view(xref=None):
    """`#rule` and `#rule/<CODE>`: every rule box, full text, one article each.

    The client's route() already handles `#<view>/<id>`: it loads /doc/rule and jumps to
    `entry-rule-<CODE>`, so no new client route is needed. Articles and rail buttons carry
    `data-rule`, which the filter treats like a script row: only the search term applies."""
    codes = rule_codes()
    if not codes:
        return ('<div class="count">no rule codes</div>',
                '<div class="empty">The rule boxes could not be read: '
                "<code>rule_codes_index.py</code> is missing or found no box.</div>")
    order = sorted(codes, key=lambda c: (c[:2], int(c[2:])))
    try:
        pnames = RULECODES.load().get("names", {})
    except Exception:
        pnames = {}
    nav, body = ['<div class="count">%d rule codes</div>' % len(order)], []
    body.append('<p class="hint">%d script-rule codes, generated from the checklist boxes by '
                "<code>.shared/scripts/rule_codes_index.py</code> (the same data as "
                "<code>RULE-CODES-INDEX.md</code>). Each box is shown in full.</p>" % len(order))
    cur = None
    for c in order:
        b = codes[c]
        if c[:2] != cur:
            cur = c[:2]
            label = RULECODES.prefix_label(cur, pnames)
            nav.append('<div class="sec rulesec">%s</div>' % _esc(label))
            body.append('<h2 class="grouphead">%s</h2>' % _esc(label))
        dom = "entry-rule-%s" % c
        hay = _esc((c + " " + b["title"]).lower())
        nav.append('<button type="button" class="ebtn" data-goto="%s" data-rule="%s" '
                   'data-hay="%s"><span class="dot"></span><span class="eid">%s</span>'
                   '<span class="etitle">%s</span></button>'
                   % (dom, c, hay, c, _esc(b["title"])))
        tags = ['<span class="tag">%s</span>' % _esc(b["file"])]
        if b["tag"]:
            tags.append('<span class="tag">%s</span>' % _esc(b["tag"]))
        if not b["checkbox"]:
            tags.append('<span class="tag">no checkbox</span>')
        body.append('<article class="entry rulebox" id="%s" data-doc="rule" data-id="%s" '
                    'data-rule="%s" data-hay="%s">'
                    '<h2 class="et"><span class="eid">%s</span> <span>%s</span></h2>'
                    '<div class="meta">%s</div><div class="md">%s</div></article>'
                    % (dom, c, c, hay, c, _esc(b["title"]), "".join(tags),
                       md_to_html(b["text"], xref)))
    return "".join(nav), "".join(body)


def audit_xrefs(index):
    """Every reference in every entry body, and what became of it.

    Returns (counts, unresolved): counts keyed 'ok'/'archived'/'blind'/'dead', and unresolved
    as (source doc, source id, target doc, target id, verdict) rows for the ones that did not
    become links. The linked count is what says the transform is ALIVE - zero links across a
    corpus full of references is exactly the shape a dead transform makes, and it would pass
    every check that only asks whether anything broke.

    ⛔ 'archived' IS COUNTED SEPARATELY FOR THE SAME REASON. If the archived branch stops
    firing, every one of those references silently falls back to 'dead' and gets a cross -
    which is what this change exists to stop, and it would otherwise look like a quiet
    corpus rather than a dead branch. A zero here is a FAILURE, not a clean run.
    """
    counts = {"ok": 0, "archived": 0, "blind": 0, "dead": 0}
    unresolved = []
    targets = XrefTargets(index, record=True)
    for d in index:
        for it in d["entries"]:
            del targets.record[:]
            md_to_html(it["md"], targets)
            for verdict, doc, eid in targets.record:
                counts[verdict] += 1
                # 'archived' resolved. Listing it as unresolved would put 100+ correct
                # citations on the list of things somebody has to go and fix.
                if verdict not in ("ok", "archived"):
                    unresolved.append((d["slug"], it["id"], doc, eid, verdict))
    return counts, unresolved


def archived_coverage(workspace):
    """Per doc: archived headings in the FILE vs archived ids the reader can actually reach.

    ⛔ THE COVERAGE TEST THIS FILE ALREADY HAD CANNOT SEE THIS. It asserts that the reader
    renders every entry `parse_entries` returns - so when the PARSER loses an entry, the
    reader faithfully renders the smaller set and every check passes.

    ⭐ SO THIS COUNTS THE FILE, NOT THE PARSE. It is deliberately a different oracle: it
    scans raw lines for a heading carrying orchdoc's own archived marker and compares that
    against what `parse_entries` yielded. Agreement is the normal state (307 of 307 across
    the eleven docs); a shortfall names a doc where entries exist and are unreachable.

    This is NOT a second parser and must never become one - it decides nothing about what an
    entry is. It only asks whether the two numbers match, and says which doc they do not.
    """
    rows = []
    for slug, path in discover_docs(workspace):
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
        # Heading lines only. Prose that quotes the marker, and the `ORCHDOC:OVERRIDE`
        # comments that explain it, are not headings - so neither is counted, which is the
        # same distinction parse_entries makes and the reason both land on 307.
        heads = [n for n, ln in enumerate(lines, start=1)
                 if orchdoc.ARCHIVED_HEADING_RE.match(ln)
                 and orchdoc.ARCHIVED_ID_RE.search(ln)]
        try:
            entries, _ = orchdoc.parse_entries(lines)
        except Exception:
            # Every heading is then unreachable, which `unreachable_archived` reports by
            # construction with an empty entry list - the honest answer when the parser
            # could not run at all.
            entries = []
        # ⭐ ONE DEFINITION, READ BY BOTH SIDES. This loop used to live here AND in
        # `orchdoc.check_doc`; the same suffix already has two notes in orchdoc.py about a
        # writer and a reader drifting apart on it. The checker and this oracle now cannot
        # disagree about what an unreachable archived heading is, because there is one.
        missing = orchdoc.unreachable_archived(lines, entries)
        rows.append({"slug": slug, "in_file": len(heads),
                     "indexed": len(heads) - len(missing), "missing_lines": missing})
    return rows


def _safe_href(url):
    """Only http(s), mailto and in-page anchors become links. Everything else renders as
    text - `javascript:` is the reason, and a scheme allowlist is the only reliable form."""
    u = html.unescape(url).strip()
    if u.startswith("#") or re.match(r"^(https?://|mailto:)", u, re.I):
        return html.escape(u, quote=True)
    return None


# Where a workspace-relative image path is resolved from. Set by `Reader.__init__` and by
# `main`; None in any caller that has no workspace, and then no image renders at all - plain
# text, never a guess, the same direction `xref=None` takes.
ASSET_ROOT = None

# ⛔ NOT `.svg`. An SVG is a document that can carry script, and these paths come out of a
# file eleven sessions write. Raster only.
IMG_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".gif": "image/gif", ".webp": "image/webp"}


def resolve_asset(rel, root=None):
    """A workspace-relative image path -> (absolute Path, posix relpath, content-type), or a
    (None, None, reason) triple saying why not.

    ⛔ THE INVERSE ALLOWLIST OF `_safe_href`, AND DELIBERATELY SO. That one takes only
    ABSOLUTE http(s) urls; this one takes only RELATIVE paths inside the workspace. A remote
    `<img src>` would be a background request out of a document eleven sessions write, from a
    page whose stated property is that it makes none - and `data:` would put the bytes in the
    markdown. Neither is a picture of a mockup, so neither is allowed to be one.

    ⛔ RESOLVE FIRST, THEN CHECK CONTAINMENT. `.resolve()` follows symlinks, so a link
    inside the workspace pointing outside it lands outside and fails the check. Checking the
    unresolved path would pass it.
    """
    root = root if root is not None else ASSET_ROOT
    if root is None:
        return None, None, "the reader has no workspace to resolve it against"
    u = html.unescape(rel).strip().replace("\\", "/")
    if not u:
        return None, None, "empty path"
    # a scheme, a UNC path, a drive letter, or a server-absolute path: all refused
    if u.startswith(("/", "//", "#")) or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", u):
        return None, None, "only a path relative to the workspace is allowed, not %r" % u
    ext = os.path.splitext(u)[1].lower()
    if ext not in IMG_TYPES:
        return None, None, "%s is not one of %s" % (ext or "no extension",
                                                    ", ".join(sorted(IMG_TYPES)))
    root = Path(root).resolve()
    try:
        p = (root / u).resolve()
    except (OSError, ValueError) as ex:
        return None, None, "unreadable path (%s)" % type(ex).__name__
    if not p.is_relative_to(root):
        return None, None, "resolves outside the workspace"
    if not p.is_file():
        return None, None, "no file at that path"
    return p, p.relative_to(root).as_posix(), IMG_TYPES[ext]


# Thread-local, so a doc page rendered at the same moment on : another thread records nothing
# here. The home cache re-checks these: whether a picture : exists is the one thing an entry's
# HTML depends on that is not in the OrchDoc file.
_IMG_SEEN = threading.local()


def _safe_img_src(url):
    """(url, None) for an image that exists, or (None, the reason it does not render)."""
    p, relp, ctype_or_why = resolve_asset(url)
    rec = getattr(_IMG_SEEN, "rec", None)
    if rec is not None:
        rec[url] = relp if p is not None else None
    if p is None:
        return None, ctype_or_why
    return "/img/" + urllib.parse.quote(relp), None


# A note is one line of text, so the picture is saved as a file and the text carries `[image:
# <absolute path>]`. The ABSOLUTE path is deliberate: the orchestrator reading the note may be
# in a worktree where a workspace-relative path does not exist, and the file is on this same
# computer either way. The page turns the marker back into the picture.
PASTE_DIR = "orchdoc-images"
PASTE_MAX_BYTES = 8 * 1024 * 1024
PASTE_MAGIC = ((b"\x89PNG\r\n\x1a\n", ".png"), (b"\xff\xd8\xff", ".jpg"),
               (b"GIF87a", ".gif"), (b"GIF89a", ".gif"))
IMAGE_MARK_RE = re.compile(r"\[image:\s*([^\]\s][^\]]*?)\s*\]")


def paste_ext(raw):
    """The extension the BYTES say this image is, or None. Never the name the page sent."""
    for magic, ext in PASTE_MAGIC:
        if raw.startswith(magic):
            return ext
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return ".webp"
    return None


def _note_body_html(text, xref=None):
    """A note's text as HTML: inline markdown, with each image marker as the picture.
    The markers are lifted out BEFORE the markdown runs, so a path can never be read as
    emphasis or a link."""
    marks = []

    def lift(m):
        marks.append(m.group(0))
        return "\x00IMG%d\x00" % (len(marks) - 1)
    # `_inline` would restore those tags from their escaped form, so every `&lt;` is held back
    # from it and put back after.
    esc = html.escape(text or "").replace("&lt;", "\x01LT\x01")
    out = _inline(IMAGE_MARK_RE.sub(lift, esc), xref).replace("\x01LT\x01", "&lt;")
    return re.sub(r"\x00IMG(\d+)\x00",
                  lambda m: _image_marks_html(marks[int(m.group(1))]), out)


def _image_marks_html(escaped_text, root=None):
    """Replace each `[image: path]` in ALREADY-ESCAPED note text with the picture itself.
    A path that is not an image inside the workspace stays as the text it was."""
    root = root if root is not None else ASSET_ROOT

    def one(m):
        raw = html.unescape(m.group(1)).replace("\\", "/")
        rel = raw
        if root is not None:
            r = Path(root).resolve().as_posix().rstrip("/") + "/"
            if raw.lower().startswith(r.lower()):
                rel = raw[len(r):]
        src, _why = _safe_img_src(rel)
        if not src:
            return m.group(0)
        # data-mark: the marker exactly as written, so "Remove image" can take out this one.
        return ('<a class="note-img" href="%s" target="_blank" rel="noopener" data-mark="%s">'
                '<img src="%s" alt="pasted image" loading="lazy"></a>'
                % (src, _esc(html.unescape(m.group(0))), src))
    return IMAGE_MARK_RE.sub(one, escaped_text)


# ==========================================================================================
# THE READ-ONLY FILE VIEWER - what a workspace path in §1 opens
#
# ⛔ CONFINED EXACTLY THE WAY `/img/` IS, AND BY THE SAME SHAPE OF FUNCTION. One resolver,
# called by the RENDERER to decide whether a path becomes a link and called again, from
# scratch, by the ROUTE on the path the browser sends back. The route never trusts that the
# path came from a link this server made.
#
# ⛔ TEXT ONLY, BY EXTENSION, AND NEVER A SECRET. The workspace holds `<your-secrets-folder>/`, and
# 127.0.0.1 is not a boundary against a page the human happens to have open (a cross-origin read is
# refused by the browser, but a DNS-rebinding page is same-origin by the time it asks). So a
# `secrets` folder and anything NAMED like a credential is refused whatever its extension, and
# the route also refuses a request whose Host header is not this server.
#
# ⛔ AND A FILE WHOSE CONTENT HOLDS A CREDENTIAL IS REFUSED, WHATEVER IT IS NAMED. A name rule
# cannot see the seed scripts, so the route scans the text it read with `secret_patterns` before
# rendering it, and the refusal names the KIND of credential, never the value.
#
# ⛔ A GET, AND IT MUTATES NOTHING. It reads one file and renders it.
# ==========================================================================================

VIEW_TYPES = {".md": "markdown", ".markdown": "markdown",
              ".txt": "text", ".py": "text", ".json": "text", ".jsonl": "text",
              ".sh": "text", ".ps1": "text", ".bat": "text", ".cmd": "text",
              ".js": "text", ".mjs": "text", ".cjs": "text", ".ts": "text", ".tsx": "text",
              ".css": "text", ".yml": "text", ".yaml": "text", ".toml": "text",
              ".ini": "text", ".cfg": "text", ".csv": "text", ".tsv": "text", ".log": "text"}
VIEW_MAX_BYTES = 3_000_000
_VIEW_DENY_DIRS = {".git", "secrets", ".secrets"}
# `^\.env` covers `.env` and every `.env.*`.
_VIEW_DENY_NAME = re.compile(
    r"(?i)(^\.env|secret|credential|password|passwd|private[-_]?key|api[-_]?key|"
    r"(?:^|[-_.])(?:oauth[-_]?)?token(?:[-_.]|$))")
# Claude Code's settings files, at ANY depth: every worktree under `.claude/worktrees/` carries
# its own `.claude/settings.local.json`, with the same webhook URLs in it.
_VIEW_DENY_CLAUDE_SETTINGS = re.compile(r"(?i)^settings.*\.json$")


def resolve_viewable(rel, root=None):
    """A workspace-relative text file -> (absolute Path, posix relpath, "markdown"|"text"), or
    (None, None, reason). ⛔ THE SAME ORDER AS `resolve_asset`: refuse the shapes, resolve, THEN
    check containment - so a symlink pointing out of the workspace fails here."""
    root = root if root is not None else ASSET_ROOT
    if root is None:
        return None, None, "the reader has no workspace to resolve it against"
    u = (rel or "").strip().replace("\\", "/")
    if not u:
        return None, None, "empty path"
    if u.startswith(("/", "//", "#")) or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", u):
        return None, None, "only a path relative to the workspace is allowed, not %r" % u
    ext = os.path.splitext(u)[1].lower()
    if ext not in VIEW_TYPES:
        return None, None, "%s is not a text type this viewer opens (%s)" % (
            ext or "no extension", " ".join(sorted(VIEW_TYPES)))
    root = Path(root).resolve()
    try:
        p = (root / u).resolve()
    except (OSError, ValueError) as ex:
        return None, None, "unreadable path (%s)" % type(ex).__name__
    if not p.is_relative_to(root) or p == root:
        return None, None, "resolves outside the workspace"
    relp = p.relative_to(root)
    folders = [part.lower() for part in relp.parts[:-1]]
    if any(part in _VIEW_DENY_DIRS for part in folders) \
            or _VIEW_DENY_NAME.search(relp.name) \
            or (".claude" in folders and _VIEW_DENY_CLAUDE_SETTINGS.match(relp.name)):
        return None, None, "refused: that path is, or is named like, a secret"
    if not p.is_file():
        return None, None, "no file at that path"
    return p, relp.as_posix(), VIEW_TYPES[ext]


def file_href(relposix, frag=""):
    return "/file/" + urllib.parse.quote(relposix) + (
        "#" + urllib.parse.quote(frag) if frag else "")


# Something written as a path: a drive letter, `~/`, `./`, a UNC share, or a word followed by
# a slash. Deliberately NOT anything with a space before its first slash - `git push origin
# origin/main` is a command, not a path.
_PATHISH_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|~[\\/]|\\\\|\.{1,3}[\\/]|[\w.@-]+[\\/])"
                         r"[^\n`<>|\"*?]*$")


def looks_like_path(s):
    s = (s or "").strip()
    return (0 < len(s) <= 400 and "://" not in s and ("/" in s or "\\" in s)
            and bool(_PATHISH_RE.match(s)))


class FileLinks:
    """Where a path in §1 (or in a viewed file) points. ⭐ THE ANSWER IS `resolve_viewable`'S -
    the same function the `/file/` route runs again - so a path becomes a link only when the
    route would serve it, and never otherwise."""

    def __init__(self, root, base_rel="", doc_slugs=None, href_prefix=""):
        self.root = Path(root).resolve()
        self.base_rel = (base_rel or "").strip("/")
        # Casefolded: an absolute path comes back through normcase, which lowercases it on
        # Windows.
        self.doc_slugs = {k.casefold(): v for k, v in (doc_slugs or {}).items()}
        self.href_prefix = href_prefix

    def target(self, raw, is_link=False):
        """("doc", slug, frag) | ("file", relposix, frag) | ("copy", why) | None."""
        u = html.unescape(raw or "").strip()
        if not u or "\n" in u or len(u) > 400:
            return None
        frag = ""
        absolute = bool(re.match(r"^[A-Za-z]:[\\/]", u)) or u.startswith(("\\\\", "~"))
        if is_link and "#" in u and not absolute:
            u, frag = u.split("#", 1)
        if "%" in u:
            u = urllib.parse.unquote(u)
        if not u:
            return None
        if absolute:
            # ⛔ NEVER RESOLVED BEFORE THE PREFIX TEST. `.resolve()` on a UNC path or on a
            # drive that is not mounted goes to the network or the disk, on a page render.
            # A string comparison decides "outside"; only an inside path is ever resolved.
            if u.startswith("\\\\"):
                return ("copy", "a network path - not served here; copy the path")
            norm = os.path.normcase(os.path.normpath(os.path.expanduser(u)))
            rootn = os.path.normcase(os.path.normpath(str(self.root)))
            if not norm.startswith(rootn + os.sep):
                return ("copy", "outside the workspace - not served here; copy the path")
            rels = [os.path.relpath(norm, rootn).replace(os.sep, "/")]
        elif u.startswith("/"):
            return ("copy", "an absolute path - not served here")
        else:
            u = u.replace("\\", "/")
            here = posixpath.normpath(posixpath.join(self.base_rel, u)) if self.base_rel \
                else posixpath.normpath(u)
            top = posixpath.normpath(u)
            # A markdown link resolves against the file it is written in; a bare path in
            # backticks is written workspace-relative in these docs. Both are tried.
            rels = [here, top] if is_link else [top, here]
        for rel in dict.fromkeys(rels):
            if rel.casefold() in self.doc_slugs:
                return ("doc", self.doc_slugs[rel.casefold()], frag)
            p, relp, _kind = resolve_viewable(rel, self.root)
            if p is not None:
                return ("file", relp, frag)
        for rel in dict.fromkeys(rels):
            try:
                q = (self.root / rel).resolve()
            except (OSError, ValueError):
                continue
            if q.is_relative_to(self.root) and q.is_dir():
                return ("copy", "a folder - not served here; copy the path")
            if q.is_relative_to(self.root) and q.exists():
                _p, _r, why = resolve_viewable(rel, self.root)
                return ("copy", why)
        # ⭐ AN ABSOLUTE PATH IS A PATH WHETHER OR NOT IT IS HERE, so it stays copyable. A
        # markdown LINK target is kept, because dropping it would hide the one thing the line
        # was pointing at.
        if absolute or is_link:
            return ("copy", "not found in this workspace")
        return None

    def doc_href(self, slug, frag):
        f = frag if re.match(r"^[A-Z]{1,2}[0-9]{1,3}[a-z]?$", frag or "") else ""
        return "%s#%s%s" % (self.href_prefix, slug, "/" + f if f else "")


def _copyable(inner_html, why):
    """A path this page will not serve, shown so it can be COPIED. The button copies the
    code element's own text, so what lands on the clipboard is exactly what is on screen."""
    return ('<span class="cpw"><code title="%s">%s</code><button type="button" class="cpbtn" '
            'data-copy title="Copy this path">copy</button></span>'
            % (html.escape(why or "", quote=True), inner_html))


# It reuses the path buttons' `[data-copy]` handler (COPY_JS), which copies the <code>'s own
# text: that is the block's text exactly, with no line break added or lost, because `md_to_html`
# escapes the fenced lines joined by "\n" and nothing else. Applied to the OrchDoc's own renders
# (an entry, the §1 links, a markdown file page), never to the script editor's (`script=True`),
# whose blocks carry line numbers for the editor. The match cannot end early: inside a block
# every `<` is escaped, so the first `</code></pre>` after an opening `<pre>` is that block's
# own end.
_FENCED_BLOCK_RE = re.compile(r"<pre(?: [^>]*)?><code>.*?</code></pre>", re.S)


def copy_fenced_blocks(html_text):
    """`html_text` with every rendered fenced block wrapped so it carries a Copy button."""
    if not html_text or "<pre" not in html_text:
        return html_text
    return _FENCED_BLOCK_RE.sub(
        lambda m: ('<div class="cpw fcopy"><button type="button" class="cpbtn" data-copy '
                   'title="Copy this block">copy</button>%s</div>' % m.group(0)),
        html_text)


def _links_ctx(xref=None, base_rel="", href_prefix=""):
    """A render context that ALSO turns workspace paths into viewer links. None when the reader
    has no workspace - then every path stays plain text, the same direction `xref=None` takes."""
    if ASSET_ROOT is None:
        return xref
    slugs = {}
    if isinstance(xref, XrefTargets):
        slugs = {("ORCHESTRATOR-DECISIONS-%s.md" % s): s for s in xref.ids}
    fl = FileLinks(ASSET_ROOT, base_rel, slugs, href_prefix)
    if isinstance(xref, XrefTargets):
        ctx = copy.copy(xref)
        ctx.files = fl
        ctx.href_prefix = href_prefix
        return ctx
    return fl


def _inline(s, xref=None, dd=False):
    """Inline markdown on ALREADY-ESCAPED text. Code spans are lifted out first so their
    contents never get bold/italic applied inside them.

    ⛔ THE REFERENCE PASS RUNS HERE - AFTER THE CODE SPANS, BEFORE `_LINK_RE`.
    After the code spans, because one reference form is written half inside a span.
    Before `_LINK_RE`, because running afterwards would let it rewrite the inside of
    an href it had just built. `xref=None` means no reference pass at all, which is
    what a caller with no index to resolve against gets - plain text, never a guess.
    """
    holes = []
    # ⛔ FINISHED HTML A RULE EMITS, HELD OUT OF REACH OF EVERY LATER RULE. The code-span
    # stash above exists for the same reason and only covers its own case: whatever a rule
    # writes into the string is still text that the next rule reads. The image fallback hit
    # this - it re-emits the raw markdown so the unresolved PATH stays visible, and `_LINK_RE`
    # then matched the `[alt](src)` inside it and replaced the path with the alt text, which
    # deleted the one thing the fallback is for.
    spans = []

    def keep(frag):
        spans.append(frag)
        return "\x01%d\x01" % (len(spans) - 1)

    def stash(m):
        holes.append(m.group(2).strip())
        return "\x00%d\x00" % (len(holes) - 1)

    # ⭐ `xref` MAY CARRY A PATH RESOLVER (`.files`), OR BE ONE. Entry bodies never get one, so
    # everything below the `files` checks is a no-op for them and they render exactly as before.
    files = xref.files if isinstance(xref, XrefTargets) else \
        (xref if isinstance(xref, FileLinks) else None)
    xt = xref if isinstance(xref, XrefTargets) else None
    consumed = set()

    s = _CODE_RE.sub(stash, s)
    s = _xref_sub(s, xt, holes, consumed)
    # Bare rule codes (AB9) become links to #rule/AB9. Code spans are already stashed, so a code
    # inside backticks is never linked; the finished link is held out of reach with `keep`.
    if xt is not None:
        s = _rule_sub(s, keep, getattr(xt, "href_prefix", "") or "")
    # Script dropdowns are FOUND here, once code spans are out of reach, and painted last.
    if dd:
        s = _dd_find(s)

    def path_link(t, text_html):
        """Markup for a resolved path. The href is stashed with `keep`, so no later inline
        rule can read an underscore in `/file/a_b_c.py` as the start of italics."""
        if t[0] == "doc":
            return ('<a class="flink fdoc" href="%s" title="Open %s in the reader">%s</a>'
                    % (keep(html.escape(files.doc_href(t[1], t[2]), quote=True)),
                       html.escape(t[1]), text_html))
        return ('<a class="flink" href="%s" target="_blank" rel="noopener" '
                'title="Open %s (read-only)">%s</a>'
                % (keep(html.escape(file_href(t[1], t[2]), quote=True)),
                   keep(html.escape(t[1], quote=True)), text_html))

    def link(m):
        href = _safe_href(m.group(2))
        if not href:
            if files is not None:
                t = files.target(m.group(2), is_link=True)
                if t and t[0] in ("doc", "file"):
                    return path_link(t, m.group(1))
                if t:
                    # ⛔ THE PATH STAYS ON SCREEN. This used to drop it and keep only the link
                    # text, so `[Pre-migration copies](C:\...)` showed the words and hid the one
                    # thing anybody needed from the line.
                    return "%s %s" % (m.group(1), keep(_copyable(m.group(2), t[1])))
            return "%s" % m.group(1)
        return '<a href="%s" target="_blank" rel="noopener">%s</a>' % (href, m.group(1))

    def image(m):
        src, why = _safe_img_src(m.group(2))
        if not src:
            # ⛔ SAY THE PATH AND SAY WHY. Same direction as the renderer's fall-through for
            # anything it does not recognise: unrendered markdown is readable, mangled markdown
            # is not.
            return keep('<span class="noimg">%s<span class="noimg-why">%s</span>'
                        '</span>'
                        % (m.group(0), html.escape(why or "not shown")))
        alt = m.group(1) or "screenshot"
        return keep('<a class="shot" href="%s" target="_blank" rel="noopener" title="%s">'
                    '<img src="%s" alt="%s" loading="lazy"></a>'
                    % (src, alt, src, alt))

    # ⛔ BEFORE THE LINK PASS. `_LINK_RE` matches the `[alt](src)` inside `![alt](src)`.
    s = _IMG_RE.sub(image, s)
    s = _LINK_RE.sub(link, s)
    s = _AUTOLINK_RE.sub(_autolink, s)
    s = _BOLD_RE.sub(r"<strong>\1</strong>", s)
    s = _strike_html(s)
    s = _ITAL_RE.sub(r"<em>\1</em>", s)
    s = _ITAL2_RE.sub(r"<em>\1</em>", s)
    s = _restore_allowed(s)
    for i, c in enumerate(holes):
        code = "<code>%s</code>" % c
        # ⛔ NOT A SPAN THE REFERENCE PASS ALREADY WRAPPED - that would nest an <a> in an <a>.
        if files is not None and i not in consumed and looks_like_path(html.unescape(c)):
            t = files.target(c)
            if t and t[0] in ("doc", "file"):
                code = path_link(t, code)
            elif t:
                code = _copyable(c, t[1])
        s = s.replace("\x00%d\x00" % i, code)
    # Spans can nest (a path link's stashed href lives inside a stashed fragment), so restore
    # until none are left - bounded by the number of spans, since each pass restores at least one.
    for _ in range(len(spans) + 1):
        if "\x01" not in s:
            break
        for i, frag in enumerate(spans):
            s = s.replace("\x01%d\x01" % i, frag)
    return _dd_paint(s) if dd else s


_HEAD_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_HR_RE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_ULI_RE = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_OLI_RE = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
_BOX_RE = re.compile(r"^\[([ xX])\]\s*(.*)$")
_QUOTE_RE = re.compile(r"^\s*&gt;\s?(.*)$")
# An HTML comment is markup, not prose - GitHub and Obsidian both hide it. These docs use it
# for orchdoc's generated-region markers and for provenance notes, so DROPPING it would lose
# real information. It is dimmed instead: still there, visibly not body text.
_CMT_RE = re.compile(r"^\s*&lt;!--(.*?)--&gt;\s*$", re.S)


def md_to_html(text, xref=None, script=False):
    """A focused markdown renderer for what the OrchDocs actually contain.

    Not a general implementation - deliberately. It covers fences, tables, headings, quotes,
    nested lists, task checkboxes, and the inline set, because that is what these documents
    use. Anything it does not recognise falls through as an escaped paragraph, which is the
    safe direction: unrendered markdown is readable, mangled markdown is not.

    ⭐ `script=True` IS THE SCRIPT EDITOR'S RENDER, AND IT ADDS EXACTLY THREE THINGS. Every
    block carries `data-src-line` - the 1-based source line it starts on - so the editor can
    keep the preview on the line being edited. Every `[[a | b]]` dropdown group has its markers
    and options wrapped in spans so they can be coloured. And every `{{{ note }}}` is wrapped,
    brackets included, the same way. The TEXT is unchanged: strip the tags from either render
    and the words are identical. With `script=False` (every OrchDoc) the output is
    byte-for-byte what it was before this parameter existed.
    """
    if not text:
        return ""
    raw = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    # Fences first, on the UNESCAPED text, so their contents are escaped wholesale and never
    # touched by an inline rule.
    blocks, buf, fence, fbuf = [], [], False, []
    start, fstart = 1, 1
    for n, ln in enumerate(raw, 1):
        m = orchdoc.is_fence_line(ln)
        if not fence and m:
            blocks.append(("md", buf, start)); buf = []
            fence, fbuf, fstart = True, [], n
            continue
        if fence:
            if m:
                blocks.append(("code", fbuf, fstart)); fence = False; fbuf = []
                start = n + 1
            else:
                fbuf.append(ln)
            continue
        buf.append(ln)
    if fence:
        blocks.append(("code", fbuf, fstart))
    blocks.append(("md", buf, start))

    out = []
    for kind, body, first in blocks:
        if kind == "code":
            if body:
                out.append("<pre%s><code>%s</code></pre>"
                           % (_src_attr(first if script else None),
                              html.escape("\n".join(body))))
            continue
        out.append(_render_md_block([html.escape(l) for l in body], xref,
                                    line0=first if script else None, dd=script))
    # Notes last, over the whole page: one can open in one block and close in another.
    return _pn_paint("".join(out)) if script else "".join(out)


def _src_attr(n):
    """The source-line attribute, or nothing - so a render without line numbers is unchanged."""
    return ' data-src-line="%d"' % n if n is not None else ""


# ==========================================================================================
# SCRIPT DROPDOWNS - `[[option one | option two]]`
#
# ⭐ TWO PASSES, BECAUSE THE GROUP HAS TO BE FOUND ON THE SOURCE BUT PAINTED ON THE HTML.
# Found first (after code spans are lifted out, so a group quoted in backticks stays code),
# with the markers swapped for control characters no inline rule treats specially. Painted
# last, after every other rule, and ONLY in text between tags: a span is never opened across
# a tag boundary, so `**a | b**` inside a group still nests correctly, and a marker that ended
# up inside an attribute goes back to being the plain character.
#
# ⛔ A `|` OUTSIDE A GROUP IS NOT A SEPARATOR and is never touched - tables split on theirs
# before any of this runs, and a bare pipe in a sentence is just a pipe.
# ==========================================================================================

_DD_RE = re.compile(r"\[\[((?:(?!\[\[|\]\]).)*)\]\]", re.S)
_DD_OPEN, _DD_CLOSE, _DD_SEP = "\x02", "\x03", "\x04"
_DD_TAG_SPLIT = re.compile(r"(<[^>]*>)")
_DD_TEXT_SPLIT = re.compile("([\x02\x03\x04])")
_DD_MARK = {_DD_OPEN: "[[", _DD_CLOSE: "]]", _DD_SEP: "|"}


def _dd_find(s):
    return _DD_RE.sub(lambda m: _DD_OPEN + m.group(1).replace("|", _DD_SEP) + _DD_CLOSE, s)


def _dd_paint(s):
    if _DD_OPEN not in s:
        return s
    out, inside = [], False
    for part in _DD_TAG_SPLIT.split(s):
        if part.startswith("<"):
            for k, v in _DD_MARK.items():
                part = part.replace(k, v)
            out.append(part)
            continue
        for piece in _DD_TEXT_SPLIT.split(part):
            if piece in _DD_MARK:
                out.append('<span class="dd-mk">%s</span>' % _DD_MARK[piece])
                inside = piece != _DD_CLOSE
            elif piece:
                out.append('<span class="dd-op">%s</span>' % piece if inside else piece)
    return "".join(out)


# ==========================================================================================
# SCRIPT NOTES - `{{{ a note about the process }}}`
#
# To make them stand out for me to easier see, make the brackets and everything inside the
# brackets #FFEBAD"*.
#
# ⭐ FOUND ON THE WHOLE PAGE, NOT PER PARAGRAPH. The painter walks the FINISHED html once, pairs
# the brackets in text only (a `}}}` in an href is not a close), and wraps each piece of text
# inside a pair in its own span: a span never crosses a tag, so the page's structure is
# untouched.
#
# A `{{{` with no `}}}` after it, and a `}}}` with no `{{{` before it, stay plain text - the
# same pairing as the editor's PN_RE, so the source and the preview agree.
# ==========================================================================================

_PN_MARK_RE = re.compile(r"\{\{\{|\}\}\}")


def _pn_wrap(t):
    return '<span class="pn">%s</span>' % t if t else ""


def _pn_paint(h):
    if "{{{" not in h:
        return h
    parts = _DD_TAG_SPLIT.split(h)          # even indexes are text, odd are tags
    marks, opened = {}, None
    for k in range(0, len(parts), 2):
        for m in _PN_MARK_RE.finditer(parts[k]):
            if m.group(0) == "{{{" and opened is None:
                opened = (k, m.start(), m.end())
            elif m.group(0) == "}}}" and opened is not None:
                marks.setdefault(opened[0], []).append(opened[1:])
                marks.setdefault(k, []).append((m.start(), m.end()))
                opened = None
    if not marks:
        return h
    out, inside = [], False
    for k, part in enumerate(parts):
        if k % 2:
            out.append(part)
            continue
        pos = 0
        for a, b in sorted(marks.get(k, ())):
            out.append(_pn_wrap(part[pos:a]) if inside else part[pos:a])
            out.append(_pn_wrap(part[a:b]))
            inside = part[a:b] == "{{{"
            pos = b
        out.append(_pn_wrap(part[pos:]) if inside else part[pos:])
    return "".join(out)


def script_diff(base, text):
    """
    """
    return TEXT.diff(base, text, paint=_pn_paint)


def _close_to(stack, out, depth):
    while len(stack) > depth:
        out.append("</li></%s>" % stack.pop())


def _render_md_block(lines, xref=None, line0=None, dd=False):
    """`line0` is the 1-based source line of `lines[0]`; given, every block says where it
    starts. `dd` turns on dropdown painting. Both default off, which is the OrchDoc render."""
    out, stack, para, i, blank = [], [], [], 0, False
    pstart = [0]

    def at(k):
        return _src_attr(line0 + k if line0 is not None else None)

    def inl(s):
        return _inline(s, xref, dd)

    def flush_para():
        if para:
            pics = _pictures_block(para, inl, at(pstart[0]))
            out.append(pics if pics is not None
                       else "<p%s>%s</p>" % (at(pstart[0]), inl(" ".join(para))))
            del para[:]

    def flush_all():
        flush_para()
        _close_to(stack, out, 0)

    while i < len(lines):
        ln = lines[i]

        if not ln.strip():
            flush_para()
            blank = True
            i += 1
            continue
        # ⛔ A BLANK LINE ENDS A LIST. Consumed here so every branch below sees it once.
        was_blank, blank = blank, False

        mc = _CMT_RE.match(ln)
        if mc:
            flush_all()
            out.append('<div class="cmt"%s>%s</div>' % (at(i), mc.group(1).strip()))
            i += 1
            continue

        m = _HEAD_RE.match(ln)
        if m:
            flush_all()
            lvl = min(6, len(m.group(1)) + 2)
            out.append("<h%d%s>%s</h%d>"
                       % (lvl, at(i), inl(m.group(2).strip()), lvl))
            i += 1
            continue

        if _HR_RE.match(ln):
            flush_all()
            out.append("<hr%s>" % at(i))
            i += 1
            continue

        # Tables: a header row followed by a |---|---| separator.
        if "|" in ln and i + 1 < len(lines) and re.match(
                r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$", lines[i + 1]):
            flush_all()
            out.append(_render_table(lines, i, xref, at(i), dd))
            while i < len(lines) and "|" in lines[i]:
                i += 1
            continue

        q = _QUOTE_RE.match(ln)
        if q:
            flush_all()
            qb, q0 = [], i
            while i < len(lines):
                qm = _QUOTE_RE.match(lines[i])
                if qm:
                    qb.append(qm.group(1))
                    i += 1
                elif lines[i].strip() and not _ULI_RE.match(lines[i]):
                    qb.append(lines[i].strip())
                    i += 1
                else:
                    break
            # Each quoted line was consumed in order, so qb[k] is source line q0 + k.
            out.append("<blockquote%s>%s</blockquote>"
                       % (at(q0), _render_md_block(
                           qb, xref, line0 + q0 if line0 is not None else None, dd)))
            continue

        mu, mo = _ULI_RE.match(ln), _OLI_RE.match(ln)
        if mu or mo:
            flush_para()
            indent = len((mu or mo).group(1).replace("\t", "    "))
            depth = indent // 2 + 1
            tag = "ul" if mu else "ol"
            text = mu.group(2) if mu else mo.group(3)

            while len(stack) > depth:
                _close_to(stack, out, len(stack) - 1)
            if len(stack) == depth and stack and stack[-1] != tag:
                _close_to(stack, out, len(stack) - 1)
            if len(stack) < depth:
                while len(stack) < depth:
                    out.append("<%s%s>" % (tag, at(i)))
                    stack.append(tag)
            else:
                out.append("</li>")
            cls = ""
            b = _BOX_RE.match(text)
            if b:
                checked = b.group(1).lower() == "x"
                cls = ' class="task %s"' % ("on" if checked else "off")
                text = ('<span class="box">%s</span> %s'
                        % ("&#10003;" if checked else "&#9744;",
                           inl(b.group(2))))
            else:
                text = inl(text)
            out.append("<li%s%s>%s" % (cls, at(i), text))
            i += 1
            continue

        if stack:
            # ⛔ ONLY AN INDENTED LINE CONTINUES AN ITEM ACROSS A BLANK LINE. Flush left
            # after a blank line means the list ended and this is a new paragraph - the
            # CommonMark rule. Without this the parser swallowed every following paragraph
            # into the last `li`, which is how whole sections ended up filed under a
            # finished `- [x]` sub-item and faded with it.
            # ⭐ A line that is NOT preceded by a blank line is a lazy continuation and
            # still belongs to the item, whatever its indent - that shape is everywhere in
            # these docs and is genuinely part of the sentence above it.
            if was_blank and len(ln) - len(ln.lstrip()) < 2:
                _close_to(stack, out, 0)
                if not para:
                    pstart[0] = i
                para.append(ln.strip())
                i += 1
                continue
            out.append(" " + inl(ln.strip()))
            i += 1
            continue

        if not para:
            pstart[0] = i
        para.append(ln.strip())
        i += 1

    flush_all()
    return "".join(out)


#
#     **Pictures:** the referral card four ways, at phone width
#
# Each image goes through the same `image()` rule as any other, so containment, the raster-only
# list and the visible reason for a path that does not resolve all still apply. `orchdoc.py
# check` refuses the old way (E-PICTUREPAGE) and names this block as the fix, so the block's
# shape is defined ONCE, in orchdoc.py, and read by both.
_PICS_HEAD_RE = orchdoc.PICTURES_HEAD_RE
_PICS_LINE_RE = orchdoc.PICTURES_LINE_RE


def _pictures_block(lines, inl, attr=""):
    """The folded gallery for one paragraph, or None when the paragraph is not one."""
    m = _PICS_HEAD_RE.match(lines[0])
    imgs = lines[1:]
    if not m or not imgs or not all(_PICS_LINE_RE.match(l) for l in imgs):
        return None
    figs = []
    for l in imgs:
        cap = _IMG_RE.match(l).group(1)
        figs.append("<figure>%s%s</figure>"
                    % (inl(l), "<figcaption>%s</figcaption>" % cap if cap else ""))
    what = m.group(1).strip()
    return ('<details class="pics"%s><summary><b>%d picture%s</b>%s'
            '<span class="pics-hint">click to show</span></summary>'
            '<div class="pics-grid">%s</div></details>'
            % (attr, len(imgs), "" if len(imgs) == 1 else "s",
               (": " + inl(what)) if what else "", "".join(figs)))


def _split_row(ln):
    s = ln.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return [c.replace("\\|", "|").strip() for c in re.split(r"(?<!\\)\|", s)]


def _render_table(lines, i, xref=None, attr="", dd=False):
    head = _split_row(lines[i])
    rows = []
    j = i + 2
    while j < len(lines) and "|" in lines[j]:
        rows.append(_split_row(lines[j]))
        j += 1
    h = "".join("<th>%s</th>" % _inline(c, xref, dd) for c in head)
    body = "".join(
        "<tr>%s</tr>" % "".join("<td>%s</td>" % _inline(c, xref, dd) for c in r)
        for r in rows)
    return ('<div class="tw"%s><table><thead><tr>%s</tr></thead><tbody>%s</tbody>'
            "</table></div>" % (attr, h, body))


# ==========================================================================================
# HTML
# ==========================================================================================

THEME_KEY = "claude-orchdoc-reader-theme"
SIZE_KEY = "claude-orchdoc-reader-textsize"

# ==========================================================================================
# TEXT SIZE
#
# ⭐ ONE ROOT SIZE, AND EVERY OTHER SIZE ALREADY POINTED AT IT. The safe way to do that is NOT to
# edit every font-size - a pass over forty declarations misses some, and the ones it misses are
# visible as a heading that did not grow next to body text that did.
#
# It was already nearly free: EVERY font-size in this stylesheet is `rem` or `em` except one.
# `body` was `font:14px/1.55`, an absolute value that would have sat still while everything
# around it scaled. Expressing it as `.875rem` (14/16, the browser default this design was
# drawn against) puts the whole scale on the root, so raising the root raises all of it and
# the hierarchy between the sizes is untouched.
#
# ⛔ THE STEPS ARE DERIVED, NOT TYPED. Writing five px values by hand is how the default and
# the control's middle step drift apart. They come from the old body size plus a whole number
# of points, so "two points bigger" is a statement the code makes rather than one a comment
# claims - and `test_orchdoc_reader.py` re-derives the same relationship instead of restating
# the numbers.
# ==========================================================================================

FS_OLD_BODY_PX = 14.0     # what `body` was, and what step -2 still is
FS_BODY_REM = 0.875       # 14px against the 16px root this stylesheet was written against
PT_PX = 4.0 / 3.0
FS_BUMP_PT = 2.0
FS_STEPS = (-2, -1, 0, 1, 2)   # offsets from the NEW default, which is step 0
FS_DEFAULT = 0


def fs_body_px(step=FS_DEFAULT):
    """Rendered body text, in px, at a step. Step 0 is the old size plus FS_BUMP_PT."""
    return FS_OLD_BODY_PX + (FS_BUMP_PT + step) * PT_PX


def fs_root_px(step=FS_DEFAULT):
    """The root size that puts body text at `fs_body_px(step)`; every rem follows it."""
    return fs_body_px(step) / FS_BODY_REM


def _px(v):
    return ("%.3f" % v).rstrip("0").rstrip(".") + "px"


# The default lives on bare :root so a page with NO stored choice - and a page whose storage
# throws - still gets the new size. The per-step rules only override it.
FS_CSS = (
    ":root{--fs:%s}\n" % _px(fs_root_px(FS_DEFAULT))
    + "html{font-size:var(--fs)}\n"
    + "".join(':root[data-fs="%d"]{--fs:%s}\n' % (s, _px(fs_root_px(s)))
              for s in FS_STEPS))

# ⛔ RUNS BEFORE THE FIRST PIXEL. Read the stored choice after the rails have painted and the
# page visibly flips colour. EVERY localStorage ACCESS IS WRAPPED - it THROWS, not returns
# null, in a private window and wherever site data is blocked, and an unguarded read here
# would abort the script and take the whole page's theming down with it.
def _pref_boot_js(key, attr, values):
    """Stamp ONE stored preference onto <html> before anything paints.

    ⭐ ONE MECHANISM, TWO PREFERENCES. Theme and text size want exactly the same thing - read
    a value, validate it against a fixed set, put it on the root element, and survive storage
    that throws - so they are the same function called twice rather than two copies that
    drift. The second copy is where the try/catch gets forgotten.
    """
    vals = json.dumps(list(values))
    a = json.dumps(attr)
    return ("(function(){var v=null;try{v=window.localStorage.getItem(%s);}catch(e){v=null;}"
            "var r=document.documentElement;"
            "if(%s.indexOf(v)>=0){r.setAttribute(%s,v);}"
            "else{r.removeAttribute(%s);}})();" % (json.dumps(key), vals, a, a))


def _pref_wire_js(key, attr, setter, values, default):
    """Click-to-change plus aria-pressed for ONE preference. Same contract as the boot half.

    Applying the attribute is separate from persisting it, so a storage write that throws
    still leaves the page on the value just picked - it simply will not be remembered.
    `default` is stored as ABSENCE: the attribute is removed and the key deleted, so the
    default always comes from the bare `:root` rule and never from a written value.
    """
    return ("(function(){var K=%s,A=%s,S=%s,V=%s,D=%s;"
            "function cur(){var v=null;try{v=window.localStorage.getItem(K);}catch(e){v=null;}"
            "return V.indexOf(v)>=0?v:D;}"
            "function paint(){var c=cur(),b=document.querySelectorAll(\"[\"+S+\"]\");"
            "for(var i=0;i<b.length;i++){"
            "b[i].setAttribute(\"aria-pressed\",b[i].getAttribute(S)===c?\"true\":\"false\");}}"
            "function set(v){var r=document.documentElement;"
            "if(v===D){r.removeAttribute(A);}else{r.setAttribute(A,v);}"
            "try{if(v===D){window.localStorage.removeItem(K);}"
            "else{window.localStorage.setItem(K,v);}}catch(e){}"
            "paint();}"
            "document.addEventListener(\"click\",function(ev){"
            "var t=ev.target&&ev.target.closest?ev.target.closest(\"[\"+S+\"]\"):null;"
            "if(t){set(t.getAttribute(S));}});"
            "paint();})();"
            % (json.dumps(key), json.dumps(attr), json.dumps(setter),
               json.dumps(list(values)), json.dumps(default)))


THEME_BOOT_JS = _pref_boot_js(THEME_KEY, "data-theme", ["light", "dark"])

# Applying the attribute is separate from persisting it, so a storage write that throws
# still leaves the page on the theme just picked - it simply will not be remembered.
THEME_WIRE_JS = _pref_wire_js(THEME_KEY, "data-theme", "data-theme-set",
                              ["auto", "light", "dark"], "auto")

# The text-size control is the SAME pair, called with a different key, attribute and
# value set. Its steps come from FS_STEPS, so a step can never be offered by a button
# that no CSS rule answers.
SIZE_VALUES = [str(v) for v in FS_STEPS]
SIZE_BOOT_JS = _pref_boot_js(SIZE_KEY, "data-fs", SIZE_VALUES)
SIZE_WIRE_JS = _pref_wire_js(SIZE_KEY, "data-fs", "data-fs-set",
                             SIZE_VALUES, str(FS_DEFAULT))

# ==========================================================================================
# {{{...}}} text."
#
# The page paints a note with two variables, --pnote (the text colour) and --pnotebg (a
# highlight). On the dark theme the colour is the text, as #FFEBAD is; on the light theme it is
# the highlight behind ordinary ink, as #FFEBAD is, because pale text cannot be read on white.
# So one entered colour becomes text on dark and highlight on light, through the CSS rules after
# `.pn`, which switch on `data-pn` on <html>.
#
# It is kept in this browser like the theme is: read and applied BEFORE THE FIRST PIXEL (the boot
# snippet below), the default stored as ABSENCE, every localStorage access in try/catch, and
# the stored value checked against the one form the page ever writes, "#RRGGBB" in capitals.
# ==========================================================================================
NOTE_COLOR_KEY = "claude-orchdoc-reader-notecolor"
NOTE_COLOR_DEFAULT = "#FFEBAD"   # the palette's --pnote (dark) and --pnotebg (light)

NOTE_BOOT_JS = (
    "(function(){var v=null;try{v=window.localStorage.getItem(%s);}catch(e){v=null;}"
    "var r=document.documentElement;"
    "if(typeof v===\"string\"&&/^#[0-9A-F]{6}$/.test(v)&&v!==%s){"
    "r.setAttribute(\"data-pn\",\"1\");r.style.setProperty(\"--pn-user\",v);}"
    "else{r.removeAttribute(\"data-pn\");r.style.removeProperty(\"--pn-user\");}})();"
    % (json.dumps(NOTE_COLOR_KEY), json.dumps(NOTE_COLOR_DEFAULT)))

# ==========================================================================================
# THE PALETTE - light on bare :root, dark emitted TWICE from ONE string.
#
# ⭐ A colour must never have its ONLY definition inside a media block, or `data-theme=light`
# on a dark-OS machine finds no light value to fall back to. So:
#   :root                                          -> the COMPLETE light palette
#   @media(dark){:root:not([data-theme="light"])}  -> dark follows the OS...
#   :root[data-theme="dark"]                       -> ...and the toggle overrides it
# Both dark selectors are needed: one alone only works in one direction. DARK_VARS is
# interpolated into both from a single string, so the copies cannot drift.
#
# uses, so work being done no longer looks like work on hold (PAUSED, DEFERRED: `--hold`).
# Measured with `wcag_contrast.py`: light #1b6969 on its chip #e2f2f3 is 5.57:1 and on the page
# 5.79:1; dark #51cdcd on its chip #123f3f is 6.06:1 and on the page 8.47:1. Its hue is 180 in
# both, the middle of the gap between DONE's green (149 light, 146 dark) and ON RECORD's blue
# (210, 211): 31 and 30 degrees from them in light, 33 and 31 in dark. Every other token in both
# strings is exactly what it was.
# ⛔ NO CSS COMMENT BETWEEN TWO DECLARATIONS IN THE STRINGS BELOW WHEN THE DECLARATION AFTER IT IS
# A HEX COLOUR. `wcag_contrast.parse_vars` splits on ";", so a comment ends up at the FRONT of the
# next declaration, which then does not start with "--" and is skipped: every palette test goes
# blind to that colour and nothing fails. That is why these two tokens carry their numbers here.
# ==========================================================================================

LIGHT_VARS = """
  --bg:#f4f3f0; --panel:#fff; --ink:#1b1b1d; --mut:#6d6c68; --faint:#95948e;
  --line:#e5e2db; --line2:#efedE8; --wash:#faf9f6;
  --gold:#CFAC6d; --goldink:#7c6229;
  --open:#bf5346; --openbg:#fbeceA; --openline:#f0cdc7;
  --done:#2d7b53; --donebg:#e9f4ee;
  --hold:#8d6a15; --holdbg:#fcf3dd;
  --info:#4a6f95; --infobg:#ecf1f7;
  /* live work that is NOT actionable now - apart from OPEN's red, PAUSED's amber, DONE's green */
  --blocked:#6b5b95; --blockedbg:#f0edf7; --blockedline:#d6cfe6;
  --prog:#1b6969; --progbg:#e2f2f3;
  --err:#a33; --sel:#f2ead8;
  --donetext:#95948e;
  --ddmark:#8a6a00; --ddopt:#0b0b0c; /* script dropdowns: the markers, then the options. Dark uses the human's #FFD800; on this light background that yellow is near-invisible, so the same hue, darkened */
  --pnote:var(--ink); --pnotebg:#FFEBAD; /* script {{{ notes }}}: the human's #FFEBAD. As text on this background it cannot be read, so here it is the highlight behind ordinary ink */
  --shadow:0 1px 2px rgba(25,22,15,.05),0 8px 22px rgba(25,22,15,.055)"""

#
# Soften the font color."* The ground was right and #eae8e4 on it was still 13.26:1.
#
# ⭐ THE LADDER, SO THE NEXT ADJUSTMENT IS ONE LINE RATHER THAN A RE-DERIVATION. Measured on
# #2d1c1c: #eae8e4 13.26 - #d8d4cf 11.00 - #cdc8c2 9.76 - **#c2bcb5 8.62** - #b8b1aa 7.65 (still
# AAA) - #ada6a0 6.75 - #a39c96 5.99.
#
# ⛔⛔ EVERY NUMBER FROM HERE DOWN IS THE PREVIOUS GENERATION. Read 6.28 below as history, not as
# the page.
#
# The reasoning below says headings must stay 1.37x above body or the pane flattens. At 8.62
# over 8.29 that step is 1.04.
#
# ⚠️ AND THE LIFT DID NOT REACH BOTH FILES FOR THREE DAYS. `render_daily_dashboard.py`
# for the same variable - the exact drift the shared-colour check exists to catch, and it
# was saying so while four other checks in that file were red for a superseded reason.
#
# the LEFT RAIL: *"Use the same ratios as in this pane - keep the darker background, but aim
# for the same ratio."* The rail's entry titles are `.etitle` and `.eid`, both `var(--mut)` -
# **#a3a19b, 6.28:1** on the same ground. So the reading pane's body text is not a nearby hex
# chosen by arithmetic; it is THAT VARIABLE, used directly.
#
# ⭐ THE SAME VARIABLE, NOT THE SAME NUMBER RETYPED. `.md{color:var(--mut)}` makes the pane and
# the rail incapable of drifting apart, which a second token holding a copy of #a3a19b would
# not.
#
# ⛔ BUT HEADINGS AND BOLD STAY ON `--ink`, OR THE PANE FLATTENS INTO ONE TONE. At 6.28:1
# everywhere there is no structure left to scan - and structure is what an OrchDoc entry is made
# of.
#
# ⚠️ THE THIRD LEVEL IS A SQUEEZE AND IT IS WORTH SAYING SO. `--faint` must sit BELOW body
# (6.28) and above the 4.5:1 AA floor, which is a corridor 1.4x wide. #95938d (5.28:1) is the
# geometric middle of it - 1.19x under body, 1.17x over the floor - so it is as far from both
# walls as anything can be. It is a real distinction but a narrow one; there is no room for a
# fourth level in the reading pane, and anything that needs one needs a different signal.
#
# ⛔ ONE RAIL COLOUR DOES MOVE, AND IT IS NAMED RATHER THAN HIDDEN. The rail's SELECTED entry
# title is `.ebtn.cur .etitle{color:var(--ink)}`, so it follows --ink from 13.26:1 to 8.62:1.
#
# `--faint` was #7d7b75, already marginal at 4.35:1 on the old page; a lighter background pushed
# it to 3.83:1, UNDER WCAG AA. #95938d (5.28:1) was chosen over the smallest value that clears -
# #8d8b85 scrapes over at 4.76:1, which is the same fragile position that put it under in the
# first place.
#
# ⛔ AND THE SURFACES HAD TO MOVE, BECAUSE THE PAGE GOT LIGHTER AND THEY DID NOT. Every one of
# them was drawn against a near-black. The worst was `--openbg`, which was **#2d1b19 against a
# page of #2d1c1c** - the same colour to one digit, so an open pill would have had no edge at
# all. Chips now lift 1.28-1.39:1 off the page (they lifted 1.12-1.18 on the old ground) and
# each still carries its own text over AA.
#
# ⛔ `--donetext` IS #6B5050 BECAUSE THE HUMAN NAMED IT, AND IT IS DELIBERATELY BELOW AA. Make
# their text 6B5050."* Measured, so it is on the record rather than discovered later: **#6B5050
# on #2d1c1c is 2.23:1** - under the 4.5:1 body floor and under the 3:1 large-text floor.
#
# A completed sub-item is history: it stays visible because seeing where the finished work sits
# is what makes the remaining decision readable (the rule), and it recedes because it is done.
#
# ⛔ SO THE RATIO MUST NOT LEAK ONTO ANYTHING LIVE, AND THAT IS WHAT THE SELECTORS ARE FOR.
# It reaches `.md li.task.on` and `.md del` - a CHECKED sub-item and struck text - and
# nothing else. A live entry's body, its heading, its id and its status pill are all
# untouched, so an entry holding one finished sub-item does not recede; that one LINE does.
# `test_dark_red_palette.py` asserts the sibling of a completed line keeps the body colour.
#
# ⭐ IF IT NEEDS ADJUSTING, IT IS ONE LINE. Measured on the same ground: #6B5050 2.23 -
# #7a5c5c 2.71 - #8a6a6a 3.36 - #9a7878 4.12 (the first that approaches AA).
#
# ⭐ THE STATUS COLOURS THEMSELVES ARE UNTOUCHED, AND THAT IS A DECISION, NOT AN OVERSIGHT.
# They were always DIMMER than the ink (--open 6.45 against 13.26); softening the ink narrows
# that gap to 1.34x rather than inverting it, and what makes a status colour read as an accent
# on a page of warm grey is CHROMA, not luminance - plus its own chip, which lifts off the
# page on its own. Checked by looking after the change as well as measured.
# `test_dark_red_palette.py` asserts every one of these numbers rather than eyeballing them.
DARK_VARS = """
  --bg:#2d1c1c; --panel:#3a2727; --ink:#c2bcb5; --mut:#bbb9b7; --faint:#95938d;
  --line:#503838; --line2:#3b2828; --wash:#382626;
  --gold:#CFAC6d; --goldink:#e2c68f;
  --open:#e78a80; --openbg:#5a2a24; --openline:#864138;
  --done:#6cc292; --donebg:#1c3b30;
  --hold:#e2bd6c; --holdbg:#463612;
  --info:#8fb3d9; --infobg:#22344a;
  --blocked:#a898cf; --blockedbg:#2f2740; --blockedline:#4a3f63;
  --prog:#51cdcd; --progbg:#123f3f;
  --err:#e88; --sel:#4e3a22;
  --donetext:#6B5050;
  --ddmark:#FFD800; --ddopt:#dcd8d3;  
  --pnote:#FFEBAD; --pnotebg:transparent;  
  --shadow:0 1px 2px rgba(0,0,0,.45),0 8px 22px rgba(0,0,0,.3)"""

CSS = (
    ":root{color-scheme:light dark;" + LIGHT_VARS + "}\n"
    "@media(prefers-color-scheme:dark){:root:not([data-theme=\"light\"]){" + DARK_VARS + "}}\n"
    ":root[data-theme=\"dark\"]{" + DARK_VARS + "}\n"
    ":root[data-theme=\"light\"]{color-scheme:light}\n"
    ":root[data-theme=\"dark\"]{color-scheme:dark}\n"
    + FS_CSS
# ⛔ RAW, AND THAT IS A BUG FIX, NOT A TIDY-UP. A CSS escape like `\2717` is also a valid
# PYTHON octal escape: in a normal string `\271` became U+00B9 and the stylesheet shipped
# `content:"¹7"`. So the unresolved marker has never rendered the cross it was written to
# render - the human has been seeing a superscript "¹7". Nothing failed, because a `content:`
# value is never validated; it just draws the wrong glyph. Raw keeps every CSS escape
# literal, and `test_orchdoc_reader.py` now asserts the backslash survives.
) + r"""
/* ⭐ THE READING MEASURE, as a token. `ch` is font-relative, so 82 characters stays 82
   characters at every step of the text-size control - which a rem or px value would not. */
:root{--measure:82ch}
*{box-sizing:border-box}
html,body{height:100%}
/* ⛔ rem, NOT px - THE ONE DECLARATION THAT HELD THE WHOLE SCALE DOWN. Every other
   font-size here is already rem or em, so this absolute 14px was the single value that
   would have sat still while the rest grew. .875rem is the same 14px against a 16px root,
   which is what this design was drawn against - so nothing moves until --fs does. */
body{margin:0;background:var(--bg);color:var(--ink);
  font:.875rem/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  -webkit-font-smoothing:antialiased;display:flex;flex-direction:column}
a{color:var(--goldink)}
/* ⛔ A LONG PATH IN AN INLINE CODE SPAN HAS NO PLACE TO BREAK, so it pushes the reading
   pane wider than the window and the whole page scrolls sideways. Found at the largest text
   step, where `src/_legacy/pages/product-app/components/Pricing...` ran 22px past the
   edge - but it was only ever a matter of how long the path was, so the fix belongs on the
   rule rather than on the step. `anywhere` breaks only when there is no other option.
   ⛔ NOT on `pre code`: a fenced block scrolls (see `pre` below), and breaking its lines
   instead would silently rewrap real commands and code. */
code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.88em;
  background:var(--wash);border:1px solid var(--line2);border-radius:4px;padding:.05em .32em;
  overflow-wrap:anywhere}
pre{background:var(--wash);border:1px solid var(--line);border-radius:8px;padding:.7rem .85rem;
  overflow-x:auto}
pre code{background:none;border:0;padding:0;font-size:.85em;overflow-wrap:normal}

/* ---- shell ---- */
.top{position:sticky;top:0;z-index:40;flex:0 0 auto;display:flex;gap:.6rem;align-items:center;
  padding:.5rem .8rem;background:var(--panel);border-bottom:1px solid var(--line);
  box-shadow:var(--shadow);flex-wrap:wrap}
.brand{font-weight:700;letter-spacing:-.01em;white-space:nowrap}
.brand b{color:var(--goldink)}
.grow{flex:1 1 8rem}
#q{width:100%;padding:.42rem .6rem;border:1px solid var(--line);border-radius:7px;
  background:var(--bg);color:var(--ink);font:inherit}
#q:focus{outline:2px solid var(--gold);outline-offset:1px}
.themer,.scope,.sizer{display:flex;gap:.2rem;align-items:center}
/* The text-size buttons are the theme buttons - same class, same pressed state - with the
   label drawn at the size it selects, which is the ordinary way to show a type ladder.
   ⛔ The ladder is in EM, so each button is sized relative to the control rather than to
   the root: at the largest step the whole header would otherwise grow by the amount the
   button is advertising, and the control would shove the search box off the row. */
.szbtn{font-weight:700;line-height:1;padding:.2rem .42rem;min-width:1.5rem}
.szbtn.z0{font-size:.62em} .szbtn.z1{font-size:.78em} .szbtn.z2{font-size:.95em}
.szbtn.z3{font-size:1.12em} .szbtn.z4{font-size:1.3em}
.tlab{color:var(--faint);font-size:.75rem;text-transform:uppercase;letter-spacing:.06em}
.tbtn{font:inherit;font-size:.78rem;padding:.24rem .5rem;border:1px solid var(--line);
  background:var(--bg);color:var(--mut);border-radius:6px;cursor:pointer}
.tbtn[aria-pressed="true"]{background:var(--gold);border-color:var(--gold);color:#241d0c;
  font-weight:650}

/* ⛔ THE HEADER'S HEIGHT IS MEASURED, NOT GUESSED. This was `height:calc(100vh - 3.05rem)`,
   a hardcoded stand-in for the header - already ~5px wrong, and a number that cannot be
   right at more than one text size, because the header is built out of the same type that
   just became adjustable. Body is a flex column now, so the shell takes whatever is left
   over and the magic number is gone rather than re-tuned. */
.app{display:grid;grid-template-columns:13.5rem 20rem 1fr;flex:1 1 auto;min-height:0}
@media(max-width:1100px){.app{grid-template-columns:11rem 17rem 1fr}}

.rail{overflow-y:auto;border-right:1px solid var(--line);background:var(--panel)}
.rail-docs{padding:.5rem}
.pane{--pane-pt:1.1rem;overflow-y:auto;padding:var(--pane-pt) 1.4rem 6rem;background:var(--bg)}

/* ---- doc rail ---- */
.dbtn{display:flex;align-items:center;gap:.5rem;width:100%;text-align:left;font:inherit;
  padding:.4rem .5rem;border:0;border-radius:7px;background:none;color:var(--ink);
  cursor:pointer}
.dbtn:hover{background:var(--wash)}
.dbtn[aria-current="true"]{background:var(--sel);font-weight:650}
.dbtn .nm{flex:1}
.badge{min-width:1.35rem;text-align:center;font-size:.72rem;font-weight:700;
  padding:.08rem .34rem;border-radius:999px;background:var(--openbg);color:var(--open);
  border:1px solid var(--openline)}
.badge.zero{background:var(--wash);color:var(--faint);border-color:var(--line)}
.badge.warn{background:var(--holdbg);color:var(--hold);border-color:var(--hold)}
.railhead{font-size:.7rem;text-transform:uppercase;letter-spacing:.08em;color:var(--faint);
  padding:.5rem .5rem .25rem;font-weight:700}

/* ---- entry rail ---- */
/* ⛔ THESE LIVE IN THE STYLESHEET, NOT INLINE. They were an inline flex declaration on the
   element, and an inline declaration outranks a stylesheet rule - so the phone layout's
   display:none on this column was silently ignored and the middle rail stayed on top of
   the reading pane. Computed style said flex while the rule said none; only asking the
   browser what it ACTUALLY computed found it. */
.rail-mid{display:flex;flex-direction:column;min-height:0}
.rail-entries{padding:.35rem .4rem 4rem;flex:1;overflow-y:auto}
.sec{font-size:.7rem;text-transform:uppercase;letter-spacing:.07em;color:var(--faint);
  font-weight:700;padding:.7rem .45rem .2rem;position:sticky;top:0;background:var(--panel);
  z-index:2}
.ebtn{display:flex;gap:.45rem;align-items:baseline;width:100%;text-align:left;font:inherit;
  padding:.3rem .45rem;border:0;border-left:2px solid transparent;border-radius:0 6px 6px 0;
  background:none;color:var(--ink);cursor:pointer;line-height:1.35}
.ebtn:hover{background:var(--wash)}
.ebtn.cur{background:var(--sel);border-left-color:var(--gold)}
.eid{font-weight:700;font-size:.78rem;font-family:ui-monospace,Menlo,Consolas,monospace;
  white-space:nowrap;color:var(--mut)}
.ebtn.cur .eid{color:var(--goldink)}
.etitle{flex:1;font-size:.82rem;color:var(--mut);overflow:hidden;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
.ebtn.cur .etitle{color:var(--ink)}
.dot{width:.5rem;height:.5rem;border-radius:50%;flex:none;align-self:center;
  background:var(--faint)}
.dot.open{background:var(--open)} .dot.done{background:var(--done)}
.dot.hold{background:var(--hold)} .dot.info{background:var(--info)}
.dot.blocked{background:var(--blocked)}
.dot.prog{background:var(--prog)}
.dot.unk{background:var(--faint);opacity:.5}
.pin{font-size:.62rem;font-weight:800;color:var(--open);letter-spacing:.04em}
#cross .etitle{display:block;-webkit-line-clamp:unset;overflow:visible}

#tbox .xdiv,#tbox .xnone,#cross .xdiv,#cross .xnone{font-size:.72rem;color:var(--mut);
  padding:.35rem .45rem;background:var(--wash)}
#tbox .xdiv,#cross .xdiv{border-top:1px solid var(--line);border-bottom:1px solid var(--line);margin:.3rem 0}
#tbox{max-height:46vh;overflow:auto;border-bottom:1px solid var(--line)}
#tbox .etitle{display:block;-webkit-line-clamp:unset;overflow:visible}
#tbox .snip mark,#cross .snip mark{background:var(--gold);color:inherit;border-radius:2px}
.ebtn.donetoday .etitle,.ebtn.donetoday .eid{color:var(--faint);opacity:.7}
.today-acts{display:flex;gap:.5rem;margin:.2rem 0 .8rem}

.today-strip{margin:0 0 1.1rem;padding:.55rem .7rem;border:1px solid var(--gold);
  border-radius:8px;background:var(--panel)}
.ts-head{font-weight:700;font-size:.82rem;letter-spacing:.03em;margin:0 0 .35rem}
.ts-head .cnt{font-weight:400;color:var(--faint)}
.ts-row{display:flex;gap:.5rem;align-items:center;flex-wrap:wrap;padding:.2rem 0;
  border-top:1px solid var(--line);font-size:.86rem}
.ts-row.done .ts-title{color:var(--faint);text-decoration:line-through}
.ts-title{flex:1 1 14rem;min-width:0}
.ts-row .today-acts{margin:0}
.hdr-acts{display:inline-flex;gap:.4rem;align-items:center;margin-left:auto}
.dt-btn.on{background:var(--gold);color:#241d0c;border-color:var(--gold)}
.prio-sel{font:inherit;font-size:.78rem;color:var(--ink);background:var(--panel);
  border:1px solid var(--line);border-radius:6px;padding:.1rem .3rem}
.mk-confirm{display:flex;flex-wrap:wrap;gap:.5rem;align-items:center;margin:.35rem 0 .5rem;
  padding:.5rem .7rem;border:1px solid var(--gold);border-radius:8px;background:var(--panel);
  font-size:.84rem}
.mk-confirm input[type=text]{flex:1 1 16rem;min-width:0;font:inherit;font-size:.84rem;
  color:var(--ink);background:var(--bg);border:1px solid var(--line);border-radius:6px;
  padding:.3rem .5rem}
.mk-undo{margin:.35rem 0 .5rem;padding:.35rem .7rem;border:1px solid var(--line);
  border-radius:8px;background:var(--panel);font-size:.82rem;color:var(--mut)}
.mk-undo .nbtn{margin-left:.4rem}
.tag.prio-a{background:var(--gold);color:#241d0c;border-color:var(--gold)}
.tag.prio-d{text-decoration:line-through}
.today-add{display:flex;flex-wrap:wrap;gap:.5rem;align-items:center;margin:0 0 1.2rem;
  padding:.7rem;border:1px solid var(--line);border-radius:8px;background:var(--panel)}
.today-add input[type=text]{flex:1 1 18rem;min-width:0}
.today-add input,.today-add select{font:inherit;font-size:.86rem;color:var(--ink);
  background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:.35rem .5rem}
.today-add input[type=number]{width:5rem}
.today-say{flex-basis:100%;font-size:.8rem;color:var(--mut)}
.today-say:empty{display:none}
.snip{display:block;font-size:.74rem;color:var(--faint);margin-top:.1rem}

.star{flex:none;align-self:baseline;font-size:.9rem;line-height:1;cursor:pointer;
  color:var(--faint);opacity:0;padding:0 .1rem;user-select:none}
.ebtn:hover .star,.star:focus-visible{opacity:1}
.star.on{opacity:1;color:var(--gold)}
.star:hover{color:var(--gold)}

.docins{position:sticky;top:calc(-1 * var(--pane-pt));z-index:7;background:var(--bg);
  margin:0 0 .8rem;padding:.55rem 0;border-bottom:1px solid var(--line)}
.docins-row{display:flex;gap:.5rem;align-items:flex-start}
.docins textarea{flex:1;min-width:0;font:inherit;font-size:.86rem;line-height:1.4;
  color:var(--ink);background:var(--panel);border:1px solid var(--line);border-radius:6px;
  padding:.35rem .55rem;height:2.1rem;resize:none}
.docins.open textarea{height:6.5rem;resize:vertical}
.docins .nbtn{margin:0;white-space:nowrap;align-self:flex-start;padding:.45rem .7rem}
.docins-say{font-size:.8rem;color:var(--mut);margin:.35rem 0 0}
.docins-say:empty{display:none}
.docins-say a{color:var(--goldink)}


/* --docins-h: a jump lands BELOW the sticky instruction bar, not under it (`jump()` reads this). */
.entry{scroll-margin-top:calc(var(--docins-h,0rem) + 1rem);margin:0 0 2.6rem;padding:0 0 2.2rem;
  border-bottom:1px solid var(--line2);max-width:62rem;
  content-visibility:auto;contain-intrinsic-size:auto 1200px}
/* ⭐ THE LAST ENTRY NEEDS NO CLOSING RULE - there is nothing below it to be separated from,
   and the border plus 4.8rem of air above the pane's own bottom padding reads as a mistake. */
.entry:last-of-type{border-bottom:0;margin-bottom:1rem}
.entry h2.et{font-size:1.06rem;margin:.2rem 0 .5rem;line-height:1.35;
  display:flex;gap:.5rem;align-items:baseline;flex-wrap:wrap}
.entry h2.et .eid{font-size:.95rem;color:var(--goldink)}
/* The pills are the entry's metadata, not its first paragraph. Separate them from the body. */
.meta{display:flex;gap:.4rem;flex-wrap:wrap;align-items:center;margin:0 0 1.05rem}
.tag{font-size:.7rem;font-weight:700;letter-spacing:.03em;padding:.1rem .42rem;
  border-radius:999px;border:1px solid var(--line);color:var(--mut);background:var(--wash)}
.tag.open{background:var(--openbg);color:var(--open);border-color:var(--openline)}
.tag.done{background:var(--donebg);color:var(--done);border-color:var(--done)}
.tag.hold{background:var(--holdbg);color:var(--hold);border-color:var(--hold)}
.tag.info{background:var(--infobg);color:var(--info);border-color:var(--info)}
.tag.blocked{background:var(--blockedbg);color:var(--blocked);border-color:var(--blockedline)}
.tag.prog{background:var(--progbg);color:var(--prog);border-color:var(--prog)}
.tag.plate{background:var(--gold);color:#241d0c;border-color:var(--gold)}

.tag.pnow{background:var(--gold);color:#241d0c;border-color:var(--gold)}
.tag.pwhy{opacity:.7;font-weight:400;letter-spacing:0}

.legend{display:flex;gap:.4rem;flex-wrap:wrap;align-items:center;margin:0 0 1.1rem;max-width:62rem}
.legend .tag{cursor:help}
.legend .lg-sep{width:1px;align-self:stretch;background:var(--line);margin:0 .15rem}
/* The label is NOT a `.tlab`: a phone hides every `.tlab` (it is the header's "Text" and
   "Theme"), and `--faint` is 2.74:1 on the light page. `--mut` is 4.74:1 there, 8.29:1 on dark. */
.legend .lg-lab{color:var(--mut);font-size:.75rem;font-weight:600;text-transform:uppercase;
  letter-spacing:.06em;margin-right:.2rem}
.legend+.grouphead{margin-top:1rem}

.md{font-size:.9rem;line-height:1.6;color:var(--mut)}
/* ⛔ AND THE STRUCTURE STAYS ABOVE IT. Body at 8.29 with headings and bold at the same
   value is one flat tone, and an OrchDoc entry is almost entirely structure - bold runs,
   sub-headings, checkbox lines. `--ink` is 8.62:1, a visible 1.37x step up. */
.md strong,.md b{color:var(--ink)}
/* ⛔ THE MEASURE APPLIES TO PROSE BLOCKS ONLY, and by listing them rather than capping `.md`
   itself - a cap on the container would take the tables and fenced blocks with it, and a
   rewrapped command is a worse defect than a wide one. */
.md>p,.md>ul,.md>ol,.md>blockquote,.md>h3,.md>h4,.md>h5,.md>h6,.md>details,.md>.cmt{
  max-width:var(--measure)}
.md>:first-child{margin-top:0}
.md>:last-child{margin-bottom:0}
/* A heading belongs to what FOLLOWS it, so the space above is much larger than the space
   below - that asymmetry is what makes a heading read as a boundary rather than a bold line. */
.md h3,.md h4,.md h5,.md h6{font-size:.94rem;margin:1.7em 0 .5em;line-height:1.35;
  color:var(--ink)}
.md p{margin:1.42em 0}
.md ul,.md ol{margin:1.42em 0;padding-left:1.35em}
/* ⛔ THE CHECKBOX LINES ARE THE WALL. An OrchDoc entry's sub-items are consecutive `li`s, and
   at .16rem they touched. This is the single value the human is most likely to feel. */
.md li{margin:0.75em 0}
/* A nested list is one thought, so it stays tighter than the gap between its parents. */
.md li>ul,.md li>ol{margin:0.675em 0}
.md li.task{list-style:none;margin-left:-1.35em}
/* ⛔ A COMPLETED SUB-ITEM RECEDES, AND THE SCOPE IS THE LINE. `.md li.task.on` is a
   CHECKED checkbox inside the reading pane - one list item - so an entry holding one
   finished sub-item keeps its heading, its id, its status pill and every sibling line at
   the body colour. Widening this to the entry is the mistake it is written to avoid.
   ⭐ First it was `--mut`, which became the BODY colour and so stopped being a signal at
   all; then `--faint`, which was too close to read as finished. The human's own value now. */
.md li.task.on{color:var(--donetext)}
/* The check glyph goes with the line it belongs to. Left on `--done` green it becomes the
   loudest thing on a row whose whole point is to be quiet. */
.md li.task.on .box{color:var(--donetext)}

.md li.task.on strong,.md li.task.on b,.md li.task.on em,.md li.task.on i,
.md li.task.on code,.md li.task.on a,.md li.task.on del,.md li.task.on span{
  color:var(--donetext)}
/* The chip's border and the link's underline carry as much weight as their text. */
.md li.task.on code{border-color:var(--donetext);background:none}
.md li.task.on a{border-bottom-color:var(--donetext)}
/* ⭐ EMOJI IGNORE `color` - they carry their own. A checked line that still shows a
   full-strength red circle has not receded, whatever its text does. Opacity is the only
   property that reaches them, and at .9 it costs the text almost nothing. */
.md li.task.on{opacity:.9}
.md .box{font-weight:700;color:var(--done)}
.md li.task.off .box{color:var(--faint)}
.md blockquote{margin:1.15em 0;padding:.35em 0 .35em 1em;border-left:3px solid var(--gold);
  color:var(--mut)}
/* Struck text is the other thing this pane already dimmed to mean 'finished'. */
.md del{color:var(--donetext)}
.md hr{border:0;border-top:1px solid var(--line);margin:1.9em 0}
.md pre{margin:1.05em 0}
.tw{overflow-x:auto;margin:1.15em 0}
.md table{border-collapse:collapse;font-size:.84rem;min-width:100%}
.md th,.md td{border:1px solid var(--line);padding:.42rem .6rem;text-align:left;
  vertical-align:top}
.md th{background:var(--wash)}
.md details{margin:1.05em 0;padding:.55em .8em;background:var(--wash);
  border:1px solid var(--line);border-radius:7px}
.md summary{cursor:pointer;font-weight:650}
.md a.shot{display:block;margin:1em 0;max-width:var(--measure);line-height:0}
.md a.shot img{max-width:100%;max-height:34rem;width:auto;border:1px solid var(--line);
  border-radius:8px;background:var(--wash);display:block}
.md a.shot:hover img{border-color:var(--gold,var(--line2))}
.md img{max-width:100%}
/* A path that did not resolve stays VISIBLE, with the reason attached. */
.md .noimg{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:.78rem;
  color:var(--mut);word-break:break-word}
.md .noimg-why{color:var(--faint);font-style:italic;margin-left:.45rem}

.md details.pics{border-left:3px solid var(--gold);border-radius:0 8px 8px 0;padding:0}
.md details.pics>summary{padding:.55em .8em;font-weight:400;list-style:none;user-select:none}
.md details.pics>summary::-webkit-details-marker{display:none}
.md details.pics>summary::before{content:"\25b8 ";display:inline-block;transition:transform .12s}
.md details.pics[open]>summary::before{transform:rotate(90deg)}
.md .pics-hint{color:var(--faint);font-size:.78rem;margin-left:.6em}
.md details.pics[open] .pics-hint{display:none}
.md .pics-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,11rem),1fr));
  gap:.8rem;padding:.2em .8em .9em;align-items:start}
.md .pics-grid figure{margin:0}
.md .pics-grid a.shot{margin:0}
.md .pics-grid figcaption{font-size:.8rem;color:var(--mut);margin:.35em 0 0;line-height:1.4}
.md .cmt{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:.72rem;
  color:var(--faint);opacity:.75;margin:.9em 0;word-break:break-word}

.notes{margin:0 0 1.25rem;max-width:var(--measure)}
/* An entry with no notes yet still needs somewhere for "Add a note" to appear - but only
   once the page knows it can actually save one. */
.notes.empty:not(.live){display:none}
.note{border-left:3px solid var(--gold);background:var(--sel);border-radius:0 8px 8px 0;
  padding:.65em .9em;margin:0 0 .6em}
.note-h{display:flex;gap:.5rem;align-items:baseline;flex-wrap:wrap;font-size:.72rem;
  font-weight:800;letter-spacing:.05em;text-transform:uppercase;color:var(--goldink);
  margin:0 0 .35em}
.note-when{font-weight:400;letter-spacing:0;text-transform:none;color:var(--mut)}
.note-b{font-size:.9rem;line-height:1.6;white-space:pre-wrap;overflow-wrap:anywhere}
.note-img{display:block;margin:.4em 0}
.note-img img{max-width:100%;max-height:22rem;border:1px solid var(--line);border-radius:6px}
.note-acts{display:flex;gap:.35rem;flex-wrap:wrap;margin:.55em 0 0}

.notes.live{display:contents}
.notes.live>*{max-width:var(--measure)}
.notes.live>[data-note-tools]{position:sticky;top:var(--docins-h,0rem);z-index:6;background:var(--bg);
  margin:0 0 1.25rem;padding:.4rem 0}
.nbtn{font:inherit;font-size:.74rem;padding:.25rem .55rem;border:1px solid var(--line);
  background:var(--panel);color:var(--mut);border-radius:6px;cursor:pointer}
.nbtn:hover{background:var(--wash);color:var(--ink)}
.nbtn.primary{background:var(--gold);border-color:var(--gold);color:#241d0c;font-weight:650}
.nbtn.danger{color:var(--open);border-color:var(--openline)}
.nbtn[disabled]{opacity:.55;cursor:default}
.note-edit{margin:0 0 .6em}
.note-edit textarea{width:100%;min-height:5em;font:inherit;font-size:.9rem;line-height:1.6;
  padding:.55em .75em;border:1px solid var(--line);border-radius:8px;background:var(--panel);
  color:var(--ink);resize:vertical}
.note-edit textarea:focus{outline:2px solid var(--gold);outline-offset:1px}
.note-hint{color:var(--faint);font-size:.75rem;margin:.4em 0 0}

.notes.live>.note-edit{position:sticky;top:calc(var(--docins-h,0rem) - var(--pane-pt,0rem));z-index:8;background:var(--bg);
  max-height:min(13rem,36vh);overflow-y:auto;padding-top:.35rem;
  box-shadow:0 4px 10px rgba(0,0,0,.12)}
.notes.live>.note-edit.instruct{background:var(--openbg)}
.notes.live>.note-edit textarea{min-height:3.2em;max-height:7rem}
/* Only the editor's own hint (a direct child). The receipt after a send reuses this class and
   its "It is on main" / "being saved to GitHub" lines are .note-hint too; those must show. */
.notes.live>.note-edit>.note-hint{display:none}

.note-err{margin:.5em 0 0;padding:.55em .75em;border:1px solid var(--open);
  background:var(--openbg);color:var(--open);border-radius:7px;font-size:.8rem}
.note-ok{margin:.5em 0 0;color:var(--done);font-size:.8rem;font-weight:650}

.note-restored{margin:0 0 .4em;padding:.4em .7em;border-left:3px solid var(--gold);
  background:var(--wash);color:var(--ink);font-size:.78rem;border-radius:0 6px 6px 0}
.note-edit .ehead{font-size:.72rem;font-weight:800;letter-spacing:.05em;
  text-transform:uppercase;color:var(--goldink);margin:0 0 .35em}
#draft-bar{position:fixed;right:1rem;bottom:1rem;z-index:60;
  max-width:min(26rem,calc(100vw - 2rem));background:var(--panel);color:var(--ink);
  border:1px solid var(--gold);border-left:4px solid var(--gold);border-radius:8px;
  padding:.6rem .8rem;box-shadow:0 6px 24px rgba(0,0,0,.18);font-size:.8rem}
#draft-bar[hidden]{display:none}
#draft-bar .dh{font-weight:750;margin:0 0 .3rem}
#draft-bar .di{display:flex;align-items:center;gap:.4rem;flex-wrap:wrap;margin:.3rem 0}
#draft-bar .dl{flex:1 1 12rem}
#draft-bar .dt{color:var(--mut)}
#draft-bar .dg{flex:1 1 100%;color:var(--open)}
#draft-bar textarea{flex:1 1 100%;min-height:4em;font:inherit;font-size:.8rem;
  border:1px solid var(--line);border-radius:6px;background:var(--wash);color:var(--ink)}

details.attest,details.note-fold{margin:.2rem 0}
details.attest>summary,details.note-fold>summary{cursor:pointer;font-size:.82rem;
  color:var(--dim);list-style:none;padding:.15rem 0;user-select:none}
details.attest>summary::-webkit-details-marker,
details.note-fold>summary::-webkit-details-marker{display:none}
details.attest>summary::before,details.note-fold>summary::before{content:"\25b8 ";
  display:inline-block;transition:transform .12s}
details.attest[open]>summary::before,
details.note-fold[open]>summary::before{transform:rotate(90deg)}
details.attest>summary:hover,details.note-fold>summary:hover{color:var(--fg)}
/* A seen note's toggle IS its header line: keep the header's look, drop the leading caret,
   and put the caret at the right end, where the human put it. */
details.note-fold>summary.note-h{font-size:.72rem;color:var(--goldink);padding:0;margin:0;
  user-select:auto}
details.note-fold>summary.note-h::before{content:none}
details.note-fold[open]>summary.note-h{margin:0 0 .35em}
.note-caret{margin-left:auto;color:var(--dim);font-size:.95rem;line-height:1;
  display:inline-block;transition:transform .12s}
details.note-fold[open] .note-caret{transform:rotate(90deg)}
details.note-fold>summary.note-h:hover .note-caret{color:var(--fg)}

/* ---- AN INSTRUCTION, WHICH IS THE ONE CONTROL ON THIS PAGE THAT REACHES OUT ----
   ⛔ IT MUST NOT LOOK LIKE A NOTE, BECAUSE IT DOES NOT BEHAVE LIKE ONE. A note is
   passive: it waits in the document until the owner happens to open it. An instruction is
   the same durable line, and it also makes that orchestrator's Stop hook refuse to end a
   round until they acknowledge it. The human is reaching into another session's turn; a control
   that did that while looking identical to the quiet one would be a trap.

   ⭐ SO IT BORROWS THE PAGE'S LOUD TRIPLE (--open / --openbg / --openline) rather than
   inventing a colour. Those three are the only accent defined in BOTH palettes with a
   background, an ink and a line, and "loud" is exactly what is being said. It is not a
   claim that anything is wrong - the heading says what it is, in words, for the same
   reason "NOTE FROM" is in the markup rather than a `content:` pseudo-element. */
.note.instruct{border-left:3px solid var(--open);border:1px solid var(--openline);
  border-left:3px solid var(--open);background:var(--openbg)}
.note.instruct .note-who{color:var(--open)}

.note-id{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  
  font-size:1.64em;font-weight:700;letter-spacing:.02em;opacity:1;
  white-space:nowrap;margin-right:.55em;padding:.05em .4em;border-radius:4px;
  border:1px solid currentColor}
.note.instruct .note-id{color:var(--open)}

.note-ack{font-weight:700;letter-spacing:0;text-transform:none;font-size:.7rem}
.note-ack.hit{color:var(--done)}
.note-ack.wait{color:var(--mut);font-weight:400;font-style:italic}
/* The button that opens it. Outlined in the loud colour at rest and filled on hover, so
   it reads as the consequential one next to the quiet "Add a note". */
.nbtn.act{color:var(--open);border-color:var(--open);font-weight:650}
.nbtn.act:hover{background:var(--open);border-color:var(--open);color:var(--panel)}
.note-edit.instruct{border:1px solid var(--openline);border-left:3px solid var(--open);
  background:var(--openbg);border-radius:0 8px 8px 0;padding:.65em .9em}
.note-edit.instruct textarea{background:var(--panel)}
.note-edit.instruct textarea:focus{outline:2px solid var(--open)}
.note-edit .ihead{font-size:.72rem;font-weight:800;letter-spacing:.05em;
  text-transform:uppercase;color:var(--open);margin:0 0 .35em}
/* What ACTUALLY happened, after the save - and only what is TRUE at that moment. The
   server writes a line to a document; it does not notify anybody, and it must not say it
   did. What it can honestly report is that the line is there and nobody has acknowledged
   it yet, plus where the acknowledgement will show up when it comes. */
.note-done{font-size:.82rem;line-height:1.6}
.note-done p{margin:.25em 0}
.note-done .miss{color:var(--open);font-weight:650}
.note-done .hit{color:var(--done);font-weight:650}

/* ---- cross-doc references ----
   A resolved one looks like a link because it IS one. The two unresolved ones must NOT: an
   unresolved reference wearing link styling would be clicked, go nowhere, and cost the
   working ones their credibility. The "?" on the blind marker is the same "?" the doc rail
   puts on a document whose entries the parser cannot read - one symbol, one meaning.

   ⛔ AN ARCHIVED REFERENCE IS THE THIRD APPEARANCE AND IT IS STILL A LINK. It reads as a
   link (it works like one), in the muted "done" colour the status dots and tags already use
   for closed work, with a check rather than a cross. The cross means "this went nowhere";
   putting it on a correct citation to a finished entry is the exact harm this replaces. */
.xref{white-space:nowrap}
a.xref{color:var(--goldink);text-decoration:none;border-bottom:1px solid var(--gold);
  border-radius:3px;padding:0 .12em}
a.xref:hover,a.xref:focus{background:var(--sel);outline:none}
a.xref code{color:var(--goldink);border-color:var(--gold)}
.md a.xref-archived{color:var(--done);border-bottom:1px dashed var(--done);
  background:var(--donebg)}
.md a.xref-archived code{color:var(--done);border-color:var(--done)}
.md a.xref-archived:after{content:"\2713";font-size:.68em;font-weight:800;
  vertical-align:super;color:var(--done)}
.md span.xref-blind{color:var(--hold);background:var(--holdbg);
  border-bottom:1px dashed var(--hold);border-radius:3px;padding:0 .12em;cursor:help}
.md span.xref-blind:after{content:"?";font-size:.72em;font-weight:800;vertical-align:super}
.md span.xref-dead{color:var(--mut);border-bottom:1px dotted var(--faint);cursor:help}
.md span.xref-dead:after{content:"\2717";font-size:.68em;font-weight:800;
  vertical-align:super;color:var(--faint)}

.sec.rulesec{text-transform:none}
.grouphead{margin:3.2rem 0 1.3rem;padding-bottom:.35rem;border-bottom:2px solid var(--gold);
  font-size:.95rem;letter-spacing:.02em}
.grouphead:first-child{margin-top:.2rem}
.grouphead .cnt{color:var(--faint);font-weight:400;font-size:.82rem}

.note{border:1px solid var(--hold);background:var(--holdbg);color:var(--hold);
  border-radius:8px;padding:.6rem .8rem;margin:0 0 1rem;font-size:.85rem;max-width:62rem}
.note b{color:var(--hold)}
.empty{color:var(--faint);padding:2rem 0;font-size:.9rem}
.hint{color:var(--faint);font-size:.78rem;margin:.1rem 0 1rem}

/* ---- filters ---- */
.filters{display:flex;gap:.25rem;flex-wrap:wrap;padding:.45rem .4rem;
  border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--panel);z-index:3}
.fbtn{font:inherit;font-size:.73rem;padding:.2rem .45rem;border:1px solid var(--line);
  background:var(--bg);color:var(--mut);border-radius:999px;cursor:pointer}
.fbtn[aria-pressed="true"]{background:var(--gold);border-color:var(--gold);color:#241d0c;
  font-weight:700}
#owner{font:inherit;font-size:.73rem;padding:.2rem .3rem;border:1px solid var(--line);
  border-radius:6px;background:var(--bg);color:var(--mut);max-width:8.5rem}
.count{padding:.3rem .5rem;font-size:.72rem;color:var(--faint);border-bottom:1px solid var(--line)}

/* filters HIDE, they never remove - see the module docstring */
.hid{display:none !important}

/* ---- §1 LINKS AND DOCS ----
   ⛔ NO NEW COLOUR TOKENS HERE OR BELOW. `test_dark_red_palette.py` pins the palette, and
   everything this needs already exists: gold for "this is a way in", the wash and line greys
   for the frame. */
.lbtn{display:flex;gap:.45rem;align-items:baseline;width:100%;text-align:left;font:inherit;
  font-size:.8rem;font-weight:700;padding:.4rem .5rem;margin:.35rem 0 .15rem;
  border:1px solid var(--line);border-left:3px solid var(--gold);border-radius:0 6px 6px 0;
  background:var(--wash);color:var(--goldink);cursor:pointer}
.lbtn:hover{background:var(--sel)}
.lbtn .cnt{margin-left:auto;color:var(--faint);font-weight:400;font-size:.72rem}
details.links{margin:0 0 2.2rem;padding:.55rem .9rem;border:1px solid var(--line);
  border-left:3px solid var(--gold);border-radius:0 8px 8px 0;background:var(--panel);
  max-width:62rem}
details.links>summary{cursor:pointer;font-weight:750;color:var(--goldink);font-size:.95rem}
details.links>summary .lsub{font-weight:400;color:var(--mut)}
details.links>summary .cnt{font-weight:400;color:var(--faint);font-size:.78rem}
details.links[open]>summary{margin:0 0 .8rem}
a.flink{color:var(--goldink);text-decoration:none;border-bottom:1px solid var(--gold)}
a.flink:hover{background:var(--sel)}
a.flink code{color:var(--goldink);border-color:var(--gold)}
.cpw{display:inline}
.cpbtn{font:inherit;font-size:.66rem;font-weight:700;letter-spacing:.03em;margin:0 0 0 .3em;
  padding:.02em .38em;border:1px solid var(--line);border-radius:4px;background:var(--panel);
  color:var(--mut);cursor:pointer;vertical-align:.08em}
.cpbtn:hover{border-color:var(--gold);color:var(--ink)}

.cpw.fcopy{display:block;margin:1.05em 0}
.cpw.fcopy>.cpbtn{display:block;margin:0 0 .3rem auto}
.cpw.fcopy>pre{margin:0}

a.dbtn{text-decoration:none}
.dbtn.rgap{margin-top:.6rem}

/* ---- the read-only file page ---- */
body.filepage{display:block}
body.filepage .top{position:sticky}
body.filepage .brand{text-decoration:none;color:var(--ink)}
.fpath{flex:1 1 12rem;min-width:0}
.fpath code{font-size:.8rem}
.filepane{padding:1.2rem 1.6rem 5rem;overflow:visible}
.fileview{max-width:62rem}
.fileraw{font-size:.95rem;max-width:100%}

.tdir>.tkids{display:none;margin-left:.55rem;border-left:1px solid var(--line)}
.tdir.open>.tkids{display:block}
.tsearching .tdir>.tkids{display:block}
.tog{display:flex;gap:.35rem;align-items:center;width:100%;text-align:left;font:inherit;
  font-size:.78rem;font-weight:650;padding:.28rem .35rem;border:0;background:none;
  color:var(--ink);cursor:pointer}
.tog:hover{background:var(--wash)}
.tog .car{flex:none;width:.7rem;height:.7rem;position:relative}
.tog .car::before{content:"";position:absolute;left:.12rem;top:.14rem;border:.28rem solid transparent;
  border-left-color:var(--mut);border-right-width:0;transition:transform .12s}
.tdir.open>.tog .car::before{transform:rotate(90deg)}
.tname{overflow-wrap:anywhere}
.ndpath{display:block;font-size:.66rem;color:var(--faint);overflow-wrap:anywhere;line-height:1.3}
.ndempty{padding:.4rem .7rem}
.tkids .ebtn{padding-left:.6rem}

.frrow{display:flex;align-items:center;gap:.25rem;flex-wrap:wrap;border-left:3px solid transparent}
.frrow:has(.ebtn.hid){display:none}
.frrow .ebtn{flex:1 1 12rem;width:auto;min-width:0}
.frchk{flex:none;width:1.25rem;height:1.25rem;margin:0 0 0 .2rem;padding:0;border:2px solid var(--mut);
  border-radius:4px;background:none;cursor:pointer;display:inline-flex;align-items:center;
  justify-content:center;font:inherit;font-size:.85rem;font-weight:800;line-height:1;color:#fff}
.frchk:hover:not([disabled]){border-color:var(--ink)}
.frchk[aria-checked="true"]{background:#2e7d32;border-color:#2e7d32}
.frchk[disabled]{opacity:.35;cursor:default}
.frdone{border-left-color:#2e7d32}
.frgrey{color:var(--faint);font-size:.78rem;padding:.3rem .4rem;opacity:.8}
.frgrey .frlab{font-weight:650}
.frrev{font-size:.7rem;margin-right:.4rem}
.frnewer{font-size:.62rem;font-weight:700;color:var(--hold);border:1px solid var(--hold);
  border-radius:999px;padding:0 .35rem}
.frpend{padding:.4rem .7rem;font-size:.78rem}
.kb{flex:none;align-self:center;font-size:.62rem;font-weight:800;letter-spacing:.03em;
  padding:.02rem .3rem;border-radius:4px;border:1px solid var(--line);color:var(--mut);
  background:var(--wash)}
.kb.k-draft{color:var(--info);background:var(--infobg);border-color:var(--info)}
.kb.k-rewrite{color:#241d0c;background:var(--gold);border-color:var(--gold)}
.kb.k-log,.kb.k-note{color:var(--faint)}
.sbadge{display:inline-block;margin-left:.35rem;font-size:.6rem;font-weight:700;
  padding:0 .35rem;border:1px solid var(--line);border-radius:999px;color:var(--mut)}
.sbadge.sb-same{border-style:dashed}
.sv-title .kb{font-size:.68rem;vertical-align:.15em}
.ebtn.sub{padding-left:1.35rem}
.shead{margin:.2rem 0 1.1rem;padding-bottom:.35rem;border-bottom:2px solid var(--gold);
  font-size:.95rem;letter-spacing:.02em}
.shead .cnt{color:var(--faint);font-weight:400;font-size:.82rem}
.sintro{max-width:var(--measure);color:var(--mut);font-size:.88rem;line-height:1.6}
.sintro p{margin:0 0 .8em}
.libsync{font-size:.88rem;color:var(--mut);margin:0 0 .8em}
.slink{font:inherit;background:none;border:0;padding:0;color:var(--goldink);cursor:pointer;
  text-align:left;border-bottom:1px solid var(--gold)}
.slink:hover{background:var(--sel)}
.snone{color:var(--faint);font-style:italic}
.slogs{color:var(--mut);font-size:.84rem}

.snote{display:block;font-size:.68rem;line-height:1.35;color:var(--faint);font-style:italic;
  overflow-wrap:anywhere;cursor:pointer}
.snote:hover{color:var(--goldink);text-decoration:underline}
/* Provenance: authored date and model under each name. One short line in a small monospace
   size so the rail's two-line title clamp is untouched; "unknown" is italic and fainter, so it
   never reads as a value. */
.sp-col{flex:1;min-width:0;display:block}
.sidtag{display:block;font-size:.66rem;color:var(--faint);font-family:ui-monospace,Consolas,monospace}
.sp-chip{display:block;font-size:.68rem;line-height:1.35;color:var(--mut);white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis;font-family:ui-monospace,Menlo,Consolas,monospace}
.slink+.sp-chip{margin:.05rem 0 .25rem}
.sp-dot{color:var(--faint)}
.sp-unk{color:var(--faint);font-style:italic;font-family:inherit}

/* ---- SCRIPTS: one file ---- */
.sv{max-width:var(--measure)}
.sv-crumb{font-size:.72rem;text-transform:uppercase;letter-spacing:.07em;color:var(--faint);
  font-weight:700}
.sv-title{font-size:1.15rem;margin:.2rem 0 .35rem;line-height:1.3}
.sv-rel code{font-size:.74rem}
.sp-line{margin:.45rem 0 0;font-size:.82rem;color:var(--mut);line-height:1.5}
.sp-line .sp-date,.sp-line .sp-time,.sp-line .sp-model,.sp-line .sp-by{color:var(--ink);font-weight:600}
.sp-line .sp-unk{font-weight:400}
.sp-src{color:var(--faint)}
.sp-sep{color:var(--faint)}
.se-what .sp-line{margin:.2rem 0 0;font-size:.76rem}
.sv-pair{margin:.6rem 0;font-size:.86rem;color:var(--mut)}
.sv-acts{margin:.9rem 0 1.4rem;align-items:center}
.sv-hint{color:var(--faint);font-size:.8rem}
.sv-compare{margin:0 0 1.4rem}
.sv-cmp-h{display:flex;gap:.5rem;align-items:center;flex-wrap:wrap;margin:0 0 .5rem;
  font-size:.86rem}
.se-plus{color:var(--done);font-weight:700}
.se-minus{color:var(--open);font-weight:700}

td .cv-open{margin:.3rem 0 0}
.cv-intro{color:var(--mut);font-size:.86rem;line-height:1.55;margin:.4rem 0 .6rem}
.cv-tools{display:flex;gap:.4rem;flex-wrap:wrap;margin:.3rem 0 1rem}
.cv-hdr{margin:0 0 1rem;font-size:.84rem}
.cv-hdr>summary{cursor:pointer;color:var(--mut);padding:.2rem 0}
.cv-overall{margin:0 0 1.2rem}
.cv-chg{border:1px solid var(--line);border-radius:8px;background:var(--panel);
  padding:.55rem .8rem .7rem;margin:0 0 .9rem}
.cv-chg.cv-stale{border-style:dashed}
.cv-h{font-size:.72rem;font-weight:700;letter-spacing:.05em;text-transform:uppercase;
  color:var(--faint);margin:0 0 .35rem}
.cv-when{text-transform:none;letter-spacing:0;font-weight:400}
.cv-side{display:grid;grid-template-columns:4rem minmax(0,1fr);gap:.5rem;padding:.3rem 0;
  font-size:.92rem;line-height:1.65}
.cv-side+.cv-side{border-top:1px dashed var(--line2)}
.cv-tag{font-size:.64rem;font-weight:800;letter-spacing:.06em;text-transform:uppercase;
  padding-top:.3rem}
.cv-a .cv-tag{color:var(--open)}
.cv-b .cv-tag{color:var(--done)}
.cv-t{white-space:pre-wrap;overflow-wrap:anywhere;color:var(--ink)}
.cv del{background:var(--openbg);color:var(--open);text-decoration:line-through;
  box-shadow:inset 0 -2px 0 var(--open)}
.cv ins{background:var(--donebg);color:var(--done);text-decoration:none;font-weight:700;
  box-shadow:inset 0 -2px 0 var(--done)}
.cv-pause .cv-t,.cv-same-l.cv-pause{color:var(--faint);font-style:italic;font-size:.86em}
.cv-same{margin:0 0 .9rem;font-size:.88rem}
.cv-same>summary{cursor:pointer;color:var(--faint);font-size:.76rem;padding:.2rem .5rem;
  background:var(--wash);border-radius:6px}
.cv-same-l{padding:.25rem .6rem;color:var(--mut);line-height:1.6;white-space:pre-wrap;
  overflow-wrap:anywhere}
.cv-why{margin:.55rem 0 0}
.cv-whyl{display:block;font-size:.78rem;font-weight:700;color:var(--goldink);margin:0 0 .2rem}
.cv-hint{font-weight:400;color:var(--faint);font-size:.74rem}
.cv-ta{display:block;width:100%;box-sizing:border-box;font:inherit;font-size:.9rem;
  line-height:1.5;padding:.45rem .6rem;border:1px solid var(--line);border-radius:6px;
  background:var(--bg);color:var(--ink);resize:vertical;min-height:2.8rem}
.cv-ta:focus{outline:2px solid var(--gold);outline-offset:1px}
.cv-ta.cv-dirty{border-color:var(--hold);box-shadow:inset 3px 0 0 var(--hold)}
.cv-row{display:flex;gap:.5rem;align-items:center;flex-wrap:wrap;margin:.3rem 0 0}
.cv-st{font-size:.78rem;color:var(--mut)}
.cv-sh{margin:1.6rem 0 .3rem;font-size:.9rem}

/* ---- SCRIPTS: the editor ----
   Source on the left, the rendered preview (or the changes) on the right. At the reader's
   normal width the pane is the right-hand column only, so "Wide" folds the two rails away. */
body.wide .app{grid-template-columns:minmax(0,1fr)}
body.wide .rail{display:none}
.se{display:flex;flex-direction:column;gap:.7rem}

.se-bar{display:flex;flex-wrap:wrap;gap:.6rem;align-items:center;justify-content:space-between;
  position:sticky;top:calc(-1 * var(--pane-pt));z-index:6;background:var(--bg);
  padding:.6rem 0 .6rem;
  border-bottom:1px solid var(--line)}
.se-what{font-size:.86rem;min-width:0}
.se-dest{color:var(--mut);font-size:.8rem}
.se-btns{display:flex;gap:.4rem;align-items:center;flex-wrap:wrap}
.se-count{color:var(--mut);font-size:.78rem;font-family:ui-monospace,Menlo,Consolas,monospace}
.se-banner{border:1px solid var(--hold);background:var(--holdbg);color:var(--ink);
  border-radius:8px;padding:.55rem .8rem;font-size:.84rem}
.se-banner .nbtn{margin:0 0 0 .4rem}
.kb.k-approved{color:var(--done);border-color:var(--done);background:var(--wash);
  font-size:.74rem;padding:.2rem .5rem}

.kb.k-final{color:var(--goldink);border:3px double var(--gold);background:var(--sel);
  font-size:.74rem;padding:.12rem .5rem;text-transform:uppercase;letter-spacing:.08em}
.ap-ov{position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:1000;display:flex;
  align-items:center;justify-content:center;padding:1rem}
.ap-box{background:var(--panel);color:var(--ink);border:1px solid var(--line);
  border-radius:10px;padding:1.1rem 1.3rem;max-width:30rem;width:100%;
  box-shadow:0 12px 40px rgba(0,0,0,.4)}
.ap-box h3{margin:0 0 .6rem;font-size:1.05rem}
.ap-body p{margin:.4rem 0;font-size:.9rem;line-height:1.45}
.ap-body{max-height:62vh;overflow:auto}
.sv-rvnote{margin:.3rem 0 .2rem;font-size:.85rem;color:var(--muted,inherit)}
.sv-rvnote:empty{display:none}
.sv-notes{margin:.5rem 0;border:1px solid var(--line,#ccc);border-radius:6px;padding:.3rem .6rem}
.sv-notes>summary{cursor:pointer;font-weight:600}
.sv-note{margin:.4rem 0;padding:.3rem .5rem;border-left:3px solid var(--line,#ccc);white-space:pre-wrap;overflow-wrap:anywhere}
.sv-note .sv-nwhen{display:block;font-size:.75rem;color:var(--muted,inherit)}
.sv-notes textarea{width:100%;box-sizing:border-box;min-height:4.5rem;font:inherit}
.ap-no{color:var(--hold);font-weight:600}
.ap-btns{display:flex;gap:.5rem;justify-content:flex-end;margin-top:1rem}
.ap-btns .nbtn{font-size:.85rem;padding:.4rem .9rem}
.se-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:1.1rem;
  align-items:start}
@media(max-width:1180px){.se-grid{grid-template-columns:minmax(0,1fr)}}
.se-left,.se-right{min-width:0}
/* ⭐ THE SOURCE IS TWO LAYERS: a plain textarea on top, with its text made transparent, and a
   highlighted copy of the same text directly behind it (.se-hl). The textarea still does all
   the editing - caret, selection, undo, IME, Ctrl+S - and its value is the only thing saved;
   the copy behind it exists only to be coloured. ⛔ EVERY PROPERTY THAT DECIDES WHERE A
   CHARACTER LANDS IS IN THE SHARED RULE BELOW, so the two cannot wrap differently.
   scrollbar-gutter:stable gives both the same text width whether or not there is a scrollbar. */
.se-edw{position:relative}
.se-src,.se-hl{margin:0;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  font-size:.86rem;font-weight:400;font-style:normal;line-height:1.7;letter-spacing:normal;
  word-spacing:normal;text-transform:none;text-indent:0;padding:.8rem .95rem;
  border:1px solid var(--line);border-radius:8px;tab-size:4;white-space:pre-wrap;
  overflow-wrap:anywhere;word-break:normal;scrollbar-gutter:stable}
.se-src{display:block;position:relative;z-index:1;width:100%;min-height:72vh;resize:vertical;
  background:transparent;color:transparent;caret-color:var(--ink)}
.se-src::selection{color:transparent;background:rgba(207,172,109,.38)}
.se-hl{position:absolute;inset:0;overflow:hidden;pointer-events:none;border-color:transparent;
  background:var(--panel);color:var(--ink)}
.se-src:focus{outline:2px solid var(--gold);outline-offset:1px}
/* Dropdown groups, in the source and in the rendered script alike. */
.dd-mk{color:var(--ddmark);font-weight:700}
.dd-op{color:var(--ddopt)}
/* {{{ Notes }}}, brackets included. After the dropdown rules so a dropdown inside a note
   takes the note's colour. */
.pn{color:var(--pnote);background:var(--pnotebg)}

:root[data-pn]{--pnote:var(--ink);--pnotebg:var(--pn-user)}
@media(prefers-color-scheme:dark){:root[data-pn]:not([data-theme="light"]){--pnote:var(--pn-user);--pnotebg:transparent}}
:root[data-pn][data-theme="dark"]{--pnote:var(--pn-user);--pnotebg:transparent}
/* The box itself: a plain row, the first thing in the pane on every Scripts view. */
.pnbox{display:flex;flex-wrap:wrap;align-items:center;gap:.4rem .6rem;margin:0 0 .9rem;
  padding:.4rem .7rem;border:1px solid var(--line);border-radius:8px;background:var(--panel);
  font-size:.82rem;color:var(--mut)}
.pnbox-l{font-weight:650;color:var(--ink)}
.pnbox-in{width:7.4rem;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:.84rem;
  color:var(--ink);background:var(--bg);border:1px solid var(--line);border-radius:6px;
  padding:.28rem .5rem}
.pnbox-in.bad{border-color:var(--open)}
.pnbox .nbtn{margin:0}
.pnbox-say{font-size:.8rem;color:var(--mut)}
.pnbox-say:empty{display:none}
.pnbox-say.bad{color:var(--open);font-weight:650}
.pnbox-say.ok{color:var(--done)}
/* Stacked columns: the preview is BELOW the source, so lock-step has nothing to keep in view.
   The button is hidden and the script also refuses to sync at this width. */
@media(max-width:1180px){.se-btns .se-lockbtn{display:none}}
.se-tabs{display:flex;gap:.3rem;align-items:center;flex-wrap:wrap;margin:0 0 .55rem}
.se-basel{color:var(--faint);font-size:.76rem;margin:0 0 0 .3rem}
.se-base{font:inherit;font-size:.76rem;max-width:100%;background:var(--bg);color:var(--mut);
  border:1px solid var(--line);border-radius:6px;padding:.1rem .25rem}
.se-prev{border:1px solid var(--line2);border-radius:8px;padding:.8rem 1rem;
  background:var(--panel);max-height:78vh;overflow:auto}
.se-diff{max-height:78vh;overflow:auto}
.se-receipt{border:1px solid var(--line);border-left:3px solid var(--done);border-radius:0 8px 8px 0;
  padding:.5rem .85rem;background:var(--panel);font-size:.84rem;line-height:1.55}
.se-receipt p{margin:.25em 0}
.se-receipt pre{white-space:pre-wrap;font-size:.74rem;max-height:18rem;overflow:auto}
.se-yes{color:var(--done);font-weight:700}
.se-no{color:var(--open);font-weight:700}
.se-wait{color:var(--hold);font-weight:700}

/* ---- the diff: additions and deletions, by line and then by word ---- */
.sd{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.8rem;
  line-height:1.6;border:1px solid var(--line);border-radius:8px;overflow:hidden;
  background:var(--panel)}
.sd-row{display:grid;grid-template-columns:2.7rem 2.7rem 1.2rem minmax(0,1fr)}
.sd-ln{color:var(--faint);text-align:right;padding:0 .4rem;user-select:none;
  border-right:1px solid var(--line2);font-size:.72rem}
.sd-m{text-align:center;color:var(--faint);user-select:none}
.sd-t{padding:0 .6rem 0 .2rem;white-space:pre-wrap;overflow-wrap:anywhere;color:var(--ink)}
.sd-del{background:var(--openbg)}
.sd-del .sd-m{color:var(--open);font-weight:800}
.sd-ins{background:var(--donebg)}
.sd-ins .sd-m{color:var(--done);font-weight:800}
.sd-mod{background:none}
.sd-del.sd-mod{box-shadow:inset 3px 0 0 var(--open)}
.sd-ins.sd-mod{box-shadow:inset 3px 0 0 var(--done)}
.sd del{background:var(--openbg);color:var(--open);text-decoration:line-through;
  box-shadow:inset 0 -2px 0 var(--open)}
.sd ins{background:var(--donebg);color:var(--done);text-decoration:none;font-weight:700;
  box-shadow:inset 0 -2px 0 var(--done)}
.sd-fold>summary{cursor:pointer;color:var(--faint);font-size:.74rem;padding:.15rem 0 .15rem 6.8rem;
  background:var(--wash);border-top:1px solid var(--line2);border-bottom:1px solid var(--line2)}
.sd-none{color:var(--faint);font-size:.86rem;padding:1rem 0}

.mob{display:none}
@media(max-width:820px){
  /* The page itself scrolls on a phone, so the shell is content-height and html/body must
     be too - a body pinned to 100% makes the sticky bar below stop sticking one screen in,
     which is exactly where it starts being useful. */
  html,body{height:auto}
  .app{grid-template-columns:1fr;height:auto;flex:none}
  .rail{border-right:0;border-bottom:1px solid var(--line);max-height:48vh}
  /* ⛔ THE HEADER SCROLLS AWAY HERE, AND THE COLUMN SWITCHER IS WHAT STAYS. Pinned, the
     header costs 30% of a phone screen at the largest text step - it is the search box and
     two settings groups, none of which you need while reading. The Docs/Entries/Reading
     switcher is the one control you do need, so it takes over the pin at top:0.
     ⛔ AND top:3.05rem WAS A GUESS AT THE HEADER'S HEIGHT - wrong before this change and
     175px wrong at the largest step, which would have parked this bar in the middle of it. */
  .top{position:static}
  .mob{display:flex;gap:.25rem;padding:.4rem .8rem;background:var(--panel);
    border-bottom:1px solid var(--line);position:sticky;top:0;z-index:30}
  /* the WRAPPER is what must be hidden - hiding the inner <nav> leaves the filter bar and
     the count sitting above the reading pane */
  body[data-col="docs"] .rail-mid,body[data-col="docs"] .pane,
  body[data-col="list"] .rail-docs,body[data-col="list"] .pane,
  body[data-col="read"] .rail-docs,body[data-col="read"] .rail-mid{display:none}
  .rail{max-height:none}
  .pane{--pane-pt:1rem;padding:var(--pane-pt) .9rem 5rem}
  /* THE WINDOW SCROLLS HERE, SO THE PANE MUST NOT. With overflow:auto the pane is the
     nearest scrolling ancestor, never scrolls, and the per-entry sticky note row sticks to
     nothing. The row pins just under the fixed column bar (.mob, about 50px). */
  .pane{overflow:visible}
  /* .docins and .se-bar are sticky at top:-1rem for the scrolling pane; with the window as scroller
     they would stick to the window under .mob (z 30) and be half cut off, so they scroll away
     here, and the note row pins under .mob alone */
  .docins,.se-bar{position:static}
  .notes.live>[data-note-tools]{top:var(--mob-h,3.2rem)}
  .notes.live>.note-edit{top:var(--mob-h,3.2rem)}
  .entry{scroll-margin-top:calc(var(--mob-h,3.2rem) + var(--docins-h,0rem) + 1rem)}
  .top{top:0}
  /* ⛔ THE HEADER IS THE THING BIGGER TYPE COSTS ON A PHONE. It already stacked here; at
     the largest text step it reached 30% of a 812px screen before the first entry. The
     "Text" and "Theme" words are what push each group onto its own row, and they are the
     most droppable thing in the header - each group carries the same words as its
     `aria-label`, so nothing is lost to a screen reader, and five ascending A's next to
     Auto/Light/Dark do not need naming to be understood. */
  .tlab{display:none}
}
"""


def _esc(s):
    return html.escape(s or "", quote=True)


def render_notes_html(it, xref=None):
    """
    ⛔ THE SECTION IS RENDERED EVEN WHEN IT IS EMPTY, and that is not a stray div. It is the
    element the write UI attaches to, so an entry with no notes yet still has somewhere for
    "Add a note" to appear. It carries the doc and the entry id because those are what the
    save has to name, and it carries nothing else the client could ask the server to trust:
    the server resolves the entry itself and never accepts prose from the page.
    """
    rows = []
    for n in it.get("notes") or ():
        # ⛔ THE KIND COMES FROM THE PARSED LINE, NOT FROM HOW IT WAS WRITTEN. An entry read
        # today may hold an instruction written by a session that no longer exists; the label
        # has to be a fact about the document rather than a memory of the request.
        instruct = (n.get("kind") or "NOTE").upper() == "INSTRUCTION"
        # The ack is written by the orchestrator at its own turn boundary, so this line is
        # evidence rather than a claim about what should have happened.
        seen = ""
        if instruct:
            seen = ('<span class="note-ack hit">%s</span>' % _esc(n["ack"])
                    if (n.get("ack") or "").startswith("deleted by ") else
                    '<span class="note-ack hit">seen by %s</span>' % _esc(n["ack"])
                    if n.get("ack") else
                    '<span class="note-ack wait">not acknowledged yet</span>')
        # \u26d4 A SEEN INSTRUCTION IS FINISHED BUSINESS AND COLLAPSES; AN UNSEEN ONE IS THE
        # ONLY THING ON THE PAGE STILL WANTING SOMETHING, SO IT STAYS OPEN.
        _fold = instruct and bool(n.get("ack"))
        # \u2b50 THE ID IS ON SCREEN, NOT ONLY IN `data-nid`. Same gap as an anchor that cannot
        # be clicked.
        head = ('<span class="note-id">%s</span><span class="note-who">%s</span>'
                '<span class="note-when">%s</span>%s'
                % (_esc(n["nid"]),
                   ("Instruction to %s from %s" % (_esc(it["doc"]), _esc(n["who"])))
                   if instruct else ("Note from %s" % _esc(n["who"])),
                   _esc(n["when"]), seen))
        # `.note-b` is white-space:pre-wrap, so the breaks and indentation draw as typed, and
        # the Edit box gets the same text back.
        _real = orchdoc.note_display(n)
        body = '<div class="note-b" data-raw="%s">%s</div>' % (
            _esc(_real), _note_body_html(_real, xref))
        # \u26d4 A SEEN NOTE IS ONE LINE. So the HEADER is the toggle, and the body and the
        # Edit/Delete row (added by noteWire) live inside what it opens.
        inner = (('<details class="note-fold"><summary class="note-h">%s'
                  '<span class="note-caret" aria-hidden="true">\u25b8</span></summary>%s'
                  '</details>') % (head, body)
                 if _fold else '<div class="note-h">%s</div>%s' % (head, body))
        rows.append('<div class="note%s" data-nid="%s" data-kind="%s">%s</div>'
                    % (" instruct" if instruct else "", _esc(n["nid"]),
                       "instruction" if instruct else "note", inner))
    return ('<section class="notes%s" data-notes-for="%s" data-notes-doc="%s" '
            'data-notes-id="%s">%s</section>'
            % ("" if rows else " empty", _esc(it["dom"]), _esc(it["doc"]), _esc(it["id"]),
               "".join(rows)))


#
# An attestation stack is a record written for the NEXT AGENT: who reviewed this, when, and on
# what evidence.
#
# \u26a0\ufe0f THE COUNT STAYS VISIBLE IN THE SUMMARY, so collapsing hides the TEXT and not
# the FACT. "17 attestations" still says the entry has been reviewed seventeen times, which is
# the part that means something at a glance.
_ATTEST_P = re.compile(
    r"(?s)(<p>(?:(?!</p>).)*?<strong>Attested-by:</strong>(?:(?!</p>).)*?</p>\s*)+")


def fold_attestations(html_text):
    """Wrap each run of rendered Attested-by paragraphs in a collapsed <details>.

    #: failure-value: html_text unchanged   # a run this cannot identify renders exactly as
    #                                         before. An unfolded attestation is verbose; a
    #                                         swallowed one is a lost record, so the fallback
    #                                         direction is deliberate.
    """
    if not html_text:
        return html_text

    def _wrap(m):
        block = m.group(0)
        n = block.count("<strong>Attested-by:</strong>")
        if n < 1:
            return block
        return ('<details class="attest"><summary>%d attestation%s</summary>%s</details>'
                % (n, "" if n == 1 else "s", block))

    return _ATTEST_P.sub(_wrap, html_text)


def plain_title(t):
    """A title with its markdown links shown as their text (a Do-today tag links its item)."""
    return re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t or "")


def render_header_controls(it):
    """Neither is drawn on an archived item; the Do-today button is not drawn on a §2.0 item, which
    is the list itself."""
    if it.get("archived") or it.get("today"):
        return ""
    import priority as _p
    d, i = _esc(it["doc"]), _esc(it["id"])
    out = []
    if not it.get("today"):
        tagged = it.get("today_tag") or ""
        out.append(
            '<button type="button" class="nbtn dt-btn%s" data-dotag="%s" data-doc="%s" '
            'data-id="%s" data-ntag="%s" data-text="%s" title="%s">%s</button>'
            % (" on" if tagged else "", "remove" if tagged else "add", d, i, _esc(tagged),
               _esc(today_tag_text(it["doc"], it["id"], it["title"][:100])),
               "On your Do today list. Click to take it off." if tagged
               else "Put this item on your Do today list.",
               "Remove from Do today" if tagged else "Do today"))
    out.append(
        '<select class="prio-sel" data-doc="%s" data-id="%s" aria-label="Set priority" '
        'title="Set this item\'s priority. NOW is class A, High B, Medium / Low / Hold for '
        'Review C, Don\'t do D (archives it).">'
        '<option value=""%s>Priority...</option>%s</select>'
        % (d, i, "" if it.get("prio_choice") else " selected",
           "".join('<option value="%s"%s>%s (%s)</option>'
                   % (_esc(c), " selected" if it.get("prio_choice") == c else "", _esc(c),
                      _p.CHOICE_CLASS[c]) for c in _p.CHOICES)))
    import reader_status as _rs
    if _rs.offer(it):
        for ch in ("done", "superseded"):
            out.append(
                '<button type="button" class="nbtn mk-btn" data-mark="%s" data-doc="%s" '
                'data-id="%s" title="Mark this item %s without asking a session to do it. '
                'You are asked to confirm first, and can undo it until the page is reloaded.">%s'
                "</button>" % (ch, d, i, _rs.LABEL_FOR[ch], _rs.LABEL_FOR[ch]))
    return '<span class="hdr-acts">%s</span>' % "".join(out)


def render_entry_html(it, show_doc=False, xref=None):
    """One entry, as it appears in the reading pane."""
    tags = []
    if it["plate"]:
        tags.append('<span class="tag plate">ON YOUR PLATE</span>')
        # \u2b50 WHY IT SITS WHERE IT SITS. An order nobody can explain stops being read
        # the first time it puts something second that the human thinks is first. Only on plate
        # items: a ranking reason on an entry that is not in the ranking is noise.
        if it.get("prio_mine"):
            tags.append('<span class="tag pnow">\u2b50 YOU MARKED THIS NOW</span>')
        elif it.get("prio_why"):
            tags.append('<span class="tag pwhy">%s</span>' % _esc(it["prio_why"]))
    if it["status"]:
        tags.append('<span class="tag %s">%s</span>' % (it["sclass"], _esc(it["status"])))
    else:
        tags.append('<span class="tag">no status field</span>')
    if it["owner"]:
        tags.append('<span class="tag">owner: %s</span>' % _esc(it["owner"]))
    if it["archived"]:
        tags.append('<span class="tag">archived</span>')
    tags.append('<span class="tag">%s</span>' % _esc(it["section"]))
    if show_doc:
        tags.insert(0, '<span class="tag">%s</span>' % _esc(it["doc"]))
    if it.get("today"):
        tags.insert(0, render_today_tags(it["today"]))
    if it.get("prio_choice"):
        tags.append('<span class="tag prio-%s" title="Priority class %s, set by you">'
                    "Priority: %s</span>"
                    % (_esc(it["prio_class"].lower()), _esc(it["prio_class"]),
                       _esc(it["prio_choice"])))
    tags.append(render_header_controls(it))
    title_html = _esc(plain_title(it["title"]) if it.get("today") or it["kind"] == "N"
                       else it["title"])

    return (
        '<article class="entry" id="%s" data-doc="%s" data-id="%s" data-status="%s" '
        'data-sclass="%s" data-owner="%s" data-plate="%d" data-kind="%s" '
        'data-today="%d"%s data-hay="%s">'
        '<h2 class="et"><span class="eid">%s</span> <span>%s</span></h2>'
        '<div class="meta">%s</div>'
        "%s%s"
        '<div class="md">%s</div>'
        "</article>"
        % (_esc(it["dom"]), _esc(it["doc"]), _esc(it["id"]), _esc(it["status"] or ""),
           it["sclass"], _esc(it["owner"]), 1 if it["plate"] else 0, _esc(it["kind"]),
           1 if it.get("today") else 0,
           # The server's decision that this is an instruction item; the page only reads it.
           (' data-ins-item="1" data-ins-count="%d"' % it.get("ins_count", 0))
           if it.get("ins_item") else "",
           _esc((it["id"] + " " + it["title"]).lower()),
           _esc(it["id"]), title_html or "<em>(untitled)</em>",
           "".join(tags), render_today_buttons(it), render_notes_html(it, xref),
           copy_fenced_blocks(fold_attestations(md_to_html(it["md"], xref)))))


def render_nav_item(it, show_doc=False):
    lead = ("%s %s" % (it["doc"], it["id"])) if show_doc else it["id"]
    # ⭐ A §2.0 item done today reads as done in the rail: green dot, greyed text (the human).
    done_today = bool(it.get("today") and it["today"]["done_today"])
    return (
        '<button type="button" class="ebtn%s" data-goto="%s" data-doc="%s" data-id="%s" '
        'data-status="%s" data-sclass="%s" data-owner="%s" data-plate="%d" '
        'data-kind="%s" data-today="%d" data-hay="%s">'
        '<span class="dot %s"></span><span class="eid">%s</span>'
        '<span class="etitle">%s</span>%s</button>'
        % (" donetoday" if done_today else "",
           _esc(it["dom"]), _esc(it["doc"]), _esc(it["id"]), _esc(it["status"] or ""),
           it["sclass"], _esc(it["owner"]), 1 if it["plate"] else 0, _esc(it["kind"]),
           1 if it.get("today") else 0,
           _esc((it["id"] + " " + it["title"]).lower()),
           "done" if done_today else it["sclass"], _esc(lead),
           _esc(plain_title(it["title"]) if it.get("today") or it["kind"] == "N" else it["title"]),
           '<span class="pin">YOU</span>' if it["plate"] else ""))


def render_links_block(model, xref=None):
    """(nav_html, body_html) for the doc's §1, or ("", "") when it has none.

    ⭐ COLLAPSED, AT THE TOP, AND NAMED IN THE RAIL. At the top so it is found without scrolling
    past 260 entries; collapsed so it does not push the first entry off the screen every time
    the doc opens; and a button at the head of the middle rail opens it and lands on it, so it
    is one click from anywhere in the doc.

    ⛔ A BUTTON, NOT AN `.ebtn`. The rail's filters hide `.ebtn`s that do not match, and the
    link section is not an entry - it must not vanish because "Open" is selected.
    """
    lk = model.get("links")
    if not lk:
        return "", ""
    dom = "links-%s" % model["slug"]
    inner = copy_fenced_blocks(md_to_html(lk["md"], _links_ctx(xref)))
    n = inner.count("<a ")
    sub = (' <span class="lsub">%s</span>' % _esc(lk["suffix"])) if lk.get("suffix") else ""
    nav = ('<button type="button" class="lbtn" data-links="%s" title="Open this doc\'s §1 '
           'Links and docs">§1 Links and docs<span class="cnt">%d link%s</span></button>'
           % (dom, n, "" if n == 1 else "s"))
    body = ('<details class="links" id="%s"><summary>§1 Links and docs%s '
            '<span class="cnt">&middot; %d link%s &middot; paths open read-only</span></summary>'
            '<div class="md">%s</div></details>'
            % (dom, sub, n, "" if n == 1 else "s",
               inner or '<p class="hint">The section is there and empty.</p>'))
    return nav, body


def render_status_legend():
    """
    One pill per row of STATUS_GROUPS, drawn with the same `.tag.<class>` rule an entry's own
    status pill uses, preceded by the gold ON YOUR PLATE pill (the "waiting on the human" mark,
    which is not a status). The hover text on a pill is the group's meaning plus the Status
    values that map to it, read from STATUS_CLASS. Display only: nothing here is written to
    a document.
    """
    members = {}
    for status, cls in STATUS_CLASS.items():
        members.setdefault(cls, []).append(status)
    pills = ['<span class="tag plate" title="%s">ON YOUR PLATE</span>'
             % _esc("Waiting on %s: an OPEN or BLOCKED item they own."
                    % orchdoc.human_name().title()),
             '<span class="lg-sep" aria-hidden="true"></span>']
    for cls, label, meaning in STATUS_GROUPS:
        names = members.get(cls)
        tip = meaning[:1].upper() + meaning[1:] + (
            ". Status: %s" % ", ".join(names) if names else ".")
        pills.append('<span class="tag %s" title="%s">%s</span>'
                     % (cls, _esc(tip), _esc(label)))
    return ('<div class="legend" role="group" aria-label="Status colors">'
            '<span class="lg-lab">Status colors</span>%s</div>' % "".join(pills))


def render_doc_view(model, xref=None):
    """The middle rail and reading pane for ONE doc. Returns (nav_html, body_html)."""
    nav, body = [], []
    lnav, lbody = render_links_block(model, xref)
    if lnav:
        nav.append(lnav)
        body.append(lbody)
    if model["unreadable"]:
        body.append(
            '<div class="note"><b>Cannot determine what is open in this doc.</b><br>'
            "No entry here is in a form the parser can read - an entry needs a "
            "<code>### &lt;ID&gt; - title</code> heading AND a <code>**Status:**</code> "
            "field. <b>This is NOT a claim that nothing is open.</b> "
            "Open the markdown file directly.</div>")
    if not model["entries"]:
        nav.append('<div class="count">0 entries the parser can read</div>')
        body.append('<div class="empty">No parseable entries in this document. '
                    "That is a statement about the parser, not about the document - "
                    "read the file itself at <code>%s</code>.</div>"
                    % _esc(os.path.basename(model["path"])))
        return "".join(nav), "".join(body)

    body.insert(0, render_status_legend())

    has_today = any(it.get("today") for it in model["entries"])
    form = render_today_form(None, fixed=model["slug"])
    if not has_today:
        body.append('<h2 class="grouphead keep">§2.0 Do today</h2>%s'
                    '<div class="empty today-empty">Nothing to do today in %s.</div>'
                    % (form, _esc(model["slug"])))
    cur = None
    for it in model["entries"]:
        if it["section"] != cur:
            cur = it["section"]
            nav.append('<div class="sec">%s</div>' % _esc(cur))
            is_today = bool(re.match(r"^\W*§\s*2\.0\b", cur))
            body.append('<h2 class="grouphead%s">%s</h2>%s'
                        % (" keep" if is_today else "", _esc(cur),
                           form if is_today and has_today else ""))
        nav.append(render_nav_item(it))
        body.append(render_entry_html(it, xref=xref))
    return "".join(nav), "".join(body)


def render_plate_view(index, xref=None):
    """⭐ EVERYTHING WAITING ON THE HUMAN, ACROSS EVERY DOC, IN ONE LIST.

    The reason this tool exists. Neither GitHub nor Obsidian can answer "what needs me"
    without opening eleven documents, so this is the landing view.
    """
    nav, body = [], []
    total = sum(d["plate_count"] for d in index)
    blind = [d["slug"] for d in index if d["unreadable"]]

    if blind:
        # ⛔ An empty plate from a doc the parser cannot read is NOT an empty plate. Saying so
        # is the "None open while items are open" failure, and it is worse than a wrong count
        # because it wears the authority of a generated number.
        body.append(
            '<div class="note"><b>%d document(s) could not be read for this list: %s.</b>'
            "<br>Their entries are not in a form the parser understands, so anything "
            "waiting on you inside them is <b>not counted here</b>. Open them directly."
            "</div>" % (len(blind), _esc(", ".join(blind))))

    body.append('<p class="hint">%d item%s across %d document%s. '
                "Generated from the same entries and the same owner test "
                "<code>orchdoc.py plate</code> uses.</p>"
                % (total, "" if total == 1 else "s",
                   sum(1 for d in index if d["plate_count"]),
                   "" if sum(1 for d in index if d["plate_count"]) == 1 else "s"))

    if not total:
        body.append('<div class="empty">Nothing is waiting on you.</div>')
    else:
        body.append(render_status_legend())

    for d in index:
        items = [i for i in d["entries"] if i["plate"]]
        if not items:
            continue
        # \u2b50 SAME ORDER AS THE ORCHDOC'S OWN PLATE, from the same key. A reader that
        # ranked by its own rule would be a second opinion about what the human should look at
        # first, and the two would disagree the moment either changed.
        items.sort(key=lambda i: i.get("prio_rank") or [9, 0, 0, i["id"]])
        nav.append('<div class="sec">%s &middot; %d</div>' % (_esc(d["slug"]), len(items)))
        body.append('<h2 class="grouphead">%s <span class="cnt">&middot; %d waiting</span>'
                    "</h2>" % (_esc(d["slug"]), len(items)))
        for it in items:
            nav.append(render_nav_item(it, show_doc=True))
            body.append(render_entry_html(it, show_doc=True, xref=xref))
    return "".join(nav), "".join(body)


TODAY_VIEW = "__today__"


def render_today_tags(st):
    """The schedule and today's state of a §2.0 item, as tags."""
    if st["done_today"]:
        state = '<span class="tag done">DONE TODAY</span>'
    elif st["overdue"]:
        state = '<span class="tag open">OVERDUE since %s</span>' % _esc(st["next_due"])
    elif st["due"]:
        state = '<span class="tag open">DUE TODAY</span>'
    else:
        state = '<span class="tag">next: %s</span>' % _esc(st["next_due"] or "-")
    rep = ('<span class="tag">one-off</span>' if st["rule"]["kind"] == "once"
           else '<span class="tag">\U0001f501 %s</span>' % _esc(st["label"]))
    return state + rep


def render_today_buttons(it):
    """Done today / Undo, and Stop for a routine. Rendered only on a live §2.0 item."""
    st = it.get("today")
    if not st:
        return ""
    d, i = _esc(it["doc"]), _esc(it["id"])
    b = ('<button type="button" class="nbtn" data-today-act="undo" data-doc="%s" data-id="%s">'
         "Undo today</button>" % (d, i)) if st["done_today"] else (
        '<button type="button" class="nbtn act" data-today-act="done" data-doc="%s" '
        'data-id="%s">%s</button>' % (d, i, "Done" if st["rule"]["kind"] == "once"
                                      else "Done today"))
    if st["rule"]["kind"] != "once":
        b += ('<button type="button" class="nbtn danger" data-today-act="stop" data-doc="%s" '
              'data-id="%s">Stop this routine</button>' % (d, i))
    return '<div class="today-acts">%s</div>' % b


def today_count(index):
    """Items due today (and not yet done) across every doc - the rail's badge."""
    return sum(1 for d in index for i in d["entries"]
               if i.get("today") and i["today"]["due"] and not i["today"]["done_today"])


def render_today_form(slugs, fixed=None):
    """The add form. `fixed` names the doc it writes to (a doc's own §2.0); otherwise a
    select offers every doc (the cross-doc Do today view)."""
    if fixed:
        where = '<input type="hidden" name="doc" value="%s">' % _esc(fixed)
    else:
        where = ('<select name="doc" title="Which orchestrator\'s OrchDoc it goes in">%s'
                 "</select>" % "".join('<option value="%s">%s</option>' % (_esc(x), _esc(x))
                                       for x in slugs))
    return (
        '<form class="today-add" onsubmit="return false">'
        '<input type="text" name="text" placeholder="Something to do today, or a routine" '
        'maxlength="%d" required>%s'
        '<select name="repeat" title="How often it repeats">'
        '<option value="no">Just once</option><option value="daily">Daily</option>'
        '<option value="every">Every N days</option><option value="weekly">Weekly</option>'
        '<option value="weekly on">Weekly on a day</option>'
        '<option value="monthly on">Monthly on a date</option></select>'
        '<input type="number" name="n" min="1" max="365" value="2" class="today-n" hidden '
        'title="Every how many days">'
        '<select name="wd" class="today-wd" hidden>%s</select>'
        '<input type="number" name="md" min="1" max="31" value="1" class="today-md" hidden '
        'title="Day of the month">'
        '<button type="submit" class="nbtn act">Add</button>'
        '<div class="today-say" aria-live="polite"></div></form>'
        % (orchdoc.NOTE_TEXT_LIMIT, where, "".join('<option value="%s">%s</option>' % (w, w.title())
                          for w in orchdoc.WEEKDAYS)))


def render_today_strip(index):
    """One compact row each, with the same Done / Undo / Stop buttons the Do-today view has. Drawn
    at the top of a doc's page and of the plate."""
    rows = [i for d in index for i in d["entries"] if i.get("today")]
    due = [i for i in rows if i["today"]["due"] and not i["today"]["done_today"]]
    done = [i for i in rows if i["today"]["done_today"]]
    later = [i for i in rows if not i["today"]["due"] and not i["today"]["done_today"]]
    due.sort(key=lambda i: (i["today"]["next_due"], i["doc"], i["id"]))
    later.sort(key=lambda i: (i["today"]["next_due"], i["doc"], i["id"]))

    def row(it):
        m = TODAY_TAG_RE.search(it["raw_title"] + "\n" + it["md"])
        goto = m.group(1) if m else "%s/%s" % (it["doc"], it["id"])
        return ('<div class="ts-row%s" data-doc="%s" data-id="%s">'
                '<a class="ts-link" href="#%s" title="Open it">%s %s</a>'
                '<span class="ts-title">%s</span>%s%s</div>'
                % (" done" if it["today"]["done_today"] else "", _esc(it["doc"]), _esc(it["id"]),
                   _esc(goto), _esc(it["doc"]), _esc(goto.split("/", 1)[1]),
                   _esc(plain_title(it["title"])), render_today_tags(it["today"]),
                   render_today_buttons(it)))
    body = "".join(row(i) for i in due + done + later)
    return ('<section class="today-strip" aria-label="Do today, every document">'
            '<div class="ts-head">Do today <span class="cnt">&middot; %d due &middot; %d done '
            "&middot; %d coming up</span></div>%s</section>"
            % (len(due), len(done), len(later),
               body or '<div class="empty">Nothing on Do today in any document.</div>'))


def render_today_view(index, xref=None):
    """Due first (overdue at the top), then what was done today, then the routines that come up
    later - and a form to add one."""
    nav, body = [], []
    rows = [i for d in index for i in d["entries"] if i.get("today")]
    due = [i for i in rows if i["today"]["due"] and not i["today"]["done_today"]]
    done = [i for i in rows if i["today"]["done_today"]]
    later = [i for i in rows if not i["today"]["due"] and not i["today"]["done_today"]]
    due.sort(key=lambda i: (i["today"]["next_due"], i["doc"], i["id"]))
    later.sort(key=lambda i: (i["today"]["next_due"], i["doc"], i["id"]))
    body.append(render_today_form([d["slug"] for d in index]))
    for label, group in (("Due today", due), ("Done today", done), ("Coming up", later)):
        if not group:
            continue
        nav.append('<div class="sec">%s &middot; %d</div>' % (label, len(group)))
        body.append('<h2 class="grouphead">%s <span class="cnt">&middot; %d</span></h2>'
                    % (label, len(group)))
        for it in group:
            nav.append(render_nav_item(it, show_doc=True))
            body.append(render_entry_html(it, show_doc=True, xref=xref))
    if not rows:
        body.append('<div class="empty">Nothing in any §2.0 yet. Add the first item above.</div>')
    return "".join(nav), "".join(body)


def doc_badge(d):
    """(class, text, hover text) of one document's badge in the left list. The one place it is
    decided: GET / renders it into the page and GET /doc-counts sends it to an open page."""
    n = d["plate_count"]
    if d["unreadable"]:
        return "warn", "?", "entries not parseable - count unknown"
    return ("zero" if not n else ""), str(n), "%d waiting on you" % n


# The server was right: its model cache follows each file's mtime and size, and the next GET /
# showed the new count. But opening a document repaints only the entry list, and a link or hash
# change is not a page load, so the document list kept the counts of whenever the page was
# loaded. GET /doc-counts gives the page every badge again, and DOC_COUNTS_JS asks for them;
# both use `doc_badge`, so they cannot disagree with GET /.
def rail_counts(index):
    """Every badge in the document list: {"docs": {slug: [class, text, hover text]},
    "plate": the Everything-waiting total, "today": the Do-today count}."""
    return {"docs": {d["slug"]: list(doc_badge(d)) for d in index},
            "plate": sum(d["plate_count"] for d in index),
            "today": today_count(index)}


def render_doc_rail(index, active, scripts=None):
    """`scripts` is the number of script folders, or None to leave the Scripts entry out -
    which is what a caller with no corpus (a test fixture, `--once`) gets."""
    # keeps the groups reading as groups.
    out = ['<button type="button" class="dbtn" data-doc="__plate__" aria-current="%s">'
           '<span class="nm">Everything waiting</span>'
           '<span class="badge %s">%d</span></button>'
           % ("true" if active == "__plate__" else "false",
              "zero" if not sum(d["plate_count"] for d in index) else "",
              sum(d["plate_count"] for d in index)),
           '<button type="button" class="dbtn" data-doc="%s" aria-current="%s">'
           '<span class="nm">Do today</span>'
           '<span class="badge %s" id="today-badge">%d</span></button>'
           % (TODAY_VIEW, "true" if active == TODAY_VIEW else "false",
              "zero" if not today_count(index) else "", today_count(index)),
           # A plain link, not a data-doc button: /done is its own page, not a view of a doc.
           '<a class="dbtn rgap" href="/done" title="What the orchestrators finished, newest first">'
           '<span class="nm">Completed work</span></a>',
           # The folders the user named in the `doc_folders` setting, listed for review. Absent
           # until they name one.
           ('<a class="dbtn" href="/documents" title="The Markdown and text files in the folders '
            'you chose">'
            '<span class="nm">Review documents</span></a>' if documents_folders() else ""),
           # (the two pieces above and below were one string before; the HTML is the same)
           # The #rule view: a data-doc button so it highlights like the others.
           ('<button type="button" class="dbtn" data-doc="rule" aria-current="%s" '
            'title="The script rule codes (AB9, CD7 ...) in full">'
            '<span class="nm">Writing Rules</span></button>'
            % ("true" if active == "rule" else "false") if RULECODES is not None else ""),
           '<div class="railhead">Documents</div>']
    for d in index:
        cls, label, title = doc_badge(d)
        out.append(
            '<button type="button" class="dbtn" data-doc="%s" aria-current="%s">'
            '<span class="nm">%s</span><span class="badge %s" title="%s">%s</span></button>'
            % (_esc(d["slug"]), "true" if active == d["slug"] else "false",
               _esc(d["slug"]), cls, title, label))
    if scripts is not None:
        out.append('<div class="railhead">Scripts</div>'
                   '<button type="button" class="dbtn" data-doc="%s" aria-current="%s">'
                   '<span class="nm">Read and edit scripts</span>'
                   '<span class="badge zero" title="%d scripts">%d</span></button>'
                   % (SCRIPTS_VIEW, "true" if active == SCRIPTS_VIEW else "false",
                      scripts, scripts))
    return "".join(out)


def owner_options(index):
    owners = sorted({i["owner"] for d in index for i in d["entries"] if i["owner"]},
                    key=lambda s: s.lower())
    return "".join('<option value="%s">%s</option>' % (_esc(o), _esc(o)) for o in owners)


# ==========================================================================================
# SCRIPTS - the list view. The disk half (discovery, ids, save, landing) is reader_scripts.py.
# ==========================================================================================

SCRIPTS_VIEW = "__scripts__"
_KB = {"draft": ("D", "agent draft"), "rewrite": ("R", "the human rewrite"),
       "log": ("L", "process log"), "note": ("N", "note")}


def _srel_key(relroot):
    """
    """
    k = SCRIPTS.rename_key(relroot or "")
    return ' data-snk="%s" data-srest="%s"' % (_esc(k[0]), _esc(k[1])) if k else ""


def _same_text_badge(f):
    """Every name is escaped.
    """
    st = f.get("same_text")
    if not st:
        return ""
    m = re.search(r"\bv\d+\b", st["label"], re.I)
    short = m.group(0) if m else st["label"]
    return ('<span class="sbadge sb-same" title="%s">same text as %s</span>'
            % (_esc("Same text as " + (st.get("file") or st["name"])), _esc(short)))


def _sid_span(f):
    """Omitted when the file name already starts with it.
    """
    sid = SCRIPTS.script_id_of(f.get("name"), f.get("folder"))
    if not sid or sid.lower() in (f.get("name") or "").lower():
        return ""
    return '<span class="sidtag">%s</span>' % _esc(sid)


def _script_btn(f, sub=False):
    k, label = _KB[f["kind"]]
    same = f.get("same") or []
    return ('<button type="button" class="ebtn%s" data-script="%s" data-hay="%s" data-srel="%s"%s '
            'title="%s - %s"><span class="kb k-%s">%s</span>'
            '<span class="sp-col"><span class="etitle">%s</span>%s%s%s%s</span></button>'
            # script's folder name finds every file of it. The names of the copies folded into
            # `#scripts/p/<path>` link names; the page maps it to this row's id.
            # (after data-hay: test_reader_rewrite_drive reads `data-script=.. data-hay=..`)
            % (" sub" if sub else "", _esc(f["id"]),
               _esc(" ".join(["%s/%s" % (f.get("folder") or "", f["name"])]
                             + [s["label"] for s in same]).lower()),
               _esc(f.get("relroot") or ""), _srel_key(f.get("relroot")),
               _esc(label), _esc(f["name"]), f["kind"], k, _esc(f["name"][:-3]),
               "".join(('<span class="sbadge sb-older" title="%s">%s</span>'
                        % (_esc("Older draft: " + f["older_draft"]), _esc(b))
                        if b == "older draft" and f.get("older_draft")
                        else '<span class="sbadge">%s</span>' % _esc(b))
                       for b in f.get("badges") or []) + _same_text_badge(f),
               render_prov_chip(f.get("prov")), _sid_span(f),
               # A copy with the same text is a note on the row it repeats; the note opens the
               # copy (any [data-script] opens its file).
               "".join('<span class="snote" data-script="%s" data-srel="%s"%s '
                       'title="Open this copy">%s</span>'
                       % (_esc(s["id"]), _esc(s.get("relroot") or ""),
                          _srel_key(s.get("relroot")),
                          _esc(SCRIPTS.SAME_NOTE % s["label"]))
                       for s in same)))


def _script_link(f):
    return ('<button type="button" class="slink" data-script="%s">%s</button>%s'
            % (_esc(f["id"]), _esc(f["name"][:-3]), render_prov_chip(f.get("prov"))))


# ---- PROVENANCE: when a script was authored and on which model (reader_scripts.Provenance
# works the values out; this only draws them). ⛔ EVERY VALUE IS ESCAPED - a model name and a
# "By" come from a header line in the file, which is text anyone writing a script controls.
# ⛔ "unknown" IS NEVER A BLANK, and it is muted so it cannot be mistaken for a real value.

def _prov_part(fld, cls):
    """One value as a span, with where it came from on hover - or a muted 'unknown'."""
    fld = fld or {}
    if not fld.get("value"):
        return ('<span class="sp-unk" title="%s">unknown</span>'
                % _esc(fld.get("title") or "not worked out for this file"))
    return ('<span class="%s" title="%s">%s</span>'
            % (cls, _esc(fld.get("title") or ""), _esc(fld["value"])))


def render_prov_chip(prov):
    """"""
    if not prov:
        return ""
    return ('<span class="sp-chip">%s<span class="sp-dot"> &middot; </span>%s</span>'
            % (_prov_part(prov.get("authored"), "sp-date"),
               _prov_part(prov.get("model"), "sp-model")))


def render_prov_line(prov):
    """'' for a process log.
    """
    if not prov:
        return ""
    a, m, c = (prov.get(k) or {} for k in ("authored", "model", "changed"))

    def src(fld):
        return (' <span class="sp-src">(%s)</span>' % _esc(fld["label"])
                if fld.get("value") and fld.get("label") else "")

    parts = ["Authored %s%s" % (_prov_part(a, "sp-date"), src(a)),
             "Model: %s%s" % (_prov_part(m, "sp-model"), src(m))]
    if prov.get("by"):
        # `Rewrite By: the human` for a rewrite: the label its own provenance line uses.
        parts.append('%s: <span class="sp-by" title="from the file\'s own provenance line">'
                     "%s</span>" % ("Rewrite By" if prov.get("rewrite") else "By",
                                    _esc(prov["by"])))
    # Local time, as the date is.
    when = (' <span class="sp-time" title="%s">%s</span>'
            % (_esc(c.get("title") or ""), _esc(c["time"]))
            if c.get("value") and c.get("time") else "")
    parts.append("Last changed %s%s%s" % (_prov_part(c, "sp-date"), when,
                                          src(c) if c.get("src") == "mtime" else ""))
    return '<div class="sp-line">%s</div>' % '<span class="sp-sep"> - </span>'.join(parts)


def render_scripts_view(listing, fr_marks=None, workspace=None):
    """(nav_html, body_html) for the Scripts list. Every file is listed exactly once in the
    rail, drafts each followed by the rewrite paired with them, so the pairing is visible
    before anything is opened."""
    nav, body = [], []
    tree = {"html": "", "kids": {}}
    folders = SCRIPTS.display_folders(listing["folders"])
    nfiles = sum(len(fo["files"]) + len(fo.get("folded") or []) for fo in folders)
    if not listing["exists"]:
        body.append('<div class="empty">No script corpus at <code>%s</code> in this '
                    "workspace.</div>" % _esc(listing["root_rel"]))
        return '<div class="count">no scripts found</div>', "".join(body)
    # ⛔ NOT `.grouphead`. `apply()` hides every `.grouphead` with no visible entry under it,
    # and this view has no entries - so the heading rendered and was then hidden on load.
    body.append('<h2 class="shead">Scripts <span class="cnt">&middot; %d script%s, %d '
                "files</span></h2>" % (len(folders), "" if len(folders) == 1 else "s", nfiles))
    body.append(
        '<div class="sintro">'
        "<p><b>Open any file to read it.</b> From a draft or a rewrite, <b>Edit</b> opens the "
        "markdown source beside a live preview and a list of your additions and deletions.</p>"
        "<p><b>Saving never changes an agent draft.</b> A save made while editing a draft goes "
        "to <i>your rewrite of it</i> - the existing one if there is one, otherwise a new "
        "<code>&lt;NAME&gt; v&lt;N&gt; THE HUMAN.md</code> beside it (N is one past the highest version). Saving over a rewrite first keeps "
        "a copy of what it said, in <code>.edit-history/</code> next to it. Nothing is ever "
        "deleted. Every save is then committed and pushed to <code>origin/main</code> on its "
        "own, and the editor says whether that worked.</p>"
        "<p><b>Compare</b> shows a draft and your rewrite of it change by change, with a box "
        "against each change for why you made it. Each note is saved on its own, beside the "
        "pair, and committed the same way.</p></div>")
    body.append('<div class="md">')
    for fo in folders:
        files = fo["files"]
        folded = fo.get("folded") or []
        by_id = {f["id"]: f for f in files + folded}
        # A folded copy is not a row, except a draft that a rewrite pairs with: its Compare
        # row stays, because the why-notes of that pair are kept under the copy's name.
        shown_ids = {f["id"] for f in files}
        drafts = [f for f in files + folded if f["kind"] == "draft"
                  and (f["id"] in shown_ids or f.get("exact") or f.get("variants"))]
        rewrites = [f for f in files if f["kind"] == "rewrite"]
        logs = [f for f in files if f["kind"] in ("log", "note")]
        newstyle = any(f.get("ver") is not None for f in files)
        paired = set()
        nav = []
        rows = []
        if newstyle:
            # one list, already in order (version, newest first; logs; notes; legacy names)
            nav.extend(_script_btn(f) for f in files)
        for d in drafts:
            if not newstyle:
                nav.append(_script_btn(d))
            mine = [by_id[x] for x in (d.get("exact") or []) + (d.get("variants") or [])
                    if x in by_id]
            for r in mine:
                if not newstyle:
                    nav.append(_script_btn(r, sub=True))
                paired.add(r["id"])
            cmp_btn = ('<button type="button" class="nbtn cv-open" data-script-cmp="%s" '
                       'title="The draft and your rewrite, change by change, with a box for '
                       'why you made each change">Compare</button>' % _esc(d["id"])
                       if d.get("pair") else "")
            rows.append("<tr><td>%s</td><td>%s%s</td></tr>" % (
                _script_link(d),
                "".join("<div>%s</div>" % _script_link(r) for r in mine)
                or '<span class="snone">no rewrite yet</span>', cmp_btn))
        for r in rewrites:
            if r["id"] in paired:
                continue
            if not newstyle:
                nav.append(_script_btn(r))
            rows.append('<tr><td><span class="snone">no draft here with that name</span></td>'
                        "<td>%s</td></tr>" % _script_link(r))
        for lg in logs:
            if not newstyle:
                nav.append(_script_btn(lg))
        node = tree
        for part in fo["name"].split("/"):
            node = node["kids"].setdefault(part, {"html": "", "kids": {}})
        node["html"] += "".join(nav)
        body.append('<h3>%s</h3>' % _esc(fo["name"]))
        if rows:
            body.append('<div class="tw"><table><thead><tr><th>Agent draft</th>'
                        "<th>the human rewrite</th></tr></thead><tbody>%s</tbody></table></div>"
                        % "".join(rows))
        if logs:
            body.append('<p class="slogs">Process log%s: %s</p>'
                        % ("" if len(logs) == 1 else "s",
                           " &middot; ".join(_script_link(x) for x in logs)))
    body.append("</div>")
    _add_new_drafts(tree, listing)
    if fr_marks is not None and workspace is not None:
        _add_full_rewrite(tree, listing, fr_marks, workspace)
    return render_scripts_tree(tree), "".join(body)


def _new_draft_btn(d):
    """One row of "0 - New Drafts": the same button the tree gives that file (so a click opens
    it the same way), with the script's title and, small and grey, its real folder path."""
    f = d["file"]
    return ('<button type="button" class="ebtn" data-script="%s" data-hay="%s" data-srel="%s"%s '
            'data-nd="1" title="Opens %s (in %s)"><span class="kb k-%s">%s</span>'
            '<span class="sp-col"><span class="etitle">%s</span>'
            '<span class="ndpath">%s</span>%s</span></button>'
            % (_esc(f["id"]),
               _esc(("%s/%s" % (f.get("folder") or "", f["name"])).lower()),
               _esc(f.get("relroot") or ""), _srel_key(f.get("relroot")),
               _esc(f["name"]), _esc(d["folder"]), f["kind"], _KB[f["kind"]][0],
               _esc(d["title"]), _esc(d["folder"]), _sid_span(f)))


def _add_new_drafts(tree, listing):
    """Nothing moves on disk; see reader_scripts.new_drafts for which scripts it lists.
    """
    items = SCRIPTS.new_drafts(listing["folders"])
    node = {"html": "".join(_new_draft_btn(d) for d in items)
            or '<div class="snone ndempty">No new drafts waiting for you.</div>',
            "kids": {}, "label": "%s (%d)" % (SCRIPTS.NEW_DRAFTS_NAME, len(items))}
    old = tree["kids"]
    tree["kids"] = {SCRIPTS.NEW_DRAFTS_NAME: node}
    tree["kids"].update(old)


def _fr_row(r):
    """One row of "00 - Full Rewrite Scripts": a reviewed toggle, the newest version (the same
    button the tree gives that file), and its review note. A script not filed yet is a grey
    line with a disabled toggle."""
    on = r["reviewed"]
    chk = ('<button type="button" class="frchk" role="checkbox" aria-checked="%s" '
           'data-fr-key="%s" data-fr-ver="%s"%s title="%s"><span class="frtick">%s</span>'
           '</button>'
           % ("true" if on else "false", _esc(r["key"]),
              "" if r["ver"] is None else int(r["ver"]),
              "" if r["filed"] else " disabled",
              "Reviewed - click to mark not reviewed" if on
              else "Not reviewed - click to mark reviewed",
              "&#10003;" if on else ""))
    if not r["filed"]:
        return ('<div class="frrow frgrey">%s<span class="frlab">%s</span> '
                '<span class="frtitle">%s</span> <span class="snone">not yet rewritten</span>'
                '</div>' % (chk, _esc(r["label"]), _esc(r["title"])))
    rv = r["review"]
    if rv is None:
        link = ""
    elif "rel" in rv:
        link = ('<a class="frrev" href="/file/%s" target="_blank" rel="noopener" '
                'title="Opens the review note in a new tab">review</a>'
                % _esc(urllib.parse.quote(rv["rel"])))
    else:
        link = ('<button type="button" class="slink frrev" data-script="%s" '
                'title="Opens the review note">review</button>' % _esc(rv["id"]))
    newer = ('<span class="frnewer" title="A newer version was filed after you marked this '
             'reviewed">newer than reviewed</span>' if r["newer"] else "")
    return '<div class="frrow%s">%s%s%s%s</div>' % (
        " frdone" if on else "", chk, _script_btn(r["file"]), link, newer)


def _add_full_rewrite(tree, listing, marks, workspace):
    """Nothing moves on disk. One sub-folder per source (FRW.SOURCES); a source with nothing yet is
    a note."""
    secs = FRW.build(listing, workspace, marks.load())
    kids, html_top, nr, nt = {}, [], 0, 0
    for sc in secs:
        nr += sc["reviewed"]
        nt += sc["total"]
        if sc["pending"]:
            html_top.append('<div class="snone frpend">%s</div>' % _esc(sc["pending"]))
            continue
        kids[sc["title"]] = {"html": "".join(_fr_row(r) for r in sc["rows"]), "kids": {},
                             "label": "%s (%d of %d reviewed)" % (
                                 sc["title"], sc["reviewed"], sc["total"]),
                             "fr": sc["id"], "frbase": sc["title"]}
    node = {"html": "".join(html_top), "kids": kids, "fr": "all", "frbase": FRW.LIST_NAME,
            "label": "%s (%d of %d reviewed)" % (FRW.LIST_NAME, nr, nt)}
    old = tree["kids"]
    tree["kids"] = {FRW.LIST_NAME: node}
    tree["kids"].update(old)


def render_scripts_tree(tree):
    """All folders render closed; the page script opens the ones remembered in this browser, and
    the first time opens the top level."""
    def walk(name, node, path, depth):
        kids = "".join(walk(k, v, path + "/" + k, depth + 1)
                       for k, v in node["kids"].items())
        fr = (' data-fr="%s" data-frbase="%s"' % (_esc(node["fr"]), _esc(node["frbase"]))
              if node.get("fr") else "")
        return ('<div class="tdir" data-tpath="%s" data-tdepth="%d"%s>'
                '<button type="button" class="tog" data-tog aria-expanded="false" '
                'title="Open or close this folder"><span class="car"></span>'
                '<span class="tname">%s</span></button>'
                '<div class="tkids">%s%s</div></div>'
                % (_esc(path), depth, fr, _esc(node.get("label") or name), node["html"], kids))
    return '<div class="stree">%s</div>' % "".join(
        walk(k, v, k, 0) for k, v in tree["kids"].items())


FILE_NOTE_JS = r"""
(function(){
var bar=document.getElementById("fnote"); if(!bar){return;}
var btn=bar.querySelector("button.fn-open"), box=bar.querySelector(".fn-box");
var ta=bar.querySelector("textarea"), send=bar.querySelector("button.fn-send");
var out=bar.querySelector(".fn-out");
btn.addEventListener("click",function(){ box.hidden=!box.hidden; if(!box.hidden){ta.focus();} });
send.addEventListener("click",function(){
  var t=ta.value.trim(); if(!t){out.textContent="Write something first.";return;}
  send.disabled=true; out.textContent="Sending...";
  fetch("/file-note",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({token:bar.getAttribute("data-token"),file:bar.getAttribute("data-file"),text:t})})
  .then(function(r){return r.json();}).then(function(j){
    send.disabled=false;
    if(j&&j.ok){ ta.value=""; out.textContent="Sent to "+(j.role||"the orchestrator")+
      (j.id?" as "+j.id:"")+". It will see this note."; }
    else{ out.textContent="Not sent: "+((j&&j.error)||"the reader refused it")+" Your text is kept."; }
  }).catch(function(e){ send.disabled=false; out.textContent="Not sent: "+e+" Your text is kept."; });
});
})();
"""

FILE_NOTE_CSS = r"""
#fnote{margin:1.2rem 1.6rem 4rem;max-width:62rem}
#fnote .fn-box{margin-top:.5rem}
#fnote textarea{width:100%;min-height:6rem;font:inherit}
#fnote .fn-out{margin-left:.6rem;color:var(--mut);font-size:.85rem}
"""


def render_file_page(relposix, text, kind, ctx=None, token=None):
    """A standalone page for one workspace file, opened in its own tab from §1.

    The file is read-only. With a `token` it also carries one small box, "Leave a note on
    this file", which posts to /file-note (see _file_note_post): the note goes into an
    OrchDoc entry that links the file, never into the file."""
    note = ""
    if token:
        note = ('<section id="fnote" data-token="%s" data-file="%s">'
                '<button type="button" class="fn-open">Leave a note on this file</button>'
                '<span class="fn-out"></span>'
                '<div class="fn-box" hidden><textarea aria-label="Your note on this file"></textarea>'
                '<button type="button" class="fn-send">Send note</button></div></section>'
                % (_esc(token), _esc(relposix)))
    if kind == "markdown":
        content = '<div class="md fileview">%s</div>' % copy_fenced_blocks(md_to_html(text, ctx))
    else:
        content = ('<pre class="fileraw"><code>%s</code></pre>' % html.escape(text))
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>%s - read-only</title><style>%s</style></head><body class=\"filepage\">"
        "<script>%s</script>"
        '<header class="top"><a class="brand" href="/" title="Back to the OrchDoc reader">'
        "Orch<b>Docs</b></a>"
        '<div class="fpath"><span class="cpw"><code>%s</code><button type="button" '
        'class="cpbtn" data-copy title="Copy this path">copy</button></span></div>'
        '<span class="tag">read-only</span></header>'
        '<main class="pane filepane">%s</main>%s'
        "<script>%s</script><script>%s</script><script>%s</script><script>%s</script></body></html>"
        % (_esc(relposix.rsplit("/", 1)[-1]), CSS + FILE_NOTE_CSS, THEME_BOOT_JS + SIZE_BOOT_JS,
           _esc(relposix), content, note, COPY_JS, THEME_WIRE_JS, SIZE_WIRE_JS, FILE_NOTE_JS))


DOCUMENTS_CSS = r"""
body.docspage{display:block}
body.docspage .brand{text-decoration:none;color:var(--ink)}
.docspane{padding:1rem 1.4rem 5rem;overflow:visible;max-width:60rem}
.docspane h2{font-size:1.05rem;margin:1.4rem 0 .3rem}
.docspane ul{list-style:none;padding:0;margin:.2rem 0}
.docspane li{padding:.28rem 0;border-bottom:1px solid var(--line,rgba(128,128,128,.25));
  display:flex;gap:.8rem;align-items:baseline}
.docspane li a{flex:1}
.docspane .dsub,.docspane .dwhen,.docspane .dnote{color:var(--mut);font-size:.8rem}
"""


def render_documents_page(workspace):
    """The /documents page: the files in the folders the user named, each linking to the
    read-only file page. ⛔ READ-ONLY, NO TOKEN, NO WRITE UI: the notes box writes into an
    OrchDoc entry, and a document in a folder has none (see reader_docs.py)."""
    names = documents_folders()

    def _ok(rel):
        p, _r, _k = resolve_viewable(rel, workspace)
        return p is not None
    got = DOCS.listing(workspace, names, accept=_ok) if (DOCS is not None and names) else \
        {"folders": []}
    parts = []
    if not names:
        parts.append(
            '<p class="dnote">No folders are chosen yet. Choose them with '
            '<code>python orchdoc_config.py set doc_folders proposals,reports</code> '
            "(folders inside your workspace, separated by commas), then reload this page.</p>")
    for f in got["folders"]:
        parts.append("<h2>%s</h2>" % _esc(f["name"]))
        if not f["ok"]:
            parts.append('<p class="dnote">Not listed: %s.</p>' % _esc(f["why"]))
            continue
        if not f["files"]:
            parts.append('<p class="dnote">No Markdown or text files in this folder.</p>')
            continue
        rows = []
        for r in f["files"]:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["mtime"]))
            sub = "" if r["dir"] in ("", ".") else '<span class="dsub">%s/</span>' % _esc(r["dir"])
            rows.append('<li><a href="%s" target="_blank" rel="noopener">%s</a>%s'
                        '<span class="dwhen">%s</span></li>'
                        % (_esc(file_href(r["rel"])), _esc(r["name"]), sub, _esc(when)))
        parts.append("<ul>%s</ul>" % "".join(rows))
        if f["more"]:
            parts.append('<p class="dnote">%d older file(s) are not listed.</p>' % f["more"])
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>Review documents</title><style>%s%s</style></head><body class=\"docspage\">"
        "<script>%s</script>"
        '<header class="top"><a class="brand" href="/" title="Back to the OrchDoc reader">'
        'Orch<b>Docs</b></a><span class="tag">review documents - read-only</span></header>'
        '<main class="pane docspane">%s</main>'
        "<script>%s</script><script>%s</script></body></html>"
        % (CSS, DOCUMENTS_CSS, THEME_BOOT_JS + SIZE_BOOT_JS, "".join(parts), THEME_WIRE_JS,
           SIZE_WIRE_JS))


# ==========================================================================================
# /done - COMPLETED WORK, from git history of the OrchDocs (done_feed.py). No model calls.
#
# ⛔ READ-ONLY AND NO LOGIN. The page carries no write token and no write UI, and it reads
# nothing but the OrchDocs - no email, no feedback text. Every string from a doc reaches the
# page through textContent, never innerHTML, so a doc cannot inject markup into it.
# ==========================================================================================

DONE_WINDOWS = ("6h", "24h", "48h", "7d")

DONE_CSS = r"""
body.donepage{display:block}
body.donepage .brand{text-decoration:none;color:var(--ink)}
.donepane{padding:1rem 1.4rem 5rem;overflow:visible;max-width:76rem}
.dn-bar{display:flex;flex-wrap:wrap;gap:.3rem;align-items:center;margin:.3rem 0}
.dn-bar .tlab{min-width:6.2rem}
.tbtn .n{opacity:.75;margin-left:.3rem;font-variant-numeric:tabular-nums}
.dn-stat{color:var(--mut);font-size:.8rem;margin:.6rem 0 .4rem}
.dn-stat.err{color:var(--err);font-weight:650}
.dn-day{font-size:1rem;margin:1.3rem 0 .2rem;padding-bottom:.25rem;border-bottom:1px solid var(--line)}
.dn-hour{font-size:.7rem;text-transform:uppercase;letter-spacing:.07em;color:var(--faint);
  font-weight:700;margin:.7rem 0 .15rem}
.dn-row{display:grid;grid-template-columns:3rem 2.8rem 4.6rem 4.8rem minmax(0,1fr);
  gap:.15rem .6rem;align-items:baseline;padding:.28rem .4rem;border-radius:6px}
.dn-row:hover{background:var(--wash)}
.dn-time{color:var(--mut);font-size:.8rem;font-variant-numeric:tabular-nums}
.dn-doc{color:var(--mut);font-size:.8rem;font-weight:650}
.dn-id{font-weight:700;text-decoration:none}
.dn-id:hover{text-decoration:underline}
.dn-kind{justify-self:start;font-size:.7rem;font-weight:700;padding:.05rem .42rem;
  border-radius:999px}
.k-ticked .dn-kind,.k-done .dn-kind{background:var(--donebg);color:var(--done)}
.k-resolved .dn-kind{background:var(--infobg);color:var(--info)}
.k-new .dn-kind{background:var(--holdbg);color:var(--hold)}
.dn-title{color:var(--faint);font-size:.78rem;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
.dn-text{overflow-wrap:anywhere}
.dn-empty{color:var(--mut);margin:1.5rem 0}
@media(max-width:640px){
  .donepane{padding:.8rem 1rem 4rem}
  .dn-row{grid-template-columns:auto auto auto minmax(0,1fr)}
  .dn-body{grid-column:1/-1}
}
"""

# ==========================================================================================
#
# fetch() has no time limit of its own. A request the server accepts and never answers stays
# open for as long as the page is, and code that waits for that answer before it asks again,
# or before it gives a button back, waits with it: the save indicator at the bottom of the
# page stopped changing until a reload (box 12 found the same in the count refresh). Each
# request whose answer something waits for goes through `fetchWithin(href, opt, ms, read)`:
# fetch, then `read(response)` (the JSON when no `read` is given), as ONE promise that settles
# within `ms`. At `ms` the request is aborted and the promise fails with "no answer within
# N s"; an answer that comes later is ignored. On both pages: GET / and GET /done.
#
# The limits, each well past the slowest answer measured or allowed for:
#   FETCH_POLL_MS   10 s  a status question asked every 1.5 to 5 s while a save is on its way
#                         (GET /landing, /landing?cid=, /script-landing/<job>). reader.log,
#   FETCH_LOAD_MS   60 s  a document, a script or the /done list. Slowest GET /doc/<slug> in
#                         the same log: 4.4 s.
#   FETCH_SAVE_MS   45 s  a save that carries an id: the server answers {"pending": true}
#                         after WRITER_ANSWER_WAIT (20 s), and a save cut off here is followed
#                         up with GET /landing?cid=, never reported as failed.
#   FETCH_WRITE_MS 660 s  any other write. The server stops a writer after WRITER_HARD_CAP
#                         (600 s), so it has answered by then if it can answer at all.
# test_reader_page_requests.py steps 5 to 8 hold requests for ever in a real browser.
# ==========================================================================================
FETCH_WITHIN_JS = r"""
var FETCH_POLL_MS=10000, FETCH_LOAD_MS=60000, FETCH_SAVE_MS=45000, FETCH_WRITE_MS=660000;
function fetchWithin(href,opt,ms,read){
  return new Promise(function(res,rej){
    var o=Object.assign({},opt||{}), done=false,
        ctl=window.AbortController?new AbortController():null;
    if(ctl){ o.signal=ctl.signal; }
    var t=setTimeout(function(){ if(done){ return; } done=true; if(ctl){ ctl.abort(); }
      rej(new Error("no answer within "+Math.round(ms/1000)+" s")); },ms);
    fetch(href,o).then(function(r){ return read?read(r):r.json(); })
      .then(function(v){ if(done){ return; } done=true; clearTimeout(t); res(v); },
            function(e){ if(done){ return; } done=true; clearTimeout(t); rej(e); });
  });
}
"""

DONE_JS = FETCH_WITHIN_JS + r"""
(function(){
var WINS=["6h","24h","48h","7d"], KINDS=["ticked","resolved","done","new"];

var DEFAULT_K=["ticked","resolved","done"];
var st=readHash(), data=null, busy=false, retry=null;
var listEl=document.getElementById("dn-list"), orchEl=document.getElementById("dn-orch"),
    kindEl=document.getElementById("dn-kind"), winEl=document.getElementById("dn-win"),
    statEl=document.getElementById("dn-stat");

/* The filters live in the URL hash, so a refresh, a reload or a link keeps them. */
function readHash(){
  var out={w:"48h",o:[],k:DEFAULT_K.slice()}, h=(location.hash||"").replace(/^#/,"");
  h.split("&").forEach(function(p){
    var i=p.indexOf("="); if(i<0){return;}
    var k=p.slice(0,i), v=""; try{v=decodeURIComponent(p.slice(i+1));}catch(e){return;}
    if(k==="w"&&WINS.indexOf(v)>=0){out.w=v;}
    if(k==="o"&&v){out.o=v.split(",").filter(function(x){return /^o\d+$/.test(x);});}
    /* "all" is written explicitly, because an absent k means the default, not every kind. */
    if(k==="k"&&v){out.k=v==="all"?[]:v.split(",").filter(function(x){return KINDS.indexOf(x)>=0;});}
  });
  return out;
}
function writeHash(){
  var p=["w="+st.w];
  if(st.o.length){p.push("o="+st.o.join(","));}
  p.push("k="+(st.k.length?st.k.join(","):"all"));
  history.replaceState(null,"","#"+p.join("&"));
}
function el(tag,cls,text){
  var e=document.createElement(tag); if(cls){e.className=cls;}
  if(text!==undefined&&text!==null){e.textContent=String(text);} return e;
}
function pad(n){return n<10?"0"+n:""+n;}
function dkey(t){var d=new Date(t*1000);return d.getFullYear()+"-"+pad(d.getMonth()+1)+"-"+pad(d.getDate());}
function dlab(t){return new Date(t*1000).toLocaleDateString(undefined,
  {weekday:"long",year:"numeric",month:"short",day:"numeric"});}
function hm(t){var d=new Date(t*1000);return pad(d.getHours())+":"+pad(d.getMinutes());}
function onum(s){return parseInt(s.slice(1),10)||0;}
function chip(label,n,pressed,attr,val){
  var b=el("button","tbtn",label); b.type="button";
  b.setAttribute("aria-pressed",pressed?"true":"false"); b.setAttribute(attr,val);
  if(n!==null){b.appendChild(el("span","n",n));} return b;
}
function passO(it){return !st.o.length||st.o.indexOf(it.doc)>=0;}
function passK(it){return !st.k.length||st.k.indexOf(it.kind)>=0;}

function renderBars(){
  winEl.textContent=""; winEl.appendChild(el("span","tlab","Window"));
  WINS.forEach(function(w){winEl.appendChild(chip(w,null,st.w===w,"data-w",w));});
  var items=data?data.items:[], oc={}, kc={};
  items.forEach(function(it){
    if(passK(it)){oc[it.doc]=(oc[it.doc]||0)+1;}
    if(passO(it)){kc[it.kind]=(kc[it.kind]||0)+1;}
  });
  var docs=Object.keys(oc); st.o.forEach(function(d){if(docs.indexOf(d)<0){docs.push(d);}});
  docs.sort(function(a,b){return onum(a)-onum(b);});
  orchEl.textContent=""; orchEl.appendChild(el("span","tlab","Orchestrator"));
  var tot=0; docs.forEach(function(d){tot+=oc[d]||0;});
  orchEl.appendChild(chip("All",tot,!st.o.length,"data-o",""));
  docs.forEach(function(d){orchEl.appendChild(chip(d,oc[d]||0,st.o.indexOf(d)>=0,"data-o",d));});
  kindEl.textContent=""; kindEl.appendChild(el("span","tlab","Kind"));
  var kt=0; KINDS.forEach(function(k){kt+=kc[k]||0;});
  kindEl.appendChild(chip("All",kt,!st.k.length,"data-k",""));
  KINDS.forEach(function(k){kindEl.appendChild(chip(k,kc[k]||0,st.k.indexOf(k)>=0,"data-k",k));});
}

function renderList(){
  listEl.textContent="";
  if(!data){return;}
  var rows=data.items.filter(function(it){return passO(it)&&passK(it);});
  if(!rows.length){listEl.appendChild(el("p","dn-empty",data.loading?
    "Still reading the OrchDoc history for this window. Items appear here as they are read.":
    "Nothing matches these filters in this window."));return;}
  var day=null, hour=null;
  rows.forEach(function(it){
    var dk=dkey(it.time), hk=dk+" "+new Date(it.time*1000).getHours();
    if(dk!==day){listEl.appendChild(el("h2","dn-day",dlab(it.time))); day=dk; hour=null;}
    if(hk!==hour){listEl.appendChild(el("div","dn-hour",pad(new Date(it.time*1000).getHours())+":00")); hour=hk;}
    var r=el("div","dn-row k-"+it.kind);
    var t=el("span","dn-time",hm(it.time)); t.title="commit "+String(it.commit||"").slice(0,10);
    r.appendChild(t);
    r.appendChild(el("span","dn-doc",it.doc));
    var a=el("a","dn-id",it.id);
    a.href="/#"+encodeURIComponent(it.doc)+"/"+encodeURIComponent(it.id);
    a.title="Open "+it.doc+" "+it.id+" in the reader";
    r.appendChild(a);
    r.appendChild(el("span","dn-kind",it.kind));
    var b=el("div","dn-body");
    if(it.title){var ti=el("div","dn-title",it.title); ti.title=it.title; b.appendChild(ti);}
    b.appendChild(el("div","dn-text",it.text));
    r.appendChild(b);
    listEl.appendChild(r);
  });
}

function render(){renderBars(); renderList();}

function load(){
  if(busy){return;} busy=true;
  var w=st.w;
  if(retry){clearTimeout(retry); retry=null;}
  if(!data){statEl.className="dn-stat"; statEl.textContent="Loading the last "+w+" ...";}
  
  fetchWithin("/done/data?w="+encodeURIComponent(w),{cache:"no-store"},FETCH_LOAD_MS)
    .then(function(d){
      busy=false;
      if(w!==st.w){load();return;}
      if(!d||!d.ok){throw new Error((d&&d.error)||"no data");}
      data=d; render();
      var s=d.items.length+" items before filters, from "+d.commits+" OrchDoc commits in the last "+w+
        " - "+d.ref+" @ "+String(d.head||"").slice(0,8)+" - refreshed "+
        new Date().toLocaleTimeString()+" - refreshes every 60 seconds";
      /* Still reading: say so, and ask again in 3 seconds rather than 60. */
      if(d.loading){s="Still loading: "+d.pending+" of "+d.commits+
        " commits not read yet, so this list is incomplete. "+s;
        retry=setTimeout(load,3000);}
      if(d.errors&&d.errors.length){s+=" - "+d.errors.length+" commit(s) could not be read: "+d.errors.join("; ");}
      statEl.className="dn-stat"+(d.errors&&d.errors.length?" err":"");
      statEl.textContent=s;
    })
    .catch(function(e){
      busy=false; statEl.className="dn-stat err";
      statEl.textContent="Could not refresh: "+e.message+(data?" - showing the last good load.":"");
    });
}

document.addEventListener("click",function(ev){
  var t=ev.target.closest&&ev.target.closest("[data-w],[data-o],[data-k]"); if(!t){return;}
  if(t.hasAttribute("data-w")){ var w=t.getAttribute("data-w");
    if(w!==st.w){st.w=w; data=null; writeHash(); render(); load();} return; }
  if(t.hasAttribute("data-o")){ var o=t.getAttribute("data-o");
    if(!o){st.o=[];} else { var i=st.o.indexOf(o); if(i>=0){st.o.splice(i,1);} else {st.o.push(o);} }
    writeHash(); render(); return; }
  if(t.hasAttribute("data-k")){ var k=t.getAttribute("data-k");
    if(!k){st.k=[];} else { var j=st.k.indexOf(k); if(j>=0){st.k.splice(j,1);} else {st.k.push(k);} }
    writeHash(); render(); return; }
});
window.addEventListener("hashchange",function(){
  var n=readHash(), reload=n.w!==st.w; st=n; if(reload){data=null;} render(); if(reload){load();}
});
writeHash(); render(); load();
setInterval(load,60000);
})();
"""


def render_done_page():
    """The /done shell. The rows arrive from /done/data, so the page itself holds no content
    and no write token."""
    themer = "".join(
        '<button type="button" class="tbtn" data-theme-set="%s" aria-pressed="false">%s'
        "</button>" % (v, lab)
        for v, lab in (("auto", "Auto"), ("light", "Light"), ("dark", "Dark")))
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>Completed work</title><style>%s%s</style></head><body class=\"donepage\">"
        "<script>%s</script>"
        '<header class="top"><a class="brand" href="/" title="Back to the OrchDoc reader">'
        "Orch<b>Docs</b></a><span class=\"tag\">completed work</span>"
        '<div class="grow"></div>'
        '<div class="themer" role="group" aria-label="Colour theme">'
        '<span class="tlab">Theme</span>%s</div></header>'
        '<main class="pane donepane">'
        '<div class="dn-bar" id="dn-win" role="group" aria-label="Window"></div>'
        '<div class="dn-bar" id="dn-orch" role="group" aria-label="Orchestrator"></div>'
        '<div class="dn-bar" id="dn-kind" role="group" aria-label="Kind"></div>'
        '<div class="dn-stat" id="dn-stat" aria-live="polite"></div>'
        '<div id="dn-list"></div></main>'
        "<script>%s</script><script>%s</script><script>%s</script></body></html>"
        % (CSS, DONE_CSS, THEME_BOOT_JS + SIZE_BOOT_JS, themer, DONE_JS, THEME_WIRE_JS,
           SIZE_WIRE_JS))


JS = r"""
(function(){
var cache={}, view="__plate__", cur=null, obs=null, spyHash=null;
var railE=document.getElementById("rail-entries"),
    pane=document.getElementById("pane"),
    railD=document.getElementById("rail-docs"),
    q=document.getElementById("q"),
    scopeAll=document.getElementById("scope-all"),
    ownerSel=document.getElementById("owner"),
    countEl=document.getElementById("count");
var filt="all";

function setCol(c){ if(window.matchMedia("(max-width:820px)").matches){
  document.body.setAttribute("data-col",c);} }
/* ⭐ THE SCRIPTS VIEW LIVES IN ITS OWN SCRIPT BLOCK and reaches in through these three, rather
   than being pasted into this closure. `readerForget` drops a cached view so a save that
   created a file shows up in the list. */
window.readerSetCol=setCol;
window.readerForget=function(v){ forget(v); };

/* ---- scrollspy: the rail highlights whatever is being read ---- */
function spy(){
  if(obs){obs.disconnect();}
  var seen={};
  obs=new IntersectionObserver(function(es){
    es.forEach(function(e){ seen[e.target.id]=e.isIntersecting?e.intersectionRatio:-1; });
    if(pin){return;}   /* a jump is settling - see jump() */
    var best=null,bv=-1;
    Object.keys(seen).forEach(function(k){ if(seen[k]>bv){bv=seen[k];best=k;} });
    if(best&&bv>0){mark(best);}
  },{root:pane,rootMargin:"-8% 0px -70% 0px",threshold:[0,.01,.25,1]});
  pane.querySelectorAll(".entry").forEach(function(n){obs.observe(n);});
}
function mark(dom){
  if(cur===dom){return;} cur=dom;
  railE.querySelectorAll(".ebtn.cur").forEach(function(b){b.classList.remove("cur");});
  var b=railE.querySelector('[data-goto="'+CSS.escape(dom)+'"]');
  if(b){ b.classList.add("cur");
    var br=b.getBoundingClientRect(), rr=railE.getBoundingClientRect();
    if(br.top<rr.top+30||br.bottom>rr.bottom-10){
      b.scrollIntoView({block:"center"});}
  }
  
  var e=document.getElementById(dom);
  if(e){ var d=e.getAttribute("data-doc"), i=e.getAttribute("data-id");
    history.replaceState(null,"","#"+d+"/"+i); spyHash=location.hash; savePlace(); }
}

/* ---- THE HUMAN'S NOTES: the only thing on this page that WRITES ----
   ⛔ EVERY SAVE GOES THROUGH `orchdoc.py note`, WHICH IS THE ONE WRITER OF AN ORCHDOC. The
   server shells out to it; nothing here or there hand-edits markdown. Eleven sessions write
   these files at once and the collisions from ad-hoc edits are what that rule exists for.

   ⛔ AND AFTER A SAVE THE ENTRY IS RE-FETCHED RATHER THAN PATCHED FROM WHAT WAS TYPED. A note
   shown as written that might not be in the file is the exact failure this feature cannot
   have. While a save is on its way (the outbox, o9cL1) the change is shown tagged "sending",
   which says it is not in the file yet; once it is, the entry is drawn from what the server
   just read off disk (GET /entry/), not the whole doc. */
function noteWire(root){
  if(!NOTE_TOKEN){return;}
  root.querySelectorAll(".notes").forEach(function(sec){
    if(sec.getAttribute("data-wired")){return;}
    sec.setAttribute("data-wired","1");
    sec.classList.add("live");
    sec.querySelectorAll(".note").forEach(noteActs);
    var row=document.createElement("div"); row.className="note-acts";
    row.setAttribute("data-note-tools","1");
    var add=document.createElement("button");
    add.type="button"; add.className="nbtn"; add.setAttribute("data-note-add","1");
    add.textContent=sec.querySelector(".note")?"Add another note":"Add a note";
    row.appendChild(add);
    
    var ins=document.createElement("button");
    ins.type="button"; ins.className="nbtn act"; ins.setAttribute("data-instruct-add","1");
    
    ins.textContent="Instruct "+sec.getAttribute("data-notes-doc");
    row.appendChild(ins);
    
    var ent=sec.closest(".entry");
    if(docInsItem(ent)){
      var live=sec.querySelector('.note[data-kind="instruction"]');
      var wd=document.createElement("button"); wd.type="button";
      wd.className="nbtn danger";
      wd.setAttribute("data-docins-del",live?live.getAttribute("data-nid"):"");
      wd.textContent="Delete this instruction";
      row.appendChild(wd);
    }
    sec.appendChild(row);
  });
}
function noteSection(el){ return el.closest?el.closest(".notes"):null; }
/* One note's own buttons. A note still in the outbox (data-ob, no id yet) gets none. */
function noteActs(n){
  if(!n.getAttribute("data-nid")){return;}
  var a=document.createElement("div"); a.className="note-acts";
  a.innerHTML='<button type="button" class="nbtn" data-note-edit>Edit</button>'+
    '<button type="button" class="nbtn danger" data-note-del>Delete</button>';
  // A seen note is collapsed to its header; its Edit/Delete belong inside the fold.
  (n.querySelector("details.note-fold")||n).appendChild(a);
  
  n.querySelectorAll(".note-img").forEach(function(im){
    var x=document.createElement("button"); x.type="button";
    x.className="nbtn danger"; x.setAttribute("data-img-del","1");
    x.textContent="Remove image"; im.insertAdjacentElement("afterend",x);
  });
}

function docInsItem(ent){
  return !!ent&&ent.getAttribute("data-ins-item")==="1";
}

function docInsConfirm(doc,n){
  if(n>1){
    return "Delete this instruction item and all "+n+" instructions on it? They are kept for "+
      "the record, each marked deleted, with a note saying so, and the W item moves to §99 as "+
      "\"Deleted with no action\". If "+doc+" has already been shown them, they may be acting "+
      "on them now - deleting them does not tell them to stop.";
  }
  return "Delete this instruction? It is kept for the record, marked deleted with a note "+
    "saying so, and its W item moves to §99 as \"Deleted with no action\". If "+doc+
    " has already been shown it, they may be acting on it now - deleting it does not tell "+
    "them to stop.";
}

function docInsWhy(why){
  return /(^|[\s(`])--[a-z]/.test(why||"")
    ? "the server would not delete this item as it now stands. Reload the page to see it as it is"
    : why;
}

var DK="claude-orchdoc-reader-draft|", orphans={};
function draftKey(kind,forId,nid){ return DK+kind+"|"+forId+"|"+(nid||""); }
function boxKind(b){ return b.classList.contains("instruct")?"instruct":"note"; }
function draftMeta(box){
  var sec=noteSection(box);
  if(!sec){return null;}
  return {kind:boxKind(box),forId:sec.getAttribute("data-notes-for")||"",
          doc:sec.getAttribute("data-notes-doc")||"",id:sec.getAttribute("data-notes-id")||"",
          nid:box.getAttribute("data-nid")||""};
}
function draftAll(){
  var out=[];
  try{ var ls=window.localStorage;
    for(var i=0;i<ls.length;i++){ var k=ls.key(i);
      if(k&&k.indexOf(DK)===0){
        try{ var v=JSON.parse(ls.getItem(k)); if(v&&v.text){ v.key=k; out.push(v); } }
        catch(e){} } }
  }catch(e){}
  return out.sort(function(a,b){ return (a.t||0)-(b.t||0); });
}
function draftGet(k){
  try{ var v=JSON.parse(window.localStorage.getItem(k)||"null"); if(v){v.key=k;} return v; }
  catch(e){ return null; }
}
function unsaved(box){
  var ta=box.querySelector("textarea");
  return !!ta&&!!ta.value.trim()&&ta.value!==(box._initial||"");
}
/* False only when the browser refused to store it. `pending` marks a save or send in flight,
   so a reset during it restores the text with a warning that it may already be in the entry. */
function draftPut(box,pending){
  var m=draftMeta(box), ta=box.querySelector("textarea"), ok=true;
  if(!m||!ta){return false;}
  var k=draftKey(m.kind,m.forId,m.nid), init=box._initial||"";
  try{
    if(!unsaved(box)){ window.localStorage.removeItem(k); }
    else{ m.text=ta.value; m.initial=init; m.t=Date.now(); m.pending=!!pending;
          
          m.cid=box._cid||""; m.cidText=box._cidText==null?"":box._cidText;
          window.localStorage.setItem(k,JSON.stringify(m)); }
  }catch(e){ ok=false; }
  draftBar();
  return ok;
}
function draftDel(box){
  var m=draftMeta(box);
  if(!m){return;}
  try{ window.localStorage.removeItem(draftKey(m.kind,m.forId,m.nid)); }catch(e){}
  draftBar();
}
function findBox(sec,kind,nid){
  var bs=sec.querySelectorAll(".note-edit");
  for(var i=0;i<bs.length;i++){
    if(bs[i].querySelector("textarea")&&boxKind(bs[i])===kind&&
       (bs[i].getAttribute("data-nid")||"")===(nid||"")){ return bs[i]; } }
  return null;
}
function secFor(root,forId){
  var all=root.querySelectorAll(".notes[data-notes-for]");
  for(var i=0;i<all.length;i++){
    if(all[i].getAttribute("data-notes-for")===forId){ return all[i]; } }
  return null;
}

function dropReceipts(sec){
  sec.querySelectorAll(".note-edit").forEach(function(b){
    if(!b.querySelector("textarea")){ b.parentNode.removeChild(b); } });
}
function draftRestore(root){
  if(!NOTE_TOKEN){return;}
  draftAll().forEach(function(d){
    var sec=secFor(root,d.forId);
    if(!sec||!sec.getAttribute("data-wired")){return;}
    var box=findBox(sec,d.kind,d.nid)||(d.kind==="instruct"?instructEditor(sec,true)
                                       :noteEditor(sec,d.nid,d.initial,true));
    var ta=box.querySelector("textarea");
    ta.value=d.text; box._initial=d.initial||"";
    if(d.cid){ box._cid=d.cid; box._cidText=d.cidText; }
    var act=d.kind==="instruct"?"sent":"saved";
    var msg=(d.nid&&!sec.querySelector('.note[data-nid="'+d.nid+'"]'))
      ? "Restored from this browser. The note you were editing is no longer on this entry, "+
        "so saving it will fail: copy your text, then Cancel."
      : d.pending
      ? "Restored from this browser. The page was reset while this was being "+act+", so it "+
        "may already be in the entry. Check the notes above before you "+
        (d.kind==="instruct"?"send":"save")+" it again."
      : "Restored from this browser: you typed this before the page was reset. It is not "+
        act+" yet.";
    var r=box.querySelector(".note-restored");
    if(!r){ r=document.createElement("div"); r.className="note-restored";
            box.insertBefore(r,ta); }
    r.textContent=msg;
    
    if(d.pending&&d.cid&&!(d.nid&&!sec.querySelector('.note[data-nid="'+d.nid+'"]'))){
      r.textContent="Restored from this browser. The page was reset while this was being "+act+
        "; checking whether it arrived...";
      nsCheckRestored(d);
    }
  });
  draftBar();
}
function draftLabel(d){
  return d.kind==="instruct"?"Instruction to "+d.doc+" on "+d.id
    : d.nid?"Edit of note "+d.nid+" on "+d.doc+" "+d.id : "Note on "+d.doc+" "+d.id;
}
function draftBar(){
  var bar=document.getElementById("draft-bar");
  if(!bar){ bar=document.createElement("div"); bar.id="draft-bar"; bar.hidden=true;
            document.body.appendChild(bar); }
  var away=NOTE_TOKEN?draftAll().filter(function(d){
    var s=secFor(pane,d.forId); return !(s&&findBox(s,d.kind,d.nid)); }):[];
  var h="";
  if(away.length){
    h='<div class="dh">Typed here, not saved or sent yet</div>';
    away.forEach(function(d){
      var k=esc(d.key).replace(/"/g,"&quot;"), when="";
      try{ when=new Date(d.t||0).toLocaleString([],{month:"short",day:"numeric",
             hour:"numeric",minute:"2-digit"}); }catch(e){}
      h+='<div class="di"><span class="dl">'+esc(draftLabel(d))+
         ' <span class="dt">'+esc(when)+'</span></span>'+
         '<button type="button" class="nbtn primary" data-draft-open="'+k+'">Open</button>'+
         '<button type="button" class="nbtn" data-draft-drop="'+k+'">Discard</button>'+
         (orphans[d.key]?'<div class="dg">'+esc(orphans[d.key])+'</div>'+
           '<textarea readonly>'+esc(d.text)+'</textarea>':'')+'</div>';
    });
  }
  if(bar._h===h){return;}
  bar._h=h; bar.innerHTML=h; bar.hidden=!h;
}
/* Open loads the entry's own doc (not the plate, which may no longer list it), lands on the
   entry, then moves down to the box once the entry has been laid out. */
function draftOpen(k){
  var d=draftGet(k);
  if(!d){ draftBar(); return; }
  load(d.doc,function(){
    var sec=secFor(pane,d.forId);
    if(!sec){
      orphans[k]=d.id+" is not in the "+d.doc+" document any more, so there is nowhere to "+
        "put this. Copy your text from here, then Discard.";
      barReset(); return; }
    delete orphans[k];
    jump(d.forId,0);
    window.setTimeout(function(){
      var box=findBox(sec,d.kind,d.nid), e=document.getElementById(d.forId);
      if(!box||!e){return;}
      jump(d.forId,Math.max(0,box.getBoundingClientRect().top-e.getBoundingClientRect().top-120));
      var ta=box.querySelector("textarea");
      try{ ta.focus({preventScroll:true}); }catch(x){ ta.focus(); }
    },150);
  },true);
}
function barReset(){ var b=document.getElementById("draft-bar"); if(b){b._h=null;} draftBar(); }
function draftDrop(k){
  var d=draftGet(k);
  if(!d){ barReset(); return; }
  var peek=d.text.length>160?d.text.slice(0,160)+"...":d.text;
  if(!window.confirm("Discard this text? It was never saved or sent, and it cannot be "+
                     "brought back.\n\n"+peek)){return;}
  try{ window.localStorage.removeItem(k); }catch(e){}
  delete orphans[k];
  var s=secFor(pane,d.forId), b=s&&findBox(s,d.kind,d.nid);
  if(b){ b.parentNode.removeChild(b); }
  barReset();
}

function noteEditor(sec,nid,initial,quiet){
  var had=findBox(sec,"note",nid);
  if(had){ if(!quiet){ had.querySelector("textarea").focus(); } return had; }
  dropReceipts(sec);
  var box=document.createElement("div");
  box.className="note-edit"; box.setAttribute("data-nid",nid||"");
  box._initial=initial||"";
  box.innerHTML=(nid?'<div class="ehead">Editing '+esc(nid)+'</div>':'')+
    '<textarea></textarea><div class="note-acts">'+
    '<button type="button" class="nbtn primary" data-note-save>Save</button>'+
    '<button type="button" class="nbtn" data-note-cancel>Cancel</button></div>'+
    '<div class="note-hint">Saved into the OrchDoc itself, so the orchestrator who owns '+
    'this entry sees it. Paragraphs, line breaks, lists and indentation are kept as you type them.</div>';
  var ta=box.querySelector("textarea");
  ta.value=initial||"";
  sec.insertBefore(box,sec.querySelector("[data-note-tools]"));
  if(!quiet){ ta.focus(); ta.setSelectionRange(ta.value.length,ta.value.length); }
  return box;
}

function instructEditor(sec,quiet){
  var had=findBox(sec,"instruct","");
  if(had){ if(!quiet){ had.querySelector("textarea").focus(); } return had; }
  dropReceipts(sec);
  var who=sec.getAttribute("data-notes-doc"), id=sec.getAttribute("data-notes-id");
  var box=document.createElement("div");
  box.className="note-edit instruct"; box.setAttribute("data-nid",""); box._initial="";
  box.innerHTML='<div class="ihead">Instruction to '+who+' on '+id+'</div>'+
    '<textarea></textarea><div class="note-acts">'+
    '<button type="button" class="nbtn act" data-instruct-save>Send the instruction'+
    '</button>'+
    '<button type="button" class="nbtn" data-note-cancel>Cancel</button></div>'+
    '<div class="note-hint">This does two things. It writes the instruction into this '+
    'entry, the same way a note is written, so it survives whether or not anything is '+
    'running. And '+who+'\u2019s session cannot finish a round without being shown it \u2014 '+
    'the Stop hook that runs at every turn boundary blocks them until they acknowledge it. '+
    'Nothing is sent anywhere. One instruction is one line \u2014 line breaks become '+
    'spaces.</div>';
  var ta=box.querySelector("textarea");
  sec.insertBefore(box,sec.querySelector("[data-note-tools]"));
  if(!quiet){ ta.focus(); }
  return box;
}

function instructDone(box,j){
  var who=esc(j.role||"that orchestrator");
  var h='<div class="note-done">';
  h+='<p><span class="hit">Recorded</span> in '+esc(j.doc||"")+' as '+esc(j.nid||"")+
     '. It is in the document whether or not anything is running.</p>';
  h+='<p><span class="miss">Not acknowledged yet.</span> '+who+'\u2019s session is shown '+
     'this at its next turn boundary and cannot finish a round until it acknowledges it. '+
     'Nothing was sent anywhere, and nothing had to be running for that to work.</p>';
  h+='<p>This block will read <b>seen by '+who+'</b> once they have. If it still says not '+
     'acknowledged tomorrow, that session has not run a turn.</p>';
  /* ⛔ THIS USED TO SAY "It is not committed" ON EVERY SAVE. Notes land themselves now, and
     the reply says whether this one did - so say that, and say it loudly when it did not. */
  if(j.landed===false){
    h+='<p class="note-err note-unlanded">Saved on this computer but NOT on main: '+
       esc(j.land_detail||"no reason was given")+'  A session working from main will not '+
       'see it until it lands.</p>';
  } else if(j.landed===true){
    h+='<p class="note-hint">It is on main ('+esc(j.land_detail||"")+').</p>';
  } else if(j.queued){
    h+='<p class="note-hint">It is being saved to GitHub in the background. The tag on '+
       esc(j.nid||"the instruction")+' above reads <b>saved</b> now and changes to '+
       '<b>landed</b> once it is on main, or says why not yet. The box at the bottom left of '+
       'the page shows the same for every save.</p>';
  }
  if(j.stuck&&j.stuck.length){
    h+='<p class="note-err note-stuck">Still on this computer only, NOT on main: '+
       esc(j.stuck.map(function(x){ return (x.nid||"?")+" ("+(x.why||"no reason given")+")"; })
         .join("; "))+'</p>';
  }
  h+='</div><div class="note-acts">'+
     '<button type="button" class="nbtn primary" data-instruct-done>Close</button></div>';
  box.innerHTML=h;
}
function esc(t){ var d=document.createElement("div"); d.textContent=t==null?"":t;
  return d.innerHTML; }
function noteSay(box,cls,msg){
  var old=box.querySelector(".note-err,.note-ok");
  if(old){old.parentNode.removeChild(old);}
  var d=document.createElement("div"); d.className=cls; d.textContent=msg;
  box.appendChild(d); return d;
}

function noteLandingWarnings(forId,nid,j,verb){
  if(!j){return;}
  var stuck=j.stuck||[];
  if(j.landed!==false&&!stuck.length){return;}
  var sec=document.querySelector('[data-notes-for="'+forId+'"]');
  if(!sec){return;}
  var tools=sec.querySelector("[data-note-tools]");
  function place(d){ if(tools){ sec.insertBefore(d,tools); } else { sec.appendChild(d); } }
  if(j.landed===false){
    var d=document.createElement("div"); d.className="note-err note-unlanded";
    d.textContent=verb+" on this computer but NOT on main: "+
      (j.land_detail||"no reason was given")+
      "  A session working from main will not see it until it lands.";
    var n=nid?sec.querySelector('.note[data-nid="'+nid+'"]'):null;
    if(n){ n.appendChild(d); } else { place(d); }
  }
  if(stuck.length){
    var s=document.createElement("div"); s.className="note-err note-stuck";
    s.textContent="Still on this computer only, NOT on main: "+
      stuck.map(function(x){ return (x.nid||"?")+" ("+(x.why||"no reason given")+")"; })
        .join("; ")+"  A session working from main will not see "+
      (stuck.length>1?"these":"this")+".";
    place(s);
  }
}

function notePost(body,onOk,onErr,path,onWait){
  var p=path||"/note", cid=null, settled=false;
  if(CID_ROUTES[p]){ body=Object.assign({},body); if(!body.cid){ body.cid=newCid(); }
    cid=body.cid; }
  function ok(j){ if(settled){return;} settled=true;
    /* A save queued for GitHub: the indicator at the bottom left and the note's tag follow it. */
    noteStateSaved(body,p,j);
    if(j&&j.queued&&window.landingPoke){window.landingPoke();}
    onOk(j); }
  function err(why){ if(settled){return;} settled=true; onErr(why); }
  
  fetchWithin(p,{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify(body)},cid?FETCH_SAVE_MS:FETCH_WRITE_MS,
    function(r){return r.json().then(function(j){return {s:r.status,j:j};});})
    .then(function(x){
      if(x.j&&x.j.pending&&cid){ if(onWait){onWait();} sentWait(cid,ok,err); return; }
      if(x.j&&x.j.ok){ok(x.j);}
      else{err((x.j&&x.j.error)||("the server refused it ("+x.s+")"));}})
    .catch(function(e){
      if(settled){ if(window.console){console.error(e);} return; }
      /* The answer was lost, which says nothing about the save: ask what became of it. */
      if(cid){ if(onWait){onWait();}
        sentWait(cid,ok,err,"could not reach the reader's server: "+e); }
      else{ err("could not reach the reader's server: "+e); } });
}

/* ⛔ THE RECEIPT NEEDS A HOME IN THE REFRESHED SECTION. The old panel lived inside the
   editor box, which the reload destroys - so after a reload there is nothing to write into.
   This makes an empty one in the same place the editor was. */
function instructReceipt(sec){
  /* Only an old receipt goes. This removed the first box of any kind, which after the reload
     could be a note draft just restored into this section. */
  dropReceipts(sec);
  var box=document.createElement("div");
  box.className="note-edit instruct";
  var tools=sec.querySelector("[data-note-tools]");
  if(tools){ sec.insertBefore(box,tools); } else { sec.appendChild(box); }
  return box;
}

function noteReload(dom,then){
  delete cache[view]; delete asking[view];   /* what forget() does: read it again from disk */
  var v=view, off=offIn(dom); view=null;
  load(v,function(){ if(dom){jump(dom,off);} if(then){then();} },true);
}

var OB="claude-orchdoc-reader-outbox|", OBM={}, OBBUSY={}, STALE={};
var OB_TITLE="On its way to the OrchDoc on this computer. You can keep working: this tag "+
  "reads saved once it is written there, and landed once it is on GitHub.";
var OB_WHO={"/note":"Note from the human","/instruct":"Instruction from the human"};
function obPut(r){ OBM[r.cid]=r;
  try{ window.localStorage.setItem(OB+r.cid,JSON.stringify(r)); }catch(e){} }
function obDel(cid){ delete OBM[cid];
  try{ window.localStorage.removeItem(OB+cid); }catch(e){} }
function obAll(){
  var out={}, k, i;
  for(k in OBM){ out[k]=OBM[k]; }
  try{ var ls=window.localStorage;
    for(i=0;i<ls.length;i++){ k=ls.key(i);
      if(k&&k.indexOf(OB)===0){
        try{ var v=JSON.parse(ls.getItem(k)); if(v&&v.cid&&!out[v.cid]){ out[v.cid]=v; } }
        catch(e){} } } }catch(e){}
  return Object.keys(out).map(function(c){ return out[c]; })
    .sort(function(a,b){ return (a.t||0)-(b.t||0); });
}
function obEntrySel(slug,id){
  return 'article.entry[data-doc="'+CSS.escape(slug||"")+'"][data-id="'+CSS.escape(id||"")+'"]';
}
/* The change on screen, in its entry, with where it is. */
function obShow(r,state,text,title){
  state=state||"sending"; text=text||(r.tries?"sending (trying again)":"sending");
  title=title||OB_TITLE;
  if(r.path==="/today"){
    var e=r.id&&pane.querySelector(obEntrySel(r.doc,r.id));
    if(e){ nsTag(e,state,text,title); }
    return; }
  if(r.path==="/instruct-doc"){
    var say=document.querySelector("#docins .docins-say");
    if(say&&view===r.doc){ say.innerHTML='<span class="note-state" data-state="'+esc(state)+
      '">'+esc(text)+'</span> Instruction for '+esc(r.doc)+': '+esc(nsClip(r.text,80))+
      ' - you can keep working.'; }
    return; }
  var sec=secFor(pane,r.forId);
  if(!sec){return;}
  var n=r.nid?sec.querySelector('.note[data-nid="'+CSS.escape(r.nid)+'"]')
             :sec.querySelector('.note[data-ob="'+CSS.escape(r.cid)+'"]');
  if(!n&&r.nid){return;}
  if(!n){
    n=document.createElement("div");
    n.className="note ob-note"+(r.path==="/instruct"?" instruct":"");
    n.setAttribute("data-ob",r.cid);
    n.setAttribute("data-kind",r.path==="/instruct"?"instruction":"note");
    n.innerHTML='<div class="note-h"><span class="note-who">'+esc(OB_WHO[r.path]||"the human")+
      '</span></div><div class="note-b"></div>';
    var all=sec.querySelectorAll(".note"), last=all.length?all[all.length-1]:null;
    sec.insertBefore(n,last?last.nextSibling:sec.firstChild);
    sec.classList.remove("empty");
  } else { n.setAttribute("data-ob",r.cid); }
  var b=n.querySelector(".note-b");
  if(b&&b.textContent!==r.text){ b.textContent=r.text; b.setAttribute("data-raw",r.text); }
  nsTag(n,state,text,title);
}
function obShowAll(){ obAll().forEach(function(r){ obShow(r); }); }
/* Into the outbox, the editor closed, the change on screen, and on its way. */
function obSave(box,sec,path,txt){
  var r={cid:boxCid(box,txt),path:path,doc:sec.getAttribute("data-notes-doc")||"",
         id:sec.getAttribute("data-notes-id")||"",forId:sec.getAttribute("data-notes-for")||"",
         nid:path==="/note"?(box.getAttribute("data-nid")||""):"",text:txt,
         initial:box._initial||"",t:Date.now()};
  obPut(r); draftDel(box);
  if(box.parentNode){ box.parentNode.removeChild(box); }
  obShow(r); obSend(r);
}
function obSend(r){
  if(OBBUSY[r.cid]){return;}
  OBBUSY[r.cid]=1;
  var body={token:NOTE_TOKEN,doc:r.doc,cid:r.cid,bg:1};
  if(r.path==="/today"){ body.act=r.act;
    if(r.id){ body.id=r.id; }
    if(r.act==="add"){ body.text=r.text; body.repeat=r.repeat; }
    if(r.act==="priority"){ body.choice=r.choice; }
    if(r.act==="status"){ body.choice=r.choice; body.reason=r.reason||""; }
    if(r.act==="unstatus"){ body.undo=r.undo; } }
  else { body.text=r.text;
    if(r.path!=="/instruct-doc"){ body.id=r.id; }
    if(r.path==="/note"){ body.nid=r.nid||""; } }
  notePost(body,function(j){ delete OBBUSY[r.cid]; obDel(r.cid); obDone(r,j); },
    function(why){ delete OBBUSY[r.cid];
      /* Not refused, only not delivered: it stays in the outbox and goes again. */
      if(/could not reach|never reached the writer/.test(why||"")){
        r.tries=(r.tries||0)+1; obPut(r);
        obShow(r,"sending","sending (trying again)",OB_TITLE+" The last try did not reach "+
          "the server: "+why);
        setTimeout(function(){ if(OBM[r.cid]){ obSend(r); } },
                   Math.min(30000,2000*Math.pow(2,Math.min(r.tries,4))));
        return; }
      obDel(r.cid); obRefused(r,why); },r.path);
}

function obStale(){
  Object.keys(cache).forEach(function(k){ if(k===view){ STALE[k]=1; } else { forget(k); } });
}
/* After a write: the entry drawn again, or, when it is on screen and that failed, the view. */
function afterWrite(slug,id,forId,then){
  obStale();
  if(!pane.querySelector(obEntrySel(slug,id))){ if(then){then();} return; }
  entryPatch(slug,id,function(ok){
    if(ok){ if(then){then();} return; }
    delete STALE[view]; noteReload(forId,then); });
}
function obDone(r,j){
  if(r.path==="/today"&&(r.act==="status"||r.act==="unstatus")){
    
    var mk=r.doc+"|"+r.id;
    if(r.act==="status"){ MARKED[mk]={label:r.choice,undo:(j&&j.undo)||null}; }
    else { delete MARKED[mk]; }
    obStale();
    entryPatch(r.doc,r.id,function(ok){ if(!ok){ todayRefresh(); return; } todayBadge();
      nsPaint(); });
    return; }
  if(r.path==="/today"){
    obStale();
    
    if(view===TODAY||r.act==="add"||r.tgt||(r.choice&&/^Don/.test(r.choice))||
       (r.act!=="priority"&&pane.querySelector(".today-strip"))||!pane.querySelector(obEntrySel(r.doc,r.id))){
      todayRefresh(); return; }
    entryPatch(r.doc,r.id,function(ok){ if(!ok){ todayRefresh(); return; } todayBadge();
      nsPaint(); });
    return; }
  if(r.path==="/instruct-doc"){
    obStale();
    var id=j.id||"", where=j.landed===false
      ? " It is on this computer only, NOT on main: "+(j.land_detail||"no reason given")+"."
      : j.queued ? " It reads saved now, and landed once it is on main." : "";
    var s2=document.querySelector("#docins .docins-say");
    if(s2&&view===r.doc){ s2.innerHTML='Sent to '+esc(r.doc)+' as <a href="#'+esc(r.doc)+'/'+
      esc(id)+'">'+esc(id)+'</a>.'+esc(where);
      pane.style.setProperty("--docins-h",document.getElementById("docins").offsetHeight+"px"); }
    
    entryInsert(r.doc,id);
    return; }
  afterWrite(r.doc,r.id,r.forId,function(){
    if(r.path==="/instruct"){ var s=secFor(pane,r.forId);
      if(s){ instructDone(instructReceipt(s),j); } }
    else { noteLandingWarnings(r.forId,(j&&j.nid)||"",j,"Saved"); }
    obShowAll(); nsPaint(); });
}
function obRefused(r,why){
  if(r.path==="/today"&&r.tgt){ todayRefresh(); window.alert("Not changed: "+why); return; }
  if(r.path==="/today"){
    var f=pane.querySelector("form.today-add");
    if(r.act==="add"&&f){ if(!f.text.value){ f.text.value=r.text||""; }
      var fs=f.querySelector(".today-say"); if(fs){ fs.textContent="NOT ADDED \u2014 "+why; }
      return; }
    var e=r.id&&pane.querySelector(obEntrySel(r.doc,r.id));
    if(e){ nsTag(e,"failing","not changed: "+nsClip(why,90),"Not changed: "+why); }
    else { window.alert("Not changed: "+why); }
    return; }
  if(r.path==="/instruct-doc"){
    try{ if(!window.localStorage.getItem(DIK+r.doc)){
      window.localStorage.setItem(DIK+r.doc,r.text); }
      /* Sent again with the same text, it carries the same id (a repeat writes nothing). */
      window.localStorage.setItem(DIK+r.doc+"|cid",JSON.stringify({cid:r.cid,text:r.text}));
    }catch(e){}
    var w=document.getElementById("docins");
    if(w&&view===r.doc){ var ta=w.querySelector("textarea");
      if(!ta.value){ ta.value=r.text; w.classList.add("open"); }
      w.querySelector(".docins-say").textContent="NOT SENT \u2014 "+why+"  Your text is back "+
        "in the box, and this browser keeps a copy until it is sent."; }
    else { window.alert("The instruction for "+r.doc+" was NOT sent: "+why+
      "\n\nYour text is kept in its box: open "+r.doc+" to send it again."); }
    return; }
  var kind=r.path==="/instruct"?"instruct":"note";
  var msg=(kind==="instruct"?"NOT SENT \u2014 ":"NOT SAVED \u2014 ")+why+
    "  Your text is still here, back in this box, and this browser keeps a copy until it is "+
    (kind==="instruct"?"sent.":"saved.");
  var sec=secFor(pane,r.forId);
  if(!sec||!sec.getAttribute("data-wired")){
    var k=draftKey(kind,r.forId,r.nid);
    try{ window.localStorage.setItem(k,JSON.stringify({kind:kind,forId:r.forId,doc:r.doc,
      id:r.id,nid:r.nid||"",text:r.text,initial:r.initial||"",t:Date.now(),pending:false})); }
    catch(e){}
    orphans[k]=msg; barReset(); return; }
  var ph=sec.querySelector('.note[data-ob="'+CSS.escape(r.cid)+'"]');
  if(ph&&!r.nid){ ph.parentNode.removeChild(ph); }
  function reopen(){
    var s=secFor(pane,r.forId); if(!s){ return; }
    var box=kind==="instruct"?instructEditor(s,true):noteEditor(s,r.nid,r.initial,true);
    var ta=box.querySelector("textarea"); ta.value=r.text; box._initial=r.initial||"";
    /* The same id: if the write did happen after all, Save again is answered as a repeat
       instead of writing it twice. */
    box._cid=r.cid; box._cidText=r.text;
    draftPut(box); noteSay(box,"note-err",msg);
  }
  /* An edit already showed its new text in the note: the entry is drawn again first. */
  if(r.nid){ entryPatch(r.doc,r.id,reopen); } else { reopen(); }
}

function entryPatch(slug,id,cb0){
  
  var called=false;
  function cb(ok,partial,h){ if(called){ return; } called=true; cb0(ok,partial,h); }
  if(!pane.querySelector(obEntrySel(slug,id))){ cb(false); return; }
  fetchWithin("/entry/"+encodeURIComponent(slug)+"/"+encodeURIComponent(id)+
    (view===slug?"":"?show_doc=1"),{cache:"no-store"},FETCH_LOAD_MS)
  .then(function(j){ try{ (function(){
    var old=pane.querySelector(obEntrySel(slug,id));
    if(!old||!j||!j.ok||!j.html){ cb(false); return; }
    var t=document.createElement("template"); t.innerHTML=String(j.html).trim();
    var fresh=t.content.querySelector("article.entry");
    if(!fresh){ cb(false); return; }
    
    var boxOpen=!!old.querySelector(".note-edit textarea");
    
    var unsent=[].slice.call(old.querySelectorAll(".note[data-ob]")), pend={};
    obAll().forEach(function(r){ pend[r.cid]=1; });
    var stale=unsent.filter(function(n){
      return !n.getAttribute("data-nid")&&!pend[n.getAttribute("data-ob")]; });
    if(j.h&&stale.length===unsent.length&&(old.getAttribute("data-eh")===j.h||
       (boxOpen&&old.getAttribute("data-ehn")===j.h))){
      stale.forEach(function(n){ var s=n.parentNode; s.removeChild(n);
        s.classList.toggle("empty",!s.querySelector(".note")); });
      entrySettled(old); cb(true,old.getAttribute("data-eh")!==j.h,j.h); return; }
    if(j.h){ fresh.setAttribute("data-eh",j.h); }
    var pr=pane.getBoundingClientRect(), r0=old.getBoundingClientRect(), h0=old.offsetHeight;
    var oa=pane.style.overflowAnchor; pane.style.overflowAnchor="none";
    if(boxOpen){
      old.removeAttribute("data-eh");
      if(j.h){ old.setAttribute("data-ehn",j.h); }
      var os=old.querySelector(".notes"), fsec=fresh.querySelector(".notes");
      if(!os||!fsec){ pane.style.overflowAnchor=oa; cb(false); return; }
      [].slice.call(os.querySelectorAll(":scope > .note")).forEach(function(n){
        n.parentNode.removeChild(n); });
      var at=os.firstChild;
      [].slice.call(fsec.querySelectorAll(":scope > .note")).forEach(function(n){
        os.insertBefore(n,at); if(os.getAttribute("data-wired")){ noteActs(n); } });
      os.classList.toggle("empty",!os.querySelector(".note"));
    } else {
      old.parentNode.replaceChild(fresh,old);
      if(obs){ try{ obs.unobserve(old); obs.observe(fresh); }catch(e){} }
      delete bodyCache[fresh.id];
      noteWire(fresh); draftRestore(fresh);
      if(cur===fresh.id){ cur=null; mark(fresh.id); }
    }
    
    if(r0.bottom<=pr.top){ pane.scrollTop+=(pane.querySelector(obEntrySel(slug,id)).offsetHeight-h0); }
    pane.style.overflowAnchor=oa;
    entrySettled(boxOpen?old:fresh); cb(true,boxOpen,j.h);
  })(); }catch(e){ if(called){ throw e; } cb(false); } },function(){ cb(false); });
}

function entryInsert(slug,id,cb0){
  var cb=cb0||function(){};
  function there(){ return !!pane.querySelector(obEntrySel(slug,id)); }
  if(!slug||!id||view!==slug||there()){ cb(view===slug&&there()); return; }
  fetchWithin("/entry/"+encodeURIComponent(slug)+"/"+encodeURIComponent(id),{cache:"no-store"},
    FETCH_LOAD_MS).then(function(j){
    if(view!==slug||there()){ cb(view===slug); return; }
    if(!j||!j.ok||!j.html){ cb(false); return; }
    var t=document.createElement("template"); t.innerHTML=String(j.html).trim();
    var fresh=t.content.querySelector("article.entry");
    if(!fresh){ cb(false); return; }
    var prev=j.after?pane.querySelector(obEntrySel(slug,j.after)):null;
    if(!prev){ var all=pane.querySelectorAll("article.entry"); prev=all.length?all[all.length-1]:null; }
    if(prev){ prev.parentNode.insertBefore(fresh,prev.nextSibling); } else { pane.appendChild(fresh); }
    if(j.nav&&!railE.querySelector('.ebtn[data-id="'+CSS.escape(id)+'"]')){
      var nt=document.createElement("template"); nt.innerHTML=String(j.nav).trim();
      var nb=nt.content.firstChild, pb=j.after?railE.querySelector('.ebtn[data-id="'+
        CSS.escape(j.after)+'"]'):null;
      if(nb){ if(pb){ pb.parentNode.insertBefore(nb,pb.nextSibling); } else { railE.appendChild(nb); } }
    }
    if(obs){ try{ obs.observe(fresh); }catch(e){} }
    noteWire(fresh); draftRestore(fresh); apply(); obShowAll(); nsPaint();
    cb(true);
  },function(){ cb(false); });
}
/* A landing put a note under another id: the note says so, without re-reading the view. False
   when that is not enough (a new entry renamed), and the view is read again. */
function nsRenumber(n,d){
  if(!n||(d.entry_final&&d.entry_final!==d.entry)||!d.nid_final){ return false; }
  var was=n.getAttribute("data-nid");
  n.setAttribute("data-nid",d.nid_final);
  var l=n.querySelector(".note-id"); if(l){ l.textContent=d.nid_final; }
  var sec=noteSection(n);
  if(sec&&was){ sec.querySelectorAll('[data-docins-del="'+CSS.escape(was)+'"]').forEach(
    function(b){ b.setAttribute("data-docins-del",d.nid_final); }); }
  return true;
}

var CID_ROUTES={"/note":1,"/instruct":1,"/instruct-doc":1,"/today":1};
var NS={last:null,mine:{},landed:{},refused:{},renumbered:{},checking:{},reload:null};
function newCid(){
  var a=new Uint8Array(12), s="", i;
  try{ window.crypto.getRandomValues(a); }
  catch(e){ for(i=0;i<a.length;i++){ a[i]=Math.floor(Math.random()*256); } }
  for(i=0;i<a.length;i++){ s+=("0"+a[i].toString(16)).slice(-2); }
  return s;
}
/* The id for what `box` holds now: the same id for as long as the text is the same. */
function boxCid(box,txt){
  if(!box._cid||box._cidText!==txt){ box._cid=newCid(); box._cidText=txt; }
  return box._cid;
}
/* Ask what became of save `cid` until it is on disk (`ok`) or refused (`err`). */
function sentWait(cid,ok,err,why){
  var miss=0, gone=0;
  function step(){
    
    fetchWithin("/landing?cid="+encodeURIComponent(cid),{cache:"no-store"},FETCH_POLL_MS)
      .then(function(s){
        var c=(s&&s.cids&&s.cids[cid])||{state:"unknown"};
        if(c.state==="done"||c.state==="saved"){ var j=c.reply||{};
          if(j.ok){ ok(j); } else { err(j.error||"the writer refused it"); } return; }
        if(c.state==="sending"){ miss=0; setTimeout(step,1000); return; }
        /* No writer has seen this id. Asked a few times: a request can still be on its way
           in when the first answer is read. */
        if(++miss>=5){ err(why||"it never reached the writer on this computer"); return; }
        setTimeout(step,1000); })
      .catch(function(){
        if(++gone>=60){ err(why||"could not reach the reader's server"); return; }
        setTimeout(step,2000); });
  }
  step();
}
function nsKey(slug,entry,nid){ return slug+"|"+(entry||"")+"|"+nid; }
function nsClip(s,n){ s=String(s==null?"":s); return s.length>n?s.slice(0,n-3)+"...":s; }
/* What the page saved itself, so its note is tagged before the next status poll comes in. */
function noteStateSaved(body,p,j){
  if(!j||body.del){return;}
  /* A Do-today change has no note: the item's own title line carries its tag. */
  if(p==="/today"){ if(j.id){ NS.mine[nsKey(body.doc,j.id,"")]={job:j.queued||"",
      state:j.queued?"saved":j.landed===true?"landed":j.landed===false?"failing":"saved",
      why:j.landed===false?(j.land_detail||""):""}; }
    return; }
  if(!j.nid){return;}
  var entry=p==="/instruct-doc"?(j.id||""):(body.id||j.id||"");
  NS.mine[nsKey(body.doc,entry,j.nid)]={job:j.queued||"",
    state:j.queued?"saved":j.landed===true?"landed":j.landed===false?"failing":"saved",
    why:j.landed===false?(j.land_detail||""):""};
}
function nsNote(slug,entry,nid){
  if(!slug||!nid){return null;}
  return pane.querySelector('.notes[data-notes-doc="'+CSS.escape(slug)+'"]'+
    (entry?'[data-notes-id="'+CSS.escape(entry)+'"]':"")+
    ' .note[data-nid="'+CSS.escape(nid)+'"]');
}
/* The note, or, for a change that has none (a Do-today item), the entry itself. */
function nsFind(slug,entry,nid){
  if(nid){ return nsNote(slug,entry,nid); }
  if(!slug||!entry){return null;}
  return pane.querySelector('article.entry[data-doc="'+CSS.escape(slug)+'"][data-id="'+
    CSS.escape(entry)+'"]');
}
function nsTag(n,state,text,title){
  var h=n.classList.contains("entry")?n.querySelector(":scope > h2.et"):n.querySelector(".note-h");
  if(!h){return;}
  var t=h.querySelector(".note-state");
  if(!t){ t=document.createElement("span"); t.className="note-state";
    h.insertBefore(t,h.querySelector(".note-caret")); }
  if(t.getAttribute("data-state")!==state){ t.setAttribute("data-state",state); }
  if(t.textContent!==text){ t.textContent=text; }
  if(t.title!==title){ t.title=title; }
}
/* "sending" on a box whose save is on its way, with what is happening in words. */
function nsSending(box,msg){
  var d=noteSay(box,"note-ok",msg), t=document.createElement("span");
  t.className="note-state"; t.setAttribute("data-state","sending"); t.textContent="sending";
  t.title="On its way to the OrchDoc on this computer.";
  d.insertBefore(document.createTextNode(" "),d.firstChild); d.insertBefore(t,d.firstChild);
  return d;
}

function nsReload(){
  var a=document.activeElement;
  if(!NS.reload||(a&&a.tagName==="TEXTAREA"&&pane.contains(a))){return;}
  NS.reload=null; noteReload(cur);
}
var SAVED_TITLE="Saved in the OrchDoc on this computer. ";
function nsPaint(){
  var s=NS.last, have={};
  ((s&&s.jobs)||[]).forEach(function(j){
    var today=j.op==="today", nid=today?"":j.nid;
    if(!j.slug||(!nid&&!today)||j.op==="delete"||j.op==="withdraw"||j.op==="retire"){return;}
    /* Known by the ids it was saved under; a landing that renamed it gives the new ones, which
       a re-read page shows. */
    var k=nsKey(j.slug,j.entry,nid), n=nsFind(j.slug,j.entry,nid)||
      ((j.entry_final||j.nid_final)?nsFind(j.slug,j.entry_final||j.entry,
                                           today?"":(j.nid_final||nid)):null);
    have[k]=1; delete NS.landed[k];
    if(!n){return;}
    if(j.state==="failing"||j.state==="parked"||j.state==="unreadable"){
      nsTag(n,"failing","saved, not landed yet: "+nsClip(j.why,90)+" - retrying"+
        (j.next_try_in?" in "+j.next_try_in+" s":""),
        SAVED_TITLE+"Not on GitHub yet: "+j.why+(j.attempts?" (tried "+j.attempts+" times)":"")+
        ". It is tried again by itself.");
    } else {
      nsTag(n,"saved","saved",SAVED_TITLE+(j.running?"Being committed and pushed to GitHub now."
        :"Committed and pushed to GitHub after the changes saved before it."));
    }
  });
  ((s&&s.done)||[]).forEach(function(d){
    if(d.restored){
      if(!NS.refused[d.job]){ NS.refused[d.job]=d;
        /* o9cL1: the entry is drawn again, not the view; the view only if it cannot be. */
        if(d.entry&&pane.querySelector(obEntrySel(d.slug,d.entry))){
          entryPatch(d.slug,d.entry,function(ok){ if(!ok){ NS.reload=true; nsReload(); } });
        } else if(d.slug===view){ NS.reload=true; } }
      return; }
    var today=d.op==="today", nid=today?"":(d.nid_final||d.nid);
    if(!d.slug||(!nid&&!today)||d.op==="delete"||d.op==="withdraw"||d.op==="retire"){return;}
    var k=nsKey(d.slug,d.entry_final||d.entry,nid);
    if(!have[k]){ NS.landed[k]=d.detail||""; }
    /* Landed under another id: this page still shows the old one until it is re-read. */
    if(((d.nid_final&&d.nid_final!==d.nid)||(d.entry_final&&d.entry_final!==d.entry))&&
       !NS.renumbered[d.job]){
      NS.renumbered[d.job]=1;
      var rn=nsFind(d.slug,d.entry,today?"":d.nid);
      if(rn&&(today||!nsRenumber(rn,d))){ NS.reload=true; } }
  });
  Object.keys(NS.landed).forEach(function(k){
    if(have[k]){return;} have[k]=1;
    var p=k.split("|"), n=nsFind(p[0],p[1],p[2]);
    if(n){ nsTag(n,"landed","landed","Committed and pushed to GitHub main"+
      (NS.landed[k]?" ("+NS.landed[k]+")":"")+"."); }
  });
  Object.keys(NS.mine).forEach(function(k){
    if(have[k]){return;}
    var m=NS.mine[k], p=k.split("|"), n=nsFind(p[0],p[1],p[2]);
    if(!n){return;}
    if(s&&s.enabled===false){
      nsTag(n,"off","saved (landing off)",SAVED_TITLE+"This reader was started with --no-land, "+
        "so nothing is committed or pushed.");
    } else if(m.state==="landed"){
      nsTag(n,"landed","landed","Committed and pushed to GitHub main.");
    } else if(m.state==="failing"){
      nsTag(n,"failing","saved, not landed: "+nsClip(m.why,90),SAVED_TITLE+"Not on GitHub: "+m.why);
    } else {
      nsTag(n,"saved","saved",SAVED_TITLE+"Being committed and pushed to GitHub.");
    }
  });
  Object.keys(NS.refused).forEach(function(job){
    var d=NS.refused[job];
    var sec=pane.querySelector('.notes[data-notes-doc="'+CSS.escape(d.slug||"")+'"]'+
      '[data-notes-id="'+CSS.escape(d.entry||"")+'"]');
    if(!sec||sec.querySelector('[data-refused="'+CSS.escape(job)+'"]')){return;}
    var e=document.createElement("div"); e.className="note-err"; e.setAttribute("data-refused",job);
    e.textContent="Not deleted: "+(d.error||d.detail||"main refused it");
    sec.insertBefore(e,sec.querySelector("[data-note-tools]"));
  });
  /* Not from inside a paint: the view is re-read after this one has finished. */
  if(NS.reload){ setTimeout(nsReload,0); }
  markPaint();
}
window.noteStatesApply=function(s){ if(s&&s.ok===true){ NS.last=s; revCheck(s.revs); revDrop(s.revs); }
  nsPaint(); };

var CREV={};
function revDrop(revs){
  if(!revs){ return; }
  Object.keys(cache).forEach(function(k){
    if(k===view||revs[k]===undefined){ return; }
    if(CREV[k]!==revs[k]){ forget(k); delete CREV[k]; } });
  /* A doc with no view held is current as of this answer, so the view fetched next is current as
     of it. The one on screen keeps the value from before its fetch: revCheck mends the page, not
     the held view, so on leaving it the view is dropped and read again. */
  Object.keys(revs).forEach(function(k){ if(!cache[k]){ CREV[k]=revs[k]; } });
}

var REVS={}, REVH={}, REVN={}, REVBUSY=false, REVPEND={}, REV_TRIES=5;
function revRetry(slug){
  var p=REVPEND[slug];
  return !!p&&Object.keys(p).some(function(id){
    if(p[id]>=REV_TRIES){ return false; }
    var e=pane.querySelector(obEntrySel(slug,id));
    return !!e&&!e.querySelector(".note-edit textarea"); });
}
function revCheck(revs){
  var slug=view;
  if(!revs||!slug||!revs[slug]||REVBUSY||REVRUN){ return; }
  var same=REVS[slug]===revs[slug];
  if(same&&!revRetry(slug)){ return; }
  REVBUSY=true;
  fetchWithin("/revs/"+encodeURIComponent(slug),{cache:"no-store"},FETCH_LOAD_MS)
  .then(function(j){
    REVBUSY=false;
    if(!j||!j.ok||view!==slug){ return; }
    var old=REVH[slug], moved=REVS[slug]!==j.rev; REVS[slug]=j.rev;
    if(!old){ REVH[slug]=j.ids; REVN[slug]=j.ids; return; }
    var next={}, changed=[], owed=[], added=[], p=REVPEND[slug];
    /* A new change on disk gives a failed entry its tries back. */
    if(p&&moved){ Object.keys(p).forEach(function(id){ p[id]=0; }); }
    if(p){ Object.keys(p).forEach(function(id){ if(j.ids[id]===undefined){ delete p[id]; } }); }
    Object.keys(j.ids).forEach(function(id){
      if(old[id]===undefined){ added.push(id); }
      else if(p&&p[id]!==undefined){ next[id]=old[id]; owed.push(id); }
      else if(old[id]!==j.ids[id]){ next[id]=old[id]; changed.push(id); }
      else { next[id]=old[id]; }
    });
    REVH[slug]=next; REVN[slug]=j.ids;
    added.forEach(function(id){ entryInsert(slug,id,function(ok){
      if(ok&&REVH[slug]){ REVH[slug][id]=j.ids[id]; } }); });
    revQueue(slug,changed.concat(owed));
  },function(){ REVBUSY=false; });
}
/* One entry's draw is over. Only a full draw records its hash. */
function revDone(slug,id,ok,partial,h){
  var cur=REVH[slug]; if(!cur){ return; }
  var p=REVPEND[slug]||(REVPEND[slug]={});
  if(ok&&!partial){ if(h){ cur[id]=h; } else if(REVN[slug]&&REVN[slug][id]!==undefined){
      cur[id]=REVN[slug][id]; }
    delete p[id]; return; }
  if(view===slug&&!pane.querySelector(obEntrySel(slug,id))){
    /* Not on the page any more (removed): nothing to draw. */
    if(REVN[slug]&&REVN[slug][id]!==undefined){ cur[id]=REVN[slug][id]; }
    delete p[id]; return; }
  p[id]=(p[id]||0)+(ok?0:1);
}

var REVQ=[], REVQS={}, REVRUN=false, REVSLUG="", REVWALK=false;
function revQueue(slug,ids){
  if(REVSLUG!==slug){ REVQ=[]; REVQS={}; REVSLUG=slug; }
  ids.forEach(function(id){ if(!REVQS[id]){ REVQS[id]=1; REVQ.push(id); } });
  if(!REVRUN&&REVQ.length){ REVRUN=true; revNext(); }
}
function revNext(){
  if(!REVQ.length||view!==REVSLUG){
    
    if(REVQ.length){ var lp=REVPEND[REVSLUG]||(REVPEND[REVSLUG]={});
      REVQ.forEach(function(q){ lp[q]=lp[q]||0; }); }
    REVQ=[]; REVQS={}; REVRUN=false;
    
    if(REVWALK){ REVWALK=false; apply(); obShowAll(); nsPaint(); }
    return; }
  var id=REVQ.shift(), slug=REVSLUG, went=false;
  delete REVQS[id];
  /* The timer is for an answer that never comes; a draw that throws is answered cb(false). */
  function go(ok,partial,h){ if(went){ return; } went=true; revDone(slug,id,ok,partial,h);
    setTimeout(revNext,0); }
  setTimeout(go,FETCH_LOAD_MS+1000);
  entryPatch(slug,id,go);
}

function entrySettled(el){
  if(REVRUN&&REVQ.length){ REVWALK=true;
    if(el&&el.isConnected){ el.classList.toggle("hid",!applyTest(el)); }
    obShowAll(); nsPaint(); return; }
  REVWALK=false; apply(); obShowAll(); nsPaint(); }
/* A draft put back by a page reset while it was being sent: ask whether it arrived. */
function nsCheckRestored(d){
  if(NS.checking[d.cid]){return;}
  NS.checking[d.cid]=1;
  var act=d.kind==="instruct"?"sent":"saved";
  function here(){ var sec=secFor(pane,d.forId);
    return {sec:sec,box:sec&&findBox(sec,d.kind,d.nid)}; }
  sentWait(d.cid,function(j){
    delete NS.checking[d.cid];
    try{ window.localStorage.removeItem(d.key); }catch(e){}
    var h=here();
    if(h.box){ h.box.parentNode.removeChild(h.box); }
    noteStateSaved({doc:d.doc,id:d.id},d.kind==="instruct"?"/instruct":"/note",j);
    if(j.queued&&window.landingPoke){ window.landingPoke(); }
    barReset();
    if(h.sec){ noteSay(h.sec,"note-ok","Your "+(d.kind==="instruct"?"instruction":"note")+
      " was "+act+(j.nid?", as "+j.nid:"")+"."); }
    if(h.sec&&j.nid&&!h.sec.querySelector('.note[data-nid="'+CSS.escape(j.nid)+'"]')){
      NS.reload=true; }
    nsPaint();
  },function(){
    delete NS.checking[d.cid];
    try{ var v=JSON.parse(window.localStorage.getItem(d.key)||"null");
      if(v){ v.pending=false; window.localStorage.setItem(d.key,JSON.stringify(v)); } }catch(e){}
    var h=here(), r=h.box&&h.box.querySelector(".note-restored");
    if(r){ r.textContent="Restored from this browser. The page was reset while this was being "+
      act+", and it did not arrive. It is not "+act+" yet."; }
  });
}

var STARK="claude-orchdoc-reader-stars";
function starSet(){ try{ return JSON.parse(window.localStorage.getItem(STARK)||"[]")||[]; }
  catch(e){ return []; } }
function starOn(d,i){ return starSet().indexOf(d+"/"+i)>=0; }
function starToggle(d,i){
  var s=starSet(), k=d+"/"+i, at=s.indexOf(k);
  if(at>=0){s.splice(at,1);}else{s.push(k);}
  try{ window.localStorage.setItem(STARK,JSON.stringify(s)); }catch(e){}
  return at<0;
}
function starMark(el,on){ el.classList.toggle("on",on); el.textContent=on?"\u2605":"\u2606";
  el.setAttribute("aria-pressed",on?"true":"false");
  el.title=on?"Unstar":"Star this item"; }
function starPaint(root){
  if(!root){return;}
  root.querySelectorAll(".ebtn").forEach(function(b){
    var d=b.getAttribute("data-doc"), i=b.getAttribute("data-id"), j=b.getAttribute("data-jump");
    if(!d&&j){ var p=j.split("/"); d=p[0]; i=p[1]; }
    if(!d||!i||b.querySelector(".star")){return;}
    var s=document.createElement("span"); s.className="star"; s.setAttribute("role","button");
    s.setAttribute("tabindex","0"); s.setAttribute("data-star",d+"/"+i);
    starMark(s,starOn(d,i)); b.appendChild(s);
  });
}
function starClick(ev){
  var s=ev.target.closest&&ev.target.closest("[data-star]");
  if(!s){return;}
  if(ev.type==="keydown"&&ev.key!=="Enter"&&ev.key!==" "){return;}
  ev.preventDefault(); ev.stopPropagation();
  var k=s.getAttribute("data-star"), cut=k.indexOf("/");
  var on=starToggle(k.slice(0,cut),k.slice(cut+1));
  document.querySelectorAll('[data-star="'+CSS.escape(k)+'"]').forEach(function(x){
    starMark(x,on);});
  if(filt==="starred"){apply();}
}
document.addEventListener("click",starClick,true);
document.addEventListener("keydown",starClick,true);
/* The body search runs on the server (the page holds only titles for other docs). Its hits
   replace the title-only list when they arrive, unless the term has changed since. */
var crossSeq=0;
function crossFull(term,titleHits){
  var my=++crossSeq;
  setTimeout(function(){ if(my===crossSeq){ crossFetch(term,my); } },200);
}
/* ONE request per term: the entries box and the scripts/rules box both read it. */
var searchHeld={term:null,p:null};
function searchGet(term){
  if(searchHeld.term===term&&searchHeld.p){return searchHeld.p;}
  var p=fetch("/search?q="+encodeURIComponent(term)).then(function(r){return r.json();});
  searchHeld={term:term,p:p};
  p.catch(function(){ if(searchHeld.p===p){searchHeld={term:null,p:null};} });
  return p;
}
/* ⛔ ESCAPE FIRST, THEN MARK. A snippet is the server's text of a document or a script, and a
   script can quote a <script> tag. The server puts control characters 1 and 2 around the matched
   part; only those two become <mark>, after every other character is escaped. */
var MARK_ON=new RegExp(String.fromCharCode(1),"g"), MARK_OFF=new RegExp(String.fromCharCode(2),"g");
function snipHtml(x){
  return String(x==null?"":x).replace(/[&<>"]/g,function(c){
    return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c];})
    .replace(MARK_ON,"<mark>").replace(MARK_OFF,"</mark>");
}
function crossFetch(term,my){
  searchGet(term)
    .then(function(j){
      if(my!==crossSeq||!crossBox||crossBox.classList.contains("hid")){return;}
      var hits=(j&&j.hits)||[];
      var esc=function(x){return String(x).replace(/[&<>"]/g,function(c){
        return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c];});};
      crossBox.innerHTML='<div class="sec">All docs &middot; '+hits.length+' match'+
        (hits.length===1?"":"es")+(hits.length>=200?" (first 200)":"")+'</div>'+
        exactRows(hits,function(r){
          return '<button type="button" class="ebtn" data-jump="'+esc(r.d)+'/'+esc(r.i)+'">'+
            '<span class="dot '+esc(r.c)+'"></span><span class="eid">'+esc(r.d)+' '+esc(r.i)+
            '</span><span class="etitle">'+esc(r.t)+'<span class="snip">'+snipHtml(r.s)+
            '</span></span>'+(r.p?'<span class="pin">YOU</span>':"")+'</button>';});
      starPaint(crossBox);
    }).catch(function(){});
}

/* Starred items in OTHER docs, under the rail, while the Starred filter is on. */
function crossStarred(){
  var s=starSet();
  var hits=IDX.filter(function(r){ return r.d!==view&&s.indexOf(r.d+"/"+r.i)>=0; });
  if(!hits.length){hideCross();return;}
  if(!crossBox){ crossBox=document.createElement("div");
    crossBox.id="cross"; railE.parentNode.insertBefore(crossBox,railE);}
  crossBox.classList.remove("hid");
  crossBox.innerHTML='<div class="sec">Starred in other docs &middot; '+hits.length+'</div>'+
    hits.map(function(r){
      return '<button type="button" class="ebtn" data-jump="'+r.d+'/'+r.i+'">'+
        '<span class="dot '+r.c+'"></span><span class="eid">'+r.d+' '+r.i+'</span>'+
        '<span class="etitle">'+r.t+'</span>'+(r.p?'<span class="pin">YOU</span>':"")+
        '</button>';}).join("");
  starPaint(crossBox);
}

var DIK="claude-orchdoc-reader-docins|";
function docInsWire(){
  var old=document.getElementById("docins"); if(old){old.remove();}
  pane.style.removeProperty("--docins-h");
  if(!NOTE_TOKEN||!view||view===PLATE||view===SCRIPTS||view===TODAY||view==="rule"){return;}
  var d=view, w=document.createElement("div"); w.id="docins"; w.className="docins";
  w.innerHTML='<div class="docins-row"><textarea rows="1" '+
    'placeholder="Instruction for '+d+' - not about any one item"></textarea>'+
    '<button type="button" class="nbtn act">Instruct '+d+'</button></div>'+
    '<div class="docins-say" aria-live="polite"></div>';
  pane.insertBefore(w,pane.firstChild);
  pane.style.setProperty("--docins-h",w.offsetHeight+"px");
  
  if(window.ResizeObserver){ try{ new ResizeObserver(function(){
    if(w.isConnected&&w.offsetHeight){ pane.style.setProperty("--docins-h",w.offsetHeight+"px"); }
  }).observe(w); }catch(e){} }
  var ta=w.querySelector("textarea"), btn=w.querySelector("button"),
      say=w.querySelector(".docins-say");
  try{ ta.value=window.localStorage.getItem(DIK+d)||""; }catch(e){}
  if(ta.value){ w.classList.add("open"); }
  ta.addEventListener("focus",function(){ w.classList.add("open"); });
  ta.addEventListener("blur",function(){ if(!ta.value.trim()){ w.classList.remove("open"); } });
  ta.addEventListener("input",function(){
    try{ if(ta.value){window.localStorage.setItem(DIK+d,ta.value);}
         else{window.localStorage.removeItem(DIK+d);} }catch(e){} });
  btn.addEventListener("click",function(){
    var txt=ta.value.trim();
    if(!txt){ say.textContent="Type the instruction first."; ta.focus(); return; }
    
    var dk=DIK+d+"|cid", dm=null, dcid;
    try{ dm=JSON.parse(window.localStorage.getItem(dk)||"null"); }catch(e){ dm=null; }
    if(dm&&dm.cid&&dm.text===txt){ dcid=dm.cid; }
    else{ dcid=newCid();
      try{ window.localStorage.setItem(dk,JSON.stringify({cid:dcid,text:txt})); }catch(e){} }
    /* o9cL1: into the outbox, the box emptied for the next one, and on its way. */
    var r={cid:dcid,path:"/instruct-doc",doc:d,text:txt,t:Date.now()};
    obPut(r);
    try{ window.localStorage.removeItem(DIK+d); window.localStorage.removeItem(dk); }catch(e){}
    ta.value=""; obShow(r); obSend(r);
  });
}

function boxDoc(ta){
  var sec=ta.closest(".notes"); if(sec){ return sec.getAttribute("data-notes-doc"); }
  return ta.closest("#docins")?view:null;
}
document.addEventListener("paste",function(ev){
  var ta=ev.target;
  if(!ta||ta.tagName!=="TEXTAREA"||!(ta.closest(".note-edit")||ta.closest("#docins"))){return;}
  var items=(ev.clipboardData&&ev.clipboardData.items)||[], file=null;
  for(var i=0;i<items.length;i++){
    if(items[i].kind==="file"&&/^image\//.test(items[i].type)){ file=items[i].getAsFile(); break; } }
  if(!file){return;}
  ev.preventDefault();
  var d=boxDoc(ta); if(!d||!NOTE_TOKEN){return;}
  var hold="[image: uploading...]", a=ta.selectionStart, b=ta.selectionEnd;
  ta.value=ta.value.slice(0,a)+hold+ta.value.slice(b);
  function put(txt){
    var at=ta.value.indexOf(hold);
    if(at<0){ ta.value+=txt; } else { ta.value=ta.value.slice(0,at)+txt+ta.value.slice(at+hold.length); }
    ta.dispatchEvent(new Event("input",{bubbles:true}));
  }
  var rd=new FileReader();
  rd.onload=function(){
    notePost({token:NOTE_TOKEN,doc:d,data:String(rd.result)},
      function(j){ put(" [image: "+j.path+"] "); },
      function(why){ put(""); window.alert("The image was NOT saved: "+why); },"/image");
  };
  rd.readAsDataURL(file);
});

var bodyCache={};
function bodyHay(n){
  var dom=n.classList.contains("entry")?n.id:n.getAttribute("data-goto");
  if(!dom){return "";}
  if(bodyCache[dom]===undefined){ var e=document.getElementById(dom);
    bodyCache[dom]=e?e.textContent.replace(/\s+/g," ").toLowerCase():""; }
  return bodyCache[dom];
}

/* ---- §2.0 DO TODAY: tick off, untick, stop, add ----
   Every change goes through `orchdoc.py today`, and the view is re-read from disk after it. */
function todayRefresh(){
  forget(TODAY); var v=view;
  if(v!==TODAY){ forget(v); delete STALE[v]; }
  view=null; load(v,null,true);
  if(v!==TODAY){ todayBadge(); }
}
function todayBadge(){
  fetch("/doc/"+TODAY).then(function(r){return r.json();}).then(function(d){
    cache[TODAY]=d; var tb=document.getElementById("today-badge");
    if(tb){ tb.textContent=d.count; tb.classList.toggle("zero",!d.count); } });
}
document.addEventListener("click",function(ev){
  var b=ev.target.closest&&ev.target.closest("[data-today-act]");
  if(!b){return;}
  var act=b.getAttribute("data-today-act");
  if(act==="stop"&&!window.confirm("Stop this routine? It moves to \u00a799, marked "+
      "Stopped, with the days it was done kept for the record.")){return;}
  /* o9cL1: through the outbox: the item shows "sending" and the page goes on. A second click
     while the first is on its way does nothing. */
  var td=b.getAttribute("data-doc"), ti=b.getAttribute("data-id");
  if(obAll().some(function(x){ return x.path==="/today"&&x.doc===td&&x.id===ti; })){return;}
  var tr={cid:newCid(),path:"/today",doc:td,id:ti,act:act,t:Date.now()};
  obPut(tr); obShow(tr); obSend(tr);
});

document.addEventListener("click",function(ev){
  var b=ev.target.closest&&ev.target.closest("[data-dotag]");
  if(!b){return;}
  var d=b.getAttribute("data-doc"), id=b.getAttribute("data-id"),
      add=b.getAttribute("data-dotag")==="add",
      key=add?id:b.getAttribute("data-ntag");
  if(obAll().some(function(x){ return x.path==="/today"&&x.doc===d&&(x.tgt===id); })){return;}
  var tr=add?{cid:boxCid(b,d+"|"+id+"|dotag"),path:"/today",doc:d,act:"add",
        text:b.getAttribute("data-text"),repeat:"no",tgt:id,t:Date.now()}
      :{cid:newCid(),path:"/today",doc:d,id:key,act:"stop",tgt:id,t:Date.now()};
  obPut(tr); b.disabled=true; b.textContent=add?"Adding...":"Removing...";
  obSend(tr);
});
document.addEventListener("change",function(ev){
  var ps=ev.target.closest&&ev.target.closest("select.prio-sel");
  if(ps){
    if(!ps.value){return;}
    var pd=ps.getAttribute("data-doc"), pi=ps.getAttribute("data-id");
    var pr={cid:newCid(),path:"/today",doc:pd,id:pi,act:"priority",choice:ps.value,t:Date.now()};
    obPut(pr); obShow(pr); obSend(pr); return; }
  var tf=ev.target.closest&&ev.target.closest(".today-add");
  if(!tf||ev.target.name!=="repeat"){return;}
  var r=ev.target.value;
  tf.querySelector(".today-n").hidden=r!=="every";
  tf.querySelector(".today-wd").hidden=r!=="weekly on";
  tf.querySelector(".today-md").hidden=r!=="monthly on";
});
document.addEventListener("submit",function(ev){
  var f=ev.target; if(!f||!f.classList||!f.classList.contains("today-add")){return;}
  ev.preventDefault();
  var r=f.repeat.value, rep=r==="every"?"every "+f.n.value+" days":
      r==="weekly on"?"weekly on "+f.wd.value:r==="monthly on"?"monthly on "+f.md.value:r;
  var say=f.querySelector(".today-say"), btn=f.querySelector("button"), ttx=f.text.value.trim();
  var ar={cid:boxCid(f,f.doc.value+"|"+ttx+"|"+rep),path:"/today",doc:f.doc.value,act:"add",
          text:ttx,repeat:rep,t:Date.now()};
  obPut(ar); f.text.value="";
  say.innerHTML='<span class="note-state" data-state="sending">sending</span> Adding '+
    esc(nsClip(ttx,60))+' - you can keep working.';
  obSend(ar);
});

var MARKED={};
function mkWord(c){ return c==="done"?"Done":"Superseded"; }
function mkClose(e){ var f=e&&e.querySelector(".mk-confirm"); if(f){ f.parentNode.removeChild(f); } }
function mkOpen(b){
  var d=b.getAttribute("data-doc"), id=b.getAttribute("data-id"), ch=b.getAttribute("data-mark");
  var e=pane.querySelector(obEntrySel(d,id)); if(!e){return;}
  if(obAll().some(function(x){ return x.path==="/today"&&x.doc===d&&x.id===id; })){return;}
  var old=e.querySelector(".mk-confirm");
  if(old){ var same=old.getAttribute("data-choice")===ch; mkClose(e); if(same){return;} }
  var f=document.createElement("form"); f.className="mk-confirm"; f.setAttribute("data-choice",ch);
  f.innerHTML='<span class="mk-q"></span><input type="text" name="reason" maxlength="200" '+
    'autocomplete="off"><button type="submit" class="nbtn primary">Confirm</button>'+
    '<button type="button" class="nbtn" data-mk-cancel="1">Cancel</button>';
  f.querySelector(".mk-q").textContent="Mark "+id+" "+mkWord(ch)+"?";
  f.reason.setAttribute("aria-label",ch==="superseded"?"Superseded by (optional)":"Reason (optional)");
  f.reason.placeholder=ch==="superseded"?"Superseded by (optional)":"Reason (optional)";
  var m=e.querySelector(":scope > .meta");
  if(m&&m.nextSibling){ e.insertBefore(f,m.nextSibling); } else { e.appendChild(f); }
  f.reason.focus();
}
document.addEventListener("click",function(ev){
  var c=ev.target.closest&&ev.target.closest("[data-mk-cancel]");
  if(c){ mkClose(c.closest("article.entry")); return; }
  var u=ev.target.closest&&ev.target.closest("[data-mk-undo]");
  if(u){
    var ud=u.getAttribute("data-doc"), ui=u.getAttribute("data-id"), mk=MARKED[ud+"|"+ui];
    if(!mk||!mk.undo){return;}
    if(obAll().some(function(x){ return x.path==="/today"&&x.doc===ud&&x.id===ui; })){return;}
    var ur={cid:newCid(),path:"/today",doc:ud,id:ui,act:"unstatus",undo:mk.undo,t:Date.now()};
    var ub=u.closest(".mk-undo"); if(ub){ ub.parentNode.removeChild(ub); }
    obPut(ur); obShow(ur); obSend(ur); return; }
  var b=ev.target.closest&&ev.target.closest("[data-mark]");
  if(b){ mkOpen(b); }
});
document.addEventListener("submit",function(ev){
  var f=ev.target; if(!f||!f.classList||!f.classList.contains("mk-confirm")){return;}
  ev.preventDefault();
  var e=f.closest("article.entry"); if(!e){return;}
  var d=e.getAttribute("data-doc"), id=e.getAttribute("data-id"), ch=f.getAttribute("data-choice");
  var r={cid:newCid(),path:"/today",doc:d,id:id,act:"status",choice:ch,
         reason:f.reason.value.trim(),t:Date.now()};
  mkClose(e); obPut(r); obShow(r); obSend(r);
});
document.addEventListener("keydown",function(ev){
  if(ev.key!=="Escape"){return;}
  var cf=ev.target.closest&&ev.target.closest(".mk-confirm");
  if(cf){ mkClose(cf.closest("article.entry")); }
});
/* The Undo line, in each entry marked since this page was loaded. */
function markPaint(){
  if(!MARKED){return;}
  Object.keys(MARKED).forEach(function(k){
    var p=k.split("|"), e=pane.querySelector(obEntrySel(p[0],p[1])), m=MARKED[k];
    if(!e||e.querySelector(".mk-undo")){return;}
    if(obAll().some(function(x){ return x.path==="/today"&&x.doc===p[0]&&x.id===p[1]; })){return;}
    var d=document.createElement("div"); d.className="mk-undo";
    d.appendChild(document.createTextNode("You marked this "+mkWord(m.label)+"."));
    if(m.undo){
      var u=document.createElement("button"); u.type="button"; u.className="nbtn";
      u.setAttribute("data-mk-undo","1"); u.setAttribute("data-doc",p[0]);
      u.setAttribute("data-id",p[1]); u.textContent="Undo";
      u.title="Put it back as it was. Available until this page is reloaded.";
      d.appendChild(u);
    } else {
      d.appendChild(document.createTextNode(" It cannot be undone from here (it was edited "+
        "in a way that cannot be reversed); ask a session to reopen it."));
    }
    var m2=e.querySelector(":scope > .meta");
    if(m2&&m2.nextSibling){ e.insertBefore(d,m2.nextSibling); } else { e.appendChild(d); }
  });
}

/* ---- filters HIDE. Nothing is ever removed from the DOM. ---- */
/* Whether one entry (or rail button) passes the filters now. */
function applyTest(n,term,own){
  if(term===undefined){ term=(q.value||"").trim().toLowerCase(); }
  if(own===undefined){ own=ownerSel.value; }
  {
    /* A script file is not an entry: it has no status, owner or plate, so only the search
       term applies to it. Without this, "Open" or "On your plate" hid the whole list. */
    if(n.hasAttribute("data-script")||n.hasAttribute("data-rule")){
      return !term||n.getAttribute("data-hay").indexOf(term)>=0; }
    var s=n.getAttribute("data-sclass"), p=n.getAttribute("data-plate")==="1",
        k=n.getAttribute("data-kind"), o=n.getAttribute("data-owner");
    if(filt==="plate"&&!p){return false;}
    if(filt==="open"&&s!=="open"){return false;}
    if(filt==="resolved"&&s!=="done"){return false;}
    if(filt==="findings"&&k!=="F"&&k!=="S"){return false;}
    if(filt==="today"&&n.getAttribute("data-today")!=="1"){return false;}
    if(filt==="starred"&&!starOn(n.getAttribute("data-doc"),n.getAttribute("data-id"))){
      return false;}
    if(own&&o!==own){return false;}
    if(term&&n.getAttribute("data-hay").indexOf(term)<0&&bodyHay(n).indexOf(term)<0){
      return false;}
    return true;
  }
}
function apply(){
  var term=(q.value||"").trim().toLowerCase();
  var own=ownerSel.value;
  var all=scopeAll.getAttribute("aria-pressed")==="true";
  var shown=0,total=0;
  function test(n){ return applyTest(n,term,own); }
  railE.querySelectorAll(".ebtn").forEach(function(n){
    total++; var ok=test(n); n.classList.toggle("hid",!ok); if(ok){shown++;}});
  pane.querySelectorAll(".entry").forEach(function(n){
    n.classList.toggle("hid",!test(n));});
  railE.classList.toggle("tsearching",!!term);
  railE.querySelectorAll(".tdir").forEach(function(d){
    d.classList.toggle("hid",!d.querySelector(".ebtn:not(.hid)"));});
  /* a section header with nothing under it is noise */
  var secs=railE.querySelectorAll(".sec");
  secs.forEach(function(s){
    var n=s.nextElementSibling,any=false;
    while(n&&!n.classList.contains("sec")){
      if(n.classList.contains("ebtn")&&!n.classList.contains("hid")){any=true;break;}
      n=n.nextElementSibling;}
    s.classList.toggle("hid",!any);});
  pane.querySelectorAll(".grouphead").forEach(function(s){
    var n=s.nextElementSibling,any=false;
    while(n&&!n.classList.contains("grouphead")){
      if(n.classList.contains("entry")&&!n.classList.contains("hid")){any=true;break;}
      n=n.nextElementSibling;}
    /* §2.0 keeps its heading and add form even with nothing in it (class "keep"). */
    s.classList.toggle("hid",!any&&!s.classList.contains("keep"));});
  countEl.textContent=shown===total?(total+" shown"):(shown+" of "+total+" shown");
  /* ⛔ AN EMPTY PANE MUST SAY IT IS EMPTY. A filter that matches nothing renders a blank
     column, and blank reads as BROKEN rather than as "no matches" - the same ambiguity as
     a doc that parses to zero entries. Say which of the two it is. */
  var nm=document.getElementById("nonemsg");
  if(!nm){nm=document.createElement("div");nm.id="nonemsg";nm.className="empty";
    pane.appendChild(nm);}
  nm.textContent="Nothing here matches "+(term?('"'+term+'"'):"this filter")+
    (all||!term?".":" - try Search all docs.");
  nm.classList.toggle("hid",shown!==0);
  if(all&&term){crossSearch(term);}
  else if(filt==="starred"){crossStarred();}
  else{hideCross();}
  textSearch(term,all);
}

var tBox=null, tSeq=0, tTimer=null;
function hideT(){ tSeq++; if(tTimer){clearTimeout(tTimer);tTimer=null;} if(tBox){tBox.classList.add("hid");} }
function textSearch(term,all){
  if(!term||term.length<2){ hideT(); return; }
  var my=++tSeq;
  if(tTimer){clearTimeout(tTimer);}
  tTimer=setTimeout(function(){ tTimer=null; textFetch(term,all,my,0); },200);
}
/* Exact hits first, then one divider line, then the hits that only hold all the words. No
   divider when all are exact or all are loose; "No exact matches" when none is exact. */
function exactRows(list,fn){
  if(!list.length){return "";}
  var ex=list.filter(function(r){return r.ex;}), lo=list.filter(function(r){return !r.ex;});
  var h=ex.length?"":'<div class="xnone">No exact matches</div>';
  h+=ex.map(fn).join("");
  if(ex.length&&lo.length){ h+='<div class="xdiv">Contain all the words, not the exact phrase</div>'; }
  return h+lo.map(fn).join("");
}
function textFetch(term,all,my,tries){
  searchGet(term).then(function(j){
    if(my!==tSeq){return;}
    var entries=(!all&&(view===SCRIPTS||view==="rule"))?((j&&j.hits)||[]):[];
    var scr=(j&&j.scripts)||[], rules=(j&&j.rules)||[];
    if(!tBox){ tBox=document.createElement("div"); tBox.id="tbox";
      railE.parentNode.insertBefore(tBox,railE); }
    tBox.classList.remove("hid");
    var h="";
    if(entries.length){
      h+='<div class="sec">Entries &middot; '+entries.length+(entries.length>=200?" (first 200)":"")+'</div>'+
        exactRows(entries,function(r){
          return '<button type="button" class="ebtn" data-jump="'+snipHtml(r.d)+'/'+snipHtml(r.i)+'">'+
            '<span class="dot '+snipHtml(r.c)+'"></span><span class="eid">'+snipHtml(r.d)+' '+
            snipHtml(r.i)+'</span><span class="etitle">'+snipHtml(r.t)+'<span class="snip">'+
            snipHtml(r.s)+'</span></span></button>'; });
    }
    var tot=(j&&j.scripts_total)||scr.length;
    h+='<div class="sec">Scripts &middot; '+(scr.length?(tot>scr.length?"first "+scr.length+" of "+tot:tot):"0")+
      (j&&j.loading?" (still reading the script files)":"")+'</div>'+
      exactRows(scr,function(r){
        return '<button type="button" class="ebtn" data-search-script="'+snipHtml(r.rel)+'">'+
          '<span class="kb k-draft">S</span><span class="sp-col">'+
          '<span class="etitle">'+snipHtml(r.t)+'</span>'+
          '<span class="sidtag">'+snipHtml((r.id?r.id+"  ":"")+(r.v?r.v+"  ":"")+r.f)+'</span>'+
          '<span class="snip">'+snipHtml(r.s)+'</span></span></button>'; });
    if(rules.length){
      h+='<div class="sec">Rules &middot; '+rules.length+'</div>'+exactRows(rules,function(r){
        return '<button type="button" class="ebtn" data-search-rule="'+snipHtml(r.c)+'">'+
          '<span class="eid">'+snipHtml(r.c)+'</span><span class="etitle">'+snipHtml(r.t)+
          '<span class="snip">'+snipHtml(r.s)+'</span></span></button>'; });
    }
    tBox.innerHTML=h;
    /* The script files are read in the background after a restart; ask again shortly. */
    if(j&&j.loading&&tries<8){
      searchHeld={term:null,p:null};
      setTimeout(function(){ if(my===tSeq){ textFetch(term,all,my,tries+1); } },1500); }
  }).catch(function(){});
}
function openSearchScript(rel){
  var nh="#scripts/p/"+rel.split("/").map(encodeURIComponent).join("/");
  if(location.hash===nh){ route(); } else { location.hash=nh; }
}

/* ---- search across ALL docs, not just the open one ---- */
var crossBox=null;
function hideCross(){ if(crossBox){crossBox.classList.add("hid");} }
function crossSearch(term){
  if(!crossBox){ crossBox=document.createElement("div");
    crossBox.id="cross"; railE.parentNode.insertBefore(crossBox,railE);}
  crossBox.classList.remove("hid");
  var hits=IDX.filter(function(r){return r.h.indexOf(term)>=0;}).slice(0,300);
  crossFull(term,hits);
  crossBox.innerHTML='<div class="sec">All docs &middot; '+hits.length+' match'+
    (hits.length===1?"":"es")+'</div>'+hits.map(function(r){
      return '<button type="button" class="ebtn" data-jump="'+r.d+'/'+r.i+'">'+
        '<span class="dot '+r.c+'"></span><span class="eid">'+r.d+' '+r.i+'</span>'+
        '<span class="etitle">'+r.t+'</span>'+(r.p?'<span class="pin">YOU</span>':"")+
        '</button>';}).join("");
  starPaint(crossBox);
}


var pin=null;
function unpin(){ if(pin){ pin=null; pane.style.overflowAnchor=""; } }
["wheel","touchstart","pointerdown","keydown"].forEach(function(ev){
  window.addEventListener(ev,unpin,{passive:true,capture:true}); });
/* Above 820px the pane scrolls; at 820px and below it has overflow:visible and the WINDOW
   scrolls, so setting pane.scrollTop did nothing (measured: a hash jump at 800 wide left the
   heading at y=2182). paneScrolls() picks the scroller; the pane path is unchanged. */
function paneScrolls(){ return !window.matchMedia("(max-width:820px)").matches; }
function nudge(d){ if(paneScrolls()){ pane.scrollTop+=d; } else { window.scrollBy(0,d); } }
function drift(e,off){
  var want=parseFloat(window.getComputedStyle(e).scrollMarginTop)||0;
  var base=paneScrolls()?pane.getBoundingClientRect().top:0;
  return (e.getBoundingClientRect().top-base)-(want-(off||0));
}
function jump(dom,off){
  var e=document.getElementById(dom);
  if(!e){return false;}
  off=Math.max(0,off||0);
  if(!paneScrolls()){ setCol("read"); }   /* the pane is display:none in the other columns */
  nudge(drift(e,off));
  var p={t0:performance.now()}; pin=p;
  /* The browser's own scroll anchoring moves the pane too (measured: +29px with no scroll
     event, 160ms after landing) and the two corrections undo each other. Off while pinned. */
  pane.style.overflowAnchor="none";
  (function tick(){
    if(pin!==p){ if(!pin){pane.style.overflowAnchor="";} return; }
    var el=document.getElementById(dom);
    if(!el){pin=null;pane.style.overflowAnchor="";return;}
    var now=performance.now(), d=drift(el,off);
    if(Math.abs(d)>1){ nudge(d); }
    if(now-p.t0>6000){ pin=null; pane.style.overflowAnchor=""; savePlace(); return; }
    window.setTimeout(tick,60);   
  })();
  mark(dom); setCol("read"); savePlace(); return true;
}

function offIn(dom){
  var e=dom&&document.getElementById(dom);
  return e?Math.max(0,Math.round(-drift(e,0))):0;
}

var SS="claude-orchdoc-reader-place";
function savePlace(){
  try{window.sessionStorage.setItem(SS,JSON.stringify(
    {doc:view,entry:cur,off:offIn(cur),scroll:pane.scrollTop}));}catch(e){}
}
function isReload(){
  try{ var n=performance.getEntriesByType("navigation")[0];
       return !!n&&(n.type==="reload"||n.type==="back_forward"); }catch(e){ return false; }
}
function readPlace(){
  try{var v=window.sessionStorage.getItem(SS);return v?JSON.parse(v):null;}
  catch(e){return null;}
}

var asking={}, loads=0;
/* Drops a view so the next load() reads it again: after a save, the request already on its way
   may hold the view from before the save, so it is dropped too, and its answer is not kept. */
function forget(v){ delete cache[v]; delete asking[v]; }
function load(doc,then,restore){
  if(view===doc&&cache[doc]){ if(then){then();} return; }
  if(view===doc&&asking[doc]){
    if(then){ asking[doc].then(function(){ if(view===doc&&cache[doc]){then();} },
                               function(){}); }
    return; }
  /* ⛔ AN OPEN EDITOR WITH UNSAVED TEXT IS NOT THROWN AWAY BY A CLICK ON THE RAIL. Loading a
     view replaces the pane, and the editor is in the pane. */
  if(window.scriptDirty&&window.scriptDirty()&&!window.confirm(
      "You have unsaved script edits. Leave them? (A copy stays in this browser and is "+
      "offered back the next time you open that editor.)")){
    if(window.scriptHash){ history.replaceState(null,"",window.scriptHash()); }
    return; }
  if(window.scriptClosed){ window.scriptClosed(); }
  
  if(view!==doc&&STALE[view]){ forget(view); delete STALE[view]; }
  view=doc; var mine=++loads;
  railD.querySelectorAll(".dbtn").forEach(function(b){
    b.setAttribute("aria-current",b.getAttribute("data-doc")===doc?"true":"false");});
  function paint(d){
    railE.innerHTML=d.nav; pane.innerHTML=d.body; cur=null;
    bodyCache={}; starPaint(railE); apply(); spy(); noteWire(pane); draftRestore(pane); docInsWire();
    obShowAll(); nsPaint();
    
    if(!restore){pane.scrollTop=0;}
    if(then){then();}
  }
  if(cache[doc]){paint(cache[doc]);return;}
  railE.innerHTML='<div class="count">loading...</div>';
  if(!asking[doc]){
    
    /* revDrop keeps CREV[doc] at the answer from before this read, which is what the view is
       judged against later. */
    var req=fetchWithin("/doc/"+encodeURIComponent(doc),null,FETCH_LOAD_MS)
      .then(function(d){ if(asking[doc]!==req){ return d; }   /* forgotten while on its way */
        cache[doc]=d;
        if(d.count!==undefined){ var tb=document.getElementById("today-badge");
          if(tb){ tb.textContent=d.count; tb.classList.toggle("zero",!d.count); } }
        return d; });
    var gone=function(){ if(asking[doc]===req){ delete asking[doc]; } };
    asking[doc]=req; req.then(gone,gone);
  }
  asking[doc].then(function(d){ if(mine===loads&&view===doc){ paint(d); } })
    .catch(function(e){ if(mine===loads&&view===doc){
      railE.innerHTML='<div class="count">failed: '+e+'</div>'; } });
}

var PLATE="__plate__", SCRIPTS="__scripts__", TODAY="__today__";
function docOf(seg){
  if(!seg||seg==="plate"||seg===PLATE){return PLATE;}
  /* Scripts get a readable hash for the same reason the plate does: `#scripts/<id>`. */
  if(seg==="scripts"||seg===SCRIPTS){return SCRIPTS;}
  if(seg==="today"||seg===TODAY){return TODAY;}
  return seg;
}

function tabTitle(){
  var h=(location.hash||"").replace(/^#/,"");
  document.title=h?h.split("/").slice(0,2).join(" ")+" · OrchDoc":"OrchDoc reader";
}
["replaceState","pushState"].forEach(function(n){
  var orig=history[n];
  history[n]=function(){ var r=orig.apply(this,arguments); try{ tabTitle(); }catch(e){} return r; };
});
window.addEventListener("hashchange",tabTitle);
tabTitle();

function route(first){
  var h=(location.hash||"").replace(/^#/,"");
  if(!h){
    
    var pl=readPlace();
    if(pl&&pl.doc){
      load(pl.doc,function(){
        if(pl.entry&&document.getElementById(pl.entry)){jump(pl.entry,pl.off);}
        else if(pl.scroll){pane.scrollTop=pl.scroll;}
      },true);
      return;
    }
    load("__plate__"); return;
  }
  var p=h.split("/");
  if(docOf(p[0])===SCRIPTS){
    load(SCRIPTS,function(){
      
      if(p[1]==="p"&&window.openScriptPath){ window.openScriptPath(p.slice(2)); }
      else if(p[1]&&p[2]==="compare"&&window.openCompare){ window.openCompare(p[1]); }
      else if(p[1]&&window.openScript){ window.openScript(p[1]); } },true);
    return;
  }
  if(p.length===2){
    
    var dom0="entry-"+p[0]+"-"+p[1], pl0=(first&&isReload())?readPlace():null;
    var off0=(pl0&&pl0.entry===dom0)?pl0.off:0;
    load(docOf(p[0]),function(){ jump(dom0,off0); },true);
  } else { load(docOf(p[0])); }
}

document.addEventListener("click",function(ev){
  var d=ev.target.closest&&ev.target.closest("[data-doc].dbtn");
  if(d){ var s=d.getAttribute("data-doc");
    /* Scripts get `#scripts` for the same reason the plate gets `#plate`. */
    if(s===SCRIPTS){ location.hash="scripts"; load(s); setCol("list"); return; }
    /* The plate gets `#plate`, never "" - see the note above `route()`. */
    location.hash=s===PLATE?"plate":s===TODAY?"today":s;
    load(s); setCol("list"); return; }
  var g=ev.target.closest&&ev.target.closest("[data-goto]");
  if(g){ jump(g.getAttribute("data-goto")); return; }
  /* §1 Links and docs: open the fold and LAND on it. */
  var lb=ev.target.closest&&ev.target.closest("[data-links]");
  if(lb){ var ld=document.getElementById(lb.getAttribute("data-links"));
    if(ld){ ld.open=true; ld.scrollIntoView({behavior:"auto",block:"start"}); setCol("read"); }
    return; }
  var ss=ev.target.closest&&ev.target.closest("[data-search-script]");
  if(ss){ openSearchScript(ss.getAttribute("data-search-script")); return; }
  var sr=ev.target.closest&&ev.target.closest("[data-search-rule]");
  if(sr){ var rh="#rule/"+sr.getAttribute("data-search-rule");
    if(location.hash===rh){ route(); } else { location.hash=rh; } return; }
  var j=ev.target.closest&&ev.target.closest("[data-jump]");
  if(j){ var t=j.getAttribute("data-jump").split("/");
    load(t[0],function(){jump("entry-"+t[0]+"-"+t[1]);},true); return; }
  var na=ev.target.closest&&ev.target.closest("[data-note-add]");
  if(na){ noteEditor(noteSection(na),"",""); return; }
  var ia=ev.target.closest&&ev.target.closest("[data-instruct-add]");
  if(ia){ instructEditor(noteSection(ia)); return; }
  var idn=ev.target.closest&&ev.target.closest("[data-instruct-done]");
  if(idn){ var rb=idn.closest(".note-edit");
    /* o9cL1: the entry already shows the instruction; Close only takes the receipt away. */
    if(rb&&rb.parentNode){ rb.parentNode.removeChild(rb); } return; }
  var isv=ev.target.closest&&ev.target.closest("[data-instruct-save]");
  if(isv){
    var ibox=isv.closest(".note-edit"), isec=noteSection(isv);
    var ita=ibox.querySelector("textarea"), itxt=ita.value;
    if(!itxt.trim()){ noteSay(ibox,"note-err",
      "Nothing to send \u2014 the instruction is empty."); return; }
    
    obSave(ibox,isec,"/instruct",itxt);
    return; }
  var ne=ev.target.closest&&ev.target.closest("[data-note-edit]");
  if(ne){ var n=ne.closest(".note");
    noteEditor(noteSection(ne),n.getAttribute("data-nid"),
               (function(b){ return (b.getAttribute("data-raw")||b.textContent).trim(); })
                 (n.querySelector(".note-b"))); return; }
  var nc=ev.target.closest&&ev.target.closest("[data-note-cancel]");
  if(nc){ var b=nc.closest(".note-edit");
    /* Cancel used to drop the box without a word. It asks when there is text to lose. */
    if(unsaved(b)&&!window.confirm("Discard what you typed in this box? It has not been "+
        (boxKind(b)==="instruct"?"sent.":"saved."))){return;}
    draftDel(b); b.parentNode.removeChild(b); return; }
  var dop=ev.target.closest&&ev.target.closest("[data-draft-open]");
  if(dop){ draftOpen(dop.getAttribute("data-draft-open")); return; }
  var ddr=ev.target.closest&&ev.target.closest("[data-draft-drop]");
  if(ddr){ draftDrop(ddr.getAttribute("data-draft-drop")); return; }
  var wdel=ev.target.closest&&ev.target.closest("[data-docins-del]");
  if(wdel){
    var wsec=noteSection(wdel), wnid=wdel.getAttribute("data-docins-del");
    /* With its instruction note still on it, this is that note's own Delete, as before. */
    var tn=wnid&&wsec.querySelector('.note[data-nid="'+CSS.escape(wnid)+'"] [data-note-del]');
    if(tn){ tn.click(); return; }
    
    if(!window.confirm(docInsConfirm(wsec.getAttribute("data-notes-doc"),
        +(wsec.closest(".entry").getAttribute("data-ins-count")||0)))){return;}
    wdel.disabled=true;
    notePost({token:NOTE_TOKEN,doc:wsec.getAttribute("data-notes-doc"),
              id:wsec.getAttribute("data-notes-id"),del:1,bg:1},
      function(j){ var _for=wsec.getAttribute("data-notes-for");
        afterWrite(wsec.getAttribute("data-notes-doc"),wsec.getAttribute("data-notes-id"),_for,
          function(){ noteLandingWarnings(_for,"",j,"Deleted"); }); },
      function(why){ wdel.disabled=false;
        noteSay(wsec,"note-err","Not deleted - "+docInsWhy(why)); });
    return; }
  var idel=ev.target.closest&&ev.target.closest("[data-img-del]");
  if(idel){
    var inote=idel.closest(".note"), isec=noteSection(idel), ia=idel.previousElementSibling;
    var nb=inote.querySelector(".note-b"), raw=nb.getAttribute("data-raw")||"";
    var mark=ia&&ia.getAttribute("data-mark");
    if(!mark||raw.indexOf(mark)<0){ noteSay(isec,"note-err","Could not find that image in the note - reload the page."); return; }
    if(!window.confirm("Remove this image from the note? The words stay, and the item stays where it is."+
        (inote.getAttribute("data-kind")==="instruction"
          ? " The instruction counts as changed, so "+isec.getAttribute("data-notes-doc")+" is shown it again."
          : ""))){return;}
    idel.disabled=true;
    var ntext=raw.replace(mark,"[image removed]").trim();
    notePost({token:NOTE_TOKEN,doc:isec.getAttribute("data-notes-doc"),
              id:isec.getAttribute("data-notes-id"),nid:inote.getAttribute("data-nid"),text:ntext,
              bg:1},
      function(j){ var _for=isec.getAttribute("data-notes-for");
        afterWrite(isec.getAttribute("data-notes-doc"),isec.getAttribute("data-notes-id"),_for,
          function(){ noteLandingWarnings(_for,"",j,"Image removed"); }); },
      function(why){ idel.disabled=false; noteSay(isec,"note-err","Image NOT removed \u2014 "+why); });
    return; }
  var nd=ev.target.closest&&ev.target.closest("[data-note-del]");
  if(nd){
    
    var nn=nd.closest(".note"), sec=noteSection(nd);
    /* ⛔ DELETING AN INSTRUCTION DOES NOT UNSAY IT. That session may already have been
       shown it at a turn boundary and already be acting on it, so the confirm says what
       deleting does and does not undo rather than implying a recall. */
    
    var docLevel=nn.getAttribute("data-kind")==="instruction"&&docInsItem(nd.closest(".entry"));
    var msg=docLevel
      ? docInsConfirm(sec.getAttribute("data-notes-doc"),
                      +(nd.closest(".entry").getAttribute("data-ins-count")||0))
      : nn.getAttribute("data-kind")==="instruction"
      ? "Delete this instruction? It is removed from the OrchDoc, and "+
        sec.getAttribute("data-notes-doc")+" stops being shown it. If they have already "+
        "been shown it they may be acting on it now - deleting it does not tell them to "+
        "stop."
      : "Delete this note? It is removed from the OrchDoc itself.";
    if(!window.confirm(msg)){return;}
    nd.disabled=true;
    notePost({token:NOTE_TOKEN,doc:sec.getAttribute("data-notes-doc"),
              id:sec.getAttribute("data-notes-id"),nid:nn.getAttribute("data-nid"),
              del:true,bg:1},
      function(j){ var _for=sec.getAttribute("data-notes-for");
        afterWrite(sec.getAttribute("data-notes-doc"),sec.getAttribute("data-notes-id"),_for,
          function(){ noteLandingWarnings(_for,"",j,"Deleted"); }); },
      function(why){ nd.disabled=false;
        noteSay(sec,"note-err","Not deleted \u2014 "+(docLevel?docInsWhy(why):why)); });
    return; }
  var ns=ev.target.closest&&ev.target.closest("[data-note-save]");
  if(ns){
    var box=ns.closest(".note-edit"), sec2=noteSection(ns);
    var nta=box.querySelector("textarea"), txt=nta.value;
    if(!txt.trim()){ noteSay(box,"note-err","Nothing to save \u2014 the note is empty.");
      return; }
    /* o9cL1: into the outbox; the editor closes now and the note shows "sending". If the
       writer refuses it, the text comes back in a box with the reason (obRefused). */
    obSave(box,sec2,"/note",txt);
    return; }
  var f=ev.target.closest&&ev.target.closest("[data-filter]");
  if(f){ filt=f.getAttribute("data-filter");
    document.querySelectorAll("[data-filter]").forEach(function(b){
      b.setAttribute("aria-pressed",b===f?"true":"false");});
    apply(); return; }
  var m=ev.target.closest&&ev.target.closest("[data-col-set]");
  if(m){ document.body.setAttribute("data-col",m.getAttribute("data-col-set"));
    document.querySelectorAll("[data-col-set]").forEach(function(b){
      b.setAttribute("aria-pressed",b===m?"true":"false");}); return; }
  if(ev.target.closest&&ev.target.closest("#scope-all")){
    var on=scopeAll.getAttribute("aria-pressed")==="true";
    scopeAll.setAttribute("aria-pressed",on?"false":"true"); apply(); return;}
});
q.addEventListener("input",apply);
ownerSel.addEventListener("change",apply);
pane.addEventListener("input",function(ev){
  var t=ev.target, b=t&&t.tagName==="TEXTAREA"&&t.closest(".note-edit");
  if(b){ draftPut(b); }
});
window.addEventListener("storage",function(e){
  if(!e.key||e.key.indexOf(DK)===0){ barReset(); } });
/* Asks before leaving ONLY when the browser refused to store the text, because only then is
   leaving a loss. With a stored draft the text comes back on the next visit. */
window.addEventListener("beforeunload",function(e){
  var lost=false;
  pane.querySelectorAll(".note-edit").forEach(function(b){
    var ta=b.querySelector("textarea");
    if(ta&&unsaved(b)&&!draftPut(b,ta.readOnly)){ lost=true; } });
  if(lost){ e.preventDefault(); e.returnValue=""; }
});
window.addEventListener("hashchange",function(ev){
  
  try{
    var asked=new URL(ev.newURL).hash;
    if(asked&&spyHash&&location.hash===spyHash&&asked!==location.hash){
      history.replaceState(null,"",asked); }
  }catch(e){}
  var h=(location.hash||"").replace(/^#/,""); var p=h.split("/");
  if(p.length===2&&p[0]===view&&document.getElementById("entry-"+p[0]+"-"+p[1])){
    jump("entry-"+p[0]+"-"+p[1]); }
  else { if(STALE[view]){ forget(view); delete STALE[view]; view=null; } route(); }
});
/* Scroll position is saved too, so a reload between entries comes back between them. */
var _st=null;
pane.addEventListener("scroll",function(){
  if(_st){return;}
  _st=setTimeout(function(){_st=null;savePlace();},250);
});

// typing". The old handler also fired INSIDE the note boxes and the script editor, so a "/" typed
// there was eaten and the cursor jumped away. Now it never fires while any text field has focus.
function typingInField(el){
  if(!el||el===document.body){return false;}
  var t=(el.tagName||"").toUpperCase();
  return t==="INPUT"||t==="TEXTAREA"||t==="SELECT"||el.isContentEditable;
}
document.addEventListener("keydown",function(e){
  if(e.key==="\\"&&!e.ctrlKey&&!e.metaKey&&!e.altKey&&document.activeElement!==q
     &&!typingInField(document.activeElement)){e.preventDefault();q.focus();q.select();}
  if(e.key==="Escape"&&document.activeElement===q){q.value="";apply();q.blur();}
});
noteWire(pane);
route(true);
/* o9cL1: what the outbox still holds from before this page was loaded goes now. */
obAll().forEach(function(r){ obSend(r); });
})();
"""


# ==========================================================================================
# a background worker puts it on main afterwards (`reader_landing.py`). So the page has to say
# where that stands, and a failure has to stay on screen until it is fixed, never only in a
# log. It polls GET /landing: every 1.5 s while a save is on its way, every 5 s while one is
# failing, every 15 s otherwise, and every 60 s while the tab is hidden. Bottom LEFT, so it
# never covers the unsaved-drafts bar at the bottom right.
#
#   "Saving to GitHub (2)"                            two saves are not on main yet
#   "Saved"                                           shown for 8 s after the last one lands
#   "Not saved to GitHub yet: <reason>, retrying"     a landing failed; click for each one
#   "landing off (--no-land)"                         this reader lands nothing
# ==========================================================================================
LANDING_CSS = r"""
#landing-ind{position:fixed;left:1rem;bottom:1rem;z-index:59;
  max-width:min(30rem,calc(100vw - 2rem));background:var(--panel);color:var(--ink);
  border:1px solid var(--line);border-left:4px solid var(--gold);border-radius:8px;
  box-shadow:0 6px 24px rgba(0,0,0,.18);font-size:.75rem}
#landing-ind[hidden]{display:none}
#landing-ind .li-head{display:block;width:100%;margin:0;padding:.35rem .7rem;border:0;
  background:none;color:inherit;font:inherit;font-weight:650;text-align:left;cursor:pointer}
#landing-ind .li-head:disabled{cursor:default}
#landing-ind .li-head:focus-visible{outline:2px solid var(--gold);outline-offset:-2px}
#landing-ind .li-more{padding:0 .7rem .5rem;max-height:40vh;overflow:auto}
#landing-ind .li-more[hidden]{display:none}
#landing-ind .li-row{margin:.3rem 0 0;color:var(--ink);overflow-wrap:anywhere}
#landing-ind.li-ok{border-left-color:var(--done)}
#landing-ind.li-ok .li-head{color:var(--done)}
#landing-ind.li-fail{border-color:var(--openline);border-left-color:var(--open);
  background:var(--openbg)}
#landing-ind.li-fail .li-head{color:var(--open)}
#landing-ind.li-off{border-left-color:var(--mut)}
#landing-ind.li-off .li-head{color:var(--mut)}

.note-state{display:inline-block;margin:0 .45rem 0 0;padding:0 .4rem;border:1px solid;
  border-radius:999px;font-size:.7rem;font-weight:700;letter-spacing:0;text-transform:none;
  line-height:1.5;vertical-align:baseline;white-space:nowrap}
.note-state[data-state="sending"]{color:var(--mut)}
.note-state[data-state="saved"]{color:var(--goldink)}
.note-state[data-state="landed"]{color:var(--done)}
.note-state[data-state="failing"]{color:var(--open);background:var(--openbg);
  white-space:normal;overflow-wrap:anywhere;max-width:100%;border-radius:.5rem}
.note-state[data-state="off"]{color:var(--mut)}
.et .note-state{margin:0 0 0 .5rem;font-size:.68rem;vertical-align:middle}

#copy-banner{flex:0 0 auto;padding:.45rem .9rem;background:var(--openbg);color:var(--ink);
  border-bottom:1px solid var(--openline);border-left:4px solid var(--open);font-size:.85rem;
  line-height:1.45;overflow-wrap:anywhere}
#copy-banner[hidden]{display:none}
#copy-banner .cb-top{display:flex;align-items:center;gap:.5rem}
#copy-banner .cb-sum{flex:1 1 auto;min-width:0;display:-webkit-box;-webkit-line-clamp:2;
  -webkit-box-orient:vertical;overflow:hidden}
#copy-banner .cb-btn{flex:0 0 auto;font:inherit;color:inherit;background:transparent;
  border:1px solid var(--openline);border-radius:.35rem;padding:0 .5rem;line-height:1.4;
  cursor:pointer}
#copy-banner .cb-btn:hover{text-decoration:underline}
#copy-banner .cb-btn:focus-visible{outline:2px solid var(--open);outline-offset:1px}
#copy-banner .cb-x{font-size:1.2em;line-height:1.1;padding:0 .45rem}
#copy-banner .cb-list{margin:.3rem 0 .1rem;padding-left:1.2rem;font-size:.8rem;
  max-height:30vh;overflow-y:auto}
#copy-banner .cb-list[hidden]{display:none}
#copy-banner .cb-list li{margin:.1rem 0}
#copy-banner .cb-note{list-style:none;margin-left:-1.2rem;color:var(--mut)}
"""

LANDING_JS = r"""
(function(){
if(typeof NOTE_TOKEN==="undefined"||!NOTE_TOKEN){return;}
var box=document.createElement("div");
box.id="landing-ind"; box.hidden=true;
box.setAttribute("role","status"); box.setAttribute("aria-live","polite");
var head=document.createElement("button"); head.type="button"; head.className="li-head";
var more=document.createElement("div"); more.className="li-more"; more.hidden=true;
box.appendChild(head); box.appendChild(more); document.body.appendChild(box);
var timer=null, inflight=false, busy=false, poked=false, savedTill=0;
function clip(s,n){ s=String(s==null?"":s); return s.length>n?s.slice(0,n-3)+"...":s; }
function times(n){ return n+" time"+(n===1?"":"s"); }
head.addEventListener("click",function(){
  more.hidden=!more.hidden; head.setAttribute("aria-expanded",more.hidden?"false":"true");
});

var cb=document.createElement("div");
cb.id="copy-banner"; cb.hidden=true; cb.setAttribute("role","status");
var hdr=document.querySelector("header.top");
if(hdr&&hdr.parentNode){ hdr.parentNode.insertBefore(cb,hdr.nextSibling); }
else{ document.body.insertBefore(cb,document.body.firstChild); }
function two(n){ return ("0"+n).slice(-2); }
function when(t){
  var d=new Date(t*1000), hm=two(d.getHours())+":"+two(d.getMinutes());
  return d.toDateString()===new Date().toDateString()?hm:
    d.toLocaleDateString(undefined,{month:"short",day:"numeric"})+" "+hm;
}
function many(n,w){ return n+" "+w+(n===1?"":"s"); }
function copyBanner(list){
  var rows=[];
  (list||[]).forEach(function(c){
    if(!c.alert){return;}
    var who=(c.slug?c.slug+"'s doc":"The doc")+" on this computer ("+c.doc+")";
    var rowsBefore=rows.length;
    if(c.state==="stopped"){
      rows.push(who+" stopped following main"+
        (c.since?(c.older?" before ":" at ")+when(c.since):
         c.changed?" (it last changed here at "+when(c.changed)+")":"")+". "+
        (c.lacks?"It lacks "+(c.older?"at least ":"")+"the "+many(c.lacks,"change")+
                 " made to it on main since then":
                 "It lacks "+many(c.missing,"line")+" that main has")+
        (c.extra?", and has "+many(c.extra,"line")+" that main does not have":"")+".");
    } else if(c.state==="behind"){
      rows.push(who+" is "+many(c.lacks,"change")+" behind main"+
        (c.since?", since "+when(c.since):"")+". This page shows that copy. deliver.py "+
        "brings it up to date the next time it runs.");
    } else if(c.state==="sending"){
      rows.push(who+" lacks the "+many(c.lacks,"change")+" made to it on main"+
        (c.since?" since "+when(c.since):"")+". It is brought up to date once the saves "+
        "made here have reached main.");
    }
    if(rows.length>rowsBefore){
      rows[rows.length-1]={text:rows[rows.length-1],
        name:c.slug||String(c.doc||"doc").replace(/^ORCHESTRATOR-DECISIONS-/,"").replace(/\.md$/,""),
        stopped:c.state==="stopped"};
    }
  });
  /* One short line for all docs, a Details toggle for the per-doc facts, and an X. Closing is
     remembered (localStorage) against the set of docs and their numbers, so a new doc going
     stale, or a doc's numbers changing, shows the banner again. */
  var key=rows.map(function(r){return r.text;}).sort().join("\n");
  var closedKey=null;
  try{ closedKey=window.localStorage.getItem(COPY_CLOSED_KEY); }catch(e){}
  /* cb._closed holds the same key in memory, so when localStorage throws Close still lasts past
     the next poll. */
  cb.hidden=!rows.length||(closedKey!==null&&closedKey===key)||cb._closed===key;
  if(cb._was===key){return;}
  var wasOpen=cb._open===true;
  cb._was=key; cb.textContent="";
  if(!rows.length){return;}
  var names=rows.map(function(r){return r.name+(r.stopped?" (stopped)":"");});
  var anyStopped=rows.some(function(r){return r.stopped;});
  var top=document.createElement("div"); top.className="cb-top";
  var sum=document.createElement("span"); sum.className="cb-sum";
  sum.textContent=(rows.length===1?"1 doc here differs":rows.length+" docs here differ")+
    " from main: "+names.join(", ")+". What you see may be out of date; saves still go to main.";
  var det=document.createElement("button"); det.type="button"; det.className="cb-btn cb-det";
  det.textContent="Details"; det.setAttribute("aria-expanded",wasOpen?"true":"false");
  var x=document.createElement("button"); x.type="button"; x.className="cb-btn cb-x";
  x.setAttribute("aria-label","Close"); x.title="Close"; x.textContent="×";
  top.appendChild(sum); top.appendChild(det); top.appendChild(x);
  var ul=document.createElement("ul"); ul.className="cb-list"; ul.hidden=!wasOpen;
  rows.forEach(function(r){
    var li=document.createElement("li"); li.textContent=r.text; ul.appendChild(li); });
  var note=document.createElement("li"); note.className="cb-note";
  note.textContent="This page shows the copy on this computer"+
    (anyStopped?", so a doc listed as stopped may be out of date, and nothing on this computer "+
    "brings it up to date by itself":"")+". What you save here still goes to main.";
  ul.appendChild(note);
  det.addEventListener("click",function(){
    ul.hidden=!ul.hidden; cb._open=!ul.hidden;
    det.setAttribute("aria-expanded",ul.hidden?"false":"true");
  });
  x.addEventListener("click",function(){
    cb._closed=key;
    try{ window.localStorage.setItem(COPY_CLOSED_KEY,key); }catch(e){}
    cb.hidden=true;
  });
  cb.appendChild(top); cb.appendChild(ul);
}
var COPY_CLOSED_KEY="orchdoc.copyBanner.closed";
function show(s){
  var state="", text="", lines=[];
  
  if(window.noteStatesApply){ try{ window.noteStatesApply(s); }catch(e){
    if(window.console){ console.error(e); } } }
  if(s&&s.ok===true){ copyBanner(s.copies); }
  if(!s||s.ok!==true){
    state="fail";
    text="Not saved to GitHub yet: the reader's server did not answer, retrying";
  } else if(s.enabled===false){
    state="off"; text=s.text||"landing off (--no-land)";
    lines.push("This reader was started with --no-land. Notes, instructions and Do-today "+
               "changes are saved on this computer only; nothing is committed or pushed.");
  } else {
    var f=s.failing||[], u=s.unreadable||[], n=s.pending||0, st=s.stuck||[];
    if(n>0||f.length){ busy=true; }
    else if(busy||poked){ busy=false; poked=false; savedTill=Date.now()+8000; }
    if(f.length||u.length){
      state="fail";
      text="Not saved to GitHub yet: "+clip(f.length?f[0].why:u[0].why,110)+", retrying";
      f.forEach(function(x){
        lines.push(x.what+": "+x.why+" (tried "+times(x.attempts)+
          (x.parked?"; later changes to other notes go ahead of it":"")+
          "; next try in "+x.next_try_in+" s)");
      });
      u.forEach(function(x){ lines.push(x.job+": "+x.why); });
      if(n>f.length+u.length){
        lines.push((n-f.length-u.length)+" more waiting to be saved to GitHub.");
      }
      lines.push("Each of these is saved on this computer. Only GitHub is behind.");
    } else if(n>0){
      state="busy"; text="Saving to GitHub ("+n+")";
      lines.push("Saved on this computer. Being committed and pushed to main, oldest first.");
    } else if(Date.now()<savedTill){
      state="ok"; text="Saved";
      if(s.last){ lines.push("Last saved to GitHub: "+s.last.what+" ("+s.last.detail+")"); }
    }
    if(st.length){
      if(!state||state==="ok"){
        state="fail";
        text="Not on GitHub: "+st.length+" note"+(st.length===1?"":"s")+
             " on this computer only";
      }
      st.forEach(function(x){
        lines.push((x.nid||"?")+" in "+x.doc+" (\""+clip(x.text,80)+"\"): "+x.why);
      });
    }
    
    var rf=(s.done||[]).filter(function(x){ return x.restored&&x.ago<300; });
    if(rf.length){
      if(!state||state==="ok"||state==="busy"){
        state="fail"; text="Not deleted: "+clip(rf[rf.length-1].error,110);
      }
      rf.forEach(function(x){ lines.push((x.nid||x.entry||x.job)+" in "+x.doc+": "+x.error); });
    }
  }
  box.hidden=!state;
  box.className=state?"li-"+state:"";
  head.textContent=text;
  head.disabled=!lines.length;
  if(!lines.length){ more.hidden=true; }
  head.setAttribute("aria-expanded",more.hidden?"false":"true");
  more.textContent="";
  lines.forEach(function(l){
    var d=document.createElement("div"); d.className="li-row"; d.textContent=l;
    more.appendChild(d);
  });
  if(timer){ clearTimeout(timer); }
  /* Fast while a save is on its way, slower while one is failing (its retry is seconds to
     minutes away), slow when there is nothing to report. */
  var ms=(state==="busy"||state==="ok")?1500:(state==="fail"&&(busy||!s||s.ok!==true))?5000:
         document.hidden?60000:15000;
  timer=setTimeout(poll,ms);
}

function poll(){
  if(inflight){ return; }
  inflight=true;
  fetchWithin("/landing",{cache:"no-store"},FETCH_POLL_MS)
    .then(function(s){ inflight=false; show(s); })
    .catch(function(){ inflight=false; show(null); });
}
/* A save just came back: look now, so "Saving to GitHub" shows straight away, and so "Saved"
   shows even when the landing finished before this asked. */
window.landingPoke=function(){ poked=true; poll(); };
document.addEventListener("visibilitychange",function(){ if(!document.hidden){ poll(); } });
poll();
})();
"""

CSS = CSS + LANDING_CSS
JS = FETCH_WITHIN_JS + JS + LANDING_JS

# See `rail_counts` for why. Asked when the page becomes visible again, when its window gets
# focus, and once a minute while it is visible; a hidden page asks nothing. Each answer is
# written into the badges' class, text and hover text only, so a click handler, the highlighted
# document and anything else in the list are left as they are.
DOC_COUNTS_JS = r"""
(function(){
var railD=document.getElementById("rail-docs"), inflight=false, asked=0;
if(!railD){ return; }

var COUNTS_LIMIT_MS=15000;
function setBadge(b,cls,label,title){
  if(!b){ return; }
  b.className="badge"+(cls?" "+cls:"");
  b.textContent=label;
  if(title!==undefined){ b.setAttribute("title",title); }
}

function syncDocs(docs){
  var want=Object.keys(docs), byName={}, cur=(location.hash||"").replace(/^#/,"").split("/")[0];
  var btns=[].slice.call(railD.querySelectorAll(".dbtn[data-doc]")).filter(function(b){
    return b.getAttribute("data-doc").indexOf("__")!==0&&b.getAttribute("data-doc")!=="rule"; });
  if(want.join(" ")===btns.map(function(b){ return b.getAttribute("data-doc"); }).join(" ")){
    return; }
  var head=btns.length?btns[0].previousSibling:null;
  if(!head){ [].forEach.call(railD.querySelectorAll(".railhead"),function(h){
    if(h.textContent==="Documents"){ head=h; } }); }
  btns.forEach(function(b){ var s=b.getAttribute("data-doc");
    if(Object.prototype.hasOwnProperty.call(docs,s)){ byName[s]=b; }
    else if(b.parentNode){ b.parentNode.removeChild(b); } });
  var prev=head;
  want.forEach(function(s){
    var b=byName[s];
    if(!b){
      b=document.createElement("button"); b.type="button"; b.className="dbtn";
      b.setAttribute("data-doc",s); b.setAttribute("aria-current",s===cur?"true":"false");
      var nm=document.createElement("span"); nm.className="nm"; nm.textContent=s;
      var g=document.createElement("span"); g.className="badge";
      b.appendChild(nm); b.appendChild(g);
      railD.insertBefore(b,prev?prev.nextSibling:null);
    }
    prev=b;
  });
}
function put(s){
  var docs=s.docs||{};
  /* An answer that names no document at all is not taken as "every one was removed". */
  if(Object.keys(docs).length){ syncDocs(docs); }
  railD.querySelectorAll(".dbtn[data-doc]").forEach(function(btn){
    var v=docs[btn.getAttribute("data-doc")];
    if(v){ setBadge(btn.querySelector(".badge"),v[0],v[1],v[2]); }
  });
  if(typeof s.plate==="number"){
    setBadge(railD.querySelector('.dbtn[data-doc="__plate__"] .badge'),
             s.plate?"":"zero",String(s.plate));
  }
  if(typeof s.today==="number"){
    setBadge(document.getElementById("today-badge"),s.today?"":"zero",String(s.today));
  }
}
function refresh(){
  if(inflight||document.hidden){ return; }
  inflight=true;
  var me=++asked, ctl=window.AbortController?new AbortController():null;
  var limit=setTimeout(function(){
    if(me===asked){ inflight=false; }
    if(ctl){ ctl.abort(); } },COUNTS_LIMIT_MS);
  fetch("/doc-counts",{cache:"no-store",signal:ctl?ctl.signal:undefined})
    .then(function(r){ return r.ok?r.json():null; })
    .then(function(s){ clearTimeout(limit);
      if(me!==asked){ return; }       /* a newer refresh was sent: its answer is the one shown */
      inflight=false; if(s){ put(s); } })
    .catch(function(){ clearTimeout(limit); if(me===asked){ inflight=false; } });
}
document.addEventListener("visibilitychange",function(){ if(!document.hidden){ refresh(); } });
window.addEventListener("focus",refresh);
setInterval(refresh,60000);
})();
"""

JS = JS + DOC_COUNTS_JS


# ⭐ ONE COPY BUTTON, on both pages. It copies the adjacent <code>'s own text, so what lands on
# the clipboard is exactly what is on screen. `navigator.clipboard` needs a secure context and
# 127.0.0.1 is one; the fallback selects the text so Ctrl+C still works where it is refused.
COPY_JS = r"""
(function(){
document.addEventListener("click",function(ev){
  var b=ev.target.closest&&ev.target.closest("[data-copy]"); if(!b){return;}
  var w=b.closest(".cpw"), c=w?w.querySelector("code"):null; if(!c){return;}
  var t=c.textContent;
  function done(ok){ b.textContent=ok?"copied":"press Ctrl+C";
    setTimeout(function(){b.textContent="copy";},1600); }
  function fallback(){ var ok=false;
    try{ var r=document.createRange(); r.selectNodeContents(c); var s=window.getSelection();
      s.removeAllRanges(); s.addRange(r); ok=document.execCommand("copy"); }catch(e){ ok=false; }
    done(ok); }
  try{ if(navigator.clipboard&&navigator.clipboard.writeText){
      navigator.clipboard.writeText(t).then(function(){done(true);},fallback); }
    else{ fallback(); } }catch(e){ fallback(); }
});
})();
"""


# ==========================================================================================
# THE SCRIPTS VIEW AND EDITOR - client half.
#
# ⛔ THE EDITOR IS THE MARKDOWN SOURCE, NEVER A contenteditable RENDERED VIEW. Scripts carry
# `<!-- slug: ... -->` and `<!-- training-version: ... -->` markers. A rendered-view editor
# would drop them invisibly, and the script import matches scripts BY their slug marker - so a
# save would break an import while looking perfect on screen.
#
# ⛔ THE PAGE NAMES A SCRIPT BY THE ID THE SERVER ISSUED AND NOTHING ELSE. It never sends a
# path, a file name or a destination; where a save goes is decided on the server.
#
# NOT SAVED in words, and the text is also kept in this browser's storage between keystrokes,
# so a crash, a reload or a closed tab offers it back the next time that editor opens.
# ==========================================================================================
SCRIPTS_JS = r"""
(function(){
var pane=document.getElementById("pane"), railE=document.getElementById("rail-entries");
var SV={cur:null, ed:null, seq:0, cmp:null};
var LS="claude-orchdoc-reader-script-unsaved:";
function esc(t){ var d=document.createElement("div"); d.textContent=t==null?"":String(t);
  return d.innerHTML; }

function call(u,opt,ok,bad){
  var o=Object.assign({},opt||{}), ms=o.limit||(o.method==="POST"?FETCH_WRITE_MS:FETCH_LOAD_MS);
  delete o.limit;
  fetchWithin(u,o,ms,function(r){
      return r.json().then(function(j){return {s:r.status,j:j};},
                           function(){return {s:r.status,j:null};}); })
    .then(function(x){ if(x.j&&x.j.ok){ok(x.j);}
      else{bad((x.j&&x.j.error)||("the server refused it ("+x.s+")"),x.s);} })
    .catch(function(e){bad("could not reach the reader's server: "+e,0);});
}

function tokenFromHtml(h){
  var m=/var NOTE_TOKEN=("(?:[^"\\]|\\.)*");/.exec(h||"");
  if(!m){ return ""; }
  try{ var t=JSON.parse(m[1]); return typeof t==="string"?t:""; }catch(e){ return ""; }
}
function freshToken(ok,bad){
  fetchWithin("/",{cache:"no-store"},FETCH_LOAD_MS,function(r){ return r.text(); })
    .then(function(h){ var t=tokenFromHtml(h); if(t){ ok(t); }else{ bad(); } })
    .catch(function(){ bad(); });
}
function postOnce(u,body,ok,bad){
  call(u,{method:"POST",headers:{"Content-Type":"application/json"},
          body:JSON.stringify(body)},ok,bad);
}
function post(u,body,ok,bad){
  postOnce(u,body,ok,function(why,s){
    if(s===403&&/bad or missing token/.test(why||"")&&body&&typeof body.token==="string"){
      freshToken(function(t){
        NOTE_TOKEN=t;
        postOnce(u,Object.assign({},body,{token:t}),ok,bad);
      },function(){ bad(why,s); });
    }else{ bad(why,s); }
  });
}
function lsGet(k){ try{var v=window.localStorage.getItem(LS+k); return v?JSON.parse(v):null;}
  catch(e){return null;} }
function lsSet(k,v){ try{window.localStorage.setItem(LS+k,JSON.stringify(v));}catch(e){} }
function lsDel(k){ try{window.localStorage.removeItem(LS+k);}catch(e){} }

function noteDirty(){ var t=document.querySelector("[data-script-note-text]"); return !!(t&&t.value.trim()); }
function dirty(){ return !!(SV.ed&&SV.ed.ta.value!==SV.ed.saved)||cvDirty()||noteDirty(); }
window.scriptDirty=dirty;
window.scriptHash=function(){ return SV.cmp?"#scripts/"+SV.cmp+"/compare":
  SV.cur?"#scripts/"+SV.cur.id:"#scripts"; };
window.scriptClosed=function(){ SV.ed=null; SV.cur=null; SV.cmp=null;
  document.body.classList.remove("wide"); };
window.addEventListener("beforeunload",function(e){
  if(dirty()){ e.preventDefault(); e.returnValue=""; return ""; } });

var TKEY="claude-orchdoc-reader-scripts-open", tMem=null;
function tRead(){ try{ var v=window.localStorage.getItem(TKEY); if(v===null){return null;}
  var a=JSON.parse(v); return Array.isArray(a)?a:null; }catch(e){ return null; } }
function tWrite(a){ try{ window.localStorage.setItem(TKEY,JSON.stringify(a)); }catch(e){} }
function tSetOpen(d,on){ d.classList.toggle("open",!!on);
  var b=d.firstElementChild; if(b){ b.setAttribute("aria-expanded",on?"true":"false"); } }
function tSave(){ var a=[]; railE.querySelectorAll(".tdir.open").forEach(function(d){
  a.push(d.getAttribute("data-tpath")); }); tMem=a; tWrite(a); }
function tInit(){
  var tr=railE.querySelector(".stree:not([data-init])"); if(!tr){return;}
  tr.setAttribute("data-init","1");
  var saved=tRead(); if(saved===null){ saved=tMem; }
  tr.querySelectorAll(".tdir").forEach(function(d){
    tSetOpen(d, saved===null ? d.getAttribute("data-tdepth")==="0"
                             : saved.indexOf(d.getAttribute("data-tpath"))>=0); });
  var c=tr.querySelector(".ebtn.cur"); if(c){ tReveal(c); }
}
function tReveal(b){ var d=b.closest(".tdir"); while(d){ tSetOpen(d,true);
  d=d.parentNode.closest?d.parentNode.closest(".tdir"):null; } }
railE.addEventListener("click",function(e){
  var t=e.target.closest?e.target.closest("[data-tog]"):null; if(!t){return;}
  var d=t.parentNode; tSetOpen(d,!d.classList.contains("open")); tSave(); });
try{ new MutationObserver(tInit).observe(railE,{childList:true}); }catch(e){}
tInit();

function frCount(){
  railE.querySelectorAll(".tdir[data-fr]").forEach(function(d){
    var all=d.querySelectorAll(".frchk:not([disabled])").length,
        on=d.querySelectorAll('.frchk[aria-checked="true"]').length,
        n=d.firstElementChild&&d.firstElementChild.querySelector(".tname");
    var tot=d.querySelectorAll(".frchk").length;
    if(n){ n.textContent=d.getAttribute("data-frbase")+" ("+on+" of "+tot+" reviewed)"; }
  });
}
function frPaint(b,on){
  b.setAttribute("aria-checked",on?"true":"false");
  b.querySelector(".frtick").innerHTML=on?"&#10003;":"";
  b.title=on?"Reviewed - click to mark not reviewed":"Not reviewed - click to mark reviewed";
  var row=b.closest(".frrow"); if(row){ row.classList.toggle("frdone",on); }
}
function frToggle(b){
  if(b.disabled||!NOTE_TOKEN){ return; }
  var on=b.getAttribute("aria-checked")!=="true", v=b.getAttribute("data-fr-ver");
  frPaint(b,on); frCount(); b.disabled=true;
  post("/full-rewrite/review",{token:NOTE_TOKEN,key:b.getAttribute("data-fr-key"),reviewed:on,
    ver:v===""||v===null?null:parseInt(v,10)},function(){
    b.disabled=false;
    var nw=b.closest(".frrow")&&b.closest(".frrow").querySelector(".frnewer");
    if(nw&&on){ nw.parentNode.removeChild(nw); }
    if(window.readerForget){ window.readerForget("__scripts__"); }
  },function(why){
    b.disabled=false; frPaint(b,!on); frCount(); b.title="Not saved: "+why;
  });
}
function markNav(id){
  railE.querySelectorAll(".ebtn.cur").forEach(function(b){b.classList.remove("cur");});
  
  var all=railE.querySelectorAll('[data-script="'+CSS.escape(id)+'"]'), real=null, first=null;
  Array.prototype.forEach.call(all,function(b){
    
    if(!b.classList.contains("ebtn")&&b.closest){ b=b.closest(".ebtn")||b; }
    b.classList.add("cur"); if(!first){ first=b; }
    if(!real&&!b.hasAttribute("data-nd")){ real=b; } });
  if(real||first){ tReveal(real||first); }
}
function openScript(id){
  if(dirty()&&!window.confirm("You have unsaved edits in this script. Leave them? (A copy "+
      "stays in this browser and is offered back the next time you open that editor.)")){
    return; }
  SV.ed=null; SV.cmp=null; document.body.classList.remove("wide");
  var my=++SV.seq;
  pane.innerHTML='<div class="empty">loading...</div>';
  call("/script/"+encodeURIComponent(id),null,function(j){
    if(my!==SV.seq){return;}
    SV.cur=j; paintRead(j); markNav(j.id);
    history.replaceState(null,"","#scripts/"+j.id);
    if(window.readerSetCol){ window.readerSetCol("read"); }
  },function(why){
    if(my!==SV.seq){return;}
    pane.innerHTML='<div class="note"><b>Could not open that script.</b> '+esc(why)+'</div>';
  });
}
window.openScript=openScript;

function openScriptPath(segs,again){
  var parts=[], bad=!segs||!segs.length;
  (segs||[]).forEach(function(s){ var d;
    try{ d=decodeURIComponent(s); }catch(e){ bad=true; return; }
    if(!d||d==="."||d===".."||/[\\/]/.test(d)){ bad=true; }
    parts.push(d); });
  if(bad){ pane.innerHTML='<div class="note"><b>Could not open that script.</b> That link '+
      "does not name a file inside the document library.</div>"; return; }
  var rel=parts.join("/"), hit=null, fold=null;
  railE.querySelectorAll("[data-srel]").forEach(function(el){
    var r=el.getAttribute("data-srel");
    if(!hit&&r===rel){ hit=el; }
    if(!fold&&r&&r.toLowerCase()===rel.toLowerCase()){ fold=el; } });
  hit=hit||fold||renamedRow(parts);
  /* A Reader tab open since before the script was filed holds the old list (`cache`): the
     link names a file added after it. Ask for the list again, once, before saying it is gone. */
  if(!hit&&!again){
    if(window.readerForget){ window.readerForget("__scripts__"); }
    fetch("/doc/__scripts__").then(function(r){ return r.json(); }).then(function(d){
      var on=document.querySelector('.dbtn[data-doc="__scripts__"][aria-current="true"]');
      if(on&&d&&d.nav){ railE.innerHTML=d.nav; }
      openScriptPath(segs,true);
    }).catch(function(){ openScriptPath(segs,true); });
    return; }
  if(!hit){ pane.innerHTML='<div class="note"><b>Could not open that script.</b> No file at <code>'+
      esc(rel)+"</code> is in the document library right now - it may have been renamed or "+
      "moved. Reload, or find it in the list.</div>"; return; }
  openScript(hit.getAttribute("data-script"));
}
window.openScriptPath=openScriptPath;

function renamedRow(parts){
  var dir=parts.slice(0,-1).join("/"), nm=parts[parts.length-1], seen={}, hits=[];
  railE.querySelectorAll("[data-srel][data-snk]").forEach(function(el){
    var r=el.getAttribute("data-srel"), k=el.getAttribute("data-snk"),
        rest=el.getAttribute("data-srest"), i=r.lastIndexOf("/");
    if(seen[r]||(i<0?"":r.slice(0,i))!==dir||!k||!rest){ return; }
    if(nm.length<k.length+rest.length||nm.slice(0,k.length)!==k||
       nm.slice(nm.length-rest.length)!==rest){ return; }
    var mid=nm.slice(k.length,nm.length-rest.length);
    if(mid===""||/^ [^ \/]+$/.test(mid)){ seen[r]=1; hits.push(el); } });
  return hits.length===1?hits[0]:null;
}

function saveSays(j){
  var e=j.edit;
  
  var kept=/ \(in app\)\.md$/i.test(j.name||"")?"This is the text in the app; it is never "+
    "changed here.":"This draft is never changed.";
  if(e.mode==="update-self"){ return "Saving updates this rewrite. What it says now is kept "+
    "first, as a snapshot in .edit-history."; }
  if(e.mode==="update-rewrite"){ return kept+" Saving updates your "+
    "rewrite of it, "+e.target_name+", and keeps what that says now as a snapshot first."; }
  return kept+" Saving creates your rewrite of it: "+e.target_name+".";
}

function notesInner(notes){
  return (notes||[]).map(function(n){
    return '<div class="sv-note" data-script-note-row>'+esc(n.text)+'<span class="sv-nwhen">'+
      esc(String(n.at||"").replace("T"," ").slice(0,16))+(n.source==="phone"?" (from the phone)":"")+'</span></div>';
  }).join("");
}
function notesHtml(j){
  var n=j.notes||[];
  return '<details class="sv-notes" data-script-notes'+(n.length?' open':'')+'><summary>Notes on this script (<span data-nnote-count>'+
    n.length+'</span>)</summary><div data-nnote-list>'+notesInner(n)+'</div>'+
    (NOTE_TOKEN?'<textarea data-script-note-text aria-label="Note on this script" placeholder="Your note"></textarea>'+
     '<div class="note-acts"><button type="button" class="nbtn primary" data-script-note-save>Save note</button>'+
     '<span class="sv-hint" data-script-note-say role="status"></span></div>':'')+'</details>';
}
function noteSave(){
  var j=SV.cur, box=document.querySelector("[data-script-notes]"); if(!j||!box||!NOTE_TOKEN){return;}
  var ta=box.querySelector("[data-script-note-text]"), say=box.querySelector("[data-script-note-say]"),
      btn=box.querySelector("[data-script-note-save]"), txt=ta.value;
  if(!txt.trim()){ say.textContent="Write something first."; return; }
  var id=j.id; btn.disabled=true; say.textContent="Saving...";
  post("/script/note",{token:NOTE_TOKEN,id:id,text:txt},function(r){
    btn.disabled=false;
    if(!SV.cur||SV.cur.id!==id){return;}
    SV.cur.notes=r.notes||[];
    box.querySelector("[data-nnote-list]").innerHTML=notesInner(SV.cur.notes);
    box.querySelector("[data-nnote-count]").textContent=String(SV.cur.notes.length);
    if(ta.value===txt){ ta.value=""; }
    say.textContent="Saved.";
  },function(why){ btn.disabled=false; say.textContent="Not saved: "+why+" Your text is still here."; });
}
function paintRead(j){
  var e=j.edit, h='<div class="sv">';
  h+='<div class="sv-crumb">'+esc(j.folder)+'</div>';
  
  h+='<h2 class="sv-title">'+esc(j.name.replace(/\.md$/i,""))+(j.kind_label?' <span class="kb k-'+
     j.kind+'">'+esc(j.kind_label)+'</span>':'')+'</h2>';
  h+='<div class="sv-rel"><span class="cpw"><code>'+esc(j.rel)+'</code><button type="button" '+
     'class="cpbtn" data-copy title="Copy this path">copy</button></span></div>';
  /* Provenance line: built AND escaped on the server (render_prov_line), inserted as is. */
  h+=j.prov_html||"";
  if(j.same_text){
    
    var stf=j.same_text.file||j.same_text.name;
    h+='<p class="sv-pair"><span class="sbadge sb-same" title="'+
       esc("Same text as "+stf).replace(/"/g,"&quot;")+
       '">same text as '+esc((/\bv\d+\b/i.exec(j.same_text.label)||[j.same_text.label])[0])+
       '</span> '+esc(stf)+'</p>'; }
  if(j.pair){
    h+='<p class="sv-pair">'+(j.kind==="rewrite"?"The agent draft this rewrites: ":
       "Your rewrite of this draft: ")+'<button type="button" class="slink" data-script="'+
       esc(j.pair.id)+'">'+esc(j.pair.name)+'</button> &middot; <button type="button" '+
       'class="slink" data-script-compare="1">show what changed between them</button>'+
       ' &middot; <button type="button" class="slink" data-script-cmp="'+esc(j.id)+'">'+
       'Compare, and say why you changed it</button></p>';
  }
  if(j.variants&&j.variants.length){
    h+='<p class="sv-pair">Also rewritten as: '+j.variants.map(esc).join(", ")+'</p>'; }
  h+='<div class="note-acts sv-acts">';
  if(!NOTE_TOKEN){
    h+='<span class="sv-hint">This page was rendered read-only, so it cannot save.</span>'; }
  else {
    if(e.editable){
      h+='<button type="button" class="nbtn primary" data-script-edit>Edit</button>'; }
    
    if(j.approve&&j.approve.show){
      h+='<button type="button" class="nbtn" data-script-approve>Approve as Final</button>'+
         '<button type="button" class="nbtn" data-script-review>Approve &amp; Review</button>'; }
    h+='<span class="sv-hint">'+esc(e.editable?saveSays(j):e.why)+'</span>';
    /* A script text without the button says why (the human: never missing without a reason). */
    if(j.approve&&!j.approve.show&&j.approve.why){
      h+='<span class="sv-hint" data-approve-why>Approve: '+esc(j.approve.why)+'</span>'; } }
  if(j.approve&&j.approve.approved){
    h+='<span class="kb k-approved" data-approved-badge>Approved</span>'; }
  
  if(j.approve&&j.approve.final){
    h+='<span class="kb k-final" data-final-badge>Final</span>'; }
  h+='</div>'+notesHtml(j)+((NOTE_TOKEN&&j.approve&&j.approve.show)?
     '<div class="sv-rvnote" data-review-note role="status" aria-live="polite"></div>':'')+
     '<div class="sv-compare"></div><div class="md sv-body">'+j.html+'</div></div>';
  pane.innerHTML=h; pane.scrollTop=0;
}

var AP=null;
function apClose(){
  if(!AP){return;}
  var id=AP.reopen; document.removeEventListener("keydown",apKey,true);
  if(AP.el.parentNode){ AP.el.parentNode.removeChild(AP.el); }
  AP=null;
  if(id){ SV.ed=null; openScript(id); }
}
function apKey(ev){ if(ev.key==="Escape"&&AP&&!AP.busy){ ev.preventDefault(); apClose(); } }
function apPaint(title,body,btns){
  AP.box.innerHTML='<h3 id="ap-title">'+esc(title)+'</h3><div class="ap-body">'+body+
    '</div><div class="ap-btns">'+btns+'</div>';
  var f=AP.box.querySelector("[data-ap-cancel],[data-ap-close]");
  if(f){ f.focus(); }
}
function apName(r){ return esc(r.name.replace(/\.md$/i,""))+
  (r.version?" (version "+esc(r.version)+")":""); }
function apOpen(){
  var j=SV.cur; if(!j||!j.approve||!j.approve.show||!NOTE_TOKEN||AP){return;}
  var ov=document.createElement("div"); ov.className="ap-ov"; ov.setAttribute("data-ap-ov","1");
  ov.innerHTML='<div class="ap-box" role="dialog" aria-modal="true" aria-labelledby="ap-title"></div>';
  document.body.appendChild(ov);
  AP={el:ov,box:ov.firstChild,busy:false,id:j.id,reopen:null};
  document.addEventListener("keydown",apKey,true);
  apPaint("Approve this script?",'<p class="ap-wait">Checking what approving would do…</p>',
          '<button type="button" class="nbtn" data-ap-cancel>Cancel</button>');
  post("/script/approve-plan",{token:NOTE_TOKEN,id:j.id},function(r){
    if(!AP||AP.id!==j.id){return;}
    var nm=apName(r);
    if(r.refused){
      apPaint("This script cannot be approved",'<p><b>'+nm+'</b></p><p class="ap-no" '+
        'data-ap-reason>'+esc(r.reason||"The approve command refused it.")+'</p>',
        '<button type="button" class="nbtn primary" data-ap-close>Close</button>'); return; }
    var b='<p>You are approving <b>'+nm+'</b>.</p>';
    if(r.file){ b+='<p>File: <code data-ap-file>'+esc(r.file)+'</code></p>'; }
    b+=(r.folder_before&&r.folder_before!==r.folder_after)?
       '<p>It moves from <code>'+esc(r.folder_before)+'</code> to Final.</p>':
       '<p>It is already in <code>'+esc(r.folder_after||r.folder_before)+
       '</code>; its approval is recorded.</p>';
    if(r.app_change==="added"){ b+='<p>It goes into the app at the next release.</p>'; }
    else if(r.app_change==="text-changed"){
      b+='<p>This text replaces what the app plays now, at the next release.</p>'; }
    /* Cancel has focus, so a stray Enter never approves. */
    apPaint("Approve this script?",b,'<button type="button" class="nbtn primary" '+
      'data-ap-confirm>Approve</button><button type="button" class="nbtn" data-ap-cancel>'+
      'Cancel</button>');
  },function(why){
    if(!AP){return;}
    apPaint("Approving is not possible",'<p class="ap-no" data-ap-reason>'+esc(why)+'</p>',
      '<button type="button" class="nbtn primary" data-ap-close>Close</button>'); });
}

function rvRequest(){
  var j=SV.cur; if(!j||!j.approve||!j.approve.show||!NOTE_TOKEN){return;}
  var id=j.id, btn=document.querySelector("[data-script-review]");
  function say(t,bad){
    if(!SV.cur||SV.cur.id!==id){return;}
    var n=document.querySelector("[data-review-note]");
    if(n){ n.textContent=t; n.className="sv-rvnote"+(bad?" ap-no":""); }
    if(btn){ btn.disabled=false; } }
  if(btn){ btn.disabled=true; }
  post("/script-review-request/"+encodeURIComponent(id),{token:NOTE_TOKEN},function(r){
    say(r.message||"Sent for a review.",false);
  },function(why){ say("Not sent: "+why,true); });
}
function apConfirm(){
  if(!AP||AP.busy){return;}
  var my=AP; my.busy=true;
  apPaint("Approving…",'<p class="ap-wait">Approving, then saving to git…</p>',"");
  post("/script/approve",{token:NOTE_TOKEN,id:my.id},function(r){
    my.busy=false; if(AP!==my){return;}
    my.reopen=r.new_id||null;
    refreshNav();
    apPaint("Approved",'<p><span class="se-yes">Approved</span> - <b>'+
      esc(r.name.replace(/\.md$/i,""))+'</b> is now in <code>'+esc(r.folder_after||"Final")+
      '</code>.</p>'+
      (r.changed?'<p class="se-land" data-ap-land>'+landText(r.landing)+'</p>':
       '<p>Nothing needed to change on disk.</p>')+
      (r.changed?'<p class="se-land" data-ap-runner></p>':''),
      '<button type="button" class="nbtn primary" data-ap-close>Close</button>');
    if(r.landing&&r.landing.id){ apPoll(r.landing.id,my); }
  },function(why){
    my.busy=false; if(AP!==my){return;}
    apPaint("NOT approved",'<p class="ap-no" data-ap-reason>NOT approved - '+esc(why)+
      '</p><p>Nothing was written.</p>','<button type="button" class="nbtn primary" '+
      'data-ap-close>Close</button>'); });
}

function runnerPoll(jid,my,L){
  var n=0;
  function put(h){ var el=AP===my?my.box.querySelector("[data-ap-runner]"):null; if(el){ el.innerHTML=h; } }
  function link(S){ return S.pr_url?' <a href="'+esc(S.pr_url)+'" target="_blank" rel="noopener" data-ap-pr>App PR'+
    (S.pr_number?' #'+esc(String(S.pr_number)):'')+'</a>':''; }
  var R=L.runner;
  if(!R){ return; }
  if(R.started===false){ put('<span class="se-no">App library run NOT started</span> - '+esc(R.error||"")+
    ' The approval itself is saved.'); return; }
  put('<span class="se-wait">App library run started…</span>');
  (function tick(){
    call("/library-sync-status?job="+encodeURIComponent(jid),{limit:FETCH_POLL_MS},function(S){
      if(AP!==my){ return; }
      /* No status file yet is normal for the first seconds of a first run: keep polling. */
      if(!S.known){ if(++n<20){ setTimeout(tick,3000); } else { put('App library runner status unknown.'); } return; }
      var mine=S.covers===true;
      if(mine&&(S.pr_url||S.final)){ put(esc(S.line)+link(S)); return; }
      if(++n<20){ put('<span class="se-wait">App library run started…</span> '+(mine?esc(S.line):'')); setTimeout(tick,3000); }
      else { put('App library run started. Its status shows on the Scripts page.'); }
    },function(){ put('App library runner status unknown.'); });
  })();
}
function apPoll(jid,my){
  var n=0;
  (function tick(){
    call("/script-landing/"+encodeURIComponent(jid),{limit:FETCH_POLL_MS},function(L){
      var el=AP===my?my.box.querySelector("[data-ap-land]"):null;
      if(el){ el.innerHTML=landText(L); }
      if(L.state==="queued"||L.state==="running"){ if(++n<400){ setTimeout(tick,1500); } }
      else { refreshNav(); if(L.state==="landed"){ runnerPoll(jid,my,L); } }
    },function(why){ var el=AP===my?my.box.querySelector("[data-ap-land]"):null;
      if(el){ el.innerHTML='<span class="se-no">Landing status unavailable</span> - '+esc(why)+
        '. The approval is on disk.'; } });
  })();
}
function compare(){
  var j=SV.cur; if(!j||!j.pair){return;}
  var box=pane.querySelector(".sv-compare"); if(!box){return;}
  var a=j.kind==="draft"?j.id:j.pair.id, b=j.kind==="draft"?j.pair.id:j.id;
  box.innerHTML='<div class="empty">comparing...</div>';
  call("/script-diff/"+encodeURIComponent(a)+"/"+encodeURIComponent(b),null,function(r){
    box.innerHTML='<div class="sv-cmp-h"><b>What changed</b> from '+esc(r.from)+' to '+
      esc(r.to)+': <span class="se-plus">+'+r.added+'</span> <span class="se-minus">−'+
      r.removed+'</span> lines <button type="button" class="nbtn" data-script-uncompare>'+
      'Hide</button></div>'+r.html;
  },function(why){ box.innerHTML='<div class="note-err"></div>';
    box.firstChild.textContent=why; });
}

/* ---- the editor ---- */
function status(kind,msg){
  var s=pane.querySelector(".se-status"); if(!s){return;}
  if(kind==="html"){ s.innerHTML=msg; return; }
  s.innerHTML='<div class="'+(kind==="err"?"note-err":"note-ok")+'"></div>';
  s.firstChild.textContent=msg;
}
function openEditor(){
  var j=SV.cur; if(!j||!j.edit.editable||!NOTE_TOKEN){return;}
  var e=j.edit;
  var start=(e.target_exists&&e.target_text!=null)?e.target_text:j.text;
  var bases=[{label:"the file you opened - "+j.name,text:j.text}];
  if(j.kind==="draft"&&e.target_exists){
    bases.push({label:"your rewrite as it was when you opened this - "+e.target_name,
                text:e.target_text}); }
  if(j.kind==="rewrite"&&j.pair_text!=null){
    bases.push({label:"the agent draft it rewrites - "+j.pair.name,text:j.pair_text}); }
  var key=j.id+":"+(e.target_sha||"new");
  var dest=e.target_exists?(e.mode==="update-self"?
      "this file; what it says now is kept as a snapshot first":
      "your existing rewrite; what it says now is kept as a snapshot first"):
      "a new file; the draft is never changed";
  var h='<div class="se">';
  h+='<div class="se-bar"><div class="se-what"><b>Editing</b> '+esc(j.name)+
     '<br><span class="se-dest">Saves to <code>'+esc(e.target_name)+'</code> - '+esc(dest)+
     '</span>'+(j.prov_html||"")+'</div><div class="se-btns"><span class="se-count" '+
     'title="Lines added and '+
     'removed against the version chosen under Changes"></span>'+
     '<button type="button" class="nbtn primary" data-se-save>Save</button>'+
     '<button type="button" class="nbtn se-lockbtn" data-se-lock aria-pressed="true" '+
     'title="Scroll the source and the preview together, and keep the preview on the line '+
     'you are editing">Lock scroll</button>'+
     '<button type="button" class="nbtn" data-se-wide aria-pressed="false">Wide</button>'+
     '<button type="button" class="nbtn" data-se-close>Close editor</button></div></div>';
  if(j.kind==="draft"&&e.target_exists){
    h+='<div class="se-banner">You already have a rewrite of this draft, so the editor starts '+
       'from <b>your rewrite</b> ('+esc(e.target_name)+') and you carry on with it. '+
       '<button type="button" class="nbtn" data-se-fromdraft>Start from the draft instead'+
       '</button></div>'; }
  h+='<div class="se-restore"></div><div class="se-status" aria-live="polite"></div>';
  h+='<div class="se-grid"><div class="se-left"><div class="se-edw"><div class="se-hl" '+
     'aria-hidden="true"></div><textarea class="se-src" spellcheck="true" '+
     'aria-label="Markdown source"></textarea></div><div class="note-hint">The markdown source, '+
     'markers included - the <code>slug</code> marker is how a script is matched on import, '+
     'so leave it in place. <b>Ctrl+S</b> saves.</div></div>';
  h+='<div class="se-right"><div class="se-tabs">'+
     '<button type="button" class="tbtn" data-se-tab="prev" aria-pressed="true">Preview'+
     '</button><button type="button" class="tbtn" data-se-tab="diff" aria-pressed="false">'+
     'Changes <span class="se-count2"></span></button>';
  if(bases.length>1){
    h+='<label class="se-basel">against <select class="se-base">'+bases.map(function(b,i){
      return '<option value="'+i+'">'+esc(b.label)+'</option>';}).join("")+'</select></label>'; }
  else { h+='<span class="se-basel">against '+esc(bases[0].label)+'</span>'; }
  h+='</div><div class="se-prev md"></div><div class="se-diff hid"></div></div></div></div>';
  pane.innerHTML=h; pane.scrollTop=0;
  var ta=pane.querySelector(".se-src");
  ta.value=start;
  var ed=SV.ed={ta:ta,saved:start,expect:e.target_exists?e.target_sha:"",bases:bases,base:0,
         key:key,t:null,busy:false,rseq:0,
         hl:pane.querySelector(".se-hl"),pv:pane.querySelector(".se-prev"),
         df:pane.querySelector(".se-diff"),lock:lockPref(),driver:ta,pend:{},raf:0,
         tops:null,anc:null,ro:null};
  pane.querySelector("[data-se-lock]").setAttribute("aria-pressed",ed.lock?"true":"false");
  var old=lsGet(key);
  if(old&&old.text&&old.text!==start){
    pane.querySelector(".se-restore").innerHTML='<div class="se-banner">This browser kept '+
      'unsaved text from '+esc(old.when||"an earlier visit")+'. <button type="button" '+
      'class="nbtn primary" data-se-restore>Put it back in the editor</button> '+
      '<button type="button" class="nbtn" data-se-discard>Discard it</button></div>'; }
  wire(ed);
  refresh(); toTop(ed);
}

function toTop(ed){
  var ta=ed.ta;
  ta.setSelectionRange(0,0);
  try{ ta.focus({preventScroll:true}); }catch(x){ ta.focus(); }
  ta.setSelectionRange(0,0);
  ta.scrollTop=0; ta.scrollLeft=0;
  setScroll(ed.pv,0); setScroll(ed.df,0);
  pane.scrollTop=0; ed.driver=ta;
  paintNow(ed);
}

/* ---- the editor: dropdown colouring behind the textarea ----
   ⛔ SCRIPT TEXT NEVER BECOMES HTML HERE: every character goes through escT. The line
   markers are empty spans, one at the start of every source line, so the scroll sync can
   read where each line sits after wrapping without measuring text. */
var DD_RE=/\[\[((?:(?!\[\[|\]\])[\s\S])*)\]\]/g;
/* {{{ Notes }}}: the same pairing as the server's _pn_paint - an open takes the first close
   after it, across lines and blank lines. */
var PN_RE=/\{\{\{[\s\S]*?\}\}\}/g;
function escT(s){ return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }
function hlHtml(t){
  var segs=[], last=0, m;
  DD_RE.lastIndex=0;
  while((m=DD_RE.exec(t))){
    if(m.index>last){ segs.push(["",t.slice(last,m.index)]); }
    segs.push(["dd-mk","[["]);
    m[1].split("|").forEach(function(p,k){
      if(k){ segs.push(["dd-mk","|"]); }
      if(p){ segs.push(["dd-op",p]); } });
    segs.push(["dd-mk","]]"]);
    last=DD_RE.lastIndex;
  }
  if(last<t.length){ segs.push(["",t.slice(last)]); }
  /* Each note's range, brackets included, is cut out of the segments above and given the
     "pn" class on top of the one it had, so a dropdown inside a note keeps both. The
     segments' text joins back to `t` exactly, so offsets into `t` are offsets into them. */
  var rs=[];
  PN_RE.lastIndex=0;
  while((m=PN_RE.exec(t))){ rs.push([m.index,PN_RE.lastIndex]); }
  if(rs.length){
    var cut=[], pos=0, r=0;
    segs.forEach(function(s){
      var a=pos, txt=s[1];
      pos+=txt.length;
      while(txt){
        while(r<rs.length&&rs[r][1]<=a){ r++; }
        var inN=r<rs.length&&rs[r][0]<=a;
        var w=r<rs.length?(inN?rs[r][1]:rs[r][0])-a:txt.length;
        if(w<=0||w>txt.length){ w=txt.length; }
        cut.push([inN?(s[0]?s[0]+" pn":"pn"):s[0], txt.slice(0,w)]);
        txt=txt.slice(w); a+=w;
      }
    });
    segs=cut;
  }
  var n=1, h=['<span class="se-ln" data-l="1"></span>'];
  segs.forEach(function(s){
    s[1].split("\n").forEach(function(p,k){
      if(k){ n++; h.push('\n<span class="se-ln" data-l="'+n+'"></span>'); }
      if(p){ h.push(s[0]?'<span class="'+s[0]+'">'+escT(p)+'</span>':escT(p)); } }); });
  /* A zero-width character so a trailing empty line still has height, as it does in the
     textarea. */
  h.push("​");
  return h.join("");
}
function paintNow(ed){
  ed.hl.innerHTML=hlHtml(ed.ta.value);
  ed.hl.scrollTop=ed.ta.scrollTop; ed.hl.scrollLeft=ed.ta.scrollLeft;
  ed.tops=null; ed.anc=null;
}

/* ---- the editor: lock-step scroll ----
   ⭐ SYNCED BY CONTENT, NOT BY SCROLL FRACTION. The source is monospace, wrapped and full of
   markup, so it is much longer than the preview and the same fraction lands on different
   text. Instead each preview block carries the source line it starts on (data-src-line), each
   source line's top is read from the marker spans behind the textarea, and the pairs become
   anchors: scrolling interpolates between one anchor and the next, so the preview moves
   smoothly at whatever rate that stretch of the script needs.
   ⭐ THE REFERENCE POINT is the top edge of each pane, sliding to the bottom edge over the
   last screen of scrolling - so both panes reach their ends together and nothing at the
   bottom of either one is unreachable.
   ⛔ NO LOOP: only the pane the reader is actually using (the "driver" - last pointed at,
   wheeled, touched or focused) propagates its scroll, and a scroll this code caused is
   recognised by its value and dropped. */
var LOCK_KEY="claude-orchdoc-reader-script-lockscroll";
function lockPref(){ try{ return window.localStorage.getItem(LOCK_KEY)!=="0"; }
  catch(x){ return true; } }
function setLockPref(on){ try{ window.localStorage.setItem(LOCK_KEY,on?"1":"0"); }catch(x){} }
function narrow(){ try{ return window.matchMedia("(max-width:1180px)").matches; }
  catch(x){ return false; } }
function lockOn(ed){ return !!(ed&&ed.lock&&!narrow()); }
function activeBox(ed){ return ed.pv.classList.contains("hid")?ed.df:ed.pv; }
function setScroll(el,v){
  var max=el.scrollHeight-el.clientHeight;
  v=Math.round(Math.max(0,Math.min(max,v)));
  if(Math.abs(el.scrollTop-v)<1){ return; }
  el._want=v; el.scrollTop=v;
}
function echo(el){ var w=el._want; el._want=null;
  return w!=null&&Math.abs(el.scrollTop-w)<=1; }
function queue(ed,k){
  if(SV.ed!==ed){ if(ed.ro){ ed.ro.disconnect(); ed.ro=null; } return; }
  ed.pend[k]=1;
  if(!ed.raf){ ed.raf=requestAnimationFrame(function(){ tick(ed); }); }
}
function tick(ed){
  ed.raf=0; if(SV.ed!==ed){ return; }
  var p=ed.pend; ed.pend={};
  if(p.paint){ paintNow(ed); }
  if(!lockOn(ed)){ return; }
  if(p.sync){ follow(ed,ed.driver!==activeBox(ed)); }
  if(p.caret&&document.activeElement===ed.ta){ caretIntoView(ed); }
}
function tops(ed){
  if(ed.tops){ return ed.tops; }
  var t=[0], ms=ed.hl.querySelectorAll(".se-ln");
  for(var i=0;i<ms.length;i++){ t.push(ms[i].offsetTop); }
  return (ed.tops=t);
}
function anchors(ed,box){
  if(ed.anc&&ed.anc.box===box){ return ed.anc.P; }
  var T=tops(ed), n=T.length-1, ta=ed.ta, isPv=box===ed.pv;
  var br=box.getBoundingClientRect(), base=br.top+box.clientTop-box.scrollTop;
  var els=box.querySelectorAll(isPv?"[data-src-line]":".sd-row");
  var P=[[0,0]], lastS=0, lastY=0;
  for(var i=0;i<els.length;i++){
    var el=els[i], ln;
    if(isPv){ ln=parseInt(el.getAttribute("data-src-line"),10); }
    else { var c=el.children[1]; ln=c?parseInt(c.textContent,10):NaN; }
    if(!(ln>=1&&ln<=n)){ continue; }
    /* ⛔ A ROW INSIDE A CLOSED FOLD STILL REPORTS A POSITION - the one it had when the fold
       was last open (measured: rows 1-39 of a closed fold answered getClientRects with their
       open-state offsets). Asked directly instead. */
    if(el.closest("details:not([open])")){ continue; }
    var r=el.getClientRects(); if(!r.length){ continue; }
    var s=T[ln], y=r[0].top-base;
    if(s>lastS&&y>lastY){ P.push([s,y]); lastS=s; lastY=y; }
  }
  var endS=ta.scrollHeight, endY=box.scrollHeight;
  while(P.length>1&&(P[P.length-1][0]>=endS||P[P.length-1][1]>=endY)){ P.pop(); }
  P.push([endS,endY]);
  ed.anc={box:box,P:P};
  return P;
}
function interp(P,a,b,x){
  if(x<=P[0][a]){ return P[0][b]; }
  for(var k=1;k<P.length;k++){
    if(x<=P[k][a]){ var p=P[k-1], q=P[k], d=q[a]-p[a];
      return p[b]+(d>0?(x-p[a])/d:0)*(q[b]-p[b]); } }
  return P[P.length-1][b];
}
function refFrac(el){
  var ch=el.clientHeight, max=el.scrollHeight-ch; if(max<=0){ return 0; }
  var from=Math.max(0,max-ch); if(el.scrollTop<=from){ return 0; }
  return Math.min(1,(el.scrollTop-from)/(max-from));
}
function follow(ed,fromSrc){
  var box=activeBox(ed), ta=ed.ta, P=anchors(ed,box);
  var src=fromSrc?ta:box, dst=fromSrc?box:ta;
  var r=refFrac(src), y=src.scrollTop+r*src.clientHeight;
  var yd=interp(P,fromSrc?0:1,fromSrc?1:0,y);
  setScroll(dst,yd-r*dst.clientHeight);
  if(!fromSrc){ ed.hl.scrollTop=ta.scrollTop; }
}
function caretIntoView(ed){
  var ta=ed.ta, box=activeBox(ed), v=ta.value, pos=ta.selectionEnd, T=tops(ed);
  var ls=pos>0?v.lastIndexOf("\n",pos-1)+1:0, le=v.indexOf("\n",pos);
  if(le<0){ le=v.length; }
  var line=1;
  for(var i=v.indexOf("\n");i>=0&&i<pos;i=v.indexOf("\n",i+1)){ line++; }
  var t0=T[line]||0, t1=line+1<T.length?T[line+1]:ta.scrollHeight;
  var y=t0+(le>ls?(pos-ls)/(le-ls):0)*(t1-t0);
  var yp=interp(anchors(ed,box),0,1,y), top=box.scrollTop, h=box.clientHeight;
  if(yp<top+8||yp>top+h-48){ setScroll(box,yp-h/3); }
}
function wire(ed){
  var ta=ed.ta;
  ta.addEventListener("input",function(){ schedule(); queue(ed,"paint"); queue(ed,"caret"); });
  ta.addEventListener("keydown",function(ev){
    ed.driver=ta;
    if((ev.ctrlKey||ev.metaKey)&&(ev.key==="s"||ev.key==="S")){ ev.preventDefault(); save(); } });
  ta.addEventListener("keyup",function(ev){
    if(/^(Arrow|Page|Home|End)/.test(ev.key||"")){ queue(ed,"caret"); } });
  ta.addEventListener("mouseup",function(){ queue(ed,"caret"); });
  ta.addEventListener("scroll",function(){
    var mine=echo(ta);
    ed.hl.scrollTop=ta.scrollTop; ed.hl.scrollLeft=ta.scrollLeft;
    if(ed.driver===ta&&!mine){ queue(ed,"sync"); } },{passive:true});
  [ed.pv,ed.df].forEach(function(box){
    box.addEventListener("scroll",function(){
      var mine=echo(box);
      if(ed.driver===box&&!mine){ queue(ed,"sync"); } },{passive:true}); });
  /* Opening a fold of unchanged lines moves every row below it without resizing the box, so
     the resize observer cannot see it. */
  ed.df.addEventListener("toggle",function(){ ed.anc=null; queue(ed,"sync"); },true);
  [ta,ed.pv,ed.df].forEach(function(el){
    ["pointerenter","pointerdown","wheel","touchstart","focusin"].forEach(function(n){
      el.addEventListener(n,function(){ ed.driver=el; },{passive:true}); }); });
  if(window.ResizeObserver){
    ed.ro=new ResizeObserver(function(){ ed.tops=null; ed.anc=null; queue(ed,"sync"); });
    ed.ro.observe(ta); ed.ro.observe(ed.pv); ed.ro.observe(ed.df);
  }
}
function schedule(){ var ed=SV.ed; if(!ed){return;}
  if(ed.t){clearTimeout(ed.t);} ed.t=setTimeout(refresh,350); }
function refresh(){
  var ed=SV.ed; if(!ed){return;} ed.t=null;
  var my=++ed.rseq;
  if(ed.ta.value!==ed.saved){
    lsSet(ed.key,{text:ed.ta.value,when:new Date().toLocaleString()}); }
  post("/script/preview",{token:NOTE_TOKEN,text:ed.ta.value,base:ed.bases[ed.base].text},
    function(r){
      if(SV.ed!==ed||my!==ed.rseq){return;}
      pane.querySelector(".se-prev").innerHTML=r.html;
      pane.querySelector(".se-diff").innerHTML=r.diff;
      pane.querySelector(".se-count").textContent=r.same?"no changes":
        ("+"+r.added+" / −"+r.removed+" lines");
      pane.querySelector(".se-count2").textContent=r.same?"":
        ("(+"+r.added+" −"+r.removed+")");
      /* New blocks, new positions: re-anchor, then put the preview back on the source. */
      ed.anc=null; queue(ed,"sync");
      if(document.activeElement===ed.ta){ queue(ed,"caret"); }
    },
    function(why){ if(SV.ed!==ed){return;}
      var p=pane.querySelector(".se-prev"); p.innerHTML='<div class="note-err"></div>';
      p.firstChild.textContent="Preview unavailable: "+why; });
}
function landText(L){
  if(!L){ return ""; }
  if(L.state==="disabled"){ return '<span class="se-no">Not landed to git</span> - '+
    esc(L.reason)+' Your save is on disk.'; }
  if(L.state==="landed"){ return '<span class="se-yes">Landed</span> on <code>'+
    esc(L.branch)+'</code> as <code>'+esc((L.sha||"").slice(0,9))+'</code>'+
    (L.reason?' ('+esc(L.reason)+')':'')+'.'; }
  if(L.state==="failed"){ return '<span class="se-no">NOT landed to git</span> - '+
    esc(L.reason)+(L.detail?'<details><summary>what git said</summary><pre>'+esc(L.detail)+
    '</pre></details>':'')+' Your save and its snapshot are on disk and safe.'; }
  return '<span class="se-wait">Landing to git…</span> '+esc(L.step||"");
}
function receipt(r){
  var h='<div class="se-receipt">';
  if(!r.changed){ status("html",h+'<p><b>Nothing changed</b> - no file was written.</p></div>');
    return; }
  h+='<p><span class="se-yes">Saved to disk:</span> <code>'+esc(r.target_rel)+'</code>'+
     (r.created?' - a new file, your first rewrite of this draft.':'.')+'</p>';
  if(r.snapshot_rel){ h+='<p>What it said before this save is kept at <code>'+
     esc(r.snapshot_rel)+'</code>.</p>'; }
  if(r.opened_unchanged===true){ h+='<p>The file you opened, <b>'+esc(r.opened_name)+
     '</b>, was not touched: its SHA-256 is <code>'+esc(r.opened_sha_after.slice(0,12))+
     '</code> before and after.</p>'; }
  else if(r.opened_unchanged===false){ h+='<p class="se-no">The file you opened CHANGED '+
     'during the save - that should never happen. Tell the orchestrator.</p>'; }
  h+='<p class="se-land">'+landText(r.landing)+'</p></div>';
  status("html",h);
}
function poll(jid,ed){
  var n=0;
  (function tick(){
    call("/script-landing/"+encodeURIComponent(jid),{limit:FETCH_POLL_MS},function(L){
      var el=pane.querySelector(".se-land");
      if(SV.ed===ed&&el){ el.innerHTML=landText(L); }
      if(L.state==="queued"||L.state==="running"){ if(++n<400){ setTimeout(tick,1500); } }
      else { refreshNav(); }
    },function(why){ var el=pane.querySelector(".se-land");
      if(SV.ed===ed&&el){ el.innerHTML='<span class="se-no">Landing status unavailable</span>'+
        ' - '+esc(why)+'. Your save is on disk.'; } });
  })();
}
function refreshNav(){
  if(window.readerForget){ window.readerForget("__scripts__"); }
  fetch("/doc/__scripts__").then(function(r){return r.json();}).then(function(d){
    var on=document.querySelector('.dbtn[data-doc="__scripts__"][aria-current="true"]');
    if(on&&d&&d.nav){ railE.innerHTML=d.nav; if(SV.cur){ markNav(SV.cur.id); } }
  }).catch(function(){});
}

function takeSaved(ed,t){
  var ta=ed.ta, old=ta.value; if(t===old){ return false; }
  var a=ta.selectionStart, b=ta.selectionEnd, dir=ta.selectionDirection||"none",
      st=ta.scrollTop, sl=ta.scrollLeft, n=Math.min(old.length,t.length), p=0, s=0;
  while(p<n&&old.charCodeAt(p)===t.charCodeAt(p)){ p++; }
  while(s<n-p&&old.charCodeAt(old.length-1-s)===t.charCodeAt(t.length-1-s)){ s++; }
  function at(x){ return x<=p?x:(x>=old.length-s?x+t.length-old.length:t.length-s); }
  ta.value=t;
  ta.setSelectionRange(at(a),at(b),dir);
  ta.scrollTop=st; ta.scrollLeft=sl;
  paintNow(ed);
  return true;
}
/* The bar above the editor describes the file that was opened. When that file IS the rewrite,
   a save changes its line (the date, the time, the model), so the bar is asked for again rather
   than left saying what it said when the editor opened. A draft is never written, so its bar
   stays. A failure leaves the bar as it was. */
function provBar(ed,j){
  if(j.edit.mode!=="update-self"){ return; }
  call("/script/"+encodeURIComponent(j.id),{cache:"no-store"},function(d){
    var el=pane.querySelector(".se-bar .sp-line");
    if(SV.ed!==ed||SV.cur!==j||!el||!d.prov_html){ return; }
    var box=document.createElement("div"); box.innerHTML=d.prov_html;
    if(box.firstElementChild){ j.prov_html=d.prov_html;
      el.parentNode.replaceChild(box.firstElementChild,el); }
  },function(){});
}

function idForSrel(nav,srel){
  if(typeof nav!=="string"||typeof srel!=="string"||!srel){ return ""; }
  var want=srel.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
    .replace(/"/g,"&quot;").replace(/'/g,"&#x27;");
  var re=/data-script="([0-9a-f]{20})"[^>]*?\sdata-srel="([^"]*)"/g, m;
  while((m=re.exec(nav))){ if(m[2]===want){ return m[1]; } }
  return "";
}
function reresolveScript(j,ok,bad){
  if(!j||typeof j.srel!=="string"||!j.srel||!j.edit||!j.edit.target_rel){ bad(); return; }
  fetchWithin("/doc/__scripts__",{cache:"no-store"},FETCH_LOAD_MS)
    .then(function(d){
      var nid=idForSrel(d&&d.nav,j.srel);
      if(!nid||nid===j.id){ bad(); return; }
      call("/script/"+encodeURIComponent(nid),{cache:"no-store"},function(n){
        if(!n||!n.edit||!n.edit.editable||n.edit.target_rel!==j.edit.target_rel){ bad(); return; }
        ok(nid);
      },function(){ bad(); });
    },function(){ bad(); });
}
function save(){
  var ed=SV.ed, j=SV.cur; if(!ed||!j||ed.busy){return;}
  if(ed.ta.value===ed.saved&&j.edit.target_exists){
    status("ok","Nothing to save - the editor matches what is on disk."); return; }
  ed.busy=true; var btn=pane.querySelector("[data-se-save]"); if(btn){btn.disabled=true;}
  status("ok","Saving…");
  var sent=ed.ta.value, again=false;
  /* ⛔ NOT SAVED, IN WORDS, AND THE TEXT STAYS WHERE IT IS. */
  function notSaved(why){
    ed.busy=false; if(btn){btn.disabled=false;}
    status("err","NOT SAVED — "+why+"  Your text is still in the editor, and this "+
      "browser keeps a copy of it."); }
  function send(){ post("/script/save",{token:NOTE_TOKEN,id:j.id,text:sent,expect:ed.expect},
    function(r){
      ed.busy=false; if(btn){btn.disabled=false;}
      if(SV.ed!==ed){return;}
      if(r.changed){
        lsDel(ed.key);
        
        var disk=typeof r.saved_text==="string"?r.saved_text:sent;
        var took=ed.ta.value===sent&&takeSaved(ed,disk);
        ed.saved=disk; ed.expect=r.target_sha;
        j.edit.target_exists=true; j.edit.target_sha=r.target_sha;
        j.edit.target_text=disk;
        ed.key=j.id+":"+r.target_sha;
        
        if(r.target_id&&r.target_id!==j.id){ ed.after=r.target_id; }
        /* After ed.saved is set, so the redraw stores no "unsaved" copy of what is on disk. */
        if(took){ if(ed.t){ clearTimeout(ed.t); ed.t=null; } refresh(); }
        else if(ed.ta.value!==ed.saved){ schedule(); }
        provBar(ed,j);
      }
      receipt(r);
      if(r.landing&&r.landing.id){ poll(r.landing.id,ed); }
      refreshNav();
    },
    function(why,s){
      if(again||s!==400||!/no script with that id/.test(why||"")){ notSaved(why); return; }
      again=true;
      reresolveScript(j,function(nid){
        if(SV.ed!==ed||SV.cur!==j){ notSaved(why); return; }
        j.id=nid;
        try{ history.replaceState(null,"","#scripts/"+nid); }catch(e){}
        send();
      },function(){ notSaved(why); });
    }); }
  send();
}

document.addEventListener("click",function(ev){
  var t=ev.target; if(!t||!t.closest){return;}
  var fk=t.closest("[data-fr-key]");
  if(fk){ frToggle(fk); return; }
  var s=t.closest("[data-script]");
  if(s){ openScript(s.getAttribute("data-script")); return; }
  if(t.closest("[data-script-edit]")){ openEditor(); return; }
  if(t.closest("[data-script-approve]")){ apOpen(); return; }
  if(t.closest("[data-script-review]")){ rvRequest(); return; }
  if(t.closest("[data-script-note-save]")){ noteSave(); return; }
  if(t.closest("[data-ap-confirm]")){ apConfirm(); return; }
  if(t.closest("[data-ap-close]")){ apClose(); return; }
  if(t.closest("[data-ap-cancel]")||(t.hasAttribute&&t.hasAttribute("data-ap-ov"))){
    if(AP&&!AP.busy){ apClose(); } return; }
  if(t.closest("[data-script-compare]")){ compare(); return; }
  if(t.closest("[data-script-uncompare]")){ var cb=pane.querySelector(".sv-compare");
    if(cb){cb.innerHTML="";} return; }
  if(t.closest("[data-se-save]")){ save(); return; }
  if(t.closest("[data-se-close]")){
    if(dirty()&&!window.confirm("Close the editor and leave your unsaved edits? (This "+
        "browser keeps a copy.)")){ return; }
    var id=(SV.ed&&SV.ed.after)||(SV.cur&&SV.cur.id); SV.ed=null;
    document.body.classList.remove("wide");
    if(id){ openScript(id); } return; }
  var w=t.closest("[data-se-wide]");
  if(w){ var on=document.body.classList.toggle("wide");
    w.setAttribute("aria-pressed",on?"true":"false"); return; }
  var tb=t.closest("[data-se-tab]");
  if(tb){ var which=tb.getAttribute("data-se-tab");
    pane.querySelectorAll("[data-se-tab]").forEach(function(b){
      b.setAttribute("aria-pressed",b===tb?"true":"false");});
    pane.querySelector(".se-prev").classList.toggle("hid",which!=="prev");
    pane.querySelector(".se-diff").classList.toggle("hid",which!=="diff");
    if(SV.ed){ SV.ed.anc=null; SV.ed.driver=SV.ed.ta; queue(SV.ed,"sync"); } return; }
  var lk=t.closest("[data-se-lock]");
  if(lk&&SV.ed){ SV.ed.lock=!SV.ed.lock; setLockPref(SV.ed.lock);
    lk.setAttribute("aria-pressed",SV.ed.lock?"true":"false");
    if(SV.ed.lock){ SV.ed.driver=SV.ed.ta; queue(SV.ed,"sync"); } return; }
  if(t.closest("[data-se-fromdraft]")&&SV.ed){
    if(SV.ed.ta.value!==SV.ed.saved&&!window.confirm("Replace what is in the editor with the "+
        "draft's text?")){ return; }
    SV.ed.ta.value=SV.cur.text; refresh(); toTop(SV.ed); return; }
  if(t.closest("[data-se-restore]")&&SV.ed){ var o=lsGet(SV.ed.key);
    if(o&&o.text){ SV.ed.ta.value=o.text; refresh(); toTop(SV.ed); }
    pane.querySelector(".se-restore").innerHTML=""; return; }
  if(t.closest("[data-se-discard]")&&SV.ed){ lsDel(SV.ed.key);
    pane.querySelector(".se-restore").innerHTML=""; return; }
});
document.addEventListener("change",function(ev){
  var el=ev.target;
  if(el&&el.classList&&el.classList.contains("se-base")&&SV.ed){
    SV.ed.base=parseInt(el.value,10)||0; refresh(); }
});

function cvDirty(){
  if(!SV.cmp){ return false; }
  var d=false;
  pane.querySelectorAll(".cv-ta").forEach(function(t){ if(t.value!==t.dataset.saved){d=true;} });
  return d;
}
function openCompare(id){
  if(dirty()&&!window.confirm("You have unsaved text here. Leave it?")){ return; }
  SV.ed=null; SV.cur=null; SV.cmp=null; document.body.classList.remove("wide");
  var my=++SV.seq;
  pane.innerHTML='<div class="empty">comparing...</div>';
  call("/script-compare/"+encodeURIComponent(id),{cache:"no-store"},function(j){
    if(my!==SV.seq){return;}
    SV.cmp=j.id; paintCompare(j); markNav(j.id);
    history.replaceState(null,"","#scripts/"+j.id+"/compare");
    if(window.readerSetCol){ window.readerSetCol("read"); }
  },function(why){
    if(my!==SV.seq){return;}
    pane.innerHTML='<div class="note"><b>Could not open the comparison.</b> '+esc(why)+'</div>';
  });
}
window.openCompare=openCompare;
function paintCompare(j){
  var s=j.stats||{}, h='<div class="sv cv">';
  h+='<div class="sv-crumb">'+esc(j.folder)+'</div>';
  h+='<h2 class="sv-title">Compare &middot; '+esc(j.draft_name.replace(/\.md$/i,""))+'</h2>';
  h+='<p class="sv-pair"><b>draft</b> <button type="button" class="slink" data-script="'+
     esc(j.id)+'">'+esc(j.draft_name)+'</button> &rarr; <b>your rewrite</b> <button '+
     'type="button" class="slink" data-script="'+esc(j.rewrite_id)+'">'+esc(j.rewrite_name)+
     '</button></p>';
  h+='<p class="cv-intro">'+esc(s.passages)+' changed passage'+(s.passages===1?'':'s')+', '+
     esc(s.noted)+' with a note'+(s.stale?'; '+esc(s.stale)+' note'+(s.stale===1?'':'s')+
     ' whose change no longer matches (at the bottom)':'')+'. Each note is saved on its own - '+
     '<b>Save this note</b>, or Ctrl+S in its box - to <code>'+esc(j.notes_rel)+'</code>, '+
     'beside the pair, '+(j.landing?'and is then committed and pushed.':
     'but this reader has landing turned off, so it is NOT committed.')+'</p>';
  h+='<div class="cv-err"></div>';
  h+='<div class="cv-tools"><button type="button" class="nbtn" data-cv-all="1">Show every '+
     'unchanged line</button><button type="button" class="nbtn" data-cv-all="0">Fold them '+
     'again</button></div>';
  h+=j.html+'</div>';
  pane.innerHTML=h; pane.scrollTop=0;
  if(j.notes_error){ var e=pane.querySelector(".cv-err");
    e.innerHTML='<div class="note-err"></div>'; e.firstChild.textContent=j.notes_error; }
  pane.querySelectorAll(".cv-ta").forEach(function(t){ t.dataset.saved=t.value;
    if(!NOTE_TOKEN||j.notes_error){ t.readOnly=true; } });
  if(!NOTE_TOKEN||j.notes_error){ pane.querySelectorAll(".cv-save").forEach(function(b){
    b.disabled=true; b.title=NOTE_TOKEN?"the notes file could not be read":
      "this page was rendered read-only"; }); }
}
function cvState(wrap,kind,html){
  var st=wrap.querySelector(".cv-st"); if(!st){return;}
  st.innerHTML=kind==="err"?'<span class="se-no"></span>':html;
  if(kind==="err"){ st.firstChild.textContent=html; }
}
function cvPoll(jid,wrap,id){
  var n=0;
  (function tick(){
    call("/script-landing/"+encodeURIComponent(jid),{limit:FETCH_POLL_MS},function(L){
      if(SV.cmp!==id){return;}
      cvState(wrap,"ok",'<span class="se-yes">Saved.</span> '+landText(L));
      if((L.state==="queued"||L.state==="running")&&++n<400){ setTimeout(tick,1500); }
    },function(why){ if(SV.cmp===id){ cvState(wrap,"ok",'<span class="se-yes">Saved.</span> '+
      '<span class="se-no">Landing status unavailable</span> - '+esc(why)); } });
  })();
}
function cvSave(wrap){
  var ta=wrap&&wrap.querySelector(".cv-ta"), btn=wrap&&wrap.querySelector(".cv-save");
  if(!ta||!SV.cmp||!NOTE_TOKEN||ta.readOnly||wrap.dataset.busy){ return; }
  var key=ta.getAttribute("data-cv-key"), sent=ta.value, id=SV.cmp;
  wrap.dataset.busy="1"; if(btn){btn.disabled=true;}
  cvState(wrap,"ok","Saving...");
  post("/script/why",{token:NOTE_TOKEN,id:id,k:key,text:sent},function(r){
    delete wrap.dataset.busy; if(btn){btn.disabled=false;}
    if(SV.cmp!==id){return;}
    ta.dataset.saved=(ta.value===sent)?(ta.value=r.text):r.text;
    ta.classList.toggle("cv-dirty",ta.value!==ta.dataset.saved);
    if(!r.changed){ cvState(wrap,"ok","Nothing changed - no file was written."); return; }
    cvState(wrap,"ok",'<span class="se-yes">Saved</span> to <code>'+esc(r.notes_rel)+
      '</code>. '+landText(r.landing));
    if(r.landing&&r.landing.id){ cvPoll(r.landing.id,wrap,id); }
  },function(why){
    delete wrap.dataset.busy; if(btn){btn.disabled=false;}
    cvState(wrap,"err","NOT SAVED - "+why+" Your text is still in the box.");
  });
}
document.addEventListener("click",function(ev){
  var t=ev.target; if(!t||!t.closest){return;}
  var c=t.closest("[data-script-cmp]");
  if(c){ openCompare(c.getAttribute("data-script-cmp")); return; }
  var b=t.closest("[data-cv-save]");
  if(b){ cvSave(b.closest(".cv-why")); return; }
  var a=t.closest("[data-cv-all]");
  if(a){ var open=a.getAttribute("data-cv-all")==="1";
    pane.querySelectorAll("details.cv-same").forEach(function(d){ d.open=open; }); }
});
document.addEventListener("input",function(ev){
  var t=ev.target;
  if(t&&t.classList&&t.classList.contains("cv-ta")){
    t.classList.toggle("cv-dirty",t.value!==t.dataset.saved); }
});
document.addEventListener("keydown",function(ev){
  var t=ev.target;
  if(t&&t.classList&&t.classList.contains("cv-ta")&&(ev.ctrlKey||ev.metaKey)&&
     (ev.key==="s"||ev.key==="S")){ ev.preventDefault(); cvSave(t.closest(".cv-why")); }
});
})();
"""

# ==========================================================================================
# list, a file being read, and the editor - holding a hex-code field, Apply and Reset.
#
# ⭐ IT IS MOUNTED BY ITS OWN OBSERVER, NOT BY THE FUNCTIONS THAT PAINT THOSE VIEWS. Each of
# them replaces the whole pane with `pane.innerHTML = ...`, which removes anything put there
# earlier, and several other changes are being made to those same functions. So nothing in them
# knows this box exists: a MutationObserver on the pane's direct children puts it back, as the
# first child, after any change that leaves a Scripts view in the pane, and takes it away after
# any change that does not (a document, the plate, "loading..."). One element is built once and
# put back each time, so its handlers survive being detached.
#
# What is applied is read back from <html> (`data-pn` + `--pn-user`), never from storage:
# storage can throw or be blocked, and the box must still say what the page is showing.
# ==========================================================================================
NOTE_COLOR_JS = ("(function(){var K=%s,D=%s;"
                 % (json.dumps(NOTE_COLOR_KEY), json.dumps(NOTE_COLOR_DEFAULT))) + r"""
var pane=document.getElementById("pane"), box=null, inp=null, sayE=null;
if(!pane){ return; }
/* #RGB or #RRGGBB, with or without the #. Everything else is refused: a color name, an
   eight-digit code with alpha, a stray character, an empty box. Returns "#RRGGBB" in capitals,
   or null. */
function norm(t){
  if(typeof t!=="string"){ return null; }
  t=t.replace(/^\s+|\s+$/g,"");
  if(t.charAt(0)==="#"){ t=t.slice(1); }
  if(!/^[0-9a-fA-F]+$/.test(t)){ return null; }
  if(t.length===3){ t=t.charAt(0)+t.charAt(0)+t.charAt(1)+t.charAt(1)+t.charAt(2)+t.charAt(2); }
  else if(t.length!==6){ return null; }
  return "#"+t.toUpperCase();
}
function shown(){
  var r=document.documentElement, v="";
  if(r.hasAttribute("data-pn")){
    v=(r.style.getPropertyValue("--pn-user")||"").replace(/\s+/g,"").toUpperCase(); }
  return /^#[0-9A-F]{6}$/.test(v)?v:D;
}
/* The default is stored as ABSENCE, like the theme: the attribute and the key are removed, so the
   default always comes from the palette and never from a written value. Applying is separate
   from persisting, so a write that throws still leaves the page on the color just chosen - it
   simply will not be remembered. */
function put(v){
  var r=document.documentElement;
  if(v===D){ r.removeAttribute("data-pn"); r.style.removeProperty("--pn-user"); }
  else{ r.setAttribute("data-pn","1"); r.style.setProperty("--pn-user",v); }
  try{ if(v===D){ window.localStorage.removeItem(K); }
       else{ window.localStorage.setItem(K,v); } }catch(e){}
}
function say(msg,kind){ sayE.textContent=msg; sayE.className="pnbox-say"+(kind?" "+kind:""); }
function clearBad(){ inp.classList.remove("bad"); inp.removeAttribute("aria-invalid"); }
function show(){ inp.value=shown(); clearBad(); say("",""); }
function applyTyped(){
  var v=norm(inp.value);
  if(v===null){
    inp.classList.add("bad"); inp.setAttribute("aria-invalid","true");
    say("Not a hex color - use #RGB or #RRGGBB. Notes stay "+shown()+".","bad");
    return; }
  put(v); inp.value=v; clearBad();
  say(v===D?"Default color "+D+" is back.":"Notes are now "+v+".","ok");
}
function reset(){ put(D); inp.value=D; clearBad(); say("Default color "+D+" is back.","ok"); }
function build(){
  var d=document.createElement("div");
  d.id="pnbox"; d.className="pnbox"; d.setAttribute("role","group");
  d.setAttribute("aria-label","Color of notes");
  d.innerHTML=
    '<label class="pnbox-l" for="pnbox-in">Color of <span class="pn">{{{ notes }}}</span></label>'+
    '<input id="pnbox-in" class="pnbox-in" type="text" spellcheck="false" autocomplete="off" '+
      'placeholder="'+D+'" title="A hex code: #RGB or #RRGGBB, with or without the #. It is the '+
      'text color of notes on the dark theme and the highlight behind them on the light theme.">'+
    '<button type="button" class="nbtn" data-pn-apply>Apply</button>'+
    '<button type="button" class="nbtn" data-pn-reset title="Put back '+D+'">Reset</button>'+
    '<span id="pnbox-say" class="pnbox-say" role="status"></span>';
  inp=d.querySelector(".pnbox-in"); sayE=d.querySelector(".pnbox-say");
  d.addEventListener("click",function(ev){
    var b=ev.target&&ev.target.closest?ev.target.closest("button"):null;
    if(!b){ return; }
    if(b.hasAttribute("data-pn-apply")){ applyTyped(); }
    else if(b.hasAttribute("data-pn-reset")){ reset(); } });
  inp.addEventListener("keydown",function(ev){
    if(ev.key==="Enter"){ ev.preventDefault(); applyTyped(); }
    else if(ev.key==="Escape"){ show(); } });
  inp.addEventListener("input",function(){ clearBad(); say("",""); });
  return d;
}
/* A Scripts view is a pane whose own child is the list heading, an open file or the editor. */
function isScripts(){
  var c=pane.children, i, k;
  for(i=0;i<c.length;i++){
    k=c[i].classList;
    if(k.contains("shead")||k.contains("sv")||k.contains("se")){ return true; } }
  return false;
}
function mount(){
  if(!isScripts()){
    if(box&&box.parentNode){ box.parentNode.removeChild(box); }
    return; }
  if(!box){ box=build(); }
  if(box.parentNode===pane&&pane.firstChild===box){ return; }
  pane.insertBefore(box,pane.firstChild);
  show();
}
new MutationObserver(mount).observe(pane,{childList:true});
mount();
})();
"""


def render_page(index, boot_nav, boot_body, active="__plate__", token="", scripts=None):
    """The shell, with the cross-doc view already in it so the landing is instant.

    ⛔ `token` IS WHAT TURNS THE NOTES UI ON, and an empty one is a correct, READ-ONLY page.
    A page rendered without it shows the notes that exist and offers no way to write one -
    which is exactly right for `--once`, for a test, and for any caller that is not the live
    server. The buttons never appear without a token the server minted this run.
    """
    flat = []
    for d in index:
        for i in d["entries"]:
            flat.append({"d": i["doc"], "i": i["id"], "t": i["title"],
                         "c": i["sclass"], "p": 1 if i["plate"] else 0,
                         "h": (i["id"] + " " + i["title"]).lower()})
    filters = "".join(
        '<button type="button" class="fbtn" data-filter="%s" aria-pressed="%s">%s</button>'
        % (v, "true" if v == "all" else "false", lab)
        for v, lab in (("all", "All"), ("plate", "On your plate"), ("open", "Open"),
                       ("resolved", "Resolved"), ("findings", "Findings"),
                       ("today", "Do today"), ("starred", "\u2605 Starred")))
    themer = "".join(
        '<button type="button" class="tbtn" data-theme-set="%s" aria-pressed="false">%s'
        "</button>" % (v, lab)
        for v, lab in (("auto", "Auto"), ("light", "Light"), ("dark", "Dark")))
    # Five steps, each one point apart, with the new default in the middle.
    sizer = "".join(
        '<button type="button" class="tbtn szbtn z%d" data-fs-set="%d" aria-pressed="false"'
        ' aria-label="Text size %s" title="Text size %s - body text %s">A</button>'
        % (i, s,
           "default" if s == FS_DEFAULT else "%+d" % s,
           "default" if s == FS_DEFAULT else "%+d" % s,
           _px(fs_body_px(s)))
        for i, s in enumerate(FS_STEPS))
    mob = "".join(
        '<button type="button" class="tbtn" data-col-set="%s" aria-pressed="%s">%s</button>'
        % (v, "true" if v == "list" else "false", lab)
        for v, lab in (("docs", "Docs"), ("list", "Entries"), ("read", "Reading")))

    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>OrchDoc reader</title><style>%s</style></head><body data-col=\"list\">"
        "<script>%s</script>"
        '<header class="top">'
        '<div class="brand">Orch<b>Docs</b></div>'
        '<div class="grow"><input id="q" type="search" '
        'placeholder="Search ids, titles and text  (press \\ )" autocomplete="off"></div>'
        '<div class="scope"><button type="button" class="tbtn" id="scope-all" '
        'aria-pressed="false" title="Search every document, not just this one">'
        "Search all docs</button></div>"
        '<div class="sizer" role="group" aria-label="Text size">'
        '<span class="tlab">Text</span>%s</div>'
        '<div class="themer" role="group" aria-label="Colour theme">'
        '<span class="tlab">Theme</span>%s</div>'
        '</header>'
        '<div class="mob" role="group" aria-label="Column">%s</div>'
        '<div class="app">'
        '<aside class="rail rail-docs" id="rail-docs">%s</aside>'
        '<div class="rail rail-mid">'
        '<div class="filters">%s'
        '<select id="owner" title="Filter by owner">'
        '<option value="">Any owner</option>%s</select></div>'
        '<div class="count" id="count"></div>'
        '<nav class="rail-entries" id="rail-entries">%s</nav>'
        "</div>"
        '<main class="pane" id="pane">%s</main>'
        "</div>"
        "<script>var IDX=%s;var NOTE_TOKEN=%s;</script><script>%s</script>"
        "<script>%s</script><script>%s</script>"
        "<script>%s</script><script>%s</script>"
        "<script>%s</script>"
        "</body></html>"
        # ⛔ ALL THREE BOOT SNIPPETS RUN IN THE SAME PLACE, BEFORE THE HEADER. Text size has the
        # same problem theme does and a worse symptom: read it after the page has painted
        # is the third: read late, every script would open in the default colour and then
        # change it.
        % (CSS, THEME_BOOT_JS + SIZE_BOOT_JS + NOTE_BOOT_JS, sizer, themer, mob,
           render_doc_rail(index, active, scripts), filters, owner_options(index),
           boot_nav, boot_body,
           json.dumps(flat, ensure_ascii=False), json.dumps(token or ""),
           # ⭐ THE SCRIPTS BLOCK LOADS BEFORE THE MAIN ONE, because the main one calls
           # `route()` as it finishes and a `#scripts/<id>` hash needs `openScript` to exist.
           SCRIPTS_JS, JS, COPY_JS, THEME_WIRE_JS, SIZE_WIRE_JS, NOTE_COLOR_JS))


# ==========================================================================================
# SERVER
#
# ⭐ IT RE-READS ON EVERY REQUEST. Parsing all eleven docs measures at 0.12s, and these
# documents are written by other sessions WHILE the human reads them - a snapshot taken at
# startup would quietly go stale, which is the same class of wrong as a dropped entry.
# ==========================================================================================

# ⛔ THE SAME NUMBER THE WRITER KEEPS, IMPORTED RATHER THAN CHOSEN. This was 4000 while
# `sanitize_note` truncated at 2000 and appended "..." - so a 3000-character note was accepted,
# reported saved, and stored as 2003 characters. Nothing lied; the two numbers had simply never
# been made to agree. Refusing at the writer's own limit is the only version where "saved" and
# "all of it" mean the same thing.
NOTE_MAX_CHARS = orchdoc.NOTE_TEXT_LIMIT

# ⛔ AN INSTRUCTION IS NOT DELIVERED ANYWHERE, AND THAT IS THE HUMAN'S RULING, NOT AN OMISSION.
# The first build of this also appended to `orch-mailboxes/o<N>.md`, on the theory that a
# watcher would wake the session.
#
#   Sessions were getting misled by stale notes about the mailbox system.
#
# It would have been a delivery receipt for a channel with no listener - the exact shape of
# failure this file exists to refuse.
#
# ⛔ SO THE SERVER CLAIMS NOTHING ABOUT NOTIFICATION. It writes the instruction into the
# document and stops. `orchdoc_stop_check.py` - a Stop hook that already runs at every turn
# boundary of every session, and already knows which OrchDoc a session owns - is what puts it
# in front of the owner, with nothing kept running. The page reports what is TRUE at the moment
# of writing: it is recorded, and nobody has acknowledged it yet.


# ⛔ A SCRIPT SAVE CARRIES THE WHOLE SCRIPT, AND A PREVIEW CARRIES IT TWICE (the text and the
# version it is diffed against). The largest file in the corpus is 58KB, so the gate's 200KB
# note-sized cap would refuse a preview of a script a little over 100KB. The cap is raised for
# these two routes only, and it is still the SAME gate function doing the checking.
SCRIPT_MAX_BODY = 2_000_000
SCRIPT_MAX_CHARS = 800_000
# /script/why carries one note: the gate's own default, the same cap /note has.
WHY_MAX_BODY = GATE.MAX_BODY

# Keys that NAME A PLACE. A script route refuses them outright, the way `/note` refuses `path`
# and `doc_path`: silently ignoring one would let a page (or a bug) send it forever while
# believing it chose where the file went.
_PATH_KEYS = {"path", "paths", "file", "files", "filename", "filepath", "file_path", "dest",
              "destination", "target", "target_path", "dir", "directory", "folder", "rel",
              "relpath", "abs", "abspath", "doc_path", "to", "src", "source", "out", "output",
              "name", "root", "snapshot"}
#: Every route that belongs to the script features. Without them the Reader answers 404 here.
SCRIPT_ROUTES = ("/script", "/library-sync-status", "/full-rewrite")
_SCRIPT_KEYS = {"/script/preview": {"token", "text", "base"},
                "/script/save": {"token", "id", "text", "expect"},
                # ONE note: the pair's id, the change's key (or "overall"), and its text.
                "/script/why": {"token", "id", "k", "text"},
                "/script/note": {"token", "id", "text"},
                # The id and nothing else; /script/approve-plan writes nothing (the command's
                # --dry-run), /script/approve runs it and lands the result.
                "/script/approve-plan": {"token", "id"},
                "/script/approve": {"token", "id"}}


# Used only when `reader_landing.py` could not be imported.
INLINE_LANDING = "inline"
LANDING_OFF_TEXT = "landing off (--no-land)"

# A save the page sent with an id waits this long for `orchdoc.py`; a writer still : running
# then is NOT reported as failed. The page is told {"pending": true} and asks : GET
# /landing?cid= until the writer answers.
WRITER_ANSWER_WAIT = 20.0
# : o9cL1: a save the page sends from its outbox carries `"bg": 1` and is answered at once :
# ({"pending": true}) while the writer runs; the page follows it with GET /landing?cid= and the
# human : is never kept waiting.
WRITER_BG_WAIT = 0.0
#: ...and a writer is stopped only after this long. It runs no git push any more, so this is a
#: guard against a hung process, not a deadline a busy machine can reach.
WRITER_HARD_CAP = 600
#: A writer that found the doc locked by another tool wrote nothing; it is run again this many
#: times, a second or two apart, before the page is told.
WRITER_LOCK_RETRIES = 3
CID_REFUSAL = "bad request shape: cid is 8 to 64 letters, digits, - or _"


# ==========================================================================================
#
# Each request now writes one line when its answer has gone:
#
#       write=0ms send=21ms bytes=3610664 landing=running waiting=1   (one line in the file)
#
#   `log_time` makes the token, for every line in the file.
#
#   landing: 2026-10-03T18:53:10.002 NOT landed 01791...-ef34ab (transient, retry in 4s): ...
#   copies: 2026-10-03T18:53:12.917 the watcher hit OSError: ...
#
#   lock     waiting for the index lock (`Reader.index`), which one request at a time holds
#   build    building the doc models under it (a doc whose file changed is parsed again)
#   render   turning the models into the page or the doc view, and into JSON
#   write    waiting for `orchdoc.py` to write a note, instruction or Do-today change
#   send     writing the answer to the socket (slow when the browser is slow to read it)
#   landing  whether a landing was running at the start or the end of the request: running,
#            idle, off (--no-land) or inline; `waiting` is how many changes were queued.
#
# ⛔ NEVER A BODY, A QUERY STRING OR THE HUMAN'S TEXT. The route is the path with its query string
# and fragment cut off, and nothing the page sent is written.
#
# ⛔ A FILE, BECAUSE THE LOGON TASK THROWS STDOUT AWAY. `ensure_local_servers.py` starts the
# live Reader with stdout and stderr set to DEVNULL, so a line printed there is lost. Each line
# is printed as before AND appended to `<workspace>/.shared/state/reader-log/reader.log`, with the
# landing worker's own lines. When the file passes READER_LOG_MAX bytes it becomes
# `reader.old.log` (replacing the previous one), so the two files never hold more than about
# twice that. Both names end in `.log`, which .gitignore ignores.
# ==========================================================================================
READER_LOG_REL = ".shared/state/reader-log/reader.log"
READER_LOG_MAX = 2 * 1024 * 1024
#: Lines the echo may fall behind before newer ones are not echoed (the file keeps them all).
READER_LOG_ECHO_BACKLOG = 1000

_REQ = threading.local()      # this thread's request being timed: see `make_handler`


def _req_add(key, ms):
    """Add `ms` to phase `key` of the request this thread is answering, if one is timed."""
    t = getattr(_REQ, "t", None)
    if t is not None:
        t[key] = t.get(key, 0.0) + ms


class _timed:
    """`with _timed("render"):` adds the block's time to that phase of this thread's request."""

    def __init__(self, key):
        self.key = key

    def __enter__(self):
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        _req_add(self.key, (time.perf_counter() - self.t0) * 1000.0)
        return False


def reader_log_path(workspace):
    """`<workspace>/.shared/state/reader-log/reader.log` in a workspace that has `.shared/scripts`.

    A folder that is not one (a user of the skill copy, whose folder holds only their OrchDocs)
    must not gain a `.shared` folder from starting the Reader, so its log goes to this computer's
    state folder instead (`shared_state.home_state_dir()`, `~/.claude/state`), in a subfolder
    """
    ws = Path(workspace)
    if (ws / ".shared" / "scripts").is_dir():
        return ws / READER_LOG_REL
    import hashlib
    import shared_state
    tag = hashlib.sha1(str(ws.resolve()).lower().encode("utf-8")).hexdigest()[:12]
    return shared_state.home_state_dir() / "reader-log" / tag / "reader.log"


def log_time(at=None):
    """The date and time each line of reader.log carries after its first word: this
    computer's local clock to the millisecond, as ONE token, `2026-10-03T18:52:01.123`."""
    at = time.time() if at is None else at
    return "%s.%03d" % (time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(at)),
                        int(at * 1000) % 1000)


class StampedLog:
    """For the landing worker and its copy watcher, which write their lines without a time. Never
    raises: the line goes as it came if it cannot be stamped."""

    def __init__(self, log):
        self.log = log

    def __call__(self, line):
        try:
            head, sep, rest = " ".join(str(line).splitlines()).strip().partition(" ")
            line = head + " " + log_time() + (" " + rest if sep else "")
        except Exception:
            pass
        self.log(line)


class ReaderLog:
    """The Reader's log: each line is printed, and appended to a file of at most `max_bytes`
    that is renamed to `<name>.old.log` when full (one old file kept). Never raises: a log that
    cannot be written must not take a request down with it. Never waits on stdout either: the
    printing is done by a thread of its own (see `_echo`).

    `own_dir=True`: the folder is the log's own, and a `.gitignore` of `*` in it keeps the log
    out of git in any workspace. The real repo ignores `*.log` already; a workspace that does
    not (a test's fixture repo) would otherwise show the log as a change on disk."""

    def __init__(self, path, max_bytes=READER_LOG_MAX, echo=True, own_dir=False):
        self.path = Path(path) if path else None
        self.old = self.path.with_name(self.path.stem + ".old" + self.path.suffix) \
            if self.path else None
        self.max_bytes = max_bytes
        self.echo = echo
        self.own_dir = own_dir
        self._mx = threading.Lock()
        self._echo_q = queue.Queue(maxsize=READER_LOG_ECHO_BACKLOG) if echo else None
        self._echo_mx = threading.Lock()
        self._echo_thread = None
        self.echo_dropped = 0

    def __call__(self, line):
        line = " ".join(str(line).splitlines())
        if self.echo:
            self._echo(line)
        if self.path is None:
            return
        with self._mx:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self.own_dir:
                    gi = self.path.parent / ".gitignore"
                    if not gi.exists():
                        gi.write_text("*\n", encoding="utf-8")
                try:
                    full = self.path.stat().st_size >= self.max_bytes
                except OSError:
                    full = False
                if full:
                    # On Windows the rename fails while another program holds the file open, and
                    # the line used to be dropped with it. It is appended anyway; the next line
                    # tries again.
                    try:
                        os.replace(str(self.path), str(self.old))
                    except OSError:
                        pass
                # `backslashreplace`: a character UTF-8 cannot encode (a lone surrogate) is
                # written as its escape rather than raising out of a writer that must not.
                with open(str(self.path), "a", encoding="utf-8", errors="backslashreplace",
                          newline="\n") as fh:
                    fh.write(line + "\n")
            except (OSError, ValueError):
                pass

    def _echo(self, line):
        """Hand the line to the echo's thread and return at once.

        `print` blocks when stdout is a pipe nobody reads; done here, under the writer's
        lock, every request would have waited behind it. The thread prints in order. If
        it falls READER_LOG_ECHO_BACKLOG lines behind, the newer lines are not echoed
        (`echo_dropped` counts them); the file has every one."""
        try:
            self._echo_q.put_nowait(line)
        except queue.Full:
            self.echo_dropped += 1
            return
        if self._echo_thread is None:
            with self._echo_mx:
                if self._echo_thread is None:
                    try:
                        t = threading.Thread(target=self._echo_loop, name="reader-log-echo",
                                             daemon=True)
                        t.start()
                        self._echo_thread = t
                    except Exception:
                        pass

    def _echo_loop(self):
        while True:
            line = self._echo_q.get()
            try:
                print(line, flush=True)
            except (OSError, ValueError):
                pass


def _landing_brief(landing):
    """(state, queued) for the line: "running" or "idle" and how many changes are queued, from
    `LandingWorker.brief()` (one folder listing); "off" for --no-land, "inline" without one."""
    if landing is None:
        return "off", 0
    if landing == INLINE_LANDING:
        return "inline", 0
    try:
        running, waiting = landing.brief()
    except Exception:
        return "?", 0
    return ("running" if running else "idle"), int(waiting)


class Reader:
    def __init__(self, workspace, token="", port=0, lander=None, landing=None, reqlog=None):
        self.workspace = workspace
        # The renderer resolves image paths against this. Set here rather than passed down
        # because `_inline` is reached through six frames of pure markdown rendering.
        global ASSET_ROOT
        ASSET_ROOT = Path(workspace).resolve()
        self.lock = threading.Lock()
        # ⛔ MINTED PER RUN. Restarting the reader invalidates every page still open, which is
        # the correct direction: a stale tab's save is refused rather than acted on.
        self.token = token
        self.port = port
        # The script corpus under this workspace. Its ids are minted per run too.
        # All six are None when the script files are not installed (HAS_SCRIPTS is False).
        self.scripts = SCRIPTS.Corpus(workspace) if HAS_SCRIPTS else None
        self.fr_marks = FRW.Marks() if HAS_SCRIPTS else None
        self.script_notes = SNOTES.Store() if HAS_SCRIPTS else None
        # The text of every script file for the search box; built in the background (see
        # `ScriptTextIndex`), started by `scripts.warm()`'s caller and on the first search.
        self.script_text = SCRIPTS.ScriptTextIndex(self.scripts) if HAS_SCRIPTS else None
        # Draft beside rewrite, with the human's reason for each change. Same corpus, same ids.
        self.compare = COMPARE.Compare(self.scripts) if HAS_SCRIPTS else None
        # ⭐ None means "do not land" - every test server and `--no-land`. The save still
        # happens and the page says, in words, that it was not committed.
        self.lander = lander
        self.runner = RUNNER.Runner() if HAS_SCRIPTS else None
        # Before this, `--no-land` stopped only the script lander, and every note a test Reader
        # saved was committed and pushed to whatever origin its workspace had.
        self.landing = landing
        self._sent = {}
        self._sent_mx = threading.Lock()
        self.reqlog = reqlog
        self._flight_mx = threading.Lock()
        self._flight = None
        self._flight_seq = 0
        self._home_cache = None

    def single_flight(self, build):
        """
        ⛔ WHY. The home page is 1.4 MB and renders in about 3 s alone. Renders now run
        one at a time. A request waiting for the mutex takes the result of a render that
        STARTED after the request arrived (so it is as fresh as its own render would
        have been) and otherwise renders; at most two renders serve any number of
        waiting requests.

        `time.monotonic()` ticks every ~16 ms on Windows, so a render that began a
        moment BEFORE a request (say, before the save that request follows) could carry
        the same reading and be shared as if it were newer. Each render takes the next
        number under the mutex; a request notes the last number given out when it
        arrives and takes only a render numbered above it. A render that raises gives
        every request sharing it the same exception, which the GET handler turns into a
        500, so no waiter hangs."""
        arrived = self._flight_seq       # one int read; renders bump it only under the mutex
        with self._flight_mx:
            hit = self._flight
            if hit is not None and hit[0] > arrived:
                if hit[2] is not None:
                    raise hit[2]
                return hit[1]
            self._flight_seq += 1
            seq = self._flight_seq
            try:
                result = build()
            except Exception as e:
                self._flight = (seq, None, e)
                raise
            self._flight = (seq, result, None)
            return result

    def home_key(self):
        """
        Read off the code: `build_index` (each OrchDoc file, by name, modification time and
        size: the same things `build_doc_model_cached` keys on), today's date (a routine falls
        due at midnight with no file moving), `scripts_count()` (the rail's script count, from
        the corpus folders) and the write token (minted per run). Times and sizes come from
        `doc_revs()`, which /landing already reports.

        `orchdoc.human_name()` names the human in the colour key's ON YOUR PLATE hover
        text, from `.orchdoc-config.json` (else `~/.claude.json`), so it is in the key.
        Whether each picture an entry shows exists on disk decides whether the page
        draws it; those are not in the key but are re-checked by `home_page` against
        what the last render saw (`_IMG_SEEN`)."""
        scripts = self.scripts_stamp()
        return (tuple(sorted(self.doc_revs().items())),
                orchdoc._dt.date.today().isoformat(), scripts, self.token,
                orchdoc.human_name())

    @staticmethod
    def _images_unchanged(seen):
        """One `resolve_asset` per picture on the home page.
        """
        for url, relp in (seen or {}).items():
            try:
                p, now, _x = resolve_asset(url)
            except Exception:
                return False
            if (now if p is not None else None) != relp:
                return False
        return True

    def scripts_stamp(self):
        """The count changes only when a folder, or a `.md` file in one, is added or removed, and
        either changes that folder's own time. `scripts_count()` itself costs about 0.9
        s on the live corpus (it models every file); this is one `scandir` per folder."""
        if not HAS_SCRIPTS:
            return None
        try:
            root = self.scripts._root_resolved()
            if root is None or not root.is_dir():
                return None
            out = []

            def walk(d, depth):
                out.append((str(d), d.stat().st_mtime_ns))
                if depth >= SCRIPTS.MAX_DEPTH:
                    return
                with os.scandir(str(d)) as it:
                    subs = [e for e in it if not e.name.startswith((".", "_"))
                            and e.is_dir()]
                for e in sorted(subs, key=lambda e: e.name):
                    walk(Path(e.path), depth + 1)
            walk(root, 0)
            return tuple(out)
        except Exception:
            # A walk that failed must never match the one before it.
            return ("unreadable", time.time_ns())

    def home_page(self):
        """
        ⛔ FRESHNESS. This runs inside the render that `single_flight` numbers, so the key is
        read after the request arrived: a request that follows a save sees the saved file's new
        time and size, misses, and renders. The key is read BEFORE the files are, so a file that
        changes mid-render leaves an older key stored and the next request renders again.

        One cached page only: `_home_cache` is (key, page, pictures seen), replaced
        whole."""
        key = self.home_key()
        hit = self._home_cache
        if hit is not None and hit[0] == key and self._images_unchanged(hit[2]):
            return hit[1]
        idx = self.index()
        _IMG_SEEN.rec = {}
        try:
            with _timed("render"):
                nav, body = render_plate_view(idx, XrefTargets(idx))
                page = render_page(idx, nav, body, token=self.token,
                                   scripts=self.scripts_count())
            seen = _IMG_SEEN.rec
        finally:
            _IMG_SEEN.rec = None
        self._home_cache = (key, page, seen)
        return page

    def scripts_count(self):
        """Folders in the corpus, or None when there is no corpus (then the rail omits it)."""
        if not HAS_SCRIPTS:
            return None
        try:
            listing = self.scripts.discover()
        except Exception:
            return None
        return len(listing["folders"]) if listing["exists"] else None

    def viewable(self, rel):
        """(text, relposix, kind) for a text file under the workspace, or (None, why).
        ⛔ RE-RESOLVED FROM SCRATCH, exactly as `asset` is - the path the browser sends back is
        never trusted to be one this server put in a link."""
        p, relp, kind_or_why = resolve_viewable(rel, self.workspace)
        if p is None:
            return None, kind_or_why
        try:
            if p.stat().st_size > VIEW_MAX_BYTES:
                return None, "larger than %d bytes - not rendered here" % VIEW_MAX_BYTES
            text = p.read_bytes().decode("utf-8", errors="replace")
        except OSError as ex:
            return None, "could not read it (%s)" % type(ex).__name__
        # ⛔ THE CONTENT, NOT ONLY THE NAME. The reason names the KIND and never the value, so
        # the refusal page cannot become the leak it exists to prevent.
        kind = secret_patterns.find_credential(text)
        if kind:
            return None, ("refused: this file holds what looks like %s, so the viewer will "
                          "not show it" % kind)
        return (text, relp, kind_or_why), None

    def index(self):
        t0 = time.perf_counter()
        with self.lock:
            t1 = time.perf_counter()
            _req_add("lock", (t1 - t0) * 1000.0)
            try:
                return build_index(self.workspace)
            finally:
                _req_add("build", (time.perf_counter() - t1) * 1000.0)

    def doc_model(self, slug, fresh=False):
        """o9cL1: one doc's model, for the checks a save makes, or None for no such doc.

        Without `fresh`, the model this process last built of that doc, even if the file
        has changed since: a save only needs to know the entry (and the note) it names
        exist, and `orchdoc.py`, which writes under the doc's own lock, refuses one that
        does not. With `fresh` (or no model yet), that one doc is modelled again, still
        without the lock.
        """
        path = self.doc_path(slug)
        if path is None:
            return None
        if not fresh:
            hit = _MODEL_CACHE.get(str(path))
            if hit:
                return hit[1]
        return build_doc_model_cached(slug, path)

    def entry_context(self, slug):
        """For `GET /entry/`, which shows what a save just wrote and so needs the doc's CURRENT
        model - but only that doc's, and without `index()`'s lock. Nothing here waits on
        another request: the models are cached by file time and size, and the other
        docs' are the ones this process last built."""
        found = list(discover_docs(self.workspace))
        mine = next((p for s, p in found if s == slug), None)
        if mine is None:
            return None, []
        model = build_doc_model_cached(slug, mine)
        idx = []
        for s, p in found:
            if s == slug:
                idx.append(model)
                continue
            hit = _MODEL_CACHE.get(str(p))
            idx.append(hit[1] if hit else build_doc_model_cached(s, p))
        return model, idx

    def doc_path(self, slug):
        """The file for `slug`, or None. ⛔ THE ONLY WAY A CLIENT NAMES A DOCUMENT.

        The browser sends a slug and it is resolved against the docs this server DISCOVERED -
        never joined onto a path. `orchdoc.resolve_doc_arg` would also refuse an escape, but a
        server that can write must not rely on the tool it calls to be the boundary.
        """
        for found, path in discover_docs(self.workspace):
            if found == slug:
                return path
        return None

    def asset(self, rel):
        """Bytes for an image under the workspace, or None. ⛔ RE-RESOLVED FROM SCRATCH.

        The page sends back a path this server put in an `<img src>`, and it is resolved
        again here rather than trusted - exactly as `doc_path` resolves a slug against the
        documents it discovered instead of joining it onto a directory. A server that can
        write these documents must not let the browser name a file.
        """
        p, _relp, ctype = resolve_asset(rel, self.workspace)
        if p is None:
            return None
        try:
            return p.read_bytes(), ctype
        except OSError:
            return None

    def write_note(self, slug, eid, nid, text, delete, kind=None, cid=None, wait=None):
        """Shell out to `orchdoc.py note`. Returns the command's own JSON verdict.

        ⛔ THIS SERVER NEVER EDITS AN ORCHDOC. `orchdoc.py` is the single writer of an OrchDoc -
        it locks the file, writes atomically, and re-reads to verify - and eleven orchestrator
        sessions write these documents concurrently. A second writer here is exactly the
        collision that rule exists to prevent, and it would be the one writer with no gates.

        ⚠️ The one markdown this server DOES write is a human rewrite in the script corpus, and
        that goes through `reader_scripts.Corpus.save` - never an agent draft, never an OrchDoc,
        and never without a snapshot of what it replaces.

        ⛔ ARGUMENTS ARE A LIST, NEVER A SHELL STRING. The human's own words are one of them.
        """
        path = self.doc_path(slug)
        if path is None:
            return {"ok": False, "error": "no such document on this page"}
        cmd = [sys.executable, str(Path(orchdoc.__file__).resolve()), "note",
               "--doc", str(path), "--json"]
        if eid:
            cmd += ["--id", eid]
        if nid:
            cmd += ["--note", nid]
        if kind:
            cmd += ["--kind", kind]
        if delete:
            cmd += ["--delete"]
        else:
            cmd += ["--text", text]
        return self._run_writer(cmd, cid, wait)

    def save_pasted_image(self, slug, raw):
        """Write one pasted image under `orchdoc-images/<doc>/`. -> {"ok", "path"} or error.
        ⛔ THE NAME IS OURS: a timestamp and random hex, the extension from the bytes."""
        import secrets as _secrets
        import time as _time
        if self.doc_path(slug) is None:
            return {"ok": False, "error": "no such document on this page"}
        ext = paste_ext(raw)
        if not ext:
            return {"ok": False, "error": "that is not a PNG, JPEG, GIF or WebP image"}
        if len(raw) > PASTE_MAX_BYTES:
            return {"ok": False, "error": "the image is over %d MB"
                                          % (PASTE_MAX_BYTES // (1024 * 1024))}
        d = Path(self.workspace).resolve() / PASTE_DIR / slug
        try:
            d.mkdir(parents=True, exist_ok=True)
            p = d / ("%s-%s%s" % (_time.strftime("%Y%m%d-%H%M%S"), _secrets.token_hex(3), ext))
            p.write_bytes(raw)
        except OSError as ex:
            return {"ok": False, "error": "could not save the image (%s)" % type(ex).__name__}
        return {"ok": True, "path": p.as_posix()}

    def write_doc_instruction(self, slug, text, cid=None, wait=None):
        """Shell out to `orchdoc.py instruct`: the human's instruction for the WHOLE doc, written as
        a new §3 W item carrying it as an INSTRUCTION note. Same single writer as a note."""
        path = self.doc_path(slug)
        if path is None:
            return {"ok": False, "error": "no such document on this page"}
        return self._run_writer([sys.executable, str(Path(orchdoc.__file__).resolve()),
                                 "instruct", "--doc", str(path), "--json", "--text", text], cid,
                                wait)

    def _land_args(self):
        """
        ⛔ EVERY WRITE GETS ONE OF THESE, so no save can land while landing is off. Now
        the writer journals the change and returns, and the worker lands it.
        """
        if self.landing is None:
            return ["--no-land"]
        if self.landing == INLINE_LANDING:
            return []
        return ["--queue-landing"]

    def landing_status(self, cids=()):
        """GET /landing: what the indicator at the bottom of the page shows, each queued change
        with its doc's slug and note id (`jobs`), the ones landed lately (`done`), and, for each
        of `cids`, what became of that save. Waits on nothing."""
        if self.landing is None:
            out = {"ok": True, "enabled": False, "text": LANDING_OFF_TEXT}
        elif self.landing == INLINE_LANDING:
            out = {"ok": True, "enabled": True, "inline": True, "pending": 0, "running": False,
                   "failing": [], "unreadable": [], "stuck": [], "landed": 0, "last": None,
                   "alive": False, "jobs": [], "done": []}
        else:
            out = dict(self.landing.status(), ok=True)
            for j in list(out.get("jobs") or ()) + list(out.get("done") or ()) + \
                    list(out.get("copies") or ()):
                m = DOC_RE.match(Path(j.get("doc") or "").stem)
                j["slug"] = m.group(1) if m else ""
        if cids:
            out["cids"] = self.sent_status(cids)
        out["revs"] = self.doc_revs()
        return out

    def doc_revs(self):
        """The page compares it with what it holds and, for the doc on screen, asks `/revs/<slug>`
        which entries changed (a save made by another session) and draws only those
        again."""
        out = {}
        for slug, path in discover_docs(self.workspace):
            try:
                st = path.stat()
                out[slug] = "%x-%x" % (st.st_mtime_ns, st.st_size)
            except OSError:
                pass
        return out

    def entry_hashes(self, slug):
        """The model is the per-doc cached one: no lock.
        """
        path = self.doc_path(slug)
        if path is None:
            return None
        try:
            st = path.stat()
            rev = "%x-%x" % (st.st_mtime_ns, st.st_size)
        except OSError:
            return None
        model = build_doc_model_cached(slug, path)
        return rev, dict((i["id"], entry_hash(i)) for i in model.get("entries") or ())

    def sent_status(self, cids):
        """{client id: {"state": "sending" | "done" | "saved" | "unknown", "reply"}}.

        "sending": its write is running now. "done": this run of the Reader answered it, and
        `reply` is that answer. "saved": a record on disk says a save with this id was written
        (the Reader restarted since). "unknown": no save with this id reached the writer."""
        out = {}
        for cid in list(cids)[:50]:
            if not isinstance(cid, str) or not orchdoc.CLIENT_ID_RE.match(cid):
                continue
            with self._sent_mx:
                hit = self._sent.get(cid)
            if hit is not None:
                out[cid] = {"state": hit["state"], "reply": hit.get("reply")}
                continue
            rec = self._sent_on_disk(cid)
            out[cid] = {"state": "saved", "reply": rec} if rec else {"state": "unknown"}
        return out

    def _sent_on_disk(self, cid):
        """The answer `orchdoc.py` recorded for the newest save with this client id, or None."""
        rec, newest = None, -1.0
        try:
            for p in (Path(self.workspace) / orchdoc.READER_SENT_REL).glob(cid + ".*.json"):
                try:
                    t = p.stat().st_mtime
                    if t > newest:
                        got = json.loads(p.read_text(encoding="utf-8"))
                        if isinstance(got, dict):
                            rec, newest = got, t
                except (OSError, ValueError):
                    continue
        except OSError:
            pass
        return dict(rec.get("reply") or {}, ok=True, duplicate=True) if rec else None

    def _run_writer(self, cmd, cid=None, wait=None):
        """Run one `orchdoc.py` write and return its own JSON verdict.

        With `cid` (the page's id for this save) a repeat of it writes nothing, the answer is
        kept for GET /landing?cid=, and a writer still running after WRITER_ANSWER_WAIT seconds
        is NOT a failure: the answer is {"ok": null, "pending": true}, the writer goes on, and
        the page asks for its verdict until it has one. The same save sent again while its
        writer runs waits for that writer instead of starting a second one.

        `wait` (seconds) replaces WRITER_ANSWER_WAIT: WRITER_BG_WAIT for a save the page sent
        from its outbox (o9cL1), which is answered "pending" at once.
        """
        with _timed("write"):
            return self._run_writer_timed(cmd, cid, wait)

    def _run_writer_timed(self, cmd, cid=None, wait=None):
        import time as _time
        cmd = list(cmd) + self._land_args() + (["--client-id", cid] if cid else [])
        if not cid:
            return self._writer_finish(self._run_writer_raw(cmd), None)
        now = _time.time()
        with self._sent_mx:
            hit = self._sent.get(cid)
            if hit is not None and hit["state"] == "sending" and hit.get("cmd") == cmd:
                ev = hit["event"]
            else:
                for k in [k for k, v in self._sent.items()
                          if v["state"] != "sending" and now - v["at"] > 3600]:
                    del self._sent[k]
                ev = threading.Event()
                self._sent[cid] = {"state": "sending", "reply": None, "at": now, "cmd": cmd,
                                   "event": ev}
                threading.Thread(target=self._writer_thread, args=(cmd, cid, ev),
                                 name="reader-writer", daemon=True).start()
        if ev.wait(WRITER_ANSWER_WAIT if wait is None else wait):
            with self._sent_mx:
                hit = self._sent.get(cid)
            if hit is not None and hit["state"] == "done":
                return dict(hit["reply"] or {})
        return {"ok": None, "pending": True, "cid": cid,
                "detail": "the writer on this computer has not answered yet; the page shows "
                          "the save as soon as it is on disk"}

    def _writer_thread(self, cmd, cid, ev):
        """Runs one writer for `_run_writer`, and records its verdict for GET /landing?cid=."""
        import time as _time
        try:
            got = self._writer_finish(self._run_writer_raw(cmd), cid)
        except BaseException:
            got = {"ok": False, "error": "the Reader failed while running the writer"}
        with self._sent_mx:
            self._sent[cid] = {"state": "done", "reply": got, "at": _time.time()}
        ev.set()

    def _writer_finish(self, got, cid):
        """What the page is told about one writer's verdict `got`."""
        if got.get("no_answer") and cid:
            # A writer that was stopped, or gave no answer, after it wrote: its record says the
            # change is on disk, so this is a save, not a failure. (A writer that answered "no"
            # wrote nothing, or deleted its record, so it is not asked.)
            got = self._sent_on_disk(cid) or got
        if got.get("ok") and got.get("queued") and self.landing not in (None, INLINE_LANDING):
            self.landing.notify()
        if got.get("ok") and self.landing is None and got.get("landed") is False:
            got["land_detail"] = ("landing is off on this reader (--no-land), so the change is "
                                  "saved on this computer and was NOT committed or pushed")
        return got

    def _run_writer_raw(self, cmd):
        import time as _time
        env = dict(os.environ, ORCHDOC_WORKSPACE=str(self.workspace),
                   ORCHDOC_REF=os.environ.get("ORCHDOC_REF") or orchdoc.CANONICAL_REF)
        env[orchdoc.READER_WRITE_ENV] = "1"
        env = orchdoc.owner_writer_env(env)
        for attempt in range(WRITER_LOCK_RETRIES + 1):
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=WRITER_HARD_CAP,
                                   encoding="utf-8", errors="replace", env=env,
                                   creationflags=NO_WINDOW)
            except subprocess.TimeoutExpired:
                # Plain words for the one case left: a writer hung for WRITER_HARD_CAP seconds.
                # `_writer_finish` still reports a save whose record is on disk as saved.
                return {"ok": False, "no_answer": True,
                        "error": "the writer on this computer ran for %d minutes without "
                                 "answering and was stopped; nothing it did was confirmed"
                                 % (WRITER_HARD_CAP // 60)}
            except Exception as ex:
                return {"ok": False, "no_answer": True,
                        "error": "the writer could not be run (%s)" % type(ex).__name__}
            out = (r.stdout or "").strip().splitlines()
            for line in reversed(out):
                try:
                    got = json.loads(line)
                except ValueError:
                    continue
                if isinstance(got, dict) and "ok" in got:
                    return got
            err = [ln.strip() for ln in (r.stderr or "").splitlines() if ln.strip()]
            # `orchdoc._lock` gave up before anything was read or written: run it again.
            if err and "is locked by another orchestrator" in err[-1] \
                    and attempt < WRITER_LOCK_RETRIES:
                _time.sleep(1.0 + attempt)
                continue
            break
        # ⛔ NO PARSEABLE VERDICT IS A FAILURE, NOT A SUCCESS. A writer that printed something
        # unexpected has not told us it wrote anything, and reporting "saved" on that basis is
        # the silent-loss case this whole feature is built around.
        return {"ok": False, "no_answer": True,
                "error": "the writer gave no verdict (exit %d)%s - nothing was confirmed"
                         % (r.returncode, (": " + err[-1][:200]) if err else "")}


def make_handler(reader):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def handle_one_request(self):
            """⛔ A CLIENT THAT GOES AWAY IS ORDINARY, NOT AN ERROR.

            HTTP/1.1 keep-alive means a browser holds this connection open between requests,
            and it drops it whenever it likes - a closed tab, a reload, a navigation, or the
            server being stopped underneath it. The read then raises out of the handler
            THREAD, which prints a traceback nobody can act on next to a page that worked
            perfectly. `_send` has tolerated this on the WRITE side since the reader was
            built; the read side was simply never hit until a test started and stopped the
            server in the same process.

            ⭐ IT MATTERS BEYOND TIDINESS. The human leaves this page open all day, so these
            fire in normal use - and a traceback that appears during correct operation is
            how a real one stops being read.

            The clock starts in `parse_request`, after the request line has arrived,
            so the time a kept-alive connection sat idle before it is not counted."""
            self._t0 = None
            try:
                BaseHTTPRequestHandler.handle_one_request(self)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError,
                    TimeoutError):
                self.close_connection = True
            finally:
                if getattr(self, "_counted", False):
                    self._counted = False
                    _flight_add(-1)
                if self._t0 is not None:
                    self._timing_line()
                _REQ.t = None

        def parse_request(self):
            self._t0 = time.perf_counter()
            self._at = time.time()
            self._code = None
            self._bytes = 0
            _REQ.t = {}
            self._land0 = _landing_brief(reader.landing)
            _flight_add(1)
            self._counted = True
            return BaseHTTPRequestHandler.parse_request(self)

        def send_response(self, code, message=None):
            self._code = code
            BaseHTTPRequestHandler.send_response(self, code, message)

        def _timing_line(self):
            """One line for the request just answered. ⛔ The path without its query string
            and fragment, and nothing the page sent."""
            if reader.reqlog is None:
                return
            try:
                t = _REQ.t or {}
                land1 = _landing_brief(reader.landing)
                states = (self._land0[0], land1[0])
                landing = "running" if "running" in states else land1[0]
                route = urllib_path(getattr(self, "path", "") or "")[:200] or "-"
                line = ("req %s %s %s %s total=%dms lock=%dms build=%dms render=%dms "
                        "write=%dms send=%dms bytes=%d landing=%s waiting=%d"
                        % (log_time(self._at),
                           re.sub(r"[^A-Z]", "", str(getattr(self, "command", "") or ""))[:10]
                           or "-", re.sub(r"\s", "", route) or "-",
                           self._code if self._code is not None else "-",
                           (time.perf_counter() - self._t0) * 1000.0, t.get("lock", 0),
                           t.get("build", 0), t.get("render", 0), t.get("write", 0),
                           t.get("send", 0), self._bytes, landing,
                           max(self._land0[1], land1[1])))
                reader.reqlog(line)
            except Exception:
                pass      # the log must never be the reason a request fails

        def _write_body(self, raw):
            self._bytes = getattr(self, "_bytes", 0) + len(raw)
            with _timed("send"):
                try:
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                    pass

        def _send(self, body, ctype="text/html; charset=utf-8", code=200):
            raw = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            # ⛔ NO Access-Control-Allow-* HEADER IS EVER SENT, and that is what makes the
            # JSON content-type requirement bite: the preflight a JSON POST triggers has
            # nothing to succeed against. Shared with the dashboard so one server cannot
            # ship three of the four.
            for _k, _v in GATE.SAFE_HEADERS:
                self.send_header(_k, _v)
            self.end_headers()
            self._write_body(raw)

        def _send_bytes(self, raw, ctype, code=200):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            for _k, _v in GATE.SAFE_HEADERS:
                self.send_header(_k, _v)
            self.end_headers()
            self._write_body(raw)

        def _json(self, obj, code=200):
            self._send(json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8",
                       code)

        def _host_ok(self):
            """⛔ THE HOST HEADER MUST NAME THIS SERVER. A DNS-rebinding page is same-origin by
            the time it asks, so the browser will let it READ the answer - and every GET route
            answers with workspace content, `/` with the write token too. This is the one
            header such a page cannot choose, because it carries the attacker's own hostname.
            `do_GET` runs it before any route."""
            port = self.server.server_address[1]
            return (self.headers.get("Host") or "").strip().lower() in (
                "127.0.0.1:%d" % port, "localhost:%d" % port)

        def do_GET(self):
            path = self.path.split("?")[0]
            # ⛔ FIRST, FOR EVERY ROUTE, SO NO ROUTE CAN BE ADDED WITHOUT IT. A route list is
            # exactly what drifts.
            if not self._host_ok():
                self._send("refused: this request did not name this server as its host",
                           "text/plain; charset=utf-8", 403)
                return
            try:
                if not HAS_SCRIPTS and path.startswith(SCRIPT_ROUTES):
                    self._send("not found", "text/plain; charset=utf-8", 404)
                    return
                if path == "/":
                    # ⛔ A GET NEVER MUTATES ANYTHING A CLIENT NAMED. It rebuilds our own page
                    # from our own files; it takes no id, no text and no body. Every write is
                    # a POST, because a GET can pass none of the four checks.
                    def _home():
                        return reader.home_page()
                    page = reader.single_flight(_home)
                    self._send(page)
                    return
                if path.startswith("/batch/"):
                    # READ-ONLY: it renders one batch.json and writes nothing; the id is checked
                    # against R<digits> by batch_approval.
                    import batch_approval
                    body, code = batch_approval.page_for(urllib.parse.unquote(path[7:]))
                    self._send(body, "text/html; charset=utf-8", code)
                    return
                if path.startswith("/file/"):
                    # ⛔ READ-ONLY, CONFINED LIKE /img/. One text file inside the workspace,
                    # re-resolved from the path the browser sent; 404 on anything else.
                    got, why = reader.viewable(urllib.parse.unquote(path[6:]))
                    if got is None:
                        self._send("<!doctype html><meta charset=utf-8><title>not shown</title>"
                                   "<p>Not shown: %s</p><p><a href=\"/\">Back to the reader"
                                   "</a></p>" % html.escape(why or "no such file"),
                                   "text/html; charset=utf-8", 404)
                        return
                    text, relp, kind = got
                    ctx = None
                    if kind == "markdown":
                        ctx = _links_ctx(XrefTargets(reader.index()),
                                         base_rel=posixpath.dirname(relp), href_prefix="/")
                    self._send(render_file_page(
                        relp, text, kind, ctx,
                        token=reader.token if discover_docs(reader.workspace) else None))
                    return
                if path.startswith("/script/"):
                    # ⛔ AN ID, NEVER A PATH. `describe` refuses anything it did not issue.
                    d = reader.scripts.describe(urllib.parse.unquote(path[len("/script/"):]))
                    if not d.get("ok"):
                        self._json({"ok": False, "error": d.get("error")}, d.get("code", 404))
                        return
                    d["html"] = md_to_html(SCRIPTS.hide_library_header(d["text"]), script=True)
                    d["prov_html"] = render_prov_line(d.get("prov"))
                    d["notes"] = reader.script_notes.list(d.get("srel"))
                    self._json(d)
                    return
                if path.startswith("/script-diff/"):
                    parts = urllib.parse.unquote(path[len("/script-diff/"):]).split("/")
                    if len(parts) != 2:
                        self._json({"ok": False, "error": "a comparison names two script ids"},
                                   400)
                        return
                    a, b = (reader.scripts.describe(x) for x in parts)
                    bad = a if not a.get("ok") else b if not b.get("ok") else None
                    if bad:
                        self._json({"ok": False, "error": bad.get("error")},
                                   bad.get("code", 404))
                        return
                    dd = script_diff(a["text"], b["text"])
                    self._json(dict(dd, ok=True, **{"from": a["name"], "to": b["name"]}))
                    return
                if path.startswith("/script-compare/"):
                    # ⛔ AN ID, NEVER A PATH - either side of the pair may be named. Rebuilt
                    # from the files on every request, never cached, so an edit to the draft
                    # or the rewrite shows the next time the page asks.
                    v = reader.compare.view(urllib.parse.unquote(path[len("/script-compare/"):]))
                    if not v.get("ok"):
                        self._json({"ok": False, "error": v.get("error")}, v.get("code", 404))
                        return
                    body, stats = COMPARE.render(v)
                    self._json({"ok": True, "id": v["draft"]["id"],
                                "rewrite_id": v["rewrite"]["id"],
                                "draft_name": v["draft"]["name"],
                                "rewrite_name": v["rewrite"]["name"],
                                "folder": v["draft"]["folder"], "notes_rel": v["notes_rel"],
                                "notes_error": v["notes_error"], "html": body, "stats": stats,
                                "landing": reader.lander is not None})
                    return
                if path == "/library-sync-status":
                    # also whether that landing's commit is inside the run's workspace commit.
                    st = reader.runner.status()
                    q = urllib.parse.parse_qs(self.path.partition("?")[2])
                    jid = (q.get("job") or [""])[0]
                    job = reader.lander.status(jid) if (jid and reader.lander) else None
                    if job:
                        st["covers"] = (reader.runner.covers(job.get("sha"),
                                                             st.get("workspace_sha"),
                                                             st.get("library_commits"))
                                        if st.get("known") else None)
                        st["started"] = job.get("runner")
                    self._json(dict(st, ok=True))
                    return
                if path.startswith("/script-landing/"):
                    st = reader.lander.status(path[len("/script-landing/"):]) \
                        if reader.lander else None
                    if st is None:
                        self._json({"ok": False,
                                    "error": "no landing by that id on this run of the reader "
                                             "- if it restarted, the save itself is still on "
                                             "disk"}, 404)
                        return
                    self._json(dict(st, ok=True))
                    return
                if path == "/documents":
                    # ⛔ READ-ONLY, like /done: lists the folders the user chose, no token.
                    self._send(render_documents_page(reader.workspace))
                    return
                if path == "/done":
                    # ⛔ NO TOKEN, NO WRITE UI, NO LOGIN - a read-only page (RULE ONE).
                    self._send(render_done_page())
                    return
                if path == "/done/data":
                    if DONE is None:
                        self._json({"ok": False, "error": "done_feed.py could not be imported "
                                                          "(it has to sit in the same folder "
                                                          "as this file)"}, 500)
                        return
                    q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                    w = (q.get("w") or ["48h"])[0]
                    if w not in DONE_WINDOWS:
                        w = "48h"
                    # ⭐ NEVER WAITS. A window whose commits are not all read yet answers with
                    # what is ready and "loading": true, and a background thread reads the
                    # rest; the page shows that and asks again a few seconds later.
                    self._json(DONE.feed(reader.workspace, DONE.DEFAULT_REF,
                                         DONE.parse_window(w), block=False))
                    return
                if path == "/search":
                    # A GET that reads only: a term in, hits out of our own documents.
                    q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                    self._send(json.dumps(search_all(
                        reader, (q.get("q") or [""])[0][:200])),
                        "application/json; charset=utf-8")
                    return
                if path == "/health":
                    self._send(json.dumps({"ok": True, "docs": len(reader.index())}),
                               "application/json; charset=utf-8")
                    return
                if path == "/doc-counts":
                    # The same models as GET /: a changed file is read again once, by whichever
                    # asks first.
                    idx = reader.index()
                    with _timed("render"):
                        obj = rail_counts(idx)
                    self._json(obj)
                    return
                if path == "/landing":
                    # Reads a folder listing and the worker's memory only. `?cid=a,b`: also what
                    # became of those saves (see `sent_status`).
                    q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                    cids = [c for c in ",".join(q.get("cid") or []).split(",") if c][:50]
                    self._json(reader.landing_status(cids))
                    return
                if path.startswith("/img/"):
                    # ⛔ A GET STILL MUTATES NOTHING. This reads one file, inside the
                    # workspace, with a raster extension, and 404s on anything else.
                    got = reader.asset(urllib.parse.unquote(path[5:]))
                    if got is None:
                        self._send("no such image in this workspace",
                                   "text/plain; charset=utf-8", 404)
                        return
                    self._send_bytes(got[0], got[1])
                    return
                if path.startswith("/revs/"):
                    got = reader.entry_hashes(urllib.parse.unquote(path[len("/revs/"):]))
                    if got is None:
                        self._json({"ok": False, "error": "no such document"}, 404)
                        return
                    self._json({"ok": True, "rev": got[0], "ids": got[1]})
                    return
                if path.startswith("/entry/"):
                    # `?show_doc=1`: as the plate and Do-today lists show it, with its doc's
                    # name.
                    parts = urllib.parse.unquote(path[len("/entry/"):]).split("/")
                    q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                    # The entry's own doc is modelled again if its file changed (that is what
                    # the entry shows), still without the lock; the others, for references, are
                    # the models this process already has.
                    d, idx = (reader.entry_context(parts[0]) if len(parts) == 2
                              else (None, []))
                    with _timed("render"):
                        ents = (d or {}).get("entries") or ()
                        pos = next((n for n, i in enumerate(ents) if i["id"] == parts[1]), None)
                        if pos is None:
                            self._json({"ok": False, "error": "no such entry"}, 404)
                            return
                        it = ents[pos]
                        show_doc = (q.get("show_doc") or [""])[0] == "1"
                        raw = render_entry_html(it, show_doc=show_doc, xref=XrefTargets(idx))
                        # `after`: the entry before it in its doc, so a page that does not have
                        # this entry yet (a W item just made from the instruction box) can put
                        # it, and its rail button, in place without reading the whole view.
                        out = {"ok": True, "html": raw, "h": entry_hash(it),
                               "after": ents[pos - 1]["id"] if pos else "",
                               "nav": render_nav_item(it, show_doc=show_doc)}
                    self._json(out)
                    return
                if path.startswith("/doc/"):
                    slug = path[5:]
                    idx = reader.index()
                    with _timed("render"):
                        xref = XrefTargets(idx)
                        code, obj = 200, None
                        # `plate` is the hash the human types and the one the rail now writes;
                        # the internal key stays `__plate__`.
                        if slug in ("__plate__", "plate"):
                            nav, body = render_plate_view(idx, xref)
                            obj = {"nav": nav, "body": render_today_strip(idx) + body}
                        elif slug in (TODAY_VIEW, "today"):
                            nav, body = render_today_view(idx, xref)
                            obj = {"nav": nav, "body": body, "count": today_count(idx)}
                        elif slug == "rule":
                            nav, body = render_rules_view(xref)
                            obj = {"nav": nav, "body": body}
                        elif HAS_SCRIPTS and slug in (SCRIPTS_VIEW, "scripts"):
                            nav, body = render_scripts_view(
                                reader.scripts.annotate_provenance(reader.scripts.discover()),
                                fr_marks=reader.fr_marks, workspace=reader.workspace)
                            obj = {"nav": nav, "body": RUNNER.status_line_html(
                                reader.runner.status(), html.escape) + body}
                        else:
                            d = next((d for d in idx if d["slug"] == slug), None)
                            if d is not None:
                                nav, body = render_doc_view(d, xref)
                                obj = {"nav": nav, "body": render_today_strip(idx) + body}
                            else:
                                code, obj = 404, {"nav": "", "body": "not found"}
                        raw = json.dumps(obj, ensure_ascii=False)
                    self._send(raw, "application/json; charset=utf-8", code)
                    return
                self._send("not found", "text/plain; charset=utf-8", 404)
            except Exception as e:  # never let one bad doc take the reader down
                self._send("reader error: %s" % html.escape(str(e)),
                           "text/plain; charset=utf-8", 500)

        def _today_post(self):
            """§2.0: add / done / undo / stop. Same gate as a note; `orchdoc.py today` writes."""
            payload, err = _reader_gate(self.headers, self.rfile, reader.token,
                                              reader.port, host=HOST)
            if err:
                self._send(json.dumps({"ok": False, "error": err[1]}),
                           "application/json; charset=utf-8", err[0])
                return

            def no(why):
                self._send(json.dumps({"ok": False, "error": why}),
                           "application/json; charset=utf-8", 400)
            cid = payload.get("cid")
            if cid is not None and not (isinstance(cid, str) and orchdoc.CLIENT_ID_RE.match(cid)):
                return no(CID_REFUSAL)
            slug, act = payload.get("doc"), payload.get("act")
            # o9cL1: one doc's model, without the global lock (see `Reader.doc_model`).
            model = reader.doc_model(slug)
            if model is None:
                return no("no such document")
            if act == "add":
                text, repeat = payload.get("text"), payload.get("repeat") or "no"
                if not isinstance(text, str) or not text.strip() or len(text) > NOTE_MAX_CHARS:
                    return no("an item needs text, at most %d characters" % NOTE_MAX_CHARS)
                if not isinstance(repeat, str) or orchdoc.parse_repeat(repeat) is None:
                    return no("that is not a repeat period this understands")
                args = ["--add", text, "--repeat", repeat]
            elif act in ("done", "undo", "stop"):
                eid = payload.get("id")
                if not any(i["id"] == eid and i.get("today") for i in model["entries"]):
                    model = reader.doc_model(slug, fresh=True) or model
                if not any(i["id"] == eid and i.get("today") for i in model["entries"]):
                    return no("that is not a §2.0 item on this page - reload it")
                args = ["--" + act, eid]
            elif act == "priority":
                eid, choice = payload.get("id"), payload.get("choice")
                import priority as _prio_w
                if not isinstance(choice, str) or _prio_w.choice_norm(choice) is None:
                    return no("the priority is one of: " + ", ".join(_prio_w.CHOICES))
                if not any(i["id"] == eid and not i["archived"] for i in model["entries"]):
                    model = reader.doc_model(slug, fresh=True) or model
                if not any(i["id"] == eid and not i["archived"] for i in model["entries"]):
                    return no("that is not a live entry on this page - reload it")
                args = ["--priority", eid, "--choice", _prio_w.choice_norm(choice)]
            elif act in ("status", "unstatus"):
                # `orchdoc.py resolve` writes it and the landing worker lands it; no model is
                # involved.
                import reader_status as _rs
                eid = payload.get("id")
                _m_id = orchdoc.ID_RE.match(eid) if isinstance(eid, str) else None
                if not _m_id or _m_id.group(1) != eid:
                    return no("that is not an entry id")
                path = reader.doc_path(slug)
                if act == "status":
                    choice, reason = payload.get("choice"), payload.get("reason") or ""
                    if choice not in _rs.STATUS_FOR:
                        return no("the choice is done or superseded")
                    if not isinstance(reason, str) or len(reason) > 1000:
                        return no("the reason is one short line")
                    if not any(i["id"] == eid and _rs.offer(i) for i in model["entries"]):
                        model = reader.doc_model(slug, fresh=True) or model
                    if not any(i["id"] == eid and _rs.offer(i) for i in model["entries"]):
                        return no("that entry is not open on this page - reload it")
                    wargs = _rs.writer_args(path, eid, choice, reason)
                else:
                    undo = payload.get("undo")
                    if not isinstance(undo, dict) or len(json.dumps(undo)) > 20000:
                        return no("nothing to undo")
                    wargs = ["resolve", eid, "--doc", str(path), "--status", "OPEN",
                             "--ruling", "undo", "--undo-json", json.dumps(undo)]
                    if not _rs.is_own_doc(path):
                        wargs.append("--not-mine")
                self._send(json.dumps(reader._run_writer(
                    [sys.executable, str(Path(orchdoc.__file__).resolve())] + wargs +
                    ["--json"], cid, WRITER_BG_WAIT if (cid and payload.get("bg")) else None)),
                    "application/json; charset=utf-8", 200)
                return
            else:
                return no("unknown action")
            path = reader.doc_path(slug)
            self._send(json.dumps(reader._run_writer(
                [sys.executable, str(Path(orchdoc.__file__).resolve()), "today",
                 "--doc", str(path), "--json"] + args, cid,
                WRITER_BG_WAIT if (cid and payload.get("bg")) else None)),
                "application/json; charset=utf-8", 200)

        def _image_post(self):
            """A pasted image. The same token and host checks as a note, a larger body."""
            import base64 as _b64
            payload, err = _reader_gate(self.headers, self.rfile, reader.token,
                                              reader.port, host=HOST,
                                              max_body=PASTE_MAX_BYTES * 4 // 3 + 4096)
            if err:
                self._send(json.dumps({"ok": False, "error": err[1]}),
                           "application/json; charset=utf-8", err[0])
                return
            slug, data = payload.get("doc"), payload.get("data")
            if not isinstance(slug, str) or not isinstance(data, str):
                self._send(json.dumps({"ok": False, "error": "bad request shape"}),
                           "application/json; charset=utf-8", 400)
                return
            if reader.doc_path(slug) is None:
                self._send(json.dumps({"ok": False, "error": "no such document"}),
                           "application/json; charset=utf-8", 400)
                return
            try:
                raw = _b64.b64decode(data.split(",", 1)[-1], validate=True)
            except ValueError:
                self._send(json.dumps({"ok": False, "error": "the image data is not base64"}),
                           "application/json; charset=utf-8", 400)
                return
            self._send(json.dumps(reader.save_pasted_image(slug, raw)),
                       "application/json; charset=utf-8", 200)

        def do_POST(self):
            """The ONLY writes on this server. Four checks, then an allowlist, then the tool.

            ⛔ THE BROWSER MAY NAME AN ENTRY, NEVER A PATH AND NEVER A FIELD. The doc slug is
            resolved against the documents this server discovered; the entry id must exist in
            that document; a note id must already be on that entry. Everything else the page
            sends is the human's own prose, which goes to `orchdoc.py note` and nowhere near a
            filename.

            ⛔ TWO ROUTES, ONE GATE. `/instruct` writes a line that will BLOCK another
            orchestrator's turn until they answer it, so it runs through the SAME four checks
            and the SAME allowlist rather than a parallel path. A second copy of a gate is how
            one of them ends up a check short, and the one that would be short here is the one
            with the larger consequence.

            ⛔ AND THE PAGE MAY NOT NAME A DESTINATION. Which orchestrator is obliged by an
            instruction follows from the document the entry was found in, derived after the
            allowlist has run. A request that tries to name one is refused outright rather than
            having the field quietly ignored, so a mistake is visible instead of silent.
            """
            if _DRAINING.is_set():
                # Nothing of this write was read or taken; the page may send it again in a few
                # seconds.
                self.close_connection = True
                self._send(json.dumps({"ok": False, "error": "it never reached the writer: the "
                                       "Reader is restarting onto new code; it is sent again in "
                                       "a few seconds"}),
                           "application/json; charset=utf-8", 503)
                return
            path = urllib_path(self.path)
            if not HAS_SCRIPTS and path.startswith(SCRIPT_ROUTES):
                self._send(json.dumps({"ok": False, "error": "this Reader has no script features"}),
                           "application/json; charset=utf-8", 404)
                return
            if path in _SCRIPT_KEYS:
                self._script_post(path)
                return
            if path == "/full-rewrite/review":
                self._full_rewrite_post()
                return
            if path.startswith("/script-review-request/"):
                self._review_request_post(
                    urllib.parse.unquote(path[len("/script-review-request/"):]))
                return
            if path == "/image":
                self._image_post()
                return
            if path == "/today":
                self._today_post()
                return
            if path == "/file-note":
                self._file_note_post()
                return
            if path not in ("/note", "/instruct", "/instruct-doc"):
                self._send(json.dumps({"ok": False, "error": "not found"}),
                           "application/json; charset=utf-8", 404)
                return
            payload, err = _reader_gate(self.headers, self.rfile, reader.token,
                                              reader.port, host=HOST)
            if err:
                self._send(json.dumps({"ok": False, "error": err[1]}),
                           "application/json; charset=utf-8", err[0])
                return

            # ⛔ A REQUEST THAT NAMES A DESTINATION IS REFUSED, NOT SANITISED. These keys
            # are never read - the role comes from the resolved document - so ignoring them
            # would be safe and would also mean a page (or a bug) could send them forever
            # while believing they worked. Refusing makes the mistake loud at the moment it
            # is made.
            named = [k for k in ("role", "to", "mailbox", "path", "doc_path", "kind")
                     if k in payload]
            if named:
                self._send(json.dumps(
                    {"ok": False,
                     "error": "this endpoint does not accept %s - an instruction is written "
                              "to the entry the page named and to nowhere else, and which "
                              "document that is comes from this server"
                              % ", ".join(sorted(named))}),
                    "application/json; charset=utf-8", 400)
                return

            # ⭐ AN INSTRUCTION FOR THE WHOLE DOC names no entry - it BECOMES one, a new §3 W
            # item that `orchdoc.py instruct` allocates. So it names only the document, which
            # goes through the same allowlist below.
            if path == "/instruct-doc":
                if "id" in payload or "nid" in payload or "del" in payload:
                    self._send(json.dumps(
                        {"ok": False, "error": "an instruction for the whole doc names no "
                                               "entry and no note"}),
                        "application/json; charset=utf-8", 400)
                    return
                payload = dict(payload, id="")
            slug = payload.get("doc")
            eid = payload.get("id")
            nid = payload.get("nid") or ""
            text = payload.get("text") or ""
            delete = bool(payload.get("del"))
            if not isinstance(slug, str) or not isinstance(eid, str) \
                    or not isinstance(nid, str) or not isinstance(text, str):
                self._send(json.dumps({"ok": False, "error": "bad request shape"}),
                           "application/json; charset=utf-8", 400)
                return
            cid = payload.get("cid")
            if cid is not None and not (isinstance(cid, str) and orchdoc.CLIENT_ID_RE.match(cid)):
                self._send(json.dumps({"ok": False, "error": CID_REFUSAL}),
                           "application/json; charset=utf-8", 400)
                return
            # ⛔ AN INSTRUCTION IS ONLY EVER ADDED. Editing or deleting one is the notes UI's
            # job and goes through `/note`, because neither can unsend the message that has
            # already left - and an endpoint that appeared to would be lying.
            if path == "/instruct" and (delete or nid):
                self._send(json.dumps(
                    {"ok": False,
                     "error": "an instruction is only ever added here; edit or delete it as "
                              "a note"}),
                    "application/json; charset=utf-8", 400)
                return
            if not delete and (not text.strip() or len(text) > NOTE_MAX_CHARS):
                self._send(json.dumps({"ok": False,
                                       "error": "a note must have text, and at most %d "
                                                "characters" % NOTE_MAX_CHARS}),
                           "application/json; charset=utf-8", 400)
                return

            # o9cL1: `"bg": 1` - the page's outbox: answer at once, the writer goes on.
            wait = WRITER_BG_WAIT if (cid and payload.get("bg")) else None
            # ⛔ THE ALLOWLIST. Resolve against what was actually served, exactly as the
            # dashboard resolves a mail key: an id that is not on this page is refused rather
            # than passed through to a writer.
            # o9cL1: against the one doc's model, without the lock every page request waits on
            # (see `Reader.doc_model`); a stale model that lacks the entry or note is read again.
            model = reader.doc_model(slug)
            # ⛔ THE DOCUMENT IS REFUSED SEPARATELY FROM THE ENTRY, and not for tidiness.
            # A slug of `../../etc/hosts` was already refused - it matches no discovered
            # doc, so the entry lookup fell through - but it was refused with "that entry
            # is not on this page", which describes the wrong thing. Anyone auditing this
            # later would have to re-derive that the path never reached a filesystem call.
            # The behaviour is unchanged; what it SAYS about itself is now true.
            if model is None:
                self._send(json.dumps(
                    {"ok": False,
                     "error": "no document called %r is served by this reader - the page "
                              "names one of the docs it was given, never a path" % slug}),
                    "application/json; charset=utf-8", 400)
                return
            if path == "/instruct-doc":
                result = reader.write_doc_instruction(slug, text, cid=cid, wait=wait)
                if result.get("ok"):
                    result["role"] = model["slug"]
                self._send(json.dumps(result), "application/json; charset=utf-8", 200)
                return
            def _find(m):
                e = next((i for i in m.get("entries", []) if i["id"] == eid), None)
                ok = e is not None and (not nid or nid in [n["nid"] for n in e.get("notes") or ()])
                return e, ok
            entry, found = _find(model)
            if not found:
                model = reader.doc_model(slug, fresh=True) or model
                entry, found = _find(model)
            if entry is None:
                self._send(json.dumps({"ok": False,
                                       "error": "that entry is not on this page - reload it"}),
                           "application/json; charset=utf-8", 400)
                return
            if nid and nid not in [n["nid"] for n in entry.get("notes") or ()]:
                self._send(json.dumps({"ok": False,
                                       "error": "that note is not on this entry - reload it"}),
                           "application/json; charset=utf-8", 400)
                return

            if path == "/note":
                result = reader.write_note(slug, eid, nid, text, delete, cid=cid, wait=wait)
                # ⛔ A REFUSAL IS STILL A 200 WITH ok:false. The page has to distinguish "the
                # writer said no, here is why, keep your text" from "the request never
                # landed", and an HTTP error code makes those look identical to a fetch
                # handler.
                self._send(json.dumps(result), "application/json; charset=utf-8", 200)
                return

            # ⛔ THE DURABLE WRITE IS THE WHOLE ACTION.
            result = reader.write_note(slug, eid, "", text, False, kind="instruction", cid=cid,
                                       wait=wait)
            # ⛔ THE ROLE IS THE RESOLVED DOCUMENT'S OWN SLUG, never a string the browser
            # chose. `model` came out of the allowlist above, so this is a fact about a file on
            # disk - and it is reported so the page can name who has to acknowledge it.
            if result.get("ok"):
                result["role"] = model["slug"]
            self._send(json.dumps(result), "application/json; charset=utf-8", 200)

        def _file_note_post(self):
            """A note on a file in the workspace. The page names the file; this server checks it
            is one it would show, picks the OrchDoc (the first one found, or the `doc` slug the
            page sent), and writes through the same single writer as /note:

              no entry yet for this file  ->  `orchdoc.py instruct` opens one, titled
                                              "Notes on the file <path>.", with the path in it
              an entry already exists     ->  the note is added to that entry

            Either way it is an INSTRUCTION note, so the orchestrator must acknowledge it."""
            payload, err = _reader_gate(self.headers, self.rfile, reader.token, reader.port,
                                        host=HOST)
            if err:
                self._json({"ok": False, "error": err[1]}, err[0])
                return
            extra = sorted(set(payload) - {"token", "file", "text", "doc"})
            if extra:
                self._json({"ok": False, "error": "unexpected field(s) %s" % ", ".join(extra)}, 400)
                return
            file_, text, doc = payload.get("file"), payload.get("text"), payload.get("doc")
            if not isinstance(file_, str) or not isinstance(text, str) or \
                    not (doc is None or isinstance(doc, str)):
                self._json({"ok": False, "error": "bad request shape"}, 400)
                return
            if not text.strip() or len(text) > NOTE_MAX_CHARS:
                self._json({"ok": False, "error": "a note must have text, and at most %d "
                                                  "characters" % NOTE_MAX_CHARS}, 400)
                return
            got, why = reader.viewable(file_)
            if got is None:
                self._json({"ok": False, "error": "that file is not shown: %s" % why}, 400)
                return
            relp = got[1]
            docs = discover_docs(reader.workspace)
            slug = doc or (docs[0][0] if docs else "")
            model = reader.doc_model(slug, fresh=True) if slug else None
            if model is None:
                self._json({"ok": False, "error": "there is no OrchDoc to put the note in yet"}, 400)
                return
            marker = "Notes on the file %s." % relp
            hit = next((e for e in model.get("entries", [])
                        if marker in (e.get("raw_title") or e.get("title") or "")), None)
            if hit:
                result = reader.write_note(slug, hit["id"], "", text, False, kind="instruction")
            else:
                result = reader.write_doc_instruction(
                    slug, "%s The file: `%s`. Note: %s" % (marker, relp, text.strip()))
            if result.get("ok"):
                result["role"] = model["slug"]
            self._json(result)

        def _script_post(self, path):
            """The script writes. ⛔ THE SAME GATE AS /note - the same function, the same four
            checks - with a larger body cap because a script is larger than a note.

            ⛔ THE PAGE NAMES A SCRIPT BY THE ID THIS SERVER ISSUED, NEVER BY A PATH, AND NEVER
            SAYS WHERE A SAVE GOES. Every key is checked against a per-route allowlist: one
            that names a place is refused with a message that says so, and any other stray key
            is refused too - ignored, it would be a field the page believes does something.
            """
            # Never larger than /note's.
            payload, err = _reader_gate(
                self.headers, self.rfile, reader.token, reader.port,
                max_body=WHY_MAX_BODY if path in ("/script/why", "/script/approve-plan",
                                                  "/script/approve", "/script/note")
                else SCRIPT_MAX_BODY,
                host=HOST)
            if err:
                self._json({"ok": False, "error": err[1]}, err[0])
                return
            extra = sorted(set(payload) - _SCRIPT_KEYS[path])
            named = [k for k in extra if k.lower() in _PATH_KEYS]
            if named:
                self._json({"ok": False,
                            "error": "this endpoint does not accept %s - a script is named by "
                                     "the id this server issued, never by a path, and where a "
                                     "save goes is decided here, not by the page"
                                     % ", ".join(named)}, 400)
                return
            if extra:
                self._json({"ok": False,
                            "error": "unexpected field(s) %s - refused rather than ignored, so "
                                     "a mistake is loud" % ", ".join(extra)}, 400)
                return
            if path in ("/script/approve-plan", "/script/approve"):
                self._approve_post(path, payload.get("id"))
                return
            if path == "/script/note":
                self._script_note_post(payload)
                return
            text = payload.get("text")
            if not isinstance(text, str) or len(text) > SCRIPT_MAX_CHARS:
                self._json({"ok": False, "error": "the text must be a string of at most %d "
                                                  "characters" % SCRIPT_MAX_CHARS}, 400)
                return

            if path == "/script/why":
                # The page names the pair by its issued id and the change by the key this server
                # computed; the server re-reads the notes file under the lock and sets that one
                # entry.
                sid, key = payload.get("id"), payload.get("k")
                if not isinstance(sid, str) or not isinstance(key, str):
                    self._json({"ok": False, "error": "bad request shape"}, 400)
                    return
                result = reader.compare.save(sid, key, text)
                if not result.get("ok"):
                    self._json({"ok": False, "error": result.get("error")},
                               result.get("code", 400))
                    return
                result.pop("code", None)
                if result.get("changed"):
                    # ⭐ LANDED LIKE A REWRITE SAVE, by the same Lander, from its own worktree -
                    # never committed in this checkout.
                    if reader.lander is None:
                        result["landing"] = {
                            "state": "disabled", "branch": "",
                            "reason": "this reader was started with landing turned off "
                                      "(--no-land), so the note was NOT committed or pushed."}
                    else:
                        jid = reader.lander.submit(result["anchor_rel"], result["notes_rel"],
                                                   result.get("history_rels"), "why-notes",
                                                   result["opened_name"],
                                                   result.get("snapshot_rel"))
                        result["landing"] = reader.lander.status(jid)
                self._json(result)
                return

            if path == "/script/preview":
                # ⭐ WRITES NOTHING. It is a POST only because the text is too large for a URL,
                # and a POST here has to pass the gate like every other.
                base = payload.get("base")
                if not isinstance(base, str) or len(base) > SCRIPT_MAX_CHARS:
                    self._json({"ok": False, "error": "base must be a string"}, 400)
                    return
                dd = script_diff(base, text)
                self._json({"ok": True, "html": md_to_html(text, script=True),
                            "diff": dd["html"],
                            "added": dd["added"], "removed": dd["removed"],
                            "same": dd["same"]})
                return

            sid, expect = payload.get("id"), payload.get("expect")
            if not isinstance(sid, str) or not isinstance(expect, str):
                self._json({"ok": False, "error": "bad request shape"}, 400)
                return
            result = reader.scripts.save(sid, text, expect)
            if not result.get("ok"):
                self._json({"ok": False, "error": result.get("error")},
                           result.get("code", 400))
                return
            result.pop("code", None)
            if result.get("changed"):
                if reader.lander is None:
                    result["landing"] = {
                        "state": "disabled", "branch": "",
                        "reason": "this reader was started with landing turned off "
                                  "(--no-land), so the save was NOT committed or pushed."}
                else:
                    jid = reader.lander.submit(result["anchor_rel"], result["target_rel"],
                                               result.get("history_rels"), result["mode"],
                                               result["opened_name"],
                                               result.get("snapshot_rel"))
                    result["landing"] = reader.lander.status(jid)
            self._json(result)

        def _script_note_post(self, payload):
            """The page names the script by the id this server issued; the note goes to
            script_note_store (a per-machine file, one per script), never into the
            script file. The full text is kept or refused, never cut."""
            f, why = reader.scripts.resolve(payload.get("id"))
            if f is None:
                self._json({"ok": False, "error": why}, 400)
                return
            ok, got = reader.script_notes.add(f["relroot"], payload.get("text"), source="laptop")
            if not ok:
                self._json({"ok": False, "error": got}, 400)
                return
            self._json({"ok": True, "note": got, "notes": reader.script_notes.list(f["relroot"])})

        def _full_rewrite_post(self):
            """The same gate as every write (token, origin, size). The key is a label, never a
            path; the only thing written is the marks file
            (reader_full_rewrite.Marks)."""
            payload, err = _reader_gate(
                self.headers, self.rfile, reader.token, reader.port,
                max_body=WHY_MAX_BODY, host=HOST)
            if err:
                self._json({"ok": False, "error": err[1]}, err[0])
                return
            if (set(payload) - {"token", "key", "reviewed", "ver"}
                    or not isinstance(payload.get("reviewed"), bool)):
                self._json({"ok": False, "error": "bad request shape"}, 400)
                return
            # Only a script the list names can be marked; clearing any well-formed key is
            # allowed, so a stale mark can always be removed.
            if payload["reviewed"] and not FRW.known_key(payload.get("key"), reader.workspace):
                self._json({"ok": False, "error": "not a script on the list"}, 400)
                return
            ok, why = reader.fr_marks.set(payload.get("key"), payload["reviewed"],
                                          payload.get("ver"))
            if not ok:
                self._json({"ok": False, "error": why}, 400)
                return
            self._json({"ok": True, "reviewed": payload["reviewed"]})

        def _review_request_post(self, sid):
            """Approve & Review: write one review request file (never approve, never touch the
            script). The same gate as every script write (token, origin, size); the
            body carries the token and nothing else; the script is named by the id
            in the path (an id this server issued, never a file path) and resolved
            and confined by Corpus."""
            payload, err = _reader_gate(
                self.headers, self.rfile, reader.token, reader.port,
                max_body=WHY_MAX_BODY, host=HOST)
            if err:
                self._json({"ok": False, "error": err[1]}, err[0])
                return
            if set(payload) - {"token"} or not sid:
                self._json({"ok": False, "error": "bad request shape"}, 400)
                return
            result = reader.scripts.send_review_request(sid)
            code = result.pop("code", 200)
            self._json(result, code) if not result.get("ok") else self._json(result)

        def _approve_post(self, path, sid):
            """The token, origin and size checks already ran in _script_post (the same gate as
            every script write); the script is named by the id this server issued,
            and resolved and confined by Corpus. /script/approve is landed like a
            save."""
            if not isinstance(sid, str):
                self._json({"ok": False, "error": "bad request shape"}, 400)
                return
            if path == "/script/approve-plan":
                result = reader.scripts.approve_plan(sid)
                code = result.pop("code", 200)
                if not result.get("ok"):
                    # a refusal of the REQUEST (bad id, not approvable) is a 4xx; a failure of
                    # the command (missing, no answer) is a 200 the page shows in the pop-up
                    self._json(result, code)
                else:
                    self._json(result)
                return
            result = reader.scripts.approve(sid)
            code = result.pop("code", 200)
            prior = result.pop("prior", None)
            if not result.get("ok"):
                self._json(result, code)
                return
            if result.get("changed"):
                if reader.lander is None:
                    result["landing"] = {
                        "state": "disabled", "branch": "",
                        "reason": "this reader was started with landing turned off "
                                  "(--no-land), so the approval was NOT committed or pushed."}
                else:
                    moved = (result.get("folder_before") != result.get("folder_after")
                             or result.get("app_change") not in (None, "none"))
                    jid = reader.lander.submit_approve(result["adds"], result["removes"],
                                                       result["opened_rel"], result["name"],
                                                       prior=prior, start_runner=moved)
                    result["landing"] = reader.lander.status(jid)
            self._json(result)

        def log_message(self, *a):
            pass
    return H


def urllib_path(raw):
    return raw.split("?")[0].split("#")[0]


# See reader_restart.py.
_FLIGHT_MX = threading.Lock()
_FLIGHT = [0]
_DRAINING = threading.Event()


def _flight_add(n):
    with _FLIGHT_MX:
        _FLIGHT[0] += n


def _flight_now():
    with _FLIGHT_MX:
        return _FLIGHT[0]


def _bind(a, wait=None):
    """Bind the port. A process started by a restart (READER_BIND_WAIT seconds) retries until the
    process it replaces has closed its socket; any other start tries once. `wait=0` (an old
    process taking its port back) always tries once."""
    if wait is None:
        try:
            wait = float(os.environ.get("READER_BIND_WAIT") or 0)
        except ValueError:
            wait = 0.0
    end = time.monotonic() + wait
    while True:
        try:
            return _Server((HOST, a.port), _StartingHandler)
        except OSError:
            if time.monotonic() >= end:
                raise
            time.sleep(0.2)


class _Server(ThreadingHTTPServer):
    # ⛔ REFUSE THE PORT IF SOMETHING ELSE HAS IT. `HTTPServer` sets allow_reuse_address = 1,
    # and on Windows that lets a SECOND process bind a port the first one is still serving.
    # Both then appear to start fine and the OLD one keeps answering - so a restart looks
    # successful while serving stale code.
    allow_reuse_address = False
    daemon_threads = True


class _StartingHandler(BaseHTTPRequestHandler):
    """
    The logon task starts the Reader and gives it ~10 s to answer. Reading and auditing the
    documents takes about 6 s on a warm machine and far longer at logon. Binding only after
    that left the task reporting FAILED; and the work between the bind and the accept loop left
    the open tab's polls in the listen queue until they timed out and closed, which shows as
    server-side CLOSE_WAIT sockets nothing ever accepted. So the port is bound and accepted
    first, and every request gets this 503 (the task counts any HTTP status as an answer)
    until `main` swaps in the real handler."""
    protocol_version = "HTTP/1.1"
    timeout = 5        # a client that declares a body and never sends it cannot hold a thread

    def _starting(self):
        # ⛔ A WRITE IS REFUSED WHOLE. A POST here never reaches the gate, the token or any
        # writer: nothing in this class can write.
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if 0 < n <= 1 << 20:
            try:
                self.rfile.read(n)
            except OSError:
                pass
        body = (b"<!doctype html><meta charset=utf-8><meta http-equiv=refresh content=2>"
                b"<title>OrchDoc Reader starting</title><p>The Reader is starting. "
                b"This page reloads itself.</p>")
        try:
            self.send_response(503)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Retry-After", "2")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
        except OSError:
            pass
        self.close_connection = True

    do_GET = do_HEAD = do_POST = do_PUT = do_DELETE = do_OPTIONS = _starting

    def log_message(self, *args):
        pass


PROCESS_POWER_THROTTLING = 4               # PROCESS_INFORMATION_CLASS ProcessPowerThrottling
POWER_THROTTLING_EXECUTION_SPEED = 0x1


def opt_out_of_ecoqos(kernel32=None, throttle=False):
    """Left system-managed, Windows 11 put the live Reader (started below-normal, with no window)
    on efficiency scheduling: py-spy showed every function in a Scripts page load slower by
    the same factor, and the load cost 4.2-4.9 s of CPU against 0.8 s in a fresh Reader on
    the same code and files. Setting this on the live process alone brought it to 0.9 s.
    True when Windows took the setting; False elsewhere or on any failure (never raises).
    """
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class _State(ctypes.Structure):
            _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG),
                        ("StateMask", wintypes.ULONG)]
        k = kernel32 or ctypes.WinDLL("kernel32", use_last_error=True)
        k.GetCurrentProcess.restype = wintypes.HANDLE
        # Control the execution-speed bit, and set it to 0: "never throttle".
        st = _State(1, POWER_THROTTLING_EXECUTION_SPEED,
                    POWER_THROTTLING_EXECUTION_SPEED if throttle else 0)
        return bool(k.SetProcessInformation(wintypes.HANDLE(k.GetCurrentProcess()),
                                            PROCESS_POWER_THROTTLING, ctypes.byref(st),
                                            ctypes.sizeof(st)))
    except Exception:
        return False


def main(argv=None):
    """
    """
    held = {}
    try:
        return _main(argv, held)
    finally:
        srv = held.get("srv")
        if srv is not None:
            srv.shutdown()
            srv.server_close()


def _main(argv, held):
    ap = argparse.ArgumentParser(
        description="Serve a three-column reader for the OrchDocs.")
    ap.add_argument("--workspace", default=str(orchdoc.PROJECTS),
                    help="workspace root holding ORCHESTRATOR-DECISIONS-o*.md")
    ap.add_argument("--port", type=int, default=orchdoc.reader_port())
    # ⛔ THERE IS NO --host. See the HOST constant at the top: this server can now WRITE the
    # documents, so the bind address is not a thing an invocation may move.
    ap.add_argument("--open", action="store_true", help="open a browser at the URL")
    ap.add_argument("--once", action="store_true",
                    help="print coverage and exit - no server")
    ap.add_argument("--no-land", action="store_true",
                    help="save to disk but do NOT commit or push anything this reader saves: "
                         "notes, note edits and deletes, instructions, Do-today items, pasted "
                         "images (their notes) and script edits")
    ap.add_argument("--land-branch", default="main",
                    help="the branch a script save is pushed to (default main). A different "
                         "branch is for exercising the landing without touching main.")
    ap.add_argument("--land-worktree", default=None,
                    help="the dedicated landing worktree (default "
                         "<workspace>/.worktrees/reader-landing)")
    ap.add_argument("--self-restart", action="store_true",
                    help="restart onto new code when a file this Reader runs changes ; "
                         "always on for the default workspace, off when READER_SELF_RESTART=0")
    ap.add_argument("--warm-done", action="store_true",
                    help="warm the /done 48h cache at startup even though --workspace is not "
                         "the default workspace (the default workspace always warms)")
    ap.add_argument("--warm-scripts", action="store_true",
                    help="warm the Scripts view's provenance cache at startup even though "
                         "--workspace is not the default workspace ")
    a = ap.parse_args(argv)
    _boot = None
    if RESTART is not None and not a.once:
        try:
            _boot = RESTART.snapshot(Path(__file__).resolve())
        except Exception:
            _boot = None

    # \u26d4 THE DRIFT CHECK RUNS HERE. It existed from the day RETRACTED went missing and
    # was called from nowhere, while the comment above STATUS_CLASS claimed it made drift
    # impossible. A function that returns a list is not a check until something reads it.
    #
    # \u26a0\ufe0f It WARNS and never refuses. Refusing to render would take the instrument away
    # to report a small flaw in it.
    _uncoloured = _status_class_covers_vocabulary()
    if _uncoloured:
        sys.stderr.write(
            "\n[status-drift] %d status(es) orchdoc accepts have NO colour here: %s\n"
            "                They render grey, as though unrecognised. Add them to\n"
            "                STATUS_CLASS in render_orchdoc_reader.py.\n\n"
            % (len(_uncoloured), ", ".join(_uncoloured)))

    ws = Path(a.workspace)
    os.environ.setdefault("ORCHDOC_WORKSPACE", str(ws.resolve()))   # the settings file is found from it
    global ASSET_ROOT
    ASSET_ROOT = ws.resolve()
    # `--once` only audits and never serves.
    srv = None
    if not a.once:
        try:
            srv = _bind(a)
        except OSError as e:
            print("[FAIL] cannot bind %s:%d - %s" % (HOST, a.port, e))
            print("       something is already serving that port. Stop it, or pass --port.")
            return 2
        _accept = threading.Thread(target=srv.serve_forever, name="reader-accept", daemon=True)
        _accept.start()
        held["srv"] = srv                # main() shuts it down however _main ends
    idx = build_index(ws)
    if not idx:
        print("[FAIL] no ORCHESTRATOR-DECISIONS-o*.md found in %s" % ws)
        return 2

    print("workspace: %s" % ws)
    print("%-6s %8s %8s %s" % ("doc", "entries", "yours", "note"))
    for d in idx:
        print("%-6s %8d %8d %s" % (d["slug"], d["parsed_count"], d["plate_count"],
                                   "PARSER CANNOT READ THIS DOC" if d["unreadable"]
                                   else ""))
    print("%-6s %8d %8d" % ("TOTAL", sum(d["parsed_count"] for d in idx),
                            sum(d["plate_count"] for d in idx)))

    counts, unresolved = audit_xrefs(idx)
    print("")
    print("cross-doc references in entry bodies: %d found, %d linked live, %d linked "
          "ARCHIVED, %d unresolved, %d in a doc the parser cannot read"
          % (sum(counts.values()), counts["ok"], counts["archived"], counts["dead"],
             counts["blind"]))
    # ⛔ A ZERO HERE IS A DEAD BRANCH, NOT A QUIET CORPUS. 100+ references in this corpus
    # point at archived entries; if none are being recognised, they are all wearing the
    # unresolved cross again and nothing else in this output would say so.
    if not counts["archived"]:
        print("[WARN] NOT ONE reference resolved as archived. Either the corpus genuinely "
              "has none, or the archived branch is dead and correct citations are being "
              "marked broken. Check before trusting the unresolved list below.")

    cov = archived_coverage(ws)
    short = [r for r in cov if r["missing_lines"]]
    print("archived entries: %d in the files, %d reachable by id"
          % (sum(r["in_file"] for r in cov), sum(r["indexed"] for r in cov)))
    if short:
        # ⛔ NAMED, NOT SUMMARISED. These entries exist and no tool can see them, so every
        # reference to one renders as broken. The line numbers are what makes it fixable.
        print("[FAIL] %d archived entr%s unreachable - the parser cannot see %s:"
              % (sum(len(r["missing_lines"]) for r in short),
                 "y is" if sum(len(r["missing_lines"]) for r in short) == 1 else "ies are",
                 "it" if sum(len(r["missing_lines"]) for r in short) == 1 else "them"))
        for r in short:
            print("       %-4s %d of %d  - heading line(s) %s"
                  % (r["slug"], len(r["missing_lines"]), r["in_file"],
                     ", ".join(str(n) for n in r["missing_lines"])))
        print("       Usually an unclosed ``` fence above them: everything after it reads "
              "as code, so the headings stop being headings.")
    if a.once:
        # ⛔ ALL OF THEM, NEVER A SAMPLE. This list is the reason to run the audit,
        # and a truncated one hides precisely the reference somebody has to go and fix.
        for row in unresolved:
            print("   %-4s %-5s -> %s:%-6s %s" % row)
        return 0
    print("       (--once lists every unresolved one)")
    opt_out_of_ecoqos()

    lander = None if (a.no_land or not HAS_SCRIPTS) else SCRIPTS.Lander(
        ws, worktree=a.land_worktree, branch=a.land_branch)
    rlog = ReaderLog(reader_log_path(ws), own_dir=True)
    # Created before the bind but started after it, so a reader that cannot bind lands nothing.
    if a.no_land:
        landing = None
    elif LANDING is None:
        landing = INLINE_LANDING
    else:
        # Its lines get the request line's date and time after their first word (box 13).
        def make_landing():
            return LANDING.LandingWorker(ws, log=StampedLog(rlog),
                                         ref=orchdoc.CANONICAL_REF, watch_copies=True)
        landing = make_landing()
    if os.environ.get("READER_RESTARTED_FROM"):
        StampedLog(rlog)("restart: started onto new code, pid %d, replacing pid %s"
                         % (os.getpid(), os.environ.get("READER_RESTARTED_FROM")))
    reader = Reader(ws, token=GATE.mint_token(), port=a.port, lander=lander, landing=landing,
                    reqlog=rlog)
    if lander is not None:
        lander.on_approve_landed = lambda job: reader.runner.start()
    if landing not in (None, INLINE_LANDING):
        landing.start()
    srv.RequestHandlerClass = make_handler(reader)
    url = "http://%s:%d/" % (HOST, a.port)
    print("")
    print("reader on %s   (ctrl-c to stop)" % url)
    # The links `orchdoc.py add` and `plate` print are built from `orchdoc.link_port()`, so a
    # reader started on any other port has to be told to them, or those links reach nothing.
    _link_port = orchdoc.link_port()
    if a.port != _link_port:
        print("links: the links orchdoc.py prints point at port %d, and this reader is on port %d."
              % (_link_port, a.port))
        print("       To make them reach this reader, set \"reader_port\": %d in the workspace's"
              % a.port)
        print("       .orchdoc-config.json. ORCHDOC_READER_PORT moves only this reader's default"
              " port, not the links.")
    # ⛔ SAY THAT IT CAN WRITE. This used to be a read-only viewer, and a page that can now
    # edit eleven live documents should not become able to do so silently.
    print("notes: ON. Anything you write is saved INTO the OrchDoc by "
          "`orchdoc.py note`, so the")
    print("       orchestrator who owns the entry sees it.")
    if landing is None:
        print("       Landing is OFF (--no-land): notes, instructions and Do-today changes stay")
        print("       on disk, uncommitted, and the page says \"%s\"." % LANDING_OFF_TEXT)
    elif landing == INLINE_LANDING:
        print("[WARN] reader_landing.py could not be imported, so each save lands before the")
        print("       page gets its answer .")
    else:
        print("       Each save returns once it is on disk; a background worker then commits")
        print("       and pushes it to %s, oldest first, and retries until it is there."
              % orchdoc.CANONICAL_REF)
        print("       Waiting saves are in %s and survive a restart." % LANDING.QUEUE_REL)
    print("       In a folder that is not a git workspace with an origin remote, a save stays")
    print("       in the file on this computer, and the page says so after the save.")
    # ⛔ SAY WHAT AN INSTRUCTION ACTUALLY DOES, AND WHAT IT DOES NOT. It is written to the
    # document like a note; what makes it different is that the Stop hook refuses to let that
    # orchestrator finish a round until they acknowledge it. Nothing is sent anywhere.
    print("instructions: ON. \"Instruct o<N>\" writes the same way a note does, marked as an")
    print("       INSTRUCTION. `orchdoc_stop_check.py` - the Stop hook that already runs at")
    print("       every turn boundary - shows it to that orchestrator and blocks their round")
    print("       until they acknowledge it. Nothing is sent, and nothing has to be running.")
    # ⛔ AND SAY THAT IT CAN WRITE SCRIPTS, AND WHERE THE WRITES GO.
    _sc = reader.scripts_count()
    if HAS_SCRIPTS:
        print("scripts: %s. An agent draft is never written; a save goes to %s's rewrite of it,"
              % ("ON (%d in %s)" % (_sc, reader.scripts.rel) if _sc is not None
                 else "no corpus at %s" % reader.scripts.rel,
                 orchdoc.human_name().title()))
        print("       and a rewrite is snapshotted to .edit-history/ before it is replaced.")
        if lander is None:
            print("       Landing is OFF (--no-land): saves stay on disk, uncommitted.")
        else:
            print("       Each save is committed and pushed to %s/%s from %s"
                  % (lander.remote, lander.branch, lander.worktree))
            print("       - never from this checkout.")
    else:
        _folders = documents_folders()
        print("documents: %s" % (("ON (%s)" % ", ".join(_folders)) if _folders else
              "no folders chosen yet; name them with `orchdoc_config.py set doc_folders <folder>`"))
    print("files: /file/<path> shows a workspace text file read-only (what §1 links open).")
    print("log: one timing line per request, and each landing, printed here and kept in")
    print("       %s (at most 2 x %d MB)" % (rlog.path, READER_LOG_MAX // (1024 * 1024)))
    # ⭐ AFTER THE BIND, SO IT CANNOT DELAY STARTUP. Without it the first /done view of the day
    # read ~270 commits (~30 s) before showing anything. Only the live workspace warms by
    # default: a test that serves a throwaway workspace must not write the human's cache.
    if DONE is not None and (a.warm_done or ws.resolve() == Path(orchdoc.PROJECTS).resolve()):
        DONE.warm(str(ws), DONE.DEFAULT_REF, DONE.parse_window("48h"))
        print("done: /done is warming its 48h window in the background.")
    # Warm it after the bind, under the same rule as /done.
    if _sc is not None and (a.warm_scripts or ws.resolve() == Path(orchdoc.PROJECTS).resolve()):
        reader.scripts.warm()
        print("scripts: the Scripts view's provenance is warming in the background.")
        reader.script_text.refresh()
        print("scripts: the search text is being read in the background.")
    if a.open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    cur = {"accept": _accept, "busy": threading.Event()}

    def _idle():
        if _flight_now() != 0:
            return False
        if landing in (None, INLINE_LANDING):
            return True
        try:
            return not landing.brief()[0]
        except Exception:
            return False

    def _drain():
        # New writes get a 503 from now on; look again after a moment for a request that was
        # already read when the flag went up.
        _DRAINING.set()
        time.sleep(0.5)
        if _idle():
            return True
        _DRAINING.clear()
        return False

    def _answers():
        try:
            urllib.request.urlopen("http://%s:%d/landing" % (HOST, a.port), timeout=3).close()
            return True
        except urllib.error.HTTPError:
            return True                       # 503 "starting" is an answer
        except Exception:
            return False

    def _restart(reason):
        nonlocal landing
        say = StampedLog(rlog)
        cur["busy"].set()
        try:
            if landing not in (None, INLINE_LANDING):
                landing.stop(timeout=60)      # waits for a landing it is running; the rest stay queued
                if landing._thread is not None and landing._thread.is_alive():
                    # Wait out the job's own time limit, and only then stop it the way
                    # reader_landing documents as safe (its job file stays; a landing that
                    # already pushed is found on main).
                    say("restart: waiting for the landing this process is running to finish")
                    landing._thread.join(getattr(LANDING, "JOB_TIMEOUT", 900) + 60)
                    if landing._thread.is_alive():
                        landing.stop(timeout=30, kill=True)
                    if landing._thread.is_alive():
                        say("restart: the landing worker would not stop; not restarting")
                        landing = make_landing()
                        reader.landing = landing
                        return False
            try:
                from no_window import SERVER_FLAGS as _flags
            except ImportError:
                _flags = 0x08000000 | 0x00000200 if os.name == "nt" else 0
            env = dict(os.environ, READER_BIND_WAIT="90", READER_RESTARTED_FROM=str(os.getpid()))
            kw = {"cwd": os.getcwd(), "env": env, "stdin": subprocess.DEVNULL,
                  "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
            if os.name == "nt":
                kw["creationflags"] = _flags
            else:
                kw["start_new_session"] = True
            child = subprocess.Popen([sys.executable, str(Path(__file__).resolve())]
                                     + list(sys.argv[1:] if argv is None else argv), **kw)
            say("restart: new process pid %d started (%s)" % (child.pid, reason))
            old = held["srv"]
            old.shutdown()
            old.server_close()
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if _answers():
                    say("restart: port %d answered again from the new process" % a.port)
                    return True
                if child.poll() is not None:
                    say("restart: the new process exited with %s before answering" % child.returncode)
                    break
                time.sleep(0.25)
            else:
                # (the port is exclusive, so the new process then fails its own bind and ends);
                # if the new process holds it, it is serving and this one ends.
                say("restart: the new process has not answered after 90 s but is still running")
                try:
                    srv2 = _bind(a, wait=0)
                except OSError:
                    say("restart: the port is taken, so the new process has it; this one ends")
                    return True
                say("restart: the port was free, so this process serves it again")
                child = None
            # The new process is gone: serve again from here, with the code already loaded.
            if child is not None:
                for _ in range(20):
                    try:
                        srv2 = _bind(a, wait=0)
                        break
                    except OSError:
                        time.sleep(0.5)
                else:
                    say("restart: could not bind the port again after the new process failed")
                    return False
            srv2.RequestHandlerClass = make_handler(reader)
            held["srv"] = srv2
            cur["accept"] = threading.Thread(target=srv2.serve_forever, name="reader-accept",
                                             daemon=True)
            cur["accept"].start()
            if landing not in (None, INLINE_LANDING):
                landing = make_landing()
                reader.landing = landing
                landing.start()
            return False
        finally:
            cur["busy"].clear()

    _self_restart = (RESTART is not None and _boot is not None
                     and os.environ.get("READER_SELF_RESTART", "1") != "0"
                     and (a.self_restart or ws.resolve() == Path(orchdoc.PROJECTS).resolve()))
    if _self_restart:
        _say = StampedLog(rlog)
        _poll = float(os.environ.get("READER_RESTART_POLL") or RESTART.POLL_SECONDS)
        _settle = float(os.environ.get("READER_RESTART_SETTLE") or RESTART.SETTLE_SECONDS)
        _restarter = RESTART.Restarter(
            Path(__file__).resolve(), _boot, idle=_idle, drain=_drain,
            undrain=_DRAINING.clear, restart=_restart, log=_say, settle=_settle,
            lock_held=lambda: RESTART.deliver_lock_held(ws))
        RESTART.watch(_restarter, poll=_poll)
        print("restart: ON - this Reader restarts itself when one of its %d code files changes."
              % len(_boot))
        # The logon task and ensure_local_servers.py send stdout nowhere, so without this line
        # nothing shows that the watcher is running.
        _say("restart: watcher ON, pid %d, watching %d code files" % (os.getpid(), len(_boot)))
    try:
        while cur["accept"].is_alive() or cur["busy"].is_set():
            if cur["accept"].is_alive():
                cur["accept"].join(0.5)
            else:
                time.sleep(0.25)       # a restart is waiting for the new process
    except KeyboardInterrupt:
        print("")
        print("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
