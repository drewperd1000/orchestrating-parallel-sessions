r"""The `<!-- training-version: -->` marker finally has a reader.

<!-- subject: script-writing -->

MASTER-TRAINING-DOC.md Section 7 has said since it was written that the marker "HAS NO READER
YET, and that is owed rather than optional". This is it.

TWO ARMS, because a missing header and a false header fail differently:

  ARM 1 - THE HEADER IS PRESENT AND COMPLETE. A version artifact carries, under its title:

      <!-- training-version: v3 | waypoint | not a specimen -->
      **This version fixes:** <the delta>
      **Known unfixed (my read, not his):** <the agent's own read, or: not assessed>

    ⚠️ `not assessed` IS A VALID VALUE and a blank field is not. Section 4: "A blank
    field is silence again, one level down: an item absent from the list reads as fixed." So the
    test is that the field is PRESENT AND NON-EMPTY, never that it lists something.

  ARM 2 - `final` WAS NOT SELF-ASSERTED. Section 4: "ONLY THE HUMAN MAKES A VERSION `final`. AN AGENT
    NEVER SETS THAT FIELD." Arm 1 catches a missing header. It cannot catch the failure this
    line was added for - an agent marking its own draft `final` and thereby granting itself DE2
    protection, which o1 notes "is the cheaper one to make, because nobody has to be misread for
    it to happen."

⭐ WHAT MAKES ARM 2 DECIDABLE, WHICH o1 FLAGGED AS POSSIBLY THE HARD PART. The question
reads as "was this file written in an agent's turn?", which needs provenance nobody has. But it
does not have to be asked that way. **the human's rulings arrive through him and land; an agent's
claim arrives as an uncommitted change.** So compare the file on disk to the same file on
`origin/main`:

    origin says final          -> the human landed it. Fine, whatever disk says.
    origin says waypoint, disk says final   -> something set it since. ALARM.
    file is not on origin at all, disk says final -> born claiming final. ALARM.

No provenance guess, no authorship heuristic, no trailer parsing. Git already holds the one
fact that matters, which is whether the claim has been through him.

⛔ BUT ARM 2 ONLY WORKS AT THE MOMENT OF THE WRITE, AND I FIRST BUILT IT AS A SCAN. o1
caught it: *"As a periodic scan it cannot fire on the case it exists for. An agent granting
itself DE2 protection lands the file like any other work ... and once it lands, disk and ref
agree."* A scan can only catch a `final` sitting unlanded on disk, which is the least likely
way this goes wrong. **The two shapes look identical in description and only one of them
fires.** So Arm 2 runs as `training_header.py hook` on PreToolUse Write/Edit, where the ref has
not moved yet. The scan keeps Arm 1, whose findings are all properties of a file at rest.

⚠️ THE KNOWN FALSE POSITIVE, stated rather than hidden: the human edits a file to `final`
and a gate runs before he commits. It fires. That is acceptable because the human does not run gates,
agents do - and the refusal text says what to do if it was him. A check that cannot fire on the
case it exists for would be the worse trade.
"""
import argparse
import collections
import io
import json
import os
import re
import subprocess
import unicodedata
try:
    from no_window import flags
except ImportError:  # a partial delivery must not take the tool down
    def flags(extra=0):
        return extra
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# <!-- training-version: v3 | waypoint | not a specimen -->
HEADER = re.compile(
    r"<!--\s*training-version:\s*([^|>]+?)\s*\|\s*([^|>]+?)\s*(?:\|\s*([^>]*?)\s*)?-->",
    re.I)

# The two prose fields. The name of the second one is LOAD-BEARING and changed on 2026-09-22:
# it was "Still unfixed and known", which asks an agent to enumerate what THE HUMAN left unfixed -
# the exact inference the training doc exists to prevent. Matching the old name would quietly
# accept the wording o1 got removed, so only the new one is accepted.
# ⛔ THE SEPARATOR AFTER THE LABEL MUST NOT CROSS A LINE. `\s` matches a newline, so
# with `\s*` an EMPTY field walked past the end of its own line and captured the next
# paragraph - measured, it returned "Body." and reported nothing. That is precisely the
# failure o1 had this field reworded to prevent: "A blank field is silence again, one level
# down." The check built to catch it was passing it by lending it unrelated prose.
#
# ⭐ It fires ONLY on the empty case - a filled field captures its own text and looks
# correct - so every well-formed example passed and the defect was invisible from the happy
# path. Caught by its own test asserting the empty case, 2026-09-22.
FIXES = re.compile(r"^[ \t]*\*\*This version fixes:\*\*[ \t]*(.*)$", re.I | re.M)
UNFIXED = re.compile(r"^[ \t]*\*\*Known unfixed \(my read, not his\):\*\*[ \t]*(.*)$",
                     re.I | re.M)
OLD_UNFIXED = re.compile(r"^\s*\*\*Still unfixed(?: and known)?:?\*\*", re.I | re.M)

# \u2b50 A `owner-rewrite` SUFFIX **IS** THE TRAINING LOOP - nothing else in the workspace is
# named that way - so it is in scope wherever it lives. Section 5 records five spellings in use.
REWRITE_SUFFIX = re.compile(r"(?i)[-_ ]owner[-_ ]?rewrite\b")

# W235 (o10L298): the script-library file names `<NAME> v<N> <AUTHOR>.md`, shared by the library tools and the
# OrchDoc Reader (design: pwa-orchestration-o10/o10L292-DESIGN.md section d). NAME is a Label (SC-Sl-D-01-001)
# or `<TYPE>-<ID>` (AT-o5-0031). AUTHOR is THE HUMAN, an orchestrator code (o8) or `agent`. Optional endings, in this
# order: ` duplicate`, `-PROCESS-LOG` (a log), ` (in app)` (the text that is in the app). A note is
# `<NAME> notes - <old name>.md`.
#
# o10L431 (the human, 2026-10-11): the name also carries a TITLE, so he can tell at a glance what a file holds:
# `<NAME> <Title-Words> v<N> <AUTHOR>...md`, e.g. `SC-Sl-D-01-001 High-Achievers-Rest v1 agent.md`. The separator between
# NAME and title is ONE SPACE. NAME is every character up to the first space (a NAME never holds a space, and an
# unlabelled NAME such as `SC-fs1011-sonnet55` or `AT-o8L145-0001` holds hyphens of its own, so a hyphen between NAME and
# title would make the NAME ambiguous). The title has no space of its own (words joined by hyphens), so it is the one
# token between NAME and ` v<N> `. A name with no title (the older shape) still parses, with the same name_key.
# A title token may not itself look like a version (`v2 `): title_slug capitalises every word, and the lookahead enforces it.
_SN_NAME = r"(?P<name>[A-Z]{2,4}-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)"
_SN_TITLE = r"(?: (?P<title>(?!v[1-9]\d* )[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*))?"
SCRIPT_NAME = re.compile(
    # o10L310: v<N> numbers from 1 with no leading zero (`v03` and `v0` are outside the shape, so two files cannot
    # claim one version), and \Z rather than $ (`$` also matches before a trailing newline).
    # o10L431: `-REVIEW` (a lane's review of a version, kept beside it) is read as a log, like `-PROCESS-LOG`.
    "^" + _SN_NAME + _SN_TITLE + r" v(?P<ver>[1-9]\d*) (?P<author>THE HUMAN|agent|o\d+)(?P<dup> duplicate)?"
    r"(?P<log>-PROCESS-LOG|-REVIEW)?(?P<app> \(in app\))?\.md\Z")
SCRIPT_NOTE = re.compile("^" + _SN_NAME + _SN_TITLE + r" notes - .+\.md\Z")

TITLE_LIMIT = 40


