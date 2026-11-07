#!/usr/bin/env python3
"""Intercept a recursive grep over the WHOLE workspace (or home) and point at the router.

<!-- subject: knowledge-routing -->
<!-- route-tags: grep grep-r recursive whole-tree workspace-root timeout walk worktrees -->

    grep -rn -i "here's the thing" --include=*.md ... -l . ~/.claude/CLAUDE.md <memory dir>

from the workspace root. It hit the 120 s tool timeout and printed nothing: it was walking every
worktree checkout, every nested product repo and node_modules. The router answered the same
question in 2.7 s. Measured across transcripts: 71 whole-workspace `grep -r` runs in 19 sessions,
13 of them timing out.

⛔ THE ROUTE-FIRST RULE WAS LOADED AND DID NOT FIRE. A rule whose trigger is a thought cannot be
enforced. A command is a tool call, and a tool call passes a chokepoint - the same argument as
the browse hook this module is dispatched from.

⭐ IT BLOCKS ONCE, THEN GETS OUT OF THE WAY. A whole-tree walk is sometimes what someone means.
The first one is held with the router command spelled out; re-issuing the same command passes,
per session.

WHAT FIRES - all of it, in one segment of the command:
  - the segment's first word is grep / egrep / fgrep, after any of PREFIX_WORDS, `timeout <n>`,
    FOO=bar, or an opening `(` or `$(`, also inside double quotes
  - it is recursive: -r, -R, --recursive, -d recurse, --directories=recurse
  - its target is the workspace root or home: a path given explicitly, `.` or `*` or no path at
    all while the effective cwd is the root. `cd <dir> &&` earlier in the command moves the cwd.

WHAT PASSES:
  - `--exclude-dir` naming the worktrees (the walk that costs the most is excluded)
  - a recursive grep from, or aimed at, a subdirectory
  - `git grep`, `rg`, the Grep tool, a grep in a commit message or an echo
  - ⛔ NOT `| grep -v worktrees`: a filter after a pipe hides lines, it does not stop the walk.

NOT REGISTERED ON ITS OWN. `block_browse_instead_of_route.py` is already a PreToolUse hook on
Bash and calls `run()` here first, so this needed no settings.json change.

Exit 2 blocks and shows stderr to the model. Exit 0 allows. Fails OPEN.
"""
import hashlib
import json
import os
import pathlib
import re
import shlex
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

WINDOW = 1800          # a wave-through lasts 30 min, like the browse hook's
MARK = os.path.join(tempfile.gettempdir(), "claude-grep-waved")   # a folder: one file per hold
MAX_SEGMENT = 8000     # longer than any grep anyone types; shlex is quadratic (1 MB took 46 s)

GREP_WORDS = ("grep", "egrep", "fgrep")
SPLIT = re.compile(r"\|\||&&|[|;&\n()]")
PREFIX_WORDS = {"sudo", "time", "command", "env", "nohup", "if", "elif", "while", "until", "then",
                "do", "else", "!", "{"}
REDIRECT = re.compile(r"^\d*&?[<>]")
REDIRECT_ALONE = re.compile(r"^\d*&?[<>]+&?$")
# short options whose argument follows (attached or as the next token)
SHORT_WITH_ARG = "efmABCdD"
LONG_WITH_ARG = {"--regexp", "--file", "--max-count", "--context", "--after-context",
                 "--before-context", "--include", "--exclude", "--exclude-from", "--exclude-dir",
                 "--label", "--binary-files", "--devices", "--directories", "--group-separator"}
_HISTORICAL_WS = pathlib.Path(r"<your-workspace>")


def _roots():
    """The workspace root(s). This file lives at <root>/.shared/scripts when the hook runs it."""
    here = pathlib.Path(__file__).resolve()
    out = {_HISTORICAL_WS}
    if here.parent.name == "scripts" and here.parent.parent.name == ".shared":
        out.add(here.parent.parent.parent)
    return out


def _norm(p, cwd, home):
    """A path as the shell would resolve it, normalised for comparison. None if unresolvable."""
    p = (p or "").strip().strip("\"'")
    if not p:
        return None
    if p == "~" or p.startswith(("~/", "~\\")):
        p = home + p[1:]
    p = p.replace("${HOME}", home).replace("$HOME", home)
    if "$" in p or "`" in p:
        return None                       # an expansion we cannot evaluate - do not guess
    m = re.match(r"^/([a-zA-Z])(/|$)", p)  # Git Bash: /c/Users -> c:/Users
    if m:
        p = m.group(1) + ":/" + p[len(m.group(0)):]
    if p in ("*", "./*", ".\\*"):
        p = "."
    elif p.endswith(("/*", "\\*")):
        p = p[:-2]
    if not os.path.isabs(p):
        p = os.path.join(cwd, p)
    return os.path.normcase(os.path.normpath(p)).rstrip("\\/")


