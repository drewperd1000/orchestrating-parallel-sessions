#!/usr/bin/env python3
"""Every OrchDoc entry id in an orchestrator's final reply must be a clickable Reader link.

<!-- subject: orchestration -->
<!-- route-tags: reader link orchdoc entry id anchor stop hook point clickable file line pointer -->

The human: *"All of the items are supposed to be linked. Previously, they were linked perfectly
99% of the time ... a good indicator of needing to build the NEW system/process hook/script to
ensure that the output is followed properly."*

WHAT IT CHECKS, in the final assistant message of the turn (the Stop payload's
`last_assistant_message`, else the transcript):

  - every OrchDoc entry id (the prefixes in orchdoc_ids.PREFIXES) must sit inside a markdown link
  - a link to any other URL on this machine - the dashboard on 8790, another port - does not
    open the entry, so its ids fail like plain text;
    replacement is the Reader link of the entry that contains that line.

WHAT IT DOES NOT COUNT, and why each exclusion is there:

    function key: a SHAPE is not membership, which is the defect stop_check_anchored_question.py
    records about its own first version. Existence is read from `orchdoc.parse_entries`, the
    same parser the Reader uses, so archived entries count (the Reader opens them).
    and stands bare must be `[AB9](http://127.0.0.1:8787/#rule/AB9)`; same exclusions as ids.
    OrchDoc ids win a shape clash (prefix DA); today no rule code shares a prefix with an id.
  - ids inside fenced code or inline code - that is quoted material, not a pointer. A fence
    opened with N backticks or tildes closes only on a line of at least N of the same.
  - an id-shaped LABEL linked to a document that is not on this machine's web server or an
    pointer at an entry.

Exit 2 blocks and prints the replacement links, one per unlinked id, so fixing it is one edit.
Exit 0 allows. A session that is not an orchestrator passes the id check.

Script texts only: a file directly under the library (README.md, INDEX.md), `_build/`, a process
log or a note needs none. See SCRIPT FILES below; the URL comes from
`reader_scripts.reader_link`, which `python <scripts>/reader_scripts.py link <path>` prints.

about without going searching and wasting time. Can we figure out how to use the proper labels
pre_label_map.json (a missing or bad file is an empty map), else asks for the library id or the
working title. See PRE-LABELS below.

Errors: any exception inside main(), including a failed import of stop_check_unfinished.py,
exits 0 with a one-line note on stderr. A failed import of orchdoc_ids.py, which this module
needs to load, exits 1 with a traceback; Claude Code reports that as a non-blocking error.

There is no `stop_hook_active` skip. bounded_exit in stop_check_unfinished.py bounds repeat
blocks per session instead - see THE BLOCK BOUND there.

Mode (hook_mode.py, see THE MODE in stop_check_unfinished.py): off exits 0 at once; warn logs a
block it would have made to the warn log and exits 0; block is the behavior above. Set it to warn
in hook-modes.json before this hook is wired (GOAL-PLAN.md section 7).

Cost: a message with no candidate id or file:line pointer never imports orchdoc. When it does,
"""
import json
import os
import re
import sys
import unicodedata

try:
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from orchdoc_ids import PREFIXES, PRE_LABEL, _is_real_reference         # noqa: E402  the id grammar

# stop_check_unfinished (the reply and the block bound) is imported inside main()'s `try`, so
# a broken edit to that shared file skips this check with a note instead of exiting 1.

HOOK = "stop_check_reader_links"

# ---------------------------------------------------------------------------------------------
# Patterns. The id grammar itself comes from orchdoc_ids; these only say WHERE an id sits.
# ---------------------------------------------------------------------------------------------

# The doc prefix is case-insensitive; the id is not (entry ids are uppercase). Spaces and tabs
# only - an id at the start of the next line is not prefixed by a group ending this one.
ID_IN_PROSE = re.compile(
    r"(?:\b(?P<orch>[oO]\d+)(?::|/|['\u2019]s\s+|[ \t]+))?"
    r"\b(?P<id>(?:%s)\d+)\b" % "|".join(PREFIXES))

# Underscores that delimit markdown emphasis: a run not flanked by letters or digits on both
# sides. `_W98_` and `__W98__` lose them; the underscores inside `snake_case_W5` stay.
EMPHASIS_UNDERSCORES = re.compile(r"(?<![A-Za-z0-9_])_+|_+(?![A-Za-z0-9_])")

ONE_CAPITAL_SERIES = re.compile(r"(?:^|[^A-Za-z0-9])[A-Z]-$")

def _reader_ports():
    """8787, and the `reader_port` setting when one is set: the links the tools print use it."""
    try:
        import orchdoc_config
        port = str(orchdoc_config.load().get("reader_port", "")).strip()
    except Exception:
        port = ""
    return "8787|%s" % port if port.isdigit() and port != "8787" else "8787"


# The Reader URL. Port 8787 is orchdoc.READER_PORT; localhost is accepted as the same place.
READER_URL = re.compile(
    r"^<?https?://(?:127\.0\.0\.1|localhost):(?:" + _reader_ports() + r")/#(?P<orch>o\d+)/(?P<id>[A-Z]{1,3}\d+[a-z]?)"
    r"(?:\.n\d+)?/?>?$")

