"""Ratify a 30/60/90 vision, then notice - mechanically - when it is not happening.

WHY THIS EXISTS. A plan written for a first-time user ended with:

    ⚠️ If at 30 days you have three impressive prototypes and no working system, stop and go
    back. That is the specific way this goes wrong for capable people, and it is recoverable -
    but only if you notice.

⛔ "BUT ONLY IF YOU NOTICE" IS A PARAGRAPH ASKING SOMEONE TO BE CAREFUL, WHICH IS NOT A FIX.
The person most likely to be three prototypes deep is the person having the most fun, and
nobody in that state stops to audit themselves. **The orchestrator is awake and can count.**

WHAT IT DOES, and the split matters:

  RATIFY  - the human states what 30, 60 and 90 days look like, once, and it is dated. Not
            aspirations: **outcomes another person could verify.** A horizon nobody could
            falsify cannot be missed, so it cannot be learned from.
  WATCH   - `checkin` runs cheaply and prints NOTHING almost every time. It speaks when a
            horizon has come due, or when the drift signal trips.
  DRIFT   - the specific pattern above, counted rather than felt: several things started,
            none finished, past the first horizon.

⭐ SILENCE IS THE DEFAULT AND IT IS LOAD-BEARING. A check-in tool that greets you daily gets
muted in a week, and a muted tool protects nothing. This one is quiet until it has something
that has actually earned an interruption.

⭐ AND IT IS OPT-IN BY CONSTRUCTION, not by a flag: with no horizons ratified, every command is
a no-op. Nobody who has not asked for this will ever hear from it.

  horizon set 30 "<what done looks like>"     draft a horizon
  horizon ratify                              lock the set, start the clock
  horizon status                              where you are, always prints
  horizon checkin                             the nudge. SILENT unless something is due
  horizon met 30 / missed 30 --because "..."  record the outcome
"""
import argparse
import json
import os
import pathlib
import re
import sys
from datetime import date, datetime, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
STATE = ROOT / ".orchdoc-horizon.json"
DAYS = (30, 60, 90)


def load():
    try:
        return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    except (OSError, ValueError):
        return {}


def save(d):
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, STATE)


def today():
    return date.today()


def started_on(d):
    s = d.get("ratified")
    return datetime.fromisoformat(s).date() if s else None


def elapsed(d):
    s = started_on(d)
    return (today() - s).days if s else None


# ------------------------------------------------------------------------------ drift signal
#
# The pattern the plan warned about, counted instead of felt: a pile of things begun and
# nothing finished. Read from whatever OrchDocs exist, because that is where work is recorded -
# and if none exist yet, that is its own answer.

def _work_counts():
    """(open_items, closed_items, docs_seen). Best-effort; absence is reported, not guessed."""
    try:
        sys.path.insert(0, str(HERE))
        import orchdoc as od
    except Exception:
        return None, None, 0
    opened = closed = docs = 0
    for p in sorted(ROOT.glob("ORCHESTRATOR-DECISIONS-*.md")):
        try:
            lines = p.read_text(encoding="utf-8").split("\n")
        except OSError:
            continue
        docs += 1
        entries, _ = od.parse_entries(lines)
        for e in entries:
            st = od.status_of(e["body"])
            if st in od.TERMINAL_STATUS:
                closed += 1
            elif st in getattr(od, "LIVE_STATUS", od.PLATE_STATUS):
                opened += 1
    return opened, closed, docs


def drift(d):
    """The 'three prototypes, no working system' signal. Returns a message, or None.

    ⭐ Deliberately narrow. It fires only when BOTH halves are true - several things in flight
    AND nothing finished - and only after the first horizon has come due. Either half alone is
    ordinary: work in progress is not drift, and a slow start is not drift. **The pattern is
    the combination, sustained past the point where something should have landed.**
    """
    el = elapsed(d)
    if el is None or el < DAYS[0]:
        return None
    opened, closed, docs = _work_counts()
    if opened is None or docs == 0:
        return None                      # nothing to read; not evidence of anything
    if opened >= 3 and closed == 0:
        return ("%d items are in flight and NONE are finished, %d days in.\n"
                "     This is the specific pattern that catches capable people: several\n"
                "     impressive things started, no working system. It is recoverable, and\n"
                "     the recovery is to finish ONE of them before starting anything else."
                % (opened, el))
    return None


def due_horizons(d):
    """Horizons whose date has passed and whose outcome was never recorded."""
    s = started_on(d)
    if not s:
        return []
    out = []
    for h in d.get("horizons", []):
        if h.get("outcome"):
            continue
        if today() >= s + timedelta(days=h["day"]):
            out.append(h)
    return out


# ----------------------------------------------------------------------------------- commands

def cmd_set(args):
    d = load()
    if d.get("ratified"):
        print("already ratified on %s - use `revise` semantics deliberately:" % d["ratified"][:10])
        print("  a horizon changed after the fact cannot be missed, and a target that")
        print("  cannot be missed teaches nothing. Record the outcome instead.")
        return 1
    if args.day not in DAYS:
        print("day must be one of %s" % ", ".join(str(x) for x in DAYS), file=sys.stderr)
        return 2
    hs = {h["day"]: h for h in d.get("horizons", [])}
    hs[args.day] = {"day": args.day, "goal": args.goal.strip()}
    d["horizons"] = [hs[k] for k in sorted(hs)]
    save(d)
    print("  day %d: %s" % (args.day, args.goal.strip()))
    missing = [x for x in DAYS if x not in hs]
    print("  still to set: %s" % (", ".join(str(x) for x in missing) if missing
                                  else "nothing - run `ratify` to start the clock"))
    return 0


