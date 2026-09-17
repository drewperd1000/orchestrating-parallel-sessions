#!/usr/bin/env python3
"""Refuse a commit that would drop lines which reached the canonical branch after your copy.

<!-- route-tags: stale copy overwrite guard pre-commit destroyed work canonical origin main
     worktree behind branch point lost commit no conflict -->

⛔ THE INCIDENT, 2026-09-08. o10 landed `8dbe2ac` on `.shared/scripts/orchdoc.py`. Three of my
commits that day - `e642cb4`, `a447d61`, `17695cf` - wrote that same file out of a working copy
that predated theirs. **Git reported no conflict on any of the three**, because overwriting a
file is not merging one. All three of o10's additions left `main` and nothing said so.

⭐ THE PART THAT MAKES THIS WORTH A GUARD RATHER THAN A HABIT: the log was intact. `git log --
.shared/scripts/orchdoc.py` listed `8dbe2ac` sitting in place, in order, with its message. Only
reading the file CONTENT on `main` showed it gone - `grep -c "def cited_regions"` answered 0.
Every reflex anyone actually has here (read the log, check for conflicts, read `git status`)
operates on history, and the loss lived in content.

⭐ AND `orchdoc.py commit`'s gate 1 already refuses exactly this - for OrchDocs. My own commit
message for the last narrowing of it, `c57affe`, says the fix *"does NOT protect prose, entry
bodies."* A function body in a Python file is neither, so it fell through a hole I had written
down four days earlier. Widening gate 1 was not the fix; gate 1 only ever runs on the OrchDoc
path. This runs on every commit in the repo.

⛔ **THE FIRST VERSION OF THIS FILE WAS BUILT ON A WRONG MODEL OF THE INCIDENT, AND ITS OWN TEST
SUITE AGREED WITH IT.** I assumed my branch had been BEHIND o10's commit, so the guard asked
"does the staged copy lack lines that reached canonical after my branch point?" Five synthetic
cases passed. Then replaying the actual commits showed `8dbe2ac` was **already an ancestor of
all three** of my parents - `cited_regions` is present at `e642cb4^` and absent at `e642cb4`.

⭐ **My branch was current. My COPY OF THE FILE was not.** I had read the file before o10's
commit, edited that text an hour later, and wrote the whole thing over a HEAD that already
contained their work. Git called it what it was - an ordinary modification that removes lines -
and `e642cb4`'s own diffstat said `24 insertions(+), 69 deletions(-)` under the subject
*"reorder: a heading inside a code fence is not a section boundary"*.

⭐ **A harness built from the same wrong model as the code cannot disagree with it.** The
synthetic cases were written by the same reasoning as the check, so they could only confirm it.
The replay is what disagreed, and it is the reason `test_stale_overwrite_guard.py` now ends with
the real commits rather than with five invented ones.

## The rule, and why it does not become the always-refusing gate

⚠️ **"Refuse any commit that deletes a line" is useless** - deleting lines is most of what
editing is, and a guard that fires on correct work several times a day is a guard people
disable. `orchdoc.py` carries 173 recorded overrides of one such check, and o10's own finding
names the cost: *an override used 38 times is not an override, it is a silenced check.*

**So it fires on a narrower thing, and the narrowing is what makes it mechanisable:**

> A stale whole-file write does not delete a scattered handful of lines. It deletes **exactly
> the lines that arrived after the copy was taken** - which means the removed set CONTAINS,
> nearly in full, the additions of one identifiable earlier commit. A targeted edit almost
> never does that.

⭐ **That is a signature, not a heuristic about intent.** It asks a question with a determinate
answer - *do the lines this commit removes reconstruct commit X's additions?* - rather than
guessing whether the author meant it. Two rules, both mechanical:

    RULE A  branch behind:   lines(canonical) - lines(merge-base), missing from staged
    RULE B  undoes a commit: |added_by(X) & removed_here| / |added_by(X)| >= 0.9

Rule B is what catches the real 2026-09-08 case; Rule A costs almost nothing and covers the
branch-behind shape that Rule B would miss when the file is new to the branch.

⭐ **Rule B is `_split_by_seen`'s question, asked of the CONTENT instead of a clock.** That
helper asks the same thing - had the author seen this line? - from the file's `st_mtime`, which
F117 measured as the wrong axis: mtime marks the END of an edit window, so it approves precisely
the lines that arrived DURING the window in which this loss can happen. Rule B never asks when
anything happened. It asks whether a whole commit's worth of additions is being removed by a
commit that does not say it is reverting anything.

⚠️ **A deliberate revert trips it, and that is correct** - `git revert` is the right way to undo
a commit, and it is not what this fires on. If you are genuinely removing a recent commit's work
by hand, the message names which commit and `--no-verify` is one flag.

## What it cannot see, stated rather than implied

⚠️ **`origin/main` is only as fresh as the last fetch.** This does not fetch - a guard that
makes a network call on every commit is a guard that gets turned off - so a line that landed
canonically since your last fetch is invisible to it. It narrows the window; it does not close
it. Run `git fetch` before a commit you care about.

⚠️ **It compares LINE SETS, so a pure move is invisible** - a line relocated within the file
still exists. That is the right trade: a moved line is not lost.

⚠️ **Binary files get a coarser check** - it can only say the staged bytes differ from canonical
while canonical has moved, which it reports rather than refuses.
"""
import io
import os
import re
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CANONICAL_CANDIDATES = ("origin/main", "origin/master")
MAX_REPORT = 12


