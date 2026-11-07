#!/usr/bin/env python3
"""Where the workspace is, resolved instead of hardcoded - so these tools can SHIP.

<!-- subject: infrastructure -->

<!-- route-tags: workspace path portable shippable root memory scripts resolve hardcoded
     claude home secrets worktrees main checkout project slug cloud linux second machine -->

⛔ THE REASON PHASE 1-3 SHIPPED NOWHERE. Every gate was wired with `WS =
Path(r"C:\\Users\\<the-owner>\\<the-workspace>")` - one machine's absolute path, spelled out.
That is not a configuration detail, it is the thing that made the whole decision tree
workspace-local by construction: a script carrying one machine's absolute path cannot run in a
skill, a fresh clone, a worktree or a cloud session. The answer was zero, and this was why.

⭐ FOUR SOURCES, MOST SPECIFIC FIRST, and each one is a fact rather than a guess:

  1. `$CLAUDE_WORKSPACE` - an explicit override always wins
  2. `git rev-parse --show-toplevel` - the repo the CALLER is actually in. This is what makes
     a worktree work: knowledge_gate once read DEV-DOCS-INDEX from the main checkout while
     committing from a worktree, and printed an instruction impossible to satisfy
  3. walk up from THIS file - these scripts live at `<workspace>/.shared/scripts/`, so the
     grandparent is the workspace whenever the file is where it belongs
  4. the historical default, `<home>/<your-workspace>` - so nothing breaks on the one machine

The memory dir gets the same treatment via `$CLAUDE_MEMORY_DIR`.

Import and use; never re-derive:

    from workspace_paths import WS, MEM, SCRIPTS

So no name below spells out a home folder. Each reads its environment variable first:

  name            variable                  default
  WS              CLAUDE_WORKSPACE          the tree the caller is in (the four sources above)
  MAIN_CHECKOUT   (follows WS)              the main checkout of WS's repo: the parent of
                                            `git rev-parse --git-common-dir`; WS itself when WS
                                            is not a worktree
  CLAUDE_HOME     CLAUDE_HOME, then         Path.home() / ".claude"
                  CLAUDE_CONFIG_DIR
  MEM             CLAUDE_MEMORY_DIR         CLAUDE_HOME / projects / <project_slug(MAIN_CHECKOUT)>
                                            / memory; when CLAUDE_WORKSPACE chose WS, the main
                                            checkout of the workspace holding this file instead
  SECRETS_DIR     CLAUDE_SECRETS_DIR        MAIN_CHECKOUT / .shared / secrets
  WORKTREES_ROOT  CLAUDE_WORKTREES_ROOT     MAIN_CHECKOUT / .worktrees
  SCRIPTS         (follows WS)              WS / .shared / scripts

    from workspace_paths import MAIN_CHECKOUT, CLAUDE_HOME, SECRETS_DIR, WORKTREES_ROOT

WS OR MAIN_CHECKOUT? They differ only when the caller runs inside a worktree.
  - WS is the tree you are working in: the files a commit from here carries, the index a
    pre-commit guard reads, the docs and indexes you regenerate.
  - MAIN_CHECKOUT is what exists once per machine and only in the main checkout: gitignored
    folders such as `<your-secrets-folder>`, `.git/hooks`, the sibling product repos, the worktrees
    folder, and the name of the memory folder.

WHY MEM FOLLOWS THE MAIN CHECKOUT. Claude Code keeps a project's memory under
`<CLAUDE_HOME>/projects/<slug of the project path>/memory`, and a session started inside a
user name for <name>) as its memory folder. So the slug is taken from MAIN_CHECKOUT, never
from `--show-toplevel`.

That variable points the tools at another tree, most often a test's sandbox clone
(test_knowledge_process.py sets it and nothing else, then runs this checkout's tools with their
working directory in the clone). MAIN_CHECKOUT, SECRETS_DIR and WORKTREES_ROOT still follow WS.
A caller that wants MEM moved sets CLAUDE_MEMORY_DIR, the explicit setting for it.

WHY SECRETS_DIR FOLLOWS THE MAIN CHECKOUT. SECRETS_DIR is a PATH ONLY: nothing in this module
opens, lists or reads it.

OVERRIDES. A variable that is set and not empty wins, even when its folder does not exist yet:
falling back to the real folder would point a test that set it at the human's real files. The two
older variables, CLAUDE_WORKSPACE and CLAUDE_MEMORY_DIR, count only when their folder exists,
as they always have. `~` in a value is expanded.

CLI:  workspace_paths.py          every name, and whether WS is a real workspace (exit 0 or 1)
      workspace_paths.py --json   every name as a JSON object (exit 0)
"""
import os
import pathlib
import subprocess
try:
    from no_window import flags
