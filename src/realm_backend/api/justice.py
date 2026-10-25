from core import chain
from core.db import Fail, deref, each, find, next_id, view
from ggg import (Appeal, Case, Contract, Court, Department, Dispute, Invoice, Judge, Member, Notification, Organization,
                 Penalty, User, Verdict)
from api.licenses import active
from core.op import op
from api.proposals import holders, realm_wide

LEVELS = {"first_instance": 1, "specialized": 1, "appellate": 2, "supreme": 3}
APPEAL_WINDOW = 14 * 86400
PAY_WITHIN = 30 * 86400
TRIAL = ("assigned", "in_progress")
PENDING = ("filed", "under_review")
PENALTIES = ("fine", "restitution", "community_service", "suspension", "revocation", "injunction", "freeze",
             "termination")
TARGETS = {"suspension": ("User", "Organization"), "revocation": ("License",), "termination": ("Contract",)}


def people(ref):
    """The principals behind a party: a user; a department's appointees and head; an organization's admins and
    representatives."""
    kind, _, key = (ref or "").partition(":")
    if kind == "User":
        return {key}
    if kind == "Department" and Department[key]:
        d = Department[key]
        return holders(d) | ({d.head} - {""})
    if kind == "Organization" and Organization[key]:
        o = Organization[key]
        return set(o.admins or []) | {a.user for p in o.positions for a in p.appointments if a.status == "active"}
    return set()


def dispute_people(d):
    return people(f"User:{d.claimant}") | people(d.respondent)


def case_people(case):
    return people(case.plaintiff) | people(case.defendant)


def of_dispute(args):
    return dispute_people(find(Dispute, args["dispute"]))


def of_case(args):
    return case_people(find(Case, args["case"]))


def of_verdict(args):
    return case_people(find(Verdict, args["verdict"]).case)


def of_appeal(args):
    return case_people(find(Appeal, args["appeal"]).case)


def of_penalty(args):
    return case_people(find(Penalty, args["penalty"]).verdict.case)


def operating(court):
    if court.status != "active":
        raise Fail(f"court {court.id} is {court.status}")
    if court.operator.startswith("Organization:") and active(court.operator, "court") is None:
        raise Fail(f"court {court.id} has no active court licence")


def sitting(case, user):
    for j in case.judges:
        if j.user == user and j.status == "active":
            return j
    raise Fail(f"you are not a judge assigned to {case.id}")


def pending(case):
    return any(a.status in PENDING for a in case.appeals)


def as_ref(text):
    return text if ":" in text else f"User:{text}"


def penalty_terms(t):
    kind, target = t.get("kind", "fine"), as_ref(t["target"])
    if kind not in PENALTIES:
        raise Fail(f"penalty kind must be one of {', '.join(PENALTIES)}")
    row = deref(target)
    if kind in TARGETS and type(row).__name__ not in TARGETS[kind]:
        raise Fail(f"a {kind} applies to {' or '.join(TARGETS[kind])}, not {target}")
    amount = int(t.get("amount", 0))
    if kind in ("fine", "restitution") and (amount <= 0 or not t.get("symbol")):
        raise Fail(f"a {kind} needs a positive amount and a symbol")
    return {"kind": kind, "target": target, "amount": amount, "symbol": t.get("symbol", ""),
            "due_at": int(t.get("due_at", 0))}


def add_penalties(verdict, terms):
    for t in terms:
        Penalty(id=next_id(Penalty, "PEN"), verdict=verdict, **t)


def verdict_view(v):
    return {**view(v, "id", "decision", "reasoning_ref", "status", "contract", "issued_at"), "case": v.case.id,
            "court": v.case.court.id if v.case.court else "", "title": v.case.title,
            "issued_by": v.issued_by.id if v.issued_by else "",
            "penalties": [view(p, "id", "kind", "target", "amount", "symbol", "status", "invoice")
                          for p in v.penalties]}


def notify(recipient, topic, title, body, sender):
    n = Notification(id=next_id(Notification, "N"), recipient=recipient, topic=topic, title=title, body=body,
                     sender=sender, at=chain.now())
    return n.id


