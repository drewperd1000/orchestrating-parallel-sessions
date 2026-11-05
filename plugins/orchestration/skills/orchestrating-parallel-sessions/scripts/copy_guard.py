#!/usr/bin/env python3
"""copy_guard - find an instruction to copy, paste, run or type text that is kept in a file or at
a URL instead of being shown where the reader already is.

<!-- subject: orchestration -->

A reader who has to follow a link to a file in order to copy something has been sent away from
the place they were already reading, and the link may not even resolve. Show the text in the
document instead.

ONE DETECTOR, SEVERAL READERS, so they cannot disagree about what counts:
  - `orchdoc.py check` raises E-COPYBYLINK on an open entry the human acts on;
  - `orchdoc.py add`, `note` and `resolve` refuse new text that would raise it;
  - a Stop hook can call copy_hits() on a reply to refuse the same instruction in chat.
Pure text in, hits out: no file reads, no git, no orchdoc import.

WHAT A HIT IS. Settled by running it over a set of live documents and judging each hit by hand:
  1. A verb from the set (copy, paste, run, execute, type, enter) that OPENS its clause, so it
     is an instruction ("paste the prompt in X") and not a noun or a report ("a full copy is
     saved at X"), followed in the same clause by a source word (in, from, at, inside, within,
     of) and, at most four words later, a path to a text file or a URL to one.
  2. "paste-ready", "copy-ready" or "ready to paste" followed by such a path or URL.
NOT A HIT:
  - a link to the Reader: "open http://127.0.0.1:8787/#o<N>/W<n>" is a link to look at. A URL on
    this computer whose path ends in a text file's name ("copy the command from
    http://127.0.0.1:8790/scripts/run.txt", or a Reader file page) is a file to copy from, and
    is a hit like any other;
  - a payload already shown in inline code: "run `python x.py` from `scripts/`";
  - a path that names a secret: that text must never be pasted anywhere;
  - a line the caller passes as None (inside a fenced block, a quoted note, a stamp).
"""
import re
import sys

#: Extensions of files whose TEXT someone would copy. A picture or an archive is not one.
TEXT_EXT = ("md", "markdown", "txt", "ps1", "psm1", "py", "sh", "bash", "json", "jsonl", "yaml",
            "yml", "toml", "cmd", "bat", "js", "mjs", "cjs", "ts", "tsx", "html", "htm", "csv",
            "tsv", "sql", "ini", "cfg", "conf", "xml", "log", "prompt")
VERBS_ALL = ("copy", "paste", "run", "execute", "type", "enter")
#: The narrower set for a Stop hook, where the common failure is copying and pasting.
VERBS_COPY = ("copy", "paste")

URL_RE = re.compile(r"<?\b(?:https?|file)://[^\s<>()\[\]`]+[^\s<>()\[\]`.,;:!?'\"]")
READER_URL_RE = re.compile(r"^<?https?://(?:127\.0\.0\.1|localhost):\d+/", re.I)
#: A link to one OrchDoc entry on the Reader: what makes a to-do line pass.
#: Groups: the doc and the entry id.
ENTRY_LINK_RE = re.compile(r"https?://(?:127\.0\.0\.1|localhost):8787/#(o\d+)/([A-Z]{1,3}\d+[a-z]?)\b")
MDLINK_RE = re.compile(r"(!?)\[((?:[^\[\]\n]|\[[^\[\]\n]*\])*)\]"
                       r"\(([^()\s]*(?:\([^()\s]*\)[^()\s]*)*)(?:\s+\"[^\"]*\")?\)")
CODE_RE = re.compile(r"(`+)(?!`)(.+?)(?<!`)\1(?!`)")
# Built by concatenation: a `%` in the character classes would break %-formatting.
PATH_RE = re.compile(
    r"(?<![\w/\\.-])(?:[A-Za-z]:[\\/]|~[\\/]|\.{1,2}[\\/])?(?:[\w.@~+%-]+[\\/])*[\w.@~+%-]*"
    r"[\w@~+%-]\.(?:" + "|".join(TEXT_EXT) + r")(?![\w])", re.I)
SECRETISH = re.compile(r"(?i)(^|[\\/])(\.?secrets?)[\\/]|(^|[\\/])\.env(\.|$)|secret|credential|"
                       r"password|passwd|private[-_]?key|api[-_]?key|"
                       r"(?:^|[-_./\\])(?:oauth[-_]?)?token(?:[-_.]|$)")
SRC_PREP_RE = re.compile(r"\b(in|from|at|inside|within|of)\b", re.I)
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[\"'(*_\[]*[A-Z0-9\x01])")
LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
HEAD_RE = re.compile(r"^#{1,6}\s")
HEAD_PREFIX_RE = re.compile(r"^#{1,6}\s*(?:[^\w\s]+\s*)*(?:[A-Z]{1,3}\d+[a-z]?\s*[-:–—]\s*)?")
IMPERATIVE_PRE_RE = re.compile(
    r"(?:^|[:;(|—]|\s-\s|\band\b|\bthen\b|\bor\b|,)\s*"
    r"(?:(?:then|and|please|just|now|next|first|finally|also|simply)\s+)*$", re.I)
