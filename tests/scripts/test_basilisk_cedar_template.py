"""Cedar is the only realm_backend pack path.

The plain CPython template is not selectable. These tests pin the leftover-free
recipe, dfx/CLI build lines, and docker setup to
``cpython_canister_template_cedar.wasm``.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import basilisk_cedar_template as bct  # noqa: E402
import pack_realm_backend as pack  # noqa: E402


FAKE_TEMPLATE = b"fake-wasm cedar"


def _write_fake_cedar(path: Path, monkeypatch) -> Path:
    """A stand-in template, and the pin set to its hash for this test."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(FAKE_TEMPLATE)
    monkeypatch.setattr(bct, "CEDAR_TEMPLATE_SHA256", hashlib.sha256(FAKE_TEMPLATE).hexdigest())
    return path


def test_plain_template_name_is_detected():
    assert bct.is_plain_cpython_template("/tmp/cpython_canister_template.wasm")
    assert not bct.is_plain_cpython_template(
        "/tmp/cpython_canister_template_cedar.wasm"
    )


def test_require_cedar_rejects_plain_template(tmp_path):
    plain = tmp_path / bct.PLAIN_TEMPLATE_NAME
    plain.write_bytes(FAKE_TEMPLATE)
    with pytest.raises(SystemExit, match="not a realm_backend pack path"):
        bct._require_cedar_template(plain)


def test_require_cedar_rejects_other_template_name(tmp_path):
    other = tmp_path / "some_other_template.wasm"
    other.write_bytes(FAKE_TEMPLATE)
    with pytest.raises(SystemExit, match="must pack with"):
        bct._require_cedar_template(other)


def test_apply_cedar_overwrites_plain_template_env(tmp_path, monkeypatch):
    dest = _write_fake_cedar(tmp_path / bct.CEDAR_TEMPLATE_NAME, monkeypatch)
    monkeypatch.setattr(bct, "cedar_template_cache_path", lambda: dest)
    env = bct.apply_cedar_template_env(
        {"BASILISK_TEMPLATE_WASM": str(tmp_path / bct.PLAIN_TEMPLATE_NAME)}
    )
    assert env["BASILISK_TEMPLATE_WASM"] == str(dest)
    assert Path(env["BASILISK_TEMPLATE_WASM"]).name == bct.CEDAR_TEMPLATE_NAME
    assert not bct.is_plain_cpython_template(env["BASILISK_TEMPLATE_WASM"])


class _BasiliskRan(Exception):
    """Raised by the stub in place of running basilisk: pack() stops there."""


def _stub_basilisk(monkeypatch):
    captured = {}

    def fake_run(cmd, cwd=None, env=None, check=False):
        captured["cmd"] = cmd
        captured["env"] = env
        raise _BasiliskRan

    monkeypatch.setattr(pack.subprocess, "run", fake_run)
    return captured


def test_the_pack_runs_basilisk_with_the_checked_cedar_template(tmp_path, monkeypatch):
    dest = _write_fake_cedar(tmp_path / bct.CEDAR_TEMPLATE_NAME, monkeypatch)
    monkeypatch.setattr(bct, "cedar_template_cache_path", lambda: dest)
    monkeypatch.setenv("BASILISK_TEMPLATE_WASM", str(tmp_path / bct.PLAIN_TEMPLATE_NAME))
    captured = _stub_basilisk(monkeypatch)
    with pytest.raises(_BasiliskRan):
        pack.pack(version="test")
    assert captured["cmd"][-2:] == ["realm_backend", str(pack.SRC / "main.py")]
    assert captured["env"]["BASILISK_TEMPLATE_WASM"] == str(dest)
    assert captured["env"]["CANISTER_CANDID_PATH"].endswith("src/realm_backend/realm_backend.did")


def test_the_pack_stops_before_basilisk_on_a_template_that_does_not_match(tmp_path, monkeypatch):
    dest = _write_fake_cedar(tmp_path / bct.CEDAR_TEMPLATE_NAME, monkeypatch)
    dest.write_bytes(FAKE_TEMPLATE + b" replaced")
    monkeypatch.setattr(bct, "cedar_template_cache_path", lambda: dest)
    captured = _stub_basilisk(monkeypatch)
    with pytest.raises(SystemExit, match="refusing to pack"):
        pack.pack(version="test")
    assert captured == {}  # basilisk never ran


def test_the_pin_is_a_sha256():
    assert len(bct.CEDAR_TEMPLATE_SHA256) == 64
    assert set(bct.CEDAR_TEMPLATE_SHA256) <= set("0123456789abcdef")


def test_a_template_with_other_bytes_is_refused(tmp_path, monkeypatch):
    """The name and the version are right, the bytes are not (realms-gos#793:
    any bytes that held the symbol name used to be accepted)."""
    dest = _write_fake_cedar(tmp_path / bct.CEDAR_TEMPLATE_NAME, monkeypatch)
    dest.write_bytes(b"NOT A BASILISK TEMPLATE ... sandbox_sha256 ...")
    with pytest.raises(SystemExit, match="refusing to pack"):
        bct._require_cedar_template(dest)


