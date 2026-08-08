---
name: A check was wrong
about: A gate, check or verb told you something untrue - or refused work that was correct
title: "[wrong] "
labels: wrong
---

**This is the most valuable report you can file.** Nearly every fix in this tooling came from
someone running a check against a real document and finding it confidently wrong. A synthetic
fixture cannot produce the case your project actually hit.

## What it said

```
paste the output
```

## What was actually true

<!-- What the doc/repo/state really was. Be specific - the gap between those two lines is the bug. -->

## What did you have to do to get past it?

<!-- ⭐ Please answer this one even if it feels obvious.
     If the only way forward was to change something that was CORRECT - reword a record,
     revert a status, delete a reference - say so. A finding whose remedy cannot be performed
     produces a workaround, and a workaround is a defect that has learned to pass. Those are
     the reports that change the design rather than patch a regex. -->

## Version

<!-- `git -C <the plugin dir> log --oneline -1`, or the plugin.json version -->
