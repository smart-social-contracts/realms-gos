# ============================================================================
# WASI stdlib compatibility patches
# Must run BEFORE any imports that trigger lazy module loads.
# These fix stub modules created by basilisk's _wasi_safe_import hook.
# Identical to the preamble in src/realm_registry_backend/main.py — keep them
# in sync until basilisk ships these natively.
# ============================================================================
import sys as _wsys

# -- 0a. builtins.open: not available in WASI (no filesystem) --
import builtins as _bi
if not hasattr(_bi, 'open'):
    def _stub_open(*a, **kw):
        raise OSError("open() not available in WASI (no filesystem)")
    _bi.open = _stub_open
del _bi

# -- 0b. os / os.path: create stub BEFORE real os.py loads (it crashes in WASI) --
_os = _wsys.modules.get('os')
if _os is None or not hasattr(_os, 'path') or not hasattr(getattr(_os, 'path', None) or _os, 'exists'):
    if _os is None:
        _os = type(_wsys)('os')
        _os.__file__ = '<wasi-stub>'
        _os.__path__ = []
        _os.__package__ = 'os'
        _wsys.modules['os'] = _os
    class _FakePath:
        sep = '/'
        def exists(self, p): return False
        def join(self, *a): return '/'.join(a)
        def dirname(self, p): return p.rsplit('/', 1)[0] if '/' in p else ''
        def basename(self, p): return p.rsplit('/', 1)[-1]
        def isfile(self, p): return False
        def isdir(self, p): return False
        def abspath(self, p): return p
        def expanduser(self, p): return p
        def normpath(self, p): return p
        def realpath(self, p): return p
        def splitext(self, p):
            i = p.rfind('.')
            return (p[:i], p[i:]) if i > 0 else (p, '')
    _os.path = _FakePath()
    if not hasattr(_os, 'sep'):
        _os.sep = '/'
    if not hasattr(_os, 'getcwd'):
        _os.getcwd = lambda: '/'
    if not hasattr(_os, 'environ'):
        _os.environ = {}
    if not hasattr(_os, 'listdir'):
        _os.listdir = lambda p='/': []
    if not hasattr(_os, 'makedirs'):
        _os.makedirs = lambda p, exist_ok=False: None
    if not hasattr(_os, 'remove'):
        _os.remove = lambda p: None
    if not hasattr(_os, 'urandom'):
        import random as _osrnd
        _os.urandom = lambda n: bytes(_osrnd.getrandbits(8) for _ in range(n))
        del _osrnd
del _os

# -- 1. dataclasses: stub is a no-op, must generate __init__/__repr__ --
_dc = _wsys.modules.get('dataclasses')
if _dc and getattr(_dc, '__file__', '') == '<frozen dataclasses>':
    def _real_dataclass(cls=None, **kw):
        def _wrap(c):
            ann = {}
            for _b in reversed(c.__mro__):
                ann.update(getattr(_b, '__annotations__', {}))
            if ann and '__init__' not in c.__dict__:
                _fs = list(ann.keys())
                _ds = {n: getattr(c, n) for n in _fs if n in c.__dict__}
                def _mkinit(fs, ds):
                    def __init__(self, *args, **kwargs):
                        for i, f in enumerate(fs):
                            if i < len(args):
                                object.__setattr__(self, f, args[i])
                            elif f in kwargs:
                                object.__setattr__(self, f, kwargs[f])
                            elif f in ds:
                                v = ds[f]
                                object.__setattr__(self, f, v() if callable(v) else v)
                            else:
                                raise TypeError(f"{c.__name__}() missing required argument: '{f}'")
                    return __init__
                c.__init__ = _mkinit(_fs, _ds)
            if '__repr__' not in c.__dict__ and ann:
                _fs = list(ann.keys())
                def _mkrepr(fs):
                    def __repr__(self):
                        return f'{type(self).__name__}({", ".join(f"{f}={getattr(self, f, None)!r}" for f in fs)})'
                    return __repr__
                c.__repr__ = _mkrepr(_fs)
            return c
        return _wrap if cls is None else _wrap(cls)
    _dc.dataclass = _real_dataclass
del _dc

# -- 2. traceback: stub has no format_exc/format_exception/print_exc --
import traceback as _tb
if not hasattr(_tb, 'format_exc'):
    def _format_exc(limit=None, chain=True, _s=_wsys):
        ei = _s.exc_info()
        if ei[1] is None:
            return ''
        parts = [f'{type(ei[1]).__name__}: {ei[1]}']
        tb = ei[2]
        frames = []
        while tb:
            f = tb.tb_frame
            frames.append(f'  File "{f.f_code.co_filename}", line {tb.tb_lineno}, in {f.f_code.co_name}')
            tb = tb.tb_next
        if frames:
            parts.insert(0, 'Traceback (most recent call last):')
            for fr in frames:
                parts.insert(-1, fr)
        return '\n'.join(parts)
    _tb.format_exc = _format_exc
    _tb.format_exception = lambda tp, val, tb, **kw: [_format_exc()]
    _tb.print_exc = lambda **kw: print(_format_exc())
del _tb

# -- 3. random: frozen module may lack getrandbits (C ext _random missing) --
import random as _rnd
if not hasattr(_rnd, 'getrandbits'):
    _rnd_counter = [0]
    def _getrandbits(k, _c=_rnd_counter):
        _c[0] += 1
        v = hash((_c[0], id(_c))) & ((1 << k) - 1)
        return v
    _rnd.getrandbits = _getrandbits
    if not hasattr(_rnd, 'randint'):
        _rnd.randint = lambda a, b: a + (_getrandbits(32) % (b - a + 1))
del _rnd

