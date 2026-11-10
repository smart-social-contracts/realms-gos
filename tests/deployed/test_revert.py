"""The revert floor and the IT admin team on the real wasm (docs/codices/layered-codex.md §10, realms-gos#490).

A codex that forbids everything and whose guards refuse every proposal and vote is still reverted: by a vote of the
members, two codices back and with nothing downloaded, decided by the rule and window recorded with the codex restored
and not by the settings the realm has now; and by the IT admin team, under the policy of its department, to the
previous codex only. No codex changes the team, and the team is told when a codex method fails. The tests run in order on one realm.
"""

import json

import pytest
from ic.candid import Types
from pic import Canister
from test_layered_codex import codex_of, new_realm, ok
from test_legislation_runtime import DENY_ALL, TOWN, failures, run

PERCENT = 1_000_000
DAY = 86400
STAND_IN_URL = "https://legislation.example.test/eu/releases.json"
# GGG's codex_revert: an optional record that names the codex to restore, or none for the one the active codex replaced.
REVERT_ARGS = Types.Opt(Types.Record({"codex": Types.Opt(Types.Text)}))
GOVERNED = Types.Variant({"Proposed": Types.Text, "Done": Types.Opt(Types.Text)})
HARMLESS = 'from realms.legislation import above\n\n\nclass Water(above("water")):\n    """%s"""\n'
FORBID_ALL = '''
from realms.legislation import F, above, forbid

class Tax(above("tax")):
    no_proposals = forbid("proposal_create", F.title != "-")
    no_votes = forbid("proposal_vote", F.proposal != "-")
'''
BREAKS = '''
from realms.legislation import above

class Water(above("water")):
    def check_proposal_create(self, args):
        if args.title == "break the water":
            raise ValueError("no water here")
'''
CHANGES_THE_TEAM = '''
from realms.legislation import above

class Register(above("register")):
    def on_proposal_create(self, event):
        if event.args["title"] == "change the team":
            self.ggg.department_set_policy(department="IT", m=1, n=1)
        if event.args["title"] == "designate":
            self.ggg.it_team_set(department="Support")
        if event.args["title"] == "by id":
            self.ggg.department_set_policy(department=__ID__, m=1, n=1)  # the team, by the internal id of its row
'''
# The codex row as the install's switch leaves it: loaded first, then made the active one with its voting rule recorded.
SWITCH = '''
import json
from api import codex
from core import layered_codex as store, legislation
from core.op import run
from ggg import LayeredCodex
chapters, layers = %(chapters)r, %(layers)r
entries, paths = {}, {}
for source, domain, text in chapters:
    entries[f"{source}/{domain}"] = {"source": source, "sha256": store.store_chapter(store.checksum(text), text).sha256}
    paths.setdefault(domain, []).append(source)
cid = store.codex_id(layers, paths, entries)
row = LayeredCodex[cid] or LayeredCodex(id=cid, layers=layers, layer_paths=paths, chapters=entries, installed_at=1)
legislation.load(row)
codex.switch_to(row, codex.active())
run(legislation.activate(row))
print(json.dumps({"codex": cid, "rule": row.voting_rule}))
'''
BUILD_ONE = '''
import json
from core import layered_codex as store, legislation
text = %(text)r
plan = {"realm": "x/town", "layers": ["x/town"], "calendars": [],
        "chapters": [{"source": "x/town", "domain": "water", "sha256": store.checksum(text)}]}
print(json.dumps(legislation.build(plan, {store.checksum(text): text})["errors"]))
'''


def switch(realm, *chapters):
    return run(realm, SWITCH % {"chapters": list(chapters), "layers": [TOWN]})


def blocker(realm, label):
    """A codex that forbids every proposal and vote, and whose guards refuse them too."""
    return switch(realm, (TOWN, "procedure", DENY_ALL), (TOWN, "tax", FORBID_ALL), (TOWN, "water", HARMLESS % label))


def status(realm, who):
    return ok(realm, "codex_status", who, query=True)


def inbox(realm, who):
    return [n["title"] for n in ok(realm, "notification_list", who, query=True, recipient="Department:IT")["notifications"]]


