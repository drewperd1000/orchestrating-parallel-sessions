# orchestrating-parallel-sessions

A Claude Code skill for running a **whole project** through an orchestrator agent.

The orchestrator fans work out to parallel sessions - headless or hand-launched - keeps them
from colliding on shared ground, tells workers to fan out their **own** agents when the work
needs it, and collates what comes back into one picture. It has been used to build
applications, browser software, knowledge graphs, websites and the KPI measurement pipelines
behind them, research tools, copywriters and the copy they produce, and Docs/Sheets
integrations. **The common shape is not code - it is a large project with layered, overlapping
parts.**

**The problem it solves.** Opening a separate session for each piece of a project works right
up until the pieces overlap. Then sessions collide, clobber each other, and the work needs
untangling afterwards - because nothing was coordinating them. A human cannot hold twenty
in-flight sessions in their head. An agent can, and that turns out to be exactly what an
orchestrator is for: hold the live map, keep the fan-out disjoint, gate what merges, and make
sense of what returns.

**The instrument that makes it work with a human in the loop** is a decision doc the tooling
lints, regenerates and refuses to let go stale (`scripts/orchdoc.py`). Open questions, pending
decisions and live links sit in one place with a stable spine - so the human never scrolls back
through thousands of lines of chat to find what they were asked, and never re-asks a question
that already has an answer. A forced section-by-section refresh keeps it complete, and an
independent audit - denied the updater's reasoning on purpose - keeps it honest.

Mechanically: one responsive **orchestrator** decomposes work into disjoint, area-scoped lanes,
hands each to a **worker** session, holds the live lane-map, and gates every merge - while an
inference-free file **watcher** (`scripts/watch_mailbox.py`, Python stdlib only) and per-lane
**mailboxes** carry the back-and-forth hands-off.

**Install as a Claude Code skill:** drop this directory into your skills path (`~/.claude/skills/orchestrating-parallel-sessions/`, or a plugin's `skills/` dir), keeping `SKILL.md` and the `scripts/` subdir together. Claude Code auto-discovers it; invoke it whenever you're coordinating parallel sessions. `scripts/PROTOCOL-template.md` and `scripts/watch_mailbox.py` are copied next to a `mailboxes/` directory at runtime to wire up the hands-off relay.
