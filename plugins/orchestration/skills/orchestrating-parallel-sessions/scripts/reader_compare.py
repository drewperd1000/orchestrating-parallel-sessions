#!/usr/bin/env python3
"""The Compare view: an agent draft beside the human's rewrite of it, changed passage by passage,
with a box against each change for the human's reason - saved beside the pair and landed to git.

<!-- subject: orchestration -->

<!-- route-tags: reader compare diff why notes explanations rewrite draft teaching loop
     training writing rules delta passage -->

The human, 2026-09-25, verbatim (o8:W72):

  *"I want you to create an HTML interface that will show the diffs between the original and my
  rewrite. I want to be able to enter explanations about why I made the choices I did so that
  you can learn from them and build writing rules to further hone your skills."*

MASTER-TRAINING-DOC.md Section 2: the teaching signal is the DELTA between a draft and his
rewrite of it. A delta tells an agent WHAT he changed; these notes are the only record of WHY.

This module owns the disk half and the markup of the comparison. `render_orchdoc_reader.py`
owns the routes and calls in here, exactly as it does for `reader_scripts.py`.

⛔ THE BROWSER NAMES A SCRIPT BY ITS ISSUED ID AND A PASSAGE BY A KEY THIS SERVER COMPUTED.
It never sends a path, a file name, or the before/after text of a passage. A passage key is
a hash of the passage's before-text and after-text, recomputed from the files on every save;
a key that matches neither a passage in the current comparison nor a note already stored is
refused, so the page cannot write a note about text that is not there.

⛔ THE NOTES FILE IS NOT A SCRIPT AND MUST NOT LOOK LIKE ONE TO ANY SCANNER.
`training_header.py` counts any file whose NAME carries a rewrite suffix as a live rewrite,
and several scanners treat every `.md` as a document. So the file is
`<draft stem>.why-notes.json`: no `owner-rewrite` in the name (the draft's stem, never the
rewrite's), and a `.json` extension that no `.md` scanner and no reader discovery picks up.

⛔ A NOTE IS NEVER DROPPED BECAUSE THE REWRITE MOVED ON, AND NEVER RE-ATTACHED TO ANOTHER
CHANGE. Each note stores the statement index and the full before-text and after-text of the
passage it explains, and its key is a hash of that text. If the draft or the rewrite is edited
and that passage no longer appears, the note is still in the file and still on the page, under
"this note's change no longer matches", with the text it was written about - it is never
quietly moved onto whatever change now sits at the same statement number. Section 3: every
iteration is kept - and a save over an existing notes file snapshots it to `.edit-history/`
first, using the same verified snapshot `reader_scripts` uses for rewrites.

⛔ ONE NOTE PER SAVE (o9's ruling, 2026-09-25). The page posts one key and one note's text.
The server reads the file as it is now, under the corpus lock, sets that entry, and writes it
with `reader_scripts._write_atomic`. A page that posted every box would carry a stale copy of
the other notes and erase anything typed in another tab since it opened.
"""
import datetime
import difflib
import hashlib
import html
import json
import re

import reader_scripts as S
import script_notes as SN
import secret_patterns

NOTES_SUFFIX = ".why-notes.json"
FORMAT = "why-notes/1"
KEY_RE = re.compile(r"^[0-9a-f]{16}$")
NOTE_MAX_CHARS = 20000
MARKER = "<!-- END SCRIPT HEADER -->"
PAUSE_RE = re.compile(r"^\(\(\s*pause\b[^)]*\)\)$", re.I)
# A line that is part of the script's layout, not a statement: a rule, or a markdown comment.
LAYOUT_RE = re.compile(r"^(?:-{3,}|\*{3,}|<!--.*-->)$")
PAIR_RATIO = 0.45          # the same threshold reader_scripts.diff pairs lines at

ABOUT = ("the human's reasons for the changes between an agent draft and his rewrite of it, written "
         "in the OrchDoc Reader's Compare view. Each note names the passage it explains by its "
         "statement index AND the full before-text (draft) and after-text (rewrite), so it "
         "still means something after the rewrite is edited again. statement_before / "
         "statement_after count the body's statements (non-blank lines below END SCRIPT "
         "HEADER that are not pause cues or layout, counted after the human's {{{ }}} review notes "
         "are removed, so a note is never a statement and never shifts a number);a pause cue carries the number of the "
         "statement it follows. Each note is about ONE line: change is mod (a draft line "
         "changed), del (a draft line removed) or add (a line added). draft_unit is a mod or "
         "del's line index among the draft body's non-blank lines; follows is an add's span "
         "[lo, hi] of draft lines it sits after (-1 = before the first). These, with the "
         "before- and after-text and the statement_before / statement_after numbers, are the "
         "ANCHOR WHERE THE NOTE WAS WRITTEN - they describe the draft and rewrite as they were "
         "then, and are never rewritten. last_matched holds the statement numbers the change "
         "was found at AS OF THE LAST NOTE SAVE in the Compare view; a rewrite saved in the "
         "script editor or edited by hand since then can make it out of date, so it is a "
         "record, not a live position. A note with empty text was cleared by the human; the entry "
         "is kept so the change stays on record. If two entries share a key, the first is the "
         "one shown and edited. occurrence (adds only) is the line's place among added lines "
         "with the same text in the same stretch, and same_in_stretch how many there were when "
         "the note was written: identical added lines pair with notes in order while that "
         "number is unchanged; once one is added or removed, the note matches nothing and is "
         "shown as 'no longer matches' rather than moved onto a line it may not be about. By design, an added "
         "line's note follows the SAME TEXT to wherever its span still overlaps - moving an "
         "added line within its stretch keeps its note - but only if same_text_changes is 1 "
         "(it was the one added line with that text when the note was written); otherwise "
         "it needs the exact span it was written at. Where several notes fit one change, the "
         "one written at exactly its position is shown and the others wait. A cleared note "
         "never stops a change from taking a new note. draft_hash is a hash of the draft body "
         "the note was written against. Every position above is a position in THAT draft: "
         "while the draft is unchanged a note is matched by position; once the draft changes "
         "(it has been edited in place before) a note is matched only if its change is the one "
         "change in the whole rewrite with the same text AND same_text_changes is 1 - it was "
         "also the one such change when the note was written - AND it sits between the same "
         "two draft lines as when the note was written (draft_above / draft_below, null past "
         "either end of the draft; for an added line, the lines either side of the gap it "
         "sits in) AND that surrounding was the only one of its kind in the draft then "
         "(draft_site_count is 1) and is now; then the page says the draft "
         "has changed; otherwise it shows as 'no longer matches'. same_text_changes is how "
         "many changes in the comparison had the note's change and text when it was written; "
         "a note without it is never matched against a changed draft. If another entry "
         "here has the same change and text (cleared or not), a later copy of the change "
         "existed, so a count of 1 is out of date: that note needs its exact position and is "
         "never matched against a changed draft. A note with no "
         "draft_hash is treated as written against a changed draft. "
         "Every earlier version of this file is kept under "
         ".edit-history/. MASTER-TRAINING-DOC.md Section 2: the delta is a DIRECTION, not a "
         "specimen - read these as why he moved, not as approved text.")


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def change_key(*parts):
    """A 16-hex key for one line-level change, hashed from its draft anchor and its text.

    ⛔ HOW A NOTE FINDS ITS CHANGE, AND WHY IT IS BUILT THIS WAY (third round, reviews of
    241e874f and 75546bb3). Two earlier keyings each let a note move or go stale when another
    change was edited: an occurrence count (reverting one of two identical pause changes moved
    the first's note onto the second), then draft neighbours with a position added only while
    twins existed (a refrain `X / pause / X / pause / X` let a note written on one pause go
    live on the other, and undoing one twin made the other's note go stale).

    ⭐ THE DRAFT IS THE ANCHOR, ALWAYS. An agent draft is never written in place (a new version
    is a new file - MASTER-TRAINING-DOC.md Section 3), so a position in the DRAFT does not
    move however the rewrite is edited. Every change is ONE LINE and is anchored there:
      - a changed or removed draft line: its draft line index, its old text, its new text
      - an added line: its own text, and the SPAN of draft positions it sits after (see
        `compare`), matched by overlap rather than by equality - see `match_notes`
    Nothing about a neighbour goes into a key, so editing, pairing or un-pairing another line
    can never change this one's. If the draft itself changes, affected notes stop matching and
    show as "no longer matches" - the safe direction."""
    return hashlib.sha256("\x00".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]


