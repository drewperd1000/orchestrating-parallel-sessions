"""Do the copies of a shared tool agree? Refuses when they do not.

<!-- subject: claude-tooling -->

A public copy, if you keep one, is EXPECTED to differ and is never counted as drift.
"""
import argparse
import glob
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import time
try:
    from no_window import flags
except ImportError:  # a partial delivery must not take the tool down
    def flags(extra=0):
        return extra
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HOME = pathlib.Path.home()
CANON = pathlib.Path(__file__).resolve().parent

CACHE_LABEL = "plugin CACHE"
MIRRORS = [
    (CACHE_LABEL, HOME / ".claude/plugins/cache"),
]

TOOLS = ["orchdoc.py", "pause_cues.py", "reader_full_rewrite.py", "script_note_store.py",
         "orchdoc_sweep.py", "safe_push.py", "orchdoc_publish.py", "route.py",
         "orchdoc_stamp.py", "orchdoc_restrike.py", "new_orchestrator.py", "count_report.sh",
         "workspace_paths.py", "knowledge_gate.py", "knowledge_pages.py",
         "block_uncited_seed.py", "tool_drift.py", "test_knowledge_process.py",
         "route_fixtures.json",
         "knowledge_subjects.json",
         # THE MODULE MUST TRAVEL WITH THE ENGINE, same rule as the line above. orchdoc.py
         # imports both of these inside a try/except so a missing one cannot crash a
         # landing - which means an unmirrored copy does not FAIL, it silently skips: gate
         # 5 never fires and the plate falls back to id order. That is the quietest way for
         # a shipped tool to be inert.
         "priority.py", "bold_wall.py",
         "render_orchdoc_reader.py", "no_window.py", "localhost_write_gate.py",
         "reader_scripts.py", "secret_patterns.py", "done_feed.py", "shared_state.py",
         "orchdoc_upkeep.py", "sandbox_env.py", "training_header.py", "hook_context.py",
         "reader_landing.py", "reader_compare.py", "script_notes.py", "reader_priority.py",
         "copy_guard.py", "ingress_taint.py",
         "reader_status.py",
         "reader_restart.py",
         "private_folders.py", "leftover_files.py",
         "file_script.py", "rule_codes_index.py",
         "check_script.py", "script_git_dates.py",
         "orchdoc_ids.py",
         "reader_library_runner.py",
         "checkout_lookup.py",
         "mentions.py", "facets.py", "corpus.py", "memory_index.py", "sweep_read.py"]

COMPANIONS = {"mentions.py": ["route.py", "knowledge_gate.py", "knowledge_pages.py"],
              "facets.py": ["route.py", "knowledge_gate.py", "knowledge_pages.py"],
              "corpus.py": ["route.py", "knowledge_pages.py"],
              "memory_index.py": ["route.py"],
              "sweep_read.py": ["orchdoc_sweep.py"]}

PARTS = {"orchdoc.py": "orchdoc_parts"}

SKILL_CANON = HOME / ".claude/plugins/marketplaces/<your-marketplace>/plugins/<your-skills-plugin>/skills"
SKILL_CACHE = HOME / ".claude/plugins/cache/<your-marketplace>/<your-skills-plugin>"
# The app's own record of which cache directory it loads for this plugin.
INSTALLED = HOME / ".claude/plugins/installed_plugins.json"
PLUGIN_KEY = "<your-skills-plugin>@<your-marketplace>"


def _ver_key(name):
    """('1.4.7-2026.08.07') -> (1,4,7). Non-numeric leading parts sort lowest."""
    head = name.split("-")[0]
    parts = []
    for p in head.split("."):
        parts.append(int(p) if p.isdigit() else -1)
    return tuple(parts + [0] * (4 - len(parts)))[:4]


