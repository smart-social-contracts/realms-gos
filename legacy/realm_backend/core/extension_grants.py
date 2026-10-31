"""``extension_access.*`` verbs — who may use which extension.

``extensions_manager`` had six near-identical functions (grant/revoke against
user, department, profile), each re-implementing the caller's permission check
by walking ``profile.allowed_to`` itself. That is the realm's RBAC logic
living in extension code, where it can drift from the host's version and where
an extension update can quietly change it. Here it is one gate, applied by the
host, and the six functions become two verbs with a target kind.

Granting extension access is a privilege-escalation primitive, so both writes
require ``role.assign`` / ``role.revoke`` from the *caller*, checked here
rather than trusted from the manifest.
"""

TARGETS = ("user", "department", "profile")

VIEW_OPERATION = "permission.view"
GRANT_OPERATION = "role.assign"
REVOKE_OPERATION = "role.revoke"


def _require(caller: str, operation: str, verb: str) -> None:
    from core.extension_bridge import caller_has_operation

    if not caller_has_operation(caller, operation):
        raise PermissionError(f"{verb} requires the '{operation}' operation")


def _extension(name: str):
    from ggg import Extension

    if not name:
        raise ValueError("extension is required")
    ext = Extension[name]
    if not ext:
        raise ValueError(f"Extension '{name}' not found")
    return ext


def _target(kind: str, name: str):
    """Resolve a grant target, and say which collection holds the link.

    User grants live on the *user* (``user.extensions``); department and
    profile grants live on the *extension*. Returning the owning object with
    the collection keeps that asymmetry in one place.
    """
    from ggg import Department, User, UserProfile

    if kind not in TARGETS:
        raise ValueError(f"target must be one of {', '.join(TARGETS)}")
    if not name:
        raise ValueError(f"{kind} is required")

    if kind == "user":
        user = User[name]
        if not user:
            raise ValueError(f"User '{name}' not found")
        return user, "extensions"

    if kind == "department":
        dept = Department[name]
        if not dept:
            raise ValueError(f"Department '{name}' not found")
        return dept, "departments"

    profile = UserProfile[name]
    if not profile:
        raise ValueError(f"Profile '{name}' not found")
    return profile, "profiles"


def _link(ext, kind: str, target):
    """The collection the grant is recorded in, and the item to put in it."""
    if kind == "user":
        return target.extensions, ext
    return getattr(ext, "departments" if kind == "department" else "profiles"), target


def v_list(caller="", **kwargs) -> dict:
    """Every extension with the users, departments and profiles granted it.

    The reverse ``extension.users`` index was removed in issue #242, so direct
    user grants are found by one pass over the membership roster.
    """
    _require(caller, VIEW_OPERATION, "extension_access.list")
    from ggg import Extension

    users_by_extension: dict = {}
    try:
        from core.membership import iter_users

        for user in iter_users():
            principal = getattr(user, "id", None)
            if not principal:
                continue
            try:
                granted = list(user.extensions)
            except Exception:
                continue
            for ext in granted:
                users_by_extension.setdefault(ext.name, []).append({
                    "principal": principal,
                    "nickname": getattr(user, "nickname", "") or "",
                })
    except Exception:
        pass

    def names(collection):
        try:
            return sorted(item.name for item in collection)
        except Exception:
            return []

    extensions = [
        {
            "name": ext.name,
            "description": ext.description or "",
            "users": users_by_extension.get(ext.name, []),
            "departments": names(getattr(ext, "departments", [])),
            "profiles": names(getattr(ext, "profiles", [])),
        }
        for ext in Extension.instances()
    ]
    return {"extensions": extensions, "total": len(extensions)}


def v_grant(caller="", extension="", target="", name="", **kwargs) -> dict:
    """Grant *extension* to a user, department, or profile."""
    _require(caller, GRANT_OPERATION, "extension_access.grant")
    ext = _extension(extension)
    obj, _field = _target(target, name)
    collection, item = _link(ext, target, obj)
    collection.add(item)
    return {"extension": extension, "target": target, "name": name,
            "granted": True}


def v_revoke(caller="", extension="", target="", name="", **kwargs) -> dict:
    """Revoke *extension* from a user, department, or profile."""
    _require(caller, REVOKE_OPERATION, "extension_access.revoke")
    ext = _extension(extension)
    obj, _field = _target(target, name)
    collection, item = _link(ext, target, obj)
    collection.remove(item)
    return {"extension": extension, "target": target, "name": name,
            "revoked": True}


READS = {"extension_access.list": v_list}
WRITES = {
    "extension_access.grant": v_grant,
    "extension_access.revoke": v_revoke,
}
VERBS = dict(READS, **WRITES)
