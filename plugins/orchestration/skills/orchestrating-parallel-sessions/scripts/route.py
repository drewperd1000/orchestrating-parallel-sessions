"""route.py - the knowledge system / decision tree, phase 1. Enter by SUBJECT; answers carry receipts.

<!-- route-tags: knowledge system decision tree route find process sop where lookup discover -->

WHY THIS EXISTS (the human, 2026-08-11): *"They shouldn't be grepping for answers and latching on to
whatever they find. They need an orderly path to get there. Start at step one, go from there."*

THE FOUNDING CASE this must always solve: a session needed a payment vendor's access process,
searched where a competent orchestrator would look, found nothing, and hand-wrote guidance into
four lane seeds. The answer existed - more complete than what they wrote - in a memory note
**filed under a name that says REFUND**, not under the vendor. Searching the vendor's own name
must land on that note in one command, forever; it is regression fixture #1.

THE CONTRACT - four outcomes now, not three. A 40-agent adversarial workflow confirmed 33 breaks
against the first version, and the deepest one was this tool repeating the exact defect it was
built to kill: **a sub-threshold match had no name**, so "matched but weakly" printed as
VERIFIED MISS - the same missing-third-state that made o10 create duplicate seeds.

  HIT            confident match - read the artifact
  WEAK           matched, below the confidence floor - NAMED and listed, never silent,
                 because "matched weakly" and "nothing exists" are different facts
  PARTIAL        a hit that does not fully answer -> EXTEND that artifact, never create a
                 new one, and `enrich` it with the terms you searched with
  VERIFIED MISS  zero matched tokens across the enumerated corpus -> the CREATE path,
                 carrying this receipt

⭐ SCORING LESSONS BOUGHT BY THE ADVERSARIAL PASS, encoded rather than remembered:
  - WORDS, NOT SUBSTRINGS. 'app' matched inside 'APProval' at full weight and flooded the
    top-8 above the real answer. Tokens now match whole words (with light stemming, so
    'tokens'/'deploying' find 'token'/'deploy' - the plural asymmetry was returning VERIFIED
    MISS for covered subjects).
  - RARITY BEATS WEIGHT. 'iphone support' ranked two notes that never mention iPhone above
    the one artifact in 396 that did - because a generic token scored the same as a
    distinguishing one, and the PARTIAL advice then told the session to enrich the WRONG
    note: the tool's own mechanism manufacturing mis-filed knowledge. A token matching few
    artifacts now outweighs one matching many.
  - DROPPED TOKENS ARE DISCLOSED. 'em' (dash rules) and 'b2' (the bucket) were silently
    discarded by a length filter; short tokens with digits are now kept, and every drop is
    printed. An undisclosed drop makes the receipt a lie.
RARITY SCORING DECAYS AS THE CORPUS GROWS AROUND A TERM - measured 2026-08-12.
The "knowledge system" fixture went red without anyone touching the router. Three specs
ABOUT knowledge were added on 08-11, pushing df("knowledge") past the 12-doc rarity cutoff
to 17 and dropping it to the lowest weight. The canonical knowledge doc then scored below
threshold on its own subject.

So: THE MORE YOU WRITE ABOUT X, THE HARDER THE CANONICAL X DOC IS TO FIND. That is the
opposite of the intuition, and it means a green fixture is a statement about TODAY'S CORPUS,
not a property of the router - fixtures here decay on their own and must be re-run, not
trusted. Fix a decayed one by ENRICHING the artifact (route.py enrich) so it carries rarer
terms. Never by lowering the threshold, which floods every query at once.

⛔ A VERIFIED MISS IS A CLAIM, AND IT WAS THE ONE CLAIM HERE NOBODY EVER RE-TESTED.
Two guards already re-check the CREATE decision at write time: `knowledge_gate.py` refuses a
new doc sharing 3+ tags with an existing one, and refuses one nobody can route to. Both fire
BEFORE the commit and never again. So a doc created on a FALSE miss - the searcher used
vocabulary the existing doc did not carry, and the tag overlap came in under three - stands
forever, and the two copies drift apart with nothing watching.

⭐ So a miss is now a STORED RECEIPT, not a moment (`route.py receipts`):

    <!-- miss-receipt: terms="..." date=YYYY-MM-DD corpus=N -->

The whole test is an AGE COMPARISON. Re-run the receipt's terms against today's corpus: an
artifact that appears and is NEWER than the receipt is normal growth and means nothing. An
artifact that appears and is OLDER than the receipt was already sitting there when the miss
was declared - so the miss was wrong when it was made, and the doc it authorised is a
duplicate. That is the only condition this fails on.
"""
import argparse
import os
import datetime as dt
import json
import pathlib
import re
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Paths are RESOLVED, not hardcoded - see workspace_paths.py. A script carrying one
# machine's absolute path cannot run in a skill, a fresh clone, a worktree or the cloud,
# which is why none of this shipped anywhere until 2026-08-13.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from workspace_paths import WS, MEM  # noqa: E402
# ⛔ THE STORES LIVE IN corpus.py NOW - a second copy of the skill roots here is a second answer
# to "what is the corpus", which is the exact defect that let this router index 1,326 artifacts
# while the knowledge map indexed 198 and neither could see the other's blind spot.
FIXTURES = pathlib.Path(__file__).resolve().parent / "route_fixtures.json"

# Stores that EXIST and are deliberately not indexed. Named in every receipt - an undeclared
# store is the o10 failure reborn, and the census lens found FIVE of them undeclared in v1
# (memory/automation/**, .shared/docs, script subdirs, third-party skills, *.txt). What is
# covered now covers those; what is not is on this list.
def _nested_repos():
    """" (a, b, c)" naming this workspace's product repos - "" if none are declared."""
    try:
        raw = json.loads((pathlib.Path(__file__).resolve().parent
                          / "facets_vocabulary.json").read_text(encoding="utf-8"))
        names = [n for n in raw.get("nested_repos", []) if n]
    except Exception:
        return ""
    return (" (%s)" % ", ".join(names)) if names else ""


