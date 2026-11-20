#!/usr/bin/env python3
"""The Reader's document collections: a config file names folders of documents, how their
versions are told apart, their stages, and the labels shown for them.

<!-- subject: orchestration -->

This module is the generic engine. It carries no workspace-specific names: a preset (a named
bundle of defaults) lives in its own file and is loaded by name, so a copy of the Reader without
that file still works and simply has no preset collection.

The config is one JSON file, `.shared/state/reader/collections.json` under the workspace:
    {"collections": [ {"id": "briefs", "name": "Legal briefs", "root": "briefs", ...}, ... ]}
or a bare list of those objects. An item may say `"preset": "<name>"`; the preset's values are
the starting point and the item's own keys override them.

Failure never raises to the caller: `load_collections` returns (collections, errors). An absent
file gives the preset collection (when its file is present) and no error; a broken file gives the
same collections plus one error string.

Run: python reader_collections.py <workspace>     prints each collection id and any error
"""
import json
import os
import re
import sys
from collections import namedtuple
from pathlib import Path

CONFIG_REL = ".shared/state/reader/collections.json"
DEFAULT_PRESET = "workspace-library"
MAX_CONFIG_BYTES = 64 * 1024
MAX_COLLECTIONS = 8
MAX_STAGES = 12
MAX_KINDS = 12
MAX_PATTERN = 400
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")

#: What every version rule returns, the same shape for every rule type.
NameParts = namedtuple("NameParts", "name_key version author kind in_app")

STAGE_KEYS = {"id", "folder", "label", "approved", "hidden"}
KIND_KEYS = {"id", "label"}
RULE_TYPES = ("name-pattern", "folder", "none")
VIEWS = ("tree", "new", "compare", "search")
TOP_KEYS = {"id", "name", "root", "glob", "skip_dirs", "version_rule", "stages", "unplaced_dir",
            "kinds", "note_syntax", "why_notes", "type_labels", "views", "final_rule",
            "extensions", "preset", "editable"}

#: Values a collection takes when neither the item nor a preset sets them.
DEFAULTS = {
    "glob": "**/*.md",
    "skip_dirs": [],
    "version_rule": {"type": "none"},
    "stages": [],
    "unplaced_dir": "",
    "kinds": [{"id": "draft", "label": "draft"}, {"id": "rewrite", "label": "edit"},
              {"id": "log", "label": "log"}, {"id": "note", "label": "note"}],
    "note_syntax": {"inline": "{{{ }}}", "comment": "{{ }}", "strike": "~~"},
    "why_notes": {"suffix": ".why-notes.json"},
    "type_labels": [],
    "views": ["tree", "search"],
    "final_rule": {"header_field": "Approved-By", "value_prefix": ""},
    "extensions": [],
    "editable": False,
}


class CollectionError(ValueError):
    """A config item that cannot be used; the message is one plain sentence."""


class Collection:
    """One validated collection. Attributes are the config keys; `parse_fn`, when set by a
    preset, replaces the version rule with that preset's own parser."""

    def __init__(self, data, parse_fn=None):
        self.data = data
        self.parse_fn = parse_fn
        for k, v in data.items():
            setattr(self, k, v)
        self._rx = None
        rule = data["version_rule"]
        if rule.get("type") == "name-pattern":
            self._rx = re.compile(rule["pattern"])

    def __repr__(self):
        return "Collection(%r)" % self.id

    def __eq__(self, other):
        return isinstance(other, Collection) and self.data == other.data

    def stage_for(self, rel_under_root):
        """The stage dict whose folder is the first path segment, or None."""
        first = str(rel_under_root).replace("\\", "/").split("/", 1)[0]
        for st in self.stages:
            if st["folder"] == first:
                return st
        return None

    def parse_name(self, name):
        return parse_name(self, name)


# ------------------------------------------------------------------ validation

def _bad(msg):
    raise CollectionError(msg)


def _check_rel(label, value, allow_empty=False):
    if not isinstance(value, str):
        _bad("%s must be text" % label)
    if not value:
        if allow_empty:
            return value
        _bad("%s is empty" % label)
    v = value.replace("\\", "/")
    if v.startswith("/") or re.match(r"^[A-Za-z]:", v) or "\x00" in v:
        _bad("%s must be a folder under the workspace, not an absolute path" % label)
    if any(part == ".." for part in v.split("/")):
        _bad("%s must not contain '..'" % label)
    return value


def _check_folder_name(label, value):
    _check_rel(label, value)
    if "/" in value.replace("\\", "/"):
        _bad("%s must be one folder name" % label)
    return value


