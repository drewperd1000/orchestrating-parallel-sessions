# orchestrating-parallel-sessions

A Claude Code skill for running a **whole project** through an orchestrator agent.

The orchestrator fans work out to parallel sessions - headless or hand-launched - keeps them
from colliding on shared ground, tells workers to fan out their **own** agents when the work
needs it, and collates what comes back into one picture.

It has been used across an entire business, of which only a small part is coding: in-depth
research, planning, brainstorming, copywriting, database creation and analysis, applications,
browser software, knowledge graphs, websites and the KPI measurement pipelines behind them,
research tools, and Docs/Sheets integrations. **The common shape is not code - it is a large
project with layered, overlapping parts.**

## Several orchestrators, working as a team

The larger win is running **more than one**. The working rule is **one orchestrator per
project** - or, for a sharper dividing line, think of them as **departments in a business**:
Operations, Finance, IT, DevOps, HR. Each owns a domain, has its own standing concerns, and
runs for weeks. That analogy answers the question people actually get stuck on - *is this a new
orchestrator, or another lane?* A lane is a slice of one project. A department outlives it.

⭐ **And you do not hand-start them.** The first session you open is usually not a project
orchestrator at all - it is the one that **creates** them. Call it the **Maestro**: the
executive over the host. You tell it what the new domain is, and it allocates the id and
mailbox, writes the charter, **introduces the new orchestrator to its peers** so it does not
duplicate their work without ever finding out, shows it how to message them directly, and
**stays available for the questions it has in its first ten minutes** - about scope, about
overlap, about what was decided six weeks ago. A bootstrap document cannot answer those. A
colleague who was there when the other eight were started can.

Each department orchestrator owns a domain and they **collaborate**: comparing work, pinging each other for data or a
second read, critiquing each other's output, and **catching what the others missed**. It reads
very much like a team of specialists who each know their own area and have to cooperate to move
the whole thing forward.

⭐ **The pattern that pays most: send a proposal to every other orchestrator and ask them to run
it against their OWN history** - not *"does this look right?"*, which gets agreement, but
*"what would have BROKEN in your workstream, and how would you tweak it so it wouldn't have?"*
An author writes tests for the failures they can imagine, which are the ones they already
understand. A peer runs it against the specific way their project actually broke.

In one session of building this tooling, that loop caught - on changes that had already passed
the author's own tests - a blocking check that was **15-for-15 false** on another
orchestrator's document, a mandated status that turned out **not to parse in any spelling**, a
remedy that **could not satisfy its own finding**, and two orchestrators who **refused to
approve** a migration that would have overwritten their curated work.

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

## Install

This repo is a **plugin marketplace** holding two skills:

| skill | what it does |
|---|---|
| `orchestrating-parallel-sessions` | the orchestration model — lanes, workers, mailboxes, the decision doc |
| `orchdoc-audit` | a full forced section-by-section refresh of a decision doc, then an independent audit |

```
/plugin marketplace add drewperd1000/orchestrating-parallel-sessions
/plugin install orchestration
```

They surface as `/orchestration:orchestrating-parallel-sessions` and `/orchestration:orchdoc-audit`.

The tools — `orchdoc.py`, `orchdoc_sweep.py`, `creds.py`, `watch_mailbox.py`,
`PROTOCOL-template.md` — ship inside the orchestration skill's `scripts/` directory. They are
Python **stdlib only**, so there is nothing to install. `orchdoc-audit` drives those same tools
rather than shipping its own copies. At runtime, `PROTOCOL-template.md` and `watch_mailbox.py`
are copied next to a `mailboxes/` directory to wire up the hands-off relay.

> ⚠️ **This layout changed on 2026-08-07.** The repo previously held a single skill with
> `SKILL.md` at the root, installed by dropping the directory into `~/.claude/skills/`. If you
> installed it that way, that copy is stale — remove it and use the steps above. The change was
> needed to hold more than one skill in one repo.
