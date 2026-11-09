"""A new codex across a realm's own quarters (docs/codices/layered-codex.md §9.5), in process: the capital tells its
quarters what it staged, a quarter stages it from its capital and switches at T, the capital judges the quarters, and
calls between quarters carry the sender's codex checksum. The other canisters are scripted peers
(tests/ggg/test_scripted_peers.py). tests/deployed/test_propagation.py runs the same on a federation of real wasms."""

import fake_chain
import pytest
import world
from api import propagation, quarters
from core import compliance
from core import legislation
from fake_chain import peer, rejects
from ggg import (FederalVoteLeg, FederationMessage, Forward, InstallJob, LayeredCodex, Proposal, Quarter, Realm,
                 TaskSchedule)
from kit import call, fire_timers, later, ok, rows
from test_federation import leg, open_fv
from test_installs import HOUR, change_list, expected_id, fire_due, install, job, own_layer, run_job, schedule, sha, status
from test_legislation_runtime import TOWN, activate, layered
from test_quarters import CAPITAL, capital, quarter

STAGED = {"ok": True, "staged": "started", "job": "J-1"}
OTHER = "sha256:" + "b" * 64


@pytest.fixture
def federation():
    """The capital, with north and south registered and reporting, and the two of them played by peers."""
    capital("north", "south")
    ok("governance_update", "ctl", voting_window=0)
    return peer("north", federation_message=STAGED), peer("south", federation_message=STAGED)


def flag(name):
    """The flag the capital keeps for quarter ``name``."""
    return compliance.of(Quarter[name].compliance)


def report(name, codex_id, **args):
    return ok("quarter_report", name, population=1, codex_id=codex_id, codex_version=codex_id, **args)


# ---- The capital tells its quarters.


def test_the_capital_tells_each_active_quarter_what_it_staged_and_when_it_switches(federation):
    layer = own_layer()
    args = change_list(layer)
    p = schedule(args)
    run_job()
    assert job()["step"] == "wait"
    body = {"codex": expected_id(layer), "checksum": args["checksum"], "at": Proposal[p].effective_at, "proposal": p}
    for north_or_south in federation:
        assert north_or_south.sent("federation_message") == [{"topic": "codex.stage", "body": body, "codex": ""}]
    messages = [(m.topic, m.body, list(m.response)) for m in rows(FederationMessage)]
    assert sorted(messages, key=lambda m: m[2]) == [("codex.stage", body, ["north"]), ("codex.stage", body, ["south"])]


def test_a_codex_installed_at_once_is_announced_with_the_acceptance_as_its_time(federation):
    layer = own_layer()
    install(change_list(layer))
    assert propagation.required().id == expected_id(layer)
    sent = federation[0].sent("federation_message")[0]["body"]
    assert (sent["codex"], sent["at"]) == (expected_id(layer), fake_chain.now())


def test_a_quarter_that_cannot_be_reached_holds_up_no_other_and_the_record_says_so(federation):
    north, south = federation
    north.answer("federation_message", rejects("the canister is stopped"))
    schedule(change_list(own_layer()))
    run_job()
    assert len(south.sent("federation_message")) == 1
    answers = {next(iter(m.response)): next(iter(m.response.values())) for m in rows(FederationMessage)}
    assert answers["south"] == STAGED
    assert answers["north"] == {"ok": False, "error": "federation_message on north failed: the canister is stopped"}


def test_a_realm_without_quarters_announces_to_no_one():
    world.build()
    ok("governance_update", "ctl", voting_window=0)
    install(change_list(own_layer()))
    assert FederationMessage.count() == 0


def test_stopping_the_proposal_tells_the_quarters_to_drop_what_they_staged(federation):
    layer = own_layer()
    p = schedule(change_list(layer))
    run_job()
    ok("proposal_stop", "ctl", proposal=p)
    fire_due()
    for quarter_peer in federation:
        assert quarter_peer.sent("federation_message")[1] == {"topic": "codex.unstage",
                                                              "body": {"codex": expected_id(layer)}, "codex": ""}


# ---- The capital judges them.


