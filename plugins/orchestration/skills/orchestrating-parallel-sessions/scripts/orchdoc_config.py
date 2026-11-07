"""Standing choices live in a CONFIG FILE, not in a forked copy of the skill.

<!-- subject: orchestration -->

  config              show every setting, and where each came from
  config get <key>
  config set <key> <value>
  config unset <key>
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


def _workspace_root():
    """The folder that holds the settings file: the workspace, never the folder the tools are in.

    In the default layout the tools are in `<workspace>/.shared/scripts`, so it is two folders up.
    Installed as a plugin the tools sit in the plugin's cache, which a plugin update replaces, so
    the file lives in the user's workspace: $ORCHDOC_WORKSPACE, else the nearest folder above the
    current one that already holds a settings file or a decision document. None when there is
    none yet; `set` then creates the file in the current folder (see _create_root)."""
    if HERE.name == "scripts" and HERE.parent.name == ".shared":
        return HERE.parent.parent
    env = os.environ.get("ORCHDOC_WORKSPACE", "").strip()
    if env and pathlib.Path(env).is_dir():
        return pathlib.Path(env)
    start = pathlib.Path.cwd().resolve()
    for cand in [start] + list(start.parents):
        if (cand / ".orchdoc-config.json").exists() or any(cand.glob("ORCHESTRATOR-DECISIONS-*.md")):
            return cand
    return None


ROOT = _workspace_root()
CONFIG = (ROOT / ".orchdoc-config.json") if ROOT else None
LEGACY_ME = (ROOT / ".orchdoc-me") if ROOT else None


def _refresh():
    """Look for the workspace again. The Reader learns its workspace after this module is
    imported (from `--workspace`), and the first `set` creates the settings file, so the paths
    above are re-read at each use rather than fixed at import."""
    global ROOT, CONFIG, LEGACY_ME
    ROOT = _workspace_root()
    CONFIG = (ROOT / ".orchdoc-config.json") if ROOT else None
    LEGACY_ME = (ROOT / ".orchdoc-me") if ROOT else None


def _create_root():
    """Where the first `set` creates the settings file: the current folder, unless that is inside
    the tools' own folder (a plugin update would delete it). Returns an error text, or None."""
    global ROOT, CONFIG, LEGACY_ME
    cwd = pathlib.Path.cwd().resolve()
    if cwd == HERE or HERE in cwd.parents:
        return ("run this from your workspace folder (the folder you open your assistant in), "
                "not from the tools' folder, or set ORCHDOC_WORKSPACE to that folder")
    ROOT = cwd
    CONFIG = ROOT / ".orchdoc-config.json"
    LEGACY_ME = ROOT / ".orchdoc-me"
    return None

KEYS = {
    "human_name": ("What to call the human. Used in the plate heading and every "
                   "'needs your ruling' line.", "ORCHDOC_HUMAN"),
    "launch_mode": ("manual = the human pastes each worker prompt. hybrid = the orchestrator "
                    "self-launches headless workers and the human watches the mailbox.",
                    "ORCHDOC_LAUNCH_MODE"),
    "me": ("This orchestrator's id (o<N>, such as o99). ⛔ SESSION-scoped: set $ORCHDOC_ME in the "
           "session, never here - every orchestrator shares this workspace.", "ORCHDOC_ME"),
    "artifact_re": ("Extra regex for artifact names in citations, so a line-reference check "
                    "does not fire on this workspace's own naming.", "ORCHDOC_ARTIFACT_RE"),
    "prefixes": ("Extra entry-id letters and what each means, as LETTERS=Meaning pairs "
                 "separated by commas, for example X=Experiment,Y=Yield. They are added to "
                 "the built-in letters.", "ORCHDOC_PREFIXES"),
    "pre_label_prefixes": ("Letters a bare label may start with before a number (for "
                           "example X-1) that an entry may not use; comma separated. "
                           "Empty means no label is refused.", "ORCHDOC_PRE_LABELS"),
    "doc_folders": ("Folders, relative to the workspace and separated by commas, whose "
                    "Markdown and text files the Reader lists for review under Documents.",
                    "ORCHDOC_DOC_FOLDERS"),
    "work_notes_dir": ("The folder, relative to the workspace, where each lane keeps its "
                       "working notes.", "ORCHDOC_WORK_NOTES_DIR"),
    "reader_own_docs": ("A list of orchestrator ids whose documents a click in the Reader writes "
                        "as their owner, with no --not-mine. Empty by default.",
                        "ORCHDOC_READER_OWN_DOCS"),
    "reader_port": ("The port the Reader listens on and the links to entries point at (default "
                    "8787). Set it when another program already uses 8787, and start the Reader "
                    "with --port <the same number>.", "ORCHDOC_READER_PORT"),
}

