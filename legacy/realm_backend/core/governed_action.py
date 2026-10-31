"""Generic org-policy gating for governed actions (issue #262).

One shared implementation of the two-layer governance state machine used by
position_admin / payroll / treasury / sandbox_admin:

- **Direct** — the governing org's policy is 1/1 (no quorum, no veto), or the
  caller is an IC controller / trusted principal (bootstrap tooling): the
  action applies immediately.
- **Confirm** — any other policy: the first call returns
  ``requires_confirmation`` so the UI can warn the user a vote is needed.
- **Proposal** — the caller re-submits with ``confirm=True``: an org-scoped
  Proposal is created whose inline code *replays the exact same call* with
  realm authority once the vote passes.

Because the direct path and the replay path converge on the same function,
an approved vote can never do something a direct call could not, and vice
versa.

Replay authority: while :func:`execute_replay` / :func:`execute_backend_replay`
run, ``in_replay()`` is True and ``core.access._check_access`` grants every
operation — an accepted proposal acts with the realm's own authority, exactly
like any other proposal inline code (which can already mutate the DB freely).
"""

import json
from typing import Any, Dict, Optional

from ic_python_logging import get_logger

logger = get_logger("core.governed_action")

# Depth counter: >0 while replaying an approved proposal's action.
_replay_depth = 0

# Yes-share of decisive votes needed by a realm-wide ballot. Matches the voting
# extension's own default so a stored value and an unset one behave alike.
REALM_WIDE_THRESHOLD = 0.6


def in_replay() -> bool:
    """True while an approved governance proposal is replaying its action."""
    return _replay_depth > 0


# Reserved args key holding the principal that originally submitted a
# governed call. Written exclusively by the dispatch gate (which strips any
# client-supplied value first), so inside a replay it cannot be forged.
INITIATOR_KEY = "_initiator"


def effective_caller(args) -> str:
    """The principal a caller-bound action is about.

    Normally the live verified caller. During the replay of an approved
    governed proposal the live caller is just whoever triggered execution,
    so the initiator recorded by the dispatch gate at submission time is
    used instead. Fail-closed: a replay without a recorded initiator gets
    an empty string, so downstream user lookups fail rather than acting on
    the executor.
    """
    if in_replay():
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (json.JSONDecodeError, TypeError):
                args = None
        if isinstance(args, dict):
            recorded = args.get(INITIATOR_KEY)
            if isinstance(recorded, str) and recorded.strip():
                return recorded.strip()
        return ""
    from _cdk import ic

    return str(ic.caller())


def policy_is_direct(dept) -> bool:
    """True when the org's policy lets a single initiator act without a vote."""
    from core.position_admin import policy_is_direct as _impl

    return _impl(dept)


def governing_org(org_name: Optional[str] = None):
    """Resolve the governing Department: *org_name* if given, else root."""
    from ggg import ROOT_ORG_NAME, Department

    if org_name:
        dept = Department[org_name]
        if dept:
            return dept
        logger.warning(f"governing_org: '{org_name}' not found, falling back to root")
    root = Department[ROOT_ORG_NAME]
    if root:
        return root
    for dept in Department.instances():
        if getattr(dept, "is_root", False):
            return dept
    return None


def format_org_policy(dept) -> str:
    m = int(getattr(dept, "policy_threshold_m", 1) or 1)
    n = int(getattr(dept, "policy_threshold_n", 1) or 1)
    q = int(getattr(dept, "policy_quorum_percent", 0) or 0)
    veto = (getattr(dept, "policy_veto_principals", "") or "").strip()
    label = f"{m}/{n}"
    extras = []
    if q > 0:
        extras.append(f"quorum {q}%")
    if veto:
        extras.append("veto")
    if extras:
        label = f"{label} ({', '.join(extras)})"
    return label


