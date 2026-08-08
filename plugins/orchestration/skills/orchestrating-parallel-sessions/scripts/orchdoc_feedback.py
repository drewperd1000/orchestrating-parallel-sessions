"""Compose a pre-filled GitHub issue so reporting a defect costs one command.

WHY BOTHER, when the repo already has issue templates: because the gap between "I should
report this" and "I have reported this" is where reports die. By the time someone has opened a
browser, found the repo, chosen a template and re-typed what happened, the moment has passed
and they have moved on - and the report that would have been most useful is the one from
someone mid-task who was in a hurry.

⭐ THE REPORTS THAT MATTER MOST COME FROM PEOPLE WHO ARE BUSY. Nearly every fix in this
toolchain came from an orchestrator running a check against a real document, finding it
confidently wrong, and stopping to say so - while blocked on something else. This makes that
stop cost one command.

  feedback wrong  "<what it said>" --actually "<what was true>"
  feedback fit    "<what it assumed>" --actually "<what is true for you>"

It prints a URL. It sends nothing, opens nothing, and contacts no one - **you paste the URL if
and when you want to.** A tool that filed a report on your behalf would be reading your
workspace and publishing it, which is not a thing this should ever do quietly.
"""
import argparse
import pathlib
import subprocess
import sys
import urllib.parse

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO = "<your-github-user>/orchestrating-parallel-sessions"
HERE = pathlib.Path(__file__).resolve().parent


def version():
    """Best-effort provenance. Absent is fine; wrong would not be."""
    for args in (["log", "--oneline", "-1"], ["describe", "--tags", "--always"]):
        r = subprocess.run(["git", "-C", str(HERE)] + args, capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()[:80]
    return "(unknown)"


TEMPLATES = {
    "wrong": ("it-was-wrong.md", "[wrong] ", """## What it said

```
{what}
```

## What was actually true

{actually}

## What did you have to do to get past it?

{workaround}

## Version

{version}
"""),
    "fit": ("it-did-not-fit.md", "[fit] ", """## What it assumed

{what}

## What is actually true for me

{actually}

## Did you find a way around it?

{workaround}

## Version

{version}
"""),
}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("kind", choices=sorted(TEMPLATES))
    ap.add_argument("what", help="what it said, or what it assumed")
    ap.add_argument("--actually", default="", help="what was actually true")
    ap.add_argument("--workaround", default="",
                    help="what you had to do to get past it - the most useful field")
    ap.add_argument("--title", default="", help="issue title; one is derived if omitted")
    a = ap.parse_args()

    tmpl, prefix, body_fmt = TEMPLATES[a.kind]
    body = body_fmt.format(
        what=a.what.strip(),
        actually=a.actually.strip() or "_(not filled in)_",
        workaround=a.workaround.strip() or "_(not filled in)_",
        version=version())

    title = a.title.strip() or (prefix + " ".join(a.what.split())[:64])
    url = "https://github.com/%s/issues/new?%s" % (REPO, urllib.parse.urlencode({
        "template": tmpl, "title": title, "body": body}))

    print("Paste this into a browser to open the report, pre-filled:")
    print()
    print(url)
    print()
    if not a.workaround.strip():
        print("⭐ Worth adding `--workaround \"...\"` before you send it. If the only way past")
        print("   was to change something that was CORRECT - reword a record, revert a status,")
        print("   delete a reference - that is the detail that changes the design instead of")
        print("   patching a regex. It is the single most useful field on the form.")
        print()
    print("Nothing has been sent. This composes a URL and does nothing else.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
