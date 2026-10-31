import fake_chain
import world
from ggg import (Case, Contract, Delegation, Department, Invoice, License, Member, Notification, Organization,
                 Penalty, User, UserProfile, Verdict)
from kit import denied, later, ok

DAY = 86400
PLANS = {"ref": "vetkey:bob:plans", "hash": "sha256:4be1"}


def agora():
    """Agora's justice: North First Instance Court under Agora Appeals Court, Judge Elena and Judge Ruth."""
    depts = world.build()
    judge = UserProfile["justice.judge"]
    judge.allowed_to = judge.allowed_to + ["resolution.link_contract", "resolution.modify_terms"]
    world.person("ruth")
    world.seat("justice.appeals_judge", "ruth", depts["Justice"], "appeal.allow", "resolution.issue")
    world.seat("justice.bailiff", "dana", depts["Justice"], "fine.apply")
    User(id="eve")
    appeals = ok("court_create", "ctl", name="Agora Appeals Court", operator="Department:Justice",
                 level="appellate")["court"]
    north = ok("court_create", "ctl", name="North First Instance Court", operator="Department:Justice",
               parent=appeals)["court"]
    elena = ok("judge_appoint", "ctl", user="elena", court=north, specialization="civil")["judge"]
    ruth = ok("judge_appoint", "ctl", user="ruth", court=appeals)["judge"]
    return {"north": north, "appeals": appeals, "elena": elena, "ruth": ruth, "depts": depts}


def trial(a, claimant, respondent, title, penalties=()):
    """File, accept, assign Elena, draft and issue: returns (case, verdict)."""
    d = ok("dispute_file", claimant, respondent=respondent, title=title)["dispute"]
    case = ok("dispute_accept", "elena", dispute=d, court=a["north"])["case"]
    ok("case_assign", "ctl", case=case, judge=a["elena"])
    v = ok("verdict_draft", "elena", case=case, decision=title + ": upheld", penalties=list(penalties))["verdict"]
    ok("verdict_issue", "elena", verdict=v)
    return case, v


def test_story_bob_vs_infrastructure_and_the_appeal():
    a = agora()
    d = ok("dispute_file", "bob", respondent="Department:Infrastructure", title="Building permit refused unfairly",
           description_ref="vetkey:bob:d14")["dispute"]
    assert ok("dispute_get", "bob", dispute=d)["dispute"]["status"] == "filed"
    assert ok("dispute_get", "carla", dispute=d)["dispute"]["claimant"] == "bob"
    assert "not your dispute" in denied("dispute_get", "alice", dispute=d)
    assert [x["id"] for x in ok("dispute_list_all", "ctl", status="filed")["disputes"]] == [d]
    denied("dispute_list_all", "alice")
    denied("dispute_accept", "alice", dispute=d, court=a["north"])
    case = ok("dispute_accept", "elena", dispute=d, court=a["north"])["case"]
    denied("case_assign", "alice", case=case, judge=a["elena"])
    ok("case_assign", "ctl", case=case, judge=a["elena"])
    assert Case[case].status == "assigned" and Case[case].plaintiff == "User:bob"
    assert "only a party" in denied("evidence_submit", "alice", case=case, **PLANS)
    assert "encrypted" in denied("evidence_submit", "bob", case=case, ref="my plans in clear")
    e = ok("evidence_submit", "bob", case=case, **PLANS)["evidence"]
    denied("evidence_evaluate", "bob", case=case, evidence=e)
    assert ok("evidence_evaluate", "elena", case=case, evidence=e)["status"] == "admitted"
    denied("verdict_draft", "alice", case=case, decision="x")
    v = ok("verdict_draft", "elena", case=case, decision="Refusal annulled; issue the permit within 15 days",
           reasoning_ref="vetkey:elena:r12")["verdict"]
    denied("verdict_issue", "carla", verdict=v)
    ok("verdict_issue", "elena", verdict=v)
    assert "only a party" in denied("appeal_file", "alice", verdict=v)
    appeal = ok("appeal_file", "carla", verdict=v, grounds_ref="vetkey:infra:grounds")
    assert appeal["court"] == a["appeals"] and Case[case].status == "appealed"
    later(15 * DAY)
    assert "pending" in denied("verdict_finalize", "elena", verdict=v)
    denied("appeal_decide", "alice", appeal=appeal["appeal"], granted=False)
    assert "only a judge" in denied("appeal_decide", "elena", appeal=appeal["appeal"], granted=False)
    assert ok("appeal_decide", "ruth", appeal=appeal["appeal"], granted=False)["status"] == "denied"
    denied("verdict_finalize", "alice", verdict=v)
    ok("verdict_finalize", "elena", verdict=v)
    assert Verdict[v].status == "final" and Case[case].status == "closed"
    denied("enforcement_record", "alice", verdict=v, note="Permit issued")
    ok("enforcement_record", "ctl", verdict=v, note="Permit issued as ordered")
    found = ok("resolution_query", "ctl", text="permit", since=fake_chain.now() - 30 * DAY)["verdicts"]
    assert [f["id"] for f in found] == [v] and found[0]["issued_by"] == a["elena"]
    denied("resolution_query", "alice")