# -- 4. hashlib: stub has no sha256 -- pure Python SHA-256 --
import hashlib as _hl
if not hasattr(_hl, 'sha256'):
    _K256 = [
        0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
        0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
        0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
        0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
        0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
        0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
        0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
        0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2,
    ]
    class _Sha256:
        def __init__(self, data=b''):
            self._h = [0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19]
            self._buf = b''
            self._count = 0
            if data:
                self.update(data)
        def _rr(self, x, n):
            return ((x >> n) | (x << (32 - n))) & 0xffffffff
        def _compress(self, block):
            w = [int.from_bytes(block[i:i+4], 'big') for i in range(0, 64, 4)]
            for i in range(16, 64):
                s0 = self._rr(w[i-15], 7) ^ self._rr(w[i-15], 18) ^ (w[i-15] >> 3)
                s1 = self._rr(w[i-2], 17) ^ self._rr(w[i-2], 19) ^ (w[i-2] >> 10)
                w.append((w[i-16] + s0 + w[i-7] + s1) & 0xffffffff)
            a,b,c,d,e,f,g,h = self._h
            for i in range(64):
                S1 = self._rr(e,6) ^ self._rr(e,11) ^ self._rr(e,25)
                ch = (e & f) ^ ((~e) & g)
                t1 = (h + S1 + ch + _K256[i] + w[i]) & 0xffffffff
                S0 = self._rr(a,2) ^ self._rr(a,13) ^ self._rr(a,22)
                mj = (a & b) ^ (a & c) ^ (b & c)
                t2 = (S0 + mj) & 0xffffffff
                h,g,f,e,d,c,b,a = g,f,e,(d+t1)&0xffffffff,c,b,a,(t1+t2)&0xffffffff
            for i,v in enumerate([a,b,c,d,e,f,g,h]):
                self._h[i] = (self._h[i] + v) & 0xffffffff
        def update(self, data):
            if isinstance(data, str):
                data = data.encode('utf-8')
            self._buf += data
            self._count += len(data)
            while len(self._buf) >= 64:
                self._compress(self._buf[:64])
                self._buf = self._buf[64:]
        def digest(self):
            buf = self._buf + b'\x80'
            buf += b'\x00' * ((55 - len(self._buf)) % 64)
            buf += (self._count * 8).to_bytes(8, 'big')
            h = list(self._h)
            _tmp = _Sha256.__new__(_Sha256)
            _tmp._h = h; _tmp._buf = b''; _tmp._count = 0
            for i in range(0, len(buf), 64):
                _tmp._compress(buf[i:i+64])
            return b''.join(v.to_bytes(4, 'big') for v in _tmp._h)
        def hexdigest(self):
            return self.digest().hex()
        def copy(self):
            c = _Sha256.__new__(_Sha256)
            c._h = list(self._h); c._buf = self._buf; c._count = self._count
            return c
    def _sha256(data=b''):
        return _Sha256(data)
    _hl.sha256 = _sha256
    _hl.new = lambda name, data=b'': _sha256(data) if name == 'sha256' else None
del _hl

# -- 5. math: stub has no ceil/floor/log/sqrt --
import math as _ma
if not hasattr(_ma, 'ceil'):
    _ma.ceil = lambda x: int(x) if x == int(x) else int(x) + (1 if x > 0 else 0)
    _ma.floor = lambda x: int(x) if x >= 0 or x == int(x) else int(x) - 1
    _ma.fabs = lambda x: x if x >= 0 else -x
    _ma.sqrt = lambda x: x ** 0.5
    _ma.pow = lambda x, y: x ** y
    _ma.pi = 3.141592653589793
    _ma.e = 2.718281828459045
    _ma.inf = float('inf')
    _ma.nan = float('nan')
del _ma

del _wsys
# ============================================================================
# End WASI stdlib compatibility patches
# ============================================================================

import json
import traceback
from typing import Optional

from _cdk import (
    Async,
    Opt,
    Principal,
    Record,
    StableBTreeMap,
    Tuple,
    Variant,
    Vec,
    blob,
    float64,
    ic,
    init,
    nat16,
    nat64,
    post_upgrade,
    query,
    text,
    update,
    void,
)
from api.assistants import (
    buy_assistant as buy_assistant_impl,
    create_assistant as create_assistant_impl,
    delist_assistant as delist_assistant_impl,
    get_assistant_details as get_assistant_details_impl,
    get_developer_assistants,
    has_purchased_assistant as has_purchased_assistant_impl,
    list_assistants as list_assistants_impl,
    search_assistants as search_assistants_impl,
)
from api.codices import (
    buy_codex as buy_codex_impl,
    create_codex as create_codex_impl,
    delist_codex as delist_codex_impl,
    get_codex_details as get_codex_details_impl,
    get_developer_codices,
    has_purchased_codex as has_purchased_codex_impl,
    list_codices as list_codices_impl,
    search_codices as search_codices_impl,
)
from api.approval import (
    admin_approve_namespace as admin_approve_namespace_impl,
    review_listing as review_listing_impl,
)
from api.config import (
    add_reviewer as add_reviewer_impl,
    get_billing_service_principal,
    get_casals_frontend_canister_id,
    get_config,
    get_file_registry_canister_id,
    get_license_pricing,
    get_reviewers,
    init_config_from_args,
    remove_reviewer as remove_reviewer_impl,
    set_billing_service_principal as set_billing_service_principal_impl,
    set_casals_frontend_canister_id as set_casals_frontend_canister_id_impl,
    set_file_registry_canister_id as set_file_registry_canister_id_impl,
    set_license_pricing as set_license_pricing_impl,
)
from api.developers import (
    set_developer_name_from_json as set_developer_name_from_json_impl,
    transfer_listing as transfer_listing_impl,
)
from api.extensions import (
    buy_extension as buy_extension_impl,
    create_extension as create_extension_impl,
    delist_extension as delist_extension_impl,
    get_developer_extensions,
    get_extension_details as get_extension_details_impl,
    get_my_purchases as get_my_purchases_impl,
    has_purchased_extension as has_purchased_extension_impl,
    list_extensions as list_extensions_impl,
    search_extensions as search_extensions_impl,
)
from api.licenses import (
    check_license as check_license_impl,
    grant_publisher_from_json as grant_publisher_from_json_impl,
    grant_manual_license as grant_manual_license_impl,
    publishing_status as publishing_status_impl,
    record_license_payment as record_license_payment_impl,
    revoke_license as revoke_license_impl,
)
from api.likes import (
    has_liked as has_liked_impl,
    like_item as like_item_impl,
    my_likes as my_likes_impl,
    recount_listing_likes as recount_listing_likes_impl,
    unlike_item as unlike_item_impl,
)
from api.rankings import (
    top_assistants_by_downloads as top_assistants_by_downloads_impl,
    top_assistants_by_likes as top_assistants_by_likes_impl,
    top_codices_by_downloads as top_codices_by_downloads_impl,
    top_codices_by_likes as top_codices_by_likes_impl,
    top_extensions_by_downloads as top_extensions_by_downloads_impl,
    top_extensions_by_likes as top_extensions_by_likes_impl,
)
from api.status import get_status
from api.version_http import (
    http_request_upgrade_signal,
    version_http_response,
)
from api.verification import (
    list_pending_audits as list_pending_audits_impl,
    request_audit as request_audit_impl,
    set_verification_status as set_verification_status_impl,
)
from ic_python_db import Database
from ic_python_logging import get_logger


