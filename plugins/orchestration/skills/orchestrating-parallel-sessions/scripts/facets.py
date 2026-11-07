#!/usr/bin/env python3
"""Controlled facets: the parts of a name a session must NOT be free to forget.

<!-- subject: knowledge-routing -->
<!-- route-tags: facet platform access mechanism component retired misconception vocabulary naming -->

A SUBJECT fixes the topic. It does not fix the four things a searcher most often types instead:
the **platform**, the **access mechanism**, the **business component**, and - the sharpest of
them - **the thing that does not exist.**

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

FLOORS = {"platform": 3, "access": 2, "component": 2}

VALUE_FLOORS = {"api": 3}

FACETS = [("platform", PLATFORMS), ("access", ACCESS), ("component", COMPONENTS)]


def declared(text, facet):
    return set(re.findall(r"<!--\s*%s:\s*([a-z0-9 ,-]+?)\s*-->" % facet, text))


def detect(text, table, floor=2, path=None):
    """Facet values this document is PLAINLY about - mentioned at least `floor` times.

    The floor exists because a passing mention is not aboutness. One naming of a platform, in a
    sentence about something else, should not force a platform facet; a doc that names it five
    times is a doc about that platform whether or not its author framed it that way.
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
            lb = r"(?<![a-z0-9.])" if "." in a else r"(?<![a-z0-9])"
            for m in re.finditer(lb + re.escape(a) + r"(?![a-z0-9])", low):
                # Key on the START offset: two aliases matching at the same place are the same
                # occurrence, however different their lengths.
                spans.add(m.start())
        if len(spans) >= max(floor, VALUE_FLOORS.get(value, 0)):
            found[value] = len(spans)
    return found


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
NEVER_FACET = {"CLAUDE.md",
               "DEV-DOCS-INDEX.md",
               "MEMORY.md",
               "Memory2.md",
               "README.md",
               "ORCHESTRATOR-REGISTRY.md"}

EPHEMERAL = re.compile(r"(-SEED|-PROMPT|-bootstrap|-bridge|-PREVIEW|-SKILL-DRAFT|-updated)"
                       r"\.md$", re.I)

GENERATED = re.compile(r"<!--\s*GENERATED by\s")


def _docs():
    """The corpus the FACET rule governs - deliberately narrower than route.py's.

    Facets are for docs a session will LOOK SOMETHING UP IN. Research logs are read forward,
    not searched sideways.
    """
    docs = [p for p in WS.glob("*.md")] + [p for p in (WS / "docs").rglob("*.md")]
    mem = [p for p in MEM.glob("*.md")]          # top level only: the curated notes
    keep = [p for p in docs + mem
            if p.name not in NEVER_FACET and not EPHEMERAL.search(p.name)
            and not p.name.startswith("ORCHESTRATOR-DECISIONS-")
            and "captured_notes" not in p.parts and "research_log" not in p.parts]
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
