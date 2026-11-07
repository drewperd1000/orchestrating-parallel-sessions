"""
<!-- subject: orchestration -->

With three levels and 57 plate items fleet-wide, everything becomes High or Medium inside a week
and the field stops carrying information - the same collapse the stop-sign glyph suffered.

⭐ THE INPUTS ARE THE TWO THAT ACTUALLY DISCRIMINATE, measured on the live plate before any
of this was written - 57 entries in section 2 across the eleven docs:

    status          55 OPEN    <- useless: no spread
    referrers       25 have none, tailing to 8
    age             6 fresh, 27 in the 14-29 day band, 8 over 30

**Two inputs, not four.** Owner and status had already done their work upstream, and
weighting a signal that has already been applied is how a ranking launders the id order it
started from as a judgement.

⛔ AND IT RANKS LEXICOGRAPHICALLY, NOT BY A WEIGHTED SUM. A sum invites "why is age worth
0.3?", cannot be defended, and cannot be explained per item. A lexicographic order can be
read out loud: *first because you marked it, then because eight things wait on it, then
because it has been open 31 days.* Every position has a sentence.
"""
import re
from datetime import date, datetime

# The one word. A scale re-invents the collapse described above; one mark stays meaningful
# because most entries do not carry it.
MARK = "NOW"

# `**Priority:** NOW` is THE HUMAN'S.
#
# ⛔ NOTHING CAN VERIFY WHICH OF US TYPED IT - every commit in this workspace is authored
# "the human", so git cannot tell an orchestrator from the human, and an uncommitted edit
# has no author at all. That is the same wall the attribution finding hit tonight.
#
# ⭐ SO THE SHAPE CARRIES THE CLAIM AND THE TOOLING MAKES THE HONEST SHAPE THE CHEAP ONE:
# `orchdoc.py priority` always writes the `(proposed by oN)` form, so the bare mark can only
# be hand-typed. Same repricing as `resolve` and the override stamp.
#
# The pattern now accepts the `?`, and `orchdoc.py priority` no longer writes it.
PRIORITY_RE = re.compile(
    r"^\*\*Priority:\*\*\s*(NOW)\b\s*(?:\(\s*proposed(?:\s+by\s+(o\d+)\??)?\s*\))?",
    re.M | re.I)

PROPOSE_TIERS = ("NOW", "High", "Medium", "Low", "Don't do")
PROPOSAL_RE = re.compile(
    r"^\*\*Priority:\*\*[ \t]*(NOW|High|Medium|Low|Don['’]t do)[ \t]*"
    r"\([ \t]*proposed(?:[ \t]+by[ \t]+(o\d+)\??)?[ \t]*\)(?:[ \t]*[-–—][ \t]*(.*?))?[ \t]*$",
    re.M | re.I)


def proposal_of(body):
    """(tier, proposer, why) for a `(proposed by oN)` line of any tier, else None."""
    m = PROPOSAL_RE.search(body or "")
    if not m:
        return None
    tier = choice_norm(m.group(1))
    return tier, (m.group(2) or "?"), (m.group(3) or "").strip()


def explicit_of(body):
    """"""
    m = PRIORITY_RE.search(body or "")
    if not m:
        return None, None
    proposed = "(proposed" in (m.group(0) or "").lower()
    return MARK, (m.group(2) or "?") if proposed else None


# Medium, Low and Hold for Review, Tier D Don't do. The choice is written as
# `**Priority:** <choice>` and is THE HUMAN'S: it outranks the orchestrator's `**Class:**` line.
# (`PRIORITY_RE` above still reads only the bare NOW; this is the wider set.)
CHOICES = ("NOW", "High", "Medium", "Low", "Hold for Review", "Don't do")
CHOICE_CLASS = {"NOW": "A", "High": "B", "Medium": "C", "Low": "C", "Hold for Review": "C",
                "Don't do": "D"}
DONT_DO = "Don't do"
_CHOICE_LINE = re.compile(r"^\*\*Priority:\*\*[ \t]*(.+?)[ \t]*$", re.M)
_CLASS_LINE = re.compile(r"^\*\*Class:\*\*[ \t]*([A-D])\b", re.M)