READY_RE = re.compile(r"\b(?:paste|copy)[- ]ready\b|\bready to (?:paste|copy)\b", re.I)
FENCE_RE = re.compile(r"^\s*(```|~~~)")


def is_text_url(u):
    """True when the URL's path ends in a text file's extension."""
    path = re.split(r"[?#]", u, 1)[0]
    return bool(re.search(r"\.(?:%s)$" % "|".join(TEXT_EXT), path, re.I))


def prose_lines(text):
    """`text` as lines for `copy_hits`: None for each line of a fenced block and its fences, and
    HTML comments blanked. For a chat reply; orchdoc prepares an OrchDoc's lines itself."""
    out, fence = [], None
    for ln in (text or "").split("\n"):
        m = FENCE_RE.match(ln)
        if fence is None and m:
            fence = m.group(1)
            out.append(None)
            continue
        if fence is not None:
            if ln.lstrip().startswith(fence):
                fence = None
            out.append(None)
            continue
        out.append(re.sub(r"<!--.*?-->", " ", ln))
    return out


def mask(unit):
    """Each link, URL, code span and path in `unit` replaced by a numbered placeholder.
    -> (masked text, [(kind, raw)]). Kinds: EMBED, READER, REF (a text file, which is what a
    hit needs), URL (any other), LINKOTHER, CODE."""
    toks = []

    def put(kind, raw):
        toks.append((kind, raw))
        return " \x01%d\x01 " % (len(toks) - 1)

    def link(m):
        bang, label, target = m.group(1), m.group(2), m.group(3)
        if bang:
            return put("EMBED", target)
        t = target.strip("<>")
        # A label that is itself a path or URL ("[NOTES.md](docs/NOTES.md)") names the target
        # again; it must not become a second reference that resolves to nothing, so its
        # paths are dropped and its words kept.
        words = PATH_RE.sub(" ", URL_RE.sub(" ", CODE_RE.sub(
            lambda c: " " if (PATH_RE.fullmatch(c.group(2).strip())
                              or URL_RE.fullmatch(c.group(2).strip())) else c.group(0),
            label)))
        if re.match(r"(?i)^(?:https?|file)://", t):
            # A text file is a REF wherever it is served, this computer included; only a
            # localhost URL that is not one (an entry, a view) is a Reader link to look at.
            if is_text_url(t):
                return words + put("REF", t)
            return words + put("READER" if READER_URL_RE.match(t) else "URL", t)
        if PATH_RE.fullmatch(t.split("#")[0]):
            return words + put("REF", t)
        return label + put("LINKOTHER", t)

    s = MDLINK_RE.sub(link, unit)

    def code(m):
        inner = m.group(2).strip()
        if PATH_RE.fullmatch(inner) or (URL_RE.fullmatch(inner) and is_text_url(inner)):
            return put("REF", inner)
        if URL_RE.fullmatch(inner):
            return put("READER" if READER_URL_RE.match(inner) else "URL", inner)
        return put("CODE", inner)

    s = CODE_RE.sub(code, s)

    def url(m):
        u = m.group(0).strip("<>")
        if is_text_url(u):
            return put("REF", u)
        return put("READER" if READER_URL_RE.match(u) else "URL", u)

    s = URL_RE.sub(url, s)
    s = PATH_RE.sub(lambda m: put("REF", m.group(0)), s)
    return s, toks


def units_of(lines):
    """[(index of first line, text)]: wrapped lines joined into one paragraph; a list item, a
    heading or a table row starts its own unit; a blank or None line ends one."""
    out, cur, start = [], [], None
    for i, ln in enumerate(lines):
        if ln is None or not ln.strip():
            if cur:
                out.append((start, " ".join(cur)))
            cur, start = [], None
            continue
        if HEAD_RE.match(ln) or LIST_RE.match(ln) or ln.lstrip().startswith("|"):
            if cur:
                out.append((start, " ".join(cur)))
            cur, start = [ln.strip()], i
            if HEAD_RE.match(ln) or ln.lstrip().startswith("|"):
                out.append((start, " ".join(cur)))
                cur, start = [], None
            continue
        if start is None:
            start = i
        cur.append(ln.strip())
    if cur:
        out.append((start, " ".join(cur)))
    return out


def imperative(sent, start, end):
    """True when the verb at sent[start:end] opens its clause, so it reads as an instruction."""
    pre = sent[:start]
    pre = HEAD_PREFIX_RE.sub("", pre)
    pre = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", pre)
    pre = re.sub(r"[*_~]+", "", pre)
    pre = re.sub(r"^\s*(?:step\s+\d+\s*[:.)-]\s*)", "", pre, flags=re.I)
    post = sent[end:end + 3]
    if re.match(r"\s*[:\d]", post):
        return False          # "Run: 13", "run 1", "Run 2": a label or a count, not a verb
    return bool(IMPERATIVE_PRE_RE.search(pre))


