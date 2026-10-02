# Reference: optimize-and-automate-claude-md-files

<!-- prose-only: this file describes steps and commands for you to run; the skill ships no script that runs them, so nothing here is checked by a parser -->

Paths are relative to your repository root unless absolute. Run any trim script from a checkout at origin/main.

## Suggested layout of the trim records

Use any folder. The examples use `claude-md-trim/` at the repository root.

| What | Where |
|---|---|
| The batch loop and the seven questions | `SKILL.md` in this folder |
| Your per-section plan, with any analysts' full reports | `claude-md-trim/SECTION-PLAN.md`, with the reports in `claude-md-trim/section-plan/` |
| Every batch, its checks and probes | `claude-md-trim/RUN-LOG.md` (the summary table and the entries) |
| Every removed sentence and where it went | `claude-md-trim/LEDGER.md` (generated) |
| A batch's records | `claude-md-trim/snapshots/<batch>/` (snapshot, `meta.json`), `claude-md-trim/batches/<batch>.json` (ledger rows), `claude-md-trim/batches/<batch>/after.md` (the whole file after the batch) |
| Moved text waiting to land in a note | `claude-md-trim/note-additions/<note>.md` (the checks count it as existing) |
| Where moved detail of the global file lives | a note you name in the ledger, for example `memory/claude-md-rule-detail.md` |

## Batch steps

This skill ships no script. Do each step by hand, or write a small script for it. Each step below says what it must do and when it must refuse.

| Step | What it must do |
|---|---|
| `begin <batch> <live file>` | Copy the live file to `claude-md-trim/snapshots/<batch>/<file name>.snapshot`. Keep the `.snapshot` ending: Claude Code loads a file named `CLAUDE.md` as instructions when a session reads a file in its folder, so a copy with that name would bring the removed rules back. Write `meta.json` beside it: the live path, the sha256 and size of the file, and the time. Refuse a batch name that already has a snapshot (retaking one needs an explicit option, and is refused while the batch is applied). A batch name is used once: after a revert, the redrafted batch takes a new name (`global-2a` is followed by `global-2b`). |
| `apply <batch>` | For a file git does not track (the global file, usually); refuse a file git tracks, which lands as a commit instead (see `record-commit`). Write `batches/<batch>/after.md` over the live file only while the live sha256 equals the snapshot's; otherwise refuse and print the diff from the snapshot to the live file, to redraft from. Keep the file's line endings, write a temporary file beside it and rename it over the original, and record the applied sha256. |
| `record-commit <batch> <sha>` | For a git-tracked file, after the commit has landed: store the commit id in `meta.json`, so that revert can find it. Refuse a file git does not track, and a commit that does not change the live file (a merge commit, for example). |
| `check <batch>` | Check each ledger row (see "A ledger row") against the live file and its destination, run the loss check below, and confirm that every generated region still matches its source (see "Special cases"). Refuse a ledger file with no rows: it proves nothing. |
| `revert <batch>` | A file git does not track: restore the snapshot, only while the live sha256 equals the applied one; otherwise print the diff and refuse. A git-tracked file: revert the recorded commit, push it, and deliver it to the checkout that sessions read; refuse when no commit is recorded. Then print the live sha256 and whether it equals the snapshot's. |
| `ledger` | Regenerate `LEDGER.md` from every `batches/*.json`. |
| `status` | Print each batch's state from the run log's summary table, and fail while any batch's state is not DONE, REVERTED or BLOCKED. |

A check looks only at the rows it is given. Compare the batch's diff with its rows before `apply`, so no sentence leaves without a row.

## The loss check

A trim is a large hand edit of the files every session loads, and it can fail in four ways that a reader of the draft will not notice. Compare the snapshot with the live file, and fail on any of these:

1. **Heading.** A `## ` section of the snapshot has no counterpart in the live file, and no ledger row holds the whole heading line in its `removed` text with a non-empty destination.
2. **Reference.** The live file names a file in backticks (no spaces) or in a link, and that file does not exist. This usually means the text it points to was meant to move there and did not. A reference that was already broken in the snapshot is a warning, not a failure.
3. **Hook.** The live file says a hook enforces a rule, and no hook command in `settings.json` runs that script. A claimed guard that does not run is worse than no claim.
4. **Marker.** A generated-region marker (see "Special cases") was dropped or altered, which breaks the sync. Removing a whole region, with ledger rows for its markers and its text, is the one exception.

## A ledger row

```json
{"file": "<absolute path of the live file>", "batch": "global-2b",
 "removed": "<the text, copied exactly from the snapshot>",
 "action": "MOVED", "destination": "memory/claude-md-rule-detail.md#anchor",
 "reason": "question 3: applies only when ...",
 "replacement": "<the new text; REWORDED rows only>"}
```

Actions are ENFORCED, REMOVED, MOVED and REWORDED. Quote enough of a sentence that it appears only once in the file. A check fails a MOVED row whose destination lacks the text, and an ENFORCED row whose hook is not wired in block mode. It also needs `removed` and `reason` filled in and `removed` found in the snapshot, so that an absence check cannot pass on text that was never there. REMOVED, ENFORCED and MOVED rows need `removed` absent from the live file; a REWORDED row needs `replacement` present in it.

Text moved into a note or a skill carries `rule-mechanized: <hook>` in an HTML comment when a hook enforces it, otherwise `prose-only: <why no hook can see it>`. A PreToolUse hook that refuses rule-shaped text in a CLAUDE.md, skill or note without one of the two keeps every new rule either enforced or recorded as one that no hook can see. The comment at the top of this skill uses the same marker.

