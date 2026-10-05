"""Codex listings — same shape as extensions but with slash-safe aliasing.

Codex IDs commonly contain a ``"/"`` (e.g. ``"syntropia/membership"``).
ic_python_db's ``__alias__`` is used as the storage key, and "/" is
unsafe there, so we maintain two parallel forms:

  * ``codex_id``     — original, e.g. ``"syntropia/membership"``.
  * ``codex_alias``  — alias used as entity key, e.g. ``"syntropia__membership"``.

Lookups accept the original id; we convert before hitting the entity.
"""

from typing import Any, Dict, List, Optional

from _cdk import ic
from api.developers import developer_name
from api.licenses import has_active_license
from core.models import CodexListingEntity, PurchaseEntity
from ic_python_logging import get_logger

logger = get_logger("api.codices")


def _is_controller() -> bool:
    try:
        return bool(ic.is_controller(ic.caller()))
    except Exception:
        return False


def _now() -> float:
    return float(ic.time())


def _safe_codex_alias(codex_id: str) -> str:
    """Replace path separators so the value is safe to use as the entity alias."""
    return (codex_id or "").replace("/", "__")


def _to_dict(c: CodexListingEntity) -> Dict[str, Any]:
    return {
        "codex_id": str(c.codex_id or ""),
        "codex_alias": str(c.codex_alias or ""),
        "realm_type": str(c.realm_type or ""),
        "developer": str(c.developer or ""),
        "developer_name": developer_name(str(c.developer or "")),
        "name": str(c.name or ""),
        "description": str(c.description or ""),
        "version": str(c.version or ""),
        "price_e8s": int(c.price_e8s or 0),
        "icon": str(c.icon or ""),
        "categories": str(c.categories or ""),
        "file_registry_canister_id": str(c.file_registry_canister_id or ""),
        "file_registry_namespace": str(c.file_registry_namespace or ""),
        "installs": int(c.installs or 0),
        "likes": int(c.likes or 0),
        "verification_status": str(c.verification_status or "unverified"),
        "verification_notes": str(c.verification_notes or ""),
        "is_active": bool(c.is_active) if c.is_active is not None else True,
        "created_at": float(c.created_at or 0),
        "updated_at": float(c.updated_at or 0),
    }


def _validate_id(codex_id: str) -> Optional[str]:
    if not codex_id or not codex_id.strip():
        return "codex_id is required"
    if ".." in codex_id:
        return "codex_id must not contain '..'"
    if len(codex_id) > 128:
        return "codex_id too long"
    # one optional path level only ("realm_type/codex_id")
    if codex_id.count("/") > 1:
        return "codex_id may contain at most one '/'"
    return None