def test_a_quarter_running_the_codex_in_force_is_compliant_and_one_that_is_not_is_non_compliant_since_it_took_effect(
        federation):
    layer = own_layer()
    install(change_list(layer))
    switched = fake_chain.now()
    report("north", expected_id(layer))
    report("south", OTHER)
    assert flag("north") == "compliant"
    assert flag("south") == {"NonCompliant": {"since": switched, "stale": ["arcadia"]}}
    rows_ = {q["id"]: q["compliance"] for q in ok("quarter_directory", fake_chain.ANONYMOUS)["quarters"]}
    assert compliance.of(rows_["south"]) == flag("south") and compliance.of(rows_[CAPITAL]) == "compliant"


def test_a_quarter_stays_non_compliant_since_the_same_time_until_it_runs_what_the_capital_runs(federation):
    first, second = own_layer(), {**own_layer(), "arcadia/tax": own_layer("2.0.0")["arcadia/tax"]}
    install(change_list(first))
    switched = fake_chain.now()
    report("south", OTHER)
    later(HOUR)
    install(change_list(second, replaces=expected_id(first), url="https://legislation.example.test/changes/second.json"))
    report("south", expected_id(first))  # a codex the capital knows: only the layers that differ are stale
    assert flag("south") == {"NonCompliant": {"since": switched, "stale": ["arcadia"]}}
    report("south", expected_id(second))
    assert flag("south") == "compliant"


def test_a_report_that_names_no_codex_is_not_judged_and_a_capital_without_a_layered_codex_judges_no_one(federation):
    report("south", OTHER)
    assert flag("south") == "unknown"  # the capital runs no layered codex
    install(change_list(own_layer()))
    ok("quarter_report", "north", population=1)
    assert flag("north") == "unknown"  # it said nothing of its codex
    report("north", "")  # it runs none: that is behind the capital
    assert flag("north")["NonCompliant"]["stale"] == ["arcadia"]


def test_the_capital_judges_the_quarters_that_did_not_switch_a_moment_after_it_did(federation):
    report("north", OTHER)
    report("south", OTHER)
    layer = own_layer()
    install(change_list(layer))
    switched = fake_chain.now()
    report("south", expected_id(layer))  # south switched with the capital and said so
    assert flag("north") == "unknown"  # nobody has judged north yet
    later(propagation.JUDGE_AFTER)
    fire_timers()
    assert flag("north") == {"NonCompliant": {"since": switched, "stale": ["arcadia"]}}
    assert flag("south") == "compliant"


def test_a_quarter_that_already_runs_the_pending_codex_at_its_time_is_not_behind_the_capital(federation):
    layer = own_layer()
    schedule(change_list(layer))
    run_job()
    report("north", expected_id(layer))
    assert flag("north") == "unknown"  # before T the capital still runs no layered codex: nothing to judge against
    later(HOUR)  # T has come; the capital's own switch is a moment behind
    report("south", expected_id(layer))
    assert flag("south") == "compliant"


# ---- A quarter stages from its capital and switches at T.


def capital_serving(layer, listed, running=None):
    """The capital as a peer: it serves the change list and the chapters by checksum, and says which codex it requires
    of its quarters (``running``, "" for none)."""
    texts = {"sha256:" + sha(body): body.decode() for body in layer.values()}
    texts[listed["checksum"]] = fake_chain.state["web"][listed["url"]][1].decode()

    def chapter(sent):
        if sent["chapter"] in texts:
            return {"ok": True, "text": texts[sent["chapter"]]}
        return {"ok": False, "error": "not found", "kind": "not_found"}

    return peer(CAPITAL, legislation_chapter=chapter, federation_message={"ok": True, "codex": running or ""},
                quarter_sync={"ok": True, "directory": [], "codex": {"id": running, "version": running}, "index": 1})


def announce(layer, listed, at, **extra):
    body = {"codex": expected_id(layer), "checksum": listed["checksum"], "at": at, "proposal": "P-9", **extra}
    return call("federation_message", CAPITAL, topic="codex.stage", body=body)


