"""The admin role: who runs the marketplace from its UI.

  1. Only controllers decide who is an admin.
  2. Admins manage every listing and every user (license, suspension, name,
     reviewer role), but not canister settings.
  3. A suspended principal cannot publish or request audits.
"""

from marketplace_backend.api import admin as admin_api
from marketplace_backend.api import codices as cx_api
from marketplace_backend.api import config as config_api
from marketplace_backend.api import developers as dev_api
from marketplace_backend.api import extensions as ext_api
from marketplace_backend.api import licenses as lic_api
from marketplace_backend.api import verification as ver_api

from .conftest import grant_license
from .test_assistants import _create as create_assistant
from .test_codices import _create as create_codex
from .test_extensions import _create as create_extension

ADMIN = "admin-1"
DEV = "dev-1"
OUTSIDER = "outsider"


def make_admin(as_caller, principal=ADMIN):
    as_caller("controller", controller=True)
    assert config_api.add_admin(principal)["success"]
    as_caller(principal)


# ── appointing admins ────────────────────────────────────────────────────────


def test_only_controllers_change_the_admin_list(as_caller):
    as_caller(OUTSIDER)
    assert "controller-only" in config_api.add_admin(OUTSIDER)["error"]
    make_admin(as_caller)
    r = config_api.add_admin("someone-else")
    assert r["success"] is False and "controller-only" in r["error"]
    r = config_api.remove_admin(ADMIN)
    assert r["success"] is False
    assert config_api.get_admins() == [ADMIN]


def test_add_is_idempotent_and_remove_revokes(as_caller):
    make_admin(as_caller)
    make_admin(as_caller)
    assert config_api.get_admins() == [ADMIN]
    assert config_api.is_admin(ADMIN) is True

    as_caller("controller", controller=True)
    assert config_api.remove_admin(ADMIN)["admins"] == []
    as_caller(ADMIN)
    assert config_api.is_admin(ADMIN) is False


def test_controllers_are_admins_and_admins_are_reviewers(as_caller):
    as_caller("controller", controller=True)
    assert admin_api.my_roles() == {"is_controller": True, "is_admin": True, "is_reviewer": True}
    make_admin(as_caller)
    assert admin_api.my_roles() == {"is_controller": False, "is_admin": True, "is_reviewer": True}
    assert config_api.is_reviewer(ADMIN) is True
    as_caller(OUTSIDER)
    assert admin_api.my_roles() == {"is_controller": False, "is_admin": False, "is_reviewer": False}


def test_admins_do_not_get_canister_settings(as_caller):
    make_admin(as_caller)
    assert config_api.set_file_registry_canister_id("x")["success"] is False
    assert config_api.set_billing_service_principal("x")["success"] is False
    assert config_api.set_license_pricing(1, 1)["success"] is False


# ── listings ─────────────────────────────────────────────────────────────────


def test_admin_sees_every_listing_including_delisted(as_caller):
    create_extension()
    create_codex(developer="dev-2")
    create_assistant()
    as_caller(DEV)
    ext_api.delist_extension(DEV, "voting")

    as_caller(OUTSIDER)
    assert admin_api.list_all_listings() == []

    make_admin(as_caller)
    rows = {(r["item_kind"], r["item_id"]): r for r in admin_api.list_all_listings()}
    assert set(rows) == {
        ("ext", "voting"),
        ("codex", "syntropia/membership"),
        ("assistant", "smart-social-contracts/ashoka"),
    }
    assert rows[("ext", "voting")]["is_active"] is False
    assert rows[("codex", "syntropia/membership")]["developer"] == "dev-2"


def test_admin_delists_and_relists_anyones_listing(as_caller):
    create_extension()
    as_caller(OUTSIDER)
    assert admin_api.set_listing_active("ext", "voting", False)["success"] is False

    make_admin(as_caller)
    assert admin_api.set_listing_active("ext", "voting", False)["action"] == "delisted"
    assert ext_api.get_extension_details("voting")["success"] is False
    assert admin_api.set_listing_active("ext", "voting", True)["action"] == "relisted"
    assert ext_api.get_extension_details("voting")["success"] is True
    assert admin_api.set_listing_active("ext", "nope", True)["success"] is False
    assert admin_api.set_listing_active("bogus", "voting", True)["success"] is False


