"""Case lifecycle: filing, judges, verdicts, penalties, appeals.

The state changes themselves are :mod:`ggg.justice`'s (``case_file``,
``case_assign_judges``, ``case_issue_verdict``, ``case_transfer``,
``case_begin_executing``, ``case_close``, ``penalty_execute``,
``penalty_waive``, ``appeal_file``, ``appeal_decide``, ``appeal_withdraw``).
What is here is the part the extension used to do and should not have:
deciding *whose* name goes on each one, and refusing the combinations that
make the process meaningless.

Three of those refusals are new, and each closes a hole in the in-process version:

* **A case is filed by its plaintiff.** ``file_case`` accepted a ``plaintiff_id``,
  so anyone holding ``dispute.create`` could file a case in another member's name.
* **A verdict is issued by an assigned judge.** ``issue_verdict`` accepted a
  ``judge_id`` and never checked it against the case, so anyone holding
  ``resolution.issue`` could issue a ruling attributed to any judge in the realm,
  on a case that judge had never seen.
* **An appeal is filed by a party to the case.** ``file_appeal`` accepted an
  ``appellant_id``, so an appeal could be filed in a stranger's name.

Cross-quarter transfer is a judge act (issue #325): origin Case freezes as
``transferred`` with a dest canister pointer; dest creates the live Case via
``justice.transfer``. Restitution uses ``justice.restitution`` after dest
collects from the defendant. Fine stays in dest treasury.
"""

import json
from typing import Any, Dict, List, Optional

from ic_python_logging import get_logger

from core.justice import content, courts, roles

logger = get_logger("core.justice.cases")


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


def find_case(case_id):
    """By id, then by case number — the UI hands back whichever it has."""
    from ggg import Case

    try:
        found = Case[case_id]
        if found:
            return found
    except Exception:
        pass
    try:
        matches = Case.find({"case_number": case_id})
        return matches[0] if matches else None
    except Exception:
        return None


def require_case(case_id):
    found = find_case(case_id)
    if not found:
        raise ValueError(f"Case {case_id} not found")
    return found


def visible_case(case_id, caller: str):
    """A case the caller may see, or the same refusal as a missing one.

    Deliberately indistinguishable: a distinct "not allowed" would let a caller
    probe ids to learn which cases exist, and the existence of a private
    litigation is itself the sensitive part.
    """
    found = find_case(case_id)
    if not found or not roles.can_view_case(found, caller):
        raise ValueError(f"Case {case_id} not found")
    return found


def case_of_verdict(verdict):
    return getattr(verdict, "case", None)


def case_of_penalty(penalty):
    verdict = getattr(penalty, "verdict", None)
    return getattr(verdict, "case", None) if verdict is not None else None


def cross_quarter_id(case) -> str:
    from core.justice.projections import parse_metadata

    return str(parse_metadata(case).get("defendant_quarter_id") or "")


def _warn_cross_quarter(case, action: str) -> None:
    quarter = cross_quarter_id(case)
    if quarter:
        logger.warning(
            f"{action}: case {case.case_number} is cross-quarter "
            f"(defendant_quarter_id={quarter}); acting against the remote "
            f"quarter needs an inter-canister call and is not implemented"
        )


# ---------------------------------------------------------------------------
# Filing
# ---------------------------------------------------------------------------


def file_case(
    caller: str,
    court_id,
    defendant_id,
    title: str,
    description: str = "",
) -> object:
    """File a public case. The plaintiff is *caller* and cannot be anything else."""
    from ggg import User, case_file

    court = courts.find_court(court_id)
    if not court:
        raise ValueError(f"Court {court_id} not found")

    plaintiff = User[caller]
    if not plaintiff:
        raise ValueError(f"{caller} is not a registered user")

    defendant = User[defendant_id] if defendant_id else None
    if defendant_id and not defendant:
        raise ValueError(f"Defendant user {defendant_id} not found")

    if not (title or "").strip():
        raise ValueError("title is required")

    new_case = case_file(
        court=court,
        plaintiff=plaintiff,
        defendant=defendant,
        title=str(title),
        description=str(description or ""),
    )
    logger.info(f"Case {new_case.case_number} filed by {caller}")
    return new_case


