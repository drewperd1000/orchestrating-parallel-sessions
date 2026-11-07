#!/usr/bin/env python3
"""
orchdoc.py - the deterministic gate for orchestrator decision docs.

<!-- route-tags: settled sub-item checkbox strike grey struck entry plate archive orchdoc decision doc lint -->

So this is not a reminder and not a document. It is a GATE plus a GENERATOR:

EXIT CODES
----------
  0  clean
  1  one or more invariants violated (the gate refuses)
  2  usage / IO error

Windows: ASCII-only output by rule. stdio is reconfigured defensively.
"""

import argparse
import json
import os
import pathlib
import re
import time
import datetime as _dt
import shutil
import subprocess
try:
    from no_window import flags
except ImportError:  # a partial delivery must not take the tool down
    def flags(extra=0):
        return extra
import tempfile
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shared_state as _shared_state  # noqa: E402
try:
    import orchdoc_upkeep as _upkeep  # noqa: E402
except ImportError:  # a partial delivery must not take the tool down
    _upkeep = None
try:  # how `selftest` deletes the temp folders it makes
    from sandbox_env import _remove_tree  # noqa: E402
except ImportError:
    def _remove_tree(root):
        """Delete the folder at `root`, never raising. git writes its object files read-only
        and Windows refuses to delete a read-only file, so each delete that fails is retried
        once with the file made writable - `shutil.rmtree(..., ignore_errors=True)` left every
        such folder behind."""
        def _retry(func, path, _exc):
            try:
                # 0o700 includes the write bit, which is the only bit os.chmod reads on Windows
                os.chmod(path, 0o700)
                func(path)
            except Exception:
                pass
        try:
            shutil.rmtree(root, **{("onexc" if sys.version_info >= (3, 12)
                                    else "onerror"): _retry})
        except Exception:
            pass

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PARTS_DIR_NAME = "orchdoc_parts"
#: An `_include` call. It starts at column 0, so a part's place in the file is plain to see.
_INCLUDE_RE = re.compile(r'^_include\("([A-Za-z0-9_]+)"\)', re.M)


def _include(name):
    """Run the part `name` in this module's namespace, where the call stands.

    The code object comes from SourceFileLoader, which reads and writes the part's normal
    __pycache__ file, so an ordinary run does not recompile it."""
    import importlib.machinery
    path = Path(__file__).resolve().parent / PARTS_DIR_NAME / (name + ".py")
    if not path.is_file():
        _tree = Path(__file__).resolve().parents[2].as_posix()
        raise FileNotFoundError(
            "[orchdoc] the part %s/%s.py is not on disk (%s). A copy of this tool is "
            "the file AND its parts folder. Restore them with:  git -C %s checkout "
            "origin/main -- .shared/scripts/orchdoc.py .shared/scripts/%s"
            % (PARTS_DIR_NAME, name, path, _tree, PARTS_DIR_NAME))
    loader = importlib.machinery.SourceFileLoader("%s.%s" % (PARTS_DIR_NAME, name), str(path))
    exec(loader.get_code(loader.name), globals())


def tool_command():
    """The command that runs this tool, as a document or a printed fix writes it. The short form
    where the workspace holds its own copy of the tools; a plugin install has none, and a document
    must not carry one computer's path, so there it is the form the setup file defines."""
    if (Path(PROJECTS) / ".shared" / "scripts" / "orchdoc.py").is_file():
        return "python <scripts>/orchdoc.py"
    return "python <scripts>/orchdoc.py"


def tool_source(path=None):
    """This tool's text: the file at `path` (default: this one), then the text of each part it
    includes, in include order. A check that reads the tool's own source reads this, so lines
    moved into a part stay in what the check sees."""
    path = Path(path or __file__).resolve()
    text = path.read_text(encoding="utf-8", errors="replace")
    return "\n".join([text] + [
        (path.parent / PARTS_DIR_NAME / (name + ".py")).read_text(encoding="utf-8",
                                                                  errors="replace")
        for name in _INCLUDE_RE.findall(text)])


def _find_workspace():
    """The workspace root, DISCOVERED. A hardcoded root is not portable to its own author.

    Marker: a directory containing `.shared/scripts` or any `ORCHESTRATOR-DECISIONS-*.md`.
    Both are definitional - a workspace without either is not one this tool can serve.
    """
    env = os.environ.get("ORCHDOC_WORKSPACE")
    if env:
        if not Path(env).is_dir():
            sys.stderr.write(
                "[orchdoc] ORCHDOC_WORKSPACE is set to a path that is not a directory:\n"
                "          %s\n"
                "          Refusing to guess a different workspace.\n" % env)
            raise SystemExit(2)
        return Path(env)

    def _walk_up(start):
        cur = Path(start).resolve()
        for cand in [cur] + list(cur.parents):
            if (cand / ".shared" / "scripts").is_dir() or (cand / ".orchdoc-config.json").is_file():
                return cand
            try:
                if any(cand.glob("ORCHESTRATOR-DECISIONS-*.md")):
                    return cand
            except (OSError, UnicodeDecodeError):
                pass
        return None

    for start in (Path.cwd(), Path(__file__).resolve().parent):
        hit = _walk_up(start)
        if hit:
            return hit

    legacy = Path.home() / "<your-workspace>"
    try:
        if legacy.is_dir() and Path.cwd().resolve().is_relative_to(legacy.resolve()):
            return legacy
    except (AttributeError, OSError):
        pass
    global WORKSPACE_IS_GUESS
    WORKSPACE_IS_GUESS = True
    return Path.cwd()


#: True when _find_workspace fell through to cwd - no marker was found anywhere.
WORKSPACE_IS_GUESS = False

PROJECTS = _find_workspace()
if WORKSPACE_IS_GUESS and os.environ.get("ORCHDOC_QUIET_GUESS") != "1":
    sys.stderr.write(
        "\n[orchdoc] NO WORKSPACE MARKER FOUND - falling back to the current directory:\n"
        "          %s\n"
        "          It has no .shared/scripts and no ORCHESTRATOR-DECISIONS-*.md, so any check\n"
        "          that resolves a cited path against it will find NOTHING and report clean.\n"
        "          Run from inside the workspace, or set ORCHDOC_WORKSPACE.\n\n" % PROJECTS)
# The ref that IS the truth. Overridable, because "origin/main" is an assumption, not a
# fact: a repo whose default branch is `master` (this tool's own published repo, as it
# happens) gets wrong answers from every freshness and verify oracle, and it was the
# condition that made touches_since crash. Auto-detect the remote HEAD, then fall back.
def _canonical_ref():
    env = os.environ.get("ORCHDOC_REF")
    if env:
        return env
    try:
        p = subprocess.run(["git", "symbolic-ref", "--quiet", "--short",
                            "refs/remotes/origin/HEAD"],
                           capture_output=True, text=True, timeout=10, creationflags=flags())
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout.strip()          # e.g. "origin/master"
    except Exception:
        pass
    return "origin/main"


CANONICAL_REF = _canonical_ref()

try:
    import workspace_paths as _wp  # noqa: E402
    _EXTERNAL_REPOS = [
        _wp.CLAUDE_HOME / "plugins" / "marketplaces" / "<private-repo>",
        _wp.MEM,
    ]
except ImportError:  # a partial delivery must not take the tool down
    _EXTERNAL_REPOS = []


def _main_worktree():
    """The MAIN working tree, whichever worktree is calling.

    Sibling repos, the memory clone and everything else an OrchDoc cites live beside the main
    checkout, never inside a worktree - so a worktree that resolves "the workspace" to itself
    sees a smaller world and calls correct citations dead.
    """
    try:
        p = subprocess.run(["git", "worktree", "list", "--porcelain"],
                           capture_output=True, text=True, timeout=15, creationflags=flags())
        for line in p.stdout.split("\n"):
            if line.startswith("worktree "):
                return Path(line[len("worktree "):].strip())
    except Exception:
        pass
    return None


def citable_repos():
    """
    Every repo an OrchDoc may cite. DISCOVERED, not listed.

    The deeper problem: a hardcoded list is a hand-maintained copy of "which repos
    exist", which is exactly the second-source-of-truth defect this tool refuses in
    everyone else's documents. It rots the moment a repo is added, and nothing notices.
    So it is derived from the filesystem instead.
    """
    roots = [PROJECTS]
    main = _main_worktree()
    if main is not None and main != PROJECTS:
        roots.append(main)
    found = list(roots)
    try:
        for root in roots:
            for g in root.glob("*/.git"):
                found.append(g.parent)
            for g in root.glob("*/*/.git"):
                if "node_modules" not in str(g):
                    found.append(g.parent)
    except Exception:
        pass
    found.extend(r for r in _EXTERNAL_REPOS if (r / ".git").exists())
    seen, out = set(), []
    for r in found:
        if str(r) not in seen:
            seen.add(str(r))
            out.append(r)
    return out


def object_store_groups(repos):
    """`repos` as a list of lists, the checkouts that share an object store together.
    A linked worktree's `.git` is a file naming `<common>/worktrees/<name>`, whose `commondir`
    file points back at the main `.git`; read as files, no git call. Unreadable -> its own group."""
    groups, out = {}, []
    for r in repos:
        key = None
        try:
            g = r / ".git"
            if g.is_dir():
                key = os.path.normcase(str(g.resolve()))
            elif g.is_file():
                m = re.match(r"gitdir:\s*(.+)", g.read_text(encoding="utf-8").strip())
                gd = Path(m.group(1).strip()) if m else None
                if gd is not None:
                    if not gd.is_absolute():
                        gd = r / gd
                    cd = gd / "commondir"
                    if cd.is_file():
                        gd = gd / cd.read_text(encoding="utf-8").strip()
                    key = os.path.normcase(str(gd.resolve()))
        except (OSError, UnicodeDecodeError):
            key = None
        if key is not None and key in groups:
            groups[key].append(r)
            continue
        out.append([r])
        if key is not None:
            groups[key] = out[-1]
    return out


def distinct_object_stores(repos):
    """`repos` reduced to the first checkout of each object store."""
    return [g[0] for g in object_store_groups(repos)]


def cloud_mode():
    """True inside a Claude Code cloud session.
    """
    return os.environ.get("CLAUDE_CODE_REMOTE", "").strip().lower() == "true"


def _is_shallow(repo):
    rc, out, _ = git(["rev-parse", "--is-shallow-repository"], cwd=repo)
    return rc == 0 and out.strip() == "true"


def ref_settle_limits(repos=None):
    """Why a commit that resolves in NO repo here may still be perfectly alive elsewhere.
    """
    repos = [r for r in (citable_repos() if repos is None else repos)
             if (r / ".git").exists()]
    bits = []
    shallow = sorted({r.name for r in repos if _is_shallow(r)})
    if shallow:
        bits.append("shallow history in %s (run: git fetch --unshallow)"
                    % ", ".join(shallow[:3]))
    if len(repos) <= 1:
        bits.append("no sibling repo is present, so a cross-repo citation cannot be "
                    "settled from here")
    return "; ".join(bits)


_MAIN_WORKTREE_CACHE = {}


def _main_worktree_cached():
    """`_main_worktree()`, asked once per working directory: it starts a git process, and its
    answer cannot change while this process lives."""
    try:
        key = os.getcwd()
    except OSError:
        key = ""
    if key not in _MAIN_WORKTREE_CACHE:
        _MAIN_WORKTREE_CACHE[key] = _main_worktree()
    return _MAIN_WORKTREE_CACHE[key]


def cited_roots():
    """Every directory a cited repo-relative path may live under, in the order they are tried."""
    roots = [Path(_find_workspace())]
    main = _main_worktree_cached()
    if main is not None:
        roots += [main, main.parent]
    for r in _EXTERNAL_REPOS:
        if r.is_dir():
            roots += [r, r.parent]
    out, seen = [], set()
    for r in roots:
        k = os.path.normcase(os.path.abspath(str(r)))
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def resolve_cited_path(rel):
    """The file a cited repo-relative path names, or None when no root has it.

    A path that climbs out of a root (`..`) names nothing here, and a leading slash is not an
    absolute path: citations are repo-relative, so it is cut before the lookup.
    """
    rel = _strip_dot_slash(str(rel)).lstrip("/\\")
    if not rel or ".." in rel.replace("\\", "/").split("/"):
        return None
    for root in cited_roots():
        try:
            cand = root / rel
            if cand.is_file():
                return cand
        except (OSError, ValueError):
            continue
    return None


ID_RE = re.compile(r"^([A-Z]{1,3}(?:\d+[a-z]?|-[A-Z][A-Z0-9]*\d*))\b")

STATUS_RE = re.compile(
    r"(?:^|[·|*-]|(?<![A-Za-z])\s)\*{0,2}status\*{0,2}\s*:\s*\*{0,2}\s*"
    r"([A-Za-z][A-Za-z_]*(?:[ -][A-Z][A-Za-z_]*)?)",
    re.IGNORECASE | re.MULTILINE)

# What a refusal must TELL the author. A check that refuses without naming the shape it
# wants makes the author reverse-engineer the parser, which is the same defect it is
# meant to catch.

def _artifact_extra():
    """The extra artifact pattern: the environment variable, else the workspace's settings file."""
    env = os.environ.get("ORCHDOC_ARTIFACT_RE")
    if env:
        return env
    try:
        import json as _json
        cfg = _json.loads((Path(PROJECTS) / ".orchdoc-config.json").read_text(encoding="utf-8"))
        return str((cfg or {}).get("artifact_re") or "")
    except Exception:
        return ""


ARTIFACT_RE = re.compile(
    r"\.(?:ts|tsx|js|jsx|py|md|json|ya?ml|astro|sh|ps1|txt|rs|go|java|rb)\b"
    + (("|" + _artifact_extra()) if _artifact_extra() else ""),
    re.I)

STATUS_CANONICAL = "**Status:** OPEN - **Owner:** the human - **Opened:** YYYY-MM-DD"

ATTEST_CANONICAL = ("**Attested-by:** <agent> at <ISO timestamp> - "
                    "<what changed and why the conclusion survives>")


VALID_STATUS = {
    # needs someone
    "OPEN", "BLOCKED", "PAUSED", "DEFERRED",
    "IN PROGRESS", "IN-PROGRESS", "INPROGRESS",
    "SEQUENCED",
    # closed out
    "RESOLVED", "ANSWERED", "DONE", "SUPERSEDED", "ARCHIVED",
    "RETRACTED",
    # informational entries: findings, specimens, records
    "CONFIRMED", "RECORDED", "ADOPTED", "SHIPPED",
}
PLATE_STATUS = {"OPEN", "BLOCKED"}

OWNER_FIELD = re.compile(r"\*\*Owner:\*\*\s*(.+?)(?:\s+[-·]\s+\*\*|\s*$)", re.M)


def owner_text(body):
    """The Owner field's value, as written, with markdown emphasis removed. "" when absent."""
    m = OWNER_FIELD.search(body or "")
    return m.group(1).replace("*", "").strip() if m else ""


_HUMAN_OWNER_CACHE = {}


def _vocabulary_human_aliases():
    """Lower-case aliases the vocabulary file gives the human; [] when the file is absent."""
    try:
        voc = Path(__file__).resolve().parent / "facets_vocabulary.json"
        return [str(n).lower() for n in
                json.loads(voc.read_text(encoding="utf-8")).get("human_owner_aliases", [])]
    except Exception:
        return []            # no vocabulary: the name from human_name() is enough


def human_owner_names():
    """The lower-case names an Owner field may use for the human who reads the plate."""
    names = {human_name().lower()} | set(_vocabulary_human_aliases())
    return {n for n in names if n}


def _human_owner_re():
    """`human_owner_names()` as one compiled pattern, kept until the workspace or config changes.

    Cached because the plate asks once per entry and `human_name()` reads two files each time.
    The key holds the config file's size and modified time, so an edited name is used on the next
    call and a long-running Reader does not need a restart for it.
    """
    try:
        st = (Path(PROJECTS) / ".orchdoc-config.json").stat()
        sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        sig = None
    key = (str(PROJECTS), sig)
    rx = _HUMAN_OWNER_CACHE.get(key)
    if rx is None:
        names = sorted(human_owner_names(), key=len, reverse=True)
        if names:
            # (?<!\w) and (?!\w) are what \b means at the ends of a name made of word characters.
            rx = re.compile(r"(?<!\w)(?:%s)(?!\w)" % "|".join(re.escape(n) for n in names), re.I)
        else:
            rx = re.compile(r"(?!)")     # no name known: nothing is the human's
        _HUMAN_OWNER_CACHE.clear()       # one entry only, so a changing workspace cannot grow it
        _HUMAN_OWNER_CACHE[key] = rx
    return rx


def owner_is_human(body):
    return bool(_human_owner_re().search(owner_text(body)))


LIVE_STATUS = PLATE_STATUS | {"IN PROGRESS", "IN-PROGRESS", "INPROGRESS", "SEQUENCED"}


def follows_stopsign_convention(entries):
    for e in entries:
        if status_of(e["body"]) in TERMINAL_STATUS and "\u26d4" in prose_only(e["body"]):
            return False
    return True


def has_open_subitems(body, stopsign_means_open=False):
    """Does this entry carry a sub-item that is NOT done?

    Deliberately reuses the same two marker regexes as W-STRIKEDONE and E-MIXEDSTATE. A third
    private definition of "done" would be a third thing to drift - the marker-format-is-a-
    contract lesson, applied inside one file.

    A thin wrapper over `first_open_subitem` for the same reason: WHICH bullet is open and
    WHETHER one is are the same question, and two loops answering it would drift.
    """
    return first_open_subitem(body, stopsign_means_open) is not None


def first_open_subitem(body, stopsign_means_open=False):
    """(offset-in-body, the raw line) of the first sub-item that is NOT done, or None.
    """
    body = prose_only(body)
    for off, raw in enumerate(body.splitlines()):
        txt = re.sub(r"~~.*?~~", "", re.sub(r"`[^`]*`", "", raw))
        if not re.match(r"\s*(?:[-*+]|\d+[.)])\s", txt):
            continue                      # only sub-items, not the entry's own fields
        if re.match(r"\s*(?:[-*+]|\d+[.)])\s+\[[xX]\]", txt):
            continue
        if _OPEN_SUBITEM_RE.search(txt):
            return off, raw
        if stopsign_means_open and "\u26d4" in txt:
            return off, raw
    return None


_OPEN_SUBITEM_RE = re.compile(
    r"⏳|(?<![A-Za-z_-])(?:NOT DONE|NOT YET|NOT STARTED|OUTSTANDING|STILL NEED"
    r"|STILL TO|TODO|PENDING|UNBUILT|AWAITING)(?![A-Za-z_-])", re.I)



def status_of(body):
    """The entry's status, normalised, or None."""
    m = STATUS_RE.search(body)
    if not m:
        return None
    v = re.sub(r"\s+", " ", m.group(1).strip()).upper()
    # One canonical value for the three spellings, so every downstream set-membership test
    # sees the same string. Otherwise "IN-PROGRESS" is live and "IN PROGRESS" is not, which is
    # the marker-format-is-a-contract failure inside a single function.
    return "IN PROGRESS" if v in ("IN-PROGRESS", "INPROGRESS") else v


DEPENDS_RE = re.compile(r"^\s*\*\*Depends:\*\*\s*(.+)$", re.MULTILINE | re.IGNORECASE)
REVIEWED_RE = re.compile(
    r"\*{0,2}(?:reviewed|attested-by)\*{0,2}\s*:\s*\*{0,2}\s*"
    r"(?:[A-Za-z0-9_?-]+\s+at\s+)?"
    r"(\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:?\d{2}|Z)?)?)"
    r"\s*[-:]?\s*([^\n]*)",
    re.IGNORECASE)

# Just the LABEL and enough to know a stamp starts here. reviewed_of re-matches the full
# REVIEWED_RE from each of these offsets, because the full pattern's trailing `[^\n]*` makes
# non-overlapping iteration blind to every stamp after the first on a line.
REVIEWED_LABEL_RE = re.compile(r"\*{0,2}(?:reviewed|attested-by)\*{0,2}\s*:", re.IGNORECASE)

RUBBER_STAMP_RE = re.compile(
    r"^\W*(still\s+(current|true|valid|accurate|good|fine|ok)|no\s+change[sd]?|"
    r"reviewed|checked|verified|current|unchanged|looks?\s+(good|fine|ok)|"
    r"n/?a|ok|fine|yes|confirmed)\W*$", re.IGNORECASE)
MIN_ATTESTATION_CHARS = 40


STAMP_CLAUSE_RE = re.compile(
    r"\*{0,2}(?:reviewed|attested-by)\*{0,2}\s*:\s*\*{0,2}\s*"
    r"(?:([A-Za-z0-9_?-]+)\s+at\s+)?"
    r"(\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:?\d{2}|Z)?)?)"
    r"\*{0,2}\s*[-:]?\s*",
    re.IGNORECASE)

# A stamp already standing on its own line. BOTH labels, because `split_stamps` preserves
# the label it found rather than inventing an attester for a stamp that never named one.
STAMP_LINE_RE = re.compile(r"^\s*\*\*(?:Attested-by|Reviewed):\*\*", re.IGNORECASE)

# The same cut `reviewed_of` makes: a following bold field belongs to the plate, not to the
# attestation. One pattern, so the writer and the reader cannot drift apart.
NEXT_FIELD_RE = re.compile(r"\s+-\s+\*\*[A-Za-z-]+:\*\*")


def split_stamps(line):
    """(the line without its review stamps, [(date, who, why), ...]).

    Every stamp on the line, in the order written. A stamp's `why` runs to the next stamp
    or to the next bold plate field, whichever comes first, and anything that WAS a plate
    field stays on the plate line - so splitting a line cannot silently drop `**Owner:**`.
    """
    ms = list(STAMP_CLAUSE_RE.finditer(line))
    if not ms:
        return (line.rstrip(), [])
    stamps, keeps = [], []
    for k, m in enumerate(ms):
        tail = line[m.end():ms[k + 1].start() if k + 1 < len(ms) else len(line)]
        cut = NEXT_FIELD_RE.search(tail)
        why, keep = (tail[:cut.start()], tail[cut.start():]) if cut else (tail, "")
        stamps.append((m.group(2), (m.group(1) or "").strip(),
                       why.strip().strip("*").strip(" -").strip()))
        if keep.strip():
            keeps.append(keep.rstrip())
    plate = re.sub(r"[\s-]+$", "", line[:ms[0].start()])
    return ((plate + "".join(keeps)).rstrip(), stamps)


def render_stamp(date, who, why):
    """One stamp, one line. No actor recorded means no actor invented - see `cmd_restamp`,
    which refuses to write an INFERRED id into a durable attestation. Migrating an old
    stamp that names nobody must not quietly answer the question it left open."""
    head = ("**Attested-by:** %s at %s" % (who, date)) if who else ("**Reviewed:** %s" % date)
    return ("%s - %s" % (head, why)) if why else head



NOTE_MARK = "ORCHDOC:NOTE"

SEEN_MARK = "\U0001f440"


NOTE_KINDS = ("NOTE", "INSTRUCTION")

NOTE_LINE_RE = re.compile(
    r"^\s*>\s*(?:" + SEEN_MARK + r"\s*)?"
    r"\*\*(?P<kind>NOTE|INSTRUCTION) from (?P<who>[^*<>\n]{1,40})\*\*\s*"
    r"\((?P<when>[^)<>\n]{1,40})\)\s*:\s*"
    r"(?P<text>.*?)\s*<!--\s*" + NOTE_MARK + r"\s+"
    r"(?P<nid>(?:[A-Z]{1,3}\d{1,4}[a-z]?\.)?n\d{1,4})"
    r"(?P<enc>\s+ESC)?"
    r"(?:\s+ACK\s+(?P<ack>[A-Za-z0-9][A-Za-z0-9:_.\- ]{0,38}[A-Za-z0-9]))?"
    r"\s*-->\s*$")


def encode_note_breaks(lines):
    """Join already-sanitized lines into the one-line escaped form."""
    return "\\n".join(l.replace("\\", "\\\\") for l in lines)


_NOTE_ESC_RE = re.compile(r"\\([\\n])")


def decode_note_text(text, enc):
    """The text a person typed, from the stored text. Only an `ESC` note is decoded."""
    if not enc or not text:
        return text or ""
    return _NOTE_ESC_RE.sub(lambda m: "\n" if m.group(1) == "n" else "\\", text)


def note_display(n):
    """A parsed note's real text, with real line breaks."""
    return decode_note_text(n.get("text"), n.get("enc"))


ACK_CHARS = re.compile(r"[^A-Za-z0-9:_.\- ]+")


def ack_token(who, when):
    t = ACK_CHARS.sub(" ", "%s %s" % (who or "orchestrator", when or ""))
    t = re.sub(r"\s{2,}", " ", t).strip()[:40].strip()
    # The pattern requires the token to START and END alphanumeric; trim anything else off
    # rather than emitting a line the reader that wrote it could not parse back.
    t = re.sub(r"^[^A-Za-z0-9]+|[^A-Za-z0-9]+$", "", t)
    return t or "orchestrator"

NOTE_ID_RE = re.compile(r"^(?:[A-Z]{1,3}\d{1,4}[a-z]?\.)?n\d{1,4}$")


NOTE_TEXT_LIMIT = 7500


def _sanitize_note_line(t):
    # Leading spaces are the indentation of a sub-bullet or an indented line: kept as typed
    # (a tab becomes four spaces). The marker (`- `, `* `, `1. `, `2) `) is kept too.
    t = t.replace("\t", "    ") if t[:1] in " \t" else t
    lead = re.match(r" *", t).group(0)
    rest = sanitize_field(t[len(lead):], None, strip_lead=False)
    t = (lead + rest) if rest else ""
    t = t.replace("```", "(fence)")
    t = re.sub(r"<\s*/?\s*span", "(span", t, flags=re.IGNORECASE)
    return t.replace(NOTE_MARK, "ORCHDOC_NOTE")


def sanitize_note(text, limit=NOTE_TEXT_LIMIT, enc=True):
    """`enc=True` (a note being written): line breaks are kept, as the escaped one-line form.
    `enc=False` (a stored legacy note, or an item title): the old one-line narrowing."""
    if enc:
        if limit is not None:
            text = refuse_if_too_long(text, limit, "note text")
        rows = [_sanitize_note_line(r) for r in (text or "").splitlines()]
        while rows and not rows[0]:
            rows.pop(0)
        while rows and not rows[-1]:
            rows.pop()
        if rows:
            # The text sits after `): ` on a `> **NOTE` line, so a leading marker cannot make
            # a heading or a list; it is kept. The reader of the line skips leading blanks.
            rows[0] = rows[0].lstrip()
        return encode_note_breaks(rows)
    return _sanitize_note_legacy(text, limit)


def _sanitize_note_legacy(text, limit=NOTE_TEXT_LIMIT):
    if limit is not None:
        text = refuse_if_too_long(text, limit, "note text")
    """Narrow a note's text so it cannot impersonate structure. See `sanitize_field`.

    Three more, specific to this form:
      * a triple backtick -> defanged, so a note cannot open a fence and swallow the document
      * <span / </span> -> defanged, because `strike` strips those globally and a note
        carrying one would be rewritten by a pass this whole design exists to be safe from
      * ORCHDOC:NOTE -> defanged, belt and braces alongside the comment defang

    The limit is generous but finite: an unbounded note is its own denial of service on a
    document meant to be read, and one line is one line.
    """
    t = sanitize_field(text or "", None)
    t = t.replace("```", "(fence)")
    t = re.sub(r"<\s*/?\s*span", "(span", t, flags=re.IGNORECASE)
    t = t.replace(NOTE_MARK, "ORCHDOC_NOTE")
    return t.strip()


def render_note(nid, who, when, text, kind="NOTE", ack=None, stored=False, enc=None):
    """The ONE writer of the form NOTE_LINE_RE reads. Both halves, always.
    """
    k = (kind or "NOTE").upper()
    if k not in NOTE_KINDS:
        raise ValueError("a note kind is one of %s, not %r" % (", ".join(NOTE_KINDS), kind))
    tail = (" ACK " + ack_token(*ack)) if ack else ""
    seen = (SEEN_MARK + " ") if ack else ""
    if stored:
        is_enc = bool(enc)
        # An escaped text was narrowed when it was encoded, and narrowing it again would
        # collapse the indentation after a break. It is kept as it is.
        body = (text or "").strip() if is_enc else sanitize_note(text, None, enc=False)
    else:
        is_enc = True
        body = sanitize_note(text, NOTE_TEXT_LIMIT, enc=True)
    return "> %s**%s from %s** (%s): %s <!-- %s %s%s%s -->" % (
        seen, k, sanitize_field(who, 40), sanitize_field(when, 40), body,
        NOTE_MARK, nid, " ESC" if is_enc else "", tail)


def parse_notes(text):
    """[{nid, who, when, text, line}] for every note in `text`, 1-indexed by line.
    """
    out = []
    rows = text.split("\n")
    for n, (raw, (fenced, _open)) in enumerate(zip(rows, fence_states(rows)), start=1):
        if fenced:
            continue
        m = NOTE_LINE_RE.match(raw)
        if m:
            out.append({"nid": m.group("nid"), "who": m.group("who").strip(),
                        "when": m.group("when").strip(),
                        "kind": m.group("kind"),
                        "ack": (m.group("ack") or "").strip(),
                        "enc": bool(m.group("enc")),
                        "text": m.group("text").strip(), "line": n})
    return out


def strip_notes(text):
    """(text without its note lines, [notes]). What the reader renders the entry body from.
    """
    notes = parse_notes(text)
    drop = set(n["line"] for n in notes)
    kept = [ln for i, ln in enumerate(text.split("\n"), start=1) if i not in drop]
    return "\n".join(kept), notes


def next_note_id(lines, entry=None):
    """The next free note id.
"""
    notes = parse_notes("\n".join(lines))
    if entry:
        pref = "%s." % entry
        used = [int(n["nid"].split(".", 1)[1][1:])
                for n in notes if n["nid"].startswith(pref)]
        return "%sn%d" % (pref, (max(used) + 1) if used else 1)
    # No entry named: the legacy whole-document sequence, so nothing that calls this the old
    # way changes behaviour.
    used = [int(n["nid"][1:]) for n in notes if "." not in n["nid"]]
    return "n%d" % ((max(used) + 1) if used else 1)




def depends_of(body):
    m = DEPENDS_RE.search(body)
    if not m:
        return []
    return [t.strip() for t in re.split(r"[,;]", m.group(1)) if t.strip()]


def _as_stamp(d):
    """
    Normalise a review date for comparison against a git ISO timestamp.
    """
    return d if len(d) > 10 else d + "T00:00:00+00:00"


def reviewed_of(body):
    """(date, attestation-text) or (None, None).
    """
    ms = []
    for lm in REVIEWED_LABEL_RE.finditer(body):
        mm = REVIEWED_RE.match(body, lm.start())
        if mm:
            ms.append(mm)
    if not ms:
        return (None, None)
    m = max(ms, key=lambda x: (x.group(1), x.start()))
    text = (m.group(2) or "")

    tail = body[m.end():]
    for line in tail.split("\n")[1:]:
        s = line.strip()
        # a blank line, a new heading, or a new plate field ends the paragraph
        if not s or s.startswith("#") or s.startswith("**") or re.match(r"^[-*_]{3,}$", s):
            break
        text += " " + s

    cut = re.search(r"\s+-\s+\*\*[A-Za-z-]+:\*\*", text)
    if cut:
        text = text[:cut.start()]
    return (m.group(1), text.strip().strip("*").strip())


def last_moved(entry):
    """
    When this entry last MOVED, from the dates it carries. Coarse by construction.
    """
    today = _dt.date.today().isoformat()
    dates = [d for d in re.findall(r"\b(\d{4}-\d{2}-\d{2})\b", entry["body"]) if d <= today]
    return max(dates) if dates else None


def _selftest_last_moved():
    """E-STALEPROSE through a `Depends:` edge, against an entry that names a date to come.
    """
    import tempfile
    today = _dt.date.today()
    soon = (today + _dt.timedelta(days=7)).isoformat()
    ok = True
    d = Path(tempfile.mkdtemp(prefix="orchdoc-lastmoved-"))
    try:
        for label, dep_text, review, want in (
                ("future date ignored", "The weekly reset is on %s." % soon,
                 " - **Reviewed:** 2026-09-20", False),
                ("past date still stale", "It changed on 2026-09-25.",
                 " - **Reviewed:** 2026-09-20", True),
                ("same day, never reviewed", "It changed on %s; the reset is on %s."
                 % (today.isoformat(), soon), "", False)):
            p = d / "ORCHESTRATOR-DECISIONS-o99.md"
            p.write_text(canonical_title("o99", "a role") + "\n\n## §3 ON CLAUDE'S PLATE\n\n"
                         "### W9902 - the dependent\n**Status:** OPEN - **Owner:** o99%s\n"
                         "**Depends:** W9901\n\nIt rests on W9901.\n\n"
                         "### W9901 - the dependency\n**Status:** OPEN - **Owner:** o99 - "
                         "**Opened:** 2026-09-01\n\n%s\n" % (review, dep_text), encoding="utf-8")
            try:
                fired = any(f.code == "E-STALEPROSE" and "'W9902'" in f.msg for f in check_doc(p))
                got = "fired" if fired else "silent"
            except Exception as ex:
                fired, got = None, "raised %s: %s" % (type(ex).__name__, str(ex)[:60])
            good = fired is want
            ok &= good
            print("  [%s] %-12s -> %s: %s" % ("OK" if good else "FAIL", "E-STALEPROSE",
                                              label, got))
    finally:
        _remove_tree(str(d))
    return ok


TOUCHES_RE = re.compile(r"^Touches:\s*(.+)$", re.MULTILINE | re.IGNORECASE)
TOUCH_TOKEN_RE = re.compile(r"^(?:(o\d+)[:/])?([A-Z]{1,3}(?:\d+[a-z]?|-[A-Z][A-Z0-9]*\d*))$")

_CITED_EXT = r"[A-Za-z0-9_.\-/]+\.(?:md|ts|tsx|js|mjs|py|json|astro|yml|yaml|sql)"
_CITED_NEEDLE = r"(?:#[A-Za-z0-9_.\-]+)?"
# A `**Depends:**` line, and a file-shaped token with NO backtick requirement. The pair
# exists to find what CITED_PATH_RE deliberately does not match: a citation an author
# believes they wrote. Same extension list, so the two can only disagree about backticks.
DEPENDS_LINE_RE = re.compile(r"^\*\*Depends:\*\*\s*(.+)$", re.M)
BARE_FILEISH_RE = re.compile(_CITED_EXT)
CITED_PATH_RE = re.compile(
    r"`(%s%s)`" % (_CITED_EXT, _CITED_NEEDLE)
    + r"|\]\((%s%s)\)" % (_CITED_EXT, _CITED_NEEDLE))


NEEDLE_WITH_SPACE_RE = re.compile(
    r"`([A-Za-z0-9_.\-/]+\.md)#([A-Za-z0-9_.\-]+)(\s+[^`]*?)`"
    r"|\]\(([A-Za-z0-9_.\-/]+\.md)#([A-Za-z0-9_.\-]+)(\s+[^)]*?)\)")


DONE_WHEN_RE = re.compile(r"^\s*\*{0,2}Done-when:\*{0,2}\s*(.+?)\s*$", re.M | re.I)


OPENED_RE = re.compile(r"\*\*Opened:\*\*\s*(\d{4}-\d{2}-\d{2})")


# A line that IS a bold field, used by `note_anchor` to find where the field block ends.
# Deliberately strict about the bold markers: this decides where a note is PLACED, and a
# loose match here would walk into prose and bury the note.
FIELD_LINE_RE = re.compile(r"^\s*\*\*[A-Za-z-]+:\*\*")

RESOLVED_LINE_RE = re.compile(r"\*\*Resolved[^*]*:\*\*")

MEASURED_RE = re.compile(r"<!--\s*measured:\s*(\S+?)\s*-->")


def measured_marker(when=None):
    """The stamp a sub-item carries, in the ONE shape every reader imports."""
    return "<!-- measured: %s -->" % (when or _now_iso())


OPENED_DAY_ONLY_RE = re.compile(r"\*\*Opened:\*\*\s+(\d{4}-\d{2}-\d{2})(?!T)")

RESOLVED_DAY_ONLY_RE = re.compile(r"\*\*(?:Resolved|RULED)\s+(\d{4}-\d{2}-\d{2})\s*:\*\*", re.I)


def opened_of(body):
    """The Opened date, or None. Used to ask "changed SINCE this was raised", not "ever"."""
    m = OPENED_RE.search(body)
    return m.group(1) if m else None


def done_when(body):
    """The declared completion signal for an entry, or None if it never declared one."""
    m = DONE_WHEN_RE.search(body)
    return m.group(1).strip() if m else None




def done_when_satisfied(cond, opened_iso, root):
    """(satisfied, evidence). ⛔ Only ever True on DEMONSTRABLE satisfaction.
    """
    if not cond:
        return False, ""
    low = cond.strip().lower()
    if low == human_name().lower():            # a Done-when naming the human (human_name(), not a fixed word)
        return False, "closes on the owner's word - no mechanical signal by declaration"
    if low.startswith("path:"):
        rel = cond.split(":", 1)[1].strip()
        p = pathlib.Path(root) / rel
        if not p.exists():
            return False, "%s does not exist yet" % rel
        rc, out, _e = git(["log", "--format=%cI", "-1"] +
                          (["--since=%s" % opened_iso] if opened_iso else []) + ["--", rel],
                          cwd=root)
        if rc == 0 and out.strip():
            return True, "%s changed at %s, after this was opened" % (rel, out.strip()[:19])
        return False, "%s exists but has not changed since Opened" % rel
    if low.startswith("cmd:"):
        cmd = cond.split(":", 1)[1].strip()
        try:
            p = subprocess.run(cmd, shell=True, cwd=str(root), capture_output=True,
                               text=True, timeout=90, creationflags=flags())
        except Exception as ex:
            return False, "could not run: %s" % str(ex)[:60]
        if p.returncode == 0:
            return True, "`%s` exits 0" % cmd[:60]
        return False, "`%s` exits %d" % (cmd[:50], p.returncode)
    return False, "unrecognised Done-when form: %r" % cond[:40]


def _strip_dot_slash(p):
    """Remove a leading `./` - the PREFIX, not the characters.

        ./docs/plan.md        -> docs/plan.md          correct
        .shared/scripts/x.py  -> shared/scripts/x.py   WRONG - resolves to nothing
    """
    return p[2:] if p.startswith("./") else p


def without_attested_by(body):
    """`body` with `**Attested-by:**` fields blanked - line count preserved.

    Blanks rather than deletes so anything downstream keeping line numbers stays true.
    """
    out, in_att = [], False
    for ln in body.splitlines():
        s = ln.strip()
        if s.startswith("**Attested-by:**"):
            in_att = True
        elif not s or (s.startswith("**") and ":**" in s[:24]):
            in_att = False
        out.append("" if in_att else ln)
    return "\n".join(out)


STALLED_DAYS = 14

CLOSED_STATES = {"RESOLVED", "ANSWERED", "DONE", "SUPERSEDED", "DEFERRED", "PARKED"}
SCHEDULES_RE = re.compile(
    r"(?i)\b(?:revisit|re-?open|later|post-?launch|once .{0,40}(?:ships|lands|exists)|"
    r"when .{0,40}(?:ships|lands|is done)|defer(?:red)? (?:to|until)|"
    r"come back to|follow[- ]up|next (?:week|month|quarter)|in a week|after the)\b")
OPEN_SUB_RE = re.compile(r"(?m)^\s*-\s*\[ \]\s+(.+)$")
PROVENANCE_RE = re.compile(r"(?i)\*\*Provenance:?\*\*")
# a sub-item explicitly belonging to somebody else is not this entry's deferred work
FOREIGN_SUB_RE = re.compile(r"(?i)\bnot mine\b|\brouted to\b|\bnot my\b|\byours\b")


def _days_since_opened(body):
    """Days since the entry's **Opened:** date, or None if it has none.

    #: failure-value: None   # an entry with no Opened date is not old, it is UNDATED, and
    #                          W-STALLED must not fire on one - that would be a different
    #                          finding wearing this one's name.
    """
    m = re.search(r"\*\*Opened:?\*\*[^\n]*?(\d{4}-\d{2}-\d{2})", body)
    if not m:
        return None
    try:
        import datetime as _dt
        d = _dt.datetime.strptime(m.group(1), "%Y-%m-%d").replace(tzinfo=_dt.timezone.utc)
        return (_dt.datetime.now(_dt.timezone.utc) - d).days
    except Exception:
        return None


def cited_paths(body):
    """Repo-relative artifacts an entry cites. Each is a dependency it already declared.

      * `cited_paths` does **not** call `without_attestations` - zero occurrences below.
      * `without_attestations` would not do that job if it were called: it matches
        `OVERRIDE_RE`, so it blanks `<!-- ORCHDOC:OVERRIDE -->` stamps and **never touches an
        `**Attested-by:**` line.**
    """
    body = without_attested_by(prose_only(body))
    out = set()
    for m in CITED_PATH_RE.finditer(body):
        p = m.group(1) or m.group(2)
        if not p or p.startswith("http"):
            continue
        base = _strip_dot_slash(p).split("#", 1)[0]
        if "/" in base or base.endswith(".md"):
            out.add(base)
    return sorted(out)


def cited_regions(body):
    """{path: [needle, ...]} for citations written as `path#needle`."""
    out = {}
    for m in CITED_PATH_RE.finditer(body):
        p = m.group(1) or m.group(2)
        if not p or p.startswith("http") or "#" not in p:
            continue
        base, needle = _strip_dot_slash(p).split("#", 1)
        if needle.strip():
            out.setdefault(base, []).append(needle.strip())
    return out


_NEWEST_COMMIT = {}
_PLAIN_PATH_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./ +@,=-]*$")


def _not_before(when_iso, since_iso):
    """True when commit date `when_iso` is at or after `since_iso`, False when before, None when
    the commit date does not parse or `since_iso` does not parse (a bare since is read as local time, as git does)."""
    try:
        a = _dt.datetime.fromisoformat(when_iso)
        b = _dt.datetime.fromisoformat(since_iso)
    except (ValueError, TypeError):
        return None
    if a.tzinfo is None:
        return None
    if b.tzinfo is None:                  # git reads a bare time given to --since as local time
        b = b.astimezone()
    return a >= b.replace(microsecond=0)  # git reads --since to the second


def prefetch_newest_commits(paths, budget=24000):
    ref = CANONICAL_REF
    _NEWEST_COMMIT.clear()      # one check's reading: `commit` re-checks after the ref has moved
    todo = sorted({str(p).rstrip("/") for p in paths if _PLAIN_PATH_RE.match(str(p))})
    batch, size = [], 0
    for p in todo + [None]:
        if p is not None and (not batch or size + len(p) + 3 <= budget):
            batch.append(p)
            size += len(p) + 3
            continue
        if batch:
            _prefetch_batch(ref, batch)
        batch, size = ([p], len(p) + 3) if p is not None else ([], 0)


def _prefetch_batch(ref, batch):
    try:
        r = subprocess.run(["git", "-C", str(PROJECTS), "log", "-c", "--no-renames", "--name-only",
                            "--format=@%cI%x1f%s%x1f%H", ref, "--"] + batch,
                           capture_output=True, text=True, errors="replace", timeout=900,
                           creationflags=flags())
    except Exception:
        return
    if r.returncode != 0:
        return
    wanted, found, folders, cur = set(batch), {}, set(), None
    for line in r.stdout.splitlines():
        if line.startswith("@"):
            parts = line[1:].split("\x1f")
            cur = (parts[0].strip(), parts[1], parts[2].strip()) if len(parts) >= 3 else None
            continue
        f = line.strip().replace("\\", "/")
        if not f or cur is None:
            continue
        if f in wanted:
            found.setdefault(f, cur)
        probe = f.rpartition("/")[0]
        while probe:                      # a path with files under it was a folder at some point
            folders.add(probe)
            probe = probe.rpartition("/")[0]
    for p in found:
        if p not in folders:
            _NEWEST_COMMIT[(ref, p)] = found[p]


def paths_changed_since(paths, since_iso, regions=None):
    """{path: (iso, subject, sha[, why])} for cited artifacts that moved after the review.

    A BARE path reports its newest commit since `since_iso`, as it always has.

    Callers indexing [0], [1] and [2] still work.
    """
    out = {}
    regions = regions or {}
    for p in paths:
        needles = regions.get(str(p)) or []
        if needles:
            hit = first_needle_mover(str(p), since_iso, needles)
            if hit:
                out[p] = hit
            continue
        hit = _NEWEST_COMMIT.get((CANONICAL_REF, str(p)))    # read once by prefetch_newest_commits
        if hit and since_iso and _not_before(hit[0], since_iso) is False:
            continue                                         # not moved since the review
        args = ["log", "-1", "--format=%cI%x1f%s%x1f%H"]
        if since_iso:
            args.append("--since=%s" % since_iso)
        args += [CANONICAL_REF, "--", p]
        rc, blob, _ = git(args)
        if rc != 0 or not blob.strip():
            continue
        parts = blob.strip().split("\x1f")
        if len(parts) >= 2:
            # The SHA rides along so a caller can say WHAT the commit touched, not merely
            # that it happened. Callers unpacking two values still work.
            out[p] = (parts[0].strip(), parts[1], parts[2].strip() if len(parts) > 2 else "")
    return out



# The id shape an entry heading carries - one definition for the span scanner and for
# deciding whether a needle names an entry at all.
ENTRY_ID_SHAPE = r"[A-Z]{1,3}\d+[a-z]?"
ENTRY_ID_RE = re.compile(r"^%s$" % ENTRY_ID_SHAPE)
ID_NEEDLE_RE = re.compile(r"^%s(?:\.n\d+)?$" % ENTRY_ID_SHAPE)
ENTRY_HEAD_RE = re.compile(r"^#{1,6}\s*\S*\s*(%s)\b" % ENTRY_ID_SHAPE)

_GENERATED_ROW_RE = re.compile(r"^\s*\|\s*\*\*\[|^- `%s` - " % ENTRY_ID_SHAPE)
_GENERATED_BEGIN_RE = re.compile(r"^\s*<!--\s*ORCHDOC:(?:PLATE|INDEX|FINDEX):BEGIN\b")
_GENERATED_END_RE = re.compile(r"^\s*<!--\s*ORCHDOC:(?:PLATE|INDEX|FINDEX):END\b")

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_ZERO_OID_RE = re.compile(r"^0+$")

NEEDLE_SCAN_LIMIT = 400

# The diff options every reader of changed lines uses. `histogram` keeps a moved block from
# being reported as deleted and re-added; `--text` stops a stray byte from turning the diff
# into "Binary files differ", which has no lines at all and would read as "nothing changed".
_DIFF_OPTS = ["--unified=0", "--diff-algorithm=histogram", "--text", "--no-color",
              "--no-ext-diff", "--no-textconv", "--no-renames"]

_NEEDLE_CACHE = {}      # (sha, path, needle) -> why it counts as touched, or "" when it does not


def entry_spans(lines):
    """[(first_line, last_line, eid), ...] - 1-based inclusive, one per entry heading.

    Factored out of `entries_touched` so the needle test reads entries exactly the way that
    function does, rather than through a second private copy of the scanner.
    """
    spans, cur, cur_line = [], None, 0
    fenced = False
    for i, ln in enumerate(lines, 1):
        if ln.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        m = ENTRY_HEAD_RE.match(ln)
        if not m and ln.startswith("#"):
            m = ARCHIVED_ID_RE.search(ln)
        if m:
            if cur:
                spans.append((cur_line, i - 1, cur))
            cur, cur_line = m.group(1), i
    if cur:
        spans.append((cur_line, len(lines), cur))
    return spans


PRIORITY_LINE_RE = re.compile(r"^\s*\*\*Priority:\*\*")


def _needle_line(ln, orch):
    """`ln` as the needle test compares it, or None when it is bookkeeping.

    `\\r` and trailing whitespace are stripped everywhere. In an OrchDoc a review stamp, an
    override stamp, a priority line, a blank line and a generated row are bookkeeping, and a
    Status line keeps its plate fields with the review stamps cut out - `restamp` rewrites
    exactly that clause. A note line keeps everything except its acknowledgement - see
    `note_without_ack`.
    """
    s = ln.rstrip()
    if not orch:
        return s
    if not s.strip():
        return None
    if (STAMP_LINE_RE.match(s) or OVERRIDE_RE.match(s) or _GENERATED_ROW_RE.match(s)
            or PRIORITY_LINE_RE.match(s)):
        return None
    if NOTE_LINE_RE.match(s):
        return note_without_ack(s)
    if "**Status:**" in s:
        s = split_stamps(s)[0]
    return s


def note_without_ack(ln):
    """A note line with its acknowledgement taken out; any other line unchanged.
    """
    m = NOTE_LINE_RE.match(ln)
    if not m:
        return ln
    return "> **%s from %s** (%s): %s <!-- %s %s%s -->" % (
        m.group("kind"), m.group("who").strip(), m.group("when").strip(),
        m.group("text").strip(), NOTE_MARK, m.group("nid"),
        " ESC" if m.group("enc") else "")


def _ack_only_hunk(removed, added):
    """True when a diff hunk does nothing but acknowledge notes: every line on both sides is a
    note line, and the two sides are the same notes once `note_without_ack` is applied."""
    if not removed or len(removed) != len(added):
        return False
    if not all(NOTE_LINE_RE.match(x.rstrip()) for x in removed + added):
        return False
    return (sorted(note_without_ack(x.rstrip()) for x in removed)
            == sorted(note_without_ack(x.rstrip()) for x in added))


def _names(needle, slug=None):
    """A test `line -> bool` for whether a line names `needle`. Case-insensitive.

    Any other needle (`cited_paths`, `E-DAYONLYRULING`, `additions`) stays a substring match.
    """
    hits = _name_hits(needle, slug)
    return lambda ln: bool(hits(ln))


_QUALIFIER_RE = re.compile(r"(?<![A-Za-z0-9])(o\d+)[:/]$", re.I)


def _name_hits(needle, slug=None):
    """A function `line -> [offsets]`: where in the line `needle` is really mentioned.
    """
    low = needle.lower()
    if not ID_NEEDLE_RE.match(needle):
        def plain(ln):
            i = ln.lower().find(low)
            return [i] if i >= 0 else []
        return plain
    rx = re.compile(r"(?<![A-Za-z0-9])(?<!(?-i:[A-Z][A-Z])-)%s(?![A-Za-z0-9])"
                    % re.escape(needle), re.I)
    own = (slug or "").lower()

    def whole(ln):
        # the substring test first: it is cheap and rules out almost every line
        if low not in ln.lower():
            return []
        out = []
        for m in rx.finditer(ln):
            q = _QUALIFIER_RE.search(ln, 0, m.start())
            if q and own and q.group(1).lower() != own:
                continue
            out.append(m.start())
        return out
    return whole


def _orchdoc_slug_or_none(path):
    m = re.search(r"ORCHESTRATOR-DECISIONS-(o\d+)\.md$", str(path).replace("\\", "/"))
    return m.group(1) if m else None


def _lines_naming(lines, needle, orch, slug=None):
    """The multiset (as a sorted list) of compared lines in `lines` that name `needle`."""
    names = _names(needle, slug)
    out = []
    for ln in lines:
        if names(ln):
            s = _needle_line(ln, orch)
            # Checked again AFTER the stamp is cut: a Status line that named the id only
            # inside its review stamp does not name it at all.
            if s is not None and names(s):
                out.append(s)
    out.sort()
    return out


def _entry_block(lines, eid, spans=None):
    """The compared lines of entry `eid` in `lines` as a sorted list, or None when absent.
    """
    if spans is None:
        spans = entry_spans(lines)
    mine = [(lo, hi) for lo, hi, x in spans if x == eid]
    if not mine:
        return None
    out = []
    for lo, hi in mine:
        inside = False
        for ln in lines[lo - 1:hi]:
            if _GENERATED_BEGIN_RE.search(ln):
                inside = True
                continue
            if _GENERATED_END_RE.search(ln):
                inside = False
                continue
            if inside:
                continue
            s = _needle_line(ln, True)
            if s is not None:
                out.append(s)
    out.sort()
    return out


def _parse_hunks(text):
    """[(old_start, old_count, new_start, new_count, removed, added), ...] from a
    `--unified=0` diff, read by the COUNTS in each `@@` header - so a changed line that itself
    begins with `--` or `++` is not mistaken for a file header. None when the text does not
    parse, which the caller treats as touched."""
    hunks = []
    lines = text.split("\n")
    n, i = len(lines), 0
    while i < n:
        m = _HUNK_RE.match(lines[i])
        i += 1
        if not m:
            continue
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) is not None else 1
        c = int(m.group(3))
        d = int(m.group(4)) if m.group(4) is not None else 1
        rem, add = [], []
        while len(rem) < b or len(add) < d:
            if i >= n:
                return None
            ln = lines[i]
            i += 1
            if ln.startswith("\\"):             # "\ No newline at end of file"
                continue
            if ln.startswith("-") and len(rem) < b and not add:
                rem.append(ln[1:])
            elif ln.startswith("+") and len(add) < d:
                add.append(ln[1:])
            else:
                return None
        hunks.append((a, b, c, d, rem, add))
    return hunks


def _reverse_patch(new_lines, hunks):
    """The file BEFORE a commit, rebuilt from the file after it and the commit's hunks.

    None when the hunks do not fit: every hunk's added side must equal what `new_lines` holds
    at that position, and its removed side must land where the header says. A version that
    cannot be verified this way is read from git instead.
    """
    out, pos = [], 0
    for a, b, c, d, rem, add in hunks:
        start = c - 1 if d else c
        if start < pos or start + d > len(new_lines):
            return None
        out.extend(new_lines[pos:start])
        if [x.rstrip() for x in new_lines[start:start + d]] != [x.rstrip() for x in add]:
            return None
        if len(out) != (a - 1 if b else a):
            return None
        out.extend(rem)
        pos = start + d
    out.extend(new_lines[pos:])
    return out


def _read_lines(oid, cwd=None):
    """A blob's lines by object id, or None when git cannot produce it.
    """
    if not oid or _ZERO_OID_RE.match(oid):
        return None
    rc, blob, _ = git(["cat-file", "blob", oid], cwd=cwd)
    if rc != 0:
        return None
    return blob.split("\n")


def _parse_log_records(blob):
    """Records from a `--format=%x1e%H%x1f%cI%x1f%s --raw -p` listing, newest first.
    """
    recs = []
    for chunk in blob.split("\x1e"):
        if not chunk.strip():
            continue
        head, _, rest = chunk.partition("\n")
        parts = head.split("\x1f")
        if len(parts) < 3:
            recs.append(None)                   # cannot tell what this was - touched
            continue
        old = new = status = None
        for ln in rest.split("\n"):
            if ln.startswith(":"):
                f = ln.split()
                if len(f) >= 5:
                    old, new, status = f[2], f[3], f[4]
                break
        recs.append({"sha": parts[0].strip(), "iso": parts[1].strip(), "subject": parts[2],
                     "old": old, "new": new, "status": status,
                     "hunks": _parse_hunks(rest)})
    return recs


def _whys_for(rec, path, needles, versions, cwd=None):
    """{needle: why} for one commit record - "" for a needle it did not touch.

    `versions` carries the file as it stood AFTER this commit ({"oid", "lines"}) between
    calls, so a newest-first walk rebuilds each older version from the one after it instead
    of reading a 2 MB blob per commit. It is updated to the version BEFORE this commit.
    `cwd` is the repo the commit is in: the workspace when None.
    """
    orch = bool(re.search(r"ORCHESTRATOR-DECISIONS-[^/\\]*\.md$", path))
    if rec is not None and rec.get("old") is None and rec.get("hunks") == []:
        return {n: "" for n in needles}
    if rec is None or rec.get("hunks") is None or not rec.get("new") or not rec.get("old"):
        versions.clear()
        return {n: "could not read what this commit did" for n in needles}
    if _ZERO_OID_RE.match(rec["old"]):
        versions.clear()
        return {n: "the file did not exist before this commit" for n in needles}

    out = {}
    removed = [x for h in rec["hunks"] for x in h[4]]
    added = [x for h in rec["hunks"] for x in h[5]]
    ids = []
    for n in needles:
        # (b) - the lines naming it. Common lines are identical on both sides, so the
        # multiset differs exactly when what was removed and what was added differ.
        if (_lines_naming(removed, n, orch, _orchdoc_slug_or_none(path))
                != _lines_naming(added, n, orch, _orchdoc_slug_or_none(path))):
            out[n] = "lines naming %s changed" % n
        elif orch and ENTRY_ID_RE.match(n):
            ids.append(n)
        else:
            out[n] = ""

    carried = versions.get("oid") == rec["new"]
    new_lines = versions.get("lines") if carried else None
    new_spans = versions.get("spans") if carried else None
    old_lines = old_spans = None
    if new_lines is not None:
        # Keep the walk's copy of the file current even when this commit needs no entry
        # text, so the next commit that does is not left reading a blob from git.
        old_lines = _reverse_patch(new_lines, rec["hunks"])
    if ids:
        # (a) - the entry's own text. Needs both versions of the file.
        if new_lines is None:
            new_lines = ([] if _ZERO_OID_RE.match(rec["new"])
                         else _read_lines(rec["new"], cwd))
            if new_lines is not None:
                old_lines = _reverse_patch(new_lines, rec["hunks"])
        if new_lines is not None and old_lines is None:
            old_lines = _read_lines(rec["old"], cwd)
        if new_lines is None or old_lines is None:
            versions.clear()
            for n in ids:
                out[n] = "could not read the file at this commit"
            return out
        if new_spans is None:
            new_spans = entry_spans(new_lines)
        old_spans = entry_spans(old_lines)
        for n in ids:
            out[n] = ("%s's own text changed" % n
                      if _entry_block(old_lines, n, old_spans)
                      != _entry_block(new_lines, n, new_spans) else "")
    if old_lines is not None:
        versions["oid"], versions["lines"], versions["spans"] = rec["old"], old_lines, old_spans
    else:
        versions.clear()
    return out


def _needle_whys(rec, path, needles, versions, cwd=None):
    """`_whys_for` through the cache, keyed on the commit so every entry citing the same
    path and needle shares one reading of it."""
    sha = (rec or {}).get("sha") or ""
    want = [n for n in needles if (sha, path, n) not in _NEEDLE_CACHE] if sha else list(needles)
    if want:
        got = _whys_for(rec, path, want, versions, cwd)
        if sha:
            for n in want:
                _NEEDLE_CACHE[(sha, path, n)] = got[n]
        else:
            return got
    return {n: _NEEDLE_CACHE[(sha, path, n)] for n in needles}


def commit_touched_region(sha, path, needle):
    """Did commit `sha` touch `path#needle`? True/False - see WHAT A COMMIT DID TO A NEEDLE.

    Returns True when it cannot tell - an unreadable commit must not silence the check. A
    commit that did not change `path` at all returns False: git answered, and the answer was
    that nothing there moved.
    """
    rc, blob, _ = git(["show", "--format=%x1e%H%x1f%cI%x1f%s", "--raw", "--no-abbrev", "-p",
                       "--diff-merges=first-parent"] + _DIFF_OPTS + [sha, "--", path])
    if rc != 0:
        return True
    recs = _parse_log_records(blob)
    # git prints NOTHING for a commit that left the path alone - a demonstrated negative,
    # unlike a non-zero exit. Anything else that is not exactly one record is unexplained.
    if not recs and not blob.strip():
        return False
    if len(recs) != 1:
        return True
    if recs[0] is not None and recs[0]["old"] is None and recs[0]["hunks"] == []:
        return False
    return bool(_needle_whys(recs[0], path, [needle], {})[needle])


def first_needle_mover(path, since_iso, needles, limit=NEEDLE_SCAN_LIMIT):
    """(iso, subject, sha, why) for the NEWEST commit on CANONICAL_REF since `since_iso` that
    touched any of `needles` in `path`, or None when none did.

    Every commit since the review is read, newest first, with ONE `git log -p` call; the
    older versions of the file are rebuilt from its diffs rather than read one by one.
    """
    args = ["log", "-n", str(limit + 1), "--format=%x1e%H%x1f%cI%x1f%s", "--raw",
            "--no-abbrev", "-p", "--diff-merges=first-parent"] + _DIFF_OPTS
    if since_iso:
        args.append("--since=%s" % since_iso)
    args += [CANONICAL_REF, "--", path]
    rc, blob, _ = git(args)
    if rc != 0 or not blob.strip():
        return None
    listed = _parse_log_records(blob)
    unread = listed[limit] if len(listed) > limit else None
    # The same test the caller applies to a bare citation: `when > since`, as strings.
    recs = [r for r in listed[:limit]
            if r is None or not since_iso or r["iso"] > since_iso]
    versions = {}
    for rec in recs:
        whys = _needle_whys(rec, path, needles, versions)
        hit = next((whys[n] for n in needles if whys[n]), "")
        if hit:
            if rec is None:
                return (since_iso or "", "(unparseable commit record)", "", hit)
            return (rec["iso"], rec["subject"], rec["sha"], hit)
    if unread is not None and (not since_iso or unread["iso"] > since_iso):
        return (unread["iso"], unread["subject"], unread["sha"],
                "over %d commits since the review; older ones not read" % limit)
    if len(listed) > limit and unread is None:
        return (since_iso or "", "(unparseable commit record)", "",
                "over %d commits since the review; older ones not read" % limit)
    return None



PRODUCT_STALE_BLOCKS = False

PRODUCT_SKIP_SUBJECT_RE = re.compile(r"^(chore\(release\)|chore\(graph\))")

# How many W-PRODUCTNONEEDLE entries `check --strict` lists before "and N more".
PRODUCT_NONEEDLE_STRICT_CAP = 10

# Tried in order when a repo has no origin/HEAD.
_PRODUCT_REF_FALLBACKS = ("refs/remotes/origin/main", "refs/remotes/origin/master")

_PRODUCT_REPOS = None   # [(prefix, root or None, label)], built once per run
_PRODUCT_STATE = {}     # root -> (ref or None, why-not); the deploy branch, looked up once
_PRODUCT_HOLDS = {}     # (root, ref, path) -> bool: is `path` a file on `ref`
_WS_HOLDS = {}          # path -> bool: is `path` a file on CANONICAL_REF in the workspace
_PRODUCT_LOG = {}       # (root, ref, path) -> (since, limit, records)
_PRODUCT_GONE = {}      # (root, ref, path) -> (sha, iso, subject) of its last deletion, or None
_PRODUCT_RENAME = {}    # (root, sha, path) -> the path it was renamed to, or ""

#: {(doc name, repo label, reason): number of cited paths} - the skips `check` prints once.
PRODUCT_NOTES = defaultdict(int)


def _repo_main_root(r):
    """The main checkout that discovered repo `r` belongs to: `r` itself, or for a linked
    worktree the checkout its `.git` file points into. None when that pointer leads nowhere,
    which is a repo whose git data is missing."""
    g = Path(r) / ".git"
    try:
        if g.is_dir():
            return Path(r)
        txt = g.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not txt.lower().startswith("gitdir:"):
        return None
    gd = Path(txt[len("gitdir:"):].strip())
    if not gd.is_absolute():
        gd = Path(r) / gd
    if not gd.exists():
        return None
    low = [x.lower() for x in gd.parts]
    if "worktrees" in low:
        i = len(low) - 1 - low[::-1].index("worktrees")
        common = Path(*gd.parts[:i])
        if common.name.lower() == ".git" and common.is_dir():
            return common.parent
    return Path(r)


def product_repos():
    """[(prefix, root, label)] for every discovered repo that is not the workspace repo.
    """
    global _PRODUCT_REPOS
    if _PRODUCT_REPOS is not None:
        return _PRODUCT_REPOS
    main = _main_worktree()
    anchors = [PROJECTS] + ([main] if main is not None and main != PROJECTS else [])
    ws = {str(a).lower() for a in anchors}
    own = _repo_main_root(PROJECTS)
    if own is not None:
        ws.add(str(own).lower())
    out, seen = [], set()
    for r in citable_repos():
        r = Path(r)
        root = _repo_main_root(r)
        if str(r).lower() in ws or (root is not None and str(root).lower() in ws):
            continue
        prefix = None
        for a in anchors:
            try:
                prefix = r.relative_to(a).as_posix()
                break
            except ValueError:
                continue
        prefix = (prefix or r.name).strip("/")
        if not prefix or prefix == "." or prefix.lower() in seen:
            continue
        seen.add(prefix.lower())
        out.append((prefix, root, (root or r).name))
    _PRODUCT_REPOS = out
    return out


def _batch_check(root, lines):
    """One `git cat-file --batch-check` in `root`: [(type or None)] per input line, or None
    when git could not answer at all."""
    try:
        p = subprocess.run(["git", "-C", str(root), "cat-file", "--batch-check"],
                           input="\n".join(lines) + "\n", capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60,
                           creationflags=flags())
    except Exception:
        return None
    if p.returncode != 0:
        return None
    rows = p.stdout.split("\n")
    if len(rows) < len(lines):
        return None
    out = []
    for row in rows[:len(lines)]:
        f = row.split()
        out.append(f[1] if len(f) == 3 and f[1] in ("blob", "tree", "commit", "tag") else None)
    return out


def _origin_head(root):
    """What `git symbolic-ref refs/remotes/origin/HEAD` answers in `root`, as a full ref name,
    or None. Read from the loose ref file when there is one - a symbolic ref is never packed -
    so the usual case costs no process; anything else asks git."""
    f = Path(root) / ".git" / "refs" / "remotes" / "origin" / "HEAD"
    try:
        txt = f.read_text(encoding="utf-8", errors="replace").strip()
        if txt.startswith("ref: refs/"):
            return txt[len("ref: "):].strip()
    except OSError:
        pass
    rc, name, _ = git(["symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"], cwd=root)
    return name if rc == 0 and name.startswith("refs/") else None


def _deploy_ref_and_holds(root, paths):
    """(ref, why, {path: bool}) for the repo at `root`: its deploy branch - origin/HEAD, as
    CANONICAL_REF is found for the workspace, then the fallbacks - and which of `paths` are
    files on it. `ref` is None, with `why`, when there is no branch to read. The branch and the
    paths are asked of git in ONE call in the usual case, and both are cached for the run."""
    if root in _PRODUCT_STATE and _PRODUCT_STATE[root][0] is None:
        return _PRODUCT_STATE[root] + (None,)
    if root in _PRODUCT_STATE:
        ref = _PRODUCT_STATE[root][0]
        held = _holds(root, ref, paths)
        return (ref, "", held) if held is not None else (None, "git could not read it", None)
    head = _origin_head(root)
    tried = ([head] if head else []) + [r for r in _PRODUCT_REF_FALLBACKS if r != head]
    first = tried[0]
    want = sorted(set(paths))
    got = _batch_check(root, tried + ["%s:%s" % (first, p) for p in want])
    if got is None:
        _PRODUCT_STATE[root] = (None, "git could not read it")
        return None, "git could not read it", None
    ref = next((t for t, kind in zip(tried, got) if kind == "commit"), None)
    if ref is None:
        why = "it has no fetched deploy branch (origin/HEAD, origin/main)"
        _PRODUCT_STATE[root] = (None, why)
        return None, why, None
    short = ref[len("refs/remotes/"):] if ref.startswith("refs/remotes/") else ref
    _PRODUCT_STATE[root] = (short, "")
    if ref == first:
        for p, kind in zip(want, got[len(tried):]):
            _PRODUCT_HOLDS[(root, short, p)] = kind == "blob"
    held = _holds(root, short, want)
    return (short, "", held) if held is not None else (None, "git could not read it", None)


def _holds(root, ref, paths):
    """{path: bool} for which of `paths` are files on `ref` in `root`, from one batched git call
    for whatever is not cached yet. None when git could not answer."""
    want = [p for p in paths if (root, ref, p) not in _PRODUCT_HOLDS]
    if want:
        got = _batch_check(root, ["%s:%s" % (ref, p) for p in want])
        if got is None:
            return None
        for p, kind in zip(want, got):
            _PRODUCT_HOLDS[(root, ref, p)] = kind == "blob"
    return {p: _PRODUCT_HOLDS[(root, ref, p)] for p in paths}


def _workspace_holds(paths):
    """{path: bool} for which of `paths` are files on CANONICAL_REF in the workspace repo, or
    None when git could not answer - and then no path is treated as a product citation."""
    want = [p for p in paths if p not in _WS_HOLDS]
    if want:
        # The ref itself first: with no CANONICAL_REF every path would read as "not here", and
        # workspace files would then be looked for in the product repos.
        got = _batch_check(PROJECTS, [CANONICAL_REF] + ["%s:%s" % (CANONICAL_REF, p)
                                                        for p in want])
        if got is None or got[0] != "commit":
            return None
        for p, kind in zip(want, got[1:]):
            _WS_HOLDS[p] = kind == "blob"
    return {p: _WS_HOLDS[p] for p in paths}


def _plain_rel_path(p):
    """True for a path `cat-file` can look up as `ref:path`. A `./` or `../` path is read
    relative to the process directory, and `...` is an ellipsis, not a directory."""
    if not p or p.startswith("/") or "\\" in p or ":" in p:
        return False
    return not any(seg in ("", ".", "..", "...") for seg in p.split("/"))


def resolve_product_citations(paths, doc_name=""):
    """{path: cite} for the cited `paths` that live in a product repo rather than the workspace.

    A checkable cite is {"label", "root", "ref", "rel"}; one that cannot be checked is
    {"label", "skip": why}, and each skip is counted in PRODUCT_NOTES under `doc_name`. A path
    the workspace repo holds, and a path no repo holds, are left out. See PRODUCT-REPO
    CITATIONS. Git is asked once per repo for all the paths, and repos are asked in parallel.
    """
    try:
        return _resolve_product_citations(paths, doc_name)
    except Exception as e:                  # a bug here must not take `check` down
        PRODUCT_NOTES[(doc_name, "(all)", "lookup failed: %s" % type(e).__name__)] += 1
        return {}


def _gone(root, ref, paths):
    """{path: (sha, iso, subject)} for those of `paths` that `ref` in `root` once held and
    deleted - the newest such commit on the first-parent line - from ONE `git log` for whatever
    is not cached yet. A path `ref` never held is absent. None when git refuses."""
    want = [p for p in paths if (root, ref, p) not in _PRODUCT_GONE]
    if want:
        rc, blob, _ = git(["log", "--first-parent", "--diff-merges=first-parent",
                           "--diff-filter=D", "--no-renames", "--name-only",
                           "--format=%x1e%H%x1f%cI%x1f%s", ref, "--"] + want, cwd=root)
        if rc != 0:
            return None
        found = {}
        for chunk in blob.split("\x1e"):
            if not chunk.strip():
                continue
            head, _, rest = chunk.partition("\n")
            parts = head.split("\x1f")
            if len(parts) < 3:
                continue
            for name in rest.split("\n"):
                name = name.strip()
                if name and name not in found:
                    found[name] = (parts[0].strip(), parts[1].strip(), parts[2])
        for p in want:
            _PRODUCT_GONE[(root, ref, p)] = found.get(p)
    return {p: _PRODUCT_GONE[(root, ref, p)] for p in paths
            if _PRODUCT_GONE[(root, ref, p)] is not None}


def _renamed_to(root, sha, path):
    """The path `path` became in commit `sha` when git sees a rename there, else ""."""
    key = (root, sha, path)
    if key not in _PRODUCT_RENAME:
        new = ""
        rc, out, _ = git(["show", "--format=", "-M", "--name-status",
                          "--diff-merges=first-parent", sha], cwd=root)
        if rc == 0:
            for ln in out.split("\n"):
                f = ln.split("\t")
                if len(f) == 3 and f[0].startswith("R") and f[1] == path:
                    new = f[2].strip()
                    break
        _PRODUCT_RENAME[key] = new
    return _PRODUCT_RENAME[key]


def _parallel(fn, jobs):
    """{job: fn(job)} with the jobs run on threads - each is a git process. A job that raises
    maps to None instead of taking the rest down with it."""
    def safe(j):
        try:
            return j, fn(j)
        except Exception:
            return j, None
    if len(jobs) <= 1:
        return dict(safe(j) for j in jobs)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(16, len(jobs))) as pool:
        return dict(pool.map(safe, jobs))


def _resolve_product_citations(paths, doc_name):
    paths = sorted({p for p in paths if _plain_rel_path(p)})
    if not paths:
        return {}
    ws = _workspace_holds(paths)
    if ws is None:
        return {}
    rest = [p for p in paths if not ws.get(p)]
    if not rest:
        return {}
    repos = product_repos()
    # Several prefixes can name one repo (a checkout and its worktrees), so git is asked once
    # per REPO, keyed by its root - or by its label when its git data is missing.
    key_of = [str(root).lower() if root is not None else "missing:" + label.lower()
              for _prefix, root, label in repos]
    home = {}
    for i, k in enumerate(key_of):
        home.setdefault(k, i)
    prefixed, bare, want = {}, [], defaultdict(set)
    for p in rest:
        low = p.lower()
        best = None
        for i, (prefix, _root, _label) in enumerate(repos):
            if low.startswith(prefix.lower() + "/") and (
                    best is None or len(prefix) > len(repos[best][0])):
                best = i
        if best is not None:
            rel = p[len(repos[best][0]) + 1:]
            if _plain_rel_path(rel):
                prefixed[p] = (key_of[best], rel)
                want[key_of[best]].add(rel)
        elif "/" in p:
            bare.append(p)
            for k in home:
                want[k].add(p)

    def repo(k):
        return repos[home[k]][1], repos[home[k]][2]

    def ask(k):
        root = repo(k)[0]
        if root is None:
            return None, "its git data is missing", None
        return _deploy_ref_and_holds(root, want[k])

    answers = {}
    for k, got in _parallel(ask, sorted(want)).items():
        answers[k] = got if got is not None else (None, "git could not read it", None)

    # Paths the deploy branch does not hold now: did it hold them once? A prefixed path is asked
    # of its own repo; a bare path held by no repo is asked of every readable one.
    held_bare = {p for p in bare
                 if any(answers[k][0] is not None and answers[k][2].get(p) for k in answers)}
    lost = defaultdict(list)
    for p, (k, rel) in prefixed.items():
        if answers[k][0] is not None and not answers[k][2].get(rel):
            lost[k].append(rel)
    for p in bare:
        if p not in held_bare:
            for k in answers:
                if answers[k][0] is not None:
                    lost[k].append(p)
    gone = {}
    for k, got in _parallel(lambda k: _gone(repo(k)[0], answers[k][0], lost[k]),
                            sorted(lost)).items():
        gone[k] = got or {}

    def cite(k, rel, gone_rec=None):
        root, label = repo(k)
        c = {"label": label, "root": root, "ref": answers[k][0], "rel": rel}
        if gone_rec:
            c["gone"] = gone_rec
        return c

    out = {}
    for p, (k, rel) in prefixed.items():
        ref, why, held = answers[k]
        label = repo(k)[1]
        if ref is None:
            out[p] = {"label": label, "skip": why}
            PRODUCT_NOTES[(doc_name, label, why)] += 1
        elif held.get(rel):
            out[p] = cite(k, rel)
        elif gone.get(k, {}).get(rel):
            out[p] = cite(k, rel, gone[k][rel])
    for p in bare:
        holders = [k for k in sorted(answers)
                   if answers[k][0] is not None and answers[k][2].get(p)]
        if not holders:
            holders = [k for k in sorted(gone) if gone[k].get(p)]
        if len(holders) == 1:
            k = holders[0]
            out[p] = cite(k, p, gone.get(k, {}).get(p) if p not in held_bare else None)
        elif len(holders) > 1:
            names = ", ".join(repo(k)[1] for k in holders)
            why = "held by more than one repo (%s); name the repo first to check it" % names
            out[p] = {"label": names, "skip": why}
            PRODUCT_NOTES[(doc_name, names, why)] += 1
        else:
            # No readable deploy branch holds it or held it. A repo that could not be read, but
            # has the file on disk, is the likely home - say that rather than stay silent.
            for k in sorted(answers):
                ref, why, _held = answers[k]
                root, label = repo(k)
                if ref is None and root is not None and (root / p).is_file():
                    out[p] = {"label": label, "skip": why}
                    PRODUCT_NOTES[(doc_name, label, why)] += 1
                    break
    return out


def product_nonneedle_paths(body, product):
    """The paths entry `body` cites into a product repo with no `#needle`, in citation order.

    ONE definition, read by the W-PRODUCTNONEEDLE finding and by the suggestion `restamp` and
    `commit` print, so the three can never disagree about which paths lack a needle. A gone
    file is left out: it gets W-PRODUCTGONE, and a needle would not bring it back.
    """
    regions = cited_regions(body)
    return [p for p in cited_paths(body)
            if product.get(p) and not product[p].get("skip") and not product[p].get("gone")
            and not regions.get(p)]


def entries_changed_since(doc, rel, ref):
    """Ids of the live entries whose text in `doc` on disk differs from `rel` on `ref` - the
    entries a landing of `doc` changes. Compared with `_entry_block`, so a restamp alone is not
    a change. [] when the ref copy cannot be read; a new doc counts every entry."""
    try:
        new = pathlib.Path(doc).read_text(encoding="utf-8", errors="replace").split("\n")
        rc, old_txt, _ = git(["show", "%s:%s" % (ref, rel)])
        old = old_txt.split("\n") if rc == 0 else []
        entries, _ = parse_entries(new)
        new_spans, old_spans = entry_spans(new), entry_spans(old)
        return [e["id"] for e in entries if not e.get("archived")
                and _entry_block(new, e["id"], new_spans) != _entry_block(old, e["id"], old_spans)]
    except Exception:
        return []


def print_needle_hints(doc, eids, lead="  "):
    """Print the W-PRODUCTNONEEDLE suggestion for each of `eids` in `doc` that has one.
    """
    try:
        lines = pathlib.Path(doc).read_text(encoding="utf-8", errors="replace").split("\n")
        entries, _ = parse_entries(lines)
        mine = [e for e in entries if e["id"] in set(eids) and not e.get("archived")]
        if not mine:
            return 0
        product = resolve_product_citations(
            [p for e in mine for p in cited_paths(e["body"])], pathlib.Path(doc).name)
        shown = 0
        for e in mine:
            paths = product_nonneedle_paths(e["body"], product)
            if not paths:
                continue
            shown += 1
            print("%s[note] %s cites product file(s) with no #needle, so no commit there can"
                  " make it stale:" % (lead, e["id"]))
            for p in paths:
                print("%s         %s  (%s)" % (lead, p, product[p]["label"]))
            print("%s       Add #<a word from the line it rests on> inside the backticks, e.g."
                  " `%s#<word>`." % (lead, paths[0]))
        return shown
    except Exception:
        return 0


def _iso_dt(s):
    try:
        d = _dt.datetime.fromisoformat(str(s).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=_dt.timezone.utc)


def _product_listing(root, ref, rel, since_iso, limit):
    """Commit records for `rel` on `ref` in `root` since `since_iso`, newest first, or None
    when git refuses. One `git log -p` per repo and path per run: a listing taken from an
    earlier `since` is reused for a later one when it was not cut off at `limit`."""
    want = _iso_dt(since_iso) if since_iso else None
    hit = _PRODUCT_LOG.get((root, ref, rel))
    if hit is not None:
        since0, limit0, recs = hit
        have = _iso_dt(since0) if since0 else None
        covers = have is None or (want is not None and have <= want)
        if covers and limit0 >= limit and len(recs) <= limit0:
            return recs
    # `--first-parent`: the deploy branch's own line, so a merge carries what landed and is
    # dated when it landed. See THE LOG FOLLOWS THE DEPLOY BRANCH'S FIRST PARENTS.
    args = ["log", "--first-parent", "-n", str(limit + 1), "--format=%x1e%H%x1f%cI%x1f%s",
            "--raw", "--no-abbrev", "-p", "--diff-merges=first-parent"] + _DIFF_OPTS
    if since_iso:
        args.append("--since=%s" % since_iso)
    args += [ref, "--", rel]
    rc, blob, _ = git(args, cwd=root)
    if rc != 0:
        return None
    recs = _parse_log_records(blob) if blob.strip() else []
    _PRODUCT_LOG[(root, ref, rel)] = (since_iso, limit, recs)
    return recs


def product_needle_mover(root, ref, rel, since_iso, needles, skip_subject=PRODUCT_SKIP_SUBJECT_RE,
                         limit=NEEDLE_SCAN_LIMIT):
    """(iso, subject, sha, why) for the newest commit on `ref` in the product repo at `root`,
    after `since_iso`, that changed a line of `rel` naming one of `needles` and whose subject
    `skip_subject` does not match. None when there is none.

    The walk follows the deploy branch's first parents (see THE LOG FOLLOWS THE DEPLOY BRANCH'S
    FIRST PARENTS): a merge counts as what it landed, dated when it landed. Still missed: a
    commit made before the review and fast-forwarded onto the branch after it.

    The needle test is the workspace one, `_needle_whys`, reading blobs from `root`. A commit
    that deletes or renames `rel` removes every line naming the needle, so it counts. Times are
    compared as instants, not as strings, because product commits carry their committer's UTC
    offset. Like `first_needle_mover`, a listing git refuses returns None, and more than
    `limit` commits since the review returns the first one not read.
    """
    listed = _product_listing(root, ref, rel, since_iso, limit)
    if not listed:
        return None
    unread = listed[limit] if len(listed) > limit else None
    since = _iso_dt(since_iso) if since_iso else None

    def after(rec):
        if since is None:
            return True
        t = _iso_dt(rec["iso"])
        return t is None or t > since

    versions = {}
    for rec in listed[:limit]:
        if rec is not None and not after(rec):
            continue
        whys = _needle_whys(rec, rel, needles, versions, root)
        hit = next((whys[n] for n in needles if whys[n]), "")
        if not hit:
            continue
        if rec is None:
            return (since_iso or "", "(unparseable commit record)", "", hit)
        if skip_subject is not None and skip_subject.match(rec["subject"] or ""):
            continue
        return (rec["iso"], rec["subject"], rec["sha"], hit)
    if len(listed) > limit:
        if unread is None:
            return (since_iso or "", "(unparseable commit record)", "",
                    "over %d commits since the review; older ones not read" % limit)
        if after(unread):
            return (unread["iso"], unread["subject"], unread["sha"],
                    "over %d commits since the review; older ones not read" % limit)
    return None



def citations_of(doc_path, ref=None, cwd=None):
    """[(other_doc_name, entry_id, [needles])] - every live entry in another OrchDoc on `ref`
    whose `path#needle` citation names `doc_path`, read the way E-STALEPROSE reads it.
    """
    ref = ref or CANONICAL_REF
    name = pathlib.Path(str(doc_path)).name
    rc, out, _ = git(["grep", "-l", "-F", name + "#", ref, "--", "ORCHESTRATOR-DECISIONS-*.md"],
                     cwd=cwd)
    if rc != 0 or not out.strip():
        return []                      # 1 is git grep's "no match"; anything else, nothing
    rows = []
    for hit in out.splitlines():
        other = hit.split(":", 1)[1] if hit.startswith(ref + ":") else hit.split(":", 1)[-1]
        if pathlib.Path(other).name == name:
            continue                   # a document is not its own dependency
        rc2, text, _ = git(["show", "%s:%s" % (ref, other)], cwd=cwd)
        if rc2 != 0:
            continue
        entries, _ = parse_entries(text.splitlines())
        for e in entries:
            if e.get("archived"):
                continue
            regs = cited_regions(e["body"])
            if not regs:
                continue
            # The same pairing the check makes: the path must be a live citation, not one that
            # appears only inside an attestation, and the needles come from the whole body.
            live = set(cited_paths(e["body"]))
            for pth, needles in regs.items():
                if pth in live and pathlib.Path(pth).name == name:
                    rows.append((pathlib.Path(other).name, e["id"], needles))
    return rows


def landing_wakes(doc_path, base=None, head="HEAD"):
    """[(other_doc_name, entry_id, needle, why)] for entries in other docs that moving
    `doc_path` from `base` to `head` would flag through a `path#needle` citation.

    Rules (a) and (b) exactly as the pull edge applies them to one commit - see
    `_whys_for` - on the diff from `base` to `head`. Bare citations are not listed: any
    landing flags those, so naming them tells the lander nothing.
    """
    base = base or CANONICAL_REF
    cites = citations_of(doc_path, base)
    if not cites:
        return []
    rc, oids, _ = git(["rev-parse", "%s:%s" % (base, doc_path), "%s:%s" % (head, doc_path)])
    ids = oids.split()
    if rc != 0 or len(ids) != 2:
        return []                      # not on the ref yet: nothing landed can depend on it
    if ids[0] == ids[1]:
        return []
    rc, diff, _ = git(["diff"] + _DIFF_OPTS + [base, head, "--", doc_path])
    rec = {"sha": "", "iso": "", "subject": "", "old": ids[0], "new": ids[1],
           "status": "M", "hunks": _parse_hunks(diff) if rc == 0 else None}
    needles = sorted({n for _, _, ns in cites for n in ns})
    whys = _whys_for(rec, str(doc_path), needles, {})
    out = []
    for other, eid, ns in cites:
        hit = next((n for n in ns if whys.get(n)), None)
        if hit:
            out.append((other, eid, hit, whys[hit]))
    return out


def print_landing_wakes(wakes, seconds):
    """The notice `commit` prints. `wakes` from `landing_wakes`."""
    if not wakes:
        print("  [ok] notice - no entry in another doc has a needle this landing touches"
              " (%.1fs)" % seconds)
        return
    print("  [note] this landing will flag %d entr%s in other docs (not a refusal; %.1fs):"
          % (len(wakes), "y" if len(wakes) == 1 else "ies", seconds))
    for other, eid, needle, why in wakes:
        slug = re.sub(r"^ORCHESTRATOR-DECISIONS-|\.md$", "", other)
        print("           %s %s (%s)" % (slug, eid, why))
    print("           Each owner will have to re-read and restamp. If a line only uses an id")
    print("           as an example, an id that does not exist (W9999) wakes nobody.")



WAKES_GUIDANCE = [
    "E-WAKESOTHERS: a line this landing adds names, in double quotes, an entry id that",
    "entries in other docs cite, so landing it flags those entries for their owners to",
    "re-read. Quoting is how an example looks, so that is what this refuses - a heuristic;",
    "a bare mention lands with a notice instead.",
    "  - if the id is only an example, use one that does not exist, such as W9999",
    "  - if the mention is real, land with",
    "      --override E-WAKESOTHERS --because \"<name the entries and why they should be"
    " re-checked>\"",
]


def _quoted_at(ln, i):
    """True when offset `i` of `ln` sits inside double quotes, straight or curly."""
    if ln.count('"', 0, i) % 2 == 1:
        return True
    return ln.rfind("“", 0, i) > ln.rfind("”", 0, i)


def _generated_indices(lines):
    """0-based indices of lines inside a generated region (plate, index, findings index)."""
    out, inside = set(), False
    for i, ln in enumerate(lines):
        if _GENERATED_BEGIN_RE.search(ln):
            inside = True
            out.add(i)
            continue
        if _GENERATED_END_RE.search(ln):
            inside = False
            out.add(i)
            continue
        if inside:
            out.add(i)
    return out


def wake_violations(old_lines, new_lines, hunks, cites, slug=None):
    """[(id, [(line_no, holder_id, text)], [(other_doc_name, citer_id)])] - ids cited from
    other docs that this change mentions in MORE lines outside their own entry than before,
    where at least one added mention is in double quotes. The lines listed are the quoted ones.
    """
    citers = {}
    for other, eid, needles in cites:
        for n in needles:
            if ID_NEEDLE_RE.match(n):
                citers.setdefault(n, []).append((other, eid))
    if not citers or not hunks:
        return []
    old_spans, new_spans = entry_spans(old_lines), entry_spans(new_lines)
    old_gen, new_gen = _generated_indices(old_lines), _generated_indices(new_lines)

    def holder(spans, pos):
        return next((eid for lo, hi, eid in spans if lo <= pos <= hi), None)

    def mentions(side_lines, positions, spans, gen, needle):
        own = needle.split(".", 1)[0]
        hits = _name_hits(needle, slug)
        found = []
        for pos, raw in zip(positions, side_lines):
            if pos - 1 in gen or NOTE_LINE_RE.match(raw.rstrip()):
                continue
            s = _needle_line(raw, True)
            where = hits(s) if s is not None else []
            if not where:
                continue
            h = holder(spans, pos)
            if h != own:
                found.append((pos, h, s, any(_quoted_at(s, i) for i in where)))
        return found

    out = []
    for needle in sorted(citers):
        added, removed = [], []
        for a, b, c, d, rem, add in hunks:
            removed += mentions(rem, range(a, a + b), old_spans, old_gen, needle)
            added += mentions(add, range(c, c + d), new_spans, new_gen, needle)
        quoted = [(p, h, s) for p, h, s, q in added if q]
        if len(added) > len(removed) and quoted:
            out.append((needle, quoted, sorted(set(citers[needle]))))
    return out


def _selftest_wakes():
    """The selftest fixture for E-WAKESOTHERS: True when a new line naming a cited id fires
    and a new line naming an id nobody cites does not. Ids are ones no real doc uses."""
    d = Path(tempfile.mkdtemp())
    try:
        git(["init", "-q", "-b", "main", "."], cwd=d)
        who = ["-c", "user.email=t@t", "-c", "user.name=t", "-c", "core.autocrlf=false"]
        dep = d / "ORCHESTRATOR-DECISIONS-o98.md"
        doc = d / "ORCHESTRATOR-DECISIONS-o99.md"
        dep.write_text("# Orchestrator Decision Doc - o98\n\n## FINDINGS\n\n"
                       "### F9999 - rests on o99's W9999\n**Status:** OPEN - **Owner:** o98\n"
                       "**Depends:** `ORCHESTRATOR-DECISIONS-o99.md#W9999`\n\nbody\n",
                       encoding="utf-8", newline="\n")
        base = ("# Orchestrator Decision Doc - o99\n\n## ON CLAUDE'S PLATE\n\n"
                "### W9998 - the entry being written\n**Status:** OPEN - **Owner:** o99\n\n"
                "body\n\n### W9999 - the entry o98 cites\n**Status:** OPEN - **Owner:** o99\n\n"
                "body\n")
        doc.write_text(base, encoding="utf-8", newline="\n")
        git(who + ["add", "."], cwd=d)
        git(who + ["commit", "-qm", "base"], cwd=d)
        doc.write_text(base.replace("body\n\n### W9999", 'as "W9997" shows\n\n### W9999'),
                       encoding="utf-8", newline="\n")
        quiet = not wake_findings(doc, "main")
        doc.write_text(base.replace("body\n\n### W9999", 'as "W9999" shows\n\n### W9999'),
                       encoding="utf-8", newline="\n")
        fired = [f for f in wake_findings(doc, "main") if f.code == "E-WAKESOTHERS"]
        return quiet and len(fired) == 1 and "F9999" in fired[0].msg
    except Exception:
        return False
    finally:
        _remove_tree(str(d))           # the repo's object files are read-only


def print_wakes_refusal(wakes):
    """The full E-WAKESOTHERS explanation: each line, its id, what it flags, and the two ways
    out. Printed once and untruncated - a finding's detail is cut at 100 characters."""
    if not wakes:
        return
    print()
    for f in wakes:
        print("          %s" % f.msg)
        print("            the line: %s" % (f.detail or ""))
    print()
    for ln in WAKES_GUIDANCE:
        print("          %s" % ln)



PRELABEL_MAP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pre_label_map.json")


def prelabel_advice(label):
    """What to write instead of `label`: the library id from pre_label_map.json when mapped."""
    try:
        with open(PRELABEL_MAP, encoding="utf-8") as fh:
            data = json.load(fh)
        lib_id = data.get(label) if isinstance(data, dict) else None
    except Exception:
        lib_id = None
    if lib_id:
        return "%s is %s; write %s with its Reader link" % (label, lib_id, lib_id)
    return ("name the document by its library id with its Reader link, or by its working title "
            "if it is not filed yet; keep pre-labels inside inline code for internal notes")


def prelabel_text_findings(old_lines, new_lines):
    """[(line_no, entry_id, label)] for each bare pre-label `new_lines` ADDS over `old_lines`."""
    import collections
    import orchdoc_ids
    new_text = "\n".join(new_lines)
    hits = orchdoc_ids.bare_pre_labels(new_text)
    if not hits:
        return []
    starts, pos = [], 0
    for ln in new_lines:
        starts.append(pos)
        pos += len(ln) + 1

    def line_of(off):
        lo, hi = 0, len(starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if starts[mid] <= off:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    def holder_of(entries, ln):
        cur = None
        for e in entries:
            if e["line"] <= ln:
                cur = e["id"]
            else:
                break
        return cur or ""

    new_entries, _s = parse_entries(new_lines)
    old_entries, _s = parse_entries(old_lines)
    seen_old = collections.Counter(old_lines)
    old_count = collections.Counter()
    old_hits = orchdoc_ids.bare_pre_labels("\n".join(old_lines))
    if old_hits:
        import bisect
        ostarts, p = [], 0
        for ln in old_lines:
            ostarts.append(p)
            p += len(ln) + 1
        for lab, off in old_hits:
            old_count[(holder_of(old_entries, bisect.bisect_right(ostarts, off)), lab)] += 1
    new_count = collections.Counter()
    avail = collections.Counter(seen_old)
    is_new = {}
    for i, ln in enumerate(new_lines, start=1):
        if avail[ln] > 0:
            avail[ln] -= 1
            is_new[i] = False
        else:
            is_new[i] = True
    flagged = []
    for lab, off in hits:
        ln = line_of(off)
        who = holder_of(new_entries, ln)
        new_count[(who, lab)] += 1
        if is_new.get(ln):
            flagged.append((ln, who, lab))
    return [(ln, who, lab) for ln, who, lab in flagged
            if new_count[(who, lab)] > old_count[(who, lab)]]


def prelabel_findings(path, ref=None):
    """E-PRELABEL findings for the working copy of `path` against `ref` (the parent the landing
    verifies against). Says nothing when git cannot answer or the doc is not on the ref yet."""
    ref = ref or CANONICAL_REF
    p = pathlib.Path(str(path)).resolve()
    rc, top, _ = git(["rev-parse", "--show-toplevel"], cwd=p.parent)
    if rc != 0 or not top.strip():
        return []
    top = pathlib.Path(top.strip())
    try:
        rel = p.relative_to(top.resolve()).as_posix()
    except ValueError:
        return []
    rc, old, _ = git(["show", "%s:%s" % (ref, rel)], cwd=top)
    if rc != 0:
        return []
    try:
        new_lines = p.read_text(encoding="utf-8").split("\n")
    except (OSError, UnicodeDecodeError):
        return []
    out = []
    for ln, who, lab in prelabel_text_findings(old.split("\n"), new_lines):
        out.append(Finding(
            "E-PRELABEL", ln,
            "%s: a line this landing adds names the script by pre-label %s" % (who or "doc", lab),
            "%s: %s" % (lab, prelabel_advice(lab))))
    return out


def print_prelabel_refusal(found):
    """Per hit: the entry id, the label, the replacement advice. Prints nothing when empty."""
    if not found:
        return
    print()
    print("  E-PRELABEL: %d bare pre-label%s on lines this landing adds. A person reads OrchDocs in"
          % (len(found), "" if len(found) == 1 else "s"))
    print("  the Reader and cannot tell which document `X-1` or `Y-3` means. Name the document by")
    print("  its library id with its Reader link; a label inside inline code is fine. Old lines")
    print("  are never checked.")
    for f in found:
        label, _, advice = f.detail.partition(": ")
        who = f.msg.split(":", 1)[0]
        print("    %s L%d  %s -> %s" % (who, f.line, label, advice))


def _selftest_ref_lookup():
    """The same unresolved citation: E-REFLOOKUP while a repo cannot answer, else E-DEADREF."""
    global git_stdin, citable_repos
    saved = git_stdin, citable_repos, os.environ.pop("CLAUDE_CODE_REMOTE", None)
    base = Path(tempfile.mkdtemp())
    answers, stuck = base / "answers", base / "stuck"
    (answers / ".git").mkdir(parents=True)
    (stuck / ".git").mkdir(parents=True)
    doc = base / "ORCHESTRATOR-DECISIONS-o99.md"
    doc.write_text("## NOTES\n\nLanded in `deadbeef1234567` last week.\n", encoding="utf-8")

    def codes(fails):
        global git_stdin

        def fake(args, payload, cwd=PROJECTS):
            if fails and cwd == stuck:
                return 1, "", "timed out"
            return 0, "\n".join("%s missing" % t for t in payload.split()), ""
        git_stdin = fake
        return {f.code for f in check_doc(doc)}
    try:
        citable_repos = lambda: [answers, stuck]
        failed, clean = codes(True), codes(False)
    finally:
        git_stdin, citable_repos = saved[0], saved[1]
        if saved[2] is not None:
            os.environ["CLAUDE_CODE_REMOTE"] = saved[2]
        _remove_tree(str(base))
    return ("E-REFLOOKUP" in failed and "E-DEADREF" not in failed
            and "E-DEADREF" in clean and "E-REFLOOKUP" not in clean)


def _selftest_prelabel():
    """The selftest fixture for E-PRELABEL: an added bare label fires; the same label already in
    the parent, or in inline code, does not."""
    head = "# Doc\n\n## PLATE\n\n### W9998 - entry\n**Status:** OPEN - **Owner:** o99\n\n"
    base = (head + "old line Y-3 here\n").split("\n")
    fire = (head + "old line Y-3 here\nnew Y-1 line\n").split("\n")
    same = (head + "old line Y-3 here\n").split("\n")
    code = (head + "old line Y-3 here\nnew `Y-1` line\n").split("\n")
    import orchdoc_ids
    saved = orchdoc_ids.PRE_LABEL        # a copy with no label series configured still tests the check
    orchdoc_ids.PRE_LABEL = re.compile(r"(?<![\w/\\.#@-])(?:Y|X)-[0-9]+(?![\w/\\@-])")
    try:
        return (len(prelabel_text_findings(base, fire)) == 1
                and not prelabel_text_findings(base, same)
                and not prelabel_text_findings(base, code))
    finally:
        orchdoc_ids.PRE_LABEL = saved



BURIEDASK_KINDS_OK = ("Q", "D", "T")
_BA_VERBS = (r"rules?|reads?|confirms?|judges?|answers?|decides?|says?|chooses?|approves?|picks?|"
             r"re-?reviews?|reviews?|gives?|replies|names?|signs? off|marks?|tells?|sends?|"
             r"listens?|looks?|checks?|opens?|rejects?|accepts?|settles?|weighs?|selects?|"
             r"okays?|specif(?:y|ies)|states?|rates?|keeps?")
_BA_CACHE = {}


def _ba_patterns():
    """(actor, whether, named, asked) patterns for the human's name from human_name(), cached per name.

    Built when first used, not at import: `human_name()` is defined further down and reads files."""
    n = re.escape(human_name())
    pats = _BA_CACHE.get(n)
    if pats is None:
        pats = _BA_CACHE[n] = (
            re.compile(r"\b(?:%s|he)\s+(?:(?:then|also|still|next|first|now)\s+)?(?:%s)\b"
                       % (n, _BA_VERBS), re.I),
            re.compile(r"\b%s\s+(?:[\w'-]+\s+){0,3}whether\b" % n, re.I),
            re.compile(r"\b%s\b" % n, re.I),
            re.compile(r"\b(?:he|him)\b|\b%s\b(?![’']s)" % n, re.I))
    return pats
_BA_COND_RE = re.compile(r"\b(?:when|after|once|if|until|before|since|because|while|unless)\b", re.I)
_BA_POINTER_RE = re.compile(
    r"#o\d+/[QDT]\d+\b|"
    r"\b(?:entry|entries|see|answers?(?:\s+(?:to|in))?|waits?\s+on|tracked\s+(?:in|as)|"
    r"asked\s+in|recorded\s+(?:in|as))\W{0,4}(?:o\d+:)?[QDT]\d+\b", re.I)
_BA_QUOTE_RE = re.compile("`[^`]*`|\\*\"[^\"]*\"\\*|\"[^\"]*\"|\u201c[^\u201d]*\u201d")


def buried_ask_in(text):
    """True when the sub-item `text` (one unchecked box, continuation lines joined) asks the human
    to act and does not point at a Q, D or T entry."""
    if _BA_POINTER_RE.search(text):
        return False
    bare = _BA_QUOTE_RE.sub(" ", text)
    actor_re, whether_re, named_re, asked_re = _ba_patterns()
    if not named_re.search(bare):
        return False
    for m in actor_re.finditer(bare):
        clause = re.split(r"[.;:\n]", bare[:m.start()])[-1]
        if _BA_COND_RE.search(clause):
            continue
        if re.search(r"\b(?:sometimes|often|usually|always|rarely|never|already)\b",
                     bare[m.end():m.end() + 40], re.I):
            continue          # "the human reads lane sessions sometimes" is a habit, not a request
        return True
    if whether_re.search(bare):
        return True
    for sent in re.findall(r"[^.!;?]*\?", bare):          # each sentence that ends in "?"
        if asked_re.search(sent):
            return True
    return False


def _buried_subitems(lines, entries, any_entry=False):
    """[(line_no, entry_id, text)] for each unchecked top-level `- [ ]` sub-item (continuation
    lines joined). Only open entries that are not Q, D or T, unless `any_entry`."""
    struct = scan_structure(lines)
    out, seen = [], set()
    for e in entries:
        m = re.match(r"^(?:o\d+:)?([A-Z]+)\d", e["id"] or "")
        if not any_entry:
            if not m or m.group(1) in BURIEDASK_KINDS_OK or e.get("archived"):
                continue
            if (status_of(e.get("body") or "") or "") in TERMINAL_STATUS:
                continue
        end = e["line"] + (e.get("body") or "").count("\n")
        i = e["line"] + 1
        while i <= min(end, len(lines)):
            raw = lines[i - 1]
            mm = re.match(r"^(\s{0,1})- \[ \] (.*)$", raw)
            if not mm or struct[i - 1][0] or struct[i - 1][1] > 0 or i in seen:
                i += 1
                continue
            parts, j = [mm.group(2).strip()], i + 1
            while j <= min(end, len(lines)):
                nxt = lines[j - 1]
                if not nxt.strip() or not nxt.startswith(" ") or re.match(r"^\s*(?:[-*] |\d+\. )", nxt):
                    break
                parts.append(nxt.strip())
                j += 1
            seen.add(i)
            out.append((i, e["id"], " ".join(parts)))
            i = j
    return out


def buried_ask_text_findings(old_lines, new_lines):
    """[(line_no, entry_id, text)] for each buried ask `new_lines` ADDS or REWORDS over `old_lines`."""
    import collections
    new_entries, _s = parse_entries(new_lines)
    old_entries, _s = parse_entries(old_lines)
    seen_old = collections.Counter(t for _l, _e, t in _buried_subitems(old_lines, old_entries, True))
    out = []
    for ln, who, text in _buried_subitems(new_lines, new_entries):
        if seen_old[text] > 0:
            seen_old[text] -= 1
            continue
        if buried_ask_in(text):
            out.append((ln, who, text))
    return out


def buried_ask_findings(path, ref=None):
    """E-BURIEDASK findings for the working copy of `path` against `ref`. Says nothing when git
    cannot answer or the doc is not on the ref yet."""
    ref = ref or CANONICAL_REF
    p = pathlib.Path(str(path)).resolve()
    rc, top, _ = git(["rev-parse", "--show-toplevel"], cwd=p.parent)
    if rc != 0 or not top.strip():
        return []
    top = pathlib.Path(top.strip())
    try:
        rel = p.relative_to(top.resolve()).as_posix()
    except ValueError:
        return []
    rc, old, _ = git(["show", "%s:%s" % (ref, rel)], cwd=top)
    if rc != 0:
        return []
    try:
        new_lines = p.read_text(encoding="utf-8").split("\n")
    except (OSError, UnicodeDecodeError):
        return []
    return [Finding("E-BURIEDASK", ln,
                    "%s: an unchecked sub-item asks the human something inside a work entry" % who,
                    text[:160])
            for ln, who, text in buried_ask_text_findings(old.split("\n"), new_lines)]


def buried_ask_sweep(path):
    """Every buried ask in `path` as it stands, with no parent: [(entry_id, text)]. Read-only."""
    lines = pathlib.Path(str(path)).read_text(encoding="utf-8").split("\n")
    entries, _s = parse_entries(lines)
    return [(who, t) for _l, who, t in _buried_subitems(lines, entries) if buried_ask_in(t)]


def print_buried_ask_refusal(found):
    """Per hit: the entry, the sub-item, the remedy. Prints nothing when empty."""
    if not found:
        return
    print()
    print("  E-BURIEDASK: %d question%s for the human written as an unchecked sub-item of a work or"
          % (len(found), "" if len(found) == 1 else "s"))
    print("  finding entry. The plate lists entries, not sub-items, so the human never sees it. Move each")
    print("  question into its own entry, state it in full there, and make the sub-item point to it:")
    print("    orchdoc.py add --doc <doc> --kind question \"<the question in full>\"")
    print("  (or --kind decision, or --kind todo). Old sub-items are never checked.")
    for f in found:
        who = f.msg.split(":", 1)[0]
        print("    %s L%d  - [ ] %s" % (who, f.line, f.detail[:110]))


def _selftest_buried_ask():
    """The selftest fixture for E-BURIEDASK: an added ask fires; a pointer, a checked box and an
    old sub-item do not."""
    head = "# Doc\n\n## PLATE\n\n### W9998 - entry\n**Status:** OPEN - **Owner:** o99\n\n"
    who = human_name().title()
    base = (head + "- [ ] %s rules on whether the title stays.\n" % who).split("\n")
    fire = (head + "- [ ] %s rules on whether the title stays.\n" % who
            + "- [ ] %s reads the draft and gives notes.\n" % who).split("\n")
    point = (head + "- [ ] %s reads the draft (now its own entry, Q9904).\n" % who).split("\n")
    checked = (head + "- [x] %s reads the draft and gives notes.\n" % who).split("\n")
    return (len(buried_ask_text_findings(base, fire)) == 1
            and not buried_ask_text_findings(base, base)
            and not buried_ask_text_findings(base, point)
            and not buried_ask_text_findings(base, checked))


def wake_findings(path, ref=None):
    """E-WAKESOTHERS findings for the working copy of `path` against `ref`.

    Works in the repository that holds `path` (so a fixture in a scratch repo checks itself),
    and says nothing when git cannot answer: a doc that is not on the ref yet has no citers.
    """
    ref = ref or CANONICAL_REF
    p = pathlib.Path(str(path)).resolve()
    rc, top, _ = git(["rev-parse", "--show-toplevel"], cwd=p.parent)
    if rc != 0 or not top.strip():
        return []
    top = pathlib.Path(top.strip())
    try:
        rel = p.relative_to(top.resolve()).as_posix()
    except ValueError:
        return []
    cites = citations_of(rel, ref, cwd=top)
    if not any(ID_NEEDLE_RE.match(n) for _, _, ns in cites for n in ns):
        return []
    rc, old, _ = git(["show", "%s:%s" % (ref, rel)], cwd=top)
    if rc != 0:
        return []
    rc, diff, _ = git(["diff"] + _DIFF_OPTS + [ref, "--", rel], cwd=top)
    hunks = _parse_hunks(diff) if rc == 0 else None
    if not hunks:
        return []
    try:
        new_lines = p.read_text(encoding="utf-8").split("\n")
    except (OSError, UnicodeDecodeError):
        return []
    out = []
    for needle, added, flagged in wake_violations(old.split("\n"), new_lines, hunks, cites,
                                                  _orchdoc_slug_or_none(rel)):
        who = ", ".join("%s:%s" % (re.sub(r"^ORCHESTRATOR-DECISIONS-|\.md$", "", o), e)
                        for o, e in flagged)
        pos, holder, text = added[0]
        out.append(Finding(
            "E-WAKESOTHERS", pos,
            "a new line names %s, which is cited by %s - landing it flags %s"
            % (needle, who, "that entry" if len(flagged) == 1 else "those entries"),
            'L%d in %s: "%s"%s' % (pos, holder or "no entry", text.strip()[:110],
                                   "" if len(added) == 1 else " (+%d more)" % (len(added) - 1))))
    return out


def entries_touched(sha, path):
    """Which entry ids did commit `sha` change lines inside, in `path`?

    So the cost of clearing comes down instead of the net coming in.
    """
    if not sha:
        return None
    rc, diff, _ = git(["show", "--format="] + _DIFF_OPTS + [sha, "--", path], keep_ws=True)
    if rc != 0 or not diff:
        return None

    parsed = _parse_hunks(diff)
    if parsed is not None:
        real = [h for h in parsed if h[4] != h[5]]
        if parsed and not real:
            # Every hunk was a no-op. A demonstrated negative, so [] and not None - but only
            # when the file can be read, the same condition `_entry_ids_in` holds the rest to.
            rc2, blob, _ = git(["show", "%s:%s" % (sha, path)])
            return [] if rc2 == 0 and blob else None
        hunks = [(h[2], h[2] + h[3] - 1) for h in real if h[3]]
        return _entry_ids_in(sha, path, hunks)
    hunks = []
    for ln in diff.split("\n"):
        m = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", ln)
        if m:
            start = int(m.group(1))
            count = int(m.group(2) or "1")
            if count:
                hunks.append((start, start + count - 1))
    return _entry_ids_in(sha, path, hunks)


def _entry_ids_in(sha, path, ranges):
    """Entry ids whose regions in `path` at `sha` overlap any of `ranges` (1-based, inclusive,
    new-file lines). None when there are no ranges or the file cannot be read - the caller
    must not read that as "touched nothing"."""
    if not ranges:
        return None
    rc2, blob, _ = git(["show", "%s:%s" % (sha, path)])
    if rc2 != 0 or not blob:
        return None
    spans = entry_spans(blob.split("\n"))

    ids = []
    for a, b in ranges:
        for lo, hi, eid in spans:
            if a <= hi and b >= lo and eid not in ids:
                ids.append(eid)
    return ids



def fetch_ok(cwd=PROJECTS, remote="origin"):
    """Fetch, and REPORT whether it worked. Never silently proceed on failure.
    """
    rc, _out, err = git(["fetch", "--quiet", "--prune", remote], cwd=cwd)
    return rc == 0, (err or "").strip().splitlines()[-1] if err else ""


def touches_since(doc_slug, since_iso, rev=None):
    """
    {entry_id: (iso, subject)} for `Touches:` trailers naming THIS doc, landed after
    `since_iso`. Timestamps come from git, never from a claim in the text.
    """
    out, bad = {}, {}
    # %H last: the SHA is what lets a caller ask what ROLE the commit played - whether it
    # CREATED the entry it names, or only re-stamped its plate line. Without it the push edge
    # can only see that something touched the entry.
    args = ["log", "--format=%cI%x1f%s%x1f%b%x1f%H%x1e"]
    if since_iso:
        args += ["--since=%s" % since_iso]
    # `rev` lets a caller ask about UNLANDED commits (`origin/main..HEAD`). That distinction is
    # what makes the unqualified-trailer rule enforceable: a commit already on the canonical ref
    # cannot be amended without rewriting shared history, so refusing over it names something
    # nobody can act on.
    args += [rev or CANONICAL_REF]
    rc, blob, _ = git(args)
    if rc != 0 or not blob:
        return out, bad
    for rec in blob.split("\x1e"):
        parts = rec.strip().split("\x1f")
        if len(parts) < 3:
            continue
        when, subject, body = parts[0].strip(), parts[1], parts[2]
        sha = parts[3].strip() if len(parts) > 3 else ""
        m = TOUCHES_RE.search(body)
        if not m:
            continue
        for tok in re.split(r"[,;\s]+", m.group(1)):
            tok = tok.strip().rstrip(".,")
            if not tok:
                continue
            mm = TOUCH_TOKEN_RE.match(tok)
            if not mm:
                bad[tok] = (when, subject, "not an entry id")
                continue
            doc_q, eid = mm.group(1), mm.group(2)
            if doc_q is None:
                bad[tok] = (when, subject, "unqualified - say o<N>:%s" % eid)
                continue
            if doc_slug and doc_q != doc_slug:
                continue                      # names a different doc: not ours
            prev = out.get(eid)
            if prev is None or when > prev[0]:
                out[eid] = (when, subject, sha)
    return out, bad

_ROLE_CACHE = {}

# A line that carries no reasoning: the entry heading, the plate line, a review stamp, a
# provenance tag. A commit that changed only these moved status, not substance.
PLATE_ONLY_RE = re.compile(
    r"^(?:#{1,6}\s|\*\*Status:\*\*|\*\*Owner:\*\*|\*\*Depends:\*\*|\*\*Touches:\*\*|"
    r"\*\*Attested-by:\*\*|\*\*Reviewed:\*\*|\*\*Enriched:\*\*|\*\*Opened:\*\*|"
    r"\*\*Priority:\*\*|"
    r"\|\s*\*\*(?:Last updated|Commissioned|Driven by|Canonical copy|Verify it is current"
    r"|Open on)\b|"
    r"\|\s*\*\*\[|\|\s*\|\s*\|\s*$|\|-{3,}\|"
    r"|"
    r"<!--\s*(?:ORCHDOC:[A-Z_]+|from|verdict|route-tags|GENERATED)\b|\s*$)")


def _generated_lines(sha, doc_name):
    """Every line sitting inside a generated block in `doc_name` at `sha`, as a set.
    """
    rc, blob, _ = git(["show", "%s:%s" % (sha, doc_name)])
    if rc != 0 or not blob:
        return set()
    out, inside = set(), False
    for ln in blob.split("\n"):
        if _GENERATED_BEGIN_RE.search(ln):
            inside = True
            continue
        if _GENERATED_END_RE.search(ln):
            inside = False
            continue
        if inside and ln.strip():
            out.add(ln.strip())
    return out


_CREATED_CACHE = {}


def _creation_times(doc_name, ref=None):
    """{entry_id: iso} - when each entry's heading FIRST appeared on `ref`.
    """
    key = (doc_name, ref or CANONICAL_REF)
    if key in _CREATED_CACHE:
        return _CREATED_CACHE[key]
    out = {}
    # `histogram`: a commit that reorders entries must not report every moved heading as newly
    # added - see WHAT A COMMIT DID TO A NEEDLE. It also shrinks this read on a reorder.
    rc, blob, _ = git(["log", "--format=%x1e%cI", "--unified=0", "--diff-algorithm=histogram",
                       "-p", ref or CANONICAL_REF, "--", doc_name])
    if rc == 0 and blob:
        head = re.compile(r"^\+#{1,6}\s+.*?(?<![A-Za-z0-9])"
                          r"([A-Z]{1,3}(?:\d+[a-z]?|-[A-Z][A-Z0-9]*\d*))\s+-\s")
        for rec in blob.split("\x1e"):
            if not rec.strip():
                continue
            lines = rec.split("\n")
            when = lines[0].strip()
            if not when:
                continue
            for ln in lines[1:]:
                m = head.match(ln)
                if m:
                    out[m.group(1)] = when      # newest first, so the last write is the first
    _CREATED_CACHE[key] = out
    return out


def entry_created(doc_name, eid, ref=None):
    """When `eid` first landed on `ref`, or None if it never has.

    None means UNLANDED, which is a different fact from "old": nothing on the ref can have
    moved under an entry the ref has never carried. Callers turn None into now.
    """
    return _creation_times(doc_name, ref).get(eid)


def _commit_role(sha, doc_name, eid):
    """(created_it, plate_only) for what commit `sha` did to entry `eid` in `doc_name`.
    """
    key = (sha, doc_name, eid)
    if key in _ROLE_CACHE:
        return _ROLE_CACHE[key]
    created = plate_only = False
    # `histogram`, or a reorder reports the entry's heading as added and `created` goes True.
    rc, diff, _ = git(["show", "--format=", "--unified=0", "--diff-algorithm=histogram",
                       sha, "--", doc_name], keep_ws=True)
    if rc == 0 and diff:
        hunks = _parse_hunks(diff)
        kept = [h for h in hunks if not _ack_only_hunk(h[4], h[5])] if hunks is not None else None
        acked = hunks is not None and len(kept) < len(hunks)
        if kept is not None:
            real = [h for h in kept if h[4] != h[5]]
            if len(real) < len(kept):
                acked = True
                kept = real
        if kept is not None:
            adds = [x for h in kept for x in h[5]]
            dels = [x for h in kept for x in h[4]]
        else:
            adds = [ln[1:] for ln in diff.split("\n")
                    if ln.startswith("+") and not ln.startswith("+++")]
            dels = [ln[1:] for ln in diff.split("\n")
                    if ln.startswith("-") and not ln.startswith("---")]
        head = re.compile(r"^#{1,6}\s.*\b%s\b" % re.escape(eid))
        created = any(head.match(a.strip()) for a in adds) and \
            not any(head.match(d.strip()) for d in dels)
        touched = [x for x in adds + dels if x.strip()]
        generated = _generated_lines(sha, doc_name)
        # `acked`: a commit that did nothing but acknowledge has no lines left to judge, and
        # that is a bookkeeping commit, not an empty one.
        plate_only = (bool(touched) or acked) and all(
            PLATE_ONLY_RE.match(x.strip()) or x.strip() in generated for x in touched)

        if not plate_only:
            # Only the hunks that were kept: an ack sitting inside this entry must not be what
            # places the commit there.
            ids = (_entry_ids_in(sha, doc_name, [(h[2], h[2] + h[3] - 1) for h in kept if h[3]])
                   if kept is not None else entries_touched(sha, doc_name))
            dels_ok = all(PLATE_ONLY_RE.match(d.strip()) for d in dels if d.strip())
            if dels_ok and ids is not None and eid not in ids:
                plate_only = True
    _ROLE_CACHE[key] = (created, plate_only)
    return created, plate_only


DECORATION_RE = re.compile(r"^(?:[\s*_`~]+|[^\w\s*_`~]+)*")

SELF_CLAIM_PATTERNS = [
    (re.compile(r"none\s+open", re.I), "claims its own contents are empty"),
    (re.compile(r"\(\s*ACTIVE\s+only\s*\)", re.I), "claims to hold only active items"),
    (re.compile(r"\(\s*was:\s*[^)]*\)", re.I), "carries a vestigial 'was:' label"),
    (re.compile(r"\bactive\s+only\b", re.I), "claims to hold only active items"),
]

HEADING_STATE_RE = re.compile(
    r"\b(BUILT|RUNNING|SHIPPED|COMPLETED?|RESOLVED|FIXED|MERGED|VERIFIED|PASSING|"
    r"WORKING|READY|STOPPED|BLOCKED|LANDED|APPLIED|FINISHED|UNBUILT|PENDING)\b")

HEADING_DATED_CLAIM_RE = re.compile(
    r"\b(?:verified|checked|measured|confirmed|tested|audited|re-?audited|as of)\b"
    r"[^)\n]{0,40}?\d{4}-\d{2}-\d{2}", re.I)
HEADING_COUNT_RE = re.compile(
    r"\b\d+\s+(open|remaining|left|outstanding|pending|done|items?|entries|decisions?)\b",
    re.I)
HEADING_SPLIT_RE = re.compile(r"\s[-–—]\s|:\s")

LINE_CITE_RE = re.compile(r"\blines?\s+\d{1,5}\b", re.I)
SHA_CITE_RE = re.compile(r"`([0-9a-f]{7,40})`")
SHA_HAS_LETTER = re.compile(r"[a-f]")

NOT_A_COMMIT_RE = re.compile(
    r"\b(sha-?256|sha-?1\b|md5|blake|digest|checksum|content[- ]hash|hash prefix|"
    r"prefix|fingerprint|etag|content\()", re.I)
EM_DASH = "\u2014"

# Sections whose entries are decisions and therefore must carry a Status field.
DECISION_SECTION_RE = re.compile(r"DECISION", re.I)

_WORD_BOUND = '(?<![A-Za-z])(?:%s)(?![A-Za-z])'
DONE_MARK_RE = re.compile(
    "\u2705|" + _WORD_BOUND % (
        "DONE|PROVEN|RESOLVED|SHIPPED|VERIFIED|COMPLETED?|LANDED"))
CHECKED_BOX_RE = re.compile(r"^\s*[-*+]\s*\[[xX]\]")
UNCHECKED_BOX_RE = re.compile(r"^\s*[-*+]\s*\[\s\]")
# Retained only to DETECT and strip the literal-text spans already written into the corpus.
GREY_SPAN_RE = re.compile(r"<span\s+style=\"color:\s*#?[0-9a-fA-F]{3,6}\"\s*>")

NOTDONE_MARK_RE = re.compile(
    "\u23f3|\u26d4|" + _WORD_BOUND % (
        "NEVER|NOT DONE|NOT YET|UNTESTED|UNBUILT|OUTSTANDING"
        "|STILL NEED|TODO|BLOCKED|PENDING"))

AMBIGUOUS_RE = re.compile(
    r"\b(?:remaining|next step|awaiting|await|still (?:shows|needs|open|to)|"
    r"do (?:it )?last|"                       # `do it LAST` slipped a rule written for `do LAST`
    r"tbd|to be (?:done|decided|confirmed)|unless|once .{0,24} lands|after .{0,24} lands|"
    r"waiting on|depends on|partial|in part)\b", re.I)

PLATE_HISTORY_RE = re.compile(
    r"~~|\b(?:superseded|corrected|correction|retracted?|"
    r"was (?:false|wrong|stale|incorrect|inaccurate)|"
    r"used to (?:say|read|claim)|earlier version|previously (?:said|read|claimed)|"
    r"turned out to be (?:false|wrong)|o\d+ (?:was wrong|caught|flagged))\b", re.I)

TRACKER_ID_RE = re.compile(r"\b(?:tk_[A-Za-z0-9]{8,}|[A-Z][A-Z0-9]{1,9}-\d+)\b")
DUE_DATE_RE = re.compile(r"\bdue\b(?!\s+to\b)", re.I)


def looks_open(text):
    """Openness by PHRASE or by STRUCTURE. Structure is the half that survives rewording."""
    return bool(AMBIGUOUS_RE.search(text)
                or TRACKER_ID_RE.search(text)
                or DUE_DATE_RE.search(text))


def why_open(text):
    """The exact token that made it ambiguous, so a refusal can be argued with rather than
    merely obeyed. A gate that will not say WHY gets overridden on reflex."""
    for rx, label in ((AMBIGUOUS_RE, None),
                      (TRACKER_ID_RE, "tracker id"),
                      (DUE_DATE_RE, "a due date")):
        m = rx.search(text)
        if m:
            return label or m.group(0)
    return "?"


# Statuses that mean the item is finished and must not sit in an active list.
TERMINAL_STATUS = {"RESOLVED", "ANSWERED", "DONE", "SUPERSEDED", "ARCHIVED", "RETRACTED"}

_ACTIVE_NAME_RE = re.compile(
    r"^(?:YOUR |MY |THE )?(?:DECISIONS?|TO-?DOS?|IN FLIGHT|ON [A-Z][\w'-]*'?S? PLATE"
    r"|ON YOUR PLATE|QUESTIONS?|OPEN)\b", re.I)


# The one place the archive section number is written down. `archive` moves INTO it and
# is_active_section() refuses to call it live; both must agree, so both read this.
ARCHIVE_SECTION = "99"


_CANON_HEAD_RE = re.compile(r"^#{1,3}\s*§\s*([\d.]+)")


def _governing_section(lines, entry_line):
    """The most recent §N heading at or before this line, ignoring prose headings.

    The section an entry BELONGS to is the last canonical one above it. A prose `##` heading in
    between is a subdivision of that section, not a replacement for it - but the parser records
    whatever heading it saw last, so the enclosing section is lost.

    Returned as the bare number ("2.1"), or None when nothing canonical precedes the entry.
    """
    cur, infence = None, False
    for i, line in enumerate(lines, 1):
        if i > entry_line:
            break
        if is_fence_line(line):
            infence = not infence
            continue
        if infence:
            continue
        m = _CANON_HEAD_RE.match(line)
        if m:
            cur = m.group(1)
    return cur


def is_active_section(title):
    """
    True when a section's NAME promises live items.
    """
    if not title:
        return False

    m = SECTION_RE.match(title if title.lstrip().startswith("#") else "## " + title)
    if m:
        num = m.group(1)
        top = num.split(".")[0]
        if top == ARCHIVE_SECTION:          # §99.x is the terminal home, never active
            return False
        if top in ("2", "3"):               # the plate and in-flight promise live items
            return True
        return False                        # links, findings, guards: not live lists

    name = HEADING_SPLIT_RE.split(strip_decoration(title), maxsplit=1)[0]
    return bool(_ACTIVE_NAME_RE.match(name.strip()))

# The visual marker a heading must carry, derived from the Status field. One writable
# home for the fact; the marker is generated from it, so a tick can never claim DONE
# while the field says OPEN.
STATUS_MARKER = {
    "OPEN": "\U0001f534", "BLOCKED": "\u26d4", "PAUSED": "\u23f8\ufe0f",
    "DEFERRED": "\u23f3", "RESOLVED": "\u2705", "ANSWERED": "\u2705",
    # \u23ed\ufe0f - ordered behind something, not stopped by it.
    "SEQUENCED": "\u23ed\ufe0f",
    "DONE": "\u2705", "SUPERSEDED": "\U0001f5c4\ufe0f", "ARCHIVED": "\U0001f5c4\ufe0f",
    "RETRACTED": "\u274c",
    "CONFIRMED": "\U0001f50e", "RECORDED": "\U0001f4dd", "ADOPTED": "\u2705",
    "SHIPPED": "\u2705",
}

# " . " as a pseudo-bullet. It does not render as a list; markdown needs "- " after a
# line break. Field lines legitimately use it, so only long runs count.
FAKE_BULLET_RE = re.compile(r"\s\u00b7\s")

# "(1) ... (2)" buried mid-paragraph rather than broken onto lines.
INLINE_ENUM_RE = re.compile(r"(?:^|[^\n])\((\d)\)\s+\S")

WALL_CHARS = 800          # a paragraph past this, with no internal structure
FAKE_BULLET_MIN = 2       # separators in one line before it reads as a fake list
INLINE_ENUM_MIN = 2       # enumerators in one paragraph



GATE_OVERRIDE_CODES = {"GATE1-REWORD", "E-DAYONLYRULING", "E-DAYONLYOPENED",
                       "BOLDWALL"}

ARCHIVED_ID_RE = re.compile(r"_\(was\s+([A-Z]+\d+)\s*[-–—]\s*[A-Z]+\)_")


def archived_heading_suffix(eid, status):
    """The ONE writer of the archived-id suffix that ARCHIVED_ID_RE reads."""
    return "  _(was %s - %s)_" % (eid, status)


# The heading shape, defined ONCE. `render_orchdoc_reader.archived_coverage` carried its
# own copy of this pattern and its own comparison loop; two readers of one marker is the
# contract defect this file already records twice about this exact suffix, so the reader
# now imports the function below instead of re-deriving it.
ARCHIVED_HEADING_RE = re.compile(r"^#{1,6}\s")


def unreachable_archived(lines, entries):
    """Line numbers of archived HEADINGS in the raw file that `parse_entries` did not reach.
    """
    seen = set(e["line"] for e in entries)
    return [n for n, raw in enumerate(lines, start=1)
            if n not in seen
            and ARCHIVED_HEADING_RE.match(raw)
            and ARCHIVED_ID_RE.search(raw)]

OVERRIDE_RE = re.compile(
    r"^\s*<!--\s*ORCHDOC:OVERRIDE\s+(\S+)\s+by=(\S+)\s+at=(\S+)\s*-->\s*(.*)$",
    re.MULTILINE)


def without_attestations(text):
    """The doc with OVERRIDE attestation lines blanked, for any check that matches CONTENT.

    Lines are blanked rather than deleted so line numbers stay true.
    """
    out, in_att = [], False
    for line in text.split("\n"):
        if OVERRIDE_RE.match(line):
            in_att = True
        elif in_att and not line.strip():
            in_att = False
        out.append("" if in_att else line)
    return "\n".join(out)


def overrides_in(text):
    """[(code, who, when, reason)] recorded in this doc."""
    return [(m.group(1), m.group(2), m.group(3), (m.group(4) or "").strip())
            for m in OVERRIDE_RE.finditer(text)]



VERIFY_LANGUAGE_RE = re.compile(
    r"\b(verified|confirmed|checked|proven|measured|tested|validated)\b", re.I)
# Evidence that a falsifier was actually named: a command, a path, a count, a ref, or an
# explicit statement of what would have contradicted the claim.
FALSIFIER_RE = re.compile(
    # a command, a ref, or an exit code
    r"`[^`]+`|\bgit \w+|\bgrep\b|origin/\w+|exit \d"
    # a count, in digits OR in words - "zero false positives across all eight docs" is
    # a falsifier, and the first version could not see it
    r"|\b\d+\s*(?:of|/)\s*\d+\b|\b\d{2,}\b"
    r"|\b(?:zero|no)\s+\w+|\ball\s+(?:\d+|two|three|four|five|six|seven|eight|nine|ten)\b"
    # an explicit statement of the contrary outcome, including an OBSERVED behaviour -
    # "verified to refuse" names exactly what would have come back the other way
    r"|would have (?:shown|returned|failed|refused|caught|flagged)"
    r"|(?:verified|observed|watched|tested)\s+(?:to|it)\s+\w+"
    r"|both (?:ways|readers|directions)|either direction",
    re.I)

HUMAN_RULING_RE = re.compile(
    r"\b(?:[A-Z][a-z]+|the human|o\d+)\s+"
    r"(ruled|confirmed|decided|chose|directed|said|corrected)\b"
    r"|\bruling\b|\b(?:his|her|their|its) (?:call|judgement|judgment|taste)\b")



SCHEMA_SECTIONS = [
    ("1",    "LINKS AND DOCS",       "every doc and URL this orchestrator owns"),
    ("2",    "LIVE ON {NAME}'S PLATE", "only what needs {NAME}. Nothing else."),
    ("2.0",  "Do today",             "what {NAME} does today, and the routines that repeat"),
    ("2.1",  "Decisions",            "need {NAME}'s ruling"),
    ("2.2",  "Questions",            "need an answer from {NAME}"),
    ("2.3",  "To-Dos",               "need {NAME} to act"),
    ("3",    "ON CLAUDE'S PLATE",    "the orchestrator's own work, NOT the human's plate"),
    ("4",    "FINDINGS",             "what was learned, and why it holds"),
    ("5",    "GUARDS",               "what this orchestrator will not do"),
    ("99",   "COMPLETED",            "closed items. Pinned at 99 so done always sinks."),
    ("99.1", "Decisions",            "ruled"),
    ("99.2", "Questions",            "answered"),
    ("99.3", "To-Dos",               "done"),
]

# Schema sections a doc may leave out. `check` does not report them missing; `scaffold` still
# writes them, in order, when it runs.
OPTIONAL_SECTIONS = {"2.0"}

KIND_SECTION_NUM = {"D": ("2.1", "99.1"), "Q": ("2.2", "99.2"),
                    "T": ("2.3", "99.3"), "A": ("2.3", "99.3"),
                    "F": ("4", "4"), "S": ("4", "4"), "W": ("3", "99.3"),
                    "N": ("2.0", "99.3")}

SECTION_RE = re.compile(r"^#{2,3}\s*(?:[^\w\s+-]*\s*)?\u00a7\s*(\d+(?:\.\d+)?)\b")

# Generated regions. Same contract discipline as the plate: one token, matched
# structurally, and a malformed marker REFUSES rather than guessing.
INDEX_BEGIN_TOKEN = "ORCHDOC:INDEX:BEGIN"
INDEX_END_TOKEN = "ORCHDOC:INDEX:END"
INDEX_BEGIN = ("<!-- %s - generated by `orchdoc.py scaffold`. Do not hand-edit. -->"
               % INDEX_BEGIN_TOKEN)
INDEX_END = "<!-- %s -->" % INDEX_END_TOKEN

# The findings index is its OWN generated region, living at the head of section 4 rather
# than in the top block. Separate markers because they are regenerated independently and
# a reader deletes or collapses one without touching the other.
FINDEX_BEGIN_TOKEN = "ORCHDOC:FINDEX:BEGIN"
FINDEX_END_TOKEN = "ORCHDOC:FINDEX:END"
FINDEX_BEGIN = ("<!-- %s - generated by `orchdoc.py scaffold`. Do not hand-edit. -->"
                % FINDEX_BEGIN_TOKEN)
FINDEX_END = "<!-- %s -->" % FINDEX_END_TOKEN

# The header metadata block. Generated, because every field in it is a MEASUREMENT and
# a hand-kept field like this drifts.
META_BEGIN_TOKEN = "ORCHDOC:META:BEGIN"
META_END_TOKEN = "ORCHDOC:META:END"
META_BEGIN = ("<!-- %s - generated by `orchdoc.py scaffold`. Do not hand-edit. -->"
              % META_BEGIN_TOKEN)
META_END = "<!-- %s -->" % META_END_TOKEN


_NEEDS_GIT = {"E-DEADREF", "W-SHACITE"}


def _self_sha():
    """A commit SHA that resolves in whatever repo we are standing in.

    Used by the W-SHACITE fixture, which must supply a RESOLVABLE sha - that is the whole
    distinction it tests. Returns a deliberately-unresolvable placeholder when there is no
    repo, in which case the fixture is skipped via _NEEDS_GIT rather than run.
    """
    for repo in citable_repos():
        if not (repo / ".git").exists():
            continue
        # Must contain a LETTER, or the SHA detector rejects it by design ("require a
        # letter so plain numbers and dates are not read as SHAs"). A short HEAD sha can
        # be all digits - the first attempt returned 6754330 and the fixture was silently
        # ignored, so neither W-SHACITE nor E-DEADREF fired and the test failed with no
        # explanation. Walk recent commits until one qualifies.
        rc, out, _ = git(["rev-list", "-n", "20", "HEAD"], cwd=repo)
        if rc != 0:
            continue
        for full in out.split():
            for cand in (full[:7], full[:12], full):
                if SHA_HAS_LETTER.search(cand):
                    return cand
    return "0000000"

BLOCKING = {"W-NOPROV", "E-DUPID", "E-SELFCLAIM", "E-NOSTATUS", "E-BADSTATUS", "E-DEADREF",
            "E-STALE", "E-ARCHIVEDMARKER", "E-PLATEDRIFT", "E-SCATTERED",
            "E-STALEPROSE", "E-RUBBERSTAMP", "E-NODEPS", "E-BADMARKER",
            "E-BADTOUCH", "E-AMBIGUOUSDATE", "E-MIXEDSTATE", "E-CLOSEDWITHOPENSUBS", "E-SETTLEDNOTSTRUCK",
            "E-IDSHAPE", "E-IDORDER", "E-ALLSUBSDONE", "E-STUBLEFT", "E-EMPTYLINKS", "E-LEGACYDOC", "E-PLATEHISTORY", "E-LOOSEINPARENT", "E-WRONGSECTION",
            "E-NOOWNER", "E-DONEINACTIVE", "E-DONEBUTOPEN", "E-MARKERDRIFT", "E-SCHEMA", "E-TITLE", "E-ONEH1", "E-FUTUREDATE", "E-NOFETCH", "E-BADID", "E-CONFLICT", "E-IO", "E-NEEDLESPACE", "E-HIDDENENTRY",
            "E-WAKESOTHERS", "E-PICTUREPAGE", "E-REFLOOKUP"}
ADVISORY = {"W-STALLED", "W-DEADCITE", "W-SHACITE", "W-LINECITE", "W-BADLINEREF", "W-EMPTYPROMISE", "W-FAKEBULLETS", "W-INLINEENUM",
            "W-OVERRIDE", "W-STRIKEDONE", "W-UNFALSIFIABLE",
            "W-WALLOFTEXT", "W-PICTURENOTSHOWN", "W-CLOUDREF"}
BLOCKING |= {"E-COPYBYLINK", "E-IMAGEBYPATH", "E-PRELABEL", "E-BURIEDASK", "E-WORDSELSEWHERE"}
ADVISORY |= {"W-PASTEDRIFT", "W-PICUNTRACKED"}
ADVISORY |= {"W-ORCHNOTE"}


CHECK_ONLY = None


def _wants(*codes):
    """True when `check` was not narrowed by --codes, or was narrowed to one of these codes."""
    return CHECK_ONLY is None or not CHECK_ONLY.isdisjoint(codes)


class Finding:
    __slots__ = ("code", "line", "msg", "detail")

    def __init__(self, code, line, msg, detail=""):
        self.code = code
        self.line = line
        self.msg = msg
        self.detail = detail


def strip_decoration(text):
    """Remove leading emoji/bold/tick decoration so an ID can be read."""
    return DECORATION_RE.sub("", text).lstrip()


READER_PORT = 8787


def reader_port():
    """The port the Reader serves on by default: READER_PORT unless this machine says otherwise.
    """
    for raw in (os.environ.get("ORCHDOC_READER_PORT"), _config_get("reader_port")):
        try:
            n = int(str(raw).strip())
        except (TypeError, ValueError):
            continue
        if 1 <= n <= 65535:
            return n
    return READER_PORT


def link_port():
    """The port that written and printed Reader links point at: `"reader_port": N` in the
    workspace's `.orchdoc-config.json` (a standing choice for that workspace, used where 8787 is
    taken), else READER_PORT. The environment variable is NOT read here; see reader_port()."""
    try:
        n = int(str(_config_get("reader_port")).strip())
    except (TypeError, ValueError):
        return READER_PORT
    if 1 <= n <= 65535:
        return n
    return READER_PORT


def reader_link(doc_name, eid):
    stem = str(doc_name)
    if stem.endswith(".md"):
        stem = stem[:-3]
    marker = "ORCHESTRATOR-DECISIONS-"
    if marker not in stem:
        return ""
    slug = stem.split(marker, 1)[1]
    if not re.fullmatch(r"o\d+", slug or ""):
        return ""
    return "http://127.0.0.1:%d/#%s/%s" % (link_port(), slug, eid)


def is_fence_line(raw):
    """True for a line that opens or closes a fenced block: three backticks after any
    indent. THE ONE DEFINITION - `parse_entries` reads fences through it, and so does
    `archive`.
    """
    return raw.lstrip().startswith("```")


def fence_states(lines):
    """[(fenced, open_after)] for each line: `fenced` is True for a fence line and every line
    inside a fence; `open_after` is True when a fence is still open below the line, so a line
    inserted straight under it would be inside that fence.
    """
    out, inside = [], False
    for raw in lines:
        if is_fence_line(raw):
            inside = not inside
            out.append((True, inside))
        else:
            out.append((inside, inside))
    return out


def scan_structure(lines):
    """How `parse_entries` reads each line: a list of (fenced, depth, opens, closes).

    `fenced` is True for a fence line and every line inside a fence. `depth` is the
    `<details>` nesting after the line, which is what makes a heading ARCHIVED; `opens` and
    `closes` say whether the line moved it. One scan, shared, so `archive` can never
    disagree with the parser about where a fold or a fence begins and ends.
    """
    out = []
    in_att = False  # inside an ORCHDOC:OVERRIDE attestation span - explanation, not structure
    depth = 0  # <details> nesting: a heading inside one is ARCHIVED, not live
    # Fences through `fence_states`, the one scan `parse_notes` and `note_slot` read too.
    for raw, (fenced, _open) in zip(lines, fence_states(lines)):
        if fenced:
            out.append((True, depth, 0, 0))
            continue
        opens = closes = 0
        if OVERRIDE_RE.match(raw):
            in_att = True
        elif in_att and not raw.strip():
            in_att = False
        if not in_att:
            low = re.sub(r"<!--.*?-->", "", raw.lower())
            low = re.sub(r"`[^`]*`", "", low)
            if "<details" in low:
                depth += 1
                opens = 1
            if "</details>" in low:
                depth = max(0, depth - 1)
                closes = 1
        out.append((False, depth, opens, closes))
    return out


def parse_entries(lines):
    """
    Return (entries, sections).

    entry  = dict(id, line, level, title, body, section)
    Body runs to the next heading of the same or shallower level.
    """
    heads = []
    # Fences and <details> folds are read by `scan_structure` - see its docstring for why
    # the parser and `archive` share one scan.
    for i, (raw, (fenced, depth, _o, _c)) in enumerate(
            zip(lines, scan_structure(lines)), start=1):
        if fenced:
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", raw)
        if m:
            heads.append((i, len(m.group(1)), m.group(2).rstrip(), depth > 0))

    entries = []
    sections = []
    for idx, (ln, level, title, archived) in enumerate(heads):
        if level <= 2:
            sections.append({"line": ln, "title": title, "level": level})
        cleaned = strip_decoration(title)
        m = ID_RE.match(cleaned)
        _arch_id = None
        if not m:
            _am = ARCHIVED_ID_RE.search(title)
            if _am:
                _arch_id = _am.group(1)
        if not m and not _arch_id:
            continue
        end = len(lines)
        for ln2, lvl2, _t, _a in heads[idx + 1:]:
            if lvl2 <= level:
                end = ln2 - 1
                break
        # Which h1/h2 section is this entry under?
        sec = ""
        for s in sections:
            if s["line"] <= ln:
                sec = s["title"]
        entries.append({
            # archived entries resolve by their demoted id, and are ALWAYS flagged archived -
            # a heading can only carry the `_(was ...)_` suffix by having been archived, so
            # trusting the heads-parser's flag alone would depend on decoration surviving.
            "id": m.group(1) if m else _arch_id,
            "line": ln,
            "level": level,
            "title": title,
            "section": sec,
            "body": "\n".join(lines[ln - 1:end]),
            "archived": archived or bool(_arch_id),
            "id_demoted": bool(_arch_id),
        })
    return entries, sections


class _lock:
    """
    Cross-process lock around a doc's read-modify-write.
    """

    def __init__(self, doc, timeout=10.0):
        self.path = Path(str(doc) + ".lock")
        self.timeout = timeout
        self.fd = None

    def __enter__(self):
        import time
        deadline = time.time() + self.timeout
        while True:
            try:
                self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                return self
            except FileExistsError:
                # A crashed holder must not wedge every future run.
                try:
                    if time.time() - self.path.stat().st_mtime > 60:
                        self.path.unlink()
                        continue
                except (OSError, UnicodeDecodeError):
                    pass
                if time.time() > deadline:
                    raise SystemExit(
                        "[REFUSE] %s is locked by another orchestrator. Retry shortly."
                        % self.path.name)
                time.sleep(0.05)

    def __exit__(self, *exc):
        try:
            if self.fd is not None:
                os.close(self.fd)
            self.path.unlink()
        except (OSError, UnicodeDecodeError):
            pass
        return False


def gh_anchor(heading):
    """
    GitHub-flavoured heading anchor. Markdown DOES support in-document links, so the
    generated index can jump the reader straight to the entry - no HTML build needed.
    Rule: lowercase, drop everything except word chars/space/hyphen, EACH space to a hyphen.

        "Renew the free map-API keys (Alpha + Beta)"
            GFM       #renew-the-free-map-api-keys-alpha--beta
            generated #renew-the-free-map-api-keys-alpha-beta     <- dead

        gh api repos/<owner>/<repo>/contents/<doc>.md -H "Accept: application/vnd.github.html"
    """
    a = heading.lower()
    a = re.sub(r"[^\w\s-]", "", a)
    return "#" + re.sub(r"\s", "-", a)


# Which kind an entry id belongs to, and the order a human scans them in.
KIND_ORDER = [
    ("Q", "QUESTIONS - need an answer from you"),
    ("D", "DECISIONS - need your call"),
    ("A", "ACTIONS - on your plate"),
    ("N", "DO TODAY - due today"),
]


def build_plate_block(entries, raw_lines=None):
    """
    Build the generated index. ONE builder, used by `plate` to write it and by `check`
    to detect a hand-edit - so the rendered block and the derivation cannot diverge.
    """
    live = []
    held = 0          # open, but owned by the orchestrator - counted, not hidden
    for e in entries:
        if e.get("archived") or status_of(e["body"]) not in PLATE_STATUS:
            continue
        # A §2.0 item is on the plate only on the days it is due.
        if not today_on_plate(e):
            continue
        if not owner_is_human(e["body"]):
            held += 1
            continue
        title = strip_decoration(e["title"])
        title = re.sub(r"^%s\s*[-:]\s*" % re.escape(e["id"]), "", title)
        title = title.replace("|", "\\|")
        if len(title) > 88:
            cut = title[:88].rsplit(" ", 1)[0].rstrip(" ,;:-")
            title = (cut or title[:88]) + "…"
        live.append((e["id"], title, owner_text(e["body"]) or "-",
                     gh_anchor(e["title"]), e["body"]))

    def kind_of(eid):
        m = re.match(r"^([A-Z]+)", eid)
        return m.group(1)[0] if m else "?"

    try:
        import priority as _prio
        _sibs = [x["body"] for x in entries if not x.get("archived")]
        # One tally for the whole block - see priority.referrer_counts.
        _counts = _prio.referrer_counts(_sibs)
        _blockers = _prio.blocker_counts(_sibs)

        def _key(row):
            return _prio.rank_key(row[0], row[4], _sibs, counts=_counts)

        def _why(row):
            return _prio.reason_short(row[0], row[4], _sibs, counts=_counts,
                                      blockers=_blockers)
    except Exception:
        _key = lambda row: row[0]          # noqa: E731 - ordering is a nicety, never a gate
        _why = lambda row: ""              # noqa: E731

    block = [PLATE_BEGIN, ""]
    total = 0
    for prefix, label in KIND_ORDER:
        group = [r for r in live if kind_of(r[0]) == prefix]
        if not group:
            continue
        total += len(group)
        block += ["**%s**" % label, "",
                  "| | What it needs from you | Owner | Why here |", "|---|---|---|---|"]
        for row in sorted(group, key=_key):
            eid, title, owner, anchor = row[0], row[1], row[2], row[3]
            block.append("| **[%s](%s)** | %s | %s | %s |"
                         % (eid, anchor, title, owner, _why(row)))
        block.append("")

    other = [r for r in live if kind_of(r[0]) not in {p for p, _ in KIND_ORDER}]
    if other:
        total += len(other)
        block += ["**OTHER OPEN**", "", "| | What | Owner | Why here |",
                  "|---|---|---|---|"]
        for row in sorted(other, key=_key):
            eid, title, owner, anchor = row[0], row[1], row[2], row[3]
            block.append("| **[%s](%s)** | %s | %s | %s |"
                         % (eid, anchor, title, owner, _why(row)))
        block.append("")

    unread = False
    _sec_titles = {e["section"] for e in entries}

    def _title_of(head_line):
        return head_line.lstrip("#").strip()

    _fz = fenced_lines(raw_lines or [])
    cur_sec, bullets = "", 0
    for _i, ln in enumerate(raw_lines or []):
        if _i in _fz:
            continue
        if ln.startswith("## "):
            if cur_sec and is_active_section(cur_sec) and bullets >= 2 and \
                    _title_of(cur_sec) not in _sec_titles:
                unread = True
            cur_sec, bullets = ln, 0
        elif re.match(r"^\s*[-*]\s+\*\*", ln):
            bullets += 1
    if cur_sec and is_active_section(cur_sec) and bullets >= 2 and \
            _title_of(cur_sec) not in _sec_titles:
        unread = True

    if total == 0 and (not entries or unread):
        block += ["⛔ **CANNOT DETERMINE what is open.** No entry in this doc is in a form",
                  "this generator can read - an entry needs a `### <ID> - title` heading",
                  "AND a `**Status:**` field. **This is NOT a claim that nothing is open.**",
                  "Read the sections below directly until the entries carry those fields.",
                  ""]
    elif total == 0:
        block += ["Nothing open. **This line is generated from the entries, so it cannot",
                  "assert a false empty.**", ""]
    if held:
        block += ["_%d open item(s) owned by the orchestrator are NOT shown here - they "
                  "need no decision from you. They live under ON CLAUDE'S PLATE._"
                  % held, ""]
    block += ["_%d open. Generated by `orchdoc.py plate`; edits here are overwritten._"
              % total, PLATE_END]
    return block


REFUSAL_LOG = _shared_state.state_dir() / "gate-refusals.jsonl"


def log_refusal(doc, findings, lines=None):
    """One row per blocking finding, at the moment the gate refuses a landing.
    """
    try:
        REFUSAL_LOG.parent.mkdir(parents=True, exist_ok=True)
        who = actor_for(doc) if doc else "unknown"
        rows = []
        for f in findings:
            text = ""
            if lines and f.line and 0 < f.line <= len(lines):
                text = lines[f.line - 1].strip()[:200]
            rows.append(json.dumps({
                "at": _now_iso(), "by": who,
                "doc": Path(doc).name if doc else None,
                "code": f.code, "line": f.line, "msg": f.msg[:200], "text": text,
            }, ensure_ascii=False))
        with open(str(REFUSAL_LOG), "a", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(rows) + "\n")
    except Exception:
        pass


PICTURES_HEAD_RE = re.compile(
    r"^\*\*(?:Pictures|Screenshots|Mock-?ups)\s*:?\s*\*\*\s*:?\s*(.*)$", re.I)
PICTURES_LINE_RE = re.compile(
    r'^!\[[^\]\n]*\]\([^)\s]+(?:\s+(?:"[^"\n]*"|&quot;(?:(?!&quot;).)*&quot;))?\)$')
PICTURE_TYPES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}   # the Reader's raster list
_PAGE_URL_RE = re.compile(r"\b(?!file:)[a-z][a-z0-9+.-]*://\S+", re.I)
_PAGE_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/])?[\w.@~+%-]+(?:[\\/][\w.@~+% -]*?[\w.@~+%-])*\.html?(?![\w.])", re.I)
_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.I)
_IMG_SRC_RE = re.compile(r"""\bsrc\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.I)
_IMG_ALT_RE = re.compile(r"""\balt\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.I)
_PAGE_READ_CAP = 5 * 1024 * 1024


def pictures_blocks(text):
    """The **Pictures:** blocks in `text` that the Reader will fold, as (heading, [image lines]).

    Stricter than the Reader on purpose: the heading must START a paragraph and the images
    must END it, so anything accepted here is folded there.
    """
    lines = text.splitlines()
    found = []
    for i, ln in enumerate(lines):
        if not PICTURES_HEAD_RE.match(ln.strip()):
            continue
        if i and lines[i - 1].strip() and not lines[i - 1].lstrip().startswith("#"):
            continue                       # joined to the paragraph above: not folded
        imgs, j = [], i + 1
        while j < len(lines) and lines[j].strip():
            if not PICTURES_LINE_RE.match(lines[j].strip()):
                imgs = []
                break
            imgs.append(lines[j].strip())
            j += 1
        if imgs:
            found.append((ln.strip(), imgs))
    return found


def page_pictures(page):
    """The local pictures an HTML page shows, as [(alt, absolute Path)], in page order.
    """
    try:
        if page.stat().st_size > _PAGE_READ_CAP:
            return []
        text = page.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    import urllib.parse
    out, seen = [], set()
    for tag in _IMG_TAG_RE.findall(text):
        m = _IMG_SRC_RE.search(tag)
        src = (m.group(1) if m and m.group(1) is not None else (m.group(2) if m else "")) or ""
        src = src.strip()
        if not src or re.match(r"^(?:[a-z][a-z0-9+.-]*:|/)", src, re.I):
            continue                       # remote, data:, or rooted at a server
        src = urllib.parse.unquote(re.split(r"[?#]", src, 1)[0])
        try:
            p = (page.parent / src).resolve()
        except (OSError, ValueError):
            continue
        if p.suffix.lower() not in PICTURE_TYPES or not p.is_file() or p in seen:
            continue
        seen.add(p)
        a = _IMG_ALT_RE.search(tag)
        alt = ((a.group(1) if a.group(1) is not None else a.group(2)) if a else "") or ""
        out.append((" ".join(alt.split()), p))
    return out


_PIC_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/])?[\w.@~+%-]+(?:[\\/][\w.@~+% -]*?[\w.@~+%-])*\.(?:png|jpe?g|gif|webp)"
    r"(?![\w.])", re.I)
_SHOWN_RE = re.compile(r"!\[[^\]\n]*\]\(([^)\s]+)[^)\n]*\)")
_ROOTS_CACHE = []


def _picture_roots(root=None):
    """Where a picture's path is taken relative to: this tree, then the shared checkout.

    The Reader serves the shared checkout, and a worktree has the same layout, so a picture
    saved in either gets the same relative path. Run from a worktree, a picture in the shared
    checkout is not "outside the workspace". Asked of git once per run.
    """
    if root is not None:
        return [Path(root).resolve()]
    if not _ROOTS_CACHE:
        roots = [Path(PROJECTS).resolve()]
        rc, common, _e = git(["rev-parse", "--path-format=absolute", "--git-common-dir"],
                             cwd=PROJECTS)
        if rc == 0 and common.strip():
            shared = Path(common.strip()).parent.resolve()
            if shared not in roots:
                roots.append(shared)
        _ROOTS_CACHE.extend(roots)
    return list(_ROOTS_CACHE)


def _local_file(raw, roots):
    """The existing file `raw` names: absolute as written, relative against each root."""
    import urllib.parse
    p = Path(urllib.parse.unquote(raw).replace("\\", "/"))
    for c in ([p] if p.is_absolute() else [r / p for r in roots]):
        try:
            c = c.resolve()
        except (OSError, ValueError):
            continue
        if c.is_file():
            return c
    return None


def _without_urls(text):
    return _PAGE_URL_RE.sub(" ", re.sub(r"file:///?", "", text, flags=re.I))


def picture_pages(text, root=None):
    """Each local HTML page `text` names that shows local pictures: [(as written, Path, pics)].
    """
    roots = _picture_roots(root)
    out, seen = [], set()
    for m in _PAGE_PATH_RE.finditer(_without_urls(text)):
        p = _local_file(m.group(0), roots)
        if p is None or p in seen:
            continue
        seen.add(p)
        pics = page_pictures(p)
        if pics:
            out.append((m.group(0), p, pics))
    return out


def pictures_not_shown(text, root=None):
    """Local picture files `text` names by path but never shows as an image: [(as written, Path)].

    A path counts as shown when the same file is the target of a `![alt](path)` anywhere in
    `text`, folded or not.
    """
    roots = _picture_roots(root)
    shown = set()
    for m in _SHOWN_RE.finditer(text):
        p = _local_file(m.group(1), roots)
        if p is not None:
            shown.add(p)
    out, seen = [], set()
    for m in _PIC_PATH_RE.finditer(_without_urls(_SHOWN_RE.sub(" ", text))):
        p = _local_file(m.group(0), roots)
        if p is None or p in shown or p in seen:
            continue
        seen.add(p)
        out.append((m.group(0), p))
    return out


def pictures_block_for(pics, what="", root=None):
    """The **Pictures:** block for [(alt, Path)], plus a line for each picture it cannot hold."""
    roots = _picture_roots(root)
    lines, left = ["**Pictures:** %s" % (what or "<say what they show>")], []
    for alt, p in pics:
        rel = None
        for r in roots:
            try:
                rel = p.resolve().relative_to(r).as_posix()
                break
            except ValueError:
                continue
        if rel is None:
            left.append("outside the workspace, so the Reader cannot show it: %s" % p)
            continue
        if re.search(r"[\s()]", rel):
            left.append("the path has a space or bracket, so the image line cannot hold it: %s"
                        % rel)
            continue
        alt = alt or re.sub(r"[-_]+", " ", p.stem)
        lines.append("![%s](%s)" % (alt.replace("]", ")").replace("[", "("), rel))
    return (lines if len(lines) > 1 else []), left


def cmd_pictures(args):
    """Print the **Pictures:** block for a page of pictures, or for a folder of them."""
    raw = args.path.replace("\\", "/")
    target = Path(raw)
    if not target.is_absolute():
        target = Path(raw) if Path(raw).exists() else Path(PROJECTS) / raw
    target = target.resolve()
    if target.is_dir():
        pics = [("", p) for p in sorted(target.iterdir())
                if p.is_file() and p.suffix.lower() in PICTURE_TYPES]
    elif target.is_file():
        pics = page_pictures(target)
    else:
        print("[REFUSE] no page or folder at %s" % target, file=sys.stderr)
        return 2
    if not pics:
        print("[REFUSE] %s holds no pictures the Reader can show (png, jpg, gif, webp, next to "
              "the page)" % target, file=sys.stderr)
        return 2
    block, left = pictures_block_for(pics, args.what)
    if block:
        print("\n".join(block))
    for why in left:
        print("[NOT IN THE BLOCK] %s" % why, file=sys.stderr)
    if left:
        print("  Copy those pictures into a folder of your own in the workspace (for example "
              "your-folder/<lane>-shots/), with no spaces in the names, and run "
              "this again.", file=sys.stderr)
    return 0 if block else 2


try:
    import copy_guard  # noqa: E402
except ImportError:
    copy_guard = None
import hashlib as _hashlib  # noqa: E402


def fenced_lines(lines):
    """0-based indexes of every line `parse_entries` reads as fenced: each ``` line and every
    line between a pair. Through `scan_structure`, so no check can disagree with the parser."""
    return {i for i, st in enumerate(scan_structure(lines)) if st[0]}


def prose_only(text):
    """`text` with every fenced line blanked, the same number of lines, for a check that reads
    what the prose SAYS. Text pasted into a fence is someone else's, kept word for word, and
    no prose rule may read it as the entry's own words. `text` must start outside a fence,
    as an entry body always does."""
    if not text or "```" not in text:
        return text
    rows = text.split("\n")
    fz = fenced_lines(rows)
    return "\n".join("" if i in fz else r for i, r in enumerate(rows))


def _prose_bodies(entries, lines):
    """Blank the fenced rows of every entry's `body`, in place, keeping the text as written in
    `raw_body` for the reader that needs the structure (the plate builder).
    -> the set of fenced line indexes, so a caller need not scan twice."""
    fz = fenced_lines(lines)
    for e in entries:
        e["raw_body"] = e["body"]
        if not fz:
            continue
        base = e["line"] - 1
        rows = e["body"].split("\n")
        if any((base + k) in fz for k in range(len(rows))):
            e["body"] = "\n".join("" if (base + k) in fz else r for k, r in enumerate(rows))
    return fz


PASTE_MARK = "ORCHDOC:PASTE"
PASTE_MARK_RE = re.compile(
    r"^\s*<!--\s*" + PASTE_MARK + r"\s+src=(?P<src>\S+)\s+commit=(?P<commit>\S+)"
    r"\s+sha256=(?P<sha>[0-9a-f]{64})\s*-->\s*$")
PASTE_FENCE_OPEN = "```text"
PASTE_FENCE_CLOSE = "```"
PASTE_MAX_CHARS = 100000
_PASTE_SAFE = "/:._-~@+()[]!$&,;="
_PASTE_BREAKS = "\v\f\x1c\x1d\x1e\x85\u2028\u2029"


def paste_marker(src, commit, sha):
    """The ONE writer of the provenance line above a pasted block. `PASTE_MARK_RE` reads it."""
    import urllib.parse
    return "<!-- %s src=%s commit=%s sha256=%s -->" % (
        PASTE_MARK, urllib.parse.quote(src, safe=_PASTE_SAFE), commit, sha)


def parse_paste_marker(line):
    """{"src", "commit", "sha"} for a line `paste_marker` wrote, else None."""
    m = PASTE_MARK_RE.match(line or "")
    if not m:
        return None
    import urllib.parse
    return {"src": urllib.parse.unquote(m.group("src")), "commit": m.group("commit"),
            "sha": m.group("sha")}


def paste_canonical(text):
    """Text as a pasted block holds it: LF line ends, and no final line break (the closing
    fence ends the last line). Every hash and comparison of pasted text goes through this."""
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    return t[:-1] if t.endswith("\n") else t


def paste_digest(canonical_text):
    """sha256 of text ALREADY passed through `paste_canonical` (or read out of a block).
    Never canonicalize twice: a text ending in a blank line would lose it and stop matching."""
    return _hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()


def _paste_src(path, roots=None):
    """How a marker names its source: relative to the workspace when the file is inside it,
    else the absolute path, both with forward slashes."""
    p = Path(path).resolve()
    for r in (roots if roots is not None else _picture_roots()):
        try:
            return p.relative_to(Path(r).resolve()).as_posix()
        except ValueError:
            continue
    return p.as_posix()


def _paste_src_path(src, roots=None):
    """The file a marker's `src` names on this computer, or None."""
    p = Path((src or "").replace("\\", "/"))
    if not str(p).strip():
        return None
    cands = [p] if p.is_absolute() else [Path(r) / p for r in
                                         (roots if roots is not None else _picture_roots())]
    for c in cands:
        try:
            c = c.resolve()
        except (OSError, ValueError):
            continue
        if c.is_file():
            return c
    return None


def _paste_commit(path):
    """The commit that last changed `path` (12 characters), "uncommitted" when the file has
    changes git has not recorded (or none at all), "none" outside a repository."""
    p = Path(path).resolve()
    rc, top, _e = git(["rev-parse", "--show-toplevel"], cwd=str(p.parent))
    top = (top or "").strip()
    if rc != 0 or not top:
        return "none"
    try:
        rel = p.relative_to(Path(top).resolve()).as_posix()
    except ValueError:
        return "none"
    rc, st, _e = git(["status", "--porcelain", "--", rel], cwd=top)
    if rc != 0 or st.strip():
        return "uncommitted"
    rc, h, _e = git(["log", "-1", "--format=%H", "--", rel], cwd=top)
    h = (h or "").strip()
    return h[:12] if rc == 0 and re.fullmatch(r"[0-9a-f]{40}", h) else "uncommitted"


def _paste_read(path):
    """(the file's text, canonical, None), or (None, why it cannot be pasted whole).
    Over the cap is a refusal: the text is never shortened."""
    try:
        size = Path(path).stat().st_size
    except OSError as ex:
        return None, "cannot read it (%s)" % type(ex).__name__
    if size > PASTE_MAX_CHARS * 4:
        return None, ("it is %d bytes, over the %d-character limit, and paste never shortens "
                      "text" % (size, PASTE_MAX_CHARS))
    try:
        raw = Path(path).read_bytes()
    except OSError as ex:
        return None, "cannot read it (%s)" % type(ex).__name__
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, "it is not UTF-8 text"
    if text.startswith("\ufeff"):
        text = text[1:]
    text = paste_canonical(text)
    if len(text) > PASTE_MAX_CHARS:
        return None, ("it is %d characters, over the %d-character limit, and paste never "
                      "shortens text" % (len(text), PASTE_MAX_CHARS))
    return text, None


def pasted_blocks(lines, fz=None):
    """Every pasted block in `lines`, as [{"marker", "open", "close", "text", "src", "commit",
    "sha"}] with 0-based line indexes. A block is a marker line (outside any fence), a fence
    on the very next line, the text, and the next fence line. "open" is None when no fence
    follows the marker; "close" and "text" are None when the fence never closes."""
    fz = fenced_lines(lines) if fz is None else fz
    out = []
    for i, ln in enumerate(lines):
        if i in fz or PASTE_MARK not in ln:
            continue
        mk = parse_paste_marker(ln)
        if not mk:
            continue
        rec = dict(mk, marker=i, open=None, close=None, text=None)
        if i + 1 < len(lines) and is_fence_line(lines[i + 1]):
            rec["open"] = i + 1
            for j in range(i + 2, len(lines)):
                if is_fence_line(lines[j]):
                    rec["close"] = j
                    rec["text"] = "\n".join(lines[i + 2:j])
                    break
        out.append(rec)
    return out


def paste_hazards(text):
    """Why `text` cannot sit inside a pasted block without some reader of the doc acting on
    it, as ["line N <why>"]; empty when it can. Each reason names what it would fool: these
    readers match raw lines, fence or no fence."""
    return ["line %d %s" % (n, why) for n, why, _fence in _paste_hazard_rows(text)]


def _paste_hazard_rows(text):
    """[(line number, why, True for a line starting with ```)], one per line `paste_hazards`
    names, so a fix can count the ``` lines apart from the rest."""
    out = []
    for n, ln in enumerate(text.split("\n"), 1):
        bad = [c for c in ln if c in _PASTE_BREAKS]
        fence = False
        if bad:
            why = ("holds the line-break character U+%04X, so the doc would read it back as two "
                   "lines" % ord(bad[0]))
        elif "\x00" in ln:
            why = "holds a NUL character"
        elif is_fence_line(ln):
            why = ("starts with ```, which would close the block early (write the source's own "
                   "fences as ~~~)")
            fence = True
        elif OVERRIDE_RE.match(ln):
            why = "is an ORCHDOC:OVERRIDE attestation line, which gate 1 reads"
        elif NOTE_LINE_RE.match(ln):
            why = "is a note line, which the note commands address and rewrite"
        elif _GENERATED_BEGIN_RE.search(ln) or _GENERATED_END_RE.search(ln):
            why = "is a generated-region marker"
        elif ARCHIVED_HEADING_RE.match(ln) and ARCHIVED_ID_RE.search(ln):
            why = "is an archived entry heading, which E-HIDDENENTRY reads"
        elif PASTE_MARK in ln and parse_paste_marker(ln):
            why = "is a paste provenance line"
        elif REVIEWED_RE.search(ln):
            why = "reads as a review stamp, and review dates are read from the whole entry"
        elif RESOLVED_LINE_RE.search(ln) or ln.startswith("**Resolved "):
            why = "reads as a ruling line, which resolve and gate 1b read"
        elif DEPENDS_RE.match(ln):
            why = "reads as a Depends line, which E-STALEPROSE follows"
        else:
            continue
        out.append((n, why, fence))
    return out


def paste_cmd(slug, eid, src):
    """The paste command, as every fix prints it."""
    return '%s paste --doc %s %s --file "%s"' % (tool_command(), slug, eid, src)


UNTRUSTED_PASTE_FOLDERS = {
    "private-feedback": ("it is under a folder named private-feedback, where a feedback form "
                     "puts what its users write"),
    "task-export": "it is under task-export/, an old export, whose text is untrusted",
}
_UNTRUSTED_TAIL = (", and an OrchDoc is read by sessions that hold a shell and push access, so "
                   "text from there never goes into one")


def _outsider_text(path):
    """None when `path` may be pasted as far as who wrote it goes, else (code, why), read from
    the path alone (the file is never opened): ("untrusted", why) for text others wrote, or
    ("nocheck", why) when ingress_taint.py cannot be loaded to ask."""
    forms = [Path(path)]
    try:
        forms.append(Path(path).resolve())
    except (OSError, ValueError):
        pass
    for p in forms:
        for part in p.parts[:-1]:
            why = UNTRUSTED_PASTE_FOLDERS.get(part.lower())
            if why:
                return "untrusted", why + _UNTRUSTED_TAIL
    try:
        import ingress_taint
        rx = ingress_taint.INGRESS_READ
    except Exception:
        return "nocheck", ("ingress_taint.py cannot be loaded, so its path cannot be checked for "
                           "text others wrote")
    if any(rx.search(p.as_posix()) or rx.search(str(p)) for p in forms):
        return "untrusted", ("its path is one ingress_taint.INGRESS_READ names, whose text "
                             "others wrote" + _UNTRUSTED_TAIL)
    return None


def _paste_check(path):
    """(text, None) when `paste` takes the file at `path`, else (None, refusal). The refusal is
    {"code", "why", "fences", "others", "hazards", "module"}: code is one of orchdoc, secret,
    untrusted, read, empty, lines, credential, nocheck; why is a clause for "paste refuses
    <file>: <why>"; for "lines", hazards is paste_hazards' list, fences the 1-based numbers of
    its lines starting with ``` and others the rest of it; for "nocheck", module is the file
    to restore. The first refusal in cmd_paste's order wins, except that a credential outranks
    the lines: changing them would not make paste take the file. A file refused by its path
    (orchdoc, secret, untrusted) is never opened."""
    p = Path(path)

    def no(code, why, fences=(), others=(), hazards=(), module=None):
        return None, {"code": code, "why": why, "fences": list(fences), "others": list(others),
                      "hazards": list(hazards), "module": module}
    if p.name.startswith("ORCHESTRATOR-DECISIONS-"):
        return no("orchdoc", "it is an OrchDoc")
    secretish = copy_guard.SECRETISH if copy_guard is not None else \
        re.compile(r"(?i)(^|[\\/])\.?secrets?[\\/]|(^|[\\/])\.env(\.|$)|secret|credential")
    if secretish.search(p.as_posix()):
        return no("secret", "it looks like a secrets file by its path. Its text is never pasted "
                            "into an OrchDoc, which is pushed to GitHub")
    outside = _outsider_text(p)
    if outside is not None:
        return no(outside[0], outside[1],
                  module="ingress_taint.py" if outside[0] == "nocheck" else None)
    text, why = _paste_read(p)
    if text is None:
        return no("read", why)
    if not text.strip():
        return no("empty", "it is empty")
    rows = _paste_hazard_rows(text)
    try:
        import secret_patterns
    except ImportError:
        return no("nocheck", "secret_patterns.py is missing, so its text cannot be checked for "
                             "credentials", module="secret_patterns.py")
    kind = secret_patterns.find_credential(text)
    if kind:
        return no("credential", "its text holds something shaped like %s, which never goes "
                                "into an OrchDoc (the value is not shown here)" % kind)
    if rows:
        return no("lines", "%d of its lines would be acted on inside the pasted block"
                  % len(rows), [n for n, _w, f in rows if f],
                  ["line %d %s" % (n, w) for n, w, f in rows if not f],
                  ["line %d %s" % (n, w) for n, w, _f in rows])
    return text, None


def _paste_refusal_fix(ref, name, eid, cmd, replacing=False):
    """The fix for a file paste refuses (`ref` from `_paste_check`), named `name`, for entry
    `eid`; `cmd` is the paste command to run once the file is changed. `replacing`: the entry
    holds an earlier paste of the file, which the snippets would replace."""
    def lines_of(n):
        return "%d line%s" % (n, "" if n == 1 else "s")
    instead = (", in place of the pasted block and the provenance line above it"
               if replacing else "")
    by_hand = ("put the text the human needs into %s as a fenced block yourself%s, and point the human to "
               "it, not to the file" % (eid, instead))
    code, fences, others = ref["code"], ref["fences"], ref["others"]
    if code == "lines" and fences:
        out = ("%s has %s starting with ``` (%s), which paste refuses because each would end the "
               "pasted block early" % (name, lines_of(len(fences)),
                                       ("the first at line %d" if len(fences) > 1
                                        else "at line %d") % fences[0]))
        also = ""
        if others:
            n = len(others)
            out += "; it also has %d other line%s paste refuses (the first: %s)" % (
                n, "" if n == 1 else "s", others[0])
            also = ", and %s too," % ("that line" if n == 1 else "those lines")
        return ("%s. Either change those fences to ~~~ in %s%s and run: %s; or put the snippets "
                "into %s as fenced blocks yourself%s, and point the human to them, not to the file"
                % (out, name, also, cmd, eid, instead))
    if code == "lines":
        return ("paste refuses %s: it has %s the doc's tools would act on inside the pasted "
                "block (the first: %s). Change %s in %s, then run: %s"
                % (name, lines_of(len(others)), others[0],
                   "that line" if len(others) == 1 else "those lines", name, cmd))
    if code == "untrusted":
        then = ("take the pasted block and the provenance line above it out of %s, and tell "
                "the human where they can read that text" % eid if replacing else
                "tell the human where they can read it, in words that do not ask them to copy or "
                "paste from that file, and never copy its text into an OrchDoc")
    else:
        then = {"orchdoc": "link the entry that holds the text, or paste the file the text came "
                           "from",
                "secret": "tell the human where the value is kept",
                "empty": "write the text into it, then run: %s" % cmd,
                "nocheck": "restore .shared/scripts/%s, then run: %s" % (
                    ref.get("module") or "the module named", cmd),
                "credential": ("put the text without that value into %s as a fenced block "
                               "yourself%s, and tell the human where the value is kept"
                               % (eid, instead)),
                }.get(code, by_hand)
    return "paste refuses %s: %s. Instead, %s" % (name, ref["why"], then)


# The refusals whose fix keeps the file's text out of the entry: a wrapper that says "put the
# text in the entry" around such a fix would contradict it.
_PASTE_KEEPS_OUT = ("secret", "untrusted")


def paste_fix_parts(slug, eid, path, roots=None, replacing=False):
    """(fix, lands): `paste_fix`'s text, and False when that fix keeps the text out of the
    entry (a secret, or text others wrote), so no caller wraps it in "put the text there"."""
    roots = roots if roots is not None else _picture_roots()
    name = _paste_src(path, roots)
    cmd = paste_cmd(slug, eid, name)
    _text, ref = _paste_check(path)
    if ref is None:
        return cmd, True
    return (_paste_refusal_fix(ref, name, eid, cmd, replacing),
            ref["code"] not in _PASTE_KEEPS_OUT)


def paste_fix(slug, eid, path, roots=None, replacing=False):
    """The fix for text the human must copy from the file at `path`, on this computer: the paste
    command when paste takes the file, else why it does not and what to do instead."""
    return paste_fix_parts(slug, eid, path, roots, replacing)[0]


def paste_save_fix(slug, eid):
    """The fix for text that is not in a file on this computer (a URL, or a path not here)."""
    return ("save the text to a file (write any line in it that starts with ``` as ~~~), then: "
            + paste_cmd(slug, eid, "<that file>"))


# --- who acts, and on which rows --------------------------------------------------------
_AGENT_WHO_RE = re.compile(r"^\s*(?:o\d+(?:L\d+)?|orch\w*|claude\w*|agent|lane|session)\b",
                           re.I)


def _note_by_agent(who):
    """True when a note's author is a session, not the human.
    """
    return bool(_AGENT_WHO_RE.match(who or ""))


_ORCH_CALLER_RE = re.compile(r"^\s*o\d+(?:L\d+)?\s*$", re.I)

ORCH_NOTE_REFUSAL = (
    "notes are the human's, and a session cannot write or edit one (%s). Put a status update in the "
    "entry body as a ticked box or a dated line. Put a question to the human in the body of its own "
    "Q entry: `orchdoc.py add --kind question`. `note --ack`, `--list`, `--show` and `--delete` "
    "of a note labelled with an orchestrator stay available."
)


ORCH_ENV_VARS = ("ORCHDOC_ME", "CLAUDECODE", "ORCHDOC_LANE")
_LANE_PREFIX_RE = re.compile(r"^\s*(o\d+)", re.I)
_DOC_OWNER_RE = re.compile(r"(?:^|ORCHESTRATOR-DECISIONS-)(o\d+)(?:\.md)?$", re.I)


def owner_writer_env(env=None):
    """A copy of `env` (default os.environ) without the variables that mark a session, for a
    child that writes the human's own words (the Reader's writer, a test that writes as the human)."""
    out = dict(os.environ if env is None else env)
    for var in ORCH_ENV_VARS:
        out.pop(var, None)
    return out


def _session_caller(args):
    """The name to record when Claude Code or a lane is running this, else None."""
    lane = (os.environ.get("ORCHDOC_LANE") or "").strip()
    cc = (os.environ.get("CLAUDECODE") or "").strip()
    if not lane and cc in ("", "0"):
        return None
    me = (os.environ.get("ORCHDOC_ME") or "").strip()
    if me and _ORCH_CALLER_RE.match(me):
        return me
    m = _LANE_PREFIX_RE.match(lane)
    if m:
        return m.group(1).lower()
    m = _DOC_OWNER_RE.search(str(getattr(args, "doc", None) or "").strip())
    if m:
        return m.group(1).lower()
    return "orchestrator"


def _orch_caller(args):
    """The o<N> name when the caller is an orchestrator (--by, $ORCHDOC_ME, or a Claude Code
    session or lane: see `_session_caller`), else None."""
    for v in (getattr(args, "by", None), os.environ.get("ORCHDOC_ME")):
        if v and _ORCH_CALLER_RE.match(v):
            return v.strip()
    return _session_caller(args)


def _orch_note_refusal(args, what):
    """Print the refusal and return True when an orchestrator is writing the human's text."""
    who = _orch_caller(args)
    if not who:
        return False
    _note_fail(args, ORCH_NOTE_REFUSAL % ("%s as %s" % (what, who)))
    return True


def _doc_slug(doc_name):
    """
    Not for the wake path or `_dup_id_refusal`, which want None for a file that is not an
    OrchDoc: they call `_orchdoc_slug_or_none`."""
    m = re.search(r"ORCHESTRATOR-DECISIONS-(o\d+)", str(doc_name))
    return m.group(1) if m else Path(str(doc_name)).stem


def _own_span(e, starts, n):
    """(a, b): the 0-based rows an entry holds itself, from its heading up to the next entry's
    heading (a child entry holds its own rows) or the end of its body."""
    import bisect
    a = e["line"] - 1
    end = a + len(e["body"].split("\n"))
    k = bisect.bisect_right(starts, a)
    nxt = starts[k] if k < len(starts) else n
    return a, min(nxt, end, n)


def _guard_lines(lines, live, fz, a, b):
    """Rows a..b-1 as the copy and picture checks read them, and the indexes (within the
    result) of the rows that are notes. None for a fenced row (it ends a paragraph); "" for an
    attestation, a review stamp or the human's note; an agent's note as its text alone; HTML
    comments blanked."""
    out, notes = [], set()
    for i in range(a, b):
        ln = lines[i]
        if i in fz:
            out.append(None)
            continue
        if live[i] != ln or STAMP_LINE_RE.match(ln):
            out.append("")
            continue
        m = NOTE_LINE_RE.match(ln)
        if m:
            if _note_by_agent(m.group("who")):
                notes.add(len(out))
                out.append(decode_note_text(m.group("text"), m.group("enc")).replace("\n", " "))
            else:
                out.append("")
            continue
        out.append(re.sub(r"<!--.*?-->", " ", ln))
    return out, notes


def _guard_open(e, force_open=()):
    """An entry the guards read: live, or named in `force_open` (resolve reads its ruling)."""
    return e["id"] in force_open or (not e.get("archived")
                                     and status_of(e["body"]) not in TERMINAL_STATUS)


def _human_acts(e):
    return bool(re.match(r"^T\d", e["id"])) or owner_is_human(e["body"])


def _entry_insert_at(lines, e, starts):
    """Where paste and image put a new block in entry `e`: after the last non-blank row of
    its own text, and never inside a <details> fold or a generated region (the block goes
    before the first one in the entry). -> 0-based index to insert at."""
    a, b = _own_span(e, starts, len(lines))
    st = scan_structure(lines)
    stop = b
    for i in range(a + 1, b):
        fenced, depth, opens, closes = st[i]
        if fenced:
            continue
        if opens or closes or depth != st[a][1] or _GENERATED_BEGIN_RE.search(lines[i]):
            stop = i
            break
    k = stop
    while k > a + 1 and not lines[k - 1].strip():
        k -= 1
    return k


# --- E-COPYBYLINK ---------------------------------------------------------------------
def _fenced_texts(rows):
    """[(open, close, text, marked)] for each closed fenced block in `rows` (indexes within
    rows, which must start outside a fence); `marked` when a PASTE marker is the row above."""
    marked_opens = {blk["open"] for blk in pasted_blocks(rows) if blk["open"] is not None}
    out, i = [], 0
    while i < len(rows):
        if is_fence_line(rows[i]):
            for j in range(i + 1, len(rows)):
                if is_fence_line(rows[j]):
                    marked = i in marked_opens
                    out.append((i, j, "\n".join(rows[i + 1:j]), marked))
                    i = j
                    break
            else:
                break
        i += 1
    return out


def _norm_text(t):
    """Text compared loosely: canonical line ends, trailing spaces and outer blank lines gone."""
    t = paste_canonical(t)
    return "\n".join(ln.rstrip() for ln in t.split("\n")).strip("\n")


def _local_text_file(ref, roots):
    """The text file a copy instruction's `ref` names on this computer, or None. A GitHub file
    URL counts as its path in the repository; any other URL, and a secret, never do."""
    if copy_guard is None:
        return None
    src = copy_guard.local_source(ref)
    if re.match(r"(?i)^[a-z][a-z0-9+.-]*://", src) or copy_guard.SECRETISH.search(src):
        return None
    src = os.path.expanduser(src.split("#")[0].strip())
    if not src:
        return None
    p = Path(src.replace("\\", "/"))
    for c in ([p] if p.is_absolute() else [Path(r) / p for r in roots]):
        try:
            c = c.resolve()
        except (OSError, ValueError):
            continue
        if c.is_file():
            return c
    return None


def _same_source(marker_src, src_path, ref, roots):
    """Does a PASTE marker's `src` name the file a copy instruction names?"""
    p = _paste_src_path(marker_src, roots)
    if p is not None and src_path is not None:
        return os.path.normcase(str(p)) == os.path.normcase(str(src_path))
    a = (marker_src or "").replace("\\", "/").strip("/").lower()
    b = (copy_guard.local_source(ref) if copy_guard else ref or "")
    b = b.replace("\\", "/").strip("/").lower()
    return bool(a and b) and (a == b or a.endswith("/" + b) or b.endswith("/" + a))


def _text_is_here(ref, raw_rows, roots):
    """True when the entry already holds, in a fenced block, the text `ref` names:
      a. a block pasted from that same file (its PASTE marker names it), or
      b. when the file is on this computer, a block whose text equals the file's, or
         404'd), any filled block without a marker: the session put the text in by hand.
    A file others wrote (`_outsider_text`) is never opened to compare: its text never belongs
    in the entry."""
    src_path = _local_text_file(ref, roots)
    blocks = _fenced_texts(raw_rows)
    for o, _c, _t, marked in blocks:
        if marked and _same_source(parse_paste_marker(raw_rows[o - 1])["src"], src_path,
                                   ref, roots):
            return True
    if src_path is not None:
        outside = _outsider_text(src_path)
        if outside is not None and outside[0] == "untrusted":
            return False
        got, _why = _paste_read(src_path)
        if got is None:
            return False
        want = _norm_text(got)
        return any(_norm_text(t) == want for _o, _c, t, _m in blocks)
    return any(t.strip() and not m for _o, _c, t, m in blocks)


def copy_findings(doc_name, entries, lines, force_open=(), roots=None, only=None, fz=None,
                  live=None):
    """E-COPYBYLINK: an open entry tells the reader to copy, paste, run or type text kept in a file
    or at a URL, and the entry does not hold the text. The remedy is the text itself, in a
    fenced block in the entry: the paste command when paste takes the file (`paste_fix`),
    else what to do instead."""
    if copy_guard is None:
        return []
    roots = roots if roots is not None else _picture_roots()
    fz = fenced_lines(lines) if fz is None else fz
    if live is None or len(live) != len(lines):
        live = without_attestations("\n".join(lines)).split("\n")
        if len(live) != len(lines):
            live = list(lines)
    starts = sorted(x["line"] - 1 for x in entries)
    slug = _doc_slug(doc_name)
    out = []
    for e in entries:
        if only is not None and e["id"] not in only:
            continue
        if not _guard_open(e, force_open):
            continue
        acts = _human_acts(e)
        a, b = _own_span(e, starts, len(lines))
        rows, _notes = _guard_lines(lines, live, fz, a, b)
        seen = set()
        for h in copy_guard.copy_hits(rows):
            # A READY hit's "verb" is the phrase as written: "ready to paste", "Paste-ready".
            ready = "ready" in h["verb"].lower()
            if h["exempt"] or not (acts or ready) or (h["line"], h["ref"]) in seen:
                continue
            seen.add((h["line"], h["ref"]))
            if _text_is_here(h["ref"], lines[a:b], roots):
                continue
            local = _local_text_file(h["ref"], roots)
            fix = (paste_fix(slug, e["id"], local, roots) if local is not None
                   else paste_save_fix(slug, e["id"]))
            verb = h["verb"].lower()
            what = ("says the text kept at %s is %s" % (h["ref"], verb) if ready
                    else "tells the human to %s text kept at %s" % (verb, h["ref"]))
            out.append(Finding("E-COPYBYLINK", a + 1 + h["line"],
                               "%s %s, and the text is not in the entry" % (e["id"], what),
                               fix))
    return out


IMAGE_DIR = "orchdoc-images"                  # = the Reader's PASTE_DIR
IMAGE_MAX_BYTES = 8 * 1024 * 1024             # = the Reader's PASTE_MAX_BYTES
IMAGE_MAGIC = ((b"\x89PNG\r\n\x1a\n", ".png"), (b"\xff\xd8\xff", ".jpg"),
               (b"GIF87a", ".gif"), (b"GIF89a", ".gif"))   # = the Reader's PASTE_MAGIC
# The Reader's `_IMG_RE`, which it matches on escaped text, written for the raw text: its
# title `&quot;[^&]*&quot;` is a quoted title holding no character that escaping changes.
_EMBED_SRC_RE = re.compile(r'!\[([^\]\n]*)\]\(([^)\s]+)(?:\s+"[^"&<>\']*")?\)')
_NOTE_IMAGE_RE = re.compile(r"\[image:\s*([^\]\s][^\]]*?)\s*\]")    # = IMAGE_MARK_RE
_READER_CODE_RE = re.compile(r"(`+)(.+?)\1", re.S)                     # = the Reader's _CODE_RE
_BLOCK_START_RE = re.compile(r"^\s*(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|\||>)")
_ELIDED_RE = re.compile(r"(?:^|[\\/])\.{3,}(?=[\\/])")
# `_PIC_PATH_RE` (W-PICTURENOTSHOWN's) with one change: a path that ENDS A SENTENCE counts.
# Its `(?![\w.])` refuses `see C:\x\shot.png.` because of the full stop, which is how a path
# is most often written; this refuses only a longer name such as `shot.png.bak`.
_PIC_MENTION_RE = re.compile(
    r"(?:[A-Za-z]:[\\/])?[\w.@~+%-]+(?:[\\/][\w.@~+% -]*?[\w.@~+%-])*\.(?:png|jpe?g|gif|webp)"
    r"(?!\w|\.\w)", re.I)
_DIGEST_CACHE = {}
IMAGE_NAME_REFUSES = False


def image_ext(raw):
    """The extension the BYTES say a picture is, or None. The Reader's `paste_ext`."""
    for magic, ext in IMAGE_MAGIC:
        if raw.startswith(magic):
            return ext
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return ".webp"
    return None


def reader_shows(raw, roots=None):
    """(Path, None) when the Reader would show `![alt](raw)` as a picture, else (None, why).
    The Reader's `resolve_asset`, tried against each root: a picture saved in a worktree has
    the same relative path in the shared checkout the Reader serves."""
    u = (raw or "").strip().replace("\\", "/")
    if not u:
        return None, "empty path"
    if u.startswith(("/", "//", "#")) or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", u):
        return None, "only a path relative to the workspace is allowed, not %r" % u
    ext = os.path.splitext(u)[1].lower()
    if ext not in PICTURE_TYPES:
        return None, "%s is not one of %s" % (ext or "no extension",
                                              ", ".join(sorted(PICTURE_TYPES)))
    why = "no file at that path"
    for r in (roots if roots is not None else _picture_roots()):
        r = Path(r).resolve()
        try:
            p = (r / u).resolve()
        except (OSError, ValueError) as ex:
            why = "unreadable path (%s)" % type(ex).__name__
            continue
        if not p.is_relative_to(r):
            why = "resolves outside the workspace"
            continue
        if p.is_file():
            return p, None
    return None, why


def _note_mark_shows(raw, roots=None):
    """The picture the Reader shows for a note's `[image: raw]`, or None: an absolute path
    inside the workspace counts as its relative path, as `_image_marks_html` reads it."""
    raw = (raw or "").replace("\\", "/")
    for r in (roots if roots is not None else _picture_roots()):
        pre = Path(r).resolve().as_posix().rstrip("/") + "/"
        rel = raw[len(pre):] if raw.lower().startswith(pre.lower()) else raw
        p, _why = reader_shows(rel, [r])
        if p is not None:
            return p
    return None


def _file_digest(p):
    """sha256 of a file's bytes, or None. Remembered per (path, size, mtime) for one run."""
    try:
        st = Path(p).stat()
    except OSError:
        return None
    if st.st_size > 4 * IMAGE_MAX_BYTES:
        return None
    key = (str(p), st.st_size, st.st_mtime_ns)
    if key not in _DIGEST_CACHE:
        try:
            _DIGEST_CACHE[key] = _hashlib.sha256(Path(p).read_bytes()).hexdigest()
        except OSError:
            return None
    return _DIGEST_CACHE[key]


def _mask_code_spans(rows):
    """`rows` with each inline code span blanked (lengths kept), paragraph by paragraph as the
    Reader pairs backticks, so an embed written inside a code span is not taken for one."""
    out = list(rows)
    seg = []

    def flush():
        if seg:
            joined = "\n".join(out[i] for i in seg)
            if "`" in joined:
                masked = _READER_CODE_RE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)),
                                             joined)
                for i, part in zip(seg, masked.split("\n")):
                    out[i] = part
            del seg[:]

    for i, r in enumerate(rows):
        if r is None or not r.strip():
            flush()
            continue
        if _BLOCK_START_RE.match(r):
            flush()
        seg.append(i)
    flush()
    return out


_CODE_SPAN_LINE_RE = re.compile(r"(`+)([^`\n]+?)\1")
_PIC_END_RE = re.compile(r"\.(?:png|jpe?g|gif|webp)\s*$", re.I)
_ABS_PATH_RE = re.compile(r"(?<![\w/\\])(?:[A-Za-z]:[\\/]|~[\\/])[^\s`'\"<>|*?()\[\]]*")
_LINK_TARGET_RE = re.compile(r"\]\(([^)\s]+)\)")


def _entry_folders(rows, roots):
    """The folders an entry names that exist here, and the folders of the files it names
    that exist here: where a partial path or a bare picture name in it is looked up, so
    `out\\p1.png` written under a named folder resolves to the picture it means."""
    cands = []
    for r in rows:
        if not r:
            continue
        cands += [m.group(2) for m in _CODE_SPAN_LINE_RE.finditer(r)]
        cands += [m.group(1) for m in _LINK_TARGET_RE.finditer(r)]
        cands += [m.group(0) for m in _ABS_PATH_RE.finditer(r)]
    out = []
    for c in cands:
        c = c.strip().rstrip(".,;:")
        if not c or len(c) > 400 or re.match(r"(?i)^[a-z][a-z0-9+.-]*://", c):
            continue
        p = Path(os.path.expanduser(c.replace("\\", "/")))
        for q in ([p] if p.is_absolute() else [Path(r) / p for r in roots]):
            try:
                q = q.resolve()
                d = q if q.is_dir() else (q.parent if q.is_file() else None)
            except (OSError, ValueError):
                continue
            if d is not None:
                if d not in out:
                    out.append(d)
                break
    return out


def _same_file_bytes(a, b):
    da = _file_digest(a)
    return da is not None and da == _file_digest(b)


def _resolve_mention(raw, roots, bases):
    """The picture file a mention names: as written (absolute, or relative to a root), else
    relative to a folder the entry names, or its parent, else by name inside one. None when
    none exists here."""
    p = _local_file(raw, roots)
    if p is not None:
        return p
    s = raw.replace("\\", "/").strip()
    gb = re.match(r"^/([A-Za-z])/(.+)$", s)          # Git Bash's /c/Users/... form
    if gb:
        return _local_file("%s:/%s" % (gb.group(1), gb.group(2)), roots)
    rp = Path(s)
    if not s or rp.is_absolute() or s.startswith("~"):
        return None
    for b in bases:
        for c in (b / rp, b.parent / rp, b / rp.name):
            try:
                c = c.resolve()
            except (OSError, ValueError):
                continue
            if c.is_file() and c.suffix.lower() in PICTURE_TYPES:
                return c
    return None


def _image_mentions(rows, notes, roots):
    """Each picture named in `rows` (from `_guard_lines`), as [(row, kind, raw, Path, why)]:
      embed-ok    an embed the Reader shows
      embed-bad   an embed the Reader cannot show (`why` says why)
      note-ok     an agent's note `[image: ...]` the Reader shows
      path        a path or a name, as text or inline code, of a picture on this computer
                  that the entry does not show (the same bytes, or the same file name,
                  shown elsewhere in the entry count as shown)
      elided      a path shortened with ... (or the ellipsis character)
      gone        a full path to a picture that is not on this computer
      name        a file name or partial path that names no picture here"""
    masked = _mask_code_spans(rows)
    found, shown, plain = [], set(), {}
    for i, r in enumerate(rows):
        if r is None or not r.strip():
            continue
        cut = list(r)
        for m in _EMBED_SRC_RE.finditer(masked[i]):
            p, why = reader_shows(m.group(2), roots)
            if p is not None:
                shown.add(p)
                found.append((i, "embed-ok", m.group(2), p, None))
            else:
                found.append((i, "embed-bad", m.group(2), None, why))
            cut[m.start():m.end()] = " " * (m.end() - m.start())
        if i in notes:
            for m in _NOTE_IMAGE_RE.finditer(masked[i]):
                p = _note_mark_shows(m.group(1), roots)
                if p is not None:
                    shown.add(p)
                    found.append((i, "note-ok", m.group(1), p, None))
                    cut[m.start():m.end()] = " " * (m.end() - m.start())
        plain[i] = "".join(cut)
    digests = {d for d in (_file_digest(p) for p in shown) if d}
    names = {p.name.lower() for p in shown}
    bases = _entry_folders([plain[i] for i in sorted(plain)], roots)

    # Each mention as (row, [readings to try, best first], the text before it). A code span
    # that ends in a picture's extension is read whole first, so a name with spaces in it
    # (`1 - the home screen.png`) is one name; then each path inside it.
    mentions = []
    for i in sorted(plain):
        text = plain[i]
        spans = []
        for m in _CODE_SPAN_LINE_RE.finditer(text):
            inner = m.group(2).strip()
            if _PIC_END_RE.search(inner):
                tries = [inner] + [x.group(0) for x in
                                   _PIC_MENTION_RE.finditer(_without_urls(inner))
                                   if x.group(0) != inner]
                mentions.append((i, tries, text[:m.start()]))
                spans.append((m.start(), m.end()))
        rest = list(text)
        for a, b in spans:
            rest[a:b] = " " * (b - a)
        rest = _without_urls("".join(rest))
        for m in _PIC_MENTION_RE.finditer(rest):
            mentions.append((i, [m.group(0)], rest[:m.start()]))

    seen = set()
    for i, tries, before in mentions:
        p, raw = None, tries[-1] if len(tries) > 1 and " " in tries[0] else tries[0]
        for t in tries:
            p = _resolve_mention(t, roots, bases)
            if p is not None:
                raw = t
                break
        if p is not None:
            if p in shown or p in seen or _file_digest(p) in digests:
                continue
            seen.add(p)
            found.append((i, "path", raw, p, None))
            continue
        key = raw.replace("\\", "/").lower()
        if key in seen or Path(key).name in names:
            continue
        seen.add(key)
        if _ELIDED_RE.search(raw) or "\u2026" in raw or \
                before.rstrip("/\\").endswith("\u2026"):
            kind = "elided"
        elif re.match(r"^(?:[A-Za-z]:[\\/]|~[\\/]|/[A-Za-z]/)", raw):
            kind = "gone"      # a full path; one starting with a bare / is a web route
        else:
            kind = "name"
        found.append((i, kind, raw, None, None))
    return found


def _main_root(roots=None):
    """The main checkout, which the Reader serves: the last of `roots`, as `_picture_roots`
    orders them (this tree first, the shared checkout last)."""
    rs = list(roots) if roots else _picture_roots()
    return Path(rs[-1]).resolve()


def _worktree_top(p, main):
    """Read from the `.git` FILE a worktree has, which names a folder under the main checkout's
    .git/worktrees/, so it costs no git call."""
    common = (main / ".git").resolve()
    d = p.parent
    while d != main and main in d.parents:
        dot_git = d / ".git"
        try:
            if dot_git.is_dir():
                return None
            if dot_git.is_file():
                m = re.match(r"\s*gitdir:\s*(.+?)\s*$",
                             dot_git.read_text(encoding="utf-8", errors="replace"), re.M)
                if m:
                    gd = Path(m.group(1))
                    gd = (gd if gd.is_absolute() else d / gd).resolve()
                    if gd.parent.name == "worktrees" and gd.parent.parent == common:
                        return d
                return None
        except (OSError, ValueError):
            return None
        d = d.parent
    return None


def picture_home(p, roots=None):
    """Where picture file `p` is, for `image` and for the fix E-IMAGEBYPATH prints:
      ("main", main, rel)   inside the main checkout, which the Reader serves: `rel` is the
                            path the Reader shows it by
      ("tree", top, rel)    inside another working tree of this repository: the calling
                            worktree, or one under the main checkout
      None                  outside them, as a session's scratchpad in %TEMP% is"""
    try:
        p = Path(p).resolve()
    except (OSError, ValueError):
        return None
    rs = []
    for r in (roots if roots else _picture_roots()):
        try:
            rs.append(Path(r).resolve())
        except (OSError, ValueError):
            continue
    if not rs:
        return None
    main = rs[-1]
    if p.is_relative_to(main):
        top = _worktree_top(p, main)
        if top is None:
            return "main", main, p.relative_to(main).as_posix()
        return "tree", top, p.relative_to(top).as_posix()
    for r in rs[:-1]:
        if p.is_relative_to(r):
            return "tree", r, p.relative_to(r).as_posix()
    return None


def _git_ignores(top, rel):
    """True when git, in the working tree `top`, ignores `rel` (a path it does not track that
    a .gitignore rule matches), False when it does not, None when git cannot say.
    orchdoc-images/ counts as not ignored: the workspace ignores it only so that a stray
    `git add` does not take the Reader's uploads, and commit lands the pictures a doc embeds
    from its own folder there (`_images_to_land`)."""
    if rel == IMAGE_DIR or rel.startswith(IMAGE_DIR + "/"):
        return False
    rc, _o, _e = git(["check-ignore", "-q", "--", rel], cwd=str(top))
    return True if rc == 0 else (False if rc == 1 else None)


def _embed_holds(rel):
    """True when `![alt](rel)` can hold this path. A space or a bracket ends the embed early,
    and the Reader does not decode %20 (`resolve_asset` unescapes HTML only)."""
    return not re.search(r"[\s()\[\]]", rel or "")


def _image_cmd(slug, eid, path):
    return '%s image --doc %s %s --file "%s"' % (tool_command(), slug, eid, path)


def _image_source(src, whole=True):
    """(the picture's bytes, the extension its bytes say, None), or (None, None, why `image`
    refuses `src`). The one test both `image` and the fixes printed for it apply. With
    whole=False only the first bytes are read, which is enough to say whether it refuses."""
    src = Path(src)
    ext = src.suffix.lower()
    if ext == ".svg":
        return None, None, ("%s is an SVG. The Reader shows raster pictures only, because an SVG "
                            "can carry script. Export it as a PNG and run this again." % src.name)
    if ext not in PICTURE_TYPES:
        return None, None, ("%s is not one of %s, the pictures the Reader shows."
                            % (src.name, ", ".join(sorted(PICTURE_TYPES))))
    try:
        size = src.stat().st_size
        raw = b""
        if size <= IMAGE_MAX_BYTES:
            with open(src, "rb") as fh:
                raw = fh.read() if whole else fh.read(16)
    except OSError as ex:
        return None, None, "cannot read %s (%s)" % (src, type(ex).__name__)
    if size > IMAGE_MAX_BYTES:
        return None, None, ("%s is %d bytes; the Reader's limit for a picture is %d MB."
                            % (src.name, size, IMAGE_MAX_BYTES // (1024 * 1024)))
    if not raw:
        return None, None, "%s is empty." % src.name
    real_ext = image_ext(raw)
    if real_ext is None:
        return None, None, ("the bytes of %s are not a PNG, JPEG, GIF or WebP picture, whatever "
                            "its name says." % src.name)
    return raw, real_ext, None


def _command_or_why(slug, eid, p):
    """The image command for picture `p`, or why `image` would refuse it."""
    _raw, _ext, why = _image_source(p, whole=False)
    return _image_cmd(slug, eid, p.as_posix()) if why is None else "image refuses it: " + why


def _clean_rel(rel):
    """`rel` with each run of spaces and brackets in its file name made one hyphen, or None
    when its folders hold one too: a name an embed can hold, for a copy beside the file."""
    d, _sep, name = rel.rpartition("/")
    if not _embed_holds(d):
        return None
    stem, dot, ext = name.rpartition(".")
    clean = re.sub(r"[\s()\[\]]+", "-", stem if dot else name).strip("-") or "picture"
    return (d + "/" if d else "") + clean + ("." + ext if dot else "")


def _ignored_words(ign):
    return "git ignores" if ign else "git could not say whether it ignores"


def picture_fix(slug, eid, p, roots=None, alt=None, instead_of="its path"):
    """The fix for picture file `p`, named in entry `eid` of doc `slug` without being shown:
      - inside the main checkout, tracked, ignored or neither: the embed of it where it is,
        which the Reader shows from disk, so nothing new lands;
      - inside another working tree of this repository: the same when the main checkout holds
        the same picture at that path; else the image command, or, for a picture git ignores
        there, where to save it in the main checkout and the embed of it there;
      - outside them: the image command, which copies it into orchdoc-images/<doc>/.
    A path an embed cannot hold (a space or a bracket) gets the image command when git does
    not ignore the picture, and a copy beside it under a name an embed holds when git does.
    The image command is printed only for a file `image` accepts; for any other, why not.
    `instead_of` says what in the entry the embed replaces (None: nothing, it is added)."""
    p = Path(p).resolve()
    ext = p.suffix.lower()
    if ext == ".svg":
        return ("%s is an SVG, which the Reader does not show because an SVG can carry script. "
                "Export it as a PNG and show that instead" % p.name)
    if ext not in PICTURE_TYPES:
        return ("%s is not one of %s, the pictures the Reader shows. Save it as a PNG and show "
                "that instead" % (p.name, ", ".join(sorted(PICTURE_TYPES))))
    rs = list(roots) if roots else _picture_roots()
    main = _main_root(rs)
    a = _image_alt(alt, p.name)
    put = "put ![%s](%%s) in %s%s" % (a.replace("%", "%%"), eid,
                                     (" in place of " + instead_of) if instead_of else "")
    home = picture_home(p, rs)
    if home is None:
        return _command_or_why(slug, eid, p)
    kind, top, rel = home
    if kind == "tree":
        twin = main / rel
        if twin.is_file() and _same_file_bytes(twin, p):
            kind, top = "main", main
    if kind == "main":
        if _embed_holds(rel):
            return "the Reader shows it where it is: " + put % rel
        ign = _git_ignores(main, rel)
        if ign is False:
            return _command_or_why(slug, eid, p)
        clean = _clean_rel(rel)
        if clean and not (main / clean).exists() and _git_ignores(main, clean):
            return ("%s %s, so it is not copied into %s/, and an embed cannot hold its name (a "
                    "space or a bracket): save a copy of it as %s (git ignores that name too), "
                    "then %s" % (_ignored_words(ign), rel, IMAGE_DIR, (main / clean).as_posix(),
                                 put % clean))
        return ("%s %s, so it is not copied into %s/, and an embed cannot hold its name (a space "
                "or a bracket): save a copy of it beside it under a name with no space or bracket "
                "that git ignores, then %s" % (_ignored_words(ign), rel, IMAGE_DIR,
                                               put % "<the copy's path in the workspace>"))
    ign = _git_ignores(top, rel)
    if ign is False:
        return _command_or_why(slug, eid, p)
    dest = rel if _embed_holds(rel) else _clean_rel(rel)
    if dest and not (main / dest).exists() and _git_ignores(main, dest):
        return ("%s %s, so it is not copied into %s/, and it is only in %s, which the Reader "
                "does not serve: save it in the main checkout as %s (git ignores that path "
                "there too), then %s" % (_ignored_words(ign), rel, IMAGE_DIR, top.as_posix(),
                                         (main / dest).as_posix(), put % dest))
    return ("%s %s, so it is not copied into %s/, and it is only in %s, which the Reader does "
            "not serve: save it in the main checkout %s, under a name with no space or bracket "
            "that git ignores there, then %s"
            % (_ignored_words(ign), rel, IMAGE_DIR, top.as_posix(), main.as_posix(),
               put % "<its path in the workspace>"))


def _unknown_picture_fix(slug, eid, lead, instead_of="its path"):
    """The fix for a picture this computer cannot find: what `picture_fix` prints, once the
    session has found the file."""
    return ("%s, then: if it is in the workspace, put ![<what it shows>](<its path in the "
            "workspace>) in %s in place of %s; if it is outside, %s"
            % (lead, eid, instead_of, _image_cmd(slug, eid, "<its full path>")))


def image_findings(doc_name, entries, lines, force_open=(), roots=None, only=None, fz=None,
                   live=None, reported=None):
    """E-IMAGEBYPATH: an open entry names a picture by its path (as text or inline code) instead of
    showing it, shortens the path so nobody can find it, or embeds one the Reader cannot
    show. A file name that names no file here counts only in an entry the human acts on. The
    fix (`picture_fix`) is the embed of the picture where it is when it is in the main
    checkout, and the image command, which copies it into orchdoc-images/<doc>/, only when
    it is outside it. `reported`, a dict, gets {entry heading line: {normcased path of each
    picture file a finding names}}, so check_doc's W-PICTURENOTSHOWN does not name the same
    picture again."""
    roots = roots if roots is not None else _picture_roots()
    fz = fenced_lines(lines) if fz is None else fz
    if live is None or len(live) != len(lines):
        live = without_attestations("\n".join(lines)).split("\n")
        if len(live) != len(lines):
            live = list(lines)
    starts = sorted(x["line"] - 1 for x in entries)
    slug = _doc_slug(doc_name)
    out = []
    for e in entries:
        if only is not None and e["id"] not in only:
            continue
        if not _guard_open(e, force_open):
            continue
        a, b = _own_span(e, starts, len(lines))
        rows, notes = _guard_lines(lines, live, fz, a, b)
        for off, kind, raw, p, why in _image_mentions(rows, notes, roots):
            line = a + 1 + off
            if reported is not None and kind in ("embed-bad", "path"):
                f = p if kind == "path" else _local_file(raw, roots)
                if f is not None:
                    reported.setdefault(e["line"], set()).add(os.path.normcase(str(f)))
            if kind == "embed-bad":
                local = _local_file(raw, roots)
                if local is not None:
                    alt = next((m.group(1) for m in _EMBED_SRC_RE.finditer(rows[off] or "")
                                if m.group(2) == raw), None)
                    fix = picture_fix(slug, e["id"], local, roots, alt, "that embed")
                else:
                    fix = _unknown_picture_fix(slug, e["id"], "find the picture", "that embed")
                out.append(Finding("E-IMAGEBYPATH", line,
                                   "%s embeds %s, which the Reader cannot show: %s"
                                   % (e["id"], raw, why), fix))
            elif kind == "path":
                out.append(Finding("E-IMAGEBYPATH", line,
                                   "%s names the picture %s by its path instead of showing it"
                                   % (e["id"], raw), picture_fix(slug, e["id"], p, roots)))
            elif kind == "elided":
                out.append(Finding("E-IMAGEBYPATH", line,
                                   "%s names a picture by a shortened path, %s, instead of "
                                   "showing it" % (e["id"], raw),
                                   _unknown_picture_fix(slug, e["id"], "find the file")))
            elif kind == "gone" and _human_acts(e):
                out.append(Finding("E-IMAGEBYPATH", line,
                                   "%s names the picture %s, which is not on this computer, "
                                   "instead of showing it" % (e["id"], raw),
                                   _unknown_picture_fix(slug, e["id"], "find or remake it")))
            elif kind == "name" and IMAGE_NAME_REFUSES and _human_acts(e):
                out.append(Finding("E-IMAGEBYPATH", line,
                                   "%s names the picture %s without showing it, and no file by "
                                   "that name is here" % (e["id"], raw),
                                   _unknown_picture_fix(slug, e["id"], "find the file")))
    return out


# --- W-PASTEDRIFT ---------------------------------------------------------------------
def pastedrift_findings(doc_name, entries, lines, roots=None, fz=None):
    """W-PASTEDRIFT: a pasted block in an open entry no longer matches its marker. Its text was
    edited in the doc, or its source file changed after the paste, so the copy the human would take
    is not the file's text. A source that no longer exists is silent: the entry's copy is then
    the only one. The remedy pastes the file again, which replaces the block, when paste takes
    the file (`paste_fix`); for a source no longer on this computer it says what to do instead
    of a paste that would be refused."""
    roots = roots if roots is not None else _picture_roots()
    fz = fenced_lines(lines) if fz is None else fz
    starts = sorted(x["line"] - 1 for x in entries)
    slug = _doc_slug(doc_name)
    blocks = pasted_blocks(lines, fz)
    out = []
    if not blocks:
        return out
    for e in entries:
        if not _guard_open(e):
            continue
        a, b = _own_span(e, starts, len(lines))
        for blk in blocks:
            if not a <= blk["marker"] < b:
                continue
            src = _paste_src_path(blk["src"], roots)
            if blk["open"] is None:
                why = "a paste provenance line has no pasted block under it"
            elif blk["close"] is None:
                why = "a pasted block is never closed"
            elif paste_digest(blk["text"]) != blk["sha"]:
                why = "the text pasted from %s was edited in the doc after the paste" % blk["src"]
            else:
                if src is None:
                    continue
                outside = _outsider_text(src)
                if outside is not None and outside[0] == "untrusted":
                    continue                     # never opened: text others wrote
                got, why_src = _paste_read(src)
                if got is None:
                    why = "%s can no longer be pasted whole: %s" % (blk["src"], why_src)
                elif paste_digest(got) != blk["sha"]:
                    why = "%s has changed since it was pasted" % blk["src"]
                else:
                    continue
            if src is not None:
                fix = paste_fix(slug, e["id"], src, roots, replacing=True)
                if blk["open"] is None:
                    fix = ("Delete the paste provenance line at line %d, which has no block under "
                           "it, then: %s" % (blk["marker"] + 1, fix))
                elif blk["close"] is None:
                    fix = ("Delete the paste provenance line at line %d and the unclosed block "
                           "under it (the fence at line %d and every line after it that was "
                           "pasted text), then: %s" % (blk["marker"] + 1, blk["open"] + 1, fix))
            else:
                fix = ("%s is not on this computer, so paste cannot put it back. If the change "
                       "to the block was meant, delete the paste provenance line above it and "
                       "the block stays as %s's own text; if not, put the pasted text back as "
                       "it was" % (blk["src"], e["id"]))
            out.append(Finding("W-PASTEDRIFT", blk["marker"] + 1, "%s: %s" % (e["id"], why), fix))
    return out


def copy_guard_findings(path, entries, lines, reported=None):
    """E-COPYBYLINK, E-IMAGEBYPATH and W-PASTEDRIFT for one doc, for `check_doc`. Each fails
    open on its own: one that cannot run says so on stderr and the rest of the check stands.
    `reported`: see `image_findings`."""
    out = []
    name = Path(str(path)).name
    fz = fenced_lines(lines)
    live = without_attestations("\n".join(lines)).split("\n")
    if len(live) != len(lines):
        live = list(lines)
    for code, run in (
            ("E-COPYBYLINK", lambda: copy_findings(name, entries, lines, fz=fz, live=live)),
            ("E-IMAGEBYPATH", lambda: image_findings(name, entries, lines, fz=fz, live=live,
                                                     reported=reported)),
            ("W-PASTEDRIFT", lambda: pastedrift_findings(name, entries, lines, fz=fz))):
        try:
            out.extend(run())
        except Exception as ex:
            sys.stderr.write("[orchdoc] %s could not run on %s: %s\n"
                             % (code, name, str(ex)[:160]))
            if code == "E-IMAGEBYPATH" and reported is not None:
                reported.clear()     # it reported nothing, so W-PICTURENOTSHOWN skips nothing
    return out


# --- the same tests at write time ------------------------------------------------------
def _changed_ids(old_entries, old_lines, new_entries, new_lines):
    """Ids whose own rows differ between two versions of a doc, and ids new in the second."""
    def spans(entries, lines):
        starts = sorted(x["line"] - 1 for x in entries)
        got = {}
        for e in entries:
            a, b = _own_span(e, starts, len(lines))
            got.setdefault(e["id"], []).append("\n".join(lines[a:b]))
        return got
    old, new = spans(old_entries, old_lines), spans(new_entries, new_lines)
    return {i for i, t in new.items() if old.get(i) != t}


def text_guard_findings(doc_name, old_text, new_text, force_open=(), roots=None):
    """The E-COPYBYLINK and E-IMAGEBYPATH findings an edit ADDS: those in the new text of the
    entries it changes, less the ones the same entries already had. For the PreToolUse hook
    and for add, note and resolve, so a write is refused when it is made, not at landing."""
    from collections import Counter

    def read(text):
        lines = text.splitlines()
        entries, _s = parse_entries(lines)
        fz = _prose_bodies(entries, lines)
        live = without_attestations(text).splitlines()
        if len(live) != len(lines):
            live = list(lines)
        return lines, entries, fz, live

    nl, ne, nfz, nlive = read(new_text or "")
    ol, oe, ofz, olive = read(old_text or "")
    ids = _changed_ids(oe, ol, ne, nl) | set(force_open)
    if not ids:
        return []
    new = (copy_findings(doc_name, ne, nl, force_open, roots, ids, nfz, nlive)
           + image_findings(doc_name, ne, nl, force_open, roots, ids, nfz, nlive))
    if not new:
        return []
    old = (copy_findings(doc_name, oe, ol, force_open, roots, ids, ofz, olive)
           + image_findings(doc_name, oe, ol, force_open, roots, ids, ofz, olive))
    left = Counter((f.code, f.msg) for f in old)
    out = []
    for f in new:
        k = (f.code, f.msg)
        if left[k] > 0:
            left[k] -= 1
            continue
        out.append(f)
    return out


def guard_refusal_lines(findings, verb):
    """The refusal for a write that would add E-COPYBYLINK or E-IMAGEBYPATH findings, with
    the command to run instead of each."""
    out = ["%s refused: it would put %d thing%s in front of the human that they have to go and find:"
           % (verb, len(findings), "" if len(findings) == 1 else "s")]
    for f in findings:
        out.append("  %s line %d: %s" % (f.code, f.line, f.msg))
        out.append("    instead: %s" % f.detail)
    out.append("  Text the human copies goes into the entry as a fenced block (the paste command);")
    out.append("  a picture goes in embedded: where it is when it is in the workspace, by the")
    out.append("  image command when it is outside.")
    return out


def _write_guard_refuses(doc, old_text, new_text, verb, force_open=(), fail=None):
    """True, with the refusal printed, when a write by add, note or resolve would add an
    E-COPYBYLINK or E-IMAGEBYPATH finding. Fails open: a guard that crashes says so and lets
    the write through, because `check` and gate 0 still read the result."""
    try:
        found = text_guard_findings(Path(doc).name, old_text, new_text, force_open)
    except Exception as ex:
        sys.stderr.write("[orchdoc] the copy and picture guard could not run (%s); the write "
                         "goes ahead and check still reads it\n" % str(ex)[:160])
        return False
    if not found:
        return False
    msg = "\n".join(guard_refusal_lines(found, verb))
    if fail is not None:
        fail(msg)
    else:
        print("[REFUSE] " + msg, file=sys.stderr)
    return True


def print_copy_guard_remedies(findings):
    """Each E-COPYBYLINK and E-IMAGEBYPATH remedy in full, once. The detail line is cut at
    100 characters, and a command with a path in it does not fit."""
    mine = []
    for f in findings:
        if f.code in ("E-COPYBYLINK", "E-IMAGEBYPATH") and f.detail not in mine:
            mine.append(f.detail)      # one command per fix, however many lines it clears
    if not mine:
        return
    print("          note: E-COPYBYLINK / E-IMAGEBYPATH - the human copies text and looks at")
    print("                pictures IN the entry . The fix for each:")
    for d in mine[:20]:
        print("                  %s" % d)
    if len(mine) > 20:
        print("                  ... and %d more fixes" % (len(mine) - 20))


# --- the paste command -----------------------------------------------------------------
def _resolve_input_file(raw):
    """The existing file `raw` names: absolute as written, else from the current folder, else
    from the workspace. None when there is none."""
    s = os.path.expanduser((raw or "").strip().strip('"'))
    if not s:
        return None
    p = Path(s)
    cands = [p] if p.is_absolute() else [Path.cwd() / p] + [Path(r) / p
                                                            for r in _picture_roots()]
    for c in cands:
        try:
            c = c.resolve()
        except (OSError, ValueError):
            continue
        if c.is_file():
            return c
    return None


def _verify_entry_write(before, after, eid, block_ok):
    """Why a write from `before` to `after` would change more than one block in entry `eid`,
    or None: the same entries in the same order, nothing newly hidden, the same status, owner
    and notes, and `block_ok(lines, entries, entry)` true for the new block."""
    bl = before.splitlines()
    al = after.splitlines()
    ob, _s = parse_entries(bl)
    nb, _s = parse_entries(al)
    if [(x["id"], bool(x.get("archived"))) for x in ob] != \
            [(x["id"], bool(x.get("archived"))) for x in nb]:
        return "the doc's entries would change"
    if len(unreachable_archived(al, nb)) != len(unreachable_archived(bl, ob)):
        return "an archived heading would become unreachable"
    o = [x for x in ob if x["id"] == eid and not x.get("archived")]
    n = [x for x in nb if x["id"] == eid and not x.get("archived")]
    if len(o) != 1 or len(n) != 1:
        return "%s is not one live entry" % eid
    if status_of(o[0]["body"]) != status_of(n[0]["body"]) or \
            owner_text(o[0]["body"]) != owner_text(n[0]["body"]):
        return "%s's status or owner would read differently" % eid

    def key(text):
        return [(x["nid"], x["who"], x["kind"], x["text"], x["ack"]) for x in parse_notes(text)]
    if key(before) != key(after):
        return "the notes would change"
    if not block_ok(al, nb, n[0]):
        return "the new block would not read back as written"
    return None


def cmd_paste(args):
    """Put a file's text into an entry, word for word, as a fenced block under a provenance
    line (`paste_marker`). Pasting the same file again replaces its block; a block already
    holding the text is left alone. Refuses rather than shortens."""
    doc = resolve_doc_arg(args.doc)
    if not doc or not doc.exists():
        print("[REFUSE] no such doc: %s" % args.doc, file=sys.stderr)
        return 2
    if refuse_if_not_mine(doc, "paste", getattr(args, "not_mine", False)):
        return 1
    src = _resolve_input_file(args.file)
    if src is None:
        print("[REFUSE] no file at %s" % args.file, file=sys.stderr)
        return 2
    roots = _picture_roots()
    text, refusal = _paste_check(src)
    if refusal is not None and refusal["code"] == "lines":
        hazards = refusal["hazards"]
        print("[REFUSE] %s has %d line%s that the doc's tools would act on inside the block:"
              % (src.as_posix(), len(hazards), "" if len(hazards) == 1 else "s"),
              file=sys.stderr)
        for h in hazards[:8]:
            print("  %s" % h, file=sys.stderr)
        if len(hazards) > 8:
            print("  ... and %d more" % (len(hazards) - 8), file=sys.stderr)
        if refusal["fences"]:
            name = _paste_src(src, roots)
            print("  %s. Nothing was written." % _paste_refusal_fix(
                refusal, name, args.id, paste_cmd(_doc_slug(doc.name), args.id, name)),
                file=sys.stderr)
        else:
            print("  Change those lines in the source file, then paste it again. Nothing was "
                  "written.", file=sys.stderr)
        return 2
    if refusal is not None:
        print("[REFUSE] paste refuses %s: %s. Nothing was written."
              % (src.as_posix(), refusal["why"]), file=sys.stderr)
        return 2

    sha = paste_digest(text)
    src_name = _paste_src(src, roots)
    block = [paste_marker(src_name, _paste_commit(src), sha), PASTE_FENCE_OPEN] \
        + text.split("\n") + [PASTE_FENCE_CLOSE]
    with _lock(doc):
        before = doc.read_text(encoding="utf-8")
        lines = before.splitlines()
        entries, _s = parse_entries(lines)
        match = [e for e in entries if e["id"] == args.id and not e.get("archived")]
        if len(match) != 1:
            print("[REFUSE] %s %s in %s" % (args.id, "is not a live entry" if not match
                                            else "appears %d times" % len(match), doc.name),
                  file=sys.stderr)
            return 1
        e = match[0]
        starts = sorted(x["line"] - 1 for x in entries)
        a, b = _own_span(e, starts, len(lines))
        fz = fenced_lines(lines)
        mine = [blk for blk in pasted_blocks(lines, fz)
                if a <= blk["marker"] < b and blk["close"] is not None]
        if any(blk["sha"] == sha and paste_digest(blk["text"]) == sha for blk in mine):
            print("[UNCHANGED] %s already holds this text, pasted from %s."
                  % (args.id, src_name))
            return 0

        def same_file(blk):
            p = _paste_src_path(blk["src"], roots)
            return (p is not None and os.path.normcase(str(p)) == os.path.normcase(str(src))) \
                or blk["src"] == src_name
        old = [blk for blk in mine if same_file(blk)]
        bare = [(o, c) for o, c, t, m in _fenced_texts(lines[a:b])
                if not m and _norm_text(t) == _norm_text(text)]
        if old:
            lines[old[0]["marker"]:old[0]["close"] + 1] = block
            verb = "REPLACED"
        elif bare:
            # The session put the text in by hand: give that block its marker, so the entry
            # does not carry the same text twice.
            lines[a + bare[0][0]:a + bare[0][1] + 1] = block
            verb = "MARKED"
        else:
            k = _entry_insert_at(lines, e, starts)
            ins = [""] + block
            if k < len(lines) and lines[k].strip():
                ins.append("")
            lines[k:k] = ins
            verb = "PASTED"
        after = "\n".join(lines) + "\n"

        def block_ok(al, nb, n):
            st2 = sorted(x["line"] - 1 for x in nb)
            a2, b2 = _own_span(n, st2, len(al))
            return any(a2 <= blk["marker"] < b2 and blk["close"] is not None
                       and blk["sha"] == sha and paste_digest(blk["text"]) == sha
                       for blk in pasted_blocks(al))
        why = _verify_entry_write(before, after, args.id, block_ok)
        if why:
            print("[REFUSE] paste would not be clean: %s. Nothing was written." % why,
                  file=sys.stderr)
            return 1
        write_doc(doc, after, expect=before)
        if doc.read_text(encoding="utf-8") != after:
            print("[FAILED] %s on disk is not what paste wrote. Read it before doing anything "
                  "else." % doc.name, file=sys.stderr)
            return 1
    n_lines = len(text.split("\n"))
    print("[%s] %s: %d characters, %d line%s from %s, in a fenced block with its source"
          % (verb, args.id, len(text), n_lines, "" if n_lines == 1 else "s", src_name))
    link = reader_link(doc.name, args.id)
    if link:
        print("  the human copies it with the block's Copy button at %s once it lands." % link)
    print('  Land it:  python <scripts>/orchdoc.py commit --doc %s --message "..." --commit'
          % _doc_slug(doc.name))
    return 0


# --- the image command -----------------------------------------------------------------
def _image_alt(alt, src):
    """Alt text for an embed: what the caller gave, else the file's name in words; never a
    character that would end the embed early. Never shortened: the Reader's embed pattern
    has no length limit, and the alt says what the picture shows."""
    a = alt if alt else re.sub(r"[-_]+", " ", Path(src).stem)
    a = re.sub(r"[\[\]()`\r\n]+", " ", a or "")
    a = " ".join(a.split())
    return a or "picture"


def cmd_image(args):
    """Copy a picture into orchdoc-images/<doc>/ under a name of its own and embed it in an
    entry, so the Reader shows it where the human is reading. commit lands the picture with the
    doc. Refuses an SVG and anything else the Reader would not show."""
    import secrets as _secrets
    doc = resolve_doc_arg(args.doc)
    if not doc or not doc.exists():
        print("[REFUSE] no such doc: %s" % args.doc, file=sys.stderr)
        return 2
    slug = _doc_slug(doc.name)
    if not re.fullmatch(r"o\d+", slug):
        print("[REFUSE] %s is not an OrchDoc" % doc.name, file=sys.stderr)
        return 2
    if refuse_if_not_mine(doc, "image", getattr(args, "not_mine", False)):
        return 1
    src = _resolve_input_file(args.file)
    if src is None:
        print("[REFUSE] no file at %s" % args.file, file=sys.stderr)
        return 2
    raw, real_ext, why = _image_source(src)
    if why:
        print("[REFUSE] " + why, file=sys.stderr)
        return 2
    roots = _picture_roots()
    home = picture_home(src, roots)
    if home is not None:
        ignored = _git_ignores(home[1], home[2])
        if ignored is not False:
            print("[REFUSE] %s %s, so image does not copy it into %s/%s/, which commit lands "
                  "in git." % (_ignored_words(ignored), home[2], IMAGE_DIR, slug),
                  file=sys.stderr)
            print("  Instead: %s" % picture_fix(slug, args.id, src, roots,
                                                getattr(args, "alt", None), instead_of=None),
                  file=sys.stderr)
            print("Nothing was written.", file=sys.stderr)
            return 2

    root = Path(PROJECTS).resolve()
    folder = root / IMAGE_DIR / slug
    digest = _hashlib.sha256(raw).hexdigest()
    target, created = None, False
    if folder.is_dir():
        for p in sorted(folder.iterdir()):
            if (p.is_file() and p.suffix.lower() in PICTURE_TYPES and p.stat().st_size == len(raw)
                    and _file_digest(p) == digest):
                target = p
                break
    if target is None:
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as ex:
            print("[REFUSE] cannot make %s (%s)" % (folder, type(ex).__name__), file=sys.stderr)
            return 1
        stem = re.sub(r"[^A-Za-z0-9]+", "-", src.stem).strip("-").lower()[:40]
        for _try in range(5):
            cand = folder / ("%s-%s%s%s" % (time.strftime("%Y%m%d-%H%M%S"), _secrets.token_hex(3),
                                            ("-" + stem) if stem else "", real_ext))
            try:
                with open(cand, "xb") as fh:
                    fh.write(raw)
                target, created = cand, True
                break
            except FileExistsError:
                continue
            except OSError as ex:
                print("[REFUSE] cannot write %s (%s)" % (cand, type(ex).__name__),
                      file=sys.stderr)
                return 1
        if target is None:
            print("[REFUSE] could not find a free name in %s" % folder, file=sys.stderr)
            return 1

    def undo():
        if created:
            try:
                target.unlink()
            except OSError:
                pass

    rel = target.resolve().relative_to(root).as_posix()
    shown, why = reader_shows(rel, [root])
    if shown is None:
        undo()
        print("[REFUSE] the Reader could not show %s: %s" % (rel, why), file=sys.stderr)
        return 1
    embed = "![%s](%s)" % (_image_alt(getattr(args, "alt", None), src), rel)
    try:
        with _lock(doc):
            before = doc.read_text(encoding="utf-8")
            lines = before.splitlines()
            entries, _s = parse_entries(lines)
            match = [e for e in entries if e["id"] == args.id and not e.get("archived")]
            if len(match) != 1:
                undo()
                print("[REFUSE] %s %s in %s" % (args.id, "is not a live entry" if not match
                                                else "appears %d times" % len(match), doc.name),
                      file=sys.stderr)
                return 1
            e = match[0]
            starts = sorted(x["line"] - 1 for x in entries)
            a, b = _own_span(e, starts, len(lines))
            fz = fenced_lines(lines)
            for i in range(a, b):
                if i not in fz and any(m.group(2) == rel
                                       for m in _EMBED_SRC_RE.finditer(lines[i])):
                    print("[UNCHANGED] %s already shows %s" % (args.id, rel))
                    return 0
            k = _entry_insert_at(lines, e, starts)
            ins = ["", embed]
            if k < len(lines) and lines[k].strip():
                ins.append("")
            lines[k:k] = ins
            after = "\n".join(lines) + "\n"

            def block_ok(al, nb, n):
                st2 = sorted(x["line"] - 1 for x in nb)
                a2, b2 = _own_span(n, st2, len(al))
                return embed in al[a2:b2]
            why = _verify_entry_write(before, after, args.id, block_ok)
            if why:
                undo()
                print("[REFUSE] image would not be clean: %s. Nothing was written." % why,
                      file=sys.stderr)
                return 1
            write_doc(doc, after, expect=before)
            if doc.read_text(encoding="utf-8") != after:
                print("[FAILED] %s on disk is not what image wrote. Read it before doing "
                      "anything else." % doc.name, file=sys.stderr)
                return 1
    except DocPathError:
        undo()
        raise
    print("[EMBEDDED] %s shows %s (copied from %s)" % (args.id, rel, src.as_posix()))
    link = reader_link(doc.name, args.id)
    if link:
        print("  the human sees it at %s once it lands." % link)
    print("  commit lands the picture with the doc:")
    print('    python <scripts>/orchdoc.py commit --doc %s --message "..." --commit' % slug)
    return 0


# --- what commit needs -----------------------------------------------------------------
def _blob_text(blob):
    """The text of a git blob, read as bytes and decoded as UTF-8 (`git()` decodes with the
    console's code page). "" when it cannot be read."""
    if not blob:
        return ""
    try:
        p = subprocess.run(["git", "-C", str(PROJECTS), "cat-file", "blob", blob],
                           capture_output=True, timeout=60, creationflags=flags())
    except (OSError, subprocess.SubprocessError):
        return ""
    return p.stdout.decode("utf-8", "replace") if p.returncode == 0 else ""


def _shown_embeds(lines, fz=None):
    """A comment may span lines; a fenced row neither opens nor closes one.
    """
    out = []
    fz = fenced_lines(lines) if fz is None else fz
    in_comment = False
    for i, ln in enumerate(lines):
        if i in fz:
            continue
        ln = _mask_code_spans([ln])[0]       # a `<!--` quoted in code opens no comment
        shown_ln, pos = [], 0
        while pos < len(ln):
            if in_comment:
                end = ln.find("-->", pos)
                if end < 0:
                    pos = len(ln)
                else:
                    in_comment, pos = False, end + 3
                    shown_ln.append(" ")
            else:
                start = ln.find("<!--", pos)
                if start < 0:
                    shown_ln.append(ln[pos:])
                    pos = len(ln)
                else:
                    shown_ln.append(ln[pos:start])
                    in_comment, pos = True, start + 4
        ln = "".join(shown_ln)
        if "](" not in ln:
            continue
        for m in _EMBED_SRC_RE.finditer(_mask_code_spans([ln])[0]):
            out.append((i, m.group(2).strip().replace("\\", "/")))
    return out


def _untracked_embeds(root, lines, fz=None):
    """ONE git call for all of them: `ls-files --others --exclude-standard` lists exactly the
    untracked files no .gitignore rule matches. ⛔ orchdoc-images/ is left out: `commit`
    lands the pictures a doc embeds from its own folder (`_images_to_land`). A file inside a
    nested repository is left out too."""
    root = Path(root).resolve()
    cand = []
    for i, u in _shown_embeds(lines, fz):
        if u == IMAGE_DIR or u.startswith(IMAGE_DIR + "/"):
            continue
        p, _why = reader_shows(u, [root])
        if p is None:
            continue
        try:
            rel = Path(p).resolve().relative_to(root).as_posix()
        except (OSError, ValueError):
            continue
        if rel == IMAGE_DIR or rel.startswith(IMAGE_DIR + "/"):
            continue                          # a link led there
        d, nested = Path(p).resolve().parent, False
        while d != root and root in d.parents:
            if (d / ".git").exists():
                nested = True
                break
            d = d.parent
        if not nested:
            cand.append((i, u, rel))
    if not cand:
        return []
    rels = sorted({c[2] for c in cand})
    try:
        p = subprocess.run(["git", "-C", str(root), "ls-files", "--others",
                            "--exclude-standard", "-z", "--"] + rels,
                           capture_output=True, timeout=60, creationflags=flags())
    except Exception:
        return []
    if p.returncode != 0:
        return []
    bad = {x for x in p.stdout.decode("utf-8", "replace").split("\0") if x}
    return [c for c in cand if c[2] in bad]


def _images_to_land(doc, pinned, text):
    """Workspace paths of the pictures `text` embeds from the doc's own folder,
    orchdoc-images/<doc>/, that `pinned` does not already hold as they are on disk. commit
    adds them to what it lands, so the Reader on main shows what the entry embeds."""
    out = set()
    if not text:
        return []
    lines = text.splitlines()
    fz = fenced_lines(lines)
    root = Path(PROJECTS).resolve()
    own = (root / IMAGE_DIR / _doc_slug(Path(str(doc)).name)).resolve()
    for _i, u in _shown_embeds(lines, fz):
        if not u.startswith(IMAGE_DIR + "/"):
            continue
        p, _why = reader_shows(u, [root])
        if p is None:
            continue
        try:
            p = Path(p).resolve()
            p.relative_to(own)
        except (OSError, ValueError):
            continue
        rel = p.relative_to(root).as_posix()
        if rel in out:
            continue
        rc, have, _e = git(["rev-parse", "--verify", "--quiet", "%s:%s" % (pinned, rel)])
        rc2, now, _e2 = git(["hash-object", "--path", rel, str(p)])
        if rc2 != 0 or not now.strip():
            continue
        if rc == 0 and have.strip() == now.strip():
            continue
        out.add(rel)
    return sorted(out)


def _drop_fenced_adds(diff_text, new_text):
    """A `--unified=0` diff of the doc with each ADDED line that sits inside a fenced block of
    `new_text` turned into context, so gate 5 reads only the prose a landing adds. Bold in
    pasted text is the source's, not the writer's, and the text must stay word for word."""
    if not diff_text or not new_text or "```" not in new_text:
        return diff_text
    fz = fenced_lines(new_text.splitlines())
    out, n = [], None
    for ln in diff_text.split("\n"):
        if ln.startswith("diff --git"):
            n = None
            out.append(ln)
            continue
        m = _HUNK_RE.match(ln)
        if m:
            n = int(m.group(3))
            out.append(ln)
            continue
        if n is None:
            out.append(ln)
            continue
        if ln.startswith("+"):
            out.append((" " + ln[1:]) if (n - 1) in fz else ln)
            n += 1
        elif ln.startswith(" "):
            out.append(ln)
            n += 1
        else:
            out.append(ln)
    return "\n".join(out)


def _selftest_copy_guards(fixtured):
    """E-COPYBYLINK, E-IMAGEBYPATH and W-PASTEDRIFT driven both ways, and E-PLATEHISTORY
    reading past a pasted block. A blocking code joins `fixtured` only when its refusal was
    SEEN to fire."""
    import tempfile
    ok = True
    td = Path(tempfile.mkdtemp(prefix="orchdoc-copyguard-")).resolve()
    scratch = Path(tempfile.mkdtemp(prefix="orchdoc-scratch-")).resolve()
    saved_roots = list(_ROOTS_CACHE)
    _ROOTS_CACHE[:] = [td]
    try:
        (td / "prompts").mkdir()
        prompt = td / "prompts" / "lane.md"
        prompt.write_text("You are lane o99L1.\nDo the work.\n", encoding="utf-8")
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
        (td / IMAGE_DIR / "o99").mkdir(parents=True)
        (td / IMAGE_DIR / "o99" / "card.png").write_bytes(png)
        shot = scratch / "shot.png"
        shot.write_bytes(png)
        doc = td / "ORCHESTRATOR-DECISIONS-o99.md"

        def codes(body, eid="T9905", status="OPEN", owner="the human", section="## §2.2 Yours"):
            doc.write_text(canonical_title("o99", "a role") + "\n\n%s\n\n### %s - a step for "
                           "the human\n**Status:** %s - **Owner:** %s\n\n%s\n"
                           % (section, eid, status, owner, body), encoding="utf-8")
            return {f.code for f in check_doc(doc)}

        ask = "Paste the prompt in `prompts/lane.md` into a new session."
        sha = paste_digest("You are lane o99L1.\nDo the work.")
        marked = "%s\n```text\nYou are lane o99L1.\nDo the work.\n```" % paste_marker(
            "prompts/lane.md", "none", sha)
        plate = "This entry previously said $25, which was wrong; o97 caught it and it is now " \
                "corrected."
        stamp1 = (
            "2026-10-21 20:07"
        )
        note_hist1 = ("> \U0001F440 **INSTRUCTION from the human** (" + stamp1 + "): Mission control is "
                      "defunct and superseded by the Reader. <!-- ORCHDOC:NOTE D9903.n1 ESC ACK o99 "
                      "2026-10-21 20:35 -->")
        # A multi-line note is stored as ONE line with `\n` escapes (see encode_note_breaks).
        stamp2 = (
            "2026-10-21 20:08"
        )
        note_hist2 = ("> **NOTE from the human** (" + stamp2 + "): First paragraph.\\n\\nThe old plan "
                      "is superseded.\\nThird line. <!-- ORCHDOC:NOTE D9903.n2 ESC -->")
        cases = [
            ("E-COPYBYLINK", "refuses a link", dict(body=ask), True),
            ("E-COPYBYLINK", "text in the entry",
             dict(body=ask + "\n\n```text\nYou are lane o99L1.\nDo the work.\n```"), False),
            ("E-COPYBYLINK", "pasted block", dict(body=ask + "\n\n" + marked), False),
            ("E-COPYBYLINK", "Reader link silent",
             dict(body="Paste the prompt from http://127.0.0.1:8787/#o99/W9901."), False),
            ("E-COPYBYLINK", "payload shown silent",
             dict(body="Run `python go.py` from `prompts/lane.md`."), False),
            ("E-COPYBYLINK", "not the human's: silent", dict(body=ask, eid="W9905", owner="o99"), False),
            ("E-COPYBYLINK", "ready form, not the human's: fires",
             dict(body="LANE PROMPT WRITTEN AND READY TO PASTE: prompts/lane.md", eid="W9905",
                  owner="o99"), True),
            ("E-IMAGEBYPATH", "refuses a path",
             dict(body="The card is at `%s`." % shot.as_posix(), eid="D9907"), True),
            ("E-IMAGEBYPATH", "embed clears it",
             dict(body="The card is at `%s`.\n\n![the card](%s/o99/card.png)"
                  % (shot.as_posix(), IMAGE_DIR), eid="D9907"), False),
            ("E-IMAGEBYPATH", "bad embed",
             dict(body="![the card](%s/o99/gone.png)" % IMAGE_DIR, eid="D9907"), True),
            ("E-IMAGEBYPATH", "shortened path",
             dict(body="See C:\\...\\scratchpad\\shot.png for the card.", eid="D9907"), True),
            ("E-IMAGEBYPATH", "finished silent",
             dict(body="The card is at `%s`." % shot.as_posix(), eid="D9907",
                  status="RESOLVED"), False),
            ("W-PASTEDRIFT", "unchanged silent", dict(body=marked), False),
            ("W-PASTEDRIFT", "edited block",
             dict(body=marked.replace("Do the work.", "Do other work.")), True),
            ("E-PLATEHISTORY", "inside a fence silent",
             dict(body="```text\n%s\n```" % plate, eid="D9903", section="## §2.1 Decisions"),
             False),
            ("E-PLATEHISTORY", "outside a fence",
             dict(body=plate, eid="D9903", section="## §2.1 Decisions"), True),
            ("E-PLATEHISTORY", "the owner's instruction silent",
             dict(body="Decide it.\n\n" + note_hist1, eid="D9903", section="## §2.1 Decisions"),
             False),
            ("E-PLATEHISTORY", "multi-line note silent",
             dict(body="Decide it.\n\n" + note_hist2, eid="D9903", section="## §2.1 Decisions"),
             False),
            ("E-PLATEHISTORY", "same word outside a note",
             dict(body="Decide it.\n\nThis was superseded by the Reader.\n\n" + note_hist1,
                  eid="D9903", section="## §2.1 Decisions"), True),
        ]
        for code, label, kw, want in cases:
            fired = code in codes(**kw)
            if fired and want and code in BLOCKING and label.startswith("refuses"):
                fixtured.add(code)
            good = fired == want
            ok &= good
            print("  [%s] %-12s -> %s: %s" % ("OK" if good else "FAIL", code, label,
                                              "fired" if fired else "silent"))
        prompt.write_text("You are lane o99L1.\nDo the new work.\n", encoding="utf-8")
        fired = "W-PASTEDRIFT" in codes(body=marked)
        ok &= fired
        print("  [%s] %-12s -> source changed: %s" % ("OK" if fired else "FAIL",
                                                     "W-PASTEDRIFT",
                                                     "fired" if fired else "silent"))
        prompt.unlink()
        fired = "W-PASTEDRIFT" in codes(body=marked)
        ok &= not fired
        print("  [%s] %-12s -> source gone silent: %s" % ("OK" if not fired else "FAIL",
                                                         "W-PASTEDRIFT",
                                                         "fired" if fired else "silent"))
    finally:
        _ROOTS_CACHE[:] = saved_roots
        _remove_tree(str(td))
        _remove_tree(str(scratch))
    return ok


WORDS_ELSEWHERE_FROM = "2000-01-01"
_WE_WORDS = r"(?:wording|words?|copy|texts?|messages?|labels?|strings?|phrasing|script lines?)"
_WE_WORD_RE = re.compile(r"\b%s\b" % _WE_WORDS, re.I)
_WE_LOCATE_RE = re.compile(
    r"\b%s\b(?:\s+[^\s|]+){0,4}?\s+(?:is|are|sits?|lives?|lies?|stays?)\s+(?:only\s+)?"
    r"(?:in|on|at)\b" % _WE_WORDS, re.I)
# "see" and "check" are left out: "see the guard comment in `send_note.ts`" points at code.
_WE_READ_AT_RE = re.compile(
    r"\b(?:read|review|edit|approve)\b[^.;|]{0,80}?\b(?:in|on|at)\s+"
    r"(?:the\s+(?:PR|pull request|branch|file|draft)\b|PR\s*#?\d+|\[|`|https?://)", re.I)
_WE_POINTER_RE = re.compile(
    r"\b(?:in|on|see|read|from|at)\s+the\s+(?:PR|pull request|branch)\b|\bthe\s+branch\b"
    r"|\bPR\s*#?\d+\b|github\.com/[^\s)]+/pull/\d+|[\w./\\-]+\.(?:tsx?|jsx?|py|json|md)\b", re.I)
_WE_ITEM_RE = re.compile(r"(\s*)(?:([-*+])|(\d+)[.)])\s")
_WE_SLEEP_SCRIPT_RE = re.compile(
    r"https?://[^\s)\]]*#scripts/[^\s)\]]*", re.I)
_WE_QUOTE_RE = re.compile(r'"([^"\n]+)"|“([^”\n]+)”')


def _we_units(body):
    """Cut a body into list items (with continuation lines) and paragraphs."""
    units, cur = [], []
    for ln in body.split("\n"):
        if not ln.strip():
            if cur:
                units.append(cur)
            cur = []
        elif _WE_ITEM_RE.match(ln) and cur:
            units.append(cur)
            cur = [ln]
        else:
            cur.append(ln)
    if cur:
        units.append(cur)
    return ["\n".join(u) for u in units]


def _we_shown(text):
    """True when `text` holds a blockquote or a fenced block."""
    return bool(re.search(r"^\s*(?:>|```|~~~)", text, re.M))


def _we_long_quotes(text):
    return sum(1 for m in _WE_QUOTE_RE.finditer(text)
               if len((m.group(1) or m.group(2) or "").split()) >= 4)


def _we_numbered_after(units, i):
    """How many NUMBERED items follow unit i as its own list: nested deeper than its list item,
    or straight after its paragraph. Stops at the first unit that is not such an item."""
    m = _WE_ITEM_RE.match(units[i])
    depth = len(m.group(1).expandtabs(4)) if m else -1
    n = 0
    for u in units[i + 1:]:
        mm = _WE_ITEM_RE.match(u)
        if not mm or not mm.group(3) or len(mm.group(1).expandtabs(4)) <= depth:
            break
        n += 1
    return n


def _we_sends(sent, unit):
    """True when one sentence sends the human to a PR, branch or file for words."""
    bare = _WE_SLEEP_SCRIPT_RE.sub("", sent)
    if not _WE_POINTER_RE.search(bare):
        return False
    if _WE_LOCATE_RE.search(bare):
        return True
    return bool(_WE_READ_AT_RE.search(bare)) and bool(
        _WE_WORD_RE.search(_WE_SLEEP_SCRIPT_RE.sub("", unit)))


def words_sent_elsewhere(body):
    """Sentences that send the human to a PR, branch or file to read words not quoted in the entry."""
    out = []
    units = _we_units(body)
    for i, u in enumerate(units):
        nxt = units[i + 1] if i + 1 < len(units) else ""
        hit = None
        # A table cell is its own sentence: a wording word in one cell and a path in another
        # are two facts about a row, not a pointer for words.
        for sent in re.split(r"(?<=[.!?])\s+|\s*\|\s*", u):
            if _we_sends(sent, u):
                hit = sent.strip()
                break
        if not hit or _we_shown(u) or _we_shown(nxt):
            continue
        if _we_long_quotes(u) + _we_long_quotes(nxt) >= 2:
            continue
        if _we_numbered_after(units, i) >= 2:
            continue
        out.append(hit)
    return out


def _words_elsewhere_applies(e):
    """Open, the human-owned, and opened on or after WORDS_ELSEWHERE_FROM."""
    if e.get("archived") or status_of(e["body"]) in TERMINAL_STATUS:
        return False
    if not owner_is_human(e["body"]):
        return False
    m = re.search(r"\*\*Opened:?\*\*[^\n]*?(\d{4}-\d{2}-\d{2})", e["body"])
    return bool(m) and m.group(1) >= WORDS_ELSEWHERE_FROM


def _selftest_words_elsewhere():
    bad = ("The draft words are in `client/src/wording.ts` on the PR. They are yours to edit; "
           "read them in the PR before release.")
    good = bad + '\n\n> 1. "Send the welcome note again please" (first page)\n'
    return bool(words_sent_elsewhere(bad)) and not words_sent_elsewhere(good)


def check_doc(path):
    """Run every invariant against one doc. Returns a list of Finding."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return [Finding("E-IO", 0, "cannot read: %s" % e)]

    lines = text.splitlines()
    # For CONTENT scans only. Same length as `lines`, so indices and reported line numbers stay
    # true - the attestation is blanked, not removed. Structural checks keep using `lines`;
    # anything matching on what the text SAYS should use this, because an attestation quotes bad
    # text on purpose. See without_attestations().
    lines_live = without_attestations(text).splitlines()
    findings = []
    entries, sections = parse_entries(lines)
    _prose_bodies(entries, lines)
    _copy_guard_doc = path

    for _n in unreachable_archived(lines, entries):
        _m = ARCHIVED_ID_RE.search(lines[_n - 1])
        findings.append(Finding(
            "E-HIDDENENTRY", _n,
            "an archived heading for %s is in the file, and no parser can reach it"
            % _m.group(1),
            "almost certainly inside a ``` fence. Move the ENTRY out; leave every fence "
            "where it is and assert the count is unchanged before writing - deleting one "
            "took this doc's reachable archived headings from 161 to 126"))

    # --- E-DUPID: one ID, one entry. The single highest-value invariant. ---
    by_id = defaultdict(list)
    _stopsign_ok = follows_stopsign_convention(entries)
    for e in entries:

        if status_of(e["body"]) in TERMINAL_STATUS and "\u26d4" in e["body"]:
            findings.append(Finding(
                "W-STOPSIGN", e["line"],
                "%s is closed but uses \u26d4, which is reserved for NOT YET DONE" % e["id"],
                "\u203c\ufe0f for plain emphasis, \u26a0\ufe0f for caution - reserving \u26d4 "
                "lets `E-CLOSEDWITHOPENSUBS` read it as state"))

        _open_sub = (first_open_subitem(e["body"], _stopsign_ok)
                     if (e["id"][:1] in ("D", "T", "W", "A")
                         and status_of(e["body"]) in TERMINAL_STATUS) else None)
        if _open_sub is not None:
            findings.append(Finding(
                "E-CLOSEDWITHOPENSUBS", e["line"] + _open_sub[0],
                "%s claims a closed status while a sub-item under it is still not done"
                % e["id"],
                "mark the container IN PROGRESS and strike the finished sub-items; it moves "
                "to \u00a799 only when ALL of them are done (the rule)"))

        if (e["id"][:1] in ("D", "T", "W", "A")
                and status_of(e["body"]) in LIVE_STATUS):
            done_unstruck, open_subs = [], 0
            for off, raw in enumerate(e["body"].splitlines()[1:]):
                if not re.match(r"^\s*[-*+]\s", raw):
                    continue
                txt = re.sub(r"`[^`]*`", "", raw)
                if UNCHECKED_BOX_RE.match(raw):
                    open_subs += 1
                    continue
                if NOTDONE_MARK_RE.search(txt):
                    open_subs += 1
                    continue
                if looks_open(txt):
                    # Neither settled nor plainly open - so it is NOT evidence the container is
                    # finished, and NOT something to demand a tick on. Same predicate the fixer
                    # uses, deliberately: when they diverged, the gate demanded back exactly the
                    # false ticks a colleague had just reverted.
                    open_subs += 1
                    continue
                if not DONE_MARK_RE.search(NOTDONE_MARK_RE.sub(" ", txt)):
                    continue
                # A checked checkbox IS the whole form - green tick, grey, strike-through, all
                # native. A literal <span style=…> is not a second way of achieving it; it is
                # visible junk in the sentence, so it counts as a defect rather than a mark.
                if not CHECKED_BOX_RE.search(raw):
                    done_unstruck.append((e["line"] + 1 + off, ["checkbox"]))
                elif GREY_SPAN_RE.search(raw):
                    done_unstruck.append((e["line"] + 1 + off, ["literal-span"]))
            if done_unstruck:
                short = ", ".join("L%d(%s)" % (n, "+".join(m)) for n, m in done_unstruck[:5])
                findings.append(Finding(
                    "E-SETTLEDNOTSTRUCK", e["line"],
                    "%s is %s but %d settled sub-item(s) are not fully marked done, so the human "
                    "must read the whole entry to find what is still owed"
                    % (e["id"], status_of(e["body"]), len(done_unstruck)),
                    "the form is a CHECKED CHECKBOX and nothing else - `- [x] the thing`. It "
                    "renders green-ticked, GREY and STRUCK THROUGH natively, which is all three "
                    "of the human's requirements from four characters. A literal <span style=...> is "
                    "NOT rendered here and shows as visible junk mid-sentence. "
                    "Fix: orchdoc.py strike --doc <doc> --commit. At: %s" % short))

            if open_subs == 0 and any(
                    DONE_MARK_RE.search(NOTDONE_MARK_RE.sub(" ", re.sub(r"`[^`]*`", "", r)))
                    for r in e["body"].splitlines()[1:]
                    if re.match(r"^\s*[-*+]\s|^\s*\d+\.\s", r)):
                findings.append(Finding(
                    "E-ALLSUBSDONE", e["line"],
                    "every sub-item under %s is done but the container is still %s, so finished "
                    "work is sitting on a live plate" % (e["id"], status_of(e["body"])),
                    "close it and move it whole: set a terminal status, then "
                    "orchdoc.py archive --doc <doc> --commit"))
        by_id[e["id"]].append(e)
    for eid, group in sorted(by_id.items()):
        if len(group) > 1:
            statuses = []
            for g in group:
                statuses.append(status_of(g["body"]) or "unstated")
            where = ", ".join("line %d (%s)" % (g["line"], s)
                              for g, s in zip(group, statuses))
            conflict = len(set(statuses)) > 1
            findings.append(Finding(
                "E-DUPID", group[0]["line"],
                "ID '%s' appears in %d entries%s" % (
                    eid, len(group), " with CONFLICTING status" if conflict else ""),
                where))

    # --- E-STALEPROSE / E-RUBBERSTAMP: reasoning whose inputs moved under it ---
    by_id_single = {e["id"]: e for e in entries if len(by_id.get(e["id"], [])) == 1}
    # One git read for the whole doc rather than one per entry.
    # No doc slug means no entry namespace, so trailers cannot be attributed to it.
    # Scanning anyway let real repo history bleed into synthetic fixtures - the check
    # must be a function of the document, not of whatever else is in the repo.
    _slug_m = re.search(r"ORCHESTRATOR-DECISIONS-(o\d+)", path.name)
    if _slug_m:
        pushed, bad_touches = touches_since(_slug_m.group(1), None)
    else:
        pushed, bad_touches = {}, {}
    known_ids = {e["id"] for e in entries}
    _ = bad_touches
    for eid in sorted(pushed):
        if eid not in known_ids:
            findings.append(Finding(
                "E-BADTOUCH", 0,
                "commit trailer names %s, which is not an entry in this doc" % eid,
                "a typo here is an invisible non-update"))
    for e in (entries if _wants("E-DONEBUTOPEN") else ()):
        if e.get("archived") or status_of(e["body"]) not in ("OPEN", "BLOCKED"):
            continue
        cond = done_when(e["body"])
        if not cond:
            continue
        sat, evidence = done_when_satisfied(cond, opened_of(e["body"]), PROJECTS)
        if sat:
            findings.append(Finding(
                "E-DONEBUTOPEN", e["line"],
                "%s is still OPEN but its own Done-when is satisfied: %s" % (e["id"], evidence),
                "resolve it (orchdoc.py resolve --doc <d> %s --ruling \"...\") or, if it is "
                "genuinely not finished, correct the Done-when - a condition that fires early "
                "is worse than none" % e["id"]))

    _product = (resolve_product_citations(
        [p for e in entries if not e.get("archived") for p in cited_paths(e["body"])],
        path.name)
        if (PRODUCT_STALE_BLOCKS
            or _wants("W-PRODUCTSTALE", "W-PRODUCTGONE", "W-PRODUCTNONEEDLE")) else {})

    if _wants("E-STALEPROSE"):
        prefetch_newest_commits(
            p for e in entries if not e.get("archived")
            for p in cited_paths(e["body"]) if not cited_regions(e["body"]).get(p))

    for e in (entries if _wants("E-AMBIGUOUSDATE", "E-NODEPS", "E-RUBBERSTAMP", "E-STALEPROSE",
                                "W-DEADCITE", "W-NOBLOCKER", "W-NOPROV", "W-STALLED",
                                "W-PRODUCTSTALE", "W-PRODUCTGONE", "W-PRODUCTNONEEDLE")
              else ()):
        if e.get("archived"):
            continue
        deps = depends_of(e["body"])
        cited = cited_paths(e["body"])
        for _dm in DEPENDS_LINE_RE.finditer(e["body"]):
            if not BARE_FILEISH_RE.search(_dm.group(1)):
                continue                   # names only entry ids - a different kind of edge
            if cited_paths(_dm.group(0)):
                continue                   # at least one citation parsed; not this defect
            findings.append(Finding(
                "W-DEADCITE", e["line"],
                "%s declares a **Depends:** on a file but it parses as NO citation, so the "
                "entry can never restale: %s" % (e["id"], _dm.group(1).strip()[:60]),
                "put backticks round the PATH - **Depends:** `path/to/doc.md#<id>` - not "
                "round the whole line"))
        cited = [c for c in cited if pathlib.Path(str(c)).name != path.name]
        st_now = status_of(e["body"])
        if st_now in ("BLOCKED", "SEQUENCED") and not e.get("archived"):
            names_it = (DEPENDS_RE.search(e["body"])
                        or DONE_WHEN_RE.search(e["body"])
                        or re.search(r"\*\*(?:Blocked-by|Waiting-on|Sequenced-behind):\*\*",
                                     e["body"], re.I))
            if not names_it:
                findings.append(Finding(
                    "W-NOBLOCKER", e["line"],
                    "%s is %s but names nothing it waits on, so nobody can tell when it "
                    "stops being %s" % (e["id"], st_now, st_now),
                    "add **Depends:**, **Done-when:** or **Blocked-by:** naming the thing"))

        if (st_now in ("OPEN", "IN PROGRESS") and not e.get("archived")
                and e["id"][:1] in ("W", "T")):
            _age = _days_since_opened(e["body"])
            _waits = (DEPENDS_RE.search(e["body"])
                      or re.search(r"\*\*(?:Blocked-by|Waiting-on|Sequenced-behind):\*\*",
                                   e["body"], re.I)
                      or re.search(r"(?i)\b(?:blocked on|waiting on|not mine)\b", e["body"]))
            if _age is not None and _age >= STALLED_DAYS and not _waits:
                findings.append(Finding(
                    "W-STALLED", e["line"],
                    "%s has been open %d days and names nothing it waits on" % (e["id"], _age),
                    "pick one: act on it, or give it **Blocked-by:** naming what it waits on. "
                    "A review is not an outcome - re-reading it again changes nothing"))

        if (st_now or "").upper() in CLOSED_STATES and not e.get("archived"):
            _subs = [s for s in OPEN_SUB_RE.findall(e["body"])
                     if not FOREIGN_SUB_RE.search(s)]
            _defer = [s for s in _subs if SCHEDULES_RE.search(s)]
            if _defer and not PROVENANCE_RE.search(e["body"]):
                findings.append(Finding(
                    "W-NOPROV", e["line"],
                    "%s is %s but schedules future work with no provenance: %s"
                    % (e["id"], st_now, _defer[0].strip()[:60]),
                    "add **Provenance:** saying WHY the task outlives the ruling - a cleanup "
                    "sweep cannot tell a deferred task from a stale one without it"))

        if not deps and not cited and e["id"] not in pushed:
            if (status_of(e["body"]) in {"RESOLVED", "ANSWERED", "DONE"}
                    and re.search(RESOLVED_LINE_RE, e["body"])):
                findings.append(Finding(
                    "E-NODEPS", e["line"],
                    "ruled decision '%s' declares no **Depends:** edge, so nothing can "
                    "ever mark it stale" % e["id"],
                    "name what the ruling rests on, or it looks permanently current"))
            continue
        rdate, attestation = reviewed_of(e["body"])

        moved = []
        ambiguous = []
        bare_citations = set()

        _born = None
        if rdate is None:
            _born = entry_created(path.name, e["id"]) or _now_iso()

        # DERIVED edges: the section cited these, so it already declared them.
        rstamp = _as_stamp(rdate) if rdate else _as_stamp(_born)
        regions = cited_regions(e["body"])
        # A citation that narrowed itself to `path#needle` comes back only when some commit
        # since the review touched a needle - every such commit is read, not just the newest -
        # and it names that commit. See WHAT A COMMIT DID TO A NEEDLE.
        for pth, _pv in paths_changed_since(cited, rstamp, regions).items():
            when, subject = _pv[0], _pv[1]
            psha = _pv[2] if len(_pv) > 2 else ""
            why = _pv[3] if len(_pv) > 3 else ""
            needles = regions.get(str(pth)) or []
            # A narrowed path was already filtered commit by commit, and a fail-closed answer
            # may carry no usable time - re-filtering here would drop exactly that answer.
            if not needles and not (rstamp is None or when > rstamp):
                continue
            note = " [%s]" % why if why else ""
            if "ORCHESTRATOR-DECISIONS-" in str(pth):
                touched = entries_touched(psha, pathlib.Path(str(pth)).name)
                if touched:
                    note += " [touched: %s]" % ", ".join(touched[:8])
                elif touched == [] and not needles:
                    continue
            if not needles:
                bare_citations.add(pathlib.Path(str(pth)).name)
            moved.append("%s changed %s%s (%s)"
                         % (pth, when[:16].replace("T", " "), note, subject[:44]))

        _baseline = rdate or ("written %s, never reviewed" % _born[:16]
                              if _born else "never reviewed")
        for pth in cited:
            pc = _product.get(pth)
            if not pc or pc.get("skip"):
                continue
            needles = regions.get(str(pth)) or []
            gone = pc.get("gone")
            gone_to = _renamed_to(pc["root"], gone[0], pc["rel"]) if gone else ""
            hit = None
            if needles:
                try:
                    hit = product_needle_mover(pc["root"], pc["ref"], pc["rel"], rstamp,
                                               needles)
                except Exception as exc:     # advice, never a crash of `check`
                    PRODUCT_NOTES[(path.name, pc["label"], "the needle test failed (%s)"
                                   % type(exc).__name__)] += 1
                    continue
            if hit:
                why = hit[3]
                if gone and hit[2] == gone[0]:
                    why = "renamed to %s" % gone_to if gone_to else "the file was deleted"
                # The commit first: the detail is cut at 100 characters, and the sha is what
                # the reader needs to look at the change.
                what = "%s on %s %s%s (%s)" % (
                    (hit[2] or "?")[:8], pc["ref"], hit[0][:16].replace("T", " "),
                    " [%s]" % why if why else "", hit[1][:44])
                if PRODUCT_STALE_BLOCKS:
                    moved.append("%s changed: %s" % (pth, what))
                else:
                    findings.append(Finding(
                        "W-PRODUCTSTALE", e["line"],
                        "entry '%s' rests on %s (%s), which moved after it was last reviewed"
                        " (%s)" % (e["id"], pth, pc["label"], _baseline),
                        what + " - walk it and re-review"))
            elif gone:
                findings.append(Finding(
                    "W-PRODUCTGONE", e["line"],
                    "entry '%s' cites %s (%s), which is not on %s: %s by %s %s (%s)"
                    % (e["id"], pth, pc["label"], pc["ref"],
                       "renamed to %s" % gone_to if gone_to else "deleted",
                       gone[0][:8], gone[1][:10], gone[2][:44]),
                    "point the citation at %s" % gone_to if gone_to
                    else "cite what replaced it, or drop the citation"))
        _nn = product_nonneedle_paths(e["body"], _product)
        if _nn:
            findings.append(Finding(
                "W-PRODUCTNONEEDLE", e["line"],
                "entry '%s' cites %d product file(s) with no #needle, so no commit there can "
                "make it stale: %s" % (e["id"], len(_nn), ", ".join(_nn)),
                "add #<a word from the line it rests on> inside each citation's backticks"))

        # PUSH edge: it is timestamped by git, so it resolves same-day ordering
        # that the date-only PULL edge cannot see.
        for eid, _v in pushed.items():
            when, subject = _v[0], _v[1]
            sha = _v[2] if len(_v) > 2 else ""
            # `rstamp` is the review stamp, or the entry's own creation instant when it has
            # never been reviewed. It was `rdate is None or ...`, which made every landing
            # count against an entry that had no stamp yet - including its own.
            if eid != e["id"] or not (when > rstamp):
                continue
            if sha:
                _created, _plate = _commit_role(sha, path.name, eid)
                # Creating an entry is not the world moving under it, and re-stamping its plate
                # line changed no reasoning. Either way there is nothing to re-read.
                if _created or _plate:
                    continue
            moved.append("work touching %s landed %s (%s)"
                         % (eid, when[:16].replace("T", " "), subject[:48]))

        for d in deps:
            dep = by_id_single.get(d)
            if dep is None:
                if re.match(r"^[A-Z]{1,3}(\d|-)", d):
                    findings.append(Finding(
                        "E-STALEPROSE", e["line"],
                        "entry '%s' depends on '%s', which does not resolve to an entry"
                        % (e["id"], d),
                        "a dependency that names nothing cannot flag anything"))
                continue
            dmoved = last_moved(dep)
            if not dmoved:
                continue
            # Same correction as the two edges above: with no review stamp, measure from
            # when the entry was written rather than from nothing.
            rday = (rdate or _born or "")[:10]
            if dmoved > rday:
                moved.append("%s moved %s" % (d, dmoved))
            elif dmoved == rday and len(rdate or _born or "") <= 10:
                ambiguous.append("%s also moved %s" % (d, dmoved))

        if ambiguous and not moved:
            findings.append(Finding(
                "E-AMBIGUOUSDATE", e["line"],
                "entry '%s' was reviewed the SAME DAY as contending work, and a bare "
                "date cannot say which came first" % e["id"],
                "; ".join(ambiguous) + " - re-attest with a precise timestamp"))
            continue

        if moved:
            findings.append(Finding(
                "E-STALEPROSE", e["line"],
                # Name the baseline the verdict actually used. "never reviewed" alone left
                # the author unable to tell WHICH instant the mover was compared against, and
                # that is the whole question when the entry is a day old.
                "entry '%s' rests on facts that moved after it was last reviewed (%s)"
                % (e["id"], rdate or ("written %s, never reviewed" % _born[:16]
                                      if _born else "never reviewed")),
                "; ".join(moved) + " - walk it and re-attest, naming what changed"))
        elif rdate and attestation:
            # An attestation exists. Is it a rubber stamp?
            if (RUBBER_STAMP_RE.match(attestation)
                    or len(attestation) < MIN_ATTESTATION_CHARS):
                findings.append(Finding(
                    "E-RUBBERSTAMP", e["line"],
                    "entry '%s' attestation says nothing: %r" % (e["id"], attestation[:50]),
                    "name WHAT changed and WHY the conclusion survives it, "
                    "or the check becomes compliance without thought"))

    for e in entries:
        if e.get("archived"):
            continue
        body_lines = e["body"].splitlines()[1:]
        for off, raw in enumerate(body_lines):
            if not re.match(r"^\s*[-*+]\s|^\s*\d+\.\s", raw):
                continue
            txt = re.sub(r"`[^`]*`", "", raw)
            notdone = NOTDONE_MARK_RE.search(txt)
            done = DONE_MARK_RE.search(NOTDONE_MARK_RE.sub(" ", txt))

            if done and not notdone and not CHECKED_BOX_RE.search(raw):
                findings.append(Finding(
                    "W-STRIKEDONE", e["line"] + 1 + off,
                    "a done sub-item is not a checked checkbox, so it is not greyed out",
                    "`- [x] the thing` - green tick, GREY and strike-through, all native. "
                    "Not `~~ ~~`, which strikes without greying, and not a <span>, which "
                    "renders as visible literal text here"))

            if done and notdone:
                findings.append(Finding(
                    "E-MIXEDSTATE", e["line"] + 1 + off,
                    "one bullet asserts both '%s' and '%s', so its leading marker "
                    "cannot be true of everything under it"
                    % (done.group(0)[:18], notdone.group(0)[:18]),
                    "split it: one status and one owner per actionable item"))

    for e in entries:
        if e.get("archived"):
            continue
        if status_of(e["body"]) in PLATE_STATUS:
            if not re.search(r"\*\*Owner:\*\*", e["body"], re.I):
                findings.append(Finding(
                    "E-NOOWNER", e["line"],
                    "entry '%s' is not done and names no owner, so the human cannot tell "
                    "whether it is a request to them" % e["id"],
                    "add **Owner:** - the human's entries are the only class that costs them anything"))

    # --- THE HUMAN'S CLARITY CHECKS ---
    for e in entries:
        if e.get("archived"):
            continue
        st = status_of(e["body"])
        if not st:
            continue

        governing = _governing_section(lines, e["line"])
        in_live = is_active_section(e["section"])
        if not in_live and governing and governing.split(".")[0] in ("2", "3"):
            in_live = True
        if st in TERMINAL_STATUS and in_live:
            findings.append(Finding(
                "E-DONEINACTIVE", e["line"],
                "'%s' is %s but sits under '%s', which promises live items"
                % (e["id"], st, (e["section"] or "")[:40]),
                "move it to a resolved section: orchdoc.py archive --doc <doc>"))

        # The heading marker is DERIVED from the field, so it cannot contradict it.
        want = STATUS_MARKER.get(st)
        head = e["title"]
        if want and want not in head:
            wrong = [m for m in STATUS_MARKER.values() if m != want and m in head]
            findings.append(Finding(
                "E-MARKERDRIFT", e["line"],
                "'%s' is %s but its heading %s"
                % (e["id"], st,
                   "carries a different marker" if wrong else "carries no marker"),
                "orchdoc.py normalize --doc <doc> regenerates markers from the field"))

        needle_fence = False
        for off, raw in enumerate((e.get("body") or "").splitlines(), start=0):
            if raw.lstrip().startswith("```"):
                needle_fence = not needle_fence
                continue
            if needle_fence or raw.startswith("    ") or raw.startswith("\t"):
                continue
            for m in NEEDLE_WITH_SPACE_RE.finditer(raw):
                cited_file, needle, tail = (m.group(1), m.group(2), m.group(3)) if m.group(1) \
                    else (m.group(4), m.group(5), m.group(6))
                # Resolved against every root a citation may live under, not only the
                # workspace: a `memory/...` citation lives in the memory clone, and the
                # workspace-only test exempted every one of them. See resolve_cited_path.
                if resolve_cited_path(cited_file) is None:
                    continue                 # an ellipsis or an example, not a live citation
                findings.append(Finding(
                    "E-NEEDLESPACE", e["line"] + off,
                    "citation `%s#%s%s` - the needle contains a space, so the WHOLE citation is "
                    "dropped and this entry is not narrowed at all"
                    % (cited_file[:40], needle, tail[:12]),
                    "use a space-free needle. Prefer a CONTENT word over an id: `#tripled` beats "
                    "`#X6 -` - it excludes the X60-X69 siblings AND catches edits to the text"))

    # --- WALLS OF TEXT (advisory) ---
    para, pstart = [], 0
    in_fence2 = False

    def _flush(para, pstart):
        if not para:
            return
        joined = " ".join(para)
        if joined.lstrip().startswith(("|", ">", "#", "-", "*")):
            return
        if len(joined) > WALL_CHARS:
            findings.append(Finding(
                "W-WALLOFTEXT", pstart,
                "paragraph is %d characters with no breaks" % len(joined),
                "split it: blank lines and real list items, not inline separators"))
        if len(INLINE_ENUM_RE.findall(joined)) >= INLINE_ENUM_MIN:
            findings.append(Finding(
                "W-INLINEENUM", pstart,
                "an enumeration is buried mid-paragraph",
                "put each item on its own line as a real list"))

    for i, raw in enumerate(lines, start=1):
        if raw.lstrip().startswith("```"):
            in_fence2 = not in_fence2
            continue
        if in_fence2:
            continue
        if len(FAKE_BULLET_RE.findall(raw)) >= FAKE_BULLET_MIN and len(raw) > 160:
            findings.append(Finding(
                "W-FAKEBULLETS", i,
                "a middle-dot separator is used as a pseudo-bullet %d times"
                % len(FAKE_BULLET_RE.findall(raw)),
                "it does not render as a list - use '- ' after a line break"))
        if raw.strip():
            if not para:
                pstart = i
            para.append(raw.strip())
        else:
            _flush(para, pstart)
            para = []
    _flush(para, pstart)

    # --- RECORDED OVERRIDES: outstanding, and their reasons held to the same bar ---
    for code, who, when, reason in overrides_in(text):
        if not reason or RUBBER_STAMP_RE.match(reason) or len(reason) < MIN_ATTESTATION_CHARS:
            findings.append(Finding(
                "E-RUBBERSTAMP", 0,
                "override of %s by %s gives no real reason: %r" % (code, who, reason[:40]),
                "an override reason held to a lower bar than an attestation becomes "
                "'needed to ship' within a week"))
        else:
            findings.append(Finding(
                "W-OVERRIDE", 0,
                "%s is overridden by %s since %s" % (code, who, when[:16]),
                reason[:96]))

    # --- W-UNFALSIFIABLE: an attestation that SOUNDS verified but names no falsifier ---
    for e in entries:
        if e.get("archived"):
            continue
        _rd, att = reviewed_of(e["body"])
        if not att or not VERIFY_LANGUAGE_RE.search(att):
            continue
        if HUMAN_RULING_RE.search(att):
            continue        # a ruling, not a measurement: nothing could falsify it
        if not FALSIFIER_RE.search(att):
            findings.append(Finding(
                "W-UNFALSIFIABLE", e["line"],
                "'%s' claims verification but names nothing that would have shown "
                "otherwise" % e["id"],
                "an oracle for the wrong question is still a proxy - say what would "
                "have come back the other way"))

    want_num = None
    m = re.search(r"ORCHESTRATOR-DECISIONS-(o\d+)", str(path))
    if m:
        want_num = m.group(1)
    h1 = next((l for l in lines[:40] if l.startswith("# ")), None)
    if want_num:
        if h1 is None:
            findings.append(Finding(
                "E-TITLE", 1, "no H1 identity line",
                "orchdoc.py scaffold --doc %s writes it" % want_num))
        else:
            tm = TITLE_RE.match(h1)
            if not tm:
                findings.append(Finding(
                    "E-TITLE", lines.index(h1) + 1,
                    "H1 is not the canonical identity line",
                    "expected: %s" % canonical_title(want_num, "(role)")))
            elif tm.group(1).lower() != want_num.lower():
                findings.append(Finding(
                    "E-TITLE", lines.index(h1) + 1,
                    "H1 says %s but the file is %s" % (tm.group(1), want_num),
                    "a doc naming the wrong orchestrator routes readers to the wrong session"))

    _today = _dt.date.today().isoformat()
    for e in entries:
        for m in re.finditer(
                r"\*\*(Attested-by|Reviewed|Recorded|Resolved|Opened)"
                r":\*\*[^\n]*?(\d{4}-\d{2}-\d{2})", e["body"]):
            if m.group(2) > _today:
                findings.append(Finding(
                    "E-FUTUREDATE", e["line"],
                    "entry '%s' is attested %s, which is AFTER today (%s)"
                    % (e["id"], m.group(2), _today),
                    "a date that has not happened cannot attest to work that has"))
                break

    for i, ln in enumerate(lines):
        m = re.match(r"^###\s+(?:\W+\s*)?([A-Za-z][\w.-]{0,6})\s*[-–:]", ln)
        if not m:
            continue
        tok = m.group(1)
        if ID_RE.match(tok) or not re.match(r"^[A-Z]{1,3}[-\d]", tok):
            continue
        findings.append(Finding(
            "E-BADID", i + 1,
            "heading looks like an entry but '%s' is not a valid id, so it is INVISIBLE "
            "to check and to the generated index" % tok,
            "ids are LETTERS then DIGITS  - not '%s'" % tok))

    for e in entries:
        if e.get("archived") or not re.match(r"^T\d+$", e["id"]):
            continue
        sec = (e.get("section") or "").lstrip("# ")
        here = re.match(r"^§\s*([\d.]+)", sec)
        if not here or here.group(1).split(".")[0] in ("2", "99"):
            continue
        findings.append(Finding(
            "E-WRONGSECTION", e["line"],
            "%s uses the T namespace but sits in §%s - T<n> is THE HUMAN'S to-do list, so a T id "
            "outside §2.3 means 'T9903' points at two different things depending on who says it"
            % (e["id"], here.group(1)),
            "an orchestrator's own work is W<n> in §3. Rename this entry to W<n>, or move it "
            "to §2.3 if it is genuinely an ask for the human"))

    _parent_heads = [(i, m.group(1)) for i, l in enumerate(lines)
                     if (m := SECTION_RE.match(l)) and "." not in m.group(1)]
    _all_heads = [(i, m.group(1)) for i, l in enumerate(lines) if (m := SECTION_RE.match(l))]
    for ln, num in _parent_heads:
        sub = next((h for h in _all_heads
                    if h[0] > ln and h[1].startswith(num + ".")), None)
        if not sub:
            continue                     # no subsections: the body is legitimately its own
        stray = [i + 1 for i in range(ln + 1, sub[0])
                 if re.match(r"^#{3,4}\s", lines[i])]
        asks = [i + 1 for i in range(ln + 1, sub[0])
                if re.match(r"^[-*+]\s", lines[i])]
        if stray or asks:
            what = []
            if stray:
                what.append("%d entr(ies)" % len(stray))
            if asks:
                what.append("%d loose bullet(s)" % len(asks))
            findings.append(Finding(
                "E-LOOSEINPARENT", ln + 1,
                "§%s holds %s in its own body, above §%s - a loose bullet belongs to NO entry, "
                "so every check here (which is triggered by an entry) is blind to it and it is "
                "never swept, statused or archived" % (num, " and ".join(what), sub[1]),
                "a section WITH subsections is a container: heading, and a description or index "
                "at most. Move each item into the subsection that owns it - a decision to §%s.1, "
                "a question to .2, a to-do to .3. Lines %s"
                % (num, ", ".join(str(n) for n in sorted(stray + asks)[:6]))))

    for e in entries:
        sec = (e.get("section") or "").lstrip("# ")
        if e.get("archived") or not sec.startswith("§2"):
            continue
        hits = []
        in_settled = False
        in_strike = False          # a ~~span~~ left open by the previous line
        for off, raw in enumerate(e["body"].splitlines()[1:], start=1):
            if STAMP_LINE_RE.match(raw):
                continue
            if NOTE_LINE_RE.match(raw.rstrip()):
                continue
            was_in_strike = in_strike
            if raw.count("~~") % 2:
                in_strike = not in_strike
            if was_in_strike:
                continue
            is_bullet = bool(re.match(r"^\s*(?:[-*+]|\d+\.)\s", raw))
            if is_bullet:
                in_settled = bool(CHECKED_BOX_RE.search(raw)
                                  or DONE_MARK_RE.search(NOTDONE_MARK_RE.sub(" ", raw))
                                  or re.match(r"^\s*[-*+]\s*~~", raw))
                if in_settled:
                    continue
            elif in_settled and raw.startswith((" ", "\t")) and raw.strip():
                continue
            elif not raw.strip():
                in_settled = False
            _clean = re.sub(r"`[^`]*`", "", raw)
            m = PLATE_HISTORY_RE.search(_clean)
            if m and _clean[m.start():].split(".")[0].rstrip().endswith("?"):
                m = None
            if m:
                hits.append((e["line"] + off, m.group(0)[:22]))
        if hits:
            findings.append(Finding(
                "E-PLATEHISTORY", e["line"],
                "%s is on the human's plate but carries %d line(s) of reasoning HISTORY, so the "
                "decision the human has to make is buried in how it got here" % (e["id"], len(hits)),
                "a §2 entry answers two questions and stops - what do you need to decide, and "
                "the minimum needed to decide it, plus a recommendation. Move the history to a "
                "finding and leave a pointer. At: %s"
                % ", ".join("L%d(%s)" % (n, w) for n, w in hits[:4])))

    _live = without_attestations(text)
    if "TODO: one paragraph" in _live:
        _ln = next((i + 1 for i, l in enumerate(_live.split("\n"))
                    if "TODO: one paragraph" in l), 1)
        findings.append(Finding(
            "E-STUBLEFT", _ln,
            "the scaffold's Purpose placeholder is still here, so the doc opens by telling "
            "its reader it was never finished",
            "one paragraph, authored - what this orchestrator is FOR. It is the only part of "
            "the spine that cannot be generated, which is why it is the part that gets left"))

    _span1 = section_span(lines, "1") if _wants("E-EMPTYLINKS") else None
    if _span1:
        _body1 = [l for l in lines[_span1[0] + 1:_span1[1]]
                  if l.strip() and not re.fullmatch(r"_.*_", l.strip())]
        try:
            # A note (a quoted line) and the title of a Reader file-note entry cite a file the
            # person pointed at; they are not the doc's own list of what it owns.
            _assets = harvest_assets(
                [l for l in lines if not l.lstrip().startswith(">")
                 and "Notes on the file " not in l], path)
        except Exception:
            _assets = {}
        _real = {k: v for k, v in _assets.items() if v.get("exists") is not False}
        if not _body1 and _real:
            findings.append(Finding(
                "E-EMPTYLINKS", _span1[0] + 1,
                "§1 is empty while this doc cites %d resolvable asset(s) in its own prose, so "
                "it claims to list every doc and URL this orchestrator owns and lists none"
                % len(_real),
                "orchdoc.py links --doc <doc> harvests them and proposes the table - §1 is a "
                "COLLECTION task, not an authoring one"))

    _SHAPE_OK = re.compile(r"^[A-Z]{1,3}\d+$")
    _order = {}
    for e in entries:
        eid = e["id"]
        if not _SHAPE_OK.match(eid):
            findings.append(Finding(
                "E-IDSHAPE", e["line"],
                "'%s' is not the canonical id form, so the workspace has several ways to name "
                "the same kind of thing" % eid,
                "ids are PREFIX + NUMBER then a summary in the title: "
                "`D9907 - pick a layout`, not `D-PAUSE`. There is no automated rename, on purpose: "
                "the references live in other orchestrators' docs, so renaming is a "
                "cross-doc change one doc cannot safely finish. Propose the new id to the "
                "docs that cite it, then edit both sides together."))
            continue
        m = re.match(r"^([A-Z]{1,3})(\d+)$", eid)
        _order.setdefault(((e.get("section") or "?"), m.group(1)), []).append(
            (e["line"], int(m.group(2)), eid))

    for (sec, pre), items in sorted(_order.items()):
        items.sort()
        for (ln_a, n_a, id_a), (_ln_b, n_b, id_b) in zip(items, items[1:]):
            if n_b < n_a:
                findings.append(Finding(
                    "E-IDORDER", _ln_b if False else items[items.index(
                        (_ln_b, n_b, id_b))][0],
                    "%s appears after %s in '%s', so the numbers do not run in order and the "
                    "section has to be scanned rather than read to the right place"
                    % (id_b, id_a, str(sec)[:34]),
                    "orchdoc.py reorder --doc <doc> sorts each section; it moves entries "
                    "whole and verifies the bytes are unchanged"))

    # --- E-CONFLICT: unresolved merge-conflict markers ---
    #
    # A doc containing "<<<<<<<" / "=======" / ">>>>>>>" has TWO versions of some entry in
    # it and no one has chosen. Every status the tool then reports is drawn from whichever
    # side happened to come first - a confident answer computed from an unresolved file.
    for i, ln in enumerate(lines):
        if re.match(r"^(<{7}|>{7})\s", ln) or re.match(r"^={7}$", ln):
            findings.append(Finding(
                "E-CONFLICT", i + 1,
                "unresolved merge-conflict marker - this doc holds two versions and "
                "every status read from it is arbitrary",
                "resolve the conflict before trusting anything in this file"))
            break

    for num, title, note in (SCHEMA_SECTIONS if _wants("W-EMPTYPROMISE") else ()):
        if num != "1":
            continue
        sp = section_span(lines, num)
        if not sp:
            continue
        body = [l for l in lines[sp[0] + 1:sp[1]]
                if l.strip() and not re.fullmatch(r"_.*_", l.strip())]
        if body:
            continue
        cited = harvest_assets(lines, path)
        if len(cited) >= 3:
            findings.append(Finding(
                "W-EMPTYPROMISE", sp[0] + 1,
                "§1 promises 'every doc and URL this orchestrator owns' and is EMPTY, "
                "while this doc cites %d asset(s) elsewhere" % len(cited),
                "run `orchdoc.py links --doc <doc>` - it harvests them and prints a "
                "paste-ready table; §1 is a COLLECTION task, not an authoring one"))

    own_ids = {e["id"] for e in entries}
    in_fence = False
    for i, ln in enumerate(lines_live):
        if ln.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or ln.lstrip().startswith("<!--"):
            continue                      # generated audit records are not claims
        probe = re.sub(r"`[^`]*`", "", ln)
        probe = re.sub(r"[\"“‘'][^\"”’']{0,90}[\"”’']", "", probe)
        for m in re.finditer(
                r"\*{0,2}(?<![A-Z][A-Z]-)([A-Z]{1,3}[-]?\d{1,3})\*{0,2}[^|\n]{0,18}?\(?\bline\s+(\d{1,4})\b",
                probe):
            eid, target = m.group(1), int(m.group(2))
            if eid not in own_ids:
                continue                  # a table row label is not an anchor
            span = probe[m.start():m.end()]
            if ARTIFACT_RE.search(span):
                continue                  # a reference into ANOTHER artifact
            if not (1 <= target <= len(lines)):
                findings.append(Finding(
                    "W-BADLINEREF", i + 1,
                    "points at line %d, past the end of the doc (%d lines)"
                    % (target, len(lines)),
                    "cite the heading; a line number rots on any edit above it"))
                continue
            window = "\n".join(lines[max(0, target - 2):target + 2])
            if not re.search(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(eid), window):
                actual = [j + 1 for j, l2 in enumerate(lines)
                          if l2.startswith("#") and re.search(
                              r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(eid), l2)]
                findings.append(Finding(
                    "W-BADLINEREF", i + 1,
                    "says '%s ... line %d', but line %d does not contain %s%s"
                    % (eid, target, target, eid,
                       " (it is at line %d)" % actual[0] if actual else ""),
                    "name the heading instead - a line number rots on any edit above it"))

    h1s, _fence = [], False
    for i, l in enumerate(lines):
        if l.lstrip().startswith("```"):
            _fence = not _fence
            continue
        if not _fence and l.startswith("# "):
            h1s.append(i)
    if len(h1s) > 1:
        findings.append(Finding(
            "E-ONEH1", h1s[1] + 1,
            "%d H1 headings; a second reads as a second DOCUMENT title" % len(h1s),
            "put the role in the same heading: %s" % canonical_title("oN", "(role)")))

    nums = []
    for ln in lines:
        m = SECTION_RE.match(ln)
        if m:
            nums.append(m.group(1))
    if not nums and re.search(r"ORCHESTRATOR-DECISIONS-", os.path.basename(str(path))):
        findings.append(Finding(
            "E-LEGACYDOC", 1,
            "this doc has NO §-numbered sections, so the schema invariants cannot run on it - "
            "it is not passing them, it is exempt from them",
            "orchdoc.py migrate --doc <doc> brings it onto the spine without losing content; "
            "until then most of what `check` reports about other docs is silent about this one"))

    if nums:                      # only judge docs that have opted into the schema
        want = [n for n, _t, _d in SCHEMA_SECTIONS]
        missing = [n for n in want if n not in nums and n not in OPTIONAL_SECTIONS]
        if missing:
            findings.append(Finding(
                "E-SCHEMA", 0,
                "missing schema section(s): %s" % ", ".join(missing),
                "orchdoc.py scaffold --doc <doc> writes the canonical spine"))
        # ORDER is checked across EVERY numbered section, including custom ones - that is
        # what makes "COMPLETED is at the bottom" true rather than merely intended, since
        # 99 sorts below anything an orchestrator adds in 6 to 98.
        ordered = list(nums)
        if ordered != sorted(ordered, key=lambda x: [int(p) for p in x.split(".")]):
            findings.append(Finding(
                "E-SCHEMA", 0,
                "schema sections are out of canonical order",
                "a numbered spine is only an anchor if the numbers ascend"))

    kinds = defaultdict(set)
    for e in entries:
        if e.get("archived"):
            continue
        m = re.match(r"^([A-Z]+)", e["id"])
        if not m:
            continue
        sec = e["section"]
        sm = SECTION_RE.match(sec if sec.lstrip().startswith("#") else "## " + sec)
        if sm and sm.group(1).split(".")[0] == ARCHIVE_SECTION:
            continue                      # archived-by-schema: not part of the live group
        kinds[m.group(1)[0]].add(sec)
    for prefix, label in KIND_ORDER:
        secs = kinds.get(prefix, set())
        if len(secs) > 1:
            findings.append(Finding(
                "E-SCATTERED", 0,
                "'%s' entries are spread across %d sections, so a human cannot find "
                "them by scrolling to one place" % (prefix, len(secs)),
                "put them all under one section (%s): %s"
                % (label, "; ".join(sorted(s[:38] for s in secs)))))

    _span, _why = plate_span(lines)
    if _why:
        findings.append(Finding("E-BADMARKER", 0, _why,
                                "readers cannot agree where derived content begins"))
    elif _span:
        try:
            start, stop = _span
            rendered = lines[start:stop + 1]
            expected = build_plate_block([dict(e, body=e.get("raw_body", e["body"]))
                                          for e in entries], lines)
            if rendered != expected:
                findings.append(Finding(
                    "E-PLATEDRIFT", start + 1,
                    "the generated index does not match what regeneration produces",
                    "hand-edited or stale; run `orchdoc.py plate --doc <doc>`"))
        except ValueError:
            pass

    for e in entries:
        if e.get("archived") and not e.get("id_demoted"):
            findings.append(Finding(
                "E-ARCHIVEDMARKER", e["line"],
                "archived entry still carries the live-looking id '%s' at the FRONT of its "
                "heading" % e["id"],
                "move the id into the archive suffix - `_(was %s - RESOLVED)_` - rather than "
                "deleting it; the id must stay readable or every commit trailer naming this "
                "entry becomes a dangling pointer" % e["id"]))

    for i, raw in enumerate(lines, start=1):
        m = re.match(r"^#{1,6}\s+(.*)$", raw)
        if not m:
            continue
        _suf = ARCHIVED_ID_RE.search(m.group(1))
        _lead = ID_RE.match(strip_decoration(m.group(1)))
        if _suf and _lead and _lead.group(1) != _suf.group(1):
            findings.append(Finding(
                "E-ARCHIVEDMARKER", i,
                "this heading names TWO entries - '%s' at the front and '%s' in its archive "
                "suffix - so the parser reads it as a live '%s' and '%s' resolves to nothing"
                % (_lead.group(1), _suf.group(1), _lead.group(1), _suf.group(1)),
                "the archived id is the real one. Lead the title with its status word - "
                "`### %s - %s` - so the front of the heading cannot parse as an id"
                % (status_of("\n".join(lines[i - 1:i + 6])) or "DONE", m.group(1)[:40])))

    # --- E-SELFCLAIM: a heading must not make a claim about its own contents ---
    for s in sections:
        hit = False
        for pat, why in SELF_CLAIM_PATTERNS:
            if pat.search(s["title"]):
                findings.append(Finding(
                    "E-SELFCLAIM", s["line"],
                    "section heading %s" % why,
                    s["title"][:100]))
                hit = True
                break
        if hit:
            continue
        parts = HEADING_SPLIT_RE.split(strip_decoration(s["title"]), maxsplit=1)
        if len(parts) > 1:
            tail = parts[1]
            lead = " ".join(tail.split()[:2])
            m = (HEADING_STATE_RE.search(lead) or HEADING_COUNT_RE.search(tail)
                 or HEADING_DATED_CLAIM_RE.search(s["title"]))
            if m:
                findings.append(Finding(
                    "E-SELFCLAIM", s["line"],
                    "section heading asserts a state or count ('%s'), which drifts silently"
                    % m.group(0),
                    s["title"][:100]))

    # --- Per-line scans, skipping fenced code ---
    sha_candidates = []
    in_fence = False
    for i, raw in enumerate(lines_live, start=1):
        if raw.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue


        m = LINE_CITE_RE.search(raw)
        if m and "line" in m.group(0).lower():
            findings.append(Finding(
                "W-LINECITE", i, "citation by line number rots on any edit above it",
                raw.strip()[:90]))

        # A hex string the surrounding words identify as a content hash is not a commit
        # citation at all, so it is neither a rot risk nor a dead pointer. Classify by
        # context, never by shape alone.
        if NOT_A_COMMIT_RE.search(raw):
            continue
        for sm in SHA_CITE_RE.finditer(raw):
            tok = sm.group(1)
            if len(tok) >= 7 and SHA_HAS_LETTER.search(tok):
                sha_candidates.append((i, tok))
                break  # one candidate per line is enough

    if sha_candidates and _wants("E-DEADREF", "W-CLOUDREF", "W-SHACITE", "E-REFLOOKUP"):
        toks = [t for _, t in sha_candidates]
        resolved = [False] * len(toks)
        settled = False
        lookup_failed = []
        # Retries are shared by the whole batch, not given per store. git_stdin waits 60 seconds
        # and the machine can be at 100% CPU, so this cap holds them to 2 minutes added in all.
        retries = 2
        for group in object_store_groups([r for r in citable_repos() if (r / ".git").exists()]):
            pending = [i for i, ok in enumerate(resolved) if not ok]
            if not pending:
                break
            probe = "\n".join("%s^{commit}" % toks[i] for i in pending) + "\n"
            failure = None
            for repo in group[:3]:
                while True:
                    rc, out, err = git_stdin(["cat-file", "--batch-check"], probe, cwd=repo)
                    rows = out.splitlines()
                    no_repo = "not a git repository" in err or "invalid gitfile" in err
                    if len(rows) == len(pending) or no_repo or retries <= 0:
                        break
                    retries -= 1
                if len(rows) != len(pending):
                    # A lone folder git says is no repo (say a worktree whose admin dir was removed)
                    # holds nothing and never recovers, so it must not turn a dead ref into "try
                    # again". A checkout of a store others share says nothing about that store.
                    if not no_repo or len(group) > 1:
                        failure = "%s: %s" % (repo.name, (err or "rc=%s, %d of %d rows"
                                                          % (rc, len(rows), len(pending)))[:80])
                    continue
                settled = True
                for i, ln in zip(pending, rows):
                    if "missing" not in ln:
                        resolved[i] = True
                break
            else:
                if failure:
                    lookup_failed.append(failure)
        if not settled:
            resolved = [True] * len(toks)  # cannot settle it: do not accuse
        _limits = ref_settle_limits() if (cloud_mode() and not all(resolved)) else ""
        for (lineno, tok), ok in zip(sha_candidates, resolved):
            if ok:
                findings.append(Finding(
                    "W-SHACITE", lineno,
                    "citation by commit SHA rots on rebase; cite the commit SUBJECT",
                    tok))
            elif _limits:
                findings.append(Finding(
                    "W-CLOUDREF", lineno,
                    "cited commit resolves in no repo reachable from here - NOT reported "
                    "as dead, because this environment cannot settle it",
                    "%s - %s" % (tok, _limits)))
            elif lookup_failed:
                findings.append(Finding(
                    "E-REFLOOKUP", lineno,
                    "could not check this cited commit: git failed in a repo that may hold "
                    "it - NOT reported as dead; run the check again",
                    "%s - %s" % (tok, "; ".join(lookup_failed)[:300])))
            else:
                findings.append(Finding(
                    "E-DEADREF", lineno,
                    "cited commit does not resolve - the pointer is already dead",
                    tok))

    for e in entries:
        st_any = status_of(e["body"])
        if st_any is not None and st_any not in VALID_STATUS:
            if not (DECISION_SECTION_RE.search(e["section"])
                    or DECISION_SECTION_RE.search(e["title"])):
                findings.append(Finding(
                    "E-BADSTATUS", e["line"],
                    "entry '%s' has Status '%s', which is not a status - it parses, so "
                    "every generated view silently DROPS this entry" % (e["id"], st_any),
                    "use one of: %s" % ", ".join(sorted(VALID_STATUS))))

    # --- E-NOSTATUS: decision entries need a machine-readable Status ---
    for e in entries:
        if DECISION_SECTION_RE.search(e["section"]) or DECISION_SECTION_RE.search(e["title"]):
            st = status_of(e["body"])
            if st is not None and st not in VALID_STATUS:
                findings.append(Finding(
                    "E-BADSTATUS", e["line"],
                    "entry '%s' has a Status field whose value '%s' is not a status - "
                    "it parses, so the gate passed it and the entry vanished from the "
                    "generated index" % (e["id"], st),
                    "use one of: %s" % ", ".join(sorted(VALID_STATUS))))
            elif st is None:
                findings.append(Finding(
                    "E-NOSTATUS", e["line"],
                    "decision entry '%s' has no machine-readable Status field" % e["id"],
                    "add a line reading:  %s" % STATUS_CANONICAL))

    _pic_reported = {}
    _guard_found = (copy_guard_findings(_copy_guard_doc, entries, lines, reported=_pic_reported)
                    if _wants("E-COPYBYLINK", "E-IMAGEBYPATH", "W-PASTEDRIFT",
                              "W-PICTURENOTSHOWN") else [])
    for e in (entries if _wants("E-PICTUREPAGE", "W-PICTURENOTSHOWN") else ()):
        if e.get("archived") or status_of(e["body"]) in TERMINAL_STATUS:
            continue
        try:
            _live = without_attestations(e["body"])
            _pages = [] if pictures_blocks(_live) else picture_pages(_live)
            _bare = pictures_not_shown(_live) if owner_is_human(e["body"]) else []
            _named = _pic_reported.get(e["line"], set())
            _bare = [(w, p) for w, p in _bare if os.path.normcase(str(p)) not in _named]
        except Exception as _e:
            sys.stderr.write("[orchdoc] E-PICTUREPAGE could not run on %s %s: %s\n"
                             % (path.name, e["id"], str(_e)[:160]))
            continue
        for _raw, _page, _pics in _pages[:1]:
            findings.append(Finding(
                "E-PICTUREPAGE", e["line"],
                "%s sends the human to a separate page of %d picture%s instead of showing them in "
                "the entry: %s" % (e["id"], len(_pics), "" if len(_pics) == 1 else "s", _raw),
                "put them in a **Pictures:** block; `orchdoc.py pictures <page>` prints it"))
        if _bare:
            findings.append(Finding(
                "W-PICTURENOTSHOWN", e["line"],
                "%s names %d picture%s for the human by path without showing %s: %s"
                % (e["id"], len(_bare), "" if len(_bare) == 1 else "s",
                   "it" if len(_bare) == 1 else "them", _bare[0][0]),
                "show it: ![what it shows](path), or several in a **Pictures:** block"))

    for e in (entries if _wants("E-WORDSELSEWHERE") else ()):
        try:
            if not _words_elsewhere_applies(e):
                continue
            _hits = words_sent_elsewhere(without_attestations(e["body"]))
        except Exception as _e:
            sys.stderr.write("[orchdoc] E-WORDSELSEWHERE could not run on %s %s: %s\n"
                             % (path.name, e["id"], str(_e)[:160]))
            continue
        if _hits:
            findings.append(Finding(
                "E-WORDSELSEWHERE", e["line"],
                "%s sends the human to a PR, branch or file to read words instead of quoting them: %s"
                % (e["id"], _hits[0][:200]),
                "quote the words in the entry, numbered, each labelled with where it shows; "
                "the PR or file may follow as the source. A document with its own Reader page is the exception: "
                "link that page instead"))

    if _wants("W-PICUNTRACKED"):
        try:
            _by_line = sorted(entries, key=lambda x: x["line"])
            for _i, _u, _rel in _untracked_embeds(PROJECTS, lines):
                _own = [x for x in _by_line if x["line"] - 1 <= _i]
                _eid = _own[-1]["id"] if _own else "<entry id>"
                _p = (Path(PROJECTS).resolve() / _rel)
                findings.append(Finding(
                    "W-PICUNTRACKED", _i + 1,
                    "%s embeds %s, a picture git neither tracks nor ignores, so it will not "
                    "land on main with this doc (only the hourly autosave ref holds it)"
                    % (_eid, _rel),
                    "%s; or keep the file where it is and commit it: git add %s"
                    % (_command_or_why(_doc_slug(_copy_guard_doc.name), _eid, _p), _rel)))
        except Exception as _e:
            sys.stderr.write("[orchdoc] W-PICUNTRACKED could not run on %s: %s\n"
                             % (_copy_guard_doc.name, str(_e)[:160]))

    if _wants("W-ORCHNOTE"):
        try:
            for _n in parse_notes("\n".join(lines)):
                if _ORCH_CALLER_RE.match(_n["who"]):
                    findings.append(Finding(
                        "W-ORCHNOTE", _n["line"],
                        "note %s is labelled from %s; notes are the human's, not a session's"
                        % (_n["nid"], _n["who"]),
                        "move any status into the entry body (ticked box or dated line), any "
                        "question into its own Q entry (`add --kind question`), then: "
                        "orchdoc.py note --doc %s --note %s --delete"
                        % (_copy_guard_doc.name, _n["nid"])))
        except Exception as _e:
            sys.stderr.write("[orchdoc] W-ORCHNOTE could not run on %s: %s\n"
                             % (_copy_guard_doc.name, str(_e)[:160]))

    findings.extend(_guard_found)

    try:
        findings.extend(wake_findings(path) if _wants("E-WAKESOTHERS") else ())
    except Exception as _e:
        sys.stderr.write("[orchdoc] E-WAKESOTHERS could not run on %s: %s\n"
                         % (path.name, str(_e)[:160]))

    try:
        findings.extend(prelabel_findings(path) if _wants("E-PRELABEL") else ())
    except Exception as _e:
        sys.stderr.write("[orchdoc] E-PRELABEL could not run on %s: %s\n"
                         % (path.name, str(_e)[:160]))

    try:
        findings.extend(buried_ask_findings(path) if _wants("E-BURIEDASK") else ())
    except Exception as _e:
        sys.stderr.write("[orchdoc] E-BURIEDASK could not run on %s: %s\n"
                         % (path.name, str(_e)[:160]))

    if _upkeep is not None and (CHECK_ONLY is None
                                or any(c.startswith("W-") for c in CHECK_ONLY)):  # the W- upkeep checks (orchdoc_upkeep.CODES) - all advisory, never blocking
        findings.extend(_upkeep.check(path, lines, entries, sys.modules[__name__]))
    findings.sort(key=lambda f: (f.line, f.code))
    return findings


def git(args, cwd=PROJECTS, keep_ws=False):
    cwd = PROJECTS if cwd is None else cwd
    try:
        p = subprocess.run(["git", "-C", str(cwd)] + args,
                           capture_output=True, text=True, errors="replace", timeout=60,
                           creationflags=flags())
        return p.returncode, (p.stdout if keep_ws else p.stdout.strip()), p.stderr.strip()
    except Exception as e:
        return 1, "", str(e)


#: Where this tool lives in the workspace, as `_refuse_if_tool_is_stale` compares it.
TOOL_PATH = ".shared/scripts/orchdoc.py"
_PARTS_REL = TOOL_PATH.rsplit("/", 1)[0] + "/" + PARTS_DIR_NAME


def _part_blobs(tool, ref):
    """[(path in the repo, this copy's blob, the ref's blob)] for each part the file at `tool`
    includes, in include order. A blob is None where that side has no such file. When the file
    includes no part, no git runs."""
    try:
        names = _INCLUDE_RE.findall(Path(tool).read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return []
    if not names:
        return []
    rels = ["%s/%s.py" % (_PARTS_REL, n) for n in names]
    files = [Path(tool).parent / PARTS_DIR_NAME / (n + ".py") for n in names]
    have = [f for f in files if f.is_file()]
    rc, out, _e = (git(["hash-object", "--path", rels[0]] + [str(f) for f in have],
                       cwd=PROJECTS) if have else (1, "", ""))
    mine = dict(zip(have, out.split())) if rc == 0 else {}
    rc, listing, _e = git(["ls-tree", "--full-name", ref, "--", _PARTS_REL + "/"],
                          cwd=PROJECTS)
    theirs = {}
    for ln in listing.splitlines() if rc == 0 else []:
        meta, _tab, path = ln.partition("\t")
        if len(meta.split()) == 3:
            theirs[path] = meta.split()[2]
    return [(r, mine.get(f), theirs.get(r)) for r, f in zip(rels, files)]


def _ref_has_parts(ref):
    """True when `ref` holds at least one part file, so updating this tool means its parts
    folder too."""
    rc, out, _e = git(["ls-tree", "--name-only", "--full-name", ref, "--", _PARTS_REL + "/"],
                      cwd=PROJECTS)
    return rc == 0 and any(ln.endswith(".py") for ln in out.splitlines())


def _refuse_if_tool_is_stale(ref=None, tool=None):
    """None when this copy of the tool may land; otherwise why not, and what to run instead.
    """
    tool = Path(tool or __file__).resolve()
    ref = ref or CANONICAL_REF
    rc1, mine, _e1 = git(["hash-object", "--path", TOOL_PATH, str(tool)], cwd=PROJECTS)
    rc2, tip, _e2 = git(["rev-parse", "--verify", "--quiet", "%s:%s" % (ref, TOOL_PATH)],
                        cwd=PROJECTS)
    if rc1 or rc2 or not mine or not tip:
        return None
    rel, what = TOOL_PATH, "this orchdoc.py"
    if mine == tip:
        for prel, pmine, ptip in _part_blobs(tool, ref):
            if pmine and ptip and pmine != ptip:
                rel, mine, tip = prel, pmine, ptip
                what = "this orchdoc.py's part %s" % prel.split("/", 2)[-1]
                break
        else:
            return None
    # Every blob this path has had on the ref, merges included. One call, ~0.4s.
    rc3, raw, _e3 = git(["log", "-m", "--format=", "--raw", "--no-abbrev", "--no-renames",
                         ref, "--", rel], cwd=PROJECTS)
    seen = set()
    for ln in raw.splitlines() if rc3 == 0 else []:
        bits = ln.split()
        if ln.startswith(":") and len(bits) >= 4:
            seen.update(bits[2:4])
    if mine not in seen:
        sys.stderr.write("[orchdoc] note: %s (%s) is on no commit of %s - a branch "
                         "copy, so it is not refused.\n" % (what, mine[:10], ref))
        return None
    tree = tool.parents[2]
    _rc, common, _e = git(["rev-parse", "--path-format=absolute", "--git-common-dir"],
                          cwd=PROJECTS)
    shared = Path(common).parent if common else None
    lines = ["%s is an OLDER version than the one on %s (this copy %s, %s has %s):"
             % (what, CANONICAL_REF, mine[:10], CANONICAL_REF, tip[:10]),
             "the landing code has changed since this copy was taken."]
    if shared and shared.resolve() != tree.resolve():
        lines.append("Run the shared checkout's copy instead:  python %s %s" % (
            (shared / TOOL_PATH).as_posix(), subprocess.list2cmdline(sys.argv[1:])))
        lines.append("or bring this tree's copy up to date:  git -C %s checkout %s -- %s"
                     % (tree.as_posix(), CANONICAL_REF, TOOL_PATH + (
                         " " + _PARTS_REL if rel != TOOL_PATH or _ref_has_parts(ref) else "")))
    else:
        lines.append("Bring the shared checkout up to date:  python %s --apply"
                     % (tree / ".shared" / "scripts" / "deliver.py").as_posix())
    return "\n".join(lines)


def behind_canonical(cwd, fetch=True):
    """How far the working copy at `cwd` is behind CANONICAL_REF.

    Returns (state, n, detail) where state is one of:
        "current"  - n == 0, the tree can be trusted to answer about the repository
        "behind"   - n > 0, every count taken from this tree is a count of old content
        "unknown"  - git could not answer: no repo, no remote, detached with no
                     canonical ref, or the fetch failed. NEVER a refusal.
    """
    rc, _, _ = git(["rev-parse", "--git-dir"], cwd=cwd)
    if rc != 0:
        return "unknown", 0, "not a git repository"

    fetched = None
    if fetch:
        remote = CANONICAL_REF.split("/", 1)[0]
        rcf, _, errf = git(["fetch", "--quiet", remote], cwd=cwd)
        fetched = (rcf == 0)
        if rcf != 0:
            # Offline, no remote configured, auth failure. Fall through and compare
            # against whatever ref is on disk, and SAY the ref may be old.
            fetched = False

    rc2, out, _ = git(["rev-list", "--count", "HEAD..%s" % CANONICAL_REF], cwd=cwd)
    if rc2 != 0 or not out.strip().isdigit():
        return "unknown", 0, "no %s to compare against" % CANONICAL_REF

    n = int(out.strip())
    detail = "%s%s" % (CANONICAL_REF,
                       "" if fetched is not False else " (NOT re-fetched - may itself be old)")
    return ("current" if n == 0 else "behind"), n, detail


def doc_holds_every_canonical_change(doc):
    """True when nothing landed in this one file is missing from the copy at `doc`.

    So all three must hold, compared as gate 1 compares (`_authored_form`, generated regions
    dropped, a line that grew still counts):
      1. every entry (id, title) on the ref is in the copy: another title is a collision
      2. every authored line on the ref is in the copy
      3. no line the ref removed since this checkout's merge-base is still in the copy
    Anything that cannot be read answers False, and `add` refuses as it always did.
    """
    try:
        doc = pathlib.Path(doc)
        rc, out, _ = git(["show", "%s:./%s" % (CANONICAL_REF, doc.name)], cwd=doc.parent)
        if rc != 0:
            return False
        mine_txt = doc.read_text(encoding="utf-8", errors="replace")
        head = re.compile(r"(?m)^#{3,4} [^\n]*?\b([A-Z]{1,3}\d+)\b - ([^\n]*)")
        if not set(head.findall(out)) <= set(head.findall(mine_txt)):
            return False

        def authored(text):
            lines = text.splitlines()
            spans, why = derived_spans(lines)
            if why:
                raise ValueError(why)
            forms = (None if not l.strip() or any(lo <= i <= hi for lo, hi in spans)
                     else _authored_form(l) for i, l in enumerate(lines))
            return [f for f in forms if f]
        mine, canon = authored(mine_txt), authored(out)
        joined = "\n".join(mine)
        if any(l not in joined for l in canon):
            return False
        rc, base, _ = git(["merge-base", "HEAD", CANONICAL_REF], cwd=doc.parent)
        if rc != 0:
            return False
        rc, old, _ = git(["show", "%s:./%s" % (base.strip(), doc.name)], cwd=doc.parent)
        if rc != 0:
            return True      # the file is new on the ref since the base: nothing was removed
        return not ((set(authored(old)) - set(canon)) & set(mine))
    except Exception:
        return False


def git_stdin(args, payload, cwd=PROJECTS):
    """git with stdin, for batch verification in a single process."""
    try:
        p = subprocess.run(["git", "-C", str(cwd)] + args, input=payload,
                           capture_output=True, text=True, timeout=60, creationflags=flags())
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except Exception as e:
        return 1, "", str(e)


def check_freshness(path, ref=CANONICAL_REF):
    """
    P5: the view a human opens must provably equal canonical state, or say it does not.
    """
    rel = os.path.relpath(str(path), str(PROJECTS)).replace("\\", "/")
    findings = []

    ok, why = fetch_ok()
    if not ok:
        return [Finding(
            "E-NOFETCH", 0,
            "cannot reach the remote, so freshness is UNKNOWN - not 'current'",
            why or "git fetch failed; refs/remotes may be a stale snapshot")], None

    rc, canon, _ = git(["show", "%s:%s" % (ref, rel)])
    if rc != 0:
        return [Finding("E-STALE", 0,
                        "doc does not exist on %s (untracked, or on another branch only)" % ref,
                        rel)], None

    try:
        local = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return [Finding("E-IO", 0, "cannot read: %s" % e)], None

    def _norm(t):
        return t.replace("\r\n", "\n").rstrip("\n")

    if _norm(local) == _norm(canon):
        return [], "identical to %s" % ref

    llines = local.splitlines()
    clines = canon.splitlines()
    _, behind, _ = git(["rev-list", "--count", "HEAD..%s" % ref])

    _, head_blob, _ = git(["rev-parse", "HEAD:%s" % rel])
    work_blob = ""
    try:
        # `--path rel`: git looks .gitattributes up by the path it is given, and HEAD's blob
        # was stored with the attributes of `rel`, not of an absolute path.
        p = subprocess.run(["git", "-C", str(PROJECTS), "hash-object", "--path", rel, str(path)],
                           capture_output=True, text=True, timeout=30, creationflags=flags())
        work_blob = p.stdout.strip()
    except Exception:
        pass

    if head_blob and work_blob and head_blob != work_blob:
        cause = ("UNCOMMITTED EDITS - this content exists ONLY in the working tree. "
                 "Commit it to %s; a shared tree is not storage." % ref)
    else:
        cause = ("the checkout is %s commits behind %s - the newer content is safe on "
                 "the canonical ref, but anyone reading this file sees the older one"
                 % (behind or "?", ref))

    findings.append(Finding(
        "E-STALE", 0,
        "working-tree copy DIFFERS from %s (local %d lines, canonical %d lines)"
        % (ref, len(llines), len(clines)),
        cause))
    return findings, None


def report(path, findings, quiet=False, strict=False):
    """
    Print one doc's result. BLOCKING first and in full; advisory as one summary line.
    """
    name = path.name
    counts = defaultdict(int)
    for f in findings:
        counts[f.code] += 1
    blocking = [f for f in findings if f.code in BLOCKING or strict]
    advisory = [f for f in findings if f.code not in BLOCKING and not strict]

    if not findings:
        if not quiet:
            print("  [OK]    %s" % name)
        return 0

    if blocking:
        print("  [BLOCK] %s  -  %d blocking" % (name, len(blocking)))
        _nn_shown = 0
        for f in blocking:
            if f.code == "W-PRODUCTNONEEDLE":
                _nn_shown += 1
                if _nn_shown > PRODUCT_NONEEDLE_STRICT_CAP:
                    continue
            loc = ("line %d" % f.line) if f.line else "doc"
            print("          %-16s %-9s %s" % (f.code, loc, f.msg))
            if f.detail:
                print("          %-16s %-9s   -> %s" % ("", "", f.detail[:100]))
        if _nn_shown > PRODUCT_NONEEDLE_STRICT_CAP:
            print("          %-16s %-9s and %d more W-PRODUCTNONEEDLE entries"
                  % ("", "", _nn_shown - PRODUCT_NONEEDLE_STRICT_CAP))
        if any(f.code == "E-STALEPROSE" and "#" not in (f.detail or "") for f in blocking):
            print("          note: an E-STALEPROSE above cites a FILE with no `#needle`, so ANY")
            print("                commit to that file restales the entry. You CAN narrow it:")
            print("                     **Depends:** `path/to/doc.md#<id>`")
            print("                counts only commits touching a line naming that id.")
            print("          \u26d4 THE BACKTICKS GO ROUND THE PATH AND THEY ARE LOAD-BEARING.")
            print("                A citation outside backticks does not parse. It is not a")
            print("                narrowed edge and not a whole-file edge - it is NO edge, so")
            print("                the entry can never restale and nothing ever says so.")
            pass
            print("          ⛔ BUT WORK THE ENTRY FIRST, AND NARROW ONLY WHAT STAYS NOISY.")
            print("                a session worked nine of these instead of narrowing")
            print("                or overriding: SEVEN carried a sentence that had gone FALSE.")
            print("                A ~78% hit rate on a check their doc had overridden 183")
            print("                times. Narrowing a high-hit-rate entry trades away the")
            print("                fires that were right.")
        if any(f.code == "E-PICTUREPAGE" for f in blocking):
            print("          note: E-PICTUREPAGE - the human looks at pictures IN the entry, folded,")
            print("                never on a page of their own . Print")
            print("                the block to paste into the entry with:")
            print('                  python <scripts>/orchdoc.py pictures "<the page named'
                  ' above>" --what "<what they show>"')
            print("                A link to the page may stay beside the block.")
        print_copy_guard_remedies(blocking)
        print_wakes_refusal([f for f in blocking if f.code == "E-WAKESOTHERS"])
    else:
        print("  [warn]  %s  -  nothing blocking" % name)

    if advisory:
        adv = defaultdict(int)
        for f in advisory:
            adv[f.code] += 1
        print("          advisory (not blocking): %s"
              % ", ".join("%s x%d" % (c, n) for c, n in sorted(adv.items())))
        print("          %d advisory finding(s) have LINE NUMBERS - see them with:  "
              "orchdoc.py check --doc <doc> --strict" % len(advisory))
        _cloudref = [f for f in advisory if f.code == "W-CLOUDREF"]
        if _cloudref:
            print("          note: %d citation(s) were downgraded from E-DEADREF because THIS"
                  % len(_cloudref))
            print("                environment cannot settle them, not because they are alive:")
            print("                     %s" % (_cloudref[0].detail.split(" - ", 1)[-1]))
            print("                They still block on a full checkout, where the sibling repos and")
            print("                the full history are present. A cloud session cannot clear")
            print("                them and must not pretend it did.")
        if adv.get("W-PRODUCTNONEEDLE"):
            print("          %d entries cite product files with no #needle - staleness cannot"
                  " be tracked for them" % adv["W-PRODUCTNONEEDLE"])

    return 1 if blocking else 0


def cmd_check(args):
    docs = resolve_docs(args)
    if not docs:
        print("no OrchDocs found", file=sys.stderr)
        return 2
    global CHECK_ONLY
    only = getattr(args, "codes", None)
    CHECK_ONLY = ({c.strip() for c in only.split(",") if c.strip()} or None) if only else None
    print("orchdoc check - %d doc(s)%s%s" % (
        len(docs), "  [STRICT]" if args.strict else "",
        "  [CODES %s]" % ",".join(sorted(CHECK_ONLY)) if CHECK_ONLY else ""))
    worst = 0
    totals = defaultdict(int)
    for d in docs:
        f = check_doc(d)
        if CHECK_ONLY is not None:
            f = [x for x in f if x.code in CHECK_ONLY]
        for x in f:
            totals[x.code] += 1
        worst |= report(d, f, quiet=args.quiet, strict=args.strict)

    if CHECK_ONLY is not None:
        _bad_local, _bad_all = {}, {}
    else:
        _, _bad_local = touches_since(None, None, rev="%s..HEAD" % CANONICAL_REF)
        _, _bad_all = touches_since(None, None)
    _unq = {t: v for t, v in _bad_local.items() if "unqualified" in v[2]}
    _old = {t: v for t, v in _bad_all.items()
            if "unqualified" in v[2] and t not in _unq}
    if _unq:
        print()
        print("  [BLOCK] %d UNLANDED commit trailer(s) name an entry without saying WHICH"
              % len(_unq))
        print("          doc, so they update nothing. Entry ids are PER-DOC: several docs")
        print("          all have a D9901. These are still amendable - fix before pushing.")
        for t, (when, subject, _why) in sorted(_unq.items()):
            print("            Touches: %-6s -> say o<N>:%-6s  (%s)"
                  % (t, t, subject[:52]))
        worst = 1
    if _old:
        print()
        print("  [note]  %d unqualified trailer(s) in LANDED history, unfixable without"
              % len(_old))
        print("          rewriting shared history. Reported, not blocking.")
        for t, (when, subject, _why) in sorted(_old.items()):
            print("            %s  %s  (%s)" % (when[:10], t, subject[:52]))

    if PRODUCT_NOTES:
        print()
        print("  [note]  product-repo citations not checked for staleness:")
        for (doc, label, why), n in sorted(PRODUCT_NOTES.items()):
            print("            %s: %s - %s (%d cited path%s)"
                  % (doc, label, why, n, "" if n == 1 else "s"))

    blk = {c: n for c, n in totals.items() if c in BLOCKING}
    adv = {c: n for c, n in totals.items() if c not in BLOCKING}
    _orphans = untracked_orchdocs()
    if _orphans:
        print("\n⛔ ON NO REF AT ALL - the human reads a file git has never seen:")
        for _o in _orphans:
            print("     %s" % _o)
        print("   Land it through this tool. Until then no freshness check can see it,")
        print("   and every anchor you give the human points into a file that may lack the entry.")

    print("\nBLOCKING: " + (", ".join("%s=%d" % (c, n) for c, n in sorted(blk.items()))
                            or "none"))
    print("ADVISORY: " + (", ".join("%s=%d" % (c, n) for c, n in sorted(adv.items()))
                          or "none"))
    print("\ngate %s" % ("REFUSES" if worst else "PASSES"))
    return worst


def cmd_freshness(args):
    docs = resolve_docs(args)
    print("orchdoc freshness - canonical ref %s" % CANONICAL_REF)
    worst = 0
    for d in docs:
        f, ok = check_freshness(d)
        if ok:
            print("  [OK]   %s  %s" % (d.name, ok))
        else:
            worst |= report(d, f)
    return worst


def resolve_docs(args):
    if getattr(args, "doc", None):
        return [resolve_doc_arg(args.doc)]
    return sorted(PROJECTS.glob("ORCHESTRATOR-DECISIONS-*.md"))


def cmd_selftest(args):
    """Synthetic fixtures. Each must produce exactly the code it is built to trip."""
    import tempfile
    try:
        import git_guard
    except ImportError:
        pass
    _ws_docs = sorted(p.name for p in Path(_find_workspace()).glob("ORCHESTRATOR-DECISIONS-o*.md"))
    _needle_doc = ("ORCHESTRATOR-DECISIONS-o910.md" if "ORCHESTRATOR-DECISIONS-o910.md" in _ws_docs
                   else (_ws_docs[0] if _ws_docs else None))
    cases = [
        ("E-DUPID",
         "## DECISIONS\n\n### D9901 - first\n**Status:** OPEN\n\ntext\n\n"
         "### D9901 - same id again\n**Status:** RESOLVED\n\ntext\n"),
        ("E-SELFCLAIM", "## DECISIONS - none open\n\nnothing here\n"),
        ("E-HIDDENENTRY",
         "## DECISIONS\n\n### D9901 - a live one\n**Status:** OPEN\n\ntext\n\n"
         "## §99 ARCHIVE\n\n```\n"
         "### DONE - swallowed by the fence above  _(was D9902 - RESOLVED)_\n"
         "**Status:** RESOLVED\n\nthis entry is in the file and reachable by nothing\n"
         "```\n"),
        ("E-CLOSEDWITHOPENSUBS",
         "## DECISIONS\n\n### D9904 - ship the three lanes\n**Status:** RESOLVED\n\n"
         # The settled ones carry the canonical checked-checkbox form. A fixture is read as an
         # EXAMPLE of correct markup, so one written in a superseded form teaches it.
         "- [x] lane A merged\n- [x] lane B merged\n- lane C NOT DONE\n"),
        ("E-WRONGSECTION",
         "## §3 ON CLAUDE'S PLATE\n\n### T9901 - the orchestrator's own work\n"
         "**Status:** OPEN - **Owner:** o99\n\nbody\n"),
        ("E-LOOSEINPARENT",
         "## §2 LIVE ON THE PLATE\n\nItems live in §2.1 below, never loose here.\n\n"
         "### D9904 - a decision parked in the container\n**Status:** OPEN\n\nbody\n\n"
         "## §2.1 Decisions\n\n### D9905 - correctly placed\n**Status:** OPEN\n\nbody\n"),
        ("E-PLATEHISTORY",
         "## §2.1 Decisions\n\n### D9903 - pick a tier\n**Status:** OPEN\n\n"
         "- [x] the price was set\n"
         "This entry previously said $25, which was wrong; o97 caught it and it is now corrected.\n"),
        ("E-LEGACYDOC",
         "# Decisions\n\n## Open items\n\n### D9901 - a decision\n**Status:** OPEN\n\nbody\n"),
        ("E-STUBLEFT",
         "## PURPOSE\n\n_TODO: one paragraph - what this orchestrator is for. "
         "Authored, never generated._\n"),
        # An empty §1 in a doc that cites assets elsewhere. The subtitle promises "every doc and
        # URL this orchestrator owns", so empty is a false claim rather than a blank.
        ("E-EMPTYLINKS",
         "## §1 LINKS AND DOCS\n\n_every doc and URL this orchestrator owns_\n\n"
         "## §4 FINDINGS\n\n### F9901 - a finding\n**Status:** CONFIRMED\n\n"
         "See `docs/plan.md` and https://example.com/dashboard for detail.\n"),
        ("E-ALLSUBSDONE",
         "## DECISIONS\n\n### D9903 - ship the lanes\n**Status:** OPEN\n\n"
         "- [x] lane A - ✅ DONE\n"
         "- [x] lane B - ✅ DONE\n"),
        ("E-IDSHAPE",
         "## DECISIONS\n\n### D-PAUSE - pick a layout\n**Status:** OPEN\n\nbody\n"),
        # ...and numbers that run in order, so a reader stops when they arrive rather than
        # scanning to the end of the section to be sure.
        ("E-IDORDER",
         "## DECISIONS\n\n### D9905 - later\n**Status:** OPEN\n\nbody\n\n"
         "### D9902 - earlier\n**Status:** OPEN\n\nbody\n"),
        ("E-SETTLEDNOTSTRUCK",
         "## DECISIONS\n\n### D9906 - classification codification\n**Status:** OPEN\n\n"
         "- (a) the four drafts - ✅ **leave as is**\n"
         "- (b) two files - ✅ **move, keep as-is**\n"
         "- (c) wording - o98 drafts it and brings it to the human\n"),
        ("E-ARCHIVEDMARKER",
         "## DECISIONS\n\n### D9901 - live\n**Status:** OPEN\n\nbody\n\n"
         "<details><summary>Original D9901 wording (superseded)</summary>\n\n"
         "### D9901 - old wording\n**Status:** RESOLVED\n\nold body\n\n</details>\n"),
        ("W-LINECITE", "## NOTES\n\nSee D9908, line 82 for detail.\n"),
        # A SHA from a repo that is actually PRESENT, so it resolves wherever this runs.
        # A literal SHA is environment-dependent by construction: it resolves in the repo
        # it was copied from and nowhere else, so this fixture inverted to E-DEADREF on
        # every fresh clone and `selftest` - the command new users are told to run first -
        # failed for all of them. The distinction W-SHACITE tests is "resolves vs does
        # not", which means the fixture has to supply one that does.
        ("W-SHACITE", "## NOTES\n\nFixed in commit `%s` yesterday.\n" % _self_sha()),
        ("E-NOSTATUS", "## DECISIONS\n\n### D9909 - a decision with no status field\n\nbody\n"),
        ("E-DEADREF", "## NOTES\n\nLanded in `deadbeef1234567` last week.\n"),
        ("E-STALEPROSE",
         "## DECISIONS\n\n### D9901 - the verdict\n"
         "**Status:** OPEN - **Reviewed:** 2026-08-01\n"
         "**Depends:** F9902\n\n10 of 12 clear the floor.\n\n"
         "## FINDINGS\n\n### F9902 - the measurement\n"
         "**Status:** CONFIRMED - **Recorded:** 2026-08-06\n\n0 of 13 clear it.\n"),
        ("E-MIXEDSTATE",
         "## DECISIONS\n\n### D9901 - chain\n**Status:** OPEN - **Owner:** the human\n\n"
         "- the full chain has **NEVER run in production**. ✅ **RECON DONE** - "
         "the first path is **PROVEN 3x**.\n"
         "- ⏳ **NOT DONE** - o91 owns: a real import has never run\n"),
        ("E-NOOWNER",
         "## DECISIONS\n\n### D9901 - needs someone\n**Status:** OPEN\n\nbody\n"),
        ("E-DONEINACTIVE",
         "## DECISIONS - need your call\n\n### D9901 - already decided\n"
         "**Status:** RESOLVED - **Owner:** the human\n\nbody\n"),
        ("W-NOBLOCKER",
         "## ON CLAUDE'S PLATE\n\n### W9901 - blocked on nothing anyone can name\n"
         "**Status:** BLOCKED - **Owner:** o99 - **Opened:** 2026-08-01\n\nbody\n"),
        ("W-NOPROV",
         "## DECISIONS - need your call\n\n"
         "### D9901 - ruled, and the work it deferred is still owed\n"
         "**Status:** RESOLVED - **Owner:** the human - **Opened:** 2026-08-01\n\n"
         "- [ ] revisit the tax line as a CR test\n"),
        ("E-DONEBUTOPEN",
         "## DECISIONS - need your call\n\n### D9901 - the work landed, the entry did not move\n"
         "**Status:** OPEN - **Owner:** the human - **Opened:** 2026-08-01\n\n"
         "**Done-when:** cmd:python -c \"pass\"\n\nbody\n"),
        # A done item must be visibly marked. The heading marker is
        # DERIVED from the Status field, so the two can never disagree.
        ("E-MARKERDRIFT",
         "## FINDINGS\n\n### F9901 - no marker on the heading\n"
         "**Status:** CONFIRMED - **Owner:** o99\n\nbody\n"),
        ("E-NEEDLESPACE",
         "## FINDINGS\n\n### F9901 - a needle with a space\n"
         "**Status:** CONFIRMED - **Owner:** o99\n\n"
         "narrowed to `%s#W9906 -` which parses as nothing\n"
         % (_needle_doc or "ORCHESTRATOR-DECISIONS-o910.md")),
        ("E-BADTOUCH", None),
        ("E-NOFETCH", None),
        # A git lookup that failed in one repo; driven both ways in _selftest_ref_lookup.
        ("E-REFLOOKUP", None),
        # A new line naming an id that ANOTHER doc cites - needs a real repo holding both docs,
        # so it is built in the loop below rather than written as a single document.
        ("E-WAKESOTHERS", None),
        ("E-PRELABEL", None),
        ("E-BURIEDASK", None),
        ("E-WORDSELSEWHERE", None),
        ("E-AMBIGUOUSDATE",
         "## DECISIONS\n\n### D9901 - verdict\n"
         "**Status:** OPEN - **Reviewed:** 2026-08-05\n**Depends:** F9902\n\nbody\n\n"
         "## FINDINGS\n\n### F9902 - input\n"
         "**Status:** CONFIRMED - **Recorded:** 2026-08-05\n\nbody\n"),
        ("E-BADMARKER",
         "# Doc\n\n" + PLATE_BEGIN + "\n\n| ID | x |\n\n"
         "## DECISIONS\n\n### D9901 - hand-authored, below an UNCLOSED marker\n"
         "**Status:** OPEN\n\nbody\n"),
        ("E-NODEPS",
         "## DECISIONS\n\n### D9901 - a ruled decision resting on nothing declared\n"
         "**Status:** RESOLVED - **Owner:** the human\n\n"
         "**Resolved 2026-08-06:** the human ruled B.\n\nbody\n"),
        ("E-RUBBERSTAMP",
         "## DECISIONS\n\n### D9901 - the verdict\n"
         "**Status:** OPEN - **Reviewed:** 2026-08-05 - still current\n"
         "**Depends:** F9902\n\nbody\n\n"
         "## FINDINGS\n\n### F9902 - an input\n"
         "**Status:** CONFIRMED - **Recorded:** 2026-08-01\n\nbody\n"),
        ("E-FUTUREDATE",
         "## DECISIONS\n\n### D9901 - a ruling\n"
         "**Status:** RESOLVED - **Owner:** the human - **Attested-by:** o99 at 2099-01-01T00:00:00-07:00 - checked it\n"
         "**Depends:** F9902\n\nbody\n\n"
         "## FINDINGS\n\n### F9902 - an input\n"
         "**Status:** CONFIRMED - **Recorded:** 2026-08-01\n\nbody\n"),
        ("W-BADLINEREF",
         "# t\n\n\n\n\n\n| blocking | **A999** (line 2) |\n\n## DECISIONS\n\n"
         "### A999 - the real location\n**Status:** OPEN - **Owner:** the human\n\nbody\n"),
        ("W-ORCHNOTE",
         "## DECISIONS\n\n### D9901 - a decision\n**Status:** OPEN - **Owner:** the human\n\nbody\n\n"
         + render_note("D9901.n1", "o97", "2026-10-24 10:00", "a status from a session") + "\n"),
        ("E-CONFLICT",
         "## DECISIONS\n\n### D9901 - a decision\n<<<<<<< HEAD\n**Status:** OPEN\n"
         "=======\n**Status:** RESOLVED\n>>>>>>> other\n\nbody\n"),
        ("E-BADID",
         "## DECISIONS\n\n### D-1 - an id that does not parse\n"
         "**Status:** OPEN - **Owner:** someone\n\nbody\n"),
        ("E-ONEH1",
         "# Orchestrator Decision Doc - o99\n# (a role)\n\n## \u00a71 LINKS AND DOCS\n\nx\n"),
        ("E-TITLE",
         "# Some Other Heading\n\n## \u00a71 LINKS AND DOCS\n\nstuff\n"),
        ("E-SCHEMA",
         "# Doc\n\n## \u00a71 LINKS AND DOCS\n\nstuff\n\n"
         "## \u00a72 LIVE ON THE HUMAN'S PLATE\n\n### D9901 - a call\n**Status:** OPEN\n\nbody\n"),
        ("E-SCATTERED",
         "## DECISIONS\n\n### D9901 - here\n**Status:** OPEN\n\nbody\n\n"
         "## FINDINGS\n\n### F9901 - a finding\n**Status:** RECORDED\n\nbody\n\n"
         "## SOMETHING ELSE\n\n### D9902 - way down here\n**Status:** OPEN\n\nbody\n"),
        ("E-BADSTATUS",
         "## DECISIONS\n\n### D9916 - THE PLAN IS WRONG\n\n"
         "**Status:** the human authorized the fix; o91 is building it\n\nbody\n"),
        ("E-PLATEDRIFT",
         "# Doc\n\n" + PLATE_BEGIN + "\n\n| ID | What it is | Owner | Opened | Enriched |\n"
         "|---|---|---|---|---|\n| `D9909` | a row a human typed in | the human | - | - |\n\n"
         "_1 open. Generated by `orchdoc.py plate`; edits here are overwritten._\n"
         + PLATE_END + "\n\n## DECISIONS\n\n### D9901 - real entry\n"
         "**Status:** OPEN - **Owner:** the human\n\nbody\n"),
    ]
    ok = True
    print("orchdoc selftest")
    _saved_remote = os.environ.pop("CLAUDE_CODE_REMOTE", None)
    for want, body in cases:
        if body is None and want == "E-NOFETCH":
            # FUNCTIONAL assertion, not a document fixture: E-NOFETCH is emitted when the
            # REMOTE is unreachable, which no .md file can simulate. A real repo with a
            # bogus remote is the only honest test, and the meta-guard is right to demand
            # one - an untestable blocking code is indistinguishable from a dead one.
            d = Path(tempfile.mkdtemp())
            try:
                git(["init", "-q", "."], cwd=d)
                git(["remote", "add", "origin",
                     "https://o99-nonexistent-host.invalid/x.git"], cwd=d)
                reached, _why = fetch_ok(cwd=d)
            finally:
                _remove_tree(str(d))
            good = (reached is False)      # an unreachable remote must report FAILURE
            ok &= good
            print("  [%s] %-12s -> unreachable remote reports FAILURE, not success"
                  % ("OK" if good else "FAIL", want))
            continue

        if body is None and want == "E-REFLOOKUP":
            good = _selftest_ref_lookup()
            ok &= good
            print("  [%s] %-12s -> a failed lookup in one repo fires, not E-DEADREF; the same "
                  "citation with every repo answering is E-DEADREF" % ("OK" if good else "FAIL", want))
            continue

        if body is None and want == "E-PRELABEL":
            good = _selftest_prelabel()
            ok &= good
            print("  [%s] %-12s -> an added bare pre-label fires; an old one, or one in "
                  "inline code, does not" % ("OK" if good else "FAIL", want))
            continue

        if body is None and want == "E-BURIEDASK":
            good = _selftest_buried_ask()
            ok &= good
            print("  [%s] %-12s -> an added unchecked sub-item asking the human fires; a pointer to a "
                  "Q entry, a checked box and an old sub-item do not" % ("OK" if good else "FAIL", want))
            continue

        if body is None and want == "E-WORDSELSEWHERE":
            good = _selftest_words_elsewhere()
            ok &= good
            print("  [%s] %-12s -> a pointer to a PR or file for words fires; the words quoted "
                  "in the entry do not" % ("OK" if good else "FAIL", want))
            continue

        if body is None and want == "E-WAKESOTHERS":
            good = _selftest_wakes()
            ok &= good
            print("  [%s] %-12s -> a new quoted mention of a cited id fires; one of an id "
                  "nobody cites does not" % ("OK" if good else "FAIL", want))
            continue

        if body is None:
            # Parser-level assertion: a trailer that cannot resolve must be reported,
            # never silently dropped.
            _, bad = touches_since("o99", None)
            probe = {}
            for tok in ["D9901", "o99:D9901", "notanid", "o97:D9916"]:
                m = TOUCH_TOKEN_RE.match(tok)
                probe[tok] = (m.group(1), m.group(2)) if m else None
            good = (probe["D9901"][0] is None            # unqualified -> must be refused
                    and probe["o99:D9901"] == ("o99", "D9901")
                    and probe["notanid"] is None
                    and probe["o97:D9916"] == ("o97", "D9916"))
            ok &= good
            print("  [%s] %-12s -> %s" % ("OK" if good else "FAIL", want,
                                          "qualified/unqualified/invalid all classified"))
            continue
        if want == "E-NEEDLESPACE" and _needle_doc is None:
            print("  [SKIP] %-12s -> needs an OrchDoc in the workspace to cite; this folder has "
                  "none" % want)
            continue
        # The fixture file must be NAMED canonically or E-TITLE can never apply: the
        # check reads the EXPECTED identity from the filename, which is the whole point
        # of it. A random temp name made the check unreachable and the fixture green -
        # the same false-pass shape the meta-guard exists to catch.
        tmp = Path(tempfile.mkdtemp()) / "ORCHESTRATOR-DECISIONS-o99.md"
        try:
            tmp.write_text(body, encoding="utf-8")
            codes = {f.code for f in check_doc(tmp)}
            if want in _NEEDS_GIT and not any((r / ".git").exists()
                                              for r in citable_repos()):
                print("  [SKIP] %-12s -> needs a git repo to settle SHAs; not verifiable "
                      "in this environment" % want)
                continue
            good = want in codes
            ok &= good
            print("  [%s] %-12s -> %s" % ("OK" if good else "FAIL", want,
                                          ",".join(sorted(codes)) or "none"))
        finally:
            _remove_tree(str(tmp.parent))  # the fixture and the folder mkdtemp made for it
    if _saved_remote is not None:
        os.environ["CLAUDE_CODE_REMOTE"] = _saved_remote

    _dead = "## NOTES\n\nLanded in `deadbeef1234567` last week.\n"
    tmp = Path(tempfile.mkdtemp()) / "ORCHESTRATOR-DECISIONS-o99.md"
    tmp.write_text(_dead, encoding="utf-8")
    try:
        if not any((r / ".git").exists() for r in citable_repos()):
            print("  [SKIP] %-12s -> needs a git repo to settle SHAs; not verifiable "
                  "in this environment" % "cloud-ref")
        else:
            _was = os.environ.pop("CLAUDE_CODE_REMOTE", None)
            laptop = {f.code for f in check_doc(tmp)}
            os.environ["CLAUDE_CODE_REMOTE"] = "true"
            try:
                cloud = {f.code for f in check_doc(tmp)}
            finally:
                os.environ.pop("CLAUDE_CODE_REMOTE", None)
                if _was is not None:
                    os.environ["CLAUDE_CODE_REMOTE"] = _was
            # In the cloud the demotion only applies when the environment REALLY cannot
            # settle a citation. A deep clone standing beside its siblings gets E-DEADREF
            # in both columns, correctly, and that is a pass - not a silent skip.
            want_cloud = "W-CLOUDREF" if ref_settle_limits() else "E-DEADREF"
            good = ("E-DEADREF" in laptop and "W-CLOUDREF" not in laptop
                    and want_cloud in cloud
                    and (want_cloud == "E-DEADREF" or "E-DEADREF" not in cloud))
            ok &= good
            print("  [%s] %-12s -> unset=E-DEADREF, CLAUDE_CODE_REMOTE=true -> %s"
                  % ("OK" if good else "FAIL", "cloud-ref", want_cloud))
    finally:
        _remove_tree(str(tmp.parent))  # the fixture and the folder mkdtemp made for it

    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(canonical_title("o99", "a role") + "\n\n"
                 "## DECISIONS\n\n### \U0001f534 W9904 - a container with work left\n"
                 "**Status:** OPEN - **Owner:** o99\n\n"
                 "- [x] 1. export everything - ✅ DONE\n"
                 "- [x] 2. stop the write path - ✅ DONE\n"
                 "- [ ] 3. rehome the rows\n")
        tmp = Path(fh.name)
    try:
        codes = {f.code for f in check_doc(tmp)}
        good = "E-ALLSUBSDONE" not in codes
        ok &= good
        print("  [%s] %-12s -> %s" % ("OK" if good else "FAIL", "open-box",
                                      "E-ALLSUBSDONE absent" if good
                                      else "FIRED on an entry with an open box"))
    finally:
        try:
            tmp.unlink()
        except (OSError, UnicodeDecodeError):
            pass

    _fixtured_apart = set()    # blocking codes driven outside `cases`; the meta-guard counts them
    _pd = Path(tempfile.mkdtemp(prefix="orchdoc-pics-"))
    try:
        (_pd / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (_pd / "shots.html").write_text('<img src="a.png" alt="version A">', encoding="utf-8")
        (_pd / "saved.html").write_text('<img src="https://example.com/a.png">',
                                        encoding="utf-8")

        def _pic_codes(body, status="OPEN", owner="the human"):
            if owner == "the human":
                owner = human_name().title()
            tmp = _pd / "ORCHESTRATOR-DECISIONS-o99.md"
            tmp.write_text(canonical_title("o99", "a role") + "\n\n## DECISIONS\n\n"
                           "### D9907 - pick a card layout\n**Status:** %s - **Owner:** %s\n\n"
                           "%s\n" % (status, owner, body), encoding="utf-8")
            return {f.code for f in check_doc(tmp)}

        _page = (_pd / "shots.html").as_posix()
        _png = (_pd / "a.png").as_posix()
        _block = "**Pictures:** the card\n![version A](%s)" % _png
        _human_note = render_note("D9907.n1", "the human", "2026-10-02 03:00", "the card is %s" % _png)
        for _code, _label, _body, _st, _own, _want in [
                ("E-PICTUREPAGE", "refuses a page", "See all of them: `%s`." % _page,
                 "OPEN", "the human", True),
                ("E-PICTUREPAGE", "block clears it",
                 "See all of them: `%s`.\n\n%s" % (_page, _block), "OPEN", "the human", False),
                ("E-PICTUREPAGE", "web page silent",
                 "Saved: `%s`." % (_pd / "saved.html").as_posix(), "OPEN", "the human", False),
                ("E-PICTUREPAGE", "finished silent", "See all of them: `%s`." % _page,
                 "RESOLVED", "the human", False),
                ("E-PICTUREPAGE", "joined block",
                 "See all of them: `%s`.\nprose above\n%s" % (_page, _block), "OPEN", "the human",
                 True),
                ("W-PICTURENOTSHOWN", "path in the human's note", "The card.\n\n%s" % _human_note,
                 "OPEN", "the human", True),
                ("W-PICTURENOTSHOWN", "shown clears it",
                 "The card.\n\n%s\n\n![the card](%s)" % (_human_note, _png), "OPEN", "the human",
                 False),
                ("W-PICTURENOTSHOWN", "not the human's: silent", "The card.\n\n%s" % _human_note,
                 "OPEN", "o99", False),
                ("W-PICTURENOTSHOWN", "E-IMAGEBYPATH names it: silent",
                 "The card: `%s`." % _png, "OPEN", "the human", False)]:
            _fired = _code in _pic_codes(_body, _st, _own)
            if _fired and _label == "refuses a page":
                _fixtured_apart.add("E-PICTUREPAGE")   # only a refusal SEEN counts as cover
            good = _fired == _want
            ok &= good
            print("  [%s] %-12s -> %s: %s" % ("OK" if good else "FAIL", _code,
                                              _label, "fired" if _fired else "silent"))
    finally:
        _remove_tree(str(_pd))

    ok &= _selftest_last_moved()
    ok &= _selftest_copy_guards(_fixtured_apart)

    if shutil.which("git"):
        for _rewrite, _want, _label in ((False, False, "role-created"),
                                        (True, True, "role-rewritten")):
            _repo = None
            try:
                _repo = pathlib.Path(tempfile.mkdtemp(prefix="orchdoc-role-"))

                def _g(*a):
                    return subprocess.run(["git", "-C", str(_repo)] + list(a),
                                          capture_output=True, text=True, creationflags=flags())

                _g("init", "-q")
                _g("config", "user.email", "fixture@example.com")
                _g("config", "user.name", "fixture")
                _name = "ORCHESTRATOR-DECISIONS-o99.md"
                _body = ("## \u00a73 IN FLIGHT\n\n### W9901 - the entry this commit creates\n\n"
                         "**Status:** OPEN - **Owner:** o99 - "
                         "**Reviewed:** 2026-01-01T00:00:00-08:00\n"
                         "**Depends:** `some/file.ts`\n\nThe reasoning, as first written.\n")
                (_repo / _name).write_text(_body, encoding="utf-8")
                _g("add", _name)
                _g("commit", "-m", "o99: add W9901\n\nTouches: o99:W9901")
                if _rewrite:
                    (_repo / _name).write_text(
                        _body.replace("The reasoning, as first written.",
                                      "The reasoning, materially rewritten after measurement."),
                        encoding="utf-8")
                    _g("add", _name)
                    _g("commit", "-m", "o99: rewrite W9901 reasoning\n\nTouches: o99:W9901")
                _g("update-ref", "refs/remotes/origin/main", "HEAD")

                _real_git = globals()["git"]
                globals()["git"] = lambda a, cwd=None, _r=_real_git, _p=_repo: _r(a, cwd=_p)
                _ROLE_CACHE.clear()
                try:
                    _codes = [f for f in check_doc(_repo / _name)
                              if f.code == "E-STALEPROSE"]
                finally:
                    globals()["git"] = _real_git
                    _ROLE_CACHE.clear()
                _got = bool(_codes)
                _good = _got == _want
                ok &= _good
                print("  [%s] %-12s -> E-STALEPROSE fired=%s (want %s)"
                      % ("OK" if _good else "FAIL", _label, _got, _want))
            except Exception as _e:
                print("  [SKIP] %-12s -> %s" % (_label, str(_e)[:60]))
            finally:
                if _repo is not None:
                    _remove_tree(str(_repo))    # the repo's object files are read-only

    _rs = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                         encoding="utf-8") as fh:
            fh.write(canonical_title("o99", "a role") + "\n\n"
                     "## DECISIONS\n\n### \U0001f534 D9901 - an entry to stamp twice\n"
                     "**Status:** OPEN - **Owner:** o99 - "
                     "**Reviewed:** 2026-01-01T00:00:00-08:00 - the first reading, "
                     "which names what it rested on at the time\n\nReasoning prose.\n")
            _rs = Path(fh.name)

        class _A(object):
            pass

        def _stamp(when, why):
            return write_restamp(_rs, "D9901", why, "o99", when)

        _one = "the first re-review, naming a mover and why the entry survives it"
        _two = "the second re-review, later, naming a different mover entirely"
        _rc = _stamp("2026-02-02T00:00:00-08:00", _one)
        _rc |= _stamp("2026-03-03T00:00:00-08:00", _two)
        _txt = _rs.read_text(encoding="utf-8", errors="replace")
        _kept = (_one in _txt) and (_two in _txt) and ("the first reading" in _txt)
        _date, _att = reviewed_of(_txt)
        _newest = (_date or "").startswith("2026-03-03")
        _good = _rc == 0 and _kept and _newest
        ok &= _good
        print("  [%s] %-12s -> appended=%s newest=%s" %
              ("OK" if _good else "FAIL", "restamp-rw", _kept, _date or "none"))
    except Exception as _e:
        print("  [SKIP] %-12s -> %s" % ("restamp-rw", str(_e)[:60]))
    finally:
        if _rs is not None:
            try:
                _rs.unlink()
            except OSError:
                pass

    _rp = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                         encoding="utf-8") as fh:
            fh.write(canonical_title("o99", "a role") + "\n\n"
                     "## DECISIONS\n\n### \U0001f534 D9901 - an entry whose plate line grew\n"
                     "**Status:** OPEN - **Owner:** the human - **Opened:** 2026-01-01 - "
                     "**Reviewed:** 2026-01-01T00:00:00-08:00 - the first reading, which "
                     "names the facts it rested on at the time - **Attested-by:** o91 at "
                     "2026-02-02T00:00:00-08:00 - the second reading, appended to the same "
                     "line, naming a mover the first one could not have seen\n"
                     "**Depends:** D9902\n\nReasoning prose.\n")
            _rp = Path(fh.name)

        _three = ("the third reading, which names what moved since February and why the "
                  "entry survives it")
        _rc = write_restamp(_rp, "D9901", _three, "o99", "2026-03-03T00:00:00-08:00")
        _txt = _rp.read_text(encoding="utf-8", errors="replace")
        _plate = next((l for l in _txt.split("\n") if l.startswith("**Status:**")), "")

        # 1. the plate line carries the tracked fields and a bare date, and no prose at all
        _short = len(_plate) <= 140 and not any(
            frag in _plate for frag in ("the first reading", "the second reading", _three))
        # it is still a PLATE: the fields that were on it are still on it
        _fields = all(f in _plate for f in ("**Status:** OPEN", "**Owner:** the human",
                                            "**Opened:** 2026-01-01"))
        # 2. nothing was dropped - and `**Depends:**` did not get eaten by the rewrite
        _kept3 = all(s in _txt for s in ("the first reading", "the second reading", _three)) \
            and "**Depends:** D9902" in _txt
        # 3. the newest stamp, with its prose, is what a reader gets
        _d, _a = reviewed_of(_txt)
        _newest = (_d or "").startswith("2026-03-03") and _three in (_a or "")
        # 4. idempotent: the same stamp again writes nothing, and shortens rather than doubles
        _rc |= write_restamp(_rp, "D9901", _three, "o99", "2026-03-03T00:00:00-08:00")
        _stable = _rp.read_text(encoding="utf-8", errors="replace") == _txt
        _good = _rc == 0 and _short and _fields and _kept3 and _newest and _stable
        ok &= _good
        print("  [%s] %-12s -> plate=%dch short=%s kept=%s newest=%s idempotent=%s"
              % ("OK" if _good else "FAIL", "restamp-plate", len(_plate), _short,
                 _kept3, _newest, _stable))
        if not _good:
            print("       plate line: %s" % _plate[:150])
    except Exception as _e:
        ok = False
        print("  [FAIL] %-12s -> %s" % ("restamp-plate", str(_e)[:70]))
    finally:
        if _rp is not None:
            try:
                _rp.unlink()
            except OSError:
                pass

    # A clean doc must produce nothing. Guards against over-eager matching.
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(canonical_title("o99", "a role") + "\n\n"
                 "## DECISIONS\n\n### \U0001f534 D9901 - a clean entry\n"
                 "**Status:** OPEN - **Owner:** the human\n\nReasoning prose here.\n")
        tmp = Path(fh.name)
    try:
        codes = {f.code for f in check_doc(tmp)}
        good = not codes
        ok &= good
        print("  [%s] %-12s -> %s" % ("OK" if good else "FAIL", "clean-doc",
                                      ",".join(sorted(codes)) or "none"))
    finally:
        try:
            tmp.unlink()
        except (OSError, UnicodeDecodeError):
            pass

    _NONCONTIG = ("## DECISIONS\n\n"
                  "### D9903 - first\n**Status:** OPEN - **Owner:** the human\n\nbody three\n\n"
                  "### D9922 - out of order\n**Status:** OPEN - **Owner:** the human\n\nbody 22\n\n"
                  "### W9901 - a different prefix, must not move\n"
                  "**Status:** OPEN - **Owner:** o99\n\nbody w1\n\n"
                  "### W9903 - also must not move\n**Status:** OPEN - **Owner:** o99\n\nbody w3\n\n"
                  "### D9921 - belongs above D9922\n**Status:** OPEN - **Owner:** the human\n\nbody 21\n")
    _dir = Path(tempfile.mkdtemp(prefix=".orchdoc-selftest-", dir=str(PROJECTS)))
    tmp = _dir / "ORCHESTRATOR-DECISIONS-o99.md"
    tmp.write_text(_NONCONTIG, encoding="utf-8")
    try:
        saw = "E-IDORDER" in {f.code for f in check_doc(tmp)}
        _ns = argparse.Namespace(doc=str(tmp), dry_run=False, not_mine=True)
        import io as _io
        import contextlib as _ctx
        with _ctx.redirect_stdout(_io.StringIO()):
            rc = cmd_reorder(_ns)
        after = tmp.read_text(encoding="utf-8")
        cleared = "E-IDORDER" not in {f.code for f in check_doc(tmp)}
        ids = [e["id"] for e in parse_entries(after.split("\n"))[0]]
        placed = ids == ["D9903", "D9921", "W9901", "W9903", "D9922"]
        kept = sorted(_NONCONTIG.split("\n")) == sorted(after.split("\n"))
        good = saw and rc == 0 and cleared and placed and kept
        ok &= good
        print("  [%s] %-12s -> check saw it=%s, reorder fixed it=%s, order=%s, bytes kept=%s"
              % ("OK" if good else "FAIL", "reorder-pair", saw, cleared,
                 ",".join(ids), kept))
    finally:
        try:
            tmp.unlink()
            _dir.rmdir()
        except (OSError, UnicodeDecodeError):
            pass

    try:
        _src = tool_source()
        _src = "\n".join(_ln.split("#")[0] for _ln in _src.split("\n"))
        _named = set(re.findall(r"orchdoc\.py\s+([a-z][a-z-]{2,})", _src))
        _real = set(re.findall(r'add_parser\(\s*"([a-z][a-z-]+)"', _src))
        _ghost = sorted(n for n in _named if n not in _real)
        _good = not _ghost and bool(_real)
        ok &= _good
        print("  [%s] %-12s -> %s"
              % ("OK" if _good else "FAIL", "ghost-cmd",
                 "every command named in a message exists" if _good
                 else "named but not a subcommand: " + ", ".join(_ghost)))
    except OSError as _e:
        print("  [SKIP] %-12s -> %s" % ("ghost-cmd", str(_e)[:60]))

    covered = {want for want, _ in cases} | _fixtured_apart
    missing = sorted(BLOCKING - covered - {"E-IO", "E-STALE"})
    if missing:
        ok = False
        print("  [FAIL] %-12s -> blocking codes with NO fixture: %s"
              % ("meta-guard", ", ".join(missing)))
    else:
        print("  [OK] %-12s -> every blocking code has a fixture" % "meta-guard")

    print("\nselftest %s" % ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


PLATE_BEGIN_TOKEN = "ORCHDOC:PLATE:BEGIN"
PLATE_END_TOKEN = "ORCHDOC:PLATE:END"
PLATE_BEGIN = ("<!-- %s - generated by `orchdoc.py plate`. Do not hand-edit. -->"
               % PLATE_BEGIN_TOKEN)
PLATE_END = "<!-- %s -->" % PLATE_END_TOKEN


def marker_span(lines, begin_tok, end_tok, label="derived-region"):
    """
    (start_idx, end_idx) of a generated region, or (None, reason).

    Refuses on anything malformed: an unclosed BEGIN, an orphan END, duplicates, or an
    END before its BEGIN. Never guesses.

    PARAMETERISED over the token pair because there are now two generated regions - the
    plate and the schema index. Re-implementing these refusal rules for the second one
    would put the marker contract in two places, and a contract in two places is the
    defect this tool exists to prevent.
    """
    def _real(tok, l):
        return re.search(r"<!--[^>]*\b%s\b[^>]*-->" % re.escape(tok),
                         re.sub(r"`[^`]*`", "", l)) is not None

    in_fence, live = False, []
    for l in lines:
        if l.lstrip().startswith("```"):
            in_fence = not in_fence
            live.append(False)
            continue
        live.append(not in_fence)

    begins = [i for i, l in enumerate(lines) if live[i] and _real(begin_tok, l)]
    ends = [i for i, l in enumerate(lines) if live[i] and _real(end_tok, l)]
    if not begins and not ends:
        return None, None  # no such region at all: legitimate, not an error
    if len(begins) != 1 or len(ends) != 1:
        return None, ("malformed %s marker: %d BEGIN, %d END (expected 1 each)"
                      % (label, len(begins), len(ends)))
    if ends[0] < begins[0]:
        return None, "malformed %s marker: END appears before BEGIN" % label
    return (begins[0], ends[0]), None


def plate_span(lines):
    """The plate's region. Every caller predates marker_span; this keeps them honest."""
    return marker_span(lines, PLATE_BEGIN_TOKEN, PLATE_END_TOKEN)


def index_span(lines):
    """The schema index's region."""
    return marker_span(lines, INDEX_BEGIN_TOKEN, INDEX_END_TOKEN, "schema-index")


def findex_span(lines):
    """The findings index's region, at the head of section 4."""
    return marker_span(lines, FINDEX_BEGIN_TOKEN, FINDEX_END_TOKEN, "findings-index")


DERIVED_REGIONS = [
    ("meta", META_BEGIN_TOKEN, META_END_TOKEN),
    ("plate", PLATE_BEGIN_TOKEN, PLATE_END_TOKEN),
    ("schema-index", INDEX_BEGIN_TOKEN, INDEX_END_TOKEN),
    ("findings-index", FINDEX_BEGIN_TOKEN, FINDEX_END_TOKEN),
]


def derived_spans(lines):
    """[(lo, hi)] for every generated region, or (None, reason) if any is malformed.

    Refuses rather than guesses, for the same reason marker_span does: a boundary that
    cannot be located cannot support a claim about what is inside it.
    """
    spans = []
    for label, b, e in DERIVED_REGIONS:
        span, why = marker_span(lines, b, e, label)
        if why:
            return None, why
        if span:
            spans.append(span)
    return spans, None

KIND_SECTION = {
    "decision": "DECISIONS",
    "question": "QUESTIONS",
    "finding": "FINDINGS",
    "todo": "TO-DOS",
    "specimen": "SPECIMENS",
    "work": "ON CLAUDE'S PLATE",
    "today": "DO TODAY",
}
KIND_PREFIX = {"decision": "D", "question": "Q", "finding": "F", "todo": "T",
               "specimen": "S", "work": "W", "today": "N"}


def _now_iso():
    from datetime import datetime
    return datetime.now().astimezone().isoformat(timespec='seconds')


def _today():
    from datetime import date
    return date.today().isoformat()


class DocPathError(Exception):
    """A doc argument that must be refused by name rather than acted on."""


def _confine(p):
    """Refuse any path that resolves outside the workspace.
    """
    try:
        rp = Path(p).resolve()
        root = Path(PROJECTS).resolve()
    except OSError as e:
        raise DocPathError("cannot resolve path: %s" % e)
    try:
        inside = rp == root or rp.is_relative_to(root)
    except AttributeError:                      # Python < 3.9
        inside = str(rp).startswith(str(root))
    if not inside:
        raise DocPathError(
            "refusing a doc outside the workspace.\n"
            "           target    : %s\n"
            "           workspace : %s\n"
            "           Set ORCHDOC_WORKSPACE if the workspace is elsewhere." % (rp, root))
    if rp.is_dir():
        raise DocPathError("that path is a DIRECTORY, not a document: %s" % rp)
    return rp


def resolve_doc_arg(val):
    if not val:
        return None
    p = Path(val)
    if p.exists():
        return _confine(p)
    if re.fullmatch(r"o\d+", val):
        return _confine(PROJECTS / ("ORCHESTRATOR-DECISIONS-%s.md" % val))
    return _confine(PROJECTS / val)



def sanitize_field(text, limit=400, strip_lead=True):
    """Narrow caller-supplied text so it cannot impersonate generated structure.

    Not an escape - a NARROWING. The result must be safe to place inside a heading or a
    field line, so anything that could be read as structure is removed rather than encoded:

      * newlines      -> spaces. A title is one line; a multi-line title forged whole
                         sections and entries.
      * ORCHDOC:*     -> defanged. A caller who writes a generated marker can otherwise
                         wedge the document permanently: two ENDs make the span
                         unresolvable, so `scaffold` refuses forever.
      * leading #     -> stripped, so text cannot become a heading.
      * **Status:** and friends -> defanged. This is the important one. Injecting a Status
                         field made an entry read RESOLVED that had never been ruled, which
                         is precisely the false-done this tool exists to prevent.
      * `<!--` / `-->` -> defanged, so a comment cannot be opened or closed.

    Truncated at `limit`, because an unbounded title is its own denial of service on a
    document meant to be read.
    """
    # ASCII replacements only. A non-raw "\u2024" inside a re.sub REPLACEMENT is not a
    # unicode escape at all - re rejects it as a bad escape - and ASCII also keeps this
    # safe on a cp1252 Windows console, where a stray glyph is its own crash.
    t = (text or "")
    t = re.sub(r"[\r\n\t\v\f\x1c-\x1e\x85  ]+", " ", t)   # a title is ONE line
    t = re.sub(r"ORCHDOC:([A-Z]+):(BEGIN|END)",             # cannot forge a marker
               r"ORCHDOC_\1_\2", t)
    t = t.replace("<!--", "<!- ").replace("-->", " -!>")    # cannot open/close a comment
    t = re.sub(r"\*{0,2}(Status|Owner|Opened|Depends|Touches|Attested-by|Reviewed|Resolved|"
               r"Recorded|Enriched|Done-when)\*{0,2}\s*:", r"(\1:)", t,
               flags=re.IGNORECASE)                         # cannot forge a FIELD
    for _rx in (STATUS_RE, REVIEWED_LABEL_RE, DONE_WHEN_RE):
        for _ in range(8):                                  # bounded: never loop on input
            _m = _rx.search(t)
            if not _m:
                break
            _c = t.find(":", _m.start())
            if _c < 0:
                break
            t = t[:_c] + " -" + t[_c + 1:]
    if strip_lead:
        t = re.sub(r"^[\s#>*\-]+", "", t)                   # cannot become a heading
    t = re.sub(r"\s{2,}", " ", t).strip()
    if limit is not None and len(t) > limit:
        t = t[:limit].rstrip() + "..."
    return t


def refuse_if_too_long(text, limit, what):
    """Return `text`, or exit with a message naming the field and both lengths.
    """
    t = (text or "")
    if len(t) <= limit:
        return t
    print("REFUSING: --%s is %d characters and the limit is %d." % (what, len(t), limit),
          file=sys.stderr)
    print(file=sys.stderr)
    print("    Nothing was written. This field is capped because it renders as a single",
          file=sys.stderr)
    print("    line a reader scans, not because your text is unwelcome - put the detail in",
          file=sys.stderr)
    print("    the entry body, which has no limit.", file=sys.stderr)
    print(file=sys.stderr)
    print("    It used to CUT at %d and report success. 138 rulings and 96 override reasons"
          % limit, file=sys.stderr)
    print("    across the fleet were shortened that way before anybody noticed.",
          file=sys.stderr)
    print(file=sys.stderr)
    print("    The last %d characters, so you can see what would have gone:" % min(len(t) - limit, 160),
          file=sys.stderr)
    print("      ...%s" % t[limit:][:160], file=sys.stderr)
    sys.exit(2)


def sanitize_body(text):
    """The same NARROWING as `sanitize_field`, for prose that must survive whole.
    """
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"ORCHDOC:([A-Z]+):(BEGIN|END)", r"ORCHDOC_\1_\2", t)
    t = t.replace("<!--", "<!- ").replace("-->", " -!>")
    t = re.sub(r"\*{0,2}(Status|Owner|Opened|Depends|Touches|Attested-by|Reviewed|Resolved|"
               r"Recorded|Enriched|Done-when)\*{0,2}\s*:", r"(\1:)", t, flags=re.IGNORECASE)
    for _rx in (STATUS_RE, REVIEWED_LABEL_RE, DONE_WHEN_RE):
        for _ in range(64):                                 # bounded: never loop on input
            _m = _rx.search(t)
            if not _m:
                break
            _c = t.find(":", _m.start())
            if _c < 0:
                break
            t = t[:_c] + " -" + t[_c + 1:]
    # Per line, because a body keeps its newlines. A blank line stays blank so paragraphs survive.
    out = []
    for line in t.split("\n"):
        out.append(re.sub(r"^[\s#>]+", "", line).rstrip())
    t = "\n".join(out).strip()
    return t



#: How often `write_doc` tries os.replace again when Windows refuses it because another
#: process (the Reader reading the doc for a page, an editor, a virus scanner) has the file open.
REPLACE_RETRIES = 5


def _replace_retry(src, dst, tries=None, pause=0.05):
    """os.replace, tried again a few times, 50 ms, 100 ms, ... apart, on PermissionError only.
    On Windows a replace fails with PermissionError while any process holds the target open
    without FILE_SHARE_DELETE; that hold lasts milliseconds, and a save should not fail on it."""
    tries = REPLACE_RETRIES if tries is None else tries
    for i in range(tries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(pause * (i + 1))


def write_doc(path, text, expect=None):
    """Write atomically. A partial OrchDoc is worse than a failed command.

    Every write here was read-modify-write straight onto the real file, so an interrupt, a
    full disk or a crash mid-write left a TRUNCATED document - and the document is the
    durable record this entire tool exists to protect.

    temp-beside-target + flush + fsync + os.replace(). os.replace is atomic on POSIX and on
    Windows, so a reader sees either the old file or the new one, never a half-written one.
    Beside the target, not in $TMP, because a cross-volume replace is not atomic.
    """
    path = Path(path)
    if expect is not None:
        try:
            _current = path.read_text(encoding="utf-8")
        except OSError:
            _current = None
        if _current is not None and _current != expect:
            raise DocPathError(
                "%s changed on disk while this command was working on it. Another session "
                "landed between the read and the write, so writing now would erase it. "
                "Nothing was written - re-run, and the rebuild will carry both." % path.name)
    tmp = path.with_name(path.name + ".orchdoc-tmp-%d" % os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        _replace_retry(str(tmp), str(path))
    except OSError as e:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise DocPathError("cannot write %s: %s" % (path.name, e))



def actor_id():
    """Who is running this. $CLAUDE_ORCH_ID, else DERIVED from the doc being written.
    """
    env = os.environ.get("CLAUDE_ORCH_ID")
    if env:
        return sanitize_field(env, 40)
    return "unattributed"


def actor_for(doc):
    """actor_id(), or the doc's own orchestrator id marked as inferred.
    """
    for var in ("CLAUDE_ORCH_ID", "ORCHDOC_ME"):
        env = (os.environ.get(var) or "").strip()
        if env:
            return sanitize_field(env, 40)
    m = re.search(r"ORCHESTRATOR-DECISIONS-(o\d+)", str(doc))
    return ("%s?" % m.group(1)) if m else "unattributed"


def next_id(entries, prefix, text=""):
    """Allocate the next free numeric id for a prefix. Never grep by hand again.
    """
    hi = 0
    pat = re.compile(r"^%s(\d+)$" % re.escape(prefix))
    for e in entries:
        m = pat.match(e["id"])
        if m:
            hi = max(hi, int(m.group(1)))
    for m in ARCHIVED_ID_RE.finditer(text or ""):
        mm = pat.match(m.group(1))
        if mm:
            hi = max(hi, int(mm.group(1)))
    return "%s%d" % (prefix, hi + 1)


REGISTRY = PROJECTS / "ORCHESTRATOR-REGISTRY.md"


def registry_status(oid):
    """The status word the REGISTRY carries for this orchestrator, or None.

    The registry is the record of that intent, so it is the authority on whether quiet is a
    finding. Parsed by SHAPE - the last cell of the row whose first cell is `oN` - rather than
    by prose, because the descriptions in that column are long and change often.
    """
    try:
        text = REGISTRY.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        if cells[0].strip("`* ").lower() != oid:
            continue
        last = cells[-1].lower()
        for word in ("retired", "graduated", "dormant", "active", "consolidated"):
            if word in last:
                return word
    return None


SELF_STAMP = re.compile(
    r"\*\*(?:Attested-by|Reviewed|Opened|Recorded|Resolved):\*\*[^\n]*?(\d{4}-\d{2}-\d{2})")


def last_worked(doc):
    """Days since this orchestrator last stamped its OWN doc, or None if it never has.

    A dated stamp inside the doc is written by that orchestrator while doing the work. No other
    session can move it, which is exactly the property the other three lacked.
    """
    try:
        text = doc.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    dates = sorted(set(SELF_STAMP.findall(text)))
    if not dates:
        return None
    try:
        newest = _dt.date.fromisoformat(dates[-1])
    except ValueError:
        return None
    return (_dt.date.today() - newest).days


def cmd_sessions(args):
    """Which OrchDocs have a session naming them, and which do not.

    Widening the matcher covers the prefixes anyone thought of. This covers the one nobody did,
    by asking the question from the other end: every OrchDoc declares an owner in its filename,
    so does any session name that owner? A doc with no session is either a retired orchestrator
    or a rename that broke identification, and the two are told apart by looking.
    """
    docs = {}
    for p in sorted(PROJECTS.glob("ORCHESTRATOR-DECISIONS-*.md")):
        oid = doc_owner_id(p)
        if oid:
            docs[oid] = p.name

    seen = {}
    try:
        for f in _app_session_files():
            d = session_head(f)
            if d is None:
                continue
            title = (d.get("title") or "").strip()
            m = TITLE_ID.match(title)
            if m:
                seen.setdefault(m.group(1).lower(), []).append((title, d.get("isArchived")))
    except Exception as e:
        print("  [WARN] could not read the session store: %s" % e)

    unnamed = [o for o in docs if o not in seen]
    orphan = [o for o in seen if o not in docs]

    print("orchestrator docs: %d   of which the hook can IDENTIFY a session for: %d"
          % (len(docs), len(seen)))
    print("(identifiable is not active - see LAST-WORKED, which no other session can move)")
    print()
    for oid in sorted(docs, key=lambda s: int(s[1:])):
        rows = seen.get(oid) or []
        live = [t for t, arch in rows if not arch]
        mark = "ok  " if live else ("arch" if rows else "NONE")
        age = last_worked(PROJECTS / docs[oid])
        when = ("never stamped" if age is None
                else "today" if age == 0
                else "%d days" % age)
        status = registry_status(oid)
        quiet = age is None or age > 14
        flag = (" <- COLD" if quiet and status in (None, "active")
                else "  (%s)" % status if status else "")
        print("  [%s] %-5s %-30s %-14s %s%s"
              % (mark, oid, docs[oid][:30], when,
                 (live or [t for t, _ in rows] or ["-- no session names this doc --"])[0][:30],
                 flag))

    if orphan:
        print()
        print("  sessions naming an orchestrator with no doc: %s" % ", ".join(sorted(orphan)))
    if unnamed:
        print()
        print("  \u26d4 %d doc(s) have NO session naming them. Each is either a retired"
              % len(unnamed))
        print("     orchestrator - fine - or a session renamed into a shape the matcher no")
        print("     longer parses, in which case its Stop hook is SILENT and nothing else")
        print("     would say so. Check the titles in the sidebar against: %s"
              % ", ".join(sorted(unnamed)))
    return 0


def cmd_registry(args):
    """Scope conflicts in ORCHESTRATOR-REGISTRY.md - the parts that are FACTS, not prose.
    """
    reg = PROJECTS / "ORCHESTRATOR-REGISTRY.md"
    if not reg.exists():
        print("no ORCHESTRATOR-REGISTRY.md at %s" % reg, file=sys.stderr)
        return 2
    text = reg.read_text(encoding="utf-8", errors="replace")

    rows = []
    for ln in text.split("\n"):
        if not ln.startswith("|"):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        oid = cells[0].strip("`* ")
        if re.fullmatch(r"o\d+", oid):
            rows.append((oid, cells[1], cells[-1]))

    rc = 0
    print("orchdoc registry - %d row(s)" % len(rows))

    claims, notes = {}, []
    for oid, desc, _status in rows:
        for sent in re.split(r"(?<=[.!?])\s+|—|–", desc):
            for m in re.finditer(r"<your-github-user>/([A-Za-z0-9._-]+)", sent):
                repo = m.group(1).lower()
                others = sorted(set(re.findall(r"\bo\d+\b", sent)) - {oid})
                if others:
                    notes.append((oid, repo, others))
                else:
                    claims.setdefault(repo, []).append(oid)
    clash = {r: sorted(set(o)) for r, o in claims.items() if len(set(o)) > 1}
    # Every ERROR is also collected for the one-line summary printed last. See the end of
    # this function for why the LAST line has to carry it.
    errors = []
    if clash:
        rc = 1
        print()
        for repo, owners in sorted(clash.items()):
            errors.append("%s is claimed by %s" % (repo, " and ".join(owners)))
            print("  [ERROR] %s is claimed by %s" % (repo, " and ".join(owners)))
        print("          Two rows naming one repo is a conflict, not a division of labour -")
        print("          a lane reading either row concludes it owns the whole thing.")
        print("          Fix: narrow one row, or name the split explicitly in both.")
    else:
        print("  [ok] no repo is claimed by two rows")
    # Shown always: a reader checking ownership wants to see the handovers, and printing them
    # is what makes the under-detection above visible rather than silent.
    for oid, repo, others in notes:
        print("  [note] %s's row names %s while pointing at %s - read as a handover, not a claim"
              % (oid, repo, ", ".join(others)))

    # --- C. a terminal row must point somewhere that exists ---
    dead = []
    for oid, desc, status in rows:
        if not re.search(r"retired|superseded|graduated|consolidat", status, re.I):
            continue
        for t_ in sorted(set(re.findall(r"\b(o\d+)\b", desc)) - {oid}):
            if not (PROJECTS / ("ORCHESTRATOR-DECISIONS-%s.md" % t_)).exists():
                dead.append((oid, t_))
    if dead:
        rc = 1
        for oid, t_ in dead:
            errors.append("%s is terminal and points at %s, which has no OrchDoc" % (oid, t_))
            print("  [ERROR] %s is terminal and points at %s, which has no OrchDoc" % (oid, t_))
    else:
        print("  [ok] every terminal row points at a doc that exists")

    # --- B. reported, never fatal unless --strict ---
    try:
        voc = json.loads((Path(__file__).resolve().parent
                          / "facets_vocabulary.json").read_text(encoding="utf-8"))
        repos = [n for n in voc.get("nested_repos", []) if n]
    except Exception:
        repos = []
    low = text.lower()
    unowned = [r for r in repos if r.lower() not in low]
    # Every name, not the first six: the old `[:6]` printed "7 of 12" beside six names and no
    # word that one was missing.
    print("  [note] %d of %d declared product repo(s) are named in no row: %s"
          % (len(unowned), len(repos), ", ".join(sorted(unowned)) or "-"))
    print("         Not an error - most should not have one. It is the number to watch when a")
    print("         repo starts getting real work and nobody has said whose it is.")
    if unowned and getattr(args, "strict", False):
        rc = 1
        errors.append("--strict: %d declared repo(s) named in no row" % len(unowned))

    if errors:
        print("registry: FAILED - %d error%s - %s"
              % (len(errors), "" if len(errors) == 1 else "s", "; ".join(errors)))
    else:
        print("registry: ok - no repo claimed twice, every terminal row points at a doc "
              "(%d handover note(s), %d repo(s) with no row)" % (len(notes), len(unowned)))
    return rc


def cmd_whoami(args):
    """
    Print THIS session's send_message id, verified by the refusal oracle.

    Two different session identifiers live in the environment and they do not match:
      CLAUDE_CODE_SESSION_ID       the transcript/scratchpad id (a BARE uuid)
      CLAUDE_CODE_HOST_SESSION_ID  what send_message wants (prefixed 'local_')
    """
    host = os.environ.get("CLAUDE_CODE_HOST_SESSION_ID", "")
    tran = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    bound = _bound_orchestrator()
    if bound:
        print("orchestrator         : %s   (from this session's NAME - static, not learned)"
              % bound)
        print()
    print("\u26d4 DO NOT PUBLISH ANYTHING BELOW. ASK THE TOOL:")
    print()
    print("      get_session(session_id='self')   ->  .sessionId IS your send_message id")
    print()
    print("   One call, authoritative, nothing to derive. If you never")
    print("   construct a candidate, the transcript-vs-messaging trap cannot fire. The")
    print("   values below are a CROSS-CHECK, not the answer.")
    print()
    print("   ⛔ AND THE TWO IDS ARE NOT HARD TO TELL APART - a warning in circulation")
    print("   says they look identical in form. They do not: one carries local_ and the")
    print("   other is a bare uuid, which is a free string test. Telling them apart was")
    print("   never the problem. HAVING TWO CANDIDATES was, and self removes that.")
    print()
    print("environment says:")
    print("  send_message id : %s" % (host or "<UNSET>"))
    print("  transcript id   : %s   (NOT a messaging target)" % (tran or "<unset>"))
    print()
    if not host:
        print("[note] CLAUDE_CODE_HOST_SESSION_ID is unset - the env cross-check is")
        print("       unavailable. That does NOT block you: get_session('self') still")
        print("       answers. Do NOT guess from a path.")
        return 0
    if not host.startswith("local_"):
        print("[FAIL] no 'local_' prefix - this is a transcript id, not a messaging id.")
        return 1
    print("[OK]   prefix check passed (free, no tool call - a bare uuid is never a target).")
    print()
    print("CROSS-CHECK, once you have called it:")
    print("  self.sessionId == %s   -> environment agrees; nothing to do" % host)
    print("  anything else                                  -> TRUST 'self', and say so:")
    print("     the env var is wrong in this session, which is worth reporting.")
    print()
    return 0


def cmd_add(args):
    """
    Near-free capture. The cost of a good entry is what makes deferral rational, and
    deferred means dropped - so this writes a STUB in one action and prints the anchor.
    """
    doc = resolve_doc_arg(args.doc)
    if not doc or not doc.exists():
        print("no such doc: %s" % doc, file=sys.stderr)
        return 2
    if refuse_if_not_mine(doc, "add", getattr(args, "not_mine", False)):
        return 1

    import orchdoc_ids
    _pl = orchdoc_ids.bare_pre_labels(getattr(args, "title", "") or "")
    if _pl:
        print("[REFUSE] E-PRELABEL: the title names a script by pre-label %s."
              % ", ".join(sorted({lab for lab, _o in _pl})), file=sys.stderr)
        for _lab in sorted({lab for lab, _o in _pl}):
            print("         %s -> %s" % (_lab, prelabel_advice(_lab)), file=sys.stderr)
        print("         the human reads OrchDocs in the Reader and cannot tell which script a"
              " pre-label means.\n         Keep it inside inline code if it is for your own"
              " notes. Nothing was written.", file=sys.stderr)
        return 1

    if not getattr(args, "allow_stale", False):
        state, n, detail = behind_canonical(doc.parent,
                                            fetch=not getattr(args, "no_fetch", False))
        if state == "behind" and doc_holds_every_canonical_change(doc):
            state = "current"      # this copy already holds all that landed in this file
        if state == "behind":
            print("[REFUSE] this working copy is %d commit(s) behind %s."
                  % (n, CANONICAL_REF), file=sys.stderr)
            print("         `add` allocates the next id by counting the entries in the file"
                  " it can\n         see. From a tree this far back it can hand you an id that"
                  " already\n         belongs to someone else, and nothing downstream would"
                  " catch it.", file=sys.stderr)
            print("\n         Nothing was written.", file=sys.stderr)
            print("\n         DO THIS - a worktree at the ref, which needs no pull:",
                  file=sys.stderr)
            print("           git -C %s worktree add --detach /tmp/od %s"
                  % (doc.parent, CANONICAL_REF), file=sys.stderr)
            print("           cd /tmp/od   # then run the same `add` here", file=sys.stderr)
            print("\n         A pull is NOT the fix and will usually abort: the shared"
                  " checkout carries\n         other sessions' uncommitted work, and"
                  " `--ff-only` refuses rather than\n         clobbering it. Do not move their"
                  " files to unblock it.", file=sys.stderr)
            print("\n         Genuinely need to capture from this tree - offline, or a"
                  " deliberate\n         historical entry? Pass --allow-stale. Then VERIFY the"
                  " id it returns is\n         free on %s before you write it anywhere."
                  % CANONICAL_REF, file=sys.stderr)
            return 1
        if state == "unknown":
            print("[WARN] could not compare this tree to %s (%s)." % (CANONICAL_REF, detail))
            print("       Proceeding. The id below was allocated from the local file only,"
                  " so it\n       is free HERE and unverified anywhere else.")

    if args.kind == "decision":
        human_owners = {"human", "the human", "user", "owner"} | human_owner_names()
        owner = (args.owner or human_name().title()).strip()
        if owner.lower() not in human_owners:
            _who = human_name()
            print("[REFUSE] --kind decision puts this in §2, which is %s'S PLATE - but "
                  "you set --owner %s." % (_who, owner), file=sys.stderr)
            print("         The namespace follows the OWNER, not the word 'decision'. A call "
                  "you made\n         and shipped is your WORK, even though deciding it felt "
                  "like a decision.", file=sys.stderr)
            print("         D<n> = %s has to rule on it.  W<n> = you own it." % _who.title(),
                  file=sys.stderr)
            print("\n         Use:  --kind work --owner %s" % owner, file=sys.stderr)
            print("         Or drop --owner if %s genuinely has to rule on this." % _who.title(),
                  file=sys.stderr)
            return 1

    with _lock(doc):
        text = doc.read_text(encoding="utf-8")
        lines = text.splitlines()
        entries, sections = parse_entries(lines)

        prefix = args.prefix or KIND_PREFIX[args.kind]
        if args.prefix and args.prefix != KIND_PREFIX[args.kind]:
            owner_kinds = [k for k, p in KIND_PREFIX.items() if p == args.prefix]
            print("[REFUSE] --prefix %s does not match --kind %s, which routes to prefix %s."
                  % (args.prefix, args.kind, KIND_PREFIX[args.kind]), file=sys.stderr)
            if owner_kinds:
                print("         Prefix %s belongs to --kind %s. Use that kind."
                      % (args.prefix, "|".join(owner_kinds)), file=sys.stderr)
            else:
                print("         No kind owns prefix %s, so there is no section for it - the "
                      "entry\n         would be placed by KIND and numbered by PREFIX, and the "
                      "placement\n         logic would create a second, UNNUMBERED section at "
                      "the end of the\n         file. Entries there are invisible to review, to "
                      "the sweep and to\n         SECTION_RE. An entry was lost exactly this way." % args.prefix, file=sys.stderr)
            print("         Pass --id %s<n> instead if you want that exact id in the section\n"
                  "         --kind %s routes to." % (args.prefix, args.kind), file=sys.stderr)
            return 1
        if args.id:
            _im = re.match(r"^([A-Z]{1,2})\d", args.id)
            _ip = _im.group(1) if _im else None
            if _ip and _ip != prefix:
                _owner = [k for k, p in KIND_PREFIX.items() if p == _ip]
                if _owner:
                    print("[REFUSE] --id %s starts with %s, but --kind %s files into the "
                          "section for\n         prefix %s. The entry would be NUMBERED %s "
                          "and PLACED where %s belongs."
                          % (args.id, _ip, args.kind, prefix, args.id, prefix),
                          file=sys.stderr)
                    print("         That is how a question lands in the decisions list, "
                          "which the human reads\n         as things needing their ruling.",
                          file=sys.stderr)
                    print("         Prefix %s belongs to --kind %s - use that kind, and the "
                          "id and the\n         section will agree."
                          % (_ip, "|".join(_owner)), file=sys.stderr)
                    return 1
        eid = args.id or next_id(entries, prefix, text)
        if any(e["id"] == eid for e in entries):
            print("[REFUSE] id %s already exists in %s - ids are never reused"
                  % (eid, doc.name), file=sys.stderr)
            return 1

        today = args.date or _now_iso()
        owner = args.owner or (human_name().title()
                               if args.kind in ("decision", "question", "todo", "today")
                               else "orchestrator")

        entry = [
            "",
            "### %s %s - %s" % (STATUS_MARKER["OPEN"], eid,
                                sanitize_field(refuse_if_too_long(args.title, 200, "title"), 200)),
            "",
            "**Status:** OPEN - **Owner:** %s - **Opened:** %s - **Enriched:** NO"
            % (owner, today),
            "",
            "_Stub captured at decision time. Enrich when load is low: paths, why it matters,",
            "the recommendation, and what is blocked until it is answered._",
            "",
        ]

        want = KIND_SECTION[args.kind]
        insert_at, target = _section_insert_at(lines, sections, args.kind, prefix)
        out = lines[:insert_at] + entry + lines[insert_at:]
        if _write_guard_refuses(doc, text, "\n".join(out) + "\n", "add"):
            return 1
        write_doc(doc, "\n".join(out) + "\n")

    return _add_report(args, doc, eid, target, want)


def _section_insert_at(lines, sections, kind, prefix):
    """(insert_at, target_section) for a new entry of `kind`, 0-indexed. Appends a `##`
    heading to `lines` when the doc has no section for it. Shared by `add` and `instruct`."""
    want = KIND_SECTION[kind]
    target = None
    live_num = KIND_SECTION_NUM.get(prefix[0], (None, None))[0]
    if live_num:
        for s in sections:
            if s["level"] == 2 and re.match(r"^\W*\s*§?\s*%s\b" % re.escape(live_num),
                                            s["title"]):
                target = s
                break

    if target is None:
        for s in sections:
            # level 2 only. Matching any heading let the h1 "Orchestrator DECISION
            # Doc" capture every decision and file it at the top of the document.
            # FIRST match, not last: on a schema doc the live section always precedes
            # its §99 counterpart.
            if s["level"] == 2 and want.rstrip("S") in s["title"].upper():
                target = s
                break
    if target is None:
        lines += ["", "## %s" % want, ""]
        insert_at = len(lines)
    else:
        insert_at = len(lines)
        later = [s for s in sections if s["line"] > target["line"]]
        schema = [s for s in later if "§" in s["title"]]
        for s in (schema or later):
            insert_at = s["line"] - 1
            break
    return insert_at, target


def _add_report(args, doc, eid, target, want):
    """`add`'s read-back and receipt, after the write."""
    try:
        _back = doc.read_text(encoding="utf-8", errors="replace")
    except Exception as _e:
        print("[REFUSE] %s: wrote, then could not re-read %s to confirm it (%s). "
              "NOT printing an anchor." % (eid, doc.name, type(_e).__name__),
              file=sys.stderr)
        return 1
    if not re.search(r"^#{2,6} [^\n]*\b%s\b" % re.escape(eid), _back, re.M):
        print("[REFUSE] %s is NOT in %s after the write - no anchor is printed, because an "
              "anchor that does not resolve is worse than none. Nothing was handed to the human."
              % (eid, doc.name), file=sys.stderr)
        return 1

    print("[ADDED] %s" % eid)
    print()
    print("  doc      : %s" % doc.name)
    print("  section  : %s" % (target["title"] if target else want))
    print("  status   : OPEN (unenriched)  (verified by re-reading the file)")
    print()
    _lnk = reader_link(doc.name, eid)
    if _lnk:
        print("PASTE THIS TO THE HUMAN (a Reader link - never a bare id, a path or a line number):")
        print("  [%s](%s)" % (eid, _lnk))
    else:
        print("PASTE THIS TO THE HUMAN (no Reader link could be built for this doc):")
        print("  %s - %s, section \"%s\"" % (eid, doc.name, target["title"] if target else want))
    if args.kind == "decision":
        print()
        print("REMINDER: this is only real if it is in the PLATE BLOCK - that is what the human")
        print("          actually reads. A decision the human cannot see is a decision nobody made.")
    return 0


def own_note_rows(lines, entry):
    """0-based rows of the notes that are `entry`'s OWN, oldest first: the notes the reader
    shows in its span (`parse_notes`, so never one inside a fence), less any whose id names a
    different entry.
    """
    start = entry["line"] - 1
    end = min(start + len(entry["body"].splitlines()), len(lines))
    eid = entry.get("id")
    return [n["line"] - 1 for n in parse_notes("\n".join(lines))
            if start <= n["line"] - 1 < end
            and not (eid and "." in n["nid"] and n["nid"].split(".", 1)[0] != eid)]


def note_slot(lines, entry):
    """(row, stacks): where a new note goes inside `entry`, 0-indexed, and whether it joins a
    run of the entry's own notes (then no blank line goes under it). By the reader's rules:
    straight under the entry's last own note (`own_note_rows`); with none, the slot a note-less
    entry gets (`note_anchor`'s docstring), and never inside a fence.
    """
    own = own_note_rows(lines, entry)
    if own:
        return own[-1] + 1, True             # notes stack, oldest first
    at = _noteless_slot(lines, entry)
    st = fence_states(lines)
    if 0 < at <= len(lines) and st[at - 1][1]:
        at = max((i for i in range(entry["line"] - 1, at) if is_fence_line(lines[i])),
                 default=at)
    return at, False


def note_anchor(lines, entry):
    """Where a new note goes inside `entry`, 0-indexed: `note_slot`'s row. Under the entry's
    own last note when it has one; else beneath the fields, above the prose.
    """
    return note_slot(lines, entry)[0]


def _noteless_slot(lines, entry):
    """The slot an entry with no note of its own gets: beneath the field block, above the prose.
    `note_slot` moves it out of a fence when it falls in one."""
    start = entry["line"] - 1
    end = min(start + len(entry["body"].splitlines()), len(lines))

    i = start + 1
    while i < end and not lines[i].strip():
        i += 1                               # the blank under the heading closes nothing
    if i < end and FIELD_LINE_RE.match(lines[i]):
        while i < end and FIELD_LINE_RE.match(lines[i]):
            i += 1                           # the run of bold field lines
        while i < end and not lines[i].strip():
            i += 1                           # and the blank that closes it
        return i
    # No field block at all - a legacy or half-written entry. Keep the old behaviour rather
    # than guess, and let the caller's re-read verify.
    for j in range(start + 1, end):
        if not lines[j].strip():
            return j + 1
    return end



LANDER_TRAILER = "Orchdoc-Lander"
READER_LANDER = "reader-note"
#: The subject `cmd_note` gives a Reader note landing: "Added D999.n2 on <doc> (<who>)".
READER_NOTE_SUBJECT_RE = re.compile(
    r"^(?:Added|Edited|Deleted)\s+(?:[A-Z]{1,3}\d{1,4}[a-z]?\.)?n\d{1,4}\s+on\s+\S+\s+\(.*\)\s*$")


def _note_entry_id(note, entries):
    """Which entry a note belongs to, or None.

    A scoped id names its entry (`D999.n2` -> D999) - when that entry is in `entries`.
    """
    nid = note["nid"]
    if "." in nid:
        pre = nid.split(".", 1)[0]
        if any(e["id"] == pre for e in entries):
            return pre
    best = None
    for e in entries:
        lo = e["line"]
        hi = lo + len(e["body"].splitlines())
        if lo <= note["line"] < hi and (best is None or lo > best["line"]):
            best = e
    return best["id"] if best else None


def _place_note_line(lines, eid, line, prev_nids=(), next_nids=()):
    """Insert one note line into entry `eid` of `lines` (a list, changed in place).

    Returns None when it was placed, or a sentence saying why it could not be.
    """
    entries = parse_entries(lines)[0]
    match = [e for e in entries if e["id"] == eid]
    if not match:
        return "entry %s is not in this copy" % eid
    if len(match) > 1:
        return "entry %s appears %d times in this copy" % (eid, len(match))
    e = match[0]
    own = set(own_note_rows(lines, e))
    where = {n["nid"]: n["line"] - 1 for n in parse_notes("\n".join(lines))
             if n["line"] - 1 in own}
    for p in reversed(list(prev_nids)):
        if p in where:
            lines.insert(where[p] + 1, line)
            return None
    for q in next_nids:
        if q in where:
            lines.insert(where[q], line)
            return None
    at, stacks = note_slot(lines, e)
    # Same rule as `cmd_note`'s add path: a trailing blank keeps a first note from being
    # absorbed into the paragraph below it.
    lines[at:at] = [line] if stacks else [line, ""]
    return None


def _free_note_id(lines, nid, avoid=()):
    """The next free id in the same scheme as `nid`, on `lines`, skipping anything in `avoid`.

    `avoid` holds the ids a disk copy is using, so a note renumbered on the way to the ref can
    never take an id that some OTHER note on that disk copy still carries.
    """
    pre = nid.split(".", 1)[0] if "." in nid else None
    cand = next_note_id(lines, pre)
    while cand in avoid:
        head, num = cand.rsplit("n", 1)
        cand = "%sn%d" % (head, int(num) + 1)
    return cand


def _note_pairs(text):
    """{(nid, text)} of every note in `text`. `parse_notes` without the per-line dicts.

    Fence-aware for the same reason `parse_notes` is. Fast on a 2 MB document because only a
    line holding the marker word is matched against the full pattern.
    """
    out, in_fence = set(), False
    for raw in text.split("\n"):
        if raw.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or NOTE_MARK not in raw:
            continue
        m = NOTE_LINE_RE.match(raw)
        if m:
            out.add((m.group("nid"), m.group("text").strip()))
    return out


def _without_notes(text):
    """`text` with every note line removed, trailing spaces dropped and blank runs collapsed.
    """
    kept = [l.rstrip() for l in text.replace("\r\n", "\n").split("\n")
            if not (NOTE_MARK in l and NOTE_LINE_RE.match(l))]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def _past_versions(rel, commit, depth=80):
    """Yield the text of each version of `rel` at or before `commit`, newest first, up to `depth`.

    Through one `cat-file --batch` per few versions, so a caller that stops early reads little:
    an OrchDoc can be two megabytes.
    """
    import subprocess as _sp
    rc, log, _ = git(["log", "--format=%H", "-n", str(depth), commit, "--", rel])
    if rc != 0:
        return
    shas = [s for s in log.split() if s]
    for i in range(0, len(shas), 8):
        names = "".join("%s:%s\n" % (s, rel) for s in shas[i:i + 8])
        p = _sp.run(["git", "-C", str(PROJECTS), "cat-file", "--batch"], input=names.encode(),
                    capture_output=True, timeout=120, creationflags=flags())
        if p.returncode != 0:
            return
        buf, pos = p.stdout, 0
        while pos < len(buf):
            nl = buf.find(b"\n", pos)
            if nl < 0:
                return
            head = buf[pos:nl].split()
            pos = nl + 1
            if len(head) != 3 or head[1] != b"blob":
                continue                              # "<name> missing"
            size = int(head[2])
            body = buf[pos:pos + size].decode("utf-8", errors="replace")
            pos += size + 1
            yield body.replace("\r\n", "\n")


def _notes_on_past_versions(rel, commit, pairs, depth=80):
    """Which (nid, text) pairs were a note in some version of `rel` at or before `commit`."""
    left, found = set(pairs), set()
    if not left:
        return found
    for text in _past_versions(rel, commit, depth):
        if NOTE_MARK not in text:
            continue
        hit = left & _note_pairs(text)
        found |= hit
        left -= hit
        if not left:
            break
    return found


def merge_canonical_notes(canon_txt, mine_txt, landed_before=frozenset()):
    """B1: every note on the canonical copy must be in the copy about to land. -> (text, changes, refusals)
    """
    canon_notes = parse_notes(canon_txt)
    if not canon_notes:
        return None, [], []
    mine_by = defaultdict(list)
    for n in parse_notes(mine_txt):
        mine_by[n["nid"]].append(n)
    canon_lines = canon_txt.split("\n")
    lines = mine_txt.split("\n")
    c_entries = parse_entries(canon_lines)[0]
    eid_of = [_note_entry_id(cn, c_entries) for cn in canon_notes]

    refusals, missing, replace = [], [], []
    for k, cn in enumerate(canon_notes):
        hits = mine_by.get(cn["nid"], [])
        if not hits:
            missing.append(k)
            continue
        same = [h for h in hits if h["text"] == cn["text"]]
        if not same:
            h = hits[0]
            if (cn["nid"], h["text"]) in landed_before:
                replace.append((k, h, "text"))
            else:
                refusals.append({"kind": "unlanded", "nid": cn["nid"], "entry": eid_of[k],
                                 "canon": cn["text"], "mine": h["text"]})
            continue
        if cn["ack"] and not any(h["ack"] for h in same):
            replace.append((k, same[0], "ack"))
    if refusals:
        return None, [], refusals

    changes = []
    # Replacements first: a line replaced in place moves nothing, so the line numbers parsed
    # above still hold for it.
    for k, h, what in replace:
        cn = canon_notes[k]
        lines[h["line"] - 1] = canon_lines[cn["line"] - 1]
        changes.append((cn["nid"], eid_of[k] or "?", what))

    for k in missing:
        cn = canon_notes[k]
        eid = eid_of[k]
        if not eid:
            refusals.append({"kind": "entry", "nid": cn["nid"], "entry": None,
                             "canon": cn["text"], "mine": ""})
            continue
        sib = [j for j in range(len(canon_notes)) if eid_of[j] == eid]
        pos = sib.index(k)
        prev = [canon_notes[j]["nid"] for j in sib[:pos]]
        nxt = [canon_notes[j]["nid"] for j in sib[pos + 1:]]
        why = _place_note_line(lines, eid, canon_lines[cn["line"] - 1], prev, nxt)
        if why:
            refusals.append({"kind": "entry", "nid": cn["nid"], "entry": eid,
                             "canon": cn["text"], "mine": "", "why": why})
            continue
        changes.append((cn["nid"], eid, "note"))
    if refusals:
        return None, [], refusals
    if not changes:
        return None, [], []
    return "\n".join(lines), changes, []


def _b1_refusal_lines(refusals, ref, doc):
    """What B1 prints for each refusal - the advice differs by kind, and one kind must never be
    told to take the ref's line."""
    out = []
    for r in refusals[:6]:
        if r["kind"] == "unlanded":
            out.append("- note %s in your copy has text that was never on %s:" % (r["nid"], ref))
            out.append("    on %s: %s" % (ref, r["canon"][:150]))
            out.append("    in your copy:  %s" % r["mine"][:150])
            out.append("  That is an edit of the human's that has not landed. Do NOT take the line from")
            out.append("  %s - that would revert the human's words - and do not rewrite it yourself: a"
                       % ref)
            out.append("  session cannot write a note. Only the Reader lands the human's words:")
            out.append("    1. If the Reader's landing queue (%s) holds a save for %s,"
                       % (READER_LANDING_REL, doc.name))
            out.append("       it lands the edit on its own. Wait for it, then land again.")
            out.append("    2. Otherwise copy the text exactly, put it in your report to the human and")
            out.append("       the doc's owner, and ask the human to save the note again from %s:"
                       % (reader_link(doc.name, r["entry"]) if r.get("entry") else "the Reader"))
            out.append("         python <scripts>/orchdoc.py note --doc %s --show %s"
                       % (doc.name, r["nid"]))
            out.append("       Leave this copy as it is until the human's save has landed.")
        else:
            where = ("entry %s" % r["entry"]) if r.get("entry") else "no entry"
            out.append("- note %s belongs to %s on %s, and your copy cannot hold it%s."
                       % (r["nid"], where, ref, (": " + r["why"]) if r.get("why") else ""))
            out.append("    note text: %s" % r["canon"][:150])
            out.append("  Put the entry back in your copy with the note under it, exactly as it")
            out.append("  is on %s:  git show %s:%s" % (ref, ref, doc.name))
    return out


def _merge_notes_before_landing(doc, rel, ref, dry_run=False):
    """B1 inside `cmd_commit`: put back every canonical note the landing copy lacks. -> (ok, dry_text).

    `ref` is the PINNED commit id, not a branch name: the same object gate 1 reads. `ok` False
    means REFUSE (it has already said why). `dry_text` is the merged text on a dry run - which
    writes nothing, so the caller hashes that text instead of the file.
    """
    rc, canon, _ = git(["show", "%s:%s" % (ref, rel)])
    if rc != 0:
        # Not on the ref yet (a first landing has no notes to keep), or unreadable - and gate 1
        # refuses an unreadable canonical copy on its own, a few lines further on.
        return True, None
    label = CANONICAL_REF
    # The history is read OUTSIDE the lock: it can take seconds, and every other writer of
    # this doc waits on the lock with a 10-second limit.
    try:
        first = doc.read_text(encoding="utf-8")
    except OSError as ex:
        print("  [REFUSE] B1 - could not read %s (%s)." % (doc.name, ex))
        return False, None
    _n, _c, pre = merge_canonical_notes(canon, first)
    pairs = [(r["nid"], r["mine"]) for r in pre if r["kind"] == "unlanded"]
    past = _notes_on_past_versions(rel, ref, pairs) if pairs else set()
    try:
        with _lock(doc):
            mine = doc.read_text(encoding="utf-8")
            new, changes, refusals = merge_canonical_notes(canon, mine, past)
            if not refusals and new is not None and not dry_run:
                write_doc(doc, new, expect=mine)
    except DocPathError as ex:
        print("  [REFUSE] B1 - %s changed while its notes were being merged, so nothing was"
              % doc.name)
        print("           written or pushed. Re-run the landing. (%s)" % str(ex)[:160])
        return False, None
    if refusals:
        print("  [REFUSE] B1 - the human's notes on %s cannot all be kept by this landing, and a"
              % label)
        print("           landing never deletes a note. Nothing was written or pushed.")
        for line in _b1_refusal_lines(refusals, label, doc):
            print("           %s" % line)
        print("           A note is removed only by `orchdoc.py note --delete`.")
        try:
            log_refusal(doc, [Finding("E-NOTELOSS", 0, "%s %s" % (r["kind"], r["nid"]))
                              for r in refusals])
        except Exception:
            pass
        return False, None
    for nid, eid, what in changes:
        if what == "ack":
            print("  [merged] the ack on note %s from %s - your copy had the note without it"
                  % (nid, label))
        elif what == "text":
            print("  [merged] note %s: your copy had an older text that was on %s before;"
                  % (nid, label))
            print("           took the current text from %s." % label)
        else:
            print("  [merged] note %s (on %s) from %s - it was not in your copy"
                  % (nid, eid, label))
    if changes and dry_run:
        print("           (dry run: merged in memory only; your file was not written)")
    return True, (new if (dry_run and new is not None) else None)


def _renumber_note_line(line, old, new):
    """The same note line with its id changed in the machine half, and nothing else."""
    out = re.sub(r"(<!--\s*" + re.escape(NOTE_MARK) + r"\s+)" + re.escape(old)
                 + r"(?=[\s-])", lambda m: m.group(1) + new, line, count=1)
    got = NOTE_LINE_RE.match(out)
    if not got or got.group("nid") != new:
        raise ValueError("could not renumber %s to %s" % (old, new))
    return out


def _item_lines(block):
    """An entry's lines as the "retire" landing compares them: blank lines out, acks out.

    An ack is an orchestrator saying it read a note, not a change to the item (see
    `note_without_ack`), and blank lines move about when notes are placed. Any other
    difference means the two copies are not the same item."""
    return [note_without_ack(l).rstrip() if NOTE_LINE_RE.match(l) else l.rstrip()
            for l in block if l.strip()]


def _catch_up_next(eid):
    branch = CANONICAL_REF.split("/", 1)[-1]
    return ("This computer's copy normally catches up with %s within a few minutes. Reload the "
            "page then to see %s as %s has it, and delete it again if you still want to"
            % (branch, eid, branch))


def _heading_after_id(line, eid):
    """What an entry heading says after its id, or None.
    """
    m = re.search(r"(?<![\w.])%s(?!\w)" % re.escape(eid), line or "")
    return line[m.end():].strip() if m else None


def _rename_heading(line, old, new):
    """An entry heading line with its id changed from `old` to `new`, and nothing else."""
    out = re.sub(r"(?<![\w.])%s(?!\w)" % re.escape(old), new, line, count=1)
    if out == line:
        raise ValueError("could not rename %s to %s in its heading" % (old, new))
    return out


def _rename_entry(text, old, new, head):
    """Unchanged when that heading is not in `text` exactly once, so an entry of the same id
    written by someone else is never touched."""
    lines = text.split("\n")
    hits = [i for i, l in enumerate(lines) if l.strip() == head.strip()]
    if len(hits) != 1:
        return text
    i = hits[0]
    ents = [e for e in parse_entries(lines)[0] if e["line"] == i + 1]
    end = _entry_end(ents[0]) if ents else i + 1
    lines[i] = _rename_heading(lines[i], old, new)
    pref = old + "."
    for k in range(i + 1, min(end, len(lines))):
        m = NOTE_LINE_RE.match(lines[k]) if NOTE_MARK in lines[k] else None
        if m and m.group("nid").startswith(pref):
            lines[k] = _renumber_note_line(lines[k], m.group("nid"),
                                           new + m.group("nid")[len(old):])
    return "\n".join(lines)


def _rename_op(op, old, new):
    """-> True when the operation acted on that entry or one of its notes.
    """
    if not isinstance(op, dict):
        return False
    hit, pref = False, old + "."
    if op.get("entry") == old:
        op["entry"], hit = new, True
    nid = op.get("nid")
    if isinstance(nid, str) and nid.startswith(pref):
        nn = new + nid[len(old):]
        for k in ("line", "prev"):
            if op.get(k):
                op[k] = _renumber_note_line(op[k], nid, nn)
        op["nid"], hit = nn, True
    blk = op.get("block")
    if hit and blk and len(blk) > 1 and op.get("entry") == new:
        op["block"] = [_rename_heading(l, old, new) if k == 1 else l for k, l in enumerate(blk)]
    t = op.get("today")
    if isinstance(t, dict) and t.get("eid") == old:
        t["eid"], hit = new, True
        if t.get("block") and len(t["block"]) > 1:
            t["block"] = [_rename_heading(l, old, new) if k == 1 else l
                          for k, l in enumerate(t["block"])]
    return hit


def _note_onto_canonical(canon, mine, op, landed_before=frozenset(), avoid=(), skip=()):
    """
    -> dict(text, nid, carried, skipped, stuck, removed, renumbered), or dict(error=...).

    `op` is {"op": "add"|"edit"|"delete", "nid", "line" (the rendered note, not for delete),
    "prev" (the line as it was on disk BEFORE an edit or delete), "entry"}. A deleted
    instruction is "withdraw" (its note still on the item) or "retire" (none left; "item" is the
    item's lines as they were before the delete), both with "who" and "when".
    """
    lines = canon.split("\n")
    kind, nid = op.get("op"), op.get("nid")
    newline, eid, prev = op.get("line"), op.get("entry"), op.get("prev")
    carried, skipped, stuck, removed, renumbered = [], [], [], [], []

    d_lines = mine.split("\n")
    d_entries = parse_entries(d_lines)[0]
    d_notes = parse_notes(mine)
    d_eid = [_note_entry_id(dn, d_entries) for dn in d_notes]
    avoid = set(n["nid"] for n in d_notes) | set(e["id"] for e in d_entries) | set(avoid)
    skip = set(skip)
    d_live = set(e["id"] for e in d_entries if not e.get("archived"))
    filed = set(e["id"] for e in d_entries if e.get("archived")) - d_live
    if kind in ("withdraw", "retire"):
        filed.discard(eid)
    c_live = None

    # 0. The entry this change creates (an `instruct` W item, a §2.0 add), when main has since
    # given its id to a different entry: rename it to the next id free on main and here.
    own_nid = nid
    entries = []
    block = op.get("block")
    t_op = op.get("today") if kind == "today" else None
    made = None
    if block and eid and len(block) > 1:
        made, made_head = eid, block[1]
    elif isinstance(t_op, dict) and t_op.get("act") == "add" and t_op.get("eid") \
            and len(t_op.get("block") or ()) > 1:
        made, made_head = t_op["eid"], t_op["block"][1]
    if made:
        c_ents0 = parse_entries(lines)[0]
        have = [e for e in c_ents0 if e["id"] == made]
        main_head = lines[have[0]["line"] - 1] if have else ""
        own_there = bool(have) and bool(own_nid) and bool(newline) and any(
            c["nid"] == own_nid and c["text"] == _note_text(newline)
            for c in parse_notes(canon))
        if have and main_head.strip() != made_head.strip() \
                and (own_there or (not have[0].get("archived") and
                     _heading_after_id(main_head, made) == _heading_after_id(made_head, made))):
            if block and eid:
                block = [main_head if k == 1 else l for k, l in enumerate(block)]
            else:
                t_op = dict(t_op, block=[main_head if k == 1 else l
                                         for k, l in enumerate(t_op["block"])])
        elif have and main_head.strip() != made_head.strip():
            pre = re.match(r"[A-Z]+", made).group(0)
            new_id = next_id(c_ents0, pre, canon)
            while new_id in avoid:
                new_id = "%s%d" % (pre, int(new_id[len(pre):]) + 1)
            new_head = _rename_heading(made_head, made, new_id)
            entries.append((made, new_id, made_head, new_head))
            if block and eid:
                block = [new_head if k == 1 else l for k, l in enumerate(block)]
                if nid and nid.startswith(eid + "."):
                    new_nid = new_id + nid[len(eid):]
                    if newline:
                        newline = _renumber_note_line(newline, nid, new_nid)
                    nid = new_nid
                eid = new_id
            else:
                t_op = dict(t_op, eid=new_id,
                            block=[new_head if k == 1 else l for k, l in enumerate(t_op["block"])])

    # 1. Earlier stuck notes first, so the new one lands after them, as it was written.
    for k, dn in enumerate(d_notes):
        if dn["nid"] == own_nid:
            continue                                  # the operation itself, below
        if dn["nid"] in skip:
            continue                                  # its own queued landing carries it
        if kind == "withdraw" and d_eid[k] == eid:
            continue                                  # its deletion note: the op writes it
        if entries and d_eid[k] == entries[0][0]:
            # In the entry being renamed: main's entry of that id is someone else's, so the
            # note must not go there. Its own queued change lands it once the entry is renamed.
            stuck.append({"nid": dn["nid"], "text": dn["text"],
                          "why": "its entry %s lands as %s, and the note is not on main yet"
                                 % (entries[0][0], entries[0][1])})
            continue
        here = [c for c in parse_notes("\n".join(lines)) if c["nid"] == dn["nid"]]
        same = [c for c in here if c["text"] == dn["text"]]
        acts = not same or (dn["ack"] and not any(c["ack"] for c in same))
        if acts and d_eid[k] in filed:
            if c_live is None:
                c_live = set(e["id"] for e in parse_entries(canon.split("\n"))[0]
                             if not e.get("archived"))
            if d_eid[k] in c_live:
                if not same:
                    stuck.append({"nid": dn["nid"], "text": dn["text"],
                                  "why": "%s is in §99 on this computer but still live on %s, "
                                         "so this note was not put on it"
                                         % (d_eid[k], CANONICAL_REF)})
                continue
        if same:
            if dn["ack"] and not any(c["ack"] for c in same):
                i = same[0]["line"] - 1
                removed.append(lines[i])
                lines[i] = d_lines[dn["line"] - 1]
                carried.append(dn["nid"] + " (ack)")
            continue
        if (dn["nid"], dn["text"]) in landed_before:
            if here:
                skipped.append("%s (an older text of a note that has since been edited on %s)"
                               % (dn["nid"], CANONICAL_REF))
            else:
                stuck.append({"nid": dn["nid"], "text": dn["text"],
                              "why": "it was on %s and has been removed there; it was not put "
                                     "back" % CANONICAL_REF})
            continue
        e = d_eid[k]
        if not e:
            stuck.append({"nid": dn["nid"], "text": dn["text"], "why": "it sits in no entry"})
            continue
        line, new_id = d_lines[dn["line"] - 1], dn["nid"]
        if here:
            new_id = _free_note_id(lines, dn["nid"], avoid)
            line = _renumber_note_line(line, dn["nid"], new_id)
        sib = [j for j in range(len(d_notes)) if d_eid[j] == e]
        pos = sib.index(k)
        why = _place_note_line(lines, e, line,
                               [d_notes[j]["nid"] for j in sib[:pos]],
                               [d_notes[j]["nid"] for j in sib[pos + 1:]])
        if why:
            stuck.append({"nid": dn["nid"], "text": dn["text"], "why": why})
            continue
        carried.append(new_id if new_id == dn["nid"] else "%s (as %s)" % (dn["nid"], new_id))
        avoid.add(new_id)
        if new_id != dn["nid"]:
            renumbered.append((dn["nid"], new_id, dn["text"]))

    if block and eid:
        c_ents, c_secs = parse_entries(lines)
        have = [e for e in c_ents if e["id"] == eid]
        if have:
            if lines[have[0]["line"] - 1].strip() != block[1].strip():
                return {"error": "%s is already a different entry on %s" % (eid, CANONICAL_REF)}
        else:
            at, _t = _section_insert_at(lines, c_secs, op.get("block_kind") or "work",
                                        re.match(r"[A-Z]+", eid).group(0))
            lines[at:at] = list(block)

    # 2. The operation.
    final = nid
    hits = [c for c in parse_notes("\n".join(lines)) if c["nid"] == nid]
    if len(hits) > 1:
        return {"error": "note %s appears %d times on %s - fix that by hand first"
                         % (nid, len(hits), CANONICAL_REF)}
    main_line = lines[hits[0]["line"] - 1] if hits else None
    same_note = bool(hits) and prev is not None \
        and note_without_ack(main_line) == note_without_ack(prev)
    if kind == "today":
        # A §2.0 change: the same change, made on the ref's text (under its new id when step 0
        # renamed the item it adds).
        before = list(lines)
        why = apply_today(lines, t_op or {})
        if why:
            return {"error": "§2.0 on %s: %s" % (CANONICAL_REF, why)}
        now = set(lines)
        removed.extend(l for l in before if l.strip() and l not in now)
    elif kind == "withdraw":
        branch = CANONICAL_REF.split("/", 1)[-1]
        if not (hits and same_note):
            return {"error": "instruction %s on %s is not the one deleted here" % (nid, CANONICAL_REF),
                    "plain": ("instruction %s on %s is not the one on this page" % (nid, branch))
                    if hits else ("instruction %s has not reached %s yet" % (nid, branch)),
                    "next": _catch_up_next(eid or nid) if hits else None}
        item = instruction_item(lines, eid) if eid else "no entry for %s" % nid
        if not isinstance(item, tuple):
            return {"error": "%s on %s: %s" % (eid, CANONICAL_REF, item),
                    "plain": "on %s, %s" % (branch, item)}
        if op.get("ins") is not None:
            theirs = set((x["nid"], x["text"]) for x in item[1])
            mine_ins = set(tuple(x) for x in op["ins"])
            extra = sorted(x[0] for x in theirs - mine_ins)
            if extra:
                return {"error": "%s on %s holds instruction %s, which this copy does not, so it "
                                 "was not deleted there" % (eid, CANONICAL_REF, extra[0]),
                        "plain": "%s has an instruction on %s (%s) that this page does not show"
                                 % (branch, eid, ", ".join(extra)),
                        "next": _catch_up_next(eid)}
            lack = sorted(x[0] for x in mine_ins - theirs)
            if lack:
                return {"error": "instruction %s is not on %s's %s, so it was not deleted there"
                                 % (lack[0], CANONICAL_REF, eid),
                        "plain": "instruction %s has not reached %s yet" % (lack[0], branch)}
        before = list(lines)
        got = withdraw_doc_instruction(lines, nid, op.get("who") or "", op.get("when") or "",
                                       seen=op.get("seen"))
        if not isinstance(got, tuple):
            return {"error": "instruction %s could not be moved to §99 on %s" % (nid, CANONICAL_REF),
                    "plain": ("%s could not be moved to §99 on %s: %s" % (eid, branch, got))
                    if isinstance(got, str) else None}
        now = set(lines)
        removed.extend(l for l in before if l.strip() and l not in now)
    elif kind == "retire":
        branch = CANONICAL_REF.split("/", 1)[-1]
        if not newline or not NOTE_LINE_RE.match(newline) or not eid:
            return {"error": "no note line to land for %s" % nid}
        item = instruction_item(lines, eid)
        if not isinstance(item, tuple):
            return {"error": "%s on %s: %s" % (eid, CANONICAL_REF, item),
                    "plain": "on %s, %s" % (branch, item)}
        if item[1]:
            return {"error": "%s on %s holds instruction %s, which this copy does not, so it was "
                             "not deleted there" % (eid, CANONICAL_REF, item[1][0]["nid"]),
                    "plain": "%s has an instruction on %s (%s) that this page does not show"
                             % (branch, eid, ", ".join(x["nid"] for x in item[1])),
                    "next": _catch_up_next(eid)}
        theirs = lines[item[0]["line"] - 1:_entry_end(item[0])]
        if _item_lines(theirs) != _item_lines(op.get("item") or []):
            return {"error": "%s on %s has changed since this copy was read, so it is not the "
                             "item deleted here" % (eid, CANONICAL_REF),
                    "plain": "%s on %s is not the same as %s on this computer, so deleting it "
                             "here could file away work done on it" % (eid, branch, eid),
                    "next": _catch_up_next(eid)}
        if hits:
            # The id is taken there by another note: land the deletion note under the next
            # free one, as an added note would.
            final = _free_note_id(lines, nid, avoid)
            newline = _renumber_note_line(newline, nid, final)
        before = list(lines)
        why = withdraw_instruction_item(lines, eid, op.get("who") or "", newline)
        if why:
            return {"error": "%s could not be moved to §99 on %s: %s" % (eid, CANONICAL_REF, why)}
        now = set(lines)
        removed.extend(l for l in before if l.strip() and l not in now)
    elif kind == "delete":
        if hits and same_note:
            i = hits[0]["line"] - 1
            removed.append(lines[i])
            del lines[i]
            if 0 < i < len(lines) and not lines[i].strip() and not lines[i - 1].strip():
                del lines[i]
        elif hits:
            skipped.append("%s on %s is a different note from the one deleted here, so it was "
                           "left alone" % (nid, CANONICAL_REF))
    elif kind in ("add", "edit"):
        if not newline or not NOTE_LINE_RE.match(newline):
            return {"error": "no note line to land for %s" % nid}
        if hits and kind == "edit" and same_note:
            i = hits[0]["line"] - 1
            removed.append(lines[i])
            lines[i] = newline
        elif hits and note_without_ack(main_line) == note_without_ack(newline):
            pass                                      # already there, word for word
        else:
            if hits:
                final = _free_note_id(lines, nid, avoid)
                newline = _renumber_note_line(newline, nid, final)
            if not eid:
                return {"error": "cannot tell which entry note %s belongs to" % nid}
            why = _place_note_line(lines, eid, newline)
            if why:
                return {"error": "note %s: %s on %s" % (nid, why, CANONICAL_REF)}
    else:
        return {"error": "unknown note operation %r" % kind}

    text = "\n".join(lines)
    gone = _lines_lost(canon, text, removed)
    if gone:
        return {"error": "building the landing from %s would still remove %d line(s) that are "
                         "on it - first: %s" % (CANONICAL_REF, len(gone), gone[0].strip()[:80])}
    return {"text": text, "nid": final, "carried": carried, "skipped": skipped,
            "stuck": stuck, "removed": removed, "renumbered": renumbered, "entries": entries}


def _lines_lost(before, after, allowed=()):
    """Non-blank lines of `before` that `after` does not have, other than `allowed`.

    A note line counts as present when the same note is there with or without an ack - acking
    rewrites the line and is not a loss.
    """
    out = set(after.split("\n"))
    out_norm = set(note_without_ack(l) for l in out if NOTE_MARK in l)
    ok = set(allowed)
    return [l for l in before.split("\n")
            if l.strip() and l not in out and l not in ok
            and not (NOTE_MARK in l and NOTE_LINE_RE.match(l)
                     and note_without_ack(l) in out_norm)]


def _hash_text(text, rel):
    """`git hash-object -w` for text that is not a file on disk. -> (rc, blob, err).

    Bytes in, not text: a text-mode pipe on Windows turns every newline into CRLF on the way
    in, and the blob would then differ from the same text written by `write_doc`.
    """
    import subprocess as _sp
    p = _sp.run(["git", "-C", str(PROJECTS), "hash-object", "-w", "--stdin", "--path", rel],
                input=text.encode("utf-8"), capture_output=True, timeout=60,
                creationflags=flags())
    return (p.returncode, p.stdout.decode("utf-8", "replace").strip(),
            p.stderr.decode("utf-8", "replace").strip())


def _apply_note_fixes(text, fixes):
    """`text` with each (old_nid, new_nid, note text) renumbering applied to the one note line
    that has that id and that text. A fix that matches no line, or two, changes nothing."""
    for old, new, ntext in fixes or ():
        ids = [n for n in parse_notes(text) if n["nid"] == old and n["text"] == ntext]
        if len(ids) == 1:
            dl = text.split("\n")
            i = ids[0]["line"] - 1
            dl[i] = _renumber_note_line(dl[i], old, new)
            text = "\n".join(dl)
    return text


def _a2_refusal(cur, landed, rel, commit, chain_base=None):
    chain_ok = bool(chain_base) and chain_base in _version_blobs(rel, commit)
    l_by = defaultdict(list)
    for n in parse_notes(landed):
        l_by[n["nid"]].append(n)
    older = []
    for n in parse_notes(cur):
        there = l_by.get(n["nid"], [])
        same = [x for x in there if x["text"] == n["text"]]
        if same:
            if n["ack"] and not any(x["ack"] for x in same):
                return "the ack on note %s is on this computer only" % n["nid"]
        elif there or chain_ok:
            older.append((n["nid"], n["text"]))
        else:
            return "note %s on this computer is not in what landed" % n["nid"]
    if older:
        known = _notes_on_past_versions(rel, commit, older)
        for pair in older:
            if pair not in known:
                return "note %s on this computer has text that is on no commit" % pair[0]
    if chain_ok:
        return ""
    mine_bare = _without_notes(cur)
    l_set = set(landed.split("\n"))
    extra = [l for l in cur.split("\n") if l.strip() and l not in l_set
             and not (NOTE_MARK in l and NOTE_LINE_RE.match(l))]
    never = set(extra)                      # lines of `extra` no version read so far has had
    for v in _past_versions(rel, commit):
        if _without_notes(v) == mine_bare:
            return ""
        if never:
            never.difference_update(v.split("\n"))
    example = [l for l in extra if l in never] or extra
    c_set = set(cur.split("\n"))
    miss = [l for l in landed.split("\n") if l.strip() and l not in c_set
            and not (NOTE_MARK in l and NOTE_LINE_RE.match(l))]
    return ("this computer's copy, notes aside, matches no version on %s - it "
            "holds an edit that has not landed%s"
            % (CANONICAL_REF,
               (" (for example the line: %s)" % example[0].strip()[:70]) if example
               else " (for example it lacks: %s)" % miss[0].strip()[:70]
               if miss else ""))


def _refresh_disk_after_landing(doc, rel, landed, commit, fixes=(), only_if=None, chain=None,
                                efixes=()):
    """
    The disk copy is replaced only when
      * every note on it is in the landed text by id and text, ack included - or is an OLDER
        text of a note the landed text still has under that id, and that older text was on the
        ref once (so it is in the history, and the note itself is not going anywhere); and
      * the disk copy with its note lines taken out equals, AS A WHOLE TEXT, some version of the
        file at or before `commit`, also with note lines taken out.
    """
    try:
        with _lock(doc):
            try:
                disk = doc.read_text(encoding="utf-8")
            except OSError as ex:
                return False, "could not re-read this computer's copy (%s)" % type(ex).__name__
            cur = disk
            for old, new, head, _new_head in efixes or ():
                cur = _rename_entry(cur, old, new, head)
            cur = _apply_note_fixes(cur, fixes)
            if cur != disk:
                write_doc(doc, cur, expect=disk)
            base = None
            if chain is not None:
                try:
                    got = chain()
                except Exception:
                    got = None
                if got and got[0] and got[1] and got[0] in (_text_blob(disk), _text_blob(cur)):
                    base = got[1]
            if only_if is not None and cur.rstrip("\n") != only_if.rstrip("\n"):
                return False, ("later changes on this computer are still waiting to land, and "
                               "the copy is refreshed when the last of them lands")
            if cur.rstrip("\n") == landed.rstrip("\n"):
                return True, ""
        why = _a2_refusal(cur, landed, rel, commit, chain_base=base)
        if why:
            return False, why
        with _lock(doc):
            write_doc(doc, landed if landed.endswith("\n") else landed + "\n", expect=cur)
        return True, ""
    except DocPathError as ex:
        return False, "this computer's copy changed while it was being refreshed (%s)" % ex


def _cmd_note_list(args, doc):
    """Read notes. `--unseen` keeps only the ones nobody has acknowledged.
    """
    if (args.doc or "").strip().lower() == "all":
        docs = sorted(PROJECTS.glob("ORCHESTRATOR-DECISIONS-*.md"))
    else:
        docs = [doc]

    unseen_only = bool(getattr(args, "unseen", False))
    out, total = [], 0
    for d in docs:
        lines = d.read_text(encoding="utf-8", errors="replace").splitlines()
        notes = parse_notes("\n".join(lines))
        entries, _ = parse_entries(lines)

        if args.id:
            match = [e for e in entries if e["id"] == args.id]
            if not match:
                if len(docs) == 1:
                    _note_fail(args, "no entry with id %s in %s" % (args.id, d.name))
                    return 1
                continue
            lo = match[0]["line"]
            hi = lo + len(match[0]["body"].splitlines())
            notes = [n for n in notes if lo <= n["line"] < hi]

        # name the ENTRY each note sits in, so a reader link is printable
        for n in notes:
            n["doc"] = d.name
            n["entry"] = ""
            for e in entries:
                lo = e["line"]
                hi = lo + len(e["body"].splitlines())
                if lo <= n["line"] < hi:
                    n["entry"] = e["id"]
                    break

        if unseen_only:
            notes = [n for n in notes if not n.get("ack")]
        total += len(notes)
        if notes:
            out.append((d, notes))

    if args.json:
        print(json.dumps({"ok": True, "unseen_only": unseen_only,
                          "docs_examined": len(docs), "count": total,
                          "notes": [n for _d, ns in out for n in ns]}))
        return 0

    label = "unacknowledged note" if unseen_only else "note"
    scope = "all docs" if len(docs) > 1 else docs[0].name
    print("orchdoc note - %s - %d %s(s)" % (scope, total, label))
    if not total:
        print("  examined %d doc(s)%s." % (len(docs), " - nothing unacknowledged"
                                           if unseen_only else " - no notes"))
        return 0
    for d, notes in out:
        slug = d.name.replace("ORCHESTRATOR-DECISIONS-", "").replace(".md", "")
        if len(docs) > 1:
            print("")
            print("  %s" % d.name)
        for n in notes:
            where = n.get("entry") or "?"
            print("  %-5s %-6s %s (%s)" % (n["nid"], where, n["who"], n["when"]))
            print("        " + ("\n" + " " * 8).join(note_display(n).split("\n")))
            if n.get("ack"):
                print("        seen: %s" % n["ack"])
            link = reader_link(d.name, where) if where != "?" else ""
            print("        %s" % link)
            if not n.get("ack"):
                print("        mark seen: orchdoc.py note --doc %s --note %s --ack"
                      % (slug, n["nid"]))
    return 0

def _undo_unlanded_delete(doc, op, built):
    """Main refused a deleted instruction: put this computer's copy back as it was. -> restored?
    """
    branch = CANONICAL_REF.split("/", 1)[-1]
    op["plain"] = built.get("plain") or ("it could not be applied to %s: %s"
                                         % (branch, built.get("error")))
    op["next"] = built.get("next")
    if op.get("pre") is None or op.get("post") is None:
        return False
    try:
        with _lock(doc):
            write_doc(doc, op["pre"], expect=op["post"])
    except (DocPathError, OSError, SystemExit):
        return False
    op["restored"] = True
    return True


def _has_origin():
    """Whether the workspace is a git repo with a remote called origin, the one notes land on."""
    import subprocess as _sp
    try:
        return _sp.run(["git", "-C", str(PROJECTS), "remote", "get-url", "origin"],
                       capture_output=True, timeout=30, creationflags=flags()).returncode == 0
    except Exception:
        return False


def _unlanded_delete_words(detail):
    """A deleted instruction that did not reach main, for a reason other than main refusing it,
    in words for the page. -> the dict `_undo_unlanded_delete` reads.

    `detail` is what `land_note` said, or that it raised. A fetch that failed means main was
    never reached, and the page says just that; anything else is rare enough that the lander's
    own words go in brackets, for whoever is asked about it."""
    branch = CANONICAL_REF.split("/", 1)[-1]
    why = re.sub(r"\s+", " ", detail or "no reason given").strip()
    why = re.sub(r"\s*(?:The note|It) is saved locally\.?$", "", why)
    if why.startswith("could not fetch origin"):
        plain = "%s could not be reached" % branch
    else:
        plain = "it could not be put on %s (%s)" % (branch, why)
    return {"error": detail, "plain": plain,
            "next": "Try again in a few minutes. If it fails the same way again, tell an "
                    "orchestrator"}


def land_note(doc, message, removed=(), op=None, mine_text=None, before_push=None, avoid=(),
              skip=(), refresh_if=None, rebuild=False, chain=None):
    """Put the human's note on the canonical ref. -> (ok, detail).

    `op` is what `cmd_note` did: {"op": "add"|"edit"|"delete", "nid", "line", "prev", "entry"}.
    On return it also carries `nid_final` when the note landed under a different id, and
    `stuck`: notes on this computer that are still not on the ref, each with the reason. A
    deleted instruction ("withdraw" or "retire") also carries `pre` and `post`, the file before
    and after the delete: if the ref refuses the delete, the file goes back to `pre` and `op`
    gets `restored` and `plain` (see `_undo_unlanded_delete`).
    """
    import subprocess as _sp
    import tempfile as _tf

    rel = os.path.relpath(str(doc), str(PROJECTS)).replace("\\", "/")

    def g(a, env=None):
        p = _sp.run(["git", "-C", str(PROJECTS)] + a, capture_output=True, text=True,
                    env=env, timeout=60, encoding="utf-8", errors="replace",
                    creationflags=flags())
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()

    def content_fail(why):
        """A failure that the same inputs will hit again."""
        if op is not None:
            op["fail"] = "content"
        return False, why

    if op is not None:
        op.pop("nid_final", None)
        op.pop("eid_final", None)
        op.pop("fail", None)
        op["stuck"] = []
    message0 = message
    last = "no attempt was made"
    for attempt in range(1, 4):
        message = message0
        rc, _, err = g(["fetch", "--quiet", "origin"])
        if rc != 0:
            return False, "could not fetch origin (%s)" % (err[:120] or "no reason given")
        rc, pinned, err = g(["rev-parse", "--verify", "%s^{commit}" % CANONICAL_REF])
        if rc != 0 or not pinned:
            return False, "could not resolve %s" % CANONICAL_REF
        # An older copy of this file WARNS and lands: see `_refuse_if_tool_is_stale`.
        _stale = _refuse_if_tool_is_stale(pinned) if attempt == 1 else None
        if _stale:
            sys.stderr.write("[orchdoc] warning: %s The note lands anyway.\n"
                             % _stale.replace("\n", " "))

        # Not through `g`: that strips, and this text may become the file.
        _p = _sp.run(["git", "-C", str(PROJECTS), "show", "%s:%s" % (pinned, rel)],
                     capture_output=True, timeout=60, creationflags=flags())
        if _p.returncode != 0:
            return False, "could not read %s:%s (%s)" % (
                CANONICAL_REF, rel, _p.stderr.decode("utf-8", "replace").strip()[:100])
        canon = _p.stdout.decode("utf-8", "replace").replace("\r\n", "\n")

        if mine_text is not None:
            mine = mine_text
        else:
            try:
                mine = doc.read_text(encoding="utf-8", errors="replace")
            except Exception as ex:
                return False, "could not read the working copy (%s)" % type(ex).__name__

        # THE ONE GATE. Line-identity, same unit `cmd_commit`'s gate 1 uses.
        # A DELETE IS A REMOVAL BY DEFINITION, so "nothing may disappear" can never land one.
        # `removed` is exactly the line(s) THIS call took out, so everything else is still
        # protected and a delete can still go.
        allowed = set(l for l in removed if l and l.strip())
        gone = _lines_lost(canon, mine, allowed)
        built, fixes, efixes = None, [], []
        if op is not None:
            op.pop("nid_final", None)
            op.pop("eid_final", None)
        # Which OTHER notes on this computer are not on the ref, and which of those were on it
        # once. The note being saved is left out: it is new, and asking the history about it
        # would read up to 80 versions of the file on every save.
        c_pairs = _note_pairs(canon)
        own_id = (op or {}).get("nid")
        ask = [p for p in _note_pairs(mine) if p not in c_pairs and p[0] != own_id]
        try:
            past = _notes_on_past_versions(rel, pinned, ask) if ask else set()
        except Exception:
            past = set(ask)                   # cannot tell: treat them as landed once (kept off)
        c_ids = set(p[0] for p in c_pairs)
        revived = [p for p in ask if p in past and p[0] not in c_ids]
        skipping = [p for p in ask if p[0] in skip] if skip else []
        if gone or ((revived or skipping or rebuild) and op):
            if not op:
                return False, ("landing this copy would REMOVE %d line(s) that are on %s - so "
                               "it holds more than a note. Reconcile it by hand; the note is "
                               "saved locally." % (len(gone), CANONICAL_REF))
            try:
                built = _note_onto_canonical(canon, mine, op, past, avoid=avoid, skip=skip)
            except Exception as ex:
                built = {"error": "the note could not be applied to %s (%s)"
                                  % (CANONICAL_REF, type(ex).__name__)}
            if built.get("error"):
                if op.get("op") in ("withdraw", "retire") and _undo_unlanded_delete(doc, op, built):
                    return content_fail("%s refused the delete, so this computer's copy was put "
                                        "back as it was: %s" % (CANONICAL_REF, built["error"]))
                return content_fail("this computer's copy is behind %s and the note could not "
                                    "be moved onto it: %s. The note is saved locally."
                                    % (CANONICAL_REF, built["error"]))
            fixes = list(built["renumbered"])
            efixes = list(built.get("entries") or [])
            for old_e, new_e, _h0, _h1 in efixes:
                op["eid_final"] = new_e
                message = re.sub(r"(?<![\w.])%s(?!\w)" % re.escape(old_e), new_e, message)
            if built["nid"] != op.get("nid"):
                op["nid_final"] = built["nid"]
                message = re.sub(r"(?<![\w.])%s(?!\w)" % re.escape(op["nid"]), built["nid"],
                                 message)
                _m = NOTE_LINE_RE.match(op.get("line") or "")
                if _m and not efixes:
                    # (With an entry renamed, the renaming carries its notes.)
                    fixes.append((op["nid"], built["nid"], _m.group("text").strip()))
            op["stuck"] = list(built["stuck"])
            landed_text = built["text"] if built["text"].endswith("\n") else built["text"] + "\n"
            allowed_lost = set(built["removed"])
            rc, blob, err = _hash_text(landed_text, rel)
        elif mine_text is not None:
            landed_text = mine
            allowed_lost = allowed
            rc, blob, err = _hash_text(mine, rel)
        else:
            landed_text = mine
            allowed_lost = allowed
            # `--path rel`, as `_hash_text` passes in the branch above: attributes are looked up
            # by the path the blob lands at, not by the absolute path of the file on disk.
            rc, blob, err = g(["hash-object", "-w", "--path", rel, str(doc)])
            if rc == 0 and blob:
                _b = _sp.run(["git", "-C", str(PROJECTS), "cat-file", "-p", blob],
                             capture_output=True, timeout=60, creationflags=flags())
                if _b.returncode == 0:
                    landed_text = _b.stdout.decode("utf-8", "replace").replace("\r\n", "\n")
        if rc != 0 or not blob:
            return False, "could not hash the document (%s)" % (err[:100] or "no reason")

        def _refresh(commit_sha, detail):
            try:
                ok, why = _refresh_disk_after_landing(
                    doc, rel, landed_text, commit_sha, () if before_push else fixes,
                    only_if=refresh_if() if refresh_if else None,
                    chain=None if (op or {}).get("stuck") else chain,
                    efixes=() if before_push else efixes)
            except (Exception, SystemExit) as ex:
                # SystemExit too: `_lock` raises it when the doc stays locked, and the note is
                # on the ref by now, so that must read as "landed, not refreshed".
                ok, why = False, "the refresh raised %s" % type(ex).__name__
            if ok:
                return True, detail
            return True, ("%s - this computer's copy was NOT refreshed to match, because %s"
                          % (detail, why))

        # Already there word for word (a retry, or a note an orchestrator's landing merged in):
        # that is landed, not a failure to build a commit.
        def _with_notes(detail):
            if built and (built["carried"] or built["skipped"] or built.get("entries")):
                bits = []
                for old_e, new_e, _h0, _h1 in built.get("entries") or ():
                    bits.append("landed as %s, because %s already names a different entry on %s"
                                % (new_e, old_e, CANONICAL_REF))
                if built["carried"]:
                    bits.append("also landed %s, stuck on this computer until now"
                                % ", ".join(built["carried"]))
                if built["skipped"]:
                    bits.append("not landed: %s" % "; ".join(built["skipped"]))
                detail += " (" + "; ".join(bits) + ")"
            return detail

        rc, on_ref, _ = g(["rev-parse", "%s:%s" % (pinned, rel)])
        if rc == 0 and on_ref == blob:
            if before_push is not None and (fixes or efixes):
                # Nothing is pushed, but the renumberings must still reach the disk copy and
                # the later queued changes before this one is called landed.
                stop = before_push(fixes, efixes)
                if stop:
                    if op is not None:
                        op["fail"] = stop[0]
                    return False, stop[1]
            return _refresh(pinned, _with_notes("already on %s at %s"
                                                % (CANONICAL_REF, pinned[:10])))

        idx = pathlib.Path(_tf.gettempdir()) / ("orchdoc-note-%s.index" % os.getpid())
        env = dict(os.environ, GIT_INDEX_FILE=str(idx))
        try:
            if idx.exists():
                idx.unlink()
            g(["read-tree", pinned], env)
            g(["update-index", "--add", "--cacheinfo", "100644,%s,%s" % (blob, rel)], env)
            rc, tree, err = g(["write-tree"], env)
            if rc != 0 or not tree:
                return False, "could not write the tree (%s)" % (err[:100] or "no reason")
        finally:
            try:
                idx.unlink()
            except OSError:
                pass

        p = _sp.run(["git", "-C", str(PROJECTS), "commit-tree", tree, "-p", pinned],
                    input=("%s\n\n%s: %s\n" % (message, LANDER_TRAILER, READER_LANDER))
                    .encode("utf-8"),
                    capture_output=True, timeout=60, creationflags=flags())
        commit = p.stdout.decode("utf-8", "replace").strip()
        if p.returncode != 0 or not commit:
            return False, "could not build the commit"

        # Touches exactly the one path, or it does not go.
        rc, names, _ = g(["diff", "--name-only", pinned, commit])
        touched = sorted(n for n in names.splitlines() if n.strip())
        if touched != [rel]:
            return content_fail("the commit would touch %s, not just %s"
                                % (touched or "nothing", rel))

        rc, parent, _ = g(["rev-parse", "%s^" % commit])
        _pt = _sp.run(["git", "-C", str(PROJECTS), "show", "%s:%s" % (parent, rel)],
                      capture_output=True, timeout=60, creationflags=flags())
        _bt = _sp.run(["git", "-C", str(PROJECTS), "show", "%s:%s" % (commit, rel)],
                      capture_output=True, timeout=60, creationflags=flags())
        if rc != 0 or parent != pinned or _pt.returncode != 0 or _bt.returncode != 0:
            return False, "could not verify the commit against its parent"
        lost = _lines_lost(_pt.stdout.decode("utf-8", "replace").replace("\r\n", "\n"),
                           _bt.stdout.decode("utf-8", "replace").replace("\r\n", "\n"),
                           allowed_lost)
        if lost:
            return content_fail("the commit would remove %d line(s) that are on its parent - "
                                "first: %s" % (len(lost), lost[0].strip()[:80]))

        if before_push is not None:
            stop = before_push(fixes, efixes)
            if stop:
                if op is not None:
                    op["fail"] = stop[0]
                return False, stop[1]

        branch = CANONICAL_REF.split("/", 1)[-1]
        rc, _, err = g(["push", "origin", "%s:%s" % (commit, branch)])
        if rc != 0:
            last = err.splitlines()[-1][:120] if err else "no reason"
            if ("rejected" in err or "fetch first" in err or "non-fast-forward" in err) \
                    and attempt < 3:
                if before_push is not None and (fixes or efixes):
                    # `before_push` has renumbered the queue and the disk copy to match this
                    # attempt, and the next attempt would start from the old ids in memory. The
                    # next `land-job` run reads the renumbered job instead, at once.
                    if op is not None:
                        op["fail"] = "changed"
                    return False, ("%s moved while this change was landing; it is tried again "
                                   "(%s)" % (CANONICAL_REF, last))
                continue                          # main moved: start again from the new tip
            return False, "push refused (%s)" % last
        return _refresh(commit, _with_notes(commit[:10]))
    return False, ("%s moved during each of 3 attempts, so the note was not landed (%s). It is "
                   "saved locally." % (CANONICAL_REF, last))


def _dup_id_refusal(args, doc, eid, n, doing):
    """Refusing a note or a delete on an id `n` entries carry. -> the reason."""
    if not args.json:
        return "id %s appears %d times - fix E-DUPID first" % (eid, n)
    return ("%s is the id of %d entries in this doc, so it is not clear which one you meant. %s "
            "has to give one of them a new id before you can %s"
            % (eid, n, _orchdoc_slug_or_none(doc) or "The orchestrator that owns this doc",
               doing))


READER_LANDING_REL = ".shared/state/reader-landing"
JOB_NAME_RE = re.compile(r"^(\d{20})-[0-9a-f]{6}\.json$")


def reader_landing_dir():
    """Where this workspace's landing queue lives. Local state: never committed."""
    return Path(PROJECTS) / READER_LANDING_REL


def _job_retry(fn):
    """`fn()`, retried for up to two seconds while Windows refuses a file that another process
    (the worker, a virus scanner) has open for a moment."""
    for k in range(40):
        try:
            return fn()
        except PermissionError:
            if k == 39:
                raise
            time.sleep(0.05)


def _job_bytes(path):
    """A job file's bytes, or None when there is no such file."""
    def _r():
        try:
            with open(str(path), "rb") as fh:
                return fh.read()
        except FileNotFoundError:
            return None
    return _job_retry(_r)


def _job_write(path, job):
    """Write one job file atomically (temp file beside it, then os.replace). -> its bytes."""
    path = Path(path)
    body = json.dumps(job, ensure_ascii=False, sort_keys=True).encode("utf-8")
    tmp = path.with_name(".%s.tmp-%d" % (path.name, os.getpid()))

    def _w():
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(tmp), "wb") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(str(tmp), str(path))
    try:
        _job_retry(_w)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return body


def _job_unlink(path):
    """Delete a job file; one already gone is fine."""
    def _u():
        try:
            os.unlink(str(path))
        except FileNotFoundError:
            pass
    _job_retry(_u)


def _jobs_for(doc):
    """[(path, job, bytes)] for every queued job of `doc`, oldest first. A job file that cannot
    be read is left out here; the worker reports it by name."""
    d = reader_landing_dir()
    rel = os.path.relpath(str(doc), str(PROJECTS)).replace("\\", "/")
    try:
        names = sorted(p.name for p in d.iterdir() if JOB_NAME_RE.match(p.name))
    except OSError:
        return []
    out = []
    for name in names:
        raw = _job_bytes(d / name)
        if raw is None:
            continue
        try:
            job = json.loads(raw.decode("utf-8"))
        except ValueError:
            continue
        if isinstance(job, dict) and job.get("doc") == rel:
            out.append((d / name, job, raw))
    out.sort(key=lambda t: (int(t[1].get("seq") or 0), t[0].name))
    return out


def _queue_job(args, doc, message, removed, op, snapshot, pre=None):
    """With `--queue-landing` (and no `--no-land`): journal one change. -> the job path, or None.

    Called INSIDE the doc's lock and BEFORE the doc is written, so a change that reached the disk
    always has its job. The caller deletes the job again if the write or its verification
    fails, because a change it reports as failed must not land.

    `pre` is the doc's text as this save read it, before its change: see `_tip_note`.
    """
    if getattr(args, "no_land", False) or not getattr(args, "queue_landing", False):
        return None
    d = reader_landing_dir()
    d.mkdir(parents=True, exist_ok=True)
    last = 0
    for p in d.iterdir():
        m = JOB_NAME_RE.match(p.name)
        if m:
            last = max(last, int(m.group(1)))
    # Ordered by the clock, and never at or before a job still queued, so the order the worker
    # lands them in is the order they were saved even if the clock steps back.
    seq = max(time.time_ns(), last + 1)
    path = d / ("%020d-%s.json" % (seq, os.urandom(3).hex()))
    _job_write(path, {"v": 1, "id": path.stem, "seq": seq,
                      "doc": os.path.relpath(str(doc), str(PROJECTS)).replace("\\", "/"),
                      "message": message, "removed": list(removed), "op": op,
                      "snapshot": snapshot, "created": _now_iso()})
    _tip_note(doc, path.name, pre, snapshot)
    return path


def _drop_job(path, doc=None):
    """Delete a job whose change did not happen. Never raises: the caller is already failing."""
    if path is not None:
        try:
            _job_unlink(path)
        except OSError:
            pass
        if doc is not None:
            _tip_forget(doc, path.name)


READER_TIPS_REL = READER_LANDING_REL + "/tips"


def _text_blob(text):
    """The git blob id of `text` as `write_doc` writes it: sha1 of the blob header and the UTF-8
    bytes, CRLF read as LF. No git call. None for None."""
    import hashlib
    if text is None:
        return None
    data = text.replace("\r\n", "\n").encode("utf-8")
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _tip_path(doc):
    return Path(PROJECTS) / READER_TIPS_REL / ("%s.json" % Path(doc).name)


def _tip_read(doc):
    """The doc's record of the Reader's run of writes, or None."""
    raw = _job_bytes(_tip_path(doc))
    if raw is None:
        return None
    try:
        rec = json.loads(raw.decode("utf-8"))
    except ValueError:
        return None
    return rec if isinstance(rec, dict) else None


def _tip_write(doc, rec):
    """Write the record. Never raises: without it the copy is not refreshed, nothing is lost."""
    try:
        _job_write(_tip_path(doc), rec)
    except OSError as ex:
        sys.stderr.write("[orchdoc] could not record the Reader's write to %s (%s)\n"
                         % (Path(doc).name, type(ex).__name__))


def _tip_note(doc, name, pre, snapshot):
    """Inside the doc's lock, before the write: the Reader is writing `snapshot` over `pre`, as
    queued change `name`. The run goes on when the copy is still what the Reader last wrote and
    one of its changes is still queued; otherwise a new run starts at `pre`."""
    old = _tip_read(doc) or {}
    d = reader_landing_dir()
    live = [n for n in (old.get("jobs") or [])
            if isinstance(n, str) and JOB_NAME_RE.match(n) and (d / n).exists()]
    pre_blob = _text_blob(pre)
    if live and old.get("base") and old.get("tip") and old.get("tip") == pre_blob:
        base = old["base"]
    else:
        base, live = pre_blob, []
    _tip_write(doc, {"v": 1, "doc": os.path.relpath(str(doc), str(PROJECTS)).replace("\\", "/"),
                     "tip": _text_blob(snapshot), "base": base, "jobs": live + [name],
                     "at": _now_iso()})


def _tip_forget(doc, name):
    """A queued change that did not happen ends the run: the copy is no longer `tip` plus it."""
    rec = _tip_read(doc)
    if rec and name in (rec.get("jobs") or []):
        rec["tip"] = None
        rec["jobs"] = [n for n in rec["jobs"] if n != name]
        _tip_write(doc, rec)


def _version_blobs(rel, commit, depth=200):
    """Blob ids `rel` has had at or before `commit`, as far back as `depth` commits that changed
    it. One git call that reads no file contents: the raw diff lines name the blob before and
    after each change, merges included."""
    rc, log, _ = git(["log", "--format=%H", "-n", str(depth), "--raw", "-m", "--no-abbrev",
                      "--no-renames", commit, "--", rel])
    out = set()
    if rc != 0:
        return out
    for line in log.splitlines():
        if line.startswith(":"):
            for b in line.split()[2:4]:
                if re.match(r"^[0-9a-f]{40}$", b) and b.strip("0"):
                    out.add(b)
    return out


QUEUED_DETAIL = ("queued: the Reader's landing worker puts it on %s in the background, and the "
                 "page shows when it is there")


READER_SENT_REL = READER_LANDING_REL + "/sent"
CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
SENT_KEEP_SECONDS = 14 * 86400


def reader_sent_dir():
    """Where the `--client-id` records live. Local state, like the landing queue."""
    return Path(PROJECTS) / READER_SENT_REL


def _sent_path(args, doc, what):
    """-> the record path for this save (`<client id>.<content hash>.json`), or None without a
    usable `--client-id`."""
    import hashlib
    cid = getattr(args, "client_id", None)
    if not cid or not CLIENT_ID_RE.match(cid):
        return None
    key = dict(what, doc=os.path.relpath(str(doc), str(PROJECTS)).replace("\\", "/"))
    h = hashlib.sha256(json.dumps(key, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return reader_sent_dir() / ("%s.%s.json" % (cid, h))


def _sent_check(args, doc, what):
    """-> (record or None, refusal or None) for this call's `--client-id`. Call it inside the
    doc's lock, so a repeat waits for the first save to finish writing."""
    cid = getattr(args, "client_id", None)
    if not cid:
        return None, None
    if not CLIENT_ID_RE.match(cid):
        return None, "--client-id is 8 to 64 letters, digits, - or _"
    raw = _job_bytes(_sent_path(args, doc, what))
    if raw is None:
        return None, None
    try:
        rec = json.loads(raw.decode("utf-8"))
    except ValueError:
        rec = None
    # A record that cannot be read still says this save reached the disk.
    return (rec if isinstance(rec, dict) else {}), None


def _sent_record(args, doc, what, reply):
    """Record this call's `--client-id`, inside the doc's lock and right after the doc is
    written. -> the record's path, or None. A record that cannot be written is reported, never
    raised: the change is on disk already, and without the record only a resend could repeat it."""
    path = _sent_path(args, doc, what)
    if path is None:
        return None
    cid, d = args.client_id, path.parent
    try:
        _job_write(path, {"v": 1, "cid": cid, "at": _now_iso(), "reply": reply,
                          "doc": os.path.relpath(str(doc), str(PROJECTS)).replace("\\", "/")})
    except OSError as ex:
        sys.stderr.write("[orchdoc] could not record client id %s (%s); a resend of this save "
                         "would write it again\n" % (cid, type(ex).__name__))
        return None
    cutoff = time.time() - SENT_KEEP_SECONDS
    try:
        for p in d.iterdir():
            if p.suffix == ".json" and p.name != path.name and p.stat().st_mtime < cutoff:
                _job_unlink(p)
    except OSError:
        pass
    return path


def _sent_update(path, reply):
    """Put the full answer into a record `_sent_record` wrote. Never raises."""
    if path is None:
        return
    try:
        raw = _job_bytes(path)
        rec = json.loads(raw.decode("utf-8")) if raw else None
        if isinstance(rec, dict):
            rec["reply"] = dict((k, v) for k, v in reply.items() if k != "notes")
            _job_write(path, rec)
    except (OSError, ValueError):
        pass


def _sent_drop(path):
    """Delete the record of a save that did not take, so sending it again writes it."""
    if path is not None:
        try:
            _job_unlink(path)
        except OSError:
            pass


def _ref_text(rel):
    """`rel` as the canonical ref has it, or None when the ref cannot be read. One local
    `git show`, no network."""
    try:
        p = subprocess.run(["git", "-C", str(PROJECTS), "show", "%s:%s" % (CANONICAL_REF, rel)],
                           capture_output=True, timeout=60, creationflags=flags())
        if p.returncode == 0:
            return p.stdout.decode("utf-8", "replace").replace("\r\n", "\n")
    except Exception:
        pass
    return None


def _ref_next_id(rel, prefix):
    """The next free `prefix` id (W, N) in `rel` as the canonical ref has it, or None when the
    ref cannot be read."""
    text = _ref_text(rel)
    if text is None:
        return None
    return next_id(parse_entries(text.split("\n"))[0], prefix, text)


def _sent_reply(args, rec):
    """Answer a repeat of a save that is already on disk. Writes nothing."""
    reply = dict(rec.get("reply") or {})
    reply.update(ok=True, duplicate=True)
    if args.json:
        print(json.dumps(reply))
    else:
        print("[ALREADY SAVED] client id %s was saved before (%s %s in %s); nothing was written"
              % (args.client_id, reply.get("action") or "?", reply.get("nid") or
                 reply.get("id") or "?", reply.get("doc") or rec.get("doc") or "?"))
    return 0


def _undo_failed_write(doc, raw, post):
    """Call it inside the lock the write was made in. -> the words to add to the refusal.

    Only when the file still holds exactly `post`: a file that changed since is someone else's
    work, so it is left alone and the refusal says so. `raw` is the file's bytes decoded, and
    `write_doc` writes it with no newline translation, so the bytes come back as they were."""
    try:
        write_doc(doc, raw, expect=post)
    except (DocPathError, OSError) as ex:
        return (". It could NOT be taken back out of the file (%s), so look at %s before "
                "sending it again" % (ex, doc.name))
    return ". Nothing was left in the file: it is back as it was before the write"


def cmd_note(args):
    if (args.doc or "").strip().lower() == "all":
        if not getattr(args, "list_only", False):
            _note_fail(args, "--doc all is for reading. Name one doc to write a note.")
            return 2
        return _cmd_note_list(args, None)

    doc = resolve_doc_arg(args.doc)
    if not doc or not doc.exists():
        _note_fail(args, "no such doc: %s" % args.doc)
        return 2

    if getattr(args, "list_only", False):
        return _cmd_note_list(args, doc)

    if getattr(args, "show", None):
        want = args.show.strip()

        def _notes_in(p):
            # Read exactly as `_cmd_note_list` reads, so --show and --list see the same notes.
            return parse_notes("\n".join(
                p.read_text(encoding="utf-8", errors="replace").splitlines()))

        hit = [n for n in _notes_in(doc) if n.get("nid") == want]
        if not hit:
            for root in _picture_roots()[1:]:
                alt = root / doc.name
                if alt.is_file() and alt.resolve() != doc.resolve():
                    hit = [n for n in _notes_in(alt) if n.get("nid") == want]
                    if hit:
                        break
        if not hit:
            sys.stderr.write("no note %s in %s\n" % (want, doc.name))
            return 1
        sys.stdout.flush()
        sys.stdout.buffer.write(note_display(hit[0]).encode("utf-8"))
        sys.stdout.buffer.flush()
        return 0

    if not args.id and not args.note:
        _note_fail(args, "say which entry (--id) or which note (--note)")
        return 2
    if getattr(args, "ack", False):
        return _cmd_note_ack(args, doc)
    if not args.delete and args.text is None:
        _note_fail(args, "--text is required unless --delete")
        return 2
    if args.note and not NOTE_ID_RE.match(args.note):
        _note_fail(args, "a note id looks like D<N>.n<M> (or a legacy n3), not %r" % args.note)
        return 2
    want_kind = (getattr(args, "kind", None) or "").upper() or None
    if want_kind and want_kind not in NOTE_KINDS:
        _note_fail(args, "--kind is one of %s, not %r"
                   % (", ".join(k.lower() for k in NOTE_KINDS), args.kind))
        return 2

    if not args.delete and _orch_note_refusal(
            args, "a new note" if not args.note else "an edit of note %s" % args.note):
        return 1

    who = _orch_caller(args) or args.by or human_name().title()
    when = args.at or _stamp_human()

    item_level = bool(args.delete and not args.note)
    _what = {"cmd": "note", "id": args.id or "", "note": args.note or "", "text": args.text,
             "delete": bool(args.delete), "kind": want_kind or ""}
    _ref_lines = None
    if args.id and not args.note and not args.delete:
        _rt = _ref_text(os.path.relpath(str(doc), str(PROJECTS)).replace("\\", "/"))
        _ref_lines = _rt.split("\n") if _rt is not None else None
    with _lock(doc):
        _seen, _bad = _sent_check(args, doc, _what)
        if _bad:
            _note_fail(args, _bad)
            return 2
        if _seen is not None:
            return _sent_reply(args, _seen)
        # The file byte for byte, so a deleted instruction that main then refuses can be put
        # back exactly as it was (see `_undo_unlanded_delete`). `lines` is what `read_text`
        # gives: universal newlines, then split.
        _raw = doc.read_bytes().decode("utf-8")
        lines = _raw.replace("\r\n", "\n").replace("\r", "\n").splitlines()
        entries, _ = parse_entries(lines)
        existing = parse_notes("\n".join(lines))
        _pre = "\n".join(lines)
        note_id, retire, op_item, op_ins, op_seen = args.note, None, None, None, None

        if args.delete and _orch_caller(args):
            _theirs = [n for n in existing if n["nid"] == args.note]
            _cands = [n for n in existing if n["nid"] == args.note
                      or n["nid"].endswith("." + (args.note or "-"))]
            _list = "; ".join("%s (%s from %s, line %d)" % (n["nid"], n["kind"], n["who"],
                                                          n["line"]) for n in _cands)
            if item_level or not all(_ORCH_CALLER_RE.match(n["who"]) for n in _theirs):
                _orch_note_refusal(args, "a delete of the human's note or instruction%s" % (
                    " (candidates: %s)" % _list if _list else ""))
                return 1
            if len(_cands) > len(_theirs):
                _note_fail(args, "note id %s is ambiguous in %s; name the full id. Candidates: %s"
                           % (args.note, doc.name, _list))
                return 1

        if item_level:
            match = [e for e in entries if e["id"] == args.id]
            if not match:
                _note_fail(args, "no entry with id %s in %s" % (args.id, doc.name))
                return 1
            if len(match) > 1:
                _note_fail(args, _dup_id_refusal(args, doc, args.id, len(match), "delete it"))
                return 1
            item = instruction_item(lines, args.id, entries, existing)
            if not isinstance(item, tuple):
                if not args.json:
                    why = ("%s. --delete with no --note deletes a whole entry only when it is a "
                           "live instruction item, one `instruct` made. To delete one note, name "
                           "it with --note; an entry can hold several" % item)
                elif match[0].get("archived"):
                    why = ("%s is already in §99 on this computer, so there is nothing to delete. "
                           "Reload the page to see it there" % args.id)
                else:
                    why = "%s, so it cannot be deleted whole" % item
                _note_fail(args, why)
                return 2
            if item[1]:
                # Its instructions are still on it, so this is exactly what deleting one of
                # them does: `withdraw_doc_instruction` marks every one of them deleted.
                note_id = item[1][0]["nid"]
            else:
                retire = item[0]

        if note_id:
            hits = [n for n in existing if n["nid"] == note_id]
            if not hits:
                _note_fail(args, "no note %s in %s" % (note_id, doc.name))
                return 1
            if len(hits) > 1:
                _note_fail(args, "note %s appears %d times in %s - fix that by hand first. "
                                 "Candidates: %s"
                           % (note_id, len(hits), doc.name, "; ".join(
                               "line %d (%s from %s)" % (h["line"], h["kind"], h["who"])
                               for h in hits)))
                return 1
            i = hits[0]["line"] - 1
            op_entry = _note_entry_id(hits[0], entries)
            # The line as it was BEFORE this edit or delete: the lander acts on main's note
            # with this id only if main's line is this same note (an id can collide).
            op_prev = lines[i]
            if args.delete and op_entry:
                # Every instruction on the item as this copy has it, so the lander deletes the
                # item on main only when main's item holds exactly these.
                _it = instruction_item(lines, op_entry, entries, existing)
                op_ins = [(x["nid"], x["text"]) for x in _it[1]] if isinstance(_it, tuple) \
                    else None
                op_seen = _seen_by(_it[1]) if isinstance(_it, tuple) else None
            withdrawn = withdraw_doc_instruction(lines, note_id, who, when) \
                if args.delete else None
            if isinstance(withdrawn, str) or (item_level and not isinstance(withdrawn, tuple)):
                _note_fail(args, "instruction %s was NOT deleted: %s"
                           % (note_id, withdrawn if isinstance(withdrawn, str) else
                              "it could not be moved to §99"))
                return 1
            if isinstance(withdrawn, tuple):
                nid, newline = note_id, None
            elif args.delete:
                del lines[i]
                # A note removed from between two blank lines leaves a double gap.
                if 0 < i < len(lines) and not lines[i].strip() and not lines[i - 1].strip():
                    del lines[i]
                nid, newline = note_id, None
            else:
                nid = note_id
                kind = want_kind or hits[0].get("kind") or "NOTE"
                newline = render_note(nid, who, when, args.text, kind)
                lines[i] = newline
        elif retire is not None:
            withdrawn = None
            nid = _free_note_id(lines, "%s.n1" % args.id, avoid={"%s.n1" % args.id})
            newline = render_note(nid, who, when, "%s deleted this instruction on %s."
                                  % (who, when))
            op_entry, op_prev = args.id, None
            # The item as it stood, so the lander can tell whether the ref's copy is this one.
            op_item = lines[retire["line"] - 1:_entry_end(retire)]
            why = withdraw_instruction_item(lines, args.id, who, newline)
            if why:
                _note_fail(args, "%s was NOT deleted: %s" % (args.id, why))
                return 1
        else:
            withdrawn = None
            match = [e for e in entries if e["id"] == args.id]
            if not match:
                _note_fail(args, "no entry with id %s in %s" % (args.id, doc.name))
                return 1
            if len(match) > 1:
                _note_fail(args, _dup_id_refusal(args, doc, args.id, len(match),
                                                 "put a note on it"))
                return 1
            nid = next_note_id(lines, args.id)
            if _ref_lines is not None:
                _rn = next_note_id(_ref_lines, args.id)
                if int(_rn.split(".", 1)[1][1:]) > int(nid.split(".", 1)[1][1:]):
                    nid = _rn
            op_entry, op_prev = args.id, None
            kind = want_kind or "NOTE"
            newline = render_note(nid, who, when, args.text, kind)
            at, stacks = note_slot(lines, match[0])
            # A trailing blank keeps the note from being absorbed into the paragraph below it
            # by a markdown renderer that continues a blockquote across a non-blank line.
            block = [newline] if stacks else [newline, ""]
            lines[at:at] = block

        if not args.delete and newline and not isinstance(withdrawn, tuple) \
                and retire is None and _note_by_agent(who) and _write_guard_refuses(
                    doc, _pre + "\n", "\n".join(lines) + "\n", "note",
                    fail=lambda _m: _note_fail(args, _m)):
            return 1

        _post = "\n".join(lines) + "\n"
        _op = {"op": "delete" if args.delete else ("edit" if args.note else "add"),
               "nid": nid, "line": newline, "entry": op_entry, "prev": op_prev}
        _subject = "%s %s on %s (%s)" % (
            "Deleted" if (isinstance(withdrawn, tuple) or args.delete)
            else ("Edited" if args.note else "Added"), nid, doc.name, sanitize_field(who, 40))
        # `pre` and `post`: the file before and after this delete, so the lander can put it
        # back if main refuses the delete (`_undo_unlanded_delete`).
        if isinstance(withdrawn, tuple):
            _op.update(op="withdraw", who=who, when=when, ins=op_ins, seen=op_seen, pre=_raw,
                       post=_post)
        elif retire is not None:
            _op.update(op="retire", who=who, when=when, item=op_item, pre=_raw, post=_post)
            # Not "Deleted <nid>": the note with that id was ADDED, and the subject would say
            # the opposite. The Orchdoc-Lander trailer is what marks it as a Reader landing.
            _subject = "Deleted instruction %s on %s (%s)" % (args.id, doc.name,
                                                             sanitize_field(who, 40))
        _new_set = set(_post.split("\n"))
        _queued = []
        if _op["op"] in ("withdraw", "retire") and not getattr(args, "no_land", False):
            _queued = [j for _p, j, _r in _jobs_for(doc)]
            _held = [j for j in _queued if (j.get("op") or {}).get("entry") == _op["entry"]]
            if _held:
                _note_fail(args, "%s has a change that has not reached %s yet (%s), so it cannot "
                                 "be deleted until that change has landed. Nothing was deleted. "
                                 "Try again once the page shows it landed"
                           % (_op["entry"], CANONICAL_REF.split("/", 1)[-1],
                              _held[0].get("message") or "a queued change"))
                return 1
        _job = _queue_job(
            args, doc, _subject,
            [l for l in _pre.split("\n") if l.strip() and l not in _new_set], _op, _post,
            pre=_raw)
        try:
            write_doc(doc, _post)
        except BaseException:
            _drop_job(_job, doc)
            raise
        _sent = _sent_record(args, doc, _what, {
            "ok": True, "nid": nid, "doc": doc.name, "id": args.id or "",
            "action": ("deleted" if (isinstance(withdrawn, tuple) or args.delete)
                       else "edited" if args.note else "added"),
            "queued": _job.stem if _job is not None else None})

        _back = doc.read_text(encoding="utf-8", errors="replace")
        # What this call took OUT, so the lander can allow exactly that and nothing else.
        _now = set(_back.split("\n"))
        _removed_lines = [l for l in _pre.split("\n") if l.strip() and l not in _now]
        after = parse_notes(_back)
        got = [n for n in after if n["nid"] == nid]
        _why = None
        if isinstance(withdrawn, tuple):
            _ents = parse_entries(_back.split("\n"))[0]
            _ins = set(x[0] for x in (op_ins or [])) | {nid}
            if not (len(got) == 1 and any(n["nid"] == withdrawn[1] for n in after)
                    and all(n["ack"] for n in after if n["nid"] in _ins)
                    and not any(e["id"] == withdrawn[0] and not e.get("archived")
                                for e in _ents)):
                _why = "instruction %s was NOT moved to §99 as written" % nid
            verb, shown = "DELETED", ""
        elif retire is not None:
            _ents = parse_entries(_back.split("\n"))[0]
            _home = [e for e in _ents if e["id"] == args.id and e.get("archived")
                     and WITHDRAWN_LABEL in e["title"] and len(got) == 1
                     and e["line"] <= got[0]["line"] < e["line"] + len(e["body"].splitlines())]
            if not _home or any(e["id"] == args.id and not e.get("archived") for e in _ents):
                _why = "%s was NOT moved to §99 as written" % args.id
            verb, shown = "DELETED", ""
        elif args.delete:
            if got:
                _why = "note %s is STILL in %s after the delete" % (nid, doc.name)
            verb, shown = "DELETED", ""
        elif not got:
            _why = "note %s is NOT in %s after the write - it did NOT take" % (nid, doc.name)
        else:
            verb, shown = ("EDITED" if args.note else "ADDED"), got[0]["text"]
            kind = got[0].get("kind") or "NOTE"
        if _why:
            _drop_job(_job, doc)
            _sent_drop(_sent)
            _note_fail(args, _why + _undo_failed_write(doc, _raw, _post))
            return 1

    landed, land_detail, stuck = (False, "not attempted", [])
    if _job is not None:
        landed, land_detail = None, QUEUED_DETAIL % CANONICAL_REF
    elif not getattr(args, "no_land", False):
        # The queued changes this copy holds stay out of the delete (see `_queued` above).
        _skip, _avoid = set(), set()
        for _j in _queued:
            _s = _j.get("snapshot") or ""
            _skip.add((_j.get("op") or {}).get("nid"))
            _avoid |= set(n["nid"] for n in parse_notes(_s))
            _avoid |= set(e["id"] for e in parse_entries(_s.split("\n"))[0])
        _skip.discard(None)
        _skip.discard(nid)
        try:
            landed, land_detail = land_note(
                doc, _subject, removed=_removed_lines, op=_op, mine_text=_post,
                rebuild=bool(_queued), skip=_skip, avoid=_avoid,
                chain=lambda: (_text_blob(_post), _text_blob(_raw)))
        except Exception as _ex:
            landed, land_detail = False, "the lander raised %s" % type(_ex).__name__
        if (not landed and _op.get("op") in ("withdraw", "retire")
                and not _op.get("restored") and _has_origin()):
            _undo_unlanded_delete(doc, _op, _unlanded_delete_words(land_detail))
        if _op.get("restored"):
            _sent_drop(_sent)
            _eid = withdrawn[0] if isinstance(withdrawn, tuple) else args.id
            _next = _op.get("next")
            if args.json:
                print(json.dumps({"ok": False, "restored": True, "landed": False,
                                  "land_detail": land_detail, "doc": doc.name, "id": _eid,
                                  "error": "%s. Nothing was deleted, on this computer or on %s%s"
                                           % (_op.get("plain"),
                                              CANONICAL_REF.split("/", 1)[-1],
                                              (". " + _next) if _next else "")}))
            else:
                print("[REFUSE] %s was NOT deleted: %s. This computer's copy is back as it was."
                      % (_eid, _op.get("plain")), file=sys.stderr)
                if _next:
                    print("  %s." % _next, file=sys.stderr)
                print("  Reason given by the lander: %s" % land_detail, file=sys.stderr)
            return 1
        if _op.get("nid_final"):
            # The id was taken on the canonical ref, so the note landed under the next free
            # one and this copy was renumbered to match. Report the id it actually has.
            nid = _op["nid_final"]
        # Notes on this computer that are still not on the canonical ref, and why - shown by
        # the Reader even when THIS note landed, so a stuck one is never silent.
        stuck = list(_op.get("stuck") or [])
        after = parse_notes(doc.read_text(encoding="utf-8"))

    _reply = {"ok": True, "action": verb.lower(), "nid": nid,
              "landed": landed, "land_detail": land_detail, "stuck": stuck,
              "queued": _job.stem if _job is not None else None,
              "doc": doc.name, "id": args.id or "",
              "kind": ("" if args.delete else kind),
              "who": who, "when": when, "text": shown,
              "notes": [n for n in after]}
    _sent_update(_sent, _reply)
    if args.json:
        print(json.dumps(_reply))
        return 0
    print("[%s %s] %s in %s%s"
          % (("INSTRUCTION" if item_level else "NOTE" if args.delete else kind), verb,
             args.id if item_level else nid, doc.name,
             "" if landed or landed is None else
             ("  \u26a0\ufe0f NOT ON %s: %s" % (CANONICAL_REF, land_detail))))
    if shown:
        print("  %s (%s): %s" % (who, when, shown[:100]))
    if item_level:
        print("  Kept for the record in \u00a799 as \"%s\"; note %s says %s deleted it."
              % (WITHDRAWN_LABEL, withdrawn[1] if isinstance(withdrawn, tuple) else nid, who))
    print()
    if landed is None:
        print("  %s (job %s)." % (land_detail[:1].upper() + land_detail[1:], _job.stem))
    elif landed:
        print("  It is on %s (%s), so every session sees it wherever they read from -"
              % (CANONICAL_REF, land_detail))
        print("  including an orchestrator working in a worktree, which is the case that")
        print("  made four of the human's notes invisible.")
    else:
        print("  \u26a0\ufe0f IT IS IN THIS COPY ONLY, so a session reading %s will NOT see it."
              % CANONICAL_REF)
        print("  Reason: %s" % land_detail)
        print("  Land it once that is resolved:")
        print("    python <scripts>/orchdoc.py commit --doc %s -m \"...\"" % doc.name)
    return 0


INSTRUCT_TITLE_LEN = 90
INSTRUCT_PROSE = ("_%s left this instruction for the whole doc, not for one entry. Act on it: "
                  "create the §2 items or the §3 work it calls for, link them here, then resolve "
                  "this entry._")


INSTRUCT_MARK_RE = re.compile(r"left this instruction for the whole doc")
WITHDRAWN_STATUS = "ARCHIVED"
WITHDRAWN_INTO = "RESOLVED - kept for the record"
WITHDRAWN_LABEL = "Deleted with no action"


# The heading text `cmd_instruct` writes after the id and its separator.
INSTRUCT_TITLE_RE = re.compile(r"^(?P<who>.{1,200}?)'s instruction: ")


def _instruct_prose_at(lines, e):
    """Index in `lines` of the "Act on it" line `cmd_instruct` wrote on entry `e`, or None
    when `e` is not an instruction item.
    """
    if e.get("archived") or not re.match(r"^W\d+$", e["id"]):
        return None
    head = strip_decoration(e["title"])
    # The same separators `_move_to_completed` reads after an id.
    m = re.match(r"^%s\**\s*[-–—.:]\**\s*" % re.escape(e["id"]), head)
    t = INSTRUCT_TITLE_RE.match(head[m.end():]) if m else None
    if not t:
        return None
    want = INSTRUCT_PROSE % sanitize_field(t.group("who"), 40)
    infence = False
    for k in range(e["line"], min(_entry_end(e), len(lines))):
        if is_fence_line(lines[k]):
            infence = not infence
        elif not infence and lines[k].strip() == want:
            return k
    return None


def instruction_item(lines, eid, entries=None, notes=None):
    """The live instruction item `eid` (one `cmd_instruct` made) and the INSTRUCTION notes on it.

    -> (entry, [its INSTRUCTION notes, in file order], index of its "Act on it" line), or a
    sentence saying why `eid` is not one. `entries` and `notes` are the parses of `lines`,
    for a caller that already has them.
    """
    entries = parse_entries(lines)[0] if entries is None else entries
    live = [e for e in entries if e["id"] == eid and not e.get("archived")]
    if not live:
        if any(e["id"] == eid for e in entries):
            return "%s is already in §99" % eid
        return "no entry with id %s" % eid
    if len(live) > 1:
        return ("%s is the id of %d live entries, and the orchestrator has to give one of them "
                "a new id before it can be deleted" % (eid, len(live)))
    e = live[0]
    at = _instruct_prose_at(lines, e)
    if at is None:
        return "%s is not an instruction item" % eid
    lo, hi = e["line"], e["line"] + len(e["body"].splitlines())
    notes = parse_notes("\n".join(lines)) if notes is None else notes
    return e, [n for n in notes
               if n["kind"] == "INSTRUCTION" and lo <= n["line"] < hi
               and _note_entry_id(n, entries) == eid], at


def instruction_items(lines, entries=None):
    """{eid: [its INSTRUCTION notes]} for every live instruction item in `lines`.
    """
    entries = parse_entries(lines)[0] if entries is None else entries
    notes, out = None, {}
    for e in entries:
        if _instruct_prose_at(lines, e) is None:
            continue
        if notes is None:
            notes = parse_notes("\n".join(lines))
        item = instruction_item(lines, e["id"], entries, notes)
        if isinstance(item, tuple):
            out[e["id"]] = item[1]
    return out


def _retire_instruction_item(lines, eid, who):
    """Move instruction item `eid` to its §99 section as "Deleted with no action".
    """
    item = instruction_item(lines, eid)
    if not isinstance(item, tuple):
        return item
    lines[item[2]] = ("_%s deleted this instruction, so nothing is owed on it._"
                      % sanitize_field(who, 40))
    return _move_to_completed(lines, eid, WITHDRAWN_STATUS, WITHDRAWN_LABEL)


def _seen_by(instructions):
    return ["%s (%s)" % (n["nid"], n["ack"]) for n in instructions
            if n.get("ack") and not n["ack"].startswith("deleted by ")]


def withdraw_doc_instruction(lines, nid, who, when, seen=None):
    """the human deleted the instruction on an `instruct` W item. Changes `lines` only when it succeeds.

    -> (eid, deletion_note_id) when done, None when `nid` is not the instruction on such an
    item (the caller then deletes it as an ordinary note), or a string saying why it failed.
    """
    notes = [n for n in parse_notes("\n".join(lines)) if n["nid"] == nid]
    if len(notes) != 1 or notes[0]["kind"] != "INSTRUCTION":
        return None
    eid = _note_entry_id(notes[0], parse_entries(lines)[0])
    item = instruction_item(lines, eid) if eid else None
    if not isinstance(item, tuple) or nid not in [x["nid"] for x in item[1]]:
        return None
    work = list(lines)
    for n in item[1]:
        work[n["line"] - 1] = render_note(n["nid"], n["who"], n["when"], n["text"],
                                          "INSTRUCTION", ack=("deleted by %s" % who, when),
                                          stored=True, enc=n.get("enc"))
    gone = next_note_id(work, eid)
    said = ("%s deleted this instruction on %s. The original is kept above for the record."
            % (who, when)) if len(item[1]) == 1 else \
        ("%s deleted these %d instructions on %s. The originals are kept above for the record."
         % (who, len(item[1]), when))
    seen = _seen_by(item[1]) if seen is None else seen
    if seen:
        said += " Before that it was seen by: %s." % "; ".join(seen)
    work.insert(item[1][-1]["line"], render_note(gone, who, when, said))
    why = _retire_instruction_item(work, eid, who)
    if why:
        return why
    lines[:] = work
    return eid, gone


def withdraw_instruction_item(lines, eid, who, line):
    """the human deleted instruction item `eid`, which holds no INSTRUCTION note any more.

    -> None when done, or a sentence saying why not; `lines` changes only when it succeeds.
    `line` is the NOTE saying so, rendered by the caller so the lander can put the SAME line
    on the canonical ref.
    """
    item = instruction_item(lines, eid)
    if not isinstance(item, tuple):
        return item
    if item[1]:
        return ("%s still holds instruction %s, so deleting that instruction is what deletes "
                "the item" % (eid, item[1][0]["nid"]))
    work = list(lines)
    why = _place_note_line(work, eid, line) or _retire_instruction_item(work, eid, who)
    if why:
        return why
    lines[:] = work
    return None


TODAY_KEEP_DONE = 30
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_REPEATS_RE = re.compile(r"\*\*Repeats:\*\*\s*([^*\n]*?)\s*(?=\s-\s\*\*|$)", re.M)
_DONE_ON_RE = re.compile(r"\*\*Done on:\*\*\s*([^*\n]*?)\s*(?=\s-\s\*\*|$)", re.M)
_DUE_RE = re.compile(r"\*\*Due:\*\*\s*(\d{4}-\d{2}-\d{2})")
_OPENED_RE = re.compile(r"\*\*Opened:\*\*\s*(\d{4}-\d{2}-\d{2})")


def parse_repeat(text):
    """`daily` -> {"kind": "days", "n": 1} ... or None when the text is not a schedule.
    "no" (or empty) is a one-off: {"kind": "once"}."""
    t = re.sub(r"\s+", " ", (text or "").strip().lower())
    if t in ("", "no", "none", "once", "one-off", "today"):
        return {"kind": "once"}
    if t == "daily":
        return {"kind": "days", "n": 1}
    m = re.fullmatch(r"every (\d{1,3}) days?", t)
    if m and int(m.group(1)) >= 1:
        return {"kind": "days", "n": int(m.group(1))}
    if t == "weekly":
        return {"kind": "days", "n": 7}
    m = re.fullmatch(r"weekly on (\w+)", t)
    if m:
        w = [d for d in WEEKDAYS if d.startswith(m.group(1)[:3])]
        if len(w) == 1:
            return {"kind": "weekday", "n": WEEKDAYS.index(w[0])}
    m = re.fullmatch(r"monthly on (?:the )?(\d{1,2})(?:st|nd|rd|th)?", t)
    if m and 1 <= int(m.group(1)) <= 31:
        return {"kind": "monthday", "n": int(m.group(1))}
    return None


def repeat_label(rule):
    """The canonical spelling written to the file, so every doc says it the same way."""
    k, n = rule["kind"], rule.get("n")
    if k == "once":
        return "no"
    if k == "days":
        return "daily" if n == 1 else "weekly" if n == 7 else "every %d days" % n
    if k == "weekday":
        return "weekly on %s" % WEEKDAYS[n].title()
    return "monthly on %d" % n


def _date(s):
    try:
        return _dt.date.fromisoformat((s or "").strip()[:10])
    except ValueError:
        return None


def next_occurrence(rule, last, start):
    """The first date the item falls due after `last` (or from `start` when never done)."""
    k, n = rule["kind"], rule.get("n")
    if k == "days":
        return start if last is None else last + _dt.timedelta(days=n)
    d = start if last is None else last + _dt.timedelta(days=1)
    for _ in range(400):
        if k == "weekday" and d.weekday() == n:
            return d
        if k == "monthday":
            nxt = (d.replace(day=28) + _dt.timedelta(days=4)).replace(day=1)
            if d.day == min(n, (nxt - _dt.timedelta(days=1)).day):
                return d
        d += _dt.timedelta(days=1)
    return d


def today_state(body, today=None):
    """For an N entry: {rule, label, done_on, last_done, next_due, due, overdue, done_today}.
    None when the body has no **Repeats:** field (not a §2.0 item)."""
    m = _REPEATS_RE.search(body or "")
    if not m:
        return None
    today = today or _dt.date.today()
    rule = parse_repeat(m.group(1)) or {"kind": "once"}
    dm = _DONE_ON_RE.search(body)
    done = sorted(d for d in (_date(x) for x in re.split(r"[,\s]+", dm.group(1) if dm else ""))
                  if d)
    om, um = _OPENED_RE.search(body), _DUE_RE.search(body)
    start = _date(um.group(1)) if um else (_date(om.group(1)) if om else today)
    last = done[-1] if done else None
    if rule["kind"] == "once":
        nxt = None if done else start
    else:
        nxt = next_occurrence(rule, last, start)
    return {"rule": rule, "label": repeat_label(rule), "done_on": [d.isoformat() for d in done],
            "last_done": last.isoformat() if last else "",
            "next_due": nxt.isoformat() if nxt else "",
            "due": bool(nxt and nxt <= today), "overdue": bool(nxt and nxt < today),
            "done_today": today in done}


def today_on_plate(e, today=None):
    """False only for a §2.0 item that is not due today - everything else is unaffected."""
    if not e.get("id", "").startswith("N"):
        return True
    st = today_state(e.get("body"), today)
    return st is None or st["due"]


def ensure_today_section(lines):
    """Insert `## §2.0 Do today` above §2.1 (else above §3, else at the end) if it is absent."""
    if section_span(lines, "2.0"):
        return
    block = ["## §2.0 Do today", "",
             "_what %s does today, and the routines that repeat_" % human_name().title(), ""]
    for want in ("2.1", "2.2", "2.3", "3"):
        for i, l in enumerate(lines):
            m = SECTION_RE.match(l)
            if m and m.group(1) == want and l.startswith("## "):
                lines[i:i] = block
                return
    lines += [""] + block


def _move_to_completed(lines, eid, status, label, dest=None):
    """Cut entry `eid`, give it the archived heading (led by `label`), and put it in its §99
    section - what `archive` does to one entry, and what `_retire_instruction_item` does."""
    e = [x for x in parse_entries(lines)[0] if x["id"] == eid and not x.get("archived")]
    if len(e) != 1:
        return "entry %s is not a live entry here" % eid
    start, end = e[0]["line"] - 1, _entry_end(e[0])
    blk = lines[start:end]
    for k, ln in enumerate(blk):
        if STATUS_RE.search(ln) and "**Status:**" in ln:
            blk[k] = re.sub(r"(\*\*Status:\*\*\s*)[A-Z][A-Z ]*?(?=\s+-\s|\s*$)",
                            r"\g<1>" + status, ln, count=1)
            break
    head = re.sub(r"^(#+)\s*\S*\s*\*{0,2}%s\*{0,2}\s*[-–—.:]\*{0,2}\s*" % re.escape(eid),
                  r"\1 ", blk[0])
    _m = re.match(r"^(#+)\s*(.*)$", head.rstrip())
    blk[0] = "%s %s%s%s" % (_m.group(1), (label + " - ") if label else "", _m.group(2),
                            archived_heading_suffix(eid, status))
    while len(blk) > 1 and not blk[-1].strip():
        blk.pop()
    del lines[start:end]
    _label, span = (None, dest(lines)) if dest else _destination(
        lines, eid, WITHDRAWN_INTO, create=True)
    if span is None:
        lines += ["", "## %s" % WITHDRAWN_INTO, ""]
        span = _section_span(lines, name=WITHDRAWN_INTO)
    _insert_in_section(lines, span, eid, blk)
    return None


def _set_field(line, name, value):
    """Set `**name:** value` on a Status line, appending the field when it is absent."""
    pat = re.compile(r"(\*\*%s:\*\*\s*)[^*\n]*?(\s*(?=\s-\s\*\*|$))" % re.escape(name))
    if pat.search(line):
        return pat.sub(lambda m: m.group(1) + value, line, count=1)
    return line.rstrip() + " - **%s:** %s" % (name, value)


def apply_today(lines, op):
    """One §2.0 change on `lines` (changed in place). -> None, or why it could not be made.

    Pure over the text, so the lander can make the SAME change on the canonical ref when this
    copy is behind it (see `_note_onto_canonical`)."""
    act, eid = op.get("act"), op.get("eid")
    if act == "add":
        have = [e for e in parse_entries(lines)[0] if e["id"] == eid]
        if have:
            return None if lines[have[0]["line"] - 1].strip() == op["block"][1].strip() \
                else "%s is already a different entry" % eid
        ensure_today_section(lines)
        at, _t = _section_insert_at(lines, parse_entries(lines)[1], "today", "N")
        lines[at:at] = list(op["block"])
        return None
    if act == "priority":
        import reader_priority as _rp
        return _rp.apply_priority(lines, eid, op.get("choice"))
    if act == "status":
        import reader_status as _rs
        return _rs.apply_status(lines, op)
    ents = [e for e in parse_entries(lines)[0] if e["id"] == eid and not e.get("archived")]
    if len(ents) != 1:
        return "%s is not a live §2.0 item here" % eid
    e = ents[0]
    st = today_state(e["body"])
    if st is None:
        return "%s has no **Repeats:** field, so it is not a §2.0 item" % eid
    lo = e["line"] - 1
    si = next((i for i in range(lo, _entry_end(e)) if "**Status:**" in lines[i]), None)
    if si is None:
        return "%s has no Status line" % eid
    day = op.get("date")
    if act in ("done", "undo"):
        done = [d for d in st["done_on"] if d != day]
        if act == "done":
            done = sorted(done + [day])[-TODAY_KEEP_DONE:]
        lines[si] = _set_field(lines[si], "Done on", ", ".join(done) or "none")
        if act == "done" and st["rule"]["kind"] == "once":
            return _move_to_completed(lines, eid, "DONE", "")
        return None
    if act == "stop":
        return _move_to_completed(lines, eid, WITHDRAWN_STATUS, "Stopped")
    return "unknown §2.0 change %r" % act


def _priority_landed(text, eid, choice):
    import reader_priority as _rp
    return _rp.priority_in(text, eid, choice)


READER_WRITE_ENV = "ORCHDOC_READER_WRITE"


def cmd_today(args):
    """Add, tick off, untick or stop a §2.0 Do-today item. Verified, then landed."""
    doc = resolve_doc_arg(args.doc)
    if not doc or not doc.exists():
        _note_fail(args, "no such doc: %s" % args.doc)
        return 2
    if (args.add is not None or getattr(args, "priority", None)
            or getattr(args, "choice", None)) and os.environ.get(READER_WRITE_ENV) != "1":
        _note_fail(args, "a priority choice and a new Do-today item are the human's, made in the "
                         "OrchDoc Reader. A session proposes with `orchdoc.py priority` "
                         "(written as `(proposed by oN)`), and ticks off its own items with "
                         "--done / --undo / --stop.")
        return 2
    who = args.by or human_name().title()
    day = (args.date or _dt.date.today().isoformat())[:10]
    if not _date(day):
        _note_fail(args, "--date is YYYY-MM-DD, not %r" % args.date)
        return 2
    rel = os.path.relpath(str(doc), str(PROJECTS)).replace("\\", "/")
    _what = {"cmd": "today", "add": args.add, "done": args.done, "undo": args.undo,
             "stop": args.stop, "repeat": args.repeat, "due": args.due, "date": day,
             "priority": getattr(args, "priority", None), "choice": getattr(args, "choice", None)}
    ref_eid = _ref_next_id(rel, "N") if args.add is not None else None
    with _lock(doc):
        _seen, _bad = _sent_check(args, doc, _what)
        if _bad:
            _note_fail(args, _bad)
            return 2
        if _seen is not None:
            return _sent_reply(args, _seen)
        _orig = doc.read_bytes().decode("utf-8")
        raw = _orig.replace("\r\n", "\n").replace("\r", "\n")
        lines = raw.splitlines()
        pre = list(lines)
        if args.add is not None:
            text = sanitize_note(args.add, enc=False)
            rule = parse_repeat(args.repeat or "no")
            if not text or rule is None:
                _note_fail(args, "an item needs text, and --repeat is one of: no, daily, "
                                 "every N days, weekly, weekly on <day>, monthly on <1-31>")
                return 2
            eid = next_id(parse_entries(lines)[0], "N", raw)
            if ref_eid and int(ref_eid[1:]) > int(eid[1:]):
                eid = ref_eid
            title = text if len(text) <= 160 else text[:160].rsplit(" ", 1)[0] + "..."
            fields = "**Status:** OPEN - **Owner:** %s - **Opened:** %s - **Repeats:** %s" % (
                sanitize_field(who, 40), _now_iso(), repeat_label(rule))
            if rule["kind"] == "once":
                fields += " - **Due:** %s" % ((args.due or day)[:10])
            fields += " - **Done on:** none"
            block = ["", "### %s %s - %s" % (STATUS_MARKER["OPEN"], eid, sanitize_field(title, 200)),
                     "", fields, ""] + ([text, ""] if title != text else [])
            op = {"act": "add", "eid": eid, "block": block}
        else:
            act = "done" if args.done else "undo" if args.undo else "stop" if args.stop else None
            eid = args.done or args.undo or args.stop
            if getattr(args, "priority", None):
                import reader_priority as _rp
                if _rp.choice_norm(getattr(args, "choice", None)) is None:
                    _note_fail(args, "--choice is one of: " + ", ".join(_rp.CHOICES))
                    return 2
                act, eid = "priority", args.priority
            if not act:
                _note_fail(args, "say --add, --done, --undo, --stop or --priority")
                return 2
            op = {"act": act, "eid": eid, "date": day}
            if act == "priority":
                op["choice"] = _rp.choice_norm(args.choice)
        why = apply_today(lines, op)
        if why:
            _note_fail(args, why)
            return 1
        _new = "\n".join(lines) + "\n"
        _new_set = set(_new.split("\n"))
        _job = _queue_job(args, doc, "Do today: %s %s on %s (%s)" % (
            op["act"], eid, doc.name, sanitize_field(who, 40)),
            [l for l in pre if l.strip() and l not in _new_set],
            {"op": "today", "nid": None, "today": op}, _new, pre=raw)
        try:
            write_doc(doc, _new)
        except BaseException:
            _drop_job(_job, doc)
            raise
        _sent = _sent_record(args, doc, _what, {
            "ok": True, "action": op["act"], "id": eid, "doc": doc.name,
            "queued": _job.stem if _job is not None else None})

        back = doc.read_text(encoding="utf-8")
        ents = parse_entries(back.split("\n"))[0]
        live = [e for e in ents if e["id"] == eid and not e.get("archived")]
        _day = _date(day)
        ok = {"add": bool(live),
              "done": (not live) or (today_state(live[0]["body"], _day) or {}).get("done_today"),
              "undo": bool(live) and not (today_state(live[0]["body"], _day)
                                          or {}).get("done_today"),
              "stop": not live,
              "priority": _priority_landed(back, eid, op.get("choice"))}[op["act"]]
        if not ok:
            _drop_job(_job, doc)
            _sent_drop(_sent)
            _note_fail(args, ("%s: the change is NOT in %s after the write" % (eid, doc.name))
                       + _undo_failed_write(doc, _orig, _new))
            return 1
    now = set(back.split("\n"))
    removed = [l for l in pre if l.strip() and l not in now]
    landed, detail = False, "not attempted"
    if _job is not None:
        landed, detail = None, QUEUED_DETAIL % CANONICAL_REF
    elif not getattr(args, "no_land", False):
        _lop = {"op": "today", "nid": None, "today": op}
        try:
            landed, detail = land_note(doc, "Do today: %s %s on %s (%s)" % (
                op["act"], eid, doc.name, sanitize_field(who, 40)),
                removed=removed, op=_lop,
                chain=lambda: (_text_blob(_new), _text_blob(raw)))
        except Exception as _ex:
            landed, detail = False, "the lander raised %s" % type(_ex).__name__
        eid = _lop.get("eid_final") or eid
    _reply = {"ok": True, "action": op["act"], "id": eid, "doc": doc.name,
              "landed": landed, "land_detail": detail,
              "queued": _job.stem if _job is not None else None}
    _sent_update(_sent, _reply)
    if args.json:
        print(json.dumps(_reply))
        return 0
    print("[TODAY %s] %s in %s%s" % (op["act"].upper(), eid, doc.name, "" if landed else
          ("  - %s" % detail) if landed is None else
          "  \u26a0\ufe0f NOT ON %s: %s" % (CANONICAL_REF, detail)))
    return 0


def cmd_instruct(args):
    """Add a doc-level instruction from the human as a new §3 W item. Verified, then landed."""
    doc = resolve_doc_arg(args.doc)
    if not doc or not doc.exists():
        _note_fail(args, "no such doc: %s" % args.doc)
        return 2
    text = sanitize_note(args.text or "")
    if not text:
        _note_fail(args, "--text is empty")
        return 2
    if _orch_note_refusal(args, "an instruction"):
        return 1
    who = args.by or human_name().title()
    when = args.at or _stamp_human()
    owner = doc_owner_id(doc) or "orchestrator"
    # A pasted picture is `[image: <path>]` in the text; a path is no title.
    _plain = re.sub(r"\s+", " ", re.sub(r"\[image:[^\]]*\]", " ",
                                        decode_note_text(text, True))).strip() or "(image)"
    first = re.split(r"(?<=[.!?])\s", _plain, maxsplit=1)[0]
    title = first if len(first) <= INSTRUCT_TITLE_LEN else \
        first[:INSTRUCT_TITLE_LEN].rsplit(" ", 1)[0] + "..."
    rel = os.path.relpath(str(doc), str(PROJECTS)).replace("\\", "/")
    _what = {"cmd": "instruct", "text": args.text or ""}
    ref_eid = _ref_next_id(rel, "W")

    with _lock(doc):
        _seen, _bad = _sent_check(args, doc, _what)
        if _bad:
            _note_fail(args, _bad)
            return 2
        if _seen is not None:
            return _sent_reply(args, _seen)
        _orig = doc.read_bytes().decode("utf-8")
        raw = _orig.replace("\r\n", "\n").replace("\r", "\n")
        lines = raw.splitlines()
        entries, sections = parse_entries(lines)
        eid = next_id(entries, "W", raw)
        if ref_eid and int(ref_eid[1:]) > int(eid[1:]):
            eid = ref_eid
        block = ["", "### %s %s - %s" % (STATUS_MARKER["OPEN"], eid,
                                         sanitize_field("%s's instruction: %s" % (who, title), 200)),
                 "",
                 "**Status:** OPEN - **Owner:** %s - **Opened:** %s - **Enriched:** NO"
                 % (owner, _now_iso()),
                 "", INSTRUCT_PROSE % sanitize_field(who, 40), ""]
        at, _t = _section_insert_at(lines, sections, "work", "W")
        lines[at:at] = block
        nid = "%s.n1" % eid
        newline = render_note(nid, who, when, text, "INSTRUCTION", stored=True, enc=True)
        why = _place_note_line(lines, eid, newline)
        if why:
            _note_fail(args, "could not place the instruction: %s" % why)
            return 1
        _new = "\n".join(lines) + "\n"
        _op = {"op": "add", "nid": nid, "line": newline, "entry": eid, "prev": None,
               "block": block, "block_kind": "work"}
        _job = _queue_job(args, doc, "Instruction %s on %s (%s)" % (
            eid, doc.name, sanitize_field(who, 40)), [], _op, _new, pre=raw)
        try:
            write_doc(doc, _new)
        except BaseException:
            _drop_job(_job, doc)
            raise
        _sent = _sent_record(args, doc, _what, {
            "ok": True, "action": "added", "id": eid, "nid": nid, "doc": doc.name,
            "kind": "INSTRUCTION", "queued": _job.stem if _job is not None else None})

        back = doc.read_text(encoding="utf-8")
        got = [n for n in parse_notes(back) if n["nid"] == nid and n["kind"] == "INSTRUCTION"]
        if not got or not any(e["id"] == eid for e in parse_entries(back.split("\n"))[0]):
            _drop_job(_job, doc)
            _sent_drop(_sent)
            _note_fail(args, ("%s is NOT in %s after the write - it did NOT take"
                              % (eid, doc.name)) + _undo_failed_write(doc, _orig, _new))
            return 1

    landed, land_detail, stuck = False, "not attempted", []
    if _job is not None:
        landed, land_detail = None, QUEUED_DETAIL % CANONICAL_REF
    elif not getattr(args, "no_land", False):
        try:
            landed, land_detail = land_note(
                doc, "Instruction %s on %s (%s)" % (eid, doc.name, sanitize_field(who, 40)),
                removed=[], op=_op, chain=lambda: (_text_blob(_new), _text_blob(raw)))
        except Exception as _ex:
            landed, land_detail = False, "the lander raised %s" % type(_ex).__name__
        stuck = list(_op.get("stuck") or [])
        eid = _op.get("eid_final") or eid
        nid = _op.get("nid_final") or nid

    _reply = {"ok": True, "action": "added", "id": eid, "nid": nid,
              "landed": landed, "land_detail": land_detail, "stuck": stuck,
              "queued": _job.stem if _job is not None else None,
              "doc": doc.name, "kind": "INSTRUCTION", "who": who, "when": when,
              "text": got[0]["text"]}
    _sent_update(_sent, _reply)
    if args.json:
        print(json.dumps(_reply))
        return 0
    print("[INSTRUCTION ADDED] %s in %s%s" % (eid, doc.name, "" if landed else
          ("  - %s" % land_detail) if landed is None else
          ("  ⚠️ NOT ON %s: %s" % (CANONICAL_REF, land_detail))))
    print("  %s (%s): %s" % (who, when, got[0]["text"][:100]))
    return 0


def _note_text(line):
    """The text of a rendered note line, as `parse_notes` reads it; "" for any other line."""
    m = NOTE_LINE_RE.match(line or "")
    return m.group("text").strip() if m else ""


def cmd_land_job(args):
    """`reader_landing.py` runs this.
"""
    def out(**kw):
        res = {"ok": True, "job": Path(args.job).name, "landed": False, "land_detail": "",
               "fail": None, "stuck": [], "nid_final": None, "eid_final": None, "gone": False}
        res.update(kw)
        print(json.dumps(res))
        return 0

    qdir = reader_landing_dir()
    try:
        path = Path(args.job).resolve()
        inside = path.parent == qdir.resolve()
    except OSError:
        inside = False
    if not inside or not JOB_NAME_RE.match(path.name):
        print(json.dumps({"ok": False, "error": "%s is not a job in this workspace's landing "
                                                "queue (%s)" % (args.job, qdir)}))
        return 2
    raw = _job_bytes(path)
    if raw is None:
        return out(gone=True, land_detail="it is no longer queued")
    try:
        job = json.loads(raw.decode("utf-8"))
        if not isinstance(job, dict) or job.get("v") != 1:
            raise ValueError("unknown queue entry format")
        doc = _confine(Path(PROJECTS) / job["doc"])
        snapshot = job["snapshot"]
        if not isinstance(snapshot, str) or not isinstance(job.get("op"), dict) \
                or not isinstance(job.get("message"), str):
            raise ValueError("a field is missing")
    except Exception as ex:
        return out(fail="content", land_detail="the queue entry could not be read (%s: %s)"
                   % (type(ex).__name__, str(ex)[:120]))
    if not doc.exists():
        return out(fail="content", land_detail="%s is no longer on this computer" % job["doc"])

    state = {"raw": raw, "snapshot": snapshot}
    own = job["op"].get("nid")
    seq = int(job.get("seq") or 0)

    def pat(nid):
        return r"(?<![\w.])%s(?!\w)" % re.escape(nid)

    def _ids(o):
        t = o.get("today") if isinstance(o.get("today"), dict) else {}
        return {"nid": o.get("nid") or "", "entry": o.get("entry") or t.get("eid") or ""}

    def _keep_was(j, before):
        """A queued change keeps the ids it was saved under: the page knows it by them."""
        w = j.get("was") if isinstance(j.get("was"), dict) else {}
        j["was"] = {"nid": w.get("nid") or before["nid"],
                    "entry": w.get("entry") or before["entry"]}

    def before_push(fixes, efixes=()):
        """Under the doc's lock: is this still the queued change? Then apply `efixes` (the entry
        this change creates, landing under a new id) and `fixes` (renumbered notes)."""
        with _lock(doc):
            if _job_bytes(path) != state["raw"]:
                return ("changed", "this change's entry in the landing queue changed while it "
                                   "was landing")
            if not fixes and not efixes:
                return None
            queued = [(p, j) for p, j, _r in _jobs_for(doc) if p.name != path.name]
            if any(int(j.get("seq") or 0) < seq for _p, j in queued):
                return ("content", "an earlier change to %s is still waiting to land, and this "
                                   "one would land %s, so it waits for that one"
                        % (doc.name, ("entry %s as %s" % (efixes[0][0], efixes[0][1])) if efixes
                           else ("note %s as %s" % (fixes[0][0], fixes[0][1]))))
            disk0 = doc.read_text(encoding="utf-8")
            used = set(n["nid"] for n in parse_notes(disk0))
            e_used = set(e["id"] for e in parse_entries(disk0.split("\n"))[0])
            for _p, j in queued:
                used |= set(n["nid"] for n in parse_notes(j.get("snapshot") or ""))
                e_used |= set(e["id"] for e in
                              parse_entries((j.get("snapshot") or "").split("\n"))[0])
            for old, new, _h0, _h1 in efixes:
                if new in e_used:
                    return ("changed", "entry id %s was given to another entry on this computer "
                                       "while this change was landing" % new)
            for old, new, _t in fixes:
                if new in used:
                    return ("changed", "note id %s was given to another note on this computer "
                                       "while this change was landing" % new)
                if old != own and any((j.get("op") or {}).get("nid") == old for _p, j in queued):
                    return ("changed", "a later change acts on note %s, which this landing "
                                       "would renumber" % old)
            disk, snap = disk0, state["snapshot"]
            later = [(p, json.loads(json.dumps(j))) for p, j in queued]
            mine = json.loads(json.dumps(job))
            for old, new, head, _nh in efixes:
                # The entry this change creates, renamed wherever it is: in this change's text
                # and operation, in the copy on disk, and in each later change - whose own
                # operation on it (a note added to it, a §2.0 tick) follows it to its new id.
                snap = _rename_entry(snap, old, new, head)
                disk = _rename_entry(disk, old, new, head)
                b = _ids(mine["op"])
                if _rename_op(mine["op"], old, new):
                    _keep_was(mine, b)
                mine["message"] = re.sub(pat(old), new, mine["message"])
                for _p, j in later:
                    j["snapshot"] = _rename_entry(j.get("snapshot") or "", old, new, head)
                    jo = j.get("op") if isinstance(j.get("op"), dict) else None
                    if jo is not None:
                        b = _ids(jo)
                        if _rename_op(jo, old, new):
                            _keep_was(j, b)
                            j["message"] = re.sub(pat(old), new, j.get("message") or "")
            for old, new, text in fixes:
                snap = _apply_note_fixes(snap, [(old, new, text)])
                if old != own:
                    # A note this change carried: rename it wherever it is, by id and text.
                    for _p, j in later:
                        j["snapshot"] = _apply_note_fixes(j.get("snapshot") or "",
                                                          [(old, new, text)])
                    disk = _apply_note_fixes(disk, [(old, new, text)])
                    continue
                # This change's own note: its operation and its commit subject...
                mo = mine["op"]
                b = _ids(mo)
                mo["nid"] = new
                if mo.get("line"):
                    mo["line"] = _renumber_note_line(mo["line"], old, new)
                _keep_was(mine, b)
                mine["message"] = re.sub(pat(old), new, mine["message"])
                # ...then each later change, oldest first, while the note is still there: an
                # edit or delete of THIS note (same line before it, ack aside) is renamed, and
                # the note is renamed in each later text as it stood after that change.
                cur, here = job["op"].get("line") or "", True
                for _p, j in later:
                    jo = j.get("op") or {}
                    if here and jo.get("nid") == old and jo.get("prev") \
                            and note_without_ack(jo["prev"]) == note_without_ack(cur):
                        nxt = jo.get("line") or cur
                        b = _ids(jo)
                        jo["nid"] = new
                        jo["prev"] = _renumber_note_line(jo["prev"], old, new)
                        if jo.get("line"):
                            jo["line"] = _renumber_note_line(jo["line"], old, new)
                        _keep_was(j, b)
                        j["message"] = re.sub(pat(old), new, j.get("message") or "")
                        if jo.get("op") == "edit":
                            cur = nxt
                        elif jo.get("op") == "delete":
                            here = False
                    if here:
                        j["snapshot"] = _apply_note_fixes(j.get("snapshot") or "",
                                                          [(old, new, _note_text(cur))])
                if here:
                    disk = _apply_note_fixes(disk, [(old, new, _note_text(cur))])
            if disk != disk0:
                write_doc(doc, disk, expect=disk0)
                # The copy is still the Reader's own last write, renamed: the run goes on.
                tip = _tip_read(doc)
                if tip and tip.get("tip") == _text_blob(disk0):
                    tip["tip"] = _text_blob(disk)
                    _tip_write(doc, tip)
            for (p, j), (_p0, j0) in zip(later, queued):
                if j != j0:
                    _job_write(p, j)
            mine["snapshot"] = snap
            state["raw"] = _job_write(path, mine)
            state["snapshot"] = snap
        return None

    def chain():
        """(tip, base) of the Reader's run of writes to this doc, once this change is the last
        of them still queued; else None (see `_tip_note`). Called under the doc's lock."""
        if any(p.name != path.name for p, _j, _r in _jobs_for(doc)):
            return None
        rec = _tip_read(doc)
        if not rec or path.name not in (rec.get("jobs") or []):
            return None
        return rec.get("tip"), rec.get("base")

    others = [(p, j) for p, j, _r in _jobs_for(doc) if p.name != path.name]
    avoid = set()
    try:
        _disk = doc.read_text(encoding="utf-8")
        avoid |= set(n["nid"] for n in parse_notes(_disk))
        avoid |= set(e["id"] for e in parse_entries(_disk.split("\n"))[0])
    except OSError:
        pass
    for _p, j in others:
        avoid |= set(n["nid"] for n in parse_notes(j.get("snapshot") or ""))
        avoid |= set(e["id"] for e in parse_entries((j.get("snapshot") or "").split("\n"))[0])
    skip = set(filter(None, ((j.get("op") or {}).get("nid") for _p, j in others)))
    skip.discard(own)
    # An earlier change to this doc still queued (the worker lets a later one past a change it
    # has parked) is in this snapshot too: build from main, never land the snapshot whole.
    rebuild = any(int(j.get("seq") or 0) < seq for _p, j in others)

    op = json.loads(json.dumps(job["op"]))            # land_note writes into it
    try:
        landed, detail = land_note(doc, job["message"], removed=job.get("removed") or [], op=op,
                                   mine_text=snapshot, before_push=before_push, avoid=avoid,
                                   skip=skip, refresh_if=lambda: state["snapshot"],
                                   rebuild=rebuild, chain=chain)
        fail = None if landed else (op.get("fail") or "transient")
    except SystemExit as ex:                          # `_lock` gives up with SystemExit
        landed, fail = False, "transient"
        detail = "the doc stayed locked (%s)" % (str(ex)[:160] or "no reason given")
    except Exception as ex:
        landed, fail = False, "transient"
        detail = "the lander raised %s (%s)" % (type(ex).__name__, str(ex)[:160])
    if (not landed and not op.get("restored") and op.get("op") in ("withdraw", "retire")
            and "is still waiting to land" not in str(detail)
            and (fail == "content" or str(detail).startswith("push refused"))):
        try:
            _undo_unlanded_delete(doc, op, _unlanded_delete_words(detail))
        except Exception:
            pass
    if not landed and op.get("restored"):
        try:
            _job_unlink(path)
        except OSError:
            pass
        return out(ok=False, restored=True, gone=True, land_detail=detail,
                   stuck=list(op.get("stuck") or []),
                   error="%s. Nothing was deleted, on this computer or on %s"
                         % (op.get("plain") or detail, CANONICAL_REF.split("/", 1)[-1]))
    if landed:
        try:
            _job_unlink(path)
        except OSError as ex:
            detail += ("; its queue entry could not be deleted (%s), and the next run finds it "
                       "already landed" % type(ex).__name__)
    return out(landed=bool(landed), land_detail=detail, fail=fail,
               stuck=list(op.get("stuck") or []), nid_final=op.get("nid_final"),
               eid_final=op.get("eid_final"))


def _cmd_note_ack(args, doc):
    """Mark ONE instruction as SEEN.
"""
    if not args.note or not NOTE_ID_RE.match(args.note or ""):
        _note_fail(args, "--ack needs --note n<N> - it acknowledges one instruction")
        return 2
    m = re.search(r"ORCHESTRATOR-DECISIONS-(o\d+)", doc.name)
    who = m.group(1) if m else "orchestrator"
    when = _stamp_human()

    with _lock(doc):
        _orig = doc.read_bytes().decode("utf-8")
        lines = _orig.replace("\r\n", "\n").replace("\r", "\n").splitlines()
        hits = [n for n in parse_notes("\n".join(lines)) if n["nid"] == args.note]
        if not hits:
            _note_fail(args, "no note %s in %s" % (args.note, doc.name))
            return 1
        if len(hits) > 1:
            _note_fail(args, "note %s appears %d times in %s - fix that by hand first"
                       % (args.note, len(hits), doc.name))
            return 1
        n = hits[0]
        if n.get("ack"):
            if args.json:
                print(json.dumps({"ok": True, "action": "already-acked",
                                  "nid": n["nid"], "doc": doc.name, "ack": n["ack"]}))
            else:
                print("[INSTRUCTION already acknowledged] %s in %s - %s"
                      % (n["nid"], doc.name, n["ack"]))
            return 0
        lines[n["line"] - 1] = render_note(n["nid"], n["who"], n["when"], n["text"],
                                           n.get("kind") or "NOTE", (who, when), stored=True,
                                           enc=n.get("enc"))
        _new = "\n".join(lines) + "\n"
        write_doc(doc, _new)

        after = [x for x in parse_notes(doc.read_text(encoding="utf-8"))
                 if x["nid"] == args.note]
        if not after or not after[0].get("ack"):
            _note_fail(args, ("%s is still unacknowledged in %s after the write - it did NOT "
                              "take" % (args.note, doc.name))
                       + _undo_failed_write(doc, _orig, _new))
            return 1
    if args.json:
        print(json.dumps({"ok": True, "action": "acked", "nid": args.note,
                          "doc": doc.name, "ack": after[0]["ack"],
                          "text": after[0]["text"]}))
        return 0
    print("[INSTRUCTION acknowledged] %s in %s by %s" % (args.note, doc.name, who))
    try:
        _st, _n, _ = behind_canonical(doc.parent, fetch=False)
    except Exception:
        _st, _n = "unknown", 0
    if _st == "behind":
        print("\n  \u26a0\ufe0f THIS ACK WILL NOT SURVIVE A LANDING FROM ANOTHER TREE.",
              file=sys.stderr)
        print("     %s is %d commit(s) behind %s, so `commit` from here would be REFUSED"
              % (doc.parent, _n, CANONICAL_REF), file=sys.stderr)
        print("     as stale - which means this marker cannot travel with a commit made here.",
              file=sys.stderr)
        print("     A `git reset --hard` in the worktree you land from will erase it, and the",
              file=sys.stderr)
        print("     only symptom is the note reappearing as unacknowledged.\n", file=sys.stderr)
        print("     DO THIS: ack in the tree you will LAND from, so the marker and the prose",
              file=sys.stderr)
        print("     travel in one commit.\n", file=sys.stderr)
    print("  %s" % after[0]["text"][:120])
    print()
    print("  Acknowledged means SEEN, not done. What you do about it goes in the entry.")
    print("  Land it with:")
    print("    python <scripts>/orchdoc.py commit --doc %s -m \"...\"" % doc.name)
    return 0


def _note_fail(args, why):
    """One refusal path, in both shapes. The browser needs JSON; a terminal needs a sentence.
    """
    if getattr(args, "json", False):
        print(json.dumps({"ok": False, "error": why}))
    else:
        print("[REFUSE] %s" % why, file=sys.stderr)


def _stamp_human():
    """
    Deliberately not the ISO stamp `restamp` writes: an attestation is a machine-comparable
    record and a note is a sentence somebody wrote on a Tuesday afternoon.
    """
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _added_forms(raw):
    """The forms an added line can take in gate 1's comparison: as written, and as gate 1
    reduces it (`_authored_form`). `lost` holds the reduced form, so matching the raw text
    alone never recognised a heading, a field line or a note."""
    out = {raw.strip()}
    try:
        f = _authored_form(raw)
    except Exception:
        f = None
    if f:
        out.add(f.strip())
    out.discard("")
    return out


ORCH_SUBJECT_RE = re.compile(r"^(o\d+(?:L\d+)?)(?![A-Za-z0-9])", re.IGNORECASE)


def _commit_origin(subject, landers, me):
    """"own", "other" or "unknown" - whose landing a commit is, as far as it says.
    """
    me_l = (me or "").lower()
    if landers:
        if me_l and all(l.strip().lower() == me_l for l in landers):
            return "own"
        return "other"
    if READER_NOTE_SUBJECT_RE.match(subject or ""):
        return "other"
    m = ORCH_SUBJECT_RE.match((subject or "").strip())
    if m:
        return "own" if (me_l and m.group(1).lower() == me_l) else "other"
    return "unknown"


def _added_after_base(rel, ref, base, me):
    """
    -> a set of lines (see `_added_forms`), or None when that cannot be answered.
    """
    if not base:
        return None
    rc, out, _ = git(["log", "-p", "--unified=0", "--diff-algorithm=histogram",
                      "--format=ORCHDOC-COMMIT%%x09%%H%%x09%%(trailers:key=%s,valueonly,"
                      "separator=%%x2C)%%x09%%s" % LANDER_TRAILER,
                      "%s..%s" % (base, ref), "--", rel])
    if rc != 0:
        return None
    added, count, in_hunk = set(), False, False
    for ln in out.split("\n"):
        if ln.startswith("ORCHDOC-COMMIT\t"):
            parts = ln.split("\t", 3)
            landers = [x for x in (parts[2] if len(parts) > 2 else "").split(",") if x.strip()]
            subj = parts[3] if len(parts) > 3 else ""
            count = _commit_origin(subj, landers, me) == "other"
            in_hunk = False
            continue
        if ln.startswith("diff --git "):
            in_hunk = False
            continue
        if ln.startswith("@@"):
            in_hunk = True
            continue
        if in_hunk and count and ln.startswith("+"):
            added |= _added_forms(ln[1:])
    return added


def _split_by_seen(doc, rel, ref, lost, base=None):
    """(seen, unseen) - lines the author had when they wrote this copy, and lines they did not.

    Who "me" is: `running_orchestrator()`, else the orchestrator the doc's filename names.

    Fails CLOSED: if the mtime, the log or the base cannot be read, everything counts as unseen
    and the gate behaves exactly as it did before. A discriminator that cannot answer must not
    become permission.
    """
    me = running_orchestrator() or doc_owner_id(doc)
    try:
        mtime = _dt.datetime.fromtimestamp(doc.stat().st_mtime).astimezone()
    except OSError:
        return [], list(lost)
    since = mtime.isoformat()
    rc, blob, _ = git(["log", "--format=%H", "--since=%s" % since, ref, "--", rel])
    if rc != 0:
        return [], list(lost)
    shas = [s for s in blob.split() if s]
    added = set()
    for sha in shas[:40]:
        # `histogram`: under the default algorithm a reorder lists every moved line as added.
        rc2, diff, _ = git(["show", "--format=", "--unified=0", "--diff-algorithm=histogram",
                            sha, "--", rel])
        if rc2 != 0:
            continue
        in_hunk = False
        for ln in diff.split("\n"):
            if ln.startswith("diff --git "):
                in_hunk = False
            elif ln.startswith("@@"):
                in_hunk = True
            elif in_hunk and ln.startswith("+"):
                added |= _added_forms(ln[1:])
    after_base = _added_after_base(rel, ref, base, me)
    if after_base is None:
        return [], list(lost)
    added |= after_base
    seen, unseen = [], []
    for l in lost:
        (unseen if l.strip() in added else seen).append(l)

    me = running_orchestrator()
    if seen and me:
        keep = []
        for l in seen:
            m = re.search(r"\*\*Attested-by:\*\*\s*(o\d+)\b", l)
            if m and m.group(1).lower() != me.lower():
                unseen.append(l)
            else:
                keep.append(l)
        seen = keep
    return seen, unseen


def _advance_plain_checkout(base):
    """Never overwrites anything: a landed file is
    staged only when the copy on disk is byte-identical to the landed one, and git refuses the merge
    if any other changed file is dirty. Returns one line for the report."""
    if (Path(base) / ".shared" / "scripts").is_dir():
        return "left where it was (this workspace keeps its own tools)"
    branch = CANONICAL_REF.split("/", 1)[-1]
    rc, cur, _ = git(["symbolic-ref", "--short", "HEAD"], cwd=base)
    if rc != 0 or cur.strip() != branch:
        return "left where it was (not on %s)" % branch
    _, names, _ = git(["diff", "--name-only", "HEAD", CANONICAL_REF], cwd=base)
    same = []
    for n in names.splitlines():
        p = Path(base) / n
        _, disk, _ = git(["hash-object", "--path", n, str(p)], cwd=base) if p.is_file() else (1, "", "")
        _, tip, _ = git(["rev-parse", "%s:%s" % (CANONICAL_REF, n)], cwd=base)
        if disk.strip() and disk.strip() == tip.strip():
            same.append(n)
    if same:
        git(["add", "--"] + same, cwd=base)
    rc, _, err = git(["merge", "--ff-only", "--quiet", CANONICAL_REF], cwd=base)
    if rc == 0:
        return "moved to %s" % CANONICAL_REF
    if same:
        git(["reset", "-q", "--"] + same, cwd=base)
    return "left where it was (%s)" % ((err or "git refused").splitlines()[0][:90])


def _write_landed(path, data):
    """Write landed bytes into the shared checkout. Returns the attempt that succeeded.
    """
    try:
        _deliver = _deliver_module()
        writer = _deliver.write_bytes_durably if _deliver else None
    except Exception:
        writer = None
    if writer is None:
        Path(path).write_bytes(data)
        return 1
    return writer(path, data)


DELIVER_TIMEOUT = 30
DELIVER_FINISH = True
DELIVER_SCRIPT = None


def _real_deliver_path():
    """None if neither exists, or if the one found predates `--summary` and would run the 69s
    survey."""
    for cand in (Path(__file__).resolve().parent / "deliver.py",
                 Path(PROJECTS) / ".shared" / "scripts" / "deliver.py"):
        try:
            if cand.exists():
                return cand if "--summary" in cand.read_text(encoding="utf-8",
                                                               errors="replace") else None
        except OSError:
            continue
    return None


def _deliver_module():
    """Never an older copy: loading one of those in-process would run its import-time stdout swap
    inside this process."""
    import importlib.util
    path = _real_deliver_path()
    if path is None:
        return None
    spec = importlib.util.spec_from_file_location("orchdoc_deliver", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _finish_delivery_detached(script, base):
    """True when it was started. Never raises. Tests set DELIVER_FINISH = False.
    """
    if not DELIVER_FINISH:
        return False
    try:
        kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
              "stderr": subprocess.DEVNULL}
        if os.name == "nt":
            try:
                from no_window import SERVER_FLAGS as _f
            except ImportError:
                _f = 0x08000000 | 0x00000200
            kw["creationflags"] = _f
        else:
            kw["start_new_session"] = True
        subprocess.Popen([sys.executable, str(script), "--apply", "--summary", "--no-fetch",
                          "--repo", str(base), "--caller", "orchdoc.py-finish"], **kw)
        return True
    except Exception:
        return False


def _checkout_human_reads():
    """The main worktree of the repo PROJECTS is in - the checkout every session reads."""
    rc, out, _ = git(["worktree", "list", "--porcelain"], cwd=PROJECTS)
    if rc == 0:
        for line in out.split("\n"):
            if line.startswith("worktree "):
                return Path(line[len("worktree "):].strip())
    return _main_worktree() or Path(PROJECTS)


def _deliver_landed_files():
    """After a landing, bring EVERY landed file into the shared checkout, not only this doc.
    """
    try:
        base = _checkout_human_reads()
        script = Path(DELIVER_SCRIPT) if DELIVER_SCRIPT else _real_deliver_path()
        if script is None or not script.exists():
            print("  deliver: skipped - no current delivery script beside orchdoc.py or in %s"
                  % (Path(PROJECTS) / ".shared" / "scripts"))
            print("  local branch: %s" % _advance_plain_checkout(base))
            return
        proc = subprocess.Popen(
            [sys.executable, str(script), "--apply", "--summary", "--no-fetch",
             "--repo", str(base), "--caller", "orchdoc.py"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace", creationflags=flags())
        try:
            out, err = proc.communicate(timeout=DELIVER_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.communicate(timeout=10)
            except Exception:
                pass
            try:
                _deliver = _deliver_module()
                if _deliver:
                    _deliver.release_lock_if_pid(base, proc.pid)
            except Exception:
                pass
            finishing = _finish_delivery_detached(script, base)
            print("  deliver: skipped - no answer within %ds, so it was stopped. The landing"
                  " stands;%s" % (DELIVER_TIMEOUT, " a detached run is finishing the delivery"
                                  " (see ~/.claude/state/deliver.log)." if finishing
                                  else " run deliver.py --apply by hand."))
            return
        lines = [l.strip() for l in (out or "").splitlines()]
        said = [l for l in lines if l.startswith("deliver:")]
        # An ALERT (the shared checkout's `main` holds commits origin does not) is printed in
        # full, above the summary line - it names commits a person has to deal with.
        alert, inside = [], False
        for raw in (out or "").splitlines():
            if raw.startswith("deliver: ALERT"):
                inside = True
            elif inside and not raw.startswith((" ", "\t")):
                inside = False
            if inside:
                alert.append(raw.rstrip())
        for a in alert:
            print("  %s" % a[:300])
        said = [l for l in said if not l.startswith("deliver: ALERT")]
        if said:
            print("  %s" % said[-1][:300])
            return
        crashed = [l for l in lines if l.startswith("deliver error:")]
        if crashed:
            print("  deliver: skipped - %s" % crashed[-1][len("deliver "):][:200])
            return
        tail = [l.strip() for l in ((err or "") + "\n" + (out or "")).splitlines() if l.strip()]
        print("  deliver: skipped - it exited %s without a summary: %s"
              % (proc.returncode, (tail[-1] if tail else "no output")[:200]))
    except Exception as e:
        print("  deliver: skipped - %s: %s" % (type(e).__name__, str(e)[:200]))


def _report_delivery(doc):
    """Does the copy the human actually opens match what just landed? Say so either way.
    """
    base = _main_worktree() or pathlib.Path(PROJECTS)
    reader = base / doc.name
    if not reader.exists():
        print("  ⚠️ NOT ON THE HUMAN'S DISK: %s is missing from %s" % (doc.name, base))
        return
    # Every `hash-object` of the reader's copy in this function passes `--path doc.name`: the
    # blobs it is compared with were stored with the attributes of that repo path, and without
    # `--path` git looks attributes up by the absolute path, which matches no slashed pattern.
    rc_a, disk, _e = git(["hash-object", "--path", doc.name, str(reader)])
    rc_b, landed, _e2 = git(["rev-parse", "%s:%s" % (CANONICAL_REF, doc.name)])
    if rc_a != 0 or rc_b != 0:
        print("  ⚠️ could not compare the reader's copy - check by hand: %s" % reader)
        return
    if disk.strip() == landed.strip():
        print("  DELIVERED. The copy the human reads at %s is byte-identical." % reader)
        return

    if doc.resolve() != reader.resolve():
        _stale_not_unique = False
        _rc_h2, _disk_sha, _ = git(["hash-object", "--path", doc.name, str(reader)])
        _disk_sha = _disk_sha.strip()
        if _disk_sha:
            _rc_l, _hist, _ = git(["rev-list", "--max-count=500", CANONICAL_REF, "--",
                                   doc.name], cwd=base)
            for _sha in (_hist.split() if _rc_l == 0 else []):
                _rc_b2, _blob_sha, _ = git(["rev-parse", "%s:%s" % (_sha, doc.name)], cwd=base)
                if _rc_b2 == 0 and _blob_sha.strip() == _disk_sha:
                    _stale_not_unique = True
                    break

        _uncommitted = False
        _unanswerable = ""
        try:
            _rc_d, _diff, _err_d = git(["diff", "--quiet", "%s" % CANONICAL_REF, "--",
                                        doc.name], cwd=base)
            if _rc_d in (0, 1):
                _uncommitted = (_rc_d == 1 and not _stale_not_unique)
            else:
                _uncommitted = True
                _unanswerable = _err_d or ("git exited %d" % _rc_d)
        except Exception as _ex_d:
            _uncommitted = True        # cannot tell -> treat as at risk, never as safe
            _unanswerable = str(_ex_d)[:120]
        if _uncommitted and _unanswerable:
            # A malformed question and a genuine difference call for opposite responses: one
            # is fixed by reconciling a file, the other by fixing this tool. Merging them is
            # how the first masqueraded as the second for a fleet-wide run of landings.
            print("  \u26a0\ufe0f NOT DELIVERED - the check could not be answered, so it")
            print("     refused rather than guessing: %s" % _unanswerable)
            print("     This is a defect in the CHECK, not a dirty copy. Compare by hand:")
            print("       git -C %s diff %s -- %s" % (base, CANONICAL_REF, doc.name))
            return
        if _uncommitted:
            print("  \u26d4 NOT DELIVERED, ON PURPOSE. %s in %s differs from the canonical ref"
                  % (doc.name, base))
            print("     and may hold work that is on NO ref - which gate 1 cannot see, because")
            print("     gate 1 only compares against the ref. Overwriting it could destroy an")
            print("     entry that exists nowhere else.")
            print("     ASK WHY THAT COPY IS DIRTY. The one confirmed case was a LANE writing")
            print("     into the shared checkout - which it cannot land from. Stopping the")
            print("     overwrite is not the fix; a lane writing there is.")
            print("     Reconcile by hand, or land that copy first:")
            print("       git -C %s diff %s -- %s" % (base, CANONICAL_REF, doc.name))
            return
        _p = subprocess.run(["git", "-C", str(PROJECTS), "cat-file", "-p",
                             "%s:%s" % (CANONICAL_REF, doc.name)],
                            capture_output=True, timeout=60, creationflags=flags())
        if _p.returncode == 0:
            try:
                _tries = _write_landed(reader, _p.stdout)
                _rc_v, _now, _ = git(["hash-object", "--path", doc.name, str(reader)])
                if _rc_v == 0 and _now.strip() == landed.strip():
                    print("  DELIVERED. Wrote the landed copy to %s%s"
                          % (reader, "" if _tries == 1 else " (on try %d)" % _tries))
                else:
                    print("  \u26a0\ufe0f WROTE %s but it does not hash to what landed."
                          % reader)
                    print("     Do not trust that copy - compare it by hand.")
                return
            except OSError as e:
                print("  \u26a0\ufe0f could not write the reader's copy: %s" % e)
    print("  ⛔ NOT DELIVERED. %s on the human's disk DIFFERS from what just landed." % doc.name)
    print("     The human is reading something else. Point them at the landed version, or")
    print("     reconcile their copy - do NOT assume 'it is on main' reached them.")


def untracked_orchdocs():
    """OrchDocs that exist on disk and are on NO ref. Invisible to every freshness check.
    """
    root = pathlib.Path(PROJECTS)
    out = []
    for p in sorted(root.glob("ORCHESTRATOR-DECISIONS-*.md")):
        rc, _o, _e = git(["rev-parse", "--verify", "%s:%s" % (CANONICAL_REF, p.name)])
        if rc != 0:
            out.append(p.name)
    return out


def cmd_restamp(args):
    """Write an Attested-by clause onto ONE entry's plate line, with the bar applied first.
    """
    doc = resolve_doc_arg(args.doc)
    if not doc or not doc.exists():
        print("no such doc: %s" % doc, file=sys.stderr)
        return 2
    if refuse_if_not_mine(doc, "restamp", getattr(args, "not_mine", False)):
        return 1

    why = (args.because or "").strip()
    if len(why) < MIN_ATTESTATION_CHARS or RUBBER_STAMP_RE.match(why):
        print("[REFUSE] --because must NAME WHAT MOVED and why the entry survives it "
              "(%d+ chars)." % MIN_ATTESTATION_CHARS, file=sys.stderr)
        print("         'still current' / 'no change' is what E-RUBBERSTAMP exists to stop,",
              file=sys.stderr)
        print("         and refusing it here costs a retry instead of a lint round-trip.",
              file=sys.stderr)
        print("         `check --doc %s` names the mover and, for an OrchDoc, the entry ids"
              % args.doc, file=sys.stderr)
        print("         that commit actually touched.", file=sys.stderr)
        return 1

    who = args.by or actor_for(doc)
    if who.endswith("?"):
        print("[REFUSE] I can only INFER that you are %s, and an attestation records WHO"
              % who[:-1], file=sys.stderr)
        print("         checked something. Say it explicitly:", file=sys.stderr)
        print("           --by %s          (this call only)" % who[:-1], file=sys.stderr)
        print("           ORCHDOC_ME=%s    (every call in this session)" % who[:-1],
              file=sys.stderr)
        return 1
    rc = write_restamp(doc, args.id, why, who, args.at or _now_iso())
    if rc == 0:
        _warn_repeat_mover(doc, args.id, why)
        print_needle_hints(doc, [args.id])
        print("NEXT: orchdoc.py check --doc %s   then commit --doc %s --commit"
              % (args.doc, args.doc))
    return rc


def _heading_names_id(doc_text, eid):
    rows = doc_text.split("\n")
    fz = fenced_lines(rows)
    pat = re.compile(r"^#{1,6}\s.*\b%s\b" % re.escape(eid))
    return any(i not in fz and pat.match(r) for i, r in enumerate(rows))


def _warn_repeat_mover(doc, eid, why):
    """Say so when this entry has now been attested 3+ times against the SAME artifact.
    """
    try:
        text = doc.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    body, found = [], False
    _rows = text.split("\n")
    _fz = fenced_lines(_rows)
    for _i, raw in enumerate(_rows):
        if _i in _fz:
            continue
        m = re.match(r"^#{3,6}\s+[^A-Za-z0-9]*([A-Z]{1,3}\d+[a-z]?)\b", raw)
        if m:
            if found:
                break
            found = m.group(1) == eid
            continue
        if found:
            body.append(raw)
    names = re.compile(r"([A-Za-z0-9][\w.-]*\.(?:md|py|ts|tsx|json))")
    seen = []
    for raw in body:
        s = re.search(r"\*\*Attested-by:\*\*[^-]*-\s*(.*)", raw)
        if s:
            hit = names.findall(s.group(1))
            if hit:
                seen.append(hit[0])
    mine = names.findall(why or "")
    if mine:
        seen.append(mine[0])
    if not seen:
        return
    top = max(set(seen), key=seen.count)
    n = seen.count(top)
    if n < 3:
        return
    print()
    print("  [NOTE] %s has now been attested %d times naming %s." % (eid, n, top))
    print("         That is usually fine - some entries rest on a file that changes often.")
    print("         But it is also where a WRONG cause hides: one entry carried five stamps")
    print("         repeating one false reason, and the real fix was a single missing line.")
    print("         Worth asking once: is the cause still the cause, or just the last answer?")


def write_restamp(doc, eid, why, who, stamp):
    """Record one attestation on `eid` BENEATH its plate line in `doc`. The write, alone.

    Every prior justification is migrated, never dropped, and the operation is IDEMPOTENT
    in both directions - re-running it on an entry whose plate line has already grown
    SHORTENS that line, and re-running it on a stamped entry writes nothing at all.
    """
        # `expect` is the RAW text, so a landing that arrives between this
    # read and the write below refuses instead of being erased.
    _before_txt = doc.read_text(encoding="utf-8")
    lines = _before_txt.splitlines()
    entries, _ = parse_entries(lines)
    match = [e for e in entries if e["id"] == eid]
    if not match:
        print("[REFUSE] no entry with id %s in %s" % (eid, doc.name), file=sys.stderr)
        return 1
    if len(match) > 1:
        print("[REFUSE] id %s appears %d times - fix E-DUPID first" % (eid, len(match)),
              file=sys.stderr)
        return 1
    e = match[0]

    # ONE LINE, ONE STAMP. `--because` arrives from a shell and may carry newlines; a stamp
    # that wraps into a second physical line is a second line the plate has to hold, and
    # `reviewed_of` would read the wrap as continuation prose. Collapse it here, once.
    why = re.sub(r"\s+", " ", why).strip()

    end = e["line"] + len(e["body"].splitlines())
    si = None
    for i in range(e["line"] - 1, min(end, len(lines))):
        if "**Status:**" in lines[i]:
            si = i
            break
    if si is None:
        si = e["line"] - 1
        no_status = True
    else:
        no_status = False

    fi = si + 1
    if no_status:
        # No field block exists - the stamp is the first thing under the
        # heading, and the scan below would otherwise walk into the prose.
        while fi < len(lines) and not lines[fi].strip():
            fi += 1

    others, prior = [], []
    while fi < min(end, len(lines)):
        stripped = lines[fi].strip()
        if not stripped:
            break                                   # the blank line closes the field block
        if not stripped.startswith("**"):
            # a continuation only if a field is open above it, and only if it is prose
            if not others or stripped.startswith(("- ", "* ", "#", ">", "|")):
                break
            others.append(lines[fi])
            fi += 1
            continue
        if STAMP_LINE_RE.match(lines[fi]):
            prior.extend(split_stamps(lines[fi])[1])
        else:
            others.append(lines[fi])
        fi += 1

    plate, inline = split_stamps(lines[si])

    rich = [s for s in inline if s[1] or s[2]]
    bare = [s for s in inline if not (s[1] or s[2])]
    prior = rich + prior
    if not prior and bare:
        prior = bare[:1]

    stamps, seen = [], set()
    already = any(s[0] == stamp and s[1].lower() == who.lower() for s in prior)
    for s in ([] if already else [(stamp, who, why)]) + prior:
        key = (s[0], s[1].lower(), s[2])
        if key in seen:
            continue
        seen.add(key)
        stamps.append(s)
    if not stamps:
        stamps = [(stamp, who, why)]
    # Newest first, and a stable sort keeps same-timestamp stamps in the order written.
    stamps.sort(key=lambda s: s[0], reverse=True)

    new_block = others + [render_stamp(*s) for s in stamps]

    if no_status:
        # The Reviewed date rides on the stamp itself, which is the only record there is.
        if lines[si + 1:fi] == new_block:
            print("[ok] %s already carries a stamp at %s" % (eid, stamp))
            return 0
        lines[si + 1:fi] = new_block
    else:
        new_status = "%s - **Reviewed:** %s" % (plate, stamps[0][0])
        if lines[si] == new_status and lines[si + 1:fi] == new_block:
            print("[ok] %s already carries a stamp at %s" % (eid, stamp))
            return 0

        lines[si] = new_status
        lines[si + 1:fi] = new_block
    write_doc(doc, "\n".join(lines) + "\n", expect=_before_txt)

    after, _ = parse_entries(doc.read_text(encoding="utf-8").splitlines())
    got = next((reviewed_of(x["body"]) for x in after if x["id"] == eid), (None, None))
    if got[0] != stamps[0][0]:
        print("[FAILED] %s reads %s after the write, not %s - the edit did NOT take."
              % (eid, got[0] or "no stamp", stamps[0][0]), file=sys.stderr)
        return 1
    print("[RESTAMPED] %s in %s" % (eid, doc.name))
    print("            %s at %s" % (who, stamp))
    if len(stamps) > 1:
        print("            plate line now %d chars; %d stamp(s) beneath it"
              % (len(plate if no_status else new_status), len(stamps)))
    return 0


def apply_resolve(lines, eid, status, ruling, depends=None, adopt=False, who="orchestrator",
                  stamp=None, doc_name="the doc"):
    """The edit `resolve` makes, on `lines` (changed in place). -> None, or the refusal text.
    """
    stamp = stamp or _now_iso()
    entries, _ = parse_entries(lines)
    match = [e for e in entries if e["id"] == eid]
    if not match:
        return "[REFUSE] no entry with id %s in %s" % (eid, doc_name)
    if len(match) > 1:
        return "[REFUSE] id %s appears %d times - fix E-DUPID first" % (eid, len(match))
    e = match[0]

    status = status.upper()
    changed = False
    end = e["line"] + len(e["body"].splitlines())
    for i in range(e["line"], min(end, len(lines))):
        m = STATUS_RE.search(lines[i])
        if not m:
            continue
        lines[i] = lines[i][:m.start(1)] + status + lines[i][m.end(1):]
        lines[i] = re.sub(r"\*\*Enriched:\*\*\s*\w+", "**Enriched:** YES", lines[i])

        # CARRY THE HEADING MARKER WITH THE STATUS. The marker is DERIVED from the status,
        # so changing one without the other is the drift E-MARKERDRIFT exists to catch -
        # and `resolve` was creating it on every single call. The user then saw a failing
        # check immediately after a successful resolve, which is the cry-wolf failure:
        # the gate firing on the tool's own output teaches the reader that red is normal.
        want_mark = STATUS_MARKER.get(status)
        if want_mark:
            h = e["line"] - 1
            if 0 <= h < len(lines) and lines[h].lstrip().startswith("#"):
                bare = re.sub(r"^(#+)\s*(?:%s)?\s*" % "|".join(
                    re.escape(v) for v in set(STATUS_MARKER.values())),
                    r"\1 ", lines[h]).rstrip()
                hashes, _, rest = bare.partition(" ")
                lines[h] = "%s %s %s" % (hashes, want_mark, rest.strip())
        note = "**Resolved %s:** %s" % (stamp, sanitize_body(ruling))
        end_i = e["line"] + len(e["body"].splitlines())
        existing = [j for j in range(e["line"], min(end_i, len(lines)))
                    if lines[j].startswith("**Resolved ")]
        if existing:
            lines[existing[0]] = note
            for j in reversed(existing[1:]):
                del lines[j]
        else:
            lines.insert(i + 1, "")
            lines.insert(i + 2, note)
        changed = True
        break
    if not changed:
        if not adopt:
            return ("[REFUSE] entry %s has no Status field to flip.\n"
                    "         Expected a line like:  %s\n"
                    "         Re-run with --adopt to insert one, or use `migrate`"
                    " for the whole doc." % (eid, STATUS_CANONICAL))
        field = ("**Status:** %s - **Owner:** the human - **Enriched:** YES" % status)
        lines.insert(e["line"], "")
        lines.insert(e["line"] + 1, field)
        lines.insert(e["line"] + 2, "")
        lines.insert(e["line"] + 3,
                     "**Resolved %s:** %s" % (stamp, sanitize_body(ruling)))
        changed = True

    if changed:
        att = ("**Attested-by:** %s at %s - ruled in this session; the ruling text above "
               "states what was decided and the Depends edge states what it rests on"
               % (sanitize_field(who, 40), stamp))
        if not any(l.startswith("**Attested-by:**")
                   for l in lines[e["line"]:e["line"] + 8]):
            for k in range(e["line"], min(len(lines), e["line"] + 8)):
                if lines[k].startswith("**Status:**"):
                    lines.insert(k + 1, att)
                    break

    if changed and depends:
        dep_line = "**Depends:** %s" % sanitize_field(refuse_if_too_long(depends, 200, "depends"), 200)
        already = any(l.startswith("**Depends:**")
                      for l in lines[e["line"]:e["line"] + 6])
        if not already:
            for k in range(e["line"], min(len(lines), e["line"] + 6)):
                if lines[k].startswith("**Status:**"):
                    lines.insert(k + 1, dep_line)
                    break

    return None


def _resolve_for_reader(args, doc):
    """
    The same edit as `resolve` (`apply_resolve`, then the move to §99 that `archive` would make),
    made as a Reader change: under the doc's lock, journalled in the landing queue
    (`--queue-landing`) so the worker lands it, answered as one JSON line, and recorded by
    client id so a resend writes nothing. No model is involved anywhere.

    `--undo-json` is the "undo" a first call returned; it puts the entry back as it was.
    Prints {"ok", "action", "id", "doc", "status", "landed", "land_detail", "queued", "undo"}.
    """
    try:
        undo_in = json.loads(args.undo_json) if getattr(args, "undo_json", None) else None
    except ValueError:
        _note_fail(args, "--undo-json is not JSON")
        return 2
    import reader_status as _rs
    eid = args.id
    status = (args.status or "").upper()
    if undo_in is None and status not in _rs.STATUS_FOR.values():
        _note_fail(args, "the Reader marks an entry %s" % " or ".join(sorted(_rs.STATUS_FOR.values())))
        return 2
    # The clock time in the ruling is left out of the resend key: the page sends the same click
    # again with the same id after the minute has turned, and that must still be one change.
    _what = {"cmd": "resolve", "id": eid, "status": status,
             "ruling": re.sub(r"at \d\d:\d\d PT", "at HH:MM PT", args.ruling or ""),
             "depends": args.depends, "undo": undo_in}
    stamp = _now_iso()
    with _lock(doc):
        _seen, _bad = _sent_check(args, doc, _what)
        if _bad:
            _note_fail(args, _bad)
            return 2
        if _seen is not None:
            return _sent_reply(args, _seen)
        _orig = doc.read_bytes().decode("utf-8")
        raw = _orig.replace("\r\n", "\n").replace("\r", "\n")
        lines = raw.splitlines()
        pre = list(lines)
        if undo_in is not None:
            op = {"act": "status", "eid": eid, "undo": undo_in}
            message = "Reader: undo marking %s on %s (the human)" % (eid, doc.name)
        else:
            op = {"act": "status", "eid": eid, "status": status, "ruling": args.ruling,
                  "depends": args.depends, "stamp": stamp, "who": _rs.WHO}
            message = "Reader: %s marked %s on %s (the human)" % (eid, status, doc.name)
        why = apply_today(lines, op)
        if why:
            _note_fail(args, why)
            return 1
        _new = "\n".join(lines) + "\n"
        if undo_in is None and _write_guard_refuses(doc, raw, _new, "resolve", force_open={eid}):
            _note_fail(args, "the reason you typed was refused (see the line above); change it "
                             "and try again")
            return 1
        undo_out = None if undo_in is not None else _rs.make_undo(pre, lines, eid)
        _new_set = set(_new.split("\n"))
        _removed = [l for l in pre if l.strip() and l not in _new_set]
        _job = _queue_job(args, doc, message, _removed,
                          {"op": "today", "nid": None, "today": op}, _new, pre=raw)
        try:
            write_doc(doc, _new)
        except BaseException:
            _drop_job(_job, doc)
            raise
        _sent = _sent_record(args, doc, _what, {
            "ok": True, "action": "undo" if undo_in is not None else "status", "id": eid,
            "doc": doc.name, "queued": _job.stem if _job is not None else None})
        # Verified in the lock the write was made in, and a refused change is taken back out.
        back = doc.read_text(encoding="utf-8")
        got = next((e for e in parse_entries(back.split("\n"))[0] if e["id"] == eid), None)
        want = (undo_in or {}).get("prev") if undo_in is not None else status
        if got is None or (status_of(got["body"]) or "").upper() != want:
            _drop_job(_job, doc)
            _sent_drop(_sent)
            _note_fail(args, ("%s: the change is NOT in %s after the write" % (eid, doc.name))
                       + _undo_failed_write(doc, _orig, _new))
            return 1
    landed, detail = False, "not attempted"
    if _job is not None:
        landed, detail = None, QUEUED_DETAIL % CANONICAL_REF
    elif not getattr(args, "no_land", False):
        _lop = {"op": "today", "nid": None, "today": op}
        try:
            landed, detail = land_note(doc, message, removed=_removed, op=_lop,
                                       chain=lambda: (_text_blob(_new), _text_blob(raw)))
        except Exception as _ex:
            landed, detail = False, "the lander raised %s" % type(_ex).__name__
    _reply = {"ok": True, "action": "undo" if undo_in is not None else "status", "id": eid,
              "doc": doc.name, "status": want, "landed": landed, "land_detail": detail,
              "queued": _job.stem if _job is not None else None, "undo": undo_out}
    _sent_update(_sent, _reply)
    print(json.dumps(_reply))
    return 0


def cmd_resolve(args):
    """
    Flip an entry's Status IN PLACE. Never append a second heading.
    """
    doc = resolve_doc_arg(args.doc)
    if not doc or not doc.exists():
        print("no such doc: %s" % doc, file=sys.stderr)
        return 2
    if refuse_if_not_mine(doc, "resolve", getattr(args, "not_mine", False)):
        return 1
    if getattr(args, "json", False):
        return _resolve_for_reader(args, doc)
        # `expect` is the RAW text, so a landing that arrives between this
    # read and the write below refuses instead of being erased.
    _before_txt = doc.read_text(encoding="utf-8")
    lines = _before_txt.splitlines()

    err = apply_resolve(lines, args.id, args.status, args.ruling, args.depends, args.adopt,
                        os.environ.get("CLAUDE_ORCH_ID", "orchestrator"), None, doc.name)
    if err:
        print(err, file=sys.stderr)
        return 1
    status = args.status.upper()

    if _write_guard_refuses(doc, _before_txt, "\n".join(lines) + "\n", "resolve",
                            force_open={args.id}):
        return 1
    write_doc(doc, "\n".join(lines) + "\n", expect=_before_txt)

    after, _ = parse_entries(doc.read_text(encoding="utf-8").splitlines())
    got = next((status_of(x["body"]) for x in after if x["id"] == args.id), None)
    if got != status:
        print("[FAILED] %s still reads %s in %s after the write - the edit did NOT take."
              % (args.id, got or "no status", doc.name), file=sys.stderr)
        print("         Nothing to trust here; inspect the entry by hand.",
              file=sys.stderr)
        return 1

    print("[RESOLVED] %s -> %s  in %s   (verified by re-reading the file)"
          % (args.id, status, doc.name))
    print("           edited in place; no second heading created")
    print()
    print("NEXT: run `orchdoc.py plate --doc %s` so every derived view updates." % args.doc)
    return 0


# The emergent status vocabulary, observed across live docs. Migration READS this
# and writes an explicit field - it never removes the marker. The emoji stays for humans;
# the field is what a machine can check.
EMOJI_STATUS = [
    ("✅", "RESOLVED"), ("⭐✅", "RESOLVED"), ("🔴", "OPEN"), ("⏸️", "PAUSED"),
    ("⏳", "DEFERRED"), ("🗄️", "ARCHIVED"), ("⛔", "OPEN"), ("🚨", "OPEN"),
]
WORD_STATUS = [
    (re.compile(r"\bRESOLVED\b|\bRULED\b|\bDONE\b|\bAPPLIED\b"), "RESOLVED"),
    (re.compile(r"\bRETRACTED\b"), "RETRACTED"),
    (re.compile(r"\bSUPERSEDED\b"), "SUPERSEDED"),
    (re.compile(r"\bPAUSED\b"), "PAUSED"),
    (re.compile(r"\bDEFERRED\b|\bPARKED\b"), "DEFERRED"),
    (re.compile(r"\bSTOPPED\b|\bBLOCKED\b"), "OPEN"),
]


def infer_status(title):
    """Best-guess status from an entry's existing heading. Never destructive."""
    for emo, st in EMOJI_STATUS:
        if emo in title:
            return st
    for pat, st in WORD_STATUS:
        if pat.search(title):
            return st
    return "OPEN"


def cmd_migrate(args):
    """
    Bring a LEGACY doc to the point where the other commands work.

    So this does the mechanical stage in one motion, and DRY-RUN IS THE DEFAULT (the
    shape release.mjs uses): it adds the missing Status field, inferring the value from
    the heading's existing emoji vocabulary, and never deletes anything.
    """
    doc = resolve_doc_arg(args.doc)
    if not doc or not doc.exists():
        print("no such doc: %s" % doc, file=sys.stderr)
        return 2

    with _lock(doc):
        lines = doc.read_text(encoding="utf-8").splitlines()
        entries, _ = parse_entries(lines)

        dup = defaultdict(list)
        for e in entries:
            dup[e["id"]].append(e)
        dups = {k: v for k, v in dup.items() if len(v) > 1}

        todo = [e for e in entries
                if status_of(e["body"]) is None
                and not e.get("archived")
                and (DECISION_SECTION_RE.search(e["section"])
                     or DECISION_SECTION_RE.search(e["title"]))]
        todo.sort(key=lambda e: e["line"], reverse=True)

        planned = []
        for e in todo:
            st = infer_status(e["title"])
            field = ("**Status:** %s - **Owner:** %s - **Opened:** %s - **Enriched:** NO"
                     % (st, args.owner, args.date or "unknown"))
            planned.append((e["line"], e["id"], st, field))
            if not args.dry_run:
                lines.insert(e["line"], "")
                lines.insert(e["line"] + 1, field)

        if not args.dry_run and planned:
            write_doc(doc, "\n".join(lines) + "\n")

    mode = "DRY RUN (nothing written)" if args.dry_run else "APPLIED"
    print("orchdoc migrate - %s - %s" % (doc.name, mode))
    print()
    if planned:
        print("  Status field added to %d entries (inferred from the existing markers):"
              % len(planned))
        for ln, eid, st, _f in sorted(planned):
            print("    %-10s -> %-10s (was marker-only, at line %d)" % (eid, st, ln))
    else:
        print("  No entries missing a Status field.")

    if dups:
        print()
        print("  ⛔ NOT touched - %d duplicate id(s). Which entry is live is a judgment"
              % len(dups))
        print("     about content, so this tool does not make it in another's doc:")
        for eid, group in sorted(dups.items()):
            print("       %-10s appears at lines %s"
                  % (eid, ", ".join(str(g["line"]) for g in group)))
        print("     Collapse the superseded copy under <details> with a summary saying")
        print("     WHY it is superseded and WHERE the live entry is, then strip its id.")

    if args.dry_run:
        print()
        print("  Re-run with --commit to write. Nothing has been changed.")
    return 0


def refresh_meta(doc, landing_now=False):
    """Re-render the meta block from the commit log. True if anything changed.

    ONE implementation, three callers: `scaffold` (creates it), `commit` (the write
    chokepoint, so it cannot drift) and `refresh-meta` (what an audit runs). Three
    hand-rolled refreshes would be three chances to disagree about what "current" means.
    """
    with _lock(doc):
        _before = doc.read_text(encoding="utf-8", errors="replace")
        lines = _before.split("\n")
        span, err = marker_span(lines, META_BEGIN_TOKEN, META_END_TOKEN, "meta")
        if err or not span:
            return False
        new = lines[:span[0]] + render_meta(doc, lines, landing_now) + lines[span[1] + 1:]
        if new == lines:
            return False
        write_doc(doc, "\n".join(new), expect=_before)
        return True


def cmd_refresh_meta(args):
    """
    The audit is a session CHOOSING to look; `commit` is the action that lands a change.
    Both refresh now, but only the second one holds for docs nobody audits - which is why
    the chokepoint is the primary and this is the convenience.
    """
    doc = resolve_doc_arg(args.doc)
    if doc is None:
        return 2
    changed = refresh_meta(doc)
    print("orchdoc refresh-meta - %s" % doc.name)
    print("  %s" % ("meta block refreshed from the commit log."
                    if changed else "already current - nothing written."))
    return 0


def _republish_mirrors(landed):
    """After a landing that touched a mirrored tool or skill, write the copies from the ref.
    """
    try:
        import tool_drift
        watched = set(tool_drift.TOOLS)
    except Exception:
        return
    touched = [p for p in (landed or [])
               if Path(p).name in watched or Path(p).name == "SKILL.md"]
    if not touched:
        return
    print()
    print("  republishing %d mirrored file(s) from %s - the copy a session LOADS"
          % (len(touched), CANONICAL_REF))
    try:
        r = subprocess.run([sys.executable,
                            str(Path(__file__).resolve().parent / "tool_drift.py"),
                            "--sync", "--cache-only"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180, creationflags=flags())
        tail = [l for l in (r.stdout or "").splitlines() if "synced" in l or "still" in l]
        for l in tail[-3:]:
            print("   %s" % l.strip())
        if not tail:
            print("    (tool_drift reported no sync - the copies may already agree)")
    except Exception as e:
        print("    [warn] republish did not run (%s) - the landing STANDS; the mirrors are"
              % type(e).__name__)
        print("           where they were. Run `tool_drift.py --sync` when convenient.")


def non_markdown_doc_refusal(doc):
    """Why `commit --doc` must refuse `doc`, or None.
"""
    if Path(doc).suffix.lower() == ".md":
        return None
    return ("--doc must be a markdown OrchDoc (.md); %s is not. Override stamps are HTML "
            "comments and would break a code or data file. Land it with `--also %s` next to "
            "its OrchDoc, and put the attestation in the commit message."
            % (Path(doc).name, Path(doc).name))


def cmd_commit(args):
    """
    Land ONE OrchDoc on the canonical ref, safely, from a dirty shared working tree.
    """
    doc = resolve_doc_arg(args.doc)
    if not doc or not doc.exists():
        print("no such doc: %s" % doc, file=sys.stderr)
        return 2
    _nonmd = non_markdown_doc_refusal(doc)
    if _nonmd:
        print("  [REFUSE] %s Nothing was written." % _nonmd, file=sys.stderr)
        return 2
    rel = os.path.relpath(str(doc), str(PROJECTS)).replace("\\", "/")
    ref = CANONICAL_REF

    print("orchdoc commit - %s -> %s" % (doc.name, ref))

    # The fetch and the pin come BEFORE the meta refresh and the auto-archive below, which
    # both write the doc, so an out-of-date copy of this tool is refused before it writes
    # anything - with the one fetch this command already made, and no second one.
    git(["fetch", "--quiet", "origin"])

    _rc_pin, pinned, _e_pin = git(["rev-parse", "--verify", "%s^{commit}" % ref])
    pinned = (pinned or "").strip()
    if _rc_pin != 0 or not re.fullmatch(r"[0-9a-f]{40}", pinned):
        print("  [REFUSE] could not resolve %s to a commit (%s). Nothing was pushed."
              % (ref, _e_pin or "git gave no reason"), file=sys.stderr)
        return 1
    print("           %s is pinned at %s for every check below." % (ref, pinned[:10]))
    _stale = None if args.dry_run else _refuse_if_tool_is_stale(pinned)
    if _stale:
        print("  [REFUSE] %s\n           It declines to land. Nothing was written or pushed."
              % _stale.replace("\n", "\n           "), file=sys.stderr)
        return 1

    if refresh_meta(doc, landing_now=True):
        print("           meta block refreshed from the commit log before landing.")

    try:
        _ns = argparse.Namespace(doc=args.doc, dry_run=False,
                                 into="RESOLVED - kept for the record",
                                 not_mine=getattr(args, "not_mine", False))
        cmd_archive(_ns)
    except Exception as _e:                    # never let tidy-up block a landing
        print("           [note] auto-archive skipped: %s" % _e)

    print("           landing your WORKING-TREE copy (%d lines), not HEAD - make sure it"
          % len(doc.read_text(encoding="utf-8", errors="replace").splitlines()))
    print("           is coherent; half-finished edits in the file WILL be landed.")
    print()

    findings = [f for f in check_doc(doc) if f.code in BLOCKING]

    override_codes = [c.strip().upper() for c in (args.override or "").split(",") if c.strip()]

    if args.override:
        if not args.because or len(args.because) < MIN_ATTESTATION_CHARS \
                or RUBBER_STAMP_RE.match(args.because):
            print("  [REFUSE] --override needs --because with a real reason (%d+ chars)."
                  % MIN_ATTESTATION_CHARS, file=sys.stderr)
            print("           An override held to a lower bar than an attestation "
                  "becomes 'needed to ship'.", file=sys.stderr)
            return 1
        _asked = override_codes
        _present = sorted(set(f.code for f in findings) | GATE_OVERRIDE_CODES)
        _unknown = [c for c in _asked if c not in _present]
        if _unknown:
            print("  REFUSED - `--override %s` matches no finding on this doc, so it would "
                  "record an attestation that overrides nothing." % ",".join(_unknown),
                  file=sys.stderr)
            print("  Codes actually present: %s"
                  % (", ".join(_present) if _present else "(none - the doc is clean)"),
                  file=sys.stderr)
            print("  Use the CODE, not the label a message printed. Nothing was written.",
                  file=sys.stderr)
            return 1
        from orchdoc_ids import missing_ids as _missing_ids, parse_report as _parse_report
        _gap = _missing_ids(_parse_report("\n".join(
            "%s  line %s %s" % (f.code, f.line, f.msg) for f in findings
            if f.code in _asked), _asked), args.because)
        if _gap and not getattr(args, "blanket_ok", False):
            for _c, _g in _gap.items():
                print("  REFUSED - %s fired on %s; --because does not name them."
                      % (_c, ", ".join(_g)), file=sys.stderr)
            print("  Open each entry and write its id in --because, as in:  --because \"%s: "
                  "opened each; <what you found>\"" % ", ".join(sorted(
                      set(i for _g in _gap.values() for i in _g))), file=sys.stderr)
            print("  Or add --blanket-ok to land without naming them; the stamp then records "
                  "which entries went unnamed. Nothing was written.", file=sys.stderr)
            return 1
        who = actor_for(doc)
        for _code in _asked:
            _unnamed = ("[blanket-ok, not named: %s] " % ", ".join(_gap[_code])
                        if _code in _gap else "")
            stamp = "<!-- ORCHDOC:OVERRIDE %s by=%s at=%s --> %s%s" % (
                _code, who, _now_iso(), _unnamed, sanitize_body(args.because))
            if args.dry_run:
                print("  [override] %s would be recorded by %s (DRY RUN - not written)."
                      % (_code, who))
                continue
            txt = doc.read_text(encoding="utf-8")
            if stamp.split("-->")[0] not in txt:
                # `expect=txt` because this is a read-modify-write like any other: the window
                # is small, but a landing inside it would be ERASED rather than merged over,
                # and an override stamp is the one line that records why a gate was bypassed.
                write_doc(doc, txt.rstrip("\n") + "\n\n" + stamp + "\n", expect=txt)
            print("  [override] %s recorded by %s - it will surface in every later check."
                  % (_code, who))
        findings = [f for f in findings if f.code not in _asked]

    if findings:
        try:
            _refused_lines = doc.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            _refused_lines = None
        log_refusal(doc, findings, _refused_lines)
        print("  [REFUSE] gate 0 - %d blocking finding(s). Nothing was pushed."
              % len(findings))
        for f in findings[:8]:
            print("           %-18s %s %s"
                  % (f.code, ("L%d" % f.line) if f.line else "doc", f.msg[:78]))
        if len(findings) > 8:
            print("           ... and %d more - orchdoc.py check --doc %s"
                  % (len(findings) - 8, args.doc))
        print_wakes_refusal([f for f in findings if f.code == "E-WAKESOTHERS"])
        print_prelabel_refusal([f for f in findings if f.code == "E-PRELABEL"])
        print_buried_ask_refusal([f for f in findings if f.code == "E-BURIEDASK"])
        print_copy_guard_remedies(findings)
        print()
        print("  Fix them, or land anyway WITH A WRITTEN REASON:")
        print("    orchdoc.py commit --doc %s --commit \\" % args.doc)
        print("      --override %s \\"
              % ",".join(sorted(set(f.code for f in findings))))
        print("      --because \"<each entry id it fired on, and why this must land before it "
              "is clean, %d+ chars>\"" % MIN_ATTESTATION_CHARS)
        return 1
    print("  [ok] gate 0 - no blocking findings")
    print_needle_hints(doc, entries_changed_since(doc, rel, ref))

    _, base, _ = git(["merge-base", pinned, "HEAD"])
    def _authored(text, label):
        """
        Non-empty lines EXCLUDING the derived region, which changes by construction and
        would otherwise report as content loss. Returns None if the boundary cannot be
        located - an oracle that cannot find its own boundary must refuse, not guess.
        """
        lines_ = text.splitlines()
        spans, why = derived_spans(lines_)
        if why:
            print("  [REFUSE] gate 1 - %s (%s)." % (why, label), file=sys.stderr)
            print("           Cannot tell derived content from hand-authored, so this",
                  file=sys.stderr)
            print("           gate cannot prove anything. Fix the markers first.",
                  file=sys.stderr)
            return None
        out = []
        for i, l in enumerate(lines_):
            if not l.strip():
                continue
            if any(lo <= i <= hi for lo, hi in spans):
                continue          # ANY generated region - see DERIVED_REGIONS
            f = _authored_form(l)
            if f:
                out.append(f)
        return out

    _b1_ok, _b1_dry_text = _merge_notes_before_landing(doc, rel, pinned,
                                                       dry_run=bool(args.dry_run))
    if not _b1_ok:
        return 1
    if _b1_dry_text is not None:
        # A dry run writes nothing, so the merged text is hashed from memory instead.
        _rc_h, _doc_blob, _e_h = _hash_text(_b1_dry_text, rel)
    else:
        # `--path rel`, as `_hash_text` passes on the dry run: attributes are looked up by the
        # path the blob lands at, so a dry run and a real run hash the same file the same way.
        _rc_h, _doc_blob, _e_h = git(["hash-object", "-w", "--path", rel, str(doc)])
    _doc_blob = _doc_blob.strip()
    if _rc_h != 0 or not _doc_blob:
        print("  [REFUSE] could not hash %s, so no gate below could be about the bytes that"
              % doc.name, file=sys.stderr)
        print("           would land: %s" % (_e_h or "git gave no reason"), file=sys.stderr)
        return 1

    _rc_c, canon_txt, _e_c = git(["show", "%s:%s" % (pinned, rel)])
    if _rc_c != 0:
        _rc_e, _o_e, _ = git(["cat-file", "-e", "%s:%s" % (pinned, rel)])
        if _rc_e == 0:
            print("  [REFUSE] gate 1 - %s IS on %s but could not be read back:"
                  % (rel, ref), file=sys.stderr)
            print("           %s" % (_e_c or "git gave no reason"), file=sys.stderr)
            print("           Refusing rather than treating an unreadable canonical copy as",
                  file=sys.stderr)
            print("           an empty one, which would pass this gate on every landing.",
                  file=sys.stderr)
            return 1
        canon_txt = ""      # genuinely not on the ref yet - a first landing preserves nothing
    canon_lines = _authored(canon_txt, "on %s" % ref)
    if canon_lines is None:
        return 1

    # The copy about to be landed is the BLOB, not the file. If the file changes between now
    # and the push, the blob does not, and the blob is what `build_on` commits.
    _rc_m, _mine_txt, _e_m = git(["cat-file", "-p", _doc_blob])
    if _rc_m != 0:
        print("  [REFUSE] could not read back the object just written for %s: %s"
              % (doc.name, _e_m or "git gave no reason"), file=sys.stderr)
        return 1
    mine_l = _authored(_mine_txt, "in the commit about to be built")
    if mine_l is None:
        return 1
    _b1_new, _b1_changes, _b1_refusals = merge_canonical_notes(canon_txt, _mine_txt)
    if _b1_refusals or _b1_changes:
        print("  [REFUSE] B1 - the copy changed while landing, and the commit about to be built")
        print("           no longer holds every note on %s. Nothing was pushed; re-run."
              % ref)
        for nid in ([r["nid"] for r in _b1_refusals] or [c[0] for c in _b1_changes])[:5]:
            print("             - note %s" % nid)
        return 1
    mine = set(mine_l)
    _joined = "\n".join(mine_l)
    lost = [l for l in canon_lines if l not in mine and l.strip() not in _joined]
    # What this gate lets the landing remove, if it passes: checked again, against the built
    # commit's ACTUAL parent, right before every push.
    _approved_removals = set(lost)
    if lost and (set(override_codes) & GATE_OVERRIDE_CODES):
        print("  [override] gate 1 reconciliation recorded by %s - %d reworded line(s)"
              % (actor_for(doc), len(lost)))
        for l in lost[:6]:
            print("             was: %s" % l.strip()[:88])
        lost = []
    seen, unseen = lost, []
    if lost:
        seen, unseen = _split_by_seen(doc, rel, pinned, lost, base=base)
    if unseen:
        print("  [REFUSE] gate 1 - landing this would remove %d line(s) that reached %s after"
              % (len(unseen), ref))
        print("           your copy was taken: added since your checkout's merge-base by a")
        print("           commit known to be someone else's, or after the file was last")
        print("           written. You never had them, so this is a stale copy or someone")
        print("           else's work - not a rewrite. First:")
        for l in unseen[:5]:
            print("             - %s" % l.strip()[:88])
        print("           Reconcile:  git diff %s -- %s" % (ref, rel))
        print("           Then re-read the file and redo the edit on top of it.")
        return 1
    if seen:
        print("  [ok] gate 1 - %d line(s) removed, all in your copy when you took it (on your"
              % len(seen))
        print("       checkout's base, or added by your own landings).")
        print("       You had them and chose; that is editing, not loss. First:")
        for l in seen[:3]:
            print("             - %s" % l.strip()[:88])
    lost = unseen
    if lost:
        print("  [REFUSE] gate 1 - landing this would REMOVE %d line(s) that are on %s."
              % (len(lost), ref))
        print("           Someone else's content, or a stale copy of yours. First:")
        for l in lost[:5]:
            print("             - %s" % l.strip()[:88])
        print("           Reconcile:  git diff %s -- %s" % (ref, rel))
        print()
        print("           ⭐ FIRST TRY RELOCATING, not overriding. This gate objects to lines")
        print("           leaving the DOCUMENT, not the section - so moving text VERBATIM into")
        print("           a finding passes with no override. A reworded copy does not: that is")
        print("           the gate holding you to the move you said you made.")
        print()
        print("           Genuinely a rewrite? --override %s --because \"<why>\""
              % sorted(GATE_OVERRIDE_CODES)[0])
        return 1
    print("  [ok] gate 1 - every line on %s survives; landing is additive and can"
          % ref)
    print("       revert nobody%s"
          % ("" if not base else " (merge-base %s)" % base[:8]))

    try:
        _canon_ids = {e["id"] for e in parse_entries(canon_txt.split("\n"))[0] if e.get("id")}
        _mine_entries = parse_entries(_mine_txt.replace("\r\n", "\n").split("\n"))[0]
    except Exception:
        _canon_ids, _mine_entries = None, []
    _opened_new = []
    if _canon_ids is not None:
        for _e in _mine_entries:
            if not _e.get("id") or _e["id"] in _canon_ids:
                continue
            for _l in (_e.get("body") or "").split("\n"):
                if OPENED_DAY_ONLY_RE.search(_l):
                    _opened_new.append("%s - %s" % (_e["id"], _l.strip()))
                    break
    if _opened_new:
        print(file=sys.stderr)
        print("  [REFUSE] gate 1c - %d entr(ies) this landing ADDS carry a date-only **Opened:**."
              % len(_opened_new), file=sys.stderr)
        for l in _opened_new[:5]:
            print("             %s" % l.strip()[:92], file=sys.stderr)
        print(file=sys.stderr)
        print("           An item has an AGE, and a bare date cannot express one.", file=sys.stderr)
        print(file=sys.stderr)
        print("           `orchdoc.py add` stamps the full time for you. Written by hand?",
              file=sys.stderr)
        print("           **Opened:** %s" % _now_iso(), file=sys.stderr)
        print(file=sys.stderr)
        print("           Capturing an item created EARLIER, whose time is genuinely unknown?",
              file=sys.stderr)
        print("           --override E-DAYONLYOPENED --because \"<whose item, and why the time",
              file=sys.stderr)
        print("           cannot be recovered>\"", file=sys.stderr)
        if "E-DAYONLYOPENED" not in (getattr(args, "override", None) or []):
            return 1
        print("  [override] E-DAYONLYOPENED recorded - the entr(ies) stay date-only.")

    try:
        _canon_by_id = {e["id"]: (e.get("body") or "")
                        for e in parse_entries(canon_txt.split("\n")) [0] if e.get("id")}
        _mine_by_id = parse_entries(_mine_txt.replace("\r\n", "\n").split("\n"))[0]
    except Exception:
        _canon_by_id, _mine_by_id = None, []
    _day_new = []
    if _canon_by_id is not None:
        for _e in _mine_by_id:
            if not _e.get("id"):
                continue
            _was = set(l.strip() for l in _canon_by_id.get(_e["id"], "").split("\n"))
            for _l in (_e.get("body") or "").split("\n"):
                if RESOLVED_DAY_ONLY_RE.search(_l) and _l.strip() not in _was:
                    _day_new.append("%s - %s" % (_e["id"], _l.strip()))
                    break
    if _day_new:
        print(file=sys.stderr)
        print("  [REFUSE] gate 1b - %d ruling(s) this landing ADDS are stamped to the day only."
              % len(_day_new), file=sys.stderr)
        for l in _day_new[:5]:
            print("             %s" % l.strip()[:92], file=sys.stderr)
        print(file=sys.stderr)
        print("           A ruling is the field every 'which one is current?' question turns",
              file=sys.stderr)
        print("           on. Two rulings on one day cannot be ordered by a date, and the",
              file=sys.stderr)
        print("           answer that comes back is whichever the parser happened to reach.",
              file=sys.stderr)
        print(file=sys.stderr)
        print("           `orchdoc.py resolve` stamps the full time for you. If you wrote the",
              file=sys.stderr)
        print("           line by hand, give it one:  **Resolved %s:**" % _now_iso(),
              file=sys.stderr)
        print(file=sys.stderr)
        print("           Recording a ruling made EARLIER, whose time is genuinely unknown?",
              file=sys.stderr)
        print("           --override E-DAYONLYRULING --because \"<whose ruling, and why the",
              file=sys.stderr)
        print("           time cannot be recovered>\"", file=sys.stderr)
        if "E-DAYONLYRULING" not in (getattr(args, "override", None) or []):
            return 1
        print("  [override] E-DAYONLYRULING recorded - the ruling(s) stay date-only.")

    wanted = [rel]
    for extra in (args.also or []):
        ep = Path(extra) if Path(extra).exists() else (PROJECTS / extra)
        if not ep.exists():
            print("  [REFUSE] --also path does not exist: %s" % extra, file=sys.stderr)
            return 1
        wanted.append(os.path.relpath(str(ep), str(PROJECTS)).replace("\\", "/"))
    _others = [w for w in wanted if w != rel
               and re.search(r"(^|/)ORCHESTRATOR-DECISIONS-o\d+\.md$", w, re.I)]
    if _others:
        print("  [REFUSE] --also names another orchestrator's OrchDoc: %s" % ", ".join(_others),
              file=sys.stderr)
        print("           Its owner lands it with `orchdoc.py commit --doc <theirs>`; a copy "
              "from this", file=sys.stderr)
        print("           disk can be older than what they landed and would undo it.",
              file=sys.stderr)
        return 1
    wanted = sorted(set(wanted))
    _imgs = _images_to_land(doc, pinned, _blob_text(_doc_blob))
    if _imgs:
        wanted = sorted(set(wanted) | set(_imgs))
        print("  [ok] %d picture(s) the doc embeds land with it: %s"
              % (len(_imgs), ", ".join(_imgs[:4]) + (" ..." if len(_imgs) > 4 else "")))

    _lander = running_orchestrator() or doc_owner_id(doc)

    def build_on(parent):
        """Build a commit = parent + exactly `wanted`, without touching the tree."""
        # tempfile.gettempdir(), not $TEMP. $TEMP is Windows-only; on macOS/Linux it is
        # usually unset, so the "." fallback silently wrote a scratch git index into the
        # CURRENT DIRECTORY - no crash, just litter dropped in the user's own repo.
        idx = Path(tempfile.gettempdir()) / ("orchdoc-%s.index" % os.getpid())
        env = dict(os.environ, GIT_INDEX_FILE=str(idx))

        def g(a):
            p = subprocess.run(["git", "-C", str(PROJECTS)] + a, capture_output=True,
                               text=True, env=env, timeout=60, creationflags=flags())
            return p.returncode, p.stdout.strip(), p.stderr.strip()
        try:
            if idx.exists():
                idx.unlink()
            g(["read-tree", parent])
            for w in wanted:
                if w == rel:
                    blob = _doc_blob
                else:
                    _, blob, _ = g(["hash-object", "-w", "--path", w, str(PROJECTS / w)])
                g(["update-index", "--add", "--cacheinfo", "100644,%s,%s" % (blob, w)])
            _, tree, _ = g(["write-tree"])
            msg = args.message or ("%s: update" % doc.name)
            p = subprocess.run(
                ["git", "-C", str(PROJECTS), "commit-tree", tree, "-p", parent],
                input=msg + ("\n\n%sCo-Authored-By: %s <noreply@anthropic.com>\n"
                             % (("%s: %s\n" % (LANDER_TRAILER, _lander)) if _lander else "",
                                sanitize_field(os.environ.get("CLAUDE_MODEL_NAME", "Claude"),
                                               40))),
                capture_output=True, text=True, timeout=60, creationflags=flags())
            return p.stdout.strip()
        finally:
            try:
                idx.unlink()
            except (OSError, UnicodeDecodeError):
                pass

    commit = build_on(pinned)
    if not commit:
        print("  [REFUSE] could not build the commit", file=sys.stderr)
        return 1

    _, names, _ = git(["diff", "--name-only", pinned, commit])
    touched = sorted(n for n in names.splitlines() if n.strip())
    extra = [n for n in touched if n not in wanted]
    if extra:
        print("  [REFUSE] gate 2 - the commit would touch %d file(s) you did not name:"
              % len(extra))
        for n in extra[:10]:
            print("           %s   <- NOT YOURS" % n)
        return 1
    if not touched:
        print("  [REFUSE] gate 2 - nothing to land: every named file already matches %s."
              % ref)
        return 1
    unchanged = [n for n in wanted if n not in touched]
    if "BOLDWALL" not in override_codes:
        try:
            import bold_wall
            # `histogram`, so a landing that moves entries is not read as ADDING them all.
            _, _bd, _ = git(["diff", "--unified=0", "--diff-algorithm=histogram",
                             pinned, commit, "--", str(doc.name)])
            _bd = _drop_fenced_adds(_bd, _blob_text(_doc_blob))
            _block, _advise = bold_wall.findings_in_diff(_bd)
        except Exception:
            _block, _advise = [], []
        if _block:
            _nwall = sum(1 for f in _block if f["kind"] == "WALL")
            _nscat = len(_block) - _nwall
            print("  [REFUSE] gate 5 - %d passage(s) this landing ADDS do not use bold the"
                  % len(_block))
            print("           way the human reads it: %d almost entirely bold, %d with bold"
                  % (_nwall, _nscat))
            print("           scattered %d+ times and opening nothing."
                  % bold_wall.SCATTER_RUNS)
            for f in _block[:3]:
                print("             %-7s %.0f%% bold, %d runs, %d chars: %s"
                      % (f["kind"], f["share"] * 100, f["runs"], f["chars"],
                         f["text"].replace("\n", " ")[:76]))
            print()
            print("           Bold the OPENING clause so it states the premise, the way a")
            print("           bullet summarises, and leave the rest plain - the human decides")
            print("           from that clause whether to read on. The backlog is")
            print("           untouched; this reads only what you are adding.")
            print("           If a passage genuinely needs it:")
            print("             --override BOLDWALL --because \"<why, %d+ chars>\""
                  % MIN_ATTESTATION_CHARS)
            log_refusal(doc, [Finding("E-BOLDWALL" if f["kind"] == "WALL"
                                      else "E-BOLDSCATTER", 0,
                                      "%.0f%% bold, %d runs: %s"
                                      % (f["share"] * 100, f["runs"],
                                         f["text"].replace("\n", " ")[:120]))
                              for f in _block])
            return 1
        if _advise:
            _nlead = sum(1 for f in _advise if f["kind"] == "NOLEAD")
            _nmulti = len(_advise) - _nlead
            print("  [ok] gate 5 - no wall, no scattered bold")
            print("       note: %d passage(s) where bold does not open the passage, %d"
                  % (_nlead, _nmulti))
            print("             where it opens it but more bold follows. The human's rule is one")
            print("             bold opening that states the premise. Not a refusal.")
            worst = _advise[0]
            print("             worst: %d runs, %.0f%% bold: %s"
                  % (worst["runs"], worst["share"] * 100,
                     worst["text"].replace("\n", " ")[:70]))
            log_refusal(doc, [Finding("A-BOLDLEAD", 0,
                                      "%d nolead, %d multi; worst %d runs %.0f%%: %s"
                                      % (_nlead, _nmulti, worst["runs"],
                                         worst["share"] * 100,
                                         worst["text"].replace("\n", " ")[:90]))])
        else:
            print("  [ok] gate 5 - bold opens each passage and nothing else is bold")

    print("  [ok] gate 2 - commit touches %d file(s), all named%s"
          % (len(touched),
             "; %d named file(s) unchanged" % len(unchanged) if unchanged else ""))

    _rc_g4, _committed, _ = git(["show", "%s:%s" % (commit, rel)])
    if _rc_g4 != 0 or not _committed.strip():
        print("  [REFUSE] gate 4 - could not read %s back out of the commit just built."
              % rel, file=sys.stderr)
        print("           Refusing rather than verifying against the file on disk, which is",
              file=sys.stderr)
        print("           what let an entry go missing while the landing reported success.",
              file=sys.stderr)
        return 1
    doc_text = _committed
    subject = (args.message or "").splitlines()[0] if args.message else ""
    intents = []

    _pfx = "|".join(sorted(KIND_SECTION_NUM, key=len, reverse=True))
    _subject = re.sub(r"\bo\d+:[A-Za-z-]*\d+", " ", subject)
    for m in re.finditer(r"(?<![A-Z][A-Z]-)\b((?:%s)\d{1,3})\b" % _pfx, _subject):
        intents.append(("entry " + m.group(1),
                        _heading_names_id(doc_text, m.group(1))))
    for exp in (args.expect or []):
        intents.append(("%r" % exp[:48], exp in doc_text))

    unmet = [name for name, ok in intents if not ok]
    if unmet:
        print("  [REFUSE] gate 4 - the commit says it does something THE COMMIT does not"
              " contain:")
        print("           \u26d4 CHECKED INSIDE THE BUILT COMMIT, NOT ON DISK. If the text is")
        print("           in your file but not here, another session wrote between the build")
        print("           and now - re-run, and the rebuild will carry it.")
        for name in unmet:
            print("           %s is named in the message but is NOT in %s"
                  % (name, doc.name))
        if any(n.startswith("entry ") for n in unmet):
            print()
            print("           If you meant ANOTHER orchestrator's entry, QUALIFY it -"
                  " `o99:D9905`, not `D9905`.")
           # The gate is right to refuse an unresolvable reference and was useless for
           # describing the problem without the remedy: the only obvious escape was to
           # DELETE the reference, which loses the traceability the qualifier exists for.
            print("           A qualified id is skipped here by construction, and it is"
                  " what keeps the")
            print("           cross-doc trail readable. Deleting the reference is the one"
                  " fix that costs something.")
        print()
        print("           Gates 1-3 are SAFETY and all pass on a no-op. This one asks")
        print("           whether the write actually happened. It did not.")
        return 1
    _rc_cb, _cblob, _ = git(["rev-parse", "%s:%s" % (commit, rel)])
    if _rc_cb != 0 or (_cblob or "").strip() != _doc_blob:
        print("  [REFUSE] gate 4 - the %s inside the built commit (%s) is not the object gate 1"
              " approved (%s)." % (doc.name, (_cblob or "unknown").strip()[:10], _doc_blob[:10]),
              file=sys.stderr)
        print("           Nothing was pushed. Re-run, and the rebuild will carry what you wrote.",
              file=sys.stderr)
        return 1
    if intents:
        print("  [ok] gate 4 - all %d intent(s) named in the message are present"
              % len(intents))
    else:
        print("  [note] gate 4 - no entry id in the commit subject and no --expect, so no entry"
              " was checked; only that the commit holds the exact bytes gate 1 approved.")

    for _od in [n for n in touched
                if re.match(r"ORCHESTRATOR-DECISIONS-[^/\\]*\.md$", pathlib.Path(n).name)]:
        try:
            _t0 = time.time()
            print_landing_wakes(landing_wakes(_od, pinned, commit), time.time() - _t0)
        except Exception as _e:        # a notice that fails must not stop a landing
            print("  [note] could not work out which other entries this landing flags: %s"
                  % str(_e)[:120])

    def _verify_parent(c):
        """None, or why commit `c` must not be pushed - checked against its ACTUAL parent.
        """
        _r1, parent, _ = git(["rev-parse", "%s^" % c])
        parent = (parent or "").strip()
        if _r1 != 0 or parent != pinned:
            return ("its parent is %s, not the pinned %s" % (parent[:10] or "unknown",
                                                              pinned[:10]))
        _r2, ptxt, _ = git(["show", "%s:%s" % (parent, rel)])
        if _r2 != 0:
            if git(["cat-file", "-e", "%s:%s" % (parent, rel)])[0] == 0:
                return "could not read %s on its parent" % rel
            return None                    # a first landing: nothing on the parent to keep
        _r3, btxt, _ = git(["show", "%s:%s" % (c, rel)])
        if _r3 != 0:
            return "could not read %s back out of the built commit" % rel
        _n, _ch, _rf = merge_canonical_notes(ptxt, btxt)
        if _ch or _rf:
            return ("note(s) on its parent are missing or changed in it: %s"
                    % ", ".join(sorted(set([x[0] for x in _ch] + [r["nid"] for r in _rf]))))
        p_auth = _authored(ptxt, "on the parent")
        b_auth = _authored(btxt, "in the built commit")
        if p_auth is None or b_auth is None:
            return "could not compare it with its parent"
        _bset, _bj = set(b_auth), "\n".join(b_auth)
        extra = [l for l in p_auth if l not in _bset and l.strip() not in _bj
                 and l not in _approved_removals]
        if extra:
            return ("it would remove %d line(s) from its parent that gate 1 did not approve - "
                    "first: %s" % (len(extra), extra[0].strip()[:80]))
        return None

    _why_parent = _verify_parent(commit)
    if _why_parent:
        print("  [REFUSE] before push - the built commit fails its own check: %s. Nothing"
              % _why_parent)
        print("           was pushed.")
        return 1
    print("  [ok] verified against its actual parent %s: every note kept, and only the"
          % pinned[:10])
    print("       removals gate 1 approved.")

    if args.dry_run:
        print()
        print("  DRY RUN. Built %s on top of %s (%s), pushed nothing."
              % (commit[:10], ref, pinned[:10]))
        print("  Re-run with --commit to fast-forward push it.")
        return 0

    branch = ref.split("/")[-1]

    if not re.fullmatch(r"[0-9a-f]{7,40}", (commit or "").strip()):
        print("  [REFUSE] refusing to push: the source ref is not a commit sha (%r)."
              % commit, file=sys.stderr)
        print("           An empty source would make this `push origin :refs/heads/%s`,"
              % branch, file=sys.stderr)
        print("           which DELETES the branch. Nothing was pushed.", file=sys.stderr)
        return 1
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", branch or ""):
        print("  [REFUSE] refusing to push: implausible branch name %r" % branch,
              file=sys.stderr)
        return 1

    for attempt in range(1, 6):
        rc, out, err = git(["-c", "credential.helper=!gh auth git-credential",
                            "push", "origin", "%s:refs/heads/%s" % (commit, branch)])
        if rc == 0:
            break
        blob = (err or out).strip()
        if "could not read Username" in blob or "/dev/tty" in blob:
            print("  [REJECTED] auth, not a race: %s" % blob[:150])
            print("             `gh auth login` in an interactive shell, then re-run.")
            print("             Nothing was clobbered.")
            return 1
        if attempt == 5:
            print("  [REJECTED] main is moving faster than this can rebuild. Re-run.")
            print("             Nothing was clobbered.")
            return 1
        print("  [race] main moved; re-parenting onto the fresh tip (attempt %d)" % attempt)
        git(["fetch", "--quiet", "origin"])
        _rc_new, _new, _ = git(["rev-parse", "--verify", "%s^{commit}" % ref])
        _new = (_new or "").strip()
        if _rc_new != 0 or not re.fullmatch(r"[0-9a-f]{40}", _new):
            print("  [REFUSE] could not resolve %s after it moved. Nothing was pushed." % ref)
            return 1
        _rc_chg, _chg, _e_chg = git(["diff", "--name-only", pinned, _new, "--"] + wanted)
        if _rc_chg != 0:
            print("  [REFUSE] while retrying, could not compare your file(s) between %s and %s:"
                  % (pinned[:10], _new[:10]))
            print("           %s" % (_e_chg or "git diff failed")[:150])
            print("           Nothing was pushed. Re-run.")
            return 1
        others = ""
        if _chg.strip():
            _, others, _ = git(["log", "--oneline", "%s..%s" % (pinned, _new), "--"] + wanted)
            others = others.strip() or ("(changed between %s and %s)"
                                        % (pinned[:10], _new[:10]))
        if others.strip():
            print("  [REFUSE] while retrying, another session landed changes to your")
            print("           file(s). Reconcile rather than overwrite:")
            for ln in others.splitlines()[:5]:
                print("             %s" % ln[:92])
            return 1
        pinned = _new
        commit = build_on(pinned)
        _, names, _ = git(["diff", "--name-only", pinned, commit])
        if sorted(n for n in names.splitlines() if n.strip()) != wanted:
            print("  [REFUSE] rebuilt commit no longer matches the named file set.")
            return 1
        _why_parent = _verify_parent(commit)
        if _why_parent:
            print("  [REFUSE] the rebuilt commit fails its own check: %s. Nothing was pushed."
                  % _why_parent)
            return 1

    git(["fetch", "--quiet", "origin"])
    rc2, _, _ = git(["merge-base", "--is-ancestor", commit, ref])
    if rc2 != 0:
        print("  [FAILED] %s is NOT an ancestor of %s after the push. Do not trust the"
              " push output; inspect by hand." % (commit[:10], ref), file=sys.stderr)
        return 1
    print("  [ok] gate 3 - verified: %s is an ancestor of %s" % (commit[:10], ref))
    print()
    print("  LANDED. %s is now current on %s." % (doc.name, ref))
    _report_delivery(doc)
    # Every OTHER landed file too - another group's landing from ten minutes ago included.
    # Only reached after gate 3 verified this landing; it cannot turn it into a failure.
    _deliver_landed_files()
    _republish_mirrors(touched)
    return 0



# Fields the TOOL writes, and therefore derived. Stripped before any content comparison
# so a field rewrite is never mistaken for lost prose. Attested-by / Resolved / Depends
# are NOT here: they carry authored reasoning, and losing one would be real damage.
MACHINE_FIELD_RE = re.compile(
    r"\*\*(?:Status|Owner|Opened|Enriched):\*\*\s*[^*\n]*", re.IGNORECASE)


def strip_machine_fields(line):
    """Remove tool-written field tokens, keep any free text the author added."""
    t = MACHINE_FIELD_RE.sub("", line)
    t = re.sub(r"^[\s\-\u00b7|]+", "", t)
    return re.sub(r"\s{2,}", " ", t).strip()


def _authored_form(l):
    """Gate 1's unit for ONE line: what the author wrote, or None if it carries nothing authored.

    Used by gate 1's `_authored` (which also drops the derived regions, a whole-document
    question) and by `_split_by_seen`, which has to reduce an ADDED line the same way before it
    can recognise it among the lost ones.
    """
    if NOTE_LINE_RE.match(l):
        return note_without_ack(l)
    if MACHINE_FIELD_RE.search(l):
        # A field line: keep only what the author wrote around the fields, so a status
        # rewrite is invisible but an attestation is still protected.
        return strip_machine_fields(l) or None
    m = re.match(r"^(#{1,6})\s+(.*)$", l)
    if m:
        bare = _strip_markers(m.group(2))
        idm = ID_RE.match(bare)
        if not idm:
            # An ARCHIVED heading carries its id mid-line, not at the start.
            idm = ARCHIVED_ID_RE.search(bare)
        return ("ENTRY:%s" % idm.group(1)) if idm else "%s %s" % (m.group(1), bare)
    return l


def _strip_markers(title):
    """
    Remove everything `normalize` GENERATES: the status glyph and, for terminal items,
    the status WORD it inserts after the id.

    Both are derived from the Status field, so both must come out before any content
    comparison - otherwise gate 1 reports normalize's own output as lost content, which
    is exactly what it did across 28 headings.
    """
    t = title
    for mk in set(STATUS_MARKER.values()):
        t = t.replace(mk, "")
    t = re.sub(r"\s{2,}", " ", t).strip()
    t = re.sub(r"^([A-Z]{1,3}(?:\d+[a-z]?|-[A-Z][A-Z0-9]*\d*))\s*-\s*(?:%s)\s*-\s*"
               % "|".join(sorted(TERMINAL_STATUS)), r"\1 - ", t)
    return t.strip()


def cmd_normalize(args):
    """
    Regenerate every entry heading's visual marker from its Status field.
    """
    doc = resolve_doc_arg(args.doc)
    if not doc or not doc.exists():
        print("no such doc: %s" % doc, file=sys.stderr)
        return 2
    if refuse_if_not_mine(doc, "normalize", getattr(args, "not_mine", False)):
        return 1
    with _lock(doc):
        lines = doc.read_text(encoding="utf-8").splitlines()
        entries, _ = parse_entries(lines)
        changes = []
        for e in entries:
            if e.get("archived"):
                continue
            st = status_of(e["body"])
            mk = STATUS_MARKER.get(st or "")
            if not mk:
                continue
            i = e["line"] - 1
            m = re.match(r"^(#{1,6})\s+(.*)$", lines[i])
            if not m:
                continue
            hashes, title = m.group(1), m.group(2)
            bare = _strip_markers(title)
            if st in TERMINAL_STATUS and not re.match(r"^\S+\s*-\s*%s\b" % st, bare):
                bare = re.sub(r"^(%s)\s*-\s*" % re.escape(e["id"]),
                              r"\1 - %s - " % st, bare, count=1)
            new = "%s %s %s" % (hashes, mk, bare)
            if new != lines[i]:
                changes.append((e["line"], lines[i], new))
                if not args.dry_run:
                    lines[i] = new
        if changes and not args.dry_run:
            write_doc(doc, "\n".join(lines) + "\n")

    print("orchdoc normalize - %s - %s"
          % (doc.name, "DRY RUN (nothing written)" if args.dry_run else "APPLIED"))
    print()
    for ln, old, new in changes[:14]:
        print("  line %-5d %s" % (ln, new[:96]))
    if len(changes) > 14:
        print("  ... and %d more" % (len(changes) - 14))
    if not changes:
        print("  every heading marker already matches its Status field.")
    elif args.dry_run:
        print()
        print("  Re-run with --commit to write.")
    return 0




def _stray_entry_looks_live(block, stopsign_means_open):
    """The first line in a stranded entry that says it may still be live work, as
    (reason, line), or None.
    """
    st = status_of(block)
    if st is not None and st not in TERMINAL_STATUS:
        m = STATUS_RE.search(block)
        return "Status is %s" % st, (m.group(0) if m else "")
    opn = first_open_subitem(block, stopsign_means_open)
    if opn:
        return "open sub-item", opn[1]
    infence = False
    for raw in block.splitlines():
        s = raw.lstrip()
        if is_fence_line(s):
            infence = not infence
            continue
        if infence:
            continue
        # a struck-through item is done by the rule, so only text left after the
        # strike keeps the box open
        txt = re.sub(r"~~.*?~~", "", re.sub(r"`[^`]*`", "", raw))
        if re.match(r"\s*(?:[-*+]|\d+[.)])\s+\[ \]\s+\S", txt):
            return "unticked box", raw
    return None


def _section_span(lines, num=None, name=None):
    """(heading_index, end_index) of the `## §num` section, or of the first UN-numbered `##`
    heading containing `name`. `end_index` is the next `##` heading outside a fence, or the
    end of the file. None when there is no such section."""
    infence, start = False, None
    for i, l in enumerate(lines):
        s = l.lstrip()
        if is_fence_line(s):
            infence = not infence
            continue
        if infence or not re.match(r"^##\s", l):
            continue
        if start is not None:
            return start, i
        m = SECTION_RE.match(l)
        if num is not None and m and m.group(1) == num:
            start = i
        elif name is not None and not m and name.upper() in l.upper():
            start = i
    return (start, len(lines)) if start is not None else None


def _heading_id(line):
    """The entry id a heading line carries, live or archived, else None."""
    m = ENTRY_HEAD_RE.match(line)
    if not m and line.startswith("#"):
        m = ARCHIVED_ID_RE.search(line)
    return m.group(1) if m else None


def _insert_in_section(lines, span, eid, block):
    """Insert `block` into the section `span`, before the first entry of the same prefix with
    a higher number - so E-IDORDER, which reads order per section and prefix, stays quiet -
    else at the end of the section. One blank line on each side. Returns the insert index.
    """
    head, end = span
    pos = end
    _lv = re.match(r"^(#+)", block[0] if block else "")
    level = len(_lv.group(1)) if _lv else 3
    m = re.match(r"^([A-Z]{1,3})(\d+)", eid or "")
    if m:
        pre, n = m.group(1), int(m.group(2))
        infence = False
        for i in range(head + 1, end):
            s = lines[i].lstrip()
            if is_fence_line(s):
                infence = not infence
                continue
            if infence:
                continue
            _hl = re.match(r"^(#+)\s", lines[i])
            if not _hl or len(_hl.group(1)) > level:
                continue
            other = _heading_id(lines[i])
            om = re.match(r"^([A-Z]{1,3})(\d+)$", other or "")
            if om and om.group(1) == pre and int(om.group(2)) > n:
                pos = i
                break
    piece = list(block)
    if pos > 0 and lines[pos - 1].strip():
        piece.insert(0, "")
    # Nothing follows at the end of the file, so no blank line goes after the block there:
    # it would leave the file ending on a blank line it did not have before.
    if pos < len(lines) and lines[pos].strip():
        piece.append("")
    lines[pos:pos] = piece
    return pos


FINDINGS_KINDS = ("F", "S")
FINDINGS_TITLE = "Findings"
FINDINGS_NOTE = "_closed_"
FINDINGS4_LABEL = "§4 FINDINGS"      # how `archive` names §4 when it reports what it left
# Why `relocate_closed_findings` left an entry in §4 when moving it would damage the document.
FOLD_REASON = "inside or across a <details> fold"
FENCE_REASON = "holds an unclosed ``` fence"
NOPLACE_REASON = "no §99 Findings subsection could be placed"
REPARSE_REASON = "the move would change how another entry parses"
NESTED_LIVE_REASON = "a live entry is nested inside it"


def _h2_outside_fences(lines):
    """Indices of every `## ` heading outside a fenced block, in file order.
    """
    return [i for i, (l, st) in enumerate(zip(lines, scan_structure(lines)))
            if not st[0] and re.match(r"^##\s", l)]


def _governing_outside_fences(lines):
    """The governing § number of every line: the last `# `-`### ` §-numbered heading above it that
    sits OUTSIDE a fence. `archive` decides what leaves §4 by this, so a move is decided
    once."""
    gov, cur = [], None
    for l, st in zip(lines, scan_structure(lines)):
        if not st[0]:
            m = _CANON_HEAD_RE.match(l)
            if m:
                cur = m.group(1)
        gov.append(cur)
    return gov


def _findings_span(lines, title=FINDINGS_TITLE):
    heads = _h2_outside_fences(lines)
    for k, i in enumerate(heads):
        m = SECTION_RE.match(lines[i])
        if (m and m.group(1).startswith(ARCHIVE_SECTION + ".")
                and lines[i][m.end():].strip() == title):
            return i, (heads[k + 1] if k + 1 < len(heads) else len(lines))
    return None


def _has_archive_section(lines):
    """True when the doc has a `## §99` or `## §99.x` heading outside a fence."""
    for i in _h2_outside_fences(lines):
        m = SECTION_RE.match(lines[i])
        if m and m.group(1).split(".")[0] == ARCHIVE_SECTION:
            return True
    return False


def _add_findings_section(lines, title=FINDINGS_TITLE, note=FINDINGS_NOTE):
    """Create `## §99.<next free number> Findings` after the last §99.x section and return
    its span. None when the doc has no §99 to put it in, or when the heading could not be
    placed where the parser reads it as a section - then `lines` is left as it was.
    """
    heads = _h2_outside_fences(lines)
    last, minor = None, len([n for n, _t, _d in SCHEMA_SECTIONS
                             if n.startswith(ARCHIVE_SECTION + ".")])
    for k, i in enumerate(heads):
        m = SECTION_RE.match(lines[i])
        if not m or m.group(1).split(".")[0] != ARCHIVE_SECTION:
            continue
        last = k
        if "." in m.group(1):
            minor = max(minor, int(m.group(1).split(".")[1]))
    if last is None:
        return None
    pos = heads[last + 1] if last + 1 < len(heads) else len(lines)
    # after the last section's own text, not after the blank lines that precede the next heading
    while pos > heads[last] + 1 and not lines[pos - 1].strip():
        pos -= 1
    piece = ["", "## §%s.%d %s" % (ARCHIVE_SECTION, minor + 1, title), "", note]
    lines[pos:pos] = piece
    span = _findings_span(lines, title)
    if span is None:
        # the heading landed inside an unclosed fence, where nothing reads it as a section
        del lines[pos:pos + len(piece)]
    return span


def _destination(lines, eid, stray_name, create=False):
    """(label, span) for where entry `eid` belongs: its kind's section from KIND_SECTION_NUM,
    else the un-numbered `stray_name` section, else (None, None).

    A finding or specimen goes to the §99 Findings subsection instead. When the doc does not
    have one yet the answer is (label, None), and `create=True` makes it. So a caller can ask
    before it cuts a block and create the section only once the block is out, when creating
    it cannot shift a line number the caller is still holding. A doc with no §99 at all keeps
    the old home, §4."""
    kind = (re.match(r"[A-Z]+", eid or "") or [""])[0][:1]
    if kind in FINDINGS_KINDS and _has_archive_section(lines):
        span = _findings_span(lines)
        if span is None and create:
            span = _add_findings_section(lines)
        if span is None:
            return "§%s %s (new)" % (ARCHIVE_SECTION, FINDINGS_TITLE), None
        return "§" + SECTION_RE.match(lines[span[0]]).group(1), span
    num = KIND_SECTION_NUM.get(kind, (None, None))[1]
    if num:
        span = _section_span(lines, num=num)
        if span:
            return "§" + num, span
    span = _section_span(lines, name=stray_name)
    return (stray_name, span) if span else (None, None)


def _entry_end(e):
    """Index one past an entry's last line. `len(body.splitlines())` is one short when the
    body ends in a blank line - splitlines() drops the empty string after the final newline -
    and that left a blank line behind at every cut, one more on every run."""
    return e["line"] - 1 + e["body"].count("\n") + 1


def _stamp_cut(lines, a, b):
    """(first override stamp in lines[a:b] outside a fence, or b; True when everything from
    that stamp to b is stamps and blank lines). A stamp is its OVERRIDE line and the lines
    after it up to a blank line, the span `parse_entries` reads as one attestation."""
    infence, c = False, b
    for i in range(a, b):
        s = lines[i].lstrip()
        if is_fence_line(s):
            infence = not infence
            continue
        if not infence and OVERRIDE_RE.match(lines[i]):
            c = i
            break
    in_stamp = False
    for i in range(c, b):
        ln = lines[i]
        if not ln.strip():
            in_stamp = False
        elif OVERRIDE_RE.match(ln):
            in_stamp = True
        elif not in_stamp:
            return c, False
    return c, True


def relocate_stray(lines, stray_name, left=None):
    """Move entries out of the un-numbered `stray_name` section into their kinds' sections.
    Mutates `lines`. Returns [(id, destination label)] for what moved. When `left` is a list,
    each entry left behind is appended to it as (id, reason, the line that says so), for
    `_print_stray_left`. Drops the stray heading once nothing is under it.

    Only entries the archiver would have moved are touched - an archived heading or a terminal
    status - and of those, only the ones with nothing still open in them. Anything else stays
    and is reported by the caller.
    """
    moved, kept = [], {}
    span = _section_span(lines, name=stray_name)
    if not span:
        return moved
    while True:
        span = _section_span(lines, name=stray_name)
        if not span:
            break
        head, end = span
        entries, _ = parse_entries(lines)
        stop = follows_stopsign_convention(entries)
        pick = None
        for e in entries:
            a = e["line"] - 1
            if not (head < a < end) or e["id"] in kept:
                continue
            st = status_of(e["body"])
            if not (e.get("archived") or st in TERMINAL_STATUS):
                m = STATUS_RE.search(e["body"])
                kept[e["id"]] = ("not finished (%s)" % ("Status %s" % st if st else "no Status"),
                                 m.group(0) if m else lines[a])
                continue
            label, dspan = _destination(lines, e["id"], stray_name)
            # (label, None) is a Findings subsection that does not exist yet - it is created
            # below, once the block is cut out
            if label is None or (dspan and dspan[0] == head):
                kept[e["id"]] = ("no section of its kind", lines[a])
                continue
            b = min(_entry_end(e), end)
            c, clean = _stamp_cut(lines, a, b)
            if not clean:
                kept[e["id"]] = ("text after a stamp", lines[c])
                continue
            why = _stray_entry_looks_live("\n".join(lines[a:c]), stop)
            if why:
                kept[e["id"]] = why        # possibly live work - the owner decides, not this
                continue
            pick = (e, label, b, c)
            break
        if not pick:
            break
        e, label, b, c = pick
        a = e["line"] - 1
        while c > a + 1 and not lines[c - 1].strip():
            c -= 1
        block = lines[a:c]
        # take the blank lines that followed the block with it, so the stray section does not
        # collect gaps; the stamps after them are left alone
        cut_end = c
        while cut_end < b and not lines[cut_end].strip():
            cut_end += 1
        cut = lines[a:cut_end]
        del lines[a:cut_end]
        label, dspan = _destination(lines, e["id"], stray_name, create=True)
        if dspan is None:
            # the Findings subsection could not be placed: put the entry back and say so,
            # rather than fail the whole run
            lines[a:a] = cut
            kept[e["id"]] = ("no section of its kind", lines[a])
            continue
        _insert_in_section(lines, dspan, e["id"], block)
        moved.append((e["id"], label))
    # An emptied stray section goes: a heading over nothing is the same clutter the move
    # removed. Only when it holds nothing at all - no entry, no stamp, no prose.
    span = _section_span(lines, name=stray_name)
    if span and not any(l.strip() for l in lines[span[0] + 1:span[1]]):
        at_eof = span[1] >= len(lines)
        del lines[span[0]:span[1]]
        if at_eof:
            while lines and not lines[-1].strip():
                lines.pop()
    elif span and _only_stamps(lines[span[0] + 1:span[1]]):
        del lines[span[0]]
        if span[0] < len(lines) and not lines[span[0]].strip():
            del lines[span[0]]
    if left is not None:
        left.extend((eid, why[0], why[1]) for eid, why in kept.items())
    return moved


def relocate_closed_findings(lines, stray_name, left=None):
    """Move the closed findings an older `archive` left in §4 into the §99 Findings subsection.
    Mutates `lines`. Returns [(id, destination label)] for what moved. When `left` is a list,
    each closed finding left in §4 is appended to it as (id, reason, the line that says so).
    """
    if not _has_archive_section(lines):
        return []
    entries, _ = parse_entries(lines)
    stop = follows_stopsign_convention(entries)
    gov = _governing_outside_fences(lines)
    scan = scan_structure(lines)
    spans = [(x["level"], x["line"] - 1, _entry_end(x)) for x in entries]
    picks, kept = [], {}
    for e in entries:
        a = e["line"] - 1
        if (e["id"][:1] not in FINDINGS_KINDS or not e.get("id_demoted")
                or gov[a] != "4"):
            continue
        if any(lv < e["level"] and s < a < t for lv, s, t in spans):
            continue
        b = _entry_end(e)
        c, clean = _stamp_cut(lines, a, b)
        if not clean:
            kept[e["id"]] = ("text after a stamp", lines[c])
            continue
        end = c
        while end > a + 1 and not lines[end - 1].strip():
            end -= 1
        part = scan[a:end]
        depth_before = scan[a - 1][1] if a > 0 else 0
        if (depth_before or part[-1][1] or sum(s[2] for s in part) != sum(s[3] for s in part)):
            kept[e["id"]] = (FOLD_REASON, lines[a])
            continue
        if sum(1 for l in lines[a:end] if is_fence_line(l)) % 2:
            kept[e["id"]] = (FENCE_REASON, lines[a])
            continue
        inner = [x for x in entries if x is not e and a < x["line"] - 1 < end
                 and not x.get("archived") and status_of(x["body"]) not in TERMINAL_STATUS]
        if inner:
            kept[e["id"]] = (NESTED_LIVE_REASON, lines[inner[0]["line"] - 1])
            continue
        why = _stray_entry_looks_live("\n".join(lines[a:c]), stop)
        if why:
            kept[e["id"]] = why        # possibly live work - the owner decides, not this
            continue
        picks.append((a, b, end, e["id"]))

    def refuse(reason):
        for _a, _b, _c, eid in picks:
            kept[eid] = (reason, lines[_a])
        if left is not None:
            left.extend((eid, why[0], why[1]) for eid, why in kept.items())
        return []

    # Everything below happens on a copy, and the copy replaces `lines` only once it is checked.
    work = list(lines)
    # Cut bottom-up so the line numbers of the entries above stay valid. The entries do not
    # overlap: each body ends at the next heading of its own level or shallower.
    blocks = []
    for a, b, c, eid in sorted(picks, reverse=True):
        cut_end = c
        while cut_end < b and not work[cut_end].strip():
            cut_end += 1
        blocks.append((eid, work[a:c]))
        del work[a:cut_end]
    moved = []
    for eid, block in reversed(blocks):
        label, span = _destination(work, eid, stray_name, create=True)
        if span is None:
            return refuse(NOPLACE_REASON)
        _insert_in_section(work, span, eid, block)
        moved.append((eid, label))
    if moved:
        sig = lambda ls: sorted((x["id"], x["title"], bool(x.get("archived")))
                                for x in parse_entries(ls)[0])
        if sig(work) != sig(lines):
            return refuse(REPARSE_REASON)
        lines[:] = work
    if left is not None:
        left.extend((eid, why[0], why[1]) for eid, why in kept.items())
    return moved


def _only_stamps(section_lines):
    """True if every paragraph in `section_lines` is an override stamp, and there is one.

    A stamp is an `<!-- ORCHDOC:OVERRIDE -->` line plus any lines after it up to a blank
    line, the same unit `_stamp_cut` uses. Any other paragraph means the section still
    holds content, so its heading stays."""
    seen = in_stamp = False
    for l in section_lines:
        if not l.strip():
            in_stamp = False
        elif OVERRIDE_RE.match(l):
            seen = in_stamp = True
        elif not in_stamp:
            return False
    return seen


def _print_stray_left(dest_name, left):
    """What `archive` left in the stray section, and why, so nothing stays there unexplained.
    `left` is relocate_stray's (id, reason, line) list; each reason gets its own heading, so
    an open entry is never reported as one with nowhere to go."""
    groups = (
        ("may still be live work - tick or strike the line, or reopen the entry in §2/§3;"
         " the next archive moves a closed one",
         lambda r: r in ("unticked box", "open sub-item", NESTED_LIVE_REASON)
         or r.startswith("Status is")),
        ("not finished - an open entry under this heading belongs in §2/§3",
         lambda r: r.startswith("not finished")),
        ("text follows an override stamp inside the entry, so where it ends is unclear -"
         " move it by hand", lambda r: r == "text after a stamp"),
        ("no section of their kind to go to", lambda r: r == "no section of its kind"),
        ("moving them would split a <details> fold or a ``` fence, or change how the rest of"
         " the doc parses - move them by hand",
         lambda r: r in (FOLD_REASON, FENCE_REASON, NOPLACE_REASON, REPARSE_REASON)),
    )
    for title, test in groups:
        rows = [x for x in left if test(x[1])]
        if not rows:
            continue
        print("  left in `## %s`, %s:" % (dest_name, title))
        for eid, why, line in rows:
            print("    %-10s %-24s %s" % (eid, why, line.strip()[:80]))


def cmd_archive(args):
    """
    Move terminal-status entries out of sections that promise live items.

    A RESOLVED decision under a section that promises live items is clutter. The active list has to hold ONLY active items or the reader
    cannot trust it - which is the same property as the generated plate, applied to the
    body of the document.
    """
    doc = resolve_doc_arg(args.doc)
    if not doc or not doc.exists():
        print("no such doc: %s" % doc, file=sys.stderr)
        return 2
    if refuse_if_not_mine(doc, "archive", getattr(args, "not_mine", False)):
        return 1
    dest_name = args.into

    with _lock(doc):
        _before = doc.read_text(encoding="utf-8")
        lines = _before.splitlines()
        left = []
        _had_stray = _section_span(lines, name=dest_name) is not None
        relocated = relocate_stray(lines, dest_name, left=left)
        dropped = _had_stray and _section_span(lines, name=dest_name) is None
        left4 = []
        relocated4 = relocate_closed_findings(lines, dest_name, left=left4)
        entries, sections = parse_entries(lines)

        movers, held = [], []
        _govmap = _governing_outside_fences(lines)
        for e in entries:
            if e.get("archived"):
                continue
            st = status_of(e["body"])
            _gov = _govmap[e["line"] - 1]
            _live = is_active_section(e["section"]) or bool(
                _gov and _gov.split(".")[0] in ("2", "3", "4"))
            if st in TERMINAL_STATUS and _live:
                if has_open_subitems(e["body"], follows_stopsign_convention(entries)):
                    held.append(e["id"])
                    continue
                movers.append(e)
        if held:
            print("orchdoc archive - %s" % doc.name)
            print("  HELD BACK, terminal status but a sub-item is still open: %s"
                  % ", ".join(held))
            print("  the rule: an item moves to 99 only when ALL its sub-items are")
            print("  done. Mark the container IN PROGRESS and strike the finished sub-items -")
            print("  they stay visible, which is the point: seeing where the done work sits")
            print("  is what makes the remaining decision readable.")
            print()
        if not movers and not relocated and not dropped and not relocated4:
            if not held:
                print("orchdoc archive - %s" % doc.name)
                print("  no terminal-status entries are sitting in an active section.")
            _print_stray_left(dest_name, left)
            _print_stray_left(FINDINGS4_LABEL, left4)
            return 0

        # Kept so a misplaced insert can be refused rather than written - see below the loop.
        _pre_move = list(lines)
        _renamed = {}
        refused_moves = []
        # Cut bottom-up so earlier line numbers stay valid.
        blocks = []
        for e in sorted(movers, key=lambda x: x["line"], reverse=True):
            start = e["line"] - 1
            end = _entry_end(e)
            blocks.append((e["id"], status_of(e["body"]), lines[start:end]))
            del lines[start:end]

        placed = []
        if blocks:
            for _id, _st, blk in reversed(blocks):
                blk = list(blk)
                if blk and blk[0].startswith("#"):
                    head = re.sub(r"^(#+)\s*\S*\s*\*{0,2}%s\*{0,2}\s*[-–—.:]\*{0,2}\s*"
                                  % re.escape(_id), r"\1 ", blk[0])
                    _m_hash = re.match(r"^(#+)\s*(.*)$", head)
                    if _m_hash and ID_RE.match(strip_decoration(_m_hash.group(2))):
                        head = "%s %s - %s" % (_m_hash.group(1), _st, _m_hash.group(2))
                    _old_t = re.match(r"^#{1,6}\s+(.*)$", blk[0])
                    blk[0] = head.rstrip() + archived_heading_suffix(_id, _st)
                    _new_t = re.match(r"^#{1,6}\s+(.*)$", blk[0])
                    if _old_t and _new_t:
                        _renamed[(_id, _old_t.group(1).rstrip())] = _new_t.group(1).rstrip()
                while len(blk) > 1 and not blk[-1].strip():
                    blk.pop()
                label, span = _destination(lines, _id, dest_name, create=True)
                if span is None:
                    lines += ["", "## %s" % dest_name, ""]
                    label, span = dest_name, _section_span(lines, name=dest_name)
                _insert_in_section(lines, span, _id, blk)
                placed.append((_id, _st, label))
            _want = sorted(
                (x["id"], _renamed.get((x["id"], x["title"]), x["title"]),
                 True if (x["id"], x["title"]) in _renamed else bool(x.get("archived")))
                for x in entries)
            _got = sorted((x["id"], x["title"], bool(x.get("archived")))
                          for x in parse_entries(lines)[0])
            if _want != _got:
                lines[:] = _pre_move
                refused_moves = [p[0] for p in placed]
                placed = []

        _old_lines = _before.splitlines()
        _pspan, _pwhy = plate_span(_old_lines)
        if not _pwhy and _pspan:
            _was = _old_lines[_pspan[0]:_pspan[1] + 1]
            if _was == build_plate_block(parse_entries(_old_lines)[0], _old_lines):
                _nspan, _nwhy = plate_span(lines)
                if not _nwhy and _nspan:
                    lines[_nspan[0]:_nspan[1] + 1] = build_plate_block(
                        parse_entries(lines)[0], lines)
        # The section index at the top lists every numbered section. When this run adds
        # `§99.x Findings` an index that was current before it stays current - the same rule
        # as the plate above, and nothing else would notice the index missing a section.
        _ispan, _iwhy = index_span(_old_lines)
        if (not _iwhy and _ispan
                and _old_lines[_ispan[0]:_ispan[1] + 1] == render_index(_old_lines)):
            _nspan, _nwhy = index_span(lines)
            if not _nwhy and _nspan:
                lines[_nspan[0]:_nspan[1] + 1] = render_index(lines)

        if not args.dry_run:
            write_doc(doc, "\n".join(lines) + "\n", expect=_before)

    print("orchdoc archive - %s - %s"
          % (doc.name, "DRY RUN (nothing written)" if args.dry_run else "APPLIED"))
    print()
    if placed:
        print("  %d finished entr%s would leave the active sections:"
              % (len(placed), "y" if len(placed) == 1 else "ies"))
        for _id, _st, label in placed:
            print("    %-10s %-10s -> %s" % (_id, _st, label))
    if refused_moves:
        print("  REFUSED, nothing in this batch moved: placing %s would have changed how another"
              " entry parses" % ", ".join(refused_moves))
        print("  (an insert landed inside a fence or a <details> fold). Move them by hand.")
    if relocated:
        print("  %d entr%s moved out of `## %s` into %s own section:"
              % (len(relocated), "y" if len(relocated) == 1 else "ies", dest_name,
                 "its" if len(relocated) == 1 else "their"))
        for _id, label in relocated:
            print("    %-10s -> %s" % (_id, label))
    if dropped:
        print("  `## %s` held nothing after the move, so its heading was removed." % dest_name)
    if relocated4:
        import textwrap
        print("  %d closed finding%s moved out of §4 into %s, unchanged:"
              % (len(relocated4), "" if len(relocated4) == 1 else "s",
                 ", ".join(sorted({lb for _i, lb in relocated4}))))
        for row in textwrap.wrap(", ".join(i for i, _lb in relocated4), 88):
            print("    " + row)
    _print_stray_left(dest_name, left)
    _print_stray_left(FINDINGS4_LABEL, left4)
    if args.dry_run:
        print()
        print("  Re-run with --commit to move them. Nothing is deleted; they move.")
    return 0


def cmd_clones(args):
    """

    Git's 3-way merge protects the content in every case. Silent reversion needs the
    merge machinery bypassed - force-push, copying a tree over - a different hazard.

    "A merge conflict announces itself. A phantom line does not. The files open and read
     as complete" - the same property that makes a truncated skill dangerous.

    So this check does not protect the repo. It protects every CLAIM you make about the
    repo while standing in it.
    """
    repos = citable_repos()
    print("orchdoc clones - is every checkout current with its remote?")
    print()
    worst = 0
    for r in repos:
        if not (r / ".git").exists():
            continue
        if not args.no_fetch:
            git(["fetch", "--quiet", "origin"], cwd=r)
        rc, head, _ = git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=r)
        rc2, behind, _ = git(["rev-list", "--count", "HEAD..%s" % CANONICAL_REF], cwd=r)
        if rc2 != 0 or not behind.isdigit():
            print("  [--]    %-34s no origin/main to compare" % r.name)
            continue
        n = int(behind)
        rc3, dirty, _ = git(["status", "--porcelain"], cwd=r)
        ndirty = len([x for x in dirty.splitlines() if x.strip()])
        if n == 0:
            print("  [OK]    %-34s current  (%s)" % (r.name, head))
        else:
            worst = 1
            print("  [BEHIND]%-34s %d commits behind origin/main  (%s%s)"
                  % (r.name, n, head,
                     ", %d dirty file(s)" % ndirty if ndirty else ""))
            if ndirty:
                print("          %-34s ANY CLAIM YOU MAKE FROM THIS TREE IS SUSPECT -"
                      % "")
                print("          %-34s deleted files still read as present, and counts"
                      % "")
                print("          %-34s are of the old content. Git protects the merge;"
                      % "")
                print("          %-34s nothing protects your reasoning." % "")
    print()
    print("  Oracle: git rev-list --count HEAD..origin/main must be 0 before branching")
    print("  or editing anywhere long-lived. One number; it cannot read plausibly wrong.")
    return worst


def _vgit(args, cwd=PROJECTS):
    """git via an argument LIST. No shell, so no MSYS path mangling, ever."""
    p = subprocess.run(["git", "-C", str(cwd)] + list(args),
                       capture_output=True, text=True, timeout=60, creationflags=flags())
    return p.returncode, p.stdout, p.stderr


def cmd_verify(args):
    """
    Known-good verification primitives. Each names its oracle and cannot return a
    plausible middle answer.

    Built because two lessons were RECORDED and not mechanised: Git Bash silently
    mangles `REF:dir/file`, and a check run through a different execution path than the
    tool is a different measurement. A library removes the hand-rolled probe entirely.
    """
    what = args.what
    repo = Path(args.repo) if args.repo else PROJECTS

    if what == "at":
        # Show a file at a ref. THE case Git Bash breaks: `git show REF:dir/file.py`
        # becomes `REF;dir\file.py` in MSYS and reports a present file as missing.
        rc, out, err = _vgit(["show", "%s:%s" % (args.ref, args.path)], cwd=repo)
        print("  oracle: git show %s:%s   (argument list, no shell)"
              % (args.ref, args.path))
        if rc != 0:
            print("  ABSENT at that ref. git said: %s" % err.strip()[:100])
            return 1
        print("  PRESENT - %d lines" % len(out.splitlines()))
        return 0

    if what == "landed":
        # Is the working copy identical to the canonical ref? The question
        # `git status` cannot answer, because it compares tree to HEAD.
        rel = args.path
        rc, canon, _ = _vgit(["show", "%s:%s" % (CANONICAL_REF, rel)], cwd=repo)
        if rc != 0:
            print("  oracle: git show %s:%s -> absent" % (CANONICAL_REF, rel))
            print("  NOT LANDED - the file does not exist on %s" % CANONICAL_REF)
            return 1
        try:
            local = (repo / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            print("  cannot read local copy: %s" % e)
            return 1
        same = local.replace("\r\n", "\n").rstrip("\n") == \
            canon.replace("\r\n", "\n").rstrip("\n")
        print("  oracle: byte comparison of the working copy against %s"
              % CANONICAL_REF)
        print("          (git status compares tree to HEAD and cannot answer this)")
        print("  %s" % ("LANDED - identical" if same else
                        "NOT LANDED - the copy on disk differs from %s" % CANONICAL_REF))
        return 0 if same else 1

    if what == "merged":
        sha = args.sha
        _vgit(["fetch", "--quiet", "origin"], cwd=repo)
        rc, _, _ = _vgit(["merge-base", "--is-ancestor", sha, CANONICAL_REF], cwd=repo)
        if rc == 0:
            print("  oracle: git merge-base --is-ancestor %s %s -> exit 0"
                  % (sha[:10], CANONICAL_REF))
            print("  MERGED (ancestor)")
            return 0
        rc2, subj, _ = _vgit(["log", "-1", "--format=%s", sha], cwd=repo)
        if rc2 == 0 and subj.strip():
            rc3, found, _ = _vgit(
                ["log", CANONICAL_REF, "--oneline", "--grep",
                 subj.strip()[:60], "-F"], cwd=repo)
            if found.strip():
                print("  oracle: ancestry said NO, but the commit SUBJECT is on %s"
                      % CANONICAL_REF)
                print("          %s" % found.splitlines()[0][:90])
                print("  MERGED (squashed - ancestry would have lied here)")
                return 0
        print("  oracle: neither ancestry nor subject found on %s" % CANONICAL_REF)
        print("  NOT MERGED")
        return 1

    if what == "current":
        _vgit(["fetch", "--quiet", "origin"], cwd=repo)
        rc, behind, _ = _vgit(["rev-list", "--count", "HEAD..%s" % CANONICAL_REF],
                              cwd=repo)
        n = behind.strip()
        print("  oracle: git rev-list --count HEAD..%s -> %s" % (CANONICAL_REF, n))
        if rc != 0 or not n.isdigit():
            print("  UNKNOWN - no %s to compare against" % CANONICAL_REF)
            return 1
        print("  %s" % ("CURRENT" if n == "0" else
                        "STALE - %s commits behind. Every claim made from this tree is "
                        "suspect." % n))
        return 0 if n == "0" else 1

    print("unknown check: %s" % what, file=sys.stderr)
    return 2



# Heading text -> schema number. ORDER IS SIGNIFICANT: the specific patterns must be
# tested before the general ones, because "RESOLVED DECISIONS" matches both the decision
# rule and the done rule and only one of those answers is right.
#
# Derived from surveying live docs, not invented - which is why it is a
# rename table rather than a reorganisation plan.
ADOPT_RULES = [
    (["SPECIMEN"],                     [],                    "4"),
    (["GUARD"],                        [],                    "5"),
    (["FINDING"],                      [],                    "4"),
    (["ON CLAUDE'S PLATE", "IN FLIGHT"], [],                  "3"),
    (["DECISION"],  ["RESOLVED", "ARCHIV", "DONE", "ANSWERED", "SUPERSEDED"], "2.1"),
    (["DECISION"],                     [],                    "99.1"),
    (["QUESTION"],  ["ANSWERED", "RESOLVED", "DONE"],         "2.2"),
    (["QUESTION"],                     [],                    "99.2"),
    (["TO-DO"],     ["DONE", "SHIPPED", "COMPLETE"],          "2.3"),
    (["TODO"],      ["DONE", "SHIPPED", "COMPLETE"],          "2.3"),
    (["TO-DO"],                        [],                    "99.3"),
    (["TODO"],                         [],                    "99.3"),
    (["PLATE"],                        [],                    "2"),
    (["URL"],                          [],                    "1"),
    (["LINK"],                         [],                    "1"),
    (["LOGIN"],                        [],                    "1"),
    (["DELIVERABLE", "PATH"],          [],                    "1"),
    (["DELIVERABLE", "INDEX"],         [],                    "1"),
    (["DONE"],                         [],                    "99"),
    (["RESOLVED"],                     [],                    "99"),
    (["ARCHIV"],                       [],                    "99"),
    (["SUPERSEDED"],                   [],                    "99"),
    (["PARKED"],                       [],                    "99"),
]


def adopt_number(heading):
    """Schema number for an existing heading, or None if it is genuinely unclear.
    """
    t = heading.lstrip("#").strip().upper()

    if ID_RE.match(re.sub(r"^[\W\s]+", "", heading.lstrip("#").strip())):
        return None

    for musts, nots, num in ADOPT_RULES:
        if all(m in t for m in musts) and not any(n in t for n in nots):
            return num
    return None


# The identity line. `oN` is REQUIRED to match the filename; the trailing role is the
# orchestrator's own words and is preserved verbatim.
TITLE_RE = re.compile(
    r"^#\s+(?:\W+\s*)?Orchestrator\s+Decision\s+Doc\s*[-\u2013\u2014]\s*"
    r"\*{0,2}(o\d+)\*{0,2}\s*(?:<br\s*/?>)?\s*(.*)$", re.IGNORECASE | re.DOTALL)


def canonical_title(num, role=""):
    """
    Two failed on rendering; the third failed on RISK:

        1. H1 + paragraph subtitle - subtitle orphaned below the generated index
        2. one line with <br>      - the renderer escaped it into visible "<br>" text
        3. two H1 lines            - worked, but a second "# " can be read as a second
                                     DOCUMENT TITLE by any future title-extractor
        4. one line, no tag        - this

    The visual line break is simply unavailable: markdown cannot break inside a heading,
    inline HTML is escaped here, and a second heading is now barred by E-ONEH1. The title
    wraps on its own, which is what it was doing anyway.
    """
    role = (role or "").strip()
    if role and not role.startswith("("):
        role = "(%s)" % role.strip("()")
    return "# Orchestrator Decision Doc - %s%s" % (num, (" " + role) if role else "")


def title_span(lines):
    """(start, end) of the title block, inclusive. THE one definition of where it ends.

    Round one of this format placed the generated index after the title's FIRST line and
    orphaned the subtitle below it. Every top-of-document insertion now asks this function
    instead of re-deriving the answer, because three places deriving it independently is
    how the first version got it wrong in one of them.
    """
    hi = next((i for i, l in enumerate(lines[:40]) if l.startswith("# ")), None)
    if hi is None:
        return None, None
    end = hi
    if hi + 1 < len(lines) and lines[hi + 1].startswith("# "):
        end = hi + 1
    return hi, end



def _git_date(doc, first=False):
    """Commission date (first commit adding the file) or last-touched date, from git.

    Returns None when git cannot answer - an unlanded doc has no commit history, and
    printing a fabricated date would be exactly the claim-versus-measurement error this
    block exists to avoid.
    """
    # Query origin/main, NOT HEAD. OrchDocs are landed to main by plumbing while the
    # working tree sits on a feature branch, so `git log -- <doc>` from HEAD finds no
    # history at all and the field would read "not yet landed" for a doc that landed
    # hours ago. The canonical ref is where the history is, which is the same reason
    # every other check in this file resolves against origin/main.
    if first:
        args = ["log", CANONICAL_REF, "--diff-filter=A", "--follow",
                "--format=%ad", "--date=format:%d-%b-%Y", "--", doc.name]
    else:
        args = ["log", CANONICAL_REF, "-1", "--format=%ad",
                "--date=format:%d-%b-%Y %H:%M", "--", doc.name]
    rc, out, _ = git(args)
    if rc != 0 or not out.strip():
        return None
    return out.strip().splitlines()[-1 if first else 0].strip()


def render_meta(doc, lines, landing_now=False):
    """The header metadata - every field a measurement, none of them a claim.
    """
    name = doc.name
    commissioned = _git_date(doc, first=True)
    updated = (_dt.datetime.now().strftime("%d-%b-%Y %H:%M") if landing_now
               else _git_date(doc))
    # Use the PLATE's own selection rule, never a private copy of it. A hand-rolled
    # second copy read a "status" key that parse_entries does not even return, so the
    # count was silently 0 while the plate itself listed an open item - a header
    # contradicting the section it points at, which is precisely the multi-copy defect
    # this tool exists to remove. One rule, one reader.
    entries, _sections = parse_entries(lines)

    _pb = build_plate_block(entries, lines)
    _m = re.search(r"_(\d+) open\. Generated by", "\n".join(_pb))
    open_plate = int(_m.group(1)) if _m else 0

    out = [META_BEGIN, ""]
    out.append("| | |")
    out.append("|---|---|")
    out.append("| **Commissioned** | %s |" % (commissioned or "_not yet landed_"))
    # The provenance in the label must match where the value ACTUALLY came from. Saying
    # "from the commit log" on a value stamped at landing time is the same defect this
    # whole field just failed at: a description asserting something the source does not say.
    out.append("| **Last updated** | %s _(%s, never hand-written)_ |"
               % (updated or "_not yet landed_",
                  "stamped by `orchdoc commit` as it landed" if landing_now
                  else "from the commit log"))
    _drv = os.environ.get("CLAUDE_CODE_HOST_SESSION_ID") or ""
    if landing_now and _drv:
        out.append("| **Driven by** | `%s` _(stamped by `orchdoc commit`; the session that "
                   "last landed this doc)_ |" % sanitize_field(_drv, 90))
    else:
        # Not landing: carry the existing value forward rather than dropping it. A refresh
        # must never blank a field it has no fresh reading for.
        for _ln in (lines or []):
            _m = re.match(r"^\|\s*\*\*Driven by\*\*\s*\|\s*(.+?)\s*\|\s*$", _ln)
            if _m:
                out.append("| **Driven by** | %s |" % _m.group(1))
                break
    _plate_title = dict((n, t) for n, t, _d in schema_sections())["2"]
    _plate_label = _plate_title.replace("LIVE ON ", "").replace("'S PLATE", "")
    _plate_label = _plate_label.title() + "'s plate"     # THE HUMAN -> the human's plate
    _plate_anchor = "#\u00a72-" + re.sub(r"[^a-z0-9]+", "-",
                                        _plate_title.lower().replace("'", "")).strip("-")
    out.append("| **Open on %s** | **%%d** - see [\u00a72](%s) |"
               % (_plate_label, _plate_anchor)
               % open_plate)
    out.append("| **Canonical copy** | `origin/main : %s` - this is the ONLY one |" % name)
    out.append("| **Verify it is current** | `%s verify current --path %s` |"
               % (tool_command(), name))
    out.append("")
    out.append(META_END)
    return out




def _config_get(key):
    """One place a standing choice lives, so there is one place to change it.

    Kept dependency-free and failure-tolerant on purpose: a missing or malformed config must
    degrade to the previous behaviour, never crash a linter run. A config that can break the
    tool is a config people delete.
    """
    try:
        import json
        p = PROJECTS / ".orchdoc-config.json"
        if p.exists():
            return (json.loads(p.read_text(encoding="utf-8")) or {}).get(key) or None
    except Exception:
        pass
    return None


def human_name(default="THE HUMAN"):
    """What to call the user, from Claude Code's own account record.

    `~/.claude.json` -> oauthAccount.displayName. An exhaustive scan of that config found
    exactly one name field, and it is not exported to the environment, so this reads the
    file directly.

    Returns the default on ANY failure - missing file, missing key, unreadable JSON. A doc
    that cannot learn the name should say "THE HUMAN" (which is what the published skill
    says anyway); it should never fail to generate over a nicety.
    """
    cfg = _config_get("human_name")
    if cfg:
        return cfg.strip().upper()[:24]
    try:
        import json as _json
        cfg = Path(os.path.expanduser("~")) / ".claude.json"
        name = _json.loads(cfg.read_text(encoding="utf-8")).get(
            "oauthAccount", {}).get("displayName", "")
        name = (name or "").strip()
        if not name:
            return default
        for tok in name.split():
            clean = re.sub(r"[^A-Za-zÀ-ɏ'\-]", "", tok)[:24]
            if clean:
                return clean.upper()
        return default
    except Exception:
        return default


def schema_sections():
    """SCHEMA_SECTIONS with the plate heading personalised.

    A function, not a constant, so the name is read when a doc is written rather than when
    the module is imported - which also means the checker and the generator cannot disagree
    about it, since both call this.
    """
    who = human_name()
    return [(n, t.replace("{NAME}", who), d.replace("{NAME}", who.title()))
            for n, t, d in SCHEMA_SECTIONS]


def render_index(lines):
    """The navigable spine, generated from the headings that actually exist.
    """
    present = {}
    for ln in lines:
        m = SECTION_RE.match(ln)
        if m:
            # Keep the TEXT: a custom section can only be listed by the name its author
            # gave it, and stripping the number leaves exactly that.
            present[m.group(1)] = re.sub(r"^#+\s*\W*\s*\u00a7?\s*[\d.]+\s*", "",
                                         ln).strip() or ("\u00a7" + m.group(1))

    out = [INDEX_BEGIN, ""]
    out.append("**Sections.** Numbered, so a reference survives the prose moving.")
    out.append("")
    known = dict((n, (t, d)) for n, t, d in schema_sections())
    allnums = sorted(set(known) | set(present),
                     key=lambda n: [int(p) for p in n.split(".")])
    for num in allnums:
        depth = num.count(".")
        if num in known:
            title, note = known[num]
            mark = "" if num in present else "  **<- MISSING**"
        else:
            title, note, mark = present[num], "this orchestrator's own", ""
            if num.startswith(ARCHIVE_SECTION + ".") and title == FINDINGS_TITLE:
                note = FINDINGS_NOTE.strip("_")
        out.append("%s- **\u00a7%s %s** - %s%s" % ("  " * depth, num, title, note, mark))
    out.append("")

    out.append(INDEX_END)
    return out



def reorder_sections(lines):
    """Return lines with numbered top-level sections in ascending order.

    Raises ValueError if any line would be lost - the caller must not write on that.
    """
    fz = fenced_lines(lines)

    # Split into: head (everything before the first numbered section) + blocks.
    first = None
    for i, ln in enumerate(lines):
        if i not in fz and ln.startswith("## ") and SECTION_RE.match(ln):
            first = i
            break
    if first is None:
        return lines

    head, rest = lines[:first], lines[first:]

    # A block runs from one TOP-LEVEL heading to the next. Un-numbered "## " sections
    # therefore travel with the numbered section above them, which keeps an
    # orchestrator's domain content next to whatever it was written beside.
    blocks, cur = [], None
    for j, ln in enumerate(rest, start=first):
        if j not in fz and ln.startswith("## "):
            if cur is not None:
                blocks.append(cur)
            cur = [ln]
        else:
            (cur if cur is not None else head).append(ln)
    if cur is not None:
        blocks.append(cur)

    def key(b):
        m = SECTION_RE.match(b[0])
        if not m:
            return (1, 0, 0)                      # un-numbered: keep after numbered
        parts = [int(x) for x in m.group(1).split(".")]
        return (0, parts[0], parts[1] if len(parts) > 1 else 0)

    ordered = head + [ln for b in sorted(blocks, key=key) for ln in b]

    if sorted(ordered) != sorted(lines):
        lost = len(lines) - len(ordered)
        raise ValueError("reorder would change content (%+d lines) - REFUSED" % -lost)
    return ordered



def render_findings_index(lines):
    """The findings index, for a reader who is ALREADY in the findings section.

    Generated, like everything else derived - a hand-written list of 53 findings is a
    second copy of the truth and would rot on the first entry anyone added.
    """
    fin = []
    _fz = fenced_lines(lines)
    for i, ln in enumerate(lines):
        if i in _fz:
            continue
        m = re.match(r"^###\s+(?:\W+\s*)?(F\d+)\b\s*[-\u2013]?\s*(.*)$", ln)
        if m:
            title = m.group(2).strip().rstrip(".")
            title = re.sub(r"^(?:DONE|OPEN|RESOLVED|RECORDED)\s*[-\u2013]\s*", "", title)
            fin.append((m.group(1), title[:100]))
    if not fin:
        return []
    out = [FINDEX_BEGIN, "", "**%d findings.**" % len(fin), ""]
    for fid, title in fin:
        out.append("- `%s` - %s" % (fid, title))
    out += ["", FINDEX_END]
    return out



def cmd_handoff(args):
    """Verify every OPEN item in a doc has a home in a LIVING doc before it is frozen.
    """
    src = resolve_doc_arg(args.doc)
    if not src or not src.exists():
        print("no such doc: %s" % args.doc, file=sys.stderr)
        return 2

    lines = src.read_text(encoding="utf-8").split("\n")
    entries, _ = parse_entries(lines)
    live = [e for e in entries
            if not e.get("archived") and status_of(e["body"]) in PLATE_STATUS]

    raw_ids = set()
    prose_titles = {}
    cur = ""
    _fz_h = fenced_lines(lines)
    for i, ln in enumerate(lines):
        if i in _fz_h:
            continue
        if ln.startswith("## "):
            cur = ln
        if not is_active_section(cur):
            continue
        # NOTE: NO \b. A word-boundary escape sent through a shell heredoc arrives
        # as a literal 0x08 BACKSPACE, which matches nothing and ships the check
        # DEAD while looking correct in a diff. The lookahead cannot be corrupted.
        for m in re.finditer(r"\*\*([A-Z]{1,3}\d{1,3})(?![A-Za-z0-9])", ln):
            raw_ids.add(m.group(1))
            # keep the surrounding text: the TITLE is what actually identifies an item
            # across documents, because the id alone is namespaced per doc.
            tail = ln[m.end():].lstrip(" *—-:").strip()
            if tail and len(tail) > len(prose_titles.get(m.group(1), "")):
                prose_titles[m.group(1)] = tail
    known = {e["id"] for e in entries}
    extra = sorted(raw_ids - known)

    # Every OTHER OrchDoc is a candidate receiver. Read them from the CANONICAL ref, not
    # the working tree: a receiver that only has the item on someone's local branch has
    # not received it in any sense that survives.
    others = {}
    for p in sorted(PROJECTS.glob("ORCHESTRATOR-DECISIONS-*.md")):
        if p.name == src.name:
            continue
        rc, txt, _ = git(["show", "%s:%s" % (CANONICAL_REF, p.name)])
        others[p.name] = txt if rc == 0 else p.read_text(encoding="utf-8", errors="replace")

    print("orchdoc handoff - %s" % src.name)
    print("  %d parsed open item(s), %d id(s) found only as prose" % (len(live), len(extra)))
    if not live and not extra:
        print("  [REFUSE] nothing to account for AND nothing parsed - this doc's items are")
        print("           not in a form this check can read, so it cannot certify that")
        print("           freezing it is safe. That is NOT the same as 'all clear'.")
        print("           Entries need `### <ID> - title` headings; until then, verify by")
        print("           hand and say so explicitly.")
        return 1
    orphans = []
    for e in live:
        title_key = strip_decoration(e["title"])[:40].lower()
        found = []
        for name, txt in others.items():
            if re.search(r"\b%s\b" % re.escape(e["id"]), txt) or \
                    (len(title_key) > 12 and title_key in txt.lower()):
                found.append(name)
        if found:
            print("  [ok]      %-8s -> %s" % (e["id"], ", ".join(found)))
        else:
            orphans.append(e)
            print("  [ORPHAN]  %-8s %s" % (e["id"], strip_decoration(e["title"])[:52]))

    # prose-only ids get the same treatment as parsed entries
    for eid in extra:
        title = strip_decoration(prose_titles.get(eid, "")).strip()
        src_tag = src.name.replace("ORCHESTRATOR-DECISIONS-", "").replace(".md", "")
        found = []
        for n, txt in others.items():
            qualified = re.search(
                r"(?<![A-Za-z0-9])%s\s*[:\-]?\s*%s(?![A-Za-z0-9])"
                % (re.escape(src_tag), re.escape(eid)), txt, re.I)
            adopted = (re.search(r"(?:adopted|from|inherited)[^\n]{0,40}%s"
                                 % re.escape(src_tag), txt, re.I)
                       and re.search(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])"
                                     % re.escape(eid), txt))
            by_title = len(title) > 18 and title[:34].lower() in txt.lower()
            if qualified or adopted or by_title:
                found.append(n)
        if found:
            print("  [ok]      %-8s -> %s  (prose-only id)" % (eid, ", ".join(found)))
        else:
            orphans.append({"id": eid, "title": eid + " (found only as prose)"})
            print("  [ORPHAN]  %-8s found only as prose, and in no other doc" % eid)

    if not orphans:
        print("  every open item appears in a living doc.")
        return 0

    print()
    print("  [REFUSE] %d open item(s) exist ONLY in this doc." % len(orphans))
    print("           Freezing it strands them - nobody is watching the gap between")
    print("           two owners, which is how an item can sit orphaned for days.")
    print("           Move each into the receiving doc FIRST, then re-run.")
    print("           A sender's handoff note is an assertion; the item appearing in the")
    print("           receiving doc is the fact.")
    return 1




# A path or URL a reader might need to OPEN. Deliberately narrow: a bare word with a dot in
# it is not an asset, and a link list padded with noise is one nobody reads.
ASSET_URL_RE = re.compile(r"https?://[^\s)\]<>\"']+")
ASSET_PATH_RE = re.compile(
    r"(?<![\w/.])((?:[A-Za-z]:[\\/]|\.{0,2}[\\/]|\.shared[\\/]|[\w.-]+[\\/])"
    r"[\w.\\/-]*\.(?:md|py|ts|tsx|json|ps1|sh|txt|ya?ml))")


def harvest_assets(lines, doc):
    """Every URL and path the doc mentions, with whether it RESOLVES.

    The point is not to list strings - it is to distinguish a pointer a reader can follow
    from one that is already dead. An unverified link list is the same false comfort as an
    unverified status field.
    """
    body = "\n".join(lines)
    found = {}
    for m in ASSET_URL_RE.finditer(body):
        # Strip markdown punctuation that abuts a URL in prose. A trailing backtick
        # produced a row labelled "`" pointing at a 404 - a dead pointer manufactured BY
        # the section whose job is to prevent dead pointers.
        u = m.group(0).rstrip(".,;:`*_”’'\"")
        found.setdefault(u, {"kind": "url", "exists": None})
    for m in ASSET_PATH_RE.finditer(body):
        raw = m.group(1).strip()
        if raw.startswith(("http", "#")):
            continue
        if "..." in raw or "<" in raw:
            continue          # an ELISION in prose ("_legacy/.../Foo.ts"), not a path
        probe = raw
        # A leading "/.claude/..." is almost always "~/.claude/..." with the tilde eaten by
        # the surrounding markdown. Resolving it against the workspace reports a LIVE file
        # as dead - and a checker that confidently calls a real asset dead is the damaging
        # direction, not the harmless one.
        if probe.startswith(("/.claude", "\\.claude")):
            probe = str(Path(os.path.expanduser("~")) / probe.lstrip("/\\"))
        pp = Path(probe)
        if not pp.is_absolute():
            pp = PROJECTS / probe
        exists = pp.exists()
        if not exists:
            for alt in (Path(os.path.expanduser("~")) / probe.lstrip("/\\"),
                        PROJECTS.parent / probe.lstrip("/\\")) + tuple(
                            base / probe.lstrip("/\\")
                            for r in _EXTERNAL_REPOS for base in (r, r.parent)):
                if alt.exists():
                    exists = True
                    break
        found.setdefault(raw, {"kind": "path", "exists": exists})
    return found


def section_span(lines, num):
    """(start, end) of a §-numbered section, or None."""
    start = None
    for i, l in enumerate(lines):
        m = SECTION_RE.match(l)
        if m and m.group(1) == num:
            start = i
        elif start is not None and l.startswith("## "):
            return start, i
    return (start, len(lines)) if start is not None else None


STRUCK_RE = re.compile(r"~~.*?~~", re.S)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
FENCE_RE = re.compile(r"^```.*?^```", re.S | re.M)


def still_asserted(text):
    """The doc with every RETRACTED span removed, so a search hits only live claims.

    Grepping for the claim found all three. Two of those hits were the CORRECTIONS - so the
    output looked like thorough coverage of a handled problem, and the one live copy was
    indistinguishable from the two dead ones. **A partially corrected document reads as
    corrected**, and the more carefully it was corrected the more hits the grep returns.

    Note this cuts the OTHER way too and that is deliberate: a claim quoted inside a fence - the
    way this docstring quotes one - is not an assertion by the document either.
    """
    blank = lambda m: "\n" * m.group(0).count("\n")     # noqa: E731
    text = FENCE_RE.sub(blank, text)
    text = HTML_COMMENT_RE.sub(blank, text)
    return STRUCK_RE.sub(blank, text)


def folded_spans(text):
    """Line ranges inside <details> - content a reader does not see unless they expand it.

    Returned as 1-indexed (start, end) pairs. Nesting is not tracked; the first </details>
    closes, which is correct for this corpus and errs toward reporting MORE lines as folded
    rather than fewer - the safe direction for a warning.
    """
    spans, open_at = [], None
    for i, line in enumerate(text.split("\n"), 1):
        low = line.lower()
        if "<details" in low:
            open_at = i
        elif "</details>" in low and open_at:
            spans.append((open_at, i))
            open_at = None
    if open_at:                      # unclosed fold runs to the end of the document
        spans.append((open_at, len(text.split("\n"))))
    return spans


def cmd_strike(args):
    """Apply the full settled-sub-item form to every done sub-item in a LIVE entry.
    """
    doc = resolve_doc_arg(args.doc)
    if not doc or not doc.exists():
        print("no such doc: %s" % args.doc, file=sys.stderr)
        return 2
    if refuse_if_not_mine(doc, "strike", getattr(args, "not_mine", False)):
        return 2

        # `expect` is the RAW text, so a landing that arrives between this
    # read and the write below refuses instead of being erased.
    _before_txt = doc.read_text(encoding="utf-8")
    lines = _before_txt.split("\n")
    entries, _ = parse_entries(lines)
    out = list(lines)
    changed, ambiguous, spans_only = [], [], []
    _fenced = fenced_lines(lines)

    for i, raw in enumerate(out):
        if STAMP_LINE_RE.match(raw) or NOTE_LINE_RE.match(raw):
            continue
        if i in _fenced:
            continue
        if GREY_SPAN_RE.search(raw):
            fixed = GREY_SPAN_RE.sub("", raw).replace("</span>", "")
            if fixed != raw:
                out[i] = fixed
                spans_only.append(i + 1)

    for e in entries:
        if e.get("archived") or status_of(e["body"]) not in LIVE_STATUS:
            continue
        if e["id"][:1] not in ("D", "T", "W", "A"):
            continue
        for off, raw in enumerate(e["body"].splitlines()[1:]):
            i = e["line"] + off                      # 0-indexed into `out`
            if i >= len(out) or out[i] != raw:
                continue
            if i in _fenced:
                continue
            if NOTE_LINE_RE.match(raw):
                continue
            if not re.match(r"^\s*[-*+]\s", raw):
                continue
            txt = re.sub(r"`[^`]*`", "", raw)
            if NOTDONE_MARK_RE.search(txt):
                continue
            if not DONE_MARK_RE.search(NOTDONE_MARK_RE.sub(" ", txt)):
                continue
            if looks_open(txt):
                ambiguous.append((i + 1, e["id"], why_open(txt), raw))
                continue
            if CHECKED_BOX_RE.search(raw) and not GREY_SPAN_RE.search(raw):
                continue

            m = re.match(r"^(\s*)([-*+]|\d+\.)\s+(?:\[[ xX]\]\s*)?(.*)$", raw)
            if not m:
                continue
            indent, _bullet, body = m.group(1), m.group(2), m.group(3)
            body = GREY_SPAN_RE.sub("", body).replace("</span>", "").strip()
            if body.startswith("~~") and body.endswith("~~"):
                body = body[2:-2].strip()
            # Rebuilt from parts, never patched, so a second run cannot double anything.
            new = "%s- [x] %s" % (indent, body)
            if new != raw:
                out[i] = new
                changed.append((i + 1, e["id"]))

    print("orchdoc strike - %s" % doc.name)
    print("  %d entr(ies) examined" % len(entries))
    if spans_only:
        print("  %d literal <span> tag(s) stripped (they render as visible text here): L%s"
              % (len(spans_only), ", L".join(str(n) for n in spans_only[:8])))

    if ambiguous:
        print()
        print("  ⚠️  %d line(s) REFUSED - each carries a done marker AND an openness signal, so"
              % len(ambiguous))
        print("      only their owner can say. Mark them by hand, or reword the line.")
        for ln, eid, word, raw in ambiguous[:8]:
            print("      L%-5d %-6s says %-12r %s" % (ln, eid, word, raw.strip()[:56]))
        if len(ambiguous) > 8:
            print("      ... and %d more" % (len(ambiguous) - 8))

    if not changed and not spans_only:
        print()
        print("  nothing to convert: every settled sub-item in a live entry already carries the")
        print("  checked-checkbox form.")
        return 0
    if changed:
        print()
        print("  %d sub-item(s) across %d entr(ies) get the full form"
              % (len(changed), len(set(c[1] for c in changed))))
        for ln, eid in changed[:8]:
            print("    L%-5d %-6s %s" % (ln, eid, out[ln - 1].strip()[:74]))
        if len(changed) > 8:
            print("    ... and %d more" % (len(changed) - 8))
    if args.dry_run:
        print("  DRY RUN - nothing written. Re-run with --commit.")
        return 0
    write_doc(doc, "\n".join(out), expect=_before_txt)
    print("  written.")
    return 0


def _reorder_slots(lines, entries):
    """(groups, spans) for the reorder - one group per (section, prefix), in document order.

    A SLOT is where a numbered entry sits: `(start, body_end, end, entry)`, all 0-indexed.
    `start..body_end` is the block that MOVES; `body_end..end` is a pinned TAIL.
    """
    NUM = re.compile(r"^([A-Z]{1,3})(\d+)$")
    spans = []
    for idx, e in enumerate(entries):
        start = e["line"] - 1
        end = entries[idx + 1]["line"] - 1 if idx + 1 < len(entries) else len(lines)
        body_end = end
        fenced = False
        for j in range(start + 1, end):
            if lines[j].lstrip().startswith("```"):
                fenced = not fenced
                continue
            if not fenced and re.match(r"^#{1,2}\s", lines[j]):
                body_end = j
                break
        spans.append((start, body_end, end, e))

    groups = {}
    for s, be, en, e in spans:
        m = NUM.match(e["id"])
        if not m:
            continue                      # a non-canonical id (E-IDSHAPE) is left exactly alone
        groups.setdefault((e.get("section") or "?", m.group(1)), []).append(
            (s, be, en, e, int(m.group(2))))
    return groups, spans


def cmd_reorder(args):
    """Sort entry numbers within each section. Moves whole entries; verifies nothing changed.
    """
    doc = resolve_doc_arg(args.doc)
    if not doc or not doc.exists():
        print("no such doc: %s" % args.doc, file=sys.stderr)
        return 2
    if refuse_if_not_mine(doc, "reorder", getattr(args, "not_mine", False)):
        return 2

        # `expect` is the RAW text, so a landing that arrives between this
    # read and the write below refuses instead of being erased.
    _before_txt = doc.read_text(encoding="utf-8")
    lines = _before_txt.split("\n")
    entries, _ = parse_entries(lines)
    groups, spans = _reorder_slots(lines, entries)

    def _inversions(gs):
        n = 0
        for members in gs.values():
            seq = [m[4] for m in sorted(members)]
            n += sum(1 for a, b in zip(seq, seq[1:]) if b < a)
        return n

    before_inv = _inversions(groups)

    moved, plans, touched = 0, [], 0
    for key, members in sorted(groups.items()):
        members = sorted(members)                      # document order
        if len(members) < 2:
            continue
        blocks = [(m[4], lines[m[0]:m[1]], m[3]["id"]) for m in members]
        ordered = sorted(blocks, key=lambda b: b[0])
        if [b[2] for b in ordered] == [b[2] for b in blocks]:
            continue
        touched += 1
        moved += sum(1 for a, b in zip(blocks, ordered) if a[2] != b[2])
        for m, blk in zip(members, ordered):
            plans.append((m[0], m[1], blk[1]))         # slot start, slot body_end, new block
        if args.dry_run:
            print("  %-34s %s" % (str(key[0])[:34], " ".join(b[2] for b in blocks)))
            print("  %-34s -> %s" % ("", " ".join(b[2] for b in ordered)))

    out = list(lines)
    for start, body_end, blk in sorted(plans, key=lambda p: -p[0]):   # bottom-up
        out[start:body_end] = blk

    if not moved:
        print("orchdoc reorder - %s" % doc.name)
        print("  nothing to permute. %d entr(ies) examined, %d section/prefix group(s)."
              % (len(entries), len(groups)))
        if before_inv:
            print("  ⚠️ %d out-of-order step(s) REMAIN and this command cannot fix them:"
                  % before_inv)
            for key, members in sorted(groups.items()):
                seq = [(m[4], m[3]["id"]) for m in sorted(members)]
                for (na, ia), (nb, ib) in zip(seq, seq[1:]):
                    if nb < na:
                        print("     %-30s %s after %s" % (str(key[0])[:30], ib, ia))
            print("  check --doc %s reports these as E-IDORDER." % doc.name)
            return 2
        print("  every section/prefix group runs in ascending order (measured).")
        return 0

    before_blocks = sorted("\n".join(lines[s:be]) for s, be, _en, _e in spans)
    new_entries, _ = parse_entries(out)
    _new_groups, new_spans = _reorder_slots(out, new_entries)
    after_blocks = sorted("\n".join(out[s:be]) for s, be, _en, _e in new_spans)
    if before_blocks != after_blocks or len(out) != len(lines):
        print("  ⛔ REFUSING - the entry blocks are not identical after the sort.")
        print("     %d before, %d after; %d lines -> %d. Nothing written."
              % (len(before_blocks), len(after_blocks), len(lines), len(out)))
        return 2
    after_inv = _inversions(_new_groups)
    if after_inv:
        # The objective, restated as an oracle. A sort that leaves inversions has not done
        # the job it was called to do, and must not report success.
        print("  ⛔ REFUSING - %d out-of-order step(s) would REMAIN after the sort."
              % after_inv)
        return 2

    print("orchdoc reorder - %s" % doc.name)
    print("  %d entr(ies) would move, across %d section/prefix group(s)" % (moved, touched))
    print("  verified: %d entry blocks byte-identical, %d -> %d out-of-order step(s)"
          % (len(before_blocks), before_inv, after_inv))
    if args.dry_run:
        print("  DRY RUN - nothing written. Re-run with --commit.")
        return 0
    write_doc(doc, "\n".join(out), expect=_before_txt)
    print("  written.")
    return 0


def cmd_asserted(args):
    """Search what a doc still CLAIMS, not what it merely mentions.

    Reports what it EXAMINED - docs scanned, spans removed - because "0 live copies" and
    "the search never ran" must not print the same way.
    """
    try:
        pat = re.compile(args.pattern, re.I)
    except re.error as e:
        # A broken pattern is not "no matches". A failed search must not
        # render as a clean one.
        print("  BAD PATTERN - this is NOT a clean result: %s" % e, file=sys.stderr)
        return 2

    docs = ([resolve_doc_arg(args.doc)] if args.doc
            else sorted(Path(".").glob("ORCHESTRATOR-DECISIONS-o*.md"))
            + sorted(Path(".").glob("*bridge*.md")))
    docs = [d for d in docs if d and d.exists()]
    if not docs:
        print("  no docs matched - nothing was examined", file=sys.stderr)
        return 2

    print("orchdoc asserted - /%s/" % args.pattern)
    print("  examined %d doc(s)" % len(docs))
    print()
    live_total = retracted_total = 0
    for d in docs:
        raw = d.read_text(encoding="utf-8", errors="replace")
        live = still_asserted(raw)
        n_raw = len(pat.findall(raw))
        n_live = len(pat.findall(live))
        retracted = n_raw - n_live
        live_total += n_live
        retracted_total += retracted
        if not n_raw:
            print("  %-34s -" % d.name)
            continue
        flag = "  <- STILL ASSERTED" if n_live else ""
        print("  %-34s live=%-3d retracted/quoted=%-3d%s" % (d.name, n_live, retracted, flag))
        if n_live:
            folded = folded_spans(raw)
            # Line numbers are preserved by still_asserted(), so this indexes the SAME lines as
            # the raw file - a struck span shows up here as blanked, which is the liveness test.
            live_lines = live.split("\n")
            for i, line in enumerate(raw.split("\n"), 1):
                if not pat.search(live_lines[i - 1] if i <= len(live_lines) else ""):
                    continue
                note = ""
                if any(a <= i <= b for a, b in folded):
                    note = " ⚠️ INSIDE A COLLAPSED <details> - invisible when read"
                elif line.lstrip().startswith(">"):
                    note = " ⚠️ IN A BLOCKQUOTE - quotation or callout? read before acting"
                print("       L%-5d %s%s" % (i, line.strip()[:88], note))

    print()
    print("  %d live claim(s); %d already retracted or quoted" % (live_total, retracted_total))
    print("  A plain grep would have reported %d and made no distinction." %
          (live_total + retracted_total))
    return 1 if live_total else 0


def cmd_links(args):
    """Harvest the doc's own assets and propose a §1 table. PROPOSES; does not overwrite."""
    doc = resolve_doc_arg(args.doc)
    if not doc or not doc.exists():
        print("no such doc: %s" % args.doc, file=sys.stderr)
        return 2
    lines = doc.read_text(encoding="utf-8").split("\n")
    assets = harvest_assets(lines, doc)

    span = section_span(lines, "1")
    listed = "\n".join(lines[span[0]:span[1]]) if span else ""
    missing = {k: v for k, v in assets.items() if k not in listed}

    print("orchdoc links - %s" % doc.name)
    print("  %d asset(s) cited in the doc, %d NOT listed in §1"
          % (len(assets), len(missing)))
    if not missing:
        print("  §1 already accounts for everything this doc points at.")
        return 0

    dead = [k for k, v in missing.items() if v["kind"] == "path" and v["exists"] is False]
    for k, v in sorted(missing.items()):
        mark = "  " if v["exists"] is not False else "  \u26a0 DOES NOT EXIST"
        print("    %-62s %s%s" % (k[:62], v["kind"], mark))
    if dead:
        print()
        print("  \u26d4 %d cited path(s) DO NOT EXIST. Those are dead pointers TODAY, and"
              % len(dead))
        print("     listing them in §1 would publish them as navigable. Fix or drop them.")

    print()
    print("  Paste-ready §1 rows (yours to edit - a harvested link is a CANDIDATE, not")
    print("  necessarily an asset you own):")
    print()
    print("| what | where |")
    print("|---|---|")
    for k, v in sorted(missing.items()):
        if v["exists"] is False:
            continue
        label = k.rstrip("/").split("/")[-1].split("\\")[-1] or k
        cell = k if v["kind"] == "url" else "[%s](%s)" % (label, k)
        print("| %s | %s |" % (label[:46], cell))
    return 0





def cmd_review(args):
    """Walk every schema section and state what it holds, what it should, and the question.

    Exit 1 when any section has an unanswered question, so it can gate a report.
    """
    doc = resolve_doc_arg(args.doc)
    if not doc or not doc.exists():
        print("no such doc: %s" % args.doc, file=sys.stderr)
        return 2
    lines = doc.read_text(encoding="utf-8").split("\n")
    entries, _sections = parse_entries(lines)
    have_ids = {e["id"] for e in entries}


    def body_of(num):
        sp = section_span(lines, num)
        if not sp:
            return None
        return [l for l in lines[sp[0] + 1:sp[1]]
                if l.strip() and not re.fullmatch(r"_.*_", l.strip())]

    print("orchdoc review - %s" % doc.name)
    print("  One section at a time. The script gathers; YOU answer. Nothing is auto-filled.")
    print()

    open_h = [e for e in entries if status_of(e["body"]) in PLATE_STATUS
              and not e.get("archived")]
    unanswered = 0

    for num, title, promise in schema_sections():
        body = body_of(num)
        if body is None:
            continue
        n = len(body)
        ids_here = [e["id"] for e in entries
                    if SECTION_RE.match(e["section"] if e["section"].startswith("#")
                                        else "## " + e["section"])
                    and SECTION_RE.match(e["section"] if e["section"].startswith("#")
                                         else "## " + e["section"]).group(1) == num]
        print("\u00a7%-5s %-26s %2d line(s), %d entr(ies)" % (num, title, n, len(ids_here)))
        print("       promises: %s" % promise)

        q = None
        if num == "1" and n == 0:
            q = "run `links --doc %s` - it harvests what this doc already cites" % args.doc
        elif num in ("2.1", "2.2", "2.3") and not ids_here:
            # "empty" and "full of text the parser cannot see" are OPPOSITE problems and
            # calling the second one empty is a false statement to the reader's face.
            if n:
                q = ("%d line(s), 0 parseable entries - so NONE of this reaches the generated"
                     " plate and no invariant runs on it. It is on the human's plate in prose"
                     " and invisible to every check. Give each item an `### <ID> - title`"
                     " heading." % n)
            else:
                q = ("EMPTY. Is that TRUE? An empty plate asserts nothing needs the human. "
                     "If anything does, it belongs here as an entry - not in prose.")
        elif num == "3" and not ids_here and n:
            q = ("has %d line(s) but NO entries - work described in prose or a table is "
                 "invisible to every check. Give each item an `### <ID> - title` heading."
                 % n)
        elif num == "4" and not ids_here:
            q = ("%d line(s) but 0 parseable entries."
                 % n) if n else "EMPTY. Did this workstream learn nothing worth keeping?"
            if n:
                q += (" Your findings are THERE and the tool cannot see any of them - so no"
                      " invariant runs on them. Give each an `### <ID> - title` heading.")
        elif num == "5" and n <= 1:
            q = "no guards. What will this orchestrator refuse to do?"
        elif num.startswith("99") and not ids_here and open_h:
            q = ("nothing archived while %d item(s) are open above. If any are finished, "
                 "`archive` moves them; if none are, say so." % len(open_h))
        if q:
            print("       \u26a0 %s" % q)
            unanswered += 1
        print()

    print("  open items the tool can SEE: %d" % len(open_h))
    print("  sections needing an answer : %d" % unanswered)
    if unanswered:
        print()
        print("  \u26a0 A section is not complete because it is quiet. Every invariant here")
        print("     is triggered BY AN ENTRY, so an EMPTY section generates NO findings -")
        print("     the emptier a doc gets, the quieter the tool gets.")
    return 1 if unanswered else 0


def refresh_subtitles(lines, name):
    """Rewrite each section's one-line subtitle from SCHEMA_SECTIONS.

    Only replaces a line that is ALREADY a subtitle (`_..._` immediately under the heading).
    It never invents one and never touches prose, so a doc that writes its own intro keeps it.
    """
    want = {num: desc.replace("{NAME}", name) for num, _t, desc in schema_sections()}
    out, i = list(lines), 0
    while i < len(out) - 2:
        m = SECTION_RE.match(out[i])
        if m and m.group(1) in want:
            for k in (i + 1, i + 2):
                if k < len(out) and re.fullmatch(r"_.*_", out[k].strip()):
                    out[k] = "_%s_" % want[m.group(1)]
                    break
        i += 1
    return out



def running_orchestrator():
    """Which orchestrator is running, or None if this machine has not said.

    Configured, never guessed. $ORCHDOC_ME wins; a `.orchdoc-me` file beside the docs is the
    persistent form. Returning None disables the ownership guard entirely, which is the point:
    a guard that fires on a machine that never opted in would be switched off within the hour,
    and a guard that gets switched off protects nothing.
    """
    v = (os.environ.get("ORCHDOC_ME") or "").strip()
    # NOT from the shared config: `me` is session-scoped, and every orchestrator reads the
    # same workspace file. A value there would tell all of them they are the same one.
    if not v:
        f = PROJECTS / ".orchdoc-me"
        try:
            v = f.read_text(encoding="utf-8").strip() if f.exists() else ""
        except OSError:
            v = ""
    m = re.fullmatch(r"o\d+", v or "")
    if m:
        return m.group(0)
    return _bound_orchestrator()


def _app_sessions_dir():
    """Where the Claude desktop app keeps its session rows.

    Off Windows there is no such directory at all. That is FINE and deliberate: both callers
    already wrap `rglob` in a try, and a session store that does not exist is the truthful
    answer for an environment that has no desktop app - not an error to raise.
    """
    env = os.environ.get("ORCHDOC_APP_SESSIONS")
    if env:
        return Path(env)
    roaming = os.environ.get("APPDATA")
    base = Path(roaming) if roaming else Path.home() / "AppData" / "Roaming"
    return base / "Claude" / "claude-code-sessions"


APP_SESSIONS = _app_sessions_dir()
_APP_SESSIONS_DEFAULT = APP_SESSIONS


def _app_session_files():
    """Every `local_*.json` session row, from the old store AND the new one.
    """
    roots = [APP_SESSIONS]
    if APP_SESSIONS == _APP_SESSIONS_DEFAULT and not os.environ.get("ORCHDOC_APP_SESSIONS"):
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        try:
            roots.extend(sorted((base / "Claude-Data").glob("claude_*/claude-code-sessions")))
        except OSError:
            pass
    for root in roots:
        if root.is_dir():
            yield from root.rglob("local_*.json")
TITLE_ID = re.compile(r"^[^0-9A-Za-z]*(o\d+)\s*(?:[:\-\u2012-\u2015\u2022|/,]|\s|$)", re.I)

LANE_ID = re.compile(r"^[^0-9A-Za-z]*(o\d+L\d+[a-z]?)\s*(?:[:\-\u2012-\u2015\u2022|/,]|\s|$)",
                     re.I)


def session_role():
    """('lane'|'orchestrator', id) for THIS session from its title, or (None, None).
    """
    title = _session_title()
    if not title:
        return None, None
    m = LANE_ID.match(title)
    if m:
        return "lane", m.group(1).lower()
    m = TITLE_ID.match(title)
    if m:
        return "orchestrator", m.group(1).lower()
    return None, None


def _session_title():
    """This session's title from the desktop app's per-session JSON, or "" when it has none."""
    head = _own_session()
    return ((head or {}).get("title") or "").strip()


_SESSION_HEAD_BYTES = 16384
_SESSION_FIELD_RE = {
    k: re.compile(r'"%s"\s*:\s*("(?:[^"\\]|\\.)*"|true|false|null)' % k)
    for k in ("sessionId", "cliSessionId", "title", "isArchived")}


def session_head(f):
    """{sessionId, cliSessionId, title, isArchived} of ONE app session file, or None.

    Falls back to parsing the whole file when the head does not hold the fields, so a
    change in the app's key order costs time, never a wrong answer.
    """
    try:
        with open(f, "rb") as fh:
            head = fh.read(_SESSION_HEAD_BYTES).decode("utf-8", "replace")
    except OSError:
        return None
    out = {}
    for k, rx in _SESSION_FIELD_RE.items():
        m = rx.search(head)
        if m:
            try:
                out[k] = json.loads(m.group(1))
            except ValueError:
                pass
    if "title" in out and ("sessionId" in out or "cliSessionId" in out):
        return out
    try:
        d = json.loads(pathlib.Path(f).read_text(encoding="utf-8"))
    except Exception:
        return None
    return {k: d.get(k) for k in _SESSION_FIELD_RE}


_OWN_SESSION_CACHE = _shared_state.home_state_dir() / "orchdoc"   # $CLAUDE_HOME_STATE_DIR moves it
_own_session_memo = {}


def _own_session():
    """THIS session's head fields (see `session_head`), or None when it cannot be found.
    """
    sid = ""
    for key in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID", "CLAUDE_CODE_HOST_SESSION_ID"):
        val = os.environ.get(key)
        if val:
            sid = "".join(c for c in val if c.isalnum() or c in "-_")[:80]
            break
    if not sid:
        return None
    want = sid.replace("local_", "")

    def ours(f):
        h = session_head(f)
        if h and want in (str(h.get("cliSessionId") or ""),
                          str(h.get("sessionId") or "").replace("local_", "")):
            return h
        return None

    cache = _OWN_SESSION_CACHE / ("session-%s.json" % want)
    cached = _own_session_memo.get(want) or ""
    if not cached:
        try:
            cached = json.loads(cache.read_text(encoding="utf-8")).get("path") or ""
        except Exception:
            cached = ""       # a bad cache is a slow call, never a wrong answer
    if cached and Path(cached).is_file():
        h = ours(cached)
        if h is not None:
            _own_session_memo[want] = cached
            return h
    try:
        for f in _app_session_files():
            h = ours(f)
            if h is None:
                continue
            _own_session_memo[want] = str(f)
            try:
                _OWN_SESSION_CACHE.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps({"path": str(f)}), encoding="utf-8")
            except Exception:
                pass          # failing to cache costs a slow call, nothing else
            return h
    except Exception:
        pass
    return None


def _bound_orchestrator():
    """Which orchestrator this session IS, read from its NAME. Static; nothing is learned.
    """
    head = _own_session()
    if head is None:
        return None
    m = TITLE_ID.match((head.get("title") or "").strip())
    return m.group(1).lower() if m else None


def doc_owner_id(doc):
    """The orchestrator id a doc's FILENAME declares, or None."""
    m = re.search(r"ORCHESTRATOR-DECISIONS-(o\d+)\.md$", str(doc), re.I)
    return m.group(1).lower() if m else None


def refuse_if_not_mine(doc, verb, allowed):
    """True when the caller must stop. Prints the refusal and the exact way through.

    Fires only when BOTH ids are known and they differ - never on an unconfigured machine and
    never on a file whose name does not declare an owner. A guard that guesses is a guard that
    gets overridden.
    """
    if allowed:
        return False
    me, owner = running_orchestrator(), doc_owner_id(doc)
    if not me or not owner or me == owner:
        return False
    print("  [REFUSE] %s WRITES, and %s belongs to %s, not %s."
          % (verb, doc.name, owner, me))
    print("           Nothing was written.")
    print()
    print("           An OrchDoc is a live document its owner is writing to right now. A")
    print("           regenerated index or a rewritten header lands in THEIR file while they")
    print("           work, and they will not know it came from here.")
    print()
    print("           If %s has agreed to this, pass --not-mine. The flag is one word; it" % owner)
    print("           exists so a deliberate cross-doc edit is distinguishable from an")
    print("           accidental one afterwards, in the shell history.")
    return True


def cmd_scaffold(args):
    """Write, or repair, the canonical spine.
    """
    doc = resolve_doc_arg(args.doc)
    if not doc:
        print("no such doc: %s" % args.doc, file=sys.stderr)
        return 2
    if refuse_if_not_mine(doc, "scaffold", getattr(args, "not_mine", False)):
        return 1
    _scaffold_before = doc.read_text(encoding="utf-8") if doc.exists() else None
    lines = _scaffold_before.split("\n") if _scaffold_before is not None else ["# " + doc.stem]

    if (getattr(args, "adopt", False) or getattr(args, "reorder", False)) \
            and not args.dry_run and not args.force:
        print("REFUSING: --adopt rewrites section headings in %s." % doc.name,
              file=sys.stderr)
        print("Run it with --dry-run first, show the owner the mapping, and pass --force",
              file=sys.stderr)
        print("once they agree. A decision record is not migrated behind its author.",
              file=sys.stderr)
        return 2

    # ---- TITLE: the identity line, repaired in place, role preserved ----
    tnum = re.search(r"ORCHESTRATOR-DECISIONS-(o\d+)", doc.name)
    if tnum:
        tnum = tnum.group(1)
        hi = next((i for i, l in enumerate(lines[:40]) if l.startswith("# ")), None)
        if hi is None:
            lines.insert(0, canonical_title(tnum))
        else:
            tm = TITLE_RE.match(lines[hi])
            # Keep whatever the author wrote after the identity - that is their role
            # description and the tool has no business naming what they are for.
            role = (tm.group(2) if tm else "").strip()
            role = re.sub(r"^<br\s*/?>\s*", "", role, flags=re.I).strip()

            _ts, _te = title_span(lines)
            if not role and _te > hi:
                # A legacy SECOND H1 line, "# (the role)". Fold it back into the single
                # heading; title_span still spans it so the replacement below removes it.
                role = lines[_te].lstrip("#").strip()

            # REPAIR the earlier paragraph form, whose "(role)" got stranded after the
            # generated index. Absorb it and delete the orphan rather than leaving a human
            # to hunt a stray line through every document - a manual migration step is
            # one that does not happen.
            if not role:
                _isp, _ = index_span(lines)
                _fsp, _ = findex_span(lines)
                _msp, _ = marker_span(lines, META_BEGIN_TOKEN, META_END_TOKEN, "meta")
                for j in range(_te + 1, min(len(lines), _te + 200)):
                    if any(sp and sp[0] <= j <= sp[1] for sp in (_isp, _fsp, _msp)):
                        continue
                    t = lines[j].strip()
                    if not t or t.startswith("<!--"):
                        continue
                    if t.startswith("(") and t.endswith(")") and len(t) < 120:
                        role = t
                        del lines[j]
                        break
                    if t.startswith("#") or t.startswith("**") or t.startswith("- ") \
                            or t.startswith(">") or t.startswith("|"):
                        break   # real content reached; there is no orphan to absorb

            fixed = canonical_title(tnum, role)
            _ts, _te = title_span(lines)
            if "\n".join(lines[_ts:_te + 1]) != fixed:
                print("title: %s" % fixed.replace("\n", " / "))
                lines[_ts:_te + 1] = fixed.split("\n")

    # ---- ADOPT: rename existing headings into their schema slot, in place ----
    renames, unmatched = [], []
    if getattr(args, "adopt", False):
        taken = set(m.group(1) for m in
                    (SECTION_RE.match(l) for l in lines) if m)
        _fz_adopt = fenced_lines(lines)
        for i, ln in enumerate(lines):
            if i in _fz_adopt or not ln.startswith("## ") or SECTION_RE.match(ln):
                continue
            num = adopt_number(ln)
            if num is None:
                unmatched.append((i, ln.lstrip("#").strip()[:70]))
                continue
            if num in taken:
                # Two headings claiming one slot. Renaming both would silently merge
                # sections that their author kept apart; the second is left for a human.
                unmatched.append((i, ln.lstrip("#").strip()[:70] + "  (\u00a7%s taken)" % num))
                continue
            taken.add(num)
            title = dict((n, t) for n, t, _d in schema_sections())[num]
            depth = "##"   # flat, so every section stays independently movable
            orig = ln.lstrip("#").strip()
            renames.append((num, orig[:64]))
            # The original wording is KEPT as a trailing note: it carries the author's
            # scoping ("ACTIVE only", a date, a lane name) that the schema title drops.
            # Drop the original wording when it merely repeats the schema title -
            # "\u00a72 LIVE ON THE HUMAN'S PLATE - ON THE HUMAN'S PLATE" is noise. Keep it whenever it
            # carries scoping the schema title loses: "(ACTIVE only)", a date, a lane name.
            keep = orig.strip().strip("*_").upper()
            same = keep in title.upper() or title.upper() in keep
            lines[i] = ("%s \u00a7%s %s" % (depth, num, title) if same
                        else "%s \u00a7%s %s - %s" % (depth, num, title, orig))

    present = set()
    _fz_present = fenced_lines(lines)
    for i, ln in enumerate(lines):
        m = None if i in _fz_present else SECTION_RE.match(ln)
        if m:
            present.add(m.group(1))

    added = []
    for num, title, note in schema_sections():
        if num in present:
            continue
        # ALL schema sections are top-level "##". The section NUMBER carries the
        # hierarchy and the generated index renders the nesting, so heading depth adds
        # nothing - while demoting 2.1 to "###" made it a CHILD of whichever block
        # preceded it, putting it out of reach of --reorder and leaving the spine
        # permanently unsortable. Flat headings keep every section independently movable.
        lines += ["", "## \u00a7%s %s" % (num, title), "", "_%s_" % note]
        added.append(num)

    # ---- REORDER: numeric order, refused outright if a single line would move out ----
    reordered = False
    if getattr(args, "reorder", False):
        try:
            new_lines = reorder_sections(lines)
        except ValueError as e:
            print("REFUSING: %s" % e, file=sys.stderr)
            return 2
        reordered = new_lines != lines
        lines = new_lines

    # ---- PURPOSE (authored) + META (generated), between the title and the index ----
    _, hi_t = title_span(lines)          # END of the title block, not its first line
    hi_t = 0 if hi_t is None else hi_t
    if not any(re.match(r"^##\s+\**Purpose", l, re.I) for l in lines[:60]):
        # Created EMPTY on purpose. A generated purpose statement would be a rubber stamp
        # in exactly the sense E-RUBBERSTAMP rejects - text that satisfies a checker while
        # carrying no thought. Only the orchestrator can say what it is for.
        lines = (lines[:hi_t + 1]
                 + ["", "## Purpose", "",
                    "_TODO: one paragraph - what this orchestrator is for. "
                    "Authored, never generated._"]
                 + lines[hi_t + 1:])

    mspan, merr = marker_span(lines, META_BEGIN_TOKEN, META_END_TOKEN, "meta")
    if merr:
        print("REFUSING: %s" % merr, file=sys.stderr)
        return 2
    meta = render_meta(doc, lines)
    if mspan:
        lines = lines[:mspan[0]] + meta + lines[mspan[1] + 1:]
    else:
        pi = next((i for i, l in enumerate(lines[:80])
                   if re.match(r"^##\s+\**Purpose", l, re.I)), hi_t)
        nx = next((j for j in range(pi + 1, min(len(lines), pi + 40))
                   if lines[j].startswith("## ") or lines[j].startswith("<!--")), pi + 1)
        lines = lines[:nx] + meta + [""] + lines[nx:]

    span, err = index_span(lines)
    if err:
        print("REFUSING: %s" % err, file=sys.stderr)
        return 2
    idx = render_index(lines)
    if span:
        lines = lines[:span[0]] + idx + lines[span[1] + 1:]
    else:
        _, _te = title_span(lines)       # after the WHOLE title, never mid-heading
        at = 1 if _te is None else _te + 1
        lines = lines[:at] + [""] + idx + lines[at:]

    # ---- the FINDINGS index, at the head of section 4 where its reader stands ----
    fspan, ferr = findex_span(lines)
    if ferr:
        print("REFUSING: %s" % ferr, file=sys.stderr)
        return 2
    fidx = render_findings_index(lines)
    if fspan:
        lines = lines[:fspan[0]] + fidx + lines[fspan[1] + 1:]
    elif fidx:
        _fz_h = fenced_lines(lines)
        h = next((i for i, l in enumerate(lines)
                  if i not in _fz_h and l.startswith("## ")
                  and re.match(r"^##\s*\u00a74\b", l)), None)
        if h is not None:
            lines = lines[:h + 1] + [""] + fidx + lines[h + 1:]

    nfind = sum(1 for l in fidx if re.match(r"^- `F\d+`", l))
    if renames or unmatched:
        print("adopt pass:")
        for num, orig in renames:
            print("  \u00a7%-4s <- %s" % (num, orig))
        for _i, orig in unmatched:
            print("  %-5s    %s" % ("?", orig))
        if unmatched:
            print("  (%d heading(s) NOT renamed - no confident mapping. Left untouched"
                  % len(unmatched))
            print("   on purpose: a wrong rename hides an entry from the human while looking")
            print("   tidier than before.)")
        print()
    if args.dry_run:
        print("would add %d section(s): %s" % (len(added), ", ".join(added) or "none"))
        print("would regenerate the index (%d findings listed)" % nfind)
        return 0

    lines = refresh_subtitles(lines, human_name())

    # `expect=` the text this function read at the top. scaffold renames headings, inserts
    # sections and refreshes subtitles across the WHOLE document, so a landing arriving in
    # between is erased rather than merged. None on a brand-new doc - there is nothing to race.
    write_doc(doc, "\n".join(lines), expect=_scaffold_before)
    print("scaffold: %s" % doc.name)
    if reordered:
        print("  sections REORDERED into numeric order (content verified identical)")
    print("  added %d section(s): %s" % (len(added), ", ".join(added) or "none"))
    print("  index regenerated, %d findings listed" % nfind)
    if added and not reordered:
        print()
        print("  Sections were APPENDED, not sorted. Moving entries under them is yours:")
        print("  only you know which entry belongs where, and a tool that guessed would")
        print("  be rewriting your doc. Then: orchdoc.py check --doc %s" % args.doc)
    return 0


def cmd_priority(args):
    """Propose that the human look at an entry first. Writes the PROPOSED form, always.
    """
    import priority as prio
    doc = resolve_doc_arg(args.doc)
    if not doc or not doc.exists():
        print("no such doc: %s" % doc, file=sys.stderr)
        return 2
    if refuse_if_not_mine(doc, "priority", getattr(args, "not_mine", False)):
        return 1
    _m = re.match(r"o\d+", actor_for(doc))
    if not _m:
        print("  [REFUSE] cannot tell which orchestrator is proposing. Set $ORCHDOC_ME (for "
              "example o99) and run it again.", file=sys.stderr)
        return 1
    me = _m.group(0)
    tier_arg = getattr(args, "tier", None) or "NOW"
    tier = prio.choice_norm(tier_arg)
    if tier not in prio.PROPOSE_TIERS:
        print("  [REFUSE] %r is not a tier to propose. Use one of: %s"
              % (tier_arg, ", ".join(prio.PROPOSE_TIERS)), file=sys.stderr)
        return 1
    why = " ".join((getattr(args, "why", None) or "").split())
    if getattr(args, "tier", None) and not why:
        print("  [REFUSE] --tier needs --why: the line carries the reason the human reads.",
              file=sys.stderr)
        return 1
    new_line = "**Priority:** %s (proposed by %s)%s" % (tier, me, (" - " + why) if why else "")
    with _lock(doc):
        text = doc.read_text(encoding="utf-8")
        lines = text.splitlines()
        entries, _ = parse_entries(lines)
        hit = next((e for e in entries if e["id"] == args.id), None)
        if hit is None:
            print("  [REFUSE] no entry %s in %s" % (args.id, doc.name), file=sys.stderr)
            return 1
        # This entry's own lines: from its heading to the next heading.
        start = hit["line"] - 1
        end = next((j for j in range(start + 1, len(lines)) if re.match(r"#{1,6} ", lines[j])),
                   len(lines))
        existing = next((j for j in range(start, end)
                         if lines[j].startswith("**Priority:**")), None)
        if existing is not None:
            if "(proposed" not in lines[existing].lower():
                print("  [REFUSE] %s already carries the human's own `%s`. That mark outranks a "
                      "proposal and this would replace it." % (args.id, lines[existing].strip()),
                      file=sys.stderr)
                return 1
            if lines[existing].rstrip() == new_line:
                print("  %s already carries `%s` - nothing to do." % (args.id, new_line))
                return 0
            out = lines[:existing] + [new_line] + lines[existing + 1:]
        else:
            # Insert directly beneath the Status line, where every other field of this entry
            # lives, so a reader finds it in the one place fields are.
            at = next((j for j in range(start, end) if lines[j].startswith("**Status:**")), None)
            if at is None:
                print("  [REFUSE] %s has no `**Status:**` line to attach to" % args.id,
                      file=sys.stderr)
                return 1
            out = lines[:at + 1] + [new_line] + lines[at + 1:]
        if args.dry_run:
            print("  DRY RUN - would write `%s` to %s" % (new_line, args.id))
            return 0
        write_doc(doc, "\n".join(out) + "\n", expect=text)
    print("  %s proposed as %s by %s. The human's own `**Priority:**` choice outranks it."
          % (args.id, tier, me))
    print("  NEXT: orchdoc.py plate --doc %s" % args.doc)
    return 0


def cmd_plate(args):
    """
    REGENERATE the human-facing index from the entries.

    A hand-maintained index IS a second copy of the truth. So the index is derived, and
    a siloed update becomes impossible because there is only one thing to write.
    """
    doc = resolve_doc_arg(args.doc)
    if not doc or not doc.exists():
        print("no such doc: %s" % doc, file=sys.stderr)
        return 2
    if refuse_if_not_mine(doc, "plate", getattr(args, "not_mine", False)):
        return 1
    with _lock(doc):
        text = doc.read_text(encoding="utf-8")
        lines = text.splitlines()
        entries, _ = parse_entries(lines)
        block = build_plate_block(entries, lines)
        # Count from the SAME rows the block renders. The old predicate
        # matched the pre-grouping format and silently reported "0 open"
        # while the block itself said 1 - a wrong number from the tool
        # built to stop wrong numbers, cosmetic or not.
        rows = [b for b in block if b.startswith("| **[")]

        span, why = plate_span(lines)
        if why:
            print("  [REFUSE] %s - fix the markers before regenerating." % why,
                  file=sys.stderr)
            return 1
        if span:
            start, stop = span

            old_rows = [l for l in lines[start:stop + 1] if l.startswith("| **[")]
            if old_rows and not rows and not getattr(args, "force", False):
                print("  [REFUSE] the generated index would be EMPTY, replacing a "
                      "hand-maintained one that lists %d item(s)." % len(old_rows),
                      file=sys.stderr)
                print("           Nothing was written. This doc's entries are not yet in "
                      "a form `plate` can read:", file=sys.stderr)
                print("           entries need a `### <ID> - title` heading AND a "
                      "`**Status:**` field.", file=sys.stderr)
                print("           An index that is honestly stale beats one that is "
                      "confidently wrong.", file=sys.stderr)
                print("           Re-run with --force only if the empty index is correct.",
                      file=sys.stderr)
                return 1

            out = lines[:start] + block + lines[stop + 1:]
        else:
            anchor = 0
            _fz_a = fenced_lines(lines)
            for i, ln in enumerate(lines):
                if i not in _fz_a and re.match(r"^#{1,2}\s", ln) and i > 0:
                    anchor = i
                    break
            out = lines[:anchor] + block + [""] + lines[anchor:]
            print("[NOTE] no plate markers found; inserted a generated block before line %d"
                  % (anchor + 1))

        if getattr(args, "dry_run", False):
            print("[PLATE] DRY RUN - nothing written. %s would hold %d open item(s):"
                  % (doc.name, len(rows)))
            for r in rows:
                print("        %s" % r[:74])
            return 0

        write_doc(doc, "\n".join(out) + "\n")

    print("[PLATE] %s regenerated from entries: %d open" % (doc.name, len(rows)))
    for r in rows:
        print("        %s" % r[:74])

    _ids = []
    for r in rows:
        m = re.search(r"\*\*\[([A-Za-z]+\d+[a-z]?)\]", r)
        if m:
            _ids.append(m.group(1))
    _links = [x for x in ((i, reader_link(doc.name, i)) for i in _ids) if x[1]]
    if _links:
        print()
        print("PASTE THIS AS THE HUMAN'S PLATE (clickable - the reader serves this exact hash):")
        print("  **Your plate:** " + " \u00b7 ".join("[%s](%s)" % (i, u) for i, u in _links))
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="Deterministic gate for orchestrator decision docs.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="validate invariants; exits non-zero on violation")
    c.add_argument("--doc", help="one doc (default: every OrchDoc in the workspace root)")
    c.add_argument("--quiet", action="store_true", help="suppress per-doc OK lines")
    c.add_argument("--strict", action="store_true",
                   help="promote advisory findings to blocking")
    c.add_argument("--codes", help="comma-separated codes, e.g. E-STALEPROSE,E-IDORDER: report "
                                   "only these, and skip the slow checks that cannot emit them "
                                   "(the blanket-override hook uses it)")
    c.set_defaults(func=cmd_check)

    f = sub.add_parser("freshness",
                       help="is the working-tree copy equal to the canonical ref?")
    f.add_argument("--doc")
    f.set_defaults(func=cmd_freshness)

    sub.add_parser("sessions",
                   help="which OrchDocs have a session naming them - the alarm for a rename "
                        "that makes a session invisible to the Stop hook"
                   ).set_defaults(func=cmd_sessions)
    rg = sub.add_parser("registry",
                        help="scope conflicts in ORCHESTRATOR-REGISTRY.md - two rows claiming "
                             "one repo, and terminal rows pointing nowhere")
    rg.add_argument("--strict", action="store_true",
                    help="also fail when a declared product repo is named in no row")
    rg.set_defaults(func=cmd_registry)
    w = sub.add_parser("whoami",
                       help="this session's send_message id, with the refusal oracle")
    w.set_defaults(func=cmd_whoami)

    a = sub.add_parser("add", help="capture a decision/finding as a stub, print its anchor")
    a.add_argument("title", help="one line; enrich later")
    a.add_argument("--doc", required=True, help="o97, or a filename, or a path")
    a.add_argument("--kind", default="decision",
                   choices=sorted(KIND_SECTION), help="default: decision")
    a.add_argument("--owner", help="default: the human's name for decision, question, todo and "
                                   "today; orchestrator for the others")
    a.add_argument("--prefix", help="override the id prefix (default: by --kind; the number is "
                                    "always the doc's highest existing id for that prefix, +1)")
    a.add_argument("--id", help="force a specific id (normally auto-allocated)")
    a.add_argument("--date", help="override the date")
    a.add_argument("--allow-stale", action="store_true",
                   help="capture even though this tree is behind the canonical ref - the "
                        "id may already belong to someone else, so verify it before "
                        "writing it anywhere")
    a.add_argument("--no-fetch", action="store_true",
                   help="skip the staleness fetch (offline). The remote-tracking ref on "
                        "disk may itself be old, and the output says so")
    a.set_defaults(func=cmd_add)
    a.add_argument("--not-mine", action="store_true",
                    help="this doc belongs to another orchestrator and they have agreed to the edit")

    nt = sub.add_parser("note",
                        help="[WRITES] add, edit or delete a note THE HUMAN wrote on an "
                             "entry - the OrchDoc reader's Save button calls this")
    nt.add_argument("--doc", required=True)
    nt.add_argument("--id", help="the entry the note belongs to (required to ADD one)")
    nt.add_argument("--note", help="an existing note id (n3) - to edit or delete it")
    nt.add_argument("--text", help="the note. Newlines become spaces: one note is one line")
    nt.add_argument("--delete", action="store_true",
                    help="remove the note named by --note. With --id W<N> and no --note, "
                         "delete a whole instruction item (one `instruct` made): it is kept "
                         "in §99 as \"Deleted with no action\", with a note saying so")
    nt.add_argument("--unseen", action="store_true",
                    help="with --list: only notes nobody has acknowledged. "
                         "Listing does NOT acknowledge them - --ack does.")
    nt.add_argument("--show", metavar="NID",
                    help="print the text of the note NID (W9902.n1) exactly as it was typed - real "
                         "line breaks, blank lines, list markers and indentation, nothing else. "
                         "Use it to copy website or script text; the file holds it escaped, "
                         "`--list` and the Stop hook indent each line. From a worktree it also "
                         "reads the main checkout's copy of the doc.")
    nt.add_argument("--list", dest="list_only", action="store_true",
                    help="read them instead of writing one; --id narrows to one entry")
    nt.add_argument("--by", help="who wrote it; defaults to this install's human")
    nt.add_argument("--no-land", dest="no_land", action="store_true",
                    help="write the note locally and do NOT put it on the canonical ref. "
                         "The default LANDS it, because a note only an orchestrator's own "
                         "checkout can see is one every other session is blind to.")
    nt.add_argument("--ack", action="store_true",
                    help="mark the instruction named by --note as SEEN, so the Stop hook "
                         "stops surfacing it. Seen, not done - it changes nothing the human "
                         "wrote, and what you DO about it still belongs in the entry.")
    nt.add_argument("--kind", choices=[k.lower() for k in NOTE_KINDS],
                    help="note (default) or instruction. An INSTRUCTION is recorded the "
                         "same way and reads as a directive rather than a remark, and "
                         "the Stop hook holds it in front of that orchestrator until "
                         "acknowledged. Editing keeps the kind it has.")
    nt.add_argument("--at", help="the visible timestamp; defaults to now")
    nt.add_argument("--json", action="store_true",
                    help="machine-readable result, for the reader's server")
    nt.add_argument("--queue-landing", dest="queue_landing", action="store_true",
                    help="write it, then return: the Reader's landing worker puts it on the "
                         "canonical ref in the background . --no-land wins over this")
    nt.add_argument("--client-id", dest="client_id",
                    help="the Reader page's id for this save: the same id with the same request "
                         "again writes nothing and prints the first answer ")
    nt.set_defaults(func=cmd_note)

    ins = sub.add_parser("instruct",
                         help="the human's instruction for the WHOLE doc: a new §3 W item carrying it "
                              "as an INSTRUCTION note, which the Stop hook holds until acked")
    ins.add_argument("--doc", required=True)
    ins.add_argument("--text", required=True,
                     help="the instruction. Newlines become spaces")
    ins.add_argument("--by", help="who wrote it; defaults to this install's human")
    ins.add_argument("--at", help="override the timestamp")
    ins.add_argument("--no-land", action="store_true",
                     help="write it locally and do NOT put it on the canonical ref")
    ins.add_argument("--queue-landing", dest="queue_landing", action="store_true",
                     help="write it, then return: the Reader's landing worker lands it")
    ins.add_argument("--client-id", dest="client_id",
                     help="the Reader page's id for this save; a repeat writes nothing")
    ins.add_argument("--json", action="store_true")
    ins.set_defaults(func=cmd_instruct)

    td = sub.add_parser("today",
                        help="§2.0 Do today: add an item (one-off or repeating), tick it off "
                             "for a day, untick it, or stop a routine")
    td.add_argument("--doc", required=True)
    td.add_argument("--add", metavar="TEXT", help="a new item")
    td.add_argument("--repeat", default="no",
                    help="no | daily | every N days | weekly | weekly on <day> | "
                         "monthly on <1-31>  (with --add)")
    td.add_argument("--due", help="YYYY-MM-DD for a one-off (default today)")
    td.add_argument("--done", metavar="ID", help="tick an item off for --date (default today)")
    td.add_argument("--undo", metavar="ID", help="untick it for --date")
    td.add_argument("--stop", metavar="ID", help="end a routine: moves it to §99")
    td.add_argument("--priority", metavar="ID",
                    help="set the human's priority on any entry (with --choice); written as "
                         "`**Priority:** <choice>`. Don't do archives it to its own §99 subsection")
    td.add_argument("--choice", help="NOW | High | Medium | Low | Hold for Review | Don't do")
    td.add_argument("--date", help="YYYY-MM-DD (default today)")
    td.add_argument("--by", help="who; defaults to this install's human")
    td.add_argument("--no-land", action="store_true")
    td.add_argument("--queue-landing", dest="queue_landing", action="store_true",
                    help="write it, then return: the Reader's landing worker lands it")
    td.add_argument("--client-id", dest="client_id",
                    help="the Reader page's id for this change; a repeat changes nothing")
    td.add_argument("--json", action="store_true")
    td.set_defaults(func=cmd_today)

    lj = sub.add_parser("land-job",
                        help="[WRITES] land one change from the OrchDoc Reader's landing queue "
                             "(.shared/state/reader-landing) - the Reader's worker runs this")
    lj.add_argument("--job", required=True, help="the job file")
    lj.add_argument("--json", action="store_true", help="accepted; the result is always JSON")
    lj.set_defaults(func=cmd_land_job)

    rs = sub.add_parser("restamp",
                        help="record a re-review on ONE entry, with the attestation bar "
                             "applied before the write")
    rs.add_argument("id")
    rs.add_argument("--doc", required=True)
    rs.add_argument("--because", required=True,
                    help="what MOVED and why this entry survives it - the bar is checked "
                         "here, not later")
    rs.add_argument("--by", default=None, help="defaults to this doc's owner")
    rs.add_argument("--at", default=None, help="ISO timestamp; defaults to now")
    rs.add_argument("--not-mine", action="store_true")
    rs.set_defaults(func=cmd_restamp)

    r = sub.add_parser("resolve", help="flip an entry's Status IN PLACE - writes the doc "
                                       "immediately, then re-reads it to verify")
    r.add_argument("id")
    r.add_argument("--doc", required=True)
    r.add_argument("--ruling", required=True, help="what was decided, and by whom")
    r.add_argument("--status", default="RESOLVED",
                   help="RESOLVED (default), SUPERSEDED, DEFERRED, PARKED")
    r.add_argument("--depends", default=None,
                   help="what this ruling RESTS ON . Required by "
                        "E-NODEPS: a ruling with no declared edge can never go stale, "
                        "so nothing can ever tell you it needs revisiting.")
    r.add_argument("--adopt", action="store_true",
                   help="legacy entry with no Status field: insert one instead of refusing")
    r.add_argument("--commit", dest="dry_run", action="store_false", default=True,
                   help="accepted and ignored: resolve always writes (there is no dry run)")
    r.set_defaults(func=cmd_resolve)
    r.add_argument("--not-mine", action="store_true",
                    help="this doc belongs to another orchestrator and they have agreed to the edit")
    r.add_argument("--json", action="store_true",
                   help="the Reader's mode: one change, journalled, answered as one JSON line")
    r.add_argument("--queue-landing", dest="queue_landing", action="store_true",
                   help="write it, then return: the Reader's landing worker lands it")
    r.add_argument("--no-land", action="store_true")
    r.add_argument("--client-id", dest="client_id",
                   help="the Reader page's id for this change; a repeat changes nothing")
    r.add_argument("--undo-json", dest="undo_json",
                   help="the Reader's Undo: the `undo` a first call returned")

    mg = sub.add_parser("migrate",
                        help="bring a LEGACY doc to where the other commands work")
    mg.add_argument("--doc", required=True)
    mg.add_argument("--owner", default="orchestrator")
    mg.add_argument("--date", help="value for Opened; default 'unknown'")
    mg.add_argument("--commit", dest="dry_run", action="store_false", default=True,
                    help="actually write (default is a dry run)")
    mg.set_defaults(func=cmd_migrate)

    hf = sub.add_parser("handoff",
                        help="before freezing a doc, verify every OPEN item exists in a "
                             "LIVING doc. Use at consolidation, retirement or dormancy.")
    hf.add_argument("--doc", required=True)
    hf.add_argument("--dormant", action="store_true",
                    help="intent flag: this doc is about to be marked dormant")
    hf.set_defaults(func=cmd_handoff)

    rv = sub.add_parser("review",
                        help="walk EVERY section one at a time and force the completeness "
                             "question - an empty section generates no findings")
    rv.add_argument("--doc", required=True)
    rv.set_defaults(func=cmd_review)

    st = sub.add_parser("strike",
                        help="apply the full settled-sub-item form (checkbox + grey + strike) "
                             "to done sub-items inside LIVE entries")
    st.add_argument("--doc", required=True)
    st.add_argument("--commit", dest="dry_run", action="store_false", default=True)
    st.add_argument("--not-mine", action="store_true")
    st.set_defaults(func=cmd_strike)

    pr = sub.add_parser("priority",
                        help="propose that the human look at an entry first - always writes the "
                             "PROPOSED form; the human's bare mark outranks it")
    pr.add_argument("id")
    pr.add_argument("--doc", required=True)
    pr.add_argument("--commit", dest="dry_run", action="store_false", default=True)
    pr.add_argument("--not-mine", action="store_true")
    pr.add_argument("--tier", metavar="TIER",
                    help="High, Medium, Low, \"Don't do\" or NOW (default NOW). Written as "
                         "`**Priority:** <tier> (proposed by oN) - <why>`; never a bare NOW")
    pr.add_argument("--why", metavar="TEXT", help="the reason; required with --tier")
    pr.set_defaults(func=cmd_priority)

    ro = sub.add_parser("reorder",
                        help="sort entry numbers within each section so a reader can stop "
                             "when they arrive instead of scanning the whole section")
    ro.add_argument("--doc", required=True)
    ro.add_argument("--commit", dest="dry_run", action="store_false", default=True)
    ro.add_argument("--not-mine", action="store_true",
                    help="this doc belongs to another orchestrator and they have agreed")
    ro.set_defaults(func=cmd_reorder)

    asrt = sub.add_parser("asserted",
                          help="search what a doc still CLAIMS - strikes, comments and fenced "
                               "quotes removed first, because a corrected doc greps like an "
                               "uncorrected one")
    asrt.add_argument("pattern", help="a regex; matched case-insensitively")
    asrt.add_argument("--doc", help="one doc; default is every OrchDoc plus the bridges")
    asrt.set_defaults(func=cmd_asserted)

    lk = sub.add_parser("links",
                        help="harvest every asset the doc cites and propose a §1 table - "
                             "§1 is a COLLECTION task, not an authoring one")
    lk.add_argument("--doc", required=True)
    lk.set_defaults(func=cmd_links)

    rm = sub.add_parser("refresh-meta",
                        help="re-derive the meta block's dates from the commit log")
    rm.add_argument("--doc", required=True)
    rm.set_defaults(func=cmd_refresh_meta)

    sc = sub.add_parser("scaffold", help="write/repair the canonical section spine")
    sc.add_argument("--doc", required=True)
    sc.add_argument("--dry-run", action="store_true")
    sc.add_argument("--adopt", action="store_true",
                    help="rename existing headings into their schema slot, in place")
    sc.add_argument("--reorder", action="store_true",
                    help="sort numbered sections into order (content-preserving)")
    sc.add_argument("--force", action="store_true",
                    help="required to WRITE an --adopt pass (owner must have agreed)")
    sc.set_defaults(func=cmd_scaffold)
    sc.add_argument("--not-mine", action="store_true",
                    help="this doc belongs to another orchestrator and they have agreed to the edit")

    p = sub.add_parser("plate",
                       help="[WRITES] REGENERATE the human-facing index from the entries")
    p.add_argument("--dry-run", action="store_true",
                   help="print what would change and write NOTHING. `check` and `review` are"
                        " read-only; this one is not, and the name does not say so.")
    p.add_argument("--doc", required=True)
    p.add_argument("--force", action="store_true",
                   help="allow replacing a populated index with an EMPTY one. Refused by "
                        "default: an empty index asserts 'nothing here', and overwriting "
                        "a curated one with it is the 'None open while items are open' "
                        "failure this tool exists to prevent.")
    p.set_defaults(func=cmd_plate)
    p.add_argument("--not-mine", action="store_true",
                    help="this doc belongs to another orchestrator and they have agreed to the edit")

    cm = sub.add_parser("commit",
                        help="land ONE OrchDoc on main safely from the dirty shared tree")
    cm.add_argument("--doc", required=True)
    cm.add_argument("--message", "-m", help="commit subject")
    cm.add_argument("--also", action="append", metavar="PATH",
                    help="additional file to land in the same commit; repeatable "
                         "(a session landed 4: its OrchDoc plus 3 deliverables)")
    cm.add_argument("--override", metavar="CODE[,CODE...]",
                    help="proceed despite these invariants, RECORDING who/what/why")
    cm.add_argument("--because", metavar="REASON",
                    help="the reason for --override; held to the attestation bar")
    cm.add_argument("--blanket-ok", action="store_true", dest="blanket_ok",
                    help="land without naming every entry the code fired on in --because; "
                         "the stamp records the entries left unnamed")
    cm.add_argument("--expect", action="append", metavar="TEXT",
                    help="text that MUST appear in the doc after the write; repeatable. "
                         "Gate 4 refuses if absent - ids in the commit subject are "
                         "checked automatically.")
    cm.add_argument("--strict", action="store_true",
                    help="NO-OP - gate 0 always refuses on a blocking "
                         "finding. Use --override CODE[,CODE...] --because to land anyway.")
    cm.add_argument("--commit", dest="dry_run", action="store_false", default=True,
                    help="actually push (default is a dry run)")
    cm.set_defaults(func=cmd_commit)

    nz = sub.add_parser("normalize",
                        help="regenerate heading markers FROM the Status field")
    nz.add_argument("--doc", required=True)
    nz.add_argument("--commit", dest="dry_run", action="store_false", default=True)
    nz.set_defaults(func=cmd_normalize)
    nz.add_argument("--not-mine", action="store_true",
                    help="this doc belongs to another orchestrator and they have agreed to the edit")

    ar = sub.add_parser("archive",
                        help="move finished entries out of the active sections")
    ar.add_argument("--doc", required=True)
    ar.add_argument("--into", default="RESOLVED - kept for the record")
    ar.add_argument("--commit", dest="dry_run", action="store_false", default=True)
    ar.set_defaults(func=cmd_archive)
    ar.add_argument("--not-mine", action="store_true",
                    help="this doc belongs to another orchestrator and they have agreed to the edit")

    cl = sub.add_parser("clones",
                        help="is every repo checkout current with its remote?")
    cl.add_argument("--no-fetch", action="store_true",
                    help="skip the fetch (faster, but the answer may be stale)")
    cl.set_defaults(func=cmd_clones)

    v = sub.add_parser("verify",
                       help="known-good verification primitives (no shell, named oracle)")
    v.add_argument("what", choices=["at", "landed", "merged", "current"])
    v.add_argument("--ref", default=CANONICAL_REF)
    v.add_argument("--path")
    v.add_argument("--sha")
    v.add_argument("--repo", help="default: the workspace")
    v.set_defaults(func=cmd_verify)

    pc = sub.add_parser("pictures",
                        help="print the **Pictures:** block that shows a page's or a folder's "
                             "pictures inside the entry, folded ")
    pc.add_argument("path", help="the HTML page of pictures, or the folder that holds them")
    pc.add_argument("--what", default="",
                    help="what the pictures show: the text after **Pictures:**")
    pc.set_defaults(func=cmd_pictures)

    pst = sub.add_parser("paste",
                         help="put a file's text into an entry, word for word, as a fenced "
                              "block the human copies with one click ")
    pst.add_argument("id", help="the entry, e.g. T9916")
    pst.add_argument("--doc", required=True, help="o<N> or a path to the OrchDoc")
    pst.add_argument("--file", required=True, help="the file whose text goes into the entry")
    pst.add_argument("--not-mine", action="store_true",
                     help="write to a doc owned by another orchestrator")
    pst.set_defaults(func=cmd_paste)
    img = sub.add_parser("image",
                         help="copy a picture into orchdoc-images/ and embed it in an entry, "
                              "so the Reader shows it ")
    img.add_argument("id", help="the entry, e.g. W9901")
    img.add_argument("--doc", required=True, help="o<N> or a path to the OrchDoc")
    img.add_argument("--file", required=True, help="the picture: png, jpg, jpeg, gif or webp")
    img.add_argument("--alt", default="", help="what the picture shows, for the alt text")
    img.add_argument("--not-mine", action="store_true",
                     help="write to a doc owned by another orchestrator")
    img.set_defaults(func=cmd_image)

    s = sub.add_parser("selftest", help="run built-in fixtures")
    s.set_defaults(func=cmd_selftest)

    args = ap.parse_args()
    try:
        return args.func(args)
    except DocPathError as e:
        # A refusal is a RESULT, not a crash. These are the cases where the tool has been
        # pointed somewhere it must not write - outside the workspace, at a directory, at
        # something unwritable - and the right behaviour is to say so by name and stop.
        # A traceback here would be indistinguishable from a bug, and mid-command crashes
        # are how half-written state happens.
        print("[REFUSE] %s" % e, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        # Writes are atomic (write_doc), so an interrupt cannot leave a partial document.
        print("\n[orchdoc] interrupted - no document was left partially written.",
              file=sys.stderr)
        return 130


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(2)
