"""``realms realm quarter`` commands: realm operations through read/run (no replica)."""

from unittest.mock import patch

import pytest
import typer

from realms.cli.commands.quarter import (
    quarter_join_federation_command,
    quarter_list_command,
    quarter_register_command,
    quarter_remove_command,
    quarter_secede_command,
    quarter_status_command,
)

DIRECTORY = {
    "ok": True,
    "capital": "can-parent",
    "quarters": [
        {"id": "can-parent", "name": "Arcadia", "population": 7, "status": "active"},
        {"id": "can-1", "name": "North", "population": 50, "status": "active"},
        {"id": "can-2", "name": "South", "population": 30, "status": "active"},
    ],
}


@pytest.fixture
def realm():
    """Resolve every realm ref to can-parent on local; record (canister, op, args, read)."""
    calls = []
    replies = {}

    def fake(canister, name, args=None, network="local", identity=None, read=False, timeout=120):
        calls.append((canister, name, args, read))
        reply = replies.get((canister, name), {"ok": True})
        return reply(args) if callable(reply) else reply

    with patch("realms.cli.commands.quarter.call_op", side_effect=fake), patch(
        "realms.cli.commands.quarter.resolve_realm_ref_to_canister_id",
        return_value=("can-parent", "Arcadia"),
    ), patch(
        "realms.cli.commands.quarter.get_effective_network_and_canister",
        return_value=("local", "realm_backend"),
    ):
        yield calls, replies


def test_list_reads_the_directory_without_the_realm_itself(realm, capsys):
    calls, replies = realm
    replies[("can-parent", "quarter_directory")] = DIRECTORY
    quarter_list_command("Arcadia", "local")
    assert calls == [("can-parent", "quarter_directory", None, True)]
    out = capsys.readouterr().out
    assert "can-1" in out and "can-2" in out
    assert "Total: 2 quarter(s)" in out


def test_list_empty(realm, capsys):
    calls, replies = realm
    replies[("can-parent", "quarter_directory")] = {**DIRECTORY, "quarters": DIRECTORY["quarters"][:1]}
    quarter_list_command("Arcadia", "local")
    assert "No quarters registered" in capsys.readouterr().out


def test_register_joins_then_registers(realm):
    calls, _ = realm
    quarter_register_command("Arcadia", "North", "can-quarter", "local")
    assert calls == [
        ("can-quarter", "quarter_join_federation", {"capital": "can-parent"}, False),
        ("can-parent", "quarter_register", {"quarter": "can-quarter", "name": "North"}, False),
    ]


def test_register_refusal_exits(realm, capsys):
    _, replies = realm
    replies[("can-quarter", "quarter_join_federation")] = {"ok": False, "error": "this realm already belongs to a federation"}
    with pytest.raises(typer.Exit):
        quarter_register_command("Arcadia", "North", "can-quarter", "local")
    assert "already belongs to a federation" in capsys.readouterr().out


def test_remove_finds_the_quarter_and_deregisters(realm):
    calls, replies = realm
    replies[("can-parent", "quarter_directory")] = DIRECTORY
    quarter_remove_command("Arcadia", "North", "local")
    assert calls[-1] == ("can-parent", "quarter_deregister", {"quarter": "can-1"}, False)


def test_status_reads_parent_then_quarter(realm, capsys):
    calls, replies = realm
    replies[("can-parent", "quarter_directory")] = DIRECTORY
    replies[("can-1", "realm_monitor")] = {"ok": True, "version": "0.9.0", "rows": {"User": 50, "Proposal": 3}}
    replies[("can-1", "quarter_directory")] = {"ok": True, "capital": "can-parent", "quarters": []}
    quarter_status_command("Arcadia", "North", "local")
    assert [(c, n) for c, n, _, _ in calls] == [
        ("can-parent", "quarter_directory"),
        ("can-1", "realm_monitor"),
        ("can-1", "quarter_directory"),
    ]
    out = capsys.readouterr().out
    assert "0.9.0" in out
    assert "parent: can-parent" in out


def test_secede(realm):
    calls, _ = realm
    quarter_secede_command("can-q", "local")
    assert calls == [("can-q", "quarter_secede", None, False)]


def test_join_federation_registers_on_the_capital_under_the_realm_name(realm):
    calls, replies = realm
    replies[("can-q", "status")] = {"ok": True, "name": "Eastside"}
    quarter_join_federation_command("can-q", "can-cap", False, "local")
    assert calls == [
        ("can-q", "quarter_join_federation", {"capital": "can-cap"}, False),
        ("can-q", "status", None, True),
        ("can-cap", "quarter_register", {"quarter": "can-q", "name": "Eastside"}, False),
    ]


def test_join_as_capital_is_not_supported(realm):
    calls, _ = realm
    with pytest.raises(typer.Exit):
        quarter_join_federation_command("can-q", "can-cap", True, "local")
    assert calls == []


def test_governed_call_reports_the_proposal(realm, capsys):
    _, replies = realm
    replies[("can-parent", "quarter_register")] = {"ok": True, "proposal": "P-4", "requires_vote": True}
    quarter_register_command("Arcadia", "North", "can-quarter", "local")
    assert "proposal P-4" in capsys.readouterr().out