def _segments(cmd):
    """Split a command at ||, &&, |, ;, &, newline and parentheses - but only OUTSIDE quotes.

    A backslash outside quotes escapes the next character the same way (grep -rn a\\|b src).

    A `$( ... )` runs whether or not it sits inside double quotes, so its contents become their
    own segment and the command around it stays whole, with `$()` in its place.
    """
    out, buf, quote, stack, i, n = [], [], "", [], 0, len(cmd)
    while i < n:
        ch = cmd[i]
        if quote != "'" and cmd.startswith("$(", i):
            stack.append((buf, quote))
            buf, quote = [], ""
            i += 2
            continue
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
            elif ch == "\\" and quote == '"' and i + 1 < n:
                i += 1
                buf.append(cmd[i])
        elif ch == "\\" and i + 1 < n:
            i += 1
            buf += [ch, cmd[i]]
        elif ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch == ")" and stack:
            out.append("".join(buf))
            buf, quote = stack.pop()
            buf.append("$()")
        else:
            m = SPLIT.match(cmd, i)
            if m:
                out.append("".join(buf))
                buf = []
                i = m.end()
                continue
            buf.append(ch)
        i += 1
    out.append("".join(buf))
    out += ["".join(b) for b, _q in stack]
    return out


def _tokens(seg):
    try:
        lex = shlex.shlex(seg, posix=False)
        lex.whitespace_split = True
        raw = list(lex)
    except ValueError:
        raw = seg.split()
    toks, skip = [], False
    for t in raw:                          # a redirect is not a path: grep -rn x 2>/dev/null walks .
        if skip:
            skip = False
        elif REDIRECT.match(t):
            skip = bool(REDIRECT_ALONE.match(t))
        else:
            toks.append(t.strip("\"'") if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'" else t)
    return toks


def _strip_prefix(toks):
    """The words that run the next word: `time grep ...`, `if grep ...`, `timeout 60 grep ...`."""
    while toks:
        if toks[0] in PREFIX_WORDS or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[0]):
            toks = toks[1:]
        elif toks[0] == "timeout":
            toks = toks[1:]
            while toks and (toks[0].startswith("-") or toks[0][:1].isdigit()):
                toks = toks[1:]
        else:
            break
    return toks


def parse_grep(toks):
    """(recursive, excludes_worktrees, paths) for a tokenised grep invocation."""
    recursive, exclude_wt, positional, pattern_given = False, False, [], False
    i = 1
    while i < len(toks):
        t = toks[i]
        if t == "--":
            positional += toks[i + 1:]
            break
        if t.startswith("--"):
            name, eq, val = t.partition("=")
            if not eq and name in LONG_WITH_ARG and i + 1 < len(toks):
                i += 1
                val = toks[i]
            if name in ("--recursive", "--dereference-recursive"):
                recursive = True
            elif name == "--directories" and val == "recurse":
                recursive = True
            elif name == "--exclude-dir" and "worktrees" in val.lower():
                exclude_wt = True
            elif name in ("--regexp", "--file"):
                pattern_given = True
            i += 1
            continue
        if t.startswith("-") and len(t) > 1:
            letters = t[1:]
            for j, c in enumerate(letters):
                if c in "rR":
                    recursive = True
                if c in SHORT_WITH_ARG:
                    arg = letters[j + 1:]
                    if not arg and i + 1 < len(toks):
                        i += 1
                        arg = toks[i]
                    if c in "ef":
                        pattern_given = True
                    if c == "d" and arg == "recurse":
                        recursive = True
                    break
            i += 1
            continue
        positional.append(t)
        i += 1
    return recursive, exclude_wt, (positional if pattern_given else positional[1:])


