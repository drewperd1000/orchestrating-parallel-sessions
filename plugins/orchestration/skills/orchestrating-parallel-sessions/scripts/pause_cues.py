#!/usr/bin/env python3
"""The one rule for a pause cue in a script (o10L639, the human 2026-10-22).

The human: different agents write pauses their own way - at the end of a paragraph instead of on their own line,
with single parentheses, with different words in the brackets ((pause roughly 3s)), ((pause ~2s)),
((pause ~3sec)). "This must be normalized via a script check."

THE CANONICAL FORM: `((pause Ns))`
  * doubled round brackets, lowercase "pause", ONE space, N a whole number or a decimal (3, 1.5), a lowercase "s";
  * no tilde, no "roughly"/"about"/"sec"/"seconds", no emphasis (`*` `_`) around it;
  * ALONE on its own line.
Only the doubled form is a cue (o10:D180). `[[pause Ns]]` is NOT a cue (it collides with the `[[a|b]]` dropdown
syntax), so it is a variant to normalize, like `(pause 3s)`, `[pause 3s]`, `{pause 3s}`, `{{pause 3s}}`, `<pause 3s>`.

PLACEMENT (read from file_script.py `attach_pause_cues` / `render_chunk` and the app's shared/pauseCues.ts
`isPauseCueLine`): a cue is a whole line. A cue that was appended to the end of a text line goes onto its own line
DIRECTLY AFTER that line (no blank line added), which is the shape render_chunk writes (statement, newline, cue).
A cue in the MIDDLE of a line, or at its start, is not moved: it is reported for a human.

What this module never touches: the header (everything up to `<!-- END SCRIPT HEADER -->`), the human's `{{{ ... }}}`
notes, `{{ ... }}` comments, struck `~~...~~` text (the same spans script_notes.py uses), `code spans`, fenced code,
and lines that start with `#`, `>`, `|` or `<!--`.

Public API
  CANONICAL_CUE_RE        compiled regex of one canonical cue (fullmatch it)
  is_canonical(s)         True when s is exactly one canonical cue
  scan(text)              every non-canonical pause-like thing, as a list of change dicts (nothing is rewritten)
  normalize(text)         (new_text, changes)   changes = the same dicts, with action "fixed" when applied
Each change dict: line (1-based, in `text`), original, canonical (or None), kinds (list), action
("fixed" | "human" | "note"), detail (plain words).
Kinds: single->double, bracket, wording, emphasis, moved, range, mid-line, no-number, in-dropdown, unparsed,
member-text.
  "fixed"  the rewrite is made (normalize) or would be made (scan)
  "human"  not changed; a person must decide (mid-line cue, no number, unreadable duration)
  "note"   not a cue (a member-style parenthetical such as "(pause here if you need to)"); report only
"""
import re
import sys

MARKER = "<!-- END SCRIPT HEADER -->"

CANONICAL_CUE = r"\(\(pause \d+(?:\.\d+)?s\)\)"
CANONICAL_CUE_RE = re.compile(CANONICAL_CUE)


def is_canonical(s):
    return bool(CANONICAL_CUE_RE.fullmatch(s))


# Every pause-like token. Doubled forms first so a single-paren alternative never takes the inside of a doubled one.
_LIKE = re.compile(
    r"(?P<dbl>\(\(\s*pause\b[^()]*\)\))"
    r"|(?P<dbk>\[\[\s*pause\b[^\[\]|]*\]\])"
    r"|(?P<dbr>(?<!\{)\{\{\s*pause\b[^{}]*\}\}(?!\}))"
    r"|(?P<sgl>(?<!\()\(\s*pause\b[^()]*\)(?!\)))"
    r"|(?P<sbk>(?<!\[)\[\s*pause\b[^\[\]]*\](?!\]))"
    r"|(?P<sbr>(?<!\{)\{\s*pause\b[^{}]*\}(?!\}))"
    r"|(?P<ang><\s*pause\b[^<>]*>)"
    # catch-all (o10L648): any other bracketed token holding the word "pause" - a missing bracket ((pause 3s) and
    # (pause 3s)), the word not first ((short pause)), ((long pause 3s)) - is reported for a person
    r"|(?P<oth>[(\[<{]+[^()\[\]<>{}|]*\bpause\b[^()\[\]<>{}|]*[)\]>}]+)",
    re.I)

_CLOSE = {"oth": ")", "dbl": "))", "dbk": "]]", "dbr": "}}", "sgl": ")", "sbk": "]", "sbr": "}", "ang": ">"}
_OPEN = {"oth": 1, "dbl": 2, "dbk": 2, "dbr": 2, "sgl": 1, "sbk": 1, "sbr": 1, "ang": 1}
_SINGLE_FORMS = ("sgl", "sbk", "sbr", "ang")

