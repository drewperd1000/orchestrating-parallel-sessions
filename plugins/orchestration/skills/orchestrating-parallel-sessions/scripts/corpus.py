#!/usr/bin/env python3
"""ONE enumeration of what this workspace knows. Every consumer reads it from here.

<!-- route-tags: corpus enumeration stores scan set coverage harvest artifacts nested repos knowledge exclusions voice notes -->
<!-- cornerstone: knowledge-routing -->

⛔ THE DEFECT THIS EXISTS TO KILL: TWO ANSWERS TO "WHAT IS THE CORPUS?"
`route.py` and `knowledge_pages.py` each enumerated the workspace independently. Measured
2026-08-17: route reached 1,326 artifacts and the knowledge map reached 198 - and nobody could
see the gap, because each tool printed a confident count of its own set. The cornerstone
candidate sheet was built from the smaller one and was therefore judging 15% of the corpus while
reading as a survey of all of it. The human: *"the referenced docs were only just barely better than
randomly assigned."*

⭐ SO THE SCAN SET IS DATA, NOT CODE-PER-TOOL. One function enumerates; every drop is a named
rule in `EXCLUSIONS`, and every rule REPORTS WHAT IT REMOVED. A disagreement between two tools
is now impossible to have by accident - it can only be written down, where a reader sees it.

⛔ AN EXCLUSION IS A MEASUREMENT, NOT AN ASSERTION (2026-08-17, this file's second pass).
`corpus.py` used to print a list of exclusion NAMES with no counts. A rule eating 184 files and
a corpus that never contained them printed the identical line. That is the same conflation as
"0 examined" vs "3 examined, all clean" - the defect this whole workstream keeps finding, here
turned on what was NOT examined. So every rule now prints its count and a sample, and
`--selftest` asserts that kept + excluded == enumerated, so nothing can leave the corpus without
appearing in that table.

⛔ AND THE REASON DECIDES THE SHAPE OF THE MATCHER. Twice now a rule whose reason was about a
LOCATION was written as a match on a NAME, and both times it over-reached the moment a second
place used that name:

  1. `name == "CLAUDE.md"`      hid `repo_1/CLAUDE.md`, the front door to repo_1,
                               which the human asked for by name and was told did not exist.
  2. `parent.name == "knowledge"`  hid `repo_2/docs/knowledge/` - 23 hand-authored
                               reference documents (authority_source_1, authority_source_2, authority_source_3, authority_source_4) -
                               under a rule written for OUR generated pages at `docs/knowledge`.
                               The human ruled `copywriting` "NONE - none of these fit" against a
                               sheet that could not see them.

⭐ A PATH LIST CANNOT GROW ITS OWN BLAST RADIUS. A name pattern silently does, every time the
corpus grows - and it grows invisibly, because the rule keeps printing the same line while
eating more. So: where the reason is about WHERE a file lives, the matcher is a path list. Where
the reason is genuinely about the FILE (a snapshot suffix, an orchestrator's state doc), a name
rule is correct and says so.

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
         ".svelte-kit", "vendor", ".codesight"}


# --------------------------------------------------------------------------------------------
# The exclusion rules. (name, reason, matcher) - never a bare pattern.
# --------------------------------------------------------------------------------------------

Exclusion = collections.namedtuple("Exclusion", "name reason match")

# A rule removing more than this share of the enumerated set must SAY SO in its reason, with the
# `broad:` token. See selftest(). The point is not to forbid a big rule - voice notes are 56% of
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
        "voice notes",
        "broad: raw capture, not yet processed into knowledge - the human, 2026-08-17: \"'voice "
        "notes' should NEVER be considered in this process. They are random URLs and thought "
        "captures. They could eventually be included in the knowledge base and routing, but "
        "not right now, and not until after they are eventually properly processed.\" This "
        "row IS the record that they exist and are deliberately deferred; deleting it to "
        "shorten the table would turn a decision back into a gap.",
        _under(MEM / "voice_notes")),

    Exclusion(
        "our generated knowledge pages",
        "views OF other artifacts - route to the sources, never to the middleman. ONE PATH, "
        "not any directory named 'knowledge': the name form of this rule was silently eating "
        "repo_2/docs/knowledge, which is 23 hand-authored reference documents. "
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

# Bounds on the WALK ITSELF, which cannot be reported as a per-file count because the files are
# never enumerated - that is the whole point of pruning. Reported separately and honestly rather
# than given a fake number, and the directory count IS measured so the row is not just a claim.
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

    # ⛔ THE UNDECLARED ONE, and it was the biggest. Found by o1, 2026-09-08, from a case that
    # shows the cost exactly: they ran `route.py enrich` on a file, wrote its tags, and the
    # router still would not return it on its most distinctive term. Tags written, behaviour
    # unchanged - because the file was never in the corpus to begin with.
    #
    # ⭐ THE STORE IS `WS.glob("*.md")` - NON-RECURSIVE - plus `docs` and `.shared/docs`. So a
    # markdown file in ANY other workspace subdirectory is structurally unreachable. Measured
    # 2026-09-08 over tracked files, worktrees excluded: 136 .md at the root against 484 in
    # subdirectories, of which only docs (28) is covered. The router reaches roughly a QUARTER
    # of this workspace's tracked markdown.
    #
    # ⛔ WHY THE SILENCE WAS THE DEFECT RATHER THAN THE SCOPE. The scope may well be right -
    # working_corpus_o5 alone is 173 files of a retired orchestrator's working notes, and
    # flooding every query with process logs would make the router useless. But the receipt
    # named six exclusions and omitted several hundred files it could not reach, so a VERIFIED
    # MISS was a claim about a corpus the asker could not see the edges of. This file's own
    # voice-notes row states the principle: a declared exclusion is a DECISION, an undeclared
    # one is a gap wearing a decision's clothes.
    #
    # ⚠️ WHETHER TO INDEX THEM IS OPEN, and it is not a code question. Two of these directories
    # hold material the human intends to train on - working_corpus_o8 (84) and the AVATAR-LANG
    # primary source in research_corpus_1 (52) - and o1 missed that second
    # corpus writing a lane seed, finding it only by checking someone else's incorrect warning.
    # Searching could not have found it. Raised as a decision rather than patched.
    ("workspace subdirectories not walked at all",
     "the doc store is WS/*.md NON-RECURSIVE plus docs/ and .shared/docs/, so markdown inside "
     "any other workspace subdirectory - orchestration working directories, research corpora, "
     "deliverable sets - is outside the corpus. Measured 2026-09-08: 484 tracked .md in "
     "subdirectories against 136 at the root. A VERIFIED MISS is verified for the stores named "
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

    The product repos are gitignored siblings that exist once per machine, in the MAIN checkout.
    A worktree has none (W233, 2026-10-21): `WS / name` alone dropped repo_1 from the
    corpus of every session run from a worktree, so the `repo_1 meta ads manager` fixture
    failed there while it passed in the main checkout. WS first (a test sandbox or a clone that
    carries its own copy wins), then the main checkout.
    """
    for base in (WS, MAIN_CHECKOUT):
        r = base / name
        if r.is_dir():
            return r
    return None


