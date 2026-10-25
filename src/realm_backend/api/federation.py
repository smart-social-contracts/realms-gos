import json

from core import chain
from api import proposals
from api import quarters
from core.db import Fail, each, find, next_id
from ggg import FederalVote, FederalVoteLeg, FederationMessage, Proposal, Quarter
from core.op import GENERATOR, dispatch, op, ops

PASSED = ("accepted", "executed")


def leg_id(vote, quarter):
    return f"{vote}:{quarter}"


def freeze(action, rule, deadline):
    return quarters.digest({"action": action, "rule": rule, "deadline": deadline})


def ballot(vote, action, deadline, proposer, by):
    """The local leg of a federal vote: a realm-wide proposal with no action of its own."""
    p = proposals.new_proposal(f"{vote}: {action.get('title') or action['op']}", proposer, by, proposals.root(),
                               None, f"Local leg of federal vote {vote}")
    p.deadline = deadline
    return p


def open_vote(action, proposer, by, origin):
    if action.get("op") not in ops():
        raise Fail(f"unknown operation '{action.get('op')}'")
    rule = quarters.published_rule()
    deadline = chain.now() + rule["window"]
    vid, h = next_id(FederalVote, "FV"), freeze(action, rule, deadline)
    body = {"vote": vid, "action": action, "rule": rule, "deadline": deadline, "hash": h, "proposer": proposer,
            "origin": origin}
    legs = {}
    for cid in [q.id for q in each(Quarter) if q.status == "active"]:
        reply = yield from quarters.ask(cid, "federation_message", {"topic": "federal_vote.open", "body": body})
        if not reply.get("ok"):
            raise Fail(f"{cid} refused {vid}: {reply.get('error')}")
        legs[cid] = reply["proposal"]
    v = FederalVote(id=vid, origin_quarter=origin, action=action, rule=rule, hash=h, deadline=deadline)
    legs[chain.self_id()] = ballot(vid, action, deadline, proposer, by).id
    for cid, pid in legs.items():
        FederalVoteLeg(id=leg_id(vid, cid), vote=v, quarter=cid, proposal_id=pid, hash=h)
    return {"vote": vid, "hash": h, "deadline": deadline, "quarters": len(legs)}


def settle(leg):
    p = find(Proposal, leg.proposal_id)
    if p.status == "voting":
        result = proposals.outcome(p, chain.now() > p.deadline)
        if result is None:
            raise Fail(f"{p.id} is still open until {p.deadline}")
        tally, size, _ = proposals.count(p)
        p.status, p.tally = result, {**(p.tally or {}), **tally, "eligible": size}
    tally, size, _ = proposals.count(p)
    leg.outcome, leg.status = "passed" if p.status in PASSED else "failed", "reported"
    leg.yes, leg.no, leg.abstain, leg.eligible = tally["yes"], tally["no"], tally["abstain"], size


def leg_data(leg):
    return {"vote": leg.vote.id, "outcome": leg.outcome, "yes": leg.yes, "no": leg.no, "abstain": leg.abstain,
            "eligible": leg.eligible, "hash": leg.hash}


def record_leg(q, data):
    leg = find(FederalVoteLeg, leg_id(data["vote"], q.id))
    if leg.vote.status != "open":
        raise Fail(f"{leg.vote.id} is already {leg.vote.status}")
    if data.get("hash") != leg.vote.hash:
        raise Fail(f"the leg does not match {leg.vote.id}'s published action and rule")
    leg.outcome, leg.status = "passed" if data["outcome"] == "passed" else "failed", "reported"
    leg.yes, leg.no, leg.abstain, leg.eligible = (int(data.get(k, 0)) for k in ("yes", "no", "abstain", "eligible"))


def rule_of(v):
    return {**quarters.RULE, **(v.rule or {})}


def count(v):
    """Apply the vote's frozen rule to the legs (I-FED-2)."""
    rule, legs = rule_of(v), list(v.legs)
    reported = [leg for leg in legs if leg.status == "reported"]
    absent = len(legs) - len(reported)
    if rule["kind"] == "majority_of_members":
        yes, no = sum(leg.yes for leg in reported), sum(leg.no for leg in reported)
    else:
        yes = sum(leg.outcome == "passed" for leg in reported)
        no = len(reported) - yes + (absent if rule["absent"] == "no" else 0)
    status = "passed" if yes * 100 > int(rule["threshold"]) * (yes + no) else "failed"
    return status, {"yes": yes, "no": no, "absent": absent, "quarters": len(legs)}


def withdraw(leg):
    p = Proposal[leg.proposal_id]
    if p is not None and p.status in proposals.OPEN:
        p.status = "withdrawn"
    leg.status = "cancelled"


def execute(v):
    out = yield from dispatch(v.action["op"], json.dumps(v.action.get("args", {})), origin="replay",
                              signer=chain.self_id())
    return json.loads(out)


@op("federal_vote.propose", delegable=True)
def federal_vote_propose(c, args):
    """Propose a federal vote titled ``title`` on ``action`` (``{"op": …, "args": …}``), held in every active quarter
    under the published rule; a quarter passes it to the capital. Returns ``vote``."""
    action = {**args["action"], "title": args["title"]}
    if not quarters.federated():
        return (yield from open_vote(action, c.user, c.by, chain.self_id()))
    reply = yield from quarters.ask(quarters.capital(), "federation_message",
                                    {"topic": "federal_vote.propose", "body": {"action": action, "proposer": c.user}})
    if not reply.get("ok"):
        raise Fail(f"the capital refused the vote: {reply.get('error')}")
    return {"vote": reply["vote"]}