# ===========================================================================
# Stable storage + DB
# ===========================================================================

storage = StableBTreeMap[str, str](memory_id=1, max_key_size=400, max_value_size=8000)
Database.init(db_storage=storage, audit_enabled=True)

logger = get_logger("marketplace_backend.main")


# ===========================================================================
# Candid Records / Variants — defined here because basilisk's .did
# generator only parses main.py for type definitions.
# ===========================================================================

# ----- Status ----------------------------------------------------------------

class StatusRecord(Record):
    version: text
    commit: text
    commit_datetime: text
    status: text
    extensions_count: nat64
    codices_count: nat64
    assistants_count: nat64
    purchases_count: nat64
    likes_count: nat64
    licenses_count: nat64
    file_registry_canister_id: text
    billing_service_principal: text
    license_price_usd_cents: nat64
    license_duration_seconds: nat64
    is_caller_controller: bool
    dependencies: Vec[text]
    python_version: text


class StatusResult(Variant, total=False):
    Ok: StatusRecord
    Err: text


# ----- Generic ---------------------------------------------------------------

class GenericResult(Variant, total=False):
    Ok: text
    Err: text


# ----- Config ----------------------------------------------------------------

class ConfigRecord(Record):
    file_registry_canister_id: text
    billing_service_principal: text
    license_price_usd_cents: nat64
    license_duration_seconds: nat64


class ConfigResult(Variant, total=False):
    Ok: ConfigRecord
    Err: text


class LicensePricingRecord(Record):
    license_price_usd_cents: nat64
    license_duration_seconds: nat64


# ----- Extensions ------------------------------------------------------------

class ExtensionInput(Record):
    extension_id: text
    name: text
    description: text
    version: text
    price_e8s: nat64
    icon: text
    categories: text
    screenshots: text
    file_registry_canister_id: text
    file_registry_namespace: text
    download_url: text


class ExtensionListing(Record):
    extension_id: text
    developer: text
    developer_name: text
    name: text
    description: text
    version: text
    price_e8s: nat64
    icon: text
    categories: text
    screenshots: text
    file_registry_canister_id: text
    file_registry_namespace: text
    download_url: text
    installs: nat64
    likes: nat64
    verification_status: text
    verification_notes: text
    is_active: bool
    created_at: float64
    updated_at: float64


class ExtensionResult(Variant, total=False):
    Ok: ExtensionListing
    Err: text


class ExtensionListResult(Record):
    listings: Vec[ExtensionListing]
    total_count: nat64
    page: nat64
    per_page: nat64


# ----- Codices ---------------------------------------------------------------

class CodexInput(Record):
    codex_id: text
    realm_type: text
    name: text
    description: text
    version: text
    price_e8s: nat64
    icon: text
    categories: text
    file_registry_canister_id: text
    file_registry_namespace: text


class CodexListing(Record):
    codex_id: text
    codex_alias: text
    realm_type: text
    developer: text
    developer_name: text
    name: text
    description: text
    version: text
    price_e8s: nat64
    icon: text
    categories: text
    file_registry_canister_id: text
    file_registry_namespace: text
    installs: nat64
    likes: nat64
    verification_status: text
    verification_notes: text
    is_active: bool
    created_at: float64
    updated_at: float64


class CodexResult(Variant, total=False):
    Ok: CodexListing
    Err: text


class CodexListResult(Record):
    listings: Vec[CodexListing]
    total_count: nat64
    page: nat64
    per_page: nat64


# ----- Assistants ------------------------------------------------------------

class AssistantInput(Record):
    assistant_id: text
    name: text
    description: text
    version: text
    price_e8s: nat64
    pricing_summary: text
    icon: text
    categories: text
    runtime: text
    endpoint_url: text
    base_model: text
    requested_role: text
    requested_permissions: text
    domains: text
    languages: text
    training_data_summary: text
    eval_report_url: text
    file_registry_canister_id: text
    file_registry_namespace: text


class AssistantListing(Record):
    assistant_id: text
    assistant_alias: text
    developer: text
    developer_name: text
    name: text
    description: text
    version: text
    price_e8s: nat64
    pricing_summary: text
    icon: text
    categories: text
    runtime: text
    endpoint_url: text
    base_model: text
    requested_role: text
    requested_permissions: text
    domains: text
    languages: text
    training_data_summary: text
    eval_report_url: text
    file_registry_canister_id: text
    file_registry_namespace: text
    installs: nat64
    likes: nat64
    verification_status: text
    verification_notes: text
    is_active: bool
    created_at: float64
    updated_at: float64


class AssistantResult(Variant, total=False):
    Ok: AssistantListing
    Err: text


class AssistantListResult(Record):
    listings: Vec[AssistantListing]
    total_count: nat64
    page: nat64
    per_page: nat64


# ----- Purchases / likes -----------------------------------------------------

class PurchaseRecord(Record):
    purchase_id: text
    realm_principal: text
    item_kind: text
    item_id: text
    developer: text
    price_paid_e8s: nat64
    purchased_at: float64


class LikeRecord(Record):
    item_kind: text
    item_id: text
    created_at: float64


# ----- Licenses --------------------------------------------------------------

class DeveloperLicense(Record):
    principal: text
    created_at: float64
    expires_at: float64
    last_payment_id: text
    last_payment_amount_usd_cents: nat64
    payment_method: text
    note: text
    is_active: bool


class LicenseResult(Variant, total=False):
    Ok: DeveloperLicense
    Err: text


class LicensePaymentInput(Record):
    principal: text
    stripe_session_id: text
    amount_usd_cents: nat64    # what the user actually paid (audit trail)
    duration_seconds: nat64    # how long to extend the license by
    payment_method: text
    note: text