NOT_COVERED = [
    ("CLAUDE.md, the two push-channel copies", "global + workspace - already in every session's "
                                               "context before it asks. A NESTED REPO's CLAUDE.md "
                                               "IS indexed; it is that repo's front door"),
    ("third-party marketplace skills", "claude-plugins-official / humanizer / mattpocock etc. - "
                                       "not this workspace's knowledge; first-party skills ARE indexed"),
    ("OrchDocs (ORCHESTRATOR-DECISIONS-*)", "per-orchestrator state; ask the owning orchestrator"),
    ("*.txt files", "none hold routable knowledge today; declared so their absence is visible"),
    ("chat history / session transcripts", "not durable; if it matters it belongs in a note"),
    ("Google Docs / Sheets", "external; pointers to them live in notes that ARE indexed"),
    # ⛔ THE SIXTH UNDECLARED STORE, found 2026-08-17 while giving a product repo its own
    # cornerstone. The nested product repos were neither harvested nor declared, so a receipt
    # read "1307 artifacts examined" while never opening the repo holding the answer. That is
    # the o10 failure exactly: an absence claim whose corpus quietly excluded the subject.
    #
    # ⭐ DECLARING IT WAS NOT ENOUGH, AND THAT IS THE LESSON. The declaration was honest and the
    # repos stayed invisible - the `repo_1` cornerstone rendered zero candidates while the
    # whole product repo sat on disk, and `repo_1/CLAUDE.md`, titled "CLAUDE.md -
    # repo_1", was excluded by a rule written for the two push-channel copies. They are now
    # ENUMERATED (corpus.py S5), bounded to root `*.md` + `docs/**`; what remains uncovered is
    # only the generated graph below.
    ("nested repos' .codesight graphs%s" % _nested_repos(),
     "machine-generated code maps - read them directly per CLAUDE.md; the repos' own docs/ and "
     "root markdown ARE indexed"),

    # ⛔ THE SEVENTH, AND IT WAS THE LARGEST AND THE ONLY SILENT ONE. Found by o1, 2026-09-08.
    # The doc store is `WS.glob("*.md")` - NON-RECURSIVE - plus docs/ and .shared/docs/, so
    # markdown in any OTHER workspace subdirectory was never walked. Measured over tracked
    # files: 136 .md at the root against 484 in subdirectories.
    #
    # ⭐ O1'S CASE IS THE ARGUMENT FOR PRINTING IT HERE rather than only in corpus.py. They ran
    # `route.py enrich` on a file, its tags were written, and the router still would not return
    # it on its most distinctive term - because the file had never been in the corpus. Tags
    # written, behaviour unchanged, and nothing on the receipt could explain why.
    #
    # ⚠️ The scope may be right; the silence was not. working_corpus_o5 alone is 173 files of a
    # retired orchestrator's notes and would flood every query. But two of these directories
    # hold material the human intends to train on, and o1 missed the AVATAR-LANG primary corpus while
    # writing a lane seed - found only by checking someone else's incorrect warning. Whether to
    # index them is on the human's plate; being honest about it is not a decision.
    # ⭐ NARROWED BY THE HUMAN'S D17 RULING, 2026-09-08: "index the two research corpora, leave o5's
    # scratch out." So this row is now smaller than it was yesterday, and it has to SAY so - a
    # declaration that overstates what is excluded is the same defect as one that understates
    # it, which is what this row was created to fix.
    ("workspace subdirectories other than docs/ and the two named research corpora",
     "the doc store is WS/*.md NON-RECURSIVE plus docs/, and - since D17 - "
     "research_corpus_1 and working_corpus_o8. Still NOT walked: "
     "working_corpus_o5 (173 files of a retired orchestrator's notes, which would flood every "
     "query) and the other working and deliverable directories. A VERIFIED MISS is verified "
     "for the stores named above and nothing else"),
]

STOP = {"the", "a", "an", "to", "of", "in", "on", "for", "and", "or", "how", "do", "i",
        "is", "it", "my", "we", "what", "where", "with", "use", "using", "does", "can"}

from mentions import ROUTE_TAGS as TAG_RE  # noqa: E402 - ONE definition
# ⛔ IMPORTED, NOT RETYPED. A marker format is a contract between every tool that reads it, and
# this file, knowledge_gate.py and knowledge_pages.py all read the same one. Three copies of the
# pattern is three chances to drift - and the drift would be silent, because a doc whose
# declaration one reader accepts and another ignores still LOOKS declared. The fallback below is
# the pre-hierarchy spelling, so a partial checkout still routes rather than crashing.
try:
    from knowledge_pages import MARKER_RE as SUBJECT_RE
except Exception:
    SUBJECT_RE = re.compile(r"<!--\s*(?:cornerstone|subject):\s*([a-z0-9-]+)\s*-->")
# ⛔ THE ANSWER, NOT A MENTION. Declared by the author, obeyed by the ranker.
#
# The founding-case note fell from #1 to #5 for its own subject - not because anything
# broke, but because the facet rollout WORKED and dozens of docs honestly gained that
# platform. Counting mentions cannot separate "this IS the answer" from "this mentions
# the answer", and no weight tuning will, because both matches are real.
#
# ⭐ So it is DECLARED, not inferred - the one thing a count can never recover. A doc
# marked canonical for a term outranks every doc that merely contains it.
#
#     <!-- canonical: acme, acme-api, refunds -->
#
# Use it sparingly. If three docs claim the same term, the marker has stopped meaning
# anything and the audit says so.
CANONICAL_RE = re.compile(r"<!--\s*canonical:\s*([a-z0-9 ,._-]+?)\s*-->")


# ⛔ ONE WRITER, ONE READER, MATCHED BY SHAPE - the marker-format-is-a-contract rule
# (`memory/marker_format_is_a_contract.md`). `miss_receipt()` below is the only thing that
# emits this line and this regex is the only thing that reads it, so a near-miss shape cannot
# be hand-rolled into existence by a session that half-remembered the format.
#
# Three fields, all required, all machine-filled:
#   terms   the EXACT tokens the router searched with - not a paraphrase. Re-running them is
#           the entire mechanism; a prettified version tests a query nobody ever made.
#   date    when the miss was declared. The age comparison is against THIS.
#   corpus  unique artifacts enumerated at the time, so growth since is visible rather than
#           assumed.
# Deliberately no `by=` field: the shape has to be trivially reproducible, and git already
# knows who committed it.
MISS_RECEIPT = re.compile(
    r"<!--\s*miss-receipt:\s*terms=\"(?P<terms>[^\"]+)\"\s+date=(?P<date>\d{4}-\d{2}-\d{2})"
    r"\s+corpus=(?P<corpus>\d+)\s*-->")


