#!/usr/bin/env python3
"""Phase 3: generate the knowledge MAP - the cornerstone tree, and what is known under each.

<!-- route-tags: knowledge map subject cornerstone taxonomy index pages generate tree hierarchy what do we know gaps phase3 -->
<!-- cornerstone: knowledge-routing -->

WHAT PHASES 1-2 COULD NOT ANSWER
--------------------------------
`route.py` answers *"where does X live?"* by ranking the corpus, and it works - both of the
lost vendor-process docs rank first for the queries that missed them. It cannot answer
**"what do we know about subject X, and what is MISSING?"** because a ranked list has no
notion of coverage. A session
that gets three hits cannot tell whether that is everything or the tip of something.

⭐ THE TREE IS CURATED; CONTENTS ARE GENERATED. Measured before choosing: 30 route-tagged
artifacts carry 251 distinct tags, almost all singletons. A page per tag would emit ~251 pages
holding one row each - noise wearing a map's clothes, and worse than no map because it looks
complete. So the SHAPE is authored (`knowledge_subjects.json`) and the CONTENT is derived - every
artifact whose tags or FACETS intersect a node. Adding a node is a deliberate act; filling one
is not.

⛔ THE FIRST EIGHT SUBJECTS WERE DERIVED FROM TAG FREQUENCY, WHICH IS A MEASURE OF THE PAST. That
taxonomy feels a bit shotgun."* A taxonomy is a claim about the business; counting what happened
to get written agrees with that only by accident.

⛔ OWNERSHIP IS SINGULAR; APPEARANCE IS PLURAL. So `route.py` NEVER restricts by cornerstone (a
node affects RANKING, never SCOPE), and every page carries three buckets: owned here, owned
elsewhere, and **owned by nobody** - the last one printed rather than hidden, because it is the
worklist.

⛔ NEVER HAND-EDIT A GENERATED PAGE. Every file this writes carries the banner, and a rerun
overwrites it. Curate `knowledge_subjects.json` beside this file; that is the one hand-authored
surface.

    knowledge_pages.py            report what WOULD change (default, writes nothing)
    knowledge_pages.py --write    regenerate docs/knowledge/
    knowledge_pages.py --gaps     nodes with nothing filed, and artifacts with no owner
    knowledge_pages.py --selftest the tree's own invariants, incl. the migration guard

Exit 0 = generated view matches disk. 1 = drift (or would change).
"""
import argparse
import json
import pathlib
import re
import sys

# Paths are RESOLVED, not hardcoded - see workspace_paths.py.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from workspace_paths import WS, MEM  # noqa: E402
try:
    from no_window import flags  # noqa: E402
except ImportError:  # a partial delivery must not take the tool down
    def flags(extra=0):
        return extra

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OUT = WS / "docs" / "knowledge"
from mentions import ROUTE_TAGS as TAG_RE  # noqa: E402 - ONE definition, see that module
from mentions import declaration_region, declares, outside_fences  # noqa: E402
import private_folders  # noqa: E402 - the folders outsiders write, ONE definition
import leftover_files  # noqa: E402 - untracked files origin/main deleted, ONE definition
try:
    import generated_writes  # noqa: E402 - what --write put on disk, ONE definition
except ImportError:  # a partial delivery must not stop a write; the copy is just not recorded
    generated_writes = None

# ⛔ ONE DEFINITION OF THE OWNERSHIP MARKER, IMPORTED BY EVERY READER. A marker format is a
# contract between every tool that reads it, and three copies of a regex is three chances to
# drift. `route.py` and `knowledge_gate.py` import this rather than carrying their own.
#
# ⭐ BOTH SPELLINGS ARE READ; `cornerstone` IS THE ONE TO WRITE. The human's word for a node is
# cornerstone; the marker already deployed on nine artifacts and taught in CREATE.md says
# `subject`. Rewriting nine files would be cheap, but the taught vocabulary lives in four places
# and a rename that misses one is a silent un-declaration - the document keeps a line that looks
# like a declaration and expands to nothing. An alternation costs nothing and cannot miss.
MARKER_RE = re.compile(r"<!--\s*(?:cornerstone|subject):\s*([a-z0-9-]+)\s*-->")

# ⭐ A SOURCE OF TRUTH DECLARES ITSELF, IN THE SAME PLACE AND THE SAME SHAPE AS ITS CORNERSTONE.
# `<!-- sot: legal -->` says "for this domain, this document is authoritative". The map is then a
# VIEW over the declarations, which is the human's index rule: never a hand-kept list.
#
# ⛔ THE DOMAIN IS NOT THE CORNERSTONE. A cornerstone is where a document is FILED; a SOT
# domain is what it is authoritative FOR, and one cornerstone can hold several (legal holds the
# privacy SOT; research-and-science holds both the science bank and the sourcing hub). Keeping
# them separate is what lets the duplicate check mean something.
SOT_RE = re.compile(r"<!--\s*sot:\s*([a-z0-9-]+)\s*-->")


def sot_of(text, path=None):
    """The domain this artifact DECLARES itself the source of truth for, or None.

    ⛔ VIA `declares`, NOT A BARE SEARCH - and the first version got this wrong in the most
    on-the-nose way available. It used `SOT_RE.search` over the whole file, so THIS FILE
    matched its own docstring example and the generator listed ITSELF as the source of truth for
    `legal`. **A documented example counted as an instance** - the third time in one day, after
    a docstring re-arming the check it documents and a marker table counting its own rows.

    `declares` answers the question the same way for every reader: inside the declaration
    region, outside fences.
    """
    return declares(text or "", SOT_RE, path) or None

