#!/usr/bin/env python3
"""Gate 2: a knowledge doc entering the workspace must be REACHABLE, or say it isn't.

<!-- route-tags: knowledge gate registration tombstone superseded reachable index dev-docs -->

WHAT THIS IS FOR, measured rather than asserted
-----------------------------------------------
`--audit` over the workspace on 2026-08-12: 82 knowledge docs examined, 63 registered in
DEV-DOCS-INDEX, 10 ephemeral (seeds/prompts/bootstraps), and **9 genuinely unreachable**.

Two of those nine are a payment vendor's OAuth homework note and its test-purchase runbook,
both dated in their filenames and sitting at the workspace root. On 2026-08-11 a session
searched for that vendor's setup process, found nothing, and reported that none existed. It
existed - at the workspace root, unindexed. The runbook even carries an owner ruling ("THIS
WILL NOT BE DONE. REMOVE IT AS A TO-DO") that a session unable to find it will re-raise as an
open question.

⭐ So the defect this blocks is not hypothetical and not rare: it is ~11% of the corpus, and
its cost is a session confidently reporting absence, then redoing solved work.

THE THREE DOORS - registration is not the only honest answer
------------------------------------------------------------
An earlier draft required a DEV-DOCS-INDEX row for every new doc. Measured: that blocks 19 of
82, and 10 of the 19 are lane seeds and bootstrap prompts, which are launch INPUTS, not
knowledge. A gate whose refusals are half wrong gets disabled, and then the 9 real ones ride
back in with it. So the requirement is REACHABILITY, satisfied by any one of:

  1. a row in DEV-DOCS-INDEX.md naming the file       (forward-looking / planned work)
  2. a `<!-- route-tags: ... -->` line in the doc      (routable by route.py)
  3. a `<!-- unindexed: <reason> -->` marker           (an honest, stated opt-out)

Door 3 is not a loophole, it is the point. Silence and "deliberately not indexed" look
identical on disk today; door 3 makes them different, and costs one line.

THE TOMBSTONE CONTRACT (`TOMBSTONE_OPEN`)
-----------------------------------------
o10, 2026-08-12: *"a format that differs per orchestrator is worse than none, since the entire
value is that a session recognises a tombstone it has never seen."* Correct, so the shape lives
HERE, in one importable place, and is matched by SHAPE not by prose - the marker-format-is-a-
contract rule (`memory/marker_format_is_a_contract.md`).

    <!-- tombstone: superseded-by=<path> date=YYYY-MM-DD by=<session> -->
    # SUPERSEDED - this document is no longer current
    **Current home:** <path>
    <one line: what changed>
    <!-- /tombstone -->
    ...ORIGINAL BODY LEFT IN PLACE, UNCHANGED...

⛔ DO NOT EMPTY A TOMBSTONED DOC. The old words are what make the tombstone findable by
someone searching for the old thing. Gut the body and you break the exact search that needed
to land here - the tombstone becomes reachable only by someone who already knows the new path,
i.e. by someone who did not need it.

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
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Paths are RESOLVED, not hardcoded - see workspace_paths.py. A script carrying one
# machine's absolute path cannot run in a skill, a fresh clone, a worktree or the cloud,
# which is why none of this shipped anywhere until 2026-08-13.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from workspace_paths import WS, MEM  # noqa: E402


def _root():
    """The repo THIS commit is happening in - not a hardcoded path.

    ⛔ FOUND BY THE GATE REFUSING ITS OWN CORRECT COMMIT, 2026-08-12. `INDEX` was
    `WS / "DEV-DOCS-INDEX.md"`, an absolute path to the main checkout. Committing from a
    WORKTREE therefore judged the staged rename against a DIFFERENT copy of the index - the
    one that still had the old rows - so a commit that correctly updated its own index was
    refused, with a message naming rows the committer had already fixed.

    ⭐ Worse than a plain false positive: the instruction it printed was impossible to satisfy.
    The rows it complained about were fixed in the very commit being refused, and no amount of
    editing the worktree's index could change the file it was actually reading.

    This is the multi-repo cwd rule (`memory/feedback_multi_repo_cwd_discipline.md`) inside a
    guard: never rely on a fixed root when the operation has its own.
    """
    try:
        p = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=10)
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
# The back-pointer badge convention from project CLAUDE.md. A doc WEARING it while absent from
# the index is worse than an unregistered doc: the badge answers the reader's question falsely,
# so they stop checking. Measured 2026-08-12: 3 of 38 badge-wearers were not in the index.
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


# Launch INPUTS, not knowledge: consumed once, by one lane, and never searched for again.
EPHEMERAL = re.compile(r"(-SEED|-PROMPT|-bootstrap|-bridge|-PREVIEW|-SKILL-DRAFT)\.md$", re.I)
NEVER_GATE = {"CLAUDE.md", "DEV-DOCS-INDEX.md", "ORCHESTRATOR-REGISTRY.md", "README.md",
              "MEMORY.md", "AUTHORING-CHECKLIST.md",
              # The script-writing checklist was split into a router + 4 rule files on
              # 2026-08-12, and file 2 was split again into 2 + 2B on 2026-08-13; the
              # companions are the same artifact and carry the same exemption.
              "SCRIPT-CHECKLIST-2-LENGTH.md", "SCRIPT-CHECKLIST-2B-PAUSE.md",
              "SCRIPT-CHECKLIST-3-DROPDOWNS.md",
              "SCRIPT-CHECKLIST-4-VOICE.md", "SCRIPT-CHECKLIST-5-FORMAT-AND-GATE.md"}


def git(args, cwd=None):
    try:
        p = subprocess.run(["git", "-C", str(cwd or ROOT)] + list(args),
                           capture_output=True, text=True, timeout=20)
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
    # ⭐ THE BEST DOOR, and the one to prefer. route-tags freeze a doc at the vocabulary of the
    # day it was written; a SUBJECT inherits that subject's whole alias set at query time, so
    # widening the subject later retroactively finds this doc too. Measured 2026-08-13: a doc
    # declaring `subject: commerce` and never containing the payment vendor's NAME is found by
    # searching that name; the identical doc without the line is invisible. That is the founding
    # failure - the webhook version filed under a note named for refunds - closed by one line.
    # The pattern is IMPORTED, never retyped - see the note on route.py's SUBJECT_RE. A doc
    # declaring `cornerstone:` must open this door too, or the gate refuses documents the router
    # has already accepted, and the two tools disagree about what "declared" means.
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

    ⛔ o7, 2026-08-18, on a doc refused as unreachable while its route-tags line sat correctly in
    the file - unreadable only because three private copies of the regex omitted re.S and the
    marker wrapped at this workspace's own column width:

        "The author would have had no way to tell the difference between 'my marker is wrong'
         and 'the reader is broken'."

    ⭐ THAT ASYMMETRY IS WHAT MAKES A FALSE REFUSAL EXPENSIVE. A refusal that says only "not
    reachable" sends the author to re-write a marker that was already right, and no amount of
    re-writing reaches the correct diagnosis. Naming the unparsed attempt costs one line and
    turns a dead end into a bug report.
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
    print("          problem. Measured 2026-08-12: 9 such docs already sit in this")
    print("          workspace, and two of them are the vendor runbooks a session searched for,")
    print("          failed to find, and reported as non-existent.")
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

# ⭐ CORRESPONDENCE IS A CONVERSATION ABOUT ONE TOPIC, AND SHARING TAGS IS WHAT A THREAD IS.
# Measured 2026-08-17: 15 docs in this workspace would be refused by the overlap check, and 7 of
# them are our own inter-orchestrator messages overlapping EACH OTHER on 5-9 tags -
# `O9-TO-o10-voice-data-promise`, `O10-REPLY-voice-data-promise`, `O1-BRIEF-generated-track-
# retention`. The gate was reading a reply as a duplicate of the message it replies to.
#
# The overlap rule is CORRECT for reference docs, where two live homes for one subject is the
# defect. It is simply the wrong instrument for a thread: a reply is not a second home for the
# subject, it is a second turn about it, and there is no version of "merge them" that helps.
#
# ⛔ AN EXPLICIT MARKER, NOT A FILENAME REGEX. `O\d+-(BRIEF|REPLY|TO|NOTICE)` would exempt any
# future doc that happens to be named that way, which is the identical over-reach as matching
# every file called CLAUDE.md - a rule whose reason is about a document's KIND, written as a
# match on its NAME, quietly widening every time the corpus grows. The writer knows what they
# are writing; one line says so. The filename SHAPE is used only to SUGGEST the marker in the
# refusal message, so the escape is discoverable at the moment it is needed.
#
# ⛔ THIS IS AN OVERLAP EXEMPTION ONLY. Correspondence still has to be reachable through one of
# the three doors, and the marker is not one of them - otherwise this becomes the hole that the
# reachability gate exists to close.
CORRESPONDENCE = re.compile(r"<!--\s*correspondence:\s*(.+?)\s*-->")
CORRESPONDENCE_SHAPE = re.compile(r"^[oO]\d+-(BRIEF|REPLY|TO|NOTICE)[-.]", re.I)


def is_correspondence(text, path=None):
    """Is this a DERIVED artifact - a thread, a generated view, a template, or a test?

    ⭐ WIDENED 2026-08-17 from correspondence-only. The overlap check exists to stop two live
    homes for one subject; none of the four derived kinds can BE a second home, because none of
    them can drift on their own. A reply shares its subject's vocabulary by definition, a
    generated page is recomputed, a template's examples are shapes, a test must contain the
    strings it exercises.

    ⛔ Still MARKER-driven, never filename shape. `looks_like_correspondence` below only
    SUGGESTS the marker in a refusal; granting the exemption on a name would let any future doc
    named that way through, which is the over-reach that hid a whole product repo elsewhere.
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

    ⛔ THE SAFEGUARD'S SAFEGUARD (the human, 2026-08-13): *"What if the process runs <words> that
    don't actually capture the correct doc? My concern is to prevent multiple CREATE.md
    creations of new processes that will then collide."*

    Correct, and nothing guarded it. A VERIFIED MISS is only as good as the words the searcher
    chose - the payment vendor's webhook version was correct, documented, and filed under a note
    named for REFUNDS, so a search for the vendor's name returned nothing. A session that
    misses for vocabulary reasons then follows CREATE.md faithfully and produces the duplicate
    the whole system exists to prevent.

    ⭐ The router runs BEFORE the doc exists, on words the session guessed. This runs AFTER, on
    the tags the session actually chose - a second, independent sample of the same question,
    taken from different evidence. A miss can survive one and not the other.

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
    # ⛔ THE 4000 STAYS, AND IT IS A RULING RATHER THAN AN OVERSIGHT (o9, 2026-09-08). A peer
    # fixed the same-looking truncation in route.py, found this alongside it, and correctly
    # stopped rather than changing it unasked.
    #
    # ⭐ THE OTHER THREE READERS TRUNCATED A **DECLARATION**; THIS ONE TRUNCATES A **SIMILARITY
    # POOL**, and that is a different kind of number. A declaration either exists or it does
    # not, so where the line sits is an accident of the file growing and reading further only
    # finds what was always there. This window instead decides HOW MUCH TEXT TWO DOCUMENTS ARE
    # COMPARED ON - so widening it makes the duplicate guard progressively STRICTER, and would
    # refuse documents today that it admitted yesterday, with nobody having decided that.
    #
    # ⚠️ AND IT CUTS BOTH WAYS, which is the part that makes leaving it defensible rather than
    # merely cautious: truncation can also MANUFACTURE duplicates, when two docs share a long
    # preamble and diverge past the cap. Moving the number in either direction trades one error
    # for the other. That is a policy call for the human, on evidence nobody has gathered.
    #
    # Measured before ruling: at 4000 versus a whole-file read, 0 documents gain a declaration
    # and 2 lose one - both non-declarations (a generator's string literal, a test fixture).
    # So there is no live defect here to fix, only a parameter to leave alone.
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

    ⛔ SCOPED TO INDEX ROWS ON PURPOSE, and the narrower scope is the point. A rename also
    leaves the old name sitting in lane seeds and OrchDoc entries - but those are RECORDS
    ("on 08-11 we wrote X at path Y"), not POINTERS. Refusing until someone rewrites history
    docs would be a false positive whose only available fix is falsifying a record, and a gate
    like that gets switched off, taking the real catches with it.

    An INDEX row is different in kind: its entire job is to be followed. A row pointing at a
    path that no longer exists is the failure this whole workstream is about, arriving from the
    opposite direction - not "knowledge nobody can find" but "a map that sends you nowhere."

    ⛔ MATCHES THE FULL OLD PATH, NEVER THE BASENAME. The first version matched the basename
    and refused this very commit AFTER the rows had been correctly updated - because the
    filename is unchanged by a move, so it still appears in the index, at its NEW path. That
    is a false positive on the correct behaviour, which is the direction that gets a guard
    switched off and takes the real catches with it.

    ⭐ The selftest did not catch it: it asserted `stale_rows(["docs/specs/a.md"], "row for
    a.md here")`, i.e. it encoded the same basename assumption the code did. A test written
    from the implementation confirms the implementation, not the requirement.

    Cost of the narrower rule: a pure DELETE whose row links by bare filename is missed. That
    is a false negative, and false negatives are the survivable direction here.
    """
    norm = index_text.replace("\\", "/")
    return [n for n in gone if n.replace("\\", "/") in norm]


ROUTE_LOG = Path.home() / ".claude" / "state" / "route-usage.jsonl"


def _this_session():
    for k in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID"):
        v = (os.environ.get(k) or "").strip()
        if v:
            return v[:80]
    return ""


def asked_the_router():
    """Did THIS session run route.py? (asked, log_readable) - and both halves matter.

    \u2b50 THE TRIGGER MOVES FROM A CLAIM TO AN ARTIFACT. The rule says to run the router when
    you are about to write "there is no X". Nothing can detect that sentence. A new knowledge
    file appearing in a commit is a fact, and it is the moment the router was meant to precede -
    so that is where the question gets asked.

    \u26d4 RETURNS TWO VALUES SO THE CALLER CANNOT CONFUSE "did not ask" WITH "cannot tell".
    Collapsing those is how a guard starts refusing on ignorance, and a guard that refuses
    wrongly gets switched off - taking the correct refusals with it.
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


