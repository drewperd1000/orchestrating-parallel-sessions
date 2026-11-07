#!/usr/bin/env python3
"""The folders whose files are written by outsiders: no generated index reads, lists or lands them.

<!-- subject: agent-security -->

<!-- route-tags: private folders private-feedback private_inbox outsider text never index never
     name generator exclusion dev-docs-index knowledge pages land step -->

The lane rules say no session reads them, and that text must never reach a file on origin/main.
Before this module, neither index generator excluded them: dev_docs_index.py walked every folder
of the tree and the memory store, and knowledge_pages.py read every `.md` corpus.py lists, which
includes the whole memory store. The nightly land step only hid such a file's NAME while its
content could still land.

Who reads these names, matched by folder NAME wherever the folder sits, in any letter case:
  - dev_docs_index.py      SKIP_DIRS, and its walk prunes either name before descending
  - knowledge_pages.py     drops any input whose path has a part with either name, unread
  - land_generated_indexes.py   refuses to land when a file whose text is in its outputs sits
                           under either folder, and counts such files instead of naming them
  - check_script.py        neither folder walk (discover, discover_corpus) descends into
                           or starts inside either folder, and main() refuses a file in
  - training_header.py     the --all walk (walk_artifacts) starts in, enters and follows a link
                           into neither folder, and a file named on its command line in one is
  - dev_docs_index.py      also starts in no private folder, enters no junction or symbolic

or symbolic link that points at `private-feedback` reaches its files under the link's own name, and
the folder itself also answers to its 8.3 short name (`APP-FE~1`) and to
`private-feedback::$INDEX_ALLOCATION`. A test of the names in a path misses all three. So:
  - in_private_folder(path) tests the path as given and the path os.path.realpath() resolves,
    which turns each of the three back into `private-feedback`. check_script.py uses it for the
    start folder of both walks and for a file named on its command line, and
    check_checklist_router.py for the start folder of the waypoint check's walk.
  - is_link(path) is True for a junction or a symbolic link. Those walks do not follow one:
    os.walk() already declines to follow a symbolic link to a folder, but Python 3.12 follows a
    junction, and this account can make a junction without admin rights.
A hard link to a private file is the same file under a second name, and no path test can tell
it apart; nothing here catches one.

    from private_folders import NAMES, is_private_name, has_private_part
    from private_folders import in_private_folder, is_link
"""
import os
import re

#: The folder names. Lower case; every comparison lower-cases the other side.
NAMES = ("private-feedback", "private_inbox")

_LOWER = frozenset(NAMES)
_SPLIT = re.compile(r"[\\/]+")


def is_private_name(name):
    """True when one folder name is either private folder's name, in any case."""
    return str(name).lower() in _LOWER


def has_private_part(path):
    """True when any FOLDER in `path` (str, bytes or path-like; either slash) has a private
    name. The last part is the file's own name and is not a folder: `private-feedback.md` is not
    private, `private-feedback/x.md` is."""
    text = os.fsdecode(os.fspath(path)) if not isinstance(path, str) else path
    parts = [p for p in _SPLIT.split(text) if p]
    return any(is_private_name(p) for p in parts[:-1])


def _named_private(text):
    """True when any part of the absolute path `text`, the last included, is a private name."""
    return any(is_private_name(p) for p in _SPLIT.split(text) if p)


def in_private_folder(path):
    """True when `path` (str, bytes or path-like) is a private folder or sits anywhere under one,
    read two ways: the path as given, made absolute, and the path os.path.realpath() resolves.

    The second reading catches a junction or symbolic link to a private folder, the folder's 8.3
    short name and `<folder>::$INDEX_ALLOCATION` (see the module docstring). realpath() asks the
    file system for the path's final name: it reads no file and lists no folder. A path it cannot
    resolve counts as private, so a caller never opens a file this could not check. Unlike
    has_private_part(), the last part counts too: a walk's start folder named `private-feedback` is
    private."""
    try:
        text = os.fsdecode(os.fspath(path))
        return _named_private(os.path.abspath(text)) or _named_private(os.path.realpath(text))
    except (OSError, ValueError):
        return True


# The reparse tag of a junction (a Windows mount point), for a Python without os.path.isjunction.
_MOUNT_POINT_TAG = 0xA0000003


def is_link(path):
    """True when `path` is a symbolic link or a junction. A walk that never follows a link skips
    every entry this is True for. Uses os.path.isjunction where Python has it (3.12 and later),
    else the junction's reparse tag."""
    if os.path.islink(path):
        return True
    isjunction = getattr(os.path, "isjunction", None)
    if isjunction is not None:
        return isjunction(path)
    try:
        return getattr(os.lstat(path), "st_reparse_tag", 0) == _MOUNT_POINT_TAG
    except (OSError, ValueError):
        return False
