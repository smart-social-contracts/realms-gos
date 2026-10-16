"""Browser uploads: the marketplace grants a developer one registry namespace.

  1. Only a signed-in, licensed, non-suspended developer gets a grant, and
     only for an item they own (or a new one).
  2. The grant is exactly ``<kind>/<id>/<version>``, asked of the registry.
  3. A registry refusal is passed on, not reported as success.
"""

import json

import pytest

from marketplace_backend.api import config as config_api
from marketplace_backend.api import developers as dev_api
from marketplace_backend.api import uploads as uploads_api

from .conftest import grant_license
from .test_admins import make_admin
from .test_extensions import _create as create_extension

REGISTRY = "registry-cai"
DEV = "dev-1"


class FakeRegistry:
    def __init__(self, response='{"ok": true}'):
        self.response = response
        self.calls = []

    def grant_publish(self, args):
        self.calls.append(json.loads(args))
        return self.response


@pytest.fixture
def registry(as_caller, monkeypatch):
    as_caller("controller", controller=True)
    config_api.set_file_registry_canister_id(REGISTRY)
    fake = FakeRegistry()
    monkeypatch.setattr(uploads_api, "FileRegistryGrantService", lambda principal: fake)
    monkeypatch.setattr(uploads_api.Principal, "from_str", staticmethod(lambda v: v), raising=False)
    return fake


def prepare(registry, **kwargs):
    generator = uploads_api.prepare_upload(**kwargs)
    to_send = None
    while True:
        try:
            generator.send(to_send)
        except StopIteration as stop:
            return stop.value
        to_send = registry.response


def test_licensed_developer_gets_exactly_the_version_namespace(as_caller, registry):
    grant_license(DEV)
    as_caller(DEV)
    r = prepare(registry, caller=DEV, item_kind="ext", item_id="voting", version="0.2.0")
    assert r == {"success": True, "namespace": "ext/voting/0.2.0", "file_registry_canister_id": REGISTRY}
    assert registry.calls == [{"namespace": "ext/voting/0.2.0", "principal": DEV}]


def test_two_segment_ids_for_codices_and_assistants(as_caller, registry):
    grant_license(DEV)
    as_caller(DEV)
    r = prepare(registry, caller=DEV, item_kind="codex", item_id="syntropia/membership", version="1.0.0")
    assert r["namespace"] == "codex/syntropia/membership/1.0.0"
    r = prepare(registry, caller=DEV, item_kind="assistant", item_id="acme/helper", version="1.0.0-rc.1")
    assert r["namespace"] == "assistant/acme/helper/1.0.0-rc.1"


@pytest.mark.parametrize("caller", ["", "2vxsx-fae"])
def test_anonymous_callers_are_refused(as_caller, registry, caller):
    as_caller(caller)
    r = prepare(registry, caller=caller, item_kind="ext", item_id="voting", version="0.1.0")
    assert "Sign in" in r["error"]
    assert registry.calls == []


def test_a_license_is_required(as_caller, registry):
    as_caller(DEV)
    r = prepare(registry, caller=DEV, item_kind="assistant", item_id="helper", version="0.1.0")
    assert "license" in r["error"]
    assert registry.calls == []


def test_suspended_developers_are_refused(as_caller, registry):
    grant_license(DEV)
    make_admin(as_caller)
    assert dev_api.set_user_suspended(DEV, True, "spam")["success"]
    as_caller(DEV)
    r = prepare(registry, caller=DEV, item_kind="ext", item_id="voting", version="0.1.0")
    assert "suspended" in r["error"]
    assert registry.calls == []


def test_someone_elses_listing_is_refused(as_caller, registry):
    grant_license("owner")
    as_caller("owner")
    assert create_extension(developer="owner")["success"]
    grant_license(DEV)
    as_caller(DEV)
    r = prepare(registry, caller=DEV, item_kind="ext", item_id="voting", version="9.9.9")
    assert "another developer" in r["error"]
    assert registry.calls == []
    as_caller("owner")
    assert prepare(registry, caller="owner", item_kind="ext", item_id="voting", version="0.2.0")["success"]


@pytest.mark.parametrize("kind,item_id,version", [
    ("ext", "a/b", "0.1.0"),
    ("codex", "a/b/c", "0.1.0"),
    ("ext", "../x", "0.1.0"),
    ("ext", "voting", "main"),
    ("ext", "voting", ""),
    ("other", "voting", "0.1.0"),
])
def test_malformed_items_are_refused(as_caller, registry, kind, item_id, version):
    grant_license(DEV)
    as_caller(DEV)
    r = prepare(registry, caller=DEV, item_kind=kind, item_id=item_id, version=version)
    assert r["success"] is False
    assert registry.calls == []


def test_a_registry_refusal_is_reported(as_caller, registry):
    registry.response = '{"error": "Unauthorized: \'ext/voting\' is already published by someone else"}'
    grant_license(DEV)
    as_caller(DEV)
    r = prepare(registry, caller=DEV, item_kind="ext", item_id="voting", version="0.1.0")
    assert r["success"] is False
    assert "someone else" in r["error"]


def test_check_upload_has_no_side_effects(as_caller, registry):
    grant_license(DEV)
    as_caller(DEV)
    r = uploads_api.check_upload(caller=DEV, item_kind="ext", item_id="voting", version="0.1.0")
    assert r["success"] and r["namespace"] == "ext/voting/0.1.0"
    assert registry.calls == []
