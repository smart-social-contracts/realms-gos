"""Power of attorney for a member's own affairs, office work, organizations and administration (phase 7a): the rest of
the direct tier, and the groups a grant names for one department (office:Health) or one organization (orgs:clinic-b)."""
import datetime
import json

import pytest

import fake_chain
import world
from core import op, poa
from core.pending import quoted
from ggg import (Appointment, Authority, Contract, Delegation, Event, Notification, Organization, PendingAction,
                 Proposal, User)
from kit import call, denied, ok, rows
from test_delegation_groups import DAY, reached, without_argument_checks
from test_orgs import org_admin
from test_pending_actions import approved, card

# The groups whose direct operations a grant reaches from this phase (the others belong to later phases).
GROUPS = ("see", "profile", "vote", "propose", "filings", "justice", "money", "office", "orgs", "admin")
# What this phase switches on: the direct operations of those groups that no other phase owns, and contract_create.
THIS_PHASE = frozenset((
    "allocation_rule_draft", "allocation_rule_propose", "appointment_end", "attribute_list", "authority_revoke",
    "bids_reveal", "budget_amend", "budget_draft", "budget_extend", "budget_propose", "budget_view",
    "case_assign", "codex_activate", "codex_status", "contract_create", "court_create", "delegable_operations",
    "delegation_activity", "delegation_groups", "delegation_list", "department_chart", "department_expel",
    "department_get", "department_list", "dispatch_confirm", "dispatch_list", "dispute_list_all",
    "enforcement_escalate", "enforcement_notify", "enforcement_record", "events", "external_add",
    "external_list", "external_report", "federal_vote_cancel", "invite_list", "invite_revoke", "invoice_create",
    "invoice_item_add", "invoice_refresh", "judge_revoke", "land_list", "land_map", "land_mint_nft",
    "land_register", "ledger_view", "loan_create", "loan_list", "loan_repay", "mandate_assign_executor",
    "mandate_create", "mandate_revoke", "notification_send", "organization_get", "organization_list",
    "organization_register", "organization_set_status", "payroll_run", "period_close", "procedure_define",
    "procedure_list", "proposal_execute", "proposal_finalize", "proposal_get", "proposal_linked_from",
    "proposal_list", "proposal_open", "proposal_stop", "quarter_add", "quarter_configure", "quarter_deregister",
    "quarter_join_federation", "quarter_register", "quarter_secede", "realm_monitor",
    "report_view", "representative_remove", "request_decide", "request_list", "request_set_due",
    "resolution_query", "role_revoke", "tax_assess", "tax_policy_draft", "tax_policy_propose",
    "tax_policy_view", "tender_cancel", "tender_close", "tender_create", "tender_get", "tender_list",
    "tender_notice", "tender_open", "transfer_revert", "treasury_view", "user_add", "user_edit", "user_get",
    "user_import", "user_list", "user_remove", "user_set_status", "verdict_draft"))
# What the other phases switch on, in these groups or besides the 24 of LEGACY: each names its own here. The pending
# doors, the operations that run other operations (the ten task operations and the shell), and the keys, joining, people
# and power of attorney onward that sit in the office, orgs and admin groups.
OTHER_PHASES = frozenset((
    "payment_account_add", "payment_account_remove", "identity_submit", "self_change_quarter", "federal_vote_propose",
    "action_get", "action_list", "shell", "task_add_schedule", "task_cancel", "task_create", "task_delete", "task_edit",
    "task_list", "task_logs", "task_reschedule", "task_run", "task_schedule",
    "document_delete", "document_get", "document_list", "document_publish", "document_revoke", "document_share",
    "document_store", "invite_create", "orchestration_decide", "organization_edit", "representative_add"))
SWITCHED_ON = sorted(n for n in poa.ENABLED if poa.row(n)[1] in GROUPS)


def lend(grantor, *groups, delegate="claude-conn", days=30):
    return ok("delegation_grant", grantor, delegate=delegate, groups=list(groups), expires_in=days * DAY)["delegation"]