def test_story_mallorys_fine():
    a = agora()
    fine = {"target": "User:mallory", "kind": "fine", "amount": 500, "symbol": "REALM"}
    case, v = trial(a, "alice", "mallory", "Spam", [fine])
    pen = Verdict[v].penalties[0].id
    assert "not final" in denied("penalty_execute", "dana", penalty=pen)
    later(15 * DAY)
    ok("verdict_finalize", "elena", verdict=v)
    denied("penalty_execute", "mallory", penalty=pen)
    denied("penalty_execute", "alice", penalty=pen)
    inv = ok("penalty_execute", "dana", penalty=pen)["invoice"]
    i = Invoice[inv]
    assert (i.payer, i.amount, i.symbol, i.reason) == ("User:mallory", 500, "REALM", f"Penalty:{pen}")
    assert i.due_at == fake_chain.now() + 30 * DAY and Penalty[pen].status == "executed"
    assert "executed" in denied("penalty_execute", "dana", penalty=pen)


def test_penalties_suspend_and_terminate_a_contract():
    a = agora()
    k = Contract(id="C-9", title="Bike lane works", parties=["Organization:buildco"], status="active")
    case, v = trial(a, "alice", "mallory", "Spam and fraud", [
        {"target": "mallory", "kind": "suspension"}, {"target": "Contract:C-9", "kind": "termination"}])
    assert "applies to Contract" in denied("verdict_modify_terms", "elena", verdict=v,
                                           penalties=[{"target": "mallory", "kind": "termination"}])
    denied("verdict_link_contract", "alice", verdict=v, contract="C-9")
    ok("verdict_link_contract", "elena", verdict=v, contract="C-9")
    assert k.status == "disputed" and Verdict[v].contract == "C-9"
    later(15 * DAY)
    ok("verdict_finalize", "elena", verdict=v)
    suspension, termination = Verdict[v].penalties
    assert "needs access.revoke" in denied("penalty_execute", "dana", penalty=suspension.id)
    assert "needs contract.terminate" in denied("penalty_execute", "dana", penalty=termination.id)
    world.seat("justice.enforcer", "carla", a["depts"]["Justice"], "access.revoke", "contract.terminate")
    ok("penalty_execute", "carla", penalty=suspension.id)
    ok("penalty_execute", "carla", penalty=termination.id)
    assert Member["mallory"].status == "suspended" and Contract["C-9"].status == "terminated"


def test_penalty_execute_needs_the_permission_of_its_kind():
    a = agora()
    case, v = trial(a, "alice", "mallory", "Spam", [
        {"target": "mallory", "amount": 500, "symbol": "REALM"}, {"target": "mallory", "kind": "suspension"},
        {"target": "mallory", "kind": "freeze"}, {"target": "mallory", "kind": "injunction"}])
    later(15 * DAY)
    ok("verdict_finalize", "elena", verdict=v)
    fine, suspension, freeze, injunction = Verdict[v].penalties
    ok("penalty_execute", "dana", penalty=fine.id)
    for p, perm in [(suspension, "access.revoke"), (freeze, "instrument.lock"), (injunction, "enforcement.record")]:
        assert f"needs {perm}" in denied("penalty_execute", "dana", penalty=p.id)
    assert Member["mallory"].status != "suspended" and Penalty[suspension.id].status == "pending"


