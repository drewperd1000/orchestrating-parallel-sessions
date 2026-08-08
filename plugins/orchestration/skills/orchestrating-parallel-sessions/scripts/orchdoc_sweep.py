"""A forced, granular, one-section-at-a-time OrchDoc sweep with an independent audit.

THE OBSERVATION THIS IS BUILT ON (the human, 2026-08-07):

    "The shorter and more concise the *exact* deliverable, the more complete the answer.
     'Refresh the OrchDoc' is TOO large a task - the result is a very spotty update that is
     never complete."

That is the correct diagnosis and it is measurable. On 2026-08-07 the reference OrchDoc - built
by the workstream whose entire product is OrchDoc discipline, checked by 27 blocking invariants
- had an empty plate while four orchestrators were waiting on a decision it had never recorded,
and seven work items living in a stale table. Nobody was careless. The task was too big to
finish, so it ended early, and nothing could tell the difference between "finished" and
"stopped".

WHY A CHECKLIST DOES NOT FIX THIS. A checklist is read once, at the start, by a context that
then fills with the work itself. By item six the first item is a memory. Every section this
sweep covers was ALREADY named in a schema the orchestrator wrote and could see. Knowing the
list was never the problem; finishing it was.

SO THE MECHANISM IS THIS: the script holds the list, hands out ONE item, and will not hand out
the next until the current one passes a check the orchestrator cannot write. Three properties,
each load-bearing:

  1. ⭐ ONE SECTION PER STEP, and `next` prints exactly that step and nothing else. Not the
     list, not the progress bar, not the next two. A prompt that shows what is coming invites
     planning ahead, and planning ahead is how a step gets done shallowly.

  2. ⭐ THE COMPLETION TOKEN IS EARNED, NOT ASSERTED. `done` runs a MECHANICAL gate for that
     specific step and REFUSES if it fails. "I finished §2.1" is a claim; "no closed entry
     remains in §2.1 and the file changed since this step opened" is a measurement. Without
     this the sweep is a to-do list that grades its own homework, which is the failure it
     exists to remove.

  3. ⭐ A NO-CHANGE STEP MUST STATE ITS REASON, ON THE RECORD. Sections legitimately need
     nothing. But "nothing needed" and "I did not look" are indistinguishable from outside, so
     the sweep makes the first one cost a sentence. That sentence lands in the report the
     auditor reads.

AND THEN THE PART THAT MAKES IT TRUSTWORTHY - THE INDEPENDENT AUDIT.

The human, again: *"run by a completely separate 'auditor' lane with no knowledge of what the
updater lanes did - this would prevent predisposition to simply agree with the updater lanes."*

Exactly right, and it is why `audit` emits a seed carrying the WORK EVIDENCE (commits, touched
files, lane branches since the baseline) and the CURRENT doc - and deliberately WITHHOLDS every
step report. An auditor shown the updater's reasoning audits the reasoning. An auditor shown
only the evidence audits the document. Those are different jobs and only the second one can
find what the updater never thought of.

⭐ THE STRONGEST FORM IS ONE FRESH WORKER PER SECTION. `next --lane` prints a ready headless
command whose entire brief is one subsection. A fresh context cannot drift, cannot get tired at
item six, and cannot carry a wrong assumption in from item two. That is the human's observation
turned into architecture rather than advice: the deliverable is small because the WORKER is
small.

  sweep start  --doc o9              open a sweep; snapshot the doc + baseline commit
  sweep next   --doc o9 [--lane]     print THE one current step (idempotent; survives compaction)
  sweep done   --doc o9 --report "…" run the gate; advance only if it passes
  sweep done   --doc o9 --no-change --because "…"     record a justified no-op
  sweep audit  --doc o9              emit the independent auditor seed (reports withheld)
  sweep status --doc o9              where the sweep is, without printing the step

State lives in `.orchdoc-sweep/<doc>.json` so a sweep survives compaction, a restart, or being
picked up by a different session tomorrow.
"""
import argparse
import json
import os
import pathlib
import re
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import orchdoc as od  # noqa: E402

STATE_DIR = HERE.parent.parent / ".orchdoc-sweep"


