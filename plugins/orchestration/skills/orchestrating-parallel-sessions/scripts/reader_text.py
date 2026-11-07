"""Text helpers the OrchDoc Reader needs whether or not it carries any other feature.

<!-- subject: orchestration -->
<!-- route-tags: reader search snippet diff words lines norm_text -->
"""
import difflib
import html
import re


def norm_text(data):
    """Bytes -> text with line endings folded. Line endings are how git checks a file out on
    this machine (core.autocrlf), not content, so two copies differing only there are equal."""
    if isinstance(data, bytes):
        data = data.decode("utf-8-sig", errors="replace")
    return data.replace("\r\n", "\n").replace("\r", "\n")


#: Marks around the matched part of a snippet. The page escapes the snippet FIRST and only then
#: turns these two control characters into <mark>, so a script that quotes a <script> tag stays text.
MARK_ON, MARK_OFF = "\x01", "\x02"
_DASH = str.maketrans({"-": " ", "_": " "})
SEARCH_MIN = 2


def norm_term(term):
    """A typed term as it is compared: lower case, `-` and `_` read as a space, runs of white space
    as one."""
    return re.sub(r"\s+", " ", (term or "").lower().translate(_DASH)).strip()


def norm_hay(text):
    """`text` lower-cased with `-` and `_` as spaces: the SAME LENGTH as `text` (one character
    for one), so a position found here is the position in the original, for the snippet."""
    text = text or ""
    lc = text.lower()
    if len(lc) != len(text):
        lc = "".join(c if len(c.lower()) != 1 else c.lower() for c in text)
    return lc.translate(_DASH)


def and_words(raw):
    """The words that must ALL be present when the whole phrase is not: the words of a term typed
    with spaces, three letters or more, and only when there are two or more of them."""
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
    """The old side is the removed and unchanged rows, the new side the added and unchanged rows;
    an unchanged row takes the new side's drawing. `paint` takes finished html and returns
    it; tags pass through it untouched, so the rows are joined by <br> and cut apart again."""
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
