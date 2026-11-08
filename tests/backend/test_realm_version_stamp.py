"""A realm wasm reports a real version, never ``VERSION_PLACEHOLDER`` (realms-gos#551).

The version lives in ``src/realm_backend/api/setup.py`` and is stamped by ``scripts/pack_realm_backend.py``:
the release tag when there is one, otherwise the commit the wasm is packed from.
``scripts/publish_build.py`` hands the packer the version it publishes under. tests/deployed/test_build_variants.py
checks the packed wasm itself.
"""

import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
BACKEND = REPO_ROOT / "src" / "realm_backend"
SNAPSHOT = "local-abc12345"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def packer():
    return _load(REPO_ROOT / "scripts" / "pack_realm_backend.py", "_packer")


@pytest.fixture(scope="module")
def publish_build():
    sys.path.insert(0, str(REPO_ROOT / "cli"))
    return _load(REPO_ROOT / "scripts" / "publish_build.py", "_publish_build")


def stamped_version(setup_source):
    return re.search(r'^VERSION = "(.*)"$', setup_source, re.MULTILINE).group(1)


@pytest.mark.parametrize("version", ["v0.8.0", SNAPSHOT, "main.1749254400.abc1234"])
def test_stamping_rewrites_the_version_in_setup_py(packer, version):
    stamped = packer.stamp_version((BACKEND / "api" / "setup.py").read_text(), version)
    assert stamped_version(stamped) == version
    assert "VERSION_PLACEHOLDER" not in stamped


def test_stamping_fails_when_setup_py_has_no_placeholder_left(packer):
    with pytest.raises(SystemExit, match="no 'VERSION = \"VERSION_PLACEHOLDER\"' line"):
        packer.stamp_version('VERSION = "v0.7.0"\n', "v0.8.0")


def test_a_snapshot_is_named_after_its_commit(packer, monkeypatch):
    monkeypatch.setattr(packer.subprocess, "check_output", lambda *args, **kwargs: "abc12345\n")
    assert packer.snapshot_version() == SNAPSHOT


@pytest.mark.parametrize("failure", [FileNotFoundError(), subprocess.CalledProcessError(128, "git")])
def test_a_snapshot_outside_git_is_still_not_the_placeholder(packer, monkeypatch, failure):
    def no_git(*args, **kwargs):
        raise failure

    monkeypatch.setattr(packer.subprocess, "check_output", no_git)
    assert packer.snapshot_version() == "local"


@pytest.mark.parametrize("version, built", [("v0.8.0", "v0.8.0"), (None, SNAPSHOT)],
                         ids=["release tag", "snapshot"])
def test_packing_builds_with_the_stamped_version_and_restores_the_source(packer, tmp_path, monkeypatch, version, built):
    """basilisk packs whatever setup.py holds while it runs; afterwards the tracked file is untouched."""
    src = tmp_path / "realm_backend"
    (src / "api").mkdir(parents=True)
    shutil.copy(BACKEND / "api" / "setup.py", src / "api" / "setup.py")
    shutil.copy(BACKEND / "realm_backend.did", src / "realm_backend.did")
    versions_packed = []

    def build(*args, **kwargs):
        versions_packed.append(stamped_version((src / "api" / "setup.py").read_text()))

    monkeypatch.setattr(packer, "SRC", src)
    monkeypatch.setattr(packer, "snapshot_version", lambda: SNAPSHOT)
    monkeypatch.setattr(packer, "apply_cedar_template_env", dict)
    monkeypatch.setattr(packer, "select_build_variant", lambda variant: None)
    monkeypatch.setattr(packer, "verify_build_variant", lambda wasm, variant: None)
    monkeypatch.setattr(packer.subprocess, "run", build)

    packer.pack(version=version)

    assert versions_packed == [built]
    assert "VERSION_PLACEHOLDER" in (src / "api" / "setup.py").read_text()


def test_the_publish_command_gives_the_packer_its_version(publish_build, tmp_path, monkeypatch):
    packed = tmp_path / ".basilisk" / "realm_backend" / "realm_backend.wasm.gz"
    packed.parent.mkdir(parents=True)
    packed.touch()
    commands = []
    monkeypatch.setattr(publish_build, "_basilisk_python", lambda root, canister: "python")
    monkeypatch.setattr(publish_build, "_run", lambda cmd, **kwargs: commands.append(cmd))

    publish_build._build_realm_backend(tmp_path, version="v0.8.0")

    assert commands[0][-2:] == ["--version", "v0.8.0"]


@pytest.mark.parametrize("args, stamped", [
    (["--version", "0.8.0"], "v0.8.0"),
    (["--version", "0.8.0", "--variant", "test"], "v0.8.0"),
    (["--from-main"], "main.1749254400.abc1234"),
    (["--from-main", "--variant", "test"], "main.1749254400.abc1234"),
], ids=["release", "test release", "main snapshot", "test main snapshot"])
def test_a_published_realm_reports_the_version_it_is_published_under(publish_build, monkeypatch, args, stamped):
    built = []

    def build_backend(root, canister, main_py, variant, version):
        built.append(version)

    monkeypatch.setattr(publish_build, "main_build_version", lambda root: "main.1749254400.abc1234")
    monkeypatch.setattr(publish_build, "git_short_sha", lambda root: "abc1234")
    monkeypatch.setattr(publish_build, "_build_backend", build_backend)
    monkeypatch.setattr(publish_build, "_run", lambda cmd, **kwargs: None)
    monkeypatch.setattr(sys, "argv", ["publish_build.py", "--environment", "local", "--family", "realm",
                                      "--component", "backend", "--file-registry", "registry", *args])

    publish_build.main()

    assert built == [stamped]
