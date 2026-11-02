#!/usr/bin/env python3
"""The `.shared/cloud/` folder: its layout and the formats of its JSON files, in ONE place.

<!-- subject: orchestration -->
<!-- route-tags: cloud layout .shared/cloud MANIFEST.json hooks.json hook-status.json format
     validate read cloud session published rules memory copy freshness stale -->

`cloud_publish.py` writes this folder; `cloud_start.py` reads it. Both import the paths, the
readers and the validators from here, so a format is defined once and a file that does not
match it is refused the same way on both sides (o9 W140, cloud Phase 2, plan section 5).

THE LAYOUT, relative to the repo root:

    .shared/cloud/global-rules.md    the laptop's global CLAUDE.md, byte for byte (named so that
                                     Claude Code never loads it as a folder's CLAUDE.md; see
                                     CLAUDE_MD below)
    .shared/cloud/memory/MEMORY.md   the memory index, then the `m/N.md` pointers it links to and
    .shared/cloud/memory/...         the note behind each pointer, laid out as in the memory folder
    .shared/cloud/hooks.json         the hooks a cloud session registers (format below)
    .shared/cloud/MANIFEST.json      what was published, from what, and when (format below)
    .shared/cloud/hook-status.json   one decision per laptop hook; kept by hand, never written by
                                     the publisher (format below)
    .shared/cloud/BUILD-RULES.md     the cloud addendum; kept by hand, never written by the
                                     publisher

MANIFEST.json, written last by cloud_publish.py:

    {
      "format": 1,
      "stamp": "2026-10-02T22:10:00-07:00",          local time with its offset
      "publisher": ".shared/scripts/cloud_publish.py",
      "sources": {                                    what the copy was made from
        "CLAUDE.md":            {"sha256": "<64 hex>", "bytes": 8912},
        "memory/MEMORY.md":     {"sha256": "...", "bytes": ...},
        "settings.json hooks":  {"sha256": "...", "bytes": ...},   canonical JSON of the
                                                                    registered hook commands
        "hook-status.json":     {"sha256": "...", "bytes": ...}
      },
      "outputs": {"global-rules.md": "<64 hex>", "memory/MEMORY.md": "...", "hooks.json": "...",
                  ...},
                                                      every file written, relative to
                                                      .shared/cloud/, MANIFEST.json itself excepted
      "counts": {"memory_files": 362, "links_resolved": 360, "hooks_published": 29, ...}
    }

    A source name is a logical name, never a laptop path. Two manifests describe the same
    publication when their `outputs` maps are equal (same_publication); the stamp and the
    sources do not count, so a run that would write the same bytes commits nothing.
    stale_sources() compares `sources` with the laptop's files, for cloud_start.py's freshness
    check.

hooks.json, the PUBLISHED hooks only, in settings.json order:

    {
      "format": 1,
      "hooks": [
        {
          "name": "block_python_heredoc.py",
          "script": ".shared/scripts/block_python_heredoc.py",     repo-relative
          "reason": "<the hook-status.json reason>",
          "registrations": [
            {"event": "PreToolUse", "matcher": "Bash", "args": "", "tail": ""}
          ]
        }
      ]
    }

    One registration per settings.json entry that runs the hook. `matcher` is "" for an
    entry with none. `args` is what the laptop passes after the script ("hook", "--quiet").
    `tail` is the laptop command's shell tail after the arguments: "" for a guard, whose
    exit 2 must still block, or a redirect and `|| true` for a hook the laptop never lets
    block ("2>&1 || true"). Both go into a shell command, so validate_hooks() accepts only
    the plain shapes in ARGS_RE and TAIL_RE, and a hook whose laptop command has any other
    tail cannot be published until its decision changes.

hook-status.json, one decision per hook that settings.json runs:

    {
      "format": 1,
      "about": "<free text>",
      "hooks": {
        "<name>.py": {"decision": "published" | "laptop-only" | "needs-port",
                      "reason": "<why, in a sentence>"}
      },
      "acknowledgments": [
        {"hook": "<name>.py", "code": "POWERSHELL", "line_sha256": "<64 hex>",
         "reason": "<why this one line is safe in a cloud session>"}
      ]
    }

    validate_hook_status(data, registered) refuses a registered hook with no decision and a
    decision for a name that is not registered, so the list cannot drift from settings.json
    in either direction without the publisher and its test failing.

    `acknowledgments` is optional. Each one clears ONE finding of cloud_start's Linux checker
    on ONE line of a hook: `code` is the finding's code (one of ACK_CODES) and `line_sha256`
    is line_sha256() of that line, so editing the line - even to a call that really breaks -
    ends the acknowledgment and the finding counts again. cloud_start reports an
    acknowledgment that matches no current line as lapsed. The validation refuses an
    acknowledgment with a missing or unknown key, a hook with no decision, an unknown code,
    a hash that is not 64 lowercase hex, no reason, or the same (hook, code, hash) twice.

Every reader raises LayoutError, a ValueError, with every problem found, one per line.
"""
import hashlib
import json
import pathlib
import posixpath
import re

