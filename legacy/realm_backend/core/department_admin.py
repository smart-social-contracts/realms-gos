"""Department destroy with child cleanup.

``access_manager.delete_department`` historically called ``dept.delete()``
and left Positions, grants, memberships, invites, and sidebar rows behind.
``destroy_department`` (and ``Department.delete``) detach or remove those
children so the department name disappears from list APIs.
"""

from __future__ import annotations

from typing import Any, Iterable

from ic_python_logging import get_logger

logger = get_logger("core.department_admin")


def list_department_names() -> list[str]:
    """Names returned by UI list scans (``Department.instances()``)."""
    from ggg import Department

    names = []
    for dept in Department.instances():
        name = getattr(dept, "name", None)
        if name:
            names.append(str(name))
    names.sort()
    return names


def destroy_department(name: str) -> dict:
    """Remove a department and detach/delete its children.

    Refuses ``root``. Missing departments succeed (idempotent).
    """
    from ggg import Department

    name = (name or "").strip()
    if not name:
        return {"success": False, "error": "name is required"}

    root_name = "root"
    try:
        from ggg import ROOT_ORG_NAME

        if isinstance(ROOT_ORG_NAME, str) and ROOT_ORG_NAME:
            root_name = ROOT_ORG_NAME
    except Exception:
        pass

    dept = Department[name]
    if not dept:
        return {
            "success": True,
            "data": {"name": name, "message": f"Department '{name}' already gone"},
        }

    if getattr(dept, "is_root", False) or name == root_name:
        return {"success": False, "error": "Cannot delete the root department"}

    purge_department_children(dept)
    _mark_purged(dept)
    dept.delete()
    logger.info(f"Department '{name}' destroyed")
    return {
        "success": True,
        "data": {"name": name, "message": f"Department '{name}' deleted"},
    }


def purge_department_children(dept) -> None:
    """Detach or delete everything that would orphan after ``dept`` is gone."""
    name = str(getattr(dept, "name", "") or "")
    if not name:
        return

    _purge_positions(name)
    _purge_memberships(dept, name)
    _clear_head(dept)
    _purge_permission_grants(dept, name)
    _purge_extension_grants(dept, name)
    _purge_menu_visibility(dept, name)
    _purge_invite_codes(name)
    _purge_authorities(dept, name)
    _purge_notifications(dept, name)
    _detach_fund(dept)


def _mark_purged(dept) -> None:
    try:
        dept._department_purge_done = True
    except Exception:
        pass


def _iter_rel(obj, attr: str) -> list:
    rel = getattr(obj, attr, None)
    if rel is None:
        return []
    try:
        return list(rel)
    except Exception:
        return []


def _remove_named(rel, item, name: str) -> None:
    if rel is None:
        return
    try:
        members = list(rel)
    except Exception:
        return
    for entry in members:
        if entry is item or getattr(entry, "name", None) == name:
            try:
                rel.remove(entry)
            except Exception:
                pass


def _purge_positions(department_name: str) -> None:
    try:
        from ggg import Appointment, Position
    except ImportError:
        return

    positions: Iterable[Any]
    try:
        positions = list(Position.for_department(department_name))
    except Exception:
        positions = [
            pos
            for pos in Position.instances()
            if getattr(getattr(pos, "department", None), "name", None)
            == department_name
        ]

    keys = {getattr(pos, "key", None) for pos in positions}
    keys.discard(None)

    try:
        appointments = list(Appointment.instances())
    except Exception:
        appointments = []

    for appt in appointments:
        pos = getattr(appt, "position", None)
        pos_key = getattr(pos, "key", None) if pos is not None else None
        if pos in positions or pos_key in keys:
            try:
                appt.delete()
            except Exception as e:
                logger.warning(f"appointment delete failed: {e}")

    for pos in positions:
        try:
            pos.delete()
        except Exception as e:
            logger.warning(f"position delete failed: {e}")