def git(repo, args):
    """(rc, stdout, stderr) - argv form, so no shell and no Windows path rewriting."""
    try:
        p = subprocess.run(["git", "-C", str(repo)] + list(args),
                           capture_output=True, timeout=45)
    except Exception as e:
        return 1, "", str(e)
    dec = lambda b: b.decode("utf-8", errors="replace")  # noqa: E731
    return p.returncode, dec(p.stdout), dec(p.stderr)


def _blob(repo, spec):
    """File content at a revision, or None when it is not there / not text."""
    try:
        p = subprocess.run(["git", "-C", str(repo), "show", spec],
                           capture_output=True, timeout=45)
    except Exception:
        return None
    if p.returncode != 0:
        return None
    if b"\x00" in p.stdout[:8192]:
        return False                      # binary - distinct from "absent"
    return p.stdout.decode("utf-8", errors="replace")


def canonical_ref(repo):
    for ref in CANONICAL_CANDIDATES:
        rc, _, _ = git(repo, ["rev-parse", "--verify", "--quiet", ref])
        if rc == 0:
            return ref
    return None


def inherited_origin(repo):
    """Reason this repo's `origin/*` cannot be trusted, or None.

    ⛔ o7's POINT, AND IT IS THE SHARPEST VERSION OF THIS ALL DAY (relayed 2026-09-08): a tool
    that does the RIGHT thing - reason about `origin/main` rather than `main` - is STILL wrong
    inside a `--local` clone, and has no way to tell. `git clone --local` builds
    `refs/remotes/origin/*` from the SOURCE'S LOCAL branches, so the staleness is inherited and
    then wears the authoritative name.

    ⭐ SO THE CHECK THAT CANNOT FAIL IS THE ONE TO WORRY ABOUT. Inside such a clone this guard
    would compare a stale `origin/main` against a stale working copy, find them in agreement, and
    report clean - which is exactly what happened to me: my own harness cloned locally, the
    guard reported SILENCE on a merge that removed 21 of 25 lines, and only hand arithmetic
    caught it.

    A filesystem path where a URL belongs is the tell, and it is cheap.
    """
    rc, url, _ = git(repo, ["remote", "get-url", "origin"])
    if rc != 0 or not url:
        return None                       # no origin: `canonical_ref` already returns None
    u = url.strip()
    if "://" in u or re.match(r"^[\w.+-]+@[\w.-]+:", u):
        return None                       # a real remote
    return ("origin is a filesystem path (%s) - this is a local clone, so its origin/* was "
            "built from the SOURCE's LOCAL branches and may be stale" % u[:60])


