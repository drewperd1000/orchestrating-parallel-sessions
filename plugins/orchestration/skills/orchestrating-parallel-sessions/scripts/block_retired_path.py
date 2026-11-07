#!/usr/bin/env python3
"""Intercept a command that reaches for a path we REMOVED, and hand back the current one.

<!-- subject: knowledge-routing -->
<!-- access: mcp -->
<!-- route-tags: retired removed hook pretooluse intercept mcp stale footgun redirect remedy -->

This is that principle applied to the commands that would bring it back. A PreToolUse hook
arrives INSTEAD of the tool result, which makes it the only mechanism in this harness with
demonstrated compliance - the session cannot not-see it.

Exit 2 blocks the call and shows stderr to the model. Exit 0 allows.
"""
import json
import re
import shlex
import sys

import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from mentions import executable_part  # noqa: E402

def _retired():
    """[(pattern, why, what to use instead, allowed targets or None)] from facets.RETIRED."""
    try:
        sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
        from facets import RETIRED as R
    except Exception:
        return []                     # cannot read the vocabulary: block nothing
    out = []
    for key, (why, aliases) in R.items():
        # The KEY itself is a searchable form too ('<vendor>-mcp'), with either separator.
        terms = list(aliases) + [key, key.replace("-", " "), key.replace("-", "_")]
        pat = "|".join(re.escape(t).replace(r"\ ", r"\s+") for t in sorted(set(terms), key=len,
                                                                          reverse=True))
        out.append((re.compile(pat, re.I), why, INSTEAD.get(key, "See docs/knowledge/INDEX.md"),
                    ALLOWED.get(key)))
    return out


def _vocabulary_retired():
    """The vocabulary's `retired` block. Absent or unreadable file -> {}."""
    try:
        p = pathlib.Path(__file__).resolve().parent / "facets_vocabulary.json"
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw.get("retired", {}) or {}
    except Exception:
        return {}


def _instead(entries):
    """{retired key: what to do instead}. Absent file -> no remedies."""
    return {k: d["instead"] for k, d in entries.items() if d.get("instead")}


def _allowed(entries):
    """{retired key: (flags, lowercased allowed values, compiled value recipes)} for entries
    that name allowed targets."""
    out = {}
    for k, d in entries.items():
        spec = d.get("allowed_targets") or {}
        flags = tuple(f for f in (spec.get("flags") or []) if isinstance(f, str) and f)
        values = frozenset(v.lower() for v in (spec.get("values") or [])
                           if isinstance(v, str) and v)
        recipes = []
        for r in spec.get("value_recipes") or []:
            try:
                recipes.append(re.compile(r))
            except (re.error, TypeError):
                pass
        if flags and values:
            out[k] = (flags, values, tuple(recipes))
    return out


# A single '&' separates two commands, and is not a separator inside a redirect: 2>&1, >&2, &>f.
_SEPARATORS = re.compile(r"&&|\|\||\|&|[;|\n]|(?<![<>&])&(?![&>])")
_CONTINUATION = re.compile(r"\\\r?\n")


def _segments(cmd):
    """The command split at shell separators: && || |& ; | & and newlines.

    A backslash-newline continues one command onto the next line, so it is joined first."""
    return [s for s in _SEPARATORS.split(_CONTINUATION.sub(" ", cmd)) if s.strip()]


def _flag_values(segment, flags):
    """Every value the segment passes to one of `flags`: `-s x`, `--service x`, `--service=x`."""
    try:
        toks = shlex.split(segment)
    except ValueError:                # an unbalanced quote: fall back to whitespace
        toks = segment.split()
    vals = []
    for i, t in enumerate(toks):
        for f in flags:
            if t == f:
                vals.append(toks[i + 1] if i + 1 < len(toks) else "")
            elif t.startswith(f + "="):
                vals.append(t[len(f) + 1:])
    return vals


def _targets_only_allowed(segment, allowed):
    """True when the segment names at least one target and every one is allowed."""
    flags, values = allowed[0], allowed[1]
    vals = _flag_values(segment, flags)
    return bool(vals) and all(v.lower() in values for v in vals)


