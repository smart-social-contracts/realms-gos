"""RFP lifecycle, with append-only transition logging.

``draft -> open -> closed -> evaluation -> award -> contract_execution``, and
nothing else: :data:`VALID_TRANSITIONS` is the whole graph, so a status cannot be
reached by any route but the one intended. Every change writes an
``RfpTransition`` row, which is what makes the tender auditable after the fact.

:func:`transition_rfp` is the single entry point. Its preconditions are the
time-and-role rules that keep the process honest — a tender that could be closed
early, or awarded without an approver, is not a tender.
"""

import json
from typing import Callable, Optional

from core.procurement import entities, roles
from core.procurement.constants import RFP_STATUSES, VALID_TRANSITIONS

# Which timestamp field each status stamps on arrival.
_ARRIVAL_FIELD = {
    "open": "opened_at",
    "closed": "closed_at",
    "evaluation": "revealed_at",
    "award": "awarded_at",
    "contract_execution": "executed_at",
}


def _stamp_arrival(rfp, to_status: str, ts: int) -> None:
    field = _ARRIVAL_FIELD.get(to_status)
    if field:
        setattr(rfp, field, ts)


def _precondition_error(rfp, to_status: str, actor_id: str, user) -> Optional[str]:
    now = roles.now_epoch()
    from_status = rfp.status or "draft"

    if to_status == "open":
        if from_status != "draft":
            return "Can only publish from draft"
        if actor_id != rfp.requester_id and not roles.is_realm_admin(actor_id):
            return "Only the requester or an admin may publish"
        if rfp.opens_at and now < int(rfp.opens_at):
            return "Cannot publish before opens_at"
        if rfp.closes_at and now > int(rfp.closes_at):
            return "Cannot publish after closes_at"

    elif to_status == "closed":
        if from_status != "open":
            return "Can only close from open"
        if rfp.closes_at and now < int(rfp.closes_at):
            return "Bidding window has not ended"
        if actor_id != roles.SYSTEM_ACTOR and not roles.is_realm_admin(actor_id):
            return "Admin or scheduled task required to close RFP"

    elif to_status == "evaluation":
        if from_status != "closed":
            return "Can only enter evaluation from closed"

    elif to_status == "award":
        if from_status != "evaluation":
            return "Can only award from evaluation"
        if user is not None and not roles.is_approver(user):
            return "Approver role required"
        if not (rfp.winning_bid_id or "").strip():
            return "winning_bid_id must be set before award transition"

    elif to_status == "contract_execution":
        if from_status != "award":
            return "Can only execute from award"
        if user is not None and not roles.is_approver(user):
            return "Approver role required"

    return None


def transition_rfp(
    rfp_id: str,
    to_status: str,
    actor_id: str,
    note: str = "",
    metadata: Optional[dict] = None,
    user=None,
    on_enter: Optional[Callable] = None,
    skip_preconditions: bool = False,
) -> dict:
    """Move an RFP to *to_status*, logging the transition.

    *skip_preconditions* bypasses the time and role gates and is reachable only
    from the test-mode demo path — see :func:`demo_advance`. The transition graph
    itself is never bypassed.
    """
    if to_status not in RFP_STATUSES:
        raise ValueError(f"Unknown status '{to_status}'")

    rfp = entities.find_rfp(rfp_id)
    if not rfp:
        raise ValueError(f"RFP '{rfp_id}' not found")

    from_status = rfp.status or "draft"
    if to_status not in VALID_TRANSITIONS.get(from_status, set()):
        raise ValueError(f"Invalid transition {from_status} -> {to_status}")

    if not skip_preconditions:
        error = _precondition_error(rfp, to_status, actor_id, user)
        if error:
            raise PermissionError(error)

    ts = roles.now_epoch()
    transition_id = entities.next_transition_id(rfp_id)
    entities.transition_class()(
        transition_id=transition_id,
        rfp_id=rfp_id,
        from_status=from_status,
        to_status=to_status,
        actor_id=actor_id,
        timestamp=ts,
        note=note or "",
        metadata_json=json.dumps(metadata or {}, separators=(",", ":")),
    )

    rfp.status = to_status
    _stamp_arrival(rfp, to_status, ts)

    if on_enter:
        on_enter(rfp, actor_id)

    if to_status == "evaluation":
        from core.procurement import seals

        seals.reveal_bids(rfp_id)

    if to_status == "award" and rfp.winning_bid_id:
        from core.procurement import vendors

        vendors.update_on_award(str(rfp.winning_bid_id), rfp_id, ts)

    return {
        "rfp_id": rfp_id,
        "transition_id": transition_id,
        "from_status": from_status,
        "to_status": to_status,
    }


