"""Reverse lookups for unidirectional user relations (issue #242).

``User.profiles``, ``User.departments``, and ``User.extensions`` are
unidirectional many-to-many relations: the user side keeps a normal (small)
forward index while the target side maintains only an O(1) counter
(``entity.reverse_count(relation)``) instead of an ID array that would be
rewritten on every registration.

Consequences:
  - Membership *checks* must traverse forward from the user (cheap).
  - Member/holder *counts* read the reverse counter (cheap).
  - Member/holder *listing* is a paginated scan over users. That is O(all
    users) and reserved for admin/display paths — never call it from hot
    request paths.
"""

from typing import Callable, Iterator, List, Optional

from ic_python_logging import get_logger

logger = get_logger("core.membership")

# Page size for user scans; keeps per-iteration memory bounded.
_SCAN_PAGE = 100


def iter_users() -> Iterator:
    from ggg import User

    max_id = User.max_id()
    from_id = 1
    while from_id <= max_id:
        batch = User.load_some(from_id=from_id, count=_SCAN_PAGE)
        for u in batch:
            yield u
        from_id += _SCAN_PAGE


def _scan_users(predicate: Callable, limit: Optional[int] = None) -> List:
    result = []
    for user in iter_users():
        try:
            if predicate(user):
                result.append(user)
                if limit is not None and len(result) >= limit:
                    break
        except Exception:
            continue
    return result


# ---------------------------------------------------------------------------
# Departments
# ---------------------------------------------------------------------------


def user_department_names(user) -> List[str]:
    """Forward membership: department names this user belongs to."""
    if user is None:
        return []
    try:
        names = []
        for dept in user.departments:
            name = getattr(dept, "name", None)
            if name:
                names.append(str(name))
        return names
    except Exception:
        return []


def user_in_department(user, dept) -> bool:
    """Forward membership check: is *user* a member of *dept*?

    ``dept`` may be a Department entity or a department name.
    """
    if user is None or dept is None:
        return False
    dept_name = dept if isinstance(dept, str) else getattr(dept, "name", None)
    if not dept_name:
        return False
    try:
        return any(getattr(d, "name", None) == dept_name for d in user.departments)
    except Exception:
        return False


def department_members(dept, limit: Optional[int] = None) -> List:
    """List User entities that are members of *dept* (paginated user scan).

    Admin/display paths only.
    """
    dept_name = dept if isinstance(dept, str) else getattr(dept, "name", None)
    if not dept_name:
        return []
    return _scan_users(
        lambda u: any(getattr(d, "name", None) == dept_name for d in u.departments),
        limit=limit,
    )


def department_member_principals(dept, include_head: bool = True) -> List[str]:
    """Principals of *dept* members (optionally including the head)."""
    principals: List[str] = []
    for m in department_members(dept):
        pid = getattr(m, "id", None)
        if pid and str(pid) not in principals:
            principals.append(str(pid))
    if include_head and not isinstance(dept, str):
        head = getattr(dept, "head", None)
        if head is not None:
            hid = getattr(head, "id", None)
            if hid and str(hid) not in principals:
                principals.append(str(hid))
    return principals


def department_member_count(dept) -> int:
    """O(1) member count from the reverse counter."""
    try:
        return int(dept.reverse_count("members"))
    except Exception:
        return 0


def add_department_member(dept, user) -> None:
    """Idempotently add *user* to *dept* via the forward relation."""
    if not user_in_department(user, dept):
        user.departments.add(dept)
        try:
            from core import dept_docs_bridge

            dept_docs_bridge.on_member_added(dept, user)
        except Exception as e:
            logger.warning(f"dept_docs on_member_added: {e}")


def remove_department_member(dept, user) -> None:
    """Remove *user* from *dept* via the forward relation."""
    user.departments.remove(dept)


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------


def user_has_profile(user, profile) -> bool:
    if user is None or profile is None:
        return False
    profile_name = (
        profile if isinstance(profile, str) else getattr(profile, "name", None)
    )
    if not profile_name:
        return False
    try:
        return any(getattr(p, "name", None) == profile_name for p in user.profiles)
    except Exception:
        return False


def users_with_profile(profile, limit: Optional[int] = None) -> List:
    """List User entities holding *profile* (paginated user scan)."""
    profile_name = (
        profile if isinstance(profile, str) else getattr(profile, "name", None)
    )
    if not profile_name:
        return []
    return _scan_users(
        lambda u: any(getattr(p, "name", None) == profile_name for p in u.profiles),
        limit=limit,
    )


def profile_user_count(profile) -> int:
    """O(1) holder count from the reverse counter."""
    try:
        return int(profile.reverse_count("users"))
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Extensions (direct user grants)
# ---------------------------------------------------------------------------


def user_has_extension_grant(user, ext) -> bool:
    if user is None or ext is None:
        return False
    ext_name = ext if isinstance(ext, str) else getattr(ext, "name", None)
    if not ext_name:
        return False
    try:
        return any(getattr(e, "name", None) == ext_name for e in user.extensions)
    except Exception:
        return False


def users_with_extension(ext, limit: Optional[int] = None) -> List:
    """List User entities with a direct grant on *ext* (paginated user scan)."""
    ext_name = ext if isinstance(ext, str) else getattr(ext, "name", None)
    if not ext_name:
        return []
    return _scan_users(
        lambda u: any(getattr(e, "name", None) == ext_name for e in u.extensions),
        limit=limit,
    )


def extension_user_grant_count(ext) -> int:
    """O(1) direct-grant count from the reverse counter."""
    try:
        return int(ext.reverse_count("users"))
    except Exception:
        return 0