def confirmation_payload(governing, summary: str) -> Dict[str, Any]:
    """Response for the first (unconfirmed) call to a governed action.

    ``success`` is False so legacy frontends that don't understand
    ``requires_confirmation`` show an error instead of a false success toast.
    """
    policy = format_org_policy(governing)
    return {
        "success": False,
        "requires_confirmation": True,
        "summary": summary,
        "governed_by": governing.name,
        "policy": policy.split(" (")[0],
        "governed_policy": policy,
        "error": (
            f"This action is governed by {governing.name}'s policy ({policy}); "
            f"a vote is required. Re-submit with confirm=true to open a proposal."
        ),
        "policy_reason": (
            f"This action is governed by {governing.name}'s policy ({policy}); "
            f"a vote is required before it can apply."
        ),
        "voters_org": governing.name,
    }


def _voting_window_seconds() -> int:
    window = 604_800
    try:
        from ggg import Realm

        realm = Realm[1]
        if realm and realm.calendar and realm.calendar.voting_window:
            window = int(realm.calendar.voting_window)
    except Exception:
        pass
    return window


def submit_replay_proposal(
    governing,
    summary: str,
    code_inline: str,
    proposer_principal: str,
    metadata_extra: Optional[Dict[str, Any]] = None,
    description: Optional[str] = None,
    allow_system_proposer: bool = False,
    realm_wide: bool = False,
) -> Dict[str, Any]:
    """Create a Proposal that replays a governed action on approval.

    Org-scoped by default: only ``governing``'s members vote, tallied against
    its M/N/quorum/veto policy. With ``realm_wide=True`` every member votes and
    the ballot is settled by the realm-wide threshold plus the codex quorum —
    used when the governing org cannot be the electorate (issue #295: a minted
    quarter's root department has no members, so an org-scoped ballot would have
    no eligible voters at all).
    """
    from _cdk import ic
    from ggg import Proposal, User

    on_behalf_of = None
    proposer = User[proposer_principal]
    if not proposer and allow_system_proposer:
        proposer = User["system"]
        on_behalf_of = (proposer_principal or "").strip() or None
    if not proposer:
        return {
            "success": False,
            "error": f"Caller {proposer_principal} is not a registered user; cannot propose",
        }

    proposal_num = len(Proposal.instances()) + 1
    proposal_id = f"prop_{proposal_num:03d}"

    metadata = {
        "proposal_type": "governed_action",
        "code_inline": code_inline,
        "codex_name": f"governed_action_{proposal_id}",
    }
    # A realm-wide ballot must carry no org scope in *either* place: the voting
    # extension resolves scope from the field and falls back to this metadata
    # key, so leaving it here would silently restrict voting to the org.
    if not realm_wide:
        metadata["org_scope"] = governing.name
    if metadata_extra:
        metadata.update(metadata_extra)
    if on_behalf_of:
        metadata["on_behalf_of"] = on_behalf_of

    deadline_s = ic.time() // 1_000_000_000 + _voting_window_seconds()

    if description is None:
        if realm_wide:
            description = f"Realm-wide governed action. Proposed by {proposer.id}."
        else:
            description = (
                f"Governed action under '{governing.name}' "
                f"(policy {format_org_policy(governing)}). "
                f"Proposed by {proposer.id}."
            )
        if on_behalf_of:
            description += f" On behalf of capital {on_behalf_of}."

    fields = {
        "proposal_id": proposal_id,
        "title": summary,
        "description": description,
        "code_url": "",
        "code_checksum": "",
        "proposer": proposer,
        "status": "voting",
        "voting_deadline": str(deadline_s),
        "votes_yes": 0.0,
        "votes_no": 0.0,
        "votes_abstain": 0.0,
        "total_voters": 0.0,
        # Org-scoped ballots ignore this (department policy decides), so the
        # historical 1.0 is inert there. A realm-wide ballot *is* settled by it,
        # and 1.0 would demand unanimity among decisive votes.
        "required_threshold": REALM_WIDE_THRESHOLD if realm_wide else 1.0,
        "metadata": json.dumps(metadata),
    }
    # Left unset for realm-wide so those rows stay out of the org_scope index.
    if not realm_wide:
        fields["org_scope"] = governing.name
    Proposal(**fields)

    scope_label = "realm-wide" if realm_wide else f"'{governing.name}'"
    logger.info(f"governed proposal {proposal_id} submitted for {scope_label}: {summary}")
    return {
        "success": True,
        "applied": "proposal",
        "proposal_id": proposal_id,
        "summary": summary,
        "governed_by": "" if realm_wide else governing.name,
        "governed_policy": "realm-wide" if realm_wide else format_org_policy(governing),
    }


