"""Host-dispatch REPL: UI and shell share the same Candid surface.

The product REPL is a client of host methods (``api.call`` / ``ext.call``),
not a second ORM back door. SecureORM entity stubs remain available as an
optional Cedar-gated debug surface.

See ``docs/issues/repl-ui-parity-spec.md``.
"""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path
from typing import Any, FrozenSet, Iterable, Optional

from ic_basilisk_toolkit.secure_orm import RpcError, SecureORM

from core.access import (
    AccessDenied,
    api_call_source,
    ext_call_source,
    format_quiet_denied,
    raise_quiet_access_denied,
    require_operation_of,
)

HOST_ACTIONS = (
    "host.call",
    "host.ext_sync",
    "host.ext_async",
    "host.list_methods",
)

BLOCKED_METHODS = frozenset(
    {
        "__shell__",
        "http_request",
        "http_request_update",
        "http_transform",
        "__get_candid_interface_tmp_hack",
    }
)

def _default_did_path() -> Path:
    """DID path for tests. Basilisk execs modules with no ``__file__``."""
    here = globals().get("__file__")
    if here:
        return Path(here).parent.parent / "realm_backend.did"
    try:
        return Path(__file__).parent.parent / "realm_backend.did"
    except Exception:
        return Path("realm_backend.did")


_DID_PATH = _default_did_path()

# Appended to the SecureORM stub. ``rpc`` is a sandbox builtin, not a global,
# and basilisk may bind the first ``eval_repl`` — so ``api``/``ext`` go in
# ``__builtins__`` and ``rpc`` is resolved at call time.
HOST_STUB_APPENDIX = r'''
def _host_rpc(action, **kwargs):
    b = __builtins__
    fn = b.get("rpc") if isinstance(b, dict) else getattr(b, "rpc", None)
    if fn is None:
        fn = globals().get("rpc")
    if not callable(fn):
        raise RuntimeError("rpc is not available")
    return fn(action, **kwargs)

class api:
    @staticmethod
    def call(method, *args, **kwargs):
        return _host_rpc("host.call", method=method, args=list(args), kwargs=dict(kwargs))

    @staticmethod
    def methods():
        return _host_rpc("host.list_methods")

class ext:
    @staticmethod
    def call(extension_name, function_name, args=None):
        return _host_rpc("host.ext_sync", extension_name=extension_name, function_name=function_name, args=args)

    @staticmethod
    def call_async(extension_name, function_name, args=None):
        return _host_rpc("host.ext_async", extension_name=extension_name, function_name=function_name, args=args)

def _install_host_names():
    b = __builtins__
    if isinstance(b, dict):
        b["api"] = api
        b["ext"] = ext
    else:
        b.api = api
        b.ext = ext
    ns = globals().get("_repl_ns")
    if isinstance(ns, dict):
        ns["api"] = api
        ns["ext"] = ext
        if not callable(ns.get("rpc")):
            fn = b.get("rpc") if isinstance(b, dict) else getattr(b, "rpc", None)
            if callable(fn):
                ns["rpc"] = fn

_install_host_names()
_eval_repl_inner = eval_repl
def eval_repl(code):
    _install_host_names()
    return _eval_repl_inner(code)
'''


def parse_candid_methods(did_text: str) -> FrozenSet[str]:
    """Quoted method names from the last ``service :`` block. No ``re`` module.

    Handles both multiline DID files and the one-line form basilisk embeds
    in ``__get_candid_interface_tmp_hack``.
    """
    marker = "service :"
    idx = did_text.rfind(marker)
    body = did_text[idx:] if idx >= 0 else did_text
    names = []
    i = 0
    while True:
        q = body.find('"', i)
        if q < 0:
            break
        end = body.find('"', q + 1)
        if end < 0:
            break
        name = body[q + 1 : end]
        rest = body[end + 1 :].lstrip()
        if rest.startswith(":") and name:
            names.append(name)
        i = end + 1
    return frozenset(names)


def _module_namespace(obj: Any) -> Optional[dict]:
    """Module/instance ``__dict__`` without going through ``__getattr__``.

    Leftover ``__dict__`` may be a mappingproxy, not a ``dict``. ``#342``
    required ``isinstance(..., dict)`` so the instance-dict allowlist
    stayed empty and leftover still raised Candid-not-found.
    """
    try:
        ns = object.__getattribute__(obj, "__dict__")
    except AttributeError:
        return None
    return ns if _mapping_like(ns) else None


