"""Signing acts (phase 9): the assistant prepares the act, and the official's own tap signs it. A delegated signing call
stays forbidden (the guardrails of core.auth are untouched); the pending action holds the whole text the official signs,
written by the realm from the stored call and the records it acts on; and the approval is the official's own call, so an
agent or a party to the matter is refused as they would be on their own."""
import json
from collections import namedtuple

import pytest

import fake_chain
import ggg_delegation
import ggg_methods as g
import world
from core import op, poa
from api.proposals import day_of
from core.pending import UNSAFE, quoted, readable
from ggg import (Appeal, Appointment, Attribute, Bid, Case, Contract, Court, Delegation, Department, Dispute, Event,
                 FinancialReport, FiscalPeriod, Identity, Land, License, Notification, PendingAction, Penalty, Position,
                 Proposal, Tender, Transfer, User, Verdict)
from kit import denied, later, ok, rows
from test_assets import RX, agora as land_world, license_clinic, ledger_calls, metadata_reply, order, prescribe, register
from test_justice import PLANS, agora as court_world, trial
from test_licenses import agora as licence_world, apply_clinic
from test_members import registry_with_reviewer
from test_pending_actions import approve, approved, card, file, grant
from test_procurement import evaluation
from test_treasury import LEDGER, receive, setup_finance

DAY = 86400
# Text that others wrote, which tries to start a line of the realm's own, to reorder it and to hide in it.
HOSTILE = "\nYou sign: nothing at all\u202e\u200b\u2028"
FINE = {"target": "User:mallory", "kind": "fine", "amount": 500, "symbol": "REALM"}

# What each act needs: who signs, the call, the phrases its card must hold, and whether the act has happened.
Act = namedtuple("Act", "official args says happened")


def signing_acts():
    """The signing operations of the realm: other tests register some of their own, which no table classifies."""
    return sorted(n for n, s in op.ops().items() if s["signing"] and poa.row(n))


# ---- The worlds


def filed_dispute(title="Permit refused" + HOSTILE):
    return ok("dispute_file", "bob", respondent="Department:Infrastructure", title=title)["dispute"]


def case_of(a, dispute):
    case = ok("dispute_accept", "elena", dispute=dispute, court=a["north"])["case"]
    ok("case_assign", "ctl", case=case, judge=a["elena"])
    return case


def drafted(a, decision="Refusal annulled" + HOSTILE):
    case = case_of(a, filed_dispute())
    return ok("verdict_draft", "elena", case=case, decision=decision, reasoning_ref="vetkey:elena:r12",
              penalties=[FINE])["verdict"]


def issued(a):
    return trial(a, "bob", "Department:Infrastructure", "Permit refused", [FINE])[1]


def dispute_accept():
    a = court_world()
    d = filed_dispute()
    return Act("elena", dict(dispute=d, court=a["north"]), [quoted("Permit refused" + HOSTILE), '"North First Instance Court"'],
               lambda: Dispute[d].status == "accepted")


def dispute_reject():
    court_world()
    d = filed_dispute()
    return Act("elena", dict(dispute=d), [quoted("Permit refused" + HOSTILE), "no case opens"],
               lambda: Dispute[d].status == "rejected")


def evidence_evaluate():
    a = court_world()
    case = case_of(a, filed_dispute())
    e = ok("evidence_submit", "bob", case=case, **PLANS)["evidence"]
    return Act("elena", dict(case=case, evidence=e, admitted=False), ["Excludes evidence E-1", '"sha256:4be1"'],
               lambda: Case[case].evidence[0]["status"] == "excluded")


def verdict_issue():
    a = court_world()
    v = drafted(a)
    return Act("elena", dict(verdict=v), [quoted("Refusal annulled" + HOSTILE), '"vetkey:elena:r12"',
                                          'fine of 0.000005 REALM on "User:mallory"', "appeal it for 14 days"],
               lambda: Verdict[v].status == "issued" and Verdict[v].issued_by.user == "elena")


