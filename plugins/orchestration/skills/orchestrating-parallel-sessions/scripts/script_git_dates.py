"""The date a file was first added to git, for the reuse check's "which came first" note.

<!-- subject: script-writing -->

check_script.py may not import subprocess (test_check_script_no_production.py, o8 guard G12:
the gate must have no route to a shell, a database or the network). The one thing the reuse
check needs from git is read-only and local: the author date of the commit that first added a
file. It lives here, with a fixed `git log` command and nothing else, so the guard on
check_script.py stays as ruled. (o3:W34, lane o9L1154.)
"""
import io
import os
import subprocess
import tarfile

import no_window


def first_added(path, workspace):
    """(iso_date, None) of the commit that FIRST ADDED `path`, or (None, reason).

    FILE creation date, not the date of a phrase: `git log -S` cannot be used because the
    reuse check holds lowercased, punctuation-free words, which never equal the text in the
    file. A file's first-add date says when the SCRIPT began; a phrase may have been edited
    in later, so callers must present the answer as an approximation.
    """
    try:
        rel = os.path.relpath(os.path.abspath(path), workspace).replace("\\", "/")
    except ValueError:
        return (None, "not inside the git workspace")
    if rel.startswith(".."):
        return (None, "not inside the git workspace")
    try:
        out = subprocess.run(
            ["git", "-C", workspace, "log", "--follow", "--diff-filter=A",
             "--format=%aI", "--", rel],
            capture_output=True, text=True, timeout=60,
            creationflags=no_window.flags())
    except (OSError, subprocess.SubprocessError):
        return (None, "git could not be run")
    lines = [x for x in out.stdout.split("\n") if x.strip()]
    if out.returncode == 0 and lines:
        return (lines[-1].strip(), None)
    return (None, "not tracked by git")


def export_library(workspace, commit, dest):
    """Write `commit`'s script-library folder into dest/script-library, read-only on the repo
    (`git archive`). Returns (True, None), or (False, reason). For --readd-reuse-rows, which re-adds
    only rows a fresh index of the freeze commit has (o10L339 SF-b, lane o9L1189)."""
    if not commit:
        return (False, "no commit named")
    try:
        out = subprocess.run(
            ["git", "-C", workspace, "archive", "--format=tar", commit, "script-library"],
            capture_output=True, timeout=600, creationflags=no_window.flags())
    except (OSError, subprocess.SubprocessError):
        return (False, "git could not be run")
    if out.returncode != 0:
        return (False, "git archive %s failed: %s"
                % (commit, out.stderr.decode("utf-8", "replace").strip()[:200]))
    with tarfile.open(fileobj=io.BytesIO(out.stdout)) as t:
        if hasattr(tarfile, "data_filter"):
            t.extractall(dest, filter="data")
        else:
            t.extractall(dest)
    return (True, None)
