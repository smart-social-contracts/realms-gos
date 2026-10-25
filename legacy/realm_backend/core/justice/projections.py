"""Plain-data projections of the GGG justice entities.

Only the fields listed here cross the bridge, so adding a field to ``Case`` does
not silently widen what a sandboxed extension sees.

One rule worth stating: for a *private* litigation the public ``Case.title`` and
``Case.description`` are empty by construction — the real content is an opaque
blob in ``LitigationContent``. :func:`litigation_row` blanks them anyway rather
than trusting that, because a case that acquired a plaintext title by some other
route must not leak it through the private-litigation listing.
"""

import json
from typing import Any, Dict, List, Optional


def _rel_id(entity, name: str) -> Optional[str]:
    related = getattr(entity, name, None)
    return related._id if related is not None else None


def _rel_attr(entity, name: str, attr: str) -> Optional[Any]:
    related = getattr(entity, name, None)
    return getattr(related, attr, None) if related is not None else None


def _related_list(entity, name: str) -> List:
    try:
        return list(getattr(entity, name, None) or [])
    except Exception:
        return []


def parse_metadata(entity) -> Dict[str, Any]:
    try:
        return json.loads(entity.metadata) if entity.metadata else {}
    except (ValueError, TypeError):
        return {}


# ---------------------------------------------------------------------------
# Structure: justice systems, courts, judges
# ---------------------------------------------------------------------------


def justice_system(js) -> Dict[str, Any]:
    return {
        "id": js._id,
        "name": js.name or "",
        "description": js.description or "",
        "system_type": js.system_type or "",
        "status": js.status or "",
        "court_count": len(_related_list(js, "courts")),
    }


def court(c) -> Dict[str, Any]:
    return {
        "id": c._id,
        "name": c.name,
        "description": c.description or "",
        "jurisdiction": c.jurisdiction or "",
        "level": c.level or "",
        "status": c.status or "",
        "justice_system_id": _rel_id(c, "justice_system"),
        "justice_system_name": _rel_attr(c, "justice_system", "name"),
        "license_valid": bool(c.license.is_valid()) if c.license else False,
        "case_count": len(_related_list(c, "cases")),
        "judge_count": len(_related_list(c, "judges")),
        "parent_court_id": _rel_id(c, "parent_court"),
        "parent_court_name": _rel_attr(c, "parent_court", "name"),
        "metadata": c.metadata or "",
    }


def judge(j) -> Dict[str, Any]:
    return {
        "id": j._id,
        "appointment_date": j.appointment_date or "",
        "status": j.status or "",
        "specialization": j.specialization or "",
        "court_id": _rel_id(j, "court"),
        "court_name": _rel_attr(j, "court", "name"),
        "member_id": _rel_id(j, "member"),
        "is_active": bool(j.is_active()),
        "case_count": len(_related_list(j, "cases_assigned")),
    }


# ---------------------------------------------------------------------------
# Proceedings: cases, verdicts, penalties, appeals
# ---------------------------------------------------------------------------


def case(c) -> Dict[str, Any]:
    judges = _related_list(c, "judges")
    verdicts = _related_list(c, "verdicts")
    appeals = _related_list(c, "appeals")
    return {
        "id": c._id,
        "case_number": c.case_number or "",
        "title": c.title or "",
        "description": c.description or "",
        "status": c.status or "",
        "filed_date": c.filed_date or "",
        "closed_date": c.closed_date or "",
        "court_id": _rel_id(c, "court"),
        "court_name": _rel_attr(c, "court", "name"),
        "plaintiff_id": _rel_id(c, "plaintiff"),
        "defendant_id": _rel_id(c, "defendant"),
        "judges": [
            {"id": j._id, "specialization": j.specialization} for j in judges
        ],
        "verdict_count": len(verdicts),
        "appeal_count": len(appeals),
        "has_verdict": bool(verdicts),
        "has_appeal": bool(appeals),
    }


def penalty(p) -> Dict[str, Any]:
    return {
        "id": p._id,
        "penalty_type": p.penalty_type or "",
        "amount": p.amount or 0,
        "currency": p.currency or "",
        "description": p.description or "",
        "status": p.status or "",
        "due_date": p.due_date or "",
        "executed_date": p.executed_date or "",
        "target_user_id": _rel_id(p, "target_user"),
        "verdict_id": _rel_id(p, "verdict"),
    }


