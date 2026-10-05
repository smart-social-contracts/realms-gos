"""Developer display names and listing ownership transfer.

A listing's ``developer`` is the principal that created it and the only one
(besides controllers) that may update or delist it. ``DeveloperProfileEntity``
gives that principal a public name; ``transfer_listing`` hands a listing to
another principal without touching its version or review state.
"""

import json
from typing import Dict

from _cdk import ic
from core.models import (
    AssistantListingEntity,
    CodexListingEntity,
    DeveloperProfileEntity,
    ExtensionListingEntity,
)
from ic_python_logging import get_logger

logger = get_logger("api.developers")

MAX_NAME_LENGTH = 64
TRANSFER_KINDS = ("ext", "codex", "assistant")


def _is_controller() -> bool:
    try:
        return bool(ic.is_controller(ic.caller()))
    except Exception:
        return False


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
    """Controller only. An empty ``name`` removes the display name."""
    if not _is_controller():
        return {"success": False, "error": "Unauthorized: controller-only"}
    principal = (principal or "").strip()
    name = (name or "").strip()
    if not principal:
        return {"success": False, "error": "principal is required"}
    if len(name) > MAX_NAME_LENGTH:
        return {"success": False, "error": f"name is longer than {MAX_NAME_LENGTH} characters"}
    profile = DeveloperProfileEntity[principal]
    if not name:
        if profile is not None:
            profile.delete()
        return {"success": True, "action": "removed"}
    if profile is None:
        DeveloperProfileEntity(principal=principal, display_name=name, updated_at=_now())
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


def _listing(item_kind: str, item_id: str):
    alias = (item_id or "").replace("/", "__")
    if item_kind == "ext":
        return ExtensionListingEntity[item_id]
    if item_kind == "codex":
        return CodexListingEntity[alias]
    return AssistantListingEntity[alias]


def transfer_listing(caller: str, item_kind: str, item_id: str, new_developer: str) -> Dict:
    """Make ``new_developer`` the owner of a listing. The current owner or a
    controller may do this. Version, files and review state stay as they are:
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
    if previous != caller and not _is_controller():
        return {"success": False, "error": "Not the owner"}
    if previous == new_developer:
        return {"success": True, "action": "unchanged", "developer": new_developer}
    listing.developer = new_developer
    logger.info(f"transferred {item_kind}:{item_id} from {previous} to {new_developer} (by {caller})")
    return {"success": True, "action": "transferred", "developer": new_developer, "previous": previous}
