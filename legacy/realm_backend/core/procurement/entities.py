"""Access to the procurement extension's own entity rows.

The classes themselves are built from the manifest's ``entities`` block by
``core.extension_bridge.register_declared_entities``, which produces exactly the
namespaced classes ``create_extension_entity_class`` used to produce. This module
is the host-side accessor plus the id/sequence helpers.
"""

from typing import List

EXT_ID = "procurement"


def _cls(name: str):
    from core.extension_bridge import own_entity_class

    return own_entity_class(EXT_ID, name)


def rfp_class():
    return _cls("Rfp")


def transition_class():
    return _cls("RfpTransition")


def bid_class():
    return _cls("Bid")


def payload_class():
    return _cls("BidPayload")


def score_class():
    return _cls("BidScore")


def vendor_class():
    return _cls("VendorRecord")


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


def find_rfp(rfp_id: str):
    return rfp_class()[rfp_id]


def find_bid(bid_id: str):
    return bid_class()[bid_id]


def find_payload(bid_id: str):
    return payload_class()[bid_id]


def find_vendor(vendor_id: str):
    return vendor_class()[vendor_id]


def all_rfps() -> List:
    return list(rfp_class().instances())


def all_vendors() -> List:
    return list(vendor_class().instances())


def list_rfp_transitions(rfp_id: str) -> List:
    return [
        t for t in transition_class().instances()
        if getattr(t, "rfp_id", "") == rfp_id
    ]


def bids_for_rfp(rfp_id: str) -> List:
    return [
        b for b in bid_class().instances()
        if getattr(b, "rfp_id", "") == rfp_id
    ]


def scores_for_rfp(rfp_id: str) -> List:
    return [
        s for s in score_class().instances()
        if getattr(s, "rfp_id", "") == rfp_id
    ]


# ---------------------------------------------------------------------------
# Id allocation
# ---------------------------------------------------------------------------


def next_rfp_id() -> str:
    return f"rfp_{len(all_rfps()) + 1:03d}"


def next_transition_id(rfp_id: str) -> str:
    return f"{rfp_id}:{len(list_rfp_transitions(rfp_id)) + 1:03d}"


def next_bid_id(rfp_id: str) -> str:
    return f"bid_{rfp_id}_{len(bids_for_rfp(rfp_id)) + 1:03d}"
