#!/usr/bin/env python3
"""Emit a correct, self-describing local timestamp. Never type one by hand.

THE FOOTGUN (the human named it, 2026-08-06)
---------------------------------------
o9 wrote 22 attestation timestamps as `2026-08-07T02:30:00-07:00`. Today was the 6th. The
first diagnosis - "the times were invented" - was WRONG, and the correction matters because
it changes the fix:

    written : 2026-08-07T02:30:00-07:00      <- value from a UTC clock, LABELLED as PDT
    actual  : 2026-08-06T19:30:00-07:00      <- the same instant, correctly labelled

Converted as UTC, every one of those stamps lands inside the real session window. The
VALUES were right. The OFFSET LABEL was wrong, and because UTC is 7 hours ahead of PDT,
anything after 17:00 local crosses midnight UTC and flips the DATE too. At 22:34 PDT on
2026-08-06 it is already 05:34 on 2026-08-07 in UTC.

⭐ That is why the error was invisible: the timestamps were well-formed, precise, internally
consistent, and monotonically increasing. They satisfied E-AMBIGUOUSDATE, which asks for
exactly that precision. Nothing about a UTC instant wearing a PDT label looks wrong.

THE FIX IS NOT CARE. IT IS NOT TYPING THEM.
-------------------------------------------
A rule saying "check your timezone" is the class of fix this whole workstream rejects. The
mechanism is that the timestamp comes from the system clock, with the offset the system
reports, through one function - so the value and its label can never disagree, because
nothing ever chooses them separately.

    python orchdoc_stamp.py            -> 2026-08-06T22:41:03-07:00
    python orchdoc_stamp.py --human    -> 06-Aug-2026 22:41 PDT
    python orchdoc_stamp.py --utc      -> 2026-08-06T22:41:03-07:00 (2026-08-07T05:41:03Z)
    python orchdoc_stamp.py --check "2026-08-07T02:30:00-07:00"

`--check` is the diagnostic: it reports whether an offset label is possible for that date
in this zone, and whether the instant is in the future. Both are mechanical questions that
a reader of a doc cannot answer by looking.

ASCII output only (Windows console rule).
"""

import argparse
import datetime as dt
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def now_local():
    """The one source of a timestamp. Value and offset come from the SAME call.

    `astimezone()` with no argument attaches the system's real UTC offset for THIS
    instant - which is also why it is correct across a DST boundary without anyone
    remembering that one exists.
    """
    return dt.datetime.now().astimezone()


def iso(t=None):
    return (t or now_local()).isoformat(timespec="seconds")


def human(t=None):
    t = t or now_local()
    return t.strftime("%d-%b-%Y %H:%M %Z").strip()


def check(text):
    """Is this stamp self-consistent, and has it happened?

    Returns (ok, [problems]). Both questions are mechanical, and neither is answerable by
    reading the string - which is precisely why 22 bad ones survived review.
    """
    problems = []
    try:
        t = dt.datetime.fromisoformat(text)
    except ValueError:
        return False, ["not an ISO-8601 timestamp: %r" % text]

    if t.tzinfo is None:
        problems.append("no offset - a bare local time is ambiguous to every other reader")
        return False, problems

    now = now_local()
    if t > now:
        problems.append("IN THE FUTURE: %s is after now (%s)" % (iso(t), iso(now)))

    # Does the LABELLED offset match what this machine's zone actually uses on that date?
    # A mismatch is the UTC-mislabelled-as-local footgun, and it is the one that also
    # moves the date across midnight.
    naive = t.replace(tzinfo=None)
    real = naive.astimezone().utcoffset()
    if real is not None and t.utcoffset() != real:
        as_utc = t.replace(tzinfo=dt.timezone.utc).astimezone()
        problems.append(
            "offset %s is not what this zone uses on %s (it uses %s)"
            % (fmt_off(t.utcoffset()), naive.date().isoformat(), fmt_off(real)))
        problems.append(
            "  if the VALUE was really UTC, the correct local stamp is: %s" % iso(as_utc))
    return (not problems), problems


def fmt_off(td):
    if td is None:
        return "none"
    total = int(td.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    return "%s%02d:%02d" % (sign, total // 3600, (total % 3600) // 60)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--done", metavar="TEXT",
                    help="emit a finished sub-item line: the ORIGINAL text struck through, plus a generated timestamp. The text is echoed back verbatim so keeping it is easier than rewording it.")
    ap.add_argument("--human", action="store_true", help="dd-Mon-yyyy HH:MM ZONE")
    ap.add_argument("--utc", action="store_true", help="also show the UTC instant")
    ap.add_argument("--check", metavar="STAMP",
                    help="report whether a stamp is self-consistent and has happened")
    a = ap.parse_args()

    if a.done:
        # One line, ready to paste. The original text is echoed VERBATIM - the whole point of
        # the strikethrough is that the words survive being marked done, so the generator
        # makes keeping them the path of least effort.
        now = dt.datetime.now().astimezone()
        # A NUMERIC offset, not %Z. Windows expands %Z to "Pacific Daylight Time", which is
        # long, locale-dependent and ambiguous across machines; "UTC-7" is none of those and
        # is what makes the stamp checkable against a commit's own time later.
        off = now.utcoffset()
        hrs = int(off.total_seconds() // 3600) if off else 0
        print("- ~~%s~~ - DONE %s @ %s (UTC%+d)"
              % (a.done.strip(), now.strftime("%d-%b-%Y"), now.strftime("%H:%M"), hrs))
        return 0


    if a.check:
        ok, problems = check(a.check)
        print("checking: %s" % a.check)
        if ok:
            print("  [OK] well-formed, offset matches this zone, and it has happened")
            return 0
        for p in problems:
            print("  [BAD] %s" % p)
        return 1

    t = now_local()
    if a.human:
        print(human(t))
    elif a.utc:
        print("%s  (%sZ)" % (iso(t),
                             t.astimezone(dt.timezone.utc).isoformat(timespec="seconds")[:-6]))
    else:
        print(iso(t))
    return 0


if __name__ == "__main__":
    sys.exit(main())