def notes_name(draft_stem):
    return draft_stem + NOTES_SUFFIX


# ==========================================================================================
# THE COMPARISON - header set aside, body split into units, units paired, words diffed
# ==========================================================================================

def split_header(text):
    """(header, body). The header is everything above `<!-- END SCRIPT HEADER -->`, marker
    included; with no marker the whole text is body."""
    t = S.norm_text(text or "")
    i = t.find(MARKER)
    if i < 0:
        return "", t
    j = i + len(MARKER)
    return t[:j], t[j:]


def units(body):
    """[{"text", "kind", "stmt"}] - one per non-blank line of the body, the human's notes removed.

    ⭐ A LINE, NOT A BLANK-LINE PARAGRAPH. In this corpus a statement is one line, and a pause
    cue sometimes sits directly on the line above the next statement with no blank between
    them. Splitting on blank lines would glue that cue to the statement in one file and not
    the other, and report a change nobody made."""
    return units_notes(body)[0]


def _lines(body):
    return [s for s in (ln.strip() for ln in body.split("\n")) if s]


def units_notes(body):
    """(units, notes, counts). The human's `{{{ }}}` review notes are removed BEFORE the body is
    split into units, with the one shared rule in `script_notes.strip_notes` (no second regex).

    ⛔ A NOTE IS NOT A STATEMENT (o8:D56, lane o8L159). Before this, every non-blank line was a
    unit, so each line of a note was an "Added" statement and every statement number after it
    moved: AT-10 v3's rewrite measured 52 statements where the script has 32, and a statement
    written on the line where a note closed was shown glued to the note as an added line.
    Because notes are gone before any unit exists, adding or removing a note changes no unit,
    no statement number, no draft position and so no why-note key.

    `notes` is [{"after": k, "text"}]: k is the index of the unit the note sits after (-1 =
    before the first unit); a note inside a line sits after that line. The page shows each
    one where it sits, unnumbered (see `_place_notes`). `counts` is strip_notes' report."""
    stripped, counts = SN.strip_notes(body)
    notes = []
    for m in SN.NOTE.finditer(body):
        # The units before the note: those of everything above it, notes removed. A note that
        # starts part-way along a line has that line's start among them, so it sits after it.
        k = len(_lines(SN.strip_notes(body[:m.start()])[0])) - 1
        notes.append({"after": k, "text": m.group(0)[len(SN.OPEN):-len(SN.CLOSE)].strip()})
    out, n = [], 0
    for s in _lines(stripped):
        if PAUSE_RE.match(s):
            kind = "pause"
        elif LAYOUT_RE.match(s):
            kind = "layout"
        else:
            kind = "statement"
            n += 1
        out.append({"text": s, "kind": kind, "stmt": n})
    return out, notes, counts


def _place_notes(blocks, ub, notes):
    """Blocks with each of the rewrite's notes put as a ("note", n) block right after the
    rewrite line it sits after. A run of unchanged lines is split at a note, so a note is never
    hidden inside a collapsed run. Placement only: no passage, key or number is touched."""
    if not notes:
        return blocks
    at = {}
    for n in notes:
        at.setdefault(n["after"], []).append(n)
    idx = {id(u): j for j, u in enumerate(ub)}
    out = [("note", n) for n in at.get(-1, [])]
    for kind, x in blocks:
        if kind != "same":
            out.append((kind, x))
            out.extend(("note", n) for n in at.get(x.get("rewrite_unit"), []))
            continue
        run = []
        for u in x:
            run.append(u)
            here = at.get(idx[id(u)])
            if here:
                out.append(("same", run))
                out.extend(("note", n) for n in here)
                run = []
        if run:
            out.append(("same", run))
    return out


_WORDS = re.compile(r"\w+|[^\w\s]", re.U)


def _similar(a, b):
    """0..1 - how alike two lines are, by words (faster than by character on long lines)."""
    return difflib.SequenceMatcher(None, _WORDS.findall(a), _WORDS.findall(b),
                                   autojunk=False).ratio()


def _align(old, new):
    """[(i or None, j or None)] - the lines of one replaced block paired BY SIMILARITY, in order.

    ⛔ NOT BY POSITION. The first build paired the first old line with the first new line, and
    so on. Delete one line and edit the two after it, and it showed "the deleted line -> the
    first edited line", asking the human why he made a change he never made (o9's review). This is
    the best order-preserving pairing by total similarity; a pair below PAIR_RATIO is not a
    pair, and those lines are shown as removed or added."""
    n, m = len(old), len(new)
    sim = [[_similar(old[i], new[j]) for j in range(m)] for i in range(n)]
    best = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            b = max(best[i + 1][j], best[i][j + 1])
            if sim[i][j] >= PAIR_RATIO:
                b = max(b, sim[i][j] + best[i + 1][j + 1])
            best[i][j] = b
    out, i, j = [], 0, 0
    while i < n or j < m:
        if i < n and j < m and sim[i][j] >= PAIR_RATIO and \
                abs(best[i][j] - (sim[i][j] + best[i + 1][j + 1])) < 1e-9:
            out.append((i, j))
            i, j = i + 1, j + 1
        elif i < n and (j >= m or abs(best[i][j] - best[i + 1][j]) < 1e-9):
            out.append((i, None))
            i += 1
        else:
            out.append((None, j))
            j += 1
    return out


def _change(change, ua, ub, i, j, follows, last_j, occ=0):
    """One line-level change. `i` is the draft line (mod, del), `j` the rewrite line (mod,
    add); `follows` is an added line's [lo, hi] span of draft positions, and `occ` its place
    among the added lines with the SAME TEXT in the same stretch (0, 1, ...)."""
    old = ua[i] if i is not None else None
    new = ub[j] if j is not None else None
    before = old["text"] if old else ""
    after = new["text"] if new else ""
    kind = (old or new)["kind"]
    if change == "mod":
        key = change_key("mod", i, before, after)
    elif change == "del":
        key = change_key("del", i, before)
    else:
        # ⛔ NO REWRITE POSITION IN THE KEY (review of 650a08d6): it changed whenever a line
        # was added above, and a note keyed by it went stale for no reason. Two identical added
        # lines in one stretch are told apart by `occ`, which only same-text lines affect.
        key = change_key("add", after, follows[0], follows[1], occ)
    sb = old["stmt"] if old else (ua[follows[1]]["stmt"] if follows[1] >= 0 else 0)
    sa = new["stmt"] if new else (ub[last_j]["stmt"] if last_j >= 0 else 0)
    return {"key": key, "change": change, "before": before, "after": after, "kind": kind,
            "draft_unit": i, "follows": list(follows) if follows else None,
            "occurrence": occ if change == "add" else None,
            "rewrite_unit": j, "statement_before": sb, "statement_after": sa,
            "n_before": 1 if old else 0, "n_after": 1 if new else 0}


