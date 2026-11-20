#!/usr/bin/env python3
"""
<!-- subject: orchestration -->
<!-- route-tags: reader collection view versions stages compare notes read-only -->

A collection is a folder of documents named in `.shared/state/reader/collections.json` (see
reader_collections.py). This module finds the documents in one, groups the versions of each, draws
a tree by stage, a document page, and a Compare page, and keeps notes through a store the caller
supplies. It writes nothing but notes, and those go to the per-machine state folder through the
caller's store, never into a document.

No workspace-specific words live here, so the file can be published. Everything that depends on
the host page (Markdown drawing, the secret-name veto, the notes store, the Compare engine) is
passed in by the caller and is optional.

Routes (the Reader serves them, this module draws them), as `/doc/<slug>` slugs:
    c/<id>               the tree, and a short intro
    c/<id>/p/<rel>       one document, <rel> being its path under the collection root
    c/<id>/cmp/<rel>     a draft beside its rewrite

The reserved id `library` belongs to the document library and is never drawn here: a configured
collection with that id is refused with a message.
"""
import hashlib
import hmac
import html
import os
import re
import secrets
import threading
from pathlib import Path

MAX_DEPTH = 8
MAX_FILES = 2000
MAX_TEXT = 2 * 1024 * 1024
RESERVED_ID = "library"
SLUG_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
_REPARSE_POINT = 0x400


def _natural(s):
    return [int(t) if t.isdigit() else t.casefold() for t in re.split(r"(\d+)", s)]


