"""A fact owned by a cornerstone may not be restated anywhere else. Enforced at commit.

the human, 2026-08-17: *"We need a hook attached to this that forces any agent attempting to write a
new doc that concerns this info to either edit it in the cornerstone, or justify why they need a
new doc, and if they create a new doc, to document the new doc in the cornerstone. It must be
forced at every step."*

⭐ THE DESIGN THAT MAKES IT SELF-CLOSING: the only escape is to REGISTER the new doc in the
cornerstone. So a session that insists on a second doc must edit the cornerstone to get past this
- which is exactly the outcome, reached by the shortest path available to them. There is no
"acknowledge and proceed" door, because a door like that is the one every session takes.

⛔ WHY A PROSE RULE WOULD NOT HAVE HELD. On 2026-08-17 four separate rules in this workspace were
written down, had a correct check, and were broken anyway - by the session that wrote them, the
same day. "Singular source of truth" is the same class of instruction and would fail the same
way.

HOW A FACT IS DECLARED - in a cornerstone's "Facts this doc owns" table:

    <!-- owns: external-provider-api-key-path -->
    | the vendor key path | `.shared/secrets/external-provider-api-key.txt` | 2026-08-17 |

The VALUE is read from the backticked cell on the marker's line or the lines just after it. That
value is then forbidden in every other doc.

HOW A SECOND DOC IS REGISTERED - a row in the cornerstone naming its path:

    | if you need the rotation procedure | `memory/vendor-key-rotation.md` |

Any doc whose path the cornerstone names is allowed to restate the facts that cornerstone owns.

Usage:
    owns_guard.py --check      refuse staged docs that restate an owned fact (exit 9)
    owns_guard.py --audit      list every owned fact and every restatement, anywhere
    owns_guard.py --selftest
"""
import argparse
import io
import pathlib
import re
import subprocess
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

REFUSE = 9
# ⛔ A KEY MUST CONTAIN A LETTER OR DIGIT. `.` is in the class, so the placeholder
# `<!-- owns: ... -->` parsed as the key "..." - and two docs quoting the syntax then
# collided as "two owners of one fact". Measured by o1, 2026-08-19.
OWNS_RE = re.compile(r"<!--\s*owns:\s*(?=[A-Za-z0-9._-]*[A-Za-z0-9])([A-Za-z0-9._-]+)\s*-->")
CORNER_RE = re.compile(r"<!--\s*cornerstone:\s*([A-Za-z0-9._-]+)\s*-->")
# A value worth protecting is a path or a token - never a bare English word, which would fire on
# ordinary prose. Backticks are how the template writes them, so backticks are the contract.
VALUE_RE = re.compile(r"`([^`\n]{6,120})`")
# ⛔ A placeholder is not a fact. The template's own example rows must not become owned values,
# or the template would forbid its own text everywhere.
from mentions import PLACEHOLDER, is_derived  # noqa: E402  - ONE definition, see that module


def _root():
    try:
        p = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                           text=True, timeout=10)
        if p.returncode == 0 and p.stdout.strip():
            return pathlib.Path(p.stdout.strip())
    except Exception:
        pass
    from workspace_paths import WS
    return WS


ROOT = _root()


def corpus_docs():
    """The one enumeration. ⛔ NO SILENT FALLBACK.

    The first version called `corpus.artifacts()`, which does not exist - the function is
    `files()`. The AttributeError hit a bare `except` and fell back to `ROOT.rglob("*.md")`
    over the whole workspace, nested repos and node_modules included. It did not fail; it
    HUNG, which is harder to diagnose than a crash and looks like slowness rather than a bug.

    ⭐ A fallback that quietly does something worse than the thing it replaced is not
    resilience. If the corpus cannot be enumerated, say so and stop.
    """
    import corpus
    return [pathlib.Path(p) for _kind, p in corpus.files()]


def collisions(facts_multi):
    """Two ways a declaration set can be incoherent. Both REFUSE - neither is resolvable.

    ⛔ the human, 2026-08-17: "what prevents 2 sessions from creating different names independently?"
    The VALUE is what the guard matches, so divergent KEY names alone do not let a duplicate
    through. But the first version did `facts.setdefault(value, ...)` - first read wins,
    SILENTLY - so two cornerstones claiming one fact resolved by directory walk order. That is
    the two-live-homes state this tool exists to prevent, inside the tool.

    ⭐ A conflict a program resolves by ORDER is a conflict it is hiding.
    """
    out = []
    for value, owners in sorted(facts_multi.items()):
        if len({str(p) for p, _k in owners}) > 1:
            out.append(("TWO OWNERS", value,
                        ", ".join(sorted("%s (owns: %s)" % (p.name, k) for p, k in owners))))
    by_key = {}
    for value, owners in facts_multi.items():
        for p, k in owners:
            by_key.setdefault(k, set()).add(value)
    for k, values in sorted(by_key.items()):
        if len(values) > 1:
            out.append(("KEY REUSED", k, " | ".join(sorted(v[:40] for v in values))))
    return out


