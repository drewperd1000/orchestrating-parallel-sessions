#!/usr/bin/env python3
"""ONE definition of what an OrchDoc entry id looks like. Every reader imports it from here.

<!-- subject: orchestration -->
<!-- route-tags: id prefix vocabulary standard entry regex shared contract orchdoc -->

The human, 2026-09-14: *"we need to standardize in the Skill the prefixes used across the board,
otherwise it will cause problems for anything programmatic trying to work with them."*

⛔ **IT ALREADY HAD, IN GUARDS WIRED THAT SAME DAY.** Three of them hard-coded
`\\b([DFWSTQA]\\d+)\\b`, which **cannot match `DA10` or `G1`**:

    block_blanket_override.py    an override correctly naming DA10 is REJECTED
    stop_check_anchored_question a reply correctly anchored to DA10 is REFUSED
    check_supersession_edges.py  a correction naming DA10 counts as naming nothing

o8's doc has **23 `DA` entries**; o5 and o8 have **16 `G`**. So each guard punishes correct
behaviour in exactly the docs it does not know about - and silently, because a non-match looks
identical to an absence.

⭐ **THIS IS THE MARKER-FORMAT-IS-A-CONTRACT DEFECT.** Four copies of one vocabulary, each written
from what its author happened to have seen. The fix is not a wider regex in four places; it is one
definition that every reader imports, so widening it is a single edit.

**Measured across all 11 OrchDocs, 2026-09-14** - the real vocabulary, not a guess:

| prefix | n | meaning |
|---|---|---|
| `F` | 336 | **Finding** - a measured fact, a permanent record |
| `W` | 198 | **Work** - on the orchestrator's own plate |
| `D` | 129 | **Decision** - needs the human |
| `T` | 89 | **To-do** - needs the human to act |
| `DA` | 23 | o8's **original decision series**, closed 2026-09-04 and superseded by `D` |
| `A` | 16 | **Agreement** - a standing commitment the group will not break |
| `G` | 16 | **Guard** (o5, o8) - a refusal: "o8 will not ..." |
| `Q` | 9 | **Question** - needs an answer, not a ruling |
| `S` | 9 | **Standing practice** - a habit the group holds itself to |

⚠️ **`D1b` in o5 parses as no prefix at all** and is invisible to every reader. One entry, named
here so it is not rediscovered as a mystery.

⛔ **TWO OF THESE GLOSSES WERE WRONG UNTIL o8 CORRECTED THEM, 2026-09-14** - and both were wrong
in the same way: I inferred a MEANING from a letter instead of reading the entries.

- **`DA` was written as "Decision-Audio - scoped to the script/audio corpus".** It is not a
  category at all. It is o8's original decision series, and `D` is the series that replaced it.
  o8 gave a falsifiable shape and it holds: **zero date overlap.** DA1-DA23 opened 2026-07-28 to
  2026-08-17; D3-D34 opened 2026-09-04 onward. Not one DA after the changeover, not one D before.
  `DA3` is an authoring-model bake-off and `DA5` is TAGR product integration - neither is audio,
  which is what breaks the reading. **`DA` is CLOSED; every new decision goes to `D`.** The old
  gloss would have told a future reader to file an audio decision under DA.
- **`G` was written as "Guarantee".** It is **Guard**. All 16 live in `§5 GUARDS` and every one
  is phrased as a refusal. o8 confirmed their 13 and explicitly declined to speak for o5's; I
  measured o5's separately and they are the same shape (*"o5 will not act on, or assert from, a
  stale local working tree"*).

⭐ **THE LESSON IS ABOUT WHERE A GLOSS COMES FROM.** A prefix's meaning is a fact about the
entries, readable by measuring them. I guessed from the letters, and "DA" plausibly expands to
"Decision-Audio" for a doc that is about audio scripts - which is exactly why it survived review.
**A plausible expansion of an abbreviation is not evidence of what it abbreviates.**

⭐ **THE ORDER IN THE PATTERN IS LOAD-BEARING.** `DA` must be tried before `D`, or `DA10` matches
as `D` and leaves a stray `A10`. A vocabulary sorted alphabetically would introduce that bug; this
one is sorted longest-first for that reason and the test pins it.
"""
import re

# Longest-first. `DA` before `D`, or `DA10` matches as `D` plus a stray `A10`.
PREFIXES = ("DA", "D", "F", "W", "T", "A", "G", "Q", "S")

MEANING = {
    "F": "Finding", "W": "Work", "D": "Decision", "T": "To-do",
    # ⛔ NOT "Decision-Audio". o8's original decision series, closed 2026-09-04, superseded
    # by D. Nothing new is filed here. See the docstring for the date split that shows it.
    "DA": "Decision (o8, original series - CLOSED, superseded by D)",
    "A": "Agreement",
    "G": "Guard",                       # a refusal: "o8 will not ..." - measured, not guessed
    "Q": "Question", "S": "Standing practice",
}

#: Matches any OrchDoc entry id, e.g. D19, F101, W24, DA10, G1, Q2, S9.
ENTRY_ID = re.compile(r"\b(%s)(\d+)\b" % "|".join(PREFIXES))

