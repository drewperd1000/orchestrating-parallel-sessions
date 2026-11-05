#!/usr/bin/env python3
"""product-app script gate - deterministic, marker-scoped.

<!-- subject: script-writing -->

WHY THIS EXISTS
---------------
The Section 9 gates in AUTHORING-CHECKLIST.md (then AUTHORING-CHECKLIST.md) were prose commands taking a
filename argument, so they could be - and were - pointed at the wrong file. On
2026-08-11 a lane ran the ASCII gate against the checklist itself, got 8 hits
from marker glyphs, and reported "the checklist cannot pass its own screen."
That was a category error: the ASCII rule is a TTS-pipeline requirement for text
a MEMBER HEARS, and internal docs are explicitly allowed non-ASCII.

The human's fix, and it is the right one: do not write the scope down, ENFORCE it.

  "Did you create a prose process, or is it deterministic and impossible to fail?"

So this tool DISCOVERS its own targets by marker and REFUSES anything else. A
script file is identified by the declared header boundary:

    <!-- END SCRIPT HEADER -->

177 script files carry it; the checklist carries zero. Pointing this gate at a
rules doc is therefore not a mistake anyone can make - it is refused.

  A CHECK THAT TAKES ITS TARGET AS AN ARGUMENT CAN ALWAYS BE POINTED AT THE
  WRONG THING. A check that discovers its targets cannot.

USAGE
-----
    python check_script.py                 # every script under the workspace
    python check_script.py <path>          # one file - refused if not a script
    python check_script.py --list          # show what it would check, and stop
    python check_script.py --json          # machine-readable
    python check_script.py --arrival-audit # the UNRULED arrival-shape verbs
    python check_script.py --run-audit     # every BW7 warning, corpus-wide
    python check_script.py --selftest      # prove the EV1 + HV6 + BW7 screens
    python check_script.py --reuse-report [<path>]   # every HV6 10-gram collision:
                                           # other file, shared-gram count, phrases
    python check_script.py --corpus-at-sc --reuse-report   # o3:W5 preset corpus
    python check_script.py --corpus DIR [--corpus DIR ...]  # any corpus
    tests: test_check_script_sevengram_count.py (W5 shared-phrase count, W11 PA5 count)

MEASUREMENT MODEL (SCRIPT-CHECKLIST-2-LENGTH.md LN1-LN6, the human 2026-08-11)
    default render = every [[a | b | c]] resolved to its FIRST option,
                     metadata stripped, nothing above END SCRIPT HEADER
    AT  = words / 200 wpm + total authored pause seconds   (SINGLET always)
    SC  = words * 6 / 200 wpm                              (mirror x2 * tripling x3)
          EXCEPT a NEGATIVE opening: its whole first paragraph counts x1, not x6
          (said once in the YOU-block, not tripled, dropped from the I-block;
          the human 2026-10-09, o9:Q6). The script's `## Opening: negative|positive`
          header says which; an SC script without it gets OPENING-UNMARKED.
    targets: SC 8-10 min - AT night 10-12 min MINIMUM - AT day 8-10 min
    statement = one full paragraph; a performed count ("One" / "Two" / "Three"
                lines with short cues, after an announced count) is part of the
                statement it completes (PA5, o3:W11) - see fold_performed_counts
"""

import argparse
import io
import json
import math
import glob
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# the human's {{{ }}} review notes are removed at every read of a script, through ONE
# shared helper (o8L157, o8:D56). See script_notes.py.
from script_notes import read_script, strip_comments, strip_strikes  # noqa: E402
# The folders whose files outsiders write (the app's feedback form, the morning email
# briefing): no walk below opens anything under one; see _skip_dir(). private_folders.py is
# the one definition of the names, and it imports only os and re.
from private_folders import (NAMES as PRIVATE_NAMES, in_private_folder,  # noqa: E402
                             is_link, is_private_name)
import script_git_dates  # noqa: E402  (first-add date of a file, for the reuse note)
import training_header  # noqa: E402  (REWRITE_SUFFIX: what a human-rewrite name is)
import pause_cues  # noqa: E402  (PA0: the one canonical pause-cue form, o10L639)
import file_script  # noqa: E402  (record_files: the one definition of a library record file)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

MARKER = "<!-- END SCRIPT HEADER -->"


def has_marker(text):
    """True only when MARKER is a line of its own.

    o8L104, 2026-08-17: this used to be a plain substring test, so a DOC that merely
    QUOTED the marker inside a code span was discovered as a script and linted as one -
    it reported five [BANNED-ALWAYS] hits that were the rule names being discussed.
    That is exactly the category error this tool's docstring promises is impossible.

    o3L19, 2026-10-03: a second, narrow boundary also counts - see implicit_header_end().
    """
    if any(ln.strip() == MARKER for ln in text.splitlines()):
        return True
    return implicit_header_end(text) is not None


# The IMPLICIT header boundary (o3L19, 2026-10-03). working_corpus_o5's
# scripts-at-v2/idea-01 to idea-09 were written without MARKER, so every gate here
# refused them as "not a script file" and the US-SPELLING screen never read them,
# while idea-10 to idea-16 in the same folder carried MARKER and were screened. Their
# header is still unmistakable: the file OPENS with an unbroken run of "#" lines (no
# blank line) and that run includes a library id line "## ID: o<N>-<NNNN>". The header
# ends where that run ends. A doc that merely mentions an id further down (for example
# SCRIPT-ORGANIZATION-SYSTEM.md, line 526) does not open that way and stays a doc.
# Since o5L45 (2f62bae6, 2026-10-03) idea-01 to idea-09 carry MARKER too, so no file
# in the workspace relies on this path today; it stays as a fallback for headers
# written without MARKER.
# Measured 2026-10-03 across the workspace: the 9 idea files are the only files this
# boundary adds. Adding MARKER to such a file is still the better fix.
_IMPLICIT_ID_LINE = re.compile(r"^##\s*ID:\s*o\d+-\d+\s*$")


def implicit_header_end(text):
    """Line index just past an implicit header, or None when there is none."""
    lines = text.splitlines()
    n, seen_id = 0, False
    while n < len(lines) and lines[n].lstrip().startswith("#"):
        if _IMPLICIT_ID_LINE.match(lines[n].strip()):
            seen_id = True
        n += 1
    if not seen_id or n >= len(lines):
        return None
    return n


def split_marker(text):
    """Return (head, body) split at the first standalone MARKER line, else at the
    implicit header boundary (implicit_header_end)."""
    lines = text.splitlines(True)
    for i, ln in enumerate(lines):
        if ln.strip() == MARKER:
            return "".join(lines[:i]), "".join(lines[i + 1:])
    end = implicit_header_end(text)
    if end is not None:
        return "".join(lines[:end]), "".join(lines[end:])
    return text, text
WPM = 200.0
SC_MULT = 6

