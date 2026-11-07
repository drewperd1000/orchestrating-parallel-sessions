"""Did a sub-item get REWORDED in the same commit that struck it through?

<!-- subject: orchestration -->

A record needs a required format, and anyone can falsify a format by following it. A format
cannot solve that, because a format is satisfied by anything of the right shape. Three layers
do different jobs, and only the third resists gaming:

  1. FORMAT       a slot must exist.
  2. ORACLE       what goes in the slot must be checkable by someone else: a sha, a path, a command.
  3. IMMUTABILITY the text inside `~~ ~~` must be the same text that was there before it was
                  struck, which git can check, because it holds the previous version.
"""
import argparse
import datetime
import difflib
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

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import orchdoc as od  # noqa: E402

STRUCK_RE = re.compile(r"~~(.+?)~~|^\s*[-*+]\s*\[[xX]\]\s+(.+?)\s*$")


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
    return sum(b.size for b in sm.get_matching_blocks() if b.size >= 4) / float(len(needle))


def commits_for(doc, since):
    rng = ["%s..%s" % (since, od.CANONICAL_REF)] if since else ["-40"]
    r = subprocess.run(["git", "log", "--format=%H", "--no-merges"] + rng + ["--", str(doc)],
                       capture_output=True, text=True, cwd=str(od.PROJECTS), creationflags=flags())
    return [c for c in r.stdout.split() if c]


def diff_lines(sha, doc):
    r = subprocess.run(["git", "show", "--format=", "--unified=0", sha, "--", str(doc)],
                       capture_output=True, text=True, cwd=str(od.PROJECTS), creationflags=flags())
    added, removed = [], []
    for l in r.stdout.split("\n"):
        if l.startswith("+") and not l.startswith("+++"):
            added.append(l[1:])
        elif l.startswith("-") and not l.startswith("---"):
            removed.append(l[1:])
    return added, removed



STAMP_RE = re.compile(
    r"DONE\s+(\d{2})-([A-Z][a-z]{2})-(\d{4})\s*@?\s*(\d{2}):(\d{2})")
_MON = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}


def commit_time(sha):
    """When git says this commit was authored. The oracle - independent of what the line claims."""
    r = subprocess.run(["git", "show", "-s", "--format=%at", sha],
                       capture_output=True, text=True, cwd=str(od.PROJECTS), creationflags=flags())
    try:
        return datetime.datetime.fromtimestamp(int(r.stdout.strip()))
    except Exception:
        return None


def check_stamps(doc, since):
    """Every DONE stamp added in history, against the commit that added it.

    Reports (sha, stamp_text, hours_apart) for anything outside the window. A stamp is only
    worth requiring if something checks it; otherwise it is a field, and a field is satisfiable
    with any string of the right shape.
    """
    out, seen = [], 0
    for sha in commits_for(doc, since):
        ct = commit_time(sha)
        if not ct:
            continue
        added, _ = diff_lines(sha, doc)
        for line in added:
            m = STAMP_RE.search(line)
            if not m:
                continue
            seen += 1
            d, mon, y, hh, mm = m.groups()
            if mon not in _MON:
                continue
            try:
                st = datetime.datetime(int(y), _MON[mon], int(d), int(hh), int(mm))
            except ValueError:
                out.append((sha, m.group(0), None))
                continue
            hours = abs((st - ct).total_seconds()) / 3600.0
            # 36h. A legitimate stamp CAN be hours from its commit - work finishes, the entry
            # gets written, other sections get swept, the commit lands later. Narrowing this
            # would flag honest work, and a check that is mostly wrong teaches the override
            # reflex. What is left has no innocent reading: a stamp DAYS from its commit was
            # typed, not generated.
            if hours > 36:
                out.append((sha, m.group(0), hours))
    return out, seen


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--doc", required=True)
    ap.add_argument("--since", help="baseline ref; default is the last 40 commits")
    ap.add_argument("--stamps", action="store_true",
                    help="also check every DONE stamp against the commit that recorded it - the stamp is only worth requiring if something verifies it")
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
            # group(1) is the ~~legacy~~ form, group(2) the checked-checkbox form.
            inner = norm(m.group(1) or m.group(2) or "")
            if not inner:
                continue
            # An unchanged strike leaves the inner text intact SOMEWHERE in the old line.
            if any(inner and inner in norm(r) for r in cand):
                continue
            best, best_frac = None, 0.0
            for r in cand:
                f = contained_fraction(inner, norm(r))
                if f > best_frac:
                    best, best_frac = r, f
            if best is None or best_frac < 0.75:
                continue                  # a genuinely NEW struck item, not a reword
            ratio = best_frac
            hits += 1
            print("  %s  similarity %.0f%%" % (sha[:10], ratio * 100))
            print("    was    : %s" % best.strip()[:96])
            print("    struck : %s" % m.group(1).strip()[:96])
            print()

    if a.stamps:
        bad, seen = check_stamps(doc, a.since)
        if bad:
            print("  DONE stamps that disagree with the commit that recorded them:")
            for sha, txt, hrs in bad:
                when = "unparseable date" if hrs is None else "%.0f hours apart" % hrs
                print("    %s  %-42s %s" % (sha[:10], txt[:42], when))
            print()
            print("    git recorded when the line actually landed, independently of what the")
            print("    line says about itself. A stamp days from its commit was TYPED, not")
            print("    generated - use `orchdoc_stamp.py --done \"<text>\"`, which cannot be")
            print("    wrong about the time because it does not ask anyone.")
            print()
            hits += len(bad)
        elif seen:
            print("  %d DONE stamp(s) examined; every one is consistent with its commit." % seen)
            print()
        else:
            print("  NO DONE stamps found in this range - 0 examined, so this proves NOTHING")
            print("  about the document. The `- [x] text - DONE dd-Mon-yyyy @ HH:MM` form is")
            print("  new; until sub-items carry it there is nothing here to verify.")
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
