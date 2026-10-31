"""Sandbox policy administration with org-policy gating.

Same pattern as position/member admin:

- **Direct** (root department policy is 1/1): ``update_config`` runs immediately.
- **Governed** (any other root policy): return ``requires_confirmation`` until
  the caller re-submits with ``confirm=True``, then create a root-scoped
  Proposal whose inline code calls the same ``update_config``.
"""

import json
from typing import Any, Dict

from ic_python_logging import get_logger

logger = get_logger("core.sandbox_admin")


def _root_department():
    from ggg import ROOT_ORG_NAME, Department

    root = Department[ROOT_ORG_NAME]
    if root:
        return root
    for dept in Department.instances():
        if getattr(dept, "is_root", False):
            return dept
    return None


def apply_sandbox_config_change(
    patch: dict,
    *,
    confirm: bool = False,
    proposer=None,
) -> Dict[str, Any]:
    """Apply a sandbox config patch, or escalate to a governance proposal.

    Returns a result dict shaped like extension responses::

        {success, data: {applied: "direct"|"proposal", ...} | requires_confirmation}
        {success: False, error}
    """
    from core import runtime_sandbox
    from core.position_admin import policy_is_direct

    if not isinstance(patch, dict) or not patch:
        return {"success": False, "error": "config patch object is required"}

    try:
        _validate_patch(patch)
    except ValueError as e:
        return {"success": False, "error": str(e)}

    root = _root_department()
    summary = runtime_sandbox.describe_config_patch(patch)

    if root is None or policy_is_direct(root):
        try:
            new_config = runtime_sandbox.update_config(patch)
        except ValueError as e:
            return {"success": False, "error": str(e)}
        warning = None
        if new_config.get("enabled") and not runtime_sandbox.is_sandbox_available():
            warning = (
                "Sandboxing enabled but this canister image has no "
                "_basilisk_sandbox — every extension resolving to 'sandbox' "
                "will fail its calls (there is no in-process fallback)"
            )
        logger.info(f"Sandbox config applied directly: {summary}")
        return {
            "success": True,
            "data": {
                "applied": "direct",
                "summary": summary,
                "config": new_config,
                "available": runtime_sandbox.is_sandbox_available(),
                "warning": warning,
                "governed_by": getattr(root, "name", "root") if root else "root",
            },
        }

    policy = f"{root.policy_threshold_m}/{root.policy_threshold_n}"
    if not confirm:
        return {
            "success": True,
            "data": {
                "requires_confirmation": True,
                "summary": summary,
                "governed_by": root.name,
                "policy": policy,
                "patch": patch,
            },
        }

    try:
        proposal_data = _submit_sandbox_proposal(patch, root, summary, proposer)
    except Exception as e:
        logger.error(f"Failed to submit sandbox config proposal: {e}")
        return {"success": False, "error": str(e)}

    return {
        "success": True,
        "data": {
            "applied": "proposal",
            "summary": summary,
            "governed_by": root.name,
            "policy": policy,
            **proposal_data,
        },
    }


