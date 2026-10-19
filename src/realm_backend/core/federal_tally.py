"""Federal vote aggregation and verification (``core.federal_tally``, issue #300).

Pure helpers for freezing a realm-wide question spec, hashing it for
tamper detection, classifying quarter-side leg outcomes, and aggregating
legs under codex policy. Wired into federation transport and entity
drivers elsewhere — no canister dependencies here.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

CANONICAL_SEPARATORS = (",", ":")

ACTION_MAX_LENGTH = 2048

DEFAULT_RULE = {
    "aggregation": "per_quarter",
    "threshold": 0.6,
    "quarter_quorum_percent": 60,
    "member_quorum_percent": 0,
    "voting_window_days": 7,
    "grace_hours": 24,
}

AGGREGATION_MODES = ("per_quarter", "per_member")

LEG_ADOPTED = "adopted"
LEG_REJECTED = "rejected"
LEG_NO_QUORUM = "no_quorum"
LEG_ABSENT = "absent"

_SECONDS_PER_DAY = 86400
_SECONDS_PER_HOUR = 3600

_BACKEND_KEYS = frozenset({"module", "function", "args"})
_EXTENSION_KEYS = frozenset({"extension", "function", "args"})


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=CANONICAL_SEPARATORS)


def compute_vote_hash(action: dict, rule: dict, deadline: int) -> str:
    payload = {
        "action": action,
        "rule": rule,
        "deadline": int(deadline),
    }
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def validate_action(action: Any) -> Tuple[Optional[dict], str]:
    """Validate and normalize an action descriptor for hashing.

    Returns ``(normalized_action, "")`` on success or ``(None, error_message)``
    on failure.
    """
    if not isinstance(action, dict):
        return None, "action must be a dict"

    has_module = "module" in action
    has_extension = "extension" in action
    if has_module and has_extension:
        return None, "action must have exactly one of module or extension"
    if not has_module and not has_extension:
        return None, "action must have exactly one of module or extension"

    allowed_keys = _EXTENSION_KEYS if has_extension else _BACKEND_KEYS
    extra = set(action.keys()) - allowed_keys
    if extra:
        return None, f"unknown action keys: {sorted(extra)}"

    function = action.get("function")
    if not isinstance(function, str) or not function.strip():
        return None, "function is required and must be a non-empty string"

    raw_args = action.get("args")
    if raw_args is None:
        args: dict = {}
    elif not isinstance(raw_args, dict):
        return None, "args must be a dict"
    else:
        args = raw_args

    if has_extension:
        ext_id = action.get("extension")
        if not isinstance(ext_id, str) or not ext_id.strip():
            return None, "extension is required and must be a non-empty string"
        normalized = {
            "extension": ext_id.strip(),
            "function": function.strip(),
            "args": args,
        }
    else:
        module = action.get("module")
        if not isinstance(module, str) or not module.strip():
            return None, "module is required and must be a non-empty string"
        normalized = {
            "module": module.strip(),
            "function": function.strip(),
            "args": args,
        }

    serialized = canonical_json(normalized)
    size = len(serialized.encode("utf-8"))
    if size > ACTION_MAX_LENGTH:
        return None, (
            f"action descriptor exceeds maximum size "
            f"({size} bytes, cap {ACTION_MAX_LENGTH})"
        )

    return normalized, ""


def _as_number(value: Any) -> Optional[float]:
    """Numeric value of a codex-supplied param, or None when it is not a number.

    JSON does not distinguish int from float, so a codex may legitimately send
    ``1`` for a threshold or ``60.0`` for a percent. ``bool`` is excluded
    because it is an ``int`` subclass in Python and ``True`` is never a
    meaningful threshold or percent.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def resolve_rule(codex_params: Optional[dict]) -> dict:
    """Merge codex-supplied params over ``DEFAULT_RULE``, ignoring invalid values.

    Never raises: a value of the wrong type or out of range falls back to the
    default for that key alone. The result carries exactly ``DEFAULT_RULE``'s
    keys so the frozen rule hashes stably across quarters.
    """
    resolved = dict(DEFAULT_RULE)
    if not codex_params or not isinstance(codex_params, dict):
        return resolved

    aggregation = codex_params.get("aggregation")
    if isinstance(aggregation, str) and aggregation in AGGREGATION_MODES:
        resolved["aggregation"] = aggregation

    threshold = _as_number(codex_params.get("threshold"))
    if threshold is not None and 0.0 < threshold <= 1.0:
        resolved["threshold"] = threshold

    for key in ("quarter_quorum_percent", "member_quorum_percent"):
        percent = _as_number(codex_params.get(key))
        if percent is not None and 0 <= percent <= 100:
            resolved[key] = int(percent)

    voting_window = _as_number(codex_params.get("voting_window_days"))
    if voting_window is not None and voting_window >= 1:
        resolved["voting_window_days"] = int(voting_window)

    grace = _as_number(codex_params.get("grace_hours"))
    if grace is not None and grace >= 0:
        resolved["grace_hours"] = int(grace)

    return resolved


