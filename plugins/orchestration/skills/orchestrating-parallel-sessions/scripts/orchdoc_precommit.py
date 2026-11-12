#!/usr/bin/env python3
"""
Pre-commit guard: an OrchDoc may only be committed to the canonical branch.

<!-- subject: orchestration -->

Ancestry lies after a squash-merge; content comparison lies once main moves. The branch
you are standing on right now lies about nothing.

WHAT IT DOES
------------
Refuses a commit that stages `ORCHESTRATOR-DECISIONS-*.md` while HEAD is not `main`,
and names the one command that does the right thing. Everything else is untouched: a
lane committing product code on its own branch never sees this.

  install:  python <scripts>/orchdoc_precommit.py --install
  test:     python <scripts>/orchdoc_precommit.py --selftest

A second mode, --refuse-shared-main, is its own line in the same hook: it refuses any commit on
the shared checkout's `main` (see refuse_shared_main).

NOTE ON DURABILITY: `.git/hooks/` is not versioned, so this does not survive a fresh
clone. `--install` is idempotent and belongs in the workspace setup path. The guard is
a backstop; `orchdoc.py commit` remains the road.

ASCII output only (Windows console rule). Exit 1 refuses the commit; exit 0 allows it.
"""

import os
import re
import subprocess
try:
    from no_window import flags
except ImportError:  # a partial delivery must not take the tool down
    def flags(extra=0):
        return extra
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    from workspace_paths import MAIN_CHECKOUT as PROJECTS
except ImportError:  # a partial delivery must not take the tool down
    PROJECTS = Path.home() / "<your-workspace>"


def _workspace_here():
    """The workspace when this tool is installed as a plugin, where workspace_paths cannot know
    it: $ORCHDOC_WORKSPACE, else the nearest folder above the current one that holds the settings
    file or a decision document, else the git repo the current folder is in. None when none."""
    env = os.environ.get("ORCHDOC_WORKSPACE", "").strip()
    if env and Path(env).is_dir():
        return Path(env)
    start = Path.cwd().resolve()
    for cand in [start] + list(start.parents):
        if (cand / ".orchdoc-config.json").exists() or any(cand.glob("ORCHESTRATOR-DECISIONS-*.md")):
            return cand
    p = subprocess.run(["git", "-C", str(start), "rev-parse", "--show-toplevel"],
                       capture_output=True, text=True, creationflags=flags())
    return Path(p.stdout.strip()) if p.returncode == 0 and p.stdout.strip() else None


# A workspace may have a git repo at PROJECTS. A plugin install has no such folder, and every
# git command here then asked about a folder that does not exist ("not a git repo").
if not (Path(PROJECTS) / ".git").exists():
    PROJECTS = _workspace_here() or PROJECTS
CANONICAL_BRANCH = "main"
HOOK_SCRIPTS = ((Path(PROJECTS) / ".shared" / "scripts")
                if (Path(PROJECTS) / ".shared" / "scripts").is_dir()
                else Path(__file__).resolve().parent).as_posix()
_S_LINE = 'S="%s"\n' % HOOK_SCRIPTS
VERDICT_FIRST = r"""# `python` below is this function. It holds a guard's output until the guard exits, so the
# exit code, which is the verdict, cannot be changed by a reader that stopped reading early.
trap '' PIPE
python() {
    # A guard script that is not installed (a setup that does not carry that feature) is skipped,
    # not reported as an error: "can't open file" on every commit teaches people to ignore output.
    case "$1" in */*.py) [ -f "$1" ] || return 0 ;; esac
    OUT=$(PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 command python "$@" 2>&1)
    RC=$?
    [ -n "$OUT" ] && printf '%s\n' "$OUT" >&2
    return $RC
}
"""

