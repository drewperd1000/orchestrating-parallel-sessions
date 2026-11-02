#!/usr/bin/env python3
"""The ONLY shell command a fresh-eyes sweep lane may run (o9:W76). It reads; it cannot write.

<!-- subject: orchestration -->

<!-- route-tags: fresh-eyes sweep lane read-only wrapper git show log gh pr view route allowlist -->

    python <workspace>/.shared/scripts/sweep_read.py git [-C <repo>] show|log|ls-tree|merge-base|rev-parse ...
    python <workspace>/.shared/scripts/sweep_read.py git [-C <repo>] cat-file -p <object>
    python <workspace>/.shared/scripts/sweep_read.py gh pr view <n> --repo <your-github-user>/<repo> --json <fields>
    python <workspace>/.shared/scripts/sweep_read.py route <terms...>

⛔ WHY A WRAPPER AND NOT PREFIX RULES. The second review of the sweep (0521fb89) showed that the
prefix rules `Bash(git log:*)`, `Bash(git show:*)` and `Bash(python .shared/scripts/route.py:*)`
let a lane write files and run code:
  * `git log --output=<file>` and `git show --output=<file>` write any content anywhere - the
    reviewer overwrote a .py file and ran it, and could have planted `.git/hooks/post-commit`,
    which every worktree shares;
  * `route.py enrich <md> --terms x` edits a doc, and `route.py fixtures --add` writes a file;
  * a deny rule for `--output` was beaten by `--outp''ut=`, because the permission check reads
    the command text while git reads the argument the shell produced.

⭐ THIS SCRIPT SEES THE ARGUMENTS AFTER THE SHELL HAS JOINED THE QUOTES, so `--outp''ut` arrives
here as `--output` and is refused. An argument that ABBREVIATES a refused option is refused too:
`git log` rejects `--outp=` (measured 2026-09-25, rc 128), but git commands built on
parse-options accept any unambiguous abbreviation, and this check should not depend on which
parser a subcommand happens to use.

What it allows, and nothing else:
  * git: the subcommands show, log, ls-tree, merge-base, rev-parse, and `cat-file -p`, run with
    `--no-pager`, in the workspace repo or in a repo `orchdoc.citable_repos()` finds (`-C`);
  * gh: `pr view <n> --repo <your-github-user>/<a repo on this machine> --json <fields>`, never `--web`;
  * route: route.py's ask path only (`cmd_ask`), never `enrich` or `fixtures`, with its usage log
    pointed at a temp file.
Every subprocess gets `creationflags=no_window.flags()` and a timeout.

⚠️ It is the lane's only shell, so it is what makes the lane read-only in practice. It is not a
sandbox: Read, Grep and Glob still read any file, which is the point of a review.
"""
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
try:
    from no_window import flags
except ImportError:  # a partial delivery must not take the tool down
    def flags(extra=0):
        return extra

WORKSPACE = HERE.parent.parent          # .shared/scripts -> .shared -> the workspace
GIT_SUBCOMMANDS = ("show", "log", "ls-tree", "merge-base", "rev-parse", "cat-file")
# Long options that write, run a program, or move git somewhere else. An argument naming any of
# them - in full, abbreviated, or with a suffix - is refused.
REFUSED_LONG = ("output", "exec-path", "git-dir", "work-tree", "ext-diff", "textconv",
                "config-env", "namespace", "super-prefix", "open-files-in-pager")
# Exact long options that happen to be a prefix of a refused one and are safe as themselves:
# `--text` (treat files as text) is not `--textconv`. git matches an exact name before an
# abbreviation, so these mean what they say.
SAFE_EXACT = ("text",)
GH_OWNER = "<your-github-user>"
GIT_TIMEOUT = 60
GH_TIMEOUT = 60
ROUTE_TIMEOUT = 180
OUTPUT_CAP = 400_000                    # bytes of a command's output passed back to the lane


class Refused(Exception):
    """A command this wrapper will not run, with the reason."""


# ------------------------------------------------------------------------------ repos

_REPOS = {}


