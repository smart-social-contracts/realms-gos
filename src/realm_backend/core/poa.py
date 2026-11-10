"""Power of attorney by groups: which calls a grant reaches, and what the member is told about them.

Every operation has a tier and a group. GGG's are in ggg_delegation, generated from delegation.json; the operations
Realms GOS adds to GGG are in OWN below. A grant names either groups (``scope = {"groups": ["vote", "*"]}``) or, if
it was made before groups, permissions (``{"operations": [...]}``). ``covered`` says whether a grant reaches a call,
``direct`` whether the call runs at once under it rather than only being prepared (core.auth turns both into
guardrails), and ``notice`` tells the member what a delegate did.
"""
from core import chain
from core.db import Fail, Forbidden, next_id
from ggg_delegation import EVERYTHING, GROUPS, OPERATIONS, PARAMETERS

EVERYTHING_SCOPE = "*"
TOPIC = "poa"  # of the notices that tell the member what a delegate did
NOTICE_WINDOW = 3600  # a delegate's acts within an hour share one notice
NOTICE_ACTS = 10
# Marking notices read leaves no notice: it would be an unread one about clearing the inbox.
NO_NOTICE = {"notification_read"}
# The operations that read or mark an inbox, and the group the inbox of each kind of recipient belongs to. A grant of
# "See my affairs" reads the member's own inbox only: an office's is office work, an organization's is its admins'.
INBOXES = ("notification_list", "notification_read")
INBOX_GROUPS = {"Department": "office", "Organization": "orgs"}

# (tier, group, group once the realm has launched, condition under which it asks each time), as in OPERATIONS, for
# the operations Realms GOS adds to GGG's: the tasks, the shell, extensions and the host's and the operator's own.
OWN = {
    "budget_view": ("direct", "see", None, None),
    "tax_policy_view": ("direct", "see", None, None),
    "task_create": ("direct", "admin", None, None),
    "task_edit": ("direct", "admin", None, None),
    "task_add_schedule": ("direct", "admin", None, None),
    "task_delete": ("direct", "admin", None, None),
    "task_run": ("direct", "admin", None, None),
    "task_logs": ("direct", "admin", None, None),
    "shell": ("direct", "admin", None, None),
    "realm_export": ("direct", "admin", None, None),
    "quarter_add": ("direct", "admin", None, None),
    "orchestration_decide": ("confirm", "admin", None, None),
    # Resuming an install the realm voted for changes nothing the vote did not approve: as codex_install is.
    "codex_install_resume": ("direct", "setup", "admin", None),
    # The admins' own settings, as codex_revert and governance_update are: a delegate may be given them in the group of
    # the setup (the admin's once the realm has launched). A fast revert is the IT team's act, in the admin's group.
    "it_team_set": ("direct", "setup", "admin", None),
    "codex_revert_fast": ("direct", "admin", None, None),
    # When the realm checks its sync sources: the admins' setting and act, outside legislation (§11.2).
    "codex_check_config": ("direct", "admin", None, None),
    "codex_check_now": ("direct", "admin", None, None),
    "extension_install": ("direct", "setup", "admin", None),
    "extension_uninstall": ("direct", "setup", "admin", None),
    "extension_set_access": ("direct", "setup", "admin", None),
    "sidebar_configure": ("direct", "setup", "admin", None),
    "realm_configure_infrastructure": ("direct", "setup", "admin", "in_setup"),
    # It belongs to no group: a grant by groups reaches it, and each operation it runs is held to the grant, as a call of
    # the delegate's own (core.op.on_behalf).
    "extension_call": ("direct", None, None, None),
    # Public reads, and an upgrade step for controllers: no member's act.
    "extension_list": ("none", None, None, None),
    "ops_list": ("none", None, None, None),
    "scale_status": ("none", None, None, None),
    "status": ("none", None, None, None),
    "token_catalog": ("none", None, None, None),
    "codex_releases_found": ("none", None, None, None),
    "version": ("none", None, None, None),
    "proposal_run_scheduled": ("none", None, None, None),
    "codex_switch_staged": ("none", None, None, None),
    "db_convert_indexes": ("none", None, None, None),
}