def compare(draft_text, rewrite_text):
    """{"header_a", "header_b", "blocks": [...], "passages": [...]}.

    A block is ("same", [units]) for an unchanged run or ("change", passage). Every passage is
    ONE LINE: a draft line changed (paired with its rewrite line by similarity - see _align),
    a draft line removed, or a rewrite line added. Each gets its own note box.

    ⭐ AN ADDED LINE'S ANCHOR IS A SPAN, [lo, hi]: lo is the last draft line before it that
    survives into the rewrite (unchanged or changed-and-paired), hi is the last draft line
    before it counting removed ones too. Both are draft positions, so they do not move when
    the rewrite is edited elsewhere; the span is what lets an added line keep its note when a
    NEIGHBOURING line is edited hard enough to stop pairing (the review's case C) - that turns
    the neighbour from "changed" into "removed + added", which widens the span, and a note
    matches any span that overlaps the one it was written at."""
    ha, ba = split_header(draft_text)
    hb, bb = split_header(rewrite_text)
    ua, _, counts_a = units_notes(ba)
    ub, notes_b, counts_b = units_notes(bb)
    sm = difflib.SequenceMatcher(None, [u["text"] for u in ua], [u["text"] for u in ub],
                                 autojunk=False)
    blocks, passages = [], []
    st = {"surv": -1, "j": -1}          # last surviving draft line; last rewrite line shown

    def add(p):
        blocks.append(("change", p))
        passages.append(p)

    def run(dels, adds):
        """One stretch of removed and added lines between two survivors: removals first."""
        lo = st["surv"]
        hi = max(dels) if dels else lo
        for i in dels:
            add(_change("del", ua, ub, i, None, None, st["j"]))
        seen = {}
        for j in adds:
            occ = seen.get(ub[j]["text"], 0)
            seen[ub[j]["text"]] = occ + 1
            p = _change("add", ua, ub, None, j, (lo, hi), st["j"], occ)
            add(p)
            st["j"] = j
        # How many same-text lines the stretch holds. It is part of the key as well as of the
        # match: when the count changes, a note written under the old count no longer matches
        # (R6), and a new note on the same line must not land on that stale entry's key.
        for p in passages[len(passages) - len(adds):]:
            p["same_in_stretch"] = seen[p["after"]]
            p["key"] = change_key("add", p["after"], lo, hi, p["occurrence"],
                                  p["same_in_stretch"])

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            blocks.append(("same", ub[j1:j2]))
            st["surv"], st["j"] = i2 - 1, j2 - 1
            continue
        if tag == "delete":
            run(list(range(i1, i2)), [])
            continue
        if tag == "insert":
            run([], list(range(j1, j2)))
            continue
        dels, adds = [], []
        for pi, pj in _align([u["text"] for u in ua[i1:i2]], [u["text"] for u in ub[j1:j2]]):
            if pi is not None and pj is not None:
                run(dels, adds)
                dels, adds = [], []
                add(_change("mod", ua, ub, i1 + pi, j1 + pj, None, st["j"]))
                st["surv"], st["j"] = i1 + pi, j1 + pj
            elif pi is not None:
                dels.append(i1 + pi)
            else:
                adds.append(j1 + pj)
        run(dels, adds)
    dh = draft_hash(ua)
    texts = [u["text"] for u in ua]
    for p in passages:
        p.update(draft_site(texts, p))
        p["draft_hash"] = dh
        # The draft's hash is part of every key, so a change found in a DIFFERENT draft never
        # shares a key with a note written against the old one - a save to the new change
        # cannot land on the old note's entry.
        p["key"] = change_key(p["key"], dh)
    return {"header_a": ha, "header_b": hb, "blocks": _place_notes(blocks, ub, notes_b),
            "passages": passages, "units_a": len(ua), "units_b": len(ub), "draft_hash": dh,
            "notes_a": counts_a, "notes_b": counts_b}


def draft_hash(ua):
    """A short hash of the draft's BODY lines - what every draft position is a position in."""
    return hashlib.sha256("\n".join(u["text"] for u in ua).encode("utf-8")).hexdigest()[:16]


def _note_matches(n, p):
    """Was note `n` written on live change `p`? Exact for a changed or removed draft line; for
    an added line, the same text and an overlapping span of draft positions."""
    ch = n.get("change")
    if ch != p["change"] or not isinstance(n.get("after"), str):
        return False
    if ch in ("mod", "del"):
        return (n.get("draft_unit") == p["draft_unit"] and n.get("before") == p["before"]
                and n.get("after") == p["after"])
    f = n.get("follows")
    if not (isinstance(f, list) and len(f) == 2 and all(isinstance(x, int) and
                                                         not isinstance(x, bool) for x in f)):
        return False
    occ = n.get("occurrence", 0)
    cnt = n.get("same_in_stretch")
    if not isinstance(occ, int) or isinstance(occ, bool) or \
            (cnt is not None and (not isinstance(cnt, int) or isinstance(cnt, bool))):
        return False
    # ⭐ Identical added lines in one stretch pair with notes IN ORDER (review of 650a08d6,
    # R5): the note written on the second is the second's.
    # ⛔ AND ONLY WHILE THE STRETCH HOLDS THE SAME NUMBER OF THEM (R6). Lines with the same
    # text cannot be told apart, so once one is removed or another added, "the second" may be
    # a different line than the one the note was written on - delete the first of two and the
    # old second becomes the new first. So a changed count sends the note to "no longer
    # matches"; it is never moved onto a remaining line.
    # ⛔ A SPAN THAT MOVED IS FOLLOWED ONLY FOR A NOTE THAT WAS ON THE ONE SUCH LINE (review of
    # 056ca8f4, round 8). Z added after S1 and after S2, a note on the second; then the second
    # Z is deleted and S2 rewritten hard. S2 stops pairing, the stretches [0, 0] and [1, 1]
    # merge into [0, 1], the one Z left is "the first of one" in it - and the second Z's note
    # went live on the first Z. Overlap is what lets a LONE added line keep its note when a
    # neighbour is rewritten (the review's case C, which must keep working); when the note was
    # written beside other changes with the same text, overlap cannot say which one it was.
    # So: the exact span it was written at, or a moved span only if same_text_changes is 1.
    if (f[0], f[1]) != (p["follows"][0], p["follows"][1]) and not _was_unique(n):
        return False
    return (n["after"] == p["after"] and f[0] <= p["follows"][1]
            and p["follows"][0] <= f[1] and occ == p["occurrence"]
            and (p["same_in_stretch"] == cnt if cnt is not None
                 else occ < p["same_in_stretch"]))


def _exact(n, p):
    """Was note `n` (already known to match `p`) written at exactly `p`'s draft position? Always
    for a changed or removed line; for an added line, the same span, not just an overlapping
    one."""
    if p["change"] != "add":
        return True
    f = n.get("follows")
    return (f[0], f[1]) == (p["follows"][0], p["follows"][1])


def _same_text(n, p):
    """Does note `n` describe the same change as `p` by TEXT alone (no position)?"""
    if n.get("change") != p["change"]:
        return False
    if p["change"] == "add":
        return n.get("after") == p["after"]
    if p["change"] == "del":
        return n.get("before") == p["before"]
    return n.get("before") == p["before"] and n.get("after") == p["after"]