## Probes

Run from the tree whose CLAUDE.md changed, after the change is live there (for a git-tracked file, after delivery to that checkout):

```
env -u CLAUDE_CODE_OAUTH_TOKEN CLAUDE_CODE_ENTRYPOINT=cli timeout 120 claude -p "From your loaded instructions only, in one line: <question>" --model haiku
```

- Write the question and the expected answer into the run log before running it.
- A pass is the expected behavior, not the expected words.
- A 401 is not proof the credential is dead: probe again, then pass a valid long-lived token in `CLAUDE_CODE_OAUTH_TOKEN` (the command `claude setup-token` makes one).
- `env -u CLAUDE_CODE_OAUTH_TOKEN` and `CLAUDE_CODE_ENTRYPOINT=cli` stop a probe started from inside another Claude Code session (the desktop app, for example) from inheriting that session's token and entry point, so the probe runs as a plain command-line session.
- If your hooks expect a marker on every headless launch (a lane name, for example), add it to the prompt.
- For a wording test with no other instructions loaded, add `--safe-mode --tools ""` and pass the text with `--append-system-prompt`.

## Testing a change to this skill

Keep your scenarios, the runner and the scores of earlier tests in one folder, for example `claude-md-trim/skill-tests/` with a `RESULTS.md`. Run them before and after any edit to this skill, in the clean configuration described in the last bullet under Probes, so your own global rules and hooks do not mix into the answers, and add the new scores.

## Delivering a moved rule at its moment

One way is a reminder hook: a PreToolUse hook that reads a rules file and prints the text of every rule whose conditions match the tool call. The hook is a script you write. One entry of its rules file (an example, with the text left out):

```json
{"id": "verify-before-delete-worktree",
 "when": {"tool": "^Bash$", "command": "git worktree remove|git branch -D"},
 "ask": "<the rule text the model receives>",
 "because": "notes/verify-before-delete.md, steps 1 and 4 (moved out of ~/.claude/CLAUDE.md by trim batch global-2a, 2026-01-15)",
 "added": "2026-01-15",
 "why_not_a_check": "<why this reminds rather than blocks>"}
```

- In this format `when` takes `tool`, `path`, `name`, `command`, `content` (case-insensitive regexes) and `path_exists` (true or false). Every key present must match.
- It runs only for tools that the hook's `settings.json` matcher names, for example Write, Edit, Bash and PowerShell. A rule for any other tool, an MCP tool for example, needs the matcher widened first.
- Let a rule reach a session at most once per 30 minutes, so a reminder does not repeat on every call.
- Give the hook a way to print one rule's text by id, so the text can be tested without making a tool call.
- A skill counts as delivered only when such a rule, or another hook, names it at its moment.

## Building or changing a hook

1. Code and tests in a temp sandbox. The hook reads its mode (off, warn or block) from a small setting you control, so the mode can change without a `settings.json` edit. It prints `{"hookSpecificOutput": {"hookEventName": ..., "additionalContext": ...}}` on stdout: text on stderr with exit 0 never reaches the model, and output over about 2 KB arrives only as a preview.
2. Replay it on past transcripts (for a Stop hook, feed it each saved final reply) and read a sample of 20 blocks and 20 passes: 0 wrong blocks, at most 1 wrong pass.
3. An independent review by a fresh agent that did not build it. Fix until it reports 0 blockers.
4. Land and deliver the code, and set its mode to warn.
5. Wire one block at a time. Install the hook's files where `settings.json` expects them, then add the hook block with a tool that backs up `settings.json` first and prints the backup's path, then check that the install is complete and that a test event runs the hook. If either fails, restore the printed backup. Edit `~/.claude/settings.json` only through that tool.
6. After at least 10 real warn events, read every line of the hook's warn log. All correct: set its mode to block. Any wrong: back to step 1.
7. Anyone can turn a hook off with no settings edit: set its mode to off.

Hooks run from the copy of each script that `settings.json` points to, usually the main checkout and not a worktree: a change is live only after it lands on origin/main and is delivered to that copy.

## Special cases

- **Generated regions.** A region between generated-region markers is rewritten by a sync script from a block in its source note. Edit the source, or remove the marker pair, then run the sync.
- **Quoted marker examples** such as `<!-- GENERATED by ... -->` must be reworded so no tool reads them as live markers.
- **A repository that other people merge into** (a product repository, for example): work in a worktree of that repository made from origin/main, and open a pull request with `gh pr create --repo <owner>/<repo>`. Its owner merges. If a scheduled job re-adds a generated note to those files, keep it.
- **A mirror copy of the global file.** If a repository keeps a mirror, edit the live file (`~/.claude/CLAUDE.md`), then run the mirror's sync script and `cmp` the two files.
- **A section that a check compares with another file** (a banned-phrases section and the catalog its check reads, for example) changes together with that file: if a sync runs the check on a schedule, it fails until the two match.

## Records, per batch

- A run-log entry: the time (with its timezone), the commands and their printed results, the commit ids, each probe with its expected answer, and the next step. The summary table row gets DONE, REVERTED or BLOCKED, and the batch table row gets the revert command.
- Regenerate `LEDGER.md`, then commit it with the batch's files.
- The entry in the record that tracks this work (an issue, a decision log or a task file).