def check():
    rc, out, _ = git(["diff", "--cached", "--name-status"])
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
        # ⛔ NEW DOCS ONLY. 266 existing docs are missing a facet they plainly need; refusing
        # those would be a backlog masquerading as a gate. What a gate can do is stop the
        # backlog GROWING - the same split that let the reachability gate ship.
        #
        # ⛔ AND NEVER A GENERATED VIEW. facets.py excludes those from its own audit corpus for
        # two reasons this gate was not inheriting: a rendered page detects the platforms of the
        # documents it LISTS, not its own subject - and `render()` rebuilds the file from
        # scratch, so a facet written there is erased on the next --write while the gate keeps
        # demanding it. That is a backlog item that can never be closed, whose only remedy is a
        # hand-edit of a file whose first line forbids hand-edits.
        #
        # Measured 2026-08-17: the cornerstone map's 15 pages produced 47 refusals in one
        # commit, every one of them a listed document's vendor. Matched with facets' OWN regex,
        # not a second copy - a marker format is a contract.
        try:
            import facets
            # ⛔ ALSO EXEMPT A DECLARED-UNINDEXED DOC. Facets exist to make a doc findable;
            # door 3 says "do not route to me, route to my sources". Requiring both is
            # incoherent, and it bit on a generated candidate LISTING - which trips facet
            # detection precisely because it contains other docs names.
            if not facets.GENERATED.search(text[:400]) and not UNINDEXED.search(text):
                for facet, value, n in facets.missing(text):
                    facetgaps.append((rel, facet, value, n))
        except Exception:
            pass

    # \u2b50 THE ROUTER'S ARTIFACT TRIGGER. Asked once per commit, not per file - the question
    # is whether this session consulted the corpus before adding to it, and one consultation
    # answers that. See F108 in ORCHESTRATOR-DECISIONS-o9.md for the measurement that motivated
    # it: ~324 router runs in its 9-day build window, 4 in the 16 days after.
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
        # ⭐ EXAMINED, not merely FOUND. "0 examined" and "3 examined, all clean" must never
        # print the same line - that conflation is the defect this workstream keeps finding.
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
        # ⛔ THIS REFUSAL OMITTED THE ONE REMEDY THAT FITS AN AUDIT. `detect()` counts
        # WORDS, so it cannot tell "this document is ABOUT X" from "this document says X is
        # ABSENT" - and a gap analysis is built almost entirely of the second kind. One measured
        # case: an audit naming a sibling application five times and an analytics vendor nine,
        # every single mention a denial of the form "not here, by design" or "X is not
        # referenced anywhere in this repository", was told to declare eight facets. Several of
        # them would have been false, and a false facet is worse than a missing one because a
        # searcher lands on the document that says the opposite of what they need.
        #
        # ⭐ `declined()` HAS EXISTED FOR EXACTLY THIS AND THIS MESSAGE NEVER NAMED IT. A guard
        # whose printed remedy is the wrong one is the same defect as a guard whose printed
        # remedy cannot be performed - `block_shared_tree_ops` has that recorded from 2026-08-18.
        # The author was left choosing between a false facet and a bypass.
        #
        # ⚠️ NOT auto-suppressed by looking for negation words. This file already prefers
        # structural signals to phrasing, and a decline carries a STATED REASON, which is
        # strictly better information than a silent skip - a facet is judgement and belongs with
        # the author, because a wrong facet routes with the map's authority.
        print("          ⛔ IF THE DOC NAMES IT ONLY TO SAY IT IS ABSENT - an audit, a gap")
        print("          analysis, a boundary statement - DO NOT DECLARE IT. Decline it with a")
        print("          reason instead, and the check stops asking:")
        # ⚠️ COLON AFTER THE FACET NAME, and the reason at least 12 characters - both required by
        # `facets.NOT_FACET`. My first draft printed `not-facet: component pwa` without the colon
        # and I ran it through `declined()` rather than trusting it: it returned an empty set.
        # I nearly shipped an unperformable remedy INSIDE the fix for printing one.
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
        print("            The payment vendor's webhook version was correct, documented, and")
        print("            filed under a note named for REFUNDS - so a search for the vendor's")
        print("            own name found nothing.")
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
        print("            of beside it. Measured: 4 runs in the 16 days before this check.")
        print()
        print("            Ask it - one command, and a HIT or PARTIAL means extend that file")
        print("            rather than add this one:")
        print("              python .shared/scripts/route.py <your subject, in your own words>")
        print()
        print("            Then commit again. A VERIFIED MISS is the verdict that licenses a")
        print("            new doc, and it carries a receipt.")
        print()
        print("            Not applicable? A doc that opts out of routing says so:")
        print("              <!-- unindexed: <why> -->")
        print()
        # ⛔ RULED BY THE HUMAN 2026-09-08: "lanes may draft, only orchestrators may land."
        #
        # ⭐ HE ASKED FOR SOMETHING BETTER THAN THE CHECK THIS MESSAGE ENFORCES. His question was
        # whether a headless lane could ping its orchestrator for clearance - "a second pair of
        # eyes from a higher perspective on whether the new doc should be created at all". The
        # ping cannot work: a `claude -p` lane runs to completion and exits, so there is no
        # moment at which it is still alive to receive an answer. Placing the responsibility
        # gets the same review with no round trip.
        #
        # ⚠️ AND IT COVERS A GAP NEITHER SPRAWL CHECK REACHES. `route.py` asks "does an artifact
        # already cover these words"; `overlapping()` asks "does this share tags with one". Both
        # ask whether a DUPLICATE exists. Neither asks whether this should be a doc AT ALL rather
        # than a section of one - and a doc that belongs inside an existing artifact passes both
        # cleanly while still being sprawl. That judgement needs cross-lane context a lane
        # structurally does not have.
        #
        # ⛔ WORDED AS A RULE, NOT AS AN INSTRUCTION TO WHOEVER IS READING - and the human is the
        # reason. The first version opened "IF YOU ARE A LANE, THIS DOC IS NOT YOURS TO LAND",
        # printed to every session, and I priced the cost of that as "an orchestrator reads a
        # paragraph that does not apply and skips it". He asked how certain we are that they
        # skip it, when the whole point of this workstream is determinism.
        #
        # ⭐ HE IS RIGHT, AND THE COST WAS MISPRICED RATHER THAN SMALL. An instruction shown to
        # someone it does not apply to is not neutral: an orchestrator reading "this doc is not
        # yours to land" might decline to land a doc that IS theirs. That is the same shape as a
        # false facet - wrong guidance carrying the tool's authority. Stating the RULE is true
        # for both readers and misleads neither.
        #
        # ⚠️ IT CANNOT BE SHOWN ONLY TO LANES, AND THIS IS NOW MEASURED RATHER THAN ASSUMED. The
        # desktop app saves one JSON file per session under
        # `AppData/Roaming/Claude/claude-code-sessions/`, holding that session's title, which is
        # how `orchdoc_stop_check.py` identifies orchestrators. Ran a headless `claude -p` and
        # counted: 1354 files before, 1354 after. A headless lane gets NO file, so title-based
        # detection would find nothing and conclude "not a lane" for exactly the sessions that
        # are one.
        #
        # ⭐ THE DETERMINISTIC HALF IS NOT HERE AT ALL - it is in the lane seed, which the
        # orchestrator writes and which therefore KNOWS the answer without inferring it. See the
        # `orchestrating-parallel-sessions` skill. This message is the backstop for a lane whose
        # seed did not say it.
        print("            THE RULE (the human, 2026-09-08): lanes may DRAFT a new knowledge doc,")
        print("            only orchestrators may LAND one. A lane that reaches this refusal")
        print("            reports the draft to its orchestrator with why it believes the")
        print("            subject is new - it does not use --no-verify and does not add")
        print("            <!-- unindexed --> to get past it.")
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


