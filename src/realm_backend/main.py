import base64
import hashlib
import importlib
import json
import sys
import traceback

import api

__basilisk_features__ = ["shell", "browse"]

from _cdk import (
    Async,
    Func,
    Opt,
    Principal,
    Query,
    Record,
    StableBTreeMap,
    Tuple,
    Variant,
    Vec,
    blob,
    ic,
    init,
    nat,
    nat16,
    post_upgrade,
    query,
    text,
    update,
    void,
)
from api.crypto import get_envelope as crypto_get_envelope
from api.crypto import group_add_member as crypto_group_add
from api.crypto import group_create as crypto_group_create
from api.crypto import group_delete as crypto_group_delete
from api.crypto import group_list as crypto_group_list
from api.crypto import group_members as crypto_group_members
from api.crypto import grant_many as crypto_grant_many
from api.crypto import group_remove_member as crypto_group_remove
from api.crypto import list_envelopes as crypto_list_envelopes
from api.crypto import revoke_many as crypto_revoke_many
from api.crypto import list_scopes as crypto_list_scopes
from api.crypto import revoke_group as crypto_revoke_group
from api.crypto import revoke_principal as crypto_revoke_principal
from api.crypto import share_with_group as crypto_share_group
from api.crypto import share_with_principal as crypto_share_principal
from api.crypto import store_envelope as crypto_store_envelope
from api.extensions import list_extensions
from api.ggg_entities import (
    list_objects,
    list_objects_paginated,
    search_objects,
)
from api.messaging import send_realm_message as _send_realm_message
from api.nft import (
    force_transfer_nft,
    freeze_nft,
    get_nft_canister_id,
    mint_land_nft,
    unfreeze_nft,
)
from api.registry import get_registry_info, register_realm
from api.status import get_status
from api.version_http import (
    http_request_upgrade_signal,
    is_version_path,
    version_http_response,
)
from api.user import (
    user_get,
    user_get_record_fields,
    user_register,
    user_update_private_data,
    user_update_public_profile,
)
from api.vetkeys import (
    derive_vetkey,
    derive_vetkey_for_sharing,
    get_root_public_key,
    get_vetkey_public_key,
)
from api.zones import get_zone_aggregation
from core.access import (
    AccessDenied,
    _check_access,
    api_call_source,
    ext_call_source,
    format_quiet_denied,
    product_shell_guard,
    raise_quiet_access_denied,
    require,
    require_controller,
    require_operation_of,
    set_controller,
)
from core.cross_quarter import (
    ResolutionStatus,
    classify_ref,
    walk_chain,
)
from core.realm_ref import RealmRef
from core.task_manager import TaskManager
from ggg import Call, Codex, Task, TaskSchedule, TaskStep
from ggg.system.user_profile import Operations
from ic_python_db import Database
from ic_python_logging import get_logger

# NOTE: Record/Variant types MUST be defined in this file (not imported from
# another module) because basilisk's Candid .did generator only parses main.py's
# AST for type definitions.  Duplicated from core/candid_types_realm.py.


class PaginationInfo(Record):
    page_num: int
    page_size: int
    total_items_count: int
    total_pages: int


class CanisterInfo(Record):
    canister_id: text
    canister_type: text


class QuarterInfoRecord(Record):
    name: text
    canister_id: text
    population: nat
    status: text
    index: nat
    is_capital: bool


class StatusRecord(Record):
    version: text
    status: text
    users_count: nat
    organizations_count: nat
    realms_count: nat
    mandates_count: nat
    tasks_count: nat
    transfers_count: nat
    instruments_count: nat
    codexes_count: nat
    disputes_count: nat
    licenses_count: nat
    trades_count: nat
    proposals_count: nat
    votes_count: nat
    commit: text
    commit_datetime: text
    extensions: Vec[text]
    test_mode: bool
    test_mode_ii_bypass: bool
    test_mode_user_self_registration: bool
    test_mode_demo_data: bool
    test_mode_skip_terms: bool
    test_mode_skip_passport_zkproof: bool
    realm_name: text
    realm_manifesto: text
    realm_welcome_message: text
    realm_stage: text
    open_registration: bool
    ai_assistant_enabled: bool
    user_profiles_count: nat
    canisters: Vec[CanisterInfo]
    registries: Vec[CanisterInfo]
    dependencies: Vec[text]
    python_version: text
    quarters: Vec[QuarterInfoRecord]
    is_quarter: bool
    parent_realm_canister_id: text
    accounting_currency: text
    accounting_currency_decimals: nat
    logo_url: text
    background_image_url: text
    file_registry_canister_id: text
    marketplace_canister_id: text
    realm_logo: text
    realm_description: text
    realm_welcome_image: text
    gos_environment: text


class UserGetRecord(Record):
    principal: Principal
    profiles: Vec[text]
    departments: Vec[text]
    nickname: text
    avatar: text
    private_data: text
    assigned_quarter: text


def _text_vec(values) -> Vec[text]:
    out = Vec[text]()
    for value in values or []:
        out.append(value)
    return out


def _user_get_record_from_fields(fields, assigned_quarter: str | None = None) -> UserGetRecord:
    """Build Candid UserGetRecord from a Python payload.

    Always includes ``departments: vec {}`` (empty list is fine). Uses
    ``assigned_quarter`` — never ``home_quarter`` — so the encoded record
    matches the frontend DID.
    """
    from core.user_get_record import candid_user_get_record_fields

    payload = candid_user_get_record_fields(fields, assigned_quarter=assigned_quarter)
    principal = payload["principal"]
    return UserGetRecord(
        principal=Principal.from_str(principal) if isinstance(principal, str) else principal,
        profiles=_text_vec(payload["profiles"]),
        departments=_text_vec(payload["departments"]),
        nickname=payload["nickname"],
        avatar=payload["avatar"],
        private_data=payload["private_data"],
        assigned_quarter=payload["assigned_quarter"],
    )


class ObjectsListRecordPaginated(Record):
    objects: Vec[text]
    pagination: PaginationInfo


class ObjectsListRecord(Record):
    objects: Vec[text]


class ExtensionsListRecord(Record):
    extensions: Vec[text]


class RealmResponseData(Variant):
    status: StatusRecord
    userGet: UserGetRecord
    error: text
    message: text
    objectsList: ObjectsListRecord
    objectsListPaginated: ObjectsListRecordPaginated
    extensionsList: ExtensionsListRecord


class RealmResponse(Record):
    success: bool
    data: RealmResponseData


class EnvelopeRecord(Record):
    scope: text
    principal_id: text
    wrapped_dek: text


class EnvelopeListRecord(Record):
    envelopes: Vec["EnvelopeRecord"]


class ScopeListRecord(Record):
    scopes: Vec[text]


class GroupRecord(Record):
    name: text
    description: text


class GroupListRecord(Record):
    groups: Vec["GroupRecord"]


class GroupMemberRecord(Record):
    principal_id: text
    role: text


class GroupMembersRecord(Record):
    members: Vec["GroupMemberRecord"]


class CryptoResponseData(Variant):
    envelope: EnvelopeRecord
    envelopeList: EnvelopeListRecord
    scopeList: ScopeListRecord
    group: GroupRecord
    groupList: GroupListRecord
    groupMembers: GroupMembersRecord
    error: text
    message: text


class CryptoResponse(Record):
    success: bool
    data: CryptoResponseData


class ExtensionCallArgs(Record):
    extension_name: text
    function_name: text
    args: text


class ExtensionCallResponse(Record):
    success: bool
    response: text


storage = StableBTreeMap[str, str](memory_id=1, max_key_size=100, max_value_size=10000)
Database.init(db_storage=storage, audit_enabled=True)

def _get_frontend_canister_id() -> str:
    """Read the frontend canister ID from the Realm entity."""
    try:
        from ggg import Realm
        realms = Realm.instances()
        realm = realms[0] if realms else None
        return str(realm.frontend_canister_id or "") if realm else ""
    except Exception:
        return ""

logger = get_logger("main")


def setup_gate_error(caller: str):
    """Load ``core.setup`` on demand.

    Use module attribute access (not ``from core.setup import …``). Basilisk
    ``_LazyMod`` plus ``hasattr`` swallows an ``AttributeError`` from ``_bload``
    and rewrites it as ``cannot import name … from 'core.setup'``.
    """
    import core.setup as _setup

    return _setup.setup_gate_error(caller)


# HostSecureORM lives on leftover-executed ``__main__``. Leftover
# ``core.repl_host`` is a Basilisk ``_LazyMod``: the class is a module-level
# name that only exists after ``_bload``, and ``_bload`` cannot run (Database
# already exists / leftover ``_bloaded``). Type-dict / getattr mazes never
# see it. Do not ``from core.repl_host import HostSecureORM``.
from ic_basilisk_toolkit.secure_orm import RpcError as _HostRpcError
from ic_basilisk_toolkit.secure_orm import SecureORM as _SecureORMBase
import inspect as _host_inspect
from pathlib import Path as _HostPath