def test_a_cached_template_is_checked_on_every_use(tmp_path, monkeypatch):
    """A cache that was right when fetched and changed since is refused: the
    check is not only on download."""
    dest = _write_fake_cedar(tmp_path / "cache" / bct.CEDAR_TEMPLATE_NAME, monkeypatch)
    monkeypatch.setattr(bct, "cedar_template_cache_path", lambda: dest)
    assert bct.ensure_cedar_template() == str(dest)
    dest.write_bytes(FAKE_TEMPLATE + b" changed")
    with pytest.raises(SystemExit, match="refusing to pack"):
        bct.ensure_cedar_template()
    with pytest.raises(SystemExit, match="refusing to pack"):
        bct.apply_cedar_template_env({})


def _serve(monkeypatch, payload: bytes):
    import urllib.request

    fetched = []

    def fake_urlretrieve(url, filename):
        fetched.append(url)
        Path(filename).write_bytes(payload)

    monkeypatch.setattr(urllib.request, "urlretrieve", fake_urlretrieve)
    return fetched


def test_a_download_that_does_not_match_never_reaches_the_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(bct, "CEDAR_TEMPLATE_SHA256", hashlib.sha256(FAKE_TEMPLATE).hexdigest())
    dest = tmp_path / "cache" / bct.CEDAR_TEMPLATE_NAME
    monkeypatch.setattr(bct, "cedar_template_cache_path", lambda: dest)
    fetched = _serve(monkeypatch, b"NOT A BASILISK TEMPLATE ... sandbox_sha256 ...")
    with pytest.raises(SystemExit, match="refusing to pack"):
        bct.ensure_cedar_template()
    assert fetched == [bct.CEDAR_TEMPLATE_URL]
    assert not dest.exists()
    assert list(dest.parent.iterdir()) == []  # no partial file left behind


def test_a_download_that_matches_is_cached_and_used(tmp_path, monkeypatch):
    monkeypatch.setattr(bct, "CEDAR_TEMPLATE_SHA256", hashlib.sha256(FAKE_TEMPLATE).hexdigest())
    dest = tmp_path / "cache" / bct.CEDAR_TEMPLATE_NAME
    monkeypatch.setattr(bct, "cedar_template_cache_path", lambda: dest)
    fetched = _serve(monkeypatch, FAKE_TEMPLATE)
    assert bct.ensure_cedar_template() == str(dest)
    assert dest.read_bytes() == FAKE_TEMPLATE
    assert bct.ensure_cedar_template() == str(dest)  # the second use reads the cache
    assert fetched == [bct.CEDAR_TEMPLATE_URL]


def test_dfx_json_realm_backend_uses_cedar_pack():
    dfx = json.loads((REPO_ROOT / "dfx.json").read_text())
    build = dfx["canisters"]["realm_backend"]["build"]
    assert build == "python3 scripts/pack_realm_backend.py"
    assert "python -m basilisk realm_backend" not in build


def test_dfx_templates_use_cedar_pack():
    for rel in ("dfx.template.json", "cli/realms/cli/dfx.template.json"):
        dfx = json.loads((REPO_ROOT / rel).read_text())
        build = dfx["canisters"]["realm_backend"]["build"]
        assert build == "python3 scripts/pack_realm_backend.py", rel


def test_setup_docker_dev_env_downloads_cedar_not_plain():
    text = (REPO_ROOT / "scripts" / "setup_docker_dev_env.sh").read_text()
    assert 'TEMPLATE="cpython_canister_template_cedar.wasm"' in text
    assert 'TEMPLATE="cpython_canister_template.wasm"' not in text
    # Versioned release, not the rolling asset that changes on every basilisk main build.
    assert "releases/download/v$BASILISK_VER/$TEMPLATE" in text
    assert "cpython-wasm-3.13.0-ic1" not in text


def test_cedar_template_pin_matches_requirements():
    req = (REPO_ROOT / "requirements.txt").read_text()
    assert f"ic-basilisk=={bct.CEDAR_TEMPLATE_BASILISK_VERSION}\n" in req
    assert bct.CEDAR_TEMPLATE_URL.endswith(
        f"/v{bct.CEDAR_TEMPLATE_BASILISK_VERSION}/{bct.CEDAR_TEMPLATE_NAME}"
    )
    assert f"v{bct.CEDAR_TEMPLATE_BASILISK_VERSION}" in str(bct.cedar_template_cache_path())


def test_mundus_leftover_free_uses_pack_script():
    text = (REPO_ROOT / "cli" / "realms" / "cli" / "commands" / "mundus.py").read_text()
    assert "pack_realm_backend.py" in text
    assert '["python", "-m", "basilisk", "realm_backend"' not in text


def test_agents_leftover_free_recipe_uses_cedar_pack():
    text = (REPO_ROOT / "AGENTS.md").read_text()
    leftover = text.split("### Direct runtime install")[1]
    leftover = leftover.split("## Environment Setup")[0]
    assert leftover.count("python3 scripts/pack_realm_backend.py") == 2
    assert "python -m basilisk realm_backend" not in leftover


def test_build_base_wasm_dry_run_still_pins_cedar(tmp_path, monkeypatch):
    dest = _write_fake_cedar(tmp_path / bct.CEDAR_TEMPLATE_NAME, monkeypatch)
    monkeypatch.setattr(bct, "cedar_template_cache_path", lambda: dest)
    monkeypatch.setenv("BASILISK_TEMPLATE_WASM", str(tmp_path / bct.PLAIN_TEMPLATE_NAME))

    import build_base_wasm as bbw

    monkeypatch.setattr(bbw, "apply_cedar_template_env", bct.apply_cedar_template_env)
    out = bbw._run_basilisk(REPO_ROOT, dry_run=True)
    assert out.name == "realm_backend.wasm"