def _purge_memberships(dept, name: str) -> None:
    try:
        from core.membership import (
            department_members,
            remove_department_member,
            user_in_department,
        )
        from ggg import User
    except ImportError:
        return

    members = []
    try:
        members = list(department_members(dept))
    except Exception:
        members = []

    try:
        users = list(User.instances())
    except Exception:
        users = []

    seen = {id(u) for u in members}
    for user in users:
        if id(user) in seen:
            continue
        try:
            if user_in_department(user, dept) or user_in_department(user, name):
                members.append(user)
        except Exception:
            continue

    for user in members:
        try:
            remove_department_member(dept, user)
        except Exception:
            _remove_named(getattr(user, "departments", None), dept, name)


def _clear_head(dept) -> None:
    try:
        dept.head = None
    except Exception:
        pass


def _purge_permission_grants(dept, name: str) -> None:
    try:
        from ggg import Permission
    except ImportError:
        Permission = None

    for perm in _iter_rel(dept, "permissions"):
        _remove_named(getattr(perm, "departments", None), dept, name)
        try:
            dept.permissions.remove(perm)
        except Exception:
            pass

    if Permission is None:
        return
    try:
        perms = list(Permission.instances())
    except Exception:
        return
    for perm in perms:
        _remove_named(getattr(perm, "departments", None), dept, name)


def _purge_extension_grants(dept, name: str) -> None:
    try:
        from ggg import Extension
    except ImportError:
        Extension = None

    for ext in _iter_rel(dept, "extensions"):
        _remove_named(getattr(ext, "departments", None), dept, name)
        try:
            dept.extensions.remove(ext)
        except Exception:
            pass

    if Extension is None:
        return
    try:
        exts = list(Extension.instances())
    except Exception:
        return
    for ext in exts:
        _remove_named(getattr(ext, "departments", None), dept, name)


def _purge_menu_visibility(dept, name: str) -> None:
    try:
        from ggg import MenuDepartmentVisibility
    except ImportError:
        return
    try:
        rules = list(MenuDepartmentVisibility.instances())
    except Exception:
        return
    for rule in rules:
        linked = getattr(rule, "department", None)
        if linked is dept or getattr(linked, "name", None) == name:
            try:
                rule.delete()
            except Exception as e:
                logger.warning(f"menu visibility delete failed: {e}")


def _purge_invite_codes(name: str) -> None:
    try:
        from ggg import RegistrationCode
    except ImportError:
        return
    try:
        codes = list(RegistrationCode.instances())
    except Exception:
        return
    prefix = f"{name}/"
    for code in codes:
        dept_field = getattr(code, "department", "") or ""
        position_field = getattr(code, "position", "") or ""
        if dept_field == name or position_field.startswith(prefix):
            try:
                code.delete()
            except Exception as e:
                logger.warning(f"invite delete failed: {e}")


def _purge_authorities(dept, name: str) -> None:
    try:
        from ggg import DepartmentAuthority
    except ImportError:
        return
    try:
        rows = list(DepartmentAuthority.instances())
    except Exception:
        return
    for auth in rows:
        grantor = getattr(auth, "grantor", None)
        target = getattr(auth, "target", None)
        target_name = getattr(auth, "target_org_name", "") or ""
        if (
            grantor is dept
            or target is dept
            or getattr(grantor, "name", None) == name
            or getattr(target, "name", None) == name
            or target_name == name
        ):
            try:
                auth.delete()
            except Exception as e:
                logger.warning(f"authority delete failed: {e}")


def _purge_notifications(dept, name: str) -> None:
    try:
        from ggg import Notification
    except ImportError:
        return
    try:
        notes = list(Notification.instances())
    except Exception:
        return
    for note in notes:
        linked = getattr(note, "department", None)
        if linked is dept or getattr(linked, "name", None) == name:
            try:
                note.delete()
            except Exception as e:
                logger.warning(f"notification delete failed: {e}")


def _detach_fund(dept) -> None:
    try:
        dept.fund = None
    except Exception:
        pass