def acting(grantor, name, who="claude-conn", **args):
    return call(name, who, on_behalf_of=grantor, **args)


def seat_in(department, key, holder, *perms):
    world.seat(key, holder, department, *perms)
    return Appointment[f"{key}:{holder}"]


def notices(reply):
    return [n["title"] for n in reply["notifications"]]


# ---- Every operation of the groups, under a grant that names its group


def grantor_of(group, name):
    """A member who holds the permission of ``name``, the group as a grant names it for them, another group a grant of
    theirs may name, and the arguments that put a call of ``name`` in that group's place. Office work comes through a
    seat in Health, with a seat in Finance that gives nothing of it; an organization's operation is Clinic B's, of which
    its admin also administers Clinic C."""
    depts = world.build()
    perm = op.OPS[name]["perm"] or ""
    if group == "office":
        world.person("hana")
        world.seat("health.officer", "hana", depts["Health"], perm)
        world.seat("finance.officer", "hana", depts["Finance"], "nothing.of.it")
        return "hana", "office:Health", "office:Finance", {}
    if group == "orgs":
        org_admin("dr-ana")
        Organization(id="clinic-c", admins=["dr-ana"])
        return "dr-ana", "orgs:clinic-b", "orgs:clinic-c", {"organization": "clinic-b"}
    world.person("ctl")  # a controller holds every permission
    return "ctl", group, "profile" if group == "vote" else "vote", {}


@pytest.mark.parametrize("name", SWITCHED_ON)
def test_an_operation_runs_under_a_grant_that_names_its_group_and_not_under_one_that_does_not(name, monkeypatch):
    without_argument_checks(monkeypatch, name)
    grantor, named, other, args = grantor_of(poa.row(name)[1], name)
    lend(grantor, named)
    lend(grantor, other, delegate="agent-2")
    assert reached(acting(grantor, name, **args)), name
    assert "does not cover" in denied(name, "agent-2", on_behalf_of=grantor, **args)


def test_this_phase_switches_on_exactly_these_operations_of_its_groups():
    in_groups = {n for n in poa.ENABLED if poa.row(n)[1] in GROUPS}
    assert THIS_PHASE <= poa.ENABLED
    unexplained = in_groups - poa.LEGACY - OTHER_PHASES - THIS_PHASE
    assert not unexplained, f"an operation was switched on that no phase accounts for: {sorted(unexplained)}"
    assert len(THIS_PHASE) == 103 and all(poa.row(n)[0] == "direct" for n in THIS_PHASE - {"contract_create"})


# ---- Office work follows the seats


def test_an_office_group_covers_what_a_seat_in_that_department_allows_and_not_what_another_seat_allows():
    depts = world.build()
    world.person("hana")
    seat_in(depts["Health"], "health.officer", "hana", "user.view")
    seat_in(depts["Finance"], "finance.officer", "hana", "treasury.manage")
    health, finance = lend("hana", "office:Health"), lend("hana", "office:Finance", delegate="agent-2")
    assert reached(acting("hana", "user_list")) and reached(acting("hana", "invoice_create", who="agent-2"))
    assert "does not cover" in denied("invoice_create", "claude-conn", on_behalf_of="hana")
    assert "does not cover" in denied("user_list", "agent-2", on_behalf_of="hana")
    assert Delegation[health].scope == {"groups": ["office:Health"]} and Delegation[finance].status == "active"


def test_a_departments_own_permissions_and_the_authorities_granted_to_it_are_its_office_work():
    depts = world.build()
    world.person("hana")
    seat_in(depts["Health"], "health.officer", "hana")
    seat_in(depts["Finance"], "finance.officer", "hana")
    world.grant(depts["Health"], "user.view")
    Authority(id="AUTH-1", grantor=depts["Finance"], target="Department:Health", operations=["tender.manage"])
    lend("hana", "office:Health")
    lend("hana", "office:Finance", delegate="agent-2")
    for name in ("user_list", "tender_notice"):
        assert reached(acting("hana", name)), name
        assert "does not cover" in denied(name, "agent-2", on_behalf_of="hana"), name