def choice_norm(text):
    """The canonical choice for `text` (any case; a curly apostrophe; "dont do"), else None."""
    t = re.sub(r"\s+", " ", (text or "").replace("’", "'")).strip().lower()
    t = t.replace("dont do", "don't do")
    return next((c for c in CHOICES if c.lower() == t), None)


def choice_of(body):
    """"""
    m = _CHOICE_LINE.search(body or "")
    if not m or "(proposed" in m.group(1).lower():
        return None
    return choice_norm(m.group(1))


def class_of(body):
    """The choice outranks the class.
    """
    c = choice_of(body)
    if c:
        return CHOICE_CLASS[c]
    m = _CLASS_LINE.search(body or "")
    return m.group(1) if m else None


def age_days(opened, today=None):
    """Days since the entry was opened, or None when it never said."""
    if not opened:
        return None
    try:
        d = datetime.fromisoformat(str(opened)[:10]).date()
    except (ValueError, TypeError):
        return None
    return ((today or date.today()) - d).days


def referrer_count(entry_id, siblings):
    """How many OTHER live entries in the same doc name this one.

    ⭐ THIS IS THE SIGNAL THAT MEANS SOMETHING IS STACKED BEHIND IT. An unruled decision
    with eight entries pointing at it releases more when it is answered than one nobody is
    waiting on, and that is the question a plate should answer.

    ⚠️ It is gameable in principle - an orchestrator could cite its own entry more
    - but a citation is load-bearing elsewhere (it drives staleness), so inflating one costs
    real noise in your own document. Worth knowing rather than worth defending against.

    ⛔ IT WAS QUADRATIC AND THE HUMAN FELT IT. Called once per entry and scanning every sibling
    body each time, this ran 446,138 regex searches on the live fleet and took 23.6s of the
    reader's 24.7s index build - which the server does PER REQUEST, so every click sat on
    "loading". Measured with cProfile after the human reported the lag, not guessed.

    ⭐ The fix is to stop asking the question one id at a time. `referrer_counts` makes ONE
    pass over the bodies, finds every id-shaped token, and tallies - O(n) searches instead
    of O(n * m). This function stays as the single-id form because the tests and
    `orchdoc.py priority` read it, and it now delegates so there is one definition of what
    counts as a reference.
    """
    return referrer_counts(siblings).get(entry_id, 0)


# One is enough: it is the difference between a faster implementation and a different one.
_ID_TOKEN = re.compile(r"(?<![A-Za-z0-9])([A-Z]{1,2}\d{1,4}[a-z]?)(?![A-Za-z0-9])")


def referrer_counts(bodies):
    """{entry_id: how many of these bodies name it} - one pass, not one pass per id.

    Counting occurrences instead would silently change every ranking.
    """
    counts = {}
    for b in bodies:
        for tok in set(_ID_TOKEN.findall(b or "")):
            counts[tok] = counts.get(tok, 0) + 1
    return counts


# \u26d4 A DEPENDS EDGE IS NOT A MENTION, and conflating them put a false claim in the human's
# reader.
DEPENDS_LINE = re.compile(r"(?mi)^\*\*(?:Depends|Blocked-by|Waiting-on|Sequenced-behind):?"
                          r"\*\*([^\n]*)$")


def blocker_counts(bodies):
    """{entry_id: how many bodies DECLARE a dependency on it} - the real edge, not a mention.

    #: failure-value: {}   # a body with no Depends line contributes nothing, which is the
    #                        same as an unparsed one. That is correct here: both mean "no
    #                        declared edge", and the caller shows a mention count either way.
    """
    counts = {}
    for b in bodies:
        for m in DEPENDS_LINE.finditer(b or ""):
            for tok in set(_ID_TOKEN.findall(m.group(1))):
                counts[tok] = counts.get(tok, 0) + 1
    return counts