# ----- Verification ----------------------------------------------------------

class PendingAudit(Record):
    item_kind: text
    item_id: text
    name: text
    developer: text
    version: text
    updated_at: float64


# ----- Init arg --------------------------------------------------------------

class MarketplaceInitArg(Record):
    file_registry: Opt[text]
    billing_service_principal: Opt[text]


# ===========================================================================
# Helpers (record builders)
# ===========================================================================


def _ext_listing_record(d: dict) -> "ExtensionListing":
    return ExtensionListing(
        extension_id=d["extension_id"],
        developer=d["developer"],
        developer_name=d.get("developer_name", ""),
        name=d["name"],
        description=d["description"],
        version=d["version"],
        price_e8s=int(d["price_e8s"]),
        icon=d["icon"],
        categories=d["categories"],
        screenshots=d["screenshots"],
        file_registry_canister_id=d["file_registry_canister_id"],
        file_registry_namespace=d["file_registry_namespace"],
        download_url=d.get("download_url", ""),
        installs=int(d["installs"]),
        likes=int(d["likes"]),
        verification_status=d["verification_status"],
        verification_notes=d["verification_notes"],
        is_active=bool(d["is_active"]),
        created_at=float(d["created_at"]),
        updated_at=float(d["updated_at"]),
    )


def _codex_listing_record(d: dict) -> "CodexListing":
    return CodexListing(
        codex_id=d["codex_id"],
        codex_alias=d["codex_alias"],
        realm_type=d["realm_type"],
        developer=d["developer"],
        developer_name=d.get("developer_name", ""),
        name=d["name"],
        description=d["description"],
        version=d["version"],
        price_e8s=int(d["price_e8s"]),
        icon=d["icon"],
        categories=d["categories"],
        file_registry_canister_id=d["file_registry_canister_id"],
        file_registry_namespace=d["file_registry_namespace"],
        installs=int(d["installs"]),
        likes=int(d["likes"]),
        verification_status=d["verification_status"],
        verification_notes=d["verification_notes"],
        is_active=bool(d["is_active"]),
        created_at=float(d["created_at"]),
        updated_at=float(d["updated_at"]),
    )


def _assistant_listing_record(d: dict) -> "AssistantListing":
    return AssistantListing(
        assistant_id=d["assistant_id"],
        assistant_alias=d["assistant_alias"],
        developer=d["developer"],
        developer_name=d.get("developer_name", ""),
        name=d["name"],
        description=d["description"],
        version=d["version"],
        price_e8s=int(d["price_e8s"]),
        pricing_summary=d["pricing_summary"],
        icon=d["icon"],
        categories=d["categories"],
        runtime=d["runtime"],
        endpoint_url=d["endpoint_url"],
        base_model=d["base_model"],
        requested_role=d["requested_role"],
        requested_permissions=d["requested_permissions"],
        domains=d["domains"],
        languages=d["languages"],
        training_data_summary=d["training_data_summary"],
        eval_report_url=d["eval_report_url"],
        file_registry_canister_id=d["file_registry_canister_id"],
        file_registry_namespace=d["file_registry_namespace"],
        installs=int(d["installs"]),
        likes=int(d["likes"]),
        verification_status=d["verification_status"],
        verification_notes=d["verification_notes"],
        is_active=bool(d["is_active"]),
        created_at=float(d["created_at"]),
        updated_at=float(d["updated_at"]),
    )


def _license_record(d: dict) -> "DeveloperLicense":
    return DeveloperLicense(
        principal=d["principal"],
        created_at=float(d["created_at"]),
        expires_at=float(d["expires_at"]),
        last_payment_id=d["last_payment_id"],
        last_payment_amount_usd_cents=int(d.get("last_payment_amount_usd_cents", 0) or 0),
        payment_method=d["payment_method"],
        note=d["note"],
        is_active=bool(d["is_active"]),
    )


# ===========================================================================
# Lifecycle
# ===========================================================================


@init
def init_canister(arg: Opt[MarketplaceInitArg] = None) -> void:
    """Initialise marketplace.

    The init arg is optional. When provided, sets the file_registry
    canister id and the billing_service_principal that may call
    ``record_license_payment``. Both fields are optional inside the
    arg as well; empty strings or absence leave existing config alone.
    """
    logger.info("Marketplace backend initialising")
    fr_id = ""
    bs_principal = ""
    if arg is not None:
        try:
            inner = arg if isinstance(arg, dict) else dict(arg)
            fr_opt = inner.get("file_registry")
            bs_opt = inner.get("billing_service_principal")
            # Opt[text] arrives as either {"None": None}/{} (None) or {"Some": str}
            if isinstance(fr_opt, dict):
                fr_id = fr_opt.get("Some", "") or ""
            elif isinstance(fr_opt, str):
                fr_id = fr_opt
            if isinstance(bs_opt, dict):
                bs_principal = bs_opt.get("Some", "") or ""
            elif isinstance(bs_opt, str):
                bs_principal = bs_opt
        except Exception as e:
            logger.warning(f"could not parse init arg: {e}")
    init_config_from_args(fr_id, bs_principal)
    logger.info("Marketplace backend initialised")


@post_upgrade
def post_upgrade_canister() -> void:
    logger.info("Marketplace backend upgraded")


# ===========================================================================
# Status / config endpoints
# ===========================================================================


@query
def status() -> StatusResult:
    try:
        s = get_status()
        return {"Ok": StatusRecord(
            version=s["version"],
            commit=s["commit"],
            commit_datetime=s["commit_datetime"],
            status=s["status"],
            extensions_count=int(s["extensions_count"]),
            codices_count=int(s["codices_count"]),
            assistants_count=int(s["assistants_count"]),
            purchases_count=int(s["purchases_count"]),
            likes_count=int(s["likes_count"]),
            licenses_count=int(s["licenses_count"]),
            file_registry_canister_id=s["file_registry_canister_id"],
            billing_service_principal=s["billing_service_principal"],
            license_price_usd_cents=int(s["license_price_usd_cents"]),
            license_duration_seconds=int(s["license_duration_seconds"]),
            is_caller_controller=bool(s["is_caller_controller"]),
            dependencies=s["dependencies"],
            python_version=s["python_version"],
        )}
    except Exception as e:
        logger.error(f"status: {e}\n{traceback.format_exc()}")
        return {"Err": f"Internal error: {e}"}