FORMAT = 1

CLOUD_DIR = ".shared/cloud"
#: The published copy of the laptop's global CLAUDE.md. ⛔ NOT NAMED CLAUDE.md (o9L1015,
#: 2026-10-03). Claude Code loads a CLAUDE.md in any folder whose files a session reads, so a
#: cloud session that touched `.shared/cloud/` got the global rules in its context twice (seen in
#: the second cloud check), and a laptop session reading a file there would load this copy,
#: which can be older than the real rules. The folder holds no other file of a name Claude Code
#: loads as instructions.
CLAUDE_MD = CLOUD_DIR + "/global-rules.md"
#: Where earlier publishers wrote that copy. cloud_publish.py removes it from origin when it is
#: still there, and the walkers skip it like a generated copy.
RETIRED_COPIES = (CLOUD_DIR + "/CLAUDE.md",)
MEMORY_DIR = CLOUD_DIR + "/memory"
MEMORY_INDEX = MEMORY_DIR + "/MEMORY.md"
HOOKS_JSON = CLOUD_DIR + "/hooks.json"
MANIFEST_JSON = CLOUD_DIR + "/MANIFEST.json"
HOOK_STATUS_JSON = CLOUD_DIR + "/hook-status.json"
BUILD_RULES = CLOUD_DIR + "/BUILD-RULES.md"

#: The generated copies under CLOUD_DIR: cloud_publish.py writes them from the laptop's global
#: CLAUDE.md and memory folder; RETIRED_COPIES names where an earlier publisher wrote one. A
#: walker of the workspace skips them (it reads the same files where they are written) and keeps
#: the hand-kept files beside them, BUILD-RULES.md and hook-status.json. is_generated_copy() is the one test; the three walkers call it
#: (dev_docs_index.py, check_unfiled_decisions.py, correction.py). gbrain_catalog_build.py called it
#: until its union step was removed (o9 W160, lane o9L1130).
GENERATED_COPIES = (CLAUDE_MD, MEMORY_DIR) + RETIRED_COPIES


def is_generated_copy(path):
    """True when `path` is .shared/cloud/global-rules.md, .shared/cloud/memory or anything under
    it, or a retired copy (.shared/cloud/CLAUDE.md).

    `path` may be absolute or relative, a str or a Path, with either slash. The test is on a run
    of path parts, so it holds in any checkout, worktree or sandbox. `.shared/cloud` itself is
    not a copy, so a walker still goes into it and finds BUILD-RULES.md."""
    parts = [p for p in re.split(r"[\\/]+", str(path)) if p not in ("", ".")]
    for gen in GENERATED_COPIES:
        g = gen.split("/")
        for i in range(len(parts) - len(g) + 1):
            if parts[i:i + len(g)] == g:
                return True
    return False

#: The same files, relative to CLOUD_DIR, as MANIFEST.json's `outputs` names them.
OUT_CLAUDE_MD = "global-rules.md"
OUT_MEMORY_DIR = "memory"
OUT_MEMORY_INDEX = "memory/MEMORY.md"
OUT_HOOKS_JSON = "hooks.json"

#: MANIFEST.json's source names. "CLAUDE.md" names the laptop's source file, which keeps its
#: name; only the published copy was renamed (OUT_CLAUDE_MD).
SOURCE_CLAUDE_MD = "CLAUDE.md"
SOURCE_MEMORY_INDEX = "memory/MEMORY.md"
SOURCE_HOOKS = "settings.json hooks"
SOURCE_HOOK_STATUS = "hook-status.json"

DECISIONS = ("published", "laptop-only", "needs-port")
EVENTS = ("PreToolUse", "PostToolUse", "PostToolUseFailure", "SessionStart", "SessionEnd",
          "Stop", "SubagentStop", "UserPromptSubmit", "Notification", "PreCompact")

