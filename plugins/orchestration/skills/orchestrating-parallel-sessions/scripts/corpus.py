#!/usr/bin/env python3
"""ONE enumeration of what this workspace knows. Every consumer reads it from here.

<!-- route-tags: corpus enumeration stores scan set coverage harvest artifacts nested repos knowledge exclusions captured notes -->
<!-- cornerstone: knowledge-routing -->

  1. `name == "CLAUDE.md"` hid a nested repo's own CLAUDE.md, the front door to that repo.
  2. `parent.name == "knowledge"` hid another repo's hand-written reference documents, under a
     rule written for generated pages at `docs/knowledge`.

    corpus.py             what is enumerated, by store, and what every exclusion removed
    corpus.py --paths     the resolved list, one per line
    corpus.py --selftest  kept + excluded == enumerated; no rule is broad without saying so
"""
import argparse
import collections
import os
import pathlib
import re
import sys
import textwrap

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from workspace_paths import WS, MEM, MAIN_CHECKOUT  # noqa: E402
import private_folders  # noqa: E402 - the folders outsiders write, ONE definition

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SKILL_ROOTS = [
    WS / ".claude" / "skills",
    pathlib.Path.home() / ".claude/plugins/marketplaces/<private-repo>/plugins/<your-skills-plugin>/skills",
]

# Directories that never hold this workspace's knowledge. Pruned during the walk, so a
# node_modules tree costs nothing rather than being enumerated and then discarded.
PRUNE = {"node_modules", ".git", "dist", "build", ".next", "coverage", ".venv", "venv",
         "__pycache__", ".astro", "out", ".turbo", ".cache", ".vercel", ".pytest_cache",
         ".svelte-kit", "vendor", ".code-graph"}


# --------------------------------------------------------------------------------------------
# The exclusion rules. (name, reason, matcher) - never a bare pattern.
# --------------------------------------------------------------------------------------------

Exclusion = collections.namedtuple("Exclusion", "name reason match")

# A rule removing more than this share of the enumerated set must SAY SO in its reason, with the
# `broad:` token. See selftest(). The point is not to forbid a big rule - captured notes can be most of
# the set and the exclusion is correct - it is that a rule with an outsized appetite may not
# acquire it silently, which is how `CLAUDE.md` and `docs/knowledge` both grew past their reason.
BROAD_FLOOR = 0.05
BROAD_TOKEN = "broad:"


def _oneof(*paths):
    """Matcher: exactly these files. THE PREFERRED SHAPE when the reason is about location."""
    s = {str(x).lower() for x in paths}
    return lambda p, o: str(p).lower() in s


def _under(root):
    """Matcher: anything below this one directory. Also a path rule - bounded by construction."""
    r = str(root).lower().rstrip("\\/") + os.sep
    return lambda p, o: str(p).lower().startswith(r)


EXCLUSIONS = [
    Exclusion(
        "folders outsiders write",
        "a file under a folder named private-feedback or "
        "private_inbox holds text that outsiders wrote, and "
        "no session reads it. Keeping its NAME out of the corpus keeps it out of route.py's "
        "answers and every generated page. A folder-NAME test (private_folders), because the "
        "folders sit in more than one place.",
        lambda p, o: private_folders.has_private_part(p)),

    Exclusion(
        "CLAUDE.md - the two push-channel copies",
        "global + workspace: already in every session's context before it asks, so indexing "
        "them lets the router answer with text the asker is already holding. A PATH LIST, not "
        "a name match - every OTHER CLAUDE.md is a repo's front door and IS indexed.",
        _oneof(WS / "CLAUDE.md", pathlib.Path.home() / ".claude" / "CLAUDE.md")),

    Exclusion(
        "memory store indexes",
        "MEMORY.md / Memory2.md / README.md at the memory root are indexes OF the store, not "
        "artifacts in it. Routing to an index puts a middleman in front of the answer.",
        _oneof(MEM / "MEMORY.md", MEM / "Memory2.md", MEM / "README.md")),

    Exclusion(
        "captured notes",
        "broad: raw capture, not yet processed into knowledge. This row is the record that they "
        "exist and are deliberately left out.",
        _under(MEM / "captured_notes")),

    Exclusion(
        "our generated knowledge pages",
        "views OF other artifacts - route to the sources, never to the middleman. ONE PATH, "
        "not any directory named 'knowledge': the name form of this rule was silently eating "
        "another repo's docs/knowledge folder, which held hand-written reference documents. "
        "Active only for consumers asking for include_generated=False; route.py wants them.",
        lambda p, o: not o["generated"] and _under(WS / "docs" / "knowledge")(p, o)),

    Exclusion(
        "ORCHESTRATOR-DECISIONS-*",
        "per-orchestrator working state; ask the owning orchestrator. A NAME rule on purpose - "
        "the property is the document's kind, and these live wherever their orchestrator "
        "put them.",
        lambda p, o: p.name.startswith("ORCHESTRATOR-DECISIONS")),

    Exclusion(
        "*pre_prune* snapshots",
        "superseded copies kept for recovery; the live file next to them is the answer. A NAME "
        "rule on purpose - the suffix is what makes it a snapshot, wherever it sits.",
        lambda p, o: "pre_prune" in p.name),

    Exclusion(
        "python caches",
        "__pycache__ / .pytest_cache reached by a recursive store walk. Build output, not "
        "knowledge.",
        lambda p, o: "__pycache__" in p.parts or ".pytest_cache" in p.parts),
]

