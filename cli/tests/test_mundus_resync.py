"""Mundus post-deploy: realm configuration and /ext/ bundles after a frontend deploy."""

from unittest.mock import patch

from realms.cli.commands.mundus import (
    _pin_frontend_directories,
    _post_deploy_config,
    _resync_extension_frontends_after_deploy,
    _should_resync_extension_frontends,
)


def test_should_resync_after_frontend_or_full_deploy():
    assert _should_resync_extension_frontends("") is True
    assert _should_resync_extension_frontends("frontend") is True
    assert _should_resync_extension_frontends("backend") is False


@patch("realms.cli.commands.mundus._dfx_call")
def test_pin_frontend_directories_pins_custom_and_ext(mock_call):
    _pin_frontend_directories("frontend-id", "test")
    assert mock_call.call_count == 2
    methods = [c.args[1] for c in mock_call.call_args_list]
    assert methods == ["pin_directory", "pin_directory"]
    args = [c.args[2] for c in mock_call.call_args_list]
    assert '(record { prefix = "/custom/" })' in args
    assert '(record { prefix = "/ext/" })' in args


@patch("realms.cli.commands.extension._dfx_call")
def test_resync_is_not_supported_and_calls_nothing(mock_call, capsys):
    realm = {"canister_id": "backend-id", "frontend_canister_id": "frontend-id"}
    _resync_extension_frontends_after_deploy(realm, "test", {"file_registry_canister_id": "registry-id"})
    mock_call.assert_not_called()
    assert "not supported by this realm version" in capsys.readouterr().out


@patch("realms.cli.commands.mundus._store_canister_ids")
@patch("realms.cli.commands.mundus._pin_frontend_directories")
@patch("realms.cli.commands.mundus.call_op", return_value={"ok": True})
def test_post_deploy_config_runs_configure_operations(mock_op, _pin, _store):
    realm = {"canister_id": "backend-id", "frontend_canister_id": "frontend-id"}
    _post_deploy_config(realm, "test", "1.2.3", parameters={"TEST_MODE": True, "TEST_MODE_II_BYPASS": True})
    assert [c.args[1:3] for c in mock_op.call_args_list] == [
        ("realm_configure_infrastructure", {"frontend_canister_id": "frontend-id"}),
        ("realm_configure", {"test_mode": True, "test_flags": {"ii_bypass": True}}),
    ]
    assert all(c.kwargs["network"] == "test" for c in mock_op.call_args_list)


@patch("realms.cli.commands.mundus._store_canister_ids")
@patch("realms.cli.commands.mundus._pin_frontend_directories")
@patch("realms.cli.commands.mundus.call_op", return_value={"ok": False, "error": "realm_configure needs realm.configure"})
def test_post_deploy_config_refusal_is_a_warning(_op, _pin, _store, capsys):
    realm = {"canister_id": "backend-id", "frontend_canister_id": "frontend-id"}
    _post_deploy_config(realm, "test", "1.2.3")
    assert "needs realm.configure" in capsys.readouterr().out
