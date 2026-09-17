#!/usr/bin/env python3
"""Intercept a command that reaches for a path we REMOVED, and hand back the current one.

<!-- subject: knowledge-routing -->
<!-- access: mcp -->
<!-- route-tags: retired removed hook pretooluse intercept mcp stale footgun redirect remedy -->

The workspace owner, 2026-08-13: *"is this a hook, or is this counting on prose?"*

It was prose, and he was right to ask. The `<!-- retired: -->` facet makes a doc FINDABLE - real
code, `facet_aliases()` in route.py. But the redirect itself was a banner an agent had to read
and obey, and **the original failure was never a search.** Sessions called the removed
`mcp__<platform>__*` tools DIRECTLY. A banner in a document nobody opened stops nothing.

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
import sys

import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from mentions import executable_part  # noqa: E402

# ⛔ ONE SOURCE, READ - NOT A SECOND COPY (the human, 2026-08-13: *"Can it programmatically check
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
    """[(compiled pattern, why, what to use instead)] built from facets.RETIRED."""
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
        out.append((re.compile(pat, re.I), why, INSTEAD.get(key, "See docs/knowledge/INDEX.md")))
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
def _instead():
    """{retired key: what to do instead} from the vocabulary. Absent file -> no remedies."""
    try:
        import json
        import pathlib
        p = pathlib.Path(__file__).resolve().parent / "facets_vocabulary.json"
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {k: d["instead"] for k, d in raw.get("retired", {}).items() if d.get("instead")}


INSTEAD = _instead()
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
    # the answer sitting four files away. Centralised 2026-08-17; see that module's docstring
    # for all eight instances.
    cmd = executable_part(cmd)

    # ⛔ A SEARCH IS THE OPPOSITE OF A USE. `grep "service account"` cannot reinstall anything -
    # someone searching for a retired path is trying to LEARN what replaced it, which is the one
    # thing this guard exists to teach. Refusing it blocks the correct response to its own
    # message.
    #
    # ⭐ Reported twice by o11 on read-only route.py searches, and it blocked o9's own grep while
    # investigating the very claim the guard was getting wrong. Third instance today of a guard
    # refusing the correct action - and refusing correct work is what teaches people to reach
    # for the escape, which disables the rule for the real case.
    # ⛔ THE WHOLE COMMAND MUST BE THE SEARCH. A first version exempted anything STARTING with a
    # search verb, and its own test caught the hole immediately: `grep foo x && <forbidden>`
    # sailed through. A search does not launder what follows it, so any chaining operator
    # disqualifies the exemption.
    if (re.match(r"^\s*(grep|rg|ag|find|ls|cat|head|tail|wc|sed\s+-n|"
                 r"(python\s+\S*route\.py))\b", cmd)
            and not re.search(r"&&|\|\||[;|`]|\$\(", cmd)):
        sys.exit(0)

    for pat, why, instead in RETIRED:
        if pat.search(cmd):
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
    except Exception:
        sys.exit(0)                   # fail OPEN, always
