"""The venv a canister is built in always holds the pins: a stale one would build a stale wasm (ic-python-db#36)."""

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
PUBLISH_BUILD = REPO_ROOT / "scripts" / "publish_build.py"


@pytest.fixture(scope="module")
def publish_build():
    spec = importlib.util.spec_from_file_location("_publish_build_venv", PUBLISH_BUILD)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(REPO_ROOT / "cli"))
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def commands(publish_build, monkeypatch):
    """The commands the build would run."""
    ran = []
    monkeypatch.setattr(publish_build, "_run", lambda cmd, **kw: ran.append([str(c) for c in cmd]))
    return ran


def test_an_existing_venv_gets_the_pins_installed_again(publish_build, commands, tmp_path):
    python = tmp_path / ".venv-basilisk" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("")

    assert publish_build._basilisk_python(tmp_path, "realm_backend") == str(python)

    assert commands == [[str(python.parent / "pip"), "install", "-q", "-r", str(tmp_path / "requirements.txt")]]


def test_a_new_venv_is_created_and_gets_the_pins(publish_build, commands, tmp_path):
    publish_build._basilisk_python(tmp_path, "realm_backend")

    venv = tmp_path / ".venv-basilisk"
    pip = str(venv / "bin" / "pip")
    assert [c[1:] for c in commands] == [["-m", "venv", str(venv)], ["install", "-q", "--upgrade", "pip"],
                                         ["install", "-q", "-r", str(tmp_path / "requirements.txt")]]
    assert commands[1][0] == commands[2][0] == pip


def test_a_canister_with_its_own_pins_has_its_own_venv(publish_build, commands, tmp_path):
    pinned = tmp_path / "src" / "marketplace_backend" / "requirements.txt"
    pinned.parent.mkdir(parents=True)
    pinned.write_text("ic-python-db==0.14.0\n")
    python = tmp_path / ".venv-basilisk-marketplace_backend" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("")

    assert publish_build._basilisk_python(tmp_path, "marketplace_backend") == str(python)

    assert commands == [[str(python.parent / "pip"), "install", "-q", "-r", str(pinned)]]


def test_the_realm_is_published_on_the_pins_its_release_is_packed_with(publish_build, commands):
    """release.yml packs the release asset after ``pip install -r requirements.txt``; the registry's build must match."""
    release = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text()
    assert "pip install -r requirements.txt" in release
    publish_build._basilisk_python(REPO_ROOT, "realm_backend")
    assert commands[-1][-2:] == ["-r", str(REPO_ROOT / "requirements.txt")]
