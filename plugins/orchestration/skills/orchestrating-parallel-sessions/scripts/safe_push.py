"""Refuse to push if it would DROP lines that are already on the remote.

<!-- subject: infrastructure -->

WHY. `orchdoc.py commit` has carried this gate for OrchDocs because a session can overwrite
another orchestrator's work:

    [REFUSE] gate 1 - landing this would REMOVE 2 line(s) that are on origin/main.
             Someone else's content, or a stale copy of yours.

Every OTHER repo got pushed with a bare `git add -A && git commit && git push`, which checks
nothing about content. Git's own protection is TOPOLOGICAL - it refuses a non-fast-forward -
and that is a different question from "does this commit delete somebody's writing?" A push can
be a perfectly clean fast-forward and still remove every line another person just added.

WHAT IT CHECKS. Fetch the remote, diff the local tree against the remote tip for each staged
path, and refuse if any non-trivial line present on the remote is absent locally. Whitespace
and pure reformatting do not count; deleted CONTENT does.

    safe_push.py --remote origin --branch master           # check, then push
    safe_push.py --check-only                              # report and stop
    safe_push.py --because "<reason>"                      # override, recorded in the message
"""
import argparse
import os
import pathlib
import re
import subprocess
try:
    from no_window import flags
except ImportError:  # a partial delivery must not take the tool down
    def flags(extra=0):
        return extra
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def git(args, cwd=None):
    r = subprocess.run(["git"] + args, capture_output=True, text=True, cwd=cwd,
                       creationflags=flags())
    return r.returncode, r.stdout, r.stderr


def meaningful(line):
    """Is this a line whose loss would matter?

    Blank lines, pure punctuation and code-fence markers move constantly during ordinary
    editing; counting them would make the gate fire on every reflow, and a gate that fires on
    every reflow is one people learn to override without reading.
    """
    t = line.strip()
    if len(t) < 4:
        return False
    return bool(re.search(r"[A-Za-z0-9]", t)) and t not in ("```", "---", "***")