SESSION_SCOPED = {"me"}

VALID = {"launch_mode": ("manual", "hybrid")}

OPTIONAL = {"prefixes", "pre_label_prefixes", "doc_folders", "work_notes_dir", "artifact_re",
            "reader_port", "reader_own_docs"}


def get_text(key, default=""):
    """The setting's value as text, or `default` when it is unset. For an OPTIONAL key only.

    A set value that is empty counts as set (an empty `pre_label_prefixes` turns the label
    check off), so this tests for presence in the file or the environment, not for truth."""
    env = KEYS[key][1]
    if env in os.environ:
        return os.environ[env].strip()
    data = load()
    if key in data and isinstance(data[key], str):
        return data[key].strip()
    return default


def get_list(key, default=()):
    """The comma-separated setting as a list of non-empty strings; `default` when unset."""
    raw = get_text(key, None)
    if raw is None:
        return list(default)
    return [x.strip() for x in raw.split(",") if x.strip()]


def load():
    _refresh()
    data = {}
    if CONFIG is not None and CONFIG.exists():
        try:
            data = json.loads(CONFIG.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
    # legacy single-purpose file, still honoured so nothing breaks on upgrade
    if "me" not in data and LEGACY_ME is not None and LEGACY_ME.exists():
        try:
            v = LEGACY_ME.read_text(encoding="utf-8").strip()
            if v:
                data["me"] = v
        except OSError:
            pass
    return data


def save(data):
    _refresh()
    if CONFIG is None:
        err = _create_root()
        if err:
            print("refusing: " + err, file=sys.stderr)
            raise SystemExit(2)
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, CONFIG)


def resolve(key):
    """(value, source). Env beats file; unset returns (None, None) and never a default."""
    env = KEYS.get(key, (None, None))[1]
    if env and os.environ.get(env, "").strip():
        return os.environ[env].strip(), "env:%s" % env
    if key in SESSION_SCOPED:
        return None, None          # env only; a file value here would be wrong for someone
    v = load().get(key)
    return (v, str(CONFIG.name)) if v else (None, None)


def cmd_show(args):
    _refresh()
    print("orchdoc config - %s" % (CONFIG or "no settings file yet; the first `set` creates "
                                  ".orchdoc-config.json in the folder you run it from"))
    print()
    unset = []
    for k, (desc, env) in KEYS.items():
        v, src = resolve(k)
        if v is None and k in OPTIONAL:
            print("  %-18s (default)" % k)
        elif v is None:
            unset.append(k)
            print("  %-18s (unset)" % k)
        else:
            print("  %-18s %-24s  from %s" % (k, v[:24], src))
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
    if args.key in SESSION_SCOPED:
        env = KEYS[args.key][1]
        print("refusing: %r is SESSION-scoped and cannot live in a shared file."
              % args.key, file=sys.stderr)
        print("  Every orchestrator shares this workspace, so a value here would claim that",
              file=sys.stderr)
        print("  ALL of them are %r - and each would then be refused on its own doc."
              % args.value, file=sys.stderr)
        print("", file=sys.stderr)
        print("  Set it per session instead:   export %s=%s" % (env, args.value),
              file=sys.stderr)
        return 2
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