def log_created(rfp_id: str, actor_id: str, note: str = "RFP created") -> dict:
    """Audit entry for a newly created RFP, which starts life already in draft."""
    ts = roles.now_epoch()
    transition_id = entities.next_transition_id(rfp_id)
    entities.transition_class()(
        transition_id=transition_id,
        rfp_id=rfp_id,
        from_status="",
        to_status="draft",
        actor_id=actor_id,
        timestamp=ts,
        note=note,
        metadata_json="{}",
    )
    return {"transition_id": transition_id}


def close_and_evaluate(
    rfp_id: str, actor_id: str = roles.SYSTEM_ACTOR, skip_preconditions: bool = False
) -> dict:
    """Close an open RFP and move it straight into evaluation.

    One step for callers, because a closed RFP with its bids still sealed is not a
    state anyone wants to be left in.
    """
    transition_rfp(
        rfp_id, "closed", actor_id,
        note="Bidding window closed",
        skip_preconditions=skip_preconditions,
    )
    return transition_rfp(
        rfp_id, "evaluation", actor_id,
        note="Bids revealed for evaluation",
        skip_preconditions=skip_preconditions,
    )


def demo_advance(rfp_id: str, actor_id: str, user=None) -> dict:
    """Advance one stage, bypassing the time and role gates. Test mode only.

    Gated on ``core.runtime_flags.is_test_mode`` *and* on the caller being the
    requester or an admin, because it is a deliberate hole in the process rules
    and must not be reachable on a live realm.
    """
    from core.runtime_flags import is_test_mode

    if not is_test_mode():
        raise PermissionError("Demo advance is only available in test mode")

    rfp = entities.find_rfp(rfp_id)
    if not rfp:
        raise ValueError(f"RFP '{rfp_id}' not found")

    if actor_id != rfp.requester_id and not roles.is_realm_admin(actor_id):
        raise PermissionError(
            "Only the requester or an admin may demo-advance this request"
        )

    from_status = rfp.status or "draft"

    if from_status == "draft":
        return transition_rfp(
            rfp_id, "open", actor_id, note="Demo: published for bidding",
            user=user, skip_preconditions=True,
        )
    if from_status == "open":
        return close_and_evaluate(rfp_id, actor_id, skip_preconditions=True)
    if from_status == "closed":
        return transition_rfp(
            rfp_id, "evaluation", actor_id,
            note="Demo: bids revealed for evaluation",
            user=user, skip_preconditions=True,
        )
    if from_status == "evaluation":
        if not (rfp.winning_bid_id or "").strip():
            bids = entities.bids_for_rfp(rfp_id)
            if not bids:
                raise ValueError(
                    "Add at least one bid before demo-advancing to award"
                )
            rfp.winning_bid_id = str(bids[0].bid_id)
        return transition_rfp(
            rfp_id, "award", actor_id,
            note=f"Demo: awarded to bid {rfp.winning_bid_id}",
            user=user, metadata={"winning_bid_id": rfp.winning_bid_id},
            skip_preconditions=True,
        )
    if from_status == "award":
        return transition_rfp(
            rfp_id, "contract_execution", actor_id,
            note="Demo: contract execution recorded",
            user=user, skip_preconditions=True,
        )
    raise ValueError("Already at the final stage")


def sweep_closed_rfps() -> dict:
    """Close every open RFP whose bidding window has ended.

    The scheduled counterpart to :func:`close_and_evaluate`: it runs as
    ``SYSTEM_ACTOR``, which is the only actor the ``closed`` precondition accepts
    besides an admin. One failure does not stop the sweep.
    """
    now = roles.now_epoch()
    processed = 0
    errors = []
    for rfp in entities.all_rfps():
        if (rfp.status or "") != "open":
            continue
        if not rfp.closes_at or now < int(rfp.closes_at):
            continue
        try:
            close_and_evaluate(str(rfp.rfp_id), roles.SYSTEM_ACTOR)
            processed += 1
        except Exception as e:
            errors.append({"rfp_id": rfp.rfp_id, "error": str(e)})
    return {"processed": processed, "errors": errors}