def cmd_ratify(args):
    d = load()
    if d.get("ratified"):
        print("already ratified on %s" % d["ratified"][:10])
        return 0
    hs = {h["day"]: h for h in d.get("horizons", [])}
    missing = [x for x in DAYS if x not in hs]
    if missing:
        print("cannot ratify: day %s not set." % ", ".join(str(x) for x in missing),
              file=sys.stderr)
        print("  A partial horizon is worse than none - the unset ones become the excuse",
              file=sys.stderr)
        print("  for whatever happened.", file=sys.stderr)
        return 1
    thin = [h for h in hs.values() if len(h["goal"]) < 25]
    if thin and not args.force:
        print("these read as aspirations rather than outcomes:")
        for h in thin:
            print("    day %d: %r" % (h["day"], h["goal"]))
        print()
        print("  ⭐ A horizon has to be something ANOTHER PERSON could verify. 'Make progress'")
        print("     cannot be missed, and a target that cannot be missed teaches nothing when")
        print("     the date arrives. Rewrite them, or --force if they really are that clear.")
        return 1
    d["ratified"] = datetime.now().astimezone().isoformat(timespec="seconds")
    save(d)
    s = started_on(d)
    print("  ratified %s" % s.isoformat())
    for h in d["horizons"]:
        print("    day %-3d  %s   %s" % (h["day"], (s + timedelta(days=h["day"])).isoformat(),
                                         h["goal"][:56]))
    print()
    print("  From here `checkin` stays silent until one of those dates arrives, or until")
    print("  work starts piling up unfinished. Nothing else will interrupt you.")
    return 0


def cmd_status(args):
    d = load()
    if not d.get("horizons"):
        print("no horizons set. This tool does nothing until you set some:")
        print("    horizon set 30 \"<what done looks like at 30 days>\"")
        return 0
    if not d.get("ratified"):
        print("drafted, not ratified - the clock has not started:")
        for h in d["horizons"]:
            print("    day %-3d %s" % (h["day"], h["goal"][:64]))
        return 0
    s, el = started_on(d), elapsed(d)
    print("  started %s - day %d" % (s.isoformat(), el))
    print()
    for h in d["horizons"]:
        due = s + timedelta(days=h["day"])
        oc = h.get("outcome")
        if oc:
            mark = "MET" if oc == "met" else "MISSED"
        elif today() >= due:
            mark = "DUE - unrecorded"
        else:
            mark = "in %d days" % (due - today()).days
        print("  day %-3d %-11s %-16s %s" % (h["day"], due.isoformat(), mark, h["goal"][:48]))
        if h.get("because"):
            print("          because: %s" % h["because"][:70])
    opened, closed, docs = _work_counts()
    if docs:
        print()
        print("  work visible across %d doc(s): %d in flight, %d finished" % (docs, opened, closed))
    return 0


def cmd_checkin(args):
    """The nudge. Prints NOTHING unless something has earned an interruption."""
    d = load()
    if not d.get("ratified"):
        return 0                          # opt-in by construction
    said = False
    for h in due_horizons(d):
        if not said:
            print("[horizon] check-in")
            said = True
        print("  ⏰ DAY %d CAME DUE and the outcome was never recorded:" % h["day"])
        print("     \"%s\"" % h["goal"])
        print("     Did it happen?  horizon met %d   |   horizon missed %d --because \"...\""
              % (h["day"], h["day"]))
        print()
    msg = drift(d)
    if msg:
        if not said:
            print("[horizon] check-in")
            said = True
        print("  ⚠️ %s" % msg)
        print()
    if said:
        print("  (This is the only thing that will interrupt you. `horizon status` any time.)")
    return 0


def _outcome(args, kind):
    d = load()
    if not d.get("ratified"):
        print("nothing ratified", file=sys.stderr)
        return 1
    for h in d.get("horizons", []):
        if h["day"] == args.day:
            h["outcome"] = kind
            h["recorded"] = today().isoformat()
            if getattr(args, "because", None):
                h["because"] = args.because
            save(d)
            print("  day %d recorded as %s" % (args.day, kind.upper()))
            if kind == "missed":
                print("  ⭐ A missed horizon is the useful kind. It is the only evidence that")
                print("     the plan and the reality disagreed, and it is worth more than a")
                print("     met one - which usually just means the target was safe.")
            return 0
    print("no horizon for day %d" % args.day, file=sys.stderr)
    return 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    st = sub.add_parser("set", help="draft one horizon")
    st.add_argument("day", type=int)
    st.add_argument("goal")
    st.set_defaults(func=cmd_set)
    rt = sub.add_parser("ratify", help="lock the set and start the clock")
    rt.add_argument("--force", action="store_true",
                    help="accept goals that read as aspirations")
    rt.set_defaults(func=cmd_ratify)
    sub.add_parser("status", help="where you are").set_defaults(func=cmd_status)
    sub.add_parser("checkin", help="silent unless something is due").set_defaults(func=cmd_checkin)
    m = sub.add_parser("met")
    m.add_argument("day", type=int)
    m.set_defaults(func=lambda a: _outcome(a, "met"))
    x = sub.add_parser("missed")
    x.add_argument("day", type=int)
    x.add_argument("--because", help="what actually happened instead")
    x.set_defaults(func=lambda a: _outcome(a, "missed"))
    a = ap.parse_args()
    return a.func(a) if a.cmd else cmd_status(a)


if __name__ == "__main__":
    sys.exit(main())
