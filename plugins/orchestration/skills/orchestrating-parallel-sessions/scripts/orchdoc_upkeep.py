#!/usr/bin/env python3
"""
<!-- subject: orchestration -->
<!-- route-tags: orchdoc check advisory upkeep plate owner human sibling rewrite ruling landed
     cross-doc citation reciprocal checkbox sub-items unnumbered section heading no id -->

That report names eleven ways an entry goes stale after it is written (P1-P11). Six of them can
be caught by reading the document, and this module reads for those six. `orchdoc.py check` calls
`check()` once per doc, at the end of `check_doc`.

EVERY CODE HERE IS ADVISORY. The rule in this codebase is to count the inherited backlog first
and promote a check to blocking only after the fleet count reaches zero (see W-DEADCITE and
W-STALLED in orchdoc.py for the same sequencing). None of these codes is in orchdoc.BLOCKING.

    code                 report  what it reports
    W-PLATEOWNER         P1      a live entry in §2.x whose Owner does not name the human, so the
    W-HUMANLINE           P2      inside a live §3 entry that is not the human's, a line asking
    W-NEWERSIBLING       P3      a live entry cites a file, and the same directory holds a
                                 later version of it (`<name> - the human rewrite`, `<name>-v2`,
                                 `<name>...rewrite`) that the entry never mentions
    W-RULEDNOTLANDED     P7      a D entry ruled in the last 14 days (archived or not) whose
                                 `**Resolved <date>:**` text names no file, PR, release or W
                                 entry carrying it, and does not say nothing needs to change
                                 (the weak form of P7)
    W-ONEWAYCITE         P8      a live W/T/D/Q/A entry cites `ORCHESTRATOR-DECISIONS-oN.md#<id>`
                                 and that entry in oN never mentions this entry back
    W-NOSUBITEMS         P9      a live W entry with no checkboxes, so its progress cannot be
                                 seen without reading all of it
    W-UNIDENTIFIED       P9      a `###` heading with no entry id: always inside §2 or §3, and
                                 anywhere else when unchecked boxes under it belong to no entry
    W-UNNUMBERED         P9      an unnumbered `##` heading with unchecked boxes under it that no
                                 entry holds; after §99 or before §1 also a closed entry's
                                 unticked box, a live entry, or a `###` heading with no id

                                 (`orchdoc-sweeps/oN/<date>.md`) has action rows, and no entry
                                 in the doc cites that report's path - so nothing records that
                                 anyone read it

beside each regex or scope line names the measured case that forced it.
                                 and its Opened date and any `**Rechecked:** <date>` line are
                                 both more than 7 days old
    W-UPKEEPCRASH        -       one of the checks above raised. Reported, never raised, so an
                                 advisory check cannot take down the commit gate

THE SWEEP REPORT FORMAT IS DEFINED HERE, not in orchdoc_sweep.py, because this module reads it
and orchdoc_sweep.py imports orchdoc, which imports this one. One home for the folder name, the
file name, the header line and "which report is the latest" - `orchdoc_sweep.py fresh` writes
through the same functions it reads with.

"LIVE" means what orchdoc.LIVE_STATUS says: OPEN, BLOCKED, IN PROGRESS or SEQUENCED, and not
archived. The report said "OPEN"; an IN PROGRESS entry goes stale the same way, so it is
included.

NOT BUILT HERE, AND WHY:
  P4 (a named blocker rots) and P5 (already-done work looks open) are covered by checks that
  already exist in orchdoc.py: W-NOBLOCKER, W-STALLED, E-STALEPROSE, E-DONEBUTOPEN,
  E-ALLSUBSDONE, E-DONEINACTIVE and E-CLOSEDWITHOPENSUBS. P6, P10 and P11 are procedures (cite
  the command not the value; run a known-good positive first; do not name an unbuilt check),
  and reading a document cannot tell whether someone followed them.

ONE READER: every question that orchdoc.py already answers - who owns an entry, what its status
is, which files it cites - is answered by calling orchdoc's own function, passed in as `od`.
This module adds no second reader for any of them.
"""
import os
import re
import subprocess
from pathlib import Path
try:
    from no_window import flags as _flags
except ImportError:  # a partial delivery must not take the tool down
    def _flags(extra=0):
        return extra

# The codes this module can emit. None of them is blocking - see the docstring.
CODES = ("W-PLATEOWNER", "W-HUMANLINE", "W-NEWERSIBLING", "W-RULEDNOTLANDED",
         "W-ONEWAYCITE", "W-NOSUBITEMS", "W-UNIDENTIFIED", "W-UNNUMBERED", "W-SWEEPOPEN",
         "W-TITLEHIDESASK", "W-EXTERNALCLAIM", "W-UPKEEPCRASH")

DOC_SLUG_RE = re.compile(r"^ORCHESTRATOR-DECISIONS-(o\d+)\.md$")

# ---- P2: language that asks something of the human ---------------------------------------------
#
#   * `needs?` - "the two older ones need the human to open the app" asks the same thing.
# The human's name is read from orchdoc.human_name() when a check runs (see `_name`), never written
# here, so a copy of this tool used by anyone else asks about its own reader.
_ASK_CACHE = {}


_NAME_MEMO = None


def _name(od=None):
    """The human's name for the patterns below: `od.human_name()`, else a neutral word."""
    if _NAME_MEMO is not None and id(od) in _NAME_MEMO:
        return _NAME_MEMO[id(od)]
    try:
        name = od.human_name()
    except Exception:
        name = "THE HUMAN"
    if _NAME_MEMO is not None:
        _NAME_MEMO[id(od)] = name
    return name


def human_ask_re(od=None):
    """The P2 pattern for language that asks something of the human, cached per name."""
    name = _name(od)
    rx = _ASK_CACHE.get(("ask", name))
    if rx is None:
        rx = _ASK_CACHE[("ask", name)] = re.compile(
            (r"\bneeds? %(n)s\b|\bhis call\b|\bwaiting on your\b|\b%(n)s's judge?ment\b"
             r"|\bonly he can\b"
             r"|\b(?:needs?|awaits?|waits? on|waiting on|a matter of|up to)\s+his taste\b"
             r"|\bfor %(n)s to\b|\b%(n)s to approve\b|\b%(n)s must\b") % {"n": re.escape(name)},
            re.I)
    return rx