@op("dispute.create", delegable=True)
def dispute_file(c, args):
    respondent = as_ref(args["respondent"])
    deref(respondent)
    d = Dispute(id=next_id(Dispute, "D"), claimant=c.user, respondent=respondent, subject=args.get("subject", ""),
                title=args["title"], description_ref=args.get("description_ref", ""))
    return {"dispute": d.id}


@op("dispute.create", delegable=True)
def dispute_withdraw(c, args):
    d = find(Dispute, args["dispute"])
    if d.claimant != c.user or d.status != "filed":
        raise Fail("only a filed dispute of yours can be withdrawn")
    d.status = "withdrawn"


@op("dispute.view", read=True, delegable=True)
def dispute_get(c, args):
    d = find(Dispute, args["dispute"])
    if c.user not in dispute_people(d):
        raise Fail(f"{d.id} is not your dispute")
    return {"dispute": view(d)}


@op("dispute.view_all", read=True)
def dispute_list_all(c, args):
    rows = [d for d in each(Dispute) if not args.get("status") or d.status == args["status"]]
    return {"disputes": [view(d, "id", "claimant", "respondent", "title", "status", "case") for d in rows]}


@op("dispute.accept", signing=True, parties=of_dispute)
def dispute_accept(c, args):
    d, court = find(Dispute, args["dispute"]), find(Court, args["court"])
    if d.status != "filed":
        raise Fail(f"{d.id} is {d.status}")
    operating(court)
    case = Case(id=next_id(Case, "CASE"), court=court, plaintiff=f"User:{d.claimant}", defendant=d.respondent,
                title=d.title, dispute=d.id, filed_at=chain.now())
    d.status, d.case = "accepted", case.id
    return {"case": case.id}


@op("dispute.reject", signing=True, parties=of_dispute)
def dispute_reject(c, args):
    d = find(Dispute, args["dispute"])
    if d.status != "filed":
        raise Fail(f"{d.id} is {d.status}")
    d.status = "rejected"


@op("dispute.assign", parties=of_case)
def case_assign(c, args):
    case, judge = find(Case, args["case"]), find(Judge, args["judge"])
    if case.status not in ("filed",) + TRIAL:
        raise Fail(f"{case.id} is {case.status}")
    if judge.status != "active" or judge.court is None or judge.court.id != case.court.id:
        raise Fail(f"{judge.id} does not sit at {case.court.id}")
    if judge.user in case_people(case):
        raise Fail(f"{judge.id} is a party to {case.id}")
    operating(case.court)
    case.judges = [j for j in case.judges if j.id != judge.id] + [judge]
    if case.status == "filed":
        case.status = "assigned"


@op("dispute.create", delegable=True)
def evidence_submit(c, args):
    case = find(Case, args["case"])
    if c.user not in case_people(case):
        raise Fail(f"only a party to {case.id} submits evidence")
    if case.status not in TRIAL:
        raise Fail(f"{case.id} is {case.status}")
    if not args.get("ref") or not str(args.get("hash", "")).startswith("sha256:"):
        raise Fail("evidence is an encrypted reference with its sha256 hash")
    items = case.evidence or []
    item = {"id": f"E-{len(items) + 1}", "by": c.user, "ref": args["ref"], "hash": args["hash"],
            "status": "submitted", "at": chain.now()}
    case.evidence, case.status = items + [item], "in_progress"
    return {"evidence": item["id"]}


@op("evidence.evaluate", signing=True, parties=of_case)
def evidence_evaluate(c, args):
    case = find(Case, args["case"])
    sitting(case, c.user)
    items = case.evidence or []
    match = [i for i in items if i["id"] == args["evidence"]]
    if not match:
        raise Fail(f"unknown evidence '{args['evidence']}' in {case.id}")
    match[0]["status"] = "admitted" if args.get("admitted", True) else "excluded"
    case.evidence = items
    return {"status": match[0]["status"]}


@op("resolution.draft", parties=of_case)
def verdict_draft(c, args):
    case = find(Case, args["case"])
    if case.status not in TRIAL:
        raise Fail(f"{case.id} is {case.status}")
    terms = [penalty_terms(t) for t in args.get("penalties", [])]
    v = Verdict(id=next_id(Verdict, "VER"), case=case, decision=args["decision"],
                reasoning_ref=args.get("reasoning_ref", ""))
    add_penalties(v, terms)
    case.status = "in_progress"
    return {"verdict": v.id}


