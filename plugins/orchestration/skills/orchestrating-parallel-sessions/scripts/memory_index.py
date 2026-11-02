r"""MEMORY.md as a GENERATED VIEW - parse, prove, inject, build.

<!-- subject: knowledge-routing -->

    python .shared/scripts/memory_index.py roundtrip   # THE proof: parse -> render -> byte-diff
    python .shared/scripts/memory_index.py survey      # what the notes carry today
    python .shared/scripts/memory_index.py inject --limit 5      # dry-run by default
    python .shared/scripts/memory_index.py inject --limit 5 --write
    python .shared/scripts/memory_index.py build                 # regenerate from the notes
    python .shared/scripts/memory_index.py build --codes         # variant B
    python .shared/scripts/memory_index.py check                 # drift
    python .shared/scripts/memory_index.py --selftest

WHY THIS EXISTS. The human's rule: an index is a VIEW computed from the artifacts, never a file someone
appends to. `MEMORY.md` is hand-kept, loads into every session, and is the one artifact whose whole
value is being complete and current. He approved the mechanism on 2026-08-17 (`o9:D7`) - a numeric
`memory_index` in frontmatter whose INTEGER part is the group and DECIMAL part the position inside
it, so a plain sort reproduces order AND nesting. Measured 2026-09-18: 0 of 202 notes carried it.

=============================================================================
THE ORDER OF OPERATIONS, AND WHY IT IS THIS ORDER
=============================================================================

⛔ **NOTHING IS WRITTEN UNTIL `roundtrip` PASSES.** The parser has to reproduce the current file
BYTE FOR BYTE from its own parse. Until it does, any injection would be encoding a model of the
file that does not match the file - and the notes would then be the authority on a shape nobody
verified.

**The row template is what makes that possible.** A row is stored as prose with `{1}`, `{2}` slots:

    - ⭐ [Service Access Guide](service_access_guide.md) - READ FIRST when blocked on creds.
      template: "⭐ {1} - READ FIRST when blocked on creds."

    - ⭐✍️ Copy craft: [Idea Vault](a.md) + [NLP](b.md)
      template: "⭐✍️ Copy craft: {1} + {2}"

The template lives on the group's FIRST note; each note owns only its own title and target. So a
row's prose has exactly one home, and rendering a code instead of a filename (variant B) is a
change to one function.

⛔ **A NOTE CAN APPEAR IN MORE THAN ONE ROW, WITH A DIFFERENT TITLE EACH TIME - found by running
`survey` before writing anything, which is exactly why that step exists.**
`pwa_migrations_manual_apply.md` is *"migrations"* in row 3 and *"§2"* in row 5;
`backblaze_b2_cli.md` appears in row 3 and again in row 85. A scalar `memory_index` cannot express
that, and a design assuming one-note-one-row would have silently dropped one of each pair. **So the
field is a JSON LIST of occurrences** - `[{"at": "3.1", "title": "...", "row": "<template>"}]`,
with `row` present only where that note holds position 1.

=============================================================================
⛔ WHAT THIS DOES NOT DO - stated because a silent limit is the defect
=============================================================================

1. **It does not judge.** Grouping, order, markers and hooks are authored. This moves where they
   LIVE; it does not invent them.
2. **`inject` is per-note and reversible** - the memory dir is a git repo, every write is one
   commit, and `--write` is never the default.
3. **A note not referenced by MEMORY.md gets no index fields.** 202 notes, 177 links: the
   difference is real and is NOT a bug to fix here.
"""
import argparse
import collections
import io
import json
import os
import re
import subprocess
try:
    from no_window import flags
except ImportError:  # a partial delivery must not take the tool down
    def flags(extra=0):
        return extra
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

NL = chr(10)
try:
    from workspace_paths import MEM as _MEM
    MEMDIR = str(_MEM)
except Exception:  # a partial delivery: the folder under the home, named for the checkout
    MEMDIR = os.path.join(os.path.expanduser("~"), ".claude", "projects",
                          re.sub(r"[^A-Za-z0-9]", "-", os.path.join(os.path.expanduser("~"),
                                                                    "<your-workspace>")), "memory")
MEMFILE = os.path.join(MEMDIR, "MEMORY.md")

LINK = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
ROW = re.compile(r"^-\s(.*)$")

# ⭐ THE CONTROL FOR `verify-titles`, PINNED. `173c282` is the last commit that touched
# MEMORY.md before this conversion began - the final hand-kept version. It lives in git where the
# thing being checked cannot edit it. Move this forward deliberately, never to make a check pass.
BASELINE_REF = "1d58289"
# Moved 2026-09-18 from 173c282, deliberately and after looking: o9 added one row
# ("HOOKS run the MAIN checkout, not your worktree"). `verify-titles` reported 1 ADDED, 0 CHANGED,
# 0 REMOVED - a new note being indexed, not a word drifting.
#
# ⛔ THE OLD RULE HERE WAS BACKWARDS AND IT BLOCKED A CORRECT CHANGE. It read "move this
# forward only after reading a CHANGED count of zero." A CHANGED row is the ONLY thing that ever
# needs a human to look at it - so that rule permitted the move exactly when there was nothing to
# approve, and forbade it exactly when someone had looked and approved. o4 hit it on 2026-09-19:
# they corrected a row that was teaching a retracted number, read CHANGED 1, correctly refused to
# move the pin on the rule as written, and had to put it on the human's plate as o9:D41 - a decision
# about a constant in a script he will never open.
#
# ⭐ THE RULE, RESTATED: move this forward only after READING each CHANGED row and naming
# here why it is correct. That is what every move above actually did; the old phrasing described
# the easy case and accidentally outlawed the real one. "Never to make a check pass" still holds
# and is the thing the naming enforces - a reason you cannot write is a move you should not make.
#
# Moved 2026-09-19 from 0a780c9 to c01c064. ONE changed row, read and named:
# `pwa-promote-scripts-tests` went from "exactly 3 named tests expected" to "COUNT THE NAMES, do
# not assume 3 (measured 2 on 2026-09-18)" - the index was teaching the exact count its own note
# had retracted, after o10 repeated the wrong one into six lane seeds in a day. The row is right
# and the baseline was wrong. `check --live` is green either side of the move.
#
# Moved 2026-10-30 from c01c064 to 00e133e (lane o1L301, o1:T24, the human: "Efficiency rules. Do it").
# Against c01c064 the list was 24 ADDED, 1 REMOVED, 2 CHANGED, every row read:
#   - 24 ADDED: new notes indexed between 2026-09-19 and 2026-10-13, each with its own note file.
#   - REMOVED `todo_spatial_pan_tab.md`: de-indexed on purpose 2026-09-21 (o4) because the feature
#     shipped; the row is in Memory2.md line 66 with that reason.
#   - CHANGED `google_sheet_feature_tracker.md` "Google Sheet Feature Tracker" -> "Feature Tracker
#     (generated)": matches the note's own name ("GENERATED from the code; the Google Sheet is the
#     output"), changed in b02185c (o10:D70).
#   - CHANGED `pwa_promote_scripts_tests_fail_in_worktrees.md` "...fail in worktrees by name" ->
#     "...fail LOCALLY by name (worktree and main checkout)": matches the note's description,
#     measured in the main checkout 2026-09-23 (c58d488).
# 00e133e is the LAST commit whose MEMORY.md this command can match: from 4320fe9 (2026-10-14) on,
# MEMORY.md holds hand rows CARRIED by `build` (`hand_rows`), and this command compares against
# `collect_from_notes()` alone, so every later commit reports each carried row as REMOVED. One
# CHANGED row stays and is intended: `feedback_key_rotation_one_command.md` "Key rotation = ONE
# command for the human" -> "Key rotation = the SESSION does ALL of it", the human's ruling of 2026-10-22,
# matching the note's description (c09d0da).
#
# Moved 2026-10-30 from 00e133e to 1d58289 (lane o1L304, o1:W42). The limit above is fixed:
# `verify-titles` now merges the carried hand rows the same way `build` and `check --live` do.
# Before the fix, against 1d58289: 0 ADDED, 17 REMOVED, 0 CHANGED, and all 17 REMOVED were hand
# rows still present in that commit's MEMORY.md. After the fix: 218 links each side, 0 ADDED,
# 0 REMOVED, 0 CHANGED. Nothing to name; the move only takes in rows added since 00e133e.

# ⛔ THE ONE THING WITH NO BASELINE ANYWHERE. Hooks are AUTHORED and appear nowhere in the
# hand-kept MEMORY.md, so nothing can reconstruct one that goes missing - and the near-miss was
# real: re-running `inject` rewrites the field wholesale and would have eaten all 44. Pinning the
# count is the whole defence. Raise it deliberately when hooks are added; never lower it to make
# a run pass.
EXPECTED_HOOKS = 46
# HISTORY OF THIS PIN, newest last. Every move names the exact hook and the reason.
#   44 -> 43  2026-09-18, cold-store round. The guard fired; the missing hook was "11 Clarity
#             features scored against our needs", on `clarity_feature_evaluation.md`, a note the human
#             asked to cold-store. It left WITH its note - a removal, not a loss. ⛔ Verified from
#             git BEFORE lowering, which is the only acceptable reason to lower it.
#   43 -> 46  2026-09-18, the human's ruling on o9:D39(a). Three parked notes gained index rows -
#             `todo_spatial_pan_tab`, `todo_variant_archive`, `post_launch_audio_value_adds` -
#             each with its own hook. ⭐ The guard reported the RISE as a note rather than a
#             failure, which is the right asymmetry: hooks appearing is normal, hooks vanishing
#             is not.


# --------------------------------------------------------------------------- parse
def parse_memory(text):
    """[{row, template, links:[{pos,title,target}]}] - one record per '- ' line, in file order.

    ⛔ Rows are 1-based and gapless: the row number IS the group, so renumbering a row silently
    re-points every note in it. Never re-derive these from a filtered list.
    """
    rows = []
    for n, raw in enumerate(text.split(NL), start=1):
        if not raw.strip():
            continue
        m = ROW.match(raw)
        if not m:
            raise ValueError("line %d is not a '- ' row: %r" % (n, raw[:60]))
        body = m.group(1)
        links = []
        out, last, pos = [], 0, 0
        for lm in LINK.finditer(body):
            pos += 1
            out.append(body[last:lm.start()])
            out.append("{%d}" % pos)
            links.append({"pos": pos, "title": lm.group(1), "target": lm.group(2)})
            last = lm.end()
        out.append(body[last:])
        rows.append({"row": len(rows) + 1, "template": "".join(out), "links": links})
    return rows


HOOK_SEP = " – "        # spaced en-dash: reads inside a '+'-joined list, unlike ' - '


CODE_DIR = "m"          # short on purpose: it is repeated once per link


def render_link(title, target, code=None, hook=None, style="path"):
    """style only matters when `code` is set.

    ⭐ "path" IS THE DEFAULT ON PURPOSE, AND IT IS 890 BYTES WORSE THAN "bare". Measured
    2026-09-18 on the live 106 rows: bare `[Title][24]` is 15,655 bytes, `[Title](m/24.md)` is
    16,545. The extra 890 buys away the entire resolution mechanism:

      bare  - needs a CLAUDE.md line AND/OR a PreToolUse hook, both edits to the human's config, and
              the sealed test measured 1 agent in 6 following a code unaided. D30's security
              finding also says the instruction cannot live in MEMORY.md, because content telling
              a session to go read another file and trust it is shaped like prompt injection -
              one agent refused on exactly those grounds and was right to.
      path  - `Read` resolves it natively. No instruction to distrust, no hook, no judgement,
              nothing in anyone's config.

    ⛔ 890 bytes is 0.9% of the hard limit. The mechanism it removes is the part that failed
    5 times out of 6 when it was tested.
    """
    if code is None:
        base = "[%s](%s)" % (title, target)
    elif style == "bare":
        base = "[%s][%s]" % (title, code)
    else:
        base = "[%s](%s/%s.md)" % (title, CODE_DIR, code)
    return base + HOOK_SEP + hook if hook else base


def render_rows(rows, codes=None, hooks=True, style="path"):
    """codes: {target: code} renders variant B. hooks=False renders the pre-step-2 shape."""
    out = []
    for r in rows:
        line = r["template"]
        for l in r["links"]:
            c = None if codes is None else codes.get(l["target"])
            h = l.get("hook") if hooks else None
            line = line.replace("{%d}" % l["pos"],
                                render_link(l["title"], l["target"], c, h, style), 1)
        out.append("- " + line)
    return NL.join(out)