# ⛔ THE MAP READ WORKING-TREE FILES, so every row was a fact about one machine at one moment
# rather than about the repository. A row backed by a committed marker and a row backed by an
# unsaved edit were INDISTINGUISHABLE in the output.
#
# ⛔ AND IT HAS ALREADY BEEN WRONG TWICE, SILENTLY BOTH TIMES.
#
# The ref is the repo's own default branch, resolved per artifact, because the corpus spans
# several repos (the workspace plus the nested product repos) and each has its own.
_SOT_STATE_CACHE = {}


def _git(args, cwd):
    import subprocess
    try:
        r = subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True,
                           timeout=30, creationflags=flags())
    except (OSError, ValueError):
        return None
    except Exception:
        return None
    if r.returncode != 0:
        return None
    return r.stdout.decode("utf-8", "replace")


def _default_ref(root):
    """The repo's default remote branch, as a ref that resolves here."""
    if root in _SOT_STATE_CACHE:
        return _SOT_STATE_CACHE[root]
    ref = None
    out = _git(["symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"], root)
    if out and out.strip():
        ref = out.strip().replace("refs/remotes/", "", 1)
    if not ref:
        for cand in ("origin/main", "origin/master"):
            if _git(["rev-parse", "--verify", "--quiet", cand + "^{commit}"], root):
                ref = cand
                break
    _SOT_STATE_CACHE[root] = ref
    return ref


def committed_sot(path, domain):
    """Does the DEFAULT BRANCH's copy of this file declare the same sot domain?

    Returns (state, detail) where state is "committed", "uncommitted" or "unknown".

    ⭐ IT CALLS `sot_of` ON THE COMMITTED BLOB rather than re-deriving the marker with a
    regex. One reader, two texts.

    ⛔ "unknown" IS NOT "fine". An answer this cannot get is reported, never swallowed - the
    whole defect being fixed is a row that looks settled while resting on nothing.
    """
    p = pathlib.Path(path)
    root = _git(["rev-parse", "--show-toplevel"], p.parent)
    if not root or not root.strip():
        return "unknown", "not inside a git repository"
    root = pathlib.Path(root.strip())
    ref = _default_ref(root)
    if not ref:
        return "unknown", "no origin/HEAD, origin/main or origin/master in %s" % root.name
    try:
        rel = p.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return "unknown", "cannot express the path relative to %s" % root.name
    blob = _git(["show", "%s:%s" % (ref, rel)], root)
    if blob is None:
        return "uncommitted", "no such file on %s - the artifact itself is uncommitted" % ref
    if sot_of(blob, path) == domain:
        return "committed", ref
    return "uncommitted", "the marker is absent from %s, so it exists only in a working tree" % ref


def sot_states(arts):
    """{id(artifact): (state, detail)} for the artifacts that DECLARE - and only those.

    One git call or three per declaring artifact, of which there are a handful; running this
    over the whole ~1000-artifact corpus would cost a process per file to be told "no marker".
    """
    return {id(a): committed_sot(a["path"], a["sot"])
            for a in arts if a.get("sot")}


# ⛔ A MARKER INSIDE A CODE FENCE IS AN EXAMPLE, NOT A CLAIM - the same distinction route.py
# already draws for miss receipts, and for the identical reason: ANY doc that TEACHES a marker
# contains that marker, so the difference has to be structural rather than a special case per
# file. A fence means "here is what one looks like"; a bare line means "here is mine".
#
# The seed quoted the three markers a new cornerstone must carry, in a fenced block, exactly as
# a seed should - and the generator read the illustration as a declaration. The page was wrong
# the moment it was first correct: a launch INPUT appeared as an artifact the node keeps
# current. Cost of the fix is a false negative on a marker somebody fences by accident, which is
# the survivable direction.
FENCE_RE = re.compile(r"^(```|~~~).*?^\1", re.S | re.M)

# Door 3 of the gate, used honestly: a generated VIEW is not a source, and routing someone
# here instead of to the artifact it lists would put a middleman in front of the answer.
BANNER = ("<!-- GENERATED by .shared/scripts/knowledge_pages.py - do not hand-edit. "
          "Curate knowledge_subjects.json beside that script instead. -->\n"
          "<!-- unindexed: generated view of other artifacts - route to the SOURCES, "
          "not to this page -->")


# ⛔ MECHANISM HERE, THE TREE IN knowledge_subjects.json - the same ENGINE/VOCABULARY split
# facets.py was forced into. The node names, their one-line descriptions and their alias sets
# are this workspace's own vocabulary: a corpus with different vendors and different products
# needs a different tree, and hardcoding one here means the generator can only ever run against
# the corpus it was born in.
#
# ⭐ It is also how a node stops being a code edit. Adding one is now editing a data file - no
# Python, no risk of breaking the harvester on the way past - which is exactly the property
# "curate the SHAPE, derive the CONTENT" was after in the first place.
#
# The file's absence is a REAL STATE, not an error: no tree means no map, and the generator says
# so and writes nothing (see main()). Raising here would take down anything importing this module
# for its BANNER, which includes facets.py and therefore every commit gate.
def _load_tree():
    """{slug: (parent-or-None, description, [own aliases])} - empty if the vocabulary is absent."""
    p = pathlib.Path(__file__).resolve().parent / "knowledge_subjects.json"
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {k: (v.get("parent"), v.get("description", ""),
                list(v.get("aliases", [])) + list(v.get("page_only_aliases", [])))
            for k, v in raw.items() if not k.startswith("_")}