def test_a_quarter_stages_from_its_capital_and_switches_at_t_and_runs_no_realm_hook(monkeypatch):
    quarter(monkeypatch, "north")
    layer = own_layer()
    listed = change_list(layer)
    serving = capital_serving(layer, listed, running=expected_id(layer))  # the capital switches at T too
    at = fake_chain.now() + HOUR
    assert announce(layer, listed, at)["staged"] == "started"
    run_job()
    seen = status()
    assert (seen["job"]["status"], seen["job"]["step"], seen["job"]["proposal"]) == ("running", "wait", "")  # not P-9
    assert seen["codex"] is None and seen["pending"]["id"] == expected_id(layer) and seen["pending"]["activates_at"] == at
    assert {c["route"] for c in seen["pending"]["chapters"].values()} == {"capital"}
    assert [sent["chapter"] for sent in serving.sent("legislation_chapter")] == [
        listed["checksum"]] + ["sha256:" + sha(body) for body in layer.values()]
    assert not [c for c in fake_chain.state["calls"] if c[0] == "https"]  # nothing came from the web
    assert TaskSchedule.count() == 1
    later(HOUR - 1)
    fire_due()
    assert status()["codex"] is None  # nothing switches before T
    later(1)
    fire_timers()
    assert status()["job"]["step"] == "confirm"  # at T it asks the capital which codex it requires
    run_job()
    seen = status()
    assert seen["codex"]["id"] == expected_id(layer) and seen["job"]["status"] == "done" and seen["pending"] is None
    assert serving.sent("federation_message") == [{"topic": "codex.required", "body": {}, "codex": ""}]
    fire_due()
    assert ok("notification_list", "alice")["notifications"] == []  # on_codex_activate runs once per realm, on the capital
    assert [s["population"] for s in serving.sent("quarter_sync")] == [len(world.PEOPLE)]  # it told its capital


def test_a_quarter_that_is_ready_after_t_switches_if_the_capital_still_runs_the_codex(monkeypatch):
    quarter(monkeypatch, "north")
    layer = own_layer()
    listed = change_list(layer)
    capital_serving(layer, listed, running=expected_id(layer))
    assert announce(layer, listed, fake_chain.now() - 5)["staged"] == "started"
    run_job()
    assert status()["codex"]["id"] == expected_id(layer) and status()["job"]["status"] == "done"
    assert TaskSchedule.count() == 0  # T had passed: no schedule


@pytest.mark.parametrize("running", [OTHER, ""], ids=["a newer codex", "none after a revert to a legacy one"])
def test_a_quarter_never_switches_to_a_codex_the_capital_has_left(monkeypatch, running):
    quarter(monkeypatch, "north")
    layer = own_layer()
    listed = change_list(layer)
    capital_serving(layer, listed, running=running)
    announce(layer, listed, fake_chain.now() - 5)
    run_job()
    seen = status()
    assert seen["codex"] is None and seen["pending"] is None
    assert seen["job"]["status"] == "failed" and "a codex it has left is not switched to" in seen["job"]["error"]


def test_a_quarter_builds_the_codex_the_capital_announced_and_no_other(monkeypatch):
    quarter(monkeypatch, "north")
    layer = own_layer()
    listed = change_list(layer)
    capital_serving(layer, listed)
    body = {"codex": OTHER, "checksum": listed["checksum"], "at": fake_chain.now() + HOUR, "proposal": "P-9"}
    call("federation_message", CAPITAL, topic="codex.stage", body=body)
    run_job()
    assert status()["pending"] is None and "not the " + OTHER + " the capital announced" in job()["error"]


def test_a_quarter_that_cannot_stage_keeps_its_codex_and_says_why(monkeypatch):
    quarter(monkeypatch, "north")
    layer = own_layer()
    listed = change_list(layer)
    capital_serving(layer, listed).answer("legislation_chapter", {"ok": False, "error": "not found"})
    at = fake_chain.now() + HOUR
    announce(layer, listed, at)
    run_job()
    assert status()["job"]["status"] == "failed" and "the capital does not serve" in status()["job"]["error"]
    later(HOUR)
    fire_timers()
    assert status()["codex"] is None and status()["pending"] is None


def test_a_staged_codex_the_capital_withdraws_is_dropped_and_never_switched_to(monkeypatch):
    quarter(monkeypatch, "north")
    layer = own_layer()
    listed = change_list(layer)
    capital_serving(layer, listed)
    announce(layer, listed, fake_chain.now() + HOUR)
    run_job()
    assert status()["pending"]["id"] == expected_id(layer)
    out = call("federation_message", CAPITAL, topic="codex.unstage", body={"codex": expected_id(layer)})
    assert out["unstaged"] is True
    assert status()["pending"] is None and status()["job"]["error"] == "the capital withdrew it"
    later(HOUR)
    fire_timers()
    assert status()["codex"] is None
    assert call("federation_message", CAPITAL, topic="codex.unstage", body={"codex": expected_id(layer)})["unstaged"] is False


