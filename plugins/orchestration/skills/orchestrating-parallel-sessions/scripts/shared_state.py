#!/usr/bin/env python3
"""Enumerate GLOBAL MUTABLE STATE - anything one session can write that another silently reads.

<!-- subject: infrastructure -->

<!-- route-tags: shared state global cache mutable cross-session marker autosave stash
     collision audit worktree -->

That is not the same as "shared". `<your-secrets-folder>/` is shared and safe: it is read-mostly, and a
write to it is a deliberate act nobody performs by accident. `refs/stash` is shared and unsafe
because an ordinary local-looking command writes it on your behalf.

Four classes, and only the first is a hazard:

Usage:  shared_state.py            report
        shared_state.py --selftest
"""
import argparse
import os
import pathlib
import subprocess
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
WS = pathlib.Path(__file__).resolve().parent.parent.parent
HOME = pathlib.Path(os.path.expanduser("~"))


def git(*a):
    r = subprocess.run(["git", "-C", str(WS)] + list(a), capture_output=True, text=True,
                       creationflags=flags())
    return r.stdout.strip()


def main_checkout():
    """The ONE working tree, never the worktree a script happens to live in.

    `git rev-parse --git-common-dir` answers this from any worktree - it names the shared
    `.git`, whose parent is the main tree. Asked with `-C` the script's own directory, so
    the answer does not depend on the caller's cwd.
    """
    here = pathlib.Path(__file__).resolve().parent
    fallback = here.parent.parent                      # .shared/scripts -> .shared -> ws
    try:
        r = subprocess.run(["git", "-C", str(here), "rev-parse", "--git-common-dir"],
                           capture_output=True, text=True, creationflags=flags())
        if r.returncode == 0 and r.stdout.strip():
            common = pathlib.Path(r.stdout.strip())
            if not common.is_absolute():
                common = (here / common).resolve()
            root = common.parent
            if (root / ".shared" / "scripts").is_dir():
                return root
    except Exception:
        pass
    return fallback


SHARED_STATE_ENV = "CLAUDE_SHARED_STATE_DIR"
HOME_STATE_ENV = "CLAUDE_HOME_STATE_DIR"


def state_dir():
    """`<main checkout>/.shared/state` - the one place cross-session logs may be written.

    `$CLAUDE_SHARED_STATE_DIR` replaces it outright, for tests.

    A copy of these scripts that sits outside a workspace (an installed skill or plugin folder,
    with no `.shared/scripts` above it) has no main checkout. `main_checkout()` then answers with
    a folder that is not a workspace, and a log written under it would land inside the installed
    plugin, which is replaced on every update. Such a copy writes to `<home state>/shared`.
    """
    env = os.environ.get(SHARED_STATE_ENV)
    if env:
        return pathlib.Path(env)
    root = main_checkout()
    if not (root / ".shared" / "scripts").is_dir():
        return home_state_dir() / "shared"
    return root / ".shared" / "state"


def home_state_dir():
    """`~/.claude/state` - per-machine markers and logs (route-usage.jsonl, orchdoc-stop/, ...).

    `$CLAUDE_HOME_STATE_DIR` replaces it outright, for tests.
    """
    env = os.environ.get(HOME_STATE_ENV)
    if env:
        return pathlib.Path(env)
    return HOME / ".claude" / "state"


def _count(path, pattern="*"):
    p = pathlib.Path(path)
    if not p.exists():
        return None
    if p.is_file():
        return 1
    try:
        return len(list(p.glob(pattern)))
    except OSError:
        return None