@query
def get_marketplace_config() -> ConfigResult:
    try:
        cfg = get_config()["config"]
        return {"Ok": ConfigRecord(
            file_registry_canister_id=cfg["file_registry_canister_id"],
            billing_service_principal=cfg["billing_service_principal"],
            license_price_usd_cents=int(cfg["license_price_usd_cents"]),
            license_duration_seconds=int(cfg["license_duration_seconds"]),
        )}
    except Exception as e:
        return {"Err": f"Internal error: {e}"}


@query
def get_file_registry_canister_id_q() -> text:
    try:
        return get_file_registry_canister_id()
    except Exception:
        return ""


@update
def set_file_registry_canister_id(canister_id: text) -> GenericResult:
    try:
        r = set_file_registry_canister_id_impl(canister_id)
        return {"Ok": r["file_registry_canister_id"]} if r["success"] else {"Err": r["error"]}
    except Exception as e:
        return {"Err": str(e)}


@query
def get_casals_frontend_canister_id_q() -> text:
    try:
        return get_casals_frontend_canister_id()
    except Exception:
        return ""


@update
def set_casals_frontend_canister_id(canister_id: text) -> GenericResult:
    try:
        r = set_casals_frontend_canister_id_impl(canister_id)
        return {"Ok": r["casals_frontend_canister_id"]} if r["success"] else {"Err": r["error"]}
    except Exception as e:
        return {"Err": str(e)}


@query
def list_reviewers() -> Vec[text]:
    """Principals allowed to decide on submissions (issue #267)."""
    try:
        return get_reviewers()
    except Exception as e:
        logger.error(f"list_reviewers: {e}")
        return []


@update
def add_reviewer(principal: text) -> GenericResult:
    try:
        r = add_reviewer_impl(principal)
        return {"Ok": ",".join(r["reviewers"])} if r["success"] else {"Err": r["error"]}
    except Exception as e:
        return {"Err": str(e)}


@update
def remove_reviewer(principal: text) -> GenericResult:
    try:
        r = remove_reviewer_impl(principal)
        return {"Ok": ",".join(r["reviewers"])} if r["success"] else {"Err": r["error"]}
    except Exception as e:
        return {"Err": str(e)}


@update
def review_listing(item_kind: text, item_id: text, approve: bool, notes: text) -> Async[text]:
    """Approve or reject a submission, stamping the decision onto the file
    registry so realms can see it. Returns the result as a JSON string."""
    try:
        result = yield from review_listing_impl(
            caller=str(ic.caller()),
            item_kind=item_kind,
            item_id=item_id,
            approve=approve,
            notes=notes,
        )
        return json.dumps(result)
    except Exception as e:
        logger.error(f"review_listing: {e}")
        return json.dumps({"success": False, "error": str(e)})


@update
def admin_approve_namespace(namespace: text, notes: text) -> Async[text]:
    """Controller-only migration tool: approve a registry namespace that has no
    listing, attributing the approval to this canister."""
    try:
        result = yield from admin_approve_namespace_impl(
            namespace=namespace, notes=notes
        )
        return json.dumps(result)
    except Exception as e:
        logger.error(f"admin_approve_namespace: {e}")
        return json.dumps({"success": False, "error": str(e)})


@query
def get_billing_service_principal_q() -> text:
    try:
        return get_billing_service_principal()
    except Exception:
        return ""


@update
def set_billing_service_principal(principal: text) -> GenericResult:
    try:
        r = set_billing_service_principal_impl(principal)
        return {"Ok": r["billing_service_principal"]} if r["success"] else {"Err": r["error"]}
    except Exception as e:
        return {"Err": str(e)}


@query
def get_license_pricing_q() -> LicensePricingRecord:
    p = get_license_pricing()["pricing"]
    return LicensePricingRecord(
        license_price_usd_cents=int(p["license_price_usd_cents"]),
        license_duration_seconds=int(p["license_duration_seconds"]),
    )


@update
def set_license_pricing(usd_cents: nat64, duration_seconds: nat64) -> GenericResult:
    try:
        r = set_license_pricing_impl(int(usd_cents), int(duration_seconds))
        return {"Ok": "updated"} if r["success"] else {"Err": r["error"]}
    except Exception as e:
        return {"Err": str(e)}


# ===========================================================================
# Extensions
# ===========================================================================


def _ext_input_to_kwargs(ext: ExtensionInput) -> dict:
    return {
        "extension_id": ext["extension_id"],
        "name": ext["name"],
        "description": ext["description"],
        "version": ext["version"],
        "price_e8s": int(ext["price_e8s"]),
        "icon": ext["icon"],
        "categories": ext["categories"],
        "screenshots": ext["screenshots"],
        "file_registry_canister_id": ext["file_registry_canister_id"],
        "file_registry_namespace": ext["file_registry_namespace"],
        "download_url": ext.get("download_url", ""),
    }


@update
def create_extension(ext: ExtensionInput) -> GenericResult:
    try:
        caller = str(ic.caller())
        r = create_extension_impl(developer=caller, **_ext_input_to_kwargs(ext))
        return {"Ok": f"{r.get('action', 'ok')}:{r.get('extension_id', ext['extension_id'])}"} if r["success"] else {"Err": r["error"]}
    except Exception as e:
        logger.error(f"create_extension: {e}\n{traceback.format_exc()}")
        return {"Err": str(e)}


@update
def update_extension(ext: ExtensionInput) -> GenericResult:
    # update has the same shape — create_extension_impl is upsert-aware.
    return create_extension(ext)


@update
def delist_extension(extension_id: text) -> GenericResult:
    try:
        caller = str(ic.caller())
        r = delist_extension_impl(caller, extension_id)
        return {"Ok": r["message"]} if r["success"] else {"Err": r["error"]}
    except Exception as e:
        return {"Err": str(e)}


@query
def get_extension_details(extension_id: text) -> ExtensionResult:
    try:
        r = get_extension_details_impl(extension_id)
        return {"Ok": _ext_listing_record(r["extension"])} if r["success"] else {"Err": r["error"]}
    except Exception as e:
        return {"Err": str(e)}


