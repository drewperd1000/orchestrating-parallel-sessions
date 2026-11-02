#!/usr/bin/env python3
"""Refuse a commit that would drop lines which reached the canonical branch after your copy.

<!-- subject: infrastructure -->

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

## Undoing or replacing a commit on purpose: two declared escapes, neither `--no-verify`

⛔ **THIS SECTION USED TO SAY A REVERT IS "NOT WHAT THIS FIRES ON". IT WAS.** Rule B reads the
staged content, and a revert's staged content removes the reverted commit's additions in full -
the exact signature. o10 measured it on 2026-09-29: reverting `170b608d3` in `product-app`
was refused. The only way past was `--no-verify`, which agents may not use and which also
switches off every other guard in the chain for that commit.

⭐ **WHEN A REVERT REACHES THIS HOOK AT ALL** (measured, git 2.53, 2026-09-29): a plain `git
revert <sha>` that applies cleanly does NOT run pre-commit - the sequencer commits without it.
A revert that stopped on a conflict and is finished with `git revert --continue` or `git
commit`, and `git revert --no-commit` followed by `git commit`, DO run it, with `REVERT_HEAD`
naming the commit being reverted. Those are the cases that were refused.

    ESCAPE 1  REVERT_HEAD           a finding whose commit IS the one being reverted is allowed
    ESCAPE 2  STALE_GUARD_REPLACES  a finding whose commit is NAMED in this variable is allowed

`STALE_GUARD_REPLACES="<sha>[,<sha>...][: why]"` is for a commit that deliberately replaces an
earlier commit's text by hand - o10 D77.n5's ruling replacing the "From:"/"To:" labels that
`170b608d3` added is the case that needed it. Both escapes are matched against the commit
Rule B IDENTIFIED, never taken as a blanket pass: a SHA naming a different commit, or leaving
out one of two commits whose lines the change removes, is still refused, and the refusal says
which commit it wanted. So a value left exported in a shell cannot excuse anything but that
same commit's lines.

Each allowed finding prints one `[stale-overwrite] ALLOWED` line and appends a row to
`.shared/state/stale-overwrite-escapes.jsonl` in the workspace's main checkout (repo, branch,
HEAD, path, the commit, how it was excused, the reason given). That log is local, like
`gate-refusals.jsonl` beside it, and does not survive a laptop loss.

## What it cannot see, stated rather than implied

⚠️ **`origin/main` is only as fresh as the last fetch.** This does not fetch - a guard that
makes a network call on every commit is a guard that gets turned off - so a line that landed
canonically since your last fetch is invisible to it. It narrows the window; it does not close
it. Run `git fetch` before a commit you care about.

⚠️ **It compares LINE SETS, so a pure move is invisible** - a line relocated within the file
still exists. That is the right trade: a moved line is not lost.

⚠️ **Binary files are not compared.** `_blob` returns False for any binary copy, so two binary
copies always look equal and rule A only notices a file that changed between text and binary.
Measured 2026-09-24 (o9): a PNG that canonical replaced after the branch point, with a
different PNG staged over it on the behind branch, produced no finding. This line said until
then that such a case is reported; it was not.

⚠️ **Files in `GENERATED_PATHS` are not checked at all.** Their own job rewrites them whole on
every run, so a line leaving one is the job's decision. It is a list of exact paths; the note
above it says why not a pattern.
"""
import datetime
import io
import json
import os
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

CANONICAL_CANDIDATES = ("origin/main", "origin/master")
MAX_REPORT = 12


def git(repo, args):
    """(rc, stdout, stderr) - argv form, so no shell and no Windows path rewriting."""
    try:
        p = subprocess.run(["git", "-C", str(repo)] + list(args),
                           capture_output=True, timeout=45, creationflags=flags())
    except Exception as e:
        return 1, "", str(e)
    dec = lambda b: b.decode("utf-8", errors="replace")  # noqa: E731
    return p.returncode, dec(p.stdout), dec(p.stderr)


def _blob(repo, spec):
    """File content at a revision, or None when it is not there / not text."""
    try:
        p = subprocess.run(["git", "-C", str(repo), "show", spec],
                           capture_output=True, timeout=45, creationflags=flags())
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