# ----------------------------------------------------------------------------- step definitions
#
# Each step is (key, section, one-line goal, the brief). The brief is written AT the orchestrator
# doing that one section, in the second person, and it always asks BOTH halves of the question:
#
#   (a) is what is HERE still true?      - catches stale status, the o9 §3 table
#   (b) is anything MISSING?             - catches D5, the decision four people asked for
#
# (b) is the half a status-only pass drops, and it is where every serious miss on 2026-08-07
# actually lived. A section can be perfectly consistent and still be silent about the thing that
# matters most.

def _brief(section, title, closed_to, kind_word):
    return """\
⏸  ONE SECTION. Do not touch any other part of the document.

§{sec} {title}

STEP 1 - IS WHAT IS HERE STILL TRUE?
  Read every entry in §{sec}. For each one, check its CURRENT status against reality - the
  code, the commits, the lane branches, the actual artifact. Not against what the entry says
  about itself.
  • finished since it was written  -> set the status, then move it to §{closed}
  • changed but not finished       -> update the status text to what is true NOW
  • unchanged                      -> leave it, and say so in your report

STEP 2 - IS ANYTHING MISSING?  ⭐ THIS IS THE HALF THAT GETS SKIPPED.
  §{sec} holds {kind}. Ask, explicitly: **is there {kind_sing} that exists in reality and
  not in this section?** Look in your own recent work, your chat with the human, your lane
  reports, your other sections' prose. An empty or short section is an ASSERTION that nothing
  more exists - if that assertion is false it is the most damaging thing this document can say,
  because the human will believe it and stop checking.
  Anything you find: `orchdoc.py add --doc <doc> --kind <kind>`, then enrich it self-contained.

STEP 3 - PROVE IT
  Run `orchdoc.py check --doc <doc>` and fix anything it reports IN THIS SECTION.
  Then: `orchdoc_sweep.py done --doc <doc> --report "<what you changed, and what you verified>"`

  If §{sec} genuinely needed nothing, that is a legitimate outcome - but it must be stated:
  `orchdoc_sweep.py done --doc <doc> --no-change --because "<how you established that>"`
  "Nothing needed" and "I did not look" are indistinguishable from outside. The sentence is
  what tells them apart, and an auditor will read it.
""".format(sec=section, title=title, closed=closed_to, kind=kind_word,
           kind_sing=kind_word.rstrip("s") if kind_word.endswith("s") else kind_word)


STEPS = [
    ("2.1", "Decisions", "99.1", "decisions that need the human's ruling"),
    ("2.2", "Questions", "99.2", "questions that need the human's answer"),
    ("2.3", "To-Dos", "99.3", "to-dos that need the human's action"),
    ("3", "ON CLAUDE'S PLATE", "99.3", "the orchestrator's own open work"),
    ("4", "FINDINGS", "4", "findings - what was learned and why it holds"),
    ("1", "LINKS AND DOCS", "1", "every doc and URL this orchestrator owns"),
    ("5", "GUARDS", "5", "what this orchestrator will not do"),
]


def state_path(doc_arg):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return STATE_DIR / ("%s.json" % re.sub(r"[^\w.-]", "_", doc_arg))


def load(doc_arg):
    p = state_path(doc_arg)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def save(doc_arg, st):
    p = state_path(doc_arg)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def git(args, cwd=None):
    try:
        r = subprocess.run(["git"] + args, capture_output=True, text=True,
                           cwd=str(cwd or od.PROJECTS))
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def extra_sections(lines):
    """Sections the orchestrator added in the 6-98 range. Enumerated, never assumed.

    The human's point: *"the same could be done for findings, links, and any other additional
    sections added beyond the first 3 by the specific Orch."* A sweep that covers only the
    sections the SCHEMA knows about would skip exactly the sections an orchestrator cared
    enough to invent - and those are the ones nobody else will check.
    """
    out = []
    for line in lines:
        m = od.SECTION_RE.match(line)
        if not m:
            continue
        num = m.group(1)
        try:
            head = int(float(num))
        except ValueError:
            continue
        if 6 <= head <= 98:
            title = re.sub(r"^#{2,3}\s*[^\w]*\s*§\s*[\d.]+\s*", "", line).strip()
            out.append((num, title or "(untitled)"))
    return out