@query
def list_marketplace_extensions(page: nat64, per_page: nat64, verified_only: bool) -> ExtensionListResult:
    try:
        r = list_extensions_impl(int(page), int(per_page), bool(verified_only))
        return ExtensionListResult(
            listings=[_ext_listing_record(d) for d in r["listings"]],
            total_count=int(r["total_count"]),
            page=int(r["page"]),
            per_page=int(r["per_page"]),
        )
    except Exception as e:
        logger.error(f"list_marketplace_extensions: {e}")
        return ExtensionListResult(listings=[], total_count=int(0), page=page, per_page=per_page)


@query
def search_extensions(query_text: text, verified_only: bool) -> Vec[ExtensionListing]:
    try:
        return [_ext_listing_record(d) for d in search_extensions_impl(query_text, bool(verified_only))]
    except Exception as e:
        logger.error(f"search_extensions: {e}")
        return []


@query
def get_my_extensions() -> Vec[ExtensionListing]:
    try:
        caller = str(ic.caller())
        return [_ext_listing_record(d) for d in get_developer_extensions(caller)]
    except Exception as e:
        logger.error(f"get_my_extensions: {e}")
        return []


# ===========================================================================
# Codices
# ===========================================================================


def _codex_input_to_kwargs(c: CodexInput) -> dict:
    return {
        "codex_id": c["codex_id"],
        "realm_type": c.get("realm_type", ""),
        "name": c["name"],
        "description": c["description"],
        "version": c["version"],
        "price_e8s": int(c["price_e8s"]),
        "icon": c["icon"],
        "categories": c["categories"],
        "file_registry_canister_id": c["file_registry_canister_id"],
        "file_registry_namespace": c["file_registry_namespace"],
    }


@update
def create_codex(c: CodexInput) -> GenericResult:
    try:
        caller = str(ic.caller())
        r = create_codex_impl(developer=caller, **_codex_input_to_kwargs(c))
        return {"Ok": f"{r.get('action', 'ok')}:{r.get('codex_id', c['codex_id'])}"} if r["success"] else {"Err": r["error"]}
    except Exception as e:
        logger.error(f"create_codex: {e}\n{traceback.format_exc()}")
        return {"Err": str(e)}


@update
def update_codex(c: CodexInput) -> GenericResult:
    return create_codex(c)


@update
def delist_codex(codex_id: text) -> GenericResult:
    try:
        caller = str(ic.caller())
        r = delist_codex_impl(caller, codex_id)
        return {"Ok": r["message"]} if r["success"] else {"Err": r["error"]}
    except Exception as e:
        return {"Err": str(e)}


@query
def get_codex_details(codex_id: text) -> CodexResult:
    try:
        r = get_codex_details_impl(codex_id)
        return {"Ok": _codex_listing_record(r["codex"])} if r["success"] else {"Err": r["error"]}
    except Exception as e:
        return {"Err": str(e)}


@query
def list_marketplace_codices(page: nat64, per_page: nat64, verified_only: bool) -> CodexListResult:
    try:
        r = list_codices_impl(int(page), int(per_page), bool(verified_only))
        return CodexListResult(
            listings=[_codex_listing_record(d) for d in r["listings"]],
            total_count=int(r["total_count"]),
            page=int(r["page"]),
            per_page=int(r["per_page"]),
        )
    except Exception as e:
        logger.error(f"list_marketplace_codices: {e}")
        return CodexListResult(listings=[], total_count=int(0), page=page, per_page=per_page)


@query
def search_codices(query_text: text, verified_only: bool) -> Vec[CodexListing]:
    try:
        return [_codex_listing_record(d) for d in search_codices_impl(query_text, bool(verified_only))]
    except Exception as e:
        logger.error(f"search_codices: {e}")
        return []


@query
def get_my_codices() -> Vec[CodexListing]:
    try:
        caller = str(ic.caller())
        return [_codex_listing_record(d) for d in get_developer_codices(caller)]
    except Exception as e:
        logger.error(f"get_my_codices: {e}")
        return []


# ===========================================================================
# Assistants
# ===========================================================================


def _assistant_input_to_kwargs(a: AssistantInput) -> dict:
    return {
        "assistant_id": a["assistant_id"],
        "name": a["name"],
        "description": a["description"],
        "version": a["version"],
        "price_e8s": int(a["price_e8s"]),
        "pricing_summary": a.get("pricing_summary", ""),
        "icon": a["icon"],
        "categories": a["categories"],
        "runtime": a.get("runtime", ""),
        "endpoint_url": a.get("endpoint_url", ""),
        "base_model": a.get("base_model", ""),
        "requested_role": a.get("requested_role", ""),
        "requested_permissions": a.get("requested_permissions", ""),
        "domains": a.get("domains", ""),
        "languages": a.get("languages", ""),
        "training_data_summary": a.get("training_data_summary", ""),
        "eval_report_url": a.get("eval_report_url", ""),
        "file_registry_canister_id": a["file_registry_canister_id"],
        "file_registry_namespace": a["file_registry_namespace"],
    }


@update
def create_assistant(a: AssistantInput) -> GenericResult:
    try:
        caller = str(ic.caller())
        r = create_assistant_impl(developer=caller, **_assistant_input_to_kwargs(a))
        return {"Ok": f"{r.get('action', 'ok')}:{r.get('assistant_id', a['assistant_id'])}"} if r["success"] else {"Err": r["error"]}
    except Exception as e:
        logger.error(f"create_assistant: {e}\n{traceback.format_exc()}")
        return {"Err": str(e)}


@update
def update_assistant(a: AssistantInput) -> GenericResult:
    return create_assistant(a)


@update
def delist_assistant(assistant_id: text) -> GenericResult:
    try:
        caller = str(ic.caller())
        r = delist_assistant_impl(caller, assistant_id)
        return {"Ok": r["message"]} if r["success"] else {"Err": r["error"]}
    except Exception as e:
        return {"Err": str(e)}


@query
def get_assistant_details(assistant_id: text) -> AssistantResult:
    try:
        r = get_assistant_details_impl(assistant_id)
        return {"Ok": _assistant_listing_record(r["assistant"])} if r["success"] else {"Err": r["error"]}
    except Exception as e:
        return {"Err": str(e)}


