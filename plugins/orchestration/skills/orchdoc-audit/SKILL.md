---
name: orchdoc-audit
description: Use to run a FULL, forced, section-by-section refresh of an orchestrator decision doc, followed by an independent audit that is deliberately denied the updater's reasoning. Invoke when a decision doc has drifted, before handing a project back to a human, at the end of a long working session, or any time "refresh the doc" would otherwise be attempted in one pass - which never completes. Also use when you need to know whether what a doc claims is finished actually is.
---

# orchdoc-audit

> **Companion to `orchestrating-parallel-sessions`, and not standalone.** It operates on an
> Orchestrator Decision Doc, which only exists if that skill is in use, and it drives that
> skill's tools rather than shipping its own - two copies of `orchdoc.py` in one repo would be
> exactly the duplicated-truth defect this tooling exists to remove. Paths below are written
> relative to the plugin root; `$SK` is
> `skills/orchestrating-parallel-sessions/scripts`.

A full diagnostic pass over one Orchestrator Decision Doc: **every section forced, one at a
time, with a mechanical gate between them - then an independent audit by a worker that never
sees what the updater said.**

> ⛔ **This exists because "refresh the OrchDoc" is too large a task to finish.** It ends early,
> and **nothing can tell "finished" from "stopped".** The smaller the exact deliverable, the more complete the answer.
>
> ⭐ **A checklist does not fix this.** A checklist is read ONCE, at the start, by a context that
> then fills with the work itself; by item six the first item is a memory. Knowing the list is not the problem. Finishing it is.

## When to run it

- A decision doc has drifted - stale statuses, prose where entries should be, sections that
  have not been read in days.
- **Before handing a project back to a human.** An empty "needs your decision" section is an
  *assertion* that nothing does, and if that is false it is the most damaging thing the
  document can say - the human believes it and stops checking.
- At the end of a long working session, before the context that did the work is gone.
- Any time you are about to attempt a whole-document refresh in one pass.

## The commands an audit uses

`python $SK/orchdoc.py --help` is the whole list and it is the answer to "does this exist".
These are the ones an audit reaches for:

| command | what it is for |
|---|---|
| `check --doc <id>` | the invariants. Add `--strict` to see WHERE an advisory fired - the default prints `W-FOO x14` and no line numbers |
| `review --doc <id>` | walks EVERY section and forces the completeness question. `check` cannot see an absence; this asks about one |
| `freshness --doc <id>` | is the working-tree copy equal to the canonical ref? Run it FIRST - auditing a copy that never landed audits nothing |
| `plate --doc <id>` | regenerate the human-facing index. Never hand-edit the block |
| `strike` / `reorder --doc <id> --commit` | the deterministic fixers |
| `restamp <ID> --doc <id> --because "<what moved>"` | record a re-review, with the bar applied at the write |
| `resolve <ID> --doc <id> --ruling "<what was decided, by whom>"` | flip a Status in place |
| `archive --doc <id> --commit` | move finished entries out of the live sections |
| `priority <ID> --doc <id>` | propose that the human look at an entry first. It always writes the PROPOSED form; the human's own bare mark outranks it |
| `commit --doc <id> -m "<msg>" --commit` | the only supported way a doc reaches `main`. Without `--commit` it is a dry run, which is how to see the landing-only gates |
| `selftest` | the built-in fixtures. Run it when a finding looks wrong before assuming the document is |

⛔ **`restamp` holds the attestation to a bar, and the bar is about the WORLD rather than the
entry.** `--because` must say what MOVED and why the entry survives it. "Re-read, unchanged" and
anything under 40 characters is refused as `E-RUBBERSTAMP`. `--by` is optional and defaults to
the document's owner.

## What it does

**Phase 0 - the mechanical pass. Run the fixers before spending any judgment.**

```bash
python $SK/orchdoc.py strike  --doc <id> --commit    # settled sub-items -> `- [x]`
python $SK/orchdoc.py reorder --doc <id> --commit    # numbers back into order
python $SK/orchdoc.py check   --doc <id>             # what is left is judgement
```

Everything these two fix is deterministic, so **no human and no auditor should spend attention
on it.** Doing this first means Phase 1 reads a document whose form is already right.

⭐ **THE FORM OF A SETTLED SUB-ITEM IS A CHECKED CHECKBOX AND NOTHING ELSE:**