# The operations that were delegable once phase 0 had closed its holes (GGG lists them, the model's "Power of
# attorney"), less attribute_list: a grant of self.data_view was consented as "profile, notifications and invoices", and
# attributes are personal data. A grant that names permissions covers exactly these, with the behaviour they had then,
# however many operations later releases make delegable.
LEGACY = frozenset((
    "me", "profile_update", "zone_set", "zone_clear", "proposal_create", "proposal_withdraw",
    "proposal_vote", "transfer_create", "invoice_check", "my_invoices", "tax_declare", "license_apply", "license_renew",
    "request_file", "request_get", "dispute_file", "dispute_withdraw", "dispute_get", "evidence_submit", "appeal_file",
    "bid_submit", "bid_withdraw", "notification_list", "notification_read"))

# The operations a grant by groups can reach in this release. Each phase of the rollout adds its own; the last
# deletes the gate. The five that phase 0 closed come back as pending actions (core.pending), and the member's
# own pending actions are read like any other record. The tasks, the shell and extension calls run other operations
# for the member: each of those is held to the grant (core.op.on_behalf).
ENABLED = LEGACY | {"payment_account_add", "payment_account_remove", "identity_submit", "self_change_quarter",
                    "federal_vote_propose", "action_get", "action_list",
                    # A member's own affairs, office work, organizations and administration: the direct operations
                    # of those groups that no other phase owns, and contract_create, which is prepared. Not
                    # realm_export: a full export is the member's own act, and no delegate gets a part of it.
                    "allocation_rule_draft",
                    "allocation_rule_propose",
                    "appointment_end",
                    "attribute_list",
                    "authority_revoke",
                    "bids_reveal",
                    "budget_amend",
                    "budget_draft",
                    "budget_extend",
                    "budget_propose",
                    "budget_view",
                    "case_assign",
                    "codex_activate",
                    "codex_status",
                    "contract_create",
                    "court_create",
                    "delegable_operations",
                    "delegation_activity",
                    "delegation_groups",
                    "delegation_list",
                    "department_chart",
                    "department_expel",
                    "department_get",
                    "department_list",
                    "dispatch_confirm",
                    "dispatch_list",
                    "dispute_list_all",
                    "enforcement_escalate",
                    "enforcement_notify",
                    "enforcement_record",
                    "events",
                    "external_add",
                    "external_list",
                    "external_report",
                    "federal_vote_cancel",
                    "invite_list",
                    "invite_revoke",
                    "invoice_create",
                    "invoice_item_add",
                    "invoice_refresh",
                    "judge_revoke",
                    "land_list",
                    "land_map",
                    "land_mint_nft",
                    "land_register",
                    "ledger_view",
                    "loan_create",
                    "loan_list",
                    "loan_repay",
                    "mandate_assign_executor",
                    "mandate_create",
                    "mandate_revoke",
                    "notification_send",
                    "organization_get",
                    "organization_list",
                    "organization_register",
                    "organization_set_status",
                    "payroll_run",
                    "period_close",
                    "procedure_define",
                    "procedure_list",
                    "proposal_execute",
                    "proposal_finalize",
                    "proposal_get",
                    "proposal_linked_from",
                    "proposal_list",
                    "proposal_open",
                    "proposal_stop",
                    "quarter_add",
                    "quarter_configure",
                    "quarter_deregister",
                    "quarter_join_federation",
                    "quarter_register",
                    "quarter_secede",
                    "realm_monitor",
                    "report_view",
                    "representative_remove",
                    "request_decide",
                    "request_list",
                    "request_set_due",
                    "resolution_query",
                    "role_revoke",
                    "tax_assess",
                    "tax_policy_draft",
                    "tax_policy_propose",
                    "tax_policy_view",
                    "tender_cancel",
                    "tender_close",
                    "tender_create",
                    "tender_get",
                    "tender_list",
                    "tender_notice",
                    "tender_open",
                    "transfer_revert",
                    "treasury_view",
                    "user_add",
                    "user_edit",
                    "user_get",
                    "user_import",
                    "user_list",
                    "user_remove",
                    "user_set_status",
                    "verdict_draft",
                    "extension_call",
                    "shell",
                    "task_add_schedule",
                    "task_cancel",
                    "task_create",
                    "task_delete",
                    "task_edit",
                    "task_list",
                    "task_logs",
                    "task_reschedule",
                    "task_run",
                    "task_schedule",
                    # The signing acts: each is prepared by the assistant and signed by the official's own tap, never
                    # done under a grant (the guardrails keep a delegated signing call forbidden).
                    "appeal_decide",
                    "attribute_review",
                    "bid_score",
                    "contract_terminate",
                    "credential_issue",
                    "credential_revoke",
                    "dispute_accept",
                    "dispute_reject",
                    "evidence_evaluate",
                    "identity_review",
                    "land_assess",
                    "land_freeze",
                    "land_transfer",
                    "land_unfreeze",
                    "license_decide",
                    "license_revoke",
                    "license_suspend",
                    "penalty_execute",
                    "report_issue",
                    "tender_award",
                    "token_force_transfer",
                    "token_freeze",
                    "verdict_finalize",
                    "verdict_issue",
                    "verdict_link_contract",
                    "verdict_modify_terms",
                    # Keys and private data, joining, power over people, power of attorney onward and the upgrade
                    # approvals: each asks the member each time, except what is read and what only removes power.
                    "crypto_access",
                    "crypto_derive_key",
                    "crypto_envelope",
                    "crypto_grant",
                    "crypto_public_key",
                    "crypto_revoke",
                    "crypto_scopes",
                    "delegation_accept",
                    "delegation_grant",
                    "delegation_request_decide",
                    "delegation_revoke",
                    "document_delete",
                    "document_get",
                    "document_list",
                    "document_publish",
                    "document_revoke",
                    "document_share",
                    "document_store",
                    "invite_create",
                    "orchestration_decide",
                    "organization_edit",
                    "private_data_update",
                    "realm_join",
                    "representative_add"}

