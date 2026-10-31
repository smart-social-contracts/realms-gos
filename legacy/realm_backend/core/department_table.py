"""Apply a live department-table JSON document.

The document is data — not a frozen Agora/Valencia org chart. Codex
``departments.json`` remains seed-only; this module is the founder apply path:

- upsert creates or updates a department, its posts, and staff
- ``action: destroy`` / ``delete`` (or ``destroy: true``) removes the
  department and children via ``destroy_department``
"""

from __future__ import annotations

import json
from typing import Any

from ic_python_logging import get_logger

logger = get_logger("core.department_table")

_STAFF_BASELINE_OPS = (
    "self.join,"
    "self.update_public_profile,"
    "self.update_private_data,"
    "self.change_quarter,"
    "extension.sync_call,"
    "extension.async_call"
)

_DESTROY_ACTIONS = frozenset({"destroy", "delete", "remove"})


def document_has_destroy(doc: Any) -> bool:
    """True when the table contains at least one destroy/delete row."""
    rows, _warnings = _normalize_rows(doc)
    return any(_is_destroy_row(row) for row in rows if isinstance(row, dict))


def apply_department_table(doc: Any) -> dict:
    """Upsert and/or destroy departments from a table document.

    Accepted shapes:
      ``{"departments": [row, ...]}``
      ``[row, ...]``
      a single row dict

    Each row needs ``name``. Default action is upsert. Destroy when
    ``action`` / ``_action`` is ``destroy`` or ``delete``, or ``destroy`` is
    true. Civil servants are ``members`` / ``staff``; posts are ``positions``.
    """
    rows, warnings = _normalize_rows(doc)
    created = []
    updated = []
    destroyed = []
    errors = []

    for row in rows:
        if not isinstance(row, dict):
            errors.append("skipped non-object row")
            continue
        name = _row_name(row)
        if not name:
            errors.append("row missing name")
            continue
        try:
            if _is_destroy_row(row):
                from core.department_admin import destroy_department

                result = destroy_department(name)
                if not result.get("success"):
                    errors.append(f"{name}: {result.get('error')}")
                    continue
                destroyed.append(name)
                continue
            result = upsert_department_row(row)
            if not result.get("success"):
                errors.append(f"{name}: {result.get('error')}")
                continue
            if result.get("created"):
                created.append(name)
            else:
                updated.append(name)
        except Exception as e:
            logger.error(f"apply_department_table row '{name}' failed: {e}")
            errors.append(f"{name}: {e}")

    success = not errors
    return {
        "success": success,
        "data": {
            "created": created,
            "updated": updated,
            "destroyed": destroyed,
            "errors": errors,
            "warnings": warnings,
        },
        "error": errors[0] if errors else None,
    }


def upsert_department_row(row: dict) -> dict:
    """Create or update one department and apply staff / posts / grants."""
    from ggg import Department

    name = _row_name(row)
    if not name:
        return {"success": False, "error": "name is required"}

    existing = Department[name]
    created = existing is None
    dept = existing or Department(
        name=name,
        description=str(row.get("description") or ""),
        is_root=bool(row.get("is_root")) or name == "root",
    )

    if "description" in row:
        dept.description = str(row.get("description") or "")

    _apply_policy(dept, row)
    _apply_head(dept, row)
    _apply_fund(dept, row)
    _ensure_profiles(row)
    _apply_positions(dept, row)
    _apply_permissions(dept, row)
    _apply_extensions(dept, row)
    _apply_hidden_extensions(dept, row)
    staff_error = _apply_staff(dept, row)
    if staff_error:
        return {"success": False, "error": staff_error, "created": created}

    return {"success": True, "created": created, "data": {"name": name}}


def _normalize_rows(doc: Any) -> tuple[list, list[str]]:
    warnings: list[str] = []
    if isinstance(doc, str):
        try:
            doc = json.loads(doc) if doc.strip() else {}
        except Exception:
            return [], ["document is not valid JSON"]
    if doc is None:
        return [], []
    if isinstance(doc, dict):
        if "departments" in doc:
            rows = doc.get("departments") or []
            if not isinstance(rows, list):
                return [], ["departments must be a list"]
            return rows, warnings
        return [doc], warnings
    if isinstance(doc, list):
        return doc, warnings
    return [], ["document must be an object or list"]


def _row_name(row: dict) -> str:
    return str(row.get("name") or row.get("_id") or "").strip()


