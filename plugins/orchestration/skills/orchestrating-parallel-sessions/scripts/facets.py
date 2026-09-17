#!/usr/bin/env python3
"""Controlled facets: the parts of a name a session must NOT be free to forget.

<!-- subject: knowledge-routing -->
<!-- route-tags: facet platform access mechanism component retired misconception vocabulary naming -->

\1The human, 2026-08-13: *"if a platform is involved, the platform MUST be included... If it involves a
specific mechanism for accessing it... Any potential MISconceptions about how it is accessed."*

A SUBJECT fixes the topic. It does not fix the four things a searcher most often types instead:
the **platform**, the **access mechanism**, the **business component**, and - the sharpest of
them - **the thing that does not exist.**

⛔ THE FACETS ARE DETECTED, NOT REQUESTED. Asking an author to remember four tags is the same
bet that already failed. This reads the document, finds the platform it is plainly about, and
REFUSES the commit until the facet is declared. The author cannot forget what the machine
noticed for them.

⭐ THE RETIRED FACET IS THE ONE NOBODY WOULD THINK TO BUILD, and it is the highest value. On
2026-07-15 a deploy platform's MCP integration was deleted on the workspace owner's direction;
sessions kept reaching for it for weeks because three documented copies of "don't" lost to one
habit. A session searching for that integration today must NOT get a clean miss - a clean miss
reads as "nobody has covered this" and invites re-adding the exact thing that was removed.
Absence of a record is not a record of absence, and for a retired path the difference is the
whole cost.

    facets.py --audit          which docs are missing a facet they plainly need
    facets.py --check <path>   what THIS doc is missing
    facets.py --selftest
"""
import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from workspace_paths import WS, MEM  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ⛔ MECHANISM HERE, VOCABULARY IN facets_vocabulary.json - and the split was forced by trying
# to PUBLISH this file. 22 of its leak lines were its own data - seven vendor and product names
# that ARE the controlled vocabulary, not incidental mentions a sanitizer can strip. A sanitized
# copy would have been a facet engine with no facets.
#
# ⭐ It is the right design regardless of publishing: adding a platform is now a data edit, not
# a code edit, so anyone can extend the vocabulary without touching detection logic - and the
# engine can travel to a workspace whose vendors are entirely different.
def _load():
    p = pathlib.Path(__file__).resolve().parent / "facets_vocabulary.json"
    try:
        v = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        # No vocabulary is a real state, not an error: the engine still runs and simply
        # detects nothing. Failing hard here would break every gate that imports this.
        return {}, {}, {}, {}
    return (v.get("platforms", {}), v.get("access", {}), v.get("components", {}),
            {k: (d["why"], d["aliases"]) for k, d in v.get("retired", {}).items()})


PLATFORMS, ACCESS, COMPONENTS, RETIRED = _load()

# ⛔ PLATFORM CARRIES A HIGHER FLOOR, and the reason is the limit of counting itself.
# A doc that ILLUSTRATES with a vendor is not a doc ABOUT that vendor. My own gate notice was
# tagged with a payments vendor because it names two of that vendor's filenames as an example;
# the notice is about the gate. At a floor of 2 that distinction is unreachable - two mentions
# is exactly what an illustration looks like.
#
# ⭐ Measured rather than guessed: raising the platform floor to 3 drops 50 of 279 detections,
# and every true positive verified by hand survives it (a GPU host x9, a payments vendor x74,
# a deploy platform x11, a CDN x3) while the illustrative ones (that same payments vendor,
# elsewhere, x2) do not. The four counts are kept apart on purpose - they are four independent
# vendors agreeing, and collapsing them to "a vendor" would read as one case. Access and
# component stay at 2 -
# they assert what a doc IS, where a platform facet asserts a VENDOR RELATIONSHIP that a reader
# will act on.
FLOORS = {"platform": 3, "access": 2, "component": 2}