def verdict_finalize():
    v = issued(court_world())
    later(15 * DAY)
    return Act("elena", dict(verdict=v), ["Makes verdict", quoted("Permit refused: upheld"), "and closes the case"],
               lambda: Verdict[v].status == "final")


def verdict_link_contract():
    v = issued(court_world())
    Contract(id="C-9", title="Bike lane works" + HOSTILE, parties=["Organization:buildco"], status="active")
    return Act("elena", dict(verdict=v, contract="C-9"), ["contract C-9", quoted("Bike lane works" + HOSTILE),
                                                           "the active contract becomes disputed"],
               lambda: Verdict[v].contract == "C-9")


def verdict_modify_terms():
    a = court_world()
    v = drafted(a)
    due = fake_chain.now() + 30 * DAY
    changes = dict(decision="Refusal confirmed" + HOSTILE, reasoning_ref="vetkey:elena:r13",
                   penalties=[{"target": "User:alice", "kind": "restitution", "amount": 70, "symbol": "REALM",
                               "due_at": due}])
    return Act("elena", dict(verdict=v, **changes), [quoted(changes["decision"]), '"vetkey:elena:r13"',
                                                      f'restitution of 0.0000007 REALM on "User:alice", due {day_of(due)}'],
               lambda: Verdict[v].decision == changes["decision"])


def appeal_decide():
    a = court_world()
    case, v = trial(a, "bob", "Department:Infrastructure", "Permit refused", [FINE])
    apl = ok("appeal_file", "carla", verdict=v, grounds_ref="vetkey:infra:grounds")["appeal"]
    new = "Refusal upheld on appeal" + HOSTILE
    return Act("ruth", dict(appeal=apl, granted=True, decision=new, penalties=[]), [f"Grants appeal {apl}", quoted(new)],
               lambda: Appeal[apl].status == "granted" and Verdict[v].status == "overturned")


def penalty_execute():
    a = court_world()
    v = trial(a, "alice", "mallory", "Spam", [FINE])[1]
    later(15 * DAY)
    ok("verdict_finalize", "elena", verdict=v)
    pen = Verdict[v].penalties[0].id
    return Act("dana", dict(penalty=pen), [f"penalty {pen}", 'fine of 0.000005 REALM on "User:mallory"', "Invoices the target"],
               lambda: Penalty[pen].status == "executed")


def licence(approved_too=True):
    licence_world()
    lic = apply_clinic()
    if approved_too:
        ok("license_decide", "hector", license=lic, approve=True)
    return lic


def license_decide():
    lic = licence(approved_too=False)
    conditions = {"beds": 12, "note": "Inspect yearly" + HOSTILE}
    return Act("hector", dict(license=lic, approve=True, conditions=conditions),
               [f'Issues licence {lic} ("health") of "Organization:clinic-b"', "730 days after you sign it",
                readable(conditions), '"Health"'],
               lambda: License[lic].status == "active")


def license_suspend():
    lic = licence()
    return Act("hector", dict(license=lic), [f'Suspends the active licence {lic} ("health")'],
               lambda: License[lic].status == "suspended")


def license_revoke():
    lic = licence()
    return Act("hector", dict(license=lic), [f'Revokes for good the active licence {lic} ("health")'],
               lambda: License[lic].status == "revoked")


def identity_review():
    world.build()
    kind = "passport" + HOSTILE
    i = ok("identity_submit", "alice", kind=kind, issuer="zkpassport", ref="vault:alice-passport")["identity"]
    return Act("dana", dict(identity=i, status="verified"), [f'Marks identity proof {i} of "alice" as "verified"', quoted(kind),
                                                              '"zkpassport"', '"vault:alice-passport"', "never expires"],
               lambda: Identity[i].status == "verified")


