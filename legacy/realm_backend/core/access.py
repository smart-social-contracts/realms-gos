"""
Access control enforcement for Realm canister endpoints.

Provides a @require() decorator that checks the caller's UserProfile
permissions before allowing an endpoint to execute.

Usage:
    from core.access import require

    @update
    @require(Operations.REALM_CONFIGURE)
    def set_canister_config(...):
        ...

    @update
    @require(Operations.ALL)  # admin-only
    def __shell__(...):
        ...
"""

from functools import wraps

from _cdk import ic
from ic_python_logging import get_logger

logger = get_logger("core.access")


class AccessDenied(PermissionError):
    """Raised when a caller lacks the required permission.

    A ``PermissionError`` so Candid ``@require`` and REPL ``api.call`` /
    ``ext.call`` deny with the same exception class. ``SHELL_EXECUTE`` is
    not a superuser bit — this is still the verb's own gate.

    ``permission`` is the exact operation that failed (realms#349).
    ``source`` is the REPL call that caused it (``api.call('…')`` /
    ``ext.call('…')``). Host / REPL surfaces print
    ``quiet_access_denied`` — never the principal, never a traceback.
    ``shell.execute`` is only for opening ``__shell__`` itself.
    """

    def __init__(
        self, message: str = "Access denied", permission: str = "", source: str = ""
    ):
        super().__init__(message)
        self.permission = permission or ""
        self.source = source or ""


def require_operation_of(fn) -> str:
    """``@require`` operation on a host verb, including leftover Query slots.

    Leftover WASI inspect is a stub. Walk ``func`` / ``fn`` / ``__wrapped__``
    only. Never leftover-import leftover inspect.
    """
    seen = []
    cur = fn
    while cur is not None and cur not in seen:
        seen.append(cur)
        named = getattr(cur, "_require_operation", "") or ""
        if named:
            return named
        nxt = None
        for slot in ("func", "fn", "_fn", "handler", "__wrapped__"):
            try:
                inner = object.__getattribute__(cur, slot)
            except Exception:
                continue
            if inner is not None and inner not in seen:
                nxt = inner
                break
        cur = nxt
    return ""


def api_call_source(method) -> str:
    if not method:
        return "api.call"
    return f"api.call({method!r})"


def ext_call_source(extension_name, function_name, async_call=False) -> str:
    verb = "ext.call_async" if async_call else "ext.call"
    parts = []
    if extension_name:
        parts.append(repr(extension_name))
    if function_name:
        parts.append(repr(function_name))
    return f"{verb}({', '.join(parts)})"


def _trim_wrap(text: str) -> str:
    chunk = text.strip()
    while chunk.endswith(")") and chunk.count("(") < chunk.count(")"):
        chunk = chunk[:-1].rstrip()
    return chunk.split("\n", 1)[0].strip()


def permission_name(exc_or_permission) -> str:
    """Exact failed operation. Never invent ``shell.execute``.

    No ``re`` module — leftover WASI ships only a stub.
    """
    if isinstance(exc_or_permission, str):
        text = exc_or_permission
        named = ""
    else:
        named = getattr(exc_or_permission, "permission", "") or ""
        if named:
            return named
        text = str(exc_or_permission)
    marker = "lacks permission '"
    idx = text.find(marker)
    if idx >= 0:
        start = idx + len(marker)
        end = text.find("'", start)
        if end > start:
            return text[start:end]
    quiet = "✗ access denied:"
    found = text.find(quiet)
    if found >= 0:
        rest = text[found + len(quiet) :].strip()
        rest = _trim_wrap(rest)
        from_at = rest.find(" from ")
        if from_at > 0:
            rest = rest[:from_at].strip()
        if rest and rest != "call on Host":
            return rest
    return named


def call_source(exc_or_text) -> str:
    """``api.call('…')`` / ``ext.call('…')`` from an exception or leftover line."""
    named = getattr(exc_or_text, "source", "") or ""
    if named:
        return named
    text = exc_or_text if isinstance(exc_or_text, str) else str(exc_or_text)
    for needle in (" from api.call", " from ext.call"):
        idx = text.find(needle)
        if idx >= 0:
            return _trim_wrap(text[idx + len(" from ") :])
    return ""


def format_quiet_denied(permission: str, source: str = "") -> str:
    """One host/REPL line. Permission + call. No principal. No stack.

    ``shell.execute`` only when there is no inner verb source (opening
    ``__shell__``). Never default an in-REPL deny to ``shell.execute``.
    """
    perm = (permission or "").strip()
    src = (source or "").strip()
    if src:
        if not perm:
            return f"✗ access denied: from {src}"
        return f"✗ access denied: {perm} from {src}"
    if perm:
        return f"✗ access denied: {perm}"
    return "✗ access denied"


def quiet_access_denied(exc_or_permission, source: str = "") -> str:
    """One host/REPL line: exact permission and the call that failed."""
    if isinstance(exc_or_permission, str):
        extracted = _extract_quiet_line(exc_or_permission)
        if extracted:
            return extracted
    perm = permission_name(exc_or_permission)
    src = source or call_source(exc_or_permission)
    return format_quiet_denied(perm, src)