def test_dispute_list_all_pages():
    agora()
    filed = [ok("dispute_file", "bob", respondent="mallory", title=f"Spam {i}")["dispute"] for i in range(3)]
    ok("dispute_withdraw", "bob", dispute=filed[1])
    first = ok("dispute_list_all", "ctl", count=2)
    second = ok("dispute_list_all", "ctl", count=2, **{"from": first["next"]})
    assert [d["id"] for d in first["disputes"] + second["disputes"]] == filed and second["next"] is None
    first = ok("dispute_list_all", "ctl", status="filed", count=1)
    second = ok("dispute_list_all", "ctl", status="filed", count=1, **{"from": first["next"]})
    assert [d["id"] for d in first["disputes"] + second["disputes"]] == [filed[0], filed[2]]
    assert second["next"] is None


def test_resolution_query_pages_newest_first():
    a = agora()
    verdicts = [trial(a, "bob", "mallory", f"Spam {i}")[1] for i in range(3)]
    first = ok("resolution_query", "ctl", text="spam", count=2)
    second = ok("resolution_query", "ctl", text="spam", count=2, **{"from": first["next"]})
    assert [v["id"] for v in first["verdicts"] + second["verdicts"]] == verdicts[::-1] and second["next"] is None


def test_verdict_modify_terms_reopens_the_window():
    a = agora()
    case, v = trial(a, "alice", "mallory", "Spam", [{"target": "mallory", "amount": 500, "symbol": "REALM"}])
    later(10 * DAY)
    denied("verdict_modify_terms", "carla", verdict=v, decision="x")
    ok("verdict_modify_terms", "elena", verdict=v, penalties=[{"target": "mallory", "amount": 200, "symbol": "REALM"}])
    assert [p.amount for p in Verdict[v].penalties] == [200]
    later(5 * DAY)
    assert "can be appealed" in denied("verdict_finalize", "elena", verdict=v)


def test_dispute_withdraw_reject_and_delegation():
    a = agora()
    denied("dispute_file", "eve", respondent="mallory", title="x")
    Delegation(id="d1", grantor="alice", delegate="claude-conn", status="active",
               scope={"operations": ["dispute.create", "dispute.view"]}, expires_at=fake_chain.now() + DAY)
    d = ok("dispute_file", "claude-conn", respondent="mallory", title="Spam", on_behalf_of="alice")["dispute"]
    assert ok("dispute_get", "claude-conn", dispute=d, on_behalf_of="alice")["dispute"]["claimant"] == "alice"
    assert "not your dispute" in denied("dispute_withdraw", "bob", dispute=d)
    ok("dispute_withdraw", "claude-conn", dispute=d, on_behalf_of="alice")
    d2 = ok("dispute_file", "alice", respondent="mallory", title="Spam again")["dispute"]
    denied("dispute_reject", "alice", dispute=d2)
    denied("dispute_reject", "claude-conn", dispute=d2, on_behalf_of="alice")
    ok("dispute_reject", "elena", dispute=d2)
    assert "rejected" in denied("dispute_accept", "elena", dispute=d2, court=a["north"])


def test_enforcement_notify_and_escalate():
    a = agora()
    case, v = trial(a, "bob", "Department:Infrastructure", "Permit refused")
    denied("enforcement_notify", "alice", verdict=v, to="Department:Infrastructure")
    denied("enforcement_escalate", "alice", verdict=v, note="x")
    n = ok("enforcement_notify", "ctl", verdict=v, to="Department:Infrastructure", body="Issue the permit")
    e = ok("enforcement_escalate", "ctl", verdict=v, note="Permit still not issued after 15 days")
    assert Notification[n["notification"]].recipient == "Department:Infrastructure"
    assert Notification[e["notification"]].recipient == "Department:Justice"


def test_courts_and_judges_are_governed_realm_wide():
    a = agora()
    denied("court_create", "alice", name="South Court", operator="Department:Justice")
    denied("judge_appoint", "alice", user="alice", court=a["north"])
    denied("judge_revoke", "alice", judge=a["elena"])
    assert "already sits" in denied("judge_appoint", "ctl", user="elena", court=a["north"])
    world.policy(Department["Administration"], 2, 3)
    out = ok("court_create", "ctl", name="South First Instance Court", operator="Department:Justice")
    assert out["requires_vote"]
    world.policy(Department["Administration"], 1, 1)
    ok("judge_revoke", "ctl", judge=a["elena"])
    d = ok("dispute_file", "bob", respondent="Department:Infrastructure", title="x")["dispute"]
    case = ok("dispute_accept", "elena", dispute=d, court=a["north"])["case"]
    assert "does not sit" in denied("case_assign", "ctl", case=case, judge=a["elena"])


