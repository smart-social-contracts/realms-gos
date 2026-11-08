from core import chain, forbids, poa
from core.db import Fail, Forbidden, each

CONTEXT = {"op": "String", "permission": "String", "user": "String", "signer": "String", "origin": "String",
           "read": "Bool", "public": "Bool", "signing": "Bool", "delegable": "Bool", "delegated": "Bool",
           "signer_agent": "Bool", "covered": "Bool", "declared": "Set<String>", "parties": "Set<String>"}
SCHEMA = """namespace Realm {
  entity User = { permissions: Set<String>, banned: Bool };
  entity Realm;
  action call appliesTo { principal: [User], resource: [Realm], context: { %s } };
}""" % ", ".join(f"{k}: {v}" for k, v in CONTEXT.items())

# Every operation's Cedar action is ``in`` ``call`` (§9.4), so the permits and guardrails cover it too.
POLICIES = """
permit (principal, action in Realm::Action::"call", resource) when { context.public };
permit (principal, action in Realm::Action::"call", resource)
  when { principal.permissions.contains("all") || principal.permissions.contains(context.permission) };
permit (principal, action in Realm::Action::"call", resource)
  when { context.origin == "codex" && context.declared.contains(context.op) };
"""

# The realm's non-negotiable rules. A forbid beats every permit; nothing at runtime can change them.
GUARDRAILS = """
forbid (principal, action, resource) when { context.signing && context.signer_agent };
forbid (principal, action, resource) when { context.delegated && context.signing };
forbid (principal, action, resource) when { context.delegated && !context.delegable };
forbid (principal, action, resource) when { context.delegated && !context.covered };
forbid (principal, action, resource)
  when { context.parties.contains(context.user) || context.parties.contains(context.signer) };
forbid (principal, action, resource) when { principal.banned && !context.read };
forbid (principal, action, resource)
  when { (context.origin == "extension" || context.origin == "codex") && !context.declared.contains(context.op) };
"""


class Denied(Forbidden):
    """Refused by the permission check. ``permission`` is the one the caller needs, when holding it would do."""

    def __init__(self, message, permission=None):
        super().__init__(message)
        self.permission = permission


class SignIn(Denied):
    kind = "unauthenticated"


# The generated forbids of a layered codex are asked on their own, apart from the permission decision: the request
# carries only the operation and its facts, under this origin, which this permit lets through so that only a forbid
# can refuse it. That is how an accepted proposal's replay meets the legislation's forbids, with no permit or guardrail
# to deny it (the vote was the permission).
FORBIDS_ONLY = 'permit (principal, action in Realm::Action::"call", resource) when { context.origin == "forbids" };\n'
BLANK = {k: "" if t == "String" else False if t == "Bool" else [] for k, t in CONTEXT.items()}
BASE = GUARDRAILS + POLICIES
# What the Cedar engine holds: the realm's own policies, and with a layered codex the generated forbids and the schema
# they validate against (``forbids``: core.legislation's ``generated``).
live = {"schema": SCHEMA, "policies": BASE, "forbids": None, "unloaded": frozenset()}


def _canister_load(schema, policies):
    from ic_basilisk_toolkit import cedar

    try:
        cedar.load(schema, policies)
    except cedar.CedarError as e:
        raise Fail(f"the Cedar policies do not validate: {e}")


def _canister_decide(principal, action, resource, entities, context):
    from ic_basilisk_toolkit import cedar

    if not _canister_decide.loaded:
        load(live["schema"], live["policies"])
        _canister_decide.loaded = True
    return cedar.is_authorized(principal, action, resource, entities, context)


_canister_decide.loaded = False
load = _canister_load  # the engine and its parse, which tests replace along with ``decide``
decide = _canister_decide


