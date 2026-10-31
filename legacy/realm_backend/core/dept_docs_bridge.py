"""``department.*`` and ``dept_doc.*`` verbs (issue #271).

Encrypted documents shared with a department. The canister never sees plaintext:
``ciphertext`` is an opaque AES-GCM blob, and who can decrypt it is governed by
``KeyEnvelope`` records at scope ``dept:<department>:doc:<id>``, managed through
the realm's existing generic grant/revoke endpoints. None of that changes here.

What changes is who decides *access to the record*. In-process, the extension
made those calls itself — ``_can_manage_department`` and ``_can_view_department``
lived in ``entry.py``. Those are the checks that decide whether one department
can read another's documents, so leaving them in extension code and giving the
sandbox generic ``ext_entity.*`` CRUD over the same rows would have been a
downgrade dressed as a port: the extension would still be the thing deciding.

So the documents stay extension-owned storage, but every read is projected and
every write is checked here, against the same two roles as before:

* **manage** (create, retitle, attach ciphertext, delete) — department head or
  realm admin, via ``core.crypto_scopes``' policy, which is the same policy the
  key-envelope grants use.
* **view** (list, get) — anyone in the department, plus managers.

Returning ciphertext to any department member is deliberate and unchanged: it is
encrypted, and holding it is useless without a KeyEnvelope.
"""

import json
from typing import Any, Dict, List, Optional

from ic_python_logging import get_logger
from core.time_utils import format_timestamp_ms, now_ms

logger = get_logger("core.dept_docs_bridge")

EXT_ID = "department_docs"
DOC_TYPE = "DepartmentDocument"
RESHARE_JOB_TYPE = "ReshareJob"
TITLE_MAX_LENGTH = 512


def _clean_title(title: str) -> str:
    """Strip and validate a document title.

    The entity field caps title length at 512; rejecting overlong titles on
    create as well as update keeps a row from being stuck with a title that
    can never be edited back into compliance.
    """
    title = (title or "").strip()
    if not title:
        raise ValueError("title is required")
    if len(title) > TITLE_MAX_LENGTH:
        raise ValueError(f"title exceeds maximum length ({TITLE_MAX_LENGTH} chars)")
    return title


# ---------------------------------------------------------------------------
# Authorization — one source, used by both the read and write verbs
# ---------------------------------------------------------------------------


def _auth_context():
    from core.crypto_scopes import production_context

    return production_context()


def can_manage(department: str, caller: str) -> bool:
    """Department head or realm admin: the roles that may share and revoke."""
    if not department or not caller:
        return False
    ctx = _auth_context()
    return bool(
        ctx.is_realm_admin(caller) or ctx.is_department_head(department, caller)
    )


def _department(name: str):
    from ggg import Department

    try:
        return Department[name]
    except Exception:
        return None


def is_member(department: str, caller: str) -> bool:
    """Forward membership check — no full user scan (issue #242)."""
    if not department or not caller:
        return False
    dept = _department(department)
    if dept is None:
        return False
    head = getattr(dept, "head", None)
    if head is not None and str(getattr(head, "id", "")) == caller:
        return True
    try:
        from core.membership import user_in_department
        from ggg import User

        return bool(user_in_department(User[caller], dept))
    except Exception:
        return caller in member_principals(department)


def can_view(department: str, caller: str) -> bool:
    return can_manage(department, caller) or is_member(department, caller)


def member_principals(department: str) -> List[str]:
    dept = _department(department)
    if dept is None:
        return []
    try:
        from core.membership import department_member_principals

        return list(department_member_principals(dept, include_head=True))
    except Exception as e:
        logger.warning(f"member_principals({department}): {e}")
        return []


def _require_manage(department: str, caller: str, what: str) -> None:
    if not can_manage(department, caller):
        raise PermissionError(
            f"{what} requires being head of '{department}' or a realm admin"
        )


def _require_view(department: str, caller: str, what: str) -> None:
    if not can_view(department, caller):
        raise PermissionError(f"{what} requires membership of '{department}'")


# ---------------------------------------------------------------------------
# Storage — the extension's own namespaced entity
# ---------------------------------------------------------------------------


def _doc_class():
    from core import extension_bridge

    return extension_bridge.own_entity_class(EXT_ID, DOC_TYPE)


def _reshare_job_class():
    from core import extension_bridge

    return extension_bridge.own_entity_class(EXT_ID, RESHARE_JOB_TYPE)


def _load(doc_id: Any):
    doc = _doc_class()[doc_id]
    if not doc:
        raise ValueError(f"document '{doc_id}' not found")
    return doc


def _doc_department(doc) -> str:
    return getattr(doc, "department", "") or ""


