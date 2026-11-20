## Reading your own folders of documents (collections)

Besides the orchestration documents, the Reader can show any folder of text documents you name
in a config file. Each folder is a "collection": its documents are listed by stage (for example
Drafting, Review, Filed), the versions of one document are grouped, a draft can be compared with
its edited version, and you can leave notes on a Markdown document.

To try it, copy one of the files in `collections-examples/` (legal briefs, marketing copy, or a
plain notes folder) to `.shared/state/reader/collections.json` in your workspace, change the
folder names to yours, and start the Reader. The full guide is in `collections.md`.

What to expect: collections are read-only. You cannot edit, approve or publish from them, and
they have no search yet. A document moves between stages when you move its file into another stage
folder. Notes work on `.md` files only. Collection notes and Compare need the optional note store
and Compare modules, which the public copy of this tooling does not include.
Only text files are listed, never keys or files named like secrets, and links that lead out of
the collection folder are not followed.
