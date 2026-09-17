"""Do the copies of a shared tool agree? Refuses when they do not.

o1's finding, and it outranks the SOP router it was raised against: *"the failure isn't usually
'which script?' - it's 'which COPY of the thing, and is my local view current?'"* They ran
`sweep start` from the plugin-cache copy and `sweep next` from `.shared/scripts`, and the state
did not carry - two copies, one task, different checksums.

⛔ MEASURED HERE, AND WORSE: `orchdoc.py` had THREE distinct versions that are all supposed to be
the same file - canonical, private marketplace, plugin cache. **The plugin cache is the copy a
session actually loads when it invokes the skill**, so a night of fixes can live entirely in a
copy that skill-invoking sessions never run.

⭐ WHY THIS IS THE ROUTER'S PREREQUISITE. A router that names `orchdoc_sweep.py` does not say
WHICH of the copies on disk, so it inherits the exact ambiguity it exists to remove. Naming the
right tool is worthless while the name resolves to three different files.

The public clone is EXPECTED to differ - it is sanitised on the way out, and that is what
publishing means. It is reported separately, never counted as drift.

    tool_drift.py            report every tracked tool
    tool_drift.py --sync     copy canonical over the private copies (never the public one)

Exit 0 = every copy agrees. Exit 1 = drift. Exit 2 = a copy is missing entirely.
"""
import argparse
import hashlib
import pathlib
import shutil
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HOME = pathlib.Path.home()
CANON = pathlib.Path(__file__).resolve().parent

# Where a copy of a shared tool is allowed to exist. The public clone is deliberately absent:
# it is sanitised by orchdoc_publish.py and comparing it here would report a working guard as a
# defect - the false-positive direction that gets guards disabled.
MIRRORS = [
    ("private marketplace", HOME / ".claude/plugins/marketplaces/<private-repo>"),
    ("plugin CACHE", HOME / ".claude/plugins/cache"),
]

TOOLS = ["orchdoc.py", "orchdoc_sweep.py", "safe_push.py", "orchdoc_publish.py", "route.py",
         "orchdoc_stamp.py", "orchdoc_restrike.py", "new_orchestrator.py", "count_report.sh",
         # The knowledge decision tree, shipped 2026-08-13 once workspace_paths.py made it
         # portable. Tracked here so the skill copy cannot silently fall behind canonical.
         "workspace_paths.py", "knowledge_gate.py", "knowledge_pages.py",
         "block_uncited_seed.py", "tool_drift.py", "test_knowledge_process.py",
         "route_fixtures.json",
         # ⛔ THE VOCABULARY MUST TRAVEL WITH THE ENGINE, and on 2026-08-17 it briefly did not.
         # knowledge_pages.py was split engine-from-data that day: the cornerstone tree moved
         # into this JSON, and the module treats an absent tree as a REAL STATE and writes
         # nothing. So a mirrored generator without its tree does not fail - it succeeds at
         # producing no map, which is the quietest possible way for a shipped tool to be inert.
         # Same reason route_fixtures.json is on this list.
         "knowledge_subjects.json"]

# ⛔ THE BLIND SPOT THIS FILE HAD, MEASURED 2026-08-12. Everything above is a SCRIPT. The
# SKILL.md that the scripts hang off was untracked - and it is the file that teaches the RULES.
# Result: the plugin cache copy of orchestrating-parallel-sessions was 117 lines behind canonical
# and still taught the bare `- ~~text~~` strike form the human had rejected twice, with ZERO mentions
# of the checkbox form that replaced it. Every session invoking the skill was being taught the
# superseded rule while the corrected copy sat in the marketplace, unread.
#
# ⭐ The instrument built to catch "the cache is what loads" was itself blind in the one place the
# rules live. A drift checker that covers the code but not the instructions is not covering the
# thing that changes behaviour.
SKILL_CANON = HOME / ".claude/plugins/marketplaces/<private-repo>/plugins/<your-skills-plugin>/skills"
SKILL_CACHE = HOME / ".claude/plugins/cache/<private-repo>/<your-skills-plugin>"


def _ver_key(name):
    """('1.4.7-2026.08.07') -> (1,4,7). Non-numeric leading parts sort lowest."""
    head = name.split("-")[0]
    parts = []
    for p in head.split("."):
        parts.append(int(p) if p.isdigit() else -1)
    return tuple(parts + [0] * (4 - len(parts)))[:4]


