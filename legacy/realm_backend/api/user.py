import json
from typing import Any

from ggg import User
from ggg.system.user import user_register as ggg_user_register
from ggg.system.user import user_to_get_record
from core.user_get_record import user_get_record_fields
from ic_python_logging import get_logger

logger = get_logger("api.user")

__all__ = [
    "user_get",
    "user_get_record_fields",
    "user_list",
    "user_register",
    "user_update_private_data",
    "user_update_public_profile",
]


def user_get(principal: str) -> dict[str, Any]:
    logger.info(f"Getting user {principal}")
    user = User[principal]
    if not user:
        return {"success": False, "error": f"User with principal {principal} not found"}
    return {"success": True, **user_to_get_record(user)}


def user_list() -> dict[str, Any]:
    logger.info("Listing users")
    return {"users": [user.serialize() for user in User.instances()]}


def user_update_public_profile(
    principal: str, nickname: str, avatar: str
) -> dict[str, Any]:
    logger.info(f"Updating public profile for user {principal}")
    user = User[principal]
    if not user:
        return {"success": False, "error": f"User with principal {principal} not found"}

    user.nickname = nickname
    user.avatar = avatar
    return {
        "success": True,
        "nickname": user.nickname or "",
        "avatar": user.avatar or "",
    }


# Email ownership is proven by notification.verify_email_code, not by
# writing these keys through the generic private_data blob.
_EMAIL_RESERVED_KEYS = (
    "email",
    "email_verified",
    "email_verify_code",
    "email_verify_expires",
    "email_verify_attempts",
)


def _as_object(raw: str) -> dict:
    try:
        data = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def user_update_private_data(principal: str, private_data: str) -> dict[str, Any]:
    logger.info(f"Updating private data for user {principal}")
    user = User[principal]
    if not user:
        return {"success": False, "error": f"User with principal {principal} not found"}

    incoming = _as_object(private_data)
    existing = _as_object(getattr(user, "private_data", "") or "")
    for key in _EMAIL_RESERVED_KEYS:
        incoming.pop(key, None)
        if key in existing:
            incoming[key] = existing[key]

    if "locale" in incoming:
        from core.realm_locales import get_realm_languages, validate_user_locale
        from ggg import Realm

        locale = incoming.get("locale")
        if locale is None:
            incoming["locale"] = ""
        elif isinstance(locale, str):
            incoming["locale"] = locale.strip()
        else:
            return {"success": False, "error": "locale must be a string"}
        realm = Realm.load("1")
        languages, _primary = get_realm_languages(realm) if realm else (["en"], "en")
        locale_err = validate_user_locale(incoming.get("locale"), languages)
        if locale_err:
            return {"success": False, "error": locale_err}

    user.private_data = json.dumps(incoming)
    return {
        "success": True,
        "private_data": user.private_data or "",
    }


def user_register(principal: str, profile: str) -> dict[str, Any]:
    """
    Register a new user or add a profile to an existing user.

    Args:
        principal: User principal ID
        profile: Profile name to assign to the user

    Returns:
        Dictionary with user data including principal, profiles, departments,
        nickname, avatar, and private_data. ``departments`` is always a list.
    """
    return ggg_user_register(principal, profile)
