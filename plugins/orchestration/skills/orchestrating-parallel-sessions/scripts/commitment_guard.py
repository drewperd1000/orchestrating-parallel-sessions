#!/usr/bin/env python3
"""A promise is not a description. Refuse an edit that resolves a mismatch by weakening it.

<!-- subject: knowledge-routing -->
<!-- route-tags: commitment promise spec privacy policy terms weaken direction correction audit -->

⭐ THE DIRECTION OF CORRECTION WAS NEVER QUESTIONED, and that is the whole defect:

  - a doc describing BEHAVIOUR is a description; a mismatch means the DOC is wrong
  - a doc describing a PROMISE is a spec; a mismatch is a BUG REPORT AGAINST THE CODE

Both look like "the doc disagrees with reality" to an agent doing an audit. One of them is a
documentation fix. The other is a privacy commitment being quietly withdrawn from the people it
was made to.

A guard that fires on producing a CLAIM has no chokepoint.* Weakening a promise is an ARTIFACT.
So it is catchable, where "an agent believed the wrong thing" is not.

HOW TO MARK A COMMITMENT
------------------------
In the doc that carries the promise:

    <!-- commitment: what we promise users about client data storage -->
    ...the promise text...
    <!-- /commitment -->

Any commit that DELETES or SHORTENS a line inside that region is refused. Adding to it is
always allowed - a promise may be strengthened without ceremony, and the asymmetry is the
point.

To change one anyway, the commit must carry the ruling that authorised it:

    git commit -m '...

⛔ THE ESCAPE DELIBERATELY REQUIRES A HUMAN'S RULING, not a rationale. An agent can always
produce a rationale; it cannot produce a ruling it was never given. That asymmetry is the only
thing standing between "the code does X" and a promise quietly becoming "we may do X".

    commitment_guard.py --check      pre-commit
    commitment_guard.py --audit      which commitment regions exist
    commitment_guard.py --selftest

Exit 9 refuses. Exit 0 allows. Fails OPEN on any internal error.
"""
import argparse
import os
import re
import subprocess
try:
    from no_window import flags
except ImportError:  # a partial delivery must not take the tool down
    def flags(extra=0):
        return extra
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pathlib  # noqa: E402
from workspace_paths import WS  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REFUSE = 9
OPEN_RE = re.compile(r"<!--\s*commitment:\s*(.+?)\s*-->")
CLOSE = "<!-- /commitment -->"
RULING_RE = re.compile(r"<!--\s*commitment-change:\s*(.+?)\s*-->", re.S)


# A verb anywhere in the marker read "the human was not asked" and "the page said ...; the human
# to review" as rulings.
_RULED_VERBS = (r"ruled|rules|asked|said|says|approved|confirmed|wrote|answered|chose|picked|"
                r"decided|told|agreed|accepted|signed off|directed|instructed")
_RULED_RE = re.compile(r"\bDrew\b(?:,[^,]{1,40},)?"
                       r"(?:\s+(?:has|had|also|then|himself|already|just|again|personally|"
                       r"explicitly))*\s+(?:re-?)?(?:" + _RULED_VERBS + r")\b"
                       r"|\bDrew['\u2019]s\s+(?:\w+\s+)?(?:ruling|decision|answer|words)\b"
                       r"|\b(?:ruled|approved|confirmed|decided|chosen|picked|requested|"
                       r"answered)\s+by\s+the human\b", re.I)
