"""Tax policy: parameters, their lifecycle, and the hand-off to the codex.

Core stores the parameters and decides which version is in force; the codex
does the calculation and the invoicing. The split matters because taxation is
codex policy (QUARTERS.md): a realm without a tax policy has no taxes, and two
codices may assess the same parameters differently.

The parameters are one JSON document per :class:`ggg.TaxPolicy` row::

    {"schema_version": 1,
     "taxes": [{"code": "property", "label": "Property tax", "enabled": true,
                "base": "land_area", "unit_value": 100,
                "method": "progressive",
                "brackets": [{"up_to": 50000, "rate_bp": 40},
                             {"up_to": null, "rate_bp": 80}],
                "exempt_below": 0, "applies_to": ["member"],
                "exempt_profiles": [], "period": "annual", "due_days": 30}]}

``base`` is what a tax is levied on: ``flat`` (once per member) or
``land_area`` (once per parcel, valued at area × ``unit_value``). Rates are in
basis points. :func:`ggg_sdk.tax_breakdown` is the reference calculation.

Adopting a draft is governed by the root org's policy. The draft is frozen as
``proposed`` while a vote runs, and the proposal adopts that row by id: the
document itself does not fit in a proposal's metadata, and freezing it means
what passes is what was voted on.
"""

import json
from typing import Any, Dict, List, Optional

from ic_python_logging import get_logger

logger = get_logger("core.taxation")

SCHEMA_VERSION = 1
BASES = ("flat", "land_area")
METHODS = ("fixed", "proportional", "progressive")
PERIODS = ("monthly", "quarterly", "annual")
MAX_TAXES = 20
MAX_BRACKETS = 12
MAX_PROFILES = 10
MAX_BP = 10_000
MAX_DOCUMENT_LENGTH = 16_384

ASSESS_HOOK = "assess_taxes"
ASSESS_BATCH = 25

