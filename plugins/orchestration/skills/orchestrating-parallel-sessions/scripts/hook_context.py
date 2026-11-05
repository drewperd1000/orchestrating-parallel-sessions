#!/usr/bin/env python3
"""hook_context.py - how a hook that exits 0 gets a message to the model.

<!-- subject: claude-tooling -->

<!-- route-tags: hook additionalContext hookSpecificOutput stdout stderr exit 0 PreToolUse
     PostToolUse UserPromptSubmit message never reaches the model crash note fail open -->

A PreToolUse or PostToolUse hook that exits 0 reaches the model only through ONE JSON object on
stdout (measured):

    {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "<text>"}}

  - Text on stderr, or plain text on stdout, from such a hook never reaches the model. Send a
    message for the model through emit(), never through print(..., file=sys.stderr).
  - A hook that exits 2 blocks, and its stderr does reach the model. Keep a blocking message on
    stderr; do not send it through here.
  - Print at most one object per run: pass every message of that run to one emit() call.
  - hookEventName must name the event the hook runs under: take it from the payload with
    event_of(), falling back to the event settings.json registers the hook under.
  - Never add permissionDecision, decision or continue to the object: a message must not change
    what the hook decided.
  - UserPromptSubmit reads the same object. A SessionStart hook here prints plain text on stdout,
    which also reaches the model at exit 0.
  - A message a hook would otherwise repeat on call after call goes through first_in_window():
    it is delivered once per context per window, and the delivery is recorded in a file in the
    home state dir.

Import it inside the code path that emits, not at the top of a hook: a missing hook_context.py
then costs that one message, never the hook's blocking decisions.
"""
import json
import os
import sys
import time

#: Replaces ~/.claude/state as the folder a delivery record lives in (the sandbox_env.py
#: convention; tests set it).
HOME_STATE_ENV = "CLAUDE_HOME_STATE_DIR"
#: A delivery older than this is dropped from a record whenever the record is written.
KEEP_SECONDS = 24 * 3600
#: Seconds to wait before each try at reading or replacing a record. Windows refuses both while
#: another copy of the hook has the file open, which parallel tool calls do.
RETRY_DELAYS = (0, 0.01, 0.02)


def event_of(payload, default):
    """The payload's hook_event_name, or `default` when it has none."""
    event = payload.get("hook_event_name") if isinstance(payload, dict) else None
    return event if isinstance(event, str) and event else default


def context_json(event, *texts):
    """The one JSON object carrying `texts`, joined by one blank line; "" when all are empty."""
    parts = [t.strip("\n") for t in texts if isinstance(t, str) and t.strip()]
    if not parts:
        return ""
    return json.dumps({"hookSpecificOutput": {"hookEventName": event,
                                              "additionalContext": "\n\n".join(parts)}})


def emit(event, *texts):
    """Print context_json(event, *texts) and a newline on stdout, or nothing when it is "".

    Returns what it printed. json.dumps escapes non-ASCII, so stdout stays ASCII.
    """
    out = context_json(event, *texts)
    if out:
        sys.stdout.write(out + "\n")
        sys.stdout.flush()
    return out


def crash_text(script, exc):
    """The message a fail-open hook delivers when it crashed."""
    return ("%s crashed (%s: %s), so it did not check this call and let it through."
            % (script, type(exc).__name__, exc))


def context_key(payload):
    """The context this call's output reaches: "<session_id> main", or "<session_id>
    agent-<agent_id>" for a call from a subagent, which holds its own context. None when the
    payload has no session id."""
    if not isinstance(payload, dict):
        return None
    sid = payload.get("session_id")
    if not isinstance(sid, str) or not sid:
        return None
    agent = payload.get("agent_id")
    if isinstance(agent, str) and agent:
        return "%s agent-%s" % (sid, agent)
    return "%s main" % sid


def record_path(name, env=None):
    """A delivery record's path: the file the environment variable `env` names when it is set,
    else `name` in $CLAUDE_HOME_STATE_DIR, else `name` in ~/.claude/state. Read on every call, so
    a test may set the variables late."""
    named = os.environ.get(env) if env else None
    if named:
        return named
    state = os.environ.get(HOME_STATE_ENV) or os.path.join(os.path.expanduser("~"), ".claude",
                                                            "state")
    return os.path.join(state, name)


def _is_time(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _read_record(path):
    """{context key: {item: unix time of its last delivery there}}. A missing, unreadable or
    malformed record, and any part of one not in that shape, reads as empty."""
    data = None
    for delay in RETRY_DELAYS:
        if delay:
            time.sleep(delay)
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            break
        except PermissionError:
            continue
        except Exception:
            break
    if not isinstance(data, dict):
        return {}
    return {ctx: {item: at for item, at in given.items() if _is_time(at)}
            for ctx, given in data.items() if isinstance(given, dict)}


def _write_record(path, record):
    """Write the record whole, through a temp file and os.replace, so no reader sees half a
    file. A write that fails is dropped: the item is then delivered again at its next call."""
    tmp = "%s.%d.tmp" % (path, os.getpid())
    try:
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=1, sort_keys=True)
        for delay in RETRY_DELAYS:
            if delay:
                time.sleep(delay)
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                pass
    except OSError:
        pass
    try:
        os.remove(tmp)
    except OSError:
        pass


def first_in_window(payload, item, name, env=None, window=1800, now=None):
    """True when `item` was not delivered to this call's context in the last `window` seconds,
    and then records it as delivered now; False when it was. Deliver the message on True and
    print nothing on False.

    The record is the file record_path(name, env) gives. A payload with no session id, an empty
    `item`, a window of 0 and any fault reading or writing the record all return True: a record
    this cannot trust delivers. Two parallel calls can both return True for one item, and a
    later write can drop an earlier one's delivery; both err toward delivering, so there is no
    lock. Deliveries older than KEEP_SECONDS are dropped. `now` (unix seconds) replaces the
    clock; tests pass it.
    """
    try:
        ctx = context_key(payload)
        if ctx is None or not window or not isinstance(item, str) or not item:
            return True
        path = record_path(name, env)
        at = _read_record(path).get(ctx, {}).get(item)
        # The clock is read after the record, so a delivery a parallel call wrote before the
        # read can never look newer than `now`.
        now = time.time() if now is None else now
        if at is not None and 0 <= now - at < window:
            return False
        # Read again right before writing: a parallel call may have recorded a delivery since.
        record = _read_record(path)
        record.setdefault(ctx, {})[item] = now
        kept = {}
        for key, times in record.items():
            # abs(): a delivery dated ahead of this clock is kept up to KEEP_SECONDS, not forever
            fresh = {i: t for i, t in times.items() if abs(now - t) < KEEP_SECONDS}
            if fresh:
                kept[key] = fresh
        _write_record(path, kept)
    except Exception:
        pass
    return True
