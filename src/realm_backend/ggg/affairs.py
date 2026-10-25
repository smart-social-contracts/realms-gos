from ic_python_db import Integer, ManyToMany, ManyToOne, OneToMany, String

from db import Json, Row


class Land(Row):
    __alias__ = "id"
    id = String()
    x = Integer(default=0)
    y = Integer(default=0)
    width = Integer(default=1)
    height = Integer(default=1)
    kind = String(default="unassigned")
    owner = String(default="", indexed=True)  # "User:alice" or "Organization:buildco"
    status = String(default="active")  # active | disputed | transferred | revoked
    token_id = String(default="")
    registered_by = String(default="")
    metadata = Json({})
    zones = OneToMany("Zone", "land")


class Zone(Row):
    __alias__ = "id"
    id = String()  # h3 index
    name = String(default="")
    kind = String(default="unassigned")
    land = ManyToOne("Land", "zones")
    user = String(default="", indexed=True)
    metadata = Json({})


class License(Row):
    __alias__ = "id"
    id = String()
    kind = String(default="")  # court | business | professional | health | education | ...
    holder = String(indexed=True)  # "Organization:clinic-b" or "User:dr-ana"
    issued_by = ManyToOne("Department", "licenses_issued")
    status = String(default="applied")  # applied | active | rejected | suspended | revoked | expired
    applied_at = Integer(default=0)
    issued_at = Integer(default=0)
    expires_at = Integer(default=0)
    evidence_ref = String(default="")
    conditions = Json({})


class Court(Row):
    __alias__ = "id"
    id = String()
    name = String(default="")
    operator = String(default="")  # "Department:justice" or "Organization:arbitra"
    level = String(default="first_instance")  # first_instance | appellate | supreme | specialized
    jurisdiction = String(default="")
    parent = ManyToOne("Court", "children")
    children = OneToMany("Court", "parent")
    status = String(default="active")
    license = String(default="")
    judges = OneToMany("Judge", "court")
    cases = OneToMany("Case", "court")
    appeals = OneToMany("Appeal", "court")


class Judge(Row):
    __alias__ = "id"
    id = String()
    user = String(indexed=True)
    court = ManyToOne("Court", "judges")
    specialization = String(default="")
    status = String(default="active")
    appointed_at = Integer(default=0)
    cases = ManyToMany(["Case"], "judges")
    verdicts = OneToMany("Verdict", "issued_by")


class Dispute(Row):
    __alias__ = "id"
    id = String()
    claimant = String(indexed=True)
    respondent = String(default="")
    subject = String(default="")
    title = String(default="")
    description_ref = String(default="")
    status = String(default="filed", indexed=True)  # filed | accepted | rejected | withdrawn
    case = String(default="")


class Case(Row):
    __alias__ = "id"
    id = String()
    court = ManyToOne("Court", "cases")
    plaintiff = String(default="")
    defendant = String(default="")
    judges = ManyToMany(["Judge"], "cases")
    title = String(default="")
    dispute = String(default="")
    status = String(default="filed")  # filed | assigned | in_progress | verdict_issued | appealed | closed | dismissed | transferred
    evidence = Json([])
    filed_at = Integer(default=0)
    closed_at = Integer(default=0)
    verdicts = OneToMany("Verdict", "case")
    appeals = OneToMany("Appeal", "case")
    ledger_entries = OneToMany("LedgerEntry", "case")


class Verdict(Row):
    __alias__ = "id"
    id = String()
    case = ManyToOne("Case", "verdicts")
    issued_by = ManyToOne("Judge", "verdicts")
    decision = String(default="")
    reasoning_ref = String(default="")
    status = String(default="draft")  # draft | issued | final | overturned
    contract = String(default="")
    issued_at = Integer(default=0)
    penalties = OneToMany("Penalty", "verdict")


class Appeal(Row):
    __alias__ = "id"
    id = String()
    case = ManyToOne("Case", "appeals")
    verdict = String(default="")
    court = ManyToOne("Court", "appeals")
    appellant = String(default="")
    grounds_ref = String(default="")
    status = String(default="filed")  # filed | under_review | granted | denied | withdrawn
    decision = String(default="")
    new_verdict = String(default="")
    ledger_entries = OneToMany("LedgerEntry", "appeal")


class Penalty(Row):
    __alias__ = "id"
    id = String()
    verdict = ManyToOne("Verdict", "penalties")
    target = String(default="")
    # fine | restitution | community_service | suspension | revocation | injunction | freeze | termination
    kind = String(default="fine")
    amount = Integer(default=0)
    symbol = String(default="")
    status = String(default="pending")  # pending | executed | waived
    due_at = Integer(default=0)
    executed_at = Integer(default=0)
    invoice = String(default="")
    ledger_entries = OneToMany("LedgerEntry", "penalty")


class Tender(Row):
    __alias__ = "id"
    id = String()
    title = String(default="")
    description = String(default="")
    requester = ManyToOne("Department", "tenders")
    status = String(default="draft")  # draft | open | closed | evaluation | awarded | contracted | cancelled
    opens_at = Integer(default=0)
    closes_at = Integer(default=0)
    rubric = Json({})  # {"price": 60, "quality": 40}
    winner = String(default="")
    contract = String(default="")
    bids = OneToMany("Bid", "tender")


class Bid(Row):
    __alias__ = "id"
    id = String()
    tender = ManyToOne("Tender", "bids")
    vendor = ManyToOne("Organization", "bids")
    submitted_at = Integer(default=0)
    sealed = Integer(default=1)
    payload_ref = String(default="")
    payload_hash = String(default="")
    price = Integer(default=0)  # revealed with the bid
    score = Integer(default=0)
    scores = OneToMany("BidScore", "bid")


class BidScore(Row):
    __alias__ = "id"
    id = String()
    bid = ManyToOne("Bid", "scores")
    evaluator = String(default="")
    criterion = String(default="")
    score = Integer(default=0)
    scored_at = Integer(default=0)
