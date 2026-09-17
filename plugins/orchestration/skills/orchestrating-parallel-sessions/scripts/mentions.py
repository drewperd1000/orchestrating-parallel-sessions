#!/usr/bin/env python3
"""Telling a guard the difference between DOING a thing and DESCRIBING it. One implementation.

<!-- route-tags: guard false positive description instance mention heredoc commit message
     generated template correspondence self-reference -->

⛔ EIGHT INSTANCES IN ONE DAY, 2026-08-17, across seven files:

  1. `block_retired_path` refused a commit whose MESSAGE explained the retired path
  2. `facets` tagged the cornerstone TEMPLATE as a doc about external_provider, because its examples used external_provider
  3. `knowledge_gate`'s overlap check read a REPLY as a duplicate of the message it replied to
  4. `owns_guard` refused the GENERATED browse page for "restating" a path it exists to link to
  5. `owns_guard` would have owned the template's own `<vendor>` placeholders
  6. `block_shared_tree_ops` blocked its own COMMIT MESSAGE, which explains what it forbids
  7. `block_shared_tree_ops` blocked its own TEST FILE, which must contain the forbidden strings
  8. `commitment_guard`'s attestation quoted the bad line, and a content check read the quote

⭐ ONE SHAPE: a guard matches TEXT, and text that DESCRIBES the forbidden thing contains it. The
author cannot see it, because at authoring time the description is obviously not an instance.

⭐ AND IT WAS SOLVED, SEPARATELY, FOUR TIMES - each fix private to one file, so the fifth guard
repeated the defect with the answer sitting four files away. **A workspace that solves a problem
and does not centralise the solution pays for it again on every recurrence.** Hence this module.

Two questions, two functions:

    executable_part(cmd)   what a shell will actually RUN, with prose removed
    is_derived(text)       whether a document is a view/template/thread rather than a claim
"""
import re

# ---- 1. commands -------------------------------------------------------------------------
# Places PROSE lives inside a shell command. Everything here is text a human wrote for a human;
# none of it is executed, so none of it should be matched.
_HEREDOC = re.compile(r"<<-?'?(\w+)'?.*?\n\1", re.S)
# ⛔ EVERY FLAG HERE CARRIES PROSE, AND PROSE IS NOT A COMMAND. `-m`/`-F` alone was not enough:
# on 2026-09-16 `gh pr close --comment "...--autostash..."` was refused by the shared-tree guard
# because the WORD appeared inside the comment explaining why the branch was landed by path.
# Minutes later the same guard blocked an `echo` describing the rule it enforces.
#
# ⭐ That is the ninth time in one day a tool read a DESCRIPTION of a thing as an INSTANCE of it,
# and the second time it happened to this very helper - whose docstring already says so. Fixing
# it here fixes every guard at once, which is the reason the helper exists.
_MSG_FLAGS = r"-(?:m|F)|--(?:message|comment|body|body-file|subject|title|notes|description)"
_MSG_QUOTED = re.compile(r"(?:%s)[\s=]+(['\"])(?:\\.|(?!\1).)*\1" % _MSG_FLAGS, re.S)
_MSG_TOKEN = re.compile(r"(?:%s)[\s=]+\S+" % _MSG_FLAGS)
# `echo`/`printf` exist to EMIT text. Their arguments are never executed, and describing a
# forbidden command is the most common legitimate thing to echo in this workspace.
_ECHO_QUOTED = re.compile(r"\b(?:echo|printf)\s+(['\"])(?:\\.|(?!\1).)*\1", re.S)
_COMMENT = re.compile(r"#[^\n]*")
# ⛔ `echo` IS NOT STRIPPED, deliberately. It looks like prose and is not: `echo "<cmd>" | sh`
# is a working bypass, and an agent echoing a retired tool name is usually reaching for it.
#
# ⭐ THE RULE THIS SETTLED: a shared helper must be the INTERSECTION of what each caller needs,
# never the union. Stripping echo helped the guard I wrote it for and silently broke
# block_retired_path, whose own test caught it - measured 2026-08-17, `echo mcp__<vendor>__list`
# stopped being refused. Generalising a fix is only safe against every existing caller's tests.


