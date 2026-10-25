"""
Status API for Realm DAO system

Provides health check and system status information
"""

import json
import sys
from typing import Any

from _cdk import ic
from ggg import (
    Codex,
    Dispute,
    Instrument,
    License,
    Mandate,
    Organization,
    Proposal,
    Quarter,
    Realm,
    Registry,
    Task,
    Trade,
    Transfer,
    User,
    UserProfile,
    Vote,
)
from ic_python_logging import get_logger


def _shared_tokens() -> dict:
    try:
        from api.tokens import shared_token_catalog
    except ImportError:
        return {}
    try:
        return shared_token_catalog()
    except Exception:  # noqa: BLE001 - status() must never fail on the catalog
        return {}

# Initialize logger
logger = get_logger("api.status")

# Above this many users the per-quarter home_quarter scan is unaffordable in
# a query call (5B instruction limit); larger realms report counter-based
# populations (join-time push) instead.
_POP_SCAN_LIMIT = 2000


def get_status() -> "dict[str, Any]":
    """
    Get current system status and health information

    Optimized to minimize instruction count for query calls.

    Returns:
        Status: Object with status information conforming to the Candid type
    """
    logger.info("Status check requested")

    # Get entity counts from the database (count() is optimized by ic_python_db)
    users_count = User.count()
    organizations_count = Organization.count()
    realms_count = Realm.count()
    mandates_count = Mandate.count()
    tasks_count = Task.count()
    transfers_count = Transfer.count()
    instruments_count = Instrument.count()
    codexes_count = Codex.count()
    disputes_count = Dispute.count()
    licenses_count = License.count()
    trades_count = Trade.count()
    proposals_count = Proposal.count()
    votes_count = Vote.count()
    user_profiles_count = UserProfile.count()

    realm_name = ""
    realm_welcome_message = ""
    realm_manifesto = ""
    realm_open_registration = False
    realm_ai_assistant_enabled = True
    realm_stage = "setup"
    logo_url = ""
    background_image_url = ""
    file_registry_canister_id = ""
    marketplace_canister_id = ""
    import core.setup as _setup

    primary_color = _setup.DEFAULT_PRIMARY_COLOR
    try:
        first_realm = Realm.load("1")
        if first_realm:
            primary_color = _setup.get_primary_color(first_realm)
            realm_name = first_realm.name or ""
            realm_welcome_message = getattr(first_realm, "welcome_message", None) or ""
            realm_manifesto = getattr(first_realm, "manifesto", None) or ""
            realm_open_registration = bool(getattr(first_realm, "open_registration", False))
            _ai_flag = getattr(first_realm, "ai_assistant_enabled", None)
            realm_ai_assistant_enabled = True if _ai_flag is None else bool(_ai_flag)
            realm_stage = getattr(first_realm, "status", None) or "setup"
            logo_url = getattr(first_realm, "logo_url", None) or ""
            background_image_url = getattr(first_realm, "background_image_url", None) or ""
            file_registry_canister_id = getattr(first_realm, "file_registry_canister_id", None) or ""
            marketplace_canister_id = getattr(first_realm, "marketplace_canister_id", None) or ""
    except Exception:
        pass

    # Extension discovery with version, commit, and datetime from manifests
    extensions_commit = "EXTENSIONS_COMMIT_HASH_PLACEHOLDER"
    extensions_commit_datetime = "EXTENSIONS_COMMIT_DATETIME_PLACEHOLDER"
    extension_entries = []
    try:
        from api.extensions import get_all_extension_manifests

        manifests = get_all_extension_manifests()
        for ext_name, manifest in manifests.items():
            extension_entries.append(
                json.dumps(
                    {
                        "name": ext_name,
                        "version": manifest.get("version", ""),
                        "commit": extensions_commit,
                        "commit_datetime": extensions_commit_datetime,
                    }
                )
            )
    except Exception as e:
        logger.warning(f"Could not list extensions: {e}")

    # Static values — placeholders replaced at build time by CI
    commit_hash = "COMMIT_HASH_PLACEHOLDER"
    commit_datetime = "COMMIT_DATETIME_PLACEHOLDER"
    version = "VERSION_PLACEHOLDER"
    test_mode = False
    test_mode_ii_bypass = False
    test_mode_user_self_registration = False
    test_mode_demo_data = False
    test_mode_skip_terms = False
    test_mode_skip_passport_zkproof = False
    realm_network = ""
    gos_environment = ""

    # Which variant this WASM was compiled as. Reported so an operator can tell a
    # production artifact from a test one without trusting the deploy log.
    try:
        from core.build_variant import BUILD_VARIANT as build_variant
    except Exception:
        build_variant = "unknown"

    # Read test flags from Realm entity (set via set_canister_config)
    try:
        _tm_realm = Realm.load("1")
        if _tm_realm:
            test_mode = bool(getattr(_tm_realm, "test_mode", False))
            realm_network = getattr(_tm_realm, "network", "") or ""
            from core.setup import get_gos_environment

            gos_environment = get_gos_environment(_tm_realm)
            # Reported through the variant-gated accessor so the frontend never
            # offers a test login that the production canister will not honour.
            from core.runtime_flags import is_ii_bypass_active

            test_mode_ii_bypass = is_ii_bypass_active()
            test_mode_user_self_registration = bool(getattr(_tm_realm, "test_mode_user_self_registration", False))
            test_mode_demo_data = bool(getattr(_tm_realm, "test_mode_demo_data", False))
            test_mode_skip_terms = bool(getattr(_tm_realm, "test_mode_skip_terms", False))
            test_mode_skip_passport_zkproof = bool(getattr(_tm_realm, "test_mode_skip_passport_zkproof", False))
    except Exception:
        pass

    # Prefer version stored in DB (set by set_canister_config / CI post-deploy)
    if version == "VERSION_PLACEHOLDER":
        try:
            _realm = Realm.load("1")
            if _realm and getattr(_realm, "installed_version", None):
                version = _realm.installed_version
        except Exception:
            pass

    # Skip expensive TaskManager status to stay under instruction limit
    # TaskManager status can be queried via separate endpoint if needed
    task_manager_status = {"status": "available"}  # Simplified to reduce instructions

    # Get canister IDs - backend is self, others from Realm entity
    canisters = []

    # Backend canister (self)
    try:
        backend_id = ic.id().to_str()
        canisters.append({"canister_id": backend_id, "canister_type": "realm_backend"})
    except Exception as e:
        logger.warning(f"Could not get backend canister ID: {e}")

    # Load other canister IDs from Realm entity (set via set_canister_config)
    try:
        first_realm = Realm.load("1")
        if first_realm:
            if getattr(first_realm, "frontend_canister_id", None):
                canisters.append(
                    {
                        "canister_id": first_realm.frontend_canister_id,
                        "canister_type": "realm_frontend",
                    }
                )
            if getattr(first_realm, "token_canister_id", None):
                canisters.append(
                    {
                        "canister_id": first_realm.token_canister_id,
                        "canister_type": "token_backend",
                    }
                )
            if getattr(first_realm, "nft_canister_id", None):
                canisters.append(
                    {
                        "canister_id": first_realm.nft_canister_id,
                        "canister_type": "nft_backend",
                    }
                )
            if getattr(first_realm, "file_registry_canister_id", None):
                canisters.append(
                    {
                        "canister_id": first_realm.file_registry_canister_id,
                        "canister_type": "file_registry",
                    }
                )
            if getattr(first_realm, "marketplace_canister_id", None):
                canisters.append(
                    {
                        "canister_id": first_realm.marketplace_canister_id,
                        "canister_type": "marketplace",
                    }
                )
    except Exception as e:
        logger.warning(f"Could not load canister IDs from Realm entity: {e}")

    # Get registries this realm is registered with
    registries = []
    try:
        for reg in Registry.instances():
            if getattr(reg, "principal_id", None):
                registries.append(
                    {"canister_id": reg.principal_id, "canister_type": "registry"}
                )
    except Exception as e:
        logger.warning(f"Could not load registries: {e}")

    # Accounting currency
    accounting_currency = ""
    accounting_currency_decimals = 8
    token_indexer_canister_id = ""
    try:
        first_realm = Realm.load("1")
        if first_realm:
            accounting_currency = getattr(first_realm, "accounting_currency", "") or ""
            accounting_currency_decimals = (
                getattr(first_realm, "accounting_currency_decimals", None) or 8
            )
            from api.tokens import get_treasury_token_indexer

            token_indexer_canister_id = get_treasury_token_indexer(
                accounting_currency,
                getattr(first_realm, "token_canister_id", "") or "",
            )
    except Exception as e:
        logger.warning(f"Could not load accounting currency: {e}")

    # Quarter discovery
    quarters = []
    quarter_entities = []
    is_quarter = False
    is_capital = False
    parent_realm_canister_id = ""
    try:
        first_realm = Realm.load("1")
        if first_realm:
            is_quarter = getattr(first_realm, "is_quarter", False) or False
            is_capital = getattr(first_realm, "is_capital", False) or False
            parent_realm_canister_id = (
                getattr(first_realm, "federation_realm_id", "") or ""
            )
            own_id = ic.id().to_str()
            quarter_entities = list(Quarter.instances())

            if quarter_entities:
                # Population accounting without a full User scan: direct
                # quarter joins live in the quarter's own table and are pushed
                # to q.population via report_quarter_population, so the
                # capital's table holds capital residents. The legacy
                # home_quarter scan is only affordable for small realms — at
                # 10k users it exceeds the 5B query instruction limit (found
                # at the 10k calibration rung).
                if users_count <= _POP_SCAN_LIMIT:
                    all_users = list(User.instances())
                    capital_pop = sum(
                        1 for u in all_users
                        if (getattr(u, "home_quarter", "") or "") in ("", own_id)
                    )
                    per_quarter_scan = {
                        q.canister_id or "": sum(
                            1 for u in all_users
                            if (getattr(u, "home_quarter", "") or "") == (q.canister_id or "")
                        )
                        for q in quarter_entities
                    }
                else:
                    capital_pop = users_count
                    per_quarter_scan = {}
                quarters.append(
                    {
                        "name": "Capital",
                        "canister_id": own_id,
                        "population": capital_pop,
                        "status": "active",
                        "index": 0,
                        "is_capital": True,
                    }
                )
                for q in quarter_entities:
                    qcid = q.canister_id or ""
                    # Users who joined the quarter directly live in the quarter's
                    # own table, not the capital's — the local home_quarter scan
                    # misses them. Join-time population push writes the quarter's
                    # true count into q.population; trust whichever is larger.
                    q_pop = max(per_quarter_scan.get(qcid, 0), int(q.population or 0))
                    quarters.append(
                        {
                            "name": q.name or "",
                            "canister_id": qcid,
                            "population": q_pop,
                            "status": q.status or "active",
                            "index": int(getattr(q, "index", 0) or 0),
                            "is_capital": False,
                        }
                    )
            else:
                # No quarters: all users belong to the capital — use count() to
                # avoid loading every User entity from stable memory (saves ~5B
                # instructions for large realms).
                quarters.append(
                    {
                        "name": "Capital",
                        "canister_id": own_id,
                        "population": users_count,
                        "status": "active",
                        "index": 0,
                        "is_capital": True,
                    }
                )
    except Exception as e:
        logger.warning(f"Could not load quarter info: {e}")

    # A capital shows the latest activity of the whole federation; quarters
    # report theirs through the gossip ``self`` block.
    last_activity_at = 0
    try:
        from core.activity import federation_latest

        last_activity_at = federation_latest(
            Realm.load("1"), [] if is_quarter else quarter_entities
        )
    except Exception:
        pass

    # Federation-wide headcount: the capital's User.count() covers capital residents
    # only — sub-quarters store their own user tables and push counts into
    # Quarter.population. Sum the quarter directory for total realm population.
    if not is_quarter and quarters:
        users_count = sum(int(q.get("population", 0) or 0) for q in quarters)

    # Dependency versions injected at build time (runtime detection doesn't work in WASM)
    dependencies = [
        "ic-basilisk==BASILISK_VERSION_PLACEHOLDER",
        "ic-basilisk-toolkit==IC_BASILISK_TOOLKIT_VERSION_PLACEHOLDER",
        "ic-python-db==IC_PYTHON_DB_VERSION_PLACEHOLDER",
        "ic-python-logging==IC_PYTHON_LOGGING_VERSION_PLACEHOLDER",
    ]

    # Return data in the format expected by the Status Candid type
    return {
        "version": version,
        "status": "ok",
        "realm_name": realm_name,
        "realm_welcome_message": realm_welcome_message,
        "realm_manifesto": realm_manifesto,
        "realm_stage": realm_stage,
        "last_activity_at": last_activity_at or None,
        "open_registration": realm_open_registration,
        "ai_assistant_enabled": realm_ai_assistant_enabled,
        "users_count": users_count,
        "organizations_count": organizations_count,
        "realms_count": realms_count,
        "mandates_count": mandates_count,
        "tasks_count": tasks_count,
        "transfers_count": transfers_count,
        "instruments_count": instruments_count,
        "codexes_count": codexes_count,
        "user_profiles_count": user_profiles_count,
        "disputes_count": disputes_count,
        "licenses_count": licenses_count,
        "trades_count": trades_count,
        "proposals_count": proposals_count,
        "votes_count": votes_count,
        "commit": commit_hash,
        "commit_datetime": commit_datetime,
        "extensions": extension_entries,
        "build_variant": build_variant,
        # The network is self-declared and gates whether test flags may be
        # enabled. An empty value is treated as production, so surface it here
        # rather than letting a realm sit in that state unnoticed.
        "network": realm_network,
        "gos_environment": gos_environment,
        "test_mode": test_mode,
        "test_mode_ii_bypass": test_mode_ii_bypass,
        "test_mode_user_self_registration": test_mode_user_self_registration,
        "test_mode_demo_data": test_mode_demo_data,
        "test_mode_skip_terms": test_mode_skip_terms,
        "test_mode_skip_passport_zkproof": test_mode_skip_passport_zkproof,
        "task_manager": task_manager_status,
        "canisters": canisters,
        # Shared ledgers this realm may adopt (from casals.json via the installer).
        # Frontends and extensions read it here instead of baking in a table.
        "shared_tokens": _shared_tokens(),
        "registries": registries,
        "dependencies": dependencies,
        "python_version": sys.version,
        "quarters": quarters,
        "is_quarter": is_quarter,
        "is_capital": is_capital,
        "parent_realm_canister_id": parent_realm_canister_id,
        "accounting_currency": accounting_currency,
        "accounting_currency_decimals": accounting_currency_decimals,
        "token_indexer_canister_id": token_indexer_canister_id,
        "logo_url": logo_url,
        "background_image_url": background_image_url,
        "file_registry_canister_id": file_registry_canister_id,
        "marketplace_canister_id": marketplace_canister_id,
        "primary_color": primary_color,
        "realm_logo": logo_url,
        "realm_description": realm_manifesto,
        "realm_welcome_image": background_image_url,
    }
