"""Find the Realms product orchestra's public canisters from a realm.

Once per realm: fetch ``/.well-known/casals.json`` from that orchestra's Casals
domain (production ``casals.realmsgos.org``, otherwise
``casals.<env>.realmsgos.org``), ask its conductor to ``orchestra_resolve`` the fleet
file registry and the RLM ledger, and pin the ids in the setup config. Only an
explicit refresh looks again, so a changed domain cannot silently redirect a
realm's package source.
"""

from __future__ import annotations

import json

PRODUCT_DOMAIN = "realmsgos.org"
WELL_KNOWN_PATH = "/.well-known/casals.json"
FLEET_TRANSFORM = "http_transform"
FLEET_CANISTERS = {"file_registry": "fleet-file-registry", "token_ledger": "token-backend"}
_HTTP_CYCLES = 30_000_000_000
# The limit covers response headers too; a certified-assets domain sends ~6 KB
# of them (ic-certificate alone is ~3.4 KB) before the 70-byte body.
_HTTP_MAX_BYTES = 32_768


def casals_url(environment: str) -> str:
    name = (environment or "").strip().lower()
    if not name:
        raise ValueError("gos environment is empty")
    if not all(ch.isalnum() or ch == "-" for ch in name):
        raise ValueError(f"gos environment {name!r} is not a DNS label")
    host = f"casals.{PRODUCT_DOMAIN}" if name == "production" else f"casals.{name}.{PRODUCT_DOMAIN}"
    return f"https://{host}{WELL_KNOWN_PATH}"


def _principal_text(value, what: str) -> str:
    text = str(value or "").strip()
    if not text or not all(ch.isalnum() or ch == "-" for ch in text) or "-" not in text:
        raise ValueError(f"{what} is not a principal: {text[:80]!r}")
    return text


def parse_well_known(text: str) -> str:
    """The conductor id from ``/.well-known/casals.json``."""
    try:
        data = json.loads(text or "")
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"casals.json is not JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("casals.json must be an object")
    return _principal_text(data.get("conductor"), "casals.json conductor")


def parse_resolve_reply(text: str, name: str) -> str:
    try:
        data = json.loads(text or "")
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"resolve {name}: reply is not JSON") from exc
    if not isinstance(data, dict) or not data.get("ok"):
        error = data.get("error") if isinstance(data, dict) else ""
        raise ValueError(f"resolve {name}: {error or 'refused'}")
    return _principal_text(data.get("canister_id"), f"resolve {name}")


def pinned_fleet(realm) -> dict:
    from core.setup import get_setup_config

    fleet = get_setup_config(realm).get("fleet")
    if isinstance(fleet, dict) and fleet.get("file_registry"):
        return dict(fleet)
    return {}


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


def _fetch_text(url: str):
    """Generator: body of a small HTTPS GET."""
    from _cdk import ic, management_canister

    resp = yield management_canister.http_request(
        {
            "url": url,
            "max_response_bytes": _HTTP_MAX_BYTES,
            "method": {"get": None},
            "headers": [
                {"name": "User-Agent", "value": "realms-gos"},
                {"name": "Accept-Encoding", "value": "identity"},
            ],
            "body": None,
            "transform": {
                "function": (ic.id(), FLEET_TRANSFORM),
                "context": bytes(),
            },
        }
    ).with_cycles(_HTTP_CYCLES)

    if not isinstance(resp, dict) or "Ok" not in resp:
        raise RuntimeError(f"{url} download failed: {resp}")
    ok = resp["Ok"] or {}
    status = int(ok.get("status") or 0)
    text = _body_text(ok.get("body"))
    if status != 200:
        raise RuntimeError(f"{url} returned HTTP {status}: {text[:200]}")
    return text


def discover_fleet(realm, refresh: bool = False):
    """Generator: ``{conductor, file_registry, token_ledger}``, pinned after the first success."""
    pinned = pinned_fleet(realm)
    if pinned and not refresh:
        return pinned

    from _cdk import Principal
    from api.file_registry import CasalsConductorService, _unwrap_call_result
    from core.setup import get_gos_environment, update_setup_config

    conductor = parse_well_known((yield from _fetch_text(casals_url(get_gos_environment(realm)))))
    service = CasalsConductorService(Principal.from_str(conductor))
    fleet = {"conductor": conductor}
    for key, name in FLEET_CANISTERS.items():
        reply = yield service.orchestra_resolve(json.dumps({"name": name}))
        fleet[key] = parse_resolve_reply(_unwrap_call_result(reply), name)

    update_setup_config(realm, {"fleet": fleet})
    realm.file_registry_canister_id = fleet["file_registry"]
    return fleet


def resolve_catalog_registry_id(realm):
    """Generator: the fleet file-registry principal for this realm.

    A realm without ``gos_environment`` (local dev) keeps its stored
    ``file_registry_canister_id``.
    """
    from core.setup import get_gos_environment

    if not pinned_fleet(realm) and not get_gos_environment(realm):
        return (getattr(realm, "file_registry_canister_id", "") or "").strip()
    fleet = yield from discover_fleet(realm)
    return fleet["file_registry"]
