#!/usr/bin/env python3
"""
Pre-commit guard: an OrchDoc may only be committed to the canonical branch.

WHY PREVENTION, NOT DETECTION
-----------------------------
o7, 2026-08-06: "Detection asks 'did this doc end up somewhere bad?' - a question about
HISTORY, where both ancestry and content comparison lie. Prevention asks 'am I about to
write this doc in a bad place?' - a question about the PRESENT, which is not ambiguous."

Ancestry lies after a squash-merge; content comparison lies once main moves. The branch
you are standing on right now lies about nothing.

WHY IT IS NEEDED AT ALL, given `orchdoc.py commit` already lands on main
------------------------------------------------------------------------
`orchdoc.py commit` builds on origin/main via plumbing and fast-forward pushes, so it is
correct from ANY checked-out branch. Nothing forces anyone to use it. On 2026-08-06 o1 -
the session that had just spent a day proving the two-states problem and had personally
landed 174 uncommitted lines - committed its 189-line audit report to a feature branch
with a plain `git commit`, within the hour. the human never saw it.

Measured context, so the scale is honest: 216 OrchDoc commits reached `main` correctly
and 14 went to a branch created 2026-08-01. This is a five-day regression, not a
long-standing structural failure - but the 14 include the audit report about the very
problem, so the failure rate does not describe the damage.

WHAT IT DOES
------------
Refuses a commit that stages `ORCHESTRATOR-DECISIONS-*.md` while HEAD is not `main`,
and names the one command that does the right thing. Everything else is untouched: a
lane committing product code on its own branch never sees this.

  install:  python .shared/scripts/orchdoc_precommit.py --install
  test:     python .shared/scripts/orchdoc_precommit.py --selftest

NOTE ON DURABILITY: `.git/hooks/` is not versioned, so this does not survive a fresh
clone. `--install` is idempotent and belongs in the workspace setup path. The guard is
a backstop; `orchdoc.py commit` remains the road.

ASCII output only (Windows console rule). Exit 1 refuses the commit; exit 0 allows it.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PROJECTS = Path(r"<your-workspace>")
CANONICAL_BRANCH = "main"
# ⛔ THIS BODY HAD DRIFTED BEHIND THE HOOK ACTUALLY ON DISK, and the drift was invisible because
# `install()` sees the string "orchdoc_precommit" in the existing file and returns "already
# installed" without comparing anything. The live hook had been hand-extended to chain
# knowledge_gate and owns_guard; this template still installed one guard. So a FRESH CLONE got a
# hook missing two of the three - and nothing would ever have said so.
#
# ⭐ A TEMPLATE THAT IS ONLY CONSULTED WHEN THE TARGET IS ABSENT IS NEVER CHECKED AGAINST IT.
# That is the same shape as a hand-kept index: the copy people read and the copy that runs drift
# apart silently, and the one that runs wins. `--check-hook` below now compares them.
HOOK_BODY = """#!/bin/sh
# Installed by .shared/scripts/orchdoc_precommit.py - see that file for the reasoning.
# Each guard exits 9 to REFUSE. Any other non-zero is a crash and must NOT block the commit.
S="<your-workspace>/.shared/scripts"
TOP=$(git rev-parse --show-toplevel 2>/dev/null)

python "$S/orchdoc_precommit.py" --check || exit 1

python "$S/knowledge_gate.py" --check
[ $? -eq 9 ] && exit 1

python "$S/owns_guard.py" --check
[ $? -eq 9 ] && exit 1

# ⭐ TOP, not the hardcoded path: this one reads the index of whichever worktree is committing,
# and the workspace has several. Passing the shared checkout would answer for the wrong tree.
python "$S/stale_overwrite_guard.py" "$TOP"
[ $? -eq 9 ] && exit 1

