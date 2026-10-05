"""Shared pytest fixtures for marketplace_backend unit tests.

These tests run in plain CPython against the real ``ic_python_db`` and
``basilisk`` packages, but without an Internet Computer runtime. We
mock ``ic.caller``, ``ic.time``, and ``ic.is_controller`` so each test
can simulate an arbitrary principal.

Imports note: the marketplace canister's own ``main.py`` does
``from core.models import …`` because basilisk puts the canister dir
on sys.path at runtime. In tests we must NOT add
``src/marketplace_backend`` to sys.path (that would collide with
``src/realm_registry_backend``'s identically-named ``core`` /
``api`` subpackages and break the registry tests). Instead we import
the api modules through their full dotted path
(``marketplace_backend.api.foo``) and re-expose them under the short
names the canister code uses.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Make src importable so `marketplace_backend.api.foo` resolves.
ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


# Mock the ic module BEFORE importing any marketplace api module.
import basilisk  # noqa: E402

mock_ic = MagicMock()
mock_ic.time.return_value = int(time.time() * 1_000_000_000)
mock_ic.caller.return_value = "anon-principal"
mock_ic.is_controller.return_value = False
basilisk.ic = mock_ic

# The marketplace_backend's _cdk.py does ``from basilisk import ic``;
# but tests import its api modules via marketplace_backend.api.* and the
# api modules do ``from _cdk import ic``. _cdk lives at
# src/marketplace_backend/_cdk.py and isn't reachable via that name from
# tests because src/marketplace_backend is not on sys.path. Wire up the
# import name manually.
import importlib  # noqa: E402
import importlib.util  # noqa: E402


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


_MP = SRC / "marketplace_backend"
# Load _cdk (with our mocked basilisk). Stash under a private name and
# install the short `_cdk` alias only long enough for the api modules
# to import — then pop it so other test suites that load their own
# canister's _cdk aren't affected.
_load_module("_cdk_marketplace", _MP / "_cdk.py")
sys.modules["_cdk"] = sys.modules["_cdk_marketplace"]


# The marketplace api modules use absolute imports of the form
# ``from core.models import …`` and ``from api.foo import …`` because
# basilisk runs the canister with the canister directory on sys.path.
# To load those modules in tests we temporarily alias the short names
# to the fully-qualified marketplace_backend.* packages, load all api
# modules so their cached references are bound, then *remove* the
# aliases so other test suites (e.g. realm_registry) that use the same
# names with their own packages aren't polluted.

_short_aliases = ["core", "core.models", "api"]
_api_short_modules = [
    "api.config", "api.developers", "api.extensions", "api.codices", "api.assistants",
    "api.likes", "api.rankings", "api.licenses", "api.verification",
    "api.status", "api.approval",
]

# 1. Install short-name aliases pointing at marketplace_backend.*.
sys.modules["core"] = importlib.import_module("marketplace_backend.core")
sys.modules["core.models"] = importlib.import_module("marketplace_backend.core.models")
sys.modules["api"] = importlib.import_module("marketplace_backend.api")
for short in _api_short_modules:
    full = "marketplace_backend." + short
    sys.modules[short] = importlib.import_module(full)

# Re-import them under the full path so test files have a stable handle.
ext_api = sys.modules["api.extensions"]
codices_api = sys.modules["api.codices"]
likes_api = sys.modules["api.likes"]
rankings_api = sys.modules["api.rankings"]
licenses_api = sys.modules["api.licenses"]
config_api = sys.modules["api.config"]
verification_api = sys.modules["api.verification"]
approval_api = sys.modules["api.approval"]
developers_api = sys.modules["api.developers"]

# 2. Drop the short-name aliases from sys.modules so other test
#    suites that import a different package's `core`, `api`, or `_cdk`
#    don't accidentally pick up ours. The api modules already hold
#    direct references to the imported symbols, so removal is safe for
#    them.
for short in _short_aliases + _api_short_modules + ["_cdk"]:
    sys.modules.pop(short, None)


from ic_python_db import Database  # noqa: E402

# We defer Database.init() until the first marketplace test is actually
# run — doing it at module load time would race with sibling test suites
# (e.g. realm_registry) that also call Database.init() at module load.
_db_initialised = False


class MockStorage:
    def __init__(self) -> None:
        self.data: dict = {}

    def get(self, key):
        return self.data.get(key)

    def insert(self, key, value):
        self.data[key] = value

    def remove(self, key):
        self.data.pop(key, None)

    def items(self):
        return list(self.data.items())

    def keys(self):
        return list(self.data.keys())

    def values(self):
        return list(self.data.values())

    def __contains__(self, key):
        return key in self.data

    def __len__(self):
        return len(self.data)


_storage = MockStorage()


def _ensure_db():
    global _db_initialised
    if _db_initialised:
        return
    try:
        Database.init(db_storage=_storage, audit_enabled=False)
    except RuntimeError as e:
        if "already exists" not in str(e):
            raise
    _db_initialised = True


@pytest.fixture(autouse=True)
def _reset_state():
    _ensure_db()
    """Wipe all marketplace entities before each test."""
    from marketplace_backend.core.models import (
        AssistantListingEntity,
        CodexListingEntity,
        DeveloperLicenseEntity,
        DeveloperProfileEntity,
        ExtensionListingEntity,
        LikeEntity,
        MarketplaceConfigEntity,
        PurchaseEntity,
    )
    for cls in (
        ExtensionListingEntity,
        CodexListingEntity,
        AssistantListingEntity,
        PurchaseEntity,
        LikeEntity,
        DeveloperLicenseEntity,
        DeveloperProfileEntity,
        MarketplaceConfigEntity,
    ):
        try:
            for inst in list(cls.instances()):
                inst.delete()
        except Exception:
            pass
    mock_ic.time.return_value = int(time.time() * 1_000_000_000)
    mock_ic.caller.return_value = "anon-principal"
    mock_ic.is_controller.return_value = False
    yield


YEAR_SECONDS = 365 * 24 * 3600


def grant_license(principal: str, *, duration_seconds: float = YEAR_SECONDS) -> None:
    """Give ``principal`` an active developer license.

    Publishing is license-gated, so suites whose subject is something else
    (listings, likes, rankings, purchases) need one as scaffolding. The row is
    written directly rather than through ``grant_manual_license`` so the
    current caller and controller flags are left exactly as the test set them.

    Deliberately not autouse: several tests exist to show that an unlicensed
    principal is refused, and a blanket license would gut them.
    """
    from marketplace_backend.core.models import DeveloperLicenseEntity

    now_ns = float(mock_ic.time())
    expires_at = now_ns + float(duration_seconds) * 1_000_000_000
    lic = DeveloperLicenseEntity[principal]
    if lic is None:
        DeveloperLicenseEntity(
            principal=principal,
            created_at=now_ns,
            expires_at=expires_at,
            last_payment_id="",
            last_payment_amount_usd_cents=0,
            payment_method="manual",
            note="test license",
            is_active=True,
        )
    else:
        lic.expires_at = expires_at
        lic.is_active = True


@pytest.fixture
def as_caller():
    def _set(principal: str, *, controller: bool = False):
        mock_ic.caller.return_value = principal
        mock_ic.is_controller.return_value = controller
    return _set


@pytest.fixture
def advance_time():
    def _set(seconds_from_now: float = 0):
        mock_ic.time.return_value = int((time.time() + seconds_from_now) * 1_000_000_000)
    return _set
