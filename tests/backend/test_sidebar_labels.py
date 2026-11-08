"""Sidebar label catalog contract and merge behavior (issue #393)."""

from __future__ import annotations

import importlib.util
import json
import re
from collections import OrderedDict
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
EXTENSIONS_ROOT = REPO_ROOT / "extensions" / "extensions"
SCRIPT_PATH = REPO_ROOT / "scripts" / "add_sidebar_labels.py"
CATALOG_PATH = REPO_ROOT / "src" / "realm_frontend" / "src" / "lib" / "i18n" / "realmLocales.ts"


def _catalog_ids():
    """The realm's language catalog lives in the frontend (the backend keeps no copy of it)."""
    return re.findall(r"\{ id: '([^']+)', name:", CATALOG_PATH.read_text(encoding="utf-8"))


CATALOG_IDS = _catalog_ids()


def _load_sidebar_script():
    spec = importlib.util.spec_from_file_location("add_sidebar_labels", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _manifests():
    if not EXTENSIONS_ROOT.is_dir():
        return []
    out = []
    for manifest_path in sorted(EXTENSIONS_ROOT.glob("*/manifest.json")):
        try:
            out.append((
                manifest_path.parent.name,
                json.loads(manifest_path.read_text(encoding="utf-8")),
            ))
        except ValueError:
            continue
    return out


def _sidebar_contract_manifests():
    return [
        (ext_id, manifest)
        for ext_id, manifest in _manifests()
        if manifest.get("show_in_sidebar") or manifest.get("sidebar_label")
    ]


ALL = _manifests()
SIDEBAR_CONTRACT = _sidebar_contract_manifests()


# Extensions known to miss locales, with the issue that fixes each. Remove the entry when it is fixed.
KNOWN_GAPS = {"realm_settings": "realms-extensions#65"}


def _expect_complete(ext_id, manifest):
    gap = KNOWN_GAPS.get(ext_id)
    marks = [pytest.mark.xfail(reason=gap)] if gap else []
    return pytest.param(ext_id, manifest, id=ext_id, marks=marks)


@pytest.mark.skipif(not SIDEBAR_CONTRACT, reason="extensions submodule not checked out")
@pytest.mark.parametrize("ext_id,manifest", [_expect_complete(e, m) for e, m in SIDEBAR_CONTRACT])
def test_sidebar_label_includes_all_catalog_locales(ext_id, manifest):
    sidebar_label = manifest.get("sidebar_label")
    if manifest.get("show_in_sidebar"):
        assert sidebar_label, f"{ext_id} has show_in_sidebar but no sidebar_label"
    if not sidebar_label:
        return
    assert isinstance(sidebar_label, dict), f"{ext_id} sidebar_label must be an object"
    missing = [locale for locale in CATALOG_IDS if locale not in sidebar_label]
    assert not missing, (
        f"{ext_id} sidebar_label missing locales: {missing}; "
        f"have {sorted(sidebar_label)}"
    )


def test_merge_fills_missing_locales_without_dropping_en():
    mod = _load_sidebar_script()
    existing = OrderedDict([("en", "Voting")])
    merged, added = mod._merge_sidebar_label(existing, mod.LABELS["voting"])

    assert merged["en"] == "Voting"
    assert "es" in merged
    assert "ca-valencia" in merged
    assert merged["ca-valencia"] == "Votació"
    assert "en" not in added
    assert set(added) == set(mod.LOCALES) - {"en"}


def test_force_catalog_replaces_existing_keys():
    mod = _load_sidebar_script()
    existing = OrderedDict([("en", "Custom English"), ("xx", "Extra")])
    merged, added = mod._merge_sidebar_label(existing, mod.LABELS["voting"])
    assert merged["en"] == "Custom English"
    assert merged["xx"] == "Extra"
    assert "ca-valencia" in merged

    forced = mod._catalog_label(mod.LABELS["voting"])
    assert forced["en"] == "Voting"
    assert "xx" not in forced
