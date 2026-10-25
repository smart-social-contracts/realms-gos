"""Vendor reputation records: award counts and conduct flags."""

import json

from core.procurement import entities


def get_or_create(vendor_id: str):
    record = entities.find_vendor(vendor_id)
    if record:
        return record
    return entities.vendor_class()(
        vendor_id=vendor_id,
        awards_count=0,
        last_award_at=0,
        last_rfp_id="",
        flags_json="[]",
    )


def update_on_award(winning_bid_id: str, rfp_id: str, ts: int) -> dict:
    bid = entities.find_bid(winning_bid_id)
    if not bid:
        raise ValueError("Winning bid not found")

    vendor_id = str(bid.vendor_id)
    record = get_or_create(vendor_id)
    record.awards_count = int(record.awards_count or 0) + 1
    record.last_award_at = ts
    record.last_rfp_id = rfp_id
    return {"vendor_id": vendor_id, "awards_count": record.awards_count}


def flag(
    vendor_id: str,
    code: str,
    note: str,
    rfp_id: str = "",
    bid_id: str = "",
    ts: int = 0,
) -> dict:
    """Append a conduct flag. Append-only: a flag is a record of a judgement, so
    it is added rather than replacing what is there."""
    record = get_or_create(vendor_id)
    try:
        flags = json.loads(record.flags_json or "[]")
    except (json.JSONDecodeError, TypeError):
        flags = []
    if not isinstance(flags, list):
        flags = []

    flags.append({
        "code": code, "rfp_id": rfp_id, "bid_id": bid_id,
        "at": ts, "note": note,
    })
    record.flags_json = json.dumps(flags, separators=(",", ":"))
    return {"vendor_id": vendor_id, "flags_count": len(flags)}


def project(record) -> dict:
    return {
        "vendor_id": record.vendor_id,
        "awards_count": int(record.awards_count or 0),
        "last_award_at": int(record.last_award_at or 0),
        "last_rfp_id": record.last_rfp_id or "",
        "flags_json": record.flags_json or "[]",
    }