# Any other URL on this machine: the daily dashboard (8790), another port, or a Reader URL that
# names no entry. None of these opens the entry, so ids linked there are reported.
LOCAL_URL = re.compile(r"^<?https?://(?:127\.0\.0\.1|localhost)(?::\d+)?(?:[/?#]|>?$)", re.I)
FRAGMENT_DOC = re.compile(r"#(o\d+)/[A-Z]{1,3}\d+")

ORCHDOC_FILE = re.compile(r"ORCHESTRATOR-DECISIONS-(o\d+)\.md", re.I)

# A markdown link. Labels may hold one level of nested brackets.
MD_LINK = re.compile(r"\[(?P<label>(?:[^\[\]\n]|\[[^\[\]\n]*\])*)\]\((?P<target>[^()\s]*(?:\([^()\s]*\)[^()\s]*)*)(?:\s+\"[^\"]*\")?\)")

# A fence line: three or more backticks or tildes after any indent.
FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
INLINE_CODE = re.compile(r"(`+)(?!`)(.+?)(?<!`)\1(?!`)")
BARE_URL = re.compile(r"<?\bhttps?://[^\s)>\]]+>?")
PATH_TOKEN = re.compile(r"[\w.~:-]*[/\\][\w./\\~-]*\.[A-Za-z]\w{0,5}\b")

# What separates an OrchDoc filename from a line number written after it: `, line 82`,
# `: line 82`, ` - L82`, ` (L82)`.
_SEP = r"[\s,;:(\-\u2013\u2014]{1,4}"
_LINE = r"(?:at\s+)?(?:lines?\s+|L)(?P<line>\d+)\b"

# File-and-line pointers. Each yields (orch or None, line).
FILE_LINE_PATTERNS = (
    # (`#15992` without the L is a heading anchor such as #21-decisions, not a line.)
    re.compile(r"ORCHESTRATOR-DECISIONS-(?P<orch>o\d+)\.md(?::L?|#L)(?P<line>\d+)", re.I),
    re.compile(r"ORCHESTRATOR-DECISIONS-(?P<orch>o\d+)\.md[`*_\]]*(?:\([^)\s]*\))?[`*_]*"
               + _SEP + _LINE, re.I),
    re.compile(r"(?:\blines?\s+|\bL)(?P<line>\d+)\s+(?:of|in)\s+[`*_\[]*"
               r"ORCHESTRATOR-DECISIONS-(?P<orch>o\d+)\.md", re.I),
    # OrchDoc line 82 / the OrchDoc, line 82 / OrchDoc L82 - the session's own doc
    re.compile(r"\bOrchDoc[`*_\]]*" + _SEP + _LINE, re.I),
)


# A script-rule code such as AB9 opens at http://127.0.0.1:8787/#rule/AB9. The boundary rule is
# the Reader's own (render_orchdoc_reader._RULE_SCAN): a code counts only as its own token, not
# after a word character, `.`, `:`, `#`, `/` or a backslash, and not before a file extension; a
# range or pair (`RC1-RC7`, `AR7/AR9`) counts on both sides.
RULE_URL = "http://127.0.0.1:8787/#rule/%s"
_RULE_LEAD = r"(?<![\w/.:#\\-])[A-Z]{2}"
RULE_CODE = re.compile(
    r"(?:(?<![\w/.:#\\-])"
    r"|(?<=" + _RULE_LEAD + r"[0-9][/-])"
    r"|(?<=" + _RULE_LEAD + r"[0-9]{2}[/-])"
    r"|(?<=" + _RULE_LEAD + r"[0-9]{3}[/-]))"
    r"(?P<code>[A-Z]{2}[0-9]{1,3})(?!\w)(?!\.[A-Za-z])")


def rule_findings(text):
    """[(bad, replacement, offset, names)] for each distinct rule code standing bare in `text`.

    A code counts only if rule_codes_index.load() has its box (a SHAPE is not membership). Code
    in fences or inline code, inside any markdown link, a URL or a path is left alone. A code
    that is also an OrchDoc id shape (the two-letter prefix DA) is read as the OrchDoc id."""
    if not text:
        return []
    s = strip_code(text)
    s = _blank_spans(s, [m.span() for m in MD_LINK.finditer(s)])
    for rx in (BARE_URL, PATH_TOKEN):
        s = _blank_spans(s, [m.span() for m in rx.finditer(s)])
    s = _blank_emphasis(s)
    hits = [m for m in RULE_CODE.finditer(s) if m.group("code")[:2] not in PREFIXES]
    if not hits:
        return []                                # no candidate: the index is never read
    try:                                         # a broken index skips ONLY the rule check
        import rule_codes_index
        codes = rule_codes_index.load()["codes"]
    except Exception as exc:
        sys.stderr.write("[reader-links] rule check skipped: %s: %s\n" % (type(exc).__name__, exc))
        return []
    out, seen = [], set()
    for m in hits:
        c = m.group("code")
        if c not in codes or c in seen:
            continue
        seen.add(c)
        title = plain_title(c, codes[c].get("title") or "")
        out.append((c, "[%s](%s)" % (c, RULE_URL % c), m.start("code"),
                    'rule %s: "%s"' % (c, title) if title else "rule %s" % c))
    return out


