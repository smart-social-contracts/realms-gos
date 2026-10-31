"""Inter-canister transport for capital→quarter codex sync (issue #295).

Holds the ``Service`` handle + async helper the capital uses to ask a quarter
to open a codex sync ballot. Mirrors ``api/quarter_provisioning`` /
``api/cross_quarter`` so ``main.py`` stays a thin wrapper.
"""

import json
from typing import Dict

from _cdk import Async, CallResult, Principal, Service, service_update, text
from ic_python_logging import get_logger

logger = get_logger("api.quarter_sync")


def _unwrap_call_text(result) -> str:
    """Extract text from an inter-canister ``CallResult`` (same as provisioning)."""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        if result.get("Ok") is not None:
            return result["Ok"]
        if result.get("ok") is not None:
            return result["ok"]
        if result.get("Err") is not None:
            return json.dumps({"err": str(result["Err"])})
        return str(result)
    if hasattr(result, "Ok") and result.Ok is not None:
        return result.Ok
    if hasattr(result, "Err") and result.Err is not None:
        return json.dumps({"err": str(result.Err)})
    if result is None:
        return ""
    return str(result)


class QuarterCodexSyncService(Service):
    """Remote interface of a quarter's codex-sync entry point."""

    @service_update
    def request_codex_sync(self, payload: text) -> text:
        ...


def request_quarter_codex_sync(quarter_canister_id: str, payload: Dict) -> Async[Dict]:
    """Ask a quarter to open a codex sync ballot.

    ``payload`` is forwarded as JSON (``target``, ``registry_canister_id``, …).
    Returns the parsed quarter response.
    """
    logger.info(f"Requesting codex sync on quarter {quarter_canister_id}: {payload}")
    try:
        service = QuarterCodexSyncService(Principal.from_str(quarter_canister_id))
        result: CallResult[text] = yield service.request_codex_sync(json.dumps(payload))
        raw = _unwrap_call_text(result)
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {"success": False, "error": f"Unparseable quarter response: {raw[:200]}"}
        if isinstance(parsed, dict):
            return parsed
        return {"success": True, "result": parsed}
    except Exception as e:
        logger.error(f"Error requesting codex sync on quarter {quarter_canister_id}: {e}")
        return {"success": False, "error": str(e)}
