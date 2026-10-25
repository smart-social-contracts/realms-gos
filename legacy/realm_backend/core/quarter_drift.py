"""Capital federation codex drift reporting (issue #295, Gap 4).

Pure helpers for comparing a quarter's gossip-reported codex against the
capital's live target, and for classifying sync-ballot outcomes. Wired into
the existing ``get_quarter_directory`` gossip exchange — no new transport.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, Mapping, Optional

# Terminal outcomes where a sync was requested but not applied. Silence on an
# org-scoped ballot settles as ``failed`` (threshold checked before quorum in
# ``core.org_policy.policy_satisfied``), not ``no_quorum`` — all three must be
# treated alike for drift visibility.
NOT_ADOPTED_BALLOT_STATUSES = frozenset({"failed", "no_quorum", "rejected"})
OPEN_BALLOT_STATUSES = frozenset({"voting", "pending_vote"})


def parse_proposal_metadata(raw: Any) -> dict:
    try:
        meta = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return meta if isinstance(meta, dict) else {}


def is_codex_sync_proposal(meta: Mapping[str, Any]) -> bool:
    return (meta or {}).get("sync_type") == "codex_sync"


# How far back the gossip block looks for a sync ballot. Loading every Proposal
# is O(max_id) and blows the per-message instruction limit at realm scale, and
# this runs on every gossip tick — so only the most recent window is scanned. A
# sync ballot older than the window reports as plain drift, which is the same
# operator signal.
RECENT_BALLOT_SCAN_LIMIT = 100


def recent_proposals(proposal_cls: Any, limit: int = RECENT_BALLOT_SCAN_LIMIT) -> list:
    """The newest *limit* proposals, or ``[]`` if the entity cannot be scanned."""
    try:
        max_id = int(proposal_cls.max_id() or 0)
    except Exception:
        return []
    if max_id <= 0:
        return []
    from_id = max(1, max_id - limit + 1)
    try:
        return list(proposal_cls.load_some(from_id=from_id, count=limit) or [])
    except Exception:
        return []


def find_latest_codex_sync_ballot(proposals: Iterable[Any]) -> Dict[str, str]:
    """Return ``{id, status}`` for the newest codex-sync proposal, or blanks."""
    latest = None
    latest_key = ""
    for proposal in proposals or []:
        meta = parse_proposal_metadata(getattr(proposal, "metadata", ""))
        if not is_codex_sync_proposal(meta):
            continue
        key = (
            getattr(proposal, "updated_at", "")
            or getattr(proposal, "created_at", "")
            or getattr(proposal, "proposal_id", "")
            or ""
        )
        if latest is None or key >= latest_key:
            latest = proposal
            latest_key = key
    if latest is None:
        return {"id": "", "status": ""}
    return {
        "id": (getattr(latest, "proposal_id", "") or "").strip(),
        "status": (getattr(latest, "status", "") or "").strip(),
    }


def normalize_version(version: Any) -> str:
    if version is None:
        return ""
    return str(version).strip()


def codex_versions_match(reported: Mapping[str, Any], target: Mapping[str, Any]) -> bool:
    """True when the quarter reports the same codex id and version as *target*."""
    reported_id = (reported.get("codex_id") or "").strip()
    target_id = (target.get("codex_id") or "").strip()
    if not reported_id or not target_id:
        return False
    if reported_id != target_id:
        return False
    return normalize_version(reported.get("version")) == normalize_version(
        target.get("version")
    )


def build_directory_self(
    canister_id: str,
    codex: Optional[Mapping[str, Any]],
    ballot: Optional[Mapping[str, Any]] = None,
    federal: Optional[Mapping[str, Any]] = None,
    last_activity_at: int = 0,
) -> dict:
    """Build the gossip ``self`` block describing this realm's codex + ballot."""
    block = {"canister_id": (canister_id or "").strip()}
    if last_activity_at:
        block["last_activity_at"] = int(last_activity_at)
    if codex:
        block["codex_id"] = (codex.get("codex_id") or "").strip()
        block["codex_version"] = normalize_version(codex.get("version"))
    ballot = ballot or {}
    block["last_sync_ballot_id"] = (ballot.get("id") or "").strip()
    block["last_sync_ballot_status"] = (ballot.get("status") or "").strip()
    federal = federal or {}
    if federal.get("vote_id"):
        block["last_federal_vote_id"] = (federal.get("vote_id") or "").strip()
        block["last_federal_leg_status"] = (federal.get("status") or "").strip()
        block["last_federal_leg_outcome"] = (federal.get("outcome") or "").strip()
    return block


def merge_self_report(quarter_entry: dict, self_block: Optional[Mapping[str, Any]]) -> bool:
    """Merge a peer ``self`` block into a quarter directory entry dict."""
    if not quarter_entry or not self_block or not isinstance(self_block, dict):
        return False
    changed = False
    for src, dst in (
        ("codex_id", "reported_codex_id"),
        ("codex_version", "reported_codex_version"),
        ("last_sync_ballot_id", "last_sync_ballot_id"),
        ("last_sync_ballot_status", "last_sync_ballot_status"),
    ):
        if src not in self_block:
            continue
        val = self_block.get(src)
        sval = "" if val is None else str(val)
        if quarter_entry.get(dst) != sval:
            quarter_entry[dst] = sval
            changed = True
    return changed