def _was_unique(n):
    """Was note `n`'s change the only change with its text in the comparison it was written in?
    Only a count of exactly 1, stored at the save, says yes; anything else (missing, hand-edited,
    a bool) says no."""
    c = n.get("same_text_changes")
    return isinstance(c, int) and not isinstance(c, bool) and c == 1


def same_text_count(passages, p):
    """How many changes in `passages` have `p`'s change and text (`p` included)."""
    return sum(1 for q in passages if _same_text(p, q))


def _at(texts, k):
    return texts[k] if 0 <= k < len(texts) else None


def draft_site(texts, p):
    """{"draft_above", "draft_below", "draft_site_count"} for change `p`, given the draft's body
    lines `texts`: the draft lines directly above and below the change (None past either end),
    and how many places in the draft have that same surrounding.

    For a changed or removed line the place is the line itself: the lines either side of draft
    line i, and the count of draft lines with the same text AND the same two neighbours. For
    an added line the place is the gap it sits in, after draft line `follows[1]`: the lines
    either side of that gap, and the count of gaps with the same two lines either side.

    ⭐ WHY (review of 056ca8f4, round 8). Against a CHANGED draft a note is matched by text
    alone, and a count of 1 at both moments does not say the change is on the same line: the
    draft `A X B X C` with only the first X changed, a note on it, then the human moves his change
    to the second X and the draft gets a typo fixed elsewhere - one X->Y change then and now,
    on different lines. The lines around a change tell those two apart. The count stops the
    same thing happening where the surrounding repeats too (`A X B A X B`)."""
    if p["change"] == "add":
        g = p["follows"][1]
        above, below = _at(texts, g), _at(texts, g + 1)
        count = sum(1 for k in range(-1, len(texts))
                    if _at(texts, k) == above and _at(texts, k + 1) == below)
    else:
        i = p["draft_unit"]
        above, below = _at(texts, i - 1), _at(texts, i + 1)
        count = sum(1 for k in range(len(texts))
                    if texts[k] == texts[i] and _at(texts, k - 1) == above
                    and _at(texts, k + 1) == below)
    return {"draft_above": above, "draft_below": below, "draft_site_count": count}


def _same_site(n, p):
    """Against a changed draft: does note `n` sit between the same two draft lines as `p`, and
    was that surrounding the only one of its kind in the draft both when the note was written
    and now? A note without these fields (written before round 8, or hand-edited) says no."""
    for f in ("draft_above", "draft_below"):
        if f not in n or not (n[f] is None or isinstance(n[f], str)) or n[f] != p[f]:
            return False
    c = n.get("draft_site_count")
    return (isinstance(c, int) and not isinstance(c, bool) and c == 1
            and p["draft_site_count"] == 1)


