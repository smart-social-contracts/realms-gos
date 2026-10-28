from core import chain
from core.db import Conflict, Forbidden, deref, each, find, next_id, page, page_by, view
from ggg import Calendar, Department, License, Organization
from core.op import op

SHOWN = ("id", "kind", "holder", "status", "issued_at", "expires_at")


def valid(lic):
    return lic.status == "active" and (not lic.expires_at or chain.now() < lic.expires_at)


def active(holder, kind):
    """The licence of ``kind`` that authorizes ``holder`` (`Entity:id`) now, or None (I-LIC-2)."""
    return next((x for x in each(License, "holder", holder) if x.kind == kind and valid(x)), None)


def representatives(org):
    return {a.user for p in org.positions for a in p.appointments if a.status == "active"}


def principals(ref):
    """Who answers for `Entity:id`: the user, or an organization's admins and representatives."""
    kind, _, key = ref.partition(":")
    if kind == "Organization":
        org = find(Organization, key)
        return set(org.admins or []) | representatives(org)
    return {key}


def holders(args):
    return principals(find(License, args["license"]).holder)


def applicant(ref, user):
    entity = deref(ref)
    org = isinstance(entity, Organization)
    if not (ref == f"User:{user}" or org and entity.status == "active" and user in (entity.admins or [])):
        raise Forbidden(f"you cannot apply for {ref}")


def application(c, kind, holder, department, evidence_ref, conditions):
    applicant(holder, c.user)
    issuer = find(Department, department) if department else None
    lic = License(id=next_id(License, "LIC"), kind=kind, holder=holder, status="applied", applied_at=chain.now(),
                  evidence_ref=evidence_ref, conditions=conditions)
    if issuer:
        lic.issued_by = issuer
    return {"license": lic.id}


@op("license.apply", delegable=True, returns="license")
def license_apply(c, args):
    """Apply for a licence of ``kind`` for ``holder`` (default you, or ``Organization:id`` of one you administer), to
    issuing ``department`` (optional), with optional ``evidence_ref`` and ``conditions``. Returns ``license``."""
    return application(c, args["kind"], args.get("holder") or f"User:{c.user}", args.get("department"),
                       args.get("evidence_ref", ""), args.get("conditions", {}))


@op("license.apply", delegable=True, returns="license")
def license_renew(c, args):
    """Apply to renew the active or expired ``license``, optionally with new ``evidence_ref``. Approving the renewal
    expires the old licence. Returns the new application's ``license``."""
    old = find(License, args["license"])
    if old.status not in ("active", "expired"):
        raise Conflict(f"{old.id} is {old.status}")
    return application(c, old.kind, old.holder, old.issued_by.id if old.issued_by else None,
                       args.get("evidence_ref", old.evidence_ref), {**(old.conditions or {}), "renews": old.id})


@op("license.issue", signing=True, parties=holders, returns="license")
def license_decide(c, args):
    """Issue the applied-for ``license`` if ``approve`` is true, else reject it; issuing ``department`` defaults to
    the one applied to. On approval: ``expires_at`` (default now plus the licence review cycle, 2 years) and extra
    ``conditions``. Returns ``status`` and ``expires_at``."""
    lic = find(License, args["license"])
    if lic.status != "applied":
        raise Conflict(f"{lic.id} is {lic.status}, not an application")
    lic.issued_by = find(Department, args.get("department") or (lic.issued_by.id if lic.issued_by else ""))
    if not args.get("approve"):
        lic.status = "rejected"
        return {"license": lic.id, "status": lic.status}
    cal = Calendar["1"]
    lic.status, lic.issued_at = "active", chain.now()
    lic.expires_at = int(args.get("expires_at") or chain.now() + (cal.license_review_cycle if cal else 2 * 365 * 86400))
    lic.conditions = {**(lic.conditions or {}), **args.get("conditions", {})}
    old = License[lic.conditions.get("renews") or "-"]
    if old and old.status == "active":
        old.status = "expired"
    return {"license": lic.id, "status": lic.status, "expires_at": lic.expires_at}


@op("license.revoke", signing=True, parties=holders, returns="license")
def license_suspend(c, args):
    """Suspend the active ``license``, or with ``resume`` true reinstate a suspended one."""
    lic = find(License, args["license"])
    before, after = ("suspended", "active") if args.get("resume") else ("active", "suspended")
    if lic.status != before:
        raise Conflict(f"{lic.id} is {lic.status}")
    lic.status = after
    return {"license": lic.id, "status": after}


@op("license.revoke", signing=True, parties=holders, returns="license")
def license_revoke(c, args):
    """Revoke the active or suspended ``license`` for good."""
    lic = find(License, args["license"])
    if lic.status not in ("active", "suspended"):
        raise Conflict(f"{lic.id} is {lic.status}")
    lic.status = "revoked"
    return {"license": lic.id, "status": lic.status}


def shown(lic):
    return {**view(lic, *SHOWN), "issued_by": lic.issued_by.id if lic.issued_by else "", "valid": valid(lic)}


@op(None, read=True, public=True, returns="licenses")
def license_list(c, args):
    """One page of licences (``from``, ``count``), optionally filtered by ``holder``, ``kind`` and ``status``; ``next``
    continues it. A page can hold fewer matches than ``count``: follow ``next``."""
    want = {k: args[k] for k in ("kind", "status") if args.get(k)}
    rows, more = page_by(License, "holder", args["holder"], args) if args.get("holder") else page(License, args)
    return {"licenses": [shown(x) for x in rows if all(getattr(x, k) == v for k, v in want.items())], "next": more}


@op(None, read=True, public=True)
def license_verify(c, args):
    """Whether ``license``, or else the licence of ``kind`` that ``holder`` (``Entity:id``) holds, is valid now.
    Returns ``valid`` and the ``license``."""
    lic = find(License, args["license"]) if args.get("license") else active(args["holder"], args["kind"])
    return {"valid": bool(lic and valid(lic)), "license": shown(lic) if lic else None}