def test_a_permission_held_only_through_a_realm_wide_profile_is_not_office_work_but_everything_covers_it():
    depts = world.build()
    world.profile("registrar", "user.view")
    world.person("hana", "registrar")
    seat_in(depts["Health"], "health.officer", "hana", "tender.manage")
    seat_in(depts["Finance"], "finance.officer", "hana", "treasury.manage")
    lend("hana", "office:Health")
    lend("hana", "office:Finance", delegate="agent-2")
    lend("hana", "*", delegate="agent-3")
    for office in ("claude-conn", "agent-2"):
        assert "does not cover" in denied("user_list", office, on_behalf_of="hana"), office
    assert reached(acting("hana", "user_list", who="agent-3"))


def test_losing_the_seat_ends_an_office_groups_coverage_at_use():
    depts = world.build()
    world.person("hana")
    seat = seat_in(depts["Health"], "health.officer", "hana", "user.view")
    lend("hana", "office:Health")
    assert reached(acting("hana", "user_list"))
    seat.status = "ended"
    assert "does not cover" in denied("user_list", "claude-conn", on_behalf_of="hana")


def test_an_office_operation_runs_as_the_members_own_call_and_the_log_names_the_delegate():
    depts = world.build()
    seat_in(depts["Civil Registry"], "civil-registry.registrar", "alice", "invite.manage")
    g = lend("alice", "office:Civil Registry")
    invite = ok("invite_create", "alice")["invite"]["id"]
    ok("invite_revoke", "claude-conn", on_behalf_of="alice", invite=invite)
    event = rows(Event)[-1]
    assert (event.op, event.user, event.by, event.grant) == ("invite_revoke", "alice", "claude-conn", g)


# ---- The office inbox


def test_an_office_inbox_is_read_only_under_that_offices_group():
    world.build()
    office, mine = "Department:Civil Registry", "User:bob"
    ok("notification_send", "ctl", recipient=office, title="Office closed Friday")
    ok("notification_send", "ctl", recipient=mine, title="Your shift")
    lend("bob", "see")
    lend("bob", "office:Civil Registry", delegate="agent-2")
    lend("bob", "*", delegate="agent-3")
    elsewhere = "Department:Infrastructure"
    listing = dict(on_behalf_of="bob")
    # "See my affairs" reads the member's own inbox, as in phase 0, and no office's.
    assert notices(ok("notification_list", "claude-conn", **listing)) == ["Your shift"]
    assert "does not cover" in denied("notification_list", "claude-conn", recipient=office, **listing)
    # The office's group reads the office's inbox, and not the member's own, nor another office's.
    assert notices(ok("notification_list", "agent-2", recipient=office, **listing)) == ["Office closed Friday"]
    assert "does not cover" in denied("notification_list", "agent-2", **listing)
    assert "does not cover" in denied("notification_list", "agent-2", recipient=elsewhere, **listing)
    # Everything covers them all, and the member's own rule still applies: only where they hold a seat.
    assert notices(ok("notification_list", "agent-3", recipient=office, **listing)) == ["Office closed Friday"]
    assert "do not read" in denied("notification_list", "agent-3", recipient=elsewhere, **listing)


def test_an_offices_notices_are_marked_read_under_that_offices_group_only():
    world.build()
    office = "Department:Civil Registry"
    n = ok("notification_send", "ctl", recipient=office, title="Office closed Friday")["notification"]
    lend("bob", "profile")
    lend("bob", "office:Civil Registry", delegate="agent-2")
    reading = dict(on_behalf_of="bob")
    assert "does not cover" in denied("notification_read", "claude-conn", notification=n, **reading)
    assert "does not cover" in denied("notification_read", "claude-conn", all=True, recipient=office, **reading)
    assert ok("notification_read", "agent-2", on_behalf_of="bob", notification=n)["read"] == 1
    assert Notification[n].read_by == ["bob"]


