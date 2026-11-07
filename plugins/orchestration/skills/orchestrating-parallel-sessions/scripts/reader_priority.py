"""
<!-- subject: orchestration -->

The six choices and their classes are in `priority.py` (NOW A, High B, Medium / Low / Hold for
Review C, Don't do D). This module is the write half: `apply_priority` changes the lines of a
document and is called by `orchdoc.apply_today` (act "priority"), so the Reader's change goes
through the same queue, lock, landing and replay on main as a Do-today change.

  * The choice is written as `**Priority:** <choice>` directly under the entry's Status line,
    replacing any earlier Priority line (the human's choice replaces an orchestrator's proposal).
  * Don't do also moves the entry to its own subsection of §99, named Don't do, so it never
    runs. The item keeps its words and the `**Priority:** Don't do` line.
"""
import re

import orchdoc
import priority
from priority import CHOICES, CHOICE_CLASS, DONT_DO, choice_norm

DONTDO_NOTE = "_items the human marked Don't do - kept for the record, never run_"
ARCHIVE_HEAD = "## §99 ARCHIVE"


def dontdo_span(lines):
    """The span of the §99.<n> Don't do subsection, made (after the last §99.x) when absent."""
    span = orchdoc._findings_span(lines, DONT_DO)
    if span is not None:
        return span
    if not orchdoc._has_archive_section(lines):
        lines += ["", ARCHIVE_HEAD, ""]
    return orchdoc._add_findings_section(lines, DONT_DO, DONTDO_NOTE)


def apply_priority(lines, eid, choice):
    """Set `choice` on live entry `eid` in `lines` (changed in place). -> None, or why not."""
    choice = choice_norm(choice)
    if choice is None:
        return "that is not one of: " + ", ".join(CHOICES)
    ents = [e for e in orchdoc.parse_entries(lines)[0]
            if e["id"] == eid and not e.get("archived")]
    if len(ents) != 1:
        return "%s is not a live entry here" % eid
    e = ents[0]
    lo, hi = e["line"] - 1, orchdoc._entry_end(e)
    si = next((i for i in range(lo, hi) if lines[i].startswith("**Status:**")), None)
    if si is None:
        return "%s has no **Status:** line to attach the priority to" % eid
    new = "**Priority:** " + choice
    pi = next((i for i in range(si + 1, hi) if orchdoc.PRIORITY_LINE_RE.match(lines[i])), None)
    if pi is not None:
        lines[pi] = new
    else:
        lines.insert(si + 1, new)
    if choice == DONT_DO:
        return orchdoc._move_to_completed(lines, eid, orchdoc.WITHDRAWN_STATUS, DONT_DO,
                                          dest=dontdo_span)
    return None


def priority_in(text, eid, choice):
    """True when `text` shows `choice` on `eid`: live for any choice but Don't do, which must
    be archived (in the document, outside its live sections)."""
    hit = [e for e in orchdoc.parse_entries(text.split("\n"))[0] if e["id"] == eid]
    if not hit:
        return False
    live = [e for e in hit if not e.get("archived")]
    if choice == DONT_DO:
        return any(priority.choice_of(e["body"]) == DONT_DO for e in hit if e.get("archived")) \
            and not live
    return bool(live) and priority.choice_of(live[0]["body"]) == choice
