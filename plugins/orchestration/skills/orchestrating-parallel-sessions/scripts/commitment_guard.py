#!/usr/bin/env python3
"""A promise is not a description. Refuse an edit that resolves a mismatch by weakening it.

<!-- subject: knowledge-routing -->
<!-- route-tags: commitment promise spec privacy policy terms weaken direction correction audit -->

⛔ THE INCIDENT (o1, 2026-08-17). The PWA uploads every generated voice track to storage and
never deletes it. The standing premise was *"we never store their voice data - EVER."* On
2026-06-10 an agent audited the Privacy Policy against the code, found the policy claimed
*"stored entirely on your device, never our servers"*, and **rewrote the policy to match the
code** - its own commit message calls the old wording "the false claim."

⭐ THE DIRECTION OF CORRECTION WAS NEVER QUESTIONED, and that is the whole defect:

  - a doc describing BEHAVIOUR is a description; a mismatch means the DOC is wrong
  - a doc describing a PROMISE is a spec; a mismatch is a BUG REPORT AGAINST THE CODE

Both look like "the doc disagrees with reality" to an agent doing an audit. One of them is a
documentation fix. The other is a privacy commitment being quietly withdrawn from the people it
was made to.

⭐ WHY THIS IS MECHANISABLE AT ALL (o1's generalisation, and it predicts which guards hold):
*a guard that fires on producing an ARTIFACT - a file, a commit, a doc, a deploy - works,
because the artifact passes through a chokepoint. A guard that fires on producing a CLAIM has
no chokepoint.* Weakening a promise is an ARTIFACT. So it is catchable, where "an agent
believed the wrong thing" is not.

HOW TO MARK A COMMITMENT
------------------------
In the doc that carries the promise:

    <!-- commitment: what we promise users about voice data storage -->
    ...the promise text...
    <!-- /commitment -->

Any commit that DELETES or SHORTENS a line inside that region is refused. Adding to it is
always allowed - a promise may be strengthened without ceremony, and the asymmetry is the
point.

To change one anyway, the commit must carry the ruling that authorised it:

    git commit -m "... <!-- commitment-change: the human ruled 2026-08-17 that ... -->"

⛔ THE ESCAPE DELIBERATELY REQUIRES A HUMAN'S RULING, not a rationale. An agent can always
produce a rationale; it cannot produce a ruling it was never given. That asymmetry is the only
thing standing between "the code does X" and a promise quietly becoming "we may do X".

    commitment_guard.py --check      pre-commit
    commitment_guard.py --audit      which commitment regions exist
    commitment_guard.py --selftest

Exit 9 refuses. Exit 0 allows. Fails OPEN on any internal error.
"""
import argparse
import re
import subprocess
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


def _root():
    """The repo THIS commit is in - not the workspace.

    ⛔ o10, 2026-08-17: check() ran `git -C WS`, so a commit made inside the PWA repo was
    judged against the WORKSPACE index. Every legal page lives in the PWA, so the guard was
    reading the wrong staging area for the only files it exists to protect.
    """
    try:
        p = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=10)
        if p.returncode == 0 and p.stdout.strip():
            return pathlib.Path(p.stdout.strip())
    except Exception:
        pass
    return WS


ROOT = _root()


def git(*a, cwd=None):
    try:
        p = subprocess.run(["git", "-C", str(cwd or ROOT)] + list(a),
                           capture_output=True, text=True, timeout=20)
        return p.returncode, p.stdout, p.stderr
    except Exception:
        return 1, "", ""


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
            # ⛔ AN UNCLOSED MARKER IS NOT A REGION. Without this, an opening marker with no
            # close swallows the rest of the file - measured 2026-08-17, a 4,348-line
            # "commitment" in an OrchDoc, and a second in a brief. Both were documents
            # QUOTING the syntax while explaining it, which is the same shape as the
            # retired-path hook blocking its own commit message: a guard matching prose that
            # describes it rather than prose that uses it.
            if j < len(lines):
                out.append((m.group(1), body))
            i = j
        i += 1
    return out


