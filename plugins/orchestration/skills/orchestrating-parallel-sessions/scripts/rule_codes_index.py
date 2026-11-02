#!/usr/bin/env python3
"""Generate RULE-CODES-INDEX.md: one row per script-rule code (HV9, TY7, UW2 ...).

<!-- subject: script-writing -->

<!-- route-tags: rule codes index rule code lookup HV9 TY7 UW2 AR7 script checklist boxes
     writing rules universal rule-codes-index generator reader #rule route -->

WHY THIS EXISTS (o9 lane o9L1197, 2026-10-10). The human sees rule codes such as HV9 or TY7 in
OrchDoc entries and reports, and had no way to look one up: the codes live as checkbox
"boxes" spread over eleven SCRIPT-CHECKLIST-*.md files plus AUTHORING-CHECKLIST-UNIVERSAL.md.
This page is GENERATED from those boxes, never kept by hand, like every other index here.

WHAT A BOX IS. A list item at column 0 whose bold or heading opens with the code:

    - [ ] **[HV9] TITLE.** `[RULED the human ...]`        the common form
    - [ ] ⛔ **[DD0] TITLE ...                         a marker emoji before the bold
    - [ ] ### [LN1] TITLE                             a heading form
    - [ ] ⚠️ **[PA1] `[MIXED - ...]`** ...             a provenance tag first, and the
          **[PA1] TITLE ...                            title on the next line
    - ⛔ **[SG3] ELIMINATED ...**                      no checkbox (a retired rule)

The box's text runs from its first line to the line before the next box or the next
markdown heading at column 0. The title is the bold text (which may wrap over lines) or
the heading text. The tag is the first `[RULED ...]`, `[DERIVED ...]`, `[MIXED ...]` or
`[UNKNOWN ...]` code span in the box's opening lines.

THE PREFIX CHECK. AUTHORING-CHECKLIST.md maps every prefix to its file in the paragraph
that starts "A row's IDs can span files". A prefix named there with no box found is
reported, and `--check` fails on it, because that means this parser missed a box shape.

    rule_codes_index.py --write       write RULE-CODES-INDEX.md in the tree holding this script
    rule_codes_index.py --check       exit 1 if the page is stale or a mapped prefix has no box
    rule_codes_index.py --precommit   pre-commit guard: exit 9 (refuse) when a commit stages a
                                      checklist or the page and the STAGED page does not match
                                      the STAGED sources. Fails open on any internal error.
    rule_codes_index.py --json        print the parsed boxes (the Reader's data) as JSON

The Reader (render_orchdoc_reader.py) imports `load()` from here for its #rule route and for
linking bare codes in entries, so the page and the Reader can never disagree.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

try:
    from no_window import flags
except ImportError:  # a partial delivery must not take the tool down
    def flags(extra=0):
        return extra

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
OUT_NAME = "RULE-CODES-INDEX.md"
ROUTER_NAME = "AUTHORING-CHECKLIST.md"
UNIVERSAL_NAME = "AUTHORING-CHECKLIST-UNIVERSAL.md"
SOURCE_GLOB = "SCRIPT-CHECKLIST-*.md"
READER = "http://127.0.0.1:8787/"
REFUSE = 9

#: A code as it appears in prose: two capitals, then a number.
CODE_RE = re.compile(r"^[A-Z]{2}[0-9]{1,3}$")

#: The opening line of a box. `pre` swallows marker emoji between the checkbox and the bold.
BOX_RE = re.compile(
    r"^- (?P<box>\[[ xX]\] )?(?P<pre>[^\w*#\[`\n]{0,12}?)"
    r"(?P<form>\*\*|#{1,6} )\[(?P<code>[A-Z]{2}[0-9]{1,3})\]\s*(?P<rest>.*)$")
#: The second line of a tag-first box (PA1, TY2): the bold title, indented.
TITLE_LINE_RE = re.compile(r"^\s+\*\*\[(?P<code>[A-Z]{2}[0-9]{1,3})\]\s*(?P<rest>.*)$")
HEADING_RE = re.compile(r"^#{1,6} ")
TAG_RE = re.compile(r"`\[((?:RULED|DERIVED|MIXED|UNKNOWN)\b[^`\]]*)\]`")
# The prefix map paragraph in AUTHORING-CHECKLIST.md.
MAP_START = "A row's IDs can span files"
MAP_PREFIX_RE = re.compile(r"`([A-Z]{2})\*`")


def _bold_text(first, following):
    """The bold text that opens at the start of `first` and may wrap onto `following`."""
    text = first
    if "**" in text:
        return text.split("**", 1)[0]
    for ln in following[:6]:
        s = ln.strip()
        if not s:
            break
        if "**" in s:
            return text + " " + s.split("**", 1)[0]
        text += " " + s
    return text


def _heading_text(first, following):
    """A `### [XX9]` title. Authors wrap it onto indented lines, so a title that has not ended
    a sentence takes up to two more lines, each cut at its first sentence end. A line that
    opens with a code span (a provenance tag) or bold ends the title."""
    text = first.rstrip()
    for ln in following[:2]:
        if not text or text[-1] in ".)!?:":
            break
        s = ln.strip()
        if not s or s[0] in "`*" or not (s[0].isalnum() or s[0] == "("):
            break
        m = re.search(r"[.!?](?:\s|$)", s)
        text += " " + (s[:m.start() + 1] if m else s)
        if m:
            break
    return text


def _clean_title(t):
    t = re.sub(r"\s+", " ", t).strip()
    t = TAG_RE.sub("", t).strip()
    return t.rstrip(" -").strip()


def parse_text(name, text):
    """Every box in one file's text, in order. Each is a dict (see module docstring)."""
    lines = text.replace("\r\n", "\n").split("\n")
    starts = []
    for i, ln in enumerate(lines):
        m = BOX_RE.match(ln)
        if m:
            starts.append((i, m))
    boxes = []
    for n, (i, m) in enumerate(starts):
        end = len(lines)
        nxt = starts[n + 1][0] if n + 1 < len(starts) else len(lines)
        for j in range(i + 1, nxt):
            if HEADING_RE.match(lines[j]):
                end = j
                break
        else:
            end = nxt
        body = lines[i:end]
        while body and not body[-1].strip():
            body.pop()
        code, rest = m.group("code"), m.group("rest")
        if m.group("form") == "**":
            title = _bold_text(rest, lines[i + 1:end])
            # Tag-first box: the bold holds only the tag, the title is on a later line.
            if not _clean_title(title):
                for ln in lines[i + 1:min(end, i + 4)]:
                    tm = TITLE_LINE_RE.match(ln)
                    if tm and tm.group("code") == code:
                        k = lines.index(ln, i + 1)
                        title = _bold_text(tm.group("rest"), lines[k + 1:end])
                        break
        else:
            title = _heading_text(rest, lines[i + 1:end])
        head = " ".join(lines[i:min(end, i + 3)])
        tm = TAG_RE.search(head)
        boxes.append({
            "code": code,
            "title": _clean_title(title) or "(no title)",
            "tag": tm.group(1).strip() if tm else "",
            "file": name,
            "line": i + 1,
            "checkbox": bool(m.group("box")),
            "text": "\n".join(body),
        })
    return boxes