SCOPE_BOUNDS = [
    ("build + dependency trees pruned",
     "never walked at all, so a node_modules tree costs nothing: %s. NOTE the count is "
     "currently ZERO, and that is the finding rather than a bug: the scope bound below "
     "already keeps the walk out of the trees this set was written for, so PRUNE is a guard "
     "against a future store being added rather than something that fires today. (The three "
     ".pytest_cache READMEs that DO get through arrive via the memory store's rglob, which "
     "does not prune - they are caught by the 'python caches' rule above and counted there.)"
     % ", ".join(sorted(PRUNE))),
    ("nested repos bounded to root *.md + docs/**",
     "a product repo's generated or vendored markdown would flood the index; its authored "
     "docs live in those two places"),

    ("workspace subdirectories not walked at all",
     "the doc store is WS/*.md NON-RECURSIVE plus docs/ and .shared/docs/, so markdown inside "
     "any other workspace subdirectory - orchestration working directories, research corpora, "
     "deliverable sets - is outside the corpus. "
     "A VERIFIED MISS is verified for the stores named "
     "here and for nothing else"),
]


def _nested_repos():
    """Product repo directory names, from the facet vocabulary - never hardcoded here.

    The roster is this workspace's own vocabulary. A copy of it in code is a second place to add
    a repo, and the day the two disagree the index is lying about its own coverage.
    """
    import json
    try:
        raw = json.loads((pathlib.Path(__file__).resolve().parent
                          / "facets_vocabulary.json").read_text(encoding="utf-8"))
        return [n for n in raw.get("nested_repos", []) if n]
    except Exception:
        return []


def _nested_repo_root(name):
    """Where product repo `name` is on disk, or None.
    """
    for base in (WS, MAIN_CHECKOUT):
        r = base / name
        if r.is_dir():
            return r
    return None


_PRUNED = []


# Working directories this workspace names by hand. The public copy names none: its user lists
# their own with `orchdoc_config.py set doc_folders a,b`, read by _configured_doc_folders().
WORKING_DIRS = ()


def _configured_doc_folders():
    """The folders the user named in the `doc_folders` setting; [] when it is unset."""
    try:
        import orchdoc_config
    except ImportError:
        return []
    return orchdoc_config.get_list("doc_folders", ())


def _walk_md(root, prune=PRUNE):
    """Every .md under `root`, with dependency + build trees pruned during the walk."""
    out = []
    for dirpath, dirnames, names in os.walk(root):
        keep = [d for d in dirnames if d not in prune]
        _PRUNED.extend(os.path.join(dirpath, d) for d in dirnames if d in prune)
        dirnames[:] = keep
        for n in names:
            if n.endswith(".md"):
                out.append(pathlib.Path(dirpath) / n)
    return out




_CACHE = None


def stores():
    """[(kind, path)] - every artifact the covered stores hold, BEFORE any exclusion.

    kind is the STORE the artifact came from rather than a guess about its content: consumers
    weight signals per store.
    """
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    del _PRUNED[:]
    out, seen = [], set()

    def add(kind, p):
        k = str(p).lower()
        if k in seen:
            return
        seen.add(k)
        out.append((kind, p))

    for p in MEM.rglob("*.md"):
        add("memory", p)

    for p in (WS / ".shared" / "scripts").rglob("*"):
        if p.suffix in (".py", ".sh", ".mjs"):
            add("script", p)

    for p in WS.glob("*.md"):
        add("doc", p)
    for sub in ("docs", ".shared/docs"):
        d = WS / sub
        if d.exists():
            for p in _walk_md(d):
                add("doc", p)

    for sub in WORKING_DIRS + tuple(_configured_doc_folders()):
        d = WS / sub
        if d.exists():
            for p in _walk_md(d):
                add("doc", p)


    for root in SKILL_ROOTS:
        if root.exists():
            for p in sorted(root.glob("*/SKILL.md")):
                add("skill", p)

    for name in _nested_repos():
        r = _nested_repo_root(name)
        if r is None:
            continue
        for p in r.glob("*.md"):
            add("repo", p)
        if (r / "docs").is_dir():
            for p in _walk_md(r / "docs"):
                add("repo", p)

    _CACHE = sorted(out, key=lambda kp: str(kp[1]).lower())
    return _CACHE


def files(include_generated=True):
    """[(kind, path)] - the corpus: every enumerated artifact no exclusion rule claims.
    """
    o = {"generated": include_generated}
    return [(k, p) for k, p in stores()
            if not any(r.match(p, o) for r in EXCLUSIONS)]