_CODE_SPAN = re.compile(r"`[^`\n]*`")
_DROPDOWN = re.compile(r"\[\[[^\[\]]*\|[^\[\]]*\]\]")
_EMPH = "*_"

_APPROX = re.compile(r"^(?:[:=\-]\s*|for\s+|of\s+|about\s+|roughly\s+|approx(?:imately|\.)?\s+|around\s+|circa\s+|c\.\s*|ca\.\s*|[~≈]\s*)+", re.I)
_DURATION = re.compile(
    r"(\d+(?:\.\d+)?)(?:\s*(?:-|–|—|to)\s*(\d+(?:\.\d+)?))?\s*(?:s|secs?|seconds?)?\s*[.]?", re.I)


def _inner(token, form):
    o = _OPEN[form]
    c = len(_CLOSE[form])
    s = token[o:len(token) - c]
    return re.sub(r"^\s*pause\b", "", s, count=1, flags=re.I)


def _parse(inner):
    """(status, seconds_text, kinds). status: ok | numberless | words | unparsed."""
    s = _APPROX.sub("", inner.strip()).strip()
    if not s or not re.search(r"\w", s):
        return "numberless", None, []
    if not re.search(r"\d", s):
        return "words", None, []
    m = _DURATION.fullmatch(s)
    if not m:
        return "unparsed", None, []
    a, b = m.group(1), m.group(2)
    if b is not None:
        big = a if float(a) >= float(b) else b
        return "ok", big, ["range"]
    return "ok", a, []


def _canon(n):
    return "((pause %ss))" % n


def _protected_spans(text):
    """[(start, end)] of every part of the text this module must not read or change."""
    spans = []
    pos = text.find(MARKER)
    if pos != -1:
        spans.append((0, pos + len(MARKER)))
    try:
        import script_notes as sn
    except ImportError:  # pragma: no cover - script_notes sits beside this file
        sn = None
    if sn is not None:
        spans += [(m.start(), m.end()) for m in sn.NOTE.finditer(text)]
        for m in sn._UNCLOSED.finditer(text):
            if m.group(0).count("}}}") == 0:
                spans.append((m.start(), m.end()))
        for m in sn.COMMENT.finditer(text):
            lm = _LIKE.fullmatch(m.group(0))
            if not (lm and lm.lastgroup == "dbr"):
                spans.append((m.start(), m.end()))
        spans += sn.strike_spans(text)
    else:  # pragma: no cover
        spans += [(m.start(), m.end()) for m in re.finditer(r"\{\{\{.*?\}\}\}", text, re.S)]
    spans += [(m.start(), m.end()) for m in _CODE_SPAN.finditer(text)]
    for m in re.finditer(r"^[ \t]*```.*?^[ \t]*```[ \t]*$", text, re.S | re.M):
        spans.append((m.start(), m.end()))
    return spans


def _masked(text):
    """text with every protected character replaced by \\x01 (newlines kept)."""
    chars = list(text)
    for a, b in _protected_spans(text):
        for i in range(a, min(b, len(chars))):
            if chars[i] != "\n":
                chars[i] = "\x01"
    return "".join(chars)


_PAIRS = {"(": ")", "[": "]", "<": ">", "{": "}"}


def _single_pair_no_digit(t):
    """True for exactly one opening and one closing bracket of the same pair, no digit, not an HTML comment."""
    if len(t) < 3 or t[0] not in _PAIRS or t[-1] != _PAIRS[t[0]] or re.search(r"\d", t) or t.startswith("<!--"):
        return False
    return len(re.findall(r"[(\[<{]", t)) == 1 and len(re.findall(r"[)\]>}]", t)) == 1


