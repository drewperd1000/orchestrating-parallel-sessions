#!/usr/bin/env python3
"""Refuse a commit that would drop lines which reached the canonical branch after your copy.

<!-- subject: infrastructure -->

<!-- route-tags: stale copy overwrite guard pre-commit destroyed work canonical origin main
     worktree behind branch point lost commit no conflict -->

## The rule

Refusing every deleted line would block ordinary edits, so it fires on a narrower thing:

> A stale whole-file write does not delete a scattered handful of lines. It deletes **exactly
> the lines that arrived after the copy was taken** - which means the removed set CONTAINS,
> nearly in full, the additions of one identifiable earlier commit. A targeted edit almost
> never does that.

    RULE A  branch behind:   lines(canonical) - lines(merge-base), missing from staged
    RULE B  undoes a commit: |added_by(X) & removed_here| / |added_by(X)| >= 0.9

## Undoing or replacing a commit on purpose: two declared escapes, neither `--no-verify`

    ESCAPE 1  REVERT_HEAD           a finding whose commit IS the one being reverted is allowed
    ESCAPE 2  STALE_GUARD_REPLACES  a finding whose commit is NAMED in this variable is allowed

Each allowed finding prints one `[stale-overwrite] ALLOWED` line and appends a row to
`.shared/state/stale-overwrite-escapes.jsonl` in the workspace's main checkout (repo, branch,
HEAD, path, the commit, how it was excused, the reason given). That log is local, like
`gate-refusals.jsonl` beside it, and does not survive the loss of this computer.
"""
import datetime
import io
import json
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

CANONICAL_CANDIDATES = ("origin/main", "origin/master")
PRODUCTION_REF = "origin/production"

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
            return _production_if_cut_from_it(repo, ref)
    return None


def _production_if_cut_from_it(repo, main_ref):
    if git(repo, ["rev-parse", "--verify", "--quiet", PRODUCTION_REF])[0] != 0:
        return main_ref
    if git(repo, ["merge-base", "--is-ancestor", main_ref, "HEAD"])[0] == 0:
        return main_ref                   # HEAD already holds main: an ordinary branch
    rc, base, _ = git(repo, ["merge-base", "HEAD", main_ref])
    base = base.strip()
    if rc != 0 or not base:
        return main_ref
    # HEAD has no history of main's that production lacks: it was cut from production.
    if git(repo, ["merge-base", "--is-ancestor", base, PRODUCTION_REF])[0] != 0:
        return main_ref
    # The graph allows production; only the branch's own record says it meant to.
    return PRODUCTION_REF if _declares_production(repo) else main_ref


def _declares_production(repo):
    rc, branch, _ = git(repo, ["symbolic-ref", "--quiet", "--short", "HEAD"])
    branch = branch.strip()
    if rc != 0 or not branch:
        return False                      # detached HEAD: nothing declares anything
    rc, up, _ = git(repo, ["rev-parse", "--abbrev-ref", "--symbolic-full-name",
                           branch + "@{upstream}"])
    if rc == 0 and up.strip() == PRODUCTION_REF:
        return True
    rc, log, _ = git(repo, ["reflog", "show", "--format=%gs", "refs/heads/" + branch, "--"])
    lines = [l.strip() for l in log.splitlines() if l.strip()] if rc == 0 else []
    return bool(lines) and lines[-1] in ("branch: Created from " + PRODUCTION_REF,
                                         "branch: Created from refs/remotes/" + PRODUCTION_REF)