def decide(payload, roots=None, home=None):
    """The offending grep segment, or "" when the command passes."""
    ti = payload.get("tool_input") or {}
    cmd = str(ti.get("command") or "")
    if not cmd or "grep" not in cmd:
        return ""                          # cheap reject: nearly every command
    try:
        from mentions import executable_part
        cmd = executable_part(cmd)         # a grep inside a message or an echo is prose
    except Exception:
        pass
    home = home or str(pathlib.Path.home())
    roots = {_norm(str(r), "/", home) for r in (roots or _roots())}
    homes = {_norm(home, "/", home)}
    cwd = str(payload.get("cwd") or os.getcwd())
    for seg in _segments(cmd):
        if len(seg) > MAX_SEGMENT:
            continue
        toks = _strip_prefix(_tokens(seg.strip()))
        if not toks:
            continue
        if toks[0] in ("cd", "pushd"):
            nxt = _norm(toks[1] if len(toks) > 1 else home, cwd, home)
            cwd = nxt or cwd
            continue
        if os.path.basename(toks[0]).lower() not in GREP_WORDS:
            continue
        recursive, exclude_wt, paths = parse_grep(toks)
        if not recursive or exclude_wt:
            continue
        here = _norm(cwd, "/", home)
        targets = [_norm(p, cwd, home) for p in paths] or [here]
        if any(t in roots or t in homes for t in targets if t):
            return " ".join(seg.split())
    return ""


def _prune(folder, now):
    """Delete holds older than WINDOW. Another session may get there first, which is fine."""
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        try:
            if now - os.path.getmtime(path) >= WINDOW:
                os.remove(path)
        except OSError:
            pass


MESSAGE = """HOLD: that is a recursive grep over the whole workspace (or home).

  It walks every worktree checkout, every nested product repo and node_modules. Many such runs hit the 120 s tool timeout.
  A filter after a pipe (| grep -v ...) does not stop the walk - it only hides lines.

  To find where something lives, use the router:
      python <scripts>/route.py <the words you would have grepped for>

  For literal text, use the Grep tool, or
      git grep -n "<text>"   plus   git -C <memory dir> grep -n "<text>"

  If you really need this walk, re-issue the same command and it will pass."""


def run(payload, mark=None):
    """0 allow, 2 block. Blocks the FIRST time a given grep is seen within WINDOW.

    `mark` is the folder holding one file per hold (MARK unless a test passes its own).
    """
    folder = mark or MARK
    seg = decide(payload)
    if not seg:
        return 0
    # keyed by session: the temp file is shared by every session, and one session's hold must not
    key = "%s|%s|%s" % (str(payload.get("session_id") or ""), str(payload.get("cwd") or "").lower(), seg)
    hold = os.path.join(folder, hashlib.sha1(key.encode("utf-8")).hexdigest())
    now = time.time()
    try:
        if now - os.path.getmtime(hold) < WINDOW:
            return 0                       # second reach - they meant it
    except OSError:
        pass                               # no hold yet
    try:
        os.makedirs(folder, exist_ok=True)
        with open(hold, "w", encoding="utf-8"):
            pass
        _prune(folder, now)
    except OSError:
        return 0                           # cannot record the wave-through: never block twice
    print(MESSAGE, file=sys.stderr)
    return 2


