#!/usr/bin/env python3
"""Keep a test from writing the real workspace's git config.

<!-- subject: infrastructure -->
<!-- route-tags: git config guard test isolation GIT_DIR environment leak user.name test
     example.invalid hooks turned off Permission denied temp repo sandbox -->

WHAT THIS MODULE DOES, once `install()` has run (importing the module runs it):

  1. Removes every variable that redirects git (GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE,
     GIT_COMMON_DIR, GIT_OBJECT_DIRECTORY, GIT_CONFIG*, ...) from os.environ, so a temp repo is
     found by its cwd. Every child inherits the clean environment.
  2. Wraps subprocess.Popen and os.system. A `git config` that writes (user.name and core.hooksPath
     alike), or `git init`, is refused with GitGuardError BEFORE it starts when it would write:
     - the protected workspace's config (the repository this file lives in, or any of its
       worktrees), reached by the cwd, `-C`, `--git-dir`, a GIT_DIR in env=, `--file` or GIT_CONFIG;
     - the real ~/.gitconfig or the system config (`--global`, `--system`), unless env= points git
       at another home or file. A global core.hooksPath would turn hooks off in every repo.
     Shell strings count: every command in `cd x && git config a b`, and `bash -c "..."`.
     Reads, commits and everything else pass through untouched.

USE: first import of any test that runs `git init` or `git config`:

`python git_guard.py` runs the selftest (a real failing case and a real passing case).
"""
import os
import pathlib
import re
import shlex
import subprocess
import sys
import tempfile

try:
    from no_window import flags
except ImportError:  # a partial delivery must not take the tool down
    def flags(extra=0):
        return extra

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

#: Variables that make git ignore the cwd or read config from somewhere else.
LEAK_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
             "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE",
             "GIT_PREFIX", "GIT_CEILING_DIRECTORIES", "GIT_CONFIG", "GIT_CONFIG_GLOBAL",
             "GIT_CONFIG_SYSTEM", "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS")
_LEAK_PREFIXES = ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")

#: `git config` flags that only read. --name-only, --show-origin and --includes are not here: they
#: modify a read and say nothing about whether the command is one.
_READ_FLAGS = {"--list", "-l"}
_READ_ACTIONS = {"get", "list"}
_WRITE_ACTIONS = {"set", "unset", "edit", "rename-section", "remove-section"}
_WRITE_FLAGS = {"--unset", "--unset-all", "--add", "--replace-all", "--edit", "-e",
                "--rename-section", "--remove-section"}
#: `git config` options whose next word is their value, not the key.
_CONFIG_VALUE_OPTS = {"-f", "--file", "--blob", "-t", "--type", "--default", "--comment",
                      "--value", "--fixed-value-pattern"}
#: `git` options before the subcommand whose next word is their value.
_GIT_VALUE_OPTS = {"--git-dir", "--work-tree", "--namespace", "--config-env", "--super-prefix"}
_SHELLS = {"bash", "sh", "dash", "zsh", "cmd", "powershell", "pwsh"}

#: Env var naming extra protected repos (os.pathsep list of any path inside them); tests use it.
PROTECT_ENV = "GIT_GUARD_PROTECT"


class GitGuardError(RuntimeError):
    pass


_REAL_POPEN = subprocess.Popen
_REAL_SYSTEM = os.system
_installed = False


def _clean(env):
    out = dict(env)
    for k in list(out):
        if k in LEAK_VARS or k.startswith(_LEAK_PREFIXES):
            del out[k]
    return out


