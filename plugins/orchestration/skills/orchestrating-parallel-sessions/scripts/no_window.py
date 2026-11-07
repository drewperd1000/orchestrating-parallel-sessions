"""Keep every console program this tooling launches off the human's screen.

<!-- subject: claude-tooling -->

    started with            GetConsoleCP   GetConsoleWindow
    no flags                     437              0          (shares the parent's console)
    CREATE_NO_WINDOW             437              0          (own console, no window)
    DETACHED_PROCESS               0              0          (no console at all)

THE RULE FOR A LAUNCH (`check_no_window.py` refuses a commit that breaks it):

    try:
        from no_window import flags
    except ImportError:  # a partial delivery must not take the tool down
        def flags(extra=0):
            return extra
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
