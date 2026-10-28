import json

from core import chain
from core.db import Conflict, Fail, Forbidden, Unavailable, each, find, first, next_id, page, page_by, view
from ggg import Appointment, Calendar, Contract, Department, Mandate, Member, Proposal, Realm, Vote
from core.op import dispatch, op, ops

CHOICES = ("yes", "no", "abstain")
OPEN = ("draft", "voting")


def root():
    d = first(Department, "is_root", True)
    if d is None:
        raise Unavailable("the realm has no root department")
    return d


def realm_wide(args):
    return root()


def settings():
    realm = Realm["1"]
    return (realm.manifest_data or {}).get("governance", {}) if realm else {}


def window():
    cal = Calendar["1"]
    return cal.voting_window if cal else 7 * 86400


def new_proposal(title, user, by, department, action=None, description=""):
    policy = department.policy or {}
    p = Proposal(id=next_id(Proposal, "P"), title=title, description=description, proposer=user,
                 on_behalf_of=user if by != user else "", department=department, action=action,
                 status="voting", opens_at=chain.now(), deadline=chain.now() + window(),
                 threshold=int(settings().get("threshold", 50)),
                 quorum_percent=int(settings().get("quorum_percent", 0) if department.is_root
                                    else policy.get("quorum_percent", 0)))
    p.tally = {c: 0 for c in CHOICES}
    if not department.is_root:
        p.tally = {**p.tally, "required": int(policy.get("m", 1)), "veto": list(policy.get("veto", []))}
    return p


def file_replay(name, args, user, by, department):
    """Turn a governed call into a proposal that replays it once accepted."""
    p = new_proposal(f"{name} {json.dumps(args, sort_keys=True)[:120]}", user, by, department,
                     {"op": name, "args": args, "initiator": by})
    return {"proposal": p.id, "requires_vote": True}


def realm_scope(p):
    return p.department is None or p.department.is_root


def seated(principal, department):
    """``principal`` heads ``department`` or holds an active seat in it."""
    return principal == department.head or any(
        a.position.department and a.position.department.id == department.id for a in appointed(principal))


def eligible(p, principal):
    if realm_scope(p):
        m = Member[principal]
        return m is not None and m.status == "active"
    return seated(principal, p.department)


def count(p):
    """The tally kept as votes are cast, the number of eligible voters and whether a veto holder voted no."""
    tally = {c: int((p.tally or {}).get(c, 0)) for c in CHOICES}
    size = (Member.count_by("status", "active") if realm_scope(p)
            else len(holders(p.department) | ({p.department.head} - {""})))
    vetoes = (Vote[f"{p.id}:{v}"] for v in (p.tally or {}).get("veto", []))
    return tally, size, any(v is not None and v.choice == "no" for v in vetoes)


def outcome(p, final):
    tally, size, vetoed = count(p)
    required = (p.tally or {}).get("required")
    if vetoed:
        return "rejected"
    if required is not None:
        if tally["yes"] >= required:
            return "accepted"
        return "rejected" if final or size - tally["no"] < required else None
    if not final:
        return None
    decisive = tally["yes"] + tally["no"]
    turnout = sum(tally.values()) * 100 >= p.quorum_percent * max(size, 1)
    return "accepted" if turnout and decisive and tally["yes"] * 100 > p.threshold * decisive else "rejected"


@op("proposal.create", delegable=True, returns="proposal")
def proposal_create(c, args):
    """File a proposal: ``title``, optional ``description``, ``department`` (default the whole realm) and ``action``
    (``{op, args}``, run once accepted). Voting opens at once. Returns ``proposal``.
    Example: ``{"title": "Bike lane on Main St", "department": "infrastructure"}`` → ``P-7``."""
    department = find(Department, args["department"]) if args.get("department") else root()
    action = args.get("action")
    if action is not None and action.get("op") not in ops():
        raise Fail(f"unknown operation '{action.get('op')}'")
    p = new_proposal(args["title"], c.user, c.by, department, action, args.get("description", ""))
    return {"proposal": p.id}


