# Collections: reading your own folders of documents in the Reader

A collection is a folder of documents that you name in a small config file. The Reader then lists
the documents by stage, groups the versions of each one, shows a page for each document, shows a
draft beside its edited version, and keeps your notes. This page says how to set one up and what
it does and does not do.

## What you get, and what you do not

Collections are for reading and reviewing. They are read-only.

- **Included:** a list of the documents grouped by stage (for example Drafting, Partner review,
  Filed); versions of one document kept together, newest first; a page for each document; a
  Compare page for a draft and its edited version; a notes box on Markdown documents.
- **Not included:** editing a document in the Reader, approving or moving a document to another
  stage, publishing, the phone export, and any other write to your files. To move a document to
  another stage, move the file into that stage's folder yourself. The Reader picks up the change
  the next time you open the page.
- **Search in the list.** Each collection's list has a "Find by id or title" box above it. It
  narrows the list as you type, matching the document's id (its name without the version and
  author), its file name and path, and its first `#` heading, all words at once, any case.
  The Reader's main search box still does not look inside collections. The config key `views`
  controls the box: leave `search` out of it to hide the box. Of the other words `views`
  accepts (`tree`, `new`, `compare`), the collection pages ignore them.
- **Notes and Compare are optional parts of the Reader.** Notes work only on `.md` files, and
  only in a Reader that has the note store and the Compare module installed. A copy of the Reader
  without them shows the document pages and the tree and leaves out the notes box and the Compare
  link. The public copy of this tooling does not include the note store and Compare modules, so
  collection notes and Compare are not available there.

## Set up a collection

1. Pick a folder inside your workspace for the documents, for example `briefs/`. The folder must
   be inside the workspace; an absolute path, a path with `..` in it, or a folder that resolves
   outside the workspace is refused.
2. Create the config file `.shared/state/reader/collections.json` under the workspace. The folder
   `.shared/state/reader/` is ignored by git, so the file stays on your machine.
3. Put the config in it (see the recipes below), or copy one of the files in
   `collections-examples/` next to the Reader's scripts and edit it.
4. Start the Reader, or reload its page. The Reader reads the config again whenever the file
   changes, so a new collection appears without a restart. A "Collections" heading appears in the
   left rail with one button per collection.

The file holds either an object with a `collections` list, or the list itself:

```json
{"collections": [ { "id": "notes", "name": "Notes", "root": "notes" } ]}
```

To start the Reader on a workspace: `python render_orchdoc_reader.py --workspace <folder>`.

### Keys of one collection

Only `id` and `root` are required. Any key not listed here is refused, and the message says which.

| Key | Meaning | Default |
| --- | --- | --- |
| `id` | Short name used in the page address (`#c/<id>`) and as the notes folder name. 1 to 32 characters: lower-case letters, digits and hyphens, starting with a letter or digit. The id `library` is reserved and refused. | required |
| `name` | The label in the rail. | the id |
| `root` | The folder of documents, relative to the workspace. | required |
| `glob` | Which files to list, matched against the path under the root. `*` is any run of characters within one folder name, `**/` is any number of folders. Case is ignored. | `**/*.md` |
| `skip_dirs` | Folder names to skip at any depth. Folders and files whose names start with `.` or `_` are always skipped. | none |
| `version_rule` | How file names tell versions and authors apart. See "Version rules". | `{"type": "none"}` |
| `stages` | An ordered list of stage folders directly under the root. See "Stages". | none |
| `unplaced_dir` | A folder name that sorts last inside each stage, for documents not yet sorted. | none |
| `kinds` | The words shown for each kind of file: `draft`, `rewrite`, `log`, `note`. | draft, edit, log, note |
| `final_rule` | A header line that marks a document final. See "Stages". | `Approved-By` |
| `why_notes` | `{"suffix": ".something.json"}`: the ending of the reasons file that Compare reads next to a draft. It must start with `.` and end with `.json`; any other value is ignored and the default is used. | `.why-notes.json` |
| `views` | A list of the words `tree`, `new`, `compare` and `search`. The collection pages read only `search`: with it, the list has the "Find by id or title" box; without it, no box. | `["tree", "search"]` |

The keys `type_labels`, `note_syntax`, `extensions` and `editable` are accepted so that a config
does not fail, and the collection pages ignore them today.

### Limits

At most 8 collections, 12 stages and 12 kinds per collection, a config file of at most 64 KB, a
version pattern of at most 400 characters, 2000 documents per collection, 8 folders deep, and a
shown document of at most 2 MB. Beyond a limit the Reader shows a message, not a crash.

## What the Reader lists

- Only viewable text types are listed: `.md`, `.markdown`, `.txt`, `.json`, `.jsonl`, `.csv`,
  `.tsv`, `.log`, `.yml`, `.yaml`, `.toml`, `.ini`, `.cfg`, and common source and script types.
  Anything else (a Word file, an image, a `.pem` key) is not listed. A file with `secret`,
  `credential`, `password`, `passwd`, `private-key` or `api-key` in its name, or `token` as a
  separate word of its name (`token.txt`, `oauth-token.md`), a `.env` file, and anything under a
  `secrets`, `.secrets` or `.git` folder is never listed.
- To list more than Markdown, set `glob` to something like `**/*.*`. The list is still limited
  to the types above.