```markdown
- [x] the thing that is settled
```

That renders with a green tick, **grey text and strike-through, natively** - all three at once,
from four characters. ⛔ **Do NOT add `<span style="color:…">` or `~~…~~`.** Inline HTML is not
rendered in this viewer: it appears as *visible literal text* in the middle of the sentence.
**Anything about rendering has to be seen rendered.**

And the rules the gate now enforces, so none of them can be closed past:

| code | the rule |
|---|---|
| `E-SETTLEDNOTSTRUCK` | a settled sub-item in a LIVE entry is `- [x]` |
| `E-ALLSUBSDONE` | all sub-items done -> close it and move it to §99 **whole**; never leave it live |
| `E-IDSHAPE` | ids are PREFIX+NUMBER with the summary in the title - `D9907 - pick a layout`, not `D-PAUSE` |
| `E-IDORDER` | numbers run in order within a section, so a reader stops when they arrive |

### The findings an auditor should expect, and what a clean run is

⭐ **Read the code before you read the line it points at.** Each names a different kind of
wrongness, and `E-STALEPROSE`, `W-STALLED` and `W-DEADCITE` are about whether the entry is
still TRUE rather than about how it is written - the kind nobody catches by looking at the page.

| code | what it refuses | blocking? |
|---|---|---|
| `E-STALEPROSE` | an entry resting on facts that moved after it was last reviewed. The remedy is `restamp`, naming what moved - never a reword | yes |
| `E-RUBBERSTAMP` | an attestation that says "still current", or one under 40 characters | yes |
| `E-PLATEDRIFT` | the rendered §2 index is not what regeneration would produce, so somebody hand-edited generated content | yes |
| `E-HIDDENENTRY` | an archived heading that no parser can reach. Almost always inside a ``` fence | yes |
| `E-DAYONLYOPENED` | an entry this landing ADDS whose `**Opened:**` carries a date and no time, so its age cannot be ordered against a sibling opened the same day | yes, at `commit` |
| `W-NOPROV` | a CLOSED entry with an open sub-item that schedules future work and no `**Provenance:**` saying why the task outlives the ruling | yes |
| `W-STALLED` | an OPEN `W` or `T` entry 14 or more days old that names nothing it waits on | no, advisory |
| `W-DEADCITE` | a `**Depends:**` line that names a file and parses as no citation at all, so the entry can never restale | no, advisory |

⛔ **`W-STALLED` is the one an auditor is most likely to wave through, and a review does NOT
exempt it.** Its test is a disjunction: the entry is either MOVING (something was acted on) or
WAITING on something it names. Re-reading an entry is the input to that decision, not an
outcome of it.

⛔ **`W-DEADCITE` is a defect the tool taught.** `**Depends:** path/to/doc.md#T9907` with no
backticks parses as no citation, so the entry satisfies the has-a-dependency check on the
strength of its text and then reads as permanently current. Backticks go round the PATH, not
round the line: **Depends:** `path/to/doc.md#T9907`.

⚠️ **`check` is not the whole gate, and an auditor who only runs `check` will miss two
classes.** Landing through `orchdoc.py commit` runs further gates over what the landing ADDS -
gate 1b on date-only rulings, gate 1c on date-only `**Opened:**` fields, and gate 5 on bold.
Gate 5 refuses a passage that is 80% or more bold, or that scatters bold three or more times
without opening with it. Run a dry-run `commit` during the audit to see them.

⭐ **A clean run is `BLOCKING: none` plus a count of what was examined, and the second half is
the part to report.** `check` on a document with no entries prints `BLOCKING: none` too, and a
run that examined nothing looks exactly like a run that found nothing wrong. The tools already
print it - `examined 1 doc(s)` - so carry that number into the audit report.

**Phase 1 - forced, one section at a time.**

```bash
python $SK/orchdoc_sweep.py start --doc <id>
python $SK/orchdoc_sweep.py next  --doc <id>     # prints ONE section, and nothing else
```

`next` deliberately shows only the current section - not the list, not what is coming. **A step
that can see the next step gets planned around instead of finished.**

Each step asks **both halves**, and the second is the one that gets dropped:

1. **Is what is HERE still true?** Check every entry's status against reality - the commits,
   the branches, the artifact - not against what the entry says about itself.