def test_admin_can_use_the_owner_delist_and_transfer(as_caller):
    create_codex()
    make_admin(as_caller)
    assert cx_api.delist_codex(ADMIN, "syntropia/membership")["success"] is True
    r = dev_api.transfer_listing(ADMIN, "codex", "syntropia/membership", "dev-2")
    assert r["action"] == "transferred"


def test_admin_reviews_without_being_appointed_reviewer(as_caller):
    create_extension()
    make_admin(as_caller)
    r = ver_api.set_verification_status(item_kind="ext", item_id="voting", status="rejected", notes="no")
    assert r["success"] is True
    assert [p["item_id"] for p in ver_api.list_pending_audits()] == []


# ── users ────────────────────────────────────────────────────────────────────


def test_admin_grants_and_revokes_licenses(as_caller):
    make_admin(as_caller)
    assert lic_api.grant_manual_license(principal=DEV, duration_seconds=3600, note="comp")["success"]
    assert lic_api.has_active_license(DEV) is True
    assert lic_api.revoke_license(DEV)["success"] is True
    assert lic_api.has_active_license(DEV) is False

    as_caller(OUTSIDER)
    assert lic_api.grant_manual_license(principal=OUTSIDER, duration_seconds=3600)["success"] is False


def test_admin_manages_reviewers(as_caller):
    make_admin(as_caller)
    assert config_api.add_reviewer("rev-1")["reviewers"] == ["rev-1"]
    assert config_api.remove_reviewer("rev-1")["reviewers"] == []


def test_user_list_gathers_everyone_with_their_state(as_caller):
    create_extension()
    create_extension(extension_id="second")
    create_codex(developer="dev-2")
    make_admin(as_caller)
    config_api.add_reviewer("rev-1")
    dev_api.set_developer_name(DEV, "Dev One")
    dev_api.set_user_suspended("dev-2", True, "spam")

    users = {u["principal"]: u for u in admin_api.list_users()}
    assert set(users) == {ADMIN, DEV, "dev-2", "rev-1"}
    assert users[ADMIN]["is_admin"] is True
    assert users["rev-1"]["is_reviewer"] is True and users["rev-1"]["has_license"] is False
    assert users[DEV]["display_name"] == "Dev One"
    assert users[DEV]["extensions_count"] == 2 and users[DEV]["license_active"] is True
    assert users["dev-2"]["is_suspended"] is True and users["dev-2"]["suspended_reason"] == "spam"
    assert users["dev-2"]["codices_count"] == 1

    as_caller(OUTSIDER)
    assert admin_api.list_users() == []


# ── suspension ───────────────────────────────────────────────────────────────


def test_suspension_is_admin_only(as_caller):
    as_caller(OUTSIDER)
    r = dev_api.set_user_suspended(DEV, True, "")
    assert r["success"] is False and "admins only" in r["error"]
    assert dev_api.is_suspended(DEV) is False


def test_suspended_developer_cannot_publish_or_request_audit(as_caller):
    assert create_extension()["success"] is True
    make_admin(as_caller)
    assert dev_api.set_user_suspended(DEV, True, "abuse")["action"] == "suspended"

    as_caller(DEV)
    r = create_extension(version="0.2.0")
    assert r["success"] is False and "suspended" in r["error"]
    assert create_codex()["success"] is False
    assert create_assistant()["success"] is False
    r = ver_api.request_audit(caller=DEV, item_kind="ext", item_id="voting")
    assert r["success"] is False and "suspended" in r["error"]

    as_caller(ADMIN)
    assert dev_api.set_user_suspended(DEV, False, "")["action"] == "reinstated"
    as_caller(DEV)
    assert create_extension(version="0.2.0")["success"] is True


def test_admins_cannot_be_suspended(as_caller):
    make_admin(as_caller, "admin-2")
    make_admin(as_caller)
    r = dev_api.set_user_suspended("admin-2", True, "")
    assert r["success"] is False and "controller" in r["error"]


def test_suspension_survives_removing_the_display_name(as_caller):
    make_admin(as_caller)
    dev_api.set_developer_name(DEV, "Dev One")
    dev_api.set_user_suspended(DEV, True, "x")
    dev_api.set_developer_name(DEV, "")
    assert dev_api.developer_name(DEV) == ""
    assert dev_api.is_suspended(DEV) is True


def test_reinstating_an_unknown_principal_is_a_no_op(as_caller):
    make_admin(as_caller)
    assert dev_api.set_user_suspended("nobody", False, "")["action"] == "unchanged"
    grant_license("nobody")
    assert dev_api.is_suspended("nobody") is False