# A negation earlier in the same clause turns the phrase into its opposite: "nothing here needs
# the human", "it does not need the human".
NEGATED_RE = re.compile(
    r"(?i)(?:\b(?:no|not|nothing|never|none|neither|nor|without)\b|n't\b)[^.;:!?]{0,40}$")
# "a note FROM the human must still count" - the human is the object of a preposition, not the one who
# must act.
PREP_BEFORE_RE = re.compile(r"(?i)\b(?:from|by|of|to|for|with)\s+$")

# A statement that RECORDS a ruling rather than asking for one.
#
# ⛔ "ruling" on its own is NOT here: "this needs the human's ruling" is the exact line P2 exists
# to find.
def ruling_mark_re(od=None):
    """The pattern for a statement that records a ruling, for the human's name; cached per name."""
    name = _name(od)
    rx = _ASK_CACHE.get(("ruling", name))
    if rx is None:
        rx = _ASK_CACHE[("ruling", name)] = re.compile(
            r"(?i:\bruled\b)|\bRESOLVED\b|\bANSWERED\b|\bRETRACTED\b|\bSUPERSEDED\b|\*\*Resolved"
            r"|(?i:\bverbatim\b)|\b%s,?\s+(?:on\s+)?\d{4}-\d{2}-\d{2}" % re.escape(name.title()))
    return rx

# An entry id written in prose.
PROSE_ID_RE = re.compile(r"(?<![A-Za-z0-9:/#])([A-Z]{1,3}\d+[a-z]?)\b")

LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s")
FIELD_START_RE = re.compile(r"^\s*\*\*[A-Za-z][A-Za-z -]*[^*]*:\*\*")
INLINE_CODE_RE = re.compile(r"`[^`]*`")
HTML_COMMENT_RE = re.compile(r"<!--.*?-->")
STRUCK_SPAN_RE = re.compile(r"~~.*?~~")
ANY_BOX_RE = re.compile(r"^\s*[-*+]\s*\[[ xX]\]")
UNCHECKED_RE = re.compile(r"^\s*[-*+]\s*\[\s\]")

# ---- P3: names that mean "a later version of this file" -------------------------------------
REWRITE_RE = re.compile(r"rewrite", re.I)
VERSION_RE = re.compile(r"[-_ ]v(\d+)[a-z]?$", re.I)
# What to strip off a cited name to find the family it belongs to.
# "<title> - the human rewrite" is a FILE-NAME suffix fixed by the document library, not the reader's name.
FAMILY_TAIL_RE = re.compile(r"(?:\s+-\s+the human rewrite.*|[-_ ]v\d+[a-z]?|[-_ ]?rewrite.*)$", re.I)
MIN_FAMILY_CHARS = 6   # a family name shorter than this matches unrelated files

# ---- P7: what counts as naming a file in a resolution --------------------------------------
RESOLUTION_FIELD_RE = re.compile(
    r"^\s*\*\*(?:Resolved|Ruled|RULED|Answered|ANSWERED|Ruling)\b[^*]*:\*\*")
# Evidence that a ruling reached something outside the tracker: a file, a PR, a URL, a commit in
# backticks, or the words "landed at".
FILE_NAMED_RE = re.compile(
    r"[\w.\-/\\]+\.(?:md|py|ts|tsx|js|mjs|cjs|jsx|json|astro|ya?ml|sql|sh|ps1|txt|html|css"
    r"|toml|csv)\b"
    r"|`[^`\s]+/[^`\s]*`"
    r"|\bPR\s*#\d+|(?<![\w&])#\d{2,}\b|\bpull/\d+|https?://|\b[\w-]+\.com/\S"
    r"|`[0-9a-f]{7,40}`|\bv\d+\.\d+\.\d+\b"
    r"|(?<!not )\b(?:landed|applied|merged|deployed|promoted)\b"
    r"|\bshipped in\b|\blive (?:in|on) production\b", re.I)
# A ruling that says nothing has to be written anywhere: no change, the status quo kept, already
# done, deferred, withdrawn, rejected, or handed to another group to carry.
NOTHING_TO_LAND_RE = re.compile(
    r"(?i)\bnothing (?:to (?:build|change|land|do|write)|changes)\b|\bno (?:change|action)\b"
    r"|\bdeferred\b|\bdeclined\b|\bno-?op\b"
    r"|\bleave\b[^.]{0,40}\bas (?:is|it is|they are)\b|\bleave (?:it|them)\b"
    r"|\balready (?:built|done|shipped|in production|live|landed|green-?lighted)\b"
    r"|\bwithdrawn\b|\bwithdrew\b|\bhanded to\b|\bfolded into\b|\bsuperseded\b"
    r"|\bnot adding\b|\brejected\b|\bstays (?:at|as)\b|\bratif(?:y|ied)\b|\bgranted\b")
# A resolution that names the W or T entry carrying the change has done what this check asks:
# the landing is tracked where someone will act on it.
TRACKED_BY_RE = re.compile(r"(?<![A-Za-z0-9])(?:o\d+:)?[WT]\d+\b")
# How long after a ruling this check keeps asking. `orchdoc.py commit` archives a resolved
# entry on the same landing, so a check limited to un-archived entries would never see a new
# ruling; the window is what lets it see one. Same shape as W-STALLED's day count.
RULED_RECENT_DAYS = 14
RESOLVED_DATE_RE = re.compile(r"\*\*(?:Resolved|Ruled|RULED|Answered|ANSWERED)\s*"
                              r"(\d{4}-\d{2}-\d{2})")
TRACKER_NAME_RE = re.compile(r"ORCHESTRATOR-DECISIONS-o\d+\.md", re.I)
DECISION_ID_RE = re.compile(r"^D[A-Z]?\d")

# ---- P8: a machine-readable cross-doc citation ----------------------------------------------
CROSS_CITE_RE = re.compile(r"ORCHESTRATOR-DECISIONS-(o\d+)\.md#([A-Z]{1,3}\d+[a-z]?)\b")

