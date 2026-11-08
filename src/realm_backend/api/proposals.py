import json

from core import chain
from core.db import Conflict, Fail, Forbidden, Unavailable, each, find, first, next_id, page, page_by, view
from ggg import (Appointment, Calendar, Contract, Department, Mandate, Member, Proposal, ProposalEdge, Realm, Task,
                 TaskSchedule, Vote)
from api import tasks
from core.op import dispatch, op, ops
from core.ratio import ONE, refuse_renamed

CHOICES = ("yes", "no", "abstain")
OPEN = ("draft", "voting")
# Proposals that can still vote or run, so their department must stay.
PENDING = OPEN + ("accepted", "execution_scheduled")
MAX_LINKS = 16  # a proposal's own links, so that a reply never outgrows the Internet Computer's limits
LINK_KINDS = ("replaces", "relates_to")
# Proposals whose deadline timers are set again after an upgrade, per message.
RESUME_PAGE = 100


def root():
    d = first(Department, "is_root", True)
    if d is None:
        raise Unavailable("the realm has no root department")
    return d


def realm_wide(args):
    return root()


def launched(args):
    """Governed realm-wide once the realm has launched; during setup the founder sets it directly (I-ADM-2)."""
    return None if Realm["1"].status == "setup" else root()


def settings():
    realm = Realm["1"]
    return (realm.manifest_data or {}).get("governance", {}) if realm else {}


def window():
    """Seconds a realm-wide proposal stays open. 0: no deadline, it closes as soon as its outcome is certain."""
    cal = Calendar["1"]
    return cal.voting_window if cal else 7 * 86400


def check_action(action):
    """An action must name a known operation and no ``on_behalf_of``: it runs once accepted as its proposer (see
    run_action), not as a member picked by whoever filed it."""
    if action.get("op") not in ops():
        raise Fail(f"unknown operation '{action.get('op')}'")
    if "on_behalf_of" in (action.get("args") or {}):
        raise Fail("an action cannot act on_behalf_of a member: it runs once accepted, as its proposer")


def check_scope(department, action):
    """A department's vote only carries actions that department governs, or one under it: the vote of a few seats
    is not the realm's. A realm-wide proposal (the root department) may carry any action. A proposal whose department
    was deleted (``None``) carries nothing: it never turns realm-wide."""
    if department is None:
        raise Forbidden("the proposal's department no longer exists")
    if department.is_root:
        return
    governed = (ops().get(action["op"]) or {}).get("governed")
    try:
        governing = governed(action.get("args") or {}) if governed else None
    except KeyError as e:
        raise Fail(f"{action['op']} is missing {e}")
    while governing is not None:
        if governing.id == department.id:
            return
        governing = governing.parent
    raise Forbidden(f"{action['op']} is not governed by {department.id}: only a realm-wide proposal can run it")


def checked_links(user, links):
    """The ``links`` of a proposal filed for member ``user``, as stored, or a refusal (ggg I-GOV-10). Nothing is written
    here: a refused proposal leaves no trace."""
    links = [] if links is None else links
    if not isinstance(links, list) or any(
            not isinstance(link, dict) or link.get("kind") not in LINK_KINDS or not isinstance(link.get("proposal"), str)
            for link in links):
        raise Fail(f"links is a list of {{kind, proposal}}, the kind one of {', '.join(LINK_KINDS)}")
    if len(links) > MAX_LINKS:
        raise Fail(f"a proposal has at most {MAX_LINKS} links")
    named = set()
    for link in links:
        if link["proposal"] in named:
            raise Fail(f"the links name proposal {link['proposal']} twice")
        named.add(link["proposal"])
        target = find(Proposal, link["proposal"])
        if link["kind"] == "replaces":
            if target.proposer != user:  # the member it was filed for: the grantor, when it was filed for one
                raise Forbidden("only the member it was filed for may replace a withdrawn proposal")
            if target.status != "withdrawn":
                raise Conflict(f"{target.id} is {target.status}: a replaced proposal must be withdrawn first")
            if target.replaced_by:
                raise Conflict(f"{target.id} is already replaced by {target.replaced_by}")
    return [{"kind": link["kind"], "proposal": link["proposal"]} for link in links]


