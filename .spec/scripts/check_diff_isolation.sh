#!/usr/bin/env bash
# One-off closing check for the trace-isolation rule (see AGENTS.md §
# Aislamiento del rastro SDD): the lines this unit's diff *adds* outside
# `.spec/` must not name the unit that produced them.
#
# Not part of any versioned pytest suite and not invoked from the normal
# validation command: the comparison only makes sense against *this* unit's
# diff, before it merges. Run by hand at closing time, naming the unit's id
# and slug:
#
#   bash .spec/scripts/check_diff_isolation.sh <unit-id> <slug>
#
# Diffs the working tree against the point where this unit's work started
# off `master` (`git merge-base`) -- not against HEAD, because most of this
# unit's work is still uncommitted when this runs. Covers both tracked
# modifications (`git diff <merge-base>`, added lines only) and new,
# untracked files (their whole content counts as added). Restricts both to
# paths outside `.spec/`, and runs four checks against the combined stream.
# Each check must find nothing -- exit 1 from `grep` -- for this script to
# report success:
#
#   1. grep -F for the unit's numeric id
#   2. grep -F for the full unit slug (`<unit-id>-<slug>`)
#   3. grep -F for the unit-artifact path prefix
#   4. grep -E for a CA-NN acceptance-criterion reference
#
# A hit in any of the four means a piece of ephemeral SDD trace leaked into
# execution content, which AGENTS.md forbids outside `.spec/`.

set -uo pipefail

UNIT_ID="${1:?usage: check_diff_isolation.sh <unit-id> <slug>}"
SLUG="${2:?usage: check_diff_isolation.sh <unit-id> <slug>}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT" || exit 2

BASE_REF="master"
MERGE_BASE="$(git merge-base "$BASE_REF" HEAD 2>/dev/null)"
if [ -z "$MERGE_BASE" ]; then
  echo "check_diff_isolation: could not resolve a merge-base against $BASE_REF" >&2
  exit 2
fi

# Build caches (`.tsbuildinfo`, `node_modules/`, `__pycache__/`, Angular's
# compiler cache) are not execution content -- they hold hashes/paths that can
# coincidentally contain any of the three literals below, and their own
# unrelated churn is not this unit's diff to police. Excluded alongside
# `.spec/`.
EXCLUDE_PATHSPECS=(
  ':!.spec/'
  ':!node_modules/'
  ':!__pycache__/'
  ':!*.tsbuildinfo'
  ':!studio/app/.angular/'
)

TRACKED_ADDED="$(
  git diff "$MERGE_BASE" -- . "${EXCLUDE_PATHSPECS[@]}" 2>/dev/null \
    | grep -E '^\+' \
    | grep -vF '+++'
)"

UNTRACKED_FILES="$(git ls-files --others --exclude-standard -- . "${EXCLUDE_PATHSPECS[@]}" 2>/dev/null)"
UNTRACKED_CONTENT=""
if [ -n "$UNTRACKED_FILES" ]; then
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    UNTRACKED_CONTENT="$(printf '%s\n%s\n' "$UNTRACKED_CONTENT" "$(cat "$f" 2>/dev/null)")"
  done <<EOF
$UNTRACKED_FILES
EOF
fi

ADDED_LINES="$(printf '%s\n%s\n' "$TRACKED_ADDED" "$UNTRACKED_CONTENT")"

status=0

if printf '%s\n' "$ADDED_LINES" | grep -F "$UNIT_ID" >/dev/null; then
  echo "check_diff_isolation: found the unit id literal in added lines outside .spec/" >&2
  status=1
fi

if printf '%s\n' "$ADDED_LINES" | grep -F "${UNIT_ID}-${SLUG}" >/dev/null; then
  echo "check_diff_isolation: found the full unit slug literal in added lines outside .spec/" >&2
  status=1
fi

if printf '%s\n' "$ADDED_LINES" | grep -F ".spec/units/" >/dev/null; then
  echo "check_diff_isolation: found a .spec/units/ path literal in added lines outside .spec/" >&2
  status=1
fi

if printf '%s\n' "$ADDED_LINES" | grep -E 'CA-[0-9]{2}' >/dev/null; then
  echo "check_diff_isolation: found a CA-NN reference in added lines outside .spec/" >&2
  status=1
fi

if [ "$status" -eq 0 ]; then
  echo "check_diff_isolation: OK -- no trace leaked outside .spec/"
fi

exit "$status"
