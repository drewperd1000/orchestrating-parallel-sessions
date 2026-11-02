"""o9L1506: the script-note store. The human's notes on a script, kept OUTSIDE the script file.

One JSON file per script, in <home-state-dir>/reader-state/script-notes/ (per machine, like the
reviewed marks of reader_full_rewrite.py; `shared_state.home_state_dir()`, `~/.claude/state`
unless CLAUDE_HOME_STATE_DIR names another). The script file is never read or written here.
This is NOT the `{{{ }}}` marker of script_notes.py, which the human types by hand inside a script.

KEY: the script's path relative to script-library/, posix, as the Scripts tab lists it
("1-drafts/<folder>/<name>.md" or "3-final/..."). The file name is the SHA-256 of that path
(first 24 hex), and the file also holds the path, so a hash collision is detected, not trusted.
Chunk ids and Reader script ids change between runs; the path does not.

Each note keeps its FULL text (never truncated: a text over the cap is refused) and a timestamp:
  {"id", "text", "at", "source"}   source is "laptop" or "phone"
A note added with the same `id` twice is stored once, so re-importing a phone row is harmless.
"""
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path

try:
    import shared_state
except Exception:       # pragma: no cover
    shared_state = None

DIR_NAME = "script-notes"
NOTE_MAX = 7500                    # the same cap as an OrchDoc note (orchdoc.NOTE_TEXT_LIMIT)
ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,80}$")
STAGES = ("1-drafts", "2-ready-for-owner", "3-final", "4-retired")   # the phone importer allows only 1-drafts and 3-final
_LOCK = threading.Lock()


def store_dir():
    base = shared_state.home_state_dir() if shared_state else Path.home() / ".claude" / "state"
    return Path(base) / "reader-state" / DIR_NAME


def clean_rel(rel):
    """The path in canonical form, or None when it is not a script path under a stage folder."""
    if not isinstance(rel, str):
        return None
    rel = rel.replace("\\", "/").strip().lstrip("/")
    parts = rel.split("/")
    if len(parts) < 2 or parts[0] not in STAGES or not rel.lower().endswith(".md"):
        return None
    if any(p in ("", ".", "..") or ":" in p or "\x00" in p for p in parts):
        return None
    return rel


def _file_for(d, rel):
    return Path(d) / (hashlib.sha256(rel.encode("utf-8")).hexdigest()[:24] + ".json")


class Store:
    def __init__(self, directory=None):
        self.dir = Path(directory) if directory else store_dir()

    def _read(self, rel):
        try:
            d = json.loads(_file_for(self.dir, rel).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(d, dict) or d.get("rel") != rel or not isinstance(d.get("notes"), list):
            return []
        return [n for n in d["notes"] if isinstance(n, dict) and isinstance(n.get("text"), str)]

    def list(self, rel):
        """The notes on a script, oldest first ([] for a bad path or none)."""
        rel = clean_rel(rel)
        return self._read(rel) if rel else []

    def add(self, rel, text, note_id=None, source="laptop", at=None):
        """(ok, error_or_note). Appends one note; with a `note_id` already stored, returns that
        note and adds nothing (idempotent)."""
        rel = clean_rel(rel)
        if rel is None:
            return False, "not a script path under script-library/"
        if not isinstance(text, str):
            return False, "the note must be text"
        if not text.strip():
            return False, "the note is empty"
        if len(text) > NOTE_MAX:
            return False, "the note is %d characters; the most is %d" % (len(text), NOTE_MAX)
        if note_id is not None and not (isinstance(note_id, str) and ID_RE.match(note_id)):
            return False, "bad note id"
        with _LOCK:
            notes = self._read(rel)
            if note_id:
                for n in notes:
                    if n.get("id") == note_id:
                        return True, n
            note = {"id": note_id or "n-%d-%s" % (time.time() * 1000, os.urandom(3).hex()),
                    "text": text, "at": at if isinstance(at, str) and at else
                    time.strftime("%Y-%m-%dT%H:%M:%S%z"), "source": source}
            notes.append(note)
            try:
                self.dir.mkdir(parents=True, exist_ok=True)
                p = _file_for(self.dir, rel)
                tmp = p.with_name(p.name + ".tmp")
                tmp.write_text(json.dumps({"rel": rel, "notes": notes}, indent=1,
                                          ensure_ascii=False), encoding="utf-8", newline="\n")
                os.replace(str(tmp), str(p))
            except OSError as ex:
                return False, "could not save the note (%s)" % type(ex).__name__
        return True, note