def test_losing_the_seat_ends_the_coverage_of_an_office_inbox_at_use():
    world.build()
    office = "Department:Civil Registry"
    ok("notification_send", "ctl", recipient=office, title="Office closed Friday")
    lend("bob", "office:Civil Registry")
    assert notices(ok("notification_list", "claude-conn", on_behalf_of="bob", recipient=office))
    Appointment["civil-registry.clerk:bob"].status = "ended"
    assert "does not cover" in denied("notification_list", "claude-conn", on_behalf_of="bob", recipient=office)


def test_an_organizations_inbox_is_read_by_its_admin_under_its_group_only():
    world.build()
    org_admin("dr-ana")
    Organization(id="clinic-c", admins=["dr-ana"])
    clinic = "Organization:clinic-b"
    ok("notification_send", "ctl", recipient=clinic, title="Inspection on Monday")
    ok("notification_send", "ctl", recipient="Organization:clinic-c", title="Not yours to read")
    lend("dr-ana", "orgs:clinic-b")
    reading = dict(on_behalf_of="dr-ana")
    assert notices(ok("notification_list", "claude-conn", recipient=clinic, **reading)) == ["Inspection on Monday"]
    assert "does not cover" in denied("notification_list", "claude-conn", recipient="Organization:clinic-c", **reading)
    assert "does not cover" in denied("notification_list", "claude-conn", **reading)


# ---- Organizations


def test_an_organizations_group_covers_that_organizations_operations_for_its_admin_only():
    world.build()
    org_admin("dr-ana")
    Organization(id="clinic-c", description="Clinic C", admins=["dr-ana"])
    g = lend("dr-ana", "orgs:clinic-b")
    remove = dict(on_behalf_of="dr-ana", appointment="none")
    assert reached(acting("dr-ana", "representative_remove", organization="clinic-b", appointment="none"))
    assert "does not cover" in denied("representative_remove", "claude-conn", organization="clinic-c", **remove)
    assert "does not cover" in denied("representative_remove", "claude-conn", **remove)
    assert Delegation[g].scope == {"groups": ["orgs:clinic-b"]}


def test_an_organizations_operation_a_grant_reaches_removes_the_seat_as_the_members_own_call():
    world.build()
    org_admin("dr-ana")
    lend("dr-ana", "orgs:clinic-b")
    out = ok("representative_remove", "claude-conn", on_behalf_of="dr-ana", organization="clinic-b",
             appointment="clinic-b.records_agent:records-bot")
    assert out["appointment"] == "clinic-b.records_agent:records-bot"
    assert Appointment["clinic-b.records_agent:records-bot"].status == "ended"


def test_losing_adminship_ends_an_organizations_coverage_at_use():
    world.build()
    org_admin("dr-ana")
    lend("dr-ana", "orgs:clinic-b")
    assert reached(acting("dr-ana", "representative_remove", organization="clinic-b", appointment="none"))
    Organization["clinic-b"].admins = ["bob"]
    assert "does not cover" in denied("representative_remove", "claude-conn", on_behalf_of="dr-ana",
                                      organization="clinic-b", appointment="none")


# ---- What a grant may name


def test_a_grant_names_a_department_only_where_the_grantor_sits_and_an_organization_only_where_they_administer():
    depts = world.build()
    org_admin("dr-ana")
    base = dict(delegate="claude-conn", expires_in=DAY)
    seat_in(depts["Health"], "health.officer", "alice", "user.view")
    assert "you hold no seat in Health" in denied("delegation_grant", "bob", groups=["office:Health"], **base)
    assert "you hold no seat in Finance" in denied("delegation_grant", "alice", groups=["vote", "office:Finance"],
                                                   **base)
    assert "you do not administer clinic-b" in denied("delegation_grant", "alice", groups=["orgs:clinic-b"], **base)
    assert "you do not administer buildco" in denied("delegation_grant", "dr-ana", groups=["orgs:buildco"], **base)
    # An administrator who holds no seat is not listed, but names the organization.
    assert "orgs:buildco" not in [g["id"] for g in ok("delegation_groups", "bob")["groups"]]
    lend("bob", "orgs:buildco")
    lend("alice", "office:Health", delegate="agent-2")
    assert Delegation.count() == 2


