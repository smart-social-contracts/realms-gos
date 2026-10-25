"""``console.*`` verbs for the sandboxed ``migration_console`` extension.

The console is a realm-setup dashboard: organizations and their seats, invite
codes, the lifecycle readiness checklist, and bulk citizen import.

Two things made it worth moving wholesale rather than verb-by-verb over
individual entities. First, ``get_console_data`` assembles one screen from six
sources, and splitting that into six round trips through the bridge would be
slower and no safer. Second, the payload is dense with invite URLs and
personal data, so the interesting decision is *who may see the screen at all*
— a question the extension previously answered itself by re-implementing the
operation check against ``profile.allowed_to``.

Invite codes are credentials. Minting one is a privilege grant, so the writes
here re-check the caller's operation host-side rather than trusting the
extension's ``entry_access``.
"""

import json

CONSOLE_OPERATIONS = ("realm.admin", "user.view")
INVITE_OPERATION = "invite.manage"
IMPORT_OPERATION = "user.add"

DEFAULT_INVITE_HOURS = 720
DEFAULT_INVITE_USES = 100
MAX_PAGE = 500


def _has(caller: str, operation: str) -> bool:
    from core.extension_bridge import caller_has_operation

    return caller_has_operation(caller, operation)


def _require(caller: str, operation: str, verb: str) -> None:
    if not _has(caller, operation):
        raise PermissionError(f"{verb} requires the '{operation}' operation")


def _realm():
    from ggg import Realm

    realms = Realm.instances()
    return realms[0] if realms else None


def _base_url(realm) -> str:
    canister = (getattr(realm, "frontend_canister_id", "") or "").strip()
    return f"https://{canister}.icp0.io" if canister else ""


def _invite_url(code, base_url: str) -> str:
    if not code.code:
        return ""
    base = (code.frontend_url or base_url or "").rstrip("/")
    return f"{base}/join?invite={code.code}" if base else ""


def _project_invite(code, base_url: str) -> dict:
    """An invite as plain data.

    ``code_hash`` is truncated to eight characters: enough to tell two codes
    apart in the UI, not enough to be a credential.
    """
    return {
        "code_hash": (code.code_hash or "")[:8],
        "profile": code.profile or "member",
        "department": code.department or "",
        "position": getattr(code, "position", "") or "",
        "url": _invite_url(code, base_url),
        "uses_count": int(code.uses_count or 0),
        "max_uses": int(code.max_uses or 1),
        "is_valid": bool(code.is_valid()),
        "revoked": code.revoked == 1,
    }


def _project_position(pos) -> dict:
    holders = []
    try:
        for appointment in pos.active_appointments():
            user = appointment.user
            if user is not None:
                holders.append({
                    "principal": user.id,
                    "nickname": user.nickname or "",
                })
    except Exception:
        pass

    profile = ""
    try:
        if pos.profile:
            profile = pos.profile.name or ""
    except Exception:
        pass

    headcount = int(pos.headcount or 1)
    return {
        "key": pos.key or "",
        "title": pos.title or "",
        "profile": profile,
        "headcount": headcount,
        "filled": len(holders),
        "vacancies": max(0, headcount - len(holders)),
        "salary_amount": int(pos.salary_amount or 0),
        "salary_period": pos.salary_period or "monthly",
        "status": pos.status or "open",
        "holders": holders,
    }


def _members_by_department() -> dict:
    """One user scan, since the reverse index was removed in issue #242."""
    by_department: dict = {}
    try:
        from core.membership import iter_users

        for user in iter_users():
            principal = getattr(user, "id", None)
            if not principal:
                continue
            try:
                memberships = list(user.departments)
            except Exception:
                continue
            for dept in memberships:
                by_department.setdefault(dept.name, []).append({
                    "principal": principal,
                    "nickname": user.nickname or "",
                })
    except Exception:
        pass
    return by_department


def _project_org(dept, base_url: str, members: list) -> dict:
    from ggg import ROOT_ORG_NAME, RegistrationCode

    fund = None
    try:
        if dept.fund:
            fund = {"code": dept.fund.code, "name": dept.fund.name}
    except Exception:
        pass

    invites = sorted(
        (_project_invite(c, base_url)
         for c in RegistrationCode.find_by_department(dept.name)),
        key=lambda i: (i["profile"], i["code_hash"]),
    )

    positions = []
    try:
        from ggg import Position

        positions = sorted(
            (_project_position(p) for p in Position.for_department(dept.name)),
            key=lambda p: p["title"],
        )
    except Exception:
        pass

    return {
        "name": dept.name,
        "description": dept.description or "",
        "is_root": bool(
            getattr(dept, "is_root", False) or dept.name == ROOT_ORG_NAME
        ),
        "member_count": len(members),
        "members": members,
        "policy": {
            "threshold_m": int(getattr(dept, "policy_threshold_m", 1) or 1),
            "threshold_n": int(getattr(dept, "policy_threshold_n", 1) or 1),
            "quorum_percent": int(
                getattr(dept, "policy_quorum_percent", 0) or 0
            ),
        },
        "fund": fund,
        "invites": invites,
        "positions": positions,
    }


def _in_a_department(caller: str) -> bool:
    from ggg import User

    try:
        user = User[caller]
        return bool(user) and any(True for _ in user.departments)
    except Exception:
        return False