_PRUNED = []


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


# o10L321 (W235 step 5): the 30 o3 drafts of origin BSS were `.txt` files, which this `.md` corpus never held;
# the rename gave them `.md` names, which would have added 30 files to the corpus. They stay out, so the
# rename changes no count (258 library files before and after). Whether they belong in the corpus is a
# separate decision for o10 (listed in the o10L321 report).
_TXT_BEFORE_RENAME = ("BSS",)
_IN_APP_NAME = re.compile(r" \(in app\)\.md\Z")
_DREW_NAME = re.compile(r" v[1-9]\d* THE HUMAN(?: duplicate)?\.md\Z")


def _library_origin(p):
    """training_header.library_origin(p), or None when training_header is not installed.

    o9:W144: the published copy does not ship training_header (workspace-specific script
    library logic), so the import is optional. Without it no file has an origin, which leaves
    the script-library part of the corpus empty. Locally it is always present.

    Only training_header itself being missing counts (o9:W144 review, lane 1403): an
    ImportError raised by one of ITS imports is a real fault and must not empty the script
    library locally without a word, so it propagates."""
    try:
        import training_header
    except ImportError as e:
        if e.name != "training_header":
            raise
        return None
    return training_header.library_origin(p)


def _file_script():
    """The file_script module, or None when it (or training_header, which it imports) is not installed.

    o10L604 (fix F1, review o10L600): as with _library_origin, only those two modules being missing
    counts (the published copy ships neither); an ImportError raised by one of THEIR imports is a
    real fault and propagates."""
    try:
        import file_script
    except ImportError as e:
        if e.name not in ("file_script", "training_header"):
            raise
        return None
    return file_script


