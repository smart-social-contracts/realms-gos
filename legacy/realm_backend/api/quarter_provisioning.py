"""Inter-canister transport for quarter auto-scaling (issue #156).

Thin yield/generator wrappers (mirroring ``api/cross_quarter.py``); the
*decision* of whether to scale lives in ``core.autoscale``. The capital is a
commander of its own Casals stand: it asks Casals for the next numbered
template member (``create_stand({name: <stand>, members: ["<stand>-quarter-<n>"]})``),
polls ``get_bindings`` until the conductor has built it, then drives
``bootstrap_as_quarter`` on the fresh canister (the template lists the capital
among the quarter's controllers).
"""

import json
from typing import Dict

from _cdk import Async, CallResult, Principal, Service, service_query, service_update, text
from ic_python_logging import get_logger

logger = get_logger("api.quarter_provisioning")


def _unwrap_call_text(result) -> str:
    """Extract the text payload from an inter-canister ``CallResult``.

    Basilisk hands back a ``CallResult`` variant (``{Ok, Err}``); ``str(result)``
    yields its Python repr (``{'Ok': '...'}``), which is *not* valid JSON. Mirror
    the unwrap used in ``api/file_registry.py`` so callers can ``json.loads`` the
    actual canister response. A rejection (``Err``) is surfaced as a parseable
    ``{"err": ...}`` envelope.
    """
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        return result.get("Ok", result.get("ok", str(result)))
    if hasattr(result, "Ok") and result.Ok is not None:
        return result.Ok
    if hasattr(result, "Err") and result.Err is not None:
        return json.dumps({"err": str(result.Err)})
    return str(result)


def parse_casals_spec(manifest_data: str, next_index: int) -> Dict:
    """Parse the ``casals`` block out of a realm's ``manifest_data``.

    Returns the spec dict consumed by ``run_quarter_scaling`` or ``None`` when
    ``stand`` is missing. Pure + importable so it can be unit-tested without
    importing the canister ``main`` module.
    """
    try:
        manifest = json.loads(manifest_data or "{}")
    except Exception:
        manifest = {}
    cas = (manifest.get("casals") if isinstance(manifest, dict) else None) or {}
    stand = (cas.get("stand") or "").strip()
    if not stand:
        return None
    return {
        "stand": stand,
        "name": f"{stand}-quarter-{next_index}",
        "casals_canister_id": (cas.get("casals_canister_id") or "").strip(),
        "registry_canister_id": (cas.get("registry_canister_id") or "").strip(),
        "codex": cas.get("codex") or None,
        "extensions": cas.get("extensions") or [],
        "frontend_canister_id": (cas.get("frontend_canister_id") or "").strip(),
    }


class CasalsService(Service):
    """The two Casals calls a stand needs to grow: ``create_stand`` (idempotent
    member union, authorized for the stand's commander) and ``get_bindings``."""

    @service_update
    def create_stand(self, args: text) -> text:
        ...

    @service_query
    def get_bindings(self) -> text:
        ...


class QuarterBootstrapService(Service):
    """Remote interface of a freshly minted quarter's bootstrap entry point.

    Runs on the quarter (same realm-backend WASM); the capital may call it
    because the stand template makes the capital a controller of its quarters.
    """

    @service_update
    def bootstrap_as_quarter(self, args: text) -> text:
        ...


def _parse_casals_reply(raw: str) -> Dict:
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {"ok": False, "error": f"Unparseable Casals response: {raw[:200]}"}
    if not isinstance(parsed, dict):
        return {"ok": False, "error": f"Unexpected Casals response: {raw[:200]}"}
    if parsed.get("err") is not None:
        return {"ok": False, "error": str(parsed["err"])}
    if parsed.get("ok") is False:
        return {"ok": False, "error": str(parsed.get("error") or "Casals call failed")}
    return parsed


def request_casals_member(casals_canister_id: str, stand: str, member: str) -> Async[Dict]:
    """Ask Casals to add ``member`` (``<stand>-quarter-<n>``) to the capital's
    stand. Idempotent — safe to repeat every tick until the binding appears.
    Returns ``{"ok": True, "members": [...]}`` or ``{"ok": False, "error": ...}``."""
    logger.info(f"Requesting member {member} of stand {stand} from Casals {casals_canister_id}")
    try:
        service = CasalsService(Principal.from_str(casals_canister_id))
        result: CallResult[text] = yield service.create_stand(json.dumps({"name": stand, "members": [member]}))
        return _parse_casals_reply(_unwrap_call_text(result))
    except Exception as e:
        logger.error(f"Error calling Casals create_stand via {casals_canister_id}: {e}")
        return {"ok": False, "error": str(e)}


def lookup_casals_binding(casals_canister_id: str, name: str) -> Async[str]:
    """The canister id Casals has bound to ``name``, or ``""`` while the
    conductor has not built it yet."""
    try:
        service = CasalsService(Principal.from_str(casals_canister_id))
        result: CallResult[text] = yield service.get_bindings()
        parsed = _parse_casals_reply(_unwrap_call_text(result))
        bindings = parsed.get("bindings") if parsed.get("ok", True) else None
        return (bindings or {}).get(name, "") if isinstance(bindings, dict) else ""
    except Exception as e:
        logger.error(f"Error reading Casals bindings via {casals_canister_id}: {e}")
        return ""


def bootstrap_quarter(quarter_canister_id: str, args: Dict) -> Async[Dict]:
    """Drive ``bootstrap_as_quarter`` on a freshly minted quarter canister.

    ``args`` is forwarded as JSON (parent realm id, registry id, optional codex
    and extension lists). Returns the parsed quarter response.
    """
    logger.info(f"Bootstrapping quarter {quarter_canister_id}: {args}")
    try:
        service = QuarterBootstrapService(Principal.from_str(quarter_canister_id))
        result: CallResult[text] = yield service.bootstrap_as_quarter(json.dumps(args))
        raw = _unwrap_call_text(result)
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {"success": False, "error": f"Unparseable quarter response: {raw[:200]}"}
    except Exception as e:
        logger.error(f"Error bootstrapping quarter {quarter_canister_id}: {e}")
        return {"success": False, "error": str(e)}
