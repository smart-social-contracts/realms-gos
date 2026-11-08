from ic_python_db import Boolean, ManyToMany, ManyToOne, OneToMany, OneToOne, String

from core.db import Json, Row
from core.ratio import PERCENT


class Department(Row):
    __alias__ = "id"
    __version__ = 2  # 2: a policy's quorum in hundred-millionths, not whole percents
    id = String()  # name
    description = String(default="")
    kind = String(default="")  # what the body is (plenary, committee), for legislation from another source to address
    parent = ManyToOne("Department", "children")
    children = OneToMany("Department", "parent")
    is_root = Boolean(default=False, indexed=True)
    head = String(default="")
    policy = Json({"m": 1, "n": 1, "quorum": 0, "veto": []})
    target_policy = Json({})
    fund = OneToOne("Fund", "department")
    permissions = ManyToMany(["Permission"], "departments")
    positions = OneToMany("Position", "department")
    authorities = OneToMany("Authority", "grantor")
    proposals = OneToMany("Proposal", "department")
    mandates = OneToMany("Mandate", "executor")
    licenses_issued = OneToMany("License", "issued_by")
    tenders = OneToMany("Tender", "requester")

    @classmethod
    def migrate(cls, obj, from_version, to_version):
        for field in ("policy", "target_policy"):
            policy = obj.get(field) or {}
            # quorum_percent, the old key, shows a policy still in whole percents.
            if "quorum_percent" in policy:
                policy["quorum"] = policy.pop("quorum_percent") * PERCENT
        return obj
