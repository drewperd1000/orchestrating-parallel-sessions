#!/usr/bin/env python3
"""Gate 2: a knowledge doc entering the workspace must be REACHABLE, or say it isn't.

<!-- subject: knowledge-routing -->

<!-- route-tags: knowledge gate registration tombstone superseded reachable index dev-docs -->

A document nothing links to might as well not exist: a session searches for it, finds nothing and
reports that none exists, and a decision recorded inside it gets raised again as an open question.

  1. a row in DEV-DOCS-INDEX.md naming the file       (forward-looking / planned work)
  2. a `<!-- route-tags: ... -->` line in the doc      (routable by route.py)
  3. a `<!-- unindexed: <reason> -->` marker           (an honest, stated opt-out)

What is CHECKED, because a pointer that rots is worse than none: the successor path must
exist, and must not itself be a tombstone (no chains, no cycles).

    knowledge_gate.py --check     pre-commit: staged ADDED docs only
    knowledge_gate.py --audit     whole tree: what is examined, what is unreachable
    knowledge_gate.py --install   install/extend the pre-commit hook
    knowledge_gate.py --selftest

Fails OPEN on any internal error: a broken guard must never block a commit. ASCII output.
"""

import os
import re
import subprocess
try:
    from no_window import flags
except ImportError:  # a partial delivery must not take the tool down
    def flags(extra=0):
        return extra
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workspace_paths import WS, MEM  # noqa: E402


def _root():
    """The repo THIS commit is happening in - not a hardcoded path.
    """
    try:
        p = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=10, creationflags=flags())
        if p.returncode == 0 and p.stdout.strip():
            return Path(p.stdout.strip())
    except Exception:
        pass
    return WS


ROOT = _root()
INDEX = ROOT / "DEV-DOCS-INDEX.md"

# --- the contract. Shape, not prose. Imported by anything that reads or writes a tombstone. --
TOMBSTONE_OPEN = re.compile(
    r"<!--\s*tombstone:\s*superseded-by=(?P<path>\S+?)\s+date=(?P<date>\d{4}-\d{2}-\d{2})"
    r"\s+by=(?P<by>\S+?)\s*-->")
TOMBSTONE_CLOSE = "<!-- /tombstone -->"
from mentions import ROUTE_TAGS  # noqa: E402 - ONE definition
UNINDEXED = re.compile(r"<!--\s*unindexed:\s*(.+?)\s*-->")
BACKPOINTER = re.compile(r"Indexed in\s*\[?DEV-DOCS-INDEX", re.I)


def tombstone_header(superseded_by, date, by, title, why):
    """The ONE writer of the shape, so no session hand-rolls a near-miss."""
    return (
        "<!-- tombstone: superseded-by=%s date=%s by=%s -->\n"
        "# SUPERSEDED - this document is no longer current\n\n"
        "**Current home:** [%s](%s)\n\n"
        "%s\n\n"
        "%s\n\n"
        "> The original text is kept below unchanged, on purpose: it is what makes this\n"
        "> tombstone findable by someone searching for the old thing.\n\n"
        "---\n" % (superseded_by, date, by, title, superseded_by, why, TOMBSTONE_CLOSE))


EPHEMERAL = re.compile(r"(-SEED|-PROMPT|-bootstrap|-bridge|-PREVIEW|-SKILL-DRAFT)\.md$", re.I)
NEVER_GATE = {"CLAUDE.md",
              "DEV-DOCS-INDEX.md",
              "ORCHESTRATOR-REGISTRY.md",
              "README.md",
              "MEMORY.md"}


def git(args, cwd=None):
    try:
        p = subprocess.run(["git", "-C", str(cwd or ROOT)] + list(args),
                           capture_output=True, text=True, timeout=20, creationflags=flags())
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except Exception:
        return 1, "", ""


def in_knowledge_path(rel):
    rel = rel.replace("\\", "/")
    if not rel.endswith(".md"):
        return False
    name = rel.rsplit("/", 1)[-1]
    if name in NEVER_GATE or name.startswith("ORCHESTRATOR-DECISIONS-"):
        return False
    if EPHEMERAL.search(name):
        return False
    if "/" not in rel:                       # workspace root
        return True
    return rel.startswith("docs/")           # workspace docs tree