WORKSPACE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ⛔ THE CORPUS COMES FROM THE CHECKED SCRIPT'S OWN TREE, NOT FROM THIS FILE'S (o9 W-hv6, lane
# o9L1184, 2026-10-09). Until then HV6-REUSE compared every script against the library under
# WORKSPACE, the checkout this checker file sits in. The o10L319 review of app PR 502 (finding 7)
# measured the same script giving 0, 1 and 5 HV6 results depending on which checkout's checker
# ran. Two causes: (1) a different checkout holds different library contents, and (2) a script
# outside the checker's tree (a worktree file checked by main's checker: .worktrees is skipped)
# is not in the index at all, so reuse_detail() returned nothing. Now each script's corpus is
# the tree that holds it: the nearest folder above it that has a script-library/ or .shared/
# folder. A script with no such folder above it keeps the old behaviour (WORKSPACE).
# test_check_script_reuse_root.py.
REPO_ROOT_MARKERS = ("script-library", ".shared")


def repo_root_for(path):
    """The tree a script belongs to: the nearest folder at or above the script's folder that
    holds a script-library/ or .shared/ folder. WORKSPACE when there is none."""
    d = os.path.dirname(os.path.abspath(path))
    while True:
        if any(os.path.isdir(os.path.join(d, m)) for m in REPO_ROOT_MARKERS):
            return d
        up = os.path.dirname(d)
        if up == d:
            return WORKSPACE
        d = up


def _same_dir(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
# ⛔ `.worktrees` IS THE ONE THAT MATTERED AND IT WAS MISSING. The `.wt-`
# prefix test below caught the ad-hoc worktrees and missed the directory every
# orchestrator actually uses, so a scan rooted at the workspace discovered
# 1,562 scripts of which 1,366 - 87% - were the SAME scripts seen through other
# checkouts. Measured 2026-09-22; after the fix, 196 and zero from .worktrees.
# Every cross-file finding (HV6-REUSE above all) was reporting a script as
# duplicating itself at another commit.
# `iterations` and `THE HUMAN-REVIEW` hold COPIES of scripts: earlier versions kept
# word for word, and pilots copied out for the human to read. Scanning them made the
# reuse screen report a script as sharing phrases with its own copy (found by two
# W57 lanes, 2026-09-24; test_discover_skips_copies.py).
# `versions` is the same kind of folder: script-library/ keeps the older and other copies of a script
# in <script folder>/versions/ (o10:W226, flat since step 5), and the folder's own record file is the one text
# to compare. Skipping it keeps HV6-REUSE from reporting a script against its own copy. A corpus root
# that IS a versions folder (REUSE_VERSIONS_ROOT) is still read, because a root is walked from the inside and only
# the folders below it are skipped by name.
SKIP_DIRS = {".git", "node_modules", ".claude", ".codesight", "__pycache__",
             ".orchdoc-sweep", ".worktrees", "iterations", "THE HUMAN-REVIEW", "versions"}


# ⛔ NEVER OPENED: a folder named `app-feedback` or `email_briefing`, in any letter case. It
# holds text written by customers and by people who email the human, and no session may read it.
# Until o9 W152 (lane o9L1065, 2026-10-05) neither walk skipped one: discover() and
# discover_corpus() opened every .md (and .txt) under it to look for the header marker, and
# HV6-REUSE prints the phrases it matches. Now neither walk descends into one or starts
# inside one, and main() refuses a file inside one without opening it. The names are not
# added to SKIP_DIRS, which matches exact names; private_folders matches any letter case.
# test_check_script_private_folders.py.
#
# ⛔ A PRIVATE FOLDER HAS OTHER NAMES (o9 W152, lane o9L1069, after review o9L1067). The name
# test above was got past three ways: a junction pointing at one (os.walk in Python 3.12 follows
# a junction, and this account can make one without admin rights), and a start folder or file
# path written with the folder's 8.3 short name (APP-FE~1) or as
# `app-feedback::$INDEX_ALLOCATION`. Now the start folder of each walk and a file named on the
# command line go through private_folders.in_private_folder(), which also tests the path
# os.path.realpath() resolves, and neither walk follows a junction or a symbolic link, to a
# folder or to a file (private_folders.is_link). Not following links at all, rather than only
# links into a private folder, was chosen because nothing needs a walk to follow one: on
# 2026-10-05 the workspace's only links were 7 junctions, all named node_modules or .wt-*,
# which both walks skip by name, and following a link would show a walk the same scripts
# under a second path, the duplicate that HV6-REUSE reported before .worktrees was skipped.
def _skip_dir(name):
    """True for a folder neither walk descends into."""
    return name in SKIP_DIRS or name.startswith(".wt-") or is_private_name(name)


def _walk_dirs(dirpath, dirnames):
    """The folders in `dirnames` (under `dirpath`) that a walk descends into: not skipped by
    name (_skip_dir), and not a junction or symbolic link."""
    return [d for d in dirnames
            if not _skip_dir(d) and not is_link(os.path.join(dirpath, d))]

DROPDOWN = re.compile(r"\[\[(.+?)\]\]", re.S)
# Every authored cue shape in the corpus. The PARENTHESISED form is the one the
# AT pilots actually use - "(pause ~15s)" - and omitting it made this tool report
# a 30-cue script as having ZERO, which is the single most important AT signal
# (o8L89: the floor is met by the ramp, not by word count). Found by testing the
# tool against a real script instead of trusting it.
# CANONICAL FORM: ((pause 15s))   -- the human, 2026-08-12.
#
# Doubled parens because members will author their own scripts. A member writes
# "(pause here if you need to)" - a natural parenthetical that the SINGLE-paren
# form parses as a zero-duration cue. Nobody writes doubled parens by accident.
#
# Rejected: {pause 15s} (the {{token}} promotion gate rejects braces, so every
# cue would hard-block shipping); <pause 15s> (angle brackets are HTML in
# markdown - a RENDERER EATS IT, so a human reviewing a script sees no pause
# where one exists); [pause 15s] ("[x](y)" is link syntax, and it sits next to
# the [[a | b | c]] dropdown namespace); and the non-ASCII glyph pair U+2A97 /
# U+2A98, which defeats typing entirely but fails the Section 9 ASCII gate,
# fails cp1252, and - because the human's D-PAUSE ruling RETAINS the cue for members
# who live-record - risks rendering as tofu boxes on a member's phone.
#
# The DOUBLED form must be matched FIRST: the single-paren alternative would
# otherwise consume "(pause 15s)" out of the middle of "((pause 15s))".
PAUSE = re.compile(
    r"\(\(\s*pause[^)]*\)\)"      # ((pause 15s))  <- CANONICAL
    r"|\(\s*pause[^)]*\)"         # (pause ~15s)   <- legacy, 745 lines, migrating
    r"|\[\s*pause[^\]]*\]"        # [pause 15s]    <- legacy
    r"|\{\{\s*pause[^}]*\}\}"     # {{pause 15s}}  <- legacy
    r"|<\s*pause[^>]*>",          # <pause 15s>    <- legacy
    re.I,
)
# Anything not the canonical form is a migration target, reported not silently accepted.
PAUSE_LEGACY = re.compile(
    r"(?<!\()\(\s*pause[^)]*\)(?!\))"
    r"|\[\s*pause[^\]]*\]"
    r"|\{\{\s*pause[^}]*\}\}"
    r"|(?<!\{)\{\s*pause[^{}]*\}(?!\})"   # {pause 15s}: PA0 rejects it; not {{pause}}, not a {{{ note }}}
    r"|<\s*pause[^>]*>",
    re.I,
)
PAUSE_SECS = re.compile(r"(\d+(?:\.\d+)?)\s*s\b", re.I)

# Banned EVERYWHERE - any script, sleep or daytime.
BANNED_ALWAYS = re.compile(
    r"\baffirmations?\b|\bAI Voice\b|\bquietly\b|\bhand-wav\w*\b|\bhonestly\b", re.I
)
# Banned in SLEEP scripts only. Correct vocabulary in a daytime script.
BANNED_SLEEP_ONLY = re.compile(
    r"\bsurges?\b|\bsurging\b|\bcharges?\b|\bcharged\b|\bcharging\b"
    r"|\bfuel(s|ed|ling|ing)?\b|\benergized\b|\bwide awake\b|\bpower up\b"
    r"|\bclimb(s|ed|ing)?\b",     # SG5 lists "climb"; a warning only, the writer judges the sense
    re.I,
)

# BW2: "quietly" is banned as a filler adverb; "literal low-volume OK". A literal low volume is
# a quiet VOICE or SOUND, so a "quietly" in the same sentence and within three words before or
# two words after a verb of speech or sound is not flagged. Any other "quietly" ("the room
# settles quietly") still is.
_VOICE_VERB = re.compile(
    r"\b(?:speak|speaks|speaking|spoke|spoken|say|says|said|saying|whisper\w*|murmur\w*"
    r"|mutter\w*|mumble\w*|hum|hums|humming|hummed|sing|sings|singing|sang|sung"
    r"|talk|talks|talking|talked|call|calls|calling|called|read|reads|reading"
    r"|repeat|repeats|repeating|repeated|answer|answers|answered|chant\w*)\b", re.I)
_STATE_WORD = re.compile(
    r"(?:am|is|are|was|were|be|been|being|i'm|feel|feels|felt|become|becomes|became"
    r"|grow|grows|grew|stay|stays|remain|remains)", re.I)


def banned_always_hits(text):
    """Matches of BANNED_ALWAYS, less a literal low-volume "quietly" (BW2)."""
    out = []
    for mo in BANNED_ALWAYS.finditer(text):
        if mo.group(0).lower() == "quietly":
            a = max(text.rfind(c, 0, mo.start()) for c in ".!?;\n") + 1
            ends = [i for i in (text.find(c, mo.end()) for c in ".!?;\n") if i >= 0]
            b = min(ends) if ends else len(text)
            before = text[a:mo.start()].split()[-3:]
            after = " ".join(text[mo.end():b].split()[:2])
            # A state word between the speech verb and "quietly" starts a new clause about the
            # speaker ("I say I am quietly strong"): that "quietly" is the softener (o9L1234).
            vi = [i for i, w in enumerate(before) if _VOICE_VERB.search(w)]
            spoken_before = bool(vi) and not any(
                _STATE_WORD.fullmatch(w.strip(",;:'\"")) for w in before[vi[-1] + 1:])
            if spoken_before or _VOICE_VERB.search(after):
                continue
        out.append(mo)
    return out
SOFT = re.compile(r"\bsoft\w*\b", re.I)

# US-SPELLING - the human, 2026-09-24 (o8:D39): "Always use the American spelling of any/all
# words." AUTHORING-CHECKLIST-UNIVERSAL.md Section U2. A British form in the spoken text is a
# finding. The list is deliberately conservative: only words whose British form has one
# clear American replacement, to avoid flagging correct American words. Extend it when a
# new one turns up; it is not exhaustive, and a clean run is not proof of none.
US_SPELLING = {
    "neighbour": "neighbor", "colour": "color", "favourite": "favorite",
    "behaviour": "behavior", "honour": "honor", "humour": "humor", "labour": "labor",
    # added 2026-09-24 from o6's blog lanes (they got past prose_gate):
    "favour": "favor", "practise": "practice", "summarise": "summarize",
    "cannibalise": "cannibalize", "systematise": "systematize",
    "visualisation": "visualization", "organisation": "organization",
    "realisation": "realization", "prioritisation": "prioritization",
    "normalisation": "normalization",
    "flavour": "flavor", "harbour": "harbor", "rumour": "rumor", "vapour": "vapor",
    "armour": "armor", "odour": "odor", "savour": "savor", "splendour": "splendor",
    "vigour": "vigor", "endeavour": "endeavor", "clamour": "clamor",
    "realise": "realize", "recognise": "recognize", "organise": "organize",
    "apologise": "apologize", "emphasise": "emphasize", "prioritise": "prioritize",
    "visualise": "visualize", "normalise": "normalize", "minimise": "minimize",
    "maximise": "maximize", "energise": "energize", "centre": "center",
    "theatre": "theater", "metre": "meter", "fibre": "fiber", "litre": "liter",
    "analyse": "analyze", "paralyse": "paralyze", "catalogue": "catalog", "grey": "gray",
    "travelled": "traveled", "travelling": "traveling", "cancelled": "canceled",
    "labelled": "labeled", "modelled": "modeled", "fuelled": "fueled",
    "levelled": "leveled", "signalled": "signaled", "whilst": "while",
    "learnt": "learned", "spelt": "spelled", "programme": "program",
    "defence": "defense", "offence": "offense", "licence": "license",
    "jewellery": "jewelry", "mum": "mom", "tyre": "tire", "aluminium": "aluminum",
    # added 2026-10-03 (o3L19) after "neighbouring" turned up in a research doc. That
    # form was already caught in a script ("neighbour" + "ing"); these are forms the
    # list did not have at all. "-our":
    "parlour": "parlor", "valour": "valor", "rigour": "rigor", "tumour": "tumor",
    "fervour": "fervor", "candour": "candor", "saviour": "savior",
    "demeanour": "demeanor",
    # "-ise" (the "-ising" form is derived below):
    "specialise": "specialize", "utilise": "utilize", "criticise": "criticize",
    "characterise": "characterize", "categorise": "categorize", "memorise": "memorize",
    "sympathise": "sympathize", "authorise": "authorize", "finalise": "finalize",
    "stabilise": "stabilize", "optimise": "optimize", "mobilise": "mobilize",
    "customise": "customize", "personalise": "personalize", "standardise": "standardize",
    "generalise": "generalize", "harmonise": "harmonize", "neutralise": "neutralize",
    "jeopardise": "jeopardize", "fantasise": "fantasize", "agonise": "agonize",
    "hypnotise": "hypnotize", "familiarise": "familiarize", "symbolise": "symbolize",
    "verbalise": "verbalize", "idealise": "idealize", "materialise": "materialize",
    "revitalise": "revitalize", "sensitise": "sensitize", "desensitise": "desensitize",
    "moisturise": "moisturize", "mesmerise": "mesmerize", "scrutinise": "scrutinize",
    "capitalise": "capitalize", "socialise": "socialize", "vocalise": "vocalize",
    "civilise": "civilize", "catalyse": "catalyze",
    # "-isation":
    "specialisation": "specialization", "utilisation": "utilization",
    "characterisation": "characterization", "categorisation": "categorization",
    "optimisation": "optimization", "personalisation": "personalization",
    "customisation": "customization", "stabilisation": "stabilization",
    "memorisation": "memorization", "authorisation": "authorization",
    "civilisation": "civilization", "generalisation": "generalization",
    "standardisation": "standardization", "socialisation": "socialization",
    "hospitalisation": "hospitalization",
    # "-re" (the regex needs a word boundary before the stem, so the compounds are
    # listed as words of their own):
    "sombre": "somber", "spectre": "specter", "lustre": "luster", "calibre": "caliber",
    "sabre": "saber", "meagre": "meager", "sceptre": "scepter", "manoeuvre": "maneuver",
    "kilometre": "kilometer", "centimetre": "centimeter", "millimetre": "millimeter",
    "epicentre": "epicenter",
    # doubled "l", and the rest:
    "traveller": "traveler", "counsellor": "counselor", "marvellous": "marvelous",
    "channelled": "channeled", "dialled": "dialed", "quarrelled": "quarreled",
    "woollen": "woolen", "labelling": "labeling", "modelling": "modeling",
    "cancelling": "canceling", "levelling": "leveling", "signalling": "signaling",
    "fuelling": "fueling", "channelling": "channeling", "counselling": "counseling",
    "dialling": "dialing", "focussed": "focused", "focussing": "focusing",
    "skilful": "skillful", "wilful": "willful", "fulfil": "fulfill",
    "fulfilment": "fulfillment", "enrolment": "enrollment", "instalment": "installment",
    "sceptic": "skeptic", "scepticism": "skepticism", "mould": "mold",
    "smoulder": "smolder", "plough": "plow", "pyjama": "pajama", "cosy": "cozy",
    "moustache": "mustache", "ageing": "aging", "artefact": "artifact",
    "aeroplane": "airplane", "cheque": "check",
}
# Correct American words that a British stem plus a suffix happens to spell:
# the plurals of "analysis" and "paralysis" (first corpus run, 2026-09-24), and
# "programmed", the American past tense of "program" (o9, 35 hits across
# customer copy, the same day). prose_gate.py imports this set too.
# "-ise"/"-yse" verbs drop the e before "-ing" (realising, summarising), so the stem plus a
# suffix never spells that form. Derive it as a word of its own (found 2026-09-24).
US_SPELLING.update({b[:-1] + "ing": a[:-1] + "ing" for b, a in list(US_SPELLING.items())
                    if b.endswith(("ise", "yse"))})
# "programmer(s)" is the American spelling too; the "r" suffix (o3L19) would
# otherwise read it as "programme" + "r".
US_SPELLING_SAME = {"analyses", "paralyses", "programmed", "programmer", "programmers"}
def us_spelling_for(stem, suffix):
    """The American form of a British stem plus its suffix. A British "-re" word
    becomes "-er", so its "-d" past tense needs the "e" back: centred -> centered.
    The same holds for any British stem ending in "e" whose American form does not:
    catalogued -> cataloged, not "catalogd" (o3L19, 2026-10-03)."""
    am = US_SPELLING[stem.lower()]
    s = suffix.lower()
    if s in ("d", "r", "rs") and stem.lower().endswith("e") and not am.endswith("e"):
        s = "e" + s
    return am + s


US_SPELLING_RE = re.compile(
    r"\b(" + "|".join(sorted(US_SPELLING, key=len, reverse=True)) + r")"
    r"(s|d|ed|ing|es|er|ers|r|rs|ation|ations|ite|ites|ful|fully|ly|al|ally|able|hood|hoods"
    r"|less)?\b", re.I)

# R1 - the COMMA-LED adjective-pair tail, checklist file 4 (HV2/R1). Same mechanical
# shape as S1: a countable pattern against a per-8-statements budget. It was prose-only
# until 2026-08-18, which is exactly why DA8 kept asking whether a one-time sweep should
# run - an unrewritten script kept its violations and nothing reported them.
#
# Detected STRUCTURALLY, not morphologically. A first attempt matched adjective suffixes
# and missed "loose and easy" and "finished and done" - real tails whose words end in a
# bare "e". The shape R1 actually names is: a comma, two single words joined by "and",
# closing the clause. That is what makes it a TAIL.
#
# ONLY comma-led pairs count, per R1. Verb-led constructions ("my legs go heavy and
# warm") are explicitly allowed, and the required comma is what excludes them.
ADJ_PAIR_STOP = {
    "i", "me", "my", "he", "him", "she", "her", "it", "we", "us", "they", "them",
    "the", "a", "an", "this", "that", "these", "those", "then", "now", "here",
    "there", "so", "yet", "but", "or", "not", "no", "all", "one", "two", "up",
    "down", "in", "out", "on", "off", "over", "under", "back", "again",
}
_ADJ_PAIR_RE = re.compile(r",\s+([a-z]+)\s+and\s+([a-z]+)\s*[.;!?]", re.I)


class _AdjPair(object):
    """Same .findall/.search surface as a compiled pattern, minus the stoplist hits."""

    @staticmethod
    def _hits(text):
        return [m for m in _ADJ_PAIR_RE.finditer(text)
                if m.group(1).lower() not in ADJ_PAIR_STOP
                and m.group(2).lower() not in ADJ_PAIR_STOP]

    def findall(self, text):
        return [m.group(0) for m in self._hits(text)]

    def search(self, text):
        h = self._hits(text)
        return h[0] if h else None


ADJ_PAIR = _AdjPair()


# ==========================================================================
# EV1 - AN ARRIVAL VERB WITH AN INANIMATE SUBJECT
# ==========================================================================
# ⛔ MEASURED 2026-09-04: `grep -c arriv check_script.py` returned 0 on
# origin/main. The gate had NEVER screened this, and a lane reported "arrival
# verbs: all zero" as a PASSING FG5 screen. The zero came from nothing looking.
#
# The rule is `BW6` (SCRIPT-CHECKLIST-4B-BANNED-WORDS.md) and `Experience Verbs.txt`,
# RULED by the human 2026-08-13 and restated 2026-09-04:
#
#   "A bus arrives. A plane arrives. The proper time can arrive. Mental clarity
#    does not arrive. This is not natural human language and is a very bad
#    'AI-ism' that has been anchored into AI languaging."
#
# ⭐ A POSSESSIVE DOES NOT RESCUE IT. The human ruled on the live line "my mental
# clarity arrives ready and sharp" and on the attempt to argue a possessive out
# of it: "NOTHING escapes the speaker erasure ban - at least for now." So the
# subject head is read THROUGH any determiner: "my clarity arrives" fires
# exactly as "clarity arrives" does.
#
# ✅ "I arrive" is fine. The defect is the CONSTRUCTION, not the word.
#
# ⛔ SCOPE, AND IT IS DELIBERATELY NARROW - only `arrive/arrives/arrived/arriving`
# is a FINDING. `SCRIPT-CHECKLIST-3-DROPDOWNS.md` [DD1] catalogues five more
# verbs found in one collapsed dropdown (answers / rises / comes / snaps / sets),
# and `Experience Verbs.txt` names those same shapes under a heading that reads,
# verbatim: "NOT YET TESTED - do not ban these on an agent's say-so ... A false
# positive here costs the behaviour, not just the catch."
#
# Measured over the 187-script corpus before choosing: raw occurrences were
# come 382, set 119, rise 83, answer 58, snap 2 - and the overwhelming majority
# were not arrival shapes at all ("nothing to answer", "the Format B set", "my
# breathing rises"). Firing on those would bury the one verb the human HAS ruled on
# under several hundred hits he has not. They are reachable instead through
# `--arrival-audit`, which reports them WITHOUT asserting a defect, so the
# unruled five can be ruled on from evidence rather than from an agent's list.
ARRIVE = re.compile(r"\barriv(?:e|es|ed|ing)\b", re.I)

# The five candidate verbs [DD1] observed. Audit-only - never a finding.
ARRIVAL_AUDIT_VERBS = {
    "answer": re.compile(r"\banswer(?:s|ed|ing)?\b", re.I),
    "rise": re.compile(r"\b(?:rise|rises|rising|rose)\b", re.I),
    "come": re.compile(r"\b(?:come|comes|coming|came)\b", re.I),
    "snap": re.compile(r"\bsnap(?:s|ped|ping)?\b", re.I),
    "set": re.compile(r"\b(?:set|sets|setting)\b", re.I),
}

# Walked BACK over on the way to the subject: none of these can BE the subject.
_ARRIVAL_SKIP_BACK = frozenset("""
to not never just then now already soon still again simply even actually really
always sometimes often usually eventually suddenly only ever yet almost about
will would can could may might must shall should do does did has have had
is are was were be been being am get gets got let lets letting
make makes making keep keeps keeping start starts starting begin begins
seem seems watch watches see sees notice notices feel feels felt hear hears
want wants help helps allow allows wait waits waiting
going gonna gotten used supposed due meant able expected likely bound
don't doesn't didn't won't can't cannot couldn't wouldn't shouldn't mustn't
isn't aren't wasn't weren't hasn't haven't hadn't ain't
""".split())

# -ly words that are NOT adverbs. Without this, "my family arrives" walks past
# the subject and reports the determiner.
_LY_NOT_ADVERB = frozenset("""
family families belly bellies july ally allies rally rallies valley valleys
lily lilies jelly folly holly trolley
""".split())

# Reaching one of these means no subject is identifiable at the surface, so the
# screen ABSTAINS. Same posture as is_daytime(): a guessed verdict costs a wrong
# finding PLUS the inference spent later discovering it was wrong.
_ARRIVAL_STOP = frozenset("""
and or but nor so for the a an of that which when where while if as at in on
before after until since because with without than though although unless
whether like my your his her its our their this these those
""".split())

# ⛔ THINGS THAT GENUINELY ARRIVE - the human's own examples first (a bus, a plane, a
# letter, dinner at a restaurant, the proper time), then the animate class the
# ban was never about.
#
# ⚠️ NIGHT / MORNING / DAY / DAWN ARE DELIBERATELY ABSENT. They read like time
# nouns, but cliche-catalog #18 gives "The night arrives easily" as an explicit
# ❌ - it is "I fall asleep easily" with the person deleted, which is the exact
# harm. Only clock-and-schedule nouns are exempt.
CAN_ARRIVE = frozenset("""
i we you he she they who whoever me us him them
myself yourself himself herself ourselves themselves
everyone someone anyone everybody somebody anybody nobody
person people man men woman women child children kid kids baby babies
guest guests visitor visitors friend friends stranger strangers
family families mother father mom dad mum parents parent wife husband partner
son daughter brother sister doctor nurse driver courier crew team
neighbor neighbors neighbour neighbours
dog dogs cat cats bird birds horse horses
bus buses plane planes airplane train trains taxi cab car cars flight flights
boat boats ship ships ferry truck trucks ambulance subway tram uber ride
letter letters mail post package packages parcel parcels delivery deliveries
shipment order orders dinner lunch breakfast meal meals food drink coffee
groceries invitation invitations message messages email emails text texts
call calls news reply response results result
time times moment moments second seconds minute minutes hour hours
deadline deadlines appointment appointments turn date dates birthday holiday
weekend weekends anniversary christmas season week weeks month months year years
""".split())

_WORD = re.compile(r"[A-Za-z][A-Za-z']*")


def _subject_head(tokens, vi):
    """Walk back from token index `vi` to the subject head. None = abstain."""
    j = vi - 1
    while j >= 0:
        w = tokens[j][1].lower()
        if w in _ARRIVAL_STOP:
            return None
        if w in _ARRIVAL_SKIP_BACK:
            j -= 1
            continue
        if w.endswith("ly") and w not in _LY_NOT_ADVERB:
            j -= 1
            continue
        return j
    return None


def arrival_hits(spoken, verb_re=ARRIVE):
    """[(phrase, "")] for EVERY use of arrive/arrives/arrived/arriving.

    ⛔ HARD BAN, RULED BY THE HUMAN 2026-09-15: "Ban it outright."  The word is out in
    every form and every construction - including "I arrive", which the previous
    version of this rule explicitly permitted.

    ⭐ THIS FUNCTION USED TO BE THE SOFT VERSION AND THAT IS WHY IT IS NOW HARD.
    It walked back from the verb to the subject head and let anything in CAN_ARRIVE
    through - bus, plane, letter, dinner, the proper time, animates.  It was running,
    and this is what it permitted, measured across the corpus on 2026-09-15:

        131  total arriv* in the script corpus
         12  the "arrive at" form previously called legitimate
       ~  5  of those, actually the human sense
          1  line in 131 used it as a person would about people

    the night arrives · warmth arrives in my chest · the weight arrives in my thighs ·
    sleep arrives · buoyancy arriving · the phrases arrive · flight arrives at statement

    ⭐ THE REASON THE ALLOW-LIST HAD TO GO, and it is about WHO DECIDES rather than
    about the list being incomplete.  A subject test leaves the model judging, case by
    case, whether THIS subject may arrive - and the model is the miscalibrated
    instrument here.  the human: "It seems like you REALLY want to use the word 'arrive' -
    and that is precisely because your training has taught you to OVERUSE it."  A
    longer list would have been the same defect with more entries.

    ⭐ AND THE REASON THAT OUTLIVES THE WORD: `arrive` is the TIGHT verb.  It
    compresses "is built", "is sharpened", "is honed", "I feel it growing" into one
    word that costs nothing to generate.  the human tied it to the loose-writing rule in the
    same breath - "YOUR GOAL IS NOT TO WRITE TIGHTLY" - so this ban is an instrument of
    that rule, and any sibling verb that compresses the same way deserves the same
    suspicion.  Do NOT add siblings on an agent's say-so; BW6 names them as untested.

    Replacements for the clarity case, the human's own: BUILT, SHARPENED, HONED, FOCUSED,
    TRAINED, HEIGHTENED, or ever-present when I need it.

    CAN_ARRIVE and _subject_head are left in place: _subject_head has other callers,
    and CAN_ARRIVE is kept as the record of what the soft rule allowed.  Neither is
    consulted here any more.
    """
    hits = []
    for m in verb_re.finditer(spoken):
        start = max(0, m.start() - 40)
        end = min(len(spoken), m.end() + 25)
        hits.append((spoken[start:end].strip(), ""))
    return hits


def _arrival_hits_by_subject(spoken, verb_re=ARRIVE):
    """THE SUPERSEDED SOFT RULE - kept unreferenced so the change is legible.

    Nothing calls this.  It is the allow-list version, retained because a reader who
    finds the hard ban above should be able to see exactly what it replaced rather
    than take the docstring's word for it.
    """
    tokens = [(m.start(), m.group(0)) for m in _WORD.finditer(spoken)]
    at = {}
    for i, (pos, _w) in enumerate(tokens):
        at[pos] = i
    hits = []
    for m in verb_re.finditer(spoken):
        vi = at.get(m.start())
        if vi is None:
            continue
        si = _subject_head(tokens, vi)
        if si is None:
            continue
        head = tokens[si][1].lower().strip("'")
        # "I'm arriving" - the subject is the half before the apostrophe.
        if head in CAN_ARRIVE or head.split("'")[0] in CAN_ARRIVE:
            continue
        start = tokens[max(0, si - 2)][0]
        hits.append((spoken[start:m.end()].replace("\n", " "), head))
    return hits


# ==========================================================================
# BW7 - "RUN" AS A PROCESS BEING EXECUTED  (SOFT BAN - ADVISORY, NOT A FINDING)
# ==========================================================================
# RULED the human, 2026-09-04, and he called it a SOFT ban in the same breath:
#
#   "Here's a new word for a soft-ban: 'run'. It's an engineering term that has
#    worked its way into LLM languaging generally. ... the common thread of
#    where this always feels like an AI-ism is when thought processes are talked
#    about like software being 'run' - Humans don't really talk normally to say
#    'I run my sleep' or 'my body knows how to run the process'."
#
# SOFT MEANS ADVISORY. This screen emits a WARNING, never a finding, and it
# never changes the exit code. The distinction it draws is semantic, so some
# share of its hits will be judgement calls. A hard block on a judgement call
# gets overridden, and a screen that gets overridden gets switched off - at
# which point the real hits stop being caught either. A warning that names the
# phrase and asks a human to look costs nothing when it is wrong.
#
# HE GAVE A TEST, NOT A WORD. Two senses are ordinary human English and must
# stay silent, and they are the ones that make the prose sound human:
#
#   A MACHINE OPERATING          "their own small furnace, which has been
#                                 running the whole time"; the car running
#   SOMETHING TRAVELLING         "The release runs from my jaw up into my
#     ALONG A PATH                temples"; pain running hip to foot; warmth
#                                 running down the arms; things running through
#                                 our minds
#
# and one is the AI-ism:
#
#   A PROCESS EXECUTED           "everything that actually runs my sleep";
#     LIKE SOFTWARE               "I am running the same order I always run";
#                                 "I could run the whole thing half asleep";
#                                 "my body knows how to run the process"
#
# THE DISTINGUISHING QUESTION: is the subject OPERATING a process, or is
# something MOVING? So the matcher is aimed at the SYNTAX of operating, not at
# the word. A bare word match fires on every allowed use above and would be
# switched off within a day.
#
# The shape it fires on is a DIRECT OBJECT that is not a duration and not a
# distance - "runs my sleep", "running the same order", "run the whole thing",
# "runs itself" - plus the fronted-object case where the object has moved ahead
# of the verb and nothing follows it at all: "the same order I always run",
# "no part of it left for me to run".
#
# It stays silent on: a directional or path word after the verb (from, down,
# along, through, out, up, on, into, across); a duration or distance complement
# (the whole time, a good deal longer, all night long, all the way up); a state
# or manner complement (runs warm, runs slower, runs better); and a machine
# subject.
#
# TWO CASES RULED HERE, both ALLOWED, because neither has an object and neither
# casts anyone as an operator:
#   "the rest of me runs warm"            - the runs-hot / runs-cold state idiom
#   "my heart runs slower than my breathing"
# Both are a thing IDLING at a setting, which is the machine sense the human allowed,
# and both are things a person says out loud. Flagging them would teach lanes to
# strip a human-sounding construction, which is the exact cost the allowed
# examples exist to prevent.
#
# INPUT: EVERY DROPDOWN OPTION, not the default render. Measured 2026-09-04 -
# two of the five hits in AT-S5-portable-induction-PILOT.md sit in options 3 and
# 4, which default_render() throws away. Option 1 is the right input for LENGTH
# (one listener hears one render) and the wrong input for WORDING (every option
# is spoken to somebody).
RUN_VERB = re.compile(r"\brun(?:s|ning)?\b|\bran\b", re.I)

# TY7 - "THE BREATHING" FOR THE LISTENER'S OWN BREATH. A FINDING, NOT A WARNING.
# RULED the human, 2026-09-24 (o8:D20): "you should never say "The breathing...." in any
# context when it's used in a visualization that is targeted at the listener's own
# breathing. It should always just be "Breathing..." or "My breathing..."" He said
# never, so this is hard. "the breathing of the sea" is somebody else's breath and
# is left alone. Reads every dropdown option.
THE_BREATHING = re.compile(r"\bthe breathing\b(?!\s+of\b)", re.I)

# TY7, WIDENED - the listener's own HEARTBEAT, PULSE and BODY. RULED the human,
# 2026-09-25 (o8:D49): "yes - my body not the body, my heartbeat not the
# heartbeat". Same exclusion as breathing: "the heartbeat of the city", "the
# pulse of someone whose part is finished" and "the body of the text" belong to
# something else, so "of" after the noun silences all three.
#
# One optional word may sit between "the" and the noun, because the case that
# raised the question was "the easy heartbeat" (D3 strategic-rest draft) and a
# bare "the heartbeat" screen misses it. A few words that turn the phrase into
# something other than the listener's own are refused in that slot.
#
# WHY HEARTBEAT IS A FINDING AND PULSE AND BODY ARE WARNINGS. Measured
# 2026-09-25 over the 205 marker-scoped scripts, every dropdown option read:
#   heartbeat - 1 hit ("the easy heartbeat"), the listener's own. No other sense
#               of the word turns up in spoken script text, so a hard finding
#               costs nothing, the same call as breathing.
#   pulse     - 0 hits once "of" is excluded. But Pulse is also a product audio
#               layer, so "the pulse under the music" is not the listener's pulse
#               and nothing in the text says which one is meant.
#   body      - 26 warnings as shipped, and about half are not the listener at
#               all: header prose that leaks into the spoken render ("the body runs
#               Format-B", "the body spine should be RE-THEMED"), and marketing or
#               research copy about bodies in general ("Effort keeps the body
#               switched on"), in files that carry the script marker. A regex
#               cannot tell those from "the body turning over", so it warns and a
#               human judges, like BW7.
# Do NOT promote pulse or body to findings without re-measuring.
THE_HEARTBEAT = re.compile(
    r"\bthe (?:(?!(?:way|same|other|one|next|last)\b)[a-z-]+ )?heartbeats?\b(?!\s+of\b)",
    re.I)
THE_PULSE = re.compile(
    r"\bthe (?:(?!(?:way|same|other|one|next|last)\b)[a-z-]+ )?pulses?\b(?!\s+of\b)",
    re.I)
# Only "whole" / "entire" may sit between "the" and "body": "the script body" and
# "the template body" are document parts. "body scan" is the name of a practice,
# and a hyphenated compound ("the body-focused scripts") is not a body at all.
THE_BODY = re.compile(r"\bthe (?:(?:whole|entire) )?body\b(?!-)(?!\s+(?:of|scan)\b)", re.I)

# After the verb: a PREPOSITION or a particle. Either something is MOVING
# ("runs from my jaw", "running through our minds") or the noun that follows
# belongs to the preposition, not to `run` ("runs without my hand", "run like
# a wired bull"). Both are allowed, and neither is an operated object.
_RUN_DIRECTION = frozenset("""
from down up along through out in into onto on off across over back past
around under underneath beneath behind between beside toward towards to
away outward inward upward downward ahead forward onward alongside against
with without within at by as like about for of near upon beyond despite
throughout among amid per opposite
""".split())

# After the verb: a DURATION or a DISTANCE, not an object. Allowed.
# "running the whole time", "run all night long", "a good deal longer",
# "runs all the way up", "ran a mile".
_RUN_EXTENT = frozenset("""
time times while moment moments minute minutes hour hours second seconds
night nights day days evening morning week weeks month months year years
course length span stretch distance deal bit lot spell way ways
now then again tonight today tomorrow yesterday anymore still yet
here there anyway always never ever already
thousands hundreds dozens millions plenty ages direction directions
mile miles block blocks lap laps race races marathon errand errands
""".split())

# After the verb: a STATE or MANNER complement - the engine runs hot. Allowed.
_RUN_STATE = frozenset("""
warm warmer cool cooler cold colder hot hotter deep deeper
slow slower slowly fast faster quick quicker high higher low lower
long longer late later early earlier short shorter
dry clear clean quiet quieter smooth smoother steady steadier even
free freely wild loose empty full thin light heavy
better best worse well fine easy easier hard harder true
calm calmer sharp sharper rough rougher close closer lighter heavier
softer straight level ragged shallow wide narrow
tired tight live lean dark silent flat blind hungry
""".split())

# Walked past on the way to the object head - none of these can BE the head.
_RUN_SKIP_FWD = frozenset("""
a an the my your his her its our their this that these those
one all each every any some no not just only right straight
whole entire same very own more most much many other another such
good great little fair full half few couple quite rather pretty real plain
""".split())

# Reaching one of these means the clause ended before an object appeared.
_RUN_CLAUSE_END = frozenset("""
and or but so because when while if though although unless until since
who which whose where whether than once
is are was were be been being am has have had will would shall should
can could may might must do does did
""".split())

# Immediately before a bare "run": it is a NOUN. "a run", "the long run".
_RUN_NOUN_DET = frozenset("""
a an the my your his her its our their this that one every another each
""".split())

# A subject that genuinely operates machinery, or is machinery. Allowed even
# with an object: "the furnace runs the whole house".
_RUN_MACHINE = frozenset("""
furnace furnaces engine engines motor motors machine machines pump pumps
fan fans heater heaters boiler boilers radiator radiators generator generators
clock clocks watch tap taps faucet faucets fridge freezer washer dryer
dishwasher kettle stove oven car cars truck trucks train trains bus buses
""".split())

# Things a person ORDINARILY runs, in plain English - an organisation, a
# scheduled event, a piece of equipment. Allowed as objects.
#
# NEVER add a mental or bodily process here. sleep, breathing, body, mind,
# thought, order, routine, process, operation, program are exactly what BW7
# exists to catch, and they take the same syntax as these do.
_RUN_ORDINARY_OBJECT = frozenset("""
company companies business businesses team teams crew department office
shop shops store stores restaurant kitchen farm site sites branch
meeting meetings session sessions shift shifts show shows rehearsal
calendar schedule diary agenda discount promotion sale sales campaign
errand errands household bath tank tanks drill drills lap laps
race races marathon numbers ledger books payroll
""".split())

# "run themselves down", "run himself ragged" - the exhaustion idiom, marked
# by the particle AFTER the reflexive. "runs itself" alone stays a hit.
_RUN_REFLEXIVE = frozenset("""
myself yourself himself herself itself ourselves yourselves themselves
""".split())
_RUN_REFLEXIVE_PARTICLE = frozenset("down out ragged dry thin flat over".split())

# A BODY PROCESS running by itself - "my breathing runs itself now". RULED the human,
# 2026-09-24 (o8:D20): "a classic technique in autogenics is letting bodily
# processes happen by themselves. So bodily operations are exempt". Nobody is
# operating anything in that line; it says the listener has stopped. Only the
# reflexive form is exempt, and only with one of these as its subject. A mind,
# a thought, the night or sleep running itself still warns, and so does the body
# operating an object ("my body knows how to run the process" is the human's own
# banned example). Do NOT add body, mind, sleep or system here.
_RUN_BODY_PROCESS = frozenset("""
breathing breath breaths heartbeat heartbeats heart pulse lungs blood
""".split())

# The fronted-object case needs a subject that could be an operator.
_RUN_OPERATOR = frozenset("""
i me my we us our myself ourselves you your yourself
body mind brain head system self
""".split())

_RUN_BOUNDARY = set(".;:!?,")


def _run_break(gap):
    """True when the text between two tokens ends the clause.

    A SPACED dash does: "it can keep running - this was never a" is two
    clauses, and reading across it reported "was" as an operated object.
    An UNSPACED hyphen does not: "out-breath" is one word, and treating its
    hyphen as a boundary would blind the screen to whatever follows it.
    """
    if any(ch in _RUN_BOUNDARY or ch == "\n" for ch in gap):
        return True
    return " -" in gap or "- " in gap


# HV9 - VARY THE COMPARISON FRAME (SCRIPT-CHECKLIST-4-VOICE.md, RULED the human
# 2026-10-02, o8:D56). Two frames are worn out by OVERUSE: "...the way it does
# every..." and "[verb] the way I [verb]". The human, 2026-10-10: "what we came down
# to was heavily limiting it *because of* the overuse." Neither shape is wrong
# once, so this counts every comparison frame in a script's spoken text (menu
# options included). The human, 2026-10-10 17:09 PT: it counts per PLAYBACK (worst case, one
# option per dropdown; comparison_playback) plus HV9-OPTION-SET (max 2 in one dropdown). Five
# shapes are counted: "the way <subject> <verb>", "just like", "just as a/an",
# "(in exactly) the same way that/as", and "as completely/fully/... as"; and, since
# o9:W221 (lane o9L1335), "like" (a simile) and "as if"/"as though". The box counts
# "every frame that likens one thing to another ... and any other"; the earlier note
# that bare "like a X" and "as if" were left to HV7 had no ruling behind it. An empty
# "like something" simile is also an HV7 fault (and HV9-GENERIC here).
# the human ruled o3:T13, 2026-10-10 14:01: "the same shape at most 2 times per
# script, and no cap on the total while the shapes differ and every image is
# specific." So HV9-SHAPE-REPEAT warns on a shape's 3rd use and there is no total
# limit. It stays a warning, never a finding.
HV9_SHAPE_MAX = 2  # the most uses of one shape allowed in a script (o3:T13 ruling)
# "the way" as a route, a direction or a degree, not a comparison.
_HV9_NOT_AFTER = (r"through|thru|down|up|to|out|over|into|onto|back|home|across|along|"
                  r"in|inside|there|here|forward|on|off|around|round|past|toward|towards|"
                  r"ahead|is|was|opens|lies|of|from|before|behind|below|beneath|under|"
                  r"between|forth|for|and|or|but|without|shows|clear|now|then")
_HV9_NOT_BEFORE = re.compile(
    r"\b(?:all|on|by|in|of|along|across|love|loves|loved|loving|notice|notices|"
    r"noticed|noticing|enjoy|enjoys|appreciate|appreciates|lead|leads|led|leading|show|shows|showed|"
    r"showing|find|finds|found|finding|make|makes|made|making|clear|clears|cleared|"
    r"clearing|pave|paves|paved|paving|halfway|half|most|part|light|lights|lit|"
    r"lighting|point|points|pointed|pointing|give|gives|gave|giving|lose|loses|"
    r"lost|losing|know|knows|knew|forgot|forget|forgets|block|blocks|blocked|"
    r"wend|wends|wended|wending|wind|winds|winding|into|about|change|changes|changed|"
    r"changing|like|likes|liked|trust|trusts|trusted)\s+$", re.I)
HV9_FRAME = re.compile(
    r"\bthe\s+way\s+(?!(?:%s)\b)[A-Za-z']+(?:[ ]+[A-Za-z']+){0,3}" % _HV9_NOT_AFTER,
    re.I)


# the human, 2026-10-10 13:39 (o9:W175 note n1; box "EVERY COMPARISON MUST NAME A
# SPECIFIC IMAGE" in SCRIPT-CHECKLIST-4-VOICE.md): (1) a comparison to something
# generic is banned; (2) no one comparison SHAPE may repeat through a script, and
# comparisons count in every form. These are the other shapes the box names. Each
# is (shape name, regex); the regex ends on the compared subject (up to five
# words) so the generic-subject test can read it. All stay WARNINGS.
_HV9_TAIL = r"[A-Za-z']+(?:[ ]+[A-Za-z']+){0,4}"
HV9_OTHER_SHAPES = (
    # "just like that" is the idiom for "suddenly", not a comparison.
    ("just like", re.compile(r"\bjust\s+like\s+(?!that\b)" + _HV9_TAIL, re.I)),
    # Only "just as a/an ..." (a temporal "just as the sun sets" is left alone).
    ("just as", re.compile(r"\bjust\s+as\s+(?=an?\b)" + _HV9_TAIL, re.I)),
    ("the same way", re.compile(
        r"\b(?:in\s+(?:exactly\s+|precisely\s+)?)?the\s+same\s+way\s+(?:that|as)\s+"
        + _HV9_TAIL, re.I)),
    # A short list on purpose: "as still as the room" (an adjective simile) is
    # not this shape and must stay uncounted.
    ("as completely as", re.compile(
        r"\bas\s+(?:completely|fully|surely|certainly|totally|entirely)\s+as\s+"
        + _HV9_TAIL, re.I)),
    # o9:W221: "as if" / "as though" (always a comparison).
    ("as if", re.compile(r"\bas\s+(?:if|though)\s+" + _HV9_TAIL, re.I)),
    # o9:W221: a "like" simile. The regex finds every "like" followed by a word; _hv9_like_ok()
    # then drops the uses that are not comparisons (see its comment).
    ("like", re.compile(r"(?<![-\w'])like\s+(?=[A-Za-z])" + _HV9_TAIL, re.I)),
)

_HV9_LIKE_SUBJ = frozenset("""i you we they he she who people d ll would wouldnt do dont doesnt did
didnt will wont might may can cant could couldnt should shouldnt to""".split())
_HV9_LIKE_ADV = frozenset("""really truly also always only simply genuinely actually even never
still quite particularly especially rather both most best much""".split())
_HV9_LIKE_FEEL = frozenset("feel feels felt feeling".split())
_HV9_LIKE_SEEM = frozenset("seem seems seemed seeming".split())
_HV9_LIKE_CLAUSE = frozenset("""i it they we you he she there this that these those what how""".split())
_HV9_LIKE_DEMO = frozenset("this that these those".split())
_HV9_NOT_GERUND = frozenset("""morning evening something anything nothing everything ceiling
building painting wedding string ring king wing swing spring sing thing during""".split())
_HV9_LIKE_SAID = frozenset("said mentioned say told".split())
# o9:W222 (2026-10-15): "more like" + a self or identity phrase ("more like my best self",
# "more like myself", "more like the person I am") likens the listener to themselves, not one
# thing to another, so it is not counted. This is o5's reading of the HV9 box, asked through
# o9:W222; it is not a ruling from the human. Only these self forms are excluded: "more like a
# stone" and "like my mother's hands" still count. "much/a little/even more like" end in
# "more like", so they are covered by the same previous-word test.
_HV9_LIKE_SELF = re.compile(
    r"^(?:myself|me|my\s+(?:best\s+|own\s+|true\s+)?self|the\s+person\s+i\s+am|who\s+i\s+am"
    r"|the\s+self\s+i|the\s+one\s+i\s+am)\b", re.I)
_HV9_LIKE_NUMBER = re.compile(r"^(?:\d[\d,.]*|one|two|three|four|five|six|seven|eight|nine|ten|"
                              r"twenty|thirty|forty|fifty|hundred|a|an)$")


def _hv9_like_ok(spoken, mo):
    """True when this "like" match is a simile; False when it is not a comparison (o9:W221).

    Not counted: the verb ("I like", "you'd like", "would like", "really like"); "feel like"
    + a gerund or a pronoun clause ("feel like sinking", "feels like it will", "feel like I");
    "seem like"; "like this/that/these/those"; "like to" (verb); "like I said"; "something
    like" meaning "about" (+ a number, this, that); "just like" (its own shape). Everything
    else that is "like" + a word counts: "like a/the ...", "like my ...", "like warm water".
    """
    before = spoken[max(0, mo.start() - 40):mo.start()].replace("’", "'")
    pm = re.search(r"((?:[A-Za-z']+\s+){0,2})$", before)
    prev = [w.lower() for w in (pm.group(1).split() if pm else [])]
    after = [w.lower().replace("’", "'") for w in mo.group(0).split()[1:]]
    p1 = prev[-1].replace("'", "") if prev else ""
    if prev and prev[-1].endswith(("'d", "'ll")):
        return False
    n1 = after[0] if after else ""
    n1b = n1.split("'")[0]
    if p1 == "just" or p1 == "the":
        return False
    if p1 == "more" and _HV9_LIKE_SELF.match(" ".join(after).replace("’", "'")):
        return False
    if p1 in _HV9_LIKE_SUBJ or p1 in _HV9_LIKE_ADV or p1 in _HV9_LIKE_SEEM:
        return False
    if p1 in _HV9_LIKE_FEEL and (
            n1b in _HV9_LIKE_CLAUSE
            or (n1.endswith("ing") and len(n1) > 4 and n1 not in _HV9_NOT_GERUND)):
        return False
    if n1 in _HV9_LIKE_DEMO or n1 == "to":
        return False
    if n1 == "i" and len(after) > 1 and after[1] in _HV9_LIKE_SAID:
        return False
    if n1 == "you" and len(after) > 1 and after[1] in _HV9_LIKE_SAID:
        return False
    if p1 in ("something", "anything") and (_HV9_LIKE_NUMBER.match(n1) and n1 not in ("a", "an")):
        return False
    return True


# GENERIC SUBJECT (box rule 1). A frame is generic when the thing it compares to
# is an indefinite pronoun or a vague noun phrase: something, anything, someone,
# a thing, a warm thing, a body, a person. Matched at the START of the text after
# the frame's opening words. SMALL ON PURPOSE: a false negative costs nothing (it
# is a warning), a false positive tells a writer to fix a good image. "a body of
# water" is a specific image and is excluded. "someone/somebody/anyone/anybody"
# followed by who/whom/that/used to ("someone who loved me") is a personal memory
# image, not a generic one, and is excluded. Judging whether a named image is
# specific enough is not mechanizable and stays with the reviewer.
HV9_GENERIC_SUBJECT = re.compile(
    r"^(?:something|anything"
    r"|(?:someone|somebody|anyone|anybody)(?!\s+(?:who|whom|that|used\s+to)\b)"
    r"|an?\s+(?:[a-z]+\s+)?(?:thing|person)"
    r"|a\s+(?:[a-z]+\s+)?body(?!\s+of\b))\b", re.I)
_HV9_OPENERS = re.compile(
    r"^(?:the\s+way|just\s+like|just\s+as|like|as\s+(?:if|though)|(?:in\s+(?:exactly\s+|precisely\s+)?)?the\s+same"
    r"\s+way\s+(?:that|as)|as\s+\w+\s+as)\s+", re.I)


def comparison_frames(spoken):
    """[(shape, phrase)] - each comparison frame in the text (HV9). Advisory.

    Shape "the way": "the way <subject> <verb>", with or without a comma before
    it. The verb is not parsed; a frame is "the way" plus a following word that
    is not a route or degree word (through, down, home, out, ...) and not
    preceded by a phrase that makes it a route ("all the way", "on the way",
    "lead the way", "find the way"). Other shapes: HV9_OTHER_SHAPES. Up to five
    words are kept for the report.
    """
    return [(shape, phrase) for _, _, shape, phrase in comparison_frame_spans(spoken)]


def comparison_frame_spans(spoken):
    """[(start, end, shape, phrase)] - comparison_frames() with each match's span, in order."""
    hits = []
    for mo in HV9_FRAME.finditer(spoken):
        if _HV9_NOT_BEFORE.search(spoken[max(0, mo.start() - 20):mo.start()]):
            continue
        # "the way perfectly ...": an adverb straight after "the way" is a
        # manner phrase, not a "the way <subject> <verb>" frame.
        if mo.group(0).split()[2].lower().endswith("ly"):
            continue
        hits.append((mo.start(), mo.end(), "the way", re.sub(r"\s+", " ", mo.group(0))))
    for shape, rx in HV9_OTHER_SHAPES:
        for mo in rx.finditer(spoken):
            if shape == "like" and not _hv9_like_ok(spoken, mo):
                continue
            hits.append((mo.start(), mo.end(), shape, re.sub(r"\s+", " ", mo.group(0))))
    hits.sort()
    return hits


def comparison_frame_hits(spoken):
    """[phrase] - every comparison frame, any shape (HV9). Advisory."""
    return [phrase for _, phrase in comparison_frames(spoken)]


def generic_frame(phrase):
    """True when the frame's compared subject is generic (HV9_GENERIC_SUBJECT)."""
    return bool(HV9_GENERIC_SUBJECT.match(_HV9_OPENERS.sub("", phrase, count=1)))


HV9_OPTION_SET_MAX = 2  # the most comparisons (all shapes) in one dropdown (the human, 2026-10-10 17:09 PT)
_HV9_DD_TOKEN = "zqhv9ddzq%dzq"
_HV9_EDGE_CHARS = 80  # spoken text read either side of a dropdown (a frame is at most ~6 words)


def comparison_playback(raw):
    """(shapes, option_sets) - HV9 measured per PLAYBACK (the human, 2026-10-10 17:09 PT:
    "'one playback' - yes, but with a maximum of 2 comparisons in any one option set.").

    shapes: {shape: (worst_count, [phrases of that worst case])}. One listener hears
    the text outside the dropdowns, plus ONE option from each dropdown; the worst case
    takes, per shape, the option of each dropdown with the most uses of that shape, so
    uses in different options of one dropdown never add up.
    option_sets: [(n, total, [phrases])] - each dropdown (numbered in order, from 1)
    with every comparison in all its options, any shape. A frame crossing into the
    dropdown from the text before it counts ONCE in that total, however many options
    it reaches (its phrase from the first option that reaches it): only comparisons
    written inside the options add up (o3 ruling, 2026-10-10: the human's limit is on how
    many comparisons a dropdown offers, and changing the subject offers no new one).
    Dropdowns are found by DROPDOWN on the same body render_all_options() reads; a
    dropdown on a line spoken_prose() drops (a list item, a **Label:** field) is not
    counted, as it was not before.

    ⛔ A FRAME THAT CROSSES A DROPDOWN EDGE (lane o9L1239): "the way [[I | we]] breathe"
    is heard as "the way I breathe", so each option is also read between the
    _HV9_EDGE_CHARS characters of spoken text either side of its dropdown (other
    dropdowns there are barriers). A frame belongs to the option when it starts inside
    the option, or starts before it, reaches into it, and is not already found in the
    outside text at that point (so a complete frame just before a dropdown is not counted
    twice). The old all-options render counted such a frame once, from option 1 only.
    Measured 2026-10-10 on script-library/: no script has one. Reading the words before a
    dropdown also applies _HV9_NOT_BEFORE to an option's first words: "I trust [[... |
    the way this unfolds]]" is not a comparison, and is no longer counted as one.
    """
    sets = []

    def take(m):
        sets.append(m.group(1))
        return " " + (_HV9_DD_TOKEN % (len(sets) - 1)) + " "

    spoken = spoken_prose(raw, lambda t: DROPDOWN.sub(take, _render_body(t)))
    tokens = list(re.finditer(r"zqhv9ddzq(\d+)zq", spoken))
    outside = re.sub(r"zqhv9ddzq\d+zq", " . ", spoken)
    base = {}
    for shape, phrase in comparison_frames(outside):
        base.setdefault(shape, []).append(phrase)
    per_set = []   # for each dropdown: one {shape: [phrases]} per option
    option_sets = []
    for n, tok in enumerate(tokens, 1):
        body = sets[int(tok.group(1))]
        pre = re.sub(r"zqhv9ddzq\d+zq", " . ", spoken[:tok.start()])[-_HV9_EDGE_CHARS:]
        post = re.sub(r"zqhv9ddzq\d+zq", " . ", spoken[tok.end():])[:_HV9_EDGE_CHARS]
        pre_starts = {s for s, _, _, _ in comparison_frame_spans(pre + " . ")}
        opts = []
        every = []
        crossed = set()  # starts of frames crossing into this dropdown, counted once
        for opt in body.split("|"):
            clean = re.sub(r"\s+", " ", re.sub(
                r"\*\*|\*|~~", " ", _CODE_SPAN.sub(" ", PAUSE.sub(" ", opt)))).strip()
            a = len(pre) + 1
            b = a + len(clean)
            by = {}
            for s, e, shape, phrase in comparison_frame_spans(pre + " " + clean + " " + post):
                own = a <= s < b
                crossing = s < a < e and s not in pre_starts
                if own or crossing:
                    by.setdefault(shape, []).append(phrase)
                    if own or s not in crossed:
                        every.append(phrase)
                    if crossing:
                        crossed.add(s)
            opts.append(by)
        per_set.append(opts)
        option_sets.append((n, len(every), every))
    shapes = {}
    for shape in set(base) | {sh for opts in per_set for by in opts for sh in by}:
        phrases = list(base.get(shape, []))
        for opts in per_set:
            phrases += max((by.get(shape, []) for by in opts), key=len, default=[])
        shapes[shape] = (len(phrases), phrases)
    return shapes, option_sets


def run_process_hits(spoken):
    """[(phrase, why)] where `run` is a process being EXECUTED. Advisory only.

    Silent on a machine working and on something travelling along a path -
    the human's two allowed senses. See the block comment above for the test.
    """
    tokens = [(m.start(), m.end(), m.group(0)) for m in _WORD.finditer(spoken)]
    hits = []
    for i, (s, e, w) in enumerate(tokens):
        lw = w.lower()
        if lw not in ("run", "runs", "running", "ran"):
            continue
        # "a run", "the long run", "my morning run" - the noun, not the verb.
        if lw == "run" and any(tokens[j][2].lower() in _RUN_NOUN_DET
                               for j in range(max(0, i - 2), i)):
            continue
        # "a running tally", "the running total" - the adjective. Only the
        # token IMMEDIATELY before counts, so "the mind running the show"
        # still fires.
        if lw == "running" and i and tokens[i - 1][2].lower() in _RUN_NOUN_DET:
            continue
        # A machine subject anywhere in the six tokens before it.
        if any(tokens[j][2].lower() in _RUN_MACHINE
               for j in range(max(0, i - 6), i)):
            continue

        allowed = None
        obj_head = None
        obj_j = None
        prev_end = e
        for j in range(i + 1, min(len(tokens), i + 7)):
            if _run_break(spoken[prev_end:tokens[j][0]]):
                break
            nw = tokens[j][2].lower()
            prev_end = tokens[j][1]
            if nw in _RUN_CLAUSE_END:
                break
            if nw in _RUN_DIRECTION:
                allowed = "direction"
                break
            if nw in _RUN_STATE:
                allowed = "state"
                break
            if nw in _RUN_EXTENT:
                allowed = "extent"
                break
            if nw.endswith("ly") and nw not in _LY_NOT_ADVERB:
                continue
            if nw in _RUN_SKIP_FWD:
                continue
            # The FIRST content word after the verb decides the sense. Do not
            # keep scanning past it: "runs my sleep came in here" would find
            # the directional "in" four words later, in a different phrase,
            # and read an operated object as travel. Measured 2026-09-04.
            obj_head = nw
            obj_j = j
            break
        if allowed:
            continue

        if obj_head is not None:
            if obj_head in _RUN_ORDINARY_OBJECT:
                continue
            if obj_head in _RUN_REFLEXIVE and obj_j is not None:
                nxt = tokens[obj_j + 1][2].lower() if obj_j + 1 < len(tokens) else ""
                if (nxt in _RUN_REFLEXIVE_PARTICLE
                        and not _run_break(spoken[tokens[obj_j][1]:
                                                  tokens[obj_j + 1][0]])):
                    continue
                # "my breathing runs itself" - a body process running by itself
                # (o8:D20). The subject sits just ahead of the verb, same clause.
                body_subject = False
                back_end = s
                for j in range(i - 1, max(-1, i - 4), -1):
                    if _run_break(spoken[tokens[j][1]:back_end]):
                        break
                    back_end = tokens[j][0]
                    if tokens[j][2].lower().split("'")[0] in _RUN_BODY_PROCESS:
                        body_subject = True
                        break
                if body_subject:
                    continue
            why = "operates the object '%s'" % obj_head
        else:
            # Nothing followed it. Banned only when the object was fronted and
            # somebody is doing the operating: "the order I always run".
            # Same clause only, and near. Reaching five tokens back across a
            # sentence boundary matched the "you" of the PREVIOUS sentence in
            # "tell you so without hesitating.. Steadiness runs so far into
            # me" (measured 2026-09-04). A fronted object puts its subject
            # right next to the verb: "the order I always run".
            found = False
            ordinary = False
            back_end = s
            for j in range(i - 1, max(-1, i - 4), -1):
                if _run_break(spoken[tokens[j][1]:back_end]):
                    break
                back_end = tokens[j][0]
                bw = tokens[j][2].lower().strip("'").split("'")[0]
                # The fronted object is in this same clause, just ahead of the
                # subject: "the best SHIFT I run" is a thing people run.
                if bw in _RUN_ORDINARY_OBJECT:
                    ordinary = True
                    break
                if bw in _RUN_OPERATOR:
                    found = True
            if ordinary or not found:
                continue
            why = "no object follows - the object is fronted"

        start = tokens[max(0, i - 6)][0]
        stop = tokens[min(len(tokens) - 1, i + 4)][1]
        hits.append((spoken[start:stop].replace("\n", " "), why))
    return hits


# ==========================================================================
# HV6 - THE SAME SPOKEN PHRASE IN TWO DIFFERENT SCRIPTS
# ==========================================================================
# ⛔ MEASURED 2026-09-04 by lane o8L128 while writing a new AT script: its first
# complete draft carried 25 overlaps of seven words with AT-03 across nine distinct
# phrases, INCLUDING WHOLE FORMULA TAILS - and the author noticed none of them.
# All 25 were found by a throwaway script afterwards. `HV6` has forbidden this
# since the checklist existed and nothing screened for it.
#
# ⭐ THE FAILURE IS INVISIBLE TO THE AUTHOR BY CONSTRUCTION. The reference script
# is in working memory precisely BECAUSE it was the model, so its phrasings arrive
# feeling like the right way to say the thing. An author who had just read the
# rule produced 25 hits and caught zero. That is why this must be mechanical.
#
# WHY 10 (7 until the human's ruling of 2026-10-10 13:58, o3:T14 item B, which made a shared phrase a run
# of 10 or more words). The first measurement (o8L128, 2026-09-04) used 7: the six canonical
# autogenic formulas - which two AT scripts SHOULD share, because they are the METHOD - are 3 to 6
# tokens long, so at any n of 7 or more they cannot produce a gram at all and the largest
# false-positive class is excluded by the LENGTH, not by a list someone has to maintain. 7 words in
# a row is already far past coincidence in ordinary English; 10 is the human's chosen length for a phrase
# worth a note. Only ONE canonical formula is long enough to need an allowlist entry, and it is
# listed below. "When a check fires on text that FOLLOWS the rules, the check is wrong, not the text."
# (check 10 of check_checklist_router.py keeps its own length, WAYPOINT_N = 7.)
REUSE_N = 10
#
# BLOCKING, NOT ADVISORY (reviewed 2026-10-03 for o3:W5, lane o3L15, and kept).
# The brief defaulted to advisory; this stays a finding because (1) HV6 is a hard
# rule ("two scripts must not share body lines"), not a soft ban like BW7; (2) the
# J13 and F9 evidence is that authors do not see these collisions and a warning
# is exactly what gets read past (25 and 43 overlaps, zero noticed); (3) it has
# been a finding since 2026-09-04 and o5's shared gate relies on that, so making
# it advisory would weaken a check other orchestrators already use; and (4) a
# false positive costs one glance, and versions of one script are already
# exempted by family_stem() and declared `## Source:` lines.

# The canonical Schultz formulas. Shared across AT scripts BY DESIGN - the method
# is the repetition. Any gram lying wholly inside one of these is not a defect.
CANONICAL_AT_FORMULAS = [
    "my right arm is heavy",
    "my left arm is heavy",
    "my arms and legs are heavy",
    "my arms and legs are warm",
    "my arms and legs are heavy and warm",
    "my heartbeat is calm and regular",
    "my heartbeat is calm and steady",
    "my breathing is calm and regular",
    "it breathes me",
    "my solar plexus is warm",
    "my forehead is cool",
    "i am completely calm",
    "i am at peace",
]

_PROSE_FIELD = re.compile(r"^\*\*[^*:]{1,40}:\*\*")
_PROSE_LIST = re.compile(r"^\s*(?:[-*+]\s|\d+[.)]\s)")
_CODE_SPAN = re.compile(r"`[^`]*`")


def render_all_options(text, sep=". "):
    """default_render(), but EVERY dropdown option kept.

    default_render() resolves [[a | b | c]] to option `a`, which is correct
    for LENGTH - one listener hears one render - and wrong for a WORDING
    screen, because options b and c are spoken to somebody too.

    Measured 2026-09-04: two of the five BW7 hits in
    AT-S5-portable-induction-PILOT.md sit in options 3 and 4, invisible to
    the default render. Options are joined with ". " so no phrase can be
    read across the boundary between two of them.

    Used by BW7 only. The older screens keep reading default_render(), for
    the reason spoken_prose() gives: widening an existing screen's input in
    the same commit that adds a new one makes it impossible to tell which
    change moved which number.
    """
    return DROPDOWN.sub(
        lambda m: sep.join(o.strip() for o in m.group(1).split("|")), _render_body(text))


def _render_body(text):
    """The body lines render_all_options() keeps, dropdowns not yet resolved."""
    if has_marker(text):
        text = split_marker(text)[1]
    keep = []
    for line in text.split("\n"):
        s = line.strip()
        if not s:
            keep.append("")
            continue
        if s.startswith("#") or s.startswith(">") or s.startswith("<!--"):
            continue
        if s.startswith("|") or set(s) <= set("-|: "):
            continue
        keep.append(s)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(keep)).strip()


# ⛔ EVERY OPTION IN ITS PLACE, NOT ONLY OPTION 1 (lane o9L1184, 2026-10-09, from o5's report on
# b986672c). render_all_options() puts a menu's options in a row with a barrier between them:
# "put it toward OPT1 | OPT2 | OPT3 every day". Only option 1 touches the words before the menu
# and only the last option touches the words after it, so a phrase a member hears as "put it
# toward OPT4" was never a 10-gram, and HV6-REUSE missed D-01-011's "put it toward the work that
# matters" (option 4 of a menu in D-02-014) and D-04-001's "My body knows exactly how to [[...]]"
# (a later option). render_options_in_context() reads option 1 in place, and adds each later
# option once more between the CONTEXT_WORDS words before the menu and the CONTEXT_WORDS words
# after it on the same line (other menus there read as their option 1), behind a barrier.
# Nine words is the most a 10-gram can borrow from either side and still hold one option word, so
# no gram made only of context words is counted twice. test_check_script_reuse_menu_options.py.
CONTEXT_WORDS = REUSE_N - 1
_CTX_TOKEN = re.compile(r"[A-Za-z0-9'’]+|[.!?]+")


def _first_option(m):
    return m.group(1).split("|")[0].strip()


def _edge_words(s, n, tail):
    """The last (tail=True) or first n words of s, with sentence stops kept between them."""
    s = _CODE_SPAN.sub(" ", PAUSE.sub(" ", DROPDOWN.sub(_first_option, s)))
    toks = _CTX_TOKEN.findall(s)
    if tail:
        toks = toks[::-1]
    out, words = [], 0
    for t in toks:
        if t[0] not in ".!?":
            if words == n:
                break
            words += 1
        out.append(t)
    if tail:
        out = out[::-1]
    return " ".join(out)


def render_options_in_context(text, sep=". "):
    """The body with each menu read as its option 1 in place, and each later option appended to
    its line between the words around the menu, every addition behind `sep` (see the block above)."""
    lines = []
    for line in _render_body(text).split("\n"):
        menus = list(DROPDOWN.finditer(line))
        if not menus:
            lines.append(line)
            continue
        extra = []
        for m in menus:
            opts = [o.strip() for o in m.group(1).split("|")]
            if len(opts) < 2:
                continue
            pre = _edge_words(line[:m.start()], CONTEXT_WORDS, tail=True)
            post = _edge_words(line[m.end():], CONTEXT_WORDS, tail=False)
            for o in opts[1:]:
                extra.append(" ".join(x for x in (pre, o, post) if x))
        lines.append(DROPDOWN.sub(_first_option, line) + "".join(sep + e for e in extra))
    return "\n".join(lines)


def spoken_prose(raw, render=None, line_mark=None):
    """The body a MEMBER HEARS - planning lines removed. `line_mark`, when given, is put after each
    kept line that does not end in a comma, semicolon or colon (HV6-REUSE rule (a): a line break ends
    a sentence, o3:W35 2026-10-10); every other caller gets the lines joined by spaces, as before.

    default_render() keeps every non-heading line, and several corpus files put
    their PLANNING under the header marker (word-banks, imagery palettes,
    per-formula design notes). Two line classes are therefore dropped here:
    markdown list items, and `**Label:** ...` metadata fields.

    Measured 2026-09-04: the field line "**Format:** self-coaching -
    first-person, present tense..." is byte-identical across 16 files and
    produced more colliding 10-grams than every real reuse defect combined; and
    comma-separated word-bank lines ("hands, carried, sink, arrives") produced
    five phantom arrival-verb subjects. A spoken statement never opens with
    "- " or with a bolded label.

    ⛔ WAS SCOPED TO THE TWO NEW SCREENS; WIDENED 2026-09-22 ON THE HUMAN'S DIRECTION.
    The original scoping was right for its own commit - widening an existing
    screen's input while adding two checks makes it impossible to tell which
    change moved which number. That reason expired once the two checks had
    settled. Every WORDING screen now reads all options; the LENGTH screens
    still read one, because a listener hears one.
    ⚠️ "Every" was not true when first written: HV6-REUSE (reuse_tokens) was
    missed and read option 1 until 2026-09-23. A claim about the whole set,
    made from the five screens that were changed.
    """
    body = (render or default_render)(raw)
    body = PAUSE.sub(" ", body)
    body = _CODE_SPAN.sub(" ", body)
    keep = []
    for ln in body.split("\n"):
        s = ln.strip()
        if not s or _PROSE_LIST.match(s) or _PROSE_FIELD.match(s):
            continue
        if line_mark and not _LINE_JOINS.search(s):
            s = "%s %s" % (s, line_mark)
        keep.append(s)
    return re.sub(r"\*\*|\*|~~", " ", " ".join(keep))


# A spoken line ending in a comma, semicolon or colon (before closing quotes, brackets or emphasis
# marks) runs on into the next line: the line break does not end its sentence (o3:W35, 2026-10-10).
_LINE_JOINS = re.compile(r"[,;:][\"'\)\]\*_~’”]*$")


_OPT_BREAK = "zqoptbreakzq"   # never occurs in prose; mapped to _BARRIER


def reuse_tokens(raw, stops=False):
    """spoken_prose() as lowercase word tokens, canonical formulas MASKED.

    ⭐ MASKING, not filtering. A canonical formula is replaced in place by a
    barrier token, so no gram can span it and no gram can be built out of it.
    Filtering whole grams instead lets a formula plus one stopword sneak
    through as a "shared phrase": "my heartbeat is calm and regular a" and
    "my forehead is cool and clear and" both fired that way before this, and
    both are the METHOD with a comma's worth of noise attached.
    """
    # ⛔ ALL OPTIONS, per W39 (the human, 2026-09-22: wording screens read every option).
    # Reuse is a WORDING screen - a phrase copied into option 4 ships exactly like one
    # in option 1. This was the one W39 missed while its docstring claimed all of them;
    # the SC-02 process-test lane found it when its draft passed clean with option-level
    # phrases shared with 23 files, three of them the human's own rewrites.
    # ⛔ A SENTINEL BETWEEN OPTIONS, not ". ". The tokenizer below discards
    # punctuation, so a full stop cannot stop a 10-gram forming from the end of
    # one option and the start of the next - words no listener ever hears
    # together. Found by the AT-S10a lane the same day HV6 was widened.
    # Each option after the first is also read between the words around its menu
    # (render_options_in_context, o9L1184): in a row, options 2 and on touched no context.
    # A curly apostrophe is the same word as a straight one, so it is normalised
    # before tokenising (o3:W5: normalise case and punctuation; the tokenizer
    # already drops every other punctuation mark).
    # `stops` is True only for HV6-REUSE's index (_build_reuse_index). The waypoint check
    # in check_checklist_router.py reads the same words with stops=False, and its test
    # pins that a rule-file quote runs on across "..." and "!" (test (d)).
    # ⛔ A SENTENCE-ENDING STOP IS A BARRIER TOO (o3:W34, lane o9L1154). The tokenizer
    # drops punctuation, so "...going to rest. Warmth comes..." formed grams across the
    # stop: words a listener hears with a sentence break between them. A full stop,
    # question mark, exclamation mark or ellipsis followed by white space or the end
    # of the text becomes the same sentinel as an option boundary ("2.5" is untouched).
    # Options in context only for HV6-REUSE (stops=True). The waypoint check keeps reading the
    # options in a row: its baseline of known hits was measured that way (W152).
    words = [_BARRIER if w == _OPT_BREAK else w for w in _reuse_words(raw, stops)]
    for formula in _CANON_SEQS:
        k = len(formula)
        i = 0
        while i <= len(words) - k:
            if words[i:i + k] == formula:
                words[i:i + k] = [_BARRIER] * k
                i += k
            else:
                i += 1
    return words


def _reuse_words(raw, stops, lines=False):
    """reuse_tokens() before the canonical formulas are masked: the words, with _OPT_BREAK (not yet
    _BARRIER) at each option boundary and, when `stops`, at each sentence stop. Masking replaces a
    formula word for word, so index i here is index i of reuse_tokens(). `lines` (for
    script_sentence_ids only) also puts _LINE_BREAK after each spoken line that ends a sentence;
    dropping those tokens gives exactly the words without `lines`."""
    render = render_options_in_context if stops else render_all_options
    text = spoken_prose(raw, lambda s: render(s, sep=" %s " % _OPT_BREAK),
                        line_mark=_LINE_BREAK if lines else None)
    if stops:
        text = re.sub(r"[.!?]+[\"')\]*_]*(?=\s|$)", " %s " % _OPT_BREAK, text)
    words = re.findall(r"[a-z0-9']+", text.lower()
        .replace("’", "'").replace("‘", "'"))
    while words and words[-1] in (_OPT_BREAK, _LINE_BREAK):   # a final stop needs no barrier after it
        words.pop()
    return words


_LINE_BREAK = "zqlinebreakzq"   # never occurs in prose; a sentence break for rule (a) only, never a gram barrier


# ⛔ A SENTENCE, FOR HV6-REUSE's SCALE RULE (o3:W35 severity, the human 2026-10-09 23:14 and 23:16; lane
# o9L1193). A sentence is the run of words between two of the breaks reuse_tokens(stops=True) already
# puts a barrier at: a full stop, question mark, exclamation mark or ellipsis followed by white space
# or the end of the text, and the boundary between two menu options (each option is read between the
# words around its menu). A masked canonical formula is NOT a break: it sits inside its sentence.
# Two breaks with no word between them make no empty sentence, so "Rest... Sleep." is two sentences
# in a row. No 10-gram crosses a break, so each shared gram lies inside exactly one sentence.
#
# ⛔ A LINE BREAK ENDS A SENTENCE TOO, for rule (a) only (o3:W35, answer to o10L350 SF-C, 2026-10-10;
# lane o9L1208), unless the line ends in a comma, semicolon or colon. Two copied spoken lines with no
# end stop between them were one sentence, so rule (a) never fired on them. A line break is NOT a gram
# barrier: the 10-grams, phrases, rule (b) and the baseline rows are unchanged. So a gram can now cross a
# sentence break at a line end; such a gram lies in no one sentence and counts toward rule (a) for
# neither sentence (sentence_of_gram), as a gram across a full stop could never form at all.
def script_sentence_ids(text):
    """[sentence number] for each index of script_reuse_tokens(text, stops=True); a break index gets
    the number of the sentence before it. A break is a stop or option boundary (a gram barrier) or the
    end of a spoken line that does not end in a comma, semicolon or colon."""
    words = _reuse_words(strip_comments(text), True, lines=True)
    out, n, prev_break = [], 0, True
    for w in words:
        if w in (_OPT_BREAK, _LINE_BREAK):
            if not prev_break:
                n += 1
            prev_break = True
            if w == _OPT_BREAK:
                out.append(n - 1)
        else:
            prev_break = False
            out.append(n)
    return out


def sentence_of_gram(sent_ids, i):
    """The sentence the 10-gram starting at word i lies in, or None when it crosses a line-end break."""
    s = sent_ids[i]
    return s if sent_ids[i + REUSE_N - 1] == s else None


def script_reuse_tokens(text, stops=False):
    """The words HV6-REUSE compares for a script: reuse_tokens() of its text once the human's
    two-brace comments, `{{the human's comment: ...}}`, are taken out (script_notes.strip_comments).
    `text` has had its {{{ }}} notes removed already (read_script does that).

    ⛔ ONE FUNCTION FOR BOTH CHECKS (o9 W152, lane o9L1062, 2026-10-05). A comment is the human's
    ruling to the writer, and nobody speaks it. The waypoint check in check_checklist_router.py
    left comments out from the day it was built (afd200de), while HV6-REUSE still counted them,
    so two scripts whose only shared run of 10 words sat inside the human's comments were reported as
    sharing a phrase. That check now reads a version file through this function too, so the
    two cannot disagree about which words a script holds.
    """
    return reuse_tokens(strip_comments(text), stops)


# Longest first, so "my arms and legs are heavy and warm" masks before the
# shorter "my arms and legs are heavy" can claim half of it.
_CANON_SEQS = sorted((f.split() for f in CANONICAL_AT_FORMULAS),
                     key=len, reverse=True)
_BARRIER = "\x00"


def _grams(tokens, n=REUSE_N):
    return [" ".join(tokens[i:i + n]) for i in range(len(tokens) - n + 1)
            if _BARRIER not in tokens[i:i + n]]


def _gram_positions(tokens, n=REUSE_N):
    """[(index, gram)] - index is needed to merge runs back into phrases."""
    return [(i, " ".join(tokens[i:i + n]))
            for i in range(len(tokens) - n + 1)
            if _BARRIER not in tokens[i:i + n]]


_FAM_DECOR = ("pilot", "draft", "rewrite", "demo", "toolkit", "deoveruse",
              "mirrored", "checklist", "script", "final", "copy")


def family_stem(path):
    """A version-insensitive stem, so v1/v2/v3 of ONE script are one family."""
    s = os.path.basename(path)
    # o10L321 (review o10L318 H2): a library text is `<NAME> v<N> <AUTHOR>[ (in app)].md`, and every version of one
    # script, the text in the app and a human rewrite share NAME. parse_script_name reads NAME as name_key, so that
    # is the family. Left to the rules below, "v2 o8" and "v3 THE HUMAN" kept words in the stem and split one script
    # into many families.
    pn = training_header.parse_script_name(s)
    if pn.version is not None:
        # W238: the family is the `## Version-Of:` line in the file, not the file name. A script's texts are named by its
        # Label once it has one (a relabel changes the name, never the line), so the name key alone would put the texts
        # of one script in two families, or make a script match itself. A text with no such line keeps its name key.
        key = file_script.header_version_of(path) or pn.name_key
        return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", key.lower())).strip("-")
    # o10L356 (W235 step 7): no library file is named SCRIPT.md any more, and a library text is read through
    # parse_script_name above. A SCRIPT.md is still read in one place: the reuse baseline's source_commit
    # (b877e343) predates the rename, and freeze_rows indexes it. There every final is a SCRIPT.md, and the
    # file name alone would make them all one family "script" with no overlap between them, so the freeze read
    # 4 rows. The folder (SC-Sl-N-01-017-slug) is the script's own name, as before the rename (lane o9L1229).
    if s.lower() == "script.md":
        # W238 (review o10L413 M1): a SCRIPT.md that carries a `## Version-Of:` line is read by that line, as a library
        # text is. Only a SCRIPT.md with no such line (every one in the freeze commit) falls back to its folder name.
        vo = file_script.header_version_of(path)
        if vo:
            return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", vo.lower())).strip("-")
        s = os.path.basename(os.path.dirname(path.replace("\\", "/"))) or s
    elif s.lower().endswith(".md"):
        s = s[:-3]
    s = s.lower()
    # "- the human rewrite" AND "-owner-rewrite": the hyphenated form left "the human" in
    # the stem, so AT-03-PILOT-...-owner-rewrite.md was a different family from
    # the pilot it rewrites (1,495 shared grams reported; found by o3L15).
    s = re.sub(r"[\s-]*owner[\s-]+rewrite.*$", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-v\d+", "-", s)
    for tok in _FAM_DECOR:
        s = re.sub(r"(?:^|-)" + tok + r"(?=-|$)", "-", s)
    return re.sub(r"-+", "-", s).strip("-") or os.path.basename(path).lower()


# A header that NAMES its source is the only non-guessed family signal there is,
# and the corpus uses it constantly ("## Source: .../03-the-helicopter-landing.md").
# It is what links AT-03-PILOT to the featured-corpus script it was written from,
# which no filename rule could see. DECLARED, not inferred - the same standard
# is_daytime() holds to.
# Up to 200 characters (o3L19, 2026-10-03; was 80): an AT-10 pilot is named
# "AT-10-worry-off-ramp-PILOT-I-Can-Take-the-Worry-Off-Ramp-v2-o8-second-half -
# the human rewrite.md", 92 characters, so a Source line naming it matched only a tail
# of the name and linked nothing. Put the name in backticks so the match starts
# exactly at its first character.
_MD_REF = re.compile(r"([A-Za-z0-9][A-Za-z0-9_. \-]{0,200}\.md)")
# A header citing many scripts is a comparison doc, not a version chain. Merging
# on it would silently absorb a whole cluster into one family and hide every real
# collision inside it, so past this many references the declaration is ignored.
_MAX_DECLARED_REFS = 6

# ⛔ THE KNOWN GAP, MEASURED SO IT IS A LIMIT AND NOT A CAVEAT: 7 of 618 corpus
# findings (1.1%) are version relations this cannot see. The five AT-10 pilots
# are rewrites of `10-worry-has-an-off-ramp.md` and NEVER say so - no shared
# filename stem, no `## Source:` line, nothing declared anywhere in the file.
# CLOSED for AT-10 on 2026-10-03 (o3L19): the three non-the human AT-10 versions now
# carry a `## Source:` line naming the version each was made from, and the six
# files plus both copies of the original are one family (measured: the AT-10
# folder's --reuse-report went from 9 colliding pairs to 0).
#
# ⭐ A THIRD SIGNAL WAS BUILT, MEASURED, AND REJECTED. Merging on the H1 title
# closes exactly that gap - and over-merges `idea-01-delegation-handoff.md` into
# `idea-01-high-achievers-rest.md`, two DIFFERENT scripts that share a title
# line. An over-merge is strictly worse than this false positive: a wrong
# collision is one glance to dismiss, a hidden family is a defect that never
# gets reported at all. The fix belongs in the pilots' headers, not here.

_REUSE = None

# --------------------------------------------------------------------------
# W5 (o3, 2026-10-03, lane o3L15) - THE CORPUS HV6-REUSE COMPARES AGAINST
# --------------------------------------------------------------------------
# DEFAULT: every marker-scoped .md script under the workspace (discover()), as
# since 2026-09-04, PLUS the marker-carrying .txt scripts in REUSE_TXT_DIRS.
# The .txt addition exists because 12 of the 15 approved lengthened AT scripts
# are .txt files: discover() reads .md only, so until this change no draft was
# ever compared against the approved AT corpus at all.
#
# `--corpus DIR` (repeatable) REPLACES the default with exactly those folders,
# .md and .txt alike. `--corpus-at-sc` is the preset o3:W5 named: the approved
# AT scripts, the o8 pilots and the new AT scripts (REUSE_AT_SC_CORPUS).
#
# Paths are relative to WORKSPACE. Edit the lists here to change the corpus.
# o10L356 (W235 step 7): the `script-library/3-final/**/versions/AL` entries are gone. The 14 approved AT .txt files that
# were to move there became flat `versions/` .md texts at step 5, so no folder of that name exists and the entries
# found nothing. A root that does not exist is skipped.
REUSE_TXT_DIRS = [
    "research_corpus_1/approved-lengthened",
]
REUSE_VERSION_ORIGINS = ("PIL", "NAT", "NATI")
REUSE_VERSIONS_ROOT = "script-library/**/versions"
# o10 via o9L1268: a script that moves into its live folder under 3-final/ keeps its texts at the TOP of
# <3-final>/**/<AT|SC>-*/ (older texts stay in versions/), which none of the folders below holds. These two
# globs are read at the top level only (discover_corpus, REUSE_TOP_ONLY_ROOTS).
REUSE_FINAL_TOP_ROOTS = (
    "script-library/3-final/**/AT-*",
    "script-library/3-final/**/SC-*",
)
REUSE_TOP_ONLY_ROOTS = frozenset(REUSE_FINAL_TOP_ROOTS)
REUSE_AT_SC_CORPUS = [
    "research_corpus_1/approved-lengthened",
    "script-library/1-drafts/AT",
    "script-library/2-ready-for-owner/AT",
    # o10L321 (W235 step 5): every older text of a script is now one file in a flat <script folder>/versions/,
    # named `<NAME> v<N> <AUTHOR>.md`; the origin (PIL, NAT, NATI) is no longer a folder. The corpus is still only
    # the o8 pilots and the new AT scripts, so this one root is filtered to those origins (REUSE_VERSION_ORIGINS,
    # applied by discover_corpus). Origin comes from the file's `## Origin:` line, else the committed rename map.
    # The SC pilots are SC drafts or ready-for-owner folders.
    REUSE_VERSIONS_ROOT,
    "script-library/1-drafts/SC",
    "script-library/2-ready-for-owner/SC",
    # o10L304 (W235 step 4): drafts and ready scripts with no category now sit in not-yet-placed/<AT|SC>/.
    "script-library/1-drafts/not-yet-placed/AT",
    "script-library/2-ready-for-owner/not-yet-placed/AT",
    "script-library/1-drafts/not-yet-placed/SC",
    "script-library/2-ready-for-owner/not-yet-placed/SC",
] + list(REUSE_FINAL_TOP_ROOTS)
# A file that COLLECTS other scripts is a copy, not a script: comparing against
# it reports every script it holds as reusing itself. This one carries a header
# marker but holds the human's edits of many lengthened scripts in one file.
REUSE_SKIP_FILES = {
    "autogenic scripts - lengthened - owner edited (not all scripts yet).txt",
    # working_corpus_o8/pilots: 986 lines holding several lengthened AT scripts (o10L249 review, Q5)
    "# autogenic descent - guided journey extra.txt",
}


def discover_corpus(roots, exts=(".md", ".txt"), base=None):
    """Marker-carrying scripts under each root (absolute, or relative to `base`, else WORKSPACE).

    Same directory skips as discover(), and like it follows no junction or
    symbolic link; also reads .txt, and drops REUSE_SKIP_FILES. Missing roots are
    skipped, not fatal: a corpus list names folders that a given checkout may not
    have yet.
    """
    found = set()
    expanded = []
    origin_filtered = set()
    top_only = set()
    for r in roots:
        pat = r if os.path.isabs(r) else os.path.join(base or WORKSPACE, r)
        if "*" in pat:
            hits = sorted(d for d in glob.glob(pat, recursive=True) if os.path.isdir(d))
            expanded += hits
            if r == REUSE_VERSIONS_ROOT:
                origin_filtered.update(hits)
            if r in REUSE_TOP_ONLY_ROOTS:
                top_only.update(hits)
        else:
            expanded.append(pat)
    for root in expanded:
        if in_private_folder(root):
            continue
        if os.path.isfile(root):
            if is_script(root):
                found.add(os.path.abspath(root))
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [] if root in top_only else _walk_dirs(dirpath, dirnames)
            for fn in filenames:
                if not fn.lower().endswith(exts) or fn.lower() in REUSE_SKIP_FILES:
                    continue
                p = os.path.abspath(os.path.join(dirpath, fn))
                if root in origin_filtered and training_header.library_origin(p) not in REUSE_VERSION_ORIGINS:
                    continue
                if not is_link(p) and is_script(p):
                    found.add(p)
    return sorted(found)


# ⛔ THE DEFAULT CORPUS IS THE LIBRARY'S SCRIPTS AND NOTHING ELSE (o3:W35 as changed 2026-10-09,
# lane o9L1184). It was every marker-carrying .md in the workspace plus the .txt files in
# REUSE_TXT_DIRS, so loose drafts (`*-DRAFT.txt`), SPEC files and research folders were compared
# too: o9's full run at 02d7f899 found 509 rows, many against files no member hears. Now it is the
# record files under <root>/script-library/ (file_script.record_files): a `<NAME> v<N> <AUTHOR>[ (in
# app)].md` file outside versions/ whose header holds a `## Stage:` line (the name the renamed
# library gives each script's current text), or a SCRIPT.md where one is still left, plus the human's
# rewrites outside versions/ (is_human_text, o3:W35 at 5a825cd5). versions/ and the other SKIP_DIRS
# are skipped as before. --corpus / --corpus-at-sc still name other folders on purpose. A script being checked
# that is not in the corpus is still compared against it (reuse_index_for).
# test_check_script_reuse_corpus.py.
#
# ⛔ ONE DEFINITION OF A RECORD FILE: file_script.record_files (o10L333 SF3, lane o9L1189). This used a
# local copy of file_script._is_record_header until o10's rename landed on main (b281458f). It now
# asks record_files(), so the HV6 corpus and file_script agree on what a record file is: every record
# file in a folder (not only the first), with ` duplicate` copies and the human's rewrites left out. The human's
# rewrites join the corpus through is_human_text, never through record_files.
# test_check_script_reuse_record_header.py checks the corpus equals the record_files set.
def _record_set(folder):
    """The normalised absolute paths of file_script.record_files(folder); empty for versions/ and
    the other NOT_SCRIPT_DIRS, which hold old texts, never a record file."""
    if os.path.basename(os.path.normpath(folder)) in file_script.NOT_SCRIPT_DIRS:
        return set()
    found = {os.path.normcase(os.path.abspath(str(p))) for p in file_script.record_files(folder)}
    # W235 step 7 (558fb834) took SCRIPT.md out of record_files: no library file has that name now. The
    # reuse baseline's source_commit (b877e343) predates the rename, and every record there is a
    # SCRIPT.md, so freeze_rows (and --readd-reuse-rows through it) read 0 rows from it. A SCRIPT.md
    # stays a record text here, as record_files counted it before the rename (lane o9L1229).
    # test_check_script_reuse_baseline_source.py, test_check_script_final_type.py.
    legacy = os.path.join(folder, "SCRIPT.md")
    if os.path.isfile(legacy):
        found.add(os.path.normcase(os.path.abspath(legacy)))
    return found


# ⛔ ONE RECORD-TEXT TEST (lane o9L1208, o10L350 item 1). The branch's is_library_record and o10L344's
# _is_record_text (caa47bc4, used by the UNNAMED check in analyse) asked the same question two ways: the first
# through file_script.record_files, the second by its own name parse plus a `## Stage:|Label:|ID:` line in the first
# 8000 bytes. They agreed on all 1377 .md files in script-library/ at the merge (23f3c8a1). They differed only on
# cases the library does not hold: a header with `## Label:` or `## ID:` but no `## Stage:`, a file with no END
# marker, a ` duplicate` copy, and a human rewrite (not `(in app)`) whose header has a `## Stage:` line. This one
# function keeps the single definition of a record file (record_files, SF3) and adds the one rule of
# _is_record_text that record_files lacks: a human rewrite that is not the app's text is never a record text (it was
# never SCRIPT.md, o10L344). The HV6 corpus still uses _record_set and adds the human's rewrites through is_human_text.
# test_check_script_reuse_record_header.py and test_check_script_final_type.py cover both callers.
def is_library_record(path):
    """True for the record text of its folder: a file_script.record_files file (SCRIPT.md in a tree from
    before W235 step 7, see _record_set, or a `<NAME> v<N> <AUTHOR>[ (in app)].md` file whose header holds a `## Stage:` line above the END marker,
    outside versions/, not a ` duplicate` copy) that is not a human rewrite outside the app (is_human_text)."""
    p = os.path.abspath(path)
    return os.path.normcase(p) in _record_set(os.path.dirname(p)) and not is_human_text(p)


# ⛔ THE HUMAN'S REWRITES: COMPARED AGAINST, NEVER REPORTED (o3:W35 as ruled at 5a825cd5, lane o9L1188).
# A human rewrite that is not the app's text - `<NAME> v<N> THE HUMAN.md` without ` (in app)`, or an older
# "<stem> - the human rewrite.md" name - is the human's own writing. HV6-REUSE prints nothing for the file
# itself (analyse() skips the check), but the file stays in the comparison set, so an agent script that
# copies the human's lines is still reported against it. It joins the family of every corpus file in its
# folder, so a record is never reported against the human's rewrite of itself. An older name must
# also end the way HUMAN_REWRITE_MD says (a "-TRIMMED-for-approval" cut was made by a lane and is
# checked). A `v<N> THE HUMAN (in app).md` file is a record file and is checked like any final.
# test_check_script_reuse_owner_rewrite.py.
def is_human_text(path):
    """True for the human's rewrite that is not the app's text (training_header.parse_script_name:
    kind "rewrite", in_app False)."""
    sn = training_header.parse_script_name(os.path.basename(path))
    if sn.kind != "rewrite" or sn.in_app:
        return False
    return sn.version is not None or bool(HUMAN_REWRITE_MD.search(os.path.basename(path)))


# ⛔ ONLY THE HUMAN'S NEWEST TEXT IN A FOLDER IS COMPARED AGAINST (o9 W226, ruled by o9; lane o9L1415).
# Every other script is compared by its current text only. The human's texts were the one exception: all of
# them outside versions/ stayed in the corpus, so a line the human had already replaced in a newer version
# of the same script (v2 THE HUMAN while v3 THE HUMAN exists) still made another script's copy of that old line
# a reuse row. The exception now covers the newest the human text of each script folder: the highest
# version among the folder's the human texts (is_human_text); an unversioned "- the human rewrite.md" name counts
# as older than any versioned one and is the newest only when it is alone; equal versions are all kept.
# is_human_text is unchanged, so a human text is still never reported itself and still joins its folder's
# family. test_check_script_reuse_owner_newest.py.
def _human_version_key(path):
    sn = training_header.parse_script_name(os.path.basename(path))
    return -1 if sn.version is None else sn.version


def is_newest_human_text(path):
    """True when `path` is a human text (is_human_text) and no other the human text in its folder has a higher
    version. Unversioned the human names rank below every versioned one."""
    if not is_human_text(path):
        return False
    folder = os.path.dirname(os.path.abspath(path))
    mine = _human_version_key(path)
    try:
        names = os.listdir(folder)
    except OSError:
        return True
    for fn in names:
        p = os.path.join(folder, fn)
        if fn.endswith(".md") and p != os.path.abspath(path) and is_human_text(p) and os.path.isfile(p):
            if _human_version_key(p) > mine:
                return False
    return True


def default_reuse_corpus(root=None):
    """The library record files (file_script.record_files, through _record_set) and the human's newest text
    in each script folder (is_newest_human_text) under <root>/script-library/ that carry the header
    marker; root defaults to WORKSPACE."""
    lib = os.path.join(root or WORKSPACE, "script-library")
    found = set()
    if not os.path.isdir(lib) or in_private_folder(lib):
        return []
    for dirpath, dirnames, filenames in os.walk(lib):
        dirnames[:] = _walk_dirs(dirpath, dirnames)
        records = _record_set(dirpath)
        for fn in filenames:
            p = os.path.abspath(os.path.join(dirpath, fn))
            if (fn.endswith(".md") and (os.path.normcase(p) in records or is_newest_human_text(p))
                    and not is_link(p) and is_script(p)):
                found.add(p)
    return sorted(found)


# ⛔ A RETIRED PROVENANCE COPY IS A VERSION OF THE SCRIPT IT WAS MADE FROM (o10L354, lane o9L1208).
# o8's de-overuse demo and toolkit rewrite of "Wake-Up: Regular Morning" (prov-wakeup-regular-morning-d
# and -t, 4-retired/AT/) are rewrites of the founders-voice draft that o10L251 filed as a version of
# AT-Sl-D-04-013 (live-0435). After the library rename their file names no longer carry the script's
# name, so family_stem split them into three families sharing 71 to 86 phrases. A file whose header says
# `## Stage: retired` now joins the family of the non-retired corpus file with the same `## Type:` whose
# `## Slug:` matches its `## Working-Slug:` (or `## Slug:`), once the bake-off words (_FAM_DECOR) and
# every non-letter are taken out, and only when exactly one family matches. It joins only a family
# with no non-retired member yet, and computes every join from the families before any join, so file
# order does not change the result (o9L1209 SF). Measured on the tree at
# o9L1208: it joins those two files to live-0435 and nothing else (an SC copy never joins an AT script).
# test_check_script_reuse_retired_family.py.
_HEAD_SLUG = re.compile(r"^##\s*(Working-Slug|Slug)\s*:\s*(\S*)\s*$", re.M)
_HEAD_TYPE = re.compile(r"^##\s*Type\s*:\s*(\S+)", re.M)
_HEAD_RETIRED = re.compile(r"^##\s*Stage\s*:\s*retired\s*$", re.M | re.I)


def _retired_slug_key(head):
    """(is retired, TYPE, slug key) of a header, or None when it names no slug (`none` is no slug)."""
    for m in _HEAD_SLUG.finditer(head):
        v = m.group(2).lower()
        if not v or v == "none":
            continue
        s = re.sub(r"[^a-z0-9]+", "-", v)
        for tok in _FAM_DECOR:
            s = re.sub(r"(?:^|-)" + tok + r"(?=-|$)", "-", s)
        t = _HEAD_TYPE.search(head)
        return (bool(_HEAD_RETIRED.search(head)), t.group(1).upper() if t else "",
                re.sub(r"[^a-z0-9]", "", s))
    return None


# HV6 leaves two folders out, as files checked and as corpus compared against (o5, o3 agreeing,
# 2026-10-10): retired scripts never ship, and the sc-replacements texts are held for the human's
# keep-or-rewrite review and nothing builds from them. An HV6-only exclusion: every other check
# still runs on those files, which is why it is not in SKIP_DIRS.
HV6_EXCLUDED_DIRS = (("script-library", "4-retired"), ("working_corpus_o8", "sc-replacements"))


def hv6_excluded(path):
    """True when `path` sits under script-library/4-retired/ or working_corpus_o8/sc-replacements/."""
    parts = [x.lower() for x in os.path.abspath(path).replace(os.sep, "/").split("/")[:-1]]
    for pair in HV6_EXCLUDED_DIRS:
        for i in range(len(parts) - 1):
            if tuple(parts[i:i + 2]) == pair:
                return True
    return False


def _build_reuse_index(paths, family_paths=()):
    """{gram: set(family)}, {path: family}. One pass, no pairwise comparison.

    family_paths: scripts that are NOT compared but still link families. A
    narrowed corpus (--corpus) drops the featured-corpus script that two pilots
    both declare as their `## Source:`, and without it two versions of one
    script read as two scripts sharing everything. Passing the whole workspace
    here keeps families the same whatever corpus is chosen (o3L15).
    """
    paths = [p for p in paths if not hv6_excluded(p)]
    family_paths = [p for p in family_paths if not hv6_excluded(p)]
    toks = {}
    heads = {}
    for p in paths:
        raw = read_script(p)[0]
        toks[p] = script_reuse_tokens(raw, stops=True)
        heads[p] = split_marker(raw)[0] if has_marker(raw) else raw[:4000]
    nodes = list(paths)
    for p in family_paths:
        if p not in heads:
            raw = read_script(p)[0]
            heads[p] = split_marker(raw)[0] if has_marker(raw) else raw[:4000]
            nodes.append(p)

    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    by_base = {}
    for p in nodes:
        find(p)
        union(p, "stem:" + family_stem(p))
        by_base.setdefault(os.path.basename(p).lower(), []).append(p)

    for p in nodes:
        refs = {r.strip().lower() for r in _MD_REF.findall(heads[p])}
        # "script.md" names no one script: every library final has that name, so a
        # header citing ".../SCRIPT.md" would join all of them into one family.
        refs = [r for r in refs if r in by_base and r != os.path.basename(p).lower()
                and r != "script.md"]
        if len(refs) > _MAX_DECLARED_REFS:
            continue
        for r in refs:
            for q in by_base[r]:
                union(p, q)

    # the human's rewrite is the same script as the record file in its folder (is_human_text block). The
    # folder, not opening_record(): after o10's rename a record may be `v<N> THE HUMAN (in app).md`,
    # which opening_record() does not pick.
    by_folder = {}
    for p in nodes:
        by_folder.setdefault(os.path.normcase(os.path.dirname(os.path.abspath(p))), []).append(p)
    for p in nodes:
        if is_human_text(p):
            for q in by_folder[os.path.normcase(os.path.dirname(os.path.abspath(p)))]:
                union(p, q)

    # A retired provenance copy joins the one live script it was made from (_retired_slug_key).
    live_by_key = {}
    retired = []
    for p in nodes:
        k = _retired_slug_key(heads[p])
        if k is None:
            continue
        if k[0]:
            retired.append((p, k[1:]))
        else:
            live_by_key.setdefault(k[1:], set()).add(p)
    # o9L1209 SF: every join is computed from the families as they stand BEFORE any join, so the
    # result does not depend on file order; a family that already has a non-retired member (through
    # a file-name stem, a `## Source:` or a human rewrite) is never joined, so this rule cannot merge
    # two live families (live slugs collide: SC `doitnow` spans 3 families); and the retired
    # members of one family must all point at the same one live family, else none joins.
    pre = {p: find(p) for p in nodes}
    has_live = {pre[q] for q in nodes if not _HEAD_RETIRED.search(heads[q])}
    wants = {}
    for p, k in retired:
        if pre[p] in has_live:
            continue
        tf = {pre[q] for q in live_by_key.get(k, ())}
        if tf:
            wants.setdefault(pre[p], set()).update(tf)
    for f, tf in wants.items():
        if len(tf) == 1:
            union(f, next(iter(tf)))

    fam = {p: find(p) for p in paths}
    idx = {}
    for p in paths:
        f = fam[p]
        for g in set(_grams(toks[p])):
            idx.setdefault(g, set()).add(f)
    return idx, fam, toks


# ⛔ o10L333 MF1: the default index of WORKSPACE is cached HERE, never in _REUSE. _REUSE means
# only "an index a caller set" (--corpus, --selftest, the tests), and reuse_index_for() returns it
# unchanged. When reuse_index() cached into _REUSE, the first call made every later call treat the
# default index as caller-set: a loose draft in WORKSPACE was compared with nothing, and
# reuse_detail_labelled() found no rows on its second lookup. test_check_script_reuse_default_index.py.
# Keyed by WORKSPACE, so a test that points WORKSPACE at a temp tree never reads a stale index.
_REUSE_DEFAULT = {}


def reuse_index():
    if _REUSE is not None:
        return _REUSE
    key = os.path.normcase(os.path.abspath(WORKSPACE))
    if key not in _REUSE_DEFAULT:
        _REUSE_DEFAULT[key] = _build_reuse_index(default_reuse_corpus())
    return _REUSE_DEFAULT[key]


# The index for a tree other than WORKSPACE, built once per tree (repo_root_for).
_REUSE_BY_ROOT = {}


def reuse_index_for(path):
    """The HV6-REUSE index to compare `path` against: an index a caller set (_REUSE: --corpus,
    --selftest, the tests) wins; else the index of the script's own tree (repo_root_for)."""
    if _REUSE is not None:
        return _REUSE
    root = repo_root_for(path)
    if _same_dir(root, WORKSPACE):
        base = reuse_index()   # the cached default (_REUSE_DEFAULT), never written to _REUSE
    else:
        key = os.path.normcase(os.path.abspath(root))
        if key not in _REUSE_BY_ROOT:
            _REUSE_BY_ROOT[key] = _build_reuse_index(default_reuse_corpus(root))
        base = _REUSE_BY_ROOT[key]
    p = os.path.abspath(path)
    # An HV6-excluded file is never in an index (_build_reuse_index drops it), so without the
    # hv6_excluded() test every such file rebuilt the whole index below, to no effect: about 3 s a
    # file, and one more full index held in memory each (o9L1234).
    if p in base[1] or hv6_excluded(p) or not os.path.isfile(p):
        return base
    # A script outside the corpus (a loose draft, a versions/ file) is compared against it too.
    # ⛔ AND AGAINST THE OTHER DRAFTS (lane o9L1365, o9:W226; o3's report on the W65 reuse count). The
    # corpus holds each folder's record file only, so a draft (`v2 o3`) checked on its own was
    # compared with records and never with another script's draft: AT-Sl-D-09-014 v2 and
    # AT-Sl-N-02-012 v4 share a 17-word sentence that no record holds, and HV6-REUSE printed
    # nothing for either. A file outside the corpus is now compared against paraphrase_corpus (the
    # records plus every other library text named `<NAME> v<N> <AUTHOR>.md`, the corpus
    # HV6-PARAPHRASE already reads). The index of a file IN the corpus is unchanged, so the
    # frozen baseline and the library finals' result do not move.
    # test_check_script_reuse_drafts.py.
    wkey = ("drafts", os.path.normcase(os.path.abspath(root)))
    if wkey not in _REUSE_BY_ROOT:
        _REUSE_BY_ROOT[wkey] = _build_reuse_index(paraphrase_corpus(root))
    if p in _REUSE_BY_ROOT[wkey][1]:
        return _REUSE_BY_ROOT[wkey]
    key = ("with", os.path.normcase(os.path.abspath(root)), os.path.normcase(p))
    if key not in _REUSE_BY_ROOT:
        _REUSE_BY_ROOT[key] = _build_reuse_index(paraphrase_corpus(root) + [p])
    return _REUSE_BY_ROOT[key]


def reuse_hits(path):
    """[(other_path, [phrase, ...])] - maximal shared runs, longest first."""
    return [(other, phrases) for other, _n, phrases in reuse_detail(path)]


def reuse_detail(path):
    """[(other_path, shared_gram_count, [phrase, ...])].

    shared_gram_count is the number of 10-word positions in THIS script whose
    gram also occurs in the other script's family (o3:W5 asked for the count; the
    J13 evidence is stated in it: "25 overlaps of seven words ... nine phrases").
    Read from _overlap_rows, so it applies the same rules as the HV6-REUSE line: no other-script match for a
    text of the same script id or folder, only the other script's newest text, nothing against a versions/ file.
    """
    if hv6_excluded(path):
        return []
    path = os.path.abspath(path)
    rows = _overlap_rows(path)
    if not rows:
        return []
    words = reuse_index_for(path)[2][path]
    out = [(other, len(hits), _hit_phrases(words, [i for i, _g, _ids in hits])) for other, hits in rows]
    out.sort(key=lambda t: (-max(len(p.split()) for p in t[2]), t[0]))
    return out


# ⛔ WHICH TEXTS HV6-REUSE COMPARES (o9:W226, lane o9L1404; o9's rulings of 2026-10-21 after the owners' triage of
# the 53 draft rows; the rows are in orch-work-summaries/w226-hv6-drafts/).
#   1. Two texts of ONE script are never "another script". One script = the same NAME (the id in the file name,
#      training_header.parse_script_name().name_key: AT-Sl-N-12-011, SC-bk1008-haiku55) or the same script folder
#      (versions/ counts as part of it). The own-folder skip used to sit only in reuse_detail(); the HV6-REUSE line
#      reads _overlap_rows(), which had none, so 44 of the 53 rows were a script against its own versions.
#      This also covers a text filed in another folder or written by another author under the same id.
#   2. HV6 is a listener hearing the same line in two LIVE scripts. A script is compared with another script's
#      CURRENT text only: the `(in app)` text, else the highest version (the folder's text before versions/ on a
#      tie), as file_script.record_files orders them; the human's own texts outside versions/ stay compared, because
#      the frozen baseline holds their ids. Older texts are retired and are no comparison source.
#   3. A text under versions/ is history: nothing is reported AGAINST it. It still takes part in the index, so a
#      newer script is not compared with it only because it is not the other script's newest text (rule 2).
# test_check_script_reuse_same_id.py.
def _script_dir(x):
    """The script folder of a text: the folder above a versions/ folder, else its own folder."""
    d = os.path.dirname(os.path.abspath(x))
    return os.path.normcase(os.path.dirname(d) if os.path.basename(d).lower() == "versions" else d)


def _in_versions(x):
    return os.path.basename(os.path.dirname(os.path.abspath(x))).lower() == "versions"


def _library_name(x):
    pn = training_header.parse_script_name(os.path.basename(x))
    return pn if pn.version is not None else None


def _same_script(path, q):
    """True when two library texts (`<NAME> v<N> <AUTHOR>.md`) are texts of one script: the same NAME, or the same
    script folder. A text that is not named that way (a pilot, a loose file) is never the same script."""
    a, b = _library_name(path), _library_name(q)
    if a is None or b is None:
        return False
    return a.name_key == b.name_key or _script_dir(path) == _script_dir(q)


_CURRENT_CACHE = {}


def _current_texts(fam):
    """The set of paths in `fam` a listener can hear: per (script folder, NAME) the `(in app)` text, else the highest
    version (the folder's text before versions/ on a tie; file_script.record_files' order), plus every the human text
    outside versions/. A text not named `<NAME> v<N> <AUTHOR>.md`
    is its own newest text. Cached per index."""
    hit = _CURRENT_CACHE.get(id(fam))
    if hit is not None and hit[0] is fam:
        return hit[1]
    best, cur = {}, set()
    for q in fam:
        pn = _library_name(q)
        if pn is None:
            cur.add(q)
            continue
        if pn.author == "THE HUMAN" and not _in_versions(q):
            cur.add(q)   # the human's own texts stay compared (the frozen baseline holds their ids)
        k = (0 if pn.in_app else 1, -pn.version, 1 if _in_versions(q) else 0, q)
        g = (_script_dir(q), pn.name_key)
        if g not in best or k < best[g][0]:
            best[g] = (k, q)
    cur.update(v[1] for v in best.values())
    _CURRENT_CACHE.clear()
    _CURRENT_CACHE[id(fam)] = (fam, cur)
    return cur


def _hit_phrases(words, hits):
    """The maximal shared runs, longest first, for sorted gram indices `hits` into `words`."""
    runs, start, prev = [], hits[0], hits[0]
    for i in hits[1:]:
        if i == prev + 1:
            prev = i
            continue
        runs.append((start, prev))
        start = prev = i
    runs.append((start, prev))
    phrases = [" ".join(words[a:b + REUSE_N]) for a, b in runs]
    phrases.sort(key=lambda s: -len(s.split()))
    return phrases


# --------------------------------------------------------------------------
# HV6-PARAPHRASE: A RUN OF STANZAS THAT TRACKS ANOTHER SCRIPT, IN ORDER (o9:W198, lane o9L1256)
# --------------------------------------------------------------------------
# HV6-REUSE counts only exact 10-word overlaps, so a rewrite that changes the words and keeps the
# plan passes it. Found 2026-10-11: AT-Sl-N-10-009 v3 o3 follows AT-Sl-N-02-002 v4 THE HUMAN stanza by
# stanza (the same three breaths, the same "if X, I know what to do with it" stanza, the same room
# stanza, the same hands-to-elbows walk), and the writing lane had reworded 50 exact overlaps to 2.
#
# METHOD (a WARNING, never a block; no dependency, no network):
#  1. A stanza is a blank-line block of the spoken text; the human's notes and comments, ((pause)) cues
#     and the dropdown brackets are taken out first. Every option of a dropdown counts (W39).
#  2. A stanza becomes a set of crude word stems with the stopwords out (_para_stem, HV6_PARA_STOP).
#  3. A stem that sits in more than HV6_PARA_COMMON of the corpus's different stanzas is dropped: that
#     is the vocabulary every AT script shares (breath, heavy, warm, mattress, feel). A stanza with
#     fewer than HV6_PARA_MIN_STEMS stems left is not scored.
#  4. Two stanzas MATCH when their idf-weighted Jaccard score is at least HV6_PARA_T.
#  5. The longest chain of matches that rises in BOTH scripts, with at most HV6_PARA_GAP stanzas
#     skipped between links, is the run. It is reported when it holds HV6_PARA_MIN_RUN stanzas and
#     is HV6_PARA_MIN_SHARE of the shorter script's scored stanzas.
# Calibration (579 library texts, lane o9L1256): the two known copies score 19-21 and 6-8; the
# next highest chain among unrelated scripts is 5 (a series of siblings built on one template).
# The same family test as HV6-REUSE over a wider corpus that holds the drafts too (paraphrase_corpus,
# o3:W50), so a script is never compared with its own versions, its own folder, or its `## Pair:` partner. test_check_script_paraphrase.py.
HV6_PARA_T = 0.25
HV6_PARA_GAP = 2
HV6_PARA_MIN_RUN = 5
HV6_PARA_MIN_SHARE = 0.20
HV6_PARA_COMMON = 0.05
HV6_PARA_MIN_STEMS = 3
HV6_PARA_SHOW = 6        # stanza pairs printed in one warning
HV6_PARA_SCRIPTS = 3     # other scripts named in a run, best first

HV6_PARA_STOP = frozenset("""a about above after again all also am an and any are as at be because been before being
below between both but by can could did do does doing down during each few for from further had has have having he
her here hers him his how i if in into is it its itself just let me more most my myself no nor not now of off on once
only or other our out over own same she should so some such than that the their them then there these they this those
through to too under until up very was we were what when where which while who whom why will with would you your
yours yourself ll ve re d s t m one two three every another around along still even ever never always""".split())

_PARA_NOTE3 = re.compile(r"\{\{\{.*?\}\}\}", re.S)
_PARA_NOTE2 = re.compile(r"\{\{(?!\{).*?\}\}", re.S)
_PARA_CUE = re.compile(r"\(\([^)]*\)\)")
_PARA_WORD = re.compile(r"[a-z][a-z']*")
_PARA_STEMS = {}


def _para_stem(w):
    """A crude stem: 'hands' and 'hand', 'loosens', 'loosened' and 'loosening' read as one word."""
    s = _PARA_STEMS.get(w)
    if s is None:
        s = w[:-2] if w.endswith("'s") else w
        s = s.replace("'", "")
        if len(s) > 4:
            for suf, rep in (("ies", "y"), ("ing", ""), ("ed", ""), ("es", ""), ("ly", ""), ("s", "")):
                if s.endswith(suf) and len(s) - len(suf) >= 3:
                    s = s[:-len(suf)] + rep
                    break
        elif len(s) > 3 and s.endswith("s") and not s.endswith("ss"):
            s = s[:-1]
        _PARA_STEMS[w] = s
    return s


def _para_keep_lines(rx, text):
    return rx.sub(lambda m: "\n" * m.group(0).count("\n"), text)


def para_stanzas(raw):
    """[(first line, last line, frozenset of stems)] for the spoken text of `raw`, line numbers in
    the file as written. Notes and comments leave their line breaks behind, so numbers hold.
    The human's struck `~~...~~` text is not spoken and is read out first (script_notes.strip_strikes; a dropped
    line stays as an empty line so the numbers still hold). A wholly struck line inside a stanza does not end the
    stanza: it is skipped, as if the human had deleted it (o9L1279)."""
    before = raw.split("\n")
    raw = strip_strikes(raw, keep_lines=True)[0]
    struck_out = {i + 1 for i, ln in enumerate(raw.split("\n")) if ln == "" and before[i].strip()}
    lines = raw.splitlines(True)
    start = None
    for i, ln in enumerate(lines):
        if ln.strip() == MARKER:
            start = i + 1
            break
    if start is None:
        start = len(split_marker(raw)[0].splitlines(True))
    body = "".join(lines[start:])
    body = _para_keep_lines(_PARA_NOTE3, body)
    body = _para_keep_lines(_PARA_NOTE2, body)
    body = _PARA_CUE.sub(" ", body)
    body = body.replace("’", "'").lower()
    out, cur, n = [], None, start
    for ln in body.splitlines():
        n += 1
        if n in struck_out:
            continue
        s = ln.strip()
        # A heading, a table row or a code fence is document layout, not spoken text: it ends a stanza.
        # (Two database-export files shared their headings and table, and flagged each other on layout.)
        if not s or s == "---" or s[0] in "#|" or s.startswith("```"):
            if cur:
                out.append(cur)
                cur = None
            continue
        if cur is None:
            cur = [n, n, []]
        cur[1] = n
        cur[2].append(s.replace("[[", " ").replace("]]", " ").replace("|", " "))
    if cur:
        out.append(cur)
    res = []
    for a, b, ls in out:
        res.append((a, b, frozenset(_para_stem(w) for w in _PARA_WORD.findall(" ".join(ls))
                                    if w not in HV6_PARA_STOP)))
    return res


# ⛔ THE PARAPHRASE CORPUS HOLDS THE DRAFTS TOO (o3:W50 ruling, 2026-10-11; lane o9L1263). HV6-REUSE compares
# against the library's record files and the human's rewrites only (default_reuse_corpus), so two drafts were never
# compared with each other: an unreleased `v3 o3` text beside a record, or a `versions/` text, was seen only as the
# file being checked. o3 ruled that HV6-PARAPHRASE reads the draft texts too, threshold unchanged. This corpus is
# default_reuse_corpus plus every other library text named `<NAME> v<N> <AUTHOR>[ (in app)].md` that carries the
# header marker, outside a folder and inside its versions/ folder, in every stage; 4-retired/ stays out
# (hv6_excluded), as do ` duplicate` copies and .edit-history/. The exclusions hold: the family test is the one
# HV6-REUSE uses (a script's versions, `## Version-Of:` line, `## Source:`, the human's rewrite), a versions/ text also
# joins the family of every text in its script folder (paraphrase_index_for), and paraphrase_hits still skips the
# script's own folder, its name key and its `## Pair:` partner.
# HV6-REUSE keeps the narrow corpus: its frozen baseline (test_check_script_reuse_baseline_source.py) and the
# record-set test (test_check_script_reuse_record_header.py) pin it, and o3's ruling named only the paraphrase check.
# test_check_script_paraphrase.py case (h).
_PARA_SKIP_DIRS = (".edit-history",)


def paraphrase_corpus(root=None):
    """default_reuse_corpus(root) plus every other marker-carrying library text under <root>/script-library/
    named `<NAME> v<N> <AUTHOR>[ (in app)].md`, versions/ included, HV6-excluded folders and duplicates left out."""
    root = root or WORKSPACE
    found = set(default_reuse_corpus(root))
    lib = os.path.join(root, "script-library")
    if not os.path.isdir(lib) or in_private_folder(lib):
        return sorted(found)
    for dirpath, dirnames, filenames in os.walk(lib):
        dirnames[:] = [d for d in dirnames
                       if (d == "versions" or not _skip_dir(d)) and d not in _PARA_SKIP_DIRS
                       and not is_link(os.path.join(dirpath, d))]
        for fn in filenames:
            if not fn.endswith(".md") or file_script._DUPLICATE_NAME.search(fn):
                continue
            if training_header.parse_script_name(fn).version is None:
                continue
            p = os.path.abspath(os.path.join(dirpath, fn))
            if hv6_excluded(p) or is_link(p) or not is_script(p):
                continue
            found.add(p)
    return sorted(found)


def _para_families(paths):
    """{path: family} for the paraphrase corpus: _build_reuse_index's families, with every versions/ text
    joined to the family of the texts in its script folder (a text filed under an older name or Label still
    belongs to the script whose folder holds it)."""
    fam = dict(_build_reuse_index(paths)[1])
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    by_folder, versioned = {}, set()
    for p in fam:
        d = os.path.dirname(os.path.abspath(p))
        if os.path.basename(d).lower() == "versions":
            d = os.path.dirname(d)
            versioned.add(os.path.normcase(d))
        by_folder.setdefault(os.path.normcase(d), []).append(p)
    # Only a folder with a versions/ text is joined, so two scripts that share a folder (an older layout)
    # stay two families, as HV6-REUSE has them.
    for d in sorted(versioned):
        roots = sorted({find(fam[p]) for p in by_folder[d]})
        for r in roots[1:]:
            parent[find(r)] = find(roots[0])
    return {p: find(f) for p, f in fam.items()}


_PARA_INDEX = {}


def paraphrase_index_for(path):
    """The {path: family} the paraphrase check compares `path` against: the family map of an index a caller set
    (_REUSE: --corpus, --selftest, the tests) wins, as in reuse_index_for; else paraphrase_corpus of the script's
    tree, with `path` added when it is not in it."""
    if _REUSE is not None:
        return _REUSE[1]
    root = os.path.normcase(os.path.abspath(repo_root_for(path)))
    if root not in _PARA_INDEX:
        _PARA_INDEX[root] = _para_families(paraphrase_corpus(repo_root_for(path)))
    base = _PARA_INDEX[root]
    p = os.path.abspath(path)
    if p in base or hv6_excluded(p) or not os.path.isfile(p):
        return base
    key = ("with", root, os.path.normcase(p))
    if key not in _PARA_INDEX:
        _PARA_INDEX[key] = _para_families(paraphrase_corpus(repo_root_for(path)) + [p])
    return _PARA_INDEX[key]


_PARA_CORPUS = {}


def _para_corpus(fam):
    """The corpus the paraphrase check reads, built once per index `fam` (the {path: family} of
    reuse_index_for): the stanzas of every file, the idf of each stem that is not common, and an
    inverted index from stem to (path, stanza number)."""
    hit = _PARA_CORPUS.get(id(fam))
    if hit is not None and hit[0] is fam:
        return hit[1]
    stz = {}
    for p in fam:
        try:
            stz[p] = para_stanzas(io.open(p, encoding="utf-8", errors="replace").read())
        except OSError:
            stz[p] = []
    seen, df, n = set(), {}, 0
    for p, sl in stz.items():
        for _a, _b, st in sl:
            k = (fam[p], st)
            if k in seen:
                continue
            seen.add(k)
            n += 1
            for s in st:
                df[s] = df.get(s, 0) + 1
    idf = {s: math.log((n + 1) / (d + 0.5)) for s, d in df.items() if d <= HV6_PARA_COMMON * n}
    data = {"stz": stz, "idf": idf, "n": n, "w": {}, "post": {}, "count": {}}
    for p, sl in stz.items():
        ws, cnt = [], 0
        for j, (_a, _b, st) in enumerate(sl):
            kept = [s for s in st if s in idf]
            if len(kept) >= HV6_PARA_MIN_STEMS:
                ws.append(sum(idf[s] for s in kept))
                cnt += 1
                for s in kept:
                    data["post"].setdefault(s, []).append((p, j))
            else:
                ws.append(0.0)
        data["w"][p] = ws
        data["count"][p] = cnt
    _PARA_CORPUS[id(fam)] = (fam, data)
    return data


def _para_chain(cells):
    """Longest chain of (i, j) cells that rises in both numbers, at most HV6_PARA_GAP stanzas skipped
    between two links on either side. Returns the chain as a list of cells."""
    cells = sorted(cells)
    best, prev = {}, {}
    top = None
    for c in cells:
        i, j = c
        b, pc = 1, None
        for c2, ln in best.items():
            i2, j2 = c2
            if i2 < i and j2 < j and i - i2 <= HV6_PARA_GAP + 1 and j - j2 <= HV6_PARA_GAP + 1 and ln + 1 > b:
                b, pc = ln + 1, c2
        best[c], prev[c] = b, pc
        if top is None or b > best[top]:
            top = c
    path = []
    while top is not None:
        path.append(top)
        top = prev[top]
    return path[::-1]


def _para_version(p):
    try:
        return training_header.parse_script_name(os.path.basename(p)).version
    except Exception:
        return None


def _para_older_draft(p):
    """True when the text's header carries a `## Older-Draft:` line (training_header.older_draft). Errors: True, so an
    unreadable text never counts as newer and the match is kept (fails open)."""
    try:
        with io.open(p, encoding="utf-8", errors="replace") as f:
            return training_header.older_draft(f.read(16000)) is not None
    except Exception:
        return True


def _para_is_current(p):
    """True for the app's text (`(in app)` in the name) or the record file of its folder. Errors count as current
    (the caller then keeps the match)."""
    try:
        if training_header.parse_script_name(os.path.basename(p)).in_app:
            return True
        folder = os.path.dirname(os.path.abspath(p))
        if os.path.basename(folder) in file_script.NOT_SCRIPT_DIRS:
            return False
        rec = file_script.record_file(folder)
        return rec is not None and os.path.normcase(os.path.abspath(str(rec))) == os.path.normcase(os.path.abspath(p))
    except Exception:
        return True


def _para_drop_superseded(cand, fam):
    """{q: run} without the superseded matches (o3 ruling via o9L1268, 2026-10-11). A match against a text q is
    SUPERSEDED when another text of q's script (same family in `fam`) has a higher version number. It is kept only
    if at least one such newer text also has a warning-level chain against the checked file (is in `cand`);
    else it is dropped: the older text was copied and then rewritten away. A text with no newer text in its
    family is never dropped, and neither is the app's text (an `(in app)` name, which can sit below newer drafts:
    AT-Sl-D-16-002 v2 THE HUMAN (in app) under v3/v4 o3) or its folder's record file (file_script.record_file); lane
    o9L1272. Fails open: any error keeps every match."""
    try:
        by_fam = {}
        for p in fam:
            v = _para_version(p)
            if v is not None:
                by_fam.setdefault(fam[p], []).append((v, p))
        out = {}
        for q, run in cand.items():
            vq = _para_version(q)
            # A text marked `## Older-Draft:` (o9:W199, o10:W245) carries a higher number but older words, so it
            # never counts as newer (lane o9L1272).
            newer = [p for v, p in by_fam.get(fam[q], ()) if vq is not None and v > vq and not _para_older_draft(p)]
            if newer and not any(p in cand for p in newer) and not _para_is_current(q):
                continue
            out[q] = run
        return out
    except Exception:
        return cand


def paraphrase_hits(path, extra_skip=()):
    """[(other_path, run, mine_count, other_count, other_stanzas)] - other scripts whose stanzas a run
    of `path`'s stanzas tracks in order, longest run first. `run` is [(my i, other j, score)].
    The paraphrase corpus (paraphrase_index_for) and HV6-REUSE's family test; `extra_skip` is a set of paths never compared."""
    if hv6_excluded(path):
        return []
    fam = paraphrase_index_for(path)
    path = os.path.abspath(path)
    if path not in fam:
        return []
    corpus = _para_corpus(fam)
    idf, post, wsum = corpus["idf"], corpus["post"], corpus["w"]
    mine = fam[path]
    mine_dir = os.path.normcase(os.path.dirname(path))
    mine_key = training_header.parse_script_name(os.path.basename(path)).name_key
    skip_fam = {fam[q] for q in extra_skip if q in fam}
    mine_stz = corpus["stz"][path]
    cells = {}
    mine_count = 0
    for i, (_a, _b, st) in enumerate(mine_stz):
        kept = [s for s in st if s in idf]
        if len(kept) < HV6_PARA_MIN_STEMS:
            continue
        mine_count += 1
        wa = sum(idf[s] for s in kept)
        acc = {}
        for s in kept:
            for key in post.get(s, ()):
                acc[key] = acc.get(key, 0.0) + idf[s]
        for (q, j), inter in acc.items():
            if q == path:
                continue
            sc = inter / (wa + wsum[q][j] - inter)
            if sc >= HV6_PARA_T:
                cells.setdefault(q, {})[(i, j)] = sc
    cand = {}
    for q, cs in cells.items():
        if len(cs) < HV6_PARA_MIN_RUN or fam[q] == mine or fam[q] in skip_fam:
            continue
        if os.path.normcase(os.path.dirname(q)) == mine_dir:
            continue
        if training_header.parse_script_name(os.path.basename(q)).name_key == mine_key:
            continue
        chain = _para_chain(list(cs))
        if len(chain) < HV6_PARA_MIN_RUN:
            continue
        shorter = min(mine_count, corpus["count"][q])
        if shorter == 0 or len(chain) < HV6_PARA_MIN_SHARE * shorter:
            continue
        cand[q] = [(i, j, cs[(i, j)]) for i, j in chain]
    cand = _para_drop_superseded(cand, fam)
    out = {}
    for q, run in cand.items():
        # one entry per other script family: the file with the longest run
        f = fam[q]
        if f not in out or len(run) > len(out[f][1]) or (len(run) == len(out[f][1]) and q < out[f][0]):
            out[f] = (q, run, mine_count, corpus["count"][q], corpus["stz"][q])
    res = sorted(out.values(), key=lambda t: (-len(t[1]), t[0]))
    return res[:HV6_PARA_SCRIPTS]


def paraphrase_warnings(path):
    """[(code, text)] for the HV6-PARAPHRASE warnings of `path`: none for the human's text, as HV6-REUSE."""
    if is_human_text(path):
        return []
    skip = set()
    try:
        raw = io.open(path, encoding="utf-8", errors="replace").read()
        pid = pair_of(raw)
        if pid:
            partner = find_pair(path, pid)[0]
            if partner:
                skip.add(os.path.abspath(partner))
    except OSError:
        pass
    mine = _para_corpus(paraphrase_index_for(path))["stz"].get(os.path.abspath(path), [])
    root = repo_root_for(path)
    out = []
    for other, run, n_mine, n_other, o_stz in paraphrase_hits(path, skip):
        pairs = ["%d-%d ~ %d-%d (%.2f)" % (mine[i][0], mine[i][1], o_stz[j][0], o_stz[j][1], sc)
                 for i, j, sc in run[:HV6_PARA_SHOW]]
        out.append(("HV6-PARAPHRASE",
                    "%d stanza(s) in a row track %s in order (this script %d scored stanza(s), that one %d); "
                    "lines here ~ there: %s%s - the words differ and the plan is the same; rewrite the "
                    "plan, not the words (HV6, o3 2026-10-11) [judge it]"
                    % (len(run), os.path.relpath(other, root).replace("\\", "/"), n_mine, n_other,
                       "; ".join(pairs), "; ..." if len(run) > HV6_PARA_SHOW else "")))
    return out


# --------------------------------------------------------------------------
# HV6-REUSE: FROZEN ON THE DAY THE CHECK LANDED, OR ADDED SINCE
# --------------------------------------------------------------------------
# Reuse between two library finals was reported as a warning (7d3f381d) because those pairs
# predated the check. After 8f1edd72 widened library_final() to every `## App: live` script, a
# REVISION of a live script that copied a phrase from another live script also passed with 0
# findings (o10L319 review of app PR 502, findings 6 and 7: nine phrases went out that way).
#
# o3:W35 (ruled 2026-10-09, changed the same day at c9378e4e): the overlaps library finals have on
# the day this check lands are FROZEN in REUSE_BASELINE_FILE, one entry per (script id, other
# script id, 10-gram). A frozen overlap is a warning. Any other overlap is "added". Since o3's
# severity ruling (the human 2026-10-09, HV6_SCALE_PHRASES block) an added overlap is a warning unless it
# is copying on a larger scale; that copying between two finals takes HV6_ADDED_REUSE_SEVERITY, and
# in a draft, or with a script that is not a final, it blocks. The 509 rows o9's full run found are
# no longer a fix list; only pairs that meet the scale rule get rewritten.
# Entries are matched by script id (script_id(): the `## ID:` header), not by path, so o10's
# rename of SCRIPT.md files keeps them matching. The file is written by --write-reuse-baseline,
# and is only ever shrunk (delete both directional rows once an overlap is fixed, in the fix commit, by
# hand or with --drop-stale-reuse-rows; restoring the phrase after that blocks; o3:W35 option (b)). A deliberate revert re-adds
# only its own rows with --readd-reuse-rows <ID> (readd_reuse_rows; o10L333 MF2).
# test_check_script_reuse_readd_rows.py, test_check_script_reuse_baseline_source.py.
# An earlier version of this branch measured "added" against the script's first saved version
# (versions/before-revise.md); o3 replaced that with this freeze, and it is gone.
# test_check_script_reuse_added.py, test_check_script_reuse_pr502.py.
HV6_ADDED_REUSE_SEVERITY = "finding"
HV6_ADDED = "added in this revision"
HV6_FROZEN = "frozen in the reuse baseline"
REUSE_BASELINE_NAME = "check_script_reuse_baseline.json"
_ID_RE = re.compile(r"^[ \t]*#{1,6}[ \t]*ID[ \t]*:[ \t]*(\S+)[ \t\r]*$", re.I | re.M)
_LABEL_RE = re.compile(r"^[ \t]*#{1,6}[ \t]*Label[ \t]*:[ \t]*(\S+)[ \t\r]*$", re.I | re.M)
_ID_CACHE = {}
_BASELINE_CACHE = {}


def _header_id(path):
    """The `## ID:` value of a file's header, else its `## Label:` value, else None."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(LIVE_HEADER_READ_BYTES).decode("utf-8", "replace")
    except OSError:
        return None
    cut = head.find("<!-- END SCRIPT HEADER -->")
    if cut >= 0:
        head = head[:cut]
    m = _ID_RE.search(head) or _LABEL_RE.search(head)
    return m.group(1) if m else None


# ⛔ A HUMAN REWRITE WITH NO ID LINE TAKES ITS FOLDER'S ID (o10L339 MF-A, lane o9L1189). Six of the human's
# rewrites carry the pilot's front matter, with no `## ID:` or `## Label:` line. Their id came from
# the file name, which o10's rename changed (SC-05-PILOT-...-v4 became SC-Sl-D-08-001), so their
# baseline rows went stale and eight finals blocked. Such a rewrite now takes the id of its folder's
# record file (file_script.record_files, the first one with an id line), else the folder name: both
# survive a file rename. test_check_script_reuse_owner_rewrite.py.
def script_id(path):
    """A script's id for the reuse baseline: its `## ID:` header, else its `## Label:`; for the human's
    rewrite with neither, its folder's record file's id, else the folder name; else the folder
    name of a SCRIPT.md, else the file name key. Stable across a file rename."""
    p = os.path.abspath(path)
    if p not in _ID_CACHE:
        own = _header_id(p)
        if own:
            _ID_CACHE[p] = own
        elif is_human_text(p):
            folder = os.path.dirname(p)
            ids = [i for i in (_header_id(str(r)) for r in file_script.record_files(folder)
                               if os.path.normcase(os.path.abspath(str(r))) != os.path.normcase(p)) if i]
            _ID_CACHE[p] = ids[0] if ids else os.path.basename(folder)
        elif os.path.basename(p) == "SCRIPT.md":
            _ID_CACHE[p] = os.path.basename(os.path.dirname(p))
        else:
            # o10L328 S3: the name key (version and author removed), so v3 and v4 share an id.
            _ID_CACHE[p] = training_header.parse_script_name(os.path.basename(p)).name_key
    return _ID_CACHE[p]


def reuse_baseline_path(root):
    return os.path.join(root, ".shared", "scripts", REUSE_BASELINE_NAME)


def load_reuse_baseline(root):
    """The frozen (script id, other script id, 10-gram) set of a tree; empty when it has none."""
    key = os.path.normcase(os.path.abspath(reuse_baseline_path(root)))
    if key not in _BASELINE_CACHE:
        rows = set()
        try:
            with io.open(key, encoding="utf-8") as fh:
                for e in json.load(fh).get("entries", []):
                    rows.add((e[0], e[1], e[2]))
        except (OSError, ValueError):
            pass
        _BASELINE_CACHE[key] = rows
    return _BASELINE_CACHE[key]


# ⛔ THE FILE A FINDING NAMES HOLDS THE MOST OF THE SHARED WORDS (o10L339 MF-B, lane o9L1189). A family
# is every corpus file in one script folder. The finding named its alphabetically first file, so a
# final that copied the human's `v9 THE HUMAN.md` was reported against the record `v2 o5 (in app).md` beside
# it, which holds none of the copied lines. It now names the member holding the most of the shared
# 10-grams; a tie goes to the first by path. test_check_script_reuse_owner_rewrite.py.
def _overlap_rows(path):
    """[(other family's representative, [(gram index, gram, [ids of family members holding it])])]
    for every overlap of `path`, in the HV6-REUSE index of its tree. The representative is the
    family member holding the most of the shared grams, the first by path on a tie."""
    idx, fam, toks = reuse_index_for(path)
    p = os.path.abspath(path)
    if p not in fam:
        return []
    if _in_versions(p):
        # history: no finding is reported against a versions/ text (rule 3 above), whatever its file name
        # (lane o9L1407: 8 `v2 o5-PRE-REVIEW-DRAFT.md` files were not library names and were reported).
        return []
    mine = fam[p]
    words = toks[p]
    current = _current_texts(fam)
    members = {}
    for q, f in fam.items():
        # only the other script's newest text, and never a text of this script (rules 1 and 2 above)
        if q in current and q != p and not _same_script(p, q):
            members.setdefault(f, []).append(q)
    grams_of = {}
    per_fam = {}
    held = {}
    for i, g in _gram_positions(words):
        for f in idx.get(g, ()):
            if f == mine or f not in members:
                continue
            holders = []
            for q in members[f]:
                if q not in grams_of:
                    grams_of[q] = set(_grams(toks[q]))
                if g in grams_of[q]:
                    holders.append(script_id(q))
                    held[q] = held.get(q, 0) + 1
            if holders:
                per_fam.setdefault(f, []).append((i, g, holders))
    return [(min(members[f], key=lambda q: (-held.get(q, 0), q)), hits)
            for f, hits in per_fam.items()]


def reuse_detail_labelled(path):
    """[(other_path, shared_gram_count, [phrase, ...], status)]: each overlap split into the
    grams frozen in the reuse baseline of the script's tree and the rest. status starts with
    HV6_FROZEN or HV6_ADDED."""
    return [r[:4] for r in reuse_detail_scaled(path)]


# ⛔ ONE SHARED PHRASE WARNS; COPYING ON A LARGER SCALE BLOCKS (o3:W35 severity, lane o9L1193).
# the human, 2026-10-09 23:14 and 23:16 (Reader notes W35.n1, W35.n2): "There are certain sentences that
# GAIN value by being repeated across scripts. [...] We don't want consistent, gross plagiarism across
# scripts, but we do actually benefit from some repeated phrases." If "I sleep well every single
# night" could be said once, in one script out of hundreds, writers would build awkward sentences to
# avoid it. So an overlap a revision adds is a WARNING unless, with that ONE other script, it is
# (a) HV6_SCALE_SENTENCES or more sentences in a row, in a row in the other script too, that each hold
# an added shared 10-gram (script_sentence_ids defines a sentence; _sentence_run; o3 2026-10-10), or (b) HV6_SCALE_PHRASES or more different added shared
# phrases. Frozen grams (the reuse baseline) count toward neither. The human set (b) at 5 on 2026-10-10 13:58 (o3:T14 item B: "5 is many"; 4 before).
# test_check_script_reuse_scale.py.
HV6_SCALE_SENTENCES = 2
HV6_SCALE_PHRASES = 5


# ⛔ "IN A ROW" MEANS IN A ROW IN BOTH SCRIPTS (o3:W35, answer to o10L350, 2026-10-10; lane o9L1208).
# Two sentences count as a copied passage only if the other script also has them next to each other.
# Two separate lines of another script brought together into one new place are not a copied passage.
# The run is counted against each file of the other script's family on its own: a pair (my sentence
# s, its sentence t) is a shared gram lying in s here and in t there, and the run is the longest chain
# (s, t), (s+1, t+1), ... of such pairs.
def _sentence_run(pairs):
    """The most sentences in a row, in both scripts, among (my sentence, other sentence) pairs."""
    pairs = set(pairs)
    best = 0
    for s, t in pairs:
        if (s - 1, t - 1) in pairs:
            continue
        k = 1
        while (s + k, t + k) in pairs:
            k += 1
        best = max(best, k)
    return best


def _sentence_ids_of(path, cache):
    """script_sentence_ids of a file's text, kept in `cache` (one reuse_detail_scaled call: a file can
    change between calls faster than its modification time does)."""
    p = os.path.abspath(path)
    if p not in cache:
        cache[p] = script_sentence_ids(read_script(p)[0])
    return cache[p]


def _both_run(path, other, fam, toks, words, sent_ids, added, cache):
    """Rule (a)'s count for the added grams (indices into `words`) that `path` shares with the family of
    `other`: the longest run of sentences in a row in `path` that is also in a row in one file of that
    family (_sentence_run)."""
    mine_s = {}
    for i in added:
        s = sentence_of_gram(sent_ids, i)
        if s is not None:
            mine_s.setdefault(" ".join(words[i:i + REUSE_N]), set()).add(s)
    if not mine_s:
        return 0
    f = fam.get(os.path.abspath(other))
    best = 0
    for q in sorted(m for m, g in fam.items() if g == f):
        qw = toks[q]
        q_ids = None
        pairs = set()
        for j, g in _gram_positions(qw):
            if g not in mine_s:
                continue
            if q_ids is None:
                q_ids = _sentence_ids_of(q, cache)
                if len(q_ids) != len(qw):
                    raise ValueError("HV6 sentence ids do not match the words of %s" % q)
            t = sentence_of_gram(q_ids, j)
            if t is not None:
                pairs.update((s, t) for s in mine_s[g])
        best = max(best, _sentence_run(pairs))
    return best


def reuse_scale_hit(scale):
    """True when an added overlap's (sentences in a row, different phrases) meets rule (a) or (b)."""
    return scale[0] >= HV6_SCALE_SENTENCES or scale[1] >= HV6_SCALE_PHRASES


def reuse_detail_scaled(path):
    """reuse_detail_labelled() rows with a fifth field: for an added row, (sentences in a row,
    different phrases) of its added grams with that other script; for a frozen row, None."""
    frozen = load_reuse_baseline(repo_root_for(path))
    sid = script_id(path)
    _idx, fam, toks = reuse_index_for(path)
    words = toks.get(os.path.abspath(path), [])
    sent_ids = None
    cache = {}
    out = []
    for other, hits in _overlap_rows(path):
        added = [i for i, g, ids in hits if not any((sid, o, g) in frozen for o in ids)]
        kept = [i for i, g, ids in hits if any((sid, o, g) in frozen for o in ids)]
        if added:
            if sent_ids is None:
                sent_ids = _sentence_ids_of(path, cache)
                if len(sent_ids) != len(words):   # cannot happen: one tokenizer for both
                    raise ValueError("HV6 sentence ids do not match the words of %s" % path)
            phrases = _hit_phrases(words, added)
            scale = (_both_run(path, other, fam, toks, words, sent_ids, added, cache), len(set(phrases)))
            out.append((other, len(added), phrases,
                        HV6_ADDED + " (not in %s); %d sentence(s) in a row, %d different "
                        "phrase(s) with this script" % (REUSE_BASELINE_NAME, scale[0], scale[1]),
                        scale))
        if kept:
            out.append((other, len(kept), _hit_phrases(words, kept),
                        HV6_FROZEN + " (%s, o3:W35)" % REUSE_BASELINE_NAME, None))
    out.sort(key=lambda t: (-max(len(p.split()) for p in t[2]), t[0]))
    return out


def reuse_baseline_rows(root):
    """Every (script id, other script id, 10-gram) overlap the library finals of `root` have."""
    rows = set()
    for p in default_reuse_corpus(root):
        if not library_final(p) or is_human_text(p):
            continue
        sid = script_id(p)
        for _other, hits in _overlap_rows(p):
            for _i, g, ids in hits:
                for o in ids:
                    rows.add((sid, o, g))
    return sorted(rows)


def _head_commit(root):
    """The commit HEAD names in root's git checkout, read from the files (this module runs no
    git command), or "" when it cannot be read."""
    try:
        gd = os.path.join(root, ".git")
        if os.path.isfile(gd):
            with io.open(gd, encoding="utf-8") as fh:
                gd = os.path.join(root, fh.read().split("gitdir:", 1)[1].strip())
        with io.open(os.path.join(gd, "HEAD"), encoding="utf-8") as fh:
            head = fh.read().strip()
        if not head.startswith("ref:"):
            return head
        ref = head[4:].strip()
        common = gd
        if os.path.isfile(os.path.join(gd, "commondir")):
            with io.open(os.path.join(gd, "commondir"), encoding="utf-8") as fh:
                common = os.path.normpath(os.path.join(gd, fh.read().strip()))
        for base in (gd, common):
            if os.path.isfile(os.path.join(base, ref)):
                with io.open(os.path.join(base, ref), encoding="utf-8") as fh:
                    return fh.read().strip()
        with io.open(os.path.join(common, "packed-refs"), encoding="utf-8") as fh:
            for line in fh:
                if line.rstrip().endswith(" " + ref):
                    return line.split()[0]
    except (OSError, IndexError):
        pass
    return ""


def write_reuse_baseline(root):
    """Write root's REUSE_BASELINE_FILE from its library. Returns the entry count."""
    rows = reuse_baseline_rows(root)
    data = {
        "about": REUSE_BASELINE_ABOUT,
        "written": __import__("datetime").date.today().isoformat(),
        "source_commit": _head_commit(root),
        "entries": [list(r) for r in rows],
    }
    _write_baseline_data(root, data)
    return len(rows)


# The baseline file's `about` text. o3:W35 at 62e71340 and 5a825cd5: rows are removed, both
# directions, in the commit that fixes an overlap; a deliberate revert re-adds only its own rows with
# --readd-reuse-rows (o10L333 MF2: --replace-baseline would also freeze unrelated new copies).
REUSE_BASELINE_ABOUT = (
    "HV6-REUSE overlaps frozen on the day the check landed (o3:W35). Each entry is "
    "[script id, other script id, 10-gram]: the `## ID:` header of a library final, "
    "the id of the script it shares the 10-gram with, and the gram in the check's "
    "normalized words. A listed overlap is a warning; any other is a finding. "
    "Written once by check_script.py --write-reuse-baseline (source_commit names the commit "
    "it was frozen from). Afterwards rows are only removed: whoever fixes an overlap deletes "
    "both directional rows ([A, B, gram] and [B, A, gram]) in the commit that fixes it "
    "(check_script.py --drop-stale-reuse-rows deletes every row that is no longer an overlap), and "
    "restoring the phrase after that blocks. A script deliberately put back to an older version "
    "re-adds its rows in that same commit with --readd-reuse-rows <ID>, which adds only the rows "
    "naming that ID. Never add rows any other way.")


def _write_baseline_data(root, data):
    path = reuse_baseline_path(root)
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    _BASELINE_CACHE.pop(os.path.normcase(os.path.abspath(path)), None)


def freeze_rows(root, commit):
    """The reuse_baseline_rows of `commit`'s script-library: a fresh index of the freeze. Raises
    ValueError when the commit is not named or cannot be read (script_git_dates.export_library)."""
    if not commit:
        raise ValueError("the reuse baseline names no source_commit")
    tmp = tempfile.mkdtemp(prefix="hv6-freeze-")
    try:
        ok, why = script_git_dates.export_library(root, commit, tmp)
        if not ok:
            raise ValueError("cannot read the freeze commit %s: %s" % (commit, why))
        # `git archive` writes CRLF line ends when core.autocrlf is true (this machine), and a record
        # whose header ends `-->\r\n` is not a record file (file_script._is_record_header), so the
        # freeze read as 0 rows. The commit holds LF; put LF back before indexing.
        for dp, _dn, fns in os.walk(tmp):
            for fn in fns:
                if fn.lower().endswith((".md", ".txt")):
                    fp = os.path.join(dp, fn)
                    with open(fp, "rb") as fh:
                        raw = fh.read()
                    if b"\r\n" in raw:
                        with open(fp, "wb") as fh:
                            fh.write(raw.replace(b"\r\n", b"\n"))
        os.makedirs(os.path.join(tmp, ".shared", "scripts"), exist_ok=True)
        return set(tuple(r) for r in reuse_baseline_rows(tmp))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def readd_reuse_rows(root, sid):
    """o3:W35 deliberate revert (o10L333 MF2): add to root's baseline file the current overlap rows
    (reuse_baseline_rows) whose script id or other script id is `sid`, and change no other row or
    field. Returns (rows added, rows naming sid now in the file). Raises OSError/ValueError when the
    file is missing or unreadable: a revert re-adds rows to a freeze, it never starts one."""
    path = reuse_baseline_path(root)
    with io.open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    entries = data.get("entries", [])
    have = {tuple(e) for e in entries}
    # o10L339 SF-b: only rows the freeze itself had. A current row naming `sid` can be another
    # script's NEW copy of the reverted lines; re-adding it would turn that copy's finding into a
    # warning. The freeze is a fresh index of the baseline's source_commit.
    frozen = freeze_rows(root, data.get("source_commit", ""))
    mine = [r for r in reuse_baseline_rows(root) if sid in (r[0], r[1]) and r in frozen]
    new = [r for r in mine if r not in have]
    data["entries"] = [list(r) for r in sorted(have | set(new))]
    _write_baseline_data(root, data)
    return len(new), len(mine)


# ⛔ DROP STALE ROWS WITH A COMMAND, NOT BY HAND (o10L350 SF-A, lane o9L1208). o3:W35: whoever fixes a
# frozen overlap deletes its rows, both directions, in the fix commit, and stale rows are deleted at
# landing. A row is STALE when it is in the file but is no longer a current overlap of the library
# (reuse_baseline_rows): at least one side no longer holds the gram. Each direction is judged on its
# own: the reverse row [B, A, gram] goes only when it is stale too. Several files can carry one
# `## ID:` (live-0327 is held by its final and by the human's v6, v7 and v9 rewrites), so [A, B, gram] can
# be stale while [B, A, gram] is still current; deleting that reverse made the unedited B warn
# HV6-REUSE (o9L1209 MF-1). Nothing is ever added and every other field of the file is kept.
# test_check_script_reuse_drop_stale.py.
def drop_stale_reuse_rows(root):
    """Delete from root's baseline file every row that is not a current overlap. A current row stays
    even when its reverse row is stale (o9L1209 MF-1).
    Returns (rows deleted, rows left). Raises OSError/ValueError when the file is missing or unreadable."""
    path = reuse_baseline_path(root)
    with io.open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    entries = [tuple(e) for e in data.get("entries", [])]
    current = set(reuse_baseline_rows(root))
    stale = {e for e in entries if e not in current}
    gone = stale
    keep = sorted({e for e in entries if e not in gone})
    data["entries"] = [list(e) for e in keep]
    _write_baseline_data(root, data)
    return len(entries) - len(keep), len(keep)


def is_linked_worktree(root):
    """True when root is a linked git worktree: its .git is a file, not a folder."""
    return os.path.isfile(os.path.join(root, ".git"))


_ADDED_CACHE = {}


def git_first_added(path):
    """(iso_date, None) of the commit that FIRST ADDED this file, or (None, reason).

    FILE creation date, not the date of the phrase: `git log -S` cannot be used
    because the reuse check holds lowercased, punctuation-free words, which never
    equal the text in the file. So the answer is an approximation and the output
    says so. The git call is in script_git_dates.py because this file may not
    import subprocess (test_check_script_no_production.py).
    """
    path = os.path.abspath(path)
    if path not in _ADDED_CACHE:
        _ADDED_CACHE[path] = script_git_dates.first_added(path, repo_root_for(path))
    return _ADDED_CACHE[path]


def _script_name(q):
    return os.path.basename(q)


def reuse_first_note(path, other, phrase):
    """One plain sentence: which script had `phrase` first, by file first-add date.

    Compares THIS script with the earliest file of `other`'s family that holds the
    phrase. Says "cannot tell" when either date is missing or the two files were
    added on the same day (a bulk import carries no order).
    """
    idx, fam, toks = reuse_index_for(path)
    f = fam.get(os.path.abspath(other))
    needle = " %s " % phrase
    holders = [m for m, g in fam.items()
               if g == f and needle in " %s " % " ".join(toks[m])] or [other]
    mine, why = git_first_added(path)
    dated = [(git_first_added(h)[0], h) for h in holders]
    dated = [d for d in dated if d[0]]
    if not mine or not dated:
        return "cannot tell which came first (%s)" % (
            why or "the other script has no git add date")
    od, oh = min(dated)
    if mine[:10] == od[:10]:
        return "cannot tell which came first (both files added %s)" % mine[:10]
    if od < mine:
        return "first in %s (file added %s; this file added %s)" % (
            _script_name(oh), od[:10], mine[:10])
    return "first in THIS script (file added %s; %s added %s)" % (
        mine[:10], _script_name(oh), od[:10])


def is_script(path):
    """A file is a script IFF it declares the header boundary: MARKER, or the narrow
    implicit boundary in implicit_header_end(). Nothing else counts."""
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            return has_marker(fh.read(200000))
    except (OSError, UnicodeError):
        return False


# ⛔ WHAT discover() REMOVED FROM THE POPULATION, so the count can be read.
# Filled on every call; `--list` prints it. A bare "N discovered" is a claim
# about the filter wearing the clothes of a claim about the corpus - o1:F66
# catalogues seven instances of that shape across four orchestrators in one
# day, five of which reported CLEAN and survived for exactly that reason.
DISCOVER_EXCLUDED = {"dirs": 0, "md_without_marker": 0, "md_links": 0}


def discover(root):
    found = []
    for k in DISCOVER_EXCLUDED:
        DISCOVER_EXCLUDED[k] = 0
    if in_private_folder(root):
        DISCOVER_EXCLUDED["dirs"] = 1
        return found
    for dirpath, dirnames, filenames in os.walk(root):
        keep = _walk_dirs(dirpath, dirnames)
        DISCOVER_EXCLUDED["dirs"] += len(dirnames) - len(keep)
        dirnames[:] = keep
        for fn in filenames:
            if fn.endswith(".md"):
                p = os.path.join(dirpath, fn)
                if is_link(p):
                    DISCOVER_EXCLUDED["md_links"] += 1
                elif is_script(p):
                    found.append(p)
                else:
                    DISCOVER_EXCLUDED["md_without_marker"] += 1
    return sorted(found)


def default_render(text):
    """Body only, dropdowns resolved to their FIRST option. Never raw-count."""
    if has_marker(text):
        text = split_marker(text)[1]
    # Blank lines are PRESERVED: a statement is one full paragraph (the human,
    # 2026-08-11) and it is the denominator of R1, S1 and the ramp segment
    # count. Stripping blanks here and then splitting on blanks later - the
    # first version of this function did exactly that - collapses every script
    # to a single statement and silently inflates every per-statement budget.
    keep = []
    for line in text.split("\n"):
        s = line.strip()
        if not s:
            keep.append("")
            continue
        if s.startswith("#") or s.startswith(">") or s.startswith("<!--"):
            continue
        if s.startswith("|") or set(s) <= set("-|: "):
            continue
        keep.append(s)
    body = "\n".join(keep)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return DROPDOWN.sub(lambda m: m.group(1).split("|")[0].strip(), body)


def from_filename(path):
    """the human's naming convention, 2026-08-11 - the authoritative type signal.

        SCRIPT-SC-NIGHT-<name>.md
        SCRIPT-SC-DAY-<name>.md
        SCRIPT-AT-NIGHT-<name>.md
        SCRIPT-AT-DAY-<name>.md

    The filename carries TYPE and TIME-OF-DAY, which between them decide
    singlet-vs-6x, mirror, ramp shape, token-blocking and the duration target.
    Encoding it in the name lets every tool key on it without reading or
    parsing a file, and makes "which kind is this?" impossible to get wrong.

    Renaming is SAFE: the importer matches on the <!-- slug --> marker, never
    on the title (memory/slug_keyed_script_import.md), so a rename cannot
    orphan a database row. Preserve the slug marker and the rename is inert.
    """
    base = os.path.basename(path)
    m = re.match(r"SCRIPT-(SC|AT)-(NIGHT|DAY)-", base, re.I)
    if not m:
        return None
    return m.group(1).upper(), m.group(2).upper() == "DAY"


# Group 1 = type (SC/AT), group 2 = tab (D = day, N = night). file_script.py writes
# this label from the header, so it is a declaration, not a guess from prose.
_FINAL_FOLDER = re.compile(r"^(SC|AT)-[A-Za-z]{2}-([DN])-\d", re.I)


def _library_text_name(base):
    """True when a bare file name is a library script text: the old `SCRIPT.md`, or the library name
    `<NAME> v<N> <AUTHOR>[ (in app)].md` (training_header.parse_script_name) of an agent or the human text. A process log
    or a note is not a text."""
    if base.upper() == "SCRIPT.MD":
        return True
    pn = training_header.parse_script_name(base)
    return pn.version is not None and pn.kind in ("draft", "rewrite")


def _final_label(path):
    """The folder-label match of a library script text, else None.

    A text counts when it sits DIRECTLY in a folder named like `SC-Sl-N-01-017-<slug>` and is named SCRIPT.md (before
    the library rename, o10 step 5) or `<ID> v<N> <AUTHOR>[ (in app)].md` (after it). The label comes from the FOLDER,
    not from the file name's own key: the folder is what file_script.py writes from the header and what this function
    read before the rename, so the answer for every renamed file stays what it was; a file's name key starts with the
    same label, but a stray copy named for another script would then carry that script's tab. A text in `versions/`
    sits in a folder with no label and gets none, as before. o10L344: the rename left no SCRIPT.md, so this returned
    None for every text and is_daytime() read all 436 finals as unknown."""
    p = path.replace("\\", "/")
    if not _library_text_name(os.path.basename(p)):
        return None
    return _FINAL_FOLDER.match(os.path.basename(os.path.dirname(p)))


def classify(*, text, path):
    """KEYWORD-ONLY ON PURPOSE - see the note on is_daytime below."""
    """AT vs SC. Filename convention wins; declaration next; folder is a guess."""
    if os.path.basename(path).lower() == "readme.md":  # a library README is never a script (o9L1291)
        return "?", "unknown"
    fn = from_filename(path)
    if fn:
        return fn[0], "filename"
    head = split_marker(text)[0] if has_marker(text) else text[:4000]
    m = re.search(r"##\s*Type:\s*(\w+)", head, re.I)
    if m:
        v = m.group(1).lower()
        # The 3-final library writes the ABBREVIATION ("## Type: SC" / "## Type: AT"),
        # not the word, so "sc" and "at" count; the word-only test left every final
        # as type "?" and the type-keyed screens (SC90, floors, caps) never ran on it.
        if v.startswith("auto") or v == "at":
            return "AT", "declared"
        if v.startswith("self") or v == "sc":
            return "SC", "declared"
    # A library text sits in a folder that carries the label (SC-Sl-N-01-017-...); it was named SCRIPT.md
    # (o10:W226), now `<ID> v<N> <AUTHOR>[ (in app)].md`. Used only when no filename or header type exists.
    fm = _final_label(path)
    if fm:
        return fm.group(1).upper(), "folder-label"
    # FOLDER IS A WEAK SIGNAL AND IT IS KNOWN TO BE WRONG.
    # 10-worry-has-an-off-ramp.md sits in self-coaching/ and is classified AT.
    # TYPE decides singlet-vs-6x, mirror, ramp and token-blocking, so guessing
    # it wrong silently produces a wrong runtime and a wrong budget. Report the
    # guess AS a guess so a caller can see the classification is unconfirmed,
    # never as fact.
    low = path.replace("\\", "/").lower()
    if "/autogenic" in low:
        return "AT", "folder-GUESS"
    if "/self-coaching" in low:
        return "SC", "folder-GUESS"
    return "?", "unknown"


def is_daytime(*, text, path):
    """Return True (day), False (night), or None (UNKNOWN - do not guess).

    ⛔ THIS FUNCTION NO LONGER READS THE SCRIPT BODY, AND THAT IS THE POINT.
    The human, 2026-09-03, killing the keyword heuristic outright:

        "I'm getting frustrated and tired of the tools like this looking for
         strings of words that will then supposedly classify a script as daytime
         or nighttime. It makes no sense. Someone can be sleepy during the day,
         and awake at night. Almost any word is going to be able to be used in
         either script corpus."

    ⭐ HE IS RIGHT AND THE HISTORY PROVES IT. The keyword list was
    ("daytime", "quick reset", "wake", "morning", "focus reset"). It classified
    AT-10 - a NIGHT script - as daytime because its spine reads "listener starts
    awake and circling" and "awake" contains "wake". The fix for that was word
    boundaries, which is a better version of the wrong idea: "wake" as a whole
    word appears in night scripts constantly ("if I wake at three"), and "morning"
    is what a night script promises. **No wordlist can separate these corpora,
    because the vocabulary is genuinely shared.**

    ⛔ SO THE RULE IS: DECLARED OR UNKNOWN. Never inferred from prose.
      1. a `## Domain:` / `## Type:` header, or a filename convention -> use it
      2. a library final's folder label (SC-Sl-D-... / SC-Sl-N-...) -> use it
      3. otherwise **None**. The caller asserts no duration verdict at all,
         exactly as it already does for an unknown TYPE.

    ⚠️ Returning None is a real answer and callers must handle it. A tool that
    guesses is worse than one that abstains, because a guess costs a wrong verdict
    plus the inference spent later discovering it was wrong.
    """
    fn = from_filename(path)
    if fn:
        return fn[1]

    hdr = re.search(r"^##\s*Domain:\s*(.+)$", text[:4000], re.M | re.I)
    if hdr:
        v = hdr.group(1).strip().lower()
        if v.startswith("day"):
            return True
        if v.startswith("night") or v.startswith("sleep"):
            return False
        if v.startswith("both") or v.startswith("either"):
            return None

    # A library text (SCRIPT.md, or `<ID> v<N> <AUTHOR>[ (in app)].md` since the rename) carries its tab in the folder label that
    # file_script.py writes: SC-Sl-D-... is day, SC-Sl-N-... is night. The label is
    # declared, not inferred. Without this every final read as None, and the
    # callers treat None as night, so once a final's type became known its daytime
    # energy words got BANNED-SLEEP and a daytime AT got the night floor
    # (o9L1122, review of o9/sc90-gate, 2026-10-08).
    fm = _final_label(path)
    if fm:
        return fm.group(2).upper() == "D"

    # A script-domains.json manifest, filled by classification lanes, was planned
    # here (3cda6fbd, 2026-09-03). The human stopped those lanes the same day ("classify
    # them DURING the rewrite"), the file was never written, and the reader looked
    # it up through a name, WS, that this module never defined, so the branch could
    # only ever return None. Removed (o9L1126, 2026-10-08).
    return None


def drift_check(text, path):
    """Refuse when the filename and an in-file declaration disagree.

    Two sources of truth drift. Making the disagreement a REFUSAL rather than a
    documented caution is the whole point of the convention: a file that says
    SC in its name and AT in its header would otherwise be scored one way and
    written the other, silently, forever.
    """
    fn = from_filename(path)
    if not fn:
        return None
    head = split_marker(text)[0] if has_marker(text) else text[:4000]
    m = re.search(r"##\s*Type:\s*(\w+)", head, re.I)
    if not m:
        return None
    v = m.group(1).lower()
    declared = "AT" if v.startswith("auto") or v == "at" else (
        "SC" if v.startswith("self") or v == "sc" else None)
    if declared and declared != fn[0]:
        return "filename says %s, header declares %s" % (fn[0], declared)
    return None


# ==========================================================================
# PA5 - A PERFORMED COUNT IS PART OF THE STATEMENT IT COMPLETES
# ==========================================================================
# RULED the human 2026-09-23 (o8:D40), on the "One / Two / Three" lines of his A1
# rewrite, verbatim: "they sit outside them. The brief pauses are a part of the
# full statement." PA5 in SCRIPT-CHECKLIST-2B-PAUSE.md carries it.
#
# Before this (o3:W11, lane o3L15), every number line read as a statement of its
# own: a cue on its own line becomes a blank line once the cue is removed, so
# "count of three / ((pause 1s)) / One / ((pause 1s)) / Two ..." split into five
# statements. Measured on AT-S10a v2: 59 statements where its pause blocks give 31.
# That nearly doubled the S1 and R1 per-8-statements budgets.
#
# WHAT COUNTS AS A PERFORMED COUNT - all of these, or it is left alone:
#   1. two or more lines that are each ONLY a number word, in order from "One"
#      ("One", "Two", "Three", optional trailing . , ! or ...);
#   2. each pair of number lines separated by cue-only lines, every one of them
#      SHORT (<= COUNT_CUE_MAX seconds; a cue with no seconds is not short);
#   3. AFTER AN ANNOUNCED COUNT: the nearest text line above it (blank and cue
#      lines skipped) says "count", or a number word before beats / counts /
#      breaths / seconds ("for three long beats").
# WHAT IT DOES: the announcing paragraph, the count, its cues and the closing
# line directly after the count (no blank line between) become ONE statement.
# PA5: the closing line "is still the statement's sensory tail (PA3); it has
# only moved." The number words still count as spoken words, and every cue
# still counts in the pause total: the listener hears both.
COUNT_CUE_MAX = 3.0
_NUMBER_WORDS = ["one", "two", "three", "four", "five",
                 "six", "seven", "eight", "nine", "ten"]
_NUMBER_LINE = re.compile(
    r"^\s*(" + "|".join(_NUMBER_WORDS) + r")\s*(?:\.{1,3}|,|!)?\s*$", re.I)
COUNT_ANNOUNCE = re.compile(
    r"\bcount(?:s|ing)?\b"
    r"|\b(?:" + "|".join(_NUMBER_WORDS) + r"|\d+)\s+(?:[a-z]+\s+)?"
    r"(?:beats?|counts?|breaths?|seconds?)\b", re.I)


def _number_of(line):
    m = _NUMBER_LINE.match(line)
    return _NUMBER_WORDS.index(m.group(1).lower()) + 1 if m else 0


def _cue_only(line):
    s = line.strip()
    return bool(s) and bool(PAUSE.search(s)) and not PAUSE.sub("", s).strip(" *_")


def _short_cue(line):
    if not _cue_only(line):
        return False
    secs = [PAUSE_SECS.search(c) for c in PAUSE.findall(line)]
    return all(secs) and sum(float(m.group(1)) for m in secs) <= COUNT_CUE_MAX


def performed_counts(body):
    """[(announce_i, first_num_i, last_num_i, tail_i_or_None)] over body lines."""
    lines = body.split("\n")
    spans = []
    i = 0
    while i < len(lines):
        if _number_of(lines[i]) != 1:
            i += 1
            continue
        a = i - 1
        while a >= 0 and (not lines[a].strip() or _cue_only(lines[a])):
            a -= 1
        if a < 0 or not COUNT_ANNOUNCE.search(lines[a]):
            i += 1
            continue
        last, want, k = i, 2, i
        while True:
            m, gap_ok, saw = k + 1, True, False
            while m < len(lines) and _cue_only(lines[m]):
                saw = True
                gap_ok = gap_ok and _short_cue(lines[m])
                m += 1
            if saw and gap_ok and m < len(lines) and _number_of(lines[m]) == want:
                last, k, want = m, m, want + 1
                continue
            break
        if last == i:            # a lone "One" is not a count
            i += 1
            continue
        e = last + 1
        while e < len(lines) and _cue_only(lines[e]):
            e += 1
        tail = e if e < len(lines) and lines[e].strip() else None
        spans.append((a, i, last, tail))
        i = (tail if tail is not None else e)
    return spans


def fold_performed_counts(body):
    """(body with each performed count folded into its statement, n_counts).

    Used for the STATEMENT count only. Words and pause seconds are taken from
    the unfolded body, because the listener hears the numbers and the cues.
    """
    spans = performed_counts(body)
    if not spans:
        return body, 0
    lines = body.split("\n")
    drop = set()
    for a, _first, last, tail in spans:
        end = tail if tail is not None else last + 1
        while end < len(lines) and tail is None and _cue_only(lines[end]):
            end += 1
        # everything between the announcing line and the tail goes: blank
        # lines, cues and number lines. The tail then sits directly under the
        # announcing paragraph, so the paragraph split reads them as one.
        drop.update(range(a + 1, end))
    return "\n".join(l for j, l in enumerate(lines) if j not in drop), len(spans)


# AR21 (file A2, the human 2026-10-14, o3 W60; lane o9L1330). A `((pause 1s))` cue on its own line between two
# paragraphs is a connector INSIDE one stanza: the text on both sides is one statement. A cue of 2 s or more
# ends a stanza. Until this fold the checker counted the text before a connector as its own statement, so an
# AT script's statement count ran one high per connector. AT only (the box names AT; no SC script gives a breath
# instruction). Used for the STATEMENT count only, like fold_performed_counts: words and seconds come from
# the unfolded body.
CONNECTOR_SECS = 1.0


def _connector_cue(line):
    if not _cue_only(line):
        return False
    secs = [PAUSE_SECS.search(c) for c in PAUSE.findall(line)]
    return all(secs) and sum(float(m.group(1)) for m in secs) <= CONNECTOR_SECS


def fold_connectors(body):
    """(body with each 1-second connector cue joined to the text on both sides, n_connectors).

    A connector counts only when text sits on both sides of it (a cue at the start or end of the
    script joins nothing).
    """
    lines = body.split("\n")
    drop = set()
    n = 0
    for i, ln in enumerate(lines):
        if not _connector_cue(ln):
            continue
        a = i - 1
        while a >= 0 and not lines[a].strip():
            a -= 1
        b = i + 1
        while b < len(lines) and not lines[b].strip():
            b += 1
        if a < 0 or b >= len(lines) or _cue_only(lines[a]) or _cue_only(lines[b]):
            continue
        drop.update(range(a + 1, b))
        n += 1
    if not n:
        return body, 0
    return "\n".join(l for j, l in enumerate(lines) if j not in drop), n


# HV4, BW8, HV10 (o3 W60 rule boxes of 2026-10-14; lane o9L1330, o9:W216). Three spoken-wording screens that
# read EVERY dropdown option, in the text a member hears (notes already removed, planning lines skipped).
# Each hit carries its statement number (the paragraph of the body, counted as the STATEMENT count counts
# paragraphs, but before any connector fold) and its line (in the checked text with the human's {{{ notes }}}
# removed, so a note above a hit moves the number).
#   HV4  "it is", "I am" and helper-verb "it has" in full. Allowed: the verb ends its clause ("right where it
#        is", "Yes, I am."), the word after it has moved ("how wonderful it is to lie here") and "has" meaning
#        owning or "has to". A hit that cannot be told from an allowed case is a WARNING ("judge it"); the
#        rest block in a draft and report on a library final (final_sink), like SC90.
#   BW8  "whole of": a WARNING, like the other file-4B screens (BW7, HV9): the box makes the writer and the
#        reviewer search for it, and the fix is a rewrite.
#   HV10 a sentence that ends on "so" + 1 or 2 words with no "that" after the "so": a WARNING, because the
#        ear's wait for "that" is a reading of the sentence end. "so much / many / far / long / on / forth /
#        be / often / soon" are left out: they finish their own thought.
_OPT_STOP = "¦"      # joins a menu's options; ends a clause so no phrase is read across two options
_CLAUSE_END = re.compile(r"[.,;:!?…—]+[\"'’”)\]]*(?:\s+|$)|\s-{1,2}\s|" + _OPT_STOP)
_HV4_FULL = re.compile(r"\b(it\s+is|I\s+am|it\s+has)\b(?!['’])", re.I)
# The box itself names how, what, where, wherever, however and as. Its principle is that the word that
# belongs after the verb has moved to the front, so every word that can front a complement counts too
# (o9L1331, from o5's drafts: "who I am at my core"): who, whom, whoever, which, whichever, and "that" as a
# relative after all/everything/anything/something/nothing. "than" is the box's case 3 (word left out).
_HV4_MOVED = {"how", "what", "where", "wherever", "however", "whatever", "than",
              "who", "whom", "whoever", "whomever", "which", "whichever"}
_HV4_THAT_AFTER = {"all", "everything", "anything", "something", "nothing", "everyone", "anyone"}
# when / whenever / why can front a complement but mostly join a clause ("when I am in bed"), so they are
# never passed on sight: they only turn a sure hit into a warning.
_HV4_MAYBE_MOVED = {"when", "whenever", "why"}
# "with it is": "it" ends a prepositional phrase, so "it is" is not a subject plus verb.
_HV4_PREPS = {"with", "to", "for", "of", "in", "on", "at", "by", "from", "about", "into", "onto", "over",
              "under", "through", "around", "without", "upon", "toward", "towards", "within", "behind",
              "beside", "beneath", "across", "against", "like", "near", "off", "out", "up", "down"}
_HV4_MOVED_AS = {"as"}
# After a moved word, "it is" is allowed when what follows is a to-phrase, a place or a joining word.
_HV4_AFTER_OK = {"to", "in", "at", "on", "with", "for", "of", "now", "here", "there", "today", "tonight",
                 "so", "and", "but", "because", "when", "while", "that", "if", "right", "still", "again",
                 "inside", "within", "around", "like", "from", "by", "or", "as"}
_HV4_PARTICIPLES = {"been", "gone", "become", "begun", "grown", "got", "gotten", "come", "run", "made",
                    "taken", "given", "known", "shown", "seen", "fallen", "risen", "drawn", "worn", "held",
                    "left", "kept", "felt", "let", "set", "put", "brought", "found", "built", "lost",
                    "slipped", "settled", "softened", "stopped"}
# o9L1332 (o5's report after fdf2dd67d). (b) a degree word moved forward: "the more certain I am that I can".
_HV4_DEGREE = re.compile(r"\bthe\s+(?:more|less|most|least)\b", re.I)
# (c) a gerund subject ending in "it": "Choosing it is something I do". The "it" is the gerund's object, not
# the subject of "is". A gerund that takes a clause ("thinking it is calm") leaves "it" the subject, so those
# are left out; so is a gerund that is not first in its clause.
_HV4_GERUND_CLAUSE = {"thinking", "knowing", "feeling", "noticing", "realizing", "realising", "believing",
                      "hoping", "seeing", "wondering", "saying", "sensing", "finding", "remembering",
                      "imagining", "trusting", "learning", "understanding", "discovering", "recognizing",
                      "recognising", "wishing", "guessing", "suspecting", "telling", "showing",
                      "hearing", "watching", "assuming", "expecting", "deciding", "wanting", "meaning",
                      "being", "during", "evening", "morning", "nothing", "something", "anything",
                      "everything"}
_HV4_GERUND_LEAD = {"and", "but", "or", "so", "just", "simply", "only", "even", "now", "then", "also",
                    "still", "because", "when", "if", "while", "that", "since"}
# (a) a noun directly before "I am" with the relative word left out ("the person I am at midnight").
# It cannot be told from "the quiet I am drifting" with confidence, so it is a [judge it] warning.
_HV4_DETS = {"the", "a", "an", "my", "your", "this", "that", "every", "each", "one", "our", "his", "her",
             "their", "some", "any", "no", "another"}
_HV4_NOT_NOUN = (_HV4_DETS | _HV4_PREPS
                 | {"and", "but", "or", "so", "because", "when", "whenever", "if", "as", "while", "then",
                    "now", "i", "it", "you", "we", "they", "he", "she", "is", "am", "are", "to", "not",
                    "just", "still", "yet", "even", "only", "also", "though", "although", "until",
                    "since", "once", "before", "after", "where", "how"})
_HV10_SO = re.compile(r"(?:^|\s)so\s+(?:(?:[A-Za-z'’-]+ly|very|quite|too)\s+)?([A-Za-z'’-]+)$", re.I)
# "and so do I", "and so it goes": the "so" answers or closes a thought; no "that" is waited for.
_HV10_INVERSION = re.compile(
    r"(?:^|\s)so\s+(?:do|does|did|am|is|are|was|were|can|could|will|would|have|has|it\s+goes)\b", re.I)
# "does so", "said so", "think so", "hope so": "so" after a verb stands for a clause; it is no intensifier.
_HV10_VERB_SO = re.compile(
    r"\b(?:do|does|did|done|doing|say|says|said|saying|think|thinks|thought|hope|hopes|hoped|believe|believes|"
    r"believed|suppose|guess|know|knew|tell|tells|told|expect|imagine|trust|fear)\s+so\b",
    re.I)
# A closing "so" + manner adverb in -ly ("so automatically") names no feeling; these -ly words do.
_HV10_FEELING_LY = {"lonely", "lovely", "friendly", "lively", "silly", "jolly", "cuddly", "comfortably"}
_HV10_SKIP = {"much", "many", "far", "long", "on", "forth", "be", "often", "soon", "that"}


def _spoken_lines(raw, mode="all"):
    """[(statement_no, line_no, text)] of the lines a member hears, every menu option in a row.

    `mode` "first" keeps only each menu's first option (the default text a listener hears); "fixed"
    drops every menu (the fixed prose only), leaving a clause break where the menu stood.

    Line numbers count the lines of `raw`. A menu that spans lines keeps its newlines after the
    expansion so the lines below it keep their numbers.
    """
    text = raw
    first = 1
    if has_marker(text):
        head, text = split_marker(text)
        first = len(head.splitlines()) + 1

    def expand(m):
        opts = " %s " % _OPT_STOP
        parts = [re.sub(r"\s+", " ", o.strip()) for o in m.group(1).split("|")]
        if mode == "first":
            parts = parts[:1]
        elif mode == "fixed":
            parts = [""]
        return opts.join(parts) + "\n" * m.group(0).count("\n")
    text = DROPDOWN.sub(expand, text)
    out, stmt, in_par = [], 0, False
    for k, line in enumerate(text.split("\n")):
        s = line.strip()
        if not s:
            in_par = False
            continue
        if s.startswith("#") or s.startswith(">") or s.startswith("<!--"):
            continue
        if s.startswith("|") or set(s) <= set("-|: "):
            continue
        if _PROSE_LIST.match(s) or _PROSE_FIELD.match(s):
            continue
        s = _CODE_SPAN.sub(" ", PAUSE.sub(" ", s))
        s = re.sub(r"\*\*|\*|~~", "", s).strip()
        if not s:
            continue
        if not in_par:
            stmt += 1
            in_par = True
        out.append((stmt, first + k, s))
    return out


def _clause_before(s, pos):
    last = 0
    for m in _CLAUSE_END.finditer(s, 0, pos):
        last = m.end()
    return s[last:pos]


def _next_word(after):
    m = re.match(r"\s*([A-Za-z'’-]+)", after)
    return m.group(1).lower() if m else ""


def voice_gate_hits(raw):
    """{"HV4": [(stmt, line, detail, sure)], "BW8": [...], "HV10": [...]}; `sure` False means judge it."""
    hv4, bw8, hv10 = [], [], []
    for stmt, ln, s in _spoken_lines(raw):
        for m in _HV4_FULL.finditer(s):
            form = " ".join(m.group(1).split())
            after = s[m.end():]
            if re.match(r"\s*(?:[.,;:!?…—]|-{1,2}\s|" + _OPT_STOP + r"|$)", after):
                continue                        # the verb ends its clause
            nxt = _next_word(after)
            ctx = "\"%s\"" % _clip(s[max(0, m.start() - 25):m.end() + 30], 70)
            low = form.lower()
            if low == "it has":
                if nxt == "to":
                    continue
                if nxt in _HV4_PARTICIPLES or (nxt.endswith("ed") and len(nxt) > 4):
                    hv4.append((stmt, ln, "\"%s\" is a helper verb here: write \"it's\" %s" % (form, ctx), True))
                continue                        # owning, or not a form the box lists
            before = [w.lower() for w in re.findall(r"[A-Za-z'’-]+", _clause_before(s, m.start()))]
            near = before[-4:]
            if low == "it is" and before and before[-1] in _HV4_PREPS:
                continue                        # "what I do with it is entirely up to me"
            if (low == "it is" and before and before[-1].endswith("ing") and len(before[-1]) > 4
                    and before[-1] not in _HV4_GERUND_CLAUSE
                    and (len(before) == 1 or before[-2] in _HV4_GERUND_LEAD)):
                continue                        # "Choosing it is something I do": "it" is the gerund's object
            relative_that = any(w == "that" and i > 0 and near[i - 1] in _HV4_THAT_AFTER
                                for i, w in enumerate(near))
            degree = _HV4_DEGREE.search(_clause_before(s, m.start()))
            if any(w in _HV4_MOVED for w in near) or relative_that or degree:
                if nxt in _HV4_AFTER_OK:
                    continue                    # "how wonderful it is to lie here"
                hv4.append((stmt, ln, "\"%s\" after a moved word (how, what, where...): allowed only if the "
                            "word that belongs after it has moved forward; \"%s\" follows %s"
                            % (form, nxt, ctx), False))
            elif any(w in _HV4_MAYBE_MOVED for w in near):
                hv4.append((stmt, ln, "\"%s\" after when/whenever/why: allowed only if the word that belongs "
                            "after it has moved forward (\"why it is so\"), a breach in \"when I am in bed\" "
                            "%s" % (form, ctx), False))
            elif any(w in _HV4_MOVED_AS for w in near):
                hv4.append((stmt, ln, "\"%s\" after \"as\": allowed in \"as calm as I am\", a breach in "
                            "\"as I am drifting\" %s" % (form, ctx), False))
            elif any(w in _HV4_MOVED for w in before):
                hv4.append((stmt, ln, "\"%s\" with a moved word (how, what, where...) earlier in the "
                            "clause %s" % (form, ctx), False))
            elif (low == "i am" and before and before[-1] not in _HV4_NOT_NOUN
                  and any(w in _HV4_DETS for w in before[-3:-1])):
                hv4.append((stmt, ln, "\"%s\" directly after a noun (\"the person I am at midnight\"): allowed "
                            "when the relative word is left out and the complement has moved, a breach in "
                            "\"the quiet I am drifting\" %s" % (form, ctx), False))
            else:
                fix = {"it is": "it's", "i am": "I'm"}[low]
                hv4.append((stmt, ln, "write \"%s\" (the voice engine cannot stress \"%s\") %s"
                            % (fix, form.split()[-1], ctx), True))
        for m in re.finditer(r"\bwhole\s+of\b", s, re.I):
            vague = re.match(r"\s*(?:this|it|that)\b", s[m.end():], re.I)
            bw8.append((stmt, ln, "\"%s\" - say \"my whole [noun]\", \"entire\" or \"full\"%s"
                        % (_clip(s[max(0, m.start() - 20):m.end() + 25], 60),
                           "; \"whole of this/it/that\" also names nothing (BW8, the human 2026-10-15): "
                           "name the real thing" if vague else ""), False))
        # sentences: split on stops; a line ends one unless it ends in a comma, semicolon or colon
        pieces = re.split(r"(?<=[.!?…])[\"'’”)\]]*\s+|" + _OPT_STOP, s)
        for k, piece in enumerate(pieces):
            piece = piece.strip()
            if k == len(pieces) - 1 and _LINE_JOINS.search(piece):
                continue
            core = re.sub(r"[.!?…\"'’”)\]\s]+$", "", piece)
            mo = _HV10_SO.search(core)
            last = mo.group(1).lower() if mo else ""
            if (mo and not _HV10_INVERSION.search(core) and last not in _HV10_SKIP
                    and not _HV10_VERB_SO.search(core)
                    and not (last.endswith("ly") and last not in _HV10_FEELING_LY)):
                hv10.append((stmt, ln, "sentence ends on \"%s\" with no \"that\" after the \"so\": \"%s\" "
                             "- finish it with a \"that\" clause or change the \"so\""
                             % (mo.group(0).strip(), _clip(core, 70)), False))
    def show(hits):
        return [(a, b, c.replace(_OPT_STOP, "|"), d) for a, b, c, d in hits]
    return {"HV4": show(hv4), "BW8": show(bw8), "HV10": show(hv10)}


# o3:W60 third notes on AT-Sl-N-04-004 v2 THE HUMAN (rule boxes at b2544550b; lane o9L1332, o9:W218). Five screens.
#   DD0-DEFAULT-WORD  (DD0 items 2 and 4c, file 3) "lovely", "belly", "plump", "delicious" or "chill" in DEFAULT
#                     text: fixed prose and the first option of a menu. Options 2 to 6 are allowed. Binds AT
#                     and SC (item 4's scope note says both; item 2's words are default-only for any script).
#   TY7-HEAD-IT       (TY7, file 0) "my head" and then "it" in one sentence. Warning. AT and SC.
#   HV12-OF-ME        (HV12, file 4B) "the <noun> of me". Warning. AT and SC.
#   HV8-RARE-REPEAT   (HV8, file 4) one word stem twice in one sentence, only when the stem is not a function
#                     word and not among the common stems (check_script_common_stems.txt: the 600 most
#                     frequent stems of the 481 library finals, counted 2026-10-15). Warning. In an SC script
#                     the box says list it for the human and do not fix it; the warning is the list.
#   RF5-FINALLY       (RF5, file 6) "finally" within six words of "somewhere good". Warning. AT: fixed text
#                     and every option; SC: fixed text only (menu options may name the negative, o5:D82).
_THIRD_DEFAULT_WORDS = re.compile(r"\b(lovely|belly|bellies|plump|delicious|chills?)\b", re.I)
_THIRD_HEAD_IT = re.compile(r"\bmy\s+head\b(?:(?!\bmy\s+head\b).)*?\bit\b", re.I)
_THIRD_OF_ME = re.compile(r"\bthe\s+(?!whole\b)([A-Za-z'’-]+(?:\s+[A-Za-z'’-]+)?)\s+of\s+me\b", re.I)
_THIRD_FINALLY = re.compile(
    r"\bfinally\b(?:\W+[A-Za-z'’-]+){0,6}?\W+somewhere\s+good\b|"
    r"\bsomewhere\s+good\b(?:\W+[A-Za-z'’-]+){0,6}?\W+finally\b", re.I)
_THIRD_FUNCTION = frozenset("""
a about above across after again against all almost along also although always am among an and any are as at
away be because been before being below between both but by can could did do does doing down during each either
else even ever every few for from had has have having he her here hers herself him himself his how i if in
into is it its itself just let me might more most much must my myself no nor not now of off on once one only
or other our ours ourselves out over own same she should so some such than that the their theirs them
themselves then there these they this those through to too under until up upon us very was we were what when
where which while who whom whose why will with without would yet you your yours yourself yourselves
""".split())
_THIRD_COMMON = None


def _third_common():
    """The common stems: check_script_common_stems.txt beside this file (lines starting # are notes)."""
    global _THIRD_COMMON
    if _THIRD_COMMON is None:
        found = set()
        try:
            with io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "check_script_common_stems.txt"), encoding="utf-8") as fh:
                for ln in fh:
                    ln = ln.strip()
                    if ln and not ln.startswith("#"):
                        found.add(ln)
        except OSError:
            pass
        _THIRD_COMMON = frozenset(found)
    return _THIRD_COMMON


def _third_sentences(raw, mode="all"):
    """[(stmt, line, sentence)] of the spoken text: a stop, or the edge of a menu option, ends a sentence."""
    out = []
    for stmt, ln, s in _spoken_lines(raw, mode):
        for piece in re.split(r"(?<=[.!?…])[\"'’”)\]]*\s+|" + _OPT_STOP, s):
            piece = piece.strip()
            if piece:
                out.append((stmt, ln, piece))
    return out


def third_notes_hits(raw, kind):
    """{"DD0-DEFAULT-WORD": [(stmt, line, detail)], "TY7-HEAD-IT", "HV12-OF-ME", "HV8-RARE-REPEAT",
    "RF5-FINALLY"}. `kind` is "AT" or "SC"."""
    out = {"DD0-DEFAULT-WORD": [], "TY7-HEAD-IT": [], "HV12-OF-ME": [], "HV8-RARE-REPEAT": [],
           "RF5-FINALLY": []}
    for stmt, ln, sent in _third_sentences(raw, "first"):
        for m in _THIRD_DEFAULT_WORDS.finditer(sent):
            out["DD0-DEFAULT-WORD"].append(
                (stmt, ln, "\"%s\" in default text (fixed prose or a first option): move it to an option "
                 "2 to 6 \"%s\"" % (m.group(0), _clip(sent, 70))))
    common = _third_common()
    for stmt, ln, sent in _third_sentences(raw, "all"):
        m = _THIRD_HEAD_IT.search(sent)
        if m:
            out["TY7-HEAD-IT"].append(
                (stmt, ln, "\"my head\" and then \"it\" in one sentence: the listener's head is not \"it\" "
                 "(use \"me\" or name the head again) \"%s\"" % _clip(sent, 80)))
        for m in _THIRD_OF_ME.finditer(sent):
            out["HV12-OF-ME"].append(
                (stmt, ln, "\"%s\" - say \"my %s\" if that says the same thing \"%s\""
                 % (m.group(0), m.group(1), _clip(sent, 70))))
        seen, dup = {}, []
        for w in re.findall(r"[A-Za-z][A-Za-z']*", sent.lower()):
            if w in _THIRD_FUNCTION or len(w) < 4:
                continue
            st = _para_stem(w)
            if st in common or st in _THIRD_FUNCTION:
                continue
            seen[st] = seen.get(st, 0) + 1
            if seen[st] == 2:
                dup.append(w)
        for w in dup:
            out["HV8-RARE-REPEAT"].append(
                (stmt, ln, "the uncommon word \"%s\" twice in one sentence \"%s\"" % (w, _clip(sent, 80))))
    for stmt, ln, sent in _third_sentences(raw, "all" if kind == "AT" else "fixed"):
        m = _THIRD_FINALLY.search(sent)
        if m:
            out["RF5-FINALLY"].append(
                (stmt, ln, "\"%s\" - \"finally\" with a vague \"somewhere good\" implies the listener was "
                 "somewhere bad; check what the sentence says they left behind \"%s\""
                 % (_clip(m.group(0), 50), _clip(sent, 70))))
    return {k: [(a, b, c.replace(_OPT_STOP, "|")) for a, b, c in v] for k, v in out.items()}


# AR9 (file AT, the human 2026-10-15, o3:W60): do not lean on the ", and I feel <feeling>" tail across the script;
# the worst case is ONE plain adjective (", and I feel light."). The human set NO number ("many of them"), so this
# is a WARNING that counts, never a block, and the checker sets no limit of its own.
# Counted: blocks (paragraphs) whose last sentence ends ", and I feel <one word>". Close variants (", and I
# am/I'm <one word>") are counted apart and reported, because the box names them as close.
_AR9_FEEL = re.compile(r",\s+and\s+I\s+feel\s+[A-Za-z'’-]+$", re.I)
_AR9_VARIANT = re.compile(r",\s+and\s+I(?:\s+am|'m|’m)\s+[A-Za-z'’-]+$", re.I)


def ar9_tail_counts(raw):
    """(blocks, feel_tail_blocks, variant_tail_blocks, [(stmt, line, ending)]) over the spoken blocks."""
    paras = {}
    for stmt, ln, s in _spoken_lines(raw):
        paras.setdefault(stmt, []).append((ln, s))
    feel = variant = 0
    where = []
    for stmt, rows in sorted(paras.items()):
        ln, last = rows[-1]
        for seg in last.split(_OPT_STOP):
            end = re.sub(r"[.!?…\"'’”)\]\s]+$", "", seg)
            if _AR9_FEEL.search(end):
                feel += 1
                where.append((stmt, ln, _clip(end, 60)))
                break
            if _AR9_VARIANT.search(end):
                variant += 1
                break
    return len(paras), feel, variant, where


def ar9_tail_report(raw):
    blocks, feel, variant, where = ar9_tail_counts(raw)
    if not feel and not variant:
        return ""
    return ("%d of %d blocks end on \", and I feel <one word>\" and %d more on \", and I am/I'm <one word>\" "
            "(AR9, the human 2026-10-15; the human set no number)%s [judge it]"
            % (feel, blocks, variant,
               "; first at " + ", ".join("statement %d line %d (%s)" % w for w in where[:3]) if where else ""))


# AR9, the ", and ..." tail (file AT, the human 2026-10-20, o3:W66): "Far too many of the tails (almost all of them) use
# ', and...' -- ', and I', ', and it', ', and I'm', ', and the'. This is repetitive and far too formulaic."
# The tail of a stanza is its last sentence, in the default text a listener hears (a closing menu counts as its
# first option). WARNING (AT only) when AR9_AND_MIN (75%, o3 ruling via the coordinator) or more of those closing
# sentences hold ", and ". Calibration, AT-Sl-N-02-012: v7 o3 27 of 28 (warns); v2 o3 17 of 30 = 0.57 and v8 o3
# 11 of 29 = 0.38 stay silent (the human liked v2 best, so "more than half" was too low).
AR9_AND_MIN = 0.75  # warn at or above this fraction of stanzas (o3 ruling via the coordinator, 2026-10-20)
_AR9_AND = re.compile(r",\s+and\s", re.I)
_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+")


def ar9_and_counts(raw):
    """(stanzas, with_and_tail, [(stmt, line, sentence)]) over each stanza's closing sentence."""
    paras = {}
    for stmt, ln, s in _spoken_lines(raw, "first"):
        paras.setdefault(stmt, []).append((ln, s))
    hit = []
    for stmt, rows in sorted(paras.items()):
        ln, last = rows[-1]
        sents = [x for x in _SENT_SPLIT.split(last) if x.strip()]
        sent = (sents[-1] if sents else last).replace(_OPT_STOP, " ")
        if _AR9_AND.search(sent):
            hit.append((stmt, ln, _clip(sent, 70)))
    return len(paras), len(hit), hit


def ar9_and_report(raw):
    n, k, hit = ar9_and_counts(raw)
    if not n or k < n * AR9_AND_MIN:
        return ""
    return ("%d of %d stanzas (%.0f%%) close on \", and ...\" - 75%% or more; vary the tails "
            "(AR9, the human 2026-10-20, o3:W66); e.g. %s [judge it]"
            % (k, n, 100.0 * k / n,
               "; ".join("statement %d line %d (\"%s\")" % h for h in hit[:4])))


# BW11 (the human 2026-10-20, o3:W66): "ribs" by itself is banned; say "chest" or "the space between my ribs". Every
# whole word rib or ribs (any case) is an ERROR in every script, AT and SC, unless it sits in "between my ribs" or
# "between the ribs". "rib cage" counts: it is the word "rib" by itself.
_BW11 = re.compile(r"\bribs?\b", re.I)
_BW11_OK = re.compile(r"\bbetween\s+(?:my|the)\s+$", re.I)


def bw11_hits(text):
    """[context] - each bare rib/ribs in `text`."""
    out = []
    for mo in _BW11.finditer(text):
        if _BW11_OK.search(text[max(0, mo.start() - 20):mo.start()]):
            continue
        out.append(_clip(text[max(0, mo.start() - 25):mo.end() + 25], 60))
    return out


# BW12, HV18, RF15 (the human's notes in SC-Sl-D-07-020 v2 THE HUMAN, installed by o5 2026-10-21, lane o5L248; the
# patterns are o5's candidates from SC79-SC81 of SC-SCRIPT-RULES-SOT-DRAFT.md).
# BW12: "Those ___ are mine" / "This ___ is mine" is banned (C17 widened it to "this"). An error on drafts, a
# warning on library finals (final_sink). A relative "that" followed by a pronoun ("the step that I chose is
# mine") is not the construct, so the word after the pointer may not be a subject pronoun.
_BW12 = re.compile(r"\b(?:those|these|this|that)\s+(?!(?:i|it|you|we|he|she|they)\b)(?:[\w']+\s+){1,2}"
                   r"(?:is|are)\s+(?:all\s+)?mine\b", re.I)
# HV18: "and I mean" is allowed for now, but every use goes to the human for his review. A WARNING, never a finding.
_HV18 = re.compile(r"\band\s+I\s+(?:really\s+|truly\s+)?mean\b", re.I)
# RF15: the listener's own things are "my card", never "that card" / "those words" (C16 scope: card, words,
# goals, notes, lines). "lines" only after "those", because "a step that lines up" is a relative "that". A WARNING.
_RF15 = re.compile(r"\b(?:that|those)\s+(?:few\s+)?(?:cards?|words|goals|notes?)\b|\bthose\s+(?:few\s+)?lines\b",
                   re.I)


def rx_hits(rx, text):
    """[context] - each match of `rx` in `text`, clipped."""
    return [_clip(text[max(0, mo.start() - 25):mo.end() + 25], 80) for mo in rx.finditer(text)]


# SC90 (the human, 2026-10-08, FM2 of SCRIPT-CHECKLIST-5-FORMAT-AND-GATE.md). In an SC
# script a menu of whole-sentence options carries NO end period inside the options;
# the single period goes after the closing brackets. Commas and other inner
# punctuation stay. A FINDING, like PAUSE-CUE-LEGACY and NON-ASCII: it is a
# mechanical format rule with no judgement in it (an option either ends in a
# period or it does not), unlike the SOFT bans that are warnings because a
# regex cannot judge them. Only "." is flagged; "?" and "!" are left alone.
# "..." is left alone (a trailing-off pause, not a sentence-end period). The
# OPPOSITE check (a whole-sentence menu with no period after "]]") is NOT done:
# a menu can sit mid-sentence, before a cue or a closing quote, and a regex
# cannot tell a whole-sentence menu from a phrase menu, so it would be a guess.
# o3 ruled (2026-10-08) that SC90 binds AT scripts too, so SC and AT are both
# checked; an unclassified type is not. A doubled full stop "]].." is flagged
# once, as SC90-MENU-DOUBLED-STOP (menu_stop_hits). Files named *-owner-rewrite.md in any spelling (HUMAN_REWRITE_MD; the human's
# own waypoint edits, kept as a record, older than the rule) and anything under
# .edit-history/ are never checked.
# An option that ends in one of these keeps its period: it belongs to the word.
# Matched case-insensitively against the end of the option. ONE list, here.
SC90_ABBREVIATIONS = ("a.m.", "p.m.", "dr.", "mr.", "mrs.", "ms.", "st.", "e.g.", "i.e.")


def sc90_is_final(path):
    return "/script-library/3-final/" in path.replace("\\", "/").lower()


# o10L311 (W235 step 4 review, defect 2): "library final" is no longer only a folder. The 424
# scripts the app serves moved from 3-final/ to 1-drafts/ with `## Stage: draft` and kept
# `## App: live`; they predate the newer rules exactly as the 3-final scripts do. So a file is
# a library final when it is under 3-final/ OR its OWN header says `## App: live`. Keyed on the
# file and not the folder: a new version or rewrite written into a live folder has no App line
# of its own and so still blocks. Local helper (not training_header.py, which another lane is
# changing); same pattern as reader_scripts.APP_LIVE_RE so the Reader and this gate agree.
APP_LIVE_HEADER_RE = re.compile(r"^[ \t]*#{1,6}[ \t]*App[ \t]*:[ \t]*live[ \t\r]*$", re.I | re.M)
LIVE_HEADER_READ_BYTES = 8192


def own_header_says_live(path):
    """True when the file's header (up to `<!-- END SCRIPT HEADER -->`, or its first 8 KB) has a
    `## App: live` line. Unreadable or missing file: False."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(LIVE_HEADER_READ_BYTES).decode("utf-8", "replace")
    except (OSError, TypeError, ValueError):
        return False
    cut = head.find("<!-- END SCRIPT HEADER -->")
    if cut >= 0:
        head = head[:cut]
    return bool(APP_LIVE_HEADER_RE.search(head))


def library_final(path):
    """A library script that predates the newer rules: under 3-final/, or its own header says
    `## App: live`. Decides whether SC90, UNDER-FLOOR, HV6-REUSE between two finals, NO-PAUSE-RAMP
    and OPENING-UNMARKED warn (final) or block. BANNED-SLEEP warns on every script (o3:D16)."""
    return sc90_is_final(path) or own_header_says_live(path)


# the human's rewrite of a script, named "<stem>-owner-rewrite.md", "<stem> - the human rewrite.md",
# "<stem>_OWNER_REWRITE.md" and so on: any letter case, with space, hyphen or underscore
# as the separator, directly before ".md". Built on training_header.REWRITE_SUFFIX, the
# one definition of a rewrite name (reader_scripts.py uses it too), anchored at ".md".
# A version number after "rewrite" is still the human's file ("- the human rewrite v2.md",
# "-owner-rewrite-v3.md": an optional space or hyphen, then v and digits).
# Any other text after "rewrite" is still checked: "-the human-rewrite-TRIMMED-for-approval.md"
# was cut by a lane (o8L155), not by the human. The old test, endswith("-owner-rewrite.md"),
# missed every spaced name (o9, 2026-10-08).
HUMAN_REWRITE_MD = re.compile(training_header.REWRITE_SUFFIX.pattern + r"(?:[- ]?v\d+)?\.md$")


def sc90_exempt(path):
    p = path.replace("\\", "/")
    if "/.edit-history/" in p.lower():
        return True
    if HUMAN_REWRITE_MD.search(os.path.basename(p)):
        return True
    # o10L329 (review o10L326 X5): after the library rename the human's rewrite is `<NAME> v<N> THE HUMAN.md`, which HUMAN_REWRITE_MD
    # does not see. Same exemption for it. Not the `(in app)` text: that was SCRIPT.md before and was checked then. The
    # lane-trimmed copy (`...-the human-rewrite-TRIMMED-for-approval.md`) is now `<NAME> v<N> o8.md`, so it stays checked.
    info = training_header.parse_script_name(os.path.basename(p))
    return info.version is not None and info.kind == "rewrite" and not info.in_app


def sc90_hits(raw):
    """Options inside [[...]] that end in a single period, as (menu_no, option)."""
    text = split_marker(raw)[1] if has_marker(raw) else raw
    hits = []
    for n, mo in enumerate(DROPDOWN.finditer(text), 1):
        for opt in mo.group(1).split("|"):
            if _sc90_option_ends_in_period(opt):
                hits.append((n, opt.strip()))
    # "]].." (two periods after the menu) is not an option period; it is reported
    # once, as SC90-MENU-DOUBLED-STOP, by menu_stop_hits().
    return hits


def _sc90_option_ends_in_period(opt):
    """True when the option ends in a single sentence-end period (not "..." and not
    an abbreviation in SC90_ABBREVIATIONS)."""
    o = PAUSE.sub("", opt).strip().rstrip("*_\"'”’ ").rstrip()
    # Only the option's LAST period is the one that moves; periods between
    # fragments ("Always. Reliably. Easily.") are rhythm and stay.
    return (o.endswith(".") and not o.endswith("..")
            and not re.search(r"(?<![A-Za-z.])(?:%s)$" % "|".join(
                re.escape(a) for a in SC90_ABBREVIATIONS), o, re.I))


def menu_stop_hits(raw):
    """Two SC90-family shapes around "]]", as (code, menu_no, detail). Asked for by
    o10 (o10L252-REPORT.md, 2026-10-08), which traced both to what members see:

      SC90-MENU-DOUBLED-STOP   two periods follow "]]" ("]].."), one hit per menu; or
                               an option ends in a period AND a period follows "]]":
                               "[[A. | B.]]." shows "A.." in the app and is spoken as
                               six dots. One hit per menu, on top of the SC90 hits
                               for the option periods. An option ending in an
                               abbreviation (a.m.) is not counted; o10 handles
                               "[[at 3 a.m.]]." in the app.
      MENU-SPACE-BEFORE-PERIOD "[[A | B]] ." - a space or tab between "]]" and the
                               period reaches the member as "A ." No parser removes it.
    """
    text = split_marker(raw)[1] if has_marker(raw) else raw
    hits = []
    for n, mo in enumerate(DROPDOWN.finditer(text), 1):
        after = text[mo.end():mo.end() + 40]
        if re.match(r"\.\.(?!\.)", after):
            # "]].." - one hit, whatever the options end in. ("]]..." is a
            # trailing-off pause and is left alone.)
            hits.append(("SC90-MENU-DOUBLED-STOP", n,
                         "two periods follow ]] (\"]]..\"), so the member sees a "
                         "doubled stop - keep one period"))
        elif after.startswith("."):
            ended = [o.strip() for o in mo.group(1).split("|") if _sc90_option_ends_in_period(o)]
            if ended:
                hits.append(("SC90-MENU-DOUBLED-STOP", n,
                             "option \"%s\" ends in a period and a period follows ]], so "
                             "the member sees a doubled stop (\"..\") - drop the period "
                             "inside the option" % _clip(ended[0], 60)))
        if re.match(r"[ \t]+\.", after):
            hits.append(("MENU-SPACE-BEFORE-PERIOD", n,
                         "a space before the period after ]] reaches the member as "
                         "\"option .\" - write ]]. with no space"))
    return hits


OPENING_RE = re.compile(r"^##\s*Opening:[ \t]*(\S*)", re.I | re.M)


LENGTH_QUICK_RESET_RE = re.compile(r"^##\s*Length:[ \t]*quick[ \t]+reset[ \t]*$", re.I | re.M)


def is_quick_reset(text):
    """True when the script's header declares `## Length: quick reset` (o3 ruling on W182 (a)):
    a daytime AT quick reset has no 8:00 floor (LN3). No lower limit replaces it."""
    head = split_marker(text)[0] if has_marker(text) else text[:4000]
    return bool(LENGTH_QUICK_RESET_RE.search(head))


def opening_of(text):
    """The `## Opening:` header of an SC script: "negative", "positive", "" (absent) or the bad value.

    the human, 2026-10-09 13:54 PT, on o9:Q6 (verbatim): "1. It's heard once in the You block.
    Removed in the I-block 2. If opening is positive, then both blocks keep it and are
    tripled. If it's negative, the whole first paragraph."

    No structural marker in a script told a negative opening from a positive one (the 3-final
    headers carry none; RF3 and RF7 are prose rules), so the writer declares it. A joy script
    (RF7) has no opening negative and declares `positive`.
    """
    head = split_marker(text)[0] if has_marker(text) else text[:4000]
    m = OPENING_RE.search(head)
    return m.group(1).strip().lower() if m else ""


def is_human_rewrite_name(path):
    """True when the file name is the human's rewrite: an old "-owner-rewrite" name in any case and
    separator, or a new `<NAME> v<N> THE HUMAN.md` name. training_header.parse_script_name decides."""
    return training_header.parse_script_name(path).kind == "rewrite"


def opening_record(path):
    """The record file of the script a human rewrite belongs to, or None.

    The record is the file that is not the human's (not a rewrite, log or note) and carries the library header (an END SCRIPT
    HEADER line). A rewrite in a `versions/` folder looks one folder up. Among several candidates the highest version
    number wins, then the name order. (o10L356, step 7: the folder's old SCRIPT.md is not looked for.)
    """
    folder = os.path.dirname(os.path.abspath(path))
    if os.path.basename(folder).lower() == "versions":
        folder = os.path.dirname(folder)
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return None
    cands = []
    for n in names:
        p = os.path.join(folder, n)
        if not n.lower().endswith(".md") or not os.path.isfile(p):
            continue
        sn = training_header.parse_script_name(n)
        if sn.kind != "draft" or os.path.normcase(p) == os.path.normcase(os.path.abspath(path)):
            continue
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                head = f.read(20000)
        except OSError:
            continue
        if has_marker(head):
            cands.append((-(sn.version or 0), n, p))
    if cands:
        return sorted(cands)[0][2]
    return _versions_fallback(folder, path)


_NAME_VERSION = re.compile(r"(?i)[-_ ]v(\d+)(?![A-Za-z0-9])")
_NOTES_NAME = re.compile(r"(?i)(?:^|[-_ ])notes?(?:[-_ .]|$)")


def _versions_fallback(folder, path):
    """No record file in the folder (o9, after ba73e829): the highest-version script text that
    is not the human's, in the folder's `versions/` subfolders at any depth. Process logs and notes
    are skipped. The version is the library-name version, else the last `-v<N>` in the name,
    else 0; ties go to the name order. SC-pil-SC08 and SC-pil-SCG1 keep their only agent
    drafts in versions/PIL/."""
    vroot = os.path.join(folder, "versions")
    me = os.path.normcase(os.path.abspath(path))
    cands = []
    for dirpath, dirs, files in os.walk(vroot):
        dirs.sort()
        for n in sorted(files):
            p = os.path.join(dirpath, n)
            if not n.lower().endswith(".md") or os.path.normcase(p) == me:
                continue
            sn = training_header.parse_script_name(n)
            if (sn.kind != "draft" or training_header.PROCESS_LOG.search(n)
                    or _NOTES_NAME.search(n[:-3])):
                continue
            ver = sn.version
            if ver is None:
                found = _NAME_VERSION.findall(n[:-3])
                ver = int(found[-1]) if found else 0
            cands.append((-ver, n, p))
    return sorted(cands)[0][2] if cands else None


def inherited_opening(path):
    """o9 W-opening (the human's rewrites, 2026-10-09): the `## Opening:` value a human rewrite with
    no line of its own takes from its record file (opening_record), as (value, record path).
    ("", None) when the record has no valid value or there is no record."""
    rec = opening_record(path)
    if not rec:
        return "", None
    try:
        with open(rec, encoding="utf-8", errors="replace") as f:
            val = opening_of(f.read())
    except OSError:
        return "", None
    return (val, rec) if val in ("negative", "positive") else ("", None)


def first_block_words(spoken):
    """Words in the first paragraph (first blank-line-separated block) of the spoken text."""
    for b in re.split(r"\n\s*\n", spoken):
        if b.strip():
            return len(re.findall(r"[A-Za-z0-9']+", b))
    return 0


def sc_expanded_words(words, first_words, opening):
    """Spoken words of an expanded SC script (o9:Q6, the human 2026-10-09).

    Every paragraph is heard 6 times (mirror x2, tripling x3). A NEGATIVE opening is the
    exception: the whole first paragraph is said once in the YOU-block, is not tripled, and
    the I-block drops it, so it is heard ONCE. A positive opening is kept and tripled in both
    blocks, so it is x6 like the rest. Anything other than "negative" counts as x6. A
    one-paragraph script has no second paragraph for the blocks to run on, so it stays x6.
    """
    if opening != "negative" or first_words >= words:
        return words * SC_MULT
    return (words - first_words) * SC_MULT + first_words


def _timed_parts(raw):
    """(body, cues, pause_secs, spoken, words) of a script's text: the one place the default render,
    its authored pause seconds and its spoken word count are taken. analyse() and pair_runtime() both
    call it, so a paired script's partner is timed exactly as the checked file is."""
    body = default_render(raw)
    cues = PAUSE.findall(body)
    pause_secs = 0.0
    for c in cues:
        m = PAUSE_SECS.search(c)
        if m:
            pause_secs += float(m.group(1))
    spoken = re.sub(r"\*\*|\*|`|~~", "", PAUSE.sub("", body))
    words = len(re.findall(r"[A-Za-z0-9']+", spoken))
    return body, cues, pause_secs, spoken, words


# ⛔ PAIRED AT LENGTH FLOOR (the human, 2026-10-10 18:05 PT, relayed by o3; lane o9L1242). A nap down-ramp
# and its nap up-ramp are ONE session in two files. Each file's header carries `## Pair: <other script
# id>` (e.g. `## Pair: AT-o3-0018`), and the AT floor (LN3) is judged on the two runtimes added
# together. The cap (OVER-CAP) stays per file. First real case: AT-o3-0017 (9:21) + AT-o3-0018 (4:55)
# = 14:16. The partner is looked for in the checked script's library tree (find_pair); a Pair line
# naming a script that cannot be found warns PAIR-NOT-FOUND and the file is judged alone.
# test_check_script_at_pair.py.
PAIR_RE = re.compile(r"^[ \t]*#{1,6}[ \t]*Pair[ \t]*:[ \t]*(\S+)[ \t\r]*$", re.I | re.M)


def pair_of(text):
    """The `## Pair:` value of a script's header, else None."""
    head = split_marker(text)[0] if has_marker(text) else text[:4000]
    m = PAIR_RE.search(head)
    return m.group(1) if m else None


def _pair_matches(cand, pid):
    """True when file `cand` is script `pid`: its `## ID:` (or `## Label:`) header is pid (with or without
    an AT-/SC- prefix on either side), or its file name stem or its folder name is pid or starts with
    pid followed by a space, "-" or "_" (the library names a file `AT-o3-0018 v1 o3.md` in a folder
    `AT-o3-0018-nap-up-ramp`)."""
    def bare(x):
        return re.sub(r"^(AT|SC)-", "", x, flags=re.I).lower()
    hid = _header_id(cand)
    if hid and bare(hid) == bare(pid):
        return True
    low = pid.lower()
    for name in (os.path.splitext(os.path.basename(cand))[0],
                 os.path.basename(os.path.dirname(cand))):
        n = name.lower()
        if n == low or (n.startswith(low) and n[len(low)] in " -_"):
            return True
    return False


def _library_top(path):
    """The script-library/ folder above `path`, else the tree repo_root_for gives."""
    probe = os.path.dirname(os.path.abspath(path))
    while True:
        if os.path.basename(probe).lower() == "script-library":
            return probe
        up = os.path.dirname(probe)
        if up == probe:
            return repo_root_for(path)
        probe = up


def find_pair(path, pid):
    """(chosen path or None, number of matches) for the script `pid` named by `path`'s Pair line.

    Searched from the checked script's category folder (two folders up: <category>/<script folder>/
    file) outward, one folder at a time, up to and including the script-library/ folder above it (or
    the tree repo_root_for gives when there is none). The nearest folder holding a match wins. Among
    matches there, a live file beats one under versions/, then one outside 4-retired/ beats a retired
    one, then an `(in app)` file (training_header.parse_script_name), then the highest version number
    in the file name (`<ID> v<N> <AUTHOR>.md`), then the lowest path in sorted order.
    ⛔ `(in app)` BEFORE HIGHEST VERSION (lane o9L1246, after o3's 22ddb0851 on 2026-10-11). A live
    folder can hold the text the app plays and a newer headerless candidate beside it
    (AT-Sl-N-02-002 has `v2 o5 (in app)` and an unreleased `v3 o3`); the pair is the text the app
    plays, so v2 wins. A folder with no `(in app)` file still gives its highest version (below).
    ⛔ HIGHEST VERSION, NOT file_script.record_file() (o9:W190, lane o9L1242). Each nap folder holds
    `v1 o3.md` (the record file: ID and Stage) and `v2 THE HUMAN.md` (the human's rewrite, which carries the
    Pair line). record_file() skips a rewrite with no `## Stage:` line, so it returns v1, and so did
    the lowest-path order this replaced: AT-o3-0017 v2 THE HUMAN was paired with 0018 v1 (14:07, not the
    true 14:16). The checked file is never its own pair. Private folders
    (PRIVATE_NAMES), symbolic links and the SKIP_DIRS other than versions/ are not entered."""
    here = os.path.normcase(os.path.abspath(path))
    top = _library_top(path)
    d = os.path.dirname(os.path.dirname(os.path.abspath(path)))
    if not os.path.normcase(d).startswith(os.path.normcase(top)):
        d = top
    skip = {s.lower() for s in SKIP_DIRS if s != "versions"}
    private = {n.lower() for n in PRIVATE_NAMES}
    while True:
        found = []
        for dirpath, dirnames, filenames in os.walk(d):
            dirnames[:] = sorted(n for n in dirnames if n.lower() not in skip
                                 and n.lower() not in private and not n.startswith(".wt-")
                                 and not os.path.islink(os.path.join(dirpath, n)))
            for fn in filenames:
                if not fn.lower().endswith(".md"):
                    continue
                cand = os.path.abspath(os.path.join(dirpath, fn))
                if os.path.normcase(cand) == here:
                    continue
                if _pair_matches(cand, pid) and is_script(cand):
                    found.append(cand)
        if found:
            def rank(c):
                parts = [x.lower() for x in os.path.normpath(c).split(os.sep)]
                pn = training_header.parse_script_name(c)
                ver = pn.version if pn.version is not None else -1
                return ("versions" in parts, "4-retired" in parts, not pn.in_app, -ver, c.lower())
            found.sort(key=rank)
            return found[0], len(found)
        up = os.path.dirname(d)
        if _same_dir(d, top) or up == d:
            return None, 0
        d = up


def pair_runtime(path):
    """The runtime in seconds of `path` timed as an AT script (words at WPM plus authored pause
    seconds): the figure analyse() gives an AT file."""
    _, _, pause_secs, _, words = _timed_parts(read_script(path)[0])
    return words / WPM * 60.0 + pause_secs


def analyse(path):
    raw, notes = read_script(path)
    body, cues, pause_secs, spoken, words = _timed_parts(raw)
    # PA5 / o3:W11: a performed count and its short cues are part of the
    # statement they complete, so the statement count is taken from a body with
    # each count folded in. `words` and `pause_secs` above stay unfolded.
    folded, count_blocks = fold_performed_counts(body)
    # AR21 (AT only): a 1-second connector cue joins its two parts into one statement.
    statements_pre_connector = len([b for b in re.split(
        r"\n\s*\n", re.sub(r"\*\*|\*|`|~~", "", PAUSE.sub("", folded))) if b.strip()])
    connector_folds = 0
    if classify(text=raw, path=path)[0] == "AT":
        folded, connector_folds = fold_connectors(folded)
    spoken_folded = re.sub(r"\*\*|\*|`|~~", "", PAUSE.sub("", folded))
    statements = len([b for b in re.split(r"\n\s*\n", spoken_folded) if b.strip()])
    statements_unfolded = len([b for b in re.split(r"\n\s*\n", spoken) if b.strip()])

    # ⛔ W39: WORDING screens read EVERY dropdown option; LENGTH screens read one.
    # A listener hears one option, so runtime is correctly measured off `spoken`.
    # But a banned word sitting in option 4 is still a banned word that ships, and
    # until now five of the six screens never looked at it - every banned-word count
    # this workstream has taken was a count of one sixth of the text.
    #
    # ⚠️ THIS IS A SECOND BODY, NOT A SWAP, and that is the whole care in it.
    # `statements` below stays on the default render because the soft-word and
    # adjective-pair budgets DIVIDE by it. Computing it from the all-options text
    # would inflate both budgets about sixfold and silence the two screens while
    # appearing to widen them.
    spoken_every = re.sub(r"\*\*|\*|`|~~", "", PAUSE.sub("", render_all_options(raw)))

    kind, how = classify(text=raw, path=path)
    day = is_daytime(text=raw, path=path)

    # SC LENGTH IS ALWAYS THE EXPANDED FIGURE (the human, 2026-08-12).
    # The written script is the BASE SUBSTRATE; expansion (mirror x2 * tripling
    # x3) is the DEFAULT, not a post-process. A 325-word SC script IS a
    # 1,950-word / 9:45 script written down efficiently. The singlet figure is a
    # PIPELINE diagnostic and must never be reported as the script's length.
    # DURATION BANDS (the human, 2026-08-12). lo/hi = the FLOOR BAND (a script should
    # not land below `lo`; `hi` is the comfortable floor). cap = the maximum.
    #
    #   SC  floor 8-10 min   cap 15:30 (the human, 2026-10-13, o10:D156: 15:00 is the target, not a
    #       cap; was 15 min / 900 s from 2026-09-15, o8:D7, and 16 min / 960 s before that)
    #   AT  floor 10-12 min  cap 30 min   (daytime AT floor stays 8-10, 2026-08-11)
    #       ⚠️ the human, 2026-09-15 (o8:D6): AT has "a minimum length and no real maximum".
    #       The 1800 s value below is kept as a prompt to look; LN3 says so.
    #
    # AT runs much longer because AT scripts are guided meditations and
    # visualizations - which is ALSO why its floor is higher. The cap is NOT a
    # cap on enrichment: the human ruled explicitly that the floor band is a minimum,
    # not a ceiling. All bands hold "unless I say otherwise."
    opening = opening_of(raw) if kind == "SC" else ""
    # A human rewrite with no `## Opening:` line of its own is timed with its record file's
    # value (o5 does not edit the human's files). Its own line, when present, wins.
    opening_from = None
    if kind == "SC" and not opening and is_human_rewrite_name(path):
        opening, opening_from = inherited_opening(path)
    first_words = first_block_words(spoken)
    spoken_words, spoken_if_negative = words, words
    if kind == "SC":
        spoken_words = sc_expanded_words(words, first_words, opening)
        # An unmarked script is timed as positive (x6); the negative figure is kept
        # alongside so a reader can see the floor it would face once marked.
        spoken_if_negative = sc_expanded_words(words, first_words, "negative")
        total = spoken_words / WPM * 60.0
        lo, hi, cap = 480, 600, 930
    elif kind == "AT":
        total = words / WPM * 60.0 + pause_secs
        lo, hi = (480, 600) if day else (600, 720)
        cap = 1800
    else:
        # Type unknown: still count authored pause seconds - they are real time in
        # the audio whichever type this is. Only the TARGET check depends on type,
        # so lo/hi/cap stay zero and no floor verdict is asserted. Reporting a
        # short runtime because the type was unreadable would look like a length
        # defect when it is a classification gap.
        total, lo, hi, cap = words / WPM * 60.0 + pause_secs, 0, 0, 0

    findings = []
    warnings = []
    # ONE severity rule for library finals (o5's, adopted by o9, 2026-10-08):
    # check_script REPORTS on a finished script (library_final: under script-library/3-final/
    # or its own header says `## App: live`, o10L311) and
    # BLOCKS on drafts and new versions. It covers the SC90 menu codes, final-to-
    # final HV6-REUSE, and the three codes that could not fire on a final until
    # its type and day/night became readable (7d3f381d): UNDER-FLOOR, NO-PAUSE-RAMP
    # and BANNED-SLEEP. Each was 0 on every final before 9251e096 and fires on many
    # now. Codes that already fired on finals before then keep their severity.
    # Since o3:D16 (2026-10-10) BANNED-SLEEP warns on drafts too; see below.
    final_sink = warnings if library_final(path) else findings
    for mo in banned_always_hits(spoken_every):
        findings.append(("BANNED-ALWAYS", mo.group(0)))
    for ctx in bw11_hits(spoken_every):
        findings.append(("BW11", "\"%s\" - \"ribs\" by itself is banned; say \"chest\" or \"the space "
                         "between my ribs\" (BW11, the human 2026-10-20)" % ctx))
    for ctx in rx_hits(_BW12, spoken_every):
        final_sink.append(("BW12", "\"%s\" - \"Those ___ are mine\" / \"This ___ is mine\" is banned; say what I "
                           "do with it (BW12, the human 2026-10-21)" % ctx))
    for ctx in rx_hits(_HV18, spoken_every):
        warnings.append(("HV18", "\"%s\" - \"and I mean\" is allowed only with the human's explicit review: list "
                         "this use for him in the report (HV18, the human 2026-10-21)" % ctx))
    for ctx in rx_hits(_RF15, spoken_every):
        warnings.append(("RF15", "\"%s\" - the listener's own things are \"my card\", \"my words\", \"my goals\", "
                         "never \"that\"/\"those\" (RF15, the human 2026-10-21) [judge it]" % ctx))
    for mo in US_SPELLING_RE.finditer(spoken_every):
        if mo.group(0).lower() in US_SPELLING_SAME:
            continue
        findings.append(("US-SPELLING", "%s -> %s" % (
            mo.group(0), us_spelling_for(mo.group(1), mo.group(2) or ""))))
    # BANNED-SLEEP is a warning on every sleep script, final or not (the human, o3:D16,
    # 2026-10-10 00:36 PT: "make it a warning"). The words are neutral and the direction
    # of the sense decides (SG5), which only a reader can judge. BANNED-ALWAYS above and
    # EV1-ARRIVES below stay blocking findings.
    if kind == "AT" and not day or kind == "SC" and not day:
        for mo in BANNED_SLEEP_ONLY.finditer(spoken_every):
            warnings.append(("BANNED-SLEEP", "%s (SG5: judge the sense: does the line ask "
                             "the body to go up or let it go down)" % mo.group(0)))

    soft_n = len(SOFT.findall(spoken_every))
    soft_budget = statements / 8.0 * 1.0 if statements else 0
    if statements and soft_n > soft_budget:
        findings.append(("S1-SOFT-BUDGET", "%d soft in %d statements (max %.1f)"
                         % (soft_n, statements, soft_budget)))

    pair_n = len(ADJ_PAIR.findall(spoken_every))
    pair_budget = statements / 8.0 * 1.0 if statements else 0
    if statements and pair_n > pair_budget:
        findings.append(("R1-ADJ-PAIR", "%d comma-led adjective-pair tail(s) in %d "
                         "statements (max %.1f)" % (pair_n, statements, pair_budget)))

    for phrase, head in arrival_hits(spoken_prose(raw, render_all_options)):
        findings.append(("EV1-ARRIVES",
                         "arrival verb with the inanimate subject '%s': \"%s\""
                         % (head, _clip(phrase, 70))))

    for mo in THE_BREATHING.finditer(spoken_prose(raw, render_all_options)):
        findings.append(("TY7-THE-BREATHING",
                         "\"%s\" - the listener's own breath is \"my breathing\" "
                         "or \"breathing\" (the human, 2026-09-24)" % mo.group(0)))

    spoken_all = spoken_prose(raw, render_all_options)
    for mo in THE_HEARTBEAT.finditer(spoken_all):
        findings.append(("TY7-THE-HEARTBEAT",
                         "\"%s\" - the listener's own heartbeat is \"my heartbeat\" "
                         "(the human, 2026-09-25)" % mo.group(0)))

    if kind in ("SC", "AT") and not sc90_exempt(path):
        # Library finals (3-final/ or `## App: live`) predate the rule, so SC90 is
        # reported as a WARNING there (non-blocking); everywhere else, drafts,
        # ready-for-owner and new versions included, it is a blocking finding.
        sink = final_sink
        for n, opt in sc90_hits(raw):
            sink.append(("SC90-MENU-END-PERIOD",
                         "menu %d option ends in a period: \"%s\" - move the "
                         "option's final period after ]] (SC90, the human, 2026-10-08)"
                         % (n, _clip(opt, 70))))
        # Same severity rule (o10 asked for these with the SC90 branch).
        for code, n, detail in menu_stop_hits(raw):
            sink.append((code, "menu %d: %s" % (n, detail)))

    # Warnings, not findings: see the THE_HEARTBEAT block comment for the measurement.
    for rx, code, own in ((THE_PULSE, "TY7-THE-PULSE", "my pulse"),
                          (THE_BODY, "TY7-THE-BODY", "my body")):
        for mo in rx.finditer(spoken_all):
            a = max(0, mo.start() - 30)
            warnings.append((code,
                             "\"%s\" - if this is the listener's own, write \"%s\" "
                             "(the human, 2026-09-25) [judge it]: \"...%s...\""
                             % (mo.group(0), own, _clip(spoken_all[a:mo.end() + 30], 80))))

    # BW7 is a SOFT ban (the human, 2026-09-04) - a WARNING, never a finding, and
    # it never moves the exit code. See the BW7 block comment for why a hard
    # verdict on a semantic judgement call would end up switched off.
    shaped = comparison_frames(spoken_all)
    # HV9 counts per PLAYBACK (the human, 2026-10-10 17:09 PT): see comparison_playback().
    play_shapes, option_sets = comparison_playback(raw)
    # The counts are `heard` and `held`, never `total`: `total` is the script's runtime, read by the
    # floor and cap checks below, and a loop variable of that name overwrote it (o9:W189, lane
    # o9L1242; test_check_script_runtime_total.py).
    for shape, (heard, phrases) in play_shapes.items():
        if heard > HV9_SHAPE_MAX:
            warnings.append(("HV9-SHAPE-REPEAT",
                             "the \"%s\" comparison shape can be heard %d times in one "
                             "playback (at most %d allowed): %s - vary the shape (HV9, "
                             "the human, 2026-10-10, o3:T13) [judge it]" % (shape, heard, HV9_SHAPE_MAX,
                                             "; ".join("\"%s\"" % f for f in phrases))))
    for n, held, phrases in option_sets:
        if held > HV9_OPTION_SET_MAX:
            warnings.append(("HV9-OPTION-SET",
                             "menu %d holds %d comparisons across its options (at most %d "
                             "allowed in one option set): %s - cut or vary them (HV9, the human, "
                             "2026-10-10 17:09 PT) [judge it]" % (n, held, HV9_OPTION_SET_MAX,
                                             "; ".join("\"%s\"" % f for f in phrases))))
    for shape, phrase in shaped:
        if generic_frame(phrase):
            warnings.append(("HV9-GENERIC",
                             "\"%s\" compares to something generic - name a specific "
                             "image (HV9, the human, 2026-10-10) [judge it]" % phrase))

    # HV4, BW8, HV10 (voice_gate_hits): every script, AT and SC. One hit per finding.
    gate = voice_gate_hits(raw)
    for stmt, ln, detail, sure in gate["HV4"]:
        (final_sink if sure else warnings).append(
            ("HV4", "statement %d, line %d: %s%s" % (stmt, ln, detail, "" if sure else " [judge it]")))
    for code in ("BW8", "HV10"):
        for stmt, ln, detail, _sure in gate[code]:
            warnings.append((code, "statement %d, line %d: %s [judge it]" % (stmt, ln, detail)))
    # o3:W60 third notes (third_notes_hits): the default-text words block in a draft and report on a library
    # final, like HV4; the other four are warnings.
    third = third_notes_hits(raw, kind)
    for stmt, ln, detail in third["DD0-DEFAULT-WORD"]:
        final_sink.append(("DD0-DEFAULT-WORD", "statement %d, line %d: %s" % (stmt, ln, detail)))
    for code in ("TY7-HEAD-IT", "HV12-OF-ME", "HV8-RARE-REPEAT", "RF5-FINALLY"):
        for stmt, ln, detail in third[code]:
            warnings.append((code, "statement %d, line %d: %s [judge it]" % (stmt, ln, detail)))
    if kind == "AT":                    # AR9 binds AT only (o5:D22 item 3)
        tail = ar9_tail_report(raw)
        if tail:
            warnings.append(("AR9-TAIL", tail))
        and_tail = ar9_and_report(raw)
        if and_tail:
            warnings.append(("AR9-AND-TAIL", and_tail))

    for phrase, why in run_process_hits(spoken_prose(raw, render_all_options)):
        warnings.append(("BW7-RUN",
                         "'run' as a process being executed - %s: \"%s\" "
                         "[SOFT ban, judge it]" % (why, _clip(phrase, 80))))

    # the human's rewrite (not the app's text) gets no HV6-REUSE output (is_human_text block).
    for other, n_grams, phrases, status, scale in ([] if is_human_text(path)
                                                   else reuse_detail_scaled(path)):
        # Reuse between two library finals was invisible until family_stem() stopped
        # making every SCRIPT.md one family (2026-10-08). An overlap FROZEN in the reuse
        # baseline (o3:W35) is a warning. An added overlap is a warning too unless it is copying
        # on a larger scale (reuse_scale_hit, HV6_SCALE_PHRASES block: the human 2026-10-09). Larger-scale
        # copying between two finals takes HV6_ADDED_REUSE_SEVERITY; in a draft, or with a
        # non-final, it blocks.
        both_final = library_final(path) and library_final(other)
        if scale is None or not reuse_scale_hit(scale):
            sink = warnings
        elif not both_final:
            # A draft's larger-scale copying is a finding. o9:W226 made it a warning for one night (a704780fb)
            # while the owners (o3:W68, o5, o8) triaged the 53 draft rows of
            # orch-work-summaries/w226-hv6-drafts/findings.tsv; all were acceptable, and lane o9L1407 ended
            # the warning. test_check_script_reuse_draft_finding.py.
            sink = findings
        else:
            sink = findings if HV6_ADDED_REUSE_SEVERITY == "finding" else warnings
        sink.append(("HV6-REUSE",
                     "%s: %d shared %d-gram(s) in %d phrase(s) also in %s; longest: \"%s\""
                     % (status, n_grams, REUSE_N, len(phrases),
                        os.path.relpath(other, repo_root_for(path)).replace("\\", "/"),
                        _clip(phrases[0], 90))
                     + "; " + reuse_first_note(path, other, phrases[0])))

    # HV6-PARAPHRASE: a run of stanzas that tracks another script in order, whatever the words (o9:W198).
    # A warning only, and the same corpus and exclusions as HV6-REUSE just above. It fails open: an
    # error inside it becomes one warning and the rest of the check runs on (review lane o9L1257).
    try:
        para = paraphrase_warnings(path)
    except Exception as e:  # noqa: BLE001 - advisory check, never break check_script on it
        para = [("HV6-PARAPHRASE", "the paraphrase check could not run (%s: %s); judge HV6 by hand"
                 % (type(e).__name__, _clip(str(e), 120)))]
    for code, text in para:
        warnings.append((code, text))

    non_ascii = sum(1 for ch in raw if ord(ch) > 127)
    if non_ascii:
        findings.append(("NON-ASCII", "%d character(s)" % non_ascii))

    if kind == "AT" and not cues:
        final_sink.append(("NO-PAUSE-RAMP","zero authored cues - AT floor is met by the ramp"))

    # PA0 (o10L639, the human 2026-10-22): one cue form, `((pause Ns))` alone on its own line (pause_cues.py). Every other
    # form is a finding that blocks, on drafts AND finals (a final is not downgraded to a warning here): a single
    # bracket, [[ ]], tilde, "roughly"/"sec", emphasis, a cue at the end of a text line or in the middle of one. Line
    # numbers are those of the file as written. A member-style "(pause here if you need to)" is text: a warning only.
    try:
        _pa0 = pause_cues.scan(io.open(path, encoding="utf-8", errors="replace").read())
    except Exception as e:  # noqa: BLE001 - never break check_script on the pause scan
        _pa0 = []
        # o10L648: a crashed scan checked nothing, so it blocks like any other PA0 finding
        findings.append(("PAUSE-CUE-LEGACY", "the pause-cue scan could not run, so the pause cues were not checked (%s: %s)"
                         % (type(e).__name__, _clip(str(e), 120))))
    for _c in _pa0:
        if _c["action"] == "note":
            warnings.append(("PAUSE-CUE-LEGACY", "line %d: %s is spoken text, not a cue (%s)"
                             % (_c["line"], _c["original"], _c["detail"])))
        else:
            findings.append(("PAUSE-CUE-LEGACY", "line %d: %s -> %s [%s]%s" % (
                _c["line"], _c["original"], _c["canonical"] or "a person must give the seconds / place it",
                ", ".join(_c["kinds"]), "" if _c["action"] == "fixed" else "; not auto-fixable: " + _c["detail"])))

    # the human's notes are already out of `raw`. A broken one is reported, because the
    # text every count above was taken from depends on where the note really ends.
    if notes["unclosed"]:
        findings.append(("UNCLOSED-NOTE",
                         "%d '{{{' with no '}}}' - removed to the end of its "
                         "paragraph; close it so the count is right" % notes["unclosed"]))
    if notes["unopened"]:
        findings.append(("UNOPENED-NOTE",
                         "%d '}}}' with no '{{{' - the text before it is counted as "
                         "spoken; open the note" % notes["unopened"]))
    # the human's struck text `~~...~~` is out of `raw` too (script_notes.strip_strikes). A line with an
    # unclosed `~~` is left in and counted as spoken; file_script.py refuses to build or approve it.
    if notes.get("unclosed_strikes"):
        findings.append(("UNCLOSED-STRIKE",
                         "%d line(s) with an unclosed '~~' - counted as spoken; close or "
                         "remove the strike" % notes["unclosed_strikes"]))

    drift = drift_check(raw, path)
    if drift:
        findings.append(("TYPE-DRIFT", drift))
    # The UNNAMED check flags the file that was SCRIPT.md before the rename (o10L344): is_library_record, the one
    # record-text test (o9L1208), which still counts a SCRIPT.md (_record_set).
    unlabelled_final = (is_library_record(path) and how == "declared"
                        and not _final_label(path))
    if how in ("folder-GUESS", "unknown") or unlabelled_final:  # "folder-label" is named
        findings.append(("UNNAMED", "no SCRIPT-<SC|AT>-<NIGHT|DAY>- prefix; type is a %s" % how))

    # o9:Q6: the runtime of an SC script depends on whether its opening is negative,
    # so the script must say. Report-only on a library final (the severity rule
    # above); a draft or new version without it is refused.
    if kind == "SC" and opening not in ("negative", "positive"):
        final_sink.append(("OPENING-UNMARKED",
                           "no `## Opening: negative|positive` header%s; timed as positive (x6),"
                           " %s if the opening is negative"
                           % ((" (read %r)" % opening) if opening else "",
                              fmt(spoken_if_negative / WPM * 60.0))))

    # o3 ruling on W182 (a): a daytime AT that declares `## Length: quick reset` skips the 8:00
    # floor, and the run says so. Any other script with the header keeps its floor.
    floor_skipped = None
    if kind == "AT" and day and lo and is_quick_reset(raw):
        floor_skipped = ("`## Length: quick reset`: the %s daytime AT floor (LN3) was skipped"
                         % fmt(lo))
    # Paired AT (PAIR_RE block): the floor is judged on this file's runtime plus its partner's.
    pair = None
    judged = total
    pid = pair_of(raw) if kind == "AT" else None
    if pid:
        ppath, n_found = find_pair(path, pid)
        if ppath:
            psecs = pair_runtime(ppath)
            judged = total + psecs
            own = _header_id(path) or os.path.splitext(os.path.basename(path))[0]
            more = ""
            if n_found > 1:
                more = " (%d files match %s; used %s)" % (
                    n_found, pid, os.path.relpath(ppath, repo_root_for(path)).replace("\\", "/"))
            pair = {"id": pid, "path": ppath, "candidates": n_found,
                    "secs": round(psecs, 1), "combined_secs": round(judged, 1),
                    "line": "AT length floor judged on the pair %s + %s: %s + %s = %s%s"
                            % (own, pid, fmt(total), fmt(psecs), fmt(judged), more)}
        else:
            warnings.append(("PAIR-NOT-FOUND",
                             "`## Pair: %s`: script %s not found in this script's library tree; "
                             "the AT length floor is judged on this file alone" % (pid, pid)))
    if lo and judged < lo and not floor_skipped:
        if pair:
            final_sink.append(("UNDER-FLOOR", "%s - the pair %s + %s (%s) is below the %s-%s "
                               "floor band" % (fmt(judged), fmt(total), fmt(pair["secs"]), pid,
                                               fmt(lo), fmt(hi))))
        else:
            final_sink.append(("UNDER-FLOOR","%s - below the %s-%s floor band"
                             % (fmt(total), fmt(lo), fmt(hi))))
    if cap and total > cap:
        if kind == "AT":
            # LN3: AT has "a minimum and no real maximum" (the human, o8:D6); the 1800 s figure is
            # "a prompt to look, not a failure". A warning, so the run's exit code is unchanged.
            warnings.append(("OVER-CAP", "%s is over %s: AT has no real maximum (LN3); look, "
                             "do not cut for length" % (fmt(total), fmt(cap))))
        else:
            findings.append(("OVER-CAP", "%s exceeds the %s maximum" % (fmt(total), fmt(cap))))

    return {
        "path": os.path.relpath(path, WORKSPACE).replace("\\", "/"),
        "type": kind, "type_source": how, "daytime": day,
        "words": words,
        "spoken_words": spoken_words,
        "opening": opening,
        "opening_from": opening_from,
        "first_paragraph_words": first_words,
        "secs_if_negative": (round(spoken_if_negative / WPM * 60.0, 1) if kind == "SC" else None),
        "statements": statements,
        "statements_unfolded": statements_unfolded, "count_blocks": count_blocks,
        "connector_folds": connector_folds, "statements_pre_connector": statements_pre_connector,
        "notes": notes["notes"],
        "cues": len(cues), "pause_secs": pause_secs,
        "total_secs": round(total, 1), "floor_lo": lo, "floor_hi": hi, "cap": cap,
        "floor_skipped": floor_skipped,
        "pair": pair,
        "findings": findings,
        "warnings": warnings,
    }


def fmt(sec):
    return "%d:%02d" % (int(sec // 60), int(sec % 60))


# The checklist box each check enforces (o9L1231). The code printed here is the box's own code
# in SCRIPT-CHECKLIST-*.md / AUTHORING-CHECKLIST-UNIVERSAL.md; the TEXT is fetched from the box at run
# time through rule_codes_index, so it never drifts from the checklist. A code with no entry has
# no box (UNCLOSED-NOTE, UNOPENED-NOTE, UNCLOSED-STRIKE, TYPE-DRIFT, UNNAMED) and prints nothing extra.
CHECK_BOX = {
    "BANNED-ALWAYS": "BW2", "US-SPELLING": "US1", "BANNED-SLEEP": "SG5",
    "S1-SOFT-BUDGET": "HV3", "R1-ADJ-PAIR": "HV2", "EV1-ARRIVES": "BW6",
    "TY7-THE-BREATHING": "TY7", "TY7-THE-HEARTBEAT": "TY7", "TY7-THE-PULSE": "TY7",
    "TY7-THE-BODY": "TY7",
    "SC90-MENU-END-PERIOD": "FM2", "SC90-MENU-DOUBLED-STOP": "FM2",
    "MENU-SPACE-BEFORE-PERIOD": "FM2",
    "HV9-SHAPE-REPEAT": "HV9", "HV9-OPTION-SET": "HV9", "HV9-GENERIC": "HV9", "BW7-RUN": "BW7", "HV6-REUSE": "HV6",
    "HV4": "HV4", "BW8": "BW8", "HV10": "HV10", "AR9-TAIL": "AR9", "AR9-AND-TAIL": "AR9", "BW11": "BW11",
    "HV6-PARAPHRASE": "HV6",
    "DD0-DEFAULT-WORD": "DD0", "TY7-HEAD-IT": "TY7", "HV12-OF-ME": "HV12", "HV8-RARE-REPEAT": "HV8",
    "RF5-FINALLY": "RF5",
    "NON-ASCII": "FM1", "NO-PAUSE-RAMP": "PA1", "PAUSE-CUE-LEGACY": "PA0",
    "OPENING-UNMARKED": "LN5", "UNDER-FLOOR": "LN3", "OVER-CAP": "LN3",
    "PAIR-NOT-FOUND": "LN3",
}


def box_code_for(code, detail=""):
    """The box code a hit enforces, or None. BANNED-ALWAYS names three boxes by the word hit."""
    if code == "BANNED-ALWAYS":
        w = (detail or "").lower()
        if w.startswith("affirmation"):
            return "BW1"
        if w.startswith("honestly"):
            return "US3"
    return CHECK_BOX.get(code)


def box_block(box, seen):
    """Indented text of one box, or "" when it is already printed, has no box, or cannot be read."""
    if not box or box in seen:
        return ""
    seen.add(box)
    try:
        import rule_codes_index
        b = rule_codes_index.load(rule_codes_index.Path(WORKSPACE))["codes"].get(box)
    except Exception:
        return ""
    if not b:
        return ""
    head = "    -- box %s (%s line %d) --" % (box, b["file"], b["line"])
    return "\n".join([head] + ["      " + ln if ln.strip() else "" for ln in b["text"].split("\n")])


def _clip(s, n):
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n - 3] + "..."


def arrival_audit(targets):
    """Report the five UNRULED [DD1] arrival-shape verbs. NOT a finding.

    the human's `Experience Verbs.txt`: "NOT YET TESTED - do not ban these on an
    agent's say-so ... A false positive here costs the behaviour, not just the
    catch." So this prints evidence to rule FROM; it never asserts a defect.
    """
    total = 0
    for name in sorted(ARRIVAL_AUDIT_VERBS):
        rows = []
        for t in targets:
            raw = read_script(t)[0]
            for phrase, head in arrival_hits(spoken_prose(raw, render_all_options),
                                             ARRIVAL_AUDIT_VERBS[name]):
                rows.append((os.path.basename(t), head, phrase))
        total += len(rows)
        print("  [%s] %d inanimate-subject occurrence(s)" % (name, len(rows)))
        for f, head, phrase in rows[:12]:
            print("      %-44s %-14s %s" % (f[:44], head, _clip(phrase, 60)))
        if len(rows) > 12:
            print("      ... %d more" % (len(rows) - 12))
    print()
    print("  %d occurrence(s) across %d file(s). UNRULED - evidence only, not"
          % (total, len(targets)))
    print("  findings. Only arrive/arrives/arrived/arriving is a ruled ban (BW6).")
    return 0


def reuse_report(targets, json_out=False):
    """Every HV6-REUSE collision for each target: other file, gram count, phrases.

    The plain run prints one line per colliding file with only the longest
    phrase; this prints every phrase, so a writer can see all of what to change.
    A report, so it exits 0; the plain run is what fails on HV6-REUSE.
    """
    joined = {}

    def rel(p):
        return os.path.relpath(p, WORKSPACE).replace("\\", "/")

    def owners(other, phrase, fam, toks):
        """The family members that really hold the phrase. `other` is only the
        family's representative, and a phrase may sit in a different version
        (found by o3L15: AT-03's rewrite was named for phrases that only the
        featured-corpus 03-the-helicopter-landing.md, in its family, holds)."""
        f = fam.get(os.path.abspath(other))
        members = [p for p, g in fam.items() if g == f]
        if len(members) < 2:
            return [rel(other)]
        out = []
        for m in members:
            if m not in joined:
                joined[m] = " %s " % " ".join(toks[m])
            if " %s " % phrase in joined[m]:
                out.append(rel(m))
        return sorted(out) or [rel(other)]

    rows = []
    for t in targets:
        _idx, fam, toks = reuse_index_for(t)
        for other, n, phrases in reuse_detail(t):
            rows.append({
                "file": rel(t), "other": rel(other),
                "family_size": sum(1 for g in fam.values()
                                   if g == fam.get(os.path.abspath(other))),
                "grams": n, "phrases": phrases,
                "first": reuse_first_note(t, other, phrases[0]),
                "owners": {ph: owners(other, ph, fam, toks) for ph in phrases}})
    if json_out:
        print(json.dumps(rows, indent=2))
        return 0
    seen = set()
    for t in targets:
        rel = os.path.relpath(t, WORKSPACE).replace("\\", "/")
        mine = [r for r in rows if r["file"] == rel]
        print("%s  -  %d shared %d-gram(s) with %d other script(s)"
              % (rel, sum(r["grams"] for r in mine), REUSE_N, len(mine)))
        for r in mine:
            fam_note = (" (family of %d; each phrase names the file that holds it)"
                        % r["family_size"]) if r["family_size"] > 1 else ""
            print("    %3d  %s%s" % (r["grams"], r["other"], fam_note))
            print("         longest phrase: %s" % r["first"])
            for ph in r["phrases"]:
                who = r["owners"][ph]
                tag = "" if who == [r["other"]] else "   <- in %s" % ", ".join(who)
                print("           \"%s\"%s" % (ph, tag))
            seen.add(tuple(sorted((rel, r["other"]))))
    print()
    print("  %d target(s), %d colliding pair(s), corpus of %d script(s)."
          % (len(targets), len(seen),
             len((reuse_index_for(targets[0]) if targets else reuse_index())[1])))
    return 0


def run_audit(targets):
    """Every BW7 warning across the corpus. Advisory - exit 0 regardless."""
    total = 0
    files = 0
    for t in targets:
        raw = read_script(t)[0]
        hits = run_process_hits(spoken_prose(raw, render_all_options))
        if not hits:
            continue
        files += 1
        total += len(hits)
        print("  %s" % os.path.relpath(t, WORKSPACE).replace("\\", "/"))
        for phrase, why in hits:
            print("      %-38s %s" % (why[:38], _clip(phrase, 84)))
    print()
    print("  %d warning(s) in %d of %d script(s). SOFT ban (the human, 2026-09-04)"
          % (total, files, len(targets)))
    print("  - advisory. A human judges each one; none of them fails a run.")
    return 0


# ==========================================================================
# SELF-TEST
# ==========================================================================
# ⛔ EVERY CASE BELOW FAILS AGAINST THE PRE-2026-09-04 GATE. That is the only
# property that makes it a test. `grep -c arriv check_script.py` returned 0 on
# origin/main while a lane reported "arrival verbs: all zero" as a PASSING
# screen - a test that passes both before and after would have certified that
# same nothing.
#
# ⭐ THE GENERAL FORM: ask what the instrument would report if the thing it
# measures were absent entirely. If the answer is "the same thing it reports
# now", it is measuring nothing.

_EV1_MUST_FIRE = [
    "My mental clarity arrives ready and sharp.",   # the human's own 2026-09-04 ruling
    "Sleep arrives on its own tonight.",
    "The relaxation arrives before I ask for it.",
    "The night arrives easily.",                    # cliche-catalog #18, explicit
    "I love the feel of it arriving.",              # Experience Verbs.txt, BAD
    "The energy arrived all at once.",
    "Because the conditions don't arrive on their own.",   # negated aux
]

# These nine were a MUST-NOT-FIRE list until 2026-09-24. They encoded the soft
# gate (a CAN_ARRIVE subject allow-list), which the human replaced with a word ban on
# 2026-09-15 ("Ban it outright."). Commit 75e303bf hardened the screen and left
# this fixture behind, so --selftest reported 9 failures for nine days while the
# screen itself was correct. o8's W57 sweep, Finding 9.
#
# They now MUST FIRE, and that is the point of keeping them: every one of them
# passes silently under the old allow-list, so if anyone re-adds a subject
# allow-list this list goes red. BW6 in SCRIPT-CHECKLIST-4B-BANNED-WORDS.md: "Re-adding a
# subject allow-list is what produced the 130."
#
# Two of them are the human's 2026-09-21 carve-outs (a physical object arriving). The
# gate fires on those too, ON PURPOSE - BW6: "it will fire on both carve-outs.
# That is the correct behaviour, not a bug to fix." The flag is the prompt to
# ask him. Do NOT move any of these back to a must-not-fire list.
_EV1_MUST_FIRE_FORMERLY_ALLOWED = [
    "I arrive at the field with time to spare.",
    "The bus arrives at seven.",
    "A letter arrives for me every Tuesday.",
    "Dinner arrives at the table.",
    "When the time arrives, I will already know.",
    "My family arrives tomorrow.",                  # -ly word that is not an adverb
    "They will finally arrive.",
    "I'm arriving at the same place I always do.",  # contraction
    "It feels like arriving.",                      # object of a preposition
]

# BW7 - the SOFT ban on `run` as a process being executed.
#
# The four MUST-FIRE cases are the human's own, verbatim from the ruling and from the
# AT-S5 pilot he read them in. The ALLOWED cases are also his, and they matter
# just as much: a lane that reads BW7 as a word ban strips them, and they are
# the human-sounding uses. A screen that silenced the first list and the second
# equally - which is what `grep run` does - would be worse than no screen.
_BW7_MUST_FIRE = [
    "everything that actually runs my sleep came in here inside my own body",
    "I am lightly aware that I am running the same order I always run",
    "It is a route I know by heart, and I could run the whole thing half asleep",
    "There is no part of it left for me to run.",
    "My body knows how to run the process without me.",
    # A reflexive that is NOT a body process still fires (o8:D20 exempts only those).
    "My mind runs itself tonight.",
    "The night runs itself.",
    # Measured in the corpus - the ordinary-object list must not swallow these.
    "I set the want, I run the math, and enough is enough.",
    "The part of me that runs sleep is out of practice.",
    "my chest, my hips - the chain running itself",
]

_BW7_MUST_NOT_FIRE = [
    # the human: a machine operating.
    "busy with their own small furnace, which has been running the whole time",
    "The car is still running in the driveway.",
    # the human: something travelling along a path.
    "The release runs from my jaw up into my temples, and I follow it there.",
    "That looseness runs down my neck and out into my shoulders.",
    "That release runs down my spine like warm water poured at the top of it.",
    "the pain runs from my hip all the way to my foot",
    "warmth running down my arms and over my shoulders",
    "the things that run through our minds at night",
    # AT-S10a, measured clean and must stay clean.
    "the difference runs all the way up to my elbows",
    "That release runs out along both arms and down the length of my spine.",
    "heaviness running from my hips right down to my ankles",
    "I let the out-breath run a good deal longer than the in-breath",
    "That trade has been running for a while and it is going to run all night long.",
    # RULED here 2026-09-04 - the runs-hot / runs-cold state idiom, both allowed.
    "My forehead stays clear and cool while the rest of me runs warm.",
    "That beat runs slower than my breathing, and the two of them slow together.",
    # The noun and the adjective, not the verb.
    "I went out for a run this morning.",
    "In the long run it makes no difference.",
    "the years against me, a running tally of blame",
    # Things a person ordinarily runs, measured in the corpus.
    "I can run a company, a team, and a household.",
    "hand it the afternoon, let it run the show",
    "the best shift I run",
    "Plenty of people I respect have run themselves down to coals.",
    # Parse boundaries, all three measured as false positives.
    "it earns its keep, and it can keep running - this was never in doubt",
    "the only part still running is a line of code",
    "It has already run thousands of nights without me.",
    # State and manner complements measured in the corpus.
    "I can run tight when tight is what the week asks for.",
    "borrowed alertness trained my system to run tired",
    "the part of life that only runs live",
    # The fronted-object walk must not reach into the phrase before it.
    "it collected me thousands of nights running while I was busy",
    "The switch is mine and I say when it runs.",
    # RULED the human 2026-09-24 (o8:D20) - a body process running by itself is exempt.
    # This line was in the MUST-FIRE list until then; "The breathing" now fails TY7.
    "The breathing runs itself now.",
    "My breathing runs itself now, and it runs better than I ever managed by hand.",
    "and my heartbeat runs itself all night.",
]

_HV6_SHARED = ("I hand the whole of it over to the morning and I sleep. "
               "Nothing waiting out there needs one more minute of me.")
_HV6_CANON = "My heartbeat is calm and regular."


def _selftest():
    import tempfile
    fails = []

    def check(ok, label):
        print("  %s %s" % ("[OK]  " if ok else "[FAIL]", label))
        if not ok:
            fails.append(label)

    print("EV1 - arrival verb with an inanimate subject")
    for s in _EV1_MUST_FIRE:
        check(bool(arrival_hits(s)), "fires: %s" % s)
    for s in _EV1_MUST_FIRE_FORMERLY_ALLOWED:
        check(bool(arrival_hits(s)), "fires (the soft gate allowed it): %s" % s)

    print()
    print("BW7 - `run` as a process being executed (SOFT ban - advisory)")
    for s in _BW7_MUST_FIRE:
        check(bool(run_process_hits(s)), "fires:  %s" % _clip(s, 72))
    for s in _BW7_MUST_NOT_FIRE:
        check(not run_process_hits(s), "silent: %s" % _clip(s, 72))
    check(bool(run_process_hits(render_all_options(
              MARKER + "\n[[nothing here | I could run the whole thing]]"))),
          "a hit in dropdown option 2 is seen (default_render drops it)")
    check(not run_process_hits(default_render(
              MARKER + "\n[[nothing here | I could run the whole thing]]")),
          "  and the default render is where it was invisible")

    print()
    print("HV6 - cross-script phrase reuse")
    check(_BARRIER in reuse_tokens(MARKER + "\n" + _HV6_CANON),
          "a canonical AT formula is masked out of the gram stream")
    check(not _grams(reuse_tokens(MARKER + "\n" + _HV6_CANON)),
          "a canonical AT formula alone yields zero grams")

    tmp = tempfile.mkdtemp(prefix="wl-gate-")
    def w(name, body):
        p = os.path.join(tmp, name)
        io.open(p, "w", encoding="utf-8").write(
            "# T\n\n%s\n\n%s\n" % (MARKER, body))
        return p

    a = w("SCRIPT-SC-NIGHT-alpha.md", _HV6_SHARED)
    b = w("SCRIPT-SC-NIGHT-beta.md", _HV6_SHARED)
    c = w("SCRIPT-SC-NIGHT-gamma.md",
          "Every part of this one is written fresh and shares nothing at all.")
    d = w("SCRIPT-SC-NIGHT-alpha-v2.md", _HV6_SHARED)   # a VERSION of alpha
    e = w("SCRIPT-AT-NIGHT-delta.md", _HV6_CANON)
    f = w("SCRIPT-AT-NIGHT-epsilon.md", _HV6_CANON)

    global _REUSE
    saved, _REUSE = _REUSE, _build_reuse_index([a, b, c, d, e, f])
    try:
        check([h[0] for h in reuse_hits(a)] == [b],
              "two DIFFERENT scripts sharing a body line collide")
        check(reuse_hits(c) == [], "a script sharing nothing is clean")
        check([h[0] for h in reuse_hits(d)] == [b],
              "alpha-v2 collides with beta but NOT with its own alpha")
        check(reuse_hits(e) == [],
              "two AT scripts sharing only the canonical formula are clean")
        check(reuse_hits(f) == [], "  (and symmetrically)")
        hits = reuse_hits(a)
        check(hits and "hand the whole of it over" in hits[0][1][0],
              "the report names the shared phrase")
    finally:
        _REUSE = saved
        for p in (a, b, c, d, e, f):
            try:
                os.remove(p)
            except OSError:
                pass
        try:
            os.rmdir(tmp)
        except OSError:
            pass

    print()
    if fails:
        print("  %d FAILURE(S)" % len(fails))
        return 1
    print("  all self-tests passed")
    return 0


def main():
    ap = argparse.ArgumentParser(description="product-app script gate (marker-scoped)")
    ap.add_argument("path", nargs="?", help="one script; refused if it is not a script")
    ap.add_argument("--list", action="store_true", help="show discovered targets and stop")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-box-text", action="store_true",
                    help="single-script run: do not print the checklist box text under each hit "
                         "(default prints it, once per box)")
    ap.add_argument("--arrival-audit", action="store_true",
                    help="report the five UNRULED [DD1] arrival-shape verbs "
                         "(answers/rises/comes/snaps/sets) as evidence, not findings")
    ap.add_argument("--run-audit", action="store_true",
                    help="every BW7 `run` warning across the corpus, with the "
                         "phrase - advisory, never a finding")
    ap.add_argument("--selftest", action="store_true",
                    help="prove the EV1, BW7 and HV6 screens on fixed cases")
    ap.add_argument("--reuse-report", action="store_true",
                    help="every HV6-REUSE collision with its gram count and every "
                         "shared phrase (o3:W5); exits 0")
    ap.add_argument("--corpus", action="append", metavar="DIR",
                    help="compare HV6-REUSE against only these folders (repeatable; "
                         ".md and .txt scripts). Default: every script in the "
                         "workspace plus the approved .txt AT scripts")
    ap.add_argument("--corpus-at-sc", action="store_true",
                    help="use REUSE_AT_SC_CORPUS (approved AT, o8 pilots, new AT "
                         "scripts) as the HV6-REUSE corpus")
    ap.add_argument("--write-reuse-baseline", action="store_true",
                    help="freeze today's HV6-REUSE overlaps of the library finals into "
                         "%s in this checkout (o3:W35)" % REUSE_BASELINE_NAME)
    ap.add_argument("--replace-baseline", action="store_true",
                    help="let --write-reuse-baseline overwrite an existing baseline file")
    ap.add_argument("--allow-worktree", action="store_true",
                    help="let --write-reuse-baseline, --readd-reuse-rows or --drop-stale-reuse-rows run in a linked git "
                         "worktree")
    ap.add_argument("--readd-reuse-rows", metavar="ID",
                    help="a script deliberately put back to an older version: add to %s only "
                         "the current overlap rows naming this `## ID:` (either column), in the "
                         "same commit (o3:W35)" % REUSE_BASELINE_NAME)
    ap.add_argument("--drop-stale-reuse-rows", action="store_true",
                    help="delete from %s every row that is no longer a current overlap (each "
                         "direction judged on its own); run it in the commit that fixes an overlap (o3:W35, "
                         "o10L350 SF-A)" % REUSE_BASELINE_NAME)
    args = ap.parse_args()

    if args.drop_stale_reuse_rows:
        if is_linked_worktree(WORKSPACE) and not args.allow_worktree:
            print("[REFUSED] %s is a linked git worktree. Drop stale rows from the main checkout, or"
                  " pass --allow-worktree when this worktree's library is the one to read."
                  % WORKSPACE)
            return 2
        if not os.path.exists(reuse_baseline_path(WORKSPACE)):
            print("[REFUSED] %s does not exist; there is no freeze to drop rows from."
                  % reuse_baseline_path(WORKSPACE))
            return 2
        dropped, left = drop_stale_reuse_rows(WORKSPACE)
        print("dropped %d stale row(s) from %s; %d row(s) left"
              % (dropped, reuse_baseline_path(WORKSPACE), left))
        return 0

    if args.readd_reuse_rows:
        if is_linked_worktree(WORKSPACE) and not args.allow_worktree:
            print("[REFUSED] %s is a linked git worktree. Re-add rows from the main checkout, or"
                  " pass --allow-worktree when this worktree's library is the one to read."
                  % WORKSPACE)
            return 2
        if not os.path.exists(reuse_baseline_path(WORKSPACE)):
            print("[REFUSED] %s does not exist; there is no freeze to re-add rows to."
                  % reuse_baseline_path(WORKSPACE))
            return 2
        try:
            added, total = readd_reuse_rows(WORKSPACE, args.readd_reuse_rows)
        except ValueError as e:
            print("[REFUSED] %s. Rows are re-added only from the freeze commit's own index." % e)
            return 2
        print("re-added %d row(s) naming %s (%d current overlap row(s) name it) to %s"
              % (added, args.readd_reuse_rows, total, reuse_baseline_path(WORKSPACE)))
        return 0

    if args.write_reuse_baseline:
        # A worktree's library may be behind or ahead of main, and a baseline written from it
        # would freeze the wrong overlaps. Refused unless asked for by name.
        if is_linked_worktree(WORKSPACE) and not args.allow_worktree:
            print("[REFUSED] %s is a linked git worktree. Write the reuse baseline from the main"
                  " checkout, or pass --allow-worktree when this worktree's library is the one"
                  " to freeze." % WORKSPACE)
            return 2
        # o10L328 S2: the freeze is written once. A second write would freeze reuse added since.
        if os.path.exists(reuse_baseline_path(WORKSPACE)) and not args.replace_baseline:
            print("[REFUSED] %s already exists. The freeze is written once (o3:W35); pass"
                  " --replace-baseline only on o3's word." % reuse_baseline_path(WORKSPACE))
            return 2
        n = write_reuse_baseline(WORKSPACE)
        print("wrote %d entries to %s" % (n, reuse_baseline_path(WORKSPACE)))
        return 0

    if args.selftest:
        return _selftest()

    if args.path:
        p = os.path.abspath(args.path)
        if in_private_folder(p):
            print("[REFUSED] the file is inside a folder named %s. Those folders hold text"
                  " customers and email senders wrote, and no session opens them"
                  " (private_folders.py)." % " or ".join(PRIVATE_NAMES))
            return 2
        if not os.path.exists(p):
            print("[REFUSED] no such file: %s" % args.path)
            return 2
        if not is_script(p):
            print("[REFUSED] not a script file: %s" % args.path)
            print()
            print("  A script is identified by the declared header boundary:")
            print("      %s" % MARKER)
            print("  This file does not carry it, so these gates do not apply to it.")
            print()
            print("  This refusal is deliberate. The ASCII and banned-word gates are")
            print("  TTS-pipeline requirements for text a MEMBER HEARS. Internal docs")
            print("  are explicitly allowed non-ASCII (the human, 2026-08-06), and a rules")
            print("  doc WILL fail them by design.")
            print()
            print("  On 2026-08-11 a lane ran the ASCII gate against")
            print("  AUTHORING-CHECKLIST.md and reported the result as a defect.")
            print("  The gate was correct; the target was wrong. Hence this check.")
            print()
            print("  If you are here to 'clean up' a doc so it passes: STOP. You would")
            print("  be degrading a correct artifact to satisfy a test that was never")
            print("  about it.")
            return 2
        targets = [p]
    else:
        targets = discover(WORKSPACE)

    # The checked script's own tree (repo_root_for): relative --corpus folders and the family
    # links are read there, not in the checkout this checker file sits in.
    base = repo_root_for(targets[0]) if args.path else WORKSPACE
    if args.corpus or args.corpus_at_sc:
        roots = list(args.corpus or []) + (REUSE_AT_SC_CORPUS if args.corpus_at_sc else [])
        corpus = discover_corpus(roots, base=base)
        if not args.path:
            targets = corpus
        # The checked file always joins the index, so a file outside the named
        # folders is still compared against them rather than silently skipped.
        global _REUSE
        _REUSE = _build_reuse_index(sorted(set(corpus) | set(targets)),
                                    family_paths=default_reuse_corpus(base))

    if args.reuse_report:
        return reuse_report(targets, json_out=args.json)

    if args.list:
        for t in targets:
            print("  %s" % os.path.relpath(t, WORKSPACE).replace("\\", "/"))
        print()
        print("  %d script file(s) discovered by marker." % len(targets))
        print("  EXCLUDED before any check ran: %d directory branch(es) "
              "(%s, any .wt-*, %s in any letter case, and any junction or"
              " symbolic link), %d .md file(s) carrying no header marker, and %d .md"
              " symbolic link(s), not followed."
              % (DISCOVER_EXCLUDED["dirs"], ", ".join(sorted(SKIP_DIRS)),
                 " and ".join(PRIVATE_NAMES),
                 DISCOVER_EXCLUDED["md_without_marker"], DISCOVER_EXCLUDED["md_links"]))
        return 0

    if args.arrival_audit:
        return arrival_audit(targets)

    if args.run_audit:
        return run_audit(targets)

    results = [analyse(t) for t in targets]

    if args.json:
        print(json.dumps(results, indent=2))
        return 0

    bad = 0
    warned = 0
    print("%-52s %-4s %6s %7s %6s %8s %s"
          % ("file", "type", "subst", "spoken", "cues", "RUNTIME", "findings"))
    print("-" * 112)
    for r in results:
        n = len(r["findings"])
        wn = len(r["warnings"])
        if n:
            bad += 1
        if wn:
            warned += 1
        spoken = r["spoken_words"]
        col = ("%d" % n) if n else "clean"
        if wn:
            col += "  (+%d warn)" % wn
        print("%-52s %-4s %6d %7d %6d %8s %s"
              % (r["path"][-52:], r["type"], r["words"], spoken, r["cues"],
                 fmt(r["total_secs"]), col))
        if r.get("floor_skipped"):
            # Not a "[CODE]" line: readers that take hit lines by their "[" are not affected.
            print("    note: %s" % r["floor_skipped"])
        if r.get("pair"):
            print("    note: %s" % r["pair"]["line"])
        for code, detail in r["warnings"]:
            if code == "PAIR-NOT-FOUND":
                print("    note: %s" % detail)
    print()
    print("  subst = words as written (the base substrate) | spoken = what is voiced")
    print("  SC spoken = subst x6 (mirror x2 * tripling x3); a NEGATIVE opening")
    print("  (## Opening: negative) counts its first paragraph once (o9:Q6). RUNTIME is the EXPANDED")
    print("  figure and IS the script's length. AT is singlet; its runtime adds pauses.")
    print()
    print("  %d script(s) checked, %d with findings, %d with warnings."
          % (len(results), bad, warned))
    print("  A WARNING is advisory - a soft ban a human judges. It never fails")
    print("  the run. Only findings do.")
    if len(targets) > 1:
        print("  Run with a single path for detail, or --json for everything.")
    else:
        # The box text sits on indented lines under the hit, never on a line that starts with
        # "[", so a reader that takes the "[CODE]" and "[warn CODE]" lines is not affected.
        # --no-box-text prints none (the output validator hook passes it: it keeps only the
        # last 40 lines, which box text would crowd the hits out of).
        seen = set()
        for tag, hits in (("", results[0]["findings"]), ("warn ", results[0]["warnings"])):
            for code, detail in hits:
                print("    [%s%s] %s" % (tag, code, detail))
                if not args.no_box_text:
                    blk = box_block(box_code_for(code, detail), seen)
                    if blk:
                        print(blk)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