def source_names(root=ROOT, reader=None):
    """The files read, in a stable order: the universal file first, then the checklists."""
    if reader is not None:
        names = reader.names()
    else:
        names = [p.name for p in Path(root).glob(SOURCE_GLOB)]
        if (Path(root) / UNIVERSAL_NAME).is_file():
            names.append(UNIVERSAL_NAME)
    chk = sorted(n for n in names if n != UNIVERSAL_NAME)
    return ([UNIVERSAL_NAME] if UNIVERSAL_NAME in names else []) + chk


class DiskReader:
    def __init__(self, root=ROOT):
        self.root = Path(root)

    def names(self):
        out = [p.name for p in self.root.glob(SOURCE_GLOB)]
        if (self.root / UNIVERSAL_NAME).is_file():
            out.append(UNIVERSAL_NAME)
        return out

    def read(self, name):
        p = self.root / name
        return p.read_text(encoding="utf-8") if p.is_file() else None


class IndexReader:
    """Reads the STAGED copies (git's index), for the pre-commit guard."""

    def __init__(self, root):
        self.root = str(root)

    def _git(self, *args):
        r = subprocess.run(["git", "-C", self.root] + list(args), capture_output=True,
                           creationflags=flags())
        if r.returncode != 0:
            return None
        return r.stdout.decode("utf-8", errors="replace")

    def names(self):
        out = self._git("ls-files", "--", SOURCE_GLOB, UNIVERSAL_NAME) or ""
        return [n for n in out.splitlines() if n and "/" not in n]

    def read(self, name):
        return self._git("show", ":" + name)


def parse_prefix_map(text):
    """The prefixes named in the router's map paragraph, sorted; [] if it is not there."""
    if not text:
        return []
    i = text.find(MAP_START)
    if i < 0:
        return []
    para = text[i:].split("\n\n", 1)[0]
    # "except PA1, PA3, AR7 ..." names single codes; their prefixes are already listed.
    return sorted(set(MAP_PREFIX_RE.findall(para)))