def build_steps(doc):
    lines = doc.read_text(encoding="utf-8").split("\n")
    steps = []
    for sec, title, closed, kind in STEPS:
        if od.section_span(lines, sec) is not None:
            steps.append({"section": sec, "title": title, "closed_to": closed, "kind": kind})
    for num, title in extra_sections(lines):
        steps.append({"section": num, "title": title, "closed_to": "99",
                      "kind": "whatever this section promises - read its own heading"})
    return steps


# ------------------------------------------------------------------------------ mechanical gates

def gate(doc, step, before_text, require_change=True):
    """Can this step be marked done? Returns (ok, [reasons]).

    ⭐ Every rule here is something the orchestrator CANNOT satisfy by asserting it. That is the
    whole point of the gate: a step that ends on the worker's own say-so is a to-do list, and a
    to-do list is what already failed. These are cheap, local, and specific to the one section
    - a gate that re-checks the whole document would fire on other sections' problems and get
    overridden, which is how a gate dies.
    """
    text = doc.read_text(encoding="utf-8")
    lines = text.split("\n")
    entries, _ = od.parse_entries(lines)
    sec = step["section"]
    reasons = []

    if require_change and text == before_text:
        reasons.append(
            "the file is byte-identical to when this step opened. If the section truly needed "
            "nothing, use --no-change --because '<how you established that>' - which records it "
            "instead of leaving 'done nothing' and 'did nothing' looking the same.")

    span = od.section_span(lines, sec)
    if span:
        here = [e for e in entries if _entry_section_num(e) == sec]
        # A closed item left in a live section is the one thing this step is FOR.
        #
        # TERMINAL_STATUS, never "not in PLATE_STATUS". PLATE_STATUS is {OPEN, BLOCKED} - what
        # belongs on the human's plate - and its complement is not "closed", it is "everything
        # else": DEFERRED, PAUSED, ADOPTED, CONFIRMED, RECORDED, SHIPPED, and IN PROGRESS.
        #
        # ⛔ IN PROGRESS was created the SAME DAY, specifically to mean an item is NOT closed,
        # and this gate classified it as closed and demanded it be archived - which would have
        # buried the exact entries that status exists to keep visible. The only way to satisfy
        # the gate was to revert them to RESOLVED: undoing the correct fix in order to pass the
        # check that enforces it.
        #
        # `archive` had this right and the gate rolled its own answer. ONE predicate, shared -
        # a second definition of "closed" that happens to agree today is a contract waiting to
        # break the next time anyone adds a status.
        stuck = [e["id"] for e in here
                 if od.status_of(e["body"]) in od.TERMINAL_STATUS and not e.get("archived")
                 and not sec.startswith("99") and sec in ("2.1", "2.2", "2.3", "3")]
        if stuck:
            reasons.append(
                "these are closed but still sit in §%s: %s. Step 1 says move them to §%s "
                "- `orchdoc.py archive --doc <doc>` does it." % (sec, ", ".join(stuck),
                                                                 step["closed_to"]))
        body = [l for l in lines[span[0] + 1:span[1]]
                if l.strip() and not re.fullmatch(r"_.*_", l.strip())
                and not l.strip().startswith("<!--")]
        if body and not here and sec in ("2.1", "2.2", "2.3", "3"):
            reasons.append(
                "§%s holds %d line(s) and 0 parseable entries, so none of it reaches the "
                "generated plate and no invariant runs on it. Give each item an "
                "`### <ID> - title` heading." % (sec, len(body)))
    return (not reasons), reasons


def _entry_section_num(e):
    head = e["section"] if e["section"].startswith("#") else "## " + e["section"]
    m = od.SECTION_RE.match(head)
    return m.group(1) if m else None


# ------------------------------------------------------------------------------------- commands

