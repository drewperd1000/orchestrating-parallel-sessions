#!/usr/bin/env python3
"""What a live credential looks like INSIDE a file - the one list, for any tool that must not
show, copy or serve one.

<!-- subject: secrets-and-access -->

<!-- route-tags: secret credential token webhook password private key scan content leak -->

ORIGIN, 2026-09-23 (security review of the OrchDoc Reader's file viewer). The viewer refused
files by NAME and by FOLDER only, and three files with innocent names were served with live
credentials in them: `.claude/settings.local.json` (Slack webhook URLs) and two seed scripts
(a production database password inside a `scheme://user:password@host` URL). A name rule cannot
see those, so the content has to be looked at.

When this was written no other list of these shapes existed under `.shared/scripts/` - checked
by grep before writing it. Add a shape HERE, not in a second list beside the tool that needs it.

    import secret_patterns
    kind = secret_patterns.find_credential(text)   # None, or a plain label such as
                                                    # "a Slack webhook URL"

⛔ THE RESULT NEVER CARRIES THE MATCHED TEXT. A caller that reports a refusal reports the
KIND, so the refusal cannot become the leak it exists to prevent.

⛔ IT FAILS TOWARD REFUSING. A shape that only looks like a credential is refused along with the
real ones. The one exception is a URL whose password is plainly a placeholder - `<pw>`,
`${DB_PASSWORD}`, `{password}`, `%s`, `****` - because the docs that explain how to connect are
written that way and would otherwise all be unreadable.
"""
import re

# Built from pieces so this file does not itself match a scanner looking for these shapes.
_PK_BEGIN = "-" * 5 + "BEGIN"

PATTERNS = [
    ("a URL with a password in it",
     re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]{1,20}://[^\s:/@'\"`<>]*:"
                r"(?![<${%*]|x{3}|X{3})[^\s@/'\"`]+@[^\s/@'\"`]+")),
    ("a Slack webhook URL", re.compile(r"hooks\.slack\.com/services/[A-Za-z0-9]")),
    ("a private key", re.compile(re.escape(_PK_BEGIN) + r" [A-Z0-9 ]*PRIVATE KEY-{5}")),
    ("an Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{8,}")),
    ("a GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("a GitHub token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("an AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("a Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{8,}")),
    # A credential in a URL's query string: a sign-in link's token, an API key, a signed URL's
    # signature. The value must be 8+ characters and hold a digit, or be 24+ characters, so
    # `?key=script-title` is not refused; a placeholder value (`<jwt>`, `${TOKEN}`, `YOUR_KEY`,
    # `xxx`, `...`) is not refused either. The `keep` group is the parameter name, which
    # redact() leaves in place so a redacted link still shows what it was.
    ("a credential in a URL's query string",
     re.compile(r"(?i)(?P<keep>[?&](?:(?:access|refresh|id|auth|session)_)?(?:token|api[_-]?key"
                r"|apikey|key|(?:client_)?secret|password|passwd|pwd|auth|sig|signature"
                r"|x-amz-(?:signature|credential|security-token))=)"
                r"(?![<${%*]|x{3}|X{3}|your_|\.\.\.|\[)"
                r"(?=[^&#\s\"'<>)\]]{24}|[^&#\s\"'<>)\]]*\d)[^&#\s\"'<>)\]]{8,}")),
]


def find_credential(text):
    """The KIND of the first credential-shaped thing in `text`, or None. Never the value."""
    if not text:
        return None
    for label, rx in PATTERNS:
        if rx.search(text):
            return label
    return None


def redact(text):
    """`text` with every credential-shaped string replaced by "[redacted]". A shape whose regex
    has a `keep` group (the query-string shape: the parameter name) keeps that group's text in
    front of the marker."""
    if not text:
        return text
    for _label, rx in PATTERNS:
        if "keep" in rx.groupindex:
            text = rx.sub(lambda m: m.group("keep") + "[redacted]", text)
        else:
            text = rx.sub("[redacted]", text)
    return text