_NOT_NEWLINE = re.compile(r"[^\n]")


def _blank_spans(text, spans):
    """`text` with each (start, end) span turned to spaces, keeping every offset and newline.

    ⛔ BUILT IN ONE PASS.
    """
    out, prev = [], 0
    for start, end in sorted(spans):
        start = max(start, prev)                # an overlap is already blank
        if end <= start:
            continue
        out.append(text[prev:start])
        out.append(_NOT_NEWLINE.sub(" ", text[start:end]))
        prev = end
    out.append(text[prev:])
    return "".join(out)


def _blank_emphasis(text):
    """The text with emphasis underscores turned to spaces, every offset unchanged."""
    return EMPHASIS_UNDERSCORES.sub(lambda m: " " * len(m.group()), text)


def _strip_fences(text):
    """The message with fenced lines blanked to spaces, so every offset still lines up."""
    return "\n".join(" " * len(line) if fenced else line for line, fenced in _fenced_lines(text))


def strip_code(text):
    """The message with fenced blocks and inline code spans blanked out."""
    s = _strip_fences(text)
    return _blank_spans(s, [m.span() for m in INLINE_CODE.finditer(s)])


def _ids_in(text):
    """[(orch or None, id, offset)] for every id-shaped token that is a real reference."""
    t = _blank_emphasis(text)
    out, kept = [], {}                          # end offset of each kept id -> its doc
    for m in ID_IN_PROSE.finditer(t):
        start = m.start("id")
        if not _is_real_reference(t, start):
            continue
        lead = m.start()
        if lead == start and ONE_CAPITAL_SERIES.search(t[max(0, start - 3):start]):
            continue
        before = t[lead - 1] if lead > 0 else ""
        orch = (m.group("orch") or "").lower() or None
        if before == "/" and lead - 1 in kept:
            orch = orch or kept[lead - 1]
        elif before and before in "#/\\._@$":
            continue
        kept[m.end()] = orch
        out.append((orch, m.group("id"), start))
    return out


def _link_problem(target):
    """(why, doc or None) when a link target should open an entry and does not, else None.

    `doc` is the OrchDoc the target names, read for a label id written without one."""
    fm = ORCHDOC_FILE.search(target)
    if LOCAL_URL.match(target):
        fd = FRAGMENT_DOC.search(target)
        doc = fd.group(1) if fd else (fm.group(1).lower() if fm else None)
        return "linked to %s, which does not open the entry" % target.strip("<>")[:60], doc
    if fm:
        return "linked to the .md file, which opens at line 1", fm.group(1).lower()
    return None


def scan(text):
    """Structural findings for a message, before any doc is read.

    Returns (candidates, file_lines):
      candidates  [(orch or None, id, why, offset)] - ids not sitting in their own Reader link
      file_lines  [(orch or None, line, raw)] - file-and-line pointers
    `orch` None means "the session's own doc".
    """
    if not text:
        return [], []
    # Ids are the other way round: an id in inline code is quoted material and is not checked.
    file_lines, seen = [], set()
    fence_free = _strip_fences(text)
    for rx in FILE_LINE_PATTERNS:
        for m in rx.finditer(fence_free):
            orch = (m.groupdict().get("orch") or "").lower() or None
            key = (orch, int(m.group("line")))
            if key not in seen:
                seen.add(key)
                file_lines.append((orch, key[1], m.group(0).strip()))

    code_free = strip_code(text)
    candidates, links = [], []
    for m in MD_LINK.finditer(code_free):
        label, target = m.group("label"), m.group("target")
        at = m.start("label")
        rm = READER_URL.match(target)
        if rm:
            for orch, eid, off in _ids_in(label):
                if eid != rm.group("id") or (orch and orch != rm.group("orch")):
                    candidates.append((orch, eid, "linked to the Reader URL of %s/%s"
                                       % (rm.group("orch"), rm.group("id")), at + off))
        else:
            problem = _link_problem(target)
            if problem:
                why, doc = problem
                for orch, eid, off in _ids_in(label):
                    candidates.append((orch or doc, eid, why, at + off))
        # Any other target: an id-shaped label linked to an ordinary document is not a pointer
        # at an entry. Either way the whole link is consumed so its URL is not re-read as prose.
        links.append(m.span())
    s = _blank_spans(code_free, links)
    for rx in (BARE_URL, PATH_TOKEN):
        s = _blank_spans(s, [m.span() for m in rx.finditer(s)])
    for orch, eid, off in _ids_in(s):
        candidates.append((orch, eid, "plain text", off))
    candidates.sort(key=lambda c: c[3])
    return candidates, file_lines