# The origins whose delegated call may wait for the member's tap: someone's call is in progress, and its reply says that
# the action waits. A task runs unattended, so it runs only what its grant covers directly (the model's "Tasks"), and so
# does anything it runs through the shell or an extension: a call asks only if its origin and its root are in this list.
ASKING_ORIGINS = ("user", "shell", "extension")

# Whether the arguments of a call make an operation ask each time. They are read only under a grant by groups: a
# grant made before groups keeps the behaviour phase 0 gave its operations (proposal_create refuses an action itself).
CONDITIONS = {
    "with_action": lambda args: args.get("action") is not None,
    "profile_or_seat": lambda args: (args.get("profile") or "member") != "member" or bool(args.get("position")),
    # The infrastructure step is Realms GOS's own: it names the registries the realm trusts, as the trust policy does.
    "trust_step": lambda args: args.get("step") in ("infrastructure", "trust_policy"),
    "head_or_veto_in_setup": lambda args: not launched() and ("head" in args or bool(args.get("veto"))),
    "shares_keys": lambda args: bool(args.get("keys")),
    "changes_admins": lambda args: "admins" in args,
    "approves": lambda args: bool(args.get("approve")),
    "in_setup": lambda args: not launched(),
}


def row(name):
    """(tier, group, group once launched, condition) of ``name``, or None for an operation in neither table."""
    return OPERATIONS.get(name) or OWN.get(name)


def classify(spec, name):
    """Give ``spec`` the tier, group and condition of ``name``. An operation in neither table (a test's, say) is
    unclassified: it keeps the ``delegable`` flag its decorator gave it. A classified one is ``delegable`` when its
    tier is direct, so a decorator can never disagree with GGG."""
    tier, group, after_launch, condition = row(name) or (None, None, None, None)
    spec.update(tier=tier, group=group, after_launch=after_launch, confirm_if=condition)
    if tier is not None:
        spec["delegable"] = tier == "direct"


