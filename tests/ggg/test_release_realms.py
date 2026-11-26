"""The upgrade of a realm a release built (v0.1.1 to v0.8.0-rc3) is refused before any of its rows is read, and the
upgrade of a realm this backend built never is (``api.setup.refuse_release_realm``, docs/deployment/README.md, "Realms
of the releases up to v0.8.0-rc3"). The upgrades themselves, from a release's own wasm and from an earlier build of
this backend, are tests/deployed/test_upgrade_chain.py.
"""

import json

import pytest
from ic_python_db import Database

import fake_chain
import world
from api import setup
from ggg import Realm

# Realm@1 as v0.7.0 stores it at install, read from its own wasm on PocketIC (``__browse__``, map ``storage``): no
# "id" (its alias is the name), the JSON fields as text, version 11.
RELEASE_ROW = json.loads(
    '{"timestamp_created": "None", "timestamp_updated": "None", "creator": "system", "updater": "system", '
    '"owner": "system", "_type": "Realm", "_id": "1", "name": "Default Realm", '
    '"manifesto": "A realm for digital governance and coordination", "welcome_message": "", "status": "setup", '
    '"manifest_data": "{}", "accounting_currency": "", "accounting_currency_decimals": 8, "principal_id": "", '
    '"frontend_canister_id": null, "token_canister_id": null, "nft_canister_id": null, '
    '"file_registry_canister_id": null, "marketplace_canister_id": null, "shared_tokens_json": "{}", '
    '"is_quarter": false, "is_capital": false, "federation_realm_id": null, "auto_scale_enabled": true, '
    '"scale_in_flight": false, "scale_requested_at": "", "bootstrap_state": "", "sync_state": "", '
    '"codex_install_state": "", "installer_canister_id": "", "trusted_principals": "", '
    '"require_marketplace_approval": true, "trusted_approvers": "", "open_registration": false, '
    '"quarter_join_mode": "auto", "ai_assistant_enabled": true, "logo_url": "", "background_image_url": "", '
    '"installed_version": "", "network": "", "can_test_mode": false, "test_mode": false, '
    '"test_mode_ii_bypass": false, "test_mode_user_self_registration": false, "test_mode_demo_data": false, '
    '"test_mode_skip_terms": false, "test_mode_skip_passport_zkproof": false, '
    '"test_mode_disable_monetary_tokens": false, "test_mode_demo_notice": false, "demo_notice_body": "", '
    '"__version__": 11}')


def raw_realm():
    return Database.get_instance().load("Realm", "1")


def test_the_realm_of_a_release_is_refused_and_none_of_its_rows_is_read():
    Database.get_instance().save("Realm", "1", RELEASE_ROW)
    with pytest.raises(fake_chain.Trap) as refused:
        setup.refuse_release_realm()
    assert str(refused.value).startswith(
        "Upgrade refused, and nothing was changed: the realm keeps the release it runs and all of its data.")
    assert "v0.8.0-rc3" in str(refused.value)
    assert raw_realm() == RELEASE_ROW  # not loaded through Realm, so no migrate() rewrote it


def test_a_realm_this_backend_built_is_not_refused():
    world.build()
    assert raw_realm()["id"] == "1"
    setup.refuse_release_realm()


def test_a_realm_row_holding_nothing_but_defaults_carries_its_id():
    """The ``id`` is stored even at its default, which is what keeps every realm of this backend from being refused."""
    Realm(id="1")
    assert raw_realm()["id"] == "1"
    setup.refuse_release_realm()


def test_a_canister_whose_rows_this_backend_cannot_see_is_refused():
    """The releases before v0.3.5 keep their rows where this backend finds none of them: no Realm row at all, which
    no realm of this backend lacks after its install. Started over, it would be a new realm on top of theirs."""
    with pytest.raises(fake_chain.Trap, match="Upgrade refused, and nothing was changed"):
        setup.refuse_release_realm()
    assert raw_realm() is None