exit 0
"""


def git(args, cwd=PROJECTS):
    try:
        p = subprocess.run(["git", "-C", str(cwd)] + args,
                           capture_output=True, text=True, timeout=20)
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
    print("          reads '%s'. A commit here is invisible to him." % CANONICAL_BRANCH)
    print()
    print("          Land it on the canonical branch instead, from right here, with no")
    print("          checkout and nothing else swept in:")
    print()
    print("            python .shared/scripts/orchdoc.py commit --doc %s -m \"...\""
          % slug)
    print()
    print("          That builds on origin/%s, gates it, and fast-forward pushes."
          % CANONICAL_BRANCH)
    return 1


# ⛔ THE PRODUCT REPOS HAD A HOOK AND IT WAS BROKEN IN ALL SEVENTEEN, identically. Measured
# 2026-09-08: every one carried ONE `python ...` line and THREE `[ $? -eq 9 ] && exit 1` lines
# testing the exit status of nothing. `product-app`' copy opens mid-sentence - a comment
# whose first half is missing - so whatever wrote them wrote partial lines.
#
# ⭐ EACH HOOK LOOKED PLAUSIBLE ON ITS OWN. It has a shebang, python, the right exit convention;
# only counting invocations against dangling checks shows three guards were meant to run and
# two never did. **A guard that is installed and not running reads, from every direction, like a
# guard that is running** - which is why `--check-hook --all` counts rather than eyeballs.
#
# ⚠️ `orchdoc_precommit --check` IS NOT IN THE PRODUCT CHAIN, deliberately. Its `git()` is pinned
# to `cwd=PROJECTS`, so run from a product repo it inspects the WORKSPACE's index and answers
# for the wrong repository. `knowledge_gate`, `owns_guard` and `stale_overwrite_guard` all
# resolve their own repo via `git rev-parse --show-toplevel`, so they travel correctly.
PRODUCT_GUARDS = ("knowledge_gate.py", "owns_guard.py", "stale_overwrite_guard.py")
GUARD_SCRIPTS = ("orchdoc_precommit.py",) + PRODUCT_GUARDS

PRODUCT_HOOK_BODY = """#!/bin/sh
# Installed by .shared/scripts/orchdoc_precommit.py --install-all
# Each guard exits 9 to REFUSE. Any other non-zero is a crash and must NOT block the commit.
# orchdoc_precommit --check is absent on purpose: it is pinned to the workspace repo and would
# answer for the wrong index here.
S="<your-workspace>/.shared/scripts"
TOP=$(git rev-parse --show-toplevel 2>/dev/null)

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

    ⛔ A WORKTREE HAS A `.git` FILE; A REPO HAS A `.git` DIRECTORY. The first version of this
    tested `.exists()` and returned seven `wt-o10L*` worktrees as repos needing their own hook.
    They share the parent repository's `.git/hooks`, so installing there would have been
    meaningless and reporting them missing was noise. Same structural test `dev_docs_index.py`
    uses to prune phantom index rows.
    """
    out = []
    for child in sorted(PROJECTS.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if (child / ".git").is_dir():
            out.append(child)
    return out


def _invoked_guards(text):
    """Guards the shell will actually RUN, from `python .../<guard>` lines only.

    ⛔ THE FIRST VERSION ASKED `if g in body`, AND THE PRODUCT TEMPLATE'S OWN COMMENT SAYS
    "orchdoc_precommit --check is absent on purpose". So the audit read a sentence explaining an
    absence as evidence of a presence, and reported every product repo as missing a guard it was
    never meant to run. **That is the description-versus-instance error, inside the check written
    to stop eyeballing hooks** - the eighth instance this workspace has recorded, and the second
    today. `mentions.executable_part` exists for exactly this and I did not reach for it.
    """
    # ⚠️ Deliberately loose about the PATH and strict about the LINE. Hooks in the wild spell the
    # script as "$S/x.py" or as a full absolute path, and a pattern that pinned the prefix
    # matched neither - the first attempt made `S` mandatory and reported 0 of 3 everywhere.
    # What has to be true is only that a shell line RUNS it.
    return [g for g in GUARD_SCRIPTS
            if re.search(r"^\s*python\s+[^\n]*%s" % re.escape(g), text, re.M)]


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
    # ⭐ COUNT THE DANGLING CHECKS. `[ $? -eq 9 ]` with no command above it tests the previous
    # line's status, which is how seventeen hooks read as installed while two thirds of their
    # guards were absent.
    checks = len(re.findall(r"^\[ \$\? -eq 9 \]", cur, re.M))
    if len(invoked) < len(want):
        return "broken", "runs %d of %d guard(s); %d dangling check(s). missing: %s" % (
            len(invoked), len(want), checks - len(invoked),
            ", ".join(g for g in want if g not in invoked))
    if not _same(cur, body):
        return "differs", "runs every guard but is not the template"
    return "ok", "%d guard(s)" % len(want)


def check_hook(all_repos=False):
    """Report whether the hook that RUNS matches the template that would be installed.

    ⭐ The template is only consulted when the target is absent, so without this nothing ever
    compares them and the two drift apart in silence - with the running copy winning.
    """
    if all_repos:
        bad = 0
        rows = [(PROJECTS, HOOK_BODY)] + [(r, PRODUCT_HOOK_BODY) for r in sibling_repos()]
        for repo, body in rows:
            status, detail = _audit_one(repo, body)
            if status in ("broken", "missing"):
                bad += 1
            print("  [%-7s] %-42s %s" % (status, repo.name, detail))
        print()
        if bad:
            print("[REFUSE] %d repo(s) are missing guards." % bad)
            print("         Fix: python .shared/scripts/orchdoc_precommit.py --install-all")
            return 1
        print("[ok] every repo runs its full guard chain.")
        return 0
    return _check_hook_one()


def _check_hook_one():
    """Report whether the hook that RUNS matches the template that would be installed.

    ⭐ The template is only consulted when the target is absent, so without this nothing ever
    compares them and the two drift apart in silence - with the running copy winning.
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
        print("         Fix: python .shared/scripts/orchdoc_precommit.py --install")
        return 1
    if not _same(cur, HOOK_BODY):
        print("[note] the installed hook runs every guard but differs from the template.")
        print("       %s" % target)
        return 0
    print("[ok] the installed hook matches the template; %d guard(s) chained."
          % len(GUARD_SCRIPTS))
    return 0


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
        backup = target.with_suffix(".pre-orchdoc")
        backup.write_text(cur, encoding="utf-8")
        detail = "replaced (%s); previous saved as %s" % (why, backup.name)
    target.write_text(body, encoding="utf-8", newline="\n")
    try:
        os.chmod(str(target), 0o755)
    except OSError:
        pass
    return "written", detail


