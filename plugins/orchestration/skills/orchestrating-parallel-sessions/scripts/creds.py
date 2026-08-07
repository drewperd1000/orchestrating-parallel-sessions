"""What credentials exist, and does this one actually work - asked so a wrong answer is hard.

TWO RECURRING FAILURES, and they are opposite mistakes about the same fact.

  1. "THE MCP IS DOWN, SO I HAVE NO ACCESS."
     Often false, and the agent cannot tell without looking. A static token for the same
     service may be sitting on disk - needing no interactive auth, and working in headless
     runs, cron jobs and subagents where the MCP does not exist at all. `creds list` answers
     it in one call instead of by recall.
     ⚠️ It may also be TRUE. A machine that genuinely has nothing configured is a clean
     slate, not a broken one, and `NOT-CONFIGURED` says so rather than implying failure.

  2. "THE TOKEN IS DEAD."
     Usually the PROBE was wrong, not the token. Three documented shapes on this machine:
       - WRONG SCOPE: an account-scoped call rejects a workspace-scoped token BY DESIGN.
         Measured: a vendor CLI's identity command returned unauthorized for a token that
         passed a correctly-scoped API query seconds later, on the same machine.
       - WRONG HTTP CLIENT: a Python HTTP client hit an edge fingerprint block - HTTP 403 -
         on a token curl accepted for the identical request. The call never reached the
         vendor, so that 403 said nothing whatsoever about the credential.
       - WRONG VERB ENTIRELY: a health check that reports the transport, not the auth.
         One MCP reported `Connected` while every call it made returned Unauthorized.

⭐ THE COST IS NOT THE FAILED CALL - IT IS THE CONCLUSION DRAWN FROM IT. Both failures end
with an agent telling the human to go re-authenticate something that was never broken. That has
happened repeatedly, once sending the human to redo a login they had completed three days
earlier. **An auth error is a CLAIM, not proof.** This tool exists so the claim gets tested
before it gets reported.

⭐ SO THE OUTPUT NEVER SAYS "DEAD" WITHOUT RULING OUT THE OTHER TWO. Every probe here encodes
the KNOWN-CORRECT invocation for its service - right scope, right client, right endpoint - and
a failure is reported as one of:

    WORKS            the credential is good, verified by a call that can distinguish
    LIKELY-BAD       the correct probe ran and the vendor rejected it
    WRONG-PROBE      this call is expected to fail on a GOOD credential; here is the one
                     that is not
    CANNOT-TELL      the probe could not run (offline, no curl, edge block). NOT evidence
    NOT-CONFIGURED   no credential here at all. A clean slate, not a failure.

⭐ THE LAST THREE ARE THE POINT. Only LIKELY-BAD justifies asking the human for anything, and
collapsing any of the others into it is what manufactures the false report. "I could not
check", "you never set this up", and "it failed" are three different sentences, and a tool
that renders them identically will get someone sent to fix something that was never broken.

⚠️ THE PROBES BELOW ARE WORKED EXAMPLES, NOT A CLAIM ABOUT YOUR STACK. A different machine has
different vendors, different scope rules, and possibly nothing yet. What travels is the
CONTRACT - a probe must distinguish a rejected credential from a call that was never going to
succeed. Add your own in `creds_local.py` beside this file, defining EXTRA_PROBES.

  creds list                inventory every credential on disk, grouped
  creds check <service>     probe ONE service with the known-correct call
  creds check --all         probe everything that has a probe
  creds how <service>       print the correct usage WITHOUT calling anything
"""
import argparse
import json
import os
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
SECRETS = HERE.parent / "secrets"

WORKS = "WORKS"
LIKELY_BAD = "LIKELY-BAD"          # the correct probe ran and the vendor rejected it
CANNOT_TELL = "CANNOT-TELL"        # the probe could not run. NOT evidence about the credential
WRONG_PROBE = "WRONG-PROBE"        # this call fails on a GOOD credential by design
NOT_CONFIGURED = "NOT-CONFIGURED"  # no credential here at all

# NOT_CONFIGURED is separate from CANNOT_TELL on purpose. Someone who has not set a service up
# has not FAILED a check - and showing them a wall of "could not check" reads as breakage on a
# clean install. Same class of collapse as "nothing to look at" vs "looked and found nothing",
# which this tool's own history has hit repeatedly.