def _token_info(tok):
    """Classify one regex match: dict(form, text, start, end, status, canonical, kinds, detail)."""
    form = tok.lastgroup
    t = tok.group(0)
    if form == "oth" and _single_pair_no_digit(t):
        # o10L656 (D180): ONE matched bracket pair with no number is spoken text inside a sentence; the whole-line
        # rule in _process still blocks it when it is alone on its line
        return {"form": form, "text": t, "start": tok.start(), "end": tok.end(), "status": "words",
                "canonical": None, "kinds": ["member-text"],
                "detail": "a single-bracket parenthetical with no number is spoken text, not a cue; left as it is"}
    if form == "oth":
        return {"form": form, "text": t, "start": tok.start(), "end": tok.end(), "status": "unknown",
                "canonical": None, "kinds": ["unrecognized"],
                "detail": "a bracketed token with the word 'pause' in a form that is not a known cue; a person must fix it"}
    status, secs, kinds = _parse(_inner(t, form))
    info = {"form": form, "text": t, "start": tok.start(), "end": tok.end(), "status": status,
            "canonical": None, "kinds": list(kinds), "detail": ""}
    if status == "ok":
        canon = _canon(secs)
        info["canonical"] = canon
        raw_inner = t[_OPEN[form]:len(t) - len(_CLOSE[form])]
        if form == "sgl":
            info["kinds"].append("single->double")
        elif form == "dbk":
            info["kinds"].append("[[ ]]")
        elif form != "dbl":
            info["kinds"].append("bracket")
        if "~" in raw_inner:
            info["kinds"].append("tilde")
        # wording: the inner text is anything but "pause", one space, the number, "s" (a tilde is counted apart)
        if not re.fullmatch(r"pause ~?\d+(?:\.\d+)?s", raw_inner) and "range" not in kinds:
            info["kinds"].append("wording")
        if "range" in kinds:
            info["detail"] = "a range was given; the larger value, %ss, is used" % secs
    elif status == "words" and form in _SINGLE_FORMS:
        info["kinds"] = ["member-text"]
        info["detail"] = "a single-bracket parenthetical with no number is spoken text, not a cue; left as it is"
    elif status == "numberless" or status == "words":
        info["kinds"] = ["no-number"]
        info["detail"] = "no duration in the cue; a person must give the seconds"
    else:
        info["kinds"] = ["unparsed"]
        info["detail"] = "the duration is not a plain number of seconds; a person must decide"
    return info


def _emph_run_before(m1, i, floor=0):
    j = i
    while j > floor and m1[j - 1] in _EMPH:
        j -= 1
    return m1[j:i]


def _emph_run_after(m1, i):
    j = i
    while j < len(m1) and m1[j] in _EMPH:
        j += 1
    return m1[i:j]