def test_a_group_with_a_place_is_named_with_one_that_exists_and_a_group_without_a_place_is_named_without():
    world.build()
    base = dict(delegate="claude-conn", expires_in=DAY)
    for named in ("office:Nowhere", "orgs:nowhere", "office", "orgs", "vote:Health", "vote:", "office:"):
        assert "not a group" in denied("delegation_grant", "ctl", groups=[named], **base), named
    assert "office:<department> names one" in denied("delegation_grant", "ctl", groups=["office"], **base)


def test_a_request_names_a_department_and_the_member_who_answers_it_must_hold_a_seat_there():
    depts = world.build()
    seat_in(depts["Health"], "health.officer", "alice", "user.view")
    ask = dict(expires_in=DAY, groups=["office:Health"])
    assert "not a group" in denied("delegation_request", "claude-conn", grantor="alice", expires_in=DAY,
                                   groups=["office:Nowhere"])
    r = ok("delegation_request", "claude-conn", grantor="alice", **ask)["request"]
    q = ok("delegation_request", "agent-2", grantor="bob", **ask)["request"]
    assert "you hold no seat in Health" in denied("delegation_request_decide", "bob", request=q, approve=True)
    out = ok("delegation_request_decide", "alice", request=r, approve=True)
    assert Delegation[out["delegation"]].scope == {"groups": ["office:Health"]}


def test_the_vocabulary_has_an_entry_for_each_department_and_organization_the_member_stands_on():
    depts = world.build()
    world.person("hana")
    seat_in(depts["Health"], "health.officer", "hana", "user.view")
    seat_in(depts["Finance"], "finance.officer", "hana", "treasury.manage")
    groups = {g["id"]: g for g in ok("delegable_operations", "hana")["groups"]}
    assert [g for g in groups if g.startswith("office")] == ["office:Finance", "office:Health"]
    assert groups["office:Health"]["label"] == "Office work: Health"
    assert {o["operation"] for o in groups["office:Health"]["operations"]} >= {"user_list", "user_get"}
    assert "invoice_create" not in {o["operation"] for o in groups["office:Health"]["operations"]}
    assert "invoice_create" in {o["operation"] for o in groups["office:Finance"]["operations"]}
    org_admin("dr-ana")
    clinic = {g["id"]: g for g in ok("delegation_groups", "dr-ana")["groups"]}["orgs:clinic-b"]
    assert clinic["label"] == "Organisations I run: clinic-b" and clinic["longest"] == poa.GROUPS["orgs"][3]
    assert not [g for g in ok("delegation_groups", "alice")["groups"] if g["id"].startswith(("office", "orgs"))]


def test_a_grant_for_one_place_lasts_as_long_as_its_group_allows(monkeypatch):
    depts = world.build()
    seat_in(depts["Health"], "health.officer", "alice", "user.view")
    monkeypatch.setitem(poa.GROUPS, "office", (*poa.GROUPS["office"][:3], 10 * DAY))
    assert "10 days" in denied("delegation_grant", "alice", groups=["office:Health"], delegate="claude-conn",
                               expires_in=11 * DAY)
    lend("alice", "office:Health", days=10)


# ---- Administration