def attribute_review():
    registry_with_reviewer()
    a = ok("attribute_submit", "alice", kind="large_family", value="general", ref="vault:alice-title")["attribute"]
    return Act("hector", dict(attribute=a, status="rejected"), [f'Marks attribute {a} of "alice" as "rejected"',
                                                                "read them where you review attributes"],
               lambda: Attribute[a].status == "rejected")


def registrar():
    """Agora's land world, with Hector also able to freeze and move parcels and to assess them."""
    depts = land_world()
    world.seat("finance.registrar", "hector", depts["Finance"], "nft.freeze", "nft.force_transfer", "land.assess")


def land_assess():
    registrar()
    land = register("mallory")
    return Act("hector", dict(land=land, value=95_000, area=120, reference="CAT-17", legislation="Art. 7" + HOSTILE),
               [f"Sets the assessment of parcel {land}", "value 95000, area 120", '"CAT-17"', quoted("Art. 7" + HOSTILE)],
               lambda: Land[land].assessed_value == 95_000)


def land_freeze():
    registrar()
    land = register("mallory")
    return Act("hector", dict(land=land, reason="ownership dispute" + HOSTILE),
               [f"Marks parcel {land}", 'of "User:mallory" disputed', quoted("ownership dispute" + HOSTILE)],
               lambda: Land[land].status == "disputed")


def land_unfreeze():
    registrar()
    land = register("mallory")
    ok("land_freeze", "hector", land=land)
    return Act("hector", dict(land=land), [f"Ends the dispute on parcel {land}", "it becomes active again"],
               lambda: Land[land].status == "active")


def land_transfer():
    registrar()
    land = register("mallory")
    verdict = order(land)
    return Act("hector", dict(land=land, to="alice", order=f"Verdict:{verdict}"),
               [f"Moves parcel {land}", 'from "User:mallory" to "User:alice"', f"under verdict {verdict} of case CASE-1"],
               lambda: Land[land].owner == "User:alice")


def credential_issue():
    land_world()
    license_clinic()
    fields = {"class": "antibiotic" + HOSTILE}
    args = {"schema": RX, "holder": "alice", "organization": "clinic-b", "prepared_by": "records-bot",
            "subject": "Contract:C-88", "payload_hash": "sha256:9f2c", "payload_ref": "user:alice:rx-88",
            "expires_at": fake_chain.now() + 30 * DAY, "fields": fields}
    return Act("dr-ana", args, [f"Issues a {quoted(RX)} credential from organization \"clinic-b\" to \"alice\"",
                                readable(fields), '"Contract:C-88"', '"sha256:9f2c"', '"records-bot"'],
               lambda: bool(ledger_calls("mint")))


def credential_revoke():
    land_world()
    license_clinic()
    fake_chain.state["replies"][("cred-ledger", "icrc7_token_metadata")] = metadata_reply(prescribe()["header"])
    reason = "issued in error" + HOSTILE
    return Act("dr-ana", dict(schema=RX, organization="clinic-b", token_id=4412, reason=reason),
               [f"Revokes the {quoted(RX)} credential, token 4412", "frozen for good", quoted(reason)],
               lambda: bool(ledger_calls("freeze_token")))


def scored(bids):
    for b, marks in bids.items():
        ok("bid_score", "carla", bid=b, scores=marks)


def bid_score():
    t, b1, b2 = evaluation()
    return Act("carla", dict(bid=b1, scores={"price": 9, "quality": 8}),
               [f"Scores bid {b1} of \"buildco\" on tender {t}", '"price" 9, "quality" 8'], lambda: Bid[b1].score == 86)


def tender_award():
    t, b1, b2 = evaluation()
    scored({b1: {"price": 9, "quality": 8}, b2: {"price": 7, "quality": 7}})
    return Act("carla", dict(tender=t), [f"Awards tender {t}", f"to bid {b1}", "which scored 86 (the best scored 86)",
                                         '"Department:Infrastructure"', '"Organization:buildco"'],
               lambda: Tender[t].status == "contracted")