def new_proposal(title, user, by, department, action=None, description="", effective_at=0, links=()):
    policy = department.policy or {}
    p = Proposal(id=next_id(Proposal, "P"), title=title, description=description, proposer=user,
                 on_behalf_of=user if by != user else "", department=department, action=action,
                 status="voting", opens_at=chain.now(), deadline=chain.now() + window() if window() else 0,
                 effective_at=effective_at, links=list(links),
                 threshold=int(settings().get("voting_threshold", ONE // 2)),
                 quorum=int(settings().get("voting_quorum", 0) if department.is_root else policy.get("quorum", 0)))
    for link in links:
        ProposalEdge(id=next_id(ProposalEdge, "L"), source=p.id, target=link["proposal"], kind=link["kind"])
        if link["kind"] == "replaces":
            Proposal[link["proposal"]].replaced_by = p.id
    p.tally = {c: 0 for c in CHOICES}
    if not department.is_root:
        p.tally = {**p.tally, "required": int(policy.get("m", 1)), "veto": list(policy.get("veto", []))}
    if p.deadline:
        arm(p)
    return p


def arm(p):
    chain.set_timer(max(p.deadline - chain.now(), 0) + 1, lambda pid=p.id: expire(pid))


def expire(pid):
    """At its deadline, a proposal still open is settled."""
    p = Proposal[pid]
    if p is not None and p.status == "voting":
        yield from settle(p, True)


def resume(start=1):
    """Timers do not survive an upgrade: set them again for the open proposals with a deadline, a page per message."""
    rows, nxt = page_by(Proposal, "status", "voting", {"from": start, "count": RESUME_PAGE})
    for p in rows:
        if p.deadline:
            arm(p)
    if nxt:
        chain.set_timer(0, lambda n=nxt: resume(n))


def close(p, result):
    tally, size, _ = count(p)
    if result == "accepted" and p.effective_at:
        result = status_at_effective_time(p)  # before any write: a refusal leaves the proposal open
    p.status, p.tally = result, {**(p.tally or {}), **tally, "eligible": size}


def vote_end(p):
    """When the vote of ``p``, closed now, ended: at its deadline, or earlier, at the vote that ended it (the last of the
    eligible members, or the one that accepted a department's vote or decided a vote with no deadline). Closing it
    later than that does not move the end: the deadline timer fires a second late, an upgrade arms it again whenever
    the realm starts, and ``proposal_finalize`` may come days after the deadline."""
    return min(chain.now(), p.deadline) if p.deadline else chain.now()


def status_at_effective_time(p):
    """The status of a proposal accepted now that states an ``effective_at``: it takes effect then, never before. A vote
    that ended after that time has missed it: the proposal expires and its action never runs. A vote that ended in time
    but is closed only after it is accepted, and its action runs at once."""
    if vote_end(p) > p.effective_at:
        return "expired"
    if not p.action or chain.now() >= p.effective_at:
        return "accepted"
    schedule(p)
    return "execution_scheduled"


def schedule(p):
    """Run the action at ``effective_at`` as a one-step task of the realm's task scheduler, which arms its timer again
    after an upgrade (tasks.rearm). The step runs as the realm itself: the vote is the authority, not the proposer."""
    if Task[scheduled_task_name(p)]:
        raise Conflict(f"task '{scheduled_task_name(p)}' exists")
    task = Task(name=scheduled_task_name(p), metadata=json.dumps({"creator": chain.self_id()}), status="pending")
    tasks.define(task, [("proposal_run_scheduled", {"proposal": p.id}, 0)])
    s = TaskSchedule(name=f"{task.name}@1", task=task, last_run_at=0, run_at=p.effective_at, repeat_every=0)
    tasks.enable(s, True)
    tasks.arm(s)


def scheduled_task_name(p):
    return f"{tasks.OWN}{p.id}"


def run_action(p):
    """Run the accepted action as its proposer: the vote is the authority, and the action cannot name anyone else.
    The vote's authority stops at the proposal's department, so an action outside it fails without running."""
    try:
        check_scope(p.department, p.action)
    except Fail as e:
        p.status, p.result = "failed", {"ok": False, "error": str(e), "kind": e.kind}
        return
    p.status = "executed"
    out = yield from dispatch(p.action["op"], json.dumps(p.action["args"]), origin="replay", signer=p.proposer,
                              proposal=p.id)
    p.result = json.loads(out)
    if not p.result.get("ok"):
        p.status = "failed"


def settle(p, final):
    """Close ``p`` once its outcome is certain (``final``: its deadline has passed) and, if it passed, run its
    action at once. Returns the outcome, or None while it is still open."""
    result = outcome(p, final)
    if result is not None:
        close(p, result)
        if p.status == "accepted" and p.action:
            yield from run_action(p)
    return result


def file_replay(name, args, user, by, department):
    """Turn a governed call into a proposal that replays it once accepted."""
    p = new_proposal(f"{name} {json.dumps(args, sort_keys=True)[:120]}", user, by, department,
                     {"op": name, "args": args})
    return {"proposal": p.id, "requires_vote": True}


def seated(principal, department):
    """``principal`` heads ``department`` or holds an active seat in it."""
    return principal == department.head or any(
        a.position.department and a.position.department.id == department.id for a in appointed(principal))


def eligible(p, principal):
    if p.department is None:
        return False
    if p.department.is_root:
        m = Member[principal]
        return m is not None and m.status == "active"
    return seated(principal, p.department)


def electorate_size(p):
    if p.department is None:
        return 0
    if p.department.is_root:
        return Member.count_by("status", "active")
    return len(holders(p.department) | ({p.department.head} - {""}))


def count(p):
    """The tally kept as votes are cast, the number of eligible voters and whether a veto holder voted no."""
    tally = {c: int((p.tally or {}).get(c, 0)) for c in CHOICES}
    size = electorate_size(p)
    vetoes = (Vote[f"{p.id}:{v}"] for v in (p.tally or {}).get("veto", []))
    return tally, size, any(v is not None and v.choice == "no" for v in vetoes)


def veto_pending(p):
    """Whether a veto holder of the department proposal ``p`` has yet to vote."""
    return any(Vote[f"{p.id}:{v}"] is None for v in (p.tally or {}).get("veto", []))


def department_outcome(p, tally, size, vetoed, final):
    """A department's vote passes with ``m`` approvals, no veto holder against and the policy's quorum met (ggg
    I-GOV-3). Before it ends it is accepted only once every veto holder has voted, and it is never rejected: a no
    can still be replaced (ggg I-GOV-7)."""
    turnout = sum(tally.values()) * ONE >= p.quorum * max(size, 1)
    passes = tally["yes"] >= p.tally["required"] and not vetoed and turnout
    if passes and (final or not veto_pending(p)):
        return "accepted"
    return "rejected" if final else None


def outcome(p, final):
    tally, size, vetoed = count(p)
    # A vote ends at its deadline, or earlier, the moment every eligible member has voted (ggg I-GOV-7).
    final = final or (size > 0 and sum(tally.values()) >= size)
    if "required" in (p.tally or {}):
        return department_outcome(p, tally, size, vetoed, final)
    if not p.deadline and not final:
        # No deadline: open until certain. Yes alone over the threshold of everyone eligible passes it; enough no
        # that the rest voting yes could not pass it fails it; once everyone has voted, it is decided as is.
        turnout = sum(tally.values()) * ONE >= p.quorum * max(size, 1)
        if tally["yes"] * ONE > p.threshold * max(size, 1) and turnout:
            return "accepted"
        if tally["no"] * ONE >= (ONE - p.threshold) * max(size, 1):
            return "rejected"
        final = sum(tally.values()) >= size
    if not final:
        return None
    decisive = tally["yes"] + tally["no"]
    turnout = sum(tally.values()) * ONE >= p.quorum * max(size, 1)
    return "accepted" if turnout and decisive and tally["yes"] * ONE > p.threshold * decisive else "rejected"


def effective_at_of(args):
    """The proposal's ``effective_at``, 0 when it states none. Only whole seconds: a fraction would be cut off and run
    early (the GGG methods round a time up to the second before they get here). Never a time that has passed."""
    value = args.get("effective_at")
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int):
        raise Fail("effective_at is whole seconds")
    if value and value < chain.now():
        raise Fail("effective_at is in the past")
    return value


@op("proposal.create", delegable=True, returns="proposal")
def proposal_create(c, args):
    """File a proposal: ``title``, optional ``description``, ``department`` (default the whole realm) and ``action``
    (``{op, args}``, run once accepted; a department's proposal may only carry actions that department or one under it
    governs). Voting opens at once. Optional ``effective_at`` (whole seconds, UTC, not in the past): once accepted, the action runs then
    instead (status ``execution_scheduled`` while it waits). A vote ends at its deadline, or earlier once every eligible
    member has voted (or, in a department, once it has ``m`` approvals, every veto holder has voted, none against, and
    its quorum is met; or, in a realm whose voting window is 0, once the outcome is certain), whenever it is closed;
    if it ends after ``effective_at`` the proposal ends ``expired`` and nothing runs.
    Optional ``links``, at most 16 of ``{kind, proposal}`` (``replaces`` or ``relates_to``), each proposal named once and
    fixed at creation: ``replaces`` names a withdrawn proposal of yours that no other proposal replaces yet.
    Returns ``proposal``.
    Example: ``{"title": "Bike lane on Main St", "department": "infrastructure"}`` → ``P-7``."""
    department = find(Department, args["department"]) if args.get("department") else root()
    action = args.get("action")
    if action is not None:
        check_action(action)
        check_scope(department, action)
    links = checked_links(c.user, args.get("links"))
    p = new_proposal(args["title"], c.user, c.by, department, action, args.get("description", ""),
                     effective_at_of(args), links)
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


@op("realm.data_view", read=True, returns="links")
def proposal_linked_from(c, args):
    """The links of other proposals to ``proposal``, oldest first, ``count`` at a time (default 100, at most 500) from
    row ``from`` (default 1): ``links`` (the ``kind`` and the ``proposal`` that carries it; ``replaces`` there reads
    "replaced by"); ``next`` continues them (null at the end). Nothing limits how many proposals link to one."""
    p = find(Proposal, args["proposal"])
    edges, nxt = page_by(ProposalEdge, "target", p.id, args)
    return {"links": [{"kind": e.kind, "proposal": e.source} for e in edges], "next": nxt}


@op("proposal.vote", delegable=True, returns="vote")
def proposal_vote(c, args):
    """Vote on ``proposal`` before its deadline: ``choice`` is yes, no or abstain. Returns ``vote``.
    Voting again replaces your earlier vote, until the vote ends: at its deadline, or once every eligible member has
    voted (in a department, once it has ``m`` approvals, every veto holder has voted, none against, and its quorum is
    met; in a realm with no voting window, once the outcome is certain). A department's vote never ends with a
    rejection before then, so a no can be replaced. After the vote ends no vote is accepted and none is replaced."""
    p = find(Proposal, args["proposal"])
    if args["choice"] not in CHOICES:
        raise Fail(f"choice must be one of {', '.join(CHOICES)}")
    if p.status != "voting" or (p.deadline and chain.now() > p.deadline):
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
    # The deciding vote closes the proposal and runs its action.
    settled = yield from settle(p, False)
    return {"vote": v.id, **({"status": p.status} if settled else {})}


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
    """Close voting on ``proposal`` once its vote has ended, and record it as accepted or rejected. Returns ``status``.
    A vote ends at its deadline, or earlier as ``proposal_vote`` says; closing it late does not move that end (see
    ``effective_at`` of ``proposal_create``). An accepted action runs at once, unless its ``effective_at`` is still
    ahead: it runs then. Returns ``result`` when it ran."""
    p = find(Proposal, args["proposal"])
    if p.status != "voting":
        raise Conflict(f"{p.id} is not being voted on")
    if (yield from settle(p, bool(p.deadline) and chain.now() > p.deadline)) is None:
        raise Conflict(f"{p.id} is still open" + (f" until {p.deadline}" if p.deadline else " until its outcome is certain"))
    return {"proposal": p.id, "status": p.status, **({"result": p.result} if p.result else {})}


@op("proposal.manage", returns="proposal")
def proposal_execute(c, args):
    """Run the action of the accepted ``proposal``, once. Returns ``status`` (executed or failed) and ``result``."""
    p = find(Proposal, args["proposal"])
    if p.status != "accepted" or not p.action:
        raise Conflict(f"{p.id} has no accepted action to execute")
    yield from run_action(p)
    return {"proposal": p.id, "status": p.status, "result": p.result}


def scheduled_department(args):
    """The department of the proposal to stop: its vote governs the stop, like any action."""
    return find(Proposal, args["proposal"]).department


@op("proposal.manage", governed=scheduled_department)
def proposal_stop(c, args):
    """Stop ``proposal``, accepted and waiting for its ``effective_at``: it ends withdrawn and its action never runs.
    A proposal stops another by carrying this as its action. Returns ``status``; it creates no record, so the GGG
    method replies ``Done`` without an id."""
    p = find(Proposal, args["proposal"])
    if p.status != "execution_scheduled":
        raise Conflict(f"{p.id} is {p.status}; only a proposal waiting for its effective time can be stopped")
    task = Task[scheduled_task_name(p)]
    if task:
        tasks.stop(task)
    p.status = "withdrawn"
    return {"proposal": p.id, "status": p.status}


@op(None, public=True, returns="proposal")
def proposal_run_scheduled(c, args):
    """Run the action of ``proposal`` if it waits for its ``effective_at`` and that time has come. Anyone may ask:
    the vote decided it and the time cannot move. The realm's scheduler calls it at that time; asking again is
    harmless. Returns ``status`` (executed or failed) and ``result``."""
    p = find(Proposal, args["proposal"])
    if p.status != "execution_scheduled":
        return {"proposal": p.id, "status": p.status, "result": p.result}
    if chain.now() < p.effective_at:
        raise Conflict(f"{p.id} takes effect at {p.effective_at}")
    yield from run_action(p)
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
    """Proposals (id, title, status, deadline, effective_at, proposer), or only those with ``status`` (e.g. voting),
    ``count`` at a time (default 100, at most 500) from row ``from`` (default 1). Returns ``proposals`` and ``next``
    (null at the end)."""
    rows, nxt = page_by(Proposal, "status", args["status"], args) if args.get("status") else page(Proposal, args)
    return {"proposals": [view(p, "id", "title", "status", "deadline", "effective_at", "proposer") for p in rows],
            "next": nxt}


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


def voting_rules(args):
    """The ratios of a ``governance_update`` call, refused if they are not between 0 and 100 %, or the old names."""
    refuse_renamed(args, {"threshold": "voting_threshold", "quorum_percent": "voting_quorum"})
    ratios = {k: int(args[k]) for k in ("voting_threshold", "voting_quorum") if k in args}
    if any(not 0 <= v <= ONE for v in ratios.values()):
        raise Fail(f"a threshold and a quorum are between 0 and {ONE} (100 %)")
    return ratios


@op("governance.update", governed=launched, check=voting_rules)
def governance_update(c, args):
    """Set the voting rules for new proposals, any of: ``voting_window`` (seconds), ``voting_threshold`` (the share of
    the yes and no votes the yes votes of a realm-wide proposal must exceed) and ``voting_quorum`` (the share of the
    members who must vote on it), both in hundred-millionths: 50 % is 50000000."""
    ratios = voting_rules(args)
    realm, cal = Realm["1"] or Realm(id="1"), Calendar["1"] or Calendar(id="1")
    if "voting_window" in args:
        cal.voting_window = int(args["voting_window"])
    data = realm.manifest_data or {}
    data["governance"] = {**data.get("governance", {}), **ratios}
    realm.manifest_data = data


def holders(department):
    return {a.user for pos in department.positions for a in pos.appointments if a.status == "active"}


def appointed(principal):
    return [a for a in each(Appointment, "user", principal) if a.status == "active"]
