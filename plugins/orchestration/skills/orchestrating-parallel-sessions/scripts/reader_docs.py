"""The list behind the Reader's "Review documents" page: the Markdown and text files in the
folders the user named, and nothing else.

<!-- subject: orchestration -->
<!-- route-tags: reader documents review folders doc_folders markdown text list -->

WHAT IT IS FOR. A decision document is one kind of thing a person reviews. Most people also keep
proposals, reports, drafts and notes in folders of their own, and want to read them in the same
place. The user names those folders once:

    python orchdoc_config.py set doc_folders proposals,reports

and the Reader lists the `.md`, `.markdown` and `.txt` files inside them under "Review
documents". A file opens in the Reader's read-only file page (`/file/<path>`), which refuses
secrets by name and by content. This module only lists; it never opens a file's text.

WHAT IT REFUSES. A folder that is absolute, climbs out of the workspace with `..`, is not inside
the workspace once symlinks are resolved, or does not exist is reported with the reason and
listed as empty. Hidden folders (a leading dot), `node_modules` and `__pycache__` are skipped. At
most MAX_FILES files are listed for a folder, newest first, and the page says when it stopped.

NOTES. The Reader's notes box writes into an OrchDoc entry through `orchdoc.py note`, and that
needs an entry to attach to. A document in a folder has none, so this page is READ ONLY. To
discuss a document, write a note on the OrchDoc entry that links to it.
"""
import os
import pathlib
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

EXTENSIONS = (".md", ".markdown", ".txt")
SKIP_DIRS = {"node_modules", "__pycache__"}
MAX_FILES = 500
MAX_DEPTH = 8


def folders():
    """The folder names from the `doc_folders` setting, in the order given; [] when unset."""
    try:
        import orchdoc_config
    except ImportError:
        return []
    return orchdoc_config.get_list("doc_folders", ())


def _check(workspace, name):
    """(absolute folder, "") for a folder name that is safe to list, else (None, why)."""
    raw = (name or "").strip().replace("\\", "/")
    if not raw:
        return None, "empty folder name"
    if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        return None, "give the folder relative to the workspace, not as a full path"
    if ".." in raw.split("/"):
        return None, "a folder name may not climb out of the workspace with .."
    root = pathlib.Path(workspace).resolve()
    try:
        p = (root / raw).resolve()
    except (OSError, ValueError):
        return None, "unreadable folder name"
    if not p.is_relative_to(root) or p == root:
        return None, "that folder is not inside the workspace"
    if not p.is_dir():
        return None, "no such folder"
    return p, ""


def listing(workspace, names=None, accept=None):
    """{"folders": [{"name", "ok", "why", "files": [...], "more": int}]} for each folder.

    Each file is {"rel": posix path from the workspace, "name", "dir": its folder under the
    chosen one, "mtime", "size"}. `accept(rel)` may veto a file (the Reader passes its own
    secret check); a vetoed file is left out."""
    names = folders() if names is None else list(names)
    root = pathlib.Path(workspace).resolve()
    out = []
    for name in names:
        p, why = _check(root, name)
        entry = {"name": name, "ok": p is not None, "why": why, "files": [], "more": 0}
        out.append(entry)
        if p is None:
            continue
        found = []
        for dirpath, dirnames, files in os.walk(str(p)):
            depth = len(pathlib.Path(dirpath).relative_to(p).parts)
            dirnames[:] = sorted(d for d in dirnames if not d.startswith(".")
                                 and d not in SKIP_DIRS) if depth < MAX_DEPTH else []
            for f in files:
                if not f.lower().endswith(EXTENSIONS) or f.startswith("."):
                    continue
                full = pathlib.Path(dirpath) / f
                try:
                    real = full.resolve()
                    if not real.is_relative_to(root):
                        continue                     # a link that leaves the workspace
                    st = full.stat()
                except OSError:
                    continue
                rel = real.relative_to(root).as_posix()
                if accept is not None and not accept(rel):
                    continue
                found.append({"rel": rel, "name": f, "mtime": st.st_mtime, "size": st.st_size,
                              "dir": pathlib.Path(dirpath).relative_to(p).as_posix()})
        found.sort(key=lambda r: (-r["mtime"], r["rel"]))
        entry["more"] = max(0, len(found) - MAX_FILES)
        entry["files"] = found[:MAX_FILES]
    return {"folders": out}


def selftest():
    import tempfile
    ok = True

    def t(label, cond):
        nonlocal ok
        print("  [%s] %s" % ("OK " if cond else "FAIL", label))
        ok &= bool(cond)

    with tempfile.TemporaryDirectory() as td:
        ws = pathlib.Path(td)
        (ws / "proposals" / "2026").mkdir(parents=True)
        (ws / "proposals" / ".hidden").mkdir()
        (ws / "proposals" / "a.md").write_text("# A", encoding="utf-8")
        (ws / "proposals" / "2026" / "b.txt").write_text("b", encoding="utf-8")
        (ws / "proposals" / "pic.png").write_bytes(b"x")
        (ws / "proposals" / ".hidden" / "c.md").write_text("c", encoding="utf-8")
        (ws / "outside.md").write_text("x", encoding="utf-8")
        got = listing(ws, ["proposals", "../x", "missing", "/abs", "proposals/.."])
        by = {f["name"]: f for f in got["folders"]}
        t("a chosen folder lists its text and Markdown files, in sub-folders too",
          sorted(f["rel"] for f in by["proposals"]["files"]) ==
          ["proposals/2026/b.txt", "proposals/a.md"])
        t("a picture and a hidden folder are left out",
          not [f for f in by["proposals"]["files"] if f["name"] in ("pic.png", "c.md")])
        t("a folder that climbs out is refused with a reason", not by["../x"]["ok"] and by["../x"]["why"])
        t("a missing folder is reported, not raised", not by["missing"]["ok"])
        t("a full path is refused", not by["/abs"]["ok"])
        t("the workspace itself is refused", not by["proposals/.."]["ok"])
        veto = listing(ws, ["proposals"], accept=lambda rel: not rel.endswith("a.md"))
        t("a vetoed file is left out", [f["name"] for f in veto["folders"][0]["files"]] == ["b.txt"])
    print("\nselftest %s" % ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        sys.exit(selftest())
    import json
    ws = sys.argv[1] if len(sys.argv) > 1 else "."
    print(json.dumps(listing(ws), indent=2))
