import json

from core import chain
from core.db import Fail, each, find, next_id, view
from ggg import Appointment, Calendar, Contract, Department, Mandate, Member, Proposal, Realm, Vote
from core.op import dispatch, op, ops

CHOICES = ("yes", "no", "abstain")
OPEN = ("draft", "voting")


def root():
    for d in each(Department):
        if d.is_root:
            return d
    raise Fail("the realm has no root department")


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
    if not department.is_root:
        p.tally = {"required": int(policy.get("m", 1)), "veto": list(policy.get("veto", []))}
    return p


def file_replay(name, args, user, by, department):
    """Turn a governed call into a proposal that replays it once accepted."""
    p = new_proposal(f"{name} {json.dumps(args, sort_keys=True)[:120]}", user, by, department,
                     {"op": name, "args": args, "initiator": by})
    return {"proposal": p.id, "requires_vote": True}


def eligible(p):
    if p.department is None or p.department.is_root:
        return {m.id for m in each(Member) if m.status == "active"}
    seats = {a.user for pos in p.department.positions for a in pos.appointments if a.status == "active"}
    return seats | ({p.department.head} - {""})


def count(p):
    voters = eligible(p)
    tally = {c: 0 for c in CHOICES}
    vetoed = False
    for v in p.votes:
        if v.voter in voters:
            tally[v.choice] += 1
            vetoed |= v.choice == "no" and v.voter in (p.tally or {}).get("veto", [])
    return tally, len(voters), vetoed


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


@op("proposal.create", delegable=True)
def proposal_create(c, args):
    department = find(Department, args["department"]) if args.get("department") else root()
    action = args.get("action")
    if action is not None and action.get("op") not in ops():
        raise Fail(f"unknown operation '{action.get('op')}'")
    p = new_proposal(args["title"], c.user, c.by, department, action, args.get("description", ""))
    return {"proposal": p.id}


@op("proposal.create", delegable=True)
def proposal_withdraw(c, args):
    p = find(Proposal, args["proposal"])
    if p.proposer != c.user or p.status not in OPEN:
        raise Fail("only an open proposal of yours can be withdrawn")
    p.status = "withdrawn"


@op("proposal.vote", delegable=True)
def proposal_vote(c, args):
    p = find(Proposal, args["proposal"])
    if args["choice"] not in CHOICES:
        raise Fail(f"choice must be one of {', '.join(CHOICES)}")
    if p.status != "voting" or chain.now() > p.deadline:
        raise Fail(f"{p.id} is not open for voting")
    if c.user not in eligible(p):
        raise Fail(f"you are not eligible to vote on {p.id}")
    key = f"{p.id}:{c.user}"
    v = Vote[key] or Vote(id=key, proposal=p, voter=c.user)
    v.choice, v.cast_by, v.cast_at = args["choice"], c.by, chain.now()
    return {"vote": v.id}


@op("proposal.manage")
def proposal_open(c, args):
    p = find(Proposal, args["proposal"])
    if p.status != "draft":
        raise Fail(f"{p.id} is not a draft")
    p.status, p.opens_at, p.deadline = "voting", chain.now(), chain.now() + window()


@op("proposal.manage")
def proposal_finalize(c, args):
    p = find(Proposal, args["proposal"])
    if p.status != "voting":
        raise Fail(f"{p.id} is not being voted on")
    result = outcome(p, chain.now() > p.deadline)
    if result is None:
        raise Fail(f"{p.id} is still open until {p.deadline}")
    tally, size, _ = count(p)
    p.status, p.tally = result, {**(p.tally or {}), **tally, "eligible": size}
    return {"status": result}


@op("proposal.manage")
def proposal_execute(c, args):
    p = find(Proposal, args["proposal"])
    if p.status != "accepted" or not p.action:
        raise Fail(f"{p.id} has no accepted action to execute")
    p.status = "executed"
    out = yield from dispatch(p.action["op"], json.dumps(p.action["args"]), origin="replay",
                                 signer=p.action.get("initiator") or p.proposer)
    p.result = json.loads(out)
    if not p.result.get("ok"):
        p.status = "failed"
    return {"status": p.status, "result": p.result}


@op("realm.data_view", read=True)
def proposal_get(c, args):
    p = find(Proposal, args["proposal"])
    tally, size, _ = count(p)
    return {"proposal": view(p), "tally": tally, "eligible": size,
            "votes": [view(v, "voter", "choice", "cast_by") for v in p.votes]}


@op("realm.data_view", read=True)
def proposal_list(c, args):
    rows = each(Proposal, "status", args["status"]) if args.get("status") else each(Proposal)
    return {"proposals": [view(p, "id", "title", "status", "deadline", "proposer") for p in rows]}


@op("mandate.create", governed=realm_wide)
def mandate_create(c, args):
    m = Mandate(id=next_id(Mandate, "M"), title=args["title"], scope=args.get("scope", {}),
                executor=find(Department, args["executor"]) if args.get("executor") else None,
                proposal=args.get("proposal", ""), expires_at=int(args.get("expires_at", 0)))
    return {"mandate": m.id}


@op("mandate.create", governed=realm_wide)
def mandate_revoke(c, args):
    find(Mandate, args["mandate"]).status = "revoked"


@op("mandate.assign_executor", governed=realm_wide)
def mandate_assign_executor(c, args):
    find(Mandate, args["mandate"]).executor = find(Department, args["executor"])


@op("contract.create_under_mandate")
def contract_create(c, args):
    m = find(Mandate, args["mandate"]) if args.get("mandate") else None
    value = int(args.get("value", 0))
    if m is not None:
        if m.status != "active" or (m.expires_at and chain.now() > m.expires_at):
            raise Fail(f"mandate {m.id} is not active")
        limit = int((m.scope or {}).get("max_value", 0))
        if limit and value > limit:
            raise Fail(f"{value} exceeds mandate {m.id}'s limit of {limit}")
        if m.expires_at and int(args.get("ends_at", 0)) > m.expires_at:
            raise Fail(f"the contract outlasts mandate {m.id}")
    k = Contract(id=next_id(Contract, "C"), title=args["title"], parties=args.get("parties", []), mandate=m,
                 terms_ref=args.get("terms_ref", ""), terms_hash=args.get("terms_hash", ""), status="active",
                 starts_at=int(args.get("starts_at", chain.now())), ends_at=int(args.get("ends_at", 0)))
    return {"contract": k.id}


@op("contract.terminate", signing=True)
def contract_terminate(c, args):
    k = find(Contract, args["contract"])
    if k.status in ("terminated", "fulfilled"):
        raise Fail(f"{k.id} is already {k.status}")
    k.status = "terminated"


@op("governance.update", governed=realm_wide)
def governance_update(c, args):
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