def _labels(label, items, allowed, cap, need_id=True):
    if not isinstance(items, list) or len(items) > cap:
        _bad("%s must be a list of at most %d" % (label, cap))
    seen = set()
    out = []
    for it in items:
        if not isinstance(it, dict):
            _bad("each entry of %s must be an object" % label)
        unknown = set(it) - allowed
        if unknown:
            _bad("%s has an unknown key: %s" % (label, ", ".join(sorted(unknown))))
        if need_id:
            i = it.get("id")
            if not isinstance(i, str) or not i or i in seen:
                _bad("%s needs a distinct id on every entry" % label)
            seen.add(i)
        out.append(dict(it))
    return out


def _check_rule(rule, trusted):
    if not isinstance(rule, dict):
        _bad("version_rule must be an object")
    t = rule.get("type")
    if t == "builtin" and trusted:
        return dict(rule)
    if t not in RULE_TYPES:
        _bad("version_rule.type must be one of %s" % ", ".join(RULE_TYPES))
    out = dict(rule)
    if t == "name-pattern":
        pat = rule.get("pattern")
        if not isinstance(pat, str) or not pat or len(pat) > MAX_PATTERN:
            _bad("version_rule.pattern must be a pattern of at most %d characters" % MAX_PATTERN)
        try:
            rx = re.compile(pat)
        except re.error as e:
            _bad("version_rule.pattern does not compile: %s" % e)
        for g in ("name", "ver", "author"):
            if g not in rx.groupindex:
                _bad("version_rule.pattern needs a named group '%s'" % g)
        roles = rule.get("author_roles", {})
        if not isinstance(roles, dict) or not all(isinstance(k, str) and isinstance(v, str)
                                                  for k, v in roles.items()):
            _bad("version_rule.author_roles must map author text to a kind id")
    elif t == "folder":
        out["folder"] = _check_folder_name("version_rule.folder", rule.get("folder", "versions"))
    return out


def _validate(item, workspace, trusted=False, parse_fn=None):
    """Merge defaults and validate one item; the result is a Collection. `workspace` may be
    None to skip the filesystem checks (a unit test of the shape alone)."""
    if not isinstance(item, dict):
        _bad("a collection must be an object")
    unknown = set(item) - TOP_KEYS
    if unknown:
        _bad("unknown key: %s" % ", ".join(sorted(unknown)))
    data = {}
    for k, v in DEFAULTS.items():
        data[k] = json.loads(json.dumps(v))
    for k, v in item.items():
        if k != "preset":
            data[k] = v
    cid = data.get("id")
    if not isinstance(cid, str) or not ID_RE.match(cid):
        _bad("id must be 1 to 32 characters: lower-case letters, digits and hyphens")
    if not isinstance(data.get("name", cid), str):
        _bad("name must be text")
    data.setdefault("name", cid)
    data["root"] = _check_rel("root", data.get("root"))
    if not isinstance(data["glob"], str) or not data["glob"]:
        _bad("glob must be text")
    _check_rel("glob", data["glob"])
    if not isinstance(data["skip_dirs"], list) or not all(isinstance(s, str) for s in data["skip_dirs"]):
        _bad("skip_dirs must be a list of folder names")
    data["version_rule"] = _check_rule(data["version_rule"], trusted)
    data["stages"] = _labels("stages", data["stages"], STAGE_KEYS, MAX_STAGES)
    for st in data["stages"]:
        _check_folder_name("stage folder", st.get("folder"))
        st.setdefault("label", st["id"])
    data["kinds"] = _labels("kinds", data["kinds"], KIND_KEYS, MAX_KINDS)
    if data["unplaced_dir"]:
        _check_folder_name("unplaced_dir", data["unplaced_dir"])
    for k in ("type_labels", "extensions"):
        if not isinstance(data[k], list) or not all(isinstance(s, str) for s in data[k]):
            _bad("%s must be a list of text" % k)
    if not isinstance(data["views"], list) or any(v not in VIEWS for v in data["views"]):
        _bad("views may only contain: %s" % ", ".join(VIEWS))
    for k in ("note_syntax", "why_notes", "final_rule"):
        if not isinstance(data[k], dict):
            _bad("%s must be an object" % k)
    if not isinstance(data["editable"], bool):
        _bad("editable must be true or false")
    if workspace is not None:
        try:
            ws = Path(workspace).resolve()
            root = (ws / data["root"].replace("\\", "/")).resolve()
        except (OSError, ValueError, RuntimeError) as e:
            _bad("root cannot be resolved: %s" % str(e)[:80])
        try:
            root.relative_to(ws)
        except ValueError:
            _bad("root resolves outside the workspace")
    return Collection(data, parse_fn=parse_fn)


def validate_collection(item, workspace=None):
    """Validate one user-written item (no preset expansion). Raises CollectionError."""
    return _validate(item, workspace)


