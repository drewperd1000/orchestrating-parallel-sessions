"""the human's review notes inside a script file: `{{{ ... }}}`.

<!-- subject: script-writing -->

The human writes notes to the writer inside a script, between `{{{` and `}}}`. A note
can sit inside a line, take a whole line, or run over several lines and blank-line
paragraphs. A note is never spoken, so nothing that counts or screens spoken text
may see it.

⛔ ONE COPY OF THIS RULE. The `{{{ }}}` marker is a contract between every tool
that reads a script body for counting or screening (check_script.py,
saturation_profile.py). Each of them calls strip_notes() here; none keeps its own
regex. Before o8L157 (2026-10-02, o8:D56) check_script.py had no handling at all
and counted the human's notes as spoken words: AT-10 v3 owner-rewrite measured 4,347
words / 26:17 with its 12 notes counted.

`{{{` is a different marker from the dropdown `[[a | b | c]]`; neither touches
the other.

The human also writes COMMENTS in two braces, `{{the human's comment: ...}}`. A comment is his
ruling to the writer, not script text, so nobody speaks it either. strip_comments()
below is the one definition of a comment. Two checks use it, both through
check_script.script_reuse_tokens(): HV6-REUSE in check_script.py and the waypoint check
in check_checklist_router.py (o9 W152; lane o9L1062 moved the rule here from the
waypoint check, 2026-10-05). strip_notes() leaves comments in place, so the length
counts and the other screens still read a comment's words as spoken words.

How a note is removed:
  * a note that fills its line(s) (only whitespace before it on its first line and
    after it on its last) is removed with its line ending, so it neither splits nor
    joins the paragraph around it;
  * a note inside a line is replaced by one space, or by a blank line when the
    note itself contains a blank line (it then sat between two paragraphs);
  * an UNCLOSED `{{{` is removed up to the end of its paragraph (the next blank
    line, or the end of the text) and REPORTED. Removing it silently could hide
    script text the human meant to keep; leaving it as text would count his note as
    speech and say nothing. Reporting it is the only choice that tells him.
  * a `}}}` with no opener is left in place and reported: the text before it may
    be a note whose opener was lost, and only a person can tell.

The findings report no line numbers, so no line-count preservation is attempted.
"""
import io
import re

OPEN = "{{{"
CLOSE = "}}}"

NOTE = re.compile(r"\{\{\{.*?\}\}\}", re.S)
_UNCLOSED = re.compile(r"\{\{\{.*?(?=\n[ \t]*\n|\Z)", re.S)
_BLANK_LINE = re.compile(r"\n[ \t]*\n")


def _replace(m, text):
    start, end = m.start(), m.end()
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end == -1:
        line_end = len(text)
    if not text[line_start:start].strip() and not text[end:line_end].strip():
        # The note fills its lines: drop them, with the trailing line ending.
        return None, line_start, min(line_end + 1, len(text))
    if _BLANK_LINE.search(m.group(0)):
        return "\n\n", start, end
    # Inside a line: take the spaces on both sides with it and leave one, or
    # none at the start or end of the line.
    while start > line_start and text[start - 1] in " \t":
        start -= 1
    while end < line_end and text[end] in " \t":
        end += 1
    return ("" if start == line_start or end == line_end else " "), start, end


def _sub(rx, text):
    out = []
    pos = 0
    for m in rx.finditer(text):
        if m.start() < pos:
            continue
        rep, a, b = _replace(m, text)
        a = max(a, pos)
        out.append(text[pos:a])
        out.append(rep or "")
        pos = b
    out.append(text[pos:])
    return "".join(out)


def strip_notes(text):
    """(text with every note removed, {"notes": n, "unclosed": n, "unopened": n})."""
    notes = len(NOTE.findall(text))
    text = _sub(NOTE, text)
    unclosed = len(_UNCLOSED.findall(text))
    if unclosed:
        text = _sub(_UNCLOSED, text)
    unopened = text.count(CLOSE)
    return text, {"notes": notes, "unclosed": unclosed, "unopened": unopened}


# Two braces open a comment and two close it; three braces are a note, strip_notes()'s.
# Only a complete pair is a comment: the text between an opener and its closer may not hold
# another `{{`. Until lane o9L1065 (2026-10-05) the pattern was `\{\{(?!\{).*?\}\}`, so a
# stray `{{` that the human never closed hid every word from it to the end of the next comment,
# paragraphs later if need be: in `{{ unclosed text {{ real comment }}` the words
# "unclosed text" were taken out as part of the comment. Now the opener nearest the closer
# starts the comment, and a stray `{{` and the words after it stay as text. Each comment the
# pattern removes ends where the old one's did and starts no earlier, so it removes nothing
# the old pattern kept. test_check_script_reuse_comments.py, cases (g) to (i).
COMMENT = re.compile(r"\{\{(?!\{)(?:(?!\{\{).)*?\}\}", re.S)


def strip_comments(text):
    """`text` with each of the human's two-brace comments, `{{ ... }}`, replaced by one space.

    A `{{` with another `{{` before the next `}}` is never closed; it and the words after it
    are left as they are.

    Run it on text strip_notes() has already been through: on a `{{{` note left in place it
    would match from the second brace and leave a stray `{` and `}` behind."""
    return COMMENT.sub(" ", text)