def _live_cache_choice():
    """(directory, how it was chosen) for the cache copy the app LOADS, or (None, why not).

    Older cached versions are NOT drift - comparing every one would report a working archive
    as a defect, which is the false-positive direction that gets a guard switched off. So
    exactly one directory is "live", and choosing it wrongly makes every sync miss.
    """
    try:
        data = json.loads(INSTALLED.read_text(encoding="utf-8"))
        for entry in (data.get("plugins") or {}).get(PLUGIN_KEY) or []:
            p = entry.get("installPath")
            if p and pathlib.Path(p).is_dir():
                return pathlib.Path(p), "the installPath registered in %s" % INSTALLED
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    if not SKILL_CACHE.exists():
        return None, "no plugin cache on disk"
    vers = [d for d in SKILL_CACHE.iterdir() if d.is_dir()]
    if not vers:
        return None, "no version directory in the plugin cache"
    return (max(vers, key=lambda d: _ver_key(d.name)),
            "highest version on disk - %s names none, so this is a GUESS" % INSTALLED)


def _live_cache_dir():
    return _live_cache_choice()[0]


def _lf(b):
    """These bytes with every CRLF folded to LF: the one fold every comparison here uses."""
    return b.replace(b"\r\n", b"\n")


def _digest(b):
    """Digest for COMPARING a copy with its source, with CRLF folded to LF. Never used for
    writing: what is written is always the source's bytes, unchanged.

    The app installs the cache through a git checkout, which on Windows writes CRLF,
    while a git blob is LF. Comparing raw bytes calls two skills that match line for line
    DRIFTED - the false-positive direction again.
    """
    return hashlib.sha256(_lf(b)).hexdigest()[:8]


def _file_digest(p):
    return _digest(p.read_bytes())


def _skill_git(*args):
    return subprocess.run(["git", "-C", str(SKILL_CANON)] + list(args), capture_output=True,
                          env=dict(os.environ, MSYS_NO_PATHCONV="1"), creationflags=flags())


def _rev(ref):
    r = _skill_git("rev-parse", "--verify", "-q", "%s^{commit}" % ref)
    return r.stdout.decode().strip() if r.returncode == 0 else None


def _is_ancestor(a, b):
    return _skill_git("merge-base", "--is-ancestor", a, b).returncode == 0


def skill_source(explicit=None):
    """(commit, label) the skills are written from, or (None, why no commit can be used).

    The skills are authored in the marketplace repo, so they come from a commit there:
    `explicit` when the caller names one (the post-commit hook passes the commit it just
    made, which is on no remote yet), otherwise HEAD. A commit is the author saying the
    content is finished; the working tree is not.
    """
    ref = explicit or "HEAD"
    sha_ = _rev(ref)
    if sha_ is None:
        return None, "the marketplace at %s has no commit %r" % (SKILL_CANON, ref)
    origin = _rev("origin/main")
    if origin is None or origin == sha_ or _is_ancestor(origin, sha_):
        return sha_, "marketplace commit %s (%s)" % (sha_[:8], ref)
    if explicit is None and _is_ancestor(sha_, origin):
        return origin, ("marketplace commit %s (origin/main - the checkout's HEAD %s is behind "
                        "it)" % (origin[:8], sha_[:8]))
    return None, ("marketplace commit %s (%s) does not contain its origin/main %s, so writing "
                  "it could move the cache BACKWARDS" % (sha_[:8], ref, origin[:8]))


def skill_bytes(name, commit, from_worktree=False):
    """SKILL.md as the commit holds it (or as the tree holds it with --from-worktree)."""
    if from_worktree:
        try:
            return (SKILL_CANON / name / "SKILL.md").read_bytes()
        except OSError:
            return None
    if commit is None:
        return None
    r = _skill_git("show", "%s:./%s/SKILL.md" % (commit, name))
    return r.stdout if r.returncode == 0 else None


def _skill_names(commit, from_worktree):
    if from_worktree:
        if not SKILL_CANON.exists():
            return []
        return sorted(d.name for d in SKILL_CANON.iterdir() if (d / "SKILL.md").exists())
    if commit is None:
        return []
    r = _skill_git("ls-tree", "--name-only", commit)
    return sorted(n for n in r.stdout.decode("utf-8", "replace").splitlines() if n)