@op("resolution.issue", signing=True, parties=of_verdict)
def verdict_issue(c, args):
    v = find(Verdict, args["verdict"])
    if v.status != "draft" or v.case.status not in TRIAL:
        raise Fail(f"{v.id} cannot be issued")
    judge = sitting(v.case, c.user)
    operating(v.case.court)
    v.issued_by, v.status, v.issued_at = judge, "issued", chain.now()
    v.case.status = "verdict_issued"
    return {"appealable_until": v.issued_at + APPEAL_WINDOW}


@op("resolution.finalize", signing=True, parties=of_verdict)
def verdict_finalize(c, args):
    v = find(Verdict, args["verdict"])
    if v.status != "issued":
        raise Fail(f"{v.id} is {v.status}")
    if chain.now() < v.issued_at + APPEAL_WINDOW:
        raise Fail(f"{v.id} can be appealed until {v.issued_at + APPEAL_WINDOW}")
    if pending(v.case):
        raise Fail(f"an appeal against {v.case.id} is pending")
    v.status = "final"
    v.case.status, v.case.closed_at = "closed", chain.now()


@op("resolution.link_contract", signing=True, parties=of_verdict)
def verdict_link_contract(c, args):
    v, k = find(Verdict, args["verdict"]), find(Contract, args["contract"])
    if v.status == "overturned" or k.status in ("terminated", "fulfilled"):
        raise Fail(f"{v.id} cannot be linked to {k.id}")
    v.contract = k.id
    if k.status == "active":
        k.status = "disputed"


@op("resolution.modify_terms", signing=True, parties=of_verdict)
def verdict_modify_terms(c, args):
    v = find(Verdict, args["verdict"])
    if v.status not in ("draft", "issued"):
        raise Fail(f"{v.id} is {v.status} and cannot change")
    sitting(v.case, c.user)
    terms = [penalty_terms(t) for t in args["penalties"]] if "penalties" in args else None
    v.decision = args.get("decision", v.decision)
    v.reasoning_ref = args.get("reasoning_ref", v.reasoning_ref)
    if terms is not None:
        for p in list(v.penalties):
            p.delete()
        add_penalties(v, terms)
    if v.status == "issued":
        v.issued_at = chain.now()  # changed terms reopen the appeal window


@op("dispute.create", delegable=True)
def appeal_file(c, args):
    v = find(Verdict, args["verdict"])
    case = v.case
    if c.user not in case_people(case):
        raise Fail(f"only a party to {case.id} can appeal")
    if v.status != "issued" or chain.now() > v.issued_at + APPEAL_WINDOW or pending(case):
        raise Fail(f"{v.id} cannot be appealed")
    below = v.issued_by.court
    court = find(Court, args["court"]) if args.get("court") else below.parent
    if court is None or LEVELS.get(court.level, 1) <= LEVELS.get(below.level, 1):
        raise Fail(f"an appeal goes to a court above {below.id}")
    operating(court)
    a = Appeal(id=next_id(Appeal, "APL"), case=case, verdict=v.id, court=court, appellant=c.user,
               grounds_ref=args.get("grounds_ref", ""))
    case.status = "appealed"
    return {"appeal": a.id, "court": court.id}


@op("appeal.allow", signing=True, parties=of_appeal)
def appeal_decide(c, args):
    a = find(Appeal, args["appeal"])
    if a.status not in PENDING:
        raise Fail(f"{a.id} is {a.status}")
    judges = [j for j in each(Judge, "user", c.user) if j.status == "active" and j.court and j.court.id == a.court.id]
    if not judges:
        raise Fail(f"only a judge of {a.court.id} decides {a.id}")
    operating(a.court)
    granted = bool(args["granted"])
    terms = [penalty_terms(t) for t in args.get("penalties", [])]
    if granted and not args.get("decision"):
        raise Fail("a granted appeal needs the new decision")
    a.status, a.decision = ("granted" if granted else "denied"), args.get("decision", "")
    if granted:
        find(Verdict, a.verdict).status = "overturned"
        new = Verdict(id=next_id(Verdict, "VER"), case=a.case, issued_by=judges[0], decision=args["decision"],
                      reasoning_ref=args.get("reasoning_ref", ""), status="issued", issued_at=chain.now())
        add_penalties(new, terms)
        a.new_verdict = new.id
    a.case.status = "verdict_issued"
    return {"status": a.status, "verdict": a.new_verdict or a.verdict}


