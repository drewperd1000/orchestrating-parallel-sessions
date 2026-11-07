#!/usr/bin/env python3
"""ONE definition of what an OrchDoc entry id looks like. Every reader imports it from here.

The built-in letters are D (decision), Q (question), W (work), T (to-do), F (finding), A (agreement), G (guard) and S (standing practice). Add your own with
`python orchdoc_config.py set prefixes X=Experiment,Y=Yield`; the longest letters are tried first, so `XY12` is never read as `X` and `Y12`. `python orchdoc_ids.py` prints the letters in force.
"""
import re

try:
    import orchdoc_config as _cfg
except ImportError:  # a partial delivery must not take the id grammar down
    _cfg = None

# The letters this copy accepts before it reads any setting, and what each means. The user adds
# more with `orchdoc_config.py set prefixes X=Meaning,Y=Meaning` (see ORCHESTRATION-SETUP.md).
BUILTIN_MEANING = {
    "F": "Finding", "W": "Work", "D": "Decision", "T": "To-do",
    "A": "Agreement",
    "G": "Guard",
    "Q": "Question", "S": "Standing practice",
}

# Letters a bare label may not start with before a number (X-1, Y-3); see PRE_LABEL below.
BUILTIN_PRE_LABELS = ()


def _extra_meaning():
    """{letters: meaning} from the `prefixes` setting, e.g. `X=Experiment,Y=Yield`. A pair that
    is not LETTERS=Meaning (1 to 3 capitals) is ignored, so a typo cannot widen the grammar."""
    out = {}
    if _cfg is not None:
        for pair in _cfg.get_list("prefixes", ()):
            letters, _, meaning = pair.partition("=")
            letters = letters.strip()
            if re.fullmatch(r"[A-Z]{1,3}", letters):
                out[letters] = meaning.strip() or letters
    return out


_EXTRA = _extra_meaning()
MEANING = dict(BUILTIN_MEANING, **_EXTRA)

# Longest-first.
PREFIXES = tuple(sorted(MEANING, key=lambda k: (-len(k), k))) if _EXTRA else \
    ("D", "F", "W", "T", "A", "G", "Q", "S")

# : Matches any OrchDoc entry id, e.g.
ENTRY_ID = re.compile(r"\b(%s)(\d+)\b" % "|".join(PREFIXES))

#
# \u2b50 THE DISCRIMINATOR IS THE TOKEN BEFORE THE HYPHEN. In a range it is itself an id; in a
# script name it is not. So a hyphen-preceded id counts only when an id precedes the hyphen.
#
# Those are real references, so gate 4 will ask for less - a FALSE NEGATIVE, where the bug it
# replaces REFUSES a correct landing. A gate that asks slightly less beats one that blocks work
# and degrades the record to get around it. The token immediately before a hyphen.
_CAPS_BEFORE_HYPHEN = re.compile(r"([A-Z]{2,})-$")


def _is_real_reference(text, start):
    """
    \u26a0\ufe0f THREE CASES, and only the first is a collision:
    """
    if start == 0 or text[start - 1] != "-":
        return True
    m = _CAPS_BEFORE_HYPHEN.search(text[:start])
    if not m:
        return True                     # lowercase compound, or a range: keep it
    # An ALL-CAPS token that is itself an id is a range bound, not a series prefix.
    return bool(ENTRY_ID.fullmatch(m.group(1)))


def find_ids(text):
    """Every entry id in `text`, in order, deduplicated."""
    text = text or ""
    seen, out = set(), []
    for m in ENTRY_ID.finditer(text):
        if not _is_real_reference(text, m.start()):
            continue
        eid = m.group(1) + m.group(2)
        if eid not in seen:
            seen.add(eid)
            out.append(eid)
    return out