def _is_destroy_row(row: dict) -> bool:
    if row.get("destroy") is True:
        return True
    action = str(row.get("action") or row.get("_action") or "").strip().lower()
    return action in _DESTROY_ACTIONS


def _apply_policy(dept, row: dict) -> None:
    policy = row.get("policy") if isinstance(row.get("policy"), dict) else {}
    if "threshold_m" in policy or "policy_threshold_m" in row:
        dept.policy_threshold_m = int(
            policy.get("threshold_m", row.get("policy_threshold_m", 1)) or 1
        )
    if "threshold_n" in policy or "policy_threshold_n" in row:
        dept.policy_threshold_n = int(
            policy.get("threshold_n", row.get("policy_threshold_n", 1)) or 1
        )
    if "quorum_percent" in policy or "policy_quorum_percent" in row:
        dept.policy_quorum_percent = int(
            policy.get("quorum_percent", row.get("policy_quorum_percent", 0)) or 0
        )
    if "veto_principals" in policy or "policy_veto_principals" in row:
        veto = policy.get("veto_principals", row.get("policy_veto_principals", ""))
        if isinstance(veto, list):
            dept.policy_veto_principals = ",".join(
                str(x).strip() for x in veto if str(x).strip()
            )
        else:
            dept.policy_veto_principals = str(veto or "")

    target = (
        row.get("target_policy") if isinstance(row.get("target_policy"), dict) else None
    )
    if not target:
        return
    if "threshold_m" in target:
        dept.target_policy_threshold_m = int(target.get("threshold_m") or 0)
    if "threshold_n" in target:
        dept.target_policy_threshold_n = int(target.get("threshold_n") or 0)
    if "quorum_percent" in target:
        dept.target_policy_quorum_percent = int(target.get("quorum_percent") or 0)


def _apply_head(dept, row: dict) -> None:
    principal = row.get("head") or row.get("head_principal")
    if not principal:
        return
    from ggg import User

    user = User[str(principal).strip()]
    if user:
        dept.head = user
        try:
            from core.membership import add_department_member

            add_department_member(dept, user)
        except Exception as e:
            logger.warning(f"head membership: {e}")


def _apply_fund(dept, row: dict) -> None:
    fund_code = str(row.get("fund_code") or "").strip()
    if not fund_code:
        return
    try:
        from ggg import Fund, FundType
    except ImportError:
        return
    fund = Fund[fund_code[:16]]
    if not fund:
        fund = Fund(
            code=fund_code[:16],
            name=row.get("fund_name") or f"{dept.name} Fund",
            fund_type=getattr(FundType, "SPECIAL_REVENUE", "special_revenue"),
            description=f"Budget for department {dept.name}",
        )
    dept.fund = fund


def _position_specs(row: dict) -> list[dict]:
    positions = row.get("positions")
    if positions:
        specs = []
        for item in positions:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("profile") or "").strip()
            profile = str(item.get("profile") or item.get("title") or "").strip()
            if not title:
                continue
            specs.append(
                {
                    "title": title,
                    "profile": profile or title,
                    "headcount": int(item.get("headcount", 1) or 1),
                    "salary_amount": int(item.get("salary_amount", 0) or 0),
                    "salary_period": item.get("salary_period") or "monthly",
                    "description": item.get("description") or "",
                }
            )
        return specs
    profiles = row.get("profiles")
    if not isinstance(profiles, list):
        return []
    return [
        {
            "title": str(p).strip(),
            "profile": str(p).strip(),
            "headcount": 1,
            "salary_amount": 0,
            "salary_period": "monthly",
            "description": "",
        }
        for p in profiles
        if str(p).strip()
    ]


def _ensure_profiles(row: dict) -> None:
    from ggg import UserProfile

    names = {spec["profile"] for spec in _position_specs(row)}
    for member in _staff_entries(row):
        profile = member.get("profile")
        if profile:
            names.add(profile)
    for pname in names:
        if UserProfile[pname]:
            continue
        UserProfile(
            name=pname,
            allowed_to=_STAFF_BASELINE_OPS,
            description=f"{pname} staff profile",
        )