def _refs(entry_id, bodies, counts):
    """One definition of "how many name it", whether or not a tally was handed in."""
    if counts is None:
        counts = referrer_counts(bodies)
    return counts.get(entry_id, 0)


def rank_key(entry_id, body, sibling_bodies, today=None, counts=None):
    """A sort key. Lower sorts FIRST. Every component is explainable on its own.

    ⭐ `counts` is the shared tally from `referrer_counts`. Without it each call walks
    every sibling body again, which is the quadratic shape that cost the reader 23.6s.
    It stays optional so the tests and `orchdoc.py priority` keep working unchanged.
    """
    mark, proposed = explicit_of(body)
    humans = 0 if (mark and not proposed) else 2
    humans = {"High": 1, "Low": 3, "Hold for Review": 4, "Don't do": 5}.get(choice_of(body), humans)
    refs = _refs(entry_id, sibling_bodies, counts)
    age = age_days(_opened(body), today) or 0
    return (humans, -refs, -age, entry_id)


def reason(entry_id, body, sibling_bodies, today=None, counts=None, blockers=None):
    """Why this sits where it does - the sentence that makes the order defensible.

    \u26d4 `blockers` IS THE SHARED TALLY FROM `blocker_counts`, FOR THE SAME REASON
    `counts` IS. The guard test covered `_refs`, not this function, so it passed. It now
    drives `reason` itself.
    """
    mark, proposed = explicit_of(body)
    if mark and not proposed:
        return "you marked it NOW"
    refs = _refs(entry_id, sibling_bodies, counts)
    age = age_days(_opened(body), today)
    bits = []
    # \u26d4 TWO DIFFERENT FACTS, TWO DIFFERENT WORDS. "named by" is a mention count and a
    # fair centrality signal; "waiting on it" is a declared dependency. Saying the first in
    # the second's words told the human that sixteen entries were blocked on a resolved item.
    if blockers is None:
        blockers = blocker_counts(sibling_bodies)
    blocked = blockers.get(entry_id, 0)
    if blocked:
        bits.append("%d entr%s waiting on it" % (blocked, "y is" if blocked == 1 else "ies are"))
    if refs:
        bits.append("named by %d" % refs)
    if age:
        bits.append("open %d day%s" % (age, "" if age == 1 else "s"))
    if proposed:
        bits.append("%s proposes NOW" % proposed)
    elif proposal_of(body) and proposal_of(body)[0] != MARK:
        bits.append("%s proposes %s" % (proposal_of(body)[1], proposal_of(body)[0]))
    return ", ".join(bits) or "nothing waiting on it, named by nothing"


def reason_short(entry_id, body, sibling_bodies, today=None, counts=None, blockers=None):
    """The same facts, sized for a table cell.

    ⛔ TWO RENDERINGS, ONE COMPUTATION. They read the same numbers rather than each
    deriving their own, because two readers of one fact is the defect this workspace keeps
    rediscovering - and a plate whose short form disagreed with the reader's long form
    would be the version of it that the human notices.

    ⛔ AND IT DID DISAGREE, FOR TWO DAYS.
    """
    mark, proposed = explicit_of(body)
    if mark and not proposed:
        return "\u2b50 yours"
    refs = _refs(entry_id, sibling_bodies, counts)
    age = age_days(_opened(body), today)
    if blockers is None:
        blockers = blocker_counts(sibling_bodies)
    blocked = blockers.get(entry_id, 0)
    bits = []
    if blocked:
        bits.append("%d waiting" % blocked)
    if refs:
        bits.append("named %d" % refs)
    if age:
        bits.append("%dd" % age)
    if proposed:
        bits.append("%s asks" % proposed)
    elif proposal_of(body) and proposal_of(body)[0] != MARK:
        bits.append("%s asks %s" % (proposal_of(body)[1], proposal_of(body)[0]))
    return ", ".join(bits) or "-"


_OPENED = re.compile(r"\*\*Opened:\*\*\s*([0-9]{4}-[0-9]{2}-[0-9]{2})")


def _opened(body):
    m = _OPENED.search(body or "")
    return m.group(1) if m else None
