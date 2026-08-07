"""Did a sub-item get REWORDED in the same commit that struck it through?

THE HUMAN'S CONUNDRUM, 2026-08-07:

    "There needs to be a requirement of a specific output onto the record so that it is
     recorded, but recorded with an enforced format - BUT it must be done in a way that it
     cannot falsify it by just following the format. It seems like a conundrum."

It is a real conundrum and a format cannot solve it, because a format is satisfied by anything
of the right shape. Three layers do different jobs, and only the third resists gaming:

  1. FORMAT     - a slot must exist. Cheap, structural, and by itself gameable.
  2. ORACLE     - what goes in the slot must be checkable BY SOMEONE ELSE: a sha, a path, a
                  command. Not prose. `E-RUBBERSTAMP` already enforces this for findings.
  3. IMMUTABILITY - ⭐ the text inside `~~ ~~` must be the SAME text that was there before it
                  was struck. This is what makes following-the-format insufficient, and it is
                  fully mechanical, because git holds the previous version.

THE FAILURE IT CATCHES, from a real incident an hour before this was written. o7 had:

    - **Copy is UNCHANGED pending your call** (o1 asked me to hold, and I agree - ...)

`E-CLOSEDWITHOPENSUBS` fired on the word *pending*, and at the time striking the line did not
clear it. So they reworded the historical record to get past a regex:

    - ~~**Copy is UNCHANGED, held for your call.**~~ **RESOLVED - ...**

They disclosed it and called it gaming, which is why it is a usable fixture. **The check would
have caught it either way** - the line gained `~~` and its inner text changed in the same
commit, which is the whole signature.

⭐ WHY THIS ONE IS TRUSTWORTHY WHERE TODAY'S OTHERS WERE NOT. Three checks shipped on
2026-08-07 and were wrong on the fleet, and every one of them read PROSE to infer INTENT. This
reads a DIFF: two versions of one line, and whether the words between the tildes survived. It
needs no theory about what the author meant. That is the same property that makes "29 lines, 0
entries" reliable while "does this bullet look unfinished?" is not.

  restrike --doc o7 [--since <ref>]   walk history, report strikes that also reworded

⚠️ ADVISORY BY DESIGN, and it must stay that way. Rewording while striking is sometimes exactly
right - a line that says "pending your call" IS false once the call is made, and rewriting it to
"you ruled on the 7th" makes the record MORE accurate, not less. **The two are identical in a
diff.** What separates them is whether the change was made to be true or made to pass, and
nothing mechanical can see that. So this reports the edit and asks; it never blocks. The
independent auditor is where the judgement belongs.
"""
import argparse
import difflib
import pathlib
import re
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import orchdoc as od  # noqa: E402

STRUCK_RE = re.compile(r"~~(.+?)~~")


def norm(s):
    """Compare MEANING, not markup. Bold, punctuation and spacing are not the record."""
    s = re.sub(r"[*_`~]+", "", s)
    s = re.sub(r"[^\w\s]+", " ", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def contained_fraction(needle, hay):
    """How much of `needle` appears in `hay`, as a fraction of needle's length.

    Asymmetric on purpose. A struck phrase is short and the line it was lifted from is long,
    so a symmetric similarity score punishes the very case this is looking for - it scored the
    corpus's one real reword below threshold while matching an unrelated numeric table row.
    """
    if not needle:
        return 0.0
    sm = difflib.SequenceMatcher(None, needle, hay)
    # Only runs of >=4 characters count. Scattered single-character matches - a shared space,
    # an 'e', a digit - are noise, and for a short needle there are enough of them to push an
    # unrelated line to 89%. Measured: two lines with nothing in common but a date scored 89%
    # by accumulating fragments. A run of four characters is a word, not a coincidence.
    return sum(b.size for b in sm.get_matching_blocks() if b.size >= 4) / float(len(needle))


def commits_for(doc, since):
    rng = ["%s..%s" % (since, od.CANONICAL_REF)] if since else ["-40"]
    r = subprocess.run(["git", "log", "--format=%H", "--no-merges"] + rng + ["--", str(doc)],
                       capture_output=True, text=True, cwd=str(od.PROJECTS))
    return [c for c in r.stdout.split() if c]


def diff_lines(sha, doc):
    r = subprocess.run(["git", "show", "--format=", "--unified=0", sha, "--", str(doc)],
                       capture_output=True, text=True, cwd=str(od.PROJECTS))
    added, removed = [], []
    for l in r.stdout.split("\n"):
        if l.startswith("+") and not l.startswith("+++"):
            added.append(l[1:])
        elif l.startswith("-") and not l.startswith("---"):
            removed.append(l[1:])
    return added, removed


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--doc", required=True)
    ap.add_argument("--since", help="baseline ref; default is the last 40 commits")
    a = ap.parse_args()

    doc = od.resolve_doc_arg(a.doc)
    if not doc or not doc.exists():
        print("no such doc: %s" % a.doc, file=sys.stderr)
        return 2
    rel = doc.name

    print("orchdoc restrike - %s" % rel)
    print("  Looking for sub-items whose text CHANGED in the same commit that struck them.")
    print()

    hits = 0
    for sha in commits_for(doc, a.since):
        added, removed = diff_lines(sha, doc)
        # Only removed lines that were NOT already struck can be the pre-strike version.
        cand = [r for r in removed if "~~" not in r and r.strip()]
        if not cand:
            continue
        for line in added:
            m = STRUCK_RE.search(line)
            if not m:
                continue
            inner = norm(m.group(1))
            if not inner:
                continue
            # An unchanged strike leaves the inner text intact SOMEWHERE in the old line.
            if any(inner and inner in norm(r) for r in cand):
                continue
            # CONTAINMENT, not similarity. The first version used SequenceMatcher.ratio(),
            # which is symmetric and length-penalised - so a short struck phrase compared
            # against the long line it came from scored BELOW threshold, and the one real
            # specimen in the corpus was missed while a numeric table row matched at 55%.
            # The question is not "are these two lines alike" but "how much of the STRUCK
            # TEXT came from this old line", which is asymmetric by nature.
            best, best_frac = None, 0.0
            for r in cand:
                f = contained_fraction(inner, norm(r))
                if f > best_frac:
                    best, best_frac = r, f
            # 0.75, set by reading every hit on five real documents rather than by tuning
            # against one. Below three-quarters containment the old line is not plausibly the
            # source of the struck text at all: measured, everything under it was two
            # unrelated lines sharing a date or a stock phrase.
            if best is None or best_frac < 0.75:
                continue                  # a genuinely NEW struck item, not a reword
            ratio = best_frac
            hits += 1
            print("  %s  similarity %.0f%%" % (sha[:10], ratio * 100))
            print("    was    : %s" % best.strip()[:96])
            print("    struck : %s" % m.group(1).strip()[:96])
            print()

    if not hits:
        print("  No strike-with-reword found. Every struck sub-item kept its words.")
        return 0
    print("  %d strike(s) also changed the wording." % hits)
    print()
    print("  ⚠ THIS IS A QUESTION, NOT A VERDICT. Rewording while striking is sometimes")
    print("    exactly right: a line reading 'pending your call' IS false once the call is")
    print("    made, and rewriting it to say what happened makes the record MORE accurate.")
    print("    A reword made to be TRUE and one made to PASS look identical in a diff.")
    print("    For each one above, answer: did the words change because the fact changed,")
    print("    or because a check was in the way?")
    return 1


if __name__ == "__main__":
    sys.exit(main())