def project(doc, caller: str, include_ciphertext: bool = False) -> Dict[str, Any]:
    """Plain data for one document. ``ciphertext`` only when asked for, so a
    listing does not ship every blob in the department."""
    out = {
        "id": doc._id,
        "title": getattr(doc, "title", "") or "",
        "department": _doc_department(doc),
        "scope": getattr(doc, "scope", "") or "",
        "created_by": getattr(doc, "created_by", "") or "",
        "created_at": getattr(doc, "timestamp_created", "") or "",
        "can_manage": can_manage(_doc_department(doc), caller),
    }
    if include_ciphertext:
        out["ciphertext"] = getattr(doc, "ciphertext", "") or ""
    return out


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def v_department_list(caller: str = "", **kwargs) -> dict:
    """Departments this caller can see, with member principals.

    Managers need the member list to pick who to share a document with; members
    get it for their own department. A caller who is in neither role sees an
    empty list rather than an error — there is nothing secret about the fact
    that other departments exist, but their membership is not this caller's.
    """
    from ggg import Department

    out = []
    for dept in Department.instances():
        name = getattr(dept, "name", "") or ""
        if not name or not can_view(name, caller):
            continue
        out.append({
            "name": name,
            "description": getattr(dept, "description", "") or "",
            "can_manage": can_manage(name, caller),
            "members": member_principals(name),
        })
    out.sort(key=lambda d: d["name"])
    return {"departments": out}


def v_doc_list(caller: str = "", department: str = "", **kwargs) -> dict:
    """Document metadata, filtered to departments the caller can view.

    The filter is applied here, not passed to the extension to apply, which is
    the whole point of the port: a caller cannot widen it.
    """
    wanted = (department or "").strip()
    if wanted and not can_view(wanted, caller):
        raise PermissionError(f"not a member of '{wanted}'")

    docs = []
    for doc in _doc_class().instances():
        dept = _doc_department(doc)
        if wanted and dept != wanted:
            continue
        if not can_view(dept, caller):
            continue
        docs.append(project(doc, caller))

    docs.sort(key=lambda d: str(d.get("created_at", "")), reverse=True)
    return {"documents": docs, "total": len(docs)}


def v_doc_get(caller: str = "", id: Any = None, **kwargs) -> dict:
    """One document including its ciphertext, for client-side decryption."""
    if id is None:
        raise ValueError("id is required")
    doc = _load(id)
    _require_view(_doc_department(doc), caller, "reading this document")
    return project(doc, caller, include_ciphertext=True)


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def v_doc_create(
    caller: str = "", department: str = "", title: str = "", **kwargs
) -> dict:
    """Create an empty document and return its id and key scope.

    Empty on purpose: the scope embeds the new id, so the client cannot encrypt
    until the row exists. ``update`` is the second half.
    """
    department = (department or "").strip()
    if not department:
        raise ValueError("department is required")
    title = _clean_title(title)
    if _department(department) is None:
        raise ValueError(f"department '{department}' not found")

    _require_manage(department, caller, "creating a document")

    doc = _doc_class()(
        department=department,
        title=title,
        ciphertext="",
        scope="",
        created_by=caller,
    )
    scope = f"dept:{department}:doc:{doc._id}"
    doc.scope = scope

    logger.info(f"dept_doc.create: {doc._id} in {department} by {caller}")
    return {"id": doc._id, "scope": scope}


def v_doc_update(
    caller: str = "",
    id: Any = None,
    title: Optional[str] = None,
    ciphertext: Optional[str] = None,
    **kwargs,
) -> dict:
    """Update plaintext title and/or encrypted blob.

    Gated on manage rather than view: a member who could overwrite the
    ciphertext could destroy a document they are only supposed to read.
    """
    if id is None:
        raise ValueError("id is required")
    if title is None and ciphertext is None:
        raise ValueError("nothing to update")

    doc = _load(id)
    _require_manage(_doc_department(doc), caller, "editing this document")

    if title is not None:
        doc.title = _clean_title(title)

    if ciphertext is not None:
        doc.ciphertext = ciphertext or ""

    logger.info(f"dept_doc.update: {doc._id} by {caller}")
    return project(doc, caller)


def v_doc_delete(caller: str = "", id: Any = None, **kwargs) -> dict:
    """Delete a document.

    Any ``KeyEnvelope`` left at the scope is harmless — it wraps a DEK for data
    that no longer exists — and a manager can revoke them through the realm's
    generic revoke endpoint. The scope is returned so the caller can.
    """
    if id is None:
        raise ValueError("id is required")
    doc = _load(id)
    department = _doc_department(doc)
    _require_manage(department, caller, "deleting this document")

    scope = getattr(doc, "scope", "") or ""
    doc.delete()
    logger.info(f"dept_doc.delete: {id} in {department} by {caller}")
    return {"id": id, "scope": scope}


