"""Extension listings — CRUD, purchase, query, my-listings.

The marketplace stores **metadata + a pointer** to a file_registry
namespace; actual files are uploaded directly from the developer's
browser to the file_registry (see file-registry-client.ts on the
frontend). Purchase increments ``installs`` (the new counter that
``top_*_by_downloads`` ranks on).
"""

from typing import Any, Dict, List, Optional

from _cdk import ic
from api.developers import developer_name
from api.licenses import has_active_license
from core.models import ExtensionListingEntity, PurchaseEntity
from ic_python_logging import get_logger

logger = get_logger("api.extensions")


VERIFY_STATUSES = (
    "unverified",
    "pending_review",
    "pending_audit",
    "verified",
    "rejected",
)


def _is_controller() -> bool:
    try:
        return bool(ic.is_controller(ic.caller()))
    except Exception:
        return False


def _now() -> float:
    return float(ic.time())


def _to_dict(ext: ExtensionListingEntity) -> Dict[str, Any]:
    return {
        "extension_id": str(ext.extension_id or ""),
        "developer": str(ext.developer or ""),
        "developer_name": developer_name(str(ext.developer or "")),
        "name": str(ext.name or ""),
        "description": str(ext.description or ""),
        "version": str(ext.version or ""),
        "price_e8s": int(ext.price_e8s or 0),
        "icon": str(ext.icon or ""),
        "categories": str(ext.categories or ""),
        "screenshots": str(ext.screenshots or ""),
        "file_registry_canister_id": str(ext.file_registry_canister_id or ""),
        "file_registry_namespace": str(ext.file_registry_namespace or ""),
        "download_url": str(ext.download_url or ""),
        "installs": int(ext.installs or 0),
        "likes": int(ext.likes or 0),
        "verification_status": str(ext.verification_status or "unverified"),
        "verification_notes": str(ext.verification_notes or ""),
        "is_active": bool(ext.is_active) if ext.is_active is not None else True,
        "created_at": float(ext.created_at or 0),
        "updated_at": float(ext.updated_at or 0),
    }


def _validate_id(extension_id: str) -> Optional[str]:
    if not extension_id or not extension_id.strip():
        return "extension_id is required"
    if "/" in extension_id or ".." in extension_id:
        return "extension_id must not contain '/' or '..'"
    if len(extension_id) > 128:
        return "extension_id too long"
    return None