def _selftest():
    """Every FIRE case has PASS cases beside it: a guard only ever seen firing is untested."""
    import shutil
    from multiprocessing.pool import ThreadPool
    root = r"<your-workspace>"
    home = r"<your-home>"
    roots = {pathlib.Path(root)}

    def p(cmd, cwd=root):
        return {"tool_input": {"command": cmd}, "cwd": cwd}

    FIRE = [
        p("grep -rn -i \"here's the thing\" --include=*.md --include=*.py --include=*.json -l "
          ". ~/.claude/CLAUDE.md <your-home>/.claude/projects/x/memory"),
        p("grep -r banned"),                                   # no path = cwd = root
        p("grep -R banned ."),
        p("egrep --recursive 'a|b' ."),
        p("fgrep -rl x *"),
        p("grep -d recurse x ."),
        p("grep -rn x <your-workspace>"),        # root given explicitly
        p("grep -rn x <your-workspace>/", cwd=root + r"\.shared"),
        p("grep -rn x ~", cwd=root + r"\.shared"),             # home, from anywhere
        p("grep -rn x $HOME", cwd=root + r"\docs"),
        p("cd <your-workspace> && grep -rn x .", cwd=home),
        p("grep -rn x . | grep -v worktrees | head"),          # a pipe filter does not stop it
        p("LC_ALL=C grep -rIl x ."),
        p("ls; grep -A3 -rn x ."),
        p("echo hi | grep -rn \"a|b\" ."),                     # the walk after a pipe, with a quoted |
        p("x=$(grep -rn foo .)"),                              # a substitution still runs
        p("x=\"$(grep -rn foo .)\""),                          # also inside double quotes
        p("for f in $(grep -rl foo .); do echo $f; done"),
        p("(grep -rn foo .)"),
        p("time grep -rn foo ."),
        p("timeout 60 grep -rn foo ."),
        p("if grep -rq foo .; then echo y; fi"),
        p("env LC_ALL=C grep -rn foo ."),
        p("grep -rn foo 2>/dev/null"),                         # a redirect is not a path
        p("grep -rn foo 2>&1 | head"),
        p("grep -rn a\\|b ."),
    ]
    PASS = [
        p("grep -rn x .", cwd=root + r"\.shared\scripts"),     # a subdirectory
        p("grep -rn x .shared/scripts"),                       # aimed at a subdirectory
        p("cd .shared && grep -rn x ."),
        p("grep -rn --exclude-dir=.worktrees x ."),            # names the worktrees
        p("grep -rn --exclude-dir .claude/worktrees x ."),
        p("grep -rn --exclude-dir={.worktrees,node_modules} x ."),
        p("grep -n x CLAUDE.md"),                              # not recursive
        p("grep -A3 -n x ."),                                  # -A takes 3; no r
        p("grep -rn \"a|b\" .shared/scripts"),                 # a quoted | is part of the pattern
        p("grep -rn 'x;y&z' .shared"),
        p("grep -rn \"a|b\" . | head", cwd=root + r"\.shared\scripts"),
        p("grep -rn a\\|b .shared"),                           # an escaped | is part of the pattern
        p("grep -rn \"$(cat pat.txt)\" .shared"),               # the path is still seen
        p("grep -rn $(cat pat.txt) .shared"),
        p("grep -rn \">x\" .shared"),                           # a quoted > is a pattern, not a redirect
        p("cd $(git rev-parse --show-toplevel)/.shared && git status"),
        p("python x.py \"" + "grep -r . " * 2000 + "\""),       # over MAX_SEGMENT: not parsed at all
        p("git commit -m \"$(cat <<'EOF'\nnever grep -rn x . here\nEOF\n)\""),
        p("git grep -n x"),
        p("rg x ."),
        p("git commit -m \"never run grep -r x . from the root\""),
        p("echo 'grep -rn x .'"),
        p("python route.py grep -r"),
        p("grep -e -r x file.md"),                             # -r is the PATTERN here
        p(""),
    ]
    bad = 0
    for c in FIRE:
        if not decide(c, roots=roots, home=home):
            bad += 1
            print("  [FAIL] should FIRE: %r (cwd %s)" % (c["tool_input"]["command"], c["cwd"]))
    for c in PASS:
        got = decide(c, roots=roots, home=home)
        if got:
            bad += 1
            print("  [FAIL] should PASS: %r -> %r" % (c["tool_input"]["command"], got))
    # block once, then pass
    mark = tempfile.mkdtemp(prefix="claude-grep-waved-selftest-")
    probe = p("grep -rn selftest-%d ." % os.getpid(), cwd=str(sorted(_roots())[0]))
    saved = sys.stderr
    sys.stderr = open(os.devnull, "w")
    try:
        probe["session_id"] = "A"
        first, second = run(probe, mark=mark), run(probe, mark=mark)
        other = dict(probe, session_id="B")          # another session's identical grep is held too
        third = run(other, mark=mark)
        # an expired hold is pruned, and the same grep is held again rather than passed
        hold = hashlib.sha1(("A|%s|%s" % (probe["cwd"].lower(), decide(probe))).encode("utf-8")).hexdigest()
        os.utime(os.path.join(mark, hold), (1, 1))
        fourth = run(probe, mark=mark)
        # many sessions at once: every hold must survive, so every session's second try passes
        sessions = [dict(probe, session_id="load-%d" % i) for i in range(30)]
        with ThreadPool(10) as pool:
            load1 = pool.map(lambda q: run(q, mark=mark), sessions)
            load2 = pool.map(lambda q: run(q, mark=mark), sessions)
    finally:
        sys.stderr.close()
        sys.stderr = saved
        shutil.rmtree(mark, ignore_errors=True)
    if (first, second, third, fourth) != (2, 0, 2, 2):
        bad += 1
        print("  [FAIL] block-once per session, expiry: got %s %s %s %s, wanted 2 0 2 2"
              % (first, second, third, fourth))
    if set(load1) != {2} or set(load2) != {0}:
        bad += 1
        print("  [FAIL] parallel holds: first %s, second %s, wanted all 2 then all 0" % (load1, load2))
    print("selftest: %d case(s), %d failure(s)" % (len(FIRE) + len(PASS) + 2, bad))
    return 1 if bad else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    try:
        sys.exit(run(json.load(sys.stdin)))
    except Exception:
        sys.exit(0)
