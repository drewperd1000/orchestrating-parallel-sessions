#!/usr/bin/env python3
"""Import every Python file beside this one. If a shipped script cannot start, say so.

WHY THIS EXISTS. On 2026-09-17 six scripts in this repository failed on import - route.py,
corpus.py, knowledge_gate.py, knowledge_pages.py, dev_docs_index.py and commitment_guard.py -
because a module they all import at the top had never been published. route.py is the first
thing this repository's documentation tells a reader to run, and it had never run for anyone
who cloned it.

The publisher had verified each of those files and passed. It copied the missing sibling into a
temporary directory, ran the file's selftest there, then wrote the file into a destination that
did not contain the sibling. The directory it checked and the directory it wrote to were
different directories, so the verification was honest about a tree that would not exist.

WHAT THIS DELIBERATELY DOES NOT DO. It does not run the scripts. Running a guard executes its
main() and makes it judge this checkout, which is a different question and one that would start
failing for reasons nobody can act on. It imports, and an import is the floor: if a file cannot
be imported, nothing else about it matters.

  exit 0  every file imported
  exit N  N files failed, each printed with its error
"""
import importlib.util
import pathlib
import sys
import traceback

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = pathlib.Path(__file__).resolve().parent
SELF = pathlib.Path(__file__).resolve()


def main():
    files = sorted(p for p in HERE.glob("*.py") if p.resolve() != SELF)

    # ⛔ AN EMPTY RUN IS NOT A PASS. If this file is ever moved, or the layout changes, the
    # glob quietly matches nothing and every future run reports success. The thing most likely
    # to go stale here is the assumption that there is anything to check.
    if not files:
        print("REFUSING: no Python files beside %s - nothing was checked" % SELF.name)
        return 1

    failed = []
    for f in files:
        spec = importlib.util.spec_from_file_location("check_%s" % f.stem, str(f))
        mod = importlib.util.module_from_spec(spec)
        # A script that calls sys.exit() at import time has still imported. argparse does this
        # on --help and several of these files run a main() guard; neither is a broken import.
        try:
            spec.loader.exec_module(mod)
            print("  [ok]     %s" % f.name)
        except SystemExit:
            print("  [ok]     %s  (exited during import, which is not an import failure)"
                  % f.name)
        except BaseException:
            print("  [FAILED] %s" % f.name)
            for line in traceback.format_exc().rstrip().split("\n")[-4:]:
                print("           %s" % line)
            failed.append(f.name)

    print()
    print("%d file(s) checked, %d failed" % (len(files), len(failed)))
    if failed:
        print()
        print("A shipped script that cannot be imported has never worked for anyone who")
        print("cloned this repository. The usual cause is a module it imports that was not")
        print("published beside it.")
    return len(failed)


if __name__ == "__main__":
    sys.exit(main())