def false_badge(rel, text, index_text):
    """Does this doc CLAIM an index row it does not have?"""
    name = rel.replace("\\", "/").rsplit("/", 1)[-1]
    if name in NEVER_GATE:
        return False
    return bool(BACKPOINTER.search(text)) and name not in index_text


def reachable(rel, text, index_text):
    """(ok, door) - which of the three doors this doc came through, if any."""
    name = rel.replace("\\", "/").rsplit("/", 1)[-1]
    if name in index_text or rel.replace("\\", "/") in index_text:
        return True, "DEV-DOCS-INDEX row"
    try:
        from knowledge_pages import MARKER_RE as _OWNER_RE
    except Exception:
        _OWNER_RE = re.compile(r"<!--\s*(?:cornerstone|subject):\s*[a-z0-9-]+\s*-->")
    if _OWNER_RE.search(text):
        return True, "cornerstone declaration"
    if ROUTE_TAGS.search(text):
        return True, "route-tags"
    m = UNINDEXED.search(text)
    if m:
        return True, "declared unindexed (%s)" % m.group(1)[:40]
    return False, None


def check_tombstones(paths):
    """[(path, problem)] - a pointer that rots is worse than no pointer."""
    bad = []
    for p in paths:
        try:
            t = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = TOMBSTONE_OPEN.search(t)
        if not m:
            continue
        if TOMBSTONE_CLOSE not in t:
            bad.append((p, "tombstone is never closed with %s" % TOMBSTONE_CLOSE))
            continue
        target = m.group("path")
        tp = Path(target) if os.path.isabs(target) else (WS / target)
        if not tp.exists():
            bad.append((p, "superseded-by points at a path that does not exist: %s" % target))
            continue
        try:
            if TOMBSTONE_OPEN.search(tp.read_text(encoding="utf-8", errors="replace")):
                bad.append((p, "superseded-by points at ANOTHER tombstone: %s" % target))
        except OSError:
            pass
    return bad


def _malformed_marker(rel):
    """Does this doc ATTEMPT a marker the reader cannot parse? Returns the offending line.
    """
    try:
        t = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for word, rx in (("route-tags", ROUTE_TAGS), ("unindexed", UNINDEXED),
                     ("tombstone", TOMBSTONE_OPEN)):
        if word in t and not rx.search(t):
            for line in t.split("\n"):
                if word in line:
                    return (word, line.strip()[:88])
    return None


def report_block(bad, examined):
    print("[knowledge] REFUSED: %d of %d new knowledge doc(s) would land unreachable."
          % (len(bad), examined))
    print()
    for rel in bad:
        print("          unreachable: %s" % rel)
        m = _malformed_marker(rel)
        if m:
            print()
            print("          ‼️ THIS DOC HAS A `%s` LINE THAT I CANNOT PARSE:" % m[0])
            print("               %s" % m[1])
            print()
            print("          So the marker is PRESENT and the reader is rejecting it - do not")
            print("          rewrite it blind. Common cause: the marker WRAPS across lines, or")
            print("          its closing `-->` is missing. If it looks correct to you, the")
            print("          reader is the thing that is wrong; say so rather than editing.")
    print()
    print("          A doc nobody can find is not knowledge, it is a second copy of the")
    print("          problem.")
    print()
    print("          Fix it with ANY ONE of these - all three are honest answers:")
    print()
    print("            1. add a row to DEV-DOCS-INDEX.md  (forward-looking / planned work)")
    print("            2. add a route-tags line, so route.py can find it:")
    print("                 <!-- route-tags: <words someone would SEARCH with> -->")
    print("            3. say plainly that it is not knowledge:")
    print("                 <!-- unindexed: scratch output of <lane>, not a reference -->")
    print()
    print("          Door 3 costs one line and is not a loophole - it is how 'deliberately")
    print("          not indexed' stops looking identical to 'nobody remembered'.")


