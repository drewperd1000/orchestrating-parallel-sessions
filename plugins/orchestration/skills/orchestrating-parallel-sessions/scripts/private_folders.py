#!/usr/bin/env python3
"""The folders whose files are written by outsiders: no generated index reads, lists or lands them.

<!-- subject: agent-security -->

<!-- route-tags: private folders private-feedback private_inbox outsider text never index never
     name generator exclusion dev-docs-index knowledge pages land step -->

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