# ---------------------------------------------------------------------------
# Replay code builders + executors
# ---------------------------------------------------------------------------


def build_extension_replay_code(ext_id: str, function_name: str, args_json: str) -> str:
    """Inline proposal code that re-runs an extension call with realm authority."""
    return (
        "from core.governed_action import execute_replay\n"
        "\n"
        "def main():\n"
        f"    return execute_replay({ext_id!r}, {function_name!r}, {args_json!r})\n"
    )


def build_backend_replay_code(
    module_path: str, function_name: str, payload_json: str
) -> str:
    """Inline proposal code that re-runs a backend core function with realm authority."""
    return (
        "from core.governed_action import execute_backend_replay\n"
        "\n"
        "def main():\n"
        f"    return execute_backend_replay({module_path!r}, {function_name!r}, {payload_json!r})\n"
    )


def _check_replay_result(result: Any) -> Any:
    """Raise if a replayed call reports failure, so the proposal is marked failed."""
    parsed = result
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            parsed = None
    if isinstance(parsed, dict):
        if parsed.get("success") is False or parsed.get("error"):
            raise RuntimeError(
                f"Governed action failed on replay: {parsed.get('error') or parsed}"
            )
    return result


def execute_replay(ext_id: str, function_name: str, args_json: str) -> Any:
    """Replay an extension call with realm authority (proposal execution only)."""
    global _replay_depth
    from core.extensions import call_extension_function

    _replay_depth += 1
    try:
        result = call_extension_function(ext_id, function_name, args_json)
        if hasattr(result, "__next__"):
            # Async extension function: hand the generator to the proposal
            # executor (it yields from main()'s return value). The final
            # result cannot be checked here.
            return result
        return _check_replay_result(result)
    finally:
        _replay_depth -= 1


def execute_backend_replay(
    module_path: str, function_name: str, payload_json: str
) -> Any:
    """Replay a backend core function with realm authority (proposal execution only)."""
    global _replay_depth
    import importlib

    _replay_depth += 1
    try:
        module = importlib.import_module(module_path)
        func = getattr(module, function_name)
        payload = json.loads(payload_json)
        result = func(payload)
        if hasattr(result, "__next__"):
            return result
        return _check_replay_result(result)
    finally:
        _replay_depth -= 1


# ---------------------------------------------------------------------------
# The generic gate
# ---------------------------------------------------------------------------


def gate(
    *,
    caller: str,
    summary: str,
    replay_code: str,
    org_name: Optional[str] = None,
    confirm: bool = False,
    metadata_extra: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Run the governed-action state machine.

    Returns None when the caller may proceed directly (1/1 policy, controller,
    trusted principal, or already inside an approved replay). Otherwise
    returns the response dict the endpoint must hand back to the caller
    (requires_confirmation payload or proposal-created payload).
    """
    if in_replay():
        return None

    from core.access import _is_controller_or_trusted

    if _is_controller_or_trusted(caller):
        return None

    governing = governing_org(org_name)
    if governing is None or policy_is_direct(governing):
        return None

    if not confirm:
        return confirmation_payload(governing, summary)

    return submit_replay_proposal(
        governing, summary, replay_code, caller, metadata_extra
    )
