from ic_python_db import Boolean, Entity, Integer, ManyToMany, ManyToOne, OneToMany, OneToOne, String

from db import Json


class Department(Entity):
    __alias__ = "id"
    id = String()  # name
    description = String(default="")
    parent = ManyToOne("Department", "children")
    children = OneToMany("Department", "parent")
    is_root = Boolean(default=False)
    head = String(default="")
    policy = Json({"m": 1, "n": 1, "quorum_percent": 0, "veto": []})
    target_policy = Json({})
    fund = OneToOne("Fund", "department")
    permissions = ManyToMany(["Permission"], "departments")
    positions = OneToMany("Position", "department")
    authorities = OneToMany("Authority", "grantor")
    proposals = OneToMany("Proposal", "department")
    mandates = OneToMany("Mandate", "executor")
    licenses_issued = OneToMany("License", "issued_by")
    tenders = OneToMany("Tender", "requester")


class Organization(Entity):
    __alias__ = "id"
    id = String()  # name
    description = String(default="")
    status = String(default="active")  # active | suspended | dissolved
    admins = Json([])
    positions = OneToMany("Position", "organization")
    bids = OneToMany("Bid", "vendor")
    ledger_entries = OneToMany("LedgerEntry", "organization")


class Position(Entity):
    __alias__ = "id"
    id = String()  # key, e.g. "civil-registry.clerk"
    department = ManyToOne("Department", "positions")
    organization = ManyToOne("Organization", "positions")
    title = String(default="")
    profile = ManyToOne("UserProfile", "positions")
    headcount = Integer(default=1)
    salary = Integer(default=0)
    salary_period = String(default="monthly")
    status = String(default="open")  # open | frozen | closed
    inherit_from_capital = Boolean(default=True)
    appointments = OneToMany("Appointment", "position")


class Appointment(Entity):
    __alias__ = "id"
    id = String()
    position = ManyToOne("Position", "appointments")
    user = String(indexed=True)
    kind = String(default="substantive")  # substantive | acting
    agent = Boolean(default=False)
    supervisor = String(default="")
    started_at = Integer(default=0)
    ended_at = Integer(default=0)
    status = String(default="active")  # active | ended
    source_quarter = String(default="")


class Authority(Entity):
    __alias__ = "id"
    id = String()
    grantor = ManyToOne("Department", "authorities")
    target = String(default="")  # "Department:x" or "Quarter:canister-id"
    operations = Json([])
