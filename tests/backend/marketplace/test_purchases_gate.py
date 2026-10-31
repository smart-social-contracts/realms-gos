"""The purchases switch: paid listings cannot be bought until an admin turns it on.

  1. While off (the default), buying a paid listing is refused and changes nothing.
  2. Free listings can always be bought.
  3. Once on, paid listings can be bought.
  4. Only admins (and controllers) flip the switch.
"""

import pytest

from marketplace_backend.api import assistants as a_api
from marketplace_backend.api import codices as cx_api
from marketplace_backend.api import config as config_api
from marketplace_backend.api import extensions as ext_api

from .test_admins import make_admin
from .test_assistants import _create as create_assistant
from .test_codices import _create as create_codex
from .test_extensions import _create as create_extension

BUYER = "realm-1"

# (create listing, buy it, read its install count, item id)
KINDS = [
    pytest.param(
        create_extension, ext_api.buy_extension, lambda: ext_api.get_extension_details("voting")["extension"], "voting",
        id="extension",
    ),
    pytest.param(
        create_codex, cx_api.buy_codex, lambda: cx_api.get_codex_details("syntropia/membership")["codex"],
        "syntropia/membership", id="codex",
    ),
    pytest.param(
        create_assistant, a_api.buy_assistant,
        lambda: a_api.get_assistant_details("smart-social-contracts/ashoka")["assistant"],
        "smart-social-contracts/ashoka", id="assistant",
    ),
]


@pytest.mark.parametrize("create,buy,details,item_id", KINDS)
def test_paid_purchase_is_refused_by_default_and_changes_nothing(create, buy, details, item_id):
    create(price_e8s=100)

    r = buy(BUYER, item_id)

    assert r == {"success": False, "error": "Purchases are coming soon"}
    assert details()["installs"] == 0


@pytest.mark.parametrize("create,buy,details,item_id", KINDS)
def test_free_purchase_works_while_off(create, buy, details, item_id):
    create(price_e8s=0)

    assert buy(BUYER, item_id)["success"] is True
    assert details()["installs"] == 1


@pytest.mark.parametrize("create,buy,details,item_id", KINDS)
def test_paid_purchase_works_once_on(as_caller, create, buy, details, item_id):
    create(price_e8s=100)
    make_admin(as_caller)
    assert config_api.set_purchases_enabled(True)["success"]

    assert buy(BUYER, item_id)["success"] is True
    assert details()["installs"] == 1


def test_switch_is_off_until_set():
    assert config_api.purchases_enabled() is False


def test_admin_can_turn_it_on_and_off(as_caller):
    make_admin(as_caller)

    assert config_api.set_purchases_enabled(True) == {"success": True, "purchases_enabled": True}
    assert config_api.purchases_enabled() is True
    assert config_api.set_purchases_enabled(False)["success"]
    assert config_api.purchases_enabled() is False


def test_controller_can_turn_it_on(as_caller):
    as_caller("controller", controller=True)

    assert config_api.set_purchases_enabled(True)["success"]
    assert config_api.purchases_enabled() is True


def test_non_admin_cannot_flip_it(as_caller):
    as_caller("outsider")

    r = config_api.set_purchases_enabled(True)

    assert r["success"] is False and "admins only" in r["error"]
    assert config_api.purchases_enabled() is False