@op("proposal.create", delegable=True, returns="proposal")
def proposal_withdraw(c, args):
    """Withdraw ``proposal``, a proposal of yours that is still a draft or being voted on."""
    p = find(Proposal, args["proposal"])
    if p.proposer != c.user:
        raise Forbidden(f"{p.id} is not your proposal")
    if p.status not in OPEN:
        raise Conflict(f"{p.id} is {p.status}; only an open proposal can be withdrawn")
    p.status = "withdrawn"
    return {"proposal": p.id}


@op("proposal.vote", delegable=True, returns="vote")
def proposal_vote(c, args):
    """Vote on ``proposal`` before its deadline: ``choice`` is yes, no or abstain. Returns ``vote``.
    Voting again replaces your earlier vote."""
    p = find(Proposal, args["proposal"])
    if args["choice"] not in CHOICES:
        raise Fail(f"choice must be one of {', '.join(CHOICES)}")
    if p.status != "voting" or chain.now() > p.deadline:
        raise Conflict(f"{p.id} is not open for voting")
    if not eligible(p, c.user):
        raise Forbidden(f"you are not eligible to vote on {p.id}")
    key = f"{p.id}:{c.user}"
    v = Vote[key] or Vote(id=key, proposal=p, proposal_id=p.id, voter=c.user)
    tally = p.tally or {}
    if v.choice:
        tally[v.choice] -= 1
    tally[args["choice"]] = tally.get(args["choice"], 0) + 1
    p.tally = tally
    v.choice, v.cast_by, v.cast_at = args["choice"], c.by, chain.now()
    return {"vote": v.id}


@op("proposal.manage", returns="proposal")
def proposal_open(c, args):
    """Open the draft ``proposal`` for voting, for the realm's voting window from now."""
    p = find(Proposal, args["proposal"])
    if p.status != "draft":
        raise Conflict(f"{p.id} is not a draft")
    p.status, p.opens_at, p.deadline = "voting", chain.now(), chain.now() + window()
    return {"proposal": p.id}


@op("proposal.manage", returns="proposal")
def proposal_finalize(c, args):
    """Close voting on ``proposal`` and record it as accepted or rejected. Returns ``status``.
    A realm-wide proposal waits for its deadline; a department's closes early once its outcome is certain."""
    p = find(Proposal, args["proposal"])
    if p.status != "voting":
        raise Conflict(f"{p.id} is not being voted on")
    result = outcome(p, chain.now() > p.deadline)
    if result is None:
        raise Conflict(f"{p.id} is still open until {p.deadline}")
    tally, size, _ = count(p)
    p.status, p.tally = result, {**(p.tally or {}), **tally, "eligible": size}
    return {"proposal": p.id, "status": result}


@op("proposal.manage", returns="proposal")
def proposal_execute(c, args):
    """Run the action of the accepted ``proposal``, once. Returns ``status`` (executed or failed) and ``result``."""
    p = find(Proposal, args["proposal"])
    if p.status != "accepted" or not p.action:
        raise Conflict(f"{p.id} has no accepted action to execute")
    p.status = "executed"
    out = yield from dispatch(p.action["op"], json.dumps(p.action["args"]), origin="replay",
                                 signer=p.action.get("initiator") or p.proposer, proposal=p.id)
    p.result = json.loads(out)
    if not p.result.get("ok"):
        p.status = "failed"
    return {"proposal": p.id, "status": p.status, "result": p.result}


@op("realm.data_view", read=True)
def proposal_get(c, args):
    """The proposal ``proposal`` with its current tally, the number of eligible voters and its votes, ``count`` at a
    time (default 100, at most 500) from row ``from`` (default 1); ``next`` continues the votes (null at the end)."""
    p = find(Proposal, args["proposal"])
    tally, size, _ = count(p)
    votes, nxt = page_by(Vote, "proposal_id", p.id, args)
    return {"proposal": view(p), "tally": tally, "eligible": size,
            "votes": [view(v, "voter", "choice", "cast_by") for v in votes], "next": nxt}


@op("realm.data_view", read=True, returns="proposals")
def proposal_list(c, args):
    """Proposals (id, title, status, deadline, proposer), or only those with ``status`` (e.g. voting), ``count`` at a
    time (default 100, at most 500) from row ``from`` (default 1). Returns ``proposals`` and ``next`` (null at the
    end)."""
    rows, nxt = page_by(Proposal, "status", args["status"], args) if args.get("status") else page(Proposal, args)
    return {"proposals": [view(p, "id", "title", "status", "deadline", "proposer") for p in rows], "next": nxt}


