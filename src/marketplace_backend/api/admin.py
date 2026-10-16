"""Admin views across every developer: all listings, all users.

The public browse endpoints hide delisted items and the ``get_my_*`` ones
only show the caller's own. Admins need the whole picture, including what
was taken down, so these read the entities directly. Every function here
is admin-only (controllers included).
"""

from typing import Any, Dict, List

from _cdk import ic
from api.config import (
    caller_is_admin,
    get_admins,
    get_reviewers,
)
from api.developers import _listing, developer_name
from api.licenses import _to_dict as _license_dict, has_active_license
from core.models import (
    AssistantListingEntity,
    CodexListingEntity,
    DeveloperLicenseEntity,
    DeveloperProfileEntity,
    ExtensionListingEntity,
)
from ic_python_logging import get_logger

logger = get_logger("api.admin")

LISTING_KINDS = ("ext", "codex", "assistant")


def _is_controller() -> bool:
    try:
        return bool(ic.is_controller(ic.caller()))
    except Exception:
        return False


def _active(listing) -> bool:
    return bool(listing.is_active) if listing.is_active is not None else True


def my_roles() -> Dict[str, bool]:
    caller = str(ic.caller())
    is_controller = _is_controller()
    return {
        "is_controller": is_controller,
        "is_admin": is_controller or caller in get_admins(),
        "is_reviewer": is_controller or caller in get_admins() or caller in get_reviewers(),
    }


def _summary(kind: str, item_id: str, listing) -> Dict[str, Any]:
    developer = str(listing.developer or "")
    return {
        "item_kind": kind,
        "item_id": item_id,
        "name": str(listing.name or ""),
        "developer": developer,
        "developer_name": developer_name(developer),
        "version": str(listing.version or ""),
        "verification_status": str(listing.verification_status or "unverified"),
        "verification_notes": str(listing.verification_notes or ""),
        "is_active": _active(listing),
        "installs": int(listing.installs or 0),
        "likes": int(listing.likes or 0),
        "file_registry_namespace": str(listing.file_registry_namespace or ""),
        "created_at": float(listing.created_at or 0),
        "updated_at": float(listing.updated_at or 0),
    }


def list_all_listings() -> List[Dict[str, Any]]:
    """Every listing of every kind, delisted ones included, newest first.
    Empty for non-admins."""
    if not caller_is_admin():
        return []
    out: List[Dict[str, Any]] = []
    for e in ExtensionListingEntity.instances():
        out.append(_summary("ext", str(e.extension_id or ""), e))
    for c in CodexListingEntity.instances():
        out.append(_summary("codex", str(c.codex_id or ""), c))
    for a in AssistantListingEntity.instances():
        out.append(_summary("assistant", str(a.assistant_id or ""), a))
    out.sort(key=lambda r: r["updated_at"], reverse=True)
    return out


def set_listing_active(item_kind: str, item_id: str, active: bool) -> Dict:
    """Delist or relist anyone's listing. Relisting keeps the review state:
    the files have not changed while it was down."""
    if not caller_is_admin():
        return {"success": False, "error": "Unauthorized: admins only"}
    if item_kind not in LISTING_KINDS:
        return {"success": False, "error": f"item_kind must be one of {LISTING_KINDS}"}
    listing = _listing(item_kind, item_id)
    if listing is None:
        return {"success": False, "error": f"{item_kind}:{item_id} not found"}
    listing.is_active = bool(active)
    listing.updated_at = float(ic.time())
    action = "relisted" if active else "delisted"
    logger.info(f"{item_kind}:{item_id} {action} by {ic.caller()}")
    return {"success": True, "action": action}


def list_users() -> List[Dict[str, Any]]:
    """Everyone the marketplace knows about: listing owners, license holders,
    named or suspended principals, reviewers and admins. Empty for
    non-admins."""
    if not caller_is_admin():
        return []

    counts: Dict[str, Dict[str, int]] = {}

    def _count(principal: str, key: str) -> None:
        if principal:
            counts.setdefault(principal, {"ext": 0, "codex": 0, "assistant": 0})[key] += 1

    for e in ExtensionListingEntity.instances():
        _count(str(e.developer or ""), "ext")
    for c in CodexListingEntity.instances():
        _count(str(c.developer or ""), "codex")
    for a in AssistantListingEntity.instances():
        _count(str(a.developer or ""), "assistant")

    licenses = {str(lic.principal): lic for lic in DeveloperLicenseEntity.instances()}
    profiles = {str(p.principal): p for p in DeveloperProfileEntity.instances()}
    reviewers = set(get_reviewers())
    admins = set(get_admins())

    principals = set(counts) | set(licenses) | set(profiles) | reviewers | admins
    out: List[Dict[str, Any]] = []
    for p in principals:
        if not p:
            continue
        profile = profiles.get(p)
        lic = licenses.get(p)
        c = counts.get(p, {"ext": 0, "codex": 0, "assistant": 0})
        lic_d = _license_dict(lic) if lic is not None else None
        out.append({
            "principal": p,
            "display_name": str(profile.display_name or "") if profile else "",
            "is_admin": p in admins,
            "is_reviewer": p in reviewers,
            "is_suspended": bool(profile.suspended) if profile else False,
            "suspended_reason": str(profile.suspended_reason or "") if profile else "",
            "has_license": lic is not None,
            "license_active": has_active_license(p),
            "license_expires_at": float(lic_d["expires_at"]) if lic_d else 0.0,
            "license_payment_method": lic_d["payment_method"] if lic_d else "",
            "license_note": lic_d["note"] if lic_d else "",
            "extensions_count": c["ext"],
            "codices_count": c["codex"],
            "assistants_count": c["assistant"],
        })
    out.sort(key=lambda u: (not u["is_admin"], u["display_name"] == "", u["display_name"].lower(), u["principal"]))
    return out
