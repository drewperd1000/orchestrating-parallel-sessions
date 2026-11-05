"""A paragraph that is almost entirely bold emphasises nothing. Find them in a DIFF.

<!-- subject: orchestration -->

THE THRESHOLD COMES FROM THE CORPUS, NOT FROM TASTE. Measured across 9,954 prose paragraphs in
one project's long-lived documents, the share of each paragraph that sits inside `**...**`:

      0- 9%  2463      50-59%   725
     10-19%  1522      60-69%   345
     20-29%  1724      70-79%   176   <- the trough
     30-39%  1559      80-89%    46
     40-49%  1074      90-99%   320   <- a SECOND population

The distribution has two peaks. It decays steadily to 176 and then rises again to 320, so
wrapping a whole paragraph in bold is a distinct habit rather than the far end of over-emphasis,
and 80% sits in the trough between the two. Measure your own documents before changing it.

IT READS THE DIFF, NEVER THE DOCUMENT. 366 of those paragraphs were already over the line. A gate
reading whole documents would refuse everything on its first day and be switched off within the
week. Scoped to what a landing ADDS, the backlog is untouched and nobody is blocked by history.

WHAT GOOD BOLD LOOKS LIKE. Bold opens a paragraph and states its premise, the way a bullet would
summarise it, so that a reader skimming only the bold can decide whether to read the rest. Three
parts of that can be checked exactly and one cannot:

  WHERE the first bold run starts        exact
  HOW MANY bold runs the unit has        exact
  HOW MUCH of the unit is bold           exact
  whether it tells the reader enough     NOT CHECKABLE - no threshold here pretends to

THE UNIT IS THE LIST ITEM WHERE THERE IS ONE, NOT THE PARAGRAPH. A bulleted list whose every
bullet opens with a bold label is the shape being asked for; measured as one paragraph it looks
like thirteen scattered bold runs.

WHY ONE RULE BLOCKS AND THE OTHERS ONLY REPORT, measured over 300 landings:

      >= 80% bold (the wall)                30.0% of landings
      bold does not open the unit           41.0%
      no lead AND 3+ runs                    7.7%   <- blocks
      2+ bold runs (the literal rule)       84.7%   <- cannot block

Blocking the literal rule would refuse five landings in six on the day it shipped, and a gate
nobody can land past gets switched off rather than obeyed. So the rare unambiguous shape - bold
scattered three or more times with nothing opening the unit, where no run is the summary -
refuses; the common one is counted and shown on every landing, so that a decision to tighten can
be made from the counts.

Excluded, each for a reason rather than for convenience:
  - fenced blocks      not prose
  - table rows         a bold cell is a column label
  - headings           already emphatic by being one
  - generated fields   `**Status:**`, `**Depends:**` and the rest are written by the tool
  - blockquotes        a quoted instruction is its author's formatting
  - short paragraphs   under 80 characters, a three-word bold is not a wall
"""
import re

BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.S)
ITEM_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)")
# A unit may open with a list marker, a checkbox, or one of the banner glyphs before the
# bold starts. None of those are prose, so "the bold leads" has to look past them.
_GLYPHS = (r"[ -㌀\U0001F000-\U0001FAFF⚠⛔⭐✅❌️\s]*")
PREFIX_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)?(?:\[[ x]\]\s*)?" + _GLYPHS)
SCATTER_RUNS = 3
FIELD_RE = re.compile(r"^\*\*(Status|Owner|Opened|Depends|Touches|Done-when|Attested-by|"
                      r"Reviewed|Resolved|Scope|Enriched|Why it is tracked)\b")
MIN_CHARS = 80
THRESHOLD = 0.80


def _skip(line):
    t = line.strip()
    return (not t or t.startswith("#") or t.startswith("|") or t.startswith(">")
            or t.startswith("```") or t.startswith("~~~") or t.startswith("<!--")
            or bool(FIELD_RE.match(t)))


def bold_share(par):
    """(fraction, non-whitespace length) - how much of this paragraph is bold."""
    total = len(re.sub(r"\s", "", par))
    if not total:
        return 0.0, 0
    bold = sum(len(re.sub(r"\s", "", m.group(1))) for m in BOLD_RE.finditer(par))
    return bold / total, total


def paragraphs_in(lines):
    """Prose paragraphs from a list of lines, with the 1-based index each starts at."""
    out, cur, start, infence = [], [], 0, False
    for i, ln in enumerate(lines, start=1):
        # A FENCE IS A SPAN, NOT A LINE - the same lesson as the archived entries that
        # landed inside one. Treating ``` as merely a paragraph break left the CONTENT
        # between the fences readable as prose, so a diff showing an example of a bolded
        # paragraph was refused for containing the thing it was illustrating.
        if ln.strip().startswith(('```', '~~~')):
            infence = not infence
            if cur:
                out.append((start, chr(10).join(cur)))
            cur, start = [], 0
            continue
        if infence:
            continue
        if _skip(ln):
            if cur:
                out.append((start, "\n".join(cur)))
            cur, start = [], 0
            continue
        if not cur:
            start = i
        cur.append(ln)
    if cur:
        out.append((start, "\n".join(cur)))
    return out


