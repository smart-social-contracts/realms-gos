"""Contract tests for codex packages (codex_api_version 2).

Every publishable codex under ``codices/codices/`` is a ``kind: codex`` package:

  - the manifest declares the hook API this realm runs
  - the code is one file, ``backend/hooks.py``, whose ``@hook`` functions are
    exactly the manifest's ``hooks``, all of them known to the realm
  - ``capabilities`` and the shape of the rest are plain data
  - every Python source parses

What a codex's hooks do against a running realm is tested in
``tests/ggg/test_codex.py``, which also checks that every capability is a
realm operation.
"""

import ast
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
CODICES_ROOT = REPO_ROOT / "codices" / "codices"

# The same number as API in src/realm_backend/api/codex.py: a realm refuses any other.
CODEX_API_VERSION = 2
# The hooks the realm fires (docs/codices/README.md, "Hooks").
KNOWN_HOOKS = {"init", "on_user_register", "on_stage_change", "assign_quarter"}

# Shared files, not publishable codex packages.
SKIP_DIRS = {"_common", "common"}


def _codex_dirs():
    if not CODICES_ROOT.is_dir():
        return []
    return sorted(
        d for d in CODICES_ROOT.iterdir()
        if d.is_dir()
        and d.name not in SKIP_DIRS
        and not d.name.startswith(".")
        and (d / "manifest.json").exists()
    )


def _manifest(codex_dir):
    with open(codex_dir / "manifest.json") as f:
        return json.load(f)


def _hook_functions(codex_dir):
    """Names of the top-level functions decorated with ``@hook`` in backend/hooks.py."""
    tree = ast.parse((codex_dir / "backend" / "hooks.py").read_text())
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and any(isinstance(d, ast.Name) and d.id == "hook" for d in node.decorator_list)
    }


CODEX_DIRS = _codex_dirs()
CODEX_IDS = [d.name for d in CODEX_DIRS]


@pytest.fixture(params=CODEX_DIRS, ids=CODEX_IDS)
def codex_dir(request):
    return request.param


def test_codices_discovered():
    """The known first-party codices are present (submodule checked out)."""
    if not CODICES_ROOT.is_dir():
        pytest.skip("codices submodule not checked out")
    assert {"agora", "syntropia", "dominion"}.issubset(set(CODEX_IDS))


class TestManifestContract:
    def test_kind_is_codex(self, codex_dir):
        assert _manifest(codex_dir).get("kind") == "codex"

    def test_api_version_is_the_one_the_realm_runs(self, codex_dir):
        assert _manifest(codex_dir).get("codex_api_version") == CODEX_API_VERSION

    def test_machine_id_matches_directory(self, codex_dir):
        """The registry namespace key is the lowercase machine id: it must
        match the package directory, never a capitalized display name."""
        manifest = _manifest(codex_dir)
        assert manifest.get("id") == codex_dir.name
        assert manifest["id"] == manifest["id"].lower()

    def test_has_version(self, codex_dir):
        assert _manifest(codex_dir).get("version")

    def test_capabilities_are_operation_names(self, codex_dir):
        capabilities = _manifest(codex_dir).get("capabilities", [])
        assert isinstance(capabilities, list)
        assert all(isinstance(name, str) and name for name in capabilities)

    def test_dependencies_shape(self, codex_dir):
        deps = _manifest(codex_dir).get("dependencies", [])
        assert isinstance(deps, (list, dict))
        names = list(deps.keys()) if isinstance(deps, dict) else deps
        for name in names:
            assert isinstance(name, str) and name


class TestPackageLayout:
    def test_hooks_file_exists(self, codex_dir):
        assert (codex_dir / "backend" / "hooks.py").exists()

    def test_no_code_of_the_previous_hook_api(self, codex_dir):
        """API 1 shipped backend/entry.py and a root init.py; API 2 runs only backend/hooks.py."""
        assert not (codex_dir / "backend" / "entry.py").exists()
        assert not (codex_dir / "init.py").exists()

    def test_no_loose_python_at_package_root(self, codex_dir):
        """All code ships under backend/."""
        loose = [
            p.name for p in codex_dir.iterdir()
            if p.suffix == ".py" and not p.name.startswith("test")
        ]
        assert loose == [], f"loose .py files outside backend/: {loose}"

    def test_all_python_sources_parse(self, codex_dir):
        for path in (codex_dir / "backend").rglob("*.py"):
            ast.parse(path.read_text(), filename=str(path))


class TestHookContract:
    def test_manifest_hooks_are_known(self, codex_dir):
        hooks = _manifest(codex_dir).get("hooks", [])
        unknown = set(hooks) - KNOWN_HOOKS
        assert not unknown, f"hooks the realm never fires: {sorted(unknown)}"

    def test_manifest_hooks_are_the_hook_functions(self, codex_dir):
        """A hook missing from the manifest is never fired; a listed one that is not defined fails when fired."""
        assert set(_manifest(codex_dir).get("hooks", [])) == _hook_functions(codex_dir)