_LAZY_KEYS = frozenset(
    {"_bsrc", "_bloaded", "_bloading", "_bload_count", "_bload"}
)
# Basilisk WASI ``types`` has no ``ModuleType``. ``type(sys)`` is the
# module type on leftover and leftover-free images.
_SKIP_TYPE_MRO = frozenset({object, type, type(sys)})
_MISSING = object()


def _mapping_like(ns: Any) -> bool:
    """True for ``dict`` / ``mappingproxy``. No ``collections.abc.Mapping``.

    Basilisk's WASI stub has no ``Mapping``. Class ``__dict__`` is a
    ``mappingproxy``, not a ``dict``, so ``isinstance(..., dict)`` would
    miss leftover host verbs on ``_LazyMod``.
    """
    get = getattr(ns, "get", None)
    items = getattr(ns, "items", None)
    contains = getattr(ns, "__contains__", None)
    return callable(get) and callable(items) and callable(contains)


def _is_lazy_mod(obj: Any) -> bool:
    """Basilisk ``_LazyMod`` instances stash source on ``_bsrc``."""
    ns = _module_namespace(obj)
    return bool(ns is not None and "_bsrc" in ns)


def _iter_type_dicts(obj: Any) -> Iterable[Any]:
    """Class ``__dict__`` along ``type(obj).__mro__``, no instance getattr.

    Leftover Cedar images keep the Candid hack / host verbs on ``_LazyMod``
    itself. Instance ``__dict__`` does not have them; ``getattr`` would
    ``_bload``. Walk the type dicts only — skip ``module`` / ``object``.
    """
    try:
        mro = type(obj).__mro__
    except Exception:
        return
    for cls in mro:
        if cls in _SKIP_TYPE_MRO:
            continue
        try:
            ns = object.__getattribute__(cls, "__dict__")
        except AttributeError:
            continue
        if _mapping_like(ns):
            yield ns


def _bind_from_class(val: Any, obj: Any, owner: Any) -> Any:
    """Unwrap a class-dict value without going through instance getattr."""
    if isinstance(val, (staticmethod, classmethod)):
        return val.__get__(obj, owner)
    if inspect.isfunction(val):
        try:
            params = list(inspect.signature(val).parameters.values())
        except (TypeError, ValueError):
            return val
        if (
            params
            and params[0].kind
            in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
            and params[0].default is inspect.Parameter.empty
            and params[0].name in {"self", "cls"}
        ):
            return val.__get__(obj, owner)
        return val
    getter = getattr(type(val), "__get__", None)
    if getter is not None and not isinstance(val, type):
        try:
            return val.__get__(obj, owner)
        except Exception:
            return val
    return val


def _type_lookup_names(obj: Any, name: str) -> tuple[str, ...]:
    """Instance name plus Python name-mangled class-body forms.

    ``__get_candid_interface_tmp_hack`` defined on ``_LazyMod`` is stored as
    ``_LazyMod__get_candid_interface_tmp_hack``. Leftover images keep that
    method on the class; ``setattr`` style keeps the unmangled name.
    """
    names = [name]
    if name.startswith("__") and not name.endswith("__"):
        try:
            mro = type(obj).__mro__
        except Exception:
            return (name,)
        for cls in mro:
            if cls in _SKIP_TYPE_MRO:
                continue
            names.append(f"_{cls.__name__.lstrip('_')}{name}")
    return tuple(dict.fromkeys(names))


def _type_attr(obj: Any, name: str, skip_module_type: bool = True) -> Any:
    """``name`` from ``_LazyMod`` (or another owner class), or ``_MISSING``."""
    try:
        mro = type(obj).__mro__
    except Exception:
        return _MISSING
    candidates = _type_lookup_names(obj, name)
    skip = set(_SKIP_TYPE_MRO) if skip_module_type else {object, type}
    for cls in mro:
        if cls in skip:
            continue
        try:
            ns = object.__getattribute__(cls, "__dict__")
        except AttributeError:
            continue
        contains = getattr(ns, "__contains__", None)
        if not callable(contains):
            continue
        for candidate in candidates:
            if candidate in ns:
                return _bind_from_class(ns[candidate], obj, cls)
        if name == "__get_candid_interface_tmp_hack" and _mapping_like(ns):
            for key, val in ns.items():
                if key.endswith("__get_candid_interface_tmp_hack"):
                    return _bind_from_class(val, obj, cls)
    return _MISSING


