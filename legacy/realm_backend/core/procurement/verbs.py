"""``procurement.*`` verbs (issue #276).

The bridge surface for the procurement extension. Each verb authenticates the
caller, checks the role the action needs, and calls into the domain modules. The
extension is left with argument parsing and response shaping.

Every verb takes ``caller`` from the host's dispatch, never from its arguments —
``RESERVED_KWARGS`` in :mod:`core.bridge_core` refuses identity-shaped arguments,
and nothing here reads one. That matters most for ``actor_id`` on a transition and
``vendor_id`` on a bid: both are attribution that ends up in the audit trail, and
both are set from the authenticated principal.
"""

import json
from typing import Any, Dict, Optional

from ic_python_logging import get_logger

from core.crypto_scopes import ScopeAuthContext, scope_kind
from core.procurement import entities, roles, scoring, seals, state_machine, vendors
from core.procurement.constants import ENCRYPTION_NONE, SEAL_SEALED

logger = get_logger("core.procurement")


# ---------------------------------------------------------------------------
# Key-envelope scope policy
# ---------------------------------------------------------------------------


@scope_kind("procurement")
def _manage_bid_scope(parts, caller, ctx: ScopeAuthContext) -> bool:
    """``procurement:rfp:<rfp_id>:bid:<bid_id>`` — who may share the bid key.

    The bidder, so they can wrap for the evaluators after reveal; the Procurement
    head and realm admins, so a tender is not held hostage by an absent vendor.
    Note this governs the *key*, not the ciphertext: reading the blob is
    :func:`core.procurement.seals.can_read_payload`.
    """
    if len(parts) < 5 or not parts[4]:
        return False
    payload = entities.find_payload(parts[4])
    if payload and caller == (getattr(payload, "created_by", "") or ""):
        return True
    if ctx.is_realm_admin(caller):
        return True
    return ctx.is_department_head(roles.PROCUREMENT_DEPARTMENT, caller)


# ---------------------------------------------------------------------------
# Projections
# ---------------------------------------------------------------------------


def project_transition(t) -> Dict[str, Any]:
    return {
        "transition_id": t.transition_id,
        "rfp_id": t.rfp_id,
        "from_status": t.from_status,
        "to_status": t.to_status,
        "actor_id": t.actor_id,
        "timestamp": int(t.timestamp or 0),
        "note": t.note or "",
        "metadata_json": t.metadata_json or "{}",
    }


def _sorted_transitions(rfp_id: str):
    return sorted(
        entities.list_rfp_transitions(rfp_id),
        key=lambda t: int(getattr(t, "timestamp", 0) or 0),
    )


def project_rfp(rfp, include_transitions: bool = False) -> Dict[str, Any]:
    out = {
        "rfp_id": rfp.rfp_id,
        "title": rfp.title or "",
        "description": rfp.description or "",
        "requester_id": rfp.requester_id or "",
        "status": rfp.status or "draft",
        "opens_at": int(rfp.opens_at or 0),
        "closes_at": int(rfp.closes_at or 0),
        "opened_at": int(rfp.opened_at or 0),
        "closed_at": int(rfp.closed_at or 0),
        "revealed_at": int(rfp.revealed_at or 0),
        "awarded_at": int(rfp.awarded_at or 0),
        "executed_at": int(rfp.executed_at or 0),
        "rubric_json": rfp.rubric_json or "[]",
        "winning_bid_id": rfp.winning_bid_id or "",
        "metadata_json": rfp.metadata_json or "{}",
    }
    if include_transitions:
        out["transitions"] = [
            project_transition(t) for t in _sorted_transitions(str(rfp.rfp_id))
        ]
    return out


def project_bid(bid, caller: str, include_payload: bool = False) -> Dict[str, Any]:
    """Bid metadata, with the ciphertext only if this caller may read it.

    The seal check happens per bid rather than once for the request: during
    ``open``, a vendor may read their own and no other, so a single yes/no for the
    whole listing would be wrong in both directions.
    """
    out = {
        "bid_id": bid.bid_id,
        "rfp_id": bid.rfp_id,
        "vendor_id": bid.vendor_id,
        "submitted_at": int(bid.submitted_at or 0),
        "seal_status": bid.seal_status or SEAL_SEALED,
        "total_score": float(bid.total_score or 0),
        "score_breakdown_json": bid.score_breakdown_json or "",
    }
    if include_payload:
        rfp = entities.find_rfp(str(bid.rfp_id))
        if rfp and seals.can_read_payload(bid, caller, rfp):
            payload = entities.find_payload(str(bid.bid_id))
            if payload:
                out["ciphertext"] = payload.ciphertext or ""
                out["encryption_mode"] = payload.encryption_mode or ENCRYPTION_NONE
                out["scope"] = payload.scope or ""
    return out


