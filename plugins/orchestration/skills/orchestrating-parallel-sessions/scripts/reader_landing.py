#!/usr/bin/env python3
"""The OrchDoc Reader's landing worker (o9:W123): it puts saved changes on origin/main in the
background, so a save in the Reader returns as soon as the change is written to disk.

<!-- subject: orchestration -->

The human, 2026-10-01: *"Something is making the OrchDoc Reader lag very badly. Every time I add an
instruction or make a change there, the pause can last as long as 30 seconds ... It needs to be
pushed to whatever background process that can hold it while i continue to work with it."*

WHAT TOOK THE TIME (measured 2026-10-01). Each save ran `orchdoc.py note` (or `instruct`, or
`today`) and waited for all of it: the write itself took 7-115 ms, and then the same process
landed the change - a `git fetch`, a commit and a `git push` to GitHub, three attempts when main
moved in between. That was 3-6 s on a quiet day and up to about 27 s when main was busy, and the
page waited the whole time.

HOW IT WORKS NOW.
  * A save runs `orchdoc.py note|instruct|today --queue-landing`. Under the doc's lock, that
    writes one job file into `.shared/state/reader-landing/` and then the doc, and returns.
  * This worker - one thread inside the Reader - runs `orchdoc.py land-job --job <file>` on the
    jobs one at a time. `land-job` lands exactly the text that save wrote, with that save's own
    commit subject, and deletes the job file only after the commit is on origin/main.
  * A failure keeps the job file, and the change stays on disk. The job is tried again after a
    pause that starts at 3 seconds and doubles up to 5 minutes. A job that fails twice for a
    reason a retry cannot fix (main holds a conflicting change) is PARKED: it is still retried
    every 5 minutes and the page shows it, and later changes to OTHER notes of the same doc land
    past it. A later change to the same note or entry waits behind it.
  * When the Reader starts, the first scan finds every job left by a crash, a restart or a
    failed push, and lands it.

ORDER. Jobs of one doc land oldest first, one after another; a job is never landed while an
older job of its doc is waiting for its retry pause, so the commits reach main in the order the
saves were made.

Nothing here writes a doc or a commit itself. `orchdoc.py` stays the only writer, with its lock
and every check it already had.

THE COPIES (`CopyWatch`, o9:W123). Measured 2026-10-01: o9's doc in the shared checkout stopped
following main at about 17:57, and every save the human made after that went into that copy and
nowhere else, while the page went on showing it as if it were current. Nothing said so. The
watcher compares each OrchDoc on this computer with main every 30 seconds and tells the page
when one has stopped following it, and since when, so the page can say so at the top.
"""
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

try:
    from no_window import flags
except ImportError:  # a partial delivery must not take the tool down
    def flags(extra=0):
        return extra

SCRIPTS = Path(__file__).resolve().parent
ORCHDOC = SCRIPTS / "orchdoc.py"

#: Must equal `orchdoc.READER_LANDING_REL` and `orchdoc.JOB_NAME_RE` (the test asserts both). Not
#: imported, so loading this module never runs orchdoc's workspace discovery.
QUEUE_REL = ".shared/state/reader-landing"
JOB_NAME_RE = re.compile(r"^(\d{20})-[0-9a-f]{6}\.json$")

FIRST_PAUSE = 3.0          # seconds before the first retry; doubles on each failure
MAX_PAUSE = 300.0          # the longest pause, and how often a parked job is retried
CHANGED_PAUSE = 0.2        # the queue changed under a landing: try again almost at once
CHANGED_LIMIT = 20         # ...this many times in a row, then wait like any other failure
PARK_AFTER = 2             # "content" failures in a row before a job is parked
RESCAN_EVERY = 30.0        # look at the queue at least this often, even when nobody asks
JOB_TIMEOUT = 900          # seconds one `land-job` may run before it is stopped
TEMP_MAX_AGE = 600         # seconds before a leftover temp file in the queue is removed
DONE_KEEP = 900            # seconds a landed change stays in `status()["done"]`, for the page
DONE_MAX = 200             # ...and at most this many of them
#: o9cL1: a landing starts this many seconds after the latest save the Reader told the worker
#: about (READER_LAND_DEBOUNCE), so a burst of saves is not landed one push per save while
#: the human is still working, and the page is not competing with `git` for the processor.
LAND_DEBOUNCE = 2.0
#: Windows: the priority class each `land-job` runs at, with its git calls (they inherit it).
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
#: POSIX: how much `land-job` is niced.
LAND_NICE = 10

#: The copy watcher. Each can be set in the environment of the Reader (the tests shorten them).
COPY_GLOB = "ORCHESTRATOR-DECISIONS-*.md"
COPY_SCAN = 30.0           # READER_COPY_SCAN: seconds between looks at the copies
COPY_CONFIRM = 20.0        # READER_COPY_CONFIRM: a copy that stopped following main is shown
                           # once it has stayed so this long
COPY_BEHIND_GRACE = 600.0  # READER_COPY_BEHIND_GRACE: a copy that is only an older version of
                           # main is shown once it is this far behind; deliver.py refreshes it
COPY_DEPTH = 200           # versions of a doc listed (o9's doc had 28 in the 24 hours to 2026-10-01)
COPY_PAST_BEST = 12        # versions read past the closest one found, before the search stops
#: Must equal `orchdoc.READER_TIPS_REL` (the test asserts it): the Reader's record of its own
#: last write to each doc, which tells a copy waiting for its saves to land from a stuck one.
TIPS_REL = QUEUE_REL + "/tips"


