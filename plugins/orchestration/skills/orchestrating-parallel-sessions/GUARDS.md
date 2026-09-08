# The guards

Ten scripts that refuse things. This page says what each one refuses, why it exists, and — the
part that matters if you are an agent about to trip one — **what the refusal is telling you to
do instead.**

Every one of them was written after a real failure. None is a style preference.

---

## How they run

Three of them are a `pre-commit` chain. `orchdoc_precommit.py --install-all` writes the chain
into the workspace repo and into every sibling repo beside it:

```
python knowledge_gate.py --check          # exit 9 refuses
python owns_guard.py --check              # exit 9 refuses
python stale_overwrite_guard.py "$TOP"    # exit 9 refuses
```

⛔ **Only exit code 9 blocks.** A crash, an import error or a git hiccup exits non-zero too, and
those must NOT stop every commit in the workspace. A guard that blocks when it is broken took a
whole workspace down for an afternoon; the convention exists because of that day.

`orchdoc_precommit.py --check-hook --all` audits the chain **by counting invocations**, not by
looking at it. That is not fussiness:

⭐ **Seventeen repos once carried a hook with one `python` line and three `[ $? -eq 9 ] && exit 1`
lines testing the exit status of nothing.** Each hook had a shebang, a python call and the right
exit convention, so each looked correct in isolation. Two thirds of the chain had never run.
**A guard that is installed and not running reads, from every direction, like a guard that is
running** — so the audit counts what the shell will execute and compares it to the template.

The rest are invoked directly or by a `PreToolUse` hook.

---

## `stale_overwrite_guard.py` — refuses a commit that erases an earlier commit's work

**What it refuses.** A staged file whose *removed* lines reconstruct, nearly in full, the
*additions* of one identifiable earlier commit — where the current commit message does not say it
is undoing anything.

**The failure.** One session read a file into its working copy. Another session then landed a
change to that file. An hour later the first session wrote its whole copy back. Git reported **no
conflict**, correctly — overwriting a file is not merging one. The second session's entire change
left the branch, and `git log` still listed their commit, in order, with its message. Only
reading the file content showed it gone.

⭐ **Every reflex anyone actually has operates on history** — read the log, check for conflicts,
read `git status`. The loss lived in content.

**Why this rule and not "refuse any deletion".** Deleting lines is most of what editing is, and a
guard that fires on correct work gets switched off. One check in this system accumulated 173
recorded overrides doing exactly that. So the guard fires on a *signature* instead: a stale
whole-file write deletes precisely the lines that arrived after the copy was taken, so the removed
set contains one commit's additions almost entirely. A targeted edit does not.

**Measured before shipping.** On the real incident the destroying commit scored **0.88** against
the commit it erased; all 29 other commits touching that file scored **0.00**. Over 250 real
commits, restricted to code, it refuses **one** — the real one.

⚠️ **Scoped to code on purpose.** Across all file types it would have refused 12 of those 250, and
**eleven were correct work**: a document entry rewritten in place removes its own previous text
wholesale, which is the same shape. Prose is guarded by `orchdoc.py commit`'s gate 1 instead,
which understands rewrites.

**When it fires:** `git fetch && git rebase`, then re-apply your edit **to the current file**
rather than re-writing your copy. Read `git show <sha>` first — the message names the commit you
would be undoing. A deliberate undo is `git revert`, which says so in the history.

---

## `owns_guard.py` — refuses a document that restates a fact another document owns

**What it refuses.** A commit whose staged prose repeats a fact declared as owned somewhere else —
a credential path, a deploy target, a vendor endpoint.

**The failure.** The same fact written in four places, three of which went stale, and readers
believing whichever they opened first. **A stale copy does not merely fail to inform; it
misinforms, with the authority of a document.**

**When it fires:** link to the owner rather than restating it. If the owner is wrong, fix the
owner — that is the whole point of there being one.

---

## `knowledge_gate.py` — refuses a new document about a subject that already has one

**What it refuses.** Creating a doc on a subject the corpus already covers, without reading what
exists.