def inherited_origin(repo):
    """Reason this repo's `origin/*` cannot be trusted, or None.

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


# A file its own job rewrites whole on every run holds nobody's work, so removing lines the last
# commit added is not a stale overwrite. List each such file by its exact path from the repo root.
# Exact paths and not a pattern, because the same folder may hold a file a person edits. Add a
# path ONLY for a file that its job rewrites whole every run and that no person edits.
GENERATED_PATHS = frozenset((
    "automation/nightly_report/state/report.json",
))


def is_generated(path):
    return path.replace("\\", "/").strip() in GENERATED_PATHS


CHECKSUM_LINE_PATHS = frozenset((
))
_CHECKSUM_LINE = re.compile(r'^\s*"(sha256|bytes)":\s')


def without_checksum_lines(path, lines):
    """`lines` minus per-file checksum lines, for the paths in CHECKSUM_LINE_PATHS only."""
    if path.replace("\\", "/").strip() not in CHECKSUM_LINE_PATHS:
        return set(lines)
    return {l for l in lines if not _CHECKSUM_LINE.match(l)}


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


CODE_SUFFIXES = (".py", ".ps1", ".sh", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
                 ".json", ".yml", ".yaml", ".toml", ".sql", ".css", ".html", ".astro")


def code_paths(repo):
    return [p for p in staged_paths(repo) if p.lower().endswith(CODE_SUFFIXES)]


def merging_in(repo, ref):
    """True when a merge is in progress with exactly one other parent and `ref` is inside it."""
    rc, path, _ = git(repo, ["rev-parse", "--git-path", "MERGE_HEAD"])
    if rc != 0 or not path.strip():
        return False
    p = pathlib.Path(path.strip())
    if not p.is_absolute():
        p = pathlib.Path(repo) / p
    try:
        heads = [l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    except OSError:
        return False
    if len(heads) != 1:
        return False
    return git(repo, ["merge-base", "--is-ancestor", ref, heads[0]])[0] == 0


def _strip_comma(line):
    """The line without its trailing comma, which a later entry adds and is not content."""
    return line.rstrip().rstrip(",")


def check(repo):
    """[(path, ref, [missing lines])] - empty when nothing is at risk OR it cannot tell.
    """
    ref = canonical_ref(repo)
    if not ref:
        return []
    # Already contains canonical - your copy descends from everything on it, so nothing on it
    # can be news to you.
    if git(repo, ["merge-base", "--is-ancestor", ref, "HEAD"])[0] == 0:
        return []
    merging_canonical = merging_in(repo, ref)
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
        arrived = without_checksum_lines(path, arrived)
        have = set(staged.split("\n"))
        if merging_canonical or path.lower().endswith(".json"):
            have |= {_strip_comma(l) for l in have}
            missing = sorted(l for l in arrived
                             if l not in have and _strip_comma(l) not in have and l.strip())
        else:
            missing = sorted(l for l in arrived if l not in have and l.strip())
        if missing:
            out.append((path, ref, missing))
    return out


# ---- rule B: does this commit remove a whole earlier commit's additions? --------------------

MIN_ADDED = 6          # below this, an overlap is coincidence rather than a signature
DEPTH = 30             # commits of history to consider per file

OVERLAP = 0.70


def _interesting(line):
    """Lines distinctive enough that matching them means something.
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
        removed = without_checksum_lines(
            path, {l.strip() for l in head_blob.split("\n") if _interesting(l)}
            - {l.strip() for l in staged.split("\n")})
        if len(removed) < MIN_ADDED:
            continue

        rc, log, _ = git(repo, ["log", "--format=%H%x1f%s", "-n", str(DEPTH), "HEAD", "--", path])
        if rc != 0:
            continue
        for row in log.split("\n"):
            if "\x1f" not in row:
                continue
            sha, subject = row.split("\x1f", 1)
            added = without_checksum_lines(path, _added_lines(repo, sha.strip(), path))
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
# somewhere else. Only the repo's own config counts: a
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


def _commits_since_branch_point(repo, ref, path):
    """Full shas of the commits on `ref` since HEAD's branch point that changed `path`."""
    rc, base, _ = git(repo, ["merge-base", "HEAD", ref])
    rc2, log, _ = git(repo, ["log", "--format=%H", "%s..%s" % (base.strip(), ref), "--", path])
    return [s.strip() for s in log.split("\n") if s.strip()] if rc == 0 and rc2 == 0 else []