def verdict(v) -> Dict[str, Any]:
    penalties = _related_list(v, "penalties")
    return {
        "id": v._id,
        "decision": v.decision or "",
        "reasoning": v.reasoning or "",
        "issued_date": v.issued_date or "",
        "case_id": _rel_id(v, "case"),
        "case_number": _rel_attr(v, "case", "case_number"),
        "issued_by_id": _rel_id(v, "issued_by"),
        "penalties": [penalty(p) for p in penalties],
        "penalty_count": len(penalties),
    }


def appeal(a) -> Dict[str, Any]:
    return {
        "id": a._id,
        "grounds": a.grounds or "",
        "status": a.status or "",
        "filed_date": a.filed_date or "",
        # The entity field is ``decided_date``; reading ``decision_date``
        # raised AttributeError on every appeal projection (10k E2E, P16).
        "decision_date": a.decided_date or "",
        "decision": a.decision or "",
        "original_case_id": _rel_id(a, "original_case"),
        "original_case_number": _rel_attr(a, "original_case", "case_number"),
        "original_verdict_id": _rel_id(a, "original_verdict"),
        "appellate_court_id": _rel_id(a, "appellate_court"),
        "appellate_court_name": _rel_attr(a, "appellate_court", "name"),
        "appellant_id": _rel_id(a, "appellant"),
    }


def case_detail(c) -> Dict[str, Any]:
    """A case with its verdicts and appeals inlined, for the detail view."""
    data = case(c)
    # Verdict.case is OneToOne("Case", "verdict") — the reverse accessor is
    # the singular ``c.verdict``. Reading a plural ``verdicts`` silently
    # yielded [] forever (same wrong-name family as the P11 appeal bug).
    single = getattr(c, "verdict", None)
    data["verdicts"] = [verdict(single)] if single is not None else []
    data["appeals"] = [appeal(a) for a in _related_list(c, "appeals")]
    return data


# ---------------------------------------------------------------------------
# Private litigations
# ---------------------------------------------------------------------------


def _defendant_fields(c, meta: Dict[str, Any]) -> Dict[str, str]:
    """A defendant is a person or a department.

    ``Case.defendant`` can only point at a ``User``, so a department defendant is
    recorded in metadata instead and has no principal.
    """
    if meta.get("defendant_kind") == "department":
        return {
            "defendant_kind": "department",
            "defendant_principal": "",
            "defendant_label": meta.get("defendant_department") or "Department",
        }
    principal = (
        c.defendant._id if c.defendant
        else (meta.get("defendant_principal") or "unknown")
    )
    return {
        "defendant_kind": "user",
        "defendant_principal": principal,
        "defendant_label": principal,
    }


def _latest_decision(c) -> Optional[str]:
    verdict_rel = getattr(c, "verdict", None)
    if verdict_rel is not None:
        return getattr(verdict_rel, "decision", None)
    verdicts = _related_list(c, "verdicts")
    return getattr(verdicts[0], "decision", None) if verdicts else None


def litigation_row(c, content) -> Dict[str, Any]:
    """One row of the private-litigation listing.

    ``content_ciphertext`` is returned to every caller who may see the case; it is
    an opaque AES-GCM blob and useless without a ``KeyEnvelope`` at
    ``content_scope``. The plaintext title and description are blanked whenever a
    content record exists.
    """
    is_private = content is not None
    meta = parse_metadata(c)
    defendant = _defendant_fields(c, meta)
    return {
        "id": str(c._id),
        "case_number": c.case_number or "",
        "requester_principal": _rel_id(c, "plaintiff") or "unknown",
        "defendant_principal": defendant["defendant_principal"],
        "defendant_kind": defendant["defendant_kind"],
        "defendant_label": defendant["defendant_label"],
        "case_title": "" if is_private else (c.title or ""),
        "description": "" if is_private else (c.description or ""),
        "content_scope": (content.scope or "") if is_private else "",
        "content_ciphertext": (content.ciphertext or "") if is_private else "",
        "is_private": is_private,
        "status": c.status or "filed",
        "court_id": str(c.court._id) if c.court else None,
        "court_name": _rel_attr(c, "court", "name") or "",
        "requested_at": c.filed_date or "",
        "verdict": _latest_decision(c),
        "actions_taken": [],
    }