except ImportError:  # a partial delivery must not take the tool down
    def flags(extra=0):
        return extra

# Source 4.
_HISTORICAL_WS = pathlib.Path.home() / "<your-workspace>"

ENV_WORKSPACE = "CLAUDE_WORKSPACE"
ENV_MEMORY = "CLAUDE_MEMORY_DIR"
ENV_HOME = "CLAUDE_HOME"
ENV_CONFIG = "CLAUDE_CONFIG_DIR"        # Claude Code's own variable for the same folder
ENV_SECRETS = "CLAUDE_SECRETS_DIR"
ENV_WORKTREES = "CLAUDE_WORKTREES_ROOT"

SLUG_MAX = 200                          # Claude Code's limit before it cuts and adds a hash

# toplevel -> git common dir, from the single `git rev-parse` call _from_git() makes, so the
# main checkout costs no second git process in the usual case.
_COMMON = {}


def _key(p):
    return os.path.normcase(os.path.normpath(str(p)))


def _git_lines(args, cwd=None):
    """stdout lines of `git <args>` run in `cwd`; None when git fails, is missing or times out."""
    try:
        p = subprocess.run(["git"] + list(args), cwd=None if cwd is None else str(cwd),
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=10, creationflags=flags())
    except Exception:
        return None
    if p.returncode != 0:
        return None
    return [line.strip() for line in p.stdout.splitlines()]


def _from_git(cwd=None):
    """The toplevel of the repo `cwd` (default: the process's) is in, or None."""
    lines = _git_lines(["rev-parse", "--show-toplevel", "--git-common-dir"], cwd)
    if not lines or not lines[0]:
        return None
    top = pathlib.Path(lines[0])
    if len(lines) > 1 and lines[1]:
        common = pathlib.Path(lines[1])
        if not common.is_absolute():    # git prints it relative to the directory it ran in
            common = pathlib.Path(cwd if cwd is not None else os.getcwd()) / common
        _COMMON[_key(top)] = common
    return top


def _from_file():
    # <workspace>/.shared/scripts/workspace_paths.py -> up three
    here = pathlib.Path(__file__).resolve()
    if here.parent.name == "scripts" and here.parent.parent.name == ".shared":
        return here.parent.parent.parent
    return None


def _env_path(name):
    """The variable's value as a Path when it is set and not empty, else None."""
    value = os.environ.get(name, "").strip()
    return pathlib.Path(os.path.expanduser(value)) if value else None


def _ws_override():
    """`$CLAUDE_WORKSPACE` as a Path when it names a folder that exists, else None."""
    env = os.environ.get(ENV_WORKSPACE)
    return pathlib.Path(env) if env and pathlib.Path(env).exists() else None


def resolve_workspace(cwd=None):
    override = _ws_override()
    if override is not None:
        return override
    for cand in (_from_git(cwd), _from_file()):
        # A repo root only counts as THE workspace if it looks like it - otherwise a script
        # run from inside a nested product repo would claim that repo as the workspace and
        # then look for DEV-DOCS-INDEX.md in it. Wrong root is worse than no root: it makes
        # every subsequent read confidently answer about the wrong tree.
        if cand and ((cand / ".shared").is_dir() or (cand / "DEV-DOCS-INDEX.md").exists()):
            return cand
    return _HISTORICAL_WS


def _main_from_porcelain(path):
    """The main working tree from `git worktree list --porcelain`, for a repo whose git dir is
    not a `.git` folder inside its working tree. None for a bare repo or on failure."""
    lines = _git_lines(["worktree", "list", "--porcelain"], cwd=path)
    if not lines or not lines[0].startswith("worktree "):
        return None
    if len(lines) > 1 and lines[1] == "bare":
        return None
    return pathlib.Path(lines[0][len("worktree "):])