def _load_page_only():
    """{slug: {word}} - words that list a document on the node's page but do not reach search.

    An alias does two jobs: `buckets` uses it to list documents on a page, and route.py
    gives it to every document declaring the node, at weight 3. A vendor word on a node as
    large as `infrastructure` should do only the first.
    """
    p = pathlib.Path(__file__).resolve().parent / "knowledge_subjects.json"
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {k: set(v.get("page_only_aliases", []))
            for k, v in raw.items() if not k.startswith("_")}


CORNERSTONES = _load_tree()
PAGE_ONLY = _load_page_only()

# ⛔ WHERE THE VENDORS WENT, AND WHY THEY ARE NOT NODES. The owner's shape asked for
# Infrastructure with a sub-cornerstone per vendor - the deploy platform, the GPU host, the
# object store, the CDN, the analytics tools and others. Those
# vendors ALREADY exist as a registry - `facets_vocabulary.json` holds 15 platforms with their
# alias lists, detected automatically and declared on 182 artifacts. Making them nodes too
# creates TWO places to add a vendor, and the day someone adds one to the vocabulary and not the
# taxonomy the map is lying. That is the drift the "an index must be generated" rule exists to
# stop, arriving through a side door.
#
# ⭐ So the vendor breakdown is GENERATED into this node's page from the facet table.
# Capabilities (secrets, deploys, backup, tooling) stay as authored children because they cut
# ACROSS vendors and so cannot be expressed as vendor sections at all.
VENDOR_PARENT = "infrastructure"


def children(slug):
    return sorted(k for k, v in CORNERSTONES.items() if v[0] == slug)


def tops():
    return sorted(k for k, v in CORNERSTONES.items() if v[0] is None)


def own_aliases(slug):
    """The node's authored vocabulary, plus its own slug.

    ⛔ A NODE MUST ANSWER TO ITS OWN NAME, and two did not. `offer-page` and `orchestration` are
    also declared COMPONENT facet values, and neither listed its own slug among its aliases - so
    twenty-nine artifacts declaring `<!-- component: offer-page -->` matched the offer-page node
    on nothing. Requiring an author to repeat the slug they just typed as the key is the same
    bet that already failed everywhere else in this system; the machine adds what it can see.
    """
    return set(CORNERSTONES[slug][2]) | {slug}


def subtree_aliases(slug):
    """Own vocabulary plus every child's.

    ⛔ A PARENT DECLARATION MUST NOT BE NARROWER THAN ITS CHILDREN. The deploy platform's
    auth note declares `infrastructure`, and route fixture #4 depends on it ranking for the
    three words of that platform, auth and token - all three now live on CHILD nodes. Without the union,
    a reorganisation that moved vocabulary downward would silently un-find every document
    declaring the parent, and NO fixture names a document by the vocabulary it INHERITS, so
    nothing else in the suite could see the loss.
    """
    s = own_aliases(slug)
    for c in children(slug):
        s |= own_aliases(c)
    return s


# ⛔ COMPATIBILITY VIEW, AND IT IS LOAD-BEARING. `route.py` reads `SUBJECTS[slug][1]` to expand a
# declaration into its vocabulary at query time. Keeping the name and the shape means the router
# needed no change for the hierarchy at all - a parent resolves to its whole subtree, a child to
# its own branch, and neither end knows a tree exists.
SUBJECTS = {k: (v[1], sorted(subtree_aliases(k) - set().union(*PAGE_ONLY.values())))
            for k, v in CORNERSTONES.items()}


def owner_of(text):
    m = MARKER_RE.search(FENCE_RE.sub("", text or ""))
    return m.group(1) if m else None