def create_extension(
    *,
    developer: str,
    extension_id: str,
    name: str,
    description: str,
    version: str,
    price_e8s: int,
    icon: str,
    categories: str,
    screenshots: str,
    file_registry_canister_id: str,
    file_registry_namespace: str,
    download_url: str = "",
) -> Dict:
    """Submit (or resubmit) an extension listing for review.

    Requires an active developer license (issue #267): the license is what
    ties a submission to an accountable identity, so it gates publishing and
    not just audit eligibility. Updates additionally require ownership
    (developer == caller) unless the caller is a controller.

    A submission is never live on arrival — it starts in ``pending_review``
    and a reviewer decides. Resubmitting resets that, since the reviewed
    bytes are no longer the bytes on offer.
    """
    err = _validate_id(extension_id)
    if err:
        return {"success": False, "error": err}

    if not has_active_license(developer) and not _is_controller():
        return {
            "success": False,
            "error": "An active developer license is required to publish to the marketplace",
        }

    now = _now()
    existing = ExtensionListingEntity[extension_id]
    if existing is not None:
        if str(existing.developer) != developer and not _is_controller():
            return {"success": False, "error": "Not the owner of this extension"}
        existing.name = name
        existing.description = description
        existing.version = version
        existing.price_e8s = int(price_e8s)
        existing.icon = icon
        existing.categories = categories
        existing.screenshots = screenshots
        existing.file_registry_canister_id = file_registry_canister_id
        existing.file_registry_namespace = file_registry_namespace
        existing.download_url = download_url
        existing.is_active = True
        existing.updated_at = now
        # New bytes mean the previous review no longer describes what is on
        # offer, whatever it concluded. Back to the queue.
        was_verified = existing.verification_status == "verified"
        existing.verification_status = "pending_review"
        existing.verification_notes = (
            "Review reset on version update" if was_verified else ""
        )
        logger.info(f"updated extension listing {extension_id} v{version}")
        return {
            "success": True,
            "extension_id": extension_id,
            "action": "updated",
            "verification_status": "pending_review",
        }

    ExtensionListingEntity(
        extension_id=extension_id,
        developer=developer,
        name=name,
        description=description,
        version=version,
        price_e8s=int(price_e8s),
        icon=icon,
        categories=categories,
        screenshots=screenshots,
        file_registry_canister_id=file_registry_canister_id,
        file_registry_namespace=file_registry_namespace,
        download_url=download_url,
        installs=0,
        likes=0,
        verification_status="pending_review",
        verification_notes="",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    logger.info(f"created extension listing {extension_id} v{version} by {developer}")
    return {
        "success": True,
        "extension_id": extension_id,
        "action": "created",
        "verification_status": "pending_review",
    }


def delist_extension(developer: str, extension_id: str) -> Dict:
    ext = ExtensionListingEntity[extension_id]
    if ext is None:
        return {"success": False, "error": "Extension not found"}
    if str(ext.developer) != developer and not _is_controller():
        return {"success": False, "error": "Not the owner"}
    ext.is_active = False
    ext.updated_at = _now()
    logger.info(f"delisted extension {extension_id}")
    return {"success": True, "message": f"Extension '{extension_id}' delisted"}


def get_extension_details(extension_id: str) -> Dict:
    ext = ExtensionListingEntity[extension_id]
    if ext is None or not (ext.is_active if ext.is_active is not None else True):
        return {"success": False, "error": "Extension not found"}
    return {"success": True, "extension": _to_dict(ext)}


def list_extensions(page: int, per_page: int, verified_only: bool) -> Dict:
    page = max(1, int(page or 1))
    per_page = max(1, min(int(per_page or 20), 100))
    all_active = [
        e for e in ExtensionListingEntity.instances()
        if (e.is_active if e.is_active is not None else True)
    ]
    if verified_only:
        all_active = [e for e in all_active if str(e.verification_status) == "verified"]
    all_active.sort(key=lambda e: float(e.updated_at or 0), reverse=True)
    total = len(all_active)
    start = (page - 1) * per_page
    end = start + per_page
    listings = [_to_dict(e) for e in all_active[start:end]]
    return {
        "success": True,
        "listings": listings,
        "total_count": total,
        "page": page,
        "per_page": per_page,
    }


def search_extensions(query: str, verified_only: bool) -> List[Dict]:
    q = (query or "").lower().strip()
    results = []
    for e in ExtensionListingEntity.instances():
        if not (e.is_active if e.is_active is not None else True):
            continue
        if verified_only and str(e.verification_status) != "verified":
            continue
        name = str(e.name or "").lower()
        desc = str(e.description or "").lower()
        cats = str(e.categories or "").lower()
        if q and (q in name or q in desc or q in cats or q in str(e.extension_id or "").lower()):
            results.append(_to_dict(e))
        elif not q:
            results.append(_to_dict(e))
    results.sort(key=lambda r: r.get("installs", 0), reverse=True)
    return results


def get_developer_extensions(developer: str) -> List[Dict]:
    return [
        _to_dict(e) for e in ExtensionListingEntity.instances()
        if str(e.developer) == developer
    ]


# ---------------------------------------------------------------------------
# Purchases
# ---------------------------------------------------------------------------

def buy_extension(realm_principal: str, extension_id: str) -> Dict:
    ext = ExtensionListingEntity[extension_id]
    if ext is None or not (ext.is_active if ext.is_active is not None else True):
        return {"success": False, "error": "Extension not found"}

    # Idempotent — repeat buys by the same principal are no-ops, return the
    # existing purchase id. Otherwise the install count would over-inflate.
    for p in PurchaseEntity.instances():
        if (
            str(p.realm_principal) == realm_principal
            and str(p.item_kind) == "ext"
            and str(p.item_id) == extension_id
        ):
            return {"success": True, "purchase_id": str(p.purchase_id), "action": "exists"}

    now = _now()
    purchase_id = f"ext_{realm_principal}_{extension_id}_{int(now)}"
    PurchaseEntity(
        purchase_id=purchase_id,
        realm_principal=realm_principal,
        item_kind="ext",
        item_id=extension_id,
        developer=str(ext.developer),
        price_paid_e8s=int(ext.price_e8s or 0),
        purchased_at=now,
    )
    ext.installs = int(ext.installs or 0) + 1
    logger.info(f"bought ext {extension_id} by {realm_principal}")
    return {"success": True, "purchase_id": purchase_id, "action": "created"}


def has_purchased_extension(realm_principal: str, extension_id: str) -> bool:
    for p in PurchaseEntity.instances():
        if (
            str(p.realm_principal) == realm_principal
            and str(p.item_kind) == "ext"
            and str(p.item_id) == extension_id
        ):
            return True
    return False


def get_my_purchases(realm_principal: str) -> List[Dict]:
    out = []
    for p in PurchaseEntity.instances():
        if str(p.realm_principal) != realm_principal:
            continue
        out.append({
            "purchase_id": str(p.purchase_id),
            "item_kind": str(p.item_kind),
            "item_id": str(p.item_id),
            "developer": str(p.developer),
            "price_paid_e8s": int(p.price_paid_e8s or 0),
            "purchased_at": float(p.purchased_at or 0),
        })
    out.sort(key=lambda r: r.get("purchased_at", 0), reverse=True)
    return out
