"""PreToolUse gate: refuse a lane launch whose seed ASSERTS domain knowledge without citing it.

<!-- route-tags: seed lane launch hook citation propagation worker prompt -->

THE FINDING THIS IMPLEMENTS (o9L10, the adversarial pass, as its single best unseen idea):
**the lane-launch junction is interceptable.** Launching a worker is a Bash tool call, so a
PreToolUse hook can refuse a seed that asserts knowledge without citing where it came from.

THE DAMAGE IT PREVENTS (o10, verbatim): *"an inherited fact repeated into N worker prompts is N
copies of an unverified claim, and none of them carry their own provenance."* Their bootstrap
said *"v1 is CURRENT; v2 and v5 are DEPRECATED"*; they propagated it into FOUR lane seeds without
verifying it, then repeated it to the human - who immediately asked a question the inherited one-liner
could not answer. **A registry entry has ONE home to correct. Four seeds have four, and the
author has to remember all four.**

⭐ WHY A HOOK AND NOT A RULE. The only mechanism that demonstrably changes behaviour here is one
that arrives INSTEAD of the result: `block_python_heredoc.py` stopped two orchestrators seven
times in one day with immediate compliance. `W-STRIKEDONE` detected its defect 14 times per run
for weeks and was ignored. Same detection, opposite outcome - one refuses, one reports.

⛔ SCOPED DELIBERATELY NARROW, because a guard that fires on correct work is the failure mode
this workstream hit four times in one day. It fires ONLY when a seed does BOTH:
  1. asserts a checkable fact about a known operational subject (a vendor, an endpoint, a
     credential, a deploy target) - in the present tense, as a given, and
  2. cites NOTHING - no file path, no `route.py`, no memory note, no URL.
A seed that asks a lane to FIND something, or that carries one citation, passes untouched.

Exit 0 = allow. Exit 2 = refuse (stderr shown to the model).
"""
import json
import pathlib
import re
import sys

# Paths are RESOLVED, not hardcoded - see workspace_paths.py. A script carrying one
# machine's absolute path cannot run in a skill, a fresh clone, a worktree or the cloud,
# which is why none of this shipped anywhere until 2026-08-13.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from workspace_paths import WS, MEM  # noqa: E402

try:
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# A lane launch: `claude -p ...`. Not `claude mcp`, not `claude setup-token`.
LAUNCH_RE = re.compile(r"\bclaude\b[^|;&\n]*?\s-p\b")

# Operational subjects where an unverified inherited claim has actually cost us.
#
# ⛔ TWO HALVES, AND ONLY ONE OF THEM TRAVELS. The generic half below - `token`, `webhook`,
# `migration`, `staging` - is true of any workspace that runs anything. The other half is a list
# of THIS workspace's vendors and products, which is vocabulary, not mechanism: a different
# corpus has entirely different names, and hardcoding ours here means the gate is either wrong
# or silent everywhere else. So the vendor half is read from facets_vocabulary.json, the same
# file facets.py and block_retired_path.py already treat as the one place a workspace's own
# nouns are declared.
#
# ⭐ An absent vocabulary is a REAL STATE: the generic half still fires. That is the right
# degradation for a guard - a narrower gate, never a broken one, and never an exception thrown
# inside a PreToolUse hook that would take a legitimate launch down with it.
GENERIC_SUBJECTS = ["oauth", "api key", "token", "webhook", "endpoint", "migration",
                    "production", "staging", "prod db", "database"]


def _subject_re():
    words = list(GENERIC_SUBJECTS)
    try:
        raw = json.loads((pathlib.Path(__file__).resolve().parent
                          / "facets_vocabulary.json").read_text(encoding="utf-8"))
        words += [w for w in raw.get("operational_subjects", []) if w]
    except Exception:
        pass
    # Longest first so a multi-word subject cannot be shadowed by a prefix of itself.
    pat = "|".join(re.escape(w) for w in sorted(set(words), key=len, reverse=True))
    return re.compile(r"\b(" + pat + r")\b", re.I)


