"""Crypto API — envelope & group management for realm-level encryption.

Wraps the ic-basilisk-toolkit CryptoService to expose envelope CRUD,
group management, and scope queries to Candid endpoints in main.py.
"""

from typing import Any

from ic_basilisk_toolkit.crypto import (
    CryptoGroup,
    CryptoGroupMember,
    CryptoService,
    KeyEnvelope,
)
from ic_python_logging import get_logger

logger = get_logger("api.crypto")

# Singleton — no VetKeyService needed for envelope storage operations.
# Async operations (init_scope) that need vetKD are driven by the caller.
_crypto = CryptoService(vetkey_service=None)


# ------------------------------------------------------------------
# Envelope operations
# ------------------------------------------------------------------


def store_envelope(principal: str, scope: str, wrapped_dek: str) -> dict[str, Any]:
    """Store (or update) a wrapped DEK envelope for the caller."""
    logger.info(f"store_envelope: scope={scope!r} principal={principal}")
    envelope = _crypto.grant_access(scope, principal, wrapped_dek)
    return {
        "success": True,
        "scope": str(envelope.scope),
        "principal": str(envelope.principal),
        "wrapped_dek": str(envelope.wrapped_dek),
    }


def get_envelope(principal: str, scope: str) -> dict[str, Any]:
    """Retrieve the caller's envelope for a scope."""
    envelope = _crypto.get_envelope(scope, principal)
    if not envelope:
        return {"success": False, "error": f"No envelope for scope {scope!r}"}
    return {
        "success": True,
        "scope": str(envelope.scope),
        "principal": str(envelope.principal),
        "wrapped_dek": str(envelope.wrapped_dek),
    }


def list_scopes(principal: str) -> dict[str, Any]:
    """List all scopes the caller has access to."""
    scopes = _crypto.list_scopes(principal)
    return {"success": True, "scopes": scopes}


def list_envelopes(scope: str) -> dict[str, Any]:
    """List all envelopes for a scope (principals with access)."""
    envelopes = _crypto.list_envelopes(scope)
    return {
        "success": True,
        "envelopes": [
            {
                "scope": str(e.scope),
                "principal": str(e.principal),
                "wrapped_dek": str(e.wrapped_dek),
            }
            for e in envelopes
        ],
    }


def share_with_principal(
    scope: str, target_principal: str, wrapped_dek: str
) -> dict[str, Any]:
    """Share access to a scope with another principal."""
    logger.info(f"share: scope={scope!r} -> {target_principal}")
    envelope = _crypto.grant_access(scope, target_principal, wrapped_dek)
    return {
        "success": True,
        "scope": str(envelope.scope),
        "principal": str(envelope.principal),
    }


def revoke_principal(scope: str, target_principal: str) -> dict[str, Any]:
    """Revoke a principal's access to a scope."""
    logger.info(f"revoke: scope={scope!r} from {target_principal}")
    deleted = _crypto.revoke_access(scope, target_principal)
    if not deleted:
        return {
            "success": False,
            "error": f"No envelope found for {target_principal} in scope {scope!r}",
        }
    return {"success": True}


def grant_many(scope: str, wrapped_deks: dict[str, str]) -> dict[str, Any]:
    """Grant access to many principals in a single pass (batch).

    Uses the toolkit's ``grant_many`` when available (single envelope-index
    snapshot); otherwise falls back to an equivalent local single-pass upsert so
    this works against older pinned toolkit versions too.
    """
    logger.info(f"grant_many: scope={scope!r} ({len(wrapped_deks)} principals)")
    batch = wrapped_deks or {}
    if hasattr(_crypto, "grant_many"):
        count = _crypto.grant_many(scope, batch)
    else:
        from ic_basilisk_toolkit.crypto import encode_envelope

        index = {str(e.principal): e for e in _crypto.list_envelopes(scope)}
        count = 0
        for principal, dek_hex in batch.items():
            principal = str(principal)
            existing = index.get(principal)
            if existing:
                if dek_hex:
                    existing.wrapped_dek = encode_envelope(dek_hex)
            else:
                index[principal] = KeyEnvelope(
                    scope=scope,
                    principal=principal,
                    wrapped_dek=encode_envelope(dek_hex or ""),
                )
            count += 1
    return {"success": True, "envelopes_granted": count}


def revoke_many(scope: str, principals: list[str]) -> dict[str, Any]:
    """Revoke access for many principals in a single pass (batch)."""
    logger.info(f"revoke_many: scope={scope!r} ({len(principals)} principals)")
    targets = principals or []
    if hasattr(_crypto, "revoke_many"):
        count = _crypto.revoke_many(scope, targets)
    else:
        target_set = {str(p) for p in targets}
        count = 0
        for e in list(_crypto.list_envelopes(scope)):
            if str(e.principal) in target_set:
                e.delete()
                count += 1
    return {"success": True, "envelopes_revoked": count}


# ------------------------------------------------------------------
# Group operations
# ------------------------------------------------------------------


def group_create(name: str, description: str = "") -> dict[str, Any]:
    """Create a new crypto group."""
    try:
        group = CryptoService.create_group(name, description)
        return {
            "success": True,
            "name": str(group.name),
            "description": str(group.description),
        }
    except ValueError as e:
        return {"success": False, "error": str(e)}


def group_delete(name: str) -> dict[str, Any]:
    """Delete a crypto group and all its members."""
    deleted = CryptoService.delete_group(name)
    if not deleted:
        return {"success": False, "error": f"Group {name!r} not found"}
    return {"success": True}


def group_add_member(
    group_name: str, principal: str, role: str = "member"
) -> dict[str, Any]:
    """Add a principal to a group."""
    try:
        member = CryptoService.add_member(group_name, principal, role)
        return {
            "success": True,
            "group": str(member.group),
            "principal": str(member.principal),
            "role": str(member.role),
        }
    except ValueError as e:
        return {"success": False, "error": str(e)}


def group_remove_member(group_name: str, principal: str) -> dict[str, Any]:
    """Remove a principal from a group."""
    removed = CryptoService.remove_member(group_name, principal)
    if not removed:
        return {
            "success": False,
            "error": f"{principal} not found in group {group_name!r}",
        }
    return {"success": True}


def group_list() -> dict[str, Any]:
    """List all crypto groups."""
    groups = CryptoService.list_groups()
    return {
        "success": True,
        "groups": [
            {"name": str(g.name), "description": str(g.description)} for g in groups
        ],
    }


def group_members(group_name: str) -> dict[str, Any]:
    """List all members of a group."""
    members = CryptoService.list_members(group_name)
    return {
        "success": True,
        "members": [
            {"principal": str(m.principal), "role": str(m.role)} for m in members
        ],
    }


def share_with_group(
    scope: str, group_name: str, wrapped_deks: dict | None = None
) -> dict[str, Any]:
    """Share access to a scope with all members of a group."""
    logger.info(f"share_with_group: scope={scope!r} group={group_name!r}")
    count = _crypto.grant_group_access(scope, group_name, wrapped_deks)
    return {"success": True, "envelopes_created": count}


def revoke_group(scope: str, group_name: str) -> dict[str, Any]:
    """Revoke all group members' access to a scope."""
    logger.info(f"revoke_group: scope={scope!r} group={group_name!r}")
    count = _crypto.revoke_group_access(scope, group_name)
    return {"success": True, "envelopes_deleted": count}