HOOK_BODY = """#!/bin/sh
# Installed by orchdoc_precommit.py - see that file for the reasoning.
# Each guard exits 9 to REFUSE. Any other non-zero is a crash and must NOT block the commit.
""" + _S_LINE + """TOP=$(git rev-parse --show-toplevel 2>/dev/null)

""" + VERDICT_FIRST + """
[ -f "$S/orchdoc_precommit.py" ] || { echo "[orchdoc pre-commit] $S/orchdoc_precommit.py is missing: this commit is refused. Restore the tools or remove this hook." >&2; exit 1; }
python "$S/orchdoc_precommit.py" --check || exit 1

python "$S/knowledge_gate.py" --check
[ $? -eq 9 ] && exit 1

python "$S/owns_guard.py" --check
[ $? -eq 9 ] && exit 1

# Reads the committing tree, because a workspace can have several checkouts.
python "$S/stale_overwrite_guard.py" "$TOP"
[ $? -eq 9 ] && exit 1

# Refuses a script that launches a process without creationflags. Reads the committing tree's index.
python "$S/check_no_window.py" --staged
[ $? -eq 9 ] && exit 1

# Refuses a commit on a shared checkout's `main`. Keys on the committing tree's PATH, so a
# worktree - which runs this same hook file - passes. The Bash-tool hook refuses the same commit
# earlier; this covers every other way of committing.
python "$S/orchdoc_precommit.py" --refuse-shared-main
[ $? -eq 9 ] && exit 1

exit 0
"""


def git(args, cwd=PROJECTS):
    try:
        p = subprocess.run(["git", "-C", str(cwd)] + args,
                           capture_output=True, text=True, timeout=20, creationflags=flags())
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except Exception:
        return 1, "", ""


def staged_orchdocs():
    rc, out, _ = git(["diff", "--cached", "--name-only"])
    if rc != 0:
        return []
    return [n for n in out.splitlines()
            if n.startswith("ORCHESTRATOR-DECISIONS-") and n.endswith(".md")]


def check():
    docs = staged_orchdocs()
    if not docs:
        return 0                      # not an OrchDoc commit: silent, always

    rc, branch, _ = git(["rev-parse", "--abbrev-ref", "HEAD"])
    if rc != 0:
        return 0                      # cannot tell: fail OPEN, never block on ignorance
    if branch == CANONICAL_BRANCH:
        return 0

    slug = docs[0].replace("ORCHESTRATOR-DECISIONS-", "").replace(".md", "")
    print("[orchdoc] REFUSED: an OrchDoc may only be committed to '%s'."
          % CANONICAL_BRANCH)
    print("          You are on '%s'." % branch)
    print()
    for d in docs:
        print("          staged: %s" % d)
    print()
    print("          A decision doc that exists in two states is not a decision doc -")
    print("          its whole value is being the ONE place that is current, and the human")
    print("          reads '%s'. A commit here is invisible to the human." % CANONICAL_BRANCH)
    print()
    print("          Land it on the canonical branch instead, from right here, with no")
    print("          checkout and nothing else swept in:")
    print()
    print("            python <scripts>/orchdoc.py commit --doc %s -m \"...\""
          % slug)
    print()
    print("          That builds on origin/%s, gates it, and fast-forward pushes."
          % CANONICAL_BRANCH)
    return 1



SHARED_MAIN_ESCAPE = "SHARED_MAIN_COMMIT_OK"
REFUSE = 9


def _same_dir(a, b):
    norm = lambda p: os.path.normcase(os.path.normpath(os.path.realpath(str(p))))  # noqa: E731
    try:
        return norm(a) == norm(b)
    except (OSError, ValueError):
        return False