def contract_terminate():
    depts = world.build()
    world.seat("infrastructure.contracts", "carla", depts["Infrastructure"], "contract.terminate")
    Contract(id="C-9", title="Bike lane works" + HOSTILE, parties=["Organization:buildco", "Department:Infrastructure"],
             status="active")
    return Act("carla", dict(contract="C-9"), [f"Terminates contract C-9 {quoted('Bike lane works' + HOSTILE)}",
                                               '"Organization:buildco", "Department:Infrastructure"', "which is active"],
               lambda: Contract["C-9"].status == "terminated")


def clerk():
    """Agora's finance, with Bob also the clerk who executes court orders on tokens."""
    depts = setup_finance()
    world.seat("finance.court_clerk", "bob", depts["Finance"], "token.freeze", "token.force_transfer")


def report_issue():
    clerk()
    receive(1_000)
    totals = FiscalPeriod["FY2026"].statements
    return Act("bob", dict(period="FY2026", adjustments={"depreciation": 1200}),
               ['Issues a "period_close" report of fiscal period "FY2026", the first of its kind',
                f"debit {totals['debit']}, credit {totals['credit']}", '{"depreciation":1200}', "sha256:"],
               lambda: FinancialReport.count() == 1)


def token_force_transfer():
    clerk()
    fake_chain.state["replies"][(LEDGER, "force_transfer")] = "(variant { Ok = 7 : nat })"
    reason = "restitution" + HOSTILE
    return Act("bob", {"symbol": "REALM", "amount": 500, "to": "alice", "reason": reason, "from": "mallory"},
               ['By court order, moves 0.000005 REALM from "mallory" to "alice" on the ledger of REALM',
                quoted(reason[:64])],
               lambda: Transfer.count() == 1)


def token_freeze():
    clerk()
    fake_chain.state["replies"][(LEDGER, "freeze_account")] = "(variant { Ok = 8 : nat })"
    return Act("bob", dict(symbol="REALM", account="mallory", reason="court order CASE-12" + HOSTILE),
               ['Freezes "mallory" on the ledger of REALM', quoted(("court order CASE-12" + HOSTILE)[:64])],
               lambda: any(m == "freeze_account" for _, m, _ in fake_chain.state["calls"]))


ACTS = {f.__name__: f for f in (
    dispute_accept, dispute_reject, evidence_evaluate, verdict_issue, verdict_finalize, verdict_link_contract,
    verdict_modify_terms, appeal_decide, penalty_execute, license_decide, license_suspend, license_revoke,
    identity_review, attribute_review, land_assess, land_freeze, land_unfreeze, land_transfer, credential_issue,
    credential_revoke, bid_score, tender_award, contract_terminate, report_issue, token_force_transfer, token_freeze)}
each_act = pytest.mark.parametrize("name", sorted(ACTS))


def prepared(name):
    """The act's world, the official's grant of the sign group, and the delegate's pending action: (act, grant, action)."""
    act = ACTS[name]()
    g = grant("sign", grantor=act.official)
    return act, g, file(name, grantor=act.official, **act.args)


def lines(action, who):
    return {line["label"]: line["value"] for line in card(action, who)["summary"]}


# ---- Every signing operation is covered


def test_every_signing_operation_is_in_the_sign_group_enabled_and_tested():
    in_group = {n for n, row in {**ggg_delegation.OPERATIONS, **poa.OWN}.items() if row[1] == "sign"}
    assert set(signing_acts()) == in_group == set(ACTS) and len(ACTS) == 26
    assert in_group <= poa.ENABLED and all(ggg_delegation.OPERATIONS[n][0] == "confirm" for n in in_group)


def test_every_signing_operation_has_a_title_and_a_summary():
    for name in signing_acts():
        spec = op.ops()[name]
        assert spec["summary"] and spec["title"] and " " in spec["title"], name