def miss_receipt(toks, corpus, today=None):
    """The pasteable line. Emitted on VERIFIED MISS, re-run by `route.py receipts`."""
    return '<!-- miss-receipt: terms="%s" date=%s corpus=%d -->' % (
        " ".join(toks), (today or dt.date.today()).isoformat(), corpus)


def _canon(text):
    """Terms this doc DECLARES itself the answer for."""
    out = []
    for m in CANONICAL_RE.findall(text or ""):
        out += [w.strip() for w in m.replace(",", " ").split()]
    return out


def _subject_aliases(text):
    """A doc that declares a SUBJECT inherits that subject's whole vocabulary, at query time.

    ⛔ THE CAUSE OF EVERY FALSE MISS, ATTACKED AT THE ROOT (the human, 2026-08-13): *"Is there a way
    to systematically choose names so that it makes it FAR harder to NOT get the keywords
    correct?"*

    Measured: a tagged doc carries ~10 words; the subject it belongs to knows up to 23. Two
    small sets rarely intersect, and that gap IS the miss - the payment vendor's webhook version
    was filed under a note named for REFUNDS, so a search for the vendor's own name returned
    nothing. Neither party was careless. They simply sampled the same idea differently.

    ⭐ EXPANSION HAPPENS HERE, NOT IN THE DOC. Copying a subject's aliases into every file would
    freeze each doc at the vocabulary of the day it was written. Expanding at QUERY time means
    widening a subject retroactively improves every doc already filed under it - the one place
    where adding a word helps documents nobody will ever touch again.

    So the author writes one word they cannot get wrong (the subject), and the router matches on
    all of them.
    """
    # A DECLARATION, NOT AN ILLUSTRATION - fenced markers stripped for the same reason
    # `_receipt_docs` strips them (see `_outside_fences`). A lane seed teaching the three
    # markers a new cornerstone must carry was inheriting that cornerstone's whole vocabulary,
    # so an ephemeral launch input ranked as a subject artifact. Found 2026-08-17 by the
    # commerce page listing that seed among the docs the node keeps current.
    m = SUBJECT_RE.search(_outside_fences(text or ""))
    if not m:
        return ""
    try:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
        from knowledge_pages import SUBJECTS
        return " ".join(SUBJECTS.get(m.group(1), ("", []))[1])
    except Exception:
        return ""
def _facet_aliases(text):
    """Aliases implied by this doc's declared platform/access/component/retired facets.

    Same principle as the subject, one level finer: the author declares the deploy platform by
    its canonical name and the router matches its domain and its short form too; declares that
    platform's RETIRED MCP integration, and a search for it lands here instead of returning a
    clean miss that reads as permission to rebuild it.

    ⛔ THIS SIGNAL IS WEIGHT 2, NOT 3, AND THE DIFFERENCE IS LOAD-BEARING. A FACET SAYS THIS
    DOC RELATES TO X; A FILENAME SAYS THIS DOC *IS* X. At equal weight the two are
    indistinguishable, and the facet backfill then drowns every canonical reference in the
    docs that merely mention its vendor.

    ⭐ Found by the founding-case fixture going RED mid-backfill, twice. The payment vendor's
    name returned 28 hits, of which eight scored identically - a filename match and a bare
    `<!-- platform: <vendor> -->` both produced a weight of 3 - so the tie broke alphabetically
    and that vendor's refund-API gotchas note, the one document the whole system exists to
    surface, fell below the display cut. Nothing was mis-tagged; the docs that outranked it
    are all correctly tagged. That is what makes it worth stating: the defect was not a wrong
    facet anywhere, it was CORRECT facets at a weight that let quantity beat aboutness.

    Lowering it to 2 puts all ten fixtures green, including the two that were already passing
    by luck of the alphabet.
    """
    try:
        from facets import facet_aliases
        return facet_aliases(text)
    except Exception:
        return ""


WORD_RE = re.compile(r"[a-z0-9][a-z0-9_.+-]*")
# What an enrich term may look like. '-->' inside a term terminated the HTML comment and the
# NEXT enrich then destroyed every previously-enriched term; a backslash term crashed re.sub
# outright (or silently corrupted via group references). Sanitizing the vocabulary is cheaper
# than escaping every consumer, and tag vocabulary has no legitimate need for punctuation.
TERM_OK = re.compile(r"^[a-z0-9][a-z0-9_.+-]{0,23}$")