def refuse_shared_main(cwd=None):
    """9 when this commit would land on the shared checkout's checked-out `main`, else 0.

    git runs a pre-commit hook from the top of the committing working tree, so the tree is
    read from the current directory - never from PROJECTS, which is the thing compared against.
    Any doubt allows: a guard that cannot tell must not block a commit.
    """
    cwd = cwd or os.getcwd()
    rc, top, _ = git(["rev-parse", "--show-toplevel"], cwd=cwd)
    if rc != 0 or not top or not _same_dir(top, PROJECTS):
        return 0
    rc, head, _ = git(["symbolic-ref", "-q", "HEAD"], cwd=cwd)
    if rc != 0 or head != "refs/heads/" + CANONICAL_BRANCH:
        return 0
    why = os.environ.get(SHARED_MAIN_ESCAPE, "").strip()
    if len(why) >= 10:
        print("[shared-main] ALLOWED by %s: %s" % (SHARED_MAIN_ESCAPE, why[:200]),
              file=sys.stderr)
        print("              This commit is on the shared checkout's `main`. Push it now, or"
              " the next delivery cannot move `main` and will rescue it to origin.",
              file=sys.stderr)
        return 0
    print("[shared-main] REFUSED: a commit on the SHARED checkout's `%s`." % CANONICAL_BRANCH,
          file=sys.stderr)
    print("              %s" % top, file=sys.stderr)
    print("", file=sys.stderr)
    print("    A commit here stays on this checkout's `main` and nowhere else. Nothing pushes it,",
          file=sys.stderr)
    print("    and once `main` holds a commit origin does not, delivery can no longer move it",
          file=sys.stderr)
    print("    forward.",
          file=sys.stderr)
    print("", file=sys.stderr)
    print("    Commit in YOUR worktree instead, where the only files are yours:", file=sys.stderr)
    print("      git worktree add <path> -b <branch>", file=sys.stderr)
    print("    In a cloud session, commit on the session's own branch.",
          file=sys.stderr)
    print("    An OrchDoc lands from anywhere with:", file=sys.stderr)
    print("      python <scripts>/orchdoc.py commit --doc o<N> --commit", file=sys.stderr)
    print("", file=sys.stderr)
    print("    Genuinely need this commit here, and will push it yourself right now? Put the",
          file=sys.stderr)
    print("    reason on the command:  %s=\"<why>\" git commit ..." % SHARED_MAIN_ESCAPE,
          file=sys.stderr)
    return REFUSE


PRODUCT_GUARDS = ("knowledge_gate.py", "owns_guard.py", "stale_overwrite_guard.py")
GUARD_SCRIPTS = (("orchdoc_precommit.py",) + PRODUCT_GUARDS
                 + ("check_no_window.py", "--refuse-shared-main"))

# Every guard the hook names must exist, which `--check-hook` tests.
OPTIONAL_GUARDS = ()


def _drop_missing_optional(body):
    for g in OPTIONAL_GUARDS:
        if not (Path(HOOK_SCRIPTS) / g).is_file():
            body = re.sub(r'(?:#[^\n]*\n)*python "\$S/%s"[^\n]*\n\[ \$\? -eq 9 \] && exit 1\n\n?'
                          % re.escape(g), "", body)
    # Refusing a commit on the shared checkout's main is a rule for a workspace with
    # several checkouts. In a plain workspace it would refuse every ordinary commit on main.
    if not HOOK_SCRIPTS.endswith("/.shared/scripts"):
        body = re.sub(r'(?:#[^\n]*\n)*python "\$S/orchdoc_precommit\.py" --refuse-shared-main\n'
                      r'\[ \$\? -eq 9 \] && exit 1\n\n?', "", body)
    return body


HOOK_BODY = _drop_missing_optional(HOOK_BODY)
GUARD_SCRIPTS = tuple(g for g in GUARD_SCRIPTS
                      if (g not in OPTIONAL_GUARDS and g != "--refuse-shared-main")
                      or g in HOOK_BODY)

PRODUCT_HOOK_BODY = """#!/bin/sh
# Installed by orchdoc_precommit.py --install-all
# Each guard exits 9 to REFUSE. Any other non-zero is a crash and must NOT block the commit.
# orchdoc_precommit --check is absent on purpose: it is pinned to the workspace repo and would
# answer for the wrong index here.
""" + _S_LINE + """TOP=$(git rev-parse --show-toplevel 2>/dev/null)

""" + VERDICT_FIRST + """
python "$S/knowledge_gate.py" --check
[ $? -eq 9 ] && exit 1

python "$S/owns_guard.py" --check
[ $? -eq 9 ] && exit 1

python "$S/stale_overwrite_guard.py" "$TOP"
[ $? -eq 9 ] && exit 1

exit 0
"""