@pytest.fixture(scope="module")
def realm(wasm_of, people):
    """A launched realm of four members, a department IT whose seats are Alice's, Bob's and Mallory's (two of the three
    decide) and an empty Support, and votes that end once the outcome is certain."""
    realm = Canister(wasm_of("production"), installer=people["installer"])
    ctl, founder = people["ctl"], people["founder"]
    ok(realm, "setup_step", people["installer"], step="install", network="local", founder=founder)
    ok(realm, "setup_step", founder, step="realm", name="Town", open_registration=True)
    ok(realm, "setup_step", founder, step="trust_policy", require_marketplace_approval=False)
    ok(realm, "setup_launch", founder)
    members = [people[name] for name in ("alice", "bob", "mallory")]
    for member in members:
        ok(realm, "realm_join", member)
    ok(realm, "governance_update", ctl, voting_window=0)
    for department in ("IT", "Support"):
        ok(realm, "department_create", ctl, department=department)
    ok(realm, "position_create", ctl, department="IT", position="it.admin", headcount=3)
    for member in members:
        ok(realm, "appoint", ctl, position="it.admin", user=member)
    ok(realm, "department_set_policy", ctl, department="IT", m=2, n=3)
    return realm


def test_a_there_is_no_fast_revert_while_the_admins_have_designated_no_team(realm, people):
    ctl, alice = people["ctl"], people["alice"]
    ok(realm, "governance_update", ctl, voting_threshold=50 * PERCENT)
    first = switch(realm, (TOWN, "water", HARMLESS % "good"))
    assert (first["rule"]["threshold"], first["rule"]["window"]) == (50 * PERCENT, 0)  # in force when it became active
    # What the codexes after it leave behind: a rule nobody can pass and a window that would stall a revert.
    ok(realm, "governance_update", ctl, voting_threshold=100 * PERCENT, voting_window=30 * DAY)
    blocker(realm, "one")
    blocker(realm, "two")
    refused = realm.call("codex_revert_fast", {}, sender=ctl)  # a holder of the permission, were there a team
    assert refused["kind"] == "conflict" and "no IT admin team" in refused["error"]
    assert realm.call("codex_revert_fast", {}, sender=alice)["kind"] == "forbidden"  # and nobody else holds it
    assert status(realm, alice)["it_team"] == ""
    assert realm.call("proposal_create", {"title": "anything"}, sender=alice)["ok"] is False  # the codex blocks it


def test_b_the_admins_designate_the_team_and_can_change_it(realm, people):
    ctl, alice = people["ctl"], people["alice"]
    assert "it_team.set" in realm.call("it_team_set", {"department": "IT"}, sender=alice)["error"]
    assert realm.call("it_team_set", {"department": "Nowhere"}, sender=ctl)["kind"] == "not_found"
    ok(realm, "it_team_set", ctl, department="Support")
    assert status(realm, alice)["it_team"] == "Support"
    ok(realm, "it_team_set", ctl, department="IT")
    assert status(realm, alice)["it_team"] == "IT"
    assert "codex.revert_fast" in realm.call("codex_revert_fast", {}, sender=people["founder"])["error"]  # not a seat of it


def test_c_a_member_vote_reverts_two_codexes_back_under_the_rule_recorded_with_the_one_restored(realm, people):
    alice, founder, bob = people["alice"], people["founder"], people["bob"]
    active, first = status(realm, alice)["codex"]["id"], status(realm, alice)["history"][0]["id"]
    assert active != first
    action = {"op": "codex_revert", "args": {"codex": first}}
    pid = ok(realm, "proposal_create", alice, title="revert", action=action)["proposal"]  # no guard, no forbid
    proposal = ok(realm, "proposal_get", alice, query=True, proposal=pid)["proposal"]
    assert proposal["threshold"] == 50 * PERCENT  # the first codex's rule, not the 100 % the realm has now
    assert proposal["deadline"] == 0  # and its window: no deadline, the vote ends once its outcome is certain
    assert realm.call("proposal_create", {"title": "revert", "action": {"op": "codex_revert", "args": {}}},
                      sender=alice)["kind"] == "invalid_argument"  # a filed revert names the codex it restores
    for voter in (founder, alice, bob):  # three of four: more than half, so that nothing more is awaited
        ok(realm, "proposal_vote", voter, proposal=pid, choice="yes")
    assert ok(realm, "proposal_get", alice, query=True, proposal=pid)["proposal"]["status"] == "executed"
    assert status(realm, alice)["codex"]["id"] == first
    plain = ok(realm, "proposal_create", alice, title="the realm runs on its own rule again")["proposal"]
    now = ok(realm, "proposal_get", alice, query=True, proposal=plain)["proposal"]
    assert (now["threshold"], now["deadline"] > 0) == (100 * PERCENT, True)


