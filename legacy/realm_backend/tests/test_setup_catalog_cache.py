"""Tests for setup wizard codex catalog cache (issue #8)."""

from __future__ import annotations

import json
import sys
import types
from unittest.mock import MagicMock

import pytest


if "_cdk" not in sys.modules:
    import typing

    _cdk = types.ModuleType("_cdk")
    _cdk.Async = typing.Iterator
    _cdk.CallResult = dict
    _cdk.Principal = MagicMock
    _cdk.Service = type("Service", (), {})
    _cdk.ic = MagicMock()
    _cdk.service_update = lambda fn: fn
    _cdk.text = str

    class _FakeStableMap:
        def __class_getitem__(cls, _item):
            return cls

        def __init__(self, *args, **kwargs):
            self._data = {}

        def insert(self, key, value):
            self._data[key] = value

        def get(self, key):
            return self._data.get(key)

    _cdk.StableBTreeMap = _FakeStableMap
    sys.modules["_cdk"] = _cdk
elif not hasattr(sys.modules["_cdk"], "StableBTreeMap"):
    class _FakeStableMap:
        def __class_getitem__(cls, _item):
            return cls

        def __init__(self, *args, **kwargs):
            self._data = {}

        def insert(self, key, value):
            self._data[key] = value

        def get(self, key):
            return self._data.get(key)

    sys.modules["_cdk"].StableBTreeMap = _FakeStableMap

mock_ic = MagicMock()
mock_ic.time.return_value = 1_700_000_000_000_000_000
sys.modules["_cdk"].ic = mock_ic


class _FakeRealm:
    instances_list = []

    def __init__(
        self,
        file_registry_canister_id="file-reg-id",
    ):
        self.file_registry_canister_id = file_registry_canister_id

    @classmethod
    def load(cls, _realm_id):
        return cls.instances_list[0] if cls.instances_list else None

    @classmethod
    def reset(cls, realm=None):
        cls.instances_list = [realm] if realm is not None else []


if "ggg" not in sys.modules:
    _ggg = types.ModuleType("ggg")
    _ggg.Realm = _FakeRealm
    sys.modules["ggg"] = _ggg
else:
    _FakeRealm = sys.modules["ggg"].Realm


def _import_setup_api():
    import importlib.util

    api_mod = types.ModuleType("api")
    api_mod.__path__ = []
    sys.modules["api"] = api_mod
    file_registry_mod = types.ModuleType("api.file_registry")
    file_registry_mod.FileRegistryService = MagicMock
    file_registry_mod._unwrap_call_result = lambda result: result
    sys.modules["api.file_registry"] = file_registry_mod

    path = "/srv/dev/realms/src/realm_backend/api/setup.py"
    spec = importlib.util.spec_from_file_location("setup_api_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _reset_state():
    _FakeRealm.reset(_FakeRealm(file_registry_canister_id="file-reg-id"))
    setup_api = _import_setup_api()
    setup_api._SETUP_CATALOG_CACHE._data.clear()
    yield


def test_get_available_codices_cached_empty():
    setup_api = _import_setup_api()
    result = json.loads(setup_api.get_available_codices_cached())
    assert result == {"success": False, "error": "empty"}


def test_list_available_codices_writes_cache():
    setup_api = _import_setup_api()

    class _Registry:
        def list_codices(self):
            return json.dumps(
                [
                    {
                        "codex_id": "syntropia",
                        "versions": ["1.0.0", "1.1.0"],
                        "latest": "1.1.0",
                    }
                ]
            )

        def get_extension_manifest(self, _args):
            return json.dumps(
                {
                    "name": "Syntropia",
                    "description": "Governance codex",
                }
            )

    setup_api.Principal.from_str = lambda _value: "file-reg-principal"
    setup_api.FileRegistryService = lambda _principal: _Registry()

    gen = setup_api.list_available_codices()
    codex_payload = json.dumps(
        [
            {
                "codex_id": "syntropia",
                "versions": ["1.0.0", "1.1.0"],
                "latest": "1.1.0",
            }
        ]
    )
    manifest_payload = json.dumps(
        {"name": "Syntropia", "description": "Governance codex"}
    )
    try:
        next(gen)
        gen.send(codex_payload)
        gen.send(manifest_payload)
    except StopIteration as stop:
        listed = json.loads(stop.value)

    assert listed["success"] is True
    assert listed["codices"][0]["id"] == "syntropia"

    cached = json.loads(setup_api.get_available_codices_cached())
    assert cached == {"success": True, "codices": listed["codices"]}

    raw = setup_api._SETUP_CATALOG_CACHE.get("catalog")
    payload = json.loads(raw)
    assert payload["fetched_at"] == mock_ic.time.return_value


def test_get_available_codices_cached_returns_envelope():
    setup_api = _import_setup_api()
    envelope = {
        "success": True,
        "codices": [{"id": "demo", "versions": ["0.1.0"], "name": "Demo"}],
    }
    setup_api._write_catalog_cache(envelope)

    cached = json.loads(setup_api.get_available_codices_cached())
    assert cached == {"success": True, "codices": envelope["codices"]}