def harvest(base=None, skipped=None):
    """Every artifact carrying route-tags, a facet, or an ownership marker, less the LEFTOVERS:
    untracked files whose path origin/main's history deleted (leftover_files.py). `base` and
    `skipped` mean what they mean for dev_docs_index.harvest: the land step's pinned commit
    (strict), or None (origin/main; everything kept when the test cannot run, as before).

    ⭐ FACETS CARRY THE MASS. Membership built from tags alone produced pages that were
    technically correct and nearly empty. A facet says "this document relates to X", which
    is precisely what an "also relevant" section is for.
    """
    # ⛔ THE DECLARED VALUE, NOT ONLY ITS EXPANSION. `facet_aliases` returns the alias PHRASES
    # ("marketing site", the site's hostname), which split into the words `marketing` and `site`
    # - and no node is called either. The declared value is the one token guaranteed to be the
    # vendor's or component's real name, so it is indexed alongside the expansion rather than
    # instead of it.
    try:
        from facets import declared, FACETS
    except Exception:                     # never let a decoration break the map
        FACETS = []

        def declared(_t, _f):
            return set()

    # ⛔ PLATFORM AND COMPONENT CROSS-LIST; ACCESS DOES NOT, AND THE REASON IS MEASURED. An
    # ACCESS facet says HOW a service is reached, which most technical documents mention
    # whatever their subject: 109 of 209 artifacts declare one, and because all eight values are
    # mechanisms they collapse onto a single node. A list that size is not a worklist, it is the
    # corpus wearing one.
    #
    # ⭐ THE VOCABULARY MAPPING STAYS. `webhook` and `cli` remain aliases of that node, so a
    # SEARCH for them still ranks it and a doc declaring it still inherits them. What is
    # withdrawn is only the automatic page MEMBERSHIP - the weaker of the two claims.
    CROSSLIST = ("platform", "component")

    # ⛔ MULTI-WORD ALIASES ARE NOT SPLIT INTO TOKENS, because the halves are not the facet.
    # `component: marketing-site` expands to the phrase "marketing site", and splitting it
    # yields the bare word `marketing` - which is the name of the PARENT node. Result: all 40
    # marketing-site artifacts landed on the marketing parent's own section as well as on the
    # child's, by accident of a space, and a deploy-reliability note read as general marketing
    # material. The declared VALUE is the reliable token; single-word aliases (a product
    # hostname, a vendor's short code, a bare brand word) are safe to add; phrases are dropped.
    def facet_words(text):
        out = set()
        for facet, table in FACETS:
            if facet not in CROSSLIST:
                continue
            for v in declared(text, facet):
                for val in v.replace(",", " ").split():
                    out.add(val.lower())
                    out |= {a.lower() for a in table.get(val, []) if " " not in a}
        return out
    # ⛔ THE SCAN SET COMES FROM corpus.py - it is not this file's to decide, and deciding it
    # here twice is what broke the cornerstone sheet. The candidate sheet built from the 198 was
    # judging 15% of the corpus while reading as a survey of all of it. The human: *"the
    # referenced docs were only just barely better than randomly assigned."*
    #
    # ⭐ The one exclusion this store still ASKS for is a declared slice of that enumeration, not
    # a private list: our own generated pages, because routing to a view instead of a source
    # puts a middleman in front of the answer. An index whose whole value is being current was
    # offering the reader a dead document and a live one as equals.
    #
    # ⭐ THE SHAPE IS IMPORTED, NEVER RE-WRITTEN. `knowledge_gate.TOMBSTONE_OPEN` is the contract,
    # and its own docstring says it is imported by anything that reads a tombstone. A second
    # regex here would be a second implementation of one format, which is how a marker format
    # silently stops meaning the same thing to two readers.
    #
    # ⚠️ THIS REMOVES IT FROM THE MAP, NOT FROM THE CORPUS. `route.py` still finds the old words
    # and forwards to the replacement, which is the entire point of keeping the text. What is
    # withdrawn is only the claim "here is a document about this subject".
    try:
        from knowledge_gate import TOMBSTONE_OPEN
    except Exception:                     # never let a decoration break the map
        TOMBSTONE_OPEN = None

    from corpus import files as corpus_files
    out = []
    tombstoned = []
    private = 0
    for kind, p in corpus_files(include_generated=False):
        if private_folders.has_private_part(p):
            private += 1
            continue
        try:
            t = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # A receipt, not a silent drop: the count is printed, so a page that shrinks says why.
        if TOMBSTONE_OPEN is not None and TOMBSTONE_OPEN.search(t):
            tombstoned.append(p)
            continue
        # ⛔ A DECLARATION IS NOT "A MARKER NEAR THE TOP". This read was `[:8000]`, route.py's
        # was 3000 and knowledge_gate's 4000, so one doc could be routable and absent from the
        # knowledge map with nothing reporting the disagreement. `declares` answers the question
        # once, for every reader. Measured across 853 artifacts: 0 docs gained, 5 lost - and all
        # five are non-declarations (a generator's string literal, a comment naming the marker,
        # a test fixture). No artifact that genuinely declares loses anything.
        decl = declares(t, TAG_RE, p)
        tags = {w.lower().strip(",.") for w in decl.split()} if decl else set()
        # ⚠️ FACETS STAY WINDOWED. They are derived from PROSE, not declared, so reading further
        # changes what they mean rather than fixing where they were found.
        fac = facet_words(t[:8000])
        owner = owner_of(t)
        sot = sot_of(t, p)
        if tags or fac or owner or sot:
            out.append({"kind": kind, "path": p, "tags": tags, "facets": fac,
                        "owner": owner, "sot": sot})
    if tombstoned:
        print("   %d tombstoned artifact(s) excluded from the map (route.py still forwards them):"
              % len(tombstoned))
        for p in tombstoned:
            print("      %s" % p.name)
    if private:
        print("   %d file(s) under a folder named %s skipped unread (outsiders' text)"
              % (private, " or ".join(private_folders.NAMES)))
    # from corpus.py, which stays as it is, so leftovers are dropped here, after the harvest:
    # a doc deleted or moved on origin/main that deliver.py left on disk would otherwise come
    # back as a page entry, and in every count on INDEX.md.
    left = []
    out = leftover_files.drop(out, WS, base, left)
    if left:
        print(leftover_files.note(len(left)))
    if skipped is not None:
        skipped.extend(left)
    return out


def buckets(slug, arts, direct_only=False):
    """(owned_here, owned_elsewhere, unowned) for one node.

    `direct_only` renders a PARENT's own section without repeating everything its children
    already list: relevance uses the node's own vocabulary, while ownership still rolls the
    subtree up, so a parent page can never hide a document its own branch owns.
    """
    al = own_aliases(slug) if direct_only else subtree_aliases(slug)
    mine = {slug} | set(children(slug))
    here, elsewhere, unowned = [], [], []
    for a in arts:
        if a["owner"] in mine:
            here.append(a)
            continue
        if not (a["tags"] | a["facets"]) & al:
            continue
        (elsewhere if a["owner"] in CORNERSTONES else unowned).append(a)

    def key(a):
        return str(a["path"]).lower()
    return sorted(here, key=key), sorted(elsewhere, key=key), sorted(unowned, key=key)


