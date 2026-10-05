"""ic_python_db Entity models for the marketplace backend.

Five families:

  * ExtensionListingEntity / CodexListingEntity — what's for sale.
  * PurchaseEntity                              — record of a buy_*.
  * LikeEntity                                  — one row per (user, item).
  * DeveloperLicenseEntity                      — paid annual license.
  * DeveloperProfileEntity                      — display name per developer principal.
  * MarketplaceConfigEntity                     — singleton config (file_registry id, billing service principal, license pricing).

All record fields are stored on-chain in the canister's StableBTreeMap
via ic_python_db. New fields can be added in future upgrades; existing
data without those fields reads back as ``None`` and our APIs always
guard with ``or 0`` / ``or ""``.
"""

from ic_python_db import (
    Boolean,
    Entity,
    Float,
    Integer,
    String,
    TimestampedMixin,
)


# ---------------------------------------------------------------------------
# Listings
# ---------------------------------------------------------------------------

class ExtensionListingEntity(Entity, TimestampedMixin):
    """One row per (extension_id) — latest version wins.

    The actual files live in a file_registry canister at the namespace
    ``ext/{extension_id}/{version}``; we only store the pointer + metadata.
    """
    __alias__ = "extension_id"
    __version__ = 2

    @classmethod
    def migrate(cls, obj, from_version, to_version):
        if from_version < 2:
            obj.setdefault("screenshots", "")
        return obj

    extension_id              = String(max_length=128)
    developer                 = String(max_length=128)   # principal text
    name                      = String(max_length=256)
    description               = String(max_length=2048)
    version                   = String(max_length=32)
    price_e8s                 = Integer()                 # display only — no on-chain transfer
    icon                      = String(max_length=64)     # short emoji / lucide name
    categories                = String(max_length=256)    # comma-separated
    screenshots               = String(max_length=1024)   # comma-separated registry-relative paths; first is the card thumbnail
    file_registry_canister_id = String(max_length=64)
    file_registry_namespace   = String(max_length=256)
    download_url              = String(max_length=512)    # legacy / fallback
    installs                  = Integer()
    likes                     = Integer()
    verification_status       = String(max_length=32)     # unverified | pending_audit | verified | rejected
    verification_notes        = String(max_length=1024)
    is_active                 = Boolean()
    created_at                = Float()
    updated_at                = Float()


class AssistantListingEntity(Entity, TimestampedMixin):
    """AI-assistant listing — a realm-hireable governance agent.

    Marketplace v2.1 third item kind. Stores **what the assistant is**
    (id, runtime, endpoint, model, requested role + permissions,
    training disclosure, eval report) and the standard listing plumbing
    (price, likes, installs, verification, file_registry pointer).

    The realm-side runtime that actually loads a hired assistant runs
    elsewhere (separate ``assistant_runner`` extension + ``hire_assistant``
    codex; out of scope for this canister). This entity is the catalog
    record, not the runtime.

    ``assistant_id`` may contain a single ``/`` to namespace by author
    (e.g. ``"smart-social-contracts/ashoka"``); the entity's alias key
    is the slash-safe form (``"smart-social-contracts__ashoka"``),
    same trick used by ``CodexListingEntity``.
    """
    __alias__ = "assistant_alias"

    assistant_alias               = String(max_length=128)
    assistant_id                  = String(max_length=128)
    developer                     = String(max_length=128)
    name                          = String(max_length=256)
    description                   = String(max_length=2048)
    version                       = String(max_length=32)
    price_e8s                     = Integer()
    pricing_summary               = String(max_length=512)   # free-text e.g. "$200/year per realm"
    icon                          = String(max_length=64)
    categories                    = String(max_length=256)

    # AI-specific fields
    runtime                       = String(max_length=32)    # runpod | openai | anthropic | on_chain_llm | self_hosted
    endpoint_url                  = String(max_length=512)
    base_model                    = String(max_length=128)
    requested_role                = String(max_length=64)    # User role the assistant asks for
    requested_permissions         = String(max_length=2048)  # comma-separated realm operations
    domains                       = String(max_length=256)   # governance,tax,justice,…
    languages                     = String(max_length=128)   # en,es,zh,…
    training_data_summary         = String(max_length=2048)
    eval_report_url               = String(max_length=512)

    # Standard listing plumbing (mirrors ExtensionListingEntity / CodexListingEntity)
    file_registry_canister_id     = String(max_length=64)
    file_registry_namespace       = String(max_length=256)   # e.g. "assistant/smart-social-contracts/ashoka/0.4.2"
    installs                      = Integer()
    likes                         = Integer()
    verification_status           = String(max_length=32)
    verification_notes            = String(max_length=1024)
    is_active                     = Boolean()
    created_at                    = Float()
    updated_at                    = Float()


