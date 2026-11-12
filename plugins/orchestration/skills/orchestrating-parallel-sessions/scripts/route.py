"""route.py - the knowledge system / decision tree, phase 1. Enter by SUBJECT; answers carry receipts.

<!-- subject: knowledge-routing -->

<!-- route-tags: knowledge system decision tree route find process sop where lookup discover -->

THE CASE this must always solve: a session needed a vendor's access process, searched where a
competent orchestrator would look, found nothing, and wrote guidance of its own. The answer
existed, and was more complete, in a note filed under a different word. Searching the vendor's
own name must land on that note in one command.

Four outcomes:

  HIT            confident match - read the artifact
  WEAK           matched, below the confidence floor - NAMED and listed, never silent,
                 because "matched weakly" and "nothing exists" are different facts
  PARTIAL        a hit that does not fully answer -> EXTEND that artifact, never create a
                 new one, and `enrich` it with the terms you searched with
  VERIFIED MISS  zero matched tokens across the enumerated corpus -> the CREATE path,
                 carrying this receipt

Rarity scoring decays as the corpus grows around a term. So: THE MORE YOU WRITE ABOUT X, THE HARDER THE CANONICAL X DOC IS TO FIND. That is the
opposite of the intuition, and it means a green fixture is a statement about TODAY'S CORPUS,
not a property of the router - fixtures here decay on their own and must be re-run, not
trusted. Fix a decayed one by ENRICHING the artifact (route.py enrich) so it carries rarer
terms. Never by lowering the threshold, which floods every query at once.

A doc created after a VERIFIED MISS carries its receipt:

    <!-- miss-receipt: terms="..." date=YYYY-MM-DD corpus=N -->
"""
import argparse
import ast
import os
import datetime as dt
import json
import pathlib
import re
import subprocess
try:
    from no_window import flags
except ImportError:  # a partial delivery must not take the tool down
    def flags(extra=0):
        return extra
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from workspace_paths import WS, MEM  # noqa: E402
FIXTURES = pathlib.Path(__file__).resolve().parent / "route_fixtures.json"

def _nested_repos():
    """" (a, b, c)" naming this workspace's product repos - "" if none are declared."""
    try:
        raw = json.loads((pathlib.Path(__file__).resolve().parent
                          / "facets_vocabulary.json").read_text(encoding="utf-8"))
        names = [n for n in raw.get("nested_repos", []) if n]
    except Exception:
        return ""
    return (" (%s)" % ", ".join(names)) if names else ""


CLAUDE_MD_SYNCED = ("claude-config", "CLAUDE.md")
CLAUDE_MD_ROW = ("CLAUDE.md",
                 "global + workspace - already in every session's context before it asks. A "
                 "NESTED REPO's CLAUDE.md IS indexed; it is that repo's front door")
CLAUDE_MD_ROW_SYNCED = ("the workspace CLAUDE.md (~/.claude/CLAUDE.md IS covered, in part: the name, "
                        "markers and first 3,000 characters of a copy you keep in the workspace)",
                        "the workspace copy is already in every session's context before it "
                        "asks. That copy is ranked like any repo doc, so a word further down "
                        "the file does not find it. A NESTED REPO's CLAUDE.md IS indexed; it is "
                        "that repo's front door")
NOT_COVERED = [
    CLAUDE_MD_ROW,
    ("third-party marketplace skills", "anything you install from a marketplace - "
                                       "not this workspace's knowledge; first-party skills ARE indexed"),
    ("OrchDocs (ORCHESTRATOR-DECISIONS-*)", "per-orchestrator state; ask the owning orchestrator"),
    ("*.txt files", "none hold routable knowledge today; declared so their absence is visible"),
    ("chat history / session transcripts", "not durable; if it matters it belongs in a note"),
    ("Google Docs / Sheets", "external; pointers to them live in notes that ARE indexed"),
    ("nested repos' .code-graph graphs%s" % _nested_repos(),
     "machine-generated code maps - read them directly per CLAUDE.md; the repos' own docs/ and "
     "root markdown ARE indexed"),

    ("workspace subdirectories other than docs/",
     "the doc store is the workspace's top-level *.md files plus docs/, non-recursive. Other "
     "working and deliverable directories are not walked. A VERIFIED MISS is verified for "
     "the stores named above and nothing else"),
]