SUBJECT = _subject_re()

# The shape of an ASSERTED given: a present-tense claim of state, not a request to find out.
ASSERTION = re.compile(
    r"\b(is|are|uses?|lives?|returns?|requires?|expects?|has|have|must be|deprecated|current|"
    r"canonical|always|never|only)\b", re.I)

# Any of these means the seed carries provenance and the gate stays out of the way.
CITATION = re.compile(
    r"(route\.py|memory/|\.shared/|docs/|https?://|`[\w./-]+\.(md|py|json|ts|tsx|sh)`|"
    r"[\w-]+\.md\b|ORCHESTRATOR-DECISIONS|CLAUDE\.md|verify|check it|confirm it|"
    r"treat .{0,30}as a claim|do not trust)", re.I)


def seed_text(cmd):
    """The prompt payload, however it was passed. Best-effort by design: a seed we cannot read
    is ALLOWED - refusing what we cannot parse would block legitimate launches on quoting
    style, and a guard that blocks correct work gets removed."""
    m = re.search(r'-p\s+"\$\(cat\s+([^)]+)\)"', cmd)
    if m:
        try:
            import pathlib
            p = pathlib.Path(m.group(1).strip().strip("'\""))
            if not p.is_absolute():
                p = WS / p
            return p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""
    m = re.search(r"-p\s+(['\"])(.*?)\1", cmd, re.S)
    return m.group(2) if m else ""


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if payload.get("tool_name") != "Bash":
        return 0
    cmd = (payload.get("tool_input") or {}).get("command", "") or ""
    if not LAUNCH_RE.search(cmd):
        return 0

    seed = seed_text(cmd)
    if len(seed) < 200:
        return 0                       # a probe or a one-liner, not a knowledge-bearing seed
    if CITATION.search(seed):
        return 0                       # carries provenance - the gate has nothing to say

    # Find the specific lines that assert a subject fact, so the refusal names them. A gate that
    # says "something is wrong" gets overridden; one that quotes the line gets fixed.
    bad = []
    for ln in seed.split("\n"):
        s = ln.strip()
        if len(s) < 25 or s.startswith(("#", ">", "|")):
            continue
        if SUBJECT.search(s) and ASSERTION.search(s):
            bad.append(s)
    if not bad:
        return 0

    print("BLOCKED: this lane seed asserts operational facts and cites nothing.", file=sys.stderr)
    print("", file=sys.stderr)
    for s in bad[:4]:
        print("    %s" % s[:100], file=sys.stderr)
    if len(bad) > 4:
        print("    ... and %d more" % (len(bad) - 4), file=sys.stderr)
    print("", file=sys.stderr)
    print("An inherited fact repeated into N worker prompts is N copies of an unverified claim,",
          file=sys.stderr)
    print("and none of them carry provenance. That is not hypothetical: four seeds went out",
          file=sys.stderr)
    print("carrying 'the payments API v1 is CURRENT, v2/v5 DEPRECATED' from an unchecked seed,",
          file=sys.stderr)
    print("and the claim could not answer the first question asked of it.", file=sys.stderr)
    print("", file=sys.stderr)
    print("DO ONE OF THESE - each takes a line:", file=sys.stderr)
    print("  1. CITE it.  python .shared/scripts/route.py <subject>   then name the artifact",
          file=sys.stderr)
    print("     the fact came from, so the lane can re-read the source rather than trust you.",
          file=sys.stderr)
    print("  2. DEMOTE it. Write 'treat X as a claim to VERIFY, not a given' - a lane that",
          file=sys.stderr)
    print("     checks is worth more than one that inherits.", file=sys.stderr)
    print("  3. If it is genuinely yours and uncheckable, say so in the seed and say why.",
          file=sys.stderr)
    print("", file=sys.stderr)
    print("A registry entry has ONE home to correct. Four seeds have four.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