def collect(reader):
    """(boxes, duplicates, prefixes_mapped). `boxes` keeps every box, duplicates included."""
    boxes = []
    for name in source_names(reader=reader):
        t = reader.read(name)
        if t:
            boxes.extend(parse_text(name, t))
    seen = {}
    for b in boxes:
        seen.setdefault(b["code"], []).append(b)
    dups = {c: v for c, v in seen.items() if len(v) > 1}
    mapped = parse_prefix_map(reader.read(ROUTER_NAME))
    return boxes, dups, mapped


# ---- PREFIX NAMES (o9L1232). The human, 2026-10-10: the Reader's group headers showed only "FG",
# "FM", "AR". Order of precedence: RULED_NAMES (o3's ruling) beats a name parsed from the
# checklist text, which beats FALLBACK_NAMES; every use of the fallback is reported.
# Three shapes in the checklists, tried in this order (the first name found for a prefix wins):
#   inline   `FM1-FM6` (Section 8 - format and mechanical)   a range, then a parenthesis
#   heading  ## Section 4b - THE REACH MOVES (`RC1-RC7`)      a heading naming its range
#   table    | 4 - Strong, empowering language | `SG1-SG7` |   a route-table row
INLINE_NAME_RE = re.compile(r"`([A-Z]{2})[0-9]+-\1[0-9]+`\s*\(([^()`]*)\)")
HEADING_NAME_RE = re.compile(
    r"^#{1,6} (?:Section \w+ - )?([^`(]+?)\s*\(`([A-Z]{2})[0-9]+-\2[0-9]+`\)", re.M)
TABLE_NAME_RE = re.compile(r"^\|\s*([^|`]+?)\s*\|\s*\**`([A-Z]{2})[0-9]+-\2[0-9]+`", re.M)

#: Names ruled by o3 (owner of the rule wording) on 2026-10-10, recorded under o9 W183. They
#: win over anything parsed from the checklist text. Change one only on o3's word.
RULED_NAMES = {
    "LB": "Line length and enrichment",
    "LN": "Length and runtime",
    "RF": "Referent test and no negative callbacks",
    "TY": "Script type, register, imagery, person and ending",
    "US": "American spelling, no honesty tags, instruction lines",
}

#: Used only for a prefix that is not ruled and that no checklist names with a shape above.
FALLBACK_NAMES = {
    "AR": "Reprogramming arc",         # file 7 title: "THE REPROGRAMMING ARC"
    "PA": "Pause cues",                # file 2B title: "THE PAUSE CUES"
    "UW": "Dash rule",                 # UW1-UW6 all sit under AUTHORING-CHECKLIST-UNIVERSAL.md U1
}


def _sentence(name):
    name = re.sub(r"\s+", " ", name.replace("*", "")).strip(" -.:")
    name = re.sub(r"^(?:\d+[a-z]?|Section \w+)\s+-\s+", "", name)
    name = re.sub(r"^[Tt][Hh][Ee]\s+", "", name)
    name = name.split(" + ")[0]      # "Banned words + AI-cliche scan" -> "Banned words"
    if name and name == name.upper():
        name = name.lower()
    return name[:1].upper() + name[1:]


def _inline_name(raw):
    raw = re.sub(r"\s+", " ", raw).strip()
    if raw.count(",") >= 3:      # a list of topics, not a name
        return ""
    if raw.startswith("Section "):
        raw = raw.split(" - ")[-1]
    return _sentence(raw.split(",")[0])


def parse_prefix_names(texts):
    """{prefix: (name, source)} from {filename: text}. `source` is the text the name came from."""
    out = {}
    for kind in ("inline", "heading", "table"):
        for fname in sorted(texts):
            text = texts[fname].replace("\r\n", "\n")
            if kind == "inline":
                flat = re.sub(r"\s+", " ", text)
                hits = [(m.group(1), _inline_name(m.group(2)), m.group(0))
                        for m in INLINE_NAME_RE.finditer(flat)]
            elif kind == "heading":
                hits = [(m.group(2), _sentence(m.group(1)), m.group(0))
                        for m in HEADING_NAME_RE.finditer(text)]
            else:
                hits = [(m.group(2), _sentence(m.group(1)), m.group(0))
                        for m in TABLE_NAME_RE.finditer(text)]
            for prefix, name, src in hits:
                if name and prefix not in out:
                    out[prefix] = (name, "%s (%s): %s" % (fname, kind, src.strip()))
    return out