# Parsed peer docs, reused across calls. Keyed on (path, mtime_ns, size) so an edited file is
# re-read rather than served stale.
_PEER_CACHE = {}
_MAIN_TREE = {}   # str(workspace) -> the main worktree's Path, or None; one git call each


# =============================================================================================
# structure of the document, computed once per call
# =============================================================================================

class _Doc:
    """Per-line facts every check below needs, computed in one pass."""

    def __init__(self, path, lines, entries, od):
        self.path = Path(path)
        self.lines = lines
        self.entries = entries
        self.od = od
        m = DOC_SLUG_RE.match(self.path.name)
        self.slug = m.group(1) if m else None
        n = len(lines)
        # 1-based arrays; index 0 unused.
        self.fenced = [False] * (n + 1)
        self.folded = [False] * (n + 1)      # inside <details>: archived, not live
        self.gov = [None] * (n + 1)          # the governing §N number for each line
        in_fence = False
        in_att = False
        depth = 0
        cur = None
        canon = od._CANON_HEAD_RE
        for i, raw in enumerate(lines, 1):
            if raw.lstrip().startswith("```"):
                self.fenced[i] = True
                in_fence = not in_fence
                self.gov[i] = cur
                continue
            if in_fence:
                self.fenced[i] = True
                self.gov[i] = cur
                continue
            # Same <details> rule as orchdoc.parse_entries, including its exemption for
            # OVERRIDE attestation text, so both agree on what is folded.
            if od.OVERRIDE_RE.match(raw):
                in_att = True
            elif in_att and not raw.strip():
                in_att = False
            if not in_att:
                low = HTML_COMMENT_RE.sub("", raw.lower())
                low = INLINE_CODE_RE.sub("", low)
                if "<details" in low:
                    depth += 1
                if "</details>" in low:
                    depth = max(0, depth - 1)
            self.folded[i] = depth > 0
            mm = canon.match(raw)
            if mm:
                cur = mm.group(1)
            self.gov[i] = cur
        self.entry_lines = {e["line"] for e in entries}
        # A doc with no §-numbered heading has not adopted the schema. That is E-LEGACYDOC's
        # finding, and every "unnumbered section" in it would be reported here too.
        self.has_schema = any(g is not None for g in self.gov)

    def top(self, line):
        """The top-level section number governing a line: "2", "3", "99", or None."""
        g = self.gov[line] if 0 < line < len(self.gov) else None
        return g.split(".")[0] if g else None

    def section(self, line):
        return self.gov[line] if 0 < line < len(self.gov) else None

    _cover = None
    _orphans = None

    def cover(self):
        """[entry or None] per line: the innermost parsed entry whose body holds the line."""
        if self._cover is None:
            cover = [None] * (len(self.lines) + 1)
            for e in sorted(self.entries, key=lambda x: x["level"]):   # inner overwrites
                n = len(e["body"].splitlines())
                for i in range(e["line"], min(e["line"] + n, len(self.lines) + 1)):
                    cover[i] = e
            self._cover = cover
        return self._cover

    def orphan_boxes(self):
        """{heading line: [box lines]} - unchecked boxes that NO entry holds, keyed by the
        heading (level 1-3) directly above them. That heading is what cut them off: an `##`
        or an id-less `###` ends the entry above it, so a list written under it belongs to
        nothing and no entry-based check reads it."""
        if self._orphans is None:
            cover = self.cover()
            orphans = {}
            head = 0
            for i, raw in enumerate(self.lines, 1):
                if self.fenced[i] or self.folded[i]:
                    continue
                if re.match(r"^#{1,3}(?!#)\s", raw):
                    head = i
                elif UNCHECKED_RE.match(raw) and cover[i] is None:
                    orphans.setdefault(head, []).append(i)
            self._orphans = orphans
        return self._orphans


def _is_live(e, od):
    return (not e.get("archived")) and od.status_of(e["body"]) in od.LIVE_STATUS


def _snip(text, n=70):
    t = re.sub(r"\s+", " ", text).strip()
    return t if len(t) <= n else t[:n - 1].rstrip() + "..."


# =============================================================================================
# P1 - W-PLATEOWNER
# =============================================================================================

def check_plate_owner(d):
    od, out = d.od, []
    for e in d.entries:
        if not _is_live(e, od):
            continue
        top = d.top(e["line"])
        if top not in ("2", "3"):
            continue
        owner = od.owner_text(e["body"])
        is_human = od.owner_is_human(e["body"])
        st = od.status_of(e["body"])
        if top == "2" and not is_human:
            # An open entry with NO Owner field is E-NOOWNER's finding already.
            if not owner and st in od.PLATE_STATUS:
                continue
            out.append(od.Finding(
                "W-PLATEOWNER", e["line"],
                "%s sits in §%s, the human's section, but its Owner is %s - the plate is chosen by "
                "Owner, so it is not on the human's plate" % (e["id"], d.section(e["line"]),
                                                     "'%s'" % _snip(owner, 40) if owner
                                                     else "missing"),
                "if it needs the human, write **Owner:** the human; if it is the orchestrator's own work, "
                "move it to §3 as a W entry"))
        elif top == "3" and is_human:
            out.append(od.Finding(
                "W-PLATEOWNER", e["line"],
                "%s sits in §3, the orchestrator's work, but its Owner '%s' names the human%s"
                % (e["id"], _snip(owner, 40),
                   " - so it renders on the human's plate as an ask" if st in od.PLATE_STATUS else ""),
                "if it needs the human, move it to §2 (a T entry for a to-do); if the orchestrator "
                "acts, name the orchestrator in **Owner:** and put the human's part in the body"))
    return out


# =============================================================================================
# P2 - W-HUMANLINE
# =============================================================================================

def _units(d, lo, hi):
    """Split lines lo..hi (1-based, inclusive) into statements.

    A statement is one list item with its wrapped continuation lines, one prose paragraph,
    one field line, or one table row. Returns [(first_line, last_line)].
    """
    units = []
    start = None
    prev = None
    for i in range(lo, hi + 1):
        raw = d.lines[i - 1]
        s = raw.strip()
        if not s:
            if start is not None:
                units.append((start, prev))
            start = None
            prev = i
            continue
        new = (start is None or LIST_ITEM_RE.match(raw) or FIELD_START_RE.match(raw)
               or s.startswith("|") or s.startswith("#") or s.startswith(">")
               or d.fenced[i] != d.fenced[start])
        if new and start is not None:
            units.append((start, prev))
            start = None
        if start is None:
            start = i
        prev = i
    if start is not None:
        units.append((start, prev))
    return units