def title_slug(title, limit=TITLE_LIMIT):
    """The title part of a script file name: the words of `title` capitalised and joined by hyphens, apostrophes and every
    other mark dropped, accents folded to plain letters, at most `limit` characters cut at a word boundary
    ("High Achiever's Rest" -> "High-Achievers-Rest"; a leading "The" is kept). "" when the title has no letter or digit.
    A first word longer than `limit` is cut at `limit`."""
    s = unicodedata.normalize("NFKD", str(title or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub("[’‘'`´]", "", s)
    words = [w[0].upper() + w[1:] for w in re.findall(r"[A-Za-z0-9]+", s)]
    out = ""
    for w in words:
        cand = out + "-" + w if out else w
        if len(cand) > limit:
            break
        out = cand
    if not out and words:
        out = words[0][:limit]
    return out


def script_file_name(name_key, version, author, title="", in_app=False, duplicate=False, suffix=""):
    """`<NAME>[ <Title-Words>] v<N> <AUTHOR>[ duplicate][<suffix>][ (in app)].md`. `title` is a title_slug (or any title: it is
    run through title_slug); `suffix` is "", "-PROCESS-LOG" or "-REVIEW". The one place the library file-name shape is
    written, so the library tools and the Reader name a new file alike."""
    t = title_slug(title)
    return "%s%s v%d %s%s%s%s.md" % (name_key, " " + t if t else "", version, author,
                                      " duplicate" if duplicate else "", suffix, " (in app)" if in_app else "")


ScriptName = collections.namedtuple("ScriptName", "name_key version author kind in_app")


def script_title(name):
    """The title token a library file name carries (`High-Achievers-Rest`), or "" for a name with none or one outside the
    library shape. Kept apart from parse_script_name so that function still returns its five fields."""
    base = str(name).replace("\\", "/").rsplit("/", 1)[-1]
    m = SCRIPT_NAME.match(base) or SCRIPT_NOTE.match(base)
    return (m.group("title") or "") if m else ""


def parse_script_name(name):
    """Read a script file name (a bare name or a path) -> (name_key, version, author, kind, in_app).

    kind is "draft", "rewrite" (author THE HUMAN), "log" or "note". version is an int, or None for a name that is
    not in the library shape. A name outside the library shape falls back to REWRITE_SUFFIX: a name with a
    owner-rewrite suffix is kind "rewrite" with author "THE HUMAN", any other is kind "draft" with author None;
    name_key is then the file stem with that suffix taken off, and in_app is False."""
    base = str(name).replace("\\", "/").rsplit("/", 1)[-1]
    m = SCRIPT_NAME.match(base)
    if m:
        if m.group("log"):
            kind = "log"
        elif m.group("author") == "THE HUMAN":
            kind = "rewrite"
        else:
            kind = "draft"
        return ScriptName(m.group("name"), int(m.group("ver")), m.group("author"), kind, bool(m.group("app")))
    m = SCRIPT_NOTE.match(base)
    if m:
        return ScriptName(m.group("name"), None, None, "note", False)
    # o10L310 (review o10L299 defects 1-3): a name outside the shape. A trailing newline is not part of a name, so it
    # is dropped from the key (the shape itself rejects it). name_key is the stem cut at the rewrite suffix, with the
    # separator in front of it taken off (the Reader's rewrite_parts does the same), and a process-log name is a log.
    stem = base[:-3] if base.lower().endswith(".md") else os.path.splitext(base)[0]
    stem = stem.rstrip("\r\n")
    m = REWRITE_SUFFIX.search(stem)
    if m:
        return ScriptName(stem[:m.start()].rstrip(" -_"), None, "THE HUMAN", "rewrite", False)
    if PROCESS_LOG.search(stem):
        return ScriptName(stem, None, None, "log", False)
    return ScriptName(stem, None, None, "draft", False)

# A bare version number is NOT enough on its own. `product_page_1\...-v2.md` matched
# the old pattern and was asked for a header it has no reason to carry; a check that fires
# outside its own subject is one people learn to ignore.
BARE_VERSION = re.compile(r"(?i)-v\d+\.md$|-FINAL\.md$")

# ⛔ A RULES PROPOSAL IS NOT A SCRIPT VERSION (o9 W152, lane o9L1124, 2026-10-08). A file of
# proposed rule boxes is drafted in the pilot folder beside the script it learns from, and its
# drafts are numbered like a script's: `AT-10-FINAL-PROPOSED-RULES-v3.md`. BARE_VERSION read
# that name as a script version, so check 10 of check_checklist_router.py failed every box
# copied from the proposal the human approved, for sharing words with the proposal itself. The name
# is the reliable sign: every proposal in the corpus is named `...-PROPOSED-RULES...`, and no
# script is. Only the bare-version arm skips it. A name that also marks a the human rewrite is still
# a version file, whatever else it says (is_versioned below).
RULES_PROPOSAL = re.compile(r"(?i)-PROPOSED-RULES(?:[-_ .]|$)")

# Section 7 names where the corpus is. Kept as a list rather than inferred, so adding a second
# corpus is a visible edit rather than a pattern quietly widening.
# o10L278 (W226 batch 2): the pilots moved into script-library/. Their draft files sit under
# <folder>/versions/PIL/ there, so that marker stands in for the pilots folder.
CORPUS_DIRS = ("working_corpus_o8/pilots", "/versions/PIL/")

# \u26d4 NESTED WORKTREES HOLD A FULL COPY OF EVERYTHING. Skipping only `.worktrees` left
# `.claude/worktrees/*` and `.wt-*`, which inflated the first live count from 11 real artifacts
# to 65. Every duplicate would have been reported to somebody as its own file to fix.
SKIP_DIRS = (".git", "node_modules", ".codesight", "__pycache__",
             ".worktrees", "worktrees", "dist", "build",
             # ⭐ The OrchDoc Reader's pre-save copies of the human's rewrites
             # (reader_scripts.py). A snapshot is an OLD version of a rewrite, never a live
             # one, and counting it would report a rewrite the human did not write. Its NAME is
             # already only a timestamp ending `.snapshot`, so is_versioned() refuses it on
             # its own - skipping the folder as well means neither guard rests on the other.
             ".edit-history")


def _skip(d):
    return d in SKIP_DIRS or d.startswith(".wt-") or d.endswith("-worktrees")


# ⛔ PRIVATE FOLDERS (o9 W152, lane o9L1073). A folder named app-feedback or email_briefing holds
# text written by customers and by people who email the human, and no session may read it. Until
# then the --all walk skipped neither: it opened every file under one whose name marks a
# rewrite ("... - the human rewrite.md", is_versioned), and it followed a junction to one, since
# os.walk in Python 3.12 follows a junction. Now the walk does not start in a private folder,
# does not descend into one (private_folders.in_private_folder, which also catches a folder's
# other names on Windows), and follows no junction or symbolic link, to a folder or to a file
# (private_folders.is_link), as check_script.py's walks do. Not following links at all is the
# same choice as there: nothing needs this walk to follow one. private_folders is imported
# here rather than at the top, so the edit hook (cmd_hook), which never walks, does not need
# it. test_training_header.py, PRIVATE FOLDERS.
def walk_artifacts(start="."):
    """The version artifacts under the folder `start`, as the --all walk finds them. [] when
    `start` is in a private folder. The walk enters no folder _skip() names, no private folder
    and no junction or symbolic link, and returns no file that is a symbolic link."""
    from private_folders import in_private_folder, is_link
    if in_private_folder(start):
        print("  NOTE: not walked: %s is in a private folder (private_folders.py)"
              % os.path.abspath(start))
        return []
    found = []
    for root, dirs, files in os.walk(start):
        dirs[:] = [d for d in dirs if not _skip(d) and not is_link(os.path.join(root, d))
                   and not in_private_folder(os.path.join(root, d))]
        for f in files:
            p = os.path.join(root, f)
            if is_versioned(p) and not is_link(p):
                found.append(p)
    return found


def is_versioned(path):
    """Is this a training-loop artifact? Name alone for a rewrite, name + place otherwise."""
    p = str(path).replace("\\", "/")
    if REWRITE_SUFFIX.search(os.path.basename(p)):
        return True
    if RULES_PROPOSAL.search(os.path.basename(p)):
        return False
    # o10L317/L321 (W235 step 5): after the library rename a the human text is `<NAME> v<N> THE HUMAN.md`, which REWRITE_SUFFIX does
    # not see. It is still a rewrite, so a library name whose parser kind is "rewrite" is a training-loop artifact.
    # The old pilot texts (`versions/PIL/...-v3.md`) became `versions/<NAME> v<N> <AUTHOR>.md`; the name no longer says
    # which ones were pilots, and treating every text under versions/ as versioned would read 159 more files than the
    # check read before. Until step 6 writes `## Origin:` into each version file, the router
    # (check_checklist_router.version_files) keeps reading the files its baseline names.
    info = parse_script_name(os.path.basename(p))
    if info.version is not None and info.kind == "rewrite" and not info.in_app:
        # A the human text of origin FCI is the human's finished text of the app's own script (it was versions/FCI/...-owner-final,
        # a name that was never a rewrite name). It is a final, not a waypoint, so it was never a version file here.
        return library_origin(path) != "FCI"
    if bool(BARE_VERSION.search(p)) and any(c in p for c in CORPUS_DIRS):
        return True
    # o10L329 (review o10L326 X6): a renamed library file was a version file when its OLD path was (a `-v<N>.md` name
    # under versions/PIL/). The rename took the old name and the PIL folder away, so read the old path from the file's
    # own `## Source-File:` line (library_old_path; the rename map is not read) and apply the same test to it. 10 of the
    # router's 34 version files were lost without this.
    if "/%s/" % LIBRARY_DIR in p:
        old = library_old_path(path)
        if old and not RULES_PROPOSAL.search(os.path.basename(old)):
            return bool(BARE_VERSION.search(old)) and any(c in "/" + old for c in CORPUS_DIRS)
    return False

EMPTY = re.compile(r"^\s*$")

# \u26d4 WHICH ARM BLOCKS IS DECIDED BY THE BACKLOG, MEASURED. 11 of 11 corpus artifacts carry
# no header because every one predates Section 4, so a blocking header arm refuses the whole
# corpus on day one and gets switched off - taking the arm that works with it. SELF-ASSERTED-
# FINAL has a backlog of zero, so it blocks from the start.
#
# \u2b50 And the asymmetry is not a compromise. NO-HEADER is a gap somebody has to go and fill.
# SELF-ASSERTED-FINAL is an agent granting its own draft DE2 protection in the turn that is
# running - repairable on the spot by the one who did it, which is when a refusal costs least.
BLOCKING = ("SELF-ASSERTED-FINAL", "OLD-FIELD-NAME", "BAD-STATUS")


def blocks(all_findings):
    """True when any finding must stop the caller. Advisory ones never do."""
    return any(c in BLOCKING for c, _ in all_findings)


# ==========================================================================================
# THE PROVENANCE LINE - FM4, SCRIPT-CHECKLIST-5-FORMAT-AND-GATE.md (o8, a99b33de, 2026-09-23)
#
#     # SC-04 - caffeine crash - PILOT v2
#     > Authored: 2026-09-23 | Model: claude-opus-5-5 | By: o8L150
#
# the human, 2026-09-23: *"I also need to be able to see the date that the script was authored...
# That is a fast indicator of how likely it is that I'm working with the correct copy."*
#
# ⛔ DEFINED HERE, ONCE, because a marker format is a contract between every tool that reads
# it: this hook, the OrchDoc Reader (reader_scripts.py shows it, and its save stamps the human's
# line), and anything later. A second parser elsewhere is a second opinion about the format.
#
# ⛔ ONE POSITION, NEVER A SCAN. The line is the first non-blank line after the `# title`, and
# it is only ever looked for, written or replaced ABOVE `<!-- END SCRIPT HEADER -->`. Below that
# marker is script body; `promote-approved-scripts.mjs` discards everything above it, and the
# importer skips `>` lines, so a `>` line is safe in every place it can end up - an HTML comment
# below the marker would be read aloud in the customer's cloned voice.
#
# AUTHORED is a date and, from 2026-10-01, the time as well: `YYYY-MM-DD HH:MM`, US Pacific. The
# time is OPTIONAL, because every line written before that day records only a date. A date alone
# stays valid, is shown as a date alone, and has no time made up for it.
#
# A THE HUMAN REWRITE. The human, 2026-10-01 14:24, verbatim: *"Even in the scripts rewrites, the original
# model should still be indicated. Currently, it reads "Authored: 2026-10-01 | Model: none | By:
# the human" It should read "Authored: 2026-10-01 13:59 | Model: Opus 5.5 | Rewrite By: the human""*. So:
#
#     > Authored: 2026-10-01 13:59 | Model: Opus 5.5 | Rewrite By: the human
#
#   Authored  when THIS version, his rewrite, was saved (date and Pacific time).
#   Model     the model that wrote the ORIGINAL script it was made from, as that script records
#             it. `unknown` when nothing records it. Never `none`: that was the form before
#             2026-10-01 (`Model: none | By: the human`); it is still READ, and the hook calls it old.
#   Rewrite By  replaces `By`, and its value is `the human`.
#
# ONE LINE, AND NOTHING WRITTEN UNDER IT. The human's example is a single line, so the original's own
# authored date is not carried into the rewrite on a second line (o9, W66 fix round 1: an earlier
# version of this module wrote `> Original authored: <stamp>` under it, and no save writes that
# now). The Model slot is the one thing taken from the original, and the Reader copies it INTO the
# rewrite when it saves, so the slot stays right when the original is renamed, moved or gone.
#
# What sits under the line is another quote, and no tool here reads it or writes it. A note of
# who else edited the file goes in a quote of its own, with a blank line between (the preview
# joins consecutive `>` lines into one quote):
#
#     > Authored: 2026-09-25 | Model: Opus 5 | Rewrite By: the human
#
#     > Edits by o8 (claude-opus-5-5): D46 typos and contractions, DE4 grammar fixes
#
# An agent's file carries a model other than `none` (`unknown` only on backfill, FM4).
# ==========================================================================================

END_HEADER = re.compile(r"^\s*<!-- END SCRIPT HEADER -->\s*$")
PROV_TITLE = re.compile(r"^#[ \t]+\S")
PROV_FIELD = re.compile(r"^\s*(authored|model|(?:rewrite[ \t]+)?by)\s*:\s*(.*?)\s*$", re.I)
# `YYYY-MM-DD`, or `YYYY-MM-DD HH:MM` (24-hour, US Pacific).
PROV_STAMP = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[ T]+(\d{2}):(\d{2}))?$")
# What a the human rewrite's Model said BEFORE 2026-10-01. Still READ; no longer written.
PROV_REWRITE_MODEL = "none"
PROV_REWRITE_BY = "the human"
PROCESS_LOG = re.compile(r"(?i)process[-_ ]?log")
# Where the hook enforces the line. NOT working_corpus_o8/THE HUMAN-REVIEW, which holds copies.
PROV_SCOPE = ("working_corpus_o8/pilots", "working_corpus_o8/sc-replacements",
              "working_corpus_o8/new-at-scripts", "working_corpus_o8/model-bakeoff")
# o10L278 (W226 batch 2): pilots and new-at-scripts moved into script-library/. Inside it the hook keeps
# the same two groups it had, and nothing wider (batch 1 left PROV_SCOPE alone on purpose: a whole-library
# scope would demand a provenance line on every library script, a rule decision for o10 and the human):
#   - a the human rewrite, wherever it sits in the library (the name carries the suffix);
#   - the former pilot / new-at-scripts files: versions/PIL, versions/NAT, versions/NATI.
LIBRARY_DIR = "script-library"
# o10L356 (W235 step 7): the old `/versions/PIL/`, `/versions/NAT/`, `/versions/NATI/` folder marks are gone. The step-5
# rename flattened versions/<CODE>/ into versions/ (no such folder is left in the library), so the folder no longer says
# where a version file came from. The origin code is the file's `## Origin:` header line (step 6 wrote it into every
# version file), or, for a process log or note (which carry no header), the frozen table below.
LIBRARY_PROV_ORIGINS = ("PIL", "NAT", "NATI")
# The step-5 record: every old path and new path of the rename. Since step 7 NO reader of the library uses it
# (library_origin and library_old_path read the file's own header lines, and the frozen table below); only the
# generator orch-work-summaries/o10L340/add_origin_headers.py reads it, through rename_map_old_path, to write
# `## Source-File:` lines. It stays on main as the record of step 5.
RENAME_MAP_REL = "orch-work-summaries/o10L321/renames.csv"
# o10L356 (W235 step 7, ruling B of o10L343): the origin of the 26 process logs and notes (PIL 24, NAT 2). A log or note has
# no header, so its origin was only in the rename map. These 26 rows were copied out of it into this small table, keyed
# by file NAME (not by folder, so a moved folder keeps its answer). library_origin reads it.
FROZEN_ORIGINS_REL = "script-library/LOG-NOTE-ORIGINS.csv"
_ORIGIN_RE = re.compile(r"^##\s+Origin:[ \t]*(\S+)[ \t]*$", re.M)
_RENAME_MAPS = {}
_RENAME_OLD_PATHS = {}


def _rename_map(root):
    """{new path: origin code} from the committed rename map under `root`, read once per file version."""
    import csv
    mp = os.path.join(root, *RENAME_MAP_REL.split("/"))
    try:
        stamp = os.stat(mp).st_mtime_ns
    except OSError:
        return {}
    hit = _RENAME_MAPS.get(mp)
    if hit and hit[0] == stamp:
        return hit[1]
    out = {}
    olds = {}
    try:
        with io.open(mp, encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                if row.get("new_path") and row.get("old_path"):
                    olds[row["new_path"]] = row["old_path"]
                m = re.search(r"/versions/([^/]+)/[^/]+$", row.get("old_path", ""))
                if m and row.get("new_path"):
                    out[row["new_path"]] = m.group(1)
    except (OSError, ValueError):
        out, olds = {}, {}
    _RENAME_MAPS[mp] = (stamp, out)
    _RENAME_OLD_PATHS[mp] = olds
    return out


_FROZEN_ORIGINS = {}


def _frozen_origins(root):
    """{file name: origin code} from the frozen table of the 26 process logs and notes under `root`, read once per file
    version. {} when the table is missing."""
    import csv
    fp = os.path.join(root, *FROZEN_ORIGINS_REL.split("/"))
    try:
        stamp = os.stat(fp).st_mtime_ns
    except OSError:
        return {}
    hit = _FROZEN_ORIGINS.get(fp)
    if hit and hit[0] == stamp:
        return hit[1]
    out = {}
    try:
        with io.open(fp, encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                if row.get("file_name") and row.get("origin"):
                    out[row["file_name"]] = row["origin"]
    except (OSError, ValueError):
        out = {}
    _FROZEN_ORIGINS[fp] = (stamp, out)
    return out


END_HEADER_MARK = "<!-- END SCRIPT HEADER -->"
_END_HEADER_LINE = re.compile(r"^[ \t]*<!-- END SCRIPT HEADER -->[ \t\r]*$", re.M)
_SOURCE_FILE_RE = re.compile(r"^##\s+Source-File:[ \t]*(\S.*?)[ \t\r]*$", re.M)


def _header_text(path, cap=1 << 20):
    """The text above the `<!-- END SCRIPT HEADER -->` line of a file, or None when the file has no such line (or is not
    readable). Read in blocks until the marker, so a long header (a pilot's front matter) is read whole: the step-6
    lines sit directly above the marker, at the END of it (o10L340). The marker counts only as a line of its own, as in
    `END_HEADER` and `check_script.has_marker`: a line that quotes it (a log's sed command, a prose sentence) is not the
    end of the header (o10L343 S1)."""
    try:
        with io.open(str(path), encoding="utf-8", errors="replace") as fh:
            buf = ""
            eof = False
            while len(buf) < cap:
                chunk = fh.read(65536)
                if not chunk:
                    eof = True
                else:
                    buf += chunk
                m = _END_HEADER_LINE.search(buf)
                # a match that ends exactly where the text read so far ends may be the start of a longer line
                if m and (eof or m.end() < len(buf)):
                    return buf[:m.start()]
                if eof:
                    return None
    except OSError:
        pass
    return None


# o9:W199 (agreed with o10): a script file the human has set aside as an older draft carries one header line,
# `## Older-Draft: <reason>`. The key is defined HERE and read only by `older_draft`; file_script.py (writer) and
# reader_scripts.py (Reader) both call that function and keep no second pattern.
OLDER_DRAFT_KEY = "Older-Draft"
_OLDER_DRAFT_LINE = re.compile(r"^##[ \t]+" + OLDER_DRAFT_KEY + r"[ \t]*:[ \t]*(.*?)[ \t\r]*$", re.I)


def older_draft(header):
    """The reason (stripped) when the header carries a non-empty `## Older-Draft: <reason>` line, else None.

    `header` is TEXT, as in `parse_provenance(text)`: the header of a file or the whole file. Only the lines above
    the `<!-- END SCRIPT HEADER -->` line count (all lines when the text has none), so a script body that quotes the
    line does not mark the file. The first such line wins. A mapping of lower-case header keys to values - what
    `reader_scripts.text_facts` returns as its first item - is accepted too, read through the same key. An empty
    reason, None or any other type gives None."""
    if isinstance(header, dict):
        for k, v in header.items():
            if isinstance(k, str) and k.casefold() == OLDER_DRAFT_KEY.casefold():
                return (str(v).strip() or None) if v is not None else None
        return None
    if not isinstance(header, str):
        return None
    for ln in header.replace("\r\n", "\n").split("\n"):
        if END_HEADER.match(ln):
            break
        m = _OLDER_DRAFT_LINE.match(ln)
        if m:
            return m.group(1).strip() or None
    return None


def library_origin(path):
    """The origin code (PIL, NAT, NATI, FCS ...) of a library version file, or "". The file's `## Origin:` line wins
    (step 6 wrote it into every version file, so a file moved by `file --category`, `--stage` or `release` keeps it);
    else, for a process log or note (they carry no header), the frozen table of the 26 (FROZEN_ORIGINS_REL, keyed by
    file name). o10L356 (step 7): the rename map is no longer read."""
    p = str(path).replace("\\", "/")
    head = _header_text(path)
    if head is not None:
        m = _ORIGIN_RE.search(head)
        if m:
            return m.group(1)
    i = p.find("/%s/" % LIBRARY_DIR)
    if i < 0:
        return ""
    return _frozen_origins(p[:i]).get(p.rsplit("/", 1)[-1], "")


def rename_map_origin(path):
    """The origin code the step-5 rename map gives a library file (the folder after /versions/ in its OLD path), or "".
    Only add_origin_headers.py (the generator of the `## Origin:` lines) calls this; library_origin does not read the map."""
    p = str(path).replace("\\", "/")
    i = p.find("/%s/" % LIBRARY_DIR)
    if i < 0:
        return ""
    return _rename_map(p[:i]).get(p[i + 1:], "")


_IN_APP_TAIL = " (in app).md"


def rename_map_old_path(path):
    """The path a library file had before the step-5 rename (repo-relative, '/'), from the committed rename map ONLY, or "".
    Only add_origin_headers.py (the generator of the `## Source-File:` lines) calls this; no reader of the library does.

    A text that o5 moved to `versions/<NAME> v<N> <AUTHOR>.md` after the rename (a `revise` of a live script moves the old
    `(in app)` record there) is not in the map under that name: the map names it `<NAME> v<N> <AUTHOR> (in app).md`, one
    folder up. That name is tried when the exact path is not in the map, in the same folder first and then, when the
    script's folder has been moved since step 5, in the one folder of that name the map holds."""
    p = str(path).replace("\\", "/")
    i = p.find("/%s/" % LIBRARY_DIR)
    if i < 0:
        return ""
    root = p[:i]
    _rename_map(root)
    mp = os.path.join(root, *RENAME_MAP_REL.split("/"))
    olds = _RENAME_OLD_PATHS.get(mp, {})
    rel = p[i + 1:]
    if rel in olds:
        return olds[rel]
    parts = rel.split("/")
    if len(parts) >= 3 and parts[-2] == "versions" and parts[-1].endswith(".md") and not parts[-1].endswith(_IN_APP_TAIL):
        in_app_name = parts[-1][:-3] + _IN_APP_TAIL
        folder = parts[:-2]
        hit = olds.get("/".join(folder + [in_app_name]))
        if hit:
            return hit
        # the folder moved: the same last folder name and file name, exactly once in the map
        tail = "/" + folder[-1] + "/" + in_app_name
        found = [v for k, v in olds.items() if k.endswith(tail)]
        if len(found) == 1:
            return found[0]
    return ""


def library_old_path(path):
    """The path a library file had before the step-5 rename (repo-relative, '/'), or "". The file's `## Source-File:` line
    wins when it is a library path (step 6 wrote it, so a moved file keeps it). A `## Source-File:` that is only a bare
    file name (164 pilot files carry the name they were written under, from `adopt`) is not a path and gives "".
    o10L356 (step 7): the rename map is no longer read."""
    head = _header_text(path)
    if head is not None:
        m = _SOURCE_FILE_RE.search(head)
        if m and m.group(1).startswith(LIBRARY_DIR + "/"):
            return m.group(1)
    return ""


def in_prov_scope(path):
    """Is this path one the provenance rules cover? PROV_SCOPE folders, or the library's two groups above."""
    p = "/" + str(path).replace("\\", "/").lstrip("/")
    pl = p.lower()
    if any(("/%s/" % d).lower() in pl for d in PROV_SCOPE):
        return True
    if ("/%s/" % LIBRARY_DIR) in pl:
        # a renamed the human text (`<NAME> v<N> THE HUMAN.md`) is a rewrite by the name parser; a former pilot / new-at-scripts file
        # is found by its origin code (M5). o10L356 (step 7): the old `versions/PIL/` folder marks are not read any more.
        pn = parse_script_name(os.path.basename(p))
        return (bool(REWRITE_SUFFIX.search(os.path.basename(p))) or (pn.kind == "rewrite" and pn.version is not None)
                or (pn.version is not None and "/versions/" in pl and library_origin(path) in LIBRARY_PROV_ORIGINS))
    return False


def _split(text):
    """(lines, newline) - the text's own newline, so a rewrite of one line keeps the rest."""
    text = text or ""
    nl = "\r\n" if "\r\n" in text else "\n"
    return text.split(nl), nl


def provenance_slot(lines):
    """(title_i, slot_i, end_i) over a list of lines, all 0-based, each None when absent.

    title_i  the `# title` - the first H1 above the END marker and above any `##` section
    slot_i   the first non-blank line after the title, if it is still above the END marker
    end_i    the `<!-- END SCRIPT HEADER -->` line
    """
    end_i = next((i for i, ln in enumerate(lines) if END_HEADER.match(ln)), None)
    limit = end_i if end_i is not None else len(lines)
    title_i = None
    for i in range(limit):
        if lines[i].startswith("##"):
            break
        if PROV_TITLE.match(lines[i]):
            title_i = i
            break
    slot_i = None
    # With no title, the line sits at the top of the file (set_provenance_line inserts it at 0),
    # so look from line 0; otherwise each save would add one more line (W66, o9 decision).
    start = title_i + 1 if title_i is not None else 0
    for i in range(start, limit):
        if lines[i].strip():
            slot_i = i
            break
    return title_i, slot_i, end_i


def parse_provenance_line(line):
    """{"authored", "model", "by"} (raw strings, only the fields present) or None.

    The line must be a `>` blockquote whose every `|` segment is one of the three fields.
    Case and spacing around the separators are tolerated; any other segment means this is a
    different quote, not the provenance line.

    `Rewrite By: the human` is the `by` field too, and adds `"rewrite": True` - the one key whose
    value is not a string - so a caller can tell a rewrite's line from an agent's."""
    s = (line or "").strip()
    if not s.startswith(">"):
        return None
    s = s.lstrip(">").strip()
    fields = {}
    for seg in s.split("|"):
        if not seg.strip():
            continue
        m = PROV_FIELD.match(seg)
        if not m:
            return None
        key = re.sub(r"\s+", " ", m.group(1).lower())
        rewrite = key.startswith("rewrite")
        if rewrite:
            key = "by"
        if key not in fields:               # the first of a repeated field wins
            fields[key] = m.group(2).strip()
            if rewrite:
                fields["rewrite"] = True
    return fields or None


def parse_provenance(text):
    """The FM4 line of `text`, parsed, or None when the header position does not hold one."""
    lines, _ = _split(text)
    _, slot_i, _ = provenance_slot(lines)
    return parse_provenance_line(lines[slot_i]) if slot_i is not None else None


def provenance_stamp(value):
    """(ISO date, "HH:MM" or None) when `value` is a real `YYYY-MM-DD` or `YYYY-MM-DD HH:MM`,
    else None. The time is optional because every line written before 2026-10-01 has none, and
    a missing time stays missing: it is never filled in."""
    import datetime
    m = PROV_STAMP.match((value or "").strip())
    if not m:
        return None
    try:
        day = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        clock = (datetime.time(int(m.group(4)), int(m.group(5)))
                 if m.group(4) is not None else None)
    except ValueError:
        return None
    return day.isoformat(), (clock.strftime("%H:%M") if clock else None)


def provenance_date(value):
    """`value` as an ISO date string when it is a real YYYY-MM-DD (a time after it is allowed
    and ignored here), else None."""
    got = provenance_stamp(value)
    return got[0] if got else None


# ---- US Pacific time ------------------------------------------------------------------------
# the human, 2026-10-01: the authored value shows the date AND the time, Pacific. zoneinfo answers
# when this machine has the zone database (Windows needs the `tzdata` package for it); when it
# does not, the US rules in force since 2007 answer instead - clocks go forward at 02:00 on the
# second Sunday of March and back at 02:00 on the first Sunday of November - so a missing
# database costs nothing and never raises.

def _nth_sunday(year, month, n):
    import datetime
    first = datetime.date(year, month, 1)
    return first + datetime.timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))


def _us_pacific_is_dst(utc):
    """Is US Pacific daylight time in force at this aware UTC instant."""
    import datetime
    start = datetime.datetime.combine(_nth_sunday(utc.year, 3, 2), datetime.time(10, 0),
                                      tzinfo=datetime.timezone.utc)      # 02:00 PST
    end = datetime.datetime.combine(_nth_sunday(utc.year, 11, 1), datetime.time(9, 0),
                                    tzinfo=datetime.timezone.utc)        # 02:00 PDT
    return start <= utc < end


def pacific_by_rules(dt):
    """`dt` as US Pacific wall-clock time from the DST rules alone, with no zone database."""
    import datetime
    utc = dt.astimezone(datetime.timezone.utc)
    hours = -7 if _us_pacific_is_dst(utc) else -8
    return utc.astimezone(datetime.timezone(datetime.timedelta(hours=hours),
                                            "PDT" if hours == -7 else "PST"))


def to_pacific(dt):
    """An aware datetime (a naive one is taken as this machine's local time) as US Pacific."""
    if dt.tzinfo is None:
        dt = dt.astimezone()
    try:
        from zoneinfo import ZoneInfo
        return dt.astimezone(ZoneInfo("America/Los_Angeles"))
    except Exception:
        return pacific_by_rules(dt)


def now_pacific():
    """The current time in US Pacific. The one clock the stamp reads, so a test can hold it."""
    import datetime
    return to_pacific(datetime.datetime.now(datetime.timezone.utc))


def pacific_stamp(dt=None):
    """`YYYY-MM-DD HH:MM` in US Pacific, for `dt` or for now."""
    t = to_pacific(dt) if dt is not None else now_pacific()
    return t.strftime("%Y-%m-%d %H:%M")


def build_provenance_line(authored, model, by, rewrite=False):
    """`> Authored: <authored> | Model: <model> | By: <by>` - or `Rewrite By:` for a rewrite."""
    return "> Authored: %s | Model: %s | %s: %s" % (authored, model,
                                                    "Rewrite By" if rewrite else "By", by)


def owner_provenance_line(authored=None, model=None):
    """The line a the human rewrite carries: `> Authored: <Pacific date and time of this save> |
    Model: <the ORIGINAL script's model> | Rewrite By: the human`.

    `authored` is a stamp to use instead of now (a date alone is accepted); `model` is the
    original's, and `unknown` when nothing records it - never `none`, which is not a model."""
    return build_provenance_line(authored or pacific_stamp(), (model or "").strip() or "unknown",
                                 PROV_REWRITE_BY, rewrite=True)


def set_provenance_line(text, line):
    """`text` with its FM4 line set to `line`: the line in the header position is replaced, or
    - when there is none - `line` is inserted directly under the `# title` (at the top of the
    file if it has no title). Nothing else in the text changes, and nothing is ever written
    below `<!-- END SCRIPT HEADER -->`.

    It writes ONE line. There is no second line to set (an `Original authored` line was dropped
    in W66 fix round 1, so a caller can no longer ask for one), and whatever already sits under
    the FM4 line, a quote of its own for instance, stays exactly as it is."""
    lines, nl = _split(text)
    title_i, slot_i, _ = provenance_slot(lines)
    if slot_i is not None and parse_provenance_line(lines[slot_i]) is not None:
        lines[slot_i] = line
    elif title_i is not None:
        lines.insert(title_i + 1, line)
    else:
        lines.insert(0, line)
    return nl.join(lines)


def strip_provenance_line(text):
    """`text` without its FM4 line (only the one in the header position). Whatever sits under
    it, a quote of its own for instance, stays."""
    lines, nl = _split(text)
    _, slot_i, _ = provenance_slot(lines)
    if slot_i is not None and parse_provenance_line(lines[slot_i]) is not None:
        del lines[slot_i]
    return nl.join(lines)


def provenance_problems(text, rewrite):
    """[(code, detail)] for `text`'s FM4 line; [] when it is right. `rewrite` = a the human rewrite."""
    p = parse_provenance(text)
    if p is None:
        return [("NO-PROVENANCE", "no `> Authored: ... | Model: ... | By: ...` line under the "
                                  "title")]
    out = []
    if not provenance_stamp(p.get("authored")):
        out.append(("BAD-PROVENANCE", "Authored must be a real YYYY-MM-DD date, optionally "
                                      "followed by the Pacific time HH:MM (got %r)"
                    % p.get("authored", "")))
    model, by = (p.get("model") or "").strip(), (p.get("by") or "").strip()
    if not model:
        out.append(("BAD-PROVENANCE", "Model is missing"))
    if not by:
        out.append(("BAD-PROVENANCE", "By is missing"))
    # The refusal message shows placeholders in angle brackets; copied verbatim they would
    # otherwise pass as a model and a lane.
    if "<" in model + by or ">" in model + by:
        out.append(("BAD-PROVENANCE", "replace the <placeholder> with the real value"))
    label = "Rewrite By" if p.get("rewrite") else "By"
    if rewrite and model and by and (model.lower() == PROV_REWRITE_MODEL
                                     or not p.get("rewrite")
                                     or by.lower() != PROV_REWRITE_BY.lower()):
        out.append(("BAD-PROVENANCE",
                    "a the human rewrite names the model of the ORIGINAL script it was made from "
                    "(`unknown` when nothing records it, never `none`) and says `Rewrite By: "
                    "the human`, not the agent's line (got Model: %s | %s: %s)" % (model, label, by)))
    if not rewrite and model.lower() == PROV_REWRITE_MODEL:
        out.append(("BAD-PROVENANCE", "an agent's file names the model that wrote it - `none` "
                                      "is not a model"))
    if not rewrite and p.get("rewrite"):
        out.append(("BAD-PROVENANCE", "`Rewrite By:` is only for a the human rewrite; an agent's "
                                      "file says `By: <lane>`"))
    return out


def is_script_file(path, text):
    """An `.md` in PROV_SCOPE carrying `<!-- END SCRIPT HEADER -->` - the same test
    check_script.py (MARKER) and the PWA importer (HEADER_END_MARKER) use. Process logs and
    `.edit-history/` snapshots are never scripts."""
    p = "/" + str(path).replace("\\", "/").lstrip("/")
    if not p.lower().endswith(".md") or "/.edit-history/" in p:
        return False
    if PROCESS_LOG.search(os.path.basename(p)):
        return False
    # the path as given, not `p`: library_origin opens the file and the rename map by their real path, and `p` has a
    # leading "/" that is not a path on Windows ("/C:/...") (o10L329).
    if not in_prov_scope(path):
        return False
    lines, _ = _split(text)
    return any(END_HEADER.match(ln) for ln in lines)


def git(args, cwd=None):
    """Run git with an ARGUMENT LIST and no shell - MSYS rewrites `ref:./path` otherwise."""
    p = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True,
                       creationflags=flags())
    return p.returncode, p.stdout, p.stderr


