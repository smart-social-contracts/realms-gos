"""Runtime test-mode flags — the single source of truth.

Test-mode flags live on the **Realm DB entity** (``Realm.load("1")``) and are set
at runtime via ``set_canister_config(test_flags_json=...)`` (see ``main.py``).
They are surfaced to the frontend through ``status()`` (``api/status.py``).

Historically some code read these flags from the build-time ``config.py`` module
(patched during the WASM build). That made the flags *baked in* — a reinstall or
environment change required a rebuild to flip them. This module replaces that with
a runtime read so a single ``set_canister_config`` call (e.g. from a Casals
arrangement) is enough, no rebuild required.

Importable from both the backend and from extensions/codices running inside the
realm canister (e.g. ``from core.runtime_flags import is_demo_data_active``).
"""


def get_realm_flag(name: str, default: bool = False) -> bool:
    """Read a boolean test-mode flag from the runtime Realm entity.

    Returns ``default`` if the Realm entity is missing or the read fails, matching
    the defensive pattern used throughout the backend (status.py, access.py).
    """
    try:
        from ggg import Realm

        realm = Realm.load("1")
        if realm is None:
            return default
        return bool(getattr(realm, name, default))
    except Exception:
        return default


# Networks on which test flags may be enabled without ``can_test_mode``. Anything
# else — including an unset or unrecognised network — is treated as production.
NON_PRODUCTION_NETWORKS = ("local", "localhost", "test", "staging", "demo")

# ---------------------------------------------------------------------------
# Flag taxonomy
#
# Everything reaching set_canister_config under ``test_flags`` used to be treated
# as a test flag, including settings that are legitimate production configuration.
# That had a real cost: enabling ``disable_monetary_tokens`` on a production realm
# was refused by the network gate, because the gate counted it as "a test flag is
# on". Splitting the two means the gate can stay strict about the flags that
# genuinely weaken the realm, without blocking product configuration.
# ---------------------------------------------------------------------------

# Weaken or bypass real behaviour. Refused on production networks.
TEST_ONLY_FLAGS = (
    "test_mode",
    "ii_bypass",
    "demo_data",
    "skip_terms",
    "skip_passport_zkproof",
)

# Ordinary product configuration that happens to have been introduced under the
# ``test_mode_`` prefix. Settable on any network. The canonical name is the key;
# the value is the legacy spelling still accepted on input for one release.
PRODUCT_FLAG_ALIASES = {
    "disable_monetary_tokens": "disable_monetary_tokens",
    "demo_notice_enabled": "demo_notice",
    "user_self_registration": "user_self_registration",
}

# Legacy spelling -> canonical name, for normalising an incoming payload.
_LEGACY_TO_CANONICAL = {
    legacy: canonical for canonical, legacy in PRODUCT_FLAG_ALIASES.items()
}


def is_test_only_flag(key: str) -> bool:
    """True when *key* names a flag that must be refused on a production network."""
    return key in TEST_ONLY_FLAGS


def normalize_flag_key(key: str) -> str:
    """Map a legacy product-flag spelling onto its canonical name."""
    return _LEGACY_TO_CANONICAL.get(key, key)


def test_flags_allowed(network: str, can_test_mode: bool) -> bool:
    """Return whether runtime test flags may be enabled for this network.

    The build variant is checked first and cannot be argued with: a production
    WASM does not contain the test code paths, so enabling the flags there would
    be a lie. Neither ``can_test_mode`` nor the network can override it.

    Past that, this fails closed: only the explicitly known non-production
    networks qualify. A realm whose ``network`` was never set is treated as
    production, because the field is self-declared and an empty value tells us
    nothing. ``can_test_mode`` (settable only by a controller) remains the
    deliberate override for a test build running on a production network.
    """
    from core.build_variant import test_flags_available

    if not test_flags_available():
        return False
    normalized = (network or "").strip().lower()
    return bool(can_test_mode) or normalized in NON_PRODUCTION_NETWORKS


def is_test_mode() -> bool:
    """True when the realm is running in test mode.

    Variant-gated, because every caller uses this to permit a test-only
    shortcut: seeding treasury allocations, forcing a procurement transition,
    and editing runtime flags without admin rights. A production WASM must
    answer False even for a realm carrying ``test_mode: true`` from an earlier
    deploy, or upgrading a test realm to a production build would leave those
    shortcuts reachable.
    """
    from core.build_variant import test_flags_available

    if not test_flags_available():
        return False
    return get_realm_flag("test_mode", False)


def is_ii_bypass_active() -> bool:
    """True when Internet Identity may be replaced by a deterministic keypair.

    Requires the test build variant. In a production WASM the code backing the
    bypass is absent, so a leftover ``test_mode_ii_bypass`` row grants nothing.
    """
    from core.build_variant import ii_bypass_available

    if not ii_bypass_available():
        return False
    return get_realm_flag("test_mode_ii_bypass", False)