def added_lines(diff_text):
    """The lines a unified diff ADDS, with `+` stripped. Hunk headers dropped.

    ⛔ A LINE THE SAME DIFF ALSO REMOVES WAS MOVED, NOT WRITTEN, and it comes back as a blank
    like any other unchanged line. A diff shows a relocated entry as removed in one place and
    added in another, and `orchdoc.py archive` - which runs inside every `commit` - moves
    entries out of the stray `## RESOLVED - kept for the record` section into §99. Without
    this, a group's next landing would be refused for bold written weeks earlier, in an entry
    it only moved - the history this gate is scoped not to judge.

    ⛔ A WHOLE RUN MOVES, NOT A LINE. Matching one line at a time let a new hard-wrapped wall
    pass whenever one of its lines happened to equal a removed line: that line blanked out,
    the paragraph split in two, and each half fell under the size floor. A run is a stretch
    of consecutive added (or removed) prose lines, broken wherever `paragraphs_in` breaks a
    paragraph; an added run counts as moved only when the same run, line for line, was
    removed. Counted, so a run added twice and removed once still counts once as written.
    """
    diff = diff_text.splitlines()

    def runs(sign):
        out, cur = [], []
        for i, ln in enumerate(diff):
            if (ln.startswith(sign) and not ln.startswith(sign * 3)
                    and not _skip(ln[1:])):
                cur.append(i)
                continue
            if cur:
                out.append(cur)
            cur = []
        if cur:
            out.append(cur)
        return out

    removed = {}
    for r in runs("-"):
        key = tuple(diff[i][1:] for i in r)
        removed[key] = removed.get(key, 0) + 1
    moved = set()
    for r in runs("+"):
        key = tuple(diff[i][1:] for i in r)
        if removed.get(key, 0) > 0:
            removed[key] -= 1
            moved.update(r)
    out = []
    for i, ln in enumerate(diff):
        if ln.startswith("+++") or ln.startswith("@@"):
            continue
        if i in moved:
            out.append("")
            continue
        out.append(ln[1:] if ln.startswith("+") else "")
    return out


def walls_in_diff(diff_text):
    """[(share, chars, paragraph)] for each paragraph this diff ADDS that is a wall.

    ⭐ A paragraph is built only from ADDED lines. An edit that touches one line of an
    existing wall therefore does NOT trip it - the author did not write the wall in this
    landing, and refusing them for it would make every nearby edit expensive, which is how
    a gate teaches people to route around it.
    """
    hits = []
    for _start, par in paragraphs_in(added_lines(diff_text)):
        share, total = bold_share(par)
        if total >= MIN_CHARS and share >= THRESHOLD:
            hits.append((share, total, par))
    return sorted(hits, reverse=True)


def units(par):
    """A paragraph split into the units a bold lead can open: list items, else the whole.

    ⭐ Consecutive bullets are ONE paragraph to `paragraphs_in`, and each bullet is its own
    claim with its own opening. Measuring them together is what made a well-formed list
    look like a wall of scattered emphasis.
    """
    out, cur = [], []
    for ln in par.split("\n"):
        if ITEM_RE.match(ln) and cur:
            out.append("\n".join(cur))
            cur = [ln]
        else:
            cur.append(ln)
    if cur:
        out.append("\n".join(cur))
    return out


def unit_facts(u):
    """(runs, leads, share, chars) for one unit. `leads` is vacuously True with no bold."""
    runs = list(BOLD_RE.finditer(u))
    share, total = bold_share(u)
    if not runs:
        return 0, True, share, total
    head = PREFIX_RE.match(u)
    return len(runs), runs[0].start() <= (head.end() if head else 0), share, total


def _finding(kind, runs, share, chars, text):
    return {"kind": kind, "runs": runs, "share": share, "chars": chars, "text": text}


def findings_in_diff(diff_text):
    """(blocking, advisory) for what this landing ADDS.

    BLOCKING
      WALL     >= THRESHOLD of the unit is bold - if all of it is loud, none of it is
      SCATTER  bold appears SCATTER_RUNS+ times and opens nothing, so no run is the summary

    ADVISORY - reported on every landing, refuses nothing
      NOLEAD   bold starts mid-unit
      MULTI    bold opens the unit but more bold follows it

    MULTI IS THE LITERAL RULE AND IT IS THE ONE THAT CANNOT BLOCK - 85% of the landings
    measured contain one. Reporting it every time puts the number in front of the author
    without making the doc unlandable.
    """
    blocking, advisory = [], []
    for _start, par in paragraphs_in(added_lines(diff_text)):
        for u in units(par):
            runs, leads, share, chars = unit_facts(u)
            if chars < MIN_CHARS or not runs:
                continue
            if share >= THRESHOLD:
                blocking.append(_finding("WALL", runs, share, chars, u))
            elif not leads and runs >= SCATTER_RUNS:
                blocking.append(_finding("SCATTER", runs, share, chars, u))
            elif not leads:
                advisory.append(_finding("NOLEAD", runs, share, chars, u))
            elif runs >= 2:
                advisory.append(_finding("MULTI", runs, share, chars, u))
    blocking.sort(key=lambda f: (-f["share"], -f["runs"]))
    advisory.sort(key=lambda f: (-f["runs"], -f["share"]))
    return blocking, advisory