2. ⭐ **Is anything MISSING?** Look in your own recent work, the chat, your lane reports, other
   sections' prose. **This half is where the serious misses live.** A section can be perfectly
   self-consistent and still be silent about the thing that matters most.

Then close the step:

```bash
python $SK/orchdoc_sweep.py done --doc <id> --report "<what changed, what you verified>"
```

⛔ **The completion token is EARNED, not asserted.** *"I finished §2.1"* is a claim; *"no closed
entry remains in §2.1 and the file changed since this step opened"* is a measurement. The gate
refuses and names what is wrong. **Without this the sweep is a to-do list grading its own
homework, which is the thing that already failed.**

A section that legitimately needs nothing is a valid outcome - but it must say so:

```bash
python $SK/orchdoc_sweep.py done --doc <id> --no-change --because "<how you established that>"
```

⚠️ *"Nothing needed"* and *"I did not look"* are indistinguishable from outside. **The sentence
is what tells them apart, and the auditor reads it.** `--no-change` waives *"the file did not
change"* and nothing else - a section holding a closed item, or forty lines of unparseable
prose, is refused regardless of the reason given.

**Phase 2 - the independent audit.**

```bash
python $SK/orchdoc_sweep.py audit --doc <id> [--lane]
```

⭐ **The seed carries the BASELINE and the COMMANDS, and deliberately WITHHOLDS every step
report.** An auditor shown the updater's reasoning audits the reasoning - and agreeing with a
coherent account is what reading one does. An auditor given only the evidence has to derive the
answer, and **only that can find what the updater never thought to look for.**

It also hands over the commands rather than the output, so the auditor gathers its own evidence
and does not inherit the updater's gathering bugs.

⛔ **If the commit range comes back EMPTY, that is a finding about the baseline - not a verdict
about the document.** An audit with no input is a rubber stamp that cost a worker. Establish
which case you are in before concluding anything.

## The strongest form: one fresh worker per section

```bash
python $SK/orchdoc_sweep.py next --doc <id> --lane     # prints a ready headless command
```

⭐ **A fresh context cannot drift, cannot tire at item six, and cannot carry a wrong assumption
in from item two.** The deliverable is small because the WORKER is small.

## Read it the way the human reads it - the OrchDoc reader

**`render_orchdoc_reader.py` serves the live docs at `http://127.0.0.1:8787/`**, and every entry
has a stable deep link at `http://127.0.0.1:8787/#o<N>/<ID>`. That anchor is how an audit points
at an item, in its report and in chat. An id on its own costs the reader a search every time.

⛔ **The page hides two things on purpose.** An auditor who does not know which will read a
fold as an absence and file it as a finding.

- **Attestation blocks collapse on every entry, with the count left in the summary.** "17
  attestations" still says the entry was reviewed seventeen times; only the text is behind the
  fold. An audit that reports an entry as never reviewed after reading the page has read a fold.
- **An acknowledged instruction collapses and an unacknowledged one stays open.** The test is
  the ack the owning orchestrator writes at its own turn boundary through `orchdoc.py note
  --ack`, so it is evidence a session read the instruction rather than a promise made when Send
  was pressed.

⚠️ **The ordering sentence says two different things and they are not interchangeable.** "N
entries are waiting on it" counts declared `**Depends:**` edges; "named by N" counts mentions.
An audit that cites how
central an entry is has to say which of the two it means.

## What a good run looks like

- Every section visited exactly once, in order, with a gate passed or a stated reason.
- At least one thing found that was **missing** rather than merely stale. If the sweep produced
  only status corrections, question whether step 2 was really run.
- An audit report that **names what it checked**. ⛔ A clean audit that did not name its
  evidence is worth nothing - it is indistinguishable from not having looked.
- Anything the audit finds either fixed, or filed as an entry with an owner. **Not narrated in
  chat and left there** - that is the failure the decision doc exists to remove.

## Closing the turn the audit ran in

⛔ **An audit generates work, and the reflex is to write the work down instead of doing it.** `IMMEDIATELY-NOT-EMPTY`
refuses a turn that parks work under "Immediately" rather than doing it in that turn.
`OTHERS-NOT-NOTIFIED` refuses one that lists another group's item without sending it - and a
list looks exactly like routing, which is why the section demands the send and not the name.

## What a sweep finds

Judge a sweep by how much startable work it finds, not by how tidy the document looks. A startable
entry and an abandoned one are the same text, so no linter finds that.