def _unmask(s, toks):
    return re.sub(r" ?\x01(\d+)\x01 ?", lambda m: " <%s:%s> " % toks[int(m.group(1))], s)


def copy_hits(lines, verbs=VERBS_ALL):
    """Every instruction in `lines` to copy, paste, run or type text kept in a file or at a URL.

    `lines`: the text as a list of lines, with None for a line that is not prose (it also ends
    the paragraph). -> [{"line": index of the paragraph's or list item's first line, "verb",
    "ref": the path or URL as written, "exempt": None, "payload-in-code" or "secret", "text":
    the sentence with each link, path and code span shown as <KIND:text>}]. A caller raises
    only the hits whose "exempt" is None.
    """
    verb_re = re.compile(r"\b(%s)\b" % "|".join(re.escape(v) for v in verbs), re.I)
    clause_re = re.compile(r"\s(?:and then|then|and)\s+(?=(?:%s|open)\b)|;"
                           % "|".join(re.escape(v) for v in VERBS_ALL), re.I)
    hits = []
    for idx, unit in units_of(lines):
        masked, toks = mask(unit)
        for sent in SENT_SPLIT.split(masked):
            for vm in verb_re.finditer(sent):
                if not imperative(sent, vm.start(), vm.end()):
                    continue
                seg = clause_re.split(sent[vm.end():], 1)[0]
                ref, why = None, None
                for pm in SRC_PREP_RE.finditer(seg):
                    tm = re.match(r"\s*(?:[\w'-]+\s+){0,4}?\x01(\d+)\x01",
                                  seg[pm.end():pm.end() + 80])
                    if not tm:
                        continue
                    kind, raw = toks[int(tm.group(1))]
                    if kind != "REF":
                        continue
                    payload = [toks[int(k)] for k in re.findall(r"\x01(\d+)\x01", seg[:pm.start()])]
                    if any(k == "CODE" for k, _ in payload):
                        why = "payload-in-code"
                    if SECRETISH.search(raw):
                        why = "secret"
                    ref = raw
                    break
                if ref:
                    hits.append({"line": idx, "verb": vm.group(1), "ref": ref, "exempt": why,
                                 "text": " ".join(_unmask(sent, toks).split())[:300]})
            for rm in READY_RE.finditer(sent):
                if not any(v in rm.group(0).lower() for v in verbs):
                    continue
                refs = [toks[int(k)] for k in re.findall(r"\x01(\d+)\x01", sent[rm.end():])]
                refs = [r for k, r in refs if k == "REF" and not SECRETISH.search(r)]
                if refs:
                    hits.append({"line": idx, "verb": rm.group(0), "ref": refs[0], "exempt": None,
                                 "text": " ".join(_unmask(sent, toks).split())[:300]})
    return hits


_GH_BLOB_RE = re.compile(r"(?i)^https?://github\.com/[^/\s]+/[^/\s]+/(?:blob|raw)/[^/\s]+/(.+)$")
_GH_RAW_RE = re.compile(r"(?i)^https?://raw\.githubusercontent\.com/[^/\s]+/[^/\s]+/[^/\s]+/(.+)$")
#: The Reader's file page (`render_orchdoc_reader.file_href`): /file/<url-quoted workspace path>.
_READER_FILE_RE = re.compile(r"(?i)^https?://(?:127\.0\.0\.1|localhost):\d+/file/(.+)$")


def local_source(ref):
    """The file a hit's `ref` most likely names on this computer, for the remedy's
    `orchdoc.py paste --file` argument: a GitHub file URL becomes its path inside the
    repository, a Reader file page its path in the workspace, `file:///C:/x` becomes `C:/x`;
    anything else is returned as written."""
    r = (ref or "").strip().strip("<>")
    for rx in (_GH_BLOB_RE, _GH_RAW_RE):
        m = rx.match(r)
        if m:
            return re.split(r"[?#]", m.group(1), 1)[0]
    m = _READER_FILE_RE.match(r)
    if m:
        from urllib.parse import unquote
        return unquote(re.split(r"[?#]", m.group(1), 1)[0])
    m = re.match(r"(?i)^file:///?(.+)$", r)
    if m:
        return m.group(1)
    return r


if __name__ == "__main__":
    # `python copy_guard.py <file>`: print each hit in a text file, for judging by hand.
    # Reconfigured here only: an importer owns its own streams.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if len(sys.argv) != 2:
        print("usage: copy_guard.py <text file>", file=sys.stderr)
        sys.exit(2)
    try:
        with open(sys.argv[1], encoding="utf-8", errors="replace") as fh:
            _lines = prose_lines(fh.read())
    except OSError as e:
        print("cannot read %s: %s" % (sys.argv[1], e.strerror), file=sys.stderr)
        sys.exit(2)
    for h in copy_hits(_lines):
        print("line %d  %-8s %-16s %s" % (h["line"] + 1, h["verb"], h["exempt"] or "HIT", h["text"]))