def prefix_names(reader, prefixes):
    """({prefix: name}, {prefix: source}, [prefixes that used the fallback or have no name])."""
    texts = {}
    for n in source_names(reader=reader):
        t = reader.read(n)
        if t:
            texts[n] = t
    parsed = parse_prefix_names(texts)
    names, sources, fallback = {}, {}, []
    for p in sorted(prefixes):
        if p in RULED_NAMES:
            names[p], sources[p] = RULED_NAMES[p], "RULED_NAMES in rule_codes_index.py (o3)"
        elif p in parsed:
            names[p], sources[p] = parsed[p]
        elif p in FALLBACK_NAMES:
            names[p], sources[p] = FALLBACK_NAMES[p], "fallback map in rule_codes_index.py"
            fallback.append(p)
        else:
            names[p], sources[p] = "", "NO NAME"
            fallback.append(p)
    return names, sources, fallback


def prefix_label(prefix, names):
    return "%s - %s" % (prefix, names[prefix]) if names.get(prefix) else prefix


def _sort_key(code):
    return (code[:2], int(code[2:]))


def by_code(boxes):
    """{code: box}, the FIRST box wins for a duplicated code (the page reports duplicates)."""
    out = {}
    for b in boxes:
        out.setdefault(b["code"], b)
    return out


def missing_prefixes(boxes, mapped):
    have = {b["code"][:2] for b in boxes}
    return [p for p in mapped if p not in have]


def unmapped_prefixes(boxes, mapped):
    if not mapped:
        return []
    return sorted({b["code"][:2] for b in boxes} - set(mapped))


def _cell(s):
    return s.replace("|", "\\|").replace("\n", " ")


def render(boxes, dups, mapped, names=None):
    codes = by_code(boxes)
    prefixes = sorted({c[:2] for c in codes})
    names = names or {}
    out = [
        "<!-- GENERATED by .shared/scripts/rule_codes_index.py - do not hand-edit. Change a box "
        "in its checklist file, then run `python .shared/scripts/rule_codes_index.py --write`. "
        "The pre-commit hook refuses a checklist commit whose staged page is stale. -->",
        "<!-- route-tags: rule codes index lookup what does HV9 mean TY7 UW2 AR7 script "
        "checklist box codes prefixes -->",
        "",
        "# Rule codes index",
        "",
        "Every script-rule code (the `[XX9]` boxes in the `SCRIPT-CHECKLIST-*.md` files and "
        "`AUTHORING-CHECKLIST-UNIVERSAL.md`), one row each. The Reader shows a box's full text at "
        "`%s#rule/<CODE>` and lists every code at `%s#rule`. Which file to read for which task "
        "is in `AUTHORING-CHECKLIST.md`." % (READER, READER),
        "",
        "**%d codes, %d prefixes, %d files.**" % (
            len(codes), len(prefixes), len({b["file"] for b in boxes})),
        "",
    ]
    miss = missing_prefixes(boxes, mapped)
    extra = unmapped_prefixes(boxes, mapped)
    if miss or extra or dups:
        out.append("## Problems the generator found")
        out.append("")
        for p in miss:
            out.append("- Prefix `%s` is mapped in `%s` but no box was found." % (p, ROUTER_NAME))
        for p in extra:
            out.append("- Prefix `%s` has boxes but is not in the map in `%s`."
                       % (p, ROUTER_NAME))
        for c in sorted(dups, key=_sort_key):
            out.append("- `%s` opens a box in more than one place: %s. The first is used."
                       % (c, ", ".join("`%s`" % b["file"] for b in dups[c])))
        out.append("")
    out.append("## Prefixes")
    out.append("")
    out.append("| Prefix | Name | Codes | File |")
    out.append("|---|---|---|---|")
    for p in prefixes:
        mine = sorted((c for c in codes if c[:2] == p), key=_sort_key)
        files = sorted({codes[c]["file"] for c in mine})
        out.append("| %s | %s | %s | %s |" % (p, _cell(names.get(p, "")), ", ".join(mine),
                                         ", ".join("[%s](%s)" % (f, f) for f in files)))
    out.append("")
    out.append("## Codes")
    out.append("")
    out.append("| Code | Title | Tag | File | Box |")
    out.append("|---|---|---|---|---|")
    for c in sorted(codes, key=_sort_key):
        b = codes[c]
        title = b["title"] + ("" if b["checkbox"] else " (no checkbox)")
        out.append("| %s | %s | %s | [%s](%s) | [open](%s#rule/%s) |" % (
            c, _cell(title), _cell(b["tag"]), b["file"], b["file"], READER, c))
    out.append("")
    return "\n".join(out)


