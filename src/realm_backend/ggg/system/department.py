from ic_python_db import Boolean, ManyToMany, ManyToOne, OneToMany, OneToOne, String

from db import Json, Row


class Department(Row):
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