def test_d_the_team_reverts_fast_to_the_previous_codex_only_under_its_own_policy(realm, people):
    alice, bob, mallory = people["alice"], people["bob"], people["mallory"]
    first = status(realm, alice)["codex"]["id"]
    blocked = blocker(realm, "three")["codex"]
    older = status(realm, alice)["history"][1]["id"]
    assert realm.call("codex_revert_fast", {"codex": older}, sender=alice)["kind"] == "conflict"  # not the previous one
    filed = ok(realm, "codex_revert_fast", alice)  # two of the three seats of IT decide, not the realm
    assert filed["requires_vote"]
    action = ok(realm, "proposal_get", alice, query=True, proposal=filed["proposal"])["proposal"]["action"]
    assert action == {"op": "codex_revert_fast", "args": {"codex": first}}
    # Its window is the one recorded with the codex it restores (no deadline), not the 30 days the realm has now.
    assert ok(realm, "proposal_get", alice, query=True, proposal=filed["proposal"])["proposal"]["deadline"] == 0
    ok(realm, "proposal_vote", alice, proposal=filed["proposal"], choice="yes")
    assert status(realm, alice)["codex"]["id"] == blocked  # one of two approvals
    ok(realm, "proposal_vote", mallory, proposal=filed["proposal"], choice="yes")
    assert status(realm, alice)["codex"]["id"] == first
    events = [e for e in ok(realm, "events", people["ctl"], query=True)["events"] if e["op"] == "codex_revert_fast"]
    assert events and events[0]["result"]["codex"] == first  # each use is recorded with an Event
    assert realm.call("codex_revert_fast", {}, sender=bob)["kind"] == "conflict"  # the first codex replaced none


def test_e_no_codex_changes_the_team_and_the_team_is_told_when_a_codex_method_fails(realm, people):
    alice, bob = people["alice"], people["bob"]
    team = run(realm, "import json\nfrom ggg import Department\nprint(json.dumps({'id': Department['IT']._id}))")["id"]
    switch(realm, (TOWN, "water", BREAKS), (TOWN, "register", CHANGES_THE_TEAM.replace("__ID__", str(team))))
    assert inbox(realm, alice) == []
    broken = realm.call("proposal_create", {"title": "break the water"}, sender=alice)
    assert broken["error"] == "legislation error in x/town/water.py: ValueError: no water here"
    told = "The codex fails: check_proposal_create of x/town/water.py"
    assert inbox(realm, alice) == [told] and inbox(realm, bob) == [told]
    realm.call("proposal_create", {"title": "break the water"}, sender=alice)
    assert inbox(realm, alice) == [told]  # told already
    realm.wait(DAY)
    realm.call("proposal_create", {"title": "break the water"}, sender=alice)
    assert inbox(realm, alice) == [told, told]  # a day on, it still fails
    assert failures(realm, alice)["x/town/water.py"]["check_proposal_create"]["count"] == 3

    for title, attempted in (("change the team", "department_set_policy"), ("designate", "it_team_set"),
                             ("by id", "department_set_policy")):
        ok(realm, "proposal_create", alice, title=title)
        error = failures(realm, alice)["x/town/register.py"]["on_proposal_create"]["error"]
        assert error.startswith(f"{attempted}: ") and ("no codex may" in error or "reserved operation" in error), error
    policy = ok(realm, "department_get", alice, query=True, department="IT")["department"]["policy"]
    assert policy["m"] == 2 and policy["n"] == 3 and status(realm, alice)["it_team"] == "IT"


def test_f_no_chapter_runs_for_the_reserved_operations_and_the_checker_refuses_a_chapter_that_guards_one(realm, people):
    from realms.legislation import RESERVED

    assert set(RESERVED) == {"codex_revert", "codex_revert_fast", "it_team_set", "sync_source_remove"}
    names = run(realm, "import json\nfrom core import legislation\n"
                "print(json.dumps({'reserved': list(legislation.RESERVED), 'action': legislation.is_reserved("
                "'proposal_vote', {'proposal': 'x'}), 'removal': legislation.is_reserved('sync_source_remove', {})}))")
    assert names["reserved"] == list(RESERVED) and names["removal"] and not names["action"]
    for name in RESERVED:
        guard = 'from realms.legislation import above\n\n\nclass Water(above("water")):\n' \
                f'    def check_{name}(self, args):\n        return self.deny("no")\n'
        out = realm.shell(BUILD_ONE % {"text": guard})
        errors = json.loads(out[out.index("["):])
        assert errors and name in errors[0], (name, errors)