_HOST_ACTIONS = (
    "host.call",
    "host.ext_sync",
    "host.ext_async",
    "host.list_methods",
)
_HOST_BLOCKED = frozenset(
    {
        "__shell__",
        "http_request",
        "http_request_update",
        "http_transform",
        "__get_candid_interface_tmp_hack",
    }
)
_HOST_STUB_APPENDIX = r'''
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
_HOST_LAZY_KEYS = frozenset({"_bsrc", "_bloaded", "_bloading", "_bload_count", "_bload"})
# Basilisk WASI ``types`` has no ``ModuleType`` — same class of stub as
# ``collections.abc.Mapping``. ``type(sys)`` is the module type.
_HOST_SKIP_MRO = frozenset({object, type, type(sys)})
_HOST_MISSING = object()


def _host_did_path():
    here = globals().get("__file__")
    if here:
        return _HostPath(here).parent / "realm_backend.did"
    return _HostPath("realm_backend.did")


def _host_mapping_like(ns):
    """True for ``dict`` / ``mappingproxy``. No ``collections.abc.Mapping``."""
    get = getattr(ns, "get", None)
    items = getattr(ns, "items", None)
    contains = getattr(ns, "__contains__", None)
    return callable(get) and callable(items) and callable(contains)


def _host_module_ns(obj):
    """Instance / module ns leftover-safely. Leftover ``__dict__`` may be
    a mappingproxy, not a ``dict`` — ``#342`` missed that and the
    instance-dict allowlist stayed empty, so leftover still raised
    ``Candid interface not found; host RPC disabled``.
    """
    try:
        ns = object.__getattribute__(obj, "__dict__")
    except AttributeError:
        return None
    return ns if _host_mapping_like(ns) else None


def _host_is_lazy(obj):
    ns = _host_module_ns(obj)
    return bool(ns is not None and "_bsrc" in ns)


def _host_bind_from_class(val, obj, owner):
    if isinstance(val, (staticmethod, classmethod)):
        return val.__get__(obj, owner)
    if _host_inspect.isfunction(val):
        try:
            params = list(_host_inspect.signature(val).parameters.values())
        except (TypeError, ValueError):
            return val
        if (
            params
            and params[0].kind
            in (
                _host_inspect.Parameter.POSITIONAL_ONLY,
                _host_inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
            and params[0].default is _host_inspect.Parameter.empty
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


def _host_type_lookup_names(obj, name):
    names = [name]
    if name.startswith("__") and not name.endswith("__"):
        try:
            mro = type(obj).__mro__
        except Exception:
            return (name,)
        for cls in mro:
            if cls in _HOST_SKIP_MRO:
                continue
            names.append(f"_{cls.__name__.lstrip('_')}{name}")
    return tuple(dict.fromkeys(names))


def _host_type_attr(obj, name, skip_module_type=True):
    try:
        mro = type(obj).__mro__
    except Exception:
        return _HOST_MISSING
    candidates = _host_type_lookup_names(obj, name)
    skip = set(_HOST_SKIP_MRO) if skip_module_type else {object, type}
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
                return _host_bind_from_class(ns[candidate], obj, cls)
        # Name-mangled leftover hack: any key ending with the Candid name.
        if name == "__get_candid_interface_tmp_hack" and _host_mapping_like(ns):
            for key, val in ns.items():
                if key.endswith("__get_candid_interface_tmp_hack"):
                    return _host_bind_from_class(val, obj, cls)
    return _HOST_MISSING


def _host_module_attr(obj, name, default=None):
    """Look up ``name`` without leftover ``_LazyMod._bload`` / Mapping."""
    if obj is None:
        return default
    ns = _host_module_ns(obj)
    if ns is not None and name in ns:
        return ns[name]
    typed = _host_type_attr(obj, name)
    if typed is not _HOST_MISSING:
        return typed
    if _host_is_lazy(obj):
        return default
    try:
        return getattr(obj, name)
    except AttributeError:
        return default


def _host_iter_type_dicts(obj):
    try:
        mro = type(obj).__mro__
    except Exception:
        return
    for cls in mro:
        if cls in _HOST_SKIP_MRO:
            continue
        try:
            ns = object.__getattribute__(cls, "__dict__")
        except AttributeError:
            continue
        if _host_mapping_like(ns):
            yield ns


def _host_is_class_host_value(key, val):
    if key.endswith("__get_candid_interface_tmp_hack"):
        return True
    if key in _HOST_LAZY_KEYS or key.startswith("_"):
        return False
    return callable(val) or isinstance(val, (staticmethod, classmethod))


def _host_has_callables(ns):
    return any(
        key not in _HOST_LAZY_KEYS and callable(val) for key, val in ns.items()
    )


def _host_class_has_surface(obj):
    for ns in _host_iter_type_dicts(obj):
        if any(_host_is_class_host_value(key, val) for key, val in ns.items()):
            return True
    return False


def _host_has_surface(mod):
    ns = _host_module_ns(mod)
    if ns is not None and _host_has_callables(ns):
        return True
    return _host_class_has_surface(mod)


def _host_is_unloaded_lazy(mod):
    if not _host_is_lazy(mod):
        return False
    ns = _host_module_ns(mod)
    if ns is None or ns.get("_bloaded") is True:
        return False
    return not _host_has_surface(mod)


def _host_is_executed(mod):
    if mod is None:
        return False
    ns = _host_module_ns(mod)
    if ns is None:
        return False
    if _host_is_lazy(mod) and ns.get("_bloaded") is False:
        return _host_has_surface(mod)
    return True


def _host_resolve_module(host_module=None):
    """Already-executed canister entry. Never ``import main`` / ``_bload``."""
    if host_module is not None and not _host_is_unloaded_lazy(host_module):
        return host_module
    main = sys.modules.get("__main__")
    named = sys.modules.get("main")
    if _host_is_executed(main):
        return main
    if _host_is_executed(named):
        return named
    if host_module is not None:
        return host_module
    return main or named


def _host_parse_candid(did_text):
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


def _host_invoke_hack(hack, module):
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


def _host_find_candid_hack(host_module=None):
    """Leftover Candid hack: instance / type dict on host, ``__main__``, ``main``.

    Live leftover has HostSecureORM and the Candid verbs on leftover-
    executed ``__main__``, but no ``__get_candid_interface_tmp_hack`` on
    leftover ``__main__`` / ``main``. Still look leftover-safely. Never
    getattr leftover. Include ``type(sys)`` — some leftover images store
    the hack on the module type itself.
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
        ns = _host_module_ns(mod)
        if ns is not None:
            hack = ns.get("__get_candid_interface_tmp_hack")
            if callable(hack):
                return hack, mod
        typed = _host_type_attr(
            mod, "__get_candid_interface_tmp_hack", skip_module_type=False
        )
        if typed is not _HOST_MISSING and callable(typed):
            return typed, mod
    return None, None


def _host_is_verb_value(val):
    """Leftover ``@query`` / ``@update`` wrappers may not be ``callable``."""
    if val is None or isinstance(val, type):
        return False
    if callable(val) or isinstance(val, (staticmethod, classmethod)):
        return True
    cls_name = type(val).__name__
    if cls_name in {"Query", "Update", "Func", "Async"}:
        return True
    ns = _host_module_ns(val)
    if ns is None:
        return False
    for key in ("func", "_func", "__wrapped__", "fn", "_fn", "callback"):
        inner = ns.get(key)
        if callable(inner):
            return True
    return False


def _host_executed_callable(val):
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


def _host_candid_verb(method, host_module=None):
    """Leftover Candid name-dispatch: same leftover ``get_global(name)``.

    Ingress looks leftover verbs up by NAME in leftover-executed
    ``__main__`` globals, not leftover packed ``__main__`` attributes.
    ``HostSecureORM._call_host`` is defined in this module, so
    ``globals()`` is that leftover-executed namespace. Do not getattr
    leftover packed ``__main__``. Leftover-free unit-test hosts (not
    leftover ``_LazyMod``) still resolve by leftover-safe name lookup.
    """
    if not isinstance(method, str) or not method:
        return None
    here = globals()
    if method in here:
        return here[method]
    ns = _host_module_ns(host_module)
    if ns is not None and method in ns:
        return ns[method]
    if host_module is not None and not _host_is_lazy(host_module):
        return _host_module_attr(host_module, method)
    return None


def _host_ns_verb_names(ns):
    names = set()
    if ns is None or not _host_mapping_like(ns):
        return names
    for key, val in ns.items():
        if not isinstance(key, str) or key.startswith("_"):
            continue
        if key in _HOST_LAZY_KEYS:
            continue
        if _host_is_verb_value(val):
            names.add(key)
    return names


def _host_callable_names(mod):
    """Public leftover-safe host verbs. Never getattr leftover."""
    names = _host_ns_verb_names(_host_module_ns(mod))
    for typed in _host_iter_type_dicts(mod):
        names |= _host_ns_verb_names(typed)
    return names


def _host_bsrc_def_names(mod):
    """Public ``def name(`` in leftover ``_bsrc``. Never ``_bload``."""
    ns = _host_module_ns(mod)
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


def _host_surface_allowlist(host_module, blocked):
    """Leftover-safe public host verbs anywhere leftover can see them.

    Live leftover has no DID and no Candid hack. Instance dict may or
    may not list ``get_sandbox_config``. Direct Candid still works, so
    the verbs exist leftover-safe on leftover-executed instance ns
    (mappingproxy), leftover ``_LazyMod`` type dict, leftover Query
    wrappers, or leftover ``_bsrc`` ``def`` names.
    """
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
        names |= _host_callable_names(mod)
        names |= _host_bsrc_def_names(mod)
    names -= set(blocked)
    return frozenset(names)


def _host_did_from_leftover_bsrc(host_module=None):
    """Packed leftover ``_bsrc`` may embed a DID. Never ``_bload``.

    Leftover-executed ``main.py`` also contains the ``service :`` marker as
    a string literal in these helpers. Only treat ``_bsrc`` as a DID
    document — not leftover Python source.
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
        ns = _host_module_ns(mod)
        if ns is None:
            continue
        src = ns.get("_bsrc")
        if not isinstance(src, str) or "service :" not in src:
            continue
        if "def " in src or "class " in src:
            continue
        names = _host_parse_candid(src)
        if names:
            return src
    return None


def _host_load_allowed(did_path=None, blocked=_HOST_BLOCKED, host_module=None):
    blocked_set = frozenset(blocked)
    path = _HostPath(did_path) if did_path is not None else _host_did_path()
    did_text = None
    try:
        if path.is_file():
            did_text = path.read_text()
    except OSError:
        did_text = None
    if did_text:
        names = _host_parse_candid(did_text) - blocked_set
        if names:
            return names
    module = _host_resolve_module(host_module)
    hack, hack_mod = _host_find_candid_hack(module)
    did_text = _host_invoke_hack(hack, hack_mod or module)
    if not did_text:
        did_text = _host_did_from_leftover_bsrc(module)
    if did_text:
        names = _host_parse_candid(did_text) - blocked_set
        if names:
            return names
    names = _host_surface_allowlist(module, blocked_set)
    if names:
        return names
    raise _HostRpcError("Candid interface not found; host RPC disabled")


def _host_json_args(args):
    if args is None:
        return "{}"
    if isinstance(args, str):
        return args
    return json.dumps(args)


def _host_drive_result(result):
    """Leftover WASI inspect is a stub. Never leftover-inspect leftover generators."""
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


class HostSecureORM(_SecureORMBase):
    """SecureORM plus host-method RPCs. Defined here so leftover ``__main__`` has it."""

    def __init__(
        self,
        *args,
        host_module=None,
        allowed_methods=None,
        blocked_methods=None,
        did_path=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._host_module = host_module
        self._blocked_methods = (
            frozenset(blocked_methods) if blocked_methods is not None else _HOST_BLOCKED
        )
        self._allowed_methods = (
            frozenset(allowed_methods) if allowed_methods is not None else None
        )
        self._did_path = _HostPath(did_path) if did_path is not None else _host_did_path()
        self._stub_source = self._stub_source + "\n" + _HOST_STUB_APPENDIX
        self._sandbox_hash = ""

    def actions(self):
        return list(super().actions()) + list(_HOST_ACTIONS)

    def allowed_methods(self):
        if not self._allowed_methods:
            self._allowed_methods = _host_load_allowed(
                self._did_path,
                self._blocked_methods,
                host_module=self.host_module(),
            )
        return self._allowed_methods - self._blocked_methods

    def host_module(self):
        if self._host_module is not None and not _host_is_unloaded_lazy(self._host_module):
            return self._host_module
        resolved = _host_resolve_module(self._host_module)
        if resolved is not None:
            self._host_module = resolved
        return resolved

    def handle_rpc(self, principal_id, action, kwargs):
        kwargs = dict(kwargs or {})
        if action.startswith("host."):
            return self._handle_host(action, kwargs)
        return super().handle_rpc(principal_id, action, kwargs)

    def _handle_host(self, action, kwargs):
        if action == "host.list_methods":
            return sorted(self.allowed_methods())
        if action == "host.call":
            method = kwargs.get("method")
            return self._call_host(
                method,
                list(kwargs.get("args") or []),
                dict(kwargs.get("kwargs") or {}),
                source=api_call_source(method),
            )
        if action == "host.ext_sync":
            ext_name = kwargs.get("extension_name")
            fn_name = kwargs.get("function_name")
            return self._call_host(
                "extension_sync_call",
                [ext_name, fn_name, _host_json_args(kwargs.get("args"))],
                {},
                source=ext_call_source(ext_name, fn_name),
            )
        if action == "host.ext_async":
            ext_name = kwargs.get("extension_name")
            fn_name = kwargs.get("function_name")
            return self._call_host(
                "extension_async_call",
                [ext_name, fn_name, _host_json_args(kwargs.get("args"))],
                {},
                source=ext_call_source(ext_name, fn_name, async_call=True),
            )
        raise _HostRpcError(f"unknown action {action!r}")

    def _call_host(self, method, args, call_kwargs, source=""):
        if not isinstance(method, str) or not method:
            raise _HostRpcError("host.call requires a method name")
        if method in self._blocked_methods:
            raise PermissionError(
                f"host method {method!r} is not callable from the REPL"
            )
        module = self.host_module()
        if module is None:
            raise _HostRpcError("host module is not loaded")
        # Leftover Candid name-dispatch (``get_global(name)``). Do not
        # getattr leftover packed ``__main__``. Do not leftover ``__dict__``
        # unwrap. Resolve before the leftover DID allowlist so a real
        # ``@require`` verb is not hidden as ``not on the allowlist``.
        fn = _host_executed_callable(_host_candid_verb(method, module))
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
            raise _HostRpcError(f"host method {method!r} is not defined")
        # Leftover WASI inspect is a stub. Call leftover-executed host
        # verbs by name. Do not leftover-import or leftover-call leftover
        # inspect on leftover invoke.
        from core.call_origin import host_call

        try:
            with host_call():
                return _host_drive_result(fn(*args, **call_kwargs))
        except PermissionError as exc:
            raise_quiet_access_denied(exc, source=source)
        except Exception as exc:
            if type(exc).__name__ == "AccessDenied":
                raise_quiet_access_denied(exc, source=source)
            raise


def _init_secure_orm():
    """Build the Cedar-gated ORM singleton for the sandboxed REPL (realms#313)."""
    from core.cedar_schema_runtime import collect_ggg_schema_entities

    import ggg
    from core import cedar_authz

    included, schema = collect_ggg_schema_entities()
    return HostSecureORM(
        engine=cedar_authz._get_engine(),
        namespace="Realm",
        entities=included,
        schema=schema,
        principal_type="User",
        principal_entity=ggg.User,
        shell_context={"repl": True},
        host_module=sys.modules[__name__],
    )


secure_orm = None
_secure_orm_error = ""


def _try_init_secure_orm():
    """Eager at import; retried on first ``__shell__`` if import-time failed."""
    global secure_orm, _secure_orm_error
    if secure_orm is not None:
        return secure_orm
    try:
        secure_orm = _init_secure_orm()
        _secure_orm_error = ""
        return secure_orm
    except Exception as exc:
        _secure_orm_error = f"{type(exc).__name__}: {exc}"
        logger.warning(f"secure_orm unavailable: {_secure_orm_error}")
        secure_orm = None
        return None


_try_init_secure_orm()

# HTTP types used by http_transform endpoint
from _cdk import (
    HttpResponse,
    HttpTransformArgs,
)

# Types for incoming HTTP requests (http_request query)
Header = Tuple[str, str]


class HttpRequest(Record):
    method: str
    url: str
    headers: Vec["Header"]
    body: blob


class HttpResponseIncoming(Record):
    status_code: nat16
    headers: Vec["Header"]
    body: blob
    streaming_strategy: Opt["StreamingStrategy"]
    upgrade: Opt[bool]


class StreamingStrategy(Variant):
    Callback: "CallbackStrategy"


class CallbackStrategy(Record):
    callback: "Callback"  # type: ignore
    token: "StreamingToken"


Callback = Func(Query[["StreamingToken"], "StreamingCallbackHttpResponse"])


class StreamingCallbackHttpResponse(Record):
    body: blob
    token: Opt["StreamingToken"]


class StreamingToken(Record):
    key: str


@query
def policy_status() -> str:
    """Whether Cedar is actually deciding calls, as JSON.

    A realm running without enforcement should be able to say so out loud. The
    failure worth catching is a deployment that believes it has Cedar and does
    not: every request succeeds, nothing is enforced, and no behaviour differs.
    """
    from core import cedar_authz

    return json.dumps(cedar_authz.status())


@query
def status() -> RealmResponse:
    try:
        logger.info("Status query executed")
        return RealmResponse(
            success=True, data=RealmResponseData(status=StatusRecord(**get_status()))
        )
    except Exception as e:
        logger.error(f"Error getting status: {str(e)}\n{traceback.format_exc()}")
        return RealmResponse(success=False, data=RealmResponseData(error=str(e)))


@query
def get_runtime_flags() -> text:
    """Return runtime test flags and join-relevant realm fields without heavy status()."""
    try:
        from core.runtime_flags import get_runtime_flags_payload

        return json.dumps(get_runtime_flags_payload())
    except Exception as e:
        logger.error(f"get_runtime_flags error: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@query
def get_quarter_info() -> RealmResponse:
    """Get quarter information for this realm (workaround for Basilisk Record field limitation)"""
    try:
        import json as _json

        from ggg import Quarter, Realm

        quarters = []
        is_quarter = False
        is_capital = False
        parent_realm_canister_id = ""

        first_realm = Realm.load("1")
        if first_realm:
            is_quarter = bool(getattr(first_realm, "is_quarter", False))
            is_capital = bool(getattr(first_realm, "is_capital", False))
            parent_realm_canister_id = (
                getattr(first_realm, "federation_realm_id", "") or ""
            )
            from ggg import User
            own_id = ic.id().to_str()
            quarter_entities = list(Quarter.instances())

            if quarter_entities:
                all_users = list(User.instances())
                capital_pop = sum(
                    1 for u in all_users
                    if (getattr(u, "home_quarter", "") or "") in ("", own_id)
                )
                quarters.append(
                    {
                        "name": "Capital",
                        "canister_id": own_id,
                        "population": capital_pop,
                        "status": "active",
                        "index": 0,
                        "is_capital": True,
                    }
                )
                for q in quarter_entities:
                    qcid = q.canister_id or ""
                    # Users who joined the quarter directly live in the quarter's
                    # own table — the local home_quarter scan misses them. The
                    # population-sync task keeps q.population fresh; trust
                    # whichever is larger.
                    local_scan = sum(
                        1 for u in all_users
                        if (getattr(u, "home_quarter", "") or "") == qcid
                    )
                    q_pop = max(local_scan, int(q.population or 0))
                    quarters.append(
                        {
                            "name": q.name or "",
                            "canister_id": qcid,
                            "population": q_pop,
                            "status": q.status or "active",
                            "index": int(getattr(q, "index", 0) or 0),
                            "is_capital": False,
                        }
                    )
            else:
                quarters.append(
                    {
                        "name": "Capital",
                        "canister_id": own_id,
                        "population": User.count(),
                        "status": "active",
                        "index": 0,
                        "is_capital": True,
                    }
                )

        result = _json.dumps(
            {
                "quarters": quarters,
                "is_quarter": is_quarter,
                "is_capital": is_capital,
                "parent_realm_canister_id": parent_realm_canister_id,
            }
        )
        return RealmResponse(success=True, data=RealmResponseData(message=result))
    except Exception as e:
        logger.error(f"Error getting quarter info: {str(e)}\n{traceback.format_exc()}")
        return RealmResponse(success=False, data=RealmResponseData(error=str(e)))


@query
def get_extensions() -> RealmResponse:
    """Get all available extensions with their metadata"""
    return list_extensions(ic.caller().to_str())


def _assign_quarter(principal: str, realm, quarters, preferred_quarter: str) -> str:
    """Assign a quarter via the sandboxed federation policy, or fall back.

    The federation codex (``Realm.federation_codex`` or the seeded
    ``quarter_assignment`` module) may define ``assign_quarter``. It runs in
    the subinterpreter over plain quarter projections (issue #265); a rejection
    raises so the user sees why (e.g. "quarter is full").

    When no policy is installed the default is deterministic random assignment
    (hash of principal), which guarantees uniform load.
    """
    active_quarters = [q for q in quarters if q.status == "active"]
    if not active_quarters:
        return ""

    from core.codex_hooks import call_assign_quarter

    result = call_assign_quarter(principal, active_quarters, preferred_quarter)
    if result:
        return str(result)

    # Default: deterministic random (hash-based)
    idx = hash(principal) % len(active_quarters)
    return active_quarters[idx].canister_id


def _default_registration_profile(realm) -> str:
    """Codex-defined default profile for codeless open registration.

    Read through the codex hook API (issue #244) — ``get_config`` merges the
    active codex's declared configuration over ``Realm.manifest_data`` — so
    the platform never hardcodes user types (issue #242); falls back to
    ``member``.
    """
    try:
        from core.codex_hooks import get_config

        default = (
            (get_config().get("onboarding") or {}).get("registration") or {}
        ).get("default_profile")
        if default and isinstance(default, str):
            return default.strip()
    except Exception:
        pass
    return "member"


def _join_fail(code: str, message: str) -> RealmResponse:
    """Candid join failure: English ``error`` tagged with a stable code (issue #393)."""
    from core.api_errors import tagged_error

    return RealmResponse(
        success=False,
        data=RealmResponseData(error=tagged_error(code, message)),
    )


def _setup_gate_json(gate_err: str) -> str:
    from core.api_errors import SETUP_NOT_OPEN, parse_tagged_error

    code, message = parse_tagged_error(gate_err)
    if not code:
        code, message = SETUP_NOT_OPEN, gate_err
    return json.dumps({"success": False, "error_code": code, "error": message})


@update
def join_realm(
    profile: str, preferred_quarter: text, invite_code_checksum_hex: text
) -> Async[RealmResponse]:
    """Register the caller in the realm.

    The granted profile is resolved server-side (issue #242): the invite
    code's profile when a code is used, otherwise the codex-defined default
    (``onboarding.registration.default_profile``, fallback ``member``). The
    ``profile`` argument is optional and only validated for consistency —
    pass "" to accept the resolved profile.

    Registration modes:
    - Code-based (default): caller must provide a valid invite_code.
      The code's profile determines the granted role.
    - Open registration: if Realm.open_registration is True, members
      may join without a code. Admin always requires a code.
    - Controller bypass: IC controllers can join with any profile
      without a code (for manual dfx deploys).
    - Test mode (two flags):
      - test_mode_user_self_registration: allows codeless member/developer
        join (open-registration equivalent) and admin join without a code.
        Internet Identity bypass alone does not enable codeless join.
      - test_mode_ii_bypass: with an invite checksum present, sha256-matched
        literals "admin", "member", and "dev"/"developer" grant the respective
        profiles (staging can type those strings in the invite field).
        user_self_registration also enables those checksum shortcuts.

    On a *new* registration against a quarter, pushes the live
    ``User.count()`` to the capital immediately so join-target populations
    stay fresh without waiting on the recurring gossip task (issue #156).
    """
    try:
        caller = ic.caller().to_str()
        # The anonymous principal must never become a member: a User record for
        # 2vxsx-fae makes every pre-auth membership probe answer "already a
        # member" (any unauthenticated actor IS 2vxsx-fae), corrupting the join
        # flow for all users. Races in embedded frontends have produced exactly
        # this (anonymous actor + test-mode code → anonymous admin).
        if caller == "2vxsx-fae":
            from core.api_errors import ANONYMOUS_CANNOT_JOIN

            return _join_fail(
                ANONYMOUS_CANNOT_JOIN,
                "Anonymous principal cannot join a realm — sign in first",
            )
        from ggg import Quarter, Realm, User

        realm = Realm.load("1")
        gate_err = setup_gate_error(caller)
        if gate_err:
            from core.api_errors import SETUP_NOT_OPEN

            return _join_fail(SETUP_NOT_OPEN, gate_err)
        has_invite = bool(invite_code_checksum_hex and invite_code_checksum_hex.strip())
        granted_profile = profile
        # Organization the invite code links to (per-department staff invites,
        # issue #241). Applied after registration succeeds.
        invite_department = ""
        # Full consume payload — carries citizen-import metadata for principal
        # binding (issue #241). Empty for test-mode and codeless joins.
        invite_consume_data = {}

        # --- Determine access ---

        is_controller = False
        try:
            is_controller = ic.is_controller(caller)
        except Exception:
            pass

        from core.runtime_flags import are_test_join_shortcuts_enabled

        # Open registration: who may join at all. Ordinary product configuration.
        _self_reg_bypass = bool(getattr(realm, "test_mode_user_self_registration", False))
        # Test shortcuts: who may join *as an administrator* without an invite.
        # Deliberately not derived from _self_reg_bypass.
        _test_code_bypass = are_test_join_shortcuts_enabled()

        if has_invite:
            # Test mode shortcuts: sha256("admin") / sha256("member") / sha256("dev") / sha256("developer") grant respective profiles
            _ADMIN_TEST_CODE_CHECKSUM_HEX = "8c6976e5b5410415bde908bd4dee15dfb167a9c873fc4bb8a81f6f2ab448a918"
            _MEMBER_TEST_CODE_CHECKSUM_HEX = "e31ab643c44f7a0ec824b59d1194d60dac334200d845e61d2d289daa0f087ea4"
            _DEV_TEST_CODE_CHECKSUM_HEX = "ef260e9aa3c673af240d17a2660480361a8e081d1ffeca2a5ed0e3219fc18567"
            _DEVELOPER_TEST_CODE_CHECKSUM_HEX = "88fa0d759f845b47c044c2cd44e29082cf6fea665c30c146374ec7c8f3d699e3"
            if _test_code_bypass and invite_code_checksum_hex == _ADMIN_TEST_CODE_CHECKSUM_HEX:
                granted_profile = "admin"
            elif _test_code_bypass and invite_code_checksum_hex == _MEMBER_TEST_CODE_CHECKSUM_HEX:
                granted_profile = "member"
            elif _test_code_bypass and invite_code_checksum_hex in (_DEV_TEST_CODE_CHECKSUM_HEX, _DEVELOPER_TEST_CODE_CHECKSUM_HEX):
                granted_profile = "developer"
            else:
                # Code-based path: validate and consume the invite code
                from ggg.system.registration_code import consume_registration_code
                consume_result = consume_registration_code(invite_code_checksum_hex, caller)

                if not consume_result.get("success"):
                    from core.api_errors import INVALID_INVITE

                    error_msg = consume_result.get("error", "Invalid or expired invitation code")
                    return _join_fail(INVALID_INVITE, error_msg)

                consume_data = consume_result.get("data", {})
                if not isinstance(consume_data, dict):
                    consume_data = consume_result
                invite_profile = consume_data.get("profile", "member")
                if profile and profile != invite_profile:
                    from core.api_errors import INVITE_PROFILE_MISMATCH

                    return _join_fail(
                        INVITE_PROFILE_MISMATCH,
                        f"Invitation grants '{invite_profile}' profile, but '{profile}' was requested",
                    )
                granted_profile = invite_profile
                invite_department = (consume_data.get("department") or "").strip()
                invite_consume_data = consume_data

        elif is_controller:
            # Controllers can join with any profile without a code
            pass

        elif profile == "admin":
            if not _test_code_bypass:
                from core.api_errors import INVITE_REQUIRED

                return _join_fail(
                    INVITE_REQUIRED,
                    "Admin registration requires an invitation code.",
                )

        else:
            # Member/developer join without code: allowed if open_registration is on or self-reg bypass
            open_reg = realm and realm.open_registration
            if not open_reg and not _self_reg_bypass:
                from core.api_errors import INVITE_REQUIRED

                return _join_fail(
                    INVITE_REQUIRED,
                    "Registration requires an invitation code.",
                )

        # --- Coordinator-only capital guard (issue #156) ---
        # Once this realm acts as a capital (it is not itself a quarter and has
        # >=1 active sub-quarter) it stops accepting brand-new members directly:
        # new members register on a quarter (the /join page routes them there).
        # Controllers and test-mode bypass stay exempt so admin tooling and demos
        # still work (ii_bypass + typed test checksum counts), and existing members
        # can idempotently re-join.
        _is_quarter_realm = bool(getattr(realm, "is_quarter", False))
        if realm and not _is_quarter_realm and not is_controller and not _test_code_bypass:
            own_id = ic.id().to_str()
            has_active_sub = any(
                (q.canister_id and q.canister_id != own_id and (q.status or "active") == "active")
                for q in Quarter.instances()
            )
            already_member = False
            try:
                already_member = bool(User[caller])
            except Exception:
                already_member = False
            if has_active_sub and not already_member:
                from core.api_errors import COORDINATOR_ONLY

                return _join_fail(
                    COORDINATOR_ONLY,
                    "This realm is coordinator-only. Please join through a quarter.",
                )

        # No explicit profile and no invite: codex-defined default (issue #242).
        if not (granted_profile or "").strip():
            granted_profile = _default_registration_profile(realm)

        # --- Quarter bootstrap guard: reject new members until dashboard is ready ---
        if realm and _is_quarter_realm and not is_controller:
            already_member = False
            try:
                already_member = bool(User[caller])
            except Exception:
                already_member = False
            if not already_member:
                from core.join_targets import JOIN_QUARTER_NOT_READY, is_dashboard_installed
                from core.runtime_extensions import list_installed, resolve_extension_id

                dash = resolve_extension_id("member_dashboard")
                if not is_dashboard_installed(list_installed(), dash):
                    from core.api_errors import QUARTER_NOT_READY

                    return _join_fail(QUARTER_NOT_READY, JOIN_QUARTER_NOT_READY)

        # --- Register user and assign quarter ---

        was_new_user = False
        try:
            was_new_user = User[caller] is None
        except Exception:
            was_new_user = True

        user = user_get_record_fields(user_register(caller, granted_profile))
        profiles = Vec[text]()
        if "profiles" in user and user["profiles"]:
            for p in user["profiles"]:
                profiles.append(p)

        # Citizen-import binding (issue #241): attach the imported census
        # record (nickname, private data) to the redeeming principal. A
        # pre-assigned quarter from the import wins over no preference.
        # Snapshot the caller-supplied quarter preference: invite-bound
        # imports may substitute their own (possibly stale) pre-assignment
        # below, which keeps the lenient fallback — strict validation applies
        # only to what the client explicitly asked for.
        explicit_preferred_quarter = (preferred_quarter or "").strip()

        if invite_consume_data:
            try:
                from core.citizen_import import bind_citizen

                u = User[caller]
                if u:
                    imported_quarter = bind_citizen(u, invite_consume_data)
                    if imported_quarter and not (preferred_quarter or "").strip():
                        preferred_quarter = imported_quarter
            except Exception as bind_err:
                logger.error(f"Citizen binding failed for {caller}: {bind_err}")

        # Department-linked invite (issue #241): add the redeemer to the org so
        # the code's prepopulated permissions/extensions apply immediately.
        if invite_department:
            try:
                from core.membership import add_department_member
                from ggg import Department

                dept = Department[invite_department]
                u = User[caller]
                if dept and u:
                    add_department_member(dept, u)
                    logger.info(
                        f"Invite code added {caller} to organization '{invite_department}'"
                    )
                elif not dept:
                    logger.warning(
                        f"Invite code references unknown organization '{invite_department}'"
                    )
            except Exception as dept_err:
                logger.error(
                    f"Failed to add {caller} to organization '{invite_department}': {dept_err}"
                )

        # Position-linked invite (issue #241): appoint the redeemer to the seat.
        # Best-effort — a full roster or closed position never fails the join.
        invite_position = (invite_consume_data.get("position") or "").strip() if invite_consume_data else ""
        if invite_position:
            try:
                from ggg import Position, appoint

                pos = Position[invite_position]
                u = User[caller]
                if pos and u:
                    appointment = appoint(pos, u)
                    if appointment:
                        logger.info(
                            f"Invite code appointed {caller} to position '{invite_position}'"
                        )
                else:
                    logger.warning(
                        f"Invite code references unknown position '{invite_position}'"
                    )
            except Exception as pos_err:
                logger.error(
                    f"Failed to appoint {caller} to position '{invite_position}': {pos_err}"
                )

        assigned_quarter_canister_id = ""
        quarters = list(Quarter.instances()) if realm else []
        if realm and quarters:
            requested_quarter = (preferred_quarter or "").strip()
            if requested_quarter and requested_quarter == explicit_preferred_quarter:
                known_quarters = {
                    str(q.canister_id) for q in quarters if q.status == "active"
                }
                if requested_quarter not in known_quarters:
                    # An explicit but unknown/inactive quarter is a client
                    # contract violation — silently joining the capital (the
                    # previous behavior) misleads clients expecting quarter
                    # assignment (CHAOS-3 finding).
                    return RealmResponse(
                        success=False,
                        data=RealmResponseData(
                            error=(
                                f"Unknown or inactive quarter '{requested_quarter}'; "
                                f"omit preferred_quarter for automatic assignment"
                            )
                        ),
                    )
            assigned_quarter_canister_id = _assign_quarter(
                caller, realm, quarters, preferred_quarter
            )
            u = User[caller]
            if u and assigned_quarter_canister_id:
                u.home_quarter = assigned_quarter_canister_id

        # Immediate capital population push (issue #156): after a brand-new
        # member lands on a quarter, tell the capital our live User.count() so
        # least-populated assignment and the admin switcher update without
        # waiting on the recurring gossip task. Best-effort — join already
        # succeeded if the push fails.
        if (
            was_new_user
            and realm
            and bool(getattr(realm, "is_quarter", False))
        ):
            capital_id = (getattr(realm, "federation_realm_id", "") or "").strip()
            if capital_id:
                try:
                    from api.cross_quarter import report_population_to_capital

                    pop = int(User.count())
                    push = yield from report_population_to_capital(capital_id, pop)
                    if not (isinstance(push, dict) and push.get("success")):
                        logger.error(
                            f"Population push to capital {capital_id} failed: {push}"
                        )
                except Exception as e:
                    logger.error(
                        f"Population push to capital {capital_id} raised: {e}"
                    )
                # Home-quarter directory upsert (issue #263): tell the capital
                # this principal now lives here so per-user federation actions
                # (verdict enforcement, messaging) can be routed. Best-effort.
                try:
                    from core.federation import send_federation_message

                    upsert = yield from send_federation_message(
                        capital_id, "gos.directory.upsert", {"principal": caller}
                    )
                    if not (isinstance(upsert, dict) and upsert.get("success")):
                        logger.error(
                            f"Directory upsert to capital {capital_id} failed: {upsert}"
                        )
                except Exception as e:
                    logger.error(
                        f"Directory upsert to capital {capital_id} raised: {e}"
                    )

        try:
            from core.membership import user_department_names

            live_user = User[caller]
            if live_user is not None:
                user["departments"] = user_department_names(live_user)
        except Exception:
            user["departments"] = list(user.get("departments") or [])

        return RealmResponse(
            success=True,
            data=RealmResponseData(
                userGet=_user_get_record_from_fields(
                    {**user, "profiles": list(profiles)},
                    assigned_quarter=assigned_quarter_canister_id,
                )
            ),
        )
    except Exception as e:
        logger.error(f"Error registering user: {str(e)}\n{traceback.format_exc()}")
        return RealmResponse(success=False, data=RealmResponseData(error=str(e)))


@update
def register_founder(principal: text) -> RealmResponse:
    """Register the deploying user as the realm's founding admin.

    Called by the realm installer after provisioning, with the principal of
    the user who requested the deployment. The installer is not an IC
    controller once the stand baton has sole control; it is the principal
    recorded at init (issue #404). Idempotent — an existing user simply
    gains the admin profile.
    """
    from core.access import AccessDenied, may_register_founder
    from ggg import Realm

    caller = ic.caller().to_str()
    if not may_register_founder(caller, Realm.load("1")):
        raise AccessDenied(
            f"Access denied: {caller} is not a controller or the realm installer"
        )
    try:
        from core.admin_users import register_admin_user

        result = register_admin_user(principal, seat_as_root_head=True)
        if not result.get("success"):
            return RealmResponse(
                success=False,
                data=RealmResponseData(
                    error=result.get("error") or "register_founder failed"
                ),
            )
        user = result.get("user") or {}
        return RealmResponse(
            success=True,
            data=RealmResponseData(
                userGet=_user_get_record_from_fields(user, assigned_quarter="")
            ),
        )
    except Exception as e:
        logger.error(f"register_founder failed: {str(e)}\n{traceback.format_exc()}")
        return RealmResponse(success=False, data=RealmResponseData(error=str(e)))


@update
@require(Operations.ALL)
def register_co_admin(principal: text) -> text:
    """Register another principal as admin (admin profile + root org member).

    Caller must already be an admin (typical: dfx ``deployer`` founder after
    ``realms new``). Does not replace the root org head. Idempotent.
    """
    try:
        from core.admin_users import register_admin_user

        caller = ic.caller().to_str()
        gate_err = setup_gate_error(caller)
        if gate_err:
            return json.dumps({"success": False, "error": gate_err})

        result = register_admin_user(principal, seat_as_root_head=False)
        if not result.get("success"):
            return json.dumps(
                {"success": False, "error": result.get("error") or "register_co_admin failed"}
            )
        user = result.get("user") or {}
        return json.dumps(
            {
                "success": True,
                "principal": (principal or "").strip(),
                "profiles": user.get("profiles") or ["admin"],
            }
        )
    except Exception as e:
        logger.error(f"register_co_admin failed: {str(e)}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@update
@require_controller
def store_admin_invite_hash(args_json: text) -> RealmResponse:
    """Controller-only endpoint to store a pre-computed admin invite hash."""
    try:
        args = json.loads(args_json)
        code_hash = args.get("code_hash", "").strip()
        expires_in_hours = args.get("expires_in_hours", 24)
        if not code_hash:
            return RealmResponse(
                success=False,
                data=RealmResponseData(error="code_hash is required"),
            )

        from ggg.system.registration_code import create_registration_code
        reg_code = create_registration_code(
            code_hash=code_hash,
            profile="admin",
            max_uses=1,
            expires_in_hours=expires_in_hours,
            created_by=ic.caller().to_str(),
            user_id="installer",
        )
        result = {
            "success": True,
            "data": {
                "code_hash": code_hash[:8],
                "expires_at": reg_code.expires_at,
                "profile": "admin",
            },
        }
        result_str = json.dumps(result)
        return RealmResponse(
            success=True,
            data=RealmResponseData(message=result_str),
        )
    except Exception as e:
        logger.error(f"Error storing admin invite hash: {str(e)}\n{traceback.format_exc()}")
        return RealmResponse(success=False, data=RealmResponseData(error=str(e)))


# ── Principal delegation (Power of Attorney) ─────────────────────────────


@update
def grant_delegation_json(args: text) -> text:
    """Grant scoped act-on-behalf authority from grantor to delegate.

    JSON args: grantor, delegate, scope ({operations: [...]} or {all: true}),
    optional label, expires_in_hours (default 168), requires_acceptance (default true).
    Caller must be grantor or realm admin.
    """
    try:
        caller = ic.caller().to_str()
        gate_err = setup_gate_error(caller)
        if gate_err:
            return json.dumps({"success": False, "error": gate_err})

        params = json.loads(args) if args else {}
        from core.delegation import grant_delegation

        grantor = (params.get("grantor") or "").strip()
        delegate = (params.get("delegate") or "").strip()
        scope = params.get("scope") or {}
        caller = ic.caller().to_str()

        if caller != grantor and not _check_access(caller, Operations.REALM_ADMIN):
            return json.dumps({
                "success": False,
                "error": "Only the grantor or a realm admin may create this delegation",
            })

        result = grant_delegation(
            grantor,
            delegate,
            scope,
            label=(params.get("label") or "").strip(),
            expires_in_hours=int(params.get("expires_in_hours") or 168),
            requires_acceptance=bool(params.get("requires_acceptance", True)),
            granted_by=caller,
        )
        return json.dumps(result)
    except Exception as e:
        logger.error(f"grant_delegation_json error: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@update
def accept_delegation_json(args: text) -> text:
    """Accept a pending delegation. JSON args: delegation_id."""
    try:
        from core.delegation import accept_delegation

        caller = ic.caller().to_str()
        gate_err = setup_gate_error(caller)
        if gate_err:
            return json.dumps({"success": False, "error": gate_err})

        params = json.loads(args) if args else {}
        delegation_id = (params.get("delegation_id") or "").strip()
        if not delegation_id:
            return json.dumps({"success": False, "error": "delegation_id is required"})
        return json.dumps(accept_delegation(delegation_id))
    except Exception as e:
        logger.error(f"accept_delegation_json error: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@update
def revoke_delegation_json(args: text) -> text:
    """Revoke a delegation. JSON args: delegation_id."""
    try:
        from core.delegation import revoke_delegation

        caller = ic.caller().to_str()
        gate_err = setup_gate_error(caller)
        if gate_err:
            return json.dumps({"success": False, "error": gate_err})

        params = json.loads(args) if args else {}
        delegation_id = (params.get("delegation_id") or "").strip()
        if not delegation_id:
            return json.dumps({"success": False, "error": "delegation_id is required"})
        return json.dumps(revoke_delegation(delegation_id))
    except Exception as e:
        logger.error(f"revoke_delegation_json error: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@query
def list_delegations_json() -> text:
    """List delegations where the caller is grantor or delegate."""
    try:
        from core.delegation import list_delegations_for_caller

        return json.dumps(list_delegations_for_caller())
    except Exception as e:
        logger.error(f"list_delegations_json error: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@update
@require(Operations.SELF_CHANGE_QUARTER)
def change_quarter(new_quarter_canister_id: text) -> RealmResponse:
    """Change the caller's assigned quarter."""
    try:
        from ggg import Quarter, Realm

        caller = ic.caller().to_str()
        gate_err = setup_gate_error(caller)
        if gate_err:
            from core.api_errors import SETUP_NOT_OPEN

            return _join_fail(SETUP_NOT_OPEN, gate_err)

        # Validate the target quarter exists and is active
        realm = Realm.load("1")
        if not realm:
            return RealmResponse(
                success=False, data=RealmResponseData(error="Realm not found")
            )

        # The capital (self) counts as quarter 0
        own_canister_id = ic.id().to_str()
        is_capital_target = new_quarter_canister_id == own_canister_id

        if not is_capital_target:
            quarters = list(Quarter.instances())
            target = None
            for q in quarters:
                if q.canister_id == new_quarter_canister_id and q.status == "active":
                    target = q
                    break

            if not target:
                return RealmResponse(
                    success=False,
                    data=RealmResponseData(
                        error=f"Quarter '{new_quarter_canister_id}' not found or not active"
                    ),
                )

        # Federation eligibility check (sandboxed; issue #265).
        from core.codex_hooks import call_assign_quarter

        try:
            call_assign_quarter(
                caller,
                [target] if (not is_capital_target and target) else [],
                new_quarter_canister_id,
            )
        except PermissionError as e:
            return RealmResponse(
                success=False, data=RealmResponseData(error=str(e))
            )

        # Persist the new assignment on the User entity
        from ggg import User

        u = User[caller]
        if u:
            u.home_quarter = new_quarter_canister_id

        return RealmResponse(
            success=True,
            data=RealmResponseData(message=new_quarter_canister_id),
        )
    except Exception as e:
        logger.error(f"Error changing quarter: {str(e)}\n{traceback.format_exc()}")
        return RealmResponse(success=False, data=RealmResponseData(error=str(e)))


@query
def get_my_principal() -> text:
    return ic.caller().to_str()


@query
def get_canister_id() -> text:
    """Return this canister's principal ID"""
    return ic.id().to_str()


def _realm_response_to_json_dict(resp) -> dict:
    """Convert a RealmResponse to {"success", "message"?/"error"?}.

    Records/Variants are TypedDicts at build time but runtime instances may be
    dict-like or attribute-based depending on the canister runtime — read both ways.
    """

    def _field(obj, key):
        try:
            return obj[key]
        except Exception:
            return getattr(obj, key, None)

    out = {"success": bool(_field(resp, "success"))}
    data = _field(resp, "data")
    if data is not None:
        message = _field(data, "message")
        error = _field(data, "error")
        if message is not None:
            out["message"] = message
        elif error is not None:
            out["error"] = error
    return out


def _set_canister_config_impl(
    frontend_canister_id=None,
    token_canister_id=None,
    nft_canister_id=None,
    file_registry_canister_id=None,
    marketplace_canister_id=None,
    installed_version=None,
    network=None,
    test_flags_json=None,
    can_test_mode=None,
    accounting_currency=None,
    accounting_currency_decimals=None,
    treasury_token_indexer_id=None,
    treasury_token_type=None,
) -> RealmResponse:
    """
    Set canister IDs and metadata for this realm (admin only).
    Called post-deployment to enable canister discovery via status().

    Args:
        frontend_canister_id: The realm_frontend canister ID
        token_canister_id: Optional token_backend canister ID
        nft_canister_id: Optional nft_backend canister ID
        file_registry_canister_id: Optional file_registry canister ID (shared infra)
        marketplace_canister_id: Optional marketplace_backend canister ID (shared infra)
        installed_version: Optional deployed version string (e.g. "0.3.5")
        network: Optional IC replica name (e.g. "ic", "local")
        test_flags_json: Optional JSON with test mode flags, e.g.
            {"test_mode":true,"ii_bypass":true,"user_self_registration":true,...}
            Rejected unless the manifest gos_environment is a non-production
            environment, or can_test_mode is set.
        can_test_mode: When True, allows test flags on production networks.
    """
    try:
        from ggg import Realm

        realm = Realm.load("1")
        if not realm:
            return RealmResponse(
                success=False, data=RealmResponseData(error="Realm not found")
            )

        if frontend_canister_id:
            realm.frontend_canister_id = frontend_canister_id
        if token_canister_id:
            realm.token_canister_id = token_canister_id
        if nft_canister_id:
            realm.nft_canister_id = nft_canister_id
        if file_registry_canister_id:
            realm.file_registry_canister_id = file_registry_canister_id
        if marketplace_canister_id:
            realm.marketplace_canister_id = marketplace_canister_id
        if installed_version:
            realm.installed_version = installed_version
        if network:
            realm.network = network

        if can_test_mode is not None:
            realm.can_test_mode = bool(can_test_mode)

        # Apply test flags (network-gated: rejected on production unless can_test_mode)
        if test_flags_json:
            from core.runtime_flags import (
                NON_PRODUCTION_NETWORKS,
                is_test_only_flag,
                normalize_flag_key,
                test_flags_allowed,
            )

            from core.demo_notice import host_environment

            effective_environment = host_environment(realm)
            raw_flags = json.loads(test_flags_json)
            # can_test_mode is the switch that permits test flags on a production
            # network, so it is only settable through the dedicated parameter of
            # the controller-gated set_canister_config — never from this payload,
            # which set_test_flags_json exposes without authentication.
            raw_flags.pop("can_test_mode", None)
            # Accept both the canonical product-flag names and their legacy
            # test_mode-era spellings.
            flags = {normalize_flag_key(k): v for k, v in raw_flags.items()}
            # Only flags that actually weaken the realm are network-gated; product
            # configuration that happens to live in this payload is not.
            enabled_test_flags = sorted(
                k for k, v in flags.items() if v and is_test_only_flag(k)
            )
            allowed = test_flags_allowed(
                effective_environment, bool(getattr(realm, "can_test_mode", False))
            )
            if enabled_test_flags and not allowed:
                return RealmResponse(
                    success=False,
                    data=RealmResponseData(
                        error=(
                            "Test mode flags "
                            + ", ".join(enabled_test_flags)
                            + " are not permitted on environment "
                            f"'{effective_environment or '(unset)'}' unless can_test_mode "
                            "is set by a controller. Environments allowing test flags: "
                            + ", ".join(NON_PRODUCTION_NETWORKS)
                        )
                    ),
                )
            _FLAG_MAP = {
                "test_mode": "test_mode",
                "ii_bypass": "test_mode_ii_bypass",
                "user_self_registration": "test_mode_user_self_registration",
                "demo_data": "test_mode_demo_data",
                "skip_terms": "test_mode_skip_terms",
                "skip_passport_zkproof": "test_mode_skip_passport_zkproof",
                "disable_monetary_tokens": "test_mode_disable_monetary_tokens",
                "demo_notice_enabled": "test_mode_demo_notice",
            }
            for key, attr in _FLAG_MAP.items():
                if key in flags:
                    setattr(realm, attr, bool(flags[key]))
            if flags.get("ii_bypass"):
                # The II-bypass principals sign in without Internet Identity, so
                # give them a real admin profile rather than a blanket bypass.
                from core.test_identity_seed import seed_ii_bypass_admin

                seed_ii_bypass_admin(test_flags_permitted=allowed)
            notice_raw = flags.get("demo_notice_body", flags.get("notice_body"))
            if notice_raw is not None:
                from core.demo_notice import dump_notice_bodies, parse_notice_bodies

                realm.demo_notice_body = dump_notice_bodies(parse_notice_bodies(notice_raw))

        if accounting_currency:
            symbol = str(accounting_currency).strip()
            if symbol:
                realm.accounting_currency = symbol[:16]
        if accounting_currency_decimals is not None:
            try:
                decimals = int(accounting_currency_decimals)
                if 0 <= decimals <= 18:
                    realm.accounting_currency_decimals = decimals
            except (TypeError, ValueError):
                pass

        if token_canister_id:
            from api.tokens import register_treasury_token

            sym = str(getattr(realm, "accounting_currency", "") or "").strip()
            if sym:
                indexer = (
                    str(treasury_token_indexer_id or "").strip() or str(token_canister_id)
                )
                decimals = int(getattr(realm, "accounting_currency_decimals", 8) or 8)
                token_type = str(treasury_token_type or "realm").strip() or "realm"
                register_treasury_token(
                    symbol=sym,
                    ledger_canister_id=str(token_canister_id),
                    indexer_canister_id=indexer,
                    decimals=decimals,
                    token_type=token_type,
                )
            else:
                logger.warning(
                    "Treasury token was not registered because ledger symbol is unknown"
                )

        logger.info(
            f"Updated canister config: frontend={frontend_canister_id}, "
            f"token={token_canister_id}, nft={nft_canister_id}, "
            f"file_registry={file_registry_canister_id}, marketplace={marketplace_canister_id}, "
            f"version={installed_version}, network={network}, "
            f"test_flags={test_flags_json}"
        )

        return RealmResponse(
            success=True, data=RealmResponseData(message="Canister config updated")
        )
    except Exception as e:
        logger.error(
            f"Error setting canister config: {str(e)}\n{traceback.format_exc()}"
        )
        return RealmResponse(success=False, data=RealmResponseData(error=str(e)))


@update
@require(Operations.REALM_ADMIN)
def set_canister_config(
    frontend_canister_id: Opt[text],
    token_canister_id: Opt[text],
    nft_canister_id: Opt[text],
    file_registry_canister_id: Opt[text],
    marketplace_canister_id: Opt[text],
    installed_version: Opt[text] = None,
    network: Opt[text] = None,
    test_flags_json: Opt[text] = None,
) -> RealmResponse:
    """Set canister IDs and metadata for this realm (admin only). Candid multi-arg
    form; see _set_canister_config_impl for the full argument docs."""
    return _set_canister_config_impl(
        frontend_canister_id,
        token_canister_id,
        nft_canister_id,
        file_registry_canister_id,
        marketplace_canister_id,
        installed_version,
        network,
        test_flags_json,
    )


@update
@require(Operations.ORGANIZATION_ADD)
def create_department(spec: text) -> text:
    """Create a department. Gate is ``organization.add`` (realms#349)."""
    try:
        data = json.loads(spec or "{}") if spec else {}
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    name = str(data.get("name") or "").strip()
    if not name:
        return json.dumps({"success": False, "error": "name is required"})
    from ggg import Department

    existing = Department[name]
    if existing:
        return json.dumps({"success": False, "error": "exists", "name": name})
    dept = Department(name=name, description=str(data.get("description") or ""))
    return json.dumps({"success": True, "name": dept.name})


@update
@require(Operations.ORGANIZATION_DELETE)
def delete_department(spec: text) -> text:
    """Destroy a department and detach children. Gate is ``organization.delete``."""
    try:
        data = json.loads(spec or "{}") if spec else {}
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    from core.department_admin import destroy_department

    return json.dumps(destroy_department(str(data.get("name") or "")))


@update
@require(Operations.ORGANIZATION_ADD)
def apply_department_table(spec: text) -> text:
    """Apply a department-table JSON document (upsert + explicit destroy)."""
    try:
        data = json.loads(spec or "{}") if spec else {}
    except Exception:
        return json.dumps({"success": False, "error": "invalid JSON"})
    from core.access import AccessDenied, _check_access
    from core.department_table import apply_department_table as _apply
    from core.department_table import document_has_destroy

    if document_has_destroy(data):
        caller = ic.caller().to_str()
        if not _check_access(caller, Operations.ORGANIZATION_DELETE):
            raise AccessDenied(
                f"Access denied: user {caller} lacks permission '{Operations.ORGANIZATION_DELETE}'",
                permission=Operations.ORGANIZATION_DELETE,
            )
    return json.dumps(_apply(data))


@update
@require(Operations.REALM_ADMIN)
def set_canister_config_json(args: text) -> Async[text]:
    """JSON text-in / text-out variant of set_canister_config.

    Lets a single declarative call configure a realm post-deploy — e.g. a Casals
    arrangement step ``{target, method: "set_canister_config_json", args: {...}}``.
    The multi-arg Candid form (set_canister_config) cannot be expressed as one
    text argument; this wrapper can.

    Args (JSON, all optional): {frontend_canister_id, token_canister_id,
    nft_canister_id, file_registry_canister_id, marketplace_canister_id,
    installed_version, network, treasury_token_indexer_id, treasury_token_type,
    can_test_mode (bool), and either test_flags_json (a JSON string) or
    test_flags (a JSON object, e.g. {"test_mode":true,"demo_data":true})}.
    can_test_mode may also appear inside test_flags (non-flag); top-level wins.
    Treasury symbol/decimals are resolved from token_canister_id via ICRC-1.

    Returns: {"success": bool, "message"?: str, "error"?: str}.
    """
    try:
        params = json.loads(args) if args else {}
        can_test_mode = params.get("can_test_mode")
        flags = params.get("test_flags_json")

        # can_test_mode is the switch that permits test flags on a production
        # network, so it has to be asked for deliberately. It used to be lifted
        # out of the test_flags blob, which meant a blob copied between
        # descriptors or arrangements could unlock production without anyone
        # writing it at the top level. Reject it there instead of honouring it.
        def _reject_nested(flag_dict):
            if "can_test_mode" in flag_dict:
                raise ValueError(
                    "can_test_mode does not belong in test_flags; pass it as a "
                    "top-level argument so unlocking a production network is "
                    "explicit rather than inherited from a copied blob"
                )

        if flags is None and isinstance(params.get("test_flags"), dict):
            test_flags_dict = dict(params["test_flags"])
            _reject_nested(test_flags_dict)
            flags = json.dumps(test_flags_dict)
        elif flags is not None:
            parsed_flags = json.loads(flags)
            if isinstance(parsed_flags, dict):
                _reject_nested(parsed_flags)
                flags = json.dumps(parsed_flags)

        accounting_currency = None
        accounting_currency_decimals = None
        treasury_token_indexer_id = None
        token_canister_id = params.get("token_canister_id")
        if token_canister_id and str(token_canister_id).strip():
            from api.tokens import resolve_ledger_token_info

            resolved = yield from resolve_ledger_token_info(str(token_canister_id).strip())
            if not resolved.get("success"):
                return json.dumps(
                    {
                        "success": False,
                        "error": resolved.get("error", "Could not resolve ledger"),
                        "error_code": "ledger_unresolvable",
                    }
                )
            accounting_currency = resolved["symbol"]
            accounting_currency_decimals = resolved["decimals"]
            treasury_token_indexer_id = resolved.get("indexer_canister_id")

        from ggg import Realm
        from core.setup import get_gos_environment, set_gos_environment

        previous_env = ""
        pre_realm = Realm.load("1")
        if pre_realm is not None:
            previous_env = get_gos_environment(pre_realm)
            if params.get("gos_environment"):
                set_gos_environment(pre_realm, params["gos_environment"])

        resp = _set_canister_config_impl(
            frontend_canister_id=params.get("frontend_canister_id"),
            token_canister_id=params.get("token_canister_id"),
            nft_canister_id=params.get("nft_canister_id"),
            file_registry_canister_id=params.get("file_registry_canister_id"),
            marketplace_canister_id=params.get("marketplace_canister_id"),
            installed_version=params.get("installed_version"),
            network=params.get("network"),
            test_flags_json=flags,
            can_test_mode=can_test_mode,
            accounting_currency=accounting_currency,
            accounting_currency_decimals=accounting_currency_decimals,
            treasury_token_indexer_id=treasury_token_indexer_id,
            treasury_token_type=params.get("treasury_token_type"),
        )
        out = _realm_response_to_json_dict(resp)
        if out.get("success"):
            from core.setup import set_creator_principal, set_realm_registry_canister_id

            realm = Realm.load("1")
            if realm:
                if params.get("creator_principal"):
                    set_creator_principal(realm, params["creator_principal"])
                if params.get("realm_registry_canister_id"):
                    set_realm_registry_canister_id(
                        realm, params["realm_registry_canister_id"]
                    )
                if not previous_env:
                    from core.demo_notice import seed_host_defaults_on_first_environment
                    from core.runtime_flags import normalize_flag_key

                    incoming_flags = {}
                    if flags:
                        try:
                            parsed_incoming = json.loads(flags)
                            if isinstance(parsed_incoming, dict):
                                incoming_flags = {
                                    normalize_flag_key(k): v
                                    for k, v in parsed_incoming.items()
                                }
                        except (TypeError, ValueError):
                            incoming_flags = {}
                    seed_host_defaults_on_first_environment(realm, incoming_flags)
        return json.dumps(out)
    except Exception as e:
        logger.error(f"set_canister_config_json error: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@update
def set_test_flags_json(args: text) -> text:
    """Edit runtime test-mode flags without admin rights — only while test_mode is on.

    Backs the footer "test flags" editor in the frontend: any user of a realm
    that is already in test mode may view and flip the flags (including turning
    test_mode off, which hides the editor and locks further edits back to
    admins). Enabling flags on mainnet is rejected by the shared network gate in
    _set_canister_config_impl.

    Args (JSON): {"test_flags": {...}} or a bare flags object with keys
    test_mode, ii_bypass, demo_data, skip_terms, skip_passport_zkproof.

    Only test-only flags are accepted here. Product configuration that merely
    happens to share the old ``test_mode_*`` prefix — disable_monetary_tokens,
    demo_notice, demo_notice_body, user_self_registration — is real settings
    that outlive test mode, so it stays behind set_canister_config's admin
    check rather than this unauthenticated editor.

    Returns: {"success": bool, "message"?: str, "error"?: str}.
    """
    try:
        from core.runtime_flags import is_test_mode, is_test_only_flag, normalize_flag_key

        if not is_test_mode():
            return json.dumps(
                {
                    "success": False,
                    "error": "Test flags can only be edited while test_mode is enabled",
                }
            )
        params = json.loads(args) if args else {}
        flags = params.get("test_flags")
        if not isinstance(flags, dict):
            flags = params if isinstance(params, dict) else {}
        # Drop product/unknown keys rather than 400. The footer editor still
        # sends the full form (demo notice, monetary tokens, …); those stay
        # behind set_canister_config. Rejecting them made Save fail after the
        # taxonomy split even when the caller only meant to flip ii_bypass.
        flags = {
            k: v
            for k, v in flags.items()
            if is_test_only_flag(normalize_flag_key(k))
        }
        if not flags:
            return json.dumps({"success": False, "error": "No test_flags provided"})
        resp = _set_canister_config_impl(test_flags_json=json.dumps(flags))
        return json.dumps(_realm_response_to_json_dict(resp))
    except Exception as e:
        logger.error(f"set_test_flags_json error: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@update
@require(Operations.QUARTER_REGISTER)
def register_quarter(quarter_name: text, quarter_canister_id: text) -> RealmResponse:
    """
    Register a new quarter under this realm.
    Creates a Quarter entity linked to the realm.

    Args:
        quarter_name: Human-readable name for the quarter
        quarter_canister_id: The canister principal ID of the quarter backend
    """
    try:
        from ggg import Quarter, QuarterStatus, Realm

        realm = Realm.load("1")
        if not realm:
            return RealmResponse(
                success=False, data=RealmResponseData(error="Realm not found")
            )

        # Check for duplicate canister ID
        quarters = Quarter.instances()
        for q in quarters:
            if q.canister_id == quarter_canister_id:
                return RealmResponse(
                    success=False,
                    data=RealmResponseData(
                        error=f"Quarter with canister ID {quarter_canister_id} already registered"
                    ),
                )

        # Assign a stable, monotonic catalog index (capital is 0, quarters >=1).
        # Users can recover their home quarter from this small integer without
        # any central per-user location index.
        next_index = 1
        for q in quarters:
            try:
                next_index = max(next_index, int(q.index or 0) + 1)
            except Exception:
                continue

        quarter = Quarter(
            name=quarter_name,
            canister_id=quarter_canister_id,
            index=next_index,
            status=QuarterStatus.SETUP,
        )
        quarter.federation = realm

        logger.info(
            f"Registered quarter '{quarter_name}' (canister: {quarter_canister_id}, index: {next_index})"
        )

        # Keep the capital's view of quarter populations fresh now that a
        # sub-quarter exists (issue #156). Best-effort: registration must not
        # fail if task seeding hiccups.
        try:
            ensure_population_sync_task()
        except Exception as e:
            logger.error(f"ensure_population_sync_task (register_quarter) failed: {e}")

        return RealmResponse(
            success=True,
            data=RealmResponseData(
                message=f"Quarter '{quarter_name}' registered with ID {quarter._id} (index {next_index})"
            ),
        )
    except Exception as e:
        logger.error(f"Error registering quarter: {str(e)}\n{traceback.format_exc()}")
        return RealmResponse(success=False, data=RealmResponseData(error=str(e)))


@update
@require(Operations.QUARTER_CONFIGURE)
def set_quarter_catalog_status(quarter_canister_id: text, status: text) -> text:
    """Set a registered quarter's catalog status (setup/active/suspended/...)."""
    try:
        from ggg import Quarter, QuarterStatus

        cid = (quarter_canister_id or "").strip()
        next_status = (status or "").strip()
        allowed = {
            QuarterStatus.SETUP,
            QuarterStatus.ACTIVE,
            QuarterStatus.SUSPENDED,
            QuarterStatus.SPLITTING,
            QuarterStatus.MERGING,
        }
        if not cid:
            return json.dumps({"success": False, "error": "missing canister id"})
        if next_status not in allowed:
            return json.dumps({
                "success": False,
                "error": f"invalid status {next_status!r}",
            })
        target = None
        for q in Quarter.instances():
            if q.canister_id == cid:
                target = q
                break
        if target is None:
            return json.dumps({
                "success": False,
                "error": f"Quarter '{cid}' not found",
            })
        previous = (target.status or "").strip() or QuarterStatus.SETUP
        target.status = next_status
        return json.dumps({
            "success": True,
            "canister_id": cid,
            "previous": previous,
            "status": next_status,
        })
    except Exception as e:
        logger.error(f"set_quarter_catalog_status failed: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@update
@require(Operations.QUARTER_DEREGISTER)
def deregister_quarter(quarter_canister_id: text) -> RealmResponse:
    """
    Remove a quarter from this realm by its canister ID.

    Args:
        quarter_canister_id: The canister principal ID of the quarter to remove
    """
    try:
        from ggg import Quarter

        for q in Quarter.instances():
            if q.canister_id == quarter_canister_id:
                q.delete()
                logger.info(
                    f"Deregistered quarter with canister ID {quarter_canister_id}"
                )
                return RealmResponse(
                    success=True,
                    data=RealmResponseData(message=f"Quarter '{q.name}' deregistered"),
                )

        return RealmResponse(
            success=False,
            data=RealmResponseData(
                error=f"Quarter with canister ID {quarter_canister_id} not found"
            ),
        )
    except Exception as e:
        logger.error(f"Error deregistering quarter: {str(e)}\n{traceback.format_exc()}")
        return RealmResponse(success=False, data=RealmResponseData(error=str(e)))


@update
@require(Operations.QUARTER_CONFIGURE)
def set_quarter_config(parent_realm_canister_id: text) -> RealmResponse:
    """
    Configure this realm as a quarter of a parent realm.
    Sets is_quarter=True and stores the parent realm's canister ID.

    Args:
        parent_realm_canister_id: The canister principal ID of the parent realm
    """
    try:
        from ggg import Realm

        realm = Realm.load("1")
        if not realm:
            return RealmResponse(
                success=False, data=RealmResponseData(error="Realm not found")
            )

        realm.is_quarter = True
        realm.federation_realm_id = parent_realm_canister_id

        logger.info(f"Configured realm as quarter of parent {parent_realm_canister_id}")

        return RealmResponse(
            success=True,
            data=RealmResponseData(
                message=f"Realm configured as quarter of {parent_realm_canister_id}"
            ),
        )
    except Exception as e:
        logger.error(
            f"Error setting quarter config: {str(e)}\n{traceback.format_exc()}"
        )
        return RealmResponse(success=False, data=RealmResponseData(error=str(e)))


@update
@require(Operations.QUARTER_CONFIGURE)
def bootstrap_as_quarter(args: text) -> text:
    """Seed a quarter-local self-bootstrap to bring a freshly minted quarter to
    parity (config + federation codex + extensions).

    Called by the capital immediately after Casals mints this canister. Because
    Casals co-adds the capital (the stand commander) as a controller of canisters
    in its stand, the capital passes the controller bypass on this gated call.

    Rather than install everything in this single message (impossible past a
    handful of extensions, given the IC instruction/time limit), this endpoint:

      1. Does the cheap config synchronously: mark as a quarter of the parent and
         trust the parent for future inter-canister calls.
      2. Records an install *plan* (codex + extensions) on the local ``Realm``.
      3. Seeds a recurring ``TaskManager`` task that installs **one item per
         tick** with retry/backoff (see ``core.quarter_bootstrap``).

    Returns immediately; progress is observable via ``get_bootstrap_status``.

    Args (JSON)::

        {
          "parent_realm_canister_id": "ihbn6-...",   # required
          "registry_canister_id": "iebdk-...",        # required for codex/extensions
          "codices": [{"codex_id": "...", "version": null, "run_init": true}, ...],
          "codex": {"codex_id": "...", "version": null} | null,  # back-compat single
          "extensions": [{"ext_id": "...", "version": null}, ...],
          "frontend_canister_id": "",                  # optional (backend-only quarters)
          "config": {                                  # capital's runtime config + branding
            "name": "Agora", "manifesto": "...", "welcome_message": "...",
            "open_registration": false, "network": "staging",
            "file_registry_canister_id": "...", "test_flags": {...}, ...
          }
        }

    The capital auto-derives ``codices``/``extensions`` from its own live
    installed set (see ``derive_capital_install_set``) so the quarter mirrors
    the capital; ``codex`` (single) is still accepted for older callers.
    """
    try:
        params = json.loads(args or "{}")
    except Exception as e:
        return json.dumps({"success": False, "error": f"bad args: {e}"})

    parent = (params.get("parent_realm_canister_id") or "").strip()

    try:
        from ggg import Realm
        from core.quarter_bootstrap import (
            apply_quarter_config,
            build_bootstrap_plan,
            save_state,
            seed_bootstrap_task,
        )

        realm = Realm.load("1")
        if not realm:
            return json.dumps({"success": False, "error": "Realm not found on quarter"})

        # 1. Synchronous config: mark as a quarter + trust the parent.
        realm.is_quarter = True
        realm.federation_realm_id = parent
        if parent:
            trusted = [p.strip() for p in str(realm.trusted_principals or "").split(",") if p.strip()]
            if parent not in trusted:
                trusted.append(parent)
                realm.trusted_principals = ",".join(trusted)

        # 1b. Mirror the capital's runtime config + branding so the quarter is
        # immediately branded and registration-ready (issue #156). The codex/
        # extension *code* arrives via the install plan below; this brings the
        # *identity* + runtime flags (name, manifesto, registration, canister
        # ids) that otherwise only come from out-of-band arrangement steps.
        applied_config = []
        cfg = params.get("config")
        if isinstance(cfg, dict) and cfg:
            try:
                applied_config = apply_quarter_config(realm, cfg)
            except Exception as e:
                logger.error(f"apply_quarter_config failed: {e}\n{traceback.format_exc()}")

        # 2. Record the install plan for the local driver.
        plan = build_bootstrap_plan(params)
        save_state(realm, plan)

        # 3. Seed the recurring TaskManager task that installs one item per tick.
        seeded = False
        if plan.get("items"):
            try:
                seed_bootstrap_task()
                seeded = True
            except Exception as e:
                logger.error(f"Failed to seed quarter bootstrap task: {e}\n{traceback.format_exc()}")

        planned = len(plan.get("items", []))
        status = "bootstrapping" if seeded else ("complete" if not planned else "blocked")
        logger.info(
            f"bootstrap_as_quarter seeded plan ({planned} items, parent={parent}, "
            f"status={status}, config_applied={len(applied_config)})"
        )
        return json.dumps({
            "success": True,
            "status": status,
            "parent": parent,
            "planned": planned,
            "seeded": seeded,
            "config_applied": applied_config,
        })
    except Exception as e:
        logger.error(f"bootstrap_as_quarter failed: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@update
@require(Operations.QUARTER_CONFIGURE)
def request_codex_sync(args: text) -> text:
    """Open a codex sync ballot on this quarter (issue #295).

    Called by the capital via inter-canister transport. Always creates a
    proposal — never applies inline, even though the capital is a controller
    and trusted principal (Gap 1). Members approve before ``apply_sync_plan``
    runs on the tick engine.
    """
    try:
        from core.quarter_sync import request_sync

        caller = ic.caller().to_str()
        try:
            params = json.loads(args or "{}")
        except Exception as e:
            return json.dumps({"success": False, "error": f"bad args: {e}"})
        return json.dumps(request_sync(caller, params))
    except Exception as e:
        logger.error(f"request_codex_sync failed: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@query
def get_bootstrap_status() -> text:
    """Report this quarter's self-bootstrap progress (issue #156).

    Returns the persisted install plan + cursor so the capital (or an operator)
    can watch a freshly minted quarter reach parity without polling the registry.
    """
    try:
        from ggg import Realm
        from core.quarter_bootstrap import load_state

        realm = Realm.load("1")
        if not realm:
            return json.dumps({"success": False, "error": "Realm not found"})
        state = load_state(realm)
        if not state:
            return json.dumps({"success": True, "status": "none", "plan": None})
        items = state.get("items") or []
        return json.dumps({
            "success": True,
            "status": state.get("status", "unknown"),
            "cursor": int(state.get("cursor") or 0),
            "total": len(items),
            "done": state.get("done", []),
            "failed": state.get("failed", []),
            "current": items[int(state.get("cursor") or 0)].get("id")
            if int(state.get("cursor") or 0) < len(items) else None,
        })
    except Exception as e:
        logger.error(f"get_bootstrap_status failed: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@update
@require(Operations.QUARTER_SECEDE)
def declare_independence() -> RealmResponse:
    """Secede from the federation, becoming an independent realm.

    Sets is_quarter=False, is_capital=False, clears federation_realm_id.
    All local users, data, governance, and extensions remain intact.
    """
    try:
        from ggg import Realm

        realm = Realm.load("1")
        if not realm:
            return RealmResponse(
                success=False, data=RealmResponseData(error="Realm not found")
            )

        if not realm.is_quarter:
            return RealmResponse(
                success=False,
                data=RealmResponseData(error="This realm is not a quarter of any federation"),
            )

        old_federation = realm.federation_realm_id or "unknown"
        realm.is_quarter = False
        realm.is_capital = False
        realm.federation_realm_id = ""

        logger.info(f"Declared independence from federation {old_federation}")

        return RealmResponse(
            success=True,
            data=RealmResponseData(
                message=f"Independence declared. Former federation: {old_federation}"
            ),
        )
    except Exception as e:
        logger.error(f"Error declaring independence: {str(e)}\n{traceback.format_exc()}")
        return RealmResponse(success=False, data=RealmResponseData(error=str(e)))


@update
@require(Operations.QUARTER_JOIN_FEDERATION)
def join_federation(capital_canister_id: text, as_capital: bool = False) -> RealmResponse:
    """Join an existing federation as a quarter.

    Sets is_quarter=True, stores the capital's canister ID, and optionally
    designates this quarter as the capital.

    Args:
        capital_canister_id: The canister principal ID of the federation's capital
        as_capital: If True, designate this quarter as the capital
    """
    try:
        from ggg import Realm

        realm = Realm.load("1")
        if not realm:
            return RealmResponse(
                success=False, data=RealmResponseData(error="Realm not found")
            )

        if realm.is_quarter:
            return RealmResponse(
                success=False,
                data=RealmResponseData(
                    error=f"Already a quarter of federation {realm.federation_realm_id}"
                ),
            )

        realm.is_quarter = True
        realm.is_capital = as_capital
        realm.federation_realm_id = capital_canister_id

        role = "capital" if as_capital else "quarter"
        logger.info(f"Joined federation {capital_canister_id} as {role}")

        return RealmResponse(
            success=True,
            data=RealmResponseData(
                message=f"Joined federation {capital_canister_id} as {role}"
            ),
        )
    except Exception as e:
        logger.error(f"Error joining federation: {str(e)}\n{traceback.format_exc()}")
        return RealmResponse(success=False, data=RealmResponseData(error=str(e)))


# ── Cross-quarter / cross-realm addressing (realm:// refs) ─────────────────
# See issue #156. A canister id is a canister id, so the same machinery serves
# both cross-quarter (same realm) and cross-realm references; only the locator
# fallback (gossip vs registry) differs by scope.


def _load_local_entity(entity_type: str, entity_id: str):
    """Return the live entity for (type, id) on THIS canister, or None."""
    try:
        results = list_objects([(entity_type, entity_id)])
        return results[0] if results else None
    except Exception:
        return None


def _find_migration_stub(subject: str):
    """Return the EntityMigration forwarding stub for ``subject`` here, or None."""
    try:
        from ggg import EntityMigration

        for m in EntityMigration.instances():
            if m.subject == subject:
                return m
        return None
    except Exception:
        return None


def _local_stub_next(ref) -> str:
    """stub_lookup for walk_chain: only this canister's stubs are visible.

    A quarter only knows the forwarding stubs it stored itself, so chain
    resolution beyond the first remote hop is the caller's/frontend's job
    (hop to ``next_ref``'s canister and call ``resolve_ref`` again).
    """
    self_id = ic.id().to_str()
    if not ref.is_local(self_id):
        return ""
    stub = _find_migration_stub(ref.entity_id)
    return stub.next_ref if (stub and stub.next_ref) else ""


@query
def resolve_ref(ref_uri: text) -> text:
    """Resolve a ``realm://<canister>/<Type>/<id>`` reference.

    Returns JSON describing where the entity currently lives:

    * ``status=local``  — entity is on this canister; ``object`` is its JSON.
    * ``status=remote`` — entity is elsewhere; ``final_ref`` + ``canister_id``
      tell the frontend which backend to switch its actor to.
    * ``status=moved``  — a local forwarding stub points onward (``final_ref``).
    * ``status=not_found`` / ``invalid`` / ``loop`` / ``too_deep`` on failure.
    """
    try:
        self_id = ic.id().to_str()
        result = walk_chain(
            ref_uri,
            self_id,
            local_lookup=lambda r: _load_local_entity(r.entity_type, r.entity_id),
            stub_lookup=_local_stub_next,
        )
        out = {
            "status": result["status"],
            "final_ref": result.get("final_ref"),
            "hops": result.get("hops", []),
        }
        final = result.get("final_ref")
        if final:
            fref = RealmRef.try_parse(final)
            if fref:
                out["canister_id"] = fref.canister_id
                out["entity_type"] = fref.entity_type
                out["entity_id"] = fref.entity_id
        if result["status"] == ResolutionStatus.LOCAL and final:
            fref = RealmRef.parse(final)
            obj = _load_local_entity(fref.entity_type, fref.entity_id)
            if obj is not None:
                out["object"] = obj.serialize()
        return json.dumps(out)
    except Exception as e:
        logger.error(f"Error resolving ref {ref_uri!r}: {e}\n{traceback.format_exc()}")
        return json.dumps({"status": "error", "error": str(e)})


@query
def get_objects_by_ref(refs: Vec[text]) -> text:
    """Batch-resolve realm refs. Local hits return objects; remote return routes.

    Returns JSON ``{"results": [ {ref, status, object?, canister_id?, ...}, ... ]}``.
    """
    try:
        self_id = ic.id().to_str()
        results = []
        for ref_uri in refs:
            info = classify_ref(ref_uri, self_id)
            entry = {"ref": ref_uri, "status": info["status"]}
            if info["status"] == ResolutionStatus.LOCAL:
                obj = _load_local_entity(info["entity_type"], info["entity_id"])
                if obj is not None:
                    entry["object"] = obj.serialize()
                else:
                    # Local ref but no entity — maybe it moved on.
                    stub = _find_migration_stub(info["entity_id"])
                    if stub and stub.next_ref:
                        entry["status"] = ResolutionStatus.MOVED
                        entry["final_ref"] = stub.next_ref
                    else:
                        entry["status"] = ResolutionStatus.NOT_FOUND
            elif info["status"] == ResolutionStatus.REMOTE:
                entry["canister_id"] = info["canister_id"]
                entry["entity_type"] = info["entity_type"]
                entry["entity_id"] = info["entity_id"]
            results.append(entry)
        return json.dumps({"results": results})
    except Exception as e:
        logger.error(f"Error in get_objects_by_ref: {e}\n{traceback.format_exc()}")
        return json.dumps({"results": [], "error": str(e)})


@query
def get_migration(subject: text) -> text:
    """Return this canister's forwarding stub for ``subject`` (for chain walks).

    JSON: ``{"found": bool, "next_ref": str, "prev_ref": str, "moved_at": str}``.
    """
    try:
        stub = _find_migration_stub(subject)
        if not stub:
            return json.dumps({"found": False})
        return json.dumps({
            "found": True,
            "subject": stub.subject,
            "entity_type": stub.entity_type,
            "prev_ref": stub.prev_ref or "",
            "next_ref": stub.next_ref or "",
            "moved_at": stub.moved_at or "",
        })
    except Exception as e:
        logger.error(f"Error in get_migration: {e}")
        return json.dumps({"found": False, "error": str(e)})


@update
@require(Operations.SELF_CHANGE_QUARTER)
def record_migration(args_json: text) -> text:
    """Record a forwarding stub: this subject left here for ``next_ref``.

    Args (JSON): ``{subject, next_ref, entity_type?, prev_ref?, signature?}``.
    ``next_ref`` must be a valid absolute ``realm://`` URI. Idempotent per
    subject — re-recording updates the existing stub.
    """
    try:
        from ggg import EntityMigration

        args = json.loads(args_json)
        subject = (args.get("subject") or "").strip()
        next_ref = (args.get("next_ref") or "").strip()
        if not subject:
            return json.dumps({"success": False, "error": "subject is required"})
        if not RealmRef.is_ref(next_ref):
            return json.dumps({
                "success": False,
                "error": f"next_ref must be a valid realm:// URI (got {next_ref!r})",
            })

        entity_type = (args.get("entity_type") or "User").strip()
        prev_ref = (args.get("prev_ref") or "").strip()
        signature = (args.get("signature") or "").strip()
        moved_at = str(ic.time())

        stub = _find_migration_stub(subject)
        if stub:
            stub.next_ref = next_ref
            stub.entity_type = entity_type
            if prev_ref:
                stub.prev_ref = prev_ref
            if signature:
                stub.signature = signature
            stub.moved_at = moved_at
        else:
            EntityMigration(
                subject=subject,
                entity_type=entity_type,
                prev_ref=prev_ref,
                next_ref=next_ref,
                moved_at=moved_at,
                signature=signature,
            )
        logger.info(f"Recorded migration for {subject} -> {next_ref}")
        return json.dumps({"success": True, "subject": subject, "next_ref": next_ref})
    except Exception as e:
        logger.error(f"Error in record_migration: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@query
def get_quarter_directory() -> text:
    """Coarse quarter directory for gossip — containers only, never contents.

    JSON: ``{"quarters": [{name, canister_id, population, status}, ...],
    "self": {canister_id, codex_id, codex_version, last_sync_ballot_id,
    last_sync_ballot_status}}``.

    The ``self`` block describes this realm's installed codex and most recent
    codex-sync ballot (issue #295). Old callers ignore it; old peers omit it.
    Includes this canister plus every Quarter entity it knows about.
    """
    try:
        from core.join_targets import (
            catalog_status_for_self,
            is_dashboard_installed,
        )
        from core.quarter_drift import (
            build_directory_self,
            find_latest_codex_sync_ballot,
        )
        from core.quarter_sync import derive_quarter_current_codex
        from core.runtime_extensions import list_installed, resolve_extension_id
        from ggg import Proposal, Quarter, Realm, User

        self_id = ic.id().to_str()
        realm = Realm.load("1")
        quarters = []
        seen = set()
        is_quarter = bool(getattr(realm, "is_quarter", False)) if realm else False
        if is_quarter:
            dashboard_installed = is_dashboard_installed(
                list_installed(),
                resolve_extension_id("member_dashboard"),
            )
            self_status = catalog_status_for_self(True, dashboard_installed)
        else:
            self_status = "active"
        if realm is not None:
            try:
                self_pop = int(User.count())
            except Exception:
                self_pop = 0
            quarters.append({
                "name": getattr(realm, "name", "") or "",
                "canister_id": self_id,
                "population": self_pop,
                "status": self_status,
                "index": 0,
                "is_self": True,
            })
            seen.add(self_id)
        for q in Quarter.instances():
            cid = q.canister_id or ""
            if cid in seen:
                continue
            seen.add(cid)
            quarters.append({
                "name": q.name or "",
                "canister_id": cid,
                "population": int(q.population or 0),
                "status": q.status or "setup",
                "index": int(q.index or 0),
            })

        # Peers depend on this directory; a failure to describe our own codex
        # must not cost them the quarter list.
        payload = {"quarters": quarters}
        try:
            from core.quarter_drift import recent_proposals

            codex = derive_quarter_current_codex()
            ballot = find_latest_codex_sync_ballot(recent_proposals(Proposal))
            federal = None
            try:
                from core.federal_vote_runtime import latest_leg_for_directory

                federal = latest_leg_for_directory()
            except Exception:
                pass
            payload["self"] = build_directory_self(self_id, codex, ballot, federal=federal)
        except Exception as e:
            logger.warning(f"get_quarter_directory: self block unavailable: {e}")

        return json.dumps(payload)
    except Exception as e:
        logger.error(f"Error in get_quarter_directory: {e}\n{traceback.format_exc()}")
        return json.dumps({"quarters": [], "error": str(e)})


@query
def list_position_holders() -> text:
    try:
        from core.acting_appointments import dump_position_holders
        return json.dumps(dump_position_holders(ic.id().to_str()))
    except Exception as e:
        return json.dumps({"success": False, "error": str(e), "positions": []})


@query
def get_join_targets() -> text:
    """Public join policy for the registration page (issue #156).

    Tells the /join page where a *new* member may register. Returns:
    ``{mode, default_quarter, capital_id, quarters: [{canister_id, name,
    population, status, index, is_capital, joinable}, ...]}``.

    ``joinable`` is a hint for *auto* routing (which quarter to pre-select).
    Registration errors (coordinator-only capital, full quarter, closed
    registration, etc.) are returned by ``join_realm`` on the target canister.

    Policy:
    - ``mode`` is the capital's ``quarter_join_mode`` ("auto" | "choice").
      Product join UX always system-assigns; invite links may still encode a
      quarter via ``?quarter=``.
    - Once >=1 active sub-quarter exists the capital becomes coordinator-only:
      it is listed with ``joinable=false`` and ``default_quarter`` points at the
      least-populated active sub-quarter (tie-break: highest index). With no
      sub-quarters the capital itself is the joinable default.

    Public (no auth): the caller is typically anonymous at this point.
    """
    try:
        from core.join_targets import is_joinable_status, pick_default_join_quarter
        from ggg import Quarter, Realm

        self_id = ic.id().to_str()
        realm = Realm.load("1")
        mode = (getattr(realm, "quarter_join_mode", "auto") or "auto") if realm else "auto"
        capital_name = (getattr(realm, "name", "") or "") if realm else ""

        sub_quarters = []
        if realm is not None:
            for q in Quarter.instances():
                cid = q.canister_id or ""
                if not cid or cid == self_id:
                    continue
                status = q.status or "setup"
                sub_quarters.append({
                    "canister_id": cid,
                    "name": q.name or "",
                    "population": int(q.population or 0),
                    "status": status,
                    "index": int(getattr(q, "index", 0) or 0),
                    "is_capital": False,
                    "joinable": is_joinable_status(status),
                })

        active_subs = [q for q in sub_quarters if q["joinable"]]
        capital_joinable = len(active_subs) == 0

        quarters = [{
            "canister_id": self_id,
            "name": capital_name or "Capital",
            "population": 0,
            "status": "active",
            "index": 0,
            "is_capital": True,
            "joinable": capital_joinable,
        }] + sub_quarters

        default_quarter = pick_default_join_quarter(active_subs, self_id)

        return json.dumps({
            "mode": mode,
            "default_quarter": default_quarter,
            "capital_id": self_id,
            "quarters": quarters,
        })
    except Exception as e:
        logger.error(f"Error in get_join_targets: {e}\n{traceback.format_exc()}")
        return json.dumps({
            "mode": "auto",
            "default_quarter": "",
            "capital_id": "",
            "quarters": [],
            "error": str(e),
        })


@update
@require(Operations.QUARTER_REGISTER)
def sync_quarters(peer_canister_id: text) -> Async[text]:
    """Gossip: pull a peer quarter's coarse directory and merge it into ours.

    Adds Quarter entities for peers we did not know about and updates known
    populations. Carries only container-level data (see issue #156).

    Delegates to ``core.quarter_bootstrap.sync_one_peer`` — the same un-gated
    merge the recurring population-sync task uses, so both paths stay identical.
    """
    from core.quarter_bootstrap import sync_one_peer

    res = yield from sync_one_peer(peer_canister_id)
    return json.dumps(res)


@query
@require(Operations.QUARTER_CONFIGURE)
def get_quarter_codex_drift() -> text:
    """Capital-side federation view of per-quarter codex drift (issue #295).

    JSON ``{success, data: {capital_codex_id, capital_codex_version,
    quarters: [{canister_id, name, reported_codex_id, reported_codex_version,
    capital_codex_id, capital_codex_version, drifted, last_sync_ballot_id,
    last_sync_ballot_status, state}, ...]}}``.

    ``state`` is one of ``aligned``, ``drifted`` (no open ballot),
    ``ballot_open``, or ``ballot_not_adopted`` (``failed`` / ``no_quorum`` /
    ``rejected``). Drift fields are refreshed by the existing quarter-directory
    gossip (``sync_quarters`` / population sync); this endpoint reads what is
    already stored on ``Quarter`` entities.

    Gated by ``QUARTER_CONFIGURE`` — same operation family as
    ``request_quarter_codex_sync`` and quarter bootstrap configuration.
    """
    try:
        from core.quarter_bootstrap import derive_capital_install_set
        from core.quarter_drift import (
            build_federation_drift_report,
            derive_capital_target_codex,
        )
        from ggg import Quarter, Realm

        realm = Realm.load("1")
        if not realm:
            return json.dumps({"success": False, "error": "Realm not found"})

        default_registry = ""
        try:
            manifest = json.loads(getattr(realm, "manifest_data", "") or "{}")
            cas = (manifest.get("casals") if isinstance(manifest, dict) else None) or {}
            default_registry = (cas.get("registry_canister_id") or "").strip()
        except Exception:
            pass

        capital_codex = derive_capital_target_codex(
            derive_capital_install_set(default_registry)
        )
        report = build_federation_drift_report(Quarter.instances(), capital_codex)
        return json.dumps({"success": True, "data": report})
    except Exception as e:
        logger.error(f"Error in get_quarter_codex_drift: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@update
def report_quarter_population(population: nat) -> text:
    """Accept a quarter's live population push (issue #156).

    Called by a sub-quarter immediately after a new member joins so the
    capital's cached ``Quarter.population`` (and therefore ``get_join_targets``
    / least-populated assignment) updates without waiting on the recurring
    gossip task.

    Auth: ``ic.caller()`` must be a canister id already registered as a
    ``Quarter`` on this capital. Population is monotonic (higher wins).
    Public to known quarters only — unknown callers are rejected.
    """
    try:
        from core.cross_quarter import resolve_population_report
        from ggg import Quarter

        caller = ic.caller().to_str()
        known = []
        target = None
        for q in Quarter.instances():
            cid = q.canister_id or ""
            if not cid:
                continue
            known.append(cid)
            if cid == caller:
                target = q

        decision = resolve_population_report(
            known,
            caller,
            population,
            int(getattr(target, "population", 0) or 0) if target else 0,
        )
        if not decision.get("ok"):
            return json.dumps({"success": False, "error": decision.get("error", "rejected")})

        if decision.get("updated") and target is not None:
            target.population = int(decision["population"])
            logger.info(
                f"Population report from {caller}: "
                f"{decision['previous']} -> {decision['population']}"
            )
            # Same as after a gossip sync tick: re-evaluate auto-scale with
            # the fresh federation-wide populations (joins land on quarters,
            # so the capital never sees the threshold via its own join path).
            try:
                from core.autoscale import maybe_request_quarter_scale

                if maybe_request_quarter_scale():
                    logger.info(
                        f"Quarter auto-scale requested after population report "
                        f"from {caller}"
                    )
            except Exception as e:
                logger.error(f"Auto-scale after population report failed: {e}")

        return json.dumps({
            "success": True,
            "updated": bool(decision.get("updated")),
            "population": int(decision.get("population") or 0),
            "previous": int(decision.get("previous") or 0),
            "canister_id": caller,
        })
    except Exception as e:
        logger.error(f"Error in report_quarter_population: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@update
def report_quarter_ready() -> text:
    """Accept a quarter's join-ready signal after member_dashboard is installed.

    Auth: ``ic.caller()`` must be a canister id already registered as a
    ``Quarter`` on this capital. Promotes ``setup`` (or empty) to ``active``.
    Idempotent when already active.
    """
    try:
        from core.join_targets import should_activate_quarter
        from ggg import Quarter, QuarterStatus

        caller = ic.caller().to_str()
        target = None
        for q in Quarter.instances():
            cid = q.canister_id or ""
            if cid == caller:
                target = q
                break

        if target is None:
            return json.dumps({
                "success": False,
                "error": "caller is not a registered quarter",
                "canister_id": caller,
            })

        current = (target.status or "").strip() or QuarterStatus.SETUP
        if current == QuarterStatus.ACTIVE:
            return json.dumps({
                "success": True,
                "updated": False,
                "status": QuarterStatus.ACTIVE,
                "canister_id": caller,
            })

        if should_activate_quarter(current, True):
            target.status = QuarterStatus.ACTIVE
            logger.info(f"Quarter {caller} promoted setup -> active")
            return json.dumps({
                "success": True,
                "updated": True,
                "status": QuarterStatus.ACTIVE,
                "canister_id": caller,
            })

        return json.dumps({
            "success": False,
            "error": f"quarter status {current!r} cannot be promoted",
            "status": current,
            "canister_id": caller,
        })
    except Exception as e:
        logger.error(f"Error in report_quarter_ready: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@update
def register_demo_citizens(payload: text) -> Async[text]:
    """Register synthetic demo citizens on this canister (capital or quarter).

    Gated on the capital by ``test_mode`` + ``test_mode_demo_data``. Quarters
    accept calls only from their federation capital canister id.
    """
    try:
        from core.demo_registration import register_demo_citizens_impl

        result = yield from register_demo_citizens_impl(payload)
        return json.dumps(result)
    except Exception as e:
        logger.error(f"Error in register_demo_citizens: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@update
def federation_message(payload: text) -> text:
    """Generic federation transport endpoint (issue #263).

    Payload: ``{msg_id, topic, body}``. Reserved ``gos.*`` topics are handled
    by core (ping, home-quarter directory); everything else dispatches to the
    active codex's ``on_federation_message`` hook.

    Auth: ``ic.caller()`` must be a federation member (the capital accepts its
    registered quarters; a quarter accepts its capital). Duplicate ``msg_id``
    deliveries replay the stored response (idempotent retries).
    """
    try:
        from core.federation import handle_incoming

        return handle_incoming(payload, ic.caller().to_str())
    except Exception as e:
        logger.error(f"Error in federation_message: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@update
@require(Operations.FEDERAL_VOTE_PROPOSE)
def propose_federal_vote(args: text) -> Async[text]:
    """Originate a realm-wide federal vote (issue #300).

    Args (JSON): ``{action, org_name?, confirm?}``
    """
    try:
        params = json.loads(args or "{}")
    except Exception as e:
        return json.dumps({"success": False, "error": f"bad args: {e}"})

    action, err = None, ""
    try:
        import core.federal_tally as _tally

        action, err = _tally.validate_action(params.get("action"))
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})
    if action is None:
        return json.dumps({"success": False, "error": err})

    try:
        from core.governed_action import build_backend_replay_code, gate as governed_gate
        from core.federal_vote_runtime import dispatch_federal_propose

        caller = ic.caller().to_str()
        confirm = bool(params.get("confirm", False))
        org_name = (params.get("org_name") or "").strip() or None
        payload = {"action": action}
        if params.get("vote_id"):
            payload["vote_id"] = params.get("vote_id")

        verdict = governed_gate(
            caller=caller,
            summary=f"Federal vote: {action.get('function', 'action')}",
            replay_code=build_backend_replay_code(
                "core.federal_vote_runtime",
                "dispatch_federal_propose",
                json.dumps(payload),
            ),
            org_name=org_name,
            confirm=confirm,
            metadata_extra={"federal_scope": "originate"},
        )
        if verdict is not None:
            return json.dumps(verdict)

        result = yield from dispatch_federal_propose(payload)
        return json.dumps(result)
    except Exception as e:
        logger.error(f"propose_federal_vote failed: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@query
def get_federal_vote(vote_id: text) -> text:
    """Return one federal vote as JSON, or ``{success:false}`` when missing."""
    try:
        from core.federal_vote_runtime import get_vote_view

        view = get_vote_view(vote_id)
        if view is None:
            return json.dumps({"success": False, "error": "vote not found"})
        return json.dumps({"success": True, "vote": view})
    except Exception as e:
        logger.error(f"get_federal_vote failed: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@query
def list_federal_votes(args: text) -> text:
    """List federal votes; optional JSON filter ``{status}``."""
    try:
        from core.federal_vote_runtime import list_votes

        params = json.loads(args or "{}")
        status = (params.get("status") or "").strip() or None
        votes = list_votes(status=status)
        return json.dumps({"success": True, "votes": votes})
    except Exception as e:
        logger.error(f"list_federal_votes failed: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@update
def finalize_federal_vote(args: text) -> Async[text]:
    """Permissionless poke to advance federal vote drivers (issue #300)."""
    try:
        from core.federal_vote_runtime import (
            advance_federal_aggregate,
            advance_federal_legs,
        )

        legs = yield from advance_federal_legs()
        aggregate = yield from advance_federal_aggregate()
        return json.dumps({"success": True, "legs": legs, "aggregate": aggregate})
    except Exception as e:
        logger.error(f"finalize_federal_vote failed: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@update
@require(Operations.FEDERAL_VOTE_MANAGE)
def cancel_federal_vote(args: text) -> Async[text]:
    """Cancel an open federal vote before its deadline."""
    try:
        from core.federal_vote_runtime import cancel_federal_vote as _cancel

        params = json.loads(args or "{}")
        vote_id = (params.get("vote_id") or "").strip()
        if not vote_id:
            return json.dumps({"success": False, "error": "vote_id is required"})
        result = yield from _cancel(vote_id)
        return json.dumps(result)
    except Exception as e:
        logger.error(f"cancel_federal_vote failed: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@query
def get_scale_status() -> text:
    """Report the federation's auto-scaling state (issue #156).

    JSON: ``{auto_scale_enabled, scale_in_flight, scale_requested_at, network,
    n, threshold, populations, should_scale}``. Used by admins/tests/frontend
    to observe sharding decisions; carries no per-user data.
    """
    try:
        from ggg import Realm

        from core.autoscale import (
            _manifest_environment,
            default_threshold_n,
            quarter_capacity_override,
            quarter_populations,
            resolve_should_scale,
            scale_at,
            _codex_should_deploy_fn,
        )

        realm = Realm.load("1")
        if not realm:
            return json.dumps({"success": False, "error": "Realm not found"})

        environment = _manifest_environment(realm)
        pops = quarter_populations(realm)
        # Report the effective N: the manifest override the registration path
        # actually uses, not just the env default (observability must match
        # behavior or operators misread stuck scales).
        n = quarter_capacity_override(realm) or default_threshold_n(environment)
        codex_fn = _codex_should_deploy_fn(realm)
        return json.dumps({
            "success": True,
            "auto_scale_enabled": bool(getattr(realm, "auto_scale_enabled", True)),
            "scale_in_flight": bool(getattr(realm, "scale_in_flight", False)),
            "scale_requested_at": getattr(realm, "scale_requested_at", "") or "",
            "network": getattr(realm, "network", "") or "",
            "n": n,
            "threshold": scale_at(n),
            "populations": pops,
            "should_scale": resolve_should_scale(
                pops, environment, codex_fn=codex_fn, n_override=quarter_capacity_override(realm),
                realm=realm,
            ),
        })
    except Exception as e:
        logger.error(f"Error in get_scale_status: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@update
@require(Operations.QUARTER_CONFIGURE)
def set_quarter_provisioning_config(args: text) -> text:
    """Set/merge the ``casals`` provisioning block in this realm's ``manifest_data``.

    The whole auto-scale loop is gated on ``manifest_data.casals`` (consumed by
    ``parse_casals_spec``); this endpoint lets an admin wire it post-deploy
    without re-importing realm data. Provided keys are merged over any existing
    ``casals`` block (pass ``{"casals": {...}}`` or the flat fields directly), so
    a partial update (e.g. just ``casals_canister_id``) leaves the rest intact.

    Recognized keys: ``stand``, ``casals_canister_id``,
    ``registry_canister_id``, ``codex`` ({codex_id, version, run_init}),
    ``extensions`` ([{ext_id, version} | "ext_id", ...]), ``frontend_canister_id``.

    Returns ``{"success": bool, "casals": {...}, "error"?: str}``.
    """
    try:
        params = json.loads(args or "{}")
        if not isinstance(params, dict):
            return json.dumps({"success": False, "error": "args must be a JSON object"})
        incoming = params.get("casals") if isinstance(params.get("casals"), dict) else params

        from ggg import Realm

        realm = Realm.load("1")
        if not realm:
            return json.dumps({"success": False, "error": "Realm not found"})

        try:
            manifest = json.loads(getattr(realm, "manifest_data", "") or "{}")
            if not isinstance(manifest, dict):
                manifest = {}
        except Exception:
            manifest = {}

        casals = manifest.get("casals") if isinstance(manifest.get("casals"), dict) else {}
        allowed = (
            "stand", "casals_canister_id", "registry_canister_id",
            "codex", "extensions", "frontend_canister_id", "baton_canister_id",
        )
        for k in allowed:
            if k in incoming:
                casals[k] = incoming[k]
        manifest["casals"] = casals
        realm.manifest_data = json.dumps(manifest)
        logger.info(f"set_quarter_provisioning_config merged casals keys: {sorted(k for k in allowed if k in incoming)}")
        return json.dumps({"success": True, "casals": casals})
    except Exception as e:
        logger.error(f"set_quarter_provisioning_config error: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


def _orchestration_baton_id(realm) -> str:
    """This realm's Baton canister id from ``manifest_data.casals.baton_canister_id``."""
    try:
        manifest = json.loads(getattr(realm, "manifest_data", "") or "{}")
        cas = (manifest.get("casals") if isinstance(manifest, dict) else None) or {}
        return (cas.get("baton_canister_id") or "").strip()
    except Exception:
        return ""


def _parse_baton_text_reply(decoded) -> dict:
    """Parse a Baton text-method reply: plain JSON or candid-wrapped ("<json>")."""
    if isinstance(decoded, dict):
        return decoded
    if isinstance(decoded, (list, tuple)) and decoded:
        decoded = decoded[0]
    s = str(decoded or "").strip()
    if s.startswith("(") and ")" in s:
        inner = s[1:s.rfind(")")].strip().rstrip(",").strip()
        if inner.startswith('"') and inner.endswith('"'):
            try:
                inner_text = json.loads(inner)
                if isinstance(inner_text, str):
                    s = inner_text
            except json.JSONDecodeError:
                pass
    try:
        parsed = json.loads(s)
        return parsed if isinstance(parsed, dict) else {"result": parsed}
    except json.JSONDecodeError:
        return {"raw": s[:500]}


@update
@require(Operations.ORCHESTRATION_APPROVE)
def approve_orchestration_action(args: text) -> Async[text]:
    """Submit this realm's approval (or rejection) of a Baton orchestration
    action — the realm-backend half of the 2-of-2 (casals-backend +
    realm-backend) approval policy on this realm's Baton.

    Who may call this is realm governance: the codex grants the
    ``orchestration.approve`` operation to the right profiles (admins for
    dominion, organization representatives for agora, all members for
    syntropia); a voting extension resolving a proposal can also drive it.

    Args (JSON): {"action_id": "<baton action id>",
                  "decision"?: "approve" | "reject",
                  "baton_canister_id"?: "<override>"}

    The Baton id defaults to ``manifest_data.casals.baton_canister_id``
    (injected by the realm_installer at provisioning time).
    Returns the Baton's JSON reply (approval progress / quorum state).
    """
    try:
        params = json.loads(args or "{}")
        action_id = (params.get("action_id") or "").strip()
        if not action_id:
            return json.dumps({"success": False, "error": "action_id required"})
        decision = (params.get("decision") or "approve").strip().lower()
        if decision not in ("approve", "reject"):
            return json.dumps({"success": False, "error": "decision must be 'approve' or 'reject'"})

        from ggg import Realm
        realm = Realm.load("1")
        baton_id = (params.get("baton_canister_id") or "").strip() or (
            _orchestration_baton_id(realm) if realm else ""
        )
        if not baton_id:
            return json.dumps({
                "success": False,
                "error": "no baton configured (manifest_data.casals.baton_canister_id)",
            })

        method = "submit_approval" if decision == "approve" else "reject_action"
        escaped = action_id.replace("\\", "\\\\").replace('"', '\\"')
        call_res: CallResult = yield ic.call_raw(
            Principal.from_str(baton_id), method,
            ic.candid_encode(f'("{escaped}")'), 0,
        )
        if isinstance(call_res, dict):
            if call_res.get("Err") is not None:
                return json.dumps({"success": False, "error": str(call_res["Err"])})
            raw = call_res.get("Ok")
        elif hasattr(call_res, "Err") and call_res.Err is not None:
            return json.dumps({"success": False, "error": str(call_res.Err)})
        else:
            raw = getattr(call_res, "Ok", call_res)
        decoded = ic.candid_decode(raw) if isinstance(raw, (bytes, bytearray)) else raw
        reply = _parse_baton_text_reply(decoded)
        ok = bool(reply.get("ok")) if isinstance(reply, dict) else True
        logger.info(
            f"orchestration {decision} for action {action_id} on baton {baton_id}: "
            f"{str(reply)[:200]}"
        )
        return json.dumps({"success": ok, "decision": decision, "baton": baton_id, "reply": reply})
    except Exception as e:
        logger.error(f"approve_orchestration_action error: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@update
@require(Operations.QUARTER_REGISTER)
def process_quarter_scaling() -> Async[text]:
    """Act on a pending auto-scale request: provision a new quarter, bring it to
    parity, register it locally, then clear the in-flight guard.

    When ``manifest_data.casals.casals_canister_id`` is set, the capital
    (commander of its Casals stand) asks Casals to ``create_canister`` a
    backend-only quarter, then drives ``bootstrap_as_quarter`` on it (Casals
    co-adds the capital as a controller of canisters minted in its stand, so
    the gated bootstrap calls are authorized). Without ``casals_canister_id``,
    provisioning is blocked until an operator wires the direct path.

    Non-blocking by design — user registration only sets ``scale_in_flight``;
    this endpoint (called by a controller, timer, or task manager) performs the
    actual provisioning out of band so joins never wait on a deploy.
    Idempotent: a no-op when no scale is in flight.
    """
    from core.quarter_scaling import run_quarter_scaling

    res = yield from run_quarter_scaling()
    return res


@update
@require(Operations.QUARTER_CONFIGURE)
def request_quarter_codex_sync(args: text) -> Async[text]:
    """Ask a quarter to open a codex sync ballot (issue #295).

    Derives the capital's live codex target from ``derive_capital_install_set``,
    then calls the quarter's ``request_codex_sync``. Wrapped in ``gate()`` so
    whoever may trigger a sync on the capital side is whatever the capital
    already configured — 1/1 admin, M/N department, or a capital vote.

    Args (JSON)::

        {
          "quarter_canister_id": "ihbn6-...",   # required
          "confirm": false                      # second step when policy != 1/1
        }

    Returns the quarter's reply (proposal created on the quarter) or a
    ``requires_confirmation`` / capital-side proposal payload from ``gate()``.
    """
    try:
        params = json.loads(args or "{}")
    except Exception as e:
        return json.dumps({"success": False, "error": f"bad args: {e}"})

    quarter_id = (params.get("quarter_canister_id") or "").strip()
    if not quarter_id:
        return json.dumps({"success": False, "error": "quarter_canister_id is required"})

    try:
        from core.governed_action import build_backend_replay_code, gate as governed_gate
        from core.quarter_sync import trigger_quarter_codex_sync

        caller = ic.caller().to_str()
        confirm = bool(params.get("confirm", False))
        replay_payload = {"quarter_canister_id": quarter_id}
        summary = f"Request codex sync for quarter {quarter_id}"

        verdict = governed_gate(
            caller=caller,
            summary=summary,
            replay_code=build_backend_replay_code(
                "core.quarter_sync",
                "trigger_quarter_codex_sync",
                json.dumps(replay_payload),
            ),
            confirm=confirm,
            metadata_extra={
                "sync_type": "quarter_codex_sync",
                "quarter_canister_id": quarter_id,
            },
        )
        if verdict is not None:
            return json.dumps(verdict)

        result = yield from trigger_quarter_codex_sync(replay_payload)
        return json.dumps(result)
    except Exception as e:
        logger.error(f"request_quarter_codex_sync failed: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


def run_autoscale_tick() -> Async[text]:
    """Compat shim: the driver lives in ``core.quarter_scaling`` so the
    TaskManager sandbox (which cannot import ``main``) can reach it. Tasks
    seeded by older builds still call ``from main import run_autoscale_tick``.
    """
    from core.quarter_scaling import run_autoscale_tick as _tick

    res = yield from _tick()
    return res


def ensure_autoscale_task() -> bool:
    """Compat shim: the implementation lives in ``core.quarter_bootstrap`` so
    sandboxed contexts (join path, __shell__) that cannot import ``main`` can
    still seed the provisioning driver. Kept for older callers/post_upgrade."""
    from core.quarter_bootstrap import ensure_autoscale_task as _ensure

    return _ensure()


def ensure_population_sync_task() -> bool:
    """Retired no-op: capital-side population refresh is push-based (issue
    #156) — each quarter calls ``report_quarter_population`` on join, so the
    old recurring pull task (``quarter_population_sync``) is retired and there
    is nothing to seed. Kept so legacy call sites stay valid; the previous
    implementation imported ``POP_SYNC_*`` names that no longer exist and
    silently failed every call."""
    return True


@query
def get_zones(resolution: nat = 6) -> text:
    """
    Get H3 zone aggregation data for users in this realm.
    Returns zones with user counts at each H3 cell.

    Args:
        resolution: H3 resolution level (0-15). Default 6.

    Returns:
        JSON string with zone data
    """
    try:
        result = get_zone_aggregation(resolution)
        return json.dumps(result)
    except Exception as e:
        logger.error(f"Error getting zones: {e}")
        return json.dumps({"success": False, "error": str(e)})


@query
def get_my_user_status() -> RealmResponse:
    try:
        user = user_get(ic.caller().to_str())
        logger.info(f"User: {user}")
        if not user["success"]:
            return RealmResponse(
                success=False, data=RealmResponseData(error=user["error"])
            )

        user = user_get_record_fields(user)
        logger.info(f"Profiles: {user.get('profiles')}")
        return RealmResponse(
            success=True,
            data=RealmResponseData(userGet=_user_get_record_from_fields(user)),
        )
    except Exception as e:
        logger.error(f"Error getting user: {str(e)}\n{traceback.format_exc()}")
        return RealmResponse(success=False, data=RealmResponseData(error=str(e)))


def _seeded_extension_names() -> set:
    """Return names of extensions that have explicit DB access grants.

    An extension is considered "seeded" when it has at least one user,
    department, or profile link in the database. Only seeded extensions are
    subject to strict whitelist filtering; un-seeded extensions fall back to
    manifest-level profile matching. This prevents a *partial* seed (e.g.
    installing a single extension, which links only that one to its profiles)
    from hiding every other extension that has not yet been seeded.
    """
    from ggg import Extension

    seeded = set()
    try:
        from core.membership import extension_user_grant_count

        for ext in Extension.instances():
            try:
                if (
                    extension_user_grant_count(ext) > 0
                    or list(ext.departments)
                    or list(ext.profiles)
                ):
                    seeded.add(ext.name)
            except Exception:
                continue
    except Exception:
        pass
    return seeded


def _user_granted_extension_names(user) -> set:
    """Union of extension names directly granted to a user via user, department,
    or profile links."""
    granted = set()
    if not user:
        return granted
    for ext in user.extensions:
        granted.add(ext.name)
    for dept in user.departments:
        for ext in dept.extensions:
            granted.add(ext.name)
    for profile in user.profiles:
        for ext in profile.extensions:
            granted.add(ext.name)
    return granted


@query
def get_my_extensions() -> text:
    """Return the list of extensions accessible to the calling user.

    For each installed extension:
      - If it has been seeded with DB access grants, it is visible only when
        the user holds a matching user/department/profile grant.
      - Otherwise (no DB grants for that extension), visibility falls back to
        manifest-level profile matching.

    This per-extension fallback ensures a partial seed never hides extensions
    that simply have not been linked in the database yet.

    Returns JSON: {"success": true, "extensions": ["voting", "vault", ...]}
    """
    try:
        from ggg import User
        from core.runtime_extensions import get_all_extension_manifests

        caller = ic.caller().to_str()
        user = User[caller]
        if not user:
            return json.dumps({"success": False, "error": "User not found"})

        user_granted = _user_granted_extension_names(user)
        seeded = _seeded_extension_names()
        user_profiles = [p.name for p in user.profiles] if user.profiles else []

        visible = set()
        for ext_id, m in get_all_extension_manifests().items():
            if not isinstance(m, dict):
                continue
            if ext_id in seeded:
                if ext_id in user_granted:
                    visible.add(ext_id)
            else:
                ext_profiles = m.get("profiles") or []
                if not ext_profiles or any(p in user_profiles for p in ext_profiles):
                    visible.add(ext_id)

        return json.dumps({"success": True, "extensions": sorted(visible)})
    except Exception as e:
        logger.error(f"Error getting user extensions: {str(e)}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@update
@require(Operations.SELF_UPDATE_PUBLIC_PROFILE)
def update_my_public_profile(nickname: str, avatar: str) -> RealmResponse:
    try:
        caller = ic.caller().to_str()
        gate_err = setup_gate_error(caller)
        if gate_err:
            from core.api_errors import SETUP_NOT_OPEN

            return _join_fail(SETUP_NOT_OPEN, gate_err)
        result = user_update_public_profile(caller, nickname, avatar)
        if not result["success"]:
            return RealmResponse(
                success=False, data=RealmResponseData(error=result["error"])
            )

        return RealmResponse(
            success=True,
            data=RealmResponseData(
                userGet=_user_get_record_from_fields(
                    {
                        "principal": ic.caller().to_str(),
                        "profiles": [],
                        "departments": [],
                        "nickname": result["nickname"],
                        "avatar": result["avatar"],
                        "private_data": "",
                        "assigned_quarter": "",
                    }
                )
            ),
        )
    except Exception as e:
        logger.error(
            f"Error updating public profile: {str(e)}\n{traceback.format_exc()}"
        )
        return RealmResponse(success=False, data=RealmResponseData(error=str(e)))


@update
@require(Operations.SELF_UPDATE_PRIVATE_DATA)
def update_my_private_data(private_data: str) -> RealmResponse:
    try:
        caller = ic.caller().to_str()
        gate_err = setup_gate_error(caller)
        if gate_err:
            from core.api_errors import SETUP_NOT_OPEN

            return _join_fail(SETUP_NOT_OPEN, gate_err)
        result = user_update_private_data(caller, private_data)
        if not result["success"]:
            return RealmResponse(
                success=False, data=RealmResponseData(error=result["error"])
            )

        return RealmResponse(
            success=True,
            data=RealmResponseData(
                userGet=_user_get_record_from_fields(
                    {
                        "principal": ic.caller().to_str(),
                        "profiles": [],
                        "departments": [],
                        "nickname": "",
                        "avatar": "",
                        "private_data": result["private_data"],
                        "assigned_quarter": "",
                    }
                )
            ),
        )
    except Exception as e:
        logger.error(f"Error updating private data: {str(e)}\n{traceback.format_exc()}")
        return RealmResponse(success=False, data=RealmResponseData(error=str(e)))


@update
@require(Operations.SELF_UPDATE_PRIVATE_DATA)
def get_my_vetkey_public_key() -> RealmResponse:
    """Get the vetKD public key for the caller's encryption context.

    The returned hex-encoded BLS12-381 G2 public key is used by the frontend
    to verify encrypted keys and set up the IBE scheme.
    """
    try:
        result = yield get_vetkey_public_key(ic.caller().to_str())
        if not result["success"]:
            return RealmResponse(
                success=False, data=RealmResponseData(error=result["error"])
            )
        return RealmResponse(
            success=True,
            data=RealmResponseData(message=result["public_key_hex"]),
        )
    except Exception as e:
        logger.error(
            f"Error getting vetkey public key: {str(e)}\n{traceback.format_exc()}"
        )
        return RealmResponse(success=False, data=RealmResponseData(error=str(e)))


@update
@require(Operations.SELF_UPDATE_PRIVATE_DATA)
def derive_my_vetkey(transport_public_key_hex: text) -> RealmResponse:
    """Derive an encrypted vetKey for the caller.

    The caller must supply a 48-byte BLS12-381 G1 transport public key
    (hex-encoded, 96 chars).  The management canister encrypts the derived
    symmetric key under this transport key so it can only be decrypted by
    the caller's frontend.
    """
    try:
        result = yield derive_vetkey(ic.caller().to_str(), transport_public_key_hex)
        if not result["success"]:
            return RealmResponse(
                success=False, data=RealmResponseData(error=result["error"])
            )
        return RealmResponse(
            success=True,
            data=RealmResponseData(message=result["encrypted_key_hex"]),
        )
    except Exception as e:
        logger.error(f"Error deriving vetkey: {str(e)}\n{traceback.format_exc()}")
        return RealmResponse(success=False, data=RealmResponseData(error=str(e)))


@update
@require(Operations.SELF_UPDATE_PRIVATE_DATA)
def get_sharing_root_public_key() -> RealmResponse:
    """Get the shared *root* vetKD public key used for member data sharing.

    Unlike :func:`get_my_vetkey_public_key` (one key per principal), this is a
    single key shared by everyone. The frontend fetches it **once** and derives
    each recipient's IBE public key locally by using the recipient's principal
    as the IBE identity — eliminating the previous one-management-call-per-
    recipient cost. Only the holder of a recipient's sharing vetKey (derivable
    solely by that principal via :func:`derive_my_sharing_vetkey`) can decrypt.
    """
    try:
        result = yield get_root_public_key()
        if not result["success"]:
            return RealmResponse(
                success=False, data=RealmResponseData(error=result["error"])
            )
        return RealmResponse(
            success=True,
            data=RealmResponseData(message=result["public_key_hex"]),
        )
    except Exception as e:
        logger.error(
            f"Error getting sharing root public key: {str(e)}\n{traceback.format_exc()}"
        )
        return RealmResponse(success=False, data=RealmResponseData(error=str(e)))


@update
@require(Operations.SELF_UPDATE_PRIVATE_DATA)
def derive_my_sharing_vetkey(transport_public_key_hex: text) -> RealmResponse:
    """Derive the caller's sharing vetKey (root context, input = own principal).

    Because the vetKD ``input`` is bound to ``ic.caller()``, a caller can only
    ever obtain the key for their own identity, and therefore can only decrypt
    IBE ciphertexts addressed to their principal under the shared root key.
    """
    try:
        result = yield derive_vetkey_for_sharing(
            ic.caller().to_str(), transport_public_key_hex
        )
        if not result["success"]:
            return RealmResponse(
                success=False, data=RealmResponseData(error=result["error"])
            )
        return RealmResponse(
            success=True,
            data=RealmResponseData(message=result["encrypted_key_hex"]),
        )
    except Exception as e:
        logger.error(
            f"Error deriving sharing vetkey: {str(e)}\n{traceback.format_exc()}"
        )
        return RealmResponse(success=False, data=RealmResponseData(error=str(e)))


# ---------------------------------------------------------------------------
# Crypto envelope & group endpoints
# ---------------------------------------------------------------------------


def _caller_can_manage_scope(scope: str) -> bool:
    """Whether the caller may grant/revoke read access for *scope*.

    Authorization is pluggable per scope *kind* (``user:``, ``dept:``,
    ``realm:``, …) and defined in :mod:`core.crypto_scopes`. This keeps the
    crypto engine generic and reusable for any payload, not just member
    personal data.
    """
    from core.crypto_scopes import caller_can_manage_scope, production_context

    return caller_can_manage_scope(scope, ic.caller().to_str(), production_context())


@update
@require(Operations.SELF_UPDATE_PRIVATE_DATA)
def crypto_store_my_envelope(scope: text, wrapped_dek: text) -> CryptoResponse:
    """Store (or update) a wrapped DEK envelope for the caller."""
    try:
        result = crypto_store_envelope(ic.caller().to_str(), scope, wrapped_dek)
        if not result["success"]:
            return CryptoResponse(
                success=False, data=CryptoResponseData(error=result["error"])
            )
        return CryptoResponse(
            success=True,
            data=CryptoResponseData(
                envelope=EnvelopeRecord(
                    scope=result["scope"],
                    principal_id=result["principal"],
                    wrapped_dek=result["wrapped_dek"],
                )
            ),
        )
    except Exception as e:
        logger.error(f"Error storing envelope: {e}\n{traceback.format_exc()}")
        return CryptoResponse(success=False, data=CryptoResponseData(error=str(e)))


@query
@require(Operations.SELF_UPDATE_PRIVATE_DATA)
def crypto_get_my_envelope(scope: text) -> CryptoResponse:
    """Get the caller's envelope for a scope."""
    try:
        result = crypto_get_envelope(ic.caller().to_str(), scope)
        if not result["success"]:
            return CryptoResponse(
                success=False, data=CryptoResponseData(error=result["error"])
            )
        return CryptoResponse(
            success=True,
            data=CryptoResponseData(
                envelope=EnvelopeRecord(
                    scope=result["scope"],
                    principal_id=result["principal"],
                    wrapped_dek=result["wrapped_dek"],
                )
            ),
        )
    except Exception as e:
        logger.error(f"Error getting envelope: {e}\n{traceback.format_exc()}")
        return CryptoResponse(success=False, data=CryptoResponseData(error=str(e)))


@query
@require(Operations.SELF_UPDATE_PRIVATE_DATA)
def crypto_get_my_scopes() -> CryptoResponse:
    """List all scopes the caller has access to."""
    try:
        result = crypto_list_scopes(ic.caller().to_str())
        return CryptoResponse(
            success=True,
            data=CryptoResponseData(scopeList=ScopeListRecord(scopes=result["scopes"])),
        )
    except Exception as e:
        logger.error(f"Error listing scopes: {e}\n{traceback.format_exc()}")
        return CryptoResponse(success=False, data=CryptoResponseData(error=str(e)))


# NOTE: These endpoints are generic over the scope *kind* — `user:`, `dept:`,
# `realm:`, or any kind registered in core.crypto_scopes. Authorization to
# manage a scope is enforced by `_caller_can_manage_scope`, so the same crypto
# sharing machinery is reusable for personal data, department documents,
# realm-level records, etc.


@update
@require(Operations.SELF_UPDATE_PRIVATE_DATA)
def crypto_grant_to_scope_batch(
    scope: text, wrapped_deks_json: text
) -> CryptoResponse:
    """Grant many principals access to a scope the caller may manage, in one call.

    ``wrapped_deks_json`` is a JSON object mapping ``principal -> wrapped_dek``
    (the DEK IBE-wrapped client-side for each recipient). Replacing N update
    calls with one both saves round-trips and lets the backend upsert all
    envelopes in a single linear pass.
    """
    try:
        if not _caller_can_manage_scope(scope):
            return CryptoResponse(
                success=False,
                data=CryptoResponseData(
                    error="You are not allowed to manage sharing for this scope"
                ),
            )
        try:
            wrapped_deks = json.loads(wrapped_deks_json)
        except Exception:
            return CryptoResponse(
                success=False,
                data=CryptoResponseData(error="Invalid wrapped_deks JSON"),
            )
        if not isinstance(wrapped_deks, dict):
            return CryptoResponse(
                success=False,
                data=CryptoResponseData(error="wrapped_deks must be a JSON object"),
            )
        result = crypto_grant_many(scope, wrapped_deks)
        if not result["success"]:
            return CryptoResponse(
                success=False, data=CryptoResponseData(error=result["error"])
            )
        return CryptoResponse(
            success=True,
            data=CryptoResponseData(
                message=f"Granted {result['envelopes_granted']} envelope(s) for {scope}"
            ),
        )
    except Exception as e:
        logger.error(f"Error batch granting to scope: {e}\n{traceback.format_exc()}")
        return CryptoResponse(success=False, data=CryptoResponseData(error=str(e)))


@update
@require(Operations.SELF_UPDATE_PRIVATE_DATA)
def crypto_revoke_from_scope_batch(
    scope: text, principals_json: text
) -> CryptoResponse:
    """Revoke many principals from a scope the caller may manage, in one call.

    ``principals_json`` is a JSON array of principal strings to revoke.
    """
    try:
        if not _caller_can_manage_scope(scope):
            return CryptoResponse(
                success=False,
                data=CryptoResponseData(
                    error="You are not allowed to manage sharing for this scope"
                ),
            )
        try:
            principals = json.loads(principals_json)
        except Exception:
            return CryptoResponse(
                success=False,
                data=CryptoResponseData(error="Invalid principals JSON"),
            )
        if not isinstance(principals, list):
            return CryptoResponse(
                success=False,
                data=CryptoResponseData(error="principals must be a JSON array"),
            )
        result = crypto_revoke_many(scope, principals)
        if not result["success"]:
            return CryptoResponse(
                success=False, data=CryptoResponseData(error=result["error"])
            )
        return CryptoResponse(
            success=True,
            data=CryptoResponseData(
                message=f"Revoked {result['envelopes_revoked']} envelope(s) from {scope}"
            ),
        )
    except Exception as e:
        logger.error(f"Error batch revoking from scope: {e}\n{traceback.format_exc()}")
        return CryptoResponse(success=False, data=CryptoResponseData(error=str(e)))


@query
@require(Operations.SELF_UPDATE_PRIVATE_DATA)
def crypto_list_scope_envelopes(scope: text) -> CryptoResponse:
    """List all principals with access to a scope the caller may manage."""
    try:
        if not _caller_can_manage_scope(scope):
            return CryptoResponse(
                success=False,
                data=CryptoResponseData(
                    error="You are not allowed to manage sharing for this scope"
                ),
            )
        result = crypto_list_envelopes(scope)
        envelopes = Vec["EnvelopeRecord"]()
        for e in result["envelopes"]:
            envelopes.append(
                EnvelopeRecord(
                    scope=e["scope"],
                    principal_id=e["principal"],
                    wrapped_dek=e["wrapped_dek"],
                )
            )
        return CryptoResponse(
            success=True,
            data=CryptoResponseData(
                envelopeList=EnvelopeListRecord(envelopes=envelopes)
            ),
        )
    except Exception as e:
        logger.error(f"Error listing scope envelopes: {e}\n{traceback.format_exc()}")
        return CryptoResponse(success=False, data=CryptoResponseData(error=str(e)))


@query
@require(Operations.SELF_UPDATE_PRIVATE_DATA)
def list_share_audiences() -> RealmResponse:
    """List the audiences a member can share their private data with.

    Returns, as a JSON string in ``message``, the set of audiences plus the
    member principals each one resolves to. The member's browser needs these
    principals to wrap their data-encryption key for each recipient.

    Audiences:
      - ``Administrators`` — the member_data_readers crypto group.
      - one per ``Department`` — its current members.
    """
    try:
        audiences = []

        try:
            from api.crypto import group_members as _group_members

            res = _group_members("member_data_readers")
            principals = [m["principal"] for m in res.get("members", []) if m.get("principal")]
            audiences.append(
                {
                    "id": "group:member_data_readers",
                    "label": "Administrators",
                    "type": "admins",
                    "principals": principals,
                }
            )
        except Exception as e:
            logger.warning(f"list_share_audiences: admins group unavailable: {e}")

        try:
            from core.membership import iter_users
            from ggg import Department

            # Single user scan building dept → principals (the reverse
            # dept.members index no longer exists — issue #242).
            by_dept: dict = {}
            for u in iter_users():
                pid = getattr(u, "id", None)
                if not pid:
                    continue
                try:
                    for d in u.departments:
                        by_dept.setdefault(d.name, []).append(pid)
                except Exception:
                    continue

            for dept in Department.instances():
                audiences.append(
                    {
                        "id": f"dept:{dept.name}",
                        "label": dept.name,
                        "type": "department",
                        "principals": by_dept.get(dept.name, []),
                    }
                )
        except Exception as e:
            logger.warning(f"list_share_audiences: departments unavailable: {e}")

        return RealmResponse(
            success=True,
            data=RealmResponseData(message=json.dumps({"audiences": audiences})),
        )
    except Exception as e:
        logger.error(f"Error listing share audiences: {e}\n{traceback.format_exc()}")
        return RealmResponse(success=False, data=RealmResponseData(error=str(e)))


@query
def directory_list() -> RealmResponse:
    """Realm directory for entity pickers (e.g. choosing a litigation defendant).

    Returns, as JSON in ``message``, a flat list of ``entries`` — realm users
    (principal + best display name) and departments (name + head principal) —
    so any extension can offer name/principal autocomplete via one fast query
    instead of an expensive per-extension update call. Read-only; exposes only
    identities already visible across the realm, never private content.

    The client is expected to fetch this once and filter in the browser. We log
    the instruction count so we can decide whether the simple full-scan needs a
    projection/index later (see ROADMAP perf notes).
    """
    _t0 = ic.performance_counter(0)
    try:
        from core.justice.directory import list_local_entries

        # This canister only. Do not loop this query over federation members
        # (issue #325: all-quarters autocomplete saturates gossip).
        entries = list_local_entries()
        user_count = sum(1 for e in entries if e.get("kind") == "user")
        dept_count = sum(1 for e in entries if e.get("kind") == "department")

        instructions = ic.performance_counter(0) - _t0
        logger.info(
            f"directory_list: {user_count} users + {dept_count} depts "
            f"({len(entries)} entries) in {instructions} instructions"
        )
        return RealmResponse(
            success=True,
            data=RealmResponseData(message=json.dumps({"entries": entries})),
        )
    except Exception as e:
        logger.error(f"Error in directory_list: {e}\n{traceback.format_exc()}")
        return RealmResponse(success=False, data=RealmResponseData(error=str(e)))


@query
@require(Operations.REALM_ADMIN)
def crypto_get_envelopes(scope: text) -> CryptoResponse:
    """List all envelopes for a scope (admin only)."""
    try:
        result = crypto_list_envelopes(scope)
        envelopes = Vec["EnvelopeRecord"]()
        for e in result["envelopes"]:
            envelopes.append(
                EnvelopeRecord(
                    scope=e["scope"],
                    principal_id=e["principal"],
                    wrapped_dek=e["wrapped_dek"],
                )
            )
        return CryptoResponse(
            success=True,
            data=CryptoResponseData(
                envelopeList=EnvelopeListRecord(envelopes=envelopes)
            ),
        )
    except Exception as e:
        logger.error(f"Error listing envelopes: {e}\n{traceback.format_exc()}")
        return CryptoResponse(success=False, data=CryptoResponseData(error=str(e)))


@update
@require(Operations.REALM_ADMIN)
def crypto_share(
    scope: text, target_principal: text, wrapped_dek: text
) -> CryptoResponse:
    """Share access to a scope with another principal (admin only)."""
    try:
        result = crypto_share_principal(scope, target_principal, wrapped_dek)
        if not result["success"]:
            return CryptoResponse(
                success=False, data=CryptoResponseData(error=result["error"])
            )
        return CryptoResponse(
            success=True,
            data=CryptoResponseData(
                message=f"Shared scope {scope} with {target_principal}"
            ),
        )
    except Exception as e:
        logger.error(f"Error sharing: {e}\n{traceback.format_exc()}")
        return CryptoResponse(success=False, data=CryptoResponseData(error=str(e)))


@update
@require(Operations.REALM_ADMIN)
def crypto_revoke(scope: text, target_principal: text) -> CryptoResponse:
    """Revoke a principal's access to a scope (admin only)."""
    try:
        result = crypto_revoke_principal(scope, target_principal)
        if not result["success"]:
            return CryptoResponse(
                success=False, data=CryptoResponseData(error=result["error"])
            )
        return CryptoResponse(
            success=True,
            data=CryptoResponseData(
                message=f"Revoked {target_principal} from scope {scope}"
            ),
        )
    except Exception as e:
        logger.error(f"Error revoking: {e}\n{traceback.format_exc()}")
        return CryptoResponse(success=False, data=CryptoResponseData(error=str(e)))


@update
@require(Operations.REALM_ADMIN)
def crypto_create_group(name: text, description: text) -> CryptoResponse:
    """Create a new crypto group (admin only)."""
    try:
        result = crypto_group_create(name, description)
        if not result["success"]:
            return CryptoResponse(
                success=False, data=CryptoResponseData(error=result["error"])
            )
        return CryptoResponse(
            success=True,
            data=CryptoResponseData(
                group=GroupRecord(
                    name=result["name"], description=result["description"]
                )
            ),
        )
    except Exception as e:
        logger.error(f"Error creating group: {e}\n{traceback.format_exc()}")
        return CryptoResponse(success=False, data=CryptoResponseData(error=str(e)))


@update
@require(Operations.REALM_ADMIN)
def crypto_delete_group(name: text) -> CryptoResponse:
    """Delete a crypto group (admin only)."""
    try:
        result = crypto_group_delete(name)
        if not result["success"]:
            return CryptoResponse(
                success=False, data=CryptoResponseData(error=result["error"])
            )
        return CryptoResponse(
            success=True,
            data=CryptoResponseData(message=f"Deleted group {name}"),
        )
    except Exception as e:
        logger.error(f"Error deleting group: {e}\n{traceback.format_exc()}")
        return CryptoResponse(success=False, data=CryptoResponseData(error=str(e)))


@update
@require(Operations.REALM_ADMIN)
def crypto_add_group_member(
    group_name: text, principal: text, role: text
) -> CryptoResponse:
    """Add a principal to a crypto group (admin only)."""
    try:
        result = crypto_group_add(group_name, principal, role or "member")
        if not result["success"]:
            return CryptoResponse(
                success=False, data=CryptoResponseData(error=result["error"])
            )
        return CryptoResponse(
            success=True,
            data=CryptoResponseData(message=f"Added {principal} to group {group_name}"),
        )
    except Exception as e:
        logger.error(f"Error adding group member: {e}\n{traceback.format_exc()}")
        return CryptoResponse(success=False, data=CryptoResponseData(error=str(e)))


@update
@require(Operations.REALM_ADMIN)
def crypto_remove_group_member(group_name: text, principal: text) -> CryptoResponse:
    """Remove a principal from a crypto group (admin only)."""
    try:
        result = crypto_group_remove(group_name, principal)
        if not result["success"]:
            return CryptoResponse(
                success=False, data=CryptoResponseData(error=result["error"])
            )
        return CryptoResponse(
            success=True,
            data=CryptoResponseData(
                message=f"Removed {principal} from group {group_name}"
            ),
        )
    except Exception as e:
        logger.error(f"Error removing group member: {e}\n{traceback.format_exc()}")
        return CryptoResponse(success=False, data=CryptoResponseData(error=str(e)))


@query
def crypto_list_groups() -> CryptoResponse:
    """List all crypto groups."""
    try:
        result = crypto_group_list()
        groups = Vec["GroupRecord"]()
        for g in result["groups"]:
            groups.append(GroupRecord(name=g["name"], description=g["description"]))
        return CryptoResponse(
            success=True,
            data=CryptoResponseData(groupList=GroupListRecord(groups=groups)),
        )
    except Exception as e:
        logger.error(f"Error listing groups: {e}\n{traceback.format_exc()}")
        return CryptoResponse(success=False, data=CryptoResponseData(error=str(e)))


@query
def crypto_get_group_members(group_name: text) -> CryptoResponse:
    """List members of a crypto group."""
    try:
        result = crypto_group_members(group_name)
        members = Vec["GroupMemberRecord"]()
        for m in result["members"]:
            members.append(
                GroupMemberRecord(principal_id=m["principal"], role=m["role"])
            )
        return CryptoResponse(
            success=True,
            data=CryptoResponseData(groupMembers=GroupMembersRecord(members=members)),
        )
    except Exception as e:
        logger.error(f"Error listing group members: {e}\n{traceback.format_exc()}")
        return CryptoResponse(success=False, data=CryptoResponseData(error=str(e)))


@update
@require(Operations.REALM_ADMIN)
def crypto_share_with_group(scope: text, group_name: text) -> CryptoResponse:
    """Share access to a scope with all members of a group (admin only)."""
    try:
        result = crypto_share_group(scope, group_name)
        if not result["success"]:
            return CryptoResponse(
                success=False, data=CryptoResponseData(error=result["error"])
            )
        return CryptoResponse(
            success=True,
            data=CryptoResponseData(
                message=f"Shared scope {scope} with group {group_name} ({result['envelopes_created']} envelopes)"
            ),
        )
    except Exception as e:
        logger.error(f"Error sharing with group: {e}\n{traceback.format_exc()}")
        return CryptoResponse(success=False, data=CryptoResponseData(error=str(e)))


@update
@require(Operations.REALM_ADMIN)
def crypto_revoke_from_group(scope: text, group_name: text) -> CryptoResponse:
    """Revoke all group members' access to a scope (admin only)."""
    try:
        result = crypto_revoke_group(scope, group_name)
        if not result["success"]:
            return CryptoResponse(
                success=False, data=CryptoResponseData(error=result["error"])
            )
        return CryptoResponse(
            success=True,
            data=CryptoResponseData(
                message=f"Revoked group {group_name} from scope {scope} ({result['envelopes_deleted']} envelopes)"
            ),
        )
    except Exception as e:
        logger.error(f"Error revoking group: {e}\n{traceback.format_exc()}")
        return CryptoResponse(success=False, data=CryptoResponseData(error=str(e)))


# New GGG API endpoints


@query
def get_objects_paginated(
    class_name: str, page_num: nat, page_size: nat, order: str = "asc"
) -> RealmResponse:
    """
    Get paginated list of objects with optional ordering.

    Args:
        class_name: Name of the entity class (e.g., "User", "Transfer", "Mandate")
        page_num: Page number (0-indexed)
        page_size: Number of items per page
        order: Sort order - "asc" for ascending (oldest first) or "desc" for descending (newest first)

    Example (ascending):
    $ dfx canister call --output json canister_id get_objects_paginated '("User", 0, 3, "asc")'

    Example (descending):
    $ dfx canister call --output json canister_id get_objects_paginated '("User", 0, 3, "desc")'

    Response:
    {
      "data": {
        "objectsListPaginated": {
          "objects": [
            "{\"timestamp_created\": \"2025-09-10 11:28:41.147\", \"timestamp_updated\": \"2025-09-10 11:28:41.147\", \"creator\": \"system\", \"updater\": \"system\", \"owner\": \"system\", \"_type\": \"User\", \"_id\": \"1\", \"id\": \"system\", \"profile_picture_url\": \"\"}",
            "{\"timestamp_created\": \"2025-09-10 11:28:41.147\", \"timestamp_updated\": \"2025-09-10 11:28:41.147\", \"creator\": \"system\", \"updater\": \"system\", \"owner\": \"system\", \"_type\": \"User\", \"_id\": \"2\", \"id\": \"fiona_rodriguez_000\", \"profile_picture_url\": \"https://api.dicebear.com/7.x/personas/svg?seed=FionaRodriguez\"}",
            "{\"timestamp_created\": \"2025-09-10 11:28:41.147\", \"timestamp_updated\": \"2025-09-10 11:28:41.147\", \"creator\": \"system\", \"updater\": \"system\", \"owner\": \"system\", \"_type\": \"User\", \"_id\": \"3\", \"id\": \"george_brown_001\", \"profile_picture_url\": \"https://api.dicebear.com/7.x/personas/svg?seed=GeorgeBrown\"}"
          ],
          "pagination": {
            "page_num": "0",
            "page_size": "3",
            "total_items_count": "51",
            "total_pages": "17"
          }
        }
      },
      "success": true
    }
    """

    try:
        logger.info(
            f"Listing {class_name} objects for page {page_num} with page size {page_size}, order: {order}"
        )
        result = list_objects_paginated(
            class_name, page_num=page_num, page_size=page_size, order=order
        )
        objects = result["items"]
        objects_json = [json.dumps(obj.serialize()) for obj in objects]
        logger.info(f"Objects JSON: {objects_json}")
        pagination = PaginationInfo(
            page_num=result["page_num"],
            page_size=result["page_size"],
            total_items_count=result["total_items_count"],
            total_pages=result["total_pages"],
        )
        return RealmResponse(
            success=True,
            data=RealmResponseData(
                objectsListPaginated=ObjectsListRecordPaginated(
                    objects=objects_json, pagination=pagination
                )
            ),
        )
    except Exception as e:
        logger.error(f"Error listing users: {str(e)}\n{traceback.format_exc()}")
        return RealmResponse(success=False, data=RealmResponseData(error=str(e)))


@query
def get_objects(params: Vec[Tuple[str, str]]) -> RealmResponse:
    """Example:

    $ dfx canister call --output json <realm-backend-id> get_objects '(
      vec { record { 0 = "User"; 1 = "1" };  record { 0 = "Realm"; 1 = "1" }; }
    )'
    {
      "data": {
        "objectsList": {
          "objects": [
            "{\"timestamp_created\": \"2025-09-10 11:28:41.147\", \"timestamp_updated\": \"2025-09-10 11:28:41.147\", \"creator\": \"system\", \"updater\": \"system\", \"owner\": \"system\", \"_type\": \"User\", \"_id\": \"1\", \"id\": \"system\", \"profile_picture_url\": \"\"}",
            "{\"timestamp_created\": \"2025-09-10 11:28:41.147\", \"timestamp_updated\": \"2025-09-10 11:28:41.147\", \"creator\": \"system\", \"updater\": \"system\", \"owner\": \"system\", \"_type\": \"Realm\", \"_id\": \"1\", \"name\": \"Generated Demo Realm\", \"manifesto\": \"Generated demo realm with 51 citizens and 5 organizations\", \"id\": \"0\", \"created_at\": \"2025-09-10T13:23:57.099332\", \"status\": \"active\", \"governance_type\": \"democratic\", \"population\": 51, \"organization_count\": 5, \"settings\": {\"voting_period_days\": 7, \"proposal_threshold\": 0.1, \"quorum_percentage\": 0.3, \"tax_rate\": 0.15, \"ubi_amount\": 1000}, \"relations\": {\"treasury\": [{\"_type\": \"Treasury\", \"_id\": \"2\"}]}}"
          ]
        }
      },
      "success": true
    }
    """

    try:
        logger.info("Listing objects")
        result = list_objects(params)
        objects = result
        objects_json = [json.dumps(obj.serialize()) for obj in objects]
        logger.info(f"Objects JSON: {objects_json}")
        return RealmResponse(
            success=True,
            data=RealmResponseData(objectsList=ObjectsListRecord(objects=objects_json)),
        )
    except Exception as e:
        logger.error(f"Error listing objects: {str(e)}\n{traceback.format_exc()}")
        return RealmResponse(success=False, data=RealmResponseData(error=str(e)))


@query
def find_objects(class_name: str, params: Vec[Tuple[str, str]]) -> RealmResponse:
    """
    Search for objects matching the given field criteria.

    Args:
        class_name: Name of the entity class (e.g., "User", "Transfer", "Mandate")
        params: List of (field_name, field_value) tuples to match

    Example:
    $ dfx canister call --output json canister_id find_objects '("User", vec { record { 0 = "id"; 1 = "system" }; })'

    Response:
    {
      "data": {
        "objectsList": {
          "objects": [
            "{\"timestamp_created\": \"2025-09-10 11:28:41.147\", ...}"
          ]
        }
      },
      "success": true
    }
    """
    try:
        logger.info(f"Searching {class_name} objects with params: {params}")
        results = search_objects(class_name, list(params))
        objects_json = [json.dumps(obj.serialize()) for obj in results]
        logger.info(f"Found {len(objects_json)} matching objects")
        return RealmResponse(
            success=True,
            data=RealmResponseData(objectsList=ObjectsListRecord(objects=objects_json)),
        )
    except Exception as e:
        logger.error(f"Error searching objects: {str(e)}\n{traceback.format_exc()}")
        return RealmResponse(success=False, data=RealmResponseData(error=str(e)))


@query
def get_my_invoices() -> RealmResponse:
    """
    Get all invoices belonging to the calling user.

    Filters Invoice entities where invoice.user.id == ic.caller().
    Returns invoices sorted by most recent first.

    Example:
    $ dfx canister call --output json canister_id get_my_invoices '()'

    Response:
    {
      "data": {
        "objectsList": {
          "objects": [
            "{\"id\": \"inv_001\", \"amount\": 0.001, \"status\": \"Pending\", ...}"
          ]
        }
      },
      "success": true
    }
    """
    try:
        caller = ic.caller().to_str()
        logger.info(f"Getting invoices for caller: {caller}")
        from ggg import Invoice

        all_invoices = Invoice.instances()
        user_invoices = [
            inv for inv in all_invoices if inv.user and inv.user.id == caller
        ]
        objects_json = [json.dumps(inv.serialize()) for inv in user_invoices]
        logger.info(f"Found {len(objects_json)} invoices for {caller}")
        return RealmResponse(
            success=True,
            data=RealmResponseData(objectsList=ObjectsListRecord(objects=objects_json)),
        )
    except Exception as e:
        logger.error(f"Error getting invoices: {str(e)}\n{traceback.format_exc()}")
        return RealmResponse(success=False, data=RealmResponseData(error=str(e)))


@update
@require(Operations.SELF_INVOICE_REFRESH)
def refresh_invoice(args: text) -> Async[text]:
    """
    Refresh payment status for an invoice.

    Delegates to the invoice's refresh() method, which uses either:
    • Subaccount mode  (SUBACCOUNT_PAYMENTS_ENABLED = True)  — checks the
      token balance on the invoice's dedicated 32-byte subaccount.
    • Nonce-suffix mode (SUBACCOUNT_PAYMENTS_ENABLED = False) — scans the
      token's ICRC-1 indexer for an incoming transfer whose amount matches
      the invoice's nonce-adjusted exact amount.

    Args (JSON): {"invoice_id": "inv_xxx"}
    Returns (JSON): {"success": true, "data": {...}} or {"success": false, "error": "..."}
    """
    try:
        params = json.loads(args)
        invoice_id = params.get("invoice_id")
        if not invoice_id:
            return json.dumps({"success": False, "error": "invoice_id is required"})

        from ggg import Invoice

        invoice = Invoice[invoice_id]
        if invoice is None:
            return json.dumps(
                {"success": False, "error": f"Invoice '{invoice_id}' not found"}
            )

        result = yield invoice.refresh()

        return json.dumps({"success": True, "data": result})

    except Exception as e:
        logger.error(f"Error in refresh_invoice: {str(e)}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


# Bump the version suffix whenever the default profile baselines in
# Profiles.ALL_PROFILES gain operations that already-deployed realms must
# receive on upgrade (e.g. the permission-based entry_access cutover).
_PROFILE_BASELINE_FLAG = "profile_baseline:v3"


def _sync_profile_baseline() -> void:
    """Add newly introduced baseline operations to existing default profiles.

    create_foundational_objects() seeds profiles only on first init, so an
    already-deployed realm upgraded to a build whose Profiles baselines
    gained operations would otherwise keep stale allowed_to lists and fail
    the extension entry_access gates. Union-add only, once per baseline
    version: admin-granted extras are preserved, and operations an admin
    deliberately revokes after this sync stay revoked.
    """
    from ggg import Profiles, UserProfile

    db = Database.get_instance()
    if db.load("_system", _PROFILE_BASELINE_FLAG):
        return
    try:
        for profile_def in Profiles.ALL_PROFILES:
            profile = UserProfile[profile_def["name"]]
            if not profile:
                continue
            current = [op for op in str(profile.allowed_to or "").split(",") if op]
            missing = [op for op in profile_def["allowed_to"] if op not in current]
            if missing:
                profile.allowed_to = ",".join(current + missing)
                logger.info(
                    f"Profile '{profile_def['name']}': baseline sync added {missing}"
                )
        db.save("_system", _PROFILE_BASELINE_FLAG, "done")
        logger.info("✅ Profile baseline sync complete")
        try:
            from core.codex_overlay import ensure_codex_revert_grants

            ensure_codex_revert_grants()
        except Exception as grant_err:
            logger.warning(f"codex.revert host grant after baseline sync failed: {grant_err}")
    except Exception as e:
        logger.error(f"❌ Profile baseline sync failed: {e}")


def create_foundational_objects() -> void:
    """Create the foundational objects required for every realm to operate."""
    from ggg import Calendar, Identity, Profiles, Realm, Treasury, User, UserProfile
    from ggg.governance.realm import RealmStatus
    from ggg.governance.calendar import DEFAULTS as CALENDAR_DEFAULTS

    logger.info("Creating foundational objects...")

    # Check if foundational objects already exist (for upgrades)
    if len(Realm.instances()) > 0:
        logger.info("Foundational objects already exist, skipping creation")
        _ensure_root_organization()
        return

    try:
        # 1. Create user profiles (all default profiles from Profiles.ALL_PROFILES)
        created_profiles = {}
        for profile_def in Profiles.ALL_PROFILES:
            p = UserProfile(
                name=profile_def["name"],
                allowed_to=",".join(profile_def["allowed_to"]),
                description=f"{profile_def['name'].capitalize()} user profile",
            )
            created_profiles[profile_def["name"]] = p

        profile_names = list(created_profiles.keys())
        logger.info(
            f"Created {len(profile_names)} user profiles: {', '.join(profile_names)}"
        )

        admin_profile = created_profiles["admin"]

        # 2. Create system user
        system_user = User(
            id="system",
        )
        # Link system user to admin profile
        system_user.profiles.add(admin_profile)

        logger.info("Created system user")

        # 3. Create identity for system user
        import uuid

        system_identity = Identity(
            type="system",
            metadata=f"{uuid.uuid4().hex[:8]}-{uuid.uuid4().hex[:8]}",
        )

        logger.info("Created system identity")

        # 4. Create realm - try to load data from manifest.json if available
        realm_name = "Default Realm"
        realm_manifesto = "A realm for digital governance and coordination"
        realm_welcome_message = ""
        realm_open_registration = False

        import json
        import os

        manifest_json_str = "{}"  # default if no manifest.json found
        try:
            manifest_paths = [
                "manifest.json",
                "../manifest.json",
                "/manifest.json",
            ]
            for manifest_path in manifest_paths:
                if os.path.exists(manifest_path):
                    with open(manifest_path, "r") as f:
                        manifest = json.load(f)
                    realm_name = manifest.get("name", realm_name)
                    realm_manifesto = manifest.get("manifesto", realm_manifesto)
                    realm_welcome_message = manifest.get("welcome_message", "")
                    realm_open_registration = manifest.get("open_registration", False)
                    calendar_config = manifest.get("calendar", {})
                    acct_currency_config = manifest.get("accounting_currency", {})
                    manifest_json_str = json.dumps(manifest)
                    logger.info(
                        f"Loaded realm config from {manifest_path}: name={realm_name}"
                    )
                    break
            calendar_config = locals().get("calendar_config", {})
            acct_currency_config = locals().get("acct_currency_config", {})
        except Exception as e:
            logger.warning(f"Could not load manifest.json: {e}, using defaults")
            calendar_config = {}
            acct_currency_config = {}

        realm = Realm(
            name=realm_name,
            manifesto=realm_manifesto,
            welcome_message=realm_welcome_message,
            accounting_currency=acct_currency_config.get("symbol", ""),
            accounting_currency_decimals=acct_currency_config.get("decimals", 8),
            principal_id="",
            manifest_data=manifest_json_str,
            open_registration=bool(realm_open_registration),
            status=RealmStatus.SETUP,
        )

        logger.info(f"Created realm: {realm_name}")

        # 5. Create calendar linked to realm
        calendar_epoch = int(ic.time() / 1_000_000_000)
        calendar = Calendar(
            name=f"{realm.name} Calendar",
            realm=realm,
            epoch=calendar_config.get("epoch", calendar_epoch),
            fiscal_period=calendar_config.get(
                "fiscal_period", CALENDAR_DEFAULTS["fiscal_period"]
            ),
            voting_window=calendar_config.get(
                "voting_window", CALENDAR_DEFAULTS["voting_window"]
            ),
            codex_release_cycle=calendar_config.get(
                "codex_release_cycle", CALENDAR_DEFAULTS["codex_release_cycle"]
            ),
            benefit_cycle=calendar_config.get(
                "benefit_cycle", CALENDAR_DEFAULTS["benefit_cycle"]
            ),
            service_payment_cycle=calendar_config.get(
                "service_payment_cycle", CALENDAR_DEFAULTS["service_payment_cycle"]
            ),
            license_review_cycle=calendar_config.get(
                "license_review_cycle", CALENDAR_DEFAULTS["license_review_cycle"]
            ),
            custom_cycles=json.dumps(calendar_config.get("custom_cycles", {})),
        )

        logger.info(
            f"Created calendar: epoch={calendar_epoch}, fiscal_period={calendar.fiscal_period}s, benefit_cycle={calendar.benefit_cycle}s"
        )

        # 6. Create treasury linked to realm
        treasury = Treasury(
            name=f"{realm.name} Treasury",
            vault_principal_id=None,  # Will be set during vault deployment
            realm=realm,
        )

        logger.info("Created treasury")

        # 7. Register the realm's accounting currency token so the invoice
        #    system can look up ledger/indexer canister IDs for payment
        #    detection.  We no longer seed every well-known token by default.
        try:
            from ic_basilisk_toolkit.wallet import Wallet
            wallet = Wallet()
            acct_currency = getattr(realm, "accounting_currency", "") or ""
            if acct_currency:
                wallet.register_well_known_tokens(acct_currency)
                logger.info(f"Registered accounting currency token: {acct_currency}")
            else:
                logger.info(
                    "Skipped accounting currency token registration (no symbol configured)"
                )
        except Exception as tok_err:
            logger.warning(f"Could not register accounting currency token: {tok_err}")

        logger.info("✅ All foundational objects created successfully")
        _ensure_root_organization()

    except Exception as e:
        logger.error(
            f"❌ Error creating foundational objects: {str(e)}\n{traceback.format_exc()}"
        )
        raise


def _ensure_root_organization() -> void:
    """Ensure the quarter ``root`` org exists (issue #240)."""
    try:
        from core.org_policy import ensure_root_org, grant_root_authority_over_local_orgs

        ensure_root_org()
        grant_root_authority_over_local_orgs()
        logger.info("Ensured root organization (issue #240)")
        try:
            from core.codex_overlay import ensure_codex_revert_grants

            ensure_codex_revert_grants()
        except Exception as grant_err:
            logger.warning(f"codex.revert host grant failed: {grant_err}")
    except Exception as e:
        logger.warning(f"Could not ensure root organization: {e}\n{traceback.format_exc()}")


def _register_wallet_transfer_hook():
    """Register the GGG permission check as the Basilisk OS Wallet pre-transfer hook."""
    try:
        from ic_basilisk_toolkit.wallet import Wallet
        from core.access import _check_access

        def realm_transfer_hook(
            token_name, to_principal, amount, from_subaccount=None, to_subaccount=None
        ):
            caller = ic.caller().to_str()
            canister_id = ic.id().to_str()
            if caller == canister_id:
                return None
            if not _check_access(caller, Operations.TRANSFER_CREATE):
                logger.warning(
                    f"Transfer blocked: {caller} lacks {Operations.TRANSFER_CREATE}"
                )
                return f"Access denied: principal {caller} lacks transfer.create permission"
            return None

        Wallet._pre_transfer_hook = realm_transfer_hook
        logger.info("Registered realm transfer hook on Basilisk OS Wallet")
    except Exception as e:
        logger.error(f"Failed to register wallet transfer hook: {e}")


def initialize() -> void:
    # Register all entity types from ggg
    import ggg
    from ggg import __all__ as entity_names

    for name in entity_names:
        try:
            entity_class = getattr(ggg, name)
            logger.info(f"Registering entity type {name}")
            Database.get_instance().register_entity_type(entity_class)
        except Exception as e:
            logger.error(
                f"Error registering entity type {name}: {str(e)}\n{traceback.format_exc()}"
            )

    # Register ic-basilisk-toolkit crypto entities
    from ic_basilisk_toolkit.crypto import CryptoGroup, CryptoGroupMember, KeyEnvelope

    for crypto_entity in (KeyEnvelope, CryptoGroup, CryptoGroupMember):
        try:
            Database.get_instance().register_entity_type(crypto_entity)
            logger.info(f"Registered crypto entity type {crypto_entity.__name__}")
        except Exception as e:
            logger.error(
                f"Error registering crypto entity {crypto_entity.__name__}: {e}"
            )

    # Create foundational objects after entity registration
    create_foundational_objects()

    # Bring existing realms' default profiles up to the current operations
    # baseline (no-op on fresh realms and when already synced).
    _sync_profile_baseline()

    # Ensure the realm's accounting currency token is in the registry.
    # register_token() is an upsert, so this is safe on every startup.
    try:
        from ic_basilisk_toolkit.wallet import Wallet
        from ggg import Realm
        wallet = Wallet()
        realm = Realm.load("1")
        acct_currency = getattr(realm, "accounting_currency", "") or "" if realm else ""
        if acct_currency:
            wallet.register_well_known_tokens(acct_currency)
            logger.info(f"Ensured accounting currency token is registered: {acct_currency}")
        else:
            logger.info(
                "Skipped accounting currency token registration (no symbol configured)"
            )
    except Exception as e:
        logger.warning(f"Could not register accounting currency token: {e}")

    # Register OS-level wallet transfer hook for permission enforcement
    _register_wallet_transfer_hook()

    # Load the realm's authorization policies before any extension runs, so an
    # extension cannot make a call in the window before the guardrails exist.
    # This sits here rather than in post_upgrade because the WASI filesystem is
    # not mounted during that hook and the policy files read as absent (#281).
    try:
        from core import cedar_authz

        if cedar_authz.load():
            logger.info("Cedar policies loaded; realm guardrails are enforcing")
        elif cedar_authz.available():
            logger.warning(
                f"Cedar present but not enforcing: {cedar_authz.status()['error']}"
            )
        else:
            logger.info(
                "No Cedar module in this build; the Python access checks remain "
                "the only gate"
            )
    except Exception as e:
        logger.warning(f"Cedar policy load skipped: {e}")

    # Initialize all installed extensions
    logger.info("Discovering and initializing extensions...")

    try:
        # Get all installed extension manifests
        extension_manifests = api.extensions.get_all_extension_manifests()
        extension_ids = list(extension_manifests.keys())
        logger.info(f"Found {len(extension_ids)} installed extensions: {extension_ids}")

        # Track status for each extension
        extension_status = {}

        # Initialize each extension
        for extension_id in extension_ids:

            extension_manifest = extension_manifests.get(extension_id, {})
            entity_method_overrides = extension_manifest.get(
                "entity_method_overrides", []
            )

            if not entity_method_overrides:
                logger.info(f"No method overrides found for {extension_id}")
            else:
                logger.info(
                    f"Loading {len(entity_method_overrides)} method override(s) for {extension_id}"
                )
                for override in entity_method_overrides:
                    try:
                        entity_name = override.get("entity")
                        method_name = override.get("method")
                        impl_path = override.get("implementation")
                        method_type = override.get(
                            "type", "method"
                        )  # default to instance method

                        # Validate manifest data
                        if not all([entity_name, method_name, impl_path]):
                            logger.warning(
                                f"Invalid override in {extension_id}: missing entity/method/implementation"
                            )
                            continue

                        # Get entity class
                        entity_class = getattr(ggg, entity_name, None)
                        if not entity_class:
                            logger.warning(
                                f"Entity '{entity_name}' not found in ggg module"
                            )
                            continue

                        parts = impl_path.split(".")
                        func_name = parts[-1]

                        from core.runtime_extensions import _load_module
                        impl_module = _load_module(extension_id)
                        if impl_module is None:
                            logger.warning(f"Extension '{extension_id}' not installed (runtime)")
                            continue
                        impl_func = getattr(impl_module, func_name, None)

                        if not impl_func:
                            logger.warning(
                                f"Function '{func_name}' not found in {module_path}"
                            )
                            continue

                        # Bind method to entity (wrap as classmethod if specified)
                        if method_type == "classmethod":
                            setattr(entity_class, method_name, classmethod(impl_func))
                            logger.info(
                                f"  ✓ {entity_name}.{method_name}() [classmethod] -> {extension_id}.{impl_path}"
                            )
                        elif method_type == "staticmethod":
                            setattr(entity_class, method_name, staticmethod(impl_func))
                            logger.info(
                                f"  ✓ {entity_name}.{method_name}() [staticmethod] -> {extension_id}.{impl_path}"
                            )
                        else:
                            setattr(entity_class, method_name, impl_func)
                            logger.info(
                                f"  ✓ {entity_name}.{method_name}() -> {extension_id}.{impl_path}"
                            )

                    except Exception as e:
                        logger.error(
                            f"Error binding method override in {extension_id}: {str(e)}"
                        )
                        logger.error(traceback.format_exc())

            status = {
                "has_entities": False,
                "has_initialize": False,
                "entity_error": False,
                "init_error": False,
            }

            # Step 1: Try to register extension entity types
            try:
                from core.runtime_extensions import _load_module
                extension_module = _load_module(extension_id)

                if extension_module and hasattr(extension_module, "register_entities"):
                    extension_module.register_entities()
                    status["has_entities"] = True
            except Exception as e:
                logger.warning(
                    f"Error registering entity types for {extension_id}: {str(e)}"
                )
                status["entity_error"] = True

            # Step 2: Try to call extension initialize function
            try:
                result = api.extensions.extension_sync_call(
                    extension_id, "initialize", "{}"
                )
                status["has_initialize"] = True
            except Exception as e:
                # Log the actual error message to help debug
                error_msg = str(e)
                logger.info(
                    f"  [DEBUG] Extension {extension_id} initialize exception: {error_msg}"
                )

                # Check if it's a real error or just missing function
                # Common indicators that the function simply doesn't exist:
                missing_function_indicators = [
                    "not found",
                    "no function",
                    "has no",
                    "does not have",
                    "no attribute",
                    "'initialize'",
                    "attributeerror",
                ]

                is_missing_function = any(
                    indicator in error_msg.lower()
                    for indicator in missing_function_indicators
                )

                if not is_missing_function:
                    # This seems like a real error, not just a missing function
                    logger.warning(f"Error initializing {extension_id}: {error_msg}")
                    status["init_error"] = True
                # Otherwise it's just a missing function (optional), status stays False

            extension_status[extension_id] = status

        # Codex entity_method_overrides used to be applied here, monkey-patching
        # core GGG methods with exec()'d Codex.code. Removed in issue #265:
        # codices reach the realm through sandboxed hooks, and an override that
        # *becomes* a host method cannot cross the sandbox boundary by design.
        _warn_on_codex_overrides()

        # Print summary as a table
        logger.info("")
        logger.info("=" * 70)
        logger.info("📊 EXTENSION INITIALIZATION SUMMARY")
        logger.info("=" * 70)
        logger.info(f"Total extensions: {len(extension_ids)}")
        logger.info("")
        logger.info(
            f"{'Extension Name':<30} {'Entity Registration':<25} {'Initialize'}"
        )
        logger.info("-" * 70)

        for ext_id in sorted(extension_ids):
            status = extension_status[ext_id]

            # Format entity registration status
            if status["entity_error"]:
                entity_status = "❌ Error"
            elif status["has_entities"]:
                entity_status = "✅ Yes"
            else:
                entity_status = "➖ No"

            # Format initialize status
            if status["init_error"]:
                init_status = "❌ Error"
            elif status["has_initialize"]:
                init_status = "✅ Yes"
            else:
                init_status = "➖ No"

            logger.info(f"{ext_id:<30} {entity_status:<25} {init_status}")

        logger.info("=" * 70)
        logger.info("✅ Extension initialization complete.")
        logger.info("")

    except Exception as e:
        logger.error(
            f"❌ Critical error during extension initialization: {str(e)}\n{traceback.format_exc()}"
        )

    # Start TaskManager to schedule pending tasks with enabled schedules.
    # Timer callbacks MUST be created in init/post_upgrade context — closures
    # created from __shell__ do not survive IC call boundaries.
    # TaskManager.run() handles everything: loads all tasks from storage,
    # resets RUNNING->PENDING (timers lost on upgrade), re-registers timers.
    try:
        TaskManager().run()
        logger.info("✅ TaskManager started")
    except Exception as e:
        logger.error(
            f"❌ Error starting TaskManager: {str(e)}\n{traceback.format_exc()}"
        )

    # Population refresh is push-based (issue #156): quarters call
    # ``report_quarter_population`` on join, so the retired
    # quarter_population_sync pull task must NOT be re-seeded. Disable any
    # stale copy left by pre-retirement builds instead.
    try:
        from core.quarter_bootstrap import disable_population_sync_task

        disable_population_sync_task()
    except Exception as e:
        logger.error(f"❌ Error disabling retired population-sync task: {str(e)}")

    # Backfill Proposal field indexes (status, org_scope — ic-python-db#11).
    # Runs as a self-re-arming timer chain so each batch stays far below the
    # per-message instruction limit; a persisted flag makes it once-only.
    try:
        _kick_off_proposal_index_backfill()
    except Exception as e:
        logger.error(f"❌ Error starting proposal index backfill: {str(e)}")

    try:
        from core.treasury_reconcile import schedule_treasury_reconcile_on_boot

        schedule_treasury_reconcile_on_boot()
    except Exception as e:
        logger.warning(f"Could not schedule treasury token reconcile: {e}")


_PROPOSAL_INDEX_BACKFILL_FLAG = "fi_backfill:Proposal:v2"
_PROPOSAL_INDEX_FIELDS = ["status", "org_scope"]


def _kick_off_proposal_index_backfill() -> void:
    """Index pre-existing Proposals for the v2 indexed fields, once.

    New/updated proposals are indexed automatically by the property
    descriptors; this covers rows written before the indexes existed.
    Loading each row also eagerly applies the v1→v2 migration (org_scope
    promoted out of the metadata JSON). Timer callbacks must be created in
    init/post_upgrade/update context, which is why this is called from
    initialize().
    """
    from ggg import Proposal

    db = Database.get_instance()
    if db.load("_system", _PROPOSAL_INDEX_BACKFILL_FLAG):
        return
    if Proposal.max_id() == 0:
        db.save("_system", _PROPOSAL_INDEX_BACKFILL_FLAG, "done")
        return

    state = {"field_idx": 0, "cursor": 1}

    def _step():
        try:
            field = _PROPOSAL_INDEX_FIELDS[state["field_idx"]]
            next_cursor = Proposal.rebuild_field_index(
                field, from_id=state["cursor"], batch=50
            )
            if next_cursor is None:
                state["field_idx"] += 1
                state["cursor"] = 1
                if state["field_idx"] >= len(_PROPOSAL_INDEX_FIELDS):
                    db.save("_system", _PROPOSAL_INDEX_BACKFILL_FLAG, "done")
                    logger.info("✅ Proposal field-index backfill complete")
                    return
            else:
                state["cursor"] = next_cursor
            ic.set_timer(1, _step)
        except Exception as e:
            logger.error(f"❌ Proposal index backfill step failed: {str(e)}")

    ic.set_timer(5, _step)
    logger.info("Proposal field-index backfill scheduled")


@init
def init_(installer_id: text) -> void:
    logger.info("Initializing Realm canister")
    set_controller(ic.caller().to_str())
    initialize()
    from core.setup import record_installer_principal

    record_installer_principal(installer_id)
    logger.info("Realm canister initialized")


@post_upgrade
def post_upgrade_() -> void:
    logger.info("Post-upgrade initializing Realm canister")
    set_controller(ic.caller().to_str())
    initialize()
    logger.info("Realm canister initialized")


@update
@require(Operations.REALM_ADMIN)
def test_timer() -> text:
    """Diagnostic: create entity now, set timer to modify it.

    1. Creates a TaskExecution with result='waiting' (verifiable immediately)
    2. Sets a 5s timer that changes result to 'timer_fired'
    3. Check later: if result is 'timer_fired', timers persist state
    """
    from _cdk import ic
    from ggg import TaskExecution

    te = TaskExecution(name="timer_diag", status="idle", result="waiting")
    te_id = str(te._id)

    def _test_cb():
        try:
            _te = TaskExecution.load(te_id)
            if _te:
                _te.result = "timer_fired"
                _te.status = "completed"
                ic.print(f"TIMER DIAG OK: updated {te_id}")
            else:
                ic.print(f"TIMER DIAG: could not load {te_id}")
        except Exception as e:
            ic.print(f"TIMER DIAG ERROR: {e}")

    tid = ic.set_timer(5, _test_cb)
    return (
        f"Created TaskExecution id={te_id} result=waiting, timer id={tid} fires in 5s"
    )


@update
@require(Operations.REALM_ADMIN)
def start_task_manager() -> text:
    """Start TaskManager to schedule pending tasks.

    Call this after data import to set up IC timers in the proper
    canister update context.  Timer callbacks created from
    __shell__ do NOT survive IC call boundaries.
    """
    try:
        # Relationships resolve via persisted reverse indexes (ic-python-db >= 0.9)
        # — no need to eagerly load child entity types.
        all_tasks = Task.load_some(1, Task.max_id()) if Task.max_id() > 0 else []

        manager = TaskManager()
        count = 0
        for t in all_tasks:
            if t.status and t.status != "completed":
                t.status = "pending"
                t.step_to_execute = 0
                for step in t.steps:
                    step.status = "pending"
                    step.timer_id = None
            manager.add_task(t)
            count += 1
        manager.run()
        msg = f"TaskManager started with {count} task(s)"
        logger.info(msg)
        return msg
    except Exception as e:
        err = f"Error starting TaskManager: {str(e)}\n{traceback.format_exc()}"
        logger.error(err)
        return err


def _extension_ok_response(extension_result) -> ExtensionCallResponse:
    from core.extension_errors import normalize_extension_result_json

    return ExtensionCallResponse(
        success=True, response=normalize_extension_result_json(extension_result)
    )


def _extension_gate_response(verdict: dict) -> ExtensionCallResponse:
    from core.extension_errors import normalize_extension_result_json

    return ExtensionCallResponse(
        success=bool(verdict.get("success")),
        response=normalize_extension_result_json(verdict),
    )


def _extension_permission_response(exc: BaseException) -> ExtensionCallResponse:
    from core.extension_errors import payload_from_permission_error

    return ExtensionCallResponse(
        success=False, response=json.dumps(payload_from_permission_error(exc))
    )


def _extension_denied_response(message: str, operation: str) -> ExtensionCallResponse:
    from core.extension_errors import permission_denied_payload

    return ExtensionCallResponse(
        success=False, response=json.dumps(permission_denied_payload(message, operation))
    )


def _extension_coded_response(exc: BaseException) -> ExtensionCallResponse:
    from core.extension_errors import error_payload

    code = getattr(exc, "error_code", None) or "validation_error"
    return ExtensionCallResponse(
        success=False, response=json.dumps(error_payload(str(code), str(exc)))
    )


@query
def extension_call(extension_name: text, function_name: text, args: text) -> ExtensionCallResponse:
    """Query version of extension call for read-only operations like get_entity_types."""
    try:
        from core.extension_access import gate_extension_call

        caller = ic.caller().to_str()
        verdict = gate_extension_call(
            extension_name, function_name, args, caller, allow_governed=False
        )
        if verdict is not None:
            return _extension_gate_response(verdict)
        logger.debug(
            f"Query calling extension '{extension_name}' function '{function_name}' with args {args}"
        )

        extension_result = api.extensions.extension_sync_call(
            extension_name, function_name, args
        )

        logger.debug(
            f"Got extension result from {extension_name} function {function_name}: {extension_result}"
        )

        return _extension_ok_response(extension_result)

    except PermissionError as e:
        return _extension_permission_response(e)
    except ValueError as e:
        return _extension_coded_response(e)
    except Exception as e:
        logger.error(f"Error in extension_call: {str(e)}\n{traceback.format_exc()}")
        return ExtensionCallResponse(success=False, response=str(e))


@update
def extension_sync_call(extension_name: text, function_name: text, args: text) -> ExtensionCallResponse:
    try:
        caller = ic.caller().to_str()
        gate_err = setup_gate_error(caller)
        if gate_err:
            return ExtensionCallResponse(
                success=False,
                response=_setup_gate_json(gate_err),
            )
        if not _check_access(caller, Operations.EXTENSION_SYNC_CALL):
            return _extension_denied_response(
                f"Access denied: you lack permission '{Operations.EXTENSION_SYNC_CALL}'",
                Operations.EXTENSION_SYNC_CALL,
            )
        from core.extension_access import gate_extension_call

        verdict = gate_extension_call(extension_name, function_name, args, caller)
        if verdict is not None:
            return _extension_gate_response(verdict)
        logger.debug(
            f"Sync calling extension '{extension_name}' entry point '{function_name}' with args {args}"
        )

        extension_result = api.extensions.extension_sync_call(
            extension_name, function_name, args
        )

        logger.debug(
            f"Got extension result from {extension_name} function {function_name}: {extension_result}, type: {type(extension_result)}"
        )

        return _extension_ok_response(extension_result)

    except PermissionError as e:
        return _extension_permission_response(e)
    except ValueError as e:
        return _extension_coded_response(e)
    except Exception as e:
        logger.error(f"Error calling extension: {str(e)}\n{traceback.format_exc()}")
        return ExtensionCallResponse(success=False, response=str(e))


@update
def extension_async_call(extension_name: text, function_name: text, args: text) -> Async[ExtensionCallResponse]:
    try:
        caller = ic.caller().to_str()
        gate_err = setup_gate_error(caller)
        if gate_err:
            return ExtensionCallResponse(
                success=False,
                response=_setup_gate_json(gate_err),
            )
        if not _check_access(caller, Operations.EXTENSION_ASYNC_CALL):
            return _extension_denied_response(
                f"Access denied: you lack permission '{Operations.EXTENSION_ASYNC_CALL}'",
                Operations.EXTENSION_ASYNC_CALL,
            )
        from core.extension_access import gate_extension_call

        verdict = gate_extension_call(extension_name, function_name, args, caller)
        if verdict is not None:
            return _extension_gate_response(verdict)
        logger.debug(
            f"Async calling extension '{extension_name}' entry point '{function_name}' with args {args}"
        )

        from core import extensions as core_extensions

        gen = core_extensions.call_extension_function(
            extension_name, function_name, args, allow_suspend=True
        )
        if not hasattr(gen, "__next__"):
            extension_result = gen
        else:
            driver = core_extensions.drive_suspending_generator(gen)
            try:
                pending = next(driver)
                while True:
                    try:
                        pending = driver.send((yield pending))
                    except StopIteration as done:
                        extension_result = done.value
                        break
            except StopIteration as done:
                extension_result = done.value

        logger.debug(
            f"Got extension result from {extension_name} function {function_name}: {extension_result}, type: {type(extension_result)}"
        )

        return _extension_ok_response(extension_result)

    except PermissionError as e:
        return _extension_permission_response(e)
    except ValueError as e:
        return _extension_coded_response(e)
    except Exception as e:
        logger.error(f"Error calling extension: {str(e)}\n{traceback.format_exc()}")
        return ExtensionCallResponse(success=False, response=str(e))


def http_request_core(data):
    d = json.dumps(data)
    return {
        "status_code": 200,
        "headers": [
            ("Content-Type", "application/json"),
            ("Access-Control-Allow-Origin", "*"),
        ],
        "body": bytes(d + "\n", "ascii"),
        "streaming_strategy": None,
        "upgrade": False,
    }


def http_request_404():
    return {
        "status_code": 404,
        "headers": [
            ("Content-Type", "application/json"),
            ("Access-Control-Allow-Origin", "*"),
        ],
        "body": b'{"error": "Not found"}\n',
        "streaming_strategy": None,
        "upgrade": False,
    }


@query
def http_request(req: HttpRequest) -> HttpResponseIncoming:
    """Handle HTTP requests to the canister. Only for unauthenticated read operations.

    GET /version (and OPTIONS) upgrade to ``http_request_update`` so the
    HTTP gateway can certify the response. Existing /status and /extensions
    stay cheap query reads.
    """
    try:
        method = req["method"]
        url = req["url"]

        logger.info(f"HTTP {method} request to {url}")

        not_found = HttpResponseIncoming(
            status_code=404,
            headers=[],
            body=bytes("Not found", "ascii"),
            streaming_strategy=None,
            upgrade=False,
        )

        if method.upper() == "OPTIONS" or is_version_path(url):
            return http_request_upgrade_signal()

        if method == "GET":
            # Strip leading slash and query params
            path = url.lstrip("/").split("?")[0]

            # Handle /status
            if path == "status" or path == "":
                return http_request_core(get_status())
            # Handle /extensions
            elif path == "extensions":
                return http_request_core({"extensions": list_extensions()})

        return not_found
    except Exception as e:
        logger.error(f"Error handling HTTP request: {str(e)}\n{traceback.format_exc()}")
        return {
            "status_code": 500,
            "headers": [],
            "body": bytes(traceback.format_exc(), "ascii"),
            "streaming_strategy": None,
            "upgrade": False,
        }


@update
def http_request_update(req: HttpRequest) -> HttpResponseIncoming:
    """Certified HTTP responses. Serves GET /version; OPTIONS → 204; else 404."""
    try:
        return version_http_response(req)
    except Exception as e:
        logger.error(f"Error handling HTTP update: {str(e)}\n{traceback.format_exc()}")
        return {
            "status_code": 500,
            "headers": [],
            "body": bytes(traceback.format_exc(), "ascii"),
            "streaming_strategy": None,
            "upgrade": False,
        }


@query
def http_transform(args: HttpTransformArgs) -> HttpResponse:
    """Transform function for HTTP requests - removes headers for consensus"""
    http_response = args["response"]
    http_response["headers"] = []
    return http_response


@update
def __shell__(code: str) -> str:
    """Sandboxed REPL. Product surface is ``api.call`` / ``ext.call`` (same
    host methods and gates as the UI). Entity stubs remain Cedar-gated.

    Missing ``shell.execute`` (opening the REPL) returns
    ``✗ access denied: shell.execute``. In-REPL denials name the inner
    ``@require`` permission and the call, e.g.
    ``✗ access denied: realm.admin from api.call('set_canister_config')``.
    No principal, no traceback (realms#349).
    """
    return product_shell_guard(lambda: _shell_execute(code))


@require(Operations.SHELL_EXECUTE)
def _shell_execute(code: str) -> str:
    orm = _try_init_secure_orm()
    if orm is None:
        raise RuntimeError(
            f"secure_orm is not available: {_secure_orm_error or 'unknown init failure'}"
        )
    return orm.shell(code)


# Removed endpoints (use __shell__ + basilisk shell commands instead):
#   execute_code, download_to_file, download_file, get_task_status,
#   list_scheduled_tasks, stop_task, start_task, get_task_logs,
#   get_task_logs_by_name, create_scheduled_task


@update
@require(Operations.TASK_CREATE)
def create_multi_step_scheduled_task(
    name: str,
    steps_config: str,
    repeat_every: nat,
    run_after: nat = 5,
) -> str:
    """
    Create a multi-step scheduled task from multiple code snippets.

    Args:
        name: Task name
        steps_config: JSON array of step configurations:
            [
                {
                    "code": "<base64-encoded-code>",
                    "run_next_after": seconds (optional, default 0)
                },
                ...
            ]
        repeat_every: Interval in seconds (0 for one-time execution)
        run_after: Delay before first execution in seconds (default: 5)

    Returns JSON with:
    - success: Boolean indicating success
    - task_id: The created task ID
    - task_name: The task name
    - schedule_id: The schedule ID
    - steps_count: Number of steps in the task
    - run_at: Scheduled execution time
    - repeat_every: Repeat interval
    - error: Error message if failed

    Note: is_async is automatically detected based on code content
          (presence of 'yield' or 'async_task')
    """
    try:
        # Parse steps configuration
        steps_data = json.loads(steps_config)

        if not steps_data or len(steps_data) == 0:
            raise ValueError("At least one step is required")

        # Create TaskStep objects
        task_steps = []
        for idx, step_config in enumerate(steps_data):
            # Decode base64 code
            try:
                decoded_code = base64.b64decode(step_config["code"]).decode("utf-8")
            except Exception as e:
                raise ValueError(f"Invalid base64 code in step {idx}: {e}")

            # Create codex for this step
            codex_name = f"_{name}_step_{idx}_{int(ic.time())}"
            codex = Codex(name=codex_name, code=decoded_code)

            # Create call
            call = Call(codex=codex)

            # Get run_next_after delay (default to 0)
            run_next_after = step_config.get("run_next_after", 0)

            # Create step (task assigned after Task creation below)
            step = TaskStep(call=call, run_next_after=run_next_after)
            task_steps.append(step)

            logger.info(
                f"Created step {idx}: codex={codex_name}, "
                f"run_next_after={run_next_after}s"
            )

        # Create task, then link steps via ManyToOne
        task = Task(name=name)
        for step in task_steps:
            step.task = task

        # Create schedule
        schedule = TaskSchedule(
            name=f"schedule_{name}",
            task=task,
            run_at=0,
            repeat_every=repeat_every,
            last_run_at=0,
            disabled=False,
        )

        # Register with TaskManager
        manager = TaskManager()
        manager.add_task(task)

        logger.info(
            f"Created multi-step task: {name} (ID: {task._id}) "
            f"with {len(task_steps)} steps"
        )

        # Trigger task manager to process the schedule
        manager.run()

        return json.dumps(
            {
                "success": True,
                "task_id": str(task._id),
                "task_name": str(task.name),
                "schedule_id": str(schedule._id),
                "steps_count": len(task_steps),
                "run_at": int(schedule.run_at),
                "repeat_every": int(schedule.repeat_every),
            },
            indent=2,
        )

    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in steps_config: {e}")
        return json.dumps({"success": False, "error": f"Invalid JSON: {str(e)}"})
    except Exception as e:
        logger.error(f"Error creating multi-step task: {e}")
        logger.error(traceback.format_exc())
        return json.dumps({"success": False, "error": str(e)}, indent=2)


@update
@require(Operations.REALM_REGISTER)
def register_realm_with_registry(
    registry_canister_id: text,
    realm_name: text,
    frontend_url: text = "",
    canister_ids_json: text = "{}",
) -> Async[text]:
    """
    Register this realm with the central registry.

    Makes an inter-canister call to the realm_registry_backend to register
    this realm. The registry uses ic.caller() (this backend's canister ID)
    as the unique realm ID, preventing duplicates via upsert logic.

    Args:
        registry_canister_id: Canister ID of the realm registry backend
        realm_name: Display name for this realm
        frontend_url: Frontend canister URL (optional)
        canister_ids_json: Pipe-delimited string: frontend_id|token_id|nft_id

    Returns:
        JSON string with success status and message
    """
    try:
        canister_ids = {}
        if canister_ids_json and "|" in canister_ids_json:
            parts = canister_ids_json.split("|")
            canister_ids = {
                "frontend_canister_id": parts[0] if len(parts) > 0 else "",
                "token_canister_id": parts[1] if len(parts) > 1 else "",
                "nft_canister_id": parts[2] if len(parts) > 2 else "",
            }

        result = yield register_realm(
            registry_canister_id, realm_name, frontend_url, "", canister_ids
        )
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error in register_realm_with_registry: {e}")
        logger.error(traceback.format_exc())
        return json.dumps({"success": False, "error": str(e)}, indent=2)


@query
def get_realm_registry_info() -> text:
    """
    Get information about registries this realm is registered with.

    Returns:
        JSON string with list of registries
    """
    try:
        result = get_registry_info()
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error in get_realm_registry_info: {e}")
        return json.dumps(
            {"success": False, "error": str(e), "registries": []}, indent=2
        )


# ── Inter-realm messaging ──────────────────────────────────────────────

@update
@require(Operations.REALM_ADMIN)
def send_realm_message(
    target_canister_id: text,
    title: text,
    message: text,
    topic: text = "",
) -> Async[text]:
    """Send a public message from this realm to another realm.

    Admin-only. Cross-realm messages are always public on the receiving end.

    Args:
        target_canister_id: Canister ID of the target realm's backend.
        title: Message title.
        message: Message body.
        topic: Optional topic label.
    """
    try:
        from ggg import Realm

        realm = Realm.load("1")
        origin_name = getattr(realm, "name", "") if realm else ""

        result = yield _send_realm_message(
            target_canister_id, title, message, topic, origin_name
        )
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error in send_realm_message: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)}, indent=2)


@update
@require_controller
def receive_realm_message(
    title: text,
    message: text,
    topic: text,
    origin_name: text,
) -> text:
    """Receive a public message from another realm (inter-canister entry point).

    Restricted to controllers / trusted principals (the sending realm canister
    must be trusted by this realm). Stored as a public, realm-wide notification
    so every user can read it; cross-realm messages can never be private.
    """
    try:
        from ggg import Notification

        sender_canister = ic.caller().to_str()
        notification = Notification(
            topic=topic or "inter-realm",
            title=title,
            message=message,
            sender=origin_name or sender_canister,
            origin_realm=sender_canister,
            visibility="public",
            audience_type="realm",
            read=False,
            read_by="",
            icon="mail",
            href="/messages",
            color="purple",
            metadata="{}",
        )
        logger.info(
            f"Received inter-realm message from {sender_canister} "
            f"({origin_name!r}): {title!r} -> notification {notification._id}"
        )
        return json.dumps({"success": True, "id": notification._id})
    except Exception as e:
        logger.error(f"Error in receive_realm_message: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


# ── Realm self-upgrade endpoints ───────────────────────────────────────

@update
@require(Operations.REALM_UPGRADE)
def request_upgrade(registry_canister_id: text = "") -> Async[text]:
    """Request an upgrade to the latest realm version.

    Calls the registry to validate credits and cycles, then enqueues
    the upgrade via the installer/deployer pipeline.

    Args:
        registry_canister_id: Optional override for registry canister ID.
            If empty, uses the first registered registry.
    """
    from api.upgrade import request_upgrade as _do_upgrade, _get_registry_canister_id

    try:
        reg_id = registry_canister_id.strip() if registry_canister_id else ""
        if not reg_id:
            reg_id = _get_registry_canister_id()
        if not reg_id:
            return json.dumps({"success": False,
                "error": "No registry canister configured. Set via set_canister_config or register first."})

        result = yield _do_upgrade(reg_id)
        return json.dumps(result)
    except Exception as e:
        logger.error(f"Error in request_upgrade: {e}")
        return json.dumps({"success": False, "error": str(e)})


@query
def get_upgrade_status() -> text:
    """Get the status of the last upgrade request.

    Returns the job_id of the most recent upgrade and its current version.
    """
    from api.upgrade import get_last_upgrade_job_id
    from api.status import get_status

    try:
        job_id = get_last_upgrade_job_id()
        status = get_status()
        current_version = status.get("version", "")
        return json.dumps({
            "success": True,
            "job_id": job_id,
            "current_version": current_version,
            "has_pending_upgrade": bool(job_id),
        })
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


@update
@require(Operations.REALM_UPGRADE)
def get_realm_credits(registry_canister_id: text = "") -> Async[text]:
    """Get this realm's credit balance from the registry.

    Args:
        registry_canister_id: Optional override for registry canister ID.
    """
    from api.upgrade import get_realm_credits as _get_credits, _get_registry_canister_id

    try:
        reg_id = registry_canister_id.strip() if registry_canister_id else ""
        if not reg_id:
            reg_id = _get_registry_canister_id()
        if not reg_id:
            return json.dumps({"success": False,
                "error": "No registry canister configured"})

        result = yield _get_credits(reg_id)
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


@update
@require(Operations.REALM_UPGRADE)
def get_available_upgrade(registry_canister_id: text = "") -> Async[text]:
    """Check if a newer version is available for upgrade.

    Args:
        registry_canister_id: Optional override for registry canister ID.
    """
    from api.upgrade import get_available_version, _get_registry_canister_id
    from api.status import get_status

    try:
        reg_id = registry_canister_id.strip() if registry_canister_id else ""
        if not reg_id:
            reg_id = _get_registry_canister_id()
        if not reg_id:
            return json.dumps({"success": False,
                "error": "No registry canister configured"})

        result = yield get_available_version(reg_id)
        if not result.get("success"):
            return json.dumps(result)

        status = get_status()
        current_version = status.get("version", "")
        latest = result.get("version", {})
        latest_version = latest.get("version", "")

        return json.dumps({
            "success": True,
            "current_version": current_version,
            "latest_version": latest_version,
            "upgrade_available": bool(latest_version and latest_version != current_version),
            "latest": latest,
        })
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


@update
@require(Operations.NFT_MINT)
def mint_land_nft_for_parcel(
    land_id: text,
    owner_principal: text,
    nft_canister_id: text = "",
) -> Async[text]:
    """
    Mint a LAND NFT for a registered land parcel.

    Makes an inter-canister call to the realm's NFT canister to mint
    an NFT representing ownership of the land parcel. The NFT canister
    assigns the next sequential token ID automatically.

    Args:
        land_id: ID of the land parcel
        owner_principal: Principal ID of the land owner
        nft_canister_id: Optional NFT canister ID (uses config if not provided)

    Returns:
        JSON string with success status and token_id
    """
    try:
        from ggg import Land

        # Get the land parcel
        land = Land[land_id]
        if not land:
            return json.dumps({"success": False, "error": f"Land {land_id} not found"})

        # Get NFT canister ID from config if not provided
        canister_id = nft_canister_id or get_nft_canister_id()
        if not canister_id:
            return json.dumps(
                {"success": False, "error": "NFT canister ID not configured"}
            )

        # Mint the NFT (token ID auto-assigned by the NFT canister)
        land_zones = list(land.zones) if hasattr(land, "zones") and land.zones else []
        h3_indexes = [
            zone.h3_index for zone in land_zones if getattr(zone, "h3_index", None)
        ]
        metadata_obj = {}
        if land.metadata:
            try:
                metadata_obj = json.loads(land.metadata)
            except json.JSONDecodeError:
                metadata_obj = {}
        if not h3_indexes and metadata_obj.get("parent_zone"):
            h3_indexes = [str(metadata_obj["parent_zone"])]
        elif metadata_obj.get("h3_indexes"):
            h3_indexes = [str(i) for i in metadata_obj["h3_indexes"] if i]

        result = yield mint_land_nft(
            nft_canister_id=canister_id,
            owner_principal=owner_principal,
            land_id=land_id,
            x_coordinate=land.x_coordinate,
            y_coordinate=land.y_coordinate,
            land_type=land.land_type,
            h3_index=h3_indexes[0] if h3_indexes else None,
            h3_indexes=h3_indexes or None,
        )

        # Update land with NFT token ID if successful
        if result.get("success"):
            land.nft_token_id = result.get("token_id", "")
            logger.info(f"Updated land {land_id} with nft_token_id={land.nft_token_id}")

        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error in mint_land_nft_for_parcel: {e}")
        logger.error(traceback.format_exc())
        return json.dumps({"success": False, "error": str(e)}, indent=2)


def _land_nft_context(land_id: str, nft_canister_id: str = ""):
    """Resolve (land, canister_id, token_id) or return an error dict."""
    from ggg import Land

    land = Land[land_id]
    if not land:
        return None, None, None, {"success": False, "error": f"Land {land_id} not found"}

    canister_id = nft_canister_id or get_nft_canister_id()
    if not canister_id:
        return None, None, None, {"success": False, "error": "NFT canister ID not configured"}

    token_id_str = (land.nft_token_id or "").strip()
    if not token_id_str:
        return None, None, None, {
            "success": False,
            "error": f"Land {land_id} has no minted NFT (nft_token_id is empty)",
        }
    try:
        token_id = int(token_id_str)
    except ValueError:
        return None, None, None, {
            "success": False,
            "error": f"Land {land_id} has an invalid nft_token_id: {token_id_str!r}",
        }
    return land, canister_id, token_id, None


def _append_land_audit(land, entry: dict) -> None:
    """Best-effort append of an authority-action audit entry to land metadata."""
    try:
        meta = json.loads(land.metadata or "{}")
    except Exception:
        meta = {}
    history = meta.get("authority_actions") or []
    history.append(entry)
    # Land.metadata is capped at 512 chars; keep only the most recent entries
    # that fit. The NFT canister's transaction log keeps the full history.
    while history:
        meta["authority_actions"] = history
        serialized = json.dumps(meta, separators=(",", ":"))
        if len(serialized) <= 512:
            land.metadata = serialized
            return
        history = history[1:]
    logger.warning(
        f"Could not persist audit entry for land {land.id} in metadata; "
        f"see the NFT canister transaction log for the authoritative record"
    )


@update
@require(Operations.NFT_FORCE_TRANSFER)
def force_transfer_land_nft(
    land_id: text,
    new_owner_principal: text,
    reason: text = "",
) -> Async[text]:
    """
    Forcefully reassign a land parcel's NFT to a new owner.

    Registry-authority override (ERC-3643-style forced transfer): intended to
    be executed as the outcome of a judicial procedure, a passed governance
    proposal, or key recovery. Requires the nft.force_transfer permission
    (admins have it; others should go through a proposal).
    """
    try:
        from ggg import Land, User

        land, canister_id, token_id, err = _land_nft_context(land_id)
        if err:
            return json.dumps(err, indent=2)

        old_owner_user = land.owner_user.id if land.owner_user else ""

        result = yield force_transfer_nft(
            nft_canister_id=canister_id,
            token_id=token_id,
            new_owner_principal=new_owner_principal,
            memo=reason or "realm force transfer",
        )

        if result.get("success"):
            # Update the realm's canonical ownership record.
            new_owner_user = User[new_owner_principal]
            land.owner_user = new_owner_user  # None if not a registered user
            land.owner_organization = None
            _append_land_audit(land, {
                "action": "force_transfer",
                "token_id": str(token_id),
                "from": old_owner_user,
                "to": new_owner_principal,
                "reason": reason or "",
                "by": str(ic.caller().to_str()),
                "at": int(ic.time()),
            })
            try:
                Land.land_transfer_posthook(land, old_owner_user, new_owner_principal)
            except Exception as hook_err:
                logger.warning(f"land_transfer_posthook failed: {hook_err}")
            result["land_id"] = land_id
            result["token_id"] = str(token_id)
            result["new_owner"] = new_owner_principal
            logger.info(
                f"Force-transferred land {land_id} NFT {token_id} to {new_owner_principal} "
                f"(reason: {reason!r})"
            )

        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error in force_transfer_land_nft: {e}")
        logger.error(traceback.format_exc())
        return json.dumps({"success": False, "error": str(e)}, indent=2)


@update
@require(Operations.NFT_FREEZE)
def freeze_land_nft(land_id: text, reason: text = "") -> Async[text]:
    """
    Freeze a land parcel's NFT: the holder cannot transfer it until unfrozen.

    Used while a dispute or investigation is ongoing. Also marks the land
    record as disputed.
    """
    try:
        from ggg import LandStatus

        land, canister_id, token_id, err = _land_nft_context(land_id)
        if err:
            return json.dumps(err, indent=2)

        result = yield freeze_nft(canister_id, token_id, reason or "realm freeze")

        if result.get("success"):
            land.status = LandStatus.DISPUTED
            _append_land_audit(land, {
                "action": "freeze",
                "token_id": str(token_id),
                "reason": reason or "",
                "by": str(ic.caller().to_str()),
                "at": int(ic.time()),
            })
            result["land_id"] = land_id
            result["token_id"] = str(token_id)

        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error in freeze_land_nft: {e}")
        logger.error(traceback.format_exc())
        return json.dumps({"success": False, "error": str(e)}, indent=2)


@update
@require(Operations.NFT_FREEZE)
def unfreeze_land_nft(land_id: text) -> Async[text]:
    """Unfreeze a land parcel's NFT, restoring normal transfers."""
    try:
        from ggg import LandStatus

        land, canister_id, token_id, err = _land_nft_context(land_id)
        if err:
            return json.dumps(err, indent=2)

        result = yield unfreeze_nft(canister_id, token_id)

        if result.get("success"):
            land.status = LandStatus.ACTIVE
            _append_land_audit(land, {
                "action": "unfreeze",
                "token_id": str(token_id),
                "by": str(ic.caller().to_str()),
                "at": int(ic.time()),
            })
            result["land_id"] = land_id
            result["token_id"] = str(token_id)

        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error in unfreeze_land_nft: {e}")
        logger.error(traceback.format_exc())
        return json.dumps({"success": False, "error": str(e)}, indent=2)


def _token_authority_context(from_principal: str = "", to_principal: str = ""):
    """Resolve the treasury token ledger and validate principals; returns (ledger, err)."""
    from api.nft import _validate_principal_text
    from api.tokens import get_token_canister_id

    ledger = get_token_canister_id()
    if not ledger:
        return None, {"success": False, "error": "Realm token canister ID not configured"}
    for label, value in (("from", from_principal), ("to", to_principal)):
        if value:
            try:
                _validate_principal_text(value)
            except Exception as e:
                return None, {"success": False, "error": f"Invalid {label} principal: {e}"}
    return ledger, None


@update
@require(Operations.TOKEN_FORCE_TRANSFER)
def force_transfer_tokens(
    from_principal: text,
    to_principal: text,
    amount: nat,
    reason: text = "",
) -> Async[text]:
    """
    Forcefully move realm treasury tokens between two accounts.

    Monetary-authority override (ERC-3643-style forcedTransfer): intended to
    be executed as the outcome of a judicial procedure, a passed governance
    proposal, or key recovery. Requires the token.force_transfer permission.
    The realm backend must be the token canister's ledger authority (or a
    controller).
    """
    try:
        from api.tokens import forced_transfer_tokens

        ledger, err = _token_authority_context(from_principal, to_principal)
        if err:
            return json.dumps(err, indent=2)

        result = yield forced_transfer_tokens(
            ledger_canister_id=ledger,
            from_principal=from_principal,
            to_principal=to_principal,
            amount=int(amount),
            memo=reason or "realm forced transfer",
        )

        if result.get("success"):
            result["from"] = from_principal
            result["to"] = to_principal
            result["amount"] = str(amount)
            logger.info(
                f"Force-transferred {amount} tokens from {from_principal} to "
                f"{to_principal} (reason: {reason!r}) by {ic.caller().to_str()}"
            )
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error in force_transfer_tokens: {e}")
        logger.error(traceback.format_exc())
        return json.dumps({"success": False, "error": str(e)}, indent=2)


@update
@require(Operations.TOKEN_FREEZE)
def freeze_token_account(user_principal: text, reason: text = "") -> Async[text]:
    """
    Freeze a realm treasury token account: it cannot send tokens until
    unfrozen (receiving remains possible). Used during disputes,
    investigations, or sanctions. Requires the token.freeze permission.
    """
    try:
        from api.tokens import freeze_token_account_call

        ledger, err = _token_authority_context(user_principal)
        if err:
            return json.dumps(err, indent=2)

        result = yield freeze_token_account_call(
            ledger, user_principal, reason or "realm freeze"
        )

        if result.get("success"):
            result["account"] = user_principal
            logger.info(
                f"Froze token account {user_principal} (reason: {reason!r}) "
                f"by {ic.caller().to_str()}"
            )
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error in freeze_token_account: {e}")
        logger.error(traceback.format_exc())
        return json.dumps({"success": False, "error": str(e)}, indent=2)


@update
@require(Operations.TOKEN_FREEZE)
def unfreeze_token_account(user_principal: text) -> Async[text]:
    """Unfreeze a realm treasury token account, restoring normal transfers."""
    try:
        from api.tokens import unfreeze_token_account_call

        ledger, err = _token_authority_context(user_principal)
        if err:
            return json.dumps(err, indent=2)

        result = yield unfreeze_token_account_call(ledger, user_principal)

        if result.get("success"):
            result["account"] = user_principal
            logger.info(
                f"Unfroze token account {user_principal} by {ic.caller().to_str()}"
            )
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error in unfreeze_token_account: {e}")
        logger.error(traceback.format_exc())
        return json.dumps({"success": False, "error": str(e)}, indent=2)


@query
def get_nft_config() -> text:
    """
    Get the NFT canister configuration for this realm.

    Returns:
        JSON string with NFT canister ID
    """
    try:
        canister_id = get_nft_canister_id()
        return json.dumps(
            {
                "success": True,
                "nft_canister_id": canister_id or "",
                "configured": bool(canister_id),
            },
            indent=2,
        )
    except Exception as e:
        logger.error(f"Error in get_nft_config: {e}")
        return json.dumps({"success": False, "error": str(e)}, indent=2)


@update
def resolve_token_ledger(ledger_canister_id: text) -> Async[text]:
    """Resolve symbol, decimals, and suggested indexer from a ledger canister."""
    try:
        from api.tokens import resolve_ledger_token_info

        result = yield from resolve_ledger_token_info(ledger_canister_id)
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"resolve_token_ledger failed: {e}")
        return json.dumps({"success": False, "error": str(e)}, indent=2)


@update
@require(Operations.REALM_ADMIN)
def reconcile_treasury_token() -> Async[text]:
    """Re-resolve treasury symbol/decimals from the configured ledger."""
    try:
        from core.treasury_reconcile import (
            reconcile_treasury_token as _reconcile_treasury_token,
        )

        result = yield from _reconcile_treasury_token()
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"reconcile_treasury_token failed: {e}")
        return json.dumps({"success": False, "error": str(e)}, indent=2)


@update
def update_realm_config(config_json: str) -> Async[text]:
    """
    Update the realm configuration (name, manifesto, welcome_message,
    branding, registration, and infrastructure settings).

    Branding-only fields (logo, background, primary color, and staged
    image uploads) may be sent by a caller who holds
    ``realm.configure.branding`` without full ``realm.configure``.

    Infrastructure fields (file_registry_canister_id, marketplace_canister_id)
    require the stronger ``realm.configure.infrastructure`` permission.

    Token/NFT canister fields (token_canister_id, token_indexer_canister_id,
    nft_canister_id) require ``realm.configure.tokens`` permission.

    Args:
        config_json: JSON string containing realm configuration fields
    """
    logger.info("🔧 update_realm_config() called")
    try:
        import json

        caller = ic.caller().to_str()
        from core.realm_config_admin import required_realm_config_operations

        config = json.loads(config_json)
        logger.info(f"📋 Config received: {list(config.keys())}")

        required_ops = required_realm_config_operations(config)
        has_configure = _check_access(caller, Operations.REALM_CONFIGURE)
        has_branding = has_configure or _check_access(
            caller, Operations.REALM_CONFIGURE_BRANDING
        )
        for op in required_ops:
            if op == Operations.REALM_CONFIGURE and not has_configure:
                return json.dumps({
                    "success": False,
                    "error": f"Access denied: you lack permission '{op}'",
                    "denied_operation": op,
                })
            if op == Operations.REALM_CONFIGURE_BRANDING and not has_branding:
                return json.dumps({
                    "success": False,
                    "error": f"Access denied: you lack permission '{op}'",
                    "denied_operation": op,
                })
            if op not in (
                Operations.REALM_CONFIGURE,
                Operations.REALM_CONFIGURE_BRANDING,
            ) and not _check_access(caller, op):
                return json.dumps({
                    "success": False,
                    "error": f"Access denied: you lack permission '{op}'",
                    "denied_operation": op,
                })

        config.pop("accounting_currency", None)
        config.pop("accounting_currency_decimals", None)

        token_canister_id = str(config.get("token_canister_id") or "").strip()
        if token_canister_id:
            from api.tokens import resolve_ledger_token_info

            resolved = yield from resolve_ledger_token_info(token_canister_id)
            if not resolved.get("success"):
                return json.dumps(
                    {
                        "success": False,
                        "error": resolved.get("error", "Could not resolve ledger"),
                        "error_code": "ledger_unresolvable",
                    }
                )
            config["accounting_currency"] = resolved["symbol"]
            config["accounting_currency_decimals"] = resolved["decimals"]
            client_indexer = str(config.get("token_indexer_canister_id") or "").strip()
            if not client_indexer:
                config["token_indexer_canister_id"] = resolved.get(
                    "indexer_canister_id"
                )

        from api.setup import stage_live_branding_asset

        staged = []
        for kind, key in (("logo", "logo_data_url"), ("background", "background_data_url")):
            if key not in config:
                continue
            data_url = config.pop(key)
            if not data_url:
                continue
            stage_err = stage_live_branding_asset(kind, data_url)
            if stage_err:
                return json.dumps({"success": False, "error": stage_err})
            staged.append(kind)
        if staged:
            config["apply_staged_branding"] = staged

        # Layer 2 — org policy (issue #262). Realm configuration is a
        # constitutional change: when the root policy is not 1/1, it must go
        # through a root-scoped proposal that replays apply_realm_config.
        from core.governed_action import build_backend_replay_code, gate as governed_gate
        from core.realm_config_admin import (
            apply_realm_config_with_assets,
            describe_realm_config,
        )

        confirm = bool(config.pop("confirm", False))
        verdict = governed_gate(
            caller=caller,
            summary=describe_realm_config(config),
            replay_code=build_backend_replay_code(
                "core.realm_config_admin",
                "apply_realm_config_with_assets",
                json.dumps(config),
            ),
            confirm=confirm,
            metadata_extra={"realm_config": config},
        )
        if verdict is not None:
            return json.dumps(verdict)

        applied = apply_realm_config_with_assets(config)
        if hasattr(applied, "send"):
            applied = yield from applied
        return json.dumps(applied)
    except Exception as e:
        logger.error(f"❌ update_realm_config failed: {e}")
        return json.dumps({"success": False, "error": str(e)})


@query
def get_sandbox_config() -> str:
    """Return the sandboxing policy plus resolved modes for extensions and
    codex hooks (issue #245). Requires ``realm.configure``."""
    try:
        import json

        caller = ic.caller().to_str()
        can_configure = _check_access(caller, Operations.REALM_CONFIGURE)
        if not can_configure:
            return json.dumps({
                "success": False,
                "error": f"Access denied: you lack permission '{Operations.REALM_CONFIGURE}'",
                "denied_operation": Operations.REALM_CONFIGURE,
            })

        from core import runtime_sandbox

        data = runtime_sandbox.get_status()
        data["caller_can_configure"] = True
        return json.dumps({"success": True, "data": data})
    except Exception as e:
        logger.error(f"get_sandbox_config failed: {e}")
        return json.dumps({"success": False, "error": str(e)})


@update
def set_sandbox_config(config_json: str) -> str:
    """Update the sandboxing policy (issue #245). Partial updates are merged.

    Accepted keys: enabled (bool), default_mode ("sandbox"|"in_process"),
    extensions ({ext_id: mode, null clears an override}), codex_hooks
    ({default_mode, hooks}), budget (int >= 0).
    Optionally wrap as ``{"patch": {...}, "confirm": true}`` when root
    department policy requires a governance vote.

    Requires ``realm.configure``. Core/system extensions cannot be sandboxed,
    nor can extensions declaring ``"runtime": "in_process"``. There is no
    in-process fallback: a sandboxed call that cannot spawn fails.
    """
    logger.info("🔧 set_sandbox_config() called")
    try:
        import json

        caller = ic.caller().to_str()
        if not _check_access(caller, Operations.REALM_CONFIGURE):
            return json.dumps({
                "success": False,
                "error": f"Access denied: you lack permission '{Operations.REALM_CONFIGURE}'",
                "denied_operation": Operations.REALM_CONFIGURE,
            })

        from core.sandbox_admin import apply_sandbox_config_change

        try:
            body = json.loads(config_json)
        except json.JSONDecodeError as e:
            return json.dumps({"success": False, "error": f"Invalid JSON: {e}"})

        if not isinstance(body, dict):
            return json.dumps({"success": False, "error": "config must be a JSON object"})

        confirm = bool(body.get("confirm", False))
        if isinstance(body.get("patch"), dict):
            patch = body["patch"]
        else:
            patch = {k: v for k, v in body.items() if k != "confirm"}

        return json.dumps(apply_sandbox_config_change(patch, confirm=confirm))
    except Exception as e:
        logger.error(f"set_sandbox_config failed: {e}")
        return json.dumps({"success": False, "error": str(e)})


def _warn_on_codex_overrides() -> None:
    """Log a migration notice for realms still carrying ``entity_method_overrides``.

    The declarations are inert now, but a realm imported from an old snapshot
    still has them in ``manifest_data``, and silently ignoring them would leave
    an operator believing a governance policy is in force when it is not.
    """
    try:
        import json

        from ggg import Realm

        realms = list(Realm.instances())
        if not realms or not realms[0].manifest_data:
            return
        manifest = json.loads(str(realms[0].manifest_data))
        overrides = manifest.get("entity_method_overrides") or []
        if not overrides:
            return
        for o in overrides:
            logger.warning(
                f"Ignoring entity_method_override "
                f"{o.get('entity')}.{o.get('method')}() -> {o.get('implementation')}: "
                f"the mechanism was removed in issue #265. Port it to a sandboxed "
                f"codex hook (see docs/reference/ENTITY_METHOD_OVERRIDES.md)."
            )
    except Exception as e:
        logger.warning(f"_warn_on_codex_overrides() failed: {e}")


# ---------------------------------------------------------------------------
# Runtime Extension Management (Layer 2)
# See: https://github.com/smart-social-contracts/realms-gos/issues/168
# ---------------------------------------------------------------------------


@update
@require(Operations.EXTENSION_INSTALL)
def install_extension(args: text) -> text:
    """Install a runtime extension from uploaded files.

    Args (JSON): {
        "extension_id": str,
        "files": {"filename": "content", ...}
    }

    At minimum, files must include "entry.py" and "manifest.json".
    Files are written to /extensions/{extension_id}/ on the persistent filesystem.
    """
    try:
        params = json.loads(args)
        ext_id = params.get("extension_id")
        files = params.get("files", {})

        if not ext_id:
            return json.dumps({"success": False, "error": "extension_id is required"})
        if not files:
            return json.dumps({"success": False, "error": "files dict is required"})

        from core.package_manager import replace_denied

        denied = replace_denied(ext_id)
        if denied:
            return json.dumps({"success": False, "error": denied})

        from core.runtime_extensions import install_extension as _install

        ok = _install(ext_id, files, owner=params.get("owner"))
        if ok:
            return json.dumps({"success": True, "extension_id": ext_id, "files_count": len(files)})
        else:
            return json.dumps({"success": False, "error": f"Failed to load extension '{ext_id}' after install"})
    except Exception as e:
        logger.error(f"install_extension error: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@update
@require(Operations.EXTENSION_UNINSTALL)
def uninstall_extension(args: text) -> Async[text]:
    """Uninstall a runtime extension.

    Args (JSON): {"extension_id": str}
    """
    try:
        params = json.loads(args)
        ext_id = params.get("extension_id")

        if not ext_id:
            return json.dumps({"success": False, "error": "extension_id is required"})

        from core.core_extensions import is_core_extension
        if is_core_extension(ext_id):
            return json.dumps({
                "success": False,
                "error": (
                    f"Extension '{ext_id}' is a core extension and cannot be uninstalled. "
                    "Disable the AI assistant in Realm Settings instead of removing llm_chat."
                    if ext_id == "llm_chat"
                    else f"Extension '{ext_id}' is a core extension and cannot be uninstalled."
                ),
            })

        # System extensions (manifest "system": true, e.g. member_dashboard)
        # are part of the platform contract: codices may *override* them via
        # extension_overrides but they cannot be plainly uninstalled (#242).
        # A codex package (manifest "kind": "codex", #244) can likewise only
        # be replaced by installing another version, never removed.
        try:
            from core.runtime_extensions import get_all_extension_manifests as _all_manifests

            _m = _all_manifests().get(ext_id) or {}
            if isinstance(_m, dict) and _m.get("system") is True:
                return json.dumps({
                    "success": False,
                    "error": (
                        f"Extension '{ext_id}' is a system extension and cannot be uninstalled. "
                        "A codex may override it via extension_overrides instead."
                    ),
                })
            if isinstance(_m, dict) and _m.get("kind") == "codex":
                return json.dumps({
                    "success": False,
                    "error": (
                        f"'{ext_id}' is this realm's codex and cannot be uninstalled — "
                        "install another version to replace it."
                    ),
                })
        except Exception:
            pass

        from core.runtime_extensions import (
            get_extension_source,
            uninstall_extension as _uninstall,
        )
        from api.file_registry import cleanup_extension_frontend_on_uninstall

        src = get_extension_source(ext_id) or {}
        version = str(src.get("version") or "")

        ok = _uninstall(ext_id)
        if ok:
            frontend_id = _get_frontend_canister_id()
            if frontend_id:
                yield from cleanup_extension_frontend_on_uninstall(
                    ext_id, version, frontend_id
                )
            return json.dumps({"success": True, "extension_id": ext_id})
        else:
            return json.dumps({"success": False, "error": f"Extension '{ext_id}' not found"})
    except Exception as e:
        logger.error(f"uninstall_extension error: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@query
def list_runtime_extensions() -> text:
    """List all runtime-installed extensions with their manifests."""
    try:
        from core.runtime_extensions import (
            get_all_extension_manifests,
            get_extension_source,
            list_installed,
        )

        installed = list_installed()
        manifests = get_all_extension_manifests()
        sources = {ext_id: get_extension_source(ext_id) for ext_id in installed}
        return json.dumps({
            "success": True,
            "runtime_extensions": installed,
            "all_manifests": manifests,
            "sources": sources,
        })
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


@query
def get_sidebar_manifests() -> text:
    """Return the slim sidebar-relevant slice of every installed extension's
    manifest, intended to be the single source of truth for the sidebar
    (Issue #168 — Layered Realm).

    Combines runtime-installed extensions and any still-bundled extensions
    so the realm_frontend Sidebar.svelte can call exactly one backend
    method regardless of how an extension was installed. The "kind" field
    reflects how it got there:

      - ``runtime``: installed via install_extension / install_extension_from_registry,
                     loaded as ESM at runtime via /extensions/<id>.
      - ``bundled``: shipped inside this realm_backend WASM (legacy path).

    Response (JSON):
      {
        "success": True,
        "manifests": [
          {
            "id":               "voting",
            "name":             "voting",
            "version":          "1.0.3",
            "icon":             "ClipboardListSolid",   # name in iconMap
            "categories":       ["public_services"],
            "profiles":         ["admin", "member"],
            "show_in_sidebar":  true,
            "sidebar_label":    {"en": "Voting", "de": "Abstimmung"},
            "is_default":       false,                  # MY REALM / My Dashboard row
            "kind":             "runtime"               # or "bundled"
          },
          ...
        ]
      }
    """
    try:
        from core.runtime_extensions import (
            get_all_extension_manifests,
            list_installed as _list_runtime_installed,
        )

        runtime_ids = set(_list_runtime_installed())
        manifests = get_all_extension_manifests()  # merged: runtime + bundled

        # Codex overrides (issue #242): a base system extension is hidden when
        # its codex-specific replacement is installed.
        active_overrides = _active_extension_overrides(manifests)
        has_member_home = any(
            isinstance(m, dict) and _sidebar_manifest_is_member_home(eid, m)
            for eid, m in manifests.items()
            if eid not in active_overrides
        )

        out = []
        for ext_id, m in manifests.items():
            if not isinstance(m, dict):
                continue
            if ext_id in active_overrides:
                continue
            label_obj = m.get("sidebar_label")
            if isinstance(label_obj, str):
                label_obj = {"en": label_obj}
            is_default = m.get("is_default") is True
            if ext_id == "public_dashboard" and not has_member_home:
                is_default = True
            out.append({
                "id": ext_id,
                "name": m.get("name") or ext_id,
                "version": m.get("version"),
                "icon": m.get("icon"),
                "categories": m.get("categories") or ["other"],
                "profiles": m.get("profiles") or [],
                "show_in_sidebar": m.get("show_in_sidebar", True) is not False,
                "sidebar_label": label_obj,
                "is_default": is_default,
                "kind": "runtime" if ext_id in runtime_ids else "bundled",
            })

        out.sort(key=lambda e: (e["categories"][0] if e["categories"] else "z", e["id"]))

        categories_meta = [
            {"id": "public_services", "name": "Public Services", "order": 1, "show_header": True, "collapsible": False},
            {"id": "governance", "name": "Governance", "order": 2, "show_header": True, "collapsible": False},
            {"id": "administration", "name": "Administration", "order": 3, "show_header": True, "collapsible": False},
            {"id": "land_territory", "name": "Territory", "order": 4, "show_header": True, "collapsible": False},
            {"id": "finances", "name": "Finances", "order": 5, "show_header": True, "collapsible": False},
            {"id": "settings", "name": "Settings", "order": 6, "show_header": True, "collapsible": False},
            {"id": "other", "name": "Other", "order": 99, "show_header": True, "collapsible": True},
        ]

        return json.dumps({
            "success": True,
            "manifests": out,
            "categories": categories_meta,
            "extension_overrides": active_overrides,
        })
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


def _sidebar_manifest_is_member_home(ext_id: str, manifest: dict) -> bool:
    """True when this row is the MY REALM / My Dashboard home target."""
    if manifest.get("is_default") is True:
        return True
    label = manifest.get("sidebar_label")
    values = list(label.values()) if isinstance(label, dict) else [label]
    return any(str(v or "").strip().lower() == "my dashboard" for v in values)


def _active_extension_overrides(manifests: dict) -> dict:
    """Codex extension overrides whose replacement is actually installed.

    Returns {base_extension_id: override_extension_id} (issue #242). An
    override only takes effect when the replacement extension exists in the
    installed manifest set — otherwise the base (system) extension stays.
    Sourced through the codex hook API (issue #244), which also covers
    legacy /codex_packages manifests.
    """
    try:
        from core.codex_hooks import get_extension_overrides

        return {
            base: override
            for base, override in get_extension_overrides().items()
            if override in manifests
        }
    except Exception:
        return {}


DEFAULT_CATEGORY_ORDER = [
    ("home", "Home", 0),
    ("public_services", "Public Services", 1),
    ("land_territory", "Territory", 2),
    ("governance", "Governance", 3),
    ("people_access", "People & Access", 4),
    ("finances", "Finances", 5),
    ("realm_management", "Realm Management", 6),
    # Legacy ids kept so third-party extensions using them still render.
    ("administration", "Administration", 7),
    ("settings", "Settings", 8),
    ("other", "Other", 99),
]

DEFAULT_ITEM_ORDER = {
    "land_territory": ["land_registry", "zone_selector"],
    "governance": ["voting", "codex_viewer"],
    "people_access": ["role_manager", "access_manager", "member_manager", "department_docs"],
    "finances": ["vault", "metrics"],
    "realm_management": [
        "realm_settings",
        "package_manager",
        "managed_services",
        "admin_dashboard",
        "task_monitor",
        "system_info",
    ],
}


@query
def get_sidebar(args: text) -> text:
    """Return the fully resolved sidebar structure for the calling user.

    Merges extension manifests, hardcoded default category order,
    database overrides (MenuCategoryConfig, MenuItemConfig), and
    department visibility (MenuDepartmentVisibility).

    Response (JSON): {
        "success": true,
        "welcome_items": [...],       # is_default extensions (top, no category)
        "categories": [               # ordered categories with ordered items
            {"id": "public_services", "label": "Public Services", "items": [...]},
            ...
        ],
        "default_path": "/extensions/member_dashboard"
    }
    """
    try:
        from core.runtime_extensions import (
            get_all_extension_manifests,
            list_installed as _list_runtime_installed,
        )
        from core.sidebar_visibility import include_sidebar_category
        from ggg import Extension, MenuCategoryConfig, MenuDepartmentVisibility, MenuItemConfig, User

        caller = ic.caller().to_str()
        params = json.loads(args) if args else {}
        locale = params.get("locale", "en")

        user = User[caller]
        user_profiles = []
        user_departments = []
        if user:
            user_profiles = [p.name for p in user.profiles] if user.profiles else []
            user_departments = [d.name for d in user.departments] if user.departments else []

        manifests = get_all_extension_manifests()

        # Codex overrides (issue #242): hide base system extensions whose
        # codex-specific replacement is installed.
        active_overrides = _active_extension_overrides(manifests)

        # Determine which extensions are visible to this user.
        #
        # Strict DB-based whitelist filtering is applied ONLY to extensions
        # that have actually been seeded with access grants in the database.
        # Extensions with no DB grants fall back to manifest-level profile
        # matching. This per-extension fallback means a partial seed (e.g.
        # installing a single extension, which links only that one to its
        # profiles) never hides every other extension that has not yet been
        # seeded, while still honoring explicit DB grants where they exist.
        user_granted = _user_granted_extension_names(user)
        seeded_extensions = _seeded_extension_names()

        # Apply department visibility rules
        hidden_by_dept = set()
        for rule in MenuDepartmentVisibility.instances():
            if not rule.visible and rule.department:
                if rule.department.name in user_departments:
                    hidden_by_dept.add(rule.extension_name)

        # Load category order overrides from DB
        db_category_configs = {c.category_id: c for c in MenuCategoryConfig.instances()}

        # Load item placement overrides from DB
        db_item_configs = {i.extension_name: i for i in MenuItemConfig.instances()}

        # Resolve category ordering: DB overrides > defaults
        category_order = {}
        category_labels = {}
        for cat_id, label, order in DEFAULT_CATEGORY_ORDER:
            category_order[cat_id] = order
            category_labels[cat_id] = label
        for cat_id, config in db_category_configs.items():
            category_order[cat_id] = config.position
            if config.label:
                category_labels[cat_id] = config.label

        # Filter and group extensions
        welcome_items = []
        mundus_items = []
        grouped = {}

        for ext_id, m in manifests.items():
            if not isinstance(m, dict):
                continue
            if ext_id in active_overrides:
                continue
            if m.get("show_in_sidebar", True) is False:
                continue
            if ext_id in hidden_by_dept:
                continue

            # Profile-based filtering (per-extension: strict whitelist only for
            # seeded extensions, manifest fallback for the rest).
            ext_profiles = m.get("profiles") or []
            if ext_id in seeded_extensions:
                if ext_id not in user_granted:
                    continue
            elif ext_profiles:
                if not any(p in user_profiles for p in ext_profiles):
                    continue

            label_obj = m.get("sidebar_label") or {}
            if isinstance(label_obj, str):
                label_obj = {"en": label_obj}
            item_label = label_obj.get(locale) or label_obj.get("en") or ext_id.replace("_", " ").title()

            _raw_desc = (m.get("short_description") or m.get("description") or "").strip()
            if _raw_desc:
                _tooltip = _raw_desc
                for _sep in [". ", " — ", " - "]:
                    _idx = _raw_desc.find(_sep)
                    if 0 < _idx <= 75:
                        _tooltip = _raw_desc[:_idx].rstrip(".")
                        break
                else:
                    if len(_raw_desc) > 70:
                        _t = _raw_desc[:70]
                        _sp = _t.rfind(" ")
                        _tooltip = (_t[:_sp] if _sp > 40 else _t) + "\u2026"
            else:
                _tooltip = ""

            item = {
                "label": item_label,
                "icon": f"ti-{m.get('icon') or 'layout-dashboard'}",
                "extension_id": ext_id,
                "href": f"/extensions/{ext_id}",
                "tooltip": _tooltip,
            }

            # Welcome pages (is_default) go at top without category
            if m.get("is_default"):
                welcome_items.append(item)
                continue

            # Determine category: DB override > manifest
            if ext_id in db_item_configs:
                cat_id = db_item_configs[ext_id].category_id
            else:
                cats = m.get("categories") or ["other"]
                cat_id = cats[0]

            # Mundus items go into their own super-category section
            if cat_id == "mundus":
                mundus_items.append(item)
                continue

            grouped.setdefault(cat_id, []).append((ext_id, item))

        # Sort items within each category: DB position > hardcoded default > alphabetical
        categories_out = []
        all_cat_ids = set(grouped.keys()) | set(category_order.keys())
        for cat_id in sorted(all_cat_ids, key=lambda c: category_order.get(c, 50)):
            if cat_id not in grouped:
                continue
            if not include_sidebar_category(cat_id, user_profiles):
                continue
            items = grouped[cat_id]
            default_order = DEFAULT_ITEM_ORDER.get(cat_id, [])

            def sort_key(entry, _cat_defaults=default_order):
                eid, itm = entry
                if eid in db_item_configs and db_item_configs[eid].position:
                    return (0, db_item_configs[eid].position, "")
                if eid in _cat_defaults:
                    return (1, _cat_defaults.index(eid), "")
                return (2, 0, itm["label"])

            items.sort(key=sort_key)
            cat_label = category_labels.get(cat_id, cat_id.replace("_", " ").title())
            categories_out.append({
                "id": cat_id,
                "label": cat_label,
                "items": [itm for _, itm in items],
            })

        # Determine default path (resolving the fallback through codex overrides)
        fallback_dashboard = active_overrides.get("member_dashboard", "member_dashboard")
        default_path = f"/extensions/{fallback_dashboard}"
        if welcome_items:
            default_path = welcome_items[0]["href"]

        return json.dumps({
            "success": True,
            "welcome_items": welcome_items,
            "mundus_items": mundus_items,
            "categories": categories_out,
            "default_path": default_path,
            "extension_overrides": active_overrides,
        })
    except Exception as e:
        logger.error(f"Error building sidebar: {str(e)}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@query
def get_menu_config() -> text:
    """Return raw menu configuration for the Menus extension admin UI.

    Response (JSON): {
        "success": true,
        "category_order": [{"category_id": "...", "label": "...", "position": N}, ...],
        "item_overrides": [{"extension_name": "...", "category_id": "...", "position": N}, ...],
        "visibility_rules": [{"extension_name": "...", "department": "...", "visible": bool}, ...]
    }
    """
    try:
        from ggg import MenuCategoryConfig, MenuDepartmentVisibility, MenuItemConfig

        category_order = [
            {"category_id": c.category_id, "label": c.label, "position": c.position}
            for c in MenuCategoryConfig.instances()
        ]
        item_overrides = [
            {"extension_name": i.extension_name, "category_id": i.category_id, "position": i.position}
            for i in MenuItemConfig.instances()
        ]
        visibility_rules = [
            {
                "extension_name": v.extension_name,
                "department": v.department.name if v.department else None,
                "visible": v.visible,
            }
            for v in MenuDepartmentVisibility.instances()
        ]

        return json.dumps({
            "success": True,
            "category_order": category_order,
            "item_overrides": item_overrides,
            "visibility_rules": visibility_rules,
            "defaults": [{"id": c[0], "label": c[1], "position": c[2]} for c in DEFAULT_CATEGORY_ORDER],
        })
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


@update
@require(Operations.REALM_ADMIN)
def set_menu_category_order(args: text) -> text:
    """Save custom category ordering. Replaces all existing MenuCategoryConfig records.

    Args (JSON): {"categories": [{"category_id": "...", "label": "...", "position": N}, ...]}
    """
    try:
        from ggg import MenuCategoryConfig

        params = json.loads(args)
        categories = params.get("categories", [])

        # Clear existing
        for existing in list(MenuCategoryConfig.instances()):
            existing.delete()

        # Create new
        for cat in categories:
            MenuCategoryConfig.create(
                category_id=cat["category_id"],
                label=cat.get("label", ""),
                position=cat.get("position", 0),
            )

        return json.dumps({"success": True})
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


@update
@require(Operations.REALM_ADMIN)
def set_menu_item_config(args: text) -> text:
    """Save custom extension placement. Creates or updates a MenuItemConfig.

    Args (JSON): {"extension_name": "...", "category_id": "...", "position": N}
    """
    try:
        from ggg import MenuItemConfig

        params = json.loads(args)
        ext_name = params["extension_name"]
        category_id = params["category_id"]
        position = params.get("position", 0)

        existing = None
        for item in MenuItemConfig.instances():
            if item.extension_name == ext_name:
                existing = item
                break

        if existing:
            existing.category_id = category_id
            existing.position = position
            existing.save()
        else:
            MenuItemConfig.create(
                extension_name=ext_name,
                category_id=category_id,
                position=position,
            )

        return json.dumps({"success": True})
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


@update
@require(Operations.REALM_ADMIN)
def set_menu_visibility(args: text) -> text:
    """Save per-department extension visibility rule.

    Args (JSON): {"extension_name": "...", "department": "...", "visible": bool}
    """
    try:
        from ggg import Department, MenuDepartmentVisibility

        params = json.loads(args)
        ext_name = params["extension_name"]
        dept_name = params["department"]
        visible = params.get("visible", True)

        dept = Department[dept_name]
        if not dept:
            return json.dumps({"success": False, "error": f"Department '{dept_name}' not found"})

        existing = None
        for rule in MenuDepartmentVisibility.instances():
            if rule.extension_name == ext_name and rule.department and rule.department.name == dept_name:
                existing = rule
                break

        if existing:
            existing.visible = visible
            existing.save()
        else:
            MenuDepartmentVisibility.create(
                extension_name=ext_name,
                department=dept,
                visible=visible,
            )

        return json.dumps({"success": True})
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


@query
def get_extension_frontend_info(args: text) -> text:
    """Return file_registry coordinates for an extension's frontend assets.

    Args (JSON): {"extension_id": str}
    Response (JSON): {
        "success": bool,
        "extension_id": str,
        "version": str,
        "registry_canister_id": str,        # canister id hosting the assets
        "namespace": str,                   # "ext/<id>/<version>"
        "frontend_path": "frontend/dist/index.js"
    }

    realm_frontend uses this to dynamic-import an extension's compiled UI
    bundle from file_registry without baking the registry id into its WASM
    (Issue #168 Layer 2).
    """
    try:
        from core.runtime_extensions import get_extension_source, _load_manifest

        params = json.loads(args) if args else {}
        ext_id = params.get("extension_id")
        if not ext_id:
            return json.dumps({"success": False, "error": "extension_id is required"})

        src = get_extension_source(ext_id) or {}
        manifest = _load_manifest(ext_id)
        manifest_version = str((manifest or {}).get("version") or "")
        # Prefer manifest version — it tracks runtime-install; _source.json can lag.
        version = manifest_version or src.get("version") or ""
        registry_id = src.get("registry_canister_id") or ""

        if not version and not registry_id:
            return json.dumps({
                "success": False,
                "error": f"No frontend source recorded for extension '{ext_id}'",
            })

        return json.dumps({
            "success": True,
            "extension_id": ext_id,
            "version": version,
            "registry_canister_id": registry_id,
            "namespace": f"ext/{ext_id}/{version}" if version else f"ext/{ext_id}",
            "frontend_path": "frontend/dist/index.js",
        })
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


# ---------------------------------------------------------------------------
# Codex package management endpoints
# ---------------------------------------------------------------------------


@update
@require(Operations.CODEX_INSTALL)
def install_codex(args: text) -> text:
    """Install a codex package from uploaded files.

    .. deprecated:: issue #244 — codices are ``kind: codex`` extension
        packages now; use install_extension / install_extension_from_registry.
        Kept for one release for already-published legacy packages.

    Args (JSON): {
        "codex_id": str,
        "files": {"filename": "content", ...},
        "run_init": bool  (optional, default true)
    }

    Files should include manifest.json and *.py codex modules.
    Each .py file creates/updates a Codex entity. Packages that still ship
    ``init.py`` are refused — use the ``init`` hook instead (issue #265).
    ``run_init`` is accepted for callers but ignored.
    """
    try:
        params = json.loads(args)
        codex_id = params.get("codex_id")
        files = params.get("files", {})

        if not codex_id:
            return json.dumps({"success": False, "error": "codex_id is required"})
        if not files:
            return json.dumps({"success": False, "error": "files dict is required"})

        from core.package_manager import replace_denied

        denied = replace_denied(codex_id)
        if denied:
            return json.dumps({"success": False, "error": denied})

        from core.runtime_codex import install_codex_package, legacy_init_py_error

        init_py_error = legacy_init_py_error(codex_id, files)
        if init_py_error:
            return json.dumps({"success": False, "error": init_py_error})

        ok = install_codex_package(codex_id, files)
        if not ok:
            return json.dumps({"success": False, "error": f"Failed to install codex package '{codex_id}'"})

        return json.dumps({"success": True, "codex_id": codex_id, "files_count": len(files)})
    except Exception as e:
        logger.error(f"install_codex error: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@update
@require(Operations.CODEX_UNINSTALL)
def uninstall_codex(args: text) -> text:
    """Uninstall a codex package and its Codex entities.

    Args (JSON): {"codex_id": str}
    """
    try:
        params = json.loads(args)
        codex_id = params.get("codex_id")

        if not codex_id:
            return json.dumps({"success": False, "error": "codex_id is required"})

        from core.runtime_codex import uninstall_codex_package

        ok = uninstall_codex_package(codex_id)
        if ok:
            return json.dumps({"success": True, "codex_id": codex_id})
        else:
            return json.dumps({"success": False, "error": f"Codex package '{codex_id}' not found"})
    except Exception as e:
        logger.error(f"uninstall_codex error: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@update
@require(Operations.CODEX_INSTALL)
def reload_codex(args: text) -> text:
    """Reload all Codex entities from a codex package's files on disk.

    Args (JSON): {"codex_id": str, "run_init": bool (optional, default false)}
    """
    try:
        params = json.loads(args)
        codex_id = params.get("codex_id")
        run_init = params.get("run_init", False)

        if not codex_id:
            return json.dumps({"success": False, "error": "codex_id is required"})

        from core.runtime_codex import legacy_init_py_error, reload_codex_package

        ok = reload_codex_package(codex_id)
        if not ok:
            return json.dumps({"success": False, "error": f"Codex package '{codex_id}' not found"})

        # run_init used to exec init.py; that path is gone. If a package still
        # has the file on disk, surface it rather than pretending setup ran.
        result = {"success": True, "codex_id": codex_id}
        if run_init:
            init_py_error = legacy_init_py_error(codex_id)
            if init_py_error:
                result["init_warning"] = init_py_error
        return json.dumps(result)
    except Exception as e:
        logger.error(f"reload_codex error: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@query
def list_codex_packages() -> text:
    """List all installed codex packages with their manifests.

    Includes both legacy /codex_packages installs and the unified
    ``kind: codex`` extension package (issue #244).
    """
    try:
        from core.runtime_codex import list_installed, get_all_codex_manifests

        installed = list_installed()
        manifests = get_all_codex_manifests()

        try:
            from core.codex_hooks import get_active_codex
            from core.runtime_extensions import get_all_extension_manifests

            active = get_active_codex()
            if active and active not in installed:
                installed = sorted(installed + [active])
                manifests = dict(manifests)
                manifests[active] = get_all_extension_manifests().get(active) or {}
        except Exception:
            pass

        return json.dumps({
            "success": True,
            "codex_packages": installed,
            "manifests": manifests,
        })
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


@update
@require(Operations.CODEX_REVERT)
def revert_codex(args: text) -> text:
    """Revert the realm codex overlay to the previous package (issue #328).

    Callable from the core System UI and from ``__shell__`` via
    ``api.call('revert_codex', '{}')`` or
    ``from core.codex_overlay import revert``.
    """
    try:
        from core.codex_overlay import revert

        return json.dumps(revert())
    except Exception as e:
        logger.error(f"revert_codex error: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@update
@require(Operations.CODEX_REVERT)
def set_codex_safe_mode(args: text) -> text:
    """Enable or disable hook-skipping safe mode (issue #328).

    Args (JSON): {"enabled": bool}
    """
    try:
        params = json.loads(args) if args else {}
        enabled = bool(params.get("enabled"))
        from core.codex_overlay import set_safe_mode

        return json.dumps(set_safe_mode(enabled))
    except Exception as e:
        logger.error(f"set_codex_safe_mode error: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@query
def get_codex_overlay_status() -> text:
    """Current / previous overlay slots and safe-mode flag."""
    try:
        from core.codex_overlay import status

        return json.dumps(status())
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


@query
def list_packages() -> text:
    """Installed package records (id, kind, version, hash, owner, lock).

    Callable from the core System UI and from ``__shell__`` via
    ``api.call('list_packages')`` or
    ``from core.package_manager import list_packages``.
    """
    try:
        from core.package_manager import list_packages as _list

        return json.dumps(_list())
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


@update
def lock_package(args: text) -> text:
    """Lock an installed package so only the owner / bypass may replace it.

    Args (JSON): {"id": str}
    """
    try:
        params = json.loads(args) if args else {}
        from core.package_manager import lock_package as _lock

        return json.dumps(_lock(params.get("id") or params.get("package_id") or ""))
    except Exception as e:
        logger.error(f"lock_package error: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@update
def unlock_package(args: text) -> text:
    """Unlock an installed package.

    Args (JSON): {"id": str}
    """
    try:
        params = json.loads(args) if args else {}
        from core.package_manager import unlock_package as _unlock

        return json.dumps(_unlock(params.get("id") or params.get("package_id") or ""))
    except Exception as e:
        logger.error(f"unlock_package error: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@update
def transfer_package(args: text) -> text:
    """Transfer package ownership to a principal or department name.

    Args (JSON): {"id": str, "owner": str}
    """
    try:
        params = json.loads(args) if args else {}
        from core.package_manager import transfer_package as _transfer

        return json.dumps(
            _transfer(
                params.get("id") or params.get("package_id") or "",
                params.get("owner") or params.get("new_owner") or "",
            )
        )
    except Exception as e:
        logger.error(f"transfer_package error: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


# ---------------------------------------------------------------------------
# Registry-based install endpoints (inter-canister pull from file registry)
# ---------------------------------------------------------------------------


@update
@require(Operations.EXTENSION_INSTALL)
def resync_extension_frontends(args: text) -> Async[text]:
    """Re-copy frontend bundles for all installed extensions.

    Use after a frontend asset canister reinstall/redeploy wipes ``/ext/``
    paths. Same-origin extension loading has no file_registry fallback.

    Bundles already present are skipped unless "force" is set.

    Args (JSON): {
        "registry_canister_id": str|null,   (defaults to realm's file registry)
        "frontend_canister_id": str|null,   (defaults to realm's frontend)
        "extension_ids": [str, ...]|null,    (defaults to all installed)
        "force": bool                        (re-copy bundles already present)
    }
    """
    try:
        params = json.loads(args) if args else {}
        registry_id = params.get("registry_canister_id") or ""
        frontend_id = params.get("frontend_canister_id") or _get_frontend_canister_id()
        extension_ids = params.get("extension_ids")
        force = bool(params.get("force"))

        from api.file_registry import resync_extension_frontends as _resync

        return (yield from _resync(registry_id, frontend_id, extension_ids, force))
    except Exception as e:
        logger.error(f"resync_extension_frontends error: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@update
@require(Operations.EXTENSION_INSTALL)
def install_extension_from_registry(args: text) -> Async[text]:
    """Install an extension by pulling backend files from the file registry.
    Copies frontend bundles to the realm's frontend asset canister before
    installing the backend. Install fails if frontend copy fails.

    Args (JSON): {
        "registry_canister_id": str,
        "ext_id": str,
        "version": str|null  (null = latest),
        "frontend_canister_id": str|null  (overrides Realm entity value)
    }
    """
    try:
        params = json.loads(args)
        registry_id = params.get("registry_canister_id")
        ext_id = params.get("ext_id")
        version = params.get("version")

        if not registry_id:
            return json.dumps({"success": False, "error": "registry_canister_id is required"})
        if not ext_id:
            return json.dumps({"success": False, "error": "ext_id is required"})

        frontend_id = params.get("frontend_canister_id") or _get_frontend_canister_id()

        from api.file_registry import install_extension_from_registry as _install

        result = yield from _install(
            registry_id,
            ext_id,
            version,
            frontend_canister_id=frontend_id,
            owner=params.get("owner"),
            install_dependencies=params.get("install_dependencies", True),
            run_init=params.get("run_init", True),
        )

        # After a canister reinstall, init_() runs before extensions are
        # installed so extension initialize() hooks never fire.  Call it
        # here so schedules/timers created by the hook get registered in
        # this @update context (IC timers require init/post_upgrade/update).
        try:
            result_data = json.loads(result) if isinstance(result, str) else result
            if result_data.get("success"):
                try:
                    init_result = api.extensions.extension_sync_call(ext_id, "initialize", "{}")
                    logger.info(f"Extension {ext_id} post-install initialize: {init_result}")
                except Exception as init_err:
                    logger.info(f"Extension {ext_id} has no initialize hook (ok): {init_err}")
        except Exception as e:
            logger.warning(f"Extension {ext_id} post-install init check failed: {e}")

        return result
    except Exception as e:
        logger.error(f"install_extension_from_registry error: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@update
@require(Operations.CODEX_INSTALL)
def install_codex_from_registry(args: text) -> Async[text]:
    """Install a codex package by pulling files from the file registry.

    Unified pipeline (issue #244): resolves the codex as a ``kind: codex``
    extension package under ``ext/`` first (dependency resolution, singleton
    enforcement, init hook), falling back to the deprecated ``codex/``
    namespace for legacy packages.

    Args (JSON): {
        "registry_canister_id": str,
        "codex_id": str,           (e.g. "syntropia")
        "version": str|null,       (null = latest)
        "run_init": bool,          (optional, default true; set false when
                                    the installer will call run_codex_init)
        "install_dependencies": bool (optional, default true; set false when
                                    deps were already installed as steps)
        "frontend_canister_id": str|null  (overrides Realm entity value)
    }
    """
    try:
        params = json.loads(args)
        registry_id = params.get("registry_canister_id")
        codex_id = params.get("codex_id")
        version = params.get("version")
        run_init = params.get("run_init", True)
        install_dependencies = params.get("install_dependencies", True)
        frontend_id = params.get("frontend_canister_id") or _get_frontend_canister_id()

        if not registry_id:
            return json.dumps({"success": False, "error": "registry_canister_id is required"})
        if not codex_id:
            return json.dumps({"success": False, "error": "codex_id is required"})

        from api.file_registry import install_codex_from_registry as _install

        result = yield from _install(
            registry_id,
            codex_id,
            version,
            run_init,
            frontend_canister_id=frontend_id or None,
            install_dependencies=install_dependencies,
        )
        return result
    except Exception as e:
        logger.error(f"install_codex_from_registry error: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@update
@require(Operations.CODEX_INSTALL)
def run_codex_init(args: text) -> text:
    """Run an already-installed codex init hook (split from package install)."""
    try:
        params = json.loads(args) if args else {}
        from api.file_registry import run_codex_init as _run

        return _run((params.get("codex_id") or "").strip())
    except Exception as e:
        logger.error(f"run_codex_init error: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@update
@require(Operations.REALM_ADMIN)
def install_branding_from_registry(args: text) -> Async[text]:
    """Pull per-realm branding images (logo, background) from the file registry
    and upload them to the realm's frontend asset canister so they are served
    same-origin (e.g. /custom/logo.png) after a reinstall.

    Args (JSON): {
        "registry_canister_id": str,
        "namespace": str,                       (default "branding")
        "files": { "<asset_key>": "<registry_path>" },
            e.g. {"/custom/logo.png": "dominion/logo.png",
                  "/custom/background.png": "dominion/background.png"}
        "frontend_canister_id": str|null        (overrides Realm entity value)
    }
    """
    try:
        params = json.loads(args)
        registry_id = params.get("registry_canister_id")
        namespace = params.get("namespace") or "branding"
        files_map = params.get("files") or {}
        frontend_id = params.get("frontend_canister_id") or _get_frontend_canister_id()

        if not registry_id:
            return json.dumps({"success": False, "error": "registry_canister_id is required"})
        if not files_map:
            return json.dumps({"success": False, "error": "files is required"})

        from api.file_registry import install_branding_from_registry as _install

        result = yield from _install(registry_id, namespace, files_map, frontend_id)
        return result
    except Exception as e:
        logger.error(f"install_branding_from_registry error: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@update
@require(Operations.REALM_ADMIN)
def register_realm_from_registry(args: text) -> Async[text]:
    """Register this realm with the realm registry from a single JSON arg.

    A `(text) -> (text)` wrapper around `api.registry.register_realm` so a Casals
    arrangement step can drive registration declaratively (arrangement steps are
    always single-text-in / text-out). The registry keys the realm on
    `ic.caller()` (== this backend's canister id), so re-applying upserts the
    same record (idempotent).

    Args (JSON): {
        "registry_canister_id": str,            (required)
        "realm_name": str,                      (required)
        "frontend_url": str,                    (optional)
        "canister_ids": {                       (optional)
            "frontend_canister_id": str,
            "token_canister_id": str,
            "nft_canister_id": str
        }
    }
    """
    try:
        params = json.loads(args)
        registry_id = params.get("registry_canister_id")
        realm_name = params.get("realm_name")
        frontend_url = params.get("frontend_url") or ""
        canister_ids = params.get("canister_ids") or {}

        if not registry_id:
            return json.dumps({"success": False, "error": "registry_canister_id is required"})
        if not realm_name:
            return json.dumps({"success": False, "error": "realm_name is required"})

        # Default the frontend id to this realm's own asset canister so the
        # registry can construct the logo/frontend URL client-side.
        if not canister_ids.get("frontend_canister_id"):
            fid = _get_frontend_canister_id()
            if fid:
                canister_ids["frontend_canister_id"] = fid

        result = yield from register_realm(
            registry_id, realm_name, frontend_url, "", canister_ids
        )
        return json.dumps(result)
    except Exception as e:
        logger.error(f"register_realm_from_registry error: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


# ── In-realm setup wizard (issue #8) ─────────────────────────────────────


@update
def enter_setup(creator: Principal, registry_id: text, environment: text) -> text:
    """Put a new realm into in-realm setup (GOS installer bootstrap)."""
    try:
        from core.setup import can_enter_setup, enter_setup as _enter_setup

        caller = ic.caller().to_str()
        if not can_enter_setup(caller, is_controller=ic.is_controller(ic.caller())):
            return json.dumps({"ok": False, "error": "unauthorized"})

        return json.dumps(
            _enter_setup(creator.to_str(), registry_id, environment, caller=caller)
        )
    except Exception as e:
        logger.error(f"enter_setup error: {e}\n{traceback.format_exc()}")
        return json.dumps({"ok": False, "error": str(e)})


@query
def get_setup_state() -> text:
    """Return setup wizard state for the in-realm configuration flow."""
    try:
        from api.setup import get_setup_state as _get_setup_state

        return _get_setup_state()
    except Exception as e:
        logger.error(f"get_setup_state error: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@query
def get_available_codices_cached() -> text:
    """Return the cached codex catalog (fast local read, no inter-canister calls)."""
    try:
        from api.setup import get_available_codices_cached as _get_cached

        return _get_cached()
    except Exception as e:
        logger.error(
            f"get_available_codices_cached error: {e}\n{traceback.format_exc()}"
        )
        return json.dumps({"success": False, "error": str(e)})


@update
def list_available_codices() -> Async[text]:
    """List codex packages available from the configured file registry.

    Exported as update, not composite_query: basilisk's async driver issues
    ic0.call_new, which the replica rejects in composite-query execution, so
    the inter-canister list_codices call fails when exported as a query.
    """
    try:
        from api.setup import list_available_codices as _list

        return (yield from _list())
    except Exception as e:
        logger.error(f"list_available_codices error: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@update
def setup_install_codex(args: text) -> Async[text]:
    """Install a codex during setup (creator or realm admin)."""
    try:
        from api.setup import setup_install_codex as _install

        return (yield from _install(args))
    except Exception as e:
        logger.error(f"setup_install_codex error: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@update
def setup_configure_token(args: text) -> Async[text]:
    """Record token configuration during setup (existing ledger only in v1)."""
    try:
        from api.setup import setup_configure_token as _configure

        return (yield from _configure(args))
    except Exception as e:
        logger.error(f"setup_configure_token error: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@update
def setup_set_branding(args: text) -> text:
    """Store branding selections during setup (creator or realm admin)."""
    try:
        from api.setup import setup_set_branding as _set_branding

        return _set_branding(args)
    except Exception as e:
        logger.error(f"setup_set_branding error: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@update
def setup_save_draft(args: text) -> Async[text]:
    """Persist draft via api.setup (leftover may steal that), then host-apply.

    Leftover ``api/setup.py`` can shadow ``from api.setup import setup_save_draft``.
    Apply lives in ``core.setup_draft_token`` so leftover cannot steal the body.
    """
    try:
        from api.setup import setup_save_draft as _save

        result = _save(args)
        if hasattr(result, "send"):
            result = yield from result
        return result if isinstance(result, str) else json.dumps(result)
    except Exception as e:
        logger.error(f"setup_save_draft error: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@update
def setup_apply_draft_token() -> Async[text]:
    """Unshadowed apply. Body is core.setup_draft_token, never api.setup."""
    try:
        from core.setup_draft_token import apply_setup_draft_token_now

        result = apply_setup_draft_token_now()
        if hasattr(result, "send"):
            return (yield from result)
        return result
    except Exception as e:
        logger.error(f"setup_apply_draft_token error: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@query
def get_setup_draft_asset(kind: text) -> text:
    """Return a single draft branding image data URL for preview."""
    try:
        from api.setup import get_setup_draft_asset as _get_asset

        return _get_asset(kind)
    except Exception as e:
        logger.error(f"get_setup_draft_asset error: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@query
def get_setup_launch_status() -> text:
    """Return persisted setup launch progress."""
    try:
        from api.setup import get_setup_launch_status as _status

        return _status()
    except Exception as e:
        logger.error(f"get_setup_launch_status error: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@update
def setup_launch() -> Async[text]:
    """Start background initialization. Returns as soon as the launch is marked running."""
    try:
        from api.setup import setup_launch as _launch

        result = _launch()
        if hasattr(result, "send"):
            return (yield from result)
        return result
    except Exception as e:
        logger.error(f"setup_launch error: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})


@update
def complete_setup() -> Async[text]:
    """Finish setup: require codex, flip to alpha, notify registry."""
    try:
        from api.setup import complete_setup as _complete

        return (yield from _complete())
    except Exception as e:
        logger.error(f"complete_setup error: {e}\n{traceback.format_exc()}")
        return json.dumps({"success": False, "error": str(e)})