def _link(a):
    """A clickable link for anything inside the repo; PLAIN TEXT for anything outside it.

    ⛔ TWO LINK BUGS, AND THE SECOND IS THE ONE WORTH STATING. (1) A workspace-relative path
    was emitted verbatim from a page living two directories down, so every root-level
    document linked to `docs/knowledge/<name>` and 404'd.

    ⭐ A BROKEN LINK IS WORSE THAN NO LINK, so the second case is not "fixed" with a deeper
    relative path that happens to work on one laptop. It is rendered as plain text, which is
    honest about the store being external, and `route.py <name>` reaches it from anywhere.
    """
    p = a["path"]
    try:
        rel = str(p.relative_to(WS)).replace("\\", "/")
        return "`%-7s` [%s](../../%s)" % (a["kind"], p.name, rel)
    except ValueError:
        pass
    try:
        rel = str(p.relative_to(MEM)).replace("\\", "/")
        return "`%-7s` %s  `memory/%s`" % (a["kind"], p.name, rel)
    except ValueError:
        # Third case, arrived with the corpus widening: a first-party skill installed under the
        # plugin marketplace lives outside BOTH stores. Same posture as the memory case - name
        # it plainly rather than emit a relative path that resolves on one laptop.
        return "`%-7s` %s  `%s`" % (a["kind"], p.name, p.parent.name)


def _rows(title, arts, note, show_owner=False):
    if not arts:
        return []
    L = ["**%s** (%d) - %s" % (title, len(arts), note), ""]
    for a in arts:
        tail = "  -> owned by `%s`" % a["owner"] if show_owner and a["owner"] else ""
        L.append("- %s%s" % (_link(a), tail))
    L.append("")
    return L


def _section(slug, arts, direct_only=False):
    here, elsewhere, unowned = buckets(slug, arts, direct_only)
    L = ["## %s" % slug.replace("-", " ").title(), "", CORNERSTONES[slug][1], "",
         "**Searched as:** %s" % ", ".join("`%s`" % a for a in sorted(own_aliases(slug) - PAGE_ONLY.get(slug, set()))), ""]
    if not (here or elsewhere or unowned):
        # ⭐ An empty node is INFORMATION, not an omission to hide. "Nobody has written this
        # down" is exactly the answer a session needs, and the whole failure this workstream
        # exists to fix is a session concluding absence without evidence.
        L += ["*Nothing is filed here yet - and that is a finding, not a gap in this page.* "
              "If you are looking for this and it matters, you are the first: write it down, "
              "then declare `<!-- cornerstone: %s -->` (see [CREATE.md](CREATE.md))." % slug,
              ""]
        return L
    L += _rows("Owned here", here, "this node keeps these current", show_owner=True)
    L += _rows("Also relevant, owned elsewhere", elsewhere,
               "findable from here, maintained by another node", show_owner=True)
    L += _rows("Also relevant, NO OWNER DECLARED", unowned,
               "matches this node and nobody has claimed it. **This is the worklist.**")
    return L


def _vendor_rows(arts):
    """the human's vendor sub-cornerstones, derived from the facet vocabulary rather than authored."""
    try:
        from facets import PLATFORMS, declared
    except Exception:
        return []
    counts = {}
    for a in arts:
        try:
            t = a["path"].read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Same rule as the tag read above: a facet declared past 8000 characters was declared.
        # The region is computed once and handed to `declared`, so this reader and the router
        # cannot disagree about what counts as a declaration.
        for v in declared(outside_fences(declaration_region(t, a["path"])), "platform"):
            for w in v.replace(",", " ").split():
                counts[w] = counts.get(w, 0) + 1
    L = ["## Vendors", "",
         "Generated from `facets_vocabulary.json`, never authored here - a vendor is a FACET, "
         "not a cornerstone, so there is exactly one place to add one. A platform showing "
         "**0** is the finding: it is in the vocabulary and nobody has written it down.", "",
         "| platform | artifacts declaring it | read them |", "|---|---|---|"]
    for v in sorted(PLATFORMS):
        n = counts.get(v, 0)
        L.append("| `%s` | %s | `route.py %s` |" % (v, n if n else "**0**", v))
    L.append("")
    return L


def unknown_declarations(arts):
    """Ownership markers naming a node that does not exist.

    ⛔ A TYPO IN THE HIGHEST-VALUE LINE IN A DOCUMENT USED TO BE INVISIBLE. `<!-- cornerstone:
    infastructure -->` matches no node, expands to no vocabulary, and reads on every page exactly
    like a document nobody has claimed - so the author's one deliberate act silently did nothing,
    and the only surface that could have said so is the map.
    """
    return sorted({(a["owner"], a["path"].name) for a in arts
                   if a["owner"] and a["owner"] not in CORNERSTONES})


def render(slug, arts):
    L = [BANNER, "", "# %s" % slug.replace("-", " ").title(), ""]
    kids = children(slug)
    L += _section(slug, arts, direct_only=bool(kids))[2:]
    if kids:
        L += ["---", ""]
        for c in kids:
            L += _section(c, arts)
    if slug == VENDOR_PARENT:
        L += _vendor_rows(arts)
    return L


