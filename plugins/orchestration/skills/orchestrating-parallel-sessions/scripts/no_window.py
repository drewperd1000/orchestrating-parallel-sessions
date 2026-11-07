"""Keep every console program this tooling launches off the human's screen.

<!-- subject: claude-tooling -->

The cause: the OrchDoc reader is launched by `ensure_local_servers.py` with DETACHED_PROCESS, so
it has NO console. On 09-23 the reader began running `git` on page loads (the script corpus and
its authored dates). A console program started from a process with no console cannot inherit
one, so Windows gives it a FRESH console, which is a visible window that takes the foreground -
one per git call, a burst per page load.

The child gets a NEW console, and a child whose output is not redirected writes to that console,
where nobody can read it. So an unconditional flag on a call like `subprocess.run(["git",
"push"])` would hide git's messages from whoever ran the script in a terminal.

⭐ THAT IS WHY `flags()` IS CONDITIONAL. It adds CREATE_NO_WINDOW only when THIS process has
no console. With a console present, a child shares it and cannot open a window of its own,
so passing nothing is already correct and `flags()` returns exactly what was passed in.
Without a console - a DETACHED process, a pythonw process, a GUI app's child - an
un-flagged console child gets a fresh VISIBLE console, and that is the only case where the
flag changes anything.

    started with            GetConsoleCP   GetConsoleWindow
    no flags                     437              0          (shares the parent's console)
    CREATE_NO_WINDOW             437              0          (own console, no window)
    DETACHED_PROCESS               0              0          (no console at all)

So GetConsoleCP() == 0 is the test for "no console", and a CREATE_NO_WINDOW process does NOT
pass it - it has a console, so its own children need no flag and inherit the hidden one. (The
parent in that run was the Claude Code Bash tool's shell: a console with no window, 437 / 0.)
No run launched an un-flagged child from a console-less parent, because that is exactly the
launch that would have put a real window on the human's screen.

THE RULE FOR A LAUNCH (`check_no_window.py` refuses a commit that breaks it):

    creationflags=flags()           every launch, by default
    creationflags=flags(OTHER)      when the call already passes other flags; OR them in
    creationflags=NO_WINDOW         allowed only when stdout AND stderr are both redirected
                                    (capture_output=True, or stdout= and stderr= both set),
                                    so nothing can be written to the hidden console. Every
                                    launch fixed in de886bf1 has that shape.
    creationflags=SERVER_FLAGS      a long-lived server that must outlive its launcher

⚠️ CREATE_NO_WINDOW is ignored when combined with DETACHED_PROCESS. A long-lived server that
must outlive its launcher uses SERVER_FLAGS (CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP),
not DETACHED_PROCESS - see ensure_local_servers.py.

⚠️ Import it guarded, so a partial delivery of the scripts folder never takes a tool down:

    try:
        from no_window import flags
    except ImportError:  # a partial delivery must not take the tool down
        def flags(extra=0):
            return extra

The fallback returns `extra` unchanged, which is exactly what those launches did before the
in a SUBFOLDER of .shared/scripts must first append the parent folder to sys.path, or the
import always fails and the fallback silently becomes the behaviour.

A Windows SCHEDULED TASK is outside everything above: Task Scheduler starts a console program
with a visible window of its own before any of this code runs.
"""
import os
import subprocess

#: creationflags value for a console child with no window. 0 off Windows, where POSIX
#: subprocess accepts 0 and rejects anything else.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0

#: For a server that must outlive the process that started it: its own process group, and a
#: console with no window, so everything it launches is windowless too.
SERVER_FLAGS = (NO_WINDOW | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
                if os.name == "nt" else 0)


def has_console():
    """True when this process has a console attached (GetConsoleCP() != 0).

    #: failure-value: False   # if the console cannot be queried, act as though there is none.
    #                           The cost of that guess is a child's un-redirected output going
    #                           to a hidden console; the cost of the other guess is a window
    #                           over the human's work, which is the failure this file exists to end.
    """
    if os.name != "nt":
        return True
    try:
        import ctypes
        return ctypes.windll.kernel32.GetConsoleCP() != 0
    except Exception:
        return False


def flags(extra=0):
    """creationflags for a launch: `extra`, plus CREATE_NO_WINDOW when this process has no console.

    Pass the call's other flags as `extra` rather than OR-ing NO_WINDOW in by hand, so the rule
    lives in one place. Off Windows this returns `extra` unchanged.
    """
    if os.name != "nt" or has_console():
        return extra
    return extra | NO_WINDOW