def survey_skills(explicit_ref=None, from_worktree=False):
    """([(skill, source_digest, live_digest_or_None, live_path)], live_dir).
    """
    live = _live_cache_dir()
    if live is None or not SKILL_CANON.exists():
        return [], live
    commit = None if from_worktree else skill_source(explicit_ref)[0]
    rows = []
    for name in _skill_names(commit, from_worktree):
        blob = skill_bytes(name, commit, from_worktree)
        if blob is None:
            continue
        mirror = live / "skills" / name / "SKILL.md"
        rows.append((name, _digest(blob),
                     _digest(mirror.read_bytes()) if mirror.exists() else None, mirror))
    return rows, live


# One hour: far longer than any sync, so a temp this old has no live writer behind it.
STALE_TEMP_SECONDS = 3600
REPLACE_WAITS = (0.1, 0.2, 0.4, 0.8)


def _sweep_stale_temps(path):
    """Delete the temp files a sync killed mid-write left beside `path`, so they do not pile up.
    Only old ones: a young one may belong to a concurrent sync, which has its own pid in the name."""
    for old in path.parent.glob(".%s.*.tmp" % glob.escape(path.name)):
        try:
            if time.time() - old.stat().st_mtime > STALE_TEMP_SECONDS:
                old.unlink()
        except OSError:
            pass


def _replace_retrying(tmp, path):
    """os.replace, retried on PermissionError. A process reading the target through Python's own
    open() holds it without delete-sharing, which on Windows refuses a rename and allows the
    in-place write this replaced, so a brief reader would otherwise turn into a failed sync. A holder that outlasts the waits
    is reported as a failure, with the original intact."""
    for wait in REPLACE_WAITS:
        try:
            os.replace(str(tmp), str(path))
            return
        except PermissionError:
            time.sleep(wait)
    os.replace(str(tmp), str(path))


def _write_verified(path, blob):
    """Write, re-read, hash. None when the bytes on disk now equal the source, else why not.
    """
    tmp = path.parent / (".%s.%d.tmp" % (path.name, os.getpid()))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _sweep_stale_temps(path)
        tmp.write_bytes(blob)
        got = tmp.read_bytes()
        if hashlib.sha256(got).digest() != hashlib.sha256(blob).digest():
            return "re-read %s, source %s" % (hashlib.sha256(got).hexdigest()[:8],
                                              hashlib.sha256(blob).hexdigest()[:8])
        _replace_retrying(tmp, path)
    except OSError as e:
        return "%s: %s" % (type(e).__name__, str(e)[:90])
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    return None


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()[:8]


def _blob_id(b):
    """git's blob id for these bytes - what `git log --raw` prints for a version of a file."""
    return hashlib.sha1(b"blob %d\0" % len(b) + b).hexdigest()


def _history(rel):
    """Every blob id `rel` has had on origin/main (all paths into it), or an empty set.
    `:(top)` because CANON is a SUBDIRECTORY, and a bare pathspec resolves against it.
    """
    r = subprocess.run(["git", "-C", str(CANON), "log", "--full-history", "--format=", "--raw",
                        "--no-abbrev", "--diff-merges=first-parent", "origin/main", "--",
                        ":(top)" + rel],
                       capture_output=True, env=dict(os.environ, MSYS_NO_PATHCONV="1"),
                       creationflags=flags())
    if r.returncode != 0:
        return set()
    return {ln.split()[3] for ln in r.stdout.decode("utf-8", "replace").splitlines()
            if ln.startswith(":") and len(ln.split()) >= 5}