def render_index(arts):
    L = [BANNER, "", "# Knowledge map", "",
         "The cornerstone tree. **Generated** - curate `knowledge_subjects.json` beside "
         "`.shared/scripts/knowledge_pages.py`, never these files. Design record, with every "
         "disagreement argued: "
         "the taxonomy spec.", "",
         "Do not read this to find ONE thing - `route.py <terms>` is faster and searches the "
         "whole corpus. **A cornerstone affects RANKING, never SCOPE**: no search is ever "
         "confined to a branch, so no document can be missed by guessing the wrong one. Read "
         "this to find out what EXISTS about a subject, and what does not.", "",
         "| cornerstone | owned | also relevant | unowned | what it covers |",
         "|---|---|---|---|---|"]
    for t in tops():
        h, e, u = buckets(t, arts)
        L.append("| [%s](%s.md) | %s | %d | %d | %s |"
                 % (t, t, len(h) if h else "**0**", len(e), len(u), CORNERSTONES[t][1]))
        for c in children(t):
            ch, ce, cu = buckets(c, arts)
            L.append("| &nbsp;&nbsp;&#8627; [%s](%s.md#%s) | %s | %d | %d | %s |"
                     % (c, t, c, len(ch) if ch else "**0**", len(ce), len(cu),
                        CORNERSTONES[c][1]))
    owned = len([a for a in arts if a["owner"] in CORNERSTONES])
    L += ["",
          "_%d artifacts carry a route-tag, a facet or an ownership marker. **%d declare an "
          "owner.** That gap is the worklist, not a defect in the tree's shape - see section 9 "
          "of the spec._" % (len(arts), owned), ""]
    bad = unknown_declarations(arts)
    if bad:
        L += ["## Declarations naming a node that does not exist", ""]
        L += ["- `%s` in `%s`" % (s, n) for s, n in bad]
        L += [""]
    L += render_sot_map(arts)
    return L


def sot_rows(arts):
    """[(domain, [artifacts])] - the declarations, grouped. A domain with two claimants is the
    finding this exists to surface."""
    by = {}
    for a in arts:
        if a.get("sot"):
            by.setdefault(a["sot"], []).append(a)
    return sorted(by.items())


def render_sot_map(arts):
    """The sources of truth, as a VIEW over what each one declares about itself.

    ⭐ WHY IT LIVES IN THE KNOWLEDGE MAP AND NOT IN ITS OWN FILE. A source of truth is the
    document you read FIRST about a subject, which is what a cornerstone already means. This is
    the cornerstone tree with one added assertion - **and this one is authoritative** - so a
    second tree would only be something to keep in step with the first.
    """
    rows = sot_rows(arts)
    L = ["", "---", "", "## Sources of truth", "",
         "**Generated from a `<!-- sot: <domain> -->` marker declared ON each document.** For "
         "these subjects there is ONE authoritative document, and every other mention is a "
         "consumer of it. If what you need is not here, no document has claimed the title - "
         "which is a finding, not a gap.", "",
         "⚠️ **Run the generator from the FULL checkout, never a worktree.** A worktree "
         "holds only tracked files and does not contain the nested product repos, so "
         "regenerating there silently DROPS rows - measured at 4 lost across 5 pages before "
         "this note existed. The corpus reaches 996 artifacts from the main checkout and 801 "
         "from a worktree.", ""]
    if not rows:
        L += ["_No document declares itself a source of truth yet._", ""]
        return L
    states = sot_states(arts)
    _MARK = {"committed": "committed",
             "uncommitted": "\u26d4 **UNCOMMITTED**",
             "unknown": "\u26a0\ufe0f unknown"}
    L += ["| domain | the source of truth | filed under | marker |",
          "|---|---|---|---|"]
    for domain, group in rows:
        for a in group:
            st, _why = states.get(id(a), ("unknown", "not checked"))
            L.append("| `%s` | %s | %s | %s |"
                     % (domain, _link(a), a.get("owner") or "-", _MARK[st]))

    # ⛔ LOUD, AND ABOVE THE DUPLICATES BLOCK. An uncommitted marker is worse than a duplicate:
    # a duplicate is visible and wrong, an uncommitted one DISAPPEARS and takes its row with it,
    # leaving a map that looks clean. The human's "ESPECIALLY with any SOT" is that priority order.
    bad = [(d, a, states[id(a)]) for d, g in rows for a in g
           if states.get(id(a), ("unknown",))[0] != "committed"]
    if bad:
        L += ["", "\u26d4 **%d row(s) rest on a marker that is NOT committed on its repo's "
              "default branch.** Discard that working-tree edit and the row disappears with "
              "no error anywhere - and if the row was one of two claimants, what is left "
              "reports a single source of truth and looks correct. Commit the marker."
              % len(bad), ""]
        for d, a, (st, why) in bad:
            L.append("- `%s`: %s - %s (%s)" % (d, _link(a), _MARK[st], why))
        L += [""]

    dupes = [(d, g) for d, g in rows if len(g) > 1]
    if dupes:
        L += ["", "⛔ **%d domain(s) have MORE THAN ONE claimant.** Two documents both "
              "asserting the title for one subject is the sprawl a source of truth exists to "
              "end, and until it is resolved a reader landing on either has no way to know the "
              "other exists." % len(dupes), ""]
        for d, g in dupes:
            L.append("- `%s`: %s" % (d, ", ".join(_link(a) for a in g)))
        L += [""]
    return L