def test_g_a_codex_that_blocks_every_proposal_cannot_block_a_realm_leaving_its_sync_source(realm, people):
    ctl, alice, bob, founder, mallory = (people[n] for n in ("ctl", "alice", "bob", "founder", "mallory"))
    source = {"Record": {"url": STAND_IN_URL, "checksum": "sha256:" + "a" * 64}}
    ok(realm, "sync_source_add", ctl, source=source, name="The EU")  # a stand-in record is active on this vote
    ok(realm, "governance_update", ctl, voting_threshold=50 * PERCENT)
    ok(realm, "department_set_policy", ctl, department="Administration", m=2, n=3)  # now a vote decides what the realm does
    blocker(realm, "four")
    assert realm.call("proposal_create", {"title": "anything"}, sender=alice)["kind"] == "forbidden"  # the codex blocks it
    filed = ok(realm, "sync_source_remove", ctl, source=source)  # a proposal with no guard, no forbid and no hook
    assert [s["status"] for s in ok(realm, "realm_info", alice, query=True)["sync_sources"]] == ["active"]
    for voter in (founder, alice, bob, mallory):
        ok(realm, "proposal_vote", voter, proposal=filed["proposal"], choice="yes")
    assert ok(realm, "proposal_get", alice, query=True, proposal=filed["proposal"])["proposal"]["status"] == "executed"
    ended = ok(realm, "realm_info", alice, query=True)["sync_sources"]
    assert len(ended) == 1 and "Ended" in ended[0]["status"]


def test_h_the_team_and_the_recorded_rules_survive_an_upgrade(realm, people):
    alice = people["alice"]
    before = run(realm, "import json\nfrom ggg import LayeredCodex\n"
                 "print(json.dumps({r.id: r.voting_rule for r in LayeredCodex.load_some(1, LayeredCodex.max_id())}))")
    assert any(rule.get("threshold") == 50 * PERCENT for rule in before.values())
    realm.wait(600)
    realm.upgrade()
    assert status(realm, alice)["it_team"] == "IT"
    after = run(realm, "import json\nfrom ggg import LayeredCodex\n"
                "print(json.dumps({r.id: r.voting_rule for r in LayeredCodex.load_some(1, LayeredCodex.max_id())}))")
    assert after == before


def test_i_a_legacy_codex_records_the_rule_too_and_is_restored_by_its_checksum(wasm_of, people):
    ctl, founder = people["ctl"], people["founder"]
    realm = new_realm(wasm_of("production"), people)
    ok(realm, "governance_update", ctl, voting_threshold=60 * PERCENT)
    first = ok(realm, "codex_install", founder, **codex_of("dominion"))["codex"]
    checksum = status(realm, founder)["codex"]["checksum"]
    ok(realm, "governance_update", ctl, voting_threshold=70 * PERCENT)
    second = ok(realm, "codex_install", founder, **codex_of("dominion", "99.0.0"))["codex"]
    newer = status(realm, founder)["codex"]["checksum"]
    rules = run(realm, "import json\nfrom ggg import Codex\n"
                "print(json.dumps({c.id: c.voting_rule['threshold'] for c in Codex.load_some(1, Codex.max_id())}))")
    assert rules == {first: 60 * PERCENT, second: 70 * PERCENT}
    assert realm.call("codex_revert", {"codex": "0" * 64}, sender=ctl)["kind"] == "not_found"
    assert ok(realm, "codex_revert", ctl, codex=checksum)["codex"] == first
    assert status(realm, founder)["codex"]["id"] == first
    revert = lambda arg: realm.ggg("ggg_codex_revert", REVERT_ARGS, arg, GOVERNED, ctl)  # noqa: E731
    assert revert([{"codex": [newer]}]) == {"Ok": {"Done": []}}  # GGG's optional argument names the codex
    assert status(realm, founder)["codex"]["id"] == second
    assert revert([]) == {"Ok": {"Done": []}} and status(realm, founder)["codex"]["id"] == first  # none: the previous one
    assert revert([{"codex": []}]) == {"Err": {"Conflict": "there is no previous codex to revert to"}}
