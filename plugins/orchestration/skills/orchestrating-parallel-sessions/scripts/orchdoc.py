#!/usr/bin/env python3
"""
orchdoc.py - the deterministic gate for orchestrator decision docs.

<!-- route-tags: settled sub-item checkbox strike grey struck entry plate archive orchdoc decision doc lint -->

WHY THIS EXISTS
---------------
Prose did not work. The SURFACE-RECORD-POINT rule is explicit, global, loaded by every
session, and reinforced by the human - and OrchDocs still go stale. A Stop-hook REMINDER was
built on 2026-07-29 (`orchdoc_stop_check.py`) and the problem persisted, because a
reminder targets the motivation to record while the real barriers are that recording is
expensive, undefined, and its omission is invisible at the moment it happens.

So this is not a reminder and not a document. It is a GATE plus a GENERATOR:

  - GATE      `check` exits NON-ZERO on any violated invariant. It does not advise.
  - GENERATOR `plate` rewrites the the human-facing index FROM the entries, so the index can
              never disagree with them. A hand-maintained index is a second copy of the
              truth, and the second copy is always the one that rots.

THE DESIGN SPLIT (from o8L67's diagnostics, principle P7)
--------------------------------------------------------
Mechanised here, impossible to get wrong: status, owner, location, references, the
derived indexes, freshness of the reader's view.
NOT mechanised, kept as free prose in the entry body: WHY a decision went the way it
did, what was rejected, the human's verbatim taste rulings, tradeoffs. Determinism must not
be bought by deleting the reasoning that stops a settled question being re-litigated.

INVARIANTS - each one traces to an observed failure, not a preference
--------------------------------------------------------------------
  E-DUPID    An entry ID appears in more than one entry.
             Seen: o7 D14/D16/D17, o8 DA12, o1 D-PAUSE. Resolution was done by appending
             a second heading, so one ID carries two contradictory statuses at once.
  E-SELFCLAIM A section heading asserts a property of its own contents ("none open",
             "ACTIVE only"). Unmaintainable by construction: other sessions write to the
             section. This is the literal "None open while items are open" failure.
  E-LINECITE A citation targets a line number. Rots on any insertion above it.
  E-SHACITE  A citation targets a bare commit SHA. Rots on rebase, and answers the wrong
             question after a squash-merge. Cite the commit SUBJECT instead.
  E-NOSTATUS A decision entry has no machine-readable Status field.
  E-STALE    The working-tree doc differs from the canonical ref. `freshness` only.

EXIT CODES
----------
  0  clean
  1  one or more invariants violated (the gate refuses)
  2  usage / IO error

Windows: ASCII-only output by rule. stdio is reconfigured defensively.
"""

import argparse
import json
import os
import pathlib
import re
import time
import datetime as _dt
import shutil
import subprocess
import tempfile
import sys
from collections import defaultdict
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

def _find_workspace():
    """The workspace root, DISCOVERED. A hardcoded root is not portable to its own author.

    Marker: a directory containing `.shared/scripts` or any `ORCHESTRATOR-DECISIONS-*.md`.
    Both are definitional - a workspace without either is not one this tool can serve.
    """
    env = os.environ.get("ORCHDOC_WORKSPACE")
    if env:
        # An EXPLICIT override that is wrong is an ERROR, never a suggestion. This used to
        # fall through silently when the path did not exist, so the caller pointed the tool
        # somewhere deliberately and it quietly used a different directory instead.
        if not Path(env).is_dir():
            sys.stderr.write(
                "[orchdoc] ORCHDOC_WORKSPACE is set to a path that is not a directory:\n"
                "          %s\n"
                "          Refusing to guess a different workspace.\n" % env)
            raise SystemExit(2)
        return Path(env)

    def _walk_up(start):
        cur = Path(start).resolve()
        for cand in [cur] + list(cur.parents):
            if (cand / ".shared" / "scripts").is_dir():
                return cand
            try:
                if any(cand.glob("ORCHESTRATOR-DECISIONS-*.md")):
                    return cand
            except (OSError, UnicodeDecodeError):
                pass
        return None

    for start in (Path.cwd(), Path(__file__).resolve().parent):
        hit = _walk_up(start)
        if hit:
            return hit

    # Historical default, and it is now GATED ON CWD. It used to apply from anywhere, so
    # running the tool from an unrelated scratch directory silently resolved to this
    # workspace - and `scaffold --doc o1` / `add --doc o1` then migrated and wrote into a
    # LIVE OrchDoc that was never the target. That happened twice while testing, cost two
    # surgical recoveries, and the second one nearly destroyed another orchestrator's
    # uncommitted work.
    #
    # ⭐ A fallback that fires when discovery FAILS is a convenience. A fallback that
    # redirects a WRITE to an unrelated real directory is a hazard, because the caller
    # believes they are working somewhere else. Only honour it from inside its own tree.
    legacy = Path(r"<your-workspace>")
    try:
        if legacy.is_dir() and Path.cwd().resolve().is_relative_to(legacy.resolve()):
            return legacy
    except (AttributeError, OSError):
        pass
    return Path.cwd()


PROJECTS = _find_workspace()
# The ref that IS the truth. Overridable, because "origin/main" is an assumption, not a
# fact: a repo whose default branch is `master` (this tool's own published repo, as it
# happens) gets wrong answers from every freshness and verify oracle, and it was the
# condition that made touches_since crash. Auto-detect the remote HEAD, then fall back.
def _canonical_ref():
    env = os.environ.get("ORCHDOC_REF")
    if env:
        return env
    try:
        p = subprocess.run(["git", "symbolic-ref", "--quiet", "--short",
                            "refs/remotes/origin/HEAD"],
                           capture_output=True, text=True, timeout=10)
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout.strip()          # e.g. "origin/master"
    except Exception:
        pass
    return "origin/main"


CANONICAL_REF = _canonical_ref()

# Every repo an OrchDoc legitimately cites. A SHA is only "dead" if NO repo has it.
#
# This list is load-bearing. The first version of this checker resolved SHAs against
# the primary workspace alone and reported 54 dead pointers; all of the ones sampled resolved
# fine in a sibling repo, because orchestrators routinely cite cross-repo commits
# ("<private-repo> b516cc2"). A checker that cries wolf is worse than no checker -
# it teaches everyone to ignore it, which is how the previous mechanism died.
# Repos that live outside the workspace tree and cannot be discovered by walking it.
_EXTERNAL_REPOS = [
    Path(r"~/.claude/plugins/marketplaces/<your-marketplace>"),
    Path(r"~/.claude/projects/<project>/memory"),
]


def _main_worktree():
    """The MAIN working tree, whichever worktree is calling.

    Sibling repos, the memory clone and everything else an OrchDoc cites live beside the main
    checkout, never inside a worktree - so a worktree that resolves "the workspace" to itself
    sees a smaller world and calls correct citations dead.
    """
    try:
        p = subprocess.run(["git", "worktree", "list", "--porcelain"],
                           capture_output=True, text=True, timeout=15)
        for line in p.stdout.split("\n"):
            if line.startswith("worktree "):
                return Path(line[len("worktree "):].strip())
    except Exception:
        pass
    return None


def citable_repos():
    """
    Every repo an OrchDoc may cite. DISCOVERED, not listed.

    This was a hardcoded list of ten. There are nineteen repos on disk, and the missing
    ones produced false dead-reference reports - `97dada6` is real, in
    `orchestrating-parallel-sessions`, which simply was not in the list. That is the
    SECOND time an incomplete repo set caused this rule to accuse a correct citation;
    the first cost 54 false positives.

    The deeper problem: a hardcoded list is a hand-maintained copy of "which repos
    exist", which is exactly the second-source-of-truth defect this tool refuses in
    everyone else's documents. It rots the moment a repo is added, and nothing notices.
    So it is derived from the filesystem instead.
    """
    # ⛔ SEARCH THE MAIN CHECKOUT TOO, NOT ONLY THE TREE WE ARE STANDING IN. Discovery used to
    # walk PROJECTS alone, which resolves to the CALLING worktree - and a per-orchestrator
    # worktree contains only this repo's tracked files, not the sibling repos that live beside
    # it in the shared checkout. So `97dada6`, real in `orchestrating-parallel-sessions`, was
    # reported as a dead pointer from every worktree and resolved fine from the main tree.
    #
    # ⭐ THIRD TIME AN INCOMPLETE REPO SET HAS MADE THIS RULE ACCUSE A CORRECT CITATION - 54
    # false positives, then the hardcoded list of ten, now the worktree. The first two were
    # about the list being hand-kept; this one is about the ROOT being read off whoever called.
    # A check whose answer depends on which tree runs it is not deterministic, and it fails in
    # the direction that trains people to ignore it.
    roots = [PROJECTS]
    main = _main_worktree()
    if main is not None and main != PROJECTS:
        roots.append(main)
    found = list(roots)
    try:
        for root in roots:
            for g in root.glob("*/.git"):
                found.append(g.parent)
            for g in root.glob("*/*/.git"):
                if "node_modules" not in str(g):
                    found.append(g.parent)
    except Exception:
        pass
    found.extend(r for r in _EXTERNAL_REPOS if (r / ".git").exists())
    seen, out = set(), []
    for r in found:
        if str(r) not in seen:
            seen.add(str(r))
            out.append(r)
    return out

# An entry ID: D1, D21, DA12, F1, S1, W1, Q1, A1, D-PAUSE, D-SC1, B2.
ID_RE = re.compile(r"^([A-Z]{1,3}(?:\d+[a-z]?|-[A-Z][A-Z0-9]*\d*))\b")

# The status field. The LABEL is lenient, the VALUE is strict.
#
# o6 wrote a perfectly clear `· STATUS: open` and the gate still refused, because the
# original pattern demanded bold-label, own-line, ALL-CAPS. o6 could only discover the
# real contract by reading this source. That is the marker-format-is-a-contract trap:
# the machine shape and the natural human shape diverged and nothing told the author.
#
# So: accept `**Status:** OPEN`, `Status: open`, `STATUS: Open`, inline or own-line, and
# normalise. The VALUE stays a single word - a looser value class once swallowed the
# ' - ' separator, captured 'OPEN -', and silently dropped an entry from the generated
# index, which is the worst failure this tool can have.
STATUS_RE = re.compile(
    # The label must be Status ITSELF, not the tail of another word. "**CONTENT STATUS:**
    # all three drafted" harvested "ALL" - a value outside the vocabulary, so the entry
    # silently vanished from every generated view while reading perfectly to a human
    # (o8, DA17). A preceding WORD disqualifies the match; a bullet or line start does not.
    # Two words, optionally, so "IN PROGRESS" parses. It captured ONE token, which harvested
    # `IN` from the status the human personally mandated and failed it as E-BADSTATUS - the value
    # was added to LIVE_STATUS and never to VALID_STATUS, and nobody tried writing one. A
    # vocabulary the parser cannot read is not a vocabulary. The second word is bounded to the
    # same charset and stays optional, so every single-word status parses exactly as before.
    r"(?:^|[·|*-]|(?<![A-Za-z])\s)\*{0,2}status\*{0,2}\s*:\s*\*{0,2}\s*"
    r"([A-Za-z][A-Za-z_]*(?:[ -][A-Z][A-Za-z_]*)?)",
    re.IGNORECASE | re.MULTILINE)

# What a refusal must TELL the author. A check that refuses without naming the shape it
# wants makes the author reverse-engineer the parser, which is the same defect it is
# meant to catch.

# "line 3" after a filename means line 3 OF THAT FILE, not of this OrchDoc - so a citation that
# names another artifact must not be measured against this doc's length. o2 found W-BADLINEREF
# firing on `| R2a | zero-shot, C line 1 |`, an accurate citation into a different document.
#
# The extension list is universal. The rest is NOT. A slug like `widget-` or `chapter B` is ONE
# workspace's artifact naming, and compiling it in means every other workspace inherits a check that is
# blind to its own artifacts while sounding just as confident - o2's cry-wolf failure, shipped
# pre-installed instead of discovered. The publish guard is what surfaced this: a term that
# cannot be published is usually a term that should not have been hardcoded.
#
# Default covers files only, which is the part that is true everywhere. Set
# $ORCHDOC_ARTIFACT_RE to add your own naming.
ARTIFACT_RE = re.compile(
    r"\.(?:ts|tsx|js|jsx|py|md|json|ya?ml|astro|sh|ps1|txt|rs|go|java|rb)\b"
    + (("|" + os.environ["ORCHDOC_ARTIFACT_RE"]) if os.environ.get("ORCHDOC_ARTIFACT_RE")
       else ""),
    re.I)

STATUS_CANONICAL = "**Status:** OPEN - **Owner:** the human - **Opened:** YYYY-MM-DD"

# ---- WHAT A TIMESTAMP CAN HONESTLY WITNESS ----
#
# o9 wrote: "an agent-written timestamp is a claim; a script-written one is a fact."
# o7 corrected it, and the correction inverts the value of the mechanism:
#
#   "A script-written timestamp is a fact about when the SCRIPT RAN. It is not a fact
#    about whether the verification underneath was real. If the audit stamps
#    `verified: <date>` after an agent attested a section, the stamp LAUNDERS an agent's
#    claim into an artifact that looks machine-established. The next reader sees a
#    script-generated timestamp and reasonably trusts it MORE than a hand-written one -
#    which is precisely wrong, because the epistemic weight lives in the attestation,
#    not the clock."
#
# ⛔ It is the only place a shipped mechanism made something LESS checkable by making it
# look MORE official. The word does the damage. A machine can honestly witness THAT an
# attestation occurred and WHEN; it cannot witness that the attestation was TRUE.
#
# So the field names the ATTESTER and what the clock actually saw:
#     **Attested-by:** o9 at 2026-08-06T17:40:00-07:00 - <what changed and why it survives>
# and never "Verified:", which claims something no clock can establish.
ATTEST_CANONICAL = ("**Attested-by:** <agent> at <ISO timestamp> - "
                    "<what changed and why the conclusion survives>")


# The status ENUM. Presence of the field is not enough - the VALUE has to mean something.
#
# o7's D16 carried `**Status:** the human authorized the fix; o1 is building it (...)`. The
# parser captured "THE HUMAN", the gate saw a field and passed, and because "THE HUMAN" is not
# "OPEN" the entry was silently EXCLUDED from the generated plate. That entry read
# "PRO IS UNSELLABLE RIGHT NOW - a Pro buyer pays and never gets access". The single
# most urgent decision in the doc was invisible to the gate while it reported clean.
#
# A field present with an unparseable value is MORE dangerous than a missing one,
# because it looks migrated. Hence two distinct codes.
VALID_STATUS = {
    # needs someone
    "OPEN", "BLOCKED", "PAUSED", "DEFERRED",
    # the human's D5 ruling, 2026-08-07: settled, but the authorised work is unfinished. All three
    # spellings, because he wrote the spaced form and that is what everyone will type.
    "IN PROGRESS", "IN-PROGRESS", "INPROGRESS",
    # closed out
    "RESOLVED", "ANSWERED", "DONE", "SUPERSEDED", "ARCHIVED",
    # informational entries: findings, specimens, records
    "CONFIRMED", "RECORDED", "ADOPTED", "SHIPPED",
}
PLATE_STATUS = {"OPEN", "BLOCKED"}

# the human's ruling on D5, 2026-08-07. Four orchestrators asked for a status meaning "settled, but
# the authorised work is unfinished". He declined to add one and gave a rule instead: mark the
# container IN PROGRESS, strike the finished sub-items, and move nothing to 99 until ALL of them
# are done. No new vocabulary - IN PROGRESS and ~~strikethrough~~ already meant this - and the
# struck sub-items STAY VISIBLE, because seeing where the finished work sits is what lets him
# rule on the rest. We were all solving the writer's labelling problem; he answered the reader's.
LIVE_STATUS = PLATE_STATUS | {"IN PROGRESS", "IN-PROGRESS", "INPROGRESS"}


def follows_stopsign_convention(entries):
    """Does this doc reserve the stop sign for NOT-DONE, per the human's 2026-08-07 vocabulary?

    Measured, not declared: zero stop-signs inside CLOSED entries. A finished entry has no
    outstanding work for the marker to point at, so a stop sign there can only be emphasis -
    which means its presence is proof the doc still uses the glyph for volume.

    ⭐ Why derive it instead of requiring it: a doc that has not converted keeps working, it
    just gets the word-only marker set. Adoption BUYS sensitivity and non-adoption costs
    nothing but sensitivity - never a false positive. A style rule that pays a measurable
    dividend gets adopted; one that only costs effort gets ignored, which is how the glyph got
    diluted in the first place.
    """
    for e in entries:
        if status_of(e["body"]) in TERMINAL_STATUS and "\u26d4" in e["body"]:
            return False
    return True


def has_open_subitems(body, stopsign_means_open=False):
    """Does this entry carry a sub-item that is NOT done?

    Deliberately reuses the same two marker regexes as W-STRIKEDONE and E-MIXEDSTATE. A third
    private definition of "done" would be a third thing to drift - the marker-format-is-a-
    contract lesson, applied inside one file.
    """
    for raw in body.splitlines():
        # Strip code spans AND strikethrough. o7 found that striking a sub-item did not clear
        # the very finding that prescribes striking it: `~~Copy is UNCHANGED pending your
        # call~~` still matched on "pending". Every sub-item worth striking contains exactly
        # this vocabulary, so the prescribed remedy could not satisfy the check and the only
        # way out was rewording the historical record to dodge a regex - an escape hatch that
        # bypasses the check instead of satisfying it.
        # This is NOT a fourth definition of "done" (o7's argument, and it is right):
        # strikethrough is not a MARKER of doneness, it is the RULING's representation of it,
        # so removing it before matching is the same move as removing code spans.
        txt = re.sub(r"~~.*?~~", "", re.sub(r"`[^`]*`", "", raw))
        if not re.match(r"\s*(?:[-*+]|\d+[.)])\s", txt):
            continue                      # only sub-items, not the entry's own fields
        if _OPEN_SUBITEM_RE.search(txt):
            return True
        if stopsign_means_open and "\u26d4" in txt:
            return True
    return False


# NARROWER than NOTDONE_MARK_RE on purpose. That one includes the stop sign, which across these
# eight documents is an EMPHASIS marker - "⛔ **The money bug**", "⛔ **Anchor corrected:**" -
# appearing constantly inside fully-closed entries. Reading it as outstanding work made every
# hit on o7 and o8 false. What survives here are only markers that cannot mean anything else:
# an explicit not-done WORD, or the hourglass. Sharing NOTDONE_MARK_RE would have been the
# tidier code and the wrong check - a marker's meaning depends on the CONTEXT that reads it,
# which is the same lesson as the marker-format contract, one level up.
#
# The boundary is written out rather than borrowed from _WORD_BOUND, which is defined further
# down the file. Reaching forward for it raised NameError at import - and the fleet sweep that
# should have caught that reported "0 hits" for all seven docs, because the counting grep read
# a crashed run as a clean one. Two silent surfaces agreeing on a false picture, again: this
# time the check was dead and the measurement said it was quiet.
_OPEN_SUBITEM_RE = re.compile(
    r"⏳|(?<![A-Za-z])(?:NOT DONE|NOT YET|NOT STARTED|OUTSTANDING|STILL NEED"
    r"|STILL TO|TODO|PENDING|UNBUILT|AWAITING)(?![A-Za-z])", re.I)



def status_of(body):
    """The entry's status, normalised, or None."""
    m = STATUS_RE.search(body)
    if not m:
        return None
    v = re.sub(r"\s+", " ", m.group(1).strip()).upper()
    # One canonical value for the three spellings, so every downstream set-membership test
    # sees the same string. Otherwise "IN-PROGRESS" is live and "IN PROGRESS" is not, which is
    # the marker-format-is-a-contract failure inside a single function.
    return "IN PROGRESS" if v in ("IN-PROGRESS", "INPROGRESS") else v


# ---- PROSE DEPENDENCIES: the mechanism for the part that cannot be mechanised ----
#
# the human's goal: "the deterministic portion FORCES the orchestrator to go through, line by
# line, section by section, and be CERTAIN that ALL content is updated... so that it
# doesn't have to re-derive ITSELF every time a doc goes stale."
#
# o8's implementable form, which is the key move: you cannot check whether reasoning is
# CORRECT. You CAN check whether it has been RE-ATTESTED since the facts beneath it
# moved. So a section declares what it rests on, and when any of those move, the section
# is presumed WRONG until someone walks it - exactly as a check with no fixture is
# presumed dead.
#
# The case this would have caught: o8's AT length verdict read "10 of 12 clear the
# 8-min floor" while a measurement four sections away said 0 of 13. Nothing connected
# the new measurement to the old conclusion.
DEPENDS_RE = re.compile(r"^\s*\*\*Depends:\*\*\s*(.+)$", re.MULTILINE | re.IGNORECASE)
# Lenient like STATUS_RE, and for the same reason: `Reviewed:` sits naturally INLINE
# with the other fields ("**Status:** OPEN - **Owner:** the human - **Reviewed:** 2026-08-01").
# An anchored ^ pattern silently read that as "never reviewed" - the machine shape
# diverging from the human shape, which is the defect o6 caught in the status field.
REVIEWED_RE = re.compile(
    r"\*{0,2}(?:reviewed|attested-by)\*{0,2}\s*:\s*\*{0,2}\s*"
    # \u26d4 THE ACTOR MAY BE HEDGED. `actor_for()` marks an INFERRED id with a trailing `?`,
    # and `?` was not in this class - so `**Attested-by:** o1? at 2026-09-03...` matched the
    # label, failed the actor group, and the date was no longer adjacent. The stamp was
    # present, well-formed and completely invisible to its only reader. Measured on o1's live
    # entry, which reported August while carrying a September attestation.
    r"(?:[A-Za-z0-9_?-]+\s+at\s+)?"
    r"(\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:?\d{2}|Z)?)?)"
    # ⛔ CAPTURE TO END OF LINE, not to the first `*`. The old `[^\n*]*` stopped dead at any
    # markdown emphasis, so an attestation that QUOTED what it checked - *"§2.1 EMPTY"* - was
    # truncated to the fragment before the quote, fell under MIN_ATTESTATION_CHARS, and was
    # reported as a rubber stamp.
    #
    # ⭐ That is backwards in the most damaging direction available: it penalised the detailed
    # attestations and passed the bare ones, while telling their authors to add detail. A check
    # that punishes exactly the behaviour it is asking for does not just miss - it teaches the
    # wrong lesson, and the author has no way to see why. Both hits on this doc were false.
    #
    # A following bold field is trimmed in reviewed_of(), which is where that belongs.
    r"\s*[-:]?\s*([^\n]*)",
    re.IGNORECASE)

# Just the LABEL and enough to know a stamp starts here. reviewed_of re-matches the full
# REVIEWED_RE from each of these offsets, because the full pattern's trailing `[^\n]*` makes
# non-overlapping iteration blind to every stamp after the first on a line.
REVIEWED_LABEL_RE = re.compile(r"\*{0,2}(?:reviewed|attested-by)\*{0,2}\s*:", re.IGNORECASE)

# o8's caution, mechanised: "a walk-through requirement that produces a note saying
# 'reviewed, still current' will decay into a rubber stamp within a week. The
# attestation must name WHAT changed and WHY the conclusion survives it."
RUBBER_STAMP_RE = re.compile(
    r"^\W*(still\s+(current|true|valid|accurate|good|fine|ok)|no\s+change[sd]?|"
    r"reviewed|checked|verified|current|unchanged|looks?\s+(good|fine|ok)|"
    r"n/?a|ok|fine|yes|confirmed)\W*$", re.IGNORECASE)
MIN_ATTESTATION_CHARS = 40


# ---- ONE STAMP PER LINE: the plate line is what the human SCANS ----
#
# ⛔ the human, 2026-09-04, on the section this tool writes into: *"There are mountains of
# text inside my §2 which is meant to allow me to quickly (QUICKLY!!) see important
# information and what I need to do/decide/answer."*
#
# `restamp` APPENDED its attestation to the `**Status:**` line, so every review made that
# line longer. Measured on ORCHESTRATOR-DECISIONS-o8.md at origin/main: DA17's plate line
# was 1,396 characters, and three more in §2 ran 311, 337 and 377. The tool whose job
# is to keep the record honest was the mechanism making the record unreadable, once per
# review - so no amount of hand-cleaning could hold, because the next `restamp` re-created
# it. A defect that regenerates itself is not a mess, it is a pump.
#
# ⭐ THE FIX IS NOT TO DROP THE PROSE. A prior `--because` is the record of why an entry
# survived a change, and deleting one is the exact loss `reviewed_of` was rewritten to
# prevent. The prose MOVES: one stamp per line, directly beneath the plate line - the shape
# `resolve` has always written, so every reader already parses it. The plate line keeps a
# bare `**Reviewed:** <date>`, which is the one part of a stamp that answers a question at
# a glance.
STAMP_CLAUSE_RE = re.compile(
    r"\*{0,2}(?:reviewed|attested-by)\*{0,2}\s*:\s*\*{0,2}\s*"
    r"(?:([A-Za-z0-9_?-]+)\s+at\s+)?"
    r"(\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:?\d{2}|Z)?)?)"
    r"\*{0,2}\s*[-:]?\s*",
    re.IGNORECASE)

# A stamp already standing on its own line. BOTH labels, because `split_stamps` preserves
# the label it found rather than inventing an attester for a stamp that never named one.
STAMP_LINE_RE = re.compile(r"^\s*\*\*(?:Attested-by|Reviewed):\*\*", re.IGNORECASE)

# The same cut `reviewed_of` makes: a following bold field belongs to the plate, not to the
# attestation. One pattern, so the writer and the reader cannot drift apart.
NEXT_FIELD_RE = re.compile(r"\s+-\s+\*\*[A-Za-z-]+:\*\*")


def split_stamps(line):
    """(the line without its review stamps, [(date, who, why), ...]).

    Every stamp on the line, in the order written. A stamp's `why` runs to the next stamp
    or to the next bold plate field, whichever comes first, and anything that WAS a plate
    field stays on the plate line - so splitting a line cannot silently drop `**Owner:**`.
    """
    ms = list(STAMP_CLAUSE_RE.finditer(line))
    if not ms:
        return (line.rstrip(), [])
    stamps, keeps = [], []
    for k, m in enumerate(ms):
        tail = line[m.end():ms[k + 1].start() if k + 1 < len(ms) else len(line)]
        cut = NEXT_FIELD_RE.search(tail)
        why, keep = (tail[:cut.start()], tail[cut.start():]) if cut else (tail, "")
        stamps.append((m.group(2), (m.group(1) or "").strip(),
                       why.strip().strip("*").strip(" -").strip()))
        if keep.strip():
            keeps.append(keep.rstrip())
    plate = re.sub(r"[\s-]+$", "", line[:ms[0].start()])
    return ((plate + "".join(keeps)).rstrip(), stamps)


def render_stamp(date, who, why):
    """One stamp, one line. No actor recorded means no actor invented - see `cmd_restamp`,
    which refuses to write an INFERRED id into a durable attestation. Migrating an old
    stamp that names nobody must not quietly answer the question it left open."""
    head = ("**Attested-by:** %s at %s" % (who, date)) if who else ("**Reviewed:** %s" % date)
    return ("%s - %s" % (head, why)) if why else head



def depends_of(body):
    m = DEPENDS_RE.search(body)
    if not m:
        return []
    return [t.strip() for t in re.split(r"[,;]", m.group(1)) if t.strip()]


def _as_stamp(d):
    """
    Normalise a review date for comparison against a git ISO timestamp.

    A BARE DATE IS AMBIGUOUS, and the tie must break toward "possibly stale".
    Normalising to end-of-day (23:59:59) means same-day work never counts as newer -
    the UNDER-firing direction, which is how E-ARCHIVEDMARKER died and which would miss
    o8's actual failure, where a verdict and the measurement contradicting it landed
    hours apart on 2026-08-05.

    So a bare date means start-of-day: any same-day work flags the section. The escape
    is trivial and correct - re-attest with a full timestamp, which `review` writes for
    you. Being asked to re-confirm once too often costs a minute; missing a
    contradicting measurement cost o8 a day of re-derivation.
    """
    return d if len(d) > 10 else d + "T00:00:00+00:00"


def reviewed_of(body):
    """(date, attestation-text) or (None, None).

    The attestation runs to end of line. Where ANOTHER bold field follows on the same line
    (`... - **Owner:** o9`), it is cut there - that field belongs to the plate, not to the
    reasoning. Trailing bold markers are trimmed so a closing `**` cannot pad the length.
    """
    # ⛔ THE LAST STAMP, NOT THE FIRST. `search` took the earliest review field on the entry, so
    # the only way to record a NEW review was to overwrite the previous one - and overwriting is
    # how the previous reviewer's reasoning gets deleted. Two real losses today: "built as
    # orchdoc_view_check.py and wired, replacing the superseded hook per the human's D1 ruling" and
    # "o8L67's diagnostics doc is unchanged" were both erased by a re-review that had nothing to
    # do with them.
    #
    # ⭐ A REVIEW IS A HISTORY, NOT A FIELD. Several stamps on one entry are not a second source
    # of truth - they are successive readings, and the current one is the most recent. Reading
    # the last makes appending the natural move, which is also the move that loses nothing and
    # that gate 1 accepts without an override.
    #
    # ⚠️ By DATE, not by position. Position would be decided by where in the entry someone
    # happened to write a stamp, and the question being asked is "when was this last checked" -
    # so the answer is the newest date, wherever it sits. A tie keeps the later one.
    #
    # ⛔ finditer CANNOT SEE THE SECOND STAMP. REVIEWED_RE's attestation group runs to end of
    # line, so the first match swallows every later stamp on that line and non-overlapping
    # iteration returns exactly one. The first attempt at "take the newest" therefore changed
    # nothing at all and reported the same nine stale entries - a fix that looked applied and
    # was inert. So: locate each stamp by its LABEL, then re-match from that offset.
    ms = []
    for lm in REVIEWED_LABEL_RE.finditer(body):
        mm = REVIEWED_RE.match(body, lm.start())
        if mm:
            ms.append(mm)
    if not ms:
        return (None, None)
    m = max(ms, key=lambda x: (x.group(1), x.start()))
    text = (m.group(2) or "")

    # ⛔ FOLLOW THE WRAP. A plate line long enough to say something useful gets wrapped, and
    # reading only the first physical line truncated W9's attestation to 26 characters - then
    # reported it as saying nothing while three clauses of evidence sat on the next two lines.
    #
    # This is the SAME defect as the `*` truncation above, in its second form, and it fails in
    # the same direction: the longer and more specific the attestation, the more likely it wraps
    # and the more likely it is judged empty. Two of two hits on this doc were false.
    tail = body[m.end():]
    for line in tail.split("\n")[1:]:
        s = line.strip()
        # a blank line, a new heading, or a new plate field ends the paragraph
        if not s or s.startswith("#") or s.startswith("**") or re.match(r"^[-*_]{3,}$", s):
            break
        text += " " + s

    cut = re.search(r"\s+-\s+\*\*[A-Za-z-]+:\*\*", text)
    if cut:
        text = text[:cut.start()]
    return (m.group(1), text.strip().strip("*").strip())


def last_moved(entry):
    """
    When this entry last MOVED, from the dates it carries. Coarse by construction.
    """
    dates = re.findall(r"\b(\d{4}-\d{2}-\d{2})\b", entry["body"])
    return max(dates) if dates else None


# ---- THE PUSH MODEL: work declares what it touches ----
#
# the human's idea, and it is the better half of the mechanism. `Depends:` is a PULL edge -
# the section author must predict, in advance, everything that might later invalidate
# their reasoning. That is expensive and needs foresight, which is why o8 flagged a
# missing edge as invisible.
#
# The PUSH edge inverts it: whoever CHANGES a subject names the section it affects, at
# the moment they have the information and at almost no cost. A commit trailer:
#
#     Touches: D14, F9
#
# Two things fall out for free, and the second one fixes a demonstrated hole:
#   1. It cannot be forgotten by the section author, because it is not their job.
#   2. GIT supplies the timestamp, to the second. The date-only comparison missed
#      SAME-DAY changes entirely - and o8's real failure (an AT verdict at one hour, a
#      contradicting measurement later the same day, 2026-08-05) is exactly that case.
#      o9 "validated against o8's real failure" using dates a day apart. The validation
#      was itself a proxy.
# ⛔ THE TRAILER MUST BE DOC-QUALIFIED: `Touches: o9:D1`, not `Touches: D1`.
#
# Entry ids are a PER-DOC namespace. o1, o7 and o9 all have a D1, and they are different
# decisions. A bare `Touches: D1` therefore names three things at once - which surfaced
# the moment the selftest ran: o9's own real commit carrying `Touches: D1` reached into a
# synthetic fixture and marked an unrelated D1 stale.
#
# That is the session's recurring defect one more time - an identifier published without
# a namespace does not resolve - and it would have been silent in production, quietly
# marking the wrong orchestrator's decision stale.
# ⛔ COLUMN ZERO. A git trailer is unindented by convention; an INDENTED `Touches:` line is a
# commit message QUOTING one while describing it. The `^\s*` this used to carry read the
# sentence "carrying 'Touches: D1' reached into a synthetic fixture and marked an unrelated D1"
# - a commit message about the very defect - as an instance of that defect.
#
# ⭐ Ninth instance of description-vs-instance in this toolchain, and the first inside a commit
# message rather than a file. `mentions.executable_part` exists for exactly this shape in shell
# commands; the same reasoning had never been applied to commit bodies.
TOUCHES_RE = re.compile(r"^Touches:\s*(.+)$", re.MULTILINE | re.IGNORECASE)
TOUCH_TOKEN_RE = re.compile(r"^(?:(o\d+)[:/])?([A-Z]{1,3}(?:\d+[a-z]?|-[A-Z][A-Z0-9]*\d*))$")

# ---- DERIVED EDGES: the section already told us what it rests on ----
#
# o8's improvement, and it removes the human from the common case entirely.
#
# PULL (`Depends:`) demands foresight from the section author. PUSH (`Touches:`) demands
# an action from the person changing the subject - better informed, but STILL an action
# someone can forget, and a forgotten trailer is silent. The invisible gap moved one
# seat over rather than closing.
#
# But a section that CITES an artifact has already declared its dependency. A commit
# touching that artifact IS an edge - no trailer, no foresight, no human action at all.
# o8's D14 verdict cited the recompute doc; the commit that changed that doc would have
# flagged it automatically.
#
# So the split is: DERIVATION covers the common case for free, and `Touches:` becomes
# the manual OVERRIDE for what derivation cannot see - a decision invalidated by
# something it never cites, which is genuinely hard and much rarer. A forgotten trailer
# now degrades to "caught anyway via the citation" instead of to silence.
CITED_PATH_RE = re.compile(
    r"`([A-Za-z0-9_.\-/]+\.(?:md|ts|tsx|js|mjs|py|json|astro|yml|yaml|sql))`"
    r"|\]\(([A-Za-z0-9_.\-/]+\.(?:md|ts|tsx|js|mjs|py|json|astro|yml|yaml|sql))\)")


DONE_WHEN_RE = re.compile(r"^\s*\*{0,2}Done-when:\*{0,2}\s*(.+?)\s*$", re.M | re.I)


OPENED_RE = re.compile(r"\*\*Opened:\*\*\s*(\d{4}-\d{2}-\d{2})")


def opened_of(body):
    """The Opened date, or None. Used to ask "changed SINCE this was raised", not "ever"."""
    m = OPENED_RE.search(body)
    return m.group(1) if m else None


def done_when(body):
    """The declared completion signal for an entry, or None if it never declared one."""
    m = DONE_WHEN_RE.search(body)
    return m.group(1).strip() if m else None


# ⭐ THE HUMAN'S NAME, IN ONE PLACE. It was written literally at four sites - a comparison, a
# docstring and two comments - which made it four facts instead of one and left the publisher
# refusing lines it had no mechanical way to rewrite. A `Done-when:` value naming the human is
# vocabulary this tool defines, so it belongs in a constant like every other token here.
OWNER_TOKEN = "owner"


def done_when_satisfied(cond, opened_iso, root):
    """(satisfied, evidence). ⛔ Only ever True on DEMONSTRABLE satisfaction.

    Unknown forms, unevaluatable conditions and OWNER_TOKEN all return False - silence, not a
    guess. A guard that refuses correct work trains bypass, and a bypassed guard is worth less
    than none (o11, 2026-08-19).
    """
    if not cond:
        return False, ""
    low = cond.strip().lower()
    if low == OWNER_TOKEN:
        return False, "closes on the owner's word - no mechanical signal by declaration"
    if low.startswith("path:"):
        rel = cond.split(":", 1)[1].strip()
        p = pathlib.Path(root) / rel
        if not p.exists():
            return False, "%s does not exist yet" % rel
        rc, out, _e = git(["log", "--format=%cI", "-1"] +
                          (["--since=%s" % opened_iso] if opened_iso else []) + ["--", rel],
                          cwd=root)
        if rc == 0 and out.strip():
            return True, "%s changed at %s, after this was opened" % (rel, out.strip()[:19])
        return False, "%s exists but has not changed since Opened" % rel
    if low.startswith("cmd:"):
        cmd = cond.split(":", 1)[1].strip()
        try:
            p = subprocess.run(cmd, shell=True, cwd=str(root), capture_output=True,
                               text=True, timeout=90)
        except Exception as ex:
            return False, "could not run: %s" % str(ex)[:60]
        if p.returncode == 0:
            return True, "`%s` exits 0" % cmd[:60]
        return False, "`%s` exits %d" % (cmd[:50], p.returncode)
    return False, "unrecognised Done-when form: %r" % cond[:40]


def cited_paths(body):
    """Repo-relative artifacts an entry cites. Each is a dependency it already declared.

    ⭐ A citation may narrow itself: `path#needle` means "only commits touching a line of
    `path` that contains `needle`". Returned as the bare path here; `cited_regions` carries
    the needles. See `commit_touched_region` for why.
    """
    out = set()
    for m in CITED_PATH_RE.finditer(body):
        p = m.group(1) or m.group(2)
        if p and not p.startswith("http") and "/" in p or (p and p.endswith(".md")):
            out.add(p.lstrip("./").split("#", 1)[0])
    return sorted(out)


def cited_regions(body):
    """{path: [needle, ...]} for citations written as `path#needle`."""
    out = {}
    for m in CITED_PATH_RE.finditer(body):
        p = m.group(1) or m.group(2)
        if not p or p.startswith("http") or "#" not in p:
            continue
        base, needle = p.lstrip("./").split("#", 1)
        if needle.strip():
            out.setdefault(base, []).append(needle.strip())
    return out


def paths_changed_since(paths, since_iso):
    """{path: (iso, subject)} for cited artifacts whose last commit postdates the review."""
    out = {}
    for p in paths:
        args = ["log", "-1", "--format=%cI%x1f%s%x1f%H"]
        if since_iso:
            args.append("--since=%s" % since_iso)
        args += [CANONICAL_REF, "--", p]
        rc, blob, _ = git(args)
        if rc != 0 or not blob.strip():
            continue
        parts = blob.strip().split("\x1f")
        if len(parts) >= 2:
            # The SHA rides along so a caller can say WHAT the commit touched, not merely
            # that it happened. Callers unpacking two values still work.
            out[p] = (parts[0].strip(), parts[1], parts[2].strip() if len(parts) > 2 else "")
    return out


def commit_touched_region(sha, path, needle):
    """Did commit `sha` change any line of `path` containing `needle`?

    ⛔ THE TREADMILL THIS EXISTS TO END. E-STALEPROSE's cited-artifact edge is FILE-level:
    ANY commit touching a cited file restales the entry, whatever it changed. Measured
    2026-09-08 - o1 added one row to DEV-DOCS-INDEX.md for an unrelated document and that
    alone restaled o10's W54, which cites the index for a DIFFERENT row. Three entries went
    stale twice in twenty minutes from edits that could not have affected any of them.

    ⭐ THE COST IS NOT THE OVERRIDE, IT IS WHAT THE OVERRIDE TEACHES. An invariant that fires
    on work it cannot be about trains the author to clear it without reading - which is
    exactly the behaviour E-RUBBERSTAMP exists to punish. Two invariants demanding opposite
    things teaches that both are noise. That reasoning is o7's, already in this file above
    `_commit_role`, and this is the same fix on the other edge.

    ⚠️ OPT-IN, AND DELIBERATELY SO. Nothing changes for a plain `path` citation - it stays
    file-level and stays loud. An entry narrows its own claim by writing `path#needle`, and
    then only commits that touch a line containing `needle` count. An entry that over-narrows
    is silencing its OWN alarm, visibly, in its own text.

    Returns True when it cannot tell - an unreadable diff must not silence the check.
    """
    rc, diff, _ = git(["show", "--format=", "--unified=0", sha, "--", path])
    if rc != 0 or not diff:
        return True
    low = needle.lower()
    for ln in diff.split("\n"):
        if ln.startswith(("+++", "---", "@@")):
            continue
        if ln.startswith(("+", "-")) and low in ln[1:].lower():
            return True
    return False


def entries_touched(sha, path):
    """Which entry ids did commit `sha` add or remove headings for, in `path`?

    ⛔ WHY THIS EXISTS. E-STALEPROSE's cited-artifact edge is FILE-level: a cited OrchDoc moved,
    so every entry citing it is suspect. That is the right granularity - narrowing it to
    per-entry would shrink a net whose over-reporting is its virtue - but it means an author
    clears most trips by opening the triggering commit and grepping its diff for their cited
    ids. o1 measured three such trips in one day, all spurious, two minutes each.

    ⭐ The danger is not the false trip. It is the author who learns the trips are usually false
    and stops measuring - o1 nearly did on the second, and o10 applied one override 38
    consecutive times before walking it. Their line: an override used 38 times is not an
    override, it is a silenced check.

    So the cost of clearing comes down instead of the net coming in.

    ⛔ RETURNS None WHEN IT CANNOT TELL; [] ONLY FOR A DEMONSTRATED NEGATIVE. The caller SKIPS
    the finding on [], so the two cases must not share a value - an unreadable diff or an
    unreadable blob would then silence a real staleness trip while looking like proof the trip
    was spurious. `commit_touched_region` already has this right on the other edge: "Returns
    True when it cannot tell." Same rule here, opposite direction. (o9, reviewing as owner
    2026-09-08: the skip shipped reading [] both ways, so it failed OPEN.)
    """
    if not sha:
        return None
    rc, diff, _ = git(["show", "--format=", "--unified=0", sha, "--", path])
    if rc != 0 or not diff:
        return None

    # \u26d4 CHANGED LINES, NOT CHANGED HEADINGS. The first version matched added/removed entry
    # HEADINGS and called the result "entries touched". o1's own case disproved it before it
    # shipped: their commit subject reads "W2 carries the pause-cue constraint now" - it changed
    # W2's BODY - and the helper returned nothing. It would have printed "touched no entry
    # headings" about a commit that touched an entry.
    #
    # \u2b50 A confident "touched nothing" is exactly the sentence that lets an author stop
    # measuring, which is the failure this note exists to prevent. So map the diff's changed
    # line ranges onto the entry regions of the file AS IT WAS at that commit.
    hunks = []
    for ln in diff.split("\n"):
        m = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", ln)
        if m:
            start = int(m.group(1))
            count = int(m.group(2) or "1")
            if count:
                hunks.append((start, start + count - 1))
    if not hunks:
        return None

    rc2, blob, _ = git(["show", "%s:%s" % (sha, path)])
    if rc2 != 0 or not blob:
        return None
    lines = blob.split("\n")
    spans, cur, cur_line = [], None, 0
    _HEAD = re.compile(r"^#{1,6}\s*\S*\s*([A-Z]{1,3}\d+[a-z]?)\b")
    fenced = False
    for i, ln in enumerate(lines, 1):
        # ⛔ A `#` INSIDE A FENCED BLOCK IS NOT A HEADING. An entry quoting a doc's markdown -
        # which OrchDoc entries do constantly - would otherwise open a bogus span, and every
        # changed line after it gets credited to the wrong id. `_reorder_slots` had the same
        # bug and got the same fix earlier today; this scanner was written before that.
        if ln.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        m = _HEAD.match(ln)
        if not m and ln.startswith("#"):
            # ⛔ AN ARCHIVED HEADING CARRIES ITS ID MID-LINE, and missing it credits that
            # entry's whole body to the entry ABOVE it. Measured against o1's independent
            # run: a hunk inside a demoted W32 entry came back as W9. `parse_entries` already
            # reads this form; this scanner had its own private regex and did not - one fact,
            # two readers, which is the defect this file has spent the week removing.
            #
            # ⭐ o1's version matched an id ANYWHERE in the heading and returned DA18 - o8's
            # entry, merely NAMED in the title. Neither of us had it right, and only running
            # both showed that.
            m = ARCHIVED_ID_RE.search(ln)
        if m:
            if cur:
                spans.append((cur_line, i - 1, cur))
            cur, cur_line = m.group(1), i
    if cur:
        spans.append((cur_line, len(lines), cur))

    ids = []
    for a, b in hunks:
        for lo, hi, eid in spans:
            if a <= hi and b >= lo and eid not in ids:
                ids.append(eid)
    return ids



def fetch_ok(cwd=PROJECTS, remote="origin"):
    """Fetch, and REPORT whether it worked. Never silently proceed on failure.

    Seven call sites discarded this return code. On failure git leaves the previously
    cached remote-tracking refs in place, so every downstream comparison silently measured
    against a stale snapshot and reported "current" - which is precisely the condition this
    tool was built to detect. An oracle that cannot tell "I checked" from "I could not
    check" is worse than no oracle: the reader stops looking.
    """
    rc, _out, err = git(["fetch", "--quiet", "--prune", remote], cwd=cwd)
    return rc == 0, (err or "").strip().splitlines()[-1] if err else ""


def touches_since(doc_slug, since_iso, rev=None):
    """
    {entry_id: (iso, subject)} for `Touches:` trailers naming THIS doc, landed after
    `since_iso`. Timestamps come from git, never from a claim in the text.

    Returns a second dict of UNQUALIFIED or UNKNOWN-doc tokens so the caller can refuse
    them rather than silently no-op - o7: "a typo becomes an invisible non-update".
    """
    out, bad = {}, {}
    # %H last: the SHA is what lets a caller ask what ROLE the commit played - whether it
    # CREATED the entry it names, or only re-stamped its plate line. Without it the push edge
    # can only see that something touched the entry.
    args = ["log", "--format=%cI%x1f%s%x1f%b%x1f%H%x1e"]
    if since_iso:
        args += ["--since=%s" % since_iso]
    # `rev` lets a caller ask about UNLANDED commits (`origin/main..HEAD`). That distinction is
    # what makes the unqualified-trailer rule enforceable: a commit already on the canonical ref
    # cannot be amended without rewriting shared history, so refusing over it names something
    # nobody can act on.
    args += [rev or CANONICAL_REF]
    rc, blob, _ = git(args)
    if rc != 0 or not blob:
        # BOTH values, always. This path returned a bare `out`, so every caller doing
        # `pushed, bad = touches_since(...)` crashed with "not enough values to unpack"
        # the moment git could not answer - no repo, no `origin/main`, a fresh clone.
        #
        # ⭐ It never fired here because this workspace always has origin/main, so the
        # ONLY reachable branch was the happy one. A fallback path that the author's
        # environment can never enter is untested by construction, and it is exactly
        # where a portability bug hides: the tool worked perfectly for one setup and
        # crashed on `check` for every new user.
        return out, bad
    for rec in blob.split("\x1e"):
        parts = rec.strip().split("\x1f")
        if len(parts) < 3:
            continue
        when, subject, body = parts[0].strip(), parts[1], parts[2]
        sha = parts[3].strip() if len(parts) > 3 else ""
        m = TOUCHES_RE.search(body)
        if not m:
            continue
        for tok in re.split(r"[,;\s]+", m.group(1)):
            tok = tok.strip().rstrip(".,")
            if not tok:
                continue
            mm = TOUCH_TOKEN_RE.match(tok)
            if not mm:
                bad[tok] = (when, subject, "not an entry id")
                continue
            doc_q, eid = mm.group(1), mm.group(2)
            if doc_q is None:
                bad[tok] = (when, subject, "unqualified - say o<N>:%s" % eid)
                continue
            if doc_slug and doc_q != doc_slug:
                continue                      # names a different doc: not ours
            prev = out.get(eid)
            if prev is None or when > prev[0]:
                out[eid] = (when, subject, sha)
    return out, bad

_ROLE_CACHE = {}

# A line that carries no reasoning: the entry heading, the plate line, a review stamp, a
# provenance tag. A commit that changed only these moved status, not substance.
PLATE_ONLY_RE = re.compile(
    r"^(?:#{1,6}\s|\*\*Status:\*\*|\*\*Owner:\*\*|\*\*Depends:\*\*|\*\*Touches:\*\*|"
    # ⛔ A REVIEW STAMP, which the comment above already lists and the pattern did not
    # match. That cost nothing while every stamp lived INLINE on the Status line - the
    # `**Status:**` alternative covered it by accident. `resolve` has written standalone
    # `**Attested-by:**` lines since it learned to attest, and `restamp` now writes them
    # too, so a re-attestation commit stopped counting as plate-only: the push edge read
    # it as work landing on the entry, and E-STALEPROSE flagged the entry as stale
    # BECAUSE it had just been re-attested. That is the cry-wolf loop o7 measured, with
    # the tool generating its own trigger.
    r"\*\*Attested-by:\*\*|\*\*Reviewed:\*\*|"
    r"<!--\s*(?:from|verdict|route-tags|GENERATED)\b|\s*$)")


def _commit_role(sha, doc_name, eid):
    """(created_it, plate_only) for what commit `sha` did to entry `eid` in `doc_name`.

    ⛔ THE PUSH EDGE COULD NOT SEE WHAT A COMMIT DID, only that it named the entry. So the
    commit that CREATED an entry counted as evidence the entry had gone stale - a new entry was
    stale the instant it landed - and so did a commit whose only change was adding a provenance
    tag or flipping a status.

    ⭐ o7 measured three of these in twenty minutes and named the real cost: the rule taught
    them to re-attest WITHOUT READING, which is the behaviour E-RUBBERSTAMP exists to punish.
    Two invariants demanding opposite things teaches that both are noise.
    """
    key = (sha, doc_name, eid)
    if key in _ROLE_CACHE:
        return _ROLE_CACHE[key]
    created = plate_only = False
    rc, diff, _ = git(["show", "--format=", "--unified=0", sha, "--", doc_name])
    if rc == 0 and diff:
        adds = [ln[1:] for ln in diff.split("\n") if ln.startswith("+") and not ln.startswith("+++")]
        dels = [ln[1:] for ln in diff.split("\n") if ln.startswith("-") and not ln.startswith("---")]
        head = re.compile(r"^#{1,6}\s.*\b%s\b" % re.escape(eid))
        created = any(head.match(a.strip()) for a in adds) and \
            not any(head.match(d.strip()) for d in dels)
        touched = [x for x in adds + dels if x.strip()]
        plate_only = bool(touched) and all(PLATE_ONLY_RE.match(x.strip()) for x in touched)
    _ROLE_CACHE[key] = (created, plate_only)
    return created, plate_only


# Leading decoration before the ID: emoji, bold, tick marks, whitespace.
DECORATION_RE = re.compile(r"^[\s*_`~]*(?:[^\w\s*_`~]+[\s]*)*")

SELF_CLAIM_PATTERNS = [
    (re.compile(r"none\s+open", re.I), "claims its own contents are empty"),
    (re.compile(r"\(\s*ACTIVE\s+only\s*\)", re.I), "claims to hold only active items"),
    (re.compile(r"\(\s*was:\s*[^)]*\)", re.I), "carries a vestigial 'was:' label"),
    (re.compile(r"\bactive\s+only\b", re.I), "claims to hold only active items"),
]

# A heading names what a section IS. It must never assert a STATE, a COUNT, or a
# property of its CONTENTS (o8, 2026-08-06). A heading cannot be checked, so anything it
# claims drifts silently; state belongs on entries, where a linter can reach it.
#
# Only the part AFTER a separator counts. That is what distinguishes a section merely
# NAMED for a lifecycle bucket ("## DONE" - a name, fine) from a section whose heading
# makes a claim about itself ("## THE EXIT - BUILT and RUN" - a claim, drifts). o8's
# case had already gone stale once and been corrected, and was about to go stale again.
HEADING_STATE_RE = re.compile(
    # LIVE removed (o1, 2026-08-07). It fired on "## LINKS AND DOCS - 🌐 LIVE URLS", where
    # LIVE NAMES the thing - those ARE the production URLs, and that word cannot drift.
    # ⭐ o1's distinction: a state ADJECTIVE in a heading usually names something; what rots
    # is a dated verification RESULT or a COUNT. The finding on that line was TRUE but on
    # the WRONG TOKEN - the drifting claim was "(all verified HTTP 200, 2026-07-30)",
    # 8 days stale and invisible because headings do not read as claims.
    r"\b(BUILT|RUNNING|SHIPPED|COMPLETED?|RESOLVED|FIXED|MERGED|VERIFIED|PASSING|"
    r"WORKING|READY|STOPPED|BLOCKED|LANDED|APPLIED|FINISHED|UNBUILT|PENDING)\b")

# A dated verification RESULT inside a heading - the class o1 identified, and the expensive
# one: it looks like documentation, it IS a measurement, and it expires silently where
# nobody re-reads. ⛔ Scoping the rule off its state-adjective false positive would have
# cost this whole class, permanently and quietly - which is the more expensive error than
# the false positive that prompted it.
HEADING_DATED_CLAIM_RE = re.compile(
    r"\b(?:verified|checked|measured|confirmed|tested|audited|re-?audited|as of)\b"
    r"[^)\n]{0,40}?\d{4}-\d{2}-\d{2}", re.I)
HEADING_COUNT_RE = re.compile(
    r"\b\d+\s+(open|remaining|left|outstanding|pending|done|items?|entries|decisions?)\b",
    re.I)
HEADING_SPLIT_RE = re.compile(r"\s[-–—]\s|:\s")

LINE_CITE_RE = re.compile(r"\blines?\s+\d{1,5}\b", re.I)
# A SHA cited as a reference. MUST be backtick-quoted, which is the convention in every
# OrchDoc (`a2f70f9`). Matching bare tokens instead produced pure noise: English words
# built only from a-f ("defaced", "effaced") are valid hex, and so is a sha256 content
# hash that o7 explicitly labels as such. Requiring the backticks removes that entire
# false-positive class without missing a single real citation.
SHA_CITE_RE = re.compile(r"`([0-9a-f]{7,40})`")
SHA_HAS_LETTER = re.compile(r"[a-f]")

# Words that mean "this hex string is NOT a commit". o7's case: `ba9ce86c0000be61` is a
# sha256 content-hash prefix, and the sentence containing it says so - it was the
# evidence that two blocks of text were byte-identical during a doc merge. The rule was
# matching on SHAPE (hex, backticked) and inferring KIND, so it reported a dead pointer
# for a pointer that never existed.
#
# o7's argument for why this matters more than a stray warning: a checker that pressures
# people into damaging correct content is worse than one that misses things. Reworded to
# satisfy a wrong check, that line would have become a weaker provenance record with no
# trace of why.
NOT_A_COMMIT_RE = re.compile(
    r"\b(sha-?256|sha-?1\b|md5|blake|digest|checksum|content[- ]hash|hash prefix|"
    r"prefix|fingerprint|etag|content\()", re.I)
EM_DASH = "\u2014"

# Sections whose entries are decisions and therefore must carry a Status field.
DECISION_SECTION_RE = re.compile(r"DECISION", re.I)

# Claim markers, for the mixed-state check. Deliberately narrow: an ASSERTION that
# something is finished, versus an assertion that it is not. Vague words ("progress",
# "soon") are excluded - they carry no claim to contradict.
# Claim markers for the mixed-state check. Word boundaries are LOOKAROUNDS, not
# backslash-b: five heredoc patches in a row ate the escapes, twice leaving literal
# backspace bytes in the pattern so it matched nothing and the check shipped DEAD.
# An expression a transport layer cannot corrupt is worth the extra characters.
_WORD_BOUND = '(?<![A-Za-z])(?:%s)(?![A-Za-z])'
DONE_MARK_RE = re.compile(
    "\u2705|" + _WORD_BOUND % (
        "DONE|PROVEN|RESOLVED|SHIPPED|VERIFIED|COMPLETED?|LANDED"))
# ---- THE SETTLED-SUB-ITEM FORM (the human, 2026-08-10) ----
#
#     - [x] the thing that is settled
#
# THAT IS THE WHOLE FORM. A checked checkbox renders with a green tick, GREY TEXT and
# STRIKE-THROUGH natively - all three of the human's requirements, from four characters.
#
# ⛔ AND THE OBVIOUS IMPLEMENTATION WAS WRONG, WHICH IS WHY THIS COMMENT IS LONG.
#
# The first version of this required `- [x] <span style="color:#8a8a8a">~~...~~</span>`, copied
# from o1's doc where it appeared 17 times. It was reasoned onto solid-looking evidence: these
# docs use <details> folds, folds demonstrably render, therefore the renderer processes HTML.
#
# **It does not process INLINE html.** the human's screenshot shows `<span style="color:#8a8a8a">`
# and `</span>` as VISIBLE LITERAL TEXT in the middle of every one of those lines. Block-level
# <details> is handled; an inline <span> is escaped and printed.
#
# ⭐ The screenshot also settles the tildes, and this is the part reasoning would not have
# reached: the strike-through in it extends ACROSS the visible `<span …>` prefix, which sits
# OUTSIDE the `~~`. So the strike cannot be coming from the tildes - the checkbox is doing it.
# Both the span and the tildes are redundant, and one of them is actively noise.
#
# The lesson is the session's own: a plausible mechanism ("HTML renders here") was inferred from
# a true observation about a DIFFERENT element, and shipped without being looked at. One
# screenshot beat it. Anything about RENDERING has to be seen rendered.
CHECKED_BOX_RE = re.compile(r"^\s*[-*+]\s*\[[xX]\]")
# o11's fix, adopted verbatim. Its absence is why an entry could read as finished while three
# of its six steps were open boxes: the counter had a way to recognise DONE and no way to
# recognise NOT DONE in the same notation.
UNCHECKED_BOX_RE = re.compile(r"^\s*[-*+]\s*\[\s\]")
# Retained only to DETECT and strip the literal-text spans already written into the corpus.
GREY_SPAN_RE = re.compile(r"<span\s+style=\"color:\s*#?[0-9a-fA-F]{3,6}\"\s*>")

NOTDONE_MARK_RE = re.compile(
    "\u23f3|\u26d4|" + _WORD_BOUND % (
        "NEVER|NOT DONE|NOT YET|UNTESTED|UNBUILT|OUTSTANDING"
        "|STILL NEED|TODO|BLOCKED|PENDING"))

# \u26d4 OPENNESS THE DONE/NOT-DONE VOCABULARY MISSES ENTIRELY.
#
# o1's specimen, 2026-08-11: `strike` converted 7 lines on their doc and FIVE were wrong. Every
# one of the five contained a \u2705 or bold text REPORTING PARTIAL PROGRESS - the tick landed on the
# sentence saying the work was not finished:
#
#     - **8th-Pillar title - \u2705 FINAL (the human, 2026-07-30). Remaining: the Google Doc**
#     - **\ud83d\udcd8 8th-Pillar title -> apply in the Google Doc - o1's next step.**
#     - **\ud83d\udcd8 7 Pillars ebook restructure ... awaiting the human's eyeball.**
#
# NOTDONE_MARK_RE knew none of `remaining`, `next step`, `awaiting`, `do LAST`. So a line could
# say "FINAL \u2026 Remaining: the Google Doc" and register as unambiguously finished.
#
# \u2b50 AND A FALSE TICK IS THE EXPENSIVE DIRECTION. A checked box tells the human HE OWES NOTHING THERE.
# It is unfalsifiable from inside the document - the tick looks identical whether earned or
# fabricated, and the sentence beside it still says "next step" in prose nobody re-reads once the
# box is green. This is the "None open while items are open" harm, written by a tool.
#
# These do NOT mean "not done". They mean **AMBIGUOUS - a human decides**, which is a third state
# the tool previously could not express.
AMBIGUOUS_RE = re.compile(
    r"\b(?:remaining|next step|awaiting|await|still (?:shows|needs|open|to)|"
    r"do (?:it )?last|"                       # `do it LAST` slipped a rule written for `do LAST`
    r"tbd|to be (?:done|decided|confirmed)|unless|once .{0,24} lands|after .{0,24} lands|"
    r"waiting on|depends on|partial|in part)\b", re.I)

# ⭐ THE STRUCTURAL SIGNALS, WHICH DO NOT DEPEND ON PHRASING AT ALL. o1's, and they are better
# than the word list above: `do LAST` was added, their line said `do it LAST`, and one word beat
# the rule. **A keyword list loses that race by construction** - there is always another phrasing.
#
# Two facts on a line mean OPEN regardless of how it is worded:
#
#   a TRACKER ID   the line has delegated its status to another system. The document is
#                  therefore NOT the authority on whether it is finished, and a formatter must
#                  not assert completion on the doc's behalf. (Resolvable, too: the id is either
#                  open or closed over there, which turns a guess into a lookup.)
#   a DUE DATE     a due date is a claim about UNFINISHED work. Nothing with a live one is done.
#
# ⛔ AND o1'S SHARPER POINT, which no vocabulary catches: on the line that slipped, the ✅
# modified **"Motion-tracked"** - not the task. The tick was true about a PROPERTY of the item,
# and the formatter read it as being about its COMPLETION. Identical shape to
# `"✅ FINAL … Remaining: the Google Doc"`. A glyph proves nothing about what it is attached to,
# which is the whole reason done-ness cannot be inferred from formatting.
# The fingerprints of a RUNNING HISTORY - text about how the entry got here rather than about
# what is being asked. Deliberately narrow: each of these describes a PAST STATE of the record
# itself, which is the one thing a decision entry never needs to carry.
PLATE_HISTORY_RE = re.compile(
    r"~~|\b(?:superseded|corrected|correction|retracted?|"
    r"was (?:false|wrong|stale|incorrect|inaccurate)|"
    r"used to (?:say|read|claim)|earlier version|previously (?:said|read|claimed)|"
    r"turned out to be (?:false|wrong)|o\d+ (?:was wrong|caught|flagged))\b", re.I)

TRACKER_ID_RE = re.compile(r"\b(?:tk_[A-Za-z0-9]{8,}|[A-Z][A-Z0-9]{1,9}-\d+)\b")
DUE_DATE_RE = re.compile(r"\bdue\b(?!\s+to\b)", re.I)


def looks_open(text):
    """Openness by PHRASE or by STRUCTURE. Structure is the half that survives rewording."""
    return bool(AMBIGUOUS_RE.search(text)
                or TRACKER_ID_RE.search(text)
                or DUE_DATE_RE.search(text))


def why_open(text):
    """The exact token that made it ambiguous, so a refusal can be argued with rather than
    merely obeyed. A gate that will not say WHY gets overridden on reflex."""
    for rx, label in ((AMBIGUOUS_RE, None),
                      (TRACKER_ID_RE, "tracker id"),
                      (DUE_DATE_RE, "a due date")):
        m = rx.search(text)
        if m:
            return label or m.group(0)
    return "?"

# ---- THE HUMAN'S CLARITY REQUIREMENTS (2026-08-06) ----
#
# "Done items are left cluttering up the active list, and/or they are not clearly
#  marked visually." And: walls of text are "hard to parse visually for a human".
#
# Both frustrations were present in EVERY OrchDoc without fail, which makes them
# systemic rather than anyone's lapse - the same bar as the rest of this tool.

# Statuses that mean the item is finished and must not sit in an active list.
TERMINAL_STATUS = {"RESOLVED", "ANSWERED", "DONE", "SUPERSEDED", "ARCHIVED"}

# Section names that PROMISE the reader only live items.
# ANY name, not ONE name. This previously read `ON (?:THE HUMAN|YOUR)'?S? PLATE`, so a doc
# belonging to anyone else - "ON ALICE'S PLATE" - simply did not match, and every check
# that depends on knowing which sections are ACTIVE went silently dead for that doc.
# ⭐ A person's name compiled into detection logic is a check that works for exactly one
# person and fails invisibly for everyone else. Surfaced by asking whether the tool could
# be published, but it was equally a latent bug for a name CHANGE on this machine.
_ACTIVE_NAME_RE = re.compile(
    # Optional possessive lead-in. "YOUR TO-DOs" is the same section as "TO-DOS", but the
    # anchored pattern rejected it - so `archive` silently ignored every doc writing it
    # that way, and the handoff check could not see the items inside. o3's D11 lived under
    # "## 📋 YOUR TO-DOs" and was invisible to both, which is why it survived a
    # consolidation, a dormancy request, and the first version of the check built to
    # catch exactly it.
    # "IN FLIGHT" stays in the alternation: seven live OrchDocs still carry it, and this
    # session does not rewrite another owner's document. Both spellings parse, indefinitely.
    r"^(?:YOUR |MY |THE )?(?:DECISIONS?|TO-?DOS?|IN FLIGHT|ON [A-Z][\w'-]*'?S? PLATE"
    r"|ON YOUR PLATE|QUESTIONS?|OPEN)\b", re.I)


# The one place the archive section number is written down. `archive` moves INTO it and
# is_active_section() refuses to call it live; both must agree, so both read this.
ARCHIVE_SECTION = "99"


_CANON_HEAD_RE = re.compile(r"^#{1,3}\s*§\s*([\d.]+)")


def _governing_section(lines, entry_line):
    """The most recent §N heading at or before this line, ignoring prose headings.

    The section an entry BELONGS to is the last canonical one above it. A prose `##` heading in
    between is a subdivision of that section, not a replacement for it - but the parser records
    whatever heading it saw last, so the enclosing section is lost.

    Returned as the bare number ("2.1"), or None when nothing canonical precedes the entry.
    """
    cur = None
    for i, line in enumerate(lines, 1):
        if i > entry_line:
            break
        m = _CANON_HEAD_RE.match(line)
        if m:
            cur = m.group(1)
    return cur


def is_active_section(title):
    """
    True when a section's NAME promises live items.

    Only the part BEFORE the separator is the name; everything after it describes.
    Matching the description moved o9's SPECIMENS section, titled "SPECIMENS -
    verification failures caught in flight", on the words "in flight".
    """
    if not title:
        return False

    # ⛔ SECTION NUMBER FIRST. Introducing the §-numbered schema SILENTLY DISABLED THIS
    # FUNCTION: every schema heading is "§2.1 Decisions", and the "§2.1 " prefix stopped
    # the name match, so is_active_section() returned False for every live section. The
    # consequence is not cosmetic - `archive` decides what to move by asking this, so on
    # a schema doc NOTHING EVER ARCHIVED and closed items accumulated on the plate
    # forever. That is precisely the clutter the schema was built to fix.
    #
    # ⭐ Same shape as `add` filing into §99: the numbered schema changed what headings
    # LOOK like, and every lookup that matched on their WORDING quietly stopped working.
    # A structural change has to be followed into every reader of that structure.
    m = SECTION_RE.match(title if title.lstrip().startswith("#") else "## " + title)
    if m:
        num = m.group(1)
        top = num.split(".")[0]
        if top == ARCHIVE_SECTION:          # §99.x is the terminal home, never active
            return False
        if top in ("2", "3"):               # the plate and in-flight promise live items
            return True
        return False                        # links, findings, guards: not live lists

    name = HEADING_SPLIT_RE.split(strip_decoration(title), maxsplit=1)[0]
    return bool(_ACTIVE_NAME_RE.match(name.strip()))

# The visual marker a heading must carry, derived from the Status field. One writable
# home for the fact; the marker is generated from it, so a tick can never claim DONE
# while the field says OPEN.
STATUS_MARKER = {
    "OPEN": "\U0001f534", "BLOCKED": "\u26d4", "PAUSED": "\u23f8\ufe0f",
    "DEFERRED": "\u23f3", "RESOLVED": "\u2705", "ANSWERED": "\u2705",
    "DONE": "\u2705", "SUPERSEDED": "\U0001f5c4\ufe0f", "ARCHIVED": "\U0001f5c4\ufe0f",
    "CONFIRMED": "\U0001f50e", "RECORDED": "\U0001f4dd", "ADOPTED": "\u2705",
    "SHIPPED": "\u2705",
}

# " . " as a pseudo-bullet. It does not render as a list; markdown needs "- " after a
# line break. Field lines legitimately use it, so only long runs count.
FAKE_BULLET_RE = re.compile(r"\s\u00b7\s")

# "(1) ... (2)" buried mid-paragraph rather than broken onto lines.
INLINE_ENUM_RE = re.compile(r"(?:^|[^\n])\((\d)\)\s+\S")

WALL_CHARS = 800          # a paragraph past this, with no internal structure
FAKE_BULLET_MIN = 2       # separators in one line before it reads as a fake list
INLINE_ENUM_MIN = 2       # enumerators in one paragraph



# ---- RECORDED OVERRIDES (o8's guard 2) ----
#
# A blocking check with no legitimate escape hatch trains the illegitimate one. An
# override costs a sentence and leaves a trace; --no-verify costs nothing and leaves
# none. Making the honest path the cheap one is the same repricing move as `resolve`.
#
# The reason is rubber-stamp checked, or "override: needed to ship" becomes the new
# "still current" within a week.
# Override codes that are GATE TOKENS rather than lint findings. A gate fires on the SHAPE of a
# change (a removed line), not on anything `check` can see - so these never appear in a findings
# set, and any validator that only knows lint codes will reject them unconditionally.
#
# ⛔ Defined here, next to nothing in particular, ON PURPOSE: both the validator and the gate that
# consumes it read this ONE name. When they each carried their own answer, adding a gate token
# silently failed to register with the validator and the documented fix became unreachable.
GATE_OVERRIDE_CODES = {"GATE1-REWORD"}

# The archived heading form, defined ONCE and read by both sides. `archive` strips the
# live-looking id from a heading (E-ARCHIVEDMARKER's own instruction) and re-attaches it here;
# gate 1 identifies headings BY ID via `ID_RE.match`, which is anchored at the start, so the
# moved id became invisible to it and every archiving commit demanded an override.
#
# ⛔ An override that a routine action requires is not an escape hatch, it is a disabled gate.
# o9 took the override once on 2026-08-13 and the human asked whether it had been fixed. It had not.
# A writer and a reader that disagree about one marker is the contract defect this workspace
# keeps rediscovering (`memory/marker_format_is_a_contract.md`) - so the shape lives here, and
# the archiver formats with it while gate 1 matches on it.
ARCHIVED_ID_RE = re.compile(r"_\(was\s+([A-Z]+\d+)\s*[-–—]\s*[A-Z]+\)_")


def archived_heading_suffix(eid, status):
    """The ONE writer of the archived-id suffix that ARCHIVED_ID_RE reads."""
    return "  _(was %s - %s)_" % (eid, status)

OVERRIDE_RE = re.compile(
    r"^\s*<!--\s*ORCHDOC:OVERRIDE\s+(\S+)\s+by=(\S+)\s+at=(\S+)\s*-->\s*(.*)$",
    re.MULTILINE)


def without_attestations(text):
    """The doc with OVERRIDE attestation lines blanked, for any check that matches CONTENT.

    ⛔ THE ATTESTATION BLOCK IS THE ONE PLACE IN A DOC THAT QUOTES BAD TEXT ON PURPOSE. Gate 1
    demands a reason for a removed line, and a GOOD reason names the removed line verbatim - so
    a content-matching check reads the explanation as a fresh instance of the thing explained.

    o10 hit the deadlock on their first scaffold: `scaffold` writes the Purpose placeholder,
    authoring a Purpose REMOVES that line, gate 1 refuses, the documented fix is an override
    whose attestation quotes the placeholder - and `E-STUBLEFT` then blocks on the quote,
    reporting the placeholder as still present. **The only ways through were to write a vaguer
    attestation or reword a committed one.** Both make the attestation weaker evidence, which
    means the check was corroding the guard it shares a document with.

    ⭐ AND THIS FILE ALREADY KNEW. The W-BADLINEREF comment says it outright: *"a GOOD `--because`
    quotes the bad value, so a blocking version of this rule penalised exactly the specificity
    the attestation bar demands. A check that corrodes another guard is worse than no check."*
    That check escaped by being demoted to ADVISORY - a per-check dodge, not a shared rule - so
    the next content-matching check written repeated the defect with the lesson four screens up.
    Hence a shared helper: the exemption belongs to the ATTESTATION, not to whichever check
    happened to trip over it.

    ⚠️ THE SPAN, NOT THE MARKER LINE. The first version of this blanked only the line carrying
    `<!-- ORCHDOC:OVERRIDE ... -->`, and `E-STUBLEFT` went on firing - because a real attestation
    WRAPS, and o10's quoted placeholder sat on the continuation line. An attestation runs from
    its marker to the next blank line, and all of it is explanation.

    Lines are blanked rather than deleted so line numbers stay true.
    """
    out, in_att = [], False
    for line in text.split("\n"):
        if OVERRIDE_RE.match(line):
            in_att = True
        elif in_att and not line.strip():
            in_att = False
        out.append("" if in_att else line)
    return "\n".join(out)


def overrides_in(text):
    """[(code, who, when, reason)] recorded in this doc."""
    return [(m.group(1), m.group(2), m.group(3), (m.group(4) or "").strip())
            for m in OVERRIDE_RE.finditer(text)]



# o5's falsifiability test. An attestation that CLAIMS verification must say what would
# have shown otherwise; one that merely records a judgement need not. The danger is the
# first kind, because it feels like verification and nobody re-checks it.
VERIFY_LANGUAGE_RE = re.compile(
    r"\b(verified|confirmed|checked|proven|measured|tested|validated)\b", re.I)
# Evidence that a falsifier was actually named: a command, a path, a count, a ref, or an
# explicit statement of what would have contradicted the claim.
FALSIFIER_RE = re.compile(
    # a command, a ref, or an exit code
    r"`[^`]+`|\bgit \w+|\bgrep\b|origin/\w+|exit \d"
    # a count, in digits OR in words - "zero false positives across all eight docs" is
    # a falsifier, and the first version could not see it
    r"|\b\d+\s*(?:of|/)\s*\d+\b|\b\d{2,}\b"
    r"|\b(?:zero|no)\s+\w+|\ball\s+(?:\d+|two|three|four|five|six|seven|eight|nine|ten)\b"
    # an explicit statement of the contrary outcome, including an OBSERVED behaviour -
    # "verified to refuse" names exactly what would have come back the other way
    r"|would have (?:shown|returned|failed|refused|caught|flagged)"
    r"|(?:verified|observed|watched|tested)\s+(?:to|it)\s+\w+"
    r"|both (?:ways|readers|directions)|either direction",
    re.I)

# A human RULING is not a measurement claim. Nothing mechanical can falsify "the human ruled
# B", so demanding a falsifier of it is a category error - o8's compliance-not-truth
# limit, arrived at from the other side.
# Any actor, any pronoun. This hardcoded one first name and only the pronoun "his", so a
# ruling by anyone else - or one referred to as "their call" - did not register at all.
HUMAN_RULING_RE = re.compile(
    r"\b(?:[A-Z][a-z]+|the human|o\d+)\s+"
    r"(ruled|confirmed|decided|chose|directed|said|corrected)\b"
    r"|\bruling\b|\b(?:his|her|their|its) (?:call|judgement|judgment|taste)\b")



# ---- THE SCHEMA: one definition, imported by the checker AND the scaffolder ----
#
# the human's skeleton, 2026-08-07. Numbered sections give a stable addressable spine that
# does not depend on prose, which is what E-SCATTERED could not supply on its own: it
# forced entries of a kind together, but each doc still named its own sections.
#
# LIVE (section 2) and COMPLETED (section 4) are deliberately symmetric, so archiving is
# a MOVE from 2.x to 4.x rather than a judgement call - which is what makes it
# mechanizable at all.
SCHEMA_SECTIONS = [
    ("1",    "LINKS AND DOCS",       "every doc and URL this orchestrator owns"),
    # "only what needs THEM" had the same ambiguity the human flagged in §3's "NOT their plate":
    # in a sentence whose whole job is to distinguish two owners, a pronoun has two candidate
    # referents. {NAME} is already derived for the heading; use it here too.
    ("2",    "LIVE ON {NAME}'S PLATE", "only what needs {NAME}. Nothing else."),
    ("2.1",  "Decisions",            "need {NAME}'s ruling"),
    ("2.2",  "Questions",            "need an answer from {NAME}"),
    ("2.3",  "To-Dos",               "need {NAME} to act"),
    # the human, 2026-08-07: "Should we set 3 for 'On Claude's Plate'?" Yes - it makes ownership
    # structural. 2 and 3 are the two halves of one question (whose is this?), and naming one
    # by OWNER and the other by STATE meant the pairing had to be remembered rather than read.
    ("3",    "ON CLAUDE'S PLATE",    "the orchestrator's own work, NOT the human's plate"),
    ("4",    "FINDINGS",             "what was learned, and why it holds"),
    ("5",    "GUARDS",               "what this orchestrator will not do"),
    # 6 through 98 are YOURS. the human, 2026-08-07: "For some orchestrators, they may need to
    # create sections other than what I've created. They need latitude to do that." An
    # earlier draft gave custom content one fixed box, which forces every orchestrator's
    # subject matter into a single section whether it divides that way or not. A range
    # does not, and the sort still keeps all of it above COMPLETED.
    ("99",   "COMPLETED",            "closed items. Pinned at 99 so done always sinks."),
    ("99.1", "Decisions",            "ruled"),
    ("99.2", "Questions",            "answered"),
    ("99.3", "To-Dos",               "done"),
]

# Prefix -> which numbered section an entry of that kind belongs in, live and completed.
KIND_SECTION_NUM = {"D": ("2.1", "99.1"), "Q": ("2.2", "99.2"),
                    "T": ("2.3", "99.3"), "A": ("2.3", "99.3"),
                    "F": ("4", "4"), "S": ("4", "4"), "W": ("3", "99.3")}

# The leading decoration class must NOT swallow a SIGN. It was `\W*`, which happily ate the
# "-" in "## \u00a7-1 NEGATIVE", so that heading read as \u00a71 and SATISFIED the schema's requirement
# for section 1 - a malformed section silently standing in for a real one. Emoji and other
# decoration are still allowed; + and - are not.
# \u26d4 THE \u00a7 IS REQUIRED. It used to be optional, so ANY heading beginning with a number was
# read as a section: o1's `### \ud83d\udce6 7-PILLARS RESTRUCTURE` became "section \u00a77", which put the
# spine out of canonical order and produced a blocking E-SCHEMA on a correctly migrated
# doc. "3 THINGS TO FIX" or "2026 review" would do the same.
#
# \u2b50 A section number is a DELIBERATE MARK, not a number that happens to appear first. An
# optional sigil means the parser is guessing at intent, and it will guess wrong on prose.
SECTION_RE = re.compile(r"^#{2,3}\s*(?:[^\w\s+-]*\s*)?\u00a7\s*(\d+(?:\.\d+)?)\b")

# Generated regions. Same contract discipline as the plate: one token, matched
# structurally, and a malformed marker REFUSES rather than guessing.
INDEX_BEGIN_TOKEN = "ORCHDOC:INDEX:BEGIN"
INDEX_END_TOKEN = "ORCHDOC:INDEX:END"
INDEX_BEGIN = ("<!-- %s - generated by `orchdoc.py scaffold`. Do not hand-edit. -->"
               % INDEX_BEGIN_TOKEN)
INDEX_END = "<!-- %s -->" % INDEX_END_TOKEN

# The findings index is its OWN generated region, living at the head of section 4 rather
# than in the top block. Separate markers because they are regenerated independently and
# a reader deletes or collapses one without touching the other.
FINDEX_BEGIN_TOKEN = "ORCHDOC:FINDEX:BEGIN"
FINDEX_END_TOKEN = "ORCHDOC:FINDEX:END"
FINDEX_BEGIN = ("<!-- %s - generated by `orchdoc.py scaffold`. Do not hand-edit. -->"
                % FINDEX_BEGIN_TOKEN)
FINDEX_END = "<!-- %s -->" % FINDEX_END_TOKEN

# The header metadata block. Generated, because every field in it is a MEASUREMENT and
# the one time a field like this was hand-maintained it produced 32 defects in one doc.
META_BEGIN_TOKEN = "ORCHDOC:META:BEGIN"
META_END_TOKEN = "ORCHDOC:META:END"
META_BEGIN = ("<!-- %s - generated by `orchdoc.py scaffold`. Do not hand-edit. -->"
              % META_BEGIN_TOKEN)
META_END = "<!-- %s -->" % META_END_TOKEN


# SEVERITY - and why the split matters.
#
# BLOCKING codes are the ones where the document actively LIES: it asserts something
# that is false, or asserts a status that contradicts itself. Those are the failures
# the human has actually been bitten by.
#
# ADVISORY codes are risk and style. They are reported and counted but do not fail the
# gate, because a gate that always fails is a gate everyone turns off - and the live
# docs carry 600+ em-dashes that belong to a separate, owner-agreed sweep. Use --strict
# to promote everything to blocking.
# Checks that cannot run without a real git repo. Their fixtures are SKIPPED (loudly)
# rather than failed when none is present, so `selftest` stays meaningful on a fresh
# clone - the one command a new user is told to run first.
_NEEDS_GIT = {"E-DEADREF", "W-SHACITE"}


def _self_sha():
    """A commit SHA that resolves in whatever repo we are standing in.

    Used by the W-SHACITE fixture, which must supply a RESOLVABLE sha - that is the whole
    distinction it tests. Returns a deliberately-unresolvable placeholder when there is no
    repo, in which case the fixture is skipped via _NEEDS_GIT rather than run.
    """
    for repo in citable_repos():
        if not (repo / ".git").exists():
            continue
        # Must contain a LETTER, or the SHA detector rejects it by design ("require a
        # letter so plain numbers and dates are not read as SHAs"). A short HEAD sha can
        # be all digits - the first attempt returned 6754330 and the fixture was silently
        # ignored, so neither W-SHACITE nor E-DEADREF fired and the test failed with no
        # explanation. Walk recent commits until one qualifies.
        rc, out, _ = git(["rev-list", "-n", "20", "HEAD"], cwd=repo)
        if rc != 0:
            continue
        for full in out.split():
            for cand in (full[:7], full[:12], full):
                if SHA_HAS_LETTER.search(cand):
                    return cand
    return "0000000"

BLOCKING = {"E-DUPID", "E-SELFCLAIM", "E-NOSTATUS", "E-BADSTATUS", "E-DEADREF",
            "E-STALE", "E-ARCHIVEDMARKER", "E-PLATEDRIFT", "E-SCATTERED",
            "E-STALEPROSE", "E-RUBBERSTAMP", "E-NODEPS", "E-BADMARKER",
            "E-BADTOUCH", "E-AMBIGUOUSDATE", "E-MIXEDSTATE", "E-CLOSEDWITHOPENSUBS", "E-SETTLEDNOTSTRUCK",
            "E-IDSHAPE", "E-IDORDER", "E-ALLSUBSDONE", "E-STUBLEFT", "E-EMPTYLINKS", "E-LEGACYDOC", "E-PLATEHISTORY", "E-LOOSEINPARENT", "E-WRONGSECTION",
            "E-NOOWNER", "E-DONEINACTIVE", "E-DONEBUTOPEN", "E-MARKERDRIFT", "E-SCHEMA", "E-TITLE", "E-ONEH1", "E-FUTUREDATE", "E-NOFETCH", "E-BADID", "E-CONFLICT", "E-IO"}
ADVISORY = {"W-SHACITE", "W-LINECITE", "W-BADLINEREF", "W-EMPTYPROMISE", "W-FAKEBULLETS", "W-INLINEENUM",
            "W-OVERRIDE", "W-STRIKEDONE", "W-UNFALSIFIABLE",
            "W-WALLOFTEXT"}


class Finding:
    __slots__ = ("code", "line", "msg", "detail")

    def __init__(self, code, line, msg, detail=""):
        self.code = code
        self.line = line
        self.msg = msg
        self.detail = detail


def strip_decoration(text):
    """Remove leading emoji/bold/tick decoration so an ID can be read."""
    return DECORATION_RE.sub("", text).lstrip()


def parse_entries(lines):
    """
    Return (entries, sections).

    entry  = dict(id, line, level, title, body, section)
    Body runs to the next heading of the same or shallower level.
    """
    heads = []
    in_fence = False
    in_att = False  # inside an ORCHDOC:OVERRIDE attestation span - explanation, not structure
    depth = 0  # <details> nesting: a heading inside one is ARCHIVED, not live
    for i, raw in enumerate(lines, start=1):
        if raw.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        # Strip inline code spans BEFORE looking for the tag. A doc that discusses
        # `<details>` in prose (this tool's own OrchDoc does) would otherwise open a
        # block that never closes, and every entry below it would be misread as
        # archived - 11 false positives on the first run.
        #
        # ⛔ AND HTML COMMENTS, AND ATTESTATIONS - the two halves that were missing. o7's doc
        # carries an `ORCHDOC:OVERRIDE` stamp, written by this tool, whose `--because` text
        # reads *"found by o9 at line 1158 inside a collapsed <details> fold"*. That text
        # sits AFTER the comment closes, so it is ordinary prose to a parser - and the depth
        # counter read it as an OPEN tag that never closes. Every entry below line 2585 was
        # therefore flagged archived. It stayed invisible for eight days because the only
        # entries down there already carried an archive suffix; `reorder` moved one ordinary
        # entry across the line and E-ARCHIVEDMARKER fired on it immediately.
        #
        # ⭐ `without_attestations()` already states the rule this needed - an attestation
        # QUOTES the thing it is explaining, so a content-matching reader must not treat the
        # quote as an instance. Its docstring says the exemption "belongs to the ATTESTATION,
        # not to whichever check happened to trip over it", and then the PARSER - which every
        # check reads through - did not have it. Same rule as marker_span's: IF A READER WOULD
        # NOT ACT ON IT, THE PARSER MUST NOT EITHER.
        if OVERRIDE_RE.match(raw):
            in_att = True
        elif in_att and not raw.strip():
            in_att = False
        if not in_att:
            low = re.sub(r"<!--.*?-->", "", raw.lower())
            low = re.sub(r"`[^`]*`", "", low)
            if "<details" in low:
                depth += 1
            if "</details>" in low:
                depth = max(0, depth - 1)
        m = re.match(r"^(#{1,6})\s+(.*)$", raw)
        if m:
            heads.append((i, len(m.group(1)), m.group(2).rstrip(), depth > 0))

    entries = []
    sections = []
    for idx, (ln, level, title, archived) in enumerate(heads):
        if level <= 2:
            sections.append({"line": ln, "title": title, "level": level})
        cleaned = strip_decoration(title)
        m = ID_RE.match(cleaned)
        # ⛔ AN ARCHIVED ENTRY STILL HAS AN ID, IT IS JUST NOT IN FRONT ANY MORE. archive
        # --commit deliberately demotes it into `_(was W8 - RESOLVED)_` so the entry stops
        # reading as live. Without this branch the `continue` below DELETED the entry from
        # `entries`, so every check lost it at once - and the visible symptom was E-BADTOUCH
        # calling the archiving commit's own `Touches:` trailer a dangling pointer, i.e. filing
        # work as done is what broke the record of having done it.
        #
        # ⭐ Gate 1 was taught this marker when the suffix was introduced; the parser was not.
        # One reader updated, the rest left behind - the exact defect the comment above
        # ARCHIVED_ID_RE warns about, repeated four lines from where it is written down.
        _arch_id = None
        if not m:
            _am = ARCHIVED_ID_RE.search(title)
            if _am:
                _arch_id = _am.group(1)
        if not m and not _arch_id:
            continue
        end = len(lines)
        for ln2, lvl2, _t, _a in heads[idx + 1:]:
            if lvl2 <= level:
                end = ln2 - 1
                break
        # Which h1/h2 section is this entry under?
        sec = ""
        for s in sections:
            if s["line"] <= ln:
                sec = s["title"]
        entries.append({
            # archived entries resolve by their demoted id, and are ALWAYS flagged archived -
            # a heading can only carry the `_(was ...)_` suffix by having been archived, so
            # trusting the heads-parser's flag alone would depend on decoration surviving.
            "id": m.group(1) if m else _arch_id,
            "line": ln,
            "level": level,
            "title": title,
            "section": sec,
            "body": "\n".join(lines[ln - 1:end]),
            "archived": archived or bool(_arch_id),
            # ⭐ WHERE the id sits, not whether it exists. E-ARCHIVEDMARKER's invariant is that
            # an archived entry must not READ AS LIVE, and only a LEADING id does that - an id
            # inside `_(was W2 - RESOLVED)_` is a record of what it was. Without this the check
            # fires on the archiver's own output and its stated remedy cannot clear it.
            "id_demoted": bool(_arch_id),
        })
    return entries, sections


class _lock:
    """
    Cross-process lock around a doc's read-modify-write.

    o7 asked whether `add` is safe when two orchestrators allocate at once. It was not:
    read-then-write with no lock races, and two concurrent adds either allocate the SAME
    id or lose one write entirely - the exact collision `add` exists to prevent. O_EXCL
    creation is atomic on Windows and POSIX alike.
    """

    def __init__(self, doc, timeout=10.0):
        self.path = Path(str(doc) + ".lock")
        self.timeout = timeout
        self.fd = None

    def __enter__(self):
        import time
        deadline = time.time() + self.timeout
        while True:
            try:
                self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                return self
            except FileExistsError:
                # A crashed holder must not wedge every future run.
                try:
                    if time.time() - self.path.stat().st_mtime > 60:
                        self.path.unlink()
                        continue
                except (OSError, UnicodeDecodeError):
                    pass
                if time.time() > deadline:
                    raise SystemExit(
                        "[REFUSE] %s is locked by another orchestrator. Retry shortly."
                        % self.path.name)
                time.sleep(0.05)

    def __exit__(self, *exc):
        try:
            if self.fd is not None:
                os.close(self.fd)
            self.path.unlink()
        except (OSError, UnicodeDecodeError):
            pass
        return False


def gh_anchor(heading):
    """
    GitHub-flavoured heading anchor. Markdown DOES support in-document links, so the
    generated index can jump the reader straight to the entry - no HTML build needed.
    Rule: lowercase, drop everything except word chars/space/hyphen, spaces to hyphens.
    """
    a = strip_decoration(heading).lower()
    a = re.sub(r"[^\w\s-]", "", a)
    return "#" + re.sub(r"\s+", "-", a.strip())


# Which kind an entry id belongs to, and the order a human scans them in.
KIND_ORDER = [
    ("Q", "QUESTIONS - need an answer from you"),
    ("D", "DECISIONS - need your call"),
    ("A", "ACTIONS - on your plate"),
]


def build_plate_block(entries, raw_lines=None):
    """
    Build the generated index. ONE builder, used by `plate` to write it and by `check`
    to detect a hand-edit - so the rendered block and the derivation cannot diverge.

    GROUPED BY KIND, with in-document links (the human, 2026-08-06). He hit Q1 and D1 next
    to each other at the top, then had to scroll past many unrelated sections to find
    D2, with no obvious place to scroll to. A flat list of ids does not help a human
    doing a VISUAL search: like goes with like, and every row is clickable.
    """
    live = []
    held = 0          # open, but owned by the orchestrator - counted, not hidden
    for e in entries:
        if e.get("archived") or status_of(e["body"]) not in PLATE_STATUS:
            continue
        own = re.search(r"\*\*Owner:\*\*\s*([^\-\n*·]+)", e["body"])

        # ⛔ THE PLATE IS "ONLY WHAT NEEDS THEM. NOTHING ELSE." - so an item the
        # ORCHESTRATOR owns does not belong on it, however open it is. Selecting on status
        # alone put every open finding and every piece of in-flight work in front of the
        # human, which is the precise noise the plate exists to remove: a queue that lists
        # things you cannot act on trains you to skim it, and then it fails at the one job
        # it has. Orchestrator-owned open work lives in §3 IN FLIGHT, where it belongs.
        who = (own.group(1).strip().lower() if own else "")
        if who in ("orchestrator", "lane", "-", "") or re.fullmatch(r"o\d+", who):
            held += 1
            continue
        title = strip_decoration(e["title"])
        title = re.sub(r"^%s\s*[-:]\s*" % re.escape(e["id"]), "", title)
        # A "|" ends a markdown table CELL. An entry title containing one
        # silently split the row and corrupted the very view the human reads.
        title = title.replace("|", "\\|")
        # Truncate at a WORD boundary, and say that it was truncated. o8: a cell cut
        # mid-word ends "...(o5's audit, 2026-0" - and "2026-0" READS AS DATA, not as an
        # elision. A silent truncation turns a clipped value into a wrong one, which is the
        # same class as every other well-formed-but-false thing found today, arriving
        # through the formatter instead of the parser.
        if len(title) > 88:
            cut = title[:88].rsplit(" ", 1)[0].rstrip(" ,;:-")
            title = (cut or title[:88]) + "…"
        live.append((e["id"], title, own.group(1).strip() if own else "-",
                     gh_anchor(e["title"])))

    def kind_of(eid):
        m = re.match(r"^([A-Z]+)", eid)
        return m.group(1)[0] if m else "?"

    block = [PLATE_BEGIN, ""]
    total = 0
    for prefix, label in KIND_ORDER:
        group = [r for r in live if kind_of(r[0]) == prefix]
        if not group:
            continue
        total += len(group)
        block += ["**%s**" % label, "",
                  "| | What it needs from you | Owner |", "|---|---|---|"]
        for eid, title, owner, anchor in group:
            block.append("| **[%s](%s)** | %s | %s |" % (eid, anchor, title, owner))
        block.append("")

    other = [r for r in live if kind_of(r[0]) not in {p for p, _ in KIND_ORDER}]
    if other:
        total += len(other)
        block += ["**OTHER OPEN**", "", "| | What | Owner |", "|---|---|---|"]
        for eid, title, owner, anchor in other:
            block.append("| **[%s](%s)** | %s | %s |" % (eid, anchor, title, owner))
        block.append("")

    # Does an ACTIVE section hold decision-shaped content the parser could not turn into
    # entries? o1 writes its live decisions as BULLETS under a DECISIONS heading, so the
    # parser sees none of them - and "0 open" is then a statement about the PARSER, not
    # about the document. Detect that rather than report it as fact.
    unread = False
    seen_ids = {(e["section"], e["id"]) for e in entries}
    cur_sec, bullets = "", 0
    for ln in (raw_lines or []):
        if ln.startswith("## "):
            if cur_sec and is_active_section(cur_sec) and bullets >= 2 and \
                    not any(s == cur_sec for s, _ in seen_ids):
                unread = True
            cur_sec, bullets = ln, 0
        elif re.match(r"^\s*[-*]\s+\*\*", ln):
            bullets += 1
    if cur_sec and is_active_section(cur_sec) and bullets >= 2 and \
            not any(s == cur_sec for s, _ in seen_ids):
        unread = True

    if total == 0 and (not entries or unread):
        # ⛔ NO PARSEABLE ENTRIES IS NOT "NOTHING OPEN". It is "I could not read this
        # document", and those must never render the same way.
        #
        # The old text said: "Nothing open. This line is generated from the entries, so it
        # cannot assert a false empty." In a doc whose decisions are written as BULLETS
        # rather than `### <ID>` entries - which is how o1 writes them - that sentence
        # CLAIMS TRUSTWORTHINESS AT THE EXACT MOMENT IT IS WRONG. It is the most dangerous
        # line the tool can emit: a false empty wearing a guarantee that it cannot be one.
        #
        # ⭐ Same distinction as E-NOFETCH: "I checked and it is fine" and "I could not
        # check" are different answers, and collapsing them is worse than having no
        # answer, because the reader stops looking.
        block += ["⛔ **CANNOT DETERMINE what is open.** No entry in this doc is in a form",
                  "this generator can read - an entry needs a `### <ID> - title` heading",
                  "AND a `**Status:**` field. **This is NOT a claim that nothing is open.**",
                  "Read the sections below directly until the entries carry those fields.",
                  ""]
    elif total == 0:
        block += ["Nothing open. **This line is generated from the entries, so it cannot",
                  "assert a false empty.**", ""]
    # ⛔ SAY WHAT WAS EXCLUDED. The plate now omits orchestrator-owned items, which is
    # right - they do not need the human - but an empty plate that does not explain its
    # emptiness is the "None open while items are open" failure this whole tool was
    # commissioned to kill. Silence about an exclusion is indistinguishable from there
    # being nothing to exclude, and the reader cannot tell which they are looking at.
    if held:
        block += ["_%d open item(s) owned by the orchestrator are NOT shown here - they "
                  "need no decision from you. They live under ON CLAUDE'S PLATE._"
                  % held, ""]
    block += ["_%d open. Generated by `orchdoc.py plate`; edits here are overwritten._"
              % total, PLATE_END]
    return block


def check_doc(path):
    """Run every invariant against one doc. Returns a list of Finding."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return [Finding("E-IO", 0, "cannot read: %s" % e)]

    lines = text.splitlines()
    # For CONTENT scans only. Same length as `lines`, so indices and reported line numbers stay
    # true - the attestation is blanked, not removed. Structural checks keep using `lines`;
    # anything matching on what the text SAYS should use this, because an attestation quotes bad
    # text on purpose. See without_attestations().
    lines_live = without_attestations(text).splitlines()
    findings = []
    entries, sections = parse_entries(lines)

    # --- E-DUPID: one ID, one entry. The single highest-value invariant. ---
    by_id = defaultdict(list)
    _stopsign_ok = follows_stopsign_convention(entries)
    for e in entries:

        # the human's vocabulary, 2026-08-07: the stop sign means NOT YET DONE and nothing else;
        # yellow triangle for caution, double-exclamation for plain emphasis. A stop sign in a
        # CLOSED entry cannot mean unfinished - the entry is finished - so it is emphasis, and
        # every instance of it spends the one glyph that could have carried machine-readable
        # state. It is ADVISORY because it is a vocabulary migration, not a correctness bug,
        # and a blocking check on 112 pre-existing instances would just get overridden.
        if status_of(e["body"]) in TERMINAL_STATUS and "\u26d4" in e["body"]:
            findings.append(Finding(
                "W-STOPSIGN", e["line"],
                "%s is closed but uses \u26d4, which is reserved for NOT YET DONE" % e["id"],
                "\u203c\ufe0f for plain emphasis, \u26a0\ufe0f for caution - reserving \u26d4 "
                "lets `E-CLOSEDWITHOPENSUBS` read it as state"))

        # the human's D5 ruling, enforced. A terminal status over a still-open sub-item is a FALSE
        # DONE - the human reads "resolved", stops checking, and the outstanding work goes
        # invisible without anyone having lied. o8 predicted it before it was reachable.
        # Safe as BLOCKING because it fires only on a contradiction INTERNAL TO ONE ENTRY:
        # no cross-doc knowledge, no line numbers, no guess about intent. That is the property
        # W-BADLINEREF lacked when it had to be demoted.
        # WORK containers only. A FINDING's bullets are narrative - they describe the bug that
        # was found, so "cannot reach", "blocked", and a stop sign all appear in a fully-closed
        # finding as a matter of course. Reading them as outstanding work made 6 of 6 hits false
        # on the first run. The distinction is not a heuristic: a decision/to-do/work item's
        # sub-items ARE the work; a finding's sub-items are a description OF work.
        if (e["id"][:1] in ("D", "T", "W", "A")
                and status_of(e["body"]) in TERMINAL_STATUS
                and has_open_subitems(e["body"], _stopsign_ok)):
            findings.append(Finding(
                "E-CLOSEDWITHOPENSUBS", e["line"],
                "%s claims a closed status while a sub-item under it is still not done"
                % e["id"],
                "mark the container IN PROGRESS and strike the finished sub-items; it moves "
                "to \u00a799 only when ALL of them are done (the human's D5 ruling)"))

        # \u26d4 THE MIRROR CASE, AND THE ONE THAT ACTUALLY COSTS THE HUMAN TIME. The check above catches
        # a CLOSED container hiding open work. This catches the opposite and more common shape:
        # a LIVE container whose sub-items are already decided, with none of them struck.
        #
        # the human, 2026-08-10, on o8's DA6: *"I'm still re-reading items that are DONE AND DECIDED.
        # This is wasting my time and I'm trying to figure out WHAT still needs to be done inside
        # them... only to realize that they just simply have not been struck-through."*
        #
        # DA6 read `Status: OPEN` while carrying three \u2705 rulings. The status field is what makes
        # him open the item; strike-through is what lets him skim it once inside. **Only the
        # second half had a check, and it was advisory.** So the half that decides whether he
        # spends the time at all was unguarded.
        #
        # \u2b50 It is BLOCKING because the cost lands on the one person whose attention this whole
        # instrument exists to protect, and it is invisible to the author - the entry looks fine
        # to whoever wrote it, precisely because they already know what is settled.
        if (e["id"][:1] in ("D", "T", "W", "A")
                and status_of(e["body"]) in LIVE_STATUS):
            done_unstruck, open_subs = [], 0
            for off, raw in enumerate(e["body"].splitlines()[1:]):
                # Same two guards as `strike`, and they have to BE the same. o1 reverted five
                # false ticks and the gate immediately demanded them back, naming the exact
                # lines - so obeying it would have re-inserted the falsehood. A check and its
                # fixer disagreeing about what qualifies turns the gate into a machine for
                # restoring the defect.
                if not re.match(r"^\s*[-*+]\s", raw):
                    continue
                txt = re.sub(r"`[^`]*`", "", raw)
                # ⛔ AN EMPTY CHECKBOX IS THE MOST EXPLICIT "NOT DONE" MARKDOWN HAS, and this
                # counter could not see it. There is a CHECKED_BOX_RE and there was no unchecked
                # equivalent anywhere in this path, so `- [ ] Rehome the 143` matched neither
                # branch, open_subs stayed 0, and E-ALLSUBSDONE reported that every sub-item was
                # finished. o11 hit it on an entry with THREE open steps, one of them the human's.
                #
                # ⭐ AND THE REMEDY IT PRINTS IS DESTRUCTIVE: "set a terminal status, then
                # archive". Following it files live work as complete. The comment three lines
                # below warns that a gate and its fixer diverging "turns the gate into a machine
                # for restoring the defect" - this is that hazard pointed the other way, and the
                # expensive direction, because the settled-sub-item convention actively
                # encourages writing sub-items as checkboxes.
                if UNCHECKED_BOX_RE.match(raw):
                    open_subs += 1
                    continue
                if NOTDONE_MARK_RE.search(txt):
                    open_subs += 1
                    continue
                if looks_open(txt):
                    # Neither settled nor plainly open - so it is NOT evidence the container is
                    # finished, and NOT something to demand a tick on. Same predicate the fixer
                    # uses, deliberately: when they diverged, the gate demanded back exactly the
                    # false ticks a colleague had just reverted.
                    open_subs += 1
                    continue
                if not DONE_MARK_RE.search(NOTDONE_MARK_RE.sub(" ", txt)):
                    continue
                # A checked checkbox IS the whole form - green tick, grey, strike-through, all
                # native. A literal <span style=…> is not a second way of achieving it; it is
                # visible junk in the sentence, so it counts as a defect rather than a mark.
                if not CHECKED_BOX_RE.search(raw):
                    done_unstruck.append((e["line"] + 1 + off, ["checkbox"]))
                elif GREY_SPAN_RE.search(raw):
                    done_unstruck.append((e["line"] + 1 + off, ["literal-span"]))
            if done_unstruck:
                # the human, 2026-08-10, gave the settled-sub-item form three parts, and all three do
                # different work: the CHECKBOX is scannable down the left margin, the STRIKE
                # reads as retracted, and the GREY drops it out of focus so the eye lands on
                # what is still owed. Two of three still leaves it competing for attention.
                short = ", ".join("L%d(%s)" % (n, "+".join(m)) for n, m in done_unstruck[:5])
                findings.append(Finding(
                    "E-SETTLEDNOTSTRUCK", e["line"],
                    "%s is %s but %d settled sub-item(s) are not fully marked done, so the human "
                    "must read the whole entry to find what is still owed"
                    % (e["id"], status_of(e["body"]), len(done_unstruck)),
                    "the form is a CHECKED CHECKBOX and nothing else - `- [x] the thing`. It "
                    "renders green-ticked, GREY and STRUCK THROUGH natively, which is all three "
                    "of the human's requirements from four characters. A literal <span style=...> is "
                    "NOT rendered here and shows as visible junk mid-sentence. "
                    "Fix: orchdoc.py strike --doc <doc> --commit. At: %s" % short))

            # ⛔ ALL sub-items done, container still live. the human: a done item with all done
            # sub-items goes to §99 COMPLETELY - never left in a live section. The existing
            # E-DONEINACTIVE only sees an entry whose STATUS is already terminal, so an entry
            # that finished its last sub-item and never had its status updated is invisible to
            # it: the work is over, the plate still shows it, and nothing says so.
            if open_subs == 0 and any(
                    DONE_MARK_RE.search(NOTDONE_MARK_RE.sub(" ", re.sub(r"`[^`]*`", "", r)))
                    for r in e["body"].splitlines()[1:]
                    if re.match(r"^\s*[-*+]\s|^\s*\d+\.\s", r)):
                findings.append(Finding(
                    "E-ALLSUBSDONE", e["line"],
                    "every sub-item under %s is done but the container is still %s, so finished "
                    "work is sitting on a live plate" % (e["id"], status_of(e["body"])),
                    "close it and move it whole: set a terminal status, then "
                    "orchdoc.py archive --doc <doc> --commit"))
        by_id[e["id"]].append(e)
    for eid, group in sorted(by_id.items()):
        if len(group) > 1:
            statuses = []
            for g in group:
                statuses.append(status_of(g["body"]) or "unstated")
            where = ", ".join("line %d (%s)" % (g["line"], s)
                              for g, s in zip(group, statuses))
            conflict = len(set(statuses)) > 1
            findings.append(Finding(
                "E-DUPID", group[0]["line"],
                "ID '%s' appears in %d entries%s" % (
                    eid, len(group), " with CONFLICTING status" if conflict else ""),
                where))

    # --- E-STALEPROSE / E-RUBBERSTAMP: reasoning whose inputs moved under it ---
    by_id_single = {e["id"]: e for e in entries if len(by_id.get(e["id"], [])) == 1}
    # One git read for the whole doc rather than one per entry.
    # No doc slug means no entry namespace, so trailers cannot be attributed to it.
    # Scanning anyway let real repo history bleed into synthetic fixtures - the check
    # must be a function of the document, not of whatever else is in the repo.
    _slug_m = re.search(r"ORCHESTRATOR-DECISIONS-(o\d+)", path.name)
    if _slug_m:
        pushed, bad_touches = touches_since(_slug_m.group(1), None)
    else:
        pushed, bad_touches = {}, {}
    known_ids = {e["id"] for e in entries}
    # An UNQUALIFIED trailer cannot be attributed to any doc, so reporting it per-doc
    # printed the same defect eight times - once for every OrchDoc, none of which caused
    # it. It is a defect of the COMMIT. Only trailers qualified to THIS doc are reported
    # here; unqualified ones are surfaced once, by cmd_check, at the end.
    _ = bad_touches
    for eid in sorted(pushed):
        if eid not in known_ids:
            findings.append(Finding(
                "E-BADTOUCH", 0,
                "commit trailer names %s, which is not an entry in this doc" % eid,
                "a typo here is an invisible non-update"))
    # --- E-DONEBUTOPEN: the work landed and the entry never moved ---
    #
    # ⛔ the human, 2026-08-19, after asking twice in five minutes why finished items were still on
    # his plate: "Rules fail, hooks are enforceable. How do we attach a HOOK to items getting
    # completed?"
    #
    # ⛔ TWO EARLIER DESIGNS FAILED, both measured rather than reasoned:
    #   the `Touches:` trailer  - zero commits named D11 or D8; it needs the author to remember
    #   paths the entry cites   - caught 1 of 4; D8 cited another repo, D9 and D12 cite nothing
    #
    # ⭐ So the entry DECLARES its own completion signal, and this evaluates it. Some items
    # genuinely have none - a taste call closes on the owner's word and nothing will ever detect
    # it - and a `Done-when:` naming the owner (OWNER_TOKEN) says so out loud, which is
    # information rather than a gap.
    #
    # ⛔ FIRES ONLY ON DEMONSTRABLE SATISFACTION. Unknown forms, unevaluatable conditions and
    # OWNER_TOKEN are all silent. o11: a guard that refuses correct work several times a day
    # trains bypass, and a bypassed guard is worth less than none.
    for e in entries:
        if e.get("archived") or status_of(e["body"]) not in ("OPEN", "BLOCKED"):
            continue
        cond = done_when(e["body"])
        if not cond:
            continue
        sat, evidence = done_when_satisfied(cond, opened_of(e["body"]), PROJECTS)
        if sat:
            findings.append(Finding(
                "E-DONEBUTOPEN", e["line"],
                "%s is still OPEN but its own Done-when is satisfied: %s" % (e["id"], evidence),
                "resolve it (orchdoc.py resolve --doc <d> %s --ruling \"...\") or, if it is "
                "genuinely not finished, correct the Done-when - a condition that fires early "
                "is worse than none" % e["id"]))

    for e in entries:
        if e.get("archived"):
            continue
        deps = depends_of(e["body"])
        cited = cited_paths(e["body"])
        # ⛔ A DOCUMENT IS NOT ITS OWN DEPENDENCY. An entry citing the file it lives in was
        # staled by every commit to that file, including commits that changed other entries -
        # so editing anything marked it stale, forever. That is the file noticing itself move,
        # which tells the reader nothing about whether this entry's reasoning still holds.
        cited = [c for c in cited if pathlib.Path(str(c)).name != path.name]
        # The PUSH edge must be consulted even when the section declared NOTHING - that
        # is the entire point of the push model: it works without the author's foresight.
        # Skipping undeclared entries meant a trailer naming them did nothing, which
        # silently reinstated the invisible-gap this model exists to close.
        if not deps and not cited and e["id"] not in pushed:
            # o8's residual risk, and it is the one this design inherits rather than
            # creates: `Depends:` edges are hand-authored, so a MISSING edge is
            # invisible. The section never goes stale because nothing declares what it
            # rests on, and it therefore looks permanently current. That is a negative
            # result from a scope the author chose - the rule already in the standard.
            # So a RULED decision must declare at least one edge, and "no dependencies
            # declared" cannot masquerade as "no dependencies moved".
            if (status_of(e["body"]) in {"RESOLVED", "ANSWERED", "DONE"}
                    and re.search(r"\*\*Resolved[^:]*:\*\*", e["body"])):
                findings.append(Finding(
                    "E-NODEPS", e["line"],
                    "ruled decision '%s' declares no **Depends:** edge, so nothing can "
                    "ever mark it stale" % e["id"],
                    "name what the ruling rests on, or it looks permanently current"))
            continue
        rdate, attestation = reviewed_of(e["body"])

        moved = []
        ambiguous = []

        # DERIVED edges: the section cited these, so it already declared them.
        rstamp = _as_stamp(rdate) if rdate else None
        regions = cited_regions(e["body"])
        for pth, _pv in paths_changed_since(cited, rstamp).items():
            when, subject = _pv[0], _pv[1]
            psha = _pv[2] if len(_pv) > 2 else ""
            if not (rstamp is None or when > rstamp):
                continue
            # A citation that narrowed itself to `path#needle` only counts commits that
            # touched a line containing that needle. See commit_touched_region.
            needles = regions.get(str(pth)) or []
            if psha and needles and not any(
                    commit_touched_region(psha, str(pth), n) for n in needles):
                continue
            note = ""
            if "ORCHESTRATOR-DECISIONS-" in str(pth):
                # ⭐ Say WHAT it touched. Most of these trips are cleared by discovering the
                # commit never went near the entry this one cites, and that answer costs three
                # commands today. Printed here, it costs a glance.
                touched = entries_touched(psha, pathlib.Path(str(pth)).name)
                if touched:
                    note = " [touched: %s]" % ", ".join(touched[:8])
                elif touched == []:
                    # ⛔ DEMONSTRABLE NEGATIVE, so it SKIPS rather than annotating. The commit
                    # changed this OrchDoc outside every entry region - a plate regeneration,
                    # a meta stamp, an override trailer. `_commit_role` already refuses to
                    # count exactly this on the PUSH edge ("re-stamping its plate line changed
                    # no reasoning"); this is the same judgement on the PULL edge, where it
                    # used to be printed as a note and counted anyway.
                    #
                    # ⭐ `== []` NOT `not touched`, and the difference is the whole safety of
                    # the skip: None means the helper could not read the diff, and that must
                    # REPORT, not skip. A run that never happened looks like a run that passed.
                    continue
            # ⛔ THE NOTE GOES BEFORE THE SUBJECT. The display truncates a finding's detail
            # at 100 characters, so the first version generated the touched-ids correctly and
            # they fell off the end of every printed line - the improvement existing and never
            # reaching the reader, which is this session's other recurring defect. The ids are
            # the decisive half; the commit subject is the nice-to-have.
            moved.append("%s changed %s%s (%s)"
                         % (pth, when[:16].replace("T", " "), note, subject[:44]))

        # PUSH edge: it is timestamped by git, so it resolves same-day ordering
        # that the date-only PULL edge cannot see.
        for eid, _v in pushed.items():
            when, subject = _v[0], _v[1]
            sha = _v[2] if len(_v) > 2 else ""
            if eid != e["id"] or not (rdate is None or when > _as_stamp(rdate)):
                continue
            if sha:
                _created, _plate = _commit_role(sha, path.name, eid)
                # Creating an entry is not the world moving under it, and re-stamping its plate
                # line changed no reasoning. Either way there is nothing to re-read.
                if _created or _plate:
                    continue
            moved.append("work touching %s landed %s (%s)"
                         % (eid, when[:16].replace("T", " "), subject[:48]))

        for d in deps:
            dep = by_id_single.get(d)
            if dep is None:
                if re.match(r"^[A-Z]{1,3}(\d|-)", d):
                    findings.append(Finding(
                        "E-STALEPROSE", e["line"],
                        "entry '%s' depends on '%s', which does not resolve to an entry"
                        % (e["id"], d),
                        "a dependency that names nothing cannot flag anything"))
                continue
            dmoved = last_moved(dep)
            if not dmoved:
                continue
            rday = (rdate or "")[:10]
            if rdate is None or dmoved > rday:
                moved.append("%s moved %s" % (d, dmoved))
            elif dmoved == rday and len(rdate) <= 10:
                # Same day, and the review carries only a DATE. o5: this is the one
                # case the value genuinely cannot answer - so refuse, rather than
                # interpret it in either direction.
                ambiguous.append("%s also moved %s" % (d, dmoved))

        # o5's CONDITIONAL REFUSE, which beats both options o9 offered and the one o8
        # endorsed. Normalising a bare date in EITHER direction still interprets an
        # ambiguous value - that IS the proxy, not the fix. Start-of-day gives a WRONG
        # answer when a review at 18:00 follows a 14:00 measurement; blanket-refuse
        # taxes the ~90% of bare dates nothing contends.
        #
        # So resolve where the value can answer, and refuse only where it cannot:
        #   later day   -> stale     (day granularity resolves it)
        #   earlier days -> current  (resolved)
        #   SAME day     -> refuse   (the only case a bare date cannot answer)
        if ambiguous and not moved:
            findings.append(Finding(
                "E-AMBIGUOUSDATE", e["line"],
                "entry '%s' was reviewed the SAME DAY as contending work, and a bare "
                "date cannot say which came first" % e["id"],
                "; ".join(ambiguous) + " - re-attest with a precise timestamp"))
            continue

        if moved:
            findings.append(Finding(
                "E-STALEPROSE", e["line"],
                "entry '%s' rests on facts that moved after it was last reviewed (%s)"
                % (e["id"], rdate or "never reviewed"),
                "; ".join(moved) + " - walk it and re-attest, naming what changed"))
        elif rdate and attestation:
            # An attestation exists. Is it a rubber stamp?
            if (RUBBER_STAMP_RE.match(attestation)
                    or len(attestation) < MIN_ATTESTATION_CHARS):
                findings.append(Finding(
                    "E-RUBBERSTAMP", e["line"],
                    "entry '%s' attestation says nothing: %r" % (e["id"], attestation[:50]),
                    "name WHAT changed and WHY the conclusion survives it, "
                    "or the check becomes compliance without thought"))

    # --- E-MIXEDSTATE / E-NOOWNER: status belongs on the smallest actionable unit ---
    #
    # the human, via o1, 2026-08-06: "There needs to be EXTREMELY CLEAR MARKING for what is
    # done, what is not done." His reaction to the specimen: "I literally don't know what
    # you are actually reporting here."
    #
    # The specimen was ONE bullet, 1399 characters, carrying five different states,
    # including a flat self-contradiction - "has NEVER run in production" and "RECON DONE
    # ... PROVEN 3x in production" - where BOTH halves carried status markers. An entry
    # check for "does this have a status?" passes it. It is worse than statusless: it is
    # confidently self-refuting, and a reader who sees the leading tick stops looking for
    # actions, which is exactly what the human could not do.
    #
    # ⭐ The contradiction dissolved the moment each claim had to carry its OWN status:
    # the webhook path IS proven 3x, and the PAYMENT leg has never run. Both true, about
    # different things, and unsayable in one container status. THE CONTAINER FORCED A
    # FALSE CHOICE BETWEEN TWO TRUE FACTS - which is the argument for per-item status.
    #
    # Scoped to individual list items, not whole entries: a finding that NARRATES a
    # past-not-done state and its resolution is legitimate prose, and flagging that would
    # be the cry-wolf failure this tool must not have.
    for e in entries:
        if e.get("archived"):
            continue
        body_lines = e["body"].splitlines()[1:]
        for off, raw in enumerate(body_lines):
            if not re.match(r"^\s*[-*+]\s|^\s*\d+\.\s", raw):
                continue
            txt = re.sub(r"`[^`]*`", "", raw)
            notdone = NOTDONE_MARK_RE.search(txt)
            # Remove the not-done spans BEFORE looking for done markers. Otherwise a
            # correctly-formed "NOT DONE" row matches DONE inside it, and the check
            # flags the very rewrite it asked for - o1's fixed row tripped it on the
            # first run. A check that fires on the fix is worse than no check.
            done = DONE_MARK_RE.search(NOTDONE_MARK_RE.sub(" ", txt))

            # ⛔ THIS CHECK USED TO TEACH THE WRONG FORM, AND THAT IS WHY IT KEPT REAPPEARING.
            #
            # It detected a missing `~~` and said *"wrap it in ~~ ~~"*. Tildes give
            # strike-through and NOTHING ELSE - no green tick, no grey. the human's rule is three
            # marks, and greying is the one that makes settled items recede so an open one
            # stands out. So every session that obeyed this remedy produced one mark out of
            # three, and then tripped E-SETTLEDNOTSTRUCK, which asks for the checkbox.
            #
            # ⭐ TWO CHECKS IN ONE FILE GAVE CONTRADICTORY INSTRUCTIONS. That is worse than
            # either being wrong alone: whichever one a session obeys, the other one fires, and
            # the natural read is that the tool is noisy rather than that the advice conflicts.
            # I repeated the bad phrasing to another orchestrator this morning, because the
            # tooling's own words for this rule were "struck through".
            #
            # ONE FORM, EVERYWHERE: `- [x]` renders green-ticked, GREY and struck NATIVELY.
            if done and not notdone and not CHECKED_BOX_RE.search(raw):
                findings.append(Finding(
                    "W-STRIKEDONE", e["line"] + 1 + off,
                    "a done sub-item is not a checked checkbox, so it is not greyed out",
                    "`- [x] the thing` - green tick, GREY and strike-through, all native. "
                    "Not `~~ ~~`, which strikes without greying, and not a <span>, which "
                    "renders as visible literal text here"))

            if done and notdone:
                findings.append(Finding(
                    "E-MIXEDSTATE", e["line"] + 1 + off,
                    "one bullet asserts both '%s' and '%s', so its leading marker "
                    "cannot be true of everything under it"
                    % (done.group(0)[:18], notdone.group(0)[:18]),
                    "split it: one status and one owner per actionable item"))

    for e in entries:
        if e.get("archived"):
            continue
        if status_of(e["body"]) in PLATE_STATUS:
            if not re.search(r"\*\*Owner:\*\*", e["body"], re.I):
                findings.append(Finding(
                    "E-NOOWNER", e["line"],
                    "entry '%s' is not done and names no owner, so the human cannot tell "
                    "whether it is a request to him" % e["id"],
                    "add **Owner:** - his is the only class that costs him anything"))

    # --- THE HUMAN'S CLARITY CHECKS ---
    for e in entries:
        if e.get("archived"):
            continue
        st = status_of(e["body"])
        if not st:
            continue

        # ⛔ A PROSE HEADING MUST NOT ERASE THE SECTION IT SITS INSIDE. o8's DA15 was RESOLVED,
        # on the plate, and invisible to BOTH `check` and `archive` - two guards agreeing an
        # entry was fine while it sat finished in §2.1 Decisions.
        #
        # Cause: a `## Founder's Voice - what actually needs doing` heading between §2.1 and the
        # entry became its section. is_active_section() does not recognise that name, and
        # returns False for anything it does not recognise - so UNKNOWN was treated exactly like
        # THE ARCHIVE, and a done item parked there was invisible rather than flagged.
        #
        # ⭐ The obvious fix - treat unknown sections as live - was MEASURED FIRST and would have
        # fired on 18 entries, at least 15 correctly placed: o9's SPECIMENS holds specimens,
        # o7's "RESOLVED - kept for the record" holds resolved things. It would have told people
        # to dismantle a sensible arrangement.
        #
        # So the rule is narrower and structural: a prose heading does not change which CANONICAL
        # section governs. Measured across every doc: 1 hit, DA15, zero false positives.
        governing = _governing_section(lines, e["line"])
        in_live = is_active_section(e["section"])
        if not in_live and governing and governing.split(".")[0] in ("2", "3"):
            in_live = True
        if st in TERMINAL_STATUS and in_live:
            findings.append(Finding(
                "E-DONEINACTIVE", e["line"],
                "'%s' is %s but sits under '%s', which promises live items"
                % (e["id"], st, (e["section"] or "")[:40]),
                "move it to a resolved section: orchdoc.py archive --doc <doc>"))

        # The heading marker is DERIVED from the field, so it cannot contradict it.
        want = STATUS_MARKER.get(st)
        head = e["title"]
        if want and want not in head:
            wrong = [m for m in STATUS_MARKER.values() if m != want and m in head]
            findings.append(Finding(
                "E-MARKERDRIFT", e["line"],
                "'%s' is %s but its heading %s"
                % (e["id"], st,
                   "carries a different marker" if wrong else "carries no marker"),
                "orchdoc.py normalize --doc <doc> regenerates markers from the field"))

    # --- WALLS OF TEXT (advisory) ---
    para, pstart = [], 0
    in_fence2 = False

    def _flush(para, pstart):
        if not para:
            return
        joined = " ".join(para)
        if joined.lstrip().startswith(("|", ">", "#", "-", "*")):
            return
        if len(joined) > WALL_CHARS:
            findings.append(Finding(
                "W-WALLOFTEXT", pstart,
                "paragraph is %d characters with no breaks" % len(joined),
                "split it: blank lines and real list items, not inline separators"))
        if len(INLINE_ENUM_RE.findall(joined)) >= INLINE_ENUM_MIN:
            findings.append(Finding(
                "W-INLINEENUM", pstart,
                "an enumeration is buried mid-paragraph",
                "put each item on its own line as a real list"))

    for i, raw in enumerate(lines, start=1):
        if raw.lstrip().startswith("```"):
            in_fence2 = not in_fence2
            continue
        if in_fence2:
            continue
        if len(FAKE_BULLET_RE.findall(raw)) >= FAKE_BULLET_MIN and len(raw) > 160:
            findings.append(Finding(
                "W-FAKEBULLETS", i,
                "a middle-dot separator is used as a pseudo-bullet %d times"
                % len(FAKE_BULLET_RE.findall(raw)),
                "it does not render as a list - use '- ' after a line break"))
        if raw.strip():
            if not para:
                pstart = i
            para.append(raw.strip())
        else:
            _flush(para, pstart)
            para = []
    _flush(para, pstart)

    # --- RECORDED OVERRIDES: outstanding, and their reasons held to the same bar ---
    for code, who, when, reason in overrides_in(text):
        if not reason or RUBBER_STAMP_RE.match(reason) or len(reason) < MIN_ATTESTATION_CHARS:
            findings.append(Finding(
                "E-RUBBERSTAMP", 0,
                "override of %s by %s gives no real reason: %r" % (code, who, reason[:40]),
                "an override reason held to a lower bar than an attestation becomes "
                "'needed to ship' within a week"))
        else:
            findings.append(Finding(
                "W-OVERRIDE", 0,
                "%s is overridden by %s since %s" % (code, who, when[:16]),
                reason[:96]))

    # --- W-UNFALSIFIABLE: an attestation that SOUNDS verified but names no falsifier ---
    for e in entries:
        if e.get("archived"):
            continue
        _rd, att = reviewed_of(e["body"])
        if not att or not VERIFY_LANGUAGE_RE.search(att):
            continue
        if HUMAN_RULING_RE.search(att):
            continue        # a ruling, not a measurement: nothing could falsify it
        if not FALSIFIER_RE.search(att):
            findings.append(Finding(
                "W-UNFALSIFIABLE", e["line"],
                "'%s' claims verification but names nothing that would have shown "
                "otherwise" % e["id"],
                "an oracle for the wrong question is still a proxy - say what would "
                "have come back the other way"))

    # --- E-TITLE: the doc says which orchestrator it belongs to, and it is right ---
    #
    # the human, 2026-08-07: "Each doc begins with its name." Six of eight already did; the
    # value of checking is the seventh. The identity must match the FILENAME because a
    # doc titled for one orchestrator in another's file routes a reader to the wrong
    # session, which is the confusion o8 inherited when it took over two roles at once.
    want_num = None
    m = re.search(r"ORCHESTRATOR-DECISIONS-(o\d+)", str(path))
    if m:
        want_num = m.group(1)
    h1 = next((l for l in lines[:40] if l.startswith("# ")), None)
    if want_num:
        if h1 is None:
            findings.append(Finding(
                "E-TITLE", 1, "no H1 identity line",
                "orchdoc.py scaffold --doc %s writes it" % want_num))
        else:
            tm = TITLE_RE.match(h1)
            if not tm:
                findings.append(Finding(
                    "E-TITLE", lines.index(h1) + 1,
                    "H1 is not the canonical identity line",
                    "expected: %s" % canonical_title(want_num, "(role)")))
            elif tm.group(1).lower() != want_num.lower():
                findings.append(Finding(
                    "E-TITLE", lines.index(h1) + 1,
                    "H1 says %s but the file is %s" % (tm.group(1), want_num),
                    "a doc naming the wrong orchestrator routes readers to the wrong session"))

    # --- E-FUTUREDATE: a date that has not happened cannot attest to work that has ---
    #
    # o9 wrote 22 attestation timestamps dated 2026-08-07 during a session that ran
    # 14:21-21:28 on 2026-08-06, and every one passed every gate. This linter checked date
    # FORMAT and date AMBIGUITY and never asked whether the date had OCCURRED - the cheaper
    # half of the job. Worse, the fabricated stamps SATISFIED E-AMBIGUOUSDATE, because a
    # precise timestamp is exactly what that check asks for: the fix for one invariant
    # supplied the input another could not judge.
    _today = _dt.date.today().isoformat()
    for e in entries:
        for m in re.finditer(
                r"\*\*(Attested-by|Reviewed|Recorded|Resolved|Opened)"
                r":\*\*[^\n]*?(\d{4}-\d{2}-\d{2})", e["body"]):
            if m.group(2) > _today:
                findings.append(Finding(
                    "E-FUTUREDATE", e["line"],
                    "entry '%s' is attested %s, which is AFTER today (%s)"
                    % (e["id"], m.group(2), _today),
                    "a date that has not happened cannot attest to work that has"))
                break

    # --- E-BADID: a heading that LOOKS like an entry but does not parse as one ---
    #
    # `### D-1 - something` matched no id pattern, so it was SILENTLY SKIPPED: present on
    # disk, absent from `check` and from the generated plate. An entry the human can read
    # but the tool cannot see is the worst outcome this tool has, because every guarantee
    # it makes is scoped to entries it parsed.
    #
    # The rule the rest of the file already follows: WHEN A PARSER CANNOT UNDERSTAND
    # SOMETHING SHAPED LIKE ITS INPUT, IT MUST COMPLAIN, NOT SKIP.
    for i, ln in enumerate(lines):
        m = re.match(r"^###\s+(?:\W+\s*)?([A-Za-z][\w.-]{0,6})\s*[-–:]", ln)
        if not m:
            continue
        tok = m.group(1)
        if ID_RE.match(tok) or not re.match(r"^[A-Z]{1,3}[-\d]", tok):
            continue
        findings.append(Finding(
            "E-BADID", i + 1,
            "heading looks like an entry but '%s' is not a valid id, so it is INVISIBLE "
            "to check and to the generated index" % tok,
            "ids are LETTERS then DIGITS (D1, F12, DA3) - not '%s'" % tok))

    # --- E-WRONGSECTION: an id whose PREFIX contradicts the section it sits in ---
    #
    # the human's ruling, 2026-08-11 (via o10): **`T<n>` is HIS to-do namespace.** An orchestrator's
    # own work is `W<n>` in §3. *"T3 has to mean one thing when he says it."*
    #
    # KIND_SECTION_NUM already encoded the mapping - T to §2.3, W to §3 - and nothing checked
    # it, so o10 scaffolded T1-T5 into §3 and only found out when the human read it. **A mapping the
    # tool knows and never enforces is a naming convention, and this workspace has already
    # demonstrated that conventions drift into three spellings of the same thing.**
    #
    # ⭐ It is not a tidiness rule. A prefix is what the human SAYS OUT LOUD - "T3" - and if T means
    # his to-do in one section and an orchestrator's own work in another, the shorthand he uses
    # to point at things stops resolving. The cost lands on the person the doc exists for.
    # ⛔ SCOPED TO THE HUMAN'S ACTUAL RULING, NOT A GENERALISATION OF IT. The first version enforced
    # the whole KIND_SECTION_NUM map and produced 19 hits, most of them FALSE: `D1` in §99 is the
    # ARCHIVE and belongs there, `A1` in §5 is o7's guards namespace, `F8` in §99.1 is an
    # archived finding. It would have told three orchestrators to dismantle correct structure -
    # the false-positive direction that costs you the behaviour, for the fourth time this week.
    #
    # the human ruled ONE thing: T is HIS namespace. That is the rule; the rest of the map is a
    # routing hint for `add`, not a constraint anyone agreed to.
    for e in entries:
        if e.get("archived") or not re.match(r"^T\d+$", e["id"]):
            continue
        sec = (e.get("section") or "").lstrip("# ")
        here = re.match(r"^§\s*([\d.]+)", sec)
        if not here or here.group(1).split(".")[0] in ("2", "99"):
            continue
        findings.append(Finding(
            "E-WRONGSECTION", e["line"],
            "%s uses the T namespace but sits in §%s - T<n> is THE HUMAN'S to-do list, so a T id "
            "outside §2.3 means 'T3' points at two different things depending on who says it"
            % (e["id"], here.group(1)),
            "an orchestrator's own work is W<n> in §3. Rename this entry to W<n>, or move it "
            "to §2.3 if it is genuinely an ask for the human"))

    # --- E-LOOSEINPARENT: entries parked in a container section, outside every subsection ---
    #
    # the human, 2026-08-11, on o1's doc: *"you have a MASSIVE wall of items that sit between §2 and
    # §2.1. There should NOT be anything there... why are you putting to-do items and decisions
    # in this space and NOT where they are obviously supposed to go???"* And, on the pattern:
    # *"This is a game of whack-a-mole. We need to codify this into the OrchDoc process."*
    #
    # ⭐ HE IS RIGHT ABOUT THE WHACK-A-MOLE, AND THIS IS THE GENERAL FORM. A section that HAS
    # subsections is a CONTAINER: its own body is for its heading and a line of description,
    # nothing else. An entry parked in the container sits outside every subsection - so no
    # per-subsection rule reaches it, `plate` does not index it, and `archive` does not sort it.
    # **It is invisible to the tooling by position rather than by content**, which is why it
    # accumulates rather than being caught, and why fixing symptoms one at a time never ends.
    #
    # ⛔ AND IT IS NOT A TIDINESS CHECK - o7 established that empirically. All THREE items parked
    # in their §2 container were DEAD: a ruling index still saying go-live needed "one PR (#70)
    # then one command" ten days after #70 merged and production promoted; a REVIEW ask whose
    # links were already in §1; and a READ ask belonging to a decision that was PAUSED.
    #
    # ⭐ Three for three, and the reason is structural: **a loose bullet belongs to no entry, and
    # every check in this file is triggered BY AN ENTRY.** So it is not merely unsorted - it is
    # exempt from staleness, from status, from archive, from the sweep. **The container is where
    # text goes to stop being maintained**, and it rots there while still looking like part of
    # the plate. Same shape as F71: absence of a trigger produces silence, not a finding.
    #
    # Measured: 293 lines sit in parent bodies across the fleet, including 5 entries in o9's own
    # §99. Prose and a description line are legitimate there (o1's own "items live in §2.1/2.2/
    # 2.3 below" rule lives exactly there, correctly).
    _parent_heads = [(i, m.group(1)) for i, l in enumerate(lines)
                     if (m := SECTION_RE.match(l)) and "." not in m.group(1)]
    _all_heads = [(i, m.group(1)) for i, l in enumerate(lines) if (m := SECTION_RE.match(l))]
    for ln, num in _parent_heads:
        sub = next((h for h in _all_heads
                    if h[0] > ln and h[1].startswith(num + ".")), None)
        if not sub:
            continue                     # no subsections: the body is legitimately its own
        stray = [i + 1 for i in range(ln + 1, sub[0])
                 if re.match(r"^#{3,4}\s", lines[i])]
        # ⛔ AND TOP-LEVEL BULLETS, WHICH IS THE FORM THE HUMAN ACTUALLY POINTED AT. The first
        # version flagged only `###` headings and missed o7's §2 entirely - its container body
        # carries "👀 REVIEW - the offer pages" and "📖 READ - the two guides", which are ASKS
        # wearing a bullet instead of a heading. A rule that only recognises the tidy shape of a
        # violation misses the messy one, and the messy one is what accumulates.
        #
        # A blockquote index or a rule line is legitimate here, so only UNINDENTED bullets count.
        asks = [i + 1 for i in range(ln + 1, sub[0])
                if re.match(r"^[-*+]\s", lines[i])]
        if stray or asks:
            what = []
            if stray:
                what.append("%d entr(ies)" % len(stray))
            if asks:
                what.append("%d loose bullet(s)" % len(asks))
            findings.append(Finding(
                "E-LOOSEINPARENT", ln + 1,
                "§%s holds %s in its own body, above §%s - a loose bullet belongs to NO entry, "
                "so every check here (which is triggered by an entry) is blind to it and it is "
                "never swept, statused or archived" % (num, " and ".join(what), sub[1]),
                "a section WITH subsections is a container: heading, and a description or index "
                "at most. Move each item into the subsection that owns it - a decision to §%s.1, "
                "a question to .2, a to-do to .3. Lines %s"
                % (num, ", ".join(str(n) for n in sorted(stray + asks)[:6]))))

    # --- E-PLATEHISTORY: the reasoning trail, filed on the human's plate ---
    #
    # the human, 2026-08-11, on o7's §2: *"You are taking notes INSIDE the §2 components. THAT IS
    # EXTRAORDINARILY difficult for me to parse. You should be putting those in Findings. I need
    # the decision that I need to make visible, and the basic info that I need to understand in
    # order to make the decision. DO NOT KEEP A RUNNING HISTORY THERE."*
    #
    # Measured across the corpus before writing this: o8's plate runs 61 lines per DECISION with
    # 10 of 11 entries carrying history; o7's D21 is 76 lines. The section he reads in order to
    # DECIDE is three times denser than the section built to hold detail.
    #
    # ⭐ THE DISTINCTION IS WHAT MAKES THIS CHECKABLE: a §2 entry is a QUESTION, a finding is a
    # RECORD. Appending history to a question does not enrich it, it buries it - a reader looking
    # for "what do I need to do" reads past a paragraph about how the last version was wrong.
    #
    # ⛔ AND IT CUTS AGAINST A HABIT THIS WORKSTREAM SPENT ALL WEEK REINFORCING. Recording
    # provenance in place - struck text, retraction blocks, who caught what - is genuinely
    # valuable and it is FINDINGS material. Right thing, wrong section.
    #
    # o7 proposed two checks and this is the better one: **do not measure the SIZE of the text,
    # measure what KIND of text it is.** A ceiling says an entry is fat; this says what to move.
    # Same shape as `asserted`.
    for e in entries:
        sec = (e.get("section") or "").lstrip("# ")
        if e.get("archived") or not sec.startswith("§2"):
            continue
        hits = []
        in_settled = False
        in_strike = False          # a ~~span~~ left open by the previous line
        for off, raw in enumerate(e["body"].splitlines()[1:], start=1):
            # Text INSIDE a multi-line struck span is part of the struck item, even when the
            # wrap happens to begin with a dash. L309 of o8's doc is the second half of a
            # settled sub-item's sentence and was flagged as history on its own.
            was_in_strike = in_strike
            if raw.count("~~") % 2:
                in_strike = not in_strike
            if was_in_strike:
                continue
            is_bullet = bool(re.match(r"^\s*(?:[-*+]|\d+\.)\s", raw))
            # ⛔ A SETTLED SUB-ITEM IS NOT HISTORY. It is the CURRENT state of a live item, and
            # the human explicitly wants those visible on the plate - struck, greyed, still there.
            #
            # The first version excluded only `- ~~…`, so it flagged `- ✅ **DONE** - ~~(a) the
            # 4 hybrids…~~`, which is the settled form with text between the bullet and the
            # tildes. That is the expensive direction: the check would have told o8 to dismantle
            # exactly the markup the rule asks for. A bullet carrying a DONE marker is settled,
            # whatever else is on the line.
            if is_bullet:
                in_settled = bool(CHECKED_BOX_RE.search(raw)
                                  or DONE_MARK_RE.search(NOTDONE_MARK_RE.sub(" ", raw))
                                  or re.match(r"^\s*[-*+]\s*~~", raw))
                if in_settled:
                    continue
            elif in_settled and raw.startswith((" ", "\t")) and raw.strip():
                # A wrapped continuation of a settled sub-item is still that sub-item - L283 was
                # the second half of L282's sentence and got flagged on its own.
                #
                # ⚠️ INDENTED only. Treating ANY non-blank line as a continuation swallowed the
                # flush-left paragraph that follows a settled bullet, so history written directly
                # under a done item became invisible - a false NEGATIVE introduced while fixing a
                # false positive. The fixture caught it immediately, which is the argument for
                # writing one per code.
                continue
            elif not raw.strip():
                in_settled = False
            _clean = re.sub(r"`[^`]*`", "", raw)
            m = PLATE_HISTORY_RE.search(_clean)
            # ⛔ A QUESTION IS NOT A HISTORY. o11 was flagged for the line `is this done,
            # superseded, or live?` - the word naming one of the CATEGORIES being asked about,
            # in a decision that legitimately lists them. Plate history is always an assertion
            # about how the record used to read; a clause ending in `?` is asking, not telling.
            #
            # ⭐ The pattern cannot tell "this was superseded" from "superseded is one of the
            # buckets" by the word alone, and the sentence's own punctuation settles it. Only
            # the clause containing the match counts - a line can ask a question and then assert
            # something afterwards.
            if m and _clean[m.start():].split(".")[0].rstrip().endswith("?"):
                m = None
            if m:
                hits.append((e["line"] + off, m.group(0)[:22]))
        if hits:
            findings.append(Finding(
                "E-PLATEHISTORY", e["line"],
                "%s is on the human's plate but carries %d line(s) of reasoning HISTORY, so the "
                "decision he has to make is buried in how it got here" % (e["id"], len(hits)),
                "a §2 entry answers two questions and stops - what do you need to decide, and "
                "the minimum needed to decide it, plus a recommendation. Move the history to a "
                "finding and leave a pointer. At: %s"
                % ", ".join("L%d(%s)" % (n, w) for n, w in hits[:4])))

    # --- E-STUBLEFT / E-EMPTYLINKS: the scaffold's own placeholders, still sitting there ---
    #
    # the human, 2026-08-10, on seeing them in live docs: *"Purpose and Links sections were not
    # actually filled out."* Five of eight docs still carried the literal TODO; three had an
    # empty §1 while citing 7, 32 and 40 assets elsewhere in their own prose.
    #
    # ⭐ THIS IS THE SAME SHAPE AS EVERY OTHER DEFECT THIS WEEK. `scaffold` writes a stub, the
    # stub is VALID markdown, every invariant parses it happily - so an UNFILLED section and a
    # FILLED one are indistinguishable to the tooling. The scaffold that exists to make a doc
    # complete is the thing leaving it incomplete, and nothing said so for weeks.
    #
    # ⛔ An empty §1 is not cosmetic. Its own generated subtitle promises "every doc and URL
    # this orchestrator owns", so an empty one is a FALSE CLAIM that the orchestrator owns
    # nothing - and §1 is the first place a reader looks for an asset.
    # Attestations blanked first - a `--because` that quotes the placeholder is EVIDENCE THE
    # PLACEHOLDER WAS REMOVED, not the placeholder. See without_attestations().
    _live = without_attestations(text)
    if "TODO: one paragraph" in _live:
        _ln = next((i + 1 for i, l in enumerate(_live.split("\n"))
                    if "TODO: one paragraph" in l), 1)
        findings.append(Finding(
            "E-STUBLEFT", _ln,
            "the scaffold's Purpose placeholder is still here, so the doc opens by telling "
            "its reader it was never finished",
            "one paragraph, authored - what this orchestrator is FOR. It is the only part of "
            "the spine that cannot be generated, which is why it is the part that gets left"))

    _span1 = section_span(lines, "1")
    if _span1:
        _body1 = [l for l in lines[_span1[0] + 1:_span1[1]]
                  if l.strip() and not re.fullmatch(r"_.*_", l.strip())]
        try:
            _assets = harvest_assets(lines, path)
        except Exception:
            _assets = {}
        # ⛔ COUNT ONLY ASSETS THAT RESOLVE. The harvester picks up every path-shaped string,
        # including ILLUSTRATIVE ones from prose - o9's own doc contributed `x/y.md` and
        # `dir/file.py` from a worked example about shell path mangling. Counting those inflates
        # the finding, and a check that overstates its evidence is one people learn to discount.
        _real = {k: v for k, v in _assets.items() if v.get("exists") is not False}
        if not _body1 and _real:
            findings.append(Finding(
                "E-EMPTYLINKS", _span1[0] + 1,
                "§1 is empty while this doc cites %d resolvable asset(s) in its own prose, so "
                "it claims to list every doc and URL this orchestrator owns and lists none"
                % len(_real),
                "orchdoc.py links --doc <doc> harvests them and proposes the table - §1 is a "
                "COLLECTION task, not an authoring one"))

    # --- E-IDSHAPE / E-IDORDER: one id form, and numbers you can scan by ---
    #
    # the human, 2026-08-10, on seeing D1, DA1, D-PAUSE and T-VENDORDISC in one workspace:
    # *"Docs are deciding on their own how to label... We need to formalize this."* and
    # *"D21 might come before D5 with D1 between them... I have to scan for the right instance
    # because the order can't be trusted."*
    #
    # Both are the same cost in different clothes: a reader who cannot PREDICT where an entry
    # is has to READ EVERYTHING to find it. Sequential numbering means you stop when you arrive;
    # unordered numbering means you stop only when the section ends.
    _SHAPE_OK = re.compile(r"^[A-Z]{1,3}\d+$")
    _order = {}
    for e in entries:
        eid = e["id"]
        if not _SHAPE_OK.match(eid):
            findings.append(Finding(
                "E-IDSHAPE", e["line"],
                "'%s' is not the canonical id form, so the workspace has several ways to name "
                "the same kind of thing" % eid,
                # ⛔ DO NOT NAME A COMMAND THAT DOES NOT EXIST. This said "orchdoc.py
                # renumber --doc <doc>" for as long as the check has existed, and there is no
                # such subcommand. Fourth advertised-then-unavailable remedy in this toolchain.
                #
                # ⭐ And it was never an oversight: the design spec records that a rename
                # touches cross-references, 17 of them in OTHER orchestrators' docs, so it is a
                # multi-doc operation one doc's repair cannot safely finish. FLAG-with-proposal
                # is what shipped. The message just kept pointing at the tool deliberately not
                # built - so it now says what to do and WHY nothing does it for you, because a
                # reason is what stops the next reader assuming the command is merely missing.
                "ids are PREFIX + NUMBER then a summary in the title: "
                "`D7 - pause cues`, not `D-PAUSE`. There is no automated rename, on purpose: "
                "the references live in other orchestrators' docs, so renaming is a "
                "cross-doc change one doc cannot safely finish. Propose the new id to the "
                "docs that cite it, then edit both sides together."))
            continue
        m = re.match(r"^([A-Z]{1,3})(\d+)$", eid)
        # Ordering is a claim only WITHIN one section and one prefix. Across sections an entry
        # legitimately leaves a gap when it is archived, and D3 sitting above F1 is a different
        # question entirely - flagging either would fire on correct documents.
        _order.setdefault(((e.get("section") or "?"), m.group(1)), []).append(
            (e["line"], int(m.group(2)), eid))

    for (sec, pre), items in sorted(_order.items()):
        items.sort()
        for (ln_a, n_a, id_a), (_ln_b, n_b, id_b) in zip(items, items[1:]):
            if n_b < n_a:
                findings.append(Finding(
                    "E-IDORDER", _ln_b if False else items[items.index(
                        (_ln_b, n_b, id_b))][0],
                    "%s appears after %s in '%s', so the numbers do not run in order and the "
                    "section has to be scanned rather than read to the right place"
                    % (id_b, id_a, str(sec)[:34]),
                    "orchdoc.py reorder --doc <doc> sorts each section; it moves entries "
                    "whole and verifies the bytes are unchanged"))

    # --- E-CONFLICT: unresolved merge-conflict markers ---
    #
    # A doc containing "<<<<<<<" / "=======" / ">>>>>>>" has TWO versions of some entry in
    # it and no one has chosen. Every status the tool then reports is drawn from whichever
    # side happened to come first - a confident answer computed from an unresolved file.
    for i, ln in enumerate(lines):
        if re.match(r"^(<{7}|>{7})\s", ln) or re.match(r"^={7}$", ln):
            findings.append(Finding(
                "E-CONFLICT", i + 1,
                "unresolved merge-conflict marker - this doc holds two versions and "
                "every status read from it is arbitrary",
                "resolve the conflict before trusting anything in this file"))
            break

    # --- W-EMPTYPROMISE: a scaffolded section that promises content and holds none ---
    #
    # the human screenshotted this doc's own §1 and asked whether it was accurate. It was a
    # heading plus the scaffold's italic note - two lines, zero links - under a title
    # promising "every doc and URL this orchestrator owns", which reads as OWNS NONE.
    #
    # ⭐ A SCAFFOLDED SECTION IS A CLAIM. `scaffold` creates the heading, so the tool
    # manufactures the promise and then relies on someone remembering to honour it - which
    # is discipline, and this whole workstream exists because discipline does not hold.
    # `check` passed it happily: nothing malformed, no marker drifted, no status wrong.
    # Structural correctness and informational emptiness are ORTHOGONAL, and every other
    # invariant here measures only the first.
    #
    # Fires only when the doc DOES cite assets elsewhere - an orchestrator that genuinely
    # owns no links should not be nagged.
    for num, title, note in SCHEMA_SECTIONS:
        if num != "1":
            continue
        sp = section_span(lines, num)
        if not sp:
            continue
        body = [l for l in lines[sp[0] + 1:sp[1]]
                if l.strip() and not re.fullmatch(r"_.*_", l.strip())]
        if body:
            continue
        cited = harvest_assets(lines, path)
        if len(cited) >= 3:
            findings.append(Finding(
                "W-EMPTYPROMISE", sp[0] + 1,
                "§1 promises 'every doc and URL this orchestrator owns' and is EMPTY, "
                "while this doc cites %d asset(s) elsewhere" % len(cited),
                "run `orchdoc.py links --doc <doc>` - it harvests them and prints a "
                "paste-ready table; §1 is a COLLECTION task, not an authoring one"))

    # --- W-BADLINEREF: a `line N` pointer that does not resolve (ADVISORY) ---
    #
    # o2's original specimen was real: "**A1** (line 28)" while A1 sat at line 42. But run
    # across the fleet, 6 of 8 hits were FALSE - and one of them was this tool's own
    # override attestation, which quotes the bad anchor in order to explain the fix.
    #
    # ⛔ The dangerous class was o1's `| R2a | zero-shot, C line 1, neutral ref |`. That is
    # line 1 of voice-script C - a reference into ANOTHER artifact, and correct. Telling an
    # owner to "fix" it means editing accurate citations to satisfy a lint: CONTENT DAMAGE
    # CAUSED BY A CHECKER, which is strictly worse than the defect it was built to catch.
    #
    # ⭐ And the perverse incentive o2 named: a GOOD `--because` quotes the bad value, so a
    # blocking version of this rule penalised exactly the specificity the attestation bar
    # demands. A check that corrodes another guard is worse than no check.
    #
    # So: ADVISORY until the FP rate is measured, and narrowed to fire only on things that
    # are actually anchors INTO THIS DOC.
    own_ids = {e["id"] for e in entries}
    in_fence = False
    for i, ln in enumerate(lines_live):
        if ln.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or ln.lstrip().startswith("<!--"):
            continue                      # generated audit records are not claims
        # Strip inline code AND QUOTED SPANS. Prose that QUOTES a bad anchor in order
        # to explain it - a post-mortem, a rule example, an attestation - is a
        # MENTION, not a POINTER. o2's class 1, and the same distinction as a doc
        # that discusses its own markers: describing a thing is not doing it.
        probe = re.sub(r"`[^`]*`", "", ln)
        probe = re.sub(r"[\"“‘'][^\"”’']{0,90}[\"”’']", "", probe)
        for m in re.finditer(
                r"\*{0,2}([A-Z]{1,3}[-]?\d{1,3})\*{0,2}[^|\n]{0,18}?\(?\bline\s+(\d{1,4})\b",
                probe):
            eid, target = m.group(1), int(m.group(2))
            if eid not in own_ids:
                continue                  # a table row label is not an anchor
            span = probe[m.start():m.end()]
            if ARTIFACT_RE.search(span):
                continue                  # a reference into ANOTHER artifact
            if not (1 <= target <= len(lines)):
                findings.append(Finding(
                    "W-BADLINEREF", i + 1,
                    "points at line %d, past the end of the doc (%d lines)"
                    % (target, len(lines)),
                    "cite the heading; a line number rots on any edit above it"))
                continue
            window = "\n".join(lines[max(0, target - 2):target + 2])
            if not re.search(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(eid), window):
                actual = [j + 1 for j, l2 in enumerate(lines)
                          if l2.startswith("#") and re.search(
                              r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(eid), l2)]
                findings.append(Finding(
                    "W-BADLINEREF", i + 1,
                    "says '%s ... line %d', but line %d does not contain %s%s"
                    % (eid, target, target, eid,
                       " (it is at line %d)" % actual[0] if actual else ""),
                    "name the heading instead - a line number rots on any edit above it"))

    # --- E-ONEH1: exactly one H1, because a second reads as a second document ---
    #
    # the human, 2026-08-07: "if that is a possibility for any unknown future grep, let's
    # revert." A revert alone would restore today's state and leave the next session free
    # to reintroduce the same shape for the same plausible reason - which is the decay
    # this tool exists to stop. The invariant is the fix; the revert was only the cleanup.
    # SKIP FENCED CODE. A doc that QUOTES a heading - this one quotes the bad skeleton it
    # replaced - is not declaring one. The check fired on its own evidence, which is the
    # third variant of "a block the reader does not interpret must not be parsed as
    # structure" (after the plate marker and the findings index).
    h1s, _fence = [], False
    for i, l in enumerate(lines):
        if l.lstrip().startswith("```"):
            _fence = not _fence
            continue
        if not _fence and l.startswith("# "):
            h1s.append(i)
    if len(h1s) > 1:
        findings.append(Finding(
            "E-ONEH1", h1s[1] + 1,
            "%d H1 headings; a second reads as a second DOCUMENT title" % len(h1s),
            "put the role in the same heading: %s" % canonical_title("oN", "(role)")))

    # --- E-SCHEMA: the canonical spine must be present and in order ---
    #
    # the human: "Why don't we have one? We need something consistent." The charter named
    # this first - "the consistent foundation" - and o9 built invariants and commands
    # without ever giving the docs a shared skeleton, so each still had its own shape.
    nums = []
    for ln in lines:
        m = SECTION_RE.match(ln)
        if m:
            nums.append(m.group(1))
    # ⛔ A DOC WITH NO SPINE AT ALL IS NOT EXEMPT - IT IS THE WORST CASE.
    #
    # `if nums:` below reads "only judge docs that have opted into the schema", and the effect
    # is an exact inversion: the further a doc is from the standard, the less of the standard
    # applies to it. o2 and o3 have NO numbered sections, so E-SCHEMA never fired, §1 could not
    # be checked for being empty because there is no §1, and both sat silently non-conforming
    # while every schema doc was being held to eight invariants.
    #
    # ⭐ Same failure as `is_active_section` returning False for an unrecognised name, and as an
    # advisory printing a bare count: UNKNOWN rendered as FINE. A checker must say when it
    # cannot check - one clear finding naming the state, not silence and not eight symptoms.
    if not nums and re.search(r"ORCHESTRATOR-DECISIONS-", path.name):
        findings.append(Finding(
            "E-LEGACYDOC", 1,
            "this doc has NO §-numbered sections, so the schema invariants cannot run on it - "
            "it is not passing them, it is exempt from them",
            "orchdoc.py migrate --doc <doc> brings it onto the spine without losing content; "
            "until then most of what `check` reports about other docs is silent about this one"))

    if nums:                      # only judge docs that have opted into the schema
        want = [n for n, _t, _d in SCHEMA_SECTIONS]
        missing = [n for n in want if n not in nums]
        if missing:
            findings.append(Finding(
                "E-SCHEMA", 0,
                "missing schema section(s): %s" % ", ".join(missing),
                "orchdoc.py scaffold --doc <doc> writes the canonical spine"))
        # ORDER is checked across EVERY numbered section, including custom ones - that is
        # what makes "COMPLETED is at the bottom" true rather than merely intended, since
        # 99 sorts below anything an orchestrator adds in 6 to 98.
        ordered = list(nums)
        if ordered != sorted(ordered, key=lambda x: [int(p) for p in x.split(".")]):
            findings.append(Finding(
                "E-SCHEMA", 0,
                "schema sections are out of canonical order",
                "a numbered spine is only an anchor if the numbers ascend"))

    # --- E-SCATTERED: like goes with like, so a human can find it ---
    #
    # the human, 2026-08-06: he saw Q1 and D1 adjacent at the top, then had to scroll past
    # many unrelated sections to find D2, "with no obvious location or section to scroll
    # to". A reader doing a VISUAL search needs one place per kind. Entries of the same
    # kind must therefore live under a single section - decisions together, questions
    # together - not interleaved with findings and specimens down the length of the doc.
    kinds = defaultdict(set)
    for e in entries:
        if e.get("archived"):
            continue
        m = re.match(r"^([A-Z]+)", e["id"])
        if not m:
            continue
        # ⛔ The LIVE/COMPLETED split is the schema's whole design, not scatter. A ruled
        # decision belongs in §99.1 and an open one in §2.1 - that is deliberate, and it
        # is what `archive` exists to do. Counting them as "spread across 2 sections"
        # made this rule fire on a CORRECTLY archived document, so following the schema
        # produced a blocking finding.
        #
        # ⭐ Fourth reader broken by the numbered schema, after `add`, is_active_section()
        # and `archive`. The lesson is now unambiguous: a structural change must be
        # followed into EVERY consumer of that structure, and "it still parses" is not
        # evidence that it still means the same thing.
        sec = e["section"]
        sm = SECTION_RE.match(sec if sec.lstrip().startswith("#") else "## " + sec)
        if sm and sm.group(1).split(".")[0] == ARCHIVE_SECTION:
            continue                      # archived-by-schema: not part of the live group
        kinds[m.group(1)[0]].add(sec)
    for prefix, label in KIND_ORDER:
        secs = kinds.get(prefix, set())
        if len(secs) > 1:
            findings.append(Finding(
                "E-SCATTERED", 0,
                "'%s' entries are spread across %d sections, so a human cannot find "
                "them by scrolling to one place" % (prefix, len(secs)),
                "put them all under one section (%s): %s"
                % (label, "; ".join(sorted(s[:38] for s in secs)))))

    # --- E-PLATEDRIFT: the rendered index must equal what regeneration would produce ---
    #
    # o6's catch, and it is the one that closes the loop. Generating the index into the
    # file is not enough: a human can edit the rendered block afterward and the second
    # copy is straight back. o6's own header drifted for exactly this reason. So the
    # check REFUSES when the block does not match its own derivation - the refusal-oracle
    # shape applied to a generated artifact. Trust the derivation, never the rendered copy.
    _span, _why = plate_span(lines)
    if _why:
        findings.append(Finding("E-BADMARKER", 0, _why,
                                "readers cannot agree where derived content begins"))
    elif _span:
        try:
            start, stop = _span
            rendered = lines[start:stop + 1]
            expected = build_plate_block(entries, lines)
            if rendered != expected:
                findings.append(Finding(
                    "E-PLATEDRIFT", start + 1,
                    "the generated index does not match what regeneration produces",
                    "hand-edited or stale; run `orchdoc.py plate --doc <doc>`"))
        except ValueError:
            pass

    # --- E-ARCHIVEDMARKER: an archived entry must not carry a live-looking marker ---
    # o8's rule, derived from three instances in one day: a superseded entry kept inside a
    # <details> block is invisible in rendered markdown but fully visible to grep, to a
    # linter, and to anyone scanning - so it still reads as live state. Preserve the
    # REASONING, never the STATUS.
    for e in entries:
        # ⛔ `id_demoted` means the id was found in the `_(was ... )_` suffix, which is what
        # `archive --commit` WRITES. Firing there refused the tool's own correct output, and
        # the printed remedy ("strip the id") could not be performed without deleting the
        # suffix the parser resolves `Touches:` trailers through. Measured by o7, 2026-08-17.
        if e.get("archived") and not e.get("id_demoted"):
            findings.append(Finding(
                "E-ARCHIVEDMARKER", e["line"],
                "archived entry still carries the live-looking id '%s' at the FRONT of its "
                "heading" % e["id"],
                "move the id into the archive suffix - `_(was %s - RESOLVED)_` - rather than "
                "deleting it; the id must stay readable or every commit trailer naming this "
                "entry becomes a dangling pointer" % e["id"]))

    # ⛔ TWO IDS IN ONE HEADING: a live-looking one in front, and a different one inside the
    # archive suffix. The parser resolves the FRONT one, so the entry answers to an id its own
    # suffix says it does not have - and the archived entry's real id resolves to nothing.
    #
    # ⭐ THIS WAS SILENT, WHICH IS WHY IT NEEDS ITS OWN FINDING. o10's `W10 - W0: the
    # morning-energy H1 ...` archived to `W0: the morning-energy H1 ... _(was W10 - DONE)_`,
    # and check then reported E-IDORDER and E-MARKERDRIFT on a phantom entry `W0` that no
    # human had written, with remedies that could not fix the real cause. `archive` no longer
    # produces this shape; this is here so a hand-written one cannot hide either.
    for i, raw in enumerate(lines, start=1):
        m = re.match(r"^#{1,6}\s+(.*)$", raw)
        if not m:
            continue
        _suf = ARCHIVED_ID_RE.search(m.group(1))
        _lead = ID_RE.match(strip_decoration(m.group(1)))
        if _suf and _lead and _lead.group(1) != _suf.group(1):
            findings.append(Finding(
                "E-ARCHIVEDMARKER", i,
                "this heading names TWO entries - '%s' at the front and '%s' in its archive "
                "suffix - so the parser reads it as a live '%s' and '%s' resolves to nothing"
                % (_lead.group(1), _suf.group(1), _lead.group(1), _suf.group(1)),
                "the archived id is the real one. Lead the title with its status word - "
                "`### %s - %s` - so the front of the heading cannot parse as an id"
                % (status_of("\n".join(lines[i - 1:i + 6])) or "DONE", m.group(1)[:40])))

    # --- E-SELFCLAIM: a heading must not make a claim about its own contents ---
    for s in sections:
        hit = False
        for pat, why in SELF_CLAIM_PATTERNS:
            if pat.search(s["title"]):
                findings.append(Finding(
                    "E-SELFCLAIM", s["line"],
                    "section heading %s" % why,
                    s["title"][:100]))
                hit = True
                break
        if hit:
            continue
        # Extended per o8: a STATE or COUNT claim after the separator is the same defect
        # wearing different words. The name itself is never flagged.
        parts = HEADING_SPLIT_RE.split(strip_decoration(s["title"]), maxsplit=1)
        if len(parts) > 1:
            tail = parts[1]
            # The state word must LEAD the tail. An assertion comes straight after the
            # separator ("THE EXIT - BUILT and RUN"); a documented VALUE appears deeper
            # in a naming phrase ("CONVENTION - marking drafts WIP vs READY", which is
            # o8's own heading and a false positive the first version flagged). Firing
            # on a document that discusses status vocabulary is how a checker gets
            # switched off, so this deliberately under-fires rather than cry wolf.
            lead = " ".join(tail.split()[:2])
            # A dated verification result anywhere in the heading, not just the
            # lead: o1's specimen sat in a trailing parenthetical.
            m = (HEADING_STATE_RE.search(lead) or HEADING_COUNT_RE.search(tail)
                 or HEADING_DATED_CLAIM_RE.search(s["title"]))
            if m:
                findings.append(Finding(
                    "E-SELFCLAIM", s["line"],
                    "section heading asserts a state or count ('%s'), which drifts silently"
                    % m.group(0),
                    s["title"][:100]))

    # --- Per-line scans, skipping fenced code ---
    sha_candidates = []
    in_fence = False
    for i, raw in enumerate(lines_live, start=1):
        if raw.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        # NO em-dash check. the human's scope ruling, 2026-08-06: em-dashes are ALLOWED in
        # internal writing, and an OrchDoc is internal. The ban exists to avoid the
        # "AI-giveaway" backlash among internet-consuming humans, which only exists where
        # the public reads. 643 advisory hits across these docs were masking real
        # defects, which is the exact "cries wolf" failure this tool must not have.
        # SoT: memory/writing_style_avoiding_ai_cliches.md section 4.

        m = LINE_CITE_RE.search(raw)
        if m and "line" in m.group(0).lower():
            findings.append(Finding(
                "W-LINECITE", i, "citation by line number rots on any edit above it",
                raw.strip()[:90]))

        # A hex string the surrounding words identify as a content hash is not a commit
        # citation at all, so it is neither a rot risk nor a dead pointer. Classify by
        # context, never by shape alone.
        if NOT_A_COMMIT_RE.search(raw):
            continue
        for sm in SHA_CITE_RE.finditer(raw):
            tok = sm.group(1)
            # Require a letter so plain numbers and dates are not read as SHAs. Whether
            # this is REALLY a commit is settled by git below, not by guessing here -
            # 'ba9ce86c0000be61' looks like a SHA and is a vendor id.
            if len(tok) >= 7 and SHA_HAS_LETTER.search(tok):
                sha_candidates.append((i, tok))
                break  # one candidate per line is enough

    # --- E-DEADREF / W-SHACITE: settle every SHA candidate against git, in ONE call ---
    # A SHA that still resolves is a rot RISK (advisory). A SHA that no longer resolves
    # is a dead pointer the reader cannot follow (blocking). Verified, never guessed.
    if sha_candidates:
        toks = [t for _, t in sha_candidates]
        resolved = [False] * len(toks)
        settled = False
        probe = "\n".join("%s^{commit}" % t for t in toks) + "\n"
        for repo in citable_repos():
            if not (repo / ".git").exists():
                continue
            rc, out, _ = git_stdin(["cat-file", "--batch-check"], probe, cwd=repo)
            rows = out.splitlines()
            if rc != 0 and not rows:
                continue
            if len(rows) != len(toks):
                continue  # cannot align the answer to the question: ignore this repo
            settled = True
            for i, ln in enumerate(rows):
                if "missing" not in ln:
                    resolved[i] = True
            if all(resolved):
                break
        if not settled:
            resolved = [True] * len(toks)  # cannot settle it: do not accuse
        for (lineno, tok), ok in zip(sha_candidates, resolved):
            if ok:
                findings.append(Finding(
                    "W-SHACITE", lineno,
                    "citation by commit SHA rots on rebase; cite the commit SUBJECT",
                    tok))
            else:
                findings.append(Finding(
                    "E-DEADREF", lineno,
                    "cited commit does not resolve - the pointer is already dead",
                    tok))

    # --- E-BADSTATUS, unconditionally: an out-of-vocabulary value must be LOUD ---
    #
    # o8's DA17 parsed with status "ALL", harvested from the prose label
    # "**CONTENT STATUS:** all three drafted". Because ALL is not in VALID_STATUS the entry
    # silently vanished from every generated view - while reading perfectly to a human.
    # E-BADSTATUS existed but was gated on the SECTION TITLE containing "DECISION", and
    # DA17's does not, so nothing fired.
    #
    # ⭐ A guard conditioned on WHERE an entry lives cannot protect an entry that lives
    # somewhere else. The value being wrong is the defect; the section is irrelevant to it.
    # Gating a correctness check on location is how a well-formed wrong value stays
    # invisible at exactly the level anyone inspects.
    for e in entries:
        st_any = status_of(e["body"])
        if st_any is not None and st_any not in VALID_STATUS:
            if not (DECISION_SECTION_RE.search(e["section"])
                    or DECISION_SECTION_RE.search(e["title"])):
                findings.append(Finding(
                    "E-BADSTATUS", e["line"],
                    "entry '%s' has Status '%s', which is not a status - it parses, so "
                    "every generated view silently DROPS this entry" % (e["id"], st_any),
                    "use one of: %s" % ", ".join(sorted(VALID_STATUS))))

    # --- E-NOSTATUS: decision entries need a machine-readable Status ---
    for e in entries:
        if DECISION_SECTION_RE.search(e["section"]) or DECISION_SECTION_RE.search(e["title"]):
            st = status_of(e["body"])
            if st is not None and st not in VALID_STATUS:
                findings.append(Finding(
                    "E-BADSTATUS", e["line"],
                    "entry '%s' has a Status field whose value '%s' is not a status - "
                    "it parses, so the gate passed it and the entry vanished from the "
                    "generated index" % (e["id"], st),
                    "use one of: %s" % ", ".join(sorted(VALID_STATUS))))
            elif st is None:
                findings.append(Finding(
                    "E-NOSTATUS", e["line"],
                    "decision entry '%s' has no machine-readable Status field" % e["id"],
                    "add a line reading:  %s" % STATUS_CANONICAL))

    findings.sort(key=lambda f: (f.line, f.code))
    return findings


def git(args, cwd=PROJECTS):
    # A None cwd must mean "the workspace", not the literal string "None". Passing None
    # produced `git -C None`, which fails with "cannot change to 'None'" - and a caller
    # reading only the return code sees that as the OPERATION failing. fetch_ok() did
    # exactly that, so the freshness oracle reported "cannot reach the remote" on every
    # single run: the cry-wolf failure, introduced by the fix for the opposite failure.
    # Always-unknown is no more useful than always-current.
    cwd = PROJECTS if cwd is None else cwd
    try:
        p = subprocess.run(["git", "-C", str(cwd)] + args,
                           capture_output=True, text=True, timeout=60)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except Exception as e:
        return 1, "", str(e)


def behind_canonical(cwd, fetch=True):
    """How far the working copy at `cwd` is behind CANONICAL_REF.

    Returns (state, n, detail) where state is one of:
        "current"  - n == 0, the tree can be trusted to answer about the repository
        "behind"   - n > 0, every count taken from this tree is a count of old content
        "unknown"  - git could not answer: no repo, no remote, detached with no
                     canonical ref, or the fetch failed. NEVER a refusal.

    ⛔ WHY THIS FETCHES BY DEFAULT. The remote-tracking ref is itself a snapshot. On
    2026-09-06 two lanes each cut a worktree from origin/main, and the SECOND lane's
    push never reached the first lane's `refs/remotes/origin/main`. Comparing against
    an unfetched ref would have reported "current" in both trees and missed the
    collision entirely. `--no-fetch` exists for a deliberately offline run.

    ⛔ AND WHY "unknown" IS NEVER A REFUSAL. A fresh `git init`, an offline laptop and a
    detached checkout are all legitimate. A guard that blocks them gets disabled, and a
    disabled guard protects nothing.
    """
    rc, _, _ = git(["rev-parse", "--git-dir"], cwd=cwd)
    if rc != 0:
        return "unknown", 0, "not a git repository"

    fetched = None
    if fetch:
        remote = CANONICAL_REF.split("/", 1)[0]
        rcf, _, errf = git(["fetch", "--quiet", remote], cwd=cwd)
        fetched = (rcf == 0)
        if rcf != 0:
            # Offline, no remote configured, auth failure. Fall through and compare
            # against whatever ref is on disk, and SAY the ref may be old.
            fetched = False

    rc2, out, _ = git(["rev-list", "--count", "HEAD..%s" % CANONICAL_REF], cwd=cwd)
    if rc2 != 0 or not out.strip().isdigit():
        return "unknown", 0, "no %s to compare against" % CANONICAL_REF

    n = int(out.strip())
    detail = "%s%s" % (CANONICAL_REF,
                       "" if fetched is not False else " (NOT re-fetched - may itself be old)")
    return ("current" if n == 0 else "behind"), n, detail


def git_stdin(args, payload, cwd=PROJECTS):
    """git with stdin, for batch verification in a single process."""
    try:
        p = subprocess.run(["git", "-C", str(cwd)] + args, input=payload,
                           capture_output=True, text=True, timeout=60)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except Exception as e:
        return 1, "", str(e)


def check_freshness(path, ref=CANONICAL_REF):
    """
    P5: the view a human opens must provably equal canonical state, or say it does not.

    Re-reading a file CANNOT detect this - the stale file is internally consistent and
    correctly formatted. On 2026-08-06 the human re-opened the o8 OrchDoc repeatedly to be
    sure it was current while 327 lines of corrections sat on another branch.
    """
    rel = os.path.relpath(str(path), str(PROJECTS)).replace("\\", "/")
    findings = []

    # ⛔ REFUSE rather than answer from cached refs. This function's entire output is a
    # claim about the remote; making that claim without having reached the remote is the
    # failure the function was written to prevent.
    ok, why = fetch_ok()
    if not ok:
        return [Finding(
            "E-NOFETCH", 0,
            "cannot reach the remote, so freshness is UNKNOWN - not 'current'",
            why or "git fetch failed; refs/remotes may be a stale snapshot")], None

    rc, canon, _ = git(["show", "%s:%s" % (ref, rel)])
    if rc != 0:
        return [Finding("E-STALE", 0,
                        "doc does not exist on %s (untracked, or on another branch only)" % ref,
                        rel)], None

    try:
        local = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return [Finding("E-IO", 0, "cannot read: %s" % e)], None

    # Normalise before comparing. The git() helper .strip()s stdout - correct when
    # reading a SHA, silently wrong when reading FILE CONTENT, because the trailing
    # newline vanishes and every file then looks changed. E-STALE is the only guard
    # against the failure that actually bit the human (327 lines of corrections on a branch
    # he was not reading), so a false alarm here is worse than in any other rule: it
    # trains the reader to ignore the one warning that matters.
    def _norm(t):
        return t.replace("\r\n", "\n").rstrip("\n")

    if _norm(local) == _norm(canon):
        return [], "identical to %s" % ref

    llines = local.splitlines()
    clines = canon.splitlines()
    _, behind, _ = git(["rev-list", "--count", "HEAD..%s" % ref])

    # WHICH of the two causes? They need opposite fixes, so naming the wrong one sends
    # the owner to do the wrong thing.
    #   uncommitted : content exists ONLY on this disk. At risk - a stray `git stash -u`
    #                 in a shared tree has already swept another session's work once.
    #   branch-old  : the newer content is safely on the canonical ref; the reader is
    #                 simply looking at an older checkout.
    _, head_blob, _ = git(["rev-parse", "HEAD:%s" % rel])
    work_blob = ""
    try:
        p = subprocess.run(["git", "-C", str(PROJECTS), "hash-object", str(path)],
                           capture_output=True, text=True, timeout=30)
        work_blob = p.stdout.strip()
    except Exception:
        pass

    if head_blob and work_blob and head_blob != work_blob:
        cause = ("UNCOMMITTED EDITS - this content exists ONLY in the working tree. "
                 "Commit it to %s; a shared tree is not storage." % ref)
    else:
        cause = ("the checkout is %s commits behind %s - the newer content is safe on "
                 "the canonical ref, but anyone reading this file sees the older one"
                 % (behind or "?", ref))

    findings.append(Finding(
        "E-STALE", 0,
        "working-tree copy DIFFERS from %s (local %d lines, canonical %d lines)"
        % (ref, len(llines), len(clines)),
        cause))
    return findings, None


def report(path, findings, quiet=False, strict=False):
    """
    Print one doc's result. BLOCKING first and in full; advisory as one summary line.

    o1: "an advisory finding in this system is a finding that does not exist" - 374
    advisory hits in its own doc, never acted on, and it only moved when the gate
    refused. The damage is not that advisory findings are ignored; it is that printing
    them beside blocking ones trains the reader to scroll past the whole report.
    the human's own rule, which o9 had not applied to its own output: failures at the top.
    """
    name = path.name
    counts = defaultdict(int)
    for f in findings:
        counts[f.code] += 1
    blocking = [f for f in findings if f.code in BLOCKING or strict]
    advisory = [f for f in findings if f.code not in BLOCKING and not strict]

    if not findings:
        if not quiet:
            print("  [OK]    %s" % name)
        return 0

    if blocking:
        print("  [BLOCK] %s  -  %d blocking" % (name, len(blocking)))
        for f in blocking:
            loc = ("line %d" % f.line) if f.line else "doc"
            print("          %-16s %-9s %s" % (f.code, loc, f.msg))
            if f.detail:
                print("          %-16s %-9s   -> %s" % ("", "", f.detail[:100]))
    else:
        print("  [warn]  %s  -  nothing blocking" % name)

    if advisory:
        adv = defaultdict(int)
        for f in advisory:
            adv[f.code] += 1
        print("          advisory (not blocking): %s"
              % ", ".join("%s x%d" % (c, n) for c, n in sorted(adv.items())))
        # ⛔ A COUNT IS NOT ACTIONABLE, and printing one without a way to expand it is how a
        # detected defect goes unfixed for weeks. o8's doc reported `W-STRIKEDONE x14` on every
        # run: correctly detected, correctly counted, and impossible to act on - there are no
        # line numbers, so to fix it you would have to already know where it is.
        #
        # the human's complaint traces straight to this line. The rule was implemented, the check was
        # firing, and the output gave nobody anywhere to go. **Detection was never the problem;
        # the path OUT was.**
        print("          %d advisory finding(s) have LINE NUMBERS - see them with:  "
              "orchdoc.py check --doc <doc> --strict" % len(advisory))

    return 1 if blocking else 0


def cmd_check(args):
    docs = resolve_docs(args)
    if not docs:
        print("no OrchDocs found", file=sys.stderr)
        return 2
    print("orchdoc check - %d doc(s)%s" % (len(docs), "  [STRICT]" if args.strict else ""))
    worst = 0
    totals = defaultdict(int)
    for d in docs:
        f = check_doc(d)
        for x in f:
            totals[x.code] += 1
        worst |= report(d, f, quiet=args.quiet, strict=args.strict)

    # ⛔ BOUND THE WINDOW, OR THE GATE CAN NEVER PASS. This scanned ALL of origin/main's
    # history, so a malformed trailer written once - on 2026-08-06, the day the feature was
    # built - refused this gate for every doc, for every orchestrator, forever. A landed commit
    # message cannot be corrected without rewriting shared history, so the finding named
    # something nobody could act on.
    #
    # ⭐ A REFUSAL NOBODY CAN SATISFY IS THE FASTEST WAY TO TEACH PEOPLE TO OVERRIDE. This doc
    # already carries 26 W-OVERRIDE stamps, and the permanent refusal above is part of why.
    #
    # So: BLOCKING inside a window where the author can still amend or re-land, and reported
    # but not blocking beyond it. The trailer's whole job is to update an entry now; one from
    # three weeks ago has already had its effect or missed it.
    # A first attempt bounded this by TIME (14 days) and did not work: the two offenders were
    # 13 days old, so they stayed inside the window while being just as unfixable. **Age was
    # the wrong axis.** The question is not how old a commit is, it is whether the author can
    # still change it - and that is decided by whether it has landed, not by when.
    _, _bad_local = touches_since(None, None, rev="%s..HEAD" % CANONICAL_REF)
    _, _bad_all = touches_since(None, None)
    _unq = {t: v for t, v in _bad_local.items() if "unqualified" in v[2]}
    _old = {t: v for t, v in _bad_all.items()
            if "unqualified" in v[2] and t not in _unq}
    if _unq:
        print()
        print("  [BLOCK] %d UNLANDED commit trailer(s) name an entry without saying WHICH"
              % len(_unq))
        print("          doc, so they update nothing. Entry ids are PER-DOC: several docs")
        print("          all have a D1. These are still amendable - fix before pushing.")
        for t, (when, subject, _why) in sorted(_unq.items()):
            print("            Touches: %-6s -> say o<N>:%-6s  (%s)"
                  % (t, t, subject[:52]))
        worst = 1
    if _old:
        print()
        print("  [note]  %d unqualified trailer(s) in LANDED history, unfixable without"
              % len(_old))
        print("          rewriting shared history. Reported, not blocking.")
        for t, (when, subject, _why) in sorted(_old.items()):
            print("            %s  %s  (%s)" % (when[:10], t, subject[:52]))

    blk = {c: n for c, n in totals.items() if c in BLOCKING}
    adv = {c: n for c, n in totals.items() if c not in BLOCKING}
    # ⛔ AN UNTRACKED ORCHDOC IS INVISIBLE TO EVERY FRESHNESS CHECK, including this one - they
    # all compare against a ref the file was never on, so they return "no difference" because
    # there is nothing to difference. o11's doc sat like that while they quoted the human anchors
    # into it and he could not find a single one. Surfaced here because a human is already
    # reading this output.
    _orphans = untracked_orchdocs()
    if _orphans:
        print("\n⛔ ON NO REF AT ALL - the human reads a file git has never seen:")
        for _o in _orphans:
            print("     %s" % _o)
        print("   Land it through this tool. Until then no freshness check can see it,")
        print("   and every anchor you give him points into a file that may lack the entry.")

    print("\nBLOCKING: " + (", ".join("%s=%d" % (c, n) for c, n in sorted(blk.items()))
                            or "none"))
    print("ADVISORY: " + (", ".join("%s=%d" % (c, n) for c, n in sorted(adv.items()))
                          or "none"))
    print("\ngate %s" % ("REFUSES" if worst else "PASSES"))
    return worst


def cmd_freshness(args):
    docs = resolve_docs(args)
    print("orchdoc freshness - canonical ref %s" % CANONICAL_REF)
    worst = 0
    for d in docs:
        f, ok = check_freshness(d)
        if ok:
            print("  [OK]   %s  %s" % (d.name, ok))
        else:
            worst |= report(d, f)
    return worst


def resolve_docs(args):
    # Must go through resolve_doc_arg. This bypassed it and did Path(args.doc) raw, so
    # `--doc o6` worked for add/resolve/plate and failed for check/freshness - the same
    # flag meaning two different things depending on the subcommand. Found by o6 on
    # first contact, which is where interface inconsistencies always surface.
    if getattr(args, "doc", None):
        return [resolve_doc_arg(args.doc)]
    return sorted(PROJECTS.glob("ORCHESTRATOR-DECISIONS-*.md"))


def cmd_selftest(args):
    """Synthetic fixtures. Each must produce exactly the code it is built to trip."""
    import tempfile
    cases = [
        ("E-DUPID",
         "## DECISIONS\n\n### D1 - first\n**Status:** OPEN\n\ntext\n\n"
         "### D1 - same id again\n**Status:** RESOLVED\n\ntext\n"),
        ("E-SELFCLAIM", "## DECISIONS - none open\n\nnothing here\n"),
        # the human's D5 shape: the container says done, one sub-item says otherwise. This is what
        # o8 meant by "burying the majority of what is actually still owed" - it looks finished.
        ("E-CLOSEDWITHOPENSUBS",
         "## DECISIONS\n\n### D4 - ship the three lanes\n**Status:** RESOLVED\n\n"
         # The settled ones carry the canonical checked-checkbox form. A fixture is read as an
         # EXAMPLE of correct markup, so one written in a superseded form teaches it.
         "- [x] lane A merged\n- [x] lane B merged\n- lane C NOT DONE\n"),
        # The mirror of the one above, from o8's real DA6 (the human, 2026-08-10): a LIVE container
        # whose rulings are already made and none of them struck, so the status says "he is
        # needed" while the contents say "settled". He opens it, reads all of it, and finds
        # nothing owed. Note the clean-doc fixture must NOT trip this - a live entry whose
        # sub-items are genuinely open carries no done marker and so cannot match.
        # the human, 2026-08-10: one id form everywhere. `D-PAUSE` and `T-VENDORDISC` read as ids but
        # are a second naming scheme, so the same kind of thing has two names in one workspace.
        # the human, 2026-08-10: a done item with all done sub-items goes to §99 COMPLETELY, never
        # left in a live section. E-DONEINACTIVE only sees an entry whose STATUS is already
        # terminal, so an entry that finished its last sub-item and never had its status changed
        # was invisible - the work over, the plate still showing it.
        # the human spotted both of these in live docs, 2026-08-10. The scaffold writes them and
        # nothing ever asked whether they had been filled in - a stub is valid markdown, so it
        # parses exactly as well as real content.
        # ⛔ The fixture name MATTERS here - the check is scoped to real OrchDocs, so a fixture
        # written under any other name would test nothing and pass. Same shape as the publish
        # probe that renamed its subject and verified itself by circularity.
        # the human, 2026-08-11: "DO NOT KEEP A RUNNING HISTORY THERE." The settled sub-item MUST NOT
        # trip it - he wants those visible on the plate; it is the prose about how the record
        # used to read that belongs in a finding.
        # the human, 2026-08-11: "There should NOT be anything there." An entry in a container
        # section is outside every subsection, so nothing else in this file can see it.
        # Prose in the container is fine and must NOT trip it - the rule line telling readers
        # where items go legitimately lives exactly there.
        # the human, 2026-08-11: T<n> is HIS to-do namespace; an orchestrator's own work is W<n>.
        # "T3 has to mean one thing when he says it."
        ("E-WRONGSECTION",
         "## §3 ON CLAUDE'S PLATE\n\n### T1 - the orchestrator's own work\n"
         "**Status:** OPEN - **Owner:** o9\n\nbody\n"),
        ("E-LOOSEINPARENT",
         "## §2 LIVE ON THE PLATE\n\nItems live in §2.1 below, never loose here.\n\n"
         "### D4 - a decision parked in the container\n**Status:** OPEN\n\nbody\n\n"
         "## §2.1 Decisions\n\n### D5 - correctly placed\n**Status:** OPEN\n\nbody\n"),
        ("E-PLATEHISTORY",
         "## §2.1 Decisions\n\n### D3 - pick a tier\n**Status:** OPEN\n\n"
         "- [x] the price was set\n"
         "This entry previously said $25, which was wrong; o7 caught it and it is now corrected.\n"),
        ("E-LEGACYDOC",
         "# Decisions\n\n## Open items\n\n### D1 - a decision\n**Status:** OPEN\n\nbody\n"),
        ("E-STUBLEFT",
         "## PURPOSE\n\n_TODO: one paragraph - what this orchestrator is for. "
         "Authored, never generated._\n"),
        # An empty §1 in a doc that cites assets elsewhere. The subtitle promises "every doc and
        # URL this orchestrator owns", so empty is a false claim rather than a blank.
        ("E-EMPTYLINKS",
         "## §1 LINKS AND DOCS\n\n_every doc and URL this orchestrator owns_\n\n"
         "## §4 FINDINGS\n\n### F1 - a finding\n**Status:** CONFIRMED\n\n"
         "See `docs/plan.md` and https://example.com/dashboard for detail.\n"),
        ("E-ALLSUBSDONE",
         "## DECISIONS\n\n### D3 - ship the lanes\n**Status:** OPEN\n\n"
         "- [x] lane A - ✅ DONE\n"
         "- [x] lane B - ✅ DONE\n"),
        ("E-IDSHAPE",
         "## DECISIONS\n\n### D-PAUSE - pause cues\n**Status:** OPEN\n\nbody\n"),
        # ...and numbers that run in order, so a reader stops when they arrive rather than
        # scanning to the end of the section to be sure.
        ("E-IDORDER",
         "## DECISIONS\n\n### D5 - later\n**Status:** OPEN\n\nbody\n\n"
         "### D2 - earlier\n**Status:** OPEN\n\nbody\n"),
        ("E-SETTLEDNOTSTRUCK",
         "## DECISIONS\n\n### D6 - classification codification\n**Status:** OPEN\n\n"
         "- (a) the four hybrids - ✅ **leave as AT**\n"
         "- (b) two scripts - ✅ **move, keep as-is**\n"
         "- (c) wording - o8 drafts it and brings it to the human\n"),
        ("E-ARCHIVEDMARKER",
         "## DECISIONS\n\n### D1 - live\n**Status:** OPEN\n\nbody\n\n"
         "<details><summary>Original D1 wording (superseded)</summary>\n\n"
         "### D1 - old wording\n**Status:** RESOLVED\n\nold body\n\n</details>\n"),
        ("W-LINECITE", "## NOTES\n\nSee D8, line 82 for detail.\n"),
        # A SHA from a repo that is actually PRESENT, so it resolves wherever this runs.
        # A literal SHA is environment-dependent by construction: it resolves in the repo
        # it was copied from and nowhere else, so this fixture inverted to E-DEADREF on
        # every fresh clone and `selftest` - the command new users are told to run first -
        # failed for all of them. The distinction W-SHACITE tests is "resolves vs does
        # not", which means the fixture has to supply one that does.
        ("W-SHACITE", "## NOTES\n\nFixed in commit `%s` yesterday.\n" % _self_sha()),
        ("E-NOSTATUS", "## DECISIONS\n\n### D9 - a decision with no status field\n\nbody\n"),
        ("E-DEADREF", "## NOTES\n\nLanded in `deadbeef1234567` last week.\n"),
        # o7's real D16. A Status field carrying prose: it parses, the gate passes it,
        # and the entry vanishes from the generated index. The most dangerous shape,
        # because it looks migrated.
        # o8's real case: a length verdict said "10 of 12 clear the floor" while a
        # measurement four sections away said 0 of 13. Nothing connected them.
        ("E-STALEPROSE",
         "## DECISIONS\n\n### D1 - the verdict\n"
         "**Status:** OPEN - **Reviewed:** 2026-08-01\n"
         "**Depends:** F2\n\n10 of 12 clear the floor.\n\n"
         "## FINDINGS\n\n### F2 - the measurement\n"
         "**Status:** CONFIRMED - **Recorded:** 2026-08-06\n\n0 of 13 clear it.\n"),
        # the human's specimen, via o1: one bullet asserting both that the chain has NEVER
        # run and that it is PROVEN 3x. Both halves carried status markers, so an
        # entry-level "has a status?" check passes it.
        ("E-MIXEDSTATE",
         "## DECISIONS\n\n### D1 - chain\n**Status:** OPEN - **Owner:** the human\n\n"
         "- the full chain has **NEVER run in production**. ✅ **RECON DONE** - "
         "the Plus path is **PROVEN 3x**.\n"
         "- ⏳ **NOT DONE** - o1 owns: a real card has never been charged\n"),
        ("E-NOOWNER",
         "## DECISIONS\n\n### D1 - needs someone\n**Status:** OPEN\n\nbody\n"),
        # the human: "done items are left cluttering up the active list". A RESOLVED
        # decision sitting under a heading that promises live items is pure clutter.
        ("E-DONEINACTIVE",
         "## DECISIONS - need your call\n\n### D1 - already decided\n"
         "**Status:** RESOLVED - **Owner:** the human\n\nbody\n"),
        # ⛔ the human asked twice in five minutes why finished items were still on his plate. An
        # entry declares its own completion signal; when that signal is DEMONSTRABLY satisfied
        # and the entry is still OPEN, the work landed and the record did not. `cmd:` with a
        # command that exits 0 is the smallest condition that is true anywhere, so the fixture
        # tests the CHECK rather than the environment.
        ("E-DONEBUTOPEN",
         "## DECISIONS - need your call\n\n### D1 - the work landed, the entry did not move\n"
         "**Status:** OPEN - **Owner:** the human - **Opened:** 2026-08-01\n\n"
         "**Done-when:** cmd:python -c \"pass\"\n\nbody\n"),
        # the human: done items are "not clearly marked visually". The heading marker is
        # DERIVED from the Status field, so the two can never disagree.
        ("E-MARKERDRIFT",
         "## FINDINGS\n\n### F1 - no marker on the heading\n"
         "**Status:** CONFIRMED - **Owner:** o9\n\nbody\n"),
        # o7: an unqualified or unknown trailer must refuse, because "a typo becomes an
        # invisible non-update". This fixture cannot be exercised without git history,
        # so it asserts the parser directly instead.
        ("E-BADTOUCH", None),
        ("E-NOFETCH", None),
        # o5: a bare date is only unanswerable when the contending work is same-day.
        ("E-AMBIGUOUSDATE",
         "## DECISIONS\n\n### D1 - verdict\n"
         "**Status:** OPEN - **Reviewed:** 2026-08-05\n**Depends:** F2\n\nbody\n\n"
         "## FINDINGS\n\n### F2 - input\n"
         "**Status:** CONFIRMED - **Recorded:** 2026-08-05\n\nbody\n"),
        # o5's marker-contract hazard: an unclosed BEGIN makes every reader below it
        # look derived, so hand-authored content could be clobbered without a word.
        ("E-BADMARKER",
         "# Doc\n\n" + PLATE_BEGIN + "\n\n| ID | x |\n\n"
         "## DECISIONS\n\n### D1 - hand-authored, below an UNCLOSED marker\n"
         "**Status:** OPEN\n\nbody\n"),
        # o8's residual risk: a ruling with no declared edge can never go stale, so
        # "nothing declared" is indistinguishable from "nothing moved".
        ("E-NODEPS",
         "## DECISIONS\n\n### D1 - a ruled decision resting on nothing declared\n"
         "**Status:** RESOLVED - **Owner:** the human\n\n"
         "**Resolved 2026-08-06:** the human ruled B.\n\nbody\n"),
        # o8's caution: an attestation that says nothing is compliance without thought.
        ("E-RUBBERSTAMP",
         "## DECISIONS\n\n### D1 - the verdict\n"
         "**Status:** OPEN - **Reviewed:** 2026-08-05 - still current\n"
         "**Depends:** F2\n\nbody\n\n"
         "## FINDINGS\n\n### F2 - an input\n"
         "**Status:** CONFIRMED - **Recorded:** 2026-08-01\n\nbody\n"),
        # the human's case: D1 near the top, D2 far below under an unrelated section, with
        # no single place to scroll to.
        # the human's schema, 2026-08-07. A doc that has opted into numbered sections but is
        # missing most of the spine - the state every existing OrchDoc is in today.
        # the human, 2026-08-07: each doc begins with its name. The failure that matters is
        # a doc naming an orchestrator other than the one whose file it is.
        # the human's ruling, 2026-08-07: a second H1 can be read as a second document title
        # by a future title-extractor, and that risk cannot be cleared by surveying
        # today's tools.
        # o9's own defect, 2026-08-06: 22 attestations dated a day in the future, all of
        # which passed every gate. 2099 so the fixture cannot rot into the past.
        ("E-FUTUREDATE",
         "## DECISIONS\n\n### D1 - a ruling\n"
         "**Status:** RESOLVED - **Owner:** the human - **Attested-by:** o9 at 2099-01-01T00:00:00-07:00 - checked it\n"
         "**Depends:** F2\n\nbody\n\n"
         "## FINDINGS\n\n### F2 - an input\n"
         "**Status:** CONFIRMED - **Recorded:** 2026-08-01\n\nbody\n"),
        # o9L7: an id the parser cannot read is skipped, so the entry vanishes from every
        # guarantee the tool makes while still being readable on the page.
        # o9L7: a doc mid-merge answers every question from whichever side sorts first.
        # o2's specimen, 2026-08-07: a pointer broken by the same edit that created it.
        ("W-BADLINEREF",
         "# t\n\n\n\n\n\n| blocking | **A1** (line 2) |\n\n## DECISIONS\n\n"
         "### A1 - the real location\n**Status:** OPEN - **Owner:** the human\n\nbody\n"),
        ("E-CONFLICT",
         "## DECISIONS\n\n### D1 - a decision\n<<<<<<< HEAD\n**Status:** OPEN\n"
         "=======\n**Status:** RESOLVED\n>>>>>>> other\n\nbody\n"),
        ("E-BADID",
         "## DECISIONS\n\n### D-1 - an id that does not parse\n"
         "**Status:** OPEN - **Owner:** someone\n\nbody\n"),
        ("E-ONEH1",
         "# Orchestrator Decision Doc - o99\n# (a role)\n\n## \u00a71 LINKS AND DOCS\n\nx\n"),
        ("E-TITLE",
         "# Some Other Heading\n\n## \u00a71 LINKS AND DOCS\n\nstuff\n"),
        ("E-SCHEMA",
         "# Doc\n\n## \u00a71 LINKS AND DOCS\n\nstuff\n\n"
         "## \u00a72 LIVE ON THE HUMAN'S PLATE\n\n### D1 - a call\n**Status:** OPEN\n\nbody\n"),
        ("E-SCATTERED",
         "## DECISIONS\n\n### D1 - here\n**Status:** OPEN\n\nbody\n\n"
         "## FINDINGS\n\n### F1 - a finding\n**Status:** RECORDED\n\nbody\n\n"
         "## SOMETHING ELSE\n\n### D2 - way down here\n**Status:** OPEN\n\nbody\n"),
        ("E-BADSTATUS",
         "## DECISIONS\n\n### D16 - PRO IS UNSELLABLE RIGHT NOW\n\n"
         "**Status:** the human authorized the fix; o1 is building it\n\nbody\n"),
        # A generated block a human has since edited. o6's case: generating the index
        # is not enough if the rendered copy can be hand-edited afterward.
        ("E-PLATEDRIFT",
         "# Doc\n\n" + PLATE_BEGIN + "\n\n| ID | What it is | Owner | Opened | Enriched |\n"
         "|---|---|---|---|---|\n| `D9` | a row a human typed in | the human | - | - |\n\n"
         "_1 open. Generated by `orchdoc.py plate`; edits here are overwritten._\n"
         + PLATE_END + "\n\n## DECISIONS\n\n### D1 - real entry\n"
         "**Status:** OPEN - **Owner:** the human\n\nbody\n"),
    ]
    ok = True
    print("orchdoc selftest")
    for want, body in cases:
        if body is None and want == "E-NOFETCH":
            # FUNCTIONAL assertion, not a document fixture: E-NOFETCH is emitted when the
            # REMOTE is unreachable, which no .md file can simulate. A real repo with a
            # bogus remote is the only honest test, and the meta-guard is right to demand
            # one - an untestable blocking code is indistinguishable from a dead one.
            d = Path(tempfile.mkdtemp())
            git(["init", "-q", "."], cwd=d)
            git(["remote", "add", "origin",
                 "https://o9-nonexistent-host.invalid/x.git"], cwd=d)
            reached, _why = fetch_ok(cwd=d)
            good = (reached is False)      # an unreachable remote must report FAILURE
            ok &= good
            print("  [%s] %-12s -> unreachable remote reports FAILURE, not success"
                  % ("OK" if good else "FAIL", want))
            continue

        if body is None:
            # Parser-level assertion: a trailer that cannot resolve must be reported,
            # never silently dropped.
            _, bad = touches_since("o9", None)
            probe = {}
            for tok in ["D1", "o9:D1", "notanid", "o7:D16"]:
                m = TOUCH_TOKEN_RE.match(tok)
                probe[tok] = (m.group(1), m.group(2)) if m else None
            good = (probe["D1"][0] is None            # unqualified -> must be refused
                    and probe["o9:D1"] == ("o9", "D1")
                    and probe["notanid"] is None
                    and probe["o7:D16"] == ("o7", "D16"))
            ok &= good
            print("  [%s] %-12s -> %s" % ("OK" if good else "FAIL", want,
                                          "qualified/unqualified/invalid all classified"))
            continue
        # The fixture file must be NAMED canonically or E-TITLE can never apply: the
        # check reads the EXPECTED identity from the filename, which is the whole point
        # of it. A random temp name made the check unreachable and the fixture green -
        # the same false-pass shape the meta-guard exists to catch.
        tmp = Path(tempfile.mkdtemp()) / "ORCHESTRATOR-DECISIONS-o99.md"
        tmp.write_text(body, encoding="utf-8")
        try:
            codes = {f.code for f in check_doc(tmp)}
            # A fixture whose check CANNOT run here is SKIPPED, visibly - not failed, and
            # never silently passed. E-DEADREF settles SHAs against real git repos and
            # deliberately refuses to accuse when it cannot settle them ("do not accuse"),
            # so with no repo present it can never fire. Reporting that as FAIL made
            # `selftest` fail on any fresh clone - which is the one command a new user is
            # told to run to confirm the tool works.
            #
            # ⭐ The skip is PRINTED. A check that quietly excuses itself is the dead
            # check this suite's meta-guard exists to catch; the honest form says out
            # loud what it could not verify and why.
            if want in _NEEDS_GIT and not any((r / ".git").exists()
                                              for r in citable_repos()):
                print("  [SKIP] %-12s -> needs a git repo to settle SHAs; not verifiable "
                      "in this environment" % want)
                continue
            good = want in codes
            ok &= good
            print("  [%s] %-12s -> %s" % ("OK" if good else "FAIL", want,
                                          ",".join(sorted(codes)) or "none"))
        finally:
            try:
                tmp.unlink()
            except (OSError, UnicodeDecodeError):
                pass

    # ⛔ o11's FALSE POSITIVE, PINNED. An entry with checked AND unchecked boxes must NOT report
    # E-ALLSUBSDONE. It did, because the counter recognised `[x]` and had no way to recognise
    # `[ ]` - and its printed remedy was "set a terminal status, then archive", which files live
    # work as complete. A NEGATIVE fixture is the only kind that can catch this: the positive
    # one passes either way, which is exactly why it shipped.
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(canonical_title("o99", "a role") + "\n\n"
                 "## DECISIONS\n\n### \U0001f534 W4 - a container with work left\n"
                 "**Status:** OPEN - **Owner:** o99\n\n"
                 "- [x] 1. export everything - ✅ DONE\n"
                 "- [x] 2. stop the write path - ✅ DONE\n"
                 "- [ ] 3. rehome the rows\n")
        tmp = Path(fh.name)
    try:
        codes = {f.code for f in check_doc(tmp)}
        good = "E-ALLSUBSDONE" not in codes
        ok &= good
        print("  [%s] %-12s -> %s" % ("OK" if good else "FAIL", "open-box",
                                      "E-ALLSUBSDONE absent" if good
                                      else "FIRED on an entry with an open box"))
    finally:
        try:
            tmp.unlink()
        except (OSError, UnicodeDecodeError):
            pass

    # ⛔ THE COMMIT-ROLE CASE, WHICH SHIPPED WITH NO FIXTURE AND SHOULD NOT HAVE. o7 measured
    # E-STALEPROSE flagging entries for their own creation commits three times in twenty minutes;
    # the fix landed verified only by counts across six live docs.
    #
    # ⭐ o7 also found the one line that made a fixture possible - `update-ref
    # refs/remotes/origin/main` - because touches_since() scans CANONICAL_REF, and a fresh
    # `git init` has none, so the push edge never runs and BOTH revisions report clean. Their
    # first attempt did exactly that and looked like a pass.
    #
    # ⭐ THE SECOND CASE IS THE CONTROL AND IT MATTERS MORE THAN THE FIRST: a commit that
    # genuinely rewrites the reasoning must STILL fire. A fix that silenced E-STALEPROSE
    # altogether would pass a one-sided test while destroying the signal the check exists for.
    if shutil.which("git"):
        for _rewrite, _want, _label in ((False, False, "role-created"),
                                        (True, True, "role-rewritten")):
            _repo = None
            try:
                _repo = pathlib.Path(tempfile.mkdtemp(prefix="orchdoc-role-"))

                def _g(*a):
                    return subprocess.run(["git", "-C", str(_repo)] + list(a),
                                          capture_output=True, text=True)

                _g("init", "-q")
                _g("config", "user.email", "fixture@example.com")
                _g("config", "user.name", "fixture")
                _name = "ORCHESTRATOR-DECISIONS-o99.md"
                _body = ("## \u00a73 IN FLIGHT\n\n### W1 - the entry this commit creates\n\n"
                         "**Status:** OPEN - **Owner:** o99 - "
                         "**Reviewed:** 2026-01-01T00:00:00-08:00\n"
                         "**Depends:** `some/file.ts`\n\nThe reasoning, as first written.\n")
                (_repo / _name).write_text(_body, encoding="utf-8")
                _g("add", _name)
                _g("commit", "-m", "o99: add W1\n\nTouches: o99:W1")
                if _rewrite:
                    (_repo / _name).write_text(
                        _body.replace("The reasoning, as first written.",
                                      "The reasoning, materially rewritten after measurement."),
                        encoding="utf-8")
                    _g("add", _name)
                    _g("commit", "-m", "o99: rewrite W1 reasoning\n\nTouches: o99:W1")
                _g("update-ref", "refs/remotes/origin/main", "HEAD")

                # ⛔ POINT git() AT THE REPO; DO NOT MOVE THE PROCESS. `git(args, cwd=PROJECTS)`
                # defaults to the real workspace, so os.chdir changed nothing - every call still
                # read <your-workspace>, the push edge found no trailer naming o99:W1 in real
                # history, and this printed OK because the check never ran.
                #
                # ⭐ A green that comes from the check not running. The CONTROL below is the only
                # reason it surfaced: a control failing while the case passes cannot be read as
                # the case passing.
                _real_git = globals()["git"]
                globals()["git"] = lambda a, cwd=None, _r=_real_git, _p=_repo: _r(a, cwd=_p)
                _ROLE_CACHE.clear()
                try:
                    _codes = [f for f in check_doc(_repo / _name)
                              if f.code == "E-STALEPROSE"]
                finally:
                    globals()["git"] = _real_git
                    _ROLE_CACHE.clear()
                _got = bool(_codes)
                _good = _got == _want
                ok &= _good
                print("  [%s] %-12s -> E-STALEPROSE fired=%s (want %s)"
                      % ("OK" if _good else "FAIL", _label, _got, _want))
            except Exception as _e:
                print("  [SKIP] %-12s -> %s" % (_label, str(_e)[:60]))
            finally:
                if _repo is not None:
                    shutil.rmtree(str(_repo), ignore_errors=True)

    # \u26d4 restamp's WRITE PATH, which cannot be honestly tested in a live document.
    # o1 tried and stopped: every entry in their doc is stamped and the gate passes, so
    # exercising the write would have meant composing a 40-character attestation about a
    # measurement they had not made - a false attestation, in a real doc, to test the tool
    # that exists to prevent false attestations. They were right to refuse.
    #
    # \u2b50 A FIXTURE IS NOT A DOCUMENT. Its content is synthetic by construction and asserts
    # nothing about the world, so a synthetic attestation here is not a claim at all. The bar
    # makes the tool untestable in a live doc and says nothing about a temp file.
    #
    # Two properties, both named by o1 as unverified: stamps APPEND rather than overwrite, and
    # reviewed_of() returns the NEWEST of them.
    _rs = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                         encoding="utf-8") as fh:
            fh.write(canonical_title("o99", "a role") + "\n\n"
                     "## DECISIONS\n\n### \U0001f534 D1 - an entry to stamp twice\n"
                     "**Status:** OPEN - **Owner:** o99 - "
                     "**Reviewed:** 2026-01-01T00:00:00-08:00 - the first reading, "
                     "which names what it rested on at the time\n\nReasoning prose.\n")
            _rs = Path(fh.name)

        class _A(object):
            pass

        def _stamp(when, why):
            return write_restamp(_rs, "D1", why, "o99", when)

        _one = "the first re-review, naming a mover and why the entry survives it"
        _two = "the second re-review, later, naming a different mover entirely"
        _rc = _stamp("2026-02-02T00:00:00-08:00", _one)
        _rc |= _stamp("2026-03-03T00:00:00-08:00", _two)
        _txt = _rs.read_text(encoding="utf-8", errors="replace")
        _kept = (_one in _txt) and (_two in _txt) and ("the first reading" in _txt)
        _date, _att = reviewed_of(_txt)
        _newest = (_date or "").startswith("2026-03-03")
        _good = _rc == 0 and _kept and _newest
        ok &= _good
        print("  [%s] %-12s -> appended=%s newest=%s" %
              ("OK" if _good else "FAIL", "restamp-rw", _kept, _date or "none"))
    except Exception as _e:
        print("  [SKIP] %-12s -> %s" % ("restamp-rw", str(_e)[:60]))
    finally:
        if _rs is not None:
            try:
                _rs.unlink()
            except OSError:
                pass

    # ⛔ THE PLATE LINE MUST NOT GROW. `restamp-rw` above proves stamps are KEPT; it passes
    # just as happily when every one of them is appended to the `**Status:**` line, which is
    # exactly what the tool did until 2026-09-04 and exactly what the human reported: *"There are
    # mountains of text inside my §2 which is meant to allow me to quickly (QUICKLY!!) see
    # important information."* DA17's plate line had reached 1,396 characters.
    #
    # ⭐ SO THIS ASSERTS THE PROPERTY THE OTHER FIXTURE CANNOT SEE - where the prose LIVES.
    # Three separate claims, because a weaker version of any one of them passes both designs:
    # the plate line stays short, every justification survives somewhere, and a re-run of a
    # stamp that is already recorded changes not one byte.
    #
    # The migration half matters as much as the write: the fixture seeds a plate line that
    # ALREADY carries an appended attestation, the shape live docs are in today. A fix that
    # only stopped appending would leave those lines long forever.
    _rp = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                         encoding="utf-8") as fh:
            fh.write(canonical_title("o99", "a role") + "\n\n"
                     "## DECISIONS\n\n### \U0001f534 D1 - an entry whose plate line grew\n"
                     "**Status:** OPEN - **Owner:** the human - **Opened:** 2026-01-01 - "
                     "**Reviewed:** 2026-01-01T00:00:00-08:00 - the first reading, which "
                     "names the facts it rested on at the time - **Attested-by:** o1 at "
                     "2026-02-02T00:00:00-08:00 - the second reading, appended to the same "
                     "line, naming a mover the first one could not have seen\n"
                     "**Depends:** D2\n\nReasoning prose.\n")
            _rp = Path(fh.name)

        _three = ("the third reading, which names what moved since February and why the "
                  "entry survives it")
        _rc = write_restamp(_rp, "D1", _three, "o99", "2026-03-03T00:00:00-08:00")
        _txt = _rp.read_text(encoding="utf-8", errors="replace")
        _plate = next((l for l in _txt.split("\n") if l.startswith("**Status:**")), "")

        # 1. the plate line carries the tracked fields and a bare date, and no prose at all
        _short = len(_plate) <= 140 and not any(
            frag in _plate for frag in ("the first reading", "the second reading", _three))
        # it is still a PLATE: the fields that were on it are still on it
        _fields = all(f in _plate for f in ("**Status:** OPEN", "**Owner:** the human",
                                            "**Opened:** 2026-01-01"))
        # 2. nothing was dropped - and `**Depends:**` did not get eaten by the rewrite
        _kept3 = all(s in _txt for s in ("the first reading", "the second reading", _three)) \
            and "**Depends:** D2" in _txt
        # 3. the newest stamp, with its prose, is what a reader gets
        _d, _a = reviewed_of(_txt)
        _newest = (_d or "").startswith("2026-03-03") and _three in (_a or "")
        # 4. idempotent: the same stamp again writes nothing, and shortens rather than doubles
        _rc |= write_restamp(_rp, "D1", _three, "o99", "2026-03-03T00:00:00-08:00")
        _stable = _rp.read_text(encoding="utf-8", errors="replace") == _txt
        _good = _rc == 0 and _short and _fields and _kept3 and _newest and _stable
        ok &= _good
        print("  [%s] %-12s -> plate=%dch short=%s kept=%s newest=%s idempotent=%s"
              % ("OK" if _good else "FAIL", "restamp-plate", len(_plate), _short,
                 _kept3, _newest, _stable))
        if not _good:
            print("       plate line: %s" % _plate[:150])
    except Exception as _e:
        ok = False
        print("  [FAIL] %-12s -> %s" % ("restamp-plate", str(_e)[:70]))
    finally:
        if _rp is not None:
            try:
                _rp.unlink()
            except OSError:
                pass

    # A clean doc must produce nothing. Guards against over-eager matching.
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(canonical_title("o99", "a role") + "\n\n"
                 "## DECISIONS\n\n### \U0001f534 D1 - a clean entry\n"
                 "**Status:** OPEN - **Owner:** the human\n\nReasoning prose here.\n")
        tmp = Path(fh.name)
    try:
        codes = {f.code for f in check_doc(tmp)}
        good = not codes
        ok &= good
        print("  [%s] %-12s -> %s" % ("OK" if good else "FAIL", "clean-doc",
                                      ",".join(sorted(codes)) or "none"))
    finally:
        try:
            tmp.unlink()
        except (OSError, UnicodeDecodeError):
            pass

    # ⭐ AGREEMENT GUARD: the reader that REPORTS a defect and the reader that FIXES it
    # must answer the same question about the same document.
    #
    # o9L20, 2026-08-18: `check` reported E-IDORDER on o7 while `reorder` printed *"every
    # section already runs in order"* on the same file. `reorder` only sorted CONTIGUOUS
    # same-prefix runs, and o7's D21 sat three W entries below D22, so its measurement could
    # not see the defect - and it was the one printing the reassuring sentence. A fixture
    # that only exercised `check` could never catch this: both codes were correct in
    # isolation. So this asserts the PAIR - report, fix, re-report - on the exact shape that
    # broke, with entries of another prefix separating the inverted pair.
    _NONCONTIG = ("## DECISIONS\n\n"
                  "### D3 - first\n**Status:** OPEN - **Owner:** the human\n\nbody three\n\n"
                  "### D22 - out of order\n**Status:** OPEN - **Owner:** the human\n\nbody 22\n\n"
                  "### W1 - a different prefix, must not move\n"
                  "**Status:** OPEN - **Owner:** o9\n\nbody w1\n\n"
                  "### W3 - also must not move\n**Status:** OPEN - **Owner:** o9\n\nbody w3\n\n"
                  "### D21 - belongs above D22\n**Status:** OPEN - **Owner:** the human\n\nbody 21\n")
    # ⛔ INSIDE the workspace. `reorder` refuses any path outside it (_confine), so a
    # fixture in the system temp dir cannot exercise the command at all - it would test the
    # path guard and report that as a reorder result.
    _dir = Path(tempfile.mkdtemp(prefix=".orchdoc-selftest-", dir=str(PROJECTS)))
    tmp = _dir / "ORCHESTRATOR-DECISIONS-o99.md"
    tmp.write_text(_NONCONTIG, encoding="utf-8")
    try:
        saw = "E-IDORDER" in {f.code for f in check_doc(tmp)}
        _ns = argparse.Namespace(doc=str(tmp), dry_run=False, not_mine=True)
        import io as _io
        import contextlib as _ctx
        with _ctx.redirect_stdout(_io.StringIO()):
            rc = cmd_reorder(_ns)
        after = tmp.read_text(encoding="utf-8")
        cleared = "E-IDORDER" not in {f.code for f in check_doc(tmp)}
        ids = [e["id"] for e in parse_entries(after.split("\n"))[0]]
        # W1/W3 keep their POSITIONS - only the D blocks are redealt among the D slots.
        placed = ids == ["D3", "D21", "W1", "W3", "D22"]
        kept = sorted(_NONCONTIG.split("\n")) == sorted(after.split("\n"))
        good = saw and rc == 0 and cleared and placed and kept
        ok &= good
        print("  [%s] %-12s -> check saw it=%s, reorder fixed it=%s, order=%s, bytes kept=%s"
              % ("OK" if good else "FAIL", "reorder-pair", saw, cleared,
                 ",".join(ids), kept))
    finally:
        try:
            tmp.unlink()
            _dir.rmdir()
        except (OSError, UnicodeDecodeError):
            pass

    # META-GUARD: every command this file TELLS someone to run must exist.
    #
    # ⛔ E-IDSHAPE's remedy said `orchdoc.py renumber --doc <doc>` for as long as the check
    # existed, and there is no such subcommand. Fourth advertised-then-unavailable remedy here,
    # and the human hit one of the others himself by typing exactly what the tool printed.
    #
    # ⭐ Nothing could have caught it by reading: the string is in a remedy and the parser is
    # thousands of lines away, so the two are never in front of the same pair of eyes. This
    # compares them mechanically, which is the same argument as the fixture meta-guard below -
    # the tool's own TEXT is an artifact, so it can be checked against the tool.
    try:
        _src = pathlib.Path(__file__).read_text(encoding="utf-8", errors="replace")
        # ⛔ STRIP COMMENT LINES FIRST. This guard fired on its OWN comment - the
        # sentence explaining that a remedy named a nonexistent command reads, to a
        # matcher, exactly like a remedy naming it. Fourteenth description-vs-instance in
        # this workspace, inside the guard written for that class, a minute after writing
        # it. mentions.py exists for precisely this and I did not reach for it.
        #
        # A comment cannot be executed, so it cannot be a bad instruction.
        _src = "\n".join(_ln.split("#")[0] for _ln in _src.split("\n"))
        _named = set(re.findall(r"orchdoc\.py\s+([a-z][a-z-]{2,})", _src))
        _real = set(re.findall(r'add_parser\(\s*"([a-z][a-z-]+)"', _src))
        _ghost = sorted(n for n in _named if n not in _real)
        _good = not _ghost and bool(_real)
        ok &= _good
        print("  [%s] %-12s -> %s"
              % ("OK" if _good else "FAIL", "ghost-cmd",
                 "every command named in a message exists" if _good
                 else "named but not a subcommand: " + ", ".join(_ghost)))
    except OSError as _e:
        print("  [SKIP] %-12s -> %s" % ("ghost-cmd", str(_e)[:60]))

    # META-GUARD: every BLOCKING code must have a fixture above.
    #
    # E-ARCHIVEDMARKER shipped DEAD for one revision: the check code was present and
    # read entry["archived"], but a failed patch meant nothing ever WROTE that key, so
    # the condition was permanently false. Present in the source, never fires - which is
    # exactly how `orchdoc_stop_check.py` failed. A check with no fixture is presumed
    # dead, so an unfixtured blocking code now fails the selftest rather than being
    # trusted because it is visible in the file.
    covered = {want for want, _ in cases}
    missing = sorted(BLOCKING - covered - {"E-IO", "E-STALE"})
    if missing:
        ok = False
        print("  [FAIL] %-12s -> blocking codes with NO fixture: %s"
              % ("meta-guard", ", ".join(missing)))
    else:
        print("  [OK] %-12s -> every blocking code has a fixture" % "meta-guard")

    print("\nselftest %s" % ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


# ---- THE DERIVED-REGION MARKER: one definition, imported by every reader ----
#
# o5's catch. Once `commit`'s isolation gate began excluding generated regions from its
# content check, this marker became a CONTRACT between two tools - the generator that
# writes it and the gate that reads it. If they drift by one character the gate silently
# mis-classifies: over-counting loss (refusing legitimate landings) or, far worse,
# UNDER-counting it, so hand-authored lines inside a mis-marked region get clobbered.
#
# See memory/marker_format_is_a_contract.md - the worked example there had a marker fix
# for one reader open a second hole in another, invisibly, with its own tests passing.
#
# Three defences, all of them o5's:
#   1. ONE definition. Every reader matches the TOKEN, never the surrounding prose, so
#      the human-facing wording can be edited freely without breaking the contract.
#   2. Match on structural SHAPE, not on the sentence.
#   3. ⛔ An oracle that cannot find its own boundary must REFUSE, not guess. A
#      malformed marker fails loud rather than defaulting to an assumption in either
#      direction, because both defaults are wrong and one of them loses content.
PLATE_BEGIN_TOKEN = "ORCHDOC:PLATE:BEGIN"
PLATE_END_TOKEN = "ORCHDOC:PLATE:END"
PLATE_BEGIN = ("<!-- %s - generated by `orchdoc.py plate`. Do not hand-edit. -->"
               % PLATE_BEGIN_TOKEN)
PLATE_END = "<!-- %s -->" % PLATE_END_TOKEN


def marker_span(lines, begin_tok, end_tok, label="derived-region"):
    """
    (start_idx, end_idx) of a generated region, or (None, reason).

    Refuses on anything malformed: an unclosed BEGIN, an orphan END, duplicates, or an
    END before its BEGIN. Never guesses.

    PARAMETERISED over the token pair because there are now two generated regions - the
    plate and the schema index. Re-implementing these refusal rules for the second one
    would put the marker contract in two places, and a contract in two places is the
    defect this tool exists to prevent.
    """
    # STRUCTURAL match: the token must sit inside an HTML comment, and mentions inside
    # inline code spans are stripped first. A doc that DISCUSSES its own markers - this
    # tool's OrchDoc documents them by name - otherwise registers extra BEGINs and the
    # span becomes unresolvable. That is o8's "a checker that fires on documents about
    # itself" and it is the second time it has appeared, after `<details>` in prose.
    def _real(tok, l):
        return re.search(r"<!--[^>]*\b%s\b[^>]*-->" % re.escape(tok),
                         re.sub(r"`[^`]*`", "", l)) is not None

    # FENCED blocks are quoted text, not structure. Stripping INLINE code spans was not
    # enough: a doc that shows the marker format in a ```fenced example - which the
    # standard's own documentation does - had those lines read as real markers, so the
    # span resolved to the wrong region and `plate` OVERWROTE THE EXAMPLE. Actual content
    # destruction, from a doc that only described the tool.
    #
    # ⭐ Third time tonight that a generated/quoted block had to be excluded from being
    # parsed as structure (after E-ONEH1 and the orphan-subtitle scan), and this is the
    # oldest and most load-bearing of the three. The rule deserves stating once: IF A
    # READER WOULD NOT ACT ON IT, THE PARSER MUST NOT EITHER.
    in_fence, live = False, []
    for l in lines:
        if l.lstrip().startswith("```"):
            in_fence = not in_fence
            live.append(False)
            continue
        live.append(not in_fence)

    begins = [i for i, l in enumerate(lines) if live[i] and _real(begin_tok, l)]
    ends = [i for i, l in enumerate(lines) if live[i] and _real(end_tok, l)]
    if not begins and not ends:
        return None, None  # no such region at all: legitimate, not an error
    if len(begins) != 1 or len(ends) != 1:
        return None, ("malformed %s marker: %d BEGIN, %d END (expected 1 each)"
                      % (label, len(begins), len(ends)))
    if ends[0] < begins[0]:
        return None, "malformed %s marker: END appears before BEGIN" % label
    return (begins[0], ends[0]), None


def plate_span(lines):
    """The plate's region. Every caller predates marker_span; this keeps them honest."""
    return marker_span(lines, PLATE_BEGIN_TOKEN, PLATE_END_TOKEN)


def index_span(lines):
    """The schema index's region."""
    return marker_span(lines, INDEX_BEGIN_TOKEN, INDEX_END_TOKEN, "schema-index")


def findex_span(lines):
    """The findings index's region, at the head of section 4."""
    return marker_span(lines, FINDEX_BEGIN_TOKEN, FINDEX_END_TOKEN, "findings-index")


# THE registry of generated regions. Anything the tool WRITES lives here, and every
# consumer that must tell derived content from authored content iterates this list rather
# than naming regions itself.
#
# Gate 1 previously excluded only the plate, because the plate was the only generated
# region when it was written. Adding the schema index and the findings index silently
# left it two regions behind, and it duly refused a landing over `**Findings (53).**` -
# a line the tool itself emits. A gate that refuses over its own output teaches its user
# to reach for --override, and an override reflex disarms the gate for the case it exists
# to catch.
DERIVED_REGIONS = [
    ("meta", META_BEGIN_TOKEN, META_END_TOKEN),
    ("plate", PLATE_BEGIN_TOKEN, PLATE_END_TOKEN),
    ("schema-index", INDEX_BEGIN_TOKEN, INDEX_END_TOKEN),
    ("findings-index", FINDEX_BEGIN_TOKEN, FINDEX_END_TOKEN),
]


def derived_spans(lines):
    """[(lo, hi)] for every generated region, or (None, reason) if any is malformed.

    Refuses rather than guesses, for the same reason marker_span does: a boundary that
    cannot be located cannot support a claim about what is inside it.
    """
    spans = []
    for label, b, e in DERIVED_REGIONS:
        span, why = marker_span(lines, b, e, label)
        if why:
            return None, why
        if span:
            spans.append(span)
    return spans, None

KIND_SECTION = {
    "decision": "DECISIONS",
    "finding": "FINDINGS",
    "todo": "TO-DOS",
    "specimen": "SPECIMENS",
    "work": "ON CLAUDE'S PLATE",
}
# "todo" files to the HUMAN's plate (2.3). There was no kind meaning "my own work", so an
# orchestrator capturing its own next step had only the kind that puts it in front of the
# human - and the default silently pushed orchestrator work onto the plate, which is the exact
# direction this schema exists to prevent. Found by using the tool: two of o9's own build items
# landed in 2.3 within a minute of the sweep telling it 2.3 was correctly empty.
KIND_PREFIX = {"decision": "D", "finding": "F", "todo": "T", "specimen": "S",
               "work": "W"}


def _now_iso():
    from datetime import datetime
    return datetime.now().astimezone().isoformat(timespec='seconds')


def _today():
    from datetime import date
    return date.today().isoformat()


class DocPathError(Exception):
    """A doc argument that must be refused by name rather than acted on."""


def _confine(p):
    """Refuse any path that resolves outside the workspace.

    `--doc "../ESCAPED"` wrote a file outside the workspace - verified, not theoretical.
    A tool that can be deployed anywhere can be POINTED anywhere, and one that builds its
    target by string concatenation has no boundary at all. The workspace is the boundary,
    so the check is: resolve fully, then require containment.
    """
    try:
        rp = Path(p).resolve()
        root = Path(PROJECTS).resolve()
    except OSError as e:
        raise DocPathError("cannot resolve path: %s" % e)
    try:
        inside = rp == root or rp.is_relative_to(root)
    except AttributeError:                      # Python < 3.9
        inside = str(rp).startswith(str(root))
    if not inside:
        raise DocPathError(
            "refusing a doc outside the workspace.\n"
            "           target    : %s\n"
            "           workspace : %s\n"
            "           Set ORCHDOC_WORKSPACE if the workspace is elsewhere." % (rp, root))
    if rp.is_dir():
        raise DocPathError("that path is a DIRECTORY, not a document: %s" % rp)
    return rp


def resolve_doc_arg(val):
    """Accept 'o7', 'ORCHESTRATOR-DECISIONS-o7.md', or a full path."""
    if not val:
        return None
    p = Path(val)
    if p.exists():
        return _confine(p)
    if re.fullmatch(r"o\d+", val):
        return _confine(PROJECTS / ("ORCHESTRATOR-DECISIONS-%s.md" % val))
    return _confine(PROJECTS / val)



def sanitize_field(text, limit=400):
    """Narrow caller-supplied text so it cannot impersonate generated structure.

    Not an escape - a NARROWING. The result must be safe to place inside a heading or a
    field line, so anything that could be read as structure is removed rather than encoded:

      * newlines      -> spaces. A title is one line; a multi-line title forged whole
                         sections and entries.
      * ORCHDOC:*     -> defanged. A caller who writes a generated marker can otherwise
                         wedge the document permanently: two ENDs make the span
                         unresolvable, so `scaffold` refuses forever.
      * leading #     -> stripped, so text cannot become a heading.
      * **Status:** and friends -> defanged. This is the important one. Injecting a Status
                         field made an entry read RESOLVED that had never been ruled, which
                         is precisely the false-done this tool exists to prevent.
      * `<!--` / `-->` -> defanged, so a comment cannot be opened or closed.

    Truncated at `limit`, because an unbounded title is its own denial of service on a
    document meant to be read.
    """
    # ASCII replacements only. A non-raw "\u2024" inside a re.sub REPLACEMENT is not a
    # unicode escape at all - re rejects it as a bad escape - and ASCII also keeps this
    # safe on a cp1252 Windows console, where a stray glyph is its own crash.
    t = (text or "")
    t = re.sub(r"[\r\n\t]+", " ", t)                       # a title is ONE line
    t = re.sub(r"ORCHDOC:([A-Z]+):(BEGIN|END)",             # cannot forge a marker
               r"ORCHDOC_\1_\2", t)
    t = t.replace("<!--", "<!- ").replace("-->", " -!>")    # cannot open/close a comment
    t = re.sub(r"\*\*(Status|Owner|Opened|Depends|Touches|Attested-by|Reviewed|Resolved|"
               r"Recorded|Enriched):\*\*", r"(\1:)", t)     # cannot forge a FIELD
    t = re.sub(r"^[\s#>*\-]+", "", t)                       # cannot become a heading
    t = re.sub(r"\s{2,}", " ", t).strip()
    if len(t) > limit:
        t = t[:limit].rstrip() + "..."
    return t



def write_doc(path, text):
    """Write atomically. A partial OrchDoc is worse than a failed command.

    Every write here was read-modify-write straight onto the real file, so an interrupt, a
    full disk or a crash mid-write left a TRUNCATED document - and the document is the
    durable record this entire tool exists to protect.

    temp-beside-target + flush + fsync + os.replace(). os.replace is atomic on POSIX and on
    Windows, so a reader sees either the old file or the new one, never a half-written one.
    Beside the target, not in $TMP, because a cross-volume replace is not atomic.
    """
    path = Path(path)
    tmp = path.with_name(path.name + ".orchdoc-tmp-%d" % os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(str(tmp), str(path))
    except OSError as e:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise DocPathError("cannot write %s: %s" % (path.name, e))



def actor_id():
    """Who is running this. $CLAUDE_ORCH_ID, else DERIVED from the doc being written.

    o8: "my override recorded `by unknown` - if --override is meant to attribute, the
    identity resolution is not picking up the caller."

    Correct, and "unknown" is the worst possible default for an ATTRIBUTION field: it
    satisfies the format while carrying none of the information the field exists for, and
    an audit record naming nobody is a record nobody can be asked about. Nothing sets
    $CLAUDE_ORCH_ID today, so every override in every doc says "unknown".

    A doc named ORCHESTRATOR-DECISIONS-o8.md is being written by o8 in every case that has
    ever occurred. That inference is not certain, so it is marked: "o8?" rather than "o8".
    A hedged right answer beats a confident empty one.
    """
    env = os.environ.get("CLAUDE_ORCH_ID")
    if env:
        return sanitize_field(env, 40)
    return "unattributed"


def actor_for(doc):
    """actor_id(), or the doc's own orchestrator id marked as inferred.

    ⛔ BOTH IDENTITY VARIABLES, because this file already had two and they did not know about
    each other. `$CLAUDE_ORCH_ID` was read here; `$ORCHDOC_ME` is what the OWNERSHIP GUARD reads
    (who_am_i / refuse_if_not_mine). o10 configured `$ORCHDOC_ME`, was correctly recognised as
    the doc's owner, and still had every attestation stamped `by=o10?` - hedged as an inference
    while the tool knew exactly who they were, four screens away.

    ⭐ One fact, two writable homes, no shared reader - the same defect shape as the marker
    format and the two definitions of "closed". A hedge is the right answer when the id is
    GUESSED; it is a wrong answer when it was configured and simply not looked at.
    """
    for var in ("CLAUDE_ORCH_ID", "ORCHDOC_ME"):
        env = (os.environ.get(var) or "").strip()
        if env:
            return sanitize_field(env, 40)
    m = re.search(r"ORCHESTRATOR-DECISIONS-(o\d+)", str(doc))
    return ("%s?" % m.group(1)) if m else "unattributed"


def next_id(entries, prefix, text=""):
    """Allocate the next free numeric id for a prefix. Never grep by hand again.

    ⛔ ARCHIVED IDS COUNT. `archive` strips the live-looking id out of a heading, so an
    archived entry stops parsing as an entry - and on 2026-08-13 this handed out W14 twice,
    minutes after the archiver shipped. "Ids are never reused" is the invariant every
    cross-reference rests on, and the tidy-up that made archived entries look tidy is what
    broke it.

    ⭐ The retired ids are still IN the text, in the form ARCHIVED_ID_RE reads. Scanning the
    raw document rather than the parsed entries is the point: the parser deliberately does
    not see archived entries, so asking it is asking the wrong witness.
    """
    hi = 0
    pat = re.compile(r"^%s(\d+)$" % re.escape(prefix))
    for e in entries:
        m = pat.match(e["id"])
        if m:
            hi = max(hi, int(m.group(1)))
    for m in ARCHIVED_ID_RE.finditer(text or ""):
        mm = pat.match(m.group(1))
        if mm:
            hi = max(hi, int(mm.group(1)))
    return "%s%d" % (prefix, hi + 1)


REGISTRY = PROJECTS / "ORCHESTRATOR-REGISTRY.md"


def registry_status(oid):
    """The status word the REGISTRY carries for this orchestrator, or None.

    \u2b50 A DATE SAYS WHAT HAPPENED; THE REGISTRY SAYS WHAT WAS INTENDED. Three docs read COLD
    on their stamps - o2, o3, o5 - and every one is quiet because the human decided it: o2 parked
    until iOS work begins, o3 retired into o8, o5 consolidated. Flagging a completed decision as
    a problem is how a report trains its reader to ignore it.

    The registry is the record of that intent, so it is the authority on whether quiet is a
    finding. Parsed by SHAPE - the last cell of the row whose first cell is `oN` - rather than
    by prose, because the descriptions in that column are long and change often.
    """
    try:
        text = REGISTRY.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        if cells[0].strip("`* ").lower() != oid:
            continue
        last = cells[-1].lower()
        for word in ("retired", "graduated", "dormant", "active", "consolidated"):
            if word in last:
                return word
    return None


SELF_STAMP = re.compile(
    r"\*\*(?:Attested-by|Reviewed|Opened|Recorded|Resolved):\*\*[^\n]*?(\d{4}-\d{2}-\d{2})")


def last_worked(doc):
    """Days since this orchestrator last stamped its OWN doc, or None if it never has.

    \u2b50 THE ONLY UNCONTAMINATED SIGNAL, and three others were tried first. `lastActivityAt`
    counts messages delivered TO a session - a broadcast to nine orchestrators reset all nine to
    zero. `isArchived` is False for every one of them, so it does not carry the "Whisper
    Archived" sidebar grouping. The last commit touching the file is `deliver.py` writing it
    into the shared checkout. Each is a true value about the wrong thing.

    A dated stamp inside the doc is written by that orchestrator while doing the work. No other
    session can move it, which is exactly the property the other three lacked.
    """
    try:
        text = doc.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    dates = sorted(set(SELF_STAMP.findall(text)))
    if not dates:
        return None
    try:
        newest = _dt.date.fromisoformat(dates[-1])
    except ValueError:
        return None
    return (_dt.date.today() - newest).days


def cmd_sessions(args):
    """Which OrchDocs have a session naming them, and which do not.

    ⭐ THE ALARM FOR A FAILURE THAT IS OTHERWISE SILENT. The Stop hook identifies a session
    from its NAME, so a rename the matcher cannot parse makes that session invisible to the
    hook - and invisible looks exactly like "not an orchestrator". the human, 2026-09-04, having
    prefixed his session names with hyphens and periods: *"there may be others later."*

    Widening the matcher covers the prefixes anyone thought of. This covers the one nobody did,
    by asking the question from the other end: every OrchDoc declares an owner in its filename,
    so does any session name that owner? A doc with no session is either a retired orchestrator
    or a rename that broke identification, and the two are told apart by looking.
    """
    docs = {}
    for p in sorted(PROJECTS.glob("ORCHESTRATOR-DECISIONS-*.md")):
        oid = doc_owner_id(p)
        if oid:
            docs[oid] = p.name

    seen = {}
    try:
        for f in APP_SESSIONS.rglob("local_*.json"):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            title = (d.get("title") or "").strip()
            m = TITLE_ID.match(title)
            if m:
                seen.setdefault(m.group(1).lower(), []).append((title, d.get("isArchived")))
    except Exception as e:
        print("  [WARN] could not read the session store: %s" % e)

    unnamed = [o for o in docs if o not in seen]
    orphan = [o for o in seen if o not in docs]

    # \u26d4 THIS COUNT ANSWERS ONE QUESTION AND IS EASY TO READ AS ANOTHER. o11, 2026-09-04:
    # *"Your '10 of 10' is measuring naming, not liveness - a true count read as answering a
    # question it does not answer."* So the line says what it measures, and the column beside
    # it says what it does not.
    print("orchestrator docs: %d   of which the hook can IDENTIFY a session for: %d"
          % (len(docs), len(seen)))
    print("(identifiable is not active - see LAST-WORKED, which no other session can move)")
    print()
    for oid in sorted(docs, key=lambda s: int(s[1:])):
        rows = seen.get(oid) or []
        live = [t for t, arch in rows if not arch]
        mark = "ok  " if live else ("arch" if rows else "NONE")
        age = last_worked(PROJECTS / docs[oid])
        when = ("never stamped" if age is None
                else "today" if age == 0
                else "%d days" % age)
        # \u26d4 COLD ONLY MEANS SOMETHING FOR AN ACTIVE ORCHESTRATOR. o2, o3 and o5 are all
        # quiet because the human decided they should be; calling that COLD reports a settled
        # decision as a problem, and a report that cries wolf gets skimmed.
        status = registry_status(oid)
        quiet = age is None or age > 14
        flag = (" <- COLD" if quiet and status in (None, "active")
                else "  (%s)" % status if status else "")
        print("  [%s] %-5s %-30s %-14s %s%s"
              % (mark, oid, docs[oid][:30], when,
                 (live or [t for t, _ in rows] or ["-- no session names this doc --"])[0][:30],
                 flag))

    if orphan:
        print()
        print("  sessions naming an orchestrator with no doc: %s" % ", ".join(sorted(orphan)))
    if unnamed:
        print()
        print("  \u26d4 %d doc(s) have NO session naming them. Each is either a retired"
              % len(unnamed))
        print("     orchestrator - fine - or a session renamed into a shape the matcher no")
        print("     longer parses, in which case its Stop hook is SILENT and nothing else")
        print("     would say so. Check the titles in the sidebar against: %s"
              % ", ".join(sorted(unnamed)))
    return 0


def cmd_registry(args):
    """Scope conflicts in ORCHESTRATOR-REGISTRY.md - the parts that are FACTS, not prose.

    \u26d4 THE REGISTRY IS READ BY ALMOST NOTHING. Before this, `orchdoc.py` touched it in one
    place - `registry_status()`, for an active/retired word. Its SCOPE column, which decides who
    owns what, was validated by no hook, gate or task. o1 measured that as their F24 after a
    day in which a the human ruling sat in two OrchDocs while both registry rows still described the
    old world.

    \u2b50 WHAT IS CHECKABLE IS NOT WHAT HURT. o1's headline case was two rows whose prose
    scopes were individually accurate and jointly ambiguous - and prose cannot be diffed, so a
    check aimed at it would fire on judgement and earn overrides. But the HARM in that case was
    not ambiguity: `service_repo_1` was owned by nobody on paper. Ownership of a NAMED
    REPO is a fact, and it is the half worth mechanising.

    Measured on the live registry before choosing what refuses:
        one repo claimed by TWO rows    1  -> ERROR
        a declared repo claimed by NONE 9  -> reported, never fails; most are correct
        terminal row, dead pointer      0  -> free to include

    \u26a0\ufe0f `--strict` also fails on the unowned count. Off by default because 9 of 12 declared
    repos have no row and most should not - <private-repo> is infrastructure, not a
    workstream - so a gate on it would refuse from the first run, which is the 173-override
    shape this file already carries the scar of.
    """
    reg = PROJECTS / "ORCHESTRATOR-REGISTRY.md"
    if not reg.exists():
        print("no ORCHESTRATOR-REGISTRY.md at %s" % reg, file=sys.stderr)
        return 2
    text = reg.read_text(encoding="utf-8", errors="replace")

    rows = []
    for ln in text.split("\n"):
        if not ln.startswith("|"):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        oid = cells[0].strip("`* ")
        if re.fullmatch(r"o\d+", oid):
            rows.append((oid, cells[1], cells[-1]))

    rc = 0
    print("orchdoc registry - %d row(s)" % len(rows))

    # --- A. one repo, two owners. ---
    #
    # ⛔ THE FIRST VERSION CALLED A HANDOVER NOTE A CONFLICT, and the human caught it on the day it
    # shipped. o1's row names `<your-github-user>/service_repo_1` in order to say the repo is
    # **o10's** - the documentation working exactly as intended, recording where a surface went.
    # Matching the repo name and counting it as a claim is the token-without-its-role error, in
    # a check whose whole argument for existing was that repo ownership is a FACT rather than
    # prose. It is a fact; WHICH ROW ASSERTS IT is still prose.
    #
    # ⭐ A mention that also names ANOTHER orchestrator in the same sentence is a handover note.
    # Measured on the live registry: that rule turns 1 false conflict into 0 conflicts and
    # correctly reads o1's row as a disclaimer.
    #
    # ⚠️ IT UNDER-DETECTS, AND THAT IS THE HONEST TRADE. o10's row says "SOLE owner of
    # <your-github-user>/product-app ... Scope carved OUT of o1", so its own genuine claim reads
    # as a handover note too. A real conflict could hide that way. The direction is deliberate:
    # this check exists to catch a surface owned by nobody or by two people, and a false alarm
    # on correct documentation is what teaches everyone to ignore it.
    claims, notes = {}, []
    for oid, desc, _status in rows:
        for sent in re.split(r"(?<=[.!?])\s+|—|–", desc):
            for m in re.finditer(r"<your-github-user>/([A-Za-z0-9._-]+)", sent):
                repo = m.group(1).lower()
                others = sorted(set(re.findall(r"\bo\d+\b", sent)) - {oid})
                if others:
                    notes.append((oid, repo, others))
                else:
                    claims.setdefault(repo, []).append(oid)
    clash = {r: sorted(set(o)) for r, o in claims.items() if len(set(o)) > 1}
    if clash:
        rc = 1
        print()
        for repo, owners in sorted(clash.items()):
            print("  [ERROR] %s is claimed by %s" % (repo, " and ".join(owners)))
        print("          Two rows naming one repo is a conflict, not a division of labour -")
        print("          a lane reading either row concludes it owns the whole thing.")
        print("          Fix: narrow one row, or name the split explicitly in both.")
    else:
        print("  [ok] no repo is claimed by two rows")
    # Shown always: a reader checking ownership wants to see the handovers, and printing them
    # is what makes the under-detection above visible rather than silent.
    for oid, repo, others in notes:
        print("  [note] %s's row names %s while pointing at %s - read as a handover, not a claim"
              % (oid, repo, ", ".join(others)))

    # --- C. a terminal row must point somewhere that exists ---
    dead = []
    for oid, desc, status in rows:
        if not re.search(r"retired|superseded|graduated|consolidat", status, re.I):
            continue
        for t_ in sorted(set(re.findall(r"\b(o\d+)\b", desc)) - {oid}):
            if not (PROJECTS / ("ORCHESTRATOR-DECISIONS-%s.md" % t_)).exists():
                dead.append((oid, t_))
    if dead:
        rc = 1
        for oid, t_ in dead:
            print("  [ERROR] %s is terminal and points at %s, which has no OrchDoc" % (oid, t_))
    else:
        print("  [ok] every terminal row points at a doc that exists")

    # --- B. reported, never fatal unless --strict ---
    try:
        voc = json.loads((Path(__file__).resolve().parent
                          / "facets_vocabulary.json").read_text(encoding="utf-8"))
        repos = [n for n in voc.get("nested_repos", []) if n]
    except Exception:
        repos = []
    low = text.lower()
    unowned = [r for r in repos if r.lower() not in low]
    print("  [note] %d of %d declared product repo(s) are named in no row: %s"
          % (len(unowned), len(repos), ", ".join(sorted(unowned)[:6]) or "-"))
    print("         Not an error - most should not have one. It is the number to watch when a")
    print("         repo starts getting real work and nobody has said whose it is.")
    if unowned and getattr(args, "strict", False):
        rc = 1
    return rc


def cmd_whoami(args):
    """
    Print THIS session's send_message id, verified by the refusal oracle.

    Two different session identifiers live in the environment and they do not match:
      CLAUDE_CODE_SESSION_ID       the transcript/scratchpad id (a BARE uuid)
      CLAUDE_CODE_HOST_SESSION_ID  what send_message wants (prefixed 'local_')

    o9 derived its id from the scratchpad path and published a dead address to seven
    sessions. o1 made the same error in the other direction. o7's contribution: a bare
    UUID with no 'local_' prefix is NEVER a valid target, which is a free string check.

    ⛔ THE POSITIVE CONFIRMATION USED TO BE A REFUSAL ORACLE - `get_session` refusing on
    your own id - AND IT NO LONGER REFUSES. Measured 2026-09-03 on a correct id: it returns
    full metadata. o1 and o11 each hit it independently, and o1 nearly published their
    transcript id as a result. It is not a regression to wait out: the tool now documents
    `self` lookup as a feature, so the refusal is not coming back.

    ⭐ The replacement is stronger than what it replaces. `get_session('self')` returns a
    sessionId, and comparing it to the env var is an exact equality with no judgement in it -
    where the old oracle asked the messaging system to confirm the messaging system's own id,
    and a title check would ask a human to eyeball a string.
    """
    host = os.environ.get("CLAUDE_CODE_HOST_SESSION_ID", "")
    tran = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    bound = _bound_orchestrator()
    if bound:
        print("orchestrator         : %s   (from this session's NAME - static, not learned)"
              % bound)
        print()
    print("\u26d4 DO NOT PUBLISH ANYTHING BELOW. ASK THE TOOL:")
    print()
    print("      get_session(session_id='self')   ->  .sessionId IS your send_message id")
    print()
    print("   One call, authoritative, nothing to derive. o7, 2026-09-03: if you never")
    print("   construct a candidate, the transcript-vs-messaging trap cannot fire. The")
    print("   values below are a CROSS-CHECK, not the answer.")
    print()
    print("   ⛔ AND THE TWO IDS ARE NOT HARD TO TELL APART - a warning in circulation")
    print("   says they look identical in form. They do not: one carries local_ and the")
    print("   other is a bare uuid, which is a free string test. Telling them apart was")
    print("   never the problem. HAVING TWO CANDIDATES was, and self removes that.")
    print()
    print("environment says:")
    print("  send_message id : %s" % (host or "<UNSET>"))
    print("  transcript id   : %s   (NOT a messaging target)" % (tran or "<unset>"))
    print()
    if not host:
        print("[note] CLAUDE_CODE_HOST_SESSION_ID is unset - the env cross-check is")
        print("       unavailable. That does NOT block you: get_session('self') still")
        print("       answers. Do NOT guess from a path.")
        return 0
    if not host.startswith("local_"):
        print("[FAIL] no 'local_' prefix - this is a transcript id, not a messaging id.")
        return 1
    print("[OK]   prefix check passed (free, no tool call - a bare uuid is never a target).")
    print()
    print("CROSS-CHECK, once you have called it:")
    print("  self.sessionId == %s   -> environment agrees; nothing to do" % host)
    print("  anything else                                  -> TRUST 'self', and say so:")
    print("     the env var is wrong in this session, which is worth reporting.")
    print()
    print("  (History: this used to say 'confirm with the refusal oracle - get_session")
    print("   REFUSES on your own id'. It does not any more; it returns your metadata by")
    print("   design, and 'self' is a documented argument. An agent following that text")
    print("   got NEITHER documented outcome on a CORRECT id, and the only failure branch")
    print("   it had been given said 'wrong id, do not publish' - so the check steered you")
    print("   away from the right answer. o1 nearly published a wrong id because of it.")
    print("   Found by o1, o7 and o11 independently, 2026-09-03.)")
    return 0


def cmd_add(args):
    """
    Near-free capture. The cost of a good entry is what makes deferral rational, and
    deferred means dropped - so this writes a STUB in one action and prints the anchor.

    o7's design point: the command's OUTPUT is the pointer you paste to the human. RECORD and
    POINT collapse into one action, so the anchor stops being a third step you can skip
    and becomes the receipt for the second.
    """
    doc = resolve_doc_arg(args.doc)
    if not doc or not doc.exists():
        print("no such doc: %s" % doc, file=sys.stderr)
        return 2
    if refuse_if_not_mine(doc, "add", getattr(args, "not_mine", False)):
        return 1

    # ⛔ THE ALLOCATOR COUNTS FROM THE FILE IT CAN SEE, SO A STALE TREE MINTS COLLIDING IDS.
    #
    # Measured 2026-09-06. Two lanes wrote the human's personal scripts on the same day. Each ran
    # this command inside its own worktree, cut from origin/main BEFORE the other lane's
    # entries landed. Both were handed ids that looked free, both wrote those ids into their
    # process logs, and neither committed the OrchDoc. Eleven ids across two logs pointed at
    # entries that existed nowhere, and the worktrees were gone by the time anyone looked.
    #
    # ⭐ A REFUSAL WITH A NAMED OVERRIDE, NOT A HARD STOP. An offline run, a fresh clone and a
    # detached checkout are all legitimate; `behind_canonical` returns "unknown" for those and
    # this proceeds with a warning. It refuses ONLY on a measured, non-zero behind-count, and
    # names --allow-stale in the refusal so a deliberate stale capture stays one word away and
    # is visible afterwards in the shell history.
    if not getattr(args, "allow_stale", False):
        state, n, detail = behind_canonical(doc.parent,
                                            fetch=not getattr(args, "no_fetch", False))
        if state == "behind":
            print("[REFUSE] this working copy is %d commit(s) behind %s."
                  % (n, CANONICAL_REF), file=sys.stderr)
            print("         `add` allocates the next id by counting the entries in the file"
                  " it can\n         see. From a tree this far back it can hand you an id that"
                  " already\n         belongs to someone else, and nothing downstream would"
                  " catch it.", file=sys.stderr)
            print("\n         Nothing was written.", file=sys.stderr)
            print("\n         DO THIS:  git -C %s pull --ff-only" % doc.parent, file=sys.stderr)
            print("         Then run the same `add` again.", file=sys.stderr)
            print("\n         Genuinely need to capture from this tree - offline, or a"
                  " deliberate\n         historical entry? Pass --allow-stale. Then VERIFY the"
                  " id it returns is\n         free on %s before you write it anywhere."
                  % CANONICAL_REF, file=sys.stderr)
            return 1
        if state == "unknown":
            print("[WARN] could not compare this tree to %s (%s)." % (CANONICAL_REF, detail))
            print("       Proceeding. The id below was allocated from the local file only,"
                  " so it\n       is free HERE and unverified anywhere else.")

    # ⛔ THE NAMESPACE FOLLOWS THE OWNER, NOT THE WORD "DECISION" (the human, 2026-08-13).
    #
    # D<n> lives in §2, which is THE HUMAN'S PLATE. W<n> lives in §3, which is the orchestrator's
    # own work. So `--kind decision` is only correct when the human is the one who has to decide.
    # A decision the ORCHESTRATOR made and shipped is its work - a W - however much it felt
    # like a decision while making it.
    #
    # Measured: o9 filed D6 ("the tombstone contract is code, not prose") as a decision it
    # owned. It was already made, already shipped, already adopted by o10 - and it sat in
    # the human's section wearing a red OPEN marker until he asked what he was supposed to decide.
    # The answer was nothing. That is a withdrawal from the only scarce resource for zero
    # return, and the tool permitted it silently.
    #
    # ⭐ Refused at CREATION rather than linted afterwards: a misfiled entry has to be found,
    # renumbered and moved across sections, and every cross-reference to its id rots. Cheap
    # here, expensive anywhere later.
    # ⛔ THE HUMAN'S NAME IS VOCABULARY, AND THIS TEST HAD IT HARDCODED IN LOWER CASE - which
    # made it the one leak in this file the publisher's rewrite could not reach, because that
    # rewrite is case-sensitive and this comparison is not. The denylist caught it.
    #
    # ⭐ WORSE THAN A LEAK, AND THIS IS THE PART WORTH KEEPING: sanitising the DEFAULT below
    # without also fixing this set produces a published tool that refuses EVERY
    # `--kind decision`, because the rewritten default is not a member of a list that still
    # names the original. Clean-looking file, broken on first use in a stranger's repo - the
    # exact class of bug the publisher's own selftest probe exists to catch.
    #
    # So: generic names in code, the workspace's own aliases from the vocabulary. Both copies
    # are then correct, and the sanitised default is already in the generic half.
    if args.kind == "decision":
        human_owners = {"human", "the human", "user", "owner"}
        try:
            import json as _json
            _voc = Path(__file__).resolve().parent / "facets_vocabulary.json"
            human_owners |= {str(n).lower()
                             for n in _json.loads(_voc.read_text(encoding="utf-8"))
                             .get("human_owner_aliases", [])}
        except Exception:
            pass                      # no vocabulary: the generic names still work
        owner = (args.owner or "the human").strip()
        if owner.lower() not in human_owners:
            print("[REFUSE] --kind decision puts this in §2, which is THE HUMAN'S PLATE - but "
                  "you set --owner %s." % owner, file=sys.stderr)
            print("         The namespace follows the OWNER, not the word 'decision'. A call "
                  "you made\n         and shipped is your WORK, even though deciding it felt "
                  "like a decision.", file=sys.stderr)
            print("         D<n> = the human has to rule on it.  W<n> = you own it.",
                  file=sys.stderr)
            print("\n         Use:  --kind work --owner %s" % owner, file=sys.stderr)
            print("         Or drop --owner if the human genuinely has to rule on this.",
                  file=sys.stderr)
            return 1

    # The lock spans read-modify-write. Without it two concurrent orchestrators either
    # allocate the SAME id or lose a write - the collision `add` exists to prevent (o7).
    with _lock(doc):
        text = doc.read_text(encoding="utf-8")
        lines = text.splitlines()
        entries, sections = parse_entries(lines)

        prefix = args.prefix or KIND_PREFIX[args.kind]
        eid = args.id or next_id(entries, prefix, text)
        if any(e["id"] == eid for e in entries):
            print("[REFUSE] id %s already exists in %s - ids are never reused"
                  % (eid, doc.name), file=sys.stderr)
            return 1

        today = args.date or _today()
        # Whoever this install belongs to, not a hardcoded name.
        owner = args.owner or (human_name().title() if args.kind == "decision"
                               else "orchestrator")

        entry = [
            "",
            # Emit the marker HERE. `add` writes Status: OPEN, so it already knows which
            # marker the heading needs - and without it every `add` was immediately
            # followed by E-MARKERDRIFT, so the documented flow (add -> plate -> check)
            # failed its own check every single time.
            #
            # ⭐ A gate that fires on the tool's own correct output is the cry-wolf failure:
            # it teaches the user that red means "normal", and then it cannot warn them
            # about anything. Emit the correct state; do not report the wrong one.
            "### %s %s - %s" % (STATUS_MARKER["OPEN"], eid,
                                sanitize_field(args.title, 200)),
            "",
            "**Status:** OPEN - **Owner:** %s - **Opened:** %s - **Enriched:** NO"
            % (owner, today),
            "",
            "_Stub captured at decision time. Enrich when load is low: paths, why it matters,",
            "the recommendation, and what is blocked until it is answered._",
            "",
        ]

        want = KIND_SECTION[args.kind]
        target = None

        # ⛔ RESOLVE BY SECTION NUMBER FIRST. The schema deliberately gives the live and
        # completed sections the SAME titles - "§2.1 Decisions" and "§99.1 Decisions" -
        # because that symmetry is what makes `archive` a mechanical MOVE rather than a
        # judgement call. But it also made the title ambiguous, and this loop took the
        # LAST match with no break, so every newly captured OPEN decision was filed under
        # §99 COMPLETED: invisible on the plate, and not flagged by `check`, because a
        # decision sitting in a completed section is exactly what that section is for.
        #
        # ⭐ The symmetry that made archiving mechanical made filing ambiguous. A design
        # that removes one judgement call can silently create another somewhere else.
        live_num = KIND_SECTION_NUM.get(prefix[0], (None, None))[0]
        if live_num:
            for s in sections:
                if s["level"] == 2 and re.match(r"^\W*\s*§?\s*%s\b" % re.escape(live_num),
                                                s["title"]):
                    target = s
                    break

        if target is None:
            for s in sections:
                # level 2 only. Matching any heading let the h1 "Orchestrator DECISION
                # Doc" capture every decision and file it at the top of the document.
                # FIRST match, not last: on a schema doc the live section always precedes
                # its §99 counterpart.
                if s["level"] == 2 and want.rstrip("S") in s["title"].upper():
                    target = s
                    break
        if target is None:
            lines += ["", "## %s" % want, ""]
            insert_at = len(lines)
        else:
            # ⛔ A SECTION ENDS AT THE NEXT SCHEMA SECTION, NOT AT THE NEXT `##`. `parse_entries`
            # calls every heading of level <= 2 a section, and entry BODIES in this doc use `##`
            # sub-headings freely - 19 of the 32 `##` lines in o9's doc are inside an entry. So
            # the old scan inserted the new stub in the MIDDLE of the last entry, and every
            # sub-heading below the split silently became the new entry's body.
            #
            # Measured 2026-09-08: adding F121 reparented six of F120's sub-sections. The gate
            # did not object - both entries still parsed, both still had a status line - so the
            # only signal was reading the file.
            #
            # ⭐ Schema sections all carry `§`; body sub-headings never do. Fall back to the old
            # scan when no later `§` section exists, so a doc that does not use the schema is
            # placed exactly as before rather than dumped at EOF.
            insert_at = len(lines)
            later = [s for s in sections if s["line"] > target["line"]]
            schema = [s for s in later if "§" in s["title"]]
            for s in (schema or later):
                insert_at = s["line"] - 1
                break

        out = lines[:insert_at] + entry + lines[insert_at:]
        write_doc(doc, "\n".join(out) + "\n")

    print("[ADDED] %s" % eid)
    print()
    print("  doc      : %s" % doc.name)
    print("  section  : %s" % (target["title"] if target else want))
    print("  status   : OPEN (unenriched)")
    print()
    print("PASTE THIS TO THE HUMAN (an ID anchor, which cannot rot - never a line number):")
    print("  %s - %s, section \"%s\"" % (eid, doc.name, target["title"] if target else want))
    if args.kind == "decision":
        print()
        # ⛔ THE MOTION TWIN IS RETIRED. the human ruled 2026-08-19 (o11:Q1): Motion is being
        # deprecated - "its core benefit was AI powered scheduling. Placing the process in your
        # hands supersedes that. Everything that needs to be done and tracked is currently
        # tracked in the Orch's OrchDocs - and that is typically where I work out of anyway."
        #
        # ⭐ BUT THE REASON THE REMINDER EXISTED SURVIVES THE TOOL, and deleting it outright
        # would lose the finding: a decision on his plate is invisible unless it is ALSO
        # somewhere he actually looks. Motion was an answer to that and it was the WRONG one -
        # o11 measured that he does not look at Motion either. The plate block is what he reads,
        # so that is what the reminder now names.
        print("REMINDER: this is only real if it is in the PLATE BLOCK - that is what the human")
        print("          actually reads. A decision he cannot see is a decision nobody made,")
        print("          and one workstream lost six weeks to exactly that.")
        print("          Do NOT create a Motion twin: Motion is retired (the human, 2026-08-19).")
    return 0


def _split_by_seen(doc, rel, ref, lost):
    """(seen, unseen) - lines the author had when they wrote this copy, and lines they did not.

    ⛔ A line that entered `ref` AFTER the working copy's mtime was never in front of the
    author, so removing it cannot have been a decision. A line that was already there was.
    That is the distinction gate 1's message always claimed to make and never tested.

    Fails CLOSED: if the mtime or the log cannot be read, everything counts as unseen and the
    gate behaves exactly as it did before. A discriminator that cannot answer must not become
    permission.
    """
    try:
        mtime = _dt.datetime.fromtimestamp(doc.stat().st_mtime).astimezone()
    except OSError:
        return [], list(lost)
    since = mtime.isoformat()
    rc, blob, _ = git(["log", "--format=%H", "--since=%s" % since, ref, "--", rel])
    if rc != 0:
        return [], list(lost)
    shas = [s for s in blob.split() if s]
    if not shas:
        return list(lost), []          # ref has not moved since; every removal was informed
    added = set()
    for sha in shas[:40]:
        rc2, diff, _ = git(["show", "--format=", "--unified=0", sha, "--", rel])
        if rc2 != 0:
            continue
        for ln in diff.split("\n"):
            if ln.startswith("+") and not ln.startswith("+++"):
                added.add(ln[1:].strip())
    seen, unseen = [], []
    for l in lost:
        (unseen if l.strip() in added else seen).append(l)

    # ⛔ MTIME IS THE WRONG CLOCK, AND THIS IS THE PART IT GETS WRONG. The docstring's reasoning
    # is right - a line that reached canonical after the author's copy was written was never in
    # front of them - but st_mtime marks the END of an edit window, not the start:
    #
    #   11:40  a session reads the doc into context
    #   11:53  ANOTHER session lands a restamp, so that line is now on canonical
    #   11:55  the first session writes its copy back -> mtime becomes 11:55
    #   11:56  commit asks "did this arrive after 11:55?" -> no -> SEEN -> allowed
    #
    # So the carve-out approves precisely the lines that arrived DURING someone's edit window,
    # which is the only window in which this loss can happen. Measured 2026-09-08: three of six
    # OrchDocs had an mtime newer than their last canonical commit, o10's among them - 11:56
    # against 11:54, the window in which o1's 11:53 restamps were destroyed.
    #
    # ⭐ A CROSS-ORCHESTRATOR ATTESTATION IS NEVER "EDITING". o1 lost F33 and F65 restamps they
    # had made on o10's doc with --not-mine, which exists so the session that made a change is
    # the one that attests to it. Removing someone ELSE'S stamp cannot be a rewrite of your own
    # work, whatever the timestamps say, so those never take the carve-out.
    #
    # ⚠️ Deliberately narrow. It keys on the ATTESTER being a different orchestrator, so it
    # cannot fire on a session reformatting or migrating its own stamps - which `write_restamp`
    # does by design, and which would otherwise turn this into the always-refusing gate whose
    # 173 overrides are recorded above.
    me = running_orchestrator()
    if seen and me:
        keep = []
        for l in seen:
            m = re.search(r"\*\*Attested-by:\*\*\s*(o\d+)\b", l)
            if m and m.group(1).lower() != me.lower():
                unseen.append(l)
            else:
                keep.append(l)
        seen = keep
    return seen, unseen


def _report_delivery(doc):
    """Does the copy the human actually opens match what just landed? Say so either way.

    ⛔ "LANDED on origin/main" and "the human can read it" are different claims, and the second is
    the one that matters. They coincide today only because this tool lands the WORKING-TREE copy
    in the shared checkout - a property nobody designed and nothing checked until now.
    """
    # ⛔ THE MAIN CHECKOUT, NOT THE CALLING WORKTREE. PROJECTS resolves to whatever tree is
    # running this, so from a per-orchestrator worktree it compared the landed copy against the
    # copy in that same worktree - which the tool had just written. It printed "DELIVERED. The
    # copy the human reads at C:\...\<your-workspace>-worktrees\o9\..." and the human does not read there.
    #
    # ⭐ A CHECK THAT COMPARES A FILE WITH ITSELF PASSES BY CONSTRUCTION. This is the delivery
    # check FAILING TO DELIVER, one day after it was built to catch exactly this, and it is the
    # same worktree-relative root as the false dead-ref and the checkout drift found today -
    # three instruments, one wrong assumption about which tree they are standing in.
    base = _main_worktree() or pathlib.Path(PROJECTS)
    reader = base / doc.name
    if not reader.exists():
        print("  ⚠️ NOT ON THE HUMAN'S DISK: %s is missing from %s" % (doc.name, base))
        return
    rc_a, disk, _e = git(["hash-object", str(reader)])
    rc_b, landed, _e2 = git(["rev-parse", "%s:%s" % (CANONICAL_REF, doc.name)])
    if rc_a != 0 or rc_b != 0:
        print("  ⚠️ could not compare the reader's copy - check by hand: %s" % reader)
        return
    if disk.strip() == landed.strip():
        print("  DELIVERED. The copy the human reads at %s is byte-identical." % reader)
        return

    # ⛔ DELIVER IT, DO NOT JUST REPORT IT. Since each orchestrator works in its own worktree,
    # this now differs after EVERY landing - the tool writes the worktree copy and nothing
    # updates the checkout the human opens. A check that fires every single time and asks a human to
    # reconcile is a check people learn to scroll past.
    #
    # ⭐ Gate 1 has already proved this landing removes nothing that was on the canonical ref,
    # and gate 3 has proved it is an ancestor of that ref - so writing the LANDED bytes into the
    # reader's copy cannot lose anything. That is the whole argument for doing it here rather
    # than telling someone to.
    if doc.resolve() != reader.resolve():
        rc, blob, _ = git(["show", "%s:%s" % (CANONICAL_REF, doc.name)])
        if rc == 0:
            try:
                reader.write_text(blob, encoding="utf-8", newline="")
                print("  DELIVERED. Wrote the landed copy to %s" % reader)
                return
            except OSError as e:
                print("  ⚠️ could not write the reader's copy: %s" % e)
    print("  ⛔ NOT DELIVERED. %s on the human's disk DIFFERS from what just landed." % doc.name)
    print("     He is reading something else. Point him at the landed version, or")
    print("     reconcile his copy - do NOT assume 'it is on main' reached him.")


def untracked_orchdocs():
    """OrchDocs that exist on disk and are on NO ref. Invisible to every freshness check.

    ⭐ Because those checks all compare against a ref the file was never on, they return "no
    difference" - not because it matches, but because there is nothing to compare. o11's doc sat
    like that while they quoted the human anchors into it.
    """
    root = pathlib.Path(PROJECTS)
    out = []
    for p in sorted(root.glob("ORCHESTRATOR-DECISIONS-*.md")):
        rc, _o, _e = git(["rev-parse", "--verify", "%s:%s" % (CANONICAL_REF, p.name)])
        if rc != 0:
            out.append(p.name)
    return out


def cmd_restamp(args):
    """Write an Attested-by clause onto ONE entry's plate line, with the bar applied first.

    ⛔ WHY THIS EXISTS: seven ad-hoc heading parsers were written in a single day - four by
    o1, three by o9 - all to do exactly this, all in throwaway scripts, none of whose authors
    would ever see another's. One of them mis-parsed an archived heading and credited an entry's
    body to the entry above it.

    ⭐ o1's correction to my own advice is the point. I told them to import the shared regex;
    a temp-directory script has no lifetime in which to be deduplicated. The fix is not sharing
    the parser - it is that nobody outside this file should ever need to find an entry by id.

    ⛔ AND THE OBJECTION IS ANSWERED RATHER THAN IGNORED. o1: a tool that makes the honest
    path frictionless makes the dishonest one frictionless too. True of the wrong friction.
    Writing a regex is friction that produces BUGS; the friction that protects is the
    ATTESTATION BAR, which is mechanical and unchanged. This applies it BEFORE the write, so
    the refusal lands while the author still remembers what they measured - rather than in a
    lint run afterwards, when the cheapest response is to weaken the sentence until it passes.
    """
    doc = resolve_doc_arg(args.doc)
    if not doc or not doc.exists():
        print("no such doc: %s" % doc, file=sys.stderr)
        return 2
    if refuse_if_not_mine(doc, "restamp", getattr(args, "not_mine", False)):
        return 1

    why = (args.because or "").strip()
    if len(why) < MIN_ATTESTATION_CHARS or RUBBER_STAMP_RE.match(why):
        print("[REFUSE] --because must NAME WHAT MOVED and why the entry survives it "
              "(%d+ chars)." % MIN_ATTESTATION_CHARS, file=sys.stderr)
        print("         'still current' / 'no change' is what E-RUBBERSTAMP exists to stop,",
              file=sys.stderr)
        print("         and refusing it here costs a retry instead of a lint round-trip.",
              file=sys.stderr)
        print("         `check --doc %s` names the mover and, for an OrchDoc, the entry ids"
              % args.doc, file=sys.stderr)
        print("         that commit actually touched.", file=sys.stderr)
        return 1

    # \u26d4 DO NOT WRITE A GUESS INTO A DURABLE ATTESTATION. actor_for() hedges an inferred id
    # with `?`, which is honest in a transient report and wrong in a line that will be read as
    # a record of who checked something. Refuse and name the two ways to answer it.
    who = args.by or actor_for(doc)
    if who.endswith("?"):
        print("[REFUSE] I can only INFER that you are %s, and an attestation records WHO"
              % who[:-1], file=sys.stderr)
        print("         checked something. Say it explicitly:", file=sys.stderr)
        print("           --by %s          (this call only)" % who[:-1], file=sys.stderr)
        print("           ORCHDOC_ME=%s    (every call in this session)" % who[:-1],
              file=sys.stderr)
        return 1
    rc = write_restamp(doc, args.id, why, who, args.at or _now_iso())
    if rc == 0:
        _warn_repeat_mover(doc, args.id, why)
        print("NEXT: orchdoc.py check --doc %s   then commit --doc %s --commit"
              % (args.doc, args.doc))
    return rc


def _warn_repeat_mover(doc, eid, why):
    """Say so when this entry has now been attested 3+ times against the SAME artifact.

    ⭐ o1's proposal, 2026-09-08, from a case they paid for: their F7 tripped five times on
    DEV-DOCS-INDEX.md, and every stamp repeated the same cause - that a doc was absent from the
    index because it sat in a dot-directory. That was FALSE; the directory was walked every time
    and the file simply had no marker, one line to fix. **Five reviews produced five stamps and
    zero action, because a structural-sounding cause reads as something to live with rather than
    something to recheck.**

    ⛔ THE REPETITION IS THE SIGNAL, NOT THE COUNT. An entry can legitimately depend on a file
    that changes often - measured across 246 attested entries, only 4 reach three stamps naming
    one artifact, and three of those are o9's own, where each stamp did name a real change. So
    this cannot decide which case it is looking at, and does not try.

    ⚠️ ADVISORY, AT RESTAMP TIME, AND THAT IS THE POINT. It fires while the writer is already
    looking at the entry, which is the only moment a re-check costs nothing. A `check` finding
    would arrive later, when they are landing something else.
    """
    try:
        text = doc.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    body, found = [], False
    for raw in text.split("\n"):
        m = re.match(r"^#{3,6}\s+[^A-Za-z0-9]*([A-Z]{1,3}\d+[a-z]?)\b", raw)
        if m:
            if found:
                break
            found = m.group(1) == eid
            continue
        if found:
            body.append(raw)
    names = re.compile(r"([A-Za-z0-9][\w.-]*\.(?:md|py|ts|tsx|json))")
    seen = []
    for raw in body:
        s = re.search(r"\*\*Attested-by:\*\*[^-]*-\s*(.*)", raw)
        if s:
            hit = names.findall(s.group(1))
            if hit:
                seen.append(hit[0])
    mine = names.findall(why or "")
    if mine:
        seen.append(mine[0])
    if not seen:
        return
    top = max(set(seen), key=seen.count)
    n = seen.count(top)
    if n < 3:
        return
    print()
    print("  [NOTE] %s has now been attested %d times naming %s." % (eid, n, top))
    print("         That is usually fine - some entries rest on a file that changes often.")
    print("         But it is also where a WRONG cause hides: o1's F7 carried five stamps")
    print("         repeating one false reason, and the real fix was a single missing line.")
    print("         Worth asking once: is the cause still the cause, or just the last answer?")


def write_restamp(doc, eid, why, who, stamp):
    """Record one attestation on `eid` BENEATH its plate line in `doc`. The write, alone.

    ⛔ IT USED TO APPEND TO THE PLATE LINE ITSELF, and that is the defect this rewrite
    removes: the line the human scans to see what needs him grew by a paragraph on every
    review. See the ONE STAMP PER LINE block above `split_stamps` for the measurement.

    Every prior justification is migrated, never dropped, and the operation is IDEMPOTENT
    in both directions - re-running it on an entry whose plate line has already grown
    SHORTENS that line, and re-running it on a stamped entry writes nothing at all.

    ⛔ SPLIT OUT SO A FIXTURE CAN REACH IT. `cmd_restamp` resolves the doc and applies
    ownership, and `resolve_doc_arg` correctly refuses a path outside the workspace - so a
    fixture driving the command got `[SKIP] refusing a doc outside the workspace`, and the
    suite printed PASSED around it. **A skipped fixture reporting green is the defect this
    file has spent the week removing.**

    ⭐ The guards belong in the wrapper; the behaviour worth testing is here.
    """
    lines = doc.read_text(encoding="utf-8").splitlines()
    entries, _ = parse_entries(lines)
    match = [e for e in entries if e["id"] == eid]
    if not match:
        print("[REFUSE] no entry with id %s in %s" % (eid, doc.name), file=sys.stderr)
        return 1
    if len(match) > 1:
        print("[REFUSE] id %s appears %d times - fix E-DUPID first" % (eid, len(match)),
              file=sys.stderr)
        return 1
    e = match[0]

    # ONE LINE, ONE STAMP. `--because` arrives from a shell and may carry newlines; a stamp
    # that wraps into a second physical line is a second line the plate has to hold, and
    # `reviewed_of` would read the wrap as continuation prose. Collapse it here, once.
    why = re.sub(r"\s+", " ", why).strip()

    end = e["line"] + len(e["body"].splitlines())
    si = None
    for i in range(e["line"] - 1, min(end, len(lines))):
        if "**Status:**" in lines[i]:
            si = i
            break
    if si is None:
        print("[REFUSE] %s has no **Status:** plate line to stamp" % eid, file=sys.stderr)
        return 1

    # The FIELD BLOCK: the run of bold plate fields directly under the Status line, which is
    # where `resolve` puts its attestation and where this one goes. Scanning only this run
    # leaves any stamp an author wrote down in the reasoning prose exactly where they put it.
    fi = si + 1
    others, prior = [], []
    while fi < min(end, len(lines)) and lines[fi].strip().startswith("**"):
        if STAMP_LINE_RE.match(lines[fi]):
            prior.extend(split_stamps(lines[fi])[1])
        else:
            others.append(lines[fi])
        fi += 1

    plate, inline = split_stamps(lines[si])

    # ⛔ THE PLATE'S OWN SHORT STAMP IS DERIVED, SO IT IS NEVER COLLECTED BACK. It carries
    # no actor and no reasoning - it is regenerated from the newest stamp below on every run -
    # and re-collecting it would append an identical bare line per invocation, which is the
    # unbounded growth this change exists to remove, moved one row down. A bare inline stamp is
    # only kept when it is the sole date the entry has, because then it is not a copy of
    # anything and dropping it would lose the only record of when the entry was last read.
    rich = [s for s in inline if s[1] or s[2]]
    bare = [s for s in inline if not (s[1] or s[2])]
    prior = rich + prior
    if not prior and bare:
        prior = bare[:1]

    # ⭐ PRESERVE EVERY JUSTIFICATION. A prior `--because` records why an entry survived a
    # change; it is evidence, not clutter. De-duplicated on the whole triple, so re-running the
    # same restamp cannot double an entry and two different readings on the same day both keep
    # their text.
    stamps, seen = [], set()
    already = any(s[0] == stamp and s[1].lower() == who.lower() for s in prior)
    for s in ([] if already else [(stamp, who, why)]) + prior:
        key = (s[0], s[1].lower(), s[2])
        if key in seen:
            continue
        seen.add(key)
        stamps.append(s)
    if not stamps:
        stamps = [(stamp, who, why)]
    # Newest first, and a stable sort keeps same-timestamp stamps in the order written.
    stamps.sort(key=lambda s: s[0], reverse=True)

    new_block = others + [render_stamp(*s) for s in stamps]
    new_status = "%s - **Reviewed:** %s" % (plate, stamps[0][0])
    if lines[si] == new_status and lines[si + 1:fi] == new_block:
        print("[ok] %s already carries a stamp at %s" % (eid, stamp))
        return 0

    lines[si] = new_status
    lines[si + 1:fi] = new_block
    doc.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")

    # VERIFY THE MUTATION, the way `resolve` does. A write that reports success from its own
    # say-so is how this file learned that "[RESOLVED] Q1 -> RESOLVED" can print three times
    # over a document that still reads OPEN. Re-parse from disk and read the stamp back.
    after, _ = parse_entries(doc.read_text(encoding="utf-8").splitlines())
    got = next((reviewed_of(x["body"]) for x in after if x["id"] == eid), (None, None))
    if got[0] != stamps[0][0]:
        print("[FAILED] %s reads %s after the write, not %s - the edit did NOT take."
              % (eid, got[0] or "no stamp", stamps[0][0]), file=sys.stderr)
        return 1
    print("[RESTAMPED] %s in %s" % (eid, doc.name))
    print("            %s at %s" % (who, stamp))
    if len(stamps) > 1:
        print("            plate line now %d chars; %d stamp(s) beneath it"
              % (len(new_status), len(stamps)))
    return 0


def cmd_resolve(args):
    """
    Flip an entry's Status IN PLACE. Never append a second heading.

    Appending a superseding heading is how one ID comes to carry two contradictory
    statuses (7 instances across the live docs today). Because lifecycle is a FIELD and
    not a location, resolving cannot strand an entry in the wrong section - there are no
    lifecycle sections to be stranded in. Every lifecycle view is generated.
    """
    doc = resolve_doc_arg(args.doc)
    if not doc or not doc.exists():
        print("no such doc: %s" % doc, file=sys.stderr)
        return 2
    if refuse_if_not_mine(doc, "resolve", getattr(args, "not_mine", False)):
        return 1
    lines = doc.read_text(encoding="utf-8").splitlines()
    entries, _ = parse_entries(lines)

    match = [e for e in entries if e["id"] == args.id]
    if not match:
        print("[REFUSE] no entry with id %s in %s" % (args.id, doc.name), file=sys.stderr)
        return 1
    if len(match) > 1:
        print("[REFUSE] id %s appears %d times - fix E-DUPID first" % (args.id, len(match)),
              file=sys.stderr)
        return 1
    e = match[0]

    today = _today()
    status = args.status.upper()
    changed = False
    end = e["line"] + len(e["body"].splitlines())
    for i in range(e["line"], min(end, len(lines))):
        m = STATUS_RE.search(lines[i])
        if not m:
            continue
        # Replace exactly the captured VALUE span. Building a second, parallel regex for
        # the write path is what broke this: it drifted from STATUS_RE, matched nothing,
        # and the code set changed=True anyway - so `resolve` printed "[RESOLVED] Q1 ->
        # RESOLVED" while the document still read OPEN. One pattern, one source of truth.
        lines[i] = lines[i][:m.start(1)] + status + lines[i][m.end(1):]
        lines[i] = re.sub(r"\*\*Enriched:\*\*\s*\w+", "**Enriched:** YES", lines[i])

        # CARRY THE HEADING MARKER WITH THE STATUS. The marker is DERIVED from the status,
        # so changing one without the other is the drift E-MARKERDRIFT exists to catch -
        # and `resolve` was creating it on every single call. The user then saw a failing
        # check immediately after a successful resolve, which is the cry-wolf failure:
        # the gate firing on the tool's own output teaches the reader that red is normal.
        want_mark = STATUS_MARKER.get(status)
        if want_mark:
            h = e["line"] - 1
            if 0 <= h < len(lines) and lines[h].lstrip().startswith("#"):
                bare = re.sub(r"^(#+)\s*(?:%s)?\s*" % "|".join(
                    re.escape(v) for v in set(STATUS_MARKER.values())),
                    r"\1 ", lines[h]).rstrip()
                hashes, _, rest = bare.partition(" ")
                lines[h] = "%s %s %s" % (hashes, want_mark, rest.strip())
        # IDEMPOTENT. the human spotted the bug in a screenshot: Q1 carried the SAME
        # "Resolved" line twice, because the first run silently failed to flip the
        # status (a drifted regex) but still inserted its line, and the re-run inserted
        # another. A mutation that is not idempotent turns every retry into corruption -
        # and retries are guaranteed, because the first attempt reported success falsely.
        note = "**Resolved %s:** %s" % (today, sanitize_field(args.ruling))
        end_i = e["line"] + len(e["body"].splitlines())
        existing = [j for j in range(e["line"], min(end_i, len(lines)))
                    if lines[j].startswith("**Resolved ")]
        if existing:
            lines[existing[0]] = note
            for j in reversed(existing[1:]):
                del lines[j]
        else:
            lines.insert(i + 1, "")
            lines.insert(i + 2, note)
        changed = True
        break
    if not changed:
        if not args.adopt:
            print("[REFUSE] entry %s has no Status field to flip.\n"
                  "         Expected a line like:  %s\n"
                  "         Re-run with --adopt to insert one, or use `migrate`"
                  " for the whole doc."
                  % (args.id, STATUS_CANONICAL), file=sys.stderr)
            return 1
        # --adopt: legacy entry, no field yet. Insert one rather than refusing - this is
        # the adoption path o7 found missing, where `resolve` was unreachable on exactly
        # the docs that needed it.
        field = ("**Status:** %s - **Owner:** the human - **Enriched:** YES" % status)
        lines.insert(e["line"], "")
        lines.insert(e["line"] + 1, field)
        lines.insert(e["line"] + 2, "")
        lines.insert(e["line"] + 3,
                     "**Resolved %s:** %s" % (today, sanitize_field(args.ruling)))
        changed = True

    # Write the declared edge, so a ruling can be told it has gone stale. E-NODEPS exists
    # because a decision resting on nothing can never be invalidated by anything - it just
    # sits there looking settled while its inputs move underneath it. Inserted next to the
    # Status field so it travels with the entry.
    # RESOLVING IS REVIEWING. E-STALEPROSE asks when an entry was last checked against the
    # facts it rests on, and a ruling made right now is exactly that check - but `resolve`
    # recorded no attestation, so every freshly-ruled decision was immediately reported as
    # "never reviewed". The user then saw a blocking finding on work they had just done
    # correctly, which is the cry-wolf failure again: the gate firing on the tool's own
    # correct output. The timestamp comes from the same generator as everywhere else, so
    # it cannot be a hand-typed guess.
    if changed:
        att = ("**Attested-by:** %s at %s - ruled in this session; the ruling text above "
               "states what was decided and the Depends edge states what it rests on"
               % (sanitize_field(os.environ.get("CLAUDE_ORCH_ID", "orchestrator"), 40),
                  _now_iso()))
        if not any(l.startswith("**Attested-by:**")
                   for l in lines[e["line"]:e["line"] + 8]):
            for k in range(e["line"], min(len(lines), e["line"] + 8)):
                if lines[k].startswith("**Status:**"):
                    lines.insert(k + 1, att)
                    break

    if changed and args.depends:
        dep_line = "**Depends:** %s" % sanitize_field(args.depends, 200)
        already = any(l.startswith("**Depends:**")
                      for l in lines[e["line"]:e["line"] + 6])
        if not already:
            for k in range(e["line"], min(len(lines), e["line"] + 6)):
                if lines[k].startswith("**Status:**"):
                    lines.insert(k + 1, dep_line)
                    break

    write_doc(doc, "\n".join(lines) + "\n")

    # VERIFY THE MUTATION. Never report success from the command's own say-so.
    #
    # The previous version printed "[RESOLVED] Q1 -> RESOLVED" three times while the
    # document still read OPEN, because a silently-failed regex still set changed=True.
    # That is o3's finding exactly: `git push origin main` prints a success-shaped line
    # when nothing landed, and the only trustworthy check is reading back the state.
    # So: re-parse from disk and confirm, or refuse loudly.
    after, _ = parse_entries(doc.read_text(encoding="utf-8").splitlines())
    got = next((status_of(x["body"]) for x in after if x["id"] == args.id), None)
    if got != status:
        print("[FAILED] %s still reads %s in %s after the write - the edit did NOT take."
              % (args.id, got or "no status", doc.name), file=sys.stderr)
        print("         Nothing to trust here; inspect the entry by hand.",
              file=sys.stderr)
        return 1

    print("[RESOLVED] %s -> %s  in %s   (verified by re-reading the file)"
          % (args.id, status, doc.name))
    print("           edited in place; no second heading created")
    print()
    print("NEXT: run `orchdoc.py plate --doc %s` so every derived view updates." % args.doc)
    return 0


# The emergent status vocabulary, observed across all seven docs. Migration READS this
# and writes an explicit field - it never removes the marker. The emoji stays for humans;
# the field is what a machine can check.
EMOJI_STATUS = [
    ("✅", "RESOLVED"), ("⭐✅", "RESOLVED"), ("🔴", "OPEN"), ("⏸️", "PAUSED"),
    ("⏳", "DEFERRED"), ("🗄️", "ARCHIVED"), ("⛔", "OPEN"), ("🚨", "OPEN"),
]
WORD_STATUS = [
    (re.compile(r"\bRESOLVED\b|\bRULED\b|\bDONE\b|\bAPPLIED\b"), "RESOLVED"),
    (re.compile(r"\bSUPERSEDED\b"), "SUPERSEDED"),
    (re.compile(r"\bPAUSED\b"), "PAUSED"),
    (re.compile(r"\bDEFERRED\b|\bPARKED\b"), "DEFERRED"),
    (re.compile(r"\bSTOPPED\b|\bBLOCKED\b"), "OPEN"),
]


def infer_status(title):
    """Best-guess status from an entry's existing heading. Never destructive."""
    for emo, st in EMOJI_STATUS:
        if emo in title:
            return st
    for pat, st in WORD_STATUS:
        if pat.search(title):
            return st
    return "OPEN"


def cmd_migrate(args):
    """
    Bring a LEGACY doc to the point where the other commands work.

    o7 found the adoption hole: `resolve` refuses while a duplicate id exists, but
    `resolve` is the path OFF the duplicate pattern - so on any doc that already has the
    defect, the tool is unreachable. Then it refuses again on a missing Status. Adoption
    was a three-stage hand migration before the tool did anything, and EVERY legacy doc
    has both conditions.

    So this does the mechanical stage in one motion, and DRY-RUN IS THE DEFAULT (the
    shape release.mjs uses): it adds the missing Status field, inferring the value from
    the heading's existing emoji vocabulary, and never deletes anything.

    It deliberately does NOT auto-resolve duplicate ids. Which of two entries is live is
    a judgment about content, and o9 does not make those in another orchestrator's doc.
    Duplicates are reported with the exact command to run.
    """
    doc = resolve_doc_arg(args.doc)
    if not doc or not doc.exists():
        print("no such doc: %s" % doc, file=sys.stderr)
        return 2

    with _lock(doc):
        lines = doc.read_text(encoding="utf-8").splitlines()
        entries, _ = parse_entries(lines)

        dup = defaultdict(list)
        for e in entries:
            dup[e["id"]].append(e)
        dups = {k: v for k, v in dup.items() if len(v) > 1}

        # Insert a Status line right under each entry heading that lacks one. Walk
        # bottom-up so earlier line numbers stay valid as we insert.
        # SCOPE = exactly what the gate demands, never more. E-NOSTATUS only fires on
        # decision entries, so migrate only stamps those. The first run also stamped
        # FINDINGS as OPEN, which would have pushed them onto the generated plate as
        # items needing the human - a migration tool that writes beyond the gate's scope is
        # editing another orchestrator's doc on its own initiative.
        todo = [e for e in entries
                if status_of(e["body"]) is None
                and not e.get("archived")
                and (DECISION_SECTION_RE.search(e["section"])
                     or DECISION_SECTION_RE.search(e["title"]))]
        todo.sort(key=lambda e: e["line"], reverse=True)

        planned = []
        for e in todo:
            st = infer_status(e["title"])
            field = ("**Status:** %s - **Owner:** %s - **Opened:** %s - **Enriched:** NO"
                     % (st, args.owner, args.date or "unknown"))
            planned.append((e["line"], e["id"], st, field))
            if not args.dry_run:
                lines.insert(e["line"], "")
                lines.insert(e["line"] + 1, field)

        if not args.dry_run and planned:
            write_doc(doc, "\n".join(lines) + "\n")

    mode = "DRY RUN (nothing written)" if args.dry_run else "APPLIED"
    print("orchdoc migrate - %s - %s" % (doc.name, mode))
    print()
    if planned:
        print("  Status field added to %d entries (inferred from the existing markers):"
              % len(planned))
        for ln, eid, st, _f in sorted(planned):
            print("    %-10s -> %-10s (was marker-only, at line %d)" % (eid, st, ln))
    else:
        print("  No entries missing a Status field.")

    if dups:
        print()
        print("  ⛔ NOT touched - %d duplicate id(s). Which entry is live is a judgment"
              % len(dups))
        print("     about content, so this tool does not make it in another's doc:")
        for eid, group in sorted(dups.items()):
            print("       %-10s appears at lines %s"
                  % (eid, ", ".join(str(g["line"]) for g in group)))
        print("     Collapse the superseded copy under <details> with a summary saying")
        print("     WHY it is superseded and WHERE the live entry is, then strip its id.")

    if args.dry_run:
        print()
        print("  Re-run with --commit to write. Nothing has been changed.")
    return 0


def refresh_meta(doc, landing_now=False):
    """Re-render the meta block from the commit log. True if anything changed.

    ONE implementation, three callers: `scaffold` (creates it), `commit` (the write
    chokepoint, so it cannot drift) and `refresh-meta` (what an audit runs). Three
    hand-rolled refreshes would be three chances to disagree about what "current" means.
    """
    lines = doc.read_text(encoding="utf-8", errors="replace").split("\n")
    span, err = marker_span(lines, META_BEGIN_TOKEN, META_END_TOKEN, "meta")
    if err or not span:
        return False
    new = lines[:span[0]] + render_meta(doc, lines, landing_now) + lines[span[1] + 1:]
    if new == lines:
        return False
    write_doc(doc, "\n".join(new))
    return True


def cmd_refresh_meta(args):
    """Deterministic date repair, for the audit path the human asked for.

    The audit is a session CHOOSING to look; `commit` is the action that lands a change.
    Both refresh now, but only the second one holds for docs nobody audits - which is why
    the chokepoint is the primary and this is the convenience.
    """
    doc = resolve_doc_arg(args.doc)
    if doc is None:
        return 2
    changed = refresh_meta(doc)
    print("orchdoc refresh-meta - %s" % doc.name)
    print("  %s" % ("meta block refreshed from the commit log."
                    if changed else "already current - nothing written."))
    return 0


def cmd_commit(args):
    """
    Land ONE OrchDoc on the canonical ref, safely, from a dirty shared working tree.

    "OrchDoc edits go straight to main" is the right RULE and it collides with reality:
    every orchestrator shares ONE working tree, on a non-main branch, with a dozen
    half-finished files from five sessions in it. o9 told three orchestrators to run
    `git push origin HEAD:main` from that state. Measured: HEAD was 21 ahead and 36
    BEHIND origin/main, so that push is rejected - or CLOBBERS 36 commits if anyone
    force-resolves it. o6 caught it before o1 or o5 acted.

    o9 had been using this plumbing all day precisely to avoid that, and still handed
    out the unsafe one-liner. o6's phrasing: a contract the author cannot see, except
    this one clobbers 36 commits instead of failing a lint. **A rule without its safe
    mechanism attached is an instruction that corrupts main on contact.**

    The recipe, o6's, with both gates enforced rather than described:
      1. ISOLATION - the doc must be identical on origin/main and HEAD, so committing
         the worktree copy cannot revert anything landed while this branch sat behind.
      2. build "origin/main + this one file" via plumbing. No checkout, so the dirty
         tree is untouched and Windows MAX_PATH never enters into it.
      3. SAFETY - the built commit must differ from origin/main in EXACTLY this file.
      4. fast-forward push. Rejects harmlessly if main moved; can never clobber.
      5. VERIFY by re-reading the remote, never by trusting the push's own output (o3).
    """
    doc = resolve_doc_arg(args.doc)
    if not doc or not doc.exists():
        print("no such doc: %s" % doc, file=sys.stderr)
        return 2
    rel = os.path.relpath(str(doc), str(PROJECTS)).replace("\\", "/")
    ref = CANONICAL_REF

    print("orchdoc commit - %s -> %s" % (doc.name, ref))

    # ⛔ REFRESH THE META BLOCK HERE, AT THE WRITE CHOKEPOINT. `render_meta` used to be
    # called by `scaffold` ONLY, so "Last updated" was stamped once and never again - while
    # the field itself says "_(from the commit log, never hand-written)_". On 2026-08-13 the human
    # noticed o9's read 07-Aug: six days stale, on a doc edited that same day.
    #
    # ⭐ That is worse than an absent field. The label asserts the value is DERIVED and
    # current, so a reader trusts it instead of checking - the stale-description defect
    # (`memory/feedback_stale_descriptions_outrank_sources.md`) committed by the tool that
    # prints the description.
    #
    # the human proposed hanging it off the full audit. Deliberately doing it here instead: an
    # audit is something a session chooses to run, and this must hold for docs nobody
    # audits. `commit` is the ONE action that lands a change, so refreshing here means the
    # stamp cannot drift from the thing it describes. `check` reports drift for docs edited
    # by hand outside the tool - detect everywhere, write at the chokepoint.
    if refresh_meta(doc, landing_now=True):
        print("           meta block refreshed from the commit log before landing.")

    # ⛔ RELOCATION IS FORCED HERE, because nothing forced it before (the human, 2026-08-13:
    # *"what forces the relocation of the item to §99?"*). The honest answer was: nothing.
    # `E-DONEINACTIVE` DETECTED a finished entry sitting in a live section, `archive` could
    # PERFORM the move, and `commit`'s gate 0 is advisory - so a done entry landed in §2 or
    # §3 with a warning nobody had to act on. Detection plus a manual verb is the shape that
    # has failed in this workspace repeatedly; W-STRIKEDONE fired 14 times a run for weeks
    # and changed nothing, because seeing the problem was never the missing part.
    #
    # ⭐ the human's rule is absolute - "Done item with all done sub-items - moved to §99
    # completely. NEVER left in live sections" - so it is applied by the action that lands
    # the doc, not by a session remembering. An entry that is finished cannot reach main
    # still sitting in a live section.
    # ⛔ THE NAMESPACE MUST CARRY EVERY FIELD cmd_archive READS, or the forced relocation
    # never happens. It was built with `commit=True` - which cmd_archive does not read, it
    # reads `dry_run` - and without `into`, so every call died on
    # `'Namespace' object has no attribute 'into'`, was swallowed by the except below, and
    # printed a one-line [note] nobody reads.
    #
    # ⭐ So the rule the human asked to be FORCED at the landing chokepoint had not run once. The
    # detection was correct, the mover was correct, and the wire between them was broken -
    # the same shape as the gate 0 flip below, and as reorder-vs-check: the check existed,
    # the fix existed, and nothing connected them at the moment it mattered.
    try:
        _ns = argparse.Namespace(doc=args.doc, dry_run=False,
                                 into="RESOLVED - kept for the record",
                                 not_mine=getattr(args, "not_mine", False))
        cmd_archive(_ns)
    except Exception as _e:                    # never let tidy-up block a landing
        print("           [note] auto-archive skipped: %s" % _e)

    # ⚠️ SAY WHAT IS ACTUALLY BEING LANDED. This commits the WORKING-TREE copy, not HEAD -
    # deliberately, because the whole point is that unlanded edits reach the human. But
    # o2 pointed out the other edge: an orchestrator holding HALF-FINISHED thoughts in the
    # tree will ship them, and nothing said so. A tool whose behaviour is correct but
    # unstated is one an author can be surprised by, and surprise is how a partial thought
    # becomes the record.
    print("           landing your WORKING-TREE copy (%d lines), not HEAD - make sure it"
          % len(doc.read_text(encoding="utf-8", errors="replace").splitlines()))
    print("           is coherent; half-finished edits in the file WILL be landed.")
    print()
    git(["fetch", "--quiet", "origin"])

    # ⛔ GATE 0 REFUSES. A finding in BLOCKING stops the landing.
    #
    # It was advisory for one reason: o1's doc carried pre-existing findings plus ~174 lines
    # that existed nowhere but one disk, and a gate that refuses on inherited debt gets
    # switched off. That reasoning was about a MOMENT - a specific doc, on a specific day,
    # with content at risk - and it was written into the tool as a PERMANENT rule.
    #
    # ⛔ the human, 2026-08-17: *"Why not fix the inherited problem? If it allows the rule to be
    # ignored, doesn't that entirely defeat the entire definition of deterministic???"* He is
    # right, and he also rejected the obvious hedge - blocking only NEW findings. **A rule
    # enforced on new violations and not old ones is not deterministic; it means a doc can
    # carry a violation forever.** The debt was cleared first (o9L20, 2026-08-18) and then
    # this was flipped, in that order, because either one alone fails.
    #
    # ⭐ A GATE THAT CANNOT REFUSE IS DOCUMENTATION. 37 blocking codes were unenforced at the
    # one moment they apply - the moment a doc is written - so every one of them was a
    # description of a rule rather than a rule.
    #
    # The escape is REAL and it is RECORDED: `--override CODE[,CODE...] --because <reason>`,
    # held to the attestation bar, stamped into the doc, and surfaced by every later check.
    # Content at genuine risk can still be landed; it just cannot be landed silently.
    findings = [f for f in check_doc(doc) if f.code in BLOCKING]

    # ⛔ PARSE THE OVERRIDE LIST ONCE, HERE, and let every gate read the SAME value. Gate 1
    # tested `args.override in GATE_OVERRIDE_CODES` against the raw string, so the moment
    # this flag accepted a comma-separated list, `--override E-STALEPROSE,GATE1-REWORD`
    # satisfied gate 0 and silently did nothing for gate 1 - the override printed as
    # recorded and the gate refused anyway. That is precisely the defect the comment inside
    # the block below describes ("the VALIDATOR and the CONSUMER disagreed about what an
    # override code IS"), reintroduced by widening the flag without following it into every
    # reader. One fact, one parse.
    override_codes = [c.strip().upper() for c in (args.override or "").split(",") if c.strip()]

    # o8's guard 2: a legitimate, RECORDED escape hatch, so nobody learns the silent one.
    if args.override:
        if not args.because or len(args.because) < MIN_ATTESTATION_CHARS \
                or RUBBER_STAMP_RE.match(args.because):
            print("  [REFUSE] --override needs --because with a real reason (%d+ chars)."
                  % MIN_ATTESTATION_CHARS, file=sys.stderr)
            print("           An override held to a lower bar than an attestation "
                  "becomes 'needed to ship'.", file=sys.stderr)
            return 1
        # ⛔ REFUSE A CODE THAT MATCHES NOTHING, BEFORE WRITING THE STAMP. The refusal message
        # prints "gate1"; the code is "GATE1-REWORD". o10 passed the label they were shown, the
        # stamp was written, the filter below matched no finding, and the gate refused anyway -
        # leaving a permanent W-OVERRIDE in their doc that overrides nothing and that they
        # cannot remove without another override.
        #
        # ⭐ An override that records itself while doing nothing is worse than an error: it looks
        # like a decision was made. Validate first; a mistyped code should cost a retry, never a
        # permanent false artifact in someone's document.
        # ⛔ LINT CODES **AND** GATE TOKENS. The first version of this validated against lint
        # findings only - and `GATE1-REWORD` is not a lint finding, it is a gate-1 reconciliation
        # token consumed further down. So it could never appear in `_present` and the guard
        # rejected it UNCONDITIONALLY, making the documented fix for a reworded line unreachable
        # for every orchestrator. o10 was blocked mid-rename within nine minutes of it shipping.
        #
        # ⭐ The error message was the tell, exactly inverted: *"matches no finding on this doc -
        # Codes actually present: (none - the doc is clean)"*. **A clean doc is PRECISELY when
        # GATE1-REWORD is needed** - gate 1 fires on removed LINES and has nothing to do with
        # lint state. A dirty-doc fixture would have passed either way, which is how it shipped.
        #
        # Same family as the defect o10 reported an hour earlier, from the other side: the
        # VALIDATOR and the CONSUMER disagreed about what an override code IS, each reading its
        # own vocabulary, neither aware of the other. One fact, two readers.
        # ⛔ ONE --because COVERS EVERY CODE LISTED, so an emergency landing is one command.
        # With gate 0 refusing, a doc carrying three blocking codes would otherwise need three
        # sequential overrides - and a gate whose escape is that awkward gets routed around
        # rather than used, which is how the silent path gets learned.
        _asked = override_codes
        _present = sorted(set(f.code for f in findings) | GATE_OVERRIDE_CODES)
        _unknown = [c for c in _asked if c not in _present]
        if _unknown:
            print("  REFUSED - `--override %s` matches no finding on this doc, so it would "
                  "record an attestation that overrides nothing." % ",".join(_unknown),
                  file=sys.stderr)
            print("  Codes actually present: %s"
                  % (", ".join(_present) if _present else "(none - the doc is clean)"),
                  file=sys.stderr)
            print("  Use the CODE, not the label a message printed. Nothing was written.",
                  file=sys.stderr)
            return 1
        # ⛔ A DRY RUN MUST NOT WRITE. This wrote the attestation stamp into the document
        # before the dry-run bail, so rehearsing a landing left a permanent override in
        # someone's doc for a push that never happened - and the stamp cannot be removed
        # without another override. Found by o9L20 on 2026-08-18 by rehearsing o7's landing.
        who = actor_for(doc)
        for _code in _asked:
            stamp = "<!-- ORCHDOC:OVERRIDE %s by=%s at=%s --> %s" % (
                _code, who, _now_iso(), sanitize_field(args.because, 600))
            if args.dry_run:
                print("  [override] %s would be recorded by %s (DRY RUN - not written)."
                      % (_code, who))
                continue
            txt = doc.read_text(encoding="utf-8")
            if stamp.split("-->")[0] not in txt:
                write_doc(doc, txt.rstrip("\n") + "\n\n" + stamp + "\n")
            print("  [override] %s recorded by %s - it will surface in every later check."
                  % (_code, who))
        findings = [f for f in findings if f.code not in _asked]

    if findings:
        # ⛔ REFUSE. There is no landing-anyway path any more - see the gate 0 note above.
        print("  [REFUSE] gate 0 - %d blocking finding(s). Nothing was pushed."
              % len(findings))
        for f in findings[:8]:
            print("           %-18s %s %s"
                  % (f.code, ("L%d" % f.line) if f.line else "doc", f.msg[:78]))
        if len(findings) > 8:
            print("           ... and %d more - orchdoc.py check --doc %s"
                  % (len(findings) - 8, args.doc))
        print()
        print("  Fix them, or land anyway WITH A WRITTEN REASON:")
        print("    orchdoc.py commit --doc %s --commit \\" % args.doc)
        print("      --override %s \\"
              % ",".join(sorted(set(f.code for f in findings))))
        print("      --because \"<why this must land before it is clean, %d+ chars>\""
              % MIN_ATTESTATION_CHARS)
        return 1
    print("  [ok] gate 0 - no blocking findings")

    # Gate 1: isolation. Compare the MERGE-BASE to the canonical ref, not HEAD to it.
    #
    # o1 caught this. The first version diffed origin/main against HEAD - but once the
    # author has committed their own work, HEAD contains their change BY CONSTRUCTION,
    # so that gate is never empty and blocks every safe operation. o1's run reported
    # "174 insertions", a failure that was entirely its own edit.
    #
    # Worse than blocking: a session reading the non-empty output as "that's just my own
    # change, fine" waves it through, which is the same gate with no gate at all.
    #
    # The question that actually matters is: did anyone ELSE touch this file while I was
    # behind? That is merge-base vs canonical.
    # Ask the question that actually matters - WOULD LANDING THIS LOSE ANYTHING? - rather
    # than the proxy "has the file changed", which is true for benign reasons constantly.
    #
    # o1 caught version 1 (origin/main vs HEAD): once you commit your own work, HEAD
    # contains it by construction, so the gate never passes. Version 2 (merge-base vs
    # canonical) had the same shape one step out: after your OWN first landing, main has
    # commits touching the file, so it blocked its own author forever.
    #
    # The content test has no such blind spot: every non-empty line on the canonical copy
    # must still be present in the copy about to be landed. If so, landing is additive
    # and can revert nobody, whoever wrote what. If not, it names the exact lines at risk.
    _, base, _ = git(["merge-base", ref, "HEAD"])
    def _authored(text, label):
        """
        Non-empty lines EXCLUDING the derived region, which changes by construction and
        would otherwise report as content loss. Returns None if the boundary cannot be
        located - an oracle that cannot find its own boundary must refuse, not guess.
        """
        lines_ = text.splitlines()
        spans, why = derived_spans(lines_)
        if why:
            print("  [REFUSE] gate 1 - %s (%s)." % (why, label), file=sys.stderr)
            print("           Cannot tell derived content from hand-authored, so this",
                  file=sys.stderr)
            print("           gate cannot prove anything. Fix the markers first.",
                  file=sys.stderr)
            return None
        out = []
        for i, l in enumerate(lines_):
            if not l.strip():
                continue
            if any(lo <= i <= hi for lo, hi in spans):
                continue          # ANY generated region - see DERIVED_REGIONS
            if MACHINE_FIELD_RE.search(l):
                # A field line: keep only what the author wrote around the fields, so a
                # status rewrite is invisible but an attestation is still protected.
                rest = strip_machine_fields(l)
                if rest:
                    out.append(rest)
                continue
            m = re.match(r"^(#{1,6})\s+(.*)$", l)
            if m:
                # Compare a heading by its ID, not its wording (o7). Every OrchDoc
                # correction rewords headings - four of the six lines gate 1 flagged
                # against o7 were changes o9's OWN LINTER had demanded. An entry whose
                # id survives ANYWHERE, including inside a <details> block, is not lost.
                # Losing the id entirely is still refused, which is the actual harm.
                bare = _strip_markers(m.group(2))
                idm = ID_RE.match(bare)
                if not idm:
                    # An ARCHIVED heading carries its id mid-line, not at the start.
                    # Without this the archiver and this gate disagree about the same
                    # entry and every archiving commit needs an override.
                    idm = ARCHIVED_ID_RE.search(bare)
                out.append("ENTRY:%s" % idm.group(1) if idm
                           else "%s %s" % (m.group(1), bare))
            else:
                out.append(l)
        return out

    _, canon_txt, _ = git(["show", "%s:%s" % (ref, rel)])
    canon_lines = _authored(canon_txt, "on %s" % ref)
    if canon_lines is None:
        return 1
    try:
        mine_l = _authored(doc.read_text(encoding="utf-8"), "in the working tree")
    except (OSError, UnicodeDecodeError) as e:
        print("  [REFUSE] cannot read %s: %s" % (doc.name, e), file=sys.stderr)
        return 1
    if mine_l is None:
        return 1
    mine = set(mine_l)
    # ⛔ A LINE THAT GREW IS NOT A LINE THAT LEFT. The comparison is line-identity, so appending
    # to a plate line - which is what re-attesting an entry DOES, since a second review field
    # would be a second source of truth - reads as the old line being deleted. Every word of it
    # is still on the page, one line longer.
    #
    # ⭐ This is a granularity error, not a leniency one: the unit being compared is the LINE
    # while the thing being protected is the CONTENT. Containment is strictly more accurate and
    # weakens nothing - a REWORDED copy still fails, because containment demands the old text
    # verbatim. It only stops the gate refusing edits that added something.
    #
    # It cost a real refusal: 17 entries re-attested under the human's "fix the inherited problem"
    # ruling could not land, and the offered remedy was the override that ruling was about.
    _joined = "\n".join(mine_l)
    lost = [l for l in canon_lines if l not in mine and l.strip() not in _joined]
    if lost and (set(override_codes) & GATE_OVERRIDE_CODES):
        # o8's guard 2, applied to gate 1: a RECORDED reconciliation beats a bypass that
        # leaves no trace. The --because reason is already held to the attestation bar.
        print("  [override] gate 1 reconciliation recorded by %s - %d reworded line(s)"
              % (actor_for(doc), len(lost)))
        for l in lost[:6]:
            print("             was: %s" % l.strip()[:88])
        lost = []
    # ⛔ SPLIT THE LOST LINES BY WHETHER THE AUTHOR EVER SAW THEM. Gate 1's own message names
    # the risk - "someone else's content, or a stale copy of yours" - and both are about
    # content the author NEVER HAD. Removing a line you wrote and are rewriting is authorship.
    #
    # Measured 2026-09-03: 173 GATE1-REWORD overrides across the live docs (o7 49, o8 37, o9
    # 27). o10's line applies - an override used that often is not an override, it is a
    # silenced check - and the cause is that ordinary editing removes lines, so the gate fired
    # on correct work until the escape became the road.
    #
    # ⭐ "Who wrote it" is unavailable: every session commits as the same git author, so blame
    # cannot separate them. The answerable question is WHEN. A line that entered the canonical
    # ref AFTER this working copy was last written is one the author never had.
    seen, unseen = lost, []
    if lost:
        seen, unseen = _split_by_seen(doc, rel, ref, lost)
    if unseen:
        print("  [REFUSE] gate 1 - landing this would remove %d line(s) that reached %s AFTER"
              % (len(unseen), ref))
        print("           your working copy was last written. You never had them, so this is")
        print("           a stale copy or someone else's work - not a rewrite. First:")
        for l in unseen[:5]:
            print("             - %s" % l.strip()[:88])
        print("           Reconcile:  git diff %s -- %s" % (ref, rel))
        print("           Then re-read the file and redo the edit on top of it.")
        return 1
    if seen:
        print("  [ok] gate 1 - %d line(s) removed, all present before your copy was written."
              % len(seen))
        print("       You had them and chose; that is editing, not loss. First:")
        for l in seen[:3]:
            print("             - %s" % l.strip()[:88])
    lost = unseen
    if lost:
        print("  [REFUSE] gate 1 - landing this would REMOVE %d line(s) that are on %s."
              % (len(lost), ref))
        print("           Someone else's content, or a stale copy of yours. First:")
        for l in lost[:5]:
            print("             - %s" % l.strip()[:88])
        print("           Reconcile:  git diff %s -- %s" % (ref, rel))
        print()
        # ⛔ NAME THE HATCH, AND NAME IT FROM THE SAME SET THE VALIDATOR READS. o7: *"a hatch
        # that exists in the message and not in the validator costs more than no hatch."* They
        # hit the version where it did not - the refusal named an override the validator
        # rejected, so the documented escape looked available and was not. Then the pre-commit
        # hook correctly refused the only other door (an OrchDoc may only land on `main`), and
        # an orchestrator that needed to remove ONE line had no path at all.
        #
        # ⭐ AND THE FIRST SUGGESTION IS RELOCATION, NOT THE OVERRIDE. o7 found that gate 1
        # objects to lines leaving the DOCUMENT, not the plate - so making "moved, not deleted"
        # literally true (byte-identical text in a finding) passes with no override. Two of
        # their attempts failed first and both were instructive: a `>` blockquote is a NEW line,
        # and dropping a redundant trailing line is still a removal. **The gate did not block
        # the work, it blocked the paraphrase they had described as a move.**
        print("           ⭐ FIRST TRY RELOCATING, not overriding. This gate objects to lines")
        print("           leaving the DOCUMENT, not the section - so moving text VERBATIM into")
        print("           a finding passes with no override. A reworded copy does not: that is")
        print("           the gate holding you to the move you said you made.")
        print()
        print("           Genuinely a rewrite? --override %s --because \"<why>\""
              % sorted(GATE_OVERRIDE_CODES)[0])
        return 1
    print("  [ok] gate 1 - every line on %s survives; landing is additive and can"
          % ref)
    print("       revert nobody%s"
          % ("" if not base else " (merge-base %s)" % base[:8]))

    # Which files are we landing? o5 landed FOUR (its OrchDoc plus three deliverables)
    # and the single-file version could not express that.
    wanted = [rel]
    for extra in (args.also or []):
        ep = Path(extra) if Path(extra).exists() else (PROJECTS / extra)
        if not ep.exists():
            print("  [REFUSE] --also path does not exist: %s" % extra, file=sys.stderr)
            return 1
        wanted.append(os.path.relpath(str(ep), str(PROJECTS)).replace("\\", "/"))
    wanted = sorted(set(wanted))

    def build_on(parent):
        """Build a commit = parent + exactly `wanted`, without touching the tree."""
        # tempfile.gettempdir(), not $TEMP. $TEMP is Windows-only; on macOS/Linux it is
        # usually unset, so the "." fallback silently wrote a scratch git index into the
        # CURRENT DIRECTORY - no crash, just litter dropped in the user's own repo.
        idx = Path(tempfile.gettempdir()) / ("orchdoc-%s.index" % os.getpid())
        env = dict(os.environ, GIT_INDEX_FILE=str(idx))

        def g(a):
            p = subprocess.run(["git", "-C", str(PROJECTS)] + a, capture_output=True,
                               text=True, env=env, timeout=60)
            return p.returncode, p.stdout.strip(), p.stderr.strip()
        try:
            if idx.exists():
                idx.unlink()
            g(["read-tree", parent])
            for w in wanted:
                _, blob, _ = g(["hash-object", "-w", str(PROJECTS / w)])
                g(["update-index", "--add", "--cacheinfo", "100644,%s,%s" % (blob, w)])
            _, tree, _ = g(["write-tree"])
            msg = args.message or ("%s: update" % doc.name)
            p = subprocess.run(
                ["git", "-C", str(PROJECTS), "commit-tree", tree, "-p", parent],
                # Attribute the model ACTUALLY running, not a hardcoded one. A published
                # tool stamping every commit "Claude Opus 5" is factually wrong for most
                # runs of it, and the trailer is the permanent record.
                input=msg + ("\n\nCo-Authored-By: %s <noreply@anthropic.com>\n"
                             % sanitize_field(os.environ.get("CLAUDE_MODEL_NAME", "Claude"), 40)),
                capture_output=True, text=True, timeout=60)
            return p.stdout.strip()
        finally:
            try:
                idx.unlink()
            except (OSError, UnicodeDecodeError):
                pass

    commit = build_on(ref)
    if not commit:
        print("  [REFUSE] could not build the commit", file=sys.stderr)
        return 1

    # Gate 2: the built commit must touch EXACTLY the files we named, and nothing else.
    _, names, _ = git(["diff", "--name-only", ref, commit])
    touched = sorted(n for n in names.splitlines() if n.strip())
    # SUBSET, not equality. The property is "nothing I did not name gets swept in".
    # Requiring equality refused a commit merely because a named file happened to be
    # unchanged this round, which is normal and harmless.
    extra = [n for n in touched if n not in wanted]
    if extra:
        print("  [REFUSE] gate 2 - the commit would touch %d file(s) you did not name:"
              % len(extra))
        for n in extra[:10]:
            print("           %s   <- NOT YOURS" % n)
        return 1
    if not touched:
        print("  [REFUSE] gate 2 - nothing to land: every named file already matches %s."
              % ref)
        return 1
    unchanged = [n for n in wanted if n not in touched]
    print("  [ok] gate 2 - commit touches %d file(s), all named%s"
          % (len(touched),
             "; %d named file(s) unchanged" % len(unchanged) if unchanged else ""))

    # --- GATE 4: EFFICACY. Did the write do what it was for? ---
    #
    # Gates 1-3 are SAFETY, and every one of them passes on a NO-OP. A no-op destroys
    # nothing, sweeps in nothing, and lands fine. So they cannot detect that the thing
    # the commit was FOR never happened - which is exactly what occurred when a patch
    # script failed on a stale anchor and `commit` ran anyway, landing the tool without
    # the reasoning it was meant to record.
    doc_text = doc.read_text(encoding="utf-8")
    subject = (args.message or "").splitlines()[0] if args.message else ""
    intents = []

    # Ids named in the SUBJECT are an assertion that those entries are in the doc.
    # Body text is excluded: bodies legitimately discuss other docs' ids ("o7's D16").
    # ONLY the known entry prefixes. A bare [A-Z]{1,3}\d{1,3} matches far more than
    # entry ids: it read "two-H1 title" as an entry called H1 and refused a landing whose
    # content was entirely present. That is the cry-wolf failure - gate 4 exists to catch
    # a write that silently did not happen, and a gate that also refuses correct writes
    # gets bypassed by reflex, which disarms it for the case it was built for.
    #
    # The prefix set is DERIVED from KIND_SECTION_NUM rather than restated here, so a new
    # entry kind cannot become invisible to this gate by someone forgetting a second list.
    _pfx = "|".join(sorted(KIND_SECTION_NUM, key=len, reverse=True))
    # A QUALIFIED id belongs to another document by construction and must not be demanded
    # here. o8's commit said "apply the human's D5 ruling" and this gate refused, because D5 is
    # an o9 entry - so the reference to the decision had to be removed from the commit that
    # implements it. Same qualifier blindness as the deleted ghost scan, but in the WRITE
    # path, where it blocks rather than merely adds noise.
    _subject = re.sub(r"\bo\d+:[A-Za-z-]*\d+", " ", subject)
    for m in re.finditer(r"\b((?:%s)\d{1,3})\b" % _pfx, _subject):
        intents.append(("entry " + m.group(1),
                        re.search(r"^#{1,6}\s.*\b%s\b" % m.group(1), doc_text,
                                  re.MULTILINE) is not None))
    for exp in (args.expect or []):
        intents.append(("%r" % exp[:48], exp in doc_text))

    unmet = [name for name, ok in intents if not ok]
    if unmet:
        print("  [REFUSE] gate 4 - the commit says it does something the doc does not"
              " show:")
        for name in unmet:
            print("           %s is named in the message but is NOT in %s"
                  % (name, doc.name))
        if any(n.startswith("entry ") for n in unmet):
            print()
            print("           If you meant ANOTHER orchestrator's entry, QUALIFY it -"
                  " `o9:D5`, not `D5`.")
           # The gate is right to refuse an unresolvable reference and was useless for
           # describing the problem without the remedy: the only obvious escape was to
           # DELETE the reference, which loses the traceability the qualifier exists for.
            print("           A qualified id is skipped here by construction, and it is"
                  " what keeps the")
            print("           cross-doc trail readable. Deleting the reference is the one"
                  " fix that costs something.")
        print()
        print("           Gates 1-3 are SAFETY and all pass on a no-op. This one asks")
        print("           whether the write actually happened. It did not.")
        return 1
    if intents:
        print("  [ok] gate 4 - all %d intent(s) named in the message are present"
              % len(intents))

    if args.dry_run:
        print()
        print("  DRY RUN. Built %s on top of %s, pushed nothing." % (commit[:10], ref))
        print("  Re-run with --commit to fast-forward push it.")
        return 0

    # The credential helper is NOT optional here. o1 hit this on step 4:
    #   bash: line 1: /dev/tty: No such device or address
    #   fatal: could not read Username for 'https://github.com'
    # `credential.helper=manager` wants a tty that headless and tool contexts do not
    # have. Routing through `gh auth git-credential` worked first try.
    #
    # ⚠️ o1's warning about diagnosing this: it LOOKS intermittent. o1's earlier
    # `git push -u origin <branch>` succeeded minutes before the failure, so a tool that
    # probes auth once and caches the verdict draws the wrong conclusion. Always pass it.
    # REBUILD-ON-RACE. o5 watched main advance THREE times during one landing, because
    # o1 and o9 were both pushing. A single attempt returns a harmless rejection, but the
    # operator then hand-repeats the whole plumbing - which is the expensive path this
    # verb exists to remove. So re-fetch, re-parent onto the fresh tip, re-gate, retry.
    branch = ref.split("/")[-1]

    # ⛔ AN EMPTY SOURCE REF IS A BRANCH DELETION. `push origin :refs/heads/main` deletes
    # main, and that is what `"%s:refs/heads/%s" % (commit, branch)` becomes the moment
    # `commit` is empty.
    #
    # o1 hit this chain for real today: backticks inside a double-quoted `commit-tree -m`
    # message were COMMAND-SUBSTITUTED by bash, the substitution failed, that emptied the
    # tree variable, which emptied the commit variable, and the push resolved to a delete.
    # GitHub's branch protection stopped it - not any local check.
    #
    # ⭐ Nothing in that chain was a push bug. A destructive push was the DEFAULT OUTCOME
    # of an earlier step failing quietly. So the guard belongs immediately before the push,
    # asserting the shape of what is about to be sent rather than trusting how it was built.
    if not re.fullmatch(r"[0-9a-f]{7,40}", (commit or "").strip()):
        print("  [REFUSE] refusing to push: the source ref is not a commit sha (%r)."
              % commit, file=sys.stderr)
        print("           An empty source would make this `push origin :refs/heads/%s`,"
              % branch, file=sys.stderr)
        print("           which DELETES the branch. Nothing was pushed.", file=sys.stderr)
        return 1
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", branch or ""):
        print("  [REFUSE] refusing to push: implausible branch name %r" % branch,
              file=sys.stderr)
        return 1

    for attempt in range(1, 6):
        rc, out, err = git(["-c", "credential.helper=!gh auth git-credential",
                            "push", "origin", "%s:refs/heads/%s" % (commit, branch)])
        if rc == 0:
            break
        blob = (err or out).strip()
        if "could not read Username" in blob or "/dev/tty" in blob:
            print("  [REJECTED] auth, not a race: %s" % blob[:150])
            print("             `gh auth login` in an interactive shell, then re-run.")
            print("             Nothing was clobbered.")
            # o1's documented dead end, so nobody re-derives it: you CANNOT route around
            # a failed push with the GitHub API. `gh api .../git/refs/heads/main -X
            # PATCH` returns 422 "Object does not exist" - a commit-tree commit lives
            # only in the local object store, so the objects must go over the wire first.
            return 1
        if attempt == 5:
            print("  [REJECTED] main is moving faster than this can rebuild. Re-run.")
            print("             Nothing was clobbered.")
            return 1
        print("  [race] main moved; re-parenting onto the fresh tip (attempt %d)" % attempt)
        git(["fetch", "--quiet", "origin"])
        _, others, _ = git(["log", "--oneline", "%s..%s" % (base, ref), "--"] + wanted)
        if others.strip():
            print("  [REFUSE] while retrying, another session landed changes to your")
            print("           file(s). Reconcile rather than overwrite:")
            for ln in others.splitlines()[:5]:
                print("             %s" % ln[:92])
            return 1
        commit = build_on(ref)
        _, names, _ = git(["diff", "--name-only", ref, commit])
        if sorted(n for n in names.splitlines() if n.strip()) != wanted:
            print("  [REFUSE] rebuilt commit no longer matches the named file set.")
            return 1

    # Gate 3: VERIFY from the remote. o3: a push is confirmed by ancestry after a fresh
    # fetch, never by the push command's own success-shaped output.
    git(["fetch", "--quiet", "origin"])
    rc2, _, _ = git(["merge-base", "--is-ancestor", commit, ref])
    if rc2 != 0:
        print("  [FAILED] %s is NOT an ancestor of %s after the push. Do not trust the"
              " push output; inspect by hand." % (commit[:10], ref), file=sys.stderr)
        return 1
    print("  [ok] gate 3 - verified: %s is an ancestor of %s" % (commit[:10], ref))
    print()
    print("  LANDED. %s is now current on %s." % (doc.name, ref))
    # ⭐ LANDED IS NOT DELIVERED. The line above is about a BRANCH; the human reads a FILE on disk.
    # Say which, at the exact moment the wrong belief would form. (o11, 2026-08-19)
    _report_delivery(doc)
    return 0



# Fields the TOOL writes, and therefore derived. Stripped before any content comparison
# so a field rewrite is never mistaken for lost prose. Attested-by / Resolved / Depends
# are NOT here: they carry authored reasoning, and losing one would be real damage.
MACHINE_FIELD_RE = re.compile(
    r"\*\*(?:Status|Owner|Opened|Enriched):\*\*\s*[^*\n]*", re.IGNORECASE)


def strip_machine_fields(line):
    """Remove tool-written field tokens, keep any free text the author added."""
    t = MACHINE_FIELD_RE.sub("", line)
    t = re.sub(r"^[\s\-\u00b7|]+", "", t)
    return re.sub(r"\s{2,}", " ", t).strip()


def _strip_markers(title):
    """
    Remove everything `normalize` GENERATES: the status glyph and, for terminal items,
    the status WORD it inserts after the id.

    Both are derived from the Status field, so both must come out before any content
    comparison - otherwise gate 1 reports normalize's own output as lost content, which
    is exactly what it did across 28 headings.
    """
    t = title
    for mk in set(STATUS_MARKER.values()):
        t = t.replace(mk, "")
    t = re.sub(r"\s{2,}", " ", t).strip()
    # "D1 - RESOLVED - title"  ->  "D1 - title"
    t = re.sub(r"^([A-Z]{1,3}(?:\d+[a-z]?|-[A-Z][A-Z0-9]*\d*))\s*-\s*(?:%s)\s*-\s*"
               % "|".join(sorted(TERMINAL_STATUS)), r"\1 - ", t)
    return t.strip()


def cmd_normalize(args):
    """
    Regenerate every entry heading's visual marker from its Status field.

    the human, 2026-08-06: a resolved decision showed as "D1 - What happens to the existing
    Stop hook" with the RESOLVED buried in a field below - so it read as live. He wants
    "[tick] D1 - DONE - ...". Deriving the marker from the field means the two can never
    disagree, which is the same move as the generated plate.
    """
    doc = resolve_doc_arg(args.doc)
    if not doc or not doc.exists():
        print("no such doc: %s" % doc, file=sys.stderr)
        return 2
    if refuse_if_not_mine(doc, "normalize", getattr(args, "not_mine", False)):
        return 1
    with _lock(doc):
        lines = doc.read_text(encoding="utf-8").splitlines()
        entries, _ = parse_entries(lines)
        changes = []
        for e in entries:
            if e.get("archived"):
                continue
            st = status_of(e["body"])
            mk = STATUS_MARKER.get(st or "")
            if not mk:
                continue
            i = e["line"] - 1
            m = re.match(r"^(#{1,6})\s+(.*)$", lines[i])
            if not m:
                continue
            hashes, title = m.group(1), m.group(2)
            bare = _strip_markers(title)
            # Terminal items also carry the word, because the human reads the WORD first and
            # the glyph second: "[tick] D1 - DONE - <title>".
            if st in TERMINAL_STATUS and not re.match(r"^\S+\s*-\s*%s\b" % st, bare):
                bare = re.sub(r"^(%s)\s*-\s*" % re.escape(e["id"]),
                              r"\1 - %s - " % st, bare, count=1)
            new = "%s %s %s" % (hashes, mk, bare)
            if new != lines[i]:
                changes.append((e["line"], lines[i], new))
                if not args.dry_run:
                    lines[i] = new
        if changes and not args.dry_run:
            write_doc(doc, "\n".join(lines) + "\n")

    print("orchdoc normalize - %s - %s"
          % (doc.name, "DRY RUN (nothing written)" if args.dry_run else "APPLIED"))
    print()
    for ln, old, new in changes[:14]:
        print("  line %-5d %s" % (ln, new[:96]))
    if len(changes) > 14:
        print("  ... and %d more" % (len(changes) - 14))
    if not changes:
        print("  every heading marker already matches its Status field.")
    elif args.dry_run:
        print()
        print("  Re-run with --commit to write.")
    return 0


def cmd_archive(args):
    """
    Move terminal-status entries out of sections that promise live items.

    the human: a RESOLVED decision sitting under "DECISIONS - need your call" is "pure
    clutter at that point". The active list has to hold ONLY active items or the reader
    cannot trust it - which is the same property as the generated plate, applied to the
    body of the document.
    """
    doc = resolve_doc_arg(args.doc)
    if not doc or not doc.exists():
        print("no such doc: %s" % doc, file=sys.stderr)
        return 2
    if refuse_if_not_mine(doc, "archive", getattr(args, "not_mine", False)):
        return 1
    dest_name = args.into

    with _lock(doc):
        lines = doc.read_text(encoding="utf-8").splitlines()
        entries, sections = parse_entries(lines)

        movers, held = [], []
        for e in entries:
            if e.get("archived"):
                continue
            st = status_of(e["body"])
            # Same governing-section rule the checker uses, and it has to be the SAME rule -
            # o8's DA15 was missed by `check` AND by `archive` because both asked
            # is_active_section() about a prose heading. Two guards sharing one blind spot is a
            # false all-clear, which is worse than a single gap: `archive` printed "no
            # terminal-status entries are sitting in an active section" while one sat in §2.1.
            _gov = _governing_section(lines, e["line"])
            _live = is_active_section(e["section"]) or bool(
                _gov and _gov.split(".")[0] in ("2", "3"))
            if st in TERMINAL_STATUS and _live:
                # the human's D5 ruling: only when ALL sub-items are complete does the FULL item
                # move to 99. o8 predicted this exact failure - "if archive sinks on resolved
                # alone, it would bury the majority of what is actually still owed" - and the
                # status alone cannot be trusted to know, because the entry writes its own
                # status and does not update it when a sub-item is added later.
                if has_open_subitems(e["body"], follows_stopsign_convention(entries)):
                    held.append(e["id"])
                    continue
                movers.append(e)
        if held:
            print("orchdoc archive - %s" % doc.name)
            print("  HELD BACK, terminal status but a sub-item is still open: %s"
                  % ", ".join(held))
            print("  the human's D5 ruling: an item moves to 99 only when ALL its sub-items are")
            print("  done. Mark the container IN PROGRESS and strike the finished sub-items -")
            print("  they stay visible, which is the point: seeing where the done work sits")
            print("  is what makes the remaining decision readable.")
            print()
        if not movers:
            if not held:
                print("orchdoc archive - %s" % doc.name)
                print("  no terminal-status entries are sitting in an active section.")
            return 0

        # Cut bottom-up so earlier line numbers stay valid.
        blocks = []
        for e in sorted(movers, key=lambda x: x["line"], reverse=True):
            start = e["line"] - 1
            end = start + len(e["body"].splitlines())
            blocks.append((e["id"], status_of(e["body"]), lines[start:end]))
            if not args.dry_run:
                del lines[start:end]

        if not args.dry_run:
            # ROUTE BY KIND, into the schema's own completed subsection. Matching on the
            # NAME found nothing on a schema doc (there is no heading containing "DONE"),
            # so archive APPENDED A NEW "## DONE" SECTION OUTSIDE THE SCHEMA - the entry
            # left the plate, which looked like success, and landed somewhere the index
            # does not describe.
            #
            # ⭐ Third command broken the same way by the numbered schema, after `add` and
            # is_active_section(): each one located a section by its WORDING. The schema
            # gave sections stable NUMBERS precisely so lookups would stop guessing from
            # prose - so every lookup has to actually use them.
            dest = None
            kinds = {(re.match(r"[A-Z]+", b[0]) or [""])[0][:1] for b in blocks}
            want_num = None
            if len(kinds) == 1:
                want_num = KIND_SECTION_NUM.get(kinds.pop(), (None, None))[1]
            if want_num:
                for i, l in enumerate(lines):
                    m = SECTION_RE.match(l)
                    if m and m.group(1) == want_num:
                        dest = i
                        break
            if dest is None:                       # legacy doc, or a mixed-kind batch
                for i, l in enumerate(lines):
                    if re.match(r"^##\s", l) and dest_name.upper() in l.upper():
                        dest = i
            if dest is None:
                lines += ["", "## %s" % dest_name, ""]
                dest = len(lines) - 1
            # ⛔ FINISH THE JOB. `archive` used to move the block verbatim, which left every
            # archived entry tripping E-ARCHIVEDMARKER - "archived entry still carries the
            # live-looking id 'D6'" - and the fix it printed was a hand edit. So the tool
            # relocated the entry and then told a human to tidy up after it, in a repo whose
            # whole point is that mechanical work is not a person's job.
            #
            # ⭐ the human, 2026-08-13, on exactly this: *"Are you creating the programmatic fix
            # for all of that?"* A step that a script flags and a script could perform is
            # not a finding, it is an unimplemented feature wearing a finding's clothes.
            #
            # The id is PRESERVED in the prose ("_was W14_") because ids are never reused and
            # references to them must still resolve - it just stops LOOKING live.
            body = []
            for _id, _st, blk in reversed(blocks):
                blk = list(blk)
                if blk and blk[0].startswith("#"):
                    head = re.sub(r"^(#+)\s*\S*\s*%s\s*[-–—]\s*" % re.escape(_id),
                                  r"\1 ", blk[0])
                    if head != blk[0]:
                        # ⛔ THE STRIP MUST NOT UNCOVER A SECOND ID-SHAPED TOKEN. o10's W10 was
                        # titled `W10 - W0: the morning-energy H1 ...`, where `W0` names a SCRIPT
                        # in o8's corpus, not an entry. Removing `W10 - ` left the heading
                        # beginning `W0:`, which ID_RE matches - so the parser stopped reading
                        # the `_(was W10 - DONE)_` suffix and registered a live entry `W0` that
                        # nobody wrote. E-IDORDER and E-MARKERDRIFT then fired on the phantom.
                        #
                        # ⭐ The archiver cannot know which id-shaped tokens in a human title
                        # are entry ids, so it must not CREATE the ambiguity: lead with the
                        # status word, which is the same shape archive already produces when a
                        # title happens to start with prose (`### RESOLVED - #153 closed ...`).
                        _m_hash = re.match(r"^(#+)\s*(.*)$", head)
                        if _m_hash and ID_RE.match(strip_decoration(_m_hash.group(2))):
                            head = "%s %s - %s" % (_m_hash.group(1), _st, _m_hash.group(2))
                        head = head.rstrip() + archived_heading_suffix(_id, _st)
                    blk[0] = head
                # ⛔ HEADING ONLY. A first version also rewrote the **Status:** line, and the
                # substitution produced `****Owner:**` - it damaged 8 entries across this doc
                # before the linter caught it. The rule E-ARCHIVEDMARKER states is about the
                # HEADING; the Status field is machine-readable state that other checks parse,
                # and stripping it broke E-NOSTATUS and E-MARKERDRIFT on entries nobody had
                # touched. Widening a narrow rule to "everything that mentions status" is how
                # a tidy-up becomes damage.
                body += blk + [""]
            insert_at = len(lines)
            for i in range(dest + 1, len(lines)):
                if re.match(r"^##\s", lines[i]):
                    insert_at = i
                    break
            lines[insert_at:insert_at] = body
            write_doc(doc, "\n".join(lines) + "\n")

    print("orchdoc archive - %s - %s"
          % (doc.name, "DRY RUN (nothing written)" if args.dry_run else "APPLIED"))
    print()
    print("  %d finished entr%s would leave the active sections:"
          % (len(blocks), "y" if len(blocks) == 1 else "ies"))
    for _id, _st, blk in reversed(blocks):
        print("    %-10s %-10s -> %s" % (_id, _st, dest_name))
    if args.dry_run:
        print()
        print("  Re-run with --commit to move them. Nothing is deleted; they move.")
    return 0


def cmd_clones(args):
    """
    Is every repo's checkout current with its remote? o5's ask, from o7's incident.

    o7 branched from a clone 129 commits behind and produced two confident-wrong claims
    from reading a stale snapshot as if it were current - a phantom line it "found", and
    "45 em-dashes on the live page" when the page has 14. The files looked completely
    normal, which is the whole problem.

    ⚠️ THE DAMAGE CLAIM WAS WRONG TWICE, and the second time it was o9's, shipped in
    this tool. Both corrections came from verification, not argument:

      o7  "merging a stale branch reverts 9,728 lines"  -> FALSE. Measured with
          `git diff`, which answers what a branch LACKS, not what a merge DOES.
      o9  "editing a stale file reverts THAT file's upstream changes" -> ALSO FALSE.
          Constructed the case: stale edit in a DIFFERENT region -> upstream survived
          2 of 2 and the edit applied; SAME region -> conflict, exit 1, announced.

    Git's 3-way merge protects the content in every case. Silent reversion needs the
    merge machinery bypassed - force-push, copying a tree over - a different hazard.

    ⭐ WHAT A STALE CLONE ACTUALLY COSTS IS SEMANTIC, and it is worse than the thing
    twice claimed, because nothing catches it. GIT PROTECTS THE CONTENT; NOTHING
    PROTECTS YOUR REASONING. Off a 129-behind tree o7 stated a phantom line that had
    been deleted upstream, and "45 em-dashes on the live page" when the page has 14.

    "A merge conflict announces itself. A phantom line does not. The files open and read
     as complete" - the same property that makes a truncated skill dangerous.

    So this check does not protect the repo. It protects every CLAIM you make about the
    repo while standing in it.

    The oracle is o5's and it is unambiguous - one number, no plausible-but-wrong
    reading:  git rev-list --count HEAD..origin/main   must be 0.
    """
    repos = citable_repos()
    print("orchdoc clones - is every checkout current with its remote?")
    print()
    worst = 0
    for r in repos:
        if not (r / ".git").exists():
            continue
        if not args.no_fetch:
            git(["fetch", "--quiet", "origin"], cwd=r)
        rc, head, _ = git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=r)
        rc2, behind, _ = git(["rev-list", "--count", "HEAD..%s" % CANONICAL_REF], cwd=r)
        if rc2 != 0 or not behind.isdigit():
            print("  [--]    %-34s no origin/main to compare" % r.name)
            continue
        n = int(behind)
        rc3, dirty, _ = git(["status", "--porcelain"], cwd=r)
        ndirty = len([x for x in dirty.splitlines() if x.strip()])
        if n == 0:
            print("  [OK]    %-34s current  (%s)" % (r.name, head))
        else:
            worst = 1
            print("  [BEHIND]%-34s %d commits behind origin/main  (%s%s)"
                  % (r.name, n, head,
                     ", %d dirty file(s)" % ndirty if ndirty else ""))
            if ndirty:
                print("          %-34s ANY CLAIM YOU MAKE FROM THIS TREE IS SUSPECT -"
                      % "")
                print("          %-34s deleted files still read as present, and counts"
                      % "")
                print("          %-34s are of the old content. Git protects the merge;"
                      % "")
                print("          %-34s nothing protects your reasoning." % "")
    print()
    print("  Oracle: git rev-list --count HEAD..origin/main must be 0 before branching")
    print("  or editing anywhere long-lived. One number; it cannot read plausibly wrong.")
    return worst


def _vgit(args, cwd=PROJECTS):
    """git via an argument LIST. No shell, so no MSYS path mangling, ever."""
    p = subprocess.run(["git", "-C", str(cwd)] + list(args),
                       capture_output=True, text=True, timeout=60)
    return p.returncode, p.stdout, p.stderr


def cmd_verify(args):
    """
    Known-good verification primitives. Each names its oracle and cannot return a
    plausible middle answer.

    Built because two lessons were RECORDED and not mechanised: Git Bash silently
    mangles `REF:dir/file`, and a check run through a different execution path than the
    tool is a different measurement. A library removes the hand-rolled probe entirely.
    """
    what = args.what
    repo = Path(args.repo) if args.repo else PROJECTS

    if what == "at":
        # Show a file at a ref. THE case Git Bash breaks: `git show REF:dir/file.py`
        # becomes `REF;dir\file.py` in MSYS and reports a present file as missing.
        rc, out, err = _vgit(["show", "%s:%s" % (args.ref, args.path)], cwd=repo)
        print("  oracle: git show %s:%s   (argument list, no shell)"
              % (args.ref, args.path))
        if rc != 0:
            print("  ABSENT at that ref. git said: %s" % err.strip()[:100])
            return 1
        print("  PRESENT - %d lines" % len(out.splitlines()))
        return 0

    if what == "landed":
        # Is the working copy identical to the canonical ref? The question
        # `git status` cannot answer, because it compares tree to HEAD.
        rel = args.path
        rc, canon, _ = _vgit(["show", "%s:%s" % (CANONICAL_REF, rel)], cwd=repo)
        if rc != 0:
            print("  oracle: git show %s:%s -> absent" % (CANONICAL_REF, rel))
            print("  NOT LANDED - the file does not exist on %s" % CANONICAL_REF)
            return 1
        try:
            local = (repo / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            print("  cannot read local copy: %s" % e)
            return 1
        same = local.replace("\r\n", "\n").rstrip("\n") == \
            canon.replace("\r\n", "\n").rstrip("\n")
        print("  oracle: byte comparison of the working copy against %s"
              % CANONICAL_REF)
        print("          (git status compares tree to HEAD and cannot answer this)")
        print("  %s" % ("LANDED - identical" if same else
                        "NOT LANDED - the copy on disk differs from %s" % CANONICAL_REF))
        return 0 if same else 1

    if what == "merged":
        # Is this commit's work on the canonical ref? ANCESTRY LIES after a squash -
        # o7 nearly deleted live work trusting `git log origin/main..HEAD`. So check
        # ancestry AND, failing that, whether the content is present.
        sha = args.sha
        _vgit(["fetch", "--quiet", "origin"], cwd=repo)
        rc, _, _ = _vgit(["merge-base", "--is-ancestor", sha, CANONICAL_REF], cwd=repo)
        if rc == 0:
            print("  oracle: git merge-base --is-ancestor %s %s -> exit 0"
                  % (sha[:10], CANONICAL_REF))
            print("  MERGED (ancestor)")
            return 0
        rc2, subj, _ = _vgit(["log", "-1", "--format=%s", sha], cwd=repo)
        if rc2 == 0 and subj.strip():
            rc3, found, _ = _vgit(
                ["log", CANONICAL_REF, "--oneline", "--grep",
                 subj.strip()[:60], "-F"], cwd=repo)
            if found.strip():
                print("  oracle: ancestry said NO, but the commit SUBJECT is on %s"
                      % CANONICAL_REF)
                print("          %s" % found.splitlines()[0][:90])
                print("  MERGED (squashed - ancestry would have lied here)")
                return 0
        print("  oracle: neither ancestry nor subject found on %s" % CANONICAL_REF)
        print("  NOT MERGED")
        return 1

    if what == "current":
        # Is this checkout current with its remote? o5's oracle: one number.
        _vgit(["fetch", "--quiet", "origin"], cwd=repo)
        rc, behind, _ = _vgit(["rev-list", "--count", "HEAD..%s" % CANONICAL_REF],
                              cwd=repo)
        n = behind.strip()
        print("  oracle: git rev-list --count HEAD..%s -> %s" % (CANONICAL_REF, n))
        if rc != 0 or not n.isdigit():
            print("  UNKNOWN - no %s to compare against" % CANONICAL_REF)
            return 1
        print("  %s" % ("CURRENT" if n == "0" else
                        "STALE - %s commits behind. Every claim made from this tree is "
                        "suspect." % n))
        return 0 if n == "0" else 1

    print("unknown check: %s" % what, file=sys.stderr)
    return 2



# Heading text -> schema number. ORDER IS SIGNIFICANT: the specific patterns must be
# tested before the general ones, because "RESOLVED DECISIONS" matches both the decision
# rule and the done rule and only one of those answers is right.
#
# Derived from a survey of all eight live docs, not invented - which is why it is a
# rename table rather than a reorganisation plan.
ADOPT_RULES = [
    # (all-of these substrings, none-of these, schema number)
    # SPECIMEN goes FIRST. o9's heading reads "SPECIMENS - verification failures caught
    # in flight", which contains the literal words IN FLIGHT and was duly filed as
    # section 3. A heading's DESCRIPTION can contain another section's NAME, so the more
    # specific noun has to be tested before the more general phrase.
    (["SPECIMEN"],                     [],                    "4"),
    (["GUARD"],                        [],                    "5"),
    (["FINDING"],                      [],                    "4"),
    (["ON CLAUDE'S PLATE", "IN FLIGHT"], [],                  "3"),
    (["DECISION"],  ["RESOLVED", "ARCHIV", "DONE", "ANSWERED", "SUPERSEDED"], "2.1"),
    (["DECISION"],                     [],                    "99.1"),
    (["QUESTION"],  ["ANSWERED", "RESOLVED", "DONE"],         "2.2"),
    (["QUESTION"],                     [],                    "99.2"),
    (["TO-DO"],     ["DONE", "SHIPPED", "COMPLETE"],          "2.3"),
    (["TODO"],      ["DONE", "SHIPPED", "COMPLETE"],          "2.3"),
    (["TO-DO"],                        [],                    "99.3"),
    (["TODO"],                         [],                    "99.3"),
    (["PLATE"],                        [],                    "2"),
    (["URL"],                          [],                    "1"),
    (["LINK"],                         [],                    "1"),
    (["LOGIN"],                        [],                    "1"),
    # DELIVERABLE alone is NOT enough. o5's "DELIVERABLES - FULL local paths" is a links
    # section; o9's "THE DELIVERABLE, RESTATED" is a statement of the charter, and the
    # bare word cannot distinguish them - the first dry-run duly filed the charter under
    # Links and Docs. Require a second word that means "a list of places."
    (["DELIVERABLE", "PATH"],          [],                    "1"),
    (["DELIVERABLE", "INDEX"],         [],                    "1"),
    (["DONE"],                         [],                    "99"),
    (["RESOLVED"],                     [],                    "99"),
    (["ARCHIV"],                       [],                    "99"),
    (["SUPERSEDED"],                   [],                    "99"),
    (["PARKED"],                       [],                    "99"),
]


def adopt_number(heading):
    """Schema number for an existing heading, or None if it is genuinely unclear.

    None is a real answer here. A heading forced into the wrong section moves an entry
    out of the human's view while looking tidier than before, which is strictly worse than
    leaving it alone and saying so.
    """
    t = heading.lstrip("#").strip().upper()

    # ⛔ A HEADING CARRYING AN ENTRY ID IS AN ENTRY, NOT A SECTION - and adopting it as a
    # section is a category error regardless of which number it would get.
    #
    # o1 caught this: `## ⭐✅ D-PAUSE — RESOLVED by the human` was being mapped to §99 because
    # it contains the word RESOLVED. But it is a DECISION, written at H2, whose ruling is
    # closed while the work it authorised is NOT built - o1 verified that in the handler
    # source rather than from the heading. Sinking it to §99 would have buried a live
    # workstream under a ✅, which is the false-DONE direction and the more dangerous one.
    #
    # ⭐ The general rule beats the special case: sections are containers, entries are
    # contents, and a mapper that cannot tell them apart will eventually file one as the
    # other. Leave entries alone; only their SECTION moves.
    if ID_RE.match(re.sub(r"^[\W\s]+", "", heading.lstrip("#").strip())):
        return None

    for musts, nots, num in ADOPT_RULES:
        if all(m in t for m in musts) and not any(n in t for n in nots):
            return num
    return None


# The identity line. `oN` is REQUIRED to match the filename; the trailing role is the
# orchestrator's own words and is preserved verbatim.
TITLE_RE = re.compile(
    r"^#\s+(?:\W+\s*)?Orchestrator\s+Decision\s+Doc\s*[-\u2013\u2014]\s*"
    r"\*{0,2}(o\d+)\*{0,2}\s*(?:<br\s*/?>)?\s*(.*)$", re.IGNORECASE | re.DOTALL)


def canonical_title(num, role=""):
    """ONE H1, one line - the human's ruling, 2026-08-07:

        # Orchestrator Decision Doc - o9 (orchestration process engineering)

    Three formats were tried. Two failed on rendering; the third failed on RISK:

        1. H1 + paragraph subtitle - subtitle orphaned below the generated index
        2. one line with <br>      - the renderer escaped it into visible "<br>" text
        3. two H1 lines            - worked, but a second "# " can be read as a second
                                     DOCUMENT TITLE by any future title-extractor
        4. one line, no tag        - this

    On (3): no current consumer extracts H1s from OrchDocs - and that does not clear it.
    The hazard belongs to tools that do not exist yet, and every one of them will assume
    the "one H1 per document" convention. the human: "if that is a possibility for any unknown
    future grep, let's revert."

    The visual line break is simply unavailable: markdown cannot break inside a heading,
    inline HTML is escaped here, and a second heading is now barred by E-ONEH1. The title
    wraps on its own, which is what it was doing anyway.
    """
    role = (role or "").strip()
    if role and not role.startswith("("):
        role = "(%s)" % role.strip("()")
    return "# Orchestrator Decision Doc - %s%s" % (num, (" " + role) if role else "")


def title_span(lines):
    """(start, end) of the title block, inclusive. THE one definition of where it ends.

    Round one of this format placed the generated index after the title's FIRST line and
    orphaned the subtitle below it. Every top-of-document insertion now asks this function
    instead of re-deriving the answer, because three places deriving it independently is
    how the first version got it wrong in one of them.
    """
    hi = next((i for i, l in enumerate(lines[:40]) if l.startswith("# ")), None)
    if hi is None:
        return None, None
    end = hi
    if hi + 1 < len(lines) and lines[hi + 1].startswith("# "):
        end = hi + 1
    return hi, end



def _git_date(doc, first=False):
    """Commission date (first commit adding the file) or last-touched date, from git.

    Returns None when git cannot answer - an unlanded doc has no commit history, and
    printing a fabricated date would be exactly the claim-versus-measurement error this
    block exists to avoid.
    """
    # Query origin/main, NOT HEAD. OrchDocs are landed to main by plumbing while the
    # working tree sits on a feature branch, so `git log -- <doc>` from HEAD finds no
    # history at all and the field would read "not yet landed" for a doc that landed
    # hours ago. The canonical ref is where the history is, which is the same reason
    # every other check in this file resolves against origin/main.
    if first:
        args = ["log", CANONICAL_REF, "--diff-filter=A", "--follow",
                "--format=%ad", "--date=format:%d-%b-%Y", "--", doc.name]
    else:
        args = ["log", CANONICAL_REF, "-1", "--format=%ad",
                "--date=format:%d-%b-%Y %H:%M", "--", doc.name]
    rc, out, _ = git(args)
    if rc != 0 or not out.strip():
        return None
    return out.strip().splitlines()[-1 if first else 0].strip()


def render_meta(doc, lines, landing_now=False):
    """The header metadata - every field a measurement, none of them a claim.

    ⛔ `landing_now` EXISTS BECAUSE THE DERIVED VALUE CANNOT INCLUDE ITSELF. "Last updated"
    reads the last commit touching this file, so a refresh run BEFORE a commit stamps the
    PREVIOUS commit - the field is then permanently one landing behind, which is how o9's
    read 07-Aug on a doc edited that day. Measured 2026-08-13: refreshing then committing
    produced 11-Aug on a commit made on the 13th. Chicken-and-egg, not a patchable bug.

    ⭐ When `orchdoc.py commit` is the caller, NOW *is* the commit timestamp, so stamping it
    is a measurement of the commit being made - not a guess about it. The field's promise is
    "never hand-written," and a tool stamping the moment it lands satisfies that exactly.
    Every other caller keeps reading the log, because for them the log is the truth.
    """
    name = doc.name
    commissioned = _git_date(doc, first=True)
    updated = (_dt.datetime.now().strftime("%d-%b-%Y %H:%M") if landing_now
               else _git_date(doc))
    # Use the PLATE's own selection rule, never a private copy of it. A hand-rolled
    # second copy read a "status" key that parse_entries does not even return, so the
    # count was silently 0 while the plate itself listed an open item - a header
    # contradicting the section it points at, which is precisely the multi-copy defect
    # this tool exists to remove. One rule, one reader.
    entries, _sections = parse_entries(lines)

    # ⭐ ASK THE PLATE BUILDER. This used to apply its OWN rule - every open entry anywhere -
    # so the moment §3 gained `Status: OPEN` entries at the §3 rename, the header announced
    # "Open on the human's plate: 4" while the generated plate 130 lines below said "Nothing
    # open". The orchestrator's own work reported as the human's, in the doc's most-read line.
    #
    # An INDEPENDENT AUDITOR found it, not `check`: no invariant compares the header to the
    # plate, because each is individually correct by its own rule. That is exactly the
    # multi-copy defect the comment above already claims to have fixed once - and a second
    # rule, however carefully written, regresses again the next time the schema moves.
    #
    # ⛔ The first fix here WAS a second rule (count only §2), and it under-counted o8 by 3:
    # three live decisions sit under a non-§ heading, so a section filter HID them - the one
    # direction that must never happen. The only stable answer is to derive the number from
    # the same function that renders the block. One rule, one reader, as the comment says.
    _pb = build_plate_block(entries, lines)
    _m = re.search(r"_(\d+) open\. Generated by", "\n".join(_pb))
    open_plate = int(_m.group(1)) if _m else 0

    out = [META_BEGIN, ""]
    out.append("| | |")
    out.append("|---|---|")
    out.append("| **Commissioned** | %s |" % (commissioned or "_not yet landed_"))
    # The provenance in the label must match where the value ACTUALLY came from. Saying
    # "from the commit log" on a value stamped at landing time is the same defect this
    # whole field just failed at: a description asserting something the source does not say.
    out.append("| **Last updated** | %s _(%s, never hand-written)_ |"
               % (updated or "_not yet landed_",
                  "stamped by `orchdoc commit` as it landed" if landing_now
                  else "from the commit log"))
    # Label AND anchor both DERIVED from the section title. Hardcoding them meant the
    # header linked to one specific person's slug, which for any other name points at a
    # heading that does not exist - a dead link in the generated doc. Third instance of
    # the same bug in this file (after the two detection regexes): a name baked into
    # something the tool GENERATES, correct for exactly one person.
    # THE HUMAN, 2026-09-04: *"the session ID should be written to the doc, no? Perhaps both if
    # possible?"* - both. The session's NAME is the primary identity and the Stop hook reads it
    # from the app's own store; this row is the second copy, and it lives in the artifact the
    # identity is ABOUT rather than in a side file. It survives the app store being unreadable,
    # it is readable by anything, and it answers a question nothing else did: which session is
    # driving this doc right now. Stamped at landing, like Last updated, so it is never a guess.
    _drv = os.environ.get("CLAUDE_CODE_HOST_SESSION_ID") or ""
    if landing_now and _drv:
        out.append("| **Driven by** | `%s` _(stamped by `orchdoc commit`; the session that "
                   "last landed this doc)_ |" % sanitize_field(_drv, 90))
    else:
        # Not landing: carry the existing value forward rather than dropping it. A refresh
        # must never blank a field it has no fresh reading for.
        for _ln in (lines or []):
            _m = re.match(r"^\|\s*\*\*Driven by\*\*\s*\|\s*(.+?)\s*\|\s*$", _ln)
            if _m:
                out.append("| **Driven by** | %s |" % _m.group(1))
                break
    _plate_title = dict((n, t) for n, t, _d in schema_sections())["2"]
    _plate_label = _plate_title.replace("LIVE ON ", "").replace("'S PLATE", "")
    _plate_label = _plate_label.title() + "'s plate"     # THE HUMAN -> the human's plate
    _plate_anchor = "#\u00a72-" + re.sub(r"[^a-z0-9]+", "-",
                                        _plate_title.lower().replace("'", "")).strip("-")
    out.append("| **Open on %s** | **%%d** - see [\u00a72](%s) |"
               % (_plate_label, _plate_anchor)
               % open_plate)
    # THE ORACLE. the human left this slot open with "?? Oracle info?? what else?" and this is
    # the answer: the reader's own question, "am I looking at the current copy?", which
    # nothing else on the page can settle. o8's doc was 327 lines behind while being
    # internally consistent and correctly formatted - re-reading it could never have
    # revealed that, because staleness leaves no trace in the stale copy.
    out.append("| **Canonical copy** | `origin/main : %s` - this is the ONLY one |" % name)
    out.append("| **Verify it is current** | `python .shared/scripts/orchdoc.py verify "
               "current --path %s` |" % name)
    out.append("")
    out.append(META_END)
    return out




def _config_get(key):
    """One place a standing choice lives, so there is one place to change it.

    Kept dependency-free and failure-tolerant on purpose: a missing or malformed config must
    degrade to the previous behaviour, never crash a linter run. A config that can break the
    tool is a config people delete.
    """
    try:
        import json
        p = PROJECTS / ".orchdoc-config.json"
        if p.exists():
            return (json.loads(p.read_text(encoding="utf-8")) or {}).get(key) or None
    except Exception:
        pass
    return None


def human_name(default="THE HUMAN"):
    """What to call the user, from Claude Code's own account record.

    `~/.claude.json` -> oauthAccount.displayName. An exhaustive scan of that config found
    exactly one name field, and it is not exported to the environment, so this reads the
    file directly.

    Returns the default on ANY failure - missing file, missing key, unreadable JSON. A doc
    that cannot learn the name should say "THE HUMAN" (which is what the published skill
    says anyway); it should never fail to generate over a nicety.
    """
    cfg = _config_get("human_name")
    if cfg:
        # What they SAID beats what we inferred. A derived name is a guess about a person.
        return cfg.strip().upper()[:24]
    try:
        import json as _json
        cfg = Path(os.path.expanduser("~")) / ".claude.json"
        name = _json.loads(cfg.read_text(encoding="utf-8")).get(
            "oauthAccount", {}).get("displayName", "")
        name = (name or "").strip()
        if not name:
            return default
        # THIS FIELD IS FREE TEXT. the human's own profile screenshot proves it: "What should
        # we call you?" held "the human This is The File". A heading built straight from it
        # becomes "LIVE ON THE HUMAN THIS IS THE FILE'S PLATE", and the field could equally
        # hold an emoji, 200 characters, or markdown that breaks the heading.
        #
        # So take the FIRST TOKEN only, strip anything that is not a letter, hyphen or
        # apostrophe, and cap the length. A section heading is structure; user-supplied
        # free text must be narrowed before it becomes structure.
        # Take the first token that actually yields letters, so a leading emoji or a
        # title ("Dr. the human") does not collapse the whole name to the fallback.
        for tok in name.split():
            clean = re.sub(r"[^A-Za-zÀ-ɏ'\-]", "", tok)[:24]
            if clean:
                return clean.upper()
        return default
    except Exception:
        return default


def schema_sections():
    """SCHEMA_SECTIONS with the plate heading personalised.

    A function, not a constant, so the name is read when a doc is written rather than when
    the module is imported - which also means the checker and the generator cannot disagree
    about it, since both call this.
    """
    who = human_name()
    # Substitute in the DESCRIPTION as well as the title. It only ever substituted the title,
    # so the moment §2's description started using {NAME} - to remove the same pronoun
    # ambiguity the human flagged in §3 - the placeholder leaked verbatim into the rendered index.
    # A template that is expanded in one of its two fields is worse than one expanded in
    # neither: the half that works hides the half that does not.
    # Upper-case in the HEADING ("LIVE ON THE HUMAN'S PLATE"), title-case in the DESCRIPTION
    # ("only what needs the human"). The same substitution reads as emphasis in a heading and as
    # shouting inside a sentence, and the description is a sentence.
    return [(n, t.replace("{NAME}", who), d.replace("{NAME}", who.title()))
            for n, t, d in SCHEMA_SECTIONS]


def render_index(lines):
    """The navigable spine, generated from the headings that actually exist.

    Two lists in one block: the section index (the human's "ORCHDOC INDEX") and the findings
    index (his idea, and a good one - 50 findings are unnavigable without it). Both
    DERIVED, because a hand-written index is a second copy of the truth and o8's plate
    showed how that ends: 5 of 16 rows pointed at items already resolved, so the human spent
    his review re-reading settled questions.
    """
    present = {}
    for ln in lines:
        m = SECTION_RE.match(ln)
        if m:
            # Keep the TEXT: a custom section can only be listed by the name its author
            # gave it, and stripping the number leaves exactly that.
            present[m.group(1)] = re.sub(r"^#+\s*\W*\s*\u00a7?\s*[\d.]+\s*", "",
                                         ln).strip() or ("\u00a7" + m.group(1))

    out = [INDEX_BEGIN, ""]
    out.append("**Sections.** Numbered, so a reference survives the prose moving.")
    out.append("")
    # MERGE the schema spine with every numbered section actually FOUND. This half is
    # load-bearing under the human's amendment: if orchestrators may add sections 6 to 98, an
    # index that only ever prints the schema would omit them - and a reader who consults
    # the index, does not see 12, and concludes it does not exist is worse off than with
    # no index at all. The index describes the document; it does not describe the standard.
    known = dict((n, (t, d)) for n, t, d in schema_sections())
    allnums = sorted(set(known) | set(present),
                     key=lambda n: [int(p) for p in n.split(".")])
    for num in allnums:
        depth = num.count(".")
        if num in known:
            title, note = known[num]
            mark = "" if num in present else "  **<- MISSING**"
        else:
            title, note, mark = present[num], "this orchestrator's own", ""
        out.append("%s- **\u00a7%s %s** - %s%s" % ("  " * depth, num, title, note, mark))
    out.append("")

    out.append(INDEX_END)
    return out



def reorder_sections(lines):
    """Return lines with numbered top-level sections in ascending order.

    Raises ValueError if any line would be lost - the caller must not write on that.
    """
    # Split into: head (everything before the first numbered section) + blocks.
    first = None
    for i, ln in enumerate(lines):
        if ln.startswith("## ") and SECTION_RE.match(ln):
            first = i
            break
    if first is None:
        return lines

    head, rest = lines[:first], lines[first:]

    # A block runs from one TOP-LEVEL heading to the next. Un-numbered "## " sections
    # therefore travel with the numbered section above them, which keeps an
    # orchestrator's domain content next to whatever it was written beside.
    blocks, cur = [], None
    for ln in rest:
        if ln.startswith("## "):
            if cur is not None:
                blocks.append(cur)
            cur = [ln]
        else:
            (cur if cur is not None else head).append(ln)
    if cur is not None:
        blocks.append(cur)

    def key(b):
        m = SECTION_RE.match(b[0])
        if not m:
            return (1, 0, 0)                      # un-numbered: keep after numbered
        parts = [int(x) for x in m.group(1).split(".")]
        return (0, parts[0], parts[1] if len(parts) > 1 else 0)

    ordered = head + [ln for b in sorted(blocks, key=key) for ln in b]

    # THE GATE. A reorder that drops a line is worse than no reorder, and "it obviously
    # only moves blocks" is exactly the confidence that produced the other content
    # defects found this session.
    if sorted(ordered) != sorted(lines):
        lost = len(lines) - len(ordered)
        raise ValueError("reorder would change content (%+d lines) - REFUSED" % -lost)
    return ordered



def render_findings_index(lines):
    """The findings index, for a reader who is ALREADY in the findings section.

    Generated, like everything else derived - a hand-written list of 53 findings is a
    second copy of the truth and would rot on the first entry anyone added.
    """
    fin = []
    for ln in lines:
        m = re.match(r"^###\s+(?:\W+\s*)?(F\d+)\b\s*[-\u2013]?\s*(.*)$", ln)
        if m:
            title = m.group(2).strip().rstrip(".")
            title = re.sub(r"^(?:DONE|OPEN|RESOLVED|RECORDED)\s*[-\u2013]\s*", "", title)
            fin.append((m.group(1), title[:100]))
    if not fin:
        return []
    out = [FINDEX_BEGIN, "", "**%d findings.**" % len(fin), ""]
    for fid, title in fin:
        out.append("- `%s` - %s" % (fid, title))
    out += ["", FINDEX_END]
    return out



def cmd_handoff(args):
    """Verify every OPEN item in a doc has a home in a LIVING doc before it is frozen.

    The oracle is the RECEIVING document, never the sending one. A sender's note saying
    "passed to o8" is an assertion; `DA17` appearing in o8's doc is a fact.
    """
    src = resolve_doc_arg(args.doc)
    if not src or not src.exists():
        print("no such doc: %s" % args.doc, file=sys.stderr)
        return 2

    lines = src.read_text(encoding="utf-8").split("\n")
    entries, _ = parse_entries(lines)
    live = [e for e in entries
            if not e.get("archived") and status_of(e["body"]) in PLATE_STATUS]

    # RAW-TEXT CANDIDATES, because the parser is not the authority on what is live here.
    # o3's D11 - the exact item this check was built for - exists only as BULLETS under an
    # "OPEN-ITEMS INDEX", so parse_entries() returns ZERO for that doc and the check
    # reported "every open item appears in a living doc" while D11 was orphaned.
    #
    # A verifier that answers "all clear" because it could not read the file is worse than
    # no verifier: it converts an unread document into a certificate. Same failure as the
    # plate saying "Nothing open" when nothing parsed, and as a fetch failure reading as
    # "current". So: also collect id-shaped tokens from the raw text of ACTIVE sections.
    raw_ids = set()
    prose_titles = {}
    cur = ""
    for ln in lines:
        if ln.startswith("## "):
            cur = ln
        if not is_active_section(cur):
            continue
        # NOTE: NO \b. A word-boundary escape sent through a shell heredoc arrives
        # as a literal 0x08 BACKSPACE, which matches nothing and ships the check
        # DEAD while looking correct in a diff. The lookahead cannot be corrupted.
        for m in re.finditer(r"\*\*([A-Z]{1,3}\d{1,3})(?![A-Za-z0-9])", ln):
            raw_ids.add(m.group(1))
            # keep the surrounding text: the TITLE is what actually identifies an item
            # across documents, because the id alone is namespaced per doc.
            tail = ln[m.end():].lstrip(" *—-:").strip()
            if tail and len(tail) > len(prose_titles.get(m.group(1), "")):
                prose_titles[m.group(1)] = tail
    known = {e["id"] for e in entries}
    extra = sorted(raw_ids - known)

    # Every OTHER OrchDoc is a candidate receiver. Read them from the CANONICAL ref, not
    # the working tree: a receiver that only has the item on someone's local branch has
    # not received it in any sense that survives.
    others = {}
    for p in sorted(PROJECTS.glob("ORCHESTRATOR-DECISIONS-*.md")):
        if p.name == src.name:
            continue
        rc, txt, _ = git(["show", "%s:%s" % (CANONICAL_REF, p.name)])
        others[p.name] = txt if rc == 0 else p.read_text(encoding="utf-8", errors="replace")

    print("orchdoc handoff - %s" % src.name)
    print("  %d parsed open item(s), %d id(s) found only as prose" % (len(live), len(extra)))
    if not live and not extra:
        print("  [REFUSE] nothing to account for AND nothing parsed - this doc's items are")
        print("           not in a form this check can read, so it cannot certify that")
        print("           freezing it is safe. That is NOT the same as 'all clear'.")
        print("           Entries need `### <ID> - title` headings; until then, verify by")
        print("           hand and say so explicitly.")
        return 1
    orphans = []
    for e in live:
        title_key = strip_decoration(e["title"])[:40].lower()
        found = []
        for name, txt in others.items():
            if re.search(r"\b%s\b" % re.escape(e["id"]), txt) or \
                    (len(title_key) > 12 and title_key in txt.lower()):
                found.append(name)
        if found:
            print("  [ok]      %-8s -> %s" % (e["id"], ", ".join(found)))
        else:
            orphans.append(e)
            print("  [ORPHAN]  %-8s %s" % (e["id"], strip_decoration(e["title"])[:52]))

    # prose-only ids get the same treatment as parsed entries
    for eid in extra:
        # ⛔ A BARE ID PROVES NOTHING ACROSS DOCS. Entry ids are a PER-DOC namespace -
        # o1, o5, o7 and o9 all have a D2 - so "D2 appears in six other docs" is six
        # different decisions, not six homes for this one. That is the same ambiguity
        # E-BADTOUCH exists to catch, and the first version of this check walked straight
        # into it and reported every item safe.
        #
        # A transfer is evidenced by a QUALIFIED reference: the receiving doc naming the
        # SENDER (`o3:D11`, "adopted from o3"), or carrying the item's own title text.
        title = strip_decoration(prose_titles.get(eid, "")).strip()
        src_tag = src.name.replace("ORCHESTRATOR-DECISIONS-", "").replace(".md", "")
        found = []
        for n, txt in others.items():
            qualified = re.search(
                r"(?<![A-Za-z0-9])%s\s*[:\-]?\s*%s(?![A-Za-z0-9])"
                % (re.escape(src_tag), re.escape(eid)), txt, re.I)
            adopted = (re.search(r"(?:adopted|from|inherited)[^\n]{0,40}%s"
                                 % re.escape(src_tag), txt, re.I)
                       and re.search(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])"
                                     % re.escape(eid), txt))
            by_title = len(title) > 18 and title[:34].lower() in txt.lower()
            if qualified or adopted or by_title:
                found.append(n)
        if found:
            print("  [ok]      %-8s -> %s  (prose-only id)" % (eid, ", ".join(found)))
        else:
            orphans.append({"id": eid, "title": eid + " (found only as prose)"})
            print("  [ORPHAN]  %-8s found only as prose, and in no other doc" % eid)

    if not orphans:
        print("  every open item appears in a living doc.")
        return 0

    print()
    print("  [REFUSE] %d open item(s) exist ONLY in this doc." % len(orphans))
    print("           Freezing it strands them - nobody is watching the gap between")
    print("           two owners, which is how D11 sat orphaned for ten days.")
    print("           Move each into the receiving doc FIRST, then re-run.")
    print("           A sender's handoff note is an assertion; the item appearing in the")
    print("           receiving doc is the fact.")
    return 1




# A path or URL a reader might need to OPEN. Deliberately narrow: a bare word with a dot in
# it is not an asset, and a link list padded with noise is one nobody reads.
ASSET_URL_RE = re.compile(r"https?://[^\s)\]<>\"']+")
ASSET_PATH_RE = re.compile(
    r"(?<![\w/.])((?:[A-Za-z]:[\\/]|\.{0,2}[\\/]|\.shared[\\/]|[\w.-]+[\\/])"
    r"[\w.\\/-]*\.(?:md|py|ts|tsx|json|ps1|sh|txt|ya?ml))")


def harvest_assets(lines, doc):
    """Every URL and path the doc mentions, with whether it RESOLVES.

    The point is not to list strings - it is to distinguish a pointer a reader can follow
    from one that is already dead. An unverified link list is the same false comfort as an
    unverified status field.
    """
    body = "\n".join(lines)
    found = {}
    for m in ASSET_URL_RE.finditer(body):
        # Strip markdown punctuation that abuts a URL in prose. A trailing backtick
        # produced a row labelled "`" pointing at a 404 - a dead pointer manufactured BY
        # the section whose job is to prevent dead pointers.
        u = m.group(0).rstrip(".,;:`*_”’'\"")
        found.setdefault(u, {"kind": "url", "exists": None})
    for m in ASSET_PATH_RE.finditer(body):
        raw = m.group(1).strip()
        if raw.startswith(("http", "#")):
            continue
        if "..." in raw or "<" in raw:
            continue          # an ELISION in prose ("_legacy/.../Foo.ts"), not a path
        probe = raw
        # A leading "/.claude/..." is almost always "~/.claude/..." with the tilde eaten by
        # the surrounding markdown. Resolving it against the workspace reports a LIVE file
        # as dead - and a checker that confidently calls a real asset dead is the damaging
        # direction, not the harmless one.
        if probe.startswith(("/.claude", "\\.claude")):
            probe = str(Path(os.path.expanduser("~")) / probe.lstrip("/\\"))
        pp = Path(probe)
        if not pp.is_absolute():
            pp = PROJECTS / probe
        exists = pp.exists()
        if not exists:
            # Try every plausible root before calling it dead. The memory dir is the one that
            # bit: `memory/foo.md` resolves nowhere near the workspace, so six LIVE notes were
            # reported as "DOES NOT EXIST" - and this section's whole job is to hand a reader
            # pointers they can follow. Confidently calling a real asset dead is the damaging
            # direction: it invites deleting a working reference, which is worse than the
            # missing row it was trying to prevent.
            for alt in (Path(os.path.expanduser("~")) / probe.lstrip("/\\"),
                        PROJECTS.parent / probe.lstrip("/\\")) + tuple(
                            base / probe.lstrip("/\\")
                            for r in _EXTERNAL_REPOS for base in (r, r.parent)):
                if alt.exists():
                    exists = True
                    break
        found.setdefault(raw, {"kind": "path", "exists": exists})
    return found


def section_span(lines, num):
    """(start, end) of a §-numbered section, or None."""
    start = None
    for i, l in enumerate(lines):
        m = SECTION_RE.match(l)
        if m and m.group(1) == num:
            start = i
        elif start is not None and l.startswith("## "):
            return start, i
    return (start, len(lines)) if start is not None else None


STRUCK_RE = re.compile(r"~~.*?~~", re.S)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
FENCE_RE = re.compile(r"^```.*?^```", re.S | re.M)


def still_asserted(text):
    """The doc with every RETRACTED span removed, so a search hits only live claims.

    ⛔ WHY A PLAIN GREP CANNOT ANSWER "DOES THIS DOC STILL CLAIM X". o7's case, and it is the
    cleanest one the fleet has produced: a false claim about the purchase chain was corrected in
    their doc, twice, with the old text struck through. A third copy sat UNSTRUCK, directly
    beneath a correction header written hours earlier.

    Grepping for the claim found all three. Two of those hits were the CORRECTIONS - so the
    output looked like thorough coverage of a handled problem, and the one live copy was
    indistinguishable from the two dead ones. **A partially corrected document reads as
    corrected**, and the more carefully it was corrected the more hits the grep returns.

    ⭐ The fix is to search what the document still ASSERTS: strike-through is a retraction
    marker, HTML comments are not rendered, fenced blocks are quoted material. Remove them, then
    search. o7 found their third copy exactly this way, by hand.

    Note this cuts the OTHER way too and that is deliberate: a claim quoted inside a fence - the
    way this docstring quotes one - is not an assertion by the document either.
    """
    # ⛔ REMOVED SPANS ARE REPLACED BY THEIR OWN NEWLINES, not deleted. Deleting them shifts
    # every later line number, so the reported L<n> pointed at the wrong line - and worse, a
    # per-line liveness test against a shifted copy misjudges MULTI-LINE strikes: the opening
    # `~~` sits on one line and the closing `~~` on another, so neither line looks struck on its
    # own. That is how a retracted claim was reported as still asserted on the first run.
    blank = lambda m: "\n" * m.group(0).count("\n")     # noqa: E731
    text = FENCE_RE.sub(blank, text)
    text = HTML_COMMENT_RE.sub(blank, text)
    return STRUCK_RE.sub(blank, text)


def folded_spans(text):
    """Line ranges inside <details> - content a reader does not see unless they expand it.

    Returned as 1-indexed (start, end) pairs. Nesting is not tracked; the first </details>
    closes, which is correct for this corpus and errs toward reporting MORE lines as folded
    rather than fewer - the safe direction for a warning.
    """
    spans, open_at = [], None
    for i, line in enumerate(text.split("\n"), 1):
        low = line.lower()
        if "<details" in low:
            open_at = i
        elif "</details>" in low and open_at:
            spans.append((open_at, i))
            open_at = None
    if open_at:                      # unclosed fold runs to the end of the document
        spans.append((open_at, len(text.split("\n"))))
    return spans


def cmd_strike(args):
    """Apply the full settled-sub-item form to every done sub-item in a LIVE entry.

    Three marks by hand, per line, is the kind of task that gets done for the first two entries
    and abandoned - which is exactly what happened: the rule existed, W-STRIKEDONE detected the
    violations 14 times a run, and nobody could act on a bare count. A rule whose compliance
    costs three edits per line needs a command, or it decays into an advisory nobody reads.

    ⛔ IT ONLY TOUCHES SUB-ITEMS THAT ARE ALREADY MARKED DONE, inside entries that are still
    LIVE. It never decides that something IS done - that is the author's call and the one thing
    a formatter must not guess at.
    """
    doc = resolve_doc_arg(args.doc)
    if not doc or not doc.exists():
        print("no such doc: %s" % args.doc, file=sys.stderr)
        return 2
    if refuse_if_not_mine(doc, "strike", getattr(args, "not_mine", False)):
        return 2

    lines = doc.read_text(encoding="utf-8").split("\n")
    entries, _ = parse_entries(lines)
    out = list(lines)
    changed, ambiguous, spans_only = [], [], []

    # ⛔ LITERAL SPANS ARE STRIPPED EVERYWHERE, not only where a line converts. I told another
    # orchestrator this command "strips the 17 <span> tags in your doc"; it did not - it only
    # touched lines inside LIVE D/T/W/A entries that it was already converting, so the tags in
    # every other entry survived and it reported `written.` regardless. An inline span renders
    # as visible literal text in this viewer whatever the line's status, so removing one asserts
    # nothing about done-ness and is safe to do unconditionally.
    for i, raw in enumerate(out):
        if GREY_SPAN_RE.search(raw):
            fixed = GREY_SPAN_RE.sub("", raw).replace("</span>", "")
            if fixed != raw:
                out[i] = fixed
                spans_only.append(i + 1)

    for e in entries:
        if e.get("archived") or status_of(e["body"]) not in LIVE_STATUS:
            continue
        if e["id"][:1] not in ("D", "T", "W", "A"):
            continue
        for off, raw in enumerate(e["body"].splitlines()[1:]):
            i = e["line"] + off                      # 0-indexed into `out`
            if i >= len(out) or out[i] != raw:
                continue
            # ⛔ NEVER A NUMBERED LIST. `2.` is document STRUCTURE, not a settled sub-item, and
            # rewriting it to `- [x]` orphans it between its own `1.` and `3.`. o1's specimen:
            # `2. ✅ **RETAINED in the stored script**` became a checkbox and broke the list it
            # belonged to. A formatter must not change what kind of thing a line IS.
            if not re.match(r"^\s*[-*+]\s", raw):
                continue
            txt = re.sub(r"`[^`]*`", "", raw)
            if NOTDONE_MARK_RE.search(txt):
                continue
            if not DONE_MARK_RE.search(NOTDONE_MARK_RE.sub(" ", txt)):
                continue
            # ⭐ AMBIGUOUS IS A THIRD STATE, AND IT IS THE HUMAN'S. A line carrying BOTH a done
            # marker and an openness signal is exactly where the tool was guessing - and it
            # guessed wrong five times out of seven on a real document. It now refuses, and
            # SAYS SO: a silent skip is the same defect as a silent conversion, one direction
            # over.
            if looks_open(txt):
                ambiguous.append((i + 1, e["id"], why_open(txt), raw))
                continue
            if CHECKED_BOX_RE.search(raw) and not GREY_SPAN_RE.search(raw):
                continue

            m = re.match(r"^(\s*)([-*+]|\d+\.)\s+(?:\[[ xX]\]\s*)?(.*)$", raw)
            if not m:
                continue
            indent, _bullet, body = m.group(1), m.group(2), m.group(3)
            # ⛔ STRIP the span rather than write one. An earlier version of this ADDED
            # `<span style="color:#8a8a8a">`, on the inference that these docs render HTML
            # because <details> folds work. Inline HTML is NOT rendered - it appears as visible
            # literal text mid-sentence - so the fixer was writing junk into every line it
            # touched. It now removes what it used to add.
            body = GREY_SPAN_RE.sub("", body).replace("</span>", "").strip()
            if body.startswith("~~") and body.endswith("~~"):
                body = body[2:-2].strip()
            # Rebuilt from parts, never patched, so a second run cannot double anything.
            new = "%s- [x] %s" % (indent, body)
            if new != raw:
                out[i] = new
                changed.append((i + 1, e["id"]))

    print("orchdoc strike - %s" % doc.name)
    print("  %d entr(ies) examined" % len(entries))
    if spans_only:
        print("  %d literal <span> tag(s) stripped (they render as visible text here): L%s"
              % (len(spans_only), ", L".join(str(n) for n in spans_only[:8])))

    # ⭐ REFUSALS ARE PRINTED FIRST AND ALWAYS. These are the lines where the tool would have
    # been GUESSING at done-ness, and on a real document it guessed wrong five times out of
    # seven. Deciding whether work is finished is judgement; the tool is the one place that must
    # never do it silently in either direction.
    if ambiguous:
        print()
        print("  ⚠️  %d line(s) REFUSED - each carries a done marker AND an openness signal, so"
              % len(ambiguous))
        print("      only their owner can say. Mark them by hand, or reword the line.")
        for ln, eid, word, raw in ambiguous[:8]:
            print("      L%-5d %-6s says %-12r %s" % (ln, eid, word, raw.strip()[:56]))
        if len(ambiguous) > 8:
            print("      ... and %d more" % (len(ambiguous) - 8))

    if not changed and not spans_only:
        print()
        print("  nothing to convert: every settled sub-item in a live entry already carries the")
        print("  checked-checkbox form.")
        return 0
    if changed:
        print()
        print("  %d sub-item(s) across %d entr(ies) get the full form"
              % (len(changed), len(set(c[1] for c in changed))))
        for ln, eid in changed[:8]:
            print("    L%-5d %-6s %s" % (ln, eid, out[ln - 1].strip()[:74]))
        if len(changed) > 8:
            print("    ... and %d more" % (len(changed) - 8))
    if args.dry_run:
        print("  DRY RUN - nothing written. Re-run with --commit.")
        return 0
    doc.write_text("\n".join(out), encoding="utf-8")
    print("  written.")
    return 0


def _reorder_slots(lines, entries):
    """(groups, spans) for the reorder - one group per (section, prefix), in document order.

    A SLOT is where a numbered entry sits: `(start, body_end, end, entry)`, all 0-indexed.
    `start..body_end` is the block that MOVES; `body_end..end` is a pinned TAIL.

    ⭐ THE TAIL IS WHY THIS IS SAFE. An entry's span runs to the next entry heading, so the
    LAST entry of a section carries the following `## §3` heading inside its own span. Moving
    that block whole would relocate a section boundary. Everything from the first level<=2
    heading onward therefore stays where it is and only the entry's own content travels.
    """
    NUM = re.compile(r"^([A-Z]{1,3})(\d+)$")
    spans = []
    for idx, e in enumerate(entries):
        start = e["line"] - 1
        end = entries[idx + 1]["line"] - 1 if idx + 1 < len(entries) else len(lines)
        # ⛔ A HEADING INSIDE A CODE FENCE IS NOT A SECTION BOUNDARY. Scanning for `^#{1,2}\s`
        # without tracking fences cut an entry's block IN HALF at a `##` line that was sample
        # text - the opening ``` travelled with the moving block while the closing ``` stayed
        # in the pinned tail, so fence parity flipped and every entry BELOW read as code.
        #
        # ⭐ MEASURED 2026-09-08 on this very file: D15 quotes `## Decisions for the human (D14,
        # D15 - ...)` inside a fence - the anchor convention the human ruled the same day. reorder
        # then produced 166 parsed entries where there were 167, D16 having vanished into the
        # broken fence. Its own set-equality check caught that and REFUSED, so nothing was
        # lost; but E-IDORDER then had no reachable remedy and the doc could only land with an
        # override.
        #
        # ⚠️ THE THIRD TIME THIS WORKSPACE HAS LEARNED THIS. route.py does not count a receipt
        # shown inside a fence, and dev_docs_index.py does not read a `dev-doc:` marker inside
        # one - both say so in their own comments. The rule keeps being rediscovered per file
        # because it is a property of MARKDOWN, not of any one parser.
        body_end = end
        fenced = False
        for j in range(start + 1, end):
            if lines[j].lstrip().startswith("```"):
                fenced = not fenced
                continue
            if not fenced and re.match(r"^#{1,2}\s", lines[j]):
                body_end = j
                break
        spans.append((start, body_end, end, e))

    groups = {}
    for s, be, en, e in spans:
        m = NUM.match(e["id"])
        if not m:
            continue                      # a non-canonical id (E-IDSHAPE) is left exactly alone
        groups.setdefault((e.get("section") or "?", m.group(1)), []).append(
            (s, be, en, e, int(m.group(2))))
    return groups, spans


def cmd_reorder(args):
    """Sort entry numbers within each section. Moves whole entries; verifies nothing changed.

    ⛔ IT PERMUTES A SECTION'S ENTRIES AMONG THEIR OWN SLOTS - it does not compact them.
    Everything that is not a numbered entry of that prefix stays byte-for-byte where it is:
    prose paragraphs, section headings, and entries of any OTHER prefix. So in a section
    holding `D3 D4 D22 W1 W3 D21`, the D blocks are redealt into the D positions and the W
    blocks never move.

    ⭐ THE EARLIER VERSION ONLY SORTED CONTIGUOUS SAME-PREFIX RUNS, and then reported
    *"every section already runs in order"* - a claim about the whole document made from a
    measurement of part of it. On o7 it printed that sentence while `check` reported
    E-IDORDER on the same file, because D21 and D22 had three W entries between them. **Two
    readers of one invariant, disagreeing, and the one that could not see the defect was the
    one being believed.** The ordering predicate is now measured here, identically to
    `check`, and any inversion this command cannot fix is NAMED rather than absorbed into a
    clean-sounding summary.

    ⭐ VERIFICATION IS SET EQUALITY ON THE BLOCKS THEMSELVES, not a line count. A reorder that
    drops or duplicates an entry would keep the line count identical in most cases, which is
    exactly the check that would pass while the damage happened.
    """
    doc = resolve_doc_arg(args.doc)
    if not doc or not doc.exists():
        print("no such doc: %s" % args.doc, file=sys.stderr)
        return 2
    if refuse_if_not_mine(doc, "reorder", getattr(args, "not_mine", False)):
        return 2

    lines = doc.read_text(encoding="utf-8").split("\n")
    entries, _ = parse_entries(lines)
    groups, spans = _reorder_slots(lines, entries)

    def _inversions(gs):
        n = 0
        for members in gs.values():
            seq = [m[4] for m in sorted(members)]
            n += sum(1 for a, b in zip(seq, seq[1:]) if b < a)
        return n

    before_inv = _inversions(groups)

    moved, plans, touched = 0, [], 0
    for key, members in sorted(groups.items()):
        members = sorted(members)                      # document order
        if len(members) < 2:
            continue
        blocks = [(m[4], lines[m[0]:m[1]], m[3]["id"]) for m in members]
        ordered = sorted(blocks, key=lambda b: b[0])
        if [b[2] for b in ordered] == [b[2] for b in blocks]:
            continue
        touched += 1
        moved += sum(1 for a, b in zip(blocks, ordered) if a[2] != b[2])
        for m, blk in zip(members, ordered):
            plans.append((m[0], m[1], blk[1]))         # slot start, slot body_end, new block
        if args.dry_run:
            print("  %-34s %s" % (str(key[0])[:34], " ".join(b[2] for b in blocks)))
            print("  %-34s -> %s" % ("", " ".join(b[2] for b in ordered)))

    out = list(lines)
    for start, body_end, blk in sorted(plans, key=lambda p: -p[0]):   # bottom-up
        out[start:body_end] = blk

    if not moved:
        print("orchdoc reorder - %s" % doc.name)
        print("  nothing to permute. %d entr(ies) examined, %d section/prefix group(s)."
              % (len(entries), len(groups)))
        # ⛔ NEVER SAY "already in order" WITHOUT MEASURING IT. That sentence was the bug.
        if before_inv:
            print("  ⚠️ %d out-of-order step(s) REMAIN and this command cannot fix them:"
                  % before_inv)
            for key, members in sorted(groups.items()):
                seq = [(m[4], m[3]["id"]) for m in sorted(members)]
                for (na, ia), (nb, ib) in zip(seq, seq[1:]):
                    if nb < na:
                        print("     %-30s %s after %s" % (str(key[0])[:30], ib, ia))
            print("  check --doc %s reports these as E-IDORDER." % doc.name)
            return 2
        print("  every section/prefix group runs in ascending order (measured).")
        return 0

    # ⭐ the block multiset must be identical - same entries, same bytes, nothing lost or doubled
    before_blocks = sorted("\n".join(lines[s:be]) for s, be, _en, _e in spans)
    new_entries, _ = parse_entries(out)
    _new_groups, new_spans = _reorder_slots(out, new_entries)
    after_blocks = sorted("\n".join(out[s:be]) for s, be, _en, _e in new_spans)
    if before_blocks != after_blocks or len(out) != len(lines):
        print("  ⛔ REFUSING - the entry blocks are not identical after the sort.")
        print("     %d before, %d after; %d lines -> %d. Nothing written."
              % (len(before_blocks), len(after_blocks), len(lines), len(out)))
        return 2
    after_inv = _inversions(_new_groups)
    if after_inv:
        # The objective, restated as an oracle. A sort that leaves inversions has not done
        # the job it was called to do, and must not report success.
        print("  ⛔ REFUSING - %d out-of-order step(s) would REMAIN after the sort."
              % after_inv)
        return 2

    print("orchdoc reorder - %s" % doc.name)
    print("  %d entr(ies) would move, across %d section/prefix group(s)" % (moved, touched))
    print("  verified: %d entry blocks byte-identical, %d -> %d out-of-order step(s)"
          % (len(before_blocks), before_inv, after_inv))
    if args.dry_run:
        print("  DRY RUN - nothing written. Re-run with --commit.")
        return 0
    doc.write_text("\n".join(out), encoding="utf-8")
    print("  written.")
    return 0


def cmd_asserted(args):
    """Search what a doc still CLAIMS, not what it merely mentions.

    Reports what it EXAMINED - docs scanned, spans removed - because "0 live copies" and
    "the search never ran" must not print the same way.
    """
    try:
        pat = re.compile(args.pattern, re.I)
    except re.error as e:
        # A broken pattern is not "no matches". Six times in this corpus a failed search has
        # rendered as a clean one.
        print("  BAD PATTERN - this is NOT a clean result: %s" % e, file=sys.stderr)
        return 2

    docs = ([resolve_doc_arg(args.doc)] if args.doc
            else sorted(Path(".").glob("ORCHESTRATOR-DECISIONS-o*.md"))
            + sorted(Path(".").glob("*bridge*.md")))
    docs = [d for d in docs if d and d.exists()]
    if not docs:
        print("  no docs matched - nothing was examined", file=sys.stderr)
        return 2

    print("orchdoc asserted - /%s/" % args.pattern)
    print("  examined %d doc(s)" % len(docs))
    print()
    live_total = retracted_total = 0
    for d in docs:
        raw = d.read_text(encoding="utf-8", errors="replace")
        live = still_asserted(raw)
        n_raw = len(pat.findall(raw))
        n_live = len(pat.findall(live))
        retracted = n_raw - n_live
        live_total += n_live
        retracted_total += retracted
        if not n_raw:
            print("  %-34s -" % d.name)
            continue
        flag = "  <- STILL ASSERTED" if n_live else ""
        print("  %-34s live=%-3d retracted/quoted=%-3d%s" % (d.name, n_live, retracted, flag))
        if n_live:
            folded = folded_spans(raw)
            # Line numbers are preserved by still_asserted(), so this indexes the SAME lines as
            # the raw file - a struck span shows up here as blanked, which is the liveness test.
            live_lines = live.split("\n")
            for i, line in enumerate(raw.split("\n"), 1):
                if not pat.search(live_lines[i - 1] if i <= len(live_lines) else ""):
                    continue
                # ⛔ A claim inside a COLLAPSED <details> is invisible to a human reading the
                # rendered page and fully live to anyone citing the file. It is the exact
                # inverse of strike-through - struck text is visible but retracted; folded text
                # is retracted from view but still asserted.
                #
                # This is not hypothetical: the fourth copy of the purchase claim survived TWO
                # deliberate correction passes, by two sessions, because it sits inside a fold.
                # Both passes were reading the doc.
                note = ""
                if any(a <= i <= b for a, b in folded):
                    note = " ⚠️ INSIDE A COLLAPSED <details> - invisible when read"
                elif line.lstrip().startswith(">"):
                    # ⛔ A BLOCKQUOTE IS AMBIGUOUS, AND STRIPPING IT IS THE DANGEROUS FIX.
                    # In this corpus `>` carries two incompatible meanings:
                    #   * a QUOTATION of another orchestrator - not this doc's claim at all
                    #   * an emoji-led CALLOUT BANNER - this doc's claim in its strongest voice
                    #     (2 in o9, 3 in o7, counted)
                    # Treating `>` like a fenced quote would silence every load-bearing banner,
                    # trading one false positive for false negatives on the lines most worth
                    # checking. So it is FLAGGED, exactly like a fold: the tool reports the
                    # ambiguity, the reader resolves it.
                    note = " ⚠️ IN A BLOCKQUOTE - quotation or callout? read before acting"
                print("       L%-5d %s%s" % (i, line.strip()[:88], note))

    print()
    print("  %d live claim(s); %d already retracted or quoted" % (live_total, retracted_total))
    print("  A plain grep would have reported %d and made no distinction." %
          (live_total + retracted_total))
    return 1 if live_total else 0


def cmd_links(args):
    """Harvest the doc's own assets and propose a §1 table. PROPOSES; does not overwrite."""
    doc = resolve_doc_arg(args.doc)
    if not doc or not doc.exists():
        print("no such doc: %s" % args.doc, file=sys.stderr)
        return 2
    lines = doc.read_text(encoding="utf-8").split("\n")
    assets = harvest_assets(lines, doc)

    span = section_span(lines, "1")
    listed = "\n".join(lines[span[0]:span[1]]) if span else ""
    missing = {k: v for k, v in assets.items() if k not in listed}

    print("orchdoc links - %s" % doc.name)
    print("  %d asset(s) cited in the doc, %d NOT listed in §1"
          % (len(assets), len(missing)))
    if not missing:
        print("  §1 already accounts for everything this doc points at.")
        return 0

    dead = [k for k, v in missing.items() if v["kind"] == "path" and v["exists"] is False]
    for k, v in sorted(missing.items()):
        mark = "  " if v["exists"] is not False else "  \u26a0 DOES NOT EXIST"
        print("    %-62s %s%s" % (k[:62], v["kind"], mark))
    if dead:
        print()
        print("  \u26d4 %d cited path(s) DO NOT EXIST. Those are dead pointers TODAY, and"
              % len(dead))
        print("     listing them in §1 would publish them as navigable. Fix or drop them.")

    print()
    print("  Paste-ready §1 rows (yours to edit - a harvested link is a CANDIDATE, not")
    print("  necessarily an asset you own):")
    print()
    print("| what | where |")
    print("|---|---|")
    for k, v in sorted(missing.items()):
        if v["exists"] is False:
            continue
        label = k.rstrip("/").split("/")[-1].split("\\")[-1] or k
        cell = k if v["kind"] == "url" else "[%s](%s)" % (label, k)
        print("| %s | %s |" % (label[:46], cell))
    return 0





def cmd_review(args):
    """Walk every schema section and state what it holds, what it should, and the question.

    Exit 1 when any section has an unanswered question, so it can gate a report.
    """
    doc = resolve_doc_arg(args.doc)
    if not doc or not doc.exists():
        print("no such doc: %s" % args.doc, file=sys.stderr)
        return 2
    lines = doc.read_text(encoding="utf-8").split("\n")
    entries, _sections = parse_entries(lines)
    have_ids = {e["id"] for e in entries}

    # A ghost-id scan lived here and was DELETED, not fixed. Two rounds, both all-false: 15/15
    # in one doc, then 5/5 in this one after three separate bugs were repaired. The check
    # cannot separate USING an id from WRITING PROSE ABOUT one, and no pattern can, because the
    # difference is authorial intent and the two are textually identical. A doc whose subject
    # matter includes ids trips it forever.
    #
    # Every TRUE positive it ever produced was already reported by the section measure below -
    # "29 lines, 0 entries" is unambiguous and has no false positives on any of the eight docs.
    # A check whose true findings are all covered by a cleaner one contributes only its false
    # ones, and those bury the real finding shipped beside them.

    def body_of(num):
        sp = section_span(lines, num)
        if not sp:
            return None
        return [l for l in lines[sp[0] + 1:sp[1]]
                if l.strip() and not re.fullmatch(r"_.*_", l.strip())]

    print("orchdoc review - %s" % doc.name)
    print("  One section at a time. The script gathers; YOU answer. Nothing is auto-filled.")
    print()

    open_h = [e for e in entries if status_of(e["body"]) in PLATE_STATUS
              and not e.get("archived")]
    unanswered = 0

    for num, title, promise in schema_sections():
        body = body_of(num)
        if body is None:
            continue
        n = len(body)
        ids_here = [e["id"] for e in entries
                    if SECTION_RE.match(e["section"] if e["section"].startswith("#")
                                        else "## " + e["section"])
                    and SECTION_RE.match(e["section"] if e["section"].startswith("#")
                                         else "## " + e["section"]).group(1) == num]
        print("\u00a7%-5s %-26s %2d line(s), %d entr(ies)" % (num, title, n, len(ids_here)))
        print("       promises: %s" % promise)

        q = None
        if num == "1" and n == 0:
            q = "run `links --doc %s` - it harvests what this doc already cites" % args.doc
        elif num in ("2.1", "2.2", "2.3") and not ids_here:
            # "empty" and "full of text the parser cannot see" are OPPOSITE problems and
            # calling the second one empty is a false statement to the reader's face.
            if n:
                q = ("%d line(s), 0 parseable entries - so NONE of this reaches the generated"
                     " plate and no invariant runs on it. It is on the human's plate in prose"
                     " and invisible to every check. Give each item an `### <ID> - title`"
                     " heading." % n)
            else:
                q = ("EMPTY. Is that TRUE? An empty plate asserts nothing needs the human. "
                     "If anything does, it belongs here as an entry - not in prose.")
        elif num == "3" and not ids_here and n:
            q = ("has %d line(s) but NO entries - work described in prose or a table is "
                 "invisible to every check. Give each item an `### <ID> - title` heading."
                 % n)
        elif num == "4" and not ids_here:
            q = ("%d line(s) but 0 parseable entries."
                 % n) if n else "EMPTY. Did this workstream learn nothing worth keeping?"
            if n:
                q += (" Your findings are THERE and the tool cannot see any of them - so no"
                      " invariant runs on them. Give each an `### <ID> - title` heading.")
        elif num == "5" and n <= 1:
            q = "no guards. What will this orchestrator refuse to do?"
        elif num.startswith("99") and not ids_here and open_h:
            q = ("nothing archived while %d item(s) are open above. If any are finished, "
                 "`archive` moves them; if none are, say so." % len(open_h))
        if q:
            print("       \u26a0 %s" % q)
            unanswered += 1
        print()

    print("  open items the tool can SEE: %d" % len(open_h))
    print("  sections needing an answer : %d" % unanswered)
    if unanswered:
        print()
        print("  \u26a0 A section is not complete because it is quiet. Every invariant here")
        print("     is triggered BY AN ENTRY, so an EMPTY section generates NO findings -")
        print("     the emptier a doc gets, the quieter the tool gets.")
    return 1 if unanswered else 0


def refresh_subtitles(lines, name):
    """Rewrite each section's one-line subtitle from SCHEMA_SECTIONS.

    ⭐ It used to be WRITE-ONCE: `scaffold` regenerated the index from the schema and left the
    subtitle under each heading frozen as first authored. So a schema edit fixed the index and
    left the body saying something else - one string, two copies, one derived and one not,
    which is the multi-copy defect this whole tool exists to remove, inside its own generator.
    the human found it by reading an index and a section that disagreed in the same document.

    Only replaces a line that is ALREADY a subtitle (`_..._` immediately under the heading).
    It never invents one and never touches prose, so a doc that writes its own intro keeps it.
    """
    want = {num: desc.replace("{NAME}", name) for num, _t, desc in schema_sections()}
    out, i = list(lines), 0
    while i < len(out) - 2:
        m = SECTION_RE.match(out[i])
        if m and m.group(1) in want:
            for k in (i + 1, i + 2):
                if k < len(out) and re.fullmatch(r"_.*_", out[k].strip()):
                    out[k] = "_%s_" % want[m.group(1)]
                    break
        i += 1
    return out



def running_orchestrator():
    """Which orchestrator is running, or None if this machine has not said.

    Configured, never guessed. $ORCHDOC_ME wins; a `.orchdoc-me` file beside the docs is the
    persistent form. Returning None disables the ownership guard entirely, which is the point:
    a guard that fires on a machine that never opted in would be switched off within the hour,
    and a guard that gets switched off protects nothing.
    """
    v = (os.environ.get("ORCHDOC_ME") or "").strip()
    # NOT from the shared config: `me` is session-scoped, and every orchestrator reads the
    # same workspace file. A value there would tell all of them they are the same one.
    if not v:
        f = PROJECTS / ".orchdoc-me"
        try:
            v = f.read_text(encoding="utf-8").strip() if f.exists() else ""
        except OSError:
            v = ""
    m = re.fullmatch(r"o\d+", v or "")
    if m:
        return m.group(0)
    # Nothing configured - which is EVERY real session, measured 2026-09-04. That returned None
    # here, which switched the ownership guard off for the whole fleet: a session writing
    # another orchestrator's doc without --not-mine was never refused, because the guard had no
    # idea who was asking. The binding is the answer that was missing. It is still not a guess:
    # the session earned it by writing that doc as its own.
    return _bound_orchestrator()


APP_SESSIONS = Path(r"<your-home>\AppData\Roaming\Claude\claude-code-sessions")
# ONE ORCHESTRATOR ID AT THE START OF THE SESSION NAME. Two rules, and each is doing work:
#
#   [^0-9A-Za-z]*   ANY run of non-alphanumeric characters may precede the id. the human prefixes
#                   names with - and . today and said plainly there may be others later, so an
#                   ALLOWLIST of punctuation is the wrong shape - a character he has not used
#                   yet fails SILENTLY, and a silent failure here is indistinguishable from a
#                   session that simply is not an orchestrator. Measured 2026-09-04: the
#                   allowlist version parsed today's six and missed 9 of 23 plausible future
#                   prefixes (+ # > ~ ! @ parentheses, a non-star emoji, and a bare id).
#
#   (?:[:...]|\s|$) the id must be FOLLOWED by a separator or end. This is what keeps a LANE
#                   out: "o1l2:Lifetime founder-ladder pricing" has l2 after the o1, so it does
#                   not match, and a lane is not an orchestrator.
#
# Battery: 23 titles that must match, 10 that must not (lanes, "Order 66 execution plan",
# "optimise 3 things", ordinary session names). 23/23 and 0 false hits.
#
# \u26d4 THIS REGEX IS DUPLICATED in orchdoc.py and orchdoc_stop_check.py - the hook must stay
# standalone rather than import an 8000-line module on every Stop. test_orchdoc_stop_hook.py
# asserts the two are byte-identical, because a matcher that two tools read is a contract, and
# a contract kept in two places drifts.
TITLE_ID = re.compile(r"^[^0-9A-Za-z]*(o\d+)\s*(?:[:\-\u2012-\u2015\u2022|/,]|\s|$)", re.I)


def _bound_orchestrator():
    """Which orchestrator this session IS, read from its NAME. Static; nothing is learned.

    ⭐ THE HUMAN, 2026-09-04: *"If a session is named o1, then it should be bound to
    ORCHESTRATOR-DECISIONS-o1... why not permanently just bind it to THAT document? Why make it
    dynamic at all?"* The earlier version learned the id from the first OrchDoc a session wrote,
    which meant a write could relabel a session - measured, it silently rebound o9 to o1. A name
    cannot be relabelled by a write.

    Measured over the app's store: 1234 titled sessions, exactly 10 parse to an orchestrator id,
    all 10 real, zero false positives - and the lane "o1l2:..." correctly does not match, since
    the id must be followed by a separator.
    """
    sid = ""
    for key in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID", "CLAUDE_CODE_HOST_SESSION_ID"):
        val = os.environ.get(key)
        if val:
            sid = "".join(c for c in val if c.isalnum() or c in "-_")[:80]
            break
    if not sid:
        return None
    want = sid.replace("local_", "")
    try:
        for f in APP_SESSIONS.rglob("local_*.json"):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            if want not in (str(d.get("cliSessionId") or ""),
                            str(d.get("sessionId") or "").replace("local_", "")):
                continue
            m = TITLE_ID.match((d.get("title") or "").strip())
            return m.group(1).lower() if m else None
    except Exception:
        pass
    return None


def doc_owner_id(doc):
    """The orchestrator id a doc's FILENAME declares, or None."""
    m = re.search(r"ORCHESTRATOR-DECISIONS-(o\d+)\.md$", str(doc), re.I)
    return m.group(1).lower() if m else None


def refuse_if_not_mine(doc, verb, allowed):
    """True when the caller must stop. Prints the refusal and the exact way through.

    Fires only when BOTH ids are known and they differ - never on an unconfigured machine and
    never on a file whose name does not declare an owner. A guard that guesses is a guard that
    gets overridden.
    """
    if allowed:
        return False
    me, owner = running_orchestrator(), doc_owner_id(doc)
    if not me or not owner or me == owner:
        return False
    print("  [REFUSE] %s WRITES, and %s belongs to %s, not %s."
          % (verb, doc.name, owner, me))
    print("           Nothing was written.")
    print()
    print("           An OrchDoc is a live document its owner is writing to right now. A")
    print("           regenerated index or a rewritten header lands in THEIR file while they")
    print("           work, and they will not know it came from here.")
    print()
    print("           If %s has agreed to this, pass --not-mine. The flag is one word; it" % owner)
    print("           exists so a deliberate cross-doc edit is distinguishable from an")
    print("           accidental one afterwards, in the shell history.")
    return True


def cmd_scaffold(args):
    """Write, or repair, the canonical spine.

    NON-DESTRUCTIVE by construction: an existing section keeps every line under it, only
    absent sections are created, and only the generated block is replaced. A scaffolder
    that reorganised a live doc would be the unilateral rewrite the charter forbids -
    and it cannot know which entry belongs under which heading. That judgement stays with
    the owner; the tool supplies the shape and says what is still missing.
    """
    doc = resolve_doc_arg(args.doc)
    if not doc:
        print("no such doc: %s" % args.doc, file=sys.stderr)
        return 2
    if refuse_if_not_mine(doc, "scaffold", getattr(args, "not_mine", False)):
        return 1
    lines = doc.read_text(encoding="utf-8").split("\n") if doc.exists() else ["# " + doc.stem]

    # The charter forbids rewriting a live OrchDoc unilaterally: propose, agree, then
    # migrate. --adopt rewrites headings, so it REFUSES to write without --force.
    #
    # An earlier draft tried to auto-detect "is this my own doc?" and skip the gate for
    # the owner. There is no reliable signal for it - whoami() returns SESSION identity,
    # not doc ownership - so that check would have been a guess, and a guess that
    # authorises rewriting someone else's decision record is the worst available place
    # to put one. An explicit flag is honest about who is deciding.
    if (getattr(args, "adopt", False) or getattr(args, "reorder", False)) \
            and not args.dry_run and not args.force:
        print("REFUSING: --adopt rewrites section headings in %s." % doc.name,
              file=sys.stderr)
        print("Run it with --dry-run first, show the owner the mapping, and pass --force",
              file=sys.stderr)
        print("once they agree. A decision record is not migrated behind its author.",
              file=sys.stderr)
        return 2

    # ---- TITLE: the identity line, repaired in place, role preserved ----
    tnum = re.search(r"ORCHESTRATOR-DECISIONS-(o\d+)", doc.name)
    if tnum:
        tnum = tnum.group(1)
        hi = next((i for i, l in enumerate(lines[:40]) if l.startswith("# ")), None)
        if hi is None:
            lines.insert(0, canonical_title(tnum))
        else:
            # THE TITLE IS A TWO-LINE UNIT, so it must be read as one. the human's format puts
            # the role on the line AFTER the <br>, and a first version of this read only
            # the H1: on a second run it found nothing after the <br>, concluded there
            # was no role, and rewrote the title without it. The doc kept the role line
            # as orphaned prose and lost the line break - a rewrite that was wrong only
            # the second time it ran, which is the kind of defect a single test never sees.
            tm = TITLE_RE.match(lines[hi])
            # Keep whatever the author wrote after the identity - that is their role
            # description and the tool has no business naming what they are for.
            role = (tm.group(2) if tm else "").strip()
            role = re.sub(r"^<br\s*/?>\s*", "", role, flags=re.I).strip()

            _ts, _te = title_span(lines)
            if not role and _te > hi:
                # A legacy SECOND H1 line, "# (the role)". Fold it back into the single
                # heading; title_span still spans it so the replacement below removes it.
                role = lines[_te].lstrip("#").strip()

            # REPAIR the earlier paragraph form, whose "(role)" got stranded after the
            # generated index. Absorb it and delete the orphan rather than leaving a human
            # to hunt a stray line through eight documents - a manual migration step is
            # one that does not happen.
            if not role:
                _isp, _ = index_span(lines)
                _fsp, _ = findex_span(lines)
                _msp, _ = marker_span(lines, META_BEGIN_TOKEN, META_END_TOKEN, "meta")
                for j in range(_te + 1, min(len(lines), _te + 200)):
                    if any(sp and sp[0] <= j <= sp[1] for sp in (_isp, _fsp, _msp)):
                        continue
                    t = lines[j].strip()
                    if not t or t.startswith("<!--"):
                        continue
                    if t.startswith("(") and t.endswith(")") and len(t) < 120:
                        role = t
                        del lines[j]
                        break
                    if t.startswith("#") or t.startswith("**") or t.startswith("- ") \
                            or t.startswith(">") or t.startswith("|"):
                        break   # real content reached; there is no orphan to absorb

            fixed = canonical_title(tnum, role)
            _ts, _te = title_span(lines)
            if "\n".join(lines[_ts:_te + 1]) != fixed:
                print("title: %s" % fixed.replace("\n", " / "))
                lines[_ts:_te + 1] = fixed.split("\n")

    # ---- ADOPT: rename existing headings into their schema slot, in place ----
    renames, unmatched = [], []
    if getattr(args, "adopt", False):
        # SEED `taken` with the sections ALREADY numbered in the document. Tracking only
        # this run's renames made --adopt non-idempotent: a second pass re-mapped a
        # different heading onto a number the first pass had already assigned, producing
        # two sections with the same number and an index that pointed at one of them.
        # The collision rule was right; its scope was wrong.
        taken = set(m.group(1) for m in
                    (SECTION_RE.match(l) for l in lines) if m)
        for i, ln in enumerate(lines):
            if not ln.startswith("## ") or SECTION_RE.match(ln):
                continue
            num = adopt_number(ln)
            if num is None:
                unmatched.append((i, ln.lstrip("#").strip()[:70]))
                continue
            if num in taken:
                # Two headings claiming one slot. Renaming both would silently merge
                # sections that their author kept apart; the second is left for a human.
                unmatched.append((i, ln.lstrip("#").strip()[:70] + "  (\u00a7%s taken)" % num))
                continue
            taken.add(num)
            title = dict((n, t) for n, t, _d in schema_sections())[num]
            depth = "##"   # flat, so every section stays independently movable
            orig = ln.lstrip("#").strip()
            renames.append((num, orig[:64]))
            # The original wording is KEPT as a trailing note: it carries the author's
            # scoping ("ACTIVE only", a date, a lane name) that the schema title drops.
            # Drop the original wording when it merely repeats the schema title -
            # "\u00a72 LIVE ON THE HUMAN'S PLATE - ON THE HUMAN'S PLATE" is noise. Keep it whenever it
            # carries scoping the schema title loses: "(ACTIVE only)", a date, a lane name.
            keep = orig.strip().strip("*_").upper()
            same = keep in title.upper() or title.upper() in keep
            lines[i] = ("%s \u00a7%s %s" % (depth, num, title) if same
                        else "%s \u00a7%s %s - %s" % (depth, num, title, orig))

    present = set()
    for ln in lines:
        m = SECTION_RE.match(ln)
        if m:
            present.add(m.group(1))

    added = []
    for num, title, note in schema_sections():
        if num in present:
            continue
        # ALL schema sections are top-level "##". The section NUMBER carries the
        # hierarchy and the generated index renders the nesting, so heading depth adds
        # nothing - while demoting 2.1 to "###" made it a CHILD of whichever block
        # preceded it, putting it out of reach of --reorder and leaving the spine
        # permanently unsortable. Flat headings keep every section independently movable.
        lines += ["", "## \u00a7%s %s" % (num, title), "", "_%s_" % note]
        added.append(num)

    # ---- REORDER: numeric order, refused outright if a single line would move out ----
    reordered = False
    if getattr(args, "reorder", False):
        try:
            new_lines = reorder_sections(lines)
        except ValueError as e:
            print("REFUSING: %s" % e, file=sys.stderr)
            return 2
        reordered = new_lines != lines
        lines = new_lines

    # ---- PURPOSE (authored) + META (generated), between the title and the index ----
    _, hi_t = title_span(lines)          # END of the title block, not its first line
    hi_t = 0 if hi_t is None else hi_t
    if not any(re.match(r"^##\s+\**Purpose", l, re.I) for l in lines[:60]):
        # Created EMPTY on purpose. A generated purpose statement would be a rubber stamp
        # in exactly the sense E-RUBBERSTAMP rejects - text that satisfies a checker while
        # carrying no thought. Only the orchestrator can say what it is for.
        lines = (lines[:hi_t + 1]
                 + ["", "## Purpose", "",
                    "_TODO: one paragraph - what this orchestrator is for. "
                    "Authored, never generated._"]
                 + lines[hi_t + 1:])

    mspan, merr = marker_span(lines, META_BEGIN_TOKEN, META_END_TOKEN, "meta")
    if merr:
        print("REFUSING: %s" % merr, file=sys.stderr)
        return 2
    meta = render_meta(doc, lines)
    if mspan:
        lines = lines[:mspan[0]] + meta + lines[mspan[1] + 1:]
    else:
        pi = next((i for i, l in enumerate(lines[:80])
                   if re.match(r"^##\s+\**Purpose", l, re.I)), hi_t)
        nx = next((j for j in range(pi + 1, min(len(lines), pi + 40))
                   if lines[j].startswith("## ") or lines[j].startswith("<!--")), pi + 1)
        lines = lines[:nx] + meta + [""] + lines[nx:]

    span, err = index_span(lines)
    if err:
        print("REFUSING: %s" % err, file=sys.stderr)
        return 2
    idx = render_index(lines)
    if span:
        lines = lines[:span[0]] + idx + lines[span[1] + 1:]
    else:
        _, _te = title_span(lines)       # after the WHOLE title, never mid-heading
        at = 1 if _te is None else _te + 1
        lines = lines[:at] + [""] + idx + lines[at:]

    # ---- the FINDINGS index, at the head of section 4 where its reader stands ----
    fspan, ferr = findex_span(lines)
    if ferr:
        print("REFUSING: %s" % ferr, file=sys.stderr)
        return 2
    fidx = render_findings_index(lines)
    if fspan:
        lines = lines[:fspan[0]] + fidx + lines[fspan[1] + 1:]
    elif fidx:
        h = next((i for i, l in enumerate(lines)
                  if l.startswith("## ") and re.match(r"^##\s*\u00a74\b", l)), None)
        if h is not None:
            lines = lines[:h + 1] + [""] + fidx + lines[h + 1:]

    nfind = sum(1 for l in fidx if re.match(r"^- `F\d+`", l))
    if renames or unmatched:
        print("adopt pass:")
        for num, orig in renames:
            print("  \u00a7%-4s <- %s" % (num, orig))
        for _i, orig in unmatched:
            print("  %-5s    %s" % ("?", orig))
        if unmatched:
            print("  (%d heading(s) NOT renamed - no confident mapping. Left untouched"
                  % len(unmatched))
            print("   on purpose: a wrong rename hides an entry from the human while looking")
            print("   tidier than before.)")
        print()
    if args.dry_run:
        print("would add %d section(s): %s" % (len(added), ", ".join(added) or "none"))
        print("would regenerate the index (%d findings listed)" % nfind)
        return 0

    # The subtitle under each heading is DERIVED, like the index above it. It used to be
    # write-once, so a schema edit updated the index and left the body saying something else -
    # one string in two copies, one generated and one frozen, which is the exact multi-copy
    # defect this tool exists to remove. the human found it by reading an index and a section that
    # contradicted each other inside the same document.
    lines = refresh_subtitles(lines, human_name())

    write_doc(doc, "\n".join(lines))
    print("scaffold: %s" % doc.name)
    if reordered:
        print("  sections REORDERED into numeric order (content verified identical)")
    print("  added %d section(s): %s" % (len(added), ", ".join(added) or "none"))
    print("  index regenerated, %d findings listed" % nfind)
    if added and not reordered:
        print()
        print("  Sections were APPENDED, not sorted. Moving entries under them is yours:")
        print("  only you know which entry belongs where, and a tool that guessed would")
        print("  be rewriting your doc. Then: orchdoc.py check --doc %s" % args.doc)
    return 0


def cmd_plate(args):
    """
    REGENERATE the the human-facing index from the entries.

    This is the command that kills the largest failure class. Status lived in three to
    five hand-maintained places per doc (o8's DA3 status appeared in FIVE), and nothing
    reconciled them. o6 put it exactly: the "None open" failure is usually not a failure
    to record, it is a failure to record in ALL the redundant restatements.

    A hand-maintained index IS a second copy of the truth. So the index is derived, and
    a siloed update becomes impossible because there is only one thing to write.
    """
    doc = resolve_doc_arg(args.doc)
    if not doc or not doc.exists():
        print("no such doc: %s" % doc, file=sys.stderr)
        return 2
    if refuse_if_not_mine(doc, "plate", getattr(args, "not_mine", False)):
        return 1
    with _lock(doc):
        text = doc.read_text(encoding="utf-8")
        lines = text.splitlines()
        entries, _ = parse_entries(lines)
        block = build_plate_block(entries, lines)
        # Count from the SAME rows the block renders. The old predicate
        # matched the pre-grouping format and silently reported "0 open"
        # while the block itself said 1 - a wrong number from the tool
        # built to stop wrong numbers, cosmetic or not.
        rows = [b for b in block if b.startswith("| **[")]

        span, why = plate_span(lines)
        if why:
            print("  [REFUSE] %s - fix the markers before regenerating." % why,
                  file=sys.stderr)
            return 1
        if span:
            start, stop = span

            # ⛔ REFUSE TO REPLACE A POPULATED INDEX WITH AN EMPTY ONE.
            #
            # o1 and o8 both predicted this independently when asked to approve a
            # migration, and both were right: their entries are not in a form `plate` can
            # parse - o1 writes decisions as BULLETS under a DECISIONS heading, o8's
            # entries carry no Status field - so regeneration would have produced an EMPTY
            # index and overwritten a hand-curated one that listed real open items.
            #
            # ⛔ That is "None open while items are open" - the single failure this whole
            # workstream was commissioned to prevent - committed BY the tool built to
            # prevent it. A generated index is only better than a curated one once the
            # entries can actually support it, and the tool must verify that rather than
            # assume it.
            #
            # ⭐ The general rule: A GENERATOR MAY NOT DESTROY MORE INFORMATION THAN IT
            # PRODUCES. Silence is a claim, and an empty list asserts "nothing here".
            old_rows = [l for l in lines[start:stop + 1] if l.startswith("| **[")]
            if old_rows and not rows and not getattr(args, "force", False):
                print("  [REFUSE] the generated index would be EMPTY, replacing a "
                      "hand-maintained one that lists %d item(s)." % len(old_rows),
                      file=sys.stderr)
                print("           Nothing was written. This doc's entries are not yet in "
                      "a form `plate` can read:", file=sys.stderr)
                print("           entries need a `### <ID> - title` heading AND a "
                      "`**Status:**` field.", file=sys.stderr)
                print("           An index that is honestly stale beats one that is "
                      "confidently wrong.", file=sys.stderr)
                print("           Re-run with --force only if the empty index is correct.",
                      file=sys.stderr)
                return 1

            out = lines[:start] + block + lines[stop + 1:]
        else:
            anchor = 0
            for i, ln in enumerate(lines):
                if re.match(r"^#{1,2}\s", ln) and i > 0:
                    anchor = i
                    break
            out = lines[:anchor] + block + [""] + lines[anchor:]
            print("[NOTE] no plate markers found; inserted a generated block before line %d"
                  % (anchor + 1))

        if getattr(args, "dry_run", False):
            # Nothing is written. o8 ran this verb as a QUERY - "does DA4 appear in the
            # index?" - and it silently edited a live document they had said they were not
            # adopting yet. `check` and `review` are read-only; this one is not, and the name
            # reads like a noun. A read-only path makes that mistake IMPOSSIBLE rather than
            # merely detectable, and impossible is what you want from a tired reader.
            print("[PLATE] DRY RUN - nothing written. %s would hold %d open item(s):"
                  % (doc.name, len(rows)))
            for r in rows:
                print("        %s" % r[:74])
            return 0

        write_doc(doc, "\n".join(out) + "\n")

    print("[PLATE] %s regenerated from entries: %d open" % (doc.name, len(rows)))
    for r in rows:
        print("        %s" % r[:74])
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="Deterministic gate for orchestrator decision docs.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="validate invariants; exits non-zero on violation")
    c.add_argument("--doc", help="one doc (default: every OrchDoc in the workspace root)")
    c.add_argument("--quiet", action="store_true", help="suppress per-doc OK lines")
    c.add_argument("--strict", action="store_true",
                   help="promote advisory findings to blocking")
    c.set_defaults(func=cmd_check)

    f = sub.add_parser("freshness",
                       help="is the working-tree copy equal to the canonical ref?")
    f.add_argument("--doc")
    f.set_defaults(func=cmd_freshness)

    sub.add_parser("sessions",
                   help="which OrchDocs have a session naming them - the alarm for a rename "
                        "that makes a session invisible to the Stop hook"
                   ).set_defaults(func=cmd_sessions)
    rg = sub.add_parser("registry",
                        help="scope conflicts in ORCHESTRATOR-REGISTRY.md - two rows claiming "
                             "one repo, and terminal rows pointing nowhere")
    rg.add_argument("--strict", action="store_true",
                    help="also fail when a declared product repo is named in no row")
    rg.set_defaults(func=cmd_registry)
    w = sub.add_parser("whoami",
                       help="this session's send_message id, with the refusal oracle")
    w.set_defaults(func=cmd_whoami)

    a = sub.add_parser("add", help="capture a decision/finding as a stub, print its anchor")
    a.add_argument("title", help="one line; enrich later")
    a.add_argument("--doc", required=True, help="o7, or a filename, or a path")
    a.add_argument("--kind", default="decision",
                   choices=sorted(KIND_SECTION), help="default: decision")
    a.add_argument("--owner", help="default: the human for decisions")
    a.add_argument("--prefix", help="override the id prefix (o8 uses DA)")
    a.add_argument("--id", help="force a specific id (normally auto-allocated)")
    a.add_argument("--date", help="override the date")
    a.add_argument("--allow-stale", action="store_true",
                   help="capture even though this tree is behind the canonical ref - the "
                        "id may already belong to someone else, so verify it before "
                        "writing it anywhere")
    a.add_argument("--no-fetch", action="store_true",
                   help="skip the staleness fetch (offline). The remote-tracking ref on "
                        "disk may itself be old, and the output says so")
    a.set_defaults(func=cmd_add)
    a.add_argument("--not-mine", action="store_true",
                    help="this doc belongs to another orchestrator and they have agreed to the edit")

    rs = sub.add_parser("restamp",
                        help="record a re-review on ONE entry, with the attestation bar "
                             "applied before the write")
    rs.add_argument("id")
    rs.add_argument("--doc", required=True)
    rs.add_argument("--because", required=True,
                    help="what MOVED and why this entry survives it - the bar is checked "
                         "here, not later")
    rs.add_argument("--by", default=None, help="defaults to this doc's owner")
    rs.add_argument("--at", default=None, help="ISO timestamp; defaults to now")
    rs.add_argument("--not-mine", action="store_true")
    rs.set_defaults(func=cmd_restamp)

    r = sub.add_parser("resolve", help="flip an entry's Status IN PLACE")
    r.add_argument("id")
    r.add_argument("--doc", required=True)
    r.add_argument("--ruling", required=True, help="what was decided, and by whom")
    r.add_argument("--status", default="RESOLVED",
                   help="RESOLVED (default), SUPERSEDED, DEFERRED, PARKED")
    r.add_argument("--depends", default=None,
                   help="what this ruling RESTS ON (e.g. 'F3, o7:D16'). Required by "
                        "E-NODEPS: a ruling with no declared edge can never go stale, "
                        "so nothing can ever tell you it needs revisiting.")
    r.add_argument("--adopt", action="store_true",
                   help="legacy entry with no Status field: insert one instead of refusing")
    r.add_argument("--commit", dest="dry_run", action="store_false", default=True,
                   help="actually write (default is a dry run, like release.mjs)")
    r.set_defaults(func=cmd_resolve)
    r.add_argument("--not-mine", action="store_true",
                    help="this doc belongs to another orchestrator and they have agreed to the edit")

    mg = sub.add_parser("migrate",
                        help="bring a LEGACY doc to where the other commands work")
    mg.add_argument("--doc", required=True)
    mg.add_argument("--owner", default="orchestrator")
    mg.add_argument("--date", help="value for Opened; default 'unknown'")
    mg.add_argument("--commit", dest="dry_run", action="store_false", default=True,
                    help="actually write (default is a dry run)")
    mg.set_defaults(func=cmd_migrate)

    hf = sub.add_parser("handoff",
                        help="before freezing a doc, verify every OPEN item exists in a "
                             "LIVING doc. Use at consolidation, retirement or dormancy.")
    hf.add_argument("--doc", required=True)
    hf.add_argument("--dormant", action="store_true",
                    help="intent flag: this doc is about to be marked dormant")
    hf.set_defaults(func=cmd_handoff)

    rv = sub.add_parser("review",
                        help="walk EVERY section one at a time and force the completeness "
                             "question - an empty section generates no findings")
    rv.add_argument("--doc", required=True)
    rv.set_defaults(func=cmd_review)

    st = sub.add_parser("strike",
                        help="apply the full settled-sub-item form (checkbox + grey + strike) "
                             "to done sub-items inside LIVE entries")
    st.add_argument("--doc", required=True)
    st.add_argument("--commit", dest="dry_run", action="store_false", default=True)
    st.add_argument("--not-mine", action="store_true")
    st.set_defaults(func=cmd_strike)

    ro = sub.add_parser("reorder",
                        help="sort entry numbers within each section so a reader can stop "
                             "when they arrive instead of scanning the whole section")
    ro.add_argument("--doc", required=True)
    ro.add_argument("--commit", dest="dry_run", action="store_false", default=True)
    ro.add_argument("--not-mine", action="store_true",
                    help="this doc belongs to another orchestrator and they have agreed")
    ro.set_defaults(func=cmd_reorder)

    asrt = sub.add_parser("asserted",
                          help="search what a doc still CLAIMS - strikes, comments and fenced "
                               "quotes removed first, because a corrected doc greps like an "
                               "uncorrected one")
    asrt.add_argument("pattern", help="a regex; matched case-insensitively")
    asrt.add_argument("--doc", help="one doc; default is every OrchDoc plus the bridges")
    asrt.set_defaults(func=cmd_asserted)

    lk = sub.add_parser("links",
                        help="harvest every asset the doc cites and propose a §1 table - "
                             "§1 is a COLLECTION task, not an authoring one")
    lk.add_argument("--doc", required=True)
    lk.set_defaults(func=cmd_links)

    rm = sub.add_parser("refresh-meta",
                        help="re-derive the meta block's dates from the commit log")
    rm.add_argument("--doc", required=True)
    rm.set_defaults(func=cmd_refresh_meta)

    sc = sub.add_parser("scaffold", help="write/repair the canonical section spine")
    sc.add_argument("--doc", required=True)
    sc.add_argument("--dry-run", action="store_true")
    sc.add_argument("--adopt", action="store_true",
                    help="rename existing headings into their schema slot, in place")
    sc.add_argument("--reorder", action="store_true",
                    help="sort numbered sections into order (content-preserving)")
    sc.add_argument("--force", action="store_true",
                    help="required to WRITE an --adopt pass (owner must have agreed)")
    sc.set_defaults(func=cmd_scaffold)
    sc.add_argument("--not-mine", action="store_true",
                    help="this doc belongs to another orchestrator and they have agreed to the edit")

    p = sub.add_parser("plate",
                       help="[WRITES] REGENERATE the human-facing index from the entries")
    p.add_argument("--dry-run", action="store_true",
                   help="print what would change and write NOTHING. `check` and `review` are"
                        " read-only; this one is not, and the name does not say so.")
    p.add_argument("--doc", required=True)
    p.add_argument("--force", action="store_true",
                   help="allow replacing a populated index with an EMPTY one. Refused by "
                        "default: an empty index asserts 'nothing here', and overwriting "
                        "a curated one with it is the 'None open while items are open' "
                        "failure this tool exists to prevent.")
    p.set_defaults(func=cmd_plate)
    p.add_argument("--not-mine", action="store_true",
                    help="this doc belongs to another orchestrator and they have agreed to the edit")

    cm = sub.add_parser("commit",
                        help="land ONE OrchDoc on main safely from the dirty shared tree")
    cm.add_argument("--doc", required=True)
    cm.add_argument("--message", "-m", help="commit subject")
    cm.add_argument("--also", action="append", metavar="PATH",
                    help="additional file to land in the same commit; repeatable "
                         "(o5 landed 4: its OrchDoc plus 3 deliverables)")
    cm.add_argument("--override", metavar="CODE[,CODE...]",
                    help="proceed despite these invariants, RECORDING who/what/why")
    cm.add_argument("--because", metavar="REASON",
                    help="the reason for --override; held to the attestation bar")
    cm.add_argument("--expect", action="append", metavar="TEXT",
                    help="text that MUST appear in the doc after the write; repeatable. "
                         "Gate 4 refuses if absent - ids in the commit subject are "
                         "checked automatically.")
    # Kept so existing scripts and habits do not break. Gate 0 refuses either way now, so
    # this flag no longer changes anything - which is stated rather than silently ignored.
    cm.add_argument("--strict", action="store_true",
                    help="NO-OP since 2026-08-18 - gate 0 always refuses on a blocking "
                         "finding. Use --override CODE[,CODE...] --because to land anyway.")
    cm.add_argument("--commit", dest="dry_run", action="store_false", default=True,
                    help="actually push (default is a dry run)")
    cm.set_defaults(func=cmd_commit)

    nz = sub.add_parser("normalize",
                        help="regenerate heading markers FROM the Status field")
    nz.add_argument("--doc", required=True)
    nz.add_argument("--commit", dest="dry_run", action="store_false", default=True)
    nz.set_defaults(func=cmd_normalize)
    nz.add_argument("--not-mine", action="store_true",
                    help="this doc belongs to another orchestrator and they have agreed to the edit")

    ar = sub.add_parser("archive",
                        help="move finished entries out of the active sections")
    ar.add_argument("--doc", required=True)
    ar.add_argument("--into", default="RESOLVED - kept for the record")
    ar.add_argument("--commit", dest="dry_run", action="store_false", default=True)
    ar.set_defaults(func=cmd_archive)
    ar.add_argument("--not-mine", action="store_true",
                    help="this doc belongs to another orchestrator and they have agreed to the edit")

    cl = sub.add_parser("clones",
                        help="is every repo checkout current with its remote?")
    cl.add_argument("--no-fetch", action="store_true",
                    help="skip the fetch (faster, but the answer may be stale)")
    cl.set_defaults(func=cmd_clones)

    v = sub.add_parser("verify",
                       help="known-good verification primitives (no shell, named oracle)")
    v.add_argument("what", choices=["at", "landed", "merged", "current"])
    v.add_argument("--ref", default=CANONICAL_REF)
    v.add_argument("--path")
    v.add_argument("--sha")
    v.add_argument("--repo", help="default: the workspace")
    v.set_defaults(func=cmd_verify)

    s = sub.add_parser("selftest", help="run built-in fixtures")
    s.set_defaults(func=cmd_selftest)

    args = ap.parse_args()
    try:
        return args.func(args)
    except DocPathError as e:
        # A refusal is a RESULT, not a crash. These are the cases where the tool has been
        # pointed somewhere it must not write - outside the workspace, at a directory, at
        # something unwritable - and the right behaviour is to say so by name and stop.
        # A traceback here would be indistinguishable from a bug, and mid-command crashes
        # are how half-written state happens.
        print("[REFUSE] %s" % e, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        # Writes are atomic (write_doc), so an interrupt cannot leave a partial document.
        print("\n[orchdoc] interrupted - no document was left partially written.",
              file=sys.stderr)
        return 130


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(2)