def survey():
    """[(name, klass, detail, why)] - measured, never asserted."""
    rows = []

    n_stash = len([x for x in git("stash", "list").split("\n") if x.strip()])
    rows.append((
        "refs/stash", "SILENT" if n_stash else "SILENT (empty)",
        "%d entr(ies), visible from every worktree" % n_stash,
        "ONE ref for the whole repo. `git stash` reads as local and is not."))

    auto = [b for b in git("branch", "-r").split("\n") if "autosave/" in b]
    rows.append((
        "autosave/* branches", "SILENT",
        "%d branch(es), force-pushed, self-pruning" % len(auto),
        "⛔ NOT A BACKUP - a rolling net. a prune job deletes them after ~3 days, so "
        "work living only there is on an invisible countdown."))

    st = home_state_dir()
    files = sorted(p.name for p in st.glob("*")) if st.exists() else []
    rows.append((
        "~/.claude/state/", "SILENT",
        "%d marker file(s): %s" % (len(files), ", ".join(files[:5])),
        "A marker means 'this happened today'. Any session's write silences every other "
        "session's check."))

    rows.append((
        "plugin marketplace cache", "ANNOUNCED",
        "version-keyed; a version-guard hook bumps on content change",
        "A copy can drift behind canonical and teach an old rule. So it is "
        "announced: the guard refuses a content change that leaves the version alone, because "
        "the cache is keyed by version and a silent edit reaches nobody."))

    rows.append((
        "mailboxes/", "ANNOUNCED",
        "%s file(s)" % (_count(WS / "mailboxes") or 0),
        "Shared directory, but one file per lane and writes are append-shaped. Collisions are "
        "visible in the file."))

    rows.append((
        "<your-secrets-folder>/", "READ-MOSTLY",
        "%s file(s)" % (_count(WS / ".shared" / "secrets") or 0),
        "Shared by design and safe for it: reads are constant, writes are a deliberate human "
        "act. Shared is not the hazard - SILENTLY WRITTEN is."))

    rows.append((
        "git worktrees", "SCOPED",
        "%d tree(s)" % len([x for x in git("worktree", "list").split("\n") if x.strip()]),
        "Each has its own index, HEAD and working files. This is the fix for the tree; it does "
        "NOT scope refs/stash, which is why the guard exists."))

    rows.append((
        ".code-graph/ graphs", "SCOPED",
        "per-directory",
        "One graph per scanned root. A stale graph misleads its own readers only."))

    return rows


def report():
    rows = survey()
    order = {"SILENT": 0, "SILENT (empty)": 1, "ANNOUNCED": 2, "READ-MOSTLY": 3, "SCOPED": 4}
    rows.sort(key=lambda r: order.get(r[1], 9))
    print("  GLOBAL MUTABLE STATE - one session writes, another reads\n")
    for name, klass, detail, why in rows:
        print("  [%-13s] %s" % (klass, name))
        print("      %s" % detail)
        for line in _wrap(why, 86):
            print("      %s" % line)
        print()
    silent = [r for r in rows if r[1].startswith("SILENT") and "empty" not in r[1]]
    print("  %d of %d classified SILENT - a write changes another session with no signal."
          % (len(silent), len(rows)))
    print()
    print("  ⭐ SHARED IS NOT THE HAZARD. `<your-secrets-folder>/` is shared and safe because writing")
    print("     it is a deliberate act. The hazard is state an ORDINARY-LOOKING command writes")
    print("     on your behalf, which is why every SILENT row above needs a guard or a report,")
    print("     not a note telling people to be careful.")
    return 0


def _wrap(s, n):
    out, cur = [], ""
    for w in s.split():
        if len(cur) + len(w) + 1 > n:
            out.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        out.append(cur)
    return out


def selftest():
    rows = survey()
    ok = len(rows) >= 8 and any(r[1].startswith("SILENT") for r in rows)
    print("  [%s] survey returns %d row(s) and classifies at least one SILENT"
          % ("OK " if ok else "FAIL", len(rows)))
    unguarded = [r[0] for r in rows if r[1] == "SILENT"
                 and not any(k in r[3] for k in ("GUARDED", "SURFACED", "exact"))]
    ok2 = not unguarded
    print("  [%s] every SILENT row names a guard or a report%s"
          % ("OK " if ok2 else "FAIL", "" if ok2 else ": %s" % ", ".join(unguarded)))
    print("\nselftest %s" % ("PASSED" if (ok and ok2) else "FAILED"))
    return 0 if (ok and ok2) else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    sys.exit(selftest() if a.selftest else report())