def job_keys(op):
    """What a queued change acts on: its note id and its entry id. A later change sharing one of
    these with a parked change waits for it rather than landing past it."""
    keys = set()
    if not isinstance(op, dict):
        return keys
    if op.get("nid"):
        keys.add("note:" + str(op["nid"]))
    if op.get("entry"):
        keys.add("entry:" + str(op["entry"]))
    today = op.get("today")
    if isinstance(today, dict) and today.get("eid"):
        keys.add("entry:" + str(today["eid"]))
    return keys


class LandingWorker:
    """One background thread that lands the Reader's queued changes, one at a time.

    With `ref` and `watch_copies`, a second thread (`CopyWatch`) compares each OrchDoc on this
    computer with `ref`, and `status()["copies"]` says which have stopped following it."""

    def __init__(self, workspace, orchdoc=ORCHDOC, python=None, log=None, ref=None,
                 watch_copies=False):
        self.workspace = Path(workspace)
        self.dir = self.workspace / QUEUE_REL
        self.orchdoc = Path(orchdoc)
        self.python = python or sys.executable
        self.log = log or (lambda msg: None)
        self.copies = CopyWatch(self.workspace, ref, queued=self._queued_docs, log=self.log,
                                queued_blobs=self._queued_blobs) \
            if (watch_copies and ref) else None
        self._cv = threading.Condition()
        self._poked = False
        self._stopping = False
        self._thread = None
        self._proc = None
        self._last_save = None   # monotonic time of the latest notify(), for LAND_DEBOUNCE
        self.debounce = _env_seconds("READER_LAND_DEBOUNCE", LAND_DEBOUNCE)
        # Everything below is shared with `status()`, which runs on the server's threads.
        self._mx = threading.Lock()
        self._meta = {}         # job name -> ((mtime_ns, size), meta or None)
        self._state = {}        # job name -> its failures so far
        self._unreadable = {}   # job name -> why it could not be read
        self._running = None    # the job being landed now
        self._stuck = {}        # doc -> notes on this computer its last landing left off main
        self._last = None       # the last landing that succeeded
        self._landed = 0
        self._done = []         # recent landings, oldest first: the page marks their notes

    # ---- control -------------------------------------------------------------------------
    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="reader-note-lander",
                                            daemon=True)
            self._thread.start()
        if self.copies is not None:
            self.copies.start()
        return self

    def notify(self):
        """A save was just queued: look now, and retry a failed landing without waiting.

        o9cL1: the copy watcher is NOT poked here any more. A save leaves its doc with a queued
        job, which the watcher reports as "sending" without reading git, so a look now told the
        page nothing new and cost a `git log` and a read of the doc in this process while the human
        was typing. It is poked after each landing (`_record`), when the copy can have changed."""
        now = time.monotonic()
        with self._mx:
            for st in self._state.values():
                if not st.get("parked"):
                    st["next"] = min(st.get("next", now), now)
            self._last_save = now
        with self._cv:
            self._poked = True
            self._cv.notify_all()

    def _debounce_left(self):
        """Seconds until LAND_DEBOUNCE has passed since the latest save, or 0."""
        with self._mx:
            last = self._last_save
        if last is None or self.debounce <= 0:
            return 0.0
        return max(0.0, self.debounce - (time.monotonic() - last))

    def stop(self, timeout=None, kill=False):
        """Stop the thread. With `kill`, also stop a `land-job` that is running: its job file
        stays, and the next start lands it (a landing that already pushed is found on main)."""
        with self._cv:
            self._stopping = True
            self._cv.notify_all()
        if self.copies is not None:
            self.copies.stop()
        p = self._proc
        if kill and p is not None and p.poll() is None:
            try:
                p.kill()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout)
        if self.copies is not None:
            self.copies.join(timeout)

    def _queued_docs(self):
        """{doc file name: how many of its changes are queued}, from the job files."""
        out = {}
        try:
            names = [p.name for p in self.dir.iterdir() if JOB_NAME_RE.match(p.name)]
        except OSError:
            return out
        for n in names:
            meta = self._read(n)[0]
            if meta and meta.get("doc"):
                k = Path(meta["doc"]).name
                out[k] = out.get(k, 0) + 1
        return out

    def _queued_blobs(self):
        """o9:W164: {doc file name: {blob id of the text each queued change of it wrote}}."""
        out = {}
        try:
            names = [p.name for p in self.dir.iterdir() if JOB_NAME_RE.match(p.name)]
        except OSError:
            return out
        for n in names:
            meta = self._read(n)[0]
            if meta and meta.get("doc") and meta.get("blob"):
                out.setdefault(Path(meta["doc"]).name, set()).add(meta["blob"])
        return out

    def alive(self):
        return self._thread is not None and self._thread.is_alive()

    def brief(self):
        """(a landing is running now, how many changes are queued), for the Reader's timing line
        on every request (o9:W150). One folder listing; reads no job file and waits on nothing
        but this object's own short lock."""
        try:
            waiting = sum(1 for n in os.listdir(str(self.dir)) if JOB_NAME_RE.match(n))
        except OSError:
            waiting = 0
        with self._mx:
            running = self._running is not None
        return running, waiting

    # ---- the loop ------------------------------------------------------------------------
    def _run(self):
        self._remove_old_temp_files()
        while True:
            with self._cv:
                if self._stopping:
                    return
                self._poked = False
            wait = 5.0
            try:
                meta, wait = self._next()
                left = self._debounce_left() if meta is not None else 0.0
                if left > 0:
                    meta, wait = None, left      # a save just came in: let the burst finish
                if meta is not None:
                    self._land(meta)
                    continue
            except Exception as ex:          # the thread must outlive any one bad pass
                self.log("landing: the worker hit %s: %s" % (type(ex).__name__, ex))
                wait = 5.0
            with self._cv:
                if self._stopping:
                    return
                if not self._poked:
                    self._cv.wait(timeout=max(0.05, min(wait, RESCAN_EVERY)))

    def _remove_old_temp_files(self):
        """Temp files a crashed writer left in the queue folder (never a job file)."""
        try:
            for p in self.dir.iterdir():
                if p.name.startswith(".") and ".tmp-" in p.name \
                        and time.time() - p.stat().st_mtime > TEMP_MAX_AGE:
                    p.unlink()
        except OSError:
            pass

    def _read(self, name):
        """-> (meta or None, found) for one job file, re-read only when it changed. `found` is
        False when the file is gone, or is being replaced this instant and was never read. Runs on
        the worker and, for a job the worker has not looked at yet, on the page's `status()`."""
        p = self.dir / name
        try:
            st = p.stat()
        except OSError:
            return None, False
        key = (st.st_mtime_ns, st.st_size)
        with self._mx:
            hit = self._meta.get(name)
        if hit and hit[0] == key:
            return hit[1], True
        try:
            with open(str(p), "rb") as fh:
                job = json.loads(fh.read().decode("utf-8"))
            op = job.get("op") if isinstance(job.get("op"), dict) else {}
            today = op.get("today") if isinstance(op.get("today"), dict) else {}
            # The ids it was saved under, which the page knows it by, and the ids it has now:
            # a landing renames a note or a new entry whose id main gave to another one.
            was = job.get("was") if isinstance(job.get("was"), dict) else {}
            nid_now = str(op.get("nid") or "")
            entry_now = str(op.get("entry") or today.get("eid") or "")
            meta = {"name": name, "seq": int(job.get("seq") or 0),
                    "doc": str(job.get("doc") or ""),
                    "message": str(job.get("message") or ""),
                    "keys": job_keys(job.get("op")),
                    "created": str(job.get("created") or ""),
                    # Which note or entry it is, so the page can mark it in place.
                    "nid": str(was.get("nid") or nid_now),
                    "entry": str(was.get("entry") or entry_now),
                    "nid_now": nid_now, "entry_now": entry_now,
                    "op": str(op.get("op") or ""),
                    # o9:W164: the text this save wrote, as a blob id: a copy that is exactly
                    # this is explained by the job (see `CopyWatch._judge`).
                    "blob": blob_id(job["snapshot"].encode("utf-8"))
                            if isinstance(job.get("snapshot"), str) else ""}
            why = None
        except FileNotFoundError:
            return None, False
        except PermissionError:
            # Being replaced this instant (os.replace on Windows): read it next pass.
            return (hit[1], True) if hit else (None, False)
        except (OSError, ValueError, TypeError, AttributeError) as ex:
            meta, why = None, "its queue file could not be read (%s)" % type(ex).__name__
        with self._mx:
            self._meta[name] = (key, meta)
            if why:
                self._unreadable[name] = why
            else:
                self._unreadable.pop(name, None)
        return meta, True

    def _scan(self):
        """{job name: meta} for the queue, re-reading only the files that changed."""
        try:
            names = [p.name for p in self.dir.iterdir() if JOB_NAME_RE.match(p.name)]
        except OSError:
            names = []
        out = {}
        for name in names:
            meta, found = self._read(name)
            if found:
                out[name] = meta
        with self._mx:
            for name in list(self._meta):
                if name not in out:
                    self._meta.pop(name, None)
                    self._state.pop(name, None)
                    self._unreadable.pop(name, None)
        return out

    def _next(self):
        """(the job to land now or None, seconds until one is due)."""
        jobs = self._scan()
        now = time.monotonic()
        by_doc = {}
        for meta in jobs.values():
            if meta is not None:
                by_doc.setdefault(meta["doc"], []).append(meta)
        best, wait = None, RESCAN_EVERY
        with self._mx:
            state = {k: dict(v) for k, v in self._state.items()}
        for metas in by_doc.values():
            metas.sort(key=lambda m: (m["seq"], m["name"]))
            blocked = set()
            for m in metas:
                st = state.get(m["name"]) or {}
                due = st.get("next", 0.0)
                if st.get("parked"):
                    if due > now:
                        # Parked and not due: the changes behind it may go first, unless they
                        # act on the same note or entry.
                        wait = min(wait, due - now)
                        blocked |= m["keys"]
                        continue
                elif blocked & m["keys"]:
                    blocked |= m["keys"]      # and whatever waits on THIS one waits too
                    continue
                elif due > now:
                    # Waiting out its retry pause: nothing newer in this doc goes before it.
                    wait = min(wait, due - now)
                    break
                if best is None or (m["seq"], m["name"]) < (best["seq"], best["name"]):
                    best = m
                break
        return best, wait

    def _land(self, meta):
        name = meta["name"]
        with self._mx:
            self._running = name
        res, why = None, ""
        try:
            env = dict(os.environ, ORCHDOC_WORKSPACE=str(self.workspace))
            # cwd = the workspace: orchdoc reads which branch is canonical from the repo it
            # runs in, and the Reader may have been started from anywhere.
            p = subprocess.Popen([self.python, str(self.orchdoc), "land-job",
                                  "--job", str(self.dir / name), "--json"],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, cwd=str(self.workspace), env=env,
                                 creationflags=flags(_low_priority()))
            self._proc = p
            _lower_priority(p.pid)
            try:
                out, err = p.communicate(timeout=JOB_TIMEOUT)
            except subprocess.TimeoutExpired:
                p.kill()
                out, err = p.communicate()
                why = ("the landing took longer than %d seconds and was stopped; it is tried "
                       "again" % JOB_TIMEOUT)
            for line in reversed((out or b"").decode("utf-8", "replace").splitlines()):
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    got = json.loads(line)
                except ValueError:
                    continue
                if isinstance(got, dict) and "ok" in got:
                    res = got
                    break
            if res is None and not why:
                tail = (err or b"").decode("utf-8", "replace").strip().splitlines()
                why = "orchdoc.py land-job gave no result (exit %s)%s" % (
                    p.returncode, (": " + tail[-1][:160]) if tail else "")
        except OSError as ex:
            why = "orchdoc.py land-job could not be started (%s)" % type(ex).__name__
        finally:
            self._proc = None
            with self._mx:
                self._running = None
        self._record(meta, res, why)

    def _record(self, meta, res, why):
        """Remember how one landing went. The log line is written after the lock is let go, so
        a slow console can never hold up `status()` and with it a page request."""
        name, doc = meta["name"], meta["doc"]
        now = time.monotonic()
        with self._mx:
            if res is not None and res.get("restored"):
                # Main refused this change and the lander put this computer's copy back as it
                # was (o9:W116), so nothing is left to land. Final, not a failure to retry: the
                # page shows the reason on the entry.
                self._state.pop(name, None)
                wall = time.time()
                self._done = [d for d in self._done if wall - d["at"] < DONE_KEEP][-DONE_MAX:]
                self._done.append({"job": name, "doc": doc, "nid": meta.get("nid") or "",
                                   "nid_final": "", "entry": meta.get("entry") or "",
                                   "op": meta.get("op") or "", "restored": True,
                                   "error": str(res.get("error") or "")[:400],
                                   "detail": str(res.get("land_detail") or "")[:300],
                                   "at": wall})
                line = "landing: main refused %s, and this computer's copy was put back: %s" % (
                    name, str(res.get("error") or "")[:160])
            elif res is not None and res.get("ok") and (res.get("landed") or res.get("gone")):
                self._state.pop(name, None)
                if res.get("landed"):
                    self._landed += 1
                    # The ids it landed under, when a landing renamed it (this one or an
                    # earlier attempt, whose renaming is in the job file).
                    nid_final = res.get("nid_final") or (
                        meta.get("nid_now") if meta.get("nid_now") != meta.get("nid") else "")
                    entry_final = res.get("eid_final") or (
                        meta.get("entry_now") if meta.get("entry_now") != meta.get("entry")
                        else "")
                    self._last = {"job": name, "doc": doc, "what": meta["message"],
                                  "detail": str(res.get("land_detail") or "")[:300],
                                  "nid_final": nid_final, "entry_final": entry_final,
                                  "at": time.time()}
                    wall = time.time()
                    self._done = [d for d in self._done if wall - d["at"] < DONE_KEEP][-DONE_MAX:]
                    self._done.append({"job": name, "doc": doc, "nid": meta.get("nid") or "",
                                       "nid_final": nid_final or "",
                                       "entry": meta.get("entry") or "",
                                       "entry_final": entry_final or "",
                                       "op": meta.get("op") or "",
                                       "detail": str(res.get("land_detail") or "")[:300],
                                       "at": wall})
                    self._stuck[doc] = list(res.get("stuck") or [])
                    # Its doc moved on: the parked changes behind it get their next try now.
                    for other, ost in self._state.items():
                        m = (self._meta.get(other) or (None, None))[1]
                        if m and m["doc"] == doc:
                            ost["next"] = now
                line = "landing: %s %s (%s)" % (
                    "landed" if res.get("landed") else "no longer queued", name,
                    meta["message"][:80])
            else:
                st = self._state.setdefault(name, {"attempts": 0, "content": 0, "changed": 0})
                if res is not None and not res.get("ok"):
                    kind = "content"
                    detail = str(res.get("error") or "the landing was refused")
                elif res is not None:
                    kind = res.get("fail") or "transient"
                    detail = str(res.get("land_detail") or "no reason given")
                    # Only a landing that got as far as comparing with main knows which notes
                    # are stuck; a failed fetch says nothing about them, so the last list stays.
                    if res.get("stuck"):
                        self._stuck[doc] = list(res.get("stuck") or [])
                else:
                    kind, detail = "transient", why or "no reason given"
                if kind == "changed" and st["changed"] < CHANGED_LIMIT:
                    # Not a failure: something moved under it, and the next run reads the new
                    # state. The page is not told unless it keeps happening.
                    st["changed"] += 1
                    pause = CHANGED_PAUSE
                else:
                    st["kind"], st["why"], st["when"] = kind, detail[:400], time.time()
                    st["changed"] = 0
                    st["attempts"] += 1
                    st["content"] = st["content"] + 1 if kind == "content" else 0
                    pause = min(MAX_PAUSE, FIRST_PAUSE * (2 ** (st["attempts"] - 1)))
                    if st["content"] >= PARK_AFTER:
                        st["parked"] = True
                        pause = MAX_PAUSE
                st["next"] = now + pause
                st["next_wall"] = time.time() + pause
                line = "landing: NOT landed %s (%s, retry in %.0fs): %s" % (
                    name, kind, pause, detail[:160])
        self.log(line)
        if self.copies is not None:
            self.copies.poke()        # a landing may have refreshed its doc's copy

    # ---- what the page shows -------------------------------------------------------------
    def status(self):
        """For GET /landing: how many changes are waiting, which are failing and why, each
        queued change with the note it is (`jobs`), and the changes landed in the last
        DONE_KEEP seconds (`done`), so the page can mark each note in place. Reads the queue
        folder and this object's memory; never waits on a landing."""
        try:
            names = sorted(p.name for p in self.dir.iterdir() if JOB_NAME_RE.match(p.name))
        except OSError:
            names = []
        # A job saved while the worker is busy landing another has not been read by it yet.
        metas = dict((n, self._read(n)[0]) for n in names)
        now = time.time()
        with self._mx:
            failing, unreadable, jobs = [], [], []
            for n in names:
                st = self._state.get(n) or {}
                m = metas.get(n) or {}
                if n in self._unreadable:
                    unreadable.append({"job": n, "why": self._unreadable[n]})
                nxt = max(0, int(round(st.get("next_wall", now) - now)))
                jobs.append({"job": n, "id": n[:-len(".json")], "doc": m.get("doc", ""),
                             "nid": m.get("nid", ""), "entry": m.get("entry", ""),
                             "nid_final": m.get("nid_now", "") if m.get("nid_now", "")
                             != m.get("nid", "") else "",
                             "entry_final": m.get("entry_now", "") if m.get("entry_now", "")
                             != m.get("entry", "") else "",
                             "op": m.get("op", ""), "running": n == self._running,
                             "state": ("unreadable" if n in self._unreadable else
                                       "parked" if st.get("parked") else
                                       "failing" if st.get("attempts") else "waiting"),
                             "why": st.get("why") or self._unreadable.get(n) or "",
                             "attempts": st.get("attempts", 0), "next_try_in": nxt})
                if not st.get("attempts"):
                    continue
                failing.append({"job": n, "doc": m.get("doc", ""),
                                "what": m.get("message") or n, "why": st.get("why") or "",
                                "kind": st.get("kind") or "", "attempts": st.get("attempts", 0),
                                "parked": bool(st.get("parked")), "next_try_in": nxt})
            stuck = []
            for d, items in sorted(self._stuck.items()):
                for s in items[:20]:
                    stuck.append({"doc": d, "nid": s.get("nid"),
                                  "text": str(s.get("text") or "")[:160],
                                  "why": str(s.get("why") or "")[:200]})
            done = [dict(d, id=d["job"][:-len(".json")], ago=int(now - d["at"]))
                    for d in self._done if now - d["at"] < DONE_KEEP]
            out = {"enabled": True, "pending": len(names), "running": self._running is not None,
                   "failing": failing, "unreadable": unreadable, "stuck": stuck,
                   "landed": self._landed, "last": dict(self._last) if self._last else None,
                   "alive": self.alive(), "jobs": jobs, "done": done}
        out["copies"] = self.copies.status() if self.copies is not None else []
        return out


