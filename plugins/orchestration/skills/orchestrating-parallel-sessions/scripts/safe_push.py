"""Refuse to push if it would DROP lines that are already on the remote.

WHY. `orchdoc.py commit` has carried this gate for OrchDocs since the day a session nearly
overwrote another orchestrator's work:

    [REFUSE] gate 1 - landing this would REMOVE 2 line(s) that are on origin/main.
             Someone else's content, or a stale copy of yours.

Every OTHER repo got pushed with a bare `git add -A && git commit && git push`, which checks
nothing about content. Git's own protection is TOPOLOGICAL - it refuses a non-fast-forward -
and that is a different question from "does this commit delete somebody's writing?" A push can
be a perfectly clean fast-forward and still remove every line another person just added.

⭐ THE PRECIPITATING INCIDENT IS INSTRUCTIVE FOR WHAT IT DOES *NOT* SHOW. The human edited a
README in the GitHub web editor while an agent was pushing to it; his commit was rejected for a
conflict and the text was lost from the browser. **Nothing here could have saved that** - it
was never committed anywhere, and no agent-side tool can see a browser textarea.

But the near-miss underneath it is entirely preventable: **had he committed successfully first,
the agent's next push would have built on a stale base and silently removed his lines**, with
git perfectly happy because the topology was fine. That is the case this gate covers, and it
needs nothing from the human at all.

WHAT IT CHECKS. Fetch the remote, diff the local tree against the remote tip for each staged
path, and refuse if any non-trivial line present on the remote is absent locally. Whitespace
and pure reformatting do not count; deleted CONTENT does.

    safe_push.py --remote origin --branch master           # check, then push
    safe_push.py --check-only                              # report and stop
    safe_push.py --because "<reason>"                      # override, recorded in the message

⚠️ THE OVERRIDE EXISTS AND REQUIRES A SENTENCE. Deletion is sometimes the point - removing a
section, retiring a file. A gate with no way through gets removed; a gate whose exception costs
one sentence keeps its meaning, and the sentence lands in the commit message where the next
reader finds it.
"""
import argparse
import difflib
import pathlib
import re
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def git(args, cwd=None):
    r = subprocess.run(["git"] + args, capture_output=True, text=True, cwd=cwd)
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


def survives_as_an_edit(line, local_lines):
    """Does this remote line survive locally in edited form?

    ⭐ The distinction the first version missed: a line REWRITTEN in place is not a line LOST.
    Its old text is absent, which is what a line-level diff reports, but its content is still
    there wearing different words. Only a line with no local descendant is a real deletion.

    Asymmetric containment, same measure the strikethrough detector needed: what fraction of
    the ORIGINAL is accounted for somewhere local. A symmetric similarity score punishes the
    common case where a line is edited by being made longer.
    """
    t = line.strip()
    if not t:
        return True
    best = 0.0
    for cand in local_lines:
        c = cand.strip()
        if not c or abs(len(c) - len(t)) > max(len(t) * 3, 120):
            continue
        sm = difflib.SequenceMatcher(None, t, c)
        # runs of >= 6 chars only: scattered single-character matches inflate short lines to
        # a passing score against text they have nothing to do with.
        frac = sum(b.size for b in sm.get_matching_blocks() if b.size >= 6) / float(len(t))
        if frac > best:
            best = frac
            if best >= 0.60:
                return True
    return False


def check(cwd, remote, branch):
    """Lines present on the remote and absent locally, per path. {} means safe."""
    rc, _, err = git(["fetch", "-q", remote, branch], cwd)
    if rc != 0:
        return None, "could not fetch %s/%s: %s" % (remote, branch, err.strip()[:90])

    ref = "%s/%s" % (remote, branch)
    rc, out, _ = git(["diff", "--name-only", ref], cwd)
    if rc != 0:
        return None, "could not diff against %s" % ref
    paths = [p for p in out.split("\n") if p.strip()]

    losses = {}
    for p in paths:
        rc, remote_text, _ = git(["show", "%s:%s" % (ref, p)], cwd)
        if rc != 0:
            continue                      # new file on our side - nothing to lose
        local = pathlib.Path(cwd or ".") / p
        try:
            local_text = local.read_text(encoding="utf-8", errors="replace")
        except OSError:
            local_text = ""
        local_lines = local_text.split("\n")
        have = set(l.strip() for l in local_lines)
        gone = [l for l in remote_text.split("\n")
                if meaningful(l) and l.strip() not in have
                and not survives_as_an_edit(l, local_lines)]
        if gone:
            losses[p] = gone
    return losses, None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cwd", default=".")
    ap.add_argument("--remote", default="origin")
    ap.add_argument("--branch", default="master")
    ap.add_argument("--check-only", action="store_true")
    ap.add_argument("--because", help="override: why this deletion is intended")
    a = ap.parse_args()

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
    if rc != 0:
        print("  push failed: %s" % (err2 or out).strip()[:200])
        return 1
    print("  pushed to %s/%s%s" % (a.remote, a.branch,
                                   " (deletion overridden)" if losses else
                                   " - nothing on the remote was lost"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
