from typing import Any

from ic_basilisk_toolkit.crypto import EncryptedString
from ic_python_db import (
    Entity,
    ManyToMany,
    OneToMany,
    OneToOne,
    String,
    TimestampedMixin,
)
from ic_python_logging import get_logger

from .user_profile import UserProfile

logger = get_logger("entity.user")


class User(Entity, TimestampedMixin):
    __owner_field__ = "id"  # realms#282 — SecureORM ownership stamp/protect
    __alias__ = "id"
    id = String()
    # Public data (visible to community)
    nickname = String(max_length=256)
    avatar = String(max_length=512)
    # Quarter federation
    home_quarter = String(max_length=64)  # Canister ID of user's home quarter
    # Private data (encrypted at rest via vetKeys + basilisk OS crypto)
    # JSON blob — schema defined in realm manifest
    private_data = EncryptedString()
    # Unidirectional (issue #242): profiles/extensions/departments fan out to
    # every user, so the target side keeps only an O(1) counter
    # (e.g. profile.reverse_count("users")) instead of an ID array that would
    # be rewritten on each registration. Reverse *listing* is a paginated user
    # scan — see core.membership.
    profiles = ManyToMany(["UserProfile"], "users", unidirectional=True)
    human = OneToOne("Human", "user")
    member = OneToOne("Member", "user")
    proposals = OneToMany("Proposal", "proposer")
    votes = OneToMany("Vote", "voter")
    notifications = OneToMany("Notification", "user")
    services = OneToMany("Service", "user")
    tax_records = OneToMany("TaxRecord", "user")
    disputes_requested = OneToMany("Dispute", "requester")
    disputes_defendant = OneToMany("Dispute", "defendant")
    payment_accounts = OneToMany("PaymentAccount", "user")
    invoices = OneToMany("Invoice", "user")
    zones = OneToMany("Zone", "user")
    ledger_entries = OneToMany("LedgerEntry", "user")
    cases_as_plaintiff = OneToMany("Case", "plaintiff")
    cases_as_defendant = OneToMany("Case", "defendant")
    penalties_received = OneToMany("Penalty", "target_user")
    appeals_filed = OneToMany("Appeal", "appellant")
    permissions = ManyToMany(["Permission"], "users")
    extensions = ManyToMany(["Extension"], "users", unidirectional=True)
    departments = ManyToMany(["Department"], "members", unidirectional=True)
    headed_departments = OneToMany("Department", "head")
    # transfers_from = OneToMany("Transfer", "from_user")
    # transfers_to = OneToMany("Transfer", "to_user")
    # trades_a = OneToMany("Trade", "user_a")
    # trades_b = OneToMany("Trade", "user_b")
    # owned_lands = OneToMany("Land", "owner_user")

    def __repr__(self):
        return f"User(id={self.id!r})"

    @staticmethod
    def user_register_posthook(user: "User"):
        """Named posthook point called after user registration (issue #244).

        Dispatch order:
          1. the active hook-API codex's ``on_user_register`` hook — the
             standard integration point for ``kind: codex`` packages;
          2. legacy fallback — a Codex entity named ``user_registration_hook``
             (or ``user_registration_hook_codex``).

        Both run sandboxed. A codex used to be able to replace this method
        outright via ``entity_method_overrides``, bypassing the body entirely;
        that mechanism was removed in issue #265.
        """
        try:
            from core.codex_hooks import dispatch_on_user_register

            if dispatch_on_user_register(user.id):
                return
        except Exception as e:
            logger.error(f"Codex on_user_register dispatch failed: {e}")

        try:
            from core.codex_hooks import call_registration_posthook

            if call_registration_posthook(user.id):
                return
        except Exception as e:
            logger.error(f"Legacy user_registration_hook dispatch failed: {e}")

        logger.info(
            f"Hook called after user registration. User {user.id} registered with profile {user.profiles}"
        )
        return

    @staticmethod
    def role_assign_prehook(user: "User", profile_name: str, assigner_principal: str) -> bool:
        """Called before assigning a profile. The codex may veto the change.

        Default: allow. Codex overrides implement governance policy (e.g.
        require an approved proposal for sensitive roles). The hook runs
        sandboxed over the capability bridge and returns a plain verdict; a
        codex that cannot be evaluated refuses the change (issue #265).
        """
        from core.codex_hooks import enforce_role_gate

        return enforce_role_gate(
            "role_assign_prehook", user.id, profile_name, assigner_principal
        )

    @staticmethod
    def role_assign_posthook(user: "User", profile_name: str, assigner_principal: str):
        """Called after successful profile assignment."""
        from core.codex_hooks import notify_role_change

        notify_role_change(
            "role_assign_posthook", user.id, profile_name, assigner_principal
        )
        logger.info(
            f"Profile '{profile_name}' assigned to user {user.id} by {assigner_principal}"
        )

    @staticmethod
    def role_revoke_prehook(user: "User", profile_name: str, revoker_principal: str) -> bool:
        """Called before revoking a profile. Same contract as
        ``role_assign_prehook``."""
        from core.codex_hooks import enforce_role_gate

        return enforce_role_gate(
            "role_revoke_prehook", user.id, profile_name, revoker_principal
        )

    @staticmethod
    def role_revoke_posthook(user: "User", profile_name: str, revoker_principal: str):
        """Called after successful profile revocation."""
        from core.codex_hooks import notify_role_change

        notify_role_change(
            "role_revoke_posthook", user.id, profile_name, revoker_principal
        )
        logger.info(
            f"Profile '{profile_name}' revoked from user {user.id} by {revoker_principal}"
        )


def user_to_get_record(user) -> dict[str, Any]:
    """Public user fields for Candid ``UserGetRecord``.

    ``departments`` is always a list (empty when the user has no org
    memberships). Omitting it makes the JS actor fail closed with
    ``Cannot find required field departments`` on join / user_get.
    """
    from core.membership import user_department_names

    return {
        "principal": user.id,
        "profiles": [profile.name for profile in user.profiles],
        "departments": list(user_department_names(user)),
        "nickname": user.nickname or "",
        "avatar": user.avatar or "",
        "private_data": user.private_data or "",
        "home_quarter": user.home_quarter or "",
    }


def user_register(principal: str, profile: str) -> dict[str, Any]:
    logger.info(f"Registering user {principal} with profile {profile}")

    user_profi = UserProfile[profile]
    if not user_profi:
        raise ValueError(f"Profile {profile} not found")

    user = User[principal]
    is_new_user = user is None
    if is_new_user:
        # Explicit empty departments so Candid UserGetRecord can always
        # encode ``vec {}`` for a brand-new member (no invented membership).
        user = User(id=principal, profiles=[user_profi], departments=[])
        logger.info(f"Created new user {principal} with profile {profile}")
    else:
        # Add profile only if not already assigned
        if user_profi not in user.profiles:
            user.profiles.add(user_profi)
            logger.info(f"Added profile {profile} to existing user {principal}")
        else:
            logger.info(f"User {principal} already has profile {profile}")

    if is_new_user:
        User.user_register_posthook(user)  # type: ignore[arg-type]
        # Auto-scaling: evaluate whether the federation should spawn a new
        # quarter now that population grew. Runs for *all* user-creation paths
        # (so a codex posthook override can't disable sharding). Non-blocking —
        # it only records intent; provisioning happens out of band.
        try:
            from core.autoscale import maybe_request_quarter_scale

            if maybe_request_quarter_scale():
                logger.info(f"Quarter auto-scale requested after registering {principal}")
        except Exception as e:
            logger.error(f"Auto-scale evaluation failed for {principal}: {e}")

    return user_to_get_record(user)
