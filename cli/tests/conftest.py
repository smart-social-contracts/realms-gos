"""Shared test fixtures for CLI tests that import realm_backend."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# realm_backend is not an installable package; add src/ to path for `realm_backend.ggg` imports
_src = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

VALENCIA_TREE = (
    Path(__file__).resolve().parents[2] / "tests/fixtures/legislation/valencia"
)


def run_git(repo: Path, *args: str) -> str:
    # -c commit.gpgsign=false: a developer's signing key must not be asked for in a throw-away repository
    command = ["git", "-c", "user.email=t@example.org", "-c", "user.name=t"]
    command += ["-c", "commit.gpgsign=false", "-C", str(repo), *args]
    return (
        subprocess.run(command, check=True, capture_output=True).stdout.decode().strip()
    )


@pytest.fixture
def git():
    """``git(repo, *args)`` for a throw-away repository: its output, and a commit made with no signing and no hooks."""
    return run_git


@pytest.fixture(scope="session")
def valencia_repo(tmp_path_factory) -> Path:
    """València's tree (``tests/fixtures/legislation/valencia``) as the ``legislation/`` folder of a throw-away git
    repository. Its first commit holds the chapters and its second the releases, which name the first: where a realm
    reads their chapters, as in realms-codices. Tests must not change it."""
    repo = tmp_path_factory.mktemp("valencia")
    run_git(repo, "init", "-q")
    shutil.copytree(VALENCIA_TREE / "eu", repo / "legislation" / "eu")
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-q", "--no-verify", "-m", "chapters")
    chapters = run_git(repo, "rev-parse", "HEAD")
    for file in (repo / "legislation").rglob("releases.json"):
        releases = json.loads(file.read_text())
        for release in releases:
            release["commit"] = chapters
        file.write_text(json.dumps(releases, ensure_ascii=False))
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-q", "--no-verify", "-m", "releases")
    return repo


@pytest.fixture
def valencia_copy(valencia_repo, tmp_path) -> Path:
    """A copy of ``valencia_repo`` a test may change."""
    return Path(shutil.copytree(valencia_repo, tmp_path / "valencia"))