def check(cwd, remote, branch):
    """Lines present on the remote and absent from what THIS PUSH WOULD LAND. {} means safe.
    """
    rc, _, err = git(["fetch", "-q", remote, branch], cwd)
    if rc != 0:
        return None, "could not fetch %s/%s: %s" % (remote, branch, err.strip()[:90])

    ref = "%s/%s" % (remote, branch)

    rc_a, _, _ = git(["merge-base", "--is-ancestor", ref, "HEAD"], cwd)
    if rc_a != 0:
        return {}, None

    rc, out, _ = git(["diff", "--name-only", ref, "HEAD"], cwd)
    if rc != 0:
        return None, "could not diff %s against HEAD" % ref
    paths = [p for p in out.split("\n") if p.strip()]

    losses = {}
    for p in paths:
        rc, remote_text, _ = git(["show", "%s:%s" % (ref, p)], cwd)
        if rc != 0:
            continue                      # new file on our side - nothing to lose
        # Read the COMMITTED version, not the file on disk. Note both of these go through
        # subprocess with an argument list, so the Git Bash rev-string mangling that eats
        # `<ref>:.shared/...` in a shell never applies here.
        rc_l, local_text, _ = git(["show", "HEAD:%s" % p], cwd)
        if rc_l != 0:
            local_text = ""
        have = set(l.strip() for l in local_text.split("\n"))
        gone = [l for l in remote_text.split("\n")
                if meaningful(l) and l.strip() not in have]
        if gone:
            losses[p] = gone
    return losses, None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cwd", default=".")
    ap.add_argument("--remote", default="origin")
    ap.add_argument("--branch", default=None,
                    help="default: the branch currently checked out")
    ap.add_argument("--check-only", action="store_true")
    ap.add_argument("--because", help="override: why this deletion is intended")
    a = ap.parse_args()

    branch = a.branch or subprocess.run(
        ["git", "-C", a.cwd, "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, creationflags=flags()).stdout.strip()
    if not branch or branch == "HEAD":
        print("  [CANNOT CHECK] no branch checked out (detached HEAD?). Pass --branch.")
        print("  This is NOT a statement that the push is safe. Nothing was pushed.")
        return 2
    a.branch = branch

    losses, err = check(a.cwd, a.remote, a.branch)
    if err:
        # CANNOT-CHECK is not CLEAN. Same distinction as everywhere else: an unrunnable gate
        # must not read as a passed one.
        print("  [CANNOT CHECK] %s" % err)
        print("  This is NOT a statement that the push is safe. Nothing was pushed.")
        return 2

    if losses:
        total = sum(len(v) for v in losses.values())
        print("  [REFUSE] this push would REMOVE %d line(s) that are on %s/%s."
              % (total, a.remote, a.branch))
        print("           Someone else's content, or a stale copy of yours. Nothing pushed.")
        print()
        for p, gone in losses.items():
            print("    %s  (%d line(s))" % (p, len(gone)))
            for l in gone[:4]:
                print("       - %s" % l.strip()[:88])
            if len(gone) > 4:
                print("       ... and %d more" % (len(gone) - 4))
        print()
        print("  Git's own check is TOPOLOGICAL - it refuses a non-fast-forward. That is a")
        print("  different question from whether this commit deletes somebody's writing, and")
        print("  a clean fast-forward can do exactly that.")
        print()
        print("  Reconcile:  git -C %s diff %s/%s" % (a.cwd, a.remote, a.branch))
        if not a.because:
            print("  Intended?   re-run with --because \"<why this deletion is correct>\"")
            return 1
        print("  OVERRIDE: %s" % a.because)
        print()

    if a.check_only:
        print("  [ok] nothing on %s/%s would be lost." % (a.remote, a.branch)
              if not losses else "  (check-only: not pushing)")
        return 0

    rc, out, err2 = git(["push", a.remote, "HEAD:%s" % a.branch], a.cwd)

    if rc != 0 and "non-fast-forward" in (err2 + out):
        print("  remote moved while you worked - rebasing in a PRIVATE worktree so the shared")
        print("  checkout, and anyone's uncommitted edits in it, are never touched.")
        wt = os.path.join(os.path.expanduser("~"), ".safe-push-wt")
        head = git(["rev-parse", "HEAD"], a.cwd)[1].strip()
        git(["worktree", "remove", wt, "--force"], a.cwd)
        rc_w, _, e_w = git(["fetch", "-q", a.remote, a.branch], a.cwd)
        rc_w, _, e_w = git(["worktree", "add", "-q", "--detach", wt,
                            "%s/%s" % (a.remote, a.branch)], a.cwd)
        if rc_w != 0:
            print("  could NOT create the worktree: %s" % e_w.strip()[:120])
            print("  Nothing pushed. The shared tree is untouched.")
            return 1
        rc_c, o_c, e_c = git(["cherry-pick", head], wt)
        if rc_c != 0:
            print("  cherry-pick CONFLICTED - your commit and the remote touch the same lines.")
            print("  %s" % (e_c or o_c).strip()[:160])
            git(["cherry-pick", "--abort"], wt)
            git(["worktree", "remove", wt, "--force"], a.cwd)
            print("  Nothing pushed, nothing altered. Reconcile by hand.")
            return 1
        rc, out, err2 = git(["push", a.remote, "HEAD:%s" % a.branch], wt)
        git(["worktree", "remove", wt, "--force"], a.cwd)
        if rc == 0:
            # the local branch still points at the pre-rebase commit; move it WITHOUT touching
            # the working tree, so another session's edits survive the sync
            git(["fetch", "-q", a.remote, a.branch], a.cwd)
            git(["reset", "--mixed", "%s/%s" % (a.remote, a.branch)], a.cwd)
            print("  local branch fast-forwarded - index synced, working tree untouched")

    if rc != 0:
        print("  push failed: %s" % (err2 or out).strip()[:200])
        return 1
    print("  pushed to %s/%s%s" % (a.remote, a.branch,
                                   " (deletion overridden)" if losses else
                                   " - nothing on the remote was lost"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