def _fenced_lines(text):
    """(line, fenced) for each line. A fence opened with N backticks or tildes closes only on a
    line holding nothing but at least N of the same character, as in CommonMark."""
    fence = None                                # (char, length) of the open fence
    for line in (text or "").split("\n"):
        m = FENCE.match(line)
        if fence is None:
            if m:
                fence = (m.group(1)[0], len(m.group(1)))
            yield line, bool(m)
            continue
        if (m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1]
                and not line[m.end():].strip()):
            fence = None
        yield line, True


ARCHIVED_SUFFIX = re.compile(r"\s*_\(was\s+[A-Z]+\d+\s*[-\u2013\u2014]\s*[A-Z]+\)_\s*$")
# Punctuation a heading commonly holds, written in ASCII so the block message stays ASCII.
_TO_ASCII = {"\u2014": "-", "\u2013": "-", "\u2012": "-", "\u2015": "-", "\u2212": "-",
             "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2026": "...",
             "\u2192": "->", "\u2190": "<-", "\u00a0": " "}
TITLE_CHARS = 80


def plain_title(eid, heading, strip_decoration=None):
    """An entry's heading as its title alone, in ASCII: no leading emoji or markup, no leading
    id, no archive suffix, at most TITLE_CHARS characters."""
    t = ARCHIVED_SUFFIX.sub("", heading or "")
    if strip_decoration:
        t = strip_decoration(t)
    t = re.sub(r"^%s\b\s*[-:\u2013\u2014]?\s*" % re.escape(eid), "", t)
    t = "".join(_TO_ASCII.get(c, c) for c in t)
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode("ascii")
    t = " ".join(t.replace("**", "").split()).strip(" *_`~")
    if len(t) > TITLE_CHARS:
        t = t[:TITLE_CHARS - 3].rstrip() + "..."
    return t