# ⛔ A FILE ITS OWN JOB REWRITES WHOLE ON EVERY RUN HOLDS NOBODY'S WORK (o9, 2026-09-24,
# reported by o1). `run_daily_briefing.py` in the memory repo writes `briefing.json` from
# scratch each morning, so yesterday's entries leave because the job decided they should, not
# because someone wrote from a stale copy. Rule B read that as undoing yesterday's commit -
# "removes 126 line(s) that fb49b15e8 added" - and refused the job's commit on 2026-09-24.
# It had passed on the five days before only because most entries carried over; that morning
# the list went from 336 entries to 72.
#
# ⭐ EXACT PATHS, NOT A PATTERN. The same `state/` folder holds `feedback.json` (the human's marks
# from the feedback UI), `dismissed.json` (the emails he dismissed) and `classified.json` (every
# verdict so far, carried forward from run to run). A stale copy of any of those WOULD lose work,
# which is what this guard is for, so `automation/*/state/*.json` would have switched protection
# off for the files there that need it most.
#
# ⚠️ A marker would sit better on the file itself, but JSON cannot carry a comment, and a
# `.gitattributes` line lives in the owning repo - a second commit in a second repo before the
# job could succeed again. Add a path here ONLY for a file that its job rewrites whole every
# run and that no person edits.
GENERATED_PATHS = frozenset((
    "automation/email_briefing/state/briefing.json",
    # ⛔ THE CLOUD PUBLICATION'S OWN FILES (o9, 2026-10-03, lane o9L1032). `cloud_publish.py
    # --apply`, run hourly by the config sync, writes these two whole on every run. Rule B
    # refused its 18:07 PT commit as undoing its own 00:12 PT publish - "removes 55 line(s) that
    # d30664d42 added" - because a publish that changes many files rewrites most of the last
    # one's MANIFEST.json lines. `.shared/cloud/hook-status.json` sits beside them and is left
    # out on purpose: people edit it, and the publisher only reads it.
    ".shared/cloud/MANIFEST.json",
    ".shared/cloud/hooks.json",
))


def is_generated(path):
    return path.replace("\\", "/").strip() in GENERATED_PATHS


def _staged_all(repo):
    # ACMR: added, copied, modified, renamed. A DELETION is an explicit act on a file you named,
    # so it is a decision by construction and this guard has nothing to say about it.
    rc, out, _ = git(repo, ["diff", "--cached", "--name-only", "--diff-filter=ACMR"])
    if rc != 0:
        return []
    return [l.strip() for l in out.split("\n") if l.strip()]


def staged_paths(repo):
    """Staged paths both rules examine - everything except GENERATED_PATHS."""
    return [p for p in _staged_all(repo) if not is_generated(p)]


def generated_staged(repo):
    """Staged paths skipped because their job regenerates them - printed, never silent."""
    return [p for p in _staged_all(repo) if is_generated(p)]


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

    `sha` is the FULL sha, so an escape can be matched against it exactly; reports shorten it.
    Every matching commit in the window is listed, not only the newest: an escape that names
    one of them must not excuse removing another's lines.

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
                out.append((path, sha.strip(), subject.strip(), sorted(hit)))
    return out


# ---- the two declared escapes from rule B ---------------------------------------------------

REPLACES_ENV = "STALE_GUARD_REPLACES"
ESCAPE_LOG = "stale-overwrite-escapes.jsonl"
_HEX = re.compile(r"^[0-9a-fA-F]{7,40}$")


def _commit(repo, rev):
    rc, out, _ = git(repo, ["rev-parse", "--verify", "--quiet", "%s^{commit}" % rev])
    return out.strip() if rc == 0 and out.strip() else None


def revert_head(repo):
    """Full sha `git revert` is in the middle of undoing, or None. Per worktree, as git keeps it."""
    return _commit(repo, "REVERT_HEAD")


def declared_replacements(repo):
    """({full sha: as written}, [tokens that name no commit here], reason) from the env var.

    Only hex SHAs count. A branch name or `HEAD~2` would declare a commit nobody named.
    """
    raw = os.environ.get(REPLACES_ENV, "").strip()
    shas, reason = (raw.split(":", 1) + [""])[:2] if raw else ("", "")
    ok, bad = {}, []
    for tok in (t for t in re.split(r"[\s,;]+", shas) if t):
        full = _commit(repo, tok) if _HEX.match(tok) else None
        if full:
            ok[full] = tok
        else:
            bad.append(tok)
    return ok, bad, reason.strip()