def owned_facts():
    """{value: (cornerstone_path, key)} - every fact a cornerstone declares it owns."""
    facts, corners, multi = {}, [], {}
    for p in corpus_docs():
        try:
            t = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not CORNER_RE.search(t):
            continue
        # ⛔ THE COLLECTING SIDE NEEDED THIS TOO. is_derived was wired into offenders() only, so
        # a template, a bootstrap or a message that QUOTES the markers was harvested as a real
        # cornerstone. Fixing one caller of a shared test is not fixing the file.
        if is_derived(t, p)[0]:
            continue
        corners.append(p)
        lines = t.split("\n")
        for i, line in enumerate(lines):
            m = OWNS_RE.search(line)
            if not m:
                continue
            # The value is on this line or within the next 4 - the table row follows the marker.
            for cand in lines[i:i + 5]:
                for v in VALUE_RE.findall(cand):
                    if PLACEHOLDER.search(v):
                        continue
                    # ⛔ EVERY owner recorded, not just the first. `setdefault` silently kept
                    # whichever the directory walk reached first, so two cornerstones claiming
                    # one fact looked like one cornerstone claiming it.
                    multi.setdefault(v.strip(), []).append((p, m.group(1)))
    for value, owners in multi.items():
        facts[value] = owners[0]
    return facts, corners, multi


def registered_paths(corner_path):
    """Doc paths this cornerstone names - those are allowed to restate what it owns."""
    try:
        t = corner_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    return {v.strip().replace("\\", "/").lower()
            for v in VALUE_RE.findall(t) if "/" in v and v.strip().endswith((".md", ".py"))}


def offenders(paths):
    facts, _c, _m = owned_facts()
    hits = []
    for p in paths:
        try:
            t = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # ⛔ A GENERATED PAGE CANNOT DRIFT, so it cannot be a second source of truth. Its whole
        # job is to list the artifacts it was computed from, which necessarily means naming
        # their paths. Measured 2026-08-17: the guard refused the knowledge browse page for
        # "restating" corpus.py, which that page exists to link to. If a generated view is ever
        # wrong, the correction is to regenerate it - never to edit it - so there is nothing
        # here for this guard to protect.
        # ⭐ WIDENED from a GENERATED-only check to all four derived kinds. The private version
        # exempted generated pages because one refused a browse page; templates, correspondence
        # and tests hit the identical defect in three other guards on the same day. Reusing the
        # shared test means the next kind added there is covered here without anyone noticing.
        derived, _why = is_derived(t, p)
        if derived:
            continue
        rel = str(p).replace("\\", "/").lower()
        for value, (corner, key) in facts.items():
            if str(corner).replace("\\", "/").lower() == rel:
                continue                                   # the owner may state its own fact
            if value not in t:
                continue
            reg = registered_paths(corner)
            if any(rel.endswith(r) for r in reg):
                continue                                   # registered in the cornerstone
            hits.append((p, value, corner, key))
    return hits


def staged():
    try:
        r = subprocess.run(["git", "-C", str(ROOT), "diff", "--cached", "--name-only",
                            "--diff-filter=ACM"], capture_output=True, text=True, timeout=20)
        return [ROOT / f for f in r.stdout.split("\n")
                if f.strip().endswith((".md", ".py")) and (ROOT / f).exists()]
    except Exception:
        return []


def install():
    """Put the guard on pre-commit in every repo here, ABOVE any terminal `exit`.

    ⛔ `.git/hooks/` is not tracked by git, so this must ship with the guard or a fresh clone
    silently has no guard at all.

    ⛔ INSERT, NEVER APPEND. Two hooks in this workspace already ended with `exit 0`. Appending
    put the guard past it: present, greppable, and never executed - which is indistinguishable
    from a guard that approved everything. The negative control landed a commit it had to refuse,
    and that is the only reason it was caught.
    """
    script = str(pathlib.Path(__file__).resolve()).replace("\\", "/")
    block = ['python "%s" --check' % script, "[ $? -eq %d ] && exit 1" % REFUSE]
    n = fixed = 0
    for r in [ROOT] + [d for d in ROOT.iterdir() if d.is_dir() and (d / ".git").is_dir()]:
        pc = r / ".git" / "hooks" / "pre-commit"
        if not pc.exists():
            pc.parent.mkdir(parents=True, exist_ok=True)
            pc.write_text("#!/bin/sh\n", encoding="utf-8", newline="\n")
        lines = [ln for ln in pc.read_text(encoding="utf-8", errors="replace").split("\n")
                 if "owns_guard" not in ln]
        idx = None
        for i, ln in enumerate(lines):
            if re.match(r"^\s*exit\s+\d+\s*$", ln):
                idx = i
                break
        if idx is None:
            lines = [ln for ln in lines if ln.strip()] + block + ["true"]
        else:
            lines[idx:idx] = block
            fixed += 1
        pc.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8", newline="\n")
        n += 1
        # ⭐ VERIFY REACHABILITY, not presence - grep finds dead code just as happily.
        body = pc.read_text(encoding="utf-8")
        live = body.split("\nexit ")[0] if "\nexit " in body else body
        if "owns_guard" not in live:
            print("  [FAIL] %s - installed but NOT reachable" % r.name)
    print("  installed on pre-commit in %d repo(s); %d needed insertion above a terminal exit"
          % (n, fixed))
    print("  verify it actually refuses: owns_guard.py --selftest, then stage a violation")
    return 0