def glob_regex(pattern):
    """A glob such as `**/*.md` as a compiled regex over a posix path under the root."""
    out, i, p = [], 0, pattern.replace("\\", "/")
    while i < len(p):
        if p.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif p.startswith("**", i):
            out.append(".*")
            i += 2
        elif p[i] == "*":
            out.append("[^/]*")
            i += 1
        elif p[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(p[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$", re.I)


def _e(s):
    return html.escape("" if s is None else str(s), quote=True)


def clean_doc_rel(rel):
    """A path under a collection root in canonical form, or None for a shape that is refused."""
    if not isinstance(rel, str) or not rel or len(rel) > 400:
        return None
    r = rel.replace("\\", "/").strip().lstrip("/")
    parts = r.split("/")
    if any(p in ("", ".", "..") or ":" in p or "\x00" in p for p in parts):
        return None
    return r


# ------------------------------------------------------------------ discovery

class CollectionCorpus:
    """The documents of one collection, found by the collection's own rules.
    """

    def __init__(self, workspace, collection, viewable=None, key=None):
        self.workspace = Path(workspace).resolve()
        self.collection = collection
        self.rel = collection.root
        self.root = self.workspace / collection.root.replace("\\", "/")
        self.key = key if key is not None else secrets.token_bytes(16)
        self._lock = threading.Lock()
        self.viewable = viewable          # callable(rel under the workspace) -> bool, or None
        self._glob = glob_regex(collection.glob)
        self._facts = {}

    def sid(self, rel):
        return hmac.new(self.key, ("%s:%s" % (self.collection.id, rel)).encode("utf-8"),
                        hashlib.sha256).hexdigest()[:20]

    def _root_resolved(self):
        try:
            return self.root.resolve()
        except OSError:
            return None

    def confine(self, p):
        root = self._root_resolved()
        if root is None:
            return None
        try:
            rp = Path(p).resolve()
        except (OSError, ValueError):
            return None
        return rp if rp.is_relative_to(root) and rp != root else None

    def _allowed(self, rel_under_root):
        if self.viewable is None:
            return True
        try:
            return bool(self.viewable((self.rel.replace("\\", "/").strip("/") + "/" +
                                       rel_under_root)))
        except Exception:
            return False

    def header_final(self, path, stamp):
        """True when the file's first lines carry the collection's final-rule header line."""
        field = (self.collection.final_rule or {}).get("header_field") or ""
        if not field:
            return False
        k = str(path)
        hit = self._facts.get(k)
        if hit and hit[0] == stamp:
            return hit[1]
        prefix = (self.collection.final_rule or {}).get("value_prefix") or ""
        rx = re.compile(r"(?im)^[ \t]*(?:#+[ \t]*|>[ \t]*)?%s[ \t]*:[ \t]*(%s\S.*)$" % (
            re.escape(field), re.escape(prefix)))
        try:
            with open(path, "rb") as fh:
                head = fh.read(4096).decode("utf-8", "replace")
            got = bool(rx.search(head))
        except OSError:
            got = False
        self._facts[k] = (stamp, got)
        return got

    def discover(self):
        """{"exists", "root_rel", "files": [...], "by_id": {id: file}, "by_rel": {rel: file}}.

        A file is {id, name, stem, path, rel, dir, stage, final, kind, version, author, name_key,
        group, pair, mtime}. Hidden stages are left out; so is anything the host's veto refuses."""
        c = self.collection
        out = {"exists": False, "root_rel": self.rel, "files": [], "by_id": {}, "by_rel": {}}
        root = self._root_resolved()
        if root is None or not root.is_dir():
            return out
        out["exists"] = True
        skip = set(c.skip_dirs or ())
        found, seen = [], set()

        def plain(entry):
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                return False
            return not (entry.is_symlink() or getattr(st, "st_file_attributes", 0) & _REPARSE_POINT)

        def walk(d, depth):
            try:
                with os.scandir(d) as it:
                    entries = sorted(it, key=lambda e: _natural(e.name))
            except OSError:
                return
            for entry in entries:
                if len(found) >= MAX_FILES:
                    return
                if entry.name.startswith((".", "_")):
                    continue
                child = d / entry.name
                try:
                    isdir, isfile = entry.is_dir(), entry.is_file()
                except OSError:
                    continue
                if isdir:
                    if entry.name in skip or depth >= MAX_DEPTH:
                        continue
                    rc = child if plain(entry) else self.confine(child)
                    if rc is None or rc in seen:
                        continue
                    seen.add(rc)
                    walk(child, depth + 1)
                elif isfile:
                    if not plain(entry) and self.confine(child) is None:
                        continue
                    rel = child.relative_to(root).as_posix()
                    if self._glob.match(rel) and self._allowed(rel):
                        try:
                            st = entry.stat()
                        except OSError:
                            continue
                        found.append((child, rel, st))

        walk(root, 0)
        for child, rel, st in found:
            stage = c.stage_for(rel) if c.stages else None
            if stage is not None and stage.get("hidden"):
                continue
            parts = c.parse_name(rel)
            dirpart = rel.rsplit("/", 1)[0] if "/" in rel else ""
            f = {"id": self.sid(rel), "name": child.name, "stem": child.stem, "path": child,
                 "rel": rel, "dir": dirpart, "stage": stage,
                 "stage_id": stage["id"] if stage else "",
                 "kind": parts.kind or "draft", "version": parts.version, "author": parts.author,
                 "name_key": parts.name_key, "pair": None, "mtime": st.st_mtime,
                 "final": bool(stage and stage.get("approved"))
                 or self.header_final(child, (st.st_mtime_ns, st.st_size))}
            out["files"].append(f)
            out["by_id"][f["id"]] = f
            out["by_rel"][rel] = f
        self._group(out["files"])
        return out

    def _group(self, files):
        """Put each file in a version group, give the unnumbered newest file its effective
        version, and pair each rewrite with its draft by the collection's rule."""
        rule = (self.collection.version_rule or {}).get("type")
        vdir = (self.collection.version_rule or {}).get("folder", "versions")
        groups = {}
        for f in files:
            d = f["dir"]
            if rule == "folder" and d.rsplit("/", 1)[-1] == vdir:
                d = d.rsplit("/", 1)[0] if "/" in d else ""
            key = (d, str(f["name_key"]).casefold())
            f["group"] = "%s|%s" % key
            groups.setdefault(key, []).append(f)
        for mem in groups.values():
            top = max([m["version"] for m in mem if m["version"]] or [0])
            for m in mem:
                m["eff_version"] = m["version"] if m["version"] else (
                    top + 1 if rule == "folder" else 0)
            mem.sort(key=lambda m: (-(m["eff_version"] or 0), -m["mtime"], m["name"]))
            drafts = [m for m in mem if m["kind"] == "draft"]
            for r in (m for m in mem if m["kind"] == "rewrite"):
                if not drafts:
                    break
                same = [x for x in drafts if x["version"] and x["version"] == r["version"]]
                below = [x for x in drafts if (x["eff_version"] or 0) <= (r["eff_version"] or 0)]
                d = (same or below or drafts)[0]
                r["pair"] = d["id"]
                if d["pair"] is None or (r["eff_version"] or 0) >= (
                        mem_by_id(mem, d["pair"]).get("eff_version") or 0):
                    d["pair"] = r["id"]

    def resolve(self, sid, listing=None):
        listing = listing or self.discover()
        f = listing["by_id"].get(sid) if isinstance(sid, str) else None
        if f is None:
            return None, "no such document (the id is from an earlier run of the Reader)"
        return f, ""


def mem_by_id(members, fid):
    return next((m for m in members if m["id"] == fid), {})


# ------------------------------------------------------------------ drawing

CSS = """<style>
.cl-tree .cl-stage{margin:.3rem 0}.cl-tree summary{cursor:pointer;font-weight:600;padding:.25rem .4rem}
.cl-grp{margin:.1rem 0 .5rem .6rem}.cl-gt{font-size:.85rem;opacity:.75}.cl-dir{opacity:.6;font-size:.78rem}
.cl-row{display:block;padding:.15rem .4rem;text-decoration:none;color:inherit;border-radius:4px}
.cl-row[aria-current="true"]{background:rgba(128,128,128,.22)}
.cl-row .cl-v{opacity:.7;font-size:.78rem;margin-left:.3rem}
.cl-badge{font-size:.7rem;border:1px solid rgba(128,128,128,.5);border-radius:3px;padding:0 .3rem;margin-left:.3rem}
.cl-final{border-color:#2e7d32;color:#2e7d32}
.cl-page{padding:1rem 1.4rem 5rem;max-width:60rem}.cl-err{color:#b71c1c;margin:.4rem 0}
.cl-notes textarea{width:100%;min-height:4rem}.cl-n{border-top:1px solid rgba(128,128,128,.3);padding:.3rem 0}
.cl-n small{opacity:.65;margin-left:.5rem}.cl-chg{border:1px solid rgba(128,128,128,.35);border-radius:4px;margin:.5rem 0;padding:.4rem .6rem}
.cl-chg ins{background:rgba(46,125,50,.2);text-decoration:none}.cl-chg del{background:rgba(183,28,28,.18)}
.cl-tag{font-size:.7rem;opacity:.7;text-transform:uppercase;margin-right:.4rem}
</style>"""


def _href(cid, kind, rel):
    from urllib.parse import quote
    return "#c/%s/%s/%s" % (cid, kind, "/".join(quote(s, safe="") for s in rel.split("/")))


def _kind_label(c, kind):
    for k in c.kinds:
        if k["id"] == kind:
            return k["label"]
    return kind


class Entry:
    def __init__(self, collection, corpus, store, compare):
        self.collection, self.corpus, self.store, self.compare = collection, corpus, store, compare


class Views:
    """The collections of one workspace and the pages for them. `refresh()` re-reads the config
    only when its file changed. Nothing here raises to the Reader: a failure is a message."""

    def __init__(self, workspace, store_factory=None, compare_factory=None, md=None,
                 viewable=None, change_body=None, find_credential=None, loader=None):
        self.workspace = workspace
        self.store_factory = store_factory
        self.compare_factory = compare_factory
        self.md = md
        self.viewable = viewable
        self.change_body = change_body
        self.find_credential = find_credential
        self._loader = loader
        self._lock = threading.Lock()
        self._stamp = "unset"
        self._entries = {}
        self._errors = []
        self._order = []

    # -- configuration -------------------------------------------------------------------

    def _config_stamp(self):
        try:
            import reader_collections as rc
            p = rc.config_path(self.workspace)
            st = p.stat()
            return (st.st_mtime_ns, st.st_size)
        except Exception:
            return None

    def stamp(self):
        return self._config_stamp()

    def refresh(self):
        """(entries in order, errors). Never raises."""
        with self._lock:
            st = self._config_stamp()
            if st == self._stamp:
                return [self._entries[i] for i in self._order], list(self._errors)
            entries, order, errors = {}, [], []
            try:
                if self._loader is not None:
                    cols, errors = self._loader(self.workspace)
                else:
                    import reader_collections as rc
                    cols, errors = rc.load_collections(self.workspace)
                errors = list(errors)
            except Exception as ex:
                cols, errors = [], ["collections could not be loaded: %s" % str(ex)[:120]]
            for c in cols:
                try:
                    if c.id == RESERVED_ID:
                        # The preset (built in) is the Scripts view's own. Any other item with
                        # this id would shadow it in the notes store and the stage rules.
                        if (c.version_rule or {}).get("type") != "builtin":
                            errors.append("a collection cannot use the id '%s': that id is "
                                          "reserved, so that collection was ignored"
                                          % RESERVED_ID)
                        continue
                    corpus = CollectionCorpus(self.workspace, c, viewable=self.viewable)
                    store = self.store_factory(c) if self.store_factory else None
                    cmp_ = self.compare_factory(corpus, c) if self.compare_factory else None
                    entries[c.id] = Entry(c, corpus, store, cmp_)
                    order.append(c.id)
                except Exception as ex:
                    errors.append("collection %s could not be set up: %s" % (c.id, str(ex)[:100]))
            self._stamp, self._entries, self._order, self._errors = st, entries, order, errors
            return [entries[i] for i in order], list(errors)

    def rail(self):
        """{"items": [(id, name)], "errors": [...]} or None when there is nothing to show."""
        entries, errors = self.refresh()
        if not entries and not errors:
            return None
        return {"items": [(e.collection.id, e.collection.name) for e in entries],
                "errors": errors}

    # -- pages ---------------------------------------------------------------------------

    def page(self, slug):
        """(code, nav html, body html) for a slug `c/<id>[/p|cmp/<rel>]`."""
        parts = slug.split("/", 3)
        if len(parts) < 2 or parts[0] != "c" or not SLUG_ID_RE.match(parts[1]):
            return 404, "", "not found"
        entries, errors = self.refresh()
        ent = next((e for e in entries if e.collection.id == parts[1]), None)
        if ent is None:
            return 404, "", "not found"
        mode, rel = (parts[2], parts[3]) if len(parts) == 4 else ("", "")
        if len(parts) == 3 or mode not in ("", "p", "cmp"):
            return 404, "", "not found"
        try:
            listing = ent.corpus.discover()
        except Exception as ex:
            return 200, CSS, '<div class="cl-page cl-err">This collection could not be read: %s</div>' % _e(str(ex)[:120])
        f = None
        if mode:
            r = clean_doc_rel(rel)
            f = listing["by_rel"].get(r) if r else None
            if f is None:
                return 404, "", "not found"
        nav = CSS + self._tree(ent, listing, f)
        if mode == "p":
            return 200, nav, self._doc(ent, listing, f)
        if mode == "cmp":
            return 200, nav, self._compare(ent, listing, f)
        return 200, nav, self._intro(ent, listing, errors)

    def _tree(self, ent, listing, sel):
        c = ent.collection
        files = listing["files"]
        out = ['<div class="count">%s: %d document%s</div>' % (
            _e(c.name), len(files), "" if len(files) == 1 else "s")]
        if not listing["exists"]:
            out.append('<div class="empty">The folder <code>%s</code> does not exist yet.</div>'
                       % _e(c.root))
            return "".join(out)
        stages = [dict(s) for s in c.stages] if c.stages else [{"id": "", "folder": None, "label": "Documents"}]
        names = {s["id"] for s in stages}
        if c.stages and any(f["stage_id"] not in names for f in files):
            stages.append({"id": "", "folder": None, "label": "No stage"})
        out.append('<div class="cl-tree">')
        for st in stages:
            if c.stages:
                mine = [f for f in files if f["stage_id"] == st["id"]]
            else:
                mine = list(files)
            if st.get("hidden") or (not mine and c.stages):
                continue
            out.append('<details class="cl-stage" open><summary>%s (%d)</summary>' % (
                _e(st["label"]), len(mine)))
            groups = {}
            for f in mine:
                groups.setdefault(f["group"], []).append(f)
            unplaced = (c.unplaced_dir or "").casefold()

            def gkey(item):
                mem = item[1][0]
                return ([(x.casefold() == unplaced, _natural(x)) for x in mem["dir"].split("/")],
                        _natural(str(mem["name_key"])))
            for _g, mem in sorted(groups.items(), key=gkey):
                mem.sort(key=lambda m: (-(m.get("eff_version") or 0), -m["mtime"], m["name"]))
                out.append('<div class="cl-grp">')
                if mem[0]["dir"]:
                    out.append('<div class="cl-dir">%s/</div>' % _e(mem[0]["dir"]))
                if len(mem) > 1:
                    out.append('<div class="cl-gt">%s - %d versions</div>' % (
                        _e(mem[0]["name_key"]), len(mem)))
                for m in mem:
                    out.append(self._row(c, m, sel))
                out.append("</div>")
            out.append("</details>")
        out.append("</div>")
        return "".join(out)

    def _row(self, c, m, sel):
        label = _kind_label(c, m["kind"])
        ver = ' <span class="cl-v">v%s</span>' % m["version"] if m["version"] else ""
        stage = (' <span class="cl-badge%s">%s</span>' % (
            " cl-final" if m["final"] else "", _e(m["stage"]["label"])) if m["stage"] else (
            ' <span class="cl-badge cl-final">final</span>' if m["final"] else ""))
        return ('<a class="cl-row" href="%s" aria-current="%s"><span class="nm">%s</span>%s'
                '<span class="kb k-%s">%s</span>%s</a>' % (
                    _e(_href(c.id, "p", m["rel"])),
                    "true" if sel is not None and sel["rel"] == m["rel"] else "false",
                    _e(m["stem"]), ver, _e(m["kind"]), _e(label), stage))

    def _intro(self, ent, listing, errors):
        c = ent.collection
        msg = ['<div class="cl-page"><h2>%s</h2>' % _e(c.name)]
        for er in errors:
            msg.append('<div class="cl-err">Collections config: %s</div>' % _e(er))
        msg.append("<p>Read-only. Pick a document in the list. Versions of one document sit "
                   "together, newest first; a draft and its rewrite have a Compare link.</p></div>")
        return "".join(msg)

    def _read(self, f):
        try:
            if f["path"].stat().st_size > MAX_TEXT:
                return None, "this file is larger than %d MB, so it is not shown" % (MAX_TEXT >> 20)
            text = f["path"].read_bytes().decode("utf-8", "replace")
        except OSError as ex:
            return None, "could not read the file (%s)" % type(ex).__name__
        if self.find_credential:
            kind = self.find_credential(text)
            if kind:
                return None, "refused: the file holds what looks like %s, so it is not shown" % kind
        return text, ""

    def _doc(self, ent, listing, f):
        c = ent.collection
        text, why = self._read(f)
        head = ['<div class="cl-page"><div class="cl-dir">%s</div><h2>%s <span class="kb k-%s">%s</span>' % (
            _e(f["dir"] or c.name), _e(f["stem"]), _e(f["kind"]), _e(_kind_label(c, f["kind"])))]
        if f["stage"]:
            head.append(' <span class="cl-badge%s">%s</span>' % (
                " cl-final" if f["final"] else "", _e(f["stage"]["label"])))
        elif f["final"]:
            head.append(' <span class="cl-badge cl-final">final</span>')
        head.append("</h2><div><code>%s/%s</code></div>" % (_e(c.root), _e(f["rel"])))
        mem = sorted((m for m in listing["files"] if m["group"] == f["group"]),
                     key=lambda m: (-(m.get("eff_version") or 0), m["name"]))
        if len(mem) > 1:
            head.append("<p>Versions: %s</p>" % " ".join(
                '<a href="%s">%s</a>%s' % (_e(_href(c.id, "p", m["rel"])),
                                           _e("v%s" % m["version"] if m["version"] else m["stem"]),
                                           "" if m["rel"] != f["rel"] else " (this one)")
                for m in mem))
        if f["pair"] and ent.compare is not None:
            other = listing["by_id"].get(f["pair"])
            if other:
                head.append('<p><a href="%s">Compare with %s</a></p>' % (
                    _e(_href(c.id, "cmp", f["rel"])), _e(other["stem"])))
        if text is None:
            return "".join(head) + '<div class="cl-err">%s</div></div>' % _e(why)
        if f["name"].lower().endswith((".md", ".markdown")) and self.md is not None:
            body = self.md(text)
        else:
            body = "<pre>%s</pre>" % _e(text)
        return "".join(head) + '<div class="cl-text">%s</div>%s</div>' % (body, self._notes(ent, f))

    def _notes(self, ent, f):
        """The notes box. Hidden for a file the note store cannot key (a type other than .md)."""
        store = ent.store
        if store is None:
            return ""
        try:
            import script_note_store as ns
            ok = ns.clean_rel(f["rel"], ent.collection) is not None
        except Exception:
            ok = False
        if not ok:
            return ""
        notes = store.list(f["rel"])
        rows = "".join('<div class="cl-n">%s<small>%s</small></div>' % (
            _e(n.get("text")), _e(str(n.get("at", "")).replace("T", " ")[:16])) for n in notes)
        doc = "c/%s/p/%s" % (ent.collection.id, f["rel"])
        return ('<details class="cl-notes" open data-coll-notes data-cid="%s" data-did="%s" '
                'data-doc="%s"><summary>Notes on this document (<span data-coll-count>%d</span>)'
                '</summary><div data-coll-list>%s</div><div data-coll-form hidden>'
                '<textarea data-coll-text aria-label="Note on this document" '
                'placeholder="Your note"></textarea><div><button type="button" class="nbtn primary" '
                'data-coll-save>Save note</button> <span data-coll-say role="status"></span></div>'
                "</div></details>" % (_e(ent.collection.id), _e(f["id"]), _e(doc), len(notes), rows))

    def _compare(self, ent, listing, f):
        c = ent.collection
        if ent.compare is None:
            return '<div class="cl-page"><div class="cl-err">Compare is not available here.</div></div>'
        v = ent.compare.view(f["id"])
        if not v.get("ok"):
            return '<div class="cl-page"><div class="cl-err">%s</div></div>' % _e(v.get("error"))
        d, r = v["draft"], v["rewrite"]
        out = ['<div class="cl-page"><h2>Compare</h2><p>%s: <a href="%s">%s</a> &nbsp; %s: '
               '<a href="%s">%s</a></p>' % (
                   _e(_kind_label(c, "draft")), _e(_href(c.id, "p", d["rel"])), _e(d["stem"]),
                   _e(_kind_label(c, "rewrite")), _e(_href(c.id, "p", r["rel"])), _e(r["stem"]))]
        passages = v["cmp"]["passages"]
        if not passages:
            out.append("<p>The body of the two files is identical.</p>")
        matched = v.get("matched") or {}
        for p in passages:
            what = {"mod": "Changed", "add": "Added", "del": "Removed"}.get(p.get("change"), "Changed")
            before, after = p.get("before") or "", p.get("after") or ""
            if self.change_body is not None:
                a, b = self.change_body(before, after)
            else:
                a = "<del>%s</del>" % _e(before) if before else ""
                b = "<ins>%s</ins>" % _e(after) if after else ""
            note = (matched.get(p["key"]) or {}).get("note") or ""
            out.append('<section class="cl-chg"><div class="cl-tag">%s</div>%s%s%s</section>' % (
                what,
                '<div><span class="cl-tag">%s</span>%s</div>' % (_e(_kind_label(c, "draft")), a) if a else "",
                '<div><span class="cl-tag">%s</span>%s</div>' % (_e(_kind_label(c, "rewrite")), b) if b else "",
                '<div class="cl-n"><b>Why:</b> %s</div>' % _e(note) if note.strip() else ""))
        out.append("</div>")
        return "".join(out)

    # -- notes ---------------------------------------------------------------------------

    def add_note(self, cid, did, text):
        """(code, answer dict). The document is named by the id this run issued."""
        if not isinstance(cid, str) or not isinstance(did, str):
            return 400, {"ok": False, "error": "bad request shape"}
        entries, _e_ = self.refresh()
        ent = next((e for e in entries if e.collection.id == cid), None)
        if ent is None or ent.store is None:
            return 404, {"ok": False, "error": "no such collection"}
        f, why = ent.corpus.resolve(did)
        if f is None:
            return 404, {"ok": False, "error": why}
        ok, got = ent.store.add(f["rel"], text, source="laptop")
        if not ok:
            return 400, {"ok": False, "error": got}
        return 200, {"ok": True, "note": got, "notes": ent.store.list(f["rel"])}


# ------------------------------------------------------------------ page script

#: Added to the page only when a collection exists. The notes box of a collection document.
COLL_JS = r"""
(function(){
function esc(t){ var d=document.createElement("div"); d.textContent=t==null?"":String(t); return d.innerHTML; }
function reveal(){
  document.querySelectorAll("[data-coll-form][hidden]").forEach(function(f){
    if(typeof NOTE_TOKEN!=="undefined"&&NOTE_TOKEN){ f.hidden=false; } });
}
new MutationObserver(reveal).observe(document.getElementById("pane"),{childList:true});
reveal();
document.addEventListener("click",function(ev){
  var b=ev.target.closest&&ev.target.closest("[data-coll-save]"); if(!b){return;}
  var box=b.closest("[data-coll-notes]"), ta=box.querySelector("[data-coll-text]"),
      say=box.querySelector("[data-coll-say]"), txt=ta.value;
  if(!txt.trim()){ say.textContent="Write something first."; return; }
  b.disabled=true; say.textContent="Saving...";
  function send(tok,retried){
    fetchWithin("/collection/note",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({token:tok,cid:box.getAttribute("data-cid"),id:box.getAttribute("data-did"),text:txt})},
      FETCH_WRITE_MS,function(r){ return r.json().then(function(j){return {s:r.status,j:j};},
        function(){return {s:r.status,j:null};}); })
    .then(function(x){
      if(x.j&&x.j.ok){
        b.disabled=false;
        box.querySelector("[data-coll-list]").innerHTML=(x.j.notes||[]).map(function(n){
          return '<div class="cl-n">'+esc(n.text)+'<small>'+esc(String(n.at||"").replace("T"," ").slice(0,16))+'</small></div>'; }).join("");
        box.querySelector("[data-coll-count]").textContent=String((x.j.notes||[]).length);
        if(ta.value===txt){ ta.value=""; }
        say.textContent="Saved.";
        if(window.readerForget){ window.readerForget(box.getAttribute("data-doc")); }
      } else if(x.s===403&&!retried&&window.__refreshToken){
        window.__refreshToken(tok,function(t){ NOTE_TOKEN=t; send(t,true); },
          function(){ b.disabled=false; say.textContent="Not saved: the page's token is out of date. Reload. Your text is still here."; });
      } else { b.disabled=false; say.textContent="Not saved: "+((x.j&&x.j.error)||("the server refused it ("+x.s+")"))+" Your text is still here."; }
    }).catch(function(e){ b.disabled=false; say.textContent="Not saved: could not reach the reader's server. Your text is still here."; });
  }
  send(NOTE_TOKEN,false);
});
})();
"""

#: Two edits to the page's main script so a `#c/<id>/...` hash opens a collection page. Applied
#: only when a collection exists, so a workspace without one gets the page byte for byte as before.
JS_PATCHES = (
    ('  var p=h.split("/");\n  if(docOf(p[0])===SCRIPTS){',
     '  var p=h.split("/");\n'
     '  if(p[0]==="c"&&p.length>=2&&/^[a-z0-9][a-z0-9-]{0,31}$/.test(p[1])&&'
     'railD.querySelector(\'.dbtn[data-doc="c/\'+p[1]+\'"]\')){\n'
     '    load(p.map(function(s){ try{ return decodeURIComponent(s); }catch(e){ return s; } }).join("/"),null,true);\n'
     '    return; }\n'
     '  if(docOf(p[0])===SCRIPTS){'),
    ('b.setAttribute("aria-current",b.getAttribute("data-doc")===doc?"true":"false");',
     'b.setAttribute("aria-current",b.getAttribute("data-doc")===(doc.indexOf("c/")===0?'
     'doc.split("/").slice(0,2).join("/"):doc)?"true":"false");'),
)


def patch_js(js):
    """`js` with the collection hash route in. Raises ValueError when the page script changed so
    that a patch no longer applies (a test calls this, so a change to the script cannot pass).
    The Reader catches the raise, says it on stderr and serves the page without the collection
    view, so a mismatch never stops the page."""
    for old, new in JS_PATCHES:
        if js.count(old) != 1:
            raise ValueError("the page script no longer contains the text the collection route patches")
        js = js.replace(old, new)
    return js


def rail_html(rail):
    """The rail block for `Views.rail()`; an empty string for None."""
    if not rail:
        return ""
    out = ['<div class="railhead">Collections</div>']
    for cid, name in rail["items"]:
        out.append('<button type="button" class="dbtn" data-doc="c/%s" aria-current="false">'
                   '<span class="nm">%s</span></button>' % (_e(cid), _e(name)))
    for er in rail["errors"]:
        out.append('<div class="count" role="alert" title="collections.json">%s</div>' % _e(er))
    return "".join(out)