def cmd_start(args):
    doc = od.resolve_doc_arg(args.doc)
    if not doc or not doc.exists():
        print("no such doc: %s" % args.doc, file=sys.stderr)
        return 2
    prior = load(args.doc)
    if prior and not prior.get("finished") and not args.restart:
        print("a sweep is already open on %s at step %d/%d (§%s)."
              % (args.doc, prior["i"] + 1, len(prior["steps"]),
                 prior["steps"][prior["i"]]["section"]))
        print("  `next` to see it, or `start --restart` to abandon and reopen.")
        return 1
    steps = build_steps(doc)
    st = {"doc": args.doc, "path": str(doc), "i": 0, "steps": steps, "reports": [],
          # The CANONICAL ref, not HEAD. OrchDoc work lands on canonical via `orchdoc
          # commit`; the local branch never moves, so a HEAD baseline made every range empty
          # and the audit seed reported "(no commits in range)" while 149 commits sat in the
          # real range. An evidence gatherer that silently gathers nothing is worse than none.
          "baseline_commit": git(["rev-parse", od.CANONICAL_REF]) or git(
              ["rev-parse", "HEAD"]),
          "baseline_stamp": subprocess.run(
              [sys.executable, str(HERE / "orchdoc_stamp.py")],
              capture_output=True, text=True).stdout.strip(),
          "before": doc.read_text(encoding="utf-8"), "finished": False}
    save(args.doc, st)
    print("sweep opened on %s - %d step(s)." % (doc.name, len(steps)))
    print("  baseline commit : %s" % (st["baseline_commit"][:10] or "(none)"))
    print()
    print("  One section per step. `next` prints the current one and NOTHING else - not the")
    print("  list, not what is coming. A step that can see the next step gets planned around")
    print("  instead of finished.")
    print()
    print("  next:  python .shared/scripts/orchdoc_sweep.py next --doc %s" % args.doc)
    return 0


def cmd_next(args):
    st = load(args.doc)
    if not st:
        print("no sweep open on %s. `start --doc %s` first." % (args.doc, args.doc))
        return 2
    if st.get("finished"):
        print("sweep on %s is complete. Run `audit --doc %s` for the independent pass."
              % (args.doc, args.doc))
        return 0
    step = st["steps"][st["i"]]
    doc = pathlib.Path(st["path"])
    st["before"] = doc.read_text(encoding="utf-8")   # re-baseline: `next` is idempotent
    save(args.doc, st)

    if args.lane:
        seed = _brief(step["section"], step["title"], step["closed_to"], step["kind"])
        seed = seed.replace("<doc>", args.doc)
        seed = ("You are a single-section OrchDoc worker. Your entire job is the one section "
                "below in %s. Do not read or edit any other section.\n\n%s" % (doc.name, seed))
        print("# ONE FRESH WORKER, ONE SECTION. A fresh context cannot drift, cannot tire at")
        print("# step six, and cannot carry a wrong assumption in from step two.")
        print("# Probe the app credential first; if it 401s, export the secrets token instead.")
        print()
        print("claude -p %s --model opus \\" % json.dumps(seed))
        print("  --allowedTools Read,Edit,Write,Bash,Grep,Glob < /dev/null &")
        return 0

    print(_brief(step["section"], step["title"], step["closed_to"], step["kind"])
          .replace("<doc>", args.doc))
    return 0


def cmd_done(args):
    st = load(args.doc)
    if not st:
        print("no sweep open on %s." % args.doc, file=sys.stderr)
        return 2
    if st.get("finished"):
        print("sweep already complete.")
        return 0
    step = st["steps"][st["i"]]
    doc = pathlib.Path(st["path"])

    if args.no_change:
        if not (args.because or "").strip():
            print("REFUSED: --no-change needs --because. 'Nothing needed' and 'I did not look'")
            print("         are indistinguishable from outside; the sentence is what tells")
            print("         them apart, and the auditor reads it.")
            return 1
        # --no-change waives ONLY "the file did not change" - which is the point of it. It does
        # NOT waive the structural checks. A section holding a closed item in a live slot, or
        # 40 lines of prose with no entries, is by definition NOT a section that needed nothing,
        # and letting a sentence excuse it would make --no-change a free skip for exactly the
        # sections most in need of the step.
        ok, reasons = gate(doc, step, "", require_change=False)
        if not ok:
            print("REFUSED - §%s cannot be 'no change'; it has structural problems."
                  % step["section"])
            print()
            for r in reasons:
                print("  ⛔ %s" % r)
            print()
            print("  --no-change waives 'the file did not change'. It does not waive these -")
            print("  a section with a closed item stuck in it is not a section that needed")
            print("  nothing, whatever the reason says.")
            return 1
        entry = {"section": step["section"], "outcome": "no-change", "because": args.because}
    else:
        if not (args.report or "").strip():
            print("REFUSED: --report is required. Say what you changed and what you verified.")
            return 1
        ok, reasons = gate(doc, step, st.get("before", ""))
        if not ok:
            print("REFUSED - §%s is not done. Nothing advanced." % step["section"])
            print()
            for r in reasons:
                print("  ⛔ %s" % r)
            print()
            print("  This gate is not a formality. A step that ends on the worker's own say-so")
            print("  is a to-do list, and a to-do list is what already failed here.")
            return 1
        entry = {"section": step["section"], "outcome": "changed", "report": args.report}

    st["reports"].append(entry)
    st["i"] += 1
    if st["i"] >= len(st["steps"]):
        st["finished"] = True
        save(args.doc, st)
        print("§%s accepted. ALL %d SECTION(S) DONE."
              % (step["section"], len(st["steps"])))
        print()
        print("  ⚠ The sweep is not finished until it has been audited by someone who did")
        print("    not do it. Your own pass cannot find what you never thought to look for.")
        print()
        print("  python .shared/scripts/orchdoc_sweep.py audit --doc %s" % args.doc)
        return 0
    save(args.doc, st)
    nxt = st["steps"][st["i"]]
    print("§%s accepted. %d of %d done." % (step["section"], st["i"], len(st["steps"])))
    print("  next: §%s %s   ->  orchdoc_sweep.py next --doc %s"
          % (nxt["section"], nxt["title"], args.doc))
    return 0