def project_score(row) -> Dict[str, Any]:
    return {
        "score_id": row.score_id,
        "bid_id": row.bid_id,
        "evaluator_id": row.evaluator_id,
        "criterion_id": row.criterion_id,
        "score": float(row.score or 0),
        "scored_at": int(row.scored_at or 0),
    }


# ---------------------------------------------------------------------------
# Argument coercion
# ---------------------------------------------------------------------------


def _rfp_id(value: Any) -> str:
    rfp_id = str(value or "").strip()
    if not rfp_id:
        raise ValueError("rfp_id is required")
    return rfp_id


def _bid_id(value: Any) -> str:
    bid_id = str(value or "").strip()
    if not bid_id:
        raise ValueError("bid_id is required")
    return bid_id


def _require_rfp(rfp_id: str):
    rfp = entities.find_rfp(rfp_id)
    if not rfp:
        raise ValueError(f"RFP '{rfp_id}' not found")
    return rfp


def _rubric_text(value: Any) -> str:
    """Accept a rubric as JSON text or as the parsed structure.

    The frontend sends both shapes depending on the form, and the difference is
    not worth pushing back onto the caller.
    """
    if isinstance(value, (list, dict)):
        value = json.dumps(value)
    text = str(value if value is not None else "[]")
    valid = scoring.validate_rubric(text)
    if not valid.get("valid"):
        raise ValueError(valid.get("error", "Invalid rubric"))
    return text


def _epoch(value: Any, field: str) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be an integer (epoch seconds)")


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def v_roles(caller: str = "", **kwargs) -> dict:
    return roles.describe(roles.get_user(caller))


def v_rfp_list(caller: str = "", status: str = "", **kwargs) -> dict:
    """Every RFP, optionally filtered by status.

    Deliberately visible to any member, including drafts: a tender is a public
    process, and what is confidential is the bids, not the fact of the tender.
    """
    roles.get_user(caller)
    wanted = str(status or "").strip()
    rfps = [
        project_rfp(rfp) for rfp in entities.all_rfps()
        if not wanted or (rfp.status or "") == wanted
    ]
    rfps.sort(key=lambda r: r.get("rfp_id", ""))
    return {"rfps": rfps, "count": len(rfps)}


def v_rfp_get(caller: str = "", rfp_id: Any = None, **kwargs) -> dict:
    roles.get_user(caller)
    rfp = _require_rfp(_rfp_id(rfp_id))
    return {"rfp": project_rfp(rfp, include_transitions=True)}


def v_transitions(caller: str = "", rfp_id: Any = None, **kwargs) -> dict:
    roles.get_user(caller)
    rfp_id = _rfp_id(rfp_id)
    return {
        "rfp_id": rfp_id,
        "transitions": [project_transition(t) for t in _sorted_transitions(rfp_id)],
    }


def v_bid_list(
    caller: str = "", rfp_id: Any = None, include_payload: bool = False, **kwargs
) -> dict:
    """Bid shells for an RFP, with ciphertext where the seal rules allow it.

    ``include_payload`` is a request, not a grant: it is passed straight to
    :func:`project_bid`, which asks :func:`~core.procurement.seals.can_read_payload`
    per bid. A caller with no evaluation role and no bid of their own therefore
    gets metadata only, whatever they ask for.
    """
    user = roles.get_user(caller)
    rfp_id = _rfp_id(rfp_id)
    _require_rfp(rfp_id)

    bids = [
        project_bid(b, str(user.id), include_payload=bool(include_payload))
        for b in entities.bids_for_rfp(rfp_id)
    ]
    return {"rfp_id": rfp_id, "bids": bids}


