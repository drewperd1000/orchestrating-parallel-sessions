"""
<!-- subject: orchestration -->

A click runs `orchdoc.py resolve --queue-landing --json` (no model call anywhere). That is the
same edit `resolve` makes (`orchdoc.apply_resolve`), and the Reader's landing queue lands it, as
it lands a note. This module is the part the Reader and the queue share:

THE ENTRY LEAVES THE LIVE SECTION, as `orchdoc.py archive` would move it. A terminal entry left
in §2 or §3 is E-DONEINACTIVE, which blocks the next `check`; `commit` runs `archive` on the
owner's own landing, but the Reader's landing does not, so the same move is made here, with the
generated plate and index brought up to date as `archive` does. An entry with an unfinished
sub-item stays where it is (the rule, the same hold `archive` applies).
"""
import difflib
import os
import re
from datetime import datetime

import orchdoc

#: What each button writes. The vocabulary is orchdoc.VALID_STATUS; terminal ones are
#: orchdoc.TERMINAL_STATUS.
STATUS_FOR = {"done": "DONE", "superseded": "SUPERSEDED"}
LABEL_FOR = {"done": "Done", "superseded": "Superseded"}
#: The entry kinds that get the buttons: decisions, questions, to-dos and work items.
KINDS = ("D", "Q", "T", "W")
#: Statuses of an entry that is still open. Anything else (a terminal status, or a finding's
#: CONFIRMED) gets no buttons.
OFFER_STATUS = {"OPEN", "BLOCKED", "PAUSED", "DEFERRED", "IN PROGRESS", "IN-PROGRESS",
                "INPROGRESS", "SEQUENCED"}
DEPENDS = "the human (Reader)"
WHO = "the human (Reader)"
REASON_MAX = 200


def offer(it):
    """True when the entry `it` (a Reader model entry) gets the Done and Superseded buttons."""
    if it.get("archived") or it.get("today"):
        return False
    if (it.get("kind") or "") not in KINDS:
        return False
    return (it.get("status") or "").upper() in OFFER_STATUS


def clean_reason(reason):
    """The reason as one short plain line, or ''."""
    if not isinstance(reason, str):
        return ""
    return re.sub(r"\s+", " ", reason).strip()[:REASON_MAX]


def ruling_text(choice, reason="", now=None):
    """The line a mark writes: who marked it, the time and the reason. The time is this
    computer's local time, never UTC."""
    now = now or datetime.now()
    text = "Marked %s by the human in the OrchDoc Reader at %s." % (LABEL_FOR[choice],
                                                                   now.strftime("%H:%M"))
    reason = clean_reason(reason)
    if reason:
        text += (" Superseded by %s." % reason.rstrip(".")) if choice == "superseded" \
            else (" " + reason)
    return text


def is_own_doc(doc_path):
    """True when the workspace's `.orchdoc-config.json` lists this doc's owner under
    `reader_own_docs`, so a click in the Reader writes it without `--not-mine`. The setting is
    empty by default: every click then carries the flag, which is what the ownership guard asks
    for from a tool that does not know which orchestrator is at the keyboard."""
    own = os.environ.get("ORCHDOC_READER_OWN_DOCS") or orchdoc._config_get("reader_own_docs") or ()
    if isinstance(own, str):
        own = [o.strip() for o in own.split(",")]
    return orchdoc.doc_owner_id(doc_path) in own


def writer_args(doc_path, eid, choice, reason="", now=None, own_doc=None):
    """The `orchdoc.py` arguments (after the interpreter and script) for one click."""
    if own_doc is None:
        own_doc = is_own_doc(doc_path)
    args = ["resolve", eid, "--doc", str(doc_path), "--status", STATUS_FOR[choice],
            "--ruling", ruling_text(choice, reason, now), "--depends", DEPENDS]
    if not own_doc:
        args.append("--not-mine")
    return args


# ------------------------------------------------------------------------------------------
# THE CHANGE ON A LIST OF LINES
# ------------------------------------------------------------------------------------------
def _entries(lines):
    return orchdoc.parse_entries(lines)[0]


def _find(lines, eid, live_only=False):
    es = [e for e in _entries(lines) if e["id"] == eid and not (live_only and e.get("archived"))]
    return es[0] if len(es) == 1 else None


def _block(lines, e):
    s, t = e["line"] - 1, orchdoc._entry_end(e)
    return s, t, lines[s:t]


def _strip_tail(blk):
    blk = list(blk)
    while blk and not blk[-1].strip():
        blk.pop()
    return blk


def _section_num(lines, e):
    """The number of the `## §N` section the entry sits in, or None."""
    for i in range(e["line"] - 2, -1, -1):
        if re.match(r"^##\s", lines[i]):
            m = orchdoc.SECTION_RE.match(lines[i])
            return m.group(1) if m else None
    return None