def test_the_same_announcement_twice_changes_nothing_and_a_newer_one_replaces_a_staging_in_progress(monkeypatch):
    quarter(monkeypatch, "north")
    layer = own_layer()
    listed = change_list(layer)
    capital_serving(layer, listed)
    at = fake_chain.now() + HOUR
    assert announce(layer, listed, at)["staged"] == "started"
    assert announce(layer, listed, at)["staged"] == "running" and InstallJob.count() == 1
    newer = {**layer, "arcadia/tax": own_layer("2.0.0")["arcadia/tax"]}
    newer_list = change_list(newer, url="https://legislation.example.test/changes/newer.json")
    assert announce(newer, newer_list, at)["staged"] == "started"
    first = InstallJob["J-1"]
    assert first.status == "failed" and "which replaces it" in first.error and InstallJob["J-2"].status == "running"


def test_an_announcement_for_the_codex_already_running_changes_nothing(monkeypatch):
    quarter(monkeypatch, "north")
    layer = own_layer()
    listed = change_list(layer)
    capital_serving(layer, listed, running=expected_id(layer))
    announce(layer, listed, fake_chain.now() - 5)
    run_job()
    assert announce(layer, listed, fake_chain.now() - 5)["staged"] == "active" and InstallJob.count() == 1


def test_only_the_capital_announces_and_an_install_of_its_own_in_a_quarter_refuses_the_announcement(monkeypatch):
    quarter(monkeypatch, "north", "south")
    layer = own_layer()
    listed = change_list(layer)
    capital_serving(layer, listed)
    body = {"codex": expected_id(layer), "checksum": listed["checksum"], "at": 0, "proposal": ""}
    assert "only this quarter's capital" in call("federation_message", "south", topic="codex.stage", body=body)["error"]
    for bad in ({**body, "codex": "agora"}, {**body, "checksum": "x"}, {**body, "at": "soon"}):
        assert "a codex to stage is" in call("federation_message", CAPITAL, topic="codex.stage", body=bad)["error"]
    ok("codex_install", "ctl", **change_list(own_layer(), url="https://legislation.example.test/changes/own.json"))
    refused = call("federation_message", CAPITAL, topic="codex.stage", body=body)
    assert (refused["kind"], refused["error"]) == ("conflict", "an install is running")


def test_a_realm_that_left_the_capital_that_announced_a_codex_never_switches_to_it(monkeypatch):
    quarter(monkeypatch, "north")
    layer = own_layer()
    listed = change_list(layer)
    capital_serving(layer, listed)
    announce(layer, listed, fake_chain.now() + HOUR)
    run_job()
    assert status()["pending"]["id"] == expected_id(layer)
    Realm["1"].is_quarter, Realm["1"].is_capital, Realm["1"].federation_realm_id = False, True, ""  # it seceded
    later(HOUR)
    fire_timers()
    assert status()["codex"] is None and status()["pending"] is None
    assert "no longer a quarter of the capital" in job()["error"]


def test_a_quarter_ready_after_t_that_left_its_capital_does_not_switch(monkeypatch):
    quarter(monkeypatch, "north")
    layer = own_layer()
    listed = change_list(layer)
    capital_serving(layer, listed, running=expected_id(layer))
    announce(layer, listed, fake_chain.now() + HOUR)
    later(HOUR)  # the job runs after T
    Realm["1"].federation_realm_id = "another-capital"
    run_job()
    assert status()["codex"] is None and "no longer a quarter of the capital" in job()["error"]


def test_switching_by_hand_before_t_is_refused_and_after_t_it_is_harmless(monkeypatch):
    quarter(monkeypatch, "north")
    layer = own_layer()
    listed = change_list(layer)
    capital_serving(layer, listed, running=expected_id(layer))
    announce(layer, listed, fake_chain.now() + HOUR)
    run_job()
    assert "switches at" in call("codex_switch_staged", fake_chain.ANONYMOUS, job="J-1")["error"]
    later(HOUR)
    assert ok("codex_switch_staged", fake_chain.ANONYMOUS, job="J-1")["step"] == "confirm"
    assert ok("codex_switch_staged", fake_chain.ANONYMOUS, job="J-1")["step"] == "confirm"  # again: nothing more happens
    run_job()
    assert job()["status"] == "done"


