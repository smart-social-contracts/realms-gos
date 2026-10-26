"""``realms self-register register`` runs the realm's realm_register operation (no replica)."""

from unittest.mock import patch

from typer.testing import CliRunner

from realms.cli.commands.self_register import app

runner = CliRunner()
ARGS = ["register", "--realm-id", "demo", "--realm-name", "Demo", "--frontend-url", "fe.icp0.io",
        "--registry-canister", "reg-id", "--realm-canister", "be-id", "--network", "staging"]


@patch("realms.cli.commands.self_register.call_op", return_value={"ok": True, "registry": "reg-id"})
def test_register_runs_realm_register(mock_op):
    result = runner.invoke(app, ARGS)
    assert result.exit_code == 0, result.output
    mock_op.assert_called_once_with(
        "be-id", "realm_register", {"registry": "reg-id", "url": "fe.icp0.io"}, network="staging"
    )
    assert "Registration Complete" in result.output


@patch("realms.cli.commands.self_register.call_op", return_value={"ok": False, "error": "the registry refused"})
def test_register_refusal_exits(_op):
    result = runner.invoke(app, ARGS)
    assert result.exit_code == 1
    assert "the registry refused" in result.output
