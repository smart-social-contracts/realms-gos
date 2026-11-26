#!/usr/bin/env python3
"""Whether each submodule pin lags its repository's main, so that merged work is missing from the product.

A pin lags when the main it tracks holds more than MAX_COMMITS commits the pin does not, or holds one it has held for
more than MAX_DAYS. How long a commit has been on main is the commit time of the first-parent commit that brought it
there: a pull request's merge commit, not the older commits of its branch.

Prints one Markdown line per submodule and exits 1 when any pin lags. A submodule whose main or pin cannot be read
lags too: a check that cannot see what main holds must not say the pin is current.

    python3 scripts/pin_lag.py          # from the repository root

PIN_LAG_NOW (Unix seconds) stands in for the clock, for tests.
"""

import os
import subprocess
import sys
import tempfile
import time

MAX_COMMITS = 10
MAX_DAYS = 2


def git(*args: str, cwd: str = ".") -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def submodules() -> list:
    """(path, url, branch) of each submodule in .gitmodules; a submodule that names no branch tracks main."""
    found = []
    for line in git("config", "--file", ".gitmodules", "--get-regexp", r"^submodule\..*\.path$").splitlines():
        key, path = line.split(" ", 1)
        name = key[len("submodule.") : -len(".path")]
        url = git("config", "--file", ".gitmodules", f"submodule.{name}.url")
        branch = git("config", "--file", ".gitmodules", "--default", "main", f"submodule.{name}.branch")
        found.append((path, url, branch))
    return found


def lag(path: str, url: str, branch: str, now: float, scratch: str) -> tuple:
    """(lags, the Markdown line that says why) for the submodule at ``path``, read into a bare repository in ``scratch``."""
    pin = git("rev-parse", f"HEAD:{path}")
    where = f"`{path}` (pinned at `{pin[:8]}`)"
    repo = os.path.join(scratch, path.replace("/", "_"))
    git("init", "--quiet", "--bare", repo)
    try:
        git("fetch", "--quiet", url, f"+refs/heads/{branch}:refs/heads/{branch}", cwd=repo)
    except subprocess.CalledProcessError as e:
        reason = (e.stderr.strip().splitlines() or ["no message"])[-1]
        return True, f"- **{where}: cannot read {branch} of {url}**, so how far it lags is unknown: {reason}"
    tip = git("rev-parse", branch, cwd=repo)
    if subprocess.run(["git", "merge-base", "--is-ancestor", pin, tip], cwd=repo, capture_output=True,
                      check=False).returncode:
        return True, f"- **{where}: the pin is not on {branch} of {url}**, so how far it lags cannot be counted"
    behind = int(git("rev-list", "--count", f"{pin}..{tip}", cwd=repo))
    if behind == 0:
        return False, f"- {where}: at {branch} of {url}"
    oldest = int(git("log", "--first-parent", "--format=%ct", f"{pin}..{tip}", cwd=repo).splitlines()[-1])
    commits = "1 commit" if behind == 1 else f"{behind} commits"
    said = (f"{commits} behind {branch} of {url} (`{tip[:8]}`), the oldest on {branch} for "
            f"{(now - oldest) / 86400:.1f} days")
    over = []
    if behind > MAX_COMMITS:
        over.append(f"more than {MAX_COMMITS} commits")
    if now - oldest > MAX_DAYS * 86400:
        over.append(f"merged more than {MAX_DAYS} days ago")
    if over:
        return True, f"- **{where} lags**: {said}: {' and '.join(over)}"
    return False, f"- {where}: {said}, within the limits ({MAX_COMMITS} commits, {MAX_DAYS} days)"


def main() -> int:
    now = float(os.environ.get("PIN_LAG_NOW") or time.time())
    any_lags = False
    with tempfile.TemporaryDirectory() as scratch:
        for path, url, branch in submodules():
            lags, line = lag(path, url, branch, now, scratch)
            print(line)
            any_lags = any_lags or lags
    return 1 if any_lags else 0


if __name__ == "__main__":
    sys.exit(main())
