"""Federal vote GOS runtime — federation transport + entity drivers (issue #300).

Wires pure helpers from ``core.federal_tally`` and entities from
``ggg.governance.federal_vote`` into federation topics, recurring tasks,
and realm endpoints. Capital originates votes; quarters mirror and ballot;
capital aggregates and broadcasts results; quarters execute adopted actions.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from ic_python_logging import get_logger

# Bind the module object only. A `from core.federal_tally import aggregate`
# during this file's Basilisk `_bload` fails on the canister: LazyMod returns
# early while still loading, so the name is missing (unknown location).
import core.federal_tally as _tally

try:
    from ggg.governance.federal_vote import (
        LEG_STATUS_ARMED,
        LEG_STATUS_EXECUTED,
        LEG_STATUS_FAILED,
        LEG_STATUS_OPEN,
        LEG_STATUS_REPORTED,
        LEG_STATUS_EXPIRED,
        VOTE_STATUS_ADOPTED,
        VOTE_STATUS_EXPIRED,
        VOTE_STATUS_NO_QUORUM,
        VOTE_STATUS_OPEN,
        VOTE_STATUS_REJECTED,
    )
except ImportError:
    try:
        from realm_backend.ggg.governance.federal_vote import (
            LEG_STATUS_ARMED,
            LEG_STATUS_EXECUTED,
            LEG_STATUS_FAILED,
            LEG_STATUS_OPEN,
            LEG_STATUS_REPORTED,
            LEG_STATUS_EXPIRED,
            VOTE_STATUS_ADOPTED,
            VOTE_STATUS_EXPIRED,
            VOTE_STATUS_NO_QUORUM,
            VOTE_STATUS_OPEN,
            VOTE_STATUS_REJECTED,
        )
    except ImportError:
        VOTE_STATUS_OPEN = "open"
        VOTE_STATUS_ADOPTED = "adopted"
        VOTE_STATUS_REJECTED = "rejected"
        VOTE_STATUS_NO_QUORUM = "no_quorum"
        VOTE_STATUS_EXPIRED = "expired"
        LEG_STATUS_OPEN = "open"
        LEG_STATUS_REPORTED = "reported"
        LEG_STATUS_ARMED = "armed"
        LEG_STATUS_EXECUTED = "executed"
        LEG_STATUS_FAILED = "failed"
        LEG_STATUS_EXPIRED = "expired"

logger = get_logger("core.federal_vote_runtime")

PROPOSAL_METADATA_MAX_LENGTH = 4096

LEG_TASK_NAME = "federal_vote_leg"
AGGREGATE_TASK_NAME = "federal_vote_aggregate"
TASK_INTERVAL_S = 15

LEG_STEP_CODE = (
    "def async_task():\n"
    "    from core.federal_vote_runtime import advance_federal_legs\n"
    "    res = yield from advance_federal_legs()\n"
    "    return res\n"
)
AGGREGATE_STEP_CODE = (
    "def async_task():\n"
    "    from core.federal_vote_runtime import advance_federal_aggregate\n"
    "    res = yield from advance_federal_aggregate()\n"
    "    return res\n"
)

_TERMINAL_PROPOSAL_STATUSES = frozenset(
    {"accepted", "executed", "rejected", "failed", "no_quorum"}
)
_VOTING_PROPOSAL_STATUSES = frozenset({"voting", "pending_vote", "pending_review"})

_propose_counter = 0


def _import_ggg():
    try:
        from ggg import FederalVote, FederalVoteLeg, Proposal, Quarter, Realm
    except ImportError:
        from realm_backend.ggg import FederalVote, FederalVoteLeg, Proposal, Quarter, Realm
    return FederalVote, FederalVoteLeg, Proposal, Quarter, Realm


def leg_key(vote_id: str, quarter_id: str) -> str:
    return f"{vote_id}:{quarter_id}"[:130]


def now_epoch_s() -> int:
    from _cdk import ic

    return ic.time() // 1_000_000_000


def _self_id() -> str:
    from _cdk import ic

    return ic.id().to_str()


def _load_realm():
    *_, Realm = _import_ggg()
    return Realm.load("1")


def is_capital(realm=None) -> bool:
    realm = realm or _load_realm()
    if not realm:
        return True
    if bool(getattr(realm, "is_capital", False)):
        return True
    if not bool(getattr(realm, "is_quarter", False)):
        return True
    fed = (getattr(realm, "federation_realm_id", "") or "").strip()
    if not fed:
        return True
    return fed == _self_id()


def capital_id(realm=None) -> str:
    realm = realm or _load_realm()
    if not realm:
        return _self_id()
    if is_capital(realm):
        return _self_id()
    return (getattr(realm, "federation_realm_id", "") or "").strip()


def known_quarter_ids() -> List[str]:
    *_, Quarter, _ = _import_ggg()
    ids = [_self_id()]
    try:
        for q in Quarter.instances():
            cid = (getattr(q, "canister_id", "") or "").strip()
            if cid:
                ids.append(cid)
    except Exception as e:
        logger.error(f"known_quarter_ids: {e}")
    return sorted(set(ids))


def load_federal_params() -> dict:
    from core.codex_hooks import call_hook

    raw = call_hook("get_federal_governance_params", {})
    params = raw if isinstance(raw, dict) else {}
    return _tally.resolve_rule(params)


def upsert_vote(**fields):
    FederalVote, *_rest = _import_ggg()
    vote_id = (fields.get("vote_id") or "").strip()
    if not vote_id:
        raise ValueError("vote_id is required")
    vote = FederalVote[vote_id]
    if vote:
        for key, value in fields.items():
            setattr(vote, key, value)
        return vote
    return FederalVote(**fields)


def upsert_leg(**fields):
    _FederalVote, FederalVoteLeg, *_rest = _import_ggg()
    key = (fields.get("leg_key") or "").strip()
    if not key:
        raise ValueError("leg_key is required")
    leg = FederalVoteLeg[key]
    if leg:
        for k, value in fields.items():
            setattr(leg, k, value)
        return leg
    return FederalVoteLeg(**fields)


def build_leg_code_inline(action: dict) -> str:
    from core.governed_action import build_backend_replay_code, build_extension_replay_code

    args_json = json.dumps(action.get("args") or {}, separators=(",", ":"))
    if "extension" in action:
        return build_extension_replay_code(
            action["extension"], action["function"], args_json
        )
    return build_backend_replay_code(
        action["module"], action["function"], args_json
    )


def build_leg_proposal_pkg(
    vote_id: str, action: dict, vote_hash: str, deadline: int
) -> dict:
    code_inline = build_leg_code_inline(action)
    summary = f"Federal vote {vote_id}: {action.get('function', 'action')}"
    description = (
        f"Realm-wide federal vote ballot.\n\n"
        f"Vote id: {vote_id}\n"
        f"Deadline: {deadline}\n"
        f"Hash: {vote_hash}\n"
        f"Action: {json.dumps(action, separators=(',', ':'))}"
    )
  # submit_replay_proposal always adds codex_name; size-check with a conservative
  # placeholder so oversized ballots fail before proposal creation.
    metadata = {
        "proposal_type": "governed_action",
        "code_inline": code_inline,
        "codex_name": "governed_action_prop_999",
        "defer_execution": True,
        "federal_vote_id": vote_id,
        "vote_hash": vote_hash,
        "federal_scope": "leg",
    }
    serialized = json.dumps(metadata, separators=(",", ":"))
    if len(serialized) > PROPOSAL_METADATA_MAX_LENGTH:
        raise ValueError(
            f"Federal leg proposal metadata ({len(serialized)} chars) exceeds "
            f"Proposal.metadata limit ({PROPOSAL_METADATA_MAX_LENGTH}); "
            f"refusing to truncate"
        )
    return {
        "summary": summary,
        "description": description,
        "code_inline": code_inline,
    }


def _local_legs():
    _FederalVote, FederalVoteLeg, *_rest = _import_ggg()
    self_id = _self_id()
    return [
        leg
        for leg in FederalVoteLeg.instances()
        if (getattr(leg, "quarter_canister_id", "") or "").strip() == self_id
    ]


def _leg_for_quarter(vote_id: str, quarter_id: str):
    _FederalVote, FederalVoteLeg, *_rest = _import_ggg()
    return FederalVoteLeg[leg_key(vote_id, quarter_id)]


def _serialize_leg(leg) -> dict:
    return {
        "quarter_canister_id": getattr(leg, "quarter_canister_id", ""),
        "proposal_id": getattr(leg, "proposal_id", ""),
        "outcome": getattr(leg, "outcome", ""),
        "votes_yes": int(getattr(leg, "votes_yes", 0) or 0),
        "votes_no": int(getattr(leg, "votes_no", 0) or 0),
        "votes_abstain": int(getattr(leg, "votes_abstain", 0) or 0),
        "eligible": int(getattr(leg, "eligible", 0) or 0),
        "reported": bool(getattr(leg, "reported", False)),
        "status": getattr(leg, "status", ""),
        "error": getattr(leg, "error", ""),
    }


def _legs_for_vote(vote_id: str):
    _FederalVote, FederalVoteLeg, *_rest = _import_ggg()
    vote_id = (vote_id or "").strip()
    try:
        if hasattr(FederalVoteLeg, "find_by"):
            batch, _ = FederalVoteLeg.find_by("vote_id", vote_id)
            return list(batch)
    except Exception:
        pass
    return [
        leg
        for leg in FederalVoteLeg.instances()
        if (getattr(leg, "vote_id", "") or "").strip() == vote_id
    ]


def _open_votes():
    FederalVote, *_rest = _import_ggg()
    try:
        if hasattr(FederalVote, "find_by"):
            batch, _ = FederalVote.find_by("status", VOTE_STATUS_OPEN)
            return list(batch)
    except Exception:
        pass
    return [
        v
        for v in FederalVote.instances()
        if (getattr(v, "status", "") or "").strip() == VOTE_STATUS_OPEN
    ]


def _vote_view(vote) -> dict:
    rule = {}
    try:
        rule = json.loads(getattr(vote, "rule_json", "") or "{}")
    except (json.JSONDecodeError, TypeError):
        pass
    action = {}
    try:
        action = json.loads(getattr(vote, "action", "") or "{}")
    except (json.JSONDecodeError, TypeError):
        pass
    tally = {}
    try:
        tally = json.loads(getattr(vote, "tally_json", "") or "{}")
    except (json.JSONDecodeError, TypeError):
        pass
    vote_id = getattr(vote, "vote_id", "")
    legs = [_serialize_leg(leg) for leg in _legs_for_vote(vote_id)]
    local = _leg_for_quarter(vote_id, _self_id())
    return {
        "vote_id": vote_id,
        "origin_quarter": getattr(vote, "origin_quarter", ""),
        "action": action,
        "rule": rule,
        "vote_hash": getattr(vote, "vote_hash", ""),
        "deadline": int(getattr(vote, "deadline", 0) or 0),
        "status": getattr(vote, "status", ""),
        "tally": tally,
        "known_quarters": int(getattr(vote, "known_quarters", 0) or 0),
        "legs": legs,
        "local_leg": _serialize_leg(local) if local else None,
    }


def get_vote_view(vote_id: str) -> Optional[dict]:
    FederalVote, *_rest = _import_ggg()
    vote = FederalVote[(vote_id or "").strip()]
    if not vote:
        return None
    return _vote_view(vote)


def list_votes(status: Optional[str] = None) -> List[dict]:
    FederalVote, *_rest = _import_ggg()
    status = (status or "").strip()
    votes = list(FederalVote.instances())
    if status:
        votes = [v for v in votes if (getattr(v, "status", "") or "").strip() == status]
    return [_vote_view(v) for v in votes]


def latest_leg_for_directory() -> Optional[dict]:
    _FederalVote, FederalVoteLeg, *_rest = _import_ggg()
    legs = _local_legs()
    if not legs:
        return None

    def _priority(leg):
        status = (getattr(leg, "status", "") or "").strip()
        if status in (LEG_STATUS_OPEN, LEG_STATUS_ARMED, LEG_STATUS_REPORTED):
            return 0
        return 1

    legs.sort(key=lambda leg: (_priority(leg), getattr(leg, "vote_id", "")))
    leg = legs[-1]
    return {
        "vote_id": getattr(leg, "vote_id", ""),
        "status": getattr(leg, "status", ""),
        "outcome": getattr(leg, "outcome", ""),
    }


def seed_federal_tasks():
    from core.quarter_bootstrap import seed_recurring_codex_task

    seed_recurring_codex_task(LEG_TASK_NAME, LEG_STEP_CODE, TASK_INTERVAL_S)
    if is_capital():
        seed_recurring_codex_task(
            AGGREGATE_TASK_NAME, AGGREGATE_STEP_CODE, TASK_INTERVAL_S
        )


def _maybe_disable_leg_task():
    from core.quarter_bootstrap import disable_recurring_task

    active = [
        leg
        for leg in _local_legs()
        if (getattr(leg, "status", "") or "").strip()
        in (LEG_STATUS_OPEN, LEG_STATUS_ARMED)
    ]
    if not active:
        disable_recurring_task(LEG_TASK_NAME)


def _maybe_disable_aggregate_task():
    from core.quarter_bootstrap import disable_recurring_task

    if not _open_votes():
        disable_recurring_task(AGGREGATE_TASK_NAME)


def open_local_leg(vote, action: dict, vote_hash: str, deadline: int) -> dict:
    from core.governed_action import submit_replay_proposal

    vote_id = getattr(vote, "vote_id", "")
    pkg = build_leg_proposal_pkg(vote_id, action, vote_hash, deadline)
    capital_or_system = capital_id() or _self_id()
    result = submit_replay_proposal(
        None,
        pkg["summary"],
        pkg["code_inline"],
        capital_or_system,
        metadata_extra={
            "defer_execution": True,
            "federal_vote_id": vote_id,
            "vote_hash": vote_hash,
            "federal_scope": "leg",
        },
        description=pkg["description"],
        allow_system_proposer=True,
        realm_wide=True,
    )
    if not result.get("success"):
        return result
    proposal_id = (result.get("proposal_id") or "").strip()
    upsert_leg(
        leg_key=leg_key(vote_id, _self_id()),
        vote_id=vote_id,
        quarter_canister_id=_self_id(),
        proposal_id=proposal_id,
        vote_hash=vote_hash,
        status=LEG_STATUS_OPEN,
    )
    return result


def handle_propose(source: str, body: dict) -> Dict[str, Any]:
    if not is_capital():
        return {"success": False, "error": "only the federation capital may propose"}

    FederalVote, *_rest = _import_ggg()

    body = body or {}
    vote_id = (body.get("vote_id") or "").strip()
    if vote_id:
        existing = FederalVote[vote_id]
        if existing:
            if body.get("action") is not None:
                retry_action, err = _tally.validate_action(body.get("action"))
                if retry_action is None:
                    return {"success": False, "error": err}
                try:
                    stored_rule = json.loads(getattr(existing, "rule_json", "") or "{}")
                except (json.JSONDecodeError, TypeError):
                    stored_rule = {}
                stored_deadline = int(getattr(existing, "deadline", 0) or 0)
                expected = _tally.compute_vote_hash(
                    retry_action, stored_rule, stored_deadline
                )
                if expected != (getattr(existing, "vote_hash", "") or "").strip():
                    return {"success": False, "error": "vote_hash mismatch"}
            return {
                "success": True,
                "vote_id": existing.vote_id,
                "vote_hash": existing.vote_hash,
                "deadline": int(existing.deadline or 0),
            }

    action, err = _tally.validate_action(body.get("action"))
    if action is None:
        return {"success": False, "error": err}

    rule = load_federal_params()
    now = now_epoch_s()
    deadline = _tally.compute_deadline(now, rule)
    global _propose_counter
    _propose_counter += 1
    if not vote_id:
        vote_id = _tally.build_vote_id(source, now, _propose_counter)
    vote_hash = _tally.compute_vote_hash(action, rule, deadline)
    quarters = known_quarter_ids()

    upsert_vote(
        vote_id=vote_id,
        origin_quarter=(source or "").strip()[:64],
        action=json.dumps(action, sort_keys=True, separators=(",", ":")),
        rule_json=json.dumps(rule, separators=(",", ":")),
        vote_hash=vote_hash,
        deadline=deadline,
        status=VOTE_STATUS_OPEN,
        known_quarters=len(quarters),
    )

    for qid in quarters:
        upsert_leg(
            leg_key=leg_key(vote_id, qid),
            vote_id=vote_id,
            quarter_canister_id=qid,
            vote_hash=vote_hash,
            status=LEG_STATUS_OPEN,
        )

    vote = FederalVote[vote_id]
    leg_result = open_local_leg(vote, action, vote_hash, deadline)
    if not leg_result.get("success"):
        return leg_result

    seed_federal_tasks()
    return {
        "success": True,
        "vote_id": vote_id,
        "vote_hash": vote_hash,
        "deadline": deadline,
    }


def handle_open(source: str, body: dict) -> Dict[str, Any]:
    if is_capital():
        return {"success": False, "error": "capital opens its own leg in-process"}

    cap = capital_id()
    if (source or "").strip() != cap:
        return {"success": False, "error": "only the federation capital may open votes"}

    FederalVote, *_ = _import_ggg()
    body = body or {}
    vote_id = (body.get("vote_id") or "").strip()
    if not vote_id:
        return {"success": False, "error": "vote_id is required"}

    existing = FederalVote[vote_id]
    action, err = _tally.validate_action(body.get("action"))
    if action is None:
        return {"success": False, "error": err}

    rule = body.get("rule") or {}
    if not isinstance(rule, dict):
        return {"success": False, "error": "rule must be a dict"}
    deadline = int(body.get("deadline") or 0)
    vote_hash = (body.get("vote_hash") or "").strip()
    expected = _tally.compute_vote_hash(action, rule, deadline)
    if vote_hash != expected:
        return {"success": False, "error": "vote_hash mismatch"}

    if existing:
        if vote_hash != (getattr(existing, "vote_hash", "") or "").strip():
            return {"success": False, "error": "vote_hash mismatch"}
        local = _leg_for_quarter(vote_id, _self_id())
        return {
            "success": True,
            "proposal_id": (getattr(local, "proposal_id", "") or "") if local else "",
        }

    upsert_vote(
        vote_id=vote_id,
        origin_quarter=cap[:64],
        action=json.dumps(action, sort_keys=True, separators=(",", ":")),
        rule_json=json.dumps(rule, separators=(",", ":")),
        vote_hash=vote_hash,
        deadline=deadline,
        status=VOTE_STATUS_OPEN,
        known_quarters=int(body.get("known_quarters") or len(known_quarter_ids())),
    )
    upsert_leg(
        leg_key=leg_key(vote_id, _self_id()),
        vote_id=vote_id,
        quarter_canister_id=_self_id(),
        vote_hash=vote_hash,
        status=LEG_STATUS_OPEN,
    )

    vote = FederalVote[vote_id]
    leg_result = open_local_leg(vote, action, vote_hash, deadline)
    if not leg_result.get("success"):
        return leg_result

    from core.quarter_bootstrap import seed_recurring_codex_task

    seed_recurring_codex_task(LEG_TASK_NAME, LEG_STEP_CODE, TASK_INTERVAL_S)
    return {"success": True, "proposal_id": leg_result.get("proposal_id", "")}


def handle_tally(source: str, body: dict) -> Dict[str, Any]:
    if not is_capital():
        return {"success": False, "error": "only the capital records leg tallies"}

    source = (source or "").strip()
    if source not in known_quarter_ids():
        return {"success": False, "error": "unknown quarter source"}

    body = body or {}
    vote_id = (body.get("vote_id") or "").strip()
    if not vote_id:
        return {"success": False, "error": "vote_id is required"}

    upsert_leg(
        leg_key=leg_key(vote_id, source),
        vote_id=vote_id,
        quarter_canister_id=source,
        proposal_id=(body.get("proposal_id") or "")[:64],
        outcome=(body.get("outcome") or "")[:32],
        votes_yes=int(body.get("yes") or body.get("votes_yes") or 0),
        votes_no=int(body.get("no") or body.get("votes_no") or 0),
        votes_abstain=int(body.get("abstain") or body.get("votes_abstain") or 0),
        eligible=int(body.get("eligible") or 0),
        reported=True,
        status=LEG_STATUS_REPORTED,
    )
    return {"success": True}


def handle_result(source: str, body: dict) -> Dict[str, Any]:
    cap = capital_id()
    if (source or "").strip() != cap:
        return {"success": False, "error": "only the capital may publish results"}

    FederalVote, *_ = _import_ggg()
    body = body or {}
    vote_id = (body.get("vote_id") or "").strip()
    if not vote_id:
        return {"success": False, "error": "vote_id is required"}

    local = _leg_for_quarter(vote_id, _self_id())
    if not local:
        return {"success": False, "error": "no local leg for vote"}

    ok, err = _tally.verify_result(
        getattr(local, "vote_hash", ""), (body.get("vote_hash") or "").strip()
    )
    if not ok:
        logger.error(f"handle_result hash guard failed for {vote_id}: {err}")
        return {"success": False, "error": err}

    vote = FederalVote[vote_id]
    if not vote:
        return {"success": False, "error": "vote not found"}

    status = (body.get("status") or "").strip()
    tally = body.get("tally") or body.get("tally_json") or {}
    if isinstance(tally, str):
        try:
            tally = json.loads(tally)
        except (json.JSONDecodeError, TypeError):
            tally = {}
    vote.status = status[:32]
    vote.tally_json = json.dumps(tally, separators=(",", ":"))[:2048]

    if status == VOTE_STATUS_ADOPTED:
        local.status = LEG_STATUS_ARMED
    elif status == VOTE_STATUS_EXPIRED:
        local.status = LEG_STATUS_EXPIRED
    else:
        local.status = LEG_STATUS_REPORTED

    return {"success": True}


def handle_executed(source: str, body: dict) -> Dict[str, Any]:
    if not is_capital():
        return {"success": False, "error": "only the capital records execution receipts"}

    source = (source or "").strip()
    if source not in known_quarter_ids():
        return {"success": False, "error": "unknown quarter source"}

    body = body or {}
    vote_id = (body.get("vote_id") or "").strip()
    if not vote_id:
        return {"success": False, "error": "vote_id is required"}

    leg = _leg_for_quarter(vote_id, source)
    if not leg:
        return {"success": False, "error": "leg not found"}

    err = (body.get("error") or "").strip()
    if err:
        leg.status = LEG_STATUS_FAILED
        leg.error = err[:256]
    elif body.get("success") is False:
        leg.status = LEG_STATUS_FAILED
        leg.error = (err or "execution failed")[:256]
    else:
        leg.status = LEG_STATUS_EXECUTED
        leg.error = ""

    return {"success": True}


def handle_federal_topic(topic: str, source: str, body: dict) -> Dict[str, Any]:
    handlers = {
        "gos.federal.propose": handle_propose,
        "gos.federal.open": handle_open,
        "gos.federal.tally": handle_tally,
        "gos.federal.result": handle_result,
        "gos.federal.executed": handle_executed,
    }
    handler = handlers.get(topic)
    if handler is None:
        return {"success": False, "error": f"unknown topic {topic}"}
    return handler(source, body or {})


def dispatch_federal_propose(payload):
    payload = payload if isinstance(payload, dict) else {}
    if is_capital():
        return handle_propose(_self_id(), payload)
    cap = capital_id()
    if not cap:
        return {"success": False, "error": "no federation capital configured"}
    from core.federation import send_federation_message

    return (yield from send_federation_message(cap, "gos.federal.propose", payload))


def cancel_federal_vote(vote_id: str):
    if not is_capital():
        return {"success": False, "error": "only the federation capital may cancel"}

    FederalVote, *_rest = _import_ggg()
    vote_id = (vote_id or "").strip()
    vote = FederalVote[vote_id]
    if not vote:
        return {"success": False, "error": "vote not found"}
    if (getattr(vote, "status", "") or "").strip() != VOTE_STATUS_OPEN:
        return {"success": False, "error": "vote is not open"}
    now = now_epoch_s()
    if now >= int(getattr(vote, "deadline", 0) or 0):
        return {"success": False, "error": "vote deadline has passed"}
    vote.status = VOTE_STATUS_EXPIRED

    result_body = {
        "vote_id": vote_id,
        "vote_hash": getattr(vote, "vote_hash", ""),
        "status": VOTE_STATUS_EXPIRED,
        "tally": {},
    }
    quarters = known_quarter_ids()
    self_id = _self_id()
    cap = capital_id() or self_id
    for qid in quarters:
        if qid == self_id:
            handle_result(cap, result_body)
        else:
            msg_id = f"fv-result:{vote_id}:{qid}"
            from core.federation import send_federation_message

            yield from send_federation_message(
                qid, "gos.federal.result", result_body, msg_id
            )

    return {"success": True, "vote_id": vote_id, "status": VOTE_STATUS_EXPIRED}


def _send_tally(vote_id: str, leg, outcome: str, proposal) -> Dict[str, Any]:
    cap = capital_id()
    body = {
        "vote_id": vote_id,
        "outcome": outcome,
        "yes": int(getattr(proposal, "votes_yes", 0) or 0),
        "no": int(getattr(proposal, "votes_no", 0) or 0),
        "abstain": int(getattr(proposal, "votes_abstain", 0) or 0),
        "eligible": int(getattr(proposal, "total_voters", 0) or 0),
        "proposal_id": getattr(proposal, "proposal_id", ""),
    }
    msg_id = f"fv-tally:{vote_id}:{_self_id()}"
    if is_capital():
        return handle_tally(_self_id(), body)
    from core.federation import send_federation_message

    return (yield from send_federation_message(cap, "gos.federal.tally", body, msg_id))


def _send_executed(vote_id: str, error: str = "") -> Dict[str, Any]:
    cap = capital_id()
    body = {"vote_id": vote_id, "error": (error or "")[:256]}
    if error:
        body["success"] = False
    msg_id = f"fv-executed:{vote_id}:{_self_id()}"
    if is_capital():
        return handle_executed(_self_id(), body)
    from core.federation import send_federation_message

    return (yield from send_federation_message(cap, "gos.federal.executed", body, msg_id))


def advance_federal_legs():
    FederalVote, _FederalVoteLeg, Proposal, *_rest = _import_ggg()

    now = now_epoch_s()
    processed = 0
    tallied = 0
    executed = 0

    for leg in _local_legs():
        status = (getattr(leg, "status", "") or "").strip()
        vote_id = getattr(leg, "vote_id", "")
        vote = FederalVote[vote_id]
        if not vote:
            continue

        if status == LEG_STATUS_OPEN:
            proposal_id = (getattr(leg, "proposal_id", "") or "").strip()
            proposal = Proposal[proposal_id] if proposal_id else None
            if not proposal:
                continue
            prop_status = (getattr(proposal, "status", "") or "").strip().lower()
            deadline = int(getattr(vote, "deadline", 0) or 0)
            terminal = prop_status in _TERMINAL_PROPOSAL_STATUSES
            past_deadline = _tally.is_past(now, deadline, 0)
            if not terminal and not past_deadline:
                continue

            if prop_status in _VOTING_PROPOSAL_STATUSES and past_deadline:
                import api.extensions

                api.extensions.extension_sync_call(
                    "voting",
                    "finalize_proposal",
                    json.dumps({"proposal_id": proposal_id}),
                )
                proposal = Proposal[proposal_id]
                prop_status = (getattr(proposal, "status", "") or "").strip().lower()

            outcome = _tally.classify_leg_outcome(prop_status)
            if outcome:
                yield from _send_tally(vote_id, leg, outcome, proposal)
                tallied += 1
                leg.outcome = outcome
            leg.status = LEG_STATUS_REPORTED
            leg.reported = True
            processed += 1

        elif status == LEG_STATUS_ARMED:
            if (getattr(leg, "vote_hash", "") or "").strip() != (
                getattr(vote, "vote_hash", "") or ""
            ).strip():
                logger.error(f"refusing execution of {vote_id}: vote_hash mismatch")
                leg.status = LEG_STATUS_FAILED
                leg.error = "vote_hash mismatch"[:256]
                continue

            try:
                action = json.loads(getattr(vote, "action", "") or "{}")
            except (json.JSONDecodeError, TypeError):
                leg.status = LEG_STATUS_FAILED
                leg.error = "invalid action json"[:256]
                continue

            action, err = _tally.validate_action(action)
            if action is None:
                leg.status = LEG_STATUS_FAILED
                leg.error = (err or "invalid action")[:256]
                continue

            try:
                rule = json.loads(getattr(vote, "rule_json", "") or "{}")
            except (json.JSONDecodeError, TypeError):
                leg.status = LEG_STATUS_FAILED
                leg.error = "invalid rule json"[:256]
                continue

            deadline = int(getattr(vote, "deadline", 0) or 0)
            expected = _tally.compute_vote_hash(action, rule, deadline)
            if expected != (getattr(vote, "vote_hash", "") or "").strip():
                logger.error(
                    f"refusing execution of {vote_id}: recomputed vote_hash mismatch"
                )
                leg.status = LEG_STATUS_FAILED
                leg.error = "vote_hash mismatch"[:256]
                continue

            proposal_id = (getattr(leg, "proposal_id", "") or "").strip()
            if not proposal_id:
                leg.status = LEG_STATUS_FAILED
                leg.error = "missing proposal_id"[:256]
                continue

            code_inline = build_leg_code_inline(action)

            from core.proposal_execution import execute_proposal_code

            try:
                yield from execute_proposal_code(proposal_id, code_inline, [])
                leg.status = LEG_STATUS_EXECUTED
                leg.error = ""
                executed += 1
                yield from _send_executed(vote_id)
            except Exception as e:
                leg.status = LEG_STATUS_FAILED
                leg.error = str(e)[:256]
                yield from _send_executed(vote_id, str(e))

    _maybe_disable_leg_task()
    return {
        "success": True,
        "processed": processed,
        "tallied": tallied,
        "executed": executed,
    }


def advance_federal_aggregate():
    if not is_capital():
        return {"success": True, "skipped": "not capital"}

    FederalVote, FederalVoteLeg, *_rest = _import_ggg()

    now = now_epoch_s()
    opened = 0
    finalized = 0
    self_id = _self_id()

    for vote in _open_votes():
        vote_id = getattr(vote, "vote_id", "")
        rule = {}
        try:
            rule = json.loads(getattr(vote, "rule_json", "") or "{}")
        except (json.JSONDecodeError, TypeError):
            rule = load_federal_params()
        action = {}
        try:
            action = json.loads(getattr(vote, "action", "") or "{}")
        except (json.JSONDecodeError, TypeError):
            action = {}
        deadline = int(getattr(vote, "deadline", 0) or 0)
        quarters = known_quarter_ids()

        for qid in quarters:
            if qid == self_id:
                continue
            leg = FederalVoteLeg[leg_key(vote_id, qid)]
            if not leg:
                continue
            if (getattr(leg, "status", "") or "").strip() != LEG_STATUS_OPEN:
                continue
            if (getattr(leg, "proposal_id", "") or "").strip():
                continue
            open_body = {
                "vote_id": vote_id,
                "action": action,
                "rule": rule,
                "deadline": deadline,
                "vote_hash": getattr(vote, "vote_hash", ""),
                "known_quarters": len(quarters),
            }
            msg_id = f"fv-open:{vote_id}:{qid}"
            from core.federation import send_federation_message

            yield from send_federation_message(
                qid, "gos.federal.open", open_body, msg_id
            )
            opened += 1

        legs_payload = []
        all_reported = True
        for qid in quarters:
            leg = FederalVoteLeg[leg_key(vote_id, qid)]
            if not leg:
                all_reported = False
                continue
            legs_payload.append(
                {
                    "reported": bool(getattr(leg, "reported", False)),
                    "outcome": getattr(leg, "outcome", ""),
                    "yes": int(getattr(leg, "votes_yes", 0) or 0),
                    "no": int(getattr(leg, "votes_no", 0) or 0),
                    "abstain": int(getattr(leg, "votes_abstain", 0) or 0),
                    "eligible": int(getattr(leg, "eligible", 0) or 0),
                }
            )
            if not getattr(leg, "reported", False):
                all_reported = False

        grace = int(rule.get("grace_hours") or 0)
        if not all_reported and not _tally.is_past(now, deadline, grace):
            continue

        tally = _tally.aggregate(legs_payload, len(quarters), rule)
        vote.status = (tally.get("status") or "")[:32]
        vote.tally_json = json.dumps(tally, separators=(",", ":"))[:2048]
        finalized += 1

        result_body = {
            "vote_id": vote_id,
            "vote_hash": getattr(vote, "vote_hash", ""),
            "status": vote.status,
            "tally": tally,
        }
        for qid in quarters:
            if qid == self_id:
                handle_result(capital_id(), result_body)
            else:
                msg_id = f"fv-result:{vote_id}:{qid}"
                from core.federation import send_federation_message

                yield from send_federation_message(
                    qid, "gos.federal.result", result_body, msg_id
                )

    _maybe_disable_aggregate_task()
    return {"success": True, "opened": opened, "finalized": finalized}