def _is_class_host_value(key: str, val: Any) -> bool:
    if key.endswith("__get_candid_interface_tmp_hack"):
        return True
    if key in _LAZY_KEYS or key.startswith("_"):
        return False
    return callable(val) or isinstance(val, (staticmethod, classmethod))


def _has_host_callables(ns: Any) -> bool:
    return any(
        key not in _LAZY_KEYS and callable(val) for key, val in ns.items()
    )


def _class_has_host_surface(obj: Any) -> bool:
    """Candid hack or public host verbs live on ``_LazyMod``, not the instance."""
    for ns in _iter_type_dicts(obj):
        if any(_is_class_host_value(key, val) for key, val in ns.items()):
            return True
    return False


def _has_host_surface(mod: Any) -> bool:
    ns = _module_namespace(mod)
    if ns is not None and _has_host_callables(ns):
        return True
    return _class_has_host_surface(mod)


def _is_unloaded_lazy(mod: Any) -> bool:
    """Unloaded LazyMod: has source, not marked loaded, no host surface."""
    if not _is_lazy_mod(mod):
        return False
    ns = _module_namespace(mod)
    if ns is None or ns.get("_bloaded") is True:
        return False
    return not _has_host_surface(mod)


def _module_attr(obj: Any, name: str, default: Any = None) -> Any:
    """Look up ``name`` without triggering Basilisk ``_LazyMod._bload``.

    ``getattr`` on a LazyMod calls ``__getattr__`` → ``_bload`` when the name
    is missing from ``__dict__``. ``_bload`` re-execs the module source. For
    the canister entry that re-runs ``Database.init`` and raises
    ``Database instance already exists`` before the host method runs.

    Leftover images put the Candid hack and some host verbs on ``_LazyMod``
    (the class), not the instance dict. Read those from the type dict.
    """
    if obj is None:
        return default
    ns = _module_namespace(obj)
    if ns is not None and name in ns:
        return ns[name]
    typed = _type_attr(obj, name)
    if typed is not _MISSING:
        return typed
    if _is_lazy_mod(obj):
        return default
    try:
        return getattr(obj, name)
    except AttributeError:
        return default


def _is_executed_host(mod: Any) -> bool:
    """True if ``mod`` already has a host surface (do not ``_bload`` it)."""
    if mod is None:
        return False
    ns = _module_namespace(mod)
    if ns is None:
        return False
    if _is_lazy_mod(mod) and ns.get("_bloaded") is False:
        return _has_host_surface(mod)
    return True


def _resolve_host_module(host_module: Any = None) -> Any:
    """The already-executed canister entry. Never ``import main``.

    Basilisk registers ``main`` as an unloaded LazyMod for the same file as
    ``__main__``. ``import main`` / ``getattr`` on that LazyMod re-execs
    ``__main__.py`` and re-inits the Database singleton.
    """
    if host_module is not None and not _is_unloaded_lazy(host_module):
        return host_module
    main = sys.modules.get("__main__")
    named = sys.modules.get("main")
    if _is_executed_host(main):
        return main
    if _is_executed_host(named):
        return named
    if host_module is not None:
        return host_module
    return main or named


def _invoke_candid_hack(hack: Any, module: Any) -> Any:
    if not callable(hack):
        return None
    try:
        return hack()
    except TypeError:
        try:
            return hack(module)
        except Exception:
            return None
    except Exception:
        return None


def _find_candid_hack(host_module: Any = None) -> tuple[Any, Any]:
    """Leftover Candid hack on host / ``__main__`` / ``main``. Never getattr."""
    seen: list[Any] = []
    for mod in (
        host_module,
        sys.modules.get("__main__"),
        sys.modules.get("main"),
    ):
        if mod is None or mod in seen:
            continue
        seen.append(mod)
        ns = _module_namespace(mod)
        if ns is not None:
            hack = ns.get("__get_candid_interface_tmp_hack")
            if callable(hack):
                return hack, mod
        typed = _type_attr(
            mod, "__get_candid_interface_tmp_hack", skip_module_type=False
        )
        if typed is not _MISSING and callable(typed):
            return typed, mod
    return None, None


def _is_verb_value(val: Any) -> bool:
    """Leftover ``@query`` / ``@update`` wrappers may not be ``callable``."""
    if val is None or isinstance(val, type):
        return False
    if callable(val) or isinstance(val, (staticmethod, classmethod)):
        return True
    if type(val).__name__ in {"Query", "Update", "Func", "Async"}:
        return True
    ns = _module_namespace(val)
    if ns is None:
        return False
    for key in ("func", "_func", "__wrapped__", "fn", "_fn", "callback"):
        if callable(ns.get(key)):
            return True
    return False