@op("federal_vote.manage", governed=proposals.realm_wide)
def federal_vote_cancel(c, args):
    """On the capital, cancel the open federal ``vote`` and withdraw its local vote in every quarter."""
    v = find(FederalVote, args["vote"])
    if quarters.federated() or v.status != "open":
        raise Fail(f"{v.id} can only be cancelled on the capital while open")
    v.status = "cancelled"
    for leg in list(v.legs):
        if leg.quarter == chain.self_id():
            withdraw(leg)
        else:
            yield from quarters.ask(leg.quarter, "federation_message",
                                    {"topic": "federal_vote.cancel", "body": {"vote": v.id, "hash": v.hash}})
    return {"status": "cancelled"}


@op(None, public=True)
def federal_vote_finalize(c, args):
    """On a quarter: settle and report the local leg of ``vote``. On the capital: decide the vote and announce it.
    The capital waits for missing legs, even past the deadline if the rule says wait; a passed vote runs its action."""
    v = find(FederalVote, args["vote"])
    if v.status != "open":
        raise Fail(f"{v.id} is already {v.status}")
    here = chain.self_id()
    own = find(FederalVoteLeg, leg_id(v.id, here))
    if quarters.federated():
        if own.status != "reported":
            settle(own)
        reply = yield from quarters.ask(quarters.capital(), "quarter_report", {**quarters.report(), "leg": leg_data(own)})
        return {"leg": own.outcome, "reported": bool(reply.get("ok")), "error": reply.get("error", "")}
    missing = [leg.quarter for leg in v.legs if leg.status != "reported" and leg.quarter != here]
    if missing and (chain.now() <= v.deadline or rule_of(v)["absent"] == "wait"):
        raise Fail(f"{v.id} is waiting for legs from {', '.join(missing)}")
    if own.status != "reported":
        settle(own)
    status, tally = count(v)
    v.status, v.tally = status, tally
    if status == "passed":
        v.tally = {**tally, "result": (yield from execute(v))}
    for cid in [leg.quarter for leg in v.legs if leg.quarter != here]:
        yield from quarters.ask(cid, "federation_message",
                                {"topic": "federal_vote.result", "body": {"vote": v.id, "status": status, "hash": v.hash}})
    return {"status": status, **tally}


def on_propose(c, body):
    if quarters.federated():
        raise Fail("federal votes are opened by the capital")
    return (yield from open_vote(body["action"], body["proposer"], c.by, c.by))


def on_open(c, body):
    quarters.from_capital(c)
    if freeze(body["action"], body["rule"], body["deadline"]) != body["hash"]:
        raise Fail("the vote's hash does not match its action, rule and deadline")
    if FederalVote[body["vote"]]:
        raise Fail(f"{body['vote']} is already open here")
    v = FederalVote(id=body["vote"], origin_quarter=body["origin"], action=body["action"], rule=body["rule"],
                    hash=body["hash"], deadline=body["deadline"])
    p = ballot(v.id, v.action, v.deadline, body["proposer"], c.by)
    FederalVoteLeg(id=leg_id(v.id, chain.self_id()), vote=v, quarter=chain.self_id(), proposal_id=p.id, hash=v.hash)
    return {"proposal": p.id}


def mirrored(c, body):
    quarters.from_capital(c)
    v = find(FederalVote, body["vote"])
    if body["hash"] != v.hash or v.status != "open":
        raise Fail(f"this does not match open vote {v.id}")
    return v


def on_result(c, body):
    v = mirrored(c, body)
    v.status = body["status"]
    if v.status == "passed":
        return {"status": v.status, "result": (yield from execute(v))}
    return {"status": v.status}


def on_cancel(c, body):
    v = mirrored(c, body)
    v.status = "cancelled"
    withdraw(find(FederalVoteLeg, leg_id(v.id, chain.self_id())))
    return {"status": "cancelled"}


TOPICS = {"user.arrive": quarters.on_arrive, "quarter.secede": quarters.on_secede, "codex": quarters.on_codex,
          "federal_vote.propose": on_propose, "federal_vote.open": on_open, "federal_vote.result": on_result,
          "federal_vote.cancel": on_cancel}


@op("quarter.sync")
def federation_message(c, args):
    """From a registered quarter: handle ``topic`` with ``body``. From a local holder of quarter.sync: send it out, to
    quarter ``to`` or by default to every quarter."""
    topic, body = args["topic"], args.get("body", {})
    if Quarter[c.by] is None:
        targets = [find(Quarter, args["to"]).id] if args.get("to") else [q.id for q in each(Quarter)]
        replies = {}
        for cid in targets:
            replies[cid] = yield from quarters.ask(cid, "federation_message", {"topic": topic, "body": body})
        m = FederationMessage(id=next_id(FederationMessage, "FM"), topic=topic, source=chain.self_id(), body=body,
                              response=replies)
        return {"message": m.id, "replies": replies}
    handler = TOPICS.get(topic)
    out = handler(c, body) if handler else {}
    if isinstance(out, GENERATOR):
        out = yield from out
    m = FederationMessage(id=next_id(FederationMessage, "FM"), topic=topic, source=c.by, body=body, response=out)
    return {"message": m.id, **out}
