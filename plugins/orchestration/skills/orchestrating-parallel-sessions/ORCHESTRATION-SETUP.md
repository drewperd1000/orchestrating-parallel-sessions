# Setting up your own documents, ids and folders

<!-- subject: orchestration -->
<!-- route-tags: setup first run onboarding entry ids prefixes folders document types config reader documents review -->

Use this the first time someone sets up the orchestration tools, and again when they add a new kind
of work. The tools assume nothing about the person's business. They ship eight id letters, a
settings file and a Reader, and the person decides what each letter means for them, which folders hold
their documents, and which of those folders the Reader opens for review. Do the five steps below with the
person in order. Write each answer into the settings file as you go, so nobody is asked twice.

Run every command in this file from the person's WORKSPACE folder (the folder they open their assistant
in), never from the plugin's own folder: a plugin update replaces that folder. Name each tool by its full
path, which is the `scripts` folder beside `SKILL.md`; below, `<scripts>` stands for that path. The
settings file `.orchdoc-config.json` and the decision documents live in the workspace folder. If a command
cannot find the workspace, set `ORCHDOC_WORKSPACE` to its full path. Also set `ORCHDOC_ME` to this
orchestrator's id before the first command (`export ORCHDOC_ME=o91`, or the same in the assistant's
environment settings); it names the document this session writes to and is never kept in the settings
file. `python <scripts>/orchdoc_config.py show` names the settings file on its first line (or says there
is none yet; the first `set` creates `.orchdoc-config.json` in the folder you run it from) and lists every
setting with its current value; run it first and again after each step.

## Step 0 - Make the workspace a git repository

Decision documents are saved with `orchdoc.py commit`, which needs a git repository with a remote named
`origin` and a branch named `main`. In the workspace folder run `git init -b main`, then
`git remote add origin <url of the person's own repository>`, then make one first commit with
`git commit --allow-empty -m "first"` and send it with `git push -u origin main`. If git has never been
used on this computer, first give it a name and address: `git config --global user.name "<name>"` and
`git config --global user.email "<address>"`. A person with no online repository can use a local one
instead: `git init --bare ../workspace-origin.git`, then `git remote add origin ../workspace-origin.git`,
then the same commit and push. `orchdoc.py commit --commit` also moves the local branch forward. A note
saved in the Reader lands by itself and does not, so the pushed copy can be ahead of the local branch;
`orchdoc.py add` keeps working while the local file holds everything in the pushed one. If `add` or
`git push` refuses, bring the branch up to date: run `git add ORCHESTRATOR-DECISIONS-*.md`, then
`git pull --ff-only`. If `git pull` refuses too, you have edits that have not landed: save them with
`orchdoc.py commit --commit` first.

## Step 1 - List what they keep track of

Ask: "What do you decide, ask yourself or a client, hand to someone, and need to remember to do?" Write
the answers as plain nouns in the person's own words. Do not suggest another business's terms. Each noun
becomes one kind of entry in step 2. The same four needs in five kinds of work:

| need | accountant | freelance designer | wedding planner | game developer | gym owner |
|---|---|---|---|---|---|
| a choice that was made | filing method for a client | typeface for a brand | venue for a couple | engine for the project | pricing for a new class |
| a question only the person can answer | which expenses are personal | which logo the client picked | the guest count | what ships first | which trainer covers Saturday |
| a job being worked | a client's quarterly return | a brand kit | a rehearsal dinner | a combat system | a spring promotion |
| a thing they must do themselves | sign the extension | approve the proofs | pay the florist deposit | review the build | sign the lease renewal |

## Step 2 - Map each kind to an id letter

An entry id is a letter plus a number, such as `D9912`. The number is never reused. These letters are built in:

| letter | default meaning | use it for |
|---|---|---|
| `D` | Decision | a choice, with its reasons and who made it |
| `Q` | Question | something the person must answer |
| `W` | Work | a job an agent is doing or has done |
| `T` | To-do | something the person must do themselves |
| `F` | Finding | something an agent found that needs a decision |
| `A` | Agreement | something two parties settled |
| `G` | Guard | something an orchestrator will not do |
| `S` | Standing practice | a rule that always applies |

Most people start with `D`, `Q`, `W` and `T` and ignore the rest. `python <scripts>/orchdoc_ids.py` prints the letters
in force. Two different jobs need two different commands:

1. To change what a letter means for this person, give it a new meaning. A wedding planner who wants `Q`
   to read as a client question runs `python <scripts>/orchdoc_config.py set prefixes Q=Client-question`.
2. To add a letter the tools do not ship, list it as `LETTERS=Meaning` with one to three capitals. A
   wedding planner who tracks vendor quotes runs `python <scripts>/orchdoc_config.py set prefixes V=Vendor-quote,Q=Client-question`.
   The value replaces the previous one, so list every extra letter in the same command. Use no commas or
   equals signs inside a meaning.

