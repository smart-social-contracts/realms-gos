"""Assistant listings — CRUD, purchase, query, my-listings.

A direct mirror of ``api/codices.py`` but with the AI-specific fields
called out in plan v2.1 (runtime, endpoint, base model, requested
role / permissions, training disclosure, eval report). Same upsert
semantics, same ownership-or-controller checks, same idempotent buy.

The marketplace stores **what the assistant is** (catalog metadata
and a pointer into a file_registry namespace where prompts, MCP
manifests, and eval transcripts live). Actual inference and the
realm-side runtime are out of scope for this canister.
"""

from typing import Any, Dict, List, Optional

from _cdk import ic
from api.developers import developer_name
from core.models import AssistantListingEntity, PurchaseEntity
from ic_python_logging import get_logger

logger = get_logger("api.assistants")


VALID_RUNTIMES = ("runpod", "openai", "anthropic", "on_chain_llm", "self_hosted")


def _is_controller() -> bool:
    try:
        return bool(ic.is_controller(ic.caller()))
    except Exception:
        return False


def _now() -> float:
    return float(ic.time())


def _safe_assistant_alias(assistant_id: str) -> str:
    """Replace path separators so the value is safe to use as the entity alias."""
    return (assistant_id or "").replace("/", "__")


def _to_dict(a: AssistantListingEntity) -> Dict[str, Any]:
    return {
        "assistant_id": str(a.assistant_id or ""),
        "assistant_alias": str(a.assistant_alias or ""),
        "developer": str(a.developer or ""),
        "developer_name": developer_name(str(a.developer or "")),
        "name": str(a.name or ""),
        "description": str(a.description or ""),
        "version": str(a.version or ""),
        "price_e8s": int(a.price_e8s or 0),
        "pricing_summary": str(a.pricing_summary or ""),
        "icon": str(a.icon or ""),
        "categories": str(a.categories or ""),
        "runtime": str(a.runtime or ""),
        "endpoint_url": str(a.endpoint_url or ""),
        "base_model": str(a.base_model or ""),
        "requested_role": str(a.requested_role or ""),
        "requested_permissions": str(a.requested_permissions or ""),
        "domains": str(a.domains or ""),
        "languages": str(a.languages or ""),
        "training_data_summary": str(a.training_data_summary or ""),
        "eval_report_url": str(a.eval_report_url or ""),
        "file_registry_canister_id": str(a.file_registry_canister_id or ""),
        "file_registry_namespace": str(a.file_registry_namespace or ""),
        "installs": int(a.installs or 0),
        "likes": int(a.likes or 0),
        "verification_status": str(a.verification_status or "unverified"),
        "verification_notes": str(a.verification_notes or ""),
        "is_active": bool(a.is_active) if a.is_active is not None else True,
        "created_at": float(a.created_at or 0),
        "updated_at": float(a.updated_at or 0),
    }


def _validate_id(assistant_id: str) -> Optional[str]:
    if not assistant_id or not assistant_id.strip():
        return "assistant_id is required"
    if ".." in assistant_id:
        return "assistant_id must not contain '..'"
    if len(assistant_id) > 128:
        return "assistant_id too long"
    if assistant_id.count("/") > 1:
        return "assistant_id may contain at most one '/'"
    return None