def _git_dir(path):
    """The nearest folder above `path` that exists, to run git in: a new script's folder may not exist yet, and git
    cannot start in a missing folder (that raised, and the hook then let the `final` through)."""
    d = os.path.dirname(os.path.abspath(path))
    while not os.path.isdir(d):
        up = os.path.dirname(d)
        if up == d:
            break
        d = up
    return d


def ref_show(path, ref="origin/main"):
    """(repo top, the file's text as `ref` has it). The text is None when the file is not on `ref`; both are None
    outside a repo."""
    rc, top, _ = git(["rev-parse", "--show-toplevel"], cwd=_git_dir(path))
    if rc:
        return None, None
    top = top.strip()
    rel = os.path.relpath(os.path.abspath(path), top).replace(os.sep, "/")
    rc, out, _ = git(["show", "%s:%s" % (ref, rel)], cwd=top)
    return top, (None if rc else out)


def status_of(text):
    """The status field of a file's text; None for no text (not on the ref), "" for no marker."""
    if text is None:
        return None
    m = HEADER.search(text)
    if is_final(text):                    # a marker saying final, or the human's Approved-By line (o9 W204)
        return "final"
    return (m.group(2).strip().lower() if m else "")


def status_on_ref(path, ref="origin/main"):
    """The status field as `ref` has it, or None when the file is not on that ref."""
    return status_of(ref_show(path, ref)[1])


