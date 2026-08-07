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
> and **nothing can tell "finished" from "stopped".** The observation behind the whole design:
> *the shorter and more concise the exact deliverable, the more complete the answer.*
>
> ⭐ **A checklist does not fix this.** A checklist is read ONCE, at the start, by a context that
> then fills with the work itself; by item six the first item is a memory. In the incident that
> produced this skill, every section was **already named in a schema the orchestrator had
> written and could see.** Knowing the list was never the problem. Finishing it was.

## When to run it

- A decision doc has drifted - stale statuses, prose where entries should be, sections that
  have not been read in days.
- **Before handing a project back to a human.** An empty "needs your decision" section is an
  *assertion* that nothing does, and if that is false it is the most damaging thing the
  document can say - the human believes it and stops checking.
- At the end of a long working session, before the context that did the work is gone.
- Any time you are about to attempt a whole-document refresh in one pass.

## What it does

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
in from item two.** This is the founding observation turned into architecture rather than
advice: **the deliverable is small because the WORKER is small.**

## What a good run looks like

- Every section visited exactly once, in order, with a gate passed or a stated reason.
- At least one thing found that was **missing** rather than merely stale. If the sweep produced
  only status corrections, question whether step 2 was really run.
- An audit report that **names what it checked**. ⛔ A clean audit that did not name its
  evidence is worth nothing - it is indistinguishable from not having looked.
- Anything the audit finds either fixed, or filed as an entry with an owner. **Not narrated in
  chat and left there** - that is the failure the decision doc exists to remove.

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