@op("fine.apply", signing=True, parties=of_penalty)
def penalty_execute(c, args):
    p = find(Penalty, args["penalty"])
    if p.status != "pending":
        raise Fail(f"{p.id} is {p.status}")
    if p.verdict.status != "final":
        raise Fail(f"{p.verdict.id} is not final yet")
    target, now = deref(p.target), chain.now()
    if p.kind == "suspension" and isinstance(target, User):
        target = find(Member, target.id)
    p.due_at = p.due_at or now + PAY_WITHIN
    if p.kind in ("fine", "restitution"):
        p.invoice = Invoice(id=next_id(Invoice, "INV"), payer=p.target, amount=p.amount, symbol=p.symbol,
                            due_at=p.due_at, reason=f"Penalty:{p.id}").id
    elif p.kind in TARGETS:
        target.status = {"suspension": "suspended", "revocation": "revoked", "termination": "terminated"}[p.kind]
    # freeze, injunction and community service are recorded here and carried out by their own operations.
    p.status, p.executed_at = "executed", now
    return {"penalty": p.id, "invoice": p.invoice}


def enforceable(args):
    v = find(Verdict, args["verdict"])
    if v.status not in ("issued", "final"):
        raise Fail(f"{v.id} is {v.status}")
    return v


@op("enforcement.record")
def enforcement_record(c, args):
    v = enforceable(args)
    return {"verdict": v.id, "note": args["note"]}


@op("enforcement.escalate")
def enforcement_escalate(c, args):
    v = enforceable(args)
    court = v.case.court
    return {"notification": notify(court.operator, "enforcement.escalate", f"{v.case.id}: enforcement escalated",
                                   args["note"], c.user)}


@op("notification.send")
def enforcement_notify(c, args):
    v = enforceable(args)
    to = as_ref(args["to"])
    deref(to)
    return {"notification": notify(to, "enforcement.notify", args.get("title", f"Verdict {v.id}"),
                                   args.get("body", v.decision), c.user)}


@op("org.create", governed=realm_wide)
def court_create(c, args):
    operator = args["operator"]
    if type(deref(operator)).__name__ not in ("Department", "Organization"):
        raise Fail("a court is run by a Department or an Organization")
    level = args.get("level", "first_instance")
    if level not in LEVELS:
        raise Fail(f"level must be one of {', '.join(LEVELS)}")
    parent = find(Court, args["parent"]) if args.get("parent") else None
    lic = active(operator, "court")
    if operator.startswith("Organization:") and lic is None:
        raise Fail(f"{operator} holds no active court licence")
    court = Court(id=next_id(Court, "COURT"), name=args["name"], operator=operator, level=level,
                  jurisdiction=args.get("jurisdiction", ""), parent=parent, license=lic.id if lic else "")
    return {"court": court.id}


@op("org.appoint", governed=realm_wide)
def judge_appoint(c, args):
    user, court = find(User, args["user"]), find(Court, args["court"])
    operating(court)
    if any(j.status == "active" and j.court and j.court.id == court.id for j in each(Judge, "user", user.id)):
        raise Fail(f"{user.id} already sits at {court.id}")
    j = Judge(id=next_id(Judge, "J"), user=user.id, court=court, specialization=args.get("specialization", ""),
              appointed_at=chain.now())
    return {"judge": j.id}


@op("org.appoint", governed=realm_wide)
def judge_revoke(c, args):
    j = find(Judge, args["judge"])
    if j.status != "active":
        raise Fail(f"{j.id} is {j.status}")
    j.status = "revoked"


@op("resolution.query", read=True)
def resolution_query(c, args):
    since, text, court = int(args.get("since", 0)), args.get("text", "").lower(), args.get("court")
    found = [v for v in each(Verdict) if v.status != "draft" and v.issued_at >= since
             and (not court or (v.case.court and v.case.court.id == court))
             and text in f"{v.decision} {v.case.title}".lower()]
    return {"verdicts": [verdict_view(v) for v in found]}