#: the human's Approve in the Reader (file_script.py approve, o10 W244) writes `## Approved-By: the human <ISO date-time>
#: ruling:<ref>` into the approved file's header; July's approvals read `the human <date> app:<hash>`. Either, in the header
#: of the file AS `ref` HOLDS IT, is the human's own act on that exact file (o9 W204 review, o9L1276). The human, in chat
#: 2026-10-12: "Approve should mark a script final. I'll only ever use it for that."
APPROVED_BY_DREW = re.compile(r"(?m)^## Approved-By:[ \t]*the human[ \t]+\d{4}-\d{2}-\d{2}")


def _header_part(text):
    """The text above the `<!-- END SCRIPT HEADER -->` line, or None when the text has no such line (a line that only
    quotes the marker inside prose does not count: `_END_HEADER_LINE` wants the marker alone on its line)."""
    m = _END_HEADER_LINE.search(text or "")
    return text[:m.start()] if m else None


def approved_on_ref(text):
    """True when `text` (a file as the ref holds it) carries the human's `## Approved-By:` line above its END SCRIPT HEADER.
    A text with no END line has no header, so the line does not count there (o9 W204, lane o9L1282)."""
    head = _header_part(text)
    return bool(head and APPROVED_BY_DREW.search(head))


def is_final(text):
    """THE ONE DEFINITION of "this script is final" (o9 W204, the human 2026-10-12: "Approve should mark a script final. I'll
    only ever use it for that."). True when the header, the part above `<!-- END SCRIPT HEADER -->`, either says
    `<!-- training-version: vN | final | ... -->` or carries `## Approved-By: the human <date> ...`. A text with no END line
    is read whole for the training-version marker, as findings() reads it, and cannot carry Approved-By. A
    `waypoint`, a bad status, no marker, Approved-By naming anyone but the human, and either marker only in the body are
    not final. No marker is invented: an approved old app text has no training-version line and is final by Approved-By."""
    if not text:
        return False
    head = _header_part(text)
    if head is None:
        head = text
    m = HEADER.search(head)
    if m and (m.group(2) or "").strip().lower() == "final":
        return True
    return bool(APPROVED_BY_DREW.search(head)) and _header_part(text) is not None


