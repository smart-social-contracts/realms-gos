"""Verification — developer requests audit, controller flips status.

Lifecycle:
    unverified ──request_audit──▶ pending_audit ──set_verification_status──▶ verified | rejected

Only license-holders who are not suspended may call ``request_audit``;
only reviewers (admins and controllers included) may flip the final status. The browse / search endpoints accept a
``verified_only`` flag that gates by ``status == "verified"``.
"""

from typing import Any, Dict, List

from _cdk import ic
from api.config import is_reviewer
from api.developers import is_suspended
from api.licenses import has_active_license
from core.models import (
    AssistantListingEntity,
    CodexListingEntity,
    ExtensionListingEntity,
)
from ic_python_logging import get_logger

logger = get_logger("api.verification")


VALID_STATUSES = (
    "unverified",
    "pending_review",
    "pending_audit",
    "verified",
    "rejected",
)
VALID_KINDS = ("ext", "codex", "assistant")

# Statuses that put a listing in front of a reviewer.
QUEUED_STATUSES = ("pending_review", "pending_audit")


def _is_controller() -> bool:
    try:
        return bool(ic.is_controller(ic.caller()))
    except Exception:
        return False


def _caller_is_reviewer() -> bool:
    return is_reviewer(str(ic.caller()))


def _safe_codex_alias(codex_id: str) -> str:
    return (codex_id or "").replace("/", "__")


def _safe_assistant_alias(assistant_id: str) -> str:
    return (assistant_id or "").replace("/", "__")


def _get_listing(item_kind: str, item_id: str):
    if item_kind == "ext":
        return ExtensionListingEntity[item_id]
    if item_kind == "codex":
        return CodexListingEntity[_safe_codex_alias(item_id)]
    if item_kind == "assistant":
        return AssistantListingEntity[_safe_assistant_alias(item_id)]
    return None


def request_audit(*, caller: str, item_kind: str, item_id: str) -> Dict:
    if item_kind not in VALID_KINDS:
        return {"success": False, "error": f"item_kind must be one of {VALID_KINDS}"}
    if is_suspended(caller):
        return {"success": False, "error": "This account is suspended"}
    if not has_active_license(caller):
        return {"success": False, "error": "Active developer license required to request an audit"}
    listing = _get_listing(item_kind, item_id)
    if listing is None:
        return {"success": False, "error": "Listing not found"}
    if str(listing.developer) != caller and not _is_controller():
        return {"success": False, "error": "Only the listing owner can request an audit"}
    listing.verification_status = "pending_audit"
    listing.verification_notes = "Audit requested by developer"
    logger.info(f"audit requested for {item_kind}:{item_id} by {caller}")
    return {"success": True, "verification_status": "pending_audit"}


def set_verification_status(*, item_kind: str, item_id: str, status: str, notes: str) -> Dict:
    """Record a review decision on the listing.

    This only moves the marketplace's own record. Making the decision count
    for realms means stamping the approval onto the file registry, which is
    what ``api.approval.approve_listing`` does — prefer that for approvals.
    """
    if not _caller_is_reviewer():
        return {"success": False, "error": "Unauthorized: reviewers only"}
    if item_kind not in VALID_KINDS:
        return {"success": False, "error": f"item_kind must be one of {VALID_KINDS}"}
    if status not in VALID_STATUSES:
        return {"success": False, "error": f"status must be one of {VALID_STATUSES}"}
    listing = _get_listing(item_kind, item_id)
    if listing is None:
        return {"success": False, "error": "Listing not found"}
    listing.verification_status = status
    listing.verification_notes = notes or ""
    logger.info(f"verification {item_kind}:{item_id} -> {status} (notes: {notes})")
    return {"success": True, "verification_status": status}


def list_pending_audits() -> List[Dict[str, Any]]:
    """The reviewer queue: everything waiting on a decision."""
    if not _caller_is_reviewer():
        return []
    out: List[Dict[str, Any]] = []
    for e in ExtensionListingEntity.instances():
        if str(e.verification_status) in QUEUED_STATUSES:
            out.append({
                "item_kind": "ext",
                "item_id": str(e.extension_id),
                "name": str(e.name),
                "developer": str(e.developer),
                "version": str(e.version),
                "updated_at": float(e.updated_at or 0),
            })
    for c in CodexListingEntity.instances():
        if str(c.verification_status) in QUEUED_STATUSES:
            out.append({
                "item_kind": "codex",
                "item_id": str(c.codex_id),
                "name": str(c.name),
                "developer": str(c.developer),
                "version": str(c.version),
                "updated_at": float(c.updated_at or 0),
            })
    for a in AssistantListingEntity.instances():
        if str(a.verification_status) in QUEUED_STATUSES:
            out.append({
                "item_kind": "assistant",
                "item_id": str(a.assistant_id),
                "name": str(a.name),
                "developer": str(a.developer),
                "version": str(a.version),
                "updated_at": float(a.updated_at or 0),
            })
    out.sort(key=lambda r: r.get("updated_at", 0), reverse=True)
    return out
