"""Unit tests for the in-realm setup wizard (issue #8)."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Stub _cdk / ic before importing setup modules
# ---------------------------------------------------------------------------

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

mock_ic = MagicMock()
mock_ic.caller.return_value.to_str.return_value = "stranger-principal"
mock_ic.id.return_value = "backend-canister-id"
mock_ic.time.return_value = 1_700_000_000_000_000_000
sys.modules["_cdk"].ic = mock_ic

if "ic_python_logging" not in sys.modules:
    _logging = types.ModuleType("ic_python_logging")
    _logging.get_logger = lambda _name: MagicMock()
    sys.modules["ic_python_logging"] = _logging


class _FakeProfile:
    def __init__(self, allowed_to=""):
        self.allowed_to = allowed_to


class _FakeUser:
    _store = {}

    def __init__(self, principal, profiles=()):
        self.id = principal
        self.profiles = list(profiles)
        self.permissions = []
        self.departments = []

    @classmethod
    def __getitem__(cls, principal):
        return cls._store.get(principal)

    @classmethod
    def reset(cls):
        cls._store = {}


# The shared-ledger catalog the installer hands a realm (set_canister_config_json
# shared_tokens, from the environment's casals.json). Tests that adopt ckEURC
# by symbol rely on this entry; the canister itself has no table.
_CATALOG_JSON = json.dumps(
    {"ckEURC": {"ledger": "pe5t5-diaaa-aaaar-qahwa-cai", "indexer": "pe5t5-diaaa-aaaar-qahwa-cai", "decimals": 6}}
)


class _FakeRealm:
    instances_list = []

    def __init__(
        self,
        status="setup",
        manifest_data="{}",
        file_registry_canister_id="file-reg-id",
        frontend_canister_id="frontend-id",
        token_canister_id="",
        marketplace_canister_id="",
        name="Test Realm",
        manifesto="",
        welcome_message="",
        accounting_currency="REALMS",
        accounting_currency_decimals=8,
        network="",
        shared_tokens_json=_CATALOG_JSON,
    ):
        self.status = status
        self.shared_tokens_json = shared_tokens_json
        self.manifest_data = manifest_data
        self.file_registry_canister_id = file_registry_canister_id
        self.frontend_canister_id = frontend_canister_id
        self.token_canister_id = token_canister_id
        self.marketplace_canister_id = marketplace_canister_id
        self.principal_id = ""
        self.name = name
        self.manifesto = manifesto
        self.welcome_message = welcome_message
        self.accounting_currency = accounting_currency
        self.accounting_currency_decimals = accounting_currency_decimals
        self.network = network
        self.installer_canister_id = ""
        self.trusted_principals = ""

    @classmethod
    def load(cls, _realm_id):
        return cls.instances_list[0] if cls.instances_list else None

    @classmethod
    def instances(cls):
        return cls.instances_list

    @classmethod
    def reset(cls, realm=None):
        cls.instances_list = [realm] if realm is not None else []


class _Operations:
    REALM_ADMIN = "realm.admin"
    ALL = "*"


_ggg = types.ModuleType("ggg")
_ggg.User = _FakeUser
_ggg.Realm = _FakeRealm
_ggg.system = types.ModuleType("ggg.system")
_ggg.system.user_profile = types.ModuleType("ggg.system.user_profile")
_ggg.system.user_profile.Operations = _Operations
_ggg.governance = types.ModuleType("ggg.governance")
_ggg.governance.realm = types.ModuleType("ggg.governance.realm")


class RealmStatus:
    SETUP = "setup"
    ALPHA = "alpha"


_ggg.governance.realm.RealmStatus = RealmStatus
sys.modules["ggg"] = _ggg
sys.modules["ggg.system"] = _ggg.system
sys.modules["ggg.system.user_profile"] = _ggg.system.user_profile
sys.modules["ggg.governance"] = _ggg.governance
sys.modules["ggg.governance.realm"] = _ggg.governance.realm

from core import setup as setup_core  # noqa: E402
from core import codex_init_host  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    _FakeUser.reset()
    _FakeRealm.reset(_FakeRealm(status=RealmStatus.SETUP))
    mock_ic.caller.return_value.to_str.return_value = "stranger-principal"
    mock_ic.is_controller.return_value = False
    monkeypatch.setattr("core.access._check_access", lambda *_args: False)
    yield


def _import_setup_api():
    import importlib.util

    api_mod = types.ModuleType("api")
    api_mod.__path__ = []
    sys.modules["api"] = api_mod
    file_registry_mod = types.ModuleType("api.file_registry")
    file_registry_mod.FileRegistryService = MagicMock
    file_registry_mod.AssetCanisterService = MagicMock
    file_registry_mod._unwrap_call_result = lambda result: result
    file_registry_mod.install_codex_from_registry = MagicMock()
    sys.modules["api.file_registry"] = file_registry_mod

    path = str(Path(__file__).resolve().parents[1] / "api" / "setup.py")
    spec = importlib.util.spec_from_file_location("setup_api_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.modules["api.setup"] = module
    api_mod.setup = module
    return module


def _authorized_creator(realm):
    _FakeRealm.reset(realm)
    mock_ic.caller.return_value.to_str.return_value = "creator-1"
    manifest = json.loads(realm.manifest_data or "{}")
    setup = manifest.get("setup") or {}
    setup["creator_principal"] = "creator-1"
    manifest["setup"] = setup
    realm.manifest_data = json.dumps(manifest)


def _clear_draft_assets(setup_api):
    if hasattr(setup_api, "_SETUP_DRAFT_ASSETS"):
        setup_api._SETUP_DRAFT_ASSETS._data.clear()


def _run_async(gen):
    try:
        value = next(gen)
        while True:
            value = gen.send(value)
    except StopIteration as stop:
        return stop.value


def _call_json(result):
    if hasattr(result, "send"):
        result = _run_async(result)
    if isinstance(result, str):
        return json.loads(result)
    return result


def _call_setup_launch(setup_api):
    return _call_json(setup_api.setup_launch())


def _call_setup_save_draft(setup_api, payload):
    if not isinstance(payload, str):
        payload = json.dumps(payload)
    return _call_json(setup_api.setup_save_draft(payload))


def _call_setup_apply_draft_token(setup_api):
    return _call_json(setup_api.setup_apply_draft_token())


def _settings_treasury_message() -> str:
    from core.realm_currency import no_treasury_token_error

    return no_treasury_token_error()["error"]


def _ensure_tokens_cdk():
    import typing

    _cdk = sys.modules["_cdk"]

    class _TypedDict(dict):
        def __init_subclass__(cls, **kwargs):
            super().__init_subclass__()

    _cdk.Opt = getattr(_cdk, "Opt", typing.Optional)
    _cdk.Record = getattr(_cdk, "Record", _TypedDict)
    _cdk.Variant = getattr(_cdk, "Variant", _TypedDict)
    _cdk.blob = getattr(_cdk, "blob", bytes)
    _cdk.nat = getattr(_cdk, "nat", int)
    _cdk.nat8 = getattr(_cdk, "nat8", int)
    _cdk.null = getattr(_cdk, "null", None)
    _cdk.service_query = getattr(_cdk, "service_query", lambda fn: fn)


def _load_real_tokens(monkeypatch):
    import importlib.util

    _ensure_tokens_cdk()
    path = Path(__file__).resolve().parents[1] / "api" / "tokens.py"
    spec = importlib.util.spec_from_file_location("api.tokens", path)
    tokens_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tokens_mod)
    monkeypatch.setitem(sys.modules, "api.tokens", tokens_mod)
    return tokens_mod


def _load_tokens_offline(monkeypatch):
    tokens_mod = _load_real_tokens(monkeypatch)
    monkeypatch.setattr(
        tokens_mod,
        "Icrc1MetadataService",
        MagicMock(side_effect=RuntimeError("offline")),
    )
    return tokens_mod


def test_realm_status_default_is_setup():
    assert RealmStatus.SETUP == "setup"
    realm = _FakeRealm()
    assert realm.status == "setup"


def test_effective_status_fail_closed_to_setup():
    realm = _FakeRealm(status="")
    assert setup_core.effective_realm_status(realm) == "setup"
    realm.status = None
    assert setup_core.effective_realm_status(realm) == "setup"


_TEST_INSTALLER = "fltjm-tyaaa-aaaap-qunhq-cai"


def _enter_setup_with_auth_gate(
    creator: str, registry_id: str, environment: str = "", caller: str = ""
) -> dict:
    """Mirror main.enter_setup caller check + core.enter_setup."""
    caller = caller or mock_ic.caller.return_value.to_str.return_value
    is_controller = bool(mock_ic.is_controller(mock_ic.caller.return_value))
    if not setup_core.can_enter_setup(caller, is_controller=is_controller):
        return {"ok": False, "error": "unauthorized"}
    return setup_core.enter_setup(creator, registry_id, environment, caller=caller)


def test_enter_setup_sets_creator_and_registry():
    realm = _FakeRealm(status=RealmStatus.SETUP, manifest_data="{}")
    _FakeRealm.reset(realm)
    mock_ic.is_controller.return_value = True

    result = _enter_setup_with_auth_gate(
        "creator-1", "registry-canister", "staging"
    )

    assert result == {"ok": True}
    setup_cfg = json.loads(realm.manifest_data)["setup"]
    assert setup_cfg["creator_principal"] == "creator-1"
    assert setup_cfg["realm_registry_canister_id"] == "registry-canister"
    assert realm.network == "staging"
    assert realm.file_registry_canister_id == "file-reg-id"  # untouched: the installer sets infra ids


def test_enter_setup_leaves_infra_ids_to_the_installer():
    """No per-network table in the canister: infra ids arrive via
    set_canister_config_json, and any environment name is accepted."""
    realm = _FakeRealm(
        status=RealmStatus.SETUP,
        manifest_data="{}",
        file_registry_canister_id="",
        marketplace_canister_id="",
    )
    _FakeRealm.reset(realm)
    mock_ic.is_controller.return_value = True

    result = _enter_setup_with_auth_gate(
        "creator-1", "registry-canister", "production"
    )

    assert result == {"ok": True}
    assert realm.network == "production"
    assert realm.file_registry_canister_id == ""
    assert realm.marketplace_canister_id == ""


def test_enter_setup_empty_environment_preserves_existing_network():
    realm = _FakeRealm(
        status=RealmStatus.SETUP,
        manifest_data="{}",
        network="production",
    )
    _FakeRealm.reset(realm)
    mock_ic.is_controller.return_value = True

    result = _enter_setup_with_auth_gate("creator-1", "registry-canister", "")

    assert result == {"ok": True}
    assert realm.network == "production"


def test_enter_setup_strips_environment_whitespace():
    realm = _FakeRealm(status=RealmStatus.SETUP, manifest_data="{}")
    _FakeRealm.reset(realm)
    mock_ic.is_controller.return_value = True

    result = _enter_setup_with_auth_gate(
        "creator-1", "registry-canister", "  test  "
    )

    assert result == {"ok": True}
    assert realm.network == "test"


def test_enter_setup_idempotent_for_same_creator():
    realm = _FakeRealm(
        status=RealmStatus.SETUP,
        manifest_data=json.dumps(
            {
                "setup": {
                    "creator_principal": "creator-1",
                    "realm_registry_canister_id": "old-registry",
                }
            }
        ),
    )
    _FakeRealm.reset(realm)
    mock_ic.is_controller.return_value = True

    result = _enter_setup_with_auth_gate("creator-1", "new-registry")

    assert result == {"ok": True}
    setup_cfg = json.loads(realm.manifest_data)["setup"]
    assert setup_cfg["creator_principal"] == "creator-1"
    assert setup_cfg["realm_registry_canister_id"] == "new-registry"


def test_enter_setup_rejects_different_creator():
    realm = _FakeRealm(
        status=RealmStatus.SETUP,
        manifest_data=json.dumps({"setup": {"creator_principal": "creator-1"}}),
    )
    _FakeRealm.reset(realm)
    mock_ic.is_controller.return_value = True

    result = _enter_setup_with_auth_gate("creator-2", "registry-canister")

    assert result == {
        "ok": False,
        "error": "setup already entered by another creator",
    }


def test_enter_setup_rejects_random_principal():
    realm = _FakeRealm(status=RealmStatus.SETUP, manifest_data="{}")
    _FakeRealm.reset(realm)
    mock_ic.is_controller.return_value = False
    mock_ic.caller.return_value.to_str.return_value = "random-attacker"

    result = _enter_setup_with_auth_gate(
        "creator-1", "registry-canister", caller="random-attacker"
    )

    assert result == {"ok": False, "error": "unauthorized"}
    assert "setup" not in json.loads(realm.manifest_data)
    assert realm.installer_canister_id == ""
    assert realm.trusted_principals == ""


def test_enter_setup_records_installer_controller():
    """The installer is a controller (Casals stand template); first boot records it."""
    realm = _FakeRealm(status=RealmStatus.SETUP, manifest_data="{}")
    _FakeRealm.reset(realm)
    mock_ic.is_controller.return_value = True
    mock_ic.caller.return_value.to_str.return_value = _TEST_INSTALLER

    result = _enter_setup_with_auth_gate(
        "creator-1", "registry-canister", "test", caller=_TEST_INSTALLER
    )

    assert result == {"ok": True}
    setup_cfg = json.loads(realm.manifest_data)["setup"]
    assert setup_cfg["creator_principal"] == "creator-1"
    assert realm.installer_canister_id == _TEST_INSTALLER
    assert _TEST_INSTALLER in realm.trusted_principals.split(",")


def test_enter_setup_installer_once_is_idempotent_same_creator():
    realm = _FakeRealm(status=RealmStatus.SETUP, manifest_data="{}")
    _FakeRealm.reset(realm)
    mock_ic.is_controller.return_value = True

    first = _enter_setup_with_auth_gate(
        "creator-1", "registry-canister", "test", caller=_TEST_INSTALLER
    )
    second = _enter_setup_with_auth_gate(
        "creator-1", "other-registry", "test", caller=_TEST_INSTALLER
    )

    assert first == {"ok": True}
    assert second == {"ok": True}
    setup_cfg = json.loads(realm.manifest_data)["setup"]
    assert setup_cfg["creator_principal"] == "creator-1"
    assert setup_cfg["realm_registry_canister_id"] == "other-registry"


def test_enter_setup_requires_controller_or_recorded_installer():
    """No baked-in installer ids: a non-controller is refused unless it is the
    installer recorded at first boot (or a trusted principal)."""
    realm = _FakeRealm(status=RealmStatus.SETUP, manifest_data="{}")
    _FakeRealm.reset(realm)
    mock_ic.is_controller.return_value = False

    denied = _enter_setup_with_auth_gate(
        "creator-1", "registry-canister", "local", caller=_TEST_INSTALLER
    )
    assert denied == {"ok": False, "error": "unauthorized"}

    mock_ic.is_controller.return_value = True
    assert _enter_setup_with_auth_gate(
        "creator-1", "registry-canister", "local", caller=_TEST_INSTALLER
    ) == {"ok": True}
    assert realm.installer_canister_id == _TEST_INSTALLER

    mock_ic.is_controller.return_value = False
    assert setup_core.can_enter_setup(_TEST_INSTALLER, is_controller=False) is True
    assert setup_core.can_enter_setup("random-attacker", is_controller=False) is False


def test_bootstrap_admin_is_only_the_recorded_installer():
    from core.access import is_bootstrap_admin_caller

    realm = _FakeRealm(status=RealmStatus.ALPHA, manifest_data="{}")
    assert is_bootstrap_admin_caller(_TEST_INSTALLER, realm) is False
    realm.installer_canister_id = _TEST_INSTALLER
    assert is_bootstrap_admin_caller(_TEST_INSTALLER, realm) is True
    assert is_bootstrap_admin_caller("random-attacker", realm) is False
    assert is_bootstrap_admin_caller(_TEST_INSTALLER, None) is False


def test_enter_setup_rejects_when_completed():
    realm = _FakeRealm(
        status=RealmStatus.ALPHA,
        manifest_data=json.dumps(
            {"setup": {"creator_principal": "creator-1", "setup_completed_at": "1"}}
        ),
    )
    _FakeRealm.reset(realm)
    mock_ic.is_controller.return_value = True

    result = _enter_setup_with_auth_gate("creator-1", "registry-canister")

    assert result == {"ok": False, "error": "setup already completed"}


def test_enter_setup_rejects_when_realm_missing():
    _FakeRealm.reset(None)
    mock_ic.is_controller.return_value = True

    result = _enter_setup_with_auth_gate("creator-1", "registry-canister")

    assert result == {"ok": False, "error": "realm not initialized"}


def test_creator_is_authorized_during_setup():
    realm = _FakeRealm(
        manifest_data=json.dumps({"setup": {"creator_principal": "creator-1"}})
    )
    _FakeRealm.reset(realm)
    mock_ic.caller.return_value.to_str.return_value = "creator-1"
    assert setup_core.is_setup_authorized("creator-1") is True


def test_realm_admin_is_authorized_during_setup(monkeypatch):
    realm = _FakeRealm(
        manifest_data=json.dumps({"setup": {"creator_principal": "creator-1"}})
    )
    _FakeRealm.reset(realm)
    admin = _FakeUser("admin-1", profiles=[_FakeProfile("realm.admin")])
    _FakeUser._store["admin-1"] = admin
    monkeypatch.setattr(
        "core.access._check_access",
        lambda caller, op: caller == "admin-1" and op == _Operations.REALM_ADMIN,
    )
    assert setup_core.is_setup_authorized("admin-1") is True


def test_stranger_not_authorized_during_setup(monkeypatch):
    realm = _FakeRealm(
        manifest_data=json.dumps({"setup": {"creator_principal": "creator-1"}})
    )
    _FakeRealm.reset(realm)
    monkeypatch.setattr("core.access._check_access", lambda *_args: False)
    assert setup_core.is_setup_authorized("stranger-principal") is False


def test_setup_module_loads_without_service_class():
    """core.setup must import without constructing a Basilisk Service subclass."""
    assert callable(setup_core.setup_gate_error)
    assert not hasattr(setup_core, "RealmRegistrySetupService")


def test_setup_gate_blocks_stranger():
    realm = _FakeRealm(status=RealmStatus.SETUP)
    _FakeRealm.reset(realm)
    err = setup_core.setup_gate_error("stranger-principal")
    assert err == setup_core.SETUP_ERROR


def test_setup_gate_allows_creator():
    realm = _FakeRealm(
        status=RealmStatus.SETUP,
        manifest_data=json.dumps({"setup": {"creator_principal": "creator-1"}}),
    )
    _FakeRealm.reset(realm)
    assert setup_core.setup_gate_error("creator-1") is None


def test_codex_init_does_not_flip_setup_to_alpha(monkeypatch):
    realm = _FakeRealm(status=RealmStatus.SETUP, manifest_data="{}")
    _FakeRealm.reset(realm)
    monkeypatch.setattr(codex_init_host, "_realm", lambda: realm)
    monkeypatch.setattr(
        codex_init_host,
        "_load_manifest",
        lambda _codex_id: {"name": "Test Codex"},
    )
    codex_init_host.apply_init_policy("test_codex")
    assert realm.status == RealmStatus.SETUP


def test_apply_init_policy_preserves_setup_block(monkeypatch):
    setup_block = {
        "creator_principal": "creator-1",
        "realm_registry_canister_id": "registry-canister",
        "token": {"symbol": "REALMS"},
    }
    realm = _FakeRealm(
        status=RealmStatus.SETUP,
        manifest_data=json.dumps(
            {
                "setup": setup_block,
                "scaling": {"capacity": 2000},
            }
        ),
    )
    _FakeRealm.reset(realm)
    monkeypatch.setattr(codex_init_host, "_realm", lambda: realm)
    monkeypatch.setattr(
        codex_init_host,
        "_load_manifest",
        lambda _codex_id: {"name": "Test Codex", "onboarding": {}},
    )
    codex_init_host.apply_init_policy("test_codex")
    manifest = json.loads(realm.manifest_data)
    assert manifest["setup"] == setup_block
    assert manifest["scaling"] == {"capacity": 2000}


def test_branding_size_cap_rejects_large_data_url():
    huge = "data:image/png;base64," + ("A" * (setup_core.BRANDING_DATA_URL_MAX_BYTES + 1))
    err = setup_core.validate_branding_payload({"logo_data_url": huge})
    assert err is not None
    assert "1.5MB" in err


def test_validate_branding_payload_primary_color():
    assert setup_core.normalize_primary_color("#3B82F6") == "#3b82f6"
    assert setup_core.normalize_primary_color("#fff") is None
    assert setup_core.normalize_primary_color("blue") is None
    assert setup_core.validate_branding_payload({"colors": {"primary": "#3B82F6"}}) is None
    err = setup_core.validate_branding_payload({"colors": {"primary": "blue"}})
    assert err is not None
    assert "colors.primary" in err


def test_complete_setup_requires_codex():
    setup_api = _import_setup_api()

    realm = _FakeRealm(status=RealmStatus.SETUP, manifest_data='{"setup": {}}')
    _FakeRealm.reset(realm)
    mock_ic.caller.return_value.to_str.return_value = "creator-1"
    realm.manifest_data = json.dumps({"setup": {"creator_principal": "creator-1"}})

    gen = setup_api.complete_setup()
    try:
        while True:
            next(gen)
    except StopIteration as stop:
        result = json.loads(stop.value)

    assert result["success"] is False
    assert "codex" in result["error"].lower()


def test_complete_setup_transitions_and_notifies(monkeypatch):
    setup_api = _import_setup_api()

    realm = _FakeRealm(
        status=RealmStatus.SETUP,
        manifest_data=json.dumps(
            {
                "setup": {
                    "creator_principal": "creator-1",
                    "realm_registry_canister_id": "registry-canister",
                    "codex": {"package": "syntropia", "version": "1.0.0"},
                }
            }
        ),
    )
    _FakeRealm.reset(realm)
    mock_ic.caller.return_value.to_str.return_value = "creator-1"

    notified = {}

    def _fake_notify(registry_id):
        notified["registry_id"] = registry_id
        yield None

    monkeypatch.setattr(setup_api, "notify_registry_setup_completed", _fake_notify)

    gen = setup_api.complete_setup()
    try:
        while True:
            next(gen)
    except StopIteration as stop:
        result = json.loads(stop.value)

    assert result["success"] is True
    assert realm.status == RealmStatus.ALPHA
    assert notified["registry_id"] == "registry-canister"
    setup_cfg = json.loads(realm.manifest_data)["setup"]
    assert setup_cfg["setup_completed_at"]
    assert setup_cfg["codex"]["version"] == "1.0.0"


def test_complete_setup_returns_success_when_notify_succeeds(monkeypatch):
    setup_api = _import_setup_api()

    realm = _FakeRealm(
        status=RealmStatus.SETUP,
        manifest_data=json.dumps(
            {
                "setup": {
                    "creator_principal": "creator-1",
                    "realm_registry_canister_id": "registry-canister",
                    "codex": {"package": "syntropia", "version": "1.0.0"},
                }
            }
        ),
    )
    _FakeRealm.reset(realm)
    mock_ic.caller.return_value.to_str.return_value = "creator-1"

    def _fake_notify(registry_id):
        yield {"Ok": "registered"}

    monkeypatch.setattr(setup_core, "notify_registry_setup_completed", _fake_notify)

    gen = setup_api.complete_setup()
    try:
        while True:
            next(gen)
    except StopIteration as stop:
        result = json.loads(stop.value)

    assert result["success"] is True
    assert result["registry_notified"] is True
    assert realm.status == RealmStatus.ALPHA


def test_safe_log_swallows_logger_failures(monkeypatch):
    monkeypatch.setattr(
        setup_core.logger,
        "info",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("logging subsystem unavailable")
        ),
    )
    setup_core._safe_log("info", "realm_setup_completed notify to %s: %s", "registry", {"Ok": "ok"})


def test_runtime_flags_default_stage_is_setup(monkeypatch):
    from core import runtime_flags

    realm = _FakeRealm(status="")
    _FakeRealm.reset(realm)
    payload = runtime_flags.get_runtime_flags_payload()
    assert payload["realm_stage"] == "setup"


def test_setup_set_branding_identity_applies_to_realm_and_stores():
    setup_api = _import_setup_api()

    realm = _FakeRealm(
        status=RealmStatus.SETUP,
        manifest_data=json.dumps({"setup": {"creator_principal": "creator-1"}}),
    )
    _FakeRealm.reset(realm)
    mock_ic.caller.return_value.to_str.return_value = "creator-1"

    result = json.loads(
        setup_api.setup_set_branding(
            json.dumps(
                {
                    "manifesto": "We build together.",
                    "welcome_message": "Welcome to our realm!",
                }
            )
        )
    )

    assert result["success"] is True
    assert realm.manifesto == "We build together."
    assert realm.welcome_message == "Welcome to our realm!"
    setup_cfg = json.loads(realm.manifest_data)["setup"]
    assert setup_cfg["identity"]["manifesto"] == "We build together."
    assert setup_cfg["identity"]["welcome_message"] == "Welcome to our realm!"
    assert result["identity"]["manifesto"] == "We build together."


def test_setup_set_branding_rejects_identity_length_limits():
    setup_api = _import_setup_api()

    realm = _FakeRealm(
        status=RealmStatus.SETUP,
        manifest_data=json.dumps({"setup": {"creator_principal": "creator-1"}}),
    )
    _FakeRealm.reset(realm)
    mock_ic.caller.return_value.to_str.return_value = "creator-1"

    long_manifesto = "x" * (setup_core.MANIFESTO_MAX_CHARS + 1)
    result = json.loads(
        setup_api.setup_set_branding(json.dumps({"manifesto": long_manifesto}))
    )
    assert result["success"] is False
    assert "manifesto" in result["error"]

    long_welcome = "y" * (setup_core.WELCOME_MESSAGE_MAX_CHARS + 1)
    result = json.loads(
        setup_api.setup_set_branding(json.dumps({"welcome_message": long_welcome}))
    )
    assert result["success"] is False
    assert "welcome_message" in result["error"]


def test_complete_setup_applies_stored_identity(monkeypatch):
    setup_api = _import_setup_api()

    realm = _FakeRealm(
        status=RealmStatus.SETUP,
        manifest_data=json.dumps(
            {
                "setup": {
                    "creator_principal": "creator-1",
                    "realm_registry_canister_id": "registry-canister",
                    "codex": {"package": "syntropia", "version": "1.0.0"},
                    "identity": {
                        "manifesto": "Stored manifesto.",
                        "welcome_message": "Stored welcome.",
                    },
                }
            }
        ),
    )
    _FakeRealm.reset(realm)
    mock_ic.caller.return_value.to_str.return_value = "creator-1"
    monkeypatch.setattr(setup_api, "notify_registry_setup_completed", lambda _id: (yield None))

    gen = setup_api.complete_setup()
    try:
        while True:
            next(gen)
    except StopIteration as stop:
        result = json.loads(stop.value)

    assert result["success"] is True
    assert realm.manifesto == "Stored manifesto."
    assert realm.welcome_message == "Stored welcome."


def test_get_setup_state_payload_includes_identity_and_realm_fields():
    realm = _FakeRealm(
        status=RealmStatus.SETUP,
        name="My Realm",
        manifesto="Live manifesto",
        welcome_message="Live welcome",
        manifest_data=json.dumps(
            {
                "setup": {
                    "creator_principal": "creator-1",
                    "identity": {
                        "manifesto": "Config manifesto",
                        "welcome_message": "Config welcome",
                    },
                }
            }
        ),
    )
    _FakeRealm.reset(realm)
    mock_ic.caller.return_value.to_str.return_value = "creator-1"

    payload = setup_core.get_setup_state_payload()

    assert payload["success"] is True
    assert payload["identity"]["manifesto"] == "Config manifesto"
    assert payload["identity"]["welcome_message"] == "Config welcome"
    assert payload["realm_name"] == "My Realm"
    assert payload["realm_manifesto"] == "Live manifesto"
    assert payload["realm_welcome_message"] == "Live welcome"
    assert payload["realm_token_canister_id"] is None
    assert payload["languages"] == ["en"]
    assert payload["primary_language"] == "en"


def test_setup_save_draft_persists_languages_and_requires_primary_in_list():
    setup_api = _import_setup_api()
    _clear_draft_assets(setup_api)

    realm = _FakeRealm(status=RealmStatus.SETUP, manifest_data="{}")
    _authorized_creator(realm)

    rejected = _call_json(
        setup_api.setup_save_draft(
            json.dumps(
                {
                    "step": "languages",
                    "languages": {
                        "languages": ["en"],
                        "primary_language": "ca-valencia",
                    },
                }
            )
        )
    )
    assert rejected["success"] is False
    assert "primary_language" in rejected["error"]

    saved = _call_json(
        setup_api.setup_save_draft(
            json.dumps(
                {
                    "step": "languages",
                    "languages": {
                        "languages": ["en", "ca-valencia"],
                        "primary_language": "ca-valencia",
                    },
                }
            )
        )
    )
    assert saved["success"] is True
    assert saved["draft"]["languages"]["languages"] == ["en", "ca-valencia"]
    assert saved["draft"]["languages"]["primary_language"] == "ca-valencia"
    manifest = json.loads(realm.manifest_data)
    assert manifest["languages"] == ["en", "ca-valencia"]
    assert manifest["primary_language"] == "ca-valencia"


def test_apply_identity_persists_languages_from_draft():
    setup_api = _import_setup_api()

    realm = _FakeRealm(
        status=RealmStatus.SETUP,
        manifest_data=json.dumps({"setup": {"creator_principal": "creator-1"}}),
    )
    _FakeRealm.reset(realm)

    result = setup_api._launch_phase_apply_identity(
        realm,
        {
            "identity": {"manifesto": "Hello"},
            "languages": {
                "languages": ["en", "es"],
                "primary_language": "es",
            },
        },
    )
    assert result["success"] is True
    manifest = json.loads(realm.manifest_data)
    assert manifest["languages"] == ["en", "es"]
    assert manifest["primary_language"] == "es"
    assert realm.manifesto == "Hello"


def test_setup_save_draft_merges_partial_updates():
    setup_api = _import_setup_api()
    _clear_draft_assets(setup_api)

    realm = _FakeRealm(status=RealmStatus.SETUP, manifest_data="{}")
    _authorized_creator(realm)

    first = _call_json(
        setup_api.setup_save_draft(
            json.dumps({"step": "codex", "codex": {"package": "agora", "version": "1.0.0"}})
        )
    )
    assert first["success"] is True
    assert first["draft"]["step"] == "codex"
    assert first["draft"]["codex"]["package"] == "agora"

    second = _call_json(
        setup_api.setup_save_draft(
            json.dumps({"step": "token", "token": {"symbol": "REALM", "decimals": 8}})
        )
    )
    assert second["success"] is True
    assert second["draft"]["step"] == "token"
    assert second["draft"]["codex"]["package"] == "agora"
    assert second["draft"]["token"]["symbol"] == "REALM"


def test_setup_save_draft_stores_images_outside_manifest():
    setup_api = _import_setup_api()
    _clear_draft_assets(setup_api)

    realm = _FakeRealm(status=RealmStatus.SETUP, manifest_data="{}")
    _authorized_creator(realm)

    logo = "data:image/png;base64,QUJD"
    background = "data:image/png;base64,REVGRw=="
    result = _call_json(
        setup_api.setup_save_draft(
            json.dumps(
                {
                    "branding": {
                        "logo_data_url": logo,
                        "background_data_url": background,
                        "colors": {"primary": "#112233"},
                    }
                }
            )
        )
    )
    assert result["success"] is True
    assert result["draft"]["branding"]["logo"] is True
    assert result["draft"]["branding"]["background"] is True
    assert "logo_data_url" not in json.dumps(json.loads(realm.manifest_data))
    assert len(realm.manifest_data) < 4096

    asset = json.loads(setup_api.get_setup_draft_asset("logo"))
    assert asset["success"] is True
    assert asset["data_url"] == logo


def test_setup_save_draft_does_not_install_or_mutate_realm():
    setup_api = _import_setup_api()
    _clear_draft_assets(setup_api)

    realm = _FakeRealm(
        status=RealmStatus.SETUP,
        manifest_data="{}",
        manifesto="",
        welcome_message="",
        token_canister_id="",
    )
    _authorized_creator(realm)

    result = _call_json(
        setup_api.setup_save_draft(
            json.dumps(
                {
                    "codex": {"package": "syntropia", "version": "2.0.0"},
                    "identity": {
                        "manifesto": "Draft manifesto",
                        "welcome_message": "Draft welcome",
                    },
                }
            )
        )
    )
    assert result["success"] is True
    assert realm.status == RealmStatus.SETUP
    assert realm.token_canister_id == ""
    assert realm.manifesto == ""
    assert realm.welcome_message == ""
    assert "codex" not in (json.loads(realm.manifest_data).get("setup") or {})


def test_drive_phase_outcome_accepts_plain_dict():
    setup_api = _import_setup_api()
    gen = setup_api._drive_phase_outcome({"success": True, "skipped": True})
    with pytest.raises(StopIteration) as caught:
        next(gen)
    assert caught.value.value == {"success": True, "skipped": True}


def test_setup_launch_step_code_does_not_need_json():
    """TaskManager execs this shim with a bare namespace; `json` is not imported."""
    _import_setup_api()
    from core.setup import SETUP_LAUNCH_STEP_CODE

    assert "json.dumps" not in SETUP_LAUNCH_STEP_CODE
    ns = {}
    exec(SETUP_LAUNCH_STEP_CODE, ns)
    assert callable(ns.get("async_task"))


def test_begin_setup_launch_blocks_fresh_running_launch():
    realm = _FakeRealm(
        manifest_data=json.dumps(
            {
                "setup": {
                    "draft": {"codex": {"package": "agora", "version": "1.0.0"}},
                    "launch": {
                        "status": "running",
                        "phase": "configure_token",
                        "steps": [
                            {"name": "install_codex", "status": "completed", "error": None},
                            {"name": "configure_token", "status": "pending", "error": None},
                            {"name": "upload_branding", "status": "pending", "error": None},
                            {"name": "apply_identity", "status": "pending", "error": None},
                            {"name": "complete", "status": "pending", "error": None},
                        ],
                        "updated_at": str(mock_ic.time.return_value),
                    },
                }
            }
        )
    )
    _FakeRealm.reset(realm)

    before = json.loads(realm.manifest_data)["setup"]["launch"]
    assert setup_core.begin_setup_launch(realm) is None
    after = json.loads(realm.manifest_data)["setup"]["launch"]
    assert after == before


def test_begin_setup_launch_resumes_stale_running_launch():
    stale_at = mock_ic.time.return_value - setup_core.SETUP_LAUNCH_STALE_NANOS - 1
    realm = _FakeRealm(
        manifest_data=json.dumps(
            {
                "setup": {
                    "draft": {"codex": {"package": "agora", "version": "1.0.0"}},
                    "launch": {
                        "status": "running",
                        "phase": "install_codex",
                        "steps": [
                            {"name": "install_codex", "status": "running", "error": None},
                            {"name": "configure_token", "status": "pending", "error": None},
                            {"name": "upload_branding", "status": "pending", "error": None},
                            {"name": "apply_identity", "status": "pending", "error": None},
                            {"name": "complete", "status": "pending", "error": None},
                        ],
                        "updated_at": str(stale_at),
                    },
                }
            }
        )
    )
    _FakeRealm.reset(realm)

    assert setup_core.begin_setup_launch(realm) is None
    launch = json.loads(realm.manifest_data)["setup"]["launch"]
    assert launch["status"] == "running"
    install_step = next(s for s in launch["steps"] if s["name"] == "install_codex")
    assert install_step["status"] == "pending"
    assert install_step["error"] is None


def test_begin_setup_launch_resumes_stuck_install_codex_phase():
    realm = _FakeRealm(
        manifest_data=json.dumps(
            {
                "setup": {
                    "draft": {"codex": {"package": "agora", "version": "1.0.0"}},
                    "launch": {
                        "status": "running",
                        "phase": "install_codex",
                        "steps": [
                            {"name": "install_codex", "status": "running", "error": None},
                            {"name": "configure_token", "status": "pending", "error": None},
                            {"name": "upload_branding", "status": "pending", "error": None},
                            {"name": "apply_identity", "status": "pending", "error": None},
                            {"name": "complete", "status": "pending", "error": None},
                        ],
                        "updated_at": str(mock_ic.time.return_value),
                    },
                }
            }
        )
    )
    _FakeRealm.reset(realm)

    assert setup_core.begin_setup_launch(realm) is None
    install_step = next(
        s
        for s in json.loads(realm.manifest_data)["setup"]["launch"]["steps"]
        if s["name"] == "install_codex"
    )
    assert install_step["status"] == "pending"


def test_begin_setup_launch_resets_failed_configure_token_even_if_running():
    """Retry must not no-op when a step is failed but parent status is running."""
    stale_err = (
        "No treasury currency — set the treasury ledger "
        "canister in Realm Settings so the token symbol "
        "can be resolved"
    )
    stale_at = str(mock_ic.time.return_value)
    realm = _FakeRealm(
        manifest_data=json.dumps(
            {
                "setup": {
                    "draft": {
                        "codex": {"package": "agora", "version": "1.0.0"},
                        "token": {
                            "symbol": "ckEURC",
                            "token_canister_id": "pe5t5-diaaa-aaaar-qahwa-cai",
                            "decimals": 6,
                        },
                    },
                    "launch": {
                        "status": "running",
                        "phase": "configure_token",
                        "steps": [
                            {"name": "install_codex", "status": "completed", "error": None},
                            {
                                "name": "configure_token",
                                "status": "failed",
                                "error": stale_err,
                            },
                            {"name": "upload_branding", "status": "pending", "error": None},
                            {"name": "apply_identity", "status": "pending", "error": None},
                            {"name": "complete", "status": "pending", "error": None},
                        ],
                        "updated_at": stale_at,
                    },
                }
            }
        )
    )
    _FakeRealm.reset(realm)
    mock_ic.time.return_value = mock_ic.time.return_value + 1

    assert setup_core.begin_setup_launch(realm) is None
    launch = json.loads(realm.manifest_data)["setup"]["launch"]
    token_step = next(s for s in launch["steps"] if s["name"] == "configure_token")
    assert token_step["status"] == "pending"
    assert token_step["error"] is None
    assert launch["status"] == "running"
    assert launch["updated_at"] != stale_at
    assert stale_err not in (token_step.get("error") or "")


def test_complete_setup_persists_registry_id_before_notify(monkeypatch):
    setup_api = _import_setup_api()

    realm = _FakeRealm(
        status=RealmStatus.SETUP,
        manifest_data=json.dumps(
            {
                "setup": {
                    "creator_principal": "creator-1",
                    "codex": {"package": "syntropia", "version": "1.0.0"},
                }
            }
        ),
    )
    _FakeRealm.reset(realm)
    mock_ic.caller.return_value.to_str.return_value = "creator-1"

    persisted = []

    def _track_set(realm_obj, registry_id):
        persisted.append(registry_id)
        setup_core.set_realm_registry_canister_id(realm_obj, registry_id)

    monkeypatch.setattr(setup_api, "set_realm_registry_canister_id", _track_set)
    monkeypatch.setattr(
        setup_api,
        "get_realm_registry_canister_id",
        lambda _realm: "resolved-registry",
    )

    def _fake_notify(registry_id):
        assert persisted == ["resolved-registry"]
        yield None

    monkeypatch.setattr(setup_api, "notify_registry_setup_completed", _fake_notify)

    gen = setup_api.complete_setup()
    try:
        while True:
            next(gen)
    except StopIteration as stop:
        result = json.loads(stop.value)

    assert result["success"] is True
    assert persisted == ["resolved-registry"]
    setup_cfg = json.loads(realm.manifest_data)["setup"]
    assert setup_cfg["realm_registry_canister_id"] == "resolved-registry"


def test_setup_launch_requires_codex_in_draft():
    setup_api = _import_setup_api()
    realm = _FakeRealm(status=RealmStatus.SETUP, manifest_data="{}")
    _authorized_creator(realm)

    result = _call_setup_launch(setup_api)
    assert result["success"] is False
    assert "codex" in result["error"].lower()


def test_setup_configure_token_returns_ledger_unresolvable(monkeypatch):
    setup_api = _import_setup_api()
    realm = _FakeRealm(status=RealmStatus.SETUP, manifest_data="{}")
    _authorized_creator(realm)

    def _unresolved(_ledger):
        result = {"success": False, "error": "offline"}
        yield result
        return result

    tokens_mod = types.ModuleType("api.tokens")
    tokens_mod.resolve_ledger_token_info = _unresolved
    tokens_mod.register_treasury_token = lambda *_args, **_kwargs: None
    monkeypatch.setitem(sys.modules, "api.tokens", tokens_mod)

    result = json.loads(
        _run_async(
            setup_api.setup_configure_token(
                json.dumps({"token_canister_id": "2rqin-xaaaa-aaaah-qunsq-cai"})
            )
        )
    )
    assert result["success"] is False
    assert result["error_code"] == "ledger_unresolvable"
    assert realm.token_canister_id == ""


def test_launch_configure_token_refused_without_treasury_ledger():
    setup_api = _import_setup_api()
    realm = _FakeRealm(
        status=RealmStatus.SETUP,
        manifest_data=json.dumps(
            {
                "setup": {
                    "creator_principal": "creator-1",
                    "draft": {"codex": {"package": "agora", "version": "1.0.0"}},
                }
            }
        ),
        token_canister_id="",
        accounting_currency="",
    )
    _FakeRealm.reset(realm)
    _authorized_creator(realm)

    result = _run_async(setup_api.run_setup_launch_phase(realm, "configure_token"))
    assert result["success"] is False
    assert result["error_code"] == "no_treasury_token"
    assert realm.token_canister_id == ""
    assert realm.accounting_currency == ""
    assert "REALMS" not in (result.get("error") or "")


def test_launch_configure_token_refused_when_token_skipped(monkeypatch):
    setup_api = _import_setup_api()
    _load_real_tokens(monkeypatch)
    realm = _FakeRealm(
        status=RealmStatus.SETUP,
        network="staging",
        manifest_data=json.dumps(
            {
                "setup": {
                    "creator_principal": "creator-1",
                    "draft": {
                        "codex": {"package": "agora", "version": "1.0.0"},
                        "token": None,
                    },
                }
            }
        ),
        token_canister_id="",
        accounting_currency="",
    )
    _FakeRealm.reset(realm)
    _authorized_creator(realm)

    assert setup_api._configured_token_canister_id(realm, {"token": None}) == ""
    result = _run_async(setup_api.run_setup_launch_phase(realm, "configure_token"))
    assert result["success"] is False
    assert result["error_code"] == "no_treasury_token"
    assert realm.token_canister_id == ""
    assert realm.accounting_currency == ""
    assert "REALMS" not in (result.get("error") or "")
    assert result["error"] == _settings_treasury_message()


def test_launch_configure_token_refused_when_ledger_unresolvable(monkeypatch):
    setup_api = _import_setup_api()
    realm = _FakeRealm(
        status=RealmStatus.SETUP,
        manifest_data=json.dumps(
            {
                "setup": {
                    "creator_principal": "creator-1",
                    "draft": {
                        "codex": {"package": "agora", "version": "1.0.0"},
                        "token": {"token_canister_id": "token-abc"},
                    },
                }
            }
        ),
        token_canister_id="",
        accounting_currency="",
    )
    _FakeRealm.reset(realm)
    _authorized_creator(realm)

    def _unresolved(_ledger):
        result = {"success": False, "error": "offline"}
        yield result
        return result

    tokens_mod = types.ModuleType("api.tokens")
    tokens_mod.resolve_ledger_token_info = _unresolved
    tokens_mod.register_treasury_token = lambda *_args, **_kwargs: None
    monkeypatch.setitem(sys.modules, "api.tokens", tokens_mod)

    result = _run_async(setup_api.run_setup_launch_phase(realm, "configure_token"))
    assert result["success"] is False
    assert result["error_code"] == "ledger_unresolvable"
    assert result["error"] == "offline"
    assert result["error"] != _settings_treasury_message()
    assert realm.token_canister_id == ""
    assert realm.accounting_currency == ""


def test_setup_configure_token_writes_realm_token_canister_id(monkeypatch):
    """Token Continue / founder start-of-Launch persist realm.token_canister_id."""
    setup_api = _import_setup_api()
    ck_eurc = "pe5t5-diaaa-aaaar-qahwa-cai"
    realm = _FakeRealm(
        status=RealmStatus.SETUP,
        network="staging",
        manifest_data=json.dumps({"setup": {"creator_principal": "creator-1"}}),
        token_canister_id="",
        accounting_currency="",
    )
    _authorized_creator(realm)
    tokens_mod = _load_real_tokens(monkeypatch)
    monkeypatch.setattr(
        tokens_mod,
        "Icrc1MetadataService",
        MagicMock(side_effect=RuntimeError("offline")),
    )

    result = json.loads(
        _run_async(
            setup_api.setup_configure_token(
                json.dumps(
                    {
                        "token_canister_id": ck_eurc,
                        "symbol": "ckEURC",
                        "decimals": 6,
                    }
                )
            )
        )
    )
    assert result["success"] is True
    assert realm.token_canister_id == ck_eurc
    assert realm.accounting_currency == "ckEURC"
    setup_cfg = json.loads(realm.manifest_data)["setup"]
    assert setup_cfg["token"]["token_canister_id"] == ck_eurc
    assert setup_cfg["token"]["symbol"] == "ckEURC"


def test_leftover_old_launch_succeeds_after_founder_configure_token(monkeypatch):
    """Create-day launch ignored draft.token; founder apply unblocks it."""
    setup_api = _import_setup_api()
    ck_eurc = "pe5t5-diaaa-aaaar-qahwa-cai"
    realm = _FakeRealm(
        status=RealmStatus.SETUP,
        network="staging",
        manifest_data=json.dumps(
            {
                "setup": {
                    "creator_principal": "creator-1",
                    "draft": {
                        "codex": {"package": "agora", "version": "1.0.0"},
                        "token": {
                            "symbol": "ckEURC",
                            "token_canister_id": ck_eurc,
                            "decimals": 6,
                        },
                    },
                }
            }
        ),
        token_canister_id="",
        accounting_currency="",
    )
    _authorized_creator(realm)
    tokens_mod = _load_real_tokens(monkeypatch)
    monkeypatch.setattr(
        tokens_mod,
        "Icrc1MetadataService",
        MagicMock(side_effect=RuntimeError("offline")),
    )

    from core.realm_currency import no_treasury_token_error, realm_currency

    def _old_launch(realm_obj, _draft):
        ledger = (getattr(realm_obj, "token_canister_id", "") or "").strip()
        if not ledger or not realm_currency():
            return {"success": False, **no_treasury_token_error()}
        return {"success": True, "skipped": True, "token": {"token_canister_id": ledger}}

    refused = _old_launch(realm, setup_core.get_setup_draft(realm))
    assert refused["success"] is False
    assert refused["error_code"] == "no_treasury_token"
    assert realm.token_canister_id == ""

    applied = json.loads(
        _run_async(
            setup_api.setup_configure_token(
                json.dumps({"token_canister_id": ck_eurc, "symbol": "ckEURC"})
            )
        )
    )
    assert applied["success"] is True
    assert realm.token_canister_id == ck_eurc
    assert realm.accounting_currency == "ckEURC"

    leftover_ok = _old_launch(realm, setup_core.get_setup_draft(realm))
    assert leftover_ok["success"] is True


def test_retry_after_draft_gains_ledger_reruns_configure_token(monkeypatch):
    """Failed Settings row must be reset; Retry re-runs configure_token on pe5t5."""
    setup_api = _import_setup_api()
    ck_eurc = "pe5t5-diaaa-aaaar-qahwa-cai"
    stale_updated_at = "1787858624611611294"
    settings_err = _settings_treasury_message()
    realm = _FakeRealm(
        status=RealmStatus.SETUP,
        network="staging",
        manifest_data=json.dumps(
            {
                "setup": {
                    "creator_principal": "creator-1",
                    "draft": {
                        "step": "review",
                        "codex": {"package": "agora", "version": "1.0.0"},
                        "token": {
                            "symbol": "ckEURC",
                            "token_canister_id": ck_eurc,
                            "decimals": 6,
                        },
                    },
                    "launch": {
                        "status": "failed",
                        "phase": "configure_token",
                        "steps": [
                            {"name": "install_codex", "status": "completed", "error": None},
                            {
                                "name": "configure_token",
                                "status": "failed",
                                "error": settings_err,
                            },
                            {"name": "upload_branding", "status": "pending", "error": None},
                            {"name": "apply_identity", "status": "pending", "error": None},
                            {"name": "complete", "status": "pending", "error": None},
                        ],
                        "updated_at": stale_updated_at,
                    },
                }
            }
        ),
        token_canister_id="",
        accounting_currency="",
    )
    _authorized_creator(realm)
    tokens_mod = _load_real_tokens(monkeypatch)
    monkeypatch.setattr(
        tokens_mod,
        "Icrc1MetadataService",
        MagicMock(side_effect=RuntimeError("offline")),
    )
    monkeypatch.setattr("core.quarter_bootstrap.seed_recurring_codex_task", lambda *_a, **_k: None)
    monkeypatch.setattr("core.quarter_bootstrap.disable_recurring_task", lambda *_a, **_k: None)

    assert setup_core.begin_setup_launch(realm) is None
    launch = json.loads(realm.manifest_data)["setup"]["launch"]
    token_step = next(s for s in launch["steps"] if s["name"] == "configure_token")
    assert token_step["status"] == "pending"
    assert token_step["error"] is None
    assert launch["status"] == "running"
    assert launch["updated_at"] != stale_updated_at

    result = _run_async(setup_api.run_setup_launch_phase(realm, "configure_token"))
    assert result["success"] is True
    assert result.get("error_code") != "no_treasury_token"
    assert "Realm Settings" not in (result.get("error") or "")
    assert realm.token_canister_id == ck_eurc
    assert realm.accounting_currency == "ckEURC"


def test_setup_launch_retry_drives_configure_token_when_draft_has_pe5t5(monkeypatch):
    """setup_launch Retry re-executes configure_token instead of leaving the stale row."""
    setup_api = _import_setup_api()
    ck_eurc = "pe5t5-diaaa-aaaar-qahwa-cai"
    stale_updated_at = "1787858624611611294"
    settings_err = _settings_treasury_message()
    realm = _FakeRealm(
        status=RealmStatus.SETUP,
        network="staging",
        manifest_data=json.dumps(
            {
                "setup": {
                    "creator_principal": "creator-1",
                    "draft": {
                        "codex": {"package": "agora", "version": "1.0.0"},
                        "token": {
                            "symbol": "ckEURC",
                            "token_canister_id": ck_eurc,
                            "decimals": 6,
                        },
                    },
                    "launch": {
                        "status": "failed",
                        "phase": "configure_token",
                        "steps": [
                            {"name": "install_codex", "status": "completed", "error": None},
                            {
                                "name": "configure_token",
                                "status": "failed",
                                "error": settings_err,
                            },
                            {"name": "upload_branding", "status": "pending", "error": None},
                            {"name": "apply_identity", "status": "pending", "error": None},
                            {"name": "complete", "status": "pending", "error": None},
                        ],
                        "updated_at": stale_updated_at,
                    },
                }
            }
        ),
        token_canister_id="",
        accounting_currency="",
    )
    _authorized_creator(realm)
    tokens_mod = _load_real_tokens(monkeypatch)
    monkeypatch.setattr(
        tokens_mod,
        "Icrc1MetadataService",
        MagicMock(side_effect=RuntimeError("offline")),
    )
    monkeypatch.setattr("core.quarter_bootstrap.seed_recurring_codex_task", lambda *_a, **_k: None)
    monkeypatch.setattr("core.quarter_bootstrap.disable_recurring_task", lambda *_a, **_k: None)

    result = _call_setup_launch(setup_api)
    assert result["success"] is True
    launch = result["launch"]
    assert launch["status"] == "running"
    token_step = next(s for s in launch["steps"] if s["name"] == "configure_token")
    assert token_step["status"] == "pending"
    assert token_step["error"] is None
    assert launch["updated_at"] != stale_updated_at
    assert realm.token_canister_id == ""

    applied = _run_async(setup_api.run_setup_launch_phase(realm, "configure_token"))
    assert applied["success"] is True
    assert realm.token_canister_id == ck_eurc
    assert realm.accounting_currency == "ckEURC"


def test_setup_launch_writes_realm_ledger_when_tick_is_dead(monkeypatch):
    """Launch returns immediately. The ledger is applied by the configure_token phase."""
    setup_api = _import_setup_api()
    ck_eurc = "pe5t5-diaaa-aaaar-qahwa-cai"
    stale_updated_at = "1787858624611611294"
    settings_err = _settings_treasury_message()
    realm = _FakeRealm(
        status=RealmStatus.SETUP,
        network="staging",
        manifest_data=json.dumps(
            {
                "setup": {
                    "creator_principal": "creator-1",
                    "draft": {
                        "codex": {"package": "agora", "version": "1.0.0"},
                        "token": {
                            "symbol": "ckEURC",
                            "token_canister_id": ck_eurc,
                            "decimals": 6,
                        },
                    },
                    "launch": {
                        "status": "failed",
                        "phase": "configure_token",
                        "steps": [
                            {"name": "install_codex", "status": "completed", "error": None},
                            {
                                "name": "configure_token",
                                "status": "failed",
                                "error": settings_err,
                            },
                            {"name": "upload_branding", "status": "pending", "error": None},
                            {"name": "apply_identity", "status": "pending", "error": None},
                            {"name": "complete", "status": "pending", "error": None},
                        ],
                        "updated_at": stale_updated_at,
                    },
                }
            }
        ),
        token_canister_id="",
        accounting_currency="",
    )
    _authorized_creator(realm)
    tokens_mod = _load_real_tokens(monkeypatch)
    monkeypatch.setattr(
        tokens_mod,
        "Icrc1MetadataService",
        MagicMock(side_effect=RuntimeError("offline")),
    )
    monkeypatch.setattr(
        "core.quarter_bootstrap.seed_recurring_codex_task",
        MagicMock(side_effect=RuntimeError("seed dead")),
    )
    monkeypatch.setattr("core.quarter_bootstrap.disable_recurring_task", lambda *_a, **_k: None)

    def _dead_advance():
        raise RuntimeError("tick dead")

    monkeypatch.setattr(setup_api, "advance_setup_launch", _dead_advance)

    result = _call_setup_launch(setup_api)
    assert result["success"] is True
    assert result["launch"]["status"] == "running"
    assert realm.token_canister_id == ""
    assert result.get("error_code") != "no_treasury_token"
    assert settings_err not in (result.get("error") or "")

    second = _run_async(setup_api.run_setup_launch_phase(realm, "configure_token"))
    assert second["success"] is True
    assert second.get("error_code") != "no_treasury_token"
    assert "Realm Settings" not in (second.get("error") or "")
    assert realm.token_canister_id == ck_eurc


def test_setup_configure_token_denies_non_founder():
    setup_api = _import_setup_api()
    realm = _FakeRealm(
        status=RealmStatus.SETUP,
        manifest_data=json.dumps({"setup": {"creator_principal": "creator-1"}}),
        token_canister_id="",
        accounting_currency="",
    )
    _FakeRealm.reset(realm)
    assert mock_ic.caller.return_value.to_str.return_value == "stranger-principal"

    result = json.loads(
        _run_async(
            setup_api.setup_configure_token(
                json.dumps({"token_canister_id": "pe5t5-diaaa-aaaar-qahwa-cai"})
            )
        )
    )
    assert result["success"] is False
    assert "Access denied" in result["error"]
    assert result.get("error_code") != "no_treasury_token"
    assert result["error"] != _settings_treasury_message()
    assert realm.token_canister_id == ""
    assert realm.accounting_currency == ""


def test_launch_configure_token_applies_draft_pe5t5_without_founder_caller(monkeypatch):
    """Launch-phase task (canister caller) applies draft pe5t5 via catalog fallback."""
    setup_api = _import_setup_api()
    ck_eurc = "pe5t5-diaaa-aaaar-qahwa-cai"
    realm = _FakeRealm(
        status=RealmStatus.SETUP,
        network="staging",
        manifest_data=json.dumps(
            {
                "setup": {
                    "creator_principal": "creator-1",
                    "draft": {
                        "codex": {"package": "agora", "version": "1.0.0"},
                        "token": {
                            "token_canister_id": ck_eurc,
                            "symbol": "ckEURC",
                        },
                    },
                }
            }
        ),
        token_canister_id="",
        accounting_currency="",
    )
    _FakeRealm.reset(realm)
    assert mock_ic.caller.return_value.to_str.return_value == "stranger-principal"

    tokens_mod = _load_real_tokens(monkeypatch)
    monkeypatch.setattr(
        tokens_mod,
        "Icrc1MetadataService",
        MagicMock(side_effect=RuntimeError("offline")),
    )

    result = _run_async(setup_api.run_setup_launch_phase(realm, "configure_token"))
    assert result["success"] is True
    assert "Realm Settings" not in (result.get("error") or "")
    assert realm.token_canister_id == ck_eurc
    assert realm.accounting_currency == "ckEURC"
    assert realm.accounting_currency != "REALMS"
    setup_cfg = json.loads(realm.manifest_data)["setup"]
    assert setup_cfg["token"]["symbol"] == "ckEURC"
    assert setup_cfg["token"]["token_canister_id"] == ck_eurc


def test_configured_token_id_resolves_ckeurc_symbol_without_ledger(monkeypatch):
    setup_api = _import_setup_api()
    _load_real_tokens(monkeypatch)
    ck_eurc = "pe5t5-diaaa-aaaar-qahwa-cai"
    realm = _FakeRealm(
        status=RealmStatus.SETUP,
        network="",
        manifest_data=json.dumps(
            {
                "setup": {
                    "creator_principal": "creator-1",
                    "draft": {"token": {"symbol": "ckEURC"}},
                }
            }
        ),
        token_canister_id="",
        accounting_currency="",
    )
    _FakeRealm.reset(realm)

    assert setup_api._configured_token_canister_id(realm, {"token": {"symbol": "ckEURC"}}) == ck_eurc
    assert setup_api._configured_token_canister_id(realm, {"token": "ckEURC"}) == ck_eurc
    assert setup_api._configured_token_canister_id(realm, {"token": "ckEURC "}) == ck_eurc
    assert setup_api._configured_token_canister_id(realm, {"token": {"id": "ckEURC"}}) == ck_eurc
    assert setup_api._configured_token_canister_id(realm, {"token": {"existing": "ckEURC"}}) == ck_eurc
    assert setup_api._configured_token_canister_id(realm, {"token": None}) == ""
    assert setup_api._configured_token_canister_id(realm, {}) == ""
    assert setup_api._token_record("ckEURC") == {"symbol": "ckEURC"}
    assert setup_api._token_record("ckEURC ") == {"symbol": "ckEURC"}
    assert setup_api._token_record({"id": "ckEURC"})["symbol"] == "ckEURC"
    assert setup_api._token_record({"existing": "ckEURC"})["symbol"] == "ckEURC"
    assert setup_api._token_record(None) is None
    assert setup_api._token_record("") is None
    assert setup_api._token_record("   ") is None


def test_launch_configure_token_applies_ckeurc_from_symbol_only_draft(monkeypatch):
    setup_api = _import_setup_api()
    ck_eurc = "pe5t5-diaaa-aaaar-qahwa-cai"
    realm = _FakeRealm(
        status=RealmStatus.SETUP,
        network="",
        manifest_data=json.dumps(
            {
                "setup": {
                    "creator_principal": "creator-1",
                    "draft": {
                        "codex": {"package": "agora", "version": "1.0.0"},
                        "token": {"symbol": "ckEURC"},
                    },
                }
            }
        ),
        token_canister_id="",
        accounting_currency="",
    )
    _FakeRealm.reset(realm)
    assert mock_ic.caller.return_value.to_str.return_value == "stranger-principal"

    tokens_mod = _load_real_tokens(monkeypatch)
    monkeypatch.setattr(
        tokens_mod,
        "Icrc1MetadataService",
        MagicMock(side_effect=RuntimeError("offline")),
    )

    result = _run_async(setup_api.run_setup_launch_phase(realm, "configure_token"))
    assert result["success"] is True
    assert "Realm Settings" not in (result.get("error") or "")
    assert realm.token_canister_id == ck_eurc
    assert realm.accounting_currency == "ckEURC"
    assert realm.accounting_currency != "REALMS"
    setup_cfg = json.loads(realm.manifest_data)["setup"]
    assert setup_cfg["token"]["symbol"] == "ckEURC"
    assert setup_cfg["token"]["token_canister_id"] == ck_eurc


def test_setup_save_draft_fills_ckeurc_ledger_from_symbol(monkeypatch):
    setup_api = _import_setup_api()
    _load_tokens_offline(monkeypatch)
    ck_eurc = "pe5t5-diaaa-aaaar-qahwa-cai"
    realm = _FakeRealm(
        status=RealmStatus.SETUP,
        network="staging",
        manifest_data=json.dumps({"setup": {"creator_principal": "creator-1"}}),
        token_canister_id="",
        accounting_currency="",
    )
    _authorized_creator(realm)

    saved = _call_json(
        setup_api.setup_save_draft(json.dumps({"step": "branding", "token": {"symbol": "ckEURC"}}))
    )
    assert saved["success"] is True
    assert saved["draft"]["token"]["symbol"] == "ckEURC"
    assert saved["draft"]["token"]["token_canister_id"] == ck_eurc
    assert saved["draft"]["token"]["decimals"] == 6
    assert saved["draft"]["token"]["indexer_canister_id"] == ck_eurc
    assert realm.token_canister_id == ""
    assert realm.accounting_currency == ""
    assert "Realm Settings" not in (saved.get("error") or "")


@pytest.mark.parametrize(
    "token_payload",
    ["ckEURC", "ckEURC ", {"id": "ckEURC"}, {"symbol": "ckEURC"}, {"existing": "ckEURC"}],
)
def test_setup_save_draft_coerces_realistic_ckeurc_shapes(monkeypatch, token_payload):
    setup_api = _import_setup_api()
    _load_tokens_offline(monkeypatch)
    ck_eurc = "pe5t5-diaaa-aaaar-qahwa-cai"
    realm = _FakeRealm(
        status=RealmStatus.SETUP,
        network="staging",
        manifest_data=json.dumps({"setup": {"creator_principal": "creator-1"}}),
        token_canister_id="",
        accounting_currency="",
    )
    _authorized_creator(realm)

    saved = _call_json(
        setup_api.setup_save_draft(json.dumps({"step": "branding", "token": token_payload}))
    )
    assert saved["success"] is True
    assert saved["draft"]["token"]["symbol"] == "ckEURC"
    assert saved["draft"]["token"]["token_canister_id"] == ck_eurc
    assert saved["draft"]["token"]["decimals"] == 6
    assert saved["draft"]["token"]["indexer_canister_id"] == ck_eurc
    assert realm.token_canister_id == ""
    assert realm.accounting_currency == ""


@pytest.mark.parametrize(
    "token_payload",
    ["ckEURC", {"id": "ckEURC"}, {"existing": "ckEURC"}],
)
def test_launch_configure_token_applies_ckeurc_from_realistic_draft_shapes(
    monkeypatch, token_payload
):
    setup_api = _import_setup_api()
    ck_eurc = "pe5t5-diaaa-aaaar-qahwa-cai"
    realm = _FakeRealm(
        status=RealmStatus.SETUP,
        network="",
        manifest_data=json.dumps(
            {
                "setup": {
                    "creator_principal": "creator-1",
                    "draft": {
                        "codex": {"package": "agora", "version": "1.0.0"},
                        "token": token_payload,
                    },
                }
            }
        ),
        token_canister_id="",
        accounting_currency="",
    )
    _FakeRealm.reset(realm)
    tokens_mod = _load_real_tokens(monkeypatch)
    monkeypatch.setattr(
        tokens_mod,
        "Icrc1MetadataService",
        MagicMock(side_effect=RuntimeError("offline")),
    )

    result = _run_async(setup_api.run_setup_launch_phase(realm, "configure_token"))
    assert result["success"] is True
    assert "Realm Settings" not in (result.get("error") or "")
    assert realm.token_canister_id == ck_eurc
    assert realm.accounting_currency == "ckEURC"
    assert realm.accounting_currency != "REALMS"


def test_setup_save_draft_skipped_token_does_not_invent_realms(monkeypatch):
    setup_api = _import_setup_api()
    _load_real_tokens(monkeypatch)
    realm = _FakeRealm(
        status=RealmStatus.SETUP,
        network="staging",
        manifest_data=json.dumps(
            {
                "setup": {
                    "creator_principal": "creator-1",
                    "draft": {"token": {"symbol": "ckEURC"}},
                }
            }
        ),
        token_canister_id="",
        accounting_currency="",
    )
    _authorized_creator(realm)

    saved = _call_json(
        setup_api.setup_save_draft(json.dumps({"step": "branding", "token": None}))
    )
    assert saved["success"] is True
    assert "token" not in saved["draft"]
    assert realm.token_canister_id == ""
    assert realm.accounting_currency == ""


def test_launch_configure_token_proceeds_when_ledger_resolves(monkeypatch):
    setup_api = _import_setup_api()
    realm = _FakeRealm(
        status=RealmStatus.SETUP,
        manifest_data=json.dumps(
            {
                "setup": {
                    "creator_principal": "creator-1",
                    "draft": {
                        "codex": {"package": "agora", "version": "1.0.0"},
                        "token": {"token_canister_id": "token-abc"},
                    },
                }
            }
        ),
        token_canister_id="",
        accounting_currency="",
    )
    _FakeRealm.reset(realm)
    _authorized_creator(realm)

    def _resolved(_ledger):
        result = {
            "success": True,
            "symbol": "AG",
            "decimals": 8,
            "indexer_canister_id": "token-abc",
        }
        yield result
        return result

    tokens_mod = types.ModuleType("api.tokens")
    tokens_mod.resolve_ledger_token_info = _resolved
    tokens_mod.register_treasury_token = lambda *_args, **_kwargs: None
    monkeypatch.setitem(sys.modules, "api.tokens", tokens_mod)

    result = _run_async(setup_api.run_setup_launch_phase(realm, "configure_token"))
    assert result["success"] is True
    assert realm.token_canister_id == "token-abc"
    assert realm.accounting_currency == "AG"
    setup_cfg = json.loads(realm.manifest_data)["setup"]
    assert setup_cfg["token"]["symbol"] == "AG"


def test_failed_launch_token_draft_persists_for_configure_token(monkeypatch):
    """Founder leaves a failed Launch, saves catalog ckEURC, retry reads the ledger."""
    setup_api = _import_setup_api()
    ck_eurc = "pe5t5-diaaa-aaaar-qahwa-cai"
    realm = _FakeRealm(
        status=RealmStatus.SETUP,
        network="staging",
        manifest_data=json.dumps(
            {
                "setup": {
                    "creator_principal": "creator-1",
                    "codex": {"package": "agora", "version": "1.0.0"},
                    "draft": {
                        "step": "review",
                        "codex": {"package": "agora", "version": "1.0.0"},
                    },
                    "launch": {
                        "status": "failed",
                        "phase": "configure_token",
                        "steps": [
                            {"name": "install_codex", "status": "completed", "error": None},
                            {
                                "name": "configure_token",
                                "status": "failed",
                                "error": (
                                    "No treasury currency — set the treasury ledger "
                                    "canister in Realm Settings so the token symbol "
                                    "can be resolved"
                                ),
                            },
                            {"name": "upload_branding", "status": "pending", "error": None},
                            {"name": "apply_identity", "status": "pending", "error": None},
                            {"name": "complete", "status": "pending", "error": None},
                        ],
                        "updated_at": "1",
                    },
                }
            }
        ),
        token_canister_id="",
        accounting_currency="",
    )
    _authorized_creator(realm)
    _load_tokens_offline(monkeypatch)

    saved = _call_json(
        setup_api.setup_save_draft(
            json.dumps(
                {
                    "step": "branding",
                    "token": {"symbol": "ckEURC", "token_canister_id": ck_eurc, "decimals": 6},
                }
            )
        )
    )
    assert saved["success"] is True
    assert saved["draft"]["token"]["token_canister_id"] == ck_eurc
    assert saved["draft"]["token"]["symbol"] == "ckEURC"
    assert "Realm Settings" not in (saved.get("error") or "")
    assert saved.get("error_code") != "no_treasury_token"
    assert realm.token_canister_id == ""
    assert realm.accounting_currency == ""

    draft = setup_core.get_setup_draft(realm)
    assert setup_api._configured_token_canister_id(realm, draft) == ck_eurc

    result = _run_async(setup_api.run_setup_launch_phase(realm, "configure_token"))
    assert result["success"] is True
    assert "Realm Settings" not in (result.get("error") or "")
    assert realm.token_canister_id == ck_eurc
    assert realm.accounting_currency == "ckEURC"


def test_setup_save_draft_with_pe5t5_writes_realm_token_canister_id(monkeypatch):
    """save_draft stores the ledger on the draft and does not call the token canister."""
    setup_api = _import_setup_api()
    _load_tokens_offline(monkeypatch)
    ck_eurc = "pe5t5-diaaa-aaaar-qahwa-cai"
    realm = _FakeRealm(
        status=RealmStatus.SETUP,
        network="staging",
        manifest_data=json.dumps({"setup": {"creator_principal": "creator-1"}}),
        token_canister_id="",
        accounting_currency="",
    )
    _authorized_creator(realm)

    saved = _call_setup_save_draft(
        setup_api,
        {
            "step": "branding",
            "token": {
                "symbol": "ckEURC",
                "token_canister_id": ck_eurc,
                "decimals": 6,
            },
        },
    )
    assert saved["success"] is True
    assert realm.token_canister_id == ""
    assert realm.accounting_currency == ""
    assert "Realm Settings" not in (saved.get("error") or "")
    setup_cfg = json.loads(realm.manifest_data)["setup"]
    assert setup_cfg["draft"]["token"]["token_canister_id"] == ck_eurc
    after_save = setup_core.get_setup_state_payload()
    assert after_save["realm_token_canister_id"] is None
    assert after_save["draft"]["token"]["token_canister_id"] == ck_eurc


def test_fossil_failed_launch_save_draft_apply_does_not_return_settings(monkeypatch):
    """Valencia fossil Settings row + save_draft/apply writes pe5t5, no Settings."""
    setup_api = _import_setup_api()
    ck_eurc = "pe5t5-diaaa-aaaar-qahwa-cai"
    settings_err = _settings_treasury_message()
    realm = _FakeRealm(
        status=RealmStatus.SETUP,
        network="staging",
        manifest_data=json.dumps(
            {
                "setup": {
                    "creator_principal": "creator-1",
                    "draft": {
                        "step": "review",
                        "codex": {"package": "agora", "version": "1.0.0"},
                    },
                    "launch": {
                        "status": "failed",
                        "phase": "configure_token",
                        "steps": [
                            {"name": "install_codex", "status": "completed", "error": None},
                            {
                                "name": "configure_token",
                                "status": "failed",
                                "error": settings_err,
                            },
                            {"name": "upload_branding", "status": "pending", "error": None},
                            {"name": "apply_identity", "status": "pending", "error": None},
                            {"name": "complete", "status": "pending", "error": None},
                        ],
                        "updated_at": "1787858624611611294",
                    },
                }
            }
        ),
        token_canister_id="",
        accounting_currency="",
    )
    _authorized_creator(realm)
    _load_tokens_offline(monkeypatch)

    saved = _call_setup_save_draft(
        setup_api,
        {
            "token": {
                "symbol": "ckEURC",
                "token_canister_id": ck_eurc,
                "decimals": 6,
            }
        },
    )
    assert saved["success"] is True
    assert saved.get("error") != settings_err
    assert "Realm Settings" not in (saved.get("error") or "")
    assert realm.token_canister_id == ""

    applied = _call_setup_apply_draft_token(setup_api)
    assert applied["success"] is True
    assert applied.get("error_code") != "no_treasury_token"
    assert applied.get("error") != settings_err
    assert "Realm Settings" not in (applied.get("error") or "")
    assert applied["token"]["token_canister_id"] == ck_eurc
    assert realm.token_canister_id == ck_eurc
    assert realm.accounting_currency != "REALMS"
    state = setup_core.get_setup_state_payload()
    assert state["realm_token_canister_id"] == ck_eurc
    assert state["token"]["token_canister_id"] == ck_eurc


def test_setup_apply_draft_token_null_token_fail_closed(monkeypatch):
    setup_api = _import_setup_api()
    _load_tokens_offline(monkeypatch)
    realm = _FakeRealm(
        status=RealmStatus.SETUP,
        network="staging",
        manifest_data=json.dumps({"setup": {"creator_principal": "creator-1"}}),
        token_canister_id="",
        accounting_currency="",
    )
    _authorized_creator(realm)

    saved = _call_setup_save_draft(setup_api, {"step": "branding", "token": None})
    assert saved["success"] is True
    assert "token" not in saved["draft"]

    applied = _call_setup_apply_draft_token(setup_api)
    assert applied["success"] is False
    assert applied["error"] == "token_canister_id is required"
    assert applied.get("error_code") != "no_treasury_token"
    assert realm.token_canister_id == ""
    assert realm.accounting_currency == ""
    assert realm.accounting_currency != "REALMS"


def test_apply_draft_token_now_hard_errors_when_symbol_cannot_apply(monkeypatch):
    """Draft token with a symbol/ledger must not fall through to the Settings tick."""
    setup_api = _import_setup_api()
    _load_tokens_offline(monkeypatch)
    realm = _FakeRealm(
        status=RealmStatus.SETUP,
        network="staging",
        manifest_data=json.dumps(
            {
                "setup": {
                    "creator_principal": "creator-1",
                    "draft": {
                        "codex": {"package": "agora", "version": "1.0.0"},
                        "token": {"symbol": "NOT_A_CATALOG_TOKEN"},
                    },
                }
            }
        ),
        token_canister_id="",
        accounting_currency="",
    )
    _authorized_creator(realm)

    apply_now = _run_async(setup_api._apply_draft_token_now(realm))
    assert apply_now["success"] is False
    assert apply_now["error_code"] == "draft_token_unapplied"
    assert apply_now["error"] != _settings_treasury_message()
    assert "Realm Settings" not in apply_now["error"]
    assert realm.token_canister_id == ""

    launched = _call_setup_launch(setup_api)
    assert launched["success"] is True
    assert launched["launch"]["status"] == "running"
    assert realm.token_canister_id == ""


def test_draft_realm_saveable_without_treasury_ledger():
    setup_api = _import_setup_api()
    realm = _FakeRealm(
        status=RealmStatus.SETUP,
        manifest_data="{}",
        token_canister_id="",
        accounting_currency="",
    )
    _authorized_creator(realm)

    result = _call_json(
        setup_api.setup_save_draft(
            json.dumps(
                {
                    "step": "token",
                    "codex": {"package": "agora", "version": "1.0.0"},
                }
            )
        )
    )
    assert result["success"] is True
    assert realm.status == RealmStatus.SETUP
    assert realm.token_canister_id == ""
    assert realm.accounting_currency == ""
    assert "token" not in result["draft"]


def test_unshadowed_apply_writes_pe5t5_when_leftover_save_only_persists_draft(monkeypatch):
    """Leftover api.setup can steal save_draft; core.setup_draft_token still applies."""
    ck_eurc = "pe5t5-diaaa-aaaar-qahwa-cai"
    realm = _FakeRealm(
        status=RealmStatus.SETUP,
        network="staging",
        manifest_data=json.dumps(
            {
                "setup": {
                    "creator_principal": "creator-1",
                    "draft": {
                        "codex": {"package": "agora", "version": "1.0.0"},
                        "token": {
                            "symbol": "ckEURC",
                            "token_canister_id": ck_eurc,
                            "decimals": 6,
                        },
                    },
                    "launch": {
                        "status": "failed",
                        "phase": "configure_token",
                        "steps": [
                            {"name": "configure_token", "status": "failed", "error": _settings_treasury_message()},
                        ],
                    },
                }
            }
        ),
        token_canister_id="",
        accounting_currency="",
    )
    _authorized_creator(realm)
    _load_tokens_offline(monkeypatch)
    from core.setup_draft_token import (
        apply_persisted_draft_if_present,
        apply_setup_draft_token_now,
    )

    skipped_or_applied = _run_async(apply_persisted_draft_if_present())
    assert skipped_or_applied.get("success") is True
    assert "Realm Settings" not in (skipped_or_applied.get("error") or "")
    assert realm.token_canister_id == ck_eurc
    assert realm.accounting_currency == "ckEURC"

    realm.token_canister_id = ""
    realm.accounting_currency = ""
    applied = json.loads(_run_async(apply_setup_draft_token_now()))
    assert applied["success"] is True
    assert applied["token"]["token_canister_id"] == ck_eurc
    assert applied.get("error_code") != "no_treasury_token"
    assert realm.token_canister_id == ck_eurc


def test_unshadowed_apply_null_token_fail_closed(monkeypatch):
    realm = _FakeRealm(
        status=RealmStatus.SETUP,
        network="staging",
        manifest_data=json.dumps({"setup": {"creator_principal": "creator-1", "draft": {}}}),
        token_canister_id="",
        accounting_currency="",
    )
    _authorized_creator(realm)
    _load_tokens_offline(monkeypatch)
    from core.setup_draft_token import apply_setup_draft_token_now

    applied = json.loads(_run_async(apply_setup_draft_token_now()))
    assert applied["success"] is False
    assert applied["error"] == "token_canister_id is required"
    assert realm.token_canister_id == ""
    assert realm.accounting_currency == ""


def test_main_setup_apply_does_not_import_api_setup():
    main_src = Path(__file__).resolve().parents[1] / "main.py"
    text = main_src.read_text()
    apply_start = text.index("def setup_apply_draft_token()")
    apply_end = text.index("def get_setup_draft_asset", apply_start)
    body = text[apply_start:apply_end]
    assert "from core.setup_draft_token import apply_setup_draft_token_now" in body
    assert "from api.setup import" not in body
    save_start = text.index("def setup_save_draft(")
    save_end = text.index("def setup_apply_draft_token()", save_start)
    save_body = text[save_start:save_end]
    assert "apply_persisted_draft_if_present" not in save_body


def test_setup_launch_runs_phases_in_order(monkeypatch):
    setup_api = _import_setup_api()
    _clear_draft_assets(setup_api)

    realm = _FakeRealm(
        status=RealmStatus.SETUP,
        manifest_data=json.dumps(
            {
                "setup": {
                    "creator_principal": "creator-1",
                    "draft": {
                        "codex": {"package": "agora", "version": "1.0.0"},
                        "token": {"token_canister_id": "token-1", "symbol": "AG"},
                        "identity": {
                            "manifesto": "Our realm.",
                            "welcome_message": "Welcome!",
                        },
                    },
                    "launch": {
                        "status": "running",
                        "phase": None,
                        "steps": [
                            {"name": n, "status": "pending", "error": None}
                            for n, _ in setup_core.SETUP_LAUNCH_PHASES
                        ],
                        "updated_at": "1",
                    },
                }
            }
        ),
    )
    _authorized_creator(realm)

    order = []

    def _fake_run(realm_obj, phase_name):
        order.append(phase_name)
        if phase_name == "install_codex":
            setup_core.update_setup_config(
                realm_obj, {"codex": {"package": "agora", "version": "1.0.0"}}
            )
            return {"success": True}
        if phase_name == "configure_token":
            realm_obj.token_canister_id = "token-1"
            setup_core.update_setup_config(
                realm_obj, {"token": {"token_canister_id": "token-1", "symbol": "AG"}}
            )
            return {"success": True}
        if phase_name == "upload_branding":
            return {"success": True, "skipped": True}
        if phase_name == "apply_identity":
            realm_obj.manifesto = "Our realm."
            realm_obj.welcome_message = "Welcome!"
            return {"success": True}
        if phase_name == "complete":
            realm_obj.status = RealmStatus.ALPHA
            return {"success": True}
        return {"success": False, "error": "unknown"}

    monkeypatch.setattr(setup_api, "run_setup_launch_phase", _fake_run)
    monkeypatch.setattr(
        "core.quarter_bootstrap.disable_recurring_task", lambda _name: None
    )

    for _ in range(len(setup_core.SETUP_LAUNCH_PHASES)):
        result = _run_async(setup_core.advance_setup_launch())
        assert result["success"] is True

    assert order == [name for name, _ in setup_core.SETUP_LAUNCH_PHASES]
    assert realm.status == RealmStatus.ALPHA
    launch = json.loads(realm.manifest_data)["setup"]["launch"]
    assert launch["status"] == "completed"


def test_setup_launch_failure_records_error_and_resume(monkeypatch):
    setup_api = _import_setup_api()
    realm = _FakeRealm(
        status=RealmStatus.SETUP,
        manifest_data=json.dumps(
            {
                "setup": {
                    "creator_principal": "creator-1",
                    "draft": {"codex": {"package": "agora", "version": "1.0.0"}},
                    "launch": {
                        "status": "running",
                        "phase": None,
                        "steps": [
                            {"name": "install_codex", "status": "pending", "error": None},
                            {
                                "name": "configure_token",
                                "status": "pending",
                                "error": None,
                            },
                            {
                                "name": "upload_branding",
                                "status": "pending",
                                "error": None,
                            },
                            {"name": "apply_identity", "status": "pending", "error": None},
                            {"name": "complete", "status": "pending", "error": None},
                        ],
                        "updated_at": "1",
                    },
                }
            }
        ),
    )
    _authorized_creator(realm)

    calls = {"n": 0}

    def _flaky_run(realm_obj, phase_name):
        if phase_name == "configure_token":
            calls["n"] += 1
            if calls["n"] == 1:
                return {"success": False, "error": "token registry unavailable"}
        if phase_name == "install_codex":
            setup_core.update_setup_config(
                realm_obj, {"codex": {"package": "agora", "version": "1.0.0"}}
            )
            return {"success": True}
        if phase_name == "configure_token":
            return {"success": True, "skipped": True}
        if phase_name == "upload_branding":
            return {"success": True, "skipped": True}
        if phase_name == "apply_identity":
            return {"success": True, "skipped": True}
        if phase_name == "complete":
            realm_obj.status = RealmStatus.ALPHA
            return {"success": True}
        return {"success": False, "error": "unexpected"}

    monkeypatch.setattr(setup_api, "run_setup_launch_phase", _flaky_run)
    monkeypatch.setattr(
        "core.quarter_bootstrap.disable_recurring_task", lambda _name: None
    )
    seeded = []

    def _fake_seed(name, code, interval):
        seeded.append(name)

    monkeypatch.setattr("core.quarter_bootstrap.seed_recurring_codex_task", _fake_seed)

    first = _run_async(setup_core.advance_setup_launch())
    assert first["success"] is True
    second = _run_async(setup_core.advance_setup_launch())
    assert second["success"] is False
    launch = json.loads(realm.manifest_data)["setup"]["launch"]
    assert launch["status"] == "failed"
    token_step = next(s for s in launch["steps"] if s["name"] == "configure_token")
    assert token_step["status"] == "failed"
    assert "token registry" in token_step["error"]

    relaunch = _call_setup_launch(setup_api)
    assert relaunch["success"] is True
    assert seeded == [setup_core.SETUP_LAUNCH_TASK_NAME]
    relaunch_launch = relaunch["launch"]
    assert relaunch_launch["status"] in ("running", "completed")
    token_step = next(
        s for s in relaunch_launch["steps"] if s["name"] == "configure_token"
    )
    assert token_step["status"] in ("pending", "completed")
    assert token_step["error"] is None

    third = _run_async(setup_core.advance_setup_launch())
    assert third["success"] is True
    for _ in range(10):
        tick = _run_async(setup_core.advance_setup_launch())
        if tick.get("status") == "completed":
            break
    assert realm.status == RealmStatus.ALPHA


def test_list_available_codices_includes_repository(monkeypatch):
    setup_api = _import_setup_api()
    realm = _FakeRealm(file_registry_canister_id="file-reg-id")
    _FakeRealm.reset(realm)

    class _Registry:
        def list_codices(self):
            return json.dumps(
                [
                    {
                        "codex_id": "agora",
                        "versions": ["1.0.0"],
                        "latest": "1.0.0",
                    }
                ]
            )

        def get_extension_manifest(self, args_json):
            return json.dumps(
                {
                    "name": "Agora",
                    "description": "Governance codex",
                    "repository": "https://github.com/example/agora",
                }
            )

    monkeypatch_registry = _Registry()

    def _service(_principal):
        return monkeypatch_registry

    monkeypatch.setattr(setup_api, "FileRegistryService", _service)
    monkeypatch.setattr(
        setup_api,
        "Principal",
        type("Principal", (), {"from_str": staticmethod(lambda value: value)}),
    )

    gen = setup_api.list_available_codices()
    payload = json.loads(_run_async(gen))
    assert payload["success"] is True
    assert payload["codices"][0]["description"] == "Governance codex"
    assert payload["codices"][0]["repository"] == "https://github.com/example/agora"


def test_list_available_codices_hides_shared_packages(monkeypatch):
    setup_api = _import_setup_api()
    realm = _FakeRealm(file_registry_canister_id="file-reg-id")
    _FakeRealm.reset(realm)

    class _Registry:
        def list_codices(self):
            return json.dumps(
                [
                    {"codex_id": "agora", "versions": ["1.0.0"], "latest": "1.0.0"},
                    {"codex_id": "common", "versions": ["0.0.0"], "latest": "0.0.0"},
                    {"codex_id": "westminster", "versions": ["0.0.0"], "latest": "0.0.0"},
                ]
            )

        def get_extension_manifest(self, args_json):
            params = json.loads(args_json)
            return json.dumps({"name": params.get("ext_id"), "description": "", "repository": ""})

    monkeypatch.setattr(setup_api, "FileRegistryService", lambda _principal: _Registry())
    monkeypatch.setattr(
        setup_api,
        "Principal",
        type("Principal", (), {"from_str": staticmethod(lambda value: value)}),
    )

    payload = json.loads(_run_async(setup_api.list_available_codices()))
    assert payload["success"] is True
    assert [item["id"] for item in payload["codices"]] == ["agora"]


# ---------------------------------------------------------------------------
# Data URL decoding (must not rely on `re`, which is a stub in the canister)
# ---------------------------------------------------------------------------


def test_decode_data_url_accepts_base64_payload():
    import base64 as _b64

    setup_api = _import_setup_api()
    raw = b"\x89PNG\r\n\x1a\n binary \x00\xff"
    payload, content_type = setup_api._decode_data_url(
        "  data:image/png;base64," + _b64.b64encode(raw).decode() + "  "
    )
    assert payload == raw
    assert content_type == "image/png"


@pytest.mark.parametrize(
    "value",
    [
        "",
        None,
        "data:image/png;base64,",
        "data:image/png,QUJD",
        "data:;base64,QUJD",
        "data:image/png;charset=utf-8;base64,QUJD",
        "notadata:image/png;base64,QUJD",
    ],
)
def test_decode_data_url_rejects_malformed(value):
    setup_api = _import_setup_api()
    with pytest.raises(ValueError):
        setup_api._decode_data_url(value)


def test_setup_api_does_not_import_re_at_module_level():
    """The canister's WASI CPython stubs out `re`, so a module-level
    `re.compile` would break every `api.setup` import at runtime."""
    import pathlib

    setup_api = _import_setup_api()
    source = pathlib.Path(setup_api.__file__).read_text()
    code = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )
    assert "\nimport re\n" not in code
    assert "re.compile(" not in code