class CodexListingEntity(Entity, TimestampedMixin):
    """One row per codex package.

    ``codex_id`` may contain ``/`` (e.g. ``"syntropia/membership"``);
    the entity alias is ``codex_alias`` which is the slash-safe form
    (``"syntropia__membership"``). Helpers are in ``api.codices``.
    """
    __alias__ = "codex_alias"

    codex_alias               = String(max_length=128)
    codex_id                  = String(max_length=128)
    realm_type                = String(max_length=64)
    developer                 = String(max_length=128)
    name                      = String(max_length=256)
    description               = String(max_length=2048)
    version                   = String(max_length=32)
    price_e8s                 = Integer()
    icon                      = String(max_length=64)
    categories                = String(max_length=256)
    file_registry_canister_id = String(max_length=64)
    file_registry_namespace   = String(max_length=256)
    installs                  = Integer()
    likes                     = Integer()
    verification_status       = String(max_length=32)
    verification_notes        = String(max_length=1024)
    is_active                 = Boolean()
    created_at                = Float()
    updated_at                = Float()


# ---------------------------------------------------------------------------
# Purchases
# ---------------------------------------------------------------------------

class PurchaseEntity(Entity):
    """One row per buy_* call.

    Used by both extension and codex purchases (``item_kind`` discriminates).
    """
    __alias__ = "purchase_id"

    purchase_id     = String(max_length=128)
    realm_principal = String(max_length=128)
    item_kind       = String(max_length=16)              # "ext" | "codex"
    item_id         = String(max_length=128)
    developer       = String(max_length=128)
    price_paid_e8s  = Integer()
    purchased_at    = Float()


# ---------------------------------------------------------------------------
# Likes
# ---------------------------------------------------------------------------

class LikeEntity(Entity):
    """One row per (principal, item_kind, item_id).

    ``like_id`` is the composite key ``"{principal}|{item_kind}|{item_id}"``.
    Listing entities carry a denormalised ``likes`` counter that this
    table is the source of truth for.
    """
    __alias__ = "like_id"

    like_id    = String(max_length=384)
    principal  = String(max_length=128)
    item_kind  = String(max_length=16)
    item_id    = String(max_length=128)
    created_at = Float()


# ---------------------------------------------------------------------------
# Developer licenses
# ---------------------------------------------------------------------------

class DeveloperLicenseEntity(Entity):
    """Paid annual license — gates ``request_audit``.

    Anyone can publish without a license; the license unlocks the audit
    flow run by Smart Social Contracts curators (controllers).
    ``last_payment_amount_usd_cents`` mirrors what the user actually
    paid for the most recent extension/renewal, kept on the row purely
    as an audit trail.
    """
    __alias__ = "principal"

    principal                     = String(max_length=128)
    created_at                    = Float()
    expires_at                    = Float()
    last_payment_id               = String(max_length=128)  # Stripe checkout session id
    last_payment_amount_usd_cents = Integer()
    payment_method                = String(max_length=32)   # stripe | manual | voucher
    note                          = String(max_length=512)
    is_active                     = Boolean()


class DeveloperProfileEntity(Entity):
    """Public display name for a developer principal (e.g. "Realms GOS team").

    Listings still name their owner by principal; this only changes how the
    owner is shown. Set by a controller (``admin_set_developer_name``).
    """
    __alias__ = "principal"

    principal    = String(max_length=128)
    display_name = String(max_length=64)
    updated_at   = Float()


# ---------------------------------------------------------------------------
# Singleton configuration
# ---------------------------------------------------------------------------

class MarketplaceConfigEntity(Entity):
    """Singleton — ``alias = "config"``.

    Holds:
      * ``file_registry_canister_id`` — where listings store their files.
      * ``casals_frontend_canister_id`` — Realms GOS Casals frontend
        (footer conductor link; not baked into marketplace WASM).
      * ``billing_service_principal`` — principal allowed to call
        ``record_license_payment`` (the off-chain Stripe relay).
      * ``license_price_usd_cents`` and ``license_duration_seconds`` —
        displayed on the developer page; the billing service uses the
        same pricing when creating a checkout session.
    """
    __alias__ = "id"
    __version__ = 3

    @classmethod
    def migrate(cls, obj, from_version, to_version):
        if from_version < 2:
            # Config written before reviewers existed: no one is appointed, so
            # only controllers can review until someone is.
            obj.setdefault("reviewers", "")
        if from_version < 3:
            obj.setdefault("casals_frontend_canister_id", "")
        return obj

    id                          = String(max_length=16)  # always "config"
    file_registry_canister_id   = String(max_length=64)
    casals_frontend_canister_id = String(max_length=64)
    billing_service_principal   = String(max_length=128)
    license_price_usd_cents     = Integer()
    license_duration_seconds    = Integer()
    # Comma-separated principals allowed to review submissions (issue #267).
    # Distinct from the controller list: reviewing content is a day-to-day job,
    # holding the upgrade key is not.
    reviewers                   = String(max_length=2048)