@op("mandate.create", governed=realm_wide, returns="mandate")
def mandate_create(c, args):
    """Grant a mandate: ``title``, optional ``scope`` (e.g. ``{"max_value": 200000}``), ``executor`` (a department),
    the ``proposal`` that approved it and ``expires_at`` (default 0: never). Returns ``mandate``."""
    m = Mandate(id=next_id(Mandate, "M"), title=args["title"], scope=args.get("scope", {}),
                executor=find(Department, args["executor"]) if args.get("executor") else None,
                proposal=args.get("proposal", ""), expires_at=int(args.get("expires_at", 0)))
    return {"mandate": m.id}


@op("mandate.create", governed=realm_wide)
def mandate_revoke(c, args):
    """Revoke ``mandate``: no new contract can be made under it."""
    find(Mandate, args["mandate"]).status = "revoked"


@op("mandate.assign_executor", governed=realm_wide)
def mandate_assign_executor(c, args):
    """Make the department ``executor`` the executor of ``mandate``."""
    find(Mandate, args["mandate"]).executor = find(Department, args["executor"])


@op("contract.create_under_mandate", returns="contract")
def contract_create(c, args):
    """Record an active contract: ``title``, optional ``parties``, ``terms_ref``, ``terms_hash``, ``starts_at`` (default
    now), ``ends_at``, ``value`` in ``symbol`` (default the realm currency) and ``mandate``. Returns ``contract``.
    Under a mandate, the mandate must be active, ``value`` within its ``max_value``, the contract must end before the
    mandate expires, and you must head or hold an active seat in the mandate's executor department."""
    m = find(Mandate, args["mandate"]) if args.get("mandate") else None
    value = int(args.get("value", 0))
    if m is not None:
        if m.status != "active" or (m.expires_at and chain.now() > m.expires_at):
            raise Conflict(f"mandate {m.id} is not active")
        if m.executor is None or not seated(c.user, m.executor):
            raise Forbidden(f"only the executor department's staff may contract under mandate {m.id}")
        limit = int((m.scope or {}).get("max_value", 0))
        if limit and value > limit:
            raise Fail(f"{value} exceeds mandate {m.id}'s limit of {limit}")
        if m.expires_at and int(args.get("ends_at", 0)) > m.expires_at:
            raise Fail(f"the contract outlasts mandate {m.id}")
    k = Contract(id=next_id(Contract, "C"), title=args["title"], parties=args.get("parties", []), mandate=m,
                 value=value, symbol=args.get("symbol") or Realm["1"].accounting_currency,
                 terms_ref=args.get("terms_ref", ""), terms_hash=args.get("terms_hash", ""), status="active",
                 starts_at=int(args.get("starts_at", chain.now())), ends_at=int(args.get("ends_at", 0)))
    return {"contract": k.id}


@op("contract.terminate", signing=True, returns="contract")
def contract_terminate(c, args):
    """Terminate ``contract``, unless it is already terminated or fulfilled."""
    k = find(Contract, args["contract"])
    if k.status in ("terminated", "fulfilled"):
        raise Conflict(f"{k.id} is already {k.status}")
    k.status = "terminated"
    return {"contract": k.id}


@op("governance.update", governed=realm_wide)
def governance_update(c, args):
    """Set the voting rules for new proposals, any of: ``voting_window`` (seconds), ``threshold`` (percent of yes
    among yes and no votes a realm-wide proposal must exceed) and ``quorum_percent`` (realm-wide turnout)."""
    realm, cal = Realm["1"] or Realm(id="1"), Calendar["1"] or Calendar(id="1")
    if "voting_window" in args:
        cal.voting_window = int(args["voting_window"])
    data = realm.manifest_data or {}
    data["governance"] = {**data.get("governance", {}),
                          **{k: int(args[k]) for k in ("threshold", "quorum_percent") if k in args}}
    realm.manifest_data = data


def holders(department):
    return {a.user for pos in department.positions for a in pos.appointments if a.status == "active"}


def appointed(principal):
    return [a for a in each(Appointment, "user", principal) if a.status == "active"]