def excluded(include_generated=True):
    """(enumerated, {rule name: [paths]}) - what each rule actually removed.

    First match wins, so every dropped artifact is attributed to exactly one rule and the
    counts sum. That is what lets selftest() assert kept + excluded == enumerated.
    """
    o = {"generated": include_generated}
    hits = collections.OrderedDict((r.name, []) for r in EXCLUSIONS)
    n = 0
    for _kind, p in stores():
        n += 1
        for r in EXCLUSIONS:
            if r.match(p, o):
                hits[r.name].append(p)
                break
    return n, hits


def counts(**kw):
    c = {}
    for kind, _p in files(**kw):
        c[kind] = c.get(kind, 0) + 1
    return c


def _rel(p):
    for root in (WS, MEM, pathlib.Path.home()):
        try:
            return str(p.relative_to(root)).replace("\\", "/")
        except ValueError:
            continue
    return str(p)


def _print_exclusions(include_generated):
    n, hits = excluded(include_generated)
    _, other = excluded(not include_generated)
    kept = n - sum(len(v) for v in hits.values())
    print("  EXCLUDED - what each rule REMOVED from the %d enumerated (a rule eating 184 files"
          % n)
    print("  and a corpus that never held them must not print the same line):")
    print()
    for r in EXCLUSIONS:
        paths = hits[r.name]
        share = (100.0 * len(paths) / n) if n else 0.0
        flag = " BROAD" if share > BROAD_FLOOR * 100 else ""
        print("    %-42s %5d  %5.1f%%%s" % (r.name[:42], len(paths), share, flag))
        for line in textwrap.wrap(r.reason, 84):
            print("        %s" % line)
        if not paths and other[r.name]:
            print("        INACTIVE in this slice - removes %d when include_generated=%s"
                  % (len(other[r.name]), not include_generated))
        for p in paths[:3]:
            print("        e.g. %s" % _rel(p))
        if len(paths) > 3:
            print("        ... and %d more" % (len(paths) - 3))
        print()
    print("    %-42s %5d" % ("kept (the corpus)", kept))
    print()
    print("  BOUNDS ON THE WALK - not per-file counts, because these files are never")
    print("  enumerated. Saying so beats inventing a number:")
    print()
    print("    %-42s %5d directories (see note)"
          % ("build + dependency trees pruned", len(set(_PRUNED))))
    for _name, why in SCOPE_BOUNDS:
        for line in textwrap.wrap(why, 84):
            print("        %s" % line)
    return n, hits, kept


def selftest():
    ok = True

    def t(label, cond):
        nonlocal ok
        print("  [%s] %s" % ("OK " if cond else "FAIL", label))
        ok &= bool(cond)

    print("corpus selftest")
    n, hits = excluded()
    kept = len(files())
    dropped = sum(len(v) for v in hits.values())

    t("kept + excluded == enumerated (%d + %d == %d) - no silent drops"
      % (kept, dropped, n), kept + dropped == n)

    for r in EXCLUSIONS:
        share = len(hits[r.name]) / float(n or 1)
        if share > BROAD_FLOOR:
            t("'%s' removes %.0f%% and SAYS SO ('%s' in its reason)"
              % (r.name, share * 100, BROAD_TOKEN), BROAD_TOKEN in r.reason.lower())
        else:
            t("'%s' removes %.1f%% - under the %.0f%% floor"
              % (r.name, share * 100, BROAD_FLOOR * 100), True)

    t("every rule states a reason", all(len(r.reason) > 40 for r in EXCLUSIONS))
    t("no captured note survives into the corpus",
      not [p for _k, p in files() if "captured_notes" in p.parts])
    t("a nested repo's CLAUDE.md is IN the corpus (the front door of that repo)",
      any(p.name == "CLAUDE.md" for _k, p in files()))
    t("a nested repo's own docs/knowledge is IN the corpus (23 reference documents)",
      any("knowledge" in p.parts and "repo_2" in p.parts
          for _k, p in files(include_generated=False)))
    t("...while OUR generated pages are still dropped for consumers that ask",
      not [p for _k, p in files(include_generated=False)
           if str(p).lower().startswith(str(WS / "docs" / "knowledge").lower())])
    t("include_logs is gone - a slice a session could switch on by accident",
      "include_logs" not in files.__code__.co_varnames)

    print("\nselftest %s" % ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--paths", action="store_true")
    ap.add_argument("--no-generated", action="store_true",
                    help="the slice the knowledge map and the candidate sheet read")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    gen = not a.no_generated
    fs = files(include_generated=gen)
    if a.paths:
        for _k, p in fs:
            print(p)
        return 0
    print("corpus - one enumeration, read by route.py, knowledge_pages.py and the candidate sheet")
    print()
    for k, v in sorted(counts(include_generated=gen).items()):
        print("  %-8s %5d" % (k, v))
    print("  %-8s %5d" % ("TOTAL", len(fs)))
    print()
    print("  minus our generated pages: %d" % len(files(include_generated=False)))
    print()
    _print_exclusions(gen)
    repos = _nested_repos()
    print()
    print("  nested product repos enumerated (%d): %s"
          % (len([r for r in repos if (WS / r).is_dir()]), ", ".join(repos) or "none declared"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
