"""Power of attorney by groups: which calls a grant reaches, and what the member is told about them.

Every operation has a tier and a group. GGG's are in ggg_delegation, generated from delegation.json; the operations
Realms GOS adds to GGG are in OWN below. A grant names either groups (``scope = {"groups": ["vote", "*"]}``) or, if
it was made before groups, permissions (``{"operations": [...]}``). ``covered`` says whether a grant reaches a call,
``direct`` whether the call runs at once under it rather than only being prepared (core.auth turns both into
guardrails), and ``notice`` tells the member what a delegate did.
"""
from core import chain
from core.db import Fail, Forbidden, next_id
from ggg_delegation import EVERYTHING, GROUPS, OPERATIONS

EVERYTHING_SCOPE = "*"
TOPIC = "poa"  # of the notices that tell the member what a delegate did
NOTICE_WINDOW = 3600  # a delegate's acts within an hour share one notice
NOTICE_ACTS = 10
# Marking notices read leaves no notice: it would be an unread one about clearing the inbox.
NO_NOTICE = {"notification_read"}

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
                    "task_schedule"}

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
    limits = [longest(g, everything) for g in groups if g != EVERYTHING_SCOPE]
    if everything:
        limits.append(EVERYTHING["longest"])
    return min(limits)


def covered(grant, name, spec, permission, at=None):
    """Whether ``grant`` reaches the call of ``name``, which needs ``permission``, at time ``at`` (default now). The
    limits are read when the call is made, so a stricter one binds a grant made before it."""
    groups = grant_groups(grant)
    if groups is None:
        return is_legacy(name, spec) and permission in (grant.scope or {}).get("operations", ())
    if name not in ENABLED or spec["tier"] in EVERYTHING["never"]:
        return False
    everything, group = EVERYTHING_SCOPE in groups, group_of(name)
    if group and not everything and group not in groups:
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


def check_groups(groups, held=None):
    """Refuse a list of groups that a grant cannot name: an unknown one, one with no operation enabled yet, and, if
    ``held`` (the grantor's permissions) is given, one in which the grantor holds none of them."""
    if not groups:
        raise Fail("name at least one group")
    enabled = enabled_by_group()
    for group in dict.fromkeys(groups):
        if group == EVERYTHING_SCOPE:
            continue
        if group not in GROUPS:
            raise Fail(f"{group} is not a group")
        if group not in enabled:
            raise Fail(f"{group} cannot be granted yet")
        if held is not None and "all" not in held and not any(spec["perm"] in held for _, spec in enabled[group]):
            raise Forbidden(f"you hold nothing in {group}")


def vocabulary(held):
    """The groups a member holding the permissions ``held`` can grant, as every client shows them: label, summary,
    risk and longest duration, whether some of the group's operations ask each time, and the operations in it the
    member holds, each with its tier."""
    out = []
    for group, operations in enabled_by_group().items():
        mine = [(name, spec) for name, spec in operations if "all" in held or spec["perm"] in held]
        if mine:
            label, summary, risk, seconds = GROUPS[group]
            out.append({"id": group, "label": label, "summary": summary, "risk": risk, "longest": seconds,
                        "confirms": any(spec["tier"] == "confirm" or spec["confirm_if"] for _, spec in mine),
                        "operations": [{"operation": name, "permission": spec["perm"], "tier": spec["tier"],
                                        "confirm_if": spec["confirm_if"]} for name, spec in mine]})
    return sorted(out, key=lambda g: list(GROUPS).index(g["id"]))


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