def create_codex(
    *,
    developer: str,
    codex_id: str,
    realm_type: str,
    name: str,
    description: str,
    version: str,
    price_e8s: int,
    icon: str,
    categories: str,
    file_registry_canister_id: str,
    file_registry_namespace: str,
) -> Dict:
    err = _validate_id(codex_id)
    if err:
        return {"success": False, "error": err}

    if not has_active_license(developer) and not _is_controller():
        return {
            "success": False,
            "error": "An active developer license is required to publish to the marketplace",
        }

    alias = _safe_codex_alias(codex_id)
    now = _now()
    existing = CodexListingEntity[alias]
    if existing is not None:
        if str(existing.developer) != developer and not _is_controller():
            return {"success": False, "error": "Not the owner of this codex"}
        existing.realm_type = realm_type
        existing.name = name
        existing.description = description
        existing.version = version
        existing.price_e8s = int(price_e8s)
        existing.icon = icon
        existing.categories = categories
        existing.file_registry_canister_id = file_registry_canister_id
        existing.file_registry_namespace = file_registry_namespace
        existing.is_active = True
        existing.updated_at = now
        # New bytes, new review (issue #267).
        was_verified = existing.verification_status == "verified"
        existing.verification_status = "pending_review"
        existing.verification_notes = (
            "Review reset on version update" if was_verified else ""
        )
        logger.info(f"updated codex listing {codex_id} v{version}")
        return {
            "success": True,
            "codex_id": codex_id,
            "action": "updated",
            "verification_status": "pending_review",
        }

    CodexListingEntity(
        codex_alias=alias,
        codex_id=codex_id,
        realm_type=realm_type,
        developer=developer,
        name=name,
        description=description,
        version=version,
        price_e8s=int(price_e8s),
        icon=icon,
        categories=categories,
        file_registry_canister_id=file_registry_canister_id,
        file_registry_namespace=file_registry_namespace,
        installs=0,
        likes=0,
        verification_status="pending_review",
        verification_notes="",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    logger.info(f"created codex listing {codex_id} v{version} by {developer}")
    return {"success": True, "codex_id": codex_id, "action": "created"}


def delist_codex(developer: str, codex_id: str) -> Dict:
    alias = _safe_codex_alias(codex_id)
    c = CodexListingEntity[alias]
    if c is None:
        return {"success": False, "error": "Codex not found"}
    if str(c.developer) != developer and not _is_controller():
        return {"success": False, "error": "Not the owner"}
    c.is_active = False
    c.updated_at = _now()
    logger.info(f"delisted codex {codex_id}")
    return {"success": True, "message": f"Codex '{codex_id}' delisted"}


def get_codex_details(codex_id: str) -> Dict:
    alias = _safe_codex_alias(codex_id)
    c = CodexListingEntity[alias]
    if c is None or not (c.is_active if c.is_active is not None else True):
        return {"success": False, "error": "Codex not found"}
    return {"success": True, "codex": _to_dict(c)}


def list_codices(page: int, per_page: int, verified_only: bool) -> Dict:
    page = max(1, int(page or 1))
    per_page = max(1, min(int(per_page or 20), 100))
    all_active = [
        c for c in CodexListingEntity.instances()
        if (c.is_active if c.is_active is not None else True)
    ]
    if verified_only:
        all_active = [c for c in all_active if str(c.verification_status) == "verified"]
    all_active.sort(key=lambda c: float(c.updated_at or 0), reverse=True)
    total = len(all_active)
    start = (page - 1) * per_page
    end = start + per_page
    listings = [_to_dict(c) for c in all_active[start:end]]
    return {
        "success": True,
        "listings": listings,
        "total_count": total,
        "page": page,
        "per_page": per_page,
    }


def search_codices(query: str, verified_only: bool) -> List[Dict]:
    q = (query or "").lower().strip()
    out = []
    for c in CodexListingEntity.instances():
        if not (c.is_active if c.is_active is not None else True):
            continue
        if verified_only and str(c.verification_status) != "verified":
            continue
        name = str(c.name or "").lower()
        desc = str(c.description or "").lower()
        cats = str(c.categories or "").lower()
        cid = str(c.codex_id or "").lower()
        rtype = str(c.realm_type or "").lower()
        if q and (q in name or q in desc or q in cats or q in cid or q in rtype):
            out.append(_to_dict(c))
        elif not q:
            out.append(_to_dict(c))
    out.sort(key=lambda r: r.get("installs", 0), reverse=True)
    return out


def get_developer_codices(developer: str) -> List[Dict]:
    return [
        _to_dict(c) for c in CodexListingEntity.instances()
        if str(c.developer) == developer
    ]


# ---------------------------------------------------------------------------
# Purchases
# ---------------------------------------------------------------------------

def buy_codex(realm_principal: str, codex_id: str) -> Dict:
    alias = _safe_codex_alias(codex_id)
    c = CodexListingEntity[alias]
    if c is None or not (c.is_active if c.is_active is not None else True):
        return {"success": False, "error": "Codex not found"}

    for p in PurchaseEntity.instances():
        if (
            str(p.realm_principal) == realm_principal
            and str(p.item_kind) == "codex"
            and str(p.item_id) == codex_id
        ):
            return {"success": True, "purchase_id": str(p.purchase_id), "action": "exists"}

    now = _now()
    purchase_id = f"codex_{realm_principal}_{alias}_{int(now)}"
    PurchaseEntity(
        purchase_id=purchase_id,
        realm_principal=realm_principal,
        item_kind="codex",
        item_id=codex_id,
        developer=str(c.developer),
        price_paid_e8s=int(c.price_e8s or 0),
        purchased_at=now,
    )
    c.installs = int(c.installs or 0) + 1
    logger.info(f"bought codex {codex_id} by {realm_principal}")
    return {"success": True, "purchase_id": purchase_id, "action": "created"}


def has_purchased_codex(realm_principal: str, codex_id: str) -> bool:
    for p in PurchaseEntity.instances():
        if (
            str(p.realm_principal) == realm_principal
            and str(p.item_kind) == "codex"
            and str(p.item_id) == codex_id
        ):
            return True
    return False