def create_assistant(
    *,
    developer: str,
    assistant_id: str,
    name: str,
    description: str,
    version: str,
    price_e8s: int,
    pricing_summary: str,
    icon: str,
    categories: str,
    runtime: str,
    endpoint_url: str,
    base_model: str,
    requested_role: str,
    requested_permissions: str,
    domains: str,
    languages: str,
    training_data_summary: str,
    eval_report_url: str,
    file_registry_canister_id: str,
    file_registry_namespace: str,
) -> Dict:
    err = _validate_id(assistant_id)
    if err:
        return {"success": False, "error": err}
    if runtime and runtime not in VALID_RUNTIMES:
        return {"success": False, "error": f"runtime must be one of {VALID_RUNTIMES} (got {runtime!r})"}

    alias = _safe_assistant_alias(assistant_id)
    now = _now()
    existing = AssistantListingEntity[alias]
    if existing is not None:
        if str(existing.developer) != developer and not _is_controller():
            return {"success": False, "error": "Not the owner of this assistant"}
        existing.name = name
        existing.description = description
        existing.version = version
        existing.price_e8s = int(price_e8s)
        existing.pricing_summary = pricing_summary
        existing.icon = icon
        existing.categories = categories
        existing.runtime = runtime
        existing.endpoint_url = endpoint_url
        existing.base_model = base_model
        existing.requested_role = requested_role
        existing.requested_permissions = requested_permissions
        existing.domains = domains
        existing.languages = languages
        existing.training_data_summary = training_data_summary
        existing.eval_report_url = eval_report_url
        existing.file_registry_canister_id = file_registry_canister_id
        existing.file_registry_namespace = file_registry_namespace
        existing.is_active = True
        existing.updated_at = now
        # Updating an assistant version sends it back to "unverified" — same
        # rule as extensions/codices, so an audit can't be quietly carried
        # forward across a code change.
        if existing.verification_status in (None, "rejected", "pending_audit"):
            existing.verification_status = "unverified"
            existing.verification_notes = ""
        elif existing.verification_status == "verified":
            existing.verification_status = "unverified"
            existing.verification_notes = "Verification reset on version update"
        logger.info(f"updated assistant listing {assistant_id} v{version}")
        return {"success": True, "assistant_id": assistant_id, "action": "updated"}

    AssistantListingEntity(
        assistant_alias=alias,
        assistant_id=assistant_id,
        developer=developer,
        name=name,
        description=description,
        version=version,
        price_e8s=int(price_e8s),
        pricing_summary=pricing_summary,
        icon=icon,
        categories=categories,
        runtime=runtime,
        endpoint_url=endpoint_url,
        base_model=base_model,
        requested_role=requested_role,
        requested_permissions=requested_permissions,
        domains=domains,
        languages=languages,
        training_data_summary=training_data_summary,
        eval_report_url=eval_report_url,
        file_registry_canister_id=file_registry_canister_id,
        file_registry_namespace=file_registry_namespace,
        installs=0,
        likes=0,
        verification_status="unverified",
        verification_notes="",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    logger.info(f"created assistant listing {assistant_id} v{version} by {developer}")
    return {"success": True, "assistant_id": assistant_id, "action": "created"}


def delist_assistant(developer: str, assistant_id: str) -> Dict:
    alias = _safe_assistant_alias(assistant_id)
    a = AssistantListingEntity[alias]
    if a is None:
        return {"success": False, "error": "Assistant not found"}
    if str(a.developer) != developer and not _is_controller():
        return {"success": False, "error": "Not the owner"}
    a.is_active = False
    a.updated_at = _now()
    logger.info(f"delisted assistant {assistant_id}")
    return {"success": True, "message": f"Assistant '{assistant_id}' delisted"}


def get_assistant_details(assistant_id: str) -> Dict:
    alias = _safe_assistant_alias(assistant_id)
    a = AssistantListingEntity[alias]
    if a is None or not (a.is_active if a.is_active is not None else True):
        return {"success": False, "error": "Assistant not found"}
    return {"success": True, "assistant": _to_dict(a)}


def list_assistants(page: int, per_page: int, verified_only: bool) -> Dict:
    page = max(1, int(page or 1))
    per_page = max(1, min(int(per_page or 20), 100))
    all_active = [
        a for a in AssistantListingEntity.instances()
        if (a.is_active if a.is_active is not None else True)
    ]
    if verified_only:
        all_active = [a for a in all_active if str(a.verification_status) == "verified"]
    all_active.sort(key=lambda a: float(a.updated_at or 0), reverse=True)
    total = len(all_active)
    start = (page - 1) * per_page
    end = start + per_page
    listings = [_to_dict(a) for a in all_active[start:end]]
    return {
        "success": True,
        "listings": listings,
        "total_count": total,
        "page": page,
        "per_page": per_page,
    }


def search_assistants(query: str, verified_only: bool) -> List[Dict]:
    q = (query or "").lower().strip()
    out = []
    for a in AssistantListingEntity.instances():
        if not (a.is_active if a.is_active is not None else True):
            continue
        if verified_only and str(a.verification_status) != "verified":
            continue
        haystack = " ".join([
            str(a.name or ""), str(a.description or ""), str(a.categories or ""),
            str(a.assistant_id or ""), str(a.domains or ""), str(a.base_model or ""),
            str(a.requested_role or ""), str(a.runtime or ""),
            str(a.languages or ""),
        ]).lower()
        if q and q in haystack:
            out.append(_to_dict(a))
        elif not q:
            out.append(_to_dict(a))
    out.sort(key=lambda r: r.get("installs", 0), reverse=True)
    return out


def get_developer_assistants(developer: str) -> List[Dict]:
    return [
        _to_dict(a) for a in AssistantListingEntity.instances()
        if str(a.developer) == developer
    ]


# ---------------------------------------------------------------------------
# Purchases
# ---------------------------------------------------------------------------

def buy_assistant(realm_principal: str, assistant_id: str) -> Dict:
    """Record that ``realm_principal`` has hired ``assistant_id``.

    The actual realm-side hiring flow (governance proposal → vote →
    runtime install) is out of scope for the marketplace canister.
    This endpoint is the bookkeeping leg — increments the install
    count and credits the developer balance just like buy_extension /
    buy_codex.
    """
    alias = _safe_assistant_alias(assistant_id)
    a = AssistantListingEntity[alias]
    if a is None or not (a.is_active if a.is_active is not None else True):
        return {"success": False, "error": "Assistant not found"}

    for p in PurchaseEntity.instances():
        if (
            str(p.realm_principal) == realm_principal
            and str(p.item_kind) == "assistant"
            and str(p.item_id) == assistant_id
        ):
            return {"success": True, "purchase_id": str(p.purchase_id), "action": "exists"}

    now = _now()
    purchase_id = f"assistant_{realm_principal}_{alias}_{int(now)}"
    PurchaseEntity(
        purchase_id=purchase_id,
        realm_principal=realm_principal,
        item_kind="assistant",
        item_id=assistant_id,
        developer=str(a.developer),
        price_paid_e8s=int(a.price_e8s or 0),
        purchased_at=now,
    )
    a.installs = int(a.installs or 0) + 1
    logger.info(f"hired assistant {assistant_id} by {realm_principal}")
    return {"success": True, "purchase_id": purchase_id, "action": "created"}


def has_purchased_assistant(realm_principal: str, assistant_id: str) -> bool:
    for p in PurchaseEntity.instances():
        if (
            str(p.realm_principal) == realm_principal
            and str(p.item_kind) == "assistant"
            and str(p.item_id) == assistant_id
        ):
            return True
    return False