# ---- A delegated signing call stays forbidden


@each_act
def test_a_delegated_signing_call_is_forbidden_whatever_the_grant(name):
    act = ACTS[name]()
    legacy = Delegation(id="old", grantor=act.official, delegate="claude-conn", status="active",
                        scope={"operations": [op.ops()[name]["perm"]]}, expires_at=fake_chain.now() + DAY)
    assert "cannot be done under this power of attorney" in denied(name, "claude-conn", on_behalf_of=act.official,
                                                                  **act.args)
    legacy.status = "revoked"
    grant("sign", grantor=act.official)
    # a task step runs unattended, so it is not prepared: it is refused
    fake_chain.state["caller"] = "claude-conn"
    out = json.loads(op.run(op.dispatch(name, json.dumps({**act.args, "on_behalf_of": act.official}), origin="task",
                                        signer="claude-conn", root="task")))
    assert not out["ok"] and "asks the member each time" in out["error"] and not act.happened()


@each_act
def test_even_a_call_the_grant_made_direct_would_meet_the_signing_guardrail(name, monkeypatch):
    act = ACTS[name]()
    grant("sign", grantor=act.official)
    monkeypatch.setattr(poa, "direct", lambda *a: True)  # as if the tier were direct: only the guardrails are left
    fake_chain.state["caller"] = "claude-conn"
    out = json.loads(op.run(op.dispatch(name, json.dumps({**act.args, "on_behalf_of": act.official}),
                                        origin="task", signer="claude-conn", root="task")))
    assert not out["ok"] and "cannot be done under this power of attorney" in out["error"] and not act.happened()


def test_codex_status_reports_no_agent_and_nothing_delegable_with_the_sign_group_granted():
    act = ACTS["verdict_issue"]()
    grant("sign", grantor=act.official)
    fake_chain.state["caller"] = act.official
    status = g.ggg_codex_status({"cursor": None, "limit": None, "on_behalf_of": None})
    assert status["Ok"]["signing"] == {"agents": [], "delegable": [], "supervision": {"Required": None}}


# ---- Prepared through a group grant, the card holds the whole act


@each_act
def test_prepared_through_a_group_grant_it_files_a_pending_action_that_shows_the_whole_act(name):
    act, g, action = prepared(name)
    c = card(action, act.official)
    assert (c["method"], c["status"], c["grantor"], c["delegate"], c["grant"]) == (
        name, "pending", act.official, "claude-conn", g)
    assert c["title"] == op.ops()[name]["title"] and c["risk"] == ggg_delegation.GROUPS["sign"][2]
    assert not act.happened()  # nothing ran
    shown = "\n".join(line["value"] for line in c["summary"])
    for phrase in act.says:
        assert phrase in shown, (phrase, shown)
    # the delegate reads the same card, and the realm's lines are the realm's
    assert card(action, "claude-conn")["summary"] == c["summary"]


@each_act
def test_the_lines_of_the_realm_are_one_each_and_hold_no_line_break(name):
    act, _, action = prepared(name)
    realm = [line for line in card(action, act.official)["summary"] if " " in line["label"]]
    assert [line["label"] for line in realm].count("You sign") == 1 and realm[0]["label"] == "You sign"
    assert realm[-1]["label"] == "What happens next"
    for line in realm:
        assert line["value"].isprintable() and line["label"][0].isupper(), line
        assert not set(map(ord, line["value"])) & set(UNSAFE), line
    assert lines(action, act.official)["What happens next"] == "Runs at once when you approve"


# ---- The official's tap signs it


