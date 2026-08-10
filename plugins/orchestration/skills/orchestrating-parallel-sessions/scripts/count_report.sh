#!/usr/bin/env bash
# Print a verdict DERIVED from the result, never a label typed next to it.
#
# THE DEFECT THIS REMOVES, which bit three times in one session in o9's own shell output:
#
#     grep -n "pattern" file | head
#     echo "  (no output above = none left)"      <- prints whether or not the grep ran
#
# The label is a constant. It renders identically when the search found nothing, when the
# search errored (`grep: Invalid back reference`), and when the file did not exist. So the
# comfortable answer is printed for all three states, and only one of them is good news.
#
# ⭐ This is the single most repeated failure in this workstream - six occurrences across the
# corpus, in checks written by four different sessions - and every fix so far has been "make the
# check report what it EXAMINED." That works, but only if the author remembers. This makes the
# correct form the SHORTER one to type, which is the only version of "remember to" that holds.
#
# USAGE
#     source .shared/scripts/count_report.sh
#     counted "surviving copies of the dead formula" grep -rn "sha256(timestamp)" .
#
# Prints:
#     surviving copies of the dead formula: 3 match(es)          [rc=0]
#     surviving copies of the dead formula: NONE (0 match(es))   [rc=1, grep found nothing]
#     surviving copies of the dead formula: ⛔ SEARCH FAILED rc=2 - this is NOT "none"
#
# The third line is the whole point. A failed search and an empty search are different facts,
# and they must not be able to print the same words.

# gshow <ref> <path> - read a file at a git ref WITHOUT the mangling.
#
# ⛔ `git show <ref>:<path>` is mangled by Git Bash whenever the right of the colon begins with
# `.` or `/` - MSYS reads `a:b` as a Unix PATH list and rewrites it, so
# `origin/main:.shared/scripts/x.py` arrives as `origin\main;.shared\scripts\x.py`. It then
# reports a PRESENT file as missing, confidently, with an error that reads like a fact about
# your repository. Piped into `grep -c` it returns a clean, wrong `0`.
#
# ⭐ THIS EXISTS BECAUSE KNOWING THE RULE IS NOT ENOUGH. Within an hour of documenting the bug,
# building a probe for it and sending the corrected condition to another orchestrator, I typed
# the broken form in my next verification and believed the answer - it said a pushed change was
# absent from origin, which would have meant redoing finished work. The broken form is simply
# the one that comes to hand.
#
#     gshow origin/main .shared/scripts/safe_push.py | grep -c "something"
gshow() {
  local ref="$1" path="$2" blob
  blob=$(git ls-tree "$ref" -- "$path" | awk '{print $3}')
  if [ -z "$blob" ]; then
    # NOT the same as an empty file, and it must not print like one.
    printf "  gshow: %s is NOT at %s (or the ref is unknown) - this is NOT empty content\n" \
      "$path" "$ref" >&2
    return 2
  fi
  git cat-file -p "$blob"
}

counted() {
  local label="$1"; shift
  local out rc n
  out=$("$@" 2>&1); rc=$?

  # grep-family: rc 0 = found, 1 = none, >=2 = error. Anything else: nonzero = error.
  if [ "$rc" -ge 2 ]; then
    printf "  %s: ⛔ SEARCH FAILED rc=%d - this is NOT \"none\"\n" "$label" "$rc"
    printf "     %s\n" "$(printf '%s' "$out" | head -1 | cut -c1-88)"
    return 2
  fi

  if [ -z "$out" ]; then
    printf "  %s: NONE (0 match(es), search ran clean)\n" "$label"
    return 1
  fi

  n=$(printf '%s\n' "$out" | grep -c .)
  printf "  %s: %d match(es)\n" "$label" "$n"
  printf '%s\n' "$out" | head -8 | sed 's/^/       /'
  [ "$n" -gt 8 ] && printf "       ... and %d more\n" "$((n - 8))"
  return 0
}