def excuse_stale(repo, findings, stream=sys.stderr):
    """
    A file is excused only when EVERY commit that changed it between the branch point and the
    canonical ref is named in the variable. Naming one commit must not excuse dropping the
    lines another commit brought, the same stance `excuse` takes for rule B.
    """
    declared, _bad, reason = declared_replacements(repo)
    if not declared or not findings:
        return findings
    left = []
    for path, ref, missing in findings:
        shas = _commits_since_branch_point(repo, ref, path)
        if shas and all(s in declared for s in shas):
            for s in shas:
                where = _log_escape(repo, path, s, "(rule A) commit on %s" % ref, len(missing),
                                    "%s=%s" % (REPLACES_ENV, declared[s]), reason)
                print("[stale-overwrite] ALLOWED: %s drops %d line(s) from %s - %s=%s. %s"
                      % (path, len(missing), s[:9], REPLACES_ENV, declared[s],
                         "Logged to %s" % where if where else "NOT LOGGED"), file=stream)
        else:
            left.append((path, ref, missing))
    return left


def report(findings, stream=sys.stderr, repo=None):
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
    p("    not merging one.\n")
    p("    DO THIS INSTEAD")
    ref0 = findings[0][1] if findings else "origin/main"
    p("      git fetch origin && git rebase %s      # then re-apply your edit" % ref0)
    p("    or bring it in by merge, and keep those lines when you resolve (only a trailing comma")
    p("    on them may change):")
    p("      git fetch origin && git merge %s" % ref0)
    p("    or, to see exactly what you are missing first:")
    p("      git diff %s -- <path>\n" % ref0)

    need = ""
    if repo is not None:
        wanted = dict.fromkeys(s for path, ref, _m in findings
                               for s in _commits_since_branch_point(repo, ref, path))
        need = ",".join(s[:9] for s in wanted)
        declared, bad, _why = declared_replacements(repo)
        if declared or bad:
            p("    %s was set, but it does not name every commit below." % REPLACES_ENV)
            for tok in bad:
                p("        %s is not a commit SHA in this repository" % tok)
            p()
    p("    Deliberate, and you have read what you are removing? Name every commit on %s that" % ref0)
    p("    changed the file since your branch point. The guard allows it and logs it:")
    if need:
        p("      %s=\"%s: why\" git commit ...           (sh / Git Bash)" % (REPLACES_ENV, need))
        p("      $env:%s='%s: why'; git commit ...       (PowerShell)" % (REPLACES_ENV, need))
    else:
        p("      %s=\"<sha>[,<sha>...]: why\" git commit ..." % REPLACES_ENV)
        p("    Find them with: git log --oneline $(git merge-base HEAD %s)..%s -- <path>" % (ref0, ref0))


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
    p("    reports it as an ordinary modification because it is one.\n")
    p("    DO THIS INSTEAD")
    p("      git diff --cached -- <path>        # read what you are removing")
    p("      git show <sha> -- <path>           # read the commit you would be undoing")
    p("    Then re-apply your edit to the CURRENT file rather than re-writing your copy.\n")

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

    need = ",".join(dict.fromkeys([s[:9] for s in sorted(_EXCUSED)]
                                  + [sha[:9] for _p, sha, _s, _h in findings
                                     if not sha.startswith("<")]))
    if need:
        p("    Deliberately undoing or replacing that commit? Name it on the commit command -")
        p("    it is matched against the commit above, printed, and logged:")
        p("      %s=%s git commit ...                       (sh / Git Bash)" % (REPLACES_ENV, need))
        p("      $env:%s='%s'; git commit ...               (PowerShell)" % (REPLACES_ENV, need))
        p("    A `git revert` of it that reaches this hook is allowed without it.")


try:
    import orchdoc_putback
except Exception:  # a partial delivery must not stop a commit
    orchdoc_putback = None


FOREIGN_ENV = "FOREIGN_ORCHDOC_EDIT"
_DOC_RE = re.compile(r"(?:^|/)ORCHESTRATOR-DECISIONS-(o[0-9]+)\.md$", re.I)
_OID_RE = re.compile(r"o[0-9]+", re.I)