def match_notes(passages, notes):
    """Attach stored notes to live changes. Sets each passage's "key" to the key of the note
    that matched it (so its box edits THAT note), and returns {key: note} for the matches.

    ⛔ ONE TO ONE, OR NOT AT ALL. A note that could match two live changes, or a live change
    two notes could match, is ambiguous - and an ambiguous note is shown as "no longer
    matches" rather than guessed onto one. A note must never show live against a change it
    was not written about."""
    # ⛔ POSITIONS ARE ONLY TRUSTED AGAINST THE DRAFT THEY WERE TAKEN IN (o9, round 5). The
    # premise that a draft never changes was false - AT-10-worry-off-ramp-PILOT.md (renamed
    # AT-10-worry-off-ramp-PILOT-Worry-Has-an-Off-Ramp.md on 2026-10-01) was edited
    # in place twice - and a line inserted above a noted change shifted every position below
    # it, so notes landed silently on OTHER changes. So a note carries the hash of the draft
    # body it was written against:
    #   same hash      -> matched by position, exactly as below;
    #   different hash -> matched only if its change is UNIQUE in the whole rewrite by text
    #                     (see the second pass), shown with "the draft has changed since";
    #   otherwise      -> "no longer matches". Never moved.
    # A note with no hash (written before this rule) is treated as a changed draft.
    #
    # ⛔ UNIQUE NOW IS NOT ENOUGH; IT MUST HAVE BEEN UNIQUE WHEN THE NOTE WAS WRITTEN (review of
    # 406aaca5, round 6). A refrain changed X->Y at two places, a note on the FIRST, then the
    # draft loses its first X: the one X->Y change left is unique by text, so the second pass
    # put the first refrain's note on the second refrain. When the note was written there were
    # two such changes and nothing recorded which one it was about, so text alone cannot place
    # it. `same_text_changes` records that count at the save; the second pass takes only a note
    # whose count was 1, and a note without the count waits under "no longer matches".
    dh = passages[0]["draft_hash"] if passages else None
    cands = {}
    first = usable_indices(notes)
    drift = []
    for ni, n in enumerate(notes):
        if ni not in first:
            continue
        if dh is not None and n.get("draft_hash") == dh:
            pis = [pi for pi, p in enumerate(passages) if _note_matches(n, p)]
            # A note written at EXACTLY a change's position is about that change; one that only
            # overlaps it is a weaker fit. A note with an exact fit is not a candidate anywhere
            # else.
            cands[ni] = [pi for pi in pis if _exact(n, passages[pi])] or pis
        else:
            drift.append(ni)
    # ⛔ A NOTE WITH A SIBLING IS ONLY TRUSTED AT ITS EXACT POSITION (review of cc9ca319, round
    # 9). A "sibling" is another stored note on a change of the same kind and text. Its
    # existence proves a second copy of the change existed at some point, so this note's
    # same_text_changes == 1 is out of date. The case: Z added once after S2, note N1 (count
    # 1); the human adds a second Z after S1 and notes it (N2, count 2); then N1's Z is deleted and
    # S2 rewritten hard. N2's count keeps N2 to its exact span, which no longer exists, so N1
    # was the only note that fit the one Z left - and went live on N2's line. Now a sibling:
    #   - in the claim loop, wins only at its exact span (it still counts as a claimant, so a
    #     change it fits stays closed while it has text, and it waits under "no longer
    #     matches");
    #   - in the drift pass (changed draft), is never matched by text.
    # A CLEARED note counts as a sibling: it was saved on a change with that text, so it is
    # the same proof. This never closes a box by itself - only a claimant WITH text closes one
    # (below), and such a note is listed under "no longer matches" where the human can edit it.
    # (A valid entry need not carry "change"; _same_text reads its SECOND argument's change
    # by index, so that argument is checked first.)
    #
    # ⛔ ADDED LINES ONLY (round 10). A changed or removed line's note already needs its exact
    # draft line in the first pass, and a draft line with the same text AND the same two
    # neighbours, unique then and now, in the drift pass - a second copy cannot move it. The
    # rule cost a real case: draft `A X B X C`, N1 on the first X's change, the human moves his
    # change to the second X and writes N2, then the draft gets a typo fixed elsewhere. N1 made
    # N2 a sibling, so N2 waited under "no longer matches" though it fits only its own line.
    sib = {ni: notes[ni].get("change") == "add" and
           any(nj != ni and nj in first and notes[nj].get("change") == "add"
               and _same_text(notes[ni], notes[nj]) for nj in range(len(notes)))
           for ni in first}
    # ⛔ A NOTE OUTDATED BY A LATER NOTE AT THE SAME SPAN NEVER WINS (round 10, the review's
    # R3a; present since 056ca8f4). Notes are appended in save order. If a note saved AFTER
    # this one, on an added line with the same text at the same span, recorded a different
    # same_in_stretch, the number of such lines in that gap changed after this note was
    # written - so "the first of one" no longer says which line this note was about. The case:
    # a pause added after S2, N1 on it (count 1); the human adds Q and a second pause in the same
    # gap and notes the second (N2, count 2); then he deletes N1's pause. The gap holds one
    # pause again, so N1 fit the pause after Q - N2's line - by span, count and order.
    # Such a note still counts as a claimant (a change it fits stays closed while it has
    # text, and it waits under "no longer matches"); it is never matched by text either.
    # A CLEARED later note counts: it was saved at that span under that count, the same proof.
    # A blank note still never closes a box by itself (the claimant rule below is unchanged).
    # Cost: if the human deletes N2's pause instead of N1's, the rewrite has the same shape and N1
    # waits too. Known limit (R3b): without N2 nothing records the second pause, and that
    # case cannot be told from the human moving Q in front of his one pause, which keeps its note.
    outdated = {ni: notes[ni].get("change") == "add" and
                any(nj > ni and nj in first and notes[nj].get("change") == "add"
                    and _same_text(notes[ni], notes[nj])
                    and notes[nj].get("follows") == notes[ni].get("follows")
                    and notes[nj].get("same_in_stretch") != notes[ni].get("same_in_stretch")
                    for nj in range(len(notes)))
                for ni in first}
    claim = {}
    for ni, pis in cands.items():
        for pi in pis:
            claim.setdefault(pi, []).append(ni)
    matched = {}
    taken = set()
    for pi, nis in claim.items():
        # One note with one candidate, alone on this change - or, where several notes fit
        # it, the ONE written at exactly this position (round 8: a note the human writes in a box
        # is always exact, so an older note that only overlaps cannot take it away). A note
        # that loses here is not moved to another change; it waits under "no longer matches".
        win = [ni for ni in nis if len(cands[ni]) == 1
               and (not sib[ni] or _exact(notes[ni], passages[pi])) and not outdated[ni]]
        if len(nis) > 1:
            win = [ni for ni in win if _exact(notes[ni], passages[pi])]
        if len(win) != 1:
            continue
        p, n = passages[pi], notes[win[0]]
        p["key"] = n["key"]
        matched[n["key"]] = n
        taken.add(n["key"])
    # ⛔ A LIVE CHANGE THAT AN UNMATCHED NOTE COULD BE ABOUT IS NOT OPEN FOR A NEW NOTE. The
    # note stays under "no longer matches" (editable there); a second note on this change
    # would be a second entry for the same thing - the review of 650a08d6 found exactly that
    # loop. Its key is changed so it cannot collide with the stored note's, and a save to it
    # is refused (Compare.save).
    # ⛔ BUT A CLEARED NOTE NEVER CLOSES A BOX (review of 056ca8f4, round 8, defect 3). A blank
    # note is not listed under "no longer matches" (render), so a change blocked only by blank
    # notes could not be noted anywhere at all. Such a change keeps its own box; a note saved
    # there is written at exactly its position, so it wins the change at the next view. A
    # blank note still counts against a note WITH text that fits the same change - that note
    # may have been written on another line, so it is still not shown here.
    for pi, nis in claim.items():
        p = passages[pi]
        if p["key"] not in taken and any(_text(notes[ni].get("note")).strip() for ni in nis):
            p["blocked"] = True
            p["key"] = change_key("blocked", p["key"])
    # SECOND PASS - notes written against a different draft. Only a change that is the ONE
    # change in the whole rewrite with this text can be its change; two notes wanting the same
    # one are both left out. A change already holding a note from the current draft wins, and
    # nothing here blocks a box: a new note on the change carries the current draft's hash and
    # is matched in the first pass.
    # ⛔ AND IT MUST SIT BETWEEN THE SAME TWO DRAFT LINES (review of 056ca8f4, round 8, defect
    # 2): one change with this text then and now can still be on a different line each time
    # (see `draft_site`). A note without the stored neighbours is not matched here.
    want = {}
    for ni in drift:
        if not _was_unique(notes[ni]) or sib[ni] or outdated[ni]:
            continue
        hits = [pi for pi, p in enumerate(passages) if _same_text(notes[ni], p)]
        if len(hits) == 1 and _same_site(notes[ni], passages[hits[0]]):
            want.setdefault(hits[0], []).append(ni)
    for pi, nis in want.items():
        p = passages[pi]
        if len(nis) != 1 or p["key"] in taken or p.get("blocked"):
            continue
        n = notes[nis[0]]
        if not _text(n.get("note")).strip():
            # A cleared note is not carried onto a changed draft: the box keeps this draft's
            # own key, so a note written there is matched by position from then on.
            continue
        p["key"] = n["key"]
        p["drifted"] = True
        matched[n["key"]] = n
        taken.add(n["key"])
    # ⛔ AN OPEN BOX NEVER CARRIES THE KEY OF A NOTE IT DOES NOT SHOW (round 10). A save edits
    # the stored entry with the box's key, so a box keyed like a note that did not win it
    # would write into THAT note. Reached by R3a with N1 cleared: the one pause left has N1's
    # own key (same span, order, count and draft), N1 is outdated so the box opens empty, and
    # a note typed there refilled N1 - which, being outdated, then waited under "no longer
    # matches" and closed the box. Such a box gets a fresh key, so the save writes a NEW entry,
    # which is matched by position (not by key) at the next view.
    stored = {n["key"] for i, n in enumerate(notes) if i in first}
    for p in passages:
        while p["key"] not in taken and p["key"] in stored:
            p["key"] = change_key("fresh", p["key"])
    return matched


# ==========================================================================================
# THE NOTES FILE
# ==========================================================================================

def empty_notes(draft_name, rewrite_name):
    return {"format": FORMAT, "about": ABOUT, "draft": draft_name, "rewrite": rewrite_name,
            "overall": {"text": "", "updated": ""}, "notes": []}


def load_notes(path):
    """(notes dict, raw bytes or None, error or None). A file that is not what this writes is
    an error - never silently replaced, because replacing it would lose whatever it holds."""
    if not path.exists():
        return None, None, None
    try:
        raw = path.read_bytes()
    except OSError as ex:
        return None, None, "could not read %s (%s)" % (path.name, type(ex).__name__)
    try:
        d = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as ex:
        return None, raw, "%s is not valid JSON (%s) - not touched" % (path.name, ex)
    if not isinstance(d, dict) or not isinstance(d.get("notes"), list):
        return None, raw, "%s is not a notes file this reader wrote - not touched" % path.name
    # ⛔ NOTHING IS DROPPED OR REWRITTEN ON READ. An entry this reader cannot use (hand-edited,
    # or written by something else) stays in the list exactly as it is, is shown as unreadable,
    # and is written back untouched by the next save (review of 241e874f).
    d.setdefault("overall", {"text": "", "updated": ""})
    return d, raw, None


def valid_note(n):
    """Is this entry one this reader can show in a box and edit? Anything else is kept as is
    and shown as unreadable."""
    return (isinstance(n, dict) and isinstance(n.get("key"), str) and bool(KEY_RE.match(n["key"]))
            and isinstance(n.get("note", ""), str) and isinstance(n.get("before", ""), str)
            and isinstance(n.get("after", ""), str))