# ⛔ ONE ALIAS IS NOT A PROPER NOUN, AND IT CLEARS A FLOOR OF 2 BY ITSELF. This is bug 3
# arriving in the ACCESS table, where that fix was never applied - there it was solved by
# requiring platform aliases to be proper nouns, and `api` is exactly the term that rule would
# have excluded. It is the most ubiquitous technical noun in this corpus and it turns up
# everywhere except in documents about API access:
#
#   `-api-key` / `<vendor>-api-token.txt` / `<vendor>-api-key.txt`     secret FILENAMES
#   `[[<vendor>_api_gotchas]]`                                         a WIKILINK to another doc
#   "The source - the file, the API, the actual branch - answers..."   a stock phrase
#
# ⭐ Measured 2026-08-17 over the whole corpus, not guessed: 83 documents detect `api`, and
# the band at exactly 2 holds 18 of them - a git-hygiene doc, a stale-descriptions writing
# principle, a handoff-phrasing note, an MCP-staleness note. Every one hand-checked, none of
# them about API access. At 3 the band is a deploy-platform reliability history, a migrations
# runbook and a staging-auth rules note - all plainly right.
#
# Keyed by VALUE, not by facet, so every caller inherits it without a signature change -
# facets_retract.py re-derives through this same function and must not be able to disagree.
VALUE_FLOORS = {"api": 3}

FACETS = [("platform", PLATFORMS), ("access", ACCESS), ("component", COMPONENTS)]


def declared(text, facet):
    return set(re.findall(r"<!--\s*%s:\s*([a-z0-9 ,-]+?)\s*-->" % facet, text))


def detect(text, table, floor=2, path=None):
    """Facet values this document is PLAINLY about - mentioned at least `floor` times.

    ⛔ A DERIVED DOCUMENT GETS NO FACETS AT ALL, whatever the counts say. A template names a
    vendor in every example row and is not about that vendor; a generated view lists whatever it
    was computed from; a test must contain the strings it exercises. Measured 2026-08-17: the
    cornerstone TEMPLATE was tagged `platform: external_provider` and `access: api` off its own placeholder
    rows, and the platform floor of 3 did not save it because the examples repeat.

    ⭐ The floor answers "is one mention aboutness?". This answers a different question the floor
    cannot reach: "is this document making a claim at all?" Four guards needed that same test on
    one day, so it lives in mentions.py.

    The floor exists because a passing mention is not aboutness. One naming of a platform, in a
    sentence about something else, should not force a platform facet; a doc that names it five
    times is a doc about that platform whether or not its author framed it that way.

    ⛔ WORDS, NOT SUBSTRINGS - the same defect route.py already fixed and this file repeated.
    `low.count("cli")` counts the "cli" inside *client*, *click* and *decline*; `"api"` counts
    the one inside *therapies* and *rapid*. Measured 2026-08-13: the backfill dry run tagged a
    sleep guide `access: api, cli`, and at 266 documents that would have written confident
    wrong facets across the corpus.

    ⭐ A wrong facet is worse than a missing one. Missing merely fails to route someone to the
    right doc; wrong actively routes them to the wrong one, with the map's authority behind it.

    ⛔ COUNT DISTINCT POSITIONS, NOT MATCHES PER ALIAS. Aliases nest - a bare brand word sits
    inside its own hostname (`acme` inside `acme.app`, `acme` inside `acme.com`), and a closed
    compound sits inside its spaced form (`acmecloud` inside `acme cloud`) - so summing per alias
    counts ONE occurrence twice and clears a floor of 2 on its own. Found doc-by-doc: a
    writing-principles doc contained a single staging URL and was about to be declared a
    document about that deploy platform.
    """
    import sys as _s
    import pathlib as _p
    _s.path.insert(0, str(_p.Path(__file__).resolve().parent))
    from mentions import is_derived
    if is_derived(text, path)[0]:
        return {}

    low = text.lower()
    found = {}
    for value, aliases in table.items():
        spans = set()
        for a in aliases:
            # ⛔ A HOSTNAME ALIAS MUST MATCH A WHOLE HOST, NOT A SUFFIX OF ONE.
            # `app.example.com` is the product app; `example.com` is the marketing site -
            # different repo, different deploy path, different component - and the second is a
            # SUFFIX of the first. So every app document also detected as a marketing-site
            # document. Measured 2026-08-17: 10 detections, and every one is a false positive;
            # `feedback_deploy_terms_plain_with_urls.md` was about to be declared a
            # marketing-site doc on the strength of two app URLs and nothing else.
            #
            # ⭐ BUG 5 CANNOT REACH THIS, which is why it survived that fix. Counting distinct
            # POSITIONS collapses two aliases matching at the same offset; here the two matches
            # start four characters apart, so they are honestly distinct positions of what is
            # one hostname. The defect is not double-counting, it is reading a subdomain as its
            # parent.
            #
            # Only aliases that CONTAIN A DOT are hostnames. A bare brand word stays loose on
            # purpose: `acme` should still be found inside `app.acme.com`, and inside
            # `acme.app` - the platform is the platform at any subdomain.
            lb = r"(?<![a-z0-9.])" if "." in a else r"(?<![a-z0-9])"
            for m in re.finditer(lb + re.escape(a) + r"(?![a-z0-9])", low):
                # Key on the START offset: two aliases matching at the same place are the same
                # occurrence, however different their lengths.
                spans.add(m.start())
        if len(spans) >= max(floor, VALUE_FLOORS.get(value, 0)):
            found[value] = len(spans)
    return found


