#!/usr/bin/env python3
"""the human's scripts, inside the OrchDoc Reader: find them, read them, save his edits WITHOUT
touching the copy he was editing, and land every save to git.

<!-- subject: orchestration -->

<!-- route-tags: reader script editor pilots owner rewrite save snapshot edit-history landing
     worktree training loop corpus opaque id -->

The human, 2026-09-23, verbatim:

  *"A large part of our work is with Scripts. It would be far more efficient if I was able to
  access and read the scripts, as well as make inline edits (additions and deletions) to the
  script, have those edits saved, and not destroy the copy that I'm editing."*

This module is the half of that feature that touches the disk. `render_orchdoc_reader.py`
owns the page and the HTTP routes and calls in here; nothing in here renders a page.

⛔ THE BROWSER MAY NAME AN ENTRY, NEVER A PATH AND NEVER A FIELD. That is the reader's rule
for OrchDoc notes and it holds here unchanged. A script has no entry id, so this module
DISCOVERS the corpus and ISSUES an opaque id per file - an HMAC of the file's place in the
corpus under a key minted per server run. The page sends that id back and nothing else. An id
this server did not issue in its current discovery is refused; so is anything shaped like a
path, because it is not an id.

⛔ AN AGENT DRAFT IS NEVER WRITTEN. MASTER-TRAINING-DOC.md Section 3: every iteration is kept,
in full, forever, because the DELTA between a draft and the human's rewrite of it is the teaching
signal (Section 2). So a save made while editing a draft goes to that draft's rewrite sibling -
the existing one, in whichever of the five legacy spellings it carries, or a new
`<stem>-owner-rewrite.md` (Section 5's convention). The draft's bytes are hashed before and after
every save and the save reports both hashes, so "untouched" is a measurement rather than a
promise.

⛔ NOTHING IS OVERWRITTEN WITHOUT A RETAINED COPY. Saving over an existing rewrite first writes
its current bytes to `<folder>/.edit-history/<rewrite stem>/<UTC stamp>.snapshot`, reads them
back, compares, and only then replaces the rewrite. A snapshot that cannot be verified aborts
the save with the rewrite untouched.

⚠️ WHY SNAPSHOTS ARE SHAPED THE WAY THEY ARE. `training_header.py` counts any file whose NAME
carries a rewrite suffix as a live rewrite, and several scanners treat any `.md` as a document.
A snapshot named `...-the human-rewrite.md.<stamp>.md` would be counted as a second real rewrite and
would wear a live document's extension. So a snapshot:
  - lives under a HIDDEN directory (`.edit-history`), which `training_header.py` skips,
  - is named only by its timestamp, so the rewrite suffix is in its directory, not its name,
  - ends in `.snapshot`, so no `.md` scanner sees it.
Each of the three is sufficient for `training_header.py`; the test asserts the count of real
artifacts is unchanged by a save that made a snapshot.

⭐ AND EVERY SAVE IS LANDED TO GIT, BEST-EFFORT AND HONESTLY. Twice on 2026-09-22 a new file
sat on no permanent ref, one laptop away from gone - and the human's rewrites are the most valuable
files in this corpus. `Lander` copies the saved file(s) into a dedicated, persistent worktree,
commits exactly those paths and pushes. The disk write never waits on it and never depends on
it: a landing that fails leaves the disk copy and its snapshot exactly where they are, and the
page says it did not land, and why.
"""
import datetime
import difflib
import hashlib
import hmac
import json
import html
import os
import queue
import re
import secrets
import shutil
import subprocess
import sys
try:
    from no_window import NO_WINDOW
except ImportError:  # a partial delivery must not take the tool down
    NO_WINDOW = 0x08000000 if os.name == "nt" else 0
import threading
import time
import urllib.parse
import uuid
from pathlib import Path

import training_header

#: o9 W192: how long the approve command may run (it runs the whole approval on a copy of the
#: library first, then for real: about 8 s measured on 2026-10-11), and what the human is told when the
#: workspace's file_script.py has no approve command.
APPROVE_TIMEOUT = 600
APPROVE_MISSING = "Approving is not available yet: file_script.py has no approve command"
#: The one file outside script-library/ that `file_script.py approve` rewrites (o10 W244 ruling 3);
#: the Reader lands it in the same commit as the library change.
WAYPOINT_BASELINE_REL = ".shared/scripts/check_checklist_router_waypoint_baseline.json"

#: o9L1310: Approve & Review writes a request file here (not in git); the owner by script type.
REVIEW_REQUESTS_REL = ".shared/state/review-requests"
REVIEW_OWNERS = {"AT": "o3", "SC": "o5"}


def _ruling_ref():
    """`Reader approve <ISO time -07:00>`: the ruling the command writes into the record."""
    try:
        from zoneinfo import ZoneInfo
        now = datetime.datetime.now(ZoneInfo("America/Los_Angeles"))
    except Exception:
        now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-7)))
    return "Reader approve " + now.isoformat(timespec="seconds")


# Where the corpus is. MASTER-TRAINING-DOC.md Section 7 names it; training_header keeps the
# same list as CORPUS_DIRS. One corpus today - a second is a visible edit, not a widened glob.
PILOTS_REL = "working_corpus_o8/pilots"
# o9:W167 - the single script library. The Reader's Scripts view reads this when the workspace
# has it, and falls back to PILOTS_REL for a workspace that does not.
LIBRARY_REL = "script-library"
# The bucket for scripts with no category yet (o10:W235 section c); listed last in its stage.
UNPLACED_DIR = "not-yet-placed"
MAX_DEPTH = 12
# FILE_ATTRIBUTE_REPARSE_POINT: a symlink, junction or other link; discover resolves these.
_REPARSE_POINT = 0x400

HISTORY_DIR = ".edit-history"
SNAP_EXT = ".snapshot"
NEW_REWRITE_SUFFIX = "-owner-rewrite"

# ⭐ REUSED, NOT RE-WRITTEN. training_header already recognises all five legacy spellings
# (`- the human rewrite`, `- THE HUMAN REWRITE`, `- THE HUMAN Rewrite`, `-the human-rewrite`, `-v2-the human-rewrite`).
# A second regex here would be a second opinion about what a rewrite is, and the two would
# disagree the first time either changed.
REWRITE_SUFFIX = training_header.REWRITE_SUFFIX
PROCESS_LOG_RE = re.compile(r"(?i)process[-_ ]?log")
ID_RE = re.compile(r"^[0-9a-f]{20}$")

KIND_LABEL = {"draft": "agent draft", "rewrite": "the human rewrite", "log": "process log",
              "note": "note"}

def kind_label_for(kind, approve):
    """The label shown beside a script's title. An agent draft that the human approved, or that is final, shows no
    "agent draft" label beside its Approved and Final badges (o9 W204 review, lane o9L1294): "" then, and the page
    shows no label."""
    if kind == "draft" and approve and (approve.get("approved") or approve.get("final")):
        return ""
    return KIND_LABEL[kind]


README_TEXT = """What this folder is
===================

Every file under here is a byte-for-byte copy of one of the human's rewrites, taken by the
OrchDoc Reader IMMEDIATELY BEFORE it saved over that rewrite. One folder per rewrite, one
file per save, named by the UTC time of the save.

MASTER-TRAINING-DOC.md Section 3: every iteration is kept, in full, forever. These are those
iterations. Do not prune, deduplicate or "tidy" them.

They end in .snapshot, not .md, and sit in a hidden folder, ON PURPOSE: training_header.py
counts any file named like a rewrite as a live rewrite, and a snapshot is not one.

RESTORING FROM ONE - DIFF FIRST. A snapshot is an OLDER version. Copying it over the live
rewrite wholesale throws away every edit made since it was taken, and git will report that as
an ordinary modification. Compare it with the live file, then carry across only what you mean
to.
"""


def classify(name):
    """'rewrite' | 'log' | 'draft', from the NAME - which is the only place the convention lives.

    ⚠️ MASTER-TRAINING-DOC.md Section 5: a `THE HUMAN-` PREFIX means the human COMMISSIONED the script,
    not that he wrote it. Only the SUFFIX says he rewrote it, and REWRITE_SUFFIX requires a
    separator in front of `owner`, so `THE HUMAN-A1-...` at the start of a name cannot match it.
    """
    # o10:W235 point 11: the library's new `<NAME> v<N> <AUTHOR>.md` shape is read by the shared
    # parser; a name it cannot read (version None) falls through to the old suffix tests below.
    info = training_header.parse_script_name(name)
    if info.kind == "note":
        return "note"
    if info.version is not None and info.kind in ("draft", "rewrite", "log"):
        return info.kind
    stem = name[:-3] if name.lower().endswith(".md") else name
    if REWRITE_SUFFIX.search(stem):
        return "rewrite"
    if PROCESS_LOG_RE.search(stem):
        return "log"
    return "draft"


def rewrite_parts(stem):
    """(base, tail) for a rewrite's stem, or None when it is not one.

    `SC-05-PILOT-v2-rule18 - THE HUMAN REWRITE`          -> ('SC-05-PILOT-v2-rule18', '')
    `AT-S5-...-PILOT - the human rewrite (working-tree variant)` -> ('AT-S5-...-PILOT',
                                                             '(working-tree variant)')
    A non-empty tail marks a VARIANT - a second rewrite of the same draft - which is never the
    one a draft's save goes to.
    """
    info = training_header.parse_script_name(stem + ".md")
    if info.version is not None:
        # new shape: no tail, and the name key is not a draft's stem, so the old stem pairing
        # finds no draft for it (Phase B pairs by name key and number)
        return (info.name_key, "") if info.kind == "rewrite" else None
    m = REWRITE_SUFFIX.search(stem)
    if not m:
        return None
    return stem[:m.start()].rstrip(" -_"), stem[m.end():].strip()


APP_LIVE_RE = re.compile(r"(?im)^[ \t]*#{1,6}[ \t]*App[ \t]*:[ \t]*live[ \t]*$")
HEADER_READ_BYTES = 8192


def header_says_live(path):
    """True when the file's header (its first 8 KB) has a `## App: live` line (o10:W235)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(HEADER_READ_BYTES).decode("utf-8", "replace")
    except (OSError, TypeError, ValueError):
        return False
    return bool(APP_LIVE_RE.search(head))


LIBRARY_FIELD_RE = re.compile(r"^##\s+([A-Za-z][A-Za-z-]*):[ \t]*(.*?)\s*$")  # file_script KEY_RE
KEEP_FIELDS = {"rewrite-of"}


def strip_library_header(text):
    """`text` without the library header block: the `## Key: value` field lines above
    `<!-- END SCRIPT HEADER -->` and that line itself (o9L1177).

    A new-shape the human text that kept the block would be a second record file of its folder
    (file_script.record_files counts any version-named file holding the END line). Every other
    line above the END line stays where it is: a `# title`, the FM4 provenance line, notes, and
    a `## Rewrite-Of:` line, which the Reader reads for pairing. Text with no END line is
    returned unchanged."""
    text = text or ""
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(nl)
    end_i = next((i for i, ln in enumerate(lines) if training_header.END_HEADER.match(ln)), None)
    if end_i is None:
        return text
    head = [ln for ln in lines[:end_i]
            if not (LIBRARY_FIELD_RE.match(ln)
                    and LIBRARY_FIELD_RE.match(ln).group(1).casefold() not in KEEP_FIELDS)]
    return nl.join(head + lines[end_i + 1:])


def hide_library_header(text):
    """`text` for the reading pane: the `## Key: value` lines above `<!-- END SCRIPT HEADER -->`
    and that line itself are blanked (o9:W174 step 8, o9L1212). They are the record's fields
    (`## Version-Of:`, `## Version:`, `## Author:`, `## Origin:`, `## Source-File:`, `## App:`
    and any other), not words of the script, and the rendered page showed them as headings.
    Each hidden line becomes an empty line, so every line keeps its number for the render's
    `data-src-line`. Nothing below the END line is touched, and a text with no END line (a human
    rewrite saved before o9L1236) is returned unchanged. Only the reading pane uses this: the editor, the diff and
    every save still work on the file's whole text."""
    text = text or ""
    lines = text.split("\n")
    end_i = next((i for i, ln in enumerate(lines)
                  if training_header.END_HEADER.match(ln.rstrip("\r"))), None)
    if end_i is None:
        return text
    out = ["" if LIBRARY_FIELD_RE.match(ln.rstrip("\r")) else ln for ln in lines[:end_i]]
    return "\n".join(out + [""] + lines[end_i + 1:])


def _split_header(text):
    """(lines, nl, first_i, end_i) for a text with a library header block, else None. The block is
    the lines from the first `## Key: value` field line above `<!-- END SCRIPT HEADER -->` to
    that END line. Same recognisers as strip_library_header (o9L1236)."""
    text = text or ""
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(nl)
    end_i = next((i for i, ln in enumerate(lines) if training_header.END_HEADER.match(ln)), None)
    if end_i is None:
        return None
    first_i = next((i for i in range(end_i) if LIBRARY_FIELD_RE.match(lines[i])), None)
    if first_i is None:
        return None
    return lines, nl, first_i, end_i


def set_header_fields(text, **fields):
    """`text` with the given header fields (e.g. Version="2", Author="THE HUMAN") set inside its
    library header block: an existing `## Key:` line is rewritten, a missing one is added just
    above the END line. Text with no header block is returned unchanged (o9L1236)."""
    sp = _split_header(text)
    if sp is None:
        return text
    lines, nl, first_i, end_i = sp
    for key, val in fields.items():
        for i in range(first_i, end_i):
            m = LIBRARY_FIELD_RE.match(lines[i])
            if m and m.group(1).casefold() == key.casefold():
                lines[i] = "## %s: %s" % (m.group(1), val)
                break
        else:
            lines.insert(end_i, "## %s: %s" % (key, val))
            end_i += 1
    return nl.join(lines)


RECORD_ONLY_FIELDS = ("stage", "label", "id")  # file_script._RECORD_HEADER_LINE: what makes a record


def drop_header_fields(text, *keys):
    """`text` without the named `## Key:` lines inside its library header block (the END line
    stays). No header block: unchanged (o9L1236)."""
    sp = _split_header(text)
    if sp is None:
        return text
    lines, nl, first_i, end_i = sp
    keys = {k.casefold() for k in keys}
    keep = [ln for i, ln in enumerate(lines)
            if not (first_i <= i < end_i and LIBRARY_FIELD_RE.match(ln)
                    and LIBRARY_FIELD_RE.match(ln).group(1).casefold() in keys)]
    return nl.join(keep)


def restore_header(text, source_text):
    """`text` with `source_text`'s library header block put back when `text` has none (the
    editor dropped it, or the human deleted it). The block goes after the leading `# title`,
    provenance, comment and blank lines. A text that has a header, or a source with none, is
    returned unchanged (o9L1236)."""
    if _split_header(text) is not None:
        return text
    sp = _split_header(source_text)
    if sp is None:
        return text
    s_lines, _, s_first, s_end = sp
    block = s_lines[s_first:s_end + 1]
    text = text or ""
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(nl)
    # o9L1238: the text kept its END line but lost every field line above it. The source's
    # field lines go back above that END line, so the file never ends up with two END lines.
    end_i = next((j for j, ln in enumerate(lines) if training_header.END_HEADER.match(ln)), None)
    if end_i is not None:
        return nl.join(lines[:end_i] + block[:-1] + lines[end_i:])
    i = 0
    while i < len(lines) and (not lines[i].strip() or re.match(r"^#\s", lines[i])
                              or _PROV_LINE.match(lines[i]) or _SLUG_LINE.match(lines[i])
                              or re.match(r"^\s*<!--.*-->\s*$", lines[i])):
        i += 1
    return nl.join(lines[:i] + block + lines[i:])


# The step-6 header lines (o10:W235 step 6) the Reader reads: which script a file is a version
# of, its number and author, its origin code and the path it had before the rename. Any other
# `## Key:` line (`## Approved-By:`, a field added later) is read into the same dict and ignored.
STEP6_FIELDS = ("version-of", "version", "author", "origin", "source-file")
_SLUG_LINE = re.compile(r"^\s*<!--\s*slug\s*:.*-->\s*$", re.I)
_CHUNK_HEAD = re.compile(r"^#{1,6}\s+Script\s+\d+\b")
_PROV_LINE = re.compile(r"^\s*>\s*Authored\s*:", re.I)


def text_facts(text):
    """({lower-case header key: value}, text_key) for one library text.

    The header is every `## Key: value` line above `<!-- END SCRIPT HEADER -->`. `text_key` is a
    hash of the body as the human reads it: the lines below the END line (or, in a file with no END
    line - a human rewrite, whose field lines `strip_library_header` took off - the lines after
    its `# title`, provenance line and any field lines), without the `<!-- slug: ... -->` line,
    without a leading rendered chunk heading (`## Script 1 [SC-DAY]: <title>`) and without
    leading or trailing `---`, blank lines and trailing spaces. Two files with the same key show
    the human the same words."""
    lines = (text or "").replace("\r\n", "\n").split("\n")
    end_i = next((i for i, ln in enumerate(lines) if training_header.END_HEADER.match(ln)), None)
    fields = {}
    for ln in lines[:end_i if end_i is not None else 0]:
        m = LIBRARY_FIELD_RE.match(ln)
        if m:
            fields.setdefault(m.group(1).casefold(), m.group(2))
    if end_i is not None:
        body = lines[end_i + 1:]
    else:
        body = list(lines)
        i, seen_title = 0, False
        while i < len(body):
            ln = body[i]
            if not ln.strip() or _PROV_LINE.match(ln) or LIBRARY_FIELD_RE.match(ln) \
                    or (not seen_title and re.match(r"^#\s", ln)):
                seen_title = seen_title or bool(re.match(r"^#\s", ln))
                i += 1
                continue
            break
        body = body[i:]
    body = [ln.rstrip() for ln in body if not _SLUG_LINE.match(ln)]
    while body and (not body[0] or body[0].strip() == "---" or _CHUNK_HEAD.match(body[0])):
        body.pop(0)
    while body and (not body[-1] or body[-1].strip() == "---"):
        body.pop()
    key = hashlib.sha256("\n".join(body).encode("utf-8")).hexdigest() if body else None
    if end_i is None:
        # o9:W199: a text with no END line (a human rewrite) can still be marked an older draft;
        # read the mark through the one parser so the Reader sees it
        od = training_header.older_draft(text or "")
        if od:
            fields.setdefault(training_header.OLDER_DRAFT_KEY.casefold(), od)
    return fields, key


def live_refusal(target):
    """Why the Reader must never write `target`, or None (o10:W235 point 7).

    Two refusals, in EVERY stage folder (not only 3-final):
    - the `(in app)` file: its text is the rendered chunk the app imports;
    - a record file of an `App: live` script: any text that is not a human rewrite, in a folder
      (or the folder above a `versions/` folder) where a record file carries `## App: live`.
    A human rewrite (`<NAME> v<N> THE HUMAN.md`, old `-owner-rewrite.md`) of a live script is how the human
    edits it, so it stays writable."""
    tp = Path(str(target))
    if not tp.name:
        return None
    info = training_header.parse_script_name(tp.name)
    if info.in_app:
        return "%s is the text that is in the app; the Reader never writes it" % tp.name
    if classify(tp.name) == "rewrite":
        return None
    if header_says_live(tp):
        return "%s is the record file of a script marked `App: live`" % tp.name
    folders = [tp.parent]
    if tp.parent.name.casefold() == "versions":
        folders.append(tp.parent.parent)
    for d in folders:
        try:
            names = sorted(d.iterdir())
        except OSError:
            continue
        for c in names:
            if not (c.is_file() and c.suffix.lower() == ".md"):
                continue
            # o10:W235 step 5: `<NAME> v<N> THE HUMAN (in app).md` is a rewrite by name but it is the
            # in-app text, so any `(in app)` sibling marks the folder live, whatever its author.
            if training_header.parse_script_name(c.name).in_app \
                    or (classify(c.name) != "rewrite" and header_says_live(c)):
                return ("%s belongs to a script marked `App: live` (%s); the Reader writes "
                        "only a human rewrite of it" % (tp.name, c.name))
    return None


def _parse(name):
    return training_header.parse_script_name(name)


def _human_version_name(name_key, n, source_name):
    """`<NAME>[ <Title-Words>] v<n> THE HUMAN.md`. o9L1248 (o10:W242, review o10L433 F3): once
    training_header names files with their title (`script_file_name(..., title=)` and
    `script_title`), a human rewrite carries the title of the file it rewrites, so the title is
    not dropped from the folder. Before W242 lands, training_header has neither, and the name
    is the untitled `<NAME> v<n> THE HUMAN.md` it always was."""
    make = getattr(training_header, "script_file_name", None)
    title_of = getattr(training_header, "script_title", None)
    if make is not None and title_of is not None:
        return make(name_key, n, "THE HUMAN", title=title_of(source_name or ""))
    return "%s v%d THE HUMAN.md" % (name_key, n)


def next_version_name(name_key, taken_versions, existing_names=(), source_name=""):
    """`<NAME> v<max+1> THE HUMAN.md` for a new the human rewrite (o10:W235 point 7), with the title of
    `source_name` (the file being rewritten) once file names carry titles (o10:W242).

    `taken_versions` are the version numbers of EVERY text of this script (the top-level files
    and the flat `versions/` files are one list); a number is never reused. `existing_names`
    guards a file name that is already there (case-folded, Windows)."""
    n = max([v for v in taken_versions if v is not None] or [0]) + 1
    have = {x.casefold() for x in existing_names}
    while True:
        name = _human_version_name(name_key, n, source_name)
        if name.casefold() not in have:
            return name
        n += 1


REWRITE_OF_RE = re.compile(r"(?im)^[ \t]*#{1,6}[ \t]*Rewrite-Of[ \t]*:[ \t]*v?(\d+)[ \t]*$")