def v_bid_payload(caller: str = "", bid_id: Any = None, **kwargs) -> dict:
    user = roles.get_user(caller)
    bid_id = _bid_id(bid_id)
    bid = entities.find_bid(bid_id)
    if not bid:
        raise ValueError(f"Bid '{bid_id}' not found")
    rfp = entities.find_rfp(str(bid.rfp_id))
    if not rfp:
        raise ValueError("Parent RFP not found")
    if not seals.can_read_payload(bid, str(user.id), rfp):
        raise PermissionError("Not allowed to read bid payload")
    payload = entities.find_payload(bid_id)
    if not payload:
        raise ValueError("Payload not found")
    return {
        "bid_id": bid_id,
        "ciphertext": payload.ciphertext or "",
        "encryption_mode": payload.encryption_mode or ENCRYPTION_NONE,
        "scope": payload.scope or "",
        "seal_status": bid.seal_status or "",
    }


def v_score_list(caller: str = "", rfp_id: Any = None, **kwargs) -> dict:
    user = roles.get_user(caller)
    if not (
        roles.is_evaluator(user)
        or roles.is_approver(user)
        or roles.is_realm_admin(str(user.id))
    ):
        raise PermissionError("Evaluator, approver or admin required")
    rfp_id = _rfp_id(rfp_id)
    return {
        "rfp_id": rfp_id,
        "scores": [project_score(s) for s in entities.scores_for_rfp(rfp_id)],
    }


def v_evaluators(caller: str = "", **kwargs) -> dict:
    roles.get_user(caller)
    principals = roles.list_evaluator_principals()
    return {"principals": principals, "count": len(principals)}


def v_vendor_get(caller: str = "", vendor_id: Any = None, **kwargs) -> dict:
    roles.get_user(caller)
    record = entities.find_vendor(str(vendor_id or "").strip())
    return {"vendor": vendors.project(record) if record else None}


def v_vendor_list(caller: str = "", **kwargs) -> dict:
    user = roles.get_user(caller)
    if not roles.is_realm_admin(str(user.id)):
        raise PermissionError("Admin required")
    records = [vendors.project(v) for v in entities.all_vendors()]
    return {"vendors": records, "count": len(records)}


# ---------------------------------------------------------------------------
# Writes — RFP lifecycle
# ---------------------------------------------------------------------------


def v_rfp_create(
    caller: str = "",
    title: str = "",
    description: str = "",
    rubric_json: Any = None,
    opens_at: Any = 0,
    closes_at: Any = 0,
    **kwargs,
) -> dict:
    user = roles.get_user(caller)
    roles.require_op(user, roles.RFP_CREATE)

    title = str(title or "").strip()
    if not title:
        raise ValueError("title is required")

    rubric = _rubric_text(rubric_json if rubric_json is not None else "[]")
    opens = _epoch(opens_at, "opens_at")
    closes = _epoch(closes_at, "closes_at")
    if closes <= opens:
        raise ValueError("closes_at must be after opens_at")

    rfp_id = entities.next_rfp_id()
    rfp = entities.rfp_class()(
        rfp_id=rfp_id,
        title=title,
        description=str(description or ""),
        requester_id=str(user.id),
        status="draft",
        opens_at=opens,
        closes_at=closes,
        rubric_json=rubric,
        winning_bid_id="",
        metadata_json="{}",
    )
    state_machine.log_created(rfp_id, str(user.id))
    logger.info(f"procurement.rfp_create: {rfp_id} by {user.id}")
    return {"rfp": project_rfp(rfp)}


def v_rfp_update(caller: str = "", rfp_id: Any = None, fields: Optional[dict] = None, **kwargs) -> dict:
    """Edit a draft RFP.

    ``fields`` is a dict so that omitting a key means "leave it alone", which a
    verb signature with defaults cannot express. Only the four editable keys are
    read from it; anything else is refused rather than ignored.
    """
    user = roles.get_user(caller)
    rfp = _require_rfp(_rfp_id(rfp_id))

    if (rfp.status or "") != "draft":
        raise ValueError("Only draft RFPs may be edited")
    if str(rfp.requester_id) != str(user.id) and not roles.is_realm_admin(str(user.id)):
        raise PermissionError("Only the requester or admin may edit this RFP")

    fields = dict(fields or {})
    editable = {"title", "description", "opens_at", "closes_at", "rubric_json"}
    unknown = sorted(set(fields) - editable)
    if unknown:
        raise ValueError(
            f"cannot edit {', '.join(unknown)}; editable fields are "
            f"{sorted(editable)}"
        )

    if "title" in fields:
        title = str(fields["title"]).strip()
        if title:
            rfp.title = title
    if "description" in fields:
        rfp.description = str(fields["description"])
    if "opens_at" in fields:
        rfp.opens_at = _epoch(fields["opens_at"], "opens_at")
    if "closes_at" in fields:
        rfp.closes_at = _epoch(fields["closes_at"], "closes_at")
    if "rubric_json" in fields:
        rfp.rubric_json = _rubric_text(fields["rubric_json"])

    return {"rfp": project_rfp(rfp)}