def _defendant_metadata(
    caller: str,
    defendant_kind: str,
    defendant_id,
    department_name: str,
    department_id: str,
    quarter_id: str,
) -> tuple:
    """Resolve a litigation's defendant into ``(defendant_user, metadata_json)``.

    A department defendant leaves ``Case.defendant`` empty — that relation only
    points at a ``User`` — and is recorded in metadata instead. Either way the
    defendant is recorded but never granted read access, so a litigation stays
    private regardless of who it names.
    """
    from _cdk import ic
    from core.justice.federation import parse_user_address
    from ggg import Department, User

    try:
        own_canister = ic.id().to_str()
    except Exception:
        own_canister = ""

    address = parse_user_address(defendant_id) if defendant_kind != "department" else {
        "principal": "", "canister_id": "", "ref": "",
    }
    if address["principal"]:
        defendant_id = address["principal"]
    # realm:// canister is an address (issue #156 / #325), not a venue.
    if not quarter_id and address["canister_id"]:
        quarter_id = address["canister_id"]

    is_cross_quarter = bool(quarter_id and quarter_id != own_canister)

    if defendant_kind == "department":
        if not (department_name or department_id):
            raise ValueError("Department defendant requires a name or id")
        dept = Department[department_id] if department_id else None
        if dept is None and department_name:
            dept = Department[department_name]
        meta: Dict[str, Any] = {
            "defendant_kind": "department",
            "defendant_department": (
                getattr(dept, "name", None) or department_name or department_id
            ),
            "defendant_department_id": str(
                getattr(dept, "_id", "") or department_id or ""
            ),
        }
        defendant = None
    else:
        defendant = User[defendant_id] if defendant_id else None
        meta = (
            {"defendant_kind": "user", "defendant_principal": defendant_id}
            if defendant_id else {}
        )
        if address.get("ref"):
            meta["defendant_ref"] = address["ref"]

    if is_cross_quarter:
        meta["defendant_quarter_id"] = quarter_id
        meta["scope_tag"] = "cross_quarter"

    return defendant, (json.dumps(meta) if meta else ""), is_cross_quarter


def create_litigation(
    caller: str,
    court_id=None,
    defendant_id=None,
    defendant_kind: str = "",
    defendant_department: str = "",
    defendant_department_id: str = "",
    defendant_quarter_id: str = "",
) -> Dict[str, Any]:
    """Open a private litigation: step 1 of 2.

    The case is created with no plaintext title or description; the encrypted
    content is attached by :func:`set_litigation_content`. Two steps because the
    key scope embeds the case id, which does not exist until the case does.

    ``defendant_id`` may be a local principal or a ``realm://`` User
    address (issue #156 / #325). That address is not a venue and does not
    skip judge Transfer.

    Returns the scope and the recipient principals the client must wrap the DEK
    for — the submitter plus the justice department.
    """
    from ggg import case_file

    if not caller:
        raise PermissionError("Unable to determine caller principal")

    court = courts.find_court(court_id) if court_id else courts.preferred_court()
    if not court:
        if court_id:
            raise ValueError(f"Court {court_id} not found")
        raise ValueError("No courts available. Please create a court first.")

    kind = (defendant_kind or "").strip().lower()
    department_name = (defendant_department or "").strip()
    department_id = str(defendant_department_id or "").strip()
    if not kind:
        kind = "department" if (department_name or department_id) else "user"

    defendant, metadata, is_cross_quarter = _defendant_metadata(
        caller, kind, defendant_id, department_name, department_id,
        str(defendant_quarter_id or "").strip(),
    )

    from ggg import User

    plaintiff = User[caller]
    if not plaintiff:
        raise ValueError(f"Submitter {caller} is not a registered user")

    new_case = case_file(
        court=court,
        plaintiff=plaintiff,
        defendant=defendant,
        title="",
        description="",
        metadata=metadata,
    )
    scope = content.create(new_case._id, caller)

    recipients = list(roles.justice_principals())
    if caller not in recipients:
        recipients.append(caller)

    result = {
        "id": str(new_case._id),
        "case_number": new_case.case_number or "",
        "scope": scope,
        "recipients": recipients,
        "message": f"Litigation {new_case.case_number} opened",
    }
    if is_cross_quarter:
        meta = json.loads(metadata) if metadata else {}
        result["defendant_quarter_id"] = str(
            meta.get("defendant_quarter_id") or defendant_quarter_id or ""
        )
        result["scope_tag"] = "cross_quarter"
        logger.info(
            f"Litigation {new_case.case_number} records defendant address "
            f"{result['defendant_quarter_id']}; venue is still judge Transfer"
        )

    logger.info(f"Litigation {new_case.case_number} opened by {caller}")
    return result