#: A landed ruling that a script is final (o9 W204). Only OrchDocs as `ref` holds them count, so an
#: agent's own unlanded note in the working tree cannot satisfy it.
ORCHDOC_NAME = re.compile(r"^ORCHESTRATOR-DECISIONS-o\d+\.md\Z")
#: At most this many OrchDocs that name the script are read in full; the rest are not looked at.
MAX_ORCHDOCS = 12
ENTRY_SPLIT = re.compile(r"(?m)^(?=#{2,3} )")
ENTRY_RESOLVED = re.compile(r"\*\*Status:\*\*\s*RESOLVED\b")
#: the human's own words in quotes, containing the word final, close behind the word the human; or "the human ruled ... final".
OWNER_QUOTE_FINAL = re.compile(
    "\\bDrew\\b[^\\n\"“]{0,120}[\"“]([^\"”\\n]*\\bfinal\\b[^\"”\\n]*)[\"”]", re.I)
OWNER_RULED_FINAL = re.compile(r"\bthe human\s+ruled\b[^\n.]{0,120}\bfinal\b", re.I)
NEGATED_FINAL = re.compile(r"\b(?:not|never|non|isn't|aren't)[-\s]*final\b|n't\s+final\b", re.I)


def owner_final_ruling(path, ref="origin/main", top=None):
    """Where a RESOLVED OrchDoc entry on `ref` names this script and records the human ruling it final, as
    "<file>: <heading>"; None when there is none. The script is matched by its file name, or by its label
    (SC-Sl-N-15-012) as a whole token. When the entry names the label with a version (`SC-Sl-N-15-012 v7`,
    `SC-Sl-N-15-012 The-Title v7`), the file's version must be one it names; an entry naming the label alone covers
    every version. Reads `ref` only, and only the OrchDocs `git grep` finds the name in (at most MAX_ORCHDOCS).
    Fails closed: any error returns None, and the caller then blocks as before."""
    try:
        if top is None:
            rc, top, _ = git(["rev-parse", "--show-toplevel"], cwd=_git_dir(path))
            if rc:
                return None
            top = top.strip()
        base = os.path.basename(path)
        info = parse_script_name(base)
        key = info.name_key or ""
        base_rx = re.compile(re.escape(base))
        label = (r"(?<![A-Za-z0-9-])" + re.escape(key) + r"(?![A-Za-z0-9]|-\d)") if key else None
        rx = re.compile(base_rx.pattern + ("|" + label if label else ""))
        named_version = re.compile(label + r"(?:[ \t]+[^\s/\\]+)?[ \t]+v(\d+)\b") if label else None
        args = ["grep", "-l", "-F", "-e", base] + (["-e", key] if key else []) + [ref, "--",
                                                                              "ORCHESTRATOR-DECISIONS-o*.md"]
        rc, listing, _ = git(args, cwd=top)
        if rc:
            return None                 # 1: no OrchDoc names it; anything else: an error
        names = sorted({n.strip()[len(ref) + 1:] if n.strip().startswith(ref + ":") else n.strip()
                        for n in listing.split("\n") if n.strip()})
        for name in [n for n in names if ORCHDOC_NAME.match(n)][:MAX_ORCHDOCS]:
            rc, text, _ = git(["show", "%s:%s" % (ref, name)], cwd=top)
            if rc or not rx.search(text):
                continue
            for entry in ENTRY_SPLIT.split(text):
                if not rx.search(entry) or not ENTRY_RESOLVED.search(entry):
                    continue
                if not base_rx.search(entry) and named_version is not None and info.version is not None:
                    versions = {int(v) for v in named_version.findall(entry)}
                    if versions and info.version not in versions:
                        continue        # the ruling is about another version of this script
                said = [m.group(1) for m in OWNER_QUOTE_FINAL.finditer(entry)]
                said += [m.group(0) for m in OWNER_RULED_FINAL.finditer(entry)]
                if any(not NEGATED_FINAL.search(x) for x in said):
                    return "%s: %s" % (name, entry.strip().split("\n", 1)[0][:100])
    except Exception:
        return None
    return None


