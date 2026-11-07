#!/usr/bin/env python3
"""Intercept READING a browse page when the session meant to SEARCH.

<!-- subject: knowledge-routing -->
<!-- route-tags: browse index cornerstone read hook pretooluse route instead of search -->

Exit 2 blocks and shows stderr to the model. Exit 0 allows. Fails OPEN.
"""
import json
import os
import re
import sys
import tempfile
import time

WINDOW = 1800          # a wave-through lasts 30 min, so the speed bump stays live in long runs
MARK = os.path.join(tempfile.gettempdir(), "claude-browse-waved.json")

# Generated BROWSE views - pages whose job is "what exists about X", not "where is X".
BROWSE = re.compile(r"docs[/\\]knowledge[/\\](?!CREATE\.md)[a-z0-9-]+\.md$", re.I)


READ_VERB = re.compile(
    r"^\s*(?:sudo\s+)?(cat|bat|nl|head|tail|sed|less|more|type|Get-Content|gc)\b", re.I)
SPLIT = re.compile(r"\|\||&&|[|;&\n]|\$\(")
MD = re.compile(r"[^\s'\"<>|;&()]+\.md\b")


def _candidate_paths(payload):
    """Every path this tool call would READ - from `file_path`, or from a Bash command.

        ... ; echo '{"tool_input":{"command":"cat docs/knowledge/x.md"}}' | python <shim> | head -2

    `head -2` supplied the verb at the far end of the pipeline and the path came out of a JSON
    string being echoed in the middle. **A guard matching a WORD for a ROLE** - written while
    wiring the very step that names that defect. So the command is split at every separator and
    only a segment whose FIRST word is a read verb contributes paths.
    """
    ti = payload.get("tool_input") or {}
    fp = ti.get("file_path") or ti.get("notebook_path") or ""
    if fp:
        return [str(fp)]
    cmd = str(ti.get("command") or "")
    if not cmd:
        return []
    out = []
    for seg in SPLIT.split(cmd):
        if seg and READ_VERB.match(seg):
            out += MD.findall(seg)
    return out


def _waved():
    try:
        with open(MARK, encoding="utf-8") as fh:
            d = json.load(fh)
    except Exception:
        return {}
    now = time.time()
    return {k: v for k, v in d.items() if now - v < WINDOW}


def _dispatch(payload):
    """Other guards that ride on THIS hook's registration. 0 allow, 2 block. Fails open.
    """
    try:
        # the same cheap reject the module has, before importing it: this runs on every Bash call
        if "grep" not in str((payload.get("tool_input") or {}).get("command") or ""):
            return 0
        import block_whole_tree_grep
        return 2 if block_whole_tree_grep.run(payload) == 2 else 0
    except Exception:
        return 0


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if _dispatch(payload) == 2:
        return 2
    path = ""
    for cand in _candidate_paths(payload):
        if BROWSE.search(str(cand).replace("\\", "/")):
            path = str(cand)
            break
    if not path:
        return 0

    key = os.path.basename(path).lower()
    waved = _waved()
    if key in waved:
        return 0                       # second reach - they meant it. Get out of the way.

    waved[key] = time.time()
    try:
        with open(MARK, "w", encoding="utf-8") as fh:
            json.dump(waved, fh)
    except Exception:
        return 0                       # cannot record the wave-through: never block twice

    print("HOLD: that is a BROWSE view, not a search.\n", file=sys.stderr)
    print("  These pages answer \"what do we know about X\" - they are generated, and they",
          file=sys.stderr)
    print("  list what each subject OWNS. They are not how you find one specific thing.\n",
          file=sys.stderr)
    print("  If you are looking for something specific, this is faster and searches the",
          file=sys.stderr)
    print("  WHOLE corpus rather than one branch:\n", file=sys.stderr)
    print("      python <scripts>/route.py <the words you would have grepped for>\n",
          file=sys.stderr)
    print("  ⛔ Why it matters: a subject tree can only tell you about the branch you picked.",
          file=sys.stderr)
    print("  Guess the wrong branch and an existing doc reads as absent - which is the exact",
          file=sys.stderr)
    print("  failure this whole system was built to stop. The router never has a branch to",
          file=sys.stderr)
    print("  guess wrong.\n", file=sys.stderr)
    print("  If you genuinely want to browse, re-issue it and it will pass.",
          file=sys.stderr)
    return 2