_NOT_PLAIN = ("$(", "`", "<(", ">(", "<<", "#")
_ENV_PREFIX = r"\s*(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*"


def _plain_allowed_use(raw, pat, allowed):
    """True only when the raw command is a plain use of the term aimed at allowed targets."""
    for recipe in allowed[2]:
        raw = recipe.sub("VALUE", raw)
    if any(t in raw for t in _NOT_PLAIN):
        return False
    segs = _segments(raw)
    if [i for i, s in enumerate(segs) if pat.search(s)] != [len(segs) - 1]:
        return False                  # the term in no segment, in two, or not in the last
    last = segs[-1]
    lead = re.compile(_ENV_PREFIX + "(?:%s)" % pat.pattern, re.I)
    return bool(lead.match(last)) and len(pat.findall(last)) == 1 \
        and _targets_only_allowed(last, allowed)


_WORD_BREAK = " \t\n;&|()<>"


def _mask_literal_hashes(cmd):
    """The command with each '#' that bash reads as text replaced by '_'.

    Bash starts a comment only at a '#' that begins a word outside quotes. executable_part cuts
    every line at its first '#', so a '#' in quotes or in a URL hides the rest of that line."""
    out, quote, i = [], None, 0
    while i < len(cmd):
        ch = cmd[i]
        if ch == "\\" and quote != "'" and i + 1 < len(cmd):
            out.append(ch + ("_" if cmd[i + 1] == "#" else cmd[i + 1]))
            i += 2
            continue
        if quote:
            if ch == quote:
                quote = None
            elif ch == "#":
                ch = "_"
        elif ch in "'\"":
            quote = ch
        elif ch == "#" and i > 0 and cmd[i - 1] not in _WORD_BREAK:
            ch = "_"
        out.append(ch)
        i += 1
    return "".join(out)


# The search exemption in main(). _CHAINED is its chaining test for every entry without an
# allowance, unchanged; _RUNS_ANOTHER is the stricter test an entry with an allowance applies.
_SEARCH = re.compile(r"^\s*(grep|rg|ag|find|ls|cat|head|tail|wc|sed\s+-n|"
                     r"(python\s+\S*route\.py))\b")
_CHAINED = re.compile(r"&&|\|\||[;|`]|\$\(")
_RUNS_ANOTHER = re.compile(r"&&|\|\||[;|`\n]|\$\(|[<>]\(|(?<![<>&])&(?![&>]|\s*$)")


_ENTRIES = _vocabulary_retired()
INSTEAD = _instead(_ENTRIES)
ALLOWED = _allowed(_ENTRIES)
RETIRED = _retired()


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0                      # unparseable: ALLOW. A guard that cannot read must not block.
    cmd = (payload.get("tool_input") or {}).get("command") or ""
    if not cmd:
        return 0

    raw = cmd
    cmd = executable_part(cmd)
    full = executable_part(_mask_literal_hashes(raw))

    search = bool(_SEARCH.match(cmd)) and not _CHAINED.search(cmd)
    search_whole = bool(_SEARCH.match(full)) and not _RUNS_ANOTHER.search(
        _CONTINUATION.sub(" ", full).strip())

    for pat, why, instead, allowed in RETIRED:
        if allowed is None:
            if search or not pat.search(cmd):
                continue
        elif (search_whole or not (pat.search(cmd) or pat.search(full))
                or _plain_allowed_use(raw, pat, allowed)):
            continue
        print("BLOCKED: this reaches for a path that was REMOVED.\n", file=sys.stderr)
        print("  %s\n" % why, file=sys.stderr)
        print("  USE INSTEAD:\n  %s\n" % instead, file=sys.stderr)
        print("  (If you are deliberately writing ABOUT the removed path - a doc, a\n"
              "   tombstone, a commit message - that is fine; this only intercepts a\n"
              "   command that would USE or REINSTALL it. Rephrase, or write the file\n"
              "   with the Write tool rather than a shell command.)", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        # fail OPEN, always - and say so, as additionalContext JSON on stdout (hook_context.py)
        try:
            from hook_context import crash_text, emit
            emit("PreToolUse", crash_text("block_retired_path.py", exc))
        except Exception:
            pass
        sys.exit(0)