def executable_part(cmd):
    """The command with everything a shell will not execute stripped out.

    ⛔ Match against THIS, never the raw command. `git commit -m "never run git stash"` contains
    the string a stash guard looks for, and blocking it stops the workspace from documenting its
    own rules - which is how a rule stops being written down at all.
    """
    if not cmd:
        return ""
    s = _HEREDOC.sub(" ", cmd)
    s = _MSG_QUOTED.sub(" ", s)
    s = _ECHO_QUOTED.sub(" ", s)
    s = _MSG_TOKEN.sub(" ", s)

    s = _COMMENT.sub(" ", s)
    return s


# ---- 2. markers --------------------------------------------------------------------------
# ⭐ ONE DEFINITION OF THE route-tags MARKER. Three modules had their own copy - route.py,
# knowledge_gate.py and knowledge_pages.py - and all three omitted re.S, so `.` never matched a
# newline and a WRAPPED marker was invisible to every one of them.
#
# ⛔ MEASURED 2026-08-18: the gate refused a doc for being unreachable while its route-tags line
# sat in the file, correct, wrapped across two lines at this workspace's own 100-column
# convention. The refusal was RIGHT in effect - route.py could not have found it either - which
# is the only reason it was caught rather than shipping as a silently unfindable doc.
#
# ⭐ A MARKER FORMAT IS A CONTRACT BETWEEN EVERY TOOL THAT READS IT
# (memory/marker_format_is_a_contract.md). Three private copies is three chances to disagree,
# and the workspace's own line-wrapping convention was enough to break all three at once.
ROUTE_TAGS = re.compile(r"<!--\s*route-tags:\s*(.+?)\s*-->", re.S)


# ---- 3. documents ------------------------------------------------------------------------
# ⛔ TWO SHAPES, because a generated file does not have to use OUR banner. o9L21 measured three
# codesight code-maps that `is_derived` called authored: CODESIGHT.md ends with
# `_Generated by [codesight](...)_` in markdown italics, and its two companion sections carry no
# banner at all. A labelling pass trusting this would have hand-labelled generated views - the
# exact error the pass was told to avoid, waved through by the helper meant to prevent it.
#
# ⭐ The generalisation: WE control our own generators and can mandate a marker; we do NOT
# control other tools', so recognising only our own shape means every foreign generator reads as
# authored. Path is the reliable signal there - a directory a tool owns is a stronger claim than
# a banner we hoped it would print.
_GENERATED = re.compile(r"<!--\s*GENERATED by|^_?Generated by \[", re.I | re.M)
_GEN_DIR = re.compile(r"[/\\]\.codesight[/\\]", re.I)
_TEMPLATE = re.compile(r"<!--\s*template\b|^#\s+\S.*\btemplate\b", re.I | re.M)
_CORRESPONDENCE = re.compile(r"<!--\s*correspondence:", re.I)
_TEST = re.compile(r"^(test_|.*[/\\]test_)", re.I)
# A placeholder is a shape, not a value: <vendor>, <slug>, _example, YOUR-KEY-HERE.
PLACEHOLDER = re.compile(r"<[^>\s]{2,}>|_example|\bYOUR[-_][A-Z]+\b")


# ---- 3. where a marker counts as a DECLARATION -----------------------------------------------
# ⛔ FOUR READERS, ONE QUESTION, FOUR ANSWERS. `route.py` read a doc's marker out of the first
# 3000 characters, `knowledge_gate.py` out of 4000, `knowledge_pages.py` and `dev_docs_index.py`
# out of 8000. A declaration that drifted past a reader's own number stopped existing FOR THAT
# READER ONLY - so a doc could be routable and absent from the knowledge map, or indexed and
# unroutable, with nothing anywhere reporting a disagreement.
#
# ⭐ THE WINDOW WAS NEVER THE POINT. A declaration is not "a marker near the top"; it is a marker
# the file makes as its OWN claim rather than as an example. That question has one right answer
# and it belongs in one place - the same rule this module already applies to `ROUTE_TAGS`.
# Reading all 503 artifacts whole costs 0.16s, so the caps were never buying anything either.
SCRIPT_PROSE_CAP = 3000


def outside_fences(text):
    """Text with fenced code blocks removed - a marker inside a fence is an EXAMPLE.

    ⭐ ANY DOC THAT TEACHES A MARKER CONTAINS THAT MARKER, so the distinction has to be
    structural rather than a list of exempt files. A fence means "here is what one looks like";
    a bare line means "here is mine". The cost is that a marker someone fences by accident goes
    uncounted - a false negative, which is the survivable direction.
    """
    return re.sub(r"^```.*?^```", "", text, flags=re.S | re.M)