def final_ground(path, ref="origin/main", shown=None):
    """Why a `final` the ref does not hold may still be written, or None. Two grounds, both read off `ref` only:
    the file as `ref` holds it carries the human's `## Approved-By:` line (the Reader's Approve button), or a RESOLVED
    OrchDoc entry on `ref` records the human ruling it final (owner_final_ruling). `shown` is ref_show's answer when the
    caller already has it. Fails closed: any error returns None."""
    try:
        top, text = shown if shown is not None else ref_show(path, ref)
        if top is None:
            return None
        if approved_on_ref(text):
            return "Approved-By: the human on %s" % ref
        return owner_final_ruling(path, ref, top=top)
    except Exception:
        return None


def findings(path, text=None, ref="origin/main"):
    """[(code, detail)] for one version artifact."""
    out = []
    if text is None:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()

    m = HEADER.search(text)
    # o9 W204 (lane o9L1283): a text final through the human's `## Approved-By: the human <date>` header line needs no training
    # header, so it gets no NO-HEADER, NO-DELTA or NO-UNFIXED. Only when it has NO training-version line: a text that has
    # one still answers for its status word (BAD-STATUS), its fields and a self-asserted `final`, as before.
    if not m and approved_on_ref(text):
        return []
    if not m:
        return [("NO-HEADER", os.path.basename(path))]

    status = (m.group(2) or "").strip().lower()
    if status not in ("waypoint", "final"):
        out.append(("BAD-STATUS", "%s - expected waypoint or final" % status))

    if OLD_UNFIXED.search(text) and not UNFIXED.search(text):
        out.append(("OLD-FIELD-NAME", "'Still unfixed' asks what THE HUMAN left undone"))

    for label, rx, code in (("This version fixes", FIXES, "NO-DELTA"),
                            ("Known unfixed (my read, not his)", UNFIXED, "NO-UNFIXED")):
        f = rx.search(text)
        if not f:
            out.append((code, "missing: **%s:**" % label))
        elif EMPTY.match(f.group(1) or ""):
            out.append((code, "empty: **%s:**  (write 'not assessed')" % label))

    # ⛔ ARM 2. Only the human makes a version final, and his rulings land.
    if status == "final":
        shown = ref_show(path, ref)
        was = status_of(shown[1])
        if was != "final" and not final_ground(path, ref, shown):
            out.append(("SELF-ASSERTED-FINAL",
                        "%s (on %s: %s)" % (os.path.basename(path), ref, was or "absent")))
    return out