def _apply_positions(dept, row: dict) -> None:
    from core.position_admin import apply_position_action

    name = dept.name
    for spec in _position_specs(row):
        key = f"{name}/{spec['title']}"
        from ggg import Position

        existing = Position[key]
        if existing:
            apply_position_action(
                {
                    "action": "update",
                    "key": key,
                    "profile": spec["profile"],
                    "headcount": spec["headcount"],
                    "salary_amount": spec["salary_amount"],
                    "salary_period": spec["salary_period"],
                    **(
                        {"description": spec["description"]}
                        if spec["description"]
                        else {}
                    ),
                }
            )
            continue
        result = apply_position_action(
            {
                "action": "create",
                "department": name,
                "title": spec["title"],
                "profile": spec["profile"],
                "headcount": spec["headcount"],
                "salary_amount": spec["salary_amount"],
                "salary_period": spec["salary_period"],
                "description": spec["description"] or f"{spec['title']} at {name}",
            }
        )
        if not result.get("success"):
            raise ValueError(result.get("error") or f"position {key} failed")


def _apply_permissions(dept, row: dict) -> None:
    names = row.get("permissions") or []
    if not isinstance(names, list):
        return
    try:
        from ggg import Permission
    except ImportError:
        return
    rel = getattr(dept, "permissions", None)
    have = {getattr(p, "name", None) for p in (rel or [])}
    for perm_name in names:
        perm_name = str(perm_name).strip()
        if not perm_name or perm_name in have:
            continue
        perm = Permission[perm_name] or Permission(name=perm_name)
        if rel is not None:
            try:
                rel.add(perm)
            except Exception as e:
                logger.warning(f"permission grant {perm_name}: {e}")
        other = getattr(perm, "departments", None)
        if other is not None:
            try:
                other.add(dept)
            except Exception:
                pass
        have.add(perm_name)


def _apply_extensions(dept, row: dict) -> None:
    names = row.get("extensions") or []
    if not isinstance(names, list):
        return
    try:
        from ggg import Extension
    except ImportError:
        return
    for ext_id in names:
        ext = Extension[str(ext_id).strip()]
        if not ext:
            continue
        rel = getattr(ext, "departments", None)
        if rel is None:
            continue
        try:
            if not any(getattr(d, "name", None) == dept.name for d in rel):
                rel.add(dept)
        except Exception as e:
            logger.warning(f"extension grant {ext_id}: {e}")


def _apply_hidden_extensions(dept, row: dict) -> None:
    names = row.get("hidden_extensions") or []
    if not isinstance(names, list):
        return
    try:
        from ggg import MenuDepartmentVisibility
    except ImportError:
        return
    existing = set()
    try:
        for rule in MenuDepartmentVisibility.instances():
            linked = getattr(rule, "department", None)
            if getattr(linked, "name", None) == dept.name:
                existing.add(getattr(rule, "extension_name", None))
    except Exception:
        pass
    for ext_id in names:
        ext_id = str(ext_id).strip()
        if not ext_id or ext_id in existing:
            continue
        MenuDepartmentVisibility(extension_name=ext_id, department=dept, visible=False)
        existing.add(ext_id)


def _staff_entries(row: dict) -> list[dict]:
    raw = row.get("members")
    if raw is None:
        raw = row.get("staff")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raw = [raw]
    entries = []
    for item in raw:
        if isinstance(item, str):
            principal = item.strip()
            if principal:
                entries.append({"principal": principal})
            continue
        if not isinstance(item, dict):
            continue
        principal = str(
            item.get("principal") or item.get("user_principal") or item.get("id") or ""
        ).strip()
        if not principal:
            continue
        entries.append(
            {
                "principal": principal,
                "profile": str(item.get("profile") or "").strip() or None,
                "title": str(item.get("position") or item.get("title") or "").strip()
                or None,
            }
        )
    return entries


def _apply_staff(dept, row: dict) -> str | None:
    from core.membership import user_has_profile
    from core.org_member_admin import apply_member_action
    from core.position_admin import apply_position_action
    from ggg import User, UserProfile

    for entry in _staff_entries(row):
        principal = entry["principal"]
        result = apply_member_action(
            {
                "action": "add",
                "department": dept.name,
                "user_principal": principal,
            }
        )
        if not result.get("success"):
            return result.get("error") or f"could not add {principal}"

        user = User[principal]
        profile_name = entry.get("profile")
        if profile_name and user:
            profile = UserProfile[profile_name]
            if profile and not user_has_profile(user, profile):
                try:
                    user.profiles.add(profile)
                except Exception as e:
                    logger.warning(f"assign profile {profile_name}: {e}")

        title = entry.get("title")
        if title:
            key = title if "/" in title else f"{dept.name}/{title}"
            appointed = apply_position_action(
                {"action": "appoint", "key": key, "principal": principal}
            )
            if not appointed.get("success"):
                return appointed.get("error") or f"could not appoint {principal}"
    return None
