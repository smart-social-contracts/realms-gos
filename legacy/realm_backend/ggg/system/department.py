from ic_python_db import (
    Boolean,
    Entity,
    Integer,
    ManyToMany,
    ManyToOne,
    OneToMany,
    OneToOne,
    String,
    TimestampedMixin,
)
from ic_python_logging import get_logger

logger = get_logger("entity.department")

# Reserved name for the quarter's top governing department (issue #240).
ROOT_ORG_NAME = "root"


class Department(Entity, TimestampedMixin):
    """Internal governance department within a quarter.

    Not to be confused with ``Organization`` (an external party the realm
    trades with). See https://github.com/smart-social-contracts/realms-gos/issues/240

    - Members are users only (no department nesting).
    - Policy (M/N, quorum, veto) governs how members exercise powers.
    - Optional fund link is the department's budget envelope.
    - Exactly one department named ``root`` with ``is_root=True`` per quarter.
    - Authority *over* other departments is modeled by ``DepartmentAuthority``,
      not by parent/child membership.
    """

    __alias__ = "name"
    __version__ = 3

    name = String(max_length=256)
    description = String(max_length=512)
    head = ManyToOne("User", "headed_departments")
    # The User→departments relation is unidirectional (issue #242): use
    # ``self.reverse_count("members")`` for the member count; membership checks
    # traverse forward (user.departments); list members via core.membership.
    permissions = ManyToMany(["Permission"], "departments")
    extensions = ManyToMany(["Extension"], "departments")
    # Deprecated: department nesting is forbidden (issue #240). Kept for schema
    # compatibility with existing data; create/update APIs must reject writes.
    parent = ManyToOne("Department", "sub_departments")
    sub_departments = OneToMany("Department", "parent")
    notifications = OneToMany("Notification", "department")

    # --- Department governance model (issue #240) ---
    is_root = Boolean(default=False)
    # Policy: require M approvals out of N eligible members (N=0 → use member count).
    policy_threshold_m = Integer(default=1)
    policy_threshold_n = Integer(default=1)
    # Minimum participation percent (0–100) among eligible members; 0 = no extra quorum.
    policy_quorum_percent = Integer(default=0)
    # Comma-separated principals that may veto an action under this department's policy.
    policy_veto_principals = String(max_length=2048, default="")
    # Stage-driven ratchet (issue #301): copied onto policy_* at beta when m > 0.
    target_policy_threshold_m = Integer(default=0)
    target_policy_threshold_n = Integer(default=0)
    target_policy_quorum_percent = Integer(default=0)
    # Budget envelope (governmental Fund).
    fund = OneToOne("Fund", "department")
    authorities_granted = OneToMany("DepartmentAuthority", "grantor")
    authorities_received = OneToMany("DepartmentAuthority", "target")

    @classmethod
    def migrate(cls, obj, from_version, to_version):
        if from_version < 2:
            obj.setdefault("is_root", (obj.get("name") or "") == ROOT_ORG_NAME)
            obj.setdefault("policy_threshold_m", 1)
            obj.setdefault("policy_threshold_n", 1)
            obj.setdefault("policy_quorum_percent", 0)
            obj.setdefault("policy_veto_principals", "")
        if from_version < 3:
            obj.setdefault("target_policy_threshold_m", 0)
            obj.setdefault("target_policy_threshold_n", 0)
            obj.setdefault("target_policy_quorum_percent", 0)
        return obj

    def __repr__(self):
        return f"Department(name={self.name!r}, is_root={self.is_root!r})"

    def veto_principal_list(self) -> list[str]:
        raw = self.policy_veto_principals or ""
        return [p.strip() for p in raw.split(",") if p.strip()]

    def delete(self):
        """Remove this department after detaching children.

        Bare ``Entity.delete`` left Positions, grants, memberships, invites,
        and sidebar visibility behind. ``access_manager.delete_department``
        calls this method.
        """
        if getattr(self, "is_root", False) or (self.name or "") == ROOT_ORG_NAME:
            raise ValueError("Cannot delete the root department")
        if not getattr(self, "_department_purge_done", False):
            from core.department_admin import purge_department_children

            self._department_purge_done = True
            purge_department_children(self)
        return super().delete()
