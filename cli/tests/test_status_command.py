"""`realms status`: what the realm reports, including the problems its settings cause."""

from unittest.mock import patch

from typer.testing import CliRunner

from realms.cli.main import app

NO_SOURCE_FUND = "the treasury's source fund ROOT, which receives invoice payments, does not exist"


def replies(name, *args, **kwargs):
    if name == "status":
        return {"ok": True, "status": "alpha", "version": "1.0.0"}
    return {"ok": True, "problems": [NO_SOURCE_FUND]}


@patch("subprocess.run")  # dfx ping
@patch("realms.cli.main.check_dependencies", return_value=True)
@patch("realms.cli.main.get_current_realm", return_value=None)
@patch("realms.cli.main.get_effective_network_and_canister", return_value=("local", "realm_backend"))
@patch("realms.cli.commands.db.realm_op", side_effect=lambda canister, name, *a, **kw: replies(name))
def test_status_prints_the_problems_the_realm_reports(mock_op, *_):
    out = " ".join(CliRunner().invoke(app, ["status"]).output.split())  # rich wraps long lines
    assert "Backend status: alpha" in out and NO_SOURCE_FUND in out
    assert [c.args[1] for c in mock_op.call_args_list] == ["status", "realm_monitor"]