#: failure-value: None   # what it returns when it CANNOT TELL - o8's rule, o9:F148
def undurable(index_text):
    """Docs the INDEX names that exist on NO pushed branch - i.e. on one laptop only.

    ⛔ THE FAILURE A PRE-COMMIT HOOK STRUCTURALLY CANNOT CATCH. Gate 2 fires when a file is
    STAGED. A file nobody ever staged is invisible to it forever. Measured 2026-08-12:
    `orchdoc-template-design.md` and `sop-router-design.md` sat untracked for a day while
    DEV-DOCS-INDEX carried rows pointing at them. A disk failure takes the docs and leaves
    the map pointing at nothing.

    ⭐ Autosave is NOT the safety net people assume. It force-pushes to rolling
    `autosave/YYYY-MM-DD` branches that self-prune after ~3 days, so it is a three-day
    reprieve, not durability.

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

    ⛔ DELIBERATELY NOT the full audit. `--audit` also reports reachability gaps, which are
    routinely non-zero because other sessions' in-progress files sit in the tree. A daily step
    that is usually red trains everyone to skip it, and then the one line that mattered scrolls
    past with the noise. This exits non-zero for exactly one thing: an indexed doc that exists
    on no pushed branch, i.e. work that a dead disk deletes.
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
    print("    Autosave is not cover: it prunes after ~3 days.")
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


# ⛔ BLOCK ONLY ON THE EXPLICIT REFUSAL CODE. `|| exit 1` blocks on ANY non-zero exit, which
# includes this file failing to PARSE - and on 2026-08-12 it did, from a bad docstring edit,
# which took every commit in the workspace down until it was noticed.
#
# ⭐ The try/except around main() cannot cover that: a SyntaxError happens before any of this
# file's code runs, so the fail-open promise was never structural, only conditional on the
# file being loadable. A guard that can hard-fail the thing it guards is not fail-open.
#
# With a dedicated code, only a real refusal blocks. A crash, a syntax error, a missing
# interpreter, a moved file - all warn and allow, which is the direction a guard must fail in.
REFUSE = 9

# ⛔ THE HOOK LINES ARE BUILT FROM THIS FILE'S OWN LOCATION, not typed. Three absolute paths
# were hardcoded here and in install() - in a file whose header says, in as many words, that
# paths are RESOLVED and that a script carrying one machine's absolute path cannot run in a
# skill, a fresh clone, a worktree or the cloud. The header was right and the code disagreed
# with it, which is the worse of the two failures: a reader who trusts the stated invariant
# stops checking.
#
# ⭐ It was found by trying to PUBLISH the file. The sanitiser rewrote all three to a
# placeholder, so the published copy would have installed a hook pointing at a directory that
# does not exist on any machine - a clean-looking file that fails on first use in somebody
# else's repo. `.as_posix()` because a pre-commit hook is run by sh, which wants forward
# slashes even on Windows; on this machine the result is byte-identical to what was typed.
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
    # ⭐ EXTEND, never replace: orchdoc_precommit.py already owns this hook and uses `exec`,
    # which would make anything appended unreachable. Insert BEFORE it, and neuter the exec.
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
    t("a lane SEED is not gated", not in_knowledge_path("o9L9-SEED.md"))
    t("a bootstrap prompt is not gated", not in_knowledge_path("o7-sales-vendor-bootstrap.md"))
    t("an OrchDoc is not gated", not in_knowledge_path("ORCHESTRATOR-DECISIONS-o9.md"))
    t("CLAUDE.md is not gated", not in_knowledge_path("CLAUDE.md"))
    t("a root knowledge doc IS gated", in_knowledge_path("vendor-oauth-homework-2026-07-29.md"))
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
    # ⭐ THE REGRESSION THAT SHIPPED. A move leaves the basename unchanged, so a basename
    # match refuses the commit that CORRECTLY updated the row. Correct behaviour must pass.
    t("rename-and-update in the SAME commit is NOT flagged (the false positive that shipped)",
      stale_rows(["docs/old/a.md"], "see docs/new/a.md here") == [])
    t("a RECORD mentioning it (seed/OrchDoc) is NOT the index, so not blocking",
      stale_rows(["docs/old/a.md"], "") == [])

    # ⛔ THE PAIR THAT MUST DISAGREE. Identical tag overlap, opposite verdicts - the whole
    # claim is that the OVERLAP RULE is right for reference docs and wrong for a thread, so a
    # fixture proving only one half proves nothing. Nine shared tags, same corpus, same floor.
    nine = "voice data promise generated tracks b2 retention privacy policy commitment"
    tagline = "<!-- route-tags: %s -->\n" % nine
    msg = "<!-- correspondence: from=o9 to=o10 -->\n" + tagline
    pool_ref = [("neighbour.md", tagline + "# a reference doc")]
    pool_msg = [("O10-REPLY-thing-2026-08-17.md", msg + "# o10 -> o9")]
    t("two REFERENCE docs sharing 9 tags are still REFUSED (the rule still works)",
      len(overlapping("new.md", tagline, pool_ref)) == 1)
    t("...and the overlap it reports really is 9 tags",
      overlapping("new.md", tagline, pool_ref)[0][1] == sorted(set(nine.split())))
    t("two CORRESPONDENCE docs sharing 9 tags PASS",
      overlapping("O9-TO-o10-thing.md", msg, pool_msg) == [])
    t("a reference doc is not refused for overlapping a MESSAGE (exempt on both sides)",
      overlapping("new.md", tagline, pool_msg) == [])
    t("a message is not refused for overlapping a REFERENCE doc",
      overlapping("O9-TO-o10-thing.md", msg, pool_ref) == [])
    # ⛔ THE OVER-REACH THIS DELIBERATELY DOES NOT COMMIT. A filename regex would exempt any
    # future doc named this way; the marker is what grants it, and the name only suggests it.
    t("the NAME alone does not grant the exemption",
      len(overlapping("O9-TO-o10-thing.md", tagline, pool_ref)) == 1)
    t("...but the name DOES trigger the suggestion in the refusal",
      looks_like_correspondence("O9-TO-o10-thing.md")
      and looks_like_correspondence("O10-REPLY-x-2026-08-17.md")
      and not looks_like_correspondence("commerce-and-external-provider-access.md"))
    t("correspondence is exempt from OVERLAP ONLY - it still needs a door",
      not reachable("O9-TO-o10.md", "<!-- correspondence: from=o9 to=o10 -->", "")[0])

    badge = "> Indexed in [DEV-DOCS-INDEX](./DEV-DOCS-INDEX.md)\n"
    t("a badge with no matching row is caught", false_badge("x.md", badge, "other stuff"))
    t("a badge WITH a matching row is fine", not false_badge("x.md", badge, "row for x.md"))
    t("no badge is never a false badge", not false_badge("x.md", "# t", ""))

    # the contract must round-trip: what we WRITE must be what we MATCH.
    h = tombstone_header("docs/new.md", "2026-08-12", "o9", "New", "moved")
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