A letter you add is recognized wherever an id is read: the entry checks, the links to entries in the Reader,
and the guard that asks for a link on every id. `orchdoc.py add` still files only the built-in kinds
(`--kind decision|question|work|todo|finding`); to record a vendor quote, file it as the nearest kind and
write the `V` id in its text. Say this to the person once, in these words, so they are not surprised.

A bare label such as `INV-4` is not an id. If the person names things with their own series
(for example `INV-4` for invoices) and does not want them written bare in entries, run
`python <scripts>/orchdoc_config.py set pre_label_prefixes INV` and the checker will ask for a link instead. Leave it
unset otherwise.

## Step 3 - Choose the folders

Ask where each of these should live, then record it and use it from then on. Folder names are relative to
the workspace folder (the folder the person opens their assistant in).

1. Decision documents are named `ORCHESTRATOR-DECISIONS-o<N>.md` and live in the workspace folder, so
   every orchestrator writes to the same place and can read the others'. There is no setting for this.
2. The folders of documents the person wants to review in the Reader (proposals, drafts, reports, notes). Pick
   one folder per kind of document, named for the kind, such as `proposals` or `client-reports`, then run
   `python <scripts>/orchdoc_config.py set doc_folders proposals,client-reports`. Files ending in `.md`, `.markdown` or
   `.txt` in those folders, including sub-folders, appear under "Review documents" in the Reader. Entries link to
   a document by its path, so choose names the person will keep.
3. The folder where each agent keeps its working notes, one sub-folder per job:
   `python <scripts>/orchdoc_config.py set work_notes_dir lane-notes`. The sweep tool searches this folder when it
   asks whether a job is finished.

Then record the other standing choices once:

```
python <scripts>/orchdoc_config.py set human_name "<what to call them>"
python <scripts>/orchdoc_config.py set launch_mode manual    # or hybrid
```

Each orchestrator's own id (`o91`, `o92`, and so on) is set per session, in the environment as `ORCHDOC_ME`,
never in the settings file, because every orchestrator shares one workspace. Leave `artifact_re` at its
default; `show` does not ask about it.

If another program already uses port 8787, give the Reader another one now, so every link the tools
print points at it: `python <scripts>/orchdoc_config.py set reader_port 8790`. Start the Reader in step 5
with the same number (`--port 8790`).

## Step 4 - Create the first decision document and an entry

`python <scripts>/orchdoc.py scaffold --doc o91 --dry-run` shows the document the tools would create for the
first orchestrator; run it without `--dry-run` once the person agrees. The new document opens with a
Purpose line that is a stub. Replace it with one sentence on what this orchestrator is for; until then
`orchdoc.py commit` refuses with `E-STUBLEFT`. Then add one test entry in each kind the person chose, for
example `python <scripts>/orchdoc.py add --doc o91 --kind question "How many guests?"`. Each `add` prints the
entry's link. Save with `python <scripts>/orchdoc.py commit --doc o91 -m "first"` (a dry run) and add
`--commit` to push.

Then install the commit guards: `python <scripts>/orchdoc_precommit.py --install-all` in the workspace, and
`python <scripts>/orchdoc_precommit.py --check-hook --all` to confirm. The plugin's Claude Code hooks (the
reminders that stop errors, and the stop check that holds a session until a Reader note is acknowledged)
are installed with the plugin; set `ORCHDOC_ME` to the orchestrator's id in each orchestrator session.

## Step 5 - Open the Reader and check it

Start the Reader with `python <scripts>/render_orchdoc_reader.py --workspace <workspace folder> --open`. Confirm four
things with the person: they can read each test entry, they can type a note in the box under it, the left
column lists the OrchDoc, and a "Review documents" link opens a list of the files in the folders from step 3.
Click one file and confirm it opens. The file itself is read-only. To comment on it, press "Leave a note
on this file": the Reader opens an entry in the first OrchDoc titled "Notes on the file <path>." that names the
file, puts the note in it, and adds later notes on the same file to the same entry. The orchestrator
must acknowledge each note; its stop hook holds the session until it does. While the Reader is open,
it saves each note to the remote in the background, within seconds. If `orchdoc.py commit` then says
another session landed changes to your file, that was the Reader: wait for the landing indicator at the
bottom of the page to clear and run the command again. If an entry's link does not open, fix the folder choice now, before real entries exist.

## What to leave out

Do not copy another business's id letters, folder names or terms into the person's setup. The tools that
every setup uses are the entry checks that keep documents written correctly, the links to entries in the
Reader, the hooks that stop errors and missed rules, the scripts that keep rule documents short, the notes
box in the Reader, and the document view in step 5. Offer a tool built for one kind of writing only when the
person asks for it.
