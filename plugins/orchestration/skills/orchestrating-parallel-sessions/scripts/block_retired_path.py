#!/usr/bin/env python3
"""Intercept a command that reaches for a path we REMOVED, and hand back the current one.

<!-- subject: knowledge-routing -->
<!-- access: mcp -->
<!-- route-tags: retired removed hook pretooluse intercept mcp stale footgun redirect remedy -->

The `<!-- retired: -->` facet makes a doc FINDABLE - real code, `facet_aliases()` in route.py.
But the redirect itself was a banner an agent had to read and obey, and **the original failure
was never a search.** Sessions called the removed `mcp__<platform>__*` tools DIRECTLY. A banner
in a document nobody opened stops nothing.

⛔ THE PATTERN THIS WORKSPACE KEEPS RELEARNING: a deploy platform's MCP integration was
documented as removed in THREE places and sessions kept reaching for it for weeks. Three copies
of "don't" lost to one habit, because reading is optional and reaching is reflex. What finally
worked was DELETING the MCP so the wrong path stopped existing.

⛔ NO VENDOR-SPECIFIC FACET LINE LIVES IN THIS HEADER, deliberately. `<!-- platform: ... -->`
and `<!-- retired: ... -->` naming one workspace's removed integration would pin this ENGINE to
that workspace's vocabulary - and the vocabulary is exactly what facets_vocabulary.json owns.
The generic tags above still route a session here for "retired path", "hook", "removed"; the
vendor terms route through the vocabulary, to the DOC that explains the removal, which is the
right landing place for a search anyway. A hook is not documentation.

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

# for retired: [KW]?"*). The first version hardcoded its own retired list here, which is a
# second copy of what `facets.py` already declares - and two copies of one vocabulary is the
# contract defect this workspace keeps rediscovering. It would have drifted the first time
# anyone retired a fourth path and armed only one of them.
#
# ⭐ Now adding an entry to `facets.RETIRED` does THREE things at once, with no second edit:
# the doc becomes findable by the retired term (route.py expands the aliases), the audit knows
# whether a landing doc exists, and THIS HOOK starts intercepting commands that reach for it.
# Declare once, armed everywhere.
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


# ⛔ THE REMEDY LIVES BESIDE THE `why`, IN facets_vocabulary.json - one entry, one edit.
# It was a dict here, which made a retired path a TWO-PLACE declaration: the vocabulary knew
# what was removed, this file knew what to do instead, and nothing made the second follow the
# first. Adding an entry to one and forgetting the other produces a block that stops a session
# with nowhere to go - and a session stopped with nowhere to go finds its own way, which is how
# the retired path gets rebuilt. That is the exact defect the comment above already names, so
# a second copy of half the vocabulary was the same mistake one level down.
#
# ⭐ The remedy is also workspace-specific in a way the MECHANISM is not: "use the release
# skill", "read this memory note" only mean anything in the corpus that has them. Engine here,
# vocabulary in the data file - the same split facets.py was forced into.
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


# ⭐ AN ENTRY CAN NAME THE ONE TARGET IT DOES NOT COVER. A retired command FORM can be retired for
# one target and documented for another: the same upload verb, pointed at a different service.
# Refusing the form everywhere refuses the documented use, and a guard that refuses correct work
# teaches people to route around it. The vocabulary entry says which flag names the target and
# which values are allowed:
#
#     "allowed_targets": {"flags": ["-s", "--service"], "values": ["<the one allowed target>"]}
#
# ⛔ AN ALLOW-LIST, NEVER A BLOCK-LIST. A command segment passes only when it names a target and
# EVERY target it names is allowed. No flag at all stays refused: the linked directory, not the
# text, then picks the target. So does a target written as an id, or any name not on the list.
# And the allowance covers only the plain form of the command - see _plain_allowed_use().
#
# The allowed use is often run after reading a credential from a file, and the documented way to
# read it is a `$(...)`. The plain form refuses every `$(...)`, so it refused the documented
# command. The entry lists each such recipe as a pattern under "value_recipes", and each match
# is read as one plain word before the form is judged: that recipe passes, and any other
# `$(...)` still fails. The patterns live in the vocabulary, never here, because the recipe
# names a vendor. A pattern that does not compile is skipped, so a bad pattern allows less,
# never more.
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


# The first version judged the flags of each segment that held the term, and a reviewer got a
# refused use through it three ways: nested in an allowed segment with $(...) or backticks; run
# by `sh -c '...'` with the allowed flag after it; and chained after a '#' inside quotes or a
# URL, where executable_part cuts the line and hides the rest of it.
#
# So the allowance reads the RAW command, with each documented value recipe read as one plain
# word, and holds it to a plain form: no $(, backtick, <(, >(, heredoc or '#' anywhere; the term
# in exactly ONE segment, and that segment the LAST one, so nothing is chained or piped after the
# allowed use (commands before it, such as a cd or an export, are fine); that segment STARTS with
# the term (after VAR=value prefixes) and holds it once; and every target it names is allowed.
# Anything else is judged as if the entry had no allowance.
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

    # ⛔ MATCH COMMAND POSITION, NOT PROSE. The first version blocked its OWN commit message,
    # because that message explains what was removed and why. Writing ABOUT a retired path is
    # the most common legitimate use there is - docs, tombstones, commit messages, this very
    # file - and a guard that punishes documenting the removal would be deleted within a day.
    #
    # So strip the places text LIVES before matching: heredoc bodies and -m/-F message
    # arguments. What remains is what the shell would actually execute.
    # ⭐ ONE DEFINITION, in mentions.py. This logic was written HERE first, then copied into
    # block_shared_tree_ops, and meanwhile five other guards hit the same defect without it -
    # for all eight instances.
    raw = cmd
    cmd = executable_part(cmd)
    # ⛔ executable_part also cuts each line at its first '#', including a '#' inside quotes or
    # inside a word such as a URL fragment, where bash starts no comment - so a command chained
    # after one is invisible in `cmd`. FULL keeps it. An entry with an allowance reads FULL: it
    # looks for its term there as well, and its search exemption reads its chaining from it.
    full = executable_part(_mask_literal_hashes(raw))

    # ⛔ A SEARCH IS THE OPPOSITE OF A USE. `grep "service account"` cannot reinstall anything -
    # someone searching for a retired path is trying to LEARN what replaced it, which is the one
    # thing this guard exists to teach. Refusing it blocks the correct response to its own
    # message.
    #
    # investigating the very claim the guard was getting wrong. Third instance today of a guard
    # refusing the correct action - and refusing correct work is what teaches people to reach
    # for the escape, which disables the rule for the real case.
    # ⛔ THE WHOLE COMMAND MUST BE THE SEARCH. A first version exempted anything STARTING with a
    # search verb, and its own test caught the hole immediately: `grep foo x && <forbidden>`
    # sailed through. A search does not launder what follows it, so any chaining operator
    # disqualifies the exemption.
    search = bool(_SEARCH.match(cmd)) and not _CHAINED.search(cmd)
    # It reads the chaining from FULL, and also counts a newline, a single '&' that is not part
    # of a redirect, <( and >( as running another command: `grep x f`, a newline, then a refused
    # use passed. Every other entry keeps the exemption exactly as it was, read from `cmd` -
    # reading FULL for every entry newly refused 111 of 1,480 generated commands, searches such
    # as `grep -n "<term>" "a#b"; echo x` among them.
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