def _human_ask(line, od=None):
    """The first phrase in `line` that asks something of the human, or None.

    Struck text, code spans and HTML comments are removed first - they do not assert anything.
    A phrase negated earlier in its clause, or a "the human must" whose the human follows a preposition,
    is not an ask."""
    ln = INLINE_CODE_RE.sub(" ", STRUCK_SPAN_RE.sub(" ", HTML_COMMENT_RE.sub(" ", line)))
    rx = human_ask_re(od)
    for m in rx.finditer(ln):
        before = ln[:m.start()]
        if NEGATED_RE.search(before[-60:]):
            continue
        if re.match(re.escape(_name(od)) + r" must", m.group(0), re.I) and PREP_BEFORE_RE.search(before):
            continue
        return m
    return None


def check_human_line(d):
    od, out, seen = d.od, [], set()
    # Live entries in §2: the places a human ask is supposed to be filed.
    plate_ids = {e["id"] for e in d.entries
                 if _is_live(e, od) and d.top(e["line"]) == "2"}
    for e in d.entries:
        if not _is_live(e, od) or d.top(e["line"]) != "3":
            continue
        if od.owner_is_human(e["body"]):
            continue
        lo = e["line"] + 1
        hi = e["line"] + len(e["body"].splitlines()) - 1
        for a, b in _units(d, lo, hi):
            if a in seen or any(d.fenced[i] or d.folded[i] for i in range(a, b + 1)):
                continue
            first = d.lines[a - 1].strip()
            if (first.startswith(">") or first.startswith("#")
                    or od.STAMP_LINE_RE.match(d.lines[a - 1])
                    or od.CHECKED_BOX_RE.match(d.lines[a - 1])):
                continue
            unit = "\n".join(d.lines[a - 1:b])
            if od.NOTE_MARK in unit:
                continue
            # What the statement still asserts: struck spans, code and comments removed.
            live = HTML_COMMENT_RE.sub(" ", unit)
            live = STRUCK_SPAN_RE.sub(" ", live)
            live = INLINE_CODE_RE.sub(" ", live)
            if ruling_mark_re(od).search(live):
                continue
            # Already filed: the statement points at a live §2 entry of this doc.
            if plate_ids & set(PROSE_ID_RE.findall(live)):
                continue
            for i in range(a, b + 1):
                m = _human_ask(d.lines[i - 1], od)
                if m:
                    seen.add(a)
                    out.append(od.Finding(
                        "W-HUMANLINE", i,
                        "%s is orchestrator work in §3, but this line asks something of the human "
                        "('%s'): %s" % (e["id"], m.group(0), _snip(d.lines[i - 1].strip())),
                        "the plate is built from entries, so this line never reaches the human. If it "
                        "needs the human, lift it into a §2 entry with **Owner:** the human and point to "
                        "it from here"))
                    break
    return out


# =============================================================================================
# P3 - W-NEWERSIBLING
# =============================================================================================

def _roots(od):
    """Where a cited path may live: this workspace, and the main checkout when this is a
    worktree - a file that is untracked, like a rewrite the human saved by hand, exists only there."""
    roots = [Path(od.PROJECTS)]
    key = str(od.PROJECTS)
    if key not in _MAIN_TREE:
        try:
            _MAIN_TREE[key] = od._main_worktree()
        except Exception:
            _MAIN_TREE[key] = None
    main = _MAIN_TREE[key]
    if main is not None:
        try:
            if Path(main).resolve() != roots[0].resolve():
                roots.append(Path(main))
        except OSError:
            pass
    return roots


def _version(stem):
    m = VERSION_RE.search(stem)
    return int(m.group(1)) if m else 0


ANY_VERSION_RE = re.compile(r"[-_ ]v(\d+)(?![0-9])", re.I)


def _any_version(stem):
    """The first -vN anywhere in a name: `X-05-PILOT-v2-rule18 - THE HUMAN REWRITE` is a rewrite
    OF v2."""
    m = ANY_VERSION_RE.search(stem)
    return int(m.group(1)) if m else 0


def _is_later(cited_stem, sib_stem):
    """By its NAME, is `sib_stem` a later version of `cited_stem`?

    Names, not modification times: in a git worktree every file carries the checkout time,
    so a time comparison says nothing there. A rewrite is later than the file it rewrites,
    and a higher -vN is later than a lower one.
    """
    if REWRITE_RE.search(sib_stem) and not REWRITE_RE.search(cited_stem):
        return _any_version(sib_stem) >= _any_version(cited_stem)
    if VERSION_RE.search(sib_stem) and _version(sib_stem) > _version(cited_stem):
        return True
    return False


