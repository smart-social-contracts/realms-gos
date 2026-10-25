"""Sealed bidding: bid shells, encrypted payloads, and who may read them.

A bid is two rows. ``Bid`` is the visible shell — who bid, when, what it scored.
``BidPayload`` is the opaque ciphertext, readable only under the rules in
:func:`can_read_payload`.

That function is the confidentiality guarantee of the whole extension, which is
why it is here and not in sandboxed code. While an RFP is open, a sealed bid is
readable *only by its own bidder*: not by evaluators, not by the requester, not by
an admin. Sealed bidding is worthless if the party running the tender can read the
bids while the window is still open.
"""

from core.procurement import entities, roles
from core.procurement.constants import (
    ENCRYPTION_VETKEYS,
    SEAL_REVEALED,
    SEAL_SEALED,
)

BID_SCOPE_TEMPLATE = "procurement:rfp:{rfp_id}:bid:{bid_id}"


def create_bid_shell(rfp_id: str, vendor_id: str) -> dict:
    """Reserve a bid id and key scope for a vendor.

    Two-step like the document flow: the scope embeds the bid id, so the vendor
    cannot encrypt until the shell exists.
    """
    rfp = entities.find_rfp(rfp_id)
    if not rfp:
        raise ValueError(f"RFP '{rfp_id}' not found")
    if (rfp.status or "") != "open":
        raise ValueError("RFP is not open for bids")

    now = roles.now_epoch()
    if rfp.closes_at and now > int(rfp.closes_at):
        raise ValueError("Bidding window has closed")

    for existing in entities.bids_for_rfp(rfp_id):
        if str(existing.vendor_id) == vendor_id:
            raise ValueError("Vendor already has a bid on this RFP")

    bid_id = entities.next_bid_id(rfp_id)
    scope = BID_SCOPE_TEMPLATE.format(rfp_id=rfp_id, bid_id=bid_id)

    entities.bid_class()(
        bid_id=bid_id,
        rfp_id=rfp_id,
        vendor_id=vendor_id,
        submitted_at=now,
        seal_status=SEAL_SEALED,
        total_score=0.0,
        score_breakdown_json="",
    )
    entities.payload_class()(
        bid_id=bid_id,
        ciphertext="",
        scope=scope,
        encryption_mode=ENCRYPTION_VETKEYS,
        created_by=vendor_id,
    )

    return {
        "bid_id": bid_id,
        "scope": scope,
        "encryption_mode": ENCRYPTION_VETKEYS,
    }


def set_bid_payload(
    bid_id: str, vendor_id: str, ciphertext: str, encryption_mode: str = ""
) -> dict:
    """Attach or replace a bid's ciphertext.

    Allowed in exactly two states: the initial sealed submission while the RFP is
    open, and a re-wrap during evaluation so the revealed bid can be read by the
    evaluators. Anything else — notably editing a sealed bid after the window
    closes — is refused.
    """
    bid = entities.find_bid(bid_id)
    if not bid:
        raise ValueError(f"Bid '{bid_id}' not found")
    if str(bid.vendor_id) != vendor_id:
        raise PermissionError("Only the bidder may set payload")

    rfp = entities.find_rfp(str(bid.rfp_id))
    if not rfp:
        raise ValueError("Parent RFP not found")

    status = rfp.status or ""
    seal = bid.seal_status or SEAL_SEALED
    initial_submit = status == "open" and seal == SEAL_SEALED
    rewrap = status == "evaluation" and seal == SEAL_REVEALED
    if not (initial_submit or rewrap):
        raise ValueError("Bid payload cannot be updated in this state")

    if status == "open":
        now = roles.now_epoch()
        if rfp.closes_at and now > int(rfp.closes_at):
            raise ValueError("Bidding window has closed")

    if not ciphertext or not str(ciphertext).strip():
        raise ValueError("ciphertext is required")

    payload = entities.find_payload(bid_id)
    if not payload:
        raise ValueError("Bid payload record missing")

    payload.ciphertext = str(ciphertext)
    mode = (encryption_mode or "").strip() or ENCRYPTION_VETKEYS
    payload.encryption_mode = mode
    return {"bid_id": bid_id, "encryption_mode": mode}


def reveal_bids(rfp_id: str) -> dict:
    """Flip every sealed bid to revealed. Called by the state machine on entering
    evaluation, never directly by a caller."""
    revealed = 0
    for bid in entities.bids_for_rfp(rfp_id):
        if (bid.seal_status or "") == SEAL_SEALED:
            bid.seal_status = SEAL_REVEALED
            revealed += 1
    return {"rfp_id": rfp_id, "revealed": revealed}


def can_read_payload(bid, caller: str, rfp) -> bool:
    """Whether *caller* may read this bid's ciphertext.

    The sealed-bidding rule: a bidder always sees their own, and nobody else sees
    anything until the RFP leaves ``open``. After that, evaluators, approvers and
    admins may read, because that is what evaluation is.
    """
    status = rfp.status or ""
    seal = bid.seal_status or SEAL_SEALED

    if str(bid.vendor_id) == caller:
        return True

    if status == "open" and seal == SEAL_SEALED:
        return False

    if status in ("evaluation", "award", "contract_execution"):
        if roles.is_realm_admin(caller):
            return True
        try:
            user = roles.get_user(caller)
        except PermissionError:
            return False
        return roles.is_evaluator(user) or roles.is_approver(user)

    return False