def v_rfp_publish(caller: str = "", rfp_id: Any = None, **kwargs) -> dict:
    user = roles.get_user(caller)
    roles.require_op(user, roles.RFP_PUBLISH)
    rfp = _require_rfp(_rfp_id(rfp_id))

    # Re-validated at publish, not just at create: the rubric is what bidders bid
    # against, and it must be sound at the moment the window opens.
    _rubric_text(rfp.rubric_json or "[]")

    transition = state_machine.transition_rfp(
        str(rfp.rfp_id), "open", str(user.id),
        note="RFP published for bidding", user=user,
    )
    return {"rfp": project_rfp(_require_rfp(str(rfp.rfp_id))), "transition": transition}


def v_rfp_close(caller: str = "", rfp_id: Any = None, **kwargs) -> dict:
    """Close an RFP early and move it into evaluation.

    Admin only. The in-process version also accepted a ``force`` flag from the
    caller, which meant any bidder could close the window they were bidding into.
    """
    user = roles.get_user(caller)
    if not roles.is_realm_admin(str(user.id)):
        raise PermissionError("Admin required to close RFP manually")
    rfp_id = _rfp_id(rfp_id)
    transition = state_machine.close_and_evaluate(rfp_id, str(user.id))
    return {"rfp": project_rfp(_require_rfp(rfp_id)), "transition": transition}


def v_demo_advance(caller: str = "", rfp_id: Any = None, **kwargs) -> dict:
    user = roles.get_user(caller)
    rfp_id = _rfp_id(rfp_id)
    transition = state_machine.demo_advance(rfp_id, str(user.id), user=user)
    return {"rfp": project_rfp(_require_rfp(rfp_id)), "transition": transition}


def v_sweep(caller: str = "", **kwargs) -> dict:
    """Close every RFP whose window has ended. The extension's scheduled task.

    Admin-gated even though it is a scheduled call, because the schedule runs it
    as the realm and an extension could otherwise invoke it to force a close.
    """
    user = roles.get_user(caller)
    if not roles.is_realm_admin(str(user.id)):
        raise PermissionError("Admin required to run the procurement sweep")
    return state_machine.sweep_closed_rfps()


# ---------------------------------------------------------------------------
# Writes — bidding
# ---------------------------------------------------------------------------


def v_bid_create(caller: str = "", rfp_id: Any = None, **kwargs) -> dict:
    """Reserve a bid shell for the calling vendor.

    The vendor is the caller. There is no parameter for it, so one vendor cannot
    open a bid in another's name.
    """
    user = roles.get_user(caller)
    roles.require_op(user, roles.BID_SUBMIT)
    return seals.create_bid_shell(_rfp_id(rfp_id), str(user.id))


def v_bid_set_payload(
    caller: str = "",
    bid_id: Any = None,
    ciphertext: str = "",
    encryption_mode: str = "",
    **kwargs,
) -> dict:
    user = roles.get_user(caller)
    return seals.set_bid_payload(
        _bid_id(bid_id), str(user.id), str(ciphertext or ""),
        encryption_mode=str(encryption_mode or "").strip(),
    )


# ---------------------------------------------------------------------------
# Writes — scoring
# ---------------------------------------------------------------------------


def v_scores_submit(
    caller: str = "", bid_id: Any = None, scores: Optional[dict] = None, **kwargs
) -> dict:
    """Record one evaluator's scores for a bid.

    The evaluator is the caller, which is what makes the per-evaluator averaging
    in :func:`core.procurement.scoring.compute_totals` mean anything.
    """
    user = roles.get_user(caller)
    if not roles.is_evaluator(user):
        raise PermissionError("Evaluator role required")
    result = scoring.submit_scores(_bid_id(bid_id), str(user.id), dict(scores or {}))
    if not result.get("success"):
        raise ValueError(result.get("error", "Could not submit scores"))
    return {k: v for k, v in result.items() if k != "success"}