_QUOTE_RE = re.compile(r'["\u201c][^"\u201c\u201d]{2,}["\u201d]')
_ID_RE = re.compile(r"\b[oO](\d{1,2})[\s:/._-]*([DQTFW])(\d{1,4})\b")
_QUOTED_RE = re.compile(r'["\u201c][^"\u201c\u201d]*["\u201d]')
_DISCLAIM_RE = re.compile(r"not (?:a|an|his|the)\b[^.;]{0,40}\bruling|to confirm at review|"
                          r"\bpending\b|\bawaiting\b|\bstanding rule\b|\bno ruling\b|"
                          r"\bpunctuation only\b|\bI (?:judged|decided|believe)\b|"
                          r"\bDrew\s+(?:has|had|did|does)(?:\s+not|n['\u2019]t)\b|"
                          r"\bDrew\s+never\s+(?:ruled|asked|said|approved|confirmed|wrote|answered|"
                          r"chose|picked|decided|told)\b|"
                          r"\bnot\s+(?:yet\s+)?(?:ruled|approved|confirmed|decided|answered)\b|"
                          r"\b(?:not|without)\s+the human\b", re.I)


def ruling_problem(text, docs_dir=None):
    """None when the marker text reads as a recorded ruling, else why it does not.
    """
    t = " ".join(text.split())
    outside = _QUOTED_RE.sub(" ", t)
    said = _DISCLAIM_RE.search(outside)
    if said:
        return "the marker says it is not a ruling (%r)" % said.group(0)
    if not _RULED_RE.search(outside):
        return "it does not say that the human ruled, asked or approved this"
    ids = _ID_RE.findall(t)
    if not _QUOTE_RE.search(t) and not ids:
        return "it has neither the human's words in double quotes nor the id of the ruling"
    bases = [docs_dir] if docs_dir else [ROOT, WS]
    for n, kind, num in ids:
        name = "ORCHESTRATOR-DECISIONS-o%s.md" % n
        for base in bases:
            doc = pathlib.Path(base) / name
            text = doc.read_text(encoding="utf-8", errors="replace") if doc.is_file() else ""
            text += "\n" + other_repo_git(base, "show", "origin/main:%s" % name)
            if text.strip():
                if not re.search(r"\b%s%s\b" % (kind, num), text):
                    return "o%s %s%s is not in %s, here or on origin/main" % (n, kind, num, name)
                break
        else:
            # No such doc, so no such ruling. A checkout with no docs at all cannot tell.
            if any(next(pathlib.Path(b).glob("ORCHESTRATOR-DECISIONS-o*.md"), None) for b in bases):
                return "there is no %s, so o%s %s%s cannot be a ruling" % (name, n, kind, num)
    return None


def _root():
    """The repo THIS commit is in - not the workspace.
    """
    try:
        p = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=10, creationflags=flags())
        if p.returncode == 0 and p.stdout.strip():
            return pathlib.Path(p.stdout.strip())
    except Exception:
        pass
    return WS


ROOT = _root()


def _text(b):
    """Bytes from git as text, with line endings translated the way text=True translated them.

    The working copy is read with read_text(), which also translates, so both sides of the
    comparison in weakened() still split into the same lines.
    """
    return b.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


def git(*a, cwd=None):
    """(rc, stdout, stderr) as text.

    This used text=True, which decodes inside subprocess's reader THREAD. `git show
    HEAD:<file>` on a PNG hands that thread byte 0x89 - the first byte of every PNG - and
    the thread dies with `UnicodeDecodeError: 'utf-8' codec can't decode byte 0x89`,
    printing a traceback on every commit that modified a screenshot. The commit still went
    through only because the main thread then failed too and the except below returned (1,
    "", "").

    Binary files are now skipped before anything reads them (see binary_staged). Decoding with
    errors="replace" here is for the other case: a TEXT file that is not valid UTF-8, which
    git's numstat calls text and which must not print the same traceback.
    """
    try:
        p = subprocess.run(["git", "-C", str(cwd or ROOT)] + list(a),
                           capture_output=True, timeout=20, creationflags=flags())
        return p.returncode, _text(p.stdout), _text(p.stderr)
    except Exception:
        return 1, "", ""