def parse_report(out, codes):
    found = {c: set() for c in codes}
    for line in out.splitlines():
        # A finding line BEGINS with its code. The `->` line under a finding says what moved, and
        # `BLOCKING:` lines name the code too, and are not findings either.
        first = line.split(None, 1)[0] if line.strip() else ""
        for c in codes:
            if first != c:
                continue
            m = re.search(r"entry '([A-Z]+\d+)'", line)
            ids = {m.group(1)} if m else set(find_ids(line.split(c, 1)[-1]))
            found[c] |= ids
    return found


def missing_ids(found, because):
    """{code: [ids]} the reason fails to name.
    """
    named = set(find_ids(because or ""))
    missing = {}
    for code, ids in found.items():
        if not ids:
            continue                      # a doc-level code with no entry; nothing to name
        gap = ids - named
        if gap:
            missing[code] = sorted(gap)
    return missing


def is_entry_id(token):
    m = ENTRY_ID.fullmatch(token or "")
    return bool(m)


def prefix_of(eid):
    m = ENTRY_ID.fullmatch(eid or "")
    return m.group(1) if m else None


# ---------------------------------------------------------------------------------------------

def _pre_label_series():
    if _cfg is None:
        return list(BUILTIN_PRE_LABELS)
    return [x for x in _cfg.get_list("pre_label_prefixes", BUILTIN_PRE_LABELS)
            if re.fullmatch(r"[A-Z]{1,6}", x)]


_SERIES = _pre_label_series()
# With no series configured nothing is refused: the pattern is one that cannot match.
PRE_LABEL = (re.compile(r"(?<![\w/\\.#@-])(?:%s)-[0-9]+(?![\w/\\@-])" % "|".join(_SERIES))
             if _SERIES else re.compile(r"(?!x)x"))

_PL_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_PL_INLINE_CODE = re.compile(r"(`+)(?!`)(.+?)(?<!`)\1(?!`)")
_PL_MD_TARGET = re.compile(r"\]\((?P<target>[^()\s]*(?:\([^()\s]*\)[^()\s]*)*)(?:\s+\"[^\"]*\")?\)")
_PL_URL = re.compile(r"<?\bhttps?://[^\s)>\]]+>?")
_PL_PATH = re.compile(r"[\w.~:-]*[/\\][\w./\\~-]*\.[A-Za-z]\w{0,5}\b")


def _pl_blank(text, spans):
    out, prev = [], 0
    for a, b in sorted(spans):
        a = max(a, prev)
        if b <= a:
            continue
        out.append(text[prev:a])
        out.append(re.sub(r"[^\n]", " ", text[a:b]))
        prev = b
    out.append(text[prev:])
    return "".join(out)


def bare_pre_labels(text):
    """[(label, offset)] for each bare pre-label (X-1, Y-3) in `text`, in order.

    Fenced blocks, inline code, link targets, URLs and paths are blanked first, so a label kept
    inside code for internal notes is not found. Every offset is an offset into `text`."""
    if not text:
        return []
    lines, fence = [], None
    for line in text.split("\n"):
        m = _PL_FENCE.match(line)
        if fence is None:
            if m:
                fence = (m.group(1)[0], len(m.group(1)))
            lines.append(" " * len(line) if m else line)
            continue
        if (m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1]
                and not line[m.end():].strip()):
            fence = None
        lines.append(" " * len(line))
    s = "\n".join(lines)
    s = _pl_blank(s, [m.span() for m in _PL_INLINE_CODE.finditer(s)])
    spans = [m.span("target") for m in _PL_MD_TARGET.finditer(s)]
    for rx in (_PL_URL, _PL_PATH):
        spans += [m.span() for m in rx.finditer(s)]
    s = _pl_blank(s, spans)
    return [(m.group(), m.start()) for m in PRE_LABEL.finditer(s)]


if __name__ == "__main__":
    import io
    import sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    print("OrchDoc entry-id vocabulary (%d prefixes):" % len(PREFIXES))
    for p in PREFIXES:
        print("  %-3s %s" % (p, MEANING[p]))
    print("\npattern: %s" % ENTRY_ID.pattern)
