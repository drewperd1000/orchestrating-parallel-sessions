#!/usr/bin/env python3
"""
<!-- subject: orchestration -->

WHY. The Reader loads its code once.

DESIGN (the simplest that is reliable). The Reader watches the files it runs and restarts itself
when one changes. Not deliver.py: deliver is only one way main's files change (a hand `git pull`,
a hook, a Drive sync and a cut-off deliver all change them too), and a watcher inside the Reader
sees every one of those and needs nothing from the writer.

  THE SET IS COMPUTED, NEVER LISTED. `import_closure` reads render_orchdoc_reader.py with `ast`
  and follows every `import x` / `from x import y` (at any depth in the file, so the function-local
  imports count) that names a .py file in the same folder, then does the same for each of those.
  A module added to the Reader's imports tomorrow is watched tomorrow.

  WHEN IT RESTARTS. Only when ALL hold: a file in the set differs from what the running process
  started with (compared by content, so a touch or a checkout of identical bytes does nothing);
  the files have stopped changing for SETTLE seconds (a delivery writes many files over many
  seconds, and a Reader restarted half-way would load a mix); deliver.py's apply lock is not
  held; every file in the set compiles; no request is in flight; no landing is running.
  Then, in this order (`render_orchdoc_reader` supplies the steps):
    1. refuse new writes with a 503 (a page retries; the save is not lost, it was never taken);
    2. stop the landing worker (waits for a job it is running; waiting jobs stay in
       LANDING.QUEUE_REL on disk and the new process lands them, so none is lost or doubled);
    3. start the new process (no console window); it waits for the port to free;
    4. close the listening socket; the new process binds it within about a second;
    5. wait until the port answers again. If the new process died, the old one binds the port
       again and goes on serving its old code, with a new landing worker, and does not retry
       until a file changes again.
  The old process never kills anything: it ends by itself. Each step is a line in reader.log.
"""
import ast
import hashlib
import os
import threading
import time
from pathlib import Path

SETTLE_SECONDS = 20.0       # files must be unchanged this long before a restart
POLL_SECONDS = 5.0
LOCK_STALE_SECONDS = 600    # deliver.py's STALE_LOCK_SECONDS: an older lock is a dead one


def _imported_names(path):
    """Top-level module names imported anywhere in `path` (function-local imports included)."""
    try:
        tree = ast.parse(Path(path).read_bytes())
    except (OSError, SyntaxError, ValueError):
        return set()
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
            for a in node.names:                  # `from folder import module`
                names.add(a.name)
    return names


def import_closure(entry, scripts_dir=None):
    """Every .py file in `scripts_dir` that `entry` imports, directly or through another, plus
    `entry` itself. Sorted list of resolved Paths. Names that are not a file there (the standard
    library) are skipped; so is a name whose file does not exist yet."""
    entry = Path(entry).resolve()
    folder = Path(scripts_dir).resolve() if scripts_dir else entry.parent
    seen, todo = {entry}, [entry]
    while todo:
        cur = todo.pop()
        for name in _imported_names(cur):
            cand = folder / (name + ".py")
            if cand.is_file():
                cand = cand.resolve()
                if cand not in seen:
                    seen.add(cand)
                    todo.append(cand)
    return sorted(seen)


def _stat_sig(p):
    try:
        st = Path(p).stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _digest(p):
    try:
        return hashlib.sha1(Path(p).read_bytes()).hexdigest()
    except OSError:
        return None


def snapshot(entry, scripts_dir=None):
    """{path: sha1 of its content} for the whole set. Taken at start-up, before the slow work,
    so it describes the code the process loaded."""
    return dict((p, _digest(p)) for p in import_closure(entry, scripts_dir))


def compile_problem(paths):
    """The first (path, error) among `paths` that does not compile, or None."""
    for p in paths:
        try:
            compile(Path(p).read_bytes(), str(p), "exec")
        except (SyntaxError, ValueError, OSError) as e:
            return p, e
    return None


def deliver_lock_held(workspace):
    """True when deliver.py's apply lock in the main checkout is present and not stale."""
    lk = Path(workspace) / ".git" / "deliver.lock"
    try:
        return time.time() - lk.stat().st_mtime < LOCK_STALE_SECONDS
    except OSError:
        return False


