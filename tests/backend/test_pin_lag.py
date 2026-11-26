"""scripts/pin_lag.py and scripts/pin_lag_issue.sh, on repositories built here with known commits and times.

Each submodule repository is a real git repository whose commits have set commit times, and the clock is fixed
(PIN_LAG_NOW), so both verdicts and both boundaries (10 commits, 2 days) are tested, not only whatever the real
repositories happen to show on the day.
"""

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
PIN_LAG = REPO_ROOT / "scripts" / "pin_lag.py"
PIN_LAG_ISSUE = REPO_ROOT / "scripts" / "pin_lag_issue.sh"

NOW = 1_800_000_000
HOUR, DAY = 3600, 86400


def git(cwd, *args, at=NOW):
    """Run git in ``cwd``, with ``at`` (Unix seconds) as the time of any commit it makes."""
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.org", "GIT_AUTHOR_DATE": f"@{at} +0000",
           "GIT_COMMITTER_DATE": f"@{at} +0000"}
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()


def commit(repo, age, message="work"):
    """An empty commit made ``age`` seconds before NOW; returns its sha."""
    git(repo, "commit", "--quiet", "--allow-empty", "-m", message, at=NOW - age)
    return git(repo, "rev-parse", "HEAD")


def upstream(tmp_path, name, *ages):
    """A submodule repository whose main has one commit per age (seconds before NOW), oldest first; its shas."""
    repo = tmp_path / name
    repo.mkdir()
    git(repo, "init", "--quiet", "-b", "main")
    return repo, [commit(repo, age) for age in ages]


def realms(tmp_path, *submodules):
    """A repository whose submodules are (path, url, pin[, branch]); its directory."""
    repo = tmp_path / "realms"
    repo.mkdir()
    git(repo, "init", "--quiet", "-b", "main")
    entries = []
    for path, url, pin, *branch in submodules:
        entries.append(f'[submodule "{path}"]\n\tpath = {path}\n\turl = {url}\n')
        if branch:
            entries.append(f"\tbranch = {branch[0]}\n")
        git(repo, "update-index", "--add", "--cacheinfo", f"160000,{pin},{path}")
    (repo / ".gitmodules").write_text("".join(entries))
    git(repo, "add", ".gitmodules")
    git(repo, "commit", "--quiet", "-m", "pins")
    return repo


def pin_lag(repo):
    """pin_lag.py run in ``repo`` at NOW: (exit status, output)."""
    run = subprocess.run([sys.executable, str(PIN_LAG)], cwd=repo, env={**os.environ, "PIN_LAG_NOW": str(NOW)},
                         capture_output=True, text=True, check=False)
    assert not run.stderr, run.stderr
    return run.returncode, run.stdout


def test_a_pin_at_main_is_quiet(tmp_path):
    sub, shas = upstream(tmp_path, "sub", 30 * DAY)
    status, out = pin_lag(realms(tmp_path, ("sub", sub, shas[-1])))
    assert status == 0
    assert out == f"- `sub` (pinned at `{shas[-1][:8]}`): at main of {sub}\n"


def test_ten_commits_behind_is_quiet(tmp_path):
    sub, shas = upstream(tmp_path, "sub", *[HOUR] * 11)
    status, out = pin_lag(realms(tmp_path, ("sub", sub, shas[0])))
    assert status == 0
    assert "10 commits behind main" in out and "within the limits (10 commits, 2 days)" in out


def test_eleven_commits_behind_lags(tmp_path):
    sub, shas = upstream(tmp_path, "sub", *[HOUR] * 12)
    status, out = pin_lag(realms(tmp_path, ("sub", sub, shas[0])))
    assert status == 1
    assert out.startswith(f"- **`sub` (pinned at `{shas[0][:8]}`) lags**: 11 commits behind main")
    assert out.rstrip().endswith(": more than 10 commits")


def test_work_on_main_for_exactly_two_days_is_quiet(tmp_path):
    sub, shas = upstream(tmp_path, "sub", 10 * DAY, 2 * DAY)
    status, out = pin_lag(realms(tmp_path, ("sub", sub, shas[0])))
    assert status == 0
    assert "1 commit behind main" in out and "for 2.0 days, within the limits" in out


def test_work_on_main_for_more_than_two_days_lags(tmp_path):
    sub, shas = upstream(tmp_path, "sub", 10 * DAY, 2 * DAY + 1)
    status, out = pin_lag(realms(tmp_path, ("sub", sub, shas[0])))
    assert status == 1
    assert out.rstrip().endswith("lags**: 1 commit behind main of " + f"{sub} (`{shas[1][:8]}`), the oldest on main "
                                 "for 2.0 days: merged more than 2 days ago")