def read(name):
    p = SECRETS / name
    try:
        return p.read_text(encoding="utf-8").strip() if p.exists() else None
    except OSError:
        return None


def curl(args, timeout=25):
    """Always curl, never urllib. See failure shape 2 - Python clients get fingerprint-blocked
    by some vendor edges and return 403 on a GOOD credential, so a Python probe cannot
    distinguish a bad token from a blocked request."""
    try:
        r = subprocess.run(["curl", "-s", "-S", "--max-time", str(timeout)] + args,
                           capture_output=True, text=True)
        return r.returncode, r.stdout, r.stderr
    except FileNotFoundError:
        return None, "", "curl not installed"


# --------------------------------------------------------------------------------- probes
#
# Each probe returns (status, detail). A probe MUST be able to distinguish a rejected
# credential from a call that was never going to work - otherwise it produces exactly the
# false negative this file exists to prevent.


def probe_claude_headless():
    """TWO credential paths, and EITHER can be the stale one. Test, never assume.

    `claude -p` falls back to ~/.claude/.credentials.json when CLAUDE_CODE_OAUTH_TOKEN is
    unset. Both have been the broken one on different dates - so a fixed instruction ("always
    export the token" / "never export it") is wrong half the time by construction.
    """
    env = dict(os.environ)
    env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    try:
        r = subprocess.run(["claude", "-p", "Reply with exactly: OK", "--model", "haiku"],
                           capture_output=True, text=True, timeout=90,
                           env=env, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        return CANNOT_TELL, "claude CLI not on PATH"
    except subprocess.TimeoutExpired:
        return CANNOT_TELL, "no reply within 90s - not evidence about the credential"
    app_ok = "OK" in (r.stdout or "")
    tok = read("claude-code-oauth-token.txt")
    if app_ok:
        return WORKS, "app credential answers; launch WITHOUT CLAUDE_CODE_OAUTH_TOKEN"
    if not tok:
        return LIKELY_BAD, "app credential 401s and no secrets token on disk"
    env["CLAUDE_CODE_OAUTH_TOKEN"] = tok
    try:
        r2 = subprocess.run(["claude", "-p", "Reply with exactly: OK", "--model", "haiku"],
                            capture_output=True, text=True, timeout=90,
                            env=env, stdin=subprocess.DEVNULL)
    except Exception as e:
        return CANNOT_TELL, str(e)[:110]
    if "OK" in (r2.stdout or ""):
        return WORKS, "app credential is stale; launch WITH CLAUDE_CODE_OAUTH_TOKEN exported"
    return LIKELY_BAD, "BOTH paths rejected - this one genuinely needs the human to /login"


PROBES = {
    # The ONE probe that is universal to this skill's audience: every orchestrator running
    # headless lanes hits this, and it is the case where a wrong conclusion is most expensive.
    "claude-headless": (probe_claude_headless, "claude-code-oauth-token.txt",
                        "TWO paths: the app credential, and this file. EITHER can be stale - "
                        "probe first, then launch the way the probe answered."),
}

# A DIFFERENT USER HAS A DIFFERENT STACK, and possibly nothing yet. The probes above are
# worked EXAMPLES of the contract, not a claim about what anyone has. Drop a `creds_local.py`
# beside this file defining EXTRA_PROBES / EXTRA_GROUPS to make your own services first-class.
# The value that travels is the four-outcome model; the vendor list is local by nature.
try:
    import creds_local as _local           # noqa: F401
    PROBES.update(getattr(_local, "EXTRA_PROBES", {}))
except Exception:
    _local = None

# Generic buckets. Deliberately keyword-based rather than a vendor list, so a stranger's
# credentials sort into something sensible without this file knowing their stack.
GROUPS = [
    ("infrastructure / hosting", ("vercel", "render", "fly", "heroku", "aws",
                                  "gcp", "azure", "cloudflare", "netlify")),
    ("storage / backup", ("s3", "bucket", "blob", "backup")),
    ("ai + inference", ("openai", "anthropic", "claude", "gemini", "hugging", "replicate")),
    ("payments / commerce", ("stripe", "paddle", "lemon", "paypal", "billing")),
    ("analytics", ("amplitude", "mixpanel", "plausible", "analytics")),
    ("email / messaging", ("resend", "sendgrid", "postmark", "slack", "twilio", "webhook")),
    ("identity / oauth", ("oauth", "-token", "service-account", "sa.json")),
]


def cmd_list(args):
    files = sorted(p.name for p in SECRETS.glob("*")
                   if p.is_file() and p.suffix.lower() in (".txt", ".json")
                   and not p.name.startswith("."))
    print("credentials on disk - %s" % SECRETS)
    if not files:
        print("  NONE YET. That is a clean slate, not a problem - but it does mean this")
        print("  machine cannot reach anything without an MCP, so note that honestly rather")
        print("  than reporting a service as broken.")
        print()
        return 0
    print("  %d file(s). An MCP being down says NOTHING about these." % len(files))
    print()
    seen = set()
    for label, prefixes in GROUPS:
        rows = [f for f in files if any(p.lower() in f.lower() for p in prefixes)]
        rows = [f for f in rows if f not in seen]
        if not rows:
            continue
        seen.update(rows)
        print("  %s" % label.upper())
        for f in rows:
            mark = "  [probe]" if any(v[1] == f for v in PROBES.values()) else ""
            print("    %s%s" % (f, mark))
        print()
    rest = [f for f in files if f not in seen]
    if rest:
        print("  OTHER")
        for f in rest:
            print("    %s" % f)
        print()
    print("  [probe] = `creds.py check <service>` knows the correct call for it.")
    print("  For anything without one, `creds.py how <service>` is empty - which means")
    print("  NOBODY has written down the right invocation yet, not that there isn't one.")
    return 0


def cmd_how(args):
    key = args.service
    if key not in PROBES:
        print("no recorded usage for %r." % key)
        print("Known: %s" % ", ".join(sorted(PROBES)))
        print()
        print("An absent entry means the correct call has not been WRITTEN DOWN, which is not")
        print("the same as there being none. Check .shared/secrets/README.md and the memory")
        print("notes before concluding a service is unreachable.")
        return 2
    _fn, fname, note = PROBES[key]
    print("%s" % key)
    print("  file : %s%s" % (fname, "" if (SECRETS / fname).exists() else "   [NOT ON DISK]"))
    print("  use  : %s" % note)
    return 0


def cmd_check(args):
    keys = sorted(PROBES) if args.all else [args.service]
    bad = 0
    _statuses = []
    print("credential probes - each uses the KNOWN-CORRECT call for its service")
    print()
    for k in keys:
        if k not in PROBES:
            print("  %-18s no probe recorded. `creds.py how` lists what is known." % k)
            bad += 1
            continue
        fn, fname, note = PROBES[k]
        status, detail = fn()
        _statuses.append(status)
        print("  %-18s %-14s %s" % (k, status, detail[:86]))
        if status == NOT_CONFIGURED:
            pass                    # a clean slate is not a finding
        elif status == LIKELY_BAD:
            bad += 1
            print("  %-18s -> the CORRECT probe ran and was rejected. This one may really" % "")
            print("  %-18s    need attention. Correct usage: %s" % ("", note[:70]))
        elif status == CANNOT_TELL:
            print("  %-18s -> COULD NOT CHECK. This is NOT evidence the credential is bad;" % "")
            print("  %-18s    do not report it as one." % "")
    print()
    if all(s == NOT_CONFIGURED for s in _statuses):
        print("  Nothing is configured here yet. That is a clean slate, not a failure -")
        print("  add credentials, then add probes for them (see `creds_local.py` in the")
        print("  header) so the next agent can VERIFY rather than guess.")
        print()
    print("  LIKELY-BAD is the only outcome that justifies asking the human for anything.")
    print("  WRONG-PROBE and CANNOT-TELL are statements about the CALL, not the credential -")
    print("  and reporting either as an expired login is how someone gets sent to redo a")
    print("  login they already did.")
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="inventory every credential on disk").set_defaults(func=cmd_list)
    c = sub.add_parser("check", help="probe with the known-correct call")
    c.add_argument("service", nargs="?")
    c.add_argument("--all", action="store_true")
    c.set_defaults(func=cmd_check)
    h = sub.add_parser("how", help="print the correct usage without calling anything")
    h.add_argument("service")
    h.set_defaults(func=cmd_how)
    a = ap.parse_args()
    if a.cmd == "check" and not a.all and not a.service:
        ap.error("check needs a service, or --all")
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