CODES_FILE = "codes.json"   # lives in <MEMDIR>/m/, tracked in the memory repo
LOCK_WAIT = 60.0            # seconds a build waits for another build's code-table lock


def codes_path():
    """Read at call time, so a test that points MEMDIR at a copy gets the copy's table."""
    return os.path.join(MEMDIR, CODE_DIR, CODES_FILE)


def load_code_table(path=None):
    """{"assigned": {code: target}, "retired": {code: target}} - empty when the file is absent.

    ⭐ AN ABSENT TABLE IS THE SEED. With nothing assigned, `assign_codes` numbers notes in
    first-encounter order from 1 - exactly the positional rule this table replaced - so the first
    build after the switch reproduces every live code and then writes them down.
    """
    path = path or codes_path()
    if not os.path.exists(path):
        return {"assigned": {}, "retired": {}}
    return validate_code_table(io.open(path, encoding="utf-8").read(), path)


class CodeTableError(Exception):
    """codes.json is unreadable or inconsistent, or another build holds its lock. `main` turns
    this into a [REFUSE] and exit 2; nothing is written."""


def validate_code_table(text, path="codes.json"):
    """Parse and check a code table; raise CodeTableError on ANY defect.

    ⛔ A BAD TABLE REFUSES THE BUILD, IT NEVER GETS REPAIRED. Read leniently, a duplicate JSON
    key keeps only the last note on that code and the other note is handed a NEW number; a
    target listed under two codes moves to the higher one. Both are a silent renumber, the one
    thing this table exists to prevent. So a corrupt file, a duplicate key, a code that is not a
    positive integer, a code both assigned and retired, or a note under two codes all stop here
    with the reason, and a person fixes the file (its git history in the memory repo has every
    good version)."""
    def no_dup_keys(pairs):
        seen = collections.OrderedDict()
        for k, v in pairs:
            if k in seen:
                raise CodeTableError("%s: duplicate key %r - two entries claim the same code"
                                     % (path, k))
            seen[k] = v
        return seen
    try:
        t = json.loads(text, object_pairs_hook=no_dup_keys)
    except ValueError as e:
        raise CodeTableError("%s is not valid JSON (%s)" % (path, e))
    if not isinstance(t, dict):
        raise CodeTableError("%s: top level is not an object" % path)
    out = {}
    for k in ("assigned", "retired"):
        v = t.get(k) or {}
        if not isinstance(v, dict):
            raise CodeTableError("%s: %r is not an object" % (path, k))
        for c, target in v.items():
            if not re.match(r"^[1-9]\d*$", c):
                raise CodeTableError("%s: %s code %r is not a positive integer" % (path, k, c))
            if not isinstance(target, str) or not target:
                raise CodeTableError("%s: %s code %s has no note filename" % (path, k, c))
        out[k] = dict(v)
    both = sorted(set(out["assigned"]) & set(out["retired"]), key=int)
    if both:
        raise CodeTableError("%s: code(s) %s are both assigned and retired" % (path, both))
    owner = {}
    for k in ("assigned", "retired"):
        for c, target in out[k].items():
            if target in owner:
                raise CodeTableError("%s: note %s is under two codes (%s and %s)"
                                     % (path, target, owner[target], c))
            owner[target] = c
    return out


class code_table_lock(object):
    """Hold `<codes.json>.lock` for one build's load -> assign -> save -> write.

    ⛔ TWO BUILDS AT ONCE WOULD BOTH READ THE SAME TABLE, both give the next free number to
    their own new note, and the later save would drop the earlier note's number - after the
    earlier build had already written a pointer for it. The lock makes the second build wait
    and then read the first one's table. A lock left by a crashed build is not broken
    automatically: after `wait` seconds the build refuses and names the file to delete."""

    def __init__(self, path=None, wait=None):
        self.path = (path or codes_path()) + ".lock"
        self.wait = LOCK_WAIT if wait is None else wait

    def __enter__(self):
        import time
        d = os.path.dirname(self.path)
        if not os.path.isdir(d):
            os.makedirs(d)
        deadline = time.time() + self.wait
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, ("pid %d\n" % os.getpid()).encode("ascii"))
                os.close(fd)
                return self
            except OSError:
                if not os.path.exists(self.path):
                    continue
                if time.time() >= deadline:
                    raise CodeTableError(
                        "another build holds %s (waited %ds). If no memory_index.py build is "
                        "running, a crashed one left it: delete that file and rebuild."
                        % (self.path, self.wait))
                time.sleep(0.2)

    def __exit__(self, *exc):
        try:
            os.remove(self.path)
        except OSError:
            pass


def save_code_table(table, path=None):
    """Write the table ATOMICALLY: a temp file in the same folder, then os.replace, so a reader
    or a crash never sees a half-written codes.json."""
    import time
    path = path or codes_path()
    d = os.path.dirname(path)
    if not os.path.isdir(d):
        os.makedirs(d)
    out = collections.OrderedDict()
    out["_about"] = ("GENERATED by memory_index.py build --codes, but it is STATE, not a view: "
                     "a code is given once and never moves or gets reused. A retired code "
                     "stays reserved. Do not renumber by hand.")
    for k in ("assigned", "retired"):
        out[k] = collections.OrderedDict(
            (c, table[k][c]) for c in sorted(table[k], key=int))
    tmp = "%s.tmp-%d" % (path, os.getpid())
    with io.open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(json.dumps(out, indent=1, ensure_ascii=False) + NL)
    for attempt in range(10):   # Windows: a reader holding the file open blocks the replace
        try:
            os.replace(tmp, path)
            return
        except OSError:
            if attempt == 9:
                os.remove(tmp)
                raise
            time.sleep(0.1)


def assign_codes(rows, table):
    """(codes {target: code}, new table). Pure - the caller decides whether to save.

    ⛔ A CODE IS NEVER DERIVED FROM POSITION. It used to be `str(len(codes) + 1)` in
    first-encounter order, so a note indexed ahead of others renumbered every code after it and
    any text outside MEMORY.md citing `m/<n>.md` came to mean another note with nothing breaking
    (see `citations`). Now a target keeps the number it was first given; a new target takes the
    next number above every number ever used, retired ones included; a target that leaves the
    index is RETIRED, and its number is reserved for it - if the same note comes back it gets
    its old number again, and no other note ever gets it.
    """
    assigned = dict(table["assigned"])
    retired = dict(table["retired"])
    by_target = {t: c for c, t in assigned.items()}
    back = {t: c for c, t in retired.items()}
    used = [int(c) for c in list(assigned) + list(retired)]
    nxt = (max(used) if used else 0) + 1
    codes = collections.OrderedDict()
    for r in rows:
        for l in r["links"]:
            t = l["target"]
            if t in codes:
                continue
            if t in by_target:
                codes[t] = by_target[t]
            elif t in back:
                c = back[t]
                del retired[c]
                assigned[c] = t
                codes[t] = c
            else:
                c = str(nxt)
                nxt += 1
                assigned[c] = t
                codes[t] = c
    for c, t in list(assigned.items()):
        if t not in codes:
            del assigned[c]
            retired[c] = t
    return codes, {"assigned": assigned, "retired": retired}


STUB_LINE_FMT = "**The note is [`%s`](../%s).** Read that; this file only points at it."
STUB_TARGET_RE = re.compile(r"The note is \[`([\w./-]+\.md)`\]")


def stub_target(text):
    """The note filename a pointer stub points at, or None. The ONE reader of the line that
    write_pointers writes with STUB_LINE_FMT; route.py imports this (marker-format contract)."""
    m = STUB_TARGET_RE.search(text or "")
    return m.group(1).split("/")[-1] if m else None


def write_pointers(codes, outdir, retired=None):
    """One tiny file per code, so a code IS a path and `Read` needs no help.

    ⛔ THESE ARE GENERATED AND CARRY NO AUTHORED WORDS. If a pointer and its note ever disagree,
    the note wins and the pointer is stale output - which is why it says so in its own text and
    why nothing but this function writes them.

    ⭐ A RETIRED CODE KEEPS A FILE that says the note was removed, so an old citation of it
    reads "retired" instead of a missing file or, worse, another note. `retired` is {code: target}.
    """
    if not os.path.isdir(outdir):
        os.makedirs(outdir)
    keep = set()
    for code, target in (retired or {}).items():
        keep.add("%s.md" % code)
        io.open(os.path.join(outdir, "%s.md" % code), "w", encoding="utf-8",
                newline="").write(NL.join([
                    "<!-- GENERATED by memory_index.py - do not hand-edit, the note is the source -->",
                    "",
                    "# MEMORY code %s - RETIRED" % code,
                    "",
                    "**Code %s is retired.** It pointed at `%s`, which is no longer in the index "
                    "(the note was removed, renamed or cold-stored). The number stays reserved and "
                    "is never given to another note." % (code, target),
                    "",
                ]) + NL)
    for target, code in codes.items():
        p = os.path.join(outdir, "%s.md" % code)
        keep.add("%s.md" % code)
        io.open(p, "w", encoding="utf-8", newline="").write(NL.join([
            "<!-- GENERATED by memory_index.py - do not hand-edit, the note is the source -->",
            "",
            "# MEMORY code %s" % code,
            "",
            STUB_LINE_FMT % (target, target),
            "",
        ]) + NL)
    # ⭐ remove codes that no longer exist, or the directory becomes its own stale index
    removed = 0
    for fn in os.listdir(outdir):
        if fn.endswith(".md") and fn not in keep:
            os.remove(os.path.join(outdir, fn))
            removed += 1
    return len(keep), removed


# --------------------------------------------------------------------------- frontmatter
def split_frontmatter(text):
    """(fm_text, rest) or (None, text). Only a leading '---' block counts."""
    if not text.startswith("---"):
        return None, text
    i = text.find(NL)
    if i < 0:
        return None, text
    end = text.find(NL + "---", i)
    if end < 0:
        return None, text
    nl2 = text.find(NL, end + 1 + 3)
    if nl2 < 0:
        nl2 = len(text)
    return text[i + 1:end], text[nl2 + 1:]


def fm_get(fm, key):
    if fm is None:
        return None
    pat = re.compile(r"^" + re.escape(key) + r":\s*(.*)$", re.M)
    m = pat.search(fm)
    return m.group(1).strip() if m else None


def fm_set(fm, key, value):
    """Set or replace a scalar key. Returns the new frontmatter text."""
    line = "%s: %s" % (key, value)
    if fm is None:
        return line
    pat = re.compile(r"^" + re.escape(key) + r":.*$", re.M)
    if pat.search(fm):
        return pat.sub(line.replace("\\", "\\\\"), fm, count=1)
    return fm.rstrip(NL) + NL + line


# --------------------------------------------------------------------------- the index field
# ⛔ A MARKER'S SHAPE IS A CONTRACT BETWEEN EVERY TOOL THAT READS IT (o9 W149 box 8, 2026-10-03).
# When the Write tool saves a memory note it rewrites the frontmatter: keys it does not own move
# under `metadata:`, and the one-line JSON value of `memory_index` becomes a YAML block list:
#
#     metadata:
#       node_type: memory
#       memory_index:
#         - at: recovery-backup.1
#           title: Restore after laptop death
#           row: "⭐ Recovery/backup: {1} (CANONICAL 10-step) + {2}."
#           order: 87
#
# The memory repo's history holds 7 notes saved that way, 2026-09-21 to 2026-09-28 (commit db7d3ac
# is one). This file read only a top-level `memory_index:` line, so such a note got no MEMORY.md
# row until someone moved the line back by hand, and the knowledge gate then refused it as a note
# no index reaches.
#
# ⭐ ONE READER AND ONE WRITER, HERE. `read_index` reads either place; `fm_set_index` writes the
# top-level line and removes a nested copy, so a note never carries two. Every command in this
# file and memory_hooks_apply.py go through them, and hook_memory_index_drift.py runs
# `check --live`, so it does too. Test: test_memory_index_nested.py.
INDEX_KEY = "memory_index"


class IndexShapeError(ValueError):
    """A `memory_index` value written in a YAML shape `_Yaml` does not read."""


_YAML_KEY = re.compile(r"""^(?P<k>[A-Za-z_][A-Za-z0-9_.-]*|"[^"]*"|'[^']*')[ \t]*:"""
                       r"""(?:[ \t]+(?P<v>.*?))?[ \t]*$""")
