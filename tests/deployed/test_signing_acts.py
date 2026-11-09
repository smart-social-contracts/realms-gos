"""A signing act on the real wasm (realms-gos#647, phase 9): a judge's assistant prepares the issue of a verdict through
GGG's ``ggg_call_for``, the card holds the verdict's text, and the judge's own ``ggg_action_approve`` issues it, as the
judge's act prepared by the assistant. The assistant can neither sign it nor have it signed on a card that no longer
says what the verdict says.
"""

import json

import pytest
from ic.candid import encode
from test_delegation_groups import launch, ok, refused
from test_pending_actions import ACTION, APPROVE, CALL_FOR, CALL_FOR_REPLY, TEXT

DAY = 86400
JUDGE = ("dispute.accept", "resolution.draft", "resolution.issue", "resolution.modify_terms")
DECISION = "Refusal annulled; issue the permit within 15 days"

# What the filing of the act and the judge's approval cost in the canister, measured by the controller's shell. The
# approval writes the card's lines again to compare them with the ones the judge read.
FILING = """
from core.op import call
out = call("verdict_issue", {{"verdict": "{verdict}", "on_behalf_of": "{judge}"}}, user="{claude}")
assert out["ok"] and out["pending"], out
"""
APPROVAL = """
from core.op import call
action = call("action_get", {{"action": "{action}"}}, user="{judge}")["action"]
out = call("action_approve", {{"action": "{action}", "hash": action["hash"]}}, user="{judge}")
assert out["ok"] and out["action"]["status"] == "done", out
"""
# A task's step runs unattended, so the delegate's call is not prepared: it meets the guardrails.
TASK_CALL = """
from core.op import call
out = call("verdict_issue", {{"verdict": "{verdict}"}}, user="{judge}", by="{claude}", origin="task", root="task")
print(out["ok"], out.get("error"))
"""
VERDICT_STATUS = "from ggg import Verdict\nprint(Verdict['{verdict}'].status)"
BUDGET = 300_000_000


@pytest.fixture(scope="module")
def realm(wasm_of, people):
    """A launched realm with a court, and Alice its judge."""
    from pic import Canister

    realm = Canister(wasm_of("production"), installer=people["installer"])
    launch(realm, people)
    ctl, alice = people["ctl"], people["alice"]
    ok(realm, "profile_create", ctl, profile="judge", allowed_to=list(JUDGE))
    ok(realm, "role_assign", ctl, user=alice, profile="judge")
    realm.court = ok(realm, "court_create", ctl, name="North Court", operator="Department:Administration")["court"]
    realm.judge = ok(realm, "judge_appoint", ctl, user=alice, court=realm.court)["judge"]
    ok(realm, "delegation_grant", alice, delegate=people["claude-conn"], groups=["sign"], expires_in=30 * DAY)
    return realm


def drafted(realm, people, decision=DECISION):
    """Bob's dispute against the founder, accepted by Alice, with a draft verdict of hers: its id."""
    ctl, alice = people["ctl"], people["alice"]
    dispute = ok(realm, "dispute_file", people["bob"], respondent=f"User:{people['founder']}", title="Permit refused")
    case = ok(realm, "dispute_accept", alice, dispute=dispute["dispute"], court=realm.court)["case"]
    ok(realm, "case_assign", ctl, case=case, judge=realm.judge)
    return ok(realm, "verdict_draft", alice, case=case, decision=decision)["verdict"]


def prepare(realm, people, verdict):
    call = {"method": "ggg_verdict_issue", "arg": encode([{"type": TEXT, "value": verdict}]),
            "on_behalf_of": people["alice"], "note": ["for the hearing"]}
    out = realm.ggg("ggg_call_for", CALL_FOR, call, CALL_FOR_REPLY, people["claude-conn"])
    assert "Ok" in out and "Pending" in out["Ok"], out
    return out["Ok"]["Pending"]


def tap(realm, people, action, who="alice"):
    arg = {"action": action["id"], "hash": action["hash"], "on_behalf_of": []}
    return realm.ggg("ggg_action_approve", APPROVE, arg, ACTION, people[who])