def _live_text_names(d):
    """Names of the files in script folder `d` that are its live text: the `(in app)` named files whose
    header says `## App: live` (the old rule), plus the folder's record file when ITS header says
    `## App: live` (o10L604, fix F1).

    `drop-app-tags` renames the 411 withheld drafts so that they lose ` (in app)`; they stay `App: live`
    in their record file's header. The corpus must keep excluding exactly what it excluded with the tag, so
    a live folder is recognised by that header, read with file_script's own parser, not by the file name."""
    names = set()
    try:
        listing = os.listdir(d)
    except OSError:
        listing = []
    for n in listing:
        if _IN_APP_NAME.search(n) and (pathlib.Path(d) / n).is_file() and _header_says_live(pathlib.Path(d) / n):
            names.add(n)
    # A flat `versions/` folder holds old texts (a THE HUMAN rewrite there carries `App: live` and a Stage line
    # too); the record file belongs to the script folder above it, never to versions/ itself.
    fs = None if pathlib.Path(d).name == "versions" else _file_script()
    if fs is not None:
        rec = fs.record_file(d)
        if rec is not None:
            try:
                app = fs.parse_script(rec)[0].get("App", "")
            except (OSError, ValueError, fs.LibraryError):
                app = ""
            if app.strip().lower() == "live":
                names.add(rec.name)
    return names


def _keep_from_live_folder(p):
    """True for the two shapes of a live script's folder that stay in the corpus.

    o10L321 (W235 step 5, review o10L318 B3). The rename put every library text into the shape
    `<NAME> v<N> <AUTHOR>[ (in app)].md` in a flat `versions/`, so the old test on a `versions/PIL/`
    folder and on a `...rewrite.md` name no longer finds anything. The same two shapes now read:
    a pilot file is a file whose origin is PIL (its `## Origin:` line, else the frozen table of logs and notes;
    training_header.library_origin), and a the human rewrite is a `<NAME> v<N> THE HUMAN.md` file or an old
    `...rewrite.md` name. The text in the app (`(in app)`) and every other origin stay out. A THE HUMAN file of
    another origin (FCI: the human's final of the app's own text, versions/FCI) stays out, as the old rule did."""
    if _IN_APP_NAME.search(p.name) or p.name in _live_text_names(p.parent):
        return False
    origin = _library_origin(p)
    if _is_pilot_file(p):
        return True
    if origin and _DREW_NAME.search(p.name):
        return False
    return bool(_DREW_NAME.search(p.name) or p.stem.casefold().endswith("rewrite"))


def _kept_script_library_md(stage_dir):
    """The .md files of one script-library stage folder that belong in the knowledge corpus.

    o10L311 (W235 step 4 review, defect 3). The app's own scripts - a folder whose live text says
    `## App: live` - are not knowledge-index material, in ANY stage folder: the folder contributes
    only its pilot files and the human's rewrite files (_keep_from_live_folder), the same two shapes the
    3-final rule below keeps. Its text in the app and the other version files (FVA, FVS, FCS, FVN,
    FCI, ...) stay out. Before the step-4 move those lived under 3-final/ and the stage folders held
    only non-live drafts; the move put 424 live scripts into 1-drafts/ and this rule keeps them out
    as 3-final did.
    A folder with no live text, or one whose header does not say live, contributes every file.
    A folder's live text is its `<NAME> v<N> <AUTHOR> (in app).md` (o10L321; o10L356 step 7: no `SCRIPT.md` is read). Keyed on the folder's own header, not on its stage folder. Local header reader
    (same test as check_script.own_header_says_live and reader_scripts.header_says_live).
    """
    live_cache = {}

    def live_text_says_live(d):
        return bool(_live_text_names(d))

    def live_folder(d):
        # nearest ancestor (up to stage_dir) holding a live text that says live, else None
        if d in live_cache:
            return live_cache[d]
        res = None
        if live_text_says_live(d):
            res = d
        elif d != stage_dir and stage_dir in d.parents:
            res = live_folder(d.parent)
        live_cache[d] = res
        return res

    out = []
    for p in _walk_md(stage_dir):
        if _library_origin(p) in _TXT_BEFORE_RENAME:
            continue
        if live_folder(p.parent) is not None and not _keep_from_live_folder(p):
            continue
        out.append(p)
    return out