- A document whose text looks like it holds a private key, an access token, an API key or a web
  address with a password in it is not shown; the page says so.
- Links (symbolic links and Windows junctions) are followed only when they stay inside the
  collection root. A link that points out of the root is skipped, so a collection cannot be used
  to read files elsewhere.
- Markdown documents are drawn as formatted text; every other type is shown as plain text.

## Stages

A stage is a folder directly under the root. Each entry has an `id`, a `folder` (one folder
name), a `label` shown in the tree and on badges, and optional flags:

- `"approved": true` marks every document in that folder as final.
- `"hidden": true` leaves that folder's documents out of the Reader altogether (a place for
  withdrawn or dead documents).

The tree shows one section per stage, in the order you list them, with the number of documents.
A document directly in the root, or in a folder that is not a stage, appears under "No stage".

A document is also shown as final when its first lines hold a line of the form
`Approved-By: someone`. Change the field with `final_rule.header_field` and require a starting
text with `value_prefix`. Set `header_field` to an empty string to turn this off.

## Version rules

The rule decides, from a file name, which document a file belongs to, its version number, who
wrote it, and its kind. Three rules exist, set with `version_rule.type`.

**`none`.** Every file is its own document. There are no versions and no Compare.

**`name-pattern`.** A regular expression matched against the file name (not the folder). It needs
the named groups `name`, `ver` and `author`. A group named `log` (any match) makes the file a log;
a group named `app` is recorded as an app copy. `author_roles` maps each author word to a kind:
`draft`, `rewrite`, `log` or `note`. An author word not in the map is a draft. A file whose name
does not match is shown as one unversioned draft, so a stray file never disappears. In JSON each
backslash is doubled, as in the legal example.

**`folder`.** Versions live in a subfolder (default `versions`, set `folder` to change it). A name
ending in a space, underscore or hyphen followed by `v` and a number (`page v1.md`, `page-v2.md`)
has that version, wherever the file sits. Inside the versions subfolder a file without such an
ending counts as version 1. A file of the same name without a version ending in the folder above
counts as the newest version. Files are grouped by
the folder above `versions/` and the name without its version ending.

Versions of one document are shown together, newest first.

### Compare

Compare shows a draft beside its edited version, passage by passage, with the reason for each
change when a reasons file exists next to the draft (the draft's file name without `.md`, plus
`.why-notes.json`). A rewrite is paired with a draft of the same version number when there is
one, and otherwise with the closest draft at or below its version. Because only the `name-pattern` rule gives a
file the kind `rewrite`, only a collection with a `name-pattern` rule and an `author_roles`
entry mapped to `rewrite` has Compare pages. The Compare page is read-only: the Reader shows an
existing reasons file and does not write one.

## Notes

A document page has a "Notes on this document" box. Notes are saved in the Reader's state folder
on your machine, one folder per collection id, and never inside your documents. A note is kept
against the document's path under the root, so renaming or moving a file starts a new, empty
notes list. The box appears only on `.md` documents, and when the collection has stages, only on
documents inside a stage folder. If the box does not appear or Save is refused, reload the page.

## Recipes

Copies of these are in `collections-examples/`.

### 1. Legal briefs (`legal-briefs.json`)

Drafts and the partner's edits are told apart by the author word at the end of the file name:

```
briefs/
  1-drafting/       motion-to-dismiss v1 associate.md
                    motion-to-dismiss v2 associate.md
                    motion-to-dismiss v2 partner.md
  2-partner-review/
  3-filed/
  4-withdrawn/      (hidden)
  archive/          (skipped)
```

The name pattern is `<name> v<number> <associate|partner>.md`. `partner` files are edits, so each
one gets a Compare page against the associate draft of the same version. The label of a file
reads "associate draft" or "partner edit". Stage badges read Drafting, Partner review and Filed;
a document with a `Signed-Off-By:` line near the top also shows as final.

### 2. Marketing copy (`marketing-copy.json`)

```
copy/
  drafts/           landing-page.md          (newest)
    versions/       landing-page v1.md
                    landing-page-v2.md
  approved/         final-ad.md
  killed/           (hidden)
```

Versions sit in a `versions/` folder; the file beside that folder is the newest. `glob` is
`**/*.*` so text files other than Markdown are listed too. This rule groups and orders versions
but does not pair drafts with edits, so there is no Compare page.

### 3. A plain folder of notes (`notes-folder.json`)

One folder of Markdown files with no stages and no version rule. Each file is a document. This is
the smallest useful config:

```json
{"collections": [{"id": "notes", "name": "Notes", "root": "notes", "skip_dirs": ["archive"]}]}
```

## When the config has a mistake

The Reader never stops for a bad config. A problem appears as a message under the "Collections"
heading in the rail and on the collection's intro page, naming the collection by its position in
the list. Collections that are valid still show. Common messages:

- `root must be a folder under the workspace, not an absolute path`
- `id must be 1 to 32 characters: lower-case letters, digits and hyphens`
- `version_rule.pattern needs a named group 'ver'`
- `unknown key: ...`
- `a collection cannot use the id 'library'`

## Check a config without starting the Reader

`python reader_collections.py <workspace>` prints each collection id and root, and one `error`
line for each problem found.