def cmd_gaps(arts):
    print("knowledge gaps - what the tree has no content for, and what has no owner")
    print()
    empty = [n for n in sorted(CORNERSTONES) if not any(buckets(n, arts))]
    print("  nodes: %d (%d top-level, %d children)   EMPTY: %d"
          % (len(CORNERSTONES), len(tops()), len(CORNERSTONES) - len(tops()), len(empty)))
    for n in empty:
        print("     - %s" % n)
    print()
    unowned = [x for x in arts if x["owner"] not in CORNERSTONES]
    print("  artifacts with NO ownership marker: %d of %d" % (len(unowned), len(arts)))
    for x in unowned[:10]:
        print("     - %s" % x["path"].name)
    bad = unknown_declarations(arts)
    if bad:
        print()
        print("  declarations naming a node that does not exist:")
        for s, n in bad:
            print("     - %s  in %s" % (s, n))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--gaps", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return cmd_selftest()

    # ⛔ NO TREE MEANS WRITE NOTHING, and the reason is that the alternative is destructive.
    # With an empty tree there are no pages, so an unguarded `--write` would rewrite INDEX.md as
    # an empty table AND delete every page as retired - a generator erasing the map because its
    # vocabulary file was missing. "Detects nothing" is the right posture for a reader; for a
    # WRITER the equivalent is to decline, loudly, and leave what is on disk alone.
    if not CORNERSTONES:
        print("knowledge map - no cornerstone tree found "
              "(knowledge_subjects.json beside this script).")
        print("   Nothing generated, and nothing on disk was touched. Add the file to curate")
        print("   a tree; its absence is a real state, not a failure.")
        return 0

    arts = harvest()
    if a.gaps:
        return cmd_gaps(arts)

    files = {OUT / ("%s.md" % t): render(t, arts) for t in tops()}
    files[OUT / "INDEX.md"] = render_index(arts)

    changed, removed = [], []
    for path, lines in files.items():
        new = "\n".join(lines) + "\n"
        old = path.read_text(encoding="utf-8", errors="replace") if path.exists() else None
        if old != new:
            changed.append(path)
            if a.write:
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "w", encoding="utf-8", newline="") as fh:
                    fh.write(new)
                if generated_writes:
                    generated_writes.record(WS, path.relative_to(WS).as_posix(), new)

    # ⛔ A RETIRED NODE MUST TAKE ITS PAGE WITH IT. `lead-magnets` became a child of `marketing`,
    # and its old top-level file would otherwise sit there generated, banner-stamped and never
    # updated again - a stale index answering a reader's question falsely, which is the whole
    # failure the "an index must be generated" rule exists to prevent. Only files carrying OUR
    # banner are removed; CREATE.md is hand-written and carries none.
    if OUT.exists():
        for p in sorted(OUT.glob("*.md")):
            if p in files:
                continue
            try:
                head = p.read_text(encoding="utf-8", errors="replace")[:60]
            except OSError:
                continue
            if head.startswith("<!-- GENERATED by .shared/scripts/knowledge_pages.py"):
                removed.append(p)
                if a.write:
                    p.unlink()

    print("knowledge map - %d node(s) (%d top-level), %d artifact(s) scanned, %d declare an owner"
          % (len(CORNERSTONES), len(tops()), len(arts),
             len([x for x in arts if x["owner"] in CORNERSTONES])))
    for p in sorted(changed):
        print("   %s %s" % ("wrote" if a.write else "WOULD CHANGE", p.name))
    for p in sorted(removed):
        print("   %s %s (node retired)" % ("removed" if a.write else "WOULD REMOVE", p.name))
    for s, n in unknown_declarations(arts):
        print("   %s declares unknown cornerstone `%s`" % (n, s))
    if not changed and not removed:
        print("   every page matches what the corpus says.")
        return 0
    if not a.write:
        print("\n  %d page(s) would change. Fix: knowledge_pages.py --write"
              % (len(changed) + len(removed)))
        return 1
    return 0


# A MIGRATION fact, not part of the tree: the tree may be re-authored freely, and this must
# still refuse to lose a word.
#
# ⛔ BUT IT IS STILL VOCABULARY - the pre-rewrite tree's own slugs and alias words, this
# workspace's vendors among them. So it lives beside the tree it describes, under a `_legacy`
# key: `_load_tree()` already skips underscore-prefixed keys, so recording it there cannot add
# a phantom node. Keeping it in code would have re-pinned the engine to one workspace's
# vocabulary the day after the engine was freed of it.
#
# ⭐ AND AN ABSENT RECORD MUST NOT READ AS A PASS. With no `_legacy` there is nothing to
# compare against, and an assertion over an empty set is vacuously true - the "0 examined and
# 3 examined, all clean print the same line" defect this workstream keeps finding. The selftest
# says NOT RECORDED instead, which is a different sentence from "no vocabulary was lost".
def _legacy():
    """(slugs, vocab) from the tree file's `_legacy` key - two empty sets if not recorded."""
    p = pathlib.Path(__file__).resolve().parent / "knowledge_subjects.json"
    try:
        raw = json.loads(p.read_text(encoding="utf-8")).get("_legacy", {})
    except Exception:
        return set(), set()
    return set(raw.get("slugs", [])), set(raw.get("vocab", []))


def _legacy_retired():
    """{word: reason} - pre-rewrite words removed ON PURPOSE, each with the commit that did it.

    Recording it here keeps the guard strict for every other word. Removing a word from the
    check instead would blind the guard to the next accidental drop.
    """
    p = pathlib.Path(__file__).resolve().parent / "knowledge_subjects.json"
    try:
        return dict(json.loads(p.read_text(encoding="utf-8")).get("_legacy", {}).get("retired", {}))
    except Exception:
        return {}


