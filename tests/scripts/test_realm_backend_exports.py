"""Ratchet: the realm backend exports the methods its callers use.

A realm is installed with one ``text`` init argument (``init_`` in main.py). After that the installer
(gos-as-a-service ``realm_installer``) and the realm frontend call it only through ``read`` and ``run``. The
installer, for example, sets a new realm up with ``run("setup_step", ...)``. A wasm without one of them fails its
first call with IC0536. These tests pin that contract everywhere it can be lost: what basilisk reads from main.py,
the check ``pack()`` makes on the candid basilisk writes, and the two committed candid files.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import basilisk_cedar_template as bct  # noqa: E402
import pack_realm_backend as pack  # noqa: E402

MAIN_PY = REPO_ROOT / "src" / "realm_backend" / "main.py"
SOURCE_DID = REPO_ROOT / "src" / "realm_backend" / "realm_backend.did"

# What the callers rely on, written out rather than read from pack.ENDPOINTS: a line dropped from the packer's
# list must fail a test, not shrink it.
CALLED = (
    "service : (text) -> {",
    '"read" : (text, text) -> (text) query;',
    '"run" : (text, text) -> (text);',
)


def test_the_pack_checks_every_method_the_callers_use():
    assert [line for line in CALLED if line not in pack.ENDPOINTS] == []


@pytest.mark.parametrize(
    "did",
    [
        "src/realm_backend/realm_backend.did",
        # The candid the frontend's actor is built from: release.yml copies this directory into the frontend.
        "src/declarations/realm_backend/realm_backend.did",
    ],
)
def test_the_committed_candid_exports_them(did):
    text = (REPO_ROOT / did).read_text()
    assert [line for line in CALLED if line not in text] == []


def test_basilisk_reads_them_from_main_py():
    """basilisk's build writes the candid from what this extractor finds in main.py."""
    from basilisk.wasm_manipulator import extract_methods_from_python

    methods, _types, lifecycle = extract_methods_from_python(MAIN_PY.read_text())
    kinds = {m["name"]: m["method_type"] for m in methods}
    assert kinds.get("read") == "query"
    assert kinds.get("run") == "update"
    assert [p["candid_type"] for p in lifecycle["init"]["params"]] == ["text"]


def _pack_with_basilisk_writing(did_text, tmp_path, monkeypatch):
    """``pack()`` in tmp_path, with basilisk replaced by a stub that writes a Cedar wasm and the given candid.

    The template is not what these tests are about (test_basilisk_cedar_template.py checks it), so its lookup is
    stubbed too.
    """
    src = tmp_path / "src" / "realm_backend"
    (src / "api").mkdir(parents=True)
    (src / "api" / "setup.py").write_text(pack.VERSION_PLACEHOLDER + "\n")
    wasm = tmp_path / "realm_backend.wasm"

    def basilisk(cmd, cwd=None, env=None, check=False):
        wasm.write_bytes(b"\0asm " + bct.CEDAR_ENGINE_MARKER)
        Path(env["CANISTER_CANDID_PATH"]).write_text(did_text)

    monkeypatch.setattr(pack, "SRC", src)
    monkeypatch.setattr(pack, "ACTIVE_VARIANT", tmp_path / "build_variant.py")
    monkeypatch.setattr(pack, "WASM", wasm)
    monkeypatch.setattr(pack, "apply_cedar_template_env", lambda: {})
    monkeypatch.setattr(pack.subprocess, "run", basilisk)
    return pack.pack(version="test")


def test_the_pack_accepts_the_committed_candid(tmp_path, monkeypatch):
    wasm = _pack_with_basilisk_writing(SOURCE_DID.read_text(), tmp_path, monkeypatch)
    assert wasm == tmp_path / "realm_backend.wasm"


@pytest.mark.parametrize("line", CALLED)
def test_the_pack_refuses_a_candid_without_one(line, tmp_path, monkeypatch):
    did = SOURCE_DID.read_text()
    assert line in did
    with pytest.raises(SystemExit, match="not exported") as refused:
        _pack_with_basilisk_writing(did.replace(line, ""), tmp_path, monkeypatch)
    assert line in str(refused.value)
