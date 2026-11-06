"""Proposals that state an effective_at, on the real wasm: its timers, its task scheduler and its upgrades.

Plain Python cannot show that the schedule survives an upgrade, because basilisk keeps no timers across one and
stubs parts of the standard library inside a canister (realms-gos#504).
"""

import json
import pathlib

import mock_ledger
import pytest
from pic import Canister

CODICES = pathlib.Path(__file__).resolve().parents[2] / "codices" / "codices"
HOUR = 3600
FUNDS = 1_000  # what the mock ledger reports every invoice paid with


def ok(realm, op, who, query=False, **args):
    out = realm.call(op, args, sender=who, query=query)
    assert out["ok"], (op, out)
    return out


def codex_of(name):
    folder = CODICES / name
    return {"manifest": json.loads((folder / "manifest.json").read_text()),
            "source": (folder / "backend" / "hooks.py").read_text()}


@pytest.fixture(scope="module")
def realm(wasm_of, people):
    """A launched realm of Alice and Bob under the Dominion codex, with `FUNDS` DOM in its root fund."""
    realm = Canister(wasm_of("production"), installer=people["installer"])
    ctl, founder, alice, bob = (people[k] for k in ("ctl", "founder", "alice", "bob"))
    ok(realm, "setup_step", people["installer"], step="install", network="local", founder=founder)
    ok(realm, "setup_step", founder, step="realm", name="Agora", open_registration=False)
    ok(realm, "setup_step", founder, step="trust_policy", require_marketplace_approval=False)
    ok(realm, "setup_step", founder, step="tokens", accounting_currency="DOM", accounting_currency_decimals=8)
    ledger = mock_ledger.install(realm.pic, FUNDS)
    ok(realm, "token_register", ctl, symbol="DOM", ledger=ledger, decimals=8)
    ok(realm, "codex_install", founder, **codex_of("dominion"))
    ok(realm, "setup_launch", founder)
    code = ok(realm, "invite_create", ctl, max_uses=5)["invite"]["code"]
    for member in (alice, bob):
        ok(realm, "realm_join", member, code=code)
    # A proposal closes as soon as its outcome is certain, instead of after the voting window.
    ok(realm, "governance_update", ctl, voting_window=0)
    ok(realm, "fund_create", ctl, fund="ROOT", name="Root")  # the fund invoices are paid into
    invoice = ok(realm, "invoice_create", ctl, payer=alice, amount=FUNDS)["invoice"]
    assert ok(realm, "invoice_check", alice, invoice=invoice)["status"] == "paid"
    return realm


def pay(realm, who, amount):
    """The action of a proposal that pays Bob `amount` from the root fund."""
    return {"op": "transfer_create", "args": {"fund": "ROOT", "to": who, "amount": amount}}


def file_and_pass(realm, people, title, action, effective_at=None):
    """Alice files a proposal and she and Bob vote yes; returns its id."""
    args = {"title": title, "action": action, **({"effective_at": effective_at} if effective_at else {})}
    proposal = ok(realm, "proposal_create", people["alice"], **args)["proposal"]
    for voter in ("alice", "bob"):
        ok(realm, "proposal_vote", people[voter], proposal=proposal, choice="yes")
    return proposal


def proposal(realm, who, proposal_id):
    return ok(realm, "proposal_get", who, query=True, proposal=proposal_id)["proposal"]


def root_balance(realm, who):
    return ok(realm, "treasury_view", who, query=True)["funds"]["ROOT"].get("DOM", 0)


def active_codex(realm, who):
    return ok(realm, "codex_status", who, query=True)["codex"]["id"]


def test_a_transfer_and_a_codex_install_take_effect_at_their_time_across_an_upgrade(realm, people):
    ctl, alice, bob = people["ctl"], people["alice"], people["bob"]
    before = active_codex(realm, ctl)
    effective_at = realm.now() + HOUR
    transfer = file_and_pass(realm, people, "Pay Bob", pay(realm, bob, 400), effective_at)
    install = file_and_pass(realm, people, "Adopt the Agora codex", {"op": "codex_install", "args": codex_of("agora")},
                            effective_at)
    for scheduled in (transfer, install):
        assert proposal(realm, alice, scheduled)["status"] == "execution_scheduled"
        assert proposal(realm, alice, scheduled)["effective_at"] == effective_at
    assert root_balance(realm, ctl) == FUNDS and active_codex(realm, ctl) == before

    realm.wait(HOUR - 600)
    realm.upgrade()
    realm.wait(effective_at - 1 - realm.now())
    assert realm.now() == effective_at - 1  # one second short
    assert root_balance(realm, ctl) == FUNDS and active_codex(realm, ctl) == before
    assert proposal(realm, alice, transfer)["status"] == "execution_scheduled"
    assert proposal(realm, alice, install)["status"] == "execution_scheduled"

    realm.wait(2)
    assert realm.now() == effective_at + 1
    assert proposal(realm, alice, transfer)["status"] == "executed"
    assert proposal(realm, alice, transfer)["result"]["status"] == "completed"
    assert root_balance(realm, ctl) == FUNDS - 400
    assert proposal(realm, alice, install)["status"] == "executed"
    assert active_codex(realm, ctl).startswith("agora@")


def test_a_second_proposal_stops_a_scheduled_one(realm, people):
    ctl, alice = people["ctl"], people["alice"]
    balance = root_balance(realm, ctl)
    scheduled = file_and_pass(realm, people, "Pay Bob again", pay(realm, people["bob"], 100), realm.now() + HOUR)
    stop = file_and_pass(realm, people, "Do not pay Bob", {"op": "proposal_stop", "args": {"proposal": scheduled}})
    assert proposal(realm, alice, stop)["status"] == "executed"
    assert proposal(realm, alice, scheduled)["status"] == "withdrawn"
    realm.wait(HOUR + 60)
    assert proposal(realm, alice, scheduled)["status"] == "withdrawn" and root_balance(realm, ctl) == balance


def test_a_vote_that_ends_after_effective_at_expires_and_runs_nothing(realm, people):
    ctl, alice = people["ctl"], people["alice"]
    balance = root_balance(realm, ctl)
    effective_at = realm.now() + HOUR
    late = ok(realm, "proposal_create", alice, title="Pay Bob late", action=pay(realm, people["bob"], 100),
              effective_at=effective_at)["proposal"]
    ok(realm, "proposal_vote", alice, proposal=late, choice="yes")
    realm.wait(HOUR + 60)
    assert ok(realm, "proposal_vote", people["bob"], proposal=late, choice="yes")["status"] == "expired"
    realm.wait(HOUR)
    assert proposal(realm, alice, late)["status"] == "expired" and root_balance(realm, ctl) == balance


def test_a_proposal_without_effective_at_runs_when_the_vote_closes(realm, people):
    ctl, alice = people["ctl"], people["alice"]
    balance = root_balance(realm, ctl)
    paid = file_and_pass(realm, people, "Pay Bob now", pay(realm, people["bob"], 100))
    assert proposal(realm, alice, paid)["status"] == "executed" and root_balance(realm, ctl) == balance - 100