def _extract_quiet_line(text: str) -> str:
    """Prefer the inner ``✗ access denied: <perm> from <call>`` family."""
    if not text:
        return ""
    marker = "✗ access denied:"
    from_at = -1
    for needle in (" from api.call", " from ext.call"):
        from_at = text.find(needle)
        if from_at >= 0:
            break
    if from_at >= 0:
        start = text.rfind(marker, 0, from_at)
        if start < 0:
            start = text.find(marker)
        if start < 0:
            return ""
        return _trim_wrap(text[start:])
    if text.strip().startswith(marker) and " on Host" not in text:
        return text.strip().split("\n", 1)[0]
    return ""


def quiet_shell_result(result, source: str = ""):
    """Unwrap leftover Host-pipe noise. Keep exact permission + call."""
    if not isinstance(result, str):
        return result
    extracted = _extract_quiet_line(result)
    if extracted:
        return extracted
    text = result.strip()
    leaked = (
        "lacks permission '" in text
        or "Access denied: user " in text
        or " on Host" in text
    )
    if not leaked:
        return result
    perm = permission_name(text)
    src = source or call_source(text)
    return format_quiet_denied(perm, src)


def raise_quiet_access_denied(exc: BaseException, source: str = "") -> None:
    """Re-raise AccessDenied as one quiet line (permission + call).

    Must be called from an ``except`` block. Other PermissionErrors pass
    through unchanged so allowlist / blocked-method errors stay intact.
    """
    is_denied = type(exc).__name__ == "AccessDenied" or bool(
        getattr(exc, "permission", "")
    )
    if not is_denied:
        raise
    if type(exc).__name__ == "AccessDenied" and not isinstance(exc, AccessDenied):
        raise PermissionError(str(exc)) from None
    perm = permission_name(exc)
    src = source or call_source(exc)
    msg = format_quiet_denied(perm, src)
    if isinstance(exc, AccessDenied):
        raise AccessDenied(msg, permission=perm, source=src) from None
    raise PermissionError(msg) from None


def product_shell_guard(run):
    """Outer ``__shell__`` body: return one ``✗`` line, never a traceback.

    Opening ``__shell__`` without ``shell.execute`` → that permission only.
    In-REPL denials keep the inner ``@require`` permission and the call.
    """
    try:
        return quiet_shell_result(run())
    except AccessDenied as exc:
        return quiet_access_denied(exc)
    except PermissionError as exc:
        return quiet_access_denied(exc)


# Controller principal captured at @init / @post_upgrade time.
# The controller always bypasses permission checks.
_controller_principal: str = ""


def set_controller(principal: str) -> None:
    """Store the canister controller principal (called once at init)."""
    global _controller_principal
    _controller_principal = principal


def _check_access(caller_principal: str, operation: str) -> bool:
    """Check if a caller has permission to perform an operation.

    There is deliberately no test-mode bypass here. Test environments seed a
    real admin ``User`` for the deterministic test principal instead, so the
    permission system is exercised rather than switched off.

    Resolution order:
      0a. IC-level controller bypass (anyone in the canister settings'
          controllers list — captured by the platform, not by us)
      0b. Init-time controller bypass (principal captured at init/post_upgrade)
      1. Check trusted_principals on the Realm (canister-to-canister trust)
      2. Look up User by principal
      3. Check each of the user's profiles for the operation (coarse RBAC)
      4. Check fine-grained Permission entities on the user
      5. Check fine-grained Permission entities on the user's profiles
      6. Check fine-grained Permission entities on the user's departments
      A profile with Operations.ALL grants everything.

    Returns True if allowed, False otherwise.
    """
    # 0-replay. An approved governance proposal replaying its action acts
    # with the realm's own authority (issue #262). Proposal inline code can
    # already mutate the DB freely, so this does not widen anything.
    try:
        from core.governed_action import in_replay
        if in_replay():
            return True
    except Exception:
        pass

    # 0a. IC-level controllers always allowed. This is critical for
    # layered deployments where realm_installer (a controller) drives
    # post-install bootstrapping (registry registration, codex install,
    # ...) and the deploying CI principal needs admin to bootstrap even
    # though no explicit User record exists for it after a reinstall.
    try:
        if ic.is_controller(caller_principal):
            return True
    except Exception:
        pass

    # 0b. First-deploy controller fallback (principal captured at init).
    if _controller_principal and caller_principal == _controller_principal:
        return True

    from ggg import Realm, User
    from ggg.system.user_profile import Operations

    # 1. Trusted principal whitelist (DAO, AI agents, parent realms)
    try:
        realm = Realm.load("1")
        if realm and realm.trusted_principals:
            trusted = [p.strip() for p in str(realm.trusted_principals).split(",") if p.strip()]
            if caller_principal in trusted:
                return True
    except Exception:
        pass

    # 1b. The installer recorded at first boot (set_canister_config_json after setup).
    try:
        realm = Realm.load("1")
        if is_bootstrap_admin_caller(caller_principal, realm):
            return True
    except Exception:
        pass

    user = User[caller_principal]
    if not user:
        return False

    # 3. Profile-level check (coarse RBAC) — direct profiles plus profiles
    # attached to active appointments (issue #301 acting/substantive seats).
    profiles = list(user.profiles or [])
    try:
        from ggg import Appointment, AppointmentStatus

        rows = Appointment.instances()
        # Production returns a list. Skip mocks / non-sequences so a MagicMock
        # ``instances()`` cannot iterate as every caller.
        if isinstance(rows, (list, tuple)):
            for appointment in rows:
                if (getattr(appointment, "status", None) or AppointmentStatus.ACTIVE) != AppointmentStatus.ACTIVE:
                    continue
                holder = getattr(appointment, "user", None)
                holder_id = getattr(holder, "id", None) if holder is not None else None
                if not isinstance(holder_id, str) or holder_id != caller_principal:
                    continue
                pos = getattr(appointment, "position", None)
                if pos is None:
                    continue
                seat_profile = getattr(pos, "profile", None)
                if seat_profile is not None:
                    profiles.append(seat_profile)
    except Exception:
        pass

    for profile in profiles:
        allowed = str(profile.allowed_to or "").split(",")
        if Operations.ALL in allowed or operation in allowed:
            return True

    # 4. Per-user Permission entities (fine-grained)
    try:
        for perm in user.permissions:
            if perm.name == operation:
                return True
    except Exception:
        pass

    # 5. Per-profile Permission entities (fine-grained)
    try:
        for profile in user.profiles:
            for perm in profile.permissions:
                if perm.name == operation:
                    return True
    except Exception:
        pass

    # 6. Per-department Permission entities (fine-grained)
    try:
        for department in user.departments:
            for perm in department.permissions:
                if perm.name == operation:
                    return True
    except Exception:
        pass

    return False


