"""``justice.*`` verbs (issue #272).

The bridge surface for the ``justice_litigation`` extension. Each verb takes
``caller`` from the host's dispatch, checks the RBAC operation the action needs,
and calls into the domain modules.

Reads are filtered, not just gated. ``justice.verdicts``, ``justice.penalties``
and ``justice.appeals`` all reach records that belong to a case, and a private
litigation's verdict is as sensitive as the litigation. In-process, those three
returned everything in the realm to anyone holding ``dispute.view``; here each row
is checked against the case it hangs off.

The one aggregate, ``justice.statistics``, deliberately counts every case
regardless of visibility. Counts and status totals are how a realm reports on its
own dispute load; they carry no case content, and hiding them per-caller would
make the number mean something different for each reader.
"""

from typing import Any, Dict, List, Optional

from ic_python_logging import get_logger

from core.justice import cases, content, courts, projections, roles

logger = get_logger("core.justice")

content.register_scope_policy()

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100


def _require_id(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    return text


def _visible(case_id: Any, caller: str):
    return cases.visible_case(_require_id(case_id, "case_id"), caller)


# ---------------------------------------------------------------------------
# Standing and structure
# ---------------------------------------------------------------------------


def v_roles(caller: str = "", **kwargs) -> dict:
    roles.get_user(caller)
    return roles.describe(caller)


def v_audience(caller: str = "", **kwargs) -> dict:
    """The justice department's recipient principals.

    A client filing a litigation needs these to IBE-wrap the DEK for everyone
    allowed to read it, so this is available to anyone who may file.
    """
    roles.get_user(caller)
    roles.require_operation(caller, roles.OP_CREATE, "reading the justice audience")
    return {
        "department": roles.JUSTICE_DEPARTMENT,
        "principals": roles.justice_principals(),
    }


def v_justice_systems(caller: str = "", system_type: str = "", **kwargs) -> dict:
    roles.get_user(caller)
    wanted = str(system_type or "").strip()
    systems = [
        projections.justice_system(s) for s in courts.all_systems()
        if not wanted or s.system_type == wanted
    ]
    return {"justice_systems": systems, "total_count": len(systems)}


def v_courts(
    caller: str = "",
    justice_system_id=None,
    status: str = "",
    level: str = "",
    **kwargs,
) -> dict:
    """The court hierarchy. Public within the realm: which courts exist and at
    what level is how a member knows where to file."""
    roles.get_user(caller)
    rows = courts.all_courts()
    if justice_system_id:
        rows = [
            c for c in rows
            if c.justice_system and c.justice_system._id == justice_system_id
        ]
    if status:
        rows = [c for c in rows if c.status == status]
    if level:
        rows = [c for c in rows if c.level == level]
    return {
        "courts": [projections.court(c) for c in rows],
        "total_count": len(rows),
    }


def v_judges(
    caller: str = "",
    court_id=None,
    status: str = "",
    specialization: str = "",
    **kwargs,
) -> dict:
    roles.get_user(caller)
    rows = cases.all_judges()
    if court_id:
        rows = [j for j in rows if j.court and j.court._id == court_id]
    if status:
        rows = [j for j in rows if j.status == status]
    if specialization:
        needle = specialization.lower()
        rows = [
            j for j in rows
            if j.specialization and needle in j.specialization.lower()
        ]
    return {
        "judges": [projections.judge(j) for j in rows],
        "total_count": len(rows),
    }


# ---------------------------------------------------------------------------
# Court administration
# ---------------------------------------------------------------------------


def _require_court_admin(caller: str, what: str) -> None:
    if not roles.can_manage_courts(caller):
        raise PermissionError(
            f"{what} requires being a realm admin or the "
            f"{roles.JUSTICE_DEPARTMENT} department head"
        )


def v_initialize(caller: str = "", **kwargs) -> dict:
    """Post-install hook: guarantee at least one active court exists.

    Idempotent, so filing never dead-ends with "No courts available" on a realm
    whose codex did not seed a hierarchy.
    """
    _require_court_admin(caller, "initializing the justice system")
    created = courts.ensure_default_court()
    return {
        "default_court_created": created.name if created else None,
        "total_courts": len(courts.all_courts()),
    }


def v_seed_courts(caller: str = "", **kwargs) -> dict:
    """Create the fallback court, so the UI can offer a one-click fix."""
    _require_court_admin(caller, "seeding courts")
    created = courts.ensure_default_court()
    rows = [projections.court(c) for c in courts.all_courts()]
    return {
        "created": created.name if created else None,
        "courts": rows,
        "total_count": len(rows),
    }


def v_create_court(
    caller: str = "",
    name: str = "",
    description: str = "",
    jurisdiction: str = "",
    level: str = "",
    justice_system_id=None,
    parent_court_id=None,
    **kwargs,
) -> dict:
    _require_court_admin(caller, "creating a court")
    court = courts.create_court(
        name=name, description=description, jurisdiction=jurisdiction,
        level=level, justice_system_id=justice_system_id,
        parent_court_id=parent_court_id,
    )
    return {"court": projections.court(court)}


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


def v_cases(
    caller: str = "",
    court_id=None,
    status: str = "",
    plaintiff_id=None,
    defendant_id=None,
    user_id=None,
    **kwargs,
) -> dict:
    """Cases the caller may see, optionally narrowed.

    The visibility filter is applied last and unconditionally. The parameters can
    only narrow the result — an ordinary member omitting them gets their own
    cases, not the realm's.
    """
    roles.get_user(caller)
    rows = list(_all_cases())

    if court_id:
        rows = [c for c in rows if c.court and c.court._id == court_id]
    if status:
        rows = [c for c in rows if c.status == status]
    if plaintiff_id:
        rows = [c for c in rows if c.plaintiff and c.plaintiff._id == plaintiff_id]
    if defendant_id:
        rows = [c for c in rows if c.defendant and c.defendant._id == defendant_id]
    if user_id:
        rows = [
            c for c in rows
            if (c.plaintiff and c.plaintiff._id == user_id)
            or (c.defendant and c.defendant._id == user_id)
        ]

    rows = [c for c in rows if roles.can_view_case(c, caller)]
    return {
        "cases": [projections.case(c) for c in rows],
        "total_count": len(rows),
    }


def _all_cases() -> List:
    from ggg import Case

    return list(Case.instances())


def v_case(caller: str = "", case_id=None, **kwargs) -> dict:
    """One case with its verdicts and appeals."""
    roles.get_user(caller)
    return {"case": projections.case_detail(_visible(case_id, caller))}


def v_file_case(
    caller: str = "",
    court_id=None,
    defendant_id=None,
    title: str = "",
    description: str = "",
    **kwargs,
) -> dict:
    """File a public case as the calling plaintiff.

    There is no ``plaintiff_id``: the in-process version took one, so a caller
    holding ``dispute.create`` could file in another member's name.
    """
    roles.get_user(caller)
    roles.require_operation(caller, roles.OP_CREATE, "filing a case")
    new_case = cases.file_case(
        caller, _require_id(court_id, "court_id"), defendant_id, title, description,
    )
    return {
        "case": projections.case(new_case),
        "message": f"Case {new_case.case_number} filed successfully",
    }


def v_assign_judge(caller: str = "", case_id=None, judge_id=None, **kwargs) -> dict:
    roles.get_user(caller)
    roles.require_operation(caller, roles.OP_ASSIGN, "assigning a judge")
    updated = cases.assign_judge(
        caller, _require_id(case_id, "case_id"), _require_id(judge_id, "judge_id"),
    )
    return {
        "case": projections.case(updated),
        "message": f"Judge {judge_id} assigned to case {updated.case_number}",
    }


# ---------------------------------------------------------------------------
# Private litigations
# ---------------------------------------------------------------------------


def v_litigations(
    caller: str = "", from_id: Any = 1, page_size: Any = DEFAULT_PAGE_SIZE, **kwargs
) -> dict:
    """The litigations the caller may access, paged.

    Justice members and admins page through every case with ``load_some``, to stay
    under the IC per-message instruction limit on a long-lived realm. Everyone
    else gets their own cases via the ``User -> Case`` relation, which is a
    forward lookup rather than a scan.

    ``can_view_all`` is derived from the caller here and is never accepted as an
    argument — it is the whole authorization decision.
    """
    from ggg import Case, User

    roles.get_user(caller)
    try:
        start = max(1, int(from_id or 1))
        limit = max(1, min(int(page_size or DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE))
    except (TypeError, ValueError):
        raise ValueError("from_id and page_size must be integers")

    sees_all = roles.sees_all_cases(caller)
    next_from_id: Optional[int] = None
    has_more = False

    if sees_all:
        max_id = Case.max_id()
        batch = Case.load_some(from_id=start, count=limit) if max_id else []
        if batch:
            next_candidate = int(batch[-1]._id) + 1
            has_more = next_candidate <= max_id
            next_from_id = next_candidate if has_more else None
        rows = batch
    else:
        user = User[caller]
        try:
            rows = list(user.cases_as_plaintiff or []) if user else []
        except Exception:
            rows = []

    litigations = [
        projections.litigation_row(c, content.find(c._id)) for c in rows
    ]
    return {
        "litigations": litigations,
        "total_count": Case.count() if sees_all else len(litigations),
        "user_profile": "admin" if sees_all else "member",
        "can_view_all": sees_all,
        "next_from_id": next_from_id,
        "has_more": has_more,
    }


def v_create_litigation(
    caller: str = "",
    court_id=None,
    defendant_principal=None,
    defendant_id=None,
    defendant_kind: str = "",
    defendant_department: str = "",
    defendant_department_id: str = "",
    defendant_quarter_id: str = "",
    **kwargs,
) -> dict:
    """Open a private litigation. The submitter is the caller.

    Defendant may be a local principal or a ``realm://`` User address
    (issue #325). That address is not a venue and does not skip Transfer.
    """
    roles.get_user(caller)
    roles.require_operation(caller, roles.OP_CREATE, "opening a litigation")
    return cases.create_litigation(
        caller,
        court_id=court_id,
        defendant_id=defendant_principal or defendant_id,
        defendant_kind=defendant_kind,
        defendant_department=defendant_department,
        defendant_department_id=defendant_department_id,
        defendant_quarter_id=defendant_quarter_id,
    )


def v_set_litigation_content(
    caller: str = "", case_id=None, id=None, ciphertext: str = "", **kwargs
) -> dict:
    roles.get_user(caller)
    roles.require_operation(caller, roles.OP_CREATE, "editing a litigation")
    return cases.set_litigation_content(
        caller, _require_id(case_id or id, "case_id"), ciphertext
    )


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------


def v_verdicts(caller: str = "", case_id=None, **kwargs) -> dict:
    """Verdicts on cases the caller may see.

    Filtered per row rather than gated once: a verdict on a private litigation is
    as sensitive as the litigation, and in-process this returned every verdict in
    the realm to anyone with ``dispute.view``.
    """
    roles.get_user(caller)
    rows = cases.all_verdicts()
    if case_id:
        rows = [v for v in rows if v.case and v.case._id == case_id]
    rows = [
        v for v in rows
        if v.case is not None and roles.can_view_case(v.case, caller)
    ]
    return {
        "verdicts": [projections.verdict(v) for v in rows],
        "total_count": len(rows),
    }


def v_issue_verdict(
    caller: str = "",
    case_id=None,
    decision: str = "",
    reasoning: str = "",
    penalties: Optional[list] = None,
    **kwargs,
) -> dict:
    """Issue a verdict, as an assigned judge.

    There is no ``judge_id``: the verdict is attributed to the caller's own
    assignment on this case. In-process the id was a parameter and was never
    checked against the case, so a caller with ``resolution.issue`` could rule on
    any case in the realm under any judge's name.
    """
    roles.get_user(caller)
    roles.require_operation(caller, roles.OP_ISSUE, "issuing a verdict")
    verdict = cases.issue_verdict(
        caller,
        _require_id(case_id, "case_id"),
        decision,
        reasoning,
        penalties,
        as_admin=roles.is_realm_admin(caller),
    )
    return {
        "verdict": projections.verdict(verdict),
        "message": f"Verdict issued for case {verdict.case.case_number}",
    }


# ---------------------------------------------------------------------------
# Penalties
# ---------------------------------------------------------------------------


def v_penalties(
    caller: str = "",
    verdict_id=None,
    target_user_id=None,
    status: str = "",
    **kwargs,
) -> dict:
    """Penalties on cases the caller may see, plus any levied against them.

    A person is always shown what they owe, even on a case they cannot otherwise
    read — a fine you cannot see is a fine you cannot pay.
    """
    roles.get_user(caller)
    rows = cases.all_penalties()
    if verdict_id:
        rows = [p for p in rows if p.verdict and p.verdict._id == verdict_id]
    if target_user_id:
        rows = [
            p for p in rows
            if p.target_user and p.target_user._id == target_user_id
        ]
    if status:
        rows = [p for p in rows if p.status == status]

    def visible(p) -> bool:
        if roles.principal_of(getattr(p, "target_user", None)) == caller:
            return True
        case = cases.case_of_penalty(p)
        return case is not None and roles.can_view_case(case, caller)

    rows = [p for p in rows if visible(p)]
    return {
        "penalties": [projections.penalty(p) for p in rows],
        "total_count": len(rows),
    }


def v_execute_penalty(caller: str = "", penalty_id=None, **kwargs) -> dict:
    """Execute a penalty, booking an executed fine as Justice revenue.

    No ``executor_id``: the in-process version accepted one and ignored it, which
    is worse than either using or refusing it.
    """
    roles.get_user(caller)
    roles.require_operation(caller, roles.OP_FINE, "executing a penalty")
    updated = cases.execute_penalty(caller, _require_id(penalty_id, "penalty_id"))
    if getattr(updated, "status", None) != "executed":
        return {
            "penalty": projections.penalty(updated),
            "message": (
                f"Penalty {penalty_id} still pending "
                "(no collection or no restitution ack)"
            ),
            "pending": True,
        }
    return {
        "penalty": projections.penalty(updated),
        "message": f"Penalty {penalty_id} executed successfully",
    }


def v_waive_penalty(
    caller: str = "", penalty_id=None, reason: str = "", **kwargs
) -> dict:
    roles.get_user(caller)
    roles.require_operation(caller, roles.OP_FINE, "waiving a penalty")
    updated = cases.waive_penalty(
        caller, _require_id(penalty_id, "penalty_id"), reason
    )
    return {
        "penalty": projections.penalty(updated),
        "message": f"Penalty {penalty_id} waived",
    }


# ---------------------------------------------------------------------------
# Appeals
# ---------------------------------------------------------------------------


def v_appeals(
    caller: str = "",
    case_id=None,
    appellant_id=None,
    status: str = "",
    court_id=None,
    **kwargs,
) -> dict:
    roles.get_user(caller)
    rows = cases.all_appeals()
    if case_id:
        rows = [
            a for a in rows if a.original_case and a.original_case._id == case_id
        ]
    if appellant_id:
        rows = [a for a in rows if a.appellant and a.appellant._id == appellant_id]
    if status:
        rows = [a for a in rows if a.status == status]
    if court_id:
        rows = [
            a for a in rows
            if a.appellate_court and a.appellate_court._id == court_id
        ]

    def visible(a) -> bool:
        appellant = getattr(a, "appellant", None)
        if roles.principal_of(appellant) == caller:
            return True
        case = getattr(a, "original_case", None)
        return case is not None and roles.can_view_case(case, caller)

    rows = [a for a in rows if visible(a)]
    return {
        "appeals": [projections.appeal(a) for a in rows],
        "total_count": len(rows),
    }


def v_file_appeal(
    caller: str = "",
    case_id=None,
    grounds: str = "",
    appellate_court_id=None,
    **kwargs,
) -> dict:
    """Appeal a case's most recent verdict, as a party to it.

    No ``appellant_id``: the appellant is the caller, and must be the plaintiff,
    the defendant, or an admin.
    """
    roles.get_user(caller)
    roles.require_operation(caller, roles.OP_CREATE, "filing an appeal")
    appeal = cases.file_appeal(
        caller, _require_id(case_id, "case_id"), grounds, appellate_court_id
    )
    return {
        "appeal": projections.appeal(appeal),
        "message": f"Appeal filed for case {appeal.original_case.case_number}",
    }


def v_decide_appeal(
    caller: str = "",
    appeal_id=None,
    decision: str = "",
    reasoning: str = "",
    **kwargs,
) -> dict:
    roles.get_user(caller)
    roles.require_operation(caller, roles.OP_APPEAL, "deciding an appeal")
    updated = cases.decide_appeal(
        caller, _require_id(appeal_id, "appeal_id"), decision, reasoning
    )
    return {
        "appeal": projections.appeal(updated),
        "message": f"Appeal {appeal_id} decided: {decision}",
    }


def v_withdraw_appeal(
    caller: str = "", appeal_id=None, **kwargs
) -> dict:
    roles.get_user(caller)
    roles.require_operation(caller, roles.OP_CREATE, "withdrawing an appeal")
    updated = cases.withdraw_appeal(caller, _require_id(appeal_id, "appeal_id"))
    return {
        "appeal": projections.appeal(updated),
        "message": f"Appeal {appeal_id} withdrawn",
    }


def v_transfer_case(
    caller: str = "",
    case_id=None,
    dest=None,
    ciphertext: str = "",
    wrapped_deks=None,
    origin_scope: str = "",
    **kwargs,
) -> dict:
    """Judge-only Transfer: origin freeze + dest canister pointer + pipe.

    Dest is a canister id, not a user venue picker. Ciphertext and optional
    wrapped DEKs (dest Justice + filer) travel; plaintext does not.
    """
    roles.get_user(caller)
    roles.require_operation(caller, roles.OP_ISSUE, "transferring a case")
    updated = cases.transfer_case(
        caller,
        _require_id(case_id, "case_id"),
        dest,
        ciphertext=ciphertext,
        wrapped_deks=wrapped_deks,
        origin_scope=origin_scope,
    )
    return {
        "case": projections.case(updated),
        "message": f"Case {updated.case_number} transferred",
    }


def v_begin_executing(caller: str = "", case_id=None, **kwargs) -> dict:
    """Verdict is final: no appeal, cannot appeal, or the window closed."""
    roles.get_user(caller)
    roles.require_operation(caller, roles.OP_ISSUE, "beginning execution")
    updated = cases.begin_executing(caller, _require_id(case_id, "case_id"))
    return {
        "case": projections.case(updated),
        "message": f"Case {updated.case_number} is executing",
    }


def v_close_case(caller: str = "", case_id=None, **kwargs) -> dict:
    roles.get_user(caller)
    roles.require_operation(caller, roles.OP_ISSUE, "closing a case")
    updated = cases.close_case(caller, _require_id(case_id, "case_id"))
    return {
        "case": projections.case(updated),
        "message": f"Case {updated.case_number} closed",
    }


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def v_statistics(caller: str = "", **kwargs) -> dict:
    """Realm-wide justice counts.

    Not filtered by visibility, unlike every read above. These are aggregates with
    no case content, and they are how a realm reports its own dispute load — a
    total that differed per reader would not be a total.
    """
    roles.get_user(caller)
    all_cases = _all_cases()
    verdicts = cases.all_verdicts()
    penalties = cases.all_penalties()
    appeals = cases.all_appeals()

    def tally(rows) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for row in rows:
            key = row.status or "unknown"
            counts[key] = counts.get(key, 0) + 1
        return counts

    return {
        "overview": {
            "total_cases": len(all_cases),
            "total_verdicts": len(verdicts),
            "total_penalties": len(penalties),
            "total_appeals": len(appeals),
            "total_courts": len(courts.all_courts()),
            "total_judges": len(cases.all_judges()),
        },
        "cases_by_status": tally(all_cases),
        "penalties": {
            "total_amount": sum(p.amount or 0 for p in penalties),
            "pending_count": len([p for p in penalties if p.status == "pending"]),
            "executed_count": len([p for p in penalties if p.status == "executed"]),
        },
        "appeals_by_status": tally(appeals),
    }


VERBS = {
    "justice.roles": v_roles,
    "justice.audience": v_audience,
    "justice.justice_systems": v_justice_systems,
    "justice.courts": v_courts,
    "justice.judges": v_judges,
    "justice.cases": v_cases,
    "justice.case": v_case,
    "justice.litigations": v_litigations,
    "justice.verdicts": v_verdicts,
    "justice.penalties": v_penalties,
    "justice.appeals": v_appeals,
    "justice.statistics": v_statistics,
    "justice.initialize": v_initialize,
    "justice.seed_courts": v_seed_courts,
    "justice.create_court": v_create_court,
    "justice.file_case": v_file_case,
    "justice.assign_judge": v_assign_judge,
    "justice.create_litigation": v_create_litigation,
    "justice.set_litigation_content": v_set_litigation_content,
    "justice.issue_verdict": v_issue_verdict,
    "justice.execute_penalty": v_execute_penalty,
    "justice.waive_penalty": v_waive_penalty,
    "justice.file_appeal": v_file_appeal,
    "justice.decide_appeal": v_decide_appeal,
    "justice.withdraw_appeal": v_withdraw_appeal,
    "justice.transfer_case": v_transfer_case,
    "justice.begin_executing": v_begin_executing,
    "justice.close_case": v_close_case,
}

READ_VERBS = frozenset({
    "justice.roles",
    "justice.audience",
    "justice.justice_systems",
    "justice.courts",
    "justice.judges",
    "justice.cases",
    "justice.case",
    "justice.litigations",
    "justice.verdicts",
    "justice.penalties",
    "justice.appeals",
    "justice.statistics",
})