_DQ_ESCAPES = {"0": "\0", "a": "\a", "b": "\b", "t": "\t", "\t": "\t", "n": "\n", "v": "\v",
               "f": "\f", "r": "\r", "e": "\x1b", " ": " ", '"': '"', "/": "/", "\\": "\\",
               "N": "\x85", "_": "\xa0", "L": " ", "P": " "}


def _is_dash(text):
    return text == "-" or text.startswith("- ")


def _strip_comment(v):
    m = re.search(r"[ \t]#", v)
    return (v[:m.start()] if m else v).strip()


def _plain_value(s):
    """YAML 1.2 core resolution, except a float stays text: an `at` such as "3.10" must keep
    its digits, and nothing in this field is a float."""
    if re.match(r"^[-+]?[0-9]+$", s):
        return int(s)
    if s in ("true", "True", "TRUE"):
        return True
    if s in ("false", "False", "FALSE"):
        return False
    if s in ("", "~", "null", "Null", "NULL"):
        return None
    return s


def _dq_unescape(s):
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        e = s[i + 1:i + 2]
        if e in _DQ_ESCAPES and e:
            out.append(_DQ_ESCAPES[e])
            i += 2
            continue
        n = {"x": 2, "u": 4, "U": 8}.get(e)
        digits = s[i + 2:i + 2 + n] if n else ""
        if not n or not re.match(r"^[0-9A-Fa-f]{%d}$" % n, digits):
            raise IndexShapeError("an escape it cannot read in a quoted value: %r" % s[i:i + 6])
        out.append(chr(int(digits, 16)))
        i += 2 + n
    # A writer that escapes non-ASCII as JSON does writes an emoji as two \u surrogates.
    return "".join(out).encode("utf-16", "surrogatepass").decode("utf-16")


def _closing_quote(s, q):
    i = 1
    while i < len(s):
        if q == '"' and s[i] == "\\":
            i += 2
            continue
        if s[i] == q:
            if q == "'" and s[i + 1:i + 2] == "'":
                i += 2
                continue
            return i
        i += 1
    return None


def _fold_quoted(body, dq):
    """YAML folding for a quoted value that spans lines: each line is trimmed, one line break
    becomes a space, each empty line a newline, and in double quotes a backslash at the end of
    a line joins it to the next with nothing between."""
    lines = body.split("\n")
    if len(lines) == 1:
        return body
    out, empties, glue = "", 0, None
    for n, ln in enumerate(lines):
        last = n == len(lines) - 1
        seg = ln if n == 0 else ln.lstrip(" \t")
        escaped = False
        if not last:
            tail = len(seg) - len(seg.rstrip("\\"))
            if dq and tail % 2 == 1:
                seg, escaped = seg[:-1], True
            else:
                seg = seg.rstrip(" \t")
        if n == 0:
            out = seg
        elif seg == "" and not last and glue is None:
            empties += 1
            continue
        else:
            out += (glue if glue is not None else ("\n" * empties if empties else " ")) + seg
        empties, glue = 0, ("" if escaped else None)
    return out


class _Yaml(object):
    """A strict reader for the YAML a frontmatter writer emits for this field: block mappings
    and lists, plain and quoted scalars (on one line or folded over several), `|` and `>` block
    scalars, and a JSON value on one line. Anchors, aliases, tags and flow collections that are
    not JSON raise IndexShapeError, so a shape it does not know is reported, never guessed."""

    def __init__(self, lines):
        self.lines = [l.rstrip("\r") for l in lines]
        self.i = 0

    def fail(self, why):
        raise IndexShapeError("%s, at line %d of the field" % (why, self.i + 1))

    def peek(self):
        """(indent, text) of the next line with content, or None. Skips blanks and comments."""
        while self.i < len(self.lines):
            s = self.lines[self.i]
            t = s.strip()
            if not t or t.startswith("#"):
                self.i += 1
                continue
            lead = s[:len(s) - len(s.lstrip(" \t"))]
            if "\t" in lead:
                self.fail("a tab in the indentation")
            return len(lead), t
        return None

    def node(self, indent):
        p = self.peek()
        if p is None or p[0] < indent:
            return None
        if _is_dash(p[1]):
            return self.seq(p[0])
        if _YAML_KEY.match(p[1]):
            return self.mapping(p[0])
        self.fail("expected a list or a mapping")

    def seq(self, ind):
        out = []
        while True:
            p = self.peek()
            if p is None or p[0] < ind:
                return out
            if p[0] > ind or not _is_dash(p[1]):
                if p[0] == ind:
                    return out              # the parent mapping's next key
                self.fail("unexpected indentation")
            rest = p[1][1:].lstrip(" ")
            self.i += 1
            if not rest:
                out.append(self.node(ind + 1))
            elif _YAML_KEY.match(rest):
                out.append(self.mapping(ind + len(p[1]) - len(rest), first=rest))
            else:
                out.append(self.scalar(rest, ind))

    def mapping(self, ind, first=None):
        out = {}
        while True:
            if first is not None:
                text, first = first, None
            else:
                p = self.peek()
                if p is None or p[0] < ind:
                    return out
                if p[0] > ind:
                    self.fail("unexpected indentation")
                text = p[1]
                if _is_dash(text):
                    self.fail("a list item where a key was expected")
                self.i += 1
            m = _YAML_KEY.match(text)
            if not m:
                self.fail("expected `key: value`")
            k = m.group("k")
            if k[0] == '"':
                k = _dq_unescape(k[1:-1])
            elif k[0] == "'":
                k = k[1:-1]
            if k in out:
                self.fail("the key %r appears twice" % k)
            v = m.group("v") or ""
            if v and not v.startswith("#"):
                out[k] = self.scalar(v, ind)
                continue
            nxt = self.peek()
            if nxt is not None and nxt[0] > ind:
                out[k] = self.node(nxt[0])
            elif nxt is not None and nxt[0] == ind and _is_dash(nxt[1]):
                out[k] = self.seq(ind)          # a list at its key's own indent (PyYAML's style)
            else:
                out[k] = None

    def scalar(self, v, ind):
        """The value after `key: ` or `- `, with any lines it continues onto."""
        if v[0] in "\"'":
            q, buf = v[0], v
            end = _closing_quote(buf, q)
            while end is None:
                if self.i >= len(self.lines):
                    self.fail("a quoted value is never closed")
                buf += "\n" + self.lines[self.i]
                self.i += 1
                end = _closing_quote(buf, q)
            after = buf[end + 1:].strip()
            if after and not after.startswith("#"):
                self.fail("text after a closing quote")
            body = _fold_quoted(buf[1:end], q == '"')
            return _dq_unescape(body) if q == '"' else body.replace("''", "'")
        if v[0] in "|>":
            return self.block_scalar(v, ind)
        if v[0] in "[{":
            try:
                return json.loads(v)
            except ValueError:
                self.fail("a flow value that is not JSON")
        if v[0] in "&*!%@`":
            self.fail("an anchor, alias or tag")
        parts = [_strip_comment(v)]
        while self.i < len(self.lines):
            s = self.lines[self.i]
            t = s.strip()
            if not t or t.startswith("#") or len(s) - len(s.lstrip(" ")) <= ind:
                break
            if _YAML_KEY.match(t) or _is_dash(t):
                self.fail("a key or list item inside a plain value")
            parts.append(_strip_comment(t))
            self.i += 1
        return _plain_value(" ".join(parts))

    def block_scalar(self, v, ind):
        m = re.match(r"^([|>])([+-]?)([1-9]?)([+-]?)[ \t]*(#.*)?$", v)
        if not m:
            self.fail("a block scalar header it cannot read")
        folded, chomp = m.group(1) == ">", m.group(2) or m.group(4)
        content = ind + int(m.group(3)) if m.group(3) else None
        body = []
        while self.i < len(self.lines):
            s = self.lines[self.i]
            if not s.strip():
                body.append("")
                self.i += 1
                continue
            lead = len(s) - len(s.lstrip(" "))
            if content is None:
                if lead <= ind:
                    break
                content = lead
            if lead < content:
                break
            body.append(s[content:])
            self.i += 1
        trail = 0
        while body and body[-1] == "":
            body.pop()
            trail += 1
        if not body:
            return "\n" * trail if chomp == "+" else ""
        if not folded:
            text = "\n".join(body)
        else:
            text, prev, empties = "", None, 0
            for ln in body:
                if ln == "":
                    empties += 1
                    continue
                if prev is None:
                    text = "\n" * empties + ln
                else:
                    more = ln.startswith((" ", "\t")) or prev.startswith((" ", "\t"))
                    text += ("\n" if more else "") + "\n" * empties + ln if empties else \
                        ("\n" if more else " ") + ln
                prev, empties = ln, 0
        if chomp == "-":
            return text
        return text + "\n" * (trail + 1 if chomp == "+" else 1)


_META_LINE = re.compile(r"^metadata:[ \t]*(#.*)?$")
_INDEX_LINE = re.compile(r"^( *)" + INDEX_KEY + r"[ \t]*:")
# The key anywhere else - deeper, quoted, or inside a flow mapping. Only used to report a shape
# nobody reads, by `_index_elsewhere`.
# ⛔ A KEY, NEVER A VALUE (o9L1023). This was one regex matching the words on any line, so a
# description that mentions `memory_index:` was reported as the field in a shape nobody reads,
# and so was an empty `memory_index:` line, which the readers before read_index skipped. Either
# one printed a [warn] line and failed roundtrip's `not problems`.
_INDEX_KEY_LINE = re.compile(r"^[ \t]*(?:-[ \t]+)?([\"']?)" + INDEX_KEY + r"\1[ \t]*:(.*)$")
_INDEX_FLOW_KEY = re.compile(r"[{,][ \t]*([\"']?)" + INDEX_KEY + r"\1[ \t]*:")
# A quoted value, where YAML opens one: at a line's start, after `- `, after `: `, or after
# `[`, `{` or `,`. An apostrophe inside a word opens nothing.
_QUOTED_VALUE = re.compile(r"(^[ \t]*(?:-[ \t]+)?|:[ \t]+|[\[{,][ \t]*)"
                           r"(\"(?:[^\"\\]|\\.)*\"|'(?:[^']|'')*')", re.M)
# A key whose value is a `|` or `>` block: the deeper lines that follow are its text.
_BLOCK_OPEN = re.compile(r"(?:^[ \t]*-|:)[ \t]+[|>][-+0-9]*[ \t]*(?:#.*)?$")


def _blank_quoted(text):
    """`text` with what is inside each quoted value turned to spaces, its line breaks kept, so a
    value can never read as a key. A quoted key that is the field's own name is kept."""
    def blank(m):
        q = m.group(2)
        if q[1:-1] == INDEX_KEY:
            return m.group(0)
        return m.group(1) + q[0] + re.sub(r"[^\n]", " ", q[1:-1]) + q[-1]
    return _QUOTED_VALUE.sub(blank, text)


def _index_elsewhere(lines, skip):
    """True when a line of this frontmatter, outside the line numbers in `skip` (the field where
    read_index reads it), holds memory_index as a KEY with a value: a key line at any depth or
    quoted, or a key inside a flow mapping. Quoted values and `|`/`>` block text are values, never
    keys, and a key with no value is skipped, as it always was."""
    plain = _blank_quoted(NL.join(ln.rstrip("\r") for ln in lines)).split(NL)
    block = None                       # indent of the key whose | or > text is being passed
    for i, s in enumerate(plain):
        if not s.strip():
            continue
        lead = len(s) - len(s.lstrip(" "))
        if block is not None:
            if lead > block:
                continue               # a line of block text: a value
            block = None
        if _BLOCK_OPEN.search(s):
            block = lead
        if i in skip:
            continue
        m = _INDEX_KEY_LINE.match(s)
        if m:
            rest = m.group(2).strip()
            if rest and not rest.startswith("#"):
                return True
            nxt = next((t for t in plain[i + 1:] if t.strip()), "")
            deeper = len(nxt) - len(nxt.lstrip(" "))
            if nxt and (deeper > lead or (deeper == lead and _is_dash(nxt.strip()))):
                return True            # its value is the block below it
            continue                   # `memory_index:` with no value
        for f in _INDEX_FLOW_KEY.finditer(s):
            before = s[:f.start() + 1]
            if before.count("{") > before.count("}"):
                return True            # inside a flow mapping that is still open
    return False