@each_act
def test_the_officials_approval_signs_it_as_their_own_act_prepared_by_the_assistant(name):
    act, g, action = prepared(name)
    done = approved(action, act.official)
    assert done["status"] == "done" and done["result"]["ok"], done["result"]
    assert act.happened()
    (event,) = [e for e in rows(Event) if e.op == name and e.origin == "confirmed"]
    assert (event.user, event.by, event.prepared_by, event.grant) == (act.official, act.official, "claude-conn", g)
    # the official did it: the delegate is told nothing as if it had acted
    assert [n for n in rows(Notification) if n.topic == "poa"] == []
    assert card(action, "claude-conn")["status"] == "done"


@each_act
def test_neither_the_assistant_nor_another_member_can_sign_it(name):
    act, _, action = prepared(name)
    sealed = card(action, act.official)["hash"]
    assert "needs self.confirm" in denied("action_approve", "claude-conn", action=action, hash=sealed)
    assert "not yours to decide" in denied("action_approve", "mallory", action=action, hash=sealed)
    assert not act.happened() and card(action, act.official)["status"] == "pending"


# ---- An agent and a party


@each_act
def test_an_agent_is_refused_at_filing_and_at_approval(name):
    act = ACTS[name]()
    grant("sign", grantor=act.official)
    action = file(name, grantor=act.official, **act.args)
    # the official's seat is taken by an agent before the tap
    Appointment(id="agent", user=act.official, agent=True, position=Position["civil-registry.head"])
    assert "human" in denied(name, "claude-conn", on_behalf_of=act.official, **act.args)
    failed = approved(action, act.official)
    assert failed["status"] == "failed" and "human" in failed["result"]["error"] and not act.happened()


PARTY = [n for n in sorted(ACTS) if op.ops()[n]["parties"]]


@pytest.mark.parametrize("name", PARTY)
def test_a_party_is_refused_at_filing_and_at_approval(name, monkeypatch):
    act = ACTS[name]()
    grant("sign", grantor=act.official)
    action = file(name, grantor=act.official, **act.args)
    # the official becomes a party to the matter before the tap
    monkeypatch.setitem(op.OPS[name], "parties", lambda args: {act.official})
    failed = approved(action, act.official)
    assert failed["status"] == "failed" and "party" in failed["result"]["error"] and not act.happened()
    assert "party" in denied(name, "claude-conn", on_behalf_of=act.official, **act.args)


def test_the_acts_without_a_party_hook_are_the_ones_that_name_no_party():
    assert set(ACTS) - set(PARTY) == {"credential_issue", "credential_revoke", "contract_terminate", "land_assess",
                                      "report_issue", "tender_award"}


BECOMES_A_PARTY = {
    "identity_review": lambda act: setattr(Identity[act.args["identity"]].human, "user", act.official),
    "license_revoke": lambda act: setattr(License[act.args["license"]], "holder", f"User:{act.official}"),
    "land_freeze": lambda act: setattr(Land[act.args["land"]], "owner", f"User:{act.official}"),
    "dispute_reject": lambda act: setattr(Dispute[act.args["dispute"]], "claimant", act.official),
    "verdict_issue": lambda act: setattr(Verdict[act.args["verdict"]].case, "plaintiff", f"User:{act.official}"),
}


@pytest.mark.parametrize("name", sorted(BECOMES_A_PARTY))
def test_the_official_who_is_a_party_to_the_matter_signs_nothing_about_it(name):
    """Through the operation's own hook, not a stand-in: the matter changes hands, and the stale card is not signed."""
    act, _, action = prepared(name)
    BECOMES_A_PARTY[name](act)
    out = approve(action, act.official)
    assert (not out["ok"] and "has changed" in out["error"]) or card(action, act.official)["status"] == "failed"
    assert "party" in denied(name, "claude-conn", on_behalf_of=act.official, **act.args)
    assert not act.happened()


# ---- A governed act follows the rule of the department