def _past_versions(rel):
    """{sha256 hex of each version `rel` has had on origin/main, CRLF folded to LF}, or an
    empty set. The blob ids come from _history; their bytes from one `git cat-file --batch`."""
    ids = _history(rel)
    if not ids:
        return set()
    r = subprocess.run(["git", "-C", str(CANON), "cat-file", "--batch"],
                       input="".join(i + "\n" for i in sorted(ids)).encode(),
                       capture_output=True, env=dict(os.environ, MSYS_NO_PATHCONV="1"),
                       creationflags=flags())
    if r.returncode != 0:
        return set()
    out, pos, found = r.stdout, 0, set()
    while pos < len(out):
        nl = out.find(b"\n", pos)
        if nl < 0:
            break
        head = out[pos:nl].split()
        pos = nl + 1
        if len(head) != 3 or head[1] != b"blob":
            continue                          # "<id> missing": no content follows
        size = int(head[2])
        found.add(hashlib.sha256(_lf(out[pos:pos + size])).hexdigest())
        pos += size + 1                       # the content, then the LF that ends it
    return found


def provably_older(path, rel, cache):
    """True when the copy at `path` holds what origin/main once held at `rel`, line endings
    aside.
    """
    try:
        b = path.read_bytes()
    except OSError:
        return False
    if rel not in cache:
        cache[rel] = _past_versions(rel)
    return hashlib.sha256(_lf(b)).hexdigest() in cache[rel]


# The file whose folder in a copy is where every tracked tool belongs in that copy.
ANCHOR = "orchdoc.py"

_WALKED = {}


def _find(root, name):
    root = pathlib.Path(root)
    if root not in _WALKED:
        index = {}
        for here, dirs, files in os.walk(str(root)):
            if ".git" in dirs:
                dirs.remove(".git")
            for n in dirs + files:
                index.setdefault(os.path.normcase(n), []).append(pathlib.Path(here) / n)
        _WALKED[root] = index
    return list(_WALKED[root].get(os.path.normcase(name), ()))


def _forget_walks():
    _WALKED.clear()


def _anchor_dirs(label, root):
    """The folders in this copy where the tracked tools belong: each folder holding its
    orchdoc.py. For the plugin cache only the LIVE directory counts - older cached versions
    are an archive, not drift (see _live_cache_choice).
    """
    if label == CACHE_LABEL:
        root = _live_cache_dir()
    if root is None or not root.exists():
        return []
    return sorted({p.parent for p in _find(root, ANCHOR)})


def _shipped(tool):
    """True when any copy, in any mirror (not only the ones this run compares), holds `tool`.
    A tracked tool no copy has ever held is left as "(no mirrored copy)": it may be
    workspace-only, and writing it into the cache would publish what nobody shipped. The
    full MIRRORS list is read even under --cache-only, because the marketplace is where a
    newly shipped tool appears first."""
    for _label, root in MIRRORS:
        if root.exists() and _find(root, tool):
            return True
    return False


def survey(from_worktree=False, mirrors=None):
    """[(tool, canonical_digest, [(label, digest, path)])] for tools that have a canonical.
    """
    rows = []
    _forget_walks()
    used = MIRRORS if mirrors is None else mirrors
    anchors = [(label, d) for label, root in used for d in _anchor_dirs(label, root)]
    for tool in TOOLS:
        src = CANON / tool
        if not src.exists():
            continue
        copies = []
        for label, root in used:
            if root.exists():
                copies += [(label, _file_digest(p), p) for p in _find(root, tool)]
        if copies or _shipped(tool) or any(_shipped(i) for i in COMPANIONS.get(tool, ())):
            for label, d in anchors:
                p = d / tool
                if not p.is_file():
                    copies.append((label, "missing", p))
        blob = source_bytes(tool, ".shared/scripts/%s" % tool, from_worktree)
        canon_digest = _digest(blob) if blob is not None else _file_digest(src)
        rows.append((tool, canon_digest, copies))
        folder = PARTS.get(tool)
        for name in part_names(folder, from_worktree) if folder else []:
            part = "%s/%s" % (folder, name)
            pblob = source_bytes(part, ".shared/scripts/%s" % part, from_worktree)
            beside = [(label, p.parent / folder / name) for label, _d, p in copies]
            rows.append((part, _digest(pblob) if pblob is not None
                         else "-", [(label, _file_digest(q) if q.is_file() else "missing", q)
                                    for label, q in beside]))
    return rows


