"""Department policy helpers (issue #240).

Realm-internal GGG rules for governance orgs (Department entity).
Not related to Casals/Baton orchestration policies.
"""

from __future__ import annotations

from typing import Iterable, Optional

from ic_python_logging import get_logger

logger = get_logger("core.org_policy")

# Default permissions root holds over every other local org.
ROOT_DEFAULT_OVER_ORG = (
    "org.appoint",
    "org.expel",
    "org.set_policy",
    "org.grant_authority",
    "org.revoke_authority",
    "org.manage_budget",
)


def parse_veto_principals(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    return [p.strip() for p in str(raw).split(",") if p.strip()]


def policy_satisfied(
    *,
    approvals: Iterable[str],
    vetoes: Iterable[str],
    eligible: Iterable[str],
    threshold_m: int,
    threshold_n: int,
    quorum_percent: int,
    veto_principals: Iterable[str],
) -> tuple[bool, str]:
    """Return (ok, reason) for an org policy check.

    - Any veto from a listed veto principal rejects.
    - Approvals are counted only among ``eligible``.
    - Need at least ``threshold_m`` approvals.
    - If ``threshold_n`` > 0, eligible pool size is treated as max(n, len(eligible))
      only for documentation; M is absolute among eligible approvals.
    - If ``quorum_percent`` > 0, approvals/len(eligible) must meet that percent.
    """
    eligible_set = {p for p in eligible if p}
    approval_set = {p for p in approvals if p} & eligible_set
    veto_set = {p for p in vetoes if p}
    veto_allowed = {p for p in veto_principals if p}

    blocking = veto_set & veto_allowed
    if blocking:
        return False, f"vetoed by {sorted(blocking)[0]}"

    m = max(1, int(threshold_m or 1))
    if len(approval_set) < m:
        return False, f"need {m} approvals, have {len(approval_set)}"

    n = int(threshold_n or 0)
    if n > 0 and len(eligible_set) > 0 and len(eligible_set) < n:
        # Fewer eligible than N: still require M among those who exist.
        pass

    q = int(quorum_percent or 0)
    if q > 0:
        if not eligible_set:
            return False, "quorum requires eligible members"
        participation = (len(approval_set) / len(eligible_set)) * 100
        if participation < q:
            return False, f"quorum {q}% not met ({participation:.1f}%)"

    return True, "ok"


def ensure_root_org(head_user=None):
    """Create the quarter ``root`` org if missing. Returns the root Department."""
    from ggg import ROOT_ORG_NAME, Department, Fund, FundType

    existing = Department[ROOT_ORG_NAME]
    if existing:
        if not getattr(existing, "is_root", False):
            existing.is_root = True
        return existing

    # Avoid duplicate roots under other names.
    for dept in Department.instances():
        if getattr(dept, "is_root", False):
            logger.warning(
                f"ensure_root_org: found is_root org named {dept.name!r}; "
                f"expected {ROOT_ORG_NAME!r}"
            )
            return dept

    fund = Fund["ROOT"]
    if not fund:
        fund = Fund(
            code="ROOT",
            name="Root Department Fund",
            fund_type=FundType.GENERAL,
            description="Budget envelope for the quarter root department",
        )

    root = Department(
        name=ROOT_ORG_NAME,
        description="Quarter top governing department",
        is_root=True,
        policy_threshold_m=1,
        policy_threshold_n=1,
        policy_quorum_percent=0,
        fund=fund,
    )
    if head_user is not None:
        root.head = head_user
        try:
            from core.membership import add_department_member

            add_department_member(root, head_user)
        except Exception:
            pass

    logger.info("ensure_root_org: created root department")
    return root


def grant_root_authority_over_local_orgs():
    """Ensure root has default manage permissions over every other local org."""
    import uuid

    from ggg import ROOT_ORG_NAME, Department, DepartmentAuthority

    root = Department[ROOT_ORG_NAME] or ensure_root_org()
    perms = ",".join(ROOT_DEFAULT_OVER_ORG)

    for dept in Department.instances():
        if dept.name == ROOT_ORG_NAME or getattr(dept, "is_root", False):
            continue
        already = False
        for auth in getattr(root, "authorities_granted", []) or []:
            target = getattr(auth, "target", None)
            if target is not None and getattr(target, "name", None) == dept.name:
                already = True
                # Refresh permission set if empty.
                if not (auth.permissions or "").strip():
                    auth.permissions = perms
                break
        if already:
            continue
        DepartmentAuthority(
            id=f"auth-root-{dept.name}-{uuid.uuid4().hex[:8]}",
            grantor=root,
            target=dept,
            permissions=perms,
            description=f"Default root authority over {dept.name}",
        )


def org_has_authority(
    grantor_name: str,
    operation: str,
    *,
    target_name: Optional[str] = None,
    target_quarter_canister_id: str = "",
) -> bool:
    """Whether grantor org holds ``operation`` over the given target."""
    from ggg import Department

    grantor = Department[grantor_name]
    if not grantor:
        return False

    # Root defaults: full local manage if no explicit grants yet.
    if getattr(grantor, "is_root", False) and not target_quarter_canister_id:
        if operation in ROOT_DEFAULT_OVER_ORG:
            if target_name and target_name != grantor_name:
                return True

    for auth in getattr(grantor, "authorities_granted", []) or []:
        perms = auth.permission_list() if hasattr(auth, "permission_list") else []
        if operation not in perms and "org.*" not in perms and "all" not in perms:
            continue
        remote = (getattr(auth, "target_quarter_canister_id", None) or "").strip()
        if target_quarter_canister_id:
            if remote == target_quarter_canister_id and (
                not target_name
                or (getattr(auth, "target_org_name", None) or "") == target_name
            ):
                return True
            continue
        target = getattr(auth, "target", None)
        if target is not None and (
            not target_name or getattr(target, "name", None) == target_name
        ):
            return True
    return False


def apply_target_policies() -> dict:
    """Copy persisted ``target_policy_*`` onto live ``policy_*`` (issue #301).

    Runs at the alpha→beta transition. A department is skipped when
    ``target_policy_threshold_m`` is 0 (unset). Idempotent.
    """
    from ggg import Department

    applied = []
    for dept in Department.instances():
        m = int(getattr(dept, "target_policy_threshold_m", 0) or 0)
        if m <= 0:
            continue
        n = int(getattr(dept, "target_policy_threshold_n", 0) or 0)
        q = int(getattr(dept, "target_policy_quorum_percent", 0) or 0)
        dept.policy_threshold_m = m
        dept.policy_threshold_n = n
        dept.policy_quorum_percent = q
        applied.append(dept.name)
        logger.info(
            f"Applied target policy on {dept.name}: {m}/{n} quorum={q}"
        )
    return {"applied": applied, "count": len(applied)}