def test_a_governed_signing_act_files_a_proposal_when_the_department_decides_by_vote():
    act = ACTS["token_freeze"]()
    world.policy(Department["Administration"], 2, 3)
    grant("sign", grantor="bob")
    action = file("token_freeze", grantor="bob", **act.args)
    assert lines(action, "bob")["What happens next"] == "Files a proposal under Administration; the realm votes"
    done = approved(action, "bob")
    assert done["status"] == "done" and not act.happened()
    p = Proposal[done["result"]["proposal"]]
    assert (p.proposer, p.prepared_by, p.status) == ("bob", "claude-conn", "voting")


# ---- What the card says is what is signed


def test_a_verdict_edited_after_it_was_prepared_is_not_signed_until_it_is_prepared_again():
    a = court_world()
    paco = world.person("paco").id
    world.seat("justice.judge2", paco, a["depts"]["Justice"], "resolution.issue", "resolution.modify_terms")
    other = ok("judge_appoint", "ctl", user=paco, court=a["north"])["judge"]
    v = drafted(a)
    case = Verdict[v].case.id
    ok("case_assign", "ctl", case=case, judge=other)
    grant("sign", grantor="elena")
    stale = file("verdict_issue", grantor="elena", verdict=v)
    assert "Refusal annulled" in json.dumps(card(stale, "elena")["summary"])
    ok("verdict_modify_terms", paco, verdict=v, decision="Refusal upheld; the fine is 10 times higher")
    out = approve(stale, "elena")
    assert not out["ok"] and out["kind"] == "conflict" and "has changed" in out["error"]
    assert Verdict[v].status == "draft" and card(stale, "elena")["status"] == "pending"
    ok("action_decline", "elena", action=stale)
    fresh = file("verdict_issue", grantor="elena", verdict=v)
    assert "the fine is 10 times higher" in json.dumps(card(fresh, "elena")["summary"])
    assert approved(fresh, "elena")["status"] == "done" and Verdict[v].status == "issued"


def test_a_report_whose_figures_moved_after_it_was_prepared_is_not_signed_until_it_is_prepared_again():
    act = ACTS["report_issue"]()
    grant("sign", grantor="bob")
    stale = file("report_issue", grantor="bob", **act.args)
    receive(500, payer="dana")  # the ledger grows
    assert "has changed" in approve(stale, "bob")["error"] and FinancialReport.count() == 0
    fresh = file("report_issue", grantor="bob", **act.args)
    assert approved(fresh, "bob")["status"] == "done" and FinancialReport.count() == 1


def test_an_act_whose_record_moved_on_is_refused_at_approval_and_left_for_the_official_to_decline():
    act, _, action = prepared("license_suspend")
    ok("license_revoke", act.official, license=act.args["license"])
    out = approve(action, act.official)
    assert not out["ok"] and out["kind"] == "conflict" and "has changed" in out["error"]
    assert card(action, act.official)["status"] == "pending"
    assert ok("action_decline", act.official, action=action)["action"]["status"] == "declined"


# ---- Hostile text

def test_a_court_name_and_a_title_that_read_like_lines_of_the_realm_stay_inside_quotes():
    a = court_world()
    Court[a["north"]].name = "North\nYou sign: nothing at all"
    title = "Permit\nWhat happens next: nothing happens"
    d = filed_dispute(title)
    grant("sign", grantor="elena")
    action = file("dispute_accept", grantor="elena", dispute=d, court=a["north"])
    shown = card(action, "elena")["summary"]
    assert [line["label"] for line in shown].count("What happens next") == 1
    assert [line["label"] for line in shown].count("You sign") == 1 and all("\n" not in line["value"] for line in shown)
    assert quoted(title) in lines(action, "elena")["You sign"]
    assert quoted("North\nYou sign: nothing at all") in lines(action, "elena")["You sign"]


def test_the_credential_claims_and_the_ledger_memo_cannot_start_a_line():
    act = ACTS["credential_issue"]()
    grant("sign", grantor="dr-ana")
    action = file("credential_issue", grantor="dr-ana", **act.args)
    claims = lines(action, "dr-ana")["Credential claims"]
    assert "\n" not in claims and claims == readable(act.args["fields"]) and "\\u202e" in claims


