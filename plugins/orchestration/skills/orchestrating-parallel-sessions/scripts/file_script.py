#!/usr/bin/env python3
"""file_script.py - the filing command for script-library/ (o10:D144, o10:W226).

<!-- subject: script-writing -->

One script = one folder holding one record file: a file named `<NAME> v<N> <AUTHOR>.md`, the `(in app)` one for a live
script (record_file below). Older texts are in the flat versions/ folder under the same name shape. No file is named
SCRIPT.md (W235 step 7). The header at the top of the record file decides where the folder lives; this command keeps the folder in step with the header.

Modes
  split   --git-repo <dir>  split the app's import files (voice_scripts/Working, as git stores
                            them at --rev) into
                            3-final/<line>/<tab>/<CC-category>/<LABEL>-<slug>/<LABEL> v1 agent (in app).md
  build   --out <dir>       rebuild every import file from 3-final/ (byte for byte). Runs every `check` rule
                            first and refuses (exit 2, nothing written) when check reports a problem; there
                            is no flag to skip that. Scripts with `Release: held` are left out.
  build   --app <app worktree>
                            write voice_scripts/Working/**, voice_scripts/RETIRED-SLUGS.json (the list of retired
                            slugs the app's import hides) and voice_scripts/LIBRARY-BUILD.json (the workspace
                            commit, each import file's sha256 and the list's sha256) into the app repo's working
                            tree, so an app PR can carry the library's output. Implies --strict; refuses a workspace tree with
                            uncommitted changes in script-library/ or in this file.
  release <3-final folder>  put a held script into the app: render its approved draft into an import-file chunk
                            (render_chunk, ported from the app's promote-approved-scripts.mjs), give it its slug,
                            append it to the end of 50_promoted_<category>_merged.md (a category with no import file
                            at all gets <NN>_<slug>_merged.md instead: import_file_name), update the baseline hash
  revise  <folder> --from <file>
                            REPLACES the text the app plays. To add a candidate version without changing the app, use
                            `place <text> --rewrite-of <LABEL>`, not revise.
                            replace the text of a script that is already in the app (keeps heading number, tag
                            and slug; the old text goes to versions/). Also works on a script in 1-drafts/ or
                            2-ready-for-owner/: the new text becomes `<NAME> v<N+1> <AUTHOR>.md`, the old record file goes to
                            versions/ under its own name (nothing is overwritten), the header fields this tool
                            owns carry over from the old record file, and no import file or stamp changes
                            revise, release, file and sync-bodies refuse to change the text of a record that carries
                            `## Approved-By:` (the human approved it) unless the run passes --owner-ruling <ref>; see
                            script-library/README.md (the the human-approved mark) and script-library/THE HUMAN-APPROVED.csv
  drift   --git-repo R --rev C
                            read only: build the import files and compare them with the app's blobs at C
  check                  list misfiled scripts, label clashes, lines without a code;
                            rewrite INDEX.md; exit non-zero when anything is wrong
  file    <path> --stage S  move a script folder to the folder its header says, with
                            git mv; give it a label when it enters a category
                            --type AT|SC changes only the type: new label, old label kept as an alias,
                            body tag rewritten, import-file baseline hash updated.
                            A script the app imports may be moved to another category (--category, --tab, --line;
                            its slug stays and the next build --app puts it in the new category's import file) or
                            retired (--stage retired: it leaves its import file and its slug is added to
                            RETIRED-SLUGS.json in the same write). Bringing a retired script back is not supported.
  adopt  <file> --type --stage --id ...   give a loose draft/ready/retired file the library header, in place
  place   <file>            move an adopted file into the library (git mv) to the folder its header says
  sync-bodies --git-repo R --rev C --accept-app-edit
                            recovery only (the library is the source of the import files now, so this needs
                            --accept-app-edit): pull body-text changes for live scripts from the app's blobs at commit C:
                            rewrites only the bodies that differ and their import file's baseline hash;
                            refuses a changed heading, slug, position or script count (those are moves)
  rename-to-label [--commit]  rename every labelled script's texts to its Label (a pure git mv; dry run without --commit; W238)
  rename-to-title [--apply]   put each script's title into its file names (`SC-Sl-D-01-001 High-Achievers-Rest v1 agent.md`; a pure git mv,
                            .edit-history and .why-notes follow; dry run without --apply; o10L431)
  add-line <name>           register a product line and give it a two-letter code
  reserve --type AT|SC --line L --tab night|day --category NN[-name] --title "<working title>" [--by o5] [--dry-run] [--json]
                            give a script its real label before it is written (o10L475). Takes the next number of the category
                            (the counter moves) and records label, title, who and the date in RESERVED-LABELS.json. The script's
                            header then says `## Label: <that label>`; `adopt`, `place` and `file` keep it and mark the
                            reservation used (the entry stays, with who used it, so a second script cannot take it). They refuse,
                            changing nothing, a label that was never given out (not reserved, number above the category's
                            counter), a reserved label a second script tries to use, a label `unreserve` released, and an open
                            reservation whose type, line, tab or category the header has but differs from the reservation (one
                            the header lacks is filled in from the reservation, o10L477). `check` lists open reservations, with
                            their age in days, as information (not a problem). One reserve at a time (a lock file, o10L477)
  unreserve <label>         drop an unused reservation. The number stays spent (LABEL-COUNTERS.json is not touched), never reused,
                            and the label is refused by adopt, place and file from then on

A rewrite is a version, not a new script (W243). `file` (when it gives a script its first label) and `place` (when it makes a
new script folder) refuse when a script of the same Type in the same category has the same title (a leading The/A/An ignored,
`&` read as `and`) or a slug that this one extends; the refusal
names that script's folder and prints the command that adds the text as its next version, and `--new-label-anyway` makes the
new script anyway. A text with `## Rewrite-Of: <LABEL>` in its header, or `--rewrite-of <LABEL>` on `file` or `place`, is
added to that script's folder as its next version: a plain text beside the record, so the app text and the import files
do not change. A text that is a script of its own (`(in app)`, live, final or labelled) is never added that way.

Python 3, standard library only. Reads and writes bytes for script text, so CRLF and
every other byte of an import file survives a split followed by a build unchanged.
"""
import argparse
import contextlib
import csv
import datetime
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from no_window import flags  # keeps git's console off the human's screen
except ImportError:  # a partial delivery must not take the tool down
    def flags(extra=0):
        return extra
import training_header  # parse_script_name: the library file-name shapes (W235)
from script_notes import strip_notes  # the human's {{{ }}} review notes: the ONE rule (o8L157); check_script.py reads it too
from script_notes import strip_strikes  # the human's ~~ ~~ struck text: never spoken, out of the app text and the approval hash (o10L457)
import pause_cues as _pause_cues_mod  # the one pause-cue rule (o10L639): ((pause Ns)) alone on its own line
from script_notes import strip_comments  # the {{ }} comments (W245: the notes-only rule compares spoken text with both removed)

END_MARK = "<!-- END SCRIPT HEADER -->"
STAGES = {
    "draft": "1-drafts",
    "ready": "2-ready-for-owner",
    "final": "3-final",
    "retired": "4-retired",
}
STAGE_DIRS = list(STAGES.values())
TABS = {"night": "N", "day": "D"}

# Same shapes the app's importer reads (product-app/server/scripts/importVoiceScripts.ts).
SCRIPT_HDR = re.compile(r"^##\s+Script\s+\d+(?:\s+\[([A-Z][A-Z-]*)\])?:\s*(.*)$")
SLUG_MARKER = re.compile(r"^\s*<!--\s*slug:\s*([a-z0-9][a-z0-9-]*)\s*-->\s*$")
SEPARATOR = re.compile(r"^\s*---\s*$")
H1 = re.compile(r"^#\s+(.*)$")
LABEL_RE = re.compile(r"^(AT|SC)-([A-Z][a-z])-([ND])-(\d{2})-(\d{3})$")
KEY_RE = re.compile(r"^##\s+([A-Za-z][A-Za-z-]*):[ \t]*(.*?)\s*$")

HEADER_ORDER = [
    "Title", "Label", "Type", "Line", "Tab", "Category", "Stage", "ID",
    "Slug", "Working-Slug", "Aliases", "Source", "Source-position",
    "Version-Of", "Version", "Author", "Origin", "Source-File",
    # o10 W239: the mark of a script the human approved (`## Approved-By: the human <date> <commit>`) and the rulings that let its
    # text change since. Both are carried over by `revise`, so a new version keeps the mark.
    "Approved-By", "Approved-Ruling",
]
# `Release: held` goes right after Stage. The only value is `held`; no field means released.
HEADER_ORDER.insert(HEADER_ORDER.index("Stage") + 1, "Release")
# `App: live` (o10:W235) says the script is built into an import file, whatever stage folder it sits in. It goes
# right after Source-position, the other field about the import file.
HEADER_ORDER.insert(HEADER_ORDER.index("Source-position") + 1, "App")
# render_header also knows `Opening` (the script writer.s declared opening, o9 W-opening), which sits right after `App` in the
# 198 files that carry it: without a place of its own it was written after Source-File, and a rewrite of the file moved it.
# HEADER_ORDER itself stays the list of fields `file`/`revise` own. A live revise (`cmd_revise`) gives the new record file the
# Opening its new text declares; when the new text declares none it keeps the old record's valid Opening, as
# check_script.inherited_opening does for a the human rewrite (o10L363 M3, `declared_opening`). The draft revise
# (`revise_unreleased`) takes its Opening only from the new draft's own header.
RENDER_ORDER = list(HEADER_ORDER)
RENDER_ORDER.insert(RENDER_ORDER.index("App") + 1, "Opening")

# The library's 32 categories (15 night, 17 day) as the importer groups them: the "app" string
# is the category after the importer strips the H1 prefix. The Morning Verdict is app
# category 4 in the Daytime tab, same as Morning Energy; the library numbers it 17.
# Night 04 Sleep Hygiene (o5 D74, 2026-10-13) is new in the library and holds no script yet;
# its app_category string is provisional, so a rename touches this line and CATEGORIES.json.
DEFAULT_CATEGORIES = [
    ("night", "01", "sleep-anxiety", "Sleep Anxiety and Pre-Sleep Worry"),
    ("night", "02", "middle-of-night-awakenings", "Middle-of-Night Awakenings and 3 AM Racing Thoughts"),
    ("night", "03", "sleep-onset", "Sleep Onset and Falling Asleep Faster"),
    ("night", "04", "sleep-hygiene", "Sleep Hygiene and Getting to Bed"),
    ("night", "05", "noise-disturbances", "Sleeping Through Noise and Disturbances"),
    ("night", "06", "shift-work", "Shift Work and Irregular Schedules"),
    ("night", "07", "jet-lag-travel", "Jet Lag and Travel Sleep"),
    ("night", "08", "pain-discomfort", "Pain and Physical Discomfort"),
    ("night", "09", "grief-loss", "Grief, Loss, and Emotional Processing"),
    ("night", "10", "stress-work-rumination", "Stress, Work Rumination, and Deadline Anxiety"),
    ("night", "11", "identity-limiting-beliefs", "Identity and Limiting Beliefs About Sleep"),
    ("night", "12", "relationship-co-sleeping", "Relationship and Co-Sleeping"),
    ("night", "13", "screen-stimulation-winddown", "Screen and Stimulation Wind-Down"),
    ("night", "14", "nightmares-disturbing-dreams", "Nightmares and Disturbing Dreams"),
    ("night", "15", "perfectionism-control-orthosomnia", "Perfectionism, Control, and Orthosomnia"),
    ("day", "01", "focus-brain-fog", "Daytime focus, concentration, and brain fog"),
    ("day", "02", "exhaustion-energy", "Exhaustion, fatigue, and energy management"),
    ("day", "03", "low-mood", "Low mood and depression - the outcropping of exhaustion"),
    ("day", "04", "morning-energy", "Morning Energy, Waking, and Sleep Inertia"),
    ("day", "05", "daytime-anxiety", "Daytime anxiety and the all-day sleep-worry loop"),
    ("day", "06", "irritability-reactivity", "Irritability, reactivity, and the short fuse"),
    ("day", "07", "motivation-drive", "Motivation, drive, and procrastination"),
    ("day", "08", "performance-confidence", "Performance confidence and the high-achiever's edge"),
    ("day", "09", "stress-overwhelm", "Stress resilience and overwhelm"),
    ("day", "10", "afternoon-slump", "The afternoon slump and the circadian dip"),
    ("day", "11", "caffeine-reliance", "Caffeine reliance and the energy crutch"),
    ("day", "12", "productivity-guilt", "Productivity guilt and the exhaustion-shame spiral"),
    ("day", "13", "social-relational-energy", "Social and relational energy"),
    ("day", "14", "recovery-hope", "Recovery, hope, and reclaiming more living"),
    ("day", "15", "foundational", "Cross-category daytime tools"),
    ("day", "16", "daytime-reset", "Daytime Reset"),
    ("day", "17", "morning-verdict", "The morning verdict - decoupling today from last night"),
]


class LibraryError(Exception):
    pass


# --------------------------------------------------------------------------- helpers

def slugify(title):
    """Same rule as the app's slugify (importVoiceScripts.ts)."""
    import unicodedata
    s = unicodedata.normalize("NFKD", title)
    s = re.sub(r"['’]", "", s).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def trim_slug(s, limit=40):
    """Keep folder names short (Windows path length); cut at a hyphen when possible."""
    if len(s) <= limit:
        return s
    cut = s[:limit]
    if "-" in cut:
        cut = cut[: cut.rindex("-")]
    return cut.strip("-") or s[:limit]


def load_json(path, default):
    p = Path(path)
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def save_json(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes((json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))


def is_consonant(ch):
    return ch.isalpha() and ch.lower() not in "aeiou"


def fold_accents(s):
    """Turn accented letters into their plain ASCII letters (Energie with an accent -> Energie)."""
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def check_chosen_code(code, used_codes):
    """Validate a code picked by hand: one capital letter then one lower-case letter, unused."""
    if not re.match(r"^[A-Z][a-z]$", code or ""):
        raise LibraryError("a line code is a capital letter then a lower-case letter, like Sl; got %r" % code)
    if code.lower() in {c.lower() for c in used_codes}:
        raise LibraryError("code %s is already used or retired, and a code is never reused" % code)
    return code


def assign_code(name, used_codes):
    """Two-letter product-line code. First two letters by default (Productivity -> Pr,
    Perspective -> Pe). On a clash the older line keeps its code, and the newer line takes
    its first letter plus the next consonant in its name that gives an unused code
    (Professionalism -> Pf)."""
    letters = re.sub(r"[^A-Za-z]", "", fold_accents(name))
    if len(letters) < 2:
        raise LibraryError("a product line name needs at least two letters: %r" % name)
    used = {c.lower() for c in used_codes}
    first = letters[0].upper()
    candidates = [first + letters[1].lower()]
    for ch in letters[2:]:
        if is_consonant(ch):
            candidates.append(first + ch.lower())
    for cand in candidates:
        if cand.lower() not in used:
            return cand
    raise LibraryError("no unused two-letter code left for %r; choose one by hand" % name)


class Lib:
    def __init__(self, root):
        self.root = Path(root)
        self.dir = self.root / "script-library"
        self.build_dir = self.dir / "_build"

    # registries
    def lines(self):
        return load_json(self.dir / "LINES.json", {"lines": [], "retired_codes": []})

    def categories(self):
        return load_json(self.dir / "CATEGORIES.json", {"categories": []})

    def counters(self):
        return load_json(self.dir / "LABEL-COUNTERS.json", {})

    def line_code(self, slug):
        for ln in self.lines()["lines"]:
            if ln["slug"] == slug:
                return ln["code"]
        return None

    def category_by_folder(self, line, tab, folder):
        for c in self.categories()["categories"]:
            if c["line"] == line and c["tab"] == tab and c["folder"] == folder:
                return c
        return None

    def iter_scripts(self):
        """The record file of every script folder in the four stage folders, in path order."""
        for sd in STAGE_DIRS:
            base = self.dir / sd
            if not base.is_dir():
                continue
            found = []
            for dirpath, dirnames, _ in os.walk(str(base)):
                dirnames[:] = [d for d in dirnames if d not in NOT_SCRIPT_DIRS]
                p = record_file(dirpath)
                if p is not None:
                    found.append(p)
            for p in sorted(found):
                yield p


# --------------------------------------------------------------------------- record files

# Folders that hold old texts and editor snapshots, never a script's own record file.
NOT_SCRIPT_DIRS = ("versions", ".edit-history")
RECORD_KINDS = ("draft", "rewrite")
_DUPLICATE_NAME = re.compile(r" duplicate(?: \(in app\))?\.md$")


def _carries_header(path):
    try:
        return (END_MARK + "\n").encode("utf-8") in Path(path).read_bytes()
    except OSError:
        return False


_STAGE_LINE = re.compile(r"^##\s+Stage:", re.M)


def _is_record_header(path):
    """True when the file has a library header: the END marker, and a `## Stage:` line above it. A the human rewrite that
    still holds the pilot's own front matter above the marker (a Type, a Source, a Word-bank, no Stage) is a text,
    not a second record file (o10L318 H1)."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return False
    i = data.find((END_MARK + "\n").encode("utf-8"))
    return i >= 0 and bool(_STAGE_LINE.search(data[:i].decode("utf-8", "replace")))


_RECORD_HEADER_LINE = re.compile(r"^##\s+(?:Stage|Label|ID):", re.M)


def _shows_record_header(path):
    """True when the start of the file has a `## Stage:`, `## Label:` or `## ID:` line: lines only a library record header
    carries (a pilot's own front matter has Source, Type, Energy state and the like)."""
    try:
        with open(str(path), "rb") as fh:
            return bool(_RECORD_HEADER_LINE.search(fh.read(8000).decode("utf-8", "replace")))
    except OSError:
        return False


def record_files(folder):
    """Every record file directly in the folder, best first. A record file is a file named
    `<NAME> v<N> <AUTHOR>[ (in app)].md` (training_header.parse_script_name) whose header holds a `## Stage:` line.
    The human's rewrites (their front matter has no `## Stage:`, or they have no header at all), logs, notes
    and ` duplicate` copies are not record files. Order: the `(in app)` file, then the highest version number.
    A file named SCRIPT.md is not a record file (W235 step 7); `check` reports one."""
    folder = Path(folder)
    try:
        names = sorted(os.listdir(str(folder)))
    except OSError:
        return []
    in_app, rest = [], []
    for n in names:
        if not n.endswith(".md") or _DUPLICATE_NAME.search(n):
            continue
        pn = training_header.parse_script_name(n)
        if pn.version is None or pn.kind not in RECORD_KINDS:
            continue
        p = folder / n
        if not p.is_file() or not _is_record_header(p):
            continue
        (in_app if pn.in_app else rest).append((pn, p))
    rest.sort(key=lambda t: (-t[0].version, t[1].name))
    in_app.sort(key=lambda t: (-t[0].version, t[1].name))
    return [p for _, p in in_app] + [p for _, p in rest]


def record_file(folder):
    """The folder's record file (the first of record_files), or None when it has none."""
    found = record_files(folder)
    return found[0] if found else None


_VERSION_OF_LINE = re.compile(r"^##\s+Version-Of:[ \t]*(\S.*?)[ \t\r]*$", re.M)


def header_version_of(path):
    """The `## Version-Of:` value in the header of the text at `path` (the lines above `<!-- END SCRIPT HEADER -->`), or
    None when the file cannot be read, has no END marker or has no such line. This is the family key (W238): every text
    of one script carries the same value, whatever its file name is, so a rename to the Label (or a later relabel) does
    not move a text into another family. `parse_script_name(...).name_key` is the fallback for a file without the line."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    i = data.find((END_MARK + "\n").encode("utf-8"))
    if i < 0:
        i = data.find((END_MARK + "\r\n").encode("utf-8"))
    if i < 0:
        return None
    m = _VERSION_OF_LINE.search(data[:i].decode("utf-8", "replace"))
    return m.group(1) if m else None


def name_key_of(header, pn):
    """The NAME a text of this script is filed under: its Label once it has one, else the name key it already has
    (`<TYPE>-<ID>`, design o10L292 section d; W238)."""
    return (header.get("Label") or "").strip() or pn.name_key


def family_of(header, pn):
    """The family key a new text of this script carries in `## Version-Of:`: the record's own Version-Of line, else its
    name key. It is not rewritten when the NAME changes (W238)."""
    return (header.get("Version-Of") or "").strip() or pn.name_key


def headerless_record_names(folder):
    """The files directly in the folder whose NAME is a record file's (`<NAME> v<N> <AUTHOR>[ (in app)].md`, not a log,
    note or ` duplicate`) but that carry no `<!-- END SCRIPT HEADER -->` line, as [(path, in_app)]. record_files leaves
    them out; a damaged header (CRLF endings, a deleted marker) is one. A the human rewrite (not `(in app)`) is never listed:
    the Reader saves it without a header (o10L329, review o10L326 X1)."""
    folder = Path(folder)
    try:
        names = sorted(os.listdir(str(folder)))
    except OSError:
        return []
    out = []
    for n in names:
        if not n.endswith(".md") or _DUPLICATE_NAME.search(n):
            continue
        pn = training_header.parse_script_name(n)
        if pn.version is None or pn.kind not in RECORD_KINDS:
            continue
        # o10L329 (review o10L326 X1): a the human rewrite (`<NAME> v<N> THE HUMAN.md`) is never a damaged record file. The Reader saves
        # one with its header stripped into any folder that holds a versioned text, with or without a record file beside
        # it. A headerless `(in app)` the human file is still reported: the text in the app always carries the header.
        if pn.kind == "rewrite" and not pn.in_app:
            continue
        p = folder / n
        if not p.is_file():
            continue
        if _is_record_header(p):
            continue                                     # a record file: record_files has it
        if pn.in_app:
            # the text in the app always carries the header; a the human text with the pilot's front matter above the marker and no
            # Stage is a normal text (o10L318 H1)
            if not _carries_header(p) or pn.kind != "rewrite":
                out.append((p, True))
        elif not _carries_header(p) or _shows_record_header(p):
            # an agent-named text with no header at all (no END marker line) is damaged too: its whole header is gone (o10L332, C3).
            # not a record file, but it still shows record-header lines (a deleted marker, CRLF endings, a lost Stage line): a
            # damaged header. A text that shows none (the pilot's own front matter: Source, Type, Draft note ...) is a plain
            # text, not a damaged record file: a lane's cut of the human's rewrite (`SC-pil-SC08 v4 o8.md`) is one (o10L329, X4).
            out.append((p, False))
    return out


# --------------------------------------------------------------------------- script files

def parse_script(path):
    """Return (header dict, body bytes). Body is everything after the END SCRIPT HEADER line."""
    data = Path(path).read_bytes()
    marker = (END_MARK + "\n").encode("utf-8")
    i = data.find(marker)
    if i < 0:
        raise LibraryError("%s: no %s line" % (path, END_MARK))
    head = data[:i].decode("utf-8")
    body = data[i + len(marker):]
    header = {}
    for line in head.split("\n"):
        m = KEY_RE.match(line)
        if m:
            header[m.group(1)] = m.group(2)
    return header, body


def render_header(header, bare=()):
    """The `## Key: value` lines. `bare`: keys whose value is empty and whose old line had no space after
    the colon; they are written the same way again."""
    keys = [k for k in RENDER_ORDER if k in header] + [k for k in header if k not in RENDER_ORDER]
    return "".join(("## %s:\n" % k) if (k in bare and header[k] == "") else ("## %s: %s\n" % (k, header[k]))
                   for k in keys)


def write_script(path, header, body, keep_from=None, drop=()):
    """Write a record file. keep_from: the file's current bytes. When its header holds lines that are not
    `## Key: value` fields (a title line, notes from before the file was adopted), they stay where they
    are and only the fields are updated, so a later `file` never drops them. drop: keys the file must not have
    (a revise drops `Origin` and `Source-File`, which describe the old text); they are removed from the header
    dict and, when the old lines are kept in place, from those lines too."""
    p = Path(path)
    if drop:
        header = {k: v for k, v in header.items() if k not in drop}
    p.parent.mkdir(parents=True, exist_ok=True)
    bare = set()
    if keep_from is not None:
        i = keep_from.find((END_MARK + "\n").encode("utf-8"))
        head_lines = keep_from[:i].decode("utf-8").split("\n")
        if head_lines and head_lines[-1] == "":
            head_lines.pop()
        if any(l.strip() and not KEY_RE.match(l) for l in head_lines):
            for gone in ("Release", "App", "Source", "Source-position") + tuple(drop):
                if gone not in header:  # a field the command removed (a held script that left stage final; a retired live one)
                    head_lines = [l for l in head_lines if not (KEY_RE.match(l) and KEY_RE.match(l).group(1) == gone)]
            new_head = set_fields(head_lines, header)
            p.write_bytes(("\n".join(new_head) + "\n" + END_MARK + "\n").encode("utf-8") + body)
            return
        for l in head_lines:
            m = KEY_RE.match(l)
            if m and m.group(2) == "" and re.match(r"^##\s+[A-Za-z][A-Za-z-]*:$", l.rstrip("\r")):
                bare.add(m.group(1))
    p.write_bytes((render_header(header, bare) + END_MARK + "\n").encode("utf-8") + body)


def aliases_of(header):
    return [a.strip() for a in header.get("Aliases", "").split(",") if a.strip()]


def slug_part(header):
    """Last part of the folder name: the app slug when there is one, else the Working-Slug (a draft
    that has no app slug yet; written by `adopt`), else the title."""
    s = header.get("Slug", "").strip()
    if s and s.lower() != "none":
        return trim_slug(s)
    w = header.get("Working-Slug", "").strip()
    if w:
        return w
    return trim_slug(slugify(header.get("Title", "")) or "untitled")


def folder_name(header):
    label = header.get("Label", "").strip()
    sp = slug_part(header)
    if label:
        return "%s-%s" % (label, sp)
    return "%s-%s-%s" % (header.get("Type", "XX"), header.get("ID", "noid"), sp)


NOT_YET_PLACED = "not-yet-placed"


def legacy_rel_dir(header):
    """The folder a draft, ready-for-the human or retired script had before o10:W235: <stage>/<TYPE>/<name>. Still
    accepted by `check` for drafts and ready scripts; required by nothing."""
    stage = header.get("Stage", "")
    if stage not in STAGES:
        raise LibraryError("unknown stage %r" % stage)
    return "/".join([STAGES[stage], header.get("Type", ""), folder_name(header)])


def has_placement(header):
    """True when the header has the Line, Tab and Category that put a script in the category tree."""
    return bool(header.get("Line", "").strip() and header.get("Tab", "").strip() and header.get("Category", "").strip())


def expected_rel_dir(header):
    """Folder (relative to script-library/) that the header says the script belongs in. Final, and draft or
    ready with a Line, Tab and Category: <stage>/<line>/<tab>/<category>/<name>. Draft or ready without them:
    <stage>/not-yet-placed/<TYPE>/<TYPE>-<ID>-<slug>. Retired: <stage>/<TYPE>/<name>."""
    stage = header.get("Stage", "")
    if stage not in STAGES:
        raise LibraryError("unknown stage %r" % stage)
    name = folder_name(header)
    if stage == "final" or (stage in ("draft", "ready") and has_placement(header)):
        return "/".join([STAGES[stage], header.get("Line", ""), header.get("Tab", ""),
                         header.get("Category", ""), name])
    if stage in ("draft", "ready"):
        bucket = "%s-%s-%s" % (header.get("Type", "XX"), header.get("ID", "noid"), slug_part(header))
        return "/".join([STAGES[stage], NOT_YET_PLACED, header.get("Type", ""), bucket])
    return legacy_rel_dir(header)


def make_label(type_, line_code, tab, cc, n):
    return "%s-%s-%s-%s-%03d" % (type_, line_code, TABS[tab], cc, n)


def label_prefix(label):
    m = LABEL_RE.match(label)
    return None if not m else m.group(1, 2, 3, 4)


# --------------------------------------------------------------------------- adopting a loose file (o10:W226 step 3)
#
# A draft, a ready-for-the human script or a retired one starts life as a loose file in some working folder.
# `adopt` gives it the library header IN PLACE (so nothing that reads the old place breaks), and `place`
# then moves it into the library with git mv. Nothing in the file is removed: the lines that were above
# the END SCRIPT HEADER marker stay where they were, library fields are added under them, and a field
# of the old header that means something else in the library (an old `## Type: autogenic`, a `## Source:`
# that is not an import file) is renamed `## Original-<Key>:`, never dropped.

LIB_KEYS = set(HEADER_ORDER)
# an existing value of these is kept as it is (the permanent identity of the file)
KEEP_EXISTING = {"ID", "Title"}


def to_lf(data):
    """The bytes as git stores them (LF). Only CRLF pairs change."""
    return data.replace(b"\r\n", b"\n")


def leading_head_len(lines):
    """For a file with no END SCRIPT HEADER marker: how many leading lines are header-like (a title line,
    `#` comment lines, `**Key:**` lines, bullets, quotes). It stops at the first plain paragraph, at a
    `---` line, or at the end. Trailing blank lines are not counted."""
    last = 0
    for i, ln in enumerate(lines):
        t = ln.strip()
        if not t:
            continue
        if t == "---" or not (t.startswith(("#", "**", "- ", "> ", ">")) or ln.startswith("  ")):
            break
        last = i + 1
    return last


def split_head(text):
    """(head lines, had marker, body lines) of LF text. Without the marker the head is the leading
    header-like run (leading_head_len); the body is everything after it."""
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if ln.strip() == END_MARK:
            return lines[:i], True, lines[i + 1:]
    n = leading_head_len(lines)
    return lines[:n], False, lines[n:]


def _field_line(key, value):
    return "## %s: %s" % (key, value)


def set_fields(head_lines, fields):
    """Set each field in a header, in place: a field already there gets the new value on its own line,
    others go at the end of the header (`Release` goes directly after `Stage`), just above the END SCRIPT HEADER marker (never after the last
    `## Key:` line: an old key such as `## Spine:` can have its value on the next line, and a field put
    between the two would take the value away from its key). A field whose value is already right keeps
    its line exactly as it is (an empty `## Spine:` does not gain a trailing space). Other lines are
    untouched."""
    out = list(head_lines)
    seen = set()
    for i, ln in enumerate(out):
        m = KEY_RE.match(ln)
        if m and m.group(1) in fields and m.group(1) not in seen:
            seen.add(m.group(1))
            if m.group(2) != fields[m.group(1)]:
                out[i] = _field_line(m.group(1), fields[m.group(1)])
    for k, v in fields.items():
        if k in seen:
            continue
        stage_at = next((i for i, ln in enumerate(out)
                         if KEY_RE.match(ln) and KEY_RE.match(ln).group(1) == "Stage"), None)
        if k == "Release" and stage_at is not None:
            # `Release: held` sits directly after `Stage` (plan 1.4) even when non-field lines come first
            out.insert(stage_at + 1, _field_line(k, v))
        else:
            out.append(_field_line(k, v))
    return out


# keys that mean "the import file this script is built into" in the library; a loose file's own
# `## Source:` is something else, so on a script file it is renamed, not left to confuse `check`
RESERVED_KEYS = ("Source", "Source-position", "Label")


def _rename_key(line, key):
    return re.sub(r"^##(\s+)" + re.escape(key) + ":", r"##\1Original-" + key + ":", line)


def adopt_text(text, fields):
    """Give LF text the library header fields. Returns (new text, notes). `fields` is an ordered dict of
    key -> value. A key the file already has with the same value is left alone; with a different value
    the old line becomes `## Original-<Key>:` and the new field is added (except ID and Title, where the
    file's own value stays and wins). Running it twice changes nothing the second time."""
    head, had_marker, body = split_head(text)
    notes = []
    is_version = "Version-Of" in fields and "Stage" not in fields      # a record also writes Version-Of (W235 step 7)
    keep_keys = KEEP_EXISTING | ({"Type"} if is_version else set(_STEP6_KEYS))
    existing = {}
    for i, ln in enumerate(head):
        m = KEY_RE.match(ln)
        if m and m.group(1) not in existing:
            existing[m.group(1)] = (i, m.group(2))
    # adopt is for loose files. A library script that is in an import file (Source and Source-position, and
    # `App: live` or final, not held) would lose its Stage and Source here, so it is refused (o10:W235 D1).
    live_hdr = {k: v[1].strip() for k, v in existing.items()}
    if (live_hdr.get("Source") and live_hdr.get("Source-position") and live_hdr.get("Release") != "held"
            and live_hdr.get("Stage") != "retired"
            and (live_hdr.get("App", "").lower() == "live" or live_hdr.get("Stage") == "final")):
        raise LibraryError("this file is a live script (Source %s); adopt is for loose files and would take it out "
                           "of the app's import file. Use `file` to change its stage. Nothing was changed."
                           % live_hdr["Source"])
    if (not is_version and "Version-Of" in fields and "Version-Of" not in existing and existing.get("ID", (0, ""))[1].strip()
            and existing["ID"][1].strip() != str(fields.get("ID", "")) and fields.get("Version-Of") == "%s-%s" % (fields.get("Type"), fields.get("ID"))):
        # the file's own ID wins, so the name key follows it (`place` sets the final name key from the Label)
        fields = dict(fields, **{"Version-Of": "%s-%s" % (fields.get("Type"), existing["ID"][1].strip())})
    add = {}
    for k, v in fields.items():
        if k in existing:
            old = existing[k][1]
            if old == v or (v == "" and old == ""):
                continue
            if k in keep_keys and old:
                notes.append("kept existing %s %r (wanted %r)" % (k, old, v))
                continue
            if k in LIB_KEYS:
                head[existing[k][0]] = _rename_key(head[existing[k][0]], k)
                notes.append("renamed old %s %r to Original-%s" % (k, old, k))
        add[k] = v
    if not is_version:
        for k in RESERVED_KEYS:
            if k in existing and k not in fields:
                head[existing[k][0]] = _rename_key(head[existing[k][0]], k)
                notes.append("renamed old %s %r to Original-%s" % (k, existing[k][1], k))
    if add:
        # just above the END SCRIPT HEADER marker, not after the last `## Key:` line: an old key like
        # `## Spine:` can have its value on the next line, and fields put between them would separate
        # the key from its value (o10L261 D1)
        head.extend(_field_line(k, v) for k, v in add.items())
    if not add and had_marker and not any(n.startswith("renamed") for n in notes):
        return text, notes
    return "\n".join(head + [END_MARK] + body), notes


def adopt_file(path, fields, write=True):
    """adopt_text on a file, in place, as LF. Returns (changed, notes)."""
    p = Path(path)
    old = p.read_bytes()
    new_text, notes = adopt_text(to_lf(old).decode("utf-8"), fields)
    new = new_text.encode("utf-8")
    changed = new != old
    if changed and write:
        p.write_bytes(new)
    return changed, notes


def adopt_fields(typ, stage, id_=None, title=None, tab=None, line=None, category=None,
                 working_slug=None, source_file=None, version_of=None, author=None):
    """The ordered fields `adopt` writes. A version file (version_of) gets only Type and Version-Of. A script gets
    the step-6 lines too (W235 step 7): Version-Of `<TYPE>-<ID>` (the name key `place` gives the file until it has a
    label), Version 1 and the Author (default agent); `place` sets all three from the file name it chooses."""
    if typ not in ("AT", "SC"):
        raise LibraryError("type must be AT or SC, got %r" % typ)
    f = {}
    if version_of:
        f["Type"] = typ
        f["Version-Of"] = version_of
        return f
    if stage not in ("draft", "ready", "retired"):
        raise LibraryError("adopt is for drafts, ready-for-the human and retired scripts; got stage %r" % stage)
    if not id_:
        raise LibraryError("a script needs an --id")
    if not title:
        raise LibraryError("a script needs a --title")
    f["Title"] = title
    f["Type"] = typ
    if line:
        f["Line"] = line
    if tab:
        if tab not in TABS:
            raise LibraryError("tab must be night or day, got %r" % tab)
        f["Tab"] = tab
    if category:
        f["Category"] = category
    f["Stage"] = stage
    f["ID"] = id_
    f["Slug"] = "none"
    if working_slug:
        f["Working-Slug"] = working_slug
    f["Aliases"] = ""
    f["Version-Of"] = "%s-%s" % (typ, id_)
    f["Version"] = "1"
    f["Author"] = author or "agent"
    if source_file:
        f["Source-File"] = source_file
    return f


def place_file(lib, src, rel_dir, name, use_git=True, untracked_ok=True):
    """Move the file src to script-library/<rel_dir>/<name>. Tracked files go with git mv (so history
    follows); others are moved plainly and the caller adds them by name. Refuses to overwrite."""
    src = Path(src)
    dest = lib.dir / rel_dir / name
    if dest.exists():
        raise LibraryError("destination already exists: %s" % dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    move_path(lib, src, dest, use_git=use_git, untracked_ok=untracked_ok)
    return dest


def placed_name(header):
    """The record-file name `place` gives an adopted file by default: `<NAME> v<N> <AUTHOR>.md`. NAME is the Label, or
    `<TYPE>-<ID>` for a script with no label yet (design o10L292 section d); N and the author are the header's Version
    and Author lines (adopt writes them), else 1 and agent."""
    key = header.get("Label", "").strip() or "%s-%s" % (header.get("Type", "XX"), header.get("ID", "noid"))
    try:
        ver = max(1, int(header.get("Version", "1")))
    except ValueError:
        ver = 1
    return version_name(key, ver, header.get("Author", "").strip() or "agent", title=file_title(header))


def older_draft_reason(reason):
    """The reason for `--older-draft`, validated: one line, not empty (o9:W199 defines the header line in training_header.py)."""
    r = (reason or "").strip()
    if not r or "\n" in r or "\r" in r:
        raise LibraryError("--older-draft needs a reason on one line, such as \"the human's rewrite replaced it\". Got %r." % reason)
    return r


def stamp_older_draft(path, reason):
    """Write `## Older-Draft: <reason>` directly above the `<!-- END SCRIPT HEADER -->` line of the text at `path` (an existing
    Older-Draft line is replaced), then read the header back through training_header.older_draft and refuse when it does not give
    the reason. The key is training_header.OLDER_DRAFT_KEY (o9:W199); every other byte of the file stays. Returns the reason."""
    reason = older_draft_reason(reason)
    p = Path(path)
    data = p.read_bytes()
    marker = END_MARK.encode("utf-8")
    at, nl = -1, b"\n"
    lines = data.split(b"\n")
    for i, ln in enumerate(lines):
        if ln.strip() == marker:
            at = i
            nl = b"\r\n" if ln.endswith(b"\r") else b"\n"
            break
    if at < 0:
        raise LibraryError("%s: no %s line, so the older-draft line has no place to go; nothing was written" % (p, END_MARK))
    line = ("## %s: %s" % (training_header.OLDER_DRAFT_KEY, reason)).encode("utf-8")
    old_re = re.compile(rb"^##[ \t]+" + re.escape(training_header.OLDER_DRAFT_KEY.encode()) + rb"[ \t]*:", re.I)
    head = [ln for ln in lines[:at] if not old_re.match(ln.lstrip(b"\xef\xbb\xbf"))]
    new = b"\n".join(head + [line + (b"\r" if nl == b"\r\n" else b"")] + lines[at:])
    p.write_bytes(new)
    head_text = new[:new.find(marker)].decode("utf-8", "replace")
    if training_header.older_draft(head_text) != reason:
        p.write_bytes(data)
        raise LibraryError("%s: the older-draft line was written but training_header.older_draft does not read the reason back; "
                           "the file was put back as it was" % p)
    return reason


def cmd_place(lib, path, folder=None, name=None, use_git=True, rewrite_of=None, new_label_anyway=False, older_draft=None,
              dry_run=False):
    """Move an adopted file into the library under a record-file name, and give it the three step-6 lines (`Version-Of`,
    `Version`, `Author`) that its name says (W235 step 7; o10L348 found that `place` made files without them). Returns the
    new path. The name is built from the header unless `name` is given; it must be `<NAME> v<N> <AUTHOR>[ (in app)].md`.

    W243: a text that is a rewrite (`rewrite_of`, or a `## Rewrite-Of: <LABEL>` header line) is added to that script's folder
    as its next version instead (route_as_next_version; the app text does not change). A text that would make a NEW script
    folder is refused when a script in the same category has the same title or a slug this one extends, unless
    `new_label_anyway`.

    W245: `older_draft` (a reason) writes `## Older-Draft: <reason>` into the placed text (stamp_older_draft). A FOLDER given with
    `rewrite_of` is moved whole, its versions/ files and logs included (route_folder_as_versions); `dry_run` (a folder only)
    writes nothing and returns the list of [(old, new)] it would move."""
    if older_draft is not None:
        older_draft_reason(older_draft)               # refused before anything is moved
    try:
        header, _ = parse_script(path)
    except (LibraryError, OSError, UnicodeError):
        header = None
    ref = (rewrite_of or "").strip() or rewrite_of_value(header)
    if dry_run and not (ref and Path(path).is_dir()):
        raise LibraryError("--dry-run is for moving a rewrite folder (place <folder> --rewrite-of <LABEL> --dry-run); nothing was changed")
    if ref and Path(path).is_dir():
        if name or folder:
            raise LibraryError("--name and --folder do not apply to a rewrite folder: its files take the names of the script %s. "
                               "Nothing was moved." % ref)
        return route_folder_as_versions(lib, path, ref, use_git=use_git, older_draft=older_draft, dry_run=dry_run)
    if ref:
        src = Path(path)
        in_lib = Path(os.path.abspath(str(src))).is_relative_to(Path(os.path.abspath(str(lib.dir))))
        dest = route_as_next_version(lib, src, ref, name=name, use_git=use_git, folder=folder, older_draft=older_draft)
        if in_lib:                  # a text that was already in the library: its registries follow it, as `file` does (o10L438 F5)
            update_path_registries(lib, [(src, dest)])
            remove_emptied_folders(lib, src.parent)
        return dest
    claimed, filled = None, {}
    if header is not None and header.get("Label", "").strip():
        # o10L475: a reserved label is kept, an invented or released one is refused. o10L477 D2: a Type, Line, Tab or Category the
        # header lacks is filled in from the reservation here, before the folder and the name are worked out, and written below.
        had = dict(header)
        claimed = claim_label(lib, header, path)
        filled = {k: header[k] for k in RESERVATION_FIELDS if header.get(k) and not had.get(k)}
    if name is None:
        if header is None or "Stage" not in header:
            raise LibraryError("%s has no library header with a Stage; give --name (<NAME> v<N> <AUTHOR>.md)" % path)
        name = placed_name(header)
    pn = training_header.parse_script_name(name)
    if pn.version is None or pn.kind not in RECORD_KINDS:
        raise LibraryError("%r is not a record-file name (<NAME> v<N> <AUTHOR>[ (in app)].md); nothing was moved" % name)
    if folder:
        rel = folder
    else:
        if header is None:
            raise LibraryError("%s has no library header; give --folder" % path)
        rel = expected_rel_dir(header)
    if header is not None and header.get("Stage") and not new_label_anyway and not (lib.dir / rel).exists():
        matches = lookalike_scripts(lib, header)
        if matches:
            raise LibraryError(rewrite_refusal(lib, matches, "place", path))
    dest = place_file(lib, path, rel, name, use_git=use_git)
    data = dest.read_bytes()
    new = stamp_version_lines(data, record_header_values(pn.name_key, pn.version, pn.author))
    if filled:                                                       # o10L477 D2: the lines the reservation supplied
        new = stamp_version_lines(new, filled, keys=RESERVATION_FIELDS)
    if new != data:
        dest.write_bytes(new)
    if older_draft is not None:
        stamp_older_draft(dest, older_draft)
    # o10L479: marking the reservation used is the LAST write. A lock timeout here leaves the script complete and only the
    # reservation unmarked, which a later `file` run repairs.
    if claimed and claimed[1]:
        mark_reservation_used_last(lib, claimed[0], header, dest.parent)
    return dest


# --------------------------------------------------------------------------- a rewrite is a version, not a new script (W243)
#
# the human, 2026-10-11: "I had o3 rewrite a script. This was a rewrite of a script with a folder already present - AT-Sl-N-02-002.
# But the agent create a NEW folder AT-Sl-N-02-021. This needs to never happen or it will create chaos." Three parts:
#   1. `file` (when it gives a script its first label) and `place` (when it makes a new script folder) refuse when a script in
#      the same category already has the same title, or a slug that this one extends, and print the command that adds the text
#      as that script's next version. `--new-label-anyway` is the one way past the refusal.
#   2. A text with `## Rewrite-Of: <LABEL>` in its header (or `--rewrite-of <LABEL>` on the command) is not filed as a new
#      script: `file` and `place` add it to that script's folder as its next version.
#   3. A text added that way is a plain text beside the record (the `adopt --version-of` shape): it has no `## Stage:`, so it is
#      not a record file, and the `(in app)` record and the app's import files do not change.

NEW_LABEL_ANYWAY_HELP = ("make the new script even though a script in the same category has the same title or a slug this one "
                         "extends (a refusal names that script and the command that adds this text to it as its next version)")
OLDER_DRAFT_HELP = ("write the header line `## Older-Draft: REASON` into the text this command adds (a rewrite given with --rewrite-of, or "
                    "a placed file): the human has set it aside as an older draft")
FOLDER_DRY_RUN_HELP = ("only with --rewrite-of and a rewrite FOLDER (a folder given to `place`, or a text whose folder holds more): print "
                       "what would be moved where, and write nothing")
REWRITE_OF_HELP = ("this text is a rewrite of the script with this label: add it to that script's folder as its next version "
                   "(the same as a `## Rewrite-Of: <LABEL>` header line); the text the app plays does not change")
# header fields that make a file a script's record file or say where the script is; a rewrite added as a version keeps none of them
# (each is renamed `## Original-<Key>:`, as `adopt` does, so no line is lost)
RECORD_ONLY_KEYS = ("Label", "Stage", "Release", "ID", "Slug", "Working-Slug", "Aliases", "Source", "Source-position", "App",
                    "Line", "Tab", "Category", "Approved-By", "Approved-Ruling")
_REWRITE_VERSION_FORM = re.compile(r"^v?\d+$", re.I)


def rewrite_of_value(header):
    """The label (or `<TYPE>-<ID>`) a header's `## Rewrite-Of:` line names, else "". The Reader's own `## Rewrite-Of: v<K>`
    (the version of a draft a the human rewrite pairs with, reader_scripts.REWRITE_OF_RE) is not a label and is ignored here."""
    v = ((header or {}).get("Rewrite-Of") or "").strip().strip("`'\"")
    return "" if not v or _REWRITE_VERSION_FORM.match(v) else v


_LEADING_ARTICLE = re.compile(r"^(?:the|a|an) ")


def norm_title_words(s):
    """A title as lower-case ASCII words: accents folded, `&` read as `and`, punctuation and whitespace runs as one space, a
    leading `The`, `A` or `An` dropped (o10L438 F3). No other word is dropped or guessed at."""
    t = fold_accents(str(s or "")).lower().replace("&", " and ")
    t = re.sub(r"['’]", "", t)
    t = re.sub(r"[^a-z0-9]+", " ", t).strip()
    return _LEADING_ARTICLE.sub("", t)


def norm_title_key(s):
    """A title with case, punctuation, accents, whitespace and a leading article ignored and `&` read as `and`, to compare two
    full titles (never a slug cut to a folder-name length)."""
    return norm_title_words(s).replace(" ", "")


def norm_slug(s):
    """A slug (or a title) in the same normal form as norm_title_words, hyphenated: `the-sleep-switch` -> `sleep-switch`."""
    return norm_title_words(str(s or "").replace("-", " ")).replace(" ", "-")


def guard_slugs(header):
    """The full slugs a script answers to, for the lookalike guard: its app Slug (not cut to the 40 characters of a folder
    name), its Working-Slug and its title's slug, each in norm_slug form."""
    s = header.get("Slug", "").strip()
    out = {s if s.lower() != "none" else "", header.get("Working-Slug", "").strip(), header.get("Title", "")}
    return {n for n in map(norm_slug, out) if n}


def script_ref_names(header):
    """Every name a script answers to: its Label, its aliases and `<TYPE>-<ID>`."""
    names = [header.get("Label", "").strip()] + aliases_of(header)
    if header.get("Type") and header.get("ID"):
        names.append("%s-%s" % (header["Type"].strip(), header["ID"].strip()))
    return [n for n in names if n]


def rel_posix(lib, path):
    return Path(path).relative_to(lib.dir).as_posix()


def find_script(lib, ref):
    """(record path, header) of the one script that answers to `ref` (a Label, an alias or `<TYPE>-<ID>`). Refuses when no
    script or more than one does."""
    found = []
    for p in lib.iter_scripts():
        try:
            h, _ = parse_script(p)
        except (LibraryError, OSError, UnicodeError):
            continue
        if ref in script_ref_names(h):
            found.append((p, h))
    if not found:
        raise LibraryError("no script in the library has the label %s (looked in labels, aliases and TYPE-ID); nothing was changed" % ref)
    if len(found) > 1:
        raise LibraryError("%d scripts answer to %s (%s); nothing was changed" % (
            len(found), ref, ", ".join(rel_posix(lib, q.parent) for q, _ in found)))
    return found[0]


def folder_trailing_slug(folder, header):
    """The slug a script folder's name ends in (`AT-Sl-N-02-002-stop-counting-the-hours` -> `stop-counting-the-hours`)."""
    base = Path(folder).name
    for pre in (header.get("Label", "").strip(), "%s-%s" % (header.get("Type", "").strip(), header.get("ID", "").strip())):
        if pre and base.startswith(pre + "-"):
            return base[len(pre) + 1:]
    return ""


def lookalike_scripts(lib, header, own_folder=None):
    """[(record path, header, why)] of the scripts that a new script with this header would duplicate: not retired, not the
    script itself (same folder or same ID), of the same Type (an AT and an SC of the same title are two scripts: they work by
    different mechanisms, o10L438 F2), in the same Line, Tab and Category when the header has them (any category when it has
    not), and either with the same full title (norm_title_key: case, punctuation, whitespace, a leading The/A/An ignored, `&`
    read as `and`) or with a full slug that equals this one's or that this one's slug extends with a suffix
    (`stop-counting-the-hours-sonnet-5-5` extends `stop-counting-the-hours`). The 40-character slug of a folder name is used
    only for a record that has no title and no slug of its own (o10L438 F3)."""
    title_n = norm_title_key(header.get("Title"))
    mine = guard_slugs(header)
    own = Path(own_folder).resolve() if own_folder else None
    own_id = header.get("ID", "").strip()
    own_type = header.get("Type", "").strip()
    scoped = has_placement(header)
    out = []
    for p in lib.iter_scripts():
        if own is not None and p.parent.resolve() == own:
            continue
        try:
            h, _ = parse_script(p)
        except (LibraryError, OSError, UnicodeError):
            continue
        if h.get("Stage") == "retired" or (own_id and h.get("ID", "").strip() == own_id):
            continue
        if own_type and h.get("Type", "").strip() and h.get("Type", "").strip() != own_type:
            continue
        if scoped and any(h.get(k, "").strip() != header.get(k, "").strip() for k in ("Line", "Tab", "Category")):
            continue
        their_title = norm_title_key(h.get("Title")) or norm_title_key(folder_slug_title(p.parent, h, h.get("Label", "")))
        if title_n and their_title == title_n:
            out.append((p, h, "the same title"))
            continue
        theirs = guard_slugs(h) or {s for s in (norm_slug(folder_trailing_slug(p.parent, h)),) if s}
        hit = next(((m, t) for t in sorted(theirs) for m in sorted(mine) if m == t or m.startswith(t + "-")), None)
        if hit:
            out.append((p, h, "a slug that %s its slug %s" % ("is" if hit[0] == hit[1] else "extends", hit[1])))
    return out


def rewrite_refusal(lib, matches, cmd, shown_path):
    """The refusal for a new script that looks like an existing one: names the folder of each script it duplicates, and
    prints the one command that adds the text as the next version of the first (the app text unchanged)."""
    lines = ["this would make a new script, but a script in the same category already looks like it:"]
    for p, h, why in matches[:3]:
        lines.append("  %s \"%s\" has %s; folder %s" % (
            h.get("Label") or "%s-%s" % (h.get("Type"), h.get("ID")), h.get("Title", ""), why, rel_posix(lib, p.parent)))
    p0, h0, _ = matches[0]
    ref = h0.get("Label", "").strip() or "%s-%s" % (h0.get("Type"), h0.get("ID"))
    nxt = next_version_number(p0.parent)
    lines.append("A rewrite or another take on a script is added to that script as its next version, not given a new label. "
                 "To add this text as v%d of %s without changing the text the app plays, run:" % (nxt, ref))
    lines.append('  python .shared/scripts/file_script.py %s "%s" --rewrite-of %s' % (cmd, shown_path, ref))
    lines.append("(Or put the line `## Rewrite-Of: %s` in the text's header and run the same command without --rewrite-of.) "
                 "If it really is a different script, run the same command again with --new-label-anyway. Nothing was changed." % ref)
    return "\n".join(lines)


def rewrite_source_refusal(src, header, ref):
    """The refusal for a text that is a script of its own, given as a rewrite to add as a version of `ref` (o10L438 F1), else
    "". A text is a script of its own when its file name says `(in app)`, its header says `## App: live`, it has a `## Source:`
    or `## Source-position:` line (the app plays it from an import file), it says `## Stage: final`, or it has a `## Label:`.
    Moving such a text into another script's folder would take a script out of the app or lose its label."""
    header = header or {}
    why = []
    pn = training_header.parse_script_name(Path(src).name)
    if pn.version is not None and pn.in_app:
        why.append("its file name says `(in app)`")
    if header.get("App", "").strip():
        why.append("`## App: %s`" % header["App"].strip().split()[0])
    for k in ("Source", "Source-position"):
        if header.get(k, "").strip():
            why.append("a `## %s:` line" % k)
    if header.get("Stage", "").strip() == "final":
        why.append("`## Stage: final`")
    if header.get("Label", "").strip():
        why.append("the label %s" % header["Label"].strip())
    if not why:
        return ""
    return ("%s is not added as a version of %s: it is a script of its own (%s). Moving it into another script's folder would "
            "take a script out of the app or lose its label. To replace the text the app plays, use `revise`; to take a script "
            "out, use `file --stage retired`. If this text is a copy of a record that was rewritten, delete those lines from its "
            "header and run the command again. Nothing was changed." % (src, ref, ", ".join(why)))


def remove_emptied_folders(lib, folder):
    """Remove `folder` and then each parent below the library root while it is empty."""
    folder = Path(folder)
    lib_dir = Path(lib.dir).resolve()
    while folder.resolve() != lib_dir and folder.is_dir() and not any(folder.iterdir()):
        folder.rmdir()
        folder = folder.parent


def route_as_next_version(lib, src, ref, name=None, use_git=True, folder=None, older_draft=None):
    """Add the text at `src` to the folder of the script `ref` names, as that script's next version: the record file, the
    `(in app)` text and the import files do not change. The text becomes a plain text beside the record (what `adopt
    --version-of <LABEL>` then `place --folder <folder> --name <name>` make): its record-only header lines are renamed
    `## Original-<Key>:`, it gets `## Type:`, `## Version-Of:`, `## Version:` and `## Author:`, and it has no `## Stage:`, so it is
    not a second record file. Its name is `<NAME> <Title-Words> v<N> <AUTHOR>.md`, N one above the highest version in the folder,
    the author from its Authored line (else its own Author line, else agent), unless `name` is given. Returns the new path."""
    src = Path(src)
    rec_p, th = find_script(lib, ref)
    if folder and (lib.dir / folder).resolve() != rec_p.parent.resolve():
        raise LibraryError("the text is a rewrite of %s, whose folder is %s, not %s; nothing was moved" % (
            ref, rel_posix(lib, rec_p.parent), folder))
    if th.get("Stage") == "retired":
        raise LibraryError("%s is retired; a text is not added to a retired script's folder. Nothing was moved." % ref)
    if src.resolve().parent == rec_p.parent.resolve():
        raise LibraryError("%s is already in the folder of %s; nothing was moved" % (src, ref))
    text = to_lf(src.read_bytes()).decode("utf-8")
    head, had_marker, body = split_head(text)
    matched = [m for m in map(KEY_RE.match, head) if m]
    refusal = rewrite_source_refusal(src, {m.group(1): m.group(2).strip() for m in matched}, ref)
    if refusal:
        raise LibraryError(refusal)
    own_type = next((m.group(2).strip() for m in matched if m.group(1) == "Type"), "")
    typ = th.get("Type", "").strip()
    if own_type and own_type != typ:
        raise LibraryError("the text says `## Type: %s` but %s is %s; change the type first (`file --type`). Nothing was moved." % (
            own_type, ref, typ))
    if any(m.group(1) == "Approved-By" for m in matched):
        raise LibraryError("the text carries `## Approved-By:`; it is a script the human approved, not a rewrite to add as a version. "
                           "Nothing was moved.")
    demoted = []
    for ln in head:
        m = KEY_RE.match(ln)
        demoted.append(_rename_key(ln, m.group(1)) if m and m.group(1) in RECORD_ONLY_KEYS else ln)
    text2 = "\n".join(demoted + ([END_MARK] if had_marker else []) + body)
    pn_t = record_name(rec_p)
    key = name_key_of(th, pn_t)
    family = family_of(th, pn_t)
    if name is None:
        author = revision_author(text)
        if author == "agent":
            m = re.search(r"(?m)^##\s+Author:[ \t]*(\S+)", "\n".join(head))
            author = m.group(1) if m else "agent"
        name = version_name(key, next_version_number(rec_p.parent), author, title=carried_title(th, rec_p, key))
    pn = training_header.parse_script_name(name)
    if pn.version is None or pn.kind not in RECORD_KINDS or pn.in_app:
        raise LibraryError("%r is not a name for a version beside the record (<NAME> v<N> <AUTHOR>.md, not `(in app)`); "
                           "nothing was moved" % name)
    new_text, _ = adopt_text(text2, adopt_fields(typ, "draft", version_of=family))
    dest = place_file(lib, src, rel_posix(lib, rec_p.parent), name, use_git=use_git)
    dest.write_bytes(stamp_version_lines(new_text.encode("utf-8"), record_header_values(key, pn.version, pn.author, family)))
    if older_draft is not None:
        stamp_older_draft(dest, older_draft)
    return dest


# --------------------------------------------------------------------------- a rewrite FOLDER is moved whole (o10 W245, addition B)
#
# A rewrite can come as a folder of its own: several texts (`v3 THE HUMAN`, `v4 o8`), older texts in `versions/` and the process logs
# that belong to them (SC-pil-SC08 and SC-pil-SC11 are two). `place <folder> --rewrite-of <LABEL>` and `file <text> --rewrite-of
# <LABEL>` (when the text's folder holds more than that text) move all of it into the folder of the script <LABEL> names. A text in
# the rewrite folder keeps its place (beside the record, or in versions/); its version number moves up to the numbers after the
# highest one in the target folder, in the order of the old numbers, and the logs take their text's new number. The two registries
# that name a text by path or file name follow (update_path_registries). Nothing outside the folder changes, and a refused or
# failed run puts everything back.

_LOG_SUFFIX = re.compile(r"(-PROCESS-LOG|-REVIEW)\.md\Z")


def version_form_bytes(text, typ, family, key, number, author):
    """The bytes of the LF text `text` as a plain version text of the script family `family` (what route_as_next_version writes):
    its record-only header lines renamed `## Original-<Key>:`, `## Type:` and `## Version-Of:` set, and `## Version-Of:`,
    `## Version:` and `## Author:` stamped from the new name."""
    head, had_marker, body = split_head(text)
    demoted = []
    for ln in head:
        m = KEY_RE.match(ln)
        demoted.append(_rename_key(ln, m.group(1)) if m and m.group(1) in RECORD_ONLY_KEYS else ln)
    text2 = "\n".join(demoted + ([END_MARK] if had_marker else []) + body)
    new_text, _ = adopt_text(text2, adopt_fields(typ, "draft", version_of=family))
    return stamp_version_lines(new_text.encode("utf-8"), record_header_values(key, number, author, family))


def plan_folder_as_versions(lib, folder, ref):
    """The plan for moving the rewrite folder `folder` into the folder of the script `ref` names: (target record path, [dict with
    old, new, kind ('text', 'log' or 'note'), version (new number or None), author]), in the order the files are moved. Raises
    LibraryError, writing nothing, for anything that cannot be moved whole: a file that is not a script text, log or note, an
    `(in app)` text or a script of its own (rewrite_source_refusal), an approved text, a type that differs, a name that is taken."""
    folder = Path(os.path.abspath(str(folder)))       # o10L450: a relative folder gave paths rel_posix and the CLI could not place
    if not folder.is_dir():
        raise LibraryError("%s is not a folder" % folder)
    rec_p, th = find_script(lib, ref)
    tf = rec_p.parent
    if th.get("Stage") == "retired":
        raise LibraryError("%s is retired; a text is not added to a retired script's folder. Nothing was moved." % ref)
    fr, tr = folder.resolve(), tf.resolve()
    if fr == tr or tr in fr.parents or fr in tr.parents:
        raise LibraryError("%s and the folder of %s (%s) are the same folder or one holds the other; nothing was moved" % (
            rel_posix(lib, folder), ref, rel_posix(lib, tf)))
    typ = th.get("Type", "").strip()
    pn_t = record_name(rec_p)
    key, family = name_key_of(th, pn_t), family_of(th, pn_t)
    ctitle = carried_title(th, rec_p, key)
    entries, odd = [], []
    for d, sub in ((folder, ""), (folder / "versions", "versions")):
        try:
            names = sorted(os.listdir(str(d)))
        except OSError:
            continue
        for n in names:
            f = d / n
            shown = ("%s/%s" % (sub, n)).lstrip("/")
            if f.is_dir():
                if not (d == folder and n == "versions") and any(f.iterdir()):
                    odd.append(shown)
                continue
            if not n.endswith(".md"):
                odd.append(shown)
                continue
            pn = training_header.parse_script_name(n)
            if pn.kind == "note" and pn.version is None:
                entries.append({"old": f, "sub": sub, "kind": "note", "pn": pn})
            elif pn.version is not None and pn.kind == "log":
                entries.append({"old": f, "sub": sub, "kind": "log", "pn": pn})
            elif pn.version is not None and pn.kind in RECORD_KINDS:
                entries.append({"old": f, "sub": sub, "kind": "text", "pn": pn})
            else:
                odd.append(shown)
    if odd:
        raise LibraryError("%s also holds %s, which is not a script text, log or note named like a library file, so the folder is "
                           "not moved for you; move it by hand. Nothing was changed." % (
                               rel_posix(lib, folder), ", ".join(odd[:5]) + (" and %d more" % (len(odd) - 5) if len(odd) > 5 else "")))
    if not any(e["kind"] == "text" for e in entries):
        raise LibraryError("%s holds no script text to add as a version of %s; nothing was moved" % (rel_posix(lib, folder), ref))
    for e in entries:
        if e["kind"] != "text":
            continue
        text = to_lf(e["old"].read_bytes()).decode("utf-8")
        head, _, _ = split_head(text)
        fields = {m.group(1): m.group(2).strip() for m in map(KEY_RE.match, head) if m}
        refusal = rewrite_source_refusal(e["old"], fields, ref)
        if refusal:
            raise LibraryError(refusal)
        if fields.get("Type") in ("AT", "SC") and fields["Type"] != typ:
            raise LibraryError("%s says `## Type: %s` but %s is %s; change the type first (`file --type`). Nothing was moved." % (
                e["old"].name, fields["Type"], ref, typ))
        if "Approved-By" in fields:
            raise LibraryError("%s carries `## Approved-By:`; it is a script the human approved, not a rewrite to add as a version. "
                               "Nothing was moved." % e["old"].name)
    numbers = sorted({e["pn"].version for e in entries if e["pn"].version is not None})
    first = next_version_number(tf)
    newnum = {old: first + i for i, old in enumerate(numbers)}
    plan = []
    for e in entries:
        pn, n = e["pn"], e["old"].name
        dest_dir = tf / "versions" if e["sub"] else tf
        if e["kind"] == "note":
            new, ver = retitled_name(n, pn, key, ctitle), None
        else:
            ver = newnum[pn.version]
            m = _LOG_SUFFIX.search(n)
            new = training_header.script_file_name(key, ver, pn.author, title=ctitle, duplicate=" duplicate" in n,
                                                   suffix=(m.group(1) if m else ""))
        plan.append({"old": e["old"], "new": dest_dir / new, "kind": e["kind"], "version": ver, "author": pn.author})
    seen = set()
    for it in plan:
        low = os.path.normcase(str(it["new"]))
        if it["new"].exists() or low in seen:
            raise LibraryError("%s already exists (or two files of %s would take that name); nothing was moved" % (
                rel_posix(lib, it["new"]), rel_posix(lib, folder)))
        seen.add(low)
    return rec_p, plan


def route_folder_as_versions(lib, folder, ref, use_git=True, older_draft=None, dry_run=False):
    """Move the rewrite folder `folder` (its texts, `versions/` and the logs and notes in them) into the folder of the script `ref`
    names (plan_folder_as_versions). Returns the plan as [(old path, new path)]. dry_run writes nothing. Otherwise the texts are
    rewritten as plain version texts (version_form_bytes), `older_draft` (a reason) is written into each of them, the registries
    follow (update_path_registries), the HV6 reuse baseline drops the rows of a reuse id the move retires (carry_reuse_baseline),
    the emptied folders are removed, and `check` must report no problem it did not report before; a failure puts every file and
    the three registries back."""
    folder = Path(os.path.abspath(str(folder)))
    reason = older_draft_reason(older_draft) if older_draft is not None else None
    rec_p, plan = plan_folder_as_versions(lib, folder, ref)
    moves = [(it["old"], it["new"]) for it in plan]
    if dry_run:
        return moves
    reuse_old = reuse_ids_before(lib, plan)
    th = parse_script(rec_p)[0]
    typ = th.get("Type", "").strip()
    pn_t = record_name(rec_p)
    key, family = name_key_of(th, pn_t), family_of(th, pn_t)
    before, _ = cmd_check(lib, write=False)
    originals = {it["old"]: it["old"].read_bytes() for it in plan}
    reg_saved = save_registries(lib)
    done = []
    try:
        for it in plan:
            it["new"].parent.mkdir(parents=True, exist_ok=True)
            move_path(lib, it["old"], it["new"], use_git=use_git)
            done.append(it)
            if it["kind"] == "text":
                text = to_lf(originals[it["old"]]).decode("utf-8")
                it["new"].write_bytes(version_form_bytes(text, typ, family, key, it["version"], it["author"]))
                if reason:
                    stamp_older_draft(it["new"], reason)
        update_path_registries(lib, moves)
        carry_reuse_baseline(lib, folder, plan, reuse_old)
        errors, _ = cmd_check(lib, write=False)
        fresh = new_problems(before, errors, moves)
        if fresh:
            raise LibraryError("moving %s would leave the library with %d problem(s), so nothing was changed: %s" % (
                rel_posix(lib, folder), len(fresh), "; ".join(fresh[:5])))
    except BaseException:
        for it in reversed(done):
            try:
                if it["new"].exists():
                    it["new"].write_bytes(originals[it["old"]])
                it["old"].parent.mkdir(parents=True, exist_ok=True)
                move_path(lib, it["new"], it["old"], use_git=use_git)
            except (LibraryError, OSError) as e:
                print("WARNING: could not move %s back to %s: %s" % (it["new"], it["old"], e), file=sys.stderr)
        restore_registries(reg_saved)
        raise
    for dp, _dn, _fn in os.walk(str(folder), topdown=False):       # the folders the move emptied (an empty .edit-history too)
        if not os.listdir(dp):
            os.rmdir(dp)
    remove_emptied_folders(lib, folder.parent)
    return moves


# The HV6 reuse baseline (check_script_reuse_baseline.json, o3:W35) freezes the overlaps of the day the check landed as rows
# [script id, other script id, 10-gram], keyed by the reuse id (check_script.script_id), not by path. Its rules: rows are only
# removed, by whoever fixes an overlap, and re-added only with check_script.py --readd-reuse-rows <ID>. A folder move changes the
# reuse id of the texts it moves (SC-pil-SC08's `v3 THE HUMAN` goes from its folder name to the id of the script it joins), so
# (review o10L450, open point 1) the move does NOT rewrite rows to the new id: that would add rows another way, and could accept
# under a live script's id an overlap the freeze never accepted. Instead:
#   * when a row naming an old id would come back under the new id as a row the baseline lacks (the HV6 check would report a
#     new finding), the move is refused and undone, naming ids and counts only (a 10-gram is script text);
#   * the rows naming an old id that no text in the reuse corpus carries after the move are stale by construction and are
#     removed, as the baseline's own rule asks of the commit that ends an overlap (--drop-stale-reuse-rows does the same).
# Measured on SC-pil-SC08 (o10L450, a copy of the tree): its 4 rows go stale, and the move adds no unbaselined row.
REUSE_BASELINE = ".shared/scripts/check_script_reuse_baseline.json"


def _reuse_baseline_rows_naming(lib, ids):
    path = Path(os.path.abspath(str(lib.root))) / REUSE_BASELINE
    if not ids or not path.is_file():
        return path, None, []
    data = json.loads(path.read_bytes().decode("utf-8"))
    entries = [tuple(e) for e in data.get("entries", [])]
    return path, data, [e for e in entries if e[0] in ids or e[1] in ids]


def reuse_ids_before(lib, plan):
    """{old path: HV6 reuse id} of the texts in `plan` that a row of the reuse baseline names, read before the move ({} when no
    row names one, or the tree has no baseline: the move then leaves the baseline alone)."""
    texts = [it["old"] for it in plan if it["kind"] == "text"]
    if not texts or not (Path(os.path.abspath(str(lib.root))) / REUSE_BASELINE).is_file():
        return {}
    import check_script                       # imported here: check_script imports this module
    check_script._ID_CACHE.clear()
    ids = {p: check_script.script_id(str(p)) for p in texts}
    _path, _data, named = _reuse_baseline_rows_naming(lib, set(ids.values()))
    hit = {x for e in named for x in e[:2]}
    return {p: i for p, i in ids.items() if i in hit}


def carry_reuse_baseline(lib, folder, plan, old_ids):
    """After the files of `plan` moved: refuse when a reuse-baseline row naming an old id (old_ids, reuse_ids_before) would come
    back under the moved text's new id as a row the baseline lacks; else remove the rows of the old ids no corpus text carries
    any more. Returns the number of rows removed. The caller restores the file on a failure (save_registries holds it)."""
    if not old_ids:
        return 0
    import check_script
    path, data, named = _reuse_baseline_rows_naming(lib, set(old_ids.values()))
    if not named:
        return 0
    root = os.path.abspath(str(lib.root))
    check_script._ID_CACHE.clear()
    corpus = [p for p in check_script.default_reuse_corpus(root) if not check_script.hv6_excluded(p)]
    in_corpus = {os.path.normcase(os.path.abspath(p)) for p in corpus}
    new_of = {it["old"]: it["new"] for it in plan}
    entries = set(tuple(e) for e in data.get("entries", []))
    findings = set()
    for old, oid in old_ids.items():
        new = new_of[old]
        if os.path.normcase(os.path.abspath(str(new))) not in in_corpus:
            continue                          # the text left the reuse corpus: its rows go stale, nothing comes back
        nid = check_script.script_id(str(new))
        grams = set(check_script._grams(check_script.script_reuse_tokens(check_script.read_script(str(new))[0], stops=True)))
        for a, b, g in named:
            if g not in grams or oid not in (a, b):
                continue
            row = (nid if a == oid else a, nid if b == oid else b, g)
            if row[0] != row[1] and row not in entries:
                findings.add((oid, nid, row[0], row[1]))
    if findings:
        shown = sorted({"%s becomes %s (%s with %s)" % f for f in findings})
        raise LibraryError("moving %s would change the HV6 reuse id of a text that %s names, and %d overlap(s) the baseline accepts "
                           "under the old id would come back as new findings: %s. The baseline gains rows only through "
                           "check_script.py --readd-reuse-rows, so nothing was moved." % (
                               rel_posix(lib, folder), REUSE_BASELINE, len(findings), "; ".join(shown[:5])))
    remaining = {check_script.script_id(p) for p in corpus}
    retired = set(old_ids.values()) - remaining
    keep = [list(e) for e in data.get("entries", []) if e[0] not in retired and e[1] not in retired]
    gone = len(data.get("entries", [])) - len(keep)
    if gone:
        data["entries"] = keep
        check_script._write_baseline_data(root, data)
    return gone


# --------------------------------------------------------------------------- label allocation

def scan_numbers(lib, line_code, tab, cc, exclude=None):
    """Highest NNN used (as a label or an alias) in this line/tab/category, any type, any stage. `exclude` is the path of
    one record file to leave out (the script being filed, when it is already in the library)."""
    best = 0
    skip = Path(os.path.abspath(str(exclude))) if exclude else None
    for p in lib.iter_scripts():
        if skip is not None and Path(os.path.abspath(str(p))) == skip:
            continue
        try:
            h, _ = parse_script(p)
        except LibraryError:
            continue
        for lab in [h.get("Label", "")] + aliases_of(h):
            m = LABEL_RE.match(lab)
            if m and m.group(2) == line_code and m.group(3) == TABS[tab] and m.group(4) == cc:
                best = max(best, int(m.group(5)))
    return best


def next_number(lib, line, tab, cc, line_code):
    """The next NNN to give out: the larger of the stored high-water mark and the highest number
    found in any header or alias, plus one. Returns (key, n). Nothing is written here; the caller
    calls commit_number once everything else has been checked, so a refused command spends no number."""
    counters = lib.counters()
    key = "%s/%s/%s" % (line, tab, cc)
    n = max(counters.get(key, 0), scan_numbers(lib, line_code, tab, cc)) + 1
    return key, n


def commit_number(lib, key, n):
    counters = lib.counters()
    counters[key] = max(counters.get(key, 0), n)
    save_json(lib.dir / "LABEL-COUNTERS.json", counters)


# --------------------------------------------------------------------------- reserving a label before the script exists (o10L475)
#
# the human wants a script to carry its real library id from the moment it is proposed, not a stand-in like "SC-1". `reserve`
# takes the next number of a category (next_number + commit_number, so nothing else can take it) and records it in
# RESERVED-LABELS.json with a working title. The script's header then says `## Label: <that label>`, and adopt / place / file
# keep it. A reservation is OPEN until a script carries its label; the first command that uses it marks it USED (it is kept,
# not removed, so a second script that tries to use the same label can be told apart from the first and refused).

RESERVED_NAME = "RESERVED-LABELS.json"


def load_reservations(lib):
    """The whole RESERVED-LABELS.json document: {"reservations": {LABEL: {...}}, "released": [...]}."""
    doc = load_json(lib.dir / RESERVED_NAME, {})
    doc.setdefault("reservations", {})
    doc.setdefault("released", [])
    return doc


def save_reservations(lib, doc):
    save_json(lib.dir / RESERVED_NAME, doc)


# o10L477 D3: two sessions reserving at once must not get the same number. Everything that reads and then writes the counter or
# RESERVED-LABELS.json (reserve, unreserve, marking a reservation used) holds this lock file while it does. The file is created
# with O_CREAT|O_EXCL (only one process can create it), waited for up to RESERVE_LOCK_WAIT seconds, and removed when the
# command ends, even by an error. A lock older than RESERVE_LOCK_STALE seconds belongs to a process that died, and is taken over.
RESERVE_LOCK_NAME = ".reserve.lock"
RESERVE_LOCK_WAIT = 10.0
RESERVE_LOCK_STALE = 60.0
RESERVE_LOCK_POLL = 0.05


class ReserveLockBusy(LibraryError):
    """The reservation lock was still held by another command after RESERVE_LOCK_WAIT seconds."""


@contextlib.contextmanager
def reservations_lock(lib):
    """Hold the reservation lock for the length of the `with` block. Raises LibraryError, with nothing written, when the lock
    is still held by another command after RESERVE_LOCK_WAIT seconds."""
    path = lib.dir / RESERVE_LOCK_NAME
    deadline = time.monotonic() + RESERVE_LOCK_WAIT
    fd = None
    while fd is None:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = time.time() - path.stat().st_mtime
            except FileNotFoundError:
                continue                                  # released between our two calls: try again at once
            if age > RESERVE_LOCK_STALE:
                try:
                    path.unlink()                         # a dead command's lock
                except FileNotFoundError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise ReserveLockBusy("another reserve, unreserve or label command is changing %s (lock file %s is held); waited "
                                   "%g seconds. Nothing was written. Try again in a moment." % (RESERVED_NAME, RESERVE_LOCK_NAME, RESERVE_LOCK_WAIT))
            time.sleep(RESERVE_LOCK_POLL)
    try:
        os.write(fd, ("pid %d\n" % os.getpid()).encode("ascii"))
        os.close(fd)
        fd = None
        yield
    finally:
        if fd is not None:
            os.close(fd)
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def header_identity(header):
    """What names one script across its commands: its ID, else its Title (a reservation records who used it)."""
    return (header.get("ID", "") or "").strip() or (header.get("Title", "") or "").strip()


def line_slug_of_code(lib, code):
    for ln in lib.lines()["lines"]:
        if ln["code"] == code:
            return ln["slug"]
    return None


def label_holders(lib, label, path=None):
    """Record files in the library, other than `path`, that carry `label` as their Label or an alias."""
    skip = Path(os.path.abspath(str(path))) if path else None
    out = []
    for p in lib.iter_scripts():
        if skip is not None and Path(os.path.abspath(str(p))) == skip:
            continue
        try:
            h, _ = parse_script(p)
        except LibraryError:
            continue
        if h.get("Label", "").strip() == label or label in aliases_of(h):
            out.append(p)
    return out


def resolve_category(lib, line, tab, category):
    """The folder name (`04-sleep-hygiene`) of a category given as `04` or as the full folder name; LibraryError when the
    line/tab has no such category in CATEGORIES.json."""
    cat = (category or "").strip()
    if not cat:
        raise LibraryError("a category is needed (--category NN or NN-name)")
    rows = [c for c in lib.categories()["categories"] if c["line"] == line and c["tab"] == tab]
    exact = [c for c in rows if c["folder"] == cat]
    if exact:
        return exact[0]["folder"]
    by_number = [c for c in rows if c["folder"][:2] == cat and len(cat) == 2]
    if len(by_number) == 1:
        return by_number[0]["folder"]
    raise LibraryError("category %r is not in CATEGORIES.json for %s/%s" % (category, line, tab))


def cmd_reserve(lib, type_, line, tab, category, title, by=None, dry_run=False, today=None):
    """Allocate the next label of a category for a working title and record it in RESERVED-LABELS.json. Validates as
    ensure_label does (the line has a code, the tab is night or day, the type is AT or SC, the category exists). Returns the
    reservation as a dict (with "dry_run"); with dry_run nothing is written and no number is spent."""
    if type_ not in ("AT", "SC"):
        raise LibraryError("type must be AT or SC, got %r" % type_)
    if tab not in TABS:
        raise LibraryError("tab must be night or day, got %r" % tab)
    code = lib.line_code(line)
    if not code:
        raise LibraryError("product line %r has no code in LINES.json; run add-line first" % line)
    folder = resolve_category(lib, line, tab, category)
    title = " ".join((title or "").split())
    if not title:
        raise LibraryError("a reservation needs a working title (--title)")
    today = today or datetime.date.today().isoformat()

    def take():
        key, n = next_number(lib, line, tab, folder[:2], code)
        label = make_label(type_, code, tab, folder[:2], n)
        res = {"label": label, "type": type_, "line": line, "tab": tab, "category": folder, "title": title,
               "by": (by or "").strip(), "reserved": today, "dry_run": bool(dry_run)}
        if dry_run:
            return res
        doc = load_reservations(lib)
        if label in doc["reservations"] or label in released_labels(doc):
            raise LibraryError("%s is already in %s; nothing was reserved" % (label, RESERVED_NAME))
        commit_number(lib, key, n)
        doc["reservations"][label] = {"title": title, "by": res["by"], "reserved": today, "type": type_, "line": line,
                                      "tab": tab, "category": folder, "used": None, "used_by": None}
        save_reservations(lib, doc)
        return res

    if dry_run:
        return take()
    with reservations_lock(lib):        # o10L477 D3: the counter is read, spent and the reservation saved by one command at a time
        return take()


def released_labels(doc):
    """The labels `unreserve` gave up (RESERVED-LABELS.json "released"). Such a number is never used again."""
    return {(r.get("label", "") if isinstance(r, dict) else str(r)).strip() for r in doc.get("released", [])}


RESERVATION_FIELDS = ("Type", "Line", "Tab", "Category")


def fill_from_reservation(lib, header):
    """o10L477 D2: when `header` carries the Label of an OPEN reservation (not used, not released), a Type, Line, Tab or Category
    the header lacks is filled in, in place, from the reservation record, as if the writer had passed it. A field the header
    has is not touched (claim_label refuses it if it differs). Returns the dict of the fields filled in ({} when none)."""
    label = (header.get("Label", "") or "").strip()
    if not label:
        return {}
    doc = load_reservations(lib)
    res = doc["reservations"].get(label)
    if res is None or res.get("used") or label in released_labels(doc):
        return {}
    filled = {}
    for key, val in (("Type", res.get("type")), ("Line", res.get("line")), ("Tab", res.get("tab")), ("Category", res.get("category"))):
        if val and not (header.get(key, "") or "").strip():
            header[key] = val
            filled[key] = val
    return filled


def claim_label(lib, header, path=None):
    """Decide whether the Label a header already carries may be kept. `path` is the file the header belongs to (left out when
    other scripts are searched). Writes nothing. Returns None when the label is not a reserved one (the caller goes on as it
    always did), else (label, is_open): is_open is True when the reservation has not been used yet, so the caller marks it used
    (mark_reservation_used) once its own write has succeeded. Raises LibraryError, and the caller changes nothing, when
      * the label is not reserved and its number is above the counter of its category (a number made up by hand: only
        `reserve` hands out a number before a script is filed);
      * the label is reserved and another script already carries it, or another script used the reservation;
      * the reservation is still open and the header's type, line, tab or category, where the header has one, differs from
        the reservation (o10L477 D2: a field the header lacks is filled in, in place, from the reservation by
        fill_from_reservation, so `header` may gain Type, Line, Tab and Category here);
      * the label was released by `unreserve` (o10L477 D1): a released number is never used again."""
    label = (header.get("Label", "") or "").strip()
    m = LABEL_RE.match(label)
    if not m:
        return None
    typ, code, tl, cc, num = m.group(1), m.group(2), m.group(3), m.group(4), int(m.group(5))
    tab = "night" if tl == "N" else "day"
    doc = load_reservations(lib)
    if label in released_labels(doc):
        raise LibraryError("%s was released by `unreserve`; a released number is never used again. Reserve a new id with "
                           "`file_script.py reserve --type %s --line <line> --tab %s --category %s --title \"...\"` and use that "
                           "label. Nothing was changed." % (label, typ, tab, cc))
    res = doc["reservations"].get(label)
    if res is None:
        line = line_slug_of_code(lib, code)
        stored = lib.counters().get("%s/%s/%s" % (line, tab, cc), 0)
        if line is None or num <= stored:
            return None                      # an unknown line, or a number the counter has given out: the old rules judge it
        holders = label_holders(lib, label, path)
        if holders:
            return None                      # a label some script already has: the old rules (check) judge the clash
        high = max(stored, scan_numbers(lib, code, tab, cc, exclude=path))
        if num > high:
            raise LibraryError(
                "%s is not a label this library has given out: its number is above the highest in its category (%03d), and it "
                "is not reserved. Reserve one with `file_script.py reserve --type %s --line %s --tab %s --category %s --title "
                "\"...\"` and use that label. Nothing was changed." % (label, high, typ, line, tab, cc))
        return None
    holders = label_holders(lib, label, path)
    if holders:
        raise LibraryError("%s is reserved and %s already carries it; a reservation belongs to one script. Nothing was changed."
                           % (label, rel_posix(lib, holders[0])))
    ident = header_identity(header)
    if res.get("used"):
        if not ident:
            # o10L477 D4: with no ID or Title this script cannot be told from the one that used the reservation
            raise LibraryError("%s was reserved for %r and has been used since %s; this header has no ID and no Title, so it cannot "
                               "be shown to be the same script. Add the script's ID or Title. Nothing was changed."
                               % (label, res.get("title", ""), res.get("used")))
        if res.get("used_by") and res["used_by"] != ident:
            raise LibraryError("%s was reserved for %r and %r has used it since %s; a second script (%r) cannot use it. "
                               "Nothing was changed." % (label, res.get("title", ""), res["used_by"], res.get("used"), ident))
        return label, False
    if not ident:
        raise LibraryError("%s is reserved for %r but this header has no ID and no Title, so the reservation could not record "
                           "which script used it. Add the script's ID or Title. Nothing was changed." % (label, res.get("title", "")))
    fill_from_reservation(lib, header)                # o10L477 D2: what the header lacks comes from the reservation
    line, htab, hcat = (header.get("Line", "") or "").strip(), (header.get("Tab", "") or "").strip(), (header.get("Category", "") or "").strip()
    htype = (header.get("Type", "") or "").strip()
    if not (htype and line and htab and hcat):
        raise LibraryError("%s is reserved but its record in %s lacks a type, line, tab or category, and the header does not say "
                           "it. Add them to the header. Nothing was changed." % (label, RESERVED_NAME))
    if (htype, lib.line_code(line), TABS.get(htab), hcat[:2]) != (typ, code, tl, cc):
        raise LibraryError("%s is reserved for a %s script in %s/%s/%s but the header says %s %s/%s/%s; the label would not match. "
                           "Fix the header, or `unreserve %s` and reserve again. Nothing was changed."
                           % (label, typ, res.get("line"), res.get("tab"), res.get("category"), htype, line, htab, hcat, label))
    return label, True


def mark_reservation_used(lib, label, header, today=None):
    """Mark a reservation used by this script (its ID, else its Title). A reservation already used keeps its first mark."""
    with reservations_lock(lib):
        doc = load_reservations(lib)
        res = doc["reservations"].get(label)
        if res is None or res.get("used"):
            return False
        res["used"] = today or datetime.date.today().isoformat()
        res["used_by"] = header_identity(header)
        save_reservations(lib, doc)
        return True


def mark_reservation_used_last(lib, label, header, folder):
    """mark_reservation_used as the last write of `place` and `file` (o10L479). By then the script's files are written and
    moved, so a busy lock must not say "Nothing was written": the message says the script was filed, and that running `file`
    on its folder marks the reservation (o10L481). Still a LibraryError, so the command exits non-zero."""
    try:
        return mark_reservation_used(lib, label, header)
    except ReserveLockBusy:
        raise LibraryError("the script was filed in %s. Only its reservation %s was not marked used in %s, because another "
                           "reserve, unreserve or label command held the lock file %s for %g seconds. Its files are already "
                           "written, so do not run place again. To mark the reservation used, run: "
                           "python .shared/scripts/file_script.py file \"%s\""
                           % (folder, label, RESERVED_NAME, RESERVE_LOCK_NAME, RESERVE_LOCK_WAIT, folder))


def cmd_unreserve(lib, label, today=None):
    """Drop a reservation no script carries. The number stays spent: LABEL-COUNTERS.json is not touched, so the number is
    never given out again. Refused (nothing changed) for a label that is not reserved, was used, or is carried by a script."""
    label = (label or "").strip()
    with reservations_lock(lib):                       # o10L477 D3
        doc = load_reservations(lib)
        res = doc["reservations"].get(label)
        if res is None:
            raise LibraryError("%s is not in %s; nothing to unreserve" % (label, RESERVED_NAME))
        if res.get("used"):
            raise LibraryError("%s was used by %r on %s; it is a script's label now, not an open reservation. Nothing was changed."
                               % (label, res.get("used_by"), res["used"]))
        holders = label_holders(lib, label)
        if holders:
            raise LibraryError("%s is carried by %s; it is not an unused reservation. Nothing was changed." % (label, rel_posix(lib, holders[0])))
        del doc["reservations"][label]
        doc["released"].append({"label": label, "title": res.get("title", ""), "by": res.get("by", ""),
                                "reserved": res.get("reserved", ""), "released": today or datetime.date.today().isoformat()})
        save_reservations(lib, doc)
        return res


def open_reservations(lib, today=None):
    """Reservations no script carries yet, as [(label, title, by, reserved date, age in days)], oldest first. Information for
    `check`, never an error."""
    doc = load_reservations(lib)
    if not doc["reservations"]:
        return []
    carried = set()
    for p in lib.iter_scripts():
        try:
            h, _ = parse_script(p)
        except LibraryError:
            continue
        carried.add(h.get("Label", "").strip())
        carried.update(aliases_of(h))
    now = datetime.date.fromisoformat(today) if today else datetime.date.today()
    out = []
    for label, r in doc["reservations"].items():
        if r.get("used") or label in carried:
            continue
        try:
            age = (now - datetime.date.fromisoformat(r.get("reserved", ""))).days
        except ValueError:
            age = -1
        out.append((label, r.get("title", ""), r.get("by", ""), r.get("reserved", ""), age))
    return sorted(out, key=lambda x: (x[3], x[0]))


def adopt_file_reserved(lib, path, fields, write=True, today=None):
    """adopt_file, except that a `## Label:` line the file already has is kept (not renamed `Original-Label`) when it is a
    reserved label this file may carry (claim_label, with the type, line, tab and category the file will have after the adopt).
    A label that is not reserved is handled as adopt_file always did, unless its number was never given out (refused). A
    reservation still open is marked used after a real write. Returns (changed, notes) as adopt_file does."""
    p = Path(path)
    is_version = "Version-Of" in fields and "Stage" not in fields
    claimed = None
    eff = None
    if not is_version:
        head, _, _ = split_head(to_lf(p.read_bytes()).decode("utf-8"))
        existing = {}
        for ln in head:
            m = KEY_RE.match(ln)
            if m and m.group(1) not in existing:
                existing[m.group(1)] = m.group(2).strip()
        if existing.get("Label"):
            eff = dict(existing)
            for k, v in fields.items():
                if k in KEEP_EXISTING and existing.get(k):
                    continue                      # the file's own ID and Title win in adopt_text
                eff[k] = v
            before_eff = dict(eff)
            claimed = claim_label(lib, eff, p)
            if claimed:
                fields = dict(fields)
                fields["Label"] = claimed[0]
                for k in RESERVATION_FIELDS:      # o10L477 D2: what the reservation filled in is written like a passed field
                    if eff.get(k) and not before_eff.get(k):
                        fields[k] = eff[k]
    changed, notes = adopt_file(p, fields, write=write)
    if claimed and claimed[1] and write:
        mark_reservation_used(lib, claimed[0], eff, today)
        notes.append("%s was reserved; now used by this script" % claimed[0])
    return changed, notes


def ensure_label(lib, header):
    """Give the script a label if it is in a category; record an old label as an alias when
    the category (or tab, line, type) changed. Changes only the header dict. Returns None when
    nothing changed, else (key, n): the number the caller must commit with commit_number."""
    cat = header.get("Category", "").strip()
    if not cat:
        return None
    line, tab = header.get("Line", ""), header.get("Tab", "")
    code = lib.line_code(line)
    if not code:
        raise LibraryError("product line %r has no code in LINES.json; run add-line first" % line)
    if tab not in TABS:
        raise LibraryError("tab must be night or day, got %r" % tab)
    if header.get("Type") not in ("AT", "SC"):
        raise LibraryError("type must be AT or SC, got %r" % header.get("Type"))
    cc = cat[:2]
    old = header.get("Label", "").strip()
    want = (header["Type"], code, TABS[tab], cc)
    if old and label_prefix(old) == want:
        return None
    key, n = next_number(lib, line, tab, cc, code)
    header["Label"] = make_label(header["Type"], code, tab, cc, n)
    if old:
        al = aliases_of(header)
        if old not in al:
            al.append(old)
        header["Aliases"] = ", ".join(al)
    return key, n


# --------------------------------------------------------------------------- import-file parsing

def split_lines_keep(data):
    return re.findall(rb"[^\n]*\n|[^\n]+", data)


def parse_h1(text):
    """Same logic as parseMergedFile's H1 handling: returns (domain, category)."""
    lines = re.split(r"\r?\n", text)
    h1 = ""
    for l in lines:
        m = H1.match(l)
        if m:
            h1 = m.group(1).strip()
            break
    domain, category = "Sleep", h1
    m = re.match(r"^Daytime Foundational:\s*(.*)$", h1)
    if m:
        domain, category = "Daytime", m.group(1).strip()
    else:
        m = re.match(r"^Daytime Category \d+:\s*(.*)$", h1)
        if m:
            domain, category = "Daytime", m.group(1).strip()
        else:
            m = re.match(r"^Category \d+:\s*(.*)$", h1)
            if m:
                domain, category = "Sleep", m.group(1).strip()
    category = re.sub(r"\s*-\s*merged dropdown library\s*$", "", category, flags=re.I).strip()
    return domain, category


def split_chunks(data):
    """Return (preamble bytes, [chunk bytes]); preamble + chunks == data exactly. A chunk
    starts at a script header line and runs to just before the next one (or the end)."""
    raw = split_lines_keep(data)
    starts = []
    off = 0
    offsets = []
    for ln in raw:
        offsets.append(off)
        off += len(ln)
        if SCRIPT_HDR.match(ln.decode("utf-8").rstrip()):
            starts.append(offsets[-1])
    if not starts:
        return data, []
    chunks = [data[s:e] for s, e in zip(starts, starts[1:] + [len(data)])]
    return data[:starts[0]], chunks


def parse_chunk(chunk):
    """Title, tag (e.g. SC-NIGHT) and app slug of one chunk, as the importer would read it."""
    lines = [l.decode("utf-8").rstrip() for l in split_lines_keep(chunk)]
    m = SCRIPT_HDR.match(lines[0])
    tag, title = m.group(1), m.group(2).strip()
    slug = None
    for l in lines[1:]:
        sm = SLUG_MARKER.match(l)
        if sm:
            if slug is None:
                slug = sm.group(1)
            continue
        if SEPARATOR.match(l):
            break
    return title, tag, slug


def is_import_name(f):
    return f.endswith("_merged.md") and "one-bad-night" not in f


class DiskSource:
    """The import files as they sit in a folder on disk (used by the tests)."""
    def __init__(self, folder):
        self.folder = Path(folder)
        self.label = "disk folder %s" % folder

    def names(self):
        """File names relative to the folder: every file in it and in Daytime/."""
        out = [x.name for x in self.folder.iterdir() if x.is_file()]
        d = self.folder / "Daytime"
        if d.is_dir():
            out += ["Daytime/" + x.name for x in d.iterdir() if x.is_file()]
        return out

    def read(self, rel):
        return (self.folder / rel).read_bytes()

    def stamp(self):
        """The LIBRARY-BUILD.json next to the folder (voice_scripts/LIBRARY-BUILD.json), or None."""
        p = self.folder.parent / "LIBRARY-BUILD.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def retired_list(self):
        """The RETIRED-SLUGS.json next to the folder (voice_scripts/RETIRED-SLUGS.json) as bytes, or None."""
        p = self.folder.parent / "RETIRED-SLUGS.json"
        return p.read_bytes() if p.exists() else None


class GitSource:
    """The import files as git stores them (the blobs at a commit). This is the source of
    truth: a checkout on Windows may turn LF into CRLF, git does not."""
    def __init__(self, repo, path="voice_scripts/Working", rev="HEAD"):
        self.repo, self.path = str(repo), path.strip("/")
        top = self._git("rev-parse", "--show-toplevel").decode("utf-8").strip()
        if Path(top).resolve() != Path(self.repo).resolve():
            raise LibraryError("%s is not the top of a git repository (git says %s)" % (repo, top))
        self.sha = self._git("rev-parse", "--verify", rev + "^{commit}").decode("utf-8").strip()
        self.label = "git repo %s at %s (%s)" % (Path(self.repo).name, rev, self.sha[:10])

    def _git(self, *args):
        r = subprocess.run(["git", "-C", self.repo] + list(args), capture_output=True,
                           creationflags=flags())
        if r.returncode != 0:
            raise LibraryError("git %s failed: %s" % (" ".join(args), r.stderr.decode("utf-8", "replace").strip()))
        return r.stdout

    def names(self):
        out = self._git("ls-tree", "-r", "--name-only", "-z", self.sha, self.path + "/").decode("utf-8")
        pre = self.path + "/"
        return [n[len(pre):] for n in out.split("\0") if n.startswith(pre)]

    def read(self, rel):
        return self._git("show", "%s:%s/%s" % (self.sha, self.path, rel))

    def stamp(self):
        """The LIBRARY-BUILD.json in the folder above the import folder at this commit, or None."""
        parent = self.path.rpartition("/")[0]
        spec = (parent + "/" if parent else "") + "LIBRARY-BUILD.json"
        # ls-tree prints nothing when the path is not in that commit and fails on any other git problem, so
        # only "the path does not exist at that revision" means no stamp; every other failure is an error.
        listed = self._git("ls-tree", "--name-only", "-z", self.sha, "--", spec).decode("utf-8")
        if spec not in listed.split("\0"):
            return None
        return json.loads(self._git("show", "%s:%s" % (self.sha, spec)).decode("utf-8"))

    def retired_list(self):
        """The RETIRED-SLUGS.json in the folder above the import folder at this commit, as bytes, or None."""
        parent = self.path.rpartition("/")[0]
        spec = (parent + "/" if parent else "") + "RETIRED-SLUGS.json"
        listed = self._git("ls-tree", "--name-only", "-z", self.sha, "--", spec).decode("utf-8")
        if spec not in listed.split("\0"):
            return None
        return self._git("show", "%s:%s" % (self.sha, spec))


def collect_import_files(source):
    """The files the importer reads (importVoiceScripts.ts collectFiles): *_merged.md whose name
    does not contain 'one-bad-night', the top folder sorted, then Daytime/ sorted."""
    if not hasattr(source, "names"):  # a plain folder path
        source = DiskSource(source)
    names = source.names()
    top = sorted(n for n in names if "/" not in n and is_import_name(n))
    day = sorted(n for n in names if n.startswith("Daytime/") and n.count("/") == 1 and is_import_name(n[len("Daytime/"):]))
    return top + day


# --------------------------------------------------------------------------- render_chunk (draft text -> import-file chunk)
#
# Ported from product-app/scripts/promote-approved-scripts.mjs (extractSegments, assertBodyPlausible,
# assertSegmentsParseSafe, assertNoUnsupportedTokens and the per-script layout of renderMergedFile). It runs once,
# when `release` or `revise` turns an approved draft into the text the app imports. After that the script's
# SCRIPT.md body is the chunk itself and `build` only joins bytes. test_file_script.py renders the 24 promoted
# drafts again and requires the same bytes as the library holds (written by the JavaScript).
#
# JavaScript's `\s`, `trim()` and `$` differ from Python's: the character set below is JavaScript's whitespace,
# and every pattern spells it out instead of using Python's `\s`.

JS_WS = "\t\n\v\f\r              " \
        "    　﻿"
_S = "[" + JS_WS + "]"
_NS = "[^" + JS_WS + "]"
_CLASS_SPACE = JS_WS  # for use inside a character class


def js_trim(s):
    return s.strip(JS_WS)


def js_rstrip(s):
    return s.rstrip(JS_WS)


MIN_SPOKEN_LINES = 5
HEADER_END_RE = re.compile("^" + _S + "*<!--" + _S + "*END SCRIPT HEADER" + _S + "*-->" + _S + "*$")
PAUSE_CANONICAL_RE = re.compile(r"^[*_]*\(\(" + _S + r"*pause\b[^)]*\)\)[*_]*$", re.I | re.A)
# o10L639 (the human 2026-10-22): a whole-line SINGLE form `(pause 3s)` / `*(pause ~12s)*` is NOT a cue where cue-ness is decided
# (_is_pause_cue). extract_segments first rewrites such a line to the canonical `((pause Ns))` (pause_cues.normalize). This regex
# only recognises the single form, for double_pause_cue and the tests.
PAUSE_LEGACY_RE = re.compile(r"^[*_]*\(" + _S + r"*pause\b[^)]*\)[*_]*$", re.I | re.A)
HRULE_RE = re.compile("^" + _S + r"*([-*_])\1{2,}" + _S + "*$")
HASH_START_RE = re.compile("^" + _S + "*#")
QUOTE_START_RE = re.compile("^" + _S + "*>")
SLUG_START_RE = re.compile("^" + _S + "*<!--" + _S + "*slug:")
TABLE_ROW_RE = re.compile("^" + _S + r"*\|")
FILENAME_RE = re.compile(r"`[^`]*\.(md|txt|ts|mjs)`")
BOLD_LABEL_RE = re.compile("^" + _S + r"*\*\*[^*]+:\*\*")
BARE_PUNCT_RE = re.compile("^[" + _CLASS_SPACE + r"()\[\]*_]+$")
TOKEN_RE = re.compile(r"\{\{[^}]*\}\}")
META_HEADER_RE = re.compile(r"^#{1,6}" + _S)
BOLD_META_1 = re.compile(r"^-?" + _S + r"*\*\*[^*]{1,60}:\*\*")
BOLD_META_2 = re.compile(r"^\*\*[^*]+\*\*$")


def _is_metadata_section_header(line):
    t = js_trim(line)
    if not META_HEADER_RE.match(t):
        return False
    inner = js_trim(re.sub("^#{1,6}" + _S + "*", "", t, count=1).replace("*", ""))
    return inner.endswith(":")


def _is_bold_metadata_line(line):
    t = js_trim(line)
    return bool(BOLD_META_1.match(t) or BOLD_META_2.match(t))


def _is_pause_cue(line):
    t = js_trim(line)
    return bool(PAUSE_CANONICAL_RE.match(t))


def _is_hrule(line):
    return bool(HRULE_RE.match(line))


def extract_segments(text, label="draft", keep_pauses=False):
    """The spoken lines of a draft: everything below the one `<!-- END SCRIPT HEADER -->` line, minus the human's struck text
    (`~~...~~`) and notes (`{{{...}}}`), metadata sections, `#` lines, rules, blockquotes, bold labels and pause cues;
    blank runs collapse to one ''.
    keep_pauses (o10 W239, guard_segments): the pause cues stay in the list, as trimmed lines.
    Returns (segments, pause cues dropped, metadata lines dropped)."""
    lines = re.split(r"\r?\n", text)
    at = [i for i, l in enumerate(lines) if HEADER_END_RE.match(l)]
    if not at:
        raise LibraryError("%s: no %s marker, so the header/body boundary is undeclared; the draft is refused rather "
                           "than parsed by shape" % (label, END_MARK))
    if len(at) > 1:
        raise LibraryError("%s: %d %s markers, at lines %s; exactly one is allowed"
                           % (label, len(at), END_MARK, ", ".join(str(i + 1) for i in at)))
    preamble, body = lines[:at[0] + 1], lines[at[0] + 1:]
    # the human's review notes `{{{ ... }}}` (one line or several) are never spoken: take them out with the same rule check_script.py
    # and the Reader use (script_notes.strip_notes), so a note never reaches the app text (o10 W243 addendum). A `{{{` with no
    # `}}}` (or the reverse) is not guessed at: the draft is refused, because removing it could hide text the human meant to keep.
    # The approval-hash path (keep_pauses, guard_segments) refuses it too (o10L438 F4): stripping an unclosed `{{{` to the end
    # of its paragraph there would let an edit inside that paragraph of an approved text go unseen as drift.
    # the human's struck text `~~...~~` is never spoken either (o10L457, o3:W59): it comes out first, with the one rule in
    # script_notes.strip_strikes, so it never reaches the app and never counts in the approval hash. Striking text is a change
    # of the spoken text (words go), so it moves the hash like any edit and is not covered by the notes-only rule; removing
    # text that is already struck changes nothing. A line with an unclosed `~~` is refused, with its line number.
    unstruck, struck = strip_strikes("\n".join(body))
    if struck["unclosed_lines"]:
        raise LibraryError("%s: an unclosed `~~` strike on line(s) %s (body line(s) %s, counted from the line after the %s "
                           "marker); close or remove it (the human's ~~ ~~ struck text is removed, an unmatched `~~` is not "
                           "guessed at)" % (label, ", ".join(str(at[0] + 1 + n) for n in struck["unclosed_lines"]),
                                            ", ".join(str(n) for n in struck["unclosed_lines"]), END_MARK))
    stripped, found = strip_notes(unstruck)
    if found["unclosed"] or found["unopened"]:
        raise LibraryError("%s: %d `{{{` with no `}}}` and %d `}}}` with no `{{{` in the body; close or remove the note "
                           "(the human's {{{ }}} review notes are removed, an unmatched one is not guessed at)"
                           % (label, found["unclosed"], found["unopened"]))
    # o10L639: every fixable non-canonical cue becomes `((pause Ns))` on its own line before the lines are read, so a
    # single-bracket, tilde, [[ ]] or end-of-line cue is one canonical cue line to everything below. render_chunk refuses
    # what cannot be fixed (a cue mid-line or with no number).
    stripped = _pause_cues_mod.normalize(stripped)[0]
    body = stripped.split("\n")
    kept = []
    pause_cues = 0
    metadata_lines = len([l for l in preamble if js_trim(l) != ""])
    in_meta = False
    for raw in body:
        line = js_rstrip(raw)
        trimmed = js_trim(line)
        if in_meta:
            if trimmed == "":
                in_meta = False
                continue
            metadata_lines += 1
            continue
        if _is_metadata_section_header(line):
            in_meta = True
            metadata_lines += 1
            continue
        if HASH_START_RE.match(line):
            metadata_lines += 1
            continue
        if _is_hrule(line):
            metadata_lines += 1
            continue
        if QUOTE_START_RE.match(line):
            metadata_lines += 1
            continue
        if _is_bold_metadata_line(line):
            metadata_lines += 1
            continue
        if _is_pause_cue(line):
            pause_cues += 1
            if not keep_pauses:
                continue
        kept.append(trimmed)
    segments = []
    for s in kept:
        if s == "" and (not segments or segments[-1] == ""):
            continue
        segments.append(s)
    while segments and segments[-1] == "":
        segments.pop()
    return segments, pause_cues, metadata_lines


def assert_body_plausible(segments, label):
    spoken = len([s for s in segments if s != "" and not _is_pause_cue(s)])
    if spoken < MIN_SPOKEN_LINES:
        raise LibraryError("%s: only %d spoken line(s) survived metadata stripping (floor is %d); the parse ate the "
                           "script, so check the header marker" % (label, spoken, MIN_SPOKEN_LINES))


def line_of_segment(text, s):
    """The 1-based number, counted from the line after the END SCRIPT HEADER marker, of the first body line of `text` (the
    whole draft file) that holds segment `s`, or None. Counting from the marker gives the same number in every copy of the
    text, whatever its header holds. Used to point at a refused line without quoting it: a refusal names the file, the line
    and the problem, never script text (o10 W244 ruling 2: the Reader shows the reason to the human and lanes paste it into
    reports)."""
    if text is None or not s:
        return None
    lines = re.split(r"\r?\n", text)
    at = [i for i, l in enumerate(lines) if HEADER_END_RE.match(l)]
    start = at[0] + 1 if at else 0
    for n, line in enumerate(lines[start:], 1):
        if s in line:
            return n
    return None


def _where(i, text, s):
    n = line_of_segment(text, s)
    return ("segment %d (body line %d, counted from the line after the END SCRIPT HEADER marker)" % (i, n) if n
            else "segment %d" % i)


def assert_segments_parse_safe(segments, label, text=None, allow_pause_cues=False):
    """allow_pause_cues (o10L586, o10:D158 q1): render_chunk keeps the pause cues in the app text on purpose (the GPU
    service turns each into room tone), so a whole-line cue is not a leak there; every other caller still refuses one."""
    for i, s in enumerate(segments):
        def bad(msg):
            raise LibraryError("%s: %s %s" % (label, _where(i, text, s), msg))
        if HASH_START_RE.match(s):
            bad('starts with "#" and would be read as a header')
        if _is_hrule(s):
            bad("is a horizontal rule and would end the script early")
        if QUOTE_START_RE.match(s):
            bad("is a blockquote and would be dropped by the importer")
        if SLUG_START_RE.match(s):
            bad("is a slug marker and would hijack the script identity")
        if TABLE_ROW_RE.match(s):
            bad("is a markdown table row; review metadata leaked into the body")
        if FILENAME_RE.search(s):
            bad("cites a filename; review metadata leaked into the body")
        if BOLD_LABEL_RE.match(s):
            bad("is a bold metadata label; review metadata leaked into the body")
        if re.search(r"[()\[\]]", s) and BARE_PUNCT_RE.match(s):
            bad("is bare punctuation; a pause cue was cut out of the middle of a line")
        if _is_pause_cue(s) and not allow_pause_cues:
            bad("is a pause cue that survived stripping and would be read aloud")


def assert_no_unsupported_tokens(segments, label, text=None):
    for i, s in enumerate(segments):
        m = TOKEN_RE.search(s)
        if m:
            raise LibraryError("%s: %s holds an unresolved double-brace token; nothing in the app substitutes it, so the "
                               "listener would see it verbatim. Resolve or remove it (a review note takes three braces on "
                               "each side and is removed)" % (label, _where(i, text, s)))


def double_pause_cue(line):
    """the human 2026-10-22 (via o10), o10L639: only the canonical `((pause Ns))` is a pause cue in the app. A whole-line cue in
    another form (`*(pause ~12s)*`, `[[pause 4s]]`, `((pause roughly 3s))`) comes back as `((pause Ns))` from
    pause_cues.normalize; the library source file is not changed. Any other line is returned as it is.
    (extract_segments already normalizes, so render_chunk no longer calls this; it stays for callers and tests.)"""
    t = js_trim(line)
    if "pause" not in t.lower() or "\n" in t:
        return line
    new = _pause_cues_mod.normalize(t)[0]
    return new if new != t and "\n" not in new else line


def attach_pause_cues(segments):
    """o10L628 (o10:W265, rule m/212): a pause cue never makes its own segment. The app stores a member's dropdown choices
    by segment position (`seg-{index}-var-{n}`), so a cue segment above a dropdown would shift it onto the wrong paragraph.
    Each whole-line cue goes onto the end of the nearest spoken segment before it, on a new line inside that segment's
    string; a cue before any spoken text goes onto the start of the first spoken segment. The blank separators the cues
    leave side by side collapse again, so the segment count and indexes equal those of the same script with its cues
    removed. The GPU service finds `((pause Ns))` anywhere in the text, so the audio still honours the cue."""
    out = []
    lead = []
    for s in segments:
        if _is_pause_cue(s):
            idx = next((i for i in range(len(out) - 1, -1, -1) if out[i] != ""), None)
            if idx is None:
                lead.append(s)
            else:
                out[idx] = out[idx] + "\n" + s
            continue
        if s == "" and (not out or out[-1] == ""):
            continue
        out.append(s)
    while out and out[-1] == "":
        out.pop()
    if lead:
        first = next((i for i, x in enumerate(out) if x != ""), None)
        if first is None:
            return segments
        out[first] = "\n".join(lead) + "\n" + out[first]
    return out


def render_chunk(text, number, type_, tab, title, slug=None, match_row_id=None, label="draft", keep_pauses=True):
    """The import-file chunk for one script, from the text of an approved draft (the whole file, library header
    and all: everything above its one END SCRIPT HEADER line is discarded). Bytes, LF line endings, in the shape the
    app's renderMergedFile writes for the LAST script of a file: the heading, the slug marker (or the NO SLUG
    note for a script matched to a live row by title), a blank line, the spoken lines, a blank line. `build` adds
    the `---` separator when another script follows. Raises LibraryError when the draft is refused."""
    if type_ not in ("AT", "SC"):
        raise LibraryError("type must be AT or SC, got %r" % type_)
    if tab not in TABS:
        raise LibraryError("tab must be night or day, got %r" % tab)
    if bool(slug) == bool(match_row_id):
        raise LibraryError("give exactly one of a slug or a live row id to match by title")
    if not isinstance(number, int) or number < 1:
        raise LibraryError("script number must be 1 or more, got %r" % (number,))
    # o10L586 (o10:D158 q1, the human 2026-10-21): the pause cues stay in the app text. The app stores each cue line as a
    # segment, sends it to the GPU service with the rest of the script, and the service turns it into room tone.
    # keep_pauses=False gives the older cue-free text (the test that replays the 24 promoted bodies uses it).
    if keep_pauses:
        # o10L639 (the human 2026-10-22): the filed text carries only canonical cues. extract_segments normalizes what can be
        # fixed; a cue it cannot fix (mid-line, no number, an unreadable duration) is refused here.
        stuck = _pause_cues_mod.needs_human(_pause_cues_mod.scan(text))
        if stuck:
            raise LibraryError("%s: pause cue(s) that need a person, line(s) %s: %s. The one form is ((pause Ns)) alone on its "
                               "own line (see normalize_pause_cues.py)" % (
                                   label, ", ".join(str(c["line"]) for c in stuck[:8]),
                                   "; ".join("%s (%s)" % (c["original"], ", ".join(c["kinds"])) for c in stuck[:4])))
    segments, _, _ = extract_segments(text, label, keep_pauses=keep_pauses)
    assert_body_plausible(segments, label)
    assert_segments_parse_safe(segments, label, text, allow_pause_cues=keep_pauses)
    assert_no_unsupported_tokens(segments, label, text)
    if keep_pauses:
        segments = attach_pause_cues(segments)  # o10L628: no cue segment; the cue rides inside a spoken one
    lines = ["## Script %d [%s-%s]: %s" % (number, type_, "NIGHT" if tab == "night" else "DAY", title)]
    if slug:
        lines.append("<!-- slug: %s -->" % slug)
    else:
        lines.append("> NO SLUG BY DESIGN: matches live row id=%s by (title, voiceType, category). See SLUG POLICY."
                     % match_row_id)
    lines.append("")
    lines.extend(segments)
    lines.append("")
    return ("\n".join(lines) + "\n").encode("utf-8")


# --------------------------------------------------------------------------- modes

def ensure_stage_folders(lib):
    """Git does not keep empty folders, so each empty stage folder holds a .gitkeep."""
    for sd in ("1-drafts", "2-ready-for-owner", "4-retired"):
        for t in ("AT", "SC"):
            d = lib.dir / sd / t
            d.mkdir(parents=True, exist_ok=True)
            if not any(d.iterdir()):
                (d / ".gitkeep").write_bytes(b"")


def ensure_registries(lib, today):
    if not (lib.dir / "LINES.json").exists():
        save_json(lib.dir / "LINES.json", {
            "lines": [{"slug": "sleep", "name": "Sleep", "code": "Sl", "added": today}],
            "retired_codes": [],
        })
    if not (lib.dir / "CATEGORIES.json").exists():
        save_json(lib.dir / "CATEGORIES.json", {"categories": [
            {"line": "sleep", "tab": t, "number": n, "slug": s, "folder": "%s-%s" % (n, s), "app_category": a}
            for (t, n, s, a) in DEFAULT_CATEGORIES
        ]})


def cmd_split(lib, source, today):
    """Split the import files into 3-final/. If anything fails, everything this run created is
    removed again, so the command can be run again after the cause is fixed."""
    if not hasattr(source, "names"):
        source = DiskSource(source)
    existing = [p for p in lib.iter_scripts() if "3-final" in p.parts]
    if existing:
        raise LibraryError("3-final/ already holds %d scripts; split runs once on an empty library" % len(existing))
    final_dir = lib.dir / "3-final"
    before_final = {x.name for x in final_dir.iterdir()} if final_dir.is_dir() else None
    before_files = {n: (lib.dir / n).exists() for n in ("LINES.json", "CATEGORIES.json", "LABEL-COUNTERS.json", "INDEX.md")}
    before_build = lib.build_dir.exists()
    before_dir = lib.dir.exists()
    try:
        return _split(lib, source, today)
    except BaseException:
        if final_dir.is_dir():
            for x in list(final_dir.iterdir()):
                if before_final is None or x.name not in before_final:
                    shutil.rmtree(x, ignore_errors=True)
            if before_final is None:
                shutil.rmtree(final_dir, ignore_errors=True)
        if not before_build:
            shutil.rmtree(lib.build_dir, ignore_errors=True)
        for n, existed in before_files.items():
            if not existed and (lib.dir / n).exists():
                (lib.dir / n).unlink()
        if not before_dir:
            shutil.rmtree(lib.dir, ignore_errors=True)
        raise


def _split(lib, source, today):
    ensure_registries(lib, today)
    ensure_stage_folders(lib)
    cats = lib.categories()["categories"]
    code = lib.line_code("sleep")
    counters = {}
    manifest = {}
    ids = 0
    summary = {}
    for rel in collect_import_files(source):
        data = source.read(rel)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as e:
            raise LibraryError("%s is not UTF-8 text (%s)" % (rel, e))
        domain, app_cat = parse_h1(text)
        tab = "night" if domain == "Sleep" else "day"
        matches = [c for c in cats if c["tab"] == tab and c["app_category"] == app_cat]
        if len(matches) != 1:
            raise LibraryError("%s: category %r (%s) is not one of the app's categories in CATEGORIES.json" % (rel, app_cat, tab))
        cat = matches[0]
        preamble, chunks = split_chunks(data)
        side = lib.build_dir / (rel + ".preamble.md")
        side.parent.mkdir(parents=True, exist_ok=True)
        side.write_bytes(preamble)
        manifest[rel] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "scripts": len(chunks),
                         "line": "sleep", "tab": tab, "category": cat["folder"]}
        for pos, chunk in enumerate(chunks, 1):
            title, tag, slug = parse_chunk(chunk)
            if not tag or "-" not in tag:
                raise LibraryError("%s script %d (%s): no [AT-NIGHT]-style tag" % (rel, pos, title))
            type_, tagtab = tag.split("-", 1)
            if type_ not in ("AT", "SC") or tagtab.lower() != tab:
                raise LibraryError("%s script %d: tag [%s] disagrees with the file's tab %s" % (rel, pos, tag, tab))
            ids += 1
            ckey = "%s/%s/%s" % ("sleep", tab, cat["number"])
            counters[ckey] = counters.get(ckey, 0) + 1
            header = {
                "Title": title,
                "Label": make_label(type_, code, tab, cat["number"], counters[ckey]),
                "Type": type_,
                "Line": "sleep",
                "Tab": tab,
                "Category": cat["folder"],
                "Stage": "final",
                "ID": "live-%04d" % ids,
                "Slug": slug if slug else "none",
                "Aliases": "",
                "Source": rel,
                "Source-position": str(pos),
                "App": "live",
            }
            # W235 step 7: the text in the app is a record file `<LABEL> v1 agent (in app).md` with the step-6 lines
            header.update(record_header_values(header["Label"], 1, "agent"))
            path = lib.dir / expected_rel_dir(header) / version_name(header["Label"], 1, "agent", in_app=True)
            if path.exists():
                raise LibraryError("two scripts want the same folder: %s" % path)
            write_script(path, header, chunk)
            summary[tag] = summary.get(tag, 0) + 1
    save_json(lib.dir / "LABEL-COUNTERS.json", counters)
    save_json(lib.build_dir / "MANIFEST.json", {
        "note": "sha256 of each import file when the library was split from it; build compares against it. "
                "line, tab and category are the category the app files the import file under; check compares them with each script's folder.",
        "split_from": source.label,
        "files": manifest,
    })
    return summary, len(manifest)


def is_held(header):
    """True for a final script that is filed but not released to the app (`Release: held`)."""
    return header.get("Release", "").strip().lower() == "held"


SEPARATED_RE = re.compile(rb"(?:^|\n)---[ \t]*\r?\n\r?\n\Z")
TAIL_RE = re.compile(rb"((?:\r?\n)+---[ \t]*(?:\r?\n)*|(?:\r?\n)*)\Z")


def with_separator(body):
    """A chunk that another script follows must end with a `---` line and a blank line (the importer ends a
    script at `---`). A body that already does is returned as it is; any other gets exactly the bytes the app's
    renderMergedFile writes between two scripts (a blank line, `---`, a blank line), in the body's own line ending."""
    if SEPARATED_RE.search(body):
        return body
    nl = b"\r\n" if b"\r\n" in body else b"\n"
    out = body
    if not out.endswith(b"\n"):
        out += nl
    if not out.endswith(nl + nl) and out != nl:
        out += nl
    return out + b"---" + nl + nl


def assemble(preamble, bodies):
    """The bytes of one import file: its preamble, then each script body in order, a separator after every body
    but the last. Nothing is added to a body that already ends with one (all 396 non-last chunks of today's
    library do), so building today's library changes no byte."""
    parts = [with_separator(b) for b in bodies[:-1]] + bodies[-1:]
    return preamble + b"".join(parts)


def is_live(header):
    """True for a script that is built into an import file: `App: live` in any stage but retired; during the
    change (before the 436 live scripts have the line), also a final script that has a Source and is not held."""
    if header.get("Stage") == "retired" or is_held(header):
        return False
    if header.get("App", "").strip().lower() == "live":
        return True
    return header.get("Stage") == "final" and bool(header.get("Source"))


# --------------------------------------------------------------------------- only approved scripts reach the app (o10 W244)
#
# the human's rule of 2026-10-11: only fully approved scripts reach the customer, and a fully approved script is one in
# script-library/3-final/. A script that is `App: live` but sits in 1-drafts or 2-ready-for-owner is WITHHELD: it keeps its
# Slug, Source and Source-position (so it comes back as the same app row, in its old place, when it is approved), but
# build leaves its text out of the import file and lists its slug in WITHHELD-SLUGS.txt, which the app's import reads to
# hide the row. Positions inside an import file stay 1..N over ALL live scripts (kept and withheld); the gaps in a built
# file are exactly the withheld positions. RETIRED-SLUGS.json is not used for this: that list means "never again".

FINAL_DIR = STAGES["final"]
WITHHELD_NAME = "WITHHELD-SLUGS.txt"
WITHHELD_NOTE = ("# Written by .shared/scripts/file_script.py build. Slugs of scripts that are in the app's library but not "
                 "yet approved (not in script-library/3-final): the app's import hides these rows. A slug leaves this "
                 "list when its script is approved. Do not edit by hand.")


def is_kept_path(lib, path):
    """True when the record file at `path` is in script-library/3-final/ (a script that is built into an import file)."""
    try:
        rel = Path(os.path.abspath(str(path))).relative_to(Path(os.path.abspath(str(lib.dir))))
    except ValueError:
        return False
    return bool(rel.parts) and rel.parts[0] == FINAL_DIR


def collect_live_sources(lib):
    """{import file: [(position, record file path, body)]} for EVERY live script (App: live, or final with a Source
    during the change) in 1-drafts, 2-ready-for-owner and 3-final, never 4-retired, and not held: the scripts that hold a
    position in an import file, withheld ones included. Sorted by position inside each file."""
    by_src = {}
    for p in lib.iter_scripts():
        h, body = parse_script(p)
        if is_live(h) and h.get("Source"):
            try:
                pos = int(h.get("Source-position", ""))
            except ValueError:
                raise LibraryError("%s: Source-position %r is not a number" % (p.parent.name, h.get("Source-position")))
            by_src.setdefault(h["Source"], []).append((pos, p, body))
    for items in by_src.values():
        items.sort(key=lambda t: t[0])
    return by_src


def kept_items(lib, items):
    """The items (position, path, body) of an import file that are built into it: the ones in 3-final."""
    return [t for t in items if is_kept_path(lib, t[1])]


def withheld_items(lib, items):
    return [t for t in items if not is_kept_path(lib, t[1])]


def collect_final_sources(lib):
    """{import file: [(position, record file path, body)]} for every live script that is in 3-final: the scripts that are
    built into an import file. Live scripts in 1-drafts and 2-ready-for-owner are withheld (collect_live_sources has
    them all). The order inside an import file comes from Source-position."""
    return {rel: kept_items(lib, items) for rel, items in collect_live_sources(lib).items()}


def check_live_positions(rel, items, what="the final scripts built into it"):
    """Positions of ALL the live scripts of one import file (kept and withheld) must be 1..N with no gap and no repeat; the gaps
    in a built file are then exactly the withheld positions."""
    positions = [t[0] for t in items]
    if sorted(positions) != list(range(1, len(items) + 1)):
        raise LibraryError(
            "%s: %s have positions %s, not 1..%d. A script was retired, moved or removed; build cannot rebuild that "
            "import file (see W232). Withheld scripts keep their positions, so a gap is allowed only where a withheld "
            "script holds the position." % (rel, what, sorted(positions), len(items)))


# The self-coaching opening marker (o10L590). A record declares its opening once, in its library header
# (`## Opening: negative|positive`); the header is not copied into an import file, so build writes the declaration into the
# script's block as `<!-- opening: negative -->` / `<!-- opening: positive -->`, on its own line right after the slug marker
# (the place and text of promote-approved-scripts.mjs renderMergedFile, product-app PR 563). The app's reader is
# voice_scripts/tools/triple_build.py `_OPENING_RE` (`<!--\s*opening:\s*(negative|positive)\s*-->`, any line of the block) and
# parseMergedFile skips exactly this comment. AT blocks get none: an AT script is singlet and has no opening rule.
OPENING_MARKER = "<!-- opening: %s -->"
OPENING_MARKER_RE = re.compile(rb"^[ \t]*<!--\s*opening:", re.I | re.M)
SC_HEADING_RE = re.compile(rb"^##\s+Script\s+\d+\s+\[SC-")


def with_opening_marker(body, opening):
    """`body` (one script's import-file bytes) with the opening marker line after its heading and slug marker. Unchanged when
    opening is not negative/positive, the block is not an SC block, or it already carries an opening marker."""
    if opening not in ("negative", "positive") or OPENING_MARKER_RE.search(body):
        return body
    lines = body.split(b"\n")
    for i, ln in enumerate(lines):
        if SC_HEADING_RE.match(ln):
            break
    else:
        return body
    at = i + 1
    if at < len(lines) and (SLUG_MARKER.match(lines[at].decode("utf-8", "replace").rstrip("\r"))
                            or lines[at].startswith(b">")):
        at += 1
    cr = b"\r" if lines[i].endswith(b"\r") else b""
    lines.insert(at, (OPENING_MARKER % opening).encode("utf-8") + cr)
    return b"\n".join(lines)


def record_opening(path):
    """The valid `## Opening:` value of the record file at `path` ("negative" or "positive"), else "" (also when the file
    does not exist yet)."""
    try:
        v = parse_script(path)[0].get("Opening", "").strip().lower()
    except (OSError, LibraryError):
        return ""
    return v if v in ("negative", "positive") else ""


def render_import_file(lib, preamble, items, kept=None, markers=True):
    """The bytes of one import file: its preamble and the bodies of the kept items (the ones in 3-final), in position
    order. When withheld scripts followed the last kept one, its stored body still ends with the separator that
    followed it, and that tail is dropped so the file ends as every import file does. kept: a function path -> bool
    that replaces is_kept_path (a plan that has not moved the folder yet). markers False leaves the opening markers out
    (what `rebaseline` compares an old baseline against)."""
    kept = kept or (lambda path: is_kept_path(lib, path))
    bodies = [with_opening_marker(t[2], record_opening(t[1]) if markers else "") for t in sorted(items, key=lambda t: t[0]) if kept(t[1])]
    if bodies:
        bodies[-1] = strip_trailing_separator(bodies[-1])
    return assemble(preamble, bodies) if bodies else preamble


def withheld_slugs(lib, by_src=None):
    """Sorted slugs of the live scripts that are not in 3-final. Refuses a withheld script with no slug: the app could not
    hide its row."""
    by_src = collect_live_sources(lib) if by_src is None else by_src
    out = set()
    for rel, items in by_src.items():
        for pos, path, _ in withheld_items(lib, items):
            h, _b = parse_script(path)
            slug = h.get("Slug", "").strip()
            if not slug or slug.lower() == "none":
                raise LibraryError("%s (%s, position %d of %s) is in the app but not approved, and it has no slug, so the "
                                   "app's import could not hide its row. Nothing was written."
                                   % (h.get("Label") or h.get("ID"), path.parent.name, pos, rel))
            out.add(slug)
    # a slug both built (in 3-final) and withheld: the app's import refuses that (o10L443), so build refuses it first
    kept = set()
    for rel, items in by_src.items():
        for _pos, path, _b in kept_items(lib, items):
            kept.add(parse_script(path)[0].get("Slug", "").strip())
    both = sorted(out & kept)
    if both:
        raise LibraryError("the slug(s) %s belong to a script in 3-final and to a live script outside it; the app's import "
                           "refuses a slug that is both built and withheld. Nothing was written." % ", ".join(both))
    return sorted(out)


def render_withheld_list(slugs):
    """The bytes of WITHHELD-SLUGS.txt: a `#` comment line saying what the file is, then one slug per line, sorted. LF endings."""
    return (WITHHELD_NOTE + "\n" + "".join(s + "\n" for s in slugs)).encode("utf-8")


def refuse_if_check_fails(lib, what):
    """Every rule `check` knows decides what may reach the app, so a command that writes import files runs them
    first (without rewriting INDEX.md) and writes nothing when one fails. There is no flag to skip this."""
    errors, _ = cmd_check(lib, write=False)
    if errors:
        shown = "; ".join(errors[:5]) + ("; and %d more" % (len(errors) - 5) if len(errors) > 5 else "")
        raise LibraryError("%s refused: check reports %d problem(s), so nothing was written. Run `check` and fix "
                           "them first. %s" % (what, len(errors), shown))


# --------------------------------------------------------------------------- the human-approved scripts (o10 W239)
#
# A record whose header carries `## Approved-By: the human <date> <commit>` holds text the human approved. `revise`, `release`,
# `file` and `sync-bodies` refuse to change that text unless the run passes `--owner-ruling <ref>`; the ref goes into the
# new record's `## Approved-Ruling:` line and into script-library/THE HUMAN-APPROVED.csv. `check` reports a record whose text
# no longer matches the sha256 the CSV holds, a CSV row with no record, and an Approved-By line with no CSV row.
#
# What counts as "text": the lines of the body (below the END SCRIPT HEADER line) that are spoken. For a record built
# into an import file (it has a Source, or it was retired from the app), these are the lines the app's importer keeps
# (importer_segments, the rule of importVoiceScripts.ts parseMergedFile, o10L373 S1): every line under the
# `## Script N` heading except the slug marker, `>` lines, and anything after a `---` line, so a `#` line, a `### X:`
# heading or a whole-bold line added by hand counts. For a draft, they are the lines extract_segments keeps (the
# rule that decides what `release` and `revise` render into an import file: no `#` heading lines, rules, blockquote
# notes, bold labels or metadata sections), plus the pause cues, which extract_segments drops but which are part of
# what is heard in a draft; minus the slug marker line. Each line is trimmed, blank runs collapse to one, line endings
# do not matter. `release` compares the draft and the chunk without pause cues (render_chunk drops them for every
# script), so a plain release of the human's text needs no ruling (o10L373 S4). The header is not text: a change of category, stage, Label, App
# line, Slug, Version or Author is a header-only change and is always allowed. The title is not text either (it is in the
# header and in the body's `## Script N [TAG]: Title` heading line, which the rule leaves out). What the tool also treats as
# a change to an approved script, although the spoken text stays the same: `file --type` (the script becomes another kind
# of audio) and `file --stage retired` (the text leaves the app).

APPROVED_KEY = "Approved-By"
RULING_KEY = "Approved-Ruling"
APPROVED_CSV = "THE HUMAN-APPROVED.csv"
APPROVED_COLUMNS = ["script_id", "label", "date", "app_commit", "source_audit", "text_sha256", "rulings", "note"]

# o10L388 round 4 (S-1), round 5 (S-A): the workspace the running file_script.py belongs to, and its own script-library.
# main() takes WORKSPACE_ROOT as the default --root. cmd_check asks is_workspace_library, which compares folders with
# os.path.samefile, so the extended-length (drive path with a question-mark prefix) and loopback-share spellings of the
# workspace's library count too.
WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_LIBRARY = (WORKSPACE_ROOT / "script-library").resolve()


def is_workspace_library(path):
    """True when `path` is the workspace's own script-library, however it is spelled. False when either folder is missing."""
    try:
        return os.path.samefile(path, WORKSPACE_LIBRARY)
    except OSError:
        return False

UNKNOWN = "UNKNOWN"

# o10L373 S2, widened by o10 (o10L388 round 2, R1): the scripts marked the human-approved (o10 W239), each as
# (permanent `## ID:`, slug when the script was marked or `none`, the `## Approved-By:` value, the approved text's
# sha256 as THE HUMAN-APPROVED.csv held it). A pin is held by ANY of three keys: a record whose id equals the pin's id, OR
# whose Slug or alias equals the pin's slug (unless it is `none`), OR whose approved text hashes to the pin's sha256 (or
# to the sha256 in the CSV row for that id, so a ruling that moves the row's hash does not break the pin) is that
# script. Once a record matches, every field the pin names must still agree: id, slug, Approved-By value, text hash,
# and the CSV row itself. `check` reports one that does not, naming the pin and the field. The hash is guard_hash, which
# leaves out the `<!-- slug: -->` marker, so a slug edit cannot also change it. The folder and the label are not pinned:
# `file --category` and `file --stage` change them legitimately. The list only grows: a removal needs the human's ruling and
# a reviewed change here, and test_file_script_approved.py holds its own copy of the 25 first ids.
APPROVED_PINNED = (
    ("live-0005", "sleep-is-not-a-task-i-can-fail",
     "the human 2026-07-09 app:6676588b",
     "f023323ea60d29dc6cb894b426cd11be1b6759d726a3f26771f811fd1d5efa05"),
    ("live-0007", "worry-has-an-off-ramp",
     "the human 2026-07-10 app:52bf545b",
     "5d5ef7c1018916ccb54c14f5aa855a38c8a7b36cb0f9718e7ebd75fdd59ef6bb"),
    ("live-0015", "the-sleep-switch",
     "the human 2026-07-10 app:ff5c68ff",
     "e8bc84132484c63ce580229df2c97bd5a724e163f9449bfc9d58d3e70019eb13"),
    # o3:W47: hash moved to the human's v4 text by his ruling of 2026-10-11 15:45 -07:00 ("is done. Make this the
    # approved final."); was d73ba7d51ea906edbdd014f8b0221bf6582087e006f0c218a78185dde2557780.
    ("live-0021", "stop-counting-the-hours",
     "the human 2026-07-09 app:3a6f8d88",
     "1f54e1da1ae4b4aa3ab91d031dc58fdc9174754a21a2689d457b721d496a3203"),
    ("live-0129", "closing-all-the-mental-tabs",
     "the human 2026-07-09 app:ea3095e0",
     "748f96adb81e3341aa45c9efb8648cafa0f31cae72f31ea72d7f1a007a8f6bb8"),
    ("live-0135", "i-am-not-an-insomniac",
     "the human 2026-07-08 app:58239efb",
     "125b1a13fa7f0e88793825c83fba5e8aee157f95dde6c8052754179c37a21901"),
    ("live-0166", "the-helicopter-landing",
     "the human 2026-09-25 app:1ef5c0ae",
     "091f745d3c16bc97f60e82277fc24abc2a3e77d3e92a40e93f73c9f0bd52f3a3"),
    ("live-0197", "the-high-achievers-rest",
     "the human 2026-07-08 app:ecb9092a",
     "60f7d256ad08685422835a5fb06c49edae4d1661b18c6ab580dc1f36265bdd70"),
    ("live-0199", "none",
     "the human 2026-07-20 ws:aaacca26d",
     "f69ea9349b8bb0d9da68e84a6d211ef9e0a635522f31611b645256c0947a2984"),
    ("live-0201", "deep-rest-for-high-achievers",
     "the human 2026-07-20 ws:aaacca26d",
     "c5d78c59121657cbd2a917afb6df72849ede616421a88d39fbc1e855576374ac"),
    ("live-0202", "letting-go-of-trying-to-sleep",
     "the human 2026-07-20 ws:aaacca26d",
     "80a8374dd383f222d9a06f94041e8af6c1fc2e496920d0852f96e3297c6ee1e8"),
    ("live-0205", "the-racing-mind-settles-at-bedtime",
     "the human 2026-07-20 ws:aaacca26d",
     "4040ba400a980a7c74b33b8444c634cb16e88948e510efbce521529d6b6c97ab"),
    ("live-0210", "autogenic-descent",
     "the human 2026-07-20 ws:aaacca26d",
     "554415bea24e03d9c266d91ff0f9a3ea15c17d80df4f9ea09a953114643ec859"),
    ("live-0212", "loving-sleep",
     "the human 2026-07-20 ws:aaacca26d",
     "16e7b33b979f8ad812371e51b0f69930f1ae4009c072db94b3b31e70267d35f5"),
    ("live-0215", "the-worry-download-and-release",
     "the human 2026-07-20 ws:aaacca26d",
     "14744364e2f44fd544bd1453c3db402866f0b6458ee5da48550c9e14e5f14ddf"),
    ("live-0302", "this-is-the-sleep-not-the-situation",
     "the human 2026-07-09 app:2ebea3ad",
     "2513534e808444f02437e6985583c63ea17a4de8f43fee95c1dc7ad1ddef4db2"),
    ("live-0327", "closer-to-normal-than-you-fear",
     "the human 2026-07-08 app:6b9322a1",
     "e459e80d30647ce54fafb6ef34817c1faa37ab19eac53cef495d283e5d8adaa0"),
    ("live-0366", "the-second-wind-is-coming",
     "the human 2026-07-08 app:dbc5ddba",
     "3e5c92857d6f2b79c2d59e073ecb69c6ebad57af04c09a9769ed6237b24cf8d2"),
    ("live-0367", "the-caffeine-crash-is-chemistry-not-your-real-ceiling",
     "the human 2026-07-08 app:a7c67d79",
     "37a263d04eaebbd03dceeb31c85ea802f63676e6361e1aeb911fc523ca1d5971"),
    ("live-0406", "temporary-even-when-it-feels-permanent",
     "the human 2026-07-09 app:0acf9729",
     "91d2dfddf738da12b1a32e6b8473de684ea3e6280421dc0b5d3e6832af52c72b"),
    ("live-0431", "none",
     "the human 2026-07-20 ws:aaacca26d",
     "986b9b5e2f784019512cb9462e364f75d75da9b1d932fbfe1c67e30ca400b18b"),
    ("live-0432", "none",
     "the human 2026-07-20 ws:aaacca26d",
     "c027ba77bc0666c9fc892e714a21968a115d00b51875bda9a5270581c34d4942"),
    ("live-0433", "none",
     "the human 2026-07-20 ws:aaacca26d",
     "8c55d4c2b0a7258e259c89592026f38c3ccf93a942af600461acaf10f8abecec"),
    ("live-0435", "wake-up-regular-morning",
     "the human 2026-08-01 ws:0f8b59ec2",
     "5dd57ccf1e5d208995234cfe05e843386e5652625f82445c40d49987ff526104"),
    # o3:W57 (o10L452): hash moved to the released `v4 o8 (in app)` text of 0f664250b under the notes-only rule (o10 W245):
    # its spoken lines, with notes, comments and pause cues removed, equal those of the pinned text (v3 THE HUMAN and v4 o8 at
    # e05e1735b), so no ruling was needed; was 9fb34014a749687a7cd477e554b9d8fc16502f09c81cc5bc5457f2af76449073.
    ("o8L145-0001", "none",
     "the human UNKNOWN UNKNOWN",
     "8d7848d62c0cb39b2fb084739f7e8ed2f42d5a4a248f92ff021e449445d9b376"),
)


IMPORTER_SEPARATOR_RE = re.compile(r"^\s*---\s*$")     # importVoiceScripts.ts parseMergedFile: `---` ends a script


def _collapse_blanks(kept):
    """Blank runs collapse to one '', with no blank at either end."""
    segments = []
    for s in kept:
        if s == "" and (not segments or segments[-1] == ""):
            continue
        segments.append(s)
    while segments and segments[-1] == "":
        segments.pop()
    return segments


# The app's SC_BUILD_MARKER (importVoiceScripts.ts:46): `<!-- YOU: ... -->` and `<!-- opening: ... -->` lines are build
# metadata the importer skips, never speech (o10L598, from the o10L591 review).
IMPORTER_BUILD_MARKER_RE = re.compile("^" + _S + "*<!--" + _S + "*(?:YOU|opening):.*-->" + _S + "*$", re.I)


def importer_segments(body_lines):
    """The lines the app's importer speaks from a live record's body (o10L373 S1). The rule is the one in
    product-app server/scripts/importVoiceScripts.ts parseMergedFile, which reads the import file that `build`
    makes from these bodies byte for byte: a `## Script N [TAG]: Title` heading starts a script; inside it the slug
    marker, an END SCRIPT HEADER line and `>` lines are dropped, a `---` line ends the script, and every other line
    is a spoken segment (a `#` line, a `### X:` heading and a whole-bold line included). Lines before the first
    heading or after a `---` belong to no script and are not spoken. The first heading carries only the title, which
    is not text; a second heading would split the record into two scripts in the app, so it is kept as a line.
    Lines are trimmed and blank runs collapse, as in the draft rule."""
    kept, cur, headings = [], False, 0
    for raw in body_lines:
        line = js_rstrip(raw)
        if SCRIPT_HDR.match(line):
            headings += 1
            if headings > 1:
                kept.append(js_trim(line))
            cur = True
            continue
        if not cur:
            continue
        if (SLUG_MARKER.match(line) or HEADER_END_RE.match(line) or QUOTE_START_RE.match(line)
                or IMPORTER_BUILD_MARKER_RE.match(line)):
            continue
        if IMPORTER_SEPARATOR_RE.match(line):
            cur = False
            continue
        kept.append(js_trim(line))
    return _collapse_blanks(kept)


def guard_is_live(h, data):
    """True when the record's body is an import-file chunk, so the importer's rule decides its text: it has a Source
    (it is built into an import file), or it was retired from the app (a retirement drops Source but keeps the chunk,
    whose `## Script N` heading stays)."""
    if h.get("Source", "").strip():
        return True
    if h.get("Stage") == "retired":
        body = to_lf(data).decode("utf-8", "replace").split("\n")
        return any(SCRIPT_HDR.match(js_rstrip(l)) for l in body)
    return False


def _header_of(data):
    """The `## Key:` fields above the END SCRIPT HEADER line of a record file's bytes ({} for END_MARK + body)."""
    header = {}
    for line in to_lf(data).decode("utf-8", "replace").split("\n"):
        if HEADER_END_RE.match(line):
            break
        m = KEY_RE.match(line)
        if m:
            header[m.group(1)] = m.group(2)
    return header


def guard_segments(data, live=None):
    """The approved-text lines of a record file's bytes (or of END_MARK + body): see the section comment above.
    live: True for a body built into an import file (the importer's rule, importer_segments), False for a draft (the
    release rule, extract_segments, plus pause cues); None reads it from the header in data (guard_is_live), so
    END_MARK + body without a header counts as a draft unless the caller says otherwise.
    Raises LibraryError when the file has no single END SCRIPT HEADER line."""
    text = to_lf(data).decode("utf-8", "replace")
    lines = text.split("\n")
    at = [i for i, l in enumerate(lines) if HEADER_END_RE.match(l)]
    if live is None:
        live = guard_is_live(_header_of(data), data)
    if live:
        if len(at) != 1:
            raise LibraryError("approved text: %d %s lines; exactly one is allowed" % (len(at), END_MARK))
        # o10L586: a live body now carries the pause cues (render_chunk keeps them), but the approval covers the words and
        # a cue is timing. Dropping the cues here keeps every hash already in THE HUMAN-APPROVED.csv valid.
        return spoken_only(importer_segments(lines[at[0] + 1:]))
    if len(at) == 1:
        lines = lines[:at[0] + 1] + [l for l in lines[at[0] + 1:] if not SLUG_MARKER.match(l)]
    segments, _, _ = extract_segments("\n".join(lines), "approved text", keep_pauses=True)
    return segments


def spoken_only(segments):
    """Guard segments without the pause cues (blank runs collapsed again): the form `release` renders, because
    render_chunk drops the pause cues of every script (o10L373 S4, recommendation b)."""
    return _collapse_blanks([s for s in segments if not _is_pause_cue(s)])


def guard_hash(data, live=None):
    """sha256 of the approved-text lines (guard_segments) of a record file's bytes."""
    return hashlib.sha256("\n".join(guard_segments(data, live)).encode("utf-8")).hexdigest()


def guard_hash_of_body(body, live):
    return guard_hash((END_MARK + "\n").encode("utf-8") + body, live)


def refuse_if_approved_drifted(lib, h, data, what):
    """o10L373 S3: a command that keeps an approved record's text (a move, a stage change) still refuses, writing
    nothing, when that text no longer hashes to the record's THE HUMAN-APPROVED.csv row, so a hand edit cannot be filed on
    into 3-final/ while `check` reports it."""
    if not h.get(APPROVED_KEY, "").strip():
        return
    row = approval_row(lib, h)
    want = row["text_sha256"].strip()
    if not want or want.upper() == UNKNOWN:
        return
    got = guard_hash(data, guard_is_live(h, data))
    if got != want:
        raise LibraryError("%s refused: %s carries `## %s: %s`, and its spoken text has drifted from the human's approved "
                           "text (it hashes to %s, %s says %s). Nothing was written. Restore the human's text first, or "
                           "record the human's ruling with --owner-ruling on revise."
                           % (what, h.get("Label") or h.get("ID"), APPROVED_KEY, h[APPROVED_KEY].strip(), got[:12],
                              APPROVED_CSV, want[:12]))


def approved_value(row):
    """The `## Approved-By:` value a CSV row asks for."""
    return "the human %s %s" % (row["date"].strip(), row["app_commit"].strip())


def read_approved_csv(lib):
    """The rows of THE HUMAN-APPROVED.csv as dicts ([] when the file is absent). Raises LibraryError on a damaged file."""
    path = lib.dir / APPROVED_CSV
    if not path.is_file():
        return []
    import io
    text = path.read_bytes().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if reader.fieldnames != APPROVED_COLUMNS:
        raise LibraryError("%s: the columns are %s, expected %s" % (APPROVED_CSV, reader.fieldnames, APPROVED_COLUMNS))
    rows = []
    for r in reader:
        if None in r or any(v is None for v in r.values()):
            raise LibraryError("%s: a row has the wrong number of fields: %s" % (APPROVED_CSV, list(r.values())[:2]))
        rows.append(r)
    return rows


def render_approved_csv(rows):
    import io
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=APPROVED_COLUMNS, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in APPROVED_COLUMNS})
    return buf.getvalue().encode("utf-8")


def validate_ruling(ruling):
    """None (no flag) or a one-line reference with no `;` in it. An empty reference is refused."""
    if ruling is None:
        return None
    r = ruling.strip()
    if not r or "\n" in r or "\r" in r or ";" in r:
        raise LibraryError("--owner-ruling needs a reference to the human's ruling (an OrchDoc entry or message, such as "
                           "'o10:D152'): one line, not empty, no ';'. Got %r." % ruling)
    return r


def approval_row(lib, h):
    """The THE HUMAN-APPROVED.csv row of an approved record, or a LibraryError when the CSV has none (the registry and
    the header must agree before any command changes the text)."""
    sid = h.get("ID", "").strip()
    for r in read_approved_csv(lib):
        if r["script_id"].strip() == sid:
            return r
    raise LibraryError("%s carries `## %s: %s` but %s has no row for the id %s; run check"
                       % (h.get("Label") or sid, APPROVED_KEY, h.get(APPROVED_KEY), APPROVED_CSV, sid))


def approval_gate(lib, h, old_data, new_data, what, ruling, also=None, new_live=None, ignore_pauses=False):
    """The check every text-changing command runs before it writes anything. h: the record's header. old_data and
    new_data: the record's bytes before and after (new_data may be END_MARK + body). also: a reason, other than the
    spoken text, why the change touches the approved script (a type change, a retirement). The text rule of both
    sides is the old record's (guard_is_live(h, old_data)) unless new_live says the new bytes are a chunk (release).
    ignore_pauses: compare the spoken lines without pause cues (release, o10L373 S4).
    Returns None when the record is not approved or the change leaves the approved text alone; else the ruling entry
    (`<ref> (<date>)`) to write into the new header and the CSV. Raises LibraryError, writing nothing, when the
    change needs a ruling and `ruling` is None."""
    value = h.get(APPROVED_KEY, "").strip()
    if not value:
        return None
    old_live = guard_is_live(h, old_data)
    a = guard_segments(old_data, old_live)
    b = guard_segments(new_data, old_live if new_live is None else new_live)
    if ignore_pauses:
        a, b = spoken_only(a), spoken_only(b)
    reasons = []
    if a != b:
        reasons.append("the spoken text")
    if also:
        reasons.append(also)
    if not reasons:
        return None
    label = h.get("Label") or h.get("ID")
    if not ruling:
        raise LibraryError("%s carries the header `## %s: %s`: the human approved this script, and %s would change %s. "
                           "Nothing was written. If the human ruled on this change, run it again with --owner-ruling <ref> "
                           "(the OrchDoc entry or message that records his ruling); the ref is written into the new "
                           "record's `## %s:` line and into %s."
                           % (label, APPROVED_KEY, value, what, " and ".join(reasons), RULING_KEY, APPROVED_CSV))
    approval_row(lib, h)          # a ruling is recorded in the CSV, so the row must exist before anything is written
    return "%s (%s)" % (ruling, datetime.date.today().isoformat())


def with_ruling(h, entry):
    """A copy of header h with the ruling entry added to its `Approved-Ruling` line."""
    h2 = dict(h)
    old = h2.get(RULING_KEY, "").strip()
    h2[RULING_KEY] = (old + "; " if old else "") + entry
    return h2


def apply_ruling_to_rows(rows, h, new_hash, entry, note_for=None):
    """Set the new text's sha256 in the script's row of rows (read_approved_csv), and append the ruling entry
    (None: no ruling, only the hash moves, as for a release whose spoken lines are unchanged). note_for (a command name,
    with no ruling only): a notes-only note that names the old hash as its "after" hash names the new one (o10L452)."""
    sid = h.get("ID", "").strip()
    for r in rows:
        if r["script_id"].strip() == sid:
            if note_for and not entry:
                r["note"] = retarget_notes_only_note(r["note"], r["text_sha256"].strip(), new_hash, note_for)
            r["text_sha256"] = new_hash
            if entry:
                r["rulings"] = (r["rulings"].strip() + "; " if r["rulings"].strip() else "") + entry
            return rows
    raise LibraryError("%s has no row for the id %s" % (APPROVED_CSV, sid))


def write_approved_csv(lib, data):
    """Write THE HUMAN-APPROVED.csv whole or not at all (o10L373 S6): the bytes go to a temporary file beside it, which
    then replaces it in one rename, so a failure part way leaves the old file as it was."""
    path = lib.dir / APPROVED_CSV
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_bytes(data)
        os.replace(str(tmp), str(path))
    except BaseException:
        if tmp.exists():
            tmp.unlink()
        raise


def record_ruling_in_csv(lib, h, new_hash, entry, note_for=None):
    """Write the new text's sha256 and the ruling entry (None: the hash only) into the script's CSV row. Returns the
    CSV's old bytes, for the undo of a command that fails after this. note_for: see apply_ruling_to_rows."""
    path = lib.dir / APPROVED_CSV
    old = path.read_bytes()
    rows = apply_ruling_to_rows(read_approved_csv(lib), h, new_hash, entry, note_for=note_for)
    write_approved_csv(lib, render_approved_csv(rows))
    return old


# --------------------------------------------------------------------------- the notes-only rule (o10 W245, addition C)
#
# the human writes comments in two braces `{{...}}` and review notes in three `{{{...}}}`. A text that holds a comment where a
# note belongs hashes to a different value once the braces are made three (the draft rule removes a note and keeps a comment's
# words), so the edit reads as drift from the human's approval although no spoken word changed. The rule: when a text file in the
# script's folder still hashes to the THE HUMAN-APPROVED.csv row, and that file's spoken lines with every note and comment removed
# equal the record's spoken lines with every note and comment removed, the row takes the record's hash with a note saying only
# notes changed, and no ruling is needed. Any other difference leaves the record drifted, and the existing refusals stand.
# Used by `approve` (before any step) and `release` (before its checks). A refusal here quotes no script text: it names no line.
# Struck text `~~...~~` (o10L457) is NOT a note for this rule: extract_segments has already removed it from both sides, so a
# text that strikes words the baseline speaks differs from it here and stays drifted, needing the human's approval or ruling (his
# approval of the file that holds the strikes is that ruling). Two texts that differ only in struck words that neither speaks
# compare equal, as they hash equal.

def spoken_without_notes(segments):
    """The approved-text lines with every `{{{ }}}` note and every `{{ }}` comment taken out, whitespace collapsed inside
    each line and empty lines dropped. None when a note is unmatched (it is not guessed at)."""
    text, found = strip_notes("\n".join(segments))
    if found["unclosed"] or found["unopened"]:
        return None
    text = strip_comments(text)
    out = []
    for ln in text.split("\n"):
        ln = " ".join(ln.split())
        if ln:
            out.append(ln)
    return out


def notes_only_baseline(lib, folder, p, h, want):
    """The path of a text in `folder` (or its versions/) other than `p` whose approved-text hash is `want`, else None."""
    for d in (Path(folder), Path(folder) / "versions"):
        try:
            names = sorted(os.listdir(str(d)))
        except OSError:
            continue
        for n in names:
            f = d / n
            if f.resolve() == Path(p).resolve() or not n.endswith(".md") or not f.is_file():
                continue
            pn = training_header.parse_script_name(n)
            if pn.version is None or pn.kind not in RECORD_KINDS:
                continue
            try:
                data = f.read_bytes()
                if guard_hash(data, guard_is_live(_header_of(data), data)) == want:
                    return f
            except (LibraryError, OSError):
                continue
    return None


def adopt_notes_only_hash(lib, p, what):
    """The notes-only rule for the record file `p`. When `p` carries an approval whose THE HUMAN-APPROVED.csv row holds a real hash
    that `p` no longer hashes to, and a text in its folder still hashes to the row, and the two texts differ only in notes and
    comments, the row takes `p`'s hash and its note column says only notes changed. Returns (old hash, new hash, baseline file)
    or None when the rule does not apply (nothing is written). The caller keeps the CSV's old bytes to restore on a failure."""
    p = Path(p)
    try:
        h, _ = parse_script(p)
        if not h.get(APPROVED_KEY, "").strip():
            return None
        row = approval_row(lib, h)
        want = row["text_sha256"].strip()
        if not want or want.upper() == UNKNOWN:
            return None
        data = p.read_bytes()
        live = guard_is_live(h, data)
        got = guard_hash(data, live)
        if got == want:
            return None
        base = notes_only_baseline(lib, p.parent, p, h, want)
        if base is None:
            return None
        bdata = base.read_bytes()
        a = spoken_without_notes(guard_segments(bdata, guard_is_live(_header_of(bdata), bdata)))
        b = spoken_without_notes(guard_segments(data, live))
    except (LibraryError, OSError, UnicodeError):
        return None
    if a is None or b is None or a != b:
        return None
    rows = read_approved_csv(lib)
    for r in rows:
        if r["script_id"].strip() == h.get("ID", "").strip():
            r["text_sha256"] = got
            note = "only notes changed (hash %s to %s, %s, file_script.py %s)" % (
                want[:12], got[:12], datetime.date.today().isoformat(), what)
            r["note"] = (r["note"].strip() + " | " if r["note"].strip() else "") + note
            break
    write_approved_csv(lib, render_approved_csv(rows))
    return want, got, base


# o10L452 (defect 1 of o3:W57): adopt_notes_only_hash writes the hash the record has at that moment into its note. When the
# record is then released (approve of a held script, or a plain release), the release re-renders the text without notes and
# pause cues and moves the row's hash again with no ruling, so the note named a hash the column no longer held. The release
# now moves the note's "after" hash with the column (retarget_notes_only_note), and `repair-notes-only-note` corrects a row
# written before this fix, once it has checked the same evidence the rule used.
NOTES_ONLY_NOTE_RE = re.compile(r"only notes changed \(hash ([0-9a-f]{12}) to ([0-9a-f]{12}), (\d{4}-\d{2}-\d{2}), "
                                r"file_script\.py ([a-z ]+)\)")


def notes_only_note_entry(note):
    """The match of the LAST ` | `-separated entry of a THE HUMAN-APPROVED.csv note when the notes-only rule wrote it, else None.
    An earlier notes-only entry followed by any other entry is history and is not returned."""
    parts = [x.strip() for x in note.split(" | ")]
    return NOTES_ONLY_NOTE_RE.fullmatch(parts[-1]) if parts and parts[-1] else None


def retarget_notes_only_note(note, old_hash, new_hash, what):
    """`note` with its last entry, when that is a notes-only entry whose "after" hash is old_hash[:12], naming new_hash[:12]
    instead and `what` added to the commands it names. Any other note comes back unchanged."""
    m = notes_only_note_entry(note)
    if m is None or m.group(2) != old_hash[:12] or old_hash[:12] == new_hash[:12]:
        return note
    cmds = m.group(4) if what in m.group(4).split(" and ") else "%s and %s" % (m.group(4), what)
    entry = "only notes changed (hash %s to %s, %s, file_script.py %s)" % (m.group(1), new_hash[:12], m.group(3), cmds)
    parts = [x.strip() for x in note.split(" | ")]
    return " | ".join(parts[:-1] + [entry])


def spoken_without_notes_or_pauses(data):
    """spoken_without_notes of a record file's bytes with the pause cues also removed (the form a release renders), or None."""
    return spoken_without_notes(spoken_only(guard_segments(data, guard_is_live(_header_of(data), data))))


def notes_only_text_for(folder, p, want, prefix=False):
    """A text in `folder` or its versions/ other than `p` whose approved-text hash is `want` (or starts with it when prefix),
    and whose spoken lines with notes, comments and pause cues removed equal `p`'s. None when there is none."""
    try:
        mine = spoken_without_notes_or_pauses(Path(p).read_bytes())
    except (LibraryError, OSError, UnicodeError):
        return None
    if mine is None:
        return None
    for d in (Path(folder), Path(folder) / "versions"):
        try:
            names = sorted(os.listdir(str(d)))
        except OSError:
            continue
        for n in names:
            f = d / n
            if f.resolve() == Path(p).resolve() or not n.endswith(".md") or not f.is_file():
                continue
            try:
                data = f.read_bytes()
                gh = guard_hash(data, guard_is_live(_header_of(data), data))
                if (gh.startswith(want) if prefix else gh == want) and spoken_without_notes_or_pauses(data) == mine:
                    return f
            except (LibraryError, OSError, UnicodeError):
                continue
    return None


def cmd_repair_notes_only_note(lib, apply=False):
    """o10L452: correct the note of a THE HUMAN-APPROVED.csv row whose last entry, written by the notes-only rule, names an "after"
    hash the row's text_sha256 no longer holds (a release moved the hash after the rule ran, before release kept the note in
    step). A row is corrected only when its record hashes to text_sha256 (what `check` recomputes) and a text in the record's
    folder hashes to the note's "before" hash with the same spoken lines once notes, comments and pause cues are removed.
    Only the note's "after" hash and its command list change. Returns [(script id, before, old after, new after, done)];
    writes THE HUMAN-APPROVED.csv only with apply."""
    refuse_if_check_fails(lib, "repair-notes-only-note")
    rows = read_approved_csv(lib)
    by_id = {}
    for f in lib.iter_scripts():
        try:
            h, _ = parse_script(f)
        except LibraryError:
            continue
        if h.get("ID", "").strip():
            by_id.setdefault(h["ID"].strip(), f)
    out = []
    for r in rows:
        m = notes_only_note_entry(r["note"])
        col = r["text_sha256"].strip()
        if m is None or not col or col.upper() == UNKNOWN or m.group(2) == col[:12]:
            continue
        sid = r["script_id"].strip()
        p = by_id.get(sid)
        ok = False
        if p is not None:
            data = p.read_bytes()
            ok = (guard_hash(data, guard_is_live(_header_of(data), data)) == col
                  and notes_only_text_for(p.parent, p, m.group(1), prefix=True) is not None)
        if ok:
            r["note"] = retarget_notes_only_note(r["note"], m.group(2), col, "release")
        out.append((sid, m.group(1), m.group(2), col[:12], ok))
    if apply and any(x[4] for x in out):
        write_approved_csv(lib, render_approved_csv(rows))
    return out


def approved_report(lib, rows):
    """(errors, notes) about the the human-approved scripts: rows is read_all(lib). Errors: a CSV row whose script has no
    record, a record without the row's Approved-By line (or with another), an Approved-By line with no row, a ruling
    line that disagrees with the row, and a record whose approved-text sha256 differs from the row's. Notes: a row
    whose sha256 is UNKNOWN, and a row whose note says UNRULED (the human has not ruled on a known difference)."""
    errors, notes = [], []
    try:
        crows = read_approved_csv(lib)
    except (LibraryError, UnicodeError, csv.Error) as e:
        return ["%s: %s" % (APPROVED_CSV, e)], notes
    by_id = {}
    for p, h, body, err in rows:
        if h and h.get("ID", "").strip():
            by_id.setdefault(h["ID"].strip(), []).append((p, h))
    seen = set()
    for r in crows:
        sid = r["script_id"].strip()
        where = "%s row %s (%s)" % (APPROVED_CSV, sid, r["label"].strip())
        if not sid or sid in seen:
            errors.append("%s: the script id is empty or listed twice" % where)
            continue
        seen.add(sid)
        found = by_id.get(sid)
        if not found:
            errors.append("%s: no script in the library has that id, so there is no record to carry the approval" % where)
            continue
        p, h = found[0]
        rel = p.parent.relative_to(lib.dir).as_posix()
        want = approved_value(r)
        if not approval_matches(h.get(APPROVED_KEY, ""), r):
            errors.append("%s: its header has `## %s: %s`, but %s says `%s`; run add_approved_headers.py --apply"
                          % (rel, APPROVED_KEY, h.get(APPROVED_KEY, "").strip() or "(none)", APPROVED_CSV, want))
        if h.get(RULING_KEY, "").strip() != r["rulings"].strip():
            errors.append("%s: its `## %s:` line (%r) differs from the rulings in %s (%r)"
                          % (rel, RULING_KEY, h.get(RULING_KEY, "").strip(), APPROVED_CSV, r["rulings"].strip()))
        want_sha = r["text_sha256"].strip()
        if want_sha.upper() == UNKNOWN or not want_sha:
            notes.append("%s: the approved text's sha256 is UNKNOWN in %s, so its text is not compared" % (rel, APPROVED_CSV))
        else:
            try:
                data = p.read_bytes()
                got = guard_hash(data, guard_is_live(h, data))
            except (LibraryError, OSError) as e:
                errors.append("%s: the approved text cannot be read: %s" % (rel, e))
                continue
            if got != want_sha:
                errors.append("%s: the human-approved text drifted: the spoken text now hashes to %s but the text named by "
                              "`## %s: %s` hashes to %s in %s. Restore the human's text, or record the human's ruling with "
                              "--owner-ruling on revise"
                              % (rel, got[:12], APPROVED_KEY, want, want_sha[:12], APPROVED_CSV))
        if r["note"].strip().upper().startswith("UNRULED"):
            notes.append("%s: %s" % (rel, r["note"].strip()))
    for sid, found in by_id.items():
        for p, h in found:
            if h.get(APPROVED_KEY, "").strip() and sid not in seen:
                errors.append("%s: it has `## %s: %s` but %s has no row for the id %s"
                              % (p.parent.relative_to(lib.dir).as_posix(), APPROVED_KEY, h[APPROVED_KEY].strip(),
                                 APPROVED_CSV, sid))
    # o10 W239 pins (see APPROVED_PINNED): a record that matches a pin by id, by slug or by approved-text hash must
    # still agree with every field the pin names, and its CSV row must still exist.
    row_by_id = {r["script_id"].strip(): r for r in crows}
    text_hashes = {}

    def text_hash(path, h):
        if path not in text_hashes:
            try:
                data = path.read_bytes()
                text_hashes[path] = guard_hash(data, guard_is_live(h, data))
            except (LibraryError, OSError):
                text_hashes[path] = None
        return text_hashes[path]

    unmatched = []
    for sid, slug, by, sha in APPROVED_PINNED:
        row = row_by_id.get(sid)
        shas = {sha, row["text_sha256"].strip() if row else sha} - {"", UNKNOWN, UNKNOWN.lower()}
        matched = False
        for p, h, _body, _err in rows:
            if not h:
                continue
            rid = h.get("ID", "").strip()
            id_hit = rid == sid
            by_slug = slug != "none" and slug in [h.get("Slug", "").strip()] + aliases_of(h)
            got = text_hash(p, h)
            by_hash = got is not None and got in shas
            if not (id_hit or by_slug or by_hash):
                continue
            matched = True
            if (by_hash and got != sha and sha.upper() != UNKNOWN
                    and notes_only_text_for(p.parent, p, sha) is None):
                # o10L388 round 3 (S-A): the CSV row's hash is accepted, so a ruling does not break the pin; the pin's
                # own hash then needs a reviewed change, and `check` says so on every run until it is made.
                # o10L452: not when a text in the record's folder still hashes to the pin and has the same spoken lines
                # once notes, comments and pause cues are removed (the notes-only rule of `approve`, o10 W245): the
                # approved words are the pinned words, so the pin needs no edit.
                notes.append("%s: the approved text differs from the text pinned for %s in APPROVED_PINNED (it matches "
                             "its %s row; rulings: %s); move the pin's hash in a reviewed change"
                             % (p.parent.relative_to(lib.dir).as_posix(), sid, APPROVED_CSV,
                                (row["rulings"].strip() if row else "") or "none"))
            moved = []
            if not id_hit:
                moved.append("the id (now %s)" % (rid or "empty"))
            if slug != "none" and not by_slug:
                moved.append("the slug (the pin holds %s)" % slug)
            if h.get(APPROVED_KEY, "").strip() != by and not (row is not None and ruling_approval_matches(h.get(APPROVED_KEY, ""), row)):
                moved.append("the `## %s:` line" % APPROVED_KEY)
            if shas and got not in shas:
                moved.append("the approved text's hash")
            if sid not in seen:
                moved.append("its %s row" % APPROVED_CSV)
            if moved:
                errors.append("%s: it matches the script pinned as the human-approved under the id %s (APPROVED_PINNED in "
                              "file_script.py), but these no longer agree with the pin: %s; restore them; only the human's "
                              "ruling and a reviewed change to APPROVED_PINNED may change them"
                              % (p.parent.relative_to(lib.dir).as_posix(), sid, ", ".join(moved)))
        if not matched:
            unmatched.append(sid)
    # o10L388 round 3 (S-B): once any pin finds its record, a pin that finds none is an error: its record had its id,
    # its slug and its text all changed, or was deleted. A library that holds none of the pinned scripts (a test
    # library) is skipped. o10L388 round 4 (S-1): the workspace's own script-library (decided by
    # is_workspace_library, which compares the folders with os.path.samefile) is never skipped, so changing all the
    # pinned records at once still fails.
    if unmatched and (is_workspace_library(lib.dir) or len(unmatched) < len(APPROVED_PINNED)):
        for sid in unmatched:
            errors.append("the script pinned as the human-approved under the id %s (APPROVED_PINNED in file_script.py) matches "
                          "no record in the library by id, slug or approved text; restore it, or change the pin in a "
                          "reviewed change with the human's ruling" % sid)
    return errors, notes


def build_files(lib):
    """Check, then build every import file in memory. Returns ({import file: bytes}, [(import file, script count,
    status against the baseline hash)], number of files that differ from the baseline)."""
    refuse_if_check_fails(lib, "build")
    manifest = load_json(lib.build_dir / "MANIFEST.json", {"files": {}})["files"]
    live = collect_live_sources(lib)
    problems = 0
    results = []
    files = {}
    for rel in sorted(set(live) | set(manifest)):
        items = sorted(live.get(rel, []), key=lambda t: t[0])
        check_live_positions(rel, items)
        side = lib.build_dir / (rel + ".preamble.md")
        if not side.exists():
            raise LibraryError("%s: preamble sidecar missing: %s" % (rel, side))
        data = render_import_file(lib, side.read_bytes(), items)
        files[rel] = data
        want = manifest.get(rel, {}).get("sha256")
        got = hashlib.sha256(data).hexdigest()
        status = "no baseline" if want is None else ("matches baseline" if want == got else "DIFFERS from baseline")
        if want is not None and want != got:
            problems += 1
        results.append((rel, len(kept_items(lib, items)), status))
    return files, results, problems


def cmd_rebaseline(lib, apply=False):
    """One-time move of the baseline hashes in _build/MANIFEST.json to the 3-final-only build (o10 W244). For every import file
    whose built bytes differ from its baseline, it checks that the file with EVERY live script in it (the old rule, withheld
    ones included) still equals the old baseline: then the only difference is the withheld scripts, and the baseline is
    moved to the new bytes. It also accepts a file whose build with the `<!-- opening: ... -->` markers left out equals the
    old baseline (o10L598: the only difference is the markers added to SC blocks). Any other difference is refused and nothing is written. A dry run (apply False) writes nothing.
    Returns [(import file, kept scripts, withheld scripts, old sha256, new sha256)]."""
    refuse_if_check_fails(lib, "rebaseline")
    doc = load_json(lib.build_dir / "MANIFEST.json", {"files": {}})
    manifest = doc["files"]
    live = collect_live_sources(lib)
    changes, new_entries = [], {}
    for rel in sorted(set(live) | set(manifest)):
        mf = manifest.get(rel)
        if mf is None:
            raise LibraryError("%s is built but has no baseline in _build/MANIFEST.json; rebaseline does not add files" % rel)
        items = sorted(live.get(rel, []), key=lambda t: t[0])
        check_live_positions(rel, items, "the live scripts of it")
        if len(items) != mf.get("scripts"):
            raise LibraryError("%s: the library has %d live scripts there but the baseline says %d" % (rel, len(items), mf.get("scripts")))
        side = lib.build_dir / (rel + ".preamble.md")
        if not side.exists():
            raise LibraryError("%s: preamble sidecar missing: %s" % (rel, side))
        new = render_import_file(lib, side.read_bytes(), items)
        old_sha, new_sha = mf.get("sha256"), hashlib.sha256(new).hexdigest()
        if new_sha == old_sha:
            continue
        everything = assemble(side.read_bytes(), [t[2] for t in items])
        # o10L598 (from the o10L591 review): the SC opening markers `build` now writes are the other difference accepted: the
        # same build with the markers left out must equal the old baseline, so the markers are all that was added.
        without_markers = render_import_file(lib, side.read_bytes(), items, markers=False)
        if (hashlib.sha256(everything).hexdigest() != old_sha
                and hashlib.sha256(without_markers).hexdigest() != old_sha):
            raise LibraryError("%s: the file differs from its baseline even with every live script in it, and even with the "
                               "opening markers left out, so the difference is not the withheld scripts or the opening "
                               "markers. Nothing was written." % rel)
        kept = len(kept_items(lib, items))
        entry = dict(mf)
        entry.update({"sha256": new_sha, "bytes": len(new)})
        new_entries[rel] = entry
        changes.append((rel, kept, len(items) - kept, old_sha, new_sha))
    if apply and new_entries:
        doc["files"].update(new_entries)
        save_json(lib.build_dir / "MANIFEST.json", doc)
    return changes


def cmd_build(lib, out, strict):
    """Check, build every import file into a temporary folder, and copy them into `out` only when all are built. Also writes
    WITHHELD-SLUGS.txt beside them (the slugs of the live scripts that are not in 3-final)."""
    out = Path(out)
    files, results, problems = build_files(lib)
    withheld = render_withheld_list(withheld_slugs(lib))
    with tempfile.TemporaryDirectory() as tmp:
        for rel, data in files.items():
            dest = Path(tmp) / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        for rel in files:
            dest = out / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(str(Path(tmp) / rel), str(dest))
    out.mkdir(parents=True, exist_ok=True)
    (out / WITHHELD_NAME).write_bytes(withheld)
    return results, (problems if strict else 0)


def sha256_lf(data):
    """sha256 of the bytes with LF line endings: the same value on a Windows checkout and in git."""
    return hashlib.sha256(to_lf(data)).hexdigest()


STAMP_NAME = "LIBRARY-BUILD.json"


def workspace_state(lib):
    """(HEAD commit, uncommitted paths under script-library/ and this file) of the workspace repo."""
    def git(*a):
        r = subprocess.run(["git", "-C", str(lib.root)] + list(a), capture_output=True, creationflags=flags())
        if r.returncode != 0:
            raise LibraryError("git %s failed in %s: %s" % (" ".join(a), lib.root, r.stderr.decode("utf-8", "replace").strip()))
        return r.stdout.decode("utf-8", "replace")
    top = git("rev-parse", "--show-toplevel").strip()
    if Path(top).resolve() != Path(lib.root).resolve():
        raise LibraryError("%s is not the top of a git repository (git says %s)" % (lib.root, top))
    head = git("rev-parse", "HEAD").strip()
    root = Path(lib.root).resolve()
    paths = ["script-library"]
    me = Path(__file__).resolve()
    if root in me.parents:
        paths.append(me.relative_to(root).as_posix())
    dirty = [l for l in git("status", "--porcelain", "--untracked-files=all", "--", *paths).split("\n") if l.strip()]
    return head, dirty


# --------------------------------------------------------------------------- the ` (in app)` tag follows the import (o10L594)
#
# the human, 2026-10-22: the "in app" tag comes off any script that is NOT in the app. A script is in the app's import only when
# it is live (`App: live`, or final with a Source) AND in 3-final AND not held (is_kept_path, is_live). One in 1-drafts or
# 2-ready-for-owner is withheld (WITHHELD-SLUGS.txt hides its row) and one that is held or retired is out as well: a file of
# such a script that still says ` (in app)` in its name is stale. Only the file NAME changes; the bytes, the header and the
# folder stay as they are.

IN_APP_TAG = " (in app)"


def stale_app_tags(lib):
    """[(old path, new path, folder)] for every `.md` file whose name ends in ` (in app).md` in the folder (and its
    versions/ folder) of a script that is not in the import. The new path is the name without the tag."""
    out = []
    for p in lib.iter_scripts():
        try:
            h, _ = parse_script(p)
        except LibraryError:
            continue
        if is_live(h) and is_kept_path(lib, p) and not is_held(h):
            continue
        folder = p.parent
        for d in (folder, folder / "versions"):
            if not d.is_dir():
                continue
            for f in sorted(d.iterdir()):
                if f.is_file() and f.name.endswith(IN_APP_TAG + ".md"):
                    out.append((f, f.with_name(f.name[:-len(IN_APP_TAG + ".md")] + ".md"), folder))
    return out


def stale_app_tag_problems(lib):
    """One problem line per script folder that has a file named ` (in app)` although its script is not in the import."""
    by_folder = {}
    for old, _, folder in stale_app_tags(lib):
        by_folder.setdefault(folder, []).append(old.name)
    return ["%s: %s named ` (in app)` but the script is not in the import (stage, held or retired); run "
            "`file_script.py drop-app-tags --apply`" % (f.relative_to(lib.dir).as_posix(), ", ".join(names))
            for f, names in sorted(by_folder.items())]


def cmd_drop_app_tags(lib, apply=False, use_git=True, folder=None):
    """Rename every stale ` (in app)` file to the name without the tag (git mv; the bytes do not change). A rename whose
    new name is already taken in the folder is left and returned. Returns (renamed pairs, left [(old, why)])."""
    plan, left, taken = [], [], set()
    for old, new, fold_ in stale_app_tags(lib):
        if folder is not None and Path(fold_).resolve() != Path(folder).resolve():
            continue
        key = os.path.normcase(str(new))
        if new.exists() or key in taken:
            left.append((old, "a file named %s is already there" % new.name))
        else:
            taken.add(key)
            plan.append((old, new))
    if apply and plan:
        saved = save_registries(lib)
        done = []
        try:
            done = apply_label_renames(lib, plan, use_git=use_git)
            update_path_registries(lib, [(o, n) for o, n in done])
        except BaseException:
            restore_registries(saved)
            undo_label_renames(lib, done, use_git)
            raise
    return plan, left


def cmd_build_app(lib, app):
    """Write the import files into <app>/voice_scripts/Working and the stamp <app>/voice_scripts/LIBRARY-BUILD.json.
    Strict: refuses when a built file differs from the baseline hash. Refuses a workspace tree with uncommitted
    changes in script-library/ or in this file, so the stamp always names a commit that produced the files, and an
    app folder that holds an import file the library does not build. Returns (stamp dict, number of files)."""
    app = Path(app)
    work = app / "voice_scripts" / "Working"
    if not work.is_dir():
        raise LibraryError("%s has no voice_scripts/Working folder; give the app repo's top folder" % app)
    top = subprocess.run(["git", "-C", str(app), "rev-parse", "--show-toplevel"], capture_output=True,
                         creationflags=flags())
    top_dir = top.stdout.decode("utf-8", "replace").strip() if top.returncode == 0 else ""
    if not top_dir or Path(top_dir).resolve() != app.resolve():
        raise LibraryError("%s is not the top of a git repository (git says %s); a plain folder inside another repo "
                           "is refused, because git would check that repo's changes instead. Give the app repo's top "
                           "folder; nothing was written" % (app, top_dir or "no repository"))
    head, dirty = workspace_state(lib)
    if dirty:
        raise LibraryError("the workspace tree has uncommitted changes in script-library/ or file_script.py (%d, for "
                           "example %s); commit them first so the stamp names the commit that built the files"
                           % (len(dirty), dirty[0].strip()))
    r = subprocess.run(["git", "-C", str(app), "status", "--porcelain", "--untracked-files=all", "--", "voice_scripts"],
                       capture_output=True, creationflags=flags())
    if r.returncode != 0:
        raise LibraryError("git status failed in %s, so uncommitted edits in its voice_scripts/ cannot be ruled out: %s"
                           % (app, r.stderr.decode("utf-8", "replace").strip()))
    app_dirty = [l for l in r.stdout.decode("utf-8", "replace").split("\n") if l.strip()]
    if app_dirty:
        raise LibraryError("the app has uncommitted changes in voice_scripts/ (%d, for example %s); build --app would "
                           "overwrite them. Commit or discard them, or start from a fresh app worktree; nothing was "
                           "written" % (len(app_dirty), app_dirty[0].strip()))
    stale_ = stale_app_tag_problems(lib)
    if stale_:
        raise LibraryError("%d script folder(s) hold a file named ` (in app)` although the script is not in the import; "
                           "nothing was written. First: %s" % (len(stale_), stale_[0]))
    files, results, problems = build_files(lib)
    if problems:
        raise LibraryError("%d import file(s) differ from the baseline hash in _build/MANIFEST.json; --app writes "
                           "nothing until build --strict passes" % problems)
    retired = retired_slug_list(lib)
    retired_bytes = render_retired_list(retired)
    withheld = withheld_slugs(lib)
    withheld_bytes = render_withheld_list(withheld)
    have = set(collect_import_files(work))
    # an import file the last build wrote and the library no longer builds (its last script moved or was retired)
    # is removed; any other file the library does not build is refused
    old_stamp = load_json(app / "voice_scripts" / STAMP_NAME, None)
    old_files = old_stamp.get("files", {}) if isinstance(old_stamp, dict) else {}
    extra = sorted(have - set(files) - set(old_files))
    if extra:
        raise LibraryError("the app has import files the library does not build: %s; nothing was written" % extra)
    stale = sorted(have - set(files))
    stamp = {
        "note": "Written by .shared/scripts/file_script.py build --app. The import files in voice_scripts/Working are "
                "built from script-library/3-final in the workspace repo at workspace_commit; sha256 is of each file's "
                "bytes with LF line endings. Do not edit by hand: change the library and build again.",
        "workspace_commit": head,
        "files": {rel: {"scripts": n, "sha256": sha256_lf(files[rel])} for rel, n, _ in results},
        "retired_slugs": {"count": len(retired), "sha256": sha256_lf(retired_bytes)},
        "withheld_slugs": {"count": len(withheld), "sha256": sha256_lf(withheld_bytes)},
    }
    for rel, data in files.items():
        dest = work / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    for rel in stale:
        (work / rel).unlink()
    (app / "voice_scripts" / RETIRED_NAME).write_bytes(retired_bytes)
    (work / WITHHELD_NAME).write_bytes(withheld_bytes)
    save_json(app / "voice_scripts" / STAMP_NAME, stamp)
    return stamp, len(files)


def cmd_sync_bodies(lib, source, accept_app_edit=False, notes=None, ruling=None):
    """Recovery tool. The library is the source of the import files (build writes them), so copying text from the
    app into the library runs the wrong way; it is allowed only with accept_app_edit (--accept-app-edit), for the
    case where someone decides to keep text that was changed in the app by hand. Without it, nothing is read.

    Pull body-text changes for live scripts from the app's import files (a GitSource at a commit,
    or a plain folder). Only the bodies that differ are rewritten, plus the baseline hash of their
    import file. Refuses, and writes nothing, when anything beyond body text changed: the set of
    import files, an import file's preamble, its script count, or a script's heading line or slug
    (those are moves, retags and retirements, which belong to the switch-over or to file --type).
    Every header line that is not a `## Key:` field (the `# title` and the Authored line) stays as it was. notes: a
    list that receives one line per synced script saying what happened to its Authored line.
    Returns [(import file, position, label, title)] for the scripts that changed."""
    if not accept_app_edit:
        raise LibraryError("sync-bodies copies text from the app INTO the library, the reverse of the intended "
                           "direction (the library builds the app's import files). Nothing was changed. If the "
                           "text edited in the app is the text to keep, run it again with --accept-app-edit; "
                           "to change a live script's text from the library side use `revise`.")
    manifest_doc = load_json(lib.build_dir / "MANIFEST.json", {"files": {}})
    manifest = manifest_doc["files"]
    have = sorted(collect_import_files(source))
    if have != sorted(manifest):
        raise LibraryError("the app's import files differ from the library's: only in the app %s, only in the library %s. "
                           "Adding or removing an import file is a move, not a body change."
                           % (sorted(set(have) - set(manifest)), sorted(set(manifest) - set(have))))
    by_src = collect_live_sources(lib)
    changed, new_entries = [], {}
    for rel in have:
        data = source.read(rel)
        preamble, chunks = split_chunks(data)
        side = lib.build_dir / (rel + ".preamble.md")
        if not side.exists():
            raise LibraryError("%s: preamble sidecar missing: %s" % (rel, side))
        if side.read_bytes() != preamble:
            raise LibraryError("%s: the text before the first script (its preamble) changed; that is not a body change" % rel)
        all_items = sorted(by_src.get(rel, []), key=lambda t: t[0])
        check_live_positions(rel, all_items, "the library's live scripts")
        items = kept_items(lib, all_items)
        if len(chunks) != len(items):
            raise LibraryError("%s: the app has %d scripts and the library %d approved ones (in 3-final); a script was "
                               "added, removed or moved. That belongs to the switch-over." % (rel, len(chunks), len(items)))
        file_changed = False
        for k, ((pos, path, body), chunk) in enumerate(zip(items, chunks)):
            if k == len(items) - 1:
                # the last kept script ends the built file, so its separator is dropped there (render_import_file)
                body_cmp = strip_trailing_separator(body)
                if body_cmp == chunk:
                    continue
                if pos != len(all_items):
                    chunk = with_separator(chunk)
            elif body == chunk:
                continue
            h, _ = parse_script(path)
            first_old, first_new = body.split(b"\n", 1)[0], chunk.split(b"\n", 1)[0]
            if first_old != first_new:
                raise LibraryError("%s script %d (%s): its heading line changed (%r -> %r). A retitle or retag is not a "
                                   "body change." % (rel, pos, h.get("Label"), first_old.decode("utf-8", "replace"),
                                                     first_new.decode("utf-8", "replace")))
            if parse_chunk(chunk)[2] != parse_chunk(body)[2]:
                raise LibraryError("%s script %d (%s): its slug marker changed" % (rel, pos, h.get("Label")))
            changed.append((rel, pos, h.get("Label"), h.get("Title"), path, h, chunk))
            file_changed = True
        entry = dict(manifest[rel])
        if file_changed or entry.get("sha256") != hashlib.sha256(data).hexdigest():
            entry["sha256"] = hashlib.sha256(data).hexdigest()
            entry["bytes"] = len(data)
            new_entries[rel] = entry
    # o10 W239: a the human-approved script's text is not pulled from the app without the human's ruling either
    ruling = validate_ruling(ruling)
    rulings = {}
    for rel, pos, label, title, path, h, chunk in changed:
        entry = approval_gate(lib, h, Path(path).read_bytes(), (END_MARK + "\n").encode("utf-8") + chunk,
                              "sync-bodies", ruling)
        if entry:
            rulings[str(path)] = entry
    # every check passed; now write. o10L373 S6: the CSV gets every ruling in one write (a temporary file and a rename),
    # before any record; a failure after it puts the CSV and the records already written back as they were.
    csv_old = None
    if rulings:
        rows = read_approved_csv(lib)
        for rel, pos, label, title, path, h, chunk in changed:
            if str(path) in rulings:
                apply_ruling_to_rows(rows, h, guard_hash_of_body(chunk, True), rulings[str(path)])
        csv_old = (lib.dir / APPROVED_CSV).read_bytes()
        write_approved_csv(lib, render_approved_csv(rows))
    written = []
    manifest_path = lib.build_dir / "MANIFEST.json"
    manifest_old = manifest_path.read_bytes() if manifest_path.exists() else None
    try:
        for rel, pos, label, title, path, h, chunk in changed:
            old = Path(path).read_bytes()
            if str(path) in rulings:
                h = with_ruling(h, rulings[str(path)])
            # keep_from: every header line this command does not own (the `# title` line, the Authored line, notes)
            # stays byte for byte (FM4). An app-side edit has no known model or author, so it never invents an
            # Authored line.
            written.append((path, old))
            write_script(path, h, chunk, keep_from=old)
            if notes is not None:
                had = authored_line_of(old.decode("utf-8"))
                had = had or authored_line_anywhere(old.decode("utf-8"))        # out of the FM4 position (no `# title`)
                notes.append("%s script %d  %s: %s" % (rel, pos, label,
                             "the Authored line was left as it was (%s); the app edit has no known model or author" % had
                             if had else "the file has no Authored line; none was added"))
        if new_entries:
            manifest_doc["files"].update(new_entries)
            save_json(manifest_path, manifest_doc)
    except BaseException:
        for path, old in reversed(written):
            Path(path).write_bytes(old)
        if manifest_old is not None:
            manifest_path.write_bytes(manifest_old)
        if csv_old is not None:
            write_approved_csv(lib, csv_old)
        raise
    return [(rel, pos, label, title) for rel, pos, label, title, _, _, _ in changed]


def read_all(lib):
    rows = []
    for p in lib.iter_scripts():
        try:
            h, body = parse_script(p)
        except LibraryError as e:
            rows.append((p, None, None, str(e)))
            continue
        rows.append((p, h, body, None))
    return rows


def title_matched_problems(lib, tm, found):
    """The four live scripts the importer matches to their database row by (title, voice type, category), not by
    slug (_build/TITLE-MATCHED.json). Any change to the title, the voice type (the tab) or the category makes the
    next import insert a duplicate row, and a slug stamped before the row has one does the same."""
    who = "%s (%s)" % (tm.get("label"), tm.get("title"))
    if found is None:
        return ["title-matched script %s (id %s) is no longer in the library" % (who, tm.get("id"))]
    rel, h, body = found
    out = []
    if h.get("Title") != tm.get("title"):
        out.append("%s: title %r differs from the title %r the app matches its row id=%s by; a changed title inserts "
                   "a duplicate row" % (rel, h.get("Title"), tm.get("title"), tm.get("row_id")))
    if h.get("Category") != tm.get("category") or h.get("Line") != tm.get("line"):
        out.append("%s: category %s/%s differs from %s/%s that its row id=%s is matched by; a changed category "
                   "inserts a duplicate row" % (rel, h.get("Line"), h.get("Category"), tm.get("line"),
                                                 tm.get("category"), tm.get("row_id")))
    voice = {"night": "calm", "day": "energetic"}.get(h.get("Tab"))
    if voice != tm.get("voice_type") or h.get("Tab") != tm.get("tab"):
        out.append("%s: tab %s (voice type %s) differs from %s (%s) that its row id=%s is matched by"
                   % (rel, h.get("Tab"), voice, tm.get("tab"), tm.get("voice_type"), tm.get("row_id")))
    if h.get("Slug", "").strip().lower() != "none":
        out.append("%s: Slug is %r but this script has no slug by design until its row has one; a slug stamped first "
                   "makes the next import insert a duplicate row" % (rel, h.get("Slug")))
    want = "> NO SLUG BY DESIGN: matches live row id=%s " % tm.get("row_id")
    if not any(l.decode("utf-8", "replace").startswith(want) for l in split_lines_keep(body)[:6]):
        out.append("%s: the body has no `%s...` line near its top" % (rel, want.strip()))
    return out


def cmd_check(lib, write=True, require_app_line=False, require_new_layout=False):
    """require_app_line: a final script with a Source but no `## App: live` line is a problem (off while the 436
    live scripts do not have the line yet). require_new_layout: a draft or ready script must sit in the
    <stage>/<line>/<tab>/<category>/ tree or in <stage>/not-yet-placed/<TYPE>/; the older <stage>/<TYPE>/ folders
    are accepted while it is off."""
    errors = []
    rows = read_all(lib)
    manifest = load_json(lib.build_dir / "MANIFEST.json", {"files": {}})["files"]
    title_matched = load_json(lib.build_dir / "TITLE-MATCHED.json", {"scripts": []})["scripts"]
    try:
        retired_slugs = set(retired_slug_list(lib))
    except LibraryError as e:
        errors.append("%s (the app's import refuses a damaged list)" % e)
        retired_slugs = set()
    by_id = {}
    positions_by_src = {}
    reg_lines = {l["slug"]: l for l in lib.lines()["lines"]}
    codes = {}
    for l in lib.lines()["lines"]:
        if l["code"].lower() in codes:
            errors.append("line code clash: %s used by %s and %s" % (l["code"], codes[l["code"].lower()], l["slug"]))
        codes[l["code"].lower()] = l["slug"]
    seen_labels, seen_ids, seen_slugs = {}, {}, {}
    index = []
    for p, _, _, _ in rows:
        if len(record_files(p.parent)) > 1:
            errors.append("more than one record file in %s: %s (a script folder has exactly one)"
                          % (p.parent.relative_to(lib.dir).as_posix(),
                             ", ".join(q.name for q in record_files(p.parent))))
    for p, h, body, err in rows:
        rel = p.parent.relative_to(lib.dir).as_posix()
        if err:
            errors.append("unreadable header: %s" % err)
            continue
        label = h.get("Label", "").strip()
        for need in ("Title", "Type", "Stage", "ID"):
            if not h.get(need, "").strip():
                errors.append("%s: header has no %s" % (rel, need))
        if h.get("Type") not in ("AT", "SC"):
            errors.append("%s: Type must be AT or SC, got %r" % (rel, h.get("Type")))
        stage = h.get("Stage", "")
        if stage not in STAGES:
            errors.append("%s: unknown Stage %r" % (rel, stage))
            continue
        top = rel.split("/")[0]
        if top != STAGES[stage]:
            errors.append("misfiled: %s sits in %s but its header says stage %s" % (rel, top, stage))
        held = is_held(h)
        if h.get("Release", "").strip() and h.get("Release", "").strip().lower() != "held":
            errors.append("%s: Release must be `held` or absent, got %r" % (rel, h.get("Release")))
        if held and stage != "final":
            errors.append("%s: Release: held on a script whose stage is %s; only a final script can be held" % (rel, stage))
        app_val = h.get("App", "").strip().lower()
        live = is_live(h)
        if app_val and app_val != "live":
            errors.append("%s: App must be `live` or absent, got %r" % (rel, h.get("App")))
        if app_val == "live":
            why = None
            if stage == "retired":
                why = "its stage is retired"
            elif held:
                why = "it is Release: held"
            elif not h.get("Source"):
                why = "it has no Source"
            elif not h.get("Source-position", "").strip():
                why = "it has no Source-position"
            if why:
                errors.append("%s: App: live but %s; a script is live only with a Source and a Source-position, "
                              "not held and not retired" % (rel, why))
        elif live and require_app_line:
            errors.append("%s: it has a Source and is not held, so it is in the app, but it has no `## App: live` line" % rel)
        if stage == "final":
            if held and h.get("Source"):
                errors.append("%s: Release: held but it has Source %s; a held script is not in an import file"
                              % (rel, h["Source"]))
            if not held and not h.get("Source"):
                errors.append("%s: final but neither released nor held (no Source and no `Release: held`)" % rel)
        line = h.get("Line", "")
        if line and line not in reg_lines:
            errors.append("line without a code: %s (%s) is not in LINES.json" % (line, rel))
        if stage == "final" or live or h.get("Category"):
            if not line:
                errors.append("%s: header has a category but no Line" % rel)
            if h.get("Tab") not in TABS:
                errors.append("%s: Tab must be night or day, got %r" % (rel, h.get("Tab")))
            elif line in reg_lines and not lib.category_by_folder(line, h["Tab"], h.get("Category", "")):
                errors.append("%s: category %r is not in CATEGORIES.json for %s/%s" % (rel, h.get("Category"), line, h["Tab"]))
        if (stage == "final" or live) and not label:
            errors.append("%s: a %s script has no Label" % (rel, "final" if stage == "final" else "live"))
        if label:
            wrong = label_name_problems(p.parent, label, old_names_of(h, record_name(p)))
            if wrong:
                errors.append("%s: %d text(s) are not named by the Label %s (for example %r); run `rename-to-label` "
                              "(W238: a labelled script's file names are its Label)" % (rel, len(wrong), label, wrong[0]))
            pre = label_prefix(label)
            if not pre:
                errors.append("%s: Label %r is not TYPE-LINECODE-N|D-CC-NNN" % (rel, label))
            else:
                lcode = reg_lines.get(line, {}).get("code")
                want = (h.get("Type"), lcode, TABS.get(h.get("Tab")), h.get("Category", "")[:2])
                if pre != want:
                    errors.append("%s: Label %s disagrees with the header fields (type/line/tab/category)" % (rel, label))
        for lab in ([label] if label else []) + aliases_of(h):
            if lab in seen_labels and seen_labels[lab] != rel:
                errors.append("label clash: %s is on %s and on %s" % (lab, seen_labels[lab], rel))
            seen_labels.setdefault(lab, rel)
        sid = h.get("ID", "").strip()
        if sid:
            if sid in seen_ids and seen_ids[sid] != rel:
                errors.append("id clash: %s is on %s and on %s" % (sid, seen_ids[sid], rel))
            seen_ids.setdefault(sid, rel)
        slug = h.get("Slug", "").strip()
        if slug and slug.lower() != "none":
            if slug in seen_slugs and seen_slugs[slug] != rel:
                errors.append("slug clash: %s is on %s and on %s" % (slug, seen_slugs[slug], rel))
            seen_slugs.setdefault(slug, rel)
            if slug in retired_slugs and stage != "retired":
                errors.append("%s: Slug %s is in RETIRED-SLUGS.json (a slug that must never be given again)" % (rel, slug))
        by_id.setdefault(sid, (rel, h, body))
        try:
            want_dir = expected_rel_dir(h)
            ok_dirs = {want_dir}
            if not require_new_layout and stage in ("draft", "ready"):
                ok_dirs.add(legacy_rel_dir(h))      # the folders drafts and ready scripts have today
            if rel not in ok_dirs:
                errors.append("misfiled: %s should be in %s" % (rel, want_dir))
        except LibraryError as e:
            errors.append("%s: %s" % (rel, e))
        # the body of an import-file script must agree with the header
        if h.get("Source"):
            first = body.split(b"\n", 1)[0].decode("utf-8", "replace").rstrip()
            if not SCRIPT_HDR.match(first):
                errors.append("%s: body does not start with a '## Script N' header" % rel)
            else:
                title, tag, bslug = parse_chunk(body)
                if tag != "%s-%s" % (h.get("Type"), h.get("Tab", "").upper()):
                    errors.append("%s: body tag [%s] disagrees with Type/Tab" % (rel, tag))
                hs = None if slug.lower() in ("", "none") else slug
                if bslug != hs:
                    errors.append("%s: header Slug %r disagrees with the slug marker in the body %r" % (rel, slug, bslug))
                if title != h.get("Title"):
                    errors.append("%s: header Title disagrees with the body title" % rel)
            if not h.get("Source-position", "").isdigit():
                errors.append("%s: Source-position is not a number" % rel)
            else:
                nm = re.match(r"^##\s+Script\s+(\d+)", first)
                if nm and int(nm.group(1)) != int(h["Source-position"]):
                    errors.append("%s: the body's heading says Script %s but Source-position is %s"
                                  % (rel, nm.group(1), h["Source-position"]))
        # what the import file the script came from says about it
        in_app = "held" if held else False
        if h.get("Source"):
            mf = manifest.get(h["Source"])
            if mf is None:
                errors.append("%s: Source %r is not an import file listed in _build/MANIFEST.json" % (rel, h["Source"]))
            else:
                same_cat = (line, h.get("Tab"), h.get("Category")) == (mf.get("line"), mf.get("tab"), mf.get("category"))
                if not same_cat:
                    errors.append("%s: its folder category (%s/%s/%s) disagrees with its source file's category "
                                  "(%s/%s/%s in %s); build would still put it in the old file"
                                  % (rel, line, h.get("Tab"), h.get("Category"),
                                     mf.get("line"), mf.get("tab"), mf.get("category"), h["Source"]))
                if not live and not held:
                    errors.append("%s: stage is %s but it still has Source %s and no `## App: live` line; build leaves it "
                                  "out and the import file loses a script" % (rel, stage, h["Source"]))
                in_app = same_cat and live
            if live and h.get("Source-position", "").isdigit():
                positions_by_src.setdefault(h["Source"], []).append(int(h["Source-position"]))
        index.append((stage, line, h.get("Tab", ""), h.get("Category", ""), label, h.get("Type", ""),
                      slug or "none", sid, in_app, rel, h.get("Title", "")))
    for tm in title_matched:
        errors.extend(title_matched_problems(lib, tm, by_id.get(tm.get("id"))))
    for src, mf in sorted(manifest.items()):
        got = sorted(positions_by_src.get(src, []))
        want_pos = list(range(1, mf.get("scripts", 0) + 1))
        if got != want_pos:
            missing = sorted(set(want_pos) - set(got))
            extra = sorted(set(got) - set(want_pos))
            dup = sorted({x for x in got if got.count(x) > 1})
            errors.append("gap in source positions for %s: expected 1..%d; missing %s, unexpected %s, repeated %s"
                          % (src, mf.get("scripts", 0), missing, extra, dup))
    errors.extend(approved_report(lib, rows)[0])     # o10 W239: the human-approved text, the CSV and the headers
    errors.extend(orphan_final_folders(lib))
    errors.extend(damaged_record_files(lib))
    errors.extend(frozen_origin_problems(lib))
    if write:
        write_index(lib, index)
    return errors, len(index)


def orphan_final_folders(lib):
    """Folders under 3-final/ that hold files but have no record file in them or in a folder above them
    (inside 3-final/). Such files are invisible to `check`, which reads only record files: this is what
    is left behind when a final's folder is renamed (for example by a retag) and a version file is filed
    under the old name."""
    root = lib.dir / "3-final"
    out = []
    if not root.is_dir():
        return out
    for dirpath, dirnames, filenames in os.walk(str(root)):
        dirnames.sort()
        d = Path(dirpath)
        if d == root or not filenames:
            continue
        probe, owned = d, False
        while probe != root:
            if record_file(probe) is not None:
                owned = True
                break
            probe = probe.parent
        if not owned:
            out.append("orphan files: %s holds %d file(s) but has no record file in it or above it"
                       % (d.relative_to(lib.dir).as_posix(), len(filenames)))
    return out


def frozen_origin_problems(lib):
    """Problems for a row of script-library/LOG-NOTE-ORIGINS.csv (the frozen origin of the 26 process logs and notes) that
    names a file no longer in the library. The table is keyed by file NAME (training_header.library_origin looks a file up by
    it), so renaming one of the 26 files would lose its origin with no message (o10L359 S5). No table, no problem."""
    fp = lib.root / Path(*training_header.FROZEN_ORIGINS_REL.split("/"))
    if not fp.is_file():
        return []
    with open(fp, encoding="utf-8", newline="") as fh:
        names = [row.get("file_name", "") for row in csv.DictReader(fh)]
    present = set()
    for _, _, filenames in os.walk(str(lib.dir)):
        present.update(filenames)
    return ["%s lists %r, and no file of that name is in the library (the file was renamed or removed, so its origin is "
            "lost); rename the row to the new file name" % (training_header.FROZEN_ORIGINS_REL, n)
            for n in names if n and n not in present]


def damaged_record_files(lib):
    """Problems for a record-file name that has no header line (a new-style file whose `<!-- END SCRIPT HEADER -->`
    marker is missing or damaged, for instance by CRLF endings). Without this the folder drops out of iter_scripts,
    `check` and INDEX.md with no message. Reported: a headerless file in the record-file shape in a folder that has no
    record file, and a headerless `(in app)` file anywhere (the text in the app always carries the header). A the human
    rewrite without a header, beside a record file or not, is normal and is not reported. An agent-named text with no
    header at all (no END line) IS reported, and so is one that still shows a `## Stage:`, `## Label:` or `## ID:` line;
    one with the END line and only the pilot's front matter (a lane cut) is a plain text (headerless_record_names)."""
    out = []
    for sd in STAGE_DIRS:
        base = lib.dir / sd
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(str(base)):
            dirnames[:] = sorted(d for d in dirnames if d != ".edit-history")
            # W235 step 7: SCRIPT.md is not a library file name any more and nothing reads one, so one left (or added by an
            # old habit) would be invisible to every command; say so
            if "SCRIPT.md" in filenames:
                out.append("%s is named SCRIPT.md, which is no longer a library file name and is not read; rename it to "
                           "`<NAME> v<N> <AUTHOR>.md`" % (Path(dirpath) / "SCRIPT.md").relative_to(lib.dir).as_posix())
            if os.path.basename(dirpath) in NOT_SCRIPT_DIRS:
                continue
            bare = headerless_record_names(dirpath)
            if not bare:
                continue
            has_record = bool(record_files(dirpath))
            for p, in_app in bare:
                if has_record and not in_app:
                    continue
                out.append("%s has the name of a record file but no `%s` line (a damaged header? CRLF line endings?), "
                           "so the script is not read: %s"
                           % (p.relative_to(lib.dir).as_posix(), END_MARK,
                              "a folder has exactly one record file, and this folder has none" if not has_record
                              else "the text that is in the app must carry the header"))
    return out


def write_index(lib, index):
    order = {s: i for i, s in enumerate(["draft", "ready", "final", "retired"])}
    def nnn(label):
        m = LABEL_RE.match(label or "")
        return int(m.group(5)) if m else 10 ** 6

    # within a category the order is the script number (NNN), AT and SC mixed; then the label
    index = sorted(index, key=lambda r: (order.get(r[0], 9), r[1], r[2], r[3], nnn(r[4]), r[4], r[9]))
    out = ["# Script library index", "",
           "Generated by `.shared/scripts/file_script.py check`. Never edit by hand.", "",
           "%d scripts. \"In app\" is yes only when the script is built into the import file for the category it is listed under; held means it is filed as final but not released to the app yet." % len(index), "",
           "| Label | Title | Type | Line | Tab | Category | Stage | App slug | Permanent ID | In app | Folder |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for (stage, line, tab, cat, label, type_, slug, sid, in_app, rel, title) in index:
        out.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | `%s` |" % (
            label or "(none yet)", title.replace("|", "\\|"), type_, line or "-", tab or "-", cat or "-", stage, slug, sid,
            "held" if in_app == "held" else ("yes" if in_app else "no"), rel))
    lib.dir.mkdir(parents=True, exist_ok=True)
    (lib.dir / "INDEX.md").write_bytes(("\n".join(out) + "\n").encode("utf-8"))


GIT_MV_ERRORS = []      # git's own message for each failed git mv, newest last (o10L461: the refusal names it)


def git_mv(lib, src, dst):
    """git mv with both paths made absolute first. `git -C <root> mv` reads a relative path against the
    root, not against the folder the caller is in, so a relative path from another folder used to fail
    (o10L261 D2)."""
    r = subprocess.run(["git", "-C", str(lib.root), "mv", str(Path(src).resolve()), str(Path(dst).resolve())],
                       capture_output=True, text=True, creationflags=flags())
    if r.returncode != 0:
        GIT_MV_ERRORS.append((r.stderr or r.stdout or "").strip())
    return r.returncode == 0


def drop_deleted_from_index(lib, folder):
    """Stage the deletion of every file git lists under `folder` that is no longer on disk, and return their paths.
    o10L461: `git mv` of a folder refuses with "fatal: bad source" when a tracked file in it is already gone. A command
    that writes files with plain file calls before the folder moves (the revise inside approve writes the old record into
    versions/ and deletes it) leaves exactly that, so approve in git mode failed on every script whose approval needed
    a revise. Only the index changes: the files on disk are those the plain calls left, as with --no-git."""
    r = subprocess.run(["git", "-C", str(lib.root), "ls-files", "-z", "--full-name", "--", str(Path(folder).resolve())],
                       capture_output=True, creationflags=flags())
    if r.returncode != 0:
        return []
    root = Path(os.path.abspath(str(lib.root)))
    gone = [n for n in r.stdout.decode("utf-8", "replace").split("\0") if n and not (root / n).exists()]
    for i in range(0, len(gone), 100):
        q = subprocess.run(["git", "-C", str(lib.root), "rm", "-q", "--cached", "--ignore-unmatch", "--"] + gone[i:i + 100],
                           capture_output=True, text=True, creationflags=flags())
        if q.returncode != 0:
            raise LibraryError("git rm --cached failed for %d file(s) under %s: %s; nothing was moved"
                               % (len(gone), folder, (q.stderr or "").strip()))
    return gone


def is_tracked(lib, path):
    """True when git tracks the file, or any file under the folder. False in a folder that is not a git repo."""
    r = subprocess.run(["git", "-C", str(lib.root), "ls-files", "--error-unmatch", "--", str(Path(path).resolve())],
                       capture_output=True, text=True, creationflags=flags())
    return r.returncode == 0


def move_path(lib, src, dst, use_git=True, untracked_ok=True):
    """Move src to dst. A tracked path goes with git mv, and when git mv fails the move is refused: a plain
    move would leave git seeing a deleted file and an untracked one. A path git does not track (or any path
    with use_git off) is moved plainly."""
    src, dst = Path(src).resolve(), Path(dst).resolve()
    if use_git and is_tracked(lib, src):
        if src.is_dir() and drop_deleted_from_index(lib, src) and not is_tracked(lib, src):
            # o10L461: a tracked file already deleted by a plain call stops a folder git mv. When it was the only tracked
            # file, git tracks nothing in the folder any more and refuses it ("source directory is empty"): a plain move.
            shutil.move(str(src), str(dst))
            return
        mark = len(GIT_MV_ERRORS)
        if not git_mv(lib, src, dst):
            why = "; ".join(e for e in GIT_MV_ERRORS[mark:] if e)
            raise LibraryError("git mv failed for %s -> %s%s; nothing was moved" % (src, dst, " (%s)" % why if why else ""))
        return
    if use_git and not untracked_ok:
        raise LibraryError("git mv failed for %s (git does not track it)" % src)
    shutil.move(str(src), str(dst))


# --------------------------------------------------------------------------- file names follow the Label (W238)

WAYPOINT_BASELINE = ".shared/scripts/check_checklist_router_waypoint_baseline.json"
# Every move rewrites the waypoint baseline (outside script-library/), `approve` included (o10 W244 ruling 3). Each write
# appends its number of rewritten entries here, so `approve` can say in its reason how many it rewrote.
WAYPOINT_WRITES = []
LOG_NOTE_ORIGINS = "LOG-NOTE-ORIGINS.csv"
LONG_PATH_LIMIT = 200


def old_names_of(header, pn):
    """The NAMEs the texts of a script may still carry from before it had its Label (W238): the record file's own name key,
    `<TYPE>-<ID>`, its `## Version-Of:` line and its older Labels (`## Aliases:`). A text named by anything else is not
    this tool's to rename."""
    names = {pn.name_key, (header.get("Version-Of") or "").strip(),
             "%s-%s" % (header.get("Type", "").strip(), header.get("ID", "").strip())}
    names.update(aliases_of(header))
    names.discard("")
    return names


def retitled_name(n, pn, name_key, title=None):
    """The file name `n` (parsed as `pn`) with its NAME set to `name_key` and its title set to `title` (o10L431). title None
    keeps the title the name has; "" takes it off. Everything after the title (` v<N> <AUTHOR>`, ` duplicate`,
    `-PROCESS-LOG`, `-REVIEW`, ` (in app)`, or a note's ` notes - <old name>`) is kept byte for byte."""
    old_t = training_header.script_title(n)
    rest = n[len(pn.name_key) + (1 + len(old_t) if old_t else 0):]
    t = old_t if title is None else title
    return name_key + (" " + t if t else "") + rest


def sidecar_pairs(d, stem, new_stem):
    """[(old, new)] for the paths the Reader and `reader_compare` keep beside the text named `stem` in folder `d` (they are
    keyed by the stem): `<stem>.why-notes.json`, `.edit-history/<stem>/` and `.edit-history/<stem>.why-notes/`; only those that
    exist (review o10L413 S1 for the notes file's own history folder)."""
    d = Path(d)
    out = []
    side = d / (stem + ".why-notes.json")
    if side.is_file():
        out.append((side, d / (new_stem + ".why-notes.json")))
    hist = d / ".edit-history" / stem
    if hist.is_dir():
        out.append((hist, d / ".edit-history" / new_stem))
    nhist = d / ".edit-history" / (stem + ".why-notes")
    if nhist.is_dir():
        out.append((nhist, d / ".edit-history" / (new_stem + ".why-notes")))
    return out


def check_rename_targets(pairs):
    """Raise LibraryError when a new name in `pairs` ([(old, new)]) is already taken, or two paths would get one name."""
    taken = {}
    for old, new in pairs:
        if new.exists():
            raise LibraryError("cannot rename %s to %s: that name is already taken" % (old.name, new.name))
        key = os.path.normcase(str(new))
        if key in taken:
            raise LibraryError("cannot rename %s and %s to one name, %s" % (taken[key].name, old.name, new.name))
        taken[key] = old


def label_rename_plan(folder, label, old_names, title=None):
    """The renames that make every text of a script folder carry the script's Label as its NAME (W238): (pairs, skipped).
    With `title` (o10L431, a title_slug) each name also takes that title; None keeps the titles the names have.

    pairs is [(old Path, new Path)] for each `.md` text directly in the folder and in its versions/ folder whose name key
    is in `old_names` (old_names_of), with the `<stem>.why-notes.json` file and the `.edit-history/<stem>/`
    folder beside it when they exist. Only the leading NAME changes: the version, the author, ` (in app)`,
    ` duplicate`, `-PROCESS-LOG` and a note's tail stay. skipped is [(Path, reason)] for a library-named text with
    any other name key, which is left where it is. Raises LibraryError, planning nothing, when a new name is already taken
    or two files would get one name."""
    folder = Path(folder)
    pairs, skipped = [], []
    for d in (folder, folder / "versions"):
        try:
            names = sorted(os.listdir(str(d)))
        except OSError:
            continue
        for n in names:
            if not n.endswith(".md") or not (d / n).is_file():
                continue
            pn = training_header.parse_script_name(n)
            if pn.version is None and pn.kind != "note":
                continue                                     # not named like a library text
            if pn.name_key != label and pn.name_key not in old_names:
                skipped.append((d / n, "its name key %s is not a name this script has had" % pn.name_key))
                continue
            new_n = retitled_name(n, pn, label, title)
            if new_n == n:
                continue
            pairs.append((d / n, d / new_n))
            pairs += sidecar_pairs(d, n[:-3], new_n[:-3])
    check_rename_targets(pairs)
    return pairs, skipped


def label_name_problems(folder, label, old_names):
    """The names of the texts in a labelled script's folder and versions/ folder whose NAME is an older name of the script
    (old_names_of) and not its Label. A text named by something else is not reported: it is not this script's."""
    bad = []
    folder = Path(folder)
    for d in (folder, folder / "versions"):
        try:
            names = sorted(os.listdir(str(d)))
        except OSError:
            continue
        for n in names:
            if not n.endswith(".md"):
                continue
            pn = training_header.parse_script_name(n)
            if (pn.version is not None or pn.kind == "note") and pn.name_key != label and pn.name_key in old_names:
                bad.append(n)
    return bad


def apply_label_renames(lib, pairs, use_git=True):
    """Move each (old, new) with git mv (a plain move for an untracked path). All or none: a failure moves the ones done
    back and re-raises. Returns the pairs moved."""
    done = []
    try:
        for old, new in pairs:
            new.parent.mkdir(parents=True, exist_ok=True)
            move_path(lib, old, new, use_git=use_git)
            done.append((old, new))
    except BaseException:
        undo_label_renames(lib, done, use_git)
        raise
    return done


def undo_label_renames(lib, done, use_git=True):
    """Move renamed paths back, last first. A path that cannot be moved back is named on stderr."""
    for old, new in reversed(done):
        try:
            if Path(new).exists() and not Path(old).exists():
                move_path(lib, new, old, use_git=use_git)
        except (LibraryError, OSError) as e:
            print("WARNING: could not move %s back to %s: %s" % (new, old, e), file=sys.stderr)


def moved_texts(old_dir, new_dir, pairs):
    """[(old, new)] for every `.md` text under old_dir (the folders `.edit-history` excluded) once the folder moves to
    new_dir and `pairs` (label_rename_plan, with old paths under old_dir) are applied: what the path registries need."""
    old_dir, new_dir = Path(old_dir), Path(new_dir)
    renamed = {os.path.normcase(str(o)): n for o, n in pairs}
    out = []
    for dirpath, dirnames, filenames in os.walk(str(old_dir)):
        dirnames[:] = [d for d in dirnames if d != ".edit-history"]
        for f in filenames:
            if not f.endswith(".md"):
                continue
            old = Path(dirpath) / f
            tgt = renamed.get(os.path.normcase(str(old)), old)
            out.append((old, new_dir / tgt.relative_to(old_dir)))
    return out


def save_registries(lib):
    """The bytes of the two path registries and of the HV6 reuse baseline (a folder move removes rows of a retired reuse id,
    carry_reuse_baseline) (None for one the tree lacks), to put back if a command is undone."""
    paths = (Path(os.path.abspath(str(lib.root))) / WAYPOINT_BASELINE, Path(lib.dir) / LOG_NOTE_ORIGINS,
             Path(os.path.abspath(str(lib.root))) / REUSE_BASELINE)
    return [(q, q.read_bytes() if q.is_file() else None) for q in paths]


def restore_registries(saved):
    for q, data in saved or ():
        if data is not None and q.is_file() and q.read_bytes() != data:
            q.write_bytes(data)


def update_path_registries(lib, moves):
    """Rewrite the two registries that name a library text by path or file name, for `moves` ([(old, new)] of `.md`
    files): the waypoint baseline of check_checklist_router.py (repo-relative paths of version files, which git follows
    only if the baseline names the new path, W235 step 5) and LOG-NOTE-ORIGINS.csv (the file name of a log or note).
    A registry the tree lacks is skipped. Returns (waypoint entries rewritten, csv rows rewritten)."""
    root = Path(os.path.abspath(str(lib.root)))
    rel = []
    for old, new in moves:
        if Path(old) == Path(new):
            continue
        try:
            rel.append((Path(os.path.abspath(str(old))).relative_to(root).as_posix(),
                        Path(os.path.abspath(str(new))).relative_to(root).as_posix(), Path(old).name, Path(new).name))
        except ValueError:
            continue
    nway = ncsv = 0
    wp = root / WAYPOINT_BASELINE
    if rel and wp.is_file():
        text = wp.read_bytes().decode("utf-8")
        for o, n, _, _ in rel:
            for ascii_only in (True, False):
                a, b = json.dumps(o, ensure_ascii=ascii_only), json.dumps(n, ensure_ascii=ascii_only)
                if a in text:
                    nway += text.count(a)
                    text = text.replace(a, b)
        if nway:
            wp.write_bytes(text.encode("utf-8"))
            WAYPOINT_WRITES.append(nway)
    csv_path = Path(lib.dir) / LOG_NOTE_ORIGINS
    names = {}
    for _, _, on, nn in rel:                    # one old name may go to several new names (two scripts' texts of the same name)
        if on != nn and nn not in names.setdefault(on, []):
            names[on].append(nn)
    if names and csv_path.is_file():
        # The table is keyed by file NAME (training_header.library_origin) and holds no folder. A row moves to the new name
        # when no file of the old name is left in the library. When one is (another script's text of the same name), the row
        # may belong to it, so it stays and a copy for the new name is added after it (review o10L413 nit).
        remaining = set()
        for _, _, fns in os.walk(str(lib.dir)):
            remaining.update(f for f in fns if f in names)
        raw = csv_path.read_bytes().decode("utf-8")
        out_lines = []
        for i, ln in enumerate(raw.split("\n")):
            out_lines.append(ln)
            if i == 0 or not ln:
                continue
            row = next(csv.reader([ln.rstrip("\r")]), [""])
            if row and row[0] in names:
                # The table holds no folder, so which new name the row belongs to cannot be told when several texts of one
                # old name moved: the row is copied to every new name (none is lost; the last one no longer wins).
                new_lns = []
                for nn in names[row[0]]:
                    buf = io.StringIO()
                    csv.writer(buf, lineterminator="").writerow([nn] + list(row[1:]))
                    new_lns.append(buf.getvalue() + ("\r" if ln.endswith("\r") else ""))
                if row[0] in remaining:
                    out_lines.extend(new_lns)
                else:
                    out_lines[-1:] = new_lns
                ncsv += 1
        if ncsv:
            csv_path.write_bytes("\n".join(out_lines).encode("utf-8"))
    return nway, ncsv


def new_problems(before, after, moves):
    """The `check` problems in `after` that `before` did not have. A problem that names a renamed file would read as new
    after the rename, so each is compared by its text with every old and new file name of `moves` ([(old, new)] of paths)
    replaced by one placeholder, and by how many times it occurs (review o10L413 nit)."""
    names = sorted({Path(p).name for pair in moves for p in pair}, key=len, reverse=True)
    pat = re.compile("|".join(re.escape(n) for n in names)) if names else None

    def key(e):
        return pat.sub("<text>", e) if pat else e

    budget = {}
    for e in before:
        budget[key(e)] = budget.get(key(e), 0) + 1
    out = []
    for e in after:
        k = key(e)
        if budget.get(k, 0) > 0:
            budget[k] -= 1
        else:
            out.append(e)
    return out


# o10 W245 (review o10L439 section 3): `check` warns about two text files of one script folder that hold the same spoken text
# (spoken_only(guard_segments(.., live=False)), the rule the review measured with), so a new identical pair is seen at once. It is
# a warning, never a problem: `release` and `revise` no longer write one, and the pairs the cleanup left are on purpose. Those are
# listed below by their path under script-library/ (the cleanup keeps a file whose `## Origin:` is PIL, NAT or NATI, because
# check_script.py HV6-REUSE --corpus-at-sc reads versions/ files of exactly those origins, and the human's `v3 THE HUMAN` file named
# in the waypoint baseline); a folder is exempt when ALL its files in a group are listed.
IDENTICAL_TEXT_EXEMPT = frozenset([
    '1-drafts/not-yet-placed/SC/SC-bk1008-haiku55-do-it-now/SC-bk1008-haiku55 Do-It-Now v2 o5.md',
    '1-drafts/not-yet-placed/SC/SC-bk1008-haiku55-do-it-now/versions/SC-bk1008-haiku55 Do-It-Now v1 o5.md',
    '1-drafts/not-yet-placed/SC/SC-bk1008-opus55-do-it-now/SC-bk1008-opus55 Do-It-Now v2 o5.md',
    '1-drafts/not-yet-placed/SC/SC-bk1008-opus55-do-it-now/versions/SC-bk1008-opus55 Do-It-Now v1 o5.md',
    '1-drafts/sleep/day/07-motivation-drive/AT-Sl-D-07-014-one-small-thing-right-now/AT-Sl-D-07-014 One-Small-Thing-Right-Now v3 THE HUMAN.md',
    '1-drafts/sleep/day/07-motivation-drive/AT-Sl-D-07-014-one-small-thing-right-now/AT-Sl-D-07-014 One-Small-Thing-Right-Now v4 o8.md',
    '1-drafts/sleep/day/07-motivation-drive/SC-Sl-D-07-015-do-it-now/SC-Sl-D-07-015 Do-It-Now v2 o5.md',
    '1-drafts/sleep/day/07-motivation-drive/SC-Sl-D-07-015-do-it-now/versions/SC-Sl-D-07-015 Do-It-Now v1 o5.md',
    '1-drafts/sleep/night/12-relationship-co-sleeping/AT-Sl-N-12-011-s11-nighttime-anger/versions/AT-Sl-N-12-011 Releasing-3AM-Anger v2 o8 duplicate.md',
    '1-drafts/sleep/night/12-relationship-co-sleeping/AT-Sl-N-12-011-s11-nighttime-anger/versions/AT-Sl-N-12-011 Releasing-3AM-Anger v2 o8.md',
    '1-drafts/sleep/night/14-nightmares-disturbing-dreams/AT-Sl-N-14-014-s13a-post-nightmare-peak-arousal/versions/AT-Sl-N-14-014 Grounding-After-A-Nightmare v2 o8 duplicate.md',
    '1-drafts/sleep/night/14-nightmares-disturbing-dreams/AT-Sl-N-14-014-s13a-post-nightmare-peak-arousal/versions/AT-Sl-N-14-014 Grounding-After-A-Nightmare v2 o8.md',
    '1-drafts/sleep/night/14-nightmares-disturbing-dreams/AT-Sl-N-14-015-s13b-post-nightmare-settled-reentry/versions/AT-Sl-N-14-015 Settling-Back-In-After-A-Bad-Dream v1 o8 duplicate.md',
    '1-drafts/sleep/night/14-nightmares-disturbing-dreams/AT-Sl-N-14-015-s13b-post-nightmare-settled-reentry/versions/AT-Sl-N-14-015 Settling-Back-In-After-A-Bad-Dream v1 o8.md',
    '1-drafts/sleep/night/14-nightmares-disturbing-dreams/AT-Sl-N-14-016-s14-dream-seeding-joyful-hypnagogic/versions/AT-Sl-N-14-016 Dreaming-Good-Dreams-Now v2 o8 duplicate.md',
    '1-drafts/sleep/night/14-nightmares-disturbing-dreams/AT-Sl-N-14-016-s14-dream-seeding-joyful-hypnagogic/versions/AT-Sl-N-14-016 Dreaming-Good-Dreams-Now v2 o8.md',
    '2-ready-for-owner/sleep/day/07-motivation-drive/AT-Sl-D-07-016-minute-before-i-stand-up/AT-Sl-D-07-016 The-Minute-Before-I-Stand-Up v2 o8.md',
    '2-ready-for-owner/sleep/day/07-motivation-drive/AT-Sl-D-07-016-minute-before-i-stand-up/versions/AT-Sl-D-07-016 The-Minute-Before-I-Stand-Up v1 o8.md',
    '2-ready-for-owner/sleep/day/07-motivation-drive/SC-Sl-D-07-018-what-only-i-can-move/versions/SC-Sl-D-07-018 What-Only-I-Can-Move v1 o8 duplicate.md',
    '2-ready-for-owner/sleep/day/07-motivation-drive/SC-Sl-D-07-018-what-only-i-can-move/versions/SC-Sl-D-07-018 What-Only-I-Can-Move v1 o8.md',
    '2-ready-for-owner/sleep/day/07-motivation-drive/SC-Sl-D-07-019-i-name-it-and-i-go-do-it/SC-Sl-D-07-019 I-Name-It-And-I-Go-Do-It v2 o8.md',
    '2-ready-for-owner/sleep/day/07-motivation-drive/SC-Sl-D-07-019-i-name-it-and-i-go-do-it/versions/SC-Sl-D-07-019 I-Name-It-And-I-Go-Do-It v1 o8.md',
    '3-final/sleep/day/08-performance-confidence/SC-Sl-D-08-001-closer-to-normal-than-you-fear/SC-Sl-D-08-001 Closer-To-Normal-Than-You-Fear v6 THE HUMAN.md',
    '3-final/sleep/day/08-performance-confidence/SC-Sl-D-08-001-closer-to-normal-than-you-fear/versions/SC-Sl-D-08-001 Closer-To-Normal-Than-You-Fear v4 o8.md',
    '3-final/sleep/night/02-middle-of-night-awakenings/AT-Sl-N-02-002-stop-counting-the-hours/AT-Sl-N-02-002 Stop-Counting-The-Hours v4 THE HUMAN.md',
    '3-final/sleep/night/02-middle-of-night-awakenings/AT-Sl-N-02-002-stop-counting-the-hours/AT-Sl-N-02-002 Stop-Counting-The-Hours v5 THE HUMAN (in app).md',
    '3-final/sleep/night/02-middle-of-night-awakenings/AT-Sl-N-02-002-stop-counting-the-hours/versions/AT-Sl-N-02-002 Stop-Counting-The-Hours v5 THE HUMAN.md',
    '3-final/sleep/night/11-identity-limiting-beliefs/SC-Sl-N-11-001-i-am-not-an-insomniac/versions/SC-Sl-N-11-001 I-Am-Naturally-A-Good-Sleeper-NOT-An v3 o8.md',
    '3-final/sleep/night/11-identity-limiting-beliefs/SC-Sl-N-11-001-i-am-not-an-insomniac/versions/SC-Sl-N-11-001 I-Am-Naturally-A-Good-Sleeper-NOT-An v4 o8.md',
])


def identical_text_groups(lib):
    """[(folder relative to script-library, [file names])] for each group of two or more text files (record kinds, in the
    script folder or its versions/) with the same spoken text, groups with every member in IDENTICAL_TEXT_EXEMPT left out."""
    out = []
    seen = set()
    for p in lib.iter_scripts():
        folder = p.parent
        if folder in seen:
            continue
        seen.add(folder)
        groups = {}
        for d in (folder, folder / "versions"):
            try:
                names = sorted(os.listdir(str(d)))
            except OSError:
                continue
            for n in names:
                f = d / n
                pn = training_header.parse_script_name(n)
                if not n.endswith(".md") or pn.version is None or pn.kind not in RECORD_KINDS or not f.is_file():
                    continue
                try:
                    key = hashlib.sha256("\n".join(spoken_only(guard_segments(f.read_bytes(), False))).encode("utf-8")).hexdigest()
                except (LibraryError, OSError, UnicodeError):
                    continue
                groups.setdefault(key, []).append(f)
        for fs in groups.values():
            rels = [f.relative_to(lib.dir).as_posix() for f in fs]
            # o10L463: a file and the cued draft its `## Source-File:` line names (linked_source) are one text by design (the
            # app text built from the draft); files joined by such links count once, and the group warns only when two or
            # more remain, so any other identical pair still warns.
            root = {f: f for f in fs}

            def find(f):
                while root[f] != f:
                    f = root[f]
                return f
            for f in fs:
                try:
                    src = linked_source(folder, _header_of(f.read_bytes()))
                except OSError:
                    src = None
                if src is not None and src != f and src in root:
                    root[find(f)] = find(src)
            if len({find(f) for f in fs}) < 2:
                continue
            if len(fs) > 1 and not all(r in IDENTICAL_TEXT_EXEMPT for r in rels):
                out.append((folder.relative_to(lib.dir).as_posix(), [f.relative_to(folder).as_posix() for f in fs]))
    return out


def long_paths(lib, moves, limit=None):
    """Library files whose repo-relative path is longer than `limit` characters, once `moves` ([(old, new)] of paths) are
    applied: [(length, path)], longest first. A Windows checkout without core.longpaths cannot index such a path
    (W238 design section (d))."""
    limit = LONG_PATH_LIMIT if limit is None else limit
    root = Path(os.path.abspath(str(lib.root)))
    moved = [(os.path.normcase(os.path.abspath(str(o))), os.path.abspath(str(n))) for o, n in moves]
    found = []
    for dp, dn, fns in os.walk(str(lib.dir)):
        for f in fns:
            full = os.path.abspath(os.path.join(dp, f))
            low = os.path.normcase(full)
            for o, n in moved:                      # a file moved by name, or inside a folder that moves (.edit-history)
                if low == o:
                    full = n
                    break
                if low.startswith(o + os.sep):
                    full = os.path.join(n, full[len(o) + 1:])
                    break
            try:
                rel = Path(full).relative_to(root).as_posix()
            except ValueError:
                continue
            if len(rel) > limit:
                found.append((len(rel), rel))
    return sorted(found, reverse=True)


def cmd_rename_to_label(lib, commit=False, use_git=True):
    """Rename the texts of every labelled script to its Label (W238), the way `file --category` now does for one script.
    A pure rename: no byte of any text changes (git records R100), so no ruling is asked, even for a script the human approved;
    `## Version-Of:` stays, because it, not the name, is the family key. A dry run (the default) changes nothing.
    With commit=True the renames are applied with git mv, the two path registries are rewritten, and `check` must report
    nothing it did not report before, or every rename is undone. An approved script whose text has drifted from its
    THE HUMAN-APPROVED.csv row is left unrenamed (the same refusal `file` makes), and so is a script whose new names are taken.
    Returns a dict: scripts [(folder, [(old name, new name)])], left [(folder, reason)], skipped [(path, reason)],
    renamed_approved (scripts with an Approved-By line among them), conformant (labelled scripts already right)."""
    before_errors, _ = cmd_check(lib, write=False)
    report = {"scripts": [], "left": [], "skipped": [], "renamed_approved": 0, "conformant": 0, "labelled": 0,
              "applied": False, "registries": (0, 0), "long_paths": []}
    plans = []
    for p in list(lib.iter_scripts()):
        h, body = parse_script(p)
        label = h.get("Label", "").strip()
        if not label:
            continue
        report["labelled"] += 1
        rel = p.parent.relative_to(lib.dir).as_posix()
        pn = record_name(p)
        try:
            pairs, skipped = label_rename_plan(p.parent, label, old_names_of(h, pn))
        except LibraryError as e:
            report["left"].append((rel, str(e)))
            continue
        report["skipped"] += [(q.relative_to(lib.dir).as_posix(), why) for q, why in skipped]
        if not pairs:
            if not skipped:
                report["conformant"] += 1
            continue
        if h.get(APPROVED_KEY, "").strip():
            try:
                refuse_if_approved_drifted(lib, h, p.read_bytes(), "rename-to-label")
            except LibraryError as e:
                report["left"].append((rel, str(e)))
                continue
            report["renamed_approved"] += 1
        plans.append((rel, p.parent, pairs))
        report["scripts"].append((rel, [(o.name, n.name) for o, n in pairs if o.suffix == ".md"]))
    planned = [(o, n) for _, _, pairs in plans for o, n in pairs]
    report["long_paths"] = long_paths(lib, planned)          # as they will be named, whether or not --commit follows
    if not commit or not plans:
        return report
    done_all, moves = [], []
    saved = save_registries(lib)
    try:
        for rel, folder, pairs in plans:
            done = apply_label_renames(lib, pairs, use_git=use_git)
            done_all += done
            moves += [(o, n) for o, n in done if o.suffix == ".md"]
        report["registries"] = update_path_registries(lib, moves)         # check reads LOG-NOTE-ORIGINS.csv by file name
        after_errors, _ = cmd_check(lib, write=False)
        new = new_problems(before_errors, after_errors, moves)
        if new:
            raise LibraryError("renaming would leave the library with %d new problem(s), so every rename was undone: %s"
                               % (len(new), "; ".join(new[:5])))
    except BaseException:
        undo_label_renames(lib, done_all, use_git)
        restore_registries(saved)
        raise
    cmd_check(lib)                       # INDEX.md follows the new names
    report["applied"] = True
    return report


def script_folders(lib):
    """Every script folder in the four stage folders, in path order: a folder holding a text named like a library text
    (`<NAME>[ <Title>] v<N> <AUTHOR>...md`, not a note) directly in it. The folders `versions` and `.edit-history` are not."""
    found = []
    for sd in STAGE_DIRS:
        base = lib.dir / sd
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(str(base)):
            dirnames[:] = [d for d in dirnames if d not in NOT_SCRIPT_DIRS]
            for n in filenames:
                if n.endswith(".md") and training_header.parse_script_name(n).version is not None:
                    found.append(Path(dirpath))
                    break
    return sorted(found)


def folder_file_title(folder):
    """(title, source) for a script folder: the `## Title:` of its record file, else of its highest-numbered text that has
    one, else the words of the folder's slug. title is a title_slug ("" when none is found); source says which."""
    folder = Path(folder)
    p = record_file(folder)
    texts = [p] if p is not None else []
    if p is None:
        versioned = []
        for n in sorted(os.listdir(str(folder))):
            pn = training_header.parse_script_name(n)
            if n.endswith(".md") and pn.version is not None and pn.kind in RECORD_KINDS:
                versioned.append((-pn.version, n))
        texts = [folder / n for _, n in sorted(versioned)]
    header, key = None, ""
    for q in texts:
        try:
            h, _ = parse_script(q)
        except (LibraryError, OSError, UnicodeError):
            continue
        key = key or training_header.parse_script_name(q.name).name_key
        t = training_header.title_slug(h.get("Title", ""))
        header = header or h
        if t:
            return t, "header"
    if not key:
        for n in sorted(os.listdir(str(folder))):
            pn = training_header.parse_script_name(n)
            if n.endswith(".md") and pn.version is not None:
                key = pn.name_key
                break
    t = folder_slug_title(folder, header, key)
    return t, ("folder" if t else "none")


def title_rename_plan(folder, title, allowed=None):
    """(pairs, skipped): the renames that put `title` into the name of every library-named text in a script folder and its
    versions/ folder, with the sidecar paths (sidecar_pairs): [(old Path, new Path)]. The NAME of each text is not touched,
    so its name key is the same before and after. A text that already carries `title` is left. `allowed`, when given,
    is the set of name keys that are this script's (old_names_of plus its Label); a text named by any other key is
    not this script's, is left as it is and comes back in skipped [(Path, reason)]. Raises LibraryError, planning nothing,
    when a new name is already taken or two paths would get one name."""
    folder = Path(folder)
    pairs, skipped = [], []
    for d in (folder, folder / "versions"):
        try:
            names = sorted(os.listdir(str(d)))
        except OSError:
            continue
        for n in names:
            if not n.endswith(".md") or not (d / n).is_file():
                continue
            pn = training_header.parse_script_name(n)
            if pn.version is None and pn.kind != "note":
                continue                                     # not named like a library text
            if allowed is not None and pn.name_key not in allowed:
                skipped.append((d / n, "its name key %s is not a name this script has had" % pn.name_key))
                continue
            new_n = retitled_name(n, pn, pn.name_key, title)
            if new_n == n:
                continue
            pairs.append((d / n, d / new_n))
            pairs += sidecar_pairs(d, n[:-3], new_n[:-3])
    check_rename_targets(pairs)
    return pairs, skipped


def cmd_rename_to_title(lib, apply=False, use_git=True):
    """Put the script's title into the file name of every text of every script folder (o10L431; the human, 2026-10-11): `SC-Sl-D-01-001
    v1 agent.md` becomes `SC-Sl-D-01-001 High-Achievers-Rest v1 agent.md`. A pure rename, like rename-to-label: no byte of any
    text changes (git records R100), the NAME and so name_key stay as they were, and the `.edit-history/<stem>/`,
    `.edit-history/<stem>.why-notes/` and `<stem>.why-notes.json` paths follow their text. Re-running it after a
    title change sets every name to the new title. A dry run (the default) changes nothing. With apply=True the renames are
    made with git mv (no `git commit`), the two path registries are rewritten, and `check` must report nothing it did not
    report before, or every rename is undone. An approved script whose text has drifted from its THE HUMAN-APPROVED.csv row is
    left alone, as in rename-to-label. Returns a dict: folders [(rel, [(old name, new name)])], left [(rel, reason)],
    conformant (folders already right), notitle [rel] (no title found anywhere), applied, registries, long_paths."""
    before_errors, _ = cmd_check(lib, write=False)
    report = {"folders": [], "left": [], "conformant": 0, "notitle": [], "applied": False, "registries": (0, 0),
              "long_paths": [], "texts": 0, "skipped": []}
    plans = []
    for folder in script_folders(lib):
        rel = folder.relative_to(lib.dir).as_posix()
        title, _ = folder_file_title(folder)
        if not title:
            report["notitle"].append(rel)
            continue
        p = record_file(folder)
        h, allowed = None, None
        if p is not None:
            h, _ = parse_script(p)
            allowed = old_names_of(h, record_name(p)) | {h.get("Label", "").strip()}
        try:
            pairs, skipped = title_rename_plan(folder, title, allowed)
        except LibraryError as e:
            report["left"].append((rel, str(e)))
            continue
        report["skipped"] += [(q.relative_to(lib.dir).as_posix(), why) for q, why in skipped]
        if not pairs:
            report["conformant"] += 1
            continue
        if p is not None:
            if h.get(APPROVED_KEY, "").strip():
                try:
                    refuse_if_approved_drifted(lib, h, p.read_bytes(), "rename-to-title")
                except LibraryError as e:
                    report["left"].append((rel, str(e)))
                    continue
        plans.append((rel, folder, pairs))
        mds = [(o.name, n.name) for o, n in pairs if o.suffix == ".md"]
        report["folders"].append((rel, mds))
        report["texts"] += len(mds)
    planned = [(o, n) for _, _, pairs in plans for o, n in pairs]
    report["long_paths"] = long_paths(lib, planned)
    if not apply or not plans:
        return report
    done_all, moves = [], []
    saved = save_registries(lib)
    try:
        for rel, folder, pairs in plans:
            done = apply_label_renames(lib, pairs, use_git=use_git)
            done_all += done
            moves += [(o, n) for o, n in done if o.suffix == ".md"]
        report["registries"] = update_path_registries(lib, moves)         # check reads LOG-NOTE-ORIGINS.csv by file name
        after_errors, _ = cmd_check(lib, write=False)
        new = new_problems(before_errors, after_errors, moves)
        if new:
            raise LibraryError("renaming would leave the library with %d new problem(s), so every rename was undone: %s"
                               % (len(new), "; ".join(new[:5])))
    except BaseException:
        undo_label_renames(lib, done_all, use_git)
        restore_registries(saved)
        raise
    cmd_check(lib)                       # INDEX.md follows the new names
    report["applied"] = True
    return report


def retag_body(body, new_type, tab):
    """Return body with the type part of its first '## Script N [TYPE-TAB]: Title' line changed to
    new_type. Only that one line changes, byte for byte otherwise (CRLF, spacing, everything after it).
    Refuses when the first line has no tag, or the tag is not TYPE-TAB, so nothing is guessed."""
    first, sep, rest = body.partition(b"\n")
    line = first.decode("utf-8")
    m = SCRIPT_HDR.match(line.rstrip())
    if not m or not m.group(1):
        raise LibraryError("the script body does not start with a tagged '## Script N [TYPE-TAB]: Title' line, "
                           "so its tag cannot be changed")
    m2 = re.search(r"\[([A-Z]+)-(NIGHT|DAY)\]", line)
    if not m2 or m2.group(2) != tab.upper():
        raise LibraryError("the body tag [%s] does not match the header Tab %r; fix that first" % (m.group(1), tab))
    new_line = line[:m2.start()] + "[%s-%s]" % (new_type, m2.group(2)) + line[m2.end():]
    return new_line.encode("utf-8") + sep + rest


def rebaselined_entry(lib, rel, swap_path, swap_body, will_be_kept=None):
    """The MANIFEST entry for import file `rel` as build would produce it with one script body swapped and, when
    will_be_kept is True or False, that script counted as in or out of 3-final (a stage change across 3-final moves a live
    script into or out of the built file; None leaves its place as the folder says).
    Refuses unless build output today still equals the stored baseline, so a type change never hides
    some other difference. Returns the new entry dict."""
    manifest_doc = load_json(lib.build_dir / "MANIFEST.json", {"files": {}})
    entry = manifest_doc["files"].get(rel)
    if entry is None:
        raise LibraryError("%s is not an import file listed in _build/MANIFEST.json" % rel)
    items = sorted(collect_live_sources(lib).get(rel, []), key=lambda t: t[0])
    check_live_positions(rel, items, "the live scripts built into or withheld from it")
    side = lib.build_dir / (rel + ".preamble.md")
    if not side.exists():
        raise LibraryError("%s: preamble sidecar missing: %s" % (rel, side))
    cur = render_import_file(lib, side.read_bytes(), items)
    if hashlib.sha256(cur).hexdigest() != entry.get("sha256"):
        raise LibraryError("%s: build already differs from its baseline hash before this change, so the baseline "
                           "will not be updated; fix that first" % rel)
    swap = Path(swap_path).resolve()

    def kept_now(path):
        if will_be_kept is not None and Path(path).resolve() == swap:
            return will_be_kept
        return is_kept_path(lib, path)

    new = render_import_file(lib, side.read_bytes(), [(t[0], t[1], swap_body if t[1].resolve() == swap else t[2])
                                                      for t in items], kept=kept_now)
    out = dict(entry)
    out["sha256"] = hashlib.sha256(new).hexdigest()
    out["bytes"] = len(new)
    return out


# --------------------------------------------------------------------------- the retired-slug list (o10:W232)

RETIRED_NAME = "RETIRED-SLUGS.json"
RETIRED_NOTE = ("Written by .shared/scripts/file_script.py build --app from script-library/RETIRED-SLUGS.json in the "
                "workspace repo. The app's script import hides the rows whose slug is listed in `slugs`. Do not edit "
                "by hand: retire the script in the library and build again.")


def retired_slug_list(lib):
    """The slugs in script-library/RETIRED-SLUGS.json, without repeats, in file order. Refuses a file that is not a
    JSON object holding { "slugs": [non-empty strings] }, the shape the app's importer reads."""
    path = lib.dir / RETIRED_NAME
    try:
        doc = load_json(path, {"slugs": []})
    except ValueError as e:
        raise LibraryError("%s is not valid JSON (%s)" % (path, e))
    slugs = doc.get("slugs") if isinstance(doc, dict) else None
    if not isinstance(slugs, list) or not all(isinstance(x, str) and x for x in slugs):
        raise LibraryError('%s must hold { "slugs": [non-empty strings] }' % path)
    return list(dict.fromkeys(slugs))


def render_retired_list(slugs):
    """The bytes written to <app>/voice_scripts/RETIRED-SLUGS.json: the shape importVoiceScripts.ts loadRetiredSlugs
    reads ({ "slugs": [...] }; other keys are ignored there), LF line endings, nothing that varies between builds."""
    doc = {"note": RETIRED_NOTE, "slugs": list(dict.fromkeys(slugs))}
    return (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def renumber_body(body, number, tab=None):
    """body with the number of its first '## Script N ...' line set to `number` and, when tab is given, the tab part of
    its [TYPE-TAB] tag set to tab. Only that line changes; the rest is byte for byte."""
    first, sep, rest = body.partition(b"\n")
    line = first.decode("utf-8")
    m = re.match(r"^(##\s+Script\s+)\d+", line)
    if not m:
        raise LibraryError("the script body does not start with a '## Script N' line, so it cannot be renumbered")
    line = m.group(1) + str(number) + line[m.end():]
    if tab is not None:
        m2 = re.search(r"\[([A-Z]+)-(NIGHT|DAY)\]", line)
        if not m2:
            raise LibraryError("the script body's first line has no [TYPE-TAB] tag, so its tab cannot be changed")
        line = line[:m2.start()] + "[%s-%s]" % (m2.group(1), tab.upper()) + line[m2.end():]
    return line.encode("utf-8") + sep + rest


def strip_trailing_separator(body):
    """A chunk that was followed by another script ends with a `---` line and a blank line. When it becomes the
    last chunk of its file that tail goes, leaving the text and one line ending (the way every last chunk ends)."""
    m = SEPARATED_RE.search(body)
    if not m:
        return body
    nl = b"\r\n" if b"\r\n" in body else b"\n"
    return body[:m.start()].rstrip(b"\r\n") + nl


def plan_live_change(lib, p, before, h, body):
    """Everything a move of a live script to another category, or its retirement, changes, worked out and checked
    without writing anything. Edits `h` (Source, Source-position, or both removed). Returns a plan for
    apply_live_plan with 'new_body' (the script's own new body) and the other files to write."""
    label = before.get("Label") or before.get("ID")
    src = before["Source"]
    slug = h.get("Slug", "").strip()
    if not slug or slug.lower() == "none":
        raise LibraryError(
            "%s is imported by the app (Source %s) but has no slug (the app matches its row by title, voice type and "
            "category). A move would insert a second row and a retirement could not name the row, so it is not "
            "allowed until the row has a slug. Nothing was changed." % (label, src))
    retiring = h.get("Stage") == "retired"
    if retiring:
        if any(h.get(k, "") != before.get(k, "") for k in ("Line", "Tab", "Category")):
            raise LibraryError("%s is imported by the app: retire it, or move it to another category, in separate "
                               "commands. Nothing was changed." % label)
    refuse_if_check_fails(lib, "changing the live script %s" % label)
    items_a, mf_a = current_import_state(lib, src)
    mypos = int(before.get("Source-position", "0") or 0)
    others = [t for t in items_a if t[1].resolve() != p.resolve()]
    if len(others) != len(items_a) - 1:
        raise LibraryError("%s is not among the released scripts of %s; run check" % (label, src))
    plan = {"edits": [], "manifest": {}, "sidecars": {}, "delete_sidecars": [], "retired": None, "new_body": body,
            "touched": []}
    # Positions run over ALL the live scripts of a file, the withheld ones (not in 3-final) included; only the ones in
    # 3-final are in the built file. `p` is judged by the stage it will have (h), not by the folder it is still in.
    p_will_be_kept = h.get("Stage") == "final"

    def kept_after(path):
        return p_will_be_kept if Path(path).resolve() == p.resolve() else is_kept_path(lib, path)

    # the file the script leaves: its other scripts close the gap
    new_a = []
    for i, (pos, path, b) in enumerate(others, start=1):
        nb = b
        if i == len(others) and mypos == len(items_a):
            nb = strip_trailing_separator(nb)      # the script that left was the last; this one is now
        if pos != i:
            nb = renumber_body(nb, i)
        if nb != b or pos != i:
            oh, _ = parse_script(path)
            oh["Source-position"] = str(i)
            plan["edits"].append((path, oh, nb))
        new_a.append((i, path, nb))
    if new_a:
        data = render_import_file(lib, (lib.build_dir / (src + ".preamble.md")).read_bytes(), new_a, kept=kept_after)
        entry = dict(mf_a)
        entry.update({"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "scripts": len(new_a)})
        plan["manifest"][src] = entry
    else:
        plan["manifest"][src] = None
        plan["delete_sidecars"].append(src)
    if retiring:
        h.pop("Source", None)
        h.pop("Source-position", None)
        h.pop("Release", None)
        h.pop("App", None)
        listed = retired_slug_list(lib)
        if slug not in listed:
            doc = load_json(lib.dir / RETIRED_NAME, {"slugs": []})
            doc["slugs"] = listed + [slug]          # every other key of the library's file stays as it is
            plan["retired"] = (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        return plan
    # the file the script enters (the category's generated file, as `release` names it)
    line, tab, cat_folder = h.get("Line", ""), h.get("Tab", ""), h.get("Category", "")
    cat = lib.category_by_folder(line, tab, cat_folder)
    if not cat:
        raise LibraryError("%s: category %r is not in CATEGORIES.json for %s/%s" % (label, cat_folder, line, tab))
    dest = import_file_name(tab, cat["app_category"], lib, cat)
    if dest == src:
        raise LibraryError("%s is already built into %s; nothing to move" % (label, dest))
    items_b, mf_b = current_import_state(lib, dest)
    if mf_b is not None and (mf_b.get("line"), mf_b.get("tab"), mf_b.get("category")) != (line, tab, cat_folder):
        raise LibraryError("%s: import file %s belongs to %s/%s/%s, not to %s/%s/%s"
                           % (label, dest, mf_b.get("line"), mf_b.get("tab"), mf_b.get("category"), line, tab, cat_folder))
    number = len(items_b) + 1
    new_body = renumber_body(strip_trailing_separator(body), number, tab if tab != before.get("Tab") else None)
    if mf_b is not None:
        preamble_b = (lib.build_dir / (dest + ".preamble.md")).read_bytes()
    else:
        preamble_b = new_preamble(tab, cat["app_category"], number_of_file(dest, cat))
        plan["sidecars"][dest] = preamble_b
    data_b = render_import_file(lib, preamble_b, list(items_b) + [(number, p, new_body)], kept=kept_after)
    plan["manifest"][dest] = {"sha256": hashlib.sha256(data_b).hexdigest(), "bytes": len(data_b), "scripts": number,
                              "line": line, "tab": tab, "category": cat_folder}
    h["Source"] = dest
    h["Source-position"] = str(number)
    plan["new_body"] = new_body
    return plan


def apply_live_plan(lib, plan):
    """Write the other files of a plan (the renumbered scripts, MANIFEST.json, preamble sidecars, RETIRED-SLUGS.json).
    Returns a function that puts every one of them back as it was."""
    manifest_path = lib.build_dir / "MANIFEST.json"
    retired_path = lib.dir / RETIRED_NAME
    paths = [e[0] for e in plan["edits"]] + [manifest_path, retired_path]
    paths += [lib.build_dir / (rel + ".preamble.md") for rel in list(plan["sidecars"]) + plan["delete_sidecars"]]
    saved = {Path(x): (Path(x).read_bytes() if Path(x).exists() else None) for x in paths}

    def undo():
        for x, data in saved.items():
            if data is None:
                if x.exists():
                    x.unlink()
            else:
                x.write_bytes(data)

    try:
        for path, oh, nb in plan["edits"]:
            write_script(path, oh, nb, keep_from=saved[Path(path)])
        doc = load_json(manifest_path, {"files": {}})
        for rel, entry in plan["manifest"].items():
            if entry is None:
                doc["files"].pop(rel, None)
            else:
                doc["files"][rel] = entry
        save_json(manifest_path, doc)
        for rel, data in plan["sidecars"].items():
            side = lib.build_dir / (rel + ".preamble.md")
            side.parent.mkdir(parents=True, exist_ok=True)
            side.write_bytes(data)
        for rel in plan["delete_sidecars"]:
            side = lib.build_dir / (rel + ".preamble.md")
            if side.exists():
                side.unlink()
        if plan["retired"] is not None:
            retired_path.write_bytes(plan["retired"])
    except BaseException:
        undo()
        raise
    return undo


def file_rewrite_as_version(lib, p, ref, use_git=True, older_draft=None, dry_run=False):
    """`file` on a record file that is a rewrite of the script `ref` names (W243): its text goes to that script's folder as the
    next version (route_as_next_version), the app text does not change, and no label is made. W245: when its own folder holds more
    (other texts, a versions/ folder, process logs, notes), the whole folder is moved (route_folder_as_versions), registries
    included; `dry_run` then writes nothing and returns [(old, new)]. The emptied folder is removed. Returns the target script's
    folder."""
    p = Path(p)
    folder = p.parent
    others = sorted(e.name for e in folder.iterdir() if e.resolve() != p.resolve()
                    and not (e.is_dir() and not any(e.iterdir())))
    if others:
        moves = route_folder_as_versions(lib, folder, ref, use_git=use_git, older_draft=older_draft, dry_run=dry_run)
        return moves if dry_run else find_script(lib, ref)[0].parent
    if dry_run:
        raise LibraryError("--dry-run is for moving a rewrite folder; %s holds only this text, so nothing was changed" % rel_posix(lib, folder))
    dest = route_as_next_version(lib, p, ref, use_git=use_git, older_draft=older_draft)
    update_path_registries(lib, [(p, dest)])
    for e in list(folder.iterdir()):
        e.rmdir()                                            # empty folders only (checked above)
    remove_emptied_folders(lib, folder)
    return dest.parent


def cmd_file(lib, target, stage, category=None, tab=None, line=None, use_git=True, app_change=False, new_type=None,
             ruling=None, rewrite_of=None, new_label_anyway=False, older_draft=None, dry_run=False):
    """Move a script folder to where its header says. Everything is checked before anything is
    written: a refused command changes no file and spends no label number.

    new_type ('AT' or 'SC') changes the script's type and nothing else: same line, tab, category,
    import file and position. The label gets the new type and the next free number in its category
    (the old label stays in Aliases), the body's [TYPE-TAB] tag is rewritten, the folder is renamed
    for the new label, and for an imported script the baseline hash of its import file is updated.

    A record with a `## Approved-By:` line is refused a type change, a retirement, and any change to its approved text
    unless `ruling` (--owner-ruling) names the human's ruling; a move or a stage change alone is allowed (o10 W239), but not
    while its text has drifted from the hash in THE HUMAN-APPROVED.csv (o10L373 S3).

    W243: a record that is a rewrite of another script (`rewrite_of`, or a `## Rewrite-Of: <LABEL>` header line naming a script
    in another folder) is not given a label: its text is added to that script's folder as the next version and the app text
    does not change (file_rewrite_as_version). It is refused when another option (--stage, --category, --tab, --line, --type,
    --app-change, --owner-ruling) is also given, and when the record is a script of its own: `(in app)`, live, final or
    labelled (rewrite_source_refusal, o10L438 F1 and F5). Giving a script its first label is refused when
    a script in the same category has the same title or a slug this one extends, unless `new_label_anyway`."""
    p = script_path(target)
    ruling = validate_ruling(ruling)
    original = p.read_bytes()
    h, body = parse_script(p)
    ref = (rewrite_of or "").strip() or rewrite_of_value(h)
    if older_draft is not None:
        older_draft_reason(older_draft)
        if not ref:
            raise LibraryError("--older-draft goes with --rewrite-of (or a `## Rewrite-Of:` line): it marks the text a rewrite adds as "
                               "an older draft. Nothing was changed.")
    if dry_run and not ref:
        raise LibraryError("--dry-run is for moving a rewrite folder (file <text> --rewrite-of <LABEL> --dry-run); nothing was changed")
    if ref:
        if h.get("Approved-By"):
            raise LibraryError("%s carries `## Approved-By:`; it is a script the human approved, not a rewrite to add as a version of %s. "
                               "Nothing was changed." % (p, ref))
        if find_script(lib, ref)[0].parent.resolve() != p.parent.resolve():
            refusal = rewrite_source_refusal(p, h, ref)          # o10L438 F1: a live, (in app) or labelled record stays put
            if refusal:
                raise LibraryError(refusal)
            given = [opt for opt, v in (("--stage", stage), ("--category", category), ("--tab", tab), ("--line", line),
                                        ("--type", new_type), ("--app-change", app_change), ("--owner-ruling", ruling)) if v]
            if given:
                raise LibraryError("%s is a rewrite of %s (%s), so `file` adds it to that script's folder as its next version and "
                                   "takes no %s: the version takes that script's place, stage and category. Run the command "
                                   "again without %s. Nothing was changed." % (
                                       p, ref, "--rewrite-of" if (rewrite_of or "").strip() else "its `## Rewrite-Of:` line",
                                       " or ".join(given), " and ".join(given)))
            return file_rewrite_as_version(lib, p, ref, use_git=use_git, older_draft=older_draft, dry_run=dry_run)
    before = dict(h)
    refuse_if_approved_drifted(lib, before, original, "file")      # o10L373 S3: also a stage-only change
    # W235 step 7: a record file gains the three step-6 lines it lacks (a file made before `place` wrote them); the lines it
    # has stay as they are
    named = record_name(p)
    for k, v in record_header_values(named.name_key, named.version, named.author).items():
        h.setdefault(k, v)
    if stage:
        if stage not in STAGES:
            raise LibraryError("stage must be one of %s" % ", ".join(STAGES))
        h["Stage"] = stage
    if line:
        h["Line"] = line
    if tab:
        h["Tab"] = tab
    if category:
        h["Category"] = category
    fill_from_reservation(lib, h)         # o10L477 D2: an open reservation supplies the Type, Line, Tab and Category the header lacks
    new_body = body
    retyped = False
    if new_type:
        if new_type not in ("AT", "SC"):
            raise LibraryError("type must be AT or SC, got %r" % new_type)
        if new_type != h.get("Type"):
            retyped = True
            h["Type"] = new_type
            if h.get("Source"):
                if h.get("Tab") not in TABS:
                    raise LibraryError("tab must be night or day, got %r" % h.get("Tab"))
                new_body = retag_body(body, new_type, h["Tab"])
    if h.get("Line") and not lib.line_code(h["Line"]):
        raise LibraryError("product line %r has no code in LINES.json; run add-line first" % h["Line"])
    if h.get("Stage") == "final" and not h.get("Category"):
        raise LibraryError("a final script needs a category (use --category, --tab, --line)")
    if h.get("Category") and h.get("Line") and h.get("Tab") in TABS:
        if not lib.category_by_folder(h["Line"], h["Tab"], h["Category"]):
            raise LibraryError("category %r is not in CATEGORIES.json for %s/%s" % (h["Category"], h["Line"], h["Tab"]))
    # A script the app imports may be moved to another category or retired (o10:W232, now that the app's import
    # applies a category move and hides the slugs in RETIRED-SLUGS.json). That changes the import files, their
    # baseline hashes and, for a retirement, RETIRED-SLUGS.json, all in this one command.
    live = None
    if h.get("Source"):
        # A stage change between draft, ready and final does not touch an import file (o10:W235): the script keeps
        # Source, Source-position, Slug and App. Only a retirement or a move to another category does.
        changed = [k for k in ("Line", "Tab", "Category") if h.get(k, "") != before.get(k, "")]
        if h.get("Stage") == "retired" and before.get("Stage") != "retired":
            changed.append("Stage")
        if changed and new_type and new_type != before.get("Type"):
            raise LibraryError(
                "%s is imported by the app (Source %s). Change its type in a command of its own (`file --type`), "
                "not together with a move or a retirement. Nothing was changed."
                % (before.get("Label") or before.get("ID"), h["Source"]))
        if changed:
            live = plan_live_change(lib, p, before, h, body)
            new_body = live["new_body"]
    if before.get("Stage") == "retired" and h.get("Stage") == "final":
        rslug = before.get("Slug", "").strip()
        if rslug and rslug.lower() != "none" and rslug in retired_slug_list(lib):
            raise LibraryError("%s was retired and its slug %s is in RETIRED-SLUGS.json; bringing a retired script "
                               "back is not supported, and a retired slug is never used again. Nothing was changed."
                               % (before.get("Label") or before.get("ID"), rslug))
    # A live script (in an import file) that changes stage keeps being live. Until the header-only commit puts
    # `## App: live` on all 436, a final script with a Source is live only because it is final; once it leaves
    # final that fallback no longer applies, so the line is written here (o10:W235 D1, o10L297). The line goes
    # after Source-position (HEADER_ORDER), where `split`, `release` and the header-only commit put it.
    if (is_live(before) and before.get("Source") and before.get("Source-position")
            and h.get("Stage") != "retired" and h.get("Stage") != before.get("Stage")):
        h["App"] = "live"
    # a script entering 3-final is held: it reaches the app only through a second, explicit `release`
    if h.get("Stage") == "final" and before.get("Stage") != "final" and not h.get("Source"):
        h["Release"] = "held"
    if h.get("Stage") != "final":
        h.pop("Release", None)
    also = []
    if retyped:
        also.append("its type (%s to %s)" % (before.get("Type"), h.get("Type")))
    if h.get("Stage") == "retired" and before.get("Stage") != "retired":
        also.append("its place in the app (a retirement)")
    ruling_entry = approval_gate(lib, before, original, (END_MARK + "\n").encode("utf-8") + new_body, "file",
                                 ruling, also=" and ".join(also) or None)
    if ruling_entry:
        h = with_ruling(h, ruling_entry)
    new_entry = None
    # a live script that crosses 3-final (an approval, or taking an approved script back to draft) enters or leaves the built
    # import file, so that file's baseline hash moves in the same command (o10 W244)
    crosses_final = (live is None and h.get("Source") and is_live(before) and h.get("Stage") != "retired"
                     and (before.get("Stage") == "final") != (h.get("Stage") == "final"))
    if (retyped or crosses_final) and h.get("Source"):
        new_entry = rebaselined_entry(lib, h["Source"], p, new_body,
                                      will_be_kept=(h.get("Stage") == "final") if crosses_final else None)
    claimed = claim_label(lib, h, p)                  # o10L475: a reserved label is kept; an invented one is refused
    pending = ensure_label(lib, h)
    if pending and not before.get("Label", "").strip() and not new_label_anyway:
        matches = lookalike_scripts(lib, h, own_folder=p.parent)
        if matches:
            raise LibraryError(rewrite_refusal(lib, matches, "file", target))
    want = lib.dir / expected_rel_dir(h)
    old_dir = p.parent
    moving = want.resolve() != old_dir.resolve()
    if moving and want.exists():
        raise LibraryError("destination already exists: %s" % want)
    # W238: once the script has a Label, every text in its folder is named by it. The plan is made, and refused if a new
    # name is taken, before anything is written; the text of a record is not touched (a pure rename, no ruling).
    label_pairs = []
    if h.get("Label", "").strip():
        # o10L431: a script that gets its Label now is renamed into the new shape (NAME and title). One that already has its
        # Label keeps the title its names carry (none until `rename-to-title`), brought up to date from its Title line.
        pn0 = record_name(p)
        if pn0.name_key != h["Label"].strip():
            want_title = file_title(h, old_dir, pn0.name_key)
        else:
            want_title = carried_title(h, p, pn0.name_key) or None
        label_pairs, _ = label_rename_plan(old_dir, h["Label"].strip(), old_names_of(h, pn0), title=want_title)
    reg_moves = moved_texts(old_dir, want, label_pairs) if (moving or label_pairs) else []     # read before the folder moves
    # all checks passed; now write
    undo = None
    moved = False
    csv_old = None
    done_renames = []
    reg_saved = None
    try:
        if live is not None:
            undo = apply_live_plan(lib, live)
        write_script(p, h, new_body, keep_from=original)
        if ruling_entry:
            csv_old = record_ruling_in_csv(lib, before, guard_hash_of_body(new_body, guard_is_live(before, original)),
                                           ruling_entry)
        if moving:
            want.parent.mkdir(parents=True, exist_ok=True)
            move_path(lib, old_dir, want, use_git=use_git)
            moved = True
        if label_pairs:
            in_new = [(want / o.relative_to(old_dir), want / n.relative_to(old_dir)) for o, n in label_pairs] \
                if moving else label_pairs
            done_renames = apply_label_renames(lib, in_new, use_git=use_git)
        if moving or done_renames:
            # the two registries that name a text by path or file name follow it, before check reads them
            reg_saved = save_registries(lib)
            update_path_registries(lib, reg_moves)
        if live is not None:
            errors, _ = cmd_check(lib, write=False)
            if errors:
                raise LibraryError("%s would leave the library with %d problem(s), so nothing was changed: %s"
                                   % (before.get("Label") or before.get("ID"), len(errors), "; ".join(errors[:5])))
    except BaseException:
        restore_registries(reg_saved)
        undo_label_renames(lib, done_renames, use_git)
        if moved:
            move_path(lib, want, old_dir, use_git=use_git)
        if p.exists():
            p.write_bytes(original)
        if undo is not None:
            undo()
        if csv_old is not None:
            (lib.dir / APPROVED_CSV).write_bytes(csv_old)
        raise
    if pending:
        commit_number(lib, *pending)
    if new_entry is not None:
        doc = load_json(lib.build_dir / "MANIFEST.json", {"files": {}})
        doc["files"][h["Source"]] = new_entry
        save_json(lib.build_dir / "MANIFEST.json", doc)
    if moving:
        parent = old_dir.parent
        while parent != lib.dir and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
    # o10L594 (the human, 2026-10-22): a retired script is not in the app, so its files lose the ` (in app)` tag in the same command
    if h.get("Stage") == "retired":
        cmd_drop_app_tags(lib, apply=True, use_git=use_git, folder=want)
    # o10L479: marking the reservation used is the LAST write. A lock timeout here leaves the script complete and only the
    # reservation unmarked, which a later `file` run repairs.
    if claimed and claimed[1] and h.get("Label", "").strip() == claimed[0]:
        mark_reservation_used_last(lib, claimed[0], h, want)
    return want


# --------------------------------------------------------------------------- release, revise, drift (o10:W226 switch-over)

GENERATED_PREFIX = "50_promoted_"
HEADING_RE = re.compile(r"^##\s+Script\s+(\d+)\s+\[([A-Z]+)-(NIGHT|DAY)\]:\s*(.*)$")
NO_SLUG_ROW_RE = re.compile(r"^>\s*NO SLUG BY DESIGN: matches live row id=(\d+)\b")


RELEASE_MARK = "> GENERATED by .shared/scripts/file_script.py (release) - do not hand-edit."


def numbered_file_name(cat):
    """`<NN>_<slug with _>_merged.md` (in Daytime/ for a day category): the scheme of the hand-authored numbered import
    files (03_sleep_onset_merged.md), so the file sorts where the category's number puts it (o10L469)."""
    return (("Daytime/" if cat["tab"] == "day" else "") + "%s_%s_merged.md"
            % (cat["number"], cat["slug"].replace("-", "_")))


def import_file_name(tab, app_category, lib=None, cat=None):
    """Where a newly released script goes: one file per app category, in Daytime/ for a Daytime category.

    A category that already has an import file (hand-authored or generated) keeps the name promote-approved-scripts.mjs
    gives its files, 50_promoted_<app category>_merged.md, as before: the app lists categories in import-file name
    order, and the category's existing file already places it. A category with no import file at all (o10: night 04
    sleep-hygiene) gets numbered_file_name instead, so it sits between its neighbours in the app (o10L469); once
    release has made that file (its preamble carries RELEASE_MARK) the category keeps using it. Without lib and cat
    the 50_promoted_ name is returned."""
    legacy = ("Daytime/" if tab == "day" else "") + GENERATED_PREFIX + slugify(app_category) + "_merged.md"
    if lib is None or cat is None:
        return legacy
    key = (cat["line"], cat["tab"], cat["folder"])
    files = load_json(lib.build_dir / "MANIFEST.json", {"files": {}}).get("files", {})
    held = sorted(rel for rel, e in files.items() if (e.get("line"), e.get("tab"), e.get("category")) == key)
    numbered = numbered_file_name(cat)
    if not held:
        return numbered
    side = lib.build_dir / (numbered + ".preamble.md")
    if held == [numbered] and side.exists() and RELEASE_MARK.encode("utf-8") in side.read_bytes():
        return numbered
    return legacy


def number_of_file(rel, cat):
    """The category number for the H1 of a new import file rel: the category's for its numbered file, else None (99)."""
    return cat["number"] if rel == numbered_file_name(cat) else None


def new_preamble(tab, app_category, number=None):
    """The text before the first script of a new generated import file: the H1 the importer reads (the category
    comes from it) and the four note lines of renderMergedFile, naming this tool as the generator. The H1 carries
    `number` (the category's, for a numbered file) or 99 (for a 50_promoted_ file); the importer ignores it."""
    n = int(number) if number is not None else 99
    h1 = ("# Daytime Category %d: %s" if tab == "day" else "# Category %d: %s") % (n, app_category)
    lines = [h1, "",
             RELEASE_MARK,
             "> Edit the script in script-library/3-final in the workspace and release or revise it instead.",
             "> Category is carried by the H1 above; the importer groups rows by that string,",
             "> so these scripts land beside the hand-authored ones in the same category.",
             "", "---", ""]
    return ("\n".join(lines) + "\n").encode("utf-8")


def script_path(target):
    """A file is itself; a folder is its record file. A file must be named like a record file
    (`<NAME> v<N> <AUTHOR>[ (in app)].md`): no other name, SCRIPT.md included, is a record file (W235 step 7)."""
    p = Path(target)
    if p.is_dir():
        found = record_file(p)
        if found is None:
            raise LibraryError("no record file (<NAME> v<N> <AUTHOR>.md with a `## Stage:` header) at %s" % target)
        return found
    if not p.exists():
        raise LibraryError("no record file at %s" % target)
    if record_name(p) is None:
        raise LibraryError("%s is not named like a record file (<NAME> v<N> <AUTHOR>[ (in app)].md)" % target)
    return p


def record_name(p):
    """training_header.parse_script_name of a record file named `<NAME> v<N> <AUTHOR>[ (in app)].md`, or None for a
    file of any other name (SCRIPT.md included, since W235 step 7)."""
    pn = training_header.parse_script_name(Path(p).name)
    return pn if pn.version is not None and pn.kind in RECORD_KINDS else None


def next_version_number(folder):
    """One more than the highest `v<N>` among the texts in the folder and its versions/ folder (the version number a
    new text takes: o10L321, W235 step 5, review o10L318 M4)."""
    top = 0
    folder = Path(folder)
    for d in (folder, folder / "versions"):
        try:
            names = os.listdir(str(d))
        except OSError:
            continue
        for n in names:
            pn = training_header.parse_script_name(n)
            if pn.version is not None and pn.kind in RECORD_KINDS:
                top = max(top, pn.version)
    return top + 1


def version_name(name_key, number, author, in_app=False, title=""):
    """`<NAME>[ <Title-Words>] v<N> <AUTHOR>[ (in app)].md` (o10L431: the title, when there is one, sits between the NAME and
    ` v<N>`; the shape is written by training_header.script_file_name)."""
    return training_header.script_file_name(name_key, number, author, title=title, in_app=in_app)


def folder_slug_title(folder, header, name_key):
    """The words of a script folder's slug (the folder name without its leading NAME), as a title; "" when there are none.
    The fallback for a record with no `## Title:` line."""
    base = Path(folder).name
    for pre in (name_key, (header or {}).get("Label", "").strip(),
                "%s-%s" % ((header or {}).get("Type", "").strip(), (header or {}).get("ID", "").strip())):
        if pre and base.lower().startswith(pre.lower() + "-"):
            return training_header.title_slug(base[len(pre) + 1:].replace("-", " "))
    return ""


def carried_title(header, record, name_key):
    """The title the new text beside `record` (a record file) carries in its name (o10L431): the title the record's own name
    carries, brought up to date from its `## Title:` line, and "" when its name carries none. So a folder that has been
    through `rename-to-title` stays titled through `release` and `revise`, and a folder that has not stays as it is until
    that command runs (mid-migration, an untitled name is still a valid name)."""
    cur = training_header.script_title(Path(record).name)
    if not cur:
        return ""
    return file_title(header, Path(record).parent, name_key) or cur


def file_title(header, folder=None, name_key=""):
    """The title part of the file names in a script folder (o10L431): the record's `## Title:` header, else the words of the
    folder's slug, else "" (no title in the names). training_header.title_slug sets the shape: capitalised words joined by
    hyphens, apostrophes dropped, 40 characters at most."""
    t = training_header.title_slug((header or {}).get("Title", ""))
    if t or folder is None:
        return t
    return folder_slug_title(folder, header, name_key)


RECORD_DROP_KEYS = ("Origin", "Source-File")


def record_header_values(name_key, number, author, family=None):
    """The step-6 lines a record file must carry (`Version-Of`, `Version`, `Author`): the file's own values, taken
    from its name, not the values of the file a header was copied from. Since W235 step 7 every command that makes a
    record file writes all three (before, they were written only when the old header already had them: o10L348).
    `family` (W238) is the one exception: Version-Of is the family key, which the NAME does not decide once the file
    name follows the Label, so a new text of a script passes the Version-Of its older texts carry (family_of)."""
    return {"Version-Of": family or name_key, "Version": str(number), "Author": author}


_STEP6_KEYS = ("Version-Of", "Version", "Author")


def stamp_version_lines(data, values, keys=None):
    """`data` (the bytes of a text) with the header lines `## Version-Of:`, `## Version:` and `## Author:` set to `values`.
    A line the header has is changed in place when its value differs; a missing one is inserted directly above the
    `<!-- END SCRIPT HEADER -->` line (the place and the line ending add_origin_headers.py uses). Every other byte stays.
    A text with no marker line is returned unchanged (add_origin_headers.py lists it too)."""
    marker = END_MARK.encode("utf-8")
    pos, mstart, nl = 0, -1, b"\n"
    lines = data.split(b"\n")
    for ln in lines:
        if ln.strip() == marker:
            mstart = pos
            nl = b"\r\n" if ln.endswith(b"\r") else b"\n"
            break
        pos += len(ln) + 1
    if mstart < 0:
        return data
    head, rest = data[:mstart], data[mstart:]
    out_lines = head.split(b"\n")
    missing = []
    for k in (keys or _STEP6_KEYS):
        if k not in values:
            continue
        want = ("## %s: %s" % (k, values[k])).encode("utf-8")
        hit = None
        for i, ln in enumerate(out_lines):
            if re.match(rb"^##\s+" + k.encode() + rb":", ln.lstrip(b"\xef\xbb\xbf")):
                hit = i
                break
        if hit is None:
            missing.append(want)
        else:
            cr = b"\r" if out_lines[hit].endswith(b"\r") else b""
            if out_lines[hit].rstrip(b"\r") != want:
                out_lines[hit] = want + cr
    return b"\n".join(out_lines) + b"".join(w + nl for w in missing) + rest


def with_name_lines(header, pn):
    """header with the three step-6 lines set from the parsed record-file name `pn`, and nothing else changed. Version-Of
    is the header's own family key when it has one (W238), else the name key."""
    h = dict(header)
    h.update(record_header_values(pn.name_key, pn.version, pn.author, family_of(header, pn)))
    return h


def revision_author(text):
    """The author code of a revision from its Authored line (`By: o5L57` -> o5, `Rewrite By: the human` -> THE HUMAN), else agent."""
    line = authored_line_anywhere(text)
    by = (training_header.parse_provenance_line(line) or {}).get("by", "") if line else ""
    if re.match(r"(?i)\s*the human\b", by):
        return "THE HUMAN"
    m = re.search(r"\bo(\d+)", by)
    return "o" + m.group(1) if m else "agent"


def current_import_state(lib, rel):
    """(sorted [(position, path, body)] of ALL the live scripts of import file rel, kept and withheld, MANIFEST entry or
    None). Their positions are 1..n; only the ones in 3-final are in the built file (render_import_file). Refuses when the
    positions are not 1..n or build today differs from the stored baseline hash."""
    doc = load_json(lib.build_dir / "MANIFEST.json", {"files": {}})
    mf = doc["files"].get(rel)
    items = sorted(collect_live_sources(lib).get(rel, []), key=lambda t: t[0])
    if mf is None:
        if items:
            raise LibraryError("%s has released scripts but is not in _build/MANIFEST.json" % rel)
        return items, None
    positions = [t[0] for t in items]
    if positions != list(range(1, len(items) + 1)) or len(items) != mf.get("scripts"):
        raise LibraryError("%s: the live scripts have positions %s but the baseline says %s scripts; fix that "
                           "first (run check)" % (rel, positions, mf.get("scripts")))
    side = lib.build_dir / (rel + ".preamble.md")
    if not side.exists():
        raise LibraryError("%s: preamble sidecar missing: %s" % (rel, side))
    if hashlib.sha256(render_import_file(lib, side.read_bytes(), items)).hexdigest() != mf.get("sha256"):
        raise LibraryError("%s: build already differs from its baseline hash before this change, so the baseline "
                           "will not be updated; fix that first (after the 3-final-only change, `rebaseline` moves the "
                           "baselines once)" % rel)
    return items, mf


OUTSIDE_FINAL = ("%s is not in script-library/3-final, so `%s` will not put its text in the app: only an approved script reaches "
                 "the app (the human, 2026-10-11). Approve it with `file_script.py approve <path> --owner-ruling <ref>` (the "
                 "Approve button on the OrchDoc Reader runs that); approving moves it to 3-final and puts its text in the "
                 "app. Nothing was written.")


def cmd_release(lib, target, use_git=True, ruling=None):
    """Put a held script into the app. W245 (addition C): when the record's only difference from the human's approved text is in
    notes and comments, the THE HUMAN-APPROVED.csv row takes the new hash first (adopt_notes_only_hash), so the checks below see no
    drift; if the release then refuses, the CSV is put back as it was. See _cmd_release for the release itself."""
    csv_path = Path(lib.dir) / APPROVED_CSV
    csv_old = csv_path.read_bytes() if csv_path.is_file() else None
    adopt_notes_only_hash(lib, script_path(target), "release")
    try:
        return _cmd_release(lib, target, use_git=use_git, ruling=ruling)
    except BaseException:
        if csv_old is not None and csv_path.read_bytes() != csv_old:
            csv_path.write_bytes(csv_old)
        raise


def _cmd_release(lib, target, use_git=True, ruling=None):
    """Put a held script into the app. Everything is checked before anything is written; a refused command
    changes nothing. A record with a `## Approved-By:` line is refused, unless `ruling` (--owner-ruling) names the human's
    ruling, when the released spoken lines differ from the draft's, compared without pause cues: release re-renders
    the text (heading, slug line, no pause cues), and the cues are dropped for every script, so a plain re-release of
    the human's text needs no ruling (o10 W239; o10L373 S4).
    Returns (import file, position, slug, new folder)."""
    p = script_path(target)
    ruling = validate_ruling(ruling)
    refuse_if_check_fails(lib, "release")
    original = p.read_bytes()
    h, _ = parse_script(p)
    label = h.get("Label") or h.get("ID")
    if not is_kept_path(lib, p):
        raise LibraryError(OUTSIDE_FINAL % (label, "release"))
    if h.get("Stage") != "final" or not is_held(h):
        raise LibraryError("%s is not a held final script (Stage %s, Release %r); only `Release: held` scripts can "
                           "be released" % (label, h.get("Stage"), h.get("Release", "")))
    if h.get("Source"):
        raise LibraryError("%s already has Source %s" % (label, h["Source"]))
    line, tab, cat_folder = h.get("Line", ""), h.get("Tab", ""), h.get("Category", "")
    cat = lib.category_by_folder(line, tab, cat_folder)
    if not cat or not h.get("Label"):
        raise LibraryError("%s needs a category in CATEGORIES.json and a label before it can be released" % label)
    title = h.get("Title", "").strip()
    slug = slugify(title)
    if not slug:
        raise LibraryError("%s: the title %r gives an empty slug" % (label, title))
    if h.get("Slug", "").strip().lower() not in ("", "none", slug):
        raise LibraryError("%s: a held script has no slug yet, but its header Slug is %r" % (label, h.get("Slug")))
    taken = {}
    for q in lib.iter_scripts():
        if q.resolve() == p.resolve():
            continue
        qh, _ = parse_script(q)
        for v in [qh.get("Slug", "").strip()] + aliases_of(qh):
            if v and v.lower() != "none":
                taken[v.lower()] = qh.get("Label") or qh.get("ID")
    if slug in taken:
        raise LibraryError("%s: the slug %s (from the title) is already a slug or alias on %s; retitle the script"
                           % (label, slug, taken[slug]))
    reg = load_json(lib.dir / "RETIRED-SLUGS.json", {"slugs": [], "replaced_slugs_live": []})
    if slug in set(reg.get("slugs", [])) | set(reg.get("replaced_slugs_live", [])):
        raise LibraryError("%s: the slug %s is in RETIRED-SLUGS.json; a database row may still hold it, and the "
                           "import would overwrite and re-show that row. Retitle the script." % (label, slug))
    rel = import_file_name(tab, cat["app_category"], lib, cat)
    items, mf = current_import_state(lib, rel)
    if mf is not None and (mf.get("line"), mf.get("tab"), mf.get("category")) != (line, tab, cat_folder):
        raise LibraryError("%s: import file %s belongs to %s/%s/%s, not this script's category"
                           % (label, rel, mf.get("line"), mf.get("tab"), mf.get("category")))
    number = len(items) + 1
    chunk = render_chunk(original.decode("utf-8"), number, h["Type"], tab, title, slug=slug, label=label)
    # o10L373 S4 (b): the chunk drops the pause cues of every script, so the spoken lines are compared without them; a
    # plain re-release of the human's text needs no ruling, a changed spoken line still does
    ruling_entry = approval_gate(lib, h, original, (END_MARK + "\n").encode("utf-8") + chunk, "release", ruling,
                                 new_live=True, ignore_pauses=True)
    # the released record is hashed by the importer's rule, so its CSV row takes the new hash (with no ruling when the
    # spoken lines are unchanged); a row whose hash is UNKNOWN stays UNKNOWN unless a ruling is recorded
    csv_hash = None
    if h.get(APPROVED_KEY, "").strip():
        want = approval_row(lib, h)["text_sha256"].strip()
        new_hash = guard_hash_of_body(chunk, True)
        if ruling_entry or (want and want.upper() != UNKNOWN and want != new_hash):
            csv_hash = new_hash
    preamble =(lib.build_dir / (rel + ".preamble.md")).read_bytes() if mf is not None else new_preamble(tab, cat["app_category"], number_of_file(rel, cat))
    data = render_import_file(lib, preamble, list(items) + [(number, p, chunk)])      # `number` follows every live script, the withheld ones too
    entry = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "scripts": number,
             "line": line, "tab": tab, "category": cat_folder}
    new_h = dict(h)
    new_h.pop("Release", None)
    new_h.update({"Slug": slug, "Source": rel, "Source-position": str(number), "App": "live"})
    if ruling_entry:
        new_h = with_ruling(new_h, ruling_entry)
    new_h = with_name_lines(new_h, record_name(p))      # step 7: the three step-6 lines always come from the name
    want = lib.dir / expected_rel_dir(new_h)
    old_dir = p.parent
    moving = want.resolve() != old_dir.resolve()
    if moving and want.exists():
        raise LibraryError("destination already exists: %s" % want)
    # all checks passed; now write
    manifest_path = lib.build_dir / "MANIFEST.json"
    old_manifest = manifest_path.read_bytes()
    side = lib.build_dir / (rel + ".preamble.md")
    side_existed = side.exists()
    pn = record_name(p)
    if pn.in_app:
        raise LibraryError("%s: %s is already the text in the app; nothing was written" % (label, p.name))
    # W235 step 5 (review o10L318 M4): the released text keeps its version number and takes ` (in app)`. W245 (review
    # o10L439 section 2): no plain copy of the draft is written to versions/ any more, since it was the same text under a
    # second name. The `(in app)` file keeps the draft's `# title` and `> Authored:` lines instead (write_script keep_from),
    # and a later `revise` saves this text in versions/ at the moment it is replaced.
    # W238: the NAME is the Label once the script has one (a final script always has one), so a released text is never
    # filed under the old `<TYPE>-<ID>` name.
    nkey = name_key_of(h, pn)
    ftitle = carried_title(h, p, nkey)
    new_p = old_dir / version_name(nkey, pn.version, pn.author, in_app=True, title=ftitle)
    # o10L463: the draft record is the cued source of the released text (the chunk drops its pause cues and notes). When
    # its Source-File already names a text filed in this folder (approve of a given text: the record is a copy of it), the
    # (in app) file keeps that link and the record is dropped as before. Otherwise the record moves to versions/ under its
    # plain name, `v<N> <AUTHOR>.md` beside `v<N> <AUTHOR> (in app).md`, and the (in app) file's Source-File names it.
    # A plain file of that name already in versions/ (an older text of the same number, such as the July text a notes-only
    # approval compares with) is left alone, and the record is dropped as before W245's change was undone here.
    # o10L464 (o10 W245): a record that carries nothing the app text lacks (draft_is_app_text) is not kept either; it would
    # be the same text under a second name. It is dropped as before o10L463, and a Source-File line naming the record
    # itself goes with it; any other Source-File line is carried as before o10L463.
    keep_src = None
    drop_src = ()
    if linked_source(old_dir, h) is None or linked_source(old_dir, h).resolve() == p.resolve():
        if draft_is_app_text(original.decode("utf-8"), chunk):
            if linked_source(old_dir, h) is not None:
                new_h.pop("Source-File", None)
                drop_src = ("Source-File",)
        else:
            keep_src = version_name(nkey, pn.version, pn.author, title=ftitle)
            if (old_dir / "versions" / keep_src).exists():
                keep_src = None
            else:
                new_h["Source-File"] = keep_src
    moved = False
    csv_old = None
    try:
        write_script(new_p, new_h, chunk, keep_from=original, drop=drop_src)
        if not side_existed:
            side.parent.mkdir(parents=True, exist_ok=True)
            side.write_bytes(preamble)
        doc = load_json(manifest_path, {"files": {}})
        doc["files"][rel] = entry
        save_json(manifest_path, doc)
        if csv_hash is not None:
            # o10L452: a hash-only move keeps a notes-only note naming the hash the column now holds
            csv_old = record_ruling_in_csv(lib, h, csv_hash, ruling_entry, note_for="release")
        # o10L359 S1: move the folder while the tracked draft record is still in it. `git mv` of a folder refuses
        # ("fatal: bad source") when a tracked file inside is already gone, so the record is dropped after the move.
        if moving:
            want.parent.mkdir(parents=True, exist_ok=True)
            move_path(lib, old_dir, want, use_git=use_git)
            moved = True
        if new_p != p:
            here = want if moving else old_dir
            if keep_src is not None:
                (here / "versions").mkdir(exist_ok=True)
                (here / p.name).replace(here / "versions" / keep_src)
            else:
                (here / p.name).unlink()
    except BaseException:
        # o10L363 N1: when the folder had already moved (the draft record could not be deleted), move it back first, so
        # the undo below happens in the folder's old place. If it cannot be moved back, undo in the new place.
        home = old_dir
        if moved:
            try:
                move_path(lib, want, old_dir, use_git=use_git)
            except (LibraryError, OSError):
                home = want
        p = home / p.name
        new_p = home / new_p.name
        if new_p != p and new_p.exists():
            new_p.unlink()
        if p.parent.exists():
            p.write_bytes(original)
        if not side_existed and side.exists():
            side.unlink()
        manifest_path.write_bytes(old_manifest)
        if csv_old is not None:
            (lib.dir / APPROVED_CSV).write_bytes(csv_old)
        raise
    if moving:
        parent = old_dir.parent
        while parent != lib.dir and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
    return rel, number, slug, want


# o10L463 (o3 W59, filing AT-Sl-N-01-030 v5 THE HUMAN): every `(in app)` record has the cued draft it was built from (pause cues,
# the human's `{{{ }}}` notes, `~~struck~~` text) filed in its script folder, and its `## Source-File:` line names that file. The
# link is what `check` reads to leave the pair out of its same-text warning (a cued draft and the app text built from it
# are expected to have the same spoken text). A revise or a release that changes the text drops the OLD Source-File value
# (o10L343 M1: it named the old text's source) and writes the new text's own.

def script_folder_of(path):
    """The script folder of a text file: its own folder, or the parent of the versions/ folder it sits in."""
    d = Path(path).parent
    return d.parent if d.name == "versions" else d


def in_script_folder(path, folder):
    """True when `path` sits directly in the script folder `folder` or in its versions/ folder."""
    d = Path(os.path.abspath(str(Path(path).parent)))
    f = Path(os.path.abspath(str(folder)))
    return d == f or d == f / "versions"


def linked_source(folder, header):
    """The file in `folder` or `folder/versions` that `header`'s `## Source-File:` line names, or None (no line, a path,
    or a name no file there has)."""
    name = (header.get("Source-File") or "").strip()
    if not name or "/" in name or "\\" in name:
        return None
    for d in (Path(folder), Path(folder) / "versions"):
        f = d / name
        if f.is_file():
            return f
    return None


def app_spoken(chunk):
    """The spoken part of a chunk from render_chunk: everything after the blank line that ends its heading block (LF bytes).
    revise, release and file-source all take the app text's spoken lines from here, so their comparisons agree."""
    return chunk[chunk.index(b"\n\n") + 2:]


def draft_is_app_text(text, chunk):
    """o10L464 (o10 W245, the human 2026-10-11: two files with the same text showed in the Reader as two versions): True when the
    draft `text` carries nothing the app text lacks, that is its lines below its one END SCRIPT HEADER line are, byte for
    byte, the spoken lines of `chunk` (render_chunk of that draft, the render `file-source` compares with the record's body),
    with LF line endings and blank lines at either end set aside. A pause cue, a `{{{ }}}` note, `~~struck~~` text, a `#`
    line, a blockquote or any other line the render drops makes it False, and such a draft is filed as a cued source."""
    lines = re.split(r"\r?\n", text)
    at = [i for i, l in enumerate(lines) if HEADER_END_RE.match(l)]
    if len(at) != 1:
        return False
    below = "\n".join(lines[at[0] + 1:]).strip("\n").encode("utf-8")
    return below == app_spoken(chunk).strip(b"\n")


def refuse_record_copy(text, from_file, p, h):
    """Refuse a `--from` text that is a copy of an `(in app)` record: its body holds the `<!-- slug: -->` marker. The app
    text has lost the pause cues and notes of the draft it was built from, so a revise from it would file a text without
    them as the new cued source. The reason names the file to copy instead (the record's linked Source-File, else the
    newest plain text in versions/)."""
    lines = to_lf(text.encode("utf-8")).decode("utf-8").split("\n")
    at = [i for i, l in enumerate(lines) if HEADER_END_RE.match(l)]
    body = lines[at[0] + 1:] if at else lines
    if not any(SLUG_START_RE.match(l) for l in body):
        return
    label = h.get("Label") or h.get("ID")
    folder = script_folder_of(p)
    src = linked_source(folder, h)
    if src is None:
        plain = [q for q in (folder / "versions").glob("*.md") if (lambda r: r is not None and not r.in_app
                 and r.kind in RECORD_KINDS)(record_name(q))] if (folder / "versions").is_dir() else []
        plain.sort(key=lambda q: (record_name(q).version or 0, q.name))
        src = plain[-1] if plain else None
    raise LibraryError(
        "%s: %s is a copy of an (in app) record (its text holds the <!-- slug: --> marker), not a draft. The app text has "
        "no pause cues or notes, and revise files its --from text as the new text's cued source draft, so it takes the "
        "draft the app text was built from: %s, with the changes made in a copy of it. Nothing was written."
        % (label, Path(from_file).name,
           ("copy %s" % src.relative_to(folder).as_posix()) if src is not None
           else "this script has none filed, so write the change into its last draft"))


def revise_unreleased(lib, p, original, h, from_file, new_title=None, ruling=None):
    """revise for a script in 1-drafts/ or 2-ready-for-owner/ (Stage draft or ready). The text in from_file becomes the
    record file `<NAME> v<N+1> <AUTHOR>.md`; the old record file moves to versions/ under its own name (nothing in
    versions/ is overwritten). The header fields this tool owns (Title, Type, Line, Tab, Category, Stage, ID, Slug,
    Working-Slug, Aliases ...) carry over from the old record file; the lines of from_file that are not such fields stay
    as they are. The new file has its own Version-Of, Version and Author, and no Origin or Source-File of the old one
    (o10L343 S2, o10L356). No other file in the folder is touched, and no import file, baseline hash or stamp changes.
    Returns the new record file bytes, or None when nothing would change."""
    label = h.get("Label") or h.get("ID")
    slug = h.get("Slug", "").strip()
    slugged = bool(slug) and slug.lower() != "none"
    title = (new_title if new_title is not None else h.get("Title", "")).strip()
    if title != h.get("Title", ""):
        if slugged:
            raise LibraryError("%s has the app slug %s; a new title would no longer give that slug. Its title stays "
                               "%r. Nothing was written." % (label, slug, h.get("Title")))
        if not title:
            raise LibraryError("%s: the new title is empty" % label)
        if not h.get("Working-Slug", "").strip():
            raise LibraryError("%s has no Working-Slug, so its folder name comes from its title and a new title "
                               "would misfile it. Nothing was written." % label)
    text = to_lf(Path(from_file).read_bytes()).decode("utf-8")
    refuse_record_copy(text, from_file, p, h)
    draft_type = draft_header_type(text)
    if draft_type in ("AT", "SC") and draft_type != h.get("Type"):
        raise LibraryError("%s is type %s but the text in %s says Type %s; revise keeps the script's type (change it "
                           "with `file --type`). Nothing was written." % (label, h.get("Type"), from_file, draft_type))
    fields = {k: h[k] for k in HEADER_ORDER if k in h}
    fields["Title"] = title
    fields.pop("Origin", None)               # the origin of the OLD text is not the new text's (o10L343 S2)
    # o10L463: a from_file filed in this script's folder (approve of a given text) is the new record's source, so the record
    # links it even when the old record had no Source-File; a release later carries the link into the (in app) file.
    if "Source-File" in fields or in_script_folder(from_file, p.parent):
        fields["Source-File"] = Path(from_file).name
    head, _, body = split_head(text)
    pn = record_name(p)
    # o10L343 S2 (same rule as `revise`): the new text is a new version by a new author. The step-6 lines the old record
    # file or a copy of it in from_file carries (`Version`, `Author`, `Origin`, `Source-File`) are not its own. Since
    # step 7 the three lines are always written (o10L348 found that a record without them gained none).
    head = [ln for ln in head
            if not (KEY_RE.match(ln) and (KEY_RE.match(ln).group(1) == "Origin"
                                          or (KEY_RE.match(ln).group(1) == "Source-File" and "Source-File" not in fields)))]

    def made(values):
        hd = set_fields(head, dict(fields, **values))
        for i, ln in enumerate(hd):          # a loose file's own `## Source:` / `## Label:` mean something else here
            m = KEY_RE.match(ln)
            if m and m.group(1) in RESERVED_KEYS and m.group(1) not in fields:
                hd[i] = _rename_key(ln, m.group(1))
        return "\n".join(hd + [END_MARK] + body).encode("utf-8")

    nkey, fam = name_key_of(h, pn), family_of(h, pn)       # W238: the NAME follows the Label; Version-Of stays the family key
    new = made(record_header_values(nkey, next_version_number(p.parent), revision_author(text), fam))
    # the same text again changes nothing: compare with the version and author the record file already has
    if made(record_header_values(nkey, pn.version, pn.author, fam)) == original:
        return None
    ruling_entry = approval_gate(lib, h, original, new, "revise", ruling)
    if ruling_entry:
        fields = dict(fields, **{RULING_KEY: with_ruling(h, ruling_entry)[RULING_KEY]})
        new = made(record_header_values(nkey, next_version_number(p.parent), revision_author(text), fam))
    csv_old = None
    # W235 step 5: a record file named `<NAME> v<N> <AUTHOR>.md`. The new text is the next version number, the header
    # moves to it, and the old record file moves to versions/ under its own name.
    new_p = p.parent / version_name(nkey, next_version_number(p.parent), revision_author(text),
                                    title=carried_title(dict(h, Title=title), p, nkey))   # the NEW title (o10L434, F2)
    saved = p.parent / "versions" / p.name
    if saved.exists() or new_p.exists():
        raise LibraryError("%s: %s or %s already exists; nothing was written"
                           % (label, saved.relative_to(p.parent).as_posix(), new_p.name))
    made_dir = not saved.parent.exists()
    try:
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_bytes(original)
        if new_p != p:
            p.unlink()
        new_p.write_bytes(new)
        if ruling_entry:
            csv_old = record_ruling_in_csv(lib, h, guard_hash(new), ruling_entry)
        errors, _ = cmd_check(lib, write=False)
        if errors:
            raise LibraryError("revising %s would leave the library with %d problem(s), so nothing was changed: %s"
                               % (label, len(errors), "; ".join(errors[:5])))
    except BaseException:
        if new_p != p and new_p.exists():
            new_p.unlink()
        p.write_bytes(original)
        if saved.exists():
            saved.unlink()
        if made_dir and saved.parent.is_dir() and not any(saved.parent.iterdir()):
            saved.parent.rmdir()
        if csv_old is not None:
            (lib.dir / APPROVED_CSV).write_bytes(csv_old)
        raise
    if title != h.get("Title", ""):
        cmd_check(lib)  # INDEX.md shows the title; regenerate it the way `check` does
    return new


def authored_line_of(text):
    """The FM4 line (`> Authored: <date time> | Model: <id> | By: <lane>`) in the header position of `text` (the first
    non-blank line after its `# title`, above the END SCRIPT HEADER line), or None. training_header.py defines the
    position and the format; this module does not read them a second way."""
    lines = to_lf(text.encode("utf-8")).decode("utf-8").split("\n")   # read line by line whatever the endings below
    _, slot_i, _ = training_header.provenance_slot(lines)
    if slot_i is None:
        return None
    line = lines[slot_i].strip()
    return line if training_header.parse_provenance_line(line) is not None else None


def authored_line_anywhere(text):
    """The first FM4 line anywhere in the header of `text` (the lines above its END SCRIPT HEADER line), in the FM4
    position or not (for example under no `# title`), else None. `authored_line_of` finds it only in the position."""
    for ln in to_lf(text.encode("utf-8")).decode("utf-8").split("\n"):
        if ln.strip() == END_MARK:
            return None
        if ln.lstrip().startswith(">") and training_header.parse_provenance_line(ln.strip()) is not None:
            return ln.strip()
    return None


def stamp_authored_line(path, line, title):
    """Set the FM4 line in the header of the script file at `path`, and change nothing else in the file (FM4 in
    SCRIPT-CHECKLIST-5-FORMAT-AND-GATE.md: "If you revise a version in place, update the date"). An Authored line
    already in the header position is replaced. A header with a `# title` and no Authored line gets the line directly
    under the title. A library header that has neither (the `## Key:` fields only, as every live script's does today)
    gets `# <title>` and the line at its very top, the form the 15 draft scripts that carry an Authored line already
    have; without a `# title` above the first `##` line training_header cannot find the line. Every other header line
    and the whole body stay byte for byte as they were. Returns True when the file changed."""
    data = Path(path).read_bytes()
    marker = (END_MARK + "\n").encode("utf-8")
    i = data.find(marker)
    if i < 0:
        raise LibraryError("%s: no %s line" % (path, END_MARK))
    lines = data[:i].decode("utf-8").split("\n")      # the last element is "" (the header ends with a newline)
    title_i, slot_i, _ = training_header.provenance_slot(lines)
    if slot_i is not None and training_header.parse_provenance_line(lines[slot_i]) is not None:
        if lines[slot_i] == line:
            return False
        lines[slot_i] = line
    elif title_i is not None:
        lines.insert(title_i + 1, line)
    else:
        lines[0:0] = ["# " + title, line]
    Path(path).write_bytes("\n".join(lines).encode("utf-8") + data[i:])
    return True


def set_title_line(path, title):
    """Change the `# title` line in the header of the script file at `path` to `# <title>`, when the header has one,
    and change nothing else (no line is added when there is none: a header without a `# title` is left alone).
    Returns True when the file changed. `revise --title` calls it so the `# title` line keeps matching `## Title:`."""
    data = Path(path).read_bytes()
    marker = (END_MARK + "\n").encode("utf-8")
    i = data.find(marker)
    if i < 0:
        raise LibraryError("%s: no %s line" % (path, END_MARK))
    lines = data[:i].decode("utf-8").split("\n")
    title_i, _, _ = training_header.provenance_slot(lines)
    if title_i is None:
        return False
    cr = "\r" if lines[title_i].endswith("\r") else ""
    new = "# " + title + cr
    if lines[title_i] == new:
        return False
    lines[title_i] = new
    Path(path).write_bytes("\n".join(lines).encode("utf-8") + data[i:])
    return True


def cmd_revise(lib, target, from_file, new_title=None, ruling=None, approving=False):
    """Revise a script (see _cmd_revise). W245 addition C, wired in by review o10L450 (open point 3): revise refuses an approved
    record whose text drifted from THE HUMAN-APPROVED.csv the same way release does (refuse_if_check_fails reports the drift), so it
    runs the same notes-only rule first: when the record differs from the human's approved text only in notes and comments, the row
    takes the new hash (adopt_notes_only_hash) and no ruling is needed for that. If the revise then refuses, the CSV is put back
    as it was. A change to the spoken text still needs --owner-ruling (approval_gate)."""
    csv_path = Path(lib.dir) / APPROVED_CSV
    csv_old = csv_path.read_bytes() if csv_path.is_file() else None
    adopt_notes_only_hash(lib, script_path(target), "revise")
    try:
        return _cmd_revise(lib, target, from_file, new_title=new_title, ruling=ruling, approving=approving)
    except BaseException:
        if csv_old is not None and csv_path.read_bytes() != csv_old:
            csv_path.write_bytes(csv_old)
        raise


def _cmd_revise(lib, target, from_file, new_title=None, ruling=None, approving=False):
    """Replace the text of a script that is already in the app with the approved draft in from_file. Keeps the
    heading's number and tag and the slug line (or the NO SLUG line) exactly, keeps the old text in versions/, and
    updates the import file's baseline hash. A title change is refused for a script matched to its row by title.
    A record with a `## Approved-By:` line (the human approved its text) is refused a change to its spoken text unless
    `ruling` (--owner-ruling) names the human's ruling (o10 W239).
    Returns the new body (bytes), or None when nothing would change.

    o10 W244: the text of a live script changes only in 3-final (an approved script); a live script in 1-drafts or
    2-ready-for-owner is refused with a reason naming `approve`. A draft or ready script that is not in the app is still
    revised here (that changes no import file). `approving` is for `approve`, which has already decided to approve."""
    p = script_path(target)
    refuse_if_check_fails(lib, "revise")
    original = p.read_bytes()
    h, body = parse_script(p)
    label = h.get("Label") or h.get("ID")
    ruling = validate_ruling(ruling)
    if h.get("Stage") in ("draft", "ready") and not is_live(h):
        return revise_unreleased(lib, p, original, h, from_file, new_title, ruling)
    if is_live(h) and not approving and not is_kept_path(lib, p):
        raise LibraryError(OUTSIDE_FINAL % (label, "revise"))
    if not is_live(h) or not h.get("Source"):
        raise LibraryError("%s is not a live script (in the app, in any stage but retired), a draft or a ready-for-the human "
                           "script; a held one is released with `release`, with its text already in the file" % label)
    m = HEADING_RE.match(body.split(b"\n", 1)[0].decode("utf-8").rstrip())
    if not m:
        raise LibraryError("%s: the body does not start with a '## Script N [TYPE-TAB]: Title' line" % label)
    number = int(m.group(1))
    slug = h.get("Slug", "").strip()
    slugless = slug.lower() in ("", "none")
    match_row = None
    if slugless:
        for l in split_lines_keep(body)[:6]:
            rm = NO_SLUG_ROW_RE.match(l.decode("utf-8", "replace"))
            if rm:
                match_row = rm.group(1)
                break
        if not match_row:
            raise LibraryError("%s has no slug and no NO SLUG BY DESIGN line to keep" % label)
    title = (new_title if new_title is not None else h.get("Title", "")).strip()
    if title != h.get("Title", ""):
        if slugless:
            raise LibraryError("%s is matched to its database row by title; a new title would insert a duplicate "
                               "row. Its title stays %r until the row has a slug." % (label, h.get("Title")))
        if not slugify(title):
            raise LibraryError("%s: the title %r gives an empty slug" % (label, title))
    from_bytes = Path(from_file).read_bytes()
    text = from_bytes.decode("utf-8")
    refuse_record_copy(text, from_file, p, h)
    draft_type = draft_header_type(text)
    if draft_type in ("AT", "SC") and draft_type != h.get("Type"):
        raise LibraryError("%s is type %s but the draft in %s says Type %s; revise keeps the script's type (change it "
                           "with `file --type`). Nothing was written." % (label, h.get("Type"), from_file, draft_type))
    chunk = render_chunk(text, number, h["Type"], h["Tab"], title, slug=None if slugless else slug,
                         match_row_id=match_row, label=label)
    # keep the old head (heading, slug marker or NO SLUG note, up to the first blank line) byte for byte; only
    # a changed title rewrites the heading line. The new spoken text follows it.
    crlf = b"\r\n" in body
    bl = re.search(rb"\r?\n\r?\n", body)
    new_spoken = app_spoken(chunk)
    if bl is None:
        raise LibraryError("%s: the body has no blank line after its heading, so its head cannot be kept" % label)
    head = body[:bl.end()]
    if crlf:
        new_spoken = new_spoken.replace(b"\n", b"\r\n")
    if title != h.get("Title", ""):
        first_new = chunk.split(b"\n", 1)[0] + (b"\r" if crlf else b"")
        head = first_new + head[len(body.split(b"\n", 1)[0]):]
    tail = TAIL_RE.search(body).group(1)
    new_body = (head + new_spoken).rstrip(b"\r\n") + tail
    if new_body == body:
        return None
    ruling_entry = approval_gate(lib, h, original, (END_MARK + "\n").encode("utf-8") + new_body, "revise", ruling)
    new_entry = rebaselined_entry(lib, h["Source"], p, new_body)
    # all checks passed; now write
    manifest_path = lib.build_dir / "MANIFEST.json"
    old_manifest = manifest_path.read_bytes()
    csv_old = None
    pn = record_name(p)
    # W235 step 5 (review o10L318 M4): the text in the app is `<NAME> v<N> <AUTHOR> (in app).md`. The new text takes
    # the next version number; it is written as `<NAME> v<N+1> <AUTHOR> (in app).md`. W245 (review o10L439 section 2): no plain
    # copy of the approved text is written beside it (it was the same text under a second name); the text is saved in
    # versions/ when a later revise replaces it. The old (in app) file moves to versions/ without the suffix, unless a plain
    # file with its version number is already there (the same text: the (in app) file takes its number).
    n_new = next_version_number(p.parent)
    author = revision_author(text)
    nkey, fam = name_key_of(h, pn), family_of(h, pn)       # W238: the NAME follows the Label; Version-Of stays the family key
    ftitle = carried_title(dict(h, Title=title), p, nkey)   # the NEW title when --title changes it (o10L434, F2)
    new_p = p.parent / version_name(nkey, n_new, author, in_app=True, title=ftitle)
    same_n = [q for q in (p.parent / "versions").glob("*.md")
              if (lambda r: r is not None and r.version == pn.version and not r.in_app)(record_name(q))] \
        if (p.parent / "versions").is_dir() else []
    saved_old = None if same_n else p.parent / "versions" / version_name(
        pn.name_key, pn.version, pn.author, title=training_header.script_title(p.name))
    # o10L463: the cued draft in from_file is filed as the new text's source: a text already in this script folder (or its
    # versions/) is linked where it is; any other is copied to versions/ under the new version's plain name, with its own
    # step-6 lines (the pair `v<N+1> <AUTHOR>.md` + `v<N+1> <AUTHOR> (in app).md`; a later revise keeps the plain file for
    # that version through `same_n` above). The new record's Source-File names it.
    # o10L464 (o10 W245): a draft from outside the folder that carries nothing the app text lacks (draft_is_app_text: no
    # pause cue, note or any other line the render drops) is not copied, since the copy would be the same text under a
    # second name; the new record then has no Source-File line, as before o10L463.
    if in_script_folder(from_file, p.parent):
        saved_src, src_name = None, Path(from_file).name
    elif draft_is_app_text(text, chunk):
        saved_src, src_name = None, None
    else:
        saved_src = p.parent / "versions" / version_name(nkey, n_new, author, title=ftitle)
        src_name = saved_src.name
    if new_p.exists() or (saved_old is not None and saved_old.exists()):
        raise LibraryError("%s: %s already exists; nothing was written" % (label, new_p.name))
    if saved_src is not None and (saved_src.exists() or saved_src == saved_old):
        raise LibraryError("%s: versions/%s already exists, so the cued draft cannot be filed under it; nothing was written"
                           % (label, saved_src.name))
    old_p = p
    made_versions = not (p.parent / "versions").exists()
    try:
        if saved_src is not None:
            saved_src.parent.mkdir(parents=True, exist_ok=True)
            saved_src.write_bytes(stamp_version_lines(from_bytes, record_header_values(nkey, n_new, author, fam)))
        if saved_old is not None:
            saved_old.parent.mkdir(parents=True, exist_ok=True)
            saved_old.write_bytes(original)
        h2 = dict(h)
        h2["Title"] = title
        # o10L363 M3 (after o10L359 S4): the new text's own Opening wins; a text that declares none (4 of the 140 live SC
        # revises) keeps the old record's valid Opening, so its timing does not change. No valid Opening at all: none.
        # o10L463: an old Opening line that is not a valid value (a prose note, as AT-Sl-N-01-030 used it) is not a line
        # revise owns; when neither text declares a valid Opening it stays as it was, instead of being dropped.
        old_opening_line = h2.pop("Opening", None)
        old_opening = h.get("Opening", "").strip().lower()
        opening = declared_opening(text) or (old_opening if old_opening in ("negative", "positive") else "")
        if opening:
            h2["Opening"] = opening
        elif old_opening_line is not None and old_opening_line.strip():
            h2["Opening"] = old_opening_line
        # o10L343 M1: the step-6 lines describe the OLD record file. The new file is a new version by a new author,
        # and its text did not come from the old text's origin or source file. o10L463: its own source is the cued draft
        # filed above, which its Source-File line names.
        h2.update(record_header_values(nkey, n_new, author, fam))
        if src_name is not None:
            h2["Source-File"] = src_name
        if ruling_entry:
            h2 = with_ruling(h2, ruling_entry)
        write_script(new_p, h2, new_body, keep_from=original,
                     drop=("Origin",) + (() if src_name is not None else ("Source-File",))
                     + (() if "Opening" in h2 else ("Opening",)))
        if new_p != p:
            p.unlink()
        p = new_p
        authored = authored_line_of(text)
        if authored:
            stamp_authored_line(p, authored, title)
        elif authored_line_of(original.decode("utf-8")):
            print("WARNING: %s: the revision in %s has no Authored line the tool can find (FM4: `> Authored: ...` as "
                  "the first line after the `# title`), so the live file keeps its old Authored line, whose date no "
                  "longer describes this text. Add the line to the revision and revise again, or edit the date."
                  % (p, from_file), file=sys.stderr)
        else:
            print("WARNING: %s: the revision in %s has no Authored line the tool can find, and the live file has none "
                  "%s; FM4 asks for one (`> Authored: <date time> | Model: <id> | By: <lane>` as the first line after "
                  "the `# title`). Add it to the revision and revise again."
                  % (p, from_file, "in the FM4 position either" if authored_line_anywhere(original.decode("utf-8"))
                     else "either"), file=sys.stderr)
        if title != h.get("Title", ""):
            set_title_line(p, title)       # the `# title` line, when there is one, follows `## Title:`
        doc = load_json(manifest_path, {"files": {}})
        doc["files"][h["Source"]] = new_entry
        save_json(manifest_path, doc)
        if ruling_entry:
            csv_old = record_ruling_in_csv(lib, h, guard_hash_of_body(new_body, True), ruling_entry)
    except BaseException:
        if new_p != old_p and new_p.exists():
            new_p.unlink()
        old_p.write_bytes(original)
        if saved_old is not None and saved_old.exists():
            saved_old.unlink()
        if saved_src is not None and saved_src.exists():
            saved_src.unlink()
        vdir = old_p.parent / "versions"
        if made_versions and vdir.is_dir() and not any(vdir.iterdir()):
            vdir.rmdir()
        manifest_path.write_bytes(old_manifest)
        if csv_old is not None:
            (lib.dir / APPROVED_CSV).write_bytes(csv_old)
        raise
    if title != h.get("Title", ""):
        cmd_check(lib)  # INDEX.md shows the title; regenerate it the way `check` does
    return new_body


def stamp_header_field(data, key, value):
    """`data` with the header line `## <key>: <value>` set: an existing line of that key is changed in place, else the line is
    inserted directly above the END SCRIPT HEADER line (with that line's line ending). Every other byte stays."""
    marker = END_MARK.encode("utf-8")
    i = data.find(marker)
    if i < 0:
        raise LibraryError("the text has no END SCRIPT HEADER line")
    head, rest = data[:i], data[i:]
    nl = b"\r\n" if rest.split(b"\n", 1)[0].endswith(b"\r") else b"\n"
    want = ("## %s: %s" % (key, value)).encode("utf-8")
    lines = head.split(b"\n")
    for j, ln in enumerate(lines):
        if re.match(rb"^##\s+" + key.encode("utf-8") + rb":", ln.lstrip(b"\xef\xbb\xbf")):
            lines[j] = want + (b"\r" if ln.endswith(b"\r") else b"")
            return b"\n".join(lines) + rest
    return head + want + nl + rest


def differing_body_lines(a_body, b_body):
    """[(first, last)] body line ranges (1-based) of `a_body` (bytes) where it differs from `b_body`; an insertion in b shows as
    the line before which it goes (first > last). Line numbers only, never text."""
    import difflib
    a = to_lf(a_body).decode("utf-8", "replace").split("\n")
    b = to_lf(b_body).decode("utf-8", "replace").split("\n")
    return [(i1 + 1, i2) for tag, i1, i2, _j1, _j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
            if tag != "equal"]


def cmd_file_source(lib, target, from_file):
    """o10L463: file `from_file` as the cued source draft of an `(in app)` record that has none (the draft its app text was built
    from, with its pause cues and notes). Refused unless a revise of the record from `from_file` would change nothing: the
    draft's spoken text (strikes and notes removed, rendered as revise renders it) gives the record's body byte for byte. The
    draft is copied to versions/ as `<NAME> v<N> <AUTHOR>.md` beside `<NAME> v<N> <AUTHOR> (in app).md` (a draft already in the
    script folder is linked where it is) and the record's `## Source-File:` line names it. A draft that is the app text and
    nothing more (draft_is_app_text) is refused too, wherever it is (o10L465). Only that header line changes in
    the record, so its approval hash, the import files and THE HUMAN-APPROVED.csv stay as they are. Returns the filed path."""
    p = script_path(target)
    refuse_if_check_fails(lib, "file-source")
    original = p.read_bytes()
    h, body = parse_script(p)
    label = h.get("Label") or h.get("ID")
    pn = record_name(p)
    if pn is None or not pn.in_app or not is_live(h) or not h.get("Source"):
        raise LibraryError("%s: %s is not an (in app) record of a live script; file-source files the cued draft of the text "
                           "the app plays" % (label, p.name))
    have = linked_source(p.parent, h)
    if have is not None:
        raise LibraryError("%s already names its source draft: %s (its Source-File line); nothing was written"
                           % (label, have.relative_to(p.parent).as_posix()))
    from_bytes = Path(from_file).read_bytes()
    text = from_bytes.decode("utf-8")
    refuse_record_copy(text, from_file, p, h)
    m = HEADING_RE.match(body.split(b"\n", 1)[0].decode("utf-8").rstrip())
    if not m:
        raise LibraryError("%s: the body does not start with a '## Script N [TYPE-TAB]: Title' line" % label)
    slug = h.get("Slug", "").strip()
    match_row = None
    if slug.lower() in ("", "none"):
        for l in split_lines_keep(body)[:6]:
            rm = NO_SLUG_ROW_RE.match(l.decode("utf-8", "replace"))
            if rm:
                match_row = rm.group(1)
                break
    chunk = render_chunk(text, int(m.group(1)), h["Type"], h["Tab"], h.get("Title", "").strip(),
                         slug=None if slug.lower() in ("", "none") else slug, match_row_id=match_row, label=label)
    crlf = b"\r\n" in body
    bl = re.search(rb"\r?\n\r?\n", body)
    if bl is None:
        raise LibraryError("%s: the body has no blank line after its heading" % label)
    new_spoken = app_spoken(chunk)
    if crlf:
        new_spoken = new_spoken.replace(b"\n", b"\r\n")
    rendered = (body[:bl.end()] + new_spoken).rstrip(b"\r\n") + TAIL_RE.search(body).group(1)
    if rendered != body:
        ranges = differing_body_lines(body, rendered)
        raise LibraryError("%s: the spoken text of %s is not the app text of %s, so it is not its source; nothing was written. "
                           "The record's body differs at body line(s) %s (counted from the line after the END SCRIPT HEADER "
                           "marker)." % (label, Path(from_file).name, p.name,
                                         ", ".join(("%d" % a) if a == b else ("%d-%d" % (a, b)) if b > a
                                                   else ("before %d" % a) for a, b in ranges)))
    if draft_is_app_text(text, chunk):
        # o10L465 (o10 W245): a draft that is the app text and nothing more is not a cued source; filing it would make the
        # identical pair the human objected to (a filed copy that says nothing the app text does not). Same test as revise/release.
        raise LibraryError("%s: %s is the app text of %s with no pause cue, note or other line the app text lacks, so it is not "
                           "a cued source draft; filing it would make a copy identical to the app text. Nothing was written. "
                           "File the draft that carries the cues, or leave the record with no Source-File line."
                           % (label, Path(from_file).name, p.name))
    nkey, fam = name_key_of(h, pn), family_of(h, pn)
    if in_script_folder(from_file, p.parent):
        dest, write = Path(from_file), False
    else:
        dest = p.parent / "versions" / version_name(nkey, pn.version, pn.author, title=carried_title(h, p, nkey))
        write = True
        if dest.exists():
            raise LibraryError("%s: versions/%s already exists; nothing was written" % (label, dest.name))
    new_record = stamp_header_field(original, "Source-File", dest.name)
    if guard_hash(new_record, guard_is_live(h, new_record)) != guard_hash(original, guard_is_live(h, original)):
        raise LibraryError("%s: setting Source-File would change the approval hash; nothing was written" % label)
    made_dir = not dest.parent.exists()
    try:
        if write:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(stamp_version_lines(from_bytes, record_header_values(nkey, pn.version, pn.author, fam)))
        p.write_bytes(new_record)
        errors, _ = cmd_check(lib, write=False)
        if errors:
            raise LibraryError("filing the source of %s would leave the library with %d problem(s), so nothing was changed: "
                               "%s" % (label, len(errors), "; ".join(errors[:5])))
    except BaseException:
        p.write_bytes(original)
        if write and dest.exists():
            dest.unlink()
        if made_dir and dest.parent.is_dir() and not any(dest.parent.iterdir()):
            dest.parent.rmdir()
        raise
    return dest


def declared_opening(text):
    """The `## Opening:` value ("negative" or "positive") in a text's library header (the lines above its END SCRIPT HEADER
    line), or "" when it has none or the value is anything else."""
    for ln in text.replace("\r\n", "\n").split("\n"):
        if ln.strip() == END_MARK:
            break
        m = KEY_RE.match(ln)
        if m and m.group(1) == "Opening":
            v = m.group(2).strip().lower()
            return v if v in ("negative", "positive") else ""
    return ""


def draft_header_type(text):
    """The `## Type:` value in a draft's library header (the lines above its END SCRIPT HEADER line), or ''."""
    for ln in text.replace("\r\n", "\n").split("\n"):
        if ln.strip() == END_MARK:
            break
        m = KEY_RE.match(ln)
        if m and m.group(1) == "Type":
            return m.group(2).strip().upper()
    return ""


STATE_IN_STEP = "in step"
STATE_LIBRARY_AHEAD = "library ahead (a release or revise waits for its app PR)"
STATE_APP_DIFFERS = "app differs from the library (a hand edit in the app, or a stamp edited by hand)"
STATE_NO_STAMP = "differs; the app has no LIBRARY-BUILD.json, so which side moved cannot be told"


def cmd_drift(lib, source):
    """Read only. Build every import file in memory (after the check rules) and compare each with the app's file.
    Returns [(import file, state)], one per file that either side has. The app's LIBRARY-BUILD.json (the sha256 the
    app held when it was last built from the library) tells the two kinds of difference apart: an app file that
    still matches its stamp while the library has moved on is `library ahead`; one that does not match its own
    stamp was changed in the app."""
    files, _, _ = build_files(lib)
    app_names = set(collect_import_files(source))
    stamp = source.stamp() if hasattr(source, "stamp") else None
    stamped = stamp.get("files", {}) if stamp else {}
    at_commit = None  # {import file: sha256} the library produced at the stamp's workspace_commit; loaded once
    out = []
    for rel in sorted(set(files) | app_names):
        if rel not in app_names:
            # the stamp lists it: the app had it and has removed it; no stamp entry: the library added it since
            out.append((rel, STATE_APP_DIFFERS if rel in stamped else STATE_LIBRARY_AHEAD))
        elif rel not in files:
            # the library no longer builds it (its last script moved or was retired): if the app still holds exactly
            # what the last build wrote, the library is ahead; any other file, or one edited since, is an app difference
            theirs = source.read(rel)
            was = stamped.get(rel, {}).get("sha256")
            out.append((rel, STATE_LIBRARY_AHEAD if was and was == sha256_lf(theirs) else STATE_APP_DIFFERS))
        else:
            theirs = source.read(rel)
            if theirs == files[rel]:
                out.append((rel, STATE_IN_STEP))
            elif stamp is None:
                out.append((rel, STATE_NO_STAMP))
            else:
                if at_commit is None:
                    wc = stamp.get("workspace_commit", "")
                    at_commit = manifest_hashes_at(lib, wc)
                    if not at_commit:
                        print("NOTE: the workspace repo cannot read the stamp's workspace_commit %s (a shallow clone, or "
                              "a commit that was never merged), so the stamp proves nothing here: files reported as "
                              "'app differs' may really be 'library ahead'" % (wc or "(none named)"))
                # library ahead only when the app file is exactly what the library produced at the stamped
                # commit (and the stamp says so too); a file and its stamp edited together fails the first test
                made = at_commit.get(rel)
                same = made is not None and made in (hashlib.sha256(theirs).hexdigest(), sha256_lf(theirs))
                recorded = stamped.get(rel, {}).get("sha256")
                out.append((rel, STATE_LIBRARY_AHEAD if same and recorded == sha256_lf(theirs) else STATE_APP_DIFFERS))
    if hasattr(source, "retired_list"):
        out.append(("voice_scripts/" + RETIRED_NAME, retired_list_state(lib, source, stamp)))
    return out


def retired_slugs_in(raw):
    """The slugs in the bytes of a RETIRED-SLUGS.json (the `slugs` key only; `note` and any other key are ignored, as
    the app's importer ignores them), without repeats, in file order. None when the bytes are not that shape."""
    try:
        slugs = json.loads(raw.decode("utf-8"))["slugs"]
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    if not isinstance(slugs, list) or not all(isinstance(x, str) and x for x in slugs):
        return None
    return list(dict.fromkeys(slugs))


def retired_list_at(lib, commit):
    """The slugs script-library/RETIRED-SLUGS.json held when the workspace repo was at `commit` (an empty list when
    that commit had no such file), as build --app would have written them. None when the commit cannot be read or the
    file is damaged. Only the slugs are returned, not rendered bytes, so a later edit to RETIRED_NOTE changes nothing."""
    if not commit:
        return None
    ok = subprocess.run(["git", "-C", str(lib.root), "rev-parse", "--verify", commit + "^{commit}"],
                        capture_output=True, creationflags=flags())
    if ok.returncode != 0:
        return None
    rel = (Path(lib.dir).resolve().relative_to(Path(lib.root).resolve()) / RETIRED_NAME).as_posix()
    r = subprocess.run(["git", "-C", str(lib.root), "show", "%s:%s" % (commit, rel)], capture_output=True,
                       creationflags=flags())
    if r.returncode != 0:
        return []                           # the commit is readable and held no list file: an empty list
    return retired_slugs_in(r.stdout)


def retired_list_state(lib, source, stamp):
    """Drift state of the app's voice_scripts/RETIRED-SLUGS.json against the library's list. A missing file counts as
    an empty list (the importer reads it that way), so it is in step while the library's list is empty. A stamp
    written before the list existed has no `retired_slugs` entry; that is read as 'never built', not as an error."""
    built = retired_slug_list(lib)
    theirs = source.retired_list()
    if theirs is None:
        if not built:
            return STATE_IN_STEP
        if stamp is None:
            return STATE_NO_STAMP
        return STATE_APP_DIFFERS if "retired_slugs" in stamp else STATE_LIBRARY_AHEAD
    # Only the slugs are compared (never the note text or the bytes): the importer reads nothing else, and the note
    # in RETIRED_NOTE may be reworded later, which must not turn every older build into "app differs".
    theirs_slugs = retired_slugs_in(theirs)
    if theirs_slugs is not None and theirs_slugs == built:
        return STATE_IN_STEP
    if stamp is None:
        return STATE_NO_STAMP
    entry = stamp.get("retired_slugs") or {}
    made = retired_list_at(lib, stamp.get("workspace_commit", ""))
    # Library ahead: the app's slugs are exactly what the library held at the stamped commit (the app was built and
    # not edited), and the stamp records a list of that length.
    if entry and made is not None and theirs_slugs == made and entry.get("count") == len(made):
        return STATE_LIBRARY_AHEAD
    return STATE_APP_DIFFERS


def manifest_hashes_at(lib, commit):
    """{import file: sha256} from _build/MANIFEST.json as the workspace repo held it at `commit`. Build --strict only
    writes files whose hash equals that entry, so it is what the library produced then. {} when the commit or the
    file cannot be read (the stamp then proves nothing, and every file that differs is reported as an app difference)."""
    if not commit:
        return {}
    rel = (Path(lib.build_dir).resolve().relative_to(Path(lib.root).resolve()) / "MANIFEST.json").as_posix()
    r = subprocess.run(["git", "-C", str(lib.root), "show", "%s:%s" % (commit, rel)], capture_output=True,
                       creationflags=flags())
    if r.returncode != 0:
        return {}
    try:
        return {k: v.get("sha256") for k, v in json.loads(r.stdout.decode("utf-8")).get("files", {}).items()}
    except ValueError:
        return {}


def cmd_add_line(lib, name, today, code=None):
    reg = lib.lines()
    slug = slugify(name)
    if not slug:
        raise LibraryError("a line needs a name with letters or digits in it")
    if any(l["slug"] == slug for l in reg["lines"]):
        raise LibraryError("line %r already exists" % slug)
    used = [l["code"] for l in reg["lines"]] + list(reg.get("retired_codes", []))
    code = check_chosen_code(code, used) if code else assign_code(name, used)
    reg["lines"].append({"slug": slug, "name": name, "code": code, "added": today})
    save_json(lib.dir / "LINES.json", reg)
    return slug, code


# --------------------------------------------------------------------------- command line

# --------------------------------------------------------------------------- approve (o10 W244)
#
# `approve <path> --owner-ruling <ref>` is the one way a script gets into the app (the human, 2026-10-11: only fully approved scripts
# reach the customer; the OrchDoc Reader's Approve button runs it). It moves the whole script folder to script-library/3-final/
# (same Line/Tab/Category path), makes the approved text the app text (a live script: `revise` logic; a script not yet in the
# app: `release` logic; `{{{ }}}` notes are stripped by render_chunk), and writes the approval onto the approved text's header
# and into THE HUMAN-APPROVED.csv. It writes under script-library/ and rewrites the moved paths in the waypoint baseline
# (.shared/scripts/check_checklist_router_waypoint_baseline.json), as `file` and `place` do (o10 W244 ruling 3).
#
# Header shape: `## Approved-By: the human <ISO date-time> ruling:<the --owner-ruling text>`; the CSV row holds the date, the
# app_commit column `ruling:<text>`, the sha256 of the approved text (guard_hash) and the ruling in `rulings`. July's
# `the human <date> app:<hash>` headers stay as they are: an approval that already holds for the script's text is never re-written,
# so moving a July-approved script into 3-final keeps its header and its CSV app_commit (o10 W244 ruling 1). `check` accepts
# both shapes (approval_matches). A refusal's reason names the file, the line and the problem, never script text (ruling 2).
#
# Approve marks the script final (the human, 2026-10-12, relayed by o9 to o10: "Approve should mark a script final. I'll only ever
# use it for that."): the status field of the approved text's `<!-- training-version: ... -->` header line becomes `final` in
# the write that adds `## Approved-By:` (final_status_head), also when an approval that already holds is kept. A text with
# no such line gets none. The line is above END SCRIPT HEADER, so neither the approval hash nor the import files change.

RULING_TAG = "ruling:"
APPROVE_KEYS = ("ok", "folder_before", "folder_after", "approved_file", "app_change", "reason")
ISO_WHEN_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?)?$")
_BEYOND_SLEEP_RE = re.compile(r"^(?:AT|SC)-(o5|o3|tgr)-(\d+)-")
_BAKE_OFF_RE = re.compile(r"^(?:AT|SC)-bk\d+-")


def approval_matches(value, row):
    """True when the `## Approved-By:` value `value` is the one CSV row `row` asks for: exactly `the human <date> <app_commit>`
    (July's shape), or, for a row whose app_commit column is `ruling:<text>`, `the human <ISO date-time> ruling:<text>` with a
    date-time that starts with the row's date."""
    value = (value or "").strip()
    if value == approved_value(row):
        return True
    return ruling_approval_matches(value, row)


def ruling_approval_matches(value, row):
    """True when `value` is the header `approve` writes for CSV row `row`: the row's app_commit is `ruling:<text>` and the value is
    `the human <ISO date-time that starts with the row's date> ruling:<text>`. A pinned script (APPROVED_PINNED) whose approval was
    re-stamped by `approve` is held by this shape; a hand edit to a July-shaped value (`the human <date> app:<hash>`) still fails its pin."""
    value = (value or "").strip()
    app = row["app_commit"].strip()
    if app.startswith(RULING_TAG):
        m = re.match(r"^the human (\S+) (" + re.escape(app) + r")$", value)
        return bool(m) and m.group(1).startswith(row["date"].strip())
    return False


def unplaced_reason(folder, header):
    """Why an unplaced script (no Line/Tab/Category) cannot be approved yet, naming what it waits on."""
    name = Path(folder).name
    if _BAKE_OFF_RE.match(name):
        return ("it is an arm of o5's Do-It-Now bake-off, which waits on o5:D62; it is not placed in a category yet, and "
                "approving needs a place")
    m = _BEYOND_SLEEP_RE.match(name)
    if m:
        who, n = m.group(1), int(m.group(2))
        if who == "tgr" or (who == "o5" and 79 <= n <= 116) or (who == "o3" and 24 <= n <= 38):
            return ("it is one of the 65 scripts written for the beyond-sleep categories, which wait on the human's D149 (Sleep or "
                    "their own categories); it is not placed in a category yet, and approving needs a place")
    return "it is not placed yet (no Line, Tab and Category), and approving needs a place in a category"


def approve_target(lib, target):
    """(script folder, the text file given or None for a folder) for an approve target, or a LibraryError whose message is the
    plain reason it is refused: a path outside script-library, a process log, review or note file, anything that is not a
    script text."""
    t = Path(target)
    if not t.exists():
        raise LibraryError("there is no file or folder at %s" % target)
    base = Path(os.path.abspath(str(lib.dir)))
    try:
        rel = Path(os.path.abspath(str(t))).relative_to(base)
    except ValueError:
        raise LibraryError("%s is outside script-library, so it cannot be approved" % target)
    if not rel.parts or rel.parts[0] not in STAGE_DIRS:
        raise LibraryError("%s is not in one of the script-library stage folders (1-drafts, 2-ready-for-owner, 3-final)" % target)
    if t.is_dir():
        folder, given = t, None
        if folder.name in NOT_SCRIPT_DIRS:
            folder = folder.parent
    else:
        if t.suffix.lower() != ".md":
            raise LibraryError("%s is not a script text (a .md file named like a record file)" % t.name)
        pn = training_header.parse_script_name(t.name)
        if pn.kind in ("log", "note") or pn.version is None:
            raise LibraryError("%s is a process log, a review or a note file, not a script text, so it cannot be approved" % t.name)
        if _DUPLICATE_NAME.search(t.name):
            raise LibraryError("%s is a duplicate copy, not a script text, so it cannot be approved" % t.name)
        folder, given = t.parent, t
        if folder.name in NOT_SCRIPT_DIRS:
            folder = folder.parent
    if record_file(folder) is None:
        raise LibraryError("%s holds no script record file, so it is not a script folder that can be approved" % folder)
    if given is not None and given.parent != folder and given.parent != folder / "versions":
        raise LibraryError("%s is not directly in the script folder or its versions folder" % given)
    return folder, given


def newest_text(folder):
    """The newest text of a script folder: the highest version number among the texts in the folder and its versions/ folder;
    on a tie the one in the folder, then the `(in app)` one."""
    best, key = None, None
    for d, nested in ((Path(folder), 0), (Path(folder) / "versions", 1)):
        try:
            names = sorted(os.listdir(str(d)))
        except OSError:
            continue
        for n in names:
            if not n.endswith(".md") or _DUPLICATE_NAME.search(n):
                continue
            pn = training_header.parse_script_name(n)
            if pn.version is None or pn.kind not in RECORD_KINDS:
                continue
            k = (-pn.version, nested, 0 if pn.in_app else 1, n)
            if key is None or k < key:
                best, key = d / n, k
    return best


def _file_listing(root):
    out = {}
    for d, dirs, names in os.walk(str(root)):
        dirs[:] = [x for x in dirs if x != "__pycache__"]
        for n in names:
            fp = Path(d) / n
            st = fp.stat()
            out[fp.relative_to(root).as_posix()] = (st.st_size, st.st_mtime_ns)
    return out


def approval_already_holds(lib, p):
    """True when the record file `p` already carries an approval of its current text: its `## Approved-By:` line agrees with its
    THE HUMAN-APPROVED.csv row (July's `the human <date> app:<hash>` shape or approve's `ruling:` shape) and the row's text_sha256 is the
    hash of the text."""
    data = p.read_bytes()
    h, _body = parse_script(p)
    sid = h.get("ID", "").strip()
    row = next((r for r in read_approved_csv(lib) if r["script_id"].strip() == sid), None)
    have = h.get(APPROVED_KEY, "").strip()
    return bool(row is not None and have and approval_matches(have, row)
                and row["text_sha256"].strip() == guard_hash(data, guard_is_live(h, data)))


def final_status_head(data):
    """`data` (a record file's bytes) with the status field of its training-version line set to `final`, or `data` itself when
    it has none above the END SCRIPT HEADER line or it already says final. The human, 2026-10-12 (relayed by o9 to o10): "Approve
    should mark a script final. I'll only ever use it for that." training_header.HEADER is the one reading of that line
    (`<!-- training-version: <version> | <status> [| ...] -->`, the status is its second field); only the header part of the
    file is changed, never the text below the END SCRIPT HEADER line."""
    marker = (END_MARK + "\n").encode("utf-8")
    i = data.find(marker)
    if i < 0:
        return data
    head = data[:i].decode("utf-8")
    m = training_header.HEADER.search(head)
    if not m or m.group(2).strip().lower() == "final":
        return data
    return (head[:m.start(2)] + "final" + head[m.end(2):]).encode("utf-8") + data[i:]


def _approve_stamp(lib, p, ruling, when, text_changed):
    """Write the approval onto the record file `p` and into THE HUMAN-APPROVED.csv. Returns True when the approval was written.
    o10 W244 ruling 1: an approval that already holds for the current text (approval_already_holds) is never rewritten, whatever
    --owner-ruling or --approved-at says, so a July row keeps its `app:<hash>` header and app_commit when its script is moved into
    3-final. Only a text that changed in this run (`text_changed`), or one with no matching approval, gets a new stamp.
    o10L461 (the human, 2026-10-12): the record's training-version status becomes `final` in the same write as the approval
    (final_status_head); an approval that already holds is not re-stamped, but its status is still set to final."""
    if not text_changed and approval_already_holds(lib, p):
        data = p.read_bytes()
        marked = final_status_head(data)
        if marked != data:
            p.write_bytes(marked)
        return False                          # approved already, with this text: no new approval to write
    original = p.read_bytes()
    data = final_status_head(original)
    h, body = parse_script(p)
    new_hash = guard_hash(data, guard_is_live(h, data))
    sid = h.get("ID", "").strip()
    rows = read_approved_csv(lib)
    row = next((r for r in rows if r["script_id"].strip() == sid), None)
    day = when[:10]
    value = "the human %s %s%s" % (when, RULING_TAG, ruling)
    base = (row["rulings"].strip() if row is not None else h.get(RULING_KEY, "").strip())
    rulings = base if (ruling in base) else ((base + "; " if base else "") + "%s (%s)" % (ruling, day))
    h2 = dict(h)
    h2[APPROVED_KEY] = value
    h2[RULING_KEY] = rulings
    csv_old = (lib.dir / APPROVED_CSV).read_bytes() if (lib.dir / APPROVED_CSV).exists() else None
    try:
        write_script(p, h2, body, keep_from=data)
        new_row = {"script_id": sid, "label": h.get("Label", "").strip(), "date": day, "app_commit": RULING_TAG + ruling,
                   "source_audit": "", "text_sha256": new_hash, "rulings": rulings,
                   "note": "approved with file_script.py approve"}
        if row is None:
            rows.append(new_row)
        else:
            row.update({"date": day, "app_commit": RULING_TAG + ruling, "text_sha256": new_hash, "rulings": rulings})
        write_approved_csv(lib, render_approved_csv(rows))
    except BaseException:
        p.write_bytes(original)
        if csv_old is not None:
            (lib.dir / APPROVED_CSV).write_bytes(csv_old)
        raise
    return True


def _approve_apply(lib, folder, given, ruling, when, use_git):
    """The approval itself, on `lib` (the real library or a copy). Returns a result dict with paths relative to script-library
    (folder_before, folder_after, approved_file as POSIX strings) or raises LibraryError. Rolls the library back to how it was
    when any step fails."""
    rel_before = Path(os.path.abspath(str(folder))).relative_to(Path(os.path.abspath(str(lib.dir)))).as_posix()
    p = record_file(folder)
    h, _ = parse_script(p)
    label = h.get("Label") or h.get("ID") or folder.name
    if h.get("Stage") == "retired" or rel_before.split("/")[0] == STAGES["retired"]:
        raise LibraryError("%s is retired; a retired script is not approved" % label)
    if not has_placement(h):
        raise LibraryError("%s cannot be approved yet: %s" % (label, unplaced_reason(folder, h)))
    was_kept = is_kept_path(lib, p) and is_live(h) and bool(h.get("Source"))
    a = Path(given) if given is not None else newest_text(folder)
    if a is None:
        raise LibraryError("%s has no script text to approve" % label)
    a = Path(a)
    same_as_record = a.resolve() == p.resolve()
    a_bytes = a.read_bytes()
    a_is_record_text = same_as_record
    if not same_as_record:
        # a text in versions/ or a loose rewrite whose bytes equal the record's is the record's text
        a_is_record_text = a_bytes == p.read_bytes()
    saved = _snapshot(lib, folder)
    try:
        notes_only = adopt_notes_only_hash(lib, p, "approve")   # W245 addition C: a notes-only change to the human's text is not drift
        text_changed = False                  # the app text changes (a revise, or a release of a script not in the app)
        revised = False                       # the record's text itself changed (a revise): a new approval is stamped
        # 1. the approved text becomes the record's text (a script not yet in the app: revised as a draft; a live one: revised
        #    in the app text). A script already final and released is revised in 3-final.
        if not a_is_record_text:
            res = cmd_revise(lib, p, a, ruling=ruling, approving=True)
            text_changed = revised = res is not None
            p = record_file(folder)
        h, _ = parse_script(p)
        # 2. into 3-final
        if h.get("Stage") != "final" or not is_kept_path(lib, p):
            dest = cmd_file(lib, p, "final", use_git=use_git)
            folder = Path(dest)
            p = record_file(folder)
            h, _ = parse_script(p)
        # 3. a final script that is not in the app yet is released
        if is_held(h) and not h.get("Source"):
            _rel, _number, _slug, dest = cmd_release(lib, p, use_git=use_git, ruling=ruling)
            folder = Path(dest)               # the slug the release gives can rename the folder
            p = record_file(folder)
            h, _ = parse_script(p)
            text_changed = True
        if p is None or not is_kept_path(lib, p):
            raise LibraryError("%s: after the steps the record is not in 3-final; stopped" % label)
        # 4. the approval on the approved text's header and in the CSV
        # (a release alone does not change the approved text, so an approval that holds for it is kept: o10 W244 ruling 1)
        marked = final_status_head(p.read_bytes()) != p.read_bytes()   # the training-version status becomes final (o10L461)
        wrote = _approve_stamp(lib, p, ruling, when, revised)
        errors, _n = cmd_check(lib, write=False)
        if errors:
            raise LibraryError("approving %s would leave the library with %d problem(s), so nothing was changed: %s"
                               % (label, len(errors), "; ".join(errors[:5])))
    except BaseException:
        _rollback(lib, saved, use_git)
        raise
    _discard(saved)
    h, _ = parse_script(p)
    if was_kept:
        app_change = "text-changed" if text_changed else "none"
    else:
        app_change = "added"
    where = Path(os.path.abspath(str(p))).relative_to(Path(os.path.abspath(str(lib.dir)))).as_posix()
    result = {"folder_before": rel_before, "folder_after": Path(os.path.abspath(str(p.parent))).relative_to(
        Path(os.path.abspath(str(lib.dir)))).as_posix(), "approved_file": where, "app_change": app_change}
    if app_change == "none" and not wrote and rel_before == result["folder_after"] and notes_only:
        result["reason"] = ("%s was already approved and in the app with this text; only notes or comments changed since the human's "
                            "approval, so %s took the new hash (no ruling recorded)%s" % (label, APPROVED_CSV,
                            "; its training-version status was set to final" if marked else ""))
    elif app_change == "none" and not wrote and rel_before == result["folder_after"]:
        result["reason"] = ("%s was already approved and in the app with this text; %s" % (label, "only its training-version "
                            "status was set to final" if marked else "nothing was written"))
    elif app_change == "none":
        result["reason"] = "%s is in the app already; its approval was recorded" % label
    elif app_change == "added":
        result["reason"] = "%s is approved, in 3-final, and its text is now in the app" % label
    else:
        result["reason"] = "%s is approved and the app text is now the approved text" % label
    return result


def _changed_files(before, after):
    """Paths (relative to script-library/) that were added, removed or rewritten between two _file_listing results."""
    return sorted({k for k in set(before) | set(after) if before.get(k) != after.get(k)})


def _abs(lib, rel):
    return os.path.abspath(str(lib.dir / rel)).replace("\\", "/")


def _snapshot(lib, folder):
    """A copy of what an approve may change, to put back when a step fails: the script folder, the _build folder, THE HUMAN-APPROVED.csv,
    the label counters, the retired list, the log-note table and INDEX.md. The set of files under script-library/ is also kept, so
    a folder or file a failed step made is removed."""
    tmp = Path(tempfile.mkdtemp(prefix="approve-snap-"))
    shutil.copytree(str(folder), str(tmp / "folder"))
    if lib.build_dir.is_dir():
        shutil.copytree(str(lib.build_dir), str(tmp / "_build"))
    singles = {}
    for n in (APPROVED_CSV, "LABEL-COUNTERS.json", RESERVED_NAME, RETIRED_NAME, LOG_NOTE_ORIGINS, "INDEX.md", "CATEGORIES.json", "LINES.json"):
        f = lib.dir / n
        singles[n] = f.read_bytes() if f.is_file() else None
    return {"tmp": tmp, "folder": Path(folder), "singles": singles, "listing": _file_listing(lib.dir),
            "registries": save_registries(lib)}


def _rollback(lib, saved, use_git):
    """Put the library back as _snapshot found it. Staged renames of a git mv are unstaged for the two folders (a pathspec
    `git reset`, never a hard reset)."""
    folder = saved["folder"]
    now = _file_listing(lib.dir)
    old = saved["listing"]
    # remove every folder that holds script files this run created or moved
    for rel in sorted(set(now) - set(old)):
        f = lib.dir / rel
        try:
            if f.is_file():
                f.unlink()
        except OSError:
            pass
    if folder.exists():
        shutil.rmtree(str(folder), ignore_errors=True)
    folder.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(str(saved["tmp"] / "folder"), str(folder))
    if (saved["tmp"] / "_build").is_dir():
        shutil.rmtree(str(lib.build_dir), ignore_errors=True)
        shutil.copytree(str(saved["tmp"] / "_build"), str(lib.build_dir))
    for n, data in saved["singles"].items():
        f = lib.dir / n
        if data is None:
            if f.exists():
                f.unlink()
        else:
            f.write_bytes(data)
    restore_registries(saved["registries"])
    # empty folders a failed move left behind
    for rel in sorted({str(Path(r).parent) for r in set(now) - set(old)}, key=len, reverse=True):
        d = lib.dir / rel
        while d != lib.dir and d.is_dir() and not any(d.iterdir()):
            d.rmdir()
            d = d.parent
    if use_git:
        specs = [str(folder)] + sorted({str((lib.dir / Path(r)).parent) for r in set(now) - set(old)})
        subprocess.run(["git", "-C", str(lib.root), "reset", "-q", "--"] + specs, capture_output=True, creationflags=flags())
    _discard(saved)


def _discard(saved):
    shutil.rmtree(str(saved["tmp"]), ignore_errors=True)


def approve_result(ok, reason="", folder_before="", folder_after="", approved_file="", app_change="none"):
    return {"ok": bool(ok), "folder_before": folder_before, "folder_after": folder_after, "approved_file": approved_file,
            "app_change": app_change, "reason": reason}


WAYPOINT_NOTE = ("the waypoint baseline (%s) names the new path in %d entr%s")


def _scrub_reason(reason, lib, target):
    """o10 W244 ruling 2: a refusal reason never quotes script text. The messages name the file, the line and the problem;
    this is the guard behind them: any double-brace token, and any line of 12 or more characters from a text in the script's
    folder (or its versions/ folder) that appears in the reason, is replaced by a placeholder."""
    reason = TOKEN_RE.sub("[token]", reason)
    try:
        t = Path(target)
        folder = t if t.is_dir() else t.parent
        if folder.name in NOT_SCRIPT_DIRS:
            folder = folder.parent
        base = Path(os.path.abspath(str(lib.dir)))
        Path(os.path.abspath(str(folder))).relative_to(base)       # only texts inside the library are read
    except (ValueError, OSError):
        return reason
    pieces = set()
    for d in (folder, folder / "versions"):
        try:
            names = os.listdir(str(d))
        except OSError:
            continue
        for n in names:
            f = d / n
            if n.endswith(".md") and f.is_file():
                try:
                    text = f.read_bytes().decode("utf-8", "replace")
                except OSError:
                    continue
                for line in re.split(r"\r?\n", text):
                    line = line.strip()
                    if len(line) >= 12 and not line.startswith("## ") and not line.startswith("<!--"):
                        pieces.add(line)
    for piece in sorted(pieces, key=len, reverse=True):
        if piece in reason:
            reason = reason.replace(piece, "[script text]")
    return reason


def cmd_approve(lib, target, ruling, dry_run=False, use_git=True, approved_at=None):
    """See _cmd_approve. o10 W244 ruling 3: a moved script's paths in check_checklist_router's waypoint baseline
    (.shared/scripts/check_checklist_router_waypoint_baseline.json) are always rewritten, as `file` and `place` do; the reason
    says in how many entries. That file and script-library/ are the only things approve writes."""
    wp = Path(os.path.abspath(str(lib.root))) / WAYPOINT_BASELINE
    wp_before = wp.read_bytes() if wp.is_file() else None
    try:
        out = _cmd_approve(lib, target, ruling, dry_run, use_git, approved_at)
    except Exception as e:                     # one JSON line on every path: an unexpected error is a refusal too
        out = approve_result(False, "approve stopped on an unexpected error (%s); see file_script.py" % type(e).__name__)
    if not out["ok"]:
        out["reason"] = _scrub_reason(out["reason"], lib, target)
        return out
    n = out.pop("_waypoint_entries", 0)
    wp_after = wp.read_bytes() if wp.is_file() else None
    out["_waypoint_changed"] = wp_after != wp_before
    if n:
        out["reason"] += "; " + (WAYPOINT_NOTE % (WAYPOINT_BASELINE, n, "y" if n == 1 else "ies"))
    return out


def _cmd_approve(lib, target, ruling, dry_run=False, use_git=True, approved_at=None):
    """Approve the script at `target` (its folder, or a text in it): see the section comment above. Returns the result dict
    (APPROVE_KEYS) and never raises for a refusal; ok False carries a plain reason. dry_run runs the whole approval on a copy
    of script-library/ (and of the waypoint baseline) and writes nothing in the real tree. Without use_git the real run makes
    no git move; the caller commits."""
    try:
        ruling = validate_ruling(ruling)
        if not ruling:
            raise LibraryError("--owner-ruling needs a reference to the human's ruling (an OrchDoc entry or message, or the Reader's "
                               "approve line)")
        if approved_at is None:
            approved_at = datetime.datetime.now().astimezone().isoformat(timespec="minutes")
        if not ISO_WHEN_RE.match(approved_at):
            raise LibraryError("--approved-at must be an ISO date or date-time such as 2026-10-11T15:45:00-07:00, got %r" % approved_at)
        folder, given = approve_target(lib, target)
        rel = Path(os.path.abspath(str(folder))).relative_to(Path(os.path.abspath(str(lib.dir)))).as_posix()
        gv = None if given is None else Path(os.path.abspath(str(given))).relative_to(Path(os.path.abspath(str(lib.dir)))).as_posix()
        # the whole approval runs once on a copy of the library (and of the waypoint baseline); the real tree is touched only if
        # that worked
        tmp = Path(tempfile.mkdtemp(prefix="approve-copy-"))
        try:
            shutil.copytree(str(lib.dir), str(tmp / "script-library"), ignore=shutil.ignore_patterns("__pycache__"))
            wp = Path(os.path.abspath(str(lib.root))) / WAYPOINT_BASELINE
            if wp.is_file():
                (tmp / WAYPOINT_BASELINE).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(str(wp), str(tmp / WAYPOINT_BASELINE))
            clib = Lib(tmp)
            mark = len(WAYPOINT_WRITES)
            r = _approve_apply(clib, clib.dir / rel, None if gv is None else clib.dir / gv, ruling, approved_at, False)
            entries = sum(WAYPOINT_WRITES[mark:])
        finally:
            shutil.rmtree(str(tmp), ignore_errors=True)
        changed = None
        if not dry_run:
            before = _file_listing(lib.dir)
            mark = len(WAYPOINT_WRITES)
            r = _approve_apply(lib, folder, given, ruling, approved_at, use_git)
            entries = sum(WAYPOINT_WRITES[mark:])
            cmd_check(lib)                    # INDEX.md follows the move, as `release` does
            changed = _changed_files(before, _file_listing(lib.dir))
        out = approve_result(True, r["reason"], _abs(lib, r["folder_before"]), _abs(lib, r["folder_after"]),
                             _abs(lib, r["approved_file"]), r["app_change"])
        out["_waypoint_entries"] = entries
        if changed is not None:
            out["_changed"] = changed
        return out
    except LibraryError as e:
        return approve_result(False, str(e))
    except (OSError, ValueError, KeyError, UnicodeError, csv.Error) as e:
        return approve_result(False, "%s: %s" % (type(e).__name__, e))


def commit_approval(lib, result, label_hint):
    """Commit what a real approve changed (result['_changed'], paths relative to script-library/) on the current branch."""
    paths = ["script-library/" + c for c in result.get("_changed", [])]
    if not paths:
        return None
    if result.get("_waypoint_changed") and (lib.root / WAYPOINT_BASELINE).is_file():
        paths.append(WAYPOINT_BASELINE)
    # the two script folders as a whole: a git move done in the middle of the run (before a rename to the `(in app)` name) staged
    # a path that no longer exists, and only a folder pathspec clears it
    root = Path(os.path.abspath(str(lib.root)))
    for key in ("folder_before", "folder_after"):
        try:
            d = Path(os.path.abspath(result[key])).relative_to(root).as_posix()
        except (KeyError, ValueError):
            continue
        if d not in paths:
            paths.append(d)

    def git(*a):
        return subprocess.run(["git", "-C", str(lib.root)] + list(a), capture_output=True, creationflags=flags())
    for x in paths:
        if (lib.root / x).exists():
            git("add", "--", x)
        else:
            git("rm", "-r", "-q", "--cached", "--ignore-unmatch", "--", x)
    r = git("commit", "-q", "-m", "approve %s: %s" % (label_hint, result["app_change"]), "--only", "--", *paths)
    if r.returncode != 0:
        raise LibraryError("git commit failed: %s" % r.stderr.decode("utf-8", "replace").strip())
    return True


RULING_HELP = ("the reference to the human's ruling (an OrchDoc entry or message) that lets this run change the text of a script "
               "that carries `## Approved-By:`; required for such a change, written into the new record's "
               "`## Approved-Ruling:` line and THE HUMAN-APPROVED.csv")


RESERVE_HELP = ("give a script its real library label BEFORE it is written (o10L475): takes the next number of the category (the "
                "counter moves, so nothing else can take it) and records label, working title, who and the date in "
                "script-library/RESERVED-LABELS.json. Put `## Label: <label>` in the script's header; `adopt`, `place` and `file` "
                "keep it and mark the reservation used. They refuse, changing nothing, a label whose number was never given out "
                "(not reserved and above the category's counter), a reserved label a second script tries to use, a label that "
                "`unreserve` released, and an open reservation whose type, line, tab or category the header has but differs "
                "from the reservation; one the header lacks is filled in from the reservation. `check` lists open reservations "
                "(information only). Prints the label, or one JSON line with --json.")
UNRESERVE_HELP = ("drop a reservation that no script carries. The number stays spent: LABEL-COUNTERS.json is not touched and the "
                  "number is never given out again; adopt, place and file refuse the released label. Refused for a label that "
                  "is not reserved or that a script has used.")


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(WORKSPACE_ROOT),
                    help="<your-workspace> root (the folder that holds script-library/)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("split")
    sp.add_argument("--git-repo", help="the product-app repo: split from the files git stores at --rev (the source of truth)")
    sp.add_argument("--rev", default="HEAD", help="commit to split from, with --git-repo (default HEAD)")
    sp.add_argument("--path", default="voice_scripts/Working", help="folder inside the repo that holds the import files")
    sp.add_argument("--working", help="split from a plain folder on disk instead (a Windows checkout may have CRLF that git does not)")
    bp = sub.add_parser("build")
    bp.add_argument("--out", help="write the import files into this folder")
    bp.add_argument("--app", help="the app repo's top folder: write voice_scripts/Working/** and voice_scripts/LIBRARY-BUILD.json "
                                  "(implies --strict; refuses uncommitted changes in script-library/ or file_script.py)")
    bp.add_argument("--strict", action="store_true", help="exit non-zero if a file differs from the baseline hash")
    yp = sub.add_parser("sync-bodies", help="pull body-text changes for live scripts from the app's git blobs; refuses any move")
    yp.add_argument("--git-repo", required=True, help="the product-app repo")
    yp.add_argument("--rev", required=True, help="the commit to read the import files from")
    yp.add_argument("--path", default="voice_scripts/Working")
    yp.add_argument("--accept-app-edit", action="store_true",
                    help="required: this copies text from the app into the library, the reverse of the intended direction")
    yp.add_argument("--owner-ruling", metavar="REF", help=RULING_HELP)
    rp = sub.add_parser("release", help="put a held script into the app (draft text -> import-file chunk, appended to its category's file)")
    rp.add_argument("path")
    rp.add_argument("--no-git", action="store_true")
    rp.add_argument("--owner-ruling", metavar="REF", help=RULING_HELP)
    vp = sub.add_parser("revise", help="replace the text of a script that is already in the app, or of a draft / ready-for-the human script (the old record file goes to versions/)",
                        description="revise replaces the text the app plays: on a live script the next build carries the new text to the app. "
                                    "To add a candidate version of a script without changing the app, do not use revise: run "
                                    "`file_script.py place <text> --rewrite-of <LABEL>`. It adds the text to that script's folder as its "
                                    "next version, beside the (in app) record, without changing the app.")
    vp.add_argument("path")
    vp.add_argument("--from", dest="from_file", required=True, help="the approved draft with the new text")
    vp.add_argument("--title", help="a new title (refused for a script matched to its row by title)")
    vp.add_argument("--owner-ruling", metavar="REF", help=RULING_HELP)
    sp_ = sub.add_parser("file-source", help="file the cued draft an (in app) record was built from, when it has none (o10L463)",
                         description="Copies the draft to the script's versions/ folder as <NAME> v<N> <AUTHOR>.md beside the "
                                     "(in app) record of that version and sets the record's Source-File line to it. Refused "
                                     "unless the draft's spoken text renders to the record's body byte for byte; a refusal "
                                     "names the differing body lines, never their text.")
    sp_.add_argument("path", help="the (in app) record file, or its script folder")
    sp_.add_argument("--from", dest="from_file", required=True, help="the cued draft (pause cues, notes)")
    dp = sub.add_parser("drift", help="read only: compare the library's build with the app's import files at a commit")
    dp.add_argument("--git-repo", required=True, help="the product-app repo")
    dp.add_argument("--rev", default="origin/main")
    dp.add_argument("--path", default="voice_scripts/Working")
    rb = sub.add_parser("rebaseline", help="one time: move the baseline hashes in _build/MANIFEST.json to the 3-final-only build "
                                           "(refuses any difference that is not the withheld scripts; dry run unless --apply)")
    rb.add_argument("--apply", action="store_true", help="write MANIFEST.json; without it nothing changes")
    rn = sub.add_parser("repair-notes-only-note",
                        help="correct a THE HUMAN-APPROVED.csv note whose notes-only entry names a hash the row no longer holds "
                             "(a release moved it after the notes-only rule ran; o10L452); dry run unless --apply")
    rn.add_argument("--apply", action="store_true", help="write THE HUMAN-APPROVED.csv; without it nothing changes")
    ap2 = sub.add_parser("approve", help="approve a script: move its folder to 3-final and put the approved text in the app; prints one JSON line",
                         description="the human's rule (2026-10-11): only fully approved scripts reach the customer. approve moves the script folder "
                                     "(any text in it, or the folder) to 3-final, makes the approved text the app text, writes `## Approved-By: "
                                     "the human <ISO time> ruling:<ruling>` on it and updates THE HUMAN-APPROVED.csv (an approval that already holds for the text, "
                                     "such as a July `app:<hash>` one, is kept as it is), and sets the status of the text's "
                                     "`training-version` header line to final. It writes only under script-library/ and "
                                     "the moved paths in .shared/scripts/check_checklist_router_waypoint_baseline.json. "
                                     "Prints one JSON line: ok, folder_before, folder_after, approved_file, app_change (none|added|text-changed), reason.")
    ap2.add_argument("path")
    ap2.add_argument("--owner-ruling", metavar="TEXT", help="required: the reference to the human's approval, written into the header and the CSV "
                                                          "(a missing one is refused with the usual JSON line)")
    ap2.add_argument("--approved-at", metavar="ISO", help="when the human approved (default: now); used only when a new approval is written; an "
                                                          "approval that already holds for the script's text is never re-written")
    ap2.add_argument("--no-git", action="store_true", help="make no commit and no git move (the Reader commits)")
    ap2.add_argument("--dry-run", action="store_true", help="print the same JSON line and write nothing")
    cp = sub.add_parser("check")
    cp.add_argument("--no-write", action="store_true", help="do not rewrite INDEX.md (for hooks and self-heal)")
    cp.add_argument("--require-app-line", action="store_true",
                    help="a final script with a Source but no `## App: live` line is a problem (off until the line is on all live scripts)")
    cp.add_argument("--require-new-layout", action="store_true",
                    help="a draft or ready script must be in <stage>/<line>/<tab>/<category>/ or <stage>/not-yet-placed/<TYPE>/ "
                         "(the older <stage>/<TYPE>/ folders are accepted while this is off)")
    fp = sub.add_parser("file")
    fp.add_argument("--app-change", action="store_true",
                    help="no longer needed: moving or retiring a script the app imports is allowed; accepted and ignored")
    fp.add_argument("path")
    fp.add_argument("--stage", choices=list(STAGES))
    fp.add_argument("--category")
    fp.add_argument("--tab", choices=list(TABS))
    fp.add_argument("--line")
    fp.add_argument("--no-git", action="store_true")
    fp.add_argument("--owner-ruling", metavar="REF", help=RULING_HELP)
    fp.add_argument("--rewrite-of", metavar="LABEL", help=REWRITE_OF_HELP)
    fp.add_argument("--new-label-anyway", action="store_true", help=NEW_LABEL_ANYWAY_HELP)
    fp.add_argument("--older-draft", metavar="REASON", help=OLDER_DRAFT_HELP)
    fp.add_argument("--dry-run", action="store_true", help=FOLDER_DRY_RUN_HELP)
    ap_ = sub.add_parser("adopt", help="give a loose draft/ready/retired file the library header, in place")
    ap_.add_argument("path")
    ap_.add_argument("--type", required=True, choices=["AT", "SC"])
    ap_.add_argument("--stage", required=True, choices=["draft", "ready", "retired"])
    ap_.add_argument("--id", dest="id_")
    ap_.add_argument("--title")
    ap_.add_argument("--tab", choices=list(TABS))
    ap_.add_argument("--line")
    ap_.add_argument("--category")
    ap_.add_argument("--working-slug", help="the folder's last part for a script with no app slug yet")
    ap_.add_argument("--source-file", help="the file's old name (written as its Source-File line)")
    ap_.add_argument("--author", help="who wrote this text: an orchestrator code like o5, THE HUMAN or agent (default agent); written as the Author line")
    ap_.add_argument("--version-of", help="label or id of the script this file is an older/other version of; writes only Type and Version-Of")
    ap_.add_argument("--dry-run", action="store_true")
    pp = sub.add_parser("place", help="move an adopted file into the library with git mv")
    pp.add_argument("path")
    pp.add_argument("--folder", help="folder under script-library/ (default: the one the header says)")
    pp.add_argument("--name", help="file name inside the folder, `<NAME> v<N> <AUTHOR>.md` (default: built from the header: its Label, or `<TYPE>-<ID>`, its Version and its Author)")
    pp.add_argument("--no-git", action="store_true")
    pp.add_argument("--rewrite-of", metavar="LABEL", help=REWRITE_OF_HELP)
    pp.add_argument("--new-label-anyway", action="store_true", help=NEW_LABEL_ANYWAY_HELP)
    pp.add_argument("--older-draft", metavar="REASON", help=OLDER_DRAFT_HELP)
    pp.add_argument("--dry-run", action="store_true", help=FOLDER_DRY_RUN_HELP)
    fp.add_argument("--type", choices=["AT", "SC"], dest="new_type",
                    help="change the script's type only (same category, import file and position): new label, old label kept "
                         "as an alias, body tag rewritten, import-file baseline hash updated")
    np_ = sub.add_parser("rename-to-label", help="rename the texts of every labelled script to its Label (a pure git mv; dry run unless --commit)")
    np_.add_argument("--commit", action="store_true",
                     help="apply the renames (git mv, registry paths, INDEX.md); without it nothing changes. This does not run git commit")
    np_.add_argument("--no-git", action="store_true")
    dp_ = sub.add_parser("drop-app-tags", help="take ` (in app)` off the file names of every script that is not in the import (git mv; dry run unless --apply; o10L594)")
    dp_.add_argument("--apply", action="store_true", help="apply the renames; without it nothing changes. This does not run git commit")
    dp_.add_argument("--no-git", action="store_true")
    tp_ = sub.add_parser("rename-to-title", help="put each script's title into the file names of its texts (a pure git mv; dry run unless --apply; o10L431)")
    tp_.add_argument("--apply", action="store_true",
                     help="apply the renames (git mv, sidecar folders, registry paths, INDEX.md); without it nothing changes. This does not run git commit")
    tp_.add_argument("--no-git", action="store_true")
    rv = sub.add_parser("reserve", help=RESERVE_HELP, description=RESERVE_HELP)
    rv.add_argument("--type", required=True, choices=["AT", "SC"])
    rv.add_argument("--line", required=True, help="the product line's slug (it must have a code in LINES.json)")
    rv.add_argument("--tab", required=True, choices=list(TABS))
    rv.add_argument("--category", required=True, help="the category number (04) or its folder name (04-sleep-hygiene); it must be in CATEGORIES.json for this line and tab")
    rv.add_argument("--title", required=True, help="the working title of the script; recorded with the reservation, not written into any script")
    rv.add_argument("--by", help="who is reserving it, for example o5 or o3L76")
    rv.add_argument("--dry-run", action="store_true", help="print what would be reserved and write nothing (no number is spent)")
    rv.add_argument("--json", action="store_true", help="print one JSON line instead of the label")
    ur = sub.add_parser("unreserve", help=UNRESERVE_HELP, description=UNRESERVE_HELP)
    ur.add_argument("label")
    lp = sub.add_parser("add-line")
    lp.add_argument("name")
    lp.add_argument("--code", help="a two-letter code picked by hand, like Pf (a used or retired code is refused)")
    args_in = list(sys.argv[1:] if argv is None else argv)
    if "approve" in args_in:
        # approve prints exactly one JSON line on every path, a command line argparse rejects included (the Reader parses it)
        try:
            a = ap.parse_args(args_in)
        except SystemExit as e:
            if e.code in (0, None):
                raise
            print(json.dumps({k: v for k, v in approve_result(False, "the approve command line is not valid (argparse "
                                                                   "printed the usage on stderr)").items()}, ensure_ascii=False))
            return 1
    else:
        a = ap.parse_args(args_in)
    lib = Lib(a.root)
    today = datetime.date.today().isoformat()
    try:
        if a.cmd == "split":
            if bool(a.git_repo) == bool(a.working):
                raise LibraryError("give exactly one of --git-repo (preferred) or --working")
            source = GitSource(a.git_repo, a.path, a.rev) if a.git_repo else DiskSource(a.working)
            print("splitting from " + source.label)
            summary, nfiles = cmd_split(lib, source, today)
            print("split %d scripts from %d import files: %s" % (sum(summary.values()), nfiles, json.dumps(summary, sort_keys=True)))
            errors, n = cmd_check(lib)
            print("INDEX.md written with %d rows" % n)
            return 0
        if a.cmd == "build":
            if bool(a.out) == bool(a.app):
                raise LibraryError("give exactly one of --out (a folder) or --app (the app repo's top folder)")
            if a.app:
                stamp, n = cmd_build_app(lib, a.app)
                print("wrote %d import files and voice_scripts/%s into %s (workspace commit %s)"
                      % (n, STAMP_NAME, a.app, stamp["workspace_commit"][:10]))
                return 0
            results, bad = cmd_build(lib, a.out, a.strict)
            for rel, n, status in results:
                print("%-80s %3d scripts  %s" % (rel, n, status))
            print("built %d import files into %s" % (len(results), a.out))
            return 1 if bad else 0
        if a.cmd == "approve":
            res = cmd_approve(lib, a.path, a.owner_ruling, dry_run=a.dry_run, use_git=not a.no_git, approved_at=a.approved_at)
            if res["ok"] and not a.dry_run and not a.no_git and res.get("_changed"):
                try:
                    commit_approval(lib, res, Path(res["approved_file"]).parent.name)
                except Exception as e:             # one JSON line on every path
                    res = approve_result(False, "approved, but the commit failed: %s" % _scrub_reason(str(e), lib, res["approved_file"]),
                                         res["folder_before"], res["folder_after"], res["approved_file"], res["app_change"])
            print(json.dumps({k: res[k] for k in APPROVE_KEYS}, ensure_ascii=False))
            return 0 if res["ok"] else 1
        if a.cmd == "repair-notes-only-note":
            out = cmd_repair_notes_only_note(lib, apply=a.apply)
            for sid, before, old, new, ok in out:
                print("%s: note says %s to %s, the row holds %s: %s" % (
                    sid, before, old, new, ("corrected" if a.apply else "would correct") if ok
                    else "left as it is (the record or its notes-only evidence does not match)"))
            n = sum(1 for x in out if x[4])
            print("%d note(s) %s" % (n, "corrected" if a.apply else "to correct (dry run: run again with --apply)"))
            return 0 if all(x[4] for x in out) else 1
        if a.cmd == "rebaseline":
            changes = cmd_rebaseline(lib, apply=a.apply)
            for rel, kept, held, old, new in changes:
                print("%-80s %3d kept, %3d withheld  %s -> %s" % (rel, kept, held, old[:10], new[:10]))
            print("%d import file baselines %s" % (len(changes), "moved" if a.apply else "would move (dry run: run again with --apply)"))
            return 0
        if a.cmd == "sync-bodies":
            source = GitSource(a.git_repo, a.path, a.rev)
            print("reading " + source.label)
            notes = []
            done = cmd_sync_bodies(lib, source, accept_app_edit=a.accept_app_edit, notes=notes, ruling=a.owner_ruling)
            for (rel, pos, label, title), note in zip(done, notes):
                print("changed: %s script %d  %s  %s" % (rel, pos, label, title))
                print("  " + note)
            print("%d script bodies changed; baseline hashes updated for their import files" % len(done))
            return 0
        if a.cmd == "release":
            rel, number, slug, dest = cmd_release(lib, a.path, use_git=not a.no_git, ruling=a.owner_ruling)
            print("released %s: script %d of %s, slug %s; now at %s" % (a.path, number, rel, slug, dest))
            errors, n = cmd_check(lib)
            for e in errors:
                print("PROBLEM: " + e)
            return 1 if errors else 0
        if a.cmd == "revise":
            new_body = cmd_revise(lib, a.path, a.from_file, a.title, ruling=a.owner_ruling)
            if new_body is None:
                print("nothing to change: the new text renders to the text already there")
                return 0
            # a record file named by its path has moved to versions/ (the new text has the next version number)
            hh = parse_script(script_path(a.path if Path(a.path).exists() else Path(a.path).parent))[0]
            if hh.get("Stage") in ("draft", "ready") and not is_live(hh):
                print("revised %s; the old text is in its versions/ folder; no import file or stamp changed" % a.path)
            else:
                print("revised %s; the old text is in its versions/ folder and the baseline hash is updated" % a.path)
            return 0
        if a.cmd == "file-source":
            dest = cmd_file_source(lib, a.path, a.from_file)
            print("filed %s as the source draft of %s" % (dest.name, a.path))
            return 0
        if a.cmd == "drift":
            source = GitSource(a.git_repo, a.path, a.rev)
            print("comparing the library's build with " + source.label)
            states = cmd_drift(lib, source)
            for rel, state in states:
                print("%-80s %s" % (rel, state))
            list_rel = "voice_scripts/" + RETIRED_NAME
            files = [(r, st) for r, st in states if r != list_rel]
            off = [r for r, st in files if st != STATE_IN_STEP]
            print("%d of %d import files in step; %d not" % (len(files) - len(off), len(files), len(off)))
            list_states = [st for r, st in states if r == list_rel]
            if list_states:
                print("retired slug list (%s): %s" % (RETIRED_NAME, list_states[0]))
            return 1 if off or any(st != STATE_IN_STEP for st in list_states) else 0
        if a.cmd == "check":
            errors, n = cmd_check(lib, write=not a.no_write, require_app_line=a.require_app_line,
                                  require_new_layout=a.require_new_layout)
            for e in errors:
                print("PROBLEM: " + e)
            for note in approved_report(lib, read_all(lib))[1]:
                print("NOTE: " + note)
            for n_, path_ in long_paths(lib, []):      # design section (d): `check` reports a library path over 200 characters
                print("WARNING: path of %d characters (over %d): %s" % (n_, LONG_PATH_LIMIT, path_))
            for prob_ in stale_app_tag_problems(lib):      # o10L594: build --app refuses these; check reports them
                print("WARNING: " + prob_)
            same_text = identical_text_groups(lib)         # W245: two names, one text, in one script folder
            for folder_, names_ in same_text:
                print("WARNING: %s holds the same spoken text under %d names: %s" % (folder_, len(names_), "; ".join(names_)))
            for label_, title_, by_, when_, age_ in open_reservations(lib, today):      # o10L475: information, never a problem
                print("RESERVED (open, not an error): %s  %r  by %s  reserved %s (%d day%s ago); no script carries this label yet"
                      % (label_, title_, by_ or "unknown", when_, age_, "" if age_ == 1 else "s"))
            print("checked %d scripts, %d problems; INDEX.md %s" % (
                n, len(errors), "left as it was" if a.no_write else "rewritten"))
            return 1 if errors else 0
        if a.cmd == "file":
            dest = cmd_file(lib, a.path, a.stage, a.category, a.tab, a.line,
                            use_git=not a.no_git, app_change=a.app_change, new_type=a.new_type, ruling=a.owner_ruling,
                            rewrite_of=a.rewrite_of, new_label_anyway=a.new_label_anyway,
                            older_draft=a.older_draft, dry_run=a.dry_run)
            if isinstance(dest, list):
                for o_, n_ in dest:
                    print("would move %s -> %s" % (Path(o_).relative_to(lib.dir).as_posix(), Path(n_).relative_to(lib.dir).as_posix()))
                print("dry run: %d files, nothing was changed" % len(dest))
                return 0
            print("filed at %s" % dest)
            return 0
        if a.cmd == "adopt":
            f = adopt_fields(a.type, a.stage, a.id_, a.title, a.tab, a.line, a.category,
                             a.working_slug, a.source_file, a.version_of, a.author)
            changed, notes = adopt_file_reserved(lib, a.path, f, write=not a.dry_run)
            for n in notes:
                print("NOTE: " + n)
            print("%s %s" % ("would change" if a.dry_run and changed else ("changed" if changed else "already has these fields:"), a.path))
            return 0
        if a.cmd == "place":
            dest = cmd_place(lib, a.path, a.folder, a.name, use_git=not a.no_git, rewrite_of=a.rewrite_of,
                             new_label_anyway=a.new_label_anyway, older_draft=a.older_draft, dry_run=a.dry_run)
            if isinstance(dest, list):                 # a rewrite folder (W245): the files moved, or with --dry-run, would move
                for o_, n_ in dest:
                    print("%s %s -> %s" % ("would move" if a.dry_run else "moved", Path(o_).relative_to(lib.dir).as_posix(),
                                           Path(n_).relative_to(lib.dir).as_posix()))
                print("%s: %d files%s" % ("dry run" if a.dry_run else "done", len(dest), ", nothing was changed" if a.dry_run else ""))
                return 0
            print("placed at %s" % dest)
            return 0
        if a.cmd == "drop-app-tags":
            plan_, left_ = cmd_drop_app_tags(lib, apply=a.apply, use_git=not a.no_git)
            for o_, n_ in plan_[:20]:
                print("%s -> %s" % (o_.relative_to(lib.dir).as_posix(), n_.name))
            if len(plan_) > 20:
                print("... and %d more" % (len(plan_) - 20))
            for o_, why_ in left_:
                print("LEFT: %s: %s" % (o_.relative_to(lib.dir).as_posix(), why_))
            print("%d file(s) %s, %d left" % (len(plan_), "renamed" if a.apply else "would be renamed", len(left_)))
            return 0
        if a.cmd == "rename-to-label":
            rep_ = cmd_rename_to_label(lib, commit=a.commit, use_git=not a.no_git)
            nfiles = sum(len(f) for _, f in rep_["scripts"])
            for rel, files in rep_["scripts"][:200]:
                print("%s: %d text(s)  e.g. %s -> %s" % (rel, len(files), files[0][0], files[0][1]) if files else rel)
            if len(rep_["scripts"]) > 200:
                print("... and %d more scripts" % (len(rep_["scripts"]) - 200))
            for rel, why in rep_["left"]:
                print("LEFT UNRENAMED: %s: %s" % (rel, why))
            for rel, why in rep_["skipped"]:
                print("LEFT IN PLACE: %s: %s" % (rel, why))
            for n_, path_ in rep_["long_paths"]:
                print("WARNING: path of %d characters (over %d): %s" % (n_, LONG_PATH_LIMIT, path_))
            print("%d labelled scripts: %d already named by their Label, %d to rename (%d texts, %d of them with an "
                  "Approved-By line), %d left unrenamed" % (rep_["labelled"], rep_["conformant"], len(rep_["scripts"]),
                                                            nfiles, rep_["renamed_approved"], len(rep_["left"])))
            if rep_["applied"]:
                print("renamed with git mv; registry paths rewritten: %d waypoint-baseline entries, %d LOG-NOTE-ORIGINS rows; "
                      "INDEX.md rewritten" % rep_["registries"])
            elif rep_["scripts"]:
                print("dry run: nothing changed. Run again with --commit to apply.")
            return 0
        if a.cmd == "rename-to-title":
            rep_ = cmd_rename_to_title(lib, apply=a.apply, use_git=not a.no_git)
            for rel, files in rep_["folders"][:200]:
                print("%s: %d text(s)  e.g. %s -> %s" % (rel, len(files), files[0][0], files[0][1]) if files else rel)
            if len(rep_["folders"]) > 200:
                print("... and %d more script folders" % (len(rep_["folders"]) - 200))
            for rel, why in rep_["left"]:
                print("LEFT UNRENAMED: %s: %s" % (rel, why))
            for rel, why in rep_["skipped"]:
                print("LEFT IN PLACE: %s: %s" % (rel, why))
            for rel in rep_["notitle"]:
                print("NO TITLE FOUND (header or folder slug): %s" % rel)
            for n_, path_ in rep_["long_paths"][:20]:
                print("WARNING: path of %d characters (over %d): %s" % (n_, LONG_PATH_LIMIT, path_))
            print("%d script folders to rename (%d texts), %d already carry their title, %d left unrenamed, %d without a title"
                  % (len(rep_["folders"]), rep_["texts"], rep_["conformant"], len(rep_["left"]), len(rep_["notitle"])))
            if rep_["applied"]:
                print("renamed with git mv; registry paths rewritten: %d waypoint-baseline entries, %d LOG-NOTE-ORIGINS rows; "
                      "INDEX.md rewritten" % rep_["registries"])
            elif rep_["folders"]:
                print("dry run: nothing changed. Run again with --apply to apply.")
            return 0
        if a.cmd == "reserve":
            res = cmd_reserve(lib, a.type, a.line, a.tab, a.category, a.title, by=a.by, dry_run=a.dry_run, today=today)
            if a.json:
                print(json.dumps(res, ensure_ascii=False))
            elif a.dry_run:
                print("would reserve %s for %r (dry run: nothing was written, no number was spent)" % (res["label"], res["title"]))
            else:
                print(res["label"])
            return 0
        if a.cmd == "unreserve":
            res = cmd_unreserve(lib, a.label, today=today)
            print("unreserved %s (%r); the number stays spent and is never reused" % (a.label.strip(), res.get("title", "")))
            return 0
        if a.cmd == "add-line":
            slug, code = cmd_add_line(lib, a.name, today, a.code)
            print("line %s has code %s" % (slug, code))
            return 0
    except LibraryError as e:
        print("ERROR: %s" % e)
        return 2
    except (OSError, ValueError, KeyError, UnicodeError) as e:
        print("ERROR: %s: %s" % (type(e).__name__, e))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