def _field_span(lines, k, ind):
    """End (exclusive) of the field whose key sits on line k at indent `ind`: the lines indented
    deeper, plus list items at `ind` itself (PyYAML's style). Trailing blank lines stay outside."""
    end = j = k + 1
    while j < len(lines):
        s = lines[j].rstrip("\r")
        t = s.strip()
        if t:
            lead = len(s) - len(s.lstrip(" "))
            if lead < ind or (lead == ind and not _is_dash(t)):
                break
            end = j + 1
        j += 1
    return end


def _index_spans(lines):
    """{"top": (start, end) or None, "metadata": (start, end, indent) or None}: where the field
    sits in these frontmatter lines (each may keep its '\r')."""
    out = {"top": None, "metadata": None}
    for i, raw in enumerate(lines):
        s = raw.rstrip("\r")
        if s.startswith(INDEX_KEY) and _INDEX_LINE.match(s) and out["top"] is None:
            out["top"] = (i, _field_span(lines, i, 0))
        if _META_LINE.match(s) and out["metadata"] is None:
            key_ind = None
            for j in range(i + 1, len(lines)):
                b = lines[j].rstrip("\r")
                if not b.strip():
                    continue
                lead = len(b) - len(b.lstrip(" "))
                if lead == 0:
                    break                             # the next top-level key ends the block
                if key_ind is None:
                    key_ind = lead
                if lead == key_ind and _INDEX_LINE.match(b):
                    out["metadata"] = (j, _field_span(lines, j, key_ind), key_ind)
                    break
    return out


def _parse_field(lines, span, ind):
    """The field's value from its lines. A string is the JSON line itself, quoted as YAML."""
    v = _Yaml(lines[span[0]:span[1]]).mapping(ind).get(INDEX_KEY)
    if isinstance(v, str):
        v = json.loads(v)
    return v


def read_index(fm):
    """(occurrences, where, problem) for a note's frontmatter text - THE reader of the field.

    where: "top" for a `memory_index:` line at column 0 (the form this file writes), "metadata"
    for one nested under `metadata:` (the form the Write tool leaves). problem: a sentence when
    the field is there and cannot be used, else None. (None, None, None) means no field.
    Both places set and equal is fine; both set and different is a problem, and the top-level
    value, the one a script wrote, is returned."""
    if fm is None:
        return None, None, None
    lines = fm.split(NL)
    spans = _index_spans(lines)
    top = nested = None
    top_err = nested_err = None
    if spans["top"]:
        try:
            top = _parse_field(lines, spans["top"], 0)
        except (ValueError, TypeError) as e:
            top_err = "memory_index is not JSON (%s)" % e
    if spans["metadata"]:
        s, e_, ind = spans["metadata"]
        try:
            nested = _parse_field(lines, (s, e_), ind)
        except (ValueError, TypeError) as e:
            nested_err = "memory_index under metadata: cannot be read (%s)" % e
    if top is not None or top_err:
        if top_err:
            return None, "top", top_err
        if nested_err:
            return top, "top", nested_err
        if nested is not None and nested != top:
            return top, "top", ("memory_index is set at the top level AND under metadata:, and "
                                "they differ; the top-level one is used")
        where = "top"
        value = top
    elif nested is not None or nested_err:
        if nested_err:
            return None, "metadata", nested_err
        where = "metadata"
        value = nested
    else:
        # A field found where it is read, with no value, is skipped silently, as it always was.
        skip = set(range(*spans["top"])) if spans["top"] else set()
        if spans["metadata"]:
            skip.update(range(spans["metadata"][0], spans["metadata"][1]))
        if _index_elsewhere(lines, skip):
            return None, None, ("memory_index is written in a shape this reader does not read "
                                "(neither a top-level line nor a key under metadata:)")
        return None, None, None
    if not isinstance(value, list):
        return None, where, "memory_index is not a list"
    return value, where, None


def fm_set_index(fm, value):
    """Write `memory_index: <value>` as the top-level line and remove a copy nested under
    `metadata:`, so the note carries the field once - the writers' half of `read_index`.
    The frontmatter's own line ending is kept."""
    line = "%s: %s" % (INDEX_KEY, value)
    if fm is None:
        return line
    lines = fm.split(NL)
    spans = _index_spans(lines)
    if spans["metadata"]:
        s, e, _ind = spans["metadata"]
        del lines[s:e]
        spans = _index_spans(lines)
    if spans["top"]:
        s, e = spans["top"]
        lines[s:e] = [line + ("\r" if lines[e - 1].endswith("\r") else "")]
        return NL.join(lines)
    out = NL.join(lines)
    tail = "\r" if out.endswith("\r") else ""
    return out.rstrip("\r" + NL) + ("\r" if "\r\n" in fm else "") + NL + line + tail


def write_frontmatter(path, newfm):
    txt = io.open(path, encoding="utf-8", errors="replace").read()
    fm, rest = split_frontmatter(txt)
    if fm is None:
        body = "---" + NL + newfm.rstrip(NL) + NL + "---" + NL + txt
    else:
        body = "---" + NL + newfm.rstrip(NL) + NL + "---" + NL + rest
    io.open(path, "w", encoding="utf-8", newline="").write(body)