def part_names(folder, from_worktree):
    """The *.py parts in `folder`, read from the source --sync writes from (see source_bytes):
    origin/main's tree, or this tree when asked. A README or __pycache__ there is not a part."""
    if from_worktree:
        d = CANON / folder
        return sorted(p.name for p in d.glob("*.py")) if d.is_dir() else []
    r = subprocess.run(["git", "-C", str(CANON), "ls-tree", "--name-only", "--full-tree",
                        "origin/main", "--", ".shared/scripts/%s/" % folder],
                       capture_output=True, text=True,
                       env=dict(os.environ, MSYS_NO_PATHCONV="1"), creationflags=flags())
    return sorted(ln.rsplit("/", 1)[-1] for ln in r.stdout.splitlines()
                  if ln.endswith(".py")) if r.returncode == 0 else []


def source_bytes(tool, rel, from_worktree):
    """The bytes to write into a mirror: origin/main's, or this tree's if asked.

    Returns None when the ref cannot answer, and the caller SKIPS rather than silently
    falling back to the tree - a fallback here would reintroduce the exact hazard.
    """
    if from_worktree:
        try:
            return (CANON / tool).read_bytes()
        except OSError:
            return None
    r = subprocess.run(["git", "-C", str(CANON), "show", "origin/main:%s" % rel],
                       capture_output=True,
                       env=dict(os.environ, MSYS_NO_PATHCONV="1"), creationflags=flags())
    return r.stdout if r.returncode == 0 else None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sync", action="store_true",
                    help="write every drifted private copy: scripts from origin/main, "
                         "SKILL.md from a marketplace commit")
    ap.add_argument("--from-worktree", action="store_true",
                    help="write from THIS tree instead of the ref - opens the AHEAD window; "
                         "only for a deliberate mid-edit sync")
    ap.add_argument("--cache-only", action="store_true",
                    help="compare and write only the plugin cache, never the marketplace "
                         "working tree (the marketplace's own commit hook uses this)")
    ap.add_argument("--skill-ref",
                    help="marketplace commit to take SKILL.md from (default HEAD; the "
                         "post-commit hook passes the commit it just made)")
    ap.add_argument("--only-older", action="store_true",
                    help="with --sync: overwrite a script copy only when its bytes are a past "
                         "version of the file on origin/main; leave any other copy alone and "
                         "report it. For an unattended run.")
    a = ap.parse_args(argv)
    history = {}

    mirrors = [m for m in MIRRORS if m[0] == CACHE_LABEL] if a.cache_only else MIRRORS
    rows = survey(a.from_worktree, mirrors)
    rows = sorted(rows, key=lambda r: 0 if "/" in r[0] else 1)
    drifted = []
    # What --sync did, counted from what is ON DISK afterwards, never from what it tried.
    synced, skipped, failed, current = [], [], [], 0
    print("tool drift - canonical is %s" % CANON)
    print()
    for tool, canon, copies in rows:
        bad = [c for c in copies if c[1] != canon]
        current += len(copies) - len(bad)
        if not copies:
            print("  %-22s %s   (no mirrored copy)" % (tool, canon))
            continue
        if not bad:
            print("  %-22s %s   %d copy(ies) agree" % (tool, canon, len(copies)))
            continue
        drifted.append(tool)
        print("  %-22s %s   [!] %d of %d DRIFTED" % (tool, canon, len(bad), len(copies)))
        for label, d, p in bad:
            print("      %-20s %s  %s" % (label, d, str(p)[-58:]))
            if not a.sync:
                continue
            if failed:
                skipped.append((str(p), "not written: an earlier write failed"))
                print("      %-20s SKIPPED - an earlier write failed; stopped" % "")
                continue
            blob = source_bytes(tool, ".shared/scripts/%s" % tool, a.from_worktree)
            if blob is None:
                # Falling back to this tree is the hazard the ref default exists to avoid.
                skipped.append((tool, "origin/main has no .shared/scripts/%s" % tool))
                print("      %-20s SKIPPED - origin/main cannot supply it" % "")
                continue
            if a.only_older and d != "missing" and \
                    not provably_older(p, ".shared/scripts/%s" % tool, history):
                skipped.append((str(p), "holds bytes origin/main never had, so it may be newer "
                                        "work - left alone"))
                print("      %-20s SKIPPED - matches no version origin/main ever had; left alone"
                      % "")
                continue
            why = _write_verified(p, blob)
            if why:
                failed.append((str(p), why))
                print("      %-20s FAILED - %s" % ("", why))
            else:
                synced.append(("%s (%s)" % (tool, label), p))

    # --- SKILL.md: the file that teaches the rules. ---
    live, how = _live_cache_choice()
    commit, src = (None, "THIS WORKING TREE") if a.from_worktree else skill_source(a.skill_ref)
    print()
    if live is None:
        print("  skills: %s - nothing to compare" % how)
    elif commit is None and not a.from_worktree:
        names = ", ".join(_skill_names(None, True)) or "none found"
        drifted.append("skills")
        skipped.append(("every SKILL.md (%s)" % names, src))
        print("  skills: NOT COMPARED (%s) - %s" % (names, src))
    else:
        print("  skills - source is %s" % src)
        print("  live cache (the copy sessions load): %s" % live)
        print("    chosen by %s" % how)
        srows, _live = survey_skills(a.skill_ref, a.from_worktree)
        for name, canon, mine, path in srows:
            if mine == canon:
                current += 1
                print("    %-34s %s   agrees" % (name, canon))
                continue
            what = name + "/SKILL.md"
            drifted.append(what)
            if mine is None:
                print("    %-34s %s   [!] MISSING from the live cache" % (name, canon))
            else:
                print("    %-34s %s   [!] DRIFTED (cache %s) - sessions load the CACHE, so they"
                      % (name, canon, mine))
                print("    %-34s     are being taught this copy, not the source." % "")
            if not a.sync:
                continue
            blob = skill_bytes(name, commit, a.from_worktree)
            if blob is None:
                skipped.append((what, "%s has no %s" % (src, what)))
                print("    %-34s     SKIPPED - %s has no %s" % ("", src, what))
                continue
            why = _write_verified(path, blob)
            if why:
                failed.append((str(path), why))
                print("    %-34s     FAILED - %s" % ("", why))
            else:
                synced.append((what, path))

    print()
    if a.sync:
        print("  sync: scripts from %s; skills from %s"
              % ("THIS WORKING TREE" if a.from_worktree else "origin/main", src))
        if synced:
            print("  sync: synced %d copy(ies), each re-read and hash-verified after writing:"
                  % len(synced))
            for what, p in synced:
                print("  sync:   %s -> %s" % (what, p))
        elif skipped or failed:
            print("  sync: synced 0 copy(ies) - see SKIPPED / FAILED below")
        else:
            print("  sync: synced 0 copy(ies) - nothing new: every copy already matched its "
                  "source")
        print("  sync: already current, left alone: %d copy(ies)" % current)
        for what, why in skipped:
            print("  sync: SKIPPED %s - %s" % (what, why))
        for what, why in failed:
            print("  sync: FAILED %s - %s" % (what, why))
        still = [t for t, c, cs in survey(a.from_worktree, mirrors)
                 if any(x[1] != c for x in cs)]
        if live is not None:
            if commit is None and not a.from_worktree:
                still.append("every SKILL.md (no usable source)")
            else:
                srows2, _l = survey_skills(a.skill_ref, a.from_worktree)
                still += [n + "/SKILL.md" for n, c, m, _p in srows2 if m != c]
        if still:
            print("  sync: [!] STILL DRIFTED after sync: %s" % ", ".join(still))
            return 1
        print("  sync: re-measured - every copy now agrees.")
        return 0
    if drifted:
        print("  [!] %d tool(s) have copies that disagree. A session invoking the skill loads" %
              len(drifted))
        print("     the CACHE copy, so a fix in canonical may not be the code that runs.")
        print("     Fix: tool_drift.py --sync")
        return 1
    print("  every tracked tool agrees across every copy.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