def _validate_patch(patch: dict) -> None:
    """Raise ValueError if *patch* would be rejected by ``update_config``.

    Performs the same checks without writing the config file by applying the
    merge logic against an in-memory copy and discarding the result.
    """
    from core import runtime_sandbox

    allowed = set(runtime_sandbox.DEFAULT_CONFIG.keys())
    unknown = set(patch.keys()) - allowed
    if unknown:
        raise ValueError(f"unknown sandbox config keys: {sorted(unknown)}")

    # Deep-enough dry run: stash cache, write to a temp path — too heavy.
    # Instead duplicate the field checks from update_config.
    if "enabled" in patch and not isinstance(patch["enabled"], bool):
        raise ValueError("'enabled' must be a boolean")
    if (
        "default_mode" in patch
        and patch["default_mode"] not in runtime_sandbox.VALID_MODES
    ):
        raise ValueError(f"'default_mode' must be one of {runtime_sandbox.VALID_MODES}")
    if "budget" in patch:
        if (
            not isinstance(patch["budget"], int)
            or isinstance(patch["budget"], bool)
            or patch["budget"] < 0
        ):
            raise ValueError("'budget' must be a non-negative integer")
    if "extensions" in patch:
        if not isinstance(patch["extensions"], dict):
            raise ValueError("'extensions' must be an object of {ext_id: mode}")
        for ext_id, mode in patch["extensions"].items():
            if mode in (None, ""):
                continue
            if mode not in runtime_sandbox.VALID_MODES:
                raise ValueError(
                    f"extension '{ext_id}': mode must be one of "
                    f"{runtime_sandbox.VALID_MODES}"
                )
            if mode == "sandbox" and runtime_sandbox.is_system_extension(ext_id):
                raise ValueError(
                    f"extension '{ext_id}' is a core/system extension and "
                    f"cannot be sandboxed"
                )
            if (
                mode == "sandbox"
                and runtime_sandbox.manifest_runtime_mode(ext_id) == "in_process"
            ):
                raise ValueError(
                    f'extension \'{ext_id}\' declares "runtime": "in_process" '
                    f"and cannot be sandboxed — it imports host modules, so the "
                    f"spawn would fail and there is no in-process fallback"
                )
    if "codex_hooks" in patch:
        raw = patch["codex_hooks"]
        if not isinstance(raw, dict):
            raise ValueError("'codex_hooks' must be an object")
        if (
            "default_mode" in raw
            and raw["default_mode"] not in runtime_sandbox.VALID_MODES
        ):
            raise ValueError(
                f"codex_hooks.default_mode must be one of {runtime_sandbox.VALID_MODES}"
            )
        hooks = raw.get("hooks")
        if hooks is not None:
            if not isinstance(hooks, dict):
                raise ValueError("codex_hooks.hooks must be an object")
            for name, mode in hooks.items():
                if mode in (None, ""):
                    continue
                if mode not in runtime_sandbox.VALID_MODES:
                    raise ValueError(
                        f"codex hook '{name}': mode must be one of "
                        f"{runtime_sandbox.VALID_MODES}"
                    )
                if (
                    str(name) in runtime_sandbox.FORCE_IN_PROCESS_HOOKS
                    and mode == "sandbox"
                ):
                    raise ValueError(
                        f"codex hook '{name}' cannot be sandboxed "
                        f"(async / seeding hooks are always in-process)"
                    )


def _submit_sandbox_proposal(patch: dict, root, summary: str, proposer) -> dict:
    from _cdk import ic
    from ggg import Proposal, User

    if proposer is None:
        caller = ic.caller().to_str()
        proposer = User[caller]
        if not proposer:
            raise ValueError(f"Caller user '{caller}' not found")

    proposal_num = len(Proposal.instances()) + 1
    proposal_id = f"prop_{proposal_num:03d}"

    from core import runtime_sandbox

    code_inline = runtime_sandbox.build_proposal_code(patch)
    metadata = {
        "proposal_type": "sandbox_config",
        "org_scope": root.name,
        "sandbox_config_patch": patch,
        "code_inline": code_inline,
        "codex_name": f"sandbox_config_{proposal_id}",
    }

    voting_window = 604_800
    try:
        from ggg import Realm

        realm = Realm[1]
        if realm and realm.calendar and realm.calendar.voting_window:
            voting_window = int(realm.calendar.voting_window)
    except Exception:
        pass
    deadline_s = ic.time() // 1_000_000_000 + voting_window

    proposal = Proposal(
        proposal_id=proposal_id,
        title=summary[:120],
        description=(
            f"Sandbox execution policy change "
            f"(policy {root.policy_threshold_m}/{root.policy_threshold_n}). "
            f"Proposed by {proposer.id}. {summary}"
        ),
        code_url="",
        code_checksum="",
        proposer=proposer,
        status="voting",
        voting_deadline=str(deadline_s),
        votes_yes=0.0,
        votes_no=0.0,
        votes_abstain=0.0,
        total_voters=0.0,
        required_threshold=1.0,
        org_scope=root.name,
        metadata=json.dumps(metadata),
    )
    logger.info(f"sandbox_config proposal {proposal_id}: {summary}")
    return {
        "proposal_id": proposal.proposal_id,
        "status": proposal.status,
        "org_scope": root.name,
    }