LEGACY_SLUGS, LEGACY_VOCAB = _legacy()
LEGACY_RETIRED = _legacy_retired()


def cmd_selftest():
    ok = True

    def t(label, cond):
        nonlocal ok
        print("  [%s] %s" % ("OK " if cond else "FAIL", label))
        ok &= bool(cond)

    print("knowledge_pages selftest")
    t("the tree loaded at all", bool(CORNERSTONES))
    t("every parent named by a child exists",
      all(v[0] is None or v[0] in CORNERSTONES for v in CORNERSTONES.values()))
    # Two levels only: a grandchild would render on no page at all, because render() walks
    # exactly one level down from each top-level node.
    t("the tree is exactly two levels deep",
      all(CORNERSTONES[v[0]][0] is None for v in CORNERSTONES.values() if v[0]))
    t("slugs are lower-kebab, so they are safe as both filenames and anchors",
      all(re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", k) for k in CORNERSTONES))
    t("every node carries a description and at least one alias",
      all(v[1] and v[2] for v in CORNERSTONES.values()))
    t("a parent's vocabulary contains every one of its children's",
      all(subtree_aliases(p) >= own_aliases(c)
          for c, v in CORNERSTONES.items() for p in [v[0]] if p))
    t("the vendor parent is a real node", VENDOR_PARENT in CORNERSTONES)
    # ⛔ THE TWO REGISTRIES MUST NOT DRIFT APART, and this is the ONLY thing that can see it. A
    # facet value with no node alias still gets a row in the generated vendor table, so the page
    # looks complete - while every artifact declaring that facet joins no node's list at all.
    # The failure is silent by construction, which is exactly why it needs an assertion.
    try:
        import facets as _f
        vals = set(_f.PLATFORMS) | set(_f.COMPONENTS) | set(_f.ACCESS)
        vocab = set()
        for k in CORNERSTONES:
            vocab |= own_aliases(k)
        stray = sorted(vals - vocab)
        t("every facet value reaches a node (unmapped: %s)" % (stray or "none"), not stray)
    except Exception as e:
        t("the facet vocabulary is importable for the drift check (%s)" % e, False)
    t("the compatibility view route.py reads is populated for every node",
      all(SUBJECTS[k][1] for k in CORNERSTONES))
    t("the ownership marker reads BOTH spellings",
      owner_of("<!-- cornerstone: app -->") == "app"
      and owner_of("<!-- subject: app -->") == "app")
    # ⛔ A FENCED MARKER IS AN EXAMPLE. The bare line beneath the fence must still be read, or
    # the fix would un-declare any doc that documents itself.
    t("a marker inside a code fence is an example, not a declaration",
      owner_of("```\n<!-- cornerstone: app -->\n```\n") is None
      and owner_of("```\n<!-- cornerstone: app -->\n```\n<!-- subject: legal -->") == "legal")
    # Any node will do, and taking one from the tree rather than naming one is deliberate:
    # a hardcoded slug turns a rename into a selftest failure that says nothing about renaming.
    t("a node with nothing filed renders the finding, not a blank page",
      any("Nothing is filed here yet" in ln
          for ln in _section(sorted(CORNERSTONES)[0], [])))
    t("an unknown declaration is reported rather than silently matching nothing",
      unknown_declarations([{"owner": "infastructure",
                             "path": pathlib.Path("x.md")}]) == [("infastructure", "x.md")])
    # ⛔ THE MIGRATION GUARD. Three artifacts declare a pre-rewrite subject today, and they
    # inherit their vocabulary at QUERY time - so an alias dropped in the rewrite un-finds every
    # document that inherited it, and NO route fixture names a document by inherited vocabulary.
    # This is the only check in the suite that can see that loss.
    if not (LEGACY_SLUGS or LEGACY_VOCAB):
        # Not a pass and not a failure - there is nothing recorded to compare against, and
        # saying so is the whole point. An empty-set assertion would print [OK] and mean it.
        print("  [-- ] migration guard NOT RECORDED - no `_legacy` block in the tree file,")
        print("         so nothing here can tell whether a pre-rewrite alias was lost.")
    else:
        t("every pre-rewrite slug is still declarable (lost: %s)"
          % (sorted(LEGACY_SLUGS - set(CORNERSTONES)) or "none"),
          LEGACY_SLUGS <= set(CORNERSTONES))
        everything = set()
        for k in CORNERSTONES:
            everything |= own_aliases(k)
        lost = sorted(LEGACY_VOCAB - everything - set(LEGACY_RETIRED))
        t("no pre-rewrite alias was dropped from the tree (lost: %s; retired on purpose: %s)"
          % (lost or "none", sorted(LEGACY_RETIRED) or "none"),
          not lost)
        # A retired word that is reachable again means the record is stale; fail so it is cleaned.
        back = sorted(set(LEGACY_RETIRED) & everything)
        t("no retired word is back in the tree (back: %s)" % (back or "none"), not back)
        t("every retired word is a pre-rewrite word with a reason",
          all(w in LEGACY_VOCAB and LEGACY_RETIRED[w] for w in LEGACY_RETIRED))
    page_only = set().union(*PAGE_ONLY.values())
    t("a page-only word lists documents on its node and is not given to search (%s)"
      % (sorted(page_only) or "none"),
      all(w in own_aliases(k) for k in PAGE_ONLY for w in PAGE_ONLY[k])
      and not any(page_only & set(SUBJECTS[k][1]) for k in SUBJECTS))
    print("\nselftest %s" % ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