def citable_repos():
    """Repo roots `-C` may name: orchdoc.citable_repos(), plus the workspace itself."""
    if "citable" not in _REPOS:
        found = [WORKSPACE]
        try:
            import orchdoc                           # heavy: only loaded when needed
            found += list(orchdoc.citable_repos())
        except Exception:                            # noqa: BLE001 - the workspace still works
            pass
        _REPOS["citable"] = found
    return _REPOS["citable"]


def _same(a, b):
    try:
        return os.path.normcase(str(pathlib.Path(a).resolve())) == \
            os.path.normcase(str(pathlib.Path(b).resolve()))
    except OSError:
        return False


def github_name(repo):
    """`<your-github-user>/<name>` from a repo's origin remote, or None."""
    try:
        p = subprocess.run(["git", "-C", str(repo), "remote", "get-url", "origin"],
                           capture_output=True, text=True, timeout=15, creationflags=flags())
    except Exception:                                # noqa: BLE001
        return None
    m = re.search(r"github\.com[:/]+(%s)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$" % GH_OWNER,
                  (p.stdout or "").strip(), re.I)
    return "%s/%s" % (GH_OWNER, m.group(2)) if (p.returncode == 0 and m) else None


def known_repos():
    """[(path, "<your-github-user>/<name>")] - every citable repo with a GitHub remote, worktrees left
    out. ONE list for the brief (which names them) and for `gh --repo` (which accepts them).

    A worktree is recognised by its `.git` being a FILE, not by where it sits: measured
    2026-09-25, `.wt-o10-cta-refund` beside the main checkout was listed as product-app
    ahead of the real clone, and the brief sent lanes to a stray worktree."""
    if "known" not in _REPOS:
        out, seen = [], set()
        for r in citable_repos():
            if ".worktrees" in pathlib.Path(r).parts or not (pathlib.Path(r) / ".git").is_dir():
                continue
            name = github_name(r)
            if name and name.lower() not in seen:
                seen.add(name.lower())
                out.append((pathlib.Path(r), name))
        _REPOS["known"] = out
    return _REPOS["known"]


# ------------------------------------------------------------------------------ git

def _long_name(arg):
    return arg[2:].split("=", 1)[0].lower()


def check_git(args):
    """-> (repo, subcommand args). Raises Refused."""
    args = list(args)
    repo = WORKSPACE
    if args[:1] == ["-C"]:
        if len(args) < 2:
            raise Refused("-C needs a repo path")
        want = args[1]
        hit = [r for r in citable_repos() if _same(r, want)]
        if not hit:
            raise Refused("-C %s is not the workspace or a repo orchdoc.citable_repos() finds"
                          % want)
        repo, args = hit[0], args[2:]
    if not args:
        raise Refused("no git subcommand")
    sub, rest = args[0], args[1:]
    if sub not in GIT_SUBCOMMANDS:
        raise Refused("git %s is not allowed; only %s" % (sub, ", ".join(GIT_SUBCOMMANDS)))
    # Exactly `-p <object>`: `-p --batch` and `-p --filters` are refused HERE, not left to git's
    # own argument check, which a future git could loosen (third review of 36a82e7f).
    if sub == "cat-file" and (len(rest) != 2 or rest[0] != "-p" or rest[1].startswith("-")):
        raise Refused("only `git cat-file -p <object>` is allowed")
    for a in rest:
        low = a.lower()
        if low == "-c" or (low.startswith("-c") and not low.startswith("--")):
            raise Refused("%s is refused" % a)
        if low.startswith("--") and len(low) > 2:
            name = _long_name(a)
            if name in SAFE_EXACT:
                continue
            for bad in REFUSED_LONG:
                if name.startswith(bad) or bad.startswith(name):
                    raise Refused("%s is refused (it is, or abbreviates, --%s)" % (a, bad))
    return repo, [sub] + rest


