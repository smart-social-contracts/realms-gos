"""Unit tests for api/developers.py: display names and listing transfer."""

import json

from marketplace_backend.api import codices as cx_api
from marketplace_backend.api import developers as dev_api
from marketplace_backend.api import extensions as ext_api
from marketplace_backend.api import licenses as lic_api
from marketplace_backend.core.models import ExtensionListingEntity

from .test_assistants import _create as create_assistant
from .test_codices import _create as create_codex
from .test_extensions import _create as create_extension


# ── display names ────────────────────────────────────────────────────────────


def test_setting_a_name_is_controller_only(as_caller):
    as_caller("dev-1", controller=False)
    r = dev_api.set_developer_name_from_json('{"principal": "dev-1", "name": "Me"}')
    assert r["success"] is False and "controller" in r["error"]
    assert dev_api.developer_name("dev-1") == ""


def test_name_shows_on_every_listing_kind_and_in_status(as_caller):
    create_extension()
    create_codex()
    create_assistant()
    as_caller("conductor", controller=True)
    r = dev_api.set_developer_name_from_json(json.dumps({"principal": "dev-1", "name": "Realms GOS team"}))
    assert r == {"success": True, "action": "created"}

    assert ext_api.get_extension_details("voting")["extension"]["developer_name"] == "Realms GOS team"
    assert cx_api.get_codex_details("syntropia/membership")["codex"]["developer_name"] == "Realms GOS team"
    assert [a["developer_name"] for a in dev_api_assistants("dev-1")] == ["Realms GOS team"]
    assert lic_api.publishing_status()["names"] == [{"principal": "dev-1", "name": "Realms GOS team"}]


def dev_api_assistants(developer):
    from marketplace_backend.api import assistants as a_api
    return a_api.get_developer_assistants(developer)


def test_unnamed_developer_has_an_empty_name():
    create_extension()
    assert ext_api.get_extension_details("voting")["extension"]["developer_name"] == ""


def test_name_can_be_renamed_and_removed(as_caller):
    as_caller("conductor", controller=True)
    dev_api.set_developer_name("dev-1", "Old")
    assert dev_api.set_developer_name("dev-1", "New")["action"] == "updated"
    assert dev_api.developer_names() == {"dev-1": "New"}
    assert dev_api.set_developer_name("dev-1", "")["action"] == "removed"
    assert dev_api.developer_names() == {}


def test_name_length_and_json_are_checked(as_caller):
    as_caller("conductor", controller=True)
    assert dev_api.set_developer_name("dev-1", "x" * 65)["success"] is False
    assert dev_api.set_developer_name_from_json("not json")["success"] is False
    assert dev_api.set_developer_name_from_json('{"name": "No principal"}')["success"] is False


# ── transfer ─────────────────────────────────────────────────────────────────


def test_owner_transfers_and_review_state_is_kept(as_caller):
    create_extension()
    ExtensionListingEntity["voting"].verification_status = "verified"
    as_caller("dev-1")
    r = dev_api.transfer_listing("dev-1", "ext", "voting", "dev-2")
    assert r["success"] is True and r["action"] == "transferred" and r["previous"] == "dev-1"
    e = ext_api.get_extension_details("voting")["extension"]
    assert e["developer"] == "dev-2"
    assert e["verification_status"] == "verified"
    assert e["version"] == "0.1.0"
    assert [x["extension_id"] for x in ext_api.get_developer_extensions("dev-2")] == ["voting"]
    assert ext_api.get_developer_extensions("dev-1") == []


def test_controller_transfers_someone_elses_codex(as_caller):
    create_codex()
    as_caller("conductor", controller=True)
    r = dev_api.transfer_listing("conductor", "codex", "syntropia/membership", "dev-2")
    assert r["success"] is True
    assert cx_api.get_codex_details("syntropia/membership")["codex"]["developer"] == "dev-2"


def test_assistant_can_be_transferred(as_caller):
    create_assistant()
    as_caller("dev-1")
    assert dev_api.transfer_listing("dev-1", "assistant", "smart-social-contracts/ashoka", "dev-2")["success"] is True
    assert [a["assistant_id"] for a in dev_api_assistants("dev-2")] == ["smart-social-contracts/ashoka"]


def test_stranger_cannot_transfer(as_caller):
    create_extension()
    as_caller("dev-3")
    r = dev_api.transfer_listing("dev-3", "ext", "voting", "dev-3")
    assert r == {"success": False, "error": "Not the owner"}
    assert ext_api.get_extension_details("voting")["extension"]["developer"] == "dev-1"


def test_transfer_input_is_checked(as_caller):
    create_extension()
    as_caller("dev-1")
    assert dev_api.transfer_listing("dev-1", "theme", "voting", "dev-2")["success"] is False
    assert dev_api.transfer_listing("dev-1", "ext", "missing", "dev-2")["success"] is False
    assert dev_api.transfer_listing("dev-1", "ext", "voting", " ")["success"] is False
    assert dev_api.transfer_listing("dev-1", "ext", "voting", "dev-1")["action"] == "unchanged"
