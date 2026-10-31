"""Scoring rubric validation and weighted totals.

Host-side because ``submit_scores`` and ``compute_totals`` write score rows and
bid totals. The rubric parsing above them is pure arithmetic, kept here so the
validation that gates an RFP and the arithmetic that scores it cannot drift apart.
"""

import json
from typing import Any, Dict, List, Optional, Tuple

WEIGHT_TOLERANCE = 0.001
SCORE_PRECISION = 4


def parse_rubric(rubric_json: str) -> Tuple[Optional[List[dict]], Optional[str]]:
    if not rubric_json or not str(rubric_json).strip():
        return None, "Rubric is required"
    try:
        rubric = json.loads(rubric_json)
    except (json.JSONDecodeError, TypeError):
        return None, "Invalid rubric JSON"
    if not isinstance(rubric, list) or not rubric:
        return None, "Rubric must be a non-empty list"

    seen_ids = set()
    total_weight = 0.0
    for item in rubric:
        if not isinstance(item, dict):
            return None, "Each rubric entry must be an object"
        cid = str(item.get("id", "")).strip()
        if not cid:
            return None, "Each criterion must have an id"
        if cid in seen_ids:
            return None, f"Duplicate criterion id '{cid}'"
        seen_ids.add(cid)
        try:
            weight = float(item.get("weight", 0))
            max_score = float(item.get("max_score", 0))
        except (TypeError, ValueError):
            return None, f"Invalid numeric fields for criterion '{cid}'"
        if weight <= 0:
            return None, f"Weight must be > 0 for criterion '{cid}'"
        if max_score <= 0:
            return None, f"max_score must be > 0 for criterion '{cid}'"
        total_weight += weight

    if abs(total_weight - 1.0) > WEIGHT_TOLERANCE:
        return None, f"Rubric weights must sum to 1.0 (got {total_weight})"

    return rubric, None


def validate_rubric(rubric_json: str) -> dict:
    _, err = parse_rubric(rubric_json)
    if err:
        return {"valid": False, "error": err}
    return {"valid": True}


def rubric_by_id(rubric_json: str) -> Dict[str, dict]:
    rubric, err = parse_rubric(rubric_json)
    if err or not rubric:
        return {}
    return {str(item["id"]): item for item in rubric}


def submit_scores(
    bid_id: str,
    evaluator_id: str,
    scores: Dict[str, Any],
) -> dict:
    from core.procurement import entities, roles

    bid = entities.find_bid(bid_id)
    if not bid:
        return {"success": False, "error": f"Bid '{bid_id}' not found"}

    rfp = entities.find_rfp(str(bid.rfp_id))
    if not rfp:
        return {"success": False, "error": "Parent RFP not found"}

    if (rfp.status or "") != "evaluation":
        return {"success": False, "error": "RFP is not in evaluation phase"}

    criteria = rubric_by_id(str(rfp.rubric_json or ""))
    if not criteria:
        return {"success": False, "error": "RFP has no valid rubric"}

    if not isinstance(scores, dict) or not scores:
        return {"success": False, "error": "scores object is required"}

    ts = roles.now_epoch()
    written = 0
    for criterion_id, raw_score in scores.items():
        cid = str(criterion_id)
        if cid not in criteria:
            return {"success": False, "error": f"Unknown criterion '{cid}'"}
        try:
            score_val = float(raw_score)
        except (TypeError, ValueError):
            return {"success": False, "error": f"Invalid score for '{cid}'"}
        max_score = float(criteria[cid]["max_score"])
        if score_val < 0 or score_val > max_score:
            return {
                "success": False,
                "error": f"Score for '{cid}' must be between 0 and {max_score}",
            }

        score_id = f"{bid_id}:{evaluator_id}:{cid}"
        existing = entities.score_class()[score_id]
        if existing:
            existing.score = score_val
            existing.scored_at = ts
        else:
            entities.score_class()(
                score_id=score_id,
                bid_id=bid_id,
                rfp_id=str(bid.rfp_id),
                evaluator_id=evaluator_id,
                criterion_id=cid,
                score=score_val,
                scored_at=ts,
            )
        written += 1

    return {"success": True, "bid_id": bid_id, "scores_written": written}


def compute_totals(rfp_id: str) -> dict:
    from core.procurement import entities

    rfp = entities.find_rfp(rfp_id)
    if not rfp:
        return {"success": False, "error": f"RFP '{rfp_id}' not found"}

    criteria = rubric_by_id(str(rfp.rubric_json or ""))
    if not criteria:
        return {"success": False, "error": "RFP has no valid rubric"}

    bids = entities.bids_for_rfp(rfp_id)
    all_scores = entities.scores_for_rfp(rfp_id)

    results = []
    for bid in bids:
        bid_id = str(bid.bid_id)
        bid_scores = [s for s in all_scores if str(s.bid_id) == bid_id]
        if not bid_scores:
            continue

        by_evaluator: Dict[str, List] = {}
        for row in bid_scores:
            by_evaluator.setdefault(str(row.evaluator_id), []).append(row)

        evaluator_totals = []
        breakdown = {}
        for evaluator_id, rows in by_evaluator.items():
            weighted = 0.0
            crit_detail = {}
            for row in rows:
                cid = str(row.criterion_id)
                crit = criteria.get(cid)
                if not crit:
                    continue
                normalized = float(row.score) / float(crit["max_score"])
                contrib = normalized * float(crit["weight"])
                weighted += contrib
                crit_detail[cid] = round(float(row.score), SCORE_PRECISION)
            evaluator_totals.append(weighted)
            breakdown[evaluator_id] = crit_detail

        if not evaluator_totals:
            continue

        total = round(sum(evaluator_totals) / len(evaluator_totals), SCORE_PRECISION)
        bid.total_score = total
        bid.score_breakdown_json = json.dumps(
            {"evaluators": breakdown, "total": total},
            separators=(",", ":"),
        )
        results.append({"bid_id": bid_id, "total_score": total})

    return {"success": True, "rfp_id": rfp_id, "bids": results}