def v_overview(caller="", **kwargs) -> dict:
    """Everything the console shell renders, in one call.

    Visible to realm admins and to department staff — the same rule as before,
    but applied against the host's RBAC rather than the extension's own reading
    of ``profile.allowed_to``.
    """
    is_admin = _has(caller, "realm.admin")
    if not (is_admin or _has(caller, "user.view") or _in_a_department(caller)):
        raise PermissionError(
            "console.overview requires realm.admin, user.view, or department "
            "membership"
        )

    from ggg import Department

    realm = _realm()
    if not realm:
        raise ValueError("No realm configured")

    base_url = _base_url(realm)
    try:
        config = json.loads(realm.manifest_data or "{}")
    except Exception:
        config = {}

    members = _members_by_department()
    orgs = sorted(
        (_project_org(d, base_url, members.get(d.name, []))
         for d in Department.instances()),
        key=lambda o: (0 if o["is_root"] else 1, o["name"]),
    )

    from core.lifecycle_gate import readiness_checklist

    checklist = readiness_checklist(realm)

    from core.citizen_import import import_status

    quarters = []
    try:
        from ggg import Quarter

        quarters = sorted(
            ({
                "name": q.name or "",
                "canister_id": q.canister_id or "",
                "population": int(q.population or 0),
                "status": q.status or "active",
                "index": int(q.index or 0),
            } for q in Quarter.instances()),
            key=lambda q: q["index"],
        )
    except Exception:
        pass

    dashboard = (config.get("dashboard", {}) or {}).get("public", {}) or {}
    return {
        "realm": {
            "name": realm.name or "",
            "status": getattr(realm, "status", "") or "",
        },
        "codex": {
            "dashboard_profile": dashboard.get("profile", ""),
            "dependencies": config.get("dependencies", []) or [],
            "lifecycle": config.get("lifecycle", {}) or {},
        },
        "checklist": checklist,
        "checklist_done": sum(1 for item in checklist if item["done"]),
        "checklist_total": len(checklist),
        "organizations": orgs,
        "citizen_import": import_status(),
        "quarters": quarters,
        "currency": {
            "accounting_currency": getattr(realm, "accounting_currency", "") or "",
            "token_canister_id": (
                getattr(realm, "token_canister_id", "") or ""
            ).strip(),
        },
        "is_admin": is_admin,
    }


def v_regenerate_invite(caller="", department="", profile="",
                        expires_in_hours=DEFAULT_INVITE_HOURS,
                        max_uses=DEFAULT_INVITE_USES, **kwargs) -> dict:
    """Revoke and replace the invite code for a (department, profile) pair.

    The position link is carried across, so redeeming the new code still
    appoints to the same seat. ``created_by`` is the authenticated caller.
    """
    _require(caller, INVITE_OPERATION, "console.regenerate_invite")
    from ggg import Department, RegistrationCode
    from ggg.system.registration_code import create_registration_code

    department = str(department or "").strip()
    profile = str(profile or "").strip()
    if not department or not profile:
        raise ValueError("department and profile are required")

    dept = Department[department]
    if not dept:
        raise ValueError(f"Department '{department}' not found")

    position = ""
    for code in RegistrationCode.find_by_department(department):
        if code.profile != profile:
            continue
        position = getattr(code, "position", "") or position
        if code.revoked != 1:
            code.revoked = 1

    realm = _realm()
    base_url = _base_url(realm) if realm else ""
    created = create_registration_code(
        code_hash=None,
        profile=profile,
        max_uses=int(max_uses or DEFAULT_INVITE_USES),
        expires_in_hours=int(expires_in_hours or DEFAULT_INVITE_HOURS),
        created_by=caller,
        frontend_url=base_url,
        department=department,
        position=position,
    )
    return _project_invite(created, base_url)


def v_import_citizens(caller="", citizens=None, frontend_url="",
                      expires_in_hours=None, **kwargs) -> dict:
    """Bulk-import citizens, minting one single-use personal invite each."""
    _require(caller, IMPORT_OPERATION, "console.import_citizens")
    from core.citizen_import import DEFAULT_EXPIRES_HOURS
    from core.citizen_import import import_citizens as run_import

    if citizens is None:
        raise ValueError("citizens (array) is required")

    realm = _realm()
    return run_import(
        citizens,
        created_by=caller,
        frontend_url=frontend_url or (_base_url(realm) if realm else ""),
        expires_in_hours=int(
            expires_in_hours
            if expires_in_hours is not None else DEFAULT_EXPIRES_HOURS
        ),
    )


def v_list_citizen_invites(caller="", offset=0, limit=100, only_pending=False,
                           **kwargs) -> dict:
    """Imported citizens with claim state and personal invite URLs.

    Paginated host-side: a multi-thousand census would otherwise exceed the
    message-size limit, and the rows carry personal invite links.
    """
    _require(caller, INVITE_OPERATION, "console.list_citizen_invites")
    from core.citizen_import import _citizen_codes

    realm = _realm()
    base_url = _base_url(realm) if realm else ""

    rows = []
    for code, meta in _citizen_codes():
        claimed = bool(code.uses_count and code.uses_count > 0)
        if only_pending and (claimed or code.revoked == 1):
            continue
        redeemed = (code.principals_redeemed or "").split(",")
        rows.append({
            "id": code.user_id or "",
            "name": meta.get("name", ""),
            "quarter": meta.get("quarter", ""),
            "email": code.email or "",
            "claimed": claimed,
            "claimed_by": redeemed[0] if claimed and redeemed else "",
            "revoked": code.revoked == 1,
            "url": _invite_url(code, base_url),
        })

    rows.sort(key=lambda r: r["id"])
    offset = max(0, int(offset or 0))
    limit = min(MAX_PAGE, max(1, int(limit or 100)))
    return {
        "citizens": rows[offset:offset + limit],
        "total": len(rows),
        "offset": offset,
        "limit": limit,
    }


READS = {
    "console.overview": v_overview,
    "console.list_citizen_invites": v_list_citizen_invites,
}

WRITES = {
    "console.regenerate_invite": v_regenerate_invite,
    "console.import_citizens": v_import_citizens,
}

VERBS = dict(READS, **WRITES)