def use_forbids(generated=None, unloaded=()):
    """Put the forbids generated for the codex now running (core.legislation's ``generated``), or none, in force.
    The engine parses them once, here, and every decision after this evaluates them without parsing again (§9.4); the
    same forbids again, as when a codex is loaded anew, are not parsed again. Fail when they do not validate: nothing
    changes. ``unloaded`` names the operations whose forbids are unknown because the codex did not load: they are
    refused (``refuse_forbidden``)."""
    if generated is None:
        if live["forbids"] is not None:
            live.update(schema=SCHEMA, policies=BASE, forbids=None)
            _canister_decide.loaded = False  # the realm's own policies are parsed again at the next decision
        live["unloaded"] = frozenset(unloaded)
        return
    policies = BASE + FORBIDS_ONLY + generated["policies"]
    if not (_canister_decide.loaded and live["policies"] == policies and live["schema"] == generated["schema"]):
        load(generated["schema"], policies)
    live.update(schema=generated["schema"], policies=policies, forbids=generated, unloaded=frozenset())
    _canister_decide.loaded = True


def check_forbids(generated):
    """Install step 5: whether the engine accepts the forbids generated for a codex, parsed and validated strictly
    against their schema. The policies in force stay in force."""
    load(generated["schema"], BASE + FORBIDS_ONLY + generated["policies"])
    if _canister_decide.loaded:  # the engine holds the codex just checked: put the running policies back
        load(live["schema"], live["policies"])


def permissions(principal):
    if chain.is_controller(principal):
        return {"all"}
    if principal == chain.ANONYMOUS:
        return set()
    from ggg import Appointment, Authority, GuestUser, Quarter, Realm, User

    realm = Realm["1"]
    if realm and realm.installer_canister_id and realm.installer_canister_id == principal:
        return {"all"}

    held = {"self.join"}
    user = User[principal]
    if user:
        held.add("self.confirm")  # every member approves what a delegate prepared for them
        for profile in user.profiles:
            held |= set(profile.allowed_to or [])
        held |= {p.id for p in user.permissions}
    guest = GuestUser[principal]
    if guest:
        held |= set(guest.permissions or [])
    held |= {"quarter.sync"} if Quarter[principal] else set()
    for a in each(Appointment, "user", principal):
        pos = a.position
        if a.status != "active" or (pos.organization and pos.organization.status != "active"):
            continue
        if pos.profile:
            held |= set(pos.profile.allowed_to or [])
        if pos.department:
            held |= {p.id for p in pos.department.permissions}
            held |= {op for auth in each(Authority, "target", f"Department:{pos.department.id}")
                     for op in auth.operations or []}
    return held


def is_agent(principal):
    from ggg import Appointment

    return any(a.agent and a.status == "active" for a in each(Appointment, "user", principal))


def is_banned(principal):
    from ggg import Member

    member = Member[principal]
    return bool(member and member.status == "banned")


def in_force(grant, now):
    """Whether ``grant``, which is active, has begun and not run out at time ``now``."""
    return grant.starts_at <= now and not (grant.expires_at and now >= grant.expires_at)


def active_grant(grantor, delegate):
    """The power of attorney ``delegate`` acts under for ``grantor`` now; Denied when there is none."""
    from ggg import Delegation

    now = chain.now()
    for d in each(Delegation, "delegate", delegate):
        if d.grantor == grantor and d.status == "active":
            if d.expires_at and now >= d.expires_at:
                d.status = "expired"
            elif in_force(d, now):
                return d
    raise Denied(f"{delegate} holds no active power of attorney from {grantor}")