SUPERSEDES = re.compile(r"<!--\s*supersedes:\s*(.+?)\s*-->")
DISTINCT = re.compile(r"<!--\s*distinct-from:\s*(.+?)\s*-->")
OVERLAP_FLOOR = 3

CORRESPONDENCE = re.compile(r"<!--\s*correspondence:\s*(.+?)\s*-->")
CORRESPONDENCE_SHAPE = re.compile(r"^[oO]\d+-(BRIEF|REPLY|TO|NOTICE)[-.]", re.I)


def is_correspondence(text, path=None):
    """Is this a DERIVED artifact - a thread, a generated view, a template, or a test?
    """
    if CORRESPONDENCE.search(text):
        return True
    from mentions import is_derived
    return is_derived(text, path)[0]


def looks_like_correspondence(rel):
    """Filename shape - used ONLY to suggest the marker, never to grant the exemption."""
    return bool(CORRESPONDENCE_SHAPE.match(rel.replace("\\", "/").rsplit("/", 1)[-1]))


def overlapping(rel, text, pool=None):
    """Existing artifacts sharing >= OVERLAP_FLOOR tags with this NEW doc.

    A VERIFIED MISS is only as good as the words the searcher chose: a fact can sit in a note
    named for something else, so a search for its subject returns nothing. A session that
    misses for that reason then produces the duplicate the whole system exists to prevent.

    Refusal is escapable in one line, and BOTH escapes require naming the neighbour:
      <!-- supersedes: <path> -->            this replaces it (then tombstone it)
      <!-- distinct-from: <path>: <why> -->  they genuinely differ, and here is how
    You cannot satisfy either without having looked at the thing you nearly duplicated, which
    is the entire point - the cost is reading one doc, and that reading IS the safeguard.

    `pool` is [(rel, text)] and exists so a fixture can pin the behaviour against a corpus it
    controls. Without it the check reads the real tree, exactly as before - a test that can
    only run against live files is a test that changes its own answer every time someone
    commits, which is how a pinned case stops pinning anything.
    """
    tags = {w.lower().strip(",.") for m in ROUTE_TAGS.findall(text) for w in m.split()}
    if len(tags) < OVERLAP_FLOOR:
        return []
    # A message is not a second HOME for the subject, so it can neither be the duplicate nor be
    # duplicated. Exempt on BOTH sides: refusing a new reference doc for overlapping last week's
    # BRIEF is the same false positive pointing the other way.
    if is_correspondence(text):
        return []
    named = " ".join(SUPERSEDES.findall(text) + DISTINCT.findall(text))
    if pool is None:
        pool = []
        for p in list(ROOT.glob("*.md")) + list((ROOT / "docs").rglob("*.md")):
            r = str(p.relative_to(ROOT)).replace("\\", "/")
            try:
                pool.append((r, p.read_text(encoding="utf-8", errors="replace")[:4000]))
            except OSError:
                continue
    hits = []
    for r, t in pool:
        name = r.rsplit("/", 1)[-1]
        if r == rel.replace("\\", "/") or not in_knowledge_path(r):
            continue
        if is_correspondence(t):
            continue
        other = {w.lower().strip(",.") for m in ROUTE_TAGS.findall(t) for w in m.split()}
        shared = tags & other
        if len(shared) >= OVERLAP_FLOOR and name not in named:
            hits.append((r, sorted(shared)))
    return hits


def stale_rows(gone, index_text):
    """Old names the INDEX still points at after this commit removes/renames them.

    Cost of the narrower rule: a pure DELETE whose row links by bare filename is missed. That
    is a false negative, and false negatives are the survivable direction here.
    """
    norm = index_text.replace("\\", "/")
    return [n for n in gone if n.replace("\\", "/") in norm]


# The file route.py appends to, found the same way (`$CLAUDE_HOME_STATE_DIR` moves it).
try:
    from shared_state import home_state_dir as _home_state_dir
except ImportError:  # a published copy without shared_state.py keeps the historical path
    def _home_state_dir():
        return Path.home() / ".claude" / "state"
ROUTE_LOG = _home_state_dir() / "route-usage.jsonl"