def launched():
    from ggg import Realm

    return Realm["1"].status != "setup"


def group_of(name):
    """The group ``name`` belongs to now: a setup operation joins another group once the realm has launched."""
    _, group, after_launch, _ = row(name)
    return after_launch if after_launch and launched() else group


def split(named):
    """``(group, place)`` of a group as a grant names it. "office:Health" is the group office for the department Health;
    a group that takes no place has "" for it."""
    group, _, place = named.partition(":")
    return group, place


def label_of(named):
    """A group as a grant names it, in the realm's words: its label ("office:Health" is 'Office work: "Health"', the place
    quoted), or what "everything" stands for."""
    from core.pending import quoted

    if named == EVERYTHING_SCOPE:
        return "Everything you can do in this realm, now and later"
    group, place = split(named)
    return f"{GROUPS[group][0]}: {quoted(place)}" if place else GROUPS[group][0]


def inbox_recipient(name, args):
    """The recipient whose inbox the call of ``name`` reads or marks, or None for any other call, and for one that
    leaves it out to mean the member's own."""
    if name not in INBOXES:
        return None
    if name == "notification_read" and not args.get("all"):
        from ggg import Notification

        found = Notification[str(args.get("notification") or "")]
        return found.recipient if found else None
    return args.get("recipient")


def about(name, args):
    """``(group, place)`` of the call of ``name`` with ``args``: the group a grant must name for it, and the department
    or organization within that group the call names, or "" if it names none. An operation is in its own group, but
    the inbox of an office or an organization is in that office's or organization's, and an operation of an
    organization names its ``organization``."""
    kind, _, place = (inbox_recipient(name, args) or "").partition(":")
    if kind in INBOX_GROUPS:
        return INBOX_GROUPS[kind], place
    group = group_of(name)
    return group, (str(args.get("organization") or "") if group == "orgs" else "")


def place_of(group, place):
    """The department (for ``group`` office) or organization (for orgs) with id ``place``, or None."""
    from ggg import Department, Organization

    return (Department if group == "office" else Organization)[place]


def stands(principal, group, place):
    """Whether ``principal`` still holds what a grant of ``group`` for ``place`` stands on: a seat in the department, or
    adminship of the organization."""
    from core import auth

    if group == "office":
        return bool(auth.seats_in(principal, place))
    org = place_of(group, place)
    return bool(org and principal in (org.admins or []))


def is_legacy(name, spec):
    """Whether a grant that names permissions can cover ``name``."""
    return name in LEGACY or (spec["tier"] is None and spec["delegable"])


def reachable(name, spec):
    """Whether a grant can reach ``name`` in this release: it is enabled, or legacy."""
    return name in ENABLED or is_legacy(name, spec)


def grant_groups(grant):
    """The groups ``grant`` names, or None if it names permissions."""
    scope = grant.scope or {}
    return scope.get("groups") if "groups" in scope else None


def longest(group, everything):
    """How long, in seconds, a grant may last since its start and still reach ``group``. Under "everything" no group
    goes past 90 days."""
    seconds = GROUPS[group][3]
    return min(seconds, EVERYTHING["longest"]) if everything else seconds


def longest_grant(groups):
    """The longest, in seconds, that a grant naming ``groups`` may last: the shortest of theirs."""
    everything = EVERYTHING_SCOPE in groups
    limits = [longest(split(g)[0], everything) for g in groups if g != EVERYTHING_SCOPE]
    if everything:
        limits.append(EVERYTHING["longest"])
    return min(limits)


def places(groups, group):
    """The departments or organizations that ``groups`` name for ``group``: "office:Health" names Health."""
    return [place for named in groups for kind, place in [split(named)] if kind == group and place]