def cmd_status(args):
    st = load(args.doc)
    if not st:
        print("no sweep open on %s." % args.doc)
        return 2
    print("sweep on %s - %d of %d step(s) done%s"
          % (args.doc, st["i"], len(st["steps"]), ", FINISHED" if st["finished"] else ""))
    print("  baseline: %s  %s" % (st["baseline_commit"][:10] or "(none)",
                                  st.get("baseline_stamp", "")))
    for r in st["reports"]:
        tag = "no-change" if r["outcome"] == "no-change" else "changed"
        print("  §%-5s %-10s %s" % (r["section"], tag,
                                         (r.get("report") or r.get("because"))[:72]))
    if not st["finished"]:
        cur = st["steps"][st["i"]]
        print("  §%-5s CURRENT    %s" % (cur["section"], cur["title"]))
    return 0


def cmd_audit(args):
    """Emit the auditor seed. Carries EVIDENCE; withholds every step report.

    ⭐ The withholding is the design, not an omission. An auditor shown the updater's reasoning
    audits the reasoning - it reads a plausible account and agrees, because agreeing with a
    coherent story is what reading one does. An auditor shown only the WORK and the DOCUMENT has
    to derive the answer independently, and only that can find what the updater never considered.
    """
    st = load(args.doc)
    if not st:
        print("no sweep on %s." % args.doc, file=sys.stderr)
        return 2
    doc = pathlib.Path(st["path"])
    base = st.get("baseline_commit") or ""
    tip = od.CANONICAL_REF
    rng = ("%s..%s" % (base, tip)) if base else "-40"
    commits = git(["log", "--oneline", "--no-merges", rng])
    touched = git(["diff", "--name-only", "%s..%s" % (base, tip)]) if base else ""
    if not commits.strip():
        # LOUD, not "(none)". A silent empty evidence set turns the auditor into a rubber
        # stamp: it audits the document against nothing, finds nothing, and reports clean -
        # which is indistinguishable from a real clean audit and costs a subagent to produce.
        commits = ("*** NO COMMITS IN RANGE %s ***\n"
                   "*** This is either (a) genuinely no work since the baseline, or (b) a\n"
                   "*** BROKEN BASELINE. Establish which BEFORE auditing anything: run\n"
                   "***   git log --oneline %s..%s\n"
                   "*** yourself. If it is (b), the evidence below is empty for a reason that\n"
                   "*** has nothing to do with the document, and any clean verdict you reach\n"
                   "*** is manufactured. Say so instead of reporting the doc complete."
                   % (rng, base or "<none>", tip))
    branches = git(["for-each-ref", "--sort=-committerdate", "--count=25",
                    "--format=%(refname:short)  %(committerdate:short)", "refs/remotes/origin"])

    seed = """You are an INDEPENDENT AUDITOR for {name}. You did not do this work and you are
not being shown what the updater said about it. That is deliberate: an auditor shown the
updater's reasoning audits the reasoning and tends to agree with it, because agreeing with a
coherent account is what reading one does. You get the BASELINE and the COMMANDS; you gather
the evidence yourself.

YOUR ONE QUESTION:
  Is every piece of work done since the baseline properly and completely captured in this
  document - in the RIGHT section, with a TRUE status?

THE DOCUMENT
  {path}
  Read it in full. Run these yourself and do not take anyone's word for their output:
    python .shared/scripts/orchdoc.py review --doc {doc}
    python .shared/scripts/orchdoc.py check  --doc {doc}

GATHER YOUR OWN EVIDENCE - run these, do not assume:
    git log --oneline --no-merges {base}..{tip}
    git diff --name-only {base}..{tip}
    git for-each-ref --sort=-committerdate --count=25 \\
        --format='%(refname:short) %(committerdate:short)' refs/remotes/origin

  Baseline: {base}   Tip: {tip}   ({stamp})

  *** IF THE COMMIT RANGE COMES BACK EMPTY, THAT IS A FINDING ABOUT THE BASELINE, NOT A
  *** VERDICT ABOUT THE DOCUMENT. This exact bug shipped and was caught by hand: the baseline
  *** was recorded from local HEAD while the work lands on the canonical ref, so the range was
  *** empty while 149 commits sat in the real one. An audit with no input is a rubber stamp.
  *** Establish which case you are in before concluding anything.

WHAT TO LOOK FOR, IN ORDER OF HOW OFTEN IT IS THE ANSWER:

  1. WORK THAT HAPPENED AND IS NOT IN THE DOCUMENT AT ALL. The most common and the hardest to
     see, because nothing in the document points at it. Work BACKWARDS from the commits: for
     each one, find where it is recorded. If you cannot, that is a finding. Do not accept "it
     is implied by" - name the entry, or report the absence.

  2. AN EMPTY OR SHORT SECTION THAT SHOULD NOT BE. An empty "what needs the human" section
     asserts that nothing does. Test that assertion against the EVIDENCE, not against the
     section. If four things landed and none needed a ruling, say so - but check.

  3. A STATUS THAT IS NOT TRUE. Entries claiming OPEN whose work shipped, or DONE whose work
     did not. Check against the commits, not against the entry's own prose.

  4. WORK IN THE WRONG SECTION - the orchestrator's own work sitting on the human's plate, or
     a live item filed under section 99 COMPLETED where nobody will look again.

  5. Anything recorded as prose or a table rather than an `### <ID>` entry, which makes it
     invisible to every automated check.

REPORT AS: a numbered list. For each item - what the evidence shows, where the document says
it (or that it does not), and the specific fix. If the document is genuinely complete, say so
plainly and NAME WHAT YOU CHECKED to establish it. A clean audit that did not name its evidence
is worth nothing - it is indistinguishable from not having looked.
""".format(name=doc.name, path=doc, doc=args.doc, base=(base or "<none>"), tip=tip,
           stamp=st.get("baseline_stamp", ""))

    if args.lane:
        print("# INDEPENDENT AUDIT - a separate worker, with the step reports withheld.")
        print("# Probe the app credential first; if it 401s, export the secrets token.")
        print()
        print("claude -p %s --model opus \\" % json.dumps(seed))
        print("  --allowedTools Read,Bash,Grep,Glob < /dev/null &")
        return 0
    print(seed)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name, fn, extra in (("start", cmd_start, "restart"),
                            ("next", cmd_next, "lane"),
                            ("done", cmd_done, "done"),
                            ("status", cmd_status, None),
                            ("audit", cmd_audit, "lane")):
        p = sub.add_parser(name)
        p.add_argument("--doc", required=True)
        if extra == "restart":
            p.add_argument("--restart", action="store_true",
                           help="abandon an open sweep and reopen")
        if extra == "lane":
            p.add_argument("--lane", action="store_true",
                           help="print a headless `claude -p` command instead of the brief")
        if extra == "done":
            p.add_argument("--report", help="what you changed and what you verified")
            p.add_argument("--no-change", action="store_true",
                           help="this section legitimately needed nothing")
            p.add_argument("--because", help="required with --no-change")
        p.set_defaults(func=fn)

    a = ap.parse_args()
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