def _executed_callable(val: Any) -> Any:
    """Leftover-executed host verb, or leftover Query/Update inner function.

    Leftover Candid ingress calls leftover ``get_global(name)`` then the
    leftover-executed function. If leftover Query/Update is not
    ``callable``, leftover-executed function may still live on a known
    slot. Do not leftover ``__dict__`` unwrap.
    """
    if val is None:
        return val
    if callable(val) and not isinstance(val, type):
        return val
    for slot in ("func", "fn", "_fn", "handler"):
        try:
            inner = object.__getattribute__(val, slot)
        except AttributeError:
            continue
        if callable(inner) and not isinstance(inner, type):
            return inner
    return val


def _candid_verb(method: Any, host_module: Any = None) -> Any:
    """Leftover Candid name-dispatch: same leftover ``get_global(name)``.

    Ingress looks leftover verbs up by NAME in leftover-executed
    ``__main__`` globals. ``HostSecureORM`` on leftover-executed
    ``__main__`` uses that module's ``globals()``. Do not getattr leftover
    packed ``__main__``. Leftover-free unit-test hosts (not leftover
    ``_LazyMod``) still resolve by leftover-safe name lookup.
    """
    if not isinstance(method, str) or not method:
        return None
    here = globals()
    if method in here:
        return here[method]
    ns = _module_namespace(host_module)
    if ns is not None and method in ns:
        return ns[method]
    if host_module is not None and not _is_lazy_mod(host_module):
        return _module_attr(host_module, method)
    return None


def _ns_verb_names(ns: Any) -> set:
    names = set()
    if ns is None or not _mapping_like(ns):
        return names
    for key, val in ns.items():
        if not isinstance(key, str) or key.startswith("_"):
            continue
        if key in _LAZY_KEYS:
            continue
        if _is_verb_value(val):
            names.add(key)
    return names


def _callable_names(mod: Any) -> set:
    """Public leftover-safe host verbs. Never getattr leftover."""
    names = _ns_verb_names(_module_namespace(mod))
    for typed in _iter_type_dicts(mod):
        names |= _ns_verb_names(typed)
    return names


def _bsrc_def_names(mod: Any) -> set:
    """Public ``def name(`` in leftover ``_bsrc``. Never ``_bload``."""
    ns = _module_namespace(mod)
    if ns is None:
        return set()
    src = ns.get("_bsrc")
    if not isinstance(src, str) or "def " not in src:
        return set()
    names = set()
    i = 0
    while True:
        idx = src.find("def ", i)
        if idx < 0:
            break
        if idx > 0 and src[idx - 1] not in "\n\r\t ;":
            i = idx + 4
            continue
        start = idx + 4
        end = start
        while end < len(src) and (src[end].isalnum() or src[end] == "_"):
            end += 1
        name = src[start:end]
        rest = src[end:].lstrip()
        if name and not name.startswith("_") and rest.startswith("("):
            names.add(name)
        i = end + 1
    return names


def _surface_allowlist(host_module: Any, blocked: Iterable[str]) -> FrozenSet[str]:
    """Leftover-safe public host verbs anywhere leftover can see them."""
    names = set()
    seen = []
    for mod in (
        host_module,
        sys.modules.get("__main__"),
        sys.modules.get("main"),
    ):
        if mod is None or mod in seen:
            continue
        seen.append(mod)
        names |= _callable_names(mod)
        names |= _bsrc_def_names(mod)
    names -= set(blocked)
    return frozenset(names)


def _did_from_leftover_bsrc(host_module: Any = None) -> Any:
    """Packed leftover ``_bsrc`` may embed a DID. Never ``_bload``.

    Leftover-executed ``main.py`` also contains the ``service :`` marker as
    a string literal. Only treat ``_bsrc`` as a DID document.
    """
    seen = []
    for mod in (
        host_module,
        sys.modules.get("__main__"),
        sys.modules.get("main"),
    ):
        if mod is None or mod in seen:
            continue
        seen.append(mod)
        ns = _module_namespace(mod)
        if ns is None:
            continue
        src = ns.get("_bsrc")
        if not isinstance(src, str) or "service :" not in src:
            continue
        if "def " in src or "class " in src:
            continue
        if parse_candid_methods(src):
            return src
    return None


