#!/usr/bin/env python3
"""The main checkout that holds a file, found with NO git process and only `os` imported.

<!-- subject: infrastructure -->

<!-- route-tags: main checkout hook git-free commondir worktree workspace_paths cloud -->

    from checkout_lookup import checkout_of
    CHECKOUT = checkout_of(__file__)          # a str: the main checkout, or <home>/<your-workspace>

It walks up from the path (symlinks resolved) to the first folder holding `.shared/scripts`.
A `.git` FOLDER there makes that folder the main checkout. A `.git` FILE (a worktree) holds
`gitdir: <git dir>`; that git dir's `commondir` file names the common dir (the main checkout's
`.git`), and the main checkout is its parent. A file that cannot be read, or a path in no
workspace, gives `<home>/<your-workspace>`, which is what a cloud session or a second computer
has. workspace_paths.checkout_of() calls this, so the two cannot disagree.

⛔ THE FOUR HOOKS HOLD A COPY OF THE FUNCTION BELOW, NOT AN IMPORT OF THIS FILE (o9L1391,
2026-10-21). A hook runs on every tool call or reply. Importing workspace_paths costs a git
process plus pathlib, subprocess and ctypes (12 to 42 ms per hook); importing this file costs
3 to 4 ms, almost all of it Python scanning the 679-file scripts folder for ONE module and
compiling it (PYTHONDONTWRITEBYTECODE is set, so nothing is cached). A copy costs under 1 ms.
The copy sits between `# BEGIN checkout_of` and `# END checkout_of` markers, named
`_checkout_of`; test_workspace_paths.py fails if a copy differs from the function here.
"""
import os


# BEGIN checkout_of
def checkout_of(path):
    try:
        d = os.path.dirname(os.path.realpath(os.fspath(path)))
        while True:
            if os.path.isdir(os.path.join(d, ".shared", "scripts")):
                c = os.path.join(d, ".git")
                if os.path.isfile(c):  # a worktree: `gitdir: <git dir>`, whose `commondir` names the main .git
                    with open(c, encoding="utf-8", errors="replace") as f:
                        c = f.readline().strip()
                    c = os.path.join(d, c[7:].strip()) if c.startswith("gitdir:") else ""
                    if os.path.isfile(os.path.join(c, "commondir")):
                        with open(os.path.join(c, "commondir"), encoding="utf-8", errors="replace") as f:
                            rel = f.readline().strip()
                        if rel:
                            c = os.path.join(c, rel)
                c = os.path.normpath(c)
                return os.path.dirname(c) if os.path.basename(c).lower() == ".git" else d
            p = os.path.dirname(d)
            if p == d:
                break
            d = p
    except Exception:
        pass
    return os.path.join(os.path.expanduser("~"), "<your-workspace>")
# END checkout_of