@query
def list_marketplace_assistants(page: nat64, per_page: nat64, verified_only: bool) -> AssistantListResult:
    try:
        r = list_assistants_impl(int(page), int(per_page), bool(verified_only))
        return AssistantListResult(
            listings=[_assistant_listing_record(d) for d in r["listings"]],
            total_count=int(r["total_count"]),
            page=int(r["page"]),
            per_page=int(r["per_page"]),
        )
    except Exception as e:
        logger.error(f"list_marketplace_assistants: {e}")
        return AssistantListResult(listings=[], total_count=int(0), page=page, per_page=per_page)


@query
def search_assistants(query_text: text, verified_only: bool) -> Vec[AssistantListing]:
    try:
        return [_assistant_listing_record(d) for d in search_assistants_impl(query_text, bool(verified_only))]
    except Exception as e:
        logger.error(f"search_assistants: {e}")
        return []


@query
def get_my_assistants() -> Vec[AssistantListing]:
    try:
        caller = str(ic.caller())
        return [_assistant_listing_record(d) for d in get_developer_assistants(caller)]
    except Exception as e:
        logger.error(f"get_my_assistants: {e}")
        return []


# ===========================================================================
# Purchases
# ===========================================================================


@update
def buy_extension(extension_id: text) -> GenericResult:
    try:
        caller = str(ic.caller())
        r = buy_extension_impl(caller, extension_id)
        return {"Ok": r["purchase_id"]} if r["success"] else {"Err": r["error"]}
    except Exception as e:
        return {"Err": str(e)}


@update
def buy_codex(codex_id: text) -> GenericResult:
    try:
        caller = str(ic.caller())
        r = buy_codex_impl(caller, codex_id)
        return {"Ok": r["purchase_id"]} if r["success"] else {"Err": r["error"]}
    except Exception as e:
        return {"Err": str(e)}


@update
def buy_assistant(assistant_id: text) -> GenericResult:
    """Record that the caller (typically a realm) hired this assistant.

    The actual realm-side runtime install runs in the separate
    ``assistant_runner`` extension + ``hire_assistant`` codex, and
    fires after the realm's governance vote on the hire passes.
    """
    try:
        caller = str(ic.caller())
        r = buy_assistant_impl(caller, assistant_id)
        return {"Ok": r["purchase_id"]} if r["success"] else {"Err": r["error"]}
    except Exception as e:
        return {"Err": str(e)}


@query
def has_purchased_extension(realm: text, extension_id: text) -> bool:
    try:
        return bool(has_purchased_extension_impl(realm, extension_id))
    except Exception:
        return False


@query
def has_purchased_codex(realm: text, codex_id: text) -> bool:
    try:
        return bool(has_purchased_codex_impl(realm, codex_id))
    except Exception:
        return False


@query
def has_purchased_assistant(realm: text, assistant_id: text) -> bool:
    try:
        return bool(has_purchased_assistant_impl(realm, assistant_id))
    except Exception:
        return False


@query
def get_my_purchases() -> Vec[PurchaseRecord]:
    try:
        caller = str(ic.caller())
        rows = get_my_purchases_impl(caller)
        return [PurchaseRecord(
            purchase_id=r["purchase_id"],
            realm_principal=caller,
            item_kind=r["item_kind"],
            item_id=r["item_id"],
            developer=r["developer"],
            price_paid_e8s=int(r["price_paid_e8s"]),
            purchased_at=float(r["purchased_at"]),
        ) for r in rows]
    except Exception as e:
        logger.error(f"get_my_purchases: {e}")
        return []


# ===========================================================================
# Likes
# ===========================================================================


@update
def like_item(item_kind: text, item_id: text) -> GenericResult:
    try:
        caller = str(ic.caller())
        r = like_item_impl(caller, item_kind, item_id)
        return {"Ok": r.get("action", "ok")} if r["success"] else {"Err": r["error"]}
    except Exception as e:
        return {"Err": str(e)}


@update
def unlike_item(item_kind: text, item_id: text) -> GenericResult:
    try:
        caller = str(ic.caller())
        r = unlike_item_impl(caller, item_kind, item_id)
        return {"Ok": r.get("action", "ok")} if r["success"] else {"Err": r["error"]}
    except Exception as e:
        return {"Err": str(e)}


@query
def has_liked(principal: text, item_kind: text, item_id: text) -> bool:
    try:
        return bool(has_liked_impl(principal, item_kind, item_id))
    except Exception:
        return False


@query
def my_likes() -> Vec[LikeRecord]:
    try:
        caller = str(ic.caller())
        return [
            LikeRecord(
                item_kind=r["item_kind"],
                item_id=r["item_id"],
                created_at=float(r["created_at"]),
            )
            for r in my_likes_impl(caller)
        ]
    except Exception as e:
        logger.error(f"my_likes: {e}")
        return []


@update
def recount_listing_likes() -> GenericResult:
    try:
        if not bool(ic.is_controller(ic.caller())):
            return {"Err": "Unauthorized: controller-only"}
        r = recount_listing_likes_impl()
        return {"Ok": json.dumps({"items": r["items"]})} if r["success"] else {"Err": "failed"}
    except Exception as e:
        return {"Err": str(e)}


# ===========================================================================
# Rankings
# ===========================================================================


@query
def top_extensions_by_downloads(n: nat64, verified_only: bool) -> Vec[ExtensionListing]:
    try:
        return [_ext_listing_record(d) for d in top_extensions_by_downloads_impl(int(n), bool(verified_only))]
    except Exception as e:
        logger.error(f"top_extensions_by_downloads: {e}")
        return []


@query
def top_extensions_by_likes(n: nat64, verified_only: bool) -> Vec[ExtensionListing]:
    try:
        return [_ext_listing_record(d) for d in top_extensions_by_likes_impl(int(n), bool(verified_only))]
    except Exception as e:
        logger.error(f"top_extensions_by_likes: {e}")
        return []


@query
def top_codices_by_downloads(n: nat64, verified_only: bool) -> Vec[CodexListing]:
    try:
        return [_codex_listing_record(d) for d in top_codices_by_downloads_impl(int(n), bool(verified_only))]
    except Exception as e:
        logger.error(f"top_codices_by_downloads: {e}")
        return []