def yaml_quote(s):
    """Minimal, predictable quoting - these values carry markdown, colons and emoji."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


# --------------------------------------------------------------------------- commands
def cmd_roundtrip(a):
    orig = io.open(MEMFILE, encoding="utf-8", errors="replace").read()
    rows = parse_memory(orig)
    back = render_rows(rows)
    o = orig.rstrip(NL)
    ok = back == o
    print("rows parsed          %4d" % len(rows))
    print("links                %4d" % sum(len(r["links"]) for r in rows))
    print("original chars       %4d" % len(o))
    print("re-rendered chars    %4d" % len(back))
    if ok:
        print()
        print("[OK] BYTE-IDENTICAL. The parse represents every row without loss.")
        return 0
    print()
    print("[FAIL] the re-render differs. First differing rows:")
    ol, bl = o.split(NL), back.split(NL)
    shown = 0
    for i in range(max(len(ol), len(bl))):
        x = ol[i] if i < len(ol) else "<missing>"
        y = bl[i] if i < len(bl) else "<missing>"
        if x != y:
            print("   row %s" % (i + 1))
            print("     orig: %s" % x[:150])
            print("     back: %s" % y[:150])
            shown += 1
            if shown >= 5:
                break
    return 1


def cmd_survey(a):
    orig = io.open(MEMFILE, encoding="utf-8", errors="replace").read()
    rows = parse_memory(orig)
    targets = [l["target"] for r in rows for l in r["links"]]
    onfile, missing = [], []
    for t in targets:
        (onfile if os.path.exists(os.path.join(MEMDIR, t)) else missing).append(t)
    multi = [r for r in rows if len(r["links"]) > 1]
    print("rows                         %4d" % len(rows))
    print("  single-link                %4d" % (len(rows) - len(multi)))
    print("  multi-link                 %4d  carrying %d links"
          % (len(multi), sum(len(r["links"]) for r in multi)))
    print("links                        %4d" % len(targets))
    print("  resolve to a file          %4d" % len(onfile))
    print("  DO NOT resolve             %4d" % len(missing))
    for t in missing[:10]:
        print("     %s" % t)
    dup = [t for t, c in collections.Counter(targets).items() if c > 1]
    print("  targets linked twice       %4d" % len(dup))
    for t in dup[:5]:
        print("     %s" % t)
    return 0


SLUG_BAD = re.compile(r"[^a-z0-9]+")


def row_key(r):
    """A stable name for a row, derived from its own prose.

    ⛔ WHY NOT THE ROW NUMBER. The number is file ORDER. Inserting one row in the middle shifts
    every later row, so every note after it carries a wrong `at` - about 170 notes rewritten to
    add one line to the index. Nobody would do that, so the index would go back to being edited by
    hand, which is the thing this replaces. o9 named it: *"position 1 is not the wrong member, it
    is the wrong KIND of place."*

    ⭐ So identity is a KEY and order is a separate integer. Renumbering moves `order` and never
    touches identity.
    """
    prefix = re.split(r"\{1\}", r["template"])[0]
    words = SLUG_BAD.sub("-", prefix.lower()).strip("-")
    words = "-".join([w for w in words.split("-") if w][:4])
    if len(words) < 3 and r["links"]:
        words = SLUG_BAD.sub("-", r["links"][0]["title"].lower()).strip("-")
        words = "-".join([w for w in words.split("-") if w][:4])
    if len(words) < 3 and r["links"]:
        words = os.path.splitext(r["links"][0]["target"])[0].lower()[:40]
    return words or ("row-%d" % r["row"])


def assign_keys(rows):
    """{row number: unique key}. Collisions get a numeric suffix, deterministically."""
    used, out = {}, {}
    for r in rows:
        k = row_key(r)
        if k in used:
            n = 2
            while "%s-%d" % (k, n) in used:
                n += 1
            k = "%s-%d" % (k, n)
        used[k] = True
        out[r["row"]] = k
    return out


def is_note(target):
    """True only when the target is a .md file sitting IN the memory dir - the collector's scope.

    ⛔ `os.path.exists(join(MEMDIR, "../../../../x.md"))` is TRUE and useless. `survey` reported
    all 177 links resolving on exactly that test, and the one link pointing outside only surfaced
    at 99% coverage, as a row that would not build. Existence is not membership.
    """
    p = os.path.normpath(os.path.join(MEMDIR, target))
    return (os.path.dirname(p).lower() == MEMDIR.lower()
            and p.lower().endswith(".md") and os.path.exists(p))


def existing_hooks():
    """{(target, rowkey_or_num, slot): hook} already on the notes.

    ⛔ `inject` REWRITES `memory_index` wholesale from MEMORY.md, so without this a re-run - which
    is how the key migration happens - silently discards the 44 authored hooks. The hooks are the
    only part of this system that is not derivable from the file.
    """
    out = {}
    for fn in sorted(os.listdir(MEMDIR)):
        if not fn.endswith(".md") or fn == "MEMORY.md":
            continue
        fm, _ = split_frontmatter(io.open(os.path.join(MEMDIR, fn), encoding="utf-8",
                                          errors="replace").read())
        occs = read_index(fm)[0]
        if not occs:
            continue
        try:
            for o in occs:
                if o.get("hook"):
                    slot = str(o["at"]).rsplit(".", 1)[-1]
                    out[(fn, slot)] = o["hook"]
        except Exception:
            continue
    return out


def plan_injection(rows):
    """{target: [occurrence, ...]} in row order. One entry per LINK that lives in a note.

    The row TEMPLATE goes to the first link of the row whose target is a real note - not blindly
    to position 1, because row 18's position 1 is the legal SoT in <your-workspace> and a file
    outside the scanned directory can never carry the field where the generator would read it.
    Out-of-dir links ride on that host as `ext`.
    """
    plan = collections.OrderedDict()
    keys = assign_keys(rows)
    keep = existing_hooks()
    for r in rows:
        hosts = [l for l in r["links"] if is_note(l["target"])]
        if not hosts:
            continue
        host = hosts[0]
        ext = [{"pos": l["pos"], "title": l["title"], "target": l["target"]}
               for l in r["links"] if not is_note(l["target"])]
        for l in r["links"]:
            if not is_note(l["target"]):
                continue
            occ = {"at": "%s.%d" % (keys[r["row"]], l["pos"]), "title": l["title"]}
            h = keep.get((l["target"], str(l["pos"])))
            if h:
                occ["hook"] = h
            if l is host:
                occ["row"] = r["template"]
                occ["order"] = r["row"]
                if ext:
                    occ["ext"] = ext
            plan.setdefault(l["target"], []).append(occ)
    return plan


def cmd_inject(a):
    """Write memory_index into the notes. Dry-run by default; --limit batches it."""
    orig = io.open(MEMFILE, encoding="utf-8", errors="replace").read()
    rows = parse_memory(orig)
    plan = plan_injection(rows)
    targets = list(plan)
    if a.limit:
        targets = targets[:a.limit]
    multi = [t for t in targets if len(plan[t]) > 1]
    print("%d note(s) planned, %d link(s)%s"
          % (len(targets), sum(len(plan[t]) for t in targets),
             "" if a.write else "   (DRY RUN - nothing is written)"))
    if multi:
        print("   %d appear in more than one row: %s" % (len(multi), ", ".join(multi)))
    for t in targets[:a.show]:
        print("   %-46s %s" % (t[:46],
                               " ".join("%s=%s" % (o["at"], o["title"][:26]) for o in plan[t])))
        for o in plan[t]:
            if "row" in o:
                print("        row %-5s %s" % (o["at"].split(".")[0], o["row"][:92]))
    if not a.write:
        print()
        print("Nothing written. Re-run with --write to apply.")
        return 0
    done = 0
    for t in targets:
        p = os.path.join(MEMDIR, t)
        txt = io.open(p, encoding="utf-8", errors="replace").read()
        fm, _ = split_frontmatter(txt)
        newfm = fm_set_index(fm, json.dumps(plan[t], ensure_ascii=False, separators=(",", ":")))
        write_frontmatter(p, newfm)
        done += 1
    print("wrote %d note(s)." % done)
    return 0


def collect_from_notes():
    """Rebuild the row records from note frontmatter. Returns (rows, problems)."""
    by_row = collections.defaultdict(dict)
    tpl = {}
    order = {}
    problems = []
    for fn in sorted(os.listdir(MEMDIR)):
        if not fn.endswith(".md") or fn == "MEMORY.md":
            continue
        txt = io.open(os.path.join(MEMDIR, fn), encoding="utf-8", errors="replace").read()
        fm, _ = split_frontmatter(txt)
        # Either place the field can sit, top level or under `metadata:` (see read_index).
        occs, _where, problem = read_index(fm)
        if problem:
            problems.append("%s: %s" % (fn, problem))
        if occs is None:
            continue
        for o in occs:
            # ⭐ `at` is "<rowkey>.<slot>". The key is IDENTITY and never changes; `order`, which
            # rides on the template occurrence, is the only thing that says where the row sits.
            try:
                rn, ps = str(o["at"]).rsplit(".", 1)
                pn = int(ps)
            except Exception:
                problems.append("%s: unparseable 'at' %r" % (fn, o.get("at")))
                continue
            if "order" in o:
                order[rn] = int(o["order"])
            if pn in by_row[rn]:
                problems.append("row %r position %d claimed by both %s and %s"
                                % (rn, pn, by_row[rn][pn]["target"], fn))
                continue
            by_row[rn][pn] = {"pos": pn, "title": o.get("title", ""), "target": fn,
                              "hook": o.get("hook")}
            if "row" in o:
                if rn in tpl and tpl[rn] != o["row"]:
                    problems.append("row %r has two different templates" % rn)
                tpl[rn] = o["row"]
            for e in o.get("ext", []):        # links to files outside the memory dir
                ep = int(e["pos"])
                if ep in by_row[rn]:
                    problems.append("row %r position %d is claimed twice" % (rn, ep))
                    continue
                by_row[rn][ep] = {"pos": ep, "title": e.get("title", ""),
                                  "target": e["target"]}
    rows = []
    unordered = [rn for rn in by_row if rn not in order]
    if unordered:
        problems.append("row(s) with no `order`: %s" % ", ".join(sorted(unordered)[:5]))
    for rn in sorted(by_row, key=lambda k: (order.get(k, 10 ** 6), str(k))):
        if rn not in tpl:
            problems.append("row %r has no template (it belongs on the row's first note)" % rn)
            continue
        # ⛔ THE SLOT COUNT COMES FROM THE TEMPLATE, NEVER FROM THE POSITIONS FOUND.
        # Measured 2026-09-18 on the first real batch: row 2 has six links, five notes had been
        # injected, and deriving the expected count from max(found) produced a five-link row that
        # rendered as a COMPLETE row with one link silently gone. A partial batch must look
        # partial; it must never look finished and be short.
        slots = sorted(int(x) for x in set(re.findall(r"\{(\d+)\}", tpl[rn])))
        missing = [p for p in slots if p not in by_row[rn]]
        if missing:
            problems.append("row %r is missing position(s) %s of %d - not built"
                            % (rn, ",".join(str(x) for x in missing), len(slots)))
            continue
        extra = [p for p in by_row[rn] if p not in slots]
        if extra:
            problems.append("row %r has position(s) %s the template has no slot for"
                            % (rn, ",".join(str(x) for x in extra)))
            continue
        rows.append({"row": rn, "template": tpl[rn],
                     "links": [by_row[rn][p] for p in sorted(by_row[rn])]})
    return rows, problems


def hand_rows(rows, disk_text, table):
    """Rows on disk that no note's `memory_index` builds, whose linked note file exists.

    ⛔ A BUILD MUST NEVER DROP A ROW WHOSE NOTE STILL EXISTS. Rows are built only from note
    frontmatter, so a row someone added to MEMORY.md by hand (a link to a named note file, no
    `memory_index` field on the note) was invisible to `build`: the rebuild overwrote the file
    and the row was gone, and `check --live` then said OK because the file matched its own
    output (o5, 2026-10-21, 8 rows lost). Such a row is CARRIED: it keeps its text, joins the
    build, and is coded and styled like any other row. A new note gets the next code from
    codes.json the same way, so a carried note is treated exactly as a newly indexed one.

    Returns [(row, anchor_target)]: row records shaped like collect_from_notes rows, with
    m/<n>.md links resolved back to filenames through the code table; anchor_target is a target
    of the nearest preceding disk row that notes build (None = the top), so the row keeps its
    place. A row is carried when at least one link resolves to an existing note and NONE
    resolves to a note a generated row already links. Rows with no existing note are stale and
    are not carried.
    """
    try:
        disk = parse_memory(disk_text.replace("\r\n", NL).rstrip(NL))
    except ValueError:
        return []
    known = set(l["target"] for r in rows for l in r["links"])
    back = dict(table.get("assigned", {}))
    back.update(table.get("retired", {}))
    pat = re.compile(r"^" + re.escape(CODE_DIR) + r"/(\d+)\.md$")
    out, anchor = [], None
    for dr in disk:
        links = []
        for l in dr["links"]:
            m = pat.match(l["target"])
            t = back.get(m.group(1), l["target"]) if m else l["target"]
            links.append({"pos": l["pos"], "title": l["title"], "target": t, "hook": None})
        targets = [l["target"] for l in links]
        if any(t in known for t in targets):
            anchor = next(t for t in targets if t in known)
            continue
        if any(t.endswith(".md") and "/" not in t and os.path.isfile(os.path.join(MEMDIR, t))
               for t in targets):
            out.append(({"row": "hand", "template": dr["template"], "links": links}, anchor))
    return out


COLD_FILE = "Memory2.md"     # the cold-storage overflow index; a note listed there is parked on purpose
_NOT_NOTES = ("MEMORY.md", "MEMORY-CODES.md", COLD_FILE)


def unlisted_notes(rows):
    """Note files that nothing lists: no built or carried row links them, and Memory2.md does not
    name them.

    ⛔ WHY (o9L1516, 2026-10-30): `build` makes a row only from a note's `memory_index` field, or
    carries a row somebody already typed into MEMORY.md. A new note with neither (the two notes
    feedback_tested_copy_always_kept.md and feedback_claim_bank_boundary_is_not_a_ban.md) produced
    NO row, NO warning and NO drift, so `check --live` said what it always said and the note was
    never loaded. It must be listed, so a build and `check --live` name it. `rows` is the list a
    build writes (carried hand rows already merged in). A note named in Memory2.md is parked on
    purpose and is not reported.
    """
    linked = set(l["target"] for r in rows for l in r["links"])
    try:
        cold = io.open(os.path.join(MEMDIR, COLD_FILE), encoding="utf-8", errors="replace").read()
    except Exception:
        cold = ""
    out = []
    for fn in sorted(os.listdir(MEMDIR)):
        if not fn.endswith(".md") or fn in _NOT_NOTES or fn in linked or fn in cold:
            continue
        out.append(fn)
    return out


def report_unlisted(rows, write):
    for fn in unlisted_notes(rows):
        write("[UNLISTED] %s - no memory_index field, no row in MEMORY.md, not in %s: "
              "build adds no row for it. Add a memory_index field (inject) or park it in %s."
              % (fn, COLD_FILE, COLD_FILE))


def merge_hand_rows(rows, extras):
    """rows with each carried row placed after its anchor row (or first), in disk order."""
    merged = list(rows)
    for r, anchor in extras:
        at = 0
        if anchor is not None:
            for i, g in enumerate(merged):
                if any(l["target"] == anchor for l in g["links"]):
                    at = i + 1
                    break
        # after the hand rows already placed at this spot, so two carried rows keep disk order
        # (o9L1447: two rows at the top swapped on every build, and check --live never settled)
        while at < len(merged) and merged[at].get("row") == "hand":
            at += 1
        merged.insert(at, r)
    return merged


def file_newline(path, default="\r\n"):
    """The line terminator the file already uses.

    ⛔ MEMORY.md is CRLF. Generating it with LF produced a file whose CONTENT was identical and
    whose every line showed as changed in git - 105 rows, 105 bytes, and a real edit would have
    been invisible inside that. A generator that reformats what it regenerates is not a view of
    the file; it is a second opinion about it.
    """
    try:
        with io.open(path, "rb") as f:
            b = f.read(65536)
        return "\r\n" if b"\r\n" in b else "\n"
    except Exception:
        return default


def cmd_build(a):
    # ⛔ A build that writes with --codes runs under the code-table lock, start to finish
    if a.codes and (a.out or a.pointers or a.supplement):
        with code_table_lock():
            return _cmd_build(a)
    return _cmd_build(a)


def _cmd_build(a):
    rows, problems = collect_from_notes()
    src = a.out if (a.out and os.path.isfile(a.out)) else MEMFILE
    try:
        disk_text = io.open(src, encoding="utf-8", errors="replace").read()
    except Exception:
        disk_text = ""
    extras = hand_rows(rows, disk_text, load_code_table())
    if extras:
        rows = merge_hand_rows(rows, extras)
        for r, _a in extras:
            print("[carry] hand row kept, no note index builds it: %s"
                  % ", ".join(l["target"] for l in r["links"]), file=sys.stderr)
    report_unlisted(rows, lambda s: print(s, file=sys.stderr))
    codes = None
    table = None
    if a.codes:
        # ⛔ ONE CODE PER UNIQUE TARGET, not per link, and FROM THE TABLE, not from position:
        # see `assign_codes`. The table is saved only by a build that writes something, so a
        # plain `build --codes` to stdout changes nothing on disk.
        codes, table = assign_codes(rows, load_code_table())
        if a.out or a.pointers or a.supplement:
            save_code_table(table)
    out = render_rows(rows, codes, style=a.style)
    if a.codes and a.pointers:
        n, gone = write_pointers(codes, a.pointers, table["retired"])
        print("wrote %d pointer file(s) to %s%s"
              % (n, a.pointers, ", removed %d stale" % gone if gone else ""),
              file=sys.stderr)
    for p in problems:
        print("[warn] %s" % p, file=sys.stderr)
    if a.supplement:
        if not a.codes:
            print("[REFUSE] --supplement only means something with --codes.", file=sys.stderr)
            return 1
        eol2 = file_newline(MEMFILE)
        body = [
            "<!-- GENERATED by .shared/scripts/memory_index.py build --codes --supplement"
            " - do not hand-edit -->",
            "",
            "# MEMORY code table",
            "",
            "Every `[N]` in `MEMORY.md` resolves here. This file is NOT loaded into a session;"
            " it is read on demand.",
            "",
            "| code | note |",
            "|---|---|",
        ]
        seen = set()
        for r in rows:
            for l in r["links"]:
                c = codes.get(l["target"])
                if c in seen:
                    continue
                seen.add(c)
                body.append("| %s | `%s` |" % (c, l["target"]))
        # ⭐ retired codes are listed too, so a gap in the numbering is explained, not a defect
        for c in sorted(table["retired"], key=int):
            body.append("| %s | retired, was `%s` |" % (c, table["retired"][c]))
        blob = (NL.join(body) + NL).replace(NL, eol2)
        io.open(a.supplement, "w", encoding="utf-8", newline="").write(blob)
        print("wrote %s  (%d codes, %d bytes)"
              % (a.supplement, len(seen), len(blob.encode("utf-8"))), file=sys.stderr)

    eol = file_newline(MEMFILE)
    if a.out:
        blob = (out + NL).replace(NL, eol)
        io.open(a.out, "w", encoding="utf-8", newline="").write(blob)
        print("wrote %s  (%d rows, %d bytes, %s line endings)"
              % (a.out, len(rows), len(blob.encode("utf-8")),
                 "CRLF" if eol == "\r\n" else "LF"), file=sys.stderr)
    else:
        sys.stdout.write(out + NL)
    return 0


def cmd_check(a):
    """Does the generated view match the file on disk?

    ⭐ PER ROW, NOT WHOLE-FILE - because the conversion runs in batches and a whole-file compare
    reports DRIFT for every row not yet injected, which is indistinguishable from a row injected
    WRONG. During a partial run the only meaningful question is: of the rows the notes can build,
    does each one match its original byte for byte?
    """
    rows, problems = collect_from_notes()
    for p in problems:
        print("[warn] %s" % p)
    if not rows:
        print("[skip] no note carries memory_index yet - nothing to check.")
        return 0

    # ⭐ THE POST-SWITCH DRIFT QUESTION, and it is a different one. Before the switch: "do the
    # notes reproduce the hand-kept file?" After it: "is the file on disk still what the notes
    # generate, or has it gone stale / been hand-edited?" That is a byte comparison against a
    # fresh build in the file's OWN style, and it is the only drift question left that matters
    # day to day.
    if getattr(a, "live", False):
        disk = io.open(MEMFILE, "rb").read()
        styled = bool(re.search(r"\]\(" + re.escape(CODE_DIR) + r"/\d+\.md\)",
                                disk.decode("utf-8", "replace")))
        extras = hand_rows(rows, disk.decode("utf-8", "replace"), load_code_table())
        rows = merge_hand_rows(rows, extras)
        report_unlisted(rows, print)
        codes = None
        if styled:
            # read-only: the codes a build WOULD give, from the saved table
            codes, _ = assign_codes(rows, load_code_table())
        eol = file_newline(MEMFILE)
        fresh = (render_rows(rows, codes) + NL).replace(NL, eol).encode("utf-8")
        print("style on disk: %s" % ("code paths" if styled else "filenames"))
        print("on disk  %6d bytes" % len(disk))
        print("fresh    %6d bytes" % len(fresh))
        if disk == fresh:
            print()
            print("[OK] MEMORY.md is exactly what the notes generate right now.")
            return 0
        print()
        for r, _a in extras:
            print("[HAND ROW] %s - no note index builds this row; a build carries it and keeps"
                  % ", ".join("%s (%s)" % (l["title"], l["target"]) for l in r["links"]))
            print("           its text. A row like this missing from the file was lost.")
        print("[DRIFT] MEMORY.md is NOT what the notes generate. Either a note changed and the")
        print("        file was not rebuilt, or the file was hand-edited. Rebuild with:")
        print("          memory_index.py build --codes --style path --out <MEMORY.md> \\")
        print("              --supplement <MEMORY-CODES.md> --pointers <m/>")
        return 1
    # ⛔ THE BASELINE HAS TO SURVIVE THE SWITCH. While MEMORY.md is hand-kept it IS the control.
    # The moment the generated view replaces it, comparing against it becomes the generator
    # checking its own output - it passes by construction and reports clean forever.
    # `--against <ref>` moves the control into git, where the thing being checked cannot edit it.
    # o9's framing: the switch is the moment every existing control stops applying at once.
    #
    # ⭐ ALIGN BY ORDER, NOT BY ID. A row's identity is a stable KEY and the file has no keys in
    # it, so the only shared coordinate between the notes and the file is position.
    if getattr(a, "against", None):
        try:
            raw = subprocess.check_output(
                ["git", "-C", MEMDIR, "show", "%s:MEMORY.md" % a.against],
                stderr=subprocess.STDOUT, creationflags=flags())
        except Exception as e:
            print("[FAIL] could not read MEMORY.md at %r: %s" % (a.against, e))
            return 1
        orig = parse_memory(raw.decode("utf-8", "replace").replace("\r\n", "\n").rstrip("\n"))
        # ⭐ Same trap as verify-titles: a post-switch baseline carries code paths, and comparing
        # them against a filename render makes every row read as changed.
        cache = {}
        for r in orig:
            for l in r["links"]:
                l["target"] = resolve_at_ref(l["target"], a.against, cache)
            r["template"] = re.sub(r"\]\(" + re.escape(CODE_DIR) + r"/\d+\.md\)", "]()",
                                   r["template"])
        print("baseline: %s (git - survives the switch)" % a.against)
    else:
        disk = io.open(MEMFILE, encoding="utf-8", errors="replace").read()
        # ⛔ AFTER THE SWITCH THIS COMPARISON IS MEANINGLESS AND MUST REFUSE.
        # `cutover` predicted it would "pass forever" - wrong, and the correction matters.
        # `check` renders real filenames while the switched file carries code paths, so it
        # FAILS forever instead. Failing loudly is the safe direction, but a check that can
        # only ever report drift is as useless as one that can only report clean, and a reader
        # who sees it fail every day stops reading it. So it refuses and says what to pass.
        if re.search(r"\]\(" + re.escape(CODE_DIR) + r"/\d+\.md\)", disk):
            print("[REFUSE] MEMORY.md carries code paths, so comparing it against a render with")
            print("         real filenames can only ever report drift. The file is now the")
            print("         generator's own output - it cannot be its own baseline.")
            print()
            print("   Use a git ref instead:")
            print("     memory_index.py check --against %s" % BASELINE_REF)
            return 1
        orig = parse_memory(disk)
    good, bad = 0, []
    for i, r in enumerate(rows):
        if i >= len(orig):
            bad.append((r["row"], "<no such row in MEMORY.md>", render_rows([r])))
            continue
        a_, b_ = render_rows([orig[i]]), render_rows([r], hooks=not a.no_hooks)
        if a_ == b_:
            good += 1
        else:
            bad.append((r["row"], a_, b_))
    total = len(orig)
    print("rows built from the notes   %4d of %d  (%.0f%%)"
          % (len(rows), total, 100.0 * len(rows) / total))
    print("  match the original        %4d" % good)
    print("  DIFFER                    %4d" % len(bad))
    for n, x, y in bad[:5]:
        print("   row %s" % n)
        print("     disk: %s" % x[:130])
        print("     gen : %s" % y[:130])
    if bad:
        return 1
    if len(rows) == total and not problems:
        print()
        print("[OK] COMPLETE - every row in MEMORY.md is reproduced from the notes.")
    else:
        print()
        print("[OK] every row the notes can build matches. %d row(s) not yet injected."
              % (total - len(rows)))
    return 0


def hook_count():
    """How many authored hooks the notes carry. Not derivable from MEMORY.md - so if it drops,
    nothing else in this system can tell you what was lost."""
    n = 0
    for fn in sorted(os.listdir(MEMDIR)):
        if not fn.endswith(".md") or fn == "MEMORY.md":
            continue
        fm, _ = split_frontmatter(io.open(os.path.join(MEMDIR, fn), encoding="utf-8",
                                          errors="replace").read())
        occs = read_index(fm)[0]
        if not occs:
            continue
        try:
            n += sum(1 for o in occs if o.get("hook"))
        except Exception:
            pass
    return n


def cmd_cutover(a):
    """What loses its baseline the moment the generated view replaces MEMORY.md.

    ⭐ o9's question, and it is the right one to ask BEFORE rather than after: the switch is the
    moment every existing control stops applying at once. This enumerates them and says which
    survive, so the list is a fact to check rather than a memory to trust.
    """
    rows, problems = collect_from_notes()
    n_hooks = hook_count()
    controls = [
        ("roundtrip", "parse MEMORY.md, re-render, byte-compare", "MEMORY.md itself",
         False, "the file becomes the generator's own output, so it passes by construction"),
        ("check (no --against)", "generated rows vs the file on disk", "MEMORY.md itself",
         False, "same - the generator would be grading its own output"),
        ("check --against <ref>", "generated rows vs a pinned git version", "git %s" % BASELINE_REF,
         True, "the control is in history, where the thing checked cannot edit it"),
        ("verify-titles", "every title and target vs the baseline", "git %s" % BASELINE_REF,
         True, "catches a wrong WORD, which every count-based check is blind to"),
        ("verify-codes", "variant-B references vs the supplement", "the notes + supplement",
         True, "never depended on MEMORY.md"),
        ("selftest", "parser, slots, keys, insert, duplicate titles", "fixtures",
         True, "self-contained"),
        ("hook count", "the 44 authored hooks are not derivable from anything",
         "NOTHING - see below", False,
         "no baseline exists; a lost hook is silently gone"),
    ]
    print("=== CONTROLS ON MEMORY.md, AND WHAT SURVIVES THE SWITCH ===")
    print()
    print("   %-24s %-34s %s" % ("control", "baseline", "survives?"))
    for name, _what, base, ok, _why in controls:
        print("   %-24s %-34s %s" % (name, base, "yes" if ok else "NO"))
    print()
    for name, what, base, ok, why in controls:
        if not ok:
            print("   [dies] %-22s %s" % (name, why))
    print()
    print("=== WHAT THAT LEAVES UNGUARDED AFTER THE SWITCH ===")
    print("   rows the notes currently build      %4d" % len(rows))
    print("   authored hooks                      %4d   <- no baseline of any kind" % n_hooks)
    print("   problems reported right now         %4d" % len(problems))
    print()
    print("   A hook is the only thing here that is AUTHORED and not present in the baseline")
    print("   file, so nothing can reconstruct one that goes missing. The count is the whole")
    print("   defence: record it, and notice when it falls.")
    print()
    if n_hooks < EXPECTED_HOOKS:
        print("   [FAIL] %d hook(s) expected, %d found - %d HAVE BEEN LOST and nothing else in"
              % (EXPECTED_HOOKS, n_hooks, EXPECTED_HOOKS - n_hooks))
        print("          this system can tell you which. Recover from git, do not rewrite them.")
        return 1
    if n_hooks > EXPECTED_HOOKS:
        print("   [note] %d hooks against an expected %d - hooks were added. Raise EXPECTED_HOOKS"
              % (n_hooks, EXPECTED_HOOKS))
        print("          in this script deliberately, so the next loss is still visible.")
        return 0
    print("   [OK] %d hooks, matching the pinned expectation." % n_hooks)
    return 0


CODE_PATH_RE = re.compile(r"^" + re.escape(CODE_DIR) + r"/(\d+)\.md$")


def resolve_at_ref(target, ref, cache):
    """A baseline target may be a CODE PATH. Resolve it back to the note it points at.

    ⛔ THIS IS THE SWITCH BREAKING A CONTROL A SECOND TIME, and I missed it. While the baseline
    was a pre-switch commit its targets were filenames and comparison worked. The moment the
    baseline moved PAST the switch its targets became `m/NN.md`, so every link read as changed -
    a check that can only report drift, which is the exact failure `cutover` was written about.

    ⭐ The pointer files are versioned too, so the resolution is read from the SAME ref. A
    baseline that resolves itself stays a baseline.
    """
    m = CODE_PATH_RE.match(target)
    if not m:
        return target
    key = (ref, target)
    if key in cache:
        return cache[key]
    try:
        blob = subprocess.check_output(
            ["git", "-C", MEMDIR, "show", "%s:%s" % (ref, target)],
            stderr=subprocess.STDOUT, creationflags=flags()).decode("utf-8", "replace")
        hit = re.search(r"\]\(\.\./([^)]+)\)", blob)
        out = hit.group(1) if hit else target
    except Exception:
        out = target
    cache[key] = out
    return out


def cmd_verify_titles(a):
    """Compare the generated titles/targets against a known-good MEMORY.md from git history.

    ⛔ THE GAP THIS CLOSES, AND IT ONLY OPENS AFTER THE SWITCH. Every other check here verifies a
    COUNT - slots declared against slots filled - and a count cannot see a wrong word. If a note's
    `title` is edited to something incorrect, the row still has the right number of links, the
    round-trip still passes, and the index quietly says the wrong thing.

    Today the original file is the control. **The moment the generated view REPLACES it, that
    control is gone** - o9 named this as the limit I would otherwise have only written down:
    *"after it there is no instrument that can find it."*

    ⭐ So the control moves into git, where it cannot be edited by the thing it checks. The last
    hand-kept `MEMORY.md` stays at a fixed commit forever, and this compares against that.
    """
    ref = a.against
    try:
        raw = subprocess.check_output(["git", "-C", MEMDIR, "show", "%s:MEMORY.md" % ref],
                                      stderr=subprocess.STDOUT, creationflags=flags())
    except Exception as e:
        print("[FAIL] could not read MEMORY.md at %r: %s" % (ref, e))
        return 1
    base = parse_memory(raw.decode("utf-8", "replace").replace("\r\n", "\n").rstrip("\n"))
    cache = {}
    want = [(l["title"], resolve_at_ref(l["target"], ref, cache))
            for r in base for l in r["links"]]
    rows, problems = collect_from_notes()
    # ⛔ COMPARE WHAT A BUILD WRITES, carried hand rows included (see `hand_rows`). Without this
    # step every hand row in a post-4320fe9 baseline read as REMOVED although it was still in the
    # file, so no baseline after 00e133e could pass (o1:W42). The carried rows come from the live
    # MEMORY.md, as `build` and `check --live` take them.
    try:
        disk_text = io.open(MEMFILE, encoding="utf-8", errors="replace").read()
    except Exception:
        disk_text = ""
    rows = merge_hand_rows(rows, hand_rows(rows, disk_text, load_code_table()))
    got = [(l["title"], l["target"]) for r in rows for l in r["links"]]
    for p in problems:
        print("[warn] %s" % p)
    print("baseline  %-28s %4d link(s)" % (ref, len(want)))
    print("generated (notes + hand rows)        %4d link(s)" % len(got))
    if want == got:
        print()
        print("[OK] every title and target matches the baseline. No word has drifted.")
        return 0
    # ⛔ REPORT INSERTIONS AS INSERTIONS. A positional compare turns one added row into "twelve
    # rows changed", because everything after it shifts - which buries the single fact a human
    # needs. o9's new row on 2026-09-18 produced exactly that. A sequence diff separates an
    # ADDED link from a CHANGED one, and only the second is a reason to worry.
    import difflib
    sm = difflib.SequenceMatcher(a=want, b=got, autojunk=False)
    added, removed, changed = [], [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "insert":
            added += got[j1:j2]
        elif tag == "delete":
            removed += want[i1:i2]
        elif tag == "replace":
            changed += list(zip(want[i1:i2], got[j1:j2]))
    print()
    print("[DRIFT] baseline %d link(s), generated %d" % (len(want), len(got)))
    print("   ADDED   %3d" % len(added))
    print("   REMOVED %3d" % len(removed))
    print("   CHANGED %3d   <- the only kind that is a word drifting" % len(changed))
    for t, n in added[:6]:
        print("     + %-46s %s" % (t[:46], n))
    for t, n in removed[:6]:
        print("     - %-46s %s" % (t[:46], n))
    for w, g in changed[:6]:
        print("     ~ %r" % (w,))
        print("       %r" % (g,))
    print()
    if changed or removed:
        print("A CHANGED or REMOVED link is the case worth looking at - a title was edited or a")
        print("note dropped out of the index.")
    else:
        print("Only ADDITIONS. Nothing that existed has changed, so this is new rows being")
        print("indexed, not a word drifting.")
    print("A difference is NOT automatically wrong - an intended edit looks the same as a slip.")
    print("A human decides, then moves the baseline forward with --against.")
    return 1


def cmd_verify_codes(a):
    """Resolve every code in a variant-B render through its supplement and prove nothing moved.

    ⛔ A size win that drops a link is not a win. The whole risk of variant B is that the address
    moves somewhere nobody checks, so the check is that every reference still resolves, IN ORDER,
    to the same (title, note) pair the current index carries.
    """
    sup = io.open(a.supplement, encoding="utf-8", errors="replace").read()
    table = dict(re.findall(r"^\|\s*(\d+)\s*\|\s*`([^`]+)`\s*\|", sup, re.M))
    retired = dict(re.findall(r"^\|\s*(\d+)\s*\|\s*retired, was `([^`]+)`\s*\|", sup, re.M))
    b = io.open(a.index, encoding="utf-8", errors="replace").read()
    # ⛔ BOTH STYLES. This read only the bare `[Title][24]` form, so on the live path-style
    # file it found 0 references and FAILED every run since the switch - a check that can only
    # fail is read by nobody.
    refs = re.findall(r"\[([^\]]*)\](?:\[(\d+)\]|\(" + re.escape(CODE_DIR) + r"/(\d+)\.md\))", b)
    refs = [(t, c1 or c2) for t, c1, c2 in refs]
    rows, problems = collect_from_notes()
    # a carried hand row is part of the index a build writes (see `hand_rows`)
    rows = merge_hand_rows(rows, hand_rows(rows, b, {"assigned": table, "retired": retired}))
    want = [(l["title"], l["target"]) for r in rows for l in r["links"]]
    got = [(t, table.get(c)) for t, c in refs]
    unresolved = [(t, c) for t, c in refs if c not in table]
    nums = sorted(int(c) for c in list(table) + list(retired))
    gaps = [n for n in range(1, (nums[-1] if nums else 0) + 1) if n not in set(nums)]
    # ⭐ THE SAVED TABLE AND THE POINTER FILES MUST AGREE WITH THE SUPPLEMENT, code for code.
    saved = load_code_table()
    mismatch = []
    if saved["assigned"] != table:
        mismatch.append("codes.json assigned != supplement (%d vs %d codes)"
                        % (len(saved["assigned"]), len(table)))
    if saved["retired"] != retired:
        mismatch.append("codes.json retired != supplement (%d vs %d)"
                        % (len(saved["retired"]), len(retired)))
    pdir = os.path.join(MEMDIR, CODE_DIR)
    for c, t in table.items():
        p = os.path.join(pdir, c + ".md")
        txt = io.open(p, encoding="utf-8").read() if os.path.exists(p) else ""
        if "(../%s)" % t not in txt:
            mismatch.append("m/%s.md does not point at %s" % (c, t))
    for c, t in retired.items():
        p = os.path.join(pdir, c + ".md")
        txt = io.open(p, encoding="utf-8").read() if os.path.exists(p) else ""
        if "is retired" not in txt:
            mismatch.append("m/%s.md is not a retired pointer for %s" % (c, t))
    print("supplement codes        %4d   retired %d" % (len(table), len(retired)))
    print("references in the index %4d   unresolvable %d" % (len(refs), len(unresolved)))
    print("links, current form     %4d" % len(want))
    print("links, resolved from B  %4d" % len(got))
    print("code numbering 1..%d, gaps: %s"
          % (nums[-1] if nums else 0, gaps[:10] if gaps else "none"))
    print("codes.json + pointers   %s" % ("agree" if not mismatch else "DISAGREE"))
    for m_ in mismatch[:10]:
        print("   %s" % m_)
    ok = (want == got) and not unresolved and not gaps and not mismatch
    print()
    if ok:
        print("[OK] every reference resolves, IN ORDER, to the same (title, note) pair.")
        return 0
    print("[FAIL] the resolved set differs from the current index.")
    for i, (w, g) in enumerate(zip(want, got)):
        if w != g:
            print("   #%d want %r" % (i + 1, w))
            print("       got %r" % (g,))
            break
    return 1


def first_clause(desc, budget):
    """A hook DRAFT from an existing description. Returns None when it cannot make a clean one.

    ⛔ A `description:` is an ABSTRACT - what the note IS. A hook is a TRIGGER - when to open it.
    They are different jobs, so this produces a DRAFT for a human to accept or replace, never a
    finished hook. It refuses rather than mangling: a hook cut mid-clause is worse than no hook,
    because it reads as authored.
    """
    if not desc:
        return None
    d = desc.strip().strip('"').strip()
    parts = re.split(r"(?<=[.;])\s+|\s+[—–-]\s+|\s+\(", d)
    cand = (parts[0] if parts else d).strip().rstrip(".,;:").strip()
    if not cand or len(cand) < 12:
        return None
    if len(cand) > budget:
        cut = cand[:budget + 1]
        sp = cut.rfind(" ")
        if sp < budget * 0.6:
            return None              # no clean word break inside the budget - refuse
        cand = cut[:sp].rstrip(".,;:").strip()
    return cand or None


def _row_filter(spec):
    if not spec:
        return None
    want = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a_, b_ = part.split("-")
            want.update(range(int(a_), int(b_) + 1))
        elif part:
            want.add(int(part))
    return want


def cmd_citations(a):
    """Find `m/NN.md` cited from OUTSIDE the generated index, and say what each points at NOW.

    ⭐ FIXED 2026-10-08 (o1:W27): codes now come from `m/codes.json` and never move or get
    reused (`assign_codes`), so a citation made after that date keeps its meaning; a removed
    note's code reads RETIRED. The history below is why, and still explains citations made
    BEFORE the switch, which may already point somewhere their author did not mean.

    ⛔ THE CODES WERE POSITIONAL. `codes[target] = str(len(codes) + 1)` assigned them by
    first-encounter order, so inserting a note anywhere but the end renumbers every code after
    it. Inside MEMORY.md that costs nothing - the file is regenerated whole, every run. Outside
    it, a citation is a NUMBER THAT QUIETLY COMES TO MEAN A DIFFERENT NOTE, and nothing breaks:
    the link still resolves, to the wrong thing.

    ⭐ IT HAD ALREADY FIRED WHEN THIS WAS WRITTEN. o1's OrchDoc recorded a sealed test as
    following `m/138.md` -> `reoon_email_verifier.md` and `m/175.md` ->
    `feedback_pushover_high_priority_only.md`. One note was later indexed ahead of them, and
    those two are now 137 and 174. The entry reads as a plain record of what an agent did, and
    a reader checking it lands on two unrelated notes and concludes the test was misreported.

    ⚠️ AND THE OBVIOUS CHECK MISSES IT. Asking "do these codes still resolve?" answers
    YES for every one of them, forever - that is what made it invisible. The question is whether
    a code resolves to what the citing text SAYS it resolves to, which only the citing text can
    answer. So this reports, and refuses to guess.
    """
    import glob as _glob
    roots = [os.path.join("C:\\", "Users", "<your-user>", "<your-workspace>")]
    hits = []
    for root in roots:
        for path in _glob.glob(os.path.join(root, "*.md")):
            try:
                txt = io.open(path, encoding="utf-8", errors="replace").read()
            except Exception:
                continue
            for m in re.finditer(r"m/(\d+)\.md", txt):
                line = txt[:m.start()].count(NL) + 1
                hits.append((os.path.basename(path), line, m.group(1)))
    if not hits:
        print("no m/NN.md citations outside the index.")
        return 0
    print("%d citation(s) of a code from outside the generated index:" % len(hits))
    print()
    cache = {}
    for fname, line, code in hits:
        if code not in cache:
            ptr = os.path.join(MEMDIR, CODE_DIR, code + ".md")
            target = "<no such pointer>"
            if os.path.exists(ptr):
                ptxt = io.open(ptr, encoding="utf-8").read()
                mm = LINK.search(ptxt)
                rr = re.search(r"is retired\.\*\* It pointed at `([^`]+)`", ptxt)
                if rr:
                    target = "<RETIRED, was %s>" % rr.group(1)
                elif mm:
                    target = os.path.basename(mm.group(2))
            cache[code] = target
        print("  %-34s line %-6s m/%s.md -> %s" % (fname, line, code, cache[code]))
    print()
    print("Codes are stable since 2026-10-08 (m/codes.json). A citation made before then may")
    print("already point at another note than its author meant - check it against its text.")
    print("Cite the note FILENAME instead; only the index itself should use codes.")
    return 0


def cmd_hooks(a):
    """Draft a per-note hook for multi-link rows from each note's own description."""
    want = _row_filter(a.rows)
    rows, problems = collect_from_notes()
    for p in problems:
        print("[warn] %s" % p)
    todo = []
    for r in rows:
        if len(r["links"]) < 2:
            continue
        if want and r["row"] not in want:
            continue
        # ⛔ SKIP A SLOT THE TEMPLATE ALREADY ANNOTATES. The text BETWEEN slots is per-note
        # signal - "(NEVER force-push)", "(SINGLE ledger)" - and 35 of the 100 multi-link
        # links already carry one. A hook there duplicates it. Measured 2026-09-18 after a dry
        # run rendered "never force-push (NEVER force-push)".
        parts = re.split(r"\{(\d+)\}", r["template"])
        annotated = set()
        for i in range(1, len(parts) - 1, 2):
            t = re.sub(r"\s+", " ", parts[i + 1]).strip(" +·—–-.,;:")
            if len(t) >= 8:
                annotated.add(int(parts[i]))
        for l in r["links"]:
            if l.get("hook") or not is_note(l["target"]) or l["pos"] in annotated:
                continue
            p = os.path.join(MEMDIR, l["target"])
            fm, _ = split_frontmatter(io.open(p, encoding="utf-8",
                                              errors="replace").read())
            draft = first_clause(fm_get_block(fm, "description"), a.budget)
            todo.append({"row": r["row"], "pos": l["pos"], "target": l["target"],
                         "title": l["title"], "draft": draft})
    got = [t for t in todo if t["draft"]]
    print("%d note(s) in scope, %d with a usable draft, %d needing one written"
          % (len(todo), len(got), len(todo) - len(got)))
    for t in todo:
        print("   %d.%-2d %-34s %s" % (t["row"], t["pos"], t["title"][:34],
                                       t["draft"] or "<< NEEDS A HAND-WRITTEN HOOK >>"))
    if not a.write:
        print()
        print("Nothing written. Re-run with --write to apply the drafts.")
        return 0
    byfile = collections.defaultdict(list)
    for t in got:
        byfile[t["target"]].append(t)
    for target, ts in byfile.items():
        p = os.path.join(MEMDIR, target)
        txt = io.open(p, encoding="utf-8", errors="replace").read()
        fm, _ = split_frontmatter(txt)
        occs = read_index(fm)[0] or []
        for t in ts:
            at = "%d.%d" % (t["row"], t["pos"])
            for o in occs:
                if o.get("at") == at:
                    o["hook"] = t["draft"]
        write_frontmatter(p, fm_set_index(fm, json.dumps(occs, ensure_ascii=False,
                                                         separators=(",", ":"))))
    print("wrote hooks onto %d note(s)." % len(byfile))
    return 0


