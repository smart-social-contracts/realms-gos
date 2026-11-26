#!/usr/bin/env bash
# Keeps one open GitHub issue per environment while the nightly live-site smoke is red, and
# closes it when a run is green. A red check nobody is told about gets ignored, and a real
# production fault then arrives unseen behind it.
#
# Environment: ENVIRONMENT (production|staging), JOB_STATUS (success|failure), RUN_URL,
# REPORT (the Playwright JSON report; may be missing if the run died early), and GH_TOKEN
# with `issues: write`.
set -euo pipefail

title="Live-site smoke is red: ${ENVIRONMENT}"

# One entry per failed check, with the first lines of its error, then how many did not run.
FAILURES='
def failing($prefix):
  (.specs[]? | select(.ok | not) |
    "- **\($prefix)\(.title)**\n\n```\n"
    + ([.tests[].results[].error.message // empty] | first // "no error message"
       | gsub("\u001b\\[[0-9;]*m"; "") | split("\n") | .[:12] | join("\n"))
    + "\n```"),
  (.suites[]? | failing($prefix + .title + " › "));
([.suites[] | failing("")] | join("\n\n")),
([.. | objects | select(has("status") and has("results")) | select(.status == "skipped")] | length) as $skipped
  | if $skipped > 0 then "\n\($skipped) check(s) did not run because a check they depend on failed." else empty end
'

issue_body() {
  echo "The nightly live-site smoke of **${ENVIRONMENT}** is red: ${RUN_URL}"
  echo
  if [ -f "${REPORT:-}" ]; then
    jq -r "$FAILURES" "$REPORT"
  else
    echo "The run failed before any check ran."
  fi
  echo
  echo "Kept up to date by \`.github/workflows/prod-smoke.yml\`; it closes itself when a run is green."
}

open_issue=$(gh issue list --state open --limit 200 --json number,title \
  --jq "map(select(.title == \"${title}\")) | first | .number // empty")

if [ "$JOB_STATUS" = "success" ]; then
  if [ -n "$open_issue" ]; then
    gh issue close "$open_issue" --comment "Green again: ${RUN_URL}"
  fi
elif [ -n "$open_issue" ]; then
  gh issue edit "$open_issue" --body "$(issue_body)"
else
  gh issue create --title "$title" --body "$(issue_body)"
fi