def v_totals_compute(caller: str = "", rfp_id: Any = None, **kwargs) -> dict:
    user = roles.get_user(caller)
    if not (roles.is_evaluator(user) or roles.is_realm_admin(str(user.id))):
        raise PermissionError("Evaluator or admin required")
    result = scoring.compute_totals(_rfp_id(rfp_id))
    if not result.get("success"):
        raise ValueError(result.get("error", "Could not compute totals"))
    return {k: v for k, v in result.items() if k != "success"}


# ---------------------------------------------------------------------------
# Writes — award, execution, vendor conduct
# ---------------------------------------------------------------------------


def v_award(
    caller: str = "", rfp_id: Any = None, winning_bid_id: Any = None, **kwargs
) -> dict:
    user = roles.get_user(caller)
    if not roles.is_approver(user):
        raise PermissionError("Approver role required")

    rfp_id = _rfp_id(rfp_id)
    winner = str(winning_bid_id or "").strip()
    if not winner:
        raise ValueError("winning_bid_id is required")

    rfp = _require_rfp(rfp_id)
    bid = entities.find_bid(winner)
    if not bid or str(bid.rfp_id) != rfp_id:
        raise ValueError("winning_bid_id does not belong to this RFP")

    rfp.winning_bid_id = winner
    transition = state_machine.transition_rfp(
        rfp_id, "award", str(user.id),
        note=f"Awarded to bid {winner}", user=user,
        metadata={"winning_bid_id": winner},
    )
    logger.info(f"procurement.award: {rfp_id} -> {winner} by {user.id}")
    return {"rfp": project_rfp(_require_rfp(rfp_id)), "transition": transition}


def v_execute(caller: str = "", rfp_id: Any = None, note: str = "", **kwargs) -> dict:
    user = roles.get_user(caller)
    roles.require_op(user, roles.EXECUTE)
    if not roles.is_approver(user):
        raise PermissionError("Approver role required")

    rfp_id = _rfp_id(rfp_id)
    transition = state_machine.transition_rfp(
        rfp_id, "contract_execution", str(user.id),
        note=str(note or "Contract execution stub (vault integration pending)"),
        user=user,
    )
    return {"rfp": project_rfp(_require_rfp(rfp_id)), "transition": transition}


def v_vendor_flag(
    caller: str = "",
    vendor_id: Any = None,
    code: str = "",
    note: str = "",
    rfp_id: str = "",
    bid_id: str = "",
    **kwargs,
) -> dict:
    user = roles.get_user(caller)
    if not roles.is_realm_admin(str(user.id)):
        raise PermissionError("Admin required")

    vendor = str(vendor_id or "").strip()
    code = str(code or "").strip()
    if not vendor or not code:
        raise ValueError("vendor_id and code are required")

    return vendors.flag(
        vendor, code, str(note or ""), str(rfp_id or ""), str(bid_id or ""),
        roles.now_epoch(),
    )


VERBS = {
    "procurement.roles": v_roles,
    "procurement.rfp_list": v_rfp_list,
    "procurement.rfp_get": v_rfp_get,
    "procurement.transitions": v_transitions,
    "procurement.bid_list": v_bid_list,
    "procurement.bid_payload": v_bid_payload,
    "procurement.score_list": v_score_list,
    "procurement.evaluators": v_evaluators,
    "procurement.vendor_get": v_vendor_get,
    "procurement.vendor_list": v_vendor_list,
    "procurement.rfp_create": v_rfp_create,
    "procurement.rfp_update": v_rfp_update,
    "procurement.rfp_publish": v_rfp_publish,
    "procurement.rfp_close": v_rfp_close,
    "procurement.demo_advance": v_demo_advance,
    "procurement.sweep": v_sweep,
    "procurement.bid_create": v_bid_create,
    "procurement.bid_set_payload": v_bid_set_payload,
    "procurement.scores_submit": v_scores_submit,
    "procurement.totals_compute": v_totals_compute,
    "procurement.award": v_award,
    "procurement.execute": v_execute,
    "procurement.vendor_flag": v_vendor_flag,
}

READ_VERBS = frozenset({
    "procurement.roles",
    "procurement.rfp_list",
    "procurement.rfp_get",
    "procurement.transitions",
    "procurement.bid_list",
    "procurement.bid_payload",
    "procurement.score_list",
    "procurement.evaluators",
    "procurement.vendor_get",
    "procurement.vendor_list",
})