def weakened(old_text, new_text):
    """[(label, removed_line)] - lines that left a commitment region, or got shorter.

    SHORTER counts, not just deleted. "we never store your voice data" edited to "we limit
    storage of your voice data" is the same act as deleting it, and a delete-only check would
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
        for line in body:
            s = line.strip()
            if not s:
                continue
            # ⭐ CONTAINMENT, NOT EQUALITY - and this asymmetry IS the rule.
            # A promise that still appears INSIDE a new line survived: it was kept and
            # extended, which is strengthening, and strengthening needs no ceremony. A first
            # version compared for equality and therefore refused "we never store your voice
            # data" -> "...data forever" - blocking the one direction that should always be
            # free, which is the false-positive direction that gets a guard disabled.
            # ⛔ COMPARE WITHOUT TERMINAL PUNCTUATION. Extending a sentence replaces its
            # full stop with a comma, so a raw substring test reads every real strengthening
            # as a removal. Measured 2026-08-17: "...never leaves your device." extended to
            # "...your device, and is deleted within 24 hours." was refused outright.
            core = s.rstrip(" .;,:!?")
            # A line that is ONLY punctuation (a JSX line-wrap such as a lone "." after an
            # <a> element) has an empty core, and the empty string is contained in every
            # line - so the region reported "SHORTENED to '.'" on ANY commit touching the
            # file, including a citation fix six lines above the region (measured
            # 2026-09-16, ConsumerHealthData.tsx:371). It carries no promise; skip it.
            if not core:
                continue
            if any(core in k for k in kept):
                continue
            shorter = [k for k in kept if k and k.rstrip(" .;,:!?") in core]
            hits.append((lab, ("SHORTENED to %r: %s" % (shorter[0][:40], s[:60])) if shorter
                         else ("REMOVED: %s" % s[:70])))
    return hits


def check(msg_path=None):
    """msg_path is git's $1 in a commit-msg hook - the file holding the message being written.

    ⛔ None means NO MESSAGE IS VISIBLE, which is what pre-commit gets. The guard still refuses
    a weakening in that case, but says so explicitly rather than silently making the documented
    escape unreachable.
    """
    rc, out, _ = git("diff", "--cached", "--name-only")
    if rc != 0:
        return 0
    staged = [f for f in out.split("\n") if f.strip()]
    if not staged:
        return 0

    # ⛔ WAS: git config --get commit.template, whose result was never read. It looked like
    # the escape's implementation and was a no-op - which is why the missing escape survived
    # review. A line that gestures at a mechanism is worse than no line: it answers the
    # reviewer's question wrongly.
    ruling = None
    if msg_path:
        try:
            ruling = RULING_RE.search(pathlib.Path(msg_path).read_text(encoding="utf-8",
                                                                       errors="replace"))
        except OSError:
            ruling = None
    bad = []
    for rel in staged:
        rc, old, _ = git("show", ":1:%s" % rel)               # merge base, if any
        rc2, head, _ = git("show", "HEAD:%s" % rel)
        if rc2 != 0:
            continue                                          # new file: nothing to weaken
        p = (ROOT / rel)
        if not p.exists():
            new = ""
        else:
            new = p.read_text(encoding="utf-8", errors="replace")
        if not OPEN_RE.search(head):
            continue                                          # not a commitment doc
        bad += [(rel, lab, why) for lab, why in weakened(head, new)]

    if not bad:
        n = sum(1 for rel in staged
                if git("show", "HEAD:%s" % rel)[1] and OPEN_RE.search(git("show",
                                                                          "HEAD:%s" % rel)[1]))
        if n:
            # ⛔ NARROW ON PURPOSE. "none weakened" asserted a clean bill of health for a check
            # that reads text survival, not meaning. The pass line is read by someone about to
            # move on, so it is the line that has to state its own blind spot.
            print("[commitment] %d commitment doc(s) touched - no promise text was REMOVED "
                  "or SHORTENED." % n)
            print("             That is all this checked. A QUALIFIER is not detected:")
            print('             "never leaves your device" -> "...unless you opt in" passes.')
        return 0

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
    print("          On 2026-06-10 an agent found the Privacy Policy contradicting the")
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
        print("               python .shared/scripts/commitment_guard.py --install")
        print()
        print("             Until then the only way past is --no-verify, which switches off")
        print("             EVERY hook in this repo - so fix the install, not the commit.")
    return REFUSE


HOOK_BODY = """#!/bin/sh
# commitment_guard - installed by commitment_guard.py --install
# ⛔ commit-msg, NEVER pre-commit: pre-commit runs before the message exists, so the
#    documented ruling escape is unreachable from there. See the module docstring.
python "%s" "$1"
[ $? -eq %d ] && exit 1
true
"""


def install():
    """Put the guard on commit-msg in every repo under the workspace, and off pre-commit.

    ⛔ REMOVING THE OLD LINE IS PART OF THE INSTALL. A leftover pre-commit copy would refuse
    weakenings with no reachable escape while the commit-msg copy allows them - two halves
    disagreeing, which is exactly the state a single hook was chosen to avoid.
    """
    script = str((pathlib.Path(__file__)).resolve())
    body = HOOK_BODY % (script.replace("\\", "/"), REFUSE)
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
    return 0


def audit():
    n = 0
    # ⛔ A PROMISE IS NOT A MARKDOWN FILE. o10 measured this: the privacy policy, the terms
    # and the consumer-health-data page are .tsx, and a markdown-only glob reported "0
    # commitment regions" while 12 existed. The failure message NAMED those three files while
    # the glob structurally could not reach them.
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

    A = "<!-- commitment: voice data -->\nwe never store your voice data\n<!-- /commitment -->"
    print("commitment_guard selftest")
    t("identical text is not a weakening", not weakened(A, A))
    t("deleting the promise line is caught",
      any("REMOVED" in w for _l, w in weakened(A, A.replace("we never store your voice data",
                                                            ""))))
    t("removing the whole region is caught",
      any("entire commitment" in w for _l, w in weakened(A, "nothing here")))
    B = A.replace("we never store your voice data", "we never store your voice data forever")
    t("ADDING to a promise is allowed", not weakened(A, B))
    C = A.replace("we never store your voice data", "we never store your voice")
    t("SHORTENING a promise is caught, not just deleting",
      any("SHORTENED" in w for _l, w in weakened(A, C)))
    t("a doc with no commitment region is untouched", not weakened("plain doc", "plain doc2"))
    D = "\n".join(["<!-- commitment: wrapped -->", "see <a>the policy</a>", ".",
                   "we never store your voice data", "<!-- /commitment -->"])
    t("a punctuation-only line inside a region is not a promise (lone '.' from a JSX wrap)",
      not weakened(D, "a line above the region changed\n" + D))
    t("the real promise in that same region is still caught",
      any("SHORTENED" in w for _l, w in weakened(D, D.replace("your voice data", "your voice"))))
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
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
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