_TAX_KEYS = frozenset({
    "code", "label", "enabled", "base", "unit_value", "method", "amount",
    "rate_bp", "brackets", "exempt_below", "applies_to", "exempt_profiles",
    "period", "due_days",
})
_CODE_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _number(value, where: str, minimum: float = 0.0, strict: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{where} must be a number")
    if value < minimum or (strict and value == minimum):
        relation = "greater than" if strict else "at least"
        raise ValueError(f"{where} must be {relation} {minimum:g}")
    return float(value)


def _rate(value, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{where} must be a whole number of basis points")
    if not 0 <= value <= MAX_BP:
        raise ValueError(f"{where} must be between 0 and {MAX_BP} basis points")
    return value


def _profiles(value, where: str) -> List[str]:
    if not isinstance(value, list) or len(value) > MAX_PROFILES:
        raise ValueError(f"{where} must be a list of at most {MAX_PROFILES} profile names")
    out = []
    for name in value:
        if not isinstance(name, str) or not name.strip() or len(name) > 64:
            raise ValueError(f"{where} contains an invalid profile name")
        out.append(name.strip())
    return out


def _brackets(value, where: str) -> List[Dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{where} needs at least one bracket")
    if len(value) > MAX_BRACKETS:
        raise ValueError(f"{where} allows at most {MAX_BRACKETS} brackets")
    out = []
    previous = 0.0
    for i, bracket in enumerate(value):
        label = f"{where} bracket {i + 1}"
        if not isinstance(bracket, dict) or set(bracket) - {"up_to", "rate_bp"}:
            raise ValueError(f"{label} must be {{'up_to', 'rate_bp'}}")
        up_to = bracket.get("up_to")
        last = i == len(value) - 1
        if up_to is None:
            if not last:
                raise ValueError(f"{label}: only the last bracket may be open-ended")
        else:
            up_to = _number(up_to, f"{label} 'up_to'", minimum=previous, strict=True)
            previous = up_to
        if last and up_to is not None:
            raise ValueError(f"{where}: the last bracket must be open-ended (up_to null)")
        out.append({"up_to": up_to, "rate_bp": _rate(bracket.get("rate_bp"), f"{label} rate")})
    return out


def _normalize_tax(raw: Any, index: int, seen: set) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"tax {index + 1} must be an object")
    unknown = sorted(set(raw) - _TAX_KEYS)
    if unknown:
        raise ValueError(f"tax {index + 1} has unknown field(s): {', '.join(unknown)}")

    code = raw.get("code")
    if (
        not isinstance(code, str)
        or not 1 <= len(code) <= 32
        or not code[0].isalpha()
        or set(code) - _CODE_CHARS
    ):
        raise ValueError(
            f"tax {index + 1}: code must be 1-32 lowercase letters, digits or "
            f"underscores, starting with a letter"
        )
    if code in seen:
        raise ValueError(f"tax code '{code}' is used twice")
    seen.add(code)
    where = f"tax '{code}'"

    label = raw.get("label") or code
    if not isinstance(label, str) or len(label) > 80:
        raise ValueError(f"{where}: label must be text of at most 80 characters")

    base = raw.get("base") or "flat"
    if base not in BASES:
        raise ValueError(f"{where}: base must be one of {', '.join(BASES)}")
    method = raw.get("method") or "fixed"
    if method not in METHODS:
        raise ValueError(f"{where}: method must be one of {', '.join(METHODS)}")
    if base == "flat" and method != "fixed":
        raise ValueError(f"{where}: a flat tax is a fixed amount per member")
    period = raw.get("period") or "annual"
    if period not in PERIODS:
        raise ValueError(f"{where}: period must be one of {', '.join(PERIODS)}")

    due_days = raw.get("due_days", 30)
    if isinstance(due_days, bool) or not isinstance(due_days, int) or not 1 <= due_days <= 365:
        raise ValueError(f"{where}: due_days must be a whole number from 1 to 365")

    tax: Dict[str, Any] = {
        "code": code,
        "label": label.strip() or code,
        "enabled": bool(raw.get("enabled", True)),
        "base": base,
        "method": method,
        "period": period,
        "due_days": due_days,
        "exempt_below": _number(raw.get("exempt_below", 0) or 0, f"{where} exempt_below"),
        "applies_to": _profiles(raw.get("applies_to", ["member"]), f"{where} applies_to"),
        "exempt_profiles": _profiles(raw.get("exempt_profiles", []), f"{where} exempt_profiles"),
    }
    if not tax["applies_to"]:
        raise ValueError(f"{where}: applies_to needs at least one profile")
    if base == "land_area":
        tax["unit_value"] = _number(
            raw.get("unit_value", 1), f"{where} unit_value", strict=True
        )
    if method == "fixed":
        tax["amount"] = _number(raw.get("amount"), f"{where} amount", strict=True)
    elif method == "proportional":
        tax["rate_bp"] = _rate(raw.get("rate_bp"), f"{where} rate")
    else:
        tax["brackets"] = _brackets(raw.get("brackets"), where)
    return tax


def normalize_document(document: Any) -> Dict[str, Any]:
    """Validate a tax document and return its canonical form.

    Unknown fields are refused rather than dropped, so a typo in a parameter
    is an error instead of a tax that silently ignores it. Fields that do not
    apply to a tax's method (a ``rate_bp`` on a fixed tax) are left out.
    """
    if isinstance(document, str):
        try:
            document = json.loads(document)
        except ValueError:
            raise ValueError("tax document is not valid JSON")
    if not isinstance(document, dict):
        raise ValueError("tax document must be an object")
    taxes = document.get("taxes")
    if not isinstance(taxes, list):
        raise ValueError("tax document needs a 'taxes' list")
    if len(taxes) > MAX_TAXES:
        raise ValueError(f"a tax policy holds at most {MAX_TAXES} taxes")
    seen: set = set()
    out = {
        "schema_version": SCHEMA_VERSION,
        "taxes": [_normalize_tax(t, i, seen) for i, t in enumerate(taxes)],
    }
    if len(json.dumps(out)) > MAX_DOCUMENT_LENGTH:
        raise ValueError("tax document is too large")
    return out


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    try:
        from _cdk import ic
        from ic_basilisk_toolkit.date_utils import epoch_to_datetime_str, ic_time_to_epoch

        return epoch_to_datetime_str(ic_time_to_epoch(int(ic.time()))).replace(" ", "T")
    except Exception:
        return ""


def summarize(policy) -> Optional[Dict[str, Any]]:
    if policy is None:
        return None
    return {
        "id": policy.id,
        "status": policy.status,
        "description": policy.description or "",
        "taxes": policy.taxes(),
        "proposal_id": policy.proposal_id or "",
        "created_by": policy.created_by or "",
        "adopted_by": policy.adopted_by or "",
        "adopted_at": policy.adopted_at or "",
    }


def describe(policy) -> str:
    """One-line summary for proposal titles."""
    taxes = [t for t in policy.taxes() if t.get("enabled", True)]
    if not taxes:
        return f"Adopt tax policy {policy.id} (no taxes)"
    names = ", ".join(t.get("label") or t.get("code", "?") for t in taxes[:4])
    more = f" and {len(taxes) - 4} more" if len(taxes) > 4 else ""
    return f"Adopt tax policy {policy.id}: {names}{more}"


def current_draft():
    from ggg import TaxPolicy, TaxPolicyStatus

    return TaxPolicy.first_with_status(TaxPolicyStatus.DRAFT)


def proposed_policies(limit: int = 10) -> list:
    from ggg import TaxPolicy, TaxPolicyStatus

    page, _ = TaxPolicy.find_by("status", TaxPolicyStatus.PROPOSED, from_id=1, count=limit)
    return page


def save_draft(document: Any, description: str = "", triggered_by: str = "") -> Dict[str, Any]:
    """Create or replace the one editable draft."""
    from ggg import TaxPolicy, TaxPolicyStatus

    doc = normalize_document(document)
    draft = current_draft()
    if draft is None:
        draft = TaxPolicy(
            id=f"TAX-POLICY-{TaxPolicy.max_id() + 1}",
            status=TaxPolicyStatus.DRAFT,
            created_by=triggered_by,
        )
    draft.document = json.dumps(doc, sort_keys=True)
    draft.schema_version = doc["schema_version"]
    draft.description = str(description or "")[:512]
    logger.info(f"tax draft {draft.id} saved by {triggered_by}")
    return summarize(draft)


def discard_draft() -> Dict[str, Any]:
    draft = current_draft()
    if draft is None:
        return {"discarded": None}
    draft_id = draft.id
    draft.delete()
    return {"discarded": draft_id}


def mark_proposed(policy_id: str, proposal_id: str) -> None:
    from ggg import TaxPolicy, TaxPolicyStatus

    policy = TaxPolicy[policy_id]
    if policy is not None and policy.status == TaxPolicyStatus.DRAFT:
        policy.status = TaxPolicyStatus.PROPOSED
        policy.proposal_id = proposal_id


def adopt_policy(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Put a draft or proposed policy in force. The direct path and the
    proposal replay both end here."""
    from ggg import TaxPolicy, TaxPolicyStatus

    policy_id = str((payload or {}).get("policy_id") or "")
    policy = TaxPolicy[policy_id] if policy_id else None
    if policy is None:
        return {"success": False, "error": f"tax policy '{policy_id}' not found"}
    if policy.status not in (TaxPolicyStatus.DRAFT, TaxPolicyStatus.PROPOSED):
        return {
            "success": False,
            "error": f"tax policy {policy_id} is {policy.status}, not adoptable",
        }

    previous = TaxPolicy.adopted()
    if previous is not None and previous.id != policy.id:
        previous.status = TaxPolicyStatus.SUPERSEDED
    policy.status = TaxPolicyStatus.ADOPTED
    policy.adopted_by = str(payload.get("triggered_by") or "proposal")[:64]
    policy.adopted_at = _now_iso()
    logger.info(f"tax policy {policy.id} adopted by {policy.adopted_by}")
    return {
        "success": True,
        "policy": summarize(policy),
        "superseded": previous.id if previous is not None and previous.id != policy.id else None,
    }


def history(cursor: int = 1, count: int = 20) -> Dict[str, Any]:
    """Policy versions, oldest first, one page at a time."""
    from ggg import TaxPolicy

    cursor = max(1, int(cursor or 1))
    count = max(1, min(int(count or 20), 50))
    rows = TaxPolicy.load_some(from_id=cursor, count=count) or []
    nxt = cursor + count
    return {
        "rows": [
            {
                "id": p.id,
                "status": p.status,
                "description": p.description or "",
                "taxes": len(p.taxes()),
                "proposal_id": p.proposal_id or "",
                "adopted_at": p.adopted_at or "",
            }
            for p in rows
        ],
        "next": nxt if nxt <= TaxPolicy.max_id() else None,
    }


# ---------------------------------------------------------------------------
# What a codex reads
# ---------------------------------------------------------------------------


def effective_policy() -> Dict[str, Any]:
    """The parameters in force: the adopted policy, else the codex's own
    ``taxes`` config block, else nothing."""
    from ggg import TaxPolicy

    policy = TaxPolicy.adopted()
    if policy is not None:
        return {"source": "policy", "policy_id": policy.id, "taxes": policy.taxes()}

    try:
        from core.codex_hooks import get_config

        block = (get_config() or {}).get("taxes")
    except Exception as e:
        logger.warning(f"could not read codex tax defaults: {e}")
        block = None
    if block:
        document = block if isinstance(block, dict) else {"taxes": block}
        try:
            return {
                "source": "codex",
                "policy_id": "",
                "taxes": normalize_document(document)["taxes"],
            }
        except ValueError as e:
            logger.warning(f"codex tax defaults ignored: {e}")
    return {"source": "none", "policy_id": "", "taxes": []}


def codex_assesses() -> bool:
    from core.codex_hooks import get_hook

    return get_hook(ASSESS_HOOK) is not None


def run_assessment(cursor: str = "", triggered_by: str = "") -> Dict[str, Any]:
    """Ask the active codex to assess one batch of taxpayers.

    The codex pages through its taxpayers with an opaque ``cursor`` and returns
    ``next_cursor`` (empty when done), so no single message walks the whole
    realm.
    """
    from core.codex_hooks import call_hook

    if not codex_assesses():
        raise ValueError("The active codex does not assess taxes")
    policy = effective_policy()
    if not any(t.get("enabled", True) for t in policy["taxes"]):
        raise ValueError("No tax is in force; adopt a tax policy first")

    result = call_hook(ASSESS_HOOK, {
        "cursor": str(cursor or ""),
        "count": ASSESS_BATCH,
        "policy": policy,
        "triggered_by": triggered_by,
    })
    if not isinstance(result, dict):
        raise RuntimeError("The codex returned no assessment result")
    if result.get("success") is False:
        raise ValueError(result.get("error") or "tax assessment failed")
    return result
