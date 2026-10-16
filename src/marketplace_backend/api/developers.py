"""Developer profiles (display name, suspension) and listing ownership transfer.

A listing's ``developer`` is the principal that created it and the only one
(besides admins) that may update or delist it. ``DeveloperProfileEntity``
gives that principal a public name and records whether it is suspended;
``transfer_listing`` hands a listing to another principal without touching
its version or review state.
"""

import json
from typing import Dict

from _cdk import ic
from api.config import caller_is_admin, get_admins
from core.models import (
    AssistantListingEntity,
    CodexListingEntity,
    DeveloperProfileEntity,
    ExtensionListingEntity,
)
from ic_python_logging import get_logger

logger = get_logger("api.developers")

MAX_NAME_LENGTH = 64
MAX_REASON_LENGTH = 512
TRANSFER_KINDS = ("ext", "codex", "assistant")


def _now() -> float:
    return float(ic.time())


def developer_name(principal: str) -> str:
    if not principal:
        return ""
    profile = DeveloperProfileEntity[principal]
    return str(profile.display_name or "") if profile is not None else ""


def developer_names() -> Dict[str, str]:
    return {
        str(p.principal): str(p.display_name)
        for p in DeveloperProfileEntity.instances()
        if p.display_name
    }


def set_developer_name(principal: str, name: str) -> Dict:
    """Admin only. An empty ``name`` removes the display name."""
    if not caller_is_admin():
        return {"success": False, "error": "Unauthorized: admins only"}
    principal = (principal or "").strip()
    name = (name or "").strip()
    if not principal:
        return {"success": False, "error": "principal is required"}
    if len(name) > MAX_NAME_LENGTH:
        return {"success": False, "error": f"name is longer than {MAX_NAME_LENGTH} characters"}
    profile = DeveloperProfileEntity[principal]
    if not name:
        if profile is not None:
            if profile.suspended:
                profile.display_name = ""
                profile.updated_at = _now()
            else:
                profile.delete()
        return {"success": True, "action": "removed"}
    if profile is None:
        DeveloperProfileEntity(
            principal=principal, display_name=name, suspended=False,
            suspended_reason="", updated_at=_now(),
        )
        action = "created"
    else:
        profile.display_name = name
        profile.updated_at = _now()
        action = "updated"
    logger.info(f"developer name {principal} = {name!r}")
    return {"success": True, "action": action}


def set_developer_name_from_json(args: str) -> Dict:
    """The sheet-row form: one JSON argument {"principal": str, "name": str},
    sent by the conductor (a controller)."""
    try:
        params = json.loads(args or "{}")
    except (TypeError, ValueError) as exc:
        return {"success": False, "error": f"args must be JSON: {exc}"}
    if not isinstance(params, dict):
        return {"success": False, "error": "args must be a JSON object"}
    return set_developer_name(str(params.get("principal") or ""), str(params.get("name") or ""))


# ---------------------------------------------------------------------------
# Suspension
# ---------------------------------------------------------------------------


def is_suspended(principal: str) -> bool:
    if not principal:
        return False
    profile = DeveloperProfileEntity[principal]
    return bool(profile.suspended) if profile is not None else False


def suspension_reason(principal: str) -> str:
    profile = DeveloperProfileEntity[principal] if principal else None
    return str(profile.suspended_reason or "") if profile is not None else ""


def set_user_suspended(principal: str, suspended: bool, reason: str = "") -> Dict:
    """Admin only. A suspended principal cannot publish, update its listings
    or request audits. Its existing listings stay as they are; delist them
    separately. Admins cannot be suspended: only controllers decide who is
    one, so they remove the admin first."""
    if not caller_is_admin():
        return {"success": False, "error": "Unauthorized: admins only"}
    principal = (principal or "").strip()
    reason = (reason or "").strip()
    if not principal:
        return {"success": False, "error": "principal is required"}
    if len(reason) > MAX_REASON_LENGTH:
        return {"success": False, "error": f"reason is longer than {MAX_REASON_LENGTH} characters"}
    if suspended and principal in get_admins():
        return {"success": False, "error": "Admins cannot be suspended; a controller must remove the admin first"}
    profile = DeveloperProfileEntity[principal]
    if profile is None:
        if not suspended:
            return {"success": True, "action": "unchanged"}
        DeveloperProfileEntity(
            principal=principal, display_name="", suspended=True,
            suspended_reason=reason, updated_at=_now(),
        )
    else:
        profile.suspended = bool(suspended)
        profile.suspended_reason = reason if suspended else ""
        profile.updated_at = _now()
    action = "suspended" if suspended else "reinstated"
    logger.info(f"{principal} {action} by {ic.caller()} (reason: {reason!r})")
    return {"success": True, "action": action}


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------


def _listing(item_kind: str, item_id: str):
    alias = (item_id or "").replace("/", "__")
    if item_kind == "ext":
        return ExtensionListingEntity[item_id]
    if item_kind == "codex":
        return CodexListingEntity[alias]
    return AssistantListingEntity[alias]


def transfer_listing(caller: str, item_kind: str, item_id: str, new_developer: str) -> Dict:
    """Make ``new_developer`` the owner of a listing. The current owner or an
    admin may do this. Version, files and review state stay as they are:
    the bytes on offer have not changed, only who answers for them."""
    if item_kind not in TRANSFER_KINDS:
        return {"success": False, "error": f"item_kind must be one of {TRANSFER_KINDS}"}
    new_developer = (new_developer or "").strip()
    if not new_developer:
        return {"success": False, "error": "new_developer is required"}
    if len(new_developer) > 128:
        return {"success": False, "error": "new_developer too long"}
    listing = _listing(item_kind, item_id)
    if listing is None:
        return {"success": False, "error": f"{item_kind}:{item_id} not found"}
    previous = str(listing.developer or "")
    if previous != caller and not caller_is_admin():
        return {"success": False, "error": "Not the owner"}
    if previous == new_developer:
        return {"success": True, "action": "unchanged", "developer": new_developer}
    listing.developer = new_developer
    logger.info(f"transferred {item_kind}:{item_id} from {previous} to {new_developer} (by {caller})")
    return {"success": True, "action": "transferred", "developer": new_developer, "previous": previous}
