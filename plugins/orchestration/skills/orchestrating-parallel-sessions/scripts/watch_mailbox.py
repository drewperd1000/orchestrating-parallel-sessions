#!/usr/bin/env python3
"""Inference-free mailbox watcher for hands-off parallel-session orchestration.

Pairs with the orchestrating-parallel-sessions skill. Copy this next to a
`mailboxes/` directory; one append-only mailbox per lane, named for its id
(`laneN.md`, or `o<N>L<m>.md` when several orchestrator groups share the dir).
`--role` is that same id: `o<N>` for the orchestrator, `o<N>L<m>` for a lane.

watch: cheap file polling (default every 20s). Exits 0 printing "NEW MAIL ..."
  as soon as ANY watched mailbox has a message numbered above this role's ack
  that was authored by someone else. Exits "HEARTBEAT ..." after --heartbeat
  seconds so the waiting session re-arms. Launch via run_in_background so the
  session is re-invoked the moment this process exits.

ack: record progress. Default records the highest message number authored by
  THIS role (safe: never skips an unseen foreign message posted concurrently);
  pass --msg N to ack a specific message you processed but didn't reply to.

No third-party dependencies (Python stdlib only).
"""
import argparse
import re
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

MSG_RE = re.compile(r"^## MSG (\d+) FROM (\S+)", re.M)


def messages(path: Path):
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return []
    return [(int(n), frm) for n, frm in MSG_RE.findall(text)]


def ack_path(mailbox: Path, role: str) -> Path:
    d = mailbox.parent / ".ack"
    d.mkdir(exist_ok=True)
    return d / (role + "-" + mailbox.stem + ".txt")


def read_ack(mailbox: Path, role: str) -> int:
    try:
        return int(ack_path(mailbox, role).read_text().strip())
    except Exception:
        return 0


def unseen_foreign(mailbox: Path, role: str):
    ack = read_ack(mailbox, role)
    return [(n, frm) for n, frm in messages(mailbox) if n > ack and frm != role]


def cmd_watch(args):
    boxes = [Path(m) for m in args.mailbox]
    start = time.time()
    while True:
        for mb in boxes:
            fresh = unseen_foreign(mb, args.role)
            if fresh:
                n, frm = fresh[-1]
                print("NEW MAIL: {} msg {} from {} ({} unseen)".format(
                    mb.name, n, frm, len(fresh)))
                return 0
        if time.time() - start >= args.heartbeat:
            print("HEARTBEAT: no new mail in {}s; re-arm the watcher".format(
                args.heartbeat))
            return 0
        time.sleep(args.interval)


def cmd_ack(args):
    for m in args.mailbox:
        mb = Path(m)
        if args.msg is not None:
            target = args.msg
        else:
            own = [n for n, frm in messages(mb) if frm == args.role]
            target = max(own) if own else 0
        ack_path(mb, args.role).write_text(str(target))
        print("ACK {} -> {}".format(mb.name, target))
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("watch")
    w.add_argument("--role", required=True)
    w.add_argument("--mailbox", nargs="+", required=True)
    w.add_argument("--interval", type=int, default=20)
    w.add_argument("--heartbeat", type=int, default=2700)
    w.set_defaults(fn=cmd_watch)
    a = sub.add_parser("ack")
    a.add_argument("--role", required=True)
    a.add_argument("--mailbox", nargs="+", required=True)
    a.add_argument("--msg", type=int, default=None)
    a.set_defaults(fn=cmd_ack)
    args = p.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