def usable_indices(notes):
    """Indices of the entries the page shows in a box and a save edits: valid, and the FIRST
    entry carrying its key.

    ⛔ ONE RULE FOR THE PAGE AND THE SAVE (o9's review of 75546bb3). A hand-edited file with two
    entries under one key had the page showing the LAST (a dict built over the list) while a
    save edited the FIRST, so an edit appeared not to take. Now both use the first; a later
    entry with the same key is shown as unreadable and kept in the file untouched."""
    seen, out = set(), set()
    for i, n in enumerate(notes):
        if valid_note(n) and n["key"] not in seen:
            seen.add(n["key"])
            out.add(i)
    return out


def valid_overall(ov):
    return isinstance(ov, dict) and isinstance(ov.get("text", ""), str)


def dump_notes(d):
    """⛔ NOT SORTED. The first build sorted the notes on every save, which crashed on a
    hand-edited number stored as text, and moved lines about for no reason. Entries keep the
    order they were written in; a new one is appended."""
    return (json.dumps(d, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


class Compare:
    """The Compare view over a `reader_scripts.Corpus`."""

    def __init__(self, corpus):
        self.corpus = corpus

    # ---- which pair ----------------------------------------------------------------------

    def resolve_pair(self, sid, listing=None):
        """(draft, rewrite, None) or (None, None, (code, why)). `sid` may name either side.

        Only a draft's ONE exact rewrite is compared. A variant (`... - the human rewrite
        (working-tree variant)`) is a second rewrite of the same draft; its notes would need a
        second file, and choosing a name for it is not this change's to make."""
        listing = listing or self.corpus.discover()
        f, why = self.corpus.resolve(sid, listing)
        if f is None:
            return None, None, (404, why)
        by = listing["by_id"]
        if f["kind"] == "draft":
            d = f
            r = by.get(f.get("pair")) if f.get("pair") else None
            if r is None:
                return None, None, (400, "this draft has no rewrite of its own to compare "
                                         "against")
        elif f["kind"] == "rewrite":
            r = f
            d = by.get(f.get("pair")) if f.get("pair") else None
            if d is None:
                return None, None, (400, "no agent draft with this rewrite's base name is in "
                                         "the folder, so there is nothing to compare it to")
            if d.get("pair") != r["id"]:
                return None, None, (400, "this is a variant rewrite (%s) - Compare covers a "
                                         "draft and its one main rewrite only" % r["name"])
        else:
            return None, None, (400, "a process log has no rewrite to compare")
        return d, r, None

    def notes_path(self, draft):
        p = draft["path"].parent / notes_name(draft["stem"])
        # Confined like every other path here; the name comes from a file on disk.
        if self.corpus.confine(p) is None or p.resolve().parent != \
                draft["path"].resolve().parent:
            return None
        return p

    # ---- the view ------------------------------------------------------------------------

    def view(self, sid):
        listing = self.corpus.discover()
        d, r, err = self.resolve_pair(sid, listing)
        if err:
            return {"ok": False, "code": err[0], "error": err[1]}
        try:
            ta = S.norm_text(d["path"].read_bytes())
            tb = S.norm_text(r["path"].read_bytes())
        except OSError as ex:
            return {"ok": False, "code": 500,
                    "error": "could not read the pair (%s)" % type(ex).__name__}
        np_ = self.notes_path(d)
        if np_ is None:
            return {"ok": False, "code": 400, "error": "the notes file would sit outside the "
                                                       "corpus - refused"}
        notes, raw, bad = load_notes(np_)
        # ⛔ THE CONTENT IS SCANNED BEFORE ANY OF IT IS RENDERED, the way /file/ scans a file.
        for label, t in (("the draft", ta), ("the rewrite", tb),
                         ("the notes file", raw.decode("utf-8", "replace") if raw else "")):
            kind = secret_patterns.find_credential(t)
            if kind:
                return {"ok": False, "code": 403,
                        "error": "refused: %s holds what looks like %s, so the Compare view "
                                 "will not show it" % (label, kind)}
        cmp_ = compare(ta, tb)
        matched = match_notes(cmp_["passages"], notes["notes"] if notes else [])
        return {"ok": True, "draft": d, "rewrite": r, "notes_path": np_, "notes": notes,
                "notes_error": bad, "expect": S.sha256(raw) if raw is not None else "",
                "cmp": cmp_, "matched": matched,
                "notes_rel": np_.relative_to(self.corpus.workspace).as_posix()}

    # ---- the save ------------------------------------------------------------------------

    def save(self, sid, key, text):
        """Write ONE note - the one the page names by `key` - and nothing else.

        ⛔ ONE NOTE PER REQUEST, READ AND WRITTEN UNDER THE LOCK (o9's ruling for W72). The page
        never posts the notes file, or even its other boxes: a second tab, or a page opened
        before another save, holds a stale copy of every OTHER note, and posting that copy would
        erase notes typed elsewhere. So the server reads the file as it is NOW, sets the one
        entry, and writes it back with `reader_scripts._write_atomic`, after a verified
        snapshot of what it held.

        `key` is a passage key from the current comparison, a key already stored in the file
        (a note whose passage has changed since), or "overall". Empty text BLANKS that note -
        the entry and its before/after text stay, and the page shows an empty box."""
        if not isinstance(key, str) or not (key == "overall" or KEY_RE.match(key)):
            return {"ok": False, "code": 400,
                    "error": "a note is named by the key this page was given - refused"}
        if not isinstance(text, str) or len(text) > NOTE_MAX_CHARS:
            return {"ok": False, "code": 400,
                    "error": "a note is text of at most %d characters" % NOTE_MAX_CHARS}
        kind = secret_patterns.find_credential(text)
        if kind:
            return {"ok": False, "code": 400,
                    "error": "not saved: the note holds what looks like %s. Notes are "
                             "committed and pushed, so take it out first." % kind}
        t = text.strip()

        with self.corpus._lock:
            v = self.view(sid)
            if not v.get("ok"):
                return {"ok": False, "code": v.get("code", 400), "error": v.get("error")}
            if v["notes_error"]:
                return {"ok": False, "code": 409, "error": v["notes_error"]}
            d, r, path = v["draft"], v["rewrite"], v["notes_path"]
            existed = v["notes"] is not None
            cur = v["notes"] or empty_notes(d["name"], r["name"])
            live = {p["key"]: p for p in v["cmp"]["passages"]}
            # Entries this reader did not write, or cannot read, are carried through as they
            # are - `list(...)` keeps them, and only a VALID entry is ever looked up or changed.
            notes = list(cur["notes"])
            idx = next((i for i, n in enumerate(notes) if valid_note(n) and n["key"] == key),
                       None)
            if key != "overall" and key not in live and idx is None:
                return {"ok": False, "code": 409,
                        "error": "that change is not in this comparison any more - the draft "
                                 "or the rewrite changed on disk since the page was opened. "
                                 "Nothing was written. Copy your text and reload."}
            now = _now()
            new = dict(cur, draft=d["name"], rewrite=r["name"], format=FORMAT, about=ABOUT)
            changed = True
            if key == "overall":
                ov = cur.get("overall")
                if not valid_overall(ov):
                    return {"ok": False, "code": 409,
                            "error": "the whole-script note in the file is not in the shape "
                                     "this reader writes, so it was not overwritten. Fix it "
                                     "by hand in %s." % v["notes_rel"]}
                if (ov.get("text") or "").strip() == t:
                    changed = False
                else:
                    new["overall"] = dict(ov, text=t, updated=now)
            elif idx is not None:
                # ⛔ CLEARING A NOTE BLANKS ITS TEXT; THE ENTRY STAYS. Removing the entry
                # deleted most of the lines the commit that added it had added, and the
                # workspace's pre-commit guard (stale_overwrite_guard.check_undoes_commit)
                # refuses exactly that - after which every later save of the file was refused
                # too, and the notes sat uncommitted in the checkout (review of 241e874f). A
                # blank note is shown as an empty box.
                if (notes[idx].get("note") or "").strip() == t:
                    changed = False
                elif not t:
                    notes[idx] = dict(notes[idx], note="", updated=now, cleared=now)
                else:
                    notes[idx] = dict(notes[idx], note=t, updated=now)
            elif not t:
                changed = False
            else:
                p = live[key]
                if p.get("blocked"):
                    return {"ok": False, "code": 409,
                            "error": "a note already in the file could be about this change, "
                                     "and it cannot tell which - it is shown under 'no longer "
                                     "matches'. Edit it there. Nothing was written."}
                entry = {"key": key, "change": p["change"], "kind": p["kind"],
                         "statement_before": p["statement_before"],
                         "statement_after": p["statement_after"]}
                if p["change"] == "add":
                    entry["follows"] = p["follows"]
                    entry["occurrence"] = p["occurrence"]
                    entry["same_in_stretch"] = p["same_in_stretch"]
                else:
                    entry["draft_unit"] = p["draft_unit"]
                entry.update({"before": p["before"], "after": p["after"],
                              "draft_hash": p["draft_hash"],
                              # Read only against a CHANGED draft: the draft lines around the
                              # change, and how many places had that surrounding (round 8).
                              "draft_above": p["draft_above"],
                              "draft_below": p["draft_below"],
                              "draft_site_count": p["draft_site_count"],
                              # Read only against a CHANGED draft: was this the one change
                              # with this text when the note was written? (round 6)
                              "same_text_changes": same_text_count(v["cmp"]["passages"], p),
                              "note": t, "created": now, "updated": now})
                notes.append(entry)
            if changed:
                # ⭐ WHERE A MATCHED CHANGE IS NOW GOES IN `last_matched`, AND ONLY THERE. A line
                # added above a change moves its statement number without changing the change
                # (o9's review). ⛔ The fields the note was WRITTEN with - its anchor and its
                # statement numbers - are never overwritten: they are the record of where it was
                # written (review of 75546bb3). Updated at a write, never on a GET; a note that
                # no longer matches keeps its last `last_matched` as it was.
                matched = v["matched"]
                first = usable_indices(notes)
                for i, n in enumerate(notes):
                    if i not in first or n["key"] not in matched:
                        continue
                    p = live.get(n["key"])
                    if p is None:
                        continue
                    pos = {"statement_before": p["statement_before"],
                           "statement_after": p["statement_after"]}
                    same = all(n.get(f) == val for f, val in pos.items())
                    if same and "last_matched" not in n:
                        continue
                    if n.get("last_matched") != pos:
                        notes[i] = dict(n, last_matched=pos)
            new["notes"] = notes
            base = {"ok": True, "key": key, "notes_rel": v["notes_rel"],
                    "anchor_rel": r["path"].relative_to(self.corpus.workspace).as_posix(),
                    "opened_name": r["name"], "text": t}
            if not changed:
                return dict(base, changed=False, created=False, snapshot_rel=None,
                            message="Nothing changed - no file written.")
            data = dump_notes(new)
            snap = None
            if existed:
                try:
                    snap = self.corpus._snapshot(path, path.read_bytes())
                except Exception as ex:
                    return {"ok": False, "code": 500,
                            "error": "could not keep a copy of the current notes first (%s: "
                                     "%s) - so NOTHING was written" % (type(ex).__name__, ex)}
            try:
                S._write_atomic(path, data)
            except Exception as ex:
                return {"ok": False, "code": 500,
                        "error": "the write failed (%s: %s) - the notes on disk are unchanged"
                                 % (type(ex).__name__, ex)}
            ws = self.corpus.workspace
            return dict(base, changed=True, created=not existed,
                        snapshot_rel=snap.relative_to(ws).as_posix() if snap else None,
                        history_rels=self.corpus.history_rels(path),
                        updated=now, message="Saved.", count=len(notes))


# ==========================================================================================
# THE MARKUP - every piece of text escaped before any tag is added around it
# ==========================================================================================

_TAG_SPLIT = re.compile(r"(<[^>]*>)")


def paint(h):
    """Colour dropdown markers and pause cues in already-escaped HTML, touching only the text
    between tags - so a `<del>` or `<ins>` from the word diff is never split. `|` is marked
    only inside a `[[ ]]` group; a bare pipe in a sentence is just a pipe."""
    out, inside = [], False
    for part in _TAG_SPLIT.split(h):
        if part.startswith("<"):
            out.append(part)
            continue
        buf, i = [], 0
        while i < len(part):
            two = part[i:i + 2]
            if two == "[[":
                buf.append('<span class="dd-mk">[[</span>')
                inside, i = True, i + 2
            elif two == "]]":
                buf.append('<span class="dd-mk">]]</span>')
                inside, i = False, i + 2
            elif part[i] == "|" and inside:
                buf.append('<span class="dd-mk">|</span>')
                i += 1
            else:
                buf.append(part[i])
                i += 1
        out.append("".join(buf))
    return "".join(out)


def _line(text):
    return paint(html.escape(text))


def _num(x):
    """A statement number as text, whatever a hand-edited file put there."""
    return str(x) if isinstance(x, (int, str)) and not isinstance(x, bool) else "?"


def _label(p):
    """A heading for a change. ⛔ READS DEFENSIVELY: a stale note's fields come from the file,
    which may have been edited by hand, so nothing here indexes a missing key or formats a
    non-number with %d (review of 241e874f)."""
    kind = p.get("kind")
    sb, sa = p.get("statement_before"), p.get("statement_after")
    if kind == "pause":
        n = sa if p.get("n_after") else sb
        return "Pause after statement %s" % _num(n) if n else "Pause before the first statement"
    if p.get("n_before") and p.get("n_after"):
        if sb == sa:
            return "Statement %s" % _num(sa)
        return "Statement %s (draft %s)" % (_num(sa), _num(sb))
    if p.get("n_after"):
        return "Added - statement %s" % _num(sa)
    return "Removed - draft statement %s" % _num(sb)


def _change_body(before, after):
    """(draft html, rewrite html). Lines recognisably the same are diffed word by word; two
    unrelated texts are shown whole, because word-diffing them produces confetti."""
    if before and after and "\n" not in before and "\n" not in after and \
            difflib.SequenceMatcher(None, before, after).quick_ratio() >= PAIR_RATIO:
        a, b = S._words(before, after)
        return paint(a), paint(b)
    a = "<br>".join("<del>%s</del>" % _line(x) for x in before.split("\n")) if before else ""
    b = "<br>".join("<ins>%s</ins>" % _line(x) for x in after.split("\n")) if after else ""
    return a, b


def _box(key, note, label, overall=False):
    """One note box: its own Save button and its own status line, because each note is saved
    on its own (see Compare.save)."""
    return ('<div class="cv-why"><label class="cv-whyl" for="cv-ta-%s">%s <span class="cv-hint">'
            '%s</span></label><textarea class="cv-ta" id="cv-ta-%s" data-cv-key="%s" rows="2" '
            'placeholder="%s">%s</textarea><div class="cv-row"><button type="button" '
            'class="nbtn cv-save" data-cv-save="%s">Save this note</button>'
            '<span class="cv-st" aria-live="polite"></span></div></div>'
            % (html.escape(key), "Why, overall?" if overall else "Why?", html.escape(label),
               html.escape(key), html.escape(key),
               "What the rewrite as a whole was going for - optional" if overall else
               "Why you made this change - optional",
               html.escape(note or ""), html.escape(key)))


def _text(x):
    return x if isinstance(x, str) else ""


def _change_html(p, note, stale=False):
    a, b = _change_body(_text(p.get("before")), _text(p.get("after")))
    label = _label(p) if not stale else "Recorded against: %s" % _label(dict(
        p, n_before=1 if p.get("before") else 0, n_after=1 if p.get("after") else 0))
    rows = []
    if a:
        rows.append('<div class="cv-side cv-a"><span class="cv-tag">draft</span>'
                    '<div class="cv-t">%s</div></div>' % a)
    if b:
        rows.append('<div class="cv-side cv-b"><span class="cv-tag">rewrite</span>'
                    '<div class="cv-t">%s</div></div>' % b)
    cls = "cv-chg" + (" cv-pause" if p.get("kind") == "pause" else "") + \
        (" cv-stale" if stale else "")
    return ('<section class="%s" data-cv-passage="%s"><div class="cv-h">%s%s</div>%s%s'
            "</section>" % (cls, html.escape(p["key"]), html.escape(label),
                            ' <span class="cv-when">note written %s</span>'
                            % html.escape(_text((note or {}).get("updated")))
                            if stale and note else "",
                            "".join(rows) + (
                                '<p class="cv-hint cv-drift">The draft has changed since this '
                                "note was written. It is shown here because this is the only "
                                "change in the rewrite with exactly this text.</p>"
                                if p.get("drifted") and not stale else ""),
                            '<p class="cv-hint">A note in the file could be about this change '
                            "but cannot be placed with certainty, so it is shown under 'no "
                            "longer matches' below. Edit it there.</p>"
                            if p.get("blocked") and not stale else
                            _box(p["key"], _text((note or {}).get("note")),
                                 "this note's change no longer matches" if stale else
                                 "saved beside the pair, with this passage's text")))


def _unreadable_html(n):
    """An entry this reader cannot use: shown as it is in the file, escaped, with no box - and
    left in the file untouched by every save."""
    try:
        shown = json.dumps(n, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        shown = repr(n)
    return ('<section class="cv-chg cv-stale cv-unreadable"><div class="cv-h">An entry this '
            'page cannot read - kept in the file exactly as it is</div><pre class="cv-t">%s'
            '</pre></section>' % html.escape(shown))


def _same_html(us):
    n = len(us)
    body = "".join('<div class="cv-same-l%s">%s</div>'
                   % (" cv-pause" if u["kind"] == "pause" else "", _line(u["text"]))
                   for u in us)
    stm = sum(1 for u in us if u["kind"] == "statement")
    return ('<details class="cv-same"><summary>%d unchanged line%s (%d statement%s)</summary>'
            "%s</details>" % (n, "" if n == 1 else "s", stm, "" if stm == 1 else "s", body))


def _note_html(n):
    """One of the human's `{{{ }}}` notes, where it sits in the rewrite: unnumbered, with no box,
    because it is not a change and not a statement. ⭐ SHOWN, NOT DROPPED: the Compare view is
    where the human reads his rewrite against the draft, and a note he wrote there explains a
    nearby change; leaving it out would remove his own text from his own view."""
    return ('<section class="cv-chg cv-rnote" style="border-style:dotted;background:var(--wash)">'
            '<div class="cv-h">Your review note in the rewrite <span class="cv-when">- not a '
            "statement: it is left out of the numbering and the comparison</span></div>"
            '<div class="cv-t">%s</div></section>' % html.escape(_text(n.get("text"))))


def _marker_hints(c):
    """A line for each note marker strip_notes could not pair, and for notes in the DRAFT
    (left out of the comparison and not shown). Notes in the rewrite need no line."""
    out = []
    for side, k in (("rewrite", "notes_b"), ("draft", "notes_a")):
        n = c.get(k) or {}
        if n.get("unclosed"):
            out.append("%d note opener%s in the %s %s not closed: the text from the opener "
                       "to the end of its paragraph is left out of the comparison."
                       % (n["unclosed"], "" if n["unclosed"] == 1 else "s", side,
                          "is" if n["unclosed"] == 1 else "are"))
        if n.get("unopened"):
            out.append("%d note closer%s in the %s ha%s no opener before it, so it is compared "
                       "as script text." % (n["unopened"], "" if n["unopened"] == 1 else "s",
                                            side, "s" if n["unopened"] == 1 else "ve"))
    na = (c.get("notes_a") or {}).get("notes")
    if na:
        out.append("The draft holds %d review note%s; %s left out of the comparison."
                   % (na, "" if na == 1 else "s", "it is" if na == 1 else "they are"))
    return ['<p class="cv-hint cv-marks">%s</p>' % html.escape(x) for x in out]


def render(v):
    """(html body, stats) for a `Compare.view` result."""
    c = v["cmp"]
    notes = v["notes"] or {"overall": {"text": ""}, "notes": []}
    first = usable_indices(notes["notes"])
    good = [n for i, n in enumerate(notes["notes"]) if i in first]
    bad = [n for i, n in enumerate(notes["notes"]) if i not in first]
    # ⛔ BOXES ARE FILLED ONLY FROM NOTES match_notes ACCEPTED (review of 650a08d6). Filling
    # them by key from every stored note showed a note live that the matcher had rejected.
    by_key = dict(v.get("matched") or {})
    parts = []
    hdr = S.diff(c["header_a"], c["header_b"]) if (c["header_a"] or c["header_b"]) else None
    if hdr is not None:
        parts.append('<details class="cv-hdr"><summary>Script header (above END SCRIPT '
                     "HEADER) - %s</summary>%s</details>"
                     % ("unchanged" if hdr["same"] else "+%d / -%d lines"
                        % (hdr["added"], hdr["removed"]), hdr["html"]))
    ov = notes.get("overall")
    if valid_overall(ov):
        parts.append('<div class="cv-overall">%s</div>'
                     % _box("overall", ov.get("text"), "the rewrite as a whole", overall=True))
    else:
        parts.append(_unreadable_html({"overall": ov}))
    if not c["passages"]:
        parts.append('<div class="sd-none">The body of the rewrite is identical to the '
                     "draft.</div>")
    parts.extend(_marker_hints(c))
    for kind, x in c["blocks"]:
        parts.append(_same_html(x) if kind == "same" else _note_html(x) if kind == "note"
                     else _change_html(x, by_key.get(x["key"])))
    # A blank note (cleared by the human) whose change is gone has nothing left to show.
    stale = [n for n in good if n["key"] not in by_key and _text(n.get("note")).strip()]
    if stale or bad:
        parts.append('<h3 class="cv-sh">This note\'s change no longer matches (%d)</h3>'
                     '<p class="cv-hint">The draft or the rewrite was edited after these were '
                     "written, so the exact change they explain is not in the comparison "
                     "above. They are kept, with the text they were written about, and are not "
                     "attached to any other change.</p>"
                     % (len(stale) + len(bad)))
        for n in stale:
            parts.append(_change_html(dict(n, n_before=0, n_after=0), n, stale=True))
        for n in bad:
            parts.append(_unreadable_html(n))
    stats = {"passages": len(c["passages"]),
             "noted": sum(1 for p in c["passages"]
                          if _text((by_key.get(p["key"]) or {}).get("note")).strip()),
             "stale": len(stale) + len(bad)}
    return "".join(parts), stats