def _not_covered(arts):
    """NOT_COVERED as this run should print it: the CLAUDE.md row says the global copy is covered
    ONLY when its synced copy is among the artifacts actually harvested - a receipt states what
    was examined, never what usually is."""
    synced = str(WS.joinpath(*CLAUDE_MD_SYNCED)).lower()
    have = any(str(a["path"]).lower() == synced for a in arts)
    return [CLAUDE_MD_ROW_SYNCED if (row is CLAUDE_MD_ROW and have) else row
            for row in NOT_COVERED]


STOP = {"the", "a", "an", "to", "of", "in", "on", "for", "and", "or", "how", "do", "i",
        "is", "it", "my", "we", "what", "where", "with", "use", "using", "does", "can"}

from mentions import ROUTE_TAGS as TAG_RE  # noqa: E402 - ONE definition
from mentions import declaration_region as mentions_declaration_region  # noqa: E402
from mentions import outside_fences as mentions_outside_fences  # noqa: E402
from mentions import copy_markers  # noqa: E402 - copy-in / enforced-by, one definition
try:
    from knowledge_pages import MARKER_RE as SUBJECT_RE
except Exception:
    SUBJECT_RE = re.compile(r"<!--\s*(?:cornerstone|subject):\s*([a-z0-9-]+)\s*-->")
try:
    from knowledge_pages import sot_of as _sot_of  # ONE definition of the sot marker
except Exception:  # a partial delivery must not take the router down; rows just lose the label
    _sot_of = None
