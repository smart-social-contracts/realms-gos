"""Candid UserGetRecord payload helpers.

The JS actor (``@dfinity/candid``) decodes ``join_realm`` as
``RealmResponse → userGet: UserGetRecord``. ``UserGetRecord`` is a
required-field record:

    principal; profiles : vec text; departments : vec text;
    nickname; avatar; private_data; assigned_quarter

``departments`` must always be present on the wire as ``vec {}`` (empty
is fine; do not invent memberships). Omitting it makes the actor throw
``Cannot find required field departments`` and the join page fails closed.

#370 normalized a *Python* dict that used ``home_quarter``. That is not
the Candid field. Basilisk encodes ``UserGetRecord`` from the dict keys
that match the DID. A constructor that stores ``home_quarter`` and skips
``assigned_quarter`` / ``departments`` produces bytes the frontend cannot
decode. This helper emits the exact DID keys.
"""

from typing import Any

# Exact Candid UserGetRecord fields from realm_backend.did / frontend IDL.
# Do not substitute home_quarter here — that name is internal-only.
CANDID_USER_GET_RECORD_KEYS = (
    "principal",
    "profiles",
    "departments",
    "nickname",
    "avatar",
    "private_data",
    "assigned_quarter",
)

# Back-compat alias used by older tests / call sites.
USER_GET_RECORD_KEYS = CANDID_USER_GET_RECORD_KEYS


def _department_names(value: Any) -> list[str]:
    if value is None:
        return []
    names: list[str] = []
    for item in value:
        if item is None:
            continue
        name = item if isinstance(item, str) else getattr(item, "name", item)
        if name:
            names.append(str(name))
    return names


def candid_user_get_record_fields(
    payload: dict[str, Any] | None,
    *,
    assigned_quarter: str | None = None,
) -> dict[str, Any]:
    """Build the exact dict basilisk encodes as Candid ``UserGetRecord``.

    ``departments`` is always a list. ``assigned_quarter`` is the Candid
    field (accepts internal ``home_quarter`` as input).
    """
    src = payload or {}
    quarter = assigned_quarter
    if quarter is None:
        quarter = src.get("assigned_quarter") or src.get("home_quarter") or ""
    fields = {
        "principal": src.get("principal") or "",
        "profiles": [str(p) for p in (src.get("profiles") or []) if p],
        "departments": _department_names(src.get("departments")),
        "nickname": src.get("nickname") or "",
        "avatar": src.get("avatar") or "",
        "private_data": src.get("private_data") or "",
        "assigned_quarter": quarter or "",
    }
    for key in CANDID_USER_GET_RECORD_KEYS:
        if key not in fields:
            raise RuntimeError(f"UserGetRecord payload missing required field {key}")
    if "home_quarter" in fields:
        raise RuntimeError("UserGetRecord must use assigned_quarter, not home_quarter")
    return fields


def user_get_record_fields(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize join / user_register / user_get payloads for UserGetRecord."""
    return candid_user_get_record_fields(payload)


def join_realm_user_get_value(
    payload: dict[str, Any] | None,
    *,
    assigned_quarter: str = "",
) -> dict[str, Any]:
    """The UserGetRecord value ``join_realm`` must encode.

    Used by Candid byte tests so the wire shape cannot drift from the
    frontend IDL (``departments: vec {}`` on a brand-new member).
    """
    return candid_user_get_record_fields(payload, assigned_quarter=assigned_quarter)
