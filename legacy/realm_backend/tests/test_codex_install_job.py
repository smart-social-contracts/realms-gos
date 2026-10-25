"""Unit tests for the multi-message Codex install job."""

import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import codex_install_job as job


class _FakeRealm:
    instances = []

    @classmethod
    def load(cls, _id):
        return cls.instances[0] if cls.instances else None

    def __init__(self, codex_install_state=""):
        self.codex_install_state = codex_install_state


@pytest.fixture(autouse=True)
def fake_ggg_module():
    ggg = types.ModuleType("ggg")
    ggg.Realm = _FakeRealm
    sys.modules["ggg"] = ggg
    _FakeRealm.instances = []
    yield
    sys.modules.pop("ggg", None)


def _drive(gen, send=None):
    try:
        while True:
            send = gen.send(send)
    except StopIteration as exc:
        return exc.value


def test_one_phase_per_call_advances_state(tmp_path, monkeypatch):
    monkeypatch.setattr(job, "STAGING_ROOT", str(tmp_path / "staging"))
    realm = _FakeRealm()
    state = job._new_job(
        "agora",
        "uq2mu-kaaaa-aaaah-avqcq-cai",
        "1.0.0",
        "frontend-id",
        False,
        None,
        False,
        "ext",
    )
    state["phase"] = "pull_backend"
    state["namespace"] = "ext/agora/1.0.0"
    state["resolved_version"] = "1.0.0"
    state["backend_paths"] = [
        "backend/a.py",
        "backend/b.py",
        "backend/c.py",
        "backend/d.py",
    ]
    state["backend_index"] = 0

    fr = types.ModuleType("api.file_registry")
    fr.FileRegistryService = lambda _pid: object()
    sys.modules["api.file_registry"] = fr

    def fake_pull(registry, namespace, path):
        return f"content-{path}"

    monkeypatch.setattr(job, "_pull_path", fake_pull)

    raw = _drive(job._phase_pull_backend(realm, state))

    result = json.loads(raw)
    assert result["status"] == "in_progress"
    assert result["phase"] == "pull_backend"
    assert result["done"] == job.MAX_PULL_FILES
    assert state["backend_index"] == job.MAX_PULL_FILES


def test_incremental_pull_budget_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(job, "STAGING_ROOT", str(tmp_path / "staging"))
    monkeypatch.setattr(job, "MAX_PULL_FILES", 10)
    monkeypatch.setattr(job, "MAX_PULL_BYTES", 35)

    realm = _FakeRealm()
    state = job._new_job(
        "agora",
        "uq2mu-kaaaa-aaaah-avqcq-cai",
        "1.0.0",
        "",
        False,
        None,
        False,
        "ext",
    )
    state.update(
        {
            "phase": "pull_backend",
            "namespace": "ext/agora/1.0.0",
            "resolved_version": "1.0.0",
            "backend_paths": ["backend/big1.py", "backend/big2.py"],
            "backend_index": 0,
        }
    )

    fr = types.ModuleType("api.file_registry")
    fr.FileRegistryService = lambda _pid: object()
    sys.modules["api.file_registry"] = fr

    def fake_pull(registry, namespace, path):
        return "x" * 40

    monkeypatch.setattr(job, "_pull_path", fake_pull)

    raw = _drive(job._phase_pull_backend(realm, state))

    result = json.loads(raw)
    assert result["status"] == "in_progress"
    assert state["backend_index"] == 1


def test_resume_same_args_continues_job(tmp_path, monkeypatch):
    monkeypatch.setattr(job, "STAGING_ROOT", str(tmp_path / "staging"))
    realm = _FakeRealm()
    state = job._new_job(
        "agora", "registry-id", "1.0.0", "", False, None, False, "ext"
    )
    state.update(
        {
            "phase": "scan",
            "namespace": "ext/agora/1.0.0",
            "resolved_version": "1.0.0",
            "backend_paths": ["manifest.json"],
            "backend_index": 1,
            "manifest": {"kind": "codex"},
            "frontend_paths": [],
        }
    )
    staging = job._staging_dir("agora")
    os.makedirs(staging, exist_ok=True)
    with open(os.path.join(staging, "manifest.json"), "w") as handle:
        handle.write('{"kind":"codex"}')

    scan = types.ModuleType("core.codex_scan")
    scan.check_codex_imports = lambda *a, **k: ""
    sys.modules["core.codex_scan"] = scan
    hooks = types.ModuleType("core.codex_hooks")
    hooks.declares_ggg_api = lambda manifest: False
    sys.modules["core.codex_hooks"] = hooks
    runtime = types.ModuleType("core.runtime_codex")
    runtime.legacy_init_py_error = lambda *a, **k: None
    sys.modules["core.runtime_codex"] = runtime

    raw = job._phase_scan(realm, state)

    result = json.loads(raw)
    assert result["status"] == "in_progress"
    assert result["phase"] == "apply_backend"


def test_init_not_before_overlay(tmp_path, monkeypatch):
    """apply_backend and overlay must run before init when run_init=True."""
    monkeypatch.setattr(job, "STAGING_ROOT", str(tmp_path / "staging"))
    calls = []

    def fake_apply(realm, state):
        calls.append("apply")
        return json.dumps(job._advance(state, "overlay"))

    def fake_overlay(realm, state):
        calls.append("overlay")
        state = dict(state)
        state["phase"] = "init"
        return json.dumps(job._advance(state, "init"))

    def fake_init(realm, state):
        calls.append("init")
        return json.dumps(job._complete_result(state))

    monkeypatch.setattr(job, "_phase_apply_backend", fake_apply)
    monkeypatch.setattr(job, "_phase_overlay", fake_overlay)
    monkeypatch.setattr(job, "_phase_init", fake_init)

    realm = _FakeRealm()
    _FakeRealm.instances = [realm]
    state = job._new_job(
        "agora", "registry-id", "1.0.0", "", False, None, True, "ext"
    )
    state["phase"] = "apply_backend"
    realm.codex_install_state = json.dumps(state)

    _drive(job.continue_codex_install("registry-id", "agora", "1.0.0", run_init=True))
    _drive(job.continue_codex_install("registry-id", "agora", "1.0.0", run_init=True))
    raw = _drive(
        job.continue_codex_install("registry-id", "agora", "1.0.0", run_init=True)
    )

    assert calls == ["apply", "overlay", "init"]
    result = json.loads(raw)
    assert result["status"] == "complete"
    assert realm.codex_install_state == ""