#: Names the FM4 advisory's delivery record in place of ADVICE_SEEN in the home state dir.
#: Tests set it.
ADVICE_SEEN_ENV = "TRAINING_HEADER_SEEN_FILE"
ADVICE_SEEN = "training-header-advice-seen.json"
#: Seconds the FM4 advisory stays silent for one file in the context it reached.
ADVICE_WINDOW = 30 * 60


def _advice_key(payload):
    """The file an advisory is about, as the delivery record keys it: the payload's path,
    absolute and case-folded; "" when the payload names none."""
    ti = payload.get("tool_input") if isinstance(payload, dict) else None
    path = (ti.get("file_path") or ti.get("filePath")) if isinstance(ti, dict) else None
    return os.path.normcase(os.path.abspath(path)) if isinstance(path, str) and path else ""


def _allow(advice=None, payload=None):
    # ⛔ NO OBJECTION MEANS SILENCE, NEVER "allow". A PreToolUse `permissionDecision: "allow"`
    # SKIPS the interactive permission prompt, and a silent hook contributes no decision, so
    # one hook's "allow" is the combined result for that call. So this guard, registered on every
    # Write|Edit in ~/.claude/settings.json, was approving every file write in every session
    # not running in bypass mode, including files it has no opinion about. Exit 0 with nothing
    # on stdout is the documented "no decision": the normal permission flow still applies.
    # ⭐ The advisory goes out as additionalContext JSON on stdout, which carries no decision
    # and reaches the model. It went to stderr until 2026-09-28, where a PreToolUse hook's
    # output on exit 0 never reaches the model (hook_context.py).
    # ⛔ ONCE PER CONTEXT PER FILE PER 30 MINUTES (REVIEW-13d78843.md S3). Once it reached the
    # model, the advisory arrived on every edit of an existing script that lacks the line: 71
    # of the 80 in PROV_SCOPE on 2026-09-28. A rule of "only for new files" would remove it:
    # a NEW script without the line is refused (exit 2), never advised. So the advisory keeps
    # its file and loses the repeat, recorded the way fire_prose_rule.py records deliveries.
    # A refusal is never held back.
    if advice:
        try:
            from hook_context import emit, event_of, first_in_window
        except Exception:  # a partial delivery costs the advisory, never the exit-0 allow
            return 0
        if first_in_window(payload, _advice_key(payload), ADVICE_SEEN, env=ADVICE_SEEN_ENV,
                           window=ADVICE_WINDOW):
            emit(event_of(payload, "PreToolUse"), advice)
    return 0


def on_any_ref(path, refs=("origin/main", "HEAD")):
    """True when `path` exists on one of `refs`, False when on none, None when git cannot say.
    "New" for the provenance arm means on NO ref - neither landed nor committed here."""
    d = os.path.dirname(os.path.abspath(path))
    while d and not os.path.isdir(d):              # a brand-new folder is not on disk yet
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent
    rc, top, _ = git(["rev-parse", "--show-toplevel"], cwd=d)
    if rc:
        return None
    top = top.strip()
    rel = os.path.relpath(os.path.abspath(path), top).replace(os.sep, "/")
    for ref in refs:
        rc, _, _ = git(["cat-file", "-e", "%s:%s" % (ref, rel)], cwd=top)
        if rc == 0:
            return True
    return False


def _text_after(ti, path):
    """The whole file as this Write or Edit would leave it, or None when it cannot be known."""
    content = ti.get("content")
    if content is not None:
        return content
    old = ti.get("old_string", ti.get("oldString"))
    new = ti.get("new_string", ti.get("newString"))
    if old is None or new is None or not os.path.isfile(path):
        return None
    with io.open(path, encoding="utf-8", errors="replace", newline="") as fh:
        cur = fh.read().replace("\r\n", "\n")
    old, new = old.replace("\r\n", "\n"), new.replace("\r\n", "\n")
    if not old or old not in cur:
        return None
    return cur.replace(old, new) if ti.get("replace_all") else cur.replace(old, new, 1)


def provenance_verdict(ti, path):
    """None when the write is fine or not a script; else ("block" | "advise", message).

    NEW script (on no git ref) without a correct FM4 line: BLOCK. An existing one: ADVISE -
    old files are not rewritten for FM4, so refusing every edit to one would switch this off."""
    text = _text_after(ti, path)
    if text is None or not is_script_file(path, text):
        return None
    # o10L329 (review o10L326 X3): a renamed the human text (`<NAME> v<N> THE HUMAN.md`) has no `-owner-rewrite` in its name; the name
    # parser's kind says it is the human's. Same test in_prov_scope uses.
    rewrite = (bool(REWRITE_SUFFIX.search(os.path.basename(path)))
               or parse_script_name(os.path.basename(path)).kind == "rewrite")
    probs = provenance_problems(text, rewrite)
    if not probs:
        return None
    new = on_any_ref(path) is False
    return ("block" if new else "advise", provenance_message(path, text, probs, rewrite, new))


def provenance_message(path, text, probs, rewrite, new):
    """Everything a lane needs to fix it from the message alone: the exact line, and where."""
    lines, _ = _split(text)
    title_i, slot_i, _ = provenance_slot(lines)
    now = pacific_stamp()
    want = (owner_provenance_line(
                now, "<the ORIGINAL script's model, as that script records it, e.g. "
                     "claude-opus-5-5 - or unknown>") if rewrite else
            build_provenance_line(now, "<your exact model id, e.g. claude-opus-5-5>",
                                  "<your lane id, e.g. o8L150 - or o8>"))
    has = slot_i is not None and parse_provenance_line(lines[slot_i]) is not None
    if has:
        where = ("REPLACE line %d, which says:\n\n      %s\n\n  with:"
                 % (slot_i + 1, lines[slot_i].strip()))
    elif title_i is not None:
        where = ("ADD this as line %d, directly under the title on line %d (`%s`):"
                 % (title_i + 2, title_i + 1, lines[title_i].strip()[:70]))
    else:
        where = ("ADD this as the FIRST line of the file (it has no `# title`), above "
                 "`<!-- END SCRIPT HEADER -->`:")
    return (
        "\n%s - %s %s\n\n"
        "  SCRIPT-CHECKLIST-5-FORMAT-AND-GATE.md FM4: every script carries its authored date,\n"
        "  its exact model and who wrote it on ONE line, the first line after the `# title`.\n"
        "  Problem: %s\n\n"
        "  %s\n\n      %s\n\n"
        "%s"
        "  Authored = the date and time this version's text was written, US Pacific, as\n"
        "  YYYY-MM-DD HH:MM (a date alone is still accepted). Model = the exact id, never a\n"
        "  family name. It is a `>` line, never an HTML comment: the importer reads any other\n"
        "  comment aloud in the customer's voice.\n"
        % (probs[0][0], os.path.basename(path),
           "is a NEW script (on no git ref), so this write was REFUSED." if new else
           "has no correct provenance line. ADVISORY - the write went through.",
           "; ".join(d for _, d in probs), where, want,
           "  (Only a the human rewrite says `Rewrite By: the human`, and its Model is the original\n"
           "  script's, not the agent's.)\n"
           if not rewrite else
           "  (The Reader's save writes this line itself when the human saves a rewrite; this is the\n"
           "  line it writes.)\n"))


#: Any `## Approved-By:` field line, whoever it names, as reader_scripts.text_facts reads the field (key matched
#: without regard to case). The Reader's Approved badge shows for any of them, so the guard refuses any of them.
APPROVED_BY_ANY = re.compile(r"(?im)^##[ \t]+approved-by:[^\n]*")


def approved_lines(text):
    """The `## Approved-By:` lines in `text`, each with its trailing spaces and CR taken off."""
    return {m.group(0).rstrip() for m in APPROVED_BY_ANY.finditer(text or "")}


def _approve_scope(path, text):
    """A file the approval guard looks at: an `.md` under script-library/, or an `.md` carrying the END SCRIPT HEADER
    line (any script, wherever it sits)."""
    p = "/" + str(path).replace("\\", "/").lstrip("/")
    if not p.lower().endswith(".md"):
        return False
    return "/script-library/" in p.lower() or bool(_END_HEADER_LINE.search(text or ""))