def set_litigation_content(caller: str, case_id, ciphertext: str) -> Dict[str, Any]:
    """Attach or replace the encrypted blob: step 2 of 2."""
    case = require_case(case_id)
    if not roles.can_manage_case(case, caller):
        raise PermissionError("Not allowed to edit this litigation")

    record = content.find(case._id)
    if record is None:
        raise ValueError("Litigation has no private content record")
    record.ciphertext = str(ciphertext or "")
    return {"id": str(case._id)}


# ---------------------------------------------------------------------------
# Judges and verdicts
# ---------------------------------------------------------------------------


def all_judges() -> List:
    from ggg import Judge

    return list(Judge.instances())


def assign_judge(caller: str, case_id, judge_id) -> object:
    """Assign a judge to a case."""
    from ggg import Judge, case_assign_judges

    case = require_case(case_id)
    judge = Judge[judge_id]
    if not judge:
        raise ValueError(f"Judge {judge_id} not found")

    updated = case_assign_judges(case=case, judges=[judge])
    logger.info(f"Judge {judge_id} assigned to case {case.case_number} by {caller}")
    return updated


def assigned_judges(case) -> List:
    try:
        return list(getattr(case, "judges", None) or [])
    except Exception:
        return []


def judge_for_caller(case, caller: str):
    """The assigned judge whose member is *caller*, or ``None``.

    This is the check that makes a verdict mean something: a ruling has to come
    from a judge who was assigned to hear the case, not from whoever holds
    ``resolution.issue``.
    """
    for judge in assigned_judges(case):
        member = getattr(judge, "member", None)
        if roles.principal_of(getattr(member, "user", None)) == caller:
            return judge
    return None


def _penalty_specs(penalties: Optional[list]) -> List[Dict[str, Any]]:
    """Rebuild each penalty from an allowlist of keys.

    The list is handed to ``case_issue_verdict``, which constructs ``Penalty``
    rows from it, so a key the caller invents must not reach it. ``status`` in
    particular is not settable: a penalty starts pending and gets executed or
    waived through the verbs that check ``fine.apply``.
    """
    from core.realm_currency import no_treasury_token_error, realm_currency
    from ggg import PenaltyType, User

    allowed = {"type", "penalty_type", "amount", "currency", "description",
               "target_user_id"}
    specs = []
    for raw in (penalties or []):
        if not isinstance(raw, dict):
            raise ValueError("each penalty must be an object")
        unknown = sorted(set(raw) - allowed)
        if unknown:
            raise ValueError(
                f"penalty does not accept {', '.join(unknown)}; "
                f"it accepts {sorted(allowed)}"
            )
        target_id = raw.get("target_user_id")
        target = User[target_id] if target_id else None
        if target_id and not target:
            raise ValueError(f"Penalty target user {target_id} not found")
        try:
            amount = float(raw.get("amount", 0) or 0)
        except (TypeError, ValueError):
            raise ValueError("penalty amount must be a number")
        penalty_type = (
            raw.get("type") or raw.get("penalty_type") or PenaltyType.FINE
        )
        raw_currency = raw.get("currency")
        if raw_currency is not None and str(raw_currency).strip():
            currency = str(raw_currency).strip()
        else:
            currency = realm_currency()
        if (
            penalty_type in (PenaltyType.FINE, PenaltyType.RESTITUTION)
            and amount > 0
            and not currency
        ):
            raise ValueError(no_treasury_token_error()["error"])
        specs.append({
            "penalty_type": penalty_type,
            "amount": amount,
            "currency": currency,
            "description": str(raw.get("description") or ""),
            "target_user": target,
        })
    return specs


