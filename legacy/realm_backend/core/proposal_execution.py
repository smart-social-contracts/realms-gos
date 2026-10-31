"""Sandboxed governance proposal code execution (issue #265).

Proposal bytes are bound by checksum; power is bound by
``metadata.requested_permissions`` (bridge verb capabilities frozen at submit).
The host applies synchronous effects and drives deferred async effects (e.g.
``treasury.transfer`` → vault ICRC call).
"""

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from ic_python_logging import get_logger

logger = get_logger("core.proposal_execution")

_PROPOSAL_MAIN_ADAPTER = """
from ggg_sdk import hook

@hook
def __proposal_main__(args):
    fn = globals().get("main")
    if fn is None or not callable(fn):
        return {"success": False, "error": "proposal code must define main()"}
    result = fn()
    if hasattr(result, "__next__") or hasattr(result, "__aiter__"):
        return {
            "success": False,
            "error": (
                "async generators in main() are not supported; "
                "use bridge effects (e.g. realm.treasury.transfer)"
            ),
        }
    if isinstance(result, dict):
        return result
    return {"success": True, "result": result}
"""


def compute_code_checksum(code: str) -> str:
    """Return ``sha256:<hex>`` for proposal source bytes."""
    digest = hashlib.sha256((code or "").encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def verify_code_checksum(code: str, expected_checksum: str) -> Optional[str]:
    """Return an error string when *expected_checksum* does not match *code*."""
    if not expected_checksum or not str(expected_checksum).strip():
        return "checksum required but missing"
    actual = compute_code_checksum(code)
    if expected_checksum != actual:
        return f"checksum mismatch: expected {expected_checksum}, got {actual}"
    return None


def normalize_proposal_permissions(raw: Any) -> List[str]:
    """Validate and dedupe bridge verb capabilities from proposal metadata."""
    from core import codex_bridge

    known = set(codex_bridge.known_verbs())
    if not isinstance(raw, list):
        return []
    seen = set()
    out: List[str] = []
    for item in raw:
        if not isinstance(item, str) or item not in known or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def wrap_proposal_code(source: str) -> str:
    """Ensure proposal source defines ``main()`` and expose ``__proposal_main__``."""
    text = source or ""
    if "def main" not in text:
        body = "\n".join("    " + line for line in text.splitlines()) or "    pass"
        text = f"def main():\n{body}\n"
    return text + _PROPOSAL_MAIN_ADAPTER


def run_proposal_code_sandboxed(
    proposal_id: str,
    code: str,
    permissions: List[str],
) -> Tuple[Any, List[dict]]:
    """Execute proposal code in the subinterpreter; return ``(result, deferred)``."""
    from core import runtime_sandbox

    wrapped = wrap_proposal_code(code)
    context_id = f"proposal:{proposal_id}"
    return runtime_sandbox.run_bridge_hook(
        context_id,
        wrapped,
        "__proposal_main__",
        {},
        list(permissions),
        {},
        defer_async=True,
    )


def drive_async_effects(deferred: List[dict]):
    """Apply deferred async bridge effects (generator for ``yield from``)."""
    try:
        from core.extensions import extension_async_call
    except ImportError:
        extension_async_call = None

    if extension_async_call is None:
        raise RuntimeError("extension_async_call unavailable")

    for effect in deferred or []:
        verb = effect.get("verb")
        kwargs = effect.get("kwargs") or {}
        if verb == "treasury.transfer":
            vault_args = {
                "to_principal": kwargs.get("to_principal", ""),
                "amount": kwargs.get("amount", 0),
            }
            if kwargs.get("token"):
                vault_args["token"] = kwargs["token"]
            yield extension_async_call("vault", "transfer", json.dumps(vault_args))
            continue
        raise PermissionError(f"unsupported deferred effect '{verb}'")


def execute_proposal_code(proposal_id: str, code: str, permissions: List[str]):
    """Sandbox proposal ``main()``, apply sync effects, drive async ones."""
    result, deferred = run_proposal_code_sandboxed(proposal_id, code, permissions)
    if deferred:
        yield from drive_async_effects(deferred)
    return result