_CACHE = {}


def load(root=ROOT):
    """The Reader's data: {"codes": {code: box}, "order": [codes]}. Cached by source mtimes."""
    reader = DiskReader(root)
    names = source_names(reader=reader)
    stamp = []
    for n in names:
        try:
            stamp.append((n, os.path.getmtime(os.path.join(str(root), n))))
        except OSError:
            stamp.append((n, None))
    key = str(root)
    hit = _CACHE.get(key)
    if hit and hit[0] == stamp:
        return hit[1]
    boxes, _d, _m = collect(reader)
    codes = by_code(boxes)
    pn, ps, pf = prefix_names(reader, {c[:2] for c in codes})
    data = {"codes": codes, "order": sorted(codes, key=_sort_key),
            "names": pn, "name_sources": ps, "name_fallback": pf}
    _CACHE[key] = (stamp, data)
    return data


def _problems(boxes, mapped, names=None):
    out = []
    for p, n in sorted((names or {}).items()):
        if not n:
            out.append("prefix %s has no name: name it in its checklist or add it to "
                       "FALLBACK_NAMES" % p)
    if not mapped:
        out.append("the prefix map in %s was not found" % ROUTER_NAME)
    for p in missing_prefixes(boxes, mapped):
        out.append("prefix %s is mapped but has no box" % p)
    return out


def main(argv):
    root = ROOT
    if "--root" in argv:
        root = Path(argv[argv.index("--root") + 1])
    if "--precommit" in argv:
        try:
            return precommit(root if "--root" in argv else None)
        except Exception as e:  # a broken guard must never block a commit
            print("rule_codes_index: guard error, allowing the commit: %s" % e, file=sys.stderr)
            return 0
    boxes, dups, mapped = collect(DiskReader(root))
    if "--json" in argv:
        print(json.dumps(by_code(boxes), ensure_ascii=False, indent=1))
        return 0
    reader = DiskReader(root)
    pn, _ps, pf = prefix_names(reader, {b["code"][:2] for b in boxes})
    page = render(boxes, dups, mapped, pn)
    out = Path(root) / OUT_NAME
    if "--write" in argv:
        out.write_text(page, encoding="utf-8", newline="\n")
        print("wrote %s: %d codes, %d duplicates" % (out, len(by_code(boxes)), len(dups)))
        if pf:
            print("names from the fallback map (not named in a checklist): %s" % ", ".join(pf))
        for p in _problems(boxes, mapped, pn):
            print("PROBLEM: %s" % p)
        return 0
    if "--check" in argv:
        rc = 0
        cur = out.read_text(encoding="utf-8") if out.is_file() else None
        if cur is None or cur.replace("\r\n", "\n") != page:
            print("STALE: %s does not match its sources. Run: python "
                  ".shared/scripts/rule_codes_index.py --write" % OUT_NAME)
            rc = 1
        for p in _problems(boxes, mapped, pn):
            print("PROBLEM: %s" % p)
            rc = 1
        if rc == 0:
            print("ok: %s is current (%d codes)" % (OUT_NAME, len(by_code(boxes))))
        return rc
    print(__doc__)
    return 2


def precommit(root=None):
    """Exit 9 when this commit stages a source or the page, and the staged page is stale."""
    if root is None:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                             creationflags=flags())
        if top.returncode != 0:
            return 0
        root = top.stdout.decode("utf-8", errors="replace").strip()
    reader = IndexReader(root)
    staged = reader._git("diff", "--cached", "--name-only") or ""
    staged = [s for s in staged.splitlines() if s]
    # The router is watched too: the page's "Problems" section is built from its prefix map.
    watched = set(source_names(reader=reader)) | {OUT_NAME, ROUTER_NAME}
    if not any(s in watched for s in staged):
        return 0
    boxes, dups, mapped = collect(reader)
    if not boxes:
        return 0
    pn, _ps, _pf = prefix_names(reader, {b["code"][:2] for b in boxes})
    page = render(boxes, dups, mapped, pn)
    cur = reader.read(OUT_NAME)
    if cur is not None and cur.replace("\r\n", "\n") == page:
        return 0
    print("BLOCKED by rule_codes_index.py: this commit changes a script checklist (or %s), and "
          "the staged %s does not match the staged checklists. Run:\n"
          "  python .shared/scripts/rule_codes_index.py --write\n"
          "then stage %s and commit again." % (OUT_NAME, OUT_NAME, OUT_NAME), file=sys.stderr)
    return REFUSE


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