# ⛔ THE THIRD DOOR, AND IT IS NOT A LOOPHOLE - it is the same door knowledge_gate opens with
# `<!-- unindexed: <reason> -->`, for the same reason: "nobody has looked at this yet" and
# "somebody looked and decided no" are indistinguishable on disk, and the difference is the
# whole value of an audit.
#
# ⭐ WHY A COUNT CANNOT CLOSE THESE. Bug 8 found a case no threshold could reach; this is the
# other one. `docs/knowledge/CREATE.md` names one vendor 10 times, and all 10 are the SAME
# recounted incident being used as the page's standing worked example - the page is about how
# to write a knowledge doc. The knowledge decision-tree specs do the identical thing with two
# vendors. Raising a floor cannot separate "cited ten times as the example" from "discussed
# ten times as the topic", and two candidate structural rules were built and MEASURED against
# the whole corpus before this was written, both refuted by their own numbers:
#
#   position ("a doc about a vendor names it early")  dropped the OAuth-setup note, the service
#                                                     access guide, the object-store CLI note
#                                                     - 93 hits
#   markup   ("a mention in a code span is a literal") dropped an API-gotchas note, the same
#                                                     OAuth-setup note, a secrets-backup todo
#                                                     - 94 hits
#
# So the residue is a judgement, and a judgement should be RECORDED where it was made rather
# than re-litigated by every future pass. The reason is mandatory and must be a real sentence:
# an escape that costs nothing gets used for everything, and one that costs a sentence keeps
# its meaning - the same bargain safe_push.py makes with `--because`.
#
#     <!-- not-facet: platform: acme - the founding failure is this page's standing example -->
# Values may be comma-joined, exactly as a DECLARATION may be - one aggregate ledger declined
# seven platforms for one reason, and seven near-identical lines in a header is how a marker
# stops being read. The reason is shared because the judgement is shared.
NOT_FACET = re.compile(r"<!--\s*not-facet:\s*(platform|access|component):\s*"
                       r"([a-z0-9, -]+?)\s+-\s+(\S[^>]*?)\s*-->")
MIN_REASON = 12


def declined(text):
    """{(facet, value)} this doc has looked at and deliberately refused, with a stated why."""
    out = set()
    for m in NOT_FACET.finditer(text):
        if len(m.group(3).strip()) < MIN_REASON:
            continue
        for v in m.group(2).replace(",", " ").split():
            out.add((m.group(1), v))
    return out


def missing(text):
    """[(facet, value, count)] the doc is plainly about but has not declared."""
    out = []
    skip = declined(text)
    for facet, table in FACETS:
        have = " ".join(declared(text, facet))
        for value, n in detect(text, table, FLOORS.get(facet, 2)).items():
            if value not in have and (facet, value) not in skip:
                out.append((facet, value, n))
    return out


def retired_hits(text):
    """Retired paths this doc discusses, so it can be made the landing place for them."""
    low = text.lower()
    return [k for k, (_why, aliases) in RETIRED.items() if any(a in low for a in aliases)]


def facet_aliases(text):
    """Every alias implied by this doc's DECLARED facets - expanded at query time by route.py.

    Same principle as subject expansion: the author declares one canonical value, and the
    router matches everything anyone might type for it. Widening an alias list later improves
    every doc already carrying that facet, without touching one of them.
    """
    words = []
    for facet, table in FACETS:
        for val in " ".join(declared(text, facet)).replace(",", " ").split():
            words += table.get(val, [])
    for k in re.findall(r"<!--\s*retired:\s*([a-z0-9 ,-]+?)\s*-->", text):
        for kk in k.replace(",", " ").split():
            if kk in RETIRED:
                words += RETIRED[kk][1]
    return " ".join(words)