def committer_id(repo):
    v = (os.environ.get("ORCHDOC_ME") or "").strip()
    if not v:
        top = git(repo, ["rev-parse", "--show-toplevel"])[1].strip()
        for base in (top, str(repo)):
            try:
                v = pathlib.Path(base, ".orchdoc-me").read_text(encoding="utf-8").strip()
            except OSError:
                v = ""
            if v:
                break
    if not v:
        v = git(repo, ["config", "user.name"])[1].strip()
    return v.lower() if _OID_RE.fullmatch(v or "") else None


def check_foreign_orchdoc(repo):
    """[(path, owner, committer, [removed lines])] for staged changes to someone else's OrchDoc."""
    me = committer_id(repo)
    if not me:
        return []
    allowed = (os.environ.get(FOREIGN_ENV) or "").split(":", 1)[0].replace(",", " ").lower().split()
    out = []
    for path in _staged_all(repo):
        m = _DOC_RE.search(path.replace(chr(92), "/"))
        if not m or m.group(1).lower() == me or m.group(1).lower() in allowed:
            continue
        head_blob, staged = _blob(repo, "HEAD:%s" % path), _blob(repo, ":%s" % path)
        if not head_blob or not staged:
            continue                       # new, binary or unreadable: nothing to say
        kept = {x.strip() for x in staged.split("\n")}
        gone = [l for l in head_blob.split("\n") if _interesting(l) and l.strip() not in kept]
        if gone:
            out.append((path, m.group(1).lower(), me, gone))
    return out


def report_foreign(findings, verdict="BLOCKED", stream=sys.stderr):
    p = lambda s="": print(s, file=stream)  # noqa: E731
    p("%s: this commit removes lines from another orchestrator's OrchDoc.\n" % verdict)
    for path, owner, me, gone in findings:
        p("    %s  (owner %s, you are %s) - %d line(s) your staged copy lacks:"
          % (path, owner, me, len(gone)))
        for l in gone[:MAX_REPORT]:
            p("        %s" % l[:110].rstrip())
    p()
    p("    Your copy of that file is older than what its owner landed.")
    p("    Unstage it:   git restore --staged -- <path>")
    p("    Deliberate?  %s=\"%s: <why>\" git commit ..." % (FOREIGN_ENV, findings[0][1]))


REFUSE = 9


def orchdoc_checks(repo):
    if orchdoc_putback is None:
        return 0
    try:
        foreign = [] if orchdoc_putback.in_progress(repo) else check_foreign_orchdoc(repo)
        back = orchdoc_putback.in_index(repo)
        if not foreign and not back:
            return 0
        mode = orchdoc_putback.mode()
        if mode == "off":
            return 0
        verdict = "BLOCKED" if mode == "block" else "WARNING"
        if foreign:
            report_foreign(foreign, verdict)
            if verdict == "WARNING":
                orchdoc_putback.hook_mode.warn(orchdoc_putback.HOOK, {
                    "foreign": [{"doc": f[0], "owner": f[1], "me": f[2], "lines": len(f[3])}
                                for f in foreign]})
        if back:
            orchdoc_putback.report(back, verdict)
        return REFUSE if verdict == "BLOCKED" else 0
    except Exception as exc:
        print("[stale-overwrite] OrchDoc checks not run: %s" % exc, file=sys.stderr)
        return 0


def main(argv):
    repo = argv[1] if len(argv) > 1 else os.getcwd()

    if orchdoc_checks(repo) == REFUSE:
        return REFUSE

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
    if findings:
        findings = excuse_stale(repo, findings)

    if (undo or findings) and is_mirror_repo(repo):
        log_mirror_pass(repo, undo, findings)
        return 0

    if undo:
        report_undo(undo, repo=repo)
        return REFUSE
    if not findings:
        return 0
    report(findings, repo=repo)
    return REFUSE


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception:
        sys.exit(0)