def are_test_join_shortcuts_enabled() -> bool:
    """True when the test-mode shortcuts into ``join_realm`` are live.

    Covers the well-known sha256 join codes and admin self-registration without
    an invite. Both hand out the ``admin`` profile, so both hang off the II
    bypass and the test build alone.

    In particular they are *not* implied by ``user_self_registration``. That flag
    is ordinary product configuration meaning "anyone may join this realm", and
    it must never also mean "anyone may join it as an administrator".
    """
    return is_ii_bypass_active()


def is_demo_data_active() -> bool:
    """True when both ``test_mode`` and ``test_mode_demo_data`` are enabled.

    This is the gate the demo simulator uses to decide whether to auto-activate.
    """
    return get_realm_flag("test_mode", False) and get_realm_flag("test_mode_demo_data", False)


def skip_passport_zkproof() -> bool:
    """True when passport ZK-proof verification should be bypassed (test mode)."""
    return get_realm_flag("test_mode_skip_passport_zkproof", False)


def _realm_or_loaded(realm=None):
    if realm is not None:
        return realm
    try:
        from ggg import Realm

        return Realm.load("1")
    except Exception:
        return None


def is_monetary_tokens_disabled(realm=None) -> bool:
    """True when the host UI must gray out ckBTC/ckUSDC/ckEURC/Custom.

    Explicit Realm flag wins. If the field was never set (pre-flag backends),
    a manifest gos_environment of staging, demo, or test defaults to disabled.
    """
    from core.demo_notice import (
        default_disable_monetary_tokens,
        explicit_or_host_default,
        host_environment,
    )

    realm = _realm_or_loaded(realm)
    if realm is None:
        return False
    return explicit_or_host_default(
        getattr(realm, "test_mode_disable_monetary_tokens", None),
        host_environment(realm),
        default_disable_monetary_tokens,
    )


def is_demo_notice_enabled(realm=None) -> bool:
    """True when join + founder setup must show the configurable demo notice."""
    from core.demo_notice import default_demo_notice, explicit_or_host_default, host_environment

    realm = _realm_or_loaded(realm)
    if realm is None:
        return False
    return explicit_or_host_default(
        getattr(realm, "test_mode_demo_notice", None),
        host_environment(realm),
        default_demo_notice,
    )


def get_demo_notice_bodies(realm=None) -> dict:
    """Locale → notice body, English seeded from Legal when unset."""
    from core.demo_notice import resolve_demo_notice_bodies

    realm = _realm_or_loaded(realm)
    stored = getattr(realm, "demo_notice_body", "") if realm is not None else ""
    return resolve_demo_notice_bodies(stored)


def get_runtime_flags_payload() -> dict:
    """Lightweight runtime flags + identity for the frontend join flow.

    Avoids the heavy ``status()`` query (which can exceed the instruction limit on
    large staging realms). Used by ``get_runtime_flags`` and unit tests.
    """
    try:
        from ggg import Realm

        realm = Realm.load("1")
    except Exception:
        realm = None
    if not realm:
        return {"success": False, "error": "Realm not found"}
    import core.setup as _setup

    return {
        "success": True,
        "realm_name": str(getattr(realm, "name", "") or ""),
        "realm_manifesto": str(getattr(realm, "manifesto", "") or ""),
        "realm_welcome_message": str(getattr(realm, "welcome_message", "") or ""),
        "realm_stage": str(getattr(realm, "status", None) or "setup"),
        "open_registration": bool(getattr(realm, "open_registration", False)),
        "ai_assistant_enabled": bool(getattr(realm, "ai_assistant_enabled", True)),
        "logo_url": str(getattr(realm, "logo_url", "") or ""),
        "background_image_url": str(getattr(realm, "background_image_url", "") or ""),
        "network": str(getattr(realm, "network", "") or ""),
        "gos_environment": _setup.get_gos_environment(realm),
        # Which variant is running, so a sheet's converged_when can refuse a
        # test build on production (and a production build where the
        # environment expects test flags) without trusting the deploy log.
        "build_variant": _build_variant(),
        "test_mode": get_realm_flag("test_mode", False),
        "test_mode_ii_bypass": is_ii_bypass_active(),
        "test_mode_user_self_registration": get_realm_flag(
            "test_mode_user_self_registration", False
        ),
        "test_mode_demo_data": get_realm_flag("test_mode_demo_data", False),
        "test_mode_skip_terms": get_realm_flag("test_mode_skip_terms", False),
        "test_mode_skip_passport_zkproof": get_realm_flag(
            "test_mode_skip_passport_zkproof", False
        ),
        "test_mode_disable_monetary_tokens": is_monetary_tokens_disabled(realm),
        "test_mode_demo_notice": is_demo_notice_enabled(realm),
        "demo_notice_body": get_demo_notice_bodies(realm),
        "primary_color": _setup.get_primary_color(realm),
        **_realm_language_flags(realm),
    }


def _build_variant() -> str:
    try:
        from core.build_variant import BUILD_VARIANT
    except Exception:
        return "unknown"
    return str(BUILD_VARIANT)


def _realm_language_flags(realm) -> dict:
    from core.realm_locales import get_realm_languages

    languages, primary = get_realm_languages(realm)
    return {
        "languages": languages,
        "primary_language": primary,
    }