def _selftest():
    """Drive it both ways. A guard that has never refused has not been tested, and one that
    refuses everything is no better - so every FIRE case below has a PASS case beside it."""
    FIRE = [
        {"tool_input": {"command": "cat docs/knowledge/legal.md"}},
        {"tool_input": {"command": "head -40 docs/knowledge/orchestration.md"}},
        {"tool_input": {"command": "sed -n '1,50p' docs/knowledge/knowledge-routing.md"}},
        {"tool_input": {"command": "ls -la && cat docs/knowledge/legal.md"}},
        {"tool_input": {"command": "cat <your-workspace>/docs/knowledge/legal.md"}},
        {"tool_input": {"file_path": "docs/knowledge/legal.md"}},
    ]
    PASS = [
        {"tool_input": {"command": "git add docs/knowledge/legal.md"}},
        {"tool_input": {"command": "python <scripts>/knowledge_pages.py --write"}},
        {"tool_input": {"command": "ls docs/knowledge/"}},
        {"tool_input": {"command": "grep -n bipa docs/knowledge/legal.md"}},
        {"tool_input": {"command": "cat README.md"}},
        {"tool_input": {"command": "cat docs/knowledge/CREATE.md"}},
        {"tool_input": {"file_path": "docs/knowledge/CREATE.md"}},
        {"tool_input": {"file_path": "ORCHESTRATOR-DECISIONS-o99.md"}},
        {"tool_input": {}},
        {"tool_input": {"command":
            "echo '{\"command\":\"cat docs/knowledge/legal.md\"}' | python hook.py | head -2"}},
        {"tool_input": {"command": "echo cat docs/knowledge/legal.md"}},
        {"tool_input": {"command": "python x.py && head -5 notes.md"}},
    ]
    bad = 0
    for p in FIRE:
        hit = [c for c in _candidate_paths(p)
               if BROWSE.search(str(c).replace("\\", "/"))]
        if not hit:
            bad += 1
            print("  [FAIL] should FIRE, saw nothing: %r" % (p["tool_input"],))
    for p in PASS:
        hit = [c for c in _candidate_paths(p)
               if BROWSE.search(str(c).replace("\\", "/"))]
        if hit:
            bad += 1
            print("  [FAIL] should PASS, would fire on %s: %r" % (hit, p["tool_input"]))
    # The dispatch: a whole-workspace grep is refused THROUGH this hook, and an ordinary command
    # is not. `run` is stubbed so the check never writes the real wave-through file.
    import io
    import block_whole_tree_grep as wtg
    real_run, real_stdin = wtg.run, sys.stdin
    try:
        for cmd, stub, want in (("grep -rn x .", 2, 2), ("git status", 0, 0)):
            wtg.run = lambda _p, _rc=stub: _rc
            sys.stdin = io.StringIO(json.dumps({"tool_input": {"command": cmd}}))
            got = main()
            if got != want:
                bad += 1
                print("  [FAIL] dispatch: %r returned %s, wanted %s" % (cmd, got, want))
    finally:
        wtg.run, sys.stdin = real_run, real_stdin
    print("selftest: %d case(s), %d failure(s)" % (len(FIRE) + len(PASS) + 2, bad))
    return 1 if bad else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    try:
        sys.exit(main())
    except Exception as exc:
        # fail OPEN - and say so, as additionalContext JSON on stdout (hook_context.py)
        try:
            from hook_context import crash_text, emit
            emit("PreToolUse", crash_text("block_browse_instead_of_route.py", exc))
        except Exception:
            pass
        sys.exit(0)