def load_allowed_methods(
    did_path: Optional[Path] = None,
    blocked: Iterable[str] = BLOCKED_METHODS,
    host_module: Any = None,
) -> FrozenSet[str]:
    """Allowlist from DID, leftover Candid hack, leftover ``_bsrc``, or verbs."""
    blocked_set = frozenset(blocked)
    path = Path(did_path) if did_path is not None else _DID_PATH
    did_text = None
    try:
        if path.is_file():
            did_text = path.read_text()
    except OSError:
        did_text = None
    if did_text:
        names = parse_candid_methods(did_text) - blocked_set
        if names:
            return names
    module = _resolve_host_module(host_module)
    hack, hack_mod = _find_candid_hack(module)
    did_text = _invoke_candid_hack(hack, hack_mod or module)
    if not did_text:
        did_text = _did_from_leftover_bsrc(module)
    if did_text:
        names = parse_candid_methods(did_text) - blocked_set
        if names:
            return names
    names = _surface_allowlist(module, blocked_set)
    if names:
        return names
    raise RpcError("Candid interface not found; host RPC disabled")


def json_args(args: Any) -> str:
    """Match the SPA: ``JSON.stringify(args)`` into ``extension_sync_call``."""
    if args is None:
        return "{}"
    if isinstance(args, str):
        return args
    return json.dumps(args)


def drive_result(result: Any) -> Any:
    """Consume a Basilisk ``Async`` generator that does not yield IC calls.

    Leftover WASI inspect is a stub. Detect leftover generators with leftover
    ``hasattr`` / leftover ``next``, never leftover inspect.
    """
    nxt = getattr(result, "__next__", None)
    send = getattr(result, "send", None)
    if not callable(nxt) or not callable(send):
        return result
    try:
        yielded = next(result)
        while True:
            if isinstance(yielded, tuple) and yielded and yielded[0] == "call":
                raise RuntimeError(
                    "async host method yielded an inter-canister call; "
                    "the REPL cannot drive IC calls yet"
                )
            yielded = result.send(None)
    except StopIteration as done:
        return done.value
    except TypeError:
        return result


def _as_permission(exc: BaseException, source: str = "") -> PermissionError:
    """AccessDenied with the exact permission and the call that failed."""
    from core.access import AccessDenied, format_quiet_denied, permission_name

    perm = permission_name(exc)
    src = source or getattr(exc, "source", "") or ""
    if isinstance(exc, AccessDenied):
        return AccessDenied(
            format_quiet_denied(perm, src), permission=perm, source=src
        )
    return PermissionError(format_quiet_denied(perm, src) if perm or src else str(exc))


def resolve_host_secure_orm() -> type:
    """``HostSecureORM`` from leftover-executed ``__main__``, never leftover ``_bload``.

    Leftover ``core.repl_host`` is a Basilisk ``_LazyMod`` at an unknown
    location. The class is a module-level name that only exists after
    ``_bload``, and ``_bload`` cannot run. Type-dict walks of leftover
    ``core.repl_host`` never see it. The canister entry leftover already
    executes defines the class on ``__main__`` / ``main``.
    """
    for key in ("__main__", "main"):
        mod = sys.modules.get(key)
        cls = _module_attr(mod, "HostSecureORM") if mod is not None else None
        if isinstance(cls, type) and cls.__name__ == "HostSecureORM":
            return cls
    here = sys.modules.get("core.repl_host")
    if here is not None and _is_lazy_mod(here):
        ns = _module_namespace(here) or {}
        if "HostSecureORM" not in ns:
            raise ImportError(
                "cannot import name 'HostSecureORM' from 'core.repl_host' "
                "(unknown location)"
            )
    here = sys.modules.get(__name__) or here
    cls = _module_attr(here, "HostSecureORM") if here is not None else None
    if isinstance(cls, type):
        return cls
    cls = globals().get("HostSecureORM")
    if isinstance(cls, type):
        return cls
    raise ImportError(
        "cannot import name 'HostSecureORM' from 'core.repl_host' "
        "(unknown location)"
    )