@query
def top_codices_by_likes(n: nat64, verified_only: bool) -> Vec[CodexListing]:
    try:
        return [_codex_listing_record(d) for d in top_codices_by_likes_impl(int(n), bool(verified_only))]
    except Exception as e:
        logger.error(f"top_codices_by_likes: {e}")
        return []


@query
def top_assistants_by_downloads(n: nat64, verified_only: bool) -> Vec[AssistantListing]:
    try:
        return [_assistant_listing_record(d) for d in top_assistants_by_downloads_impl(int(n), bool(verified_only))]
    except Exception as e:
        logger.error(f"top_assistants_by_downloads: {e}")
        return []


@query
def top_assistants_by_likes(n: nat64, verified_only: bool) -> Vec[AssistantListing]:
    try:
        return [_assistant_listing_record(d) for d in top_assistants_by_likes_impl(int(n), bool(verified_only))]
    except Exception as e:
        logger.error(f"top_assistants_by_likes: {e}")
        return []


# ===========================================================================
# Licenses
# ===========================================================================


@query
def check_license(principal: text) -> LicenseResult:
    try:
        r = check_license_impl(principal)
        return {"Ok": _license_record(r["license"])} if r["success"] else {"Err": r["error"]}
    except Exception as e:
        return {"Err": str(e)}


@query
def get_license_status() -> LicenseResult:
    try:
        caller = str(ic.caller())
        r = check_license_impl(caller)
        return {"Ok": _license_record(r["license"])} if r["success"] else {"Err": r["error"]}
    except Exception as e:
        return {"Err": str(e)}


@update
def record_license_payment(payment: LicensePaymentInput) -> GenericResult:
    try:
        r = record_license_payment_impl(
            principal=payment["principal"],
            stripe_session_id=payment.get("stripe_session_id", ""),
            amount_usd_cents=int(payment.get("amount_usd_cents", 0)),
            duration_seconds=int(payment["duration_seconds"]),
            payment_method=payment.get("payment_method", "stripe"),
            note=payment.get("note", ""),
        )
        return {"Ok": r.get("action", "ok")} if r["success"] else {"Err": r["error"]}
    except Exception as e:
        logger.error(f"record_license_payment: {e}\n{traceback.format_exc()}")
        return {"Err": str(e)}


@update
def grant_manual_license(principal: text, duration_seconds: nat64, note: text) -> GenericResult:
    try:
        r = grant_manual_license_impl(principal=principal, duration_seconds=int(duration_seconds), note=note)
        return {"Ok": r.get("action", "ok")} if r["success"] else {"Err": r["error"]}
    except Exception as e:
        return {"Err": str(e)}


@update
def admin_grant_publisher(args: text) -> text:
    """License (and optionally appoint as reviewer) a principal, from a JSON
    argument, so a Casals sheet config row can do it through the conductor
    (the controller; a human is not). Args (JSON): {"principal",
    "duration_seconds", "note", "reviewer"}. Controller only."""
    try:
        return json.dumps(grant_publisher_from_json_impl(args))
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


@update
def admin_set_developer_name(args: text) -> text:
    """Give a developer principal a public display name, from a JSON argument
    {"principal", "name"} (empty name removes it), so a Casals sheet config row
    can set it through the conductor. Controller only."""
    try:
        return json.dumps(set_developer_name_from_json_impl(args))
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


@update
def transfer_listing(item_kind: text, item_id: text, new_developer: text) -> GenericResult:
    """Hand a listing (item_kind ext | codex | assistant) to another principal.
    Current owner or a controller only; version and review state are kept."""
    try:
        r = transfer_listing_impl(str(ic.caller()), item_kind, item_id, new_developer)
        return {"Ok": r["action"]} if r["success"] else {"Err": r["error"]}
    except Exception as e:
        return {"Err": str(e)}


@query
def admin_status() -> text:
    """{"reviewers": [...], "licensed": [...]} as JSON, for a sheet's `converged_when`."""
    try:
        return json.dumps(publishing_status_impl())
    except Exception as e:
        return json.dumps({"error": str(e)})


@update
def revoke_license(principal: text) -> GenericResult:
    try:
        r = revoke_license_impl(principal)
        return {"Ok": "revoked"} if r["success"] else {"Err": r["error"]}
    except Exception as e:
        return {"Err": str(e)}


# ===========================================================================
# Verification
# ===========================================================================


@update
def request_audit(item_kind: text, item_id: text) -> GenericResult:
    try:
        caller = str(ic.caller())
        r = request_audit_impl(caller=caller, item_kind=item_kind, item_id=item_id)
        return {"Ok": r["verification_status"]} if r["success"] else {"Err": r["error"]}
    except Exception as e:
        return {"Err": str(e)}


@update
def set_verification_status(item_kind: text, item_id: text, status: text, notes: text) -> GenericResult:
    try:
        r = set_verification_status_impl(
            item_kind=item_kind, item_id=item_id, status=status, notes=notes,
        )
        return {"Ok": r["verification_status"]} if r["success"] else {"Err": r["error"]}
    except Exception as e:
        return {"Err": str(e)}


@query
def list_pending_audits() -> Vec[PendingAudit]:
    try:
        return [
            PendingAudit(
                item_kind=r["item_kind"],
                item_id=r["item_id"],
                name=r["name"],
                developer=r["developer"],
                version=r["version"],
                updated_at=float(r["updated_at"]),
            )
            for r in list_pending_audits_impl()
        ]
    except Exception as e:
        logger.error(f"list_pending_audits: {e}")
        return []


@query
def greet(name: str) -> str:
    return f"Hello from Marketplace, {name}!"


# ---------------------------------------------------------------------------
# Incoming HTTP (GET /version) — gos-as-a-service#39
# Types match the shapes already declared in marketplace_backend.did
# (Header / HttpRequest / HttpResponseIncoming).
# ---------------------------------------------------------------------------

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
    streaming_strategy: Opt[str]
    upgrade: Opt[bool]


@query
def http_request(req: HttpRequest) -> HttpResponseIncoming:
    """Signal the gateway to upgrade so /version can be certified."""
    return http_request_upgrade_signal()


@update
def http_request_update(req: HttpRequest) -> HttpResponseIncoming:
    """Serve GET /version; OPTIONS → 204; anything else → 404 JSON."""
    return version_http_response(req)
