"""``treasury.*`` verbs (issue #269).

Epoch-based allocation of recognized revenue into department funds — the GGG
treasury standard from #261. The read model already lives in
:mod:`core.treasury_allocation`; the reads here are thin gated wrappers over it.

The writes are the interesting half. Every mutating treasury action is governed
by the org linked to the source fund (ROOT by default): a 1/1 policy applies
directly, anything else becomes an org-scoped proposal that replays the action on
approval. In-process, ``budget_manager`` made that whole decision itself — the
rights check, the direct-vs-vote split, and the construction of the proposal.

Two things about that are worth not reproducing across a sandbox boundary:

* **The action dict is replayed.** ``apply_treasury_action`` dispatches on it,
  and ``build_treasury_proposal_code`` embeds it in code that runs after a vote
  passes. A verb accepting an arbitrary dict would let the extension put anything
  in there, so :data:`ACTION_FIELDS` is a per-kind allowlist and the action is
  rebuilt here from scratch rather than forwarded.
* **``triggered_by`` is attribution.** It ends up in the audit trail and in the
  proposal description. It is set from the authenticated caller and an extension
  cannot supply it — ``RESERVED_KWARGS`` in the bridge already refuses
  identity-shaped arguments, and the rebuild means there is nowhere to hide one.

Disabling the automatic schedule is deliberately the one write that is always
direct for a manager: switching automation off is the safe direction, and making
it wait for a vote would mean money keeps moving while the vote runs.
"""

import json
from typing import Any, Dict, FrozenSet, Optional

from ic_python_logging import get_logger

logger = get_logger("core.treasury_bridge")

# Per-kind field allowlist. The action is rebuilt from these keys only, so a key
# the extension invents cannot reach `apply_treasury_action` or the proposal code.
ACTION_FIELDS: Dict[str, FrozenSet[str]] = {
    "set_rule": frozenset({"rules", "description"}),
    "run_allocation": frozenset({"period"}),
    "set_epoch": frozenset({"epoch_length", "anchor_month", "epoch_minutes"}),
    "set_schedule": frozenset({"enabled", "auto_allocate"}),
    "issue_report": frozenset({"as_of", "period"}),
}

DEFAULT_VOTING_WINDOW_SECONDS = 604_800


# ---------------------------------------------------------------------------
# Who governs, and who may act
# ---------------------------------------------------------------------------


def governing_department():
    """Org whose policy gates treasury actions: the source fund's org, else root."""
    from ggg import ROOT_ORG_NAME, Department

    try:
        from core.treasury_allocation import _source_fund

        fund = _source_fund()
        dept = getattr(fund, "department", None) if fund else None
    except Exception:
        dept = None
    return dept or Department[ROOT_ORG_NAME]


def _caller_user(caller: str):
    from ggg import User

    user = User[caller]
    if not user:
        raise PermissionError(f"User {caller} not found")
    return user


def _has_operation(user, operation: str) -> bool:
    from ggg.system.user_profile import OPERATIONS_SEPARATOR, Operations

    for profile in user.profiles or []:
        allowed = str(profile.allowed_to or "").split(OPERATIONS_SEPARATOR)
        if Operations.ALL in allowed or operation in allowed:
            return True
    try:
        for permission in user.permissions:
            if permission.name == operation:
                return True
    except Exception:
        pass
    return False


def _in_root(user) -> bool:
    from ggg import ROOT_ORG_NAME, Department

    root = Department[ROOT_ORG_NAME]
    if not root:
        return False
    head = getattr(root, "head", None)
    if head is not None and getattr(head, "id", None) == user.id:
        return True
    try:
        from core.membership import user_in_department

        return bool(user_in_department(user, root))
    except Exception:
        return False


def can_manage(user, department) -> bool:
    """Admin, org-appoint rights, the treasury org's head, or a root member."""
    from ggg.system.user_profile import Operations

    if _has_operation(user, Operations.ALL):
        return True
    if _has_operation(user, Operations.ORG_APPOINT):
        return True
    head = getattr(department, "head", None) if department else None
    if head is not None and getattr(head, "id", None) == user.id:
        return True
    return _in_root(user)