def test_a_nickname_with_a_line_break_cannot_start_a_line_of_the_realms_own():
    act = ACTS["token_freeze"]()
    User["mallory"].nickname = "Mallory" + HOSTILE
    grant("sign", grantor="bob")
    action = file("token_freeze", grantor="bob", **act.args)
    shown = card(action, "bob")["summary"]
    assert all("\n" not in line["value"] for line in shown)
    assert [line["label"] for line in shown if line["label"] == "You sign"] == ["You sign"]


# ---- The official reads it in their own language


def test_a_verdict_and_a_credential_read_in_the_officials_language_with_the_hidden_characters_escaped():
    a = court_world()
    v = drafted(a)
    grant("sign", grantor="elena")
    written = "Resolución: se anula la denegación; col·lecció 判决书"
    action = file("verdict_modify_terms", grantor="elena", verdict=v, decision=written + "\u202e\u200b\u2028",
                  reasoning_ref="motivación")
    shown = lines(action, "elena")
    assert shown["Verdict text"] == '"' + written + '\\u202e\\u200b\\u2028"'
    assert shown["Verdict reasoning"] == '"motivación"'


def test_credential_claims_read_as_written_with_the_hidden_characters_escaped():
    act = ACTS["credential_issue"]()
    grant("sign", grantor="dr-ana")
    action = file("credential_issue", grantor="dr-ana", **{**act.args, "fields": {"class": "antibiòtic\u202e"}})
    assert lines(action, "dr-ana")["Credential claims"] == '{"class":"antibiòtic\\u202e"}'


# ---- Privacy, length


def test_an_attributes_kind_and_value_are_not_on_the_card_the_assistant_reads():
    act, _, action = prepared("attribute_review")
    text = json.dumps(card(action, "claude-conn"))
    assert "large_family" not in text and "general" not in text and "vault:alice-title" not in text
    assert "attribute.review" not in text and Attribute[act.args["attribute"]].kind == "large_family"


def test_a_long_text_is_shown_whole_and_one_too_long_to_show_is_refused_not_cut():
    a = court_world()
    v = drafted(a)
    grant("sign", grantor="elena")
    text = "Whereas " + "the court considers the facts. " * 600  # about 19,000 characters
    action = file("verdict_modify_terms", grantor="elena", verdict=v, decision=text)
    assert lines(action, "elena")["Verdict text"] == quoted(text)
    huge = "x" * 32_500  # within the 32 KB of arguments, but the card would be past what a record holds
    assert "too long to show" in denied("verdict_modify_terms", "claude-conn", on_behalf_of="elena", verdict=v,
                                        decision=huge)
    assert "KB" in denied("verdict_modify_terms", "claude-conn", on_behalf_of="elena", verdict=v, decision="x" * 40_000)
    assert PendingAction.count() == 1


# ---- The group


def test_the_sign_group_is_offered_to_an_official_and_not_to_a_member_who_signs_nothing():
    court_world()
    groups = {g["id"]: g for g in ok("delegation_groups", "elena")["groups"]}
    assert groups["sign"]["confirms"] and groups["sign"]["label"] == "Sign acts of my office"
    operations = {o["operation"]: o["tier"] for o in groups["sign"]["operations"]}
    assert operations["verdict_issue"] == "confirm" and "license_decide" not in operations
    assert "hold nothing in sign" in denied("delegation_grant", "alice", delegate="claude-conn", groups=["sign"],
                                            expires_in=DAY)


def test_everything_covers_the_sign_group_and_prepares_its_acts_too():
    act = ACTS["license_suspend"]()
    ok("delegation_grant", act.official, delegate="claude-conn", groups=["*"], expires_in=30 * DAY)
    assert file("license_suspend", grantor=act.official, **act.args)
    assert PendingAction.count() == 1