# ------------------------------------------------------------------ presets

def load_preset(name):
    """The preset named `name` as (dict, parse_fn), or None when its file is not present."""
    if name != DEFAULT_PRESET:
        return None
    try:
        import reader_collections_library as lib
    except ImportError:
        return None
    return json.loads(json.dumps(lib.PRESET)), getattr(lib, "parse_name", None)


def _expand(item, workspace):
    """One config item, its preset merged in when it names one."""
    if isinstance(item, dict) and "preset" in item:
        got = load_preset(item["preset"])
        if got is None:
            _bad("preset '%s' is not available here" % (item["preset"],))
        base, fn = got
        merged = dict(base)
        merged.update({k: v for k, v in item.items() if k != "preset"})
        return _validate(merged, workspace, trusted=True, parse_fn=fn)
    return _validate(item, workspace)


def default_collection(workspace=None):
    """The preset collection, or None when the preset file is absent (the public copy)."""
    got = load_preset(DEFAULT_PRESET)
    if got is None:
        return None
    data, fn = got
    return _validate(data, workspace, trusted=True, parse_fn=fn)


# ------------------------------------------------------------------ loading

def config_path(workspace):
    return Path(workspace) / CONFIG_REL


def load_collections(workspace, path=None):
    """(collections, errors). Never raises. No file: the preset collection alone. A broken file,
    or an item that fails validation: the preset collection plus one error string per problem;
    the items that did validate are kept."""
    try:
        base = default_collection(workspace)
    except Exception as e:  # the preset's own root may be a link out of the workspace
        return [], ["the preset collection cannot be used: %s" % str(e)[:120]]
    fallback = [base] if base is not None else []
    try:
        p = Path(path) if path else config_path(workspace)
        if not p.is_file():
            return fallback, []
        if p.stat().st_size > MAX_CONFIG_BYTES:
            return fallback, ["collections config is larger than %d bytes" % MAX_CONFIG_BYTES]
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError, RecursionError) as e:
        return fallback, ["collections config could not be read: %s" % str(e)[:120]]
    items = raw.get("collections") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return fallback, ["collections config must hold a list of collections"]
    if len(items) > MAX_COLLECTIONS:
        return fallback, ["collections config lists more than %d collections" % MAX_COLLECTIONS]
    out, errors, seen = [], [], set()
    for n, item in enumerate(items, 1):
        try:
            c = _expand(item, workspace)
            if c.id in seen:
                _bad("duplicate id '%s'" % c.id)
            seen.add(c.id)
            out.append(c)
        except CollectionError as e:
            errors.append("collection %d: %s" % (n, e))
        except Exception as e:  # this function never raises to its caller
            errors.append("collection %d: could not be read: %s" % (n, str(e)[:120]))
    if base is not None and not any(c.id == base.id for c in out):
        out.insert(0, base)
    return out, errors


# ------------------------------------------------------------------ version rules

_VER_SUFFIX = re.compile(r"^(?P<name>.*?)[ _-]v(?P<ver>[1-9]\d*)$", re.I)


def parse_name(collection, name):
    """The version rule applied to a file name or relative path -> NameParts.
    Rules: a preset's own parser, name-pattern, folder, none."""
    if collection.parse_fn is not None and collection.version_rule.get("type") == "builtin":
        r = collection.parse_fn(name)
        return NameParts(*tuple(r))
    norm = str(name).replace("\\", "/")
    base = norm.rsplit("/", 1)[-1]
    stem = base[:-3] if base.lower().endswith(".md") else os.path.splitext(base)[0]
    rule = collection.version_rule
    t = rule.get("type")
    if t == "name-pattern":
        m = collection._rx.match(base)
        if not m:
            return NameParts(stem, None, None, "draft", False)
        gd = m.groupdict()
        author = gd.get("author")
        kind = "log" if gd.get("log") else rule.get("author_roles", {}).get(author, "draft")
        return NameParts(gd["name"], int(gd["ver"]), author, kind, bool(gd.get("app")))
    if t == "folder":
        parts = norm.split("/")
        in_versions = len(parts) >= 2 and parts[-2] == rule.get("folder", "versions")
        m = _VER_SUFFIX.match(stem)
        if m:
            return NameParts(m.group("name"), int(m.group("ver")), None, "draft", False)
        return NameParts(stem, 1 if in_versions else None, None, "draft", False)
    return NameParts(stem, None, None, "draft", False)


def main(argv):
    ws = argv[1] if len(argv) > 1 else os.getcwd()
    cols, errs = load_collections(ws)
    for c in cols:
        print("collection", c.id, "root", c.root)
    for e in errs:
        print("error", e)
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main(sys.argv))
