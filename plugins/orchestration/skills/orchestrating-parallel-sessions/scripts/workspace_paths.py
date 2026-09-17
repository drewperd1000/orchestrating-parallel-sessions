#!/usr/bin/env python3
"""Where the workspace is, resolved instead of hardcoded - so these tools can SHIP.

<!-- route-tags: workspace path portable shippable root memory scripts resolve hardcoded -->

⛔ THE REASON PHASE 1-3 SHIPPED NOWHERE. Every gate was wired with
`WS = Path(r"C:\\Users\\<the-owner>\\<the-workspace>")` - one machine's absolute path, spelled
out. That is not a configuration detail, it is
the thing that made the whole decision tree workspace-local by construction: a script carrying
one machine's absolute path cannot run in a skill, a fresh clone, a worktree or a cloud
session.\1The human asked what percentage had shipped. The answer was zero, and this was why.

⭐ FOUR SOURCES, MOST SPECIFIC FIRST, and each one is a fact rather than a guess:

  1. `$CLAUDE_WORKSPACE` - an explicit override always wins
  2. `git rev-parse --show-toplevel` - the repo the CALLER is actually in. This is what makes
     a worktree work: knowledge_gate once read DEV-DOCS-INDEX from the main checkout while
     committing from a worktree, and printed an instruction impossible to satisfy
  3. walk up from THIS file - these scripts live at `<workspace>/.shared/scripts/`, so the
     grandparent is the workspace whenever the file is where it belongs
  4. the historical default - so nothing breaks on the one machine that has it

The memory dir gets the same treatment via `$CLAUDE_MEMORY_DIR`.

Import and use; never re-derive:

    from workspace_paths import WS, MEM, SCRIPTS
"""
import os
import pathlib
import subprocess

_HISTORICAL_WS = pathlib.Path(r"<your-workspace>")
_HISTORICAL_MEM = (pathlib.Path.home()
                   / ".claude/projects/C--Users-<your-user>-Claude-Projects/memory")


def _from_git():
    try:
        p = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=10)
        if p.returncode == 0 and p.stdout.strip():
            return pathlib.Path(p.stdout.strip())
    except Exception:
        pass
    return None


def _from_file():
    # <workspace>/.shared/scripts/workspace_paths.py -> up three
    here = pathlib.Path(__file__).resolve()
    if here.parent.name == "scripts" and here.parent.parent.name == ".shared":
        return here.parent.parent.parent
    return None


def resolve_workspace():
    env = os.environ.get("CLAUDE_WORKSPACE")
    if env and pathlib.Path(env).exists():
        return pathlib.Path(env)
    for cand in (_from_git(), _from_file()):
        # A repo root only counts as THE workspace if it looks like it - otherwise a script
        # run from inside a nested product repo would claim that repo as the workspace and
        # then look for DEV-DOCS-INDEX.md in it. Wrong root is worse than no root: it makes
        # every subsequent read confidently answer about the wrong tree.
        if cand and ((cand / ".shared").is_dir() or (cand / "DEV-DOCS-INDEX.md").exists()):
            return cand
    return _HISTORICAL_WS


def resolve_memory():
    env = os.environ.get("CLAUDE_MEMORY_DIR")
    if env and pathlib.Path(env).exists():
        return pathlib.Path(env)
    return _HISTORICAL_MEM


WS = resolve_workspace()
MEM = resolve_memory()
SCRIPTS = WS / ".shared" / "scripts"


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print("workspace paths")
    print("  WS      = %s   (exists=%s)" % (WS, WS.exists()))
    print("  MEM     = %s   (exists=%s)" % (MEM, MEM.exists()))
    print("  SCRIPTS = %s   (exists=%s)" % (SCRIPTS, SCRIPTS.exists()))
    print()
    print("  env CLAUDE_WORKSPACE  = %s" % os.environ.get("CLAUDE_WORKSPACE", "(unset)"))
    print("  env CLAUDE_MEMORY_DIR = %s" % os.environ.get("CLAUDE_MEMORY_DIR", "(unset)"))
    ok = WS.exists() and (WS / ".shared").is_dir()
    print("\n  [%s] resolved to a real workspace" % ("OK " if ok else "FAIL"))
    sys.exit(0 if ok else 1)