# Not routed TO: the auto-loaded instruction files and the index itself. A facet on CLAUDE.md
# would tag the push channel as if it were a destination, and on DEV-DOCS-INDEX the map as if
# it were the territory.
NEVER_FACET = {"CLAUDE.md", "DEV-DOCS-INDEX.md", "MEMORY.md", "Memory2.md", "README.md",
               "ORCHESTRATOR-REGISTRY.md",
               # The script-writing canon: a rule family read start-to-finish, like
               # CLAUDE.md. knowledge_gate already exempts it; this file did not.
               "AUTHORING-CHECKLIST.md", "SCRIPT-CHECKLIST-2-LENGTH.md",
               "SCRIPT-CHECKLIST-2B-PAUSE.md", "SCRIPT-CHECKLIST-3-DROPDOWNS.md",
               "SCRIPT-CHECKLIST-4-VOICE.md", "SCRIPT-CHECKLIST-5-FORMAT-AND-GATE.md"}

# ⛔ LAUNCH INPUTS, NOT KNOWLEDGE - the same exclusion knowledge_gate already applies, which
# this file failed to inherit. A lane seed names every platform its lane will touch, so it
# detects as a doc about all of them; it is read ONCE, by ONE lane, and never searched for
# again. Facets on it pollute the map with one-shot prompts that outrank real references.
EPHEMERAL = re.compile(r"(-SEED|-PROMPT|-bootstrap|-bridge|-PREVIEW|-SKILL-DRAFT|-updated)"
                       r"\.md$", re.I)

# ⛔ A GENERATED VIEW IS THE MAP, NOT THE TERRITORY - bug 4 arriving from a new direction.
# `docs/knowledge/*.md` are rendered by knowledge_pages.py from the artifacts they LIST.
# Measured 2026-08-17 on commerce.md: it detects one payment vendor six times, and every hit is
# either a linked document's FILENAME or its own "Searched as" alias row. Not one of them is
# the page being about a vendor - it is a directory OF vendor docs, and its own banner says
# "route to the SOURCES, not to this page". Tagging it puts a middleman in front of the answer.
#
# ⭐ And the second, independent reason: render() rebuilds these files from scratch, so a facet
# written here is erased on the next --write while the audit keeps demanding it - a backlog
# item that can never be closed, and a hand-edit of a file whose first line forbids hand-edits.
#
# Matched by SHAPE against the banner knowledge_pages.py writes (marker-format-is-a-contract);
# the selftest round-trips this regex against that module's own BANNER so the two cannot drift.
GENERATED = re.compile(r"<!--\s*GENERATED by\s")


def _docs():
    """The corpus the FACET rule governs - deliberately narrower than route.py's.

    ⛔ The first version scanned everything and reported 842 documents 'missing a facet they
    plainly need', most of them voice-note research entries that mention Google once per
    paragraph and are not process docs at all. A gate whose refusals are mostly wrong gets
    switched off, and takes the real ones with it - the same measurement that stopped the
    reachability gate from demanding an index row for lane seeds.

    Facets are for docs a session will LOOK SOMETHING UP IN. Research logs are read forward,
    not searched sideways.
    """
    docs = [p for p in WS.glob("*.md")] + [p for p in (WS / "docs").rglob("*.md")]
    mem = [p for p in MEM.glob("*.md")]          # top level only: the curated notes
    keep = [p for p in docs + mem
            if p.name not in NEVER_FACET and not EPHEMERAL.search(p.name)
            and not p.name.startswith("ORCHESTRATOR-DECISIONS-")
            and "voice_notes" not in p.parts and "research_log" not in p.parts]
    # Read to exclude generated views. Costs one open per doc; the alternative is a path rule
    # that would go stale the moment a generated page is written anywhere else.
    out = []
    for p in keep:
        try:
            if not GENERATED.search(p.read_text(encoding="utf-8", errors="replace")[:400]):
                out.append(p)
        except OSError:
            out.append(p)
    return out


