"""``notification.*`` verbs for the sandboxed ``notifications`` extension.

Notification visibility is per-record RBAC in its purest form: a private
message is readable by its addressee, a department message by that
department, a realm message by any registered member. The plan's claim is that
per-record RBAC falls out of the bridge rather than being a separate project,
and this is where that gets tested — the host resolves the caller's
departments and membership and filters before anything is returned, so a
sandboxed extension never sees a row it should not.

Porting also closes two holes that were live in the in-process version:

  * ``delete_notification`` took an id and deleted it, with no check that the
    caller had anything to do with the notification. Any member could delete
    any message in the realm.
  * ``mark_as_read`` flipped the shared ``read`` flag on a single-user
    notification without checking the caller was its addressee.

Both are now scoped host-side, so they are unexpressible rather than merely
fixed.
"""

import json

from core.time_utils import format_timestamp_ms, now_ms, parse_timestamp_ms


def _valid_email(address: str) -> bool:
    """Rough ``local@domain.tld`` check.

    Deliberately not a module-level ``re.compile``: some platform contexts
    ship a gutted ``re`` without ``compile``, which would break lazy module
    loading for the entire bridge.
    """
    try:
        import re

        return bool(re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", address))
    except Exception:
        local, _, domain = (address or "").partition("@")
        return bool(local) and "." in domain and " " not in address


AUDIENCES = ("user", "department", "realm")
VISIBILITIES = ("private", "public")

# Reading and clearing the outbound email queue is an off-chain worker's job,
# not a member's.
WORKER_OPERATION = "notification.send"
ADMIN_OPERATION = "realm.admin"


# ---------------------------------------------------------------------------
# Caller context and visibility
# ---------------------------------------------------------------------------


def caller_context(caller: str):
    """The caller's membership, departments, and admin status.

    Resolved here rather than in the extension because visibility is decided
    against it — an extension that computed its own context would be deciding
    who may read what.
    """
    departments = set()
    is_member = False
    is_admin = False

    if not caller:
        return is_member, departments, is_admin

    try:
        from ggg import User

        user = User[caller]
        if user:
            is_member = True
            for attr in ("departments", "headed_departments"):
                for dept in (getattr(user, attr, None) or []):
                    name = getattr(dept, "name", None)
                    if name:
                        departments.add(name)
    except Exception:
        pass

    try:
        from core.crypto_scopes import production_context

        is_admin = bool(production_context().is_realm_admin(caller))
    except Exception:
        pass

    return is_member, departments, is_admin


def _audience(n) -> str:
    return getattr(n, "audience_type", "user") or "user"


def _target_user(n) -> str:
    try:
        user = n.user
    except Exception:
        return ""
    if not user:
        return ""
    return getattr(user, "id", None) or getattr(user, "_id", None) or ""


def _target_department(n) -> str:
    try:
        dept = n.department
    except Exception:
        return ""
    return (getattr(dept, "name", "") or "") if dept else ""


def is_visible_to(n, caller: str, is_member: bool, departments: set) -> bool:
    """Whether *caller* may read *n*.

    public        -> anyone
    private user  -> only the addressed user
    private dept  -> only members (and heads) of that department
    private realm -> only registered users of this realm
    """
    if (getattr(n, "visibility", "private") or "private") == "public":
        return True

    audience = _audience(n)
    if audience == "user":
        return bool(caller) and _target_user(n) == caller
    if audience == "department":
        return _target_department(n) in departments
    if audience == "realm":
        return is_member
    return False


def _is_read_by(n, caller: str) -> bool:
    """Broadcasts track readers individually; single-user ones use a flag."""
    if _audience(n) == "user":
        return bool(getattr(n, "read", False))
    readers = [p for p in (getattr(n, "read_by", "") or "").split(",") if p]
    return bool(caller) and caller in readers


def _timestamp_ms(n) -> int:
    for attr in ("_timestamp_created", "_timestamp_updated"):
        value = getattr(n, attr, None)
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
    for attr in ("timestamp_created", "timestamp_updated"):
        value = getattr(n, attr, None)
        if value and str(value) != "None":
            stamp = parse_timestamp_ms(str(value))
            if stamp:
                return stamp
    return 0


def _sender_display(n) -> str:
    """Public nickname when the sender is a known member; else the raw principal."""
    sender = getattr(n, "sender", "") or ""
    if not sender:
        return ""
    try:
        from ggg import User

        user = User[sender]
        if user:
            nick = (getattr(user, "nickname", "") or "").strip()
            if nick:
                return nick
    except Exception:
        pass
    return sender


def project(n, caller: str = "") -> dict:
    topic = getattr(n, "topic", "") or ""
    message = getattr(n, "message", "") or ""
    if topic == "email_verification" and message:
        message = f"{VERIFY_PREVIEW_TEXT} {message}"
    return {
        "id": n._id,
        "topic": topic,
        "title": getattr(n, "title", "") or "",
        "message": message,
        "sender": getattr(n, "sender", "") or "",
        "sender_name": _sender_display(n),
        "recipient": getattr(n, "recipient", "") or "",
        "visibility": getattr(n, "visibility", "private") or "private",
        "audience_type": _audience(n),
        "department": _target_department(n),
        "origin_realm": getattr(n, "origin_realm", "") or "",
        "timestamp_ms": _timestamp_ms(n),
        "read": _is_read_by(n, caller),
        "icon": getattr(n, "icon", "bell") or "bell",
        "href": getattr(n, "href", "/notifications") or "/notifications",
        "color": getattr(n, "color", "blue") or "blue",
    }


def _visible_notification(caller: str, notification_id):
    """Load a notification the caller is allowed to see, or refuse.

    Every id-addressed verb goes through here. That is what makes "any member
    can delete any notification" unexpressible rather than merely fixed.
    """
    from ggg import Notification

    if not notification_id:
        raise ValueError("id is required")

    n = Notification.load(str(notification_id))
    if not n:
        raise ValueError(f"Notification {notification_id} not found")

    is_member, departments, is_admin = caller_context(caller)
    if not (is_admin or is_visible_to(n, caller, is_member, departments)):
        # Same message as a genuine miss: whether a notification exists is
        # itself information the caller is not entitled to.
        raise ValueError(f"Notification {notification_id} not found")
    return n, is_admin


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def v_list(caller="", **kwargs) -> dict:
    """Notifications visible to the caller, newest first."""
    from ggg import Notification

    is_member, departments, _is_admin = caller_context(caller)
    rows = [
        project(n, caller) for n in Notification.instances()
        if is_visible_to(n, caller, is_member, departments)
    ]
    rows.sort(key=lambda r: r["timestamp_ms"], reverse=True)
    return {
        "notifications": rows,
        "unread_count": sum(1 for r in rows if not r["read"]),
        "total_count": len(rows),
    }


def v_departments(caller="", **kwargs) -> dict:
    """The realm's departments, for use as message recipients."""
    from ggg import Department

    departments = []
    for dept in Department.instances():
        try:
            members = list(getattr(dept, "members", None) or [])
        except Exception:
            members = []
        departments.append({
            "name": getattr(dept, "name", "") or "",
            "description": getattr(dept, "description", "") or "",
            "member_count": len(members),
        })
    departments.sort(key=lambda d: d["name"].lower())
    return {"departments": departments}


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def v_create(caller="", title="", message="", audience_type="", subject="",
             department="", visibility="private", topic="general",
             icon="bell", href="/notifications", color="blue", metadata="{}",
             event_type="", **kwargs) -> dict:
    """Create a notification.

    User and department messages may be sent by any registered member; a
    realm-wide broadcast requires admin. ``sender`` is the authenticated
    caller, never an argument.
    """
    from ggg import Department, Notification, User

    if not str(title).strip():
        raise ValueError("title is required")
    if not str(message).strip():
        raise ValueError("message is required")

    audience_type = audience_type or ("department" if department else "user")
    if audience_type not in AUDIENCES:
        raise ValueError(f"Invalid audience_type: {audience_type}")
    if visibility not in VISIBILITIES:
        raise ValueError(f"Invalid visibility: {visibility}")

    is_member, _departments, is_admin = caller_context(caller)

    user = None
    dept = None
    if audience_type == "user":
        if subject:
            user = User[subject]
            if not user:
                raise ValueError(f"User '{subject}' not found")
    elif audience_type == "department":
        name = str(department or "").strip()
        if not name:
            raise ValueError("department is required for a department message")
        dept = Department[name]
        if not dept:
            raise ValueError(f"Department '{name}' not found")
    elif not is_admin:
        raise PermissionError(
            "Only a realm admin or the system may send realm-wide messages"
        )

    if audience_type != "realm" and not (is_member or is_admin):
        raise PermissionError("Only registered members may send messages")

    notification = Notification(
        topic=topic or "general",
        title=str(title),
        message=str(message),
        sender=caller,
        visibility=visibility,
        audience_type=audience_type,
        user=user,
        department=dept,
        read=False,
        read_by="",
        icon=icon or "bell",
        href=href or "/notifications",
        color=color or "blue",
        metadata=metadata if isinstance(metadata, str) else json.dumps(metadata),
    )
    _stamp(notification)
    queue_email(notification, event_type)
    return {"id": notification._id, "audience_type": audience_type}


def v_mark_read(caller="", id="", read=True, **kwargs) -> dict:
    """Mark a notification read or unread *for the caller*.

    Broadcasts track readers individually, so one member marking a realm
    message read does not clear it for everyone.
    """
    n, _is_admin = _visible_notification(caller, id)
    read = bool(read)

    if _audience(n) == "user":
        n.read = read
    else:
        readers = [p for p in (getattr(n, "read_by", "") or "").split(",") if p]
        if read and caller and caller not in readers:
            readers.append(caller)
        elif not read and caller in readers:
            readers.remove(caller)
        n.read_by = ",".join(readers)

    return {"id": id, "read": read}


def v_delete(caller="", id="", **kwargs) -> dict:
    """Delete a notification.

    Restricted to the addressee, the sender, or an admin. The in-process
    version checked none of these.
    """
    n, is_admin = _visible_notification(caller, id)

    is_sender = (getattr(n, "sender", "") or "") == caller
    is_addressee = _audience(n) == "user" and _target_user(n) == caller
    if not (is_admin or is_sender or is_addressee):
        raise PermissionError(
            "Only the recipient, the sender, or an admin may delete a "
            "notification"
        )

    n.delete()
    return {"id": id, "deleted": True}


# ---------------------------------------------------------------------------
# Email delivery
# ---------------------------------------------------------------------------


def _realm_email_config() -> dict:
    from ggg import Realm

    try:
        realm = Realm.load("1")
        if not realm:
            return {}
        manifest = json.loads(getattr(realm, "manifest_data", "{}") or "{}")
        email = manifest.get("email") if isinstance(manifest, dict) else {}
        return email if isinstance(email, dict) else {}
    except Exception:
        return {}


def _private_data(user) -> dict:
    try:
        data = json.loads(getattr(user, "private_data", "") or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _email_info(user) -> dict:
    data = _private_data(user)
    return {
        "email": str(data.get("email") or "").strip(),
        "email_notifications_enabled": data.get(
            "email_notifications_enabled", True
        ),
        "email_verified": bool(data.get("email_verified", False)),
    }


def _metadata(n) -> dict:
    try:
        data = json.loads(getattr(n, "metadata", "") or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def queue_email(notification, event_type: str = "") -> None:
    """Mark a notification for email delivery when settings allow it.

    Single-user notifications only; broadcast email is not implemented. An
    address already present as ``force_email_to`` (an admin test) is kept.
    """
    try:
        metadata = _metadata(notification)
        existing = str(metadata.get("force_email_to", "")).strip()

        config = _realm_email_config()
        if config.get("enabled") is False:
            return

        resolved = event_type or (
            getattr(notification, "topic", "") or ""
        )
        if not resolved or resolved == "general":
            resolved = "notification"

        if _audience(notification) != "user":
            return

        target = _target_user(notification)
        if not target:
            return

        from ggg import User

        user = User[target]
        if not user:
            return

        info = _email_info(user)
        if not info.get("email_notifications_enabled", True):
            return
        if not existing and not info.get("email"):
            return
        # Only verified addresses receive notification mail. The
        # ``force_email_to`` path (admin tests, the verification email
        # itself) bypasses this so an unverified address can still be
        # reached to prove ownership.
        if not existing and not info.get("email_verified", False):
            return

        metadata["email_status"] = "pending"
        metadata["event_type"] = resolved
        if not existing:
            metadata["force_email_to"] = info["email"]
        notification.metadata = json.dumps(metadata)
    except Exception:
        pass


def _caller_user(caller: str):
    from ggg import User

    if not caller:
        raise PermissionError("No caller identity")
    user = User[caller]
    if not user:
        raise ValueError("User not found")
    return user


def v_email_settings(caller="", **kwargs) -> dict:
    """The caller's own email address and delivery preference."""
    return _email_info(_caller_user(caller))


def v_set_email(caller="", email="", **kwargs) -> dict:
    """Set the caller's own email address. Only ever their own.

    Setting an address always marks it unverified; verification is a
    separate step so the flag cannot be self-asserted.
    """
    return v_set_email_unverified(caller=caller, email=email)


def v_set_email_preferences(caller="", email_notifications_enabled=True,
                            **kwargs) -> dict:
    user = _caller_user(caller)
    data = _private_data(user)
    enabled = bool(email_notifications_enabled)
    data["email_notifications_enabled"] = enabled
    user.private_data = json.dumps(data)
    return {"email_notifications_enabled": enabled}


# --- Email address verification -------------------------------------------

VERIFY_CODE_TTL_SECONDS = 15 * 60
VERIFY_MAX_ATTEMPTS = 5
VERIFY_PREVIEW_TEXT = (
    "Use this code in Realm settings to confirm your email address. "
    "It expires in 15 minutes."
)


def _stamp(n) -> None:
    ms = now_ms()
    if ms <= 0:
        return
    text = format_timestamp_ms(ms)
    n.timestamp_created = text
    n.timestamp_updated = text
    n._timestamp_created = ms
    n._timestamp_updated = ms
    save = getattr(n, "_save", None)
    if callable(save) and not getattr(n, "_do_not_save", False):
        save()


def _now_seconds() -> int:
    """Current Unix time in whole seconds, canister-safe.

    ``time.time()`` returns 0.0 under Kybra/WASM; the IC exposes a
    nanosecond clock via ``ic.time()``. Falls back to ``time.time()`` for
    local/test runs.
    """
    return now_ms() // 1000


def _generate_verify_code() -> str:
    """Six-digit numeric code, IC-safe (no ``secrets``/system entropy)."""
    from core.random import generate_unique_id

    return str(int(generate_unique_id(length=16), 16) % 1_000_000).zfill(6)


def _clear_verify_state(data: dict) -> None:
    for key in ("email_verify_code", "email_verify_expires",
                "email_verify_attempts"):
        data.pop(key, None)


def v_set_email_unverified(caller="", email="", **kwargs) -> dict:
    """Store the caller's address as unverified and clear any stale code."""
    address = str(email or "").strip().lower()
    if address and not _valid_email(address):
        raise ValueError("Invalid email address")

    user = _caller_user(caller)
    data = _private_data(user)
    data["email"] = address
    data["email_verified"] = False
    _clear_verify_state(data)
    user.private_data = json.dumps(data)
    return {"email": address, "email_verified": False}


def v_request_email_verification(caller="", email="", **kwargs) -> dict:
    """Store the address, mint a code, and queue the verification email.

    The verification mail uses ``force_email_to`` so it reaches the
    (not-yet-verified) address and bypasses the event-toggle gate.
    """
    from ggg import Notification

    address = str(email or "").strip().lower()
    if not address:
        from core.extension_errors import CodedError

        raise CodedError("email_required", "email is required")
    if not _valid_email(address):
        from core.extension_errors import CodedError

        raise CodedError("email_invalid", "Invalid email address")

    user = _caller_user(caller)
    data = _private_data(user)
    code = _generate_verify_code()
    data["email"] = address
    data["email_verified"] = False
    data["email_verify_code"] = code
    data["email_verify_expires"] = _now_seconds() + VERIFY_CODE_TTL_SECONDS
    data["email_verify_attempts"] = 0
    user.private_data = json.dumps(data)

    notification = Notification(
        topic="email_verification",
        title="Verify your email address",
        message=code,
        sender=caller,
        visibility="private",
        audience_type="user",
        user=user,
        read=False,
        read_by="",
        icon="mail",
        href="/settings",
        color="blue",
        metadata=json.dumps({
            "email_status": "pending",
            "event_type": "email_verification",
            "force_email_to": address,
        }),
    )
    _stamp(notification)
    return {"id": notification._id, "email": address}


def v_verify_email_code(caller="", code="", **kwargs) -> dict:
    """Confirm ownership of the stored address with the emailed code."""
    user = _caller_user(caller)
    data = _private_data(user)

    from core.extension_errors import CodedError

    expected = str(data.get("email_verify_code") or "")
    if not expected:
        raise CodedError(
            "email_no_verification",
            "No verification in progress; request a code first",
        )

    attempts = int(data.get("email_verify_attempts", 0))
    if attempts >= VERIFY_MAX_ATTEMPTS:
        _clear_verify_state(data)
        user.private_data = json.dumps(data)
        raise CodedError("email_too_many_attempts", "Too many attempts; request a new code")

    expires = int(data.get("email_verify_expires", 0))
    if expires and _now_seconds() > expires:
        _clear_verify_state(data)
        user.private_data = json.dumps(data)
        raise CodedError(
            "email_code_expired",
            "Verification code expired; request a new one",
        )

    if str(code or "").strip() != expected:
        data["email_verify_attempts"] = attempts + 1
        user.private_data = json.dumps(data)
        raise CodedError("email_code_incorrect", "Incorrect verification code")

    data["email_verified"] = True
    _clear_verify_state(data)
    user.private_data = json.dumps(data)
    return {"email": data.get("email", ""), "email_verified": True}


def _require_worker(caller: str, verb: str) -> None:
    from core.extension_bridge import caller_has_operation

    if not caller_has_operation(caller, WORKER_OPERATION):
        raise PermissionError(f"{verb} requires the '{WORKER_OPERATION}' operation")


def v_pending_emails(caller="", **kwargs) -> dict:
    """The outbound email queue, for the off-chain worker.

    Returns recipient addresses, so it is gated on the worker operation rather
    than being readable by any member.
    """
    _require_worker(caller, "notification.pending_emails")
    from ggg import Notification, User

    pending = []
    for n in Notification.instances():
        metadata = _metadata(n)
        if metadata.get("email_status") != "pending":
            continue

        target = _target_user(n)
        address = str(metadata.get("force_email_to", "") or "")
        if not address and target:
            user = User[target]
            if user:
                address = _email_info(user).get("email", "")
        if not address:
            continue

        pending.append({
            "id": n._id,
            "topic": getattr(n, "topic", "") or "",
            "title": getattr(n, "title", "") or "",
            "message": getattr(n, "message", "") or "",
            "href": getattr(n, "href", "") or "",
            "to_address": address,
            "event_type": metadata.get("event_type", "notification"),
            "user_id": target,
        })

    pending.sort(key=lambda p: str(p["id"]))
    return {"notifications": pending}


def v_mark_email_sent(caller="", id="", success=False, error="",
                      **kwargs) -> dict:
    """Record the worker's delivery outcome."""
    _require_worker(caller, "notification.mark_email_sent")
    from ggg import Notification

    if not id:
        raise ValueError("id is required")
    n = Notification.load(str(id))
    if not n:
        raise ValueError(f"Notification {id} not found")

    metadata = _metadata(n)
    metadata["email_status"] = "sent" if success else "failed"
    if error:
        metadata["email_error"] = str(error)
    n.metadata = json.dumps(metadata)
    return {"id": id, "email_status": metadata["email_status"]}


def v_send_test_email(caller="", to="", subject="Realms email test",
                      body="This is a test email from Realms.",
                      **kwargs) -> dict:
    """Queue a test email. Admin-only, since it can mail an arbitrary address."""
    from core.extension_bridge import caller_has_operation
    from ggg import Notification

    if not caller_has_operation(caller, ADMIN_OPERATION):
        raise PermissionError(
            f"notification.send_test_email requires the "
            f"'{ADMIN_OPERATION}' operation"
        )

    user = _caller_user(caller)
    address = str(to or "").strip().lower()
    if not address:
        address = _email_info(user).get("email", "")
        if not address:
            raise ValueError(
                "to address is required — set your email in Settings first"
            )
    if not _valid_email(address):
        raise ValueError("Invalid to address")
    notification = Notification(
        topic="email_test",
        title=str(subject).strip(),
        message=str(body).strip(),
        sender=caller,
        visibility="private",
        audience_type="user",
        user=user,
        read=False,
        read_by="",
        icon="mail",
        href="/notifications",
        color="blue",
        metadata=json.dumps({
            "email_status": "pending",
            "event_type": "email_test",
            "force_email_to": address,
        }),
    )
    _stamp(notification)
    return {"id": notification._id, "to": address}


JOIN_PATH = "/join"
INHABITANT_PROFILES = ("member", "citizen")
INHABITANT_INVITE_HOURS = 720
INHABITANT_INVITE_USES = 100
MAX_JOIN_EMAILS = 50
JOIN_EVENT_TYPE = "notification"


def _parse_email_list(emails=None, to="") -> list:
    """Accept a list or a comma / newline / semicolon separated string."""
    raw = emails if emails is not None else to
    if raw is None or raw == "":
        raise ValueError("emails is required")
    if isinstance(raw, str):
        items = raw.replace(";", ",").replace("\n", ",").split(",")
    elif isinstance(raw, (list, tuple)):
        items = raw
    else:
        raise ValueError("emails must be a list or comma-separated string")

    addresses = []
    seen = set()
    for item in items:
        address = str(item or "").strip().lower()
        if not address:
            continue
        if not _valid_email(address):
            raise ValueError(f"Invalid email address: {address}")
        if address in seen:
            continue
        seen.add(address)
        addresses.append(address)

    if not addresses:
        raise ValueError("emails is required")
    if len(addresses) > MAX_JOIN_EMAILS:
        raise ValueError(f"at most {MAX_JOIN_EMAILS} emails per send")
    return addresses


def _realm_record():
    from ggg import Realm

    if hasattr(Realm, "instances"):
        realms = Realm.instances()
        if realms:
            return realms[0]
    load = getattr(Realm, "load", None)
    if load:
        return load("1")
    return None


def _is_shared_inhabitant_code(code) -> bool:
    """True for a reusable inhabitant invite (not staff or citizen-import)."""
    profile = (getattr(code, "profile", None) or "member").strip()
    if profile not in INHABITANT_PROFILES:
        return False
    if (getattr(code, "department", None) or "").strip():
        return False
    if (getattr(code, "position", None) or "").strip():
        return False
    if not (getattr(code, "code", None) or "").strip():
        return False
    try:
        meta = json.loads(getattr(code, "metadata", "") or "{}")
    except Exception:
        meta = {}
    if isinstance(meta, dict) and meta.get("kind") == "citizen_import":
        return False
    is_valid = getattr(code, "is_valid", None)
    if callable(is_valid):
        return bool(is_valid())
    return True


def inhabitant_join_href(caller: str = "") -> str:
    """Portal join path for inhabitants: ``/join`` or ``/join?invite=…``.

    Relative on purpose. The off-chain worker prefixes ``REALM_PUBLIC_URL``
    (for example ``https://test.gos.earth/r/<slug>``) so the mailed link is
    the public portal join URL, not a canister id.
    """
    realm = _realm_record()
    if realm and getattr(realm, "open_registration", False):
        return JOIN_PATH

    try:
        from ggg import RegistrationCode
    except Exception:
        return JOIN_PATH

    try:
        for code in RegistrationCode.instances():
            if _is_shared_inhabitant_code(code):
                return f"{JOIN_PATH}?invite={code.code}"
    except Exception:
        pass

    try:
        created = RegistrationCode.create(
            user_id="",
            created_by=caller or "realm.admin",
            frontend_url="",
            expires_in_hours=INHABITANT_INVITE_HOURS,
            profile="member",
            max_uses=INHABITANT_INVITE_USES,
        )
        plaintext = (getattr(created, "code", None) or "").strip()
        if plaintext:
            return f"{JOIN_PATH}?invite={plaintext}"
    except Exception:
        pass

    raise ValueError(
        "Could not create an inhabitant invite code; enable open "
        "registration or generate an invite first"
    )


def v_send_join_link(caller="", emails=None, to="", **kwargs) -> dict:
    """Queue the inhabitant join URL to one or more addresses.

    Admin/founder only. Recipients need not be members — each notification
    uses ``force_email_to`` so the worker can deliver before a User exists.
    """
    from core.extension_bridge import caller_has_operation
    from ggg import Notification

    if not caller_has_operation(caller, ADMIN_OPERATION):
        raise PermissionError(
            f"notification.send_join_link requires the "
            f"'{ADMIN_OPERATION}' operation"
        )

    user = _caller_user(caller)
    addresses = _parse_email_list(emails=emails, to=to)
    href = inhabitant_join_href(caller)

    realm = _realm_record()
    realm_name = (getattr(realm, "name", None) or "").strip() or "this realm"
    title = f"You are invited to join {realm_name}"
    message = (
        "You are invited to join this realm as an inhabitant. "
        f"Open the join link to register:\n{href}"
    )

    queued = []
    for address in addresses:
        notification = Notification(
            topic="join_invite",
            title=title,
            message=message,
            sender=caller,
            visibility="private",
            audience_type="user",
            user=user,
            read=False,
            read_by="",
            icon="mail",
            href=href,
            color="blue",
            metadata=json.dumps({
                "email_status": "pending",
                "event_type": JOIN_EVENT_TYPE,
                "force_email_to": address,
            }),
        )
        _stamp(notification)
        queued.append({"id": notification._id, "to": address})

    return {"href": href, "queued": len(queued), "notifications": queued}


READS = {
    "notification.list": v_list,
    "notification.departments": v_departments,
    "notification.email_settings": v_email_settings,
    "notification.pending_emails": v_pending_emails,
}

WRITES = {
    "notification.create": v_create,
    "notification.mark_read": v_mark_read,
    "notification.delete": v_delete,
    "notification.set_email": v_set_email,
    "notification.set_email_unverified": v_set_email_unverified,
    "notification.set_email_preferences": v_set_email_preferences,
    "notification.request_email_verification": v_request_email_verification,
    "notification.verify_email_code": v_verify_email_code,
    "notification.mark_email_sent": v_mark_email_sent,
    "notification.send_test_email": v_send_test_email,
    "notification.send_join_link": v_send_join_link,
}

VERBS = dict(READS, **WRITES)