def install_all():
    """The workspace hook here, the product hook in every sibling repo.

    ⭐ ONE INSTALLER FOR EVERY REPO, because the seventeen broken hooks were the cost of not
    having one: each was written separately, three of them lost guard invocations, and no
    command existed that could have said so.
    """
    rows = [(PROJECTS, HOOK_BODY)] + [(r, PRODUCT_HOOK_BODY) for r in sibling_repos()]
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
            # ⛔ "ALREADY INSTALLED" USED TO END HERE, AND THAT IS HOW THE TEMPLATE DRIFTED.
            # The live hook had been hand-extended with two more guards while HOOK_BODY still
            # installed one, and nothing compared them - so a fresh clone got a weaker hook than
            # this machine had, silently, for weeks. Same shape as a hand-kept index: two copies
            # of one fact, only one of them running.
            if _same(cur, HOOK_BODY):
                print("[ok] already installed and current: %s" % target)
                return 0
            missing = _missing_guards(cur)
            print("[note] the installed hook DIFFERS from the template.")
            if missing:
                print("       it is missing: %s" % ", ".join(missing))
            backup = target.with_suffix(".pre-orchdoc")
            backup.write_text(cur, encoding="utf-8")
            print("[note] previous hook backed up to %s" % backup.name)
        else:
            backup = target.with_suffix(".pre-orchdoc")
            backup.write_text(cur, encoding="utf-8")
            print("[note] existing pre-commit backed up to %s" % backup.name)
    target.write_text(HOOK_BODY, encoding="utf-8")
    try:
        os.chmod(str(target), 0o755)
    except OSError:
        pass
    print("[ok] installed: %s" % target)
    print("     OrchDoc commits are now refused on any branch but '%s'."
          % CANONICAL_BRANCH)
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
            sys.exit(install())
        if arg == "--install-all":
            sys.exit(install_all())
        if arg == "--check-hook":
            sys.exit(check_hook(all_repos="--all" in sys.argv))
        if arg == "--selftest":
            sys.exit(selftest())
        sys.exit(check())
    except Exception:
        sys.exit(0)   # fail open: a broken guard must never block a commit