def run_git(args):
    repo, cmd = check_git(args)
    env = dict(os.environ, GIT_PAGER="cat", PAGER="cat", GIT_TERMINAL_PROMPT="0",
               GIT_OPTIONAL_LOCKS="0")
    env.pop("GIT_EXTERNAL_DIFF", None)
    full = ["git", "--no-pager", "-c", "core.fsmonitor=false", "-C", str(repo)] + cmd
    return _run(full, env, GIT_TIMEOUT)


# ------------------------------------------------------------------------------ gh

def check_gh(args):
    """-> the gh argv. Raises Refused. Exactly `pr view <n> --repo <known> --json <fields>`."""
    args = list(args)
    if args[:2] != ["pr", "view"] or len(args) < 3 or not re.fullmatch(r"\d+", args[2]):
        raise Refused("only `gh pr view <number> --repo %s/<repo> --json <fields>`" % GH_OWNER)
    opts, i = {}, 3
    while i < len(args):
        a = args[i]
        if a in ("--repo", "-R", "--json") and i + 1 < len(args):
            opts["--repo" if a in ("--repo", "-R") else "--json"] = args[i + 1]
            i += 2
            continue
        raise Refused("gh option %s is not allowed" % a)
    names = set(n.lower() for _p, n in known_repos())
    repo = opts.get("--repo", "")
    if repo.lower() not in names:
        raise Refused("--repo %s is not a repo on this machine (%s)"
                      % (repo or "(missing)", ", ".join(sorted(names)) or "none found"))
    fields = opts.get("--json", "")
    if not re.fullmatch(r"[A-Za-z]+(?:,[A-Za-z]+)*", fields):
        raise Refused("--json needs a comma-separated list of field names")
    return ["gh", "pr", "view", args[2], "--repo", repo, "--json", fields]


def run_gh(args):
    env = dict(os.environ, GH_PROMPT_DISABLED="1", GH_PAGER="cat", NO_COLOR="1")
    return _run(check_gh(args), env, GH_TIMEOUT)


# ------------------------------------------------------------------------------ route

def run_route(terms):
    if not terms:
        raise Refused("route needs search terms")
    # A child process, for the timeout; it calls route.cmd_ask directly (never route.main,
    # which is where `enrich` and `fixtures` are dispatched).
    return _run([sys.executable, str(pathlib.Path(__file__).resolve()), "--route-inner",
                 json.dumps(list(terms))], dict(os.environ), ROUTE_TIMEOUT)


def _route_inner(payload):
    import argparse
    import route
    route.USAGE_LOG = pathlib.Path(tempfile.gettempdir()) / "sweep-read-route-usage.jsonl"
    terms = [str(t) for t in json.loads(payload)]
    return route.cmd_ask(argparse.Namespace(terms=terms)) or 0


# ------------------------------------------------------------------------------ plumbing

def _run(cmd, env, timeout):
    try:
        p = subprocess.run(cmd, env=env, capture_output=True, timeout=timeout,
                           creationflags=flags())
    except subprocess.TimeoutExpired:
        print("sweep_read: timed out after %ds" % timeout, file=sys.stderr)
        return 124
    except OSError as ex:
        print("sweep_read: could not run %s (%s)" % (cmd[0], ex), file=sys.stderr)
        return 127
    out = p.stdout or b""
    sys.stdout.buffer.write(out[:OUTPUT_CAP])
    if len(out) > OUTPUT_CAP:
        sys.stdout.write("\n[sweep_read: output cut at %d of %d bytes]\n" % (OUTPUT_CAP,
                                                                           len(out)))
    sys.stdout.flush()
    if p.stderr:
        sys.stderr.write(p.stderr.decode("utf-8", "replace")[:20000])
    return p.returncode


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--route-inner"] and len(argv) == 2:
        return _route_inner(argv[1])
    tool, rest = (argv[0], argv[1:]) if argv else ("", [])
    try:
        if tool == "git":
            return run_git(rest)
        if tool == "gh":
            return run_gh(rest)
        if tool == "route":
            return run_route(rest)
        raise Refused("the first word must be git, gh or route")
    except Refused as ex:
        print("sweep_read: refused - %s" % ex, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