def issue_verdict(
    caller: str,
    case_id,
    decision: str,
    reasoning: str = "",
    penalties: Optional[list] = None,
    as_admin: bool = False,
) -> object:
    """Issue a verdict for a case.

    *as_admin* lets a realm admin rule on a case they are not the assigned judge
    for. Kept explicit and separate from the assigned-judge path so the two are
    distinguishable in review — an admin override is a legitimate operational
    escape hatch, but it should not be the ordinary route.
    """
    from ggg import case_issue_verdict

    case = require_case(case_id)
    if not (decision or "").strip():
        raise ValueError("decision is required")

    judge = judge_for_caller(case, caller)
    if judge is None and not as_admin:
        raise PermissionError(
            f"only a judge assigned to case {case.case_number} may issue its "
            f"verdict"
        )

    _warn_cross_quarter(case, "issue_verdict")

    penalty_specs = _penalty_specs(penalties)
    verdict = case_issue_verdict(
        case=case,
        decision=str(decision),
        reasoning=str(reasoning or ""),
        penalties=penalty_specs,
    )
    logger.info(
        f"Verdict issued on case {case.case_number} by {caller}"
        f"{' (admin override)' if judge is None else ''}"
    )
    return verdict


def all_verdicts() -> List:
    from ggg import Verdict

    return list(Verdict.instances())


# ---------------------------------------------------------------------------
# Penalties
# ---------------------------------------------------------------------------


def all_penalties() -> List:
    from ggg import Penalty

    return list(Penalty.instances())


def find_penalty(penalty_id):
    from ggg import Penalty

    penalty = Penalty[penalty_id]
    if not penalty:
        raise ValueError(f"Penalty {penalty_id} not found")
    return penalty


def record_penalty_revenue(penalty) -> int:
    """Book an executed fine as revenue against the Justice department's fund.

    A department that collects money has to record it against its fund (issue
    #260) or the inflow appears nowhere. Creates a balanced pair — debit cash,
    credit fine revenue — keyed on the penalty id so re-execution cannot
    double-book.

    Restitution is excluded: it compensates the harmed party and is not realm
    revenue. Returns the number of ledger entries created.
    """
    from ggg import Department, LedgerEntry, PenaltyType

    if penalty.penalty_type != PenaltyType.FINE:
        return 0
    amount = int(round(penalty.amount or 0))
    if amount <= 0:
        return 0

    dept = Department[roles.JUSTICE_DEPARTMENT]
    fund = getattr(dept, "fund", None) if dept else None
    if fund is None:
        logger.warning(
            f"Penalty {penalty.id} executed but department "
            f"'{roles.JUSTICE_DEPARTMENT}' has no fund — revenue not recorded"
        )
        return 0

    transaction_id = f"TXN-PEN-{penalty.id}"
    if LedgerEntry.find({"transaction_id": transaction_id}):
        return 0

    entry_date = penalty.executed_date or ""
    description = f"Litigation fine — penalty {penalty.id}"
    common = {
        "entry_date": entry_date,
        "currency": penalty.currency or "",
        "fund": fund,
        "penalty": penalty,
        "tags": "litigation,fine",
    }
    entries = LedgerEntry.create_transaction(transaction_id, [
        {**common, "entry_type": "asset", "category": "cash",
         "debit": amount, "credit": 0,
         "description": f"{description} - Cash received"},
        {**common, "entry_type": "revenue", "category": "fine",
         "debit": 0, "credit": amount,
         "description": f"{description} - Revenue"},
    ])
    logger.info(
        f"Recorded fine revenue for penalty {penalty.id} against fund "
        f"{fund.code} ({amount} {penalty.currency})"
    )
    return len(entries)


