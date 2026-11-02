#!/usr/bin/env python3
"""Refuse a process launch that could put a console window on the human's screen.

<!-- subject: orchestration -->
<!-- route-tags: console window pop up foreground CREATE_NO_WINDOW creationflags subprocess
     launch invisible hidden detached pre-commit guard no_window os.system -->

⛔ THE HUMAN, 2026-09-24 (via o1): console windows kept jumping in front of his work, in bursts of
5-20. The cause was found and fixed in de886bf1 - a server started with DETACHED_PROCESS had
no console, so every `git` it ran got a fresh, visible one. He then asked for this:
*"build in a requirement that every script or process your tooling creates or launches runs
invisibly - never in a window that takes over his screen. Make it a check that fails, not only
a written rule."*

WHAT IT REFUSES, per Python file (read with `ast`, so a call inside a string or a comment is
never counted):

  - `subprocess.run / Popen / call / check_call / check_output` with no `creationflags=`
    keyword and no `**kwargs` splat. The fix is `creationflags=no_window.flags()` - see
    no_window.py for the rule and the measurements behind it.
  - `os.system`, `os.popen`, `subprocess.getoutput`, `subprocess.getstatusoutput`. None of
    them can take creationflags at all, so the fix is to use subprocess.run with the flag.

It resolves `import subprocess as sp`, `from subprocess import run`, and `x.subprocess.run`.

⚠️ WHAT IT CANNOT SEE: a wrapper such as `def run(cmd, **kw): return subprocess.run(cmd, **kw)`
passes, because the splat may carry the flag - so the wrapper's CALLERS are not checked. It
reads Python only; a `.ps1` or `.mjs` launcher is not covered.

THE ESCAPE, for the rare call that must NOT get the flag - on the line where the call starts:

    subprocess.run(cmd)  # no-window: <why, 10 or more characters>

An escape without a reason is itself a finding. Honoured escapes are printed as a count on
every run, so they stay visible.

Usage:
    check_no_window.py --staged      staged .py files under .shared/scripts/ (pre-commit)
    check_no_window.py --all         every .py file under .shared/scripts/
    check_no_window.py <paths...>    these files
    add --escapes to list every honoured escape

Exit 0 clean, 9 on findings (the pre-commit convention: 9 REFUSES, any other non-zero is a
crash and must not block the commit), 2 on a crash or bad usage. ASCII output only.
"""
import argparse
import ast
import io
import os
import re
import subprocess
import sys
import tokenize

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

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
SCOPE = ".shared/scripts/"            # what --staged checks, relative to the repo root
SKIP_DIRS = {".codesight", "__pycache__"}

LAUNCHERS = {"run", "Popen", "call", "check_call", "check_output"}
# Launches with no creationflags parameter at all: the only fix is to not use them.
NO_FLAG_SUBPROCESS = {"getoutput", "getstatusoutput"}
NO_FLAG_OS = {"system", "popen"}

ESCAPE = re.compile(r"#\s*no-window:(.*)$")
MIN_REASON = 10

FIX_FLAG = "add creationflags=no_window.flags()  (see .shared/scripts/no_window.py)"
FIX_REPLACE = ("use subprocess.run([...], creationflags=no_window.flags()) instead - "
               "this call cannot hide its window")


def _aliases(tree):
    """Names this module uses for the subprocess and os modules and their functions.

    Collected from every import in the file, at any depth. A function-local import is rare
    and over-collecting it only ever adds a finding, never hides one.
    """
    mods = {"subprocess": set(), "os": set()}
    funcs = {}                        # local name -> ("subprocess"|"os", real function name)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name in mods:
                    mods[a.name].add(a.asname or a.name)
        elif isinstance(node, ast.ImportFrom) and node.module in mods and node.level == 0:
            for a in node.names:
                funcs[a.asname or a.name] = (node.module, a.name)
    return mods, funcs


def _target(func, mods, funcs):
    """("subprocess"|"os", name) when `func` is a subprocess or os function, else None."""
    if isinstance(func, ast.Name):
        return funcs.get(func.id)
    if isinstance(func, ast.Attribute):
        base = func.value
        for mod in ("subprocess", "os"):
            if isinstance(base, ast.Name) and base.id in mods[mod]:
                return mod, func.attr
            # `lane_status.subprocess.run(...)` - reached through another module
            if isinstance(base, ast.Attribute) and base.attr == mod:
                return mod, func.attr
    return None


def _comments(src):
    """{line number: comment text} from real comments only - never from inside a string."""
    out = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                out[tok.start[0]] = tok.string
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    return out


def check_source(src, label):
    """(findings, escapes) for one file's source. Each is a list of (label, line, message)."""
    tree = ast.parse(src, filename=label)
    mods, funcs = _aliases(tree)
    comments = _comments(src)
    findings, escapes = [], []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        hit = _target(node.func, mods, funcs)
        if not hit:
            continue
        mod, name = hit
        shown = "%s.%s" % (mod, name)
        if mod == "subprocess" and name in LAUNCHERS:
            kws = node.keywords
            if any(k.arg is None or k.arg == "creationflags" for k in kws):
                continue
            problem = "%s(...) has no creationflags= -> %s" % (shown, FIX_FLAG)
        elif (mod == "subprocess" and name in NO_FLAG_SUBPROCESS) or \
                (mod == "os" and name in NO_FLAG_OS):
            problem = "%s(...) -> %s" % (shown, FIX_REPLACE)
        else:
            continue
        m = ESCAPE.search(comments.get(node.lineno, ""))
        if m:
            reason = m.group(1).strip()
            if len(reason) >= MIN_REASON:
                escapes.append((label, node.lineno, "%s: %s" % (shown, reason)))
                continue
            problem = ("%s(...) has a '# no-window:' escape with no reason of %d+ characters"
                       " -> say why this call must open a window, or %s"
                       % (shown, MIN_REASON, FIX_FLAG))
        findings.append((label, node.lineno, problem))
    findings.sort(key=lambda f: f[1])
    return findings, escapes