def classify_leg_outcome(proposal_status: str) -> str:
    """Map a voting-extension terminal proposal status to a leg outcome.

    ``accepted`` / ``executed`` → ``adopted``; ``rejected`` / ``failed`` →
    ``rejected``; ``no_quorum`` → ``no_quorum``. Everything else, including the
    open statuses ``voting`` / ``pending_vote`` / ``pending_review``, returns
    ``""`` meaning the leg has not settled yet.
    """
    status = (proposal_status or "").strip().lower()
    if status in ("accepted", "executed"):
        return LEG_ADOPTED
    if status in ("rejected", "failed"):
        return LEG_REJECTED
    if status == "no_quorum":
        return LEG_NO_QUORUM
    return ""


def aggregate(legs: List[dict], known_quarters: int, rule: dict) -> dict:
    reported_count = sum(1 for leg in legs if leg.get("reported"))
    absent = max(0, int(known_quarters) - reported_count)

    if known_quarters <= 0:
        quarter_participation_percent = 0.0
        quarter_quorum_met = False
    else:
        quarter_participation_percent = reported_count / known_quarters * 100.0
        quarter_quorum_met = (
            quarter_participation_percent >= rule.get("quarter_quorum_percent", 0)
        )

    aggregation = rule.get("aggregation", "per_quarter")
    yes_weight = 0.0
    no_weight = 0.0
    member_yes = 0
    member_no = 0
    member_abstain = 0
    eligible_total = 0

    settled_outcomes = {LEG_ADOPTED, LEG_REJECTED}

    for leg in legs:
        if not leg.get("reported"):
            continue
        outcome = (leg.get("outcome") or "").strip()
        if aggregation == "per_quarter":
            if outcome == LEG_ADOPTED:
                yes_weight += 1.0
            elif outcome == LEG_REJECTED:
                no_weight += 1.0
        elif outcome in settled_outcomes:
            member_yes += int(leg.get("yes") or 0)
            member_no += int(leg.get("no") or 0)
            member_abstain += int(leg.get("abstain") or 0)
            eligible_total += int(leg.get("eligible") or 0)

    if aggregation == "per_member":
        yes_weight = float(member_yes)
        no_weight = float(member_no)

    decisive_total = yes_weight + no_weight
    threshold_met = (
        decisive_total > 0
        and yes_weight / decisive_total >= rule.get("threshold", 0.6)
    )

    member_quorum_percent = rule.get("member_quorum_percent", 0)
    if member_quorum_percent <= 0:
        member_quorum_met = True
    elif eligible_total <= 0:
        member_quorum_met = False
    else:
        participation = (
            (member_yes + member_no + member_abstain) / eligible_total * 100.0
        )
        member_quorum_met = participation >= member_quorum_percent

    quorums_met = quarter_quorum_met and member_quorum_met

    if not quorums_met:
        status = "no_quorum"
        if not quarter_quorum_met:
            reason = "quarter participation below quorum"
        else:
            reason = "member participation below quorum"
    elif threshold_met:
        status = "adopted"
        reason = "threshold met"
    else:
        status = "rejected"
        if decisive_total <= 0:
            reason = "no decisive votes"
        else:
            reason = "threshold not met"

    return {
        "status": status,
        "yes_weight": yes_weight,
        "no_weight": no_weight,
        "reported": reported_count,
        "absent": absent,
        "known_quarters": int(known_quarters),
        "quarter_participation_percent": quarter_participation_percent,
        "threshold_met": threshold_met,
        "quarter_quorum_met": quarter_quorum_met,
        "member_quorum_met": member_quorum_met,
        "aggregation": aggregation,
        "reason": reason,
    }


def verify_result(stored_hash: str, message_hash: str) -> Tuple[bool, str]:
    """Constant-shape comparison of frozen vote spec hashes.

    A federal vote binds the action, rule and deadline at open time via
    ``vote_hash``. Quarters execute only when the capital's result message
    carries the same hash stored on the local leg. Without this guard the
    capital could swap the action, threshold or deadline after members voted
    and manufacture adoption — turning federation into capital rule, which
    ``QUARTERS.md`` rejects. Both hashes must be non-empty and equal.
    """
    stored = (stored_hash or "").strip()
    message = (message_hash or "").strip()
    if not stored:
        return False, "no leg hash stored — leg was never opened"
    if not message:
        return False, "result message missing vote_hash"
    if stored != message:
        return False, "vote_hash mismatch"
    return True, ""


def compute_deadline(now_epoch_s: int, rule: dict) -> int:
    window_days = rule.get("voting_window_days", DEFAULT_RULE["voting_window_days"])
    return int(now_epoch_s) + int(window_days) * _SECONDS_PER_DAY


def is_past(now_epoch_s: int, deadline: int, grace_hours: int = 0) -> bool:
    grace_seconds = int(grace_hours) * _SECONDS_PER_HOUR
    return int(now_epoch_s) >= int(deadline) + grace_seconds


def build_vote_id(origin_quarter: str, now_epoch_s: int, counter: int) -> str:
    # No ``re`` here: the canister's ``re`` has no ``compile``.
    quarter = "".join(
        c if (c.isascii() and c.isalnum()) or c in "_-" else "_"
        for c in (origin_quarter or "").strip()
    )[:32]
    raw = f"fv_{quarter}_{int(now_epoch_s)}_{int(counter)}"
    return raw[:64]