def _this_session():
    for k in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID"):
        v = (os.environ.get(k) or "").strip()
        if v:
            return v[:80]
    return ""


def asked_the_router():
    """Did THIS session run route.py? (asked, log_readable) - and both halves matter.
    """
    sid = _this_session()
    if not sid:
        return False, False                     # automation: it cannot ask, so it is not asked of
    try:
        text = ROUTE_LOG.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return False, False                     # no log yet: never block on ignorance
    for line in text.splitlines():
        if sid in line:
            return True, True
    return False, True


def merge_parents():
    """The commits being merged in (MERGE_HEAD, one per line), or [] when this is no merge.

    Git writes full commit ids here. Anything else was not written by a merge, so it counts as
    no merge and the commit is judged in full; a line passed to `git diff` could also be an option.
    """
    rc, path, _ = git(["rev-parse", "--git-path", "MERGE_HEAD"])
    if rc != 0 or not path:
        return []
    p = Path(path) if os.path.isabs(path) else ROOT / path
    try:
        heads = [ln.strip() for ln in p.read_text(encoding="utf-8", errors="replace").splitlines()
                 if ln.strip()]
    except OSError:
        return []
    return heads if all(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", h) for h in heads) else []


def staged_changes(heads=None):
    """`git diff --cached --name-status` lines this commit's AUTHOR is answerable for.

    A normal commit: every staged change. A merge (heads = the MERGE_HEAD commits): only paths
    whose result differs from the first parent AND from every merged-in parent. A doc that came
    in unchanged from the other branch is identical to that parent, so it was not written by the
    author of the merge and is not theirs to justify. A doc added by hand in the merge, or a
    conflict resolution, differs from every parent and is judged as before.

    If a git call in the merge case fails (git() also reports a timeout as a failure), the narrowing
    is dropped and every staged change is judged, as before this rule existed. Returning an
    error here would make check() allow the commit, which would turn the gate off for that merge.
    """
    def everything():
        rc, out, _ = git(["diff", "--cached", "--name-status"])
        return rc, out.splitlines()

    if not heads:
        return everything()
    rc, out, _ = git(["diff", "--cached", "--name-status", "--no-renames", "HEAD"])
    if rc != 0:
        return everything()
    mine = {}
    for ln in out.splitlines():
        parts = ln.split("\t", 1)
        if len(parts) == 2:
            mine[parts[1]] = ln
    for h in heads:
        rc, out, _ = git(["diff", "--cached", "--name-only", "--no-renames", h])
        if rc != 0:
            return everything()
        differs = set(out.splitlines())
        mine = {k: v for k, v in mine.items() if k in differs}
    return 0, list(mine.values())


def check():
    rc, lines = staged_changes(merge_parents())
    out = "\n".join(lines)
    if rc != 0:
        return 0                                   # cannot tell: never block on ignorance
    added = [ln.split("\t", 1)[1] for ln in out.splitlines()
             if ln.startswith("A\t") and "\t" in ln]
    # A rename is R<score>\told\tnew; a delete is D\tpath. Both retire the OLD name.
    gone = []
    for ln in out.splitlines():
        parts = ln.split("\t")
        if parts[0].startswith("R") and len(parts) == 3:
            gone.append(parts[1])
            added.append(parts[2])
        elif parts[0] == "D" and len(parts) == 2:
            gone.append(parts[1])
    gone = [g for g in gone if in_knowledge_path(g)]
    cands = [r for r in added if in_knowledge_path(r)]
    if not cands and not gone:
        return 0                                   # not a knowledge commit: silent, always

    index_text = INDEX.read_text(encoding="utf-8", errors="replace") if INDEX.exists() else ""
    rotted = stale_rows(gone, index_text)
    bad, liars, dupes, facetgaps = [], [], [], []
    for rel in cands:
        p = ROOT / rel
        text = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
        ok, _door = reachable(rel, text, index_text)
        if not ok:
            bad.append(rel)
        if false_badge(rel, text, index_text):
            liars.append(rel)
        for other, shared in overlapping(rel, text):
            dupes.append((rel, other, shared))
        try:
            import facets
            if not facets.GENERATED.search(text[:400]) and not UNINDEXED.search(text):
                for facet, value, n in facets.missing(text):
                    facetgaps.append((rel, facet, value, n))
        except Exception:
            pass

    unrouted = []
    if cands:
        asked, knowable = asked_the_router()
        if knowable and not asked:
            unrouted = [r for r in cands
                        if not UNINDEXED.search(
                            (ROOT / r).read_text(encoding="utf-8", errors="replace")
                            if (ROOT / r).exists() else "")
                        and not looks_like_correspondence(r)]

    tomb = check_tombstones([ROOT / r for r in cands if (ROOT / r).exists()])
    if not bad and not tomb and not liars and not rotted and not dupes and not facetgaps \
            and not unrouted:
        print("[knowledge] %d new + %d retired knowledge doc(s) examined, all clean."
              % (len(cands), len(gone)))
        return 0

    if bad:
        report_block(bad, len(cands))
    for rel in liars:
        print()
        print("[knowledge] REFUSED: %s wears the 'Indexed in DEV-DOCS-INDEX' badge" % rel)
        print("            but the index does not list it.")
        print("            A false badge is worse than no badge: it answers the reader's")
        print("            question, so they stop checking. Either add the row, or remove")
        print("            the badge - both are honest; disagreeing is not.")
    if facetgaps:
        print()
        print("[knowledge] REFUSED: %d facet(s) this doc plainly needs are not declared."
              % len(facetgaps))
        print()
        for rel, facet, value, n in facetgaps:
            print("          %s says '%s' %d times but never declares it:" % (rel, value, n))
            print("              <!-- %s: %s -->" % (facet, value))
        print()
        print("          A searcher types the PLATFORM and the ACCESS MECHANISM far more often")
        print("          than they type your topic. Declaring them costs one line each and")
        print("          expands, at query time, to every alias anyone might type instead.")
        print()
        print("          ⛔ IF THE DOC NAMES IT ONLY TO SAY IT IS ABSENT - an audit, a gap")
        print("          analysis, a boundary statement - DO NOT DECLARE IT. Decline it with a")
        print("          reason instead, and the check stops asking:")
        print("              <!-- not-facet: %s: %s - names it only to record that it is absent -->"
              % (facetgaps[0][1], facetgaps[0][2]))
        print("          The reason is required and is the point: a stated refusal is worth more")
        print("          than a silent skip, and more than a facet that is not true.")
        print()
        print("          If this doc covers a path that NO LONGER EXISTS, say so too -")
        print("              <!-- retired: <the-path-that-was-removed> -->")
        print("          so a search for it lands here instead of returning a clean miss,")
        print("          which reads as permission to rebuild the thing that was removed.")
    for rel, other, shared in dupes:
        print()
        print("[knowledge] REFUSED: %s overlaps an existing doc on %d tag(s)." % (rel, len(shared)))
        print("            existing: %s" % other)
        print("            shared  : %s" % ", ".join(shared))
        print()
        print("            A VERIFIED MISS is only as good as the words you searched with.")
        print("            A fact can sit in a note named for something else, so a search for")
        print("            its subject finds nothing.")
        print("            This is the second, independent check: the router ran on words you")
        print("            guessed, this runs on the tags you chose.")
        print()
        print("            READ that doc. Then one line, either way:")
        print("              <!-- supersedes: %s -->" % other)
        print("              <!-- distinct-from: %s: <how they differ> -->" % other)
        # The filename shape does NOT grant the exemption - it only makes the escape
        # discoverable at the one moment anybody is looking for it.
        if looks_like_correspondence(rel):
            print()
            print("            This is named like a MESSAGE between sessions. If it is one,")
            print("            say so instead - a reply shares its thread's tags by nature,")
            print("            and that is not a duplicate of the message it answers:")
            print("              <!-- correspondence: from=<you> to=<them> -->")
    if rotted:
        print()
        print("[knowledge] REFUSED: %d retired doc(s) are still named by DEV-DOCS-INDEX."
              % len(rotted))
        print()
        for r in rotted:
            print("          removed/renamed here, still an index row: %s" % r)
        print()
        print("          An index row exists to be followed. One pointing at a path that no")
        print("          longer exists is the same failure as an unfindable doc, arriving")
        print("          from the other side - a map that sends the reader nowhere.")
        print()
        print("          Fix: update the row to the new path in THIS commit, or leave a")
        print("          tombstone at the old path so the pointer still resolves:")
        print("            <!-- tombstone: superseded-by=<new path> date=YYYY-MM-DD by=<you> -->")
    if unrouted:
        print()
        print("[knowledge] REFUSED: %d new doc(s), and this session never asked the router."
              % len(unrouted))
        for r in unrouted:
            print("            %s" % r)
        print()
        print("            The router exists to be consulted BEFORE a doc is created, so the")
        print("            answer lands in the artifact that already holds the subject instead")
        print("            of beside it.")
        print()
        print("            Ask it - one command, and a HIT or PARTIAL means extend that file")
        print("            rather than add this one:")
        print("              python <scripts>/route.py <your subject, in your own words>")
        print()
        print("            Then commit again. A VERIFIED MISS is the verdict that licenses a")
        print("            new doc, and it carries a receipt.")
        print()
        print("            Not applicable? A doc that opts out of routing says so:")
        print("              <!-- unindexed: <why> -->")
        print()
        print("            THE RULE: lanes may DRAFT a new doc; only orchestrators may LAND one.")
        print("            A lane that reaches this refusal reports the draft to its")
        print("            orchestrator with why it believes the subject is new - it does not")
        print("            use --no-verify and does not add <!-- unindexed --> to get past it.")
        print()
        print("            Why an orchestrator: the router asks whether an artifact already")
        print("            covers these WORDS, and the overlap check asks whether one shares")
        print("            TAGS. Neither asks whether this should be a doc at all rather than a")
        print("            section of one - and a doc that belongs inside an existing artifact")
        print("            passes both cleanly while still being sprawl.")

    for p, why in tomb:
        print()
        print("[knowledge] REFUSED: broken tombstone in %s" % p.name)
        print("            %s" % why)
        print("            A tombstone whose pointer rots sends the next session nowhere,")
        print("            with more confidence than no pointer at all.")
    return REFUSE


def undurable(index_text):
    """Docs the INDEX names that exist on NO pushed branch - i.e. on one laptop only.

    Matched by BASENAME against origin/main's whole tree, deliberately: a file that MOVED is
    still durable, and reporting it lost because its old path is gone is the false positive
    that would make this list ignorable. Cost: a genuine delete-and-recreate elsewhere reads
    as safe. False negative, the survivable direction.
    """
    rc, out, _ = git(["ls-tree", "-r", "--name-only", "origin/main"])
    if rc != 0:
        return None                       # cannot tell: say so, never guess
    names = {p.rsplit("/", 1)[-1] for p in out.splitlines()}
    local = list(ROOT.glob("*.md")) + list((ROOT / "docs").rglob("*.md"))
    return [p.relative_to(ROOT) for p in local
            if p.name in index_text and p.name not in names]


def durability():
    """ONLY the data-loss case, so it can run daily and mean something when it is red.
    """
    index_text = INDEX.read_text(encoding="utf-8", errors="replace") if INDEX.exists() else ""
    nd = undurable(index_text)
    if nd is None:
        print("[knowledge] durability: cannot reach origin/main - not concluding anything.")
        return 0
    if not nd:
        print("[knowledge] durability: every indexed doc exists on a pushed branch.")
        return 0
    print("[knowledge] ⛔ %d indexed doc(s) exist on ONE LAPTOP ONLY:" % len(nd))
    for f in nd:
        print("              %s" % f)
    print()
    print("    DEV-DOCS-INDEX points a reader at these and no pushed branch has them.")
    print("    A disk failure takes the doc and leaves the map pointing at nothing.")
    return 1


def audit():
    """Whole tree. Same predicate as --check, so the audit cannot disagree with the gate."""
    index_text = INDEX.read_text(encoding="utf-8", errors="replace") if INDEX.exists() else ""
    cands = [p for p in list(WS.glob("*.md")) + list(WS.glob("docs/**/*.md"))
             if in_knowledge_path(str(p.relative_to(WS)))]
    doors, gaps, liars = {}, [], []
    for p in cands:
        t = p.read_text(encoding="utf-8", errors="replace")
        rel = str(p.relative_to(WS))
        ok, door = reachable(rel, t, index_text)
        if ok:
            doors[door.split(" (")[0]] = doors.get(door.split(" (")[0], 0) + 1
        else:
            gaps.append(p.relative_to(WS))
        if false_badge(rel, t, index_text):
            liars.append(p.relative_to(WS))
    print("knowledge audit - workspace")
    print()
    print("  knowledge docs EXAMINED : %d" % len(cands))
    for d, n in sorted(doors.items(), key=lambda x: -x[1]):
        print("    reachable via %-22s : %d" % (d, n))
    print("    UNREACHABLE                          : %d" % len(gaps))
    for g in gaps:
        print("       - %s" % g)
    print("    FALSE index badge (claims a row it lacks): %d" % len(liars))
    for l in liars:
        print("       - %s" % l)
    nd = undurable(index_text)
    if nd is None:
        print("    on ONE laptop only                   : cannot tell (no origin/main)")
        nd = []
    else:
        print("    ⛔ on ONE laptop only (indexed, on no pushed branch): %d" % len(nd))
        for f in nd:
            print("       - %s" % f)
    tomb = check_tombstones(cands)
    print()
    print("  tombstones with a broken pointer       : %d" % len(tomb))
    for p, why in tomb:
        print("       - %s: %s" % (p.name, why))
    print()
    if gaps or tomb or liars or nd:
        print("  %d doc(s) cannot be found by any route. Each is a future session" % len(gaps))
        print("  reporting 'this does not exist' about something that does.")
        return 1
    print("  every knowledge doc is reachable.")
    return 0


REFUSE = 9

SCRIPTS = Path(__file__).resolve().parent


def _hook_cmd(script, args=""):
    """The exact `python "<abs path>" <args>` a pre-commit hook needs, resolved not typed."""
    return 'python "%s"%s' % ((SCRIPTS / script).as_posix(), args)


HOOK_LINE = _hook_cmd("knowledge_gate.py", " --check") + '; [ $? -eq 9 ] && exit 1; true'


def install():
    rc, gitdir, _ = git(["rev-parse", "--git-dir"])
    if rc != 0:
        print("not a git repo: %s" % WS, file=sys.stderr)
        return 2
    hooks = (WS / gitdir / "hooks") if not os.path.isabs(gitdir) else (Path(gitdir) / "hooks")
    hooks.mkdir(parents=True, exist_ok=True)
    target = hooks / "pre-commit"
    cur = target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""
    if "knowledge_gate" in cur:
        print("[ok] already installed: %s" % target)
        return 0
    if not cur:
        cur = "#!/bin/sh\n"
    precommit = _hook_cmd("orchdoc_precommit.py", " --check")
    cur = cur.replace("exec " + precommit, precommit + " || exit 1")
    body = cur.rstrip("\n") + "\n" + HOOK_LINE + "\nexit 0\n"
    target.write_text(body, encoding="utf-8")
    try:
        os.chmod(str(target), 0o755)
    except OSError:
        pass
    print("[ok] installed: %s" % target)
    print("     Both guards now run, and neither shadows the other.")
    return 0


def selftest():
    ok = True

    def t(label, cond):
        nonlocal ok
        print("  [%s] %s" % ("OK " if cond else "FAIL", label))
        ok &= bool(cond)

    print("knowledge_gate selftest")
    t("a lane SEED is not gated", not in_knowledge_path("o99L1-SEED.md"))
    t("a bootstrap prompt is not gated", not in_knowledge_path("o99-sample-bootstrap.md"))
    t("an OrchDoc is not gated", not in_knowledge_path("ORCHESTRATOR-DECISIONS-o99.md"))
    t("CLAUDE.md is not gated", not in_knowledge_path("CLAUDE.md"))
    t("a root knowledge doc IS gated", in_knowledge_path("sample-notes-2026-07-29.md"))
    t("a docs/ doc IS gated", in_knowledge_path("docs/specs/whatever.md"))
    t("a repo doc is NOT gated (other repos own their own)",
      not in_knowledge_path("product-repo/docs/x.md"))

    t("door 2 opens on route-tags",
      reachable("x.md", "<!-- route-tags: alpha beta -->", "")[0])
    t("door 3 opens on a stated opt-out",
      reachable("x.md", "<!-- unindexed: lane scratch -->", "")[0])
    t("door 1 opens on an index row", reachable("x.md", "", "see x.md here")[0])
    t("no door -> unreachable", not reachable("x.md", "# title\nbody", "")[0])

    t("a retired doc whose OLD PATH the index still names is caught",
      stale_rows(["docs/old/a.md"], "see docs/old/a.md here") == ["docs/old/a.md"])
    t("a retired doc the index never named is fine",
      stale_rows(["docs/old/a.md"], "unrelated index text") == [])
    t("rename-and-update in the SAME commit is NOT flagged",
      stale_rows(["docs/old/a.md"], "see docs/new/a.md here") == [])
    t("a RECORD mentioning it (seed/OrchDoc) is NOT the index, so not blocking",
      stale_rows(["docs/old/a.md"], "") == [])

    nine = "client data promise stored files archive retention privacy policy commitment"
    tagline = "<!-- route-tags: %s -->\n" % nine
    msg = "<!-- correspondence: from=o99 to=o98 -->\n" + tagline
    pool_ref = [("neighbour.md", tagline + "# a reference doc")]
    pool_msg = [("O98-REPLY-thing-2026-08-17.md", msg + "# o98 -> o99")]
    t("two REFERENCE docs sharing 9 tags are still REFUSED (the rule still works)",
      len(overlapping("new.md", tagline, pool_ref)) == 1)
    t("...and the overlap it reports really is 9 tags",
      overlapping("new.md", tagline, pool_ref)[0][1] == sorted(set(nine.split())))
    t("two CORRESPONDENCE docs sharing 9 tags PASS",
      overlapping("O99-TO-o98-thing.md", msg, pool_msg) == [])
    t("a reference doc is not refused for overlapping a MESSAGE (exempt on both sides)",
      overlapping("new.md", tagline, pool_msg) == [])
    t("a message is not refused for overlapping a REFERENCE doc",
      overlapping("O99-TO-o98-thing.md", msg, pool_ref) == [])
    t("the NAME alone does not grant the exemption",
      len(overlapping("O99-TO-o98-thing.md", tagline, pool_ref)) == 1)
    t("...but the name DOES trigger the suggestion in the refusal",
      looks_like_correspondence("O99-TO-o98-thing.md")
      and looks_like_correspondence("O98-REPLY-x-2026-08-17.md")
      and not looks_like_correspondence("commerce-and-external-provider-access.md"))
    t("correspondence is exempt from OVERLAP ONLY - it still needs a door",
      not reachable("O99-TO-o98.md", "<!-- correspondence: from=o99 to=o98 -->", "")[0])

    badge = "> Indexed in [DEV-DOCS-INDEX](./DEV-DOCS-INDEX.md)\n"
    t("a badge with no matching row is caught", false_badge("x.md", badge, "other stuff"))
    t("a badge WITH a matching row is fine", not false_badge("x.md", badge, "row for x.md"))
    t("no badge is never a false badge", not false_badge("x.md", "# t", ""))

    h = tombstone_header("docs/new.md", "2026-08-12", "o99", "New", "moved")
    m = TOMBSTONE_OPEN.search(h)
    t("the header we write is matched by the regex we read with", bool(m))
    t("...and carries the successor path", bool(m) and m.group("path") == "docs/new.md")
    t("...and is closed", TOMBSTONE_CLOSE in h)

    print("\nselftest %s" % ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        arg = sys.argv[1] if len(sys.argv) > 1 else "--check"
        sys.exit({"--install": install, "--selftest": selftest, "--durability": durability,
                  "--audit": audit, "--check": check}.get(arg, check)())
    except Exception:
        sys.exit(0)   # fail open, always