# \u26d4 A HYPHEN BEFORE AN ID MEANS TWO DIFFERENT THINGS AND ONLY ONE IS A COLLISION.
# o1, 2026-09-22, after gate 4 refused their landing over the words "the human's own AT-S5 rewrite":
# the gate extracted S5 and demanded an entry that does not exist. o8's script names collide
# constantly - AT-S5, AT-S10a, THE HUMAN-A1, THE HUMAN-A2 - and the natural workaround is to stop naming
# the script, which makes their commit messages worse.
#
# o1 proposed requiring a non-hyphen boundary. Measured across all twelve OrchDocs that removes
# 228 matches: ~157 are the collisions (A2 x56, S5 x53, A1 x48) and ~71 are REAL references
# inside RANGES - W2-W8, D1-D17, F137-F140, T1-T8, D21-D32 - which six docs use.
#
# \u2b50 THE DISCRIMINATOR IS THE TOKEN BEFORE THE HYPHEN. In a range it is itself an id; in a
# script name it is not. So a hyphen-preceded id counts only when an id precedes the hyphen.
#
# \u26a0\ufe0f This still drops "pre-D35" and "SK-D1", where the preceding token is neither.
# Those are real references, so gate 4 will ask for less - a FALSE NEGATIVE, where the bug it
# replaces REFUSES a correct landing. A gate that asks slightly less beats one that blocks work
# and degrades the record to get around it.
# The token immediately before a hyphen. Script series are ALL-CAPS - AT-, THE HUMAN-, SC-, SK- -
# and hyphenated English is not: after-, pre-, non-, re-. That separates them without a list of
# script prefixes to maintain, which would go stale the first time o8 names a new series.
_CAPS_BEFORE_HYPHEN = re.compile(r"([A-Z]{2,})-$")


def _is_real_reference(text, start):
    """False when this match is the tail of a script name like AT-S5 or THE HUMAN-A1.

    \u26a0\ufe0f THREE CASES, and only the first is a collision:
        AT-S5, THE HUMAN-A1   an ALL-CAPS series prefix -> not a reference
        W2-W8, D1-D17    a RANGE, an id before the hyphen -> both are references
        pre-D35, W8-after-W25   hyphenated English -> a reference
    """
    if start == 0 or text[start - 1] != "-":
        return True
    m = _CAPS_BEFORE_HYPHEN.search(text[:start])
    if not m:
        return True                     # lowercase compound, or a range: keep it
    # An ALL-CAPS token that is itself an id is a range bound, not a series prefix.
    return bool(ENTRY_ID.fullmatch(m.group(1)))


def find_ids(text):
    """Every entry id in `text`, in order, deduplicated."""
    text = text or ""
    seen, out = set(), []
    for m in ENTRY_ID.finditer(text):
        if not _is_real_reference(text, m.start()):
            continue
        eid = m.group(1) + m.group(2)
        if eid not in seen:
            seen.add(eid)
            out.append(eid)
    return out


def is_entry_id(token):
    m = ENTRY_ID.fullmatch(token or "")
    return bool(m)


def prefix_of(eid):
    m = ENTRY_ID.fullmatch(eid or "")
    return m.group(1) if m else None


# ---------------------------------------------------------------------------------------------
# PRE-LABELS (o9L1320, o9L1322, o9:W210). The human, 2026-10-13: scripts are named to him by library id
# with a Reader link, never by a pre-label such as SC-1 or AT-3: "It's fine to keep them for your
# own inner processes, but I don't want to see them." ONE definition, read by
# stop_check_reader_links.py (replies) and orchdoc.py (`add` titles, the E-PRELABEL landing gate).
# ---------------------------------------------------------------------------------------------

PRE_LABEL = re.compile(r"(?<![\w/\\.#@-])(?:SC|AT)-[0-9]+(?![\w/\\@-])")

_PL_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_PL_INLINE_CODE = re.compile(r"(`+)(?!`)(.+?)(?<!`)\1(?!`)")
_PL_MD_TARGET = re.compile(r"\]\((?P<target>[^()\s]*(?:\([^()\s]*\)[^()\s]*)*)(?:\s+\"[^\"]*\")?\)")
_PL_URL = re.compile(r"<?\bhttps?://[^\s)>\]]+>?")
_PL_PATH = re.compile(r"[\w.~:-]*[/\\][\w./\\~-]*\.[A-Za-z]\w{0,5}\b")


def _pl_blank(text, spans):
    out, prev = [], 0
    for a, b in sorted(spans):
        a = max(a, prev)
        if b <= a:
            continue
        out.append(text[prev:a])
        out.append(re.sub(r"[^\n]", " ", text[a:b]))
        prev = b
    out.append(text[prev:])
    return "".join(out)


def bare_pre_labels(text):
    """[(label, offset)] for each bare pre-label (SC-1, AT-3) in `text`, in order.

    Fenced blocks, inline code, link targets, URLs and paths are blanked first, so a label kept
    inside code for internal notes is not found. Every offset is an offset into `text`."""
    if not text:
        return []
    lines, fence = [], None
    for line in text.split("\n"):
        m = _PL_FENCE.match(line)
        if fence is None:
            if m:
                fence = (m.group(1)[0], len(m.group(1)))
            lines.append(" " * len(line) if m else line)
            continue
        if (m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1]
                and not line[m.end():].strip()):
            fence = None
        lines.append(" " * len(line))
    s = "\n".join(lines)
    s = _pl_blank(s, [m.span() for m in _PL_INLINE_CODE.finditer(s)])
    spans = [m.span("target") for m in _PL_MD_TARGET.finditer(s)]
    for rx in (_PL_URL, _PL_PATH):
        spans += [m.span() for m in rx.finditer(s)]
    s = _pl_blank(s, spans)
    return [(m.group(), m.start()) for m in PRE_LABEL.finditer(s)]


if __name__ == "__main__":
    import io
    import sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    print("OrchDoc entry-id vocabulary (%d prefixes):" % len(PREFIXES))
    for p in PREFIXES:
        print("  %-3s %s" % (p, MEANING[p]))
    print("\npattern: %s" % ENTRY_ID.pattern)