# ---- Calls between quarters carry the sender's codex checksum.


def test_a_message_from_a_quarter_is_recorded_with_its_codex_and_the_flag_the_capital_gives_it(federation):
    layer = own_layer()
    install(change_list(layer))
    report("south", OTHER)
    ok("federation_message", "south", topic="ping", body={}, codex=OTHER)
    message = rows(FederationMessage)[-1]
    assert (message.source, message.codex) == ("south", OTHER) and compliance.of(message.compliance) != "compliant"


def test_a_vote_leg_is_recorded_with_the_codex_of_its_quarter_and_its_flag(monkeypatch):
    vote = open_fv("north", "south")
    ok("governance_update", "ctl", voting_window=0)
    layer = own_layer()
    install(change_list(layer))
    assert leg("north", vote, "passed", yes=2, codex=expected_id(layer))["ok"]
    assert leg("south", vote, "passed", yes=2, codex=OTHER)["ok"]
    north, south = FederalVoteLeg[f"{vote}:north"], FederalVoteLeg[f"{vote}:south"]
    assert (north.codex, compliance.of(north.compliance)) == (expected_id(layer), "compliant")
    assert south.codex == OTHER and compliance.of(south.compliance)["NonCompliant"]["stale"] == ["arcadia"]
    assert legislation._read({"entity": "FederalVoteLeg", "method": "get", "id": south.id})["codex"] == OTHER


FEDERATION_CHAPTER = '''
from ggg import Quarter
from realms.legislation import above

class Federation(above("federation")):
    def check_federation_message(self, args):
        if args.codex == "%(other)s":
            return self.deny("the sender runs " + args.codex[:13])
        if args.topic == "user.arrive" and Quarter.get(args.body["from"]).compliance != "compliant":
            return self.deny("moves in from a quarter that is not compliant are refused")
'''


def test_legislation_decides_on_a_message_by_the_codex_it_carries_and_the_flag_of_its_sender(monkeypatch):
    quarter(monkeypatch, "north", "south")
    activate(layered({(TOWN, "federation"): FEDERATION_CHAPTER % {"other": OTHER}}, layers=[TOWN]))
    assert call("federation_message", "south", topic="ping", body={}, codex=OTHER)["error"] == "the sender runs sha256:bbbbbb"
    assert ok("federation_message", "south", topic="ping", body={}, codex=expected_id(own_layer()))
    arrival = {"user": "bob", "from": "south", "moved_at": 1, "signature": "x"}
    Quarter["south"].compliance = compliance.valid("compliant")
    assert "signature does not match" in call("federation_message", "south", topic="user.arrive", body=arrival)["error"]
    Quarter["south"].compliance = compliance.valid({"NonCompliant": {"since": 5, "stale": ["arcadia"]}})
    assert "not compliant are refused" in call("federation_message", "south", topic="user.arrive", body=arrival)["error"]


def test_a_move_carries_the_codex_of_the_quarter_it_leaves_and_is_recorded_with_the_flag_of_the_one_it_enters(monkeypatch):
    quarter(monkeypatch, "north", "south")
    LayeredCodex(id=OTHER, status="active", layers=["arcadia"], layer_paths={}, chapters={})
    Quarter["south"].compliance = compliance.valid({"NonCompliant": {"since": 5, "stale": ["arcadia"]}})
    south = peer("south", federation_message={"ok": True, "member": "bob"})
    out = ok("self_change_quarter", "bob", quarter="south")
    assert south.sent("federation_message")[0]["codex"] == OTHER
    moved = Forward[out["forward"]]
    assert moved.codex == OTHER and compliance.of(moved.compliance) == {"NonCompliant": {"since": 5, "stale": ["arcadia"]}}


def test_a_quarter_drifts_when_its_layered_codex_is_not_its_capitals(monkeypatch):
    quarter(monkeypatch, "north")
    LayeredCodex(id=OTHER, status="active", layers=["arcadia"], layer_paths={}, chapters={})
    assert quarters.codex() == {"id": OTHER, "version": OTHER}
    Realm["1"].sync_state = {"codex": {"id": "sha256:" + "c" * 64, "version": "sha256:" + "c" * 64}}
    assert quarters.drifting(quarters.codex()["version"])


