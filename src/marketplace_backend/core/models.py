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

from typing import Callable, List, Tuple

from ic_python_db import (
    Boolean,
    Database,
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
    realm_principal = String(max_length=128, indexed=True)
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
    principal  = String(max_length=128, indexed=True)
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
    """Per-principal account state kept by the marketplace.

    ``display_name`` is a public name (e.g. "Realms GOS team"); listings still
    name their owner by principal, this only changes how the owner is shown.
    ``suspended`` blocks the principal from publishing and requesting audits.
    Both are set by an admin.
    """
    __alias__ = "principal"
    __version__ = 2

    @classmethod
    def migrate(cls, obj, from_version, to_version):
        if from_version < 2:
            obj.setdefault("suspended", False)
            obj.setdefault("suspended_reason", "")
        return obj

    principal        = String(max_length=128)
    display_name     = String(max_length=64)
    suspended        = Boolean()
    suspended_reason = String(max_length=512)
    updated_at       = Float()


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
    __version__ = 4

    @classmethod
    def migrate(cls, obj, from_version, to_version):
        if from_version < 2:
            # Config written before reviewers existed: no one is appointed, so
            # only controllers can review until someone is.
            obj.setdefault("reviewers", "")
        if from_version < 3:
            obj.setdefault("casals_frontend_canister_id", "")
        if from_version < 4:
            obj.setdefault("admins", "")
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
    # Comma-separated principals who run the marketplace day to day: every
    # listing, every user, every license. Only controllers change this list.
    admins                      = String(max_length=2048)


# ---------------------------------------------------------------------------
# Per-principal lookups
# ---------------------------------------------------------------------------
#
# Purchases and likes are read per principal through a field index, not by
# loading the whole table: a table scan costs about 1M instructions per row,
# so it would trap once the table reaches a few thousand rows.

def purchases_of(realm_principal: str) -> List[PurchaseEntity]:
    """Every purchase made by ``realm_principal``."""
    return _all_by(PurchaseEntity, "realm_principal", realm_principal)


def likes_of(principal: str) -> List[LikeEntity]:
    """Every like given by ``principal``."""
    return _all_by(LikeEntity, "principal", principal)


def _all_by(entity_cls, field: str, value: str) -> list:
    out, cursor = [], 1
    while cursor is not None:
        page, cursor = entity_cls.find_by(field, value, from_id=cursor, count=100)
        out.extend(page)
    return out


# Listings read per call by the paged list and search endpoints, whatever
# matches: about 1M instructions each, far below a query's 5B budget.
SCAN_BUDGET = 200


def newest_first(entity_cls, cursor: int, count: int, keep: Callable) -> Tuple[list, int]:
    """Up to ``count`` entities ``keep`` accepts, newest (highest id) first.

    ``cursor`` is the id to continue from, 0 to start at the newest. Returns
    the entities and the next cursor, 0 once every id has been read. At most
    SCAN_BUDGET ids are read per call, so when few listings match, a page can
    come back short, even empty, with a non-zero cursor: call again.
    """
    count = max(1, min(int(count or 20), 100))
    entity_id = int(cursor) if cursor else entity_cls.max_id()
    found = []
    for _ in range(SCAN_BUDGET):
        if entity_id < 1 or len(found) >= count:
            break
        entity = entity_cls.load(str(entity_id))
        entity_id -= 1
        if entity is not None and keep(entity):
            found.append(entity)
    return found, max(entity_id, 0)


_INDEX_BACKFILL_KEY = "marketplace_principal_index_v1"


def backfill_principal_indexes() -> None:
    """Index purchases and likes written before their principal was indexed.

    Runs once (a marker in ``_system`` records it); later rows are indexed as
    they are written. Called from post_upgrade.
    """
    db = Database.get_instance()
    if db.load("_system", _INDEX_BACKFILL_KEY):
        return
    for entity_cls, field in ((PurchaseEntity, "realm_principal"), (LikeEntity, "principal")):
        cursor = 1
        while cursor is not None:
            cursor = entity_cls.rebuild_field_index(field, from_id=cursor)
    db.save("_system", _INDEX_BACKFILL_KEY, "1")