def status(realm, verdict):
    return realm.shell(VERDICT_STATUS.format(verdict=verdict)).strip()


def test_a_verdict_prepared_through_ggg_call_for_is_issued_by_the_judges_own_tap(realm, people, costs):
    alice, claude = people["alice"], people["claude-conn"]
    verdict = drafted(realm, people)
    action = prepare(realm, people, verdict)
    assert next(iter(action["status"])) == "Pending" and status(realm, verdict) == "draft"

    # the card holds the text the judge signs, quoted
    lines = {line["label"]: line["value"] for line in action["summary"]}
    assert lines["Verdict text"] == json.dumps(DECISION) and "Issues verdict" in lines["You sign"]
    assert lines["What happens next"] == "Runs at once when you approve"

    # the assistant cannot tap, and nobody else can; nothing is signed
    assert "Err" in tap(realm, people, action, "claude-conn") and "Err" in tap(realm, people, action, "bob")
    assert status(realm, verdict) == "draft"

    done = tap(realm, people, action)["Ok"]
    assert next(iter(done["status"])) == "Done" and status(realm, verdict) == "issued"
    signed = realm.shell(f"from ggg import Verdict\nprint(Verdict['{verdict}'].issued_by.user)").strip()
    assert signed == alice
    events = ok(realm, "events", people["ctl"], query=True, count=40)["events"]
    approval = [e for e in events if e["op"] == "verdict_issue" and e["origin"] == "confirmed"][0]
    assert (approval["user"], approval["by"], approval["prepared_by"]) == (alice, alice, claude)

    # the instructions of a filing and of an approval, on a verdict of its own
    second = drafted(realm, people)
    costs.measure(realm, "signing act: a delegate files a verdict's issue",
                  FILING.format(verdict=second, judge=alice, claude=claude))
    filed = ok(realm, "action_list", alice, query=True, status="pending")["actions"][0]["id"]
    spent = costs.measure(realm, "signing act: the judge approves it", APPROVAL.format(action=filed, judge=alice))
    assert spent < BUDGET and max(costs.samples["signing act: a delegate files a verdict's issue"]) < BUDGET
    assert status(realm, second) == "issued"


def test_a_delegated_signing_call_is_still_refused_on_the_wasm(realm, people):
    alice, claude, mallory = people["alice"], people["claude-conn"], people["mallory"]
    verdict = drafted(realm, people)
    # a grant that names the signing permission cannot be made, and one of another group does not reach the act
    assert "can never be delegated" in refused(realm, "delegation_grant", alice, delegate=mallory,
                                              operations=["resolution.issue"], expires_in=DAY)
    ok(realm, "delegation_grant", alice, delegate=mallory, groups=["vote"], expires_in=DAY)
    assert "does not cover" in refused(realm, "verdict_issue", mallory, verdict=verdict, on_behalf_of=alice)
    # and the assistant that holds the sign group is refused outright when nobody is waiting to tap
    out = realm.shell(TASK_CALL.format(verdict=verdict, judge=alice, claude=claude)).strip()
    assert out.startswith("False") and "asks the member each time" in out
    assert status(realm, verdict) == "draft"


def test_a_verdict_edited_after_it_was_prepared_is_not_issued_on_the_old_card(realm, people):
    alice = people["alice"]
    verdict = drafted(realm, people)
    stale = prepare(realm, people, verdict)
    ok(realm, "verdict_modify_terms", alice, verdict=verdict, decision="The fine is ten times higher")
    refusal = tap(realm, people, stale)
    assert "Err" in refusal and status(realm, verdict) == "draft"
    assert ok(realm, "action_get", alice, query=True, action=stale["id"])["action"]["status"] == "pending"
    ok(realm, "action_decline", alice, action=stale["id"])

    fresh = prepare(realm, people, verdict)
    assert json.dumps("The fine is ten times higher") in [line["value"] for line in fresh["summary"]]
    assert next(iter(tap(realm, people, fresh)["Ok"]["status"])) == "Done" and status(realm, verdict) == "issued"