def names_place(grant, group, place, permission):
    """Whether a grant by groups names ``group``, without saying everything, for a call that needs ``permission`` and
    names ``place`` ("" if it names none). A group that takes a place is named for one, and the grantor must still stand
    on it whenever the grant is used. A call that names its place (an organization's operation, an inbox) is covered by
    that place's group. Office work names none: it is covered by a department in which the grantor holds the permission
    through a seat."""
    groups = grant_groups(grant)
    if group not in PARAMETERS:
        return group in groups
    if place:
        return f"{group}:{place}" in groups and stands(grant.grantor, group, place)
    if group != "office":
        return False
    from core import auth

    return any("all" in held or permission in held
               for held in (auth.permissions_via(grant.grantor, d) for d in places(groups, group)))


def reads_inbox(grant, recipient):
    """Whether ``grant`` reaches the inbox of ``recipient`` ("Department:Health"), if it is one by groups: it names that
    office or organization, or says everything."""
    groups = grant_groups(grant)
    kind, _, place = recipient.partition(":")
    group = INBOX_GROUPS.get(kind)
    return bool(groups is not None and group and (EVERYTHING_SCOPE in groups or names_place(grant, group, place, None)))


def covered(grant, name, spec, permission, args, at=None):
    """Whether ``grant`` reaches the call of ``name`` with ``args``, which needs ``permission``, at time ``at`` (default
    now). The limits are read when the call is made, so a stricter one binds a grant made before it."""
    groups = grant_groups(grant)
    if groups is None:
        return is_legacy(name, spec) and permission in (grant.scope or {}).get("operations", ())
    if name not in ENABLED or spec["tier"] in EVERYTHING["never"]:
        return False
    (group, place), everything = about(name, args), EVERYTHING_SCOPE in groups
    if group and not everything and not names_place(grant, group, place, permission):
        return False
    # An operation of no group is reached by every grant, for as long as the grant may last.
    lasts = longest(group, everything) if group else longest_grant(groups)
    # The grant counts from when it began to act: its start, or its acceptance if that came later.
    return (at or chain.now()) - max(grant.starts_at, grant.accepted_at) < lasts


def asks(grant, spec, args):
    """Whether the call of ``spec`` with ``args`` asks the member each time under ``grant``: its tier is confirm, or
    its tier is direct and the arguments make it ask. Only a grant by groups asks: what an old grant does not cover
    is refused, never prepared."""
    if grant is None or grant_groups(grant) is None:
        return False
    return spec["tier"] == "confirm" or bool(spec["confirm_if"] and CONDITIONS[spec["confirm_if"]](args))


def direct(grant, name, spec, args):
    """Whether the call runs at once under ``grant``, rather than waiting for the member: its tier is direct, this
    release has switched it on (GGG's flag alone opens nothing), and, under a grant by groups, the arguments do not
    make it ask each time."""
    return spec["delegable"] and reachable(name, spec) and not asks(grant, spec, args)


def enabled_by_group():
    """The enabled operations (name, spec) of each group, by name. One that belongs to no group is in none."""
    from core.op import ops

    out = {}
    for name in sorted(ENABLED):
        if group_of(name):
            out.setdefault(group_of(name), []).append((name, ops()[name]))
    return out


def check_place(group, place, grantor):
    """Refuse a department or organization that does not exist and, when the ``grantor`` is known, one that they do not
    stand on: they hold no seat in the department, or do not administer the organization."""
    if place_of(group, place) is None:
        raise Fail(f"{group}:{place} is not a group: there is no {PARAMETERS[group]} {place}")
    if grantor and not stands(grantor, group, place):
        raise Forbidden(f"you hold no seat in {place}" if group == "office" else f"you do not administer {place}")