# ==========================================================================================
# ⭐ IS EACH COPY STILL FOLLOWING MAIN? (o9:W123 (a))
#
# o9's doc on this computer stopped following main at about 17:57 on 2026-10-01: a Do-today item
# saved there never landed, deliver.py does not overwrite a copy that holds a change main lacks,
# and the copy then missed every landing after it. The page went on showing it, the human's saves went
# into it, and nothing anywhere said the copy was stale.
#
# Each pass reads which version of each doc main has (`git rev-parse`, `git ls-tree`: no file
# contents) and the blob id of the copy on disk, worked out here without git. Then each copy is:
#   * "current"  the same blob as main's;
#   * "sending"  what this Reader last wrote, on top of a version of main, while its changes are
#                still queued: they land, and the copy is refreshed when the last one has;
#   * "behind"   an older version of main, word for word: deliver.py brings it up to date;
#   * "ahead"    every change main made, and changes of its own that are not on main;
#   * "stopped"  anything else: it lacks changes main made, and nothing here will bring them in.
# For "behind" and "stopped" the watcher works out the last version of main the copy had, and
# so the time of the first change on main it lacks: when it stopped following main. A change on
# main the copy already has (a save made here, landed without the copy being refreshed after)
# is the copy's own, and is not counted as one it lacks.
# The page is told of a "stopped" copy after COPY_CONFIRM seconds, and of one "behind" or
# "sending" while it lacks changes made on main more than COPY_BEHIND_GRACE seconds ago.
# "main" is the workspace's `ref` (origin/main) as of its last fetch: deliver.py and every
# landing fetch it. The watcher itself never fetches, never writes, and reads file contents
# from git only for a copy that is not current, sending or behind.
# ==========================================================================================
def _low_priority():
    """creationflags for a background `land-job`: below-normal priority on Windows, so the
    browser and the Reader keep the processor while it fetches, rebases and pushes."""
    return BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 0


