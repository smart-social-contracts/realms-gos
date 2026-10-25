from core import chain
from core.db import each

CONTEXT = {"op": "String", "permission": "String", "user": "String", "signer": "String", "origin": "String",
           "read": "Bool", "public": "Bool", "signing": "Bool", "delegable": "Bool", "delegated": "Bool",
           "signer_agent": "Bool", "scope": "Set<String>", "declared": "Set<String>", "parties": "Set<String>"}
SCHEMA = """namespace Realm {
  entity User = { permissions: Set<String>, banned: Bool };
  entity Realm;
  action call appliesTo { principal: [User], resource: [Realm], context: { %s } };
}""" % ", ".join(f"{k}: {v}" for k, v in CONTEXT.items())

POLICIES = """
permit (principal, action == Realm::Action::"call", resource) when { context.public };
permit (principal, action == Realm::Action::"call", resource)
  when { principal.permissions.contains("all") || principal.permissions.contains(context.permission) };
permit (principal, action == Realm::Action::"call", resource)
  when { context.origin == "codex" && context.declared.contains(context.op) };
"""

# The realm's non-negotiable rules. A forbid beats every permit; nothing at runtime can change them.
GUARDRAILS = """
forbid (principal, action, resource) when { context.signing && context.signer_agent };
forbid (principal, action, resource) when { context.delegated && context.signing };
forbid (principal, action, resource) when { context.delegated && !context.delegable };
forbid (principal, action, resource) when { context.delegated && !context.scope.contains(context.permission) };
forbid (principal, action, resource)
  when { context.parties.contains(context.user) || context.parties.contains(context.signer) };
forbid (principal, action, resource) when { principal.banned && !context.read };
forbid (principal, action, resource)
  when { (context.origin == "extension" || context.origin == "codex") && !context.declared.contains(context.op) };
"""


class Denied(Exception):
    pass


def _canister_decide(principal, action, resource, entities, context):
    from ic_basilisk_toolkit import cedar

    if not _canister_decide.loaded:
        cedar.load(SCHEMA, GUARDRAILS + POLICIES)
        _canister_decide.loaded = True
    return cedar.is_authorized(principal, action, resource, entities, context)


_canister_decide.loaded = False
decide = _canister_decide


def permissions(principal):
    if chain.is_controller(principal):
        return {"all"}
    if principal == chain.ANONYMOUS:
        return set()
    from ggg import Appointment, Authority, GuestUser, Quarter, User

    held = {"self.join"}
    user = User[principal]
    if user:
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


def delegated_scope(grantor, delegate):
    from ggg import Delegation

    now = chain.now()
    for d in each(Delegation, "delegate", delegate):
        if d.grantor == grantor and d.status == "active":
            if d.expires_at and now >= d.expires_at:
                d.status = "expired"
            elif d.starts_at <= now:
                return set((d.scope or {}).get("operations", []))
    raise Denied(f"{delegate} holds no active power of attorney from {grantor}")


def authorize(user, by, name, spec, args, origin="user", declared=()):
    if origin == "replay":
        return
    if by == chain.ANONYMOUS and not spec["public"]:
        raise Denied("sign in first")
    held = permissions(user)
    needed = spec["perm_for"](args) if spec.get("perm_for") else spec["perm"]
    context = {
        "op": name, "permission": needed or "", "user": user, "signer": by, "origin": origin,
        "read": spec["read"], "public": spec["public"], "signing": spec["signing"],
        "delegable": spec["delegable"], "delegated": user != by,
        "signer_agent": spec["signing"] and is_agent(by),
        "scope": sorted(delegated_scope(user, by)) if user != by else [],
        "declared": sorted(declared),
        "parties": sorted(spec["parties"](args)) if spec["parties"] else [],
    }
    entities = [{"uid": {"type": "Realm::User", "id": user},
                 "attrs": {"permissions": sorted(held), "banned": is_banned(user)}, "parents": []},
                {"uid": {"type": "Realm::Realm", "id": "1"}, "attrs": {}, "parents": []}]
    if not decide(f'Realm::User::"{user}"', 'Realm::Action::"call"', 'Realm::Realm::"1"', entities, context):
        raise Denied(reason(name, spec, context, held))


def reason(name, spec, context, held):
    if context["signing"] and context["signer_agent"]:
        return f"{name} must be done by a human, not an agent"
    if context["delegated"]:
        return f"{name} cannot be done under this power of attorney"
    if context["user"] in context["parties"] or context["signer"] in context["parties"]:
        return f"{name}: you are a party to this matter"
    if context["origin"] in ("extension", "codex") and name not in context["declared"]:
        return f"{name} is not declared by this {context['origin']}"
    return f"{name} needs {context['permission']}"