def staged_paths(repo):
    # ACMR: added, copied, modified, renamed. A DELETION is an explicit act on a file you named,
    # so it is a decision by construction and this guard has nothing to say about it.
    rc, out, _ = git(repo, ["diff", "--cached", "--name-only", "--diff-filter=ACMR"])
    if rc != 0:
        return []
    return [l.strip() for l in out.split("\n") if l.strip()]


# ⛔ MEASURED, AND IT IS THE REASON THIS IS SCOPED AT ALL. Replaying the last 250 commits on
# main: over ALL file types rule B would have refused 12 commits, and ELEVEN of them were
# correct work - an OrchDoc entry rewritten in place removes its own previous text wholesale,
# which is the same shape as a stale overwrite and is exactly what rewriting an entry means.
# One scored 1.00 against the commit it was deliberately superseding.
#
# ⭐ RESTRICTED TO CODE the same sweep refuses ONE commit in 250: e642cb4, the real destruction.
# 39 (commit, file) pairs examined, 0 false alarms.
#
# ⚠️ AND PROSE IS NOT LEFT UNGUARDED - OrchDocs land through `orchdoc.py commit`, whose gate 1
# does this job with the vocabulary prose needs (it understands attestations, rewordings and
# cross-orchestrator stamps). Two guards, each on the material it can actually read.
CODE_SUFFIXES = (".py", ".ps1", ".sh", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
                 ".json", ".yml", ".yaml", ".toml", ".sql", ".css", ".html", ".astro")


def code_paths(repo):
    return [p for p in staged_paths(repo) if p.lower().endswith(CODE_SUFFIXES)]


def check(repo):
    """[(path, ref, [missing lines])] - empty when nothing is at risk OR it cannot tell.

    ⛔ IT ALLOWS WHEN IT CANNOT TELL, and that is deliberate here even though the same choice
    was a defect forty lines into `entries_touched` this morning. The difference is what the
    two silences DO. There, returning "nothing" made a staleness FINDING disappear - a run that
    never happened looked like a run that passed. Here, returning nothing leaves the commit
    exactly as it behaved before this file existed. This guard only ever adds refusals; it
    removes no signal that was there.
    """
    ref = canonical_ref(repo)
    if not ref:
        return []
    # Already contains canonical - your copy descends from everything on it, so nothing on it
    # can be news to you.
    if git(repo, ["merge-base", "--is-ancestor", ref, "HEAD"])[0] == 0:
        return []
    rc, base, _ = git(repo, ["merge-base", "HEAD", ref])
    base = base.strip()
    if rc != 0 or not base:
        return []

    out = []
    for path in staged_paths(repo):
        ref_blob = _blob(repo, "%s:%s" % (ref, path))
        if ref_blob is None:
            continue                                  # not on canonical: nothing to lose
        staged = _blob(repo, ":%s" % path)
        if staged is None:
            continue
        base_blob = _blob(repo, "%s:%s" % (base, path))

        if ref_blob is False or staged is False or base_blob is False:
            if ref_blob != staged:
                out.append((path, ref, ["<binary> staged bytes differ from %s, which has moved "
                                        "since your branch point" % ref]))
            continue

        arrived = set(ref_blob.split("\n")) - set((base_blob or "").split("\n"))
        arrived.discard("")
        have = set(staged.split("\n"))
        missing = sorted(l for l in arrived if l not in have and l.strip())
        if missing:
            out.append((path, ref, missing))
    return out


# ---- rule B: does this commit remove a whole earlier commit's additions? --------------------

MIN_ADDED = 6          # below this, an overlap is coincidence rather than a signature
DEPTH = 30             # commits of history to consider per file

# ⛔ 0.90 WAS A GUESS AND IT MISSED THE REAL CASE BY TWO POINTS. Measured on the actual incident:
# the destroying commit scored 0.88 against o10's commit, and all 29 other commits touching that
# file in the window scored 0.00.
#
# ⭐ THAT SEPARATION IS THE FINDING, not the number. 0.88 against a noise floor of exactly zero
# means the discriminator is nearly binary on real history, so the threshold belongs clear of
# the floor rather than hugging the single true positive - a cutoff tuned to one case fits that
# case and nothing else.
OVERLAP = 0.70


