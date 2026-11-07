#!/usr/bin/env python3
"""Give a test its own state directories and workspace, so running it writes nothing shared.

<!-- subject: infrastructure -->

<!-- route-tags: test isolation sandbox temp workspace state dir shared checkout -->

When parallel sessions run tests, a test that hardcodes a shared path writes into every other
session's state. Typical cases: a test that writes a throwaway document into the shared checkout
and commits it; a test that snapshots and restores a shared state folder, which reverts whatever
another session wrote while it ran; a timing run that appends rows to a shared usage log.

Every one of those locations has ONE environment override, read in one place, whose default is
the real path. This module sets all of them at once:

    CLAUDE_SHARED_STATE_DIR  shared_state.state_dir()       the workspace's shared logs
    CLAUDE_HOME_STATE_DIR    shared_state.home_state_dir()  per-user state under ~/.claude/state
    ORCHDOC_LANE_STATE       the lane id records

USE, as the first thing a test does - BEFORE importing the module under test, because several
of them compute these paths at import time:

    import sandbox_env
    SANDBOX = sandbox_env.isolate("mytest-")       # a temp dir; removed at exit

Subprocesses inherit the overrides through os.environ, including ones started with
`env=dict(os.environ, ...)`. A test that builds an environment from nothing must add
`sandbox_env.env_overrides()` to it.

`git_workspace(parent)` builds a throwaway git repo with a bare origin, for a test that needs
orchdoc to commit, strike or land somewhere real that nobody else uses.
"""
import atexit
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile

try:
    from no_window import flags
except ImportError:  # a partial delivery must not take the tool down
    def flags(extra=0):
        return extra

#: (environment variable, sub-directory of the sandbox it points at)
OVERRIDES = (
    ("CLAUDE_SHARED_STATE_DIR", "shared-state"),
    ("CLAUDE_HOME_STATE_DIR", "home-state"),
    ("ORCHDOC_LANE_STATE", "home-state/lane-ids"),
)


def env_overrides(root):
    """{var: path} for a sandbox rooted at `root` - for a test that builds env from nothing."""
    root = pathlib.Path(root)
    return {var: str(root / sub) for var, sub in OVERRIDES}


#: The keyword shutil.rmtree takes its error handler by: `onexc` from Python 3.12, `onerror`
#: before that.
_HANDLER_KW = "onexc" if sys.version_info >= (3, 12) else "onerror"


def _make_writable_and_retry(func, path, _exc):
    """shutil.rmtree's error handler: clear the read-only bit on `path` and retry the one call
    that failed. What still fails is left in place, and nothing is raised."""
    try:
        # S_IRWXU holds S_IWRITE, the only bit os.chmod reads on Windows; on POSIX it also
        # lets the owner list and enter a directory.
        os.chmod(path, stat.S_IRWXU)
        func(path)
    except Exception:
        pass


def _remove_tree(root):
    """Delete the sandbox at `root`, never raising. isolate() registers this to run at exit.

    git writes its object files read-only, and os.unlink on Windows refuses a read-only file.
    `shutil.rmtree(root, ignore_errors=True)` skips those files without a word, so every sandbox
    that held a git repo would stay in the temp dir.
    """
    try:
        shutil.rmtree(root, **{_HANDLER_KW: _make_writable_and_retry})
    except Exception:
        pass


def isolate(prefix="sandbox-", keep=False):
    """Make a temp sandbox, point every override at it in os.environ, and return its root.

    `keep=True` leaves the directory behind for debugging; otherwise it is removed at exit.
    """
    root = pathlib.Path(tempfile.mkdtemp(prefix=prefix))
    for var, path in env_overrides(root).items():
        pathlib.Path(path).mkdir(parents=True, exist_ok=True)
        os.environ[var] = path
    # A test runs as no orchestrator. `orchdoc.py note` refuses a caller whose $ORCHDOC_ME
    # names one, and a test run from an orchestrator's session inherits it. $CLAUDECODE (every
    # Claude Code shell) and $ORCHDOC_LANE (a lane) mark a session too; a test that writes a
    # note as the human must not inherit them.
    for var in ("ORCHDOC_ME", "CLAUDECODE", "ORCHDOC_LANE"):
        os.environ.pop(var, None)
    if not keep:
        atexit.register(_remove_tree, str(root))
    return root


def _git(cwd, *args):
    r = subprocess.run(["git", "-C", str(cwd)] + list(args), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120, creationflags=flags())
    if r.returncode != 0:
        raise RuntimeError("sandbox git %s failed: %s" % (" ".join(args), r.stderr.strip()[:300]))
    return r.stdout


def git_workspace(parent, name="ws", files=None):
    """A git repo at <parent>/<name> on `main`, pushed to a bare origin at <parent>/<name>.git.

    `files` is {relative path: text} committed as the first commit (a README if omitted).
    Returns the repo path. Nothing in it can reach a real remote.
    """
    # Imported here and not at the top of this file: orchdoc.py imports this module and runs
    # in the pre-commit hook, where it needs the GIT_DIR and GIT_INDEX_FILE that git_guard
    # removes.
    import git_guard  # noqa
    parent = pathlib.Path(parent)
    ws, bare = parent / name, parent / (name + ".git")
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], capture_output=True,
                   timeout=60, creationflags=flags())
    _git(bare, "symbolic-ref", "HEAD", "refs/heads/main")
    subprocess.run(["git", "init", "-q", "-b", "main", str(ws)], capture_output=True,
                   timeout=60, creationflags=flags())
    for k, v in (("user.email", "sandbox@example.invalid"), ("user.name", "sandbox"),
                 ("commit.gpgsign", "false"), ("core.autocrlf", "false")):
        _git(ws, "config", k, v)
    _git(ws, "remote", "add", "origin", str(bare))
    for rel, text in (files or {"README.md": "sandbox workspace\n"}).items():
        p = ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
        _git(ws, "add", rel)
    _git(ws, "commit", "-q", "-m", "sandbox seed")
    _git(ws, "push", "-q", "origin", "main")
    _git(ws, "remote", "set-head", "origin", "main")
    return ws


if __name__ == "__main__":
    import sys
    root = isolate("sandbox-selftest-")
    ok = all(os.environ.get(v) == str(root / s) and (root / s).is_dir() for v, s in OVERRIDES)
    ws = git_workspace(root)
    ok2 = (ws / "README.md").exists() and "origin/main" in _git(ws, "branch", "-r")
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import shared_state
    ok3 = (shared_state.state_dir() == root / "shared-state"
           and shared_state.home_state_dir() == root / "home-state")
    print("  [%s] every override points inside the sandbox" % ("OK " if ok else "FAIL"))
    print("  [%s] git_workspace builds a repo pushed to a bare origin" % ("OK " if ok2 else "FAIL"))
    print("  [%s] shared_state honours both state overrides" % ("OK " if ok3 else "FAIL"))
    sys.exit(0 if (ok and ok2 and ok3) else 1)