**The failure.** A session searched for a vendor's process material, found none, reported that
none existed, and hand-wrote guidance into four work orders. Two runbooks were sitting at the
workspace root the whole time, and one carried a ruling the new guidance contradicted.

⛔ **"I grepped and found nothing" is not evidence of absence.** `route.py` is the other half of
this: it prints what it examined and what it deliberately does not cover, so a miss is a receipt
rather than an impression.

**When it fires:** extend the existing artifact. Two documents on one subject is how the first one
stops being true.

---

## `commitment_guard.py` — refuses a commit message that claims work it did not do

**What it refuses.** A message naming an intent that the diff does not contain.

⭐ **It is a `commit-msg` hook, never `pre-commit`,** and that is load-bearing: `pre-commit` runs
before the message exists, so the documented escape would be unreachable from there.

---

## `block_python_heredoc.py` — refuses Python source inlined in a shell heredoc

**What it refuses.** `python - <<'PY' … PY` where the source contains backslashes.

**The failure.** Inlining source puts two escaping layers in front of one string and only one gets
reasoned about. In a single session this produced a literal `0x08` byte from `\b`, a
`unicodeescape` SyntaxError from a Windows path, and an assertion comparing two strings that
differed only in escaping. Each was silent or misleading.

**When it fires:** write the script to a file and run it. Zero escaping layers, it fails on a
syntax error before executing anything, and it survives for debugging.

---

## `route.py` — the thing to run before writing "there is no X"

⛔ **The trigger is the SENTENCE, not the situation.** The moment you are about to write *"there
is no…"*, *"we don't have…"*, *"nothing exists for…"*, or you are about to CREATE something
because you believe nothing covers it — run it first.

Four answers: **HIT** (read it), **PARTIAL** (extend that, do not create a second), **WEAK**
(something adjacent exists — read it before writing), **VERIFIED MISS** (zero matches across an
enumerated corpus; now you may create).

It prints the corpus it examined **and what it deliberately does not cover**, so absence is
evidenced rather than asserted.

---

## `corpus.py`, `mentions.py`, `dev_docs_index.py`, `knowledge_pages.py`

Supporting machinery, each carrying one lesson worth reading in the source:

- **`corpus.py`** — what counts as searchable, and why a rule written as a NAME match (`name ==
  "CLAUDE.md"`) hides files a rule written as a LOCATION would have found. It ships a
  `SCOPE_BOUNDS` table declaring what it does not reach, because **a declared exclusion is a
  decision and an undeclared one is a gap wearing a decision's clothes.**
- **`mentions.py`** — one implementation of *"is this text DOING the thing or DESCRIBING it?"*.
  Eight guards in one day refused their own commit messages, test files and templates for
  containing the string they forbid. **A guard matches text, and text that describes the
  forbidden thing contains it.** The author cannot see it, because at authoring time the
  description is obviously not an instance.
- **`dev_docs_index.py`** — a generated index. **If deleting the index file would lose
  information, it was never an index — it was a document pretending to be one.** Authored
  summaries live on the artifacts; the index is a view.
- **`knowledge_pages.py`** — generates the subject pages the router searches.

---

## The pattern underneath all of them

⭐ **An instrument answering about ITSELF, read as an answer about the world.** It appeared eight
times in one week and every guard here is shaped to resist it:

- a publisher checking its output against its own term list, where a term missing from both the
  list and the substitutions is invisible and the output reads clean
- a health check reporting `Connected` while every call it made returned `Unauthorized`
- a staleness check returning "nothing found" both for *"I looked and there is nothing"* and for
  *"I could not look"* — so a run that never happened looked like a run that passed
- a search reporting no results as though it had established absence

**The defence is always the same: verify with an instrument that does not share the subject's
assumptions.** A publisher's cleanliness is checked by a scan built from the filesystem, not from
the publisher. A guard's false-alarm rate is measured by replaying real history, not by the test
cases its author imagined — a harness written from the same model as the code can only agree
with it.