class Docs:
    """Entries per OrchDoc, read through orchdoc.parse_entries - the Reader's own parser."""

    def __init__(self, workspace):
        self.workspace = workspace
        self._cache = {}
        self._headings = {}
        self._orchdoc = None

    def orchdoc(self):
        if self._orchdoc is None:
            import orchdoc                       # lazy: only when there is something to check
            self._orchdoc = orchdoc
        return self._orchdoc

    def entries(self, orch):
        """{id: (first_line, last_line)} for a doc, or None when the doc does not exist."""
        if orch in self._cache:
            return self._cache[orch]
        path = os.path.join(self.workspace, "ORCHESTRATOR-DECISIONS-%s.md" % orch)
        got, heads = None, {}
        if os.path.isfile(path):
            with open(path, encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
            ents, _ = self.orchdoc().parse_entries(lines)
            got = {}
            for e in ents:
                if e["id"] in got:
                    continue                     # first heading wins, as the Reader's anchor does
                got[e["id"]] = (e["line"], e["line"] + e["body"].count("\n"))
                heads[e["id"]] = e.get("title") or ""
        self._cache[orch] = got
        self._headings[orch] = heads
        return got

    def link(self, orch, eid):
        return self.orchdoc().reader_link("ORCHESTRATOR-DECISIONS-%s.md" % orch, eid)

    def names(self, orch, eid):
        """"""
        self.entries(orch)
        title = plain_title(eid, self._headings.get(orch, {}).get(eid, ""),
                            getattr(self.orchdoc(), "strip_decoration", None))
        return "%s's %s: \"%s\"" % (orch, eid, title) if title else "%s's %s" % (orch, eid)

    def entry_at(self, orch, line):
        """The id of the innermost entry whose span contains `line`, or None."""
        best = None
        for eid, (a, b) in (self.entries(orch) or {}).items():
            if a <= line <= b and (best is None or a > best[1]):
                best = (eid, a)
        return best[0] if best else None


def findings(text, orch, workspace):
    """[(bad, replacement, offset, names)] for a message from session `orch`, or [] when it
    passes.

    `offset` is where the id sits in the message (None for a file:line pointer) - for a
    reviewer checking a verdict, not for the block message.
    """
    if not text or not orch:
        return []
    candidates, file_lines = scan(text)
    if not candidates and not file_lines:
        return rule_findings(text)
    docs = Docs(workspace)
    out, seen = [], set()
    for c_orch, eid, why, off in candidates:
        doc = c_orch or orch
        ents = docs.entries(doc)
        if not ents or eid not in ents:
            continue                             # not an entry in that doc: not a pointer
        shown = eid if doc == orch else "%s:%s" % (doc, eid)
        key = ("id", doc, eid)
        if key in seen:
            continue
        seen.add(key)
        was = shown if why == "plain text" else "%s (%s)" % (shown, why)
        out.append((was, "[%s](%s)" % (shown, docs.link(doc, eid)), off, docs.names(doc, eid)))
    for f_orch, line, raw in file_lines:
        doc = f_orch or orch
        key = ("line", doc, line)
        if key in seen:
            continue
        seen.add(key)
        if docs.entries(doc) is None:
            continue
        eid = docs.entry_at(doc, line)
        if eid:
            shown = eid if doc == orch else "%s:%s" % (doc, eid)
            out.append((raw, "[%s](%s)" % (shown, docs.link(doc, eid)), None,
                        docs.names(doc, eid)))
        else:
            out.append((raw, "(line %d is in no entry - name the entry id and link it)" % line,
                        None, ""))
    return out + rule_findings(text)


#
# Every library path in the reply - in prose, inline code or a fence, since a path in
# backticks is exactly how that one was written - needs, somewhere in the same reply, a markdown
# link to that file's Reader URL (`reader_scripts.reader_link`, the stable `#scripts/p/<path>`
# form). A script FOLDER needs a link to its record file. Only paths that EXIST under the
# library count, and only what the Reader lists (`reader_scripts.locate`). This check runs for
# every session, orchestrator or not: lanes report script files to their orchestrator.
# ---------------------------------------------------------------------------------------------

LIBRARY_HIT = re.compile(r"(?!)")
# Characters that end a path: a path in prose stops at one of these; spaces are allowed, since
# script names hold them, and the longest prefix that exists on disk is the path.
_PATH_STOP = set("\n\r\t`\"<>|*[]")
_PATH_BACK_STOP = _PATH_STOP | set(" (")
# A path may end before one of these (or at the end of the run).
_PATH_END_BEFORE = set(" ,.;:)!?'/\\")
_PATH_MAX = 400
MAX_RUNS = 300
SCRIPT_LINK = re.compile(
    r"^<?https?://(?:127\.0\.0\.1|localhost):8787/#scripts/p/(?P<rel>[^>\s]+?)/?>?$", re.I)
LINK_CLI = "python <scripts>/reader_scripts.py link \"<path>\""


def _script_mentions(text):
    """
    """
    out = []
    for m in LIBRARY_HIT.finditer(text):
        a = m.start()
        while a > 0 and text[a - 1] not in _PATH_BACK_STOP:
            a -= 1
        b = m.end()
        while b < len(text) and b - a < _PATH_MAX and text[b] not in _PATH_STOP:
            b += 1
        out.append((a, text[a:b]))
    return out


def _exists_under(run, workspace, rs):
    """(the longest prefix of `run` naming something that exists under a document library, is it a
    folder), or (None, False).
    A prefix ends at the end of the run or before a space, punctuation or a slash."""
    ends = [k for k in range(len(run), 0, -1)
            if run[k - 1] not in " \\/" and (k == len(run) or run[k] in _PATH_END_BEFORE)]
    lib = os.path.join(workspace, rs.LIBRARY_REL)
    for k in ends:
        s = run[:k]
        split = rs._library_parts(s)
        if not split or not split[1] or any(p in (".", "..") for p in split[1]):
            continue
        for p in (os.path.join(lib, *split[1]), s if os.path.isabs(s) else None):
            if p and os.path.exists(p):
                return s, os.path.isdir(p)
    return None, False


def _linked_script_rels(text):
    """The paths under the corpus root that the reply links with a `#scripts/p/` Reader URL,
    case-folded. Links inside code are not clickable, so they do not count."""
    import urllib.parse
    got = set()
    for m in MD_LINK.finditer(strip_code(text)):
        sm = SCRIPT_LINK.match(m.group("target"))
        if sm:
            got.add("/".join(urllib.parse.unquote(s) for s in sm.group("rel").split("/"))
                    .casefold())
    return got


def is_script_text(rel, rs):
    """A file directly under the library (README.md, INDEX.md, any other) is not one, and neither
    is a process log or a note. The kind is the Reader's own `classify`, so a name the
    shared parser cannot read still counts as a draft, and a renamed library keeps asking
    for links. `_build/` and other underscore folders never get this far: `locate` refuses
    them."""
    if "/" not in rel:
        return False
    return rs.classify(rel.rsplit("/", 1)[-1]) in ("draft", "rewrite")


def script_findings(text, workspace):
    """[(as written, replacement link, offset, what it opens)] for each library path in
    `text` with no Reader link to it anywhere in the message; [] when every one has one."""
    if not text or not LIBRARY_HIT.search(text):
        return []                                # no candidate: reader_scripts is never imported
    try:
        import reader_scripts as rs
    except ImportError:                          # a setup with no document library (the public copy)
        return []
    linked = None
    out, seen, runs = [], set(), set()
    for start, run in _script_mentions(text):
        # Each distinct run is read once, and at most MAX_RUNS of them: a run costs up to one
        # stat per space or punctuation mark in it, and a reply can repeat a path many times.
        if run in runs or len(runs) >= MAX_RUNS:
            continue
        runs.add(run)
        s, folder = _exists_under(run, workspace, rs)
        if not s:
            continue                             # not a file in the library: not a pointer
        rel, _why = rs.locate(s, workspace)
        if not rel or rel.casefold() in seen or not is_script_text(rel, rs):
            continue
        seen.add(rel.casefold())
        if linked is None:
            linked = _linked_script_rels(text)
        if rel.casefold() in linked:
            continue
        name = rel.rsplit("/", 1)[-1]
        label = name[:-3] if name.lower().endswith(".md") else name
        out.append((s, "[%s](%s)" % (label.replace("[", "(").replace("]", ")"),
                                     rs.link_for_rel(rel)), start,
                    "the folder's script, %s" % name if folder else ""))
    return out


# ---------------------------------------------------------------------------------------------
# 'Decision' item, the script should also be linked."* A reply that names a script by NAME alone
# needs the same Reader link a path needs.
#
# The name SHAPE is training_header's own (`_SN_NAME`, `SCRIPT_NAME`), the one reader_scripts.
# rename_key reads: this module writes no pattern of its own for it. NAME_TRIGGER decides only
# whether to import anything. A name counts only when it resolves to a script in the library
#   - a full file name (key, version, author; the title is used when it matches) is met by a
#     link to that file;
#   - a bare key is met by a link to ANY file in its script folder (a `versions/` folder counts
#     as its script's folder); its replacement is the folder's record file, the file the path
#     check gives a folder (reader_scripts.locate), else the best file by check_script.find_pair's
#     ranking: live over versions/, not retired, `(in app)`, highest version.
# Like the path check, a name in inline code or in a fence IS checked (the link must be a real
# link: one inside code does not count). Link targets and bare URLs are not read. A name right
# after `/`, a backslash, `.`, `#`, `@` or `-`, or right before `/` or a backslash, is part of
# a path or a longer token and is skipped; the path check owns paths. Process logs, notes,
# README, INDEX and `_build/` have no entry in the index, so they need nothing.
# ---------------------------------------------------------------------------------------------

NAME_TRIGGER = re.compile(r"[A-Z]{2,4}-[A-Za-z0-9]")
_NAME_FULL_WORDS = 7                             # name, title, vN, author, duplicate, (in app)
MAX_NAMES = 300


def _name_candidates(text, th):
    """[(start, key, full SCRIPT_NAME match or None, text as written)] for each name-shaped token."""
    pat = re.compile(r"(?<![\w/\\.#@-])" + th._SN_NAME + r"(?![\w/\\@])")
    out = []
    for m in pat.finditer(text):
        a = m.start()
        nl = text.find("\n", a)
        seg = text[a:min(len(text) if nl < 0 else nl, a + 300)]
        ends = [w.end() for w in re.finditer(r"\S+", seg)][:_NAME_FULL_WORDS]
        if re.match(r"(?: \S+)? notes - ", seg[len(m.group()):]):
            continue                             # a note (`<NAME> notes - <old name>.md`)
        full = None
        for end in reversed(ends):
            cut = seg[:end].split("]", 1)[0]
            for v in (seg[:end], seg[:end].rstrip(",;:!?)*`'\"."), cut,
                      cut.rstrip(",;:!?)*`'\".")):
                fm = th.SCRIPT_NAME.match(v if v.endswith(".md") else v + ".md")
                if fm and fm.group("name") == m.group("name"):
                    full = (fm, v)
                    break
            if full:
                break
        if full and full[0].group("log"):
            continue                             # a process log or review: no script text
        out.append((a, m.group("name"), full[0] if full else None,
                    full[1] if full else m.group()))
    return out


def _library_index(workspace, rs, th):
    """{name key: [entry]} for the script texts in the library, listed as the Reader lists them
    (hidden and underscore folders skipped, MAX_DEPTH deep, nothing directly under the library).
    entry = (rel, version, author, title, in_app, duplicate)."""
    lib = os.path.join(workspace, rs.LIBRARY_REL)
    idx = {}
    for dirpath, dirnames, filenames in os.walk(lib):
        dirnames[:] = [d for d in dirnames if not d.startswith((".", "_"))]
        rel_dir = os.path.relpath(dirpath, lib).replace("\\", "/")
        if rel_dir == ".":
            continue
        if rel_dir.count("/") + 1 >= rs.MAX_DEPTH:
            dirnames[:] = []
        for fn in filenames:
            m = th.SCRIPT_NAME.match(fn)
            if not m or m.group("log"):
                continue
            idx.setdefault(m.group("name"), []).append(
                ("%s/%s" % (rel_dir, fn), int(m.group("ver")), m.group("author"),
                 m.group("title") or "", bool(m.group("app")), bool(m.group("dup"))))
    return idx


def _script_dir(rel):
    d = rel.rsplit("/", 1)[0] if "/" in rel else ""
    return d[:-len("/versions")] if d.casefold().endswith("/versions") else d


def _entry_rank(e):
    parts = [x.casefold() for x in e[0].split("/")]
    return ("versions" in parts, "4-retired" in parts, not e[4], -e[1], e[0].casefold())


def name_findings(text, workspace):
    """[(as written, replacement link, offset, what it opens)] for each script named by name
    with no Reader link to it in the message; [] when none."""
    if not text or not NAME_TRIGGER.search(text):
        return []                                # no candidate: reader_scripts is never imported
    try:
        import reader_scripts as rs
        import training_header as th
    except ImportError:                          # a setup with no document library (the public copy)
        return []
    spans = [m.span("target") for m in MD_LINK.finditer(text)]
    spans += [m.span() for m in BARE_URL.finditer(text)]
    cands = _name_candidates(_blank_spans(text, spans) if spans else text, th)
    if not cands:
        return []
    idx = linked = None
    out, seen = [], set()
    for start, key, fm, written in cands[:MAX_NAMES]:
        if idx is None:
            idx = _library_index(workspace, rs, th)
        entries = idx.get(key)
        if not entries:
            continue                             # a look-alike: no such script
        match = []
        if fm:
            ver, auth = int(fm.group("ver")), fm.group("author")
            match = [e for e in entries if e[1] == ver and e[2] == auth
                     and e[4] == bool(fm.group("app")) and e[5] == bool(fm.group("dup"))] \
                or [e for e in entries if e[1] == ver and e[2] == auth]
            if fm.group("title"):
                match = [e for e in match if e[3] == fm.group("title")] or match
        ident = ("file", key, tuple(sorted(e[0].casefold() for e in match))) if match \
            else ("key", key)
        if ident in seen:
            continue
        seen.add(ident)
        if linked is None:
            linked = _linked_script_rels(text)
        if match:
            # A link to any file of that key, version and author meets it: a name written
            # without `(in app)` or its title still means the file the reply links.
            if any(e[0].casefold() in linked for e in entries
                   if e[1] == match[0][1] and e[2] == match[0][2]):
                continue
            rel = min(match, key=_entry_rank)[0]
            as_written = written
        else:
            folders = {_script_dir(e[0]).casefold() for e in entries}
            if any(_script_dir(r).casefold() in folders for r in linked):
                continue
            best = min([e for e in entries if not e[5]] or entries, key=_entry_rank)
            rel, _why = rs.locate(os.path.join(workspace, rs.LIBRARY_REL,
                                               *_script_dir(best[0]).split("/")), workspace)
            rel = rel or best[0]
            as_written = key
        fname = rel.rsplit("/", 1)[-1]
        label = fname[:-3] if fname.lower().endswith(".md") else fname
        out.append((as_written, "[%s](%s)" % (label.replace("[", "(").replace("]", ")"),
                                              rs.link_for_rel(rel)), start,
                    "opens %s" % fname))
    return out


# See the module docstring. The map is pre_label_map.json beside this file.
# ---------------------------------------------------------------------------------------------

PRE_LABEL_MAP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pre_label_map.json")


def _load_pre_label_map(path=None):
    """{label: library id} from the JSON file; {} when it is missing, unreadable or not a dict."""
    try:
        with open(path or PRE_LABEL_MAP, encoding="utf-8") as fh:
            data = json.load(fh)
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except Exception:
        return {}


def pre_label_findings(text, workspace, map_path=None):
    """[(label, advice, offset, "")] for each distinct bare pre-label in `text`."""
    if not text:
        return []
    s = strip_code(text)
    spans = [m.span("target") for m in MD_LINK.finditer(s)]
    for rx in (BARE_URL, PATH_TOKEN):
        spans += [m.span() for m in rx.finditer(s)]
    s = _blank_spans(s, spans)
    hits = list(PRE_LABEL.finditer(s))
    if not hits:
        return []
    pmap = _load_pre_label_map(map_path)
    out, seen = [], set()
    for m in hits:
        label = m.group()
        if label in seen:
            continue
        seen.add(label)
        lib_id = pmap.get(label)
        if lib_id:
            link = ""
            try:
                found = name_findings(lib_id, workspace)
                if found:
                    link = found[0][1]
            except Exception:
                link = ""
            if link:
                advice = "%s is %s; write %s" % (label, lib_id, link)
            else:
                advice = ("%s is %s (that script is not on disk, so there is no Reader link; "
                          "write the id)" % (label, lib_id))
        else:
            advice = ("name the script by its library id with its Reader link, or by its working "
                      "title if it is not filed yet; keep pre-labels inside inline code for "
                      "internal notes")
        out.append((label, advice, m.start(), ""))
    return out


def report_pre_labels(bad):
    lines = ["STOP BLOCKED - %d pre-label%s in your reply.\n"
             % (len(bad), "" if len(bad) == 1 else "s"),
             "the human cannot tell which script a label like `X-1` means without searching. Name the script",
             "by its library id with its Reader link. Pre-labels are fine inside inline code, for",
             "your own notes. Replace each one:\n"]
    for was, advice, _off, _n in bad:
        lines.append("    %s -> %s" % (was, advice))
    return "\n".join(lines) + "\n"


def report_names(bad):
    lines = ["STOP BLOCKED - %d script name%s in your reply %s no Reader link.\n"
             % (len(bad), "" if len(bad) == 1 else "s", "has" if len(bad) == 1 else "have"),
             "A script named like an OrchDoc id needs its link, so the human opens it from the reply",
             "instead of hunting the Scripts view. Add each link below anywhere in the reply",
             "(keep the name if you want it):\n"]
    for was, new, _off, names in bad:
        lines.append("    %s -> %s   (%s)" % (was, new, names))
    lines.append("\nThe link of any script file or folder: %s" % LINK_CLI)
    lines.append("A bare name links its folder's script: the record file, else the best file.")
    lines.append("Names that match no script in the library are not checked.")
    return "\n".join(lines) + "\n"


def report_scripts(bad):
    lines = ["STOP BLOCKED - %d library path%s in your reply %s no Reader link.\n"
             % (len(bad), "" if len(bad) == 1 else "s", "has" if len(bad) == 1 else "have"),
             "the human reads scripts in the Reader's Scripts view. A path alone means scrolling the",
             "list to find the file. Add each link below anywhere in the reply (keep the path if",
             "you want it):\n"]
    for was, new, _off, names in bad:
        lines.append("    %s -> %s%s" % (was, new, "   (" + names + ")" if names else ""))
    lines.append("\nThe link of any script file or folder: %s" % LINK_CLI)
    lines.append("A folder links its script: the record file, else its newest draft or rewrite.")
    return "\n".join(lines) + "\n"


def report(bad, orch):
    lines = ["STOP BLOCKED - %d OrchDoc pointer%s in your reply %s not a Reader link.\n"
             % (len(bad), "" if len(bad) == 1 else "s", "is" if len(bad) == 1 else "are"),
             "the human opens entries in the Reader. A plain id or a file:line means searching for",
             "it, which is the cost the link exists to remove. Replace each one:\n"]
    for was, new, _off, names in bad:
        lines.append("    %s -> %s%s" % (was, new, "   " + names if names else ""))
    lines.append("\nEach line names the entry its link opens. A bare id is read as YOUR doc (%s)."
                 % orch)
    lines.append("If a title is not the entry you meant, do not paste that link: write the entry")
    lines.append("you meant, as o<N>:<id> when it is another group's, and link that entry.")
    lines.append("Every `orchdoc.py add` and `plate` run prints these links; paste them rather")
    lines.append("than writing ids by hand. Ids in fenced or inline code are not checked.")
    if any("#rule/" in new for _w, new, _o, _n in bad):
        lines.append("A rule code (AB9, CD7) opens its rule in the Reader at #rule/<CODE>.")
    return "\n".join(lines) + "\n"


def _session():
    """(orch, workspace) via orchdoc_stop_check - the same identity every Stop hook uses."""
    import orchdoc_stop_check
    orch, _ws = orchdoc_stop_check._whose_session()
    return orch, str(orchdoc_stop_check.PROJECTS)


# A setup without stop_check_unfinished.py (the published copy) still checks the links. The reply is
# the Stop payload's own field, and one block per stop is the bound: Claude Code sets
# stop_hook_active on the stop that follows a block, and that stop passes.
def _plain_final_text(payload):
    msg = payload.get("last_assistant_message") if isinstance(payload, dict) else None
    return msg if isinstance(msg, str) else None


def _plain_mode(_hook):
    return "block"


def _plain_bounded_exit(_hook, payload, _text, code, message, _mode):
    if isinstance(payload, dict) and payload.get("stop_hook_active") is True:
        return 0
    sys.stderr.write(message + "\n")
    return code


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0
    try:
        try:
            from stop_check_unfinished import bounded_exit, final_text, run_mode
        except ModuleNotFoundError as exc:
            if exc.name != "stop_check_unfinished":
                raise
            bounded_exit, final_text, run_mode = _plain_bounded_exit, _plain_final_text, _plain_mode
        mode = run_mode(HOOK)
        if mode == "off":
            return 0
        orch, workspace = _session()
        text = final_text(payload)
        bad = findings(text, orch, workspace) if orch else []
        try:
            sbad = script_findings(text, workspace)
        except Exception as exc:                 # a broken script check skips only itself
            sys.stderr.write("[reader-links] script check skipped: %s: %s\n"
                             % (type(exc).__name__, exc))
            sbad = []
        try:
            nbad = name_findings(text, workspace)
        except Exception as exc:                 # a broken name check skips only itself
            sys.stderr.write("[reader-links] name check skipped: %s: %s\n"
                             % (type(exc).__name__, exc))
            nbad = []
        try:
            pbad = pre_label_findings(text, workspace)
        except Exception as exc:                 # a broken pre-label check skips only itself
            sys.stderr.write("[reader-links] pre-label check skipped: %s: %s\n"
                             % (type(exc).__name__, exc))
            pbad = []
        message = "\n".join(m for m in (report(bad, orch) if bad else "",
                                        report_scripts(sbad) if sbad else "",
                                        report_names(nbad) if nbad else "",
                                        report_pre_labels(pbad) if pbad else "") if m)
        bad = bad + sbad + nbad + pbad
    except Exception as exc:                     # fail OPEN, and say so
        sys.stderr.write("[reader-links] check skipped: %s: %s\n" % (type(exc).__name__, exc))
        return 0
    if not bad:
        return 0
    return bounded_exit(HOOK, payload, text, 2, message, mode)


if __name__ == "__main__":
    sys.exit(main())