def _regen_derived(old_lines, new_lines):
    """The generated plate and section index, brought up to date the way `archive` does it:
    only when they matched their own regeneration before this change."""
    pspan, pwhy = orchdoc.plate_span(old_lines)
    if not pwhy and pspan:
        was = old_lines[pspan[0]:pspan[1] + 1]
        if was == orchdoc.build_plate_block(orchdoc.parse_entries(old_lines)[0], old_lines):
            nspan, nwhy = orchdoc.plate_span(new_lines)
            if not nwhy and nspan:
                new_lines[nspan[0]:nspan[1] + 1] = orchdoc.build_plate_block(
                    orchdoc.parse_entries(new_lines)[0], new_lines)
    ispan, iwhy = orchdoc.index_span(old_lines)
    if not iwhy and ispan and old_lines[ispan[0]:ispan[1] + 1] == orchdoc.render_index(old_lines):
        nspan, nwhy = orchdoc.index_span(new_lines)
        if not nwhy and nspan:
            new_lines[nspan[0]:nspan[1] + 1] = orchdoc.render_index(new_lines)


def apply_status(lines, op):
    """One Done / Superseded change, or its undo, made on `lines` (changed in place).
    -> None, or why it could not be made.

    Do:   op = {"act": "status", "eid", "status", "ruling", "depends", "stamp", "who"}
    Undo: op = {"act": "status", "eid", "undo": {"hunks", "moved", "num", "prev"}}
    """
    eid = op.get("eid")
    old = list(lines)
    if op.get("undo"):
        why = _undo(lines, eid, op["undo"])
    else:
        why = _do(lines, eid, op)
    if why:
        lines[:] = old
        return why
    if lines != old:
        _regen_derived(old, lines)
    return None


def _do(lines, eid, op):
    status, stamp = op.get("status"), op.get("stamp")
    e = _find(lines, eid)
    if e is None:
        return "%s is not an entry here" % eid
    cur = orchdoc.status_of(e["body"])
    if e.get("archived") or (cur or "").upper() in orchdoc.TERMINAL_STATUS:
        # The same click landed before (a retry of a job whose answer was lost): nothing to do.
        if stamp and ("**Resolved %s:**" % stamp) in e["body"]:
            return None
        return "%s is already %s here, so it was not changed again" % (
            eid, (cur or "closed").upper())
    why = orchdoc.apply_resolve(lines, eid, status, op.get("ruling") or "", op.get("depends"),
                                False, op.get("who") or WHO, stamp)
    if why:
        return why.replace("[REFUSE] ", "")
    ents = _entries(lines)
    e = _find(lines, eid, live_only=True)
    if e is None:
        return "%s could not be found again after the change" % eid
    if orchdoc.has_open_subitems(e["body"], orchdoc.follows_stopsign_convention(ents)):
        return None                       # stays where it is: see the module note
    return orchdoc._move_to_completed(lines, eid, status, "")


def _undo(lines, eid, u):
    e = _find(lines, eid)
    if e is None:
        return "%s is not an entry here" % eid
    s, t, blk = _block(lines, e)
    tail = len(blk) - len(_strip_tail(blk))
    blk = _strip_tail(blk)
    for h in u.get("hunks") or ():
        new, oldl = list(h["new"]), list(h["old"])
        at = next((i for i in range(len(blk) - len(new) + 1) if blk[i:i + len(new)] == new), None)
        if at is None or not new:
            return ("%s was edited after it was marked, so the mark cannot be taken back "
                    "automatically" % eid)
        blk[at:at + len(new)] = oldl
    if u.get("moved"):
        del lines[s:t]
        if s >= len(lines):               # it was the last thing in the file: no blank left behind
            while lines and not lines[-1].strip():
                lines.pop()
        span =orchdoc._section_span(lines, num=u.get("num"))
        if span is None:
            return "the section %s that %s came from is not in this document" % (u.get("num"), eid)
        orchdoc._insert_in_section(lines, span, eid, blk)
    else:
        lines[s:t] = blk + [""] * tail
    back = _find(lines, eid, live_only=True)
    if back is None or (orchdoc.status_of(back["body"]) or "").upper() != (u.get("prev") or ""):
        return "%s did not return to %s" % (eid, u.get("prev"))
    return None


def make_undo(pre_lines, post_lines, eid):
    """What `apply_status` needs to take a change back, from the text before and after it.
    -> a JSON-able dict, or None when this change cannot be taken back cleanly."""
    e0, e1 = _find(pre_lines, eid, live_only=True), _find(post_lines, eid)
    if e0 is None or e1 is None:
        return None
    a = _strip_tail(_block(pre_lines, e0)[2])
    b = _strip_tail(_block(post_lines, e1)[2])
    hunks = []
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if j1 == j2:
            return None                   # a pure deletion has no place to be put back
        hunks.append({"old": a[i1:i2], "new": b[j1:j2]})
    return {"hunks": hunks, "moved": bool(e1.get("archived")),
            "num": _section_num(pre_lines, e0),
            "prev": (orchdoc.status_of(e0["body"]) or "").upper()}