# ---------------------------------------------------------------------------
# Re-share jobs — when a member joins after docs were shared
# ---------------------------------------------------------------------------


def _project_job(job) -> Dict[str, Any]:
    return {
        "id": job._id,
        "department": getattr(job, "department", "") or "",
        "new_member_principal": getattr(job, "new_member_principal", "") or "",
        "status": getattr(job, "status", "") or "",
        "created_at": getattr(job, "created_at", "") or "",
    }


def _load_job(job_id: Any):
    job = _reshare_job_class()[job_id]
    if not job:
        raise ValueError(f"re-share job '{job_id}' not found")
    return job


def _pending_job_exists(department: str, principal: str) -> bool:
    for job in _reshare_job_class().instances():
        if (
            getattr(job, "status", "") == "pending"
            and getattr(job, "department", "") == department
            and getattr(job, "new_member_principal", "") == principal
        ):
            return True
    return False


def _notify_reshare_managers(dept, department_name: str, new_member_principal: str) -> None:
    from core import notification_bridge

    recipients: set[str] = set()
    head = getattr(dept, "head", None)
    if head is not None:
        head_principal = str(getattr(head, "id", ""))
        if head_principal:
            recipients.add(head_principal)

    try:
        from core.membership import users_with_profile

        for admin in users_with_profile("realm.admin"):
            pid = str(getattr(admin, "id", ""))
            if pid:
                recipients.add(pid)
    except Exception as e:
        logger.warning(f"reshare admin enumeration: {e}")

    if not recipients:
        return

    metadata = json.dumps({
        "department": department_name,
        "new_member": new_member_principal,
    })
    title = "Document re-share needed"
    message = (
        f"A new member was added to {department_name}. "
        "Re-share department documents so they can decrypt."
    )

    for subject in recipients:
        try:
            notification_bridge.v_create(
                caller=new_member_principal,
                title=title,
                message=message,
                audience_type="user",
                subject=subject,
                topic="dept_doc_reshare",
                href="/extensions/department_docs",
                metadata=metadata,
            )
        except Exception as e:
            logger.warning(f"reshare notification to {subject}: {e}")


def on_member_added(dept, user) -> None:
    department = getattr(dept, "name", "") or ""
    principal = str(getattr(user, "id", ""))
    if not department or not principal:
        return
    if _pending_job_exists(department, principal):
        return

    created_at = format_timestamp_ms(now_ms())
    _reshare_job_class()(
        department=department,
        new_member_principal=principal,
        status="pending",
        created_at=created_at,
    )
    _notify_reshare_managers(dept, department, principal)
    logger.info(f"dept_doc.reshare: pending job for {principal} in {department}")


def v_reshare_list(caller: str = "", department: str = "", **kwargs) -> dict:
    wanted = (department or "").strip()
    jobs = []
    for job in _reshare_job_class().instances():
        dept = getattr(job, "department", "") or ""
        if getattr(job, "status", "") != "pending":
            continue
        if wanted and dept != wanted:
            continue
        if not can_manage(dept, caller):
            continue
        jobs.append(_project_job(job))

    jobs.sort(key=lambda j: str(j.get("created_at", "")), reverse=True)
    return {"jobs": jobs}


def v_reshare_dismiss(caller: str = "", id: Any = None, **kwargs) -> dict:
    if id is None:
        raise ValueError("id is required")
    job = _load_job(id)
    dept = getattr(job, "department", "") or ""
    _require_manage(dept, caller, "dismissing a re-share job")
    job.status = "dismissed"
    logger.info(f"dept_doc.reshare_dismiss: {id} in {dept} by {caller}")
    return _project_job(job)


def v_reshare_complete(caller: str = "", id: Any = None, **kwargs) -> dict:
    if id is None:
        raise ValueError("id is required")
    job = _load_job(id)
    dept = getattr(job, "department", "") or ""
    _require_manage(dept, caller, "completing a re-share job")
    job.status = "done"
    logger.info(f"dept_doc.reshare_complete: {id} in {dept} by {caller}")
    return _project_job(job)


VERBS = {
    "department.list": v_department_list,
    "dept_doc.list": v_doc_list,
    "dept_doc.get": v_doc_get,
    "dept_doc.create": v_doc_create,
    "dept_doc.update": v_doc_update,
    "dept_doc.delete": v_doc_delete,
    "dept_doc.reshare_list": v_reshare_list,
    "dept_doc.reshare_dismiss": v_reshare_dismiss,
    "dept_doc.reshare_complete": v_reshare_complete,
}

READ_VERBS = frozenset({
    "department.list", "dept_doc.list", "dept_doc.get", "dept_doc.reshare_list",
})