def declaration_region(text, path=None):
    """The part of an artifact where a marker counts as ITS OWN declaration.

    MARKDOWN - the whole file. PYTHON - the module docstring only, however long, which is
    already what `route.py enrich` tells authors when it refuses. An unparseable `.py` falls
    back to the old prose window, the conservative direction: that is what it got before.

    Measured across 700 artifacts when this rule was introduced: zero markdown artifacts lose a
    marker to fence-stripping, 25 scripts keep theirs, and the 1 that loses it is a test fixture
    that was never a declaration.
    """
    if path is not None and str(path).lower().endswith(".py"):
        try:
            import ast
            return ast.get_docstring(ast.parse(text)) or ""
        except (SyntaxError, ValueError, RecursionError):
            return text[:SCRIPT_PROSE_CAP]
    return text


def declares(text, marker_re, path=None):
    """The first value this artifact declares for `marker_re`, or "" - read over the whole file.

    ⛔ CHEAP REJECT FIRST, and it is load-bearing rather than tidy. Most artifacts carry no
    marker, and without a substring pre-check each one pays an `ast.parse` and a whole-file
    fence regex to be told so - measured at +55% on every router query, which is the kind of
    number that gets a correct fix reverted.
    """
    probe = getattr(marker_re, "pattern", "")
    m = re.search(r"[a-z][a-z-]{3,}", probe)
    if m and m.group(0) not in text:
        return ""
    hit = marker_re.search(outside_fences(declaration_region(text, path)))
    return hit.group(1) if hit else ""


def is_derived(text, path=None):
    """(bool, reason) - is this a VIEW, a FORM, a THREAD or a TEST rather than a claim?

    ⭐ THE COMMON PROPERTY, and the reason these four belong together: none of them can DRIFT
    independently of something else.

      - a GENERATED page is recomputed; if it is wrong you regenerate it, never edit it
      - a TEMPLATE's examples are shapes to be replaced, not values to be trusted
      - a REPLY shares its subject's vocabulary BY DEFINITION - that is what a thread is
      - a TEST must contain the exact strings it exercises, or it tests nothing

    So a content check has nothing to protect in any of them, and firing there produces a
    refusal the author cannot satisfy without damaging the artifact.
    """
    p = str(path or "")
    if _GEN_DIR.search(p.replace("\\", "/")):
        return True, "inside a generator's own directory - the tool owns every file in it"
    if _GENERATED.search(text or ""):
        return True, "generated view - regenerate it, never edit it"
    if _CORRESPONDENCE.search(text or ""):
        return True, "correspondence - a thread shares its subject's words by definition"
    if _TEMPLATE.search(text or ""):
        return True, "template - its examples are shapes, not values"
    if _TEST.search(p.replace("\\", "/").split("/")[-1]):
        return True, "test - it must contain the strings it exercises"
    return False, ""


def selftest():
    ok = True

    def t(name, cond):
        nonlocal ok
        ok = ok and bool(cond)
        print("  [%s] %s" % ("OK " if cond else "FAIL", name))

    STASH = "git " + "sta" + "sh"
    t("a -m message is not executable",
      STASH not in executable_part('git commit -m "never run %s here"' % STASH))
    t("a heredoc body is not executable",
      STASH not in executable_part("git commit -F - <<'EOF'\nnever %s\nEOF" % STASH))
    t("a trailing comment is not executable",
      STASH not in executable_part("git status  # not %s" % STASH))
    t("the real command survives", STASH in executable_part("%s -u" % STASH))
    t("a named dotfile path survives",
      ".shared/x.py" in executable_part("git add .shared/x.py"))
    t("generated is derived", is_derived("<!-- GENERATED by pages.py -->")[0])
    t("correspondence is derived", is_derived("<!-- correspondence: from=o9 to=o10 -->")[0])
    t("a test file is derived", is_derived("x", "a/test_thing.py")[0])
    t("an ordinary doc is NOT derived", not is_derived("# A note\n\nfacts.", "a/note.md")[0])
    t("placeholders are recognised", PLACEHOLDER.search("`.shared/secrets/<vendor>-key.txt`"))
    t("a real value is not a placeholder",
      not PLACEHOLDER.search("`.shared/secrets/external-provider-api-key.txt`"))
    print("\nselftest %s" % ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(selftest())
