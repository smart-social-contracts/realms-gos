"""Proposals that state an effective_at, on the real wasm: its timers, its task scheduler and its upgrades.

Plain Python cannot show that the schedule survives an upgrade, because basilisk keeps no timers across one and
stubs parts of the standard library inside a canister (realms-gos#504).
"""

import json
import pathlib

import mock_ledger
import pytest
from ic.candid import Types
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


@pytest.fixture
def hour_long_vote(realm, people):
    """Proposals filed in a test using this stay open for an hour, to their deadline, not just to the deciding vote."""
    ok(realm, "governance_update", people["ctl"], voting_window=HOUR)
    yield
    ok(realm, "governance_update", people["ctl"], voting_window=0)


def test_a_vote_that_ended_before_effective_at_is_accepted_though_its_timer_fires_after_it(realm, people, hour_long_vote):
    ctl, alice = people["ctl"], people["alice"]
    balance = root_balance(realm, ctl)
    # The vote ends in an hour, ten minutes before the payment is due.
    paid = file_and_pass(realm, people, "Pay Bob in time", pay(realm, people["bob"], 100), realm.now() + HOUR + 600)
    assert proposal(realm, alice, paid)["status"] == "voting"
    realm.wait(HOUR + 1200)  # one jump: the deadline timer runs ten minutes after the payment was due
    assert proposal(realm, alice, paid)["status"] == "executed" and root_balance(realm, ctl) == balance - 100


def test_a_vote_that_ends_after_effective_at_expires_when_its_timer_fires(realm, people, hour_long_vote):
    ctl, alice = people["ctl"], people["alice"]
    balance = root_balance(realm, ctl)
    late = file_and_pass(realm, people, "Pay Bob too late", pay(realm, people["bob"], 100), realm.now() + 600)
    realm.wait(HOUR + 1200)
    assert proposal(realm, alice, late)["status"] == "expired" and root_balance(realm, ctl) == balance


def test_a_vote_ends_when_everyone_has_voted_and_takes_no_more_votes(realm, people, hour_long_vote):
    """The realm has three members, the founder and Alice and Bob: two yes votes leave the hour-long vote open."""
    ctl, alice = people["ctl"], people["alice"]
    balance = root_balance(realm, ctl)
    paid = file_and_pass(realm, people, "Pay Bob when all have voted", pay(realm, people["bob"], 50))
    assert proposal(realm, alice, paid)["status"] == "voting" and root_balance(realm, ctl) == balance
    last = ok(realm, "proposal_vote", people["founder"], proposal=paid, choice="no")  # the third vote ends it
    assert last["status"] == "executed" and root_balance(realm, ctl) == balance - 50
    for voter in ("alice", "founder"):  # no vote is accepted or replaced after the end
        out = realm.call("proposal_vote", {"proposal": paid, "choice": "no"}, sender=people[voter])
        assert not out["ok"] and out["kind"] == "conflict" and "not open for voting" in out["error"], out
    assert proposal(realm, alice, paid)["tally"] == {"yes": 2, "no": 1, "abstain": 0, "eligible": 3}


def test_a_late_finalize_runs_an_async_transfer_at_once(realm, people, hour_long_vote):
    """The deadline timer is lost in an upgrade and armed again one second later: until then the vote has ended but
    nothing has closed it, and ``proposal_finalize`` does, running the payment, a call to the ledger, as it replies."""
    ctl, alice = people["ctl"], people["alice"]
    balance = root_balance(realm, ctl)
    effective_at = realm.now() + HOUR + 600  # an hour of voting, then ten minutes before the payment is due
    late = file_and_pass(realm, people, "Pay Bob after a late finalize", pay(realm, people["bob"], 50), effective_at)
    assert proposal(realm, alice, late)["status"] == "voting"
    realm.upgrade()  # the timer is lost, and armed again for the deadline
    realm.pic.advance_time((HOUR + 1200) * 10**9)  # past the deadline and past effective_at, with no round run yet
    assert proposal(realm, alice, late)["status"] == "voting" and root_balance(realm, ctl) == balance
    out = ok(realm, "proposal_finalize", ctl, proposal=late)
    assert out["status"] == "executed" and out["result"]["ok"], out
    assert proposal(realm, alice, late)["result"]["status"] == "completed" and root_balance(realm, ctl) == balance - 50


GOVERNED = Types.Variant({"Done": Types.Opt(Types.Text), "Proposed": Types.Text})


def test_ggg_proposal_stop_replies_done_without_an_id_then_proposed_under_a_voting_policy(realm, people):
    """Last in the file: it gives the root department a voting policy, and nothing after it expects the old one."""
    ctl, alice = people["ctl"], people["alice"]
    scheduled = file_and_pass(realm, people, "Pay Bob, to be stopped", pay(realm, people["bob"], 50), realm.now() + HOUR)
    assert proposal(realm, alice, scheduled)["status"] == "execution_scheduled"
    assert realm.ggg("ggg_proposal_stop", Types.Text, scheduled, GOVERNED, ctl) == {"Ok": {"Done": []}}  # no id
    assert proposal(realm, alice, scheduled)["status"] == "withdrawn"
    assert "Conflict" in realm.ggg("ggg_proposal_stop", Types.Text, scheduled, GOVERNED, ctl)["Err"]

    another = file_and_pass(realm, people, "Pay Bob, to be stopped by a vote", pay(realm, people["bob"], 50),
                            realm.now() + HOUR)
    ok(realm, "department_set_policy", ctl, department="Administration", m=2, n=3)
    filed = realm.ggg("ggg_proposal_stop", Types.Text, another, GOVERNED, ctl)["Ok"]["Proposed"]
    assert proposal(realm, alice, filed)["action"] == {"op": "proposal_stop", "args": {"proposal": another}}
    assert proposal(realm, alice, another)["status"] == "execution_scheduled"  # a vote of the department stops it