### The one instruction that finds the most

**Open the file the entry names, every time, and do not decide from the entry's description of
the world.** The orchestrator
that wrote an entry is the reader least able to check it, because it holds the same model of
the world the entry encodes.

### The checklist `[DERIVED]`

**When you write a work entry:**

- [ ] Its first sub-item is *open the artifact*, not *read the log about it*.
- [ ] It names a path, and you opened that path today.
- [ ] It carries a `Done-when:` a script could test, or it says plainly that it closes on judgement and whose.
- [ ] Any blocker it names carries what the blocker is, where that lives, when it was last verified true, and by what command.
- [ ] A blocker shared with other entries is its OWN entry, and they all `Depends:` on it. Four copies of one gate go stale one at a time.
- [ ] If it mirrors another group's entry, one side holds the record and the other is a bare pointer carrying no state about the first.
- [ ] It has checkboxes. Zero checkboxes means progress nobody can see without reading the body.
- [ ] Its Owner names who holds it. If a line inside it needs the human, that line is its own §2 entry owned by the human.

**When you attest one:**

- [ ] Name the fact you re-derived and the command that derived it. "Re-read, unchanged" is the failing form.
- [ ] Glob the directory of every file it names for newer siblings.
- [ ] If it says it is blocked on something, re-verify that thing. A blocker is the likeliest fact to have moved, because nobody owns re-checking it.
- [ ] If it cites another group's entry, open that entry. A citation's silence is not agreement.
- [ ] A figure you quote is a location and a command, never a value. A value dies at the next change, and the attestation then proves the opposite of what it says.

**When a ruling lands:**

- [ ] It is not RESOLVED until it is in the artifact the workers actually read, cited as `landed at <file>:<line>`. A guard section is not an artifact a worker reads.
- [ ] Re-check every work entry whose sub-items the ruling touches.

**When you name a check you did not build:**

- [ ] Do not. File it as an entry with a `Done-when:`, or delete the sentence. A named-and-unbuilt check reads as coverage to the next person.

## Reflexes

- ⛔ **Run `review` before `check`, not instead of it.** Every invariant in the linter is
  triggered BY AN ENTRY, so **a section with no entries generates NO findings** - the emptier a
  doc gets, the quieter the tool gets, and total emptiness is perfectly silent. `check` cannot
  see an absence; `review` asks about one.
- ⛔ **Report how many things you EXAMINED, not only what you found.** "0 examined" and "N
  examined, all clean" are different facts that print identically unless you make them differ.
- ⚠️ **Do not sweep a doc you do not own.** Propose, get the owner's agreement, then run it.
  The tooling refuses by default (`$ORCHDOC_ME`, `--not-mine` for an agreed pass).
- ⭐ **When the audit and the sweep disagree, the audit is usually right** - it read the
  document as a reader, and the sweep read it as its author.
- ⛔ **Run the mechanical fixers FIRST, and never hand-format what a fixer owns.** Three marks
  per line by hand is the task that gets done for two entries and abandoned. `strike` and `reorder` are idempotent and verify their own output.
- ⛔ **A rule with no enforcement point decays into an advisory nobody reads.** The gate in `orchdoc_sweep.py done` is where a form rule becomes real:
  a step cannot be closed while its own section violates one.
- ⚠️ **Advisory findings have line numbers; the default output hides them.** `check` prints
  `W-FOO x14` and stops. Use `check --doc <id> --strict` to see WHERE. A count you cannot act
  on is how a detected defect survives.
- ⛔ **When a check fires on text that FOLLOWS the rules, the check is wrong - not the text.**
  **A false positive that lands on correct behaviour costs you the
  behaviour**, and invisibly, because the people penalised are the ones doing it right and the
  advice they receive tells them to do more of what triggered the flag. Ask which DIRECTION a
  check is wrong in before fixing the symptom.
- ⭐ **A rule that recognises only the TIDY shape of a violation misses the messy one - and the
  messy one is what accumulates.** Tidy violations get
  fixed by whoever made them; messy ones survive precisely because they do not look like the
  thing the rule describes.
- ⭐ **`E-LOOSEINPARENT` is not a tidiness check.** A loose bullet belongs to no entry, and every
  invariant here is triggered BY an entry - so text in a container body is exempt from staleness,
  status, archive and the sweep. **It rots there while still looking like part of the plate.**