def _live_cache_dir():
    """The version the app actually loads. Older pinned versions are NOT drift - comparing
    every cached version would report a working archive as a defect, which is the
    false-positive direction that gets a guard switched off.

    ⛔ SELECTED BY VERSION, NOT MTIME. mtime picked 0.1.0 on 2026-08-12 and that was provably
    wrong: 0.1.0 contains no `orchdoc-audit` skill, yet `<your-skills-plugin>:orchdoc-audit` was
    available in the running session - so the loaded cache had to be a version that contains
    it. A stale archive can be touched at any time; its version number cannot drift upward.
    """
    if not SKILL_CACHE.exists():
        return None
    vers = [d for d in SKILL_CACHE.iterdir() if d.is_dir()]
    return max(vers, key=lambda d: _ver_key(d.name)) if vers else None


def survey_skills():
    """[(skill, canonical_digest, live_digest_or_None, path)] for every canonical skill."""
    rows = []
    live = _live_cache_dir()
    if not SKILL_CANON.exists() or live is None:
        return rows, live
    for d in sorted(SKILL_CANON.iterdir()):
        src = d / "SKILL.md"
        if not src.exists():
            continue
        mirror = live / "skills" / d.name / "SKILL.md"
        rows.append((d.name, sha(src), sha(mirror) if mirror.exists() else None, mirror))
    return rows, live


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()[:8]


def survey():
    """[(tool, canonical_digest, [(label, digest, path)])] for tools that have a canonical."""
    rows = []
    for tool in TOOLS:
        src = CANON / tool
        if not src.exists():
            continue
        copies = []
        for label, root in MIRRORS:
            if root.exists():
                copies += [(label, sha(p), p) for p in root.rglob(tool)]
        rows.append((tool, sha(src), copies))
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sync", action="store_true",
                    help="copy canonical over every drifted private copy")
    a = ap.parse_args()

    rows = survey()
    drifted, synced = [], 0
    print("tool drift - canonical is %s" % CANON)
    print()
    for tool, canon, copies in rows:
        bad = [c for c in copies if c[1] != canon]
        if not copies:
            print("  %-22s %s   (no mirrored copy)" % (tool, canon))
            continue
        if not bad:
            print("  %-22s %s   %d copy(ies) agree" % (tool, canon, len(copies)))
            continue
        drifted.append(tool)
        print("  %-22s %s   ⛔ %d of %d DRIFTED" % (tool, canon, len(bad), len(copies)))
        for label, d, p in bad:
            print("      %-20s %s  %s" % (label, d, str(p)[-58:]))
            if a.sync:
                shutil.copy2(CANON / tool, p)
                synced += 1

    # --- SKILL.md: the file that teaches the rules, and the one that was never checked. ---
    srows, live = survey_skills()
    print()
    if live is None:
        print("  skills: no plugin cache on disk - nothing to compare")
    else:
        print("  skills - canonical is the marketplace; live cache is %s" % live.name)
        for name, canon, mine, path in srows:
            if mine is None:
                drifted.append(name + "/SKILL.md")
                print("    %-34s %s   ⛔ MISSING from the live cache" % (name, canon))
            elif mine != canon:
                drifted.append(name + "/SKILL.md")
                print("    %-34s %s   ⛔ DRIFTED (cache %s) - sessions load the CACHE, so they"
                      % (name, canon, mine))
                print("    %-34s     are being taught this copy, not the canonical one." % "")
                if a.sync:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(SKILL_CANON / name / "SKILL.md", path)
                    synced += 1
            else:
                print("    %-34s %s   agrees" % (name, canon))

    print()
    if a.sync and synced:
        print("  synced %d copy(ies) from canonical." % synced)
        # ⭐ Re-survey rather than assert success - a sync that reports "done" without
        # re-measuring is the tool grading its own homework, which is the failure this
        # whole workstream keeps finding.
        still = [t for t, c, cs in survey() if any(x[1] != c for x in cs)]
        # Re-measure the SKILLS too. Re-surveying only the scripts would print
        # "every copy now agrees" while a drifted SKILL.md sat there unfixed - the tool
        # grading its own homework on the half it happens to look at.
        srows2, live2 = survey_skills()
        still += [n + "/SKILL.md" for n, c, m, _p in srows2 if m != c] if live2 else []
        if still:
            print("  ⛔ STILL DRIFTED after sync: %s" % ", ".join(still))
            return 1
        print("  re-measured: every copy now agrees.")
        return 0
    if drifted:
        print("  ⛔ %d tool(s) have copies that disagree. A session invoking the skill loads" %
              len(drifted))
        print("     the CACHE copy, so a fix in canonical may not be the code that runs.")
        print("     Fix: tool_drift.py --sync")
        return 1
    print("  every tracked tool agrees across every copy.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