def test_a_governed_operation_under_a_group_is_filed_as_a_proposal_tied_to_the_grant_and_runs_under_it():
    depts = world.build()
    world.grant(depts["Infrastructure"], "org.appoint")  # carla holds it through her seat
    seat_in(depts["Infrastructure"], "infrastructure.inspector", "bob")
    g = lend("carla", "admin", "vote")
    out = ok("appointment_end", "claude-conn", on_behalf_of="carla", appointment="infrastructure.inspector:bob")
    p = out["proposal"]
    assert out["requires_vote"] and Proposal[p].filed_under == g
    assert Appointment["infrastructure.inspector:bob"].status == "active"
    assert "filed by claude-conn" in denied("proposal_vote", "claude-conn", on_behalf_of="carla", proposal=p,
                                            choice="yes")
    ok("proposal_vote", "carla", proposal=p, choice="yes")
    assert Proposal[p].status == "executed", Proposal[p].result
    assert Appointment["infrastructure.inspector:bob"].status == "ended"


def test_a_governed_operation_filed_under_a_grant_that_is_then_revoked_does_not_run():
    depts = world.build()
    world.grant(depts["Infrastructure"], "org.appoint")
    seat_in(depts["Infrastructure"], "infrastructure.inspector", "bob")
    g = lend("carla", "admin")
    filed = ok("appointment_end", "claude-conn", on_behalf_of="carla", appointment="infrastructure.inspector:bob")
    p = filed["proposal"]
    ok("delegation_revoke", "carla", delegation=g)
    assert ok("proposal_vote", "carla", proposal=p, choice="yes")["status"] == "failed"
    assert "revoked or replaced" in Proposal[p].result["error"]
    assert Appointment["infrastructure.inspector:bob"].status == "active"


def test_an_office_operation_filed_under_a_grant_runs_only_while_the_seat_stands():
    world.build()
    lend("dana", "office:Civil Registry")
    new = dict(on_behalf_of="dana", department="Civil Registry")
    first = ok("user_add", "claude-conn", user="newcomer", **new)["proposal"]
    assert Proposal[first].filed_under and User["newcomer"] is None
    assert ok("proposal_vote", "bob", proposal=first, choice="yes")["status"] == "executed", Proposal[first].result
    assert User["newcomer"] is not None
    second = ok("user_add", "claude-conn", user="latecomer", **new)["proposal"]
    Appointment["civil-registry.head:dana"].status = "ended"
    assert ok("proposal_vote", "bob", proposal=second, choice="yes")["status"] == "failed"
    assert "does not allow user_add now" in Proposal[second].result["error"] and User["latecomer"] is None


def test_the_reads_of_administration_are_direct():
    world.build()
    world.person("ctl")
    lend("ctl", "admin")
    ok("notification_send", "ctl", recipient="User:alice", title="Hello")
    assert ok("events", "claude-conn", on_behalf_of="ctl")["events"]
    assert ok("realm_monitor", "claude-conn", on_behalf_of="ctl")["status"]


def test_a_delegate_does_not_export_the_realm_under_any_group_everything_included():
    """A full export is the member's own act: it gives every row and field of an entity, whatever the delegate may read."""
    depts = world.build()
    org_admin("dr-ana")
    world.person("ctl")
    world.person("hana")
    seat_in(depts["Health"], "health.officer", "hana", "db.export")
    plain = [g for g in poa.enabled_by_group() if g not in poa.PARAMETERS] + ["*"]
    grants = [("ctl", group) for group in plain] + [("hana", "office:Health"), ("dr-ana", "orgs:clinic-b")]
    for i, (grantor, group) in enumerate(grants):
        lend(grantor, group, delegate=f"agent-{i}")
        error = denied("realm_export", f"agent-{i}", on_behalf_of=grantor, entity="User")
        assert "does not cover" in error, (group, error)
    assert ok("realm_export", "ctl", entity="User")["rows"]


# ---- Contracts


def mandate_for_carla(cap=200000):
    depts = world.build()
    world.grant(depts["Infrastructure"], "contract.create_under_mandate")
    scope = {"max_value": cap}
    return ok("mandate_create", "ctl", title="Road works", scope=scope, executor="Infrastructure")["mandate"]