def check_newer_sibling(d):
    od, out = d.od, []
    listings = {}          # one listdir per distinct directory, per call

    def listing(dirpath):
        key = str(dirpath)
        if key not in listings:
            try:
                listings[key] = os.listdir(dirpath) if dirpath.is_dir() else []
            except OSError:
                listings[key] = []
        return listings[key]

    roots = None
    for e in d.entries:
        if not _is_live(e, od):
            continue
        # Citations from statements that are still OPEN. A file named in a checked sub-item or
        lo = e["line"] + 1
        hi = e["line"] + len(e["body"].splitlines()) - 1
        cited = {}
        for a, b in _units(d, lo, hi):
            first = d.lines[a - 1]
            if (od.CHECKED_BOX_RE.match(first) or od.STAMP_LINE_RE.match(first)
                    or any(d.fenced[i] for i in range(a, b + 1))):
                continue
            text = STRUCK_SPAN_RE.sub(" ", "\n".join(d.lines[a - 1:b]))
            for c in od.cited_paths(text):
                if Path(c).name != d.path.name:
                    cited.setdefault(c, []).append(text.lower())
        if not cited:
            continue
        if roots is None:
            roots = _roots(od)
        body_low = e["body"].lower()
        reported = set()
        for c, stmts in sorted(cited.items()):
            rel = Path(c)
            if rel.is_absolute() or ".." in rel.parts:
                continue
            stem, suffix = rel.stem, rel.suffix.lower()
            family = FAMILY_TAIL_RE.sub("", stem).strip()
            if len(family) < MIN_FAMILY_CHARS:
                continue
            fam_low = family.lower()
            for root in roots:
                full = root / rel
                for name in listing(full.parent):
                    if (name == rel.name or name in reported
                            or not name.lower().startswith(fam_low)):
                        continue
                    sp = Path(name)
                    if sp.suffix.lower() != suffix:
                        continue
                    if not sp.stem.lower().startswith(family.lower()):
                        continue
                    if not _is_later(stem, sp.stem):
                        continue
                    # The entry already knows about it: it names the sibling, or the statement
                    # citing this file says there is a rewrite.
                    if name.lower() in body_low or sp.stem.lower() in body_low:
                        continue
                    if REWRITE_RE.search(sp.stem) and any("rewrite" in s for s in stmts):
                        continue
                    # When both files have real times and the sibling is clearly OLDER, the
                    # name is misleading - trust the clock. Equal times (a worktree) fall
                    # through to the name.
                    try:
                        if full.exists() and (full.parent / name).stat().st_mtime \
                                < full.stat().st_mtime - 60:
                            continue
                    except OSError:
                        pass
                    reported.add(name)
                    out.append(od.Finding(
                        "W-NEWERSIBLING", e["line"],
                        "%s cites %s, and the same folder holds a later version it never "
                        "mentions: %s" % (e["id"], rel.name, name),
                        "open the later file; if it supersedes the cited one, re-point the "
                        "entry at it (an entry was attested five times against a superseded "
                        "file)"))
    return out


# =============================================================================================
# P7 - W-RULEDNOTLANDED (weak form)
# =============================================================================================

def resolution_text(body):
    """The text of every **Resolved/Ruled/Answered ...:** field, each to the end of its
    paragraph or the next field. "" when the entry has none."""
    out = []
    lines = body.splitlines()
    i = 0
    while i < len(lines):
        if RESOLUTION_FIELD_RE.match(lines[i]):
            chunk = [lines[i]]
            j = i + 1
            while j < len(lines):
                s = lines[j].strip()
                if not s or FIELD_START_RE.match(lines[j]) or s.startswith("#"):
                    break
                chunk.append(lines[j])
                j += 1
            out.append("\n".join(chunk))
            i = j
            continue
        i += 1
    return "\n".join(out)


def names_a_file(text):
    """Does the text name an artifact? OrchDoc filenames do not count - a tracker is the place
    the ruling was recorded, not the place a lane reads it."""
    for m in FILE_NAMED_RE.finditer(text):
        if not TRACKER_NAME_RE.search(m.group(0)):
            return True
    return False


def check_ruled_not_landed(d, today=None):
    """A D entry ruled in the last RULED_RECENT_DAYS whose resolution names nowhere it landed.

      * ARCHIVED ENTRIES ARE INCLUDED. `commit` archives a resolved entry on the same landing,
        so every new ruling is archived within minutes; skipping archived entries would have
        made this check blind to exactly the rulings it exists for.
      * ONLY RECENT RULINGS. The fleet's older rulings are closed history; asking for a
        "landed at" on each of them is bookkeeping, not catching a ruling while it can still
        be carried.
      * ONLY ENTRIES WITH A `**Resolved <date>:**` LINE, the form `orchdoc.py resolve --ruling`
        writes. An older entry whose ruling lives in its heading or a ✅ paragraph has no
        resolution text to read; guessing which paragraph it is measured 1 true positive in 6.
    """
    import datetime as _dt
    od, out = d.od, []
    today = today or _dt.date.today()
    for e in d.entries:
        if not DECISION_ID_RE.match(e["id"]):
            continue
        st = od.status_of(e["body"])
        if st not in ("RESOLVED", "ANSWERED"):
            continue
        res = resolution_text(e["body"])
        if not res:
            continue
        m = RESOLVED_DATE_RE.search(res)
        if not m:
            continue
        try:
            age = (today - _dt.date.fromisoformat(m.group(1))).days
        except ValueError:
            continue
        if age < 0 or age > RULED_RECENT_DAYS:
            continue
        if (names_a_file(res) or NOTHING_TO_LAND_RE.search(res)
                or TRACKED_BY_RE.search(res)):
            continue
        out.append(od.Finding(
            "W-RULEDNOTLANDED", e["line"],
            "%s was ruled %d day(s) ago and its resolution names no file, PR or release it "
            "landed in, so nothing records that the ruling reached what a lane reads"
            % (e["id"], age),
            "add 'landed at <file>:<line>' (or the PR) to the resolution once it is written in; "
            "if it needs to reach a file that does not carry it yet, that is a W entry"))
    return out


# =============================================================================================
# P8 - W-ONEWAYCITE
# =============================================================================================

def _peer_entries(path, od):
    try:
        st = path.stat()
    except OSError:
        return None
    key = (str(path), st.st_mtime_ns, st.st_size)
    hit = _PEER_CACHE.get(key)
    if hit is not None:
        return hit
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    entries, _ = od.parse_entries(text.splitlines())
    by_id = {}
    for e in entries:
        by_id.setdefault(e["id"], e)
    # Drop stale versions of the same file so the cache cannot grow without bound.
    for k in [k for k in _PEER_CACHE if k[0] == str(path)]:
        del _PEER_CACHE[k]
    _PEER_CACHE[key] = by_id
    return by_id


def mentions_back(target_body, target_id, my_slug, my_id):
    """Does the target entry name this entry - as `oN:ID`, or as the bare id?
    """
    if re.search(r"\b%s\s*[:/]\s*%s\b" % (re.escape(my_slug), re.escape(my_id)), target_body):
        return True
    if re.search(r"ORCHESTRATOR-DECISIONS-%s\.md#%s\b" % (re.escape(my_slug), re.escape(my_id)),
                 target_body):
        return True
    if my_id != target_id and re.search(r"(?<![A-Za-z0-9])%s\b" % re.escape(my_id),
                                        target_body):
        return True
    return False