def test_I_JUS_1_judge_not_party():
    a = agora()
    d = ok("dispute_file", "alice", respondent="Department:Justice", title="Court fees too high")["dispute"]
    assert "party" in denied("dispute_accept", "elena", dispute=d, court=a["north"])
    case = ok("dispute_accept", "ctl", dispute=d, court=a["north"])["case"]
    assert "party" in denied("case_assign", "ctl", case=case, judge=a["elena"])
    other = ok("dispute_file", "bob", respondent="Department:Infrastructure", title="x")["dispute"]
    case2 = ok("dispute_accept", "elena", dispute=other, court=a["north"])["case"]
    ok("case_assign", "ctl", case=case2, judge=a["elena"])
    v = ok("verdict_draft", "elena", case=case2, decision="y")["verdict"]
    world.seat("infrastructure.advisor", "elena", a["depts"]["Infrastructure"])
    assert "party" in denied("verdict_issue", "elena", verdict=v)


def test_I_JUS_2_only_assigned_judges_issue():
    a = agora()
    d = ok("dispute_file", "bob", respondent="Department:Infrastructure", title="x")["dispute"]
    case = ok("dispute_accept", "elena", dispute=d, court=a["north"])["case"]
    ok("case_assign", "ctl", case=case, judge=a["elena"])
    v = ok("verdict_draft", "elena", case=case, decision="y")["verdict"]
    assert "not a judge assigned" in denied("verdict_issue", "ruth", verdict=v)
    ok("verdict_issue", "elena", verdict=v)
    assert Verdict[v].issued_by.id == a["elena"]


def test_I_JUS_3_appeal_goes_to_a_higher_court():
    a = agora()
    case, v = trial(a, "bob", "Department:Infrastructure", "Permit refused")
    assert "above" in denied("appeal_file", "carla", verdict=v, court=a["north"])
    lone = ok("court_create", "ctl", name="Island Court", operator="Department:Justice")["court"]
    ok("appeal_file", "carla", verdict=v, court=a["appeals"])
    island = ok("judge_appoint", "ctl", user="elena", court=lone)["judge"]
    case2 = ok("dispute_accept", "elena", court=lone,
               dispute=ok("dispute_file", "bob", respondent="mallory", title="z")["dispute"])["case"]
    ok("case_assign", "ctl", case=case2, judge=island)
    v2 = ok("verdict_draft", "elena", case=case2, decision="z")["verdict"]
    ok("verdict_issue", "elena", verdict=v2)
    assert "above" in denied("appeal_file", "mallory", verdict=v2)


def test_I_JUS_3_granted_appeal_replaces_the_verdict():
    a = agora()
    case, v = trial(a, "bob", "Department:Infrastructure", "Permit refused")
    ap = ok("appeal_file", "carla", verdict=v)["appeal"]
    assert "new decision" in denied("appeal_decide", "ruth", appeal=ap, granted=True)
    new = ok("appeal_decide", "ruth", appeal=ap, granted=True, decision="Refusal stands")["verdict"]
    assert Verdict[v].status == "overturned" and Verdict[new].issued_by.id == a["ruth"]
    assert Case[case].status == "verdict_issued"


def test_I_JUS_4_penalty_waits_for_final_verdict():
    a = agora()
    case, v = trial(a, "alice", "mallory", "Spam", [{"target": "mallory", "amount": 500, "symbol": "REALM"}])
    pen = Verdict[v].penalties[0].id
    ok("appeal_file", "mallory", verdict=v)
    later(15 * DAY)
    assert "pending" in denied("verdict_finalize", "elena", verdict=v)
    assert "not final" in denied("penalty_execute", "dana", penalty=pen)
    assert Invoice.count() == 0


def test_I_JUS_5_private_court_needs_a_court_licence():
    agora()
    Organization(id="arbitra", admins=["dana"])
    assert "licence" in denied("court_create", "ctl", name="Arbitra", operator="Organization:arbitra")
    lic = License(id="LIC-7", kind="court", holder="Organization:arbitra", status="active",
                  expires_at=fake_chain.now() + 365 * DAY)
    court = ok("court_create", "ctl", name="Arbitra", operator="Organization:arbitra")["court"]
    ok("judge_appoint", "ctl", user="elena", court=court)
    d = ok("dispute_file", "bob", respondent="mallory", title="Unpaid invoice")["dispute"]
    lic.status = "revoked"
    assert "no active court licence" in denied("dispute_accept", "elena", dispute=d, court=court)
