"""Every operation an extension's manifest lists must still exist in the realm.

An extension calls operations by name, through ``read`` and ``run``, and lists them in its manifest's
``operations``. Renaming or removing one breaks the extension only when someone opens it in a running realm.
"""

import json
import pathlib

import pytest

from core.op import ops

EXTENSIONS = pathlib.Path(__file__).resolve().parents[2] / "extensions" / "extensions"
MANIFESTS = sorted(EXTENSIONS.glob("*/manifest.json"))


@pytest.mark.skipif(not MANIFESTS, reason="the extensions submodule is not checked out")
def test_every_manifest_operation_exists():
    realm = set(ops())
    missing = {}
    for path in MANIFESTS:
        listed = json.loads(path.read_text()).get("operations", [])
        assert all(isinstance(name, str) for name in listed), f"{path.parent.name}: operations are names"
        if gone := sorted(set(listed) - realm):
            missing[path.parent.name] = gone
    assert not missing, f"operations the realm no longer has, by extension: {missing}"