def _read_first_line(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.readline().strip()
    except OSError:
        return None


def _common_from_files(path):
    """
    Walks up from `path` to the first `.git`, as git does. A `.git` FOLDER is the common dir.
    A `.git` FILE holds `gitdir: <git dir>` (absolute, or relative to the folder holding the
    file); a worktree's git dir holds a `commondir` file naming the common dir (relative to the
    git dir, usually `../..`). A git dir with no `commondir` (a submodule's) is its own common
    dir. None when no `.git` is found or a file cannot be read.
    """
    here = pathlib.Path(os.path.abspath(str(path)))
    for folder in [here] + list(here.parents):
        dotgit = folder / ".git"
        if dotgit.is_dir():
            return dotgit
        if not dotgit.is_file():
            continue
        line = _read_first_line(dotgit) or ""
        if not line.startswith("gitdir:"):
            return None
        gitdir = pathlib.Path(line[len("gitdir:"):].strip())
        if not gitdir.is_absolute():
            gitdir = folder / gitdir
        if not gitdir.is_dir():
            return None
        rel = _read_first_line(gitdir / "commondir")
        if not rel:
            return gitdir
        common = pathlib.Path(rel)
        return common if common.is_absolute() else gitdir / common
    return None


def main_checkout(path=None):
    """The main checkout of the repo that holds `path` (default: the workspace WS resolves to).

    The parent of `git rev-parse --git-common-dir`, so a worktree answers with the checkout it
    was made from. When git cannot answer (missing, timed out, refused), the same common dir is
    read from the `.git` and `commondir` files instead. `path` itself when it is not in a git
    repo or is not a directory.
    """
    path = pathlib.Path(path) if path is not None else resolve_workspace()
    if (path / ".git").is_dir():        # a main working tree: no git process needed
        return path
    common = _COMMON.get(_key(path))
    if common is None and path.is_dir():
        lines = _git_lines(["rev-parse", "--git-common-dir"], cwd=path)
        if lines and lines[0]:
            common = pathlib.Path(lines[0])
            if not common.is_absolute():
                common = path / common
            _COMMON[_key(path)] = common    # a second ask for this path starts no git process
        else:
            # Before this, a worktree counted as its own main checkout here, and MEM and
            # WORKTREES_ROOT followed it into the worktree.
            common = _common_from_files(path)
    if common is None:
        return path
    common = pathlib.Path(os.path.normpath(str(common)))
    if common.name.lower() == ".git":
        return common.parent
    return _main_from_porcelain(path) or path


def checkout_of(path):
    """The main checkout (a Path) of the workspace that holds `path`, found with NO git process.

    For a hook: pass `__file__`. The work is done by checkout_lookup.checkout_of, which imports
    only `os`, so a hook can call that directly and skip this module's import cost (a git
    process and pathlib, subprocess and ctypes). A path in no workspace gives
    `<home>/<your-workspace>`.
    """
    from checkout_lookup import checkout_of as _lookup
    return pathlib.Path(_lookup(path))


def resolve_main_checkout(cwd=None):
    """main_checkout() of the workspace that a caller running in `cwd` resolves to."""
    return main_checkout(resolve_workspace(cwd))


def resolve_claude_home():
    """`$CLAUDE_HOME`, else `$CLAUDE_CONFIG_DIR`, else `<home>/.claude`."""
    return (_env_path(ENV_HOME) or _env_path(ENV_CONFIG)
            or pathlib.Path.home() / ".claude")


def _java_hash(text):
    """JavaScript's `h = (h << 5) - h + charCodeAt(i) | 0` over the UTF-16 code units."""
    data = text.encode("utf-16-le", "surrogatepass")
    h = 0
    for i in range(0, len(data), 2):
        h = (h * 31 + (data[i] | (data[i + 1] << 8))) & 0xFFFFFFFF
    return h - 0x100000000 if h >= 0x80000000 else h


def _base36(n):
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    while True:
        n, r = divmod(n, 36)
        out = digits[r] + out
        if not n:
            return out


def project_slug(path):
    """The folder name Claude Code gives the project at `path` under `<CLAUDE_HOME>/projects`.

    Pass the path as Claude Code sees it (a Windows path on Windows, a POSIX path on Linux);
    it is used exactly as given. See THE SLUG RULE in the module docstring.
    """
    text = os.fspath(path)
    slug = "".join(ch if ("a" <= ch <= "z" or "A" <= ch <= "Z" or "0" <= ch <= "9")
                   else ("--" if ord(ch) > 0xFFFF else "-") for ch in text)
    if len(slug) <= SLUG_MAX:
        return slug
    return "%s-%s" % (slug[:SLUG_MAX], _base36(abs(_java_hash(text))))


def memory_dir_for(project, claude_home=None):
    """`<claude_home>/projects/<project_slug(project)>/memory`; claude_home defaults to
    resolve_claude_home()."""
    home = pathlib.Path(claude_home) if claude_home is not None else resolve_claude_home()
    return home / "projects" / project_slug(project) / "memory"


# Kept so any caller that named it still works; resolve_memory() no longer reads it.
_HISTORICAL_MEM = memory_dir_for(_HISTORICAL_WS, pathlib.Path.home() / ".claude")


def _memory_project(main):
    """The checkout whose slug names MEM: `main`, the main checkout of WS, unless
    `$CLAUDE_WORKSPACE` chose WS. See CLAUDE_WORKSPACE DOES NOT MOVE MEM in the docstring."""
    if _ws_override() is None:
        return main
    return main_checkout(_from_file() or _HISTORICAL_WS)


def _memory(main, home):
    env = os.environ.get(ENV_MEMORY)
    if env and pathlib.Path(env).exists():
        return pathlib.Path(env)
    return memory_dir_for(_memory_project(main), home)


def resolve_memory(cwd=None):
    """`$CLAUDE_MEMORY_DIR` when that folder exists, else the main checkout's memory folder
    (never moved by `$CLAUDE_WORKSPACE` alone)."""
    return _memory(resolve_main_checkout(cwd), resolve_claude_home())


def _secrets(main):
    return _env_path(ENV_SECRETS) or main / ".shared" / "secrets"


def resolve_secrets_dir(cwd=None):
    """Where the secrets folder is. A PATH ONLY: nothing here opens, lists or reads it."""
    return _secrets(resolve_main_checkout(cwd))


def _worktrees(main):
    return _env_path(ENV_WORKTREES) or main / ".worktrees"


def resolve_worktrees_root(cwd=None):
    """Where new worktrees go: `$CLAUDE_WORKTREES_ROOT`, else `<main checkout>/.worktrees`."""
    return _worktrees(resolve_main_checkout(cwd))


WS = resolve_workspace()
MAIN_CHECKOUT = main_checkout(WS)
CLAUDE_HOME = resolve_claude_home()
MEM = _memory(MAIN_CHECKOUT, CLAUDE_HOME)
SCRIPTS = WS / ".shared" / "scripts"
SECRETS_DIR = _secrets(MAIN_CHECKOUT)
WORKTREES_ROOT = _worktrees(MAIN_CHECKOUT)

NAMES = ("WS", "MAIN_CHECKOUT", "CLAUDE_HOME", "MEM", "SCRIPTS", "SECRETS_DIR", "WORKTREES_ROOT")
ENV_NAMES = (ENV_WORKSPACE, ENV_MEMORY, ENV_HOME, ENV_CONFIG, ENV_SECRETS, ENV_WORKTREES)


if __name__ == "__main__":
    import json
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if sys.argv[1:] == ["--json"]:
        print(json.dumps({n: str(globals()[n]) for n in NAMES}, indent=1))
        sys.exit(0)
    print("workspace paths")
    for n in NAMES:
        p = globals()[n]
        # SECRETS_DIR: whether the path exists says nothing about what is in it
        print("  %-14s = %s   (exists=%s)" % (n, p, p.exists()))
    print()
    for v in ENV_NAMES:
        print("  env %-21s = %s" % (v, os.environ.get(v, "(unset)")))
    ok = WS.exists() and (WS / ".shared").is_dir()
    print("\n  [%s] resolved to a real workspace" % ("OK " if ok else "FAIL"))
    sys.exit(0 if ok else 1)