def _approved_elsewhere(path, line, ref="origin/main"):
    """True when a file on `ref` whose script label is this file's label already carries `line` exactly: a version
    move copies an approved text to a new path. False when the file has no label or git cannot say."""
    key = parse_script_name(os.path.basename(path)).name_key
    if not key:
        return False
    rc, top, _ = git(["rev-parse", "--show-toplevel"], cwd=_git_dir(path))
    if rc:
        return False
    top = top.strip()
    rc, out, _ = git(["grep", "-l", "-F", "-e", line, ref, "--", "*.md"], cwd=top)
    if rc:
        return False
    for name in out.splitlines():
        name = name.strip()
        if name.startswith(ref + ":"):
            name = name[len(ref) + 1:]
        if not name or parse_script_name(os.path.basename(name)).name_key != key:
            continue
        rc, text, _ = git(["show", "%s:%s" % (ref, name)], cwd=top)
        if not rc and line in approved_lines(text):
            return True
    return False


def approval_verdict(ti, path):
    """None when the write adds no `## Approved-By:` line; else the refusal message (o9 W204 review, lane o9L1294).

    Only the human approves, and his Approve button writes the line through file_script.py (a Python process, which no
    Write or Edit hook sees). So a Write or Edit whose result holds an `## Approved-By:` line is refused unless that
    exact line is already (a) in the file on disk, where the human's Approve put it and not yet landed, (b) in the same path
    on origin/main, or (c) in a file on origin/main with the same script label (a version move to a new path). An Edit
    whose old text is not in the file is judged on its new text against its old text. Fails CLOSED: once a new line is
    seen, any error while looking for its ground refuses."""
    if not path:
        return None
    disk = None
    if os.path.isfile(path):
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            disk = fh.read()
    text = _text_after(ti, path)
    if text is not None:
        before = approved_lines(disk)
    else:
        text = ti.get("new_string", ti.get("newString")) or ""
        before = approved_lines(ti.get("old_string", ti.get("oldString")) or "")
    if not (_approve_scope(path, text) or _approve_scope(path, disk)):
        return None
    added = approved_lines(text) - before
    if not added:
        return None
    try:
        _top, on_ref = ref_show(path)
        added -= approved_lines(on_ref)
        added = {ln for ln in added if not _approved_elsewhere(path, ln)}
    except Exception:
        pass                            # fails closed: `added` stands
    if not added:
        return None
    return (
        "\nAPPROVED-BY-NOT-OWNERS - this write adds an approval line to %s:\n\n      %s\n\n"
        "  Only the human approves a script, with the Approve button in the Reader, which writes\n"
        "  `## Approved-By: the human <date> ...` itself. The Reader's Approved and Final badges,\n"
        "  check_checklist_router.py check 10 and training_header.findings() all read that\n"
        "  line, so an agent writing it would mark the script final in the human's name.\n\n"
        "  REMOVE the line from your write. This passes only when the exact line is already\n"
        "  in the file on disk, in the same path on origin/main, or (a version move) in a\n"
        "  file on origin/main with the same script label (%s).\n"
        % (os.path.basename(path), "\n      ".join(sorted(added)),
           parse_script_name(os.path.basename(path)).name_key or "this file has none"))


def cmd_hook():
    """Arm 2 AT THE MOMENT OF THE WRITE. Deny a write that sets `final` on an artifact the
    ref does not already call final.

    \u26d4 THIS IS THE ONLY PLACE ARM 2 CAN WORK, and o1 caught that the scan was the wrong
    shape: an agent lands a self-asserted `final` like any other work, after which disk and ref
    agree and there is nothing left to compare. Here the ref has not moved yet, so "disk says
    final, the ref does not" means exactly what it claims.

    \u26a0\ufe0f Fails OPEN on anything unexpected. A guard that cannot parse its input must
    not block the work.
    """
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        return _allow()

    ti = data.get("tool_input", {}) or {}
    if not isinstance(ti, dict):
        return _allow()
    path = ti.get("file_path") or ti.get("filePath") or ""

    # ⛔ THE APPROVAL ARM (o9 W204 review, lane o9L1294). An agent never writes `## Approved-By:`; the human's Approve
    # button does, through Python. Refused before anything else; an error in the arm itself fails open like the rest.
    try:
        refusal = approval_verdict(ti, path)
    except Exception:
        refusal = None
    if refusal:
        sys.stderr.write(refusal)
        return 2

    # ⛔ THE PROVENANCE ARM (FM4). A new script without its line is refused; an existing one
    # is told. Judged on the whole file as the write leaves it, not only the new text.
    advice = None
    try:
        verdict = provenance_verdict(ti, path) if path else None
    except Exception:
        verdict = None                  # fails open, like the rest of this hook
    if verdict and verdict[0] == "block":
        sys.stderr.write(verdict[1])
        return 2
    if verdict:
        advice = verdict[1]

    content = ti.get("content")
    if content is None:                 # Edit gives new_string; judge only the NEW text
        content = ti.get("new_string") or ti.get("newString") or ""
    if not path or not content or not is_versioned(path):
        return _allow(advice, data)

    m = HEADER.search(content)
    if not m or (m.group(2) or "").strip().lower() != "final":
        return _allow(advice, data)

    try:
        shown = ref_show(path)
        was = status_of(shown[1])
    except Exception:
        return _allow(advice, data)
    if was == "final":
        return _allow(advice, data)     # the human already landed it as final
    if final_ground(path, shown=shown):  # never raises: errors return None
        return _allow(advice, data)     # his Approve or his ruling is landed on origin/main

    _name = parse_script_name(os.path.basename(path))

    sys.stderr.write(
        "\nSELF-ASSERTED-FINAL - this write marks %s as `final`, and %s does not.\n\n"
        "  MASTER-TRAINING-DOC.md Section 4: \"ONLY THE HUMAN MAKES A VERSION `final`. AN AGENT\n"
        "  NEVER SETS THAT FIELD.\" `waypoint` is the default for everything an agent writes,\n"
        "  and it stays waypoint until he says otherwise in words.\n\n"
        "  Only `final` may be quoted as a specimen, so setting it grants this draft DE2\n"
        "  protection. o1 on review: that is the cheaper error to make, \"because nobody has\n"
        "  to be misread for it to happen.\"\n\n"
        "  WRITE `waypoint`. Three things make `final` pass, each read from origin/main only\n"
        "  (a change only in your working tree or on an unlanded branch does not count):\n"
        "    1. The human pressed the Reader's Approve button on this script. Approve\n"
        "       (file_script.py approve) writes `## Approved-By: the human <date> ...` into this\n"
        "       file's header; once that is on origin/main, write `final` again. The human,\n"
        "       2026-10-12: \"Approve should mark a script final. I'll only ever use it for that.\"\n"
        "    2. The human's ruling is landed in an OrchDoc entry (ORCHESTRATOR-DECISIONS-o<N>.md)\n"
        "       whose status is RESOLVED, that names this script by its label (%s) or file\n"
        "       name, and that quotes the human's own words with the word \"final\" in them (or says\n"
        "       \"the human ruled ... final\"). If the entry names a version next to the label\n"
        "       (`%s v7`), this file must be that version (this file is %s).\n"
        "    3. The file is already `final` on origin/main.\n"
        % (os.path.basename(path), "origin/main" if was is None else "the ref (%s)" % was,
           _name.name_key or "its label", _name.name_key or "LABEL",
           "v%d" % _name.version if _name.version is not None else "unversioned"))
    return 2


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "hook":
        return cmd_hook()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--ref", default="origin/main")
    ap.add_argument("--all", action="store_true",
                    help="walk the working tree for version artifacts")
    a = ap.parse_args()

    paths = list(a.paths)
    if a.all or not paths:
        paths.extend(walk_artifacts("."))

    checked = bad = 0
    every = []
    for p in paths:
        if not is_versioned(p):
            continue                    # not a version artifact; not this check's business
        if p in a.paths:
            # A file named on the command line is not opened when it sits in a private folder
            # under any of its names (o9 W152; the same rule as check_script.py's main()).
            from private_folders import in_private_folder
            if in_private_folder(p):
                print("  NOTE: not read: %s is in a private folder (private_folders.py)" % p)
                continue
        checked += 1
        fs = findings(p, ref=a.ref)
        every.extend(fs)
        if fs:
            bad += 1
            mark = "REFUSED " if blocks(fs) else "advisory"
            print("  [%s] %s" % (mark, p))
            for code, detail in fs:
                print("      %-20s %s" % (code, detail))

    print("\ntraining-header: %d artifact(s) checked, %d with findings" % (checked, bad))
    if bad:
        print(
            "\n  NO-HEADER / NO-DELTA / NO-UNFIXED - MASTER-TRAINING-DOC.md Section 4. The\n"
            "      marker goes ON the artifact because the mistake happens while reading the\n"
            "      artifacts, not while reading a doc about them. 'not assessed' is a valid\n"
            "      value for the third field; blank is not, because an item absent from the\n"
            "      list reads as fixed.\n"
            "  SELF-ASSERTED-FINAL - only the human makes a version final, and an agent never sets\n"
            "      that field. waypoint is the default for everything an agent writes. If you\n"
            "      are the human and you set this: land it, and the check goes quiet.\n")
    if blocks(every):
        return 1
    if bad:
        print("  All findings are ADVISORY - nothing here stops a caller. The corpus predates\n"
              "  Section 4, so every artifact is missing a header; that is a gap to fill, not a\n"
              "  turn to refuse.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
