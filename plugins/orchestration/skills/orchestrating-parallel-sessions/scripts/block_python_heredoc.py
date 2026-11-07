"""PreToolUse hook: refuse `python <<EOF` when the inlined source contains a backslash.

<!-- subject: claude-tooling -->

  "\\b"           -> a literal 0x08 BACKSPACE byte, so the regex matched nothing, silently
  "C:\\Users\\..." -> SyntaxError: 'unicodeescape' codec can't decode
  "\\d"           -> SyntaxWarning: invalid escape sequence
  "\\\\b" vs "\\b"  -> an assertion comparing two strings that differ only in escaping

Condition 3 is what keeps this from becoming a nuisance. A heredoc carrying a commit message,
a JSON blob or plain prose has no backslashes and passes untouched - and those are the uses the
tooling docs actively recommend. A blanket ban on heredocs would be wrong AND would be
overridden within a day, which is worse than no rule: the block that gets disabled protects
nothing, while a block that fires only on the real failure keeps its credibility.

Exit 0 = allow. Exit 2 = block, with stderr shown to the caller.
"""
import json
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# `python - <<'EOF'`, `python3 <<EOF`, `py <<"EOF"` - and the body up to the closing marker.
HEREDOC_RE = re.compile(
    r"\b(?:python[0-9.]*|py)\b[^\n|;&]*?<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1(.*?)^\2\s*$",
    re.S | re.M)


def offending(command):
    """The heredoc body that would break, or None. Only bodies containing a backslash."""
    for m in HEREDOC_RE.finditer(command or ""):
        body = m.group(3)
        if "\\" in body:
            return body
    return None


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0                       # unreadable input is not a reason to block work
    if payload.get("tool_name") not in ("Bash", "PowerShell"):
        return 0
    cmd = (payload.get("tool_input") or {}).get("command", "")
    body = offending(cmd)
    if body is None:
        return 0

    sample = [l for l in body.split("\n") if "\\" in l][:3]
    print("BLOCKED: Python source inlined in a heredoc, containing backslashes.", file=sys.stderr)
    print("", file=sys.stderr)
    for l in sample:
        print("    %s" % l.strip()[:88], file=sys.stderr)
    print("", file=sys.stderr)
    print("The heredoc is not what breaks - it passes text through verbatim. What breaks is",
          file=sys.stderr)
    print("that inlining source puts TWO escaping layers in front of one string, and only one",
          file=sys.stderr)
    print("of them gets reasoned about. In one session this produced a literal 0x08 byte from",
          file=sys.stderr)
    print("`\\\\b`, a unicodeescape SyntaxError from a Windows path, and an assertion comparing",
          file=sys.stderr)
    print("two strings that differed only in escaping - each one silent or misleading.",
          file=sys.stderr)
    print("", file=sys.stderr)
    print("DO THIS INSTEAD - it has zero escaping layers:", file=sys.stderr)
    print("    1. Write the script to a file with the Write tool", file=sys.stderr)
    print("    2. Run it:  python <path>", file=sys.stderr)
    print("", file=sys.stderr)
    print("The file also survives for debugging, fails on a syntax error before executing",
          file=sys.stderr)
    print("anything, and stays reviewable instead of living only in shell history.",
          file=sys.stderr)
    print("", file=sys.stderr)
    print("(Heredocs WITHOUT backslashes - commit messages, JSON, prose - are not blocked.)",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