class HostSecureORM(SecureORM):
    """SecureORM plus host-method RPCs (``api`` / ``ext`` in the REPL)."""

    def __init__(
        self,
        *args: Any,
        host_module: Any = None,
        allowed_methods: Optional[Iterable[str]] = None,
        blocked_methods: Optional[Iterable[str]] = None,
        did_path: Optional[Path] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._host_module = host_module
        self._blocked_methods = (
            frozenset(blocked_methods)
            if blocked_methods is not None
            else BLOCKED_METHODS
        )
        self._allowed_methods: Optional[FrozenSet[str]] = (
            frozenset(allowed_methods) if allowed_methods is not None else None
        )
        self._did_path = Path(did_path) if did_path is not None else _DID_PATH
        self._stub_source = self._stub_source + "\n" + HOST_STUB_APPENDIX
        self._sandbox_hash = ""

    def actions(self) -> list:
        return list(super().actions()) + list(HOST_ACTIONS)

    def allowed_methods(self) -> FrozenSet[str]:
        if not self._allowed_methods:
            self._allowed_methods = load_allowed_methods(
                self._did_path,
                self._blocked_methods,
                host_module=self.host_module(),
            )
        return self._allowed_methods - self._blocked_methods

    def host_module(self) -> Any:
        if self._host_module is not None and not _is_unloaded_lazy(self._host_module):
            return self._host_module
        resolved = _resolve_host_module(self._host_module)
        if resolved is not None:
            self._host_module = resolved
        return resolved

    def handle_rpc(self, principal_id: str, action: str, kwargs: dict) -> Any:
        kwargs = dict(kwargs or {})
        if action.startswith("host."):
            return self._handle_host(action, kwargs)
        return super().handle_rpc(principal_id, action, kwargs)

    def _handle_host(self, action: str, kwargs: dict) -> Any:
        if action == "host.list_methods":
            return sorted(self.allowed_methods())
        if action == "host.call":
            method = kwargs.get("method")
            args = list(kwargs.get("args") or [])
            call_kwargs = dict(kwargs.get("kwargs") or {})
            return self._call_host(
                method, args, call_kwargs, source=api_call_source(method)
            )
        if action == "host.ext_sync":
            ext_name = kwargs.get("extension_name")
            fn_name = kwargs.get("function_name")
            return self._call_host(
                "extension_sync_call",
                [ext_name, fn_name, json_args(kwargs.get("args"))],
                {},
                source=ext_call_source(ext_name, fn_name),
            )
        if action == "host.ext_async":
            ext_name = kwargs.get("extension_name")
            fn_name = kwargs.get("function_name")
            return self._call_host(
                "extension_async_call",
                [ext_name, fn_name, json_args(kwargs.get("args"))],
                {},
                source=ext_call_source(ext_name, fn_name, async_call=True),
            )
        raise RpcError(f"unknown action {action!r}")

    def _call_host(
        self, method: Any, args: list, call_kwargs: dict, source: str = ""
    ) -> Any:
        if not isinstance(method, str) or not method:
            raise RpcError("host.call requires a method name")
        if method in self._blocked_methods:
            raise PermissionError(
                f"host method {method!r} is not callable from the REPL"
            )
        module = self.host_module()
        if module is None:
            raise RpcError("host module is not loaded")
        # Leftover Candid name-dispatch (``get_global(name)``). Do not
        # getattr leftover packed ``__main__``. Do not leftover ``__dict__``
        # unwrap. Live leftover Query is not callable and has no leftover
        # slots; leftover executed host verbs live in leftover-executed
        # globals. Resolve before the leftover DID allowlist so a real
        # ``@require`` verb is not hidden as ``not on the allowlist``.
        fn = _executed_callable(_candid_verb(method, module))
        allowed = self.allowed_methods()
        if method not in allowed:
            op = require_operation_of(fn)
            if not op:
                raise PermissionError(
                    f"host method {method!r} is not on the allowlist"
                )
            if not callable(fn):
                raise AccessDenied(
                    format_quiet_denied(op, source),
                    permission=op,
                    source=source,
                ) from None
        elif not callable(fn):
            raise RpcError(f"host method {method!r} is not defined")
        # Leftover WASI inspect is a stub. Call leftover-executed host
        # verbs by name. Do not leftover-import or leftover-call leftover
        # inspect on leftover invoke.
        from core.call_origin import host_call

        try:
            # Host verbs must not run Cedar with context.repl. Empty origin
            # matches a browser Candid ingress; extension_sync_call then
            # sets context.extension on the bridge like the UI does.
            with host_call():
                return drive_result(fn(*args, **call_kwargs))
        except PermissionError as exc:
            raise_quiet_access_denied(exc, source=source)
        except Exception as exc:
            if type(exc).__name__ == "AccessDenied":
                raise_quiet_access_denied(exc, source=source)
            raise
