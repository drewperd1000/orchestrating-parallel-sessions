---
name: optimize-and-automate-claude-md-files
description: Use when a CLAUDE.md file (the global ~/.claude/CLAUDE.md, a project or repo CLAUDE.md, or a mirrored copy of either) is too large or is being trimmed, cut, split, reworded or audited; when deciding whether a rule should stay in CLAUDE.md or move to a hook, script, skill or note; or when, after a CLAUDE.md edit, sessions ignore a rule or ask before doing something the file allowed.
---

# Optimize and automate CLAUDE.md files

<!-- prose-only: whether a section is needed in every task, and where a moved rule will be seen, are judgments no parser can make. This skill ships no script, so the snapshot, ledger and loss checks it describes are steps for you, or for a script you write -->

## Overview

A CLAUDE.md loads into every session. A line stays only if every task needs it, or if nothing else can make sure an agent sees it at the moment it applies. Everything else moves to the hook, script, skill, note or narrower CLAUDE.md that is in front of the agent at that moment. Every removed sentence gets a ledger row saying where it went, and a fresh session must still answer what the old file answered.

"The owner" is whoever owns the file and approves changes to it. Commands, file layout and hook-building steps: `reference.md` in this folder.

## What a hook can and cannot do

- **Text a hook adds without refusing the call reaches the model with the tool's result, after the call has run.** This holds for PreToolUse hooks, including a hook that looks up a stored reminder and prints it. That is soon enough for a rule about a file's content, which the agent can still fix before it is committed or deployed. It is too late for a call that does the harm itself (a push, a deletion, a send, a launch); only a hook that refuses the call can stop that.
- **A refusing hook can enforce a prohibition, but no hook can grant a permission.** The agent decides whether to ask the owner before its first tool call. So a permission to act without asking, an exception to a rule, when to ask the owner, and what a reply must contain stay in the file. Without the permission sentence the agent asks, even when a hook would have run the permission's checks.
- **A hook counts only when `settings.json` runs it,** and a line is ENFORCED only when that hook is in block mode at apply time. In block mode the hook refuses the call; in warn mode it lets the call run and only logs what it would have refused.
- **What each event sees:** SessionStart runs a command but cannot call an MCP tool; UserPromptSubmit sees the prompt; PreToolUse sees the tool name, path, command and content, and can refuse the call; PostToolUse sees the output; Stop sees the final reply and can refuse it once; the git pre-commit hook sees staged files. A step every session must run becomes a SessionStart hook, not a line.

## The questions, per section and then per sentence

1. **Can a hook or script enforce it?** Fully: the line leaves once that hook runs in block mode (ENFORCED). Partly: enforce that part and ask the rest. Only a prohibition can be ENFORCED. A permission or an exception stays in the file even when a hook refuses every case it does not allow, because only the sentence tells the agent it need not ask.
2. **Could an agent get this wrong without the line?** No, or it duplicates a line that stays: REMOVED, naming the copy that stays. Read that copy first: it must hold the same condition and the same permission.
3. **Does it apply only at one moment or task?** MOVE it to what is opened at that moment, if question 6 passes.
4. **Which tasks need it?** Some: MOVE it to the narrowest CLAUDE.md level that covers all of them.
5. **Is it a reason or an origin story?** The rule stays; the story moves to a note named in one clause.
6. **If moved, will something show it at its moment every time?** That means a hook that refuses the call, a SessionStart hook, a narrower CLAUDE.md, or a skill or note that a hook opens or names at that moment. A note or skill that nothing opens counts as deleted. If nothing shows it, it stays, cut to its core, with a pointer.
7. **Per sentence: does it change what an agent does?** If not, remove it: restatement, emphasis, morals. Keep conditions, exceptions, formats, commands, names and facts. When unsure, keep it.

## The plan comes first

Give the owner these five parts, in this order, and change nothing until the owner approves:

1. **A table with one row per `##` section,** split into more rows where a section's sentences get different plans. Columns: section | bytes | needed in every task? | needed before the first tool call (a permission, an exception, when to ask, a reply format)? | when it applies, and which hook event sees that moment | what shows it at that moment (question 6), existing (E) or new (N) | plan: KEEP, REMOVE, MOVE, REPLACE or ENFORCED | bytes left. A yes in the "before the first tool call" column means KEEP. A "what shows it" cell that names only a note, a skill, a review, or the agent finding it fails question 6, and that row is KEEP.
2. **Wrong facts found.** Check each fact a section states against the code or the live system as you read it. One earlier trim of three files found 17.
3. **Probe questions, each with its expected answer:** one for every permission and exception in the sections being changed, and one or two per batch for what the changed lines decide: a command, a name, a condition.
4. **The apply steps, numbered, from the batch loop below:** destinations first, snapshot, ledger, apply, checks, probes, and the revert of any batch whose check or probe fails.
5. **What needs the owner's decision,** each with a recommendation. When the plan does not reach a size the owner asked for, give the size it reaches and the needed rules that the owner's number would cost.

Before keeping an "every session" instruction, count in transcripts how often sessions follow it. In one workspace, a line telling every session to read a generated code summary before starting was followed in 11 of about 1,449 sessions, and it was removed.

## Apply in batches, with a way back

1. **Destinations first.** Land the skill, note, stored reminder or hook before the text leaves the file, so no sentence is ever in neither place.
2. **Snapshot** a batch of 1 to 3 adjacent sections: copy the live file into `claude-md-trim/snapshots/<batch>/` and record its hash (`reference.md`, "Batch steps").
3. **Ledger:** one row per removed or reworded sentence in `claude-md-trim/batches/<batch>.json`, with the exact text, the action, the destination or the question number, and the reason.
4. **Apply.** A file git does not track, usually the global file: write the batch's after-file over it, only if its hash still equals the snapshot's. A git-tracked file: land the commit, deliver it, then record the commit id in the batch. A file in a product repository: a pull request, which only the repository's owner merges.
5. **Check:** the batch's rows against the files; the loss check, which compares the snapshot with the live file and fails on a section heading or generated-region marker that is gone with no row for it, a reference to a file that does not exist, or a hook the file names that `settings.json` does not run; and that generated regions match their sources (`reference.md`, "Batch steps" and "The loss check").
6. **Probe** each planned question in a fresh headless haiku session in the file's tree, after writing the expected answer into the run log (the command is in `reference.md`).
7. **Any check or probe fails: revert the batch at once.** Restore the snapshot, or for a git-tracked file revert the recorded commit and deliver the revert (`reference.md`, "Batch steps"). Confirm the live hash equals the snapshot's, record the batch as REVERTED with the failure, and re-plan it smaller once.
8. **Records:** regenerate `LEDGER.md`; a run-log entry; the entry in the record that tracks this work; notes committed and pushed; for the global file, run the mirror sync if you keep a mirror of it, and compare the two files with `cmp`.

## Rewording: what went wrong before

- **The owner's verbatim quotes stay word for word.** Cutting or rewording one is the owner's call. An origin story around a quote may move to a note.
- **An exception or permission stays a whole sentence of its own.** Folded into a parenthetical on the rule it overrides, "delete without asking once the checks pass" made a fresh session answer "ask the owner first" (that batch was reverted).
- **Rewrites that save bytes drop clauses and change names.** In tests, "then report" disappeared and the owner's name changed. List each clause of the old text and find it in the new text.
- **Sizes are reported, never targets.** Never cut a needed rule to reach a number.
- **Moving a line between the global and the project file saves nothing** when both load in nearly every session.
- **Two plans that each remove a line as a duplicate of the other file delete it from both.** Settle duplicates across files in one plan.

## Red flags

| Thought | What is true |
|---|---|
| "A PreToolUse rule will remind them before they push" | Its text arrives with the push's result. Only a refusal stops a first push |
| "A hook checks the conditions, so the permission sentence can go" | The agent decides whether to ask before any command runs. Without the sentence, it asks |
| "It moved to a note (or a skill)" | Unless a hook opens it at its moment, it is gone |
| "The quote is only motivation" | The owner's verbatim quote stays; the story around it may move |
| "The owner asked for 2 KB, so the plan must reach it" | Report the size reached and what the owner's number would cost. Success is every probe passing |
| "The diff reads fine" | Only a fresh session's answers show what an agent will do |
| "The plan is obvious, I'll apply it" | The owner approves the plan first |