def _interesting(line):
    """Lines distinctive enough that matching them means something.

    ⛔ WITHOUT THIS, EVERY COMMIT MATCHES EVERY OTHER. Blank lines, bare braces, `import sys`
    and a lone `return` recur in every diff in the repo, so an overlap built from them measures
    the language, not the change.
    """
    s = line.strip()
    return len(s) >= 12 and s not in ("except Exception:", "else:", "continue", "break")


def _added_lines(repo, sha, path):
    rc, diff, _ = git(repo, ["show", "--format=", "--unified=0", sha, "--", path])
    if rc != 0 or not diff:
        return set()
    return {ln[1:].strip() for ln in diff.split("\n")
            if ln.startswith("+") and not ln.startswith("+++") and _interesting(ln[1:])}


def check_undoes_commit(repo):
    """[(path, sha, subject, [lines])] - staged content that removes a past commit's additions.

    Returns [] when it cannot tell, for the same reason `check` does: this guard only ever adds
    refusals to the previous behaviour, and one that refuses on a git hiccup gets switched off.
    """
    out = []
    for path in code_paths(repo):
        head_blob = _blob(repo, "HEAD:%s" % path)
        staged = _blob(repo, ":%s" % path)

        # ⛔ "NOT AT HEAD" AND "COULD NOT READ IT" ARE DIFFERENT FACTS, and `_blob` returns None
        # for both. The first is a NEW FILE - there is nothing to undo and skipping is right.
        # The second is the checker failing, and skipping there is the fail-open I fixed in
        # `entries_touched` this morning, still sitting in the guard I wrote to replace it.
        #
        # ⭐ FOUND BY A HARNESS BUG, WHICH IS THE ONLY REASON IT SURFACED. Checking whether a
        # peer's PR would revert an already-merged commit, my scratch clone had no usable
        # `origin/main`, so HEAD carried no such file, `_blob` returned None, and the guard
        # reported SILENCE on a merge that removes 21 of 25 lines. The wrong answer and the
        # right one are the same word.
        if head_blob is None:
            rc, _o, _e = git(repo, ["cat-file", "-e", "HEAD:%s" % path])
            if rc == 0:
                out.append((path, "<unreadable>",
                            "cannot read this file at HEAD - the check could not run",
                            ["the guard could not compare; treat as UNKNOWN, not as clean"]))
            continue
        if staged is None:
            out.append((path, "<unreadable>",
                        "cannot read the staged copy - the check could not run",
                        ["the guard could not compare; treat as UNKNOWN, not as clean"]))
            continue
        if head_blob is False or staged is False:
            continue                       # binary: rule A reports these, line diffing cannot
        removed = {l.strip() for l in head_blob.split("\n") if _interesting(l)} \
            - {l.strip() for l in staged.split("\n")}
        if len(removed) < MIN_ADDED:
            continue

        rc, log, _ = git(repo, ["log", "--format=%H%x1f%s", "-n", str(DEPTH), "HEAD", "--", path])
        if rc != 0:
            continue
        for row in log.split("\n"):
            if "\x1f" not in row:
                continue
            sha, subject = row.split("\x1f", 1)
            added = _added_lines(repo, sha.strip(), path)
            if len(added) < MIN_ADDED:
                continue
            hit = added & removed
            if len(hit) / float(len(added)) >= OVERLAP:
                out.append((path, sha.strip()[:9], subject.strip(), sorted(hit)))
                break          # the newest such commit is the one to name
    return out