def cmd_audit():
    gaps = 0
    refused = []
    print("facet audit - documents plainly about a platform/access/component that do not say so")
    print()
    for p in _docs():
        try:
            t = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for facet, value in sorted(declined(t)):
            refused.append((p.name, facet, value))
        m = missing(t)
        if m:
            gaps += 1
            print("  %s" % p.name)
            for facet, value, n in m[:4]:
                print("      <!-- %s: %s -->   (mentioned %d times)" % (facet, value, n))
    print()
    print("  %d document(s) missing a facet they plainly need." % gaps)
    # ⭐ PRINTED, ALWAYS. An opt-out nobody can see is the loophole the reason-requirement was
    # meant to prevent; listed here, every declined facet stays challengeable by the next pass.
    if refused:
        print()
        print("  %d facet(s) inspected and DECLINED as illustration, not aboutness:" % len(refused))
        for name, facet, value in refused:
            print("      %-52s %s: %s" % (name[:52], facet, value))
    print("  RETIRED paths with no landing doc:")
    covered = set()
    for p in _docs():
        try:
            covered |= set(retired_hits(p.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            pass
    for k in RETIRED:
        if k not in covered:
            print("     ⛔ %s - a search for this returns a CLEAN MISS, which reads as "
                  "permission to rebuild it" % k)
    return 1 if gaps else 0


# ⛔ THE SELFTEST OWNS ITS VOCABULARY - it must not assert against the live table.
# Every assertion below used to name real entries from facets_vocabulary.json, which made the
# test unrunnable anywhere but this one workspace: a checkout whose vendors are different, or
# one where somebody renamed a single entry, fails on the DATA while the engine is perfectly
# correct. That is the same defect recorded in stale_rows() - a test written from the thing it
# is checking confirms that thing rather than the requirement.
#
# ⭐ These fixtures are also a legible specification. `acme` / `globex` / `example.com` carry
# the exact SHAPES the rules turn on - a bare brand word, a hostname alias, a short secondary
# alias, a subdomain that is a suffix of its parent - and nothing else, so a reader can see
# which property each assertion is about instead of inferring it from a vendor's name.
FIXTURE_PLATFORMS = {
    "acme": ["acme", "acme.app", "acm"],     # bare brand + hostname + a short secondary alias
    "globex": ["globex"],
}
FIXTURE_ACCESS = {"api": ["api"], "webhook": ["webhook"]}
FIXTURE_COMPONENTS = {
    "webapp": ["webapp", "app.example.com"],   # the subdomain
    "marketing-site": ["example.com"],         # ...whose parent is a SUFFIX of it
}
FIXTURE_RETIRED = {
    "acme-mcp": ("The vendor's MCP integration was removed; use a static token and curl.",
                 ["acme mcp", "mcp__acme"]),
}


def cmd_selftest():
    ok = True

    def t(label, cond):
        nonlocal ok
        print("  [%s] %s" % ("OK " if cond else "FAIL", label))
        ok &= bool(cond)

    # Swapped in for the duration, and restored in the `finally` below: detect/missing/
    # facet_aliases/retired_hits all read these at CALL time, so the fixture reaches every
    # one of them without changing a single signature.
    global PLATFORMS, ACCESS, COMPONENTS, RETIRED, FACETS
    saved = (PLATFORMS, ACCESS, COMPONENTS, RETIRED, FACETS)
    PLATFORMS, ACCESS, COMPONENTS, RETIRED = (FIXTURE_PLATFORMS, FIXTURE_ACCESS,
                                              FIXTURE_COMPONENTS, FIXTURE_RETIRED)
    FACETS = [("platform", PLATFORMS), ("access", ACCESS), ("component", COMPONENTS)]
    try:
        print("facets selftest")
        acme = "acme deploy. acme token. acme service."
        t("detects a platform the doc is plainly about", "acme" in detect(acme, PLATFORMS))
        t("a single passing mention does NOT force a facet",
          "acme" not in detect("we also use acme once", PLATFORMS))
        t("declared facet is not reported missing",
          not [m for m in missing("<!-- platform: acme -->" + acme) if m[1] == "acme"])
        t("undeclared facet IS reported missing",
          any(m[1] == "acme" for m in missing(acme)))
        t("declared facet expands to its aliases",
          "acm" in facet_aliases("<!-- platform: acme -->"))
        t("a retired path is recognised",
          retired_hits("the acme mcp is gone") == ["acme-mcp"])
        t("retired expansion needs the marker",
          "mcp__acme" in facet_aliases("<!-- retired: acme-mcp -->"))
        # ⭐ THE CONTRACT MUST ROUND-TRIP: what knowledge_pages.py WRITES must be what this
        # file READS. Asserting the regex against a hand-typed string would test my typing,
        # not the contract - the same defect that let stale_rows() ship with its own
        # assumption encoded in its own test.
        try:
            import knowledge_pages
            t("the generated banner that module writes is matched by the regex we exclude with",
              bool(GENERATED.search(knowledge_pages.BANNER)))
        except Exception as e:
            t("knowledge_pages.BANNER is importable for the round-trip check (%s)" % e, False)
        t("an ordinary doc is not mistaken for a generated one",
          not GENERATED.search("# A doc\nit mentions a GENERATED file elsewhere"))
        # The ambiguous-alias floor. Two mentions of `api` is what a secret FILENAME and a
        # wikilink look like; it must not be what a facet looks like.
        t("two mentions of the ambiguous alias `api` do NOT force an access facet",
          "api" not in detect("the acme-api-token.txt file. see [[acme_api_gotchas]].", ACCESS))
        t("...but three do", "api" in detect("api access. the api key. call the api.", ACCESS))
        t("a NARROW access alias still clears at two (the floor is per-value, not global)",
          "webhook" in detect("the webhook fires. verify the webhook secret.", ACCESS))
        # The third door. It must open on a stated reason and STAY SHUT on an empty one.
        ill = "<!-- not-facet: platform: acme - cited here only as the retired-MCP example -->"
        t("an inspected refusal with a reason suppresses that one facet",
          not [m for m in missing(ill + acme) if m[1] == "acme"])
        t("...and only that one - the doc's other facets are untouched",
          any(m[1] == "globex" for m in missing(ill + acme + " globex globex globex")))
        t("a refusal with NO reason is not a refusal",
          any(m[1] == "acme"
              for m in missing("<!-- not-facet: platform: acme - x -->" + acme)))
        t("one refusal line can carry several values, as a declaration can",
          declined("<!-- not-facet: platform: acme, globex - an aggregate ledger, not a "
                   "reference -->") == {("platform", "acme"), ("platform", "globex")})
        # The hostname rule. The app's host must not read as the marketing site's host.
        appurl = "https://app.example.com/x and https://app.example.com/y"
        t("a SUBDOMAIN does not detect as its parent host",
          "marketing-site" not in detect(appurl, COMPONENTS))
        t("...while the subdomain's own component still does",
          "webapp" in detect(appurl, COMPONENTS))
        t("the apex host itself still detects",
          "marketing-site" in detect("see example.com and example.com/blog", COMPONENTS))
        t("a bare BRAND alias stays loose - a platform is itself at any subdomain",
          "acme" in detect("us.acme.com, eu.acme.com, app.acme.com", PLATFORMS))
        # ⭐ THE ENGINE MUST SURVIVE AN ABSENT VOCABULARY. A public checkout ships no
        # facets_vocabulary.json, and every gate in the workspace imports this module at
        # commit time - so "no vocabulary" has to be a real state that detects nothing, never
        # an exception that takes the commit down with it.
        FACETS = [("platform", {}), ("access", {}), ("component", {})]
        RETIRED = {}
        t("an empty vocabulary detects nothing rather than raising", detect(acme, {}) == {})
        t("...so a doc plainly about a platform is asked for no facet at all",
          missing(acme) == [])
        t("...and no retired path is claimed", retired_hits(acme + " acme mcp") == [])
    finally:
        PLATFORMS, ACCESS, COMPONENTS, RETIRED, FACETS = saved
    print("\nselftest %s" % ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--check")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(cmd_selftest())
    if a.check:
        txt = pathlib.Path(a.check).read_text(encoding="utf-8", errors="replace")
        m = missing(txt)
        for facet, value, n in m:
            print("  <!-- %s: %s -->   (mentioned %d times)" % (facet, value, n))
        print("  %d facet(s) missing" % len(m))
        sys.exit(1 if m else 0)
    sys.exit(cmd_audit())