# Kinds that hold WORK or an ASK, and so describe another group's plan. A finding (F) or a
# specimen (S) citing another doc's entry is citing evidence, which does not go stale the way a
# description of someone else's plan does.
MIRROR_KINDS_RE = re.compile(r"^(?:W|T|D|Q|A)\d")


def check_one_way_cite(d):
    od, out = d.od, []
    if not d.slug:
        return out
    for e in d.entries:
        if not _is_live(e, od) or not MIRROR_KINDS_RE.match(e["id"]):
            continue
        body = STRUCK_SPAN_RE.sub(" ", od.without_attested_by(e["body"]))
        done = set()
        for m in CROSS_CITE_RE.finditer(body):
            slug, tid = m.group(1), m.group(2)
            if slug == d.slug or (slug, tid) in done:
                continue
            done.add((slug, tid))
            peer = _peer_entries(d.path.parent / ("ORCHESTRATOR-DECISIONS-%s.md" % slug), od)
            if not peer or tid not in peer:
                continue          # a citation that does not resolve is a different defect
            if mentions_back(peer[tid]["body"], tid, d.slug, e["id"]):
                continue
            out.append(od.Finding(
                "W-ONEWAYCITE", e["line"],
                "%s:%s cites %s:%s, and %s:%s never mentions %s:%s back"
                % (d.slug, e["id"], slug, tid, slug, tid, d.slug, e["id"]),
                "two records of one fact with no join key drift apart. Ask %s to name %s:%s "
                "in %s, or make this side a bare pointer that describes nothing of theirs"
                % (slug, d.slug, e["id"], tid)))
    return out


# =============================================================================================
# P9 - W-NOSUBITEMS, W-UNIDENTIFIED, W-UNNUMBERED
# =============================================================================================

def check_no_subitems(d):
    od, out = d.od, []
    for e in d.entries:
        if not _is_live(e, od) or not re.match(r"^W\d", e["id"]):
            continue
        if any(ANY_BOX_RE.match(ln) for ln in e["body"].splitlines()):
            continue
        out.append(od.Finding(
            "W-NOSUBITEMS", e["line"],
            "%s is %s with no checkboxes, so its progress cannot be seen without reading all "
            "of it" % (e["id"], od.status_of(e["body"])),
            "list the remaining steps as `- [ ]` sub-items, first one 'open the artifact'"))
    return out


H3_RE = re.compile(r"^###(?!#)\s+(.*)$")
H12_RE = re.compile(r"^#{1,2}(?!#)\s+(.*)$")


def check_unidentified(d):
    """A `###` heading with no entry id.

    In §2 or §3 always: those sections promise that every item in them is an entry. One
    duplicate...` orphaned the box "The generator-side fix is NOT done".
    """
    od, out = d.od, []
    if not d.has_schema:
        return out
    orphans = d.orphan_boxes()
    for i, raw in enumerate(d.lines, 1):
        if d.fenced[i] or d.folded[i] or i in d.entry_lines:
            continue
        m = H3_RE.match(raw)
        if not m or od.SECTION_RE.match(raw):
            continue          # not ###, or a numbered sub-section such as ### §3.1
        boxes = orphans.get(i, [])
        if d.top(i) not in ("2", "3") and not boxes:
            continue
        tail = (" - %d unchecked box(es) under it belong to no entry" % len(boxes)
                if boxes else "")
        out.append(od.Finding(
            "W-UNIDENTIFIED", i,
            "a ### heading in §%s has no entry id, so no check that works on entries sees "
            "what is under it%s: %s" % (d.section(i) or "-", tail, _snip(m.group(1), 60)),
            "give it an id (orchdoc.py add) if it is an item; if it is part of the entry above, "
            "make it #### so the entry keeps its body"))
    return out


def check_unnumbered(d):
    """An unnumbered `##` heading, and what sits under it that no entry-based check can see.

    Counted, and nothing else:
      * an unchecked `- [ ]` directly under it that NO entry holds - an `##` heading ends the
        the human" inside §3)
      * when the heading sits in the tail or head (after §99, or before §1):
          - a live entry, or a `###` heading that is not an entry
    A `###` heading in §2 or §3, or one with orphaned boxes under it, is W-UNIDENTIFIED's
    finding, so it is not counted twice here. An entry with an id is visible to every check
    wherever it sits.
    """
    od, out = d.od, []
    if not d.has_schema:
        return out
    heads = [i for i, raw in enumerate(d.lines, 1)
             if not d.fenced[i] and H12_RE.match(raw)]
    cover = d.cover()
    orphans = d.orphan_boxes()
    for k, i in enumerate(heads):
        raw = d.lines[i - 1]
        if not re.match(r"^##(?!#)", raw):
            continue          # a level-1 title ends a section but is not one
        if od.SECTION_RE.match(raw):
            continue
        end = heads[k + 1] - 1 if k + 1 < len(heads) else len(d.lines)
        top = d.top(i)
        outside = top in (None, "99")
        boxes, subheads, live = list(orphans.get(i, [])), [], []
        for j in range(i + 1, end + 1):
            if not outside:
                break
            if d.fenced[j] or d.folded[j]:
                continue
            ln = d.lines[j - 1]
            owner = cover[j]
            if UNCHECKED_RE.match(ln):
                if owner is not None and od.status_of(owner["body"]) in od.TERMINAL_STATUS:
                    boxes.append(j)
            elif H3_RE.match(ln):
                if j in d.entry_lines:
                    if owner is not None and owner["line"] == j and _is_live(owner, od):
                        live.append(owner["id"])
                elif not od.SECTION_RE.match(ln) and not orphans.get(j):
                    subheads.append(j)
        boxes.sort()
        if not (boxes or subheads or live):
            continue
        parts = []
        if boxes:
            parts.append("%d unchecked box(es) no open entry holds, first at line %d"
                         % (len(boxes), boxes[0]))
        if live:
            parts.append("live entr%s %s" % ("y" if len(live) == 1 else "ies",
                                             ", ".join(live[:4])))
        if subheads:
            parts.append("%d ### heading(s) with no id, first at line %d"
                         % (len(subheads), subheads[0]))
        where = ("outside the numbered sections" if outside
                 else "inside §%s, where it ends the entry above it" % d.section(i))
        out.append(od.Finding(
            "W-UNNUMBERED", i,
            "unnumbered section '%s' (%s) holds %s"
            % (_snip(H12_RE.match(raw).group(1), 50), where, "; ".join(parts)),
            "move live items into §2/§3 as entries and tick or delete closed boxes; demote a "
            "heading inside an entry to ####; number a reference section (§6-§98)"))
    return out