def _common_dir(path, env=None):
    """Resolved git common dir of the repo containing `path`, or None. Runs with a clean env."""
    p = pathlib.Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    if p.is_file():
        p = p.parent
    r = _REAL_POPEN(["git", "-C", str(p), "rev-parse", "--git-common-dir"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                    env=_clean(env if env is not None else os.environ), creationflags=flags())
    out = r.communicate(timeout=60)[0].strip()
    if r.returncode != 0 or not out:
        return None
    cd = pathlib.Path(out)
    if not cd.is_absolute():
        cd = p / cd
    return os.path.normcase(str(cd.resolve()))


def protected_dirs():
    """Common git dirs a test may never configure: this file's repo plus $GIT_GUARD_PROTECT."""
    global _HERE_CACHE
    out = set()
    if _HERE_CACHE is None:
        _HERE_CACHE = _common_dir(pathlib.Path(__file__).resolve().parent, env=_ORIGINAL_ENV) or ""
    if _HERE_CACHE:
        out.add(_HERE_CACHE)
    for extra in os.environ.get(PROTECT_ENV, "").split(os.pathsep):
        if extra.strip():
            cd = _common_dir(extra.strip())
            if cd:
                out.add(cd)
    return out


_HERE_CACHE = None


_ORIGINAL_ENV = dict(os.environ)


def _shell_segments(text):
    """The simple commands in a shell string, each a list of words. Backslashes stay, so a Windows
    path survives; a string the lexer cannot read is split on spaces rather than ignored."""
    try:
        lex = shlex.shlex(text, posix=False, punctuation_chars=";&|()\n")
        lex.whitespace = " \t\r"
        lex.whitespace_split = True
        lex.commenters = ""
        words = list(lex)
    except ValueError:
        words = text.split()
    segments, cur = [], []
    for w in words:
        if w and all(c in ";&|()\n" for c in w):
            segments.append(cur)
            cur = []
        else:
            cur.append(w[1:-1] if len(w) > 1 and w[0] == w[-1] and w[0] in "\"'" else w)
    segments.append(cur)
    return [seg for seg in segments if seg]


def _git_commands(args, cwd, env):
    """Yield (git argv, cwd, env) for every git command this Popen or os.system would start."""
    if isinstance(args, (str, bytes, os.PathLike)):
        text = os.fsdecode(args)
        where, seg_env = cwd, env
        for seg in _shell_segments(text):
            seg_env = dict(seg_env)  # `GIT_DIR=x git config a b` holds for that command only
            while seg and re.fullmatch(r"\w+=.*", seg[0]):
                k, v = seg.pop(0).split("=", 1)
                seg_env[k] = v.strip("\"'")
            if seg[:1] == ["cd"]:  # `cd x && git config a b` acts in x
                target = [w for w in seg[1:] if w.lower() != "/d"]
                if target:
                    where = pathlib.Path(where or pathlib.Path.cwd()) / target[0]
                continue
            yield from _git_commands(seg, where, seg_env)
        return
    args = [os.fsdecode(a) for a in args]
    if not args:
        return
    prog = os.path.basename(args[0].replace("\\", "/")).lower()
    prog = prog[:-4] if prog.endswith(".exe") else prog
    if prog in _SHELLS:
        for j, a in enumerate(args[1:], 1):
            if a.lower() in ("-c", "/c", "-command") and j + 1 < len(args):
                yield from _git_commands(" ".join(args[j + 1:]), cwd, env)
                return
    elif prog == "git":
        yield args, cwd, env


def _parse(argv):
    """(subcommand, rest, -C dirs, --git-dir value or None) for a git argv, or None."""
    i, dirs, git_dir = 1, [], None
    while i < len(argv):
        a = argv[i]
        if a == "-C" and i + 1 < len(argv):
            dirs.append(argv[i + 1]); i += 2
        elif a == "-c" and i + 1 < len(argv):
            i += 2
        elif a in _GIT_VALUE_OPTS and i + 1 < len(argv):
            git_dir = argv[i + 1] if a == "--git-dir" else git_dir
            i += 2
        elif a.startswith("--git-dir="):
            git_dir = a.split("=", 1)[1]; i += 1
        elif a.startswith("-"):
            i += 1
        else:
            return a, argv[i + 1:], dirs, git_dir
    return None


def _writes_config(sub, rest):
    if sub == "init":
        return True
    if sub != "config":
        return False
    positional, j = [], 0
    while j < len(rest):
        if rest[j] in _CONFIG_VALUE_OPTS:
            j += 2
            continue
        if not rest[j].startswith("-"):
            positional.append(rest[j])
        j += 1
    if any(a in _WRITE_FLAGS for a in rest):
        return True
    if any(a in _READ_FLAGS or a.startswith("--get") for a in rest):
        return False
    if positional and positional[0] in _READ_ACTIONS:
        return False
    # `git config <key>` alone reads; a key and a value, or a named write action, write.
    return len(positional) >= 2 or bool(positional and positional[0] in _WRITE_ACTIONS)


def _config_file(rest):
    for j, a in enumerate(rest):
        if a.startswith("--file="):
            return a.split("=", 1)[1]
        if a in ("--file", "-f") and j + 1 < len(rest):
            return rest[j + 1]
    return None


def _inside(path, base):
    return os.path.normcase(str(path.resolve())).startswith(base + os.sep)


def _real_homes():
    homes = set()
    for k in ("HOME", "USERPROFILE"):
        if _ORIGINAL_ENV.get(k):
            homes.add(os.path.normcase(str(pathlib.Path(_ORIGINAL_ENV[k]).resolve())))
    return homes


def _refuse(sub, rest, why):
    raise GitGuardError("refused: `git %s %s` would write %s. "
                        "A test must work in a temp repo; see .shared/scripts/git_guard.py"
                        % (sub, " ".join(rest), why))


def _check_scope(sub, rest, env):
    """--global and --system: the real files are refused, a redirected one is the test's own."""
    if "--system" in rest and not env.get("GIT_CONFIG_SYSTEM"):
        _refuse(sub, rest, "the machine's system git config")
    if "--global" in rest and not env.get("GIT_CONFIG_GLOBAL"):
        home = env.get("HOME") or env.get("USERPROFILE") or ""
        if not home or os.path.normcase(str(pathlib.Path(home).resolve())) in _real_homes():
            _refuse(sub, rest, "the real global git config (~/.gitconfig)")


def _check_one(argv, cwd, env):
    parsed = _parse(argv)
    if not parsed:
        return
    sub, rest, dirs, git_dir = parsed
    if not _writes_config(sub, rest):
        return
    if sub == "config" and ("--global" in rest or "--system" in rest):
        _check_scope(sub, rest, env)
        return
    base = pathlib.Path(cwd) if cwd else pathlib.Path.cwd()
    for d in dirs:
        base = base / d  # an absolute d replaces base
    prot = protected_dirs()
    if not prot:
        return
    # Where git acts. A GIT_DIR or --git-dir decides alone: git then ignores the cwd. It may be a
    # linked worktree's .git/worktrees/x, whose config writes still go to the shared common dir.
    named = git_dir or env.get("GIT_DIR")
    file_ = _config_file(rest) if sub == "config" else None
    file_ = file_ or (env.get("GIT_CONFIG") if sub == "config" else None)
    if file_:
        # Only a file inside the git dir is refused: `-f .gitmodules` is a tracked file's edit.
        if any(_inside(base / file_, p) for p in prot):
            _refuse(sub, rest, "a config file inside the protected git dir (%s)" % file_)
        return
    if named:
        label, cd = "git dir " + named, _common_dir(base / named)
    elif sub == "init":
        paths = [a for j, a in enumerate(rest) if not a.startswith("-")
                 and not (j and rest[j - 1] in ("-b", "--initial-branch", "--template",
                                                "--separate-git-dir", "--object-format", "--shared"))]
        where = base / paths[-1] if paths else base
        # A new repo in a plain subfolder is its own repo. Refuse a re-init of the repo itself.
        if not (where / ".git").exists() and not any(_inside(where, p) or
                                                     os.path.normcase(str(where.resolve())) == p
                                                     for p in prot):
            return
        label, cd = "git init in " + str(where), _common_dir(where)
    else:
        label, cd = "cwd " + str(base), _common_dir(base)
    if cd in prot:
        _refuse(sub, rest, "the protected workspace config (%s -> %s)" % (label, cd))


def check_command(args, cwd=None, env=None):
    """Raise GitGuardError if this command would write a config this module protects."""
    env = env if env is not None else os.environ
    for argv, where, seg_env in _git_commands(args, cwd, env):
        _check_one(argv, where, seg_env)


class _GuardedPopen(_REAL_POPEN):
    def __init__(self, args, *a, **kw):
        cwd = kw.get("cwd")
        env = kw.get("env")
        check_command(args, cwd=cwd, env=env if env is not None else os.environ)
        super().__init__(args, *a, **kw)


def _guarded_system(command):
    check_command(command)
    return _REAL_SYSTEM(command)


def scrub_environ():
    # GIT_CEILING_DIRECTORIES stays: it can only stop git finding a repo, never send it to one, so
    # a test or harness that set it is left with the limit it asked for.
    for k in list(os.environ):
        if (k in LEAK_VARS or k.startswith(_LEAK_PREFIXES)) and k != "GIT_CEILING_DIRECTORIES":
            del os.environ[k]


def install():
    global _installed
    scrub_environ()
    if not _installed:
        subprocess.Popen = _GuardedPopen
        os.system = _guarded_system
        _installed = True


# ---- selftest ---------------------------------------------------------------------------

def _run(args, cwd=None, env=None):
    return _REAL_POPEN(args, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       text=True, creationflags=flags())


def _selftest():
    import shutil
    fails = 0

    def check(label, ok, detail=""):
        nonlocal fails
        fails += (not ok)
        print("  [%s] %s%s" % ("OK" if ok else "FAIL", label, ("  -> " + str(detail)) if detail and not ok else ""))

    root = pathlib.Path(tempfile.mkdtemp(prefix="gitguard-")).resolve()
    try:
        victim, temp = root / "workspace", root / "tempdir"
        for d in (victim, temp):
            d.mkdir()
        # A stand-in for the real workspace: the real .git/config is never an experiment subject.
        _run(["git", "init", "-q", "-b", "main", str(victim)],
             env=_clean(os.environ)).communicate()

        def victim_cfg(key):
            p = _run(["git", "-C", str(victim), "config", "--local", "--get", key], env=_clean(os.environ))
            return p.communicate()[0].strip()

        print("git_guard selftest")
        # 1. The default protection is the repo this file lives in.
        prot = protected_dirs()
        mine = _common_dir(pathlib.Path(__file__).resolve().parent, env=_ORIGINAL_ENV)
        check("default protected dir is this repo's common dir", mine in prot, (mine, prot))
        # 2. Offender, before the guard: a leaked GIT_DIR sends a temp-cwd `git config` to the victim.
        leaked = dict(_clean(os.environ), GIT_DIR=str(victim / ".git"))
        _run(["git", "init", "-q", "-b", "main", "."], cwd=str(temp), env=_clean(os.environ)).communicate()
        _run(["git", "config", "user.name", "leak"], cwd=str(temp), env=leaked).communicate()
        check("UNGUARDED case reproduces: GIT_DIR leak writes the victim from a temp cwd",
              victim_cfg("user.name") == "leak", victim_cfg("user.name"))
        _run(["git", "config", "--local", "--unset", "user.name"], cwd=str(victim), env=_clean(os.environ)).communicate()
        # 3. Guarded refusal: explicit env naming the victim, and a cwd inside the victim.
        os.environ[PROTECT_ENV] = str(victim)
        for label, kw in (("GIT_DIR in env= naming the protected repo", dict(cwd=str(temp), env=leaked)),
                          ("cwd inside the protected repo", dict(cwd=str(victim), env=None))):
            try:
                _GuardedPopen(["git", "config", "user.name", "guarded"], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, creationflags=flags(), **kw).communicate()
                check("REFUSED: " + label, False, "no error raised")
            except GitGuardError:
                check("REFUSED: " + label, True)
        check("  and the protected config was not written", victim_cfg("user.name") == "", victim_cfg("user.name"))
        try:
            _GuardedPopen(["git", "init", "-q", "."], cwd=str(victim), stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, creationflags=flags()).communicate()
            check("REFUSED: git init inside the protected repo", False, "no error raised")
        except GitGuardError:
            check("REFUSED: git init inside the protected repo", True)
        # 4. Guarded passing cases: temp repo write works; reads of the protected repo work.
        p = _GuardedPopen(["git", "config", "user.name", "ok"], cwd=str(temp), env=_clean(os.environ),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags())
        p.communicate()
        t = _run(["git", "-C", str(temp), "config", "--local", "--get", "user.name"], env=_clean(os.environ))
        check("PASSES: config write in a real temp repo", t.communicate()[0].strip() == "ok" and p.returncode == 0)
        p = _GuardedPopen(["git", "config", "--get", "core.bare"], cwd=str(victim), stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, creationflags=flags())
        p.communicate()
        check("PASSES: read of the protected repo's config", p.returncode == 0)
        # 4b. Every other way to name the protected repo, each started for real if it gets through.
        v, t = victim.as_posix(), temp.as_posix()
        _run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q",
              "--allow-empty", "-m", "x"], cwd=str(victim), env=_clean(os.environ)).communicate()
        linked = root / "linked"
        _run(["git", "worktree", "add", "-q", "--detach", str(linked)], cwd=str(victim),
             env=_clean(os.environ)).communicate()
        linked_gitdir = next((victim / ".git" / "worktrees").iterdir())
        hooks_before = victim_cfg("core.hookspath")
        clean = _clean(os.environ)
        refused = [
            ("-C", ["git", "-C", v, "config", "a.b", "1"], {}),
            ("-c then -C", ["git", "-c", "x.y=1", "-C", v, "config", "a.b", "1"], {}),
            ("--git-dir=", ["git", "--git-dir=%s/.git" % v, "config", "a.b", "1"], dict(cwd=t)),
            ("--git-dir value", ["git", "--git-dir", v + "/.git", "config", "a.b", "1"], dict(cwd=t)),
            ("core.hooksPath set", ["git", "-C", v, "config", "core.hooksPath", t], {}),
            ("core.hooksPath unset", ["git", "-C", v, "config", "--unset", "core.hooksPath"], {}),
            ("a linked worktree's cwd", ["git", "config", "a.b", "1"], dict(cwd=str(linked))),
            ("GIT_DIR of a linked worktree in env=", ["git", "config", "a.b", "1"],
             dict(cwd=t, env=dict(clean, GIT_DIR=str(linked_gitdir)))),
            ("--file, relative to the cwd", ["git", "config", "--file", ".git/config", "a.b", "1"],
             dict(cwd=v)),
            ("GIT_CONFIG in env=", ["git", "config", "a.b", "1"],
             dict(cwd=t, env=dict(clean, GIT_CONFIG=v + "/.git/config"))),
            ("shell string", 'git -C "%s" config a.b 1' % v, dict(shell=True)),
            ("shell cd &&", 'cd "%s" && git config a.b 1' % v, dict(shell=True)),
            ("--name-only does not make a write a read",
             ["git", "-C", v, "config", "--name-only", "a.b", "1"], {}),
        ]
        for label, cmd, kw in refused:
            try:
                _GuardedPopen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              creationflags=flags(), **kw).communicate()
                check("REFUSED: " + label, False, "no error raised")
            except GitGuardError:
                check("REFUSED: " + label, True)
        check("  and none of them wrote the protected config",
              victim_cfg("a.b") == "" and victim_cfg("core.hookspath") == hooks_before,
              (victim_cfg("a.b"), victim_cfg("core.hookspath")))
        # Refused by check_command alone: these must never start, because a miss would write the
        # real ~/.gitconfig or need a shell this machine may not have.
        real_env = dict(clean, HOME=os.path.expanduser("~"))
        for label, cmd, kw in (
                ("--global at the real home", ["git", "config", "--global", "core.hooksPath", t],
                 dict(cwd=t, env=real_env)),
                ("--global --unset at the real home", ["git", "config", "--global", "--unset", "a.b"],
                 dict(cwd=t, env=real_env)),
                ("--system", ["git", "config", "--system", "a.b", "1"], dict(cwd=t, env=clean)),
                ("bash -c", ["bash", "-c", 'cd "%s" && git config a.b 1' % v], {}),
                ("shell env prefix", 'GIT_DIR="%s/.git" git config a.b 1' % v, dict(cwd=t)),
                ("os.system text", 'git -C "%s" config a.b 1' % v, {})):
            try:
                check_command(cmd, **kw)
                check("REFUSED: " + label, False, "no error raised")
            except GitGuardError:
                check("REFUSED: " + label, True)
        # Passing cases: the same shapes aimed somewhere harmless must run and must write.
        (victim / "sub").mkdir()
        home = root / "home"
        home.mkdir()
        passing = [
            ("GIT_DIR in env= decides, whatever the cwd", ["git", "config", "p.a", "1"],
             dict(cwd=v, env=dict(clean, GIT_DIR=t + "/.git"))),
            ("--global with a temp HOME, from inside the protected repo",
             ["git", "config", "--global", "p.b", "1"], dict(cwd=v, env=dict(clean, HOME=str(home)))),
            ("--global with GIT_CONFIG_GLOBAL",
             ["git", "config", "--global", "p.c", "1"],
             dict(cwd=t, env=dict(clean, GIT_CONFIG_GLOBAL=str(home / "other")))),
            ("--file on a tracked-style file in the repo", ["git", "config", "-f", "side.cfg", "p.d", "1"],
             dict(cwd=v)),
            ("git init in a plain subfolder", ["git", "init", "-q", "."], dict(cwd=str(victim / "sub"))),
            ("--type is a read", ["git", "-C", v, "config", "--type", "bool", "core.bare"], {}),
            ("-f read of the repo's config", ["git", "config", "-f", ".git/config", "core.bare"], dict(cwd=v)),
            ("list with --show-origin", ["git", "-C", v, "config", "--list", "--show-origin"], {}),
            ("shell string aimed at the temp repo", 'git -C "%s" config p.e 1' % t, dict(shell=True)),
            ("a commit in the protected repo is not this guard's business",
             ["git", "-C", v, "status", "--short"], {}),
        ]
        for label, cmd, kw in passing:
            try:
                p = _GuardedPopen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  creationflags=flags(), **kw)
                err = p.communicate()[1]
                check("PASSES: " + label, p.returncode == 0, err)
            except GitGuardError as e:
                check("PASSES: " + label, False, e)
        t_cfg = _run(["git", "-C", t, "config", "--local", "--get", "p.a"], env=clean).communicate()[0].strip()
        check("  and the GIT_DIR write landed in the repo it named", t_cfg == "1" and victim_cfg("p.a") == "", t_cfg)
        check("  and the --global write landed in the temp home", (home / ".gitconfig").exists())
        # 5. install() scrubs the leak: the same temp-cwd write now stays in the temp repo.
        os.environ["GIT_DIR"] = str(victim / ".git")
        scrub_environ()
        check("scrub_environ removes GIT_DIR", "GIT_DIR" not in os.environ)
        os.environ["GIT_CEILING_DIRECTORIES"] = str(root)
        scrub_environ()
        check("scrub_environ leaves GIT_CEILING_DIRECTORIES", "GIT_CEILING_DIRECTORIES" in os.environ)
        os.environ.pop("GIT_CEILING_DIRECTORIES", None)
        os.environ.pop(PROTECT_ENV, None)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("FAILED %d" % fails if fails else "ALL OK")
    return 1 if fails else 0


# Importing the module is the opt-in; the selftest must see the unguarded Popen first.
if __name__ == "__main__":
    sys.exit(_selftest())
else:
    install()
