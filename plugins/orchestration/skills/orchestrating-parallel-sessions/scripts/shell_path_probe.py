r"""Does the shell you are typing into mangle path arguments? Run it FROM that shell.

WHY THIS EXISTS. An orchestrator was warned that `git show <ref>:<path>` gets mangled here.
They tested it, saw nothing, concluded the warning was wrong - and then spent real time chasing
16 "missing" files that were all present. Their words: *"I dismissed it because I tested it with
a path that had no slashes in it."*

⛔ THEIR TEST OMITTED THE TRIGGER, so it came back clean and DISPROVED A TRUE WARNING. That is
worse than no test: it does not merely fail to confirm, it licenses ignoring the thing.

⭐ THE GENERAL RULE, worth more than this script: **WHEN YOU TEST A WARNING AND IT DOES NOT
REPRODUCE, THE FIRST HYPOTHESIS IS THAT YOUR TEST LACKED THE TRIGGER - NOT THAT THE WARNING IS
FALSE.** A warning names a condition. A test that omits the condition tests nothing, and returns
the comfortable answer while doing it.

⚠️ AND THIS FILE GOT IT WRONG FOUR TIMES BEFORE IT WORKED. That is the point of reading it -
each version looked like a test, ran clean, and tested a different nothing:

  v1  handed the trigger to `printf`, a SHELL BUILTIN. Builtins never leave the MSYS world, so
      nothing converts. It carried the trigger CHARACTERS and not the trigger CONDITION.
  v2  handed it to `git` - correctly native - but through `bash -c` invoked from Python. The
      conversion happens at a different boundary than the one that tested.
  v3  ran from the right shell, but with the canary `HEAD:some/dir/file.md`. Also clean: a
      non-path left-hand side is not the trigger either.
  v4  is this one, and its canary was found by BISECTION rather than by reasoning:

        a/b:c/d                  ->  a/b:c/d                   clean
        a/b:.c/d                 ->  MANGLED
        origin/main:shared/x     ->  origin/main:shared/x      clean
        origin/main:.shared/x    ->  MANGLED

⭐ **THE TRIGGER IS A LEADING DOT ON THE RIGHT OF THE COLON.** MSYS reads `x:.y` as a
colon-separated PATH LIST containing a relative dot-entry, and rewrites the whole argument -
forward slashes to backslashes, colon to semicolon. Slashes alone do not do it.
Both-sides-look-like-paths does not do it.

⚠️ WHICH MEANS THE ORIGINAL WARNING WAS TRUE AND MIS-STATED. It said "paths with slashes and/or
colons"; someone tested exactly that, saw nothing, and reasonably concluded it was false. **A
warning that names the wrong trigger gets disproved by an honest test - and then a real bug is
correctly ignored by someone doing their job properly.** Both parties behaved well.

⭐ PRACTICAL SCOPE, sharper than "be careful with paths": it hits `.shared/`, `.claude/`,
`.github/` - **dotfile directories, which is where tooling and config live.** Ordinary source
paths are untouched, which is why this appears when you inspect infrastructure and never when
you inspect application code.

    python shell_path_probe.py --arg "origin/main:.shared/scripts/thing.py"

  Type that INTO the shell in question. It reports what actually arrived.

  exit 0 = arrived intact    exit 1 = MANGLED, with the evidence
"""
import argparse
import pathlib
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CANARY = "origin/main:.shared/scripts/thing.py"

# The four canaries that were WRONG, kept so the difference is visible rather than claimed.
# Every one of them reports CLEAN on a machine that demonstrably mangles.
CONTROLS = [
    ("no trigger at all", "HEAD"),
    ("slashes, no dot after the colon", "HEAD:some/dir/file.md"),
    ("both sides paths, no dot", "a/b:c/d"),
    ("dot on the LEFT only", ".a/b:c/d"),
]


def looks_mangled(arg):
    """Detect the transformation by SHAPE, not by comparing to a fixed constant.

    ⭐ An earlier version tested `got == CANARY`, which meant ANY other argument reported
    MANGLED - including the deliberately-clean controls. That is a check that cannot pass,
    which is as useless as one that cannot fail: it stops carrying information either way.
    What identifies the conversion is backslashes appearing where forward slashes were sent,
    or a semicolon replacing the colon.
    """
    return "\\" in arg or (";" in arg and ":" not in arg)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--arg", help="a rev:path written INLINE in the shell command")
    a = ap.parse_args()

    if not a.arg:
        print("shell path probe")
        print()
        print("  Run this FROM the shell you want to test, with the trigger written inline:")
        print()
        print('      python %s --arg "%s"' % (pathlib.Path(__file__).name, CANARY))
        print()
        print("  ⛔ It must be typed into that shell. Nothing invoked from Python can observe")
        print("     this - the conversion happens as the shell hands an argument to a native")
        print("     binary, and a subprocess call never crosses that boundary. FOUR earlier")
        print("     versions of this probe reported CLEAN on a machine that demonstrably")
        print("     mangles; the docstring records how each one was wrong.")
        return 2

    got = a.arg
    print("  arrived as: %s" % got)
    print()

    if not looks_mangled(got):
        print("  [ok] this argument arrived intact.")
        print()
        if got in [c for _, c in CONTROLS]:
            print("  ⚠️ BUT THAT ARGUMENT CARRIES NO TRIGGER, so this result means nothing.")
            print("     It is one of the four canaries that were wrong. Re-run with:")
            print('        --arg "%s"' % CANARY)
            return 0
        print("  ⭐ Scope: this says the form you passed survived in THIS shell. It says")
        print("     nothing about another shell, and it would have said 'clean' either way")
        print("     had the argument lacked a dot-prefixed path after the colon. If a")
        print("     mangling warning does not reproduce for you, suspect your test first.")
        return 0

    print("  ⛔ MANGLED. This shell rewrites path arguments on the way to a native binary.")
    print()
    if "\\" in got:
        print("     forward slashes became backslashes")
    if ";" in got and ":" not in got:
        print("     the colon became a semicolon - so `rev:path` is no longer a rev:path")
    print()
    print("     The trigger is a LEADING DOT after the colon: `.shared/`, `.claude/`,")
    print("     `.github/` - dotfile directories, which is where tooling and config live.")
    print("     Ordinary source paths pass through fine, which is why this bites when you")
    print("     inspect infrastructure and never when you inspect application code.")
    print()
    print("  What you will actually hit: `git show <ref>:<path>` reports a PRESENT file as")
    print("  missing and a VALID ref as unknown - confidently, with an error that reads like")
    print("  a fact about your repository rather than about your shell.")
    print()
    print("  Use argument lists, not shell strings: subprocess without a shell, or")
    print("  `git cat-file -p` with the parts passed separately.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
