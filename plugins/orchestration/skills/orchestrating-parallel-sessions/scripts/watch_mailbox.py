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
    # ⛔ REFUSE BY DEFAULT - a perpetual watcher is a thing the human does not want running.
    #
    # the human, 2026-09-16: "What I don't want are perpetual watchers firing for every session all
    # the time. That creates its own problems that I don't want." And on the channel itself:
    # "I had to vocally tell everyone to stop using the mailboxes... It was ONLY there while
    # the send_message tool was broken by Anthropic."
    #
    # ⭐ THE ASYMMETRY THIS CLOSES. `orch_msg.py send` - the WRITE half - has refused since
    # 2026-09-08. `watch` did not, so the decommissioned channel could still be armed by any
    # session that read the docstring and used it exactly as documented. That is the shape that
    # misled sessions before: the mechanism is explained here, and the ruling against it lives
    # somewhere else.
    #
    # ⭐ A REFUSAL, NOT A WARNING. A warning prints and the watcher starts anyway.
    if not getattr(args, "send_message_is_broken", None):
        print("  REFUSED: do not arm a mailbox watcher.\n")
        print("    the human, 2026-09-16: \"What I don't want are perpetual watchers firing for")
        print("    every session all the time. That creates its own problems.\"\n")
        print("    THE MAILBOX WAS A STOPGAP. It existed only while")
        print("    mcp__ccd_session_mgmt__send_message was broken by Anthropic in August. That")
        print("    tool works now, and it is the road.\n")
        print("    TO REACH A LIVE SESSION")
        print("      1. mcp__ccd_session_mgmt__list_sessions   - find the session by title")
        print("      2. MATCH o<N> WITH ITS COLON - \"o1\" also matches o10 and o11")
        print("      3. mcp__ccd_session_mgmt__send_message with that sessionId\n")
        print("    TO REACH AN ORCHESTRATOR THAT IS NOT LIVE")
        print("      Put it on their OrchDoc. The Stop hook surfaces it at their next turn")
        print("      boundary and needs no process kept running.\n")
        print("    Genuinely broken - send_message errored? Pass --send-message-is-broken")
        print("    \"<what it did>\". That is a claim you tried, and it stays in the history.")
        return 2

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
    w.add_argument("--send-message-is-broken", metavar="WHAT_IT_DID",
                   help="arm anyway, recording what send_message actually did")
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