def _same(a, b):
    """Line-ending and trailing-whitespace insensitive. CRLF is not drift."""
    norm = lambda s: [l.rstrip() for l in s.replace("\r\n", "\n").strip().split("\n")]  # noqa: E731
    return norm(a) == norm(b)


def _missing_guards(hook_text):
    return [g for g in GUARD_SCRIPTS if g not in hook_text]


def sibling_repos():
    """Every git REPO directly under the workspace - not its worktrees.
    """
    out = []
    for child in sorted(PROJECTS.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if (child / ".git").is_dir():
            out.append(child)
    return out


def memory_repo():
    """The notes repo, which `sibling_repos()` structurally CANNOT reach.

    It takes PRODUCT_HOOK_BODY, not HOOK_BODY, for the reason recorded on PRODUCT_GUARDS:
    `orchdoc_precommit --check` pins `git()` to `cwd=PROJECTS`, so run from here it would
    inspect the WORKSPACE's index and answer for the wrong repository. The other three guards
    each resolve their own repo, so they travel correctly.
    """
    try:                              # lazy: --check runs on every commit and never needs this
        from workspace_paths import MEM
    except Exception:
        return []
    p = Path(MEM)
    return [p] if (p / ".git").is_dir() else []


def _rows():
    """(repo, hook body) for EVERY repo the installer covers - the one list, built once.
    """
    return ([(PROJECTS, HOOK_BODY)]
            + [(r, PRODUCT_HOOK_BODY) for r in sibling_repos()]
            + [(r, PRODUCT_HOOK_BODY) for r in memory_repo()])


def _invoked_guards(text):
    """Guards the shell will actually RUN, from `python .../<guard>` lines only.
    """
    return [g for g in GUARD_SCRIPTS
            if re.search(r"^\s*python\s+[^\n]*%s" % re.escape(g), text, re.M)]


def _missing_files(hook_text):
    """Guard files the hook runs that are not in the scripts folder its `S=` line names. The hook
    skips a guard that is absent (a setup without that feature), so a hook can read as installed
    while a guard never runs; this is the check that says so."""
    m = re.search(r'^S="([^"]*)"', hook_text, re.M)
    folder = Path(m.group(1)) if m else Path(HOOK_SCRIPTS)
    return [g for g in _invoked_guards(hook_text)
            if not g.startswith("--") and not (folder / g).is_file()]


def _hooks_dir(repo):
    rc, gitdir, _ = git(["rev-parse", "--git-dir"], cwd=repo)
    if rc != 0:
        return None
    p = Path(gitdir)
    return (p / "hooks") if p.is_absolute() else (Path(repo) / gitdir / "hooks")


def _audit_one(repo, body):
    """(status, detail) for one repo's pre-commit hook."""
    hooks = _hooks_dir(repo)
    if hooks is None:
        return "skip", "not a git repo"
    target = hooks / "pre-commit"
    if not target.exists():
        return "missing", "no pre-commit hook"
    cur = target.read_text(encoding="utf-8", errors="replace")
    want = _invoked_guards(body)
    invoked = _invoked_guards(cur)
    checks = len(re.findall(r"^\[ \$\? -eq 9 \]", cur, re.M))
    if len(invoked) < len(want):
        return "broken", "runs %d of %d guard(s); %d dangling check(s). missing: %s" % (
            len(invoked), len(want), checks - len(invoked),
            ", ".join(g for g in want if g not in invoked))
    absent = _missing_files(cur)
    if absent:
        return "broken", "names guard file(s) that are not on disk: %s" % ", ".join(absent)
    if not _same(cur, body):
        return "differs", "runs every guard but is not the template"
    return "ok", "%d guard(s)" % len(want)


def check_hook(all_repos=False):
    """Report whether the hook that RUNS matches the template that would be installed.
    """
    if all_repos:
        bad = 0
        rows = _rows()
        for repo, body in rows:
            status, detail = _audit_one(repo, body)
            if status in ("broken", "missing"):
                bad += 1
            print("  [%-7s] %-42s %s" % (status, repo.name, detail))
        print()
        if bad:
            print("[REFUSE] %d repo(s) are missing guards." % bad)
            print("         Fix: python <scripts>/orchdoc_precommit.py --install-all")
            return 1
        print("[ok] every repo runs its full guard chain.")
        return 0
    return _check_hook_one()


def _check_hook_one():
    """Report whether the hook that RUNS matches the template that would be installed.
    """
    rc, gitdir, _ = git(["rev-parse", "--git-dir"])
    if rc != 0:
        print("not a git repo: %s" % PROJECTS, file=sys.stderr)
        return 2
    hooks = (PROJECTS / gitdir / "hooks") if not os.path.isabs(gitdir) \
        else (Path(gitdir) / "hooks")
    target = hooks / "pre-commit"
    if not target.exists():
        print("[REFUSE] no pre-commit hook at %s - run --install" % target)
        return 1
    cur = target.read_text(encoding="utf-8", errors="replace")
    missing = _missing_guards(cur)
    if missing:
        print("[REFUSE] the installed hook is missing: %s" % ", ".join(missing))
        print("         %s" % target)
        print("         Fix: python <scripts>/orchdoc_precommit.py --install")
        return 1
    absent = _missing_files(cur)
    if absent:
        print("[REFUSE] the installed hook runs guard file(s) that are not on disk: %s"
              % ", ".join(absent))
        print("         %s" % target)
        return 1
    if not _same(cur, HOOK_BODY):
        print("[note] the installed hook runs every guard but differs from the template.")
        print("       %s" % target)
        return 0
    print("[ok] the installed hook matches the template; %d guard(s) chained."
          % len(GUARD_SCRIPTS))
    return 0


def _backup(target, cur):
    """Save `cur`, the hook about to be replaced, beside `target`, never over an earlier backup.
    """
    backup = target.with_suffix(".pre-orchdoc")
    if backup.exists():
        if backup.read_text(encoding="utf-8", errors="replace") == cur:
            return backup
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = target.with_name("%s.pre-orchdoc.%s" % (target.name, stamp))
        n = 1
        while backup.exists():
            n += 1
            backup = target.with_name("%s.pre-orchdoc.%s-%d" % (target.name, stamp, n))
    backup.write_text(cur, encoding="utf-8")
    return backup


def install_one(repo, body):
    """Write `body` as repo's pre-commit hook, backing up anything different. (status, detail)."""
    hooks = _hooks_dir(repo)
    if hooks is None:
        return "skip", "not a git repo"
    hooks.mkdir(parents=True, exist_ok=True)
    target = hooks / "pre-commit"
    detail = "installed"
    if target.exists():
        cur = target.read_text(encoding="utf-8", errors="replace")
        if _same(cur, body):
            return "ok", "already current"
        status, why = _audit_one(repo, body)
        backup = _backup(target, cur)
        detail = "replaced (%s); previous saved as %s" % (why, backup.name)
    target.write_text(body, encoding="utf-8", newline="\n")
    try:
        os.chmod(str(target), 0o755)
    except OSError:
        pass
    return "written", detail


def install_all():
    """The workspace hook here, the product hook in every sibling repo.
    """
    rows = _rows()
    for repo, body in rows:
        status, detail = install_one(repo, body)
        print("  [%-8s] %-42s %s" % (status, repo.name, detail))
    print()
    print("  %d repo(s). Verify with:  orchdoc_precommit.py --check-hook --all" % len(rows))
    return 0


def install():
    rc, gitdir, _ = git(["rev-parse", "--git-dir"])
    if rc != 0:
        print("not a git repo: %s" % PROJECTS, file=sys.stderr)
        return 2
    hooks = (PROJECTS / gitdir / "hooks") if not os.path.isabs(gitdir) \
        else (Path(gitdir) / "hooks")
    hooks.mkdir(parents=True, exist_ok=True)
    target = hooks / "pre-commit"
    if target.exists():
        cur = target.read_text(encoding="utf-8", errors="replace")
        if "orchdoc_precommit" in cur:
            if _same(cur, HOOK_BODY):
                print("[ok] already installed and current: %s" % target)
                return 0
            missing = _missing_guards(cur)
            print("[note] the installed hook DIFFERS from the template.")
            if missing:
                print("       it is missing: %s" % ", ".join(missing))
            backup = _backup(target, cur)
            print("[note] previous hook backed up to %s" % backup.name)
        else:
            backup = _backup(target, cur)
            print("[note] existing pre-commit backed up to %s" % backup.name)
    target.write_text(HOOK_BODY, encoding="utf-8")
    try:
        os.chmod(str(target), 0o755)
    except OSError:
        pass
    print("[ok] installed: %s" % target)
    print("     OrchDoc commits are now refused on any branch but '%s'."
          % CANONICAL_BRANCH)
    if "--refuse-shared-main" in HOOK_BODY:
        print("     Any commit on the shared checkout's '%s' is refused (--refuse-shared-main)."
              % CANONICAL_BRANCH)
    return 0


PREPUSH_BODY = """#!/bin/sh
# Installed by orchdoc_precommit.py --install-prepush
# Only exit 9 from the check refuses a push; anything else is a crash and must not stop it.
""" + _S_LINE + """[ -f "$S/orchdoc_putback.py" ] || exit 0
PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 python "$S/orchdoc_putback.py" --pre-push
[ $? -eq 9 ] && exit 1
exit 0
"""


def install_prepush():
    """Write the workspace repo's pre-push hook, saving a copy of anything different there."""
    rc, common, _ = git(["rev-parse", "--git-common-dir"])
    if rc != 0:
        print("not a git repo: %s" % PROJECTS, file=sys.stderr)
        return 2
    common = Path(common)
    hooks = (common if common.is_absolute() else Path(PROJECTS) / common) / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    target = hooks / "pre-push"
    if target.exists():
        cur = target.read_text(encoding="utf-8", errors="replace")
        if _same(cur, PREPUSH_BODY):
            print("[ok] pre-push already current: %s" % target)
            return 0
        print("[note] previous pre-push saved as %s" % _backup(target, cur).name)
    target.write_text(PREPUSH_BODY, encoding="utf-8", newline="\n")
    try:
        os.chmod(str(target), 0o755)
    except OSError:
        pass
    print("[ok] installed: %s" % target)
    return 0


def selftest():
    """Assert the two behaviours that matter, without touching the real index."""
    ok = True
    rc, branch, _ = git(["rev-parse", "--abbrev-ref", "HEAD"])
    print("orchdoc_precommit selftest   (HEAD is '%s')" % branch)

    staged = staged_orchdocs()
    a = (staged == [] or branch == CANONICAL_BRANCH or check() == 1)
    print("  [%s] refuses an OrchDoc commit off '%s'"
          % ("OK" if a else "FAIL", CANONICAL_BRANCH))
    ok &= a

    # A commit with no OrchDoc staged must always pass, whatever the branch.
    b = (staged_orchdocs() == [] and check() == 0) or staged != []
    print("  [%s] silent when no OrchDoc is staged" % ("OK" if b else "FAIL"))
    ok &= b

    print("\nselftest %s" % ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        arg = sys.argv[1] if len(sys.argv) > 1 else "--check"
        if arg == "--install":
            rc_one = install()
            install_prepush()
            sys.exit(rc_one)
        if arg == "--install-all":
            rc_all = install_all()
            install_prepush()
            sys.exit(rc_all)
        if arg == "--install-prepush":
            sys.exit(install_prepush())
        if arg == "--check-hook":
            sys.exit(check_hook(all_repos="--all" in sys.argv))
        if arg == "--selftest":
            sys.exit(selftest())
        if arg == "--refuse-shared-main":
            sys.exit(refuse_shared_main())
        sys.exit(check())
    except Exception:
        sys.exit(0)   # fail open: a broken guard must never block a commit