def test_a_branch_merged_today_counts_from_its_merge_not_from_its_older_commits(tmp_path):
    sub, shas = upstream(tmp_path, "sub", 10 * DAY)
    git(sub, "switch", "--quiet", "-c", "feature")
    commit(sub, 5 * DAY, "written five days ago")
    git(sub, "switch", "--quiet", "main")
    git(sub, "merge", "--quiet", "--no-ff", "-m", "merged an hour ago", "feature", at=NOW - HOUR)
    status, out = pin_lag(realms(tmp_path, ("sub", sub, shas[0])))
    assert status == 0
    assert "2 commits behind main" in out and "the oldest on main for 0.0 days" in out


def test_a_main_that_cannot_be_read_lags_and_is_named(tmp_path):
    missing = tmp_path / "no-such-repository"
    status, out = pin_lag(realms(tmp_path, ("sub", missing, "1" * 40)))
    assert status == 1
    assert out.startswith(f"- **`sub` (pinned at `11111111`): cannot read main of {missing}**, so how far it lags "
                          "is unknown: ")


def test_a_pin_that_is_not_on_main_lags(tmp_path):
    sub, _ = upstream(tmp_path, "sub", 10 * DAY)
    git(sub, "switch", "--quiet", "-c", "unmerged")
    stray = commit(sub, DAY)
    git(sub, "switch", "--quiet", "main")
    status, out = pin_lag(realms(tmp_path, ("sub", sub, stray)))
    assert status == 1
    assert f"**`sub` (pinned at `{stray[:8]}`): the pin is not on main of {sub}**" in out


def test_a_submodule_is_compared_with_the_branch_gitmodules_names(tmp_path):
    sub, shas = upstream(tmp_path, "sub", 10 * DAY)
    git(sub, "branch", "release")
    for _ in range(11):
        commit(sub, 5 * DAY)
    status, out = pin_lag(realms(tmp_path, ("sub", sub, shas[0], "release")))
    assert status == 0
    assert out == f"- `sub` (pinned at `{shas[0][:8]}`): at release of {sub}\n"


def test_one_lagging_pin_fails_the_run_and_every_pin_is_listed(tmp_path):
    # The lagging pin comes first, so a current pin after it cannot clear the verdict.
    behind, behind_shas = upstream(tmp_path, "behind", *[HOUR] * 12)
    current, current_shas = upstream(tmp_path, "current", DAY)
    status, out = pin_lag(realms(tmp_path, ("behind", behind, behind_shas[0]), ("current", current, current_shas[-1])))
    assert status == 1
    lines = out.splitlines()
    assert lines[0].startswith(f"- **`behind` (pinned at `{behind_shas[0][:8]}`) lags**")
    assert lines[1] == f"- `current` (pinned at `{current_shas[-1][:8]}`): at main of {current}"


# pin_lag_issue.sh, with a gh that records what it is asked and answers `issue list` with OPEN_ISSUE.
FAKE_GH = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$GH_CALLS"
if [ "$1 $2" = "issue list" ]; then echo "${OPEN_ISSUE:-}"; fi
"""


def issue_upkeep(tmp_path, status, open_issue="", report="- **`ggg` (pinned at `54196732`) lags**: 34 commits"):
    """pin_lag_issue.sh after a run that ended ``status``: the gh commands it ran, one per line."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(FAKE_GH)
    (bin_dir / "gh").chmod(0o755)
    (tmp_path / "pin-lag.md").write_text(report)
    calls = tmp_path / "gh-calls"
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "GH_CALLS": str(calls), "OPEN_ISSUE": open_issue,
           "JOB_STATUS": status, "RUN_URL": "https://example.org/run/1", "REPORT": str(tmp_path / "pin-lag.md")}
    subprocess.run(["bash", str(PIN_LAG_ISSUE)], env=env, check=True, capture_output=True, text=True)
    return calls.read_text()


def test_a_lagging_run_opens_the_issue_with_the_report(tmp_path):
    calls = issue_upkeep(tmp_path, "failure")
    assert "issue create --title Submodule pins lag behind main --body" in calls
    assert "34 commits" in calls and "https://example.org/run/1" in calls


def test_a_lagging_run_updates_the_open_issue_instead_of_opening_another(tmp_path):
    calls = issue_upkeep(tmp_path, "failure", open_issue="7")
    assert "issue edit 7 --body" in calls and "34 commits" in calls
    assert "issue create" not in calls


def test_a_quiet_run_closes_the_open_issue(tmp_path):
    calls = issue_upkeep(tmp_path, "success", open_issue="7")
    assert calls.splitlines()[-1] == "issue close 7 --comment No pin lags: https://example.org/run/1"


def test_a_quiet_run_with_no_open_issue_opens_none(tmp_path):
    calls = issue_upkeep(tmp_path, "success").splitlines()
    assert len(calls) == 1 and calls[0].startswith("issue list --state open"), calls


def test_a_run_that_died_before_comparing_says_so(tmp_path):
    calls = issue_upkeep(tmp_path, "failure", report="")
    assert "issue create" in calls and "The run failed before it compared any pin." in calls