# ---- A revert on the capital reaches the quarters.


def two_installs():
    """The capital's codex, installed twice: the first, then the second that replaces it. Returns their ids."""
    first, second = own_layer(), {**own_layer(), "arcadia/tax": own_layer("2.0.0")["arcadia/tax"]}
    install(change_list(first))
    install(change_list(second, replaces=expected_id(first), url="https://legislation.example.test/changes/second.json"))
    return expected_id(first), expected_id(second)


def test_a_revert_on_the_capital_tells_each_quarter_and_runs_no_realm_hook(federation):
    first, second = two_installs()
    report("north", second)
    report("south", second)
    for quarter_peer in federation:
        quarter_peer.calls.clear()
    fire_due()
    activated = len(ok("notification_list", "alice")["notifications"])
    later(HOUR)
    ok("codex_revert", "ctl")
    fire_due()
    for quarter_peer in federation:
        assert quarter_peer.sent("federation_message") == [{"topic": "codex.revert", "body": {"codex": first},
                                                            "codex": first}]
    assert len(ok("notification_list", "alice")["notifications"]) == activated  # a revert runs no on_codex_activate
    reverted = fake_chain.now()
    report("north", first)  # north followed
    later(propagation.JUDGE_AFTER)
    fire_timers()  # the capital judges the quarters that did not
    assert flag("north") == "compliant"
    assert flag("south") == {"NonCompliant": {"since": reverted, "stale": ["arcadia"]}}  # since the revert, not since first


def test_a_quarter_that_reports_a_codex_the_realm_voted_out_is_told_again(federation):
    first, second = two_installs()
    ok("codex_revert", "ctl")
    fire_due()
    federation[1].calls.clear()
    report("south", second)  # it was down when the capital told it, and reports what it still runs
    fire_due()
    assert federation[1].sent("federation_message") == [{"topic": "codex.revert", "body": {"codex": first}, "codex": first}]
    federation[1].calls.clear()
    report("south", first)  # it restored it: nothing more to say
    report("south", OTHER)  # a codex the realm never ran is not one it voted out
    fire_due()
    assert federation[1].sent("federation_message") == []


def stored_codices():
    """Two codices a quarter has run, the second now active: ids of the first and the second."""
    realm = "from realms.legislation import above\n\nclass Realm(above('realm')):\n    pass\n"
    first = activate(layered({("arcadia", "realm"): realm}, layers=["arcadia"]))
    first.status = "inactive"
    second = activate(layered({("arcadia", "realm"): realm + "# two\n"}, layers=["arcadia"]))
    second.previous = first.id
    return first.id, second.id


def test_a_quarter_restores_the_codex_its_capital_reverted_to_from_its_own_history_and_reports_it(monkeypatch):
    quarter(monkeypatch, "north")
    first, second = stored_codices()
    capital_peer = peer(CAPITAL, quarter_sync={"ok": True, "directory": [], "codex": {"id": first, "version": first}, "index": 1})
    out = call("federation_message", CAPITAL, topic="codex.revert", body={"codex": first})
    assert out["ok"] and out["reverted"] == "restored"
    assert status()["codex"]["id"] == first and LayeredCodex[second].status == "reverted"
    fire_due()
    assert ok("notification_list", "alice")["notifications"] == []  # no on_codex_activate
    assert [s["codex_id"] for s in capital_peer.sent("quarter_sync")] == [first]  # it told its capital
    assert call("federation_message", CAPITAL, topic="codex.revert", body={"codex": first})["reverted"] == "active"


def test_a_quarter_refuses_a_revert_to_a_codex_it_never_ran_and_from_anyone_but_its_capital(monkeypatch):
    quarter(monkeypatch, "north", "south")
    first, second = stored_codices()
    unknown = call("federation_message", CAPITAL, topic="codex.revert", body={"codex": OTHER})
    assert (unknown["kind"], status()["codex"]["id"]) == ("not_found", second)
    assert "only this quarter's capital" in call("federation_message", "south", topic="codex.revert", body={"codex": first})["error"]
    assert status()["codex"]["id"] == second


# ---- What the review found: the relay, the switch at T, and the proposal ids.