def other_repo_git(repo, *a):
    """Stdout of git in `repo`, without the variables a commit hook inherits.
    """
    env = {k: v for k, v in os.environ.items()
           if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX")}
    try:
        p = subprocess.run(["git", "-C", str(repo)] + list(a), capture_output=True, timeout=20,
                           env=env, creationflags=flags())
        return _text(p.stdout)
    except Exception:
        return ""


def binary_staged():
    """Staged paths git itself calls binary: `-` in both columns of `git diff --numstat`.

    ⭐ GIT'S OWN TEST, not a second one. It is the NUL-in-the-first-8000-bytes check plus any
    `binary` / `-diff` in .gitattributes, so this agrees with what `git diff` shows the author.
    A binary file cannot carry promise text, and reading one as UTF-8 feeds the check garbage.
    """
    rc, out, _ = git("diff", "--cached", "--numstat", "-z")
    if rc != 0:
        return set()
    found, toks, i = set(), out.split("\0"), 0
    while i < len(toks):
        parts = toks[i].split("\t")
        if len(parts) != 3:
            i += 1
            continue
        added, deleted, path = parts
        # With -z a rename or copy leaves the path field empty and puts the old and new paths
        # in the next two fields.
        if path == "":
            paths, i = toks[i + 1:i + 3], i + 3
        else:
            paths, i = [path], i + 1
        if added == "-" and deleted == "-":
            found.update(p for p in paths if p)
    return found


def regions(text):
    """[(label, [lines])] for every marked commitment region."""
    out, lines = [], text.split("\n")
    i = 0
    while i < len(lines):
        m = OPEN_RE.search(lines[i])
        if m:
            body = []
            j = i + 1
            while j < len(lines) and CLOSE not in lines[j]:
                body.append(lines[j])
                j += 1
            # ⛔ AN UNCLOSED MARKER IS NOT A REGION. Both were documents QUOTING the syntax while
            # explaining it, which is the same shape as the retired-path hook blocking its own
            # commit message: a guard matching prose that describes it rather than prose that
            # uses it.
            if j < len(lines):
                out.append((m.group(1), body))
            i = j
        i += 1
    return out


_DASH_ENTITY_RE = re.compile(r"&(?:[mn]dash|minus|horbar|hyphen|#(?:8208|8209|8210|8211|8212|8213|8722|45)|"
                             r"#x(?:2010|2011|2012|2013|2014|2015|2212|2d));", re.I)


# A mark that belongs to a number is part of what the number says, so it stays as a word:
# "100% refund" and "$100 refund" are different promises, and "3.5 days" is not "3-5 days".
_NUMBER_MARKS = (
    (re.compile(r"(?<=\d),(?=\d{3}(?!\d))"), ""),                                    # 1,000 = 1000
    (re.compile(r"(?<=\d)\.(?=\d)"), "point"),
    (re.compile(r"(?<=\d)[-\u2010\u2011\u2012\u2013\u2014\u2015\u2212](?=\d)"), " to "),
    (re.compile(r"(?<=\d)/(?=\d)"), " of "),
    (re.compile(r"(?<=\d)\s*%"), " percent"),
    (re.compile(r"\$\s*(?=\d)"), " dollar "),
    (re.compile(r"\u20ac\s*(?=\d)"), " euro "),
    (re.compile(r"\u00a3\s*(?=\d)"), " pound "),
    (re.compile(r"\u00a5\s*(?=\d)"), " yen "),
    (re.compile(r"(?<=\d)\+(?!\d)"), " plus"),
    (re.compile(r"\+\s*(?=\d)"), " plus "),
    (re.compile(r"<\s*(?=\d)"), " under "),
    (re.compile(r">\s*(?=\d)"), " over "),
    (re.compile(r"\u2264\s*(?=\d)"), " at most "),
    (re.compile(r"\u2265\s*(?=\d)"), " at least "),
    (re.compile(r"[~\u2248]\s*(?=\d)"), " about "),
)
_WORD_RE = re.compile(r"[^\W_]+")
_STOP_RE = re.compile(r"[.!?;]")
_STRONG_MARK_RE = re.compile("[:()\\u2010-\\u2015\\u2212-]")
# A footnote mark, a strikethrough or a comment opener put next to the promise takes it back with
# no word lost. A doubled * is bold, which promises nothing.
_TAKEN_BACK_RE = re.compile(r"(?<!\*)\*(?!\*)|[\u2020\u2021]|~~|<!--|//")
# Marks that divide a sentence: changing to or from nothing, or moving, changes what is promised.
_DIVIDERS = ("stop", "comma")
_MEANING_CHANGED = ({"stop", "none"}, {"stop", "comma"}, {"comma", "none"})


def _parts(line):
    """(words, gaps) of a line. gaps[i] is the text just before words[i]; one gap more than words.

    Dashes, hyphens, quotes, case and the HTML entity forms of the dashes no longer count.
    """
    t = _DASH_ENTITY_RE.sub("\u2014", line)
    for pat, word in _NUMBER_MARKS:
        t = pat.sub(word, t)
    return [w.lower() for w in _WORD_RE.findall(t)], _WORD_RE.split(t)


def _plain(line):
    """A line reduced to its words, one space between them."""
    return " ".join(_parts(line)[0])


def _mark(gap):
    """The strongest mark in a gap: "stop", "strong" (a dash, colon or bracket), "comma" or "none"."""
    return ("stop" if _STOP_RE.search(gap) else "strong" if _STRONG_MARK_RE.search(gap)
            else "comma" if "," in gap else "none")


def _marks_kept(old_gaps, new_gaps):
    """False when the marks between the same words change what the words promise."""
    pairs = [(_mark(o), _mark(n)) for o, n in zip(old_gaps[1:-1], new_gaps[1:-1])]
    if any({o, n} in _MEANING_CHANGED for o, n in pairs):
        return False
    # "charged. After the trial ends - cancel" to "charged - after the trial ends. Cancel": each
    # gap alone is an allowed swap, and together they move the sentence end.
    return not any(any(o == d != n for o, n in pairs) and any(n == d != o for o, n in pairs)
                   for d in _DIVIDERS)


def _find(old_parts, new_parts):
    """Where the old words sit in the new line, as one of: "kept", "stop moved", None.

    Whole words in order, so "sell" is not found in "resell". Between two old words a sentence
    end or a comma may become a dash or a colon, which is what PR 621 did, but neither may appear,
    vanish or move:

        "You will not be charged. After the trial ends, cancel."
        "You will not be charged after the trial ends. Cancel."
        "No refunds after 30 days" and "No, refunds after 30 days"

    Those have the same words in the same order and opposite promises. The gap after the last
    old word is free, because extending a sentence replaces its full stop, except for a mark that
    takes the promise back (_TAKEN_BACK_RE), which counts on either side of the old words.
    """
    ow, og = old_parts
    nw, ng = new_parts
    n, found = len(ow), None
    for i in range(len(nw) - n + 1):
        if nw[i:i + n] == ow:
            found = "stop moved"
            added = any(_TAKEN_BACK_RE.search(ng[i + j]) and not _TAKEN_BACK_RE.search(og[j])
                        for j in range(n + 1))
            if not added and _marks_kept(og, ng[i:i + n + 1]):
                return "kept"
    return found


def weakened(old_text, new_text):
    """[(label, removed_line)] - lines that left a commitment region, or got shorter.

    SHORTER counts, not just deleted. "we never store your client data" edited to "we limit
    storage of your client data" is the same act as deleting it, and a delete-only check would
    wave it through - which is precisely the shape the originating incident took.

    ⚠️ WHAT THIS CANNOT SEE, stated plainly so nobody trusts it further than it goes: survival
    of the old text is not strength. "...never leaves your device" -> "...never leaves your
    device unless you opt in" passes, because the promise is still literally present and a
    qualifying clause was added after it. This function catches DELETION and SHORTENING. It
    does not read meaning, and a reviewer still has to.
    """
    old = {lab: body for lab, body in regions(old_text)}
    new = {lab: body for lab, body in regions(new_text)}
    hits = []
    for lab, body in old.items():
        if lab not in new:
            hits.append((lab, "the entire commitment region was removed"))
            continue
        kept = [l.strip() for l in new[lab] if l.strip()]
        kept_parts = [_parts(k) for k in kept]
        for line in body:
            s = line.strip()
            if not s:
                continue
            # ⭐ CONTAINMENT, NOT EQUALITY - and this asymmetry IS the rule. A promise that still
            # appears INSIDE a new line survived: it was kept and extended, which is
            # strengthening, and strengthening needs no ceremony. A first version compared for
            # equality and therefore refused "we never store your client data" -> "...data
            # forever" - blocking the one direction that should always be free, which is the
            # false-positive direction that gets a guard disabled. ⛔ COMPARE WITHOUT TERMINAL
            # PUNCTUATION. Extending a sentence replaces its full stop with a comma, so a raw
            # substring test reads every real strengthening as a removal.
            old_parts = _parts(s)
            core = " ".join(old_parts[0])
            # It carries no promise; skip it.
            if not core:
                continue
            found = {_find(old_parts, k) for k in kept_parts}
            if "kept" in found:
                continue
            if "stop moved" in found:
                hits.append((lab, "SAME WORDS, BUT A MARK THAT CHANGES THEIR MEANING: %s" % s[:50]))
                continue
            shorter = [k for k in kept if k and _plain(k) and _plain(k) in core]
            hits.append((lab, ("SHORTENED to %r: %s" % (shorter[0][:40], s[:60])) if shorter
                         else ("REMOVED: %s" % s[:70])))
    return hits


def check(msg_path=None):
    """msg_path is git's $1 in a commit-msg hook - the file holding the message being written.

    ⛔ None means NO MESSAGE IS VISIBLE, which is what pre-commit gets. The guard still refuses
    a weakening in that case, but says so explicitly rather than silently making the documented
    escape unreachable.
    """
    rc, out, _ = git("diff", "--cached", "--name-status", "-z", "--find-renames")
    if rc != 0:
        return 0
    staged, was, deleted = [], {}, set()
    toks = out.split("\0")
    while len(toks) > 1:
        status = toks.pop(0)
        path = toks.pop(0)
        if status[:1] in "RC" and toks:
            old_path, path = path, toks.pop(0)
            was[path] = old_path
        elif status[:1] == "D":
            deleted.add(path)
        staged.append(path)
    if not staged:
        return 0

    # ⛔ WAS: git config --get commit.template, whose result was never read. It looked like
    # the escape's implementation and was a no-op - which is why the missing escape survived
    # review. A line that gestures at a mechanism is worse than no line: it answers the
    # reviewer's question wrongly.
    markers = []
    if msg_path:
        try:
            markers = list(RULING_RE.finditer(pathlib.Path(msg_path).read_text(encoding="utf-8",
                                                                               errors="replace")))
        except OSError:
            markers = []
    bad = []
    binary = binary_staged()
    for rel in staged:
        if rel in binary:
            continue                                          # no promise text in a binary
        # A file git calls renamed is compared with its old self: read under the new name alone,
        # it looked like a new file with nothing to weaken.
        rc2, head, _ = git("show", "HEAD:%s" % was.get(rel, rel))
        if rc2 != 0:
            continue                                          # new file: nothing to weaken
        if not OPEN_RE.search(head):
            continue                                          # not a commitment doc
        # The index, because that is what lands. The working copy passed a weakened page whose
        # working copy had been restored, and refused a clean page over an unstaged edit.
        new = ""                                              # deleted
        if rel not in deleted:
            rc3, new, _ = git("show", ":%s" % rel)
            if rc3 != 0:
                # Fails open like every other internal error. Read as a deletion, a timeout
                # refused the commit and told the author a promise had been removed.
                print("[commitment] could not read %s from the index: NOT checked." % rel)
                continue
        bad +=[(rel, lab, why) for lab, why in weakened(head, new)]

    if not bad:
        n = sum(1 for rel in staged
                if rel not in binary
                and OPEN_RE.search(git("show", "HEAD:%s" % was.get(rel, rel))[1]))
        if n:
            # ⛔ NARROW ON PURPOSE. "none weakened" asserted a clean bill of health for a check
            # that reads text survival, not meaning. The pass line is read by someone about to
            # move on, so it is the line that has to state its own blind spot.
            print("[commitment] %d commitment doc(s) touched - no promise text was REMOVED "
                  "or SHORTENED." % n)
            print("             That is all this checked. A QUALIFIER is not detected:")
            print('             "never leaves your device" -> "...unless you opt in" passes.')
        return 0

    problems = [ruling_problem(m.group(1)) for m in markers]
    ruling = next((m for m, why in zip(markers, problems) if why is None), None)
    if markers and not ruling:
        print("[commitment] The commit-message marker was NOT accepted as a ruling: %s." % problems[0])
        print("             A ruling names the human, says that the human ruled or approved, and quotes the human's words")
        print("             in double quotes or gives the id it is recorded under .")
        print()
    if ruling:
        # ⭐ ALLOWED, AND SAID OUT LOUD. The point is never that a promise cannot change - it is
        # that changing one is a RULING and has to be recorded as such. A silent pass would
        # lose the very thing the escape exists to capture, so the authorisation is echoed
        # into the commit output where it stays greppable.
        print("[commitment] WEAKENED - allowed by ruling in the commit message:")
        print("             %s" % " ".join(ruling.group(1).split())[:300])
        for rel, lab, why in bad[:8]:
            print("             %s: %s" % (rel, lab))
        return 0

    print("[commitment] REFUSED: this edit WEAKENS a promise.")
    print()
    for rel, lab, why in bad[:8]:
        print("          %s" % rel)
        print("            commitment: %s" % lab)
        print("            %s" % why)
    print()
    print("          ⛔ A doc describing BEHAVIOUR is corrected to match the code.")
    print("             A doc describing a PROMISE is a spec - a mismatch is a BUG")
    print("             REPORT AGAINST THE CODE, not a wording problem.")
    print()
    print("          Once, an agent found the Privacy Policy contradicting the")
    print("          implementation and rewrote the POLICY. The direction of correction")
    print("          was never questioned. That is what this refuses.")
    print()
    print("          If the code is wrong, fix the code. If the promise genuinely changed,")
    print("          the commit message must carry the ruling that authorised it:")
    print()
    print('            <!-- commitment-change: <who ruled it, when, and what they said> -->')
    print()
    print("          A rationale is not a ruling. You can always write the first.")
    if msg_path is None:
        print()
        print("          ⚠️ NO COMMIT MESSAGE WAS VISIBLE TO THIS RUN, so the ruling above")
        print("             could not be read even if you wrote one. That means this guard is")
        print("             installed as a pre-commit hook, which runs BEFORE the message")
        print("             exists. Reinstall it on commit-msg:")
        print()
        print("               python <scripts>/commitment_guard.py --install")
        print()
        print("             Until then the only way past is --no-verify, which switches off")
        print("             EVERY hook in this repo - so fix the install, not the commit.")
    return REFUSE


HOOK_BODY = """#!/bin/sh
# commitment_guard - installed by commitment_guard.py --install
# ⛔ commit-msg, NEVER pre-commit: pre-commit runs before the message exists, so the
#    documented ruling escape is unreachable from there. See the module docstring.
%s
python "%s" "$1"
[ $? -eq %d ] && exit 1
true
"""


def hook_body(script):
    """The commit-msg hook text for `script`.
    """
    from orchdoc_precommit import VERDICT_FIRST   # lazy: the hook run itself never needs it
    return HOOK_BODY % (VERDICT_FIRST.rstrip("\n"), script, REFUSE)


def install():
    """Put the guard on commit-msg in every repo under the workspace, and off pre-commit.

    ⛔ REMOVING THE OLD LINE IS PART OF THE INSTALL. A leftover pre-commit copy would refuse
    weakenings with no reachable escape while the commit-msg copy allows them - two halves
    disagreeing, which is exactly the state a single hook was chosen to avoid.
    """
    script = str((pathlib.Path(__file__)).resolve())
    body = hook_body(script.replace("\\", "/"))
    repos = [WS] + [d for d in WS.iterdir() if d.is_dir() and (d / ".git").is_dir()]
    n_add = n_strip = 0
    for r in repos:
        hd = r / ".git" / "hooks"
        if not hd.is_dir():
            continue
        (hd / "commit-msg").write_text(body, encoding="utf-8", newline="\n")
        n_add += 1
        pc = hd / "pre-commit"
        if pc.exists():
            src = pc.read_text(encoding="utf-8", errors="replace")
            keep = [ln for ln in src.split("\n") if "commitment_guard" not in ln]
            if len(keep) != len(src.split("\n")):
                pc.write_text("\n".join(keep), encoding="utf-8", newline="\n")
                n_strip += 1
        print("  [OK ] %s" % r.name)
    print("  installed on commit-msg in %d repo(s); stripped a stale pre-commit line in %d"
          % (n_add, n_strip))
    if n_add == 0:
        # Nothing installed is a failure.
        sys.stderr.write("  [FAIL] no repo with a .git/hooks folder under %s. Run it from the "
                         "main checkout, or set CLAUDE_WORKSPACE to it.\n" % WS)
        return 2
    return 0


def audit():
    n = 0
    # The failure message NAMED those three files while the glob structurally could not reach
    # them.
    #
    # ⭐ So the extensions come from where PROMISES live, not from where docs live: rendered
    # pages are code. Scanned across the workspace AND every sibling product repo, because a
    # guard that only sees its own repo is the defect one level up.
    EXT = ("*.md", "*.tsx", "*.ts", "*.jsx", "*.html")
    roots = [WS] + [d for d in WS.iterdir() if d.is_dir() and (d / ".git").exists()]
    seen, files = set(), []
    for r in roots:
        for sub in (r, r / "docs", r / "client" / "src" / "pages", r / "src" / "pages"):
            if not sub.exists():
                continue
            for pat in EXT:
                for f in sub.rglob(pat) if sub != r else sub.glob(pat):
                    k = str(f.resolve()).lower()
                    if k not in seen and "node_modules" not in f.parts:
                        seen.add(k)
                        files.append(f)
    print("  scanned %d file(s) across %d repo(s)" % (len(files), len(roots)))
    for p in files:
        try:
            r = regions(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        for lab, body in r:
            n += 1
            print("  %-46s %s (%d line(s))" % (p.name[:46], lab[:40], len(body)))
    print()
    print("  %d commitment region(s) marked." % n)
    if not n:
        print("  ⛔ NONE. The mechanism exists and guards nothing until a promise is marked -")
        print("     the privacy policy, the terms, the consumer-health-data page.")
    return 0


def selftest():
    ok = True

    def t(label, cond):
        nonlocal ok
        print("  [%s] %s" % ("OK " if cond else "FAIL", label))
        ok &= bool(cond)

    A = "<!-- commitment: client data -->\nwe never store your client data\n<!-- /commitment -->"
    print("commitment_guard selftest")
    t("identical text is not a weakening", not weakened(A, A))
    t("deleting the promise line is caught",
      any("REMOVED" in w for _l, w in weakened(A, A.replace("we never store your client data",
                                                            ""))))
    t("removing the whole region is caught",
      any("entire commitment" in w for _l, w in weakened(A, "nothing here")))
    B = A.replace("we never store your client data", "we never store your client data forever")
    t("ADDING to a promise is allowed", not weakened(A, B))
    C = A.replace("we never store your client data", "we never store your client")
    t("SHORTENING a promise is caught, not just deleting",
      any("SHORTENED" in w for _l, w in weakened(A, C)))
    t("a doc with no commitment region is untouched", not weakened("plain doc", "plain doc2"))
    D = "\n".join(["<!-- commitment: wrapped -->", "see <a>the policy</a>", ".",
                   "we never store your client data", "<!-- /commitment -->"])
    t("a punctuation-only line inside a region is not a promise (lone '.' from a JSX wrap)",
      not weakened(D, "a line above the region changed\n" + D))
    t("the real promise in that same region is still caught",
      any("SHORTENED" in w for _l, w in weakened(D, D.replace("your client data", "your client"))))
    # ⭐ THE TEST THAT WOULD HAVE CAUGHT THE REAL BUG. Every assertion above exercises the
    # guard's LOGIC, and the logic was never wrong - it was never REACHED, because the hook
    # invoked an argument argparse rejected. So: read each installed hook, extract the command
    # line it actually runs, and execute it. A guard is only installed if its own caller works.
    import subprocess
    # ⛔ commit-msg, not pre-commit. The guard moved because pre-commit cannot see a commit
    # message; this scan was pinned to the OLD LOCATION and would have reported "no hook
    # exercised" the moment the underlying defect was fixed.
    hooks = [WS / ".git" / "hooks" / "commit-msg"]
    hooks += [d / ".git" / "hooks" / "commit-msg" for d in WS.iterdir()
              if d.is_dir() and (d / ".git").is_dir()]
    checked = 0
    for h in hooks:
        if not h.exists():
            continue
        for line in h.read_text(encoding="utf-8", errors="replace").split("\n"):
            if "commitment_guard" not in line or line.strip().startswith("#"):
                continue
            cmd = line.split(";")[0].strip()
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                               creationflags=flags())
            checked += 1
            # exit 2 is argparse refusing the arguments - the invocation is wrong, and the
            # hook's `|| true` tail would turn that into a silent approval.
            t("hook invocation is accepted: %s" % str(h.parent.parent.parent.name or "workspace"),
              r.returncode != 2)
    # A HARD `checked > 0` MADE THIS TEST DEPEND ON WHERE IT RAN. It passes in the main
    # workspace, which has sibling repos with hooks, and FAILS in a worktree or a fresh clone
    # that has none - reporting a code defect when the only difference is the directory. That
    # is the same location-pinning that broke the pre-commit scan when the guard moved.
    #
    # The real invariant: every hook that IS installed must be invokable. Zero installed is a
    # fact to report, not a failure to assert.
    if checked:
        t("every installed hook was exercised (%d)" % checked, True)
    else:
        print("  [--  ] no installed hook found here - nothing to exercise "
              "(expected in a worktree or fresh clone)")

    print("\nselftest %s" % ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    # ⛔ ACCEPTED BECAUSE A HOOK PASSED IT AND ARGPARSE REJECTED IT, SILENTLY APPROVING EVERY
    # COMMIT. `--check` is the verb this file's own docstring documents and the form every
    # sibling guard uses; refusing it made the two installs diverge by one word, and the
    # divergent one was a no-op that looked installed.
    ap.add_argument("--check", action="store_true",
                    help="accepted so no install can be wrong")
    ap.add_argument("--install", action="store_true",
                    help="install as a commit-msg hook in every repo here")
    # git passes the message file as $1 to commit-msg. Positional, optional.
    ap.add_argument("msg_file", nargs="?", default=None)
    a = ap.parse_args()
    try:
        sys.exit(selftest() if a.selftest else install() if a.install
                 else audit() if a.audit else check(a.msg_file))
    except Exception:
        sys.exit(0)