# --------------------------------------------------------------------------- the human's strikes: `~~ ... ~~`
#
# the human strikes text in a script file with Markdown `~~...~~` and keeps it there as his record of the edit (o10L457, for
# o3:W59). Struck text is never spoken, so the app build and the approval hash must not see it. ONE COPY OF THIS RULE, like
# the note rule above: file_script.py (extract_segments: the app build, the approval hash, the notes-only rule) and
# read_script() below (check_script.py's counts and HV6-REUSE, saturation_profile.py) and check_checklist_router.py's
# waypoint words all call strip_strikes(); none keeps its own regex.
#
# strip_notes() does not do this itself, because not every caller of it wants it: reader_compare.py (the Reader's Compare
# view) shows the human's text as he wrote it.
#
# The rule, measured on the library on 2026-10-12 (every `~~` in script-library/ sits in pairs on one line; none crosses a
# line and none is unclosed):
#   * a span never crosses a line; `~~` pairs are taken in order along the line;
#   * a span inside a line is removed and the rest of the line kept; where the removal leaves whitespace on both sides of
#     the gap, the run is cut to one space;
#   * a line holding a span that is empty or only whitespace once its spans are out is dropped whole, with its line ending,
#     so a struck pause cue `~~((pause 10s))~~` leaves no cue behind;
#   * a `~~` inside a `{{{ }}}` note or a `{{ }}` comment is text of that note or comment, not a strike;
#   * a line with an odd number of `~~` is UNCLOSED: it is left as it is and its line number (1-based, in the text given)
#     is reported, never guessed at. file_script.py refuses such a text; check_script.py reports it.
STRIKE = "~~"


def _masked(text):
    """[(start, end)] of every closed `{{{ }}}` note and `{{ }}` comment in text."""
    spans = [(m.start(), m.end()) for m in NOTE.finditer(text)]
    spans += [(m.start(), m.end()) for m in COMMENT.finditer(text)]
    return spans


def _strike_marks(line, off, masked):
    """Positions in `line` of each `~~` outside a note or comment (off: the line's offset in the whole text)."""
    out = []
    i = line.find(STRIKE)
    while i != -1:
        a = off + i
        if not any(s <= a < e for s, e in masked):
            out.append(i)
            i = line.find(STRIKE, i + 2)
        else:
            i = line.find(STRIKE, i + 1)
    return out


def strip_strikes(text, keep_lines=False):
    """(text with every `~~ ... ~~` span removed, {"strikes": n, "dropped_lines": n, "unclosed_lines": [line numbers]}).

    A text with no `~~` comes back unchanged, the same string.
    keep_lines=True leaves an empty line where a whole line is dropped, so the line numbers of the rest hold
    (check_script.py's paraphrase stanzas report line numbers of the file as written)."""
    found = {"strikes": 0, "dropped_lines": 0, "unclosed_lines": []}
    if STRIKE not in text:
        return text, found
    masked = _masked(text)
    out = []
    off = 0
    lines = text.split("\n")
    for n, line in enumerate(lines, 1):
        marks = _strike_marks(line, off, masked) if STRIKE in line else []
        off += len(line) + 1
        if not marks:
            out.append(line)
            continue
        if len(marks) % 2:
            found["unclosed_lines"].append(n)
            out.append(line)
            continue
        kept = line[:marks[0]]
        for k in range(0, len(marks), 2):
            rest_end = marks[k + 2] if k + 2 < len(marks) else len(line)
            rest = line[marks[k + 1] + 2:rest_end]
            if kept[-1:] in (" ", "\t") and rest[:1] in (" ", "\t"):
                kept = kept.rstrip(" \t") + " "
                rest = rest.lstrip(" \t")
            kept += rest
            found["strikes"] += 1
        if kept.strip() == "":
            found["dropped_lines"] += 1
            if keep_lines:
                out.append("")
            continue
        out.append(kept)
    return "\n".join(out), found


def strike_spans(text):
    """[(start, end)] offsets in `text` of every struck span, the `~~` marks included, found by the SAME rule as
    strip_strikes() (same helpers: pairs in order along one line, a `~~` inside a note or comment ignored, a line with an
    odd number of `~~` has no span). The Reader (render_orchdoc_reader.py) uses it to show struck text with a line
    through it, so what it shows as struck is exactly what the build removes."""
    out = []
    if STRIKE not in text:
        return out
    masked = _masked(text)
    off = 0
    for line in text.split("\n"):
        marks = _strike_marks(line, off, masked) if STRIKE in line else []
        if marks and len(marks) % 2 == 0:
            for k in range(0, len(marks), 2):
                out.append((off + marks[k], off + marks[k + 1] + 2))
        off += len(line) + 1
    return out


def read_script(path):
    """A script file's text with the human's notes and struck text removed, and the counts: strip_notes()'s keys, plus
    "strikes" and "unclosed_strikes" (the number of lines with an unclosed `~~`, left as they are)."""
    raw = io.open(path, encoding="utf-8", errors="replace").read()
    raw, struck = strip_strikes(raw)
    text, found = strip_notes(raw)
    found["strikes"] = struck["strikes"]
    found["unclosed_strikes"] = len(struck["unclosed_lines"])
    return text, found