def _process(text):
    """Returns (new_text, changes). The one implementation behind scan() and normalize()."""
    lines = text.split("\n")
    masked_lines = _masked(text).split("\n")
    dd_spans_by_line = []
    for ml in masked_lines:
        dd_spans_by_line.append([(m.start(), m.end()) for m in _DROPDOWN.finditer(ml)])
    out_lines = []
    changes = []
    for n, (L0, M0) in enumerate(zip(lines, masked_lines), 1):
        cr = "\r" if L0.endswith("\r") else ""
        L = L0[:-1] if cr else L0
        M = M0[:-1] if cr else M0
        s = L.lstrip()
        if (not _LIKE.search(M)) or s[:1] in ("#", ">", "|") or s.startswith("<!--"):
            out_lines.append(L0)
            continue
        toks = [_token_info(t) for t in _LIKE.finditer(M)]
        dd = dd_spans_by_line[n - 1]
        # a canonical cue inside a dropdown option is accepted; anything else inside one needs a person
        live = []
        for t in toks:
            in_dd = any(a <= t["start"] < b for a, b in dd)
            if in_dd:
                if t["text"] == t["canonical"]:
                    continue
                changes.append({"line": n, "original": t["text"], "canonical": t["canonical"],
                                "kinds": ["in-dropdown"] + [k for k in t["kinds"] if k != "member-text"],
                                "action": "human",
                                "detail": "a non-canonical cue inside a [[a|b]] option; a person must fix it"})
                continue
            live.append(t)
        toks = live
        if not toks:
            out_lines.append(L0)
            continue
        # o10L648: a single-bracket pause token with words and no number that is ALL the line holds (emphasis allowed) is a
        # stage direction, not member text: a person must decide (it would otherwise be spoken aloud)
        if any("member-text" in t["kinds"] for t in toks):
            _rest = M
            for a, b in sorted([(t["start"], t["end"]) for t in toks], reverse=True):
                _rest = _rest[:a] + " " * (b - a) + _rest[b:]
            if not re.sub(r"[\s*_\x01]", "", _rest):
                for t in toks:
                    if "member-text" in t["kinds"]:
                        t["kinds"] = ["no-number", "stage-direction"]
                        t["status"] = "numberless"
                        t["detail"] = ("a pause direction with no number is the only thing on its line; it would be spoken "
                                       "aloud. A person must give the seconds or remove it")
        notes = [t for t in toks if "member-text" in t["kinds"]]
        for t in notes:
            changes.append({"line": n, "original": t["text"], "canonical": None, "kinds": ["member-text"],
                            "action": "note", "detail": t["detail"]})
        toks = [t for t in toks if "member-text" not in t["kinds"]]
        if not toks:
            out_lines.append(L0)
            continue
        blocked = [t for t in toks if t["status"] != "ok"]

        def add(t, kinds, action, detail="", canonical="keep"):
            changes.append({"line": n, "original": t["text"],
                            "canonical": t["canonical"] if canonical == "keep" else canonical,
                            "kinds": kinds, "action": action, "detail": detail or t["detail"]})

        # --- structure of the line -------------------------------------------------
        spans = [(t["start"], t["end"]) for t in toks]
        rest = M
        for a, b in sorted(spans, reverse=True):
            rest = rest[:a] + " " * (b - a) + rest[b:]
        spoken = re.sub(r"[\s*_\x01]", "", rest)
        has_protected = "\x01" in M
        if not spoken:
            # ---- the line holds only cue(s) (and emphasis, notes) -----------------
            if blocked:
                for t in blocked:
                    add(t, t["kinds"], "human")
                out_lines.append(L0)
                continue
            if has_protected and len(toks) > 1:
                for t in toks:
                    add(t, t["kinds"] + ["mid-line"], "human", "several cues share a line with a note; a person must split them")
                out_lines.append(L0)
                continue
            if len(toks) == 1 and has_protected:
                t = toks[0]
                add(t, t["kinds"] + ["with-note"], "human",
                    "the cue shares its line with a note or other protected text; a person must put the cue alone on its line")
                out_lines.append(L0)
                continue
            if len(toks) == 1:
                t = toks[0]
                a = t["start"] - len(_emph_run_before(M, t["start"]))
                b = t["end"] + len(_emph_run_after(M, t["end"]))
                new = t["canonical"]  # alone on its line: no indent, no trailing space (nbsp included)
                if a != t["start"] or b != t["end"]:
                    t["kinds"].append("emphasis")
                if new != L and not t["kinds"]:
                    t["kinds"].append("whitespace")
                if new == L:
                    out_lines.append(L0)
                    continue
                add(t, t["kinds"], "fixed")
                out_lines.append(new + cr)
                continue
            # several cues on one line, no note: one per line
            outs = []
            ok_all = True
            for t in toks:
                outs.append(t["canonical"])
            for t in toks:
                add(t, t["kinds"] + ["moved"], "fixed", "several cues on one line; each goes on its own line")
            out_lines.extend(o + cr for o in outs)
            continue
        # ---- the line holds spoken text ------------------------------------------
        first = toks[0]
        tail_ok = True
        prev_end = first["start"]
        for t in toks:
            gap = M[prev_end:t["start"]]
            if gap.strip(_EMPH + " \t"):
                tail_ok = False
            prev_end = t["end"]
        last_tail = M[prev_end:]
        o_run = _emph_run_before(M, first["start"])
        c_run = _emph_run_after(M, prev_end)
        if last_tail[len(c_run):].strip():
            tail_ok = False
        if o_run != c_run:
            tail_ok = False
        if "\x01" in M[first["start"]:]:
            tail_ok = False
        if blocked or not tail_ok:
            for t in toks:
                add(t, t["kinds"] + (["mid-line"] if "mid-line" not in t["kinds"] else []), "human",
                    "the cue shares a line with spoken text and is not at its end; a person must place it on its own line"
                    if t["status"] == "ok" else t["detail"])
            out_lines.append(L0)
            continue
        head = L[:first["start"] - len(o_run)].rstrip()
        out_lines.append(head + cr)
        for t in toks:
            kinds = list(t["kinds"]) + ["moved"]
            if o_run:
                kinds.append("emphasis")
            add(t, kinds, "fixed", "moved from the end of its text line onto its own line directly after it")
            out_lines.append(t["canonical"] + cr)
    new_text = "\n".join(out_lines)
    return new_text, changes


def scan(text):
    """Every non-canonical pause-like thing in `text`, as change dicts. The text is not changed."""
    return _process(text)[1]


def normalize(text):
    """(new_text, changes). Rewrites every fixable non-canonical cue to `((pause Ns))` on its own line.
    Idempotent: normalize(new_text) returns (new_text, [only the human / note items])."""
    new_text, changes = _process(text)
    return new_text, changes


def needs_human(changes):
    return [c for c in changes if c["action"] == "human"]


def fixable(changes):
    return [c for c in changes if c["action"] == "fixed"]


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if sys.argv[1:] == ["selftest"]:
        # The publisher runs `<script> selftest` on the sanitised copy (orchdoc_publish.py).
        # Without this, "selftest" was opened as a file name and the run ended in a traceback.
        _once = normalize("One. (pause 2sec)\n")[0]
        assert _once == normalize(_once)[0] and not scan(_once), _once
        print("pause_cues selftest: ok")
        sys.exit(0)
    for p in sys.argv[1:]:
        t = open(p, encoding="utf-8", errors="replace").read()
        for c in scan(t):
            print("%s:%d: %s %s -> %s %s" % (p, c["line"], c["action"], c["original"], c["canonical"], ",".join(c["kinds"])))
