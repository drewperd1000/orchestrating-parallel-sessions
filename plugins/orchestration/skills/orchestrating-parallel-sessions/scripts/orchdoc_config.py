"""Standing choices live in a CONFIG FILE, not in a forked copy of the skill.

THE PROBLEM THIS REPLACES. The shared skill said *"follow the human's standing preference"* -
correct, because it is read by people with different ones. So one machine hand-edited its own
copy to say *"HYBRID by default"*, and every sync from the shared version overwrote that edit
and it had to be re-applied by hand.

⛔ THAT IS A FORK MAINTAINED BY DISCIPLINE, WHICH IS THE DEFECT THIS WHOLE TOOLCHAIN EXISTS TO
REMOVE - a one-line difference re-applied manually will eventually not be, and nothing announces
it when the re-application is missed. The choice was being stored as a local EDIT when it should
have been local DATA.

⭐ SO THE SKILL STAYS BYTE-IDENTICAL EVERYWHERE AND READS THE ANSWER FROM HERE. Same split
already used for the orchestrator's own id and for vendor probes: **the generic principle ships;
the specific choice is data the shipped thing looks up.**

  config              show every setting, and where each came from
  config get <key>
  config set <key> <value>
  config unset <key>

⭐ AND "WHERE EACH CAME FROM" IS NOT DECORATION. A setting can be unset, set here, or overridden
by an environment variable, and an orchestrator about to act on one needs to know which - "the
default" and "what you chose" are different facts, and only one of them is safe to act on
silently.

⚠️ AN UNSET KEY IS UNSET, NOT DEFAULTED. `get` returns nothing and says so, because the
alternative is an orchestrator quietly adopting a launch mode the human never picked. Ask once,
write it here, and never ask again - which is the whole point.
"""
import argparse
import json
import os
import pathlib
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
CONFIG = ROOT / ".orchdoc-config.json"
LEGACY_ME = ROOT / ".orchdoc-me"

# Every setting the skill is allowed to look up, what it means, and the env var that overrides
# it. Anything not listed here is refused - a typo'd key that silently stores is a setting the
# skill will never read and the human will believe they set.
KEYS = {
    "human_name": ("What to call the human. Used in the plate heading and every "
                   "'needs your ruling' line.", "ORCHDOC_HUMAN"),
    "launch_mode": ("manual = the human pastes each worker prompt. hybrid = the orchestrator "
                    "self-launches headless workers and the human watches the mailbox.",
                    "ORCHDOC_LAUNCH_MODE"),
    "me": ("This orchestrator's id (o1, o2, ...). Mutating verbs refuse to write another "
           "orchestrator's doc.", "ORCHDOC_ME"),
    "docs_dir": ("Where OrchDocs live, so every orchestrator writes to one place and can read "
                 "the others.", "ORCHDOC_DIR"),
    "artifact_re": ("Extra regex for artifact names in citations, so a line-reference check "
                    "does not fire on this workspace's own naming.", "ORCHDOC_ARTIFACT_RE"),
}

VALID = {"launch_mode": ("manual", "hybrid")}


def load():
    data = {}
    if CONFIG.exists():
        try:
            data = json.loads(CONFIG.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
    # legacy single-purpose file, still honoured so nothing breaks on upgrade
    if "me" not in data and LEGACY_ME.exists():
        try:
            v = LEGACY_ME.read_text(encoding="utf-8").strip()
            if v:
                data["me"] = v
        except OSError:
            pass
    return data


def save(data):
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, CONFIG)


def resolve(key):
    """(value, source). Env beats file; unset returns (None, None) and never a default."""
    env = KEYS.get(key, (None, None))[1]
    if env and os.environ.get(env, "").strip():
        return os.environ[env].strip(), "env:%s" % env
    v = load().get(key)
    return (v, str(CONFIG.name)) if v else (None, None)


def cmd_show(args):
    print("orchdoc config - %s" % CONFIG)
    print()
    unset = []
    for k, (desc, env) in KEYS.items():
        v, src = resolve(k)
        if v is None:
            unset.append(k)
            print("  %-13s (unset)" % k)
        else:
            print("  %-13s %-24s  from %s" % (k, v[:24], src))
    print()
    if unset:
        print("  UNSET, and unset is not a default: %s" % ", ".join(unset))
        print("  An orchestrator must ASK before acting on any of these, once, and then")
        print("  `config set` the answer so it never asks again.")
    else:
        print("  Everything is set. Nothing here should be asked again.")
    print()
    print("  Change any of them at any time - tell the orchestrator, or run `config set`.")
    return 0


def cmd_get(args):
    v, src = resolve(args.key)
    if v is None:
        print("(unset)")
        return 1
    print(v)
    return 0


def cmd_set(args):
    if args.key not in KEYS:
        print("unknown setting %r. Known: %s" % (args.key, ", ".join(sorted(KEYS))),
              file=sys.stderr)
        print("A key nobody reads is a setting the human believes they made.", file=sys.stderr)
        return 2
    allowed = VALID.get(args.key)
    if allowed and args.value not in allowed:
        print("%s must be one of: %s" % (args.key, ", ".join(allowed)), file=sys.stderr)
        return 2
    data = load()
    old = data.get(args.key)
    data[args.key] = args.value
    save(data)
    print("  %s = %s%s" % (args.key, args.value,
                           "   (was %s)" % old if old and old != args.value else ""))
    env = KEYS[args.key][1]
    if os.environ.get(env, "").strip():
        print("  ⚠ $%s is set in this environment and OVERRIDES the file." % env)
        print("    The value you just saved will not take effect until it is unset.")
    return 0


def cmd_unset(args):
    data = load()
    if data.pop(args.key, None) is None:
        print("  %s was not set" % args.key)
        return 0
    save(data)
    print("  %s unset - an orchestrator will ask again before acting on it" % args.key)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("show").set_defaults(func=cmd_show)
    g = sub.add_parser("get")
    g.add_argument("key")
    g.set_defaults(func=cmd_get)
    st = sub.add_parser("set")
    st.add_argument("key")
    st.add_argument("value")
    st.set_defaults(func=cmd_set)
    un = sub.add_parser("unset")
    un.add_argument("key")
    un.set_defaults(func=cmd_unset)
    a = ap.parse_args()
    return a.func(a) if a.cmd else cmd_show(a)


if __name__ == "__main__":
    sys.exit(main())