class Restarter:
    """Decides when to restart; `step()` is called every POLL_SECONDS by `watch`.

    Callbacks (the Reader supplies them; tests supply fakes):
      idle()      -> True when no request is in flight and no landing is running
      drain()     -> True once new writes are refused AND it is still idle; else it undoes itself
      undrain()   -> writes are accepted again
      restart(reason) -> True when a new process now answers the port
      log(line)
      lock_held() -> True while a delivery is writing files
    """

    def __init__(self, entry, baseline, idle, drain, undrain, restart, log,
                 scripts_dir=None, settle=SETTLE_SECONDS, lock_held=lambda: False,
                 clock=time.monotonic):
        self.entry = Path(entry).resolve()
        self.scripts_dir = scripts_dir
        self.base = dict(baseline)
        self.idle, self.drain, self.undrain = idle, drain, undrain
        self.restart, self.log = restart, log
        self.settle, self.lock_held, self.clock = settle, lock_held, clock
        self.files = sorted(self.base)
        self.stats = dict((p, _stat_sig(p)) for p in self.files)
        self.changed_at = None
        self.pending = []          # names of the files that differ from the running code
        self.done = False
        self._said = None          # the last "waiting" reason logged, so it is said once
        self._failed = None        # the content signature of a restart that failed

    def _say(self, key, line):
        if self._said != key:
            self._said = key
            self.log(line)

    def _look(self):
        """Re-read the set after any file's stat moved; fills `pending`."""
        self.files = import_closure(self.entry, self.scripts_dir)
        cur = dict((p, _digest(p)) for p in self.files)
        self.stats = dict((p, _stat_sig(p)) for p in self.files)
        diff = [p for p in sorted(set(cur) | set(self.base))
                if cur.get(p) != self.base.get(p)]
        self.pending = diff
        self._cur = cur

    def step(self):
        """One look. True when a restart happened (the caller then ends)."""
        if self.done:
            return False
        now = self.clock()
        stats = dict((p, _stat_sig(p)) for p in self.files)
        if stats != self.stats:
            self._look()
            self.changed_at = now
            self._said = None
        if not self.pending or self.changed_at is None:
            return False
        if now - self.changed_at < self.settle:
            return False
        names = ", ".join(sorted(Path(p).name for p in self.pending))
        sig = tuple(sorted((str(p), self._cur.get(p)) for p in self.pending))
        if sig == self._failed:
            return False
        if self.lock_held():
            self._say("lock", "restart: waiting - a delivery holds the apply lock (%s changed)"
                      % names)
            return False
        bad = compile_problem(self.files)
        if bad:
            self._say(("bad", sig), "restart: NOT restarting - %s does not compile: %s"
                      % (Path(bad[0]).name, str(bad[1])[:200]))
            return False
        if not self.idle():
            self._say("busy", "restart: waiting - a request or a landing is running (%s changed)"
                      % names)
            return False
        if not self.drain():
            return False
        reason = "code changed: %s" % names
        self.log("restart: %s - restarting onto the new code" % reason)
        ok = False
        try:
            ok = bool(self.restart(reason))
        except Exception as e:
            self.log("restart: FAILED with %s: %s" % (type(e).__name__, str(e)[:200]))
        if ok:
            self.done = True
            return True
        self._failed = sig
        self.undrain()
        self.log("restart: FAILED - the old process went on serving; will try again only "
                 "after another file changes")
        return False


def watch(restarter, poll=POLL_SECONDS, stop=None):
    """Run `restarter.step()` every `poll` seconds on a daemon thread. Never raises."""
    stop = stop or threading.Event()

    def loop():
        while not stop.wait(poll):
            try:
                if restarter.step():
                    return
            except Exception as e:      # a watcher bug must not take the Reader down
                try:
                    restarter.log("restart: watcher error %s: %s" % (type(e).__name__, e))
                except Exception:
                    pass
    t = threading.Thread(target=loop, name="reader-restart-watch", daemon=True)
    t.start()
    return t, stop
