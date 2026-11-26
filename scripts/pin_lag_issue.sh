#!/usr/bin/env bash
# Keeps one open GitHub issue while a submodule pin lags its repository's main (scripts/pin_lag.py), and closes it
# when a run finds none. One issue, updated in place: a new issue every morning gets muted, and then a pin can lag
# for weeks with nobody told.
#
# Environment: JOB_STATUS (success|failure), RUN_URL, REPORT (pin_lag.py's output; may be missing if the run died
# early), and GH_TOKEN with `issues: write`.
set -euo pipefail

title="Submodule pins lag behind main"

issue_body() {
  echo "A submodule pin of this repository lags its repository's main, so merged work is not in the product: ${RUN_URL}"
  echo
  if [ -s "${REPORT:-}" ]; then
    cat "$REPORT"
  else
    echo "The run failed before it compared any pin."
  fi
  echo
  echo "A pin lags when it is more than 10 commits behind, or main has held work it lacks for more than 2 days."
  echo "Kept up to date by \`.github/workflows/pin-lag.yml\`; it closes itself when no pin lags."
}

open_issue=$(gh issue list --state open --limit 200 --json number,title \
  --jq "map(select(.title == \"${title}\")) | first | .number // empty")

if [ "$JOB_STATUS" = "success" ]; then
  if [ -n "$open_issue" ]; then
    gh issue close "$open_issue" --comment "No pin lags: ${RUN_URL}"
  fi
elif [ -n "$open_issue" ]; then
  gh issue edit "$open_issue" --body "$(issue_body)"
else
  gh issue create --title "$title" --body "$(issue_body)"
fi