def report(findings, stream=sys.stderr):
    p = lambda s="": print(s, file=stream)  # noqa: E731
    p("BLOCKED: this commit would DROP lines that are on the canonical branch and were")
    p("         never in front of you.\n")
    for path, ref, missing in findings:
        p("    %s  -  %d line(s) on %s that your staged copy does not have:" % (path, len(missing), ref))
        for l in missing[:MAX_REPORT]:
            p("        %s" % l[:110].rstrip())
        if len(missing) > MAX_REPORT:
            p("        ... and %d more" % (len(missing) - MAX_REPORT))
        p()
    p("    These lines reached the canonical branch AFTER the commit your working copy")
    p("    descends from, so dropping them cannot be a decision you made - your copy never")
    p("    contained them. Git reports no conflict for this, because overwriting a file is")
    p("    not merging one. On 2026-09-08 that silently removed another session's whole")
    p("    change from main, three times, with the commit still sitting in the log.\n")
    p("    DO THIS INSTEAD")
    p("      git fetch origin && git rebase origin/main      # then re-apply your edit")
    p("    or, to see exactly what you are missing first:")
    p("      git diff origin/main -- <path>\n")
    p("    Deliberate, and you have read what you are removing? `git commit --no-verify`.")
    p("    That is a claim you looked, and it stays in the reflog as one.")


def report_undo(findings, stream=sys.stderr):
    p = lambda s="": print(s, file=stream)  # noqa: E731
    p("BLOCKED: this commit REMOVES an earlier commit's work, in full, without saying so.\n")
    for path, sha, subject, hit in findings:
        p("    %s" % path)
        p("        removes %d line(s) that %s added:" % (len(hit), sha))
        p("        %s  %s" % (sha, subject[:80]))
        for l in hit[:MAX_REPORT]:
            p("            %s" % l[:104].rstrip())
        if len(hit) > MAX_REPORT:
            p("            ... and %d more" % (len(hit) - MAX_REPORT))
        p()
    p("    A targeted edit does not delete one past commit's additions nearly in full. A file")
    p("    written whole from a copy taken BEFORE that commit does exactly that, and git")
    p("    reports it as an ordinary modification because it is one. On 2026-09-08 a commit")
    p("    titled \"reorder: a heading inside a code fence is not a section boundary\" landed")
    p("    24 insertions and 69 deletions, and the 69 were another session's entire change.\n")
    p("    DO THIS INSTEAD")
    p("      git diff --cached -- <path>        # read what you are removing")
    p("      git show <sha> -- <path>           # read the commit you would be undoing")
    p("    Then re-apply your edit to the CURRENT file rather than re-writing your copy.\n")
    p("    Genuinely undoing it? `git revert <sha>` says so in the history. If it must be")
    p("    by hand, `git commit --no-verify` - a claim you read the diff, kept in the reflog.")


# ⭐ 9 MEANS REFUSE, and it is the convention the three guards already in this repo's pre-commit
# chain use. The point is that ONLY 9 blocks: a crash, an import error or a git hiccup exits
# non-zero too, and those must not stop every commit in the workspace. A guard that blocks when
# it is broken is the failure knowledge_gate.py shipped on 2026-08-13.
REFUSE = 9


def main(argv):
    repo = argv[1] if len(argv) > 1 else os.getcwd()

    # ⛔ SAY SO RATHER THAN ANSWER. Inside a `--local` clone both sides of every comparison below
    # are inherited from the source's LOCAL branches, so the guard would find agreement and
    # report clean - a check that cannot fail in the one place it is needed (o7's point, relayed
    # 2026-09-08). This is the third time today that "could not tell" had to be prised apart
    # from "nothing found"; the other two were `entries_touched` and `_blob`.
    why = inherited_origin(repo)
    if why:
        print("UNKNOWN: this guard cannot answer here.\n", file=sys.stderr)
        print("    %s\n" % why, file=sys.stderr)
        print("    Both sides of the comparison come from the same inherited refs, so agreement\n"
              "    between them is not evidence. Run it where the ORIGINAL clone is.\n",
              file=sys.stderr)
        return 0                          # not REFUSE: it adds no signal, it just has none

    undo = check_undoes_commit(repo)
    if undo:
        report_undo(undo)
        return REFUSE
    findings = check(repo)
    if not findings:
        return 0
    report(findings)
    return REFUSE


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception:
        # ⛔ NEVER take the repo down. A guard that crashes must not refuse every commit in the
        # workspace - that is the failure knowledge_gate.py shipped on 2026-08-13, where one
        # syntax error blocked everyone until somebody found it.
        sys.exit(0)