def fm_get_block(fm, key):
    """A frontmatter value that may wrap onto following indented lines."""
    if fm is None:
        return None
    lines = fm.split(NL)
    for i, l in enumerate(lines):
        if l.startswith(key + ":"):
            v = l.split(":", 1)[1].strip()
            for r in lines[i + 1:]:
                if re.match(r"^[A-Za-z_][A-Za-z0-9_-]*:", r):
                    break
                v += " " + r.strip()
            return v.strip()
    return None


def selftest():
    src = NL.join([
        "- \u26d4 **A** \u2014 [One](one.md) \u2014 hook text here.",
        "- \u2b50 Group: [Two](two.md) + [Three](three.md) \u00b7 tail",
        "- plain [Four](four.md)",
    ])
    rows = parse_memory(src)
    ok = True
    if len(rows) != 3:
        print("[FAIL] parsed %d rows, expected 3" % len(rows))
        ok = False
    if rows[1]["template"] != "\u2b50 Group: {1} + {2} \u00b7 tail":
        print("[FAIL] template: %r" % rows[1]["template"])
        ok = False
    if render_rows(rows) != src:
        print("[FAIL] round-trip differs")
        ok = False
    CODES = {"one.md": "1", "two.md": "2", "three.md": "3", "four.md": "4"}
    b = render_rows(rows, CODES, style="bare")
    if "[One][1]" not in b or "(one.md)" in b:
        print("[FAIL] bare variant-B render: %s" % b.split(NL)[0])
        ok = False
    # ⭐ THE DEFAULT IS THE PATH STYLE, because a code that IS a path needs no mechanism.
    p = render_rows(rows, CODES)
    if "(m/1.md)" not in p or "(one.md)" in p:
        print("[FAIL] path variant-B render: %s" % p.split(NL)[0])
        ok = False
    if len(p) <= len(b):
        print("[FAIL] the path style should cost MORE than bare: %d vs %d" % (len(p), len(b)))
        ok = False
    # a row whose link text contains brackets must not break the slot substitution
    tricky = "- x [a [b] c](t.md) y"
    try:
        rr = parse_memory(tricky)
        if render_rows(rr) != tricky:
            print("[note] nested-bracket title does not round-trip - %r" % rr[0]["template"])
    except Exception as e:
        print("[note] nested-bracket title raised: %s" % e)
    # frontmatter set/get
    fm = "name: x" + NL + "description: y"
    fm2 = fm_set(fm, "memory_index", "3.2")
    if fm_get(fm2, "memory_index") != "3.2" or fm_get(fm2, "name") != "x":
        print("[FAIL] fm_set/get: %r" % fm2)
        ok = False
    fm3 = fm_set(fm2, "memory_index", "4.1")
    if fm_get(fm3, "memory_index") != "4.1" or fm3.count("memory_index") != 1:
        print("[FAIL] fm_set did not replace in place: %r" % fm3)
        ok = False
    # W149 box 8: the field as the Write tool leaves it, nested under metadata as a YAML list.
    nested = NL.join(["metadata: ", "  node_type: memory", "  memory_index: ",
                      "    - at: k.1", "      title: A", '      row: "x: {1}"', "      order: 3",
                      "  modified: 2026-09-25T21:37:53.419Z"])
    want = [{"at": "k.1", "title": "A", "row": "x: {1}", "order": 3}]
    if read_index(nested) != (want, "metadata", None):
        print("[FAIL] read_index nested: %r" % (read_index(nested),))
        ok = False
    moved = fm_set_index(nested, json.dumps(want))
    if read_index(moved) != (want, "top", None) or moved.count("memory_index") != 1 \
            or "  modified: 2026-09-25T21:37:53.419Z" not in moved:
        print("[FAIL] fm_set_index did not move the field to the top level: %r" % moved)
        ok = False

    # ⛔ THE CASE THAT BROKE THE FIRST DESIGN: one note in two rows, different title each time.
    # A scalar field drops one of them silently, so the round-trip has to prove both survive.
    dup = NL.join([
        "- A [migrations](p.md) and [x](x.md)",
        "- B see [§2](p.md).",
    ])
    rr = parse_memory(dup)
    plan = {}
    for r in rr:
        for l in r["links"]:
            occ = {"at": "%d.%d" % (r["row"], l["pos"]), "title": l["title"]}
            if l["pos"] == 1:
                occ["row"] = r["template"]
            plan.setdefault(l["target"], []).append(occ)
    if len(plan.get("p.md", [])) != 2:
        print("[FAIL] the doubly-linked note produced %d occurrence(s), expected 2"
              % len(plan.get("p.md", [])))
        ok = False
    elif [o["title"] for o in plan["p.md"]] != ["migrations", "§2"]:
        print("[FAIL] the two titles did not both survive: %r"
              % [o["title"] for o in plan["p.md"]])
        ok = False
    # ⛔ A PARTIALLY-INJECTED ROW MUST NOT BUILD. Measured on the first real batch: five of row
    # 2's six notes were written and the row rendered as a complete row with one link gone.
    import shutil
    import tempfile
    d = tempfile.mkdtemp()
    save_dir = globals()["MEMDIR"]
    try:
        globals()["MEMDIR"] = d
        io.open(os.path.join(d, "a.md"), "w", encoding="utf-8", newline="").write(
            "---" + NL + 'memory_index: [{"at":"1.1","title":"A","row":"x {1} + {2}"}]'
            + NL + "---" + NL + "body" + NL)
        rows_p, probs = collect_from_notes()
        if rows_p:
            print("[FAIL] a row missing slot 2 was built anyway: %r" % render_rows(rows_p))
            ok = False
        elif not any("missing position" in p for p in probs):
            print("[FAIL] no 'missing position' problem reported: %r" % probs)
            ok = False
    finally:
        globals()["MEMDIR"] = save_dir

    # ⛔ THE TEMPLATE IS THE NEW SINGLE POINT OF TRUST, so a WRONG template must refuse in BOTH
    # directions - o9's catch. Too few slots must not swallow a note; too many must not render
    # short. The partial-batch run only ever proved the second.
    d2 = tempfile.mkdtemp()
    save2 = globals()["MEMDIR"]
    try:
        globals()["MEMDIR"] = d2
        io.open(os.path.join(d2, "a.md"), "w", encoding="utf-8", newline="").write(
            "---" + NL + 'memory_index: [{"at":"k.1","title":"A","row":"x {1} + {2}","order":1}]'
            + NL + "---" + NL)
        io.open(os.path.join(d2, "b.md"), "w", encoding="utf-8", newline="").write(
            "---" + NL + 'memory_index: [{"at":"k.2","title":"B"}]' + NL + "---" + NL)
        io.open(os.path.join(d2, "c.md"), "w", encoding="utf-8", newline="").write(
            "---" + NL + 'memory_index: [{"at":"k.3","title":"C"}]' + NL + "---" + NL)
        rr, pp = collect_from_notes()
        if rr or not any("no slot for" in p for p in pp):
            print("[FAIL] a template with too FEW slots did not refuse: rows=%d %r" % (len(rr), pp))
            ok = False
    finally:
        globals()["MEMDIR"] = save2
        shutil.rmtree(d2, ignore_errors=True)

    # ⭐ IDENTITY SURVIVES A ROW INSERT. This is the whole reason `at` is keyed by NAME: under
    # row NUMBERS, inserting one row rewrote every note after it, so nobody would ever do it.
    d3 = tempfile.mkdtemp()
    save3 = globals()["MEMDIR"]
    try:
        globals()["MEMDIR"] = d3
        for n_, k_, o_ in (("a", "alpha", 1), ("b", "beta", 2), ("c", "gamma", 4),
                           ("n", "delta", 3)):
            io.open(os.path.join(d3, n_ + ".md"), "w", encoding="utf-8", newline="").write(
                "---" + NL + 'memory_index: [{"at":"%s.1","title":"%s","row":"%s: {1}",'
                '"order":%d}]' % (k_, n_.upper(), k_, o_) + NL + "---" + NL)
        rr, pp = collect_from_notes()
        if [r["row"] for r in rr] != ["alpha", "beta", "delta", "gamma"]:
            print("[FAIL] order did not follow `order`: %r" % [r["row"] for r in rr])
            ok = False
    finally:
        globals()["MEMDIR"] = save3
        shutil.rmtree(d3, ignore_errors=True)

    if ok:
        print("[OK] parse, template, round-trip, variant-B render, frontmatter set/get,")
        print("     a note in two rows keeps BOTH titles, a row missing one of its template's")
        print("     slots REFUSES to build, a template with too FEW slots also refuses, and")
        print("     row identity survives an insert because `at` is keyed by NAME.")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("roundtrip")
    sub.add_parser("survey")
    p = sub.add_parser("inject")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--show", type=int, default=10)
    p.add_argument("--write", action="store_true")
    p = sub.add_parser("build")
    p.add_argument("--codes", action="store_true")
    p.add_argument("--out")
    p.add_argument("--supplement", help="with --codes: write the code->note table here")
    p.add_argument("--pointers", help="with --codes: write one pointer file per code here, so "
                                      "every code is a path Read resolves with no help")
    p.add_argument("--style", choices=("path", "bare"), default="path",
                   help="path = [Title](m/24.md), needs nothing. bare = [Title][24], 890 bytes "
                        "smaller and needs a resolution mechanism.")
    p = sub.add_parser("check")
    p.add_argument("--no-hooks", action="store_true",
                   help="compare ignoring hooks. ONLY meaningful against a PRE-switch baseline - "
                        "a post-switch baseline is a rendered file that already contains them, "
                        "so suppressing them on one side only makes every hooked row differ.")
    p.add_argument("--live", action="store_true",
                   help="is MEMORY.md byte-identical to a fresh build? the post-switch question")
    p.add_argument("--against", help="baseline from a git ref instead of the file on disk. "
                                     "REQUIRED once the generated view replaces MEMORY.md, or "
                                     "the check becomes the generator grading its own output.")
    p = sub.add_parser("hooks")
    p.add_argument("--rows", default="", help="comma list or range, e.g. 2 or 2,3 or 2-9")
    p.add_argument("--write", action="store_true")
    p.add_argument("--budget", type=int, default=84,
                   help="max hook chars; the 3/4-parity figure from D30")
    p = sub.add_parser("verify-codes")
    p.add_argument("--index", required=True, help="a variant-B render")
    p.add_argument("--supplement", required=True, help="its code table")
    sub.add_parser("cutover")
    sub.add_parser("citations")
    p = sub.add_parser("verify-titles")
    p.add_argument("--against", default=BASELINE_REF,
                   help="git ref in the memory repo holding a known-good MEMORY.md")
    a = ap.parse_args()
    if a.selftest or not a.cmd:
        return selftest()
    try:
        return {"roundtrip": cmd_roundtrip, "survey": cmd_survey, "inject": cmd_inject,
                "build": cmd_build, "check": cmd_check, "hooks": cmd_hooks,
                "verify-codes": cmd_verify_codes, "verify-titles": cmd_verify_titles,
                "cutover": cmd_cutover,
                "citations": cmd_citations}[a.cmd](a)
    except CodeTableError as e:
        print("[REFUSE] %s" % e, file=sys.stderr)
        print("         Nothing was written. Codes are never renumbered to get past this.",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main() or 0)