@pytest.mark.parametrize("topic", ["codex.stage", "codex.unstage", "codex.revert", "codex.required"])
def test_an_admin_cannot_relay_the_realms_own_topics_to_the_quarters(federation, topic):
    layer = own_layer()
    body = {"codex": expected_id(layer), "checksum": change_list(layer)["checksum"], "at": 0, "proposal": "P-1"}
    refused = call("federation_message", "ctl", topic=topic, body=body)  # a holder of everything, the controller
    assert refused["kind"] == "forbidden" and "never relayed" in refused["error"]
    for quarter_peer in federation:
        assert quarter_peer.sent("federation_message") == []
    assert ok("federation_message", "ctl", topic="ping", body={})["replies"].keys() == {"north", "south"}  # others go


def test_the_capital_says_which_codex_it_requires_the_pending_one_once_it_is_due(federation):
    assert ok("federation_message", "north", topic="codex.required")["codex"] == ""  # no layered codex
    layer = own_layer()
    p = schedule(change_list(layer))
    run_job()
    assert ok("federation_message", "north", topic="codex.required")["codex"] == ""  # pending, not due
    later(HOUR)
    assert ok("federation_message", "north", topic="codex.required")["codex"] == expected_id(layer)  # due
    fire_timers()
    run_job()
    assert Proposal[p].status == "executed"
    assert ok("federation_message", "north", topic="codex.required")["codex"] == expected_id(layer)  # in force


def staged_until_t(monkeypatch, running):
    """A quarter that staged a codex for T an hour away, its capital requiring ``running``; returns the capital peer."""
    quarter(monkeypatch, "north")
    layer = own_layer()
    listed = change_list(layer)
    serving = capital_serving(layer, listed, running=running)
    announce(layer, listed, fake_chain.now() + HOUR)
    run_job()
    assert job()["step"] == "wait"
    later(HOUR)
    return serving, layer


def test_a_quarter_that_missed_the_withdrawal_does_not_switch_at_t_because_the_capital_no_longer_requires_it(monkeypatch):
    staged_until_t(monkeypatch, running="")  # the proposal was stopped; the unstage never arrived
    fire_timers()
    run_job()
    assert status()["codex"] is None and status()["pending"] is None
    assert "a codex it has left is not switched to" in job()["error"]


def test_a_quarter_does_not_switch_at_t_when_the_capital_requires_another_codex(monkeypatch):
    staged_until_t(monkeypatch, running=OTHER)
    fire_timers()
    run_job()
    assert status()["codex"] is None and "the capital requires " + OTHER in job()["error"]


def test_a_capital_that_does_not_answer_at_t_leaves_the_quarter_on_its_codex_until_an_admin_asks_again(monkeypatch):
    serving, layer = staged_until_t(monkeypatch, running=None)
    serving.answer("federation_message", rejects("the canister is stopped"))
    fire_timers()
    run_job()
    assert status()["codex"] is None and job()["status"] == "paused" and "did not confirm" in job()["error"]
    serving.answer("federation_message", {"ok": True, "codex": expected_id(layer)})
    ok("codex_install_resume", "ctl", job="J-1")
    run_job()
    assert status()["codex"]["id"] == expected_id(layer) and job()["status"] == "done" and job()["error"] == ""


def test_the_capitals_proposal_id_is_not_the_id_of_a_proposal_of_the_quarter(monkeypatch):
    quarter(monkeypatch, "north")
    layer = own_layer()
    listed = change_list(layer)
    capital_serving(layer, listed, running=expected_id(layer))
    announce(layer, listed, fake_chain.now() + HOUR, proposal="P-1")  # the id of the quarter's own first proposal
    run_job()
    assert job()["proposal"] == "" and status()["pending"]["proposal"] == ""
    ok("governance_update", "ctl", voting_window=0)
    other = change_list(own_layer("2.0.0"), url="https://legislation.example.test/changes/own.json")
    p = ok("proposal_create", "alice", title="Legislation", action={"op": "codex_install", "args": other})["proposal"]
    assert p == "P-1"
    for voter in ("alice", "bob", "dana", "carla"):
        if Proposal[p].status == "voting":
            ok("proposal_vote", voter, proposal=p, choice="yes")
    assert Proposal[p].status == "failed" and "an install is running" in Proposal[p].result["error"]  # not the staged job
    assert status()["codex"] is None and job()["step"] == "wait"
