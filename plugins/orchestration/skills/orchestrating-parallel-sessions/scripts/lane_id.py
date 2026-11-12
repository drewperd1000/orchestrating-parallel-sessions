#!/usr/bin/env python3
"""Allocate a unique id for a sublane, so the marker names the SESSION and not just the tree.

<!-- subject: orchestration -->

<!-- route-tags: lane id sublane allocate ORCHDOC_LANE child marker unique orchestrator -->
"""
import errno
import io
import os
import pathlib
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

STATE = pathlib.Path(os.environ.get(
    "ORCHDOC_LANE_STATE", str(pathlib.Path.home() / ".claude" / "state" / "lane-ids")))

LANE_RE = re.compile(r"^o\d+L\d+(?:\.\d+)*$", re.I)
MAX_SIBLINGS = 5000


def allocate(parent):
    """Return the next free child id of `parent`, claiming it atomically."""
    STATE.mkdir(parents=True, exist_ok=True)
    for n in range(1, MAX_SIBLINGS + 1):
        child = "%s.%d" % (parent, n)
        claim = STATE / (child + ".claim")
        try:
            fd = os.open(str(claim), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except OSError as exc:
            if exc.errno == errno.EEXIST:
                continue          # somebody else holds this number - take the next
            raise
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("pid=%d ppid=%d\n" % (os.getpid(), os.getppid()))
        return child
    raise SystemExit("lane_id: %s already has %d children" % (parent, MAX_SIBLINGS))


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "next"
    if cmd not in ("next", "current"):
        print("usage: lane_id.py [next|current]", file=sys.stderr)
        return 2

    parent = (os.environ.get("ORCHDOC_LANE") or "").strip()
    if cmd == "current":
        print(parent or "")
        return 0

    if not parent:
        print("lane_id: no ORCHDOC_LANE in the environment, so there is no parent to extend.\n"
              "    A TOP-LEVEL lane id is not allocated here. The orchestrator picks it: o<N>L<m>, with\n"
              "    <N> its own number and <m> the next number it has not used.", file=sys.stderr)
        return 2
    if not LANE_RE.match(parent):
        print("lane_id: ORCHDOC_LANE=%r is not a lane id (expected o<N>L<m> or o<N>L<m>.<k>...)"
              % parent, file=sys.stderr)
        return 2

    print(allocate(parent))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