def require(operation: str):
    """Decorator that enforces an operation permission on an endpoint.

    The caller is identified via ic.caller(). If the caller is not a
    registered User or none of their profiles grant the required
    operation, an AccessDenied exception is raised.

    For generator-based async endpoints (those using ``yield``), wraps
    the generator transparently.
    """
    def decorator(fn):
        _is_gen = getattr(fn, '__code__', None) is not None and (fn.__code__.co_flags & 0x20)

        if _is_gen:
            @wraps(fn)
            def async_wrapper(*args, **kwargs):
                caller = ic.caller().to_str()
                if not _check_access(caller, operation):
                    raise AccessDenied(
                        f"Access denied: user {caller} lacks permission '{operation}'",
                        permission=operation,
                    )
                return (yield from fn(*args, **kwargs))
            async_wrapper._require_operation = operation
            return async_wrapper
        else:
            @wraps(fn)
            def wrapper(*args, **kwargs):
                caller = ic.caller().to_str()
                if not _check_access(caller, operation):
                    raise AccessDenied(
                        f"Access denied: user {caller} lacks permission '{operation}'",
                        permission=operation,
                    )
                return fn(*args, **kwargs)
            wrapper._require_operation = operation
            return wrapper

    return decorator


def is_bootstrap_admin_caller(caller_principal: str, realm) -> bool:
    """The installer recorded at first boot may act as admin (it configures the
    realm after setup). Being a controller is checked before this; there is no
    baked-in list of installer ids."""
    if not realm:
        return False
    installer_id = str(getattr(realm, "installer_canister_id", "") or "").strip()
    return bool(installer_id and installer_id == caller_principal)


def _is_controller_or_trusted(caller_principal: str) -> bool:
    """Check if caller is an IC controller, init-time controller, or trusted principal."""
    try:
        if ic.is_controller(caller_principal):
            return True
    except Exception:
        pass

    if _controller_principal and caller_principal == _controller_principal:
        return True

    from ggg import Realm
    try:
        realm = Realm.load("1")
        if realm and realm.trusted_principals:
            trusted = [p.strip() for p in str(realm.trusted_principals).split(",") if p.strip()]
            if caller_principal in trusted:
                return True
    except Exception:
        pass

    return False


def may_register_founder(caller: str, realm) -> bool:
    """Founder registration: an IC controller, a trusted principal, or the
    installer recorded at init (issue #404)."""
    if _is_controller_or_trusted(caller):
        return True
    return is_bootstrap_admin_caller(caller, realm)


def require_controller(fn):
    """Decorator that restricts an endpoint to IC controllers and trusted principals.

    Raises AccessDenied if the caller is not a controller (IC-level or
    init-time) and not in the realm's trusted_principals list.
    """
    _is_gen = getattr(fn, '__code__', None) is not None and (fn.__code__.co_flags & 0x20)

    if _is_gen:
        @wraps(fn)
        def async_wrapper(*args, **kwargs):
            caller = ic.caller().to_str()
            if not _is_controller_or_trusted(caller):
                raise AccessDenied(
                    f"Access denied: {caller} is not a controller or trusted principal"
                )
            return (yield from fn(*args, **kwargs))
        return async_wrapper
    else:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            caller = ic.caller().to_str()
            if not _is_controller_or_trusted(caller):
                raise AccessDenied(
                    f"Access denied: {caller} is not a controller or trusted principal"
                )
            return fn(*args, **kwargs)
        return wrapper