def apply_self_report_to_quarter(quarter_entity: Any, self_block: Optional[Mapping[str, Any]]) -> bool:
    """Persist gossip ``self`` fields on a capital-side ``Quarter`` entity."""
    if quarter_entity is None or not self_block or not isinstance(self_block, dict):
        return False
    changed = False
    for entity_field, self_key in (
        ("reported_codex_id", "codex_id"),
        ("reported_codex_version", "codex_version"),
        ("last_sync_ballot_id", "last_sync_ballot_id"),
        ("last_sync_ballot_status", "last_sync_ballot_status"),
    ):
        if self_key not in self_block:
            continue
        val = self_block.get(self_key)
        sval = "" if val is None else str(val)
        if (getattr(quarter_entity, entity_field, "") or "") != sval:
            setattr(quarter_entity, entity_field, sval)
            changed = True
    try:
        reported = int(self_block.get("last_activity_at") or 0)
    except (TypeError, ValueError):
        reported = 0
    if reported > int(getattr(quarter_entity, "last_activity_at", 0) or 0):
        quarter_entity.last_activity_at = reported
        changed = True
    return changed


def classify_quarter_drift_state(
    *, aligned: bool, has_report: bool, ballot_status: str
) -> str:
    """Per-quarter drift state for the capital federation view."""
    status = (ballot_status or "").strip().lower()
    if aligned:
        return "aligned"
    if status in OPEN_BALLOT_STATUSES:
        return "ballot_open"
    if status in NOT_ADOPTED_BALLOT_STATUSES:
        return "ballot_not_adopted"
    if not has_report:
        # Absence of gossip is not evidence of drift — only a confirmed report can show misalignment.
        return "unknown"
    return "drifted"


def build_quarter_drift_entry(
    *,
    canister_id: str,
    name: str,
    reported_codex_id: str,
    reported_codex_version: str,
    capital_codex_id: str,
    capital_codex_version: str,
    last_sync_ballot_id: str,
    last_sync_ballot_status: str,
) -> dict:
    reported = {
        "codex_id": (reported_codex_id or "").strip(),
        "version": (reported_codex_version or "").strip() or None,
    }
    capital = {
        "codex_id": (capital_codex_id or "").strip(),
        "version": (capital_codex_version or "").strip() or None,
    }
    has_report = bool(reported["codex_id"])
    aligned = has_report and codex_versions_match(reported, capital)
    return {
        "canister_id": canister_id or "",
        "name": name or "",
        "reported_codex_id": reported["codex_id"],
        "reported_codex_version": normalize_version(reported.get("version")),
        "capital_codex_id": capital["codex_id"],
        "capital_codex_version": normalize_version(capital.get("version")),
        "drifted": has_report and not aligned,
        "last_sync_ballot_id": (last_sync_ballot_id or "").strip(),
        "last_sync_ballot_status": (last_sync_ballot_status or "").strip(),
        "state": classify_quarter_drift_state(
            aligned=aligned,
            has_report=has_report,
            ballot_status=last_sync_ballot_status,
        ),
    }


def build_federation_drift_report(quarters: Iterable[Any], capital_codex: Mapping[str, Any]) -> dict:
    """Assemble the capital-side drift payload from ``Quarter`` entities."""
    entries = []
    for quarter in quarters or []:
        entries.append(
            build_quarter_drift_entry(
                canister_id=getattr(quarter, "canister_id", "") or "",
                name=getattr(quarter, "name", "") or "",
                reported_codex_id=getattr(quarter, "reported_codex_id", "") or "",
                reported_codex_version=getattr(quarter, "reported_codex_version", "") or "",
                capital_codex_id=capital_codex.get("codex_id") or "",
                capital_codex_version=capital_codex.get("version") or "",
                last_sync_ballot_id=getattr(quarter, "last_sync_ballot_id", "") or "",
                last_sync_ballot_status=getattr(quarter, "last_sync_ballot_status", "") or "",
            )
        )
    return {
        "capital_codex_id": (capital_codex.get("codex_id") or "").strip(),
        "capital_codex_version": normalize_version(capital_codex.get("version")),
        "quarters": entries,
    }


def derive_capital_target_codex(derived_install_set: Mapping[str, Any]) -> dict:
    """Pick the primary codex target from ``derive_capital_install_set`` output."""
    codices = derived_install_set.get("codices") or []
    if not codices:
        return {"codex_id": "", "version": None}
    primary = codices[0] if isinstance(codices[0], dict) else {}
    return {
        "codex_id": (primary.get("codex_id") or "").strip(),
        "version": primary.get("version"),
    }