def format_policy(department) -> str:
    m = int(getattr(department, "policy_threshold_m", 1) or 1)
    n = int(getattr(department, "policy_threshold_n", 1) or 1)
    quorum = int(getattr(department, "policy_quorum_percent", 0) or 0)
    veto = (getattr(department, "policy_veto_principals", "") or "").strip()

    label = f"{m}/{n}"
    extras = []
    if quorum > 0:
        extras.append(f"quorum {quorum}%")
    if veto:
        extras.append("veto")
    if extras:
        label = f"{label} ({', '.join(extras)})"
    return label


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def _require_member(caller: str):
    """Any registered member may look at the treasury read model."""
    return _caller_user(caller)


def v_overview(caller: str = "", **kwargs) -> dict:
    from core.treasury_allocation import treasury_overview

    _require_member(caller)
    data = treasury_overview()
    department = governing_department()
    data["governed_by"] = department.name if department else None
    data["governed_policy"] = format_policy(department) if department else ""
    return data


def _unwrap(result: dict) -> dict:
    if isinstance(result, dict) and result.get("error"):
        raise ValueError(result["error"])
    return result


def v_allocation_status(
    caller: str = "", period: Optional[str] = None, **kwargs
) -> dict:
    from core.treasury_allocation import allocation_status

    _require_member(caller)
    return _unwrap(allocation_status(period))


def v_flows(caller: str = "", period: Optional[str] = None, **kwargs) -> dict:
    from core.treasury_allocation import allocation_flows

    _require_member(caller)
    return _unwrap(allocation_flows(period))


def v_budgets(caller: str = "", period: Optional[str] = None, **kwargs) -> dict:
    from core.treasury_allocation import budgets_for_period

    _require_member(caller)
    return _unwrap(budgets_for_period(period))


def v_timeline(
    caller: str = "",
    center_ts: Optional[Any] = None,
    before: int = 20,
    after: int = 20,
    **kwargs,
) -> dict:
    from core.treasury_allocation import epoch_timeline

    _require_member(caller)
    return epoch_timeline(center_ts=center_ts, before=before, after=after)


# ---------------------------------------------------------------------------
# The one write verb
# ---------------------------------------------------------------------------


def _build_action(kind: str, fields: Dict[str, Any], caller: str) -> Dict[str, Any]:
    """Rebuild the action from allowlisted keys, with attribution set here.

    Unknown keys are refused rather than dropped: this dict is replayed after a
    vote, so quietly discarding part of it would mean the thing voted on is not
    the thing described.
    """
    allowed = ACTION_FIELDS.get(kind)
    if allowed is None:
        raise ValueError(
            f"unknown treasury action '{kind}'; known: {sorted(ACTION_FIELDS)}"
        )

    unknown = sorted(set(fields) - allowed)
    if unknown:
        raise ValueError(
            f"treasury action '{kind}' does not take {', '.join(unknown)}; "
            f"it takes {sorted(allowed)}"
        )

    action: Dict[str, Any] = {"kind": kind}

    if kind == "set_rule":
        rules = fields.get("rules")
        if not isinstance(rules, list) or not rules:
            raise ValueError("set_rule requires a non-empty 'rules' list")
        action["rules"] = rules
        action["description"] = str(fields.get("description") or "")
    elif kind == "run_allocation":
        action["period"] = str(fields.get("period") or "")
    elif kind == "set_epoch":
        epoch_length = str(fields.get("epoch_length") or "").strip()
        if not epoch_length:
            raise ValueError("set_epoch requires 'epoch_length'")
        action["epoch_length"] = epoch_length
        if fields.get("anchor_month") is not None:
            action["anchor_month"] = int(fields["anchor_month"])
        if fields.get("epoch_minutes") is not None:
            action["epoch_minutes"] = int(fields["epoch_minutes"])
    elif kind == "set_schedule":
        action["enabled"] = bool(fields.get("enabled"))
        if fields.get("auto_allocate") is not None:
            action["auto_allocate"] = bool(fields["auto_allocate"])
    elif kind == "issue_report":
        action["as_of"] = str(fields.get("as_of") or "")
        action["period"] = str(fields.get("period") or "")

    action["triggered_by"] = caller
    return action


def _confirmation(department, summary: str) -> dict:
    """What the UI shows before a proposal is created.

    A vote is a public, durable act, so it is never started as a side effect of
    a button press — the caller has to come back with ``confirm``.
    """
    policy = format_policy(department)
    return {
        "requires_confirmation": True,
        "summary": summary,
        "governed_by": department.name,
        "policy": policy.split(" (")[0],
        "governed_policy": policy,
        "policy_reason": (
            f"Treasury actions are governed by {department.name}'s policy "
            f"({policy}); a vote is required before this change can apply."
        ),
        "voters_org": department.name,
    }