# =============================================================================================
# =============================================================================================

SWEEP_DIR = "orchdoc-sweeps"
SWEEP_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:-(\d+))?\.md$")
# One machine-readable line at the top of every report. Values carry no spaces.
SWEEP_HEADER_RE = re.compile(r"<!--\s*orchdoc-sweep:\s*([^>]*?)\s*-->")


def sweep_report_key(name):
    """Sort key for a report file name, or None when the name is not a report.
    """
    m = SWEEP_NAME_RE.match(Path(str(name)).name)
    return (m.group(1), int(m.group(2) or 1)) if m else None


def render_sweep_header(fields):
    """`<!-- orchdoc-sweep: k=v k=v -->` from an ordered list of (key, value) pairs."""
    parts = []
    for k, v in fields:
        v = re.sub(r"\s+", "_", str(v))
        parts.append("%s=%s" % (k, v))
    return "<!-- orchdoc-sweep: %s -->" % " ".join(parts)


def parse_sweep_header(text):
    """{key: value} from the first header line in a report, or None when it has none."""
    m = SWEEP_HEADER_RE.search(text or "")
    if not m:
        return None
    out = {}
    for tok in m.group(1).split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            out.setdefault(k, v)
    return out


def sweep_action_rows(text):
    """The header's action-row count; 0 when it is missing or unreadable."""
    try:
        return int((parse_sweep_header(text) or {}).get("action_rows", "0"))
    except ValueError:
        return 0


def _git_bytes(root, args):
    """(rc, text) of a git command, decoded as UTF-8 whatever the console's code page is.
    A report quotes a lane's output, which can carry any character."""
    try:
        p = subprocess.run(["git", "-C", str(root)] + list(args), capture_output=True,
                           timeout=60, creationflags=_flags())
        return p.returncode, p.stdout.decode("utf-8", "replace").replace("\r\n", "\n")
    except Exception:                            # noqa: BLE001 - no git is "no answer"
        return 1, ""


def latest_sweep_report(od, slug):
    """(relpath, text) of the latest sweep report for `slug`, or None.

    Names are listed from the canonical ref AND from disk: `fresh land` commits a report
    straight to the ref without touching any working tree, so a checkout that has not moved
    does not have it yet, and a doc checked in a scratch folder has only the disk. Then ONLY
    the latest is read - one `git show` at most, however many reports have piled up (this runs
    inside every `orchdoc.py check`). The ref's copy wins when both hold it."""
    rel_dir = "%s/%s" % (SWEEP_DIR, slug)
    on_ref, on_disk = set(), set()
    disk = Path(od.PROJECTS) / SWEEP_DIR / slug
    try:
        on_disk = set(n for n in (os.listdir(disk) if disk.is_dir() else [])
                      if sweep_report_key(n) is not None)
    except OSError:
        pass
    ref = getattr(od, "CANONICAL_REF", None)
    if ref:
        rc, out = _git_bytes(od.PROJECTS, ["ls-tree", "--name-only", ref, "--", rel_dir + "/"])
        if rc == 0:
            on_ref = set(Path(r.strip()).name for r in out.split("\n")
                         if r.strip() and sweep_report_key(r.strip()) is not None)
    names = on_ref | on_disk
    if not names:
        return None
    name = max(names, key=sweep_report_key)
    rel = "%s/%s" % (rel_dir, name)
    if name in on_ref:
        rc, text = _git_bytes(od.PROJECTS, ["show", "%s:%s" % (ref, rel)])
        if rc == 0:
            return rel, text
    try:
        return rel, (disk / name).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def check_sweep_open(d):
    """The latest sweep report has action rows and nothing in the doc cites it.

    Citing the report is the cheapest proof that someone read it: the entry that records
    what was done about a row names the file it came from. Only the LATEST report counts -
    an older one was either acted on or superseded by a newer sweep of the same boxes."""
    od = d.od
    if not d.slug:
        return []
    latest = latest_sweep_report(od, d.slug)
    if not latest:
        return []
    rel, text = latest
    n = sweep_action_rows(text)
    if n <= 0:
        return []
    for e in d.entries:
        if rel in e["body"].replace("\\", "/"):
            return []
    return [od.Finding(
        "W-SWEEPOPEN", 0,
        "the latest fresh-eyes sweep report %s has %d action row(s), and no entry in this doc "
        "cites it" % (rel, n),
        "read it, act on its rows, and cite %s in the entry that records what you did" % rel)]


# =============================================================================================
# =============================================================================================
#
# W-TITLEHIDESASK: a live entry the orchestrator owns has a statement that asks something of the
# human, and its TITLE says nothing of it. Advisory while that rate stands.
TITLE_ASK_RE = re.compile(
    r"(?i)\b(?:the human|owner|rulings?|rule[sd]?|decid\w*|decisions?|approv\w*|legal|questions?|answers?"
    r"|needs?|your|sign-?off|choose|pick)\b")