def _git(args, cwd):
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True,
                          creationflags=flags())


def _in_scope(rel):
    rel = rel.replace("\\", "/")
    if not rel.endswith(".py"):
        return False
    parts = rel.split("/")
    return not any(p in SKIP_DIRS for p in parts)


def staged_sources():
    """[(repo-relative path, staged source)] for staged .py files under .shared/scripts/.

    Reads the STAGED blob, not the working copy: the commit carries what is staged, and a
    working copy fixed after `git add` would otherwise pass a commit that still has the call.
    """
    top = _git(["rev-parse", "--show-toplevel"], None)
    if top.returncode != 0:
        raise RuntimeError("not inside a git repository")
    top = top.stdout.decode("utf-8", "replace").strip()
    names = _git(["diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"], top)
    if names.returncode != 0:
        raise RuntimeError("git diff --cached failed")
    out = []
    for rel in names.stdout.decode("utf-8", "replace").split("\0"):
        if not rel.startswith(SCOPE) or not _in_scope(rel):
            continue
        blob = _git(["show", ":" + rel], top)
        if blob.returncode != 0:
            raise RuntimeError("could not read the staged copy of %s" % rel)
        out.append((rel, blob.stdout.decode("utf-8", "replace")))
    return out


def tree_files(root=SCRIPTS):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for f in sorted(filenames):
            if f.endswith(".py"):
                out.append(os.path.join(dirpath, f))
    return out


def _read(path):
    with open(path, "rb") as fh:
        return fh.read().decode("utf-8", "replace")


def _label(path):
    try:
        rel = os.path.relpath(path, SCRIPTS)
    except ValueError:
        return path
    return path if rel.startswith("..") else rel.replace("\\", "/")


def selftest():
    """`check_no_window.py selftest`: the checker refuses what it should and passes the rest.
    The public publisher runs every published file's selftest; this one had none."""
    cases = [
        ("import subprocess\nsubprocess.run(['git'])\n", 1, "bare subprocess.run"),
        ("import os\nos.system('dir')\n", 1, "os.system"),
        ("from subprocess import Popen\nPopen(['x'])\n", 1, "imported Popen"),
        ("import subprocess\nsubprocess.run(['git'], creationflags=0)\n", 0, "flag given"),
        ("import subprocess\ndef r(c, **kw):\n    return subprocess.run(c, **kw)\n", 0,
         "splat wrapper"),
        ("import subprocess\nsubprocess.run(['x'])  # no-window: a reason of ten chars\n", 0,
         "escape with a reason"),
        ("s = 'subprocess.run(x)'\n", 0, "inside a string"),
    ]
    bad = 0
    for src, want, what in cases:
        got = len(check_source(src, "<selftest>")[0])
        ok = got == want
        bad += not ok
        print("  [%s] %s: %d finding(s), wanted %d" % ("ok" if ok else "FAIL", what, got, want))
    print("check_no_window selftest: %s" % ("PASS" if not bad else "%d FAILED" % bad))
    return 1 if bad else 0


def main(argv=None):
    if (argv if argv is not None else sys.argv[1:]) == ["selftest"]:
        return selftest()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--staged", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--escapes", action="store_true", help="list every honoured escape")
    ap.add_argument("paths", nargs="*")
    a = ap.parse_args(argv)
    if sum(bool(x) for x in (a.staged, a.all, a.paths)) != 1:
        ap.error("give exactly one of --staged, --all, or paths")

    if a.staged:
        sources = staged_sources()
    else:
        files = tree_files() if a.all else a.paths
        sources = [(_label(p), _read(p)) for p in files]

    findings, escapes, unparsed = [], [], []
    for label, src in sources:
        try:
            f, e = check_source(src, label)
        except SyntaxError as ex:
            unparsed.append("%s: does not parse (%s) - NOT CHECKED" % (label, ex.msg))
            continue
        findings += f
        escapes += e

    for label, line, msg in findings:
        print("%s:%d: %s" % (label, line, msg))
    for u in unparsed:
        print(u)
    if a.escapes:
        for label, line, msg in escapes:
            print("%s:%d: [escape] %s" % (label, line, msg))
    files_hit = sorted({f[0] for f in findings})
    print("check_no_window: %d file(s) checked, %d finding(s) in %d file(s), "
          "%d escape(s) honoured%s"
          % (len(sources), len(findings), len(files_hit), len(escapes),
             "" if a.escapes or not escapes else " (list them with --escapes)"))
    if findings:
        print("  Every process a tool launches must be invisible - a console window over "
              "the human's work is the failure this refuses. Fix each line as shown.")
        return 9
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as ex:           # a crash must never read as a refusal
        print("check_no_window: CRASHED, nothing was checked: %s: %s"
              % (type(ex).__name__, ex), file=sys.stderr)
        sys.exit(2)
