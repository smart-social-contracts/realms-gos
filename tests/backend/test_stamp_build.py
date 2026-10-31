"""scripts/stamp_build.py fills in the build provenance status() and /version report."""

import importlib.util
import re
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
# The realm backend has no status.py or version_http.py: its version is stamped into
# api/setup.py by scripts/pack_realm_backend.py.
BACKENDS = ("marketplace_backend",)


@pytest.fixture
def stamp_build(tmp_path, monkeypatch):
    """The script, pointed at a copy of the backends' stamped files."""
    spec = importlib.util.spec_from_file_location(
        "_stamp_build", REPO_ROOT / "scripts" / "stamp_build.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for canister in BACKENDS:
        for rel in module.FILES:
            dest = tmp_path / "src" / canister / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(REPO_ROOT / "src" / canister / rel, dest)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    git = {
        ("rev-parse", "--short", "HEAD"): "abc1234",
        ("rev-parse", "HEAD"): "abc1234" + "0" * 33,
        ("log", "-1", "--format=%cI"): "2026-10-29T13:19:19+00:00",
    }
    monkeypatch.setattr(module, "_git", lambda _repo, *args: git.get(args))
    versions = {
        "ic-basilisk": "0.15.1",
        "ic-basilisk-toolkit": "0.5.0",
        "ic-python-db": "0.14.0",
        "ic-python-logging": "0.3.4",
    }
    monkeypatch.setattr(module, "_package_version", versions.get)
    return module


def _read(module, canister, rel):
    return (module.ROOT / "src" / canister / rel).read_text()


@pytest.mark.parametrize("canister", BACKENDS)
def test_a_release_build_has_no_placeholders_left(stamp_build, canister):
    stamp_build.stamp(canister, "v0.8.0")
    status = _read(stamp_build, canister, "api/status.py")
    left = set(stamp_build.TOKEN.findall(status)) - {
        # The extensions submodule isn't checked out in this copy
        "EXTENSIONS_COMMIT_HASH_PLACEHOLDER",
        "EXTENSIONS_COMMIT_DATETIME_PLACEHOLDER",
    }
    assert not left
    assert '"ic-python-db==0.14.0"' in status
    assert '"ic-basilisk-toolkit==0.5.0"' in status
    assert '_stamped("v0.8.0")' in status
    version_http = _read(stamp_build, canister, "api/version_http.py")
    assert not stamp_build.TOKEN.findall(version_http)
    assert '_SHA_STAMP = "abc1234"' in version_http


def test_the_version_never_lands_inside_a_dependency_placeholder(stamp_build):
    stamp_build.stamp("marketplace_backend", "v0.8.0")
    status = _read(stamp_build, "marketplace_backend", "api/status.py")
    assert "v0.8.0" not in "".join(re.findall(r'"ic-[^"]*"', status))


def test_unknown_values_keep_their_placeholder(stamp_build, monkeypatch):
    monkeypatch.setattr(stamp_build, "_package_version", lambda _name: None)
    stamp_build.stamp("marketplace_backend", None)  # a snapshot: no version
    status = _read(stamp_build, "marketplace_backend", "api/status.py")
    assert '"ic-python-db==IC_PYTHON_DB_VERSION_PLACEHOLDER"' in status
    assert '_stamped("VERSION_PLACEHOLDER")' in status
    # status() still recognises what is left unstamped
    assert 'return "" if "PLACEHOLDER" in value else value' in status


def test_every_placeholder_in_status_is_one_the_script_knows(stamp_build):
    known = set(stamp_build.stamp_values("v0.8.0")) | {
        "EXTENSIONS_COMMIT_HASH_PLACEHOLDER",
        "EXTENSIONS_COMMIT_DATETIME_PLACEHOLDER",
    }
    for canister in BACKENDS:
        for rel in stamp_build.FILES:
            found = set(stamp_build.TOKEN.findall(_read(stamp_build, canister, rel)))
            assert found <= known, f"{canister}/{rel}: {found - known}"