_APP_LIVE_RE = re.compile(r"^[ \t]*#{1,6}[ \t]*App[ \t]*:[ \t]*live[ \t\r]*$", re.I | re.M)


def _header_says_live(path):
    """True when the file's header (up to `<!-- END SCRIPT HEADER -->`, or its first 8 KB) has a
    `## App: live` line. Unreadable file: False."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(8192).decode("utf-8", "replace")
    except (OSError, ValueError):
        return False
    cut = head.find("<!-- END SCRIPT HEADER -->")
    if cut >= 0:
        head = head[:cut]
    return bool(_APP_LIVE_RE.search(head))


def _is_live_text_name(name):
    """A script folder's live text: `<NAME> v<N> <AUTHOR> (in app).md`."""
    return bool(_IN_APP_NAME.search(name))


def _is_pilot_file(p):
    """A pilot file: origin PIL (its `## Origin:` line, or the frozen table for a log or note)."""
    return _library_origin(p) == "PIL"


def _version_file_folder(p):
    """The script folder of a file kept in the flat `versions/` folder, else None."""
    if p.parent.name == "versions":
        return p.parent.parent
    return None


def _folder_is_live(d):
    """True when the script folder `d` holds a live text whose header says `## App: live`."""
    return bool(_live_text_names(d))


_CACHE = None