def read_rewrite_of(path):
    """K from a `## Rewrite-Of: v<K>` line in the file's header, or None."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(HEADER_READ_BYTES).decode("utf-8", "replace")
    except (OSError, TypeError, ValueError):
        return None
    m = REWRITE_OF_RE.search(head)
    return int(m.group(1)) if m else None


def pair_by_name(files):
    """Pair a script's new-shape files by NAME KEY and number (o10:W235 point 6).

    `files` are the file dicts of one script (its folder plus its flat `versions/`). A HUMAN
    file pairs with the version its `## Rewrite-Of: v<K>` line names, else with the highest
    non-THE HUMAN version below its own. Sets `pair` on both sides, and on a draft `exact` (the
    rewrites that claim it) and `pair` (when exactly one does). A `(in app)` THE HUMAN file is the
    rendered copy of the plain THE HUMAN file of the same number: it pairs with that file and never
    counts as a second rewrite of the draft. Old-shape files (no version) are left alone."""
    new = [f for f in files if f.get("ver") is not None and f["kind"] in ("draft", "rewrite")]
    keys = {}
    for f in new:
        keys.setdefault(f["nkey"].casefold(), []).append(f)
    for fs in keys.values():
        drafts = [f for f in fs if f["kind"] == "draft"]
        rewrites = [f for f in fs if f["kind"] == "rewrite"]
        for d in drafts:
            d["exact"], d["variants"], d["pair"] = [], [], None
        for r in sorted(rewrites, key=lambda x: (x["ver"], x["in_app"])):
            if r["in_app"]:
                twin = [x for x in rewrites if x["ver"] == r["ver"] and not x["in_app"]]
                if twin:
                    r["pair"] = twin[0]["id"]
                    continue
            k = r.get("rewrite_of")
            cands = [d for d in drafts if d["ver"] == k] if k is not None else []
            if not cands:
                cands = [d for d in drafts if d["ver"] < r["ver"]]
                if cands:
                    top = max(d["ver"] for d in cands)
                    cands = [d for d in cands if d["ver"] == top]
            if not cands:
                r["pair"] = None
                continue
            d = sorted(cands, key=lambda x: (x["in_app"], x["name"].casefold()))[0]
            r["pair"] = d["id"]
            if not r["in_app"]:
                d["exact"].append(r["id"])
        for d in drafts:
            if len(d["exact"]) == 1:
                d["pair"] = d["exact"][0]


def competing_rewrite(anchor_name, target_name, names):
    """The name in `names` (a remote folder listing) that is already a rewrite of the draft
    `anchor_name`, spelled differently from `target_name`; else None (o10:W235 point 8).

    New shape: a HUMAN file of the same name key whose pairing draft (the highest non-THE HUMAN
    version below it) is the anchor. Old shape: the stem-and-suffix test."""
    a = _parse(anchor_name)
    tn = target_name.casefold()
    if a.version is None:
        stem = anchor_name[:-3] if anchor_name.lower().endswith(".md") else anchor_name
        for name in names:
            parts = rewrite_parts(name[:-3]) if name.lower().endswith(".md") else None
            if parts and parts[0].casefold() == stem.casefold() and not parts[1] \
                    and name.casefold() != tn:
                return name
        return None
    infos = [(n, _parse(n)) for n in names]
    draft_vs = {p.version for n, p in infos
                if p.version is not None and p.kind == "draft"
                and p.name_key.casefold() == a.name_key.casefold()}
    draft_vs.add(a.version)
    for n, p in infos:
        if p.version is None or p.kind != "rewrite" or p.in_app \
                or p.name_key.casefold() != a.name_key.casefold() or n.casefold() == tn:
            continue
        below = [v for v in draft_vs if v < p.version]
        if below and max(below) == a.version:
            return n
    return None


def version_clash(target_name, names):
    """The name in `names` (a remote folder listing) that already uses the version number of the
    new-shape `target_name` for the same script, spelled differently; else None (o9L1177).

    N is one sequence per script (o10:W235 section d), so a remote `X v4 o10.md` that this
    checkout has not seen means a new `X v4 THE HUMAN.md` would give two texts the number 4."""
    t = _parse(target_name)
    if t.version is None:
        return None
    tn = target_name.casefold()
    for n in names:
        p = _parse(n)
        if p.version == t.version and p.name_key.casefold() == t.name_key.casefold() \
                and n.casefold() != tn:
            return n
    return None


def order_files(files):
    """The list order and badges for one script's files (o10:W235 points 1 to 4).

    Texts by version, highest first (`versions/` files are in the same list); at one number the
    `(in app)` file first. After the texts: process logs, highest version first; then notes;
    then every name the parser cannot read, in natural order. Sets `badges` on each file:
    `newest` on the top text, `in app`, and `legacy name` on the unreadable ones. A text whose
    header carries `## Older-Draft: <reason>` (o9:W199) sorts below the script's unmarked texts,
    never gets `newest` and gets `older draft`.

    A folder with no versioned name at all (the pilots corpus, PILOTS_REL, which a workspace
    without `script-library/` falls back to) keeps the order it has and gets no badges."""
    if not any(f.get("ver") is not None for f in files):
        for f in files:
            f["badges"] = []
        return list(files)
    texts = [f for f in files if f.get("ver") is not None and f["kind"] in ("draft", "rewrite")]
    logs = [f for f in files if f.get("ver") is not None and f["kind"] == "log"]
    notes = [f for f in files if f["kind"] == "note"]
    rest = [f for f in files if f not in texts and f not in logs and f not in notes]
    # o9:W199: a text marked `## Older-Draft:` sorts below the script's current texts whatever its number
    texts.sort(key=lambda f: ([_natural(f["nkey"])], bool(f.get("older_draft")), -f["ver"],
                              not f["in_app"], f["name"].casefold()))
    logs.sort(key=lambda f: (-f["ver"], f["name"].casefold()))
    notes.sort(key=lambda f: _natural(f["name"]))
    rest.sort(key=lambda f: _natural(f["name"]))
    for f in files:
        f["badges"] = []
    current = next((f for f in texts if not f.get("older_draft")), None)
    if current is not None:
        current["badges"].append("newest")
    for f in texts:
        if f.get("older_draft"):
            f["badges"].append("older draft")
    for f in files:
        if f.get("in_app"):
            f["badges"].append("in app")
    for f in rest:
        f["badges"].append("legacy name")
    return texts + logs + notes + rest


# ⭐ EACH DISTINCT TEXT ONCE (o10:W235 step 8, o9:W174). After the rename, 172 `(in app)` files
# had a `versions/` file with the same text (the approved revision `revise` saved) and 138
# ` duplicate` files repeated another file of their script word for word; the Scripts list
# showed each of them as its own row, so one text was listed two or three times. The files stay
# on disk (o10's ruling); the list shows the text once and names the other copies in a note on
# that row. "The same text" is `text_key`: the body below the header, without the slug comment,
# the rendered chunk heading (`## Script 1 [SC-DAY]: <title>`) and leading or trailing `---`
# (see `text_facts`). Two rules, and nothing wider:
#   1. a `versions/` text with the same text as an `(in app)` file of its script is a note on
#      the `(in app)` row;
#   2. a ` duplicate` file with the same text as another text of its script is a note on that
#      text's row (the one its name repeats when that one is shown).
# A ` duplicate` file whose text is different stays a row of its own: it is a distinct text.
SAME_NOTE = "same text saved as %s"


def same_label(f):
    """How a note names a folded file: `versions/<name>` or `<name>`, without `.md`."""
    stem = f["name"][:-3] if f["name"].lower().endswith(".md") else f["name"]
    return ("versions/" + stem) if (f.get("folder") or "").endswith("/versions") else stem


def same_text_leader(f, files):
    """o9:W197: the file in `files` that holds the text of `f` at the highest precedence (an
    unmarked text before an older draft, the higher version, the `(in app)` file), or None when
    `f` is itself that file or no other text of its script has the same `text_key`. Marking only;
    no row is hidden."""
    if f.get("ver") is None or f["kind"] not in ("draft", "rewrite") or not f.get("text_key"):
        return None
    peers = [g for g in files if g.get("ver") is not None and g["kind"] in ("draft", "rewrite")
             and g.get("text_key") == f["text_key"]
             and (g.get("nkey") or "").casefold() == (f.get("nkey") or "").casefold()]
    if len(peers) < 2:
        return None
    # o9L1271: at one number a ` duplicate` copy after the file it repeats, and a `versions/`
    # file after the script folder's file of the same name, so the pick never depends on the
    # order the walk met them
    peers.sort(key=lambda g: (bool(g.get("older_draft")), -g["ver"], not g.get("in_app"),
                              bool(g.get("dup")), (g.get("folder") or "").endswith("/versions"),
                              g["name"].casefold()))
    return peers[0] if peers[0] is not f else None


def same_text_note(lead):
    """What a row or card carries about its leader: the file name, the label a note uses
    (`versions/<stem>` or `<stem>`) and `file`, that label with the file's `.md`, for the
    hover title."""
    lab = same_label(lead)
    ext = lead["name"][-3:] if lead["name"].lower().endswith(".md") else ""
    return {"name": lead["name"], "label": lab, "file": lab + ext}


def same_text_of(f, files):
    """o9L1271: the `same_text` note the card of `f` shows, the one its row in the Scripts list
    shows. `files` is every file of the listing. The script folder and its `versions/` folder
    are folded on copies (the listing's own dicts are not changed), as `display_folders` does,
    so a file the list folds is never named as the leader. A folded file (no row) is compared
    with the rows of its script."""
    if f.get("ver") is None or f["kind"] not in ("draft", "rewrite") or not f.get("text_key"):
        return None
    fo = f.get("folder") or ""
    base = fo[:-len("/versions")] if fo.endswith("/versions") else fo
    names = {base, base + "/versions"}
    if fo.endswith("/versions") and not any((g.get("folder") or "") == base for g in files):
        names = {fo}  # no script folder: display_folders lists `versions/` on its own
    copies = [dict(g) for g in files if (g.get("folder") or "") in names]
    mine = next((c for c in copies if c["id"] == f["id"]), None)
    if mine is None:
        return None
    shown, _ = fold_same_text(order_files(copies))
    if any(c is mine for c in shown):
        return mine.get("same_text")
    lead = same_text_leader(mine, shown + [mine])
    return same_text_note(lead) if lead is not None else None


def fold_same_text(files):
    """(shown, folded) for one script's ordered files. Sets `same` on every shown file: a list
    of {"id", "label"} for the folded files that repeat it. `folded` files get `same_as`, the id
    of the row that names them. The order of `shown` is the order of `files`."""
    for f in files:
        f["same"] = []
        f.pop("same_as", None)
    texts = [f for f in files if f.get("ver") is not None and f["kind"] in ("draft", "rewrite")
             and f.get("text_key")]
    groups = {}
    for f in texts:
        groups.setdefault((f["nkey"].casefold(), f["text_key"]), []).append(f)
    folded = {}
    for fs in groups.values():
        if len(fs) < 2:
            continue
        apps = [f for f in fs if f.get("in_app")]
        if apps:
            for f in fs:
                if not f.get("in_app") and (f.get("folder") or "").endswith("/versions"):
                    folded[id(f)] = (f, apps[0])
        left = [f for f in fs if id(f) not in folded]
        keep = [f for f in left if not f.get("dup")]
        for f in left:
            # o9L1212: an `(in app)` file is never folded, even when its name also says
            # ` duplicate` (`v2 o5 duplicate (in app).md` is a legal name): it would be folded
            # into `apps[0]`, which can be itself, and the app's row would vanish.
            if not f.get("dup") or f.get("in_app") or not keep:
                continue
            base = f["name"].replace(" duplicate", "", 1).casefold()
            to = (apps[0] if apps else None) or next(
                (k for k in keep if k["name"].casefold() == base), None) or keep[0]
            if to.get("older_draft") and not f.get("older_draft"):
                # o9:W199: a current text is never folded into a row marked older draft
                continue
            folded[id(f)] = (f, to)
    shown = [f for f in files if id(f) not in folded]
    for f in files:
        if id(f) in folded:
            g, to = folded[id(f)]
            g["same_as"] = to["id"]
            to["same"].append({"id": g["id"], "label": same_label(g),
                               "relroot": g.get("relroot")})
    for f in files:
        f.pop("same_text", None)
    for f in shown:
        lead = same_text_leader(f, shown)
        if lead is not None:
            f["same_text"] = same_text_note(lead)
    gone = [f for f in files if id(f) in folded]
    if any("newest" in (f.get("badges") or []) for f in gone):
        # the newest text was folded into a row below it: that row is the newest shown
        for f in gone:
            f["badges"].remove("newest")
        top = next((f for f in shown if f.get("ver") is not None
                    and f["kind"] in ("draft", "rewrite") and not f.get("older_draft")), None)
        if top is not None and "newest" not in top["badges"]:
            top["badges"].insert(0, "newest")
    return shown, gone


def display_folders(folders):
    """The folders as the Scripts view lists them: a script's flat `versions/` folder is folded
    into the script's own folder, one list (o10:W235 point 1). Returns new folder dicts; the
    input is not changed. Files are ordered by `order_files`, and a text another file repeats
    is listed once (`fold_same_text`): `files` are the rows, `folded` the repeats, each named in
    a `same` note on its row."""
    by_name = {fo["name"]: fo for fo in folders}
    out, merged = [], set()
    for fo in folders:
        m = re.match(r"^(.*)/versions$", fo["name"])
        if m and m.group(1) in by_name:
            merged.add(fo["name"])
    for fo in folders:
        if fo["name"] in merged:
            continue
        files = list(fo["files"]) + list((by_name.get(fo["name"] + "/versions") or {}).get(
            "files", []))
        shown, folded = fold_same_text(order_files(files))
        out.append({"name": fo["name"], "files": shown, "folded": folded})
    return out


# ==========================================================================================
# "0 - New Drafts" (o9L1285, W205). A filter over the library, not a folder: nothing moves.
# A script is listed while its newest text was written by an agent (not the human) and carries no
# `## Approved-By: the human` line. The human saving a rewrite (a newer `... THE HUMAN.md`) or approving it
# takes it off the list, so the list is derived from the version files and cannot fall out
# of step with them. Scripts under 3-final (approved, in the app) and 4-retired never appear.
# ==========================================================================================
NEW_DRAFTS_NAME = "0 - New Drafts"
#: Only texts written on or after this day are listed: the library holds hundreds of agent
#: drafts the human never opened from before the bakeoffs. None lists them all. o9 chooses the day.
NEW_DRAFTS_SINCE = "2026-10-10"  # o9 (W205): 2026-10-08 is a bulk refile date
_NEW_DRAFTS_SKIP = ("3-final", "4-retired")
_AUTHORED_RE = re.compile(r"(?m)^>\s*Authored\s*:\s*(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}:\d{2}))?")
_nd_cache = {}


def _nd_facts(f):
    """(title, written 'YYYY-MM-DD HH:MM', approved) from the head of one file, cached on
    its mtime and size. The written stamp falls back to the file's mtime."""
    p = f["path"]
    stamp = (f.get("mtime"), f.get("size"))
    hit = _nd_cache.get(str(p))
    if hit and hit[0] == stamp:
        return hit[1]
    try:
        with open(p, "rb") as fh:
            head = fh.read(16384).decode("utf-8", "replace")
    except OSError:
        head = ""
    m = _AUTHORED_RE.search(head)
    if m:
        when = "%s %s" % (m.group(1), m.group(2) or "00:00")
    else:
        when = datetime.datetime.fromtimestamp(f.get("mtime") or 0).strftime("%Y-%m-%d %H:%M")
    t = re.search(r"(?m)^#\s+(.+?)\s*$", head)
    got = ((t.group(1) if t else f["stem"]), when, training_header.is_final(head))  # o9 W204: the one "final" test
    _nd_cache[str(p)] = (stamp, got)
    return got


def new_drafts(folders, since=NEW_DRAFTS_SINCE):
    """The script texts the "0 - New Drafts" filter lists, newest first. `folders` is a
    `discover()` folder list. `since` ('YYYY-MM-DD') drops texts written before that day.
    Each item: {"file", "title", "when", "folder"} where `file` is the newest text's row."""
    groups = {}
    for fo in folders:
        for f in fo["files"]:
            if f.get("ver") is None or f["kind"] not in ("draft", "rewrite") \
                    or f.get("older_draft"):
                continue
            groups.setdefault((f.get("group") or fo["name"], f.get("nkey")), []).append(f)
    out = []
    for (gname, _k), texts in groups.items():
        if gname.split("/")[0].casefold() in _NEW_DRAFTS_SKIP:
            continue
        top = max(texts, key=lambda f: (f["ver"], bool(f.get("in_app")), f["name"].casefold()))
        if top["kind"] == "rewrite" or str(top.get("author") or "").casefold() == "the human":
            continue
        title, when, approved = _nd_facts(top)
        # the Reader's provenance (header line, else first commit) beats the file's own head,
        # whose fallback is the checkout's mtime
        pa = ((top.get("prov") or {}).get("authored") or {})
        if pa.get("value") and pa.get("src") in ("header", "git"):
            when = str(pa["value"])[:16]
        if approved or (since and when[:10] < since):
            continue
        out.append({"file": top, "title": title, "when": when, "folder": gname})
    out.sort(key=lambda d: (d["when"], d["folder"]), reverse=True)
    return out


def migrate_renamed(old_path, new_path):
    """Move a renamed file's `.edit-history/<old stem>/`, `<old stem>.why-notes.json` and that notes
    file's `.edit-history/<old stem>.why-notes/` to the
    new stem (o10:W235 point 12). o10's rename step calls this in the same commit as the rename.

    All three sit beside the file's folder. Nothing is overwritten: if the destination
    already exists the source is left and named in the result. Returns a list of
    (kind, old, new, "moved" | "absent" | "exists") tuples."""
    old, new = Path(old_path), Path(new_path)
    jobs = [("edit-history", old.parent / HISTORY_DIR / old.stem,
             new.parent / HISTORY_DIR / new.stem),
            ("why-notes", old.parent / (old.stem + ".why-notes.json"),
             new.parent / (new.stem + ".why-notes.json")),
            # o9L1241: the notes file's own snapshots (an overwritten notes file is kept under
            # `.edit-history/<stem>.why-notes/`), same folder name file_script uses (o10 W238).
            ("why-notes-history", old.parent / HISTORY_DIR / (old.stem + ".why-notes"),
             new.parent / HISTORY_DIR / (new.stem + ".why-notes"))]
    out = []
    for kind, src, dst in jobs:
        if not src.exists():
            out.append((kind, str(src), str(dst), "absent"))
        elif dst.exists():
            out.append((kind, str(src), str(dst), "exists"))
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.replace(str(src), str(dst))
            out.append((kind, str(src), str(dst), "moved"))
    return out


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def norm_text(data):
    """Bytes -> text with line endings folded. Line endings are how git checks a file out on
    this machine (core.autocrlf), not content, so two copies differing only there are equal."""
    if isinstance(data, bytes):
        data = data.decode("utf-8-sig", errors="replace")
    return data.replace("\r\n", "\n").replace("\r", "\n")


def _text_hash(data):
    """sha256 of norm_text(data): two copies that differ only in line endings hash the same."""
    return hashlib.sha256(norm_text(data).encode("utf-8")).hexdigest()


def encode_like(text, template):
    """Encode `text` the way `template` (bytes, or None) is encoded: CRLF or LF, BOM or not.

    ⛔ WITHOUT THIS EVERY SAVE REWRITES EVERY LINE ENDING. The corpus is checked out CRLF and a
    browser textarea hands back LF, so a one-word edit would arrive as a whole-file diff - which
    buries the delta the training loop reads under a change nobody made.
    """
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    if template and b"\r\n" in template and template.count(b"\r\n") * 2 >= template.count(b"\n"):
        t = t.replace("\n", "\r\n")
    data = t.encode("utf-8")
    if template and template.startswith(b"\xef\xbb\xbf"):
        data = b"\xef\xbb\xbf" + data
    return data


def decode_strict(data):
    """(text, None) or (None, why). A file that is not valid UTF-8 is not editable here: the
    textarea would round-trip it through replacement characters and corrupt it on save."""
    try:
        return data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n"), None
    except UnicodeDecodeError as ex:
        return None, "not valid UTF-8 at byte %d - editing it here could corrupt it" % ex.start


def _utc_stamp():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _natural(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


# ==========================================================================================
# PROVENANCE - the date a script was authored, and the model that wrote it
#
# the human, 2026-09-23, verbatim: *"I also need to be able to see the date that the script was
# authored... That is a fast indicator of how likely it is that I'm working with the correct
# copy. If available, I would also like to be able to know what Claude model was used to write
# a script. That provenance may not be known for a lot of the older scripts, which is fine - it
# can simply be listed as 'unknown'."*
#
# AUTHORED - the first of these that answers, and the page says which one did. It is the date
# AND the time (`2026-10-01 13:59`, US Pacific, the human 2026-10-01) wherever the time is known: the
# header line when it records one, git's commit time, a file's modification time. A header line
# written before 2026-10-01 records only a date, and the page then shows only the date.
#   1. the FM4 provenance line, `> Authored: YYYY-MM-DD HH:MM | Model: <id> | By: <lane>`, the
#      first line under the `# title` (SCRIPT-CHECKLIST-5-FORMAT-AND-GATE.md, o8, a99b33de). ⛔
#      PARSED BY training_header.parse_provenance, the one definition of the format - never
#      here. A human rewrite that still carries the AGENT's line (copied over with the draft's
#      text) is not dated by it: only `Rewrite By: the human` (or the older `Model: none | By: the human`)
#      is his.
#   2. the date git first saw the file - its EARLIEST add, following renames (the corpus was
#      moved into per-script folders, so without --follow every date would be the move).
#      ⛔ origin/main FIRST. The checkout the live reader serves is hundreds of commits behind,
#      so its HEAD would answer with stale history, or not at all. HEAD only when origin/main
#      does not have the file at that path.
#   3. a file git has never seen: its modification time, labelled "not committed yet".
#   4. otherwise "unknown" - shown as unknown, never as a blank.
# MODEL: the header line's Model field; else the prose the corpus already writes ("Authored on
# **Opus 5**"), shown as written; else "unknown".
# ⭐ A HUMAN REWRITE SHOWS THE ORIGINAL'S MODEL (the human, 2026-10-01 14:24: "Even in the scripts
# rewrites, the original model should still be indicated"), never "the human" and never "none". The
# first of these that answers: (1) the model its own `Rewrite By: the human` line carries, which the
# Reader's save copied in from the original; (2) the original draft it pairs with, by its header
# line, else its prose; (3) the AGENT's line a rewrite still carries from being copied; (4) the
# rewrite's own prose, which is the draft's copied over; else "unknown". Who wrote the rewrite is
# shown as "Rewrite By: the human", the label its own line uses. The original's authored date is NOT
# carried over: the header is one line (o9, W66 fix round 1).
# LAST CHANGED: the latest commit touching it on the ref it was found on - or, when the file on
# disk differs from that ref, its modification time, because the commit date would be false.
#
# ⭐ CACHED, BECAUSE THE SCRIPTS PAGE LISTS EVERY FILE. The git answers are keyed on the ref's
# sha and the per-file answers on the file's mtime and size. When origin/main moves - it moves
# every time any session pushes - ONE `git diff` between the old and new sha says which corpus
# paths changed, and only those are asked again. Without that, every push would re-run the
# whole corpus's history on the next page load.
# ==========================================================================================

_MODEL_NAME = r"(?:Claude[\s-]+)?(?:Opus|Sonnet|Haiku|Fable)[\s-]*\d+(?:[.-]\d+)*"
# `Authored on **Opus 5**`, `authored on Opus 5`, `Authored by **Opus 5**`,
# `Authoring model: **Opus 5.5**`. The name is captured AS WRITTEN.
_PROSE_MODEL = re.compile(r"(?:authored\s+(?:on|by|with|using)|authoring\s+model\s*:)\s*"
                          r"(?:\*\*|__)?\s*(" + _MODEL_NAME + r")", re.I)
GIT_TIMEOUT = 60


def header_fields(text):
    """The FM4 line's fields, ready to show: {"authored": ISO date, "time": "HH:MM", "model",
    "by", "rewrite": True}, each only when it carries a value.

    `time` is there only when the line records one (Pacific; lines written before 2026-10-01
    record a date alone). `rewrite` is there when the line says `Rewrite By:`.
    `Model: unknown` (FM4's backfill value) is no value, so the prose fallback still gets its
    turn. `Model: none` is KEPT - it is how a human rewrite said so before 2026-10-01."""
    p = training_header.parse_provenance(text) or {}
    out = {}
    got = training_header.provenance_stamp(p.get("authored"))
    if got:
        out["authored"] = got[0]
        if got[1]:
            out["time"] = got[1]
    for k in ("model", "by"):
        v = (p.get(k) or "").strip()[:80]
        if v and not (k == "model" and v.lower() == "unknown"):
            out[k] = v
    if p.get("rewrite"):
        out["rewrite"] = True
    return out


def prose_model(text):
    """The model named in the corpus's own prose ("Authored on **Opus 5**"), as written."""
    m = _PROSE_MODEL.search(norm_text(text or ""))
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else None


def carried_model(text):
    """The model the FM4 line of `text` names as the ORIGINAL script's, or None.

    Not `unknown`, not `none`, and not the Model slot of a human line written before 2026-10-01
    (`Model: none | By: the human`, or a hand-written `none (text), ... | By: the human (text), ...`):
    those say nothing about the original. A placeholder in angle brackets is not a model."""
    p = training_header.parse_provenance(text) or {}
    model = (p.get("model") or "").strip()
    low = model.lower()
    if not model or low == "unknown" or low.startswith(training_header.PROV_REWRITE_MODEL):
        return None
    if not p.get("rewrite") and (p.get("by") or "").strip().lower().startswith(
            training_header.PROV_REWRITE_BY.lower()):
        return None
    if "<" in model or ">" in model:
        return None
    return model[:80]


def _blob_ids(data):
    """git blob ids this content could be stored as: as-is, LF-folded, BOM-stripped. The
    corpus is checked out CRLF (core.autocrlf) and stored LF, so a byte compare alone would
    call every file changed."""
    out = set()
    variants = [data]
    t = data[3:] if data.startswith(b"\xef\xbb\xbf") else data
    variants += [t, t.replace(b"\r\n", b"\n")]
    for v in variants:
        out.add(hashlib.sha1(b"blob %d\0" % len(v) + v).hexdigest())
    return out


def _local_date(ts):
    try:
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return None


def _local_time(ts):
    try:
        return datetime.datetime.fromtimestamp(ts).strftime("%H:%M")
    except (OverflowError, OSError, ValueError):
        return None


def _iso_key(s):
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _local_stamp(iso):
    """(date, HH:MM) of a git ISO date in THIS machine's zone, as an mtime is read. Taking the
    date from the commit's own offset and the time from another zone could put a time after
    the wrong day. An unreadable date keeps its first ten characters and has no time."""
    t = _iso_key(iso)
    if t is None:
        return iso[:10], None
    t = t.astimezone()
    return t.strftime("%Y-%m-%d"), t.strftime("%H:%M")


# ⭐ AUTHORED IS SHOWN IN US PACIFIC TIME, `YYYY-MM-DD HH:MM` (the human, 2026-10-01: *"Authored:
# 2026-10-01 13:59"*), whatever zone this machine or a commit was in. LAST CHANGED stays in
# this machine's local time, as it has been since it got its time earlier that day.

def _pacific_iso(iso):
    """`YYYY-MM-DD HH:MM` in US Pacific for a git ISO date. A date git gave in a form that
    cannot be read keeps its first ten characters and no time - none is made up."""
    t = _iso_key(iso)
    if t is None:
        return (iso or "")[:10] or None
    return training_header.pacific_stamp(t)


def _pacific_ts(ts):
    """`YYYY-MM-DD HH:MM` in US Pacific for a file's modification time, or None."""
    try:
        return training_header.pacific_stamp(
            datetime.datetime.fromtimestamp(ts, datetime.timezone.utc))
    except (OverflowError, OSError, ValueError):
        return None


def _pt(value):
    """A hover suffix naming the zone, for a value that carries a time of day."""
    return " - time is US Pacific" if value and len(value) > 10 else ""


def _field(value, src, label, title, time=None):
    """`time` (local HH:MM) is set only on Last changed - the human, 2026-10-01: *"the "last
    changed" needs a time stamp after the date stamp"*. An AUTHORED value carries its own time
    inside `value` (`2026-10-01 13:59`), because the page prints `value` and nothing beside it."""
    return {"value": value, "src": src, "label": label, "title": title, "time": time}


def provenance_cache_path(workspace):
    """Where the saved provenance lives: beside reader.log, as `reader_log_path` chooses it
    (`<workspace>/.shared/state/reader-log/` in a workspace that has `.shared/scripts`, else
    this computer's `~/.claude/state/reader-log/<tag>/`), so a folder that is not a full
    workspace gains no `.shared` folder from the Scripts page."""
    ws = Path(workspace)
    if (ws / ".shared" / "scripts").is_dir():
        return ws / ".shared" / "state" / "reader-log" / "scripts-provenance.json"
    import shared_state
    tag = hashlib.sha1(str(ws.resolve()).lower().encode("utf-8")).hexdigest()[:12]
    return shared_state.home_state_dir() / "reader-log" / tag / "scripts-provenance.json"


class Provenance:
    """Authored date, model and last change for corpus files. Owned by a `Corpus`."""

    def __init__(self, workspace, remote_ref="refs/remotes/origin/main", workers=8,
                 cache_path=None):
        self.workspace = Path(workspace).resolve()
        # ⭐ SAVED TO DISK (o9L1219). `_hist` is the expensive part (a cold library took 14-30 s
        # of `git log --follow` steps); a restarted Reader reloads it from `cache_path` and
        # asks git only about files a newer commit changed (`_history_cached`). None = no file.
        self.cache_path = Path(cache_path) if cache_path else None
        self._disk_loaded = False
        self._disk_bytes = None
        self.remote_ref = remote_ref
        self.workers = workers
        # ⭐ COUNTED BY SUBCOMMAND so a test can prove a second page load ran no `log`.
        self.calls = {}
        self._lock = threading.Lock()
        self._top = None            # the repository's top level, or False when there is none
        self._trees = {}            # sha -> {top-relative path: blob id}
        self._hist = {}             # rel -> (sha, {"added", "changed"} or None)
        self._moved = {}            # (old sha, new sha) -> set of changed rels, or None
        self._content = {}          # abs path -> ((mtime_ns, size), facts)
        self.batch_asked = 0        # single commits the last `histories` asked --follow about
        self.batch_whole = 0        # files the last `histories` asked --follow about whole
        # One annotation at a time: the caches above are shared by the server's threads.
        self._run = threading.Lock()

    # ---- git ---------------------------------------------------------------------------

    def _git(self, *args, cwd=None):
        """(returncode, stdout bytes). Never raises: no git, or a git that fails, is an
        answer the resolution order already has a step for."""
        with self._lock:
            self.calls[args[0]] = self.calls.get(args[0], 0) + 1
        # ⛔ LITERAL PATHSPECS. A file name is data; `:` or `*` in one must not become pathspec
        # magic that matches some other file's history.
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_LITERAL_PATHSPECS="1")
        try:
            p = subprocess.run(["git", "-C", str(cwd or self._top or self.workspace)]
                               + list(args), capture_output=True, timeout=GIT_TIMEOUT, env=env,
                               creationflags=NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            return 1, b""
        return p.returncode, p.stdout or b""

    def _toplevel(self):
        if self._top is None:
            rc, out = self._git("rev-parse", "--show-toplevel", cwd=self.workspace)
            top = out.decode("utf-8", "replace").strip() if rc == 0 else ""
            self._top = Path(top).resolve() if top else False
        return self._top

    def _rel(self, p):
        top = self._toplevel()
        if not top:
            return None
        a = os.path.realpath(str(p))
        # `top` is already resolved (`_toplevel`); resolving it again for every file was
        # half of this call's cost on a Scripts page load (o9L1467).
        t = str(top)
        if not os.path.normcase(a).startswith(os.path.normcase(t).rstrip("\\/") + os.sep):
            return None
        return os.path.relpath(a, t).replace(os.sep, "/")

    def _tips(self):
        """{"origin": sha or None, "head": sha or None} - one process when both exist."""
        rc, out = self._git("rev-parse", self.remote_ref + "^{commit}", "HEAD^{commit}")
        lines = out.decode("ascii", "replace").split()
        if rc == 0 and len(lines) == 2:
            return {"origin": lines[0], "head": lines[1]}
        tips = {}
        for name, ref in (("origin", self.remote_ref), ("head", "HEAD")):
            rc, out = self._git("rev-parse", "--verify", "-q", ref + "^{commit}")
            tips[name] = out.decode("ascii", "replace").strip() if rc == 0 else None
        return tips

    def _tree(self, sha, under):
        key = (sha, under)
        if key not in self._trees:
            rc, out = self._git("ls-tree", "-r", "-z", "--full-tree", sha, "--", under)
            tree = {}
            if rc == 0:
                for rec in out.split(b"\0"):
                    meta, _, path = rec.partition(b"\t")
                    parts = meta.split()
                    if len(parts) == 3 and parts[1] == b"blob":
                        tree[path.decode("utf-8", "replace")] = parts[2].decode("ascii")
            if len(self._trees) > 8:
                self._trees.clear()
            self._trees[key] = tree
        return self._trees[key]

    def _changed_between(self, old, new, under):
        """The corpus paths whose history can differ between `old` and `new`, or None when
        that cannot be known cheaply (`new` does not descend from `old`)."""
        key = (old, new, under)
        if key not in self._moved:
            rc, _ = self._git("merge-base", "--is-ancestor", old, new)
            if rc != 0:
                self._moved[key] = None
            else:
                rc, out = self._git("diff", "--name-only", "-z", "--no-renames", old, new,
                                    "--", under)
                self._moved[key] = (None if rc else
                                    {x.decode("utf-8", "replace") for x in out.split(b"\0")
                                     if x})
            if len(self._moved) > 64:
                self._moved = {key: self._moved[key]}
        return self._moved[key]

    def history(self, sha, rel):
        """{"added": ISO date of the EARLIEST add following renames, "changed": ISO date of the
        latest commit touching it} on `sha`, or None. One `git log --follow` per file."""
        commits = self._follow_log(sha, rel)
        if not commits:
            return None
        added = self._earliest_add(commits)
        if added is None:
            added = self.added_through_merges(sha, rel)
        return {"added": added, "changed": commits[0][0]}

    def _follow_log(self, sha, rel, *extra):
        """[[ISO date, status letters]] of `git log --follow`, newest first; None on failure."""
        rc, out = self._git("log", sha, "--follow", *extra, "--name-status",
                            "--format=%x01%aI", "--", rel)
        if rc != 0:
            return None
        commits = []
        for line in out.decode("utf-8", "replace").splitlines():
            if line.startswith("\x01"):
                commits.append([line[1:].strip(), ""])
            elif commits and line.strip():
                commits[-1][1] += line[:1]
        return commits

    # ⭐ A TRAIL THAT ENDS WITHOUT AN ADD WENT THROUGH A MERGE (o9L1191, 2026-10-09). `git log
    # --follow` does not diff merge commits, so a file whose path was made inside a merge - the
    # o10 script move resolved its renames in merge 7d171263 - has a followed history of
    # renames only, no add, and its Authored chip read "unknown" (SC-Sl-D-10-014 v1 and v2 o5,
    # SC-Sl-D-14-014 v1 and v2 o5 and v3 agent, all without a `> Authored:` line). For those
    # files ALONE the walk is asked again diffing each merge against its first parent, which
    # carries the trail back to the add (SC-Sl-D-14-014 v1 o5: 2026-07-08, in
    # working_corpus_o5). Only `added` comes from it; every other file is asked as before, so
    # its dates are unchanged.
    # ⛔ AN ADD THAT FIRST-PARENT PUTS IN A MERGE CAME FROM THE MERGED BRANCH (o9L1195). Diffed
    # against its first parent, a merge shows every file the other branch brought in as added
    # (or copied) AT THE MERGE: SC-Sl-D-14-014 v3 agent.md read 2026-10-09T15:43:07, merge
    # 98fc5244, a C099 copy of the script's pre-rename record file, but it was added as
    # before-revise.md in 355790fb at 15:38:08 on the merge's second parent. When the earliest add is inside a merge, the file is
    # looked up on the merge's other parents - under its name there, or the name the merge
    # renamed it from - and the add found on that side is used. Only when no other parent had
    # the file was it really made in the merge, and the merge's date stands.
    MERGE_DEPTH = 4         # merges of merges followed this deep, then the merge date stands

    def added_through_merges(self, sha, rel, _depth=0):
        trail = self._follow_trail(sha, rel, "--diff-merges=first-parent")
        if not trail:
            return None
        pick = None
        for rec in trail:
            if ("A" in rec["st"] or "C" in rec["st"]) and _iso_key(rec["date"]):
                if pick is None or _iso_key(rec["date"]) < _iso_key(pick["date"]):
                    pick = rec
            if "C" in rec["st"]:
                break
        if pick is None:
            return None
        if len(pick["parents"]) > 1 and _depth < self.MERGE_DEPTH:
            found = [d for p in pick["parents"][1:]
                     for d in [self._added_on_side(pick["commit"], p, pick["name"], _depth)]
                     if d]
            if found:
                return min(found, key=_iso_key)
        return pick["date"]

    def _added_on_side(self, merge, parent, name, depth):
        """The add date of the file `name` of `merge` as the merge's parent `parent` had it, or
        None when that parent did not have it."""
        rc, _ = self._git("cat-file", "-e", "%s:%s" % (parent, name))
        src = name if rc == 0 else None
        if src is None:
            rc, out = self._git("diff", "-M", "-l0", "--name-status", "-z", parent, merge)
            toks = [t.lstrip(b"\n").decode("utf-8", "replace") for t in out.split(b"\0")]
            i = 0
            while rc == 0 and i < len(toks):
                st = toks[i][:1]
                if st in ("R", "C") and i + 2 < len(toks):
                    if toks[i + 2] == name:
                        src = toks[i + 1]
                        break
                    i += 3
                else:
                    i += 2
        if src is None:
            return None
        commits = self._follow_log(parent, src)
        added = self._earliest_add(commits) if commits else None
        if added is None and commits:
            added = self.added_through_merges(parent, src, depth + 1)
        return added

    def _follow_trail(self, sha, rel, *extra):
        """[{"commit", "parents", "date", "st", "name"}] of `git log --follow`, newest first,
        where "name" is the file's path in that commit; None on failure."""
        rc, out = self._git("log", sha, "--follow", *extra, "--name-status", "-z",
                            "--format=%x01%H %P %aI", "--", rel)
        if rc != 0:
            return None
        trail = []
        toks = [t.lstrip(b"\n") for t in out.split(b"\0")]
        i, n = 0, len(toks)
        while i < n:
            t = toks[i]
            if t.startswith(b"\x01"):
                parts = t[1:].decode("ascii", "replace").split()
                if len(parts) >= 2:
                    trail.append({"commit": parts[0], "parents": parts[1:-1],
                                  "date": parts[-1], "st": "", "name": rel})
                i += 1
                continue
            if not t or not trail:
                i += 1
                continue
            st = t[:1].decode("ascii", "replace")
            if st in ("R", "C"):
                name = toks[i + 2] if i + 2 < n else b""
                i += 3
            else:
                name = toks[i + 1] if i + 1 < n else b""
                i += 2
            trail[-1]["st"] += st
            trail[-1]["name"] = name.decode("utf-8", "replace")
        return trail

    @staticmethod
    def _earliest_add(commits):
        # ⛔ STOP AT A COPY. `--follow` detects copies as well as renames (git runs it with
        # find-copies-harder), so a rewrite created from its draft - status C - is followed
        # back into the DRAFT's history, and the earliest add would be the draft's. Measured on
        # the real corpus: SC-05-PILOT-v4-the human-rewrite.md showed its draft's 09-03 instead of
        # its own 09-04. A rename (R) is the same file moved and is followed; a copy is where
        # this file began.
        adds = []
        for d, st in commits:
            if ("A" in st or "C" in st) and _iso_key(d):
                adds.append(d)
            if "C" in st:
                break
        return min(adds, key=_iso_key) if adds else None

    # ⭐ ONE `git log` FOR THE WHOLE CORPUS ON A COLD LOAD (o9L1191). One `git log --follow`
    # per file walked all ~5,800 commits once per file: on 2026-10-09 the 1,191 files of the
    # library took 33-88 s on a cold Scripts page, against 0.6 s for ONE `git log --raw -M`
    # over the whole history, from which every file's chain of renames is read back.
    # ⛔ THE BATCH IS NOT TRUSTED WHERE IT CAN DISAGREE WITH --follow. A whole-commit rename
    # pass pairs each deleted file with ONE added file; `--follow` asks for the best source of
    # its one file alone (and also finds copies of files the commit left untouched). The two
    # differ only where the commit holds ANOTHER file with the same content as this one - a
    # DRAFT and its identical snapshot moved in the same commit (24 of 1,191 files, measured)
    # - or where the file was added with no rename the batch could see. Those files are asked
    # with `--follow`, exactly as before, so every date on the page is the one it was.
    BATCH_MIN = 12          # fewer files than this (a push moved a few): --follow each

    def histories(self, sha, rels):
        """{rel: history(sha, rel)} for many files, with one batched `git log` plus a
        `--follow` for each file the batch cannot answer the same way. None when the batch
        log itself fails (the caller then asks file by file)."""
        rc, raw = self._git("log", sha, "--raw", "-M", "-z", "--no-abbrev",
                            "--format=%x01%H %aI")
        if rc != 0:
            return None
        commits = []            # [(sha, date, [(status, src, dst, old blob, new blob)])]
        toks = raw.split(b"\0")
        del raw
        i, n = 0, len(toks)
        while i < n:
            t = toks[i].lstrip(b"\n")
            if t.startswith(b"\x01"):
                h, _, d = t[1:].decode("ascii", "replace").strip().partition(" ")
                commits.append((h, d.strip(), []))
                i += 1
                continue
            if not t.startswith(b":") or not commits:
                i += 1
                continue
            meta = t.decode("ascii", "replace").split()
            if len(meta) < 5:
                i += 1
                continue
            st = meta[4][:1]
            if st in "RC":
                src = toks[i + 1].decode("utf-8", "replace") if i + 1 < n else ""
                dst = toks[i + 2].decode("utf-8", "replace") if i + 2 < n else ""
                i += 3
            else:
                src, dst = None, (toks[i + 1].decode("utf-8", "replace") if i + 1 < n else "")
                i += 2
            commits[-1][2].append((st, src, dst, meta[2], meta[3]))
        by_dst = {}
        for ci, (_h, _d, ents) in enumerate(commits):
            for e in ents:
                if e[0] != "D":
                    by_dst.setdefault(e[2], []).append((ci, e))
        zero = "0" * 40
        state = {rel: [rel, 0, []] for rel in rels}     # rel -> [current name, from, seen]
        steps = {}                      # (commit index, name) -> --follow's (status, source)
        whole = []                      # files --follow is asked about whole
        result = {}
        self.batch_asked = 0

        def walk(rel):
            """Advance one file's chain; "done", or the (commit, name) --follow must settle."""
            st8 = state[rel]
            while True:
                cur, pos, seen = st8
                nxt = None
                for ci, e in by_dst.get(cur, ()):
                    if ci < pos:
                        continue
                    st, src = e[0], e[1]
                    older = any(cj > ci for cj, _e in by_dst[cur])
                    if st in "ARC" and not self._batch_sure(commits[ci][2], e, zero, older):
                        got = steps.get((ci, cur), False)
                        if got is False:
                            return (ci, cur)
                        if got is None:
                            return "whole"
                        st, src = got
                    seen.append((commits[ci][1], st))
                    if st == "R":
                        nxt = (src, ci + 1)
                        break
                    if st == "C":
                        return "done"
                    if st == "A" and e[0] != "A":
                        # --follow found no source where the batch did: the name goes on
                        # being followed in older commits, as an add does.
                        nxt = (cur, ci + 1)
                        break
                if nxt is None:
                    return "done"
                st8[0], st8[1] = nxt

        pending = list(rels)
        import concurrent.futures as cf
        while pending:
            ask = {}
            for rel in pending:
                r = walk(rel)
                if r == "whole":
                    whole.append(rel)
                elif r != "done":
                    ask.setdefault(r, []).append(rel)
            if not ask:
                break
            keys = list(ask)
            self.batch_asked += len(keys)
            # Each step is one short git process (~0.1-0.3 s); measured 4.8 s for 156 steps
            # with 8 at a time, 3.6 s with 16.
            with cf.ThreadPoolExecutor(max_workers=self._step_workers()) as ex:
                got = list(ex.map(lambda k: self._follow_step(commits[k[0]][0], k[1]), keys))
            steps.update(zip(keys, got))
            pending = [rel for k in keys for rel in ask[k]]
        for rel in rels:
            if rel in whole:
                continue
            seen = state[rel][2]
            if not seen:
                result[rel] = None
                continue
            adds = [d for d, st in seen if st in "AC" and _iso_key(d)]
            result[rel] = {"added": min(adds, key=_iso_key) if adds else None,
                           "changed": seen[0][0]}
        bare = [rel for rel, h in result.items() if h and h["added"] is None]
        with cf.ThreadPoolExecutor(max_workers=self._step_workers()) as ex:
            via = list(ex.map(lambda r: self.added_through_merges(sha, r), bare))
            result.update(zip(whole, ex.map(lambda r: self.history(sha, r), whole)))
        for rel, added in zip(bare, via):
            result[rel] = dict(result[rel], added=added)
        self.batch_whole = len(whole)
        return result

    def _step_workers(self):
        return max(1, self.workers, min(16, os.cpu_count() or 1))

    def _follow_step(self, commit, name):
        """(status, source) that `--follow` gives `name` in `commit` alone - the same rename
        detection a whole `git log --follow` runs when its walk reaches that commit, at the
        cost of one commit instead of the whole history. None when it says nothing usable."""
        rc, out = self._git("log", commit, "-1", "--follow", "--name-status", "-z",
                            "--format=%x01", "--", name)
        if rc != 0:
            return None
        toks = [t.lstrip(b"\n") for t in out.split(b"\0")]
        toks = [t for t in toks if t and t != b"\x01"]
        if not toks:
            return None
        st = toks[0][:1].decode("ascii", "replace")
        if st in ("R", "C") and len(toks) >= 3:
            if toks[2].decode("utf-8", "replace") != name:
                return None
            return st, toks[1].decode("utf-8", "replace")
        if st in ("A", "M") and len(toks) >= 2:
            return st, None
        return None

    @staticmethod
    def _batch_sure(ents, e, zero, older=False):
        """True when `--follow` would make the same choice as the batch for the file created
        by entry `e` of a commit whose entries are `ents`: an exact rename (or copy) whose
        content no OTHER source in the commit also has, or a plain add in a commit that
        removed nothing and moved nothing, of a name with no `older` life. An inexact rename
        is never sure: the batch gave each source to one destination, `--follow` gives this
        one its own best match.
        ⛔ A NAME THAT LIVED BEFORE (o9L1195). `--follow` also finds copies of files the commit
        left untouched, which the batch's `-M` cannot see: a name deleted and later re-created
        as a copy of an unchanged file is a C to `--follow`, which stops there, while the batch
        saw a plain A and walked on into the name's old life, giving its older date. A plain
        add is sure only when the name has no earlier entry; otherwise `--follow` settles it."""
        st, _src, _dst, old, new = e
        if st == "A" and older:
            return False
        if new == zero:
            return False
        if st in "RC":
            if old != new:
                return False
            srcs = {o[1] if o[0] in "RC" else o[2] for o in ents
                    if o[0] in "RCDM" and o[3] == new}
            return len(srcs) == 1
        return not any(o[0] in "RD" or (o[0] == "C" and o[3] == new) for o in ents)

    def _history_cached(self, sha, rel, under):
        got = self._hist.get(rel)
        if got is not None:
            if got[0] == sha:
                return got[1], False
            moved = self._changed_between(got[0], sha, under)
            if moved is not None and rel not in moved:
                self._hist[rel] = (sha, got[1])
                return got[1], False
        return None, True

    # ---- the file itself ---------------------------------------------------------------

    def _facts(self, path):
        """What the file's own bytes say, cached on its mtime and size."""
        try:
            st = path.stat()
        except OSError:
            return None
        key = (st.st_mtime_ns, st.st_size)
        got = self._content.get(str(path))
        if got and got[0] == key:
            return got[1]
        try:
            data = path.read_bytes()
        except OSError:
            return None
        text = data.decode("utf-8-sig", errors="replace")
        facts = {"header": header_fields(text), "prose_model": prose_model(text),
                 "blobs": _blob_ids(data), "mtime": st.st_mtime}
        self._content[str(path)] = (key, facts)
        return facts

    # ---- the answer --------------------------------------------------------------------

    def annotate(self, files, by_id, root):
        """Set f["prov"] on every draft and rewrite in `files`. Process logs are skipped - they
        are an agent's record of a run, not a script. `root` is the corpus folder; git is only
        asked about paths under it. `by_id` is the listing's files by id: a rewrite is shown with
        its ORIGINAL's model, and the original is the draft it is paired with."""
        files = [f for f in files if f.get("kind") in ("draft", "rewrite")]
        if not files:
            return
        with self._run:
            self._annotate(files, by_id or {}, root)

    DISK_VERSION = 1

    def _load_disk(self, under):
        """Reload `_hist` from `cache_path` once per process. ⛔ Never trusted blindly: every
        entry carries the sha it was computed on, and `_history_cached` keeps it only when
        that sha is an ancestor of the current one and `git diff` says the file did not
        change between them. A missing, unreadable, other-version or other-folder file is
        simply no cache."""
        if self._disk_loaded:
            return
        self._disk_loaded = True
        if self.cache_path is None:
            return
        try:
            raw = self.cache_path.read_bytes()
            doc = json.loads(raw.decode("utf-8"))
            if (doc.get("v") != self.DISK_VERSION or doc.get("under") != under
                    or doc.get("workspace") != str(self.workspace)
                    or doc.get("ref") != self.remote_ref):
                return
            for rel, ent in (doc.get("entries") or {}).items():
                sha, hist = ent
                if (isinstance(rel, str) and isinstance(sha, str) and len(sha) == 40
                        and (hist is None or (isinstance(hist, dict)
                                              and set(hist) <= {"added", "changed"}
                                              and all(v is None or isinstance(v, str)
                                                      for v in hist.values())))):
                    self._hist.setdefault(rel, (sha, hist))
            self._disk_bytes = raw
        except (OSError, ValueError, TypeError, AttributeError):
            return

    def _save_disk(self, under, tips):
        """Write `_hist` atomically when it differs from what is on disk. Entries for files
        in neither tip's tree are dropped, so deleted scripts do not pile up. A failure is
        ignored: the cache is a speed-up, never a requirement."""
        if self.cache_path is None:
            return
        try:
            live = set()
            for name in ("origin", "head"):
                if tips.get(name):
                    live |= set(self._tree(tips[name], under))
            entries = {rel: [sha, hist] for rel, (sha, hist) in sorted(self._hist.items())
                       if rel in live}
            data = json.dumps({"v": self.DISK_VERSION, "workspace": str(self.workspace),
                               "ref": self.remote_ref, "under": under, "entries": entries},
                              sort_keys=True, separators=(",", ":")).encode("utf-8")
            if data == self._disk_bytes:
                return
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            _write_atomic(self.cache_path, data)
            self._disk_bytes = data
        except (OSError, ValueError, TypeError):
            pass

    def _annotate(self, files, by_id, root):
        top = self._toplevel()
        tips = self._tips() if top else {"origin": None, "head": None}
        under = (self._rel(root) or ".") if top else "."
        if top:
            self._load_disk(under)
        # ⭐ A REWRITE IS SHOWN WITH ITS ORIGINAL'S MODEL, so its original is read too - including
        # when only ONE rewrite is annotated (its read view), whose original is then not in
        # `files`. ⛔ Such an original is read for its MODEL ALONE, which its header or prose
        # gives without git: asking git for its history as well would double the cold cost of
        # opening a rewrite (a `git log --follow` is ~1s on the real history). Its history is
        # asked when it is needed - by a save, which annotates it as a listed file.
        origs = {}
        for f in files:
            o = by_id.get(f.get("pair")) if f["kind"] == "rewrite" and f.get("pair") else None
            if o is not None and o.get("kind") == "draft":
                origs[f["id"]] = o
        listed = {f["id"] for f in files}
        extra = {}
        for o in origs.values():
            if o["id"] not in listed:
                extra[o["id"]] = o
        todo, plan = [], {}
        for f in files + list(extra.values()):
            rel = self._rel(f["path"]) if top else None
            where = None
            if rel is not None and f["id"] not in extra:
                for name in ("origin", "head"):
                    sha = tips.get(name)
                    if sha and rel in self._tree(sha, under):
                        where = (name, sha)
                        break
            plan[f["id"]] = (rel, where)
            if where:
                hist, miss = self._history_cached(where[1], rel, under)
                if miss:
                    todo.append((rel, where[1]))
        reader = None
        # ⛔ JOINED EVEN WHEN GIT RAISES: the facts thread alone touches `_content` until
        # it is joined, so an exception must not leave it running beside the next caller.
        try:
            if len(todo) >= self.BATCH_MIN:
                # ⭐ A COLD LOAD: one batched log per ref (see `histories`). Files it does not
                # answer, or a batch that fails, fall through to `--follow` below. Every file's
                # own bytes (`_facts`, ~1 s for the library) are read meanwhile, in one thread
                # that alone touches `_content` until it is joined.
                reader = threading.Thread(
                    target=lambda: [self._facts(f["path"]) for f in files + list(extra.values())],
                    name="scripts-facts", daemon=True)
                reader.start()
                by_sha = {}
                for rel, sha in todo:
                    by_sha.setdefault(sha, []).append(rel)
                for sha, rels in by_sha.items():
                    got = self.histories(sha, rels)
                    for rel, res in (got or {}).items():
                        self._hist[rel] = (sha, res)
                todo = [(rel, sha) for rel, sha in todo
                        if self._hist.get(rel, (None,))[0] != sha]
            if todo:
                # ⭐ IN PARALLEL on a first load: each `git log --follow` is ~0.15s, and a cold
                # Scripts page would otherwise wait for all of them one after another.
                import concurrent.futures as cf
                with cf.ThreadPoolExecutor(max_workers=max(1, self.workers)) as ex:
                    results = list(ex.map(lambda t: self.history(t[1], t[0]), todo))
                for (rel, sha), res in zip(todo, results):
                    self._hist[rel] = (sha, res)
        finally:
            if reader is not None:
                reader.join()
        if top:
            self._save_disk(under, tips)
        for f in files:
            rel, where = plan[f["id"]]
            hist = self._hist.get(rel, (None, None))[1] if where else None
            orig = None
            o = origs.get(f["id"])
            if o is not None:
                orel, owhere = plan[o["id"]]
                ohist = self._hist.get(orel, (None, None))[1] if owhere else None
                orig = self._assemble(o, self._facts(o["path"]), orel, owhere, ohist, top)
                orig["name"] = o["name"]
            f["prov"] = self._assemble(f, self._facts(f["path"]), rel, where, hist, top, orig)

    def _assemble(self, f, facts, rel, where, hist, top, orig=None):
        """`orig` is the assembled provenance of the draft a rewrite is paired with (plus its
        "name"), or None. Only a rewrite reads it."""
        facts = facts or {"header": {}, "prose_model": None, "blobs": set(),
                          "mtime": f.get("mtime") or 0}
        header = dict(facts["header"])
        is_rewrite = f["kind"] == "rewrite"
        # A line is THE HUMAN'S when it says so: `Rewrite By: the human`, or the form used before
        # 2026-10-01, `Model: none | By: the human`.
        is_human = bool(header.get("rewrite")
                     or header.get("model", "").lower() == training_header.PROV_REWRITE_MODEL
                     or header.get("by", "").lower() == training_header.PROV_REWRITE_BY.lower())
        inherited = False
        agent_model = None
        # ⛔ A REWRITE STARTS AS A COPY OF ITS DRAFT, HEADER LINE AND ALL. Read as the rewrite's
        # own, the agent's line would date the human's rewrite to the day the agent wrote the draft.
        # Only a line that says it is the human's is his. The agent's MODEL is still kept, as a last
        # resort for the rewrite's model: it is the original's, copied over.
        if is_rewrite and header and not is_human:
            agent_model = header.get("model")
            if (agent_model or "").lower().startswith(training_header.PROV_REWRITE_MODEL):
                agent_model = None              # `none (text), ...` is a hand-written line
            header, inherited = {}, True
        if not is_rewrite and is_human:
            header.pop("model", None)           # `none` on an agent's file names no model
        refname = ("origin/main" if where and where[0] == "origin"
                   else "this checkout's HEAD (the file is not at this path on origin/main)")
        mdate = _local_date(facts["mtime"])
        mstamp = _pacific_ts(facts["mtime"])

        also = ("; its provenance line is the AGENT's, copied over from the draft, so it is not "
                "used" if inherited else "")
        if header.get("authored"):
            val = header["authored"] + (" " + header["time"] if header.get("time") else "")
            authored = _field(val, "header", "from the header line",
                              "from the file's own provenance line (> Authored: ...)%s"
                              % _pt(val))
        elif where and hist and hist.get("added"):
            val = _pacific_iso(hist["added"])
            authored = _field(val, "git", "first committed",
                              "first committed - from git, %s, following renames%s%s"
                              % (refname, also, _pt(val)))
        elif top and rel is not None and not where:
            authored = _field(mstamp, "mtime", "not committed yet",
                              "not committed yet - the file's modification time on disk%s"
                              % _pt(mstamp))
        elif not top:
            authored = _field(mstamp, "mtime", "no git history",
                              "the file's modification time - this folder is not in a git "
                              "repository, so there is no commit to date it by%s" % _pt(mstamp))
        else:
            authored = _field(None, "unknown", "unknown",
                              "no header line, and git history did not give a date")
        if authored["value"] is None:
            authored = _field(None, "unknown", "unknown", authored["title"])

        if is_rewrite:
            # ⭐ THE ORIGINAL'S MODEL, never "the human" and never "none" (the human, 2026-10-01 14:24).
            own = header.get("model")
            om = (orig or {}).get("model") or {}
            # `none` in ANY form names no model - `none (text)` too, as `carried_model` reads it,
            # so the bar shows the model a save of this file would carry (o9 W66 review, o9L973).
            if own and not own.lower().startswith(training_header.PROV_REWRITE_MODEL):
                model = _field(own, "header", "from the header line",
                               "the model that wrote the ORIGINAL script, copied into this "
                               "rewrite's own provenance line (| Model: ...)")
            elif om.get("value"):
                model = _field(om["value"], "original", "from the original",
                               "the model that wrote the original script, %s (%s)"
                               % (orig.get("name") or "the draft this rewrites",
                                  om.get("label") or "its own record"))
            elif agent_model:
                model = _field(agent_model, "header", "from the copied header line",
                               "from the AGENT's provenance line this rewrite was copied with "
                               "(| Model: ...) - the original's model")
            elif facts["prose_model"]:
                model = _field(facts["prose_model"], "prose", "from the header prose",
                               "from the file's own prose - its 'Authored on/by <model>' "
                               "sentence, copied over from the draft")
            else:
                model = _field(None, "unknown", "unknown",
                               "neither this rewrite nor its original names a model - no header "
                               "line, no 'Authored on' prose")
        elif header.get("model"):
            model = _field(header["model"], "header", "from the header line",
                           "from the file's own provenance line (| Model: ...)")
        elif facts["prose_model"]:
            model = _field(facts["prose_model"], "prose", "from the header prose",
                           "from the file's own prose - its 'Authored on/by <model>' sentence")
        else:
            model = _field(None, "unknown", "unknown",
                           "the file names no model - no header line, no 'Authored on' prose")

        mtime = _local_time(facts["mtime"])
        if where and hist and hist.get("changed"):
            blob = self._blob_at(where, rel)
            if blob and blob in facts["blobs"]:
                cday, ctime = _local_stamp(hist["changed"])
                changed = _field(cday, "git", "last commit",
                                 "the latest commit touching it on %s" % refname, ctime)
            else:
                changed = _field(mdate, "mtime", "changed on disk",
                                 "the file on disk differs from %s - its modification time"
                                 % refname, mtime)
        elif mdate:
            changed = _field(mdate, "mtime", "modified",
                             "the file's modification time on disk", mtime)
        else:
            changed = _field(None, "unknown", "unknown", "no commit and no modification time")

        # A rewrite is the human's, whatever its line says: `by` is `the human` and `rewrite` is set. The
        # bar (drawn by render_orchdoc_reader.py, not here) labels it `Rewrite By:` for a
        # rewrite and `By:` otherwise, the same two labels the file's own line uses.
        return {"authored": authored, "model": model, "changed": changed,
                "by": training_header.PROV_REWRITE_BY if is_rewrite else header.get("by"),
                "rewrite": is_rewrite,
                "header_inherited": inherited}

    def _blob_at(self, where, rel):
        for (sha, _under), tree in self._trees.items():
            if sha == where[1] and rel in tree:
                return tree[rel]
        return None


# ==========================================================================================
# A STABLE READER LINK FOR A SCRIPT FILE (o9L1243). The human, 2026-10-11: *"Create a hook that
# forces the agent adding a script to the script library to give a clickable link to the
# OrchDoc Reader."* The opaque id (`#scripts/<id>`) cannot be that link: it is an HMAC under a
# key minted per server run (see Corpus.__init__), so it dies on every restart and no other
# process can compute it. That key stays as it is - it is what stops a tab left open across a
# restart from saving into a file it named before.
#
# ⛔ THE BROWSER STILL NEVER SENDS A PATH. `#scripts/p/<path under the corpus root>` is read by
# the page only: it looks the path up among the files the server already listed in the Scripts
# rail (each row carries `data-srel`, its path under the root) and opens that row's id, exactly
# as a click on it does. Discovery built that list through `confine`, so nothing outside the
# corpus is on it, and a path with an empty, `.` or `..` segment is refused before the lookup.
# The server routes are unchanged: they still take an id this run issued and nothing else.
# ==========================================================================================

READER_BASE = "http://127.0.0.1:8787/"
PATH_ROUTE = "#scripts/p/"


def link_for_rel(rel_to_root):
    """The Reader URL of the file at `rel_to_root` (posix, under the corpus root)."""
    return READER_BASE + PATH_ROUTE + "/".join(
        urllib.parse.quote(s, safe="") for s in rel_to_root.split("/"))


def rename_key(name):
    """(name_key, rest) of a library file name, read by training_header's parser, or None for a
    name outside the library shape. `rest` is the name from ` v<N>` (or ` notes - `) to the
    end: version, author and the ` duplicate`, log and ` (in app)` endings. o9L1248 (review
    o10L433 F4): the page reads these off each Scripts row (`data-snk`, `data-srest`), so a
    `#scripts/p/<path>` link whose file was since renamed only in its title words (o10:W242)
    still finds it: same folder, a link name of `<name_key>[ <one title token>]<rest>`. The
    page compares strings only; the parsing is done here."""
    base = os.path.basename(str(name).replace("\\", "/"))
    m = training_header.SCRIPT_NAME.match(base)
    if m:
        return m.group("name"), base[m.start("ver") - 2:]
    m = training_header.SCRIPT_NOTE.match(base)
    if m:
        i = base.find(" notes - ", m.end("name"))
        if i >= 0:
            return m.group("name"), base[i:]
    return None


def _library_parts(path):
    """(parts before the last `script-library` segment, parts after it) of `path`, any slash
    style, or None when it has no such segment."""
    parts = [s for s in re.split(r"[\\/]+", str(path)) if s != ""]
    for i in range(len(parts) - 1, -1, -1):
        if parts[i].casefold() == LIBRARY_REL.casefold():
            return parts[:i], parts[i + 1:]
    return None


def _newest_text(folder, kinds):
    """The folder's best text by name alone, in file_script.record_files' order (the `(in app)`
    file, then the highest version), for a folder with no record file - a draft in
    `1-drafts/not-yet-placed/` carries no `## Stage:` line, so record_files skips it. Names are
    read by training_header.parse_script_name; ` duplicate` copies and logs are left out."""
    best = None
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return None
    for n in names:
        if not n.lower().endswith(".md"):
            continue
        info = training_header.parse_script_name(n)
        m = training_header.SCRIPT_NAME.match(n)
        if info.version is None or info.kind not in kinds or (m and m.group("dup")):
            continue
        if not (Path(folder) / n).is_file():
            continue
        rank = (not info.in_app, -info.version, n)
        if best is None or rank < best[0]:
            best = (rank, Path(folder) / n)
    return best[1] if best else None


def locate(path, workspace=None):
    """(rel_to_root, None) for the file the Reader shows for `path`, else (None, why).

    `path` may be absolute (`C:\\...\\script-library\\...`, either slash), repo-relative
    (`script-library/...`) or relative to the working directory. It must name, under a
    `script-library` folder, either a `.md` file the Scripts view lists or a script folder; a
    folder resolves to its record file (file_script.record_file: the `(in app)` file, else the
    highest version). Only what EXISTS counts: a path-shaped string is not a file. The path is
    looked for under `workspace`'s library first (what the Reader serves), then as written.
    """
    workspace = Path(workspace) if workspace else Path(__file__).resolve().parents[2]
    raw = str(path).strip().strip("`\"'<>")
    split = _library_parts(raw)
    if split is None:
        cand = Path(os.path.abspath(raw)) if raw else None
        split = _library_parts(cand) if cand and cand.exists() else None
        if split is None:
            return None, "not a path under script-library/"
        raw = str(cand)
    _before, rel = split
    if not rel:
        return None, "that is the library itself, not a script in it"
    if any(s in (".", "..") for s in rel):
        return None, "a path with a . or .. segment is refused"
    roots = [workspace / LIBRARY_REL]
    if os.path.isabs(raw):                      # a worktree's copy, named by its own path
        last = list(re.finditer(r"(?i)(?:^|[\\/])script-library(?=[\\/]|$)", raw))
        if last:
            roots.append(Path(raw[:last[-1].end()]))
    for root in roots:
        try:
            rroot = Path(os.path.realpath(root))
            p = Path(os.path.realpath(root.joinpath(*rel)))
        except (OSError, ValueError):
            continue
        if not p.exists() or not p.is_relative_to(rroot) or p == rroot:
            continue
        if p.is_dir():
            import file_script                  # lazy: only a folder needs it
            rec = file_script.record_file(p) or _newest_text(p, file_script.RECORD_KINDS)
            if rec is None:
                return None, ("that folder holds no script text (no record file, and no "
                              "`<NAME> v<N> <AUTHOR>.md` draft or rewrite)")
            p = Path(os.path.realpath(rec))
        if p.suffix.lower() != ".md":
            return None, "the Reader lists only .md files"
        parts = p.relative_to(rroot).parts
        if any(s.startswith((".", "_")) for s in parts):
            return None, "the Reader skips hidden and underscore folders"
        if len(parts) > MAX_DEPTH + 1:
            return None, "deeper than the Reader looks (MAX_DEPTH)"
        return "/".join(parts), None
    return None, "no such file or folder in the script library"


def reader_link(path, workspace=None):
    """(url, None) - the stable Reader link for a script file or folder - or (None, why)."""
    rel, why = locate(path, workspace)
    return (link_for_rel(rel), None) if rel else (None, why)


class Corpus:
    """The script corpus under one workspace. Discovery is re-run on every call - these files
    are written by other sessions while the human reads them, so a snapshot of the listing taken at
    startup would go stale exactly the way a cached OrchDoc model would."""

    def __init__(self, workspace, key=None, rel=None):
        self.workspace = Path(workspace).resolve()
        if rel is None:
            rel = LIBRARY_REL if (self.workspace / LIBRARY_REL).is_dir() else PILOTS_REL
        self.rel = rel
        self.root = self.workspace / rel
        # ⛔ MINTED PER RUN, like the write token. Ids from a previous server run do not
        # resolve, so a tab left open across a restart cannot save into a file it named
        # before the corpus could have changed under it.
        self.key = key if key is not None else secrets.token_bytes(16)
        self._lock = threading.Lock()
        self._approve_lock = threading.Lock()
        self.approve_cmd = None          # a test points this at a fake approve command
        # o9L1300: after a successful Approve the "Finished Scripts" tab is rebuilt in the
        # background. A test sets `sheet_launcher` to a fake: callable(argv) -> (exit code, last
        # error line). `sheet_async=False` runs the refresh loop inline so a test can wait.
        self.sheet_launcher = None
        self.sheet_async = True
        self._sheet_state = threading.Lock()
        self._sheet_running = False
        self._sheet_pending = False
        # Authored date / model / last change, cached across page loads (see PROVENANCE).
        self.prov = Provenance(self.workspace, cache_path=provenance_cache_path(self.workspace))
        # abs path -> ((mtime_ns, size), (header fields, text_key)); see text_facts
        self._facts_cache = {}

    def _facts(self, p, st):
        """`text_facts` of the file at `p`, cached on its mtime and size: the Scripts view lists
        every file on every load, and a file is read again only when it changed."""
        k, stamp = str(p), (st.st_mtime_ns, st.st_size)
        hit = self._facts_cache.get(k)
        if hit and hit[0] == stamp:
            return hit[1]
        try:
            with open(p, "rb") as fh:
                got = text_facts(fh.read().decode("utf-8", "replace"))
        except OSError:
            got = ({}, None)
        self._facts_cache[k] = (stamp, got)
        return got

    # ---- identity ----------------------------------------------------------------------

    def sid(self, rel_to_root):
        return hmac.new(self.key, rel_to_root.encode("utf-8"), hashlib.sha256).hexdigest()[:20]

    def _root_resolved(self):
        try:
            return self.root.resolve()
        except OSError:
            return None

    def confine(self, p):
        """p, resolved, if it lies inside the corpus root - else None.

        ⛔ RESOLVE FIRST, THEN CHECK. `.resolve()` follows symlinks, so a link inside the corpus
        pointing out of it lands outside and fails here. Checking the unresolved path would
        pass it - the same order `resolve_asset` uses for images, for the same reason."""
        root = self._root_resolved()
        if root is None:
            return None
        try:
            rp = Path(p).resolve()
        except (OSError, ValueError):
            return None
        return rp if rp.is_relative_to(root) and rp != root else None

    # ---- discovery ---------------------------------------------------------------------

    def discover(self):
        """{"exists", "root_rel", "folders": [...], "by_id": {id: file}}.

        Any depth (o9:W167), `.md` only. Each folder that holds `.md` files is one group, named
        by its path under the root ("3-final/sleep/day/04-x/AT-Sl-D-04-001-slug"). Hidden and
        underscore folders are skipped (`.edit-history`, `__pycache__`, the library's `_build`),
        and so is anything that resolves outside the root. A loose `.md` at the top of the corpus
        is listed under "(top level)" rather than dropped.
        """
        out = {"exists": False, "root_rel": self.rel, "folders": [], "by_id": {}}
        root = self._root_resolved()
        if root is None or not root.is_dir():
            return out
        out["exists"] = True
        groups = []
        loose = []
        seen = set()
        stats = {}

        # ⭐ ONE DIRECTORY LISTING PER FOLDER, NO PER-FILE RESOLVE (o9L1467). This walk ran on
        # every Scripts page load and did ~11,000 `realpath` calls plus two `stat`s per entry:
        # each opens a handle on Windows, ~3 s of a ~5 s page in a fresh process and far more in
        # the live Reader (o9 W248 follow-up, 2026-10-22). `os.scandir` gives the type, size and
        # mtime from the listing itself, and the walk starts at the RESOLVED root, so an entry
        # that is not a reparse point (symlink, junction, ...) is already its own resolved path.
        # ⛔ A reparse point still goes through `confine`, which resolves it and refuses one
        # that lands outside the root - the check is skipped only where it cannot fail.
        def plain(entry):
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                return False
            if entry.is_symlink() or getattr(st, "st_file_attributes", 0) & _REPARSE_POINT:
                return False
            return True

        def walk(d, depth):
            try:
                with os.scandir(d) as it:
                    entries = sorted(it, key=lambda e: _natural(e.name))
            except OSError:
                return
            files = []
            for entry in entries:
                if entry.name.startswith((".", "_")):
                    continue
                child = d / entry.name
                fast = plain(entry)
                try:
                    isdir = entry.is_dir()
                    isfile = entry.is_file()
                except OSError:
                    continue
                if isdir:
                    rc = child if fast else self.confine(child)
                    if rc is None or rc in seen or depth >= MAX_DEPTH:
                        continue
                    seen.add(rc)
                    walk(child, depth + 1)
                elif isfile and child.suffix.lower() == ".md" \
                        and (fast or self.confine(child) is not None):
                    if fast:
                        try:
                            stats[child] = entry.stat()
                        except OSError:
                            pass
                    files.append(child)
            if files:
                if d == root:
                    loose.extend(files)
                else:
                    groups.append((d.relative_to(root).as_posix(), files))

        walk(root, 0)
        # o10:W235 point 5: the `not-yet-placed` bucket goes last in each stage folder, after
        # every line; a plain natural sort put it before `sleep`.
        groups.sort(key=lambda g: [(x.casefold() == UNPLACED_DIR, _natural(x))
                                   for x in g[0].split("/")])
        if loose:
            groups.append(("(top level)", loose))

        for gname, files in groups:
            folder = self._model_folder(gname, sorted(files, key=lambda p: _natural(p.name)),
                                        root, stats)
            out["folders"].append(folder)
            for f in folder["files"]:
                out["by_id"][f["id"]] = f
        # o10:W235 points 1 and 6: a script's flat `versions/` folder is one list with the
        # script folder, and new-shape files pair by name key and number inside it.
        by_name = {fo["name"]: fo for fo in out["folders"]}
        for fo in out["folders"]:
            m = re.match(r"^(.*)/versions$", fo["name"])
            if m and m.group(1) in by_name:
                continue
            group = list(fo["files"]) + list((by_name.get(fo["name"] + "/versions") or {})
                                             .get("files", []))
            for f in group:
                f["group"] = fo["name"]
            pair_by_name(group)
        for fo in out["folders"]:
            for f in fo["files"]:
                f.setdefault("group", fo["name"])
        return out

    def _model_folder(self, gname, paths, root, stats=None):
        files = []
        for p in paths:
            rel_root = p.relative_to(root).as_posix()
            stem = p.name[:-3]
            kind = classify(p.name)
            parts = rewrite_parts(stem) if kind == "rewrite" else None
            info = training_header.parse_script_name(p.name)
            try:
                # the listing's own stat when discover has it (o9L1467), else one call
                st = (stats or {}).get(p) or p.stat()
                size, mtime = st.st_size, st.st_mtime
            except OSError:
                st, size, mtime = None, 0, 0
            # o9:W174 step 8: a library text's number and author come from its step-6 header
            # lines (`## Version:`, `## Author:`), the name when it has none (a human rewrite
            # saved before o9L1236, when its header was taken off on save); `text_key` finds the same text saved
            # twice (fold_same_text).
            hdr, text_key = ({}, None)
            if info.version is not None and kind in ("draft", "rewrite") and st is not None:
                hdr, text_key = self._facts(p, st)
            hver = hdr.get("version", "").strip()
            hver = int(hver) if re.fullmatch(r"[1-9]\d*", hver) else None
            m_dup = training_header.SCRIPT_NAME.match(p.name)
            files.append({
                "id": self.sid(rel_root),
                "name": p.name,
                "stem": stem,
                "kind": kind,
                "folder": gname,
                "rel": (Path(self.rel) / rel_root).as_posix(),
                # o9L1243: the path under the root, which a `#scripts/p/` link names
                "relroot": rel_root,
                "path": p,                          # server-side only; never serialised
                "base": parts[0] if parts else None,
                "tail": parts[1] if parts else None,
                "size": size,
                "mtime": mtime,
                "pair": None,
                "variants": [],
                # o10:W235: what the shared parser read from the name (ver None = old shape)
                "nkey": info.name_key,
                "ver": hver if hver is not None else info.version,
                "author": hdr.get("author", "").strip() or info.author,
                "in_app": bool(info.in_app),
                "dup": bool(m_dup and m_dup.group("dup")),
                "version_of": hdr.get("version-of", "").strip() or None,
                "text_key": text_key,
                # o9:W199: the reason of a `## Older-Draft:` line (None when unmarked); the page shows it
                "older_draft": training_header.older_draft(hdr),
                "rewrite_of": (read_rewrite_of(p) if info.version is not None
                               and kind == "rewrite" else None),
                "badges": [],
            })
        drafts = [f for f in files if f["kind"] == "draft"]
        rewrites = [f for f in files if f["kind"] == "rewrite"]
        for d in drafts:
            key = d["stem"].casefold()
            exact = [r for r in rewrites if r["base"].casefold() == key and not r["tail"]]
            d["variants"] = [r["id"] for r in rewrites
                             if r["base"].casefold() == key and r["tail"]]
            d["exact"] = [r["id"] for r in exact]
            if len(exact) == 1:
                d["pair"] = exact[0]["id"]
        by_stem = {d["stem"].casefold(): d for d in drafts}
        for r in rewrites:
            d = by_stem.get(r["base"].casefold())
            if d is not None:
                r["pair"] = d["id"]
        return {"name": gname, "files": files}

    @staticmethod
    def public(f):
        """A file as the page may see it: everything but the absolute path."""
        return {k: v for k, v in f.items() if k not in ("path",)}

    def annotate_provenance(self, listing):
        """Add f["prov"] - authored date, model, last change, each with where it came from - to
        every draft and rewrite in a `discover()` listing. Returns the listing."""
        self.prov.annotate(list(listing["by_id"].values()), listing["by_id"], self.root)
        return listing

    def resolve(self, sid, listing=None):
        """(file, None) or (None, why). ⛔ THE ONLY WAY A CLIENT NAMES A SCRIPT."""
        if not isinstance(sid, str) or not ID_RE.match(sid):
            return None, ("that is not a script id this reader issues - the page names a "
                          "script by the id it was given, never by a path")
        listing = listing or self.discover()
        f = listing["by_id"].get(sid)
        if f is None:
            return None, ("no script with that id is in the corpus right now - it was never "
                          "issued, it was issued by an earlier run of the reader, or the file "
                          "has gone. Reload the Scripts list.")
        # ⛔ CONFINED AGAIN, after the map. The map was built from a directory walk that already
        # refused anything outside the root; checking again costs one stat and means the
        # guarantee does not rest on how discovery happened to be written.
        if self.confine(f["path"]) is None:
            return None, "that script resolves outside the corpus - refused"
        return f, None

    # ---- what a save would do ----------------------------------------------------------

    def warm(self):
        """Fill the provenance cache in a background thread and return the thread.

        o9:W167: on the real library (about 620 files) the first Scripts view after a restart
        runs one `git log --follow` per file, about 45 s; once cached a view takes about 1 s.
        The server calls this after it binds, so the human's first click does not pay for it. A
        failure is ignored: the view then computes it on demand, as it did before."""
        def run():
            try:
                self.annotate_provenance(self.discover())
            except Exception:
                pass
        t = threading.Thread(target=run, name="scripts-warm", daemon=True)
        t.start()
        return t

    @staticmethod
    def _beside(target, opened):
        """True when `target` is in the folder of `opened`, or - for a text in the script's flat
        `versions/` folder - in the script folder above it, where its `v<N> THE HUMAN.md` goes.
        o9:W174 step 8: the pre-rename `<script>/versions/<TAG>/` layout (o9L1145) is gone from
        the library, and a target two folders up from a file is refused again."""
        tp, op = target.resolve().parent, opened.resolve().parent
        return tp == op or (op.name.casefold() == "versions" and tp == op.parent)

    def plan(self, f, listing):
        """Where a save of `f` goes: `_plan`, wrapped with the `App: live` guard.

        o9L1177 (o9's ruling, after o10's `## App: live` header landed at 17c77fb9): the old
        3-final path test (o9:W167) is gone. `live_refusal` refuses the `(in app)` file and every
        record file of a live script in every stage folder, which covers what it covered."""
        p = self._plan(f, listing)
        if p.get("editable") and p.get("target_path") is not None \
                and not self._beside(Path(str(p["target_path"])), f["path"]):
            # the same beside test save() makes, so a plan never offers a target save() refuses
            return {"editable": False, "mode": None, "target_path": None, "target_name": None,
                    "why": "Refused: the save target does not sit beside the file you opened."}
        if p.get("editable"):
            why = live_refusal(p.get("target_path") or "")
            if why:
                return {"editable": False, "mode": None, "target_path": None,
                        "target_name": None, "why": "Refused: " + why + "."}
        return p

    def _plan(self, f, listing):
        """Where a save of the file `f` goes, decided HERE and never by the page.

        {"editable", "why", "mode", "target_path", "target_name"}
          mode "update-self"     f is a human rewrite: it is updated, after a snapshot
          mode "update-rewrite"  f is a draft with one rewrite: THAT is updated, after a snapshot
          mode "create-rewrite"  f is a draft with none: `<stem>-owner-rewrite.md` is created
        """
        if f["kind"] == "note":
            return {"editable": False, "mode": None, "target_path": None, "target_name": None,
                    "why": "Notes are read-only here - they are an agent's record, not a "
                           "script."}
        if f["kind"] == "log":
            return {"editable": False, "mode": None, "target_path": None, "target_name": None,
                    "why": "Process logs are read-only here - they are an agent's record of "
                           "what it decided and why, not a script."}
        if f["kind"] == "rewrite" and live_refusal(f["path"]) is None:
            return {"editable": True, "mode": "update-self", "why": "",
                    "target_path": f["path"], "target_name": f["name"]}
        if f["kind"] == "rewrite" and f.get("ver") is not None:
            # o9L1264 (the human, 2026-10-11): the `(in app)` file is never written, but its script
            # still gets an Edit button. The edit goes to a plain `<NAME> v<N> THE HUMAN.md` in the
            # same script folder, numbered past every text of the script. When such a file
            # already exists above every in-app text (a rewrite saved mid-way), it is updated
            # instead, so opening the `(in app)` row again resumes it and mints no new number.
            same = [x for x in listing["by_id"].values()
                    if x.get("group") == f.get("group") and x.get("nkey") == f["nkey"]]
            top_app = max([x["ver"] for x in same if x.get("in_app") and x.get("ver") is not None]
                          or [f["ver"]])
            mine = [x for x in same if x["kind"] == "rewrite" and not x.get("in_app")
                    and (x.get("author") or "").casefold() == "the human"
                    and x.get("ver") is not None and x["ver"] > top_app
                    and live_refusal(x["path"]) is None]
            if mine:
                newest = max(mine, key=lambda x: x["ver"])
                return {"editable": True, "mode": "update-rewrite", "why": "",
                        "target_path": newest["path"], "target_name": newest["name"]}
            folder = f["path"].parent
            if folder.name.casefold() == "versions":
                folder = folder.parent
            try:
                on_disk = [c.name for c in folder.iterdir()]
            except OSError:
                on_disk = []
            name = next_version_name(f["nkey"], [x["ver"] for x in same], on_disk,
                                     source_name=f["name"])
            return {"editable": True, "mode": "create-rewrite", "why": "",
                    "target_path": folder / name, "target_name": name}
        if f["kind"] == "rewrite":
            return {"editable": True, "mode": "update-self", "why": "",
                    "target_path": f["path"], "target_name": f["name"]}
        # by id, not by folder: a draft in the flat versions/ names a rewrite one level up
        exact = [listing["by_id"][i] for i in (f.get("exact") or []) if i in listing["by_id"]]
        if len(exact) > 1:
            return {"editable": False, "mode": None, "target_path": None, "target_name": None,
                    "why": "This draft has %d rewrites whose names both claim to be THE "
                           "rewrite of it (%s), so a save here could not say which one it "
                           "updates. Open the one you mean and edit that."
                           % (len(exact), ", ".join(x["name"] for x in exact))}
        if exact:
            return {"editable": True, "mode": "update-rewrite", "why": "",
                    "target_path": exact[0]["path"], "target_name": exact[0]["name"]}
        if f.get("ver") is not None:
            # o10:W235 point 7: `<NAME> v<max+1> THE HUMAN.md`, in the script folder (the folder
            # above a flat `versions/`), numbered past EVERY text of the script.
            folder = f["path"].parent
            if folder.name.casefold() == "versions":
                folder = folder.parent
            same = [x for x in listing["by_id"].values()
                    if x.get("group") == f.get("group") and x.get("nkey") == f["nkey"]]
            try:
                on_disk = [c.name for c in folder.iterdir()]
            except OSError:
                on_disk = []
            name = next_version_name(f["nkey"], [x["ver"] for x in same], on_disk,
                                     source_name=f["name"])
            return {"editable": True, "mode": "create-rewrite", "why": "",
                    "target_path": folder / name, "target_name": name}
        name = f["stem"] + NEW_REWRITE_SUFFIX + ".md"
        return {"editable": True, "mode": "create-rewrite", "why": "",
                "target_path": f["path"].parent / name, "target_name": name}

    def describe(self, sid):
        """Everything the page needs to show one script and open an editor on it."""
        listing = self.discover()
        f, why = self.resolve(sid, listing)
        if f is None:
            return {"ok": False, "error": why, "code": 404}
        try:
            raw = f["path"].read_bytes()
        except OSError as ex:
            return {"ok": False, "error": "could not read it (%s)" % type(ex).__name__,
                    "code": 500}
        text, bad = decode_strict(raw)
        if text is None:
            text = norm_text(raw)
        plan = self.plan(f, listing)
        if bad:
            plan = dict(plan, editable=False, why="This file is %s." % bad)
        pair = listing["by_id"].get(f.get("pair")) if f.get("pair") else None
        self.prov.annotate([f], listing["by_id"], self.root)
        pair_text = None
        if pair is not None:
            try:
                pair_text = norm_text(pair["path"].read_bytes())
            except OSError:
                pair_text = None
        edit = {"editable": plan["editable"], "why": plan["why"], "mode": plan["mode"],
                "target_name": plan["target_name"], "target_exists": False,
                "target_sha": "", "target_text": None, "target_rel": None}
        if plan["editable"]:
            tp = plan["target_path"]
            edit["target_rel"] = tp.relative_to(self.workspace).as_posix()
            if tp.exists():
                tb = tp.read_bytes()
                edit["target_exists"] = True
                edit["target_sha"] = sha256(tb)
                edit["target_text"] = norm_text(tb)
        approve = self.approve_state(f, text)
        return {"ok": True, "id": f["id"], "name": f["name"], "folder": f["folder"],
                "same_text": same_text_of(f, list(listing["by_id"].values())),
                "rel": f["rel"], "kind": f["kind"], "kind_label": kind_label_for(f["kind"], approve),
                # o9L1267 (o9:W200): the path under the corpus root, the same string as the
                # row's `data-srel`. Ids are minted per run, so after a restart the page finds
                # this script again by it (matched against a fresh list, never sent to a write).
                "srel": f["relroot"],
                "text": text, "sha": sha256(raw),
                "pair": ({"id": pair["id"], "name": pair["name"], "kind": pair["kind"]}
                         if pair else None),
                "pair_text": pair_text,
                "prov": f.get("prov"),
                "variants": [listing["by_id"][v]["name"] for v in f.get("variants") or []
                             if v in listing["by_id"]],
                "edit": edit,
                "approve": approve}

    # ---- approving a script (o9 W192, the human 2026-10-11) -------------------------------------
    # the human's ruling: only approved scripts (in script-library/3-final/) reach the app. The button
    # asks `file_script.py approve` (o10's command) what it would do (--dry-run), shows that in a
    # pop-up, and on confirmation runs it for real; the Reader then lands the result by the same
    # Lander as a save. The page sends the script's id, never a path; the command's location is
    # `approve_argv`, one function, so a test can point it at a fake.

    def approve_argv(self):
        """The command prefix that runs approve: [python, <workspace>/.shared/scripts/
        file_script.py, --root, <workspace>, approve]. --root is passed so the command works on
        this Reader's workspace and no other. A test sets `self.approve_cmd` to a list to run
        another command (a fake, or the real one against a temp workspace)."""
        if self.approve_cmd:
            return list(self.approve_cmd)
        return [sys.executable, str(self.workspace / ".shared" / "scripts" / "file_script.py"),
                "--root", str(self.workspace), "approve"]

    def approve_state(self, f, text):
        """{"show", "approved", "why"}: whether the page offers Approve on this text, and whether
        it shows the Approved badge. The human (2026-10-11): every script text gets Approve, an agent
        draft or his own rewrite, an `(in app)` text included. Not a note or a process log (nor
        the legacy names the parser reads as logs); "why" stays empty for those, as they are not
        scripts. A text whose header carries `## Approved-By:` shows the badge; it keeps the
        button unless it is already in 3-final, since approving a July-approved text outside
        3-final still moves it there. A retired text, or a text outside script-library, gets
        no button and a "why" the page shows, so the button is never missing without a reason."""
        if f["kind"] in ("note", "log"):
            return {"show": False, "approved": False, "why": "", "final": False}
        approved = bool(text_facts(text)[0].get("approved-by", "").strip()) if text else False
        # "final" is training_header.is_final, the one definition (o9 W204): the human's Approved-By line or a final status.
        fin = training_header.is_final(text) if text else False
        if self.rel != LIBRARY_REL:
            return {"show": False, "approved": approved, "final": fin,
                    "why": "only a text in script-library can be approved"}
        top = f["relroot"].split("/", 1)[0].casefold()
        if top == "4-retired":
            return {"show": False, "approved": approved, "final": fin,
                    "why": "a retired script is not approved"}
        if approved and top == "3-final":
            return {"show": False, "approved": True, "final": fin, "why": ""}
        return {"show": True, "approved": approved, "final": fin, "why": ""}

    def _approve_run(self, f, dry):
        """Run the command. -> (result dict or None, error text). NEVER A SHELL; the path passed
        is the one this server resolved from the id."""
        argv = self.approve_argv() + [str(f["path"]), "--owner-ruling", _ruling_ref(), "--no-git"]
        if dry:
            argv.append("--dry-run")
        script = argv[1] if len(argv) > 1 else ""
        try:
            p = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=APPROVE_TIMEOUT,
                               cwd=str(self.workspace), creationflags=NO_WINDOW)
        except subprocess.TimeoutExpired:
            return None, "the approve command did not answer within %d seconds" % APPROVE_TIMEOUT
        except OSError as ex:
            return None, "the approve command could not be started (%s)" % type(ex).__name__
        got = None
        for line in reversed((p.stdout or "").strip().splitlines()):
            try:
                v = json.loads(line)
            except ValueError:
                continue
            if isinstance(v, dict) and "ok" in v:
                got = v
                break
        if got is None:
            err = ((p.stderr or "") + "\n" + (p.stdout or "")).strip()
            if "invalid choice" in err or "unknown command" in err.casefold() \
                    or not os.path.isfile(script):
                return None, APPROVE_MISSING
            # The output itself is never shown: a traceback can quote a script line, and an
            # error the human reads never carries script text (o10 W244 ruling 2).
            return None, ("the approve command gave no JSON answer (exit %s, %d line(s) of "
                          "output); nothing is shown here in case it quotes the script"
                          % (p.returncode, len(err.splitlines())))
        return got, ""

    def _show(self, p):
        """A folder path from the command, as the human reads it: relative to the library."""
        t = str(p or "").replace("\\", "/")
        for base in (str(self.root).replace("\\", "/"),
                     str(self.workspace).replace("\\", "/")):
            if t.casefold().startswith(base.casefold() + "/"):
                t = t[len(base) + 1:]
        return t.rstrip("/")

    def _approvable(self, sid):
        """(file, None, "") or (None, None, why)."""
        listing = self.discover()
        f, why = self.resolve(sid, listing)
        if f is None:
            return None, None, why
        try:
            text, _bad = decode_strict(f["path"].read_bytes())
        except OSError:
            text = None
        st = self.approve_state(f, text or "")
        if not st["show"]:
            return None, None, ("This text is already approved." if st["approved"] else
                                "This text cannot be approved here: %s."
                                % (st["why"] or "it is a note or a process log, not a script"))
        return f, listing, ""

    def approve_plan(self, sid):
        """What approving would do, from the command's --dry-run. Writes nothing."""
        f, _l, why = self._approvable(sid)
        if f is None:
            return {"ok": False, "error": why, "code": 400}
        got, err = self._approve_run(f, dry=True)
        # The pop-up names the script and the file before anything runs (the human's rule).
        base = {"name": f["name"], "version": f.get("ver"),
                "file": (Path(self.rel) / f["relroot"]).as_posix()}
        if got is None:
            return dict(base, ok=False, error=err, code=200)
        return dict(base, ok=True, refused=not got.get("ok"),
                    reason=str(got.get("reason") or ""),
                    folder_before=self._show(got.get("folder_before")),
                    folder_after=self._show(got.get("folder_after")),
                    app_change=str(got.get("app_change") or "none"))

    def _library_files(self):
        """{workspace-relative path: (size, mtime_ns, sha256 of the normalized text)} for every
        file under the library. The hash is what the Lander compares with the remote."""
        out = {}
        for d, dirs, names in os.walk(self.root):
            dirs[:] = [x for x in dirs if x != "__pycache__"]
            for n in names:
                fp = Path(d) / n
                try:
                    st = fp.stat()
                    data = fp.read_bytes()
                except OSError:
                    continue
                out[fp.relative_to(self.workspace).as_posix()] = (
                    st.st_size, st.st_mtime_ns, _text_hash(data))
        return out

    def _waypoint_bytes(self):
        """sha256 of the waypoint baseline's normalized text, or None when it is missing."""
        try:
            return _text_hash((self.workspace / WAYPOINT_BASELINE_REL).read_bytes())
        except OSError:
            return None

    # ---- refresh the "Finished Scripts" tab after an Approve (o9L1300, the human 2026-10-12) ------
    # the human: "The tab should update itself after each Approve in the Reader". Overlap: one
    # refresh runs at a time; an Approve that lands meanwhile sets a rerun flag, and the running
    # refresh starts one more run when it ends, so the last Approve is always reflected. A
    # Google failure is logged and never reaches the Approve reply.

    def sheet_log_path(self):
        return self.workspace / ".shared" / "state" / "reader-log" / "finished-sheet.log"

    def _sheet_log(self, line):
        try:
            lp = self.sheet_log_path()
            lp.parent.mkdir(parents=True, exist_ok=True)
            stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            old = []
            if lp.is_file():
                old = lp.read_text(encoding="utf-8", errors="replace").splitlines()[-199:]
            lp.write_text("\n".join(old + [stamp + " " + line]) + "\n", encoding="utf-8",
                          newline="\n")
        except Exception:
            pass

    def _sheet_launch(self, argv):
        """Run the sheet script. -> (exit code, last error line). The real launcher: no console
        window, no shell, output captured."""
        if self.sheet_launcher:
            return self.sheet_launcher(argv)
        if not os.path.isfile(argv[1]):
            return 127, "finished_scripts_sheet.py not found in this workspace"
        p = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=600, cwd=str(self.workspace),
                           creationflags=NO_WINDOW)
        lines = [x.strip() for x in ((p.stderr or "") + "\n" + (p.stdout or "")).splitlines()
                 if x.strip()]
        return p.returncode, (lines[-1][:300] if p.returncode and lines else "")

    def _sheet_loop(self):
        argv = [sys.executable, str(self.workspace / ".shared" / "scripts" /
                                    "finished_scripts_sheet.py")]
        while True:
            self._sheet_log("start")
            try:
                code, err = self._sheet_launch(argv)
                self._sheet_log("exit %s%s" % (code, (" last error: " + err) if err else ""))
            except Exception as ex:
                self._sheet_log("exit failed to run: %s" % type(ex).__name__)
            with self._sheet_state:
                if not self._sheet_pending:
                    self._sheet_running = False
                    return
                self._sheet_pending = False

    def refresh_finished_sheet(self):
        """Ask for a refresh; returns at once. Never raises."""
        try:
            with self._sheet_state:
                if self._sheet_running:
                    self._sheet_pending = True
                    return
                self._sheet_running = True
            if self.sheet_async:
                threading.Thread(target=self._sheet_loop, daemon=True,
                                 name="finished-sheet").start()
            else:
                self._sheet_loop()
        except Exception:
            with self._sheet_state:
                self._sheet_running = False

    # ---- review request (o9L1310) --------------------------------------------------------------
    # Approve & Review does not approve and does not touch the script. It writes one request file
    # naming the owning orchestrator (AT -> o3, SC -> o5); approve_review_relay.py lists the
    # unsent ones and o9 forwards each with send_message.

    @staticmethod
    def review_type(relroot):
        """"AT" / "SC" / "other", by the nearest folder name starting AT- or SC-: the same rule as
        render_daily_dashboard.final_script_folders (a folder's leaf name; `versions/` is part of
        its script's folder, so the walk goes upward past it)."""
        parts = str(relroot).replace("\\", "/").split("/")[:-1]
        for part in reversed(parts):
            u = part.upper()
            if u.startswith("AT-"):
                return "AT"
            if u.startswith("SC-"):
                return "SC"
        return "other"

    def review_requests_dir(self):
        return self.workspace / REVIEW_REQUESTS_REL

    def send_review_request(self, sid):
        """Write a review request for the text. -> {"ok", "message", "owner", "written", "code"}.
        Writes nothing but the one request file; the script is not read for content or changed."""
        f, _l, why = self._approvable(sid)
        if f is None:
            return {"ok": False, "error": why, "code": 400}
        typ = self.review_type(f["relroot"])
        owner = REVIEW_OWNERS.get(typ)
        if owner is None:
            return {"ok": False, "code": 400,
                    "error": "A review can be requested only for an AT or an SC script; "
                             "this text is neither."}
        try:
            data = f["path"].read_bytes()
        except OSError:
            return {"ok": False, "error": "could not read the text", "code": 200}
        sha = _text_hash(data)
        d = self.review_requests_dir()
        with self._lock:
            if d.is_dir():
                for q in d.glob("*.json"):
                    try:
                        v = json.loads(q.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        continue
                    if isinstance(v, dict) and v.get("sha256") == sha and not v.get("sent"):
                        return {"ok": True, "written": False, "owner": owner, "code": 200,
                                "message": "Already sent to %s for a review." % owner}
            now = datetime.datetime.now(datetime.timezone.utc)
            url, _w = reader_link(str(f["path"]), self.workspace)
            rec = {"rel": f["relroot"], "path": str(f["path"]).replace("\\", "/"),
                   "type": typ, "owner": owner, "sha256": sha, "reader_link": url or "",
                   "requested": now.isoformat(timespec="seconds"), "sent": None}
            d.mkdir(parents=True, exist_ok=True)
            stamp = now
            while (d / ("%s-%s.json" % (stamp.strftime("%Y%m%dT%H%M%SZ"), sha[:8]))).exists():
                stamp += datetime.timedelta(seconds=1)       # never overwrite an earlier request
            name = "%s-%s.json" % (stamp.strftime("%Y%m%dT%H%M%SZ"), sha[:8])
            tmp = d / (".tmp-%s" % uuid.uuid4().hex[:12])
            tmp.write_text(json.dumps(rec, indent=1), encoding="utf-8", newline="\n")
            os.replace(tmp, d / name)
        return {"ok": True, "written": True, "owner": owner, "code": 200,
                "message": "Sent to %s for a review." % owner}

    def approve(self, sid):
        """Run approve for real (with --no-git: the command makes no commit and no git move, and
        the Reader lands the result through its Lander, never in this workspace's checkout).
        -> a dict with "ok"; on success "adds" and "removes" (paths under the workspace, found by
        comparing the library before and after, plus the waypoint baseline when the command
        rewrote it, so the move and the baseline land in ONE commit), "new_id" (the approved
        text's id now) and the command's fields. A refusal ("ok": false from the command) moves
        nothing: the command rolls itself back, and nothing is landed."""
        with self._approve_lock:
            f, _l, why = self._approvable(sid)
            if f is None:
                return {"ok": False, "error": why, "code": 400}
            before = self._library_files()
            wp_before = self._waypoint_bytes()
            got, err = self._approve_run(f, dry=False)
            if got is None:
                return {"ok": False, "error": err, "code": 200}
            if not got.get("ok"):
                return {"ok": False, "error": str(got.get("reason") or "refused"), "code": 200}
            after = self._library_files()
            adds = sorted(r for r, v in after.items() if before.get(r) != v)
            removes = sorted(r for r in before if r not in after)
            if self._waypoint_bytes() != wp_before and \
                    (self.workspace / WAYPOINT_BASELINE_REL).is_file():
                adds.append(WAYPOINT_BASELINE_REL)
            # What each changed path held BEFORE the command ran (sha256 of its normalized text,
            # or None for a path that did not exist). The Lander lands only if the remote still
            # holds exactly that, so an approval never replaces a change someone else landed,
            # and two approvals in a row land without waiting for this checkout to update.
            prior = {}
            for r in adds + removes:
                if r == WAYPOINT_BASELINE_REL:
                    data = wp_before
                else:
                    data = before[r][2] if r in before else None
                prior[r] = data
            new_id = None
            cand = str(got.get("approved_file") or "")
            if cand:
                cp = Path(cand)
                for c in ([cp] if cp.is_absolute() else [self.workspace / cp, self.root / cp]):
                    rc = self.confine(c)
                    if rc is not None and rc.is_file():
                        try:
                            new_id = self.sid(rc.relative_to(self.root.resolve()).as_posix())
                        except ValueError:
                            pass
                        break
            self.refresh_finished_sheet()
            return {"ok": True, "adds": adds, "removes": removes, "new_id": new_id,
                    "name": f["name"], "opened_rel": (Path(self.rel) / f["relroot"]).as_posix(),
                    "folder_before": self._show(got.get("folder_before")),
                    "folder_after": self._show(got.get("folder_after")),
                    "app_change": str(got.get("app_change") or "none"),
                    "changed": bool(adds or removes), "prior": prior}

    # ---- what the rewrite's header carries from the original ---------------------------------

    def carry(self, f, listing, text, cur):
        """The model for the header of the rewrite being saved: the ORIGINAL script's model, or
        None when nothing records it.

        ⭐ COPIED INTO THE REWRITE rather than looked up each time the page is drawn (the human,
        2026-10-01: *"the original model should still be indicated"*), so the line stays right
        when the original is renamed, moved or gone. The first that answers:
          1. the text being saved: its line's model. This is how a rewrite keeps what it already
             carries, and how the human corrects it by hand;
          2. the same from the rewrite on disk the save replaces (he deleted the line);
          3. the original's own record, as the page shows it - the draft that was opened, or the
             draft a rewrite is paired with: its header line, else its prose;
          4. a prose model in the text itself ("authored on Opus 5"), copied over from the draft.
        ⭐ ONLY THE MODEL IS CARRIED. The original's authored date and time are not: the human's
        example is one line, so no `Original authored` line is written or restored (o9, W66 fix
        round 1).
        ⛔ IT NEVER BLOCKS A SAVE. A failure while asking about the original leaves the model
        `unknown`, and the save goes ahead."""
        model = None
        for t in [text] + ([norm_text(cur)] if cur is not None else []):
            model = model or carried_model(t)
        if not model:
            try:
                o = f if f["kind"] == "draft" else listing["by_id"].get(f.get("pair"))
                if o is not None and o.get("kind") == "draft":
                    self.prov.annotate([o], listing["by_id"], self.root)
                    model = ((o.get("prov") or {}).get("model") or {}).get("value")
            except Exception:
                pass
        return model or prose_model(text)

    # ---- the save ----------------------------------------------------------------------

    def save(self, sid, text, expect):
        """Write one save. Never touches the opened file unless it IS the human's rewrite.

        Returns a dict with "ok" and, on refusal, "code" and "error". On success it reports
        what was written, where the prior content went, and the opened file's hash before and
        after - so "the file you opened is untouched" is a measurement on every save.
        """
        with self._lock:
            listing = self.discover()
            f, why = self.resolve(sid, listing)
            if f is None:
                return {"ok": False, "code": 400, "error": why}
            plan = self.plan(f, listing)
            if not plan["editable"]:
                return {"ok": False, "code": 400, "error": plan["why"]}
            opened = f["path"]
            target = plan["target_path"]
            # ⛔ THE TARGET IS CONFINED AND MUST SIT BESIDE THE OPENED FILE. It is derived from
            # the opened file's own name, so this cannot fail today - it is checked so that a
            # later change to how targets are named cannot quietly start writing elsewhere.
            if self.confine(target) is None or self.confine(opened) is None \
                    or not self._beside(target, opened):
                return {"ok": False, "code": 400,
                        "error": "refused: the save target does not sit beside the file you "
                                 "opened inside the corpus"}
            # ⛔ o10:W235, CHECKED AGAIN AT THE WRITE, in every stage folder: never the
            # `(in app)` file, never a record file of an `App: live` script.
            why_live = live_refusal(target)
            if why_live:
                return {"ok": False, "code": 400, "error": "refused: " + why_live}

            try:
                opened_before = opened.read_bytes()
            except OSError as ex:
                return {"ok": False, "code": 500,
                        "error": "could not read the file you opened (%s)" % type(ex).__name__}
            cur = None
            if target.exists():
                try:
                    cur = target.read_bytes()
                except OSError as ex:
                    return {"ok": False, "code": 500,
                            "error": "could not read the rewrite (%s)" % type(ex).__name__}
                if decode_strict(cur)[0] is None:
                    return {"ok": False, "code": 400,
                            "error": "the rewrite on disk is not valid UTF-8, so saving over "
                                     "it from a text box could corrupt it - nothing written"}
            cur_sha = sha256(cur) if cur is not None else ""
            # ⛔ OPTIMISTIC CONCURRENCY. Other sessions write this corpus. If the rewrite on
            # disk is not the one the editor was opened against, a save would replace a
            # version the human has never seen - retained in a snapshot, but gone from the live file
            # without his knowing. Refuse and say so; his text stays in the editor.
            if (expect or "") != cur_sha:
                if cur is not None and expect:
                    why = ("%s changed on disk after you opened the editor (someone or "
                           "something else saved it)." % plan["target_name"])
                elif cur is not None:
                    why = ("%s was created on disk after you opened the editor."
                           % plan["target_name"])
                else:
                    why = "%s is gone from disk since you opened the editor." \
                          % plan["target_name"]
                return {"ok": False, "code": 409,
                        "error": why + " Nothing was written. Copy your text, reload, and "
                                       "re-apply it."}

            # ⛔ FM4: EVERY SAVE HERE IS A SAVE OF THE HUMAN'S REWRITE, SO IT CARRIES HIS LINE. A
            # rewrite starts as a copy of the agent's draft, header line and all; without this it
            # would go on naming the agent's model and date. The line under the `# title` is
            # replaced (or inserted there when absent) and nothing else in his text is touched.
            # It is part of the saved bytes, so it goes through the 409 check, the snapshot and
            # the landing exactly like his words do.
            # ⭐ IT NAMES THE ORIGINAL'S MODEL (the human, 2026-10-01) - see `carry`. It is ONE line:
            # nothing is written under it, and nothing that is already there is touched.
            # ⛔ o9L1177: a `<NAME> v<N> THE HUMAN.md` file that carries the library header block is a
            # second record file, and `file_script.py check` flags the folder. The human's text
            # starts as a copy of the record file, header and all, so the header goes here.
            # ⛔ o9L1236 REVERSES the strip above: the library header is KEPT. Stripping it left
            # a file check_script.py refuses as "not a script". A new rewrite takes the opened
            # record's header with `## Version:` = the number in its new name and `## Author:`
            # THE HUMAN; a later save keeps the header the file has. The editor may or may not
            # show the block: text sent without it gets it back from the file being replaced
            # (or from the opened record when the rewrite is new).
            pn_t = training_header.parse_script_name(target.name)
            if pn_t.version is not None and classify(target.name) == "rewrite":
                # o9L1238: a HUMAN file saved between o9L1177 and o9L1236 has no header on
                # disk. When the opened file is its source record, the header comes from that
                # record, as on create, so the next save makes the file a script again.
                if cur is not None and _split_header(norm_text(cur)) is not None:
                    text = restore_header(text, norm_text(cur))
                elif opened == target:
                    # o9L1241: update-self of such a file (the human opened the HUMAN file itself).
                    # Neither the file on disk nor, often, the text sent has a header, so the
                    # header comes from the script's record file, as on create. No record
                    # found: saved as before, without a header.
                    if _split_header(text) is None:
                        src = self._record_text_for(target, f, listing)
                        if src is not None and _split_header(src) is not None:
                            text = restore_header(text, src)
                            text = set_header_fields(text, Version=str(pn_t.version),
                                                     Author="THE HUMAN")
                else:
                    text = restore_header(text, norm_text(opened_before))
                    text = set_header_fields(text, Version=str(pn_t.version), Author="THE HUMAN")
                # A human rewrite is a script but never a record file (o9L1177 rule kept):
                # file_script's record test looks for these three lines.
                text = drop_header_fields(text, *RECORD_ONLY_FIELDS)
            model = self.carry(f, listing, text, cur)
            stamped = training_header.set_provenance_line(
                text, training_header.human_provenance_line(model=model))
            new = encode_like(stamped, cur if cur is not None else opened_before)
            base = {"ok": True, "mode": plan["mode"], "opened_name": f["name"],
                    "target_name": plan["target_name"],
                    "target_rel": target.relative_to(self.workspace).as_posix(),
                    "anchor_rel": opened.relative_to(self.workspace).as_posix(),
                    "opened_sha_before": sha256(opened_before)}
            # o9L1324 (o9:W212): the id this server issues for the file the save wrote, so the
            # page can ask for a review of THAT version (Approve & Review) and not of the file
            # that was open before the save. Same id Corpus.discover() will give it.
            try:
                base["target_id"] = self.sid(
                    target.relative_to(self.root.resolve()).as_posix())
            except ValueError:
                try:
                    base["target_id"] = self.sid(target.relative_to(self.root).as_posix())
                except ValueError:
                    base["target_id"] = None
            # "Changed" means HIS WORDS changed. The date in his line moving on its own is not
            # an edit, and writing it would snapshot, commit and push a save of nothing.
            # ⛔ A MODEL HE TYPED INTO THE LINE IS AN EDIT TOO. `carry` takes the model from his
            # text first, so that he can correct it by hand; when the correction was the only
            # change, comparing the words alone answered "Nothing changed" and dropped it (o9,
            # W66 review, o9L973). `unknown`, `none` or a deleted line names no model, so those
            # still write nothing.
            typed = carried_model(text)
            if cur is not None and _sans_line(cur) == _sans_line(new) \
                    and (typed is None or typed == carried_model(norm_text(cur))):
                return dict(base, changed=False, created=False, snapshot_rel=None,
                            target_sha=cur_sha, opened_sha_after=base["opened_sha_before"],
                            opened_unchanged=True,
                            message="Nothing changed - no file was written.")
            if cur is None and target != opened and _sans_line(new) == _sans_line(opened_before):
                return {"ok": False, "code": 400,
                        "error": "There are no changes against the draft, so there is nothing "
                                 "to save - no rewrite was created."}

            snap = None
            if cur is not None:
                try:
                    snap = self._snapshot(target, cur)
                except Exception as ex:
                    return {"ok": False, "code": 500,
                            "error": "could not keep a copy of the current rewrite first (%s: "
                                     "%s) - so NOTHING was written" % (type(ex).__name__, ex)}
            try:
                _write_atomic(target, new)
            except Exception as ex:
                return {"ok": False, "code": 500,
                        "error": "the write failed (%s: %s). %s" % (
                            type(ex).__name__, ex,
                            "The rewrite on disk is unchanged, and a copy of it was already "
                            "kept at %s." % snap.relative_to(self.workspace).as_posix()
                            if snap else "Nothing was created.")}

            if target != opened:
                after = opened.read_bytes()
                opened_after = sha256(after)
            else:
                opened_after = sha256(new)
            return dict(
                base, changed=True, created=cur is None,
                snapshot_rel=snap.relative_to(self.workspace).as_posix() if snap else None,
                history_rels=self.history_rels(target),
                target_sha=sha256(new), opened_sha_after=opened_after,
                # What was written, line endings folded - the editor shows it, so the stamped
                # provenance line is on screen and not only on disk.
                saved_text=norm_text(new),
                # Meaningful only when the opened file is not the target; when the human edits his
                # own rewrite, that file is SUPPOSED to change and the snapshot is the record.
                opened_unchanged=(opened_after == base["opened_sha_before"])
                if target != opened else None,
                message="Saved.")

    def _record_text_for(self, target, f, listing):
        """The text of the record file a human rewrite at `target` belongs to, or None (o9L1241).

        First the folder's record file by the library's own rule (file_script.record_file).
        Else the draft the Reader pairs the rewrite with (`pair`: its `## Rewrite-Of: v<K>`
        line, else the highest non-THE HUMAN version below it). Never `target` itself, and never a
        file outside the corpus. Any error reading it gives None, so the save goes on."""
        cands = []
        try:
            import file_script
            rec = file_script.record_file(target.parent)
            if rec is not None:
                cands.append(Path(rec))
        except Exception:
            pass
        pair = listing["by_id"].get(f.get("pair")) if f.get("pair") else None
        if pair is not None:
            cands.append(Path(pair["path"]))
        for p in cands:
            try:
                if p.resolve() == target.resolve() or self.confine(p) is None:
                    continue
                return norm_text(p.read_bytes())
            except OSError:
                continue
        return None

    def _snapshot(self, target, data):
        hist = target.parent / HISTORY_DIR
        sdir = hist / target.stem
        # Confined like everything else - the stem comes from a name on disk.
        root = self._root_resolved()
        sres = sdir.resolve()
        if root is None or not sres.is_relative_to(root):
            raise OSError("snapshot folder would fall outside the corpus")
        sdir.mkdir(parents=True, exist_ok=True)
        readme = hist / "README.txt"
        if not readme.exists():
            with open(readme, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(README_TEXT)
        stamp = _utc_stamp()
        for n in range(1, 1000):
            p = sdir / ("%s%s%s" % (stamp, "" if n == 1 else "-%d" % n, SNAP_EXT))
            try:
                fh = open(p, "xb")          # exclusive: a snapshot is never overwritten either
            except FileExistsError:
                continue
            with fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            break
        else:
            raise OSError("no free snapshot name")
        # ⛔ READ BACK AND COMPARE. A copy that was not verified is not a copy.
        if p.read_bytes() != data:
            raise OSError("the snapshot did not read back identical")
        return p

    def history_rels(self, target):
        """Every file under the target's history folder, plus the folder README. These are
        landed with the rewrite, so a snapshot left behind by a failed landing is swept up by
        the next one."""
        hist = target.parent / HISTORY_DIR
        out = []
        readme = hist / "README.txt"
        if readme.is_file():
            out.append(readme.relative_to(self.workspace).as_posix())
        sdir = hist / target.stem
        if sdir.is_dir():
            for p in sorted(sdir.iterdir()):
                if p.is_file() and p.suffix == SNAP_EXT:
                    out.append(p.relative_to(self.workspace).as_posix())
        return out


def _sans_line(data):
    """Text with line endings folded and the FM4 provenance line removed - what "did his words
    change?" is asked of."""
    return training_header.strip_provenance_line(norm_text(data))


def _write_atomic(path, data):
    """Temp file beside it, fsync, os.replace, read back.

    ⚠️ The temp name carries NO part of the real name. The real name may carry a rewrite
    suffix, and a scanner that happened to walk the folder during the write would count a
    `...the human rewrite...tmp` as a live rewrite."""
    tmp = path.parent / (".reader-save-%s.tmp" % uuid.uuid4().hex[:12])
    with open(tmp, "xb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    try:
        os.replace(tmp, path)
    except OSError:
        # Only OUR temp file is removed - never anything that was there before.
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    if path.read_bytes() != data:
        raise OSError("the saved file did not read back identical")


# ==========================================================================================
# THE DIFF - additions and deletions against the file he opened
#
# ⭐ LINES FIRST, THEN WORDS INSIDE A CHANGED LINE. A script is prose, and a paragraph is one
# line, so a line-only diff would mark a whole paragraph changed for one word. Pairing the
# changed lines and diffing their words shows the edit he actually made - which is the delta
# MASTER-TRAINING-DOC.md Section 2 says is the teaching signal.
# ==========================================================================================

_TOK = re.compile(r"\s+|[\w\u2019'-]+|[^\w\s]", re.U)
CONTEXT = 2


def _words(a, b):
    """Two already-unescaped lines -> (html for the old line, html for the new line)."""
    ta, tb = _TOK.findall(a), _TOK.findall(b)
    sm = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
    oa, ob = [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        x, y = html.escape("".join(ta[i1:i2])), html.escape("".join(tb[j1:j2]))
        if tag == "equal":
            oa.append(x)
            ob.append(y)
        else:
            if x:
                oa.append("<del>%s</del>" % x)
            if y:
                ob.append("<ins>%s</ins>" % y)
    return "".join(oa), "".join(ob)


class _Row(str):
    """A drawn diff row that also remembers its parts, so a painter can redraw its text."""
    parts = None


def _row(cls, lo, ln, body):
    mark = "&minus;" if "sd-del" in cls else "+" if "sd-ins" in cls else "&nbsp;"
    r = _Row('<div class="sd-row %s"><span class="sd-ln">%s</span><span class="sd-ln">%s</span>'
             '<span class="sd-m">%s</span><span class="sd-t">%s</span></div>'
             % (cls, lo if lo is not None else "", ln if ln is not None else "", mark,
                body or "&nbsp;"))
    r.parts = (cls, lo, ln, body)
    return r


class _Fold:
    def __init__(self, hidden, inner):
        self.hidden, self.inner = hidden, inner

    def html(self):
        return ('<details class="sd-fold"><summary>%d unchanged line%s</summary>%s</details>'
                % (self.hidden, "" if self.hidden == 1 else "s", "".join(self.inner)))


def _paint_rows(rows, paint):
    """o9:W120 box 2 - redraw each row's text through `paint`, one side at a time, so a note that
    runs over several lines is paired as it is in the read view. The old side is the removed and
    unchanged rows, the new side the added and unchanged rows; an unchanged row takes the new
    side's drawing. `paint` takes finished html and returns it; tags pass through it untouched,
    so the rows are joined by <br> and cut apart again."""
    flat = []
    for r in rows:
        flat.extend(r.inner if isinstance(r, _Fold) else [r])
    sep = "<br>"
    drawn = {}
    for side in ("sd-del", "sd-ins"):
        mine = [r for r in flat if side in r.parts[0] or "sd-eq" in r.parts[0]]
        out = paint(sep.join(r.parts[3] or "" for r in mine)).split(sep)
        if len(out) != len(mine):
            return rows                     # never lose a row to a painter that misbehaves
        for r, t in zip(mine, out):
            if side == "sd-ins" or "sd-eq" not in r.parts[0]:
                drawn[id(r)] = t

    def redo(r):
        c, lo, ln, _b = r.parts
        return _row(c, lo, ln, drawn.get(id(r), r.parts[3]))
    return [_Fold(r.hidden, [redo(x) for x in r.inner]) if isinstance(r, _Fold) else redo(r)
            for r in rows]


def diff(base, text, paint=None):
    """{"html", "added", "removed", "changed", "same"} comparing `text` against `base`.
    `paint`, when given, redraws the rows' text (the Reader passes its {{{ note }}} painter)."""
    a = norm_text(base or "").split("\n")
    b = norm_text(text or "").split("\n")
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    ops = sm.get_opcodes()
    rows, added, removed = [], 0, 0
    for k, (tag, i1, i2, j1, j2) in enumerate(ops):
        if tag == "equal":
            n = i2 - i1
            first, last = k == 0, k == len(ops) - 1
            head = 0 if first else min(CONTEXT, n)
            tail = 0 if last else min(CONTEXT, n - head)
            for d in range(head):
                rows.append(_row("sd-eq", i1 + d + 1, j1 + d + 1, html.escape(a[i1 + d])))
            hidden = n - head - tail
            if hidden > 0:
                inner = [_row("sd-eq", i1 + head + d + 1, j1 + head + d + 1,
                              html.escape(a[i1 + head + d])) for d in range(hidden)]
                rows.append(_Fold(hidden, inner))
            for d in range(n - tail, n):
                rows.append(_row("sd-eq", i1 + d + 1, j1 + d + 1, html.escape(a[i1 + d])))
            continue
        old, new = a[i1:i2], b[j1:j2]
        removed += len(old)
        added += len(new)
        if tag == "replace":
            m = min(len(old), len(new))
            for d in range(m):
                # ⭐ ONLY PAIR LINES THAT ARE RECOGNISABLY THE SAME LINE. Two unrelated
                # paragraphs diffed word by word produce confetti; shown as a removal and an
                # addition they read as what they are.
                if difflib.SequenceMatcher(None, old[d], new[d]).quick_ratio() >= 0.45:
                    x, y = _words(old[d], new[d])
                    rows.append(_row("sd-del sd-mod", i1 + d + 1, None, x))
                    rows.append(_row("sd-ins sd-mod", None, j1 + d + 1, y))
                else:
                    rows.append(_row("sd-del", i1 + d + 1, None, html.escape(old[d])))
                    rows.append(_row("sd-ins", None, j1 + d + 1, html.escape(new[d])))
            for d in range(m, len(old)):
                rows.append(_row("sd-del", i1 + d + 1, None, html.escape(old[d])))
            for d in range(m, len(new)):
                rows.append(_row("sd-ins", None, j1 + d + 1, html.escape(new[d])))
        elif tag == "delete":
            for d, ln in enumerate(old):
                rows.append(_row("sd-del", i1 + d + 1, None, html.escape(ln)))
        elif tag == "insert":
            for d, ln in enumerate(new):
                rows.append(_row("sd-ins", None, j1 + d + 1, html.escape(ln)))
    same = not added and not removed
    if same:
        body = '<div class="sd-none">No changes against this version.</div>'
    else:
        if paint is not None:
            rows = _paint_rows(rows, paint)
        body = '<div class="sd">%s</div>' % "".join(
            r.html() if isinstance(r, _Fold) else r for r in rows)
    return {"html": body, "added": added, "removed": removed, "same": same}


# ==========================================================================================
# LANDING - every save reaches origin, from a worktree that is NOT the shared checkout
#
# ⛔ NEVER THE SHARED CHECKOUT. Several sessions share `<your-workspace>`, it  # home-path-ok: comment text, never opened
# carries their uncommitted work, and it is hundreds of commits behind. Committing there would
# sweep their staged files into the human's save, or be refused by their state. So the landing runs
# in a dedicated worktree, reused across saves, brought to the remote's tip before every one.
#
# ⛔ AND IT IS VERIFIED TO BE ITS OWN WORKTREE BEFORE ANY git COMMAND RUNS IN IT. The worktree
# lives INSIDE the workspace. If its `.git` file were ever missing, `git -C <it>` would walk up
# and act on the SHARED CHECKOUT - and `checkout -f` there would destroy other sessions' work.
# `rev-parse --show-toplevel` must name the worktree itself, or nothing runs.
#
# ⛔ NEVER --no-verify. The workspace's pre-commit hooks run exactly as they would for anyone,
# and a hook that refuses is reported in the page in its own words.
# ==========================================================================================

TRAILER = "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"


def _same_path(a, b):
    return os.path.normcase(os.path.realpath(str(a))) == os.path.normcase(os.path.realpath(str(b)))


class LandingFailed(Exception):
    def __init__(self, reason, detail=""):
        Exception.__init__(self, reason)
        self.reason = reason
        self.detail = detail


class Lander:
    def __init__(self, workspace, worktree=None, remote="origin", branch="main",
                 base_branch="main"):
        self.workspace = Path(workspace).resolve()
        self.worktree = Path(worktree).resolve() if worktree else \
            self.workspace / ".worktrees" / "reader-landing"
        self.remote = remote
        self.branch = branch
        # Only used when `branch` does not exist on the remote yet (a test branch): the first
        # landing is based on this instead.
        self.base_branch = base_branch
        self.jobs = {}
        self._q = queue.Queue()
        self._lock = threading.Lock()
        self._thread = None
        # o9 W202: called with the job after an approve job that moved a folder has landed on
        # `main`; returns the runner-start answer kept as job["runner"]. None (the default, and
        # every test) starts nothing. The Reader's main() sets it.
        self.on_approve_landed = None

    # ---- the queue ---------------------------------------------------------------------

    def submit(self, anchor_rel, target_rel, history_rels, mode, opened_name,
               snapshot_rel=None, extra=None):
        jid = uuid.uuid4().hex[:12]
        job = {"id": jid, "state": "queued", "step": "waiting to land",
               "anchor_rel": anchor_rel, "target_rel": target_rel,
               "history_rels": list(history_rels or []), "mode": mode,
               "opened_name": opened_name, "snapshot_rel": snapshot_rel,
               "branch": self.branch,
               "sha": "", "reason": "", "detail": ""}
        # set before the job is queued: the worker may pick it up the moment it is put
        job.update(extra or {})
        with self._lock:
            self.jobs[jid] = job
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._worker, daemon=True,
                                                name="reader-lander")
                self._thread.start()
        self._q.put(jid)
        return jid

    def submit_approve(self, adds, removes, opened_rel, label, prior=None, start_runner=False):
        """o9 W192: land what `file_script.py approve` changed - files to add (or replace) and
        files to delete, as paths under the workspace - in ONE commit. `prior` maps each path to
        the hash of what it held before approving (None: it did not exist); _check_approve
        lands only if the remote still holds that. The same queue, worktree, hooks and push as
        a save."""
        return self.submit(opened_rel, adds[0] if adds else opened_rel, [], "approve", label,
                           extra={"adds": list(adds), "removes": list(removes),
                                  "prior": dict(prior or {}),
                                  "start_runner": bool(start_runner)})

    def status(self, jid):
        with self._lock:
            j = self.jobs.get(jid)
            return dict(j) if j else None

    def _set(self, job, **kw):
        with self._lock:
            job.update(kw)

    def _worker(self):
        while True:
            jid = self._q.get()
            job = self.jobs.get(jid)
            if job is None:
                continue
            self._set(job, state="running", step="starting")
            try:
                sha, note = self.land(job)
                runner = self._after_landed(job, sha)
                self._set(job, state="landed", sha=sha, step="done", reason=note,
                          **({"runner": runner} if runner else {}))
            except LandingFailed as ex:
                self._set(job, state="failed", reason=ex.reason, detail=ex.detail[-4000:],
                          step="stopped")
            except Exception as ex:          # never let the thread die silently
                self._set(job, state="failed", reason="the landing crashed (%s: %s)"
                          % (type(ex).__name__, ex), step="stopped")

    def _after_landed(self, job, sha):
        """o9 W202: start the library runner once an approval that moved a folder is on `main`.
        Never raises and never waits: a failure is returned for the page to show."""
        if job.get("mode") != "approve" or not job.get("start_runner") \
                or self.on_approve_landed is None:
            return None
        if self.branch != "main":
            return {"started": False, "error": "this landing went to %s, not main, so no "
                    "library run was started" % self.branch}
        try:
            got = self.on_approve_landed(dict(job, sha=sha))
            return got if isinstance(got, dict) else {"started": False, "error": "no answer"}
        except Exception as ex:
            return {"started": False, "error": "the library runner could not be started (%s)"
                    % type(ex).__name__}

    # ---- git -----------------------------------------------------------------------------

    def _git(self, cwd, *args, timeout=300):
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_EDITOR="true",
                   GIT_MERGE_AUTOEDIT="no")
        p = subprocess.run(["git", "-C", str(cwd)] + list(args), capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           env=env, creationflags=NO_WINDOW)
        return p.returncode, p.stdout or "", p.stderr or ""

    def _wt(self, *args, **kw):
        return self._git(self.worktree, *args, **kw)

    def _verify_worktree(self):
        # ⛔ PATHS FIRST, BEFORE ANY git COMMAND RUNS IN IT. Security review, 2026-09-23:
        # `--land-worktree <the workspace>` passed every check below, and the landing then ran
        # `checkout -f` and `clean -fd` in the shared checkout - another session's untracked
        # file deleted, its staged change reverted, HEAD detached. A folder that IS the
        # workspace, or holds it, is refused whatever git says about it.
        wt = os.path.normcase(os.path.realpath(str(self.worktree)))
        ws = os.path.normcase(os.path.realpath(str(self.workspace)))
        if wt == ws or ws.startswith(wt.rstrip("\\/") + os.sep):
            raise LandingFailed(
                "the landing folder is the workspace itself, or a folder that contains it, so "
                "nothing was run in it",
                "%s would be reset and cleaned by a landing, and the workspace %s is inside "
                "it. Point --land-worktree at a folder of its own." % (self.worktree,
                                                                       self.workspace))
        rc, top, err = self._wt("rev-parse", "--show-toplevel")
        if rc or not _same_path(top.strip(), self.worktree):
            raise LandingFailed(
                "the landing folder is not its own git worktree, so no git command was run "
                "in it",
                "%s: git reports its top level as %r. Running git there would act on the "
                "checkout that CONTAINS it. Remove the folder by hand (it holds nothing but "
                "copies) and the next save recreates it." % (self.worktree, top.strip() or err))
        # ⛔ A LINKED WORKTREE, NEVER A MAIN CHECKOUT. A main checkout is its own top level and
        # belongs to the right repository, so the checks either side of this pass it - and a
        # main checkout is exactly where other sessions keep uncommitted work. Only a linked
        # worktree has a git dir that differs from the repository's common dir.
        rcg, gd, _ = self._wt("rev-parse", "--path-format=absolute", "--git-dir")
        rcc, cd, _ = self._wt("rev-parse", "--path-format=absolute", "--git-common-dir")
        if rcg or rcc or not gd.strip() or _same_path(gd.strip(), cd.strip()):
            raise LandingFailed(
                "the landing folder is a repository's MAIN checkout, not a linked worktree, so "
                "nothing was run in it",
                "%s: git dir %r, common dir %r. A landing resets and cleans the folder it runs "
                "in; point --land-worktree at a folder of its own and the next save creates "
                "a linked worktree there." % (self.worktree, gd.strip(), cd.strip()))
        rc1, c1, _ = self._wt("rev-parse", "--path-format=absolute", "--git-common-dir")
        rc2, c2, _ = self._git(self.workspace, "rev-parse", "--path-format=absolute",
                               "--git-common-dir")
        if rc1 or rc2 or not _same_path(c1.strip(), c2.strip()):
            raise LandingFailed("the landing worktree belongs to a different repository than "
                                "the workspace", "%r vs %r" % (c1.strip(), c2.strip()))

    def _ensure_worktree(self, job):
        if not self.worktree.exists():
            self._set(job, step="creating the landing worktree")
            rc, out, err = self._git(self.workspace, "fetch", "-q", self.remote,
                                     self.base_branch)
            if rc:
                raise LandingFailed("could not fetch %s/%s" % (self.remote, self.base_branch),
                                    err or out)
            rc, out, err = self._git(self.workspace, "worktree", "add", "-q", "--detach",
                                     str(self.worktree),
                                     "%s/%s" % (self.remote, self.base_branch))
            if rc:
                raise LandingFailed("could not create the landing worktree at %s"
                                    % self.worktree, err or out)
        self._verify_worktree()

    def _remote_has_branch(self):
        rc, _, _ = self._wt("rev-parse", "--verify", "-q",
                            "refs/remotes/%s/%s" % (self.remote, self.branch))
        return rc == 0

    def _tip(self):
        return "%s/%s" % (self.remote,
                          self.branch if self._remote_has_branch() else self.base_branch)

    def _names_at(self, ref, folder_rel):
        """{casefolded name: name} of the files directly in `folder_rel` at `ref`."""
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
        spec = ["--", folder_rel.rstrip("/") + "/"] if folder_rel.strip("/") else []
        p = subprocess.run(["git", "-C", str(self.worktree), "ls-tree", "-z", "--name-only",
                            ref] + spec,
                           capture_output=True, timeout=120, env=env,
                           creationflags=NO_WINDOW)
        if p.returncode:
            return {}
        names = [n.decode("utf-8", "replace").rsplit("/", 1)[-1]
                 for n in (p.stdout or b"").split(b"\0") if n]
        return {n.casefold(): n for n in names}

    def _read_at(self, ref, rel):
        """The bytes of `rel` at `ref`, or None. ⛔ READ FROM THE REF, NOT THE WORKTREE'S FILES:
        after a race is rebased the worktree holds OUR commit on top of theirs, and a check read
        from it would find the human's own file and pass. A name differing only in case is found
        too, as the Windows checkout this replaced would have found it."""
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
        folder, _, name = rel.rpartition("/")
        actual = self._names_at(ref, folder).get(name.casefold()) if folder else name
        if not actual:
            return None
        p = subprocess.run(["git", "-C", str(self.worktree), "cat-file", "blob",
                            "%s:%s" % (ref, (folder + "/" if folder else "") + actual)],
                           capture_output=True, timeout=120, env=env,
                           creationflags=NO_WINDOW)
        return p.stdout if p.returncode == 0 else None

    def _check_against_remote(self, job, ref=None):
        """⛔ REFUSE TO LAND OVER A VERSION THIS WORKSPACE NEVER SAW.

        The reader's workspace can be far behind the remote. If the remote holds a version of
        the rewrite that is not the one the human edited from, landing his file would replace it
        on `main` - in history, but gone from the tip without anyone deciding that. So the
        remote's copy must be absent (a new rewrite), or be one this workspace already had:
        the current file, or one of its own snapshots.
        """
        # Read from the REF - the remote tip, or after a rebase the tip that was rebased onto -
        # never from the worktree's files, which after a rebase hold OUR commit as well.
        ref = ref or self._tip()
        anchor, target = job["anchor_rel"], job["target_rel"]
        if self._read_at(ref, anchor) is None:
            raise LandingFailed(
                "the file you opened is not at this path on %s, so this checkout is behind "
                "(it was probably moved or renamed there). Saved on disk only." % ref,
                "missing on the remote: %s" % anchor)
        remote = self._read_at(ref, target)
        disk = (self.workspace / target).read_bytes()
        if remote is not None:
            known = {norm_text(disk)}
            for rel in job["history_rels"]:
                if rel.endswith(SNAP_EXT):
                    try:
                        known.add(norm_text((self.workspace / rel).read_bytes()))
                    except OSError:
                        pass
            if norm_text(remote) not in known:
                raise LandingFailed(
                    "%s has a DIFFERENT version of %s than the one you edited - someone "
                    "changed it there since this checkout last updated. Landing yours would "
                    "replace theirs, so it was not landed. Saved on disk only."
                    % (ref, Path(target).name), target)
            return
        # Absent on the remote. Fine for a brand-new rewrite - unless this workspace's own
        # HEAD has the file, which means the remote deleted or moved it since.
        rc, _, _ = self._git(self.workspace, "cat-file", "-e", "HEAD:%s" % target)
        if rc == 0:
            raise LandingFailed(
                "%s no longer has %s (moved, renamed or deleted since this checkout last "
                "updated). Landing would re-create it at the old path. Saved on disk only."
                % (ref, Path(target).name), target)
        # And the remote must not already hold a DIFFERENTLY-SPELLED rewrite of the same draft.
        if job["mode"] == "create-rewrite":
            folder = target.rpartition("/")[0]
            names = set(self._names_at(ref, folder).values())
            names |= set(self._names_at(ref, folder + "/versions").values())  # flat versions/
            names |= set(self._names_at(ref, anchor.rpartition("/")[0]).values())
            name = competing_rewrite(Path(anchor).name, Path(target).name, sorted(names))
            if name:
                raise LandingFailed(
                    "%s already has a rewrite of this draft named %s, which this checkout "
                    "does not have. Landing yours would make two. Saved on disk only."
                    % (ref, name), name)
            name = version_clash(Path(target).name, sorted(names))
            if name:
                raise LandingFailed(
                    "%s already has %s, a text with the same version number as yours, which "
                    "this checkout does not have. Landing yours would give two texts one "
                    "number. Saved on disk only." % (ref, name), name)

    def _check_approve(self, job, ref=None):
        """A removed file must be on the remote tip and be the text this workspace has at HEAD:
        otherwise the checkout is behind, or someone changed the text there, and moving it would
        carry their change away (or re-create a moved file). A file approving rewrote (the
        approved text, THE HUMAN-APPROVED.csv, INDEX.md, the waypoint baseline) must be on the remote
        exactly as this workspace has it at HEAD, or absent from both: otherwise someone changed
        it there since this checkout updated, and landing ours would replace their change.

        "Before approving" is job["prior"] (Corpus.approve hashed the files before the command
        ran), not this checkout's HEAD: the checkout is not updated by a landing, and two
        approvals in a row would otherwise refuse the second over the first one's own change."""
        ref = ref or self._tip()
        prior = job.get("prior") or {}
        for rel in job.get("removes", []) + job.get("adds", []):
            remote = self._read_at(ref, rel)
            have = prior.get(rel)
            got = None if remote is None else _text_hash(remote)
            if got == have:
                continue
            if rel in job.get("removes", []) and remote is None:
                # never on the remote: an untracked file the command moved, nothing to remove
                # there - unless this checkout's HEAD has it, which means the remote moved it
                p = subprocess.run(["git", "-C", str(self.workspace), "cat-file", "-e",
                                    "HEAD:" + rel], capture_output=True, timeout=120,
                                   creationflags=NO_WINDOW)
                if p.returncode != 0:
                    continue
                raise LandingFailed(
                    "%s does not have %s, which approving moved. This checkout is behind (it "
                    "was probably moved or renamed there). Saved on disk only." % (ref, rel), rel)
            raise LandingFailed(
                "%s has a different version of %s than this workspace had before approving "
                "(someone changed it there, or this checkout is behind), so landing would "
                "replace their change. Not landed. Saved on disk only." % (ref, rel), rel)

    def land(self, job):
        """Run one landing to completion. Returns (sha, note) or raises LandingFailed."""
        self._ensure_worktree(job)

        self._set(job, step="fetching %s" % self.remote)
        rc, out, err = self._wt("fetch", "-q", self.remote)
        if rc:
            raise LandingFailed("could not fetch from %s" % self.remote, err or out)
        tip = self._tip()
        self._set(job, step="bringing the landing worktree to %s" % tip)
        self._verify_worktree()
        rc, out, err = self._wt("checkout", "-q", "-f", "--detach", tip)
        if rc:
            raise LandingFailed("could not check out %s in the landing worktree" % tip,
                                err or out)
        # Leftovers from an earlier landing that failed part-way. Safe ONLY because the
        # worktree was verified above to be its own top level - never the shared checkout.
        rc, out, err = self._wt("clean", "-fdq")
        if rc:
            raise LandingFailed("could not clean the landing worktree", err or out)

        self._set(job, step="checking %s has not moved underneath you" % tip)
        approve = job["mode"] == "approve"
        if approve:
            self._check_approve(job)
            rels = list(job["adds"])
        else:
            self._check_against_remote(job)
            rels = [job["target_rel"]] + [r for r in job["history_rels"]
                                          if r != job["target_rel"]]
        lw_root = self.worktree.resolve()
        for rel in rels:
            src = self.workspace / rel
            dst = (self.worktree / rel)
            if not dst.resolve().is_relative_to(lw_root):
                raise LandingFailed("refused: %s would land outside the worktree" % rel)
            if not src.is_file():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            data = src.read_bytes()
            if dst.is_file() and dst.read_bytes() == data:
                continue
            with open(dst, "wb") as fh:
                fh.write(data)

        self._set(job, step="committing")
        removes = list(job.get("removes", [])) if approve else []
        for rel in removes:
            if not (self.worktree / rel).resolve().is_relative_to(lw_root):
                raise LandingFailed("refused: %s would be removed outside the worktree" % rel)
            rc, out, err = self._wt("rm", "-q", "-f", "--ignore-unmatch", "--", rel)
            if rc:
                raise LandingFailed("git rm refused", err or out)
        if rels:
            rc, out, err = self._wt("add", "--", *rels)
            if rc:
                raise LandingFailed("git add refused", err or out)
        rc, _, _ = self._wt("diff", "--cached", "--quiet", "--", *(rels + removes))
        if rc == 0:
            rc, sha, _ = self._wt("rev-parse", "HEAD")
            return sha.strip(), "already on %s - identical content, nothing to commit" % tip

        target_name = Path(job["target_rel"]).name
        # The snapshot THIS save made. Earlier ones in the same folder are landed too (a failed
        # landing leaves them behind), but the message names what this save did.
        snaps = [job["snapshot_rel"]] if job.get("snapshot_rel") else []
        # ⛔ THE MESSAGE SAYS WHAT HAPPENED IN THIS MODE, NOT A GENERAL PROMISE. When the human opens
        # his own rewrite, the opened file IS the one that changes - a line claiming otherwise
        # would be false in exactly the commits that carry a snapshot.
        what = ("the human edited his own rewrite; what it said before is kept in the snapshot "
                "above." if job["mode"] == "update-self" else
                # reader_compare.py (o8:W72): a note on WHY he made a change. Neither the
                # draft nor the rewrite is written by it; the notes file is.
                # ⛔ "the snapshot above" only when a snapshot line WAS written above: the first
                # save of a notes file creates it, and has no earlier version to keep.
                "the human's reason for a change, written in the Compare view. The draft and the "
                "rewrite are not changed by it%s." % (
                    "; the notes file's earlier version is kept in the snapshot above"
                    if snaps else "; this save created the notes file")
                if job["mode"] == "why-notes" else
                "The agent draft that was opened is not changed by a save.")
        msg = ("Reader save - the human's edit to %s\n\n"
               "Saved from the OrchDoc Reader's script editor (%s).\n"
               "  file:      %s\n"
               "  opened:    %s\n"
               "%s\n"
               "%s A rewrite is snapshotted under\n"
               ".edit-history/ before it is replaced.\n\n%s\n"
               % (target_name, job["mode"], job["target_rel"], job["opened_name"],
                  "".join("  snapshot:  %s\n" % s for s in snaps), what, TRAILER))
        if approve:
            msg = ("Reader approve - the human approved %s\n\n"
                   "Approved from the OrchDoc Reader's script view after its confirmation "
                   "pop-up; `file_script.py approve` made the change.\n"
                   "%s%s\n%s\n"
                   % (job["opened_name"],
                      "".join("  added:     %s\n" % r for r in rels),
                      "".join("  removed:   %s\n" % r for r in removes), TRAILER))
        rc, out, err = self._wt("commit", "-q", "-m", msg, timeout=600)
        if rc:
            # ⛔ THE HOOK'S OWN WORDS. A refusal paraphrased is a refusal nobody can act on.
            raise LandingFailed("the commit was refused (a pre-commit or commit-msg hook said "
                                "no). Saved on disk; not landed.", (out + "\n" + err).strip())

        pushed = False
        detail = ""
        for attempt in (1, 2):
            self._set(job, step="pushing to %s/%s (attempt %d)" % (self.remote, self.branch,
                                                                   attempt))
            rc, out, err = self._wt("push", "-q", self.remote,
                                    "HEAD:refs/heads/%s" % self.branch)
            if rc == 0:
                pushed = True
                break
            detail = (out + "\n" + err).strip()
            if attempt != 1:
                break
            # ⭐ WAS IT A RACE? Asked of git, not of the error text - `push -q` words its
            # refusals differently across versions, and a matcher on the wording is one quiet
            # upgrade away from never retrying. A race means the remote branch now holds a
            # commit this HEAD does not: fetch it and ask whether it is an ancestor of HEAD.
            rc, o2, e2 = self._wt("fetch", "-q", self.remote, self.branch)
            if rc:
                break
            rc, _, _ = self._wt("merge-base", "--is-ancestor",
                                "%s/%s" % (self.remote, self.branch), "HEAD")
            if rc == 0:
                break                        # the remote did not move - not a race; report it
            # ⭐ SOMEONE LANDED FIRST. Rebase once onto their tip and try again - the files this
            # commit touches are the human's rewrite and its history, so a conflict is rare and,
            # when it happens, it is reported rather than resolved by guessing.
            self._set(job, step="the push raced another landing - rebasing once")
            rc, o2, e2 = self._wt("rebase", "-q", "%s/%s" % (self.remote, self.branch))
            if rc:
                self._wt("rebase", "--abort")
                raise LandingFailed("the push raced another commit and the rebase "
                                    "conflicted. Saved on disk; not landed.",
                                    (o2 + "\n" + e2).strip())
            # ⛔ RE-CHECK AGAINST WHAT WAS JUST REBASED ONTO. The check above ran before the
            # push; the commit that raced it can have added a differently-spelled rewrite of
            # the same draft, or a new version of this one, and a clean rebase says nothing
            # about either. Security review, 2026-09-23: without this, main ended up with two
            # rewrites of one draft.
            self._set(job, step="the push raced another landing - re-checking the remote")
            try:
                (self._check_approve if approve else self._check_against_remote)(
                    job, ref="%s/%s" % (self.remote, self.branch))
            except LandingFailed as ex:
                raise LandingFailed("NOT landed: the push raced another commit, and after "
                                    "rebasing onto it: " + ex.reason, ex.detail)
        if not pushed:
            raise LandingFailed("the push was refused. Saved on disk; not landed.", detail)

        rc, sha, _ = self._wt("rev-parse", "HEAD")
        sha = sha.strip()
        # ⛔ A PUSH THAT EXITED 0 IS NOT A RECEIPT. Fetch the branch back and prove the commit
        # is on it - the same standard the workspace holds an HTTP 200 to.
        self._set(job, step="confirming the commit is on %s/%s" % (self.remote, self.branch))
        self._wt("fetch", "-q", self.remote, self.branch)
        rc, _, _ = self._wt("merge-base", "--is-ancestor", sha,
                            "%s/%s" % (self.remote, self.branch))
        if rc:
            raise LandingFailed("the push reported success but %s is not on %s/%s when "
                                "fetched back" % (sha[:9], self.remote, self.branch))
        return sha, ""


# ==========================================================================================
# SEARCH: ids, titles and text (the human, 2026-10-21)
#
# "In the OrchDoc Reader, I'm only able to search ID's and Titles. I just tried to search for a
# specific script using its ID - SC-Sl-N-02-005 - but nothing came up." The Scripts rail was
# the only place a script could be found, and it matched file names only. This is the server
# half of the fix: `norm_term`/`find_term` are the one definition of how a term matches (used for
# entries, rules and scripts alike), and `ScriptTextIndex` holds the lower-cased text of every
# script file so a search over 10 MB of text costs milliseconds.
# ==========================================================================================

#: Marks around the matched part of a snippet. The page escapes the snippet FIRST and only then
#: turns these two control characters into <mark>, so a script that quotes a <script> tag stays text.
MARK_ON, MARK_OFF = "\x01", "\x02"
_DASH = str.maketrans({"-": " ", "_": " "})
SCRIPT_ID_RE = re.compile(
    r"(?<![A-Za-z0-9])((?:SC|AT)-[A-Za-z]+-[A-Za-z]-\d+-\d+)(?![A-Za-z0-9])")
SEARCH_MIN = 2


def norm_term(term):
    """A typed term as it is compared: lower case, `-` and `_` read as a space, runs of white
    space as one. So `SC-Sl-N-02-005` and `sc sl n 02 005` are the same term."""
    return re.sub(r"\s+", " ", (term or "").lower().translate(_DASH)).strip()


def norm_hay(text):
    """`text` lower-cased with `-` and `_` as spaces: the SAME LENGTH as `text` (one character
    for one), so a position found here is the position in the original, for the snippet."""
    # o9L1441: a character whose lower case is longer (U+0130 is two) is kept as it is, so the
    # lengths still agree; that per-character pass runs only for such a text.
    text = text or ""
    lc = text.lower()
    if len(lc) != len(text):
        lc = "".join(c if len(c.lower()) != 1 else c.lower() for c in text)
    return lc.translate(_DASH)


def and_words(raw):
    """The words that must ALL be present when the whole phrase is not: the words of a term
    typed with spaces, three letters or more, and only when there are two or more of them.
    An id typed with hyphens (`SC-Sl-N-02-005`) or a short word gets none, so `sl` and `n` do not
    drag in every entry that holds those letters."""
    if len((raw or "").split()) < 2:
        return []
    words = [w for w in norm_term(raw).split(" ") if len(w) >= 3]
    return words if len(words) >= 2 else []


def find_term(lc, term, words=()):
    """(position, phrase) of `term` (already `norm_term`ed) in `lc` (a `norm_hay`), or None.

    A phrase hit is the whole term as typed. Failing that, when `words` (see `and_words`) are all
    present anywhere it is a hit too (phrase False), placed at the first of them."""
    at = lc.find(term)
    if at >= 0:
        return at, True
    if not words:
        return None
    pos = []
    for w in words:
        p = lc.find(w)
        if p < 0:
            return None
        pos.append(p)
    return min(pos), False


def make_snippet(raw, at, n, before=60, after=80):
    """~140 characters of `raw` around the match at `at` (length `n`), the match marked."""
    a = max(0, at - before)
    tail = raw[at + n:at + n + after].rstrip()
    return (("..." if a else "") + raw[a:at].lstrip() + MARK_ON + raw[at:at + n] + MARK_OFF
            + tail + "...")


def script_id_of(*texts):
    """The SC/AT id (`SC-Sl-N-02-005`) named in the first of `texts` that holds one, or ''."""
    for t in texts:
        m = SCRIPT_ID_RE.search(t or "")
        if m:
            return m.group(1)
    return ""


class ScriptTextIndex:
    """The text of every script file, lower-cased, for the search box.

    ⭐ BUILT IN THE BACKGROUND, NEVER ON A KEYSTROKE. `refresh()` starts a thread that walks the
    corpus (the listing the Scripts view uses) and reads each file once; a file is read again only
    when its mtime or size changed (`_files` is keyed by the path under the root and holds the
    stamp). `search()` answers from what is ready and says `loading` while the first build has not
    finished, the way `/done` does. It also starts a new refresh when the last one is older than
    `TTL` seconds, so a file written by another session turns up within a few seconds without
    anything being rebuilt per keystroke.

    Every version is searched (v1, v2, `(in app)`): the human searches by a phrase he remembers, not by
    which version held it. The library header (`## Key: value` lines) is not part of the text."""

    TTL = 10.0

    def __init__(self, corpus):
        self.corpus = corpus
        self._files = {}                     # relroot -> record
        self._lock = threading.Lock()
        self._thread = None
        self._ready = False
        self._built = 0.0

    # ---- building ------------------------------------------------------------------------

    def refresh(self, wait=False):
        """Start a background rebuild (unless one is running). `wait` joins it: for tests."""
        with self._lock:
            t = self._thread
            if t is None or not t.is_alive():
                t = threading.Thread(target=self._build, name="script-text-index", daemon=True)
                self._thread = t
                t.start()
        if wait:
            t.join()
        return t

    def _build(self):
        try:
            listing = self.corpus.discover()
            old = self._files
            new = {}
            for fo in listing.get("folders", []):
                for f in fo["files"]:
                    p, rel = f["path"], f["relroot"]
                    try:
                        st = p.stat()
                    except OSError:
                        continue
                    stamp = (st.st_mtime_ns, st.st_size)
                    prev = old.get(rel)
                    if prev and prev["stamp"] == stamp:
                        prev.update(name=f["name"], folder=f["folder"], in_app=f["in_app"],
                                    ver=f["ver"] or 0, mtime=st.st_mtime)
                        new[rel] = prev
                        continue
                    try:
                        with open(p, "rb") as fh:
                            raw = fh.read().decode("utf-8", "replace")
                    except OSError:
                        continue
                    txt = re.sub(r"\s+", " ", strip_library_header(raw)).strip()
                    new[rel] = {"stamp": stamp, "rel": rel, "name": f["name"],
                                "folder": f["folder"], "in_app": bool(f["in_app"]),
                                "ver": f["ver"] or 0, "mtime": st.st_mtime, "txt": txt,
                                "lc": norm_hay(txt)}
            self._files = new
            self._ready = True
        except Exception:
            pass
        finally:
            self._built = time.monotonic()

    def ready(self):
        return self._ready

    # ---- searching -----------------------------------------------------------------------

    def search(self, term, limit=60):
        """({"hits": [...], "total": n}, loading). A hit:
        {k:"script", sid, rel, t, f, id, v, rank, s, ex}. `ex` is true for an exact
        match (rank 0-2, or the whole phrase in the text), false for all-the-words only. `sid` is the id the Scripts view issues
        (`Corpus.sid`): the page never sends a path it could open by."""
        if not self._ready or time.monotonic() - self._built > self.TTL:
            self.refresh()
        loading = not self._ready
        t = norm_term(term)
        if len(t) < SEARCH_MIN:
            return {"hits": [], "total": 0}, loading
        words = and_words(term)
        found = []
        for rel, r in list(self._files.items()):
            sid_ = script_id_of(r["name"], r["folder"])
            idl = norm_hay(sid_)
            folder_l = norm_hay(r["folder"])
            name_l = norm_hay(r["name"])
            snip = None
            phrase = True
            if sid_ and idl == t:
                rank = 0
            elif (sid_ and t in idl) or t in folder_l:
                rank = 1
            elif t in name_l:
                rank = 2
            else:
                got = find_term(r["lc"], t, words)
                if got is not None:
                    rank, phrase = 3, got[1]
                    n = len(t) if got[1] else len(words[0])
                    snip = make_snippet(r["txt"], got[0], n)
                elif words and all(w in folder_l or w in name_l or w in r["lc"]
                                   for w in words):
                    rank, phrase = 3, False
                else:
                    continue
            if snip is None:
                at = r["lc"].find(t)
                snip = (make_snippet(r["txt"], at, len(t), 0, 140) if at >= 0
                        else (r["txt"][:140] + "..."))
            found.append(({"k": "script", "sid": self.corpus.sid(rel), "rel": rel,
                           "t": r["name"][:-3] if r["name"].endswith(".md") else r["name"],
                           "f": r["folder"], "id": sid_,
                           "v": "in app" if r["in_app"] else ("v%s" % r["ver"] if r["ver"] else ""),
                           "rank": rank, "s": snip, "ex": bool(rank < 3 or phrase)},
                          (rank, 0 if phrase else 1, 0 if r["in_app"] else 1,
                           -(r["ver"] or 0), -r["mtime"], rel)))
        found.sort(key=lambda x: x[1])
        return {"hits": [h for h, _ in found[:limit]], "total": len(found)}, loading


def main(argv=None):
    """`python reader_scripts.py link <path>`: print the Reader link of a script file or folder.

    Exit 0 with the URL on stdout; exit 1 with the reason on stderr when the path is not a
    script the Reader lists."""
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if len(argv) < 2 or argv[0] != "link":
        sys.stderr.write("usage: python reader_scripts.py link <path to a script-library file "
                         "or script folder>\n")
        return 2
    url, why = reader_link(" ".join(argv[1:]))
    if not url:
        sys.stderr.write("no Reader link: %s\n" % why)
        return 1
    print(url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