def check_groups(groups, grantor=None):
    """Refuse a list of groups that a grant cannot name: an unknown one, one with no operation enabled yet, a place that
    does not exist, and, if the ``grantor`` is given, one in which they hold nothing: none of its operations, or no
    standing on the department or organization it names."""
    if not groups:
        raise Fail("name at least one group")
    from core import auth

    enabled, held = enabled_by_group(), auth.permissions(grantor) if grantor else None
    for named in dict.fromkeys(groups):
        if named == EVERYTHING_SCOPE:
            continue
        group, place = split(named)
        if group not in GROUPS or (":" in named) != (group in PARAMETERS):
            hint = f" ({group}:<{PARAMETERS[group]}> names one)" if group in PARAMETERS else ""
            raise Fail(f"{named} is not a group{hint}")
        if group not in enabled:
            raise Fail(f"{named} cannot be granted yet")
        if ":" in named:
            check_place(group, place, grantor)
        elif held is not None and "all" not in held and not any(spec["perm"] in held for _, spec in enabled[group]):
            raise Forbidden(f"you hold nothing in {group}")


def held_places(principal, group):
    """The ids of the departments (group office) or organizations (orgs) that ``principal`` can name in ``group``: those
    where they hold a seat and, for an organization, administer it. An organization they administer without a seat is
    not listed: finding it would read every organization (check_groups still takes it by name)."""
    from core import auth

    seats = auth.seats(principal)
    if group == "office":
        return sorted({a.position.department.id for a in seats if a.position.department})
    return sorted({a.position.organization.id for a in seats
                   if a.position.organization and stands(principal, group, a.position.organization.id)})


def vocabulary(principal):
    """The groups ``principal`` can grant, as every client shows them: label, summary, risk and longest duration,
    whether some of the group's operations ask each time, and the operations in it they hold, each with its tier.
    A group that takes a place has one entry for each department or organization they stand on ("office:Health",
    labelled "Office work: Health"), with the operations their seat in the department gives them."""
    from core import auth

    held, out = auth.permissions(principal), []
    for group, operations in enabled_by_group().items():
        if group == "office":
            offered = {d: auth.permissions_via(principal, d) for d in held_places(principal, group)}
        elif group in PARAMETERS:
            offered = {o: held for o in held_places(principal, group)}
        else:
            offered = {"": held}
        for place, permissions in offered.items():
            mine = [(name, spec) for name, spec in operations
                    if "all" in permissions or spec["perm"] in permissions]
            if mine or place:
                label, summary, risk, seconds = GROUPS[group]
                out.append({"id": f"{group}:{place}" if place else group,
                            "label": f"{label}: {place}" if place else label, "summary": summary, "risk": risk,
                            "longest": seconds,
                            "confirms": any(spec["tier"] == "confirm" or spec["confirm_if"] for _, spec in mine),
                            "operations": [{"operation": name, "permission": spec["perm"], "tier": spec["tier"],
                                            "confirm_if": spec["confirm_if"]} for name, spec in mine]})
    return sorted(out, key=lambda g: list(GROUPS).index(split(g["id"])[0]))


def notice(grant_id, name, args, out):
    """Tell the grantor what a delegate did under ``grant_id``: a notice of topic ``poa``, linked to the proposal the
    act concerns or else to the Power of attorney page. A delegate's acts within an hour of the notice's first share
    it, kept as a count and the last ten acts. Every field written is a save of its row, so a busy delegate's later
    acts write only what changed."""
    if name in NO_NOTICE:
        return
    from ggg import Delegation, Notification

    grant, now = Delegation[grant_id], chain.now()
    who, proposal = grant.label or grant.delegate, out.get("proposal") or args.get("proposal")
    act = f"{name} {proposal}" if proposal else name
    shared = Notification[grant.notice] if grant.notice else None
    if shared is None or now - shared.at >= NOTICE_WINDOW:
        grant.notice = Notification(
            id=next_id(Notification, "N"), recipient=f"User:{grant.grantor}", topic=TOPIC, title=f"{who} did {act}",
            body=act, href=f"/proposal/{proposal}" if proposal else "/delegations", sender=grant.delegate, at=now).id
        grant.notice_acts = 1
        return
    grant.notice_acts += 1
    shared.title = f"{who} did {grant.notice_acts} things"
    shared.body = "\n".join((shared.body.split("\n") + [act])[-NOTICE_ACTS:])
    if shared.read_by:
        shared.read_by = []  # it says something new
    if grant.notice_acts == 2:
        shared.href = "/delegations"