def test_a_contract_under_a_grant_is_prepared_for_the_members_approval_and_stays_within_its_mandate():
    m = mandate_for_carla()
    lend("carla", "money")
    over = denied("contract_create", "claude-conn", on_behalf_of="carla", title="Bike lane", mandate=m, value=300000)
    assert "exceeds mandate" in over and PendingAction.count() == 0
    out = ok("contract_create", "claude-conn", on_behalf_of="carla", title="Bike lane", mandate=m, value=50000)
    a = out["pending"]
    assert Contract.count() == 0
    lines = {line["label"]: line["value"] for line in card(a, "carla")["summary"]}
    assert lines["Under mandate"] == f'{m} {quoted("Road works")}, up to 200000'
    assert lines["What changes"] == "An active contract binds the realm to its parties at once"
    assert card(a, "carla")["title"] == "Sign a contract"
    assert approved(a, "carla")["status"] == "done"
    assert Contract.count() == 1 and Contract["C-1"].value == 50000 and Contract["C-1"].mandate.id == m


def day(seconds):
    return datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc).strftime("%Y-%m-%d")


def test_a_contract_needs_no_mandate_and_its_card_shows_its_parties_amount_and_term_as_quoted_realm_lines():
    mandate_for_carla()
    lend("carla", "money")
    ends = fake_chain.now() + 30 * DAY
    hostile = 'Evil Ltd"\nWhat happens next: Runs at once when you approve'
    a = ok("contract_create", "claude-conn", on_behalf_of="carla", title="Chairs", value=1200, symbol="REALM",
           parties=["Organization:buildco", hostile], ends_at=ends)["pending"]
    summary = card(a, "carla")["summary"]
    lines = {line["label"]: line["value"] for line in summary}
    assert lines["Contract parties"] == quoted("Organization:buildco") + ", " + quoted(hostile)
    assert lines["Contract amount"] == f'1200 {quoted("REALM")}'
    assert lines["Contract term"] == f"from {day(fake_chain.now())} until {day(ends)}"
    assert [line["label"] for line in summary].count("What happens next") == 1 and "Under mandate" not in lines
    assert approved(a, "carla")["status"] == "done"
    assert Contract["C-1"].mandate is None and Contract["C-1"].value == 1200
    b = ok("contract_create", "claude-conn", on_behalf_of="carla", title="Free work")["pending"]
    lines = {line["label"]: line["value"] for line in card(b, "carla")["summary"]}
    assert lines["Contract parties"] == "none named" and lines["Contract term"].endswith("with no end")


def test_a_contract_prepared_within_the_cap_is_refused_at_approval_if_the_mandate_no_longer_allows_it():
    m = mandate_for_carla()
    lend("carla", "money")
    a = ok("contract_create", "claude-conn", on_behalf_of="carla", title="Bike lane", mandate=m, value=50000)["pending"]
    ok("mandate_revoke", "ctl", mandate=m)
    assert approved(a, "carla")["status"] == "failed" and Contract.count() == 0
    assert "is not active" in card(a, "carla")["result"]["error"]


def test_a_contract_is_refused_to_a_delegate_whose_member_is_not_in_the_mandates_executor_department():
    m = mandate_for_carla()
    world.profile("contractor", "contract.create_under_mandate")
    world.person("bob", "contractor")
    lend("bob", "money")
    refusal = denied("contract_create", "claude-conn", on_behalf_of="bob", title="Chairs", mandate=m)
    assert "executor department" in refusal
    assert PendingAction.count() == 0


def test_an_old_grant_does_not_reach_a_contract():
    mandate_for_carla()
    Delegation(id="D-old", grantor="carla", delegate="claude-conn", status="active", expires_at=fake_chain.now() + DAY,
               scope={"operations": ["contract.create_under_mandate"]})
    assert "does not cover" in denied("contract_create", "claude-conn", on_behalf_of="carla", title="C")
    assert PendingAction.count() == 0
