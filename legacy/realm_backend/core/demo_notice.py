"""Host demo notice + monetary-token test flags.

The English notice body is the Legal seed (kept in-module for the canister;
``demo_notice_defaults.json`` is the host Legal copy — tests require a match).
Primary-locale slots are stored on the Realm entity and left empty until
Legal/Story writes them — this module does not invent translations.

Staging/demo default: monetary tokens disabled and the demo notice on.
Test (gos.earth) default: monetary tokens disabled; notice follows skip_terms.
"""

from __future__ import annotations

import json

HOST_DISABLE_MONETARY_NETWORKS = frozenset({"staging", "demo", "test"})
HOST_DEMO_NOTICE_NETWORKS = frozenset({"staging", "demo"})

# Catalog locales that may receive a later Legal translation. Empty string = slot.
NOTICE_LOCALE_SLOTS = ("en", "es", "de", "fr", "it", "zh-CN", "ca-valencia")


# Canister Python has no module path and no readable copy of the JSON, so the
# Legal English seed lives here. ``demo_notice_defaults.json`` stays the host
# Legal source; tests require the two texts to match.
_SEEDED_ENGLISH_FALLBACK = (
    "Before you continue. Please read. This is a notice, not a contract.\n\n"
    "1. This software is for demo / experimental purposes. Do not use for any "
    "production purposes. This software can break. Data can be lost. The software "
    "may change without notice. There is no service level agreement (SLA).\n\n"
    "2. Do not send real funds. Do not enter personal data. Billing is off. Real "
    "tokens are not available. This software has not been security-audited.\n\n"
    "3. This is not a government service.\n"
    "4. Any data in this software can be altered or destroyed without notice.\n"
    "5. Some actions may be recorded on the Internet Computer and may not be erasable."
)


DEFAULT_DEMO_NOTICE_EN = _SEEDED_ENGLISH_FALLBACK.replace("sofware", "software")


def normalize_network(network: str | None) -> str:
    return (network or "").strip().lower()


def manifest_environment(realm) -> str:
    """``gos_environment`` stored on the realm manifest. Empty when unset."""
    if realm is None:
        return ""
    try:
        manifest = json.loads(getattr(realm, "manifest_data", "") or "{}")
    except (json.JSONDecodeError, TypeError, ValueError):
        return ""
    setup = manifest.get("setup") if isinstance(manifest, dict) else None
    if not isinstance(setup, dict):
        return ""
    return str(setup.get("gos_environment") or "").strip().lower()


def host_environment(realm) -> str:
    """Environment name for host defaults.

    The manifest value wins. When it has never been set, the replica field is
    the only name older realms stored.
    """
    env = manifest_environment(realm)
    if env:
        return env
    if realm is None:
        return ""
    return str(getattr(realm, "network", "") or "").strip().lower()


def default_disable_monetary_tokens(network: str | None) -> bool:
    return normalize_network(network) in HOST_DISABLE_MONETARY_NETWORKS


def default_demo_notice(network: str | None) -> bool:
    return normalize_network(network) in HOST_DEMO_NOTICE_NETWORKS


def seed_host_flag_defaults(obj: dict, network: str | None = None) -> dict:
    """Set missing host go-live fields on a Realm migrate dict. Never overwrites."""
    net = normalize_network(network if network is not None else obj.get("network"))
    obj.setdefault(
        "test_mode_disable_monetary_tokens", default_disable_monetary_tokens(net)
    )
    obj.setdefault("test_mode_demo_notice", default_demo_notice(net))
    obj.setdefault("demo_notice_body", "")
    return obj


def seed_host_defaults_on_first_environment(realm, incoming_flags: dict | None = None) -> None:
    """Seed monetary-token and demo-notice defaults from the manifest environment.

    Caller invokes this only when ``gos_environment`` was empty before this
    configure call. Keys already present in *incoming_flags* stay as set.
    """
    environment = manifest_environment(realm)
    if not environment:
        return
    incoming = incoming_flags or {}
    if "disable_monetary_tokens" not in incoming:
        realm.test_mode_disable_monetary_tokens = default_disable_monetary_tokens(
            environment
        )
    if "demo_notice_enabled" not in incoming:
        realm.test_mode_demo_notice = default_demo_notice(environment)


def parse_notice_bodies(raw) -> dict[str, str]:
    """Parse a stored JSON object (or JSON string) of locale → body."""
    if raw is None:
        data = {}
    elif isinstance(raw, dict):
        data = raw
    elif isinstance(raw, str):
        text = raw.strip()
        if not text:
            data = {}
        else:
            try:
                parsed = json.loads(text)
            except (TypeError, ValueError):
                # A bare English override, not a locale map.
                data = {"en": text}
            else:
                data = parsed if isinstance(parsed, dict) else {}
    else:
        data = {}
    bodies: dict[str, str] = {}
    for key, value in data.items():
        loc = str(key).strip()
        if not loc:
            continue
        bodies[loc] = str(value or "").strip()
    return bodies


def dump_notice_bodies(bodies: dict[str, str]) -> str:
    return json.dumps(bodies, ensure_ascii=True, sort_keys=True)


def resolve_demo_notice_bodies(stored_raw=None) -> dict[str, str]:
    """Return locale → body, seeding English from Legal when empty.

    Other catalog locales are present as empty slots unless a stored
    translation exists. Callers must not invent missing translations.
    """
    stored = parse_notice_bodies(stored_raw)
    resolved: dict[str, str] = {}
    for loc in NOTICE_LOCALE_SLOTS:
        resolved[loc] = stored.get(loc, "")
    english = stored.get("en") or DEFAULT_DEMO_NOTICE_EN
    resolved["en"] = english.replace("sofware", "software")
    for loc, body in stored.items():
        if loc not in resolved:
            resolved[loc] = body
    return resolved


def explicit_or_host_default(explicit, network: str | None, host_default) -> bool:
    """Use an explicit Realm flag when present; otherwise the host-network default."""
    if explicit is not None:
        return bool(explicit)
    return bool(host_default(network))


def is_monetary_token_choice(choice_id: str) -> bool:
    """True for catalog tokens that are not the shared REALMS token."""
    ident = (choice_id or "").strip()
    if not ident:
        return False
    return ident.upper() not in {"REALMS"}