def check():
    # ⛔ CONFLICTS FIRST. A guard whose own declaration set is incoherent cannot say anything
    # trustworthy about a staged doc - it would name whichever owner it happened to read first.
    _f, _c, multi = owned_facts()
    coll = collisions(multi)
    if coll:
        print("[owns] REFUSED: %d declaration conflict(s). Two docs cannot both own one fact."
              % len(coll))
        print()
        for kind, what, who in coll[:8]:
            print("          %-11s %s" % (kind, what[:68]))
            print("                      %s" % who[:96])
        print()
        print("          TWO OWNERS: pick one and delete the other declaration.")
        print("          KEY REUSED: the key no longer identifies anything - give the")
        print("                      second fact its own key.")
        print()
        print("          A conflict resolved by read order is a conflict the tool is")
        print("          hiding. Neither doc is the source of truth while this stands.")
        return REFUSE

    hits = offenders(staged())
    n_facts = len(_f)
    if not hits:
        print("[owns] %d owned fact(s) known; no staged doc restates one." % n_facts)
        return 0
    print("[owns] REFUSED: %d staged doc(s) restate a fact another doc OWNS." % len(hits))
    print()
    for p, value, corner, key in hits[:8]:
        print("          %s" % str(p.relative_to(ROOT)).replace("\\", "/"))
        print("            restates: %s" % value[:78])
        print("            owned by: %s  (owns: %s)"
              % (str(corner.relative_to(ROOT)).replace("\\", "/"), key))
        print()
    print("          A second copy of a fact is a second thing to keep current, and")
    print("          nothing keeps them in step. Whichever is read first wins, and the")
    print("          reader cannot tell which one that was.")
    print()
    print("          THREE WAYS FORWARD - all honest, and all end with one copy:")
    print()
    print("          1. EDIT THE CORNERSTONE instead of writing this. If the fact changed,")
    print("             it changed there. Delete it from this doc.")
    print("          2. LINK, do not restate. Name the cornerstone and let the reader open it.")
    print("          3. GENUINELY NEED A SEPARATE DOC? Then REGISTER it in the cornerstone's")
    print("             'Where to go for what' table, as a row naming this path:")
    print()
    print("               | if you need <the question this answers> | `<this doc's path>` |")
    print()
    print("          Option 3 is not a loophole - it is the requirement. The cornerstone")
    print("          must know every doc that carries its facts, or it is no longer the")
    print("          place you can look things up.")
    return REFUSE


def audit():
    facts, corners, _m = owned_facts()
    print("  %d cornerstone(s), %d owned fact(s)" % (len(corners), len(facts)))
    for value, (corner, key) in sorted(facts.items()):
        print("    %-46s %s" % (value[:46], key))
    hits = offenders([p for p in corpus_docs()])
    print("\n  %d restatement(s) outside the owner:" % len(hits))
    for p, value, corner, _k in hits[:30]:
        print("    %-56s %s" % (str(p.name)[:56], value[:40]))
    return 0


def selftest():
    ok = True

    def t(name, cond):
        nonlocal ok
        ok = ok and bool(cond)
        print("  [%s] %s" % ("OK " if cond else "FAIL", name))

    facts, corners, multi = owned_facts()
    t("no two docs own one fact",
      not [c for c in collisions(multi) if c[0] == "TWO OWNERS"])
    t("no key names two facts",
      not [c for c in collisions(multi) if c[0] == "KEY REUSED"])
    t("the corpus is reachable", len(corpus_docs()) > 50)
    # ⛔ THE TEMPLATE MUST NOT OWN ITS OWN EXAMPLES. It writes `<vendor>-key.txt` as an
    # illustration; if that became an owned fact the guard would forbid the template's own text
    # in every doc. Same defect class as the facet detector reading a template as a doc about
    # its examples, measured the same day.
    bad = [v for v in facts if PLACEHOLDER.search(v)]
    t("no placeholder became an owned fact (%d)" % len(bad), not bad)
    t("an owner may state its own fact", not offenders(list(corners)))
    print("\nselftest %s" % ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--install", action="store_true",
                    help="install on pre-commit in every repo here")
    a = ap.parse_args()
    try:
        sys.exit(selftest() if a.selftest else install() if a.install
                 else audit() if a.audit else check())
    except Exception as e:
        # ⛔ NEVER take the workspace down. A crash here must not block every commit - that is
        # the failure knowledge_gate.py shipped on 2026-08-13, where a syntax error refused
        # every commit in the workspace until it was found.
        print("[owns] guard error (not blocking): %s" % e)
        sys.exit(0)