def _lower_priority(pid):
    """POSIX: nice a `land-job` that was just started. Its git calls inherit it; done right
    after the start rather than in preexec_fn, which is unsafe in a process with threads."""
    if os.name == "nt" or not hasattr(os, "setpriority"):
        return
    try:
        os.setpriority(os.PRIO_PROCESS, pid,
                       min(19, os.getpriority(os.PRIO_PROCESS, pid) + LAND_NICE))
    except (OSError, AttributeError):
        pass


def _env_seconds(name, default):
    try:
        v = float(os.environ.get(name) or default)
    except ValueError:
        return default
    return v if v >= 0 else default


def blob_id(data):
    """The git blob id of a doc's bytes, CRLF read as LF as the docs are stored. Must agree with
    `orchdoc._text_blob` (the test checks it)."""
    data = data.replace(b"\r\n", b"\n")
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _lines(text):
    return set(l for l in text.replace("\r\n", "\n").split("\n") if l.strip())


class CopyWatch:
    """One thread that compares each OrchDoc copy in `workspace` with `ref`."""

    def __init__(self, workspace, ref, queued=None, log=None, queued_blobs=None):
        self.workspace = Path(workspace)
        self.ref = ref
        self.tips = self.workspace / TIPS_REL
        self.queued = queued or (lambda: {})
        # o9:W164: {doc: {blob ids of the texts this Reader's queued changes wrote}}. Without it
        # no queued change is known to explain a copy.
        self.queued_blobs = queued_blobs or (lambda: {})
        self.log = log or (lambda msg: None)
        self.every = _env_seconds("READER_COPY_SCAN", COPY_SCAN)
        self.confirm = _env_seconds("READER_COPY_CONFIRM", COPY_CONFIRM)
        self.grace = _env_seconds("READER_COPY_BEHIND_GRACE", COPY_BEHIND_GRACE)
        self._cv = threading.Condition()
        self._poked = False
        self._stopping = False
        self._thread = None
        self._mx = threading.Lock()
        self._copies = []       # what `status()` returns
        self._seen = {}         # doc -> (state, monotonic time it was first seen in it)
        self._blobs = {}        # doc -> ((mtime_ns, size), blob id)
        self._versions = {}     # (doc, ref commit) -> its versions on main, newest first
        self._judged = {}       # (doc, disk blob, ref commit, ...) -> what `_judge` found
        self._own = {}          # this pass: doc -> blob ids of the texts its queued changes wrote
        self.passes = 0

    # ---- control -------------------------------------------------------------------------
    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="reader-copy-watch",
                                            daemon=True)
            self._thread.start()
        return self

    def poke(self):
        with self._cv:
            self._poked = True
            self._cv.notify_all()

    def stop(self):
        with self._cv:
            self._stopping = True
            self._cv.notify_all()

    def join(self, timeout=None):
        if self._thread is not None:
            self._thread.join(timeout)

    def status(self):
        with self._mx:
            return [dict(c) for c in self._copies]

    def _run(self):
        while True:
            with self._cv:
                if self._stopping:
                    return
                self._poked = False
            try:
                self.scan_once()
            except Exception as ex:          # the thread must outlive any one bad pass
                self.log("copies: the watcher hit %s: %s" % (type(ex).__name__, ex))
            with self._cv:
                if self._stopping:
                    return
                if not self._poked:
                    self._cv.wait(timeout=max(0.2, self.every))

    # ---- one pass ------------------------------------------------------------------------
    def _git(self, args, data=None):
        try:
            p = subprocess.run(["git", "-C", str(self.workspace)] + args, input=data,
                               capture_output=True, timeout=120, creationflags=flags())
        except (OSError, subprocess.SubprocessError):
            return 1, b""
        return p.returncode, p.stdout

    def _tree(self, commit):
        """{doc file name: blob id} at `commit`, for the docs at the top of the workspace."""
        rc, out = self._git(["ls-tree", commit])
        res = {}
        if rc != 0:
            return res
        for line in out.decode("utf-8", "replace").splitlines():
            meta, _t, name = line.partition("\t")
            f = meta.split()
            if len(f) == 3 and f[1] == "blob" and fnmatch.fnmatchcase(name, COPY_GLOB):
                res[name] = f[2]
        return res

    def _disk_blob(self, path):
        try:
            st = path.stat()
        except OSError:
            return None
        key = (st.st_mtime_ns, st.st_size)
        hit = self._blobs.get(path.name)
        if hit and hit[0] == key:
            return hit[1]
        try:
            blob = blob_id(path.read_bytes())
        except OSError:
            return None
        self._blobs[path.name] = (key, blob)
        return blob

    def _versions_of(self, name, ref_sha):
        """[(commit, commit time, blob id, subject)] of `name` on `ref_sha`, newest first: one
        `git log` that reads no file contents. A run of commits that leave the same text (a merge
        shows the file again) is kept as its oldest one, the commit that made that text."""
        key = (name, ref_sha)
        if key in self._versions:
            return self._versions[key]
        rc, out = self._git(["log", "--format=%x01%H %ct %s", "-n", str(COPY_DEPTH), "--raw",
                             "-m", "--no-abbrev", "--no-renames", ref_sha, "--", name])
        res, seen, cur = [], set(), None
        for line in out.decode("utf-8", "replace").splitlines() if rc == 0 else ():
            if line.startswith("\x01"):
                f = line[1:].split(" ", 2)
                cur = (f[0], int(f[1]), f[2] if len(f) > 2 else "") if len(f) >= 2 else None
            elif line.startswith(":") and cur and cur[0] not in seen:
                b = line.split()[3] if len(line.split()) > 3 else ""
                if re.match(r"^[0-9a-f]{40}$", b) and b.strip("0"):
                    seen.add(cur[0])
                    if res and res[-1][2] == b:
                        res[-1] = (cur[0], cur[1], b, cur[2])
                    else:
                        res.append((cur[0], cur[1], b, cur[2]))
        self._versions = dict((k, v) for k, v in self._versions.items() if k[1] == ref_sha)
        self._versions[key] = res
        return res

    def _contents(self, blobs):
        """Yield the text of each blob, in order: `git cat-file --batch`, a few at a time."""
        for i in range(0, len(blobs), 8):
            rc, buf = self._git(["cat-file", "--batch"],
                                data="".join(b + "\n" for b in blobs[i:i + 8]).encode())
            if rc != 0:
                return
            pos = 0
            while pos < len(buf):
                nl = buf.find(b"\n", pos)
                if nl < 0:
                    return
                head = buf[pos:nl].split()
                pos = nl + 1
                if len(head) != 3 or head[1] != b"blob":
                    yield ""
                    continue
                size = int(head[2])
                yield buf[pos:pos + size].decode("utf-8", "replace")
                pos += size + 1

    def _tip(self, name):
        try:
            rec = json.loads((self.tips / (name + ".json")).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return rec if isinstance(rec, dict) else None

    def scan_once(self):
        """Look at every copy once, and keep what `status()` reports."""
        rc, out = self._git(["rev-parse", self.ref, "HEAD"])
        shas = out.decode("ascii", "replace").split()
        if rc != 0 or len(shas) != 2:
            with self._mx:
                self._copies = []
            return
        ref_sha, head_sha = shas
        ref_tree = self._tree(ref_sha)
        head_tree = ref_tree if head_sha == ref_sha else self._tree(head_sha)
        queued = self.queued() or {}
        self._own = self.queued_blobs() or {}
        mono, wall = time.monotonic(), time.time()
        res = []
        for path in sorted(self.workspace.glob(COPY_GLOB)):
            name = path.name
            ref_blob = ref_tree.get(name)
            disk = self._disk_blob(path)
            if not ref_blob or not disk:
                continue                      # a doc main does not have follows nothing
            v = {"doc": name, "state": "current", "since": None, "lacks": 0, "missing": 0,
                 "extra": 0, "queued": queued.get(name, 0), "first_missing": "",
                 "changed": None, "older": False}
            if disk != ref_blob:
                v.update(self._judge(path, disk, ref_sha, ref_blob, head_tree.get(name),
                                     queued.get(name, 0)))
            prev = self._seen.get(name)
            first = prev[1] if prev and prev[0] == v["state"] else mono
            self._seen[name] = (v["state"], first)
            held = mono - first
            if v["state"] == "stopped":
                v["alert"] = held >= self.confirm
            elif v["state"] in ("behind", "sending"):
                v["alert"] = bool(v["lacks"]) and (
                    held >= self.grace or (v["since"] is not None and wall - v["since"] >= self.grace))
            else:
                v["alert"] = False
            res.append(v)
        with self._mx:
            self._copies = res
        self.passes += 1

    def _judge(self, path, disk, ref_sha, ref_blob, head_blob, nqueued):
        """What a copy that is not main's version is (see the section header). Worked out once
        for each text of the copy, version of main and record of the Reader's writes, and kept."""
        name = path.name
        tip = self._tip(name) or {}
        run = tip if tip.get("tip") and tip.get("tip") == disk else None
        key = (name, disk, ref_sha, head_blob, bool(nqueued), bool(tip.get("tip")),
               (run or {}).get("base"))
        hit = self._judged.get(key)
        if hit is None and nqueued and run is None and disk in self._own.get(name, ()):
            # o9cL1: this Reader's saves to this doc are still queued and this text of the copy
            # is not one its own record names (`run`), so the full judge (a `git log` of the doc
            # and reads of several versions of it) has nothing to go on but the job count. Not
            # kept, so it is judged in full once the queue is empty.
            # ⛔ o9:W164: ONLY WHEN A QUEUED JOB OF THIS READER WROTE EXACTLY THIS TEXT. Any queued
            # job used to make every copy of that doc "sending", so a copy that fell behind main
            # for another reason (or that someone else wrote) was neither reported nor alerted
            # for as long as any job was queued or parked. Otherwise the full judge runs, which
            # says "behind" or "stopped" and alerts as it always did.
            return {"state": "sending"}
        if hit is None:
            hit = self._judge_new(path, disk, ref_sha, ref_blob, head_blob, nqueued, tip, run)
            if len(self._judged) >= 64:
                self._judged = {}
            self._judged = dict((k, v) for k, v in self._judged.items() if k[2] == ref_sha)
            self._judged[key] = hit
        return dict(hit)

    def _judge_new(self, path, disk, ref_sha, ref_blob, head_blob, nqueued, tip, run):
        versions = self._versions_of(path.name, ref_sha)
        blobs = [b for _c, _t, b, _s in versions]
        base = (run or {}).get("base")
        # 1. This Reader's own saves, on top of a version of main, still on their way.
        if nqueued:
            # o9:W164: a copy with no record of the Reader's writes was "sending" whatever it
            # held. Now only a text a queued job wrote is (`_judge`); any other is judged below.
            if not tip.get("tip") and disk in self._own.get(path.name, ()):
                return {"state": "sending"}   # queued before the record existed: cannot tell
            if run is not None and base in blobs:
                idx = blobs.index(base)
                missed = []
                if idx:
                    mine, _when = self._read_lines(path)
                    missed = list(range(idx)) if mine is None else \
                        self._follow(mine, blobs, base=idx)[1]
                out = self._after(versions, idx, missed)
                out["state"] = "sending"
                return out
            if run is not None and base and base == head_blob:
                return {"state": "sending"}
        # 2. An older version of main, word for word: it lacks every change made after it.
        if disk in blobs:
            idx = blobs.index(disk)
            out = self._after(versions, idx, list(range(idx)))
            out["state"] = "behind"
            return out
        if head_blob and disk == head_blob:
            return {"state": "behind"}
        # 3. Neither: which version did it last have, and which later changes does it lack?
        mine, changed = self._read_lines(path)
        if mine is None:
            return {"state": "stopped"}
        out = {"changed": changed}
        for text in self._contents([ref_blob]):
            have = _lines(text)
            out["missing"] = len(have - mine)
            out["extra"] = len(mine - have)
        if out.get("missing") == 0:
            out["state"] = "ahead"
            return out
        if run is not None and base in blobs:
            # This Reader's saves on top of a version of main, all landed (nothing is queued),
            # and the copy was not refreshed after them.
            idx, missed = self._follow(mine, blobs, base=blobs.index(base))
        else:
            idx, missed = self._follow(mine, blobs)
        if idx and not missed:
            out["state"] = "ahead"            # it has every change main made after its version
            return out
        out["state"] = "stopped"
        if idx:
            out.update(self._after(versions, idx, missed))
        return out

    def _read_lines(self, path):
        """(the copy's lines, when it last changed), or (None, None)."""
        try:
            data = path.read_bytes().decode("utf-8", "replace")
            return _lines(data), path.stat().st_mtime
        except OSError:
            return None, None

    def _after(self, versions, idx, missed):
        """What the page is told of a copy whose version of main is versions[idx], lacking the
        changes made by the later versions at the indexes `missed`."""
        if not missed:
            return {"lacks": 0, "since": None, "first_missing": "", "older": False}
        j = max(missed)
        return {"lacks": len(missed), "since": versions[j][1],
                "first_missing": versions[j][3][:160],
                # The oldest version listed, with no older one to compare: it may have stopped
                # before that.
                "older": idx == len(versions) - 1 and len(versions) >= COPY_DEPTH}

    def _follow(self, mine, blobs, base=None):
        """-> (index of the version of main the copy last followed, or None when no older
        version is closer to it than main's newest; [indexes of the later versions whose change
        the copy lacks]). With `base`, that version is known, and only the changes after it are
        read.

        The copy is its version, plus what was saved here, minus what main changed after it. Its
        version is the one closest to it, counting the lines in one and not the other: an older
        version also lacks lines the copy has, a newer one has lines the copy lacks. The
        versions are read newest first, until COPY_PAST_BEST in a row come no closer.

        A later change counts as lacking unless the copy has every line it added and none it took
        out: a save made here that main took in later, without this copy being refreshed, is
        the copy's own change and not one it lacks."""
        if base == 0:
            return None, []
        best, past, prev, has = None, 0, None, []
        for i, text in enumerate(self._contents(blobs if base is None else blobs[:base + 1])):
            have = _lines(text)
            if prev is not None:
                has.append((prev - have) <= mine and not ((have - prev) & mine))
            prev = have
            if base is None:
                score = len(have - mine) + len(mine - have)
                if best is None or score < best[0]:
                    best, past = (score, i), 0
                else:
                    past += 1
                    if past >= COPY_PAST_BEST:
                        break
        idx = base if base is not None else (best[1] if best else None)
        if not idx:
            return None, []
        return idx, [j for j in range(idx) if j >= len(has) or not has[j]]


if __name__ == "__main__":
    # Run once in the foreground, for a person checking the queue by hand:
    #   python reader_landing.py <workspace>    lands what is queued there, then exits
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    w = LandingWorker(sys.argv[1], log=print).start()
    while True:
        time.sleep(1.0)
        s = w.status()
        if not s["pending"] or (s["failing"] and len(s["failing"]) == s["pending"]
                                and not s["running"]):
            break
    w.stop(timeout=30)
    s = w.status()
    print("landing: %d landed, %d still queued, %d failing"
          % (s["landed"], s["pending"], len(s["failing"])))
    for f in s["failing"]:
        print("  %s: %s" % (f["what"][:80], f["why"][:200]))
    sys.exit(1 if s["pending"] else 0)
