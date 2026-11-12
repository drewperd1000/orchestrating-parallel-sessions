"""
<!-- subject: orchestration -->

**Two inputs, not four.** Owner and status had already done their work upstream, and
weighting a signal that has already been applied is how a ranking launders the id order it
started from as a judgement.
"""
import re
from datetime import date, datetime

# The one word. A scale re-invents the collapse described above; one mark stays meaningful
# because most entries do not carry it.
MARK = "NOW"

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
    m = PRIORITY_RE.search(body or "")
    if not m:
        return None, None
    proposed = "(proposed" in (m.group(0) or "").lower()
    return MARK, (m.group(2) or "?") if proposed else None


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
    """
    return referrer_counts(siblings).get(entry_id, 0)


_ID_TOKEN = re.compile(r"(?<![A-Za-z0-9])([A-Z]{1,2}\d{1,4}[a-z]?)(?![A-Za-z0-9])")


def referrer_counts(bodies):
    """{entry_id: how many of these bodies name it} - one pass, not one pass per id.
    """
    counts = {}
    for b in bodies:
        for tok in set(_ID_TOKEN.findall(b or "")):
            counts[tok] = counts.get(tok, 0) + 1
    return counts


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
    """
    mark, proposed = explicit_of(body)
    humans = 0 if (mark and not proposed) else 2
    humans = {"High": 1, "Low": 3, "Hold for Review": 4, "Don't do": 5}.get(choice_of(body), humans)
    refs = _refs(entry_id, sibling_bodies, counts)
    age = age_days(_opened(body), today) or 0
    return (humans, -refs, -age, entry_id)


def reason(entry_id, body, sibling_bodies, today=None, counts=None, blockers=None):
    """Why this sits where it does - the sentence that makes the order defensible.
    """
    mark, proposed = explicit_of(body)
    if mark and not proposed:
        return "you marked it NOW"
    refs = _refs(entry_id, sibling_bodies, counts)
    age = age_days(_opened(body), today)
    bits = []
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