def execute_penalty(caller: str, penalty_id) -> object:
    from core.justice.federation import (
        collect_from_defendant,
        notify_restitution,
        _penalty_meta,
        _write_penalty_meta,
    )
    from ggg import PenaltyType, penalty_execute

    penalty = find_penalty(penalty_id)
    case = case_of_penalty(penalty)
    if case is not None:
        _warn_cross_quarter(case, "execute_penalty")

    if getattr(penalty, "penalty_type", None) == PenaltyType.RESTITUTION:
        if not collect_from_defendant(penalty):
            logger.info(
                f"Penalty {penalty_id} restitution still pending: "
                "defendant not collected"
            )
            return penalty
        ack = notify_restitution(penalty, case)
        meta = _penalty_meta(penalty)
        meta["collected"] = True
        if ack.get("success"):
            meta["restitution_awaiting_ack"] = False
            meta["restitution_acked"] = True
            _write_penalty_meta(penalty, meta)
            updated = penalty_execute(penalty)
            logger.info(f"Penalty {penalty_id} restitution executed after ack")
            return updated
        meta["restitution_awaiting_ack"] = True
        meta["restitution_acked"] = False
        if ack.get("error"):
            meta["restitution_error"] = str(ack["error"])[:200]
        _write_penalty_meta(penalty, meta)
        logger.info(
            f"Penalty {penalty_id} restitution still pending: no home-quarter ack"
        )
        return penalty

    updated = penalty_execute(penalty)
    try:
        record_penalty_revenue(updated)
    except Exception as e:
        # The penalty is executed either way; failing the call would leave the
        # caller thinking it was not.
        logger.error(f"Penalty revenue accounting failed for {penalty_id}: {e}")

    logger.info(f"Penalty {penalty_id} executed by {caller}")
    return updated


def waive_penalty(caller: str, penalty_id, reason: str = "") -> object:
    from ggg import penalty_waive

    penalty = find_penalty(penalty_id)
    updated = penalty_waive(penalty, str(reason or ""))
    logger.info(f"Penalty {penalty_id} waived by {caller}: {reason}")
    return updated


# ---------------------------------------------------------------------------
# Appeals
# ---------------------------------------------------------------------------


def all_appeals() -> List:
    from ggg import Appeal

    return list(Appeal.instances())


def is_party(case, caller: str) -> bool:
    """Plaintiff or defendant. Unlike viewing, the defendant *is* a party — they
    are the one with something to appeal."""
    for relation in ("plaintiff", "defendant"):
        party = getattr(case, relation, None)
        if roles.principal_of(party) == caller:
            return True
    return False


def file_appeal(
    caller: str, case_id, grounds: str, appellate_court_id=None
) -> object:
    """File an appeal against a case's most recent verdict.

    The appellant is *caller*, and must be a party to the case or an admin. A
    stranger appealing someone else's verdict is not a thing.
    """
    from ggg import User, appeal_file

    case = require_case(case_id)
    if not (grounds or "").strip():
        raise ValueError("grounds are required")

    if not (is_party(case, caller) or roles.is_realm_admin(caller)):
        raise PermissionError(
            f"only a party to case {case.case_number} may appeal it"
        )

    appellant = User[caller]
    if not appellant:
        raise ValueError(f"{caller} is not a registered user")

    # Verdict.case is OneToOne("Case", "verdict") — the reverse accessor is
    # the singular ``case.verdict``. Reading a plural ``verdicts`` here made
    # every appeal fail with "no verdict" even right after a verdict was
    # issued (found by the 10k E2E, phase 6).
    verdict = getattr(case, "verdict", None)
    if verdict is None:
        raise ValueError("Cannot appeal a case without a verdict")

    appellate_court = (
        courts.find_court(appellate_court_id) if appellate_court_id else None
    )
    if appellate_court_id and appellate_court is None:
        raise ValueError(f"Court {appellate_court_id} not found")

    # Party right on the live case. Supreme sits in Capital; if we are
    # already there, appeal only changes court level (no hop).
    if appellate_court is None:
        try:
            from core.federal_vote_runtime import is_capital

            on_capital = is_capital()
        except Exception:
            on_capital = False
        if on_capital:
            appellate_court = courts.preferred_appellate_court()

    appeal = appeal_file(
        case=case,
        appellant=appellant,
        grounds=str(grounds),
        appellate_court=appellate_court,
    )
    logger.info(f"Appeal filed on case {case.case_number} by {caller}")
    return appeal