def check_title_hides_ask(d):
    od, out = d.od, []
    plate_ids = {e["id"] for e in d.entries if _is_live(e, od) and d.top(e["line"]) == "2"}
    for e in d.entries:
        if not _is_live(e, od) or od.owner_is_human(e["body"]):
            continue
        if TITLE_ASK_RE.search(re.sub(r"^\W*[A-Z]\d+\b", "", e["title"])) \
                or TITLE_ASK_RE.search(_name_title(od, e["title"])):
            continue
        lo = e["line"] + 1
        hi = e["line"] + len(e["body"].splitlines()) - 1
        for a, b in _units(d, lo, hi):
            if any(d.fenced[i] or d.folded[i] for i in range(a, b + 1)):
                continue
            first = d.lines[a - 1].strip()
            if (first.startswith(">") or first.startswith("#")
                    or od.STAMP_LINE_RE.match(d.lines[a - 1])
                    or od.CHECKED_BOX_RE.match(d.lines[a - 1])):
                continue
            unit = "\n".join(d.lines[a - 1:b])
            if od.NOTE_MARK in unit:
                continue
            live = STRUCK_SPAN_RE.sub(" ", HTML_COMMENT_RE.sub(" ", unit))
            if ruling_mark_re(od).search(live):
                continue
            if plate_ids & set(PROSE_ID_RE.findall(INLINE_CODE_RE.sub(" ", live))):
                continue
            m = _human_ask(live.replace("\n", " "), od)
            if m:
                out.append(od.Finding(
                    "W-TITLEHIDESASK", e["line"],
                    "%s asks something of the human ('%s', line %d), and its title does not say so: %s"
                    % (e["id"], m.group(0), a, _snip(e["title"], 80)),
                    "the human navigates by title. Name the ask in the title (what the human is deciding), "
                    "or lift the ask into its own §2 entry"))
                break
    return out


def _name_title(od, title):
    """"""
    n = _name(od)
    return n if n.lower() in title.lower() else ""


# A code claim rots visibly; this one reads as true forever. Advisory only (never auto-resolve):
# add `**Rechecked:** <date> - <command and output>` after looking, and it stays quiet for
# EXTERNAL_RECHECK_DAYS days.
EXTERNAL_RECHECK_DAYS = 7
EXTERNAL_SERVICE_RE = re.compile(
    r"(?i)\b(?:github actions|github|stripe|meta|resend"
    r"|vercel|cloudflare|slack|google|anthropic|openai)\b.{0,60}?"
    r"\b(?:is down|outage|stopped running|stopped|failing|billing (?:failure|problem|issue|lapse)|suspended|not running"
    r"|rate-?limited|quota|expired|revoked|unavailable)\b")
EXTERNAL_BLOCK_RE = re.compile(
    r"(?i)\b(?:nothing can|cannot|can't|blocked|until the human|waiting|needs? The human)\b")
RECHECKED_RE = re.compile(r"(?i)\*\*Re-?checked:?\*\*:?\s*(\d{4}-\d{2}-\d{2})")
OPENED_DATE_RE = re.compile(r"\*\*Opened:\*\*\s*(\d{4}-\d{2}-\d{2})")


def check_external_claim(d, today=None):
    import datetime
    od, out = d.od, []
    today = today or datetime.date.today()

    def _d(s):
        try:
            return datetime.date.fromisoformat(s)
        except ValueError:
            return None

    for e in d.entries:
        if not _is_live(e, od):
            continue
        dates = [_d(x) for x in RECHECKED_RE.findall(e["body"])]
        mo = OPENED_DATE_RE.search(e["body"])
        dates.append(_d(mo.group(1)) if mo else None)
        newest = max([x for x in dates if x], default=None)
        if newest and (today - newest).days <= EXTERNAL_RECHECK_DAYS:
            continue

        def _flag(line, m):
            return od.Finding(
                "W-EXTERNALCLAIM", line,
                "%s says an outside service is failing and blocks work ('%s'), and nothing "
                "has re-checked it in %d days (last dated %s)"
                % (e["id"], _snip(m.group(0), 60), EXTERNAL_RECHECK_DAYS,
                   newest.isoformat() if newest else "never"),
                "look at the service now, then add `**Rechecked:** %s - <command and its "
                "output>` to the entry, or strike the claim if it no longer holds"
                % today.isoformat())

        m = EXTERNAL_SERVICE_RE.search(e["title"])
        if m and EXTERNAL_BLOCK_RE.search(e["title"]) and not e["title"].rstrip().endswith("?"):
            out.append(_flag(e["line"], m))
            continue
        lo = e["line"] + 1
        hi = e["line"] + len(e["body"].splitlines()) - 1
        for a, b in _units(d, lo, hi):
            if any(d.fenced[i] or d.folded[i] for i in range(a, b + 1)):
                continue
            if (d.lines[a - 1].strip().startswith(">") or od.CHECKED_BOX_RE.match(d.lines[a - 1])
                    or od.STAMP_LINE_RE.match(d.lines[a - 1])):
                continue
            unit = " ".join(d.lines[a - 1:b])
            unit = INLINE_CODE_RE.sub(" ", STRUCK_SPAN_RE.sub(" ", HTML_COMMENT_RE.sub(" ", unit)))
            m = EXTERNAL_SERVICE_RE.search(unit)
            if m and EXTERNAL_BLOCK_RE.search(unit):
                out.append(_flag(a, m))
                break
    return out


# =============================================================================================
# entry point
# =============================================================================================

CHECKS = (
    ("P1", check_plate_owner),
    ("P2", check_human_line),
    ("P3", check_newer_sibling),
    ("P7", check_ruled_not_landed),
    ("P8", check_one_way_cite),
    ("P9a", check_no_subitems),
    ("P9b", check_unidentified),
    ("P9c", check_unnumbered),
    ("sweep-open", check_sweep_open),
    ("title-hides-ask", check_title_hides_ask),
    ("external-claim", check_external_claim),
)


def check(path, lines, entries, od):
    """Advisory findings for one doc. `od` is the orchdoc module that is calling.

    A check that raises is reported as W-UPKEEPCRASH and the rest still run: an advisory
    check must never be the reason the commit gate falls over.
    """
    global _NAME_MEMO
    _NAME_MEMO = {}
    try:
        try:
            d = _Doc(path, lines, entries, od)
        except Exception as ex:                  # noqa: BLE001 - reported, not swallowed
            return [od.Finding("W-UPKEEPCRASH", 0, "orchdoc_upkeep could not read the doc: %r"
                               % ex)]
        findings = []
        for label, fn in CHECKS:
            try:
                findings.extend(fn(d))
            except Exception as ex:              # noqa: BLE001 - reported, not swallowed
                findings.append(od.Finding(
                    "W-UPKEEPCRASH", 0, "orchdoc_upkeep %s (%s) raised %r"
                    % (label, fn.__name__, ex)))
        return findings
    finally:
        _NAME_MEMO = None
