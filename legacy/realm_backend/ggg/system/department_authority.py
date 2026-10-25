from ic_python_db import Entity, ManyToOne, String, TimestampedMixin
from ic_python_logging import get_logger

logger = get_logger("entity.department_authority")


class DepartmentAuthority(Entity, TimestampedMixin):
    """Permission grant from one department over another (issue #240).

    Local target: set ``target`` to a Department on this quarter.
    Remote target (capital → other quarter): leave ``target`` empty and set
    ``target_quarter_canister_id`` + ``target_org_name``.

    ``permissions`` is a comma-separated list of operations, e.g.
    ``org.appoint,org.expel,org.set_policy``.
    """

    __alias__ = "id"
    id = String(max_length=64)
    grantor = ManyToOne("Department", "authorities_granted")
    target = ManyToOne("Department", "authorities_received")
    # Remote (cross-quarter) target — used when target is on another canister.
    target_quarter_canister_id = String(max_length=64, default="")
    target_org_name = String(max_length=256, default="")
    permissions = String(max_length=1024, default="")
    description = String(max_length=512, default="")

    def __repr__(self):
        return f"DepartmentAuthority(id={self.id!r})"

    def permission_list(self) -> list[str]:
        raw = self.permissions or ""
        return [p.strip() for p in raw.split(",") if p.strip()]