def decide_appeal(caller: str, appeal_id, decision: str, reasoning: str = ""):
    from ggg import Appeal, appeal_decide

    appeal = Appeal[appeal_id]
    if not appeal:
        raise ValueError(f"Appeal {appeal_id} not found")
    if not (decision or "").strip():
        raise ValueError("decision is required")

    # An appellant deciding their own appeal is the one combination that makes
    # the appeal process meaningless, so it is refused regardless of operations.
    if roles.principal_of(getattr(appeal, "appellant", None)) == caller:
        raise PermissionError("an appellant may not decide their own appeal")

    updated = appeal_decide(appeal, str(decision), str(reasoning or ""))
    logger.info(f"Appeal {appeal_id} decided {decision!r} by {caller}")
    return updated


def withdraw_appeal(caller: str, appeal_id):
    """Appellant (or a party / admin) withdraws a pending appeal."""
    from ggg import Appeal, appeal_withdraw

    appeal = Appeal[appeal_id]
    if not appeal:
        raise ValueError(f"Appeal {appeal_id} not found")

    case = getattr(appeal, "original_case", None)
    appellant_is_caller = (
        roles.principal_of(getattr(appeal, "appellant", None)) == caller
    )
    party = case is not None and is_party(case, caller)
    if not (appellant_is_caller or party or roles.is_realm_admin(caller)):
        raise PermissionError("only the appellant or a party may withdraw this appeal")

    updated = appeal_withdraw(appeal)
    logger.info(f"Appeal {appeal_id} withdrawn by {caller}")
    return updated


def _require_judge_or_admin(case, caller: str, what: str):
    judge = judge_for_caller(case, caller)
    if judge is None and not roles.is_realm_admin(caller):
        raise PermissionError(
            f"only a judge assigned to case {case.case_number} may {what}"
        )
    return judge


def transfer_case(
    caller: str,
    case_id,
    dest=None,
    ciphertext: str = "",
    wrapped_deks=None,
    origin_scope: str = "",
) -> object:
    """Judge-only Transfer: freeze origin, pointer dest canister, send pipe.

    Dest is a canister id (or ``realm://`` address). Not a filer venue picker.
    Ciphertext (already encrypted title/description) travels; re-wrap DEKs
    for dest Justice + filer are passed through, never unwrapped here.
    """
    from core.justice.federation import dest_canister_id, notify_transfer
    from ggg import case_transfer

    case = require_case(case_id)
    _require_judge_or_admin(case, caller, "transfer it")

    if isinstance(dest, str) and dest.strip():
        try:
            dest = json.loads(dest)
        except (TypeError, ValueError):
            dest = {"id": dest}

    # Judge dest only. A filer ``realm://`` address / defendant_quarter_id
    # must not become the transfer target or skip this verb.
    canister = dest_canister_id(dest)
    pointer = dest if isinstance(dest, dict) else {}
    if canister:
        pointer = dict(pointer)
        pointer["id"] = canister

    updated = case_transfer(case, dest=pointer or dest)
    if canister:
        notify_transfer(
            updated,
            canister,
            ciphertext=ciphertext,
            wrapped_deks=wrapped_deks,
            origin_scope=origin_scope,
        )
    logger.info(f"Case {case.case_number} marked transferred by {caller}")
    return updated


def begin_executing(caller: str, case_id) -> object:
    """Declare the verdict final (no appeal / cannot appeal / window closed)."""
    from ggg import case_begin_executing

    case = require_case(case_id)
    _require_judge_or_admin(case, caller, "begin execution")
    updated = case_begin_executing(case)
    logger.info(f"Case {case.case_number} began executing by {caller}")
    return updated


def close_case(caller: str, case_id) -> object:
    from ggg import case_close

    case = require_case(case_id)
    updated = case_close(case)
    logger.info(f"Case {case.case_number} closed by {caller}")
    return updated