CANONICAL_RE = re.compile(r"<!--\s*canonical:\s*([a-z0-9 ,._-]+?)\s*-->")


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

    So the author writes one word they cannot get wrong (the subject), and the router matches on
    all of them.
    """
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
    subjects, which routes a session to CREATE and births the duplicate doc.
    """
    for suf in ("ing", "ed"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            w = w[: len(w) - len(suf)]
            break
    else:
        if (w.endswith("es") and len(w) - 2 >= 3
                and w[:-2].endswith(("ss", "us", "ch", "sh", "x"))):
            w = w[:-2]
        elif (w.endswith("s") and len(w) - 1 >= 3
                and not w.endswith(("ss", "us", "is", "ias"))):
            w = w[:-1]
    if not _ALPHA_WORD.match(w):
        return w                       # route.py, v2, s3: a name, not an inflected word
    if w.endswith("e") and len(w) >= 5:
        w = w[:-1]
    if w.endswith("y") and len(w) >= 4:
        w = w[:-1] + "i"
    return w


_ALPHA_WORD = re.compile(r"^[a-z][a-z-]*$")


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
                dropped.append("%s (too short)" % t)
            else:
                toks.append(t)
        for a, b in zip(words, words[1:]):
            if a in STOP or b in STOP:
                continue
            pair = "%s-%s" % (a, b)
            if len(pair) > 4 and pair not in toks:
                toks.append(pair)
    return toks, dropped


PROSE_CAP = {"memory": 6000, "script": 3000, "doc": 3000, "repo": 3000, "skill": 2500}
# What an unknown store gets. Named so the drift check and `_read` cannot disagree about it.
DEFAULT_CAP = 6000


def _read(p, n=None):
    """The artifact's text. `n` caps it FOR PROSE RANKING; None returns the whole file.

    See PROSE_CAP.
    """
    try:
        t = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return t if n is None else t[:n]


def _declaration_region(text, path=None):
    """The part of an artifact where a marker counts as ITS OWN declaration.

    An unparseable .py falls back to the old prose window - the conservative direction, since
    that is exactly what it got before this function existed.
    """
    return mentions_declaration_region(text, path)


def declared_tags(text, path=None):
    """This artifact's OWN route-tags line, found over the WHOLE file. "" when it has none.
    """
    if "route-tags" not in text:
        return ""
    m = TAG_RE.search(_outside_fences(_declaration_region(text, path)))
    return m.group(1) if m else ""


def _declared_extras(whole, path):
    """What an artifact declares ABOUT ITSELF beyond its tags: its copies and its enforcers.
    """
    m = copy_markers(whole, path)
    return {"copy_in": m["copy-in"], "enforced_by": m["enforced-by"], "sot": _sot(whole, path)}


def _sot(whole, path):
    """The domain this artifact declares itself the source of truth for, or None.
    """
    if "sot:" not in (whole or "") or _sot_of is None:
        return None
    try:
        return _sot_of(whole, path)
    except Exception:
        return None


def _fm_field(text, name):
    m = re.search(r"^%s:\s*(.+)$" % name, text[:1500], re.M)
    return m.group(1).strip() if m else ""


def harvest():
    """Every artifact across every covered store, as {path -> artifact}.

    Keyed by resolved path so one file is ONE artifact: a note listed twice inflates the
    receipt's count and burns display slots on duplicates. A receipt that overcounts is the same defect as one that undercounts.
    """
    arts = {}

    def add(kind, path, pairs, canonical=(), extra=None):
        key = str(path.resolve()).lower() if path.exists() else str(path).lower()
        if key in arts:
            arts[key]["signals"].extend(pairs)
            arts[key]["canonical"] |= {stem(c) for c in canonical}
        else:
            arts[key] = {"kind": kind, "path": path, "signals": list(pairs),
                         # STEMMED, because rank() matches on stems - storing raw terms here
                         # made the tier silently never fire, which is a guard that looks
                         # installed and does nothing.
                         "canonical": {stem(c) for c in canonical},
                         "copy_in": [], "enforced_by": [], "sot": None}
        for k, v in (extra or {}).items():
            if v:
                arts[key][k] = v

    from memory_index import stub_target as _stub_target
    stub_target = {}
    stub_dir = MEM / "m"
    if stub_dir.is_dir():
        for sp in stub_dir.glob("*.md"):
            tgt = _stub_target(_read(sp, 4000))
            # fold only when the note exists; a stub to a missing note stays a hit with its hook
            if tgt and (MEM / tgt).exists():
                stub_target[sp.resolve()] = tgt
    hooks = {}
    for line in _read(MEM / "MEMORY.md", 300000).split("\n"):
        for m in re.finditer(r"\(([\w./-]+\.md)\)", line):
            name = m.group(1).split("/")[-1]
            if m.group(1).startswith("m/") and (stub_dir / name).resolve() in stub_target:
                name = stub_target[(stub_dir / name).resolve()]
            hooks.setdefault(name, []).append(line)

    from corpus import files as _corpus_files
    by_kind = {}
    for kind, p in _corpus_files():
        by_kind.setdefault(kind, []).append(p)

    for p in by_kind.get("memory", []):
        if p.resolve() in stub_target:
            continue  # folded into its note (see POINTER STUBS above)
        whole = _read(p)
        t = whole[:PROSE_CAP["memory"]]
        add("memory", p, [
            (3, p.stem.replace("_", " ").replace("-", " ")),
            (2, _fm_field(t, "description")),
            (2, _fm_field(t, "name").replace("-", " ")),
            (3, declared_tags(whole, p)),
            (3, _subject_aliases(t)),
            (3, " ".join(CANONICAL_RE.findall(t))),
            (2, _facet_aliases(t)),
            (1, " ".join(re.findall(r"^#{1,3}\s+(.+)$", t, re.M)[:6])),
            (2, " ".join(hooks.get(p.name, []))),
        ], canonical=_canon(t), extra=_declared_extras(whole, p))

    m2 = MEM / "Memory2.md"
    if m2.exists():
        for line in _read(m2, 300000).split("\n"):
            lm = re.match(r"^- .*?\[(.+?)\]\(([\w./-]+)\)(.*)$", line)
            if lm:
                add("memory", MEM / lm.group(2), [(3, lm.group(1)), (2, lm.group(3))])

    for p in by_kind.get("script", []):
        whole = _read(p)
        t = whole[:PROSE_CAP["script"]]
        add("script", p, [
            (3, p.stem.replace("_", " ")),
            (2, " ".join(t.split("\n")[:6])),
            (3, declared_tags(whole, p)),
            (3, _subject_aliases(t)),
            (2, _facet_aliases(t)),
        ], canonical=_canon(t), extra=_declared_extras(whole, p))

    for kind in ("doc", "repo"):
        for p in by_kind.get(kind, []):
            whole = _read(p)
            t = whole[:PROSE_CAP[kind]]
            add(kind, p, [
                (3, p.stem.replace("-", " ").replace("_", " ")),
                (1, " ".join(re.findall(r"^#{1,2}\s+(.+)$", t, re.M)[:4])),
                (3, declared_tags(whole, p)),
                (3, _subject_aliases(t)),
                (2, _facet_aliases(t)),
                (1, t.split("\n\n")[1][:200] if "\n\n" in t else ""),
            ], canonical=_canon(t), extra=_declared_extras(whole, p))

    seen = set()
    for p in by_kind.get("skill", []):
        if p.parent.name in seen:
            continue
        seen.add(p.parent.name)
        whole = _read(p)
        t = whole[:PROSE_CAP["skill"]]
        add("skill", p, [
            (3, p.parent.name.replace("-", " ")),
            (2, _fm_field(t, "description")),
            (3, declared_tags(whole, p)),
            (3, _subject_aliases(t)),
            (2, _facet_aliases(t)),
        ], canonical=_canon(t), extra=_declared_extras(whole, p))

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


def sot_in_query(domain, qset, score=None, best=0):
    """True when EVERY word of a declared SOT domain is among the query's stems.
    """
    if not domain:
        return False
    words = {stem(w) for w in re.split(r"[-_ ]+", domain) if w}
    if not (bool(words) and words <= qset):
        return False
    if len(words) == 1 and score is not None and best:
        return score * 2 >= best
    return True


def rank(arts, toks):
    """(hits, weak) under the rarity-weighted word-match scoring."""
    stems = [stem(t) for t in toks]
    qset = set(stems)
    df = {s: sum(1 for a in arts if s in a["words"]) for s in stems}

    def rarity(s):
        return 3 if df[s] <= 3 else (2 if df[s] <= 12 else 1)

    raw = []
    for a in arts:
        matched = [s for s in set(stems) if s in a["words"]]
        if not matched:
            continue
        raw.append((sum(a["words"][s] * rarity(s) for s in matched) + 4 * len(matched), matched, a))
    best = max((r[0] for r in raw), default=0)
    scored = []
    for sc, matched, a in raw:
        canon = 1 if (any(s in a.get("canonical", set()) for s in matched)
                      or sot_in_query(a.get("sot"), qset, sc, best)) else 0
        scored.append((canon, sc, len(matched), a))
    scored.sort(key=lambda x: (-x[0], -x[1], -x[2]))
    hits = [(s, a) for _c, s, _m, a in scored if s >= 7]
    weak = [(s, a) for _c, s, _m, a in scored if 4 <= s < 7]
    return hits, weak, df


def _drifted_skills():
    """Names whose canonical copy != the copy a session actually LOADS.
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
    sot = "  SOT: %s" % a["sot"] if a.get("sot") else ""
    return "    %-7s %-62s %s%s%s" % (a["kind"], short[:62], age, sot, warn)


def _row_extras(a):
    """The lines printed UNDER a row: the copies and enforcers its artifact declares. [] if none.

    Enforcers are grouped by file, so twelve rules in one gate read as one line naming the gate
    and its rule ids rather than twelve lines repeating the path.
    """
    out = []
    if a.get("copy_in"):
        out.append("            copies (copy-in): %s" % "; ".join(a["copy_in"]))
    if a.get("enforced_by"):
        by_file = {}
        for v in a["enforced_by"]:
            f, _sep, rule = v.partition("#")
            by_file.setdefault(f, []).append(rule)
        out.append("            enforced by:      %s" % "; ".join(
            "%s#%s" % (f, ",".join(r for r in rules if r)) if any(rules) else f
            for f, rules in by_file.items()))
    return out


_SUBJ_CACHE = {}


def _declared_subject(path):
    """The cornerstone a file DECLARES, or None. Read lazily and cached.

    Read at verdict time from the top hits only, rather than captured during harvest. Harvest
    has a dozen `add(...)` call sites and every one would have needed a new argument; the
    verdict needs at most eight files. Fewer places to keep in step is the whole reason.
    """
    key = str(path)
    if key in _SUBJ_CACHE:
        return _SUBJ_CACHE[key]
    try:
        m = SUBJECT_RE.search(_outside_fences(_read(path, 40000) or ""))
    except Exception:
        m = None
    _SUBJ_CACHE[key] = m.group(1) if m else None
    return _SUBJ_CACHE[key]


def _subject_vocabulary(subj):
    """The stemmed alias vocabulary of a cornerstone, by NAME. Cached."""
    if subj in _SUBJ_VOCAB:
        return _SUBJ_VOCAB[subj]
    words = set()
    try:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
        from knowledge_pages import SUBJECTS
        desc, aliases = SUBJECTS.get(subj, ("", []))
        for a in list(aliases) + [subj.replace("-", " ")]:
            for w in re.split(r"[^a-z0-9]+", str(a).lower()):
                if w:
                    words.add(stem(w))
    except Exception:
        pass
    _SUBJ_VOCAB[subj] = words
    return words


_SUBJ_VOCAB = {}

_OWN_WORDS = {}


def _own_words(path):
    """The stems a file itself CONTAINS - not what its subject lends it. Cached.
    """
    key = str(path)
    if key in _OWN_WORDS:
        return _OWN_WORDS[key]
    try:
        text = (_read(path, 200000) or "").lower()
    except Exception:
        text = ""
    _OWN_WORDS[key] = {stem(w) for w in re.split(r"[^a-z0-9]+", text) if w}
    return _OWN_WORDS[key]

def partial_split(hits, toks, df, topn=8, band=None, artifact_count=None):
    """(subject, {term: [artifact]}, members) when one SUBJECT holds the answer in pieces.

        PARTIAL = two or more of the top hits declare the SAME cornerstone, and NO single one
                  of them holds all the distinguishing terms they hold between them.

    Which is the sprawl signal itself: the subject has a home, and the answer is spread across
    it. The receipt then names the subject to extend rather than only saying "do not create".
    """
    groups = {}
    for _s, a in hits[:topn]:
        subj = _declared_subject(a["path"])
        if subj:
            groups.setdefault(subj, []).append(a)
    if band is None:
        band = max(12, round(12 * (artifact_count or 0) / 1631))
    keys = {stem(x) for x in toks if df.get(stem(x), 99) <= band}
    qstems = {stem(x) for x in toks}
    for subj, members in groups.items():
        if len(members) < 2:
            continue
        if not (qstems & _subject_vocabulary(subj)):
            continue
        held = {}
        for a in members:
            own = _own_words(a["path"])
            for k in keys:
                if k in own:
                    held.setdefault(k, []).append(a)
        if not held:
            continue
        if any(all(k in _own_words(a["path"]) for k in held) for a in members):
            continue
        return subj, held, members
    return None


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
          % "; ".join(n for n, _w in _not_covered(arts)))
    print()

    if hits:
        print("  %d hit(s)%s:" % (len(hits), " (top 8 shown)" if len(hits) > 8 else ""))
        for s, a in hits[:8]:
            print(_row(s, a))
            for line in _row_extras(a):
                print(line)
        if len(hits) > 8:
            print("    ... and %d more below these" % (len(hits) - 8))
        print("    (dates are mtime - unreliable after a git clone/checkout; trust the")
        print("     artifact's own Verified/Reviewed fields over this column)")
        print()
        split = partial_split(hits, toks, df, artifact_count=len(arts))
        if split:
            subj, holders, _members = split
            print("  \u26d4 PARTIAL - the subject `%s` already has a home, and your answer is"
                  % subj)
            print("     SPLIT across it. The top hit does not carry every rare term you asked")
            print("     about; these artifacts under the same cornerstone do:")
            for s in sorted(holders):
                for a in holders[s][:2]:
                    print("       %-18s %s" % (s, _row(0, a).strip()[8:].strip()))
            print()
            print("     \u2b50 EXTEND ONE OF THESE, DO NOT CREATE A SECOND. Then make it")
            print("     findable by the terms you actually used:")
            print("       route.py enrich <file> --terms %s" % " ".join(toks))
            _VERDICT["last"] = "PARTIAL"
            return 0
        print("  ⭐ IF THE TOP HIT DOES NOT FULLY ANSWER, THAT IS A PARTIAL - NOT A MISS.")
        print("     Extend the artifact THAT HOLDS THE SUBJECT (not merely the top row),")
        print("     then make it findable by the terms you actually searched with:")
        print("       route.py enrich <file> --terms %s" % " ".join(toks))
        print("     A new artifact beside an old one is how docs go stale in pairs.")
        _VERDICT["last"] = "HIT"
        return 0

    if weak:
        print("  NO CONFIDENT HIT - but %d artifact(s) matched below the floor:" % len(weak))
        for s, a in weak[:6]:
            print(_row(s, a))
            for line in _row_extras(a):
                print(line)
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
    print("  ⭐ THE RECEIPT - paste this line into the doc you create.")
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
    print("  be extended because Y'). Then:")
    print("    route.py fixtures --add \"%s\" --must-hit <new-file-name>" % " ".join(toks))
    print("  so the next session finds it by the query that just failed.")
    _VERDICT["last"] = "VERIFIED MISS"
    return 1


# The markers a route-tags line belongs directly beneath, most specific first. Both are
# top-of-file frontmatter by construction, so anchoring to them puts the declaration in the
# block a reader already scans.
DEV_DOC_RE = re.compile(r"^<!--\s*dev-doc:.*?-->[^\S\n]*$", re.M | re.S)
H1_RE = re.compile(r"^#\s+\S.*$", re.M)


def _cut_tag_line(text, m):
    """Remove an existing tag line. Returns (text, the offset it was removed from).

    Takes the whole LINE when the marker is alone on it - leaving a blank line behind would
    make every enrich add one - and only the marker's own span when it shares the line with
    other content, where deleting the line would destroy that content.
    """
    start = text.rfind("\n", 0, m.start()) + 1
    end = m.end()
    if not text[start:m.start()].strip() and not text[end:].split("\n", 1)[0].strip():
        nl = text.find("\n", end)
        end = len(text) if nl == -1 else nl + 1
    else:
        start = m.start()
    return text[:start] + text[end:], start


def _tag_anchor(text):
    """Where a route-tags line belongs: an offset at a line boundary.

    Order is deliberate. Frontmatter must be cleared before anything else - a line inserted
    inside a YAML block corrupts it - and after that the most specific marker wins.

      1. below a `<!-- dev-doc: ... -->` marker, the workspace's own top-of-file frontmatter
      2. below a YAML frontmatter block
      3. below the H1, so the file still opens with its title
      4. the very top, when the file has none of those
    """
    at = 0
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if 0 < end < 1500:
            close = text.find("\n", end + 1)
            at = len(text) if close == -1 else close + 1
    dev = DEV_DOC_RE.search(text, at)
    if dev:
        nl = text.find("\n", dev.end())
        return len(text) if nl == -1 else nl + 1
    h1 = H1_RE.search(text, at)
    if h1:
        nl = text.find("\n", h1.end())
        return len(text) if nl == -1 else nl + 1
    return at


def cmd_enrich(args):
    """Append searched-with terms to the artifact so the next session's query hits.
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
        print("  THE DOCSTRING IS NOW THE RULE, not a suggestion: a marker anywhere else in")
        print("  a .py is read as a MENTION, not a declaration, so a script that writes the")
        print("  line into a file it generates does not accidentally claim those terms.")
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
    moved = False
    if m:
        text, removed_at = _cut_tag_line(text, m)
        anchor = _tag_anchor(text)
        moved = removed_at != anchor
    else:
        anchor = _tag_anchor(text)
    nl = "\r\n" if "\r\n" in text[:8000] else "\n"
    text = text[:anchor] + line + nl + text[anchor:]

    # newline-preserving write. Path.write_text on Windows translated every LF to CRLF, so
    # enriching one line rewrote the whole file's endings - a 1-line change arriving as a
    # 400-line diff, and the reviewer cannot see the line that matters.
    with open(p, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)

    covered = any(str(p.resolve()).lower().startswith(str(r.resolve()).lower())
                  for r in [MEM, WS] if r.exists())
    print("  enriched %s" % p.name)
    print("  route-tags now: %s" % " ".join(terms))
    if moved:
        print("  moved the tag line up to the file's frontmatter (it was further down)")
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
        want_v = f.get("must_verdict")
        if want_v and not missing:
            got_v = ("PARTIAL" if partial_split(hits, toks, _df, artifact_count=len(arts))
                     else ("HIT" if hits else ("WEAK" if weak else "VERIFIED MISS")))
            if got_v != want_v:
                bad += 1
                print("  [FAIL] %-38s -> verdict %s, wanted %s"
                      % (f["query"], got_v, want_v))
                continue
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
    """
    from corpus import files as _corpus_files
    return [p for _k, p in _corpus_files()]


FENCE_RE = re.compile(r"^(```|~~~).*?^\1", re.S | re.M)


def _outside_fences(text):
    """Text with fenced code blocks removed - a marker inside a fence is an EXAMPLE.
    """
    # One implementation, in the shared module - see `_declaration_region`.
    return mentions_outside_fences(text)


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

    `--follow` is deliberately omitted: it needs exactly one pathspec and misreports across some
    rename shapes. Cost is that a renamed artifact reports its rename date, i.e. looks younger.
    Again a false negative.
    """
    try:
        p = subprocess.run(["git", "-C", str(path.parent), "log", "--diff-filter=A",
                            "--format=%ad", "--date=short", "--", str(path)],
                           capture_output=True, text=True, timeout=20, creationflags=flags())
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


def _older_lines(older):
    """One printed line per older artifact a falsified receipt reaches - EVERY one of them.
    """
    out = []
    for _sc, a, created, src, tier in older:
        p = str(a["path"]).replace(str(WS) + "\\", "").replace(str(MEM) + "\\", "memory\\")
        out.append("       %-5s %-52s created %s (%s)" % (tier, p, created, src))
    return out


def cmd_receipts(_args):
    """Re-run every stored miss against TODAY's corpus.
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
        for line in _older_lines(older):
            print(line)
        print()
    print("  A NEWER artifact appearing here would be normal growth and is ignored. An OLDER")
    print("  one is not: it was sitting in the corpus while the miss was being declared, so")
    print("  the miss was wrong when it was made and the doc it authorised may be a duplicate.")
    print()
    print("  Read the older artifact, then close it one of three ways:")
    print("    - it IS the same subject   -> merge into it and tombstone the newer doc")
    print("      (knowledge_gate.py owns the tombstone shape)")
    print("    - they genuinely differ    -> one line in the newer doc, and this stops firing:")
    print("        <!-- distinct-from: <that path>: <how they differ> -->")
    print("    - the older doc was enriched with these terms AFTER the receipt date, so it")
    print("      could not have been found then -> also distinct-from, saying exactly that.")
    print("      Check with: git log -p --  <that path>   (look at when its tags changed)")
    return RECEIPT_REFUSE


def cmd_markers(_args):
    """Is every declaration this router reads actually inside the part of the file it reads?

    route-tags is NOT in this table, and its absence is the point: `declared_tags` reads it over
    the whole file, so it has no window left to fall out of. What remains here is every marker
    that still has one. If one of these is ever widened too, delete its row.
    """
    windowed = [
        ("canonical", CANONICAL_RE,
         "outranks every doc that merely contains the term - unread, the doc silently "
         "drops back to ordinary ranking"),
        ("cornerstone/subject", SUBJECT_RE,
         "unread, the doc loses its subject's whole vocabulary - up to 23 terms"),
    ]
    from corpus import files as _corpus_files

    problems, checked, tagged = [], 0, 0
    py_outside = []
    for kind, p in _corpus_files():
        text = _read(p)
        if not text:
            continue
        checked += 1
        cap = PROSE_CAP.get(kind, DEFAULT_CAP)
        region = _outside_fences(_declaration_region(text, p))
        for label, rx, why in windowed:
            mm = rx.search(region)
            if not mm:
                continue
            at = text.find(mm.group(0))
            if at >= cap:
                problems.append((label, kind, p, at, cap, why))
        if declared_tags(text, p):
            tagged += 1
        elif TAG_RE.search(_outside_fences(text)) and str(p).lower().endswith(".py"):
            # A .py carrying the marker somewhere other than its module docstring. Usually
            # correct - a generator emitting the line into the doc it writes - so this is
            # REPORTED, never failed: the file is not claiming the tag for itself.
            py_outside.append(p)

    print("route - marker placement")
    print()
    print("  artifacts examined                  : %d" % checked)
    print("  carrying a route-tags declaration   : %d" % tagged)
    print("  route-tags read over                : the WHOLE file (no window - see PROSE_CAP)")
    print("  windowed markers, by store          : %s"
          % ", ".join("%s=%d" % kv for kv in sorted(PROSE_CAP.items())))
    print()
    if py_outside:
        print("  %d .py file(s) mention route-tags outside their module docstring." % len(py_outside))
        print("  Not a fault - a script that WRITES the marker into a generated doc contains")
        print("  it without declaring it. Listed so the distinction stays visible:")
        for p in py_outside:
            print("     %s" % p.name)
        print()
    if problems:
        print("  FAIL - %d marker(s) sit outside the window their store reads." % len(problems))
        print("  Each one is inert: present in the file, invisible to the router.")
        print()
        for label, kind, p, off, cap, why in sorted(problems, key=lambda x: -x[3]):
            print("     %-20s char %-7d > %s cap %d" % (label, off, kind, cap))
            print("       %s" % p)
            print("       cost: %s" % why)
        print()
        print("  FIX: move the marker up into the file's frontmatter - directly below the")
        print("       <!-- dev-doc: ... --> marker, or below the H1. Never raise the cap:")
        print("       the cap bounds PROSE ranking cost, and raising it to rescue one file")
        print("       slows every query while leaving the next file to hit the same edge.")
        return 1
    print("  PASS - every windowed marker is inside the window its store reads, and every")
    print("         route-tags declaration is read whole-file regardless of depth.")
    return 0


def cmd_stores(_args):
    arts = harvest()
    counts = {}
    for a in arts:
        counts[a["kind"]] = counts.get(a["kind"], 0) + 1
    print("route - coverage")
    for k, v in sorted(counts.items()):
        print("  COVERED      %-12s %d unique artifacts" % (k, v))
    for name, why in _not_covered(arts):
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
    if argv and argv[0] == "markers":
        return cmd_markers(None)
    if argv and argv[0] == "receipts":
        return cmd_receipts(None)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("terms", nargs="+", help="the subject, in your own words")
    parsed = ap.parse_args(argv)
    rc = cmd_ask(parsed)
    _log_ask(parsed.terms, rc)
    return rc


try:
    from shared_state import home_state_dir as _home_state_dir
except ImportError:  # a published copy without shared_state.py keeps the historical path
    def _home_state_dir():
        return pathlib.Path.home() / ".claude" / "state"
USAGE_LOG = _home_state_dir() / "route-usage.jsonl"
_VERDICT = {"last": None}


def _log_ask(terms, rc):
    """One line per real question asked. The router could not answer anything about itself.

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
