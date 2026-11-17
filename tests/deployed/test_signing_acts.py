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
FSI, PDI = "\u2068", "\u2069"  # the isolate each quotation of the realm sits in


def isolated(quotation):
    return FSI + quotation + PDI

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
# The largest card a signing act can have: a verdict text of 28,000 characters, which the card holds twice (as an argument and
# as the text the judge signs) and which is past ASCII.
LARGEST = 28_000
FILING_LARGEST = """
from core.op import call
decision = ("Considerando la resoluci\\u00f3n que anula la denegaci\\u00f3n. " * 1000)[:{size}]
out = call("verdict_modify_terms", {{"verdict": "{verdict}", "decision": decision, "on_behalf_of": "{judge}"}}, user="{claude}")
assert out["ok"] and out["pending"], out
"""
VERDICT_STATUS = "from ggg import Verdict\nprint(Verdict['{verdict}'].status)"
BUDGET = 300_000_000
QUERY_LIMIT = 5_000_000_000  # what a query may spend


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
    assert lines["Verdict text"] == isolated(json.dumps(DECISION)) and "Issues verdict" in lines["You sign"]
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
    assert isolated(json.dumps("The fine is ten times higher")) in [line["value"] for line in fresh["summary"]]
    assert next(iter(tap(realm, people, fresh)["Ok"]["status"])) == "Done" and status(realm, verdict) == "issued"


def test_a_verdict_reads_in_the_judges_language_on_the_wasm_with_the_hidden_characters_escaped(realm, people):
    alice, claude = people["alice"], people["claude-conn"]
    verdict = drafted(realm, people)
    written = "Resolución: se anula la denegación; col·lecció 判决书"
    fine = [{"kind": "fine", "target": f"User:{people['bob']}", "amount": 5, "symbol": "€uro"}]
    clause = "; fine x1000"  # in tag characters, which render as nothing
    hidden = "".join(chr(0xE0000 + ord(c)) for c in clause)
    filed = ok(realm, "verdict_modify_terms", claude, verdict=verdict, on_behalf_of=alice, penalties=fine,
               decision=written + "\u202e\u200b\u200c\u2028\ufe0f" + hidden)
    card = ok(realm, "action_get", alice, query=True, action=filed["pending"])["action"]
    shown = {line["label"]: line["value"] for line in card["summary"]}
    escapes = "\\u202e\\u200b\\u200c\\u2028\\ufe0f" + "".join("\\udb40\\udc%02x" % ord(c) for c in clause)
    assert shown["Verdict text"] == isolated('"' + written + escapes + '"')
    assert isolated('"€uro"') in shown["Verdict penalties"] and '"symbol":"€uro"' in shown["penalties"]
    assert all(c.isprintable() for line in card["summary"] for c in line["value"].replace(FSI, "").replace(PDI, ""))
    ok(realm, "action_decline", alice, action=filed["pending"])


# Quoting a text of 30,000 characters with a hidden character in it, and one made of 20,000 different ones (private-use
# code points, each of which is escaped by itself): the dearest a card can ask.
QUOTING = """
from core.pending import quoted
quoted("a" * 30000 + chr(0x200C))
"""
QUOTING_EACH = """
from core.pending import quoted
quoted("".join(chr(0xE000 + n) for n in range(6400)) + "".join(chr(0xF0000 + n) for n in range(13600)))
"""


def test_quoting_the_longest_text_a_card_can_hold_costs_little(realm, costs):
    one = costs.measure(realm, "signing act: quoting 30,000 characters, one of them hidden", QUOTING)
    each = costs.measure(realm, "signing act: quoting 20,000 different hidden characters", QUOTING_EACH)
    assert one < BUDGET // 2 and each < BUDGET * 2


def test_the_largest_card_a_signing_act_can_have_is_filed_and_approved_within_the_budget(realm, people, costs):
    alice, claude = people["alice"], people["claude-conn"]
    verdict = drafted(realm, people)
    filed = costs.measure(realm, "signing act: a delegate files the largest card (28,000 characters)",
                          FILING_LARGEST.format(size=LARGEST, verdict=verdict, judge=alice, claude=claude))
    action = [a for a in ok(realm, "action_list", alice, query=True, status="pending")["actions"]
              if a["method"] == "verdict_modify_terms" and len(a["args"]["decision"]) == LARGEST][0]
    approved = costs.measure(realm, "signing act: the judge approves the largest card",
                             APPROVAL.format(action=action["id"], judge=alice))
    assert filed < BUDGET and approved < BUDGET
    assert realm.shell(f"from ggg import Verdict\nprint(len(Verdict['{verdict}'].decision))").strip() == str(LARGEST)


# A page of 100 cards that a stranger reads, such as a controller's export (the secrets of the calls they hold stay out): each
# holds a long text of letters, zero-width characters and line breaks.
HOSTILE_CARDS = """
from core import pending
from ggg import PendingAction

for n in range(100):
    args = {"network": "ICP", "address": "address-%d" % n, "symbol": "REALM", "label": "\\u00e9\\u200b\\n" * 300}
    lines = [{"label": key, "value": pending.shown(key, value, args)} for key, value in sorted(args.items())]
    PendingAction(id="HC-%d" % n, grantor="g", delegate="d", grant="D-0", method="payment_account_add", args=args,
                  hash="h", summary=lines)
"""
EXPORT = """
from core.op import call
out = call("realm_export", {{"entity": "PendingAction", "count": 100}}, user="{ctl}")
assert out["ok"] and len(out["rows"]) == 100 and out["rows"][0]["summary"], out
"""


def test_a_page_of_a_hundred_hostile_cards_is_cheap_to_read_for_a_stranger(realm, people, costs):
    made = realm.shell(HOSTILE_CARDS)
    assert "Traceback" not in made, made
    realm.shell(EXPORT.format(ctl=people["ctl"]))  # the first read compiles what it reads: not part of the cost of a page
    spent = costs.measure(realm, "pending actions: a stranger exports 100 hostile cards",
                          EXPORT.format(ctl=people["ctl"]))
    assert spent < QUERY_LIMIT // 5