def stem(w):
    """Light stemming, both sides of the match. 'tokens' must find 'token' and 'deploying'
    must find 'deploy' - the inflection asymmetry produced VERIFIED MISS receipts for covered
    subjects, which routes a session to CREATE and births the duplicate doc."""
    for suf in ("ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: len(w) - len(suf)]
    return w


def tokenize(terms):
    """(tokens, dropped) - and the dropped list is PRINTED, never swallowed."""
    toks, dropped = [], []
    for raw in terms:
        words = WORD_RE.findall(raw.lower())
        for t in words:
            if t in STOP:
                dropped.append("%s (stopword)" % t)
            elif len(t) < 2:
                dropped.append("%s (too short)" % t)
            elif len(t) == 2 and not any(c.isdigit() for c in t):
                # 'b2', 's3', 'v2' are real subjects; bare two-letter words are noise
                dropped.append("%s (too short)" % t)
            else:
                toks.append(t)
        # ⛔ A DROPPED SHORT WORD CAN BE HALF OF A REAL COMPOUND. `route.py "em dash"` found
        # nothing while `route.py "em-dash"` found the rule that answers it - the corpus stores
        # the compound hyphenated (`route-tags: dash em-dash hyphen style`), so the spelling a
        # person actually types was the one spelling that missed.
        #
        # ⭐ o7 reported that rule as "buried" tonight. It was not buried; it was findable by a
        # spelling nobody uses. And the miss looked honest - "DROPPED (not searched): em (too
        # short)" reads as housekeeping, not as "the word carrying your meaning is gone".
        #
        # The drop stays: a two-letter word alone is still noise. Adjacent pairs are joined and
        # searched as one COMPOUND, so a short word only ever rejoins the query attached to its
        # neighbour, where the corpus may genuinely hold it.
        for a, b in zip(words, words[1:]):
            if a in STOP or b in STOP:
                continue
            pair = "%s-%s" % (a, b)
            if len(pair) > 4 and pair not in toks:
                toks.append(pair)
    return toks, dropped


def _read(p, n=6000):
    try:
        return p.read_text(encoding="utf-8", errors="replace")[:n]
    except OSError:
        return ""


def _fm_field(text, name):
    m = re.search(r"^%s:\s*(.+)$" % name, text[:1500], re.M)
    return m.group(1).strip() if m else ""


def harvest():
    """Every artifact across every covered store, as {path -> artifact}.

    Keyed by resolved path so one file is ONE artifact: v1 listed Memory2-indexed notes twice,
    which both inflated the receipt's count (396 enumerated, 376 real) and burned top-8 display
    slots on duplicates. A receipt that overcounts is the same defect as one that undercounts.
    """
    arts = {}

    def add(kind, path, pairs, canonical=()):
        key = str(path.resolve()).lower() if path.exists() else str(path).lower()
        if key in arts:
            arts[key]["signals"].extend(pairs)
            arts[key]["canonical"] |= set(canonical)
        else:
            arts[key] = {"kind": kind, "path": path, "signals": list(pairs),
                         # STEMMED, because rank() matches on stems - storing raw terms here
                         # made the tier silently never fire, which is a guard that looks
                         # installed and does nothing.
                         "canonical": {stem(c) for c in canonical}}

    hooks = {}
    for line in _read(MEM / "MEMORY.md", 300000).split("\n"):
        for m in re.finditer(r"\(([\w./-]+\.md)\)", line):
            hooks.setdefault(m.group(1).split("/")[-1], []).append(line)

    # ⛔ THE STORES COME FROM corpus.py, NOT FROM HERE. Two independent enumerations is how
    # this router reached 1,326 artifacts while the knowledge map reached 198 and neither could
    # see the other's blind spot. What stays here is the SIGNAL EXTRACTION - which parts of a
    # file carry weight - because that genuinely differs per store. Membership does not.
    from corpus import files as _corpus_files
    by_kind = {}
    for kind, p in _corpus_files():
        by_kind.setdefault(kind, []).append(p)

    # S1 - memory notes, RECURSIVE. memory/automation/** holds the sweep + briefing runbooks
    # and was silently unharvested in v1 while the receipt claimed the memory store was covered.
    for p in by_kind.get("memory", []):
        t = _read(p)
        add("memory", p, [
            (3, p.stem.replace("_", " ").replace("-", " ")),
            (2, _fm_field(t, "description")),
            (2, _fm_field(t, "name").replace("-", " ")),
            (3, " ".join(TAG_RE.findall(t))),
            (3, _subject_aliases(t)),
            (3, " ".join(CANONICAL_RE.findall(t))),
            (2, _facet_aliases(t)),
            (1, " ".join(re.findall(r"^#{1,3}\s+(.+)$", t, re.M)[:6])),
            (2, " ".join(hooks.get(p.name, []))),
        ], canonical=_canon(t))

    # S2 - Memory2 cold index: signals MERGE into the existing artifact when the target exists
    m2 = MEM / "Memory2.md"
    if m2.exists():
        for line in _read(m2, 300000).split("\n"):
            lm = re.match(r"^- .*?\[(.+?)\]\(([\w./-]+)\)(.*)$", line)
            if lm:
                add("memory", MEM / lm.group(2), [(3, lm.group(1)), (2, lm.group(3))])

    # S3 - shared scripts, recursive, pycache excluded
    for p in by_kind.get("script", []):
        t = _read(p, 3000)
        add("script", p, [
            (3, p.stem.replace("_", " ")),
            (2, " ".join(t.split("\n")[:6])),
            (3, " ".join(TAG_RE.findall(t))),
            (3, _subject_aliases(t)),
            (2, _facet_aliases(t)),
        ], canonical=_canon(t))

    # S4 - docs: workspace root, docs/**, .shared/docs/**, and (S5) the nested product repos'
    # own root markdown + docs/**. A repo doc gets doc-shaped signals because that is what it
    # is; the STORE it came from is kept as its kind so a reader can see where an answer lives.
    for kind in ("doc", "repo"):
        for p in by_kind.get(kind, []):
            t = _read(p, 3000)
            add(kind, p, [
                (3, p.stem.replace("-", " ").replace("_", " ")),
                (1, " ".join(re.findall(r"^#{1,2}\s+(.+)$", t, re.M)[:4])),
                (3, " ".join(TAG_RE.findall(t))),
                (3, _subject_aliases(t)),
                (2, _facet_aliases(t)),
                (1, t.split("\n\n")[1][:200] if "\n\n" in t else ""),
            ], canonical=_canon(t))

    # S6 - first-party skills
    seen = set()
    for p in by_kind.get("skill", []):
        if p.parent.name in seen:
            continue
        seen.add(p.parent.name)
        t = _read(p, 2500)
        add("skill", p, [
            (3, p.parent.name.replace("-", " ")),
            (2, _fm_field(t, "description")),
            (3, " ".join(TAG_RE.findall(t))),
            (3, _subject_aliases(t)),
            (2, _facet_aliases(t)),
        ], canonical=_canon(t))

    # word index per artifact: {stemmed word -> max signal weight}
    for a in arts.values():
        wmap = {}
        for w, text in a["signals"]:
            for word in WORD_RE.findall((text or "").lower()):
                s = stem(word)
                if wmap.get(s, 0) < w:
                    wmap[s] = w
        a["words"] = wmap
    return list(arts.values())


def rank(arts, toks):
    """(hits, weak) under the rarity-weighted word-match scoring."""
    stems = [stem(t) for t in toks]
    df = {s: sum(1 for a in arts if s in a["words"]) for s in stems}

    def rarity(s):
        return 3 if df[s] <= 3 else (2 if df[s] <= 12 else 1)

    scored = []
    for a in arts:
        matched = [s for s in set(stems) if s in a["words"]]
        if not matched:
            continue
        sc = sum(a["words"][s] * rarity(s) for s in matched) + 4 * len(matched)
        # ⭐ A CANONICAL CLAIM IS A SEPARATE AXIS, not a bigger number. Sorting canonical docs
        # ahead of everything else - rather than adding to their score - means a doc that
        # merely mentions the term can never out-accumulate the doc that IS the answer, however
        # many times it says the word. Scores still order WITHIN each tier.
        canon = 1 if any(s in a.get("canonical", set()) for s in matched) else 0
        scored.append((canon, sc, len(matched), a))
    scored.sort(key=lambda x: (-x[0], -x[1], -x[2]))
    hits = [(s, a) for _c, s, _m, a in scored if s >= 7]
    weak = [(s, a) for _c, s, _m, a in scored if 4 <= s < 7]
    return hits, weak, df


def _drifted_skills():
    """Names whose canonical copy != the copy a session actually LOADS.

    ⛔ WHY THE ROUTER MUST KNOW THIS. o1's finding: the failure is rarely "which artifact?" -
    it is "which COPY, and is the one I am reading the one that runs?" This router indexes the
    MARKETPLACE copy of a plugin skill and zero cache copies, but a session invoking that skill
    loads the CACHE. Measured 2026-08-12: two of four had drifted, one by 117 lines, still
    teaching a strike format that had been rejected twice.

    ⭐ So a routing answer that names a drifted artifact is WORSE than no answer. It sends the
    session to read a file that is not the one governing its behaviour, with full confidence.
    A single source of truth that points at the wrong copy is not a source of truth; it is one
    more opinion, wearing the authority of the map.
    """
    try:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
        import tool_drift
        rows, live = tool_drift.survey_skills()
        return {n for n, canon, mine, _p in rows if mine != canon} if live else set()
    except Exception:
        return set()          # never let a decoration break the answer


_DRIFT = None


def _row(sc, a):
    global _DRIFT
    if _DRIFT is None:
        _DRIFT = _drifted_skills()
    try:
        age = dt.date.fromtimestamp(a["path"].stat().st_mtime).isoformat()
    except OSError:
        age = "?"
    short = str(a["path"]).replace(str(WS) + "\\", "").replace(str(MEM) + "\\", "memory\\")
    warn = ""
    if a["kind"] == "skill" and a["path"].parent.name in _DRIFT:
        warn = "  ⛔ DRIFTED - the CACHE copy is what loads, and it differs. Fix: tool_drift.py --sync"
    return "    %-7s %-62s %s%s" % (a["kind"], short[:62], age, warn)


def cmd_ask(args):
    toks, dropped = tokenize(args.terms)
    if not toks:
        print("  every term was dropped: %s" % "; ".join(dropped))
        return 2
    arts = harvest()
    counts = {}
    for a in arts:
        counts[a["kind"]] = counts.get(a["kind"], 0) + 1
    hits, weak, df = rank(arts, toks)

    # ---- the receipt, printed FIRST and always ----
    print("route - terms: %s" % " ".join(toks))
    if dropped:
        print("  DROPPED (not searched): %s" % "; ".join(dropped))
    print("  EXAMINED: %s  (%d unique artifacts)"
          % (", ".join("%s x%d" % (k, v) for k, v in sorted(counts.items())),
             len(arts)))
    print("  NOT COVERED (exists, not indexed): %s"
          % "; ".join(n for n, _w in NOT_COVERED))
    print()

    if hits:
        print("  %d hit(s)%s:" % (len(hits), " (top 8 shown)" if len(hits) > 8 else ""))
        for s, a in hits[:8]:
            print(_row(s, a))
        if len(hits) > 8:
            print("    ... and %d more below these" % (len(hits) - 8))
        print("    (dates are mtime - unreliable after a git clone/checkout; trust the")
        print("     artifact's own Verified/Reviewed fields over this column)")
        print()
        print("  ⭐ IF THE TOP HIT DOES NOT FULLY ANSWER, THAT IS A PARTIAL - NOT A MISS.")
        print("     Extend the artifact THAT HOLDS THE SUBJECT (not merely the top row),")
        print("     then make it findable by the terms you actually searched with:")
        print("       route.py enrich <file> --terms %s" % " ".join(toks))
        print("     A new artifact beside an old one is how docs go stale in pairs.")
        _VERDICT["last"] = "HIT"
        return 0

    if weak:
        # ⭐ THE STATE v1 COULD NOT NAME. These artifacts DID match - printing VERIFIED MISS
        # here sent the session to CREATE past an existing home, which is the founding case's
        # exact mechanism. Weak is not a verdict; it is a instruction to look at the list.
        print("  NO CONFIDENT HIT - but %d artifact(s) matched below the floor:" % len(weak))
        for s, a in weak[:6]:
            print(_row(s, a))
        print()
        print("  This is NOT a verified miss. One of these is likely the home - open it; if")
        print("  it is the subject, enrich it with your terms so the next ask hits:")
        print("       route.py enrich <file> --terms %s" % " ".join(toks))
        _VERDICT["last"] = "WEAK"
        return 1

    print("  VERIFIED MISS - 0 matched tokens across the %d artifacts enumerated above."
          % len(arts))
    print("  (token document-frequency: %s)"
          % ", ".join("%s=%d" % (t, df.get(stem(t), 0)) for t in toks))
    print()
    print("  Before creating ANYTHING:")
    print("    1. re-ask with different words - a vocabulary miss is not a knowledge miss")
    print("    2. check the NOT-COVERED stores named above for your subject")
    print()
    print("  ⭐ THE RECEIPT - paste this line into the doc you create (CREATE.md step 1).")
    print("     It is what makes this claim re-testable. Without it, nothing ever re-checks")
    print("     whether the miss was true, and a doc born from a false miss stands forever:")
    print()
    print("    %s" % miss_receipt(toks, len(arts)))
    print()
    print("     `route.py receipts` re-runs these terms against the corpus at every session")
    print("     start, and fails only if they now reach an artifact OLDER than this date -")
    print("     i.e. one that was already there while you were declaring nothing was.")
    print()
    print("  Also carry a nearest-neighbor line ('closest existing artifact is X; could not")
    print("  be extended because Y'). CREATE path: docs/knowledge/CREATE.md. Then:")
    print("    route.py fixtures --add \"%s\" --must-hit <new-file-name>" % " ".join(toks))
    print("  so the next session finds it by the query that just failed.")
    _VERDICT["last"] = "VERIFIED MISS"
    return 1


def cmd_enrich(args):
    """Append searched-with terms to the artifact so the next session's query hits.

    The adversarial pass broke v1 eight ways here - backslash terms crashed or silently
    corrupted, '-->' truncated the comment and the NEXT enrich destroyed earlier terms, LF
    files were rewritten wholesale to CRLF, and success was reported for terms the ask path
    can never match. Every one of those is now either sanitized away or refused loudly.
    """
    p = pathlib.Path(args.file)
    if not p.exists():
        p2 = MEM / args.file
        p = p2 if p2.exists() else p
    if not p.exists():
        print("  no such file: %s" % args.file)
        return 2
    if p.suffix.lower() != ".md":
        print("  enrich writes only to .md artifacts. For a script, add the terms to a")
        print("  `<!-- route-tags: ... -->` line inside its top docstring - route.py indexes")
        print("  that at full weight (plain docstring prose ranks lower and may not clear")
        print("  the hit floor).")
        return 2

    good, bad = [], []
    for t in args.terms:
        t = t.lower()
        if not TERM_OK.match(t):
            bad.append("%s (illegal characters)" % t)
        elif t in STOP or len(t) < 2:
            bad.append("%s (unsearchable - the ask path drops it)" % t)
        else:
            good.append(t)
    if bad:
        print("  SKIPPED %d unusable term(s): %s" % (len(bad), "; ".join(bad)))
    if not good:
        print("  nothing usable to add. No write performed.")
        return 2

    raw = p.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    m = TAG_RE.search(text)
    terms = sorted(set((m.group(1).split() if m else []) + good))
    line = "<!-- route-tags: %s -->" % " ".join(terms)
    if len(line) > 800:
        print("  REFUSED - the tag line would exceed 800 chars. A tag that long has stopped")
        print("  being vocabulary; split the artifact or prune the terms.")
        return 2
    if m:
        # span splice, never re.sub - a replacement string containing backslashes was being
        # interpreted for group references, corrupting the tag while stdout reported the clean
        # version. The write and the report must come from the same bytes.
        text = text[: m.start()] + line + text[m.end():]
    elif text.startswith("---") and 0 < text.find("\n---", 3) < 1500:
        end = text.find("\n---", 3)
        close = text.find("\n", end + 1)
        text = text[: close + 1] + line + "\n" + text[close + 1:]
    else:
        text = line + "\n" + text

    # newline-preserving write. Path.write_text on Windows translated every LF to CRLF, so
    # enriching one line rewrote the whole file's endings - a 1-line change arriving as a
    # 400-line diff, and the reviewer cannot see the line that matters.
    with open(p, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)

    covered = any(str(p.resolve()).lower().startswith(str(r.resolve()).lower())
                  for r in [MEM, WS] if r.exists())
    print("  enriched %s" % p.name)
    print("  route-tags now: %s" % " ".join(terms))
    if not covered:
        print("  ⚠️  this file is OUTSIDE every covered store - the tag was written but no")
        print("      query will ever read it. Move the artifact or extend the stores.")
    print("  verify with the query that missed:  route.py %s" % " ".join(good))
    return 0


def cmd_fixtures(args):
    """Replay every recorded query->artifact promise. A fixture that stops hitting is a defect.

    --add REQUIRES --must-hit: v1 accepted a bare --add and produced a fixture that passed on a
    zero-hit query - regression protection theater, worse than none because it reads as cover.
    """
    fx = json.loads(FIXTURES.read_text(encoding="utf-8")) if FIXTURES.exists() else []
    if args.add:
        if not args.must_hit:
            print("  REFUSED - a fixture without --must-hit is vacuously green forever.")
            print("  Name the path substring(s) the query must return.")
            return 2
        fx.append({"query": args.add, "must_hit": args.must_hit,
                   "added": dt.date.today().isoformat()})
        FIXTURES.write_text(json.dumps(fx, indent=2), encoding="utf-8")
        print("  fixture added (%d total)." % len(fx))
        return 0
    if not fx:
        print("  no fixtures recorded - which means NO regression protection. That is a")
        print("  state to fix, not a pass.")
        return 2
    arts = harvest()
    bad = 0
    for f in fx:
        if not f.get("must_hit"):
            bad += 1
            print("  [FAIL] %-38s -> VACUOUS (no must_hit) - repair or remove" % f["query"])
            continue
        toks, _ = tokenize(f["query"].split())
        hits, weak, _df = rank(arts, toks)
        top = [str(a["path"]).lower() for _s, a in (hits + weak)[:8]]
        missing = [w for w in f["must_hit"] if not any(w.lower() in t for t in top)]
        # ⛔ RANK, NOT MERE PRESENCE. The founding-case note fell from #1 to #5 and every
        # fixture stayed green, because they only asserted the artifact was IN the results.
        # A fixture that cannot tell first from fifth will not fire until the answer falls
        # off the list entirely - by which time the router has been quietly wrong for weeks.
        first = f.get("must_rank_first")
        if first and not missing:
            if not top or first.lower() not in top[0]:
                at = next((i + 1 for i, x in enumerate(top) if first.lower() in x), None)
                missing = ["%s is #%s, must be #1" % (first, at or "absent")]
        ok = not missing
        bad += (not ok)
        print("  [%s] %-38s -> %s" % ("OK " if ok else "FAIL", f["query"],
                                      "hits" if ok else "MISSING: %s" % ", ".join(missing)))
    print()
    print("  %d fixture(s), %d failing, %d unique artifacts examined" % (len(fx), bad, len(arts)))
    return 2 if bad else 0


# A neighbour the author was already FORCED to read. `knowledge_gate.py` refuses a new doc
# overlapping an existing one on 3+ tags unless it names that doc in one of these two lines, and
# naming the wrong file does not work. Re-flagging a neighbour someone has demonstrably opened
# is noise, and noise is what gets a check switched off.
RECEIPT_NEIGHBOUR = re.compile(r"<!--\s*(?:supersedes|distinct-from):\s*([^:>]+)")
# The refusal code. Only this blocks; see the fail-open note on cmd_receipts.
RECEIPT_REFUSE = 9


def _receipt_files():
    """Every file a receipt could live in - THE SAME STORES harvest() indexes, from corpus.py.

    ⛔ It used to be a second hand-written enumeration of the same stores, one function below the
    first. A receipt living in a file this list forgot is a falsified miss nobody re-checks, and
    the two lists could drift without any test seeing it - which is the exact class of defect
    corpus.py exists to make impossible.
    """
    from corpus import files as _corpus_files
    return [p for _k, p in _corpus_files()]


FENCE_RE = re.compile(r"^(```|~~~).*?^\1", re.S | re.M)


def _outside_fences(text):
    """Text with fenced code blocks removed - a marker inside a fence is an EXAMPLE.

    ⛔ FOUND BY THIS CHECKER FAILING ON ITS OWN DOCUMENTATION, first run. `CREATE.md` step 1
    shows the receipt shape in a fenced block, exactly as it must, and the scanner read that
    illustration as a live claim - then correctly reported it falsified, since the sample terms
    reach sixteen artifacts predating it. The doc was right, the receipt was right, and the
    reading was wrong.

    ⭐ Not a special case for one file: ANY doc that teaches a marker contains that marker, so
    the distinction has to be structural. A fence means "here is what one looks like"; a bare
    line means "here is mine". Cost is that a receipt someone fences by accident goes uncounted
    - a false negative, the survivable direction, and CREATE.md says paste the line, not fence
    it.
    """
    return FENCE_RE.sub("", text)


def _receipt_docs():
    """[{path, terms, date, corpus, named}] - every stored miss receipt in the workspace."""
    found = []
    for p in _receipt_files():
        t = _read(p, 8000)
        if "miss-receipt" not in t:
            continue                       # cheap reject before the regex
        t = _outside_fences(t)
        for m in MISS_RECEIPT.finditer(t):
            found.append({
                "path": p,
                "terms": m.group("terms"),
                "date": m.group("date"),
                "corpus": int(m.group("corpus")),
                "named": [n.strip().replace("\\", "/").rsplit("/", 1)[-1].lower()
                          for n in RECEIPT_NEIGHBOUR.findall(t)],
            })
    return found


def _created(path):
    """(iso-date, source) - when this artifact FIRST existed. git first, mtime as fallback.

    ⛔ mtime is NOT trustworthy here, and this file's own hit rows already say so: a clone or a
    checkout stamps every file with the time of the clone. Under that stamp every artifact looks
    NEWER than every receipt and the check passes on everything - a false NEGATIVE, which is the
    survivable direction, and the source is printed beside every date so a reader can see which
    kind of answer they are looking at rather than having to assume.

    `--follow` is deliberately omitted: it needs exactly one pathspec and misreports across some
    rename shapes. Cost is that a renamed artifact reports its rename date, i.e. looks younger.
    Again a false negative.
    """
    try:
        p = subprocess.run(["git", "-C", str(path.parent), "log", "--diff-filter=A",
                            "--format=%ad", "--date=short", "--", str(path)],
                           capture_output=True, text=True, timeout=20)
        lines = [ln.strip() for ln in p.stdout.splitlines() if ln.strip()]
        if p.returncode == 0 and lines:
            return lines[-1], "git"
    except Exception:
        pass
    try:
        return dt.date.fromtimestamp(path.stat().st_mtime).isoformat(), "mtime"
    except OSError:
        return "", "unknown"


def _recheck(rec, arts):
    """[(score, artifact, created, source, tier)] - artifacts OLDER than this receipt that its
    terms now reach. Empty means the miss still stands."""
    toks, _dropped = tokenize(rec["terms"].split())
    if not toks:
        return None                        # unusable receipt; the caller reports it as such
    hits, weak, _df = rank(arts, toks)
    self_key = str(rec["path"].resolve()).lower()
    out = []
    for tier, rows in (("HIT", hits), ("WEAK", weak)):
        for sc, a in rows[:8]:
            if str(a["path"].resolve()).lower() == self_key:
                continue                   # the doc the receipt lives in, tagged with its terms
            if a["path"].name.lower() in rec["named"]:
                continue                   # already read and declared - see RECEIPT_NEIGHBOUR
            created, src = _created(a["path"])
            if created and created < rec["date"]:
                out.append((sc, a, created, src, tier))
    return out


def cmd_receipts(_args):
    """Re-run every stored miss against TODAY's corpus.

    ⛔ FAILS OPEN, ON PURPOSE AND STRUCTURALLY. Only RECEIPT_REFUSE means "a miss was falsified".
    A crash, an unreadable file, a git that is not there - all warn and return 0. On 2026-08-12 a
    syntax error in a guard took down every commit in this workspace because its hook used
    `|| exit 1`, which blocks on any non-zero exit including the guard failing to parse.

    ⭐ AND IT REPORTS WHAT IT EXAMINED, NOT ONLY WHAT IT FOUND. "0 receipts checked" and "14
    checked, all still valid" must never print the same line: the first is an empty set and the
    second is evidence, and a reader who cannot tell them apart will read silence as safety.
    """
    try:
        holders = _receipt_docs()
        arts = harvest()
    except Exception as e:
        print("[receipts] could not scan (%s) - warning, not concluding." % str(e)[:120])
        return 0

    print("route - miss receipts")
    if not holders:
        print("  EXAMINED: 0 receipts, over %d artifacts scanned." % len(arts))
        print("  No VERIFIED MISS carries a receipt yet, so there is nothing to re-test.")
        print("  That is an EMPTY SET, not a pass. Docs predating this mechanism have no")
        print("  receipt and are NOT violations - only a recorded miss is re-checkable.")
        return 0

    bad, unusable = [], []
    for rec in sorted(holders, key=lambda r: str(r["path"])):
        short = str(rec["path"]).replace(str(WS) + "\\", "").replace(str(MEM) + "\\", "memory\\")
        try:
            older = _recheck(rec, arts)
        except Exception as e:
            # One malformed receipt must not cost the other thirteen their re-check, and it
            # must not manufacture a refusal either. Warn on this row, keep going.
            print("  [WARN] %-52s could not re-run (%s)" % (short[:52], str(e)[:40]))
            unusable.append(short)
            continue
        if older is None:
            unusable.append(short)
            print("  [SKIP] %-52s every term unsearchable" % short[:52])
            continue
        if older:
            bad.append((short, rec, older))
            print("  [FAIL] %-52s %s  \"%s\"" % (short[:52], rec["date"], rec["terms"]))
        else:
            print("  [OK  ] %-52s %s  corpus %d -> %d"
                  % (short[:52], rec["date"], rec["corpus"], len(arts)))

    print()
    print("  EXAMINED: %d receipt(s) re-run against today's corpus of %d artifacts."
          % (len(holders), len(arts)))
    if not bad:
        print("  %d still valid%s - none of them now reaches an artifact that already"
              % (len(holders) - len(unusable),
                 ", %d unusable" % len(unusable) if unusable else ""))
        print("  existed on the day its miss was declared.")
        return 0

    print()
    for short, rec, older in bad:
        print("  ⛔ FALSIFIED MISS: %s" % short)
        print("     recorded %s, searching: %s" % (rec["date"], rec["terms"]))
        print("     those terms now reach %d artifact(s) that ALREADY EXISTED then:" % len(older))
        for sc, a, created, src, tier in older[:5]:
            p = str(a["path"]).replace(str(WS) + "\\", "").replace(str(MEM) + "\\", "memory\\")
            print("       %-5s %-52s created %s (%s)" % (tier, p[:52], created, src))
        print()
    print("  A NEWER artifact appearing here would be normal growth and is ignored. An OLDER")
    print("  one is not: it was sitting in the corpus while the miss was being declared, so")
    print("  the miss was wrong when it was made and the doc it authorised may be a duplicate.")
    print()
    print("  Read the older artifact, then close it one of three ways:")
    print("    - it IS the same subject   -> merge into it and tombstone the newer doc")
    print("      (knowledge_gate.py owns the tombstone shape; CREATE.md section 5)")
    print("    - they genuinely differ    -> one line in the newer doc, and this stops firing:")
    print("        <!-- distinct-from: <that path>: <how they differ> -->")
    print("    - the older doc was enriched with these terms AFTER the receipt date, so it")
    print("      could not have been found then -> also distinct-from, saying exactly that.")
    print("      Check with: git log -p --  <that path>   (look at when its tags changed)")
    return RECEIPT_REFUSE


def cmd_stores(_args):
    arts = harvest()
    counts = {}
    for a in arts:
        counts[a["kind"]] = counts.get(a["kind"], 0) + 1
    print("route - coverage")
    for k, v in sorted(counts.items()):
        print("  COVERED      %-12s %d unique artifacts" % (k, v))
    for name, why in NOT_COVERED:
        print("  NOT COVERED  %-40s %s" % (name, why))
    return 0


def main():
    argv = sys.argv[1:]
    if argv and argv[0] == "enrich":
        ap = argparse.ArgumentParser(prog="route.py enrich")
        ap.add_argument("file")
        ap.add_argument("--terms", nargs="+", required=True)
        return cmd_enrich(ap.parse_args(argv[1:]))
    if argv and argv[0] == "fixtures":
        ap = argparse.ArgumentParser(prog="route.py fixtures")
        ap.add_argument("--add", help="record a new query->artifact promise")
        ap.add_argument("--must-hit", nargs="+", help="path substrings the query must return")
        return cmd_fixtures(ap.parse_args(argv[1:]))
    if argv and argv[0] == "stores":
        return cmd_stores(None)
    if argv and argv[0] == "receipts":
        return cmd_receipts(None)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("terms", nargs="+", help="the subject, in your own words")
    parsed = ap.parse_args(argv)
    rc = cmd_ask(parsed)
    _log_ask(parsed.terms, rc)
    return rc


USAGE_LOG = pathlib.Path.home() / ".claude" / "state" / "route-usage.jsonl"
_VERDICT = {"last": None}


def _log_ask(terms, rc):
    """One line per real question asked. The router could not answer anything about itself.

    ⛔ the human, 2026-09-04: *"Has it been in use? Any way to measure effectiveness concretely?"* -
    and answering it took reading 392 session transcripts, because this file recorded nothing.
    Worse, the obvious reading was wrong: 259 transcripts "mention route.py", but route.py is
    named in CLAUDE.md, which is injected into every session, so that number measures the
    injection. Counting actual invocations gave a very different shape - roughly 324 in the nine
    days it was being BUILT, and 4 in the two weeks since.

    ⭐ A tool that cannot say whether it is used cannot be judged, only guessed at. One appended
    line makes the next answer free: what was asked, what came back, when, and by whom.

    Only the ASK path is logged. `fixtures` and `enrich` are the router exercising or feeding
    itself, and counting those as use is what inflated the first measurement.
    """
    try:
        USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
        sid = ""
        for k in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID"):
            if os.environ.get(k):
                sid = os.environ[k][:80]
                break
        row = {"at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
               "q": " ".join(terms)[:200], "verdict": _VERDICT.get("last") or "?",
               "rc": rc, "session": sid, "cwd": str(pathlib.Path.cwd())}
        with USAGE_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except Exception:
        pass                       # a router that breaks on its own bookkeeping is worse


if __name__ == "__main__":
    sys.exit(main())
