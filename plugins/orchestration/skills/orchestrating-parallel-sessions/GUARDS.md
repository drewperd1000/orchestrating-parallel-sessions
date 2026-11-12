# The guards

The scripts that refuse things. Every guard shipped in `scripts/` has a section here, and the publisher refuses to ship this page if one is missing. This page says what each one refuses, why it exists, and — the
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
those must NOT stop every commit in the workspace. A guard that blocks when it is broken can stop a whole workspace.

`orchdoc_precommit.py --check-hook --all` audits the chain **by counting invocations**, not by
looking at it. That is not fussiness:

⭐ **A hook can carry one `python` line and three `[ $? -eq 9 ] && exit 1` lines testing the exit
status of nothing.** It has a shebang, a python call and the right exit convention, so it looks
correct in isolation, and two thirds of the chain never run.
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
guard that fires on correct work gets switched off. So the guard fires on a *signature* instead: a stale
whole-file write deletes precisely the lines that arrived after the copy was taken, so the removed
set contains one commit's additions almost entirely. A targeted edit does not.

⚠️ **Scoped to code on purpose.** Across all file types it refuses correct work: a document entry rewritten in place removes its own previous text
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
  A guard can refuse its own commit message, test file or template for containing the string it
  forbids. **A guard matches text, and text that describes the
  forbidden thing contains it.** The author cannot see it, because at authoring time the
  description is obviously not an instance.
- **`dev_docs_index.py`** — a generated index. **If deleting the index file would lose
  information, it was never an index — it was a document pretending to be one.** Authored
  summaries live on the artifacts; the index is a view.
- **`knowledge_pages.py`** — generates the subject pages the router searches.

---

## `block_uncited_seed.py` — refuses a headless launch whose prompt cites nothing

**What it refuses.** A `claude -p` launch whose seed prompt carries no citation: no file path,
no entry id, no commit.

**The failure.** A worker launched with a seed that names no source has to reconstruct the
context from its own guesses, and it will. The output looks confident and is built on whatever
the model filled the gap with. The orchestrator then reviews prose rather than work.

**When it fires:** put the source in the seed. A path, an entry id, a commit - anything the
worker can open. If the task genuinely rests on nothing written down, that is worth knowing
before a worker spends a run on it.

---

## `block_retired_path.py` — refuses a command that uses a path or tool you retired

**What it refuses.** A command naming something the workspace has decommissioned.

**The failure.** Removing a mechanism and leaving its description is how a stale note is made.
A session reads the note, reaches for the tool, and gets a confusing error - or worse, a tool
half of which still works. Retiring something means the next session cannot accidentally use
it, and a vocabulary file is what makes that enforceable rather than remembered.

**When it fires:** read what replaced it. The refusal names the replacement, because a refusal
that only says "no" makes the reader guess.

---

## `block_browse_instead_of_route.py` — refuses reading the knowledge tree by hand

**What it refuses.** Opening a knowledge document directly when you have not asked the router
first.

**The failure.** Browsing finds what you already suspected exists. The router searches the
whole corpus, so it finds the document you did not know to look for - which is the case that
matters, because it is the one where you would otherwise conclude nothing exists and write a
second copy.

**When it fires:** run the router with the words you would have grepped for. It answers HIT,
PARTIAL, WEAK or VERIFIED MISS, and only the last is permission to create.

---

## `copy_guard.py` — finds an instruction to copy text kept somewhere else

**What it refuses.** Through `orchdoc.py`: an open entry, or new text for one, that tells the
reader to copy, paste, run or type something from a file or URL (`E-COPYBYLINK`). A payload
already shown in inline code, a link that is only there to look at, and a path naming a secret do
not count. It is a detector with no hook of its own, so `orchdoc.py check`, `add`, `note` and
`resolve` all ask the same question.

**The failure.** A reader already looking at a document was sent to a URL to fetch text they
could have been shown, and the URL returned 404.

**When it fires:** put the text in the entry, inside a fence or inline code. `orchdoc.py paste
--file` copies a file's text into the entry for you.

---

## `ingress_taint.py` — takes the shell away from a session that read an outsider's text

**What it refuses.** Every Bash call, for the rest of the session, once that session has read a
path or run a command you named as ingress. It does not read the command, because argument
patterns are documented as unreliable. Name your ingress paths in `INGRESS_TAINT_READ_RE` and
`INGRESS_TAINT_CMD_RE`; the file's opening comment lists every setting.

**The failure.** A session holding a shell, secrets and push access reads text a stranger wrote.
Telling it to ignore instructions in that text is a request, and this removes the capability.

**When it fires:** finish what you can with Read, Write and Edit, then send your orchestrator the
name of what you read and the exact command you still need, and stop. A fresh lane that never saw
the text runs it. No flag reopens the shell, on purpose.

---

## `git_guard.py` — keeps a test from writing your real repository's git config

**What it refuses.** Once imported, it removes the variables that redirect git (`GIT_DIR`,
`GIT_INDEX_FILE` and the like) and refuses a `git config` write, or a `git init`, that would reach
your real repository's config or your global one. The refusal is raised before git starts.

**The failure.** Git sets `GIT_DIR` for a hook, and a script a hook starts passes it on. A test
that makes a temporary repository and sets a user name in it then writes that name into the real
repository, because git ignores the folder it was started in when `GIT_DIR` is set.

**When it fires:** make the temporary repository through `sandbox_env.py`, which imports this
guard, and give each test its own folder.

---

## `block_whole_tree_grep.py` — holds a recursive search over the whole workspace once

**What it refuses.** The first recursive `grep` or `rg` aimed at the workspace root or your home
folder in a session. The message names the router command to run instead. Sending the same command
again passes, so the rare search that really needs the whole tree is never stuck.

**The failure.** A search across every worktree and dependency folder runs past the tool's time
limit and prints nothing, while the router answers the same question in seconds.

**When it fires:** run the router command the message shows, or narrow the search to one folder.

---

## The pattern underneath all of them

⭐ **An instrument answering about ITSELF, read as an answer about the world.** Every guard here is shaped to resist it:

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
