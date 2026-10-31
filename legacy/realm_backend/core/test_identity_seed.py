"""Seed the deterministic II-bypass identities as real users.

When a realm runs with ``ii_bypass`` the browser signs in as a fixed, publicly
known keypair instead of going through Internet Identity. Those principals used
to get their privileges from a removed flag that disabled every permission check
in the canister. Granting them ordinary profiles instead means test environments
exercise the same authorization path as production.

The roster mirrors ``config/deterministic-test-identity-principals.json`` and
``src/realm_frontend/src/lib/test-identities.js`` (seed ``0xED 0x57`` followed by
the little-endian identity index).

Seeding is refused unless the caller has already established that test flags are
permitted for this network, so it cannot run on a production realm.
"""

from __future__ import annotations

from typing import Any

from ic_python_logging import get_logger

logger = get_logger("core.test_identity_seed")

# Index 0 — "Identity 1 (Creator)". Administers the realm in test environments.
TEST_IDENTITY_ADMIN_PRINCIPAL = (
    "2eqns-rmzes-7npxw-dxpw2-qdy2s-mw6ix-svdo2-oya7o-a6ldc-sqgwh-bqe"
)


def seed_ii_bypass_admin(*, test_flags_permitted: bool) -> dict[str, Any]:
    """Give the index-0 II-bypass principal an admin profile. Idempotent.

    ``test_flags_permitted`` is the caller's already-evaluated production gate;
    passing ``False`` makes this a no-op rather than an error so a production
    realm can be configured through the same code path.
    """
    if not test_flags_permitted:
        return {"success": False, "skipped": "test flags are not permitted here"}

    from core.admin_users import register_admin_user

    try:
        result = register_admin_user(
            TEST_IDENTITY_ADMIN_PRINCIPAL, seat_as_root_head=False
        )
    except Exception as err:  # pragma: no cover - defensive, never blocks config
        logger.warning(f"Could not seed II-bypass admin: {err}")
        return {"success": False, "error": str(err)}

    if result.get("success"):
        logger.info(
            f"II-bypass admin {TEST_IDENTITY_ADMIN_PRINCIPAL} seeded with admin profile"
        )
    return result