def authorize(user, by, name, spec, args, origin="user", declared=(), grant=None, at=None):
    """Refuse the call with Denied, or return the id of the power of attorney a delegate acts under ("" for no one).
    A delegate acts under its active grant from ``user``, or under ``grant`` when one is given. The grant's longest
    duration is measured at ``at`` (default now): the filing of a proposal that carries the call out."""
    if origin == "replay":
        return ""
    if by == chain.ANONYMOUS and not spec["public"]:
        raise SignIn("sign in first")
    held = permissions(user)
    grant = (grant or active_grant(user, by)) if user != by else None
    needed = spec["perm_for"](args) if spec.get("perm_for") else spec["perm"]
    context = {
        "op": name, "permission": needed or "", "user": user, "signer": by, "origin": origin,
        "read": spec["read"], "public": spec["public"], "signing": spec["signing"],
        "delegable": poa.direct(grant, name, spec, args), "delegated": user != by,
        "signer_agent": spec["signing"] and is_agent(by),
        "covered": poa.covered(grant, name, spec, needed, at) if grant else False,
        "declared": sorted(declared),
        # A party may be named by principal or as "User:<principal>" (I-ALL-3).
        "parties": sorted({q for p in spec["parties"](args) for q in (p, p.removeprefix("User:"))})
        if spec["parties"] else [],
    }
    entities = [{"uid": {"type": "Realm::User", "id": user},
                 "attrs": {"permissions": sorted(held), "banned": is_banned(user)}, "parents": []},
                {"uid": {"type": "Realm::Realm", "id": "1"}, "attrs": {}, "parents": []}]
    if not decide(f'Realm::User::"{user}"', 'Realm::Action::"call"', 'Realm::Realm::"1"', entities, context):
        raise Denied(*reason(name, spec, context, held))
    return grant.id if grant else ""


def refuse_forbidden(user, name, spec, args, proposal):
    """Refuse the call with Denied when a forbid of the running codex's legislation fires on its facts (§6, §9.4): one
    request on the operation's action, and one per element of each list a forbid ranges over. It follows ``authorize``
    for every origin, a replay among them, where ``proposal`` is the proposal being executed. With no forbid on
    ``name``, or none may be asked of it, nothing is. A refusal names the forbids that fired, the id and citation of
    each, found only then."""
    in_force = live["forbids"]
    if in_force is None and not live["unloaded"]:
        return
    from core import legislation  # it imports core.op, which imports this module

    if legislation.is_reserved(name, args):  # a reserved operation, and a proposal or a vote about one: no forbid (§10.4)
        return
    if name in live["unloaded"]:  # a read too: the realm refuses what a forbid it cannot load would have judged
        raise Denied(f"{name} is forbidden by the legislation, which does not load: revert the codex to use it")
    if in_force is None:
        return
    entities = [{"uid": {"type": "Realm::User", "id": user}, "attrs": {"permissions": [], "banned": False}, "parents": []},
                {"uid": {"type": "Realm::Realm", "id": "1"}, "attrs": {}, "parents": []}]
    for action, label, facts in forbids.requests(name, spec, args, proposal, in_force["targeted"], in_force["types"]):
        context = {**BLANK, "op": name, "origin": "forbids", "facts": facts}
        if not decide(f'Realm::User::"{user}"', f'Realm::Action::"{action}"', 'Realm::Realm::"1"', entities, context):
            raise Denied(f"{name} is forbidden by the legislation: {label}{refused_by(legislation.firing(action, facts))}")


def refused_by(firing):
    """``: refused by <id> (<citation>)`` for the forbids that fired (core.legislation.firing), or nothing."""
    by = ", ".join(f["id"] + (f" ({f['legislation']})" if f["legislation"] else "") for f in firing)
    return f": refused by {by}" if by else ""


def reason(name, spec, context, held):
    """Why the call was refused, and the permission that would have allowed it, if one would."""
    if context["signing"] and context["signer_agent"]:
        return f"{name} must be done by a human, not an agent", None
    if context["delegated"]:
        return f"{name} cannot be done under this power of attorney: {why_not(spec, context)}", None
    if context["user"] in context["parties"] or context["signer"] in context["parties"]:
        return f"{name}: you are a party to this matter", None
    if context["origin"] in ("extension", "codex") and name not in context["declared"]:
        return f"{name} is not declared by this {context['origin']}", None
    return f"{name} needs {context['permission']}", context["permission"] or None


def why_not(spec, context):
    """Why a delegate may not do this call: the grant does not reach it, it asks the member each time, it is not the
    kind of act a delegate does, or the member could not do it now."""
    if not context["covered"]:
        return "this grant does not cover it"
    if not context["delegable"]:
        return "it asks the member each time" if spec["tier"] == "confirm" or spec["confirm_if"] else "it is not delegable"
    return "the member may not do it now"