#: A hook's arguments: plain words, flags and `key=value`, single spaces between them.
ARGS_RE = re.compile(r"^(?:[A-Za-z0-9_.:=/-]+(?: [A-Za-z0-9_.:=/-]+)*)?$")
#: A hook's tail: an optional stderr or stdout redirect, then an optional `|| true`.
TAIL_RE = re.compile(r"^(?:(?:2>&1|2>/dev/null|>/dev/null|>/dev/null 2>&1)"
                     r"(?: \|\| true)?|\|\| true)?$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
#: The finding codes of cloud_start.HOOK_PROBLEMS, which an acknowledgment may name.
#: test_cloud_start checks that the two lists match.
ACK_CODES = ("WINPATH", "POWERSHELL", "WINDLL", "WINREG", "STARTFILE")
#: The keys of one acknowledgment in hook-status.json, all required, no others.
ACK_KEYS = ("hook", "code", "line_sha256", "reason")


class LayoutError(ValueError):
    """A `.shared/cloud/` file is missing, is not JSON, or does not match its format."""


def cloud_root(repo_root):
    """`<repo_root>/.shared/cloud` as a Path."""
    return pathlib.Path(repo_root) / ".shared" / "cloud"


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def line_sha256(line):
    """The hash an acknowledgment in hook-status.json names: sha256 of the line's text with
    leading and trailing whitespace stripped, as UTF-8. Indenting the line keeps the hash;
    any other edit changes it."""
    return sha256_bytes(line.strip().encode("utf-8"))


def sha256_file(path):
    """The file's sha256, or None when it cannot be read."""
    try:
        return sha256_bytes(pathlib.Path(path).read_bytes())
    except OSError:
        return None


def dumps(data):
    """The JSON text every file here is written as: two-space indent, non-ASCII kept as is, one
    final newline, keys in the order the caller built them. The same data always gives the
    same bytes, which is what lets same_publication() compare hashes."""
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def safe_rel(rel):
    """True for a relative POSIX path that stays inside the folder it is relative to."""
    if not isinstance(rel, str) or not rel or "\\" in rel or rel.startswith("/"):
        return False
    if re.match(r"^[A-Za-z]:", rel):
        return False
    norm = posixpath.normpath(rel)
    return norm == rel and not norm.startswith("../") and norm != ".."


def _need_format(data, what):
    if not isinstance(data, dict):
        return ["%s: the top level is not a JSON object" % what]
    if data.get("format") != FORMAT:
        return ["%s: format is %r, this reader knows %d" % (what, data.get("format"), FORMAT)]
    return []


# --- hook-status.json ----------------------------------------------------------------------

def validate_hook_status(data, registered=None):
    """Problems with a hook-status.json object, as a list of lines; [] when it is good.

    `registered`, when given, is the list of hook names settings.json runs
    (install_hooks.hook_names()); every one must have a decision and every decision must name
    one of them.
    """
    problems = _need_format(data, "hook-status.json")
    if problems:
        return problems
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return ["hook-status.json: `hooks` is not an object"]
    for name, entry in hooks.items():
        if not name.endswith(".py") or "/" in name or "\\" in name:
            problems.append("hook-status.json: %r is not a script file name" % name)
        if not isinstance(entry, dict):
            problems.append("hook-status.json: %s: the entry is not an object" % name)
            continue
        if entry.get("decision") not in DECISIONS:
            problems.append("hook-status.json: %s: decision %r is not one of %s"
                            % (name, entry.get("decision"), ", ".join(DECISIONS)))
        if not str(entry.get("reason") or "").strip():
            problems.append("hook-status.json: %s: no reason" % name)
    if registered is not None:
        reg = list(registered)
        for name in reg:
            if name not in hooks:
                problems.append("hook-status.json: %s is registered in settings.json and has no "
                                "decision" % name)
        for name in hooks:
            if name not in reg:
                problems.append("hook-status.json: %s has a decision and settings.json does not "
                                "run it" % name)
    return problems + _ack_problems(data.get("acknowledgments", []), hooks)


def _ack_problems(acks, hooks):
    """Problems with hook-status.json's `acknowledgments` list (see the format above)."""
    if not isinstance(acks, list):
        return ["hook-status.json: `acknowledgments` is not a list"]
    problems, seen = [], set()
    for i, ack in enumerate(acks):
        where = "hook-status.json: acknowledgment %d" % i
        if not isinstance(ack, dict):
            problems.append("%s is not an object" % where)
            continue
        missing = [k for k in ACK_KEYS if k not in ack]
        extra = sorted(k for k in ack if k not in ACK_KEYS)
        if missing:
            problems.append("%s has no %s" % (where, ", ".join(missing)))
        if extra:
            problems.append("%s has unknown key(s) %s" % (where, ", ".join(map(repr, extra))))
        hook, code, sha = ack.get("hook"), ack.get("code"), ack.get("line_sha256")
        if "hook" in ack and not (isinstance(hook, str) and hook in hooks):
            problems.append("%s: hook %r has no decision in `hooks`" % (where, hook))
        if "code" in ack and code not in ACK_CODES:
            problems.append("%s: code %r is not one of %s" % (where, code, ", ".join(ACK_CODES)))
        if "line_sha256" in ack and not (isinstance(sha, str) and _HEX64.match(sha)):
            problems.append("%s: line_sha256 %r is not 64 lowercase hex characters"
                            % (where, sha))
        if "reason" in ack and not (isinstance(ack["reason"], str) and ack["reason"].strip()):
            problems.append("%s: no reason" % where)
        key = (str(hook), str(code), str(sha))
        if key in seen:
            problems.append("%s repeats hook %s, code %s and hash %s" % ((where,) + key))
        seen.add(key)
    return problems


def decisions(data):
    """{name: decision} from a hook-status.json object that validated."""
    return {n: e["decision"] for n, e in data["hooks"].items()}


def acknowledgments(data):
    """The `acknowledgments` list of a hook-status.json object that validated; [] if none."""
    return list(data.get("acknowledgments") or [])


# --- hooks.json ----------------------------------------------------------------------------

def validate_hooks(data):
    """Problems with a hooks.json object, as a list of lines; [] when it is good."""
    problems = _need_format(data, "hooks.json")
    if problems:
        return problems
    hooks = data.get("hooks")
    if not isinstance(hooks, list):
        return ["hooks.json: `hooks` is not a list"]
    seen = set()
    for i, h in enumerate(hooks):
        where = "hooks.json: hook %d" % i
        if not isinstance(h, dict):
            problems.append(where + ": not an object")
            continue
        name = h.get("name")
        if not isinstance(name, str) or not re.match(r"^[A-Za-z0-9_.-]+\.py$", name):
            problems.append(where + ": name %r is not a script file name" % (name,))
            continue
        where = "hooks.json: %s" % name
        if name in seen:
            problems.append(where + ": listed twice")
        seen.add(name)
        if h.get("script") != ".shared/scripts/" + name:
            problems.append(where + ": script is %r, expected .shared/scripts/%s"
                            % (h.get("script"), name))
        if not str(h.get("reason") or "").strip():
            problems.append(where + ": no reason")
        regs = h.get("registrations")
        if not isinstance(regs, list) or not regs:
            problems.append(where + ": no registrations")
            continue
        for r in regs:
            if not isinstance(r, dict):
                problems.append(where + ": a registration is not an object")
                continue
            if r.get("event") not in EVENTS:
                problems.append(where + ": event %r is not a hook event" % (r.get("event"),))
            if not isinstance(r.get("matcher"), str):
                problems.append(where + ": matcher is not a string")
            if not isinstance(r.get("args"), str) or not ARGS_RE.match(r.get("args")):
                problems.append(where + ": arguments %r are not plain words" % (r.get("args"),))
            if not isinstance(r.get("tail"), str) or not TAIL_RE.match(r.get("tail")):
                problems.append(where + ": tail %r is not a redirect and `|| true`"
                                % (r.get("tail"),))
    return problems


# --- MANIFEST.json -------------------------------------------------------------------------

def validate_manifest(data):
    """Problems with a MANIFEST.json object, as a list of lines; [] when it is good."""
    problems = _need_format(data, "MANIFEST.json")
    if problems:
        return problems
    if not isinstance(data.get("stamp"), str) or not data.get("stamp"):
        problems.append("MANIFEST.json: no stamp")
    sources = data.get("sources")
    if not isinstance(sources, dict):
        problems.append("MANIFEST.json: `sources` is not an object")
    else:
        for name, s in sources.items():
            if not isinstance(s, dict) or not _HEX64.match(str(s.get("sha256", ""))):
                problems.append("MANIFEST.json: source %s has no sha256" % name)
    outputs = data.get("outputs")
    if not isinstance(outputs, dict) or not outputs:
        problems.append("MANIFEST.json: `outputs` is empty or not an object")
    else:
        for rel, digest in outputs.items():
            if not safe_rel(rel):
                problems.append("MANIFEST.json: output %r is not a relative path inside "
                                ".shared/cloud" % (rel,))
            if not _HEX64.match(str(digest)):
                problems.append("MANIFEST.json: output %s has no sha256" % rel)
        for need in (OUT_CLAUDE_MD, OUT_MEMORY_INDEX, OUT_HOOKS_JSON):
            if need not in outputs:
                problems.append("MANIFEST.json: output %s is not listed" % need)
    if not isinstance(data.get("counts"), dict):
        problems.append("MANIFEST.json: `counts` is not an object")
    return problems


def same_publication(old, new):
    """True when two manifests list the same output files with the same bytes."""
    return (isinstance(old, dict) and isinstance(new, dict)
            and old.get("format") == new.get("format")
            and old.get("outputs") == new.get("outputs"))


def stale_sources(manifest, claude_md_path, memory_index_path):
    """The source names whose laptop file no longer matches the manifest: [] when fresh.

    cloud_start.py's freshness check: the global CLAUDE.md and the memory index as they are
    on the laptop now, against the hashes the last publish recorded. A file that cannot be
    read counts as stale.
    """
    sources = (manifest or {}).get("sources") or {}
    out = []
    for name, path in ((SOURCE_CLAUDE_MD, claude_md_path),
                       (SOURCE_MEMORY_INDEX, memory_index_path)):
        recorded = (sources.get(name) or {}).get("sha256")
        if not recorded or sha256_file(path) != recorded:
            out.append(name)
    return out


# --- readers -------------------------------------------------------------------------------

def _load(path, what):
    p = pathlib.Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise LayoutError("%s: not found at %s" % (what, p))
    except (OSError, UnicodeDecodeError) as e:
        raise LayoutError("%s: cannot read %s: %s" % (what, p, e))
    try:
        return json.loads(text)
    except ValueError as e:
        raise LayoutError("%s: not JSON: %s" % (what, e))


def _checked(data, problems):
    if problems:
        raise LayoutError("\n".join(problems))
    return data


def read_published_bytes(repo_root, rel):
    """The bytes of a published file under `repo_root`, named by one of the path constants
    above (CLAUDE_MD, MEMORY_INDEX, BUILD_RULES); LayoutError when it is not there.

    cloud_start.py copies the markdown files from here into a start branch, so a missing one
    is refused the same way as a missing JSON file."""
    if not (isinstance(rel, str) and rel.startswith(CLOUD_DIR + "/") and safe_rel(rel)):
        raise LayoutError("%r is not a path inside %s" % (rel, CLOUD_DIR))
    p = pathlib.Path(repo_root) / rel
    try:
        return p.read_bytes()
    except FileNotFoundError:
        raise LayoutError("%s: not found at %s" % (rel[len(CLOUD_DIR) + 1:], p))
    except OSError as e:
        raise LayoutError("%s: cannot read %s: %s" % (rel[len(CLOUD_DIR) + 1:], p, e))


def load_hook_status_text(text, registered=None):
    """A hook-status.json object from its text, validated (see validate_hook_status)."""
    try:
        data = json.loads(text)
    except ValueError as e:
        raise LayoutError("hook-status.json: not JSON: %s" % e)
    return _checked(data, validate_hook_status(data, registered))


def read_hook_status(repo_root, registered=None):
    """`.shared/cloud/hook-status.json` under `repo_root`, validated."""
    data = _load(cloud_root(repo_root) / "hook-status.json", "hook-status.json")
    return _checked(data, validate_hook_status(data, registered))


def read_hooks(repo_root):
    """`.shared/cloud/hooks.json` under `repo_root`, validated; returns its `hooks` list."""
    data = _load(cloud_root(repo_root) / "hooks.json", "hooks.json")
    return _checked(data, validate_hooks(data))["hooks"]


def read_manifest(repo_root):
    """`.shared/cloud/MANIFEST.json` under `repo_root`, validated."""
    data = _load(cloud_root(repo_root) / "MANIFEST.json", "MANIFEST.json")
    return _checked(data, validate_manifest(data))


def load_manifest_text(text):
    """A MANIFEST.json object from its text, validated."""
    try:
        data = json.loads(text)
    except ValueError as e:
        raise LayoutError("MANIFEST.json: not JSON: %s" % e)
    return _checked(data, validate_manifest(data))
