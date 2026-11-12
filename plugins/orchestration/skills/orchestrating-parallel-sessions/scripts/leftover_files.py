#!/usr/bin/env python3
"""LEFTOVER FILES: an untracked file whose path the base's history deleted. The ONE definition.

<!-- subject: infrastructure -->

<!-- route-tags: leftover leftovers untracked deleted moved renamed deliver never deletes
     generator dev-docs-index knowledge-pages index-regen nightly landing origin main history -->

THE TEST. A file is a LEFTOVER of `base` in `repo` when all three hold:
  1. it is in the repo's own tree: no folder between the repo's top and the file holds a
     `.git`. A nested product repo has its own history, and the memory store is another repo.
  2. git does not track it: its path is not in the repo's index.
  3. `base`'s history deleted its path: `git log --no-renames --diff-filter=D --full-history
     --diff-merges=first-parent <base> -- <path>` names it. --no-renames, because rename
     detection turns a move into one R entry and --diff-filter=D would then miss the old path;
     --full-history, so a deletion made on a branch that was merged counts; first-parent diffs
     for merges, so a deletion that a merge brought onto main counts.
A new untracked doc whose path the history never held is NOT a leftover, and keeps its row.

TWO STRICTNESSES, ONE TEST.
  strict  `base` given. A landing job passes the origin/main it pinned. When the test
          cannot run (a git error, a base that does not resolve, a folder that is not the top
          of a repository) CannotCheck is raised, and the job refuses that output.
  plain   `base` None: a generator run by hand. The base is
          origin/main. When the test cannot run, nothing is dropped, which is what the
          generators did before this module, so a plain run never fails where it worked.
"""
import os
import subprocess

try:
    from no_window import flags
except ImportError:  # a partial delivery must not take the tool down
    def flags(extra=0):
        return extra

import private_folders  # the folders outsiders write, ONE definition

#: What a plain run checks against: the ref a landing job pins as its base.
PLAIN_BASE = "refs/remotes/origin/main"
TIMEOUT = 60
#: Paths per `git log`, so its command line stays far below Windows' 32,767 characters.
BATCH = 100


class CannotCheck(Exception):
    """The leftover test could not run. The message says why and names no file."""


def _norm(p):
    return os.path.normcase(os.path.abspath(os.fsdecode(p)))


def _said(err):
    line = next((l.strip() for l in (err or "").splitlines() if l.strip()), "no message")
    if any(n in line.lower() for n in private_folders.NAMES):
        return "its message names a folder whose files outsiders write, so it is withheld"
    return line[:200]


def _git(top, args):
    what = next((a for a in args if not a.startswith("-")), "?")
    try:
        p = subprocess.run(["git", "-C", top] + args, capture_output=True, timeout=TIMEOUT,
                           creationflags=flags())
    except subprocess.TimeoutExpired:
        raise CannotCheck("git %s took longer than %d seconds" % (what, TIMEOUT))
    except OSError as e:
        raise CannotCheck("git did not run: %s" % e)
    if p.returncode != 0:
        raise CannotCheck("git %s failed: %s"
                          % (what, _said(p.stderr.decode("utf-8", errors="replace"))))
    return p.stdout.decode("utf-8", errors="replace")


def _same(a, b):
    return _norm(a) == _norm(b) or _norm(os.path.realpath(a)) == _norm(os.path.realpath(b))


def _rel(top, p):
    """`p` relative to `top` in git's form, keeping the file's own case; None when outside."""
    ap = os.path.abspath(os.fsdecode(p))
    if os.path.normcase(ap).startswith(os.path.normcase(top) + os.sep):
        return ap[len(top) + 1:].replace(os.sep, "/")
    return None


def _own_tree(top, rel, cache):
    """True when no folder between `top` and the file holds a `.git` (a nested repo)."""
    d = top
    for part in rel.split("/")[:-1]:
        d = os.path.join(d, part)
        k = os.path.normcase(d)
        if k not in cache:
            cache[k] = os.path.exists(os.path.join(d, ".git"))
        if cache[k]:
            return False
    return True


def find(repo, paths, base):
    """{normalised absolute path} of the LEFTOVERS among `paths`, by the test in this module's
    docstring. Read-only. Raises CannotCheck when it cannot tell."""
    top = os.path.abspath(os.fsdecode(repo)).rstrip("\\/")
    shown = _git(top, ["rev-parse", "--show-toplevel"]).strip()
    if not shown or not _same(shown, top):
        raise CannotCheck("the folder given is not the top of its git repository")
    try:
        sha = _git(top, ["rev-parse", "--verify", "--quiet", "%s^{commit}" % base]).strip()
    except CannotCheck:
        sha = ""
    if not sha:
        raise CannotCheck("the base %s does not resolve to a commit" % str(base)[:40])
    tracked = {os.path.normcase(n)
               for n in _git(top, ["--no-optional-locks", "ls-files", "-z"]).split("\0") if n}
    cands, cache = {}, {}
    for p in paths:
        rel = _rel(top, p)
        if rel is None:
            continue
        k = os.path.normcase(rel)
        if k in tracked or not _own_tree(top, rel, cache):
            continue
        cands.setdefault(k, (rel, set()))[1].add(_norm(p))
    if not cands:
        return set()
    rels = sorted(rel for rel, _ in cands.values())
    deleted = set()
    for i in range(0, len(rels), BATCH):
        out = _git(top, ["log", "--no-renames", "--diff-filter=D", "--name-only", "--format=",
                         "-z", "--full-history", "--diff-merges=first-parent", sha, "--"]
                   + [":(literal,icase)" + r for r in rels[i:i + BATCH]])
        deleted |= {os.path.normcase(n.strip("\n")) for n in out.split("\0") if n.strip("\n")}
    return {n for k, (_r, names) in cands.items() if k in deleted for n in names}


def drop(arts, repo, base=None, skipped=None):
    """The artifacts in `arts` (dicts with a "path") that are not leftovers, in their order.

    Strict when `base` is given: CannotCheck reaches the caller. Plain when it is None: the
    base is origin/main, and when the test cannot run every artifact is kept, as before.
    `skipped`, when a list, receives each artifact dropped, so the caller can count them."""
    try:
        gone = find(repo, [a["path"] for a in arts], PLAIN_BASE if base is None else base)
    except CannotCheck:
        if base is not None:
            raise
        return list(arts)
    kept = []
    for a in arts:
        if _norm(a["path"]) in gone:
            if skipped is not None:
                skipped.append(a)
        else:
            kept.append(a)
    return kept


def note(n):
    """The one line a generator prints for `n` leftovers skipped: counted, never named."""
    return ("   %d leftover file(s) skipped: untracked, and origin/main's history deleted their "
            "paths (the delivery step writes and never deletes); left on disk for a person to clear" % n)