def _log_escape(repo, path, sha, subject, n, via, reason):
    """Append one row to the shared escape log; the log's path, or None if it could not write."""
    try:
        import shared_state
        d = shared_state.state_dir()
        d.mkdir(parents=True, exist_ok=True)
        row = {"ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
               "repo": git(repo, ["rev-parse", "--show-toplevel"])[1].strip(),
               "branch": git(repo, ["rev-parse", "--abbrev-ref", "HEAD"])[1].strip(),
               "head": git(repo, ["rev-parse", "HEAD"])[1].strip(),
               "path": path, "replaces": sha, "subject": subject, "lines": n,
               "via": via, "reason": reason}
        with open(d / ESCAPE_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return str(d / ESCAPE_LOG)
    except Exception:
        return None


_EXCUSED = set()          # commits `excuse` allowed on this run, for the refusal's wording

# ESCAPE 3. `git config --local staleguard.mirror true` marks one repo as a mirror of files kept
# somewhere else (<private-repo> mirrors ~/.claude). Only the repo's own config counts: a
# global or system value is ignored, so one setting cannot switch the guard off everywhere.
MIRROR_KEY = "staleguard.mirror"


def is_mirror_repo(repo):
    rc, out, _ = git(repo, ["config", "--local", "--type=bool", "--get", MIRROR_KEY])
    return rc == 0 and out.strip() == "true"


def log_mirror_pass(repo, undo, findings, stream=sys.stderr):
    """Print and log one row per commit the mirror setting let through."""
    paths = sorted({p for p, _s, _t, _h in (undo or [])} | {p for p, _r, _m in (findings or [])})
    lines = sum(len(h) for _p, _s, _t, h in (undo or [])) + sum(len(m) for _p, _r, m in (findings or []))
    where = _log_escape(repo, ", ".join(paths)[:300], "", "", lines,
                        "mirror repo (%s=true)" % MIRROR_KEY,
                        "repo mirrors files from elsewhere; %d path(s) would have been refused"
                        % len(paths))
    print("[stale-overwrite] ALLOWED: mirror repo (%s=true): %d path(s), %d line(s) the guard "
          "would have refused. %s"
          % (MIRROR_KEY, len(paths), lines,
             "Logged to %s" % where if where else "NOT LOGGED: the escape log could not be written"),
          file=stream)


def excuse(repo, findings, stream=sys.stderr):
    """Rule B findings still refused, after REVERT_HEAD and STALE_GUARD_REPLACES.

    Each finding is matched on its OWN commit. An `<unreadable>` finding is never excused: the
    check did not run, so there is nothing a declaration could be matched against.
    """
    reverting = revert_head(repo)
    declared, _bad, reason = declared_replacements(repo)
    left = []
    _EXCUSED.clear()
    for path, sha, subject, hit in findings:
        if sha == reverting:
            via = "revert (REVERT_HEAD)"
        elif sha in declared:
            via = "%s=%s" % (REPLACES_ENV, declared[sha])
        else:
            left.append((path, sha, subject, hit))
            continue
        _EXCUSED.add(sha)
        where = _log_escape(repo, path, sha, subject, len(hit), via, reason)
        print("[stale-overwrite] ALLOWED: %s removes %d line(s) that %s added - %s. %s"
              % (path, len(hit), sha[:9], via,
                 "Logged to %s" % where if where else "NOT LOGGED: the escape log could not be written"),
              file=stream)
    return left


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


def report_undo(findings, stream=sys.stderr, repo=None):
    p = lambda s="": print(s, file=stream)  # noqa: E731
    p("BLOCKED: this commit REMOVES an earlier commit's work, in full, without saying so.\n")
    for path, sha, subject, hit in findings:
        sha = sha[:9]
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

    # ⛔ A DECLARATION THAT DID NOT MATCH IS SAID OUT LOUD. Silently refusing it again would
    # read as "the escape is broken", which is how a guard ends up bypassed.
    if repo is not None:
        declared, bad, _why = declared_replacements(repo)
        reverting = revert_head(repo)
        # a SHA that DID excuse another finding is not wrong, so it is not listed as wrong
        wrong = {f: t for f, t in declared.items() if f not in _EXCUSED}
        if wrong or bad:
            p("    %s was set, but it does not name the commit above:" % REPLACES_ENV)
            for full, tok in wrong.items():
                p("        %s is commit %s, which is not one whose lines this removes" % (tok, full[:9]))
            for tok in bad:
                p("        %s is not a commit SHA in this repository" % tok)
            p()
        if reverting:
            p("    A revert of %s is in progress, but the lines above belong to a different commit.\n"
              % reverting[:9])

    # ⭐ THE COMMITS ALREADY EXCUSED GO IN TOO. The variable is replaced, not added to, so a
    # remedy naming only the missing commit would, followed literally, refuse the other one.
    need = ",".join(dict.fromkeys([s[:9] for s in sorted(_EXCUSED)]
                                  + [sha[:9] for _p, sha, _s, _h in findings
                                     if not sha.startswith("<")]))
    if need:
        p("    Deliberately undoing or replacing that commit? Name it on the commit command -")
        p("    it is matched against the commit above, printed, and logged:")
        p("      %s=%s git commit ...                       (sh / Git Bash)" % (REPLACES_ENV, need))
        p("      $env:%s='%s'; git commit ...               (PowerShell)" % (REPLACES_ENV, need))
        p("    A `git revert` of it that reaches this hook is allowed without it.")


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

    # One line, so an exemption shows in the commit output instead of looking like a clean check.
    for path in generated_staged(repo):
        print("[stale-overwrite] not checked - its job rewrites it whole every run: %s" % path,
              file=sys.stderr)

    undo = check_undoes_commit(repo)
    if undo:
        undo = excuse(repo, undo)
    findings = check(repo)

    # ESCAPE 3, a repo that MIRRORS files from elsewhere (W143). Its working copy is rewritten
    # whole from another place on purpose, so a line the mirror source dropped is not a lost
    # edit. Every commit this lets through that would otherwise be refused is printed and logged.
    if (undo or findings) and is_mirror_repo(repo):
        log_mirror_pass(repo, undo, findings)
        return 0

    if undo:
        report_undo(undo, repo=repo)
        return REFUSE
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