def _voting_deadline_seconds() -> int:
    from _cdk import ic

    window = DEFAULT_VOTING_WINDOW_SECONDS
    try:
        from ggg import Realm

        realm = Realm[1]
        if realm and realm.calendar and realm.calendar.voting_window:
            window = int(realm.calendar.voting_window)
    except Exception:
        pass
    return ic.time() // 1_000_000_000 + window


def _submit_proposal(action: dict, department, summary: str, proposer) -> dict:
    from core.treasury_allocation import build_treasury_proposal_code
    from ggg import Proposal

    proposal_id = f"prop_{len(Proposal.instances()) + 1:03d}"
    metadata = {
        "proposal_type": "treasury_action",
        "org_scope": department.name,
        "treasury_action": action,
        "code_inline": build_treasury_proposal_code(action),
        "codex_name": f"treasury_action_{proposal_id}",
    }

    proposal = Proposal(
        proposal_id=proposal_id,
        title=summary,
        description=(
            f"Treasury action governed by '{department.name}' "
            f"(policy {department.policy_threshold_m}/"
            f"{department.policy_threshold_n}). Proposed by {proposer.id}."
        ),
        code_url="",
        code_checksum="",
        proposer=proposer,
        status="voting",
        voting_deadline=str(_voting_deadline_seconds()),
        votes_yes=0.0,
        votes_no=0.0,
        votes_abstain=0.0,
        total_voters=0.0,
        required_threshold=1.0,
        org_scope=department.name,
        metadata=json.dumps(metadata),
    )
    logger.info(
        f"treasury proposal {proposal_id} submitted for '{department.name}': {summary}"
    )
    return {
        "proposal_id": proposal.proposal_id,
        "status": proposal.status,
        "org_scope": department.name,
    }


def v_action(
    caller: str = "",
    kind: str = "",
    fields: Optional[dict] = None,
    confirm: bool = False,
    **kwargs,
) -> dict:
    """Apply a treasury action directly, or open a vote on it.

    One verb rather than four, because the direct-vs-proposal decision is the
    same for every kind and is exactly the part that must not be re-implemented
    per action.
    """
    from core.position_admin import policy_is_direct
    from core.treasury_allocation import (
        apply_treasury_action,
        describe_treasury_action,
    )

    user = _caller_user(caller)
    department = governing_department()
    if not can_manage(user, department):
        raise PermissionError("treasury actions require admin/head rights")

    action = _build_action(kind, dict(fields or {}), user.id)

    if policy_is_direct(department):
        result = _unwrap(apply_treasury_action(action))
        logger.info(f"treasury {kind} applied directly by {user.id}")
        return {**result, "applied": "direct", "governed_by": department.name}

    summary = describe_treasury_action(action)
    if not confirm:
        return _confirmation(department, summary)

    data = _submit_proposal(action, department, summary, user)
    return {**data, "applied": "proposal", "summary": summary}


def v_disable_schedule(caller: str = "", **kwargs) -> dict:
    """Turn the automatic sweep + allocation off, without a vote.

    Separate from :func:`v_action` on purpose. Enabling a standing automatic
    money movement is policy-gated, but disabling one is the safe direction — a
    vote there would mean the money keeps moving while it runs.
    """
    from core.treasury_allocation import set_treasury_schedule

    user = _caller_user(caller)
    department = governing_department()
    if not can_manage(user, department):
        raise PermissionError("treasury actions require admin/head rights")

    result = _unwrap(set_treasury_schedule(False, triggered_by=user.id))
    return {**result, "applied": "direct"}


def v_issue_draft(caller: str = "", **kwargs) -> dict:
    """Freeze current books as an unofficial draft. Not a money movement."""
    from core.financial_reports import issue_draft

    user = _require_member(caller)
    return _unwrap(issue_draft(issued_by=user.id))


VERBS = {
    "treasury.overview": v_overview,
    "treasury.allocation_status": v_allocation_status,
    "treasury.flows": v_flows,
    "treasury.budgets": v_budgets,
    "treasury.timeline": v_timeline,
    "treasury.action": v_action,
    "treasury.disable_schedule": v_disable_schedule,
    "treasury.issue_draft": v_issue_draft,
}

READ_VERBS = frozenset(
    {
        "treasury.overview",
        "treasury.allocation_status",
        "treasury.flows",
        "treasury.budgets",
        "treasury.timeline",
    }
)
