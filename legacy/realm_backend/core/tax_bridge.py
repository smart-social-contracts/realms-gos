"""``tax.*`` verbs: the tax policy an extension edits and a codex applies.

Reads are open to any registered member: what a realm taxes is public. Writes
need the same rights as treasury actions (admin, org-appoint, the root org's
head, or a root member), and adopting additionally goes through
:func:`core.governed_action.gate`, so under any policy other than 1/1 it opens
a vote instead of applying.

The verbs take parameters, never an identity: who saved a draft or adopted a
policy is the host-supplied ``caller``.
"""

import json
from typing import Any, Dict

from ic_python_logging import get_logger

logger = get_logger("core.tax_bridge")


def _caller_user(caller: str):
    from ggg import User

    user = User[caller] if caller else None
    if not user:
        raise PermissionError(f"User {caller} not found")
    return user


def _governing():
    from core.governed_action import governing_org

    return governing_org(None)


def _can_manage(user) -> bool:
    from core.treasury_bridge import can_manage

    return can_manage(user, _governing())


def _require_manager(caller: str):
    user = _caller_user(caller)
    if not _can_manage(user):
        raise PermissionError("changing the tax policy requires admin/head rights")
    return user


def v_overview(caller: str = "", **kwargs) -> Dict[str, Any]:
    from core import taxation
    from core.governed_action import format_org_policy, policy_is_direct

    user = _caller_user(caller)
    governing = _governing()
    try:
        from core.realm_currency import realm_currency

        currency = realm_currency()
    except Exception:
        currency = ""
    try:
        from core.codex_hooks import get_active_codex

        codex = get_active_codex() or ""
    except Exception:
        codex = ""
    from ggg import TaxPolicy

    effective = taxation.effective_policy()
    return {
        "adopted": taxation.summarize(TaxPolicy.adopted()),
        "draft": taxation.summarize(taxation.current_draft()),
        "proposed": [taxation.summarize(p) for p in taxation.proposed_policies()],
        "effective_source": effective["source"],
        "effective_taxes": effective["taxes"] if effective["source"] == "codex" else [],
        "currency": currency,
        "codex": codex,
        "codex_assesses": taxation.codex_assesses(),
        "can_manage": _can_manage(user),
        "governed_by": governing.name if governing else "",
        "governed_policy": format_org_policy(governing) if governing else "",
        "direct": governing is None or policy_is_direct(governing),
        "options": {
            "bases": list(taxation.BASES),
            "methods": list(taxation.METHODS),
            "periods": list(taxation.PERIODS),
        },
    }


def v_history(caller: str = "", cursor: int = 1, count: int = 20, **kwargs) -> Dict[str, Any]:
    from core import taxation

    _caller_user(caller)
    return taxation.history(cursor=cursor, count=count)


def v_save_draft(
    caller: str = "", document: Any = None, description: str = "", **kwargs
) -> Dict[str, Any]:
    from core import taxation

    user = _require_manager(caller)
    return taxation.save_draft(document, description=description, triggered_by=user.id)


def v_discard_draft(caller: str = "", **kwargs) -> Dict[str, Any]:
    from core import taxation

    _require_manager(caller)
    return taxation.discard_draft()


def v_adopt(caller: str = "", confirm: bool = False, **kwargs) -> Dict[str, Any]:
    """Adopt the current draft, directly or through a vote."""
    from core import taxation
    from core.governed_action import build_backend_replay_code, gate

    user = _require_manager(caller)
    draft = taxation.current_draft()
    if draft is None:
        raise ValueError("there is no draft to adopt; save one first")
    taxation.normalize_document(draft.document)

    payload = {"policy_id": draft.id, "triggered_by": user.id}
    verdict = gate(
        caller=user.id,
        summary=taxation.describe(draft),
        replay_code=build_backend_replay_code(
            "core.taxation", "adopt_policy", json.dumps(payload)
        ),
        confirm=bool(confirm),
        metadata_extra={"tax_policy_id": draft.id},
    )
    if verdict is None:
        result = taxation.adopt_policy(payload)
        if not result.get("success"):
            raise ValueError(result.get("error") or "could not adopt the tax policy")
        return {**result, "applied": "direct"}
    if verdict.get("applied") == "proposal":
        taxation.mark_proposed(draft.id, verdict.get("proposal_id", ""))
    elif verdict.get("success") is False and not verdict.get("requires_confirmation"):
        raise ValueError(verdict.get("error") or "could not open a vote")
    return verdict


def v_assess(caller: str = "", cursor: str = "", **kwargs) -> Dict[str, Any]:
    """Run one assessment batch in the active codex."""
    from core import taxation

    user = _require_manager(caller)
    return taxation.run_assessment(cursor=cursor, triggered_by=user.id)


VERBS = {
    "tax.overview": v_overview,
    "tax.history": v_history,
    "tax.save_draft": v_save_draft,
    "tax.discard_draft": v_discard_draft,
    "tax.adopt": v_adopt,
    "tax.assess": v_assess,
}

READ_VERBS = frozenset({"tax.overview", "tax.history"})
