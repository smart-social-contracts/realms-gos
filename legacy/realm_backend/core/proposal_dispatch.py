"""Host-side typed proposal dispatcher (issue #305).

Voting stores a frozen ``metadata.action`` and this module applies it.
The sandbox is used only for ``code_execution``.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from ic_python_logging import get_logger

logger = get_logger("core.proposal_dispatch")

PROPOSAL_TYPES = frozenset({"transaction", "upgrade", "poll", "code_execution"})
UPGRADE_TARGETS = frozenset({"codex", "extension", "core", "codex_revert"})
HIGH_RISK_VERBS = frozenset(
    {
        "treasury.transfer",
        "member.assign_profile",
        "member.revoke_profile",
        "member.activate",
    }
)
CODEX_NAME_PREFIX = "proposal_"
_FORBIDDEN_SUBMIT_KEYS = ("code_inline", "codices", "codex_name")


def _is_positive_decimal(value: Any) -> bool:
    """``^[1-9][0-9]*$`` without ``re``: the canister's ``re`` has no
    ``compile``, and a failing module body makes every attribute of this
    lazily loaded module look missing."""
    return (
        isinstance(value, str)
        and value.isascii()
        and value.isdigit()
        and value[0] != "0"
    )


class DispatchError(Exception):
    def __init__(self, error: str, error_code: str = "dispatch_failed"):
        super().__init__(error)
        self.error = error
        self.error_code = error_code


def submit_gate(proposal_type: str, action: dict) -> str:
    """Operation required to *propose* this type. Execution uses the ballot."""
    if proposal_type == "transaction":
        return "transfer.create"
    if proposal_type == "upgrade":
        target = (action or {}).get("target")
        if target == "codex":
            return "codex.install"
        if target == "codex_revert":
            return "codex.revert"
        if target == "extension":
            return "extension.install"
        if target == "core":
            return "orchestration.approve"
        return "extension.install"
    return "proposal.create"


def uses_timelock(proposal_type: str, action: dict) -> bool:
    if proposal_type == "transaction":
        return True
    return proposal_type == "upgrade" and (action or {}).get("target") == "core"


def baton_configured() -> bool:
    from ggg import Realm

    realm = Realm.load("1")
    if not realm:
        return False
    try:
        manifest = json.loads(getattr(realm, "manifest_data", "") or "{}")
    except (json.JSONDecodeError, TypeError):
        return False
    cas = manifest.get("casals") if isinstance(manifest.get("casals"), dict) else {}
    return bool((cas.get("baton_canister_id") or "").strip())


def registry_canister_id() -> str:
    from ggg import Realm

    realm = Realm.load("1")
    if not realm:
        return ""
    direct = (getattr(realm, "file_registry_canister_id", "") or "").strip()
    if direct:
        return direct
    try:
        manifest = json.loads(getattr(realm, "manifest_data", "") or "{}")
    except (json.JSONDecodeError, TypeError):
        return ""
    cas = manifest.get("casals") if isinstance(manifest.get("casals"), dict) else {}
    return (cas.get("registry_canister_id") or "").strip()


def reject_forbidden_submit_keys(args: dict) -> Optional[dict]:
    for key in _FORBIDDEN_SUBMIT_KEYS:
        if key in args and args[key] not in (None, "", [], {}):
            return {
                "error": f"{key} is not accepted",
                "error_code": "forbidden_field",
            }
    return None


def freeze_action(
    proposal_type: str,
    raw_action: Any,
    *,
    source: str = "",
    source_url: str = "",
    requested_permissions: Any = None,
    proposal_id: str = "",
) -> Tuple[dict, List[str], Optional[dict]]:
    """Validate and freeze the action dict. Returns (action, permissions, error)."""
    if proposal_type not in PROPOSAL_TYPES:
        return {}, [], {
            "error": f"unknown proposal_type: {proposal_type}",
            "error_code": "unknown_proposal_type",
        }
    action = dict(raw_action) if isinstance(raw_action, dict) else {}

    if proposal_type != "code_execution" and requested_permissions:
        return {}, [], {
            "error": "requested_permissions is only valid on code_execution",
            "error_code": "permissions_not_allowed",
        }

    if proposal_type == "poll":
        return {}, [], None

    if proposal_type == "transaction":
        token = str(action.get("token") or "").strip()
        to_principal = str(action.get("to_principal") or "").strip()
        amount = action.get("amount")
        if not token or not to_principal:
            return {}, [], {
                "error": "token and to_principal are required",
                "error_code": "invalid_action",
            }
        if not _is_positive_decimal(amount):
            return {}, [], {
                "error": "amount must be a positive decimal string",
                "error_code": "invalid_amount",
            }
        return {
            "token": token,
            "to_principal": to_principal,
            "amount": amount,
        }, [], None

    if proposal_type == "upgrade":
        target = str(action.get("target") or "").strip()
        if target not in UPGRADE_TARGETS:
            return {}, [], {
                "error": "upgrade target must be codex, extension, core, or codex_revert",
                "error_code": "invalid_action",
            }
        if target == "codex_revert":
            return {"target": "codex_revert"}, [], None
        if target == "core":
            if not baton_configured():
                return {}, [], {
                    "error": "no baton configured",
                    "error_code": "no_baton",
                }
            action_id = str(action.get("action_id") or "").strip()
            if not action_id:
                return {}, [], {
                    "error": "action_id is required",
                    "error_code": "invalid_action",
                }
            decision = str(action.get("decision") or "approve").strip().lower()
            if decision not in ("approve", "reject"):
                return {}, [], {
                    "error": "decision must be approve or reject",
                    "error_code": "invalid_action",
                }
            return {
                "target": "core",
                "action_id": action_id,
                "decision": decision,
            }, [], None

        package_id = str(action.get("package_id") or "").strip()
        version = str(action.get("version") or "").strip()
        if not package_id:
            return {}, [], {
                "error": "package_id is required",
                "error_code": "invalid_action",
            }
        if package_id == "voting":
            return {}, [], {
                "error": "cannot upgrade the voting extension by proposal",
                "error_code": "self_upgrade_unsupported",
            }
        if not version or version.lower() == "latest":
            return {}, [], {
                "error": "version must be pinned; resolve latest at submit",
                "error_code": "version_not_pinned",
            }
        registry_id = str(action.get("registry_canister_id") or "").strip() or registry_canister_id()
        if not registry_id:
            return {}, [], {
                "error": "registry_canister_id is required",
                "error_code": "invalid_action",
            }
        return {
            "target": target,
            "package_id": package_id,
            "version": version,
            "registry_canister_id": registry_id,
        }, [], None

    # code_execution
    from core.proposal_execution import normalize_proposal_permissions

    text = (source or "").strip()
    url = (source_url or "").strip()
    if not text and not url:
        return {}, [], {
            "error": "source or source_url is required",
            "error_code": "missing_source",
        }
    perms = normalize_proposal_permissions(requested_permissions or [])
    if not proposal_id:
        return {}, [], {
            "error": "proposal_id required to name stored code",
            "error_code": "invalid_action",
        }
    return {
        "codex_name": f"{CODEX_NAME_PREFIX}{proposal_id}",
        "source_url": url,
    }, perms, None


def persist_code_execution_source(
    proposal_id: str,
    source: str,
    source_url: str = "",
) -> Tuple[str, str]:
    """Write proposal source to a reserved Codex row. Returns (name, checksum)."""
    from core.proposal_execution import compute_code_checksum
    from ggg import Codex

    name = f"{CODEX_NAME_PREFIX}{proposal_id}"
    checksum = compute_code_checksum(source)
    existing = Codex[name]
    if existing:
        existing.url = source_url or existing.url
        existing.checksum = checksum
        existing.description = f"Code execution source for {proposal_id}"
        existing.code = source
    else:
        row = Codex(
            name=name,
            url=source_url,
            checksum=checksum,
            description=f"Code execution source for {proposal_id}",
        )
        row.code = source
    return name, checksum


def _fail(proposal, metadata: dict, error: str, error_code: str):
    proposal.status = "failed"
    proposal.metadata = json.dumps({
        **metadata,
        "error": error,
        "error_code": error_code,
    })
    logger.error(f"Proposal {proposal.proposal_id} failed: {error_code}: {error}")


def _vault_ok(raw: Any) -> Tuple[bool, str, str]:
    parsed = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return False, str(raw)[:300], "vault_error"
    if not isinstance(parsed, dict):
        return False, str(parsed)[:300], "vault_error"
    if parsed.get("success") is False or parsed.get("err") or parsed.get("error"):
        return (
            False,
            str(parsed.get("error") or parsed.get("err") or parsed),
            str(parsed.get("error_code") or "vault_error"),
        )
    if parsed.get("success") is True or "ok" in parsed or "Ok" in parsed:
        return True, "", ""
    return False, str(parsed)[:300], "vault_error"


def _json_ok(raw: Any) -> Tuple[bool, str, str]:
    parsed = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return False, str(raw)[:300], "install_failed"
    if not isinstance(parsed, dict):
        return False, str(parsed)[:300], "install_failed"
    if parsed.get("status") == "in_progress":
        return False, "codex install still in progress", "install_in_progress"
    if parsed.get("success") is False or parsed.get("error"):
        return (
            False,
            str(parsed.get("error") or parsed),
            str(parsed.get("error_code") or "install_failed"),
        )
    if parsed.get("success") is True:
        return True, "", ""
    return False, str(parsed)[:300], "install_failed"


def dispatch_proposal(proposal):
    """Apply a frozen typed action. Caller must already have set status=executing.

    Poll is handled inline at ballot close and should not reach here.
    """
    from core.proposal_execution import (
        execute_proposal_code,
        normalize_proposal_permissions,
        verify_code_checksum,
    )

    if getattr(proposal, "status", "") != "executing":
        raise DispatchError(
            f"dispatcher refuses status {proposal.status}",
            "not_executing",
        )

    metadata = {}
    try:
        metadata = json.loads(proposal.metadata or "{}")
    except (json.JSONDecodeError, TypeError):
        metadata = {}

    if metadata.get("defer_execution"):
        logger.info(f"Skipping {proposal.proposal_id}: defer_execution")
        return
    if metadata.get("dispatch_started"):
        raise DispatchError("dispatch already started", "already_dispatching")
    metadata["dispatch_started"] = True
    proposal.metadata = json.dumps(metadata)

    proposal_type = metadata.get("proposal_type")
    action = metadata.get("action") if isinstance(metadata.get("action"), dict) else {}

    if proposal_type not in PROPOSAL_TYPES:
        _fail(proposal, metadata, f"unknown proposal_type: {proposal_type}", "unknown_proposal_type")
        return

    if proposal_type == "poll":
        proposal.status = "executed"
        return

    if proposal_type == "transaction":
        from core.extensions import extension_async_call

        vault_args = json.dumps({
            "to_principal": action.get("to_principal", ""),
            "amount": int(action["amount"]),
            "token": action.get("token", ""),
        })
        raw = yield extension_async_call("vault", "transfer", vault_args)
        ok, err, code = _vault_ok(raw)
        if not ok:
            _fail(proposal, metadata, err, code)
            return
        proposal.status = "executed"
        return

    if proposal_type == "upgrade":
        target = action.get("target")
        if target == "core":
            raw = yield from _approve_orchestration(action)
            ok, err, code = _json_ok(raw)
            if not ok:
                _fail(proposal, metadata, err, code)
                return
            proposal.status = "executed"
            return
        if target == "codex_revert":
            from core.codex_overlay import revert

            raw = json.dumps(revert(authorized_by_vote=True))
            ok, err, code = _json_ok(raw)
            if not ok:
                _fail(proposal, metadata, err, code)
                return
            proposal.status = "executed"
            return
        from api.file_registry import (
            install_codex_from_registry,
            install_extension_from_registry,
        )

        registry_id = action.get("registry_canister_id") or registry_canister_id()
        package_id = action.get("package_id")
        version = action.get("version")
        if target == "codex":
            raw = yield from install_codex_from_registry(
                registry_id, package_id, version
            )
        else:
            raw = yield from install_extension_from_registry(
                registry_id, package_id, version
            )
        ok, err, code = _json_ok(raw)
        if not ok:
            _fail(proposal, metadata, err, code)
            return
        proposal.status = "executed"
        return

    # code_execution
    from ggg import Codex

    name = action.get("codex_name") or ""
    if not name.startswith(CODEX_NAME_PREFIX):
        _fail(proposal, metadata, "stored codex name is not reserved", "invalid_codex_name")
        return
    row = Codex[name]
    code = getattr(row, "code", None) if row else None
    if not code:
        _fail(proposal, metadata, "stored proposal code is missing", "missing_source")
        return
    checksum_error = verify_code_checksum(code, proposal.code_checksum or "")
    if checksum_error:
        _fail(proposal, metadata, checksum_error, "checksum_mismatch")
        return
    permissions = normalize_proposal_permissions(
        metadata.get("requested_permissions", [])
    )
    try:
        result = yield from execute_proposal_code(
            proposal.proposal_id, code, permissions
        )
    except Exception as e:
        _fail(proposal, metadata, f"Code execution failed: {e}", "sandbox_error")
        return
    if not isinstance(result, dict) or result.get("success") is not True:
        err = ""
        if isinstance(result, dict):
            err = str(result.get("error") or result)
        else:
            err = str(result)
        _fail(proposal, metadata, err or "sandbox returned unsuccessful", "sandbox_unsuccessful")
        return
    proposal.status = "executed"


def _approve_orchestration(action: dict):
    from _cdk import ic
    from _cdk import CallResult, Principal
    from ggg import Realm

    realm = Realm.load("1")
    baton_id = ""
    if realm:
        try:
            manifest = json.loads(getattr(realm, "manifest_data", "") or "{}")
            cas = manifest.get("casals") if isinstance(manifest.get("casals"), dict) else {}
            baton_id = (cas.get("baton_canister_id") or "").strip()
        except (json.JSONDecodeError, TypeError):
            baton_id = ""
    if not baton_id:
        return json.dumps({
            "success": False,
            "error": "no baton configured",
            "error_code": "no_baton",
        })

    action_id = action.get("action_id") or ""
    decision = action.get("decision") or "approve"
    method = "submit_approval" if decision == "approve" else "reject_action"
    escaped = action_id.replace("\\", "\\\\").replace('"', '\\"')
    call_res: CallResult = yield ic.call_raw(
        Principal.from_str(baton_id),
        method,
        ic.candid_encode(f'("{escaped}")'),
        0,
    )
    if isinstance(call_res, dict) and call_res.get("Err") is not None:
        return json.dumps({
            "success": False,
            "error": str(call_res["Err"]),
            "error_code": "orchestration_failed",
        })
    raw = call_res.get("Ok") if isinstance(call_res, dict) else getattr(call_res, "Ok", call_res)
    decoded = ic.candid_decode(raw) if isinstance(raw, (bytes, bytearray)) else raw
    reply = parse_baton_reply(decoded)
    if reply.get("ok") is not True:
        return json.dumps({
            "success": False,
            "error": f"baton refused {method}: {reply.get('error') or reply}",
            "error_code": "orchestration_refused",
            "reply": reply,
        })
    return json.dumps({"success": True, "reply": reply})


def parse_baton_reply(decoded: Any) -> dict:
    """A Baton text reply (plain JSON, or candid text ``("<json>")``) as a dict."""
    if isinstance(decoded, dict):
        return decoded
    if isinstance(decoded, (list, tuple)) and decoded:
        decoded = decoded[0]
    s = str(decoded or "").strip()
    if s.startswith("(") and ")" in s:
        inner = s[1:s.rfind(")")].strip().rstrip(",").strip()
        if inner.startswith('"') and inner.endswith('"'):
            try:
                inner_text = json.loads(inner.replace("\\'", "'"))
                if isinstance(inner_text, str):
                    s = inner_text
            except json.JSONDecodeError:
                pass
    try:
        parsed = json.loads(s)
        return parsed if isinstance(parsed, dict) else {"result": parsed}
    except json.JSONDecodeError:
        return {"raw": s[:500]}