def stores():
    """[(kind, path)] - every artifact the covered stores hold, BEFORE any exclusion.

    ⛔ SEPARATED FROM files() ON PURPOSE. While the exclusions were applied inside the walk,
    "this rule removed 184 files" and "the corpus never held them" were the same observation,
    and neither was written down. The enumeration is now the fixed point both `files()` and
    `excluded()` are computed from, which is what makes the table in `main()` a MEASUREMENT.

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

    # S1 - memory notes, recursive. memory/automation/** holds the sweep + briefing runbooks and
    # was silently unharvested until 2026-08-12 while the receipt claimed the store was covered.
    for p in MEM.rglob("*.md"):
        add("memory", p)

    # S2 - shared scripts, recursive
    for p in (WS / ".shared" / "scripts").rglob("*"):
        if p.suffix in (".py", ".sh", ".mjs"):
            add("script", p)

    # S3 - docs: workspace root, docs/**, .shared/docs/**
    for p in WS.glob("*.md"):
        add("doc", p)
    for sub in ("docs", ".shared/docs"):
        d = WS / sub
        if d.exists():
            for p in _walk_md(d):
                add("doc", p)

    # ⭐ S3b - THE TWO RESEARCH CORPORA. The human ruled D17 on 2026-09-08: "index the two research
    # corpora, leave o5's scratch out."
    #
    # The doc store above is WS/*.md NON-RECURSIVE, so every working directory was outside the
    # corpus - 456 tracked .md against 136 at the root, undeclared until 2026-09-08 (F115). o1
    # found it the expensive way: they ran `route.py enrich` on a file, the tags were written,
    # and the router still would not return it, because the file had never been in the corpus.
    #
    # ⛔ NOT "index the subdirectories". The scope is a JUDGEMENT about what belongs in a
    # knowledge index, and the human made it per-directory:
    #   IN   research_corpus_1  the AVATAR-LANG primary source, ~1,000
    #        quotes across 23 files. o1 missed it writing a lane seed and found it only by
    #        checking someone else's incorrect warning.
    #   IN   working_corpus_o8                       the pilots and rewrite pairs
    #   OUT  working_corpus_o5                      173 files of a RETIRED orchestrator's
    #        working notes; process logs would flood every query
    #
    # ⚠️ A NAMED LIST, not a pattern, and deliberately so. A rule like "any research directory"
    # would silently adopt the next working directory somebody creates, which is how the doc
    # store came to exclude 456 files without anyone deciding to. Adding one is one line, and
    # the line is the decision.
    # o10:W226 (lane o10L251): scripts that left those two folders for script-library/ are still the
    # same drafts, so the three folders that now hold them are named here. NOT 3-final: that is the
    # app's own scripts, which were never in this corpus.
    # o10L311: the step-4 move put the app's 424 live scripts (`## App: live`) into 1-drafts/, so
    # the three script-library folders go through _kept_script_library_md(): a live script's
    # folder contributes only its pilot and the human-rewrite files, as 3-final does below.
    for sub in ("research_corpus_1", "working_corpus_o8",
                "script-library/1-drafts", "script-library/2-ready-for-owner", "script-library/4-retired"):
        d = WS / sub
        if d.exists():
            for p in (_kept_script_library_md(d) if sub.startswith("script-library/") else _walk_md(d)):
                add("doc", p)

    # o9L1145 (o10 review of batch 2): the pilot scripts whose script folder is now under 3-final/
    # moved there too, so they were in this corpus before the move (as working_corpus_o8/pilots)
    # and fell out of it by it. 3-final/ stays out as a whole - the `(in app)` texts and the versions/FVA,
    # FVS, FCS, FVN files are the app's own scripts. Only the moved pilot files come back, by the
    # two shapes the move gave them: anything under a `versions/PIL/` folder, and the the human
    # rewrite files (`...rewrite.md`) beside it. A NAMED RULE, not a pattern over 3-final.
    d = WS / "script-library" / "3-final"
    if d.exists():
        for p in _walk_md(d):
            if _keep_from_live_folder(p):
                add("doc", p)

    # S4 - first-party skills
    for root in SKILL_ROOTS:
        if root.exists():
            for p in sorted(root.glob("*/SKILL.md")):
                add("skill", p)

    # S5 - nested product repos, BOUNDED. Their absence was route.py's sixth undeclared store
    # until it declared it, and declaring it did not make `repo_1` findable - the repo holding
    # every answer about that product was still never opened.
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

    ⛔ THERE IS NO `include_logs`. Voice notes were an optional slice until 2026-08-17, when
    the human ruled them out of this process entirely: an option is a thing a future session can
    switch on by accident, and 850 raw captures outvote the authored corpus 1.3:1. The
    exclusion stays VISIBLE in the table `main()` prints, which is what keeps it a decision
    rather than a gap.
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
    # ⭐ A CONDITIONAL RULE MUST NOT READ AS A DEAD ONE. `our generated knowledge pages` removes
    # nothing in the wide slice and 18 in the narrow one; printing a bare 0 for it would say
    # "this rule does nothing", which is the same false reassurance as an uncounted exclusion.
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
    # Measured, not asserted - and it currently measures ZERO, which is itself the finding:
    # the scope bounds below keep the walk out of the trees PRUNE was written for, so the set
    # is a guard against a future store being added, not a thing that fires today.
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

    # ⭐ THE LOAD-BEARING ONE. Anything that leaves the corpus without being claimed by a named
    # rule is invisible by construction - it would print nowhere and nobody could ask about it.
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
    t("no voice note survives into the corpus",
      not [p for _k, p in files() if "voice_notes" in p.parts])
    # ⛔ THE REGRESSION THAT SHIPPED TWICE. Both were a location reason written as a name match.
    t("a nested repo's CLAUDE.md is IN the corpus (the repo_1 front door)",
      any(p.name == "CLAUDE.md" for _k, p in files()))
    t("a nested repo's own docs/knowledge is IN the corpus (23 reference documents)",
      any("knowledge" in p.parts and "repo_2" in p.parts
          for _k, p in files(include_generated=False)))
    t("...while OUR generated pages are still dropped for consumers that ask",
      not [p for _k, p in files(include_generated=False)
           if str(p).lower().startswith(str(WS / "docs" / "knowledge").lower())])
    t("the moved pilot files under 3-final/ are IN the corpus (o9L1145)",
      any("3-final" in p.parts and _is_pilot_file(p) for _k, p in files()))
    t("...while the app's own text under 3-final/ (`(in app)`) is still out",
      not [p for _k, p in files() if "3-final" in p.parts and _is_live_text_name(p.name)])
    # o10L311 (step-4 review, defect 3): the app's live scripts are out in EVERY stage folder.
    sl = WS / "script-library"
    t("no `App: live` text (`(in app)`) is in the corpus, in any stage folder",
      not [p for _k, p in files()
           if sl in p.parents and _is_live_text_name(p.name) and _header_says_live(p)])
    t("no live script's app version file (versions/FVA, FVS, FCS, FCI, SCRI...) is in the corpus",
      not [p for _k, p in files()
           if sl in p.parents and _version_file_folder(p) is not None and not _is_pilot_file(p)
           and not _DREW_NAME.search(p.name) and _folder_is_live(_version_file_folder(p))])
    t("a non-live draft's text is IN the corpus (the not-yet-placed drafts)",
      any(sl in p.parents and _is_live_text_name(p.name) and not _header_says_live(p) for _k, p in files())
      or any(sl in p.parents and _version_file_folder(p) is not None
             and not _folder_is_live(_version_file_folder(p)) for _k, p in files())
      or not (sl / "1-drafts" / "not-yet-placed").exists())
    t("a pilot file inside a live folder is IN the corpus",
      any(sl in p.parents and _is_pilot_file(p) and _version_file_folder(p) is not None
          and _folder_is_live(_version_file_folder(p)) for _k, p in files()))
    # The rule itself, on a temp tree (it must not depend on what the real library holds today).
    import tempfile
    tmp = pathlib.Path(tempfile.mkdtemp())
    live_hdr = "## Title: T\n## App: live\n<!-- END SCRIPT HEADER -->\nbody\n"
    draft_hdr = "## Title: T\n## Stage: draft\n<!-- END SCRIPT HEADER -->\nbody\n"
    def mk(rel, text):
        f = tmp / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8", newline="")
        return f
    mk("sleep/L1/L1 v2 o5 (in app).md", live_hdr)
    mk("sleep/L1/versions/L1 v1 o8.md", "## Title: T\n## Origin: PIL\n<!-- END SCRIPT HEADER -->\npilot\n")
    mk("sleep/L1/versions/L1 v3 o5.md", "## Title: T\n## Origin: FVS\n<!-- END SCRIPT HEADER -->\nfvs\n")
    mk("sleep/L1/Some - the human rewrite.md", "rewrite")
    mk("sleep/L3/L3 v2 o5 (in app).md", live_hdr.replace("\n", "\r\n"))
    mk("sleep/L4/notes.md", "no record file in this folder")
    mk("sleep/L5/L5 v1 agent (in app).md", "## Title: T\n<!-- END SCRIPT HEADER -->\n## App: live\n")
    # Step 5 layout (o10L321): `<NAME> v<N> <AUTHOR>[ (in app)].md`, a flat versions/, origin in the header.
    hdr = "## Title: T\n## Origin: %s\n<!-- END SCRIPT HEADER -->\nbody\n"
    mk("sleep/N1/N1 v4 o5 (in app).md", live_hdr)
    mk("sleep/N1/versions/N1 v1 o8.md", hdr % "PIL")
    mk("sleep/N1/versions/N1 v2 o5.md", hdr % "FCS")
    mk("sleep/N1/versions/N1 v3 o3.md", hdr % "BSS")
    mk("sleep/N1/N1 v5 THE HUMAN.md", "a the human rewrite")
    mk("sleep/N1/versions/N1 v6 THE HUMAN.md", hdr % "FCI")
    mk("sleep/N2/N2 v2 o8.md", draft_hdr)
    mk("sleep/N2/versions/N2 v1 o8.md", hdr % "PIL")
    mk("sleep/N3/versions/N3 v1 o8.md", hdr % "FCS")
    kept = sorted(str(p.relative_to(tmp)).replace(os.sep, "/") for p in _kept_script_library_md(tmp))
    t("temp tree, step 5 names: a live folder keeps its pilot file and its the human rewrite only",
      [k for k in kept if k.startswith("sleep/N1/")] == ["sleep/N1/N1 v5 THE HUMAN.md", "sleep/N1/versions/N1 v1 o8.md"])
    t("temp tree, step 5 names: a draft folder keeps every file except an origin kept out (BSS)",
      [k for k in kept if k.startswith("sleep/N2/") or k.startswith("sleep/N3/")] ==
      ["sleep/N2/N2 v2 o8.md", "sleep/N2/versions/N2 v1 o8.md", "sleep/N3/versions/N3 v1 o8.md"])
    t("temp tree: a live folder keeps only its pilot file (origin PIL) and the rewrite file",
      [k for k in kept if k.startswith("sleep/L1/")] ==
      ["sleep/L1/Some - the human rewrite.md", "sleep/L1/versions/L1 v1 o8.md"])
    t("temp tree: a CRLF live header is still live", not [k for k in kept if k.startswith("sleep/L3/")])
    t("temp tree: a folder with no record file keeps its files", "sleep/L4/notes.md" in kept)
    t("temp tree: an App line below the header end marker does not make a folder live",
      "sleep/L5/L5 v1 agent (in app).md" in kept)
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
