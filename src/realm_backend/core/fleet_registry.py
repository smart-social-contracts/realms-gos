"""Resolve a realm's fleet file-registry from its GaaS environment name.

GaaS does not know these principals. The map is ``fleet-registries.json`` at
the root of the Realms GitHub repo, fetched when a catalog or codex install
needs it. ``network: ic`` is not an environment name.
"""

from __future__ import annotations

import json

FLEET_REGISTRIES_URL = (
    "https://raw.githubusercontent.com/smart-social-contracts/realms-gos/main/fleet-registries.json"
)
FLEET_REGISTRIES_TRANSFORM = "http_transform"
_HTTP_CYCLES = 30_000_000_000
_HTTP_MAX_BYTES = 100_000

_cached_body = ""


def parse_fleet_registries(text: str) -> dict:
    """Return ``{environment: principal}``. Keys starting with ``_`` are comments."""
    try:
        data = json.loads(text or "")
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"fleet-registries.json is not JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("fleet-registries.json must be an object")
    out = {}
    for key, value in data.items():
        name = str(key or "").strip().lower()
        if not name or name.startswith("_"):
            continue
        principal = str(value or "").strip()
        if not principal:
            raise ValueError(f"fleet-registries.json has an empty principal for {name!r}")
        out[name] = principal
    return out


def registry_for_environment(table: dict, environment: str) -> str:
    """Principal for ``environment``, or raise ValueError."""
    name = (environment or "").strip().lower()
    if not name:
        raise ValueError("gos environment is empty")
    principal = (table.get(name) or "").strip()
    if not principal:
        known = ", ".join(sorted(table)) or "(none)"
        raise ValueError(
            f"no fleet file-registry for environment {name!r} "
            f"(published environments: {known})"
        )
    return principal


def _body_text(body) -> str:
    if body is None:
        return ""
    if isinstance(body, str):
        return body
    if isinstance(body, (bytes, bytearray)):
        return bytes(body).decode("utf-8")
    if isinstance(body, list):
        return bytes(body).decode("utf-8")
    return str(body)


def fetch_fleet_registries_text():
    """Generator: JSON text of the published map. Cached for this canister process."""
    global _cached_body
    if _cached_body:
        return _cached_body

    from _cdk import ic, management_canister

    resp = yield management_canister.http_request(
        {
            "url": FLEET_REGISTRIES_URL,
            "max_response_bytes": _HTTP_MAX_BYTES,
            "method": {"get": None},
            "headers": [
                {"name": "User-Agent", "value": "realms-gos"},
                {"name": "Accept-Encoding", "value": "identity"},
            ],
            "body": None,
            "transform": {
                "function": (ic.id(), FLEET_REGISTRIES_TRANSFORM),
                "context": bytes(),
            },
        }
    ).with_cycles(_HTTP_CYCLES)

    if not isinstance(resp, dict) or "Ok" not in resp:
        raise RuntimeError(f"fleet registry list download failed: {resp}")
    ok = resp["Ok"] or {}
    status = int(ok.get("status") or 0)
    text = _body_text(ok.get("body"))
    if status != 200:
        raise RuntimeError(
            f"fleet registry list download returned HTTP {status}: {text[:200]}"
        )
    # Validate before caching so a bad document is not sticky.
    parse_fleet_registries(text)
    _cached_body = text
    return text


def clear_fleet_registries_cache() -> None:
    global _cached_body
    _cached_body = ""


def resolve_catalog_registry_id(realm):
    """Generator: fleet file-registry principal for this realm.

    A realm with ``gos_environment`` loads the map from GitHub. Otherwise the
    stored ``file_registry_canister_id`` is used, which is how realms created
    before this contract keep working.
    """
    from core.setup import get_gos_environment

    environment = get_gos_environment(realm)
    if not environment:
        return (getattr(realm, "file_registry_canister_id", "") or "").strip()

    text = yield from fetch_fleet_registries_text()
    registry_id = registry_for_environment(parse_fleet_registries(text), environment)
    if registry_id and (getattr(realm, "file_registry_canister_id", "") or "") != registry_id:
        realm.file_registry_canister_id = registry_id
    return registry_id
