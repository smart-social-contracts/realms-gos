"""In-realm setup wizard API (issue #8 / GaaS setup flow)."""

from __future__ import annotations

import base64
import json
from typing import Any, Dict, Optional

from _cdk import Async, CallResult, Principal, StableBTreeMap, ic
from ic_python_logging import get_logger

from api.file_registry import (
    AssetCanisterService,
    FileRegistryService,
    _pin_frontend_prefix,
    _unwrap_call_result,
)
from core.setup import (
    BRANDING_DATA_URL_MAX_BYTES,
    SETUP_LAUNCH_STEP_CODE,
    SETUP_LAUNCH_TASK_NAME,
    SETUP_LAUNCH_TICK_SECONDS,
    advance_setup_launch,
    begin_setup_launch,
    draft_for_response,
    get_realm_registry_canister_id,
    get_setup_config,
    get_setup_draft,
    get_setup_state_payload,
    get_launch_state,
    validate_draft_for_launch,
    is_setup_stage,
    launch_state_for_response,
    _next_pending_launch_step,
    merge_setup_draft,
    notify_registry_setup_completed,
    require_setup_authorized,
    set_realm_registry_canister_id,
    update_setup_config,
    validate_branding_payload,
    validate_identity_payload,
)
from ggg.governance.realm import RealmStatus

logger = get_logger("api.setup")

# Durable catalog cache (separate from Realm.manifest_data, which is capped at
# 4096 chars). memory_id=2 avoids colliding with ic_python_db storage (id=1).
_SETUP_CATALOG_CACHE = StableBTreeMap[str, str](
    memory_id=2, max_key_size=64, max_value_size=262_144
)
_SETUP_CATALOG_CACHE_KEY = "catalog"

# Draft wizard images (logo/background data URLs). memory_id=3 is unused elsewhere.
# The value is the data URL itself, so the bound must match what validation admits.
_SETUP_DRAFT_ASSETS = StableBTreeMap[str, str](
    memory_id=3, max_key_size=32, max_value_size=BRANDING_DATA_URL_MAX_BYTES
)
_DRAFT_ASSET_KEYS = frozenset({"logo", "background", "live_logo", "live_background"})
_LIVE_ASSET_KEYS = {"logo": "live_logo", "background": "live_background"}
_LIVE_ASSET_PATHS = {
    "logo": "/custom/logo.png",
    "background": "/custom/background.png",
}
_SETUP_HIDDEN_CODICES = frozenset({"common", "westminster", "_common"})


def _visible_setup_codices(codices: Any) -> list:
    if not isinstance(codices, list):
        return []
    visible = []
    for item in codices:
        if not isinstance(item, dict):
            continue
        codex_id = str(item.get("id") or item.get("codex_id") or "")
        if not codex_id or codex_id in _SETUP_HIDDEN_CODICES:
            continue
        visible.append(item)
    return visible
_BRANDING_ASSET_PATHS = {
    "logo": "/custom/logo.png",
    "background": "/custom/background.png",
}


def _read_catalog_cache() -> Optional[Dict[str, Any]]:
    raw = _SETUP_CATALOG_CACHE.get(_SETUP_CATALOG_CACHE_KEY)
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_catalog_cache(envelope: Dict[str, Any]) -> None:
    payload = dict(envelope)
    payload["fetched_at"] = ic.time()
    _SETUP_CATALOG_CACHE.insert(_SETUP_CATALOG_CACHE_KEY, json.dumps(payload))


def get_available_codices_cached() -> str:
    cached = _read_catalog_cache()
    if not cached or not cached.get("codices"):
        return json.dumps({"success": False, "error": "empty"})
    return json.dumps({"success": True, "codices": _visible_setup_codices(cached.get("codices"))})


def get_setup_state() -> str:
    return json.dumps(get_setup_state_payload())


def get_setup_launch_status() -> str:
    realm = _load_realm()
    if not realm:
        return json.dumps({"success": False, "error": "Realm not found"})
    return json.dumps({"success": True, "launch": launch_state_for_response(realm)})


def _load_realm():
    from ggg import Realm

    return Realm.load("1")


def _store_draft_asset(kind: str, data_url: str) -> int:
    _SETUP_DRAFT_ASSETS.insert(kind, data_url)
    return len(data_url.encode("utf-8"))


def _get_draft_asset(kind: str) -> Optional[str]:
    if kind not in _DRAFT_ASSET_KEYS:
        return None
    return _SETUP_DRAFT_ASSETS.get(kind)


def stage_live_branding_asset(kind: str, data_url: str) -> Optional[str]:
    """Validate and stage a logo/background for a later ``/custom/*`` upload.

    Returns an error string, or None on success. Does not write the public
    asset yet — ``upload_live_branding_assets`` does that on apply/replay.
    """
    kind = (kind or "").strip()
    if kind not in _LIVE_ASSET_KEYS:
        return "kind must be logo or background"
    payload = {f"{kind}_data_url": data_url}
    branding_err = validate_branding_payload(payload)
    if branding_err:
        return branding_err
    try:
        content, _content_type = _decode_data_url(data_url)
    except Exception as exc:
        return f"invalid data URL: {exc}"
    if not content:
        return "empty file"
    _store_draft_asset(_LIVE_ASSET_KEYS[kind], data_url)
    return None


def upload_live_branding_assets(kinds) -> Async[dict]:
    """Write staged live logo/background bytes onto the frontend asset canister."""
    from ggg import Realm

    realm = Realm.load("1")
    frontend_id = (getattr(realm, "frontend_canister_id", "") or "").strip() if realm else ""
    if not frontend_id:
        return {"success": False, "error": "frontend_canister_id not configured"}

    asset = AssetCanisterService(Principal.from_str(frontend_id))
    yield from _pin_frontend_prefix(Principal.from_str(frontend_id), "/custom/")
    uploaded = []
    errors: Dict[str, str] = {}
    for kind in kinds:
        store_key = _LIVE_ASSET_KEYS.get(kind)
        asset_path = _LIVE_ASSET_PATHS.get(kind)
        if not store_key or not asset_path:
            continue
        data_url = _get_draft_asset(store_key)
        if not data_url:
            errors[kind] = "staged asset missing"
            continue
        try:
            content, content_type = _decode_data_url(data_url)
        except Exception as exc:
            errors[kind] = f"decode failed: {exc}"
            continue
        if not content:
            errors[kind] = "empty file"
            continue
        try:
            store_res: CallResult = yield asset.store(
                {
                    "key": asset_path,
                    "content_type": content_type,
                    "content_encoding": "identity",
                    "content": content,
                    "sha256": None,
                }
            )
            if isinstance(store_res, dict) and "Err" in store_res:
                errors[kind] = f"store failed: {store_res['Err']}"
            else:
                uploaded.append(kind)
        except Exception as exc:
            errors[kind] = f"store exception: {exc}"

    if errors and not uploaded:
        return {"success": False, "error": "; ".join(f"{k}: {v}" for k, v in errors.items())}

    version = str(ic.time() // 1_000_000_000)
    return {
        "success": len(errors) == 0,
        "uploaded": uploaded,
        "errors": errors or None,
        "version": version,
    }


def get_setup_draft_asset(kind: str) -> str:
    auth_err = require_setup_authorized()
    if auth_err:
        return json.dumps(auth_err)

    kind = (kind or "").strip()
    if kind not in ("logo", "background"):
        return json.dumps({"success": False, "error": "kind must be logo or background"})

    data_url = _get_draft_asset(kind)
    if not data_url:
        return json.dumps({"success": False, "error": f"no draft asset for {kind}"})
    return json.dumps({"success": True, "kind": kind, "data_url": data_url})


def setup_save_draft(args_json: str) -> Async[str]:
    auth_err = require_setup_authorized()
    if auth_err:
        return json.dumps(auth_err)

    try:
        params = json.loads(args_json) if args_json else {}
    except json.JSONDecodeError:
        return json.dumps({"success": False, "error": "Invalid JSON"})

    if not isinstance(params, dict):
        return json.dumps({"success": False, "error": "payload must be an object"})

    branding_in = params.get("branding")
    if branding_in is not None:
        if not isinstance(branding_in, dict):
            return json.dumps({"success": False, "error": "branding must be an object"})
        branding_err = validate_branding_payload(branding_in)
        if branding_err:
            return json.dumps({"success": False, "error": branding_err})

    identity_in = params.get("identity")
    if identity_in is not None:
        if not isinstance(identity_in, dict):
            return json.dumps({"success": False, "error": "identity must be an object"})
        identity_err = validate_identity_payload(identity_in)
        if identity_err:
            return json.dumps({"success": False, "error": identity_err})

    languages_in = params.get("languages")
    if languages_in is not None:
        if not isinstance(languages_in, dict):
            return json.dumps({"success": False, "error": "languages must be an object"})
        from core.realm_locales import normalize_languages

        _langs, _primary, lang_err = normalize_languages(
            languages_in.get("languages"),
            languages_in.get("primary_language"),
            require_primary=True,
        )
        if lang_err:
            return json.dumps({"success": False, "error": lang_err})

    realm = _load_realm()
    if not realm:
        return json.dumps({"success": False, "error": "Realm not found"})

    if "token" in params:
        params["token"] = _complete_catalog_token_draft(params.get("token"))

    partial = {
        k: params[k]
        for k in ("step", "codex", "token", "identity", "languages")
        if k in params
    }
    branding_markers: Dict[str, Any] = {}
    if isinstance(branding_in, dict):
        for asset_key, url_key in (
            ("logo", "logo_data_url"),
            ("background", "background_data_url"),
        ):
            data_url = branding_in.get(url_key)
            if data_url is None:
                continue
            size = _store_draft_asset(asset_key, data_url)
            branding_markers[asset_key] = True
            branding_markers[f"{asset_key}_size"] = size
        colors = branding_in.get("colors")
        if isinstance(colors, dict):
            branding_markers["colors"] = colors
        if branding_markers:
            partial["branding"] = branding_markers

    try:
        draft = merge_setup_draft(realm, partial)
    except ValueError as exc:
        return json.dumps({"success": False, "error": str(exc)})

    languages_to_persist = languages_in if isinstance(languages_in, dict) else None
    if languages_to_persist is None and isinstance(identity_in, dict):
        if "languages" in identity_in or "primary_language" in identity_in:
            languages_to_persist = {
                key: identity_in[key]
                for key in ("languages", "primary_language")
                if key in identity_in
            }
    if languages_to_persist:
        persist_err = _persist_realm_languages(realm, languages_to_persist)
        if persist_err:
            return json.dumps({"success": False, "error": persist_err})

    # The treasury ledger is applied by the background launch phase. Doing it
    # here makes this update slow enough that the sync call certificate can
    # move to ``done`` and drop the reply before the browser reads it.
    return json.dumps({"success": True, "draft": draft_for_response(draft)})


def _draft_token_unapplied_error() -> dict:
    """Hard error when draft.token has a symbol/ledger that cannot be applied.

    Must not use the Settings treasury message — that paints the fossil row.
    """
    return {
        "success": False,
        "error": "Could not apply treasury ledger from draft token",
        "error_code": "draft_token_unapplied",
    }


def _apply_persisted_draft_token(realm, token_value: Any) -> Async[Optional[dict]]:
    """Apply a just-persisted draft.token when it has a resolvable ledger.

    Catalog symbols (ckEURC → pe5t5) are completed first. Explicit skip/null
    returns None and does not invent REALMS.
    """
    completed = _complete_catalog_token_draft(token_value)
    token = _token_record(completed)
    if token is None:
        return None
    token_canister_id = (token.get("token_canister_id") or "").strip()
    if not token_canister_id:
        catalog = _catalog_token_for_symbol(str(token.get("symbol") or ""))
        if catalog:
            token_canister_id = (catalog.get("ledger") or "").strip()
    if not token_canister_id:
        return None
    result = yield from _apply_configured_token(
        realm,
        {
            "token_canister_id": token_canister_id,
            "symbol": token.get("symbol"),
            "decimals": token.get("decimals"),
            "indexer_canister_id": token.get("indexer_canister_id"),
            "token_type": token.get("token_type"),
        },
    )
    return result


def _apply_draft_token_now(realm) -> Async[Optional[dict]]:
    """Write realm.token_canister_id from draft now. Does not wait for the tick.

    Returns None only when draft.token is null/empty (launch stays fail-closed).
    If draft.token has a symbol or ledger and apply cannot run, return a hard
    error — do not proceed to a tick that paints the Settings fossil.
    """
    draft = dict(get_setup_draft(realm))
    draft["token"] = _complete_catalog_token_draft(draft.get("token"))
    token = _token_record(draft.get("token"))
    token_canister_id = _configured_token_canister_id(realm, draft)
    if not token_canister_id:
        if token is not None:
            return _draft_token_unapplied_error()
        return None
    result = yield from _apply_configured_token(
        realm,
        {
            "token_canister_id": token_canister_id,
            "symbol": token.get("symbol") if token else None,
            "decimals": token.get("decimals") if token else None,
            "indexer_canister_id": token.get("indexer_canister_id") if token else None,
            "token_type": token.get("token_type") if token else None,
        },
    )
    return result


def setup_apply_draft_token() -> Async[str]:
    """Leftover-unshadowed apply: draft.token → realm ledger. New Candid name."""
    auth_err = require_setup_authorized()
    if auth_err:
        return json.dumps(auth_err)

    realm = _load_realm()
    if not realm:
        return json.dumps({"success": False, "error": "Realm not found"})

    result = yield from _apply_draft_token_now(realm)
    if result is None:
        return json.dumps({"success": False, "error": "token_canister_id is required"})
    return json.dumps(result)


def setup_launch() -> str:
    """Start background initialization and return immediately.

    Ledger calls and phase work run on the launch timer. This HTTP call only
    records ``launch.status = running`` so the page can poll.
    """
    auth_err = require_setup_authorized()
    if auth_err:
        return json.dumps(auth_err)

    realm = _load_realm()
    if not realm:
        return json.dumps({"success": False, "error": "Realm not found"})

    draft = get_setup_draft(realm)
    codex_err = validate_draft_for_launch(draft)
    if codex_err:
        return json.dumps({"success": False, "error": codex_err})

    launch = get_launch_state(realm)
    resuming = any(
        step.get("status") == "failed" for step in (launch.get("steps") or [])
    )
    token = _token_record(_complete_catalog_token_draft(draft.get("token")))
    has_token = token is not None and (
        (token.get("token_canister_id") or "").strip() or (token.get("symbol") or "").strip()
    )
    if not resuming and not has_token:
        return json.dumps(_treasury_token_refused())

    err = begin_setup_launch(realm)
    if err:
        return json.dumps(err)

    from core.quarter_bootstrap import seed_recurring_codex_task

    try:
        seed_recurring_codex_task(
            SETUP_LAUNCH_TASK_NAME,
            SETUP_LAUNCH_STEP_CODE,
            SETUP_LAUNCH_TICK_SECONDS,
        )
    except Exception as seed_err:
        logger.warning("setup_launch: seed_recurring_codex_task failed: %s", seed_err)

    return json.dumps({"success": True, "launch": get_launch_state(realm)})


def _decode_data_url(data_url: str) -> tuple[bytes, str]:
    # Parsed by hand rather than with `re`: the canister's WASI CPython has no
    # working `re`, and basilisk's preamble silently substitutes an empty stub
    # for missing stdlib modules, so `re.compile` would fail at import time and
    # take this whole module down.
    raw = (data_url or "").strip()
    header, sep, payload = raw.partition(",")
    if not raw.startswith("data:") or not sep or not payload:
        raise ValueError("invalid data URL")
    meta = header[len("data:") :]
    if not meta.endswith(";base64"):
        raise ValueError("invalid data URL")
    content_type = meta[: -len(";base64")]
    if not content_type or ";" in content_type:
        raise ValueError("invalid data URL")
    return base64.b64decode(payload), content_type


def _drive_phase_outcome(outcome):
    """Drive a phase that may be a generator or a plain dict.

    ``yield from`` on a dict iterates its keys and raises TypeError
    (``dict_keyiterator``) — configure_token is sync and must not be
    yielded from directly.
    """
    if hasattr(outcome, "send"):
        return (yield from outcome)
    return outcome


def run_setup_launch_phase(realm, phase_name: str) -> Async[dict]:
    draft = get_setup_draft(realm)
    if phase_name == "install_codex":
        outcome = _launch_phase_install_codex(realm, draft)
    elif phase_name == "configure_token":
        draft = dict(draft)
        draft["token"] = _complete_catalog_token_draft(draft.get("token"))
        outcome = _launch_phase_configure_token(realm, draft)
    elif phase_name == "upload_branding":
        outcome = _launch_phase_upload_branding(realm, draft)
    elif phase_name == "apply_identity":
        outcome = _launch_phase_apply_identity(realm, draft)
    elif phase_name == "complete":
        outcome = _launch_phase_complete(realm, draft)
    else:
        return {"success": False, "error": f"unknown launch phase: {phase_name}"}
    return (yield from _drive_phase_outcome(outcome))


def _launch_phase_install_codex(realm, draft: dict) -> Async[dict]:
    codex = draft.get("codex") or {}
    package = (codex.get("package") or codex.get("codex_id") or "").strip()
    version = codex.get("version")
    if version is not None:
        version = str(version).strip() or None
    extra_params = codex.get("params")
    if not package:
        return {"success": False, "error": "draft codex package is required"}

    existing = get_setup_config(realm).get("codex") or {}
    if (
        isinstance(existing, dict)
        and (existing.get("package") or "").strip() == package
        and (existing.get("version") or "").strip() == (version or "").strip()
        and existing.get("version")
    ):
        return {"success": True, "skipped": True, "codex": existing}

    from core.fleet_registry import resolve_catalog_registry_id

    registry_id = yield from resolve_catalog_registry_id(realm)
    if not registry_id:
        return {"success": False, "error": "file_registry_canister_id not configured"}

    from api.file_registry import install_codex_from_registry as _install

    frontend_id = (getattr(realm, "frontend_canister_id", "") or "").strip() or None
    result_raw = yield from _install(
        registry_id,
        package,
        version,
        True,
        frontend_canister_id=frontend_id,
    )
    try:
        result = json.loads(result_raw) if isinstance(result_raw, str) else result_raw
    except json.JSONDecodeError:
        return {"success": False, "error": "Unexpected install response"}

    if not result.get("success"):
        return result

    if result.get("status") == "in_progress":
        return result

    if result.get("status") != "complete":
        return {"success": False, "error": "Unexpected install response"}

    resolved_version = (result.get("version") or version or "").strip()
    codex_record: Dict[str, Any] = {"package": package, "version": resolved_version}
    if isinstance(extra_params, dict) and extra_params:
        codex_record["params"] = extra_params
    update_setup_config(realm, {"codex": codex_record})
    return {"success": True, "codex": codex_record}


def _treasury_token_refused() -> dict:
    from core.realm_currency import no_treasury_token_error

    return {"success": False, **no_treasury_token_error()}


def _token_symbol_from_value(value: Any) -> str:
    """Pull a catalog symbol out of any realistic draft/applied token shape."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, dict):
        return ""
    for key in ("symbol", "id", "existing"):
        raw = value.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return ""


def _token_record(value: Any) -> Optional[dict]:
    """Normalize draft.token to a dict, or None if empty/null.

    Accepts a dict with ``symbol`` / ``id`` / ``existing`` / ``token_canister_id``,
    or a bare string such as ``"ckEURC"`` / ``"ckEURC "``. Empty/null returns
    None so Launch stays fail-closed (no invented REALMS).
    """
    if value is None:
        return None
    if isinstance(value, str):
        symbol = value.strip()
        return {"symbol": symbol} if symbol else None
    if not isinstance(value, dict):
        return None
    out = dict(value)
    if not (out.get("symbol") or "").strip():
        symbol = _token_symbol_from_value(out)
        if symbol:
            out["symbol"] = symbol
    has_ledger = bool((out.get("token_canister_id") or "").strip())
    has_symbol = bool((out.get("symbol") or "").strip())
    if not has_ledger and not has_symbol:
        return None
    return out


def _catalog_token_for_symbol(symbol: str) -> Optional[dict]:
    """Look up a shared-catalog token. Empty symbol does not invent REALMS."""
    symbol = (symbol or "").strip()
    if not symbol:
        return None
    try:
        from api.tokens import resolve_shared_token
    except ImportError:
        return None

    return resolve_shared_token(symbol)


def _complete_catalog_token_draft(token: Any) -> Any:
    """Coerce any catalog-shaped token into {symbol, ledger, decimals, indexer}.

    Strings, ``{id}``, ``{existing}``, and ``{symbol}`` all resolve through
    the catalog. ``None`` / empty stay ``None`` (explicit skip).
    """
    if token is None:
        return None
    record = _token_record(token)
    if record is None:
        return None
    completed = dict(record)
    if (completed.get("token_canister_id") or "").strip():
        return completed
    catalog = _catalog_token_for_symbol(str(completed.get("symbol") or ""))
    if not catalog:
        return completed
    ledger = (catalog.get("ledger") or "").strip()
    if not ledger:
        return completed
    completed["token_canister_id"] = ledger
    if completed.get("decimals") is None and catalog.get("decimals") is not None:
        completed["decimals"] = catalog["decimals"]
    if not (completed.get("indexer_canister_id") or "").strip() and catalog.get("indexer"):
        completed["indexer_canister_id"] = catalog["indexer"]
    return completed


def _configured_token_canister_id(realm, draft: dict) -> str:
    token = _token_record(draft.get("token"))
    if token:
        token_canister_id = (token.get("token_canister_id") or "").strip()
        if token_canister_id:
            return token_canister_id
    token_canister_id = (getattr(realm, "token_canister_id", "") or "").strip()
    if token_canister_id:
        return token_canister_id
    setup_token = _token_record(get_setup_config(realm).get("token"))
    if setup_token:
        token_canister_id = (setup_token.get("token_canister_id") or "").strip()
        if token_canister_id:
            return token_canister_id
    symbol = _token_symbol_from_value(token) or _token_symbol_from_value(setup_token)
    if not symbol:
        return ""
    catalog = _catalog_token_for_symbol(symbol)
    if not catalog:
        return ""
    return (catalog.get("ledger") or "").strip()


def _apply_configured_token(realm, params: dict) -> Async[dict]:
    """Resolve and persist a treasury ledger. No caller-auth check.

    The candid/UI entry point authorizes first. The launch-phase task runs as
    the canister (not the founder) and must apply a draft ledger without
    ``require_setup_authorized``.
    """
    token_canister_id = (params.get("token_canister_id") or "").strip()
    if not token_canister_id:
        return {"success": False, "error": "token_canister_id is required"}

    token_record = {
        "token_canister_id": token_canister_id,
        "symbol": (params.get("symbol") or "").strip(),
        "decimals": params.get("decimals"),
        "indexer_canister_id": (params.get("indexer_canister_id") or "").strip(),
        "token_type": (params.get("token_type") or "realm").strip() or "realm",
    }

    from api.tokens import register_treasury_token, resolve_ledger_token_info

    resolved = yield from resolve_ledger_token_info(token_canister_id)
    if not resolved.get("success"):
        return {
            "success": False,
            "error": resolved.get("error") or "Could not resolve ledger metadata",
            "error_code": "ledger_unresolvable",
        }

    sym = str(resolved["symbol"]).strip()
    decimals = int(resolved["decimals"])
    indexer = str(resolved.get("indexer_canister_id") or token_canister_id).strip()
    token_record["symbol"] = sym
    token_record["decimals"] = decimals
    token_record["indexer_canister_id"] = indexer

    realm.token_canister_id = token_canister_id
    realm.accounting_currency = sym[:16]
    realm.accounting_currency_decimals = decimals
    update_setup_config(realm, {"token": token_record})

    try:
        register_treasury_token(
            symbol=sym,
            ledger_canister_id=token_canister_id,
            indexer_canister_id=indexer,
            decimals=decimals,
            token_type=token_record["token_type"],
        )
    except Exception as token_err:
        logger.warning("setup_configure_token treasury registration failed: %s", token_err)

    return {"success": True, "token": token_record}


def _launch_phase_configure_token(realm, draft: dict) -> Async[dict]:
    from core.realm_currency import realm_currency

    draft = dict(draft or {})
    draft["token"] = _complete_catalog_token_draft(draft.get("token"))

    token_canister_id = _configured_token_canister_id(realm, draft)
    if not token_canister_id:
        return _treasury_token_refused()

    if realm_currency() and (getattr(realm, "token_canister_id", "") or "").strip() == token_canister_id:
        token_record = get_setup_config(realm).get("token")
        if isinstance(token_record, dict) and token_record.get("token_canister_id") == token_canister_id:
            return {"success": True, "skipped": True, "token": token_record}

    token = _token_record(draft.get("token")) or {}
    result = yield from _apply_configured_token(
        realm,
        {
            "token_canister_id": token_canister_id,
            "symbol": token.get("symbol"),
            "decimals": token.get("decimals"),
            "indexer_canister_id": token.get("indexer_canister_id"),
            "token_type": token.get("token_type"),
        },
    )
    return result


def _launch_phase_upload_branding(realm, draft: dict) -> Async[dict]:
    branding = draft.get("branding") or {}
    if not isinstance(branding, dict):
        return {"success": True, "skipped": True}

    frontend_id = (getattr(realm, "frontend_canister_id", "") or "").strip()
    if not frontend_id:
        return {"success": False, "error": "frontend_canister_id not configured"}

    uploaded = []
    errors: Dict[str, str] = {}
    asset = AssetCanisterService(Principal.from_str(frontend_id))
    yield from _pin_frontend_prefix(Principal.from_str(frontend_id), "/custom/")

    for kind, asset_key in _BRANDING_ASSET_PATHS.items():
        if not branding.get(kind):
            continue
        data_url = _get_draft_asset(kind)
        if not data_url:
            errors[kind] = "draft asset missing"
            continue
        try:
            content, content_type = _decode_data_url(data_url)
        except Exception as exc:
            errors[kind] = f"decode failed: {exc}"
            continue
        if not content:
            errors[kind] = "empty file"
            continue
        try:
            store_res: CallResult = yield asset.store(
                {
                    "key": asset_key,
                    "content_type": content_type,
                    "content_encoding": "identity",
                    "content": content,
                    "sha256": None,
                }
            )
            if isinstance(store_res, dict) and "Err" in store_res:
                errors[kind] = f"store failed: {store_res['Err']}"
            else:
                uploaded.append(asset_key)
        except Exception as exc:
            errors[kind] = f"store exception: {exc}"

    if errors and not uploaded:
        return {"success": False, "error": "; ".join(f"{k}: {v}" for k, v in errors.items())}

    colors = branding.get("colors")
    branding_record: Dict[str, Any] = {}
    if colors:
        branding_record["colors"] = colors
    for kind in _DRAFT_ASSET_KEYS:
        if branding.get(kind):
            branding_record[kind] = True
            size_key = f"{kind}_size"
            if branding.get(size_key) is not None:
                branding_record[size_key] = branding[size_key]
    if branding_record:
        update_setup_config(realm, {"branding": branding_record})

    return {
        "success": len(errors) == 0,
        "uploaded": uploaded,
        "errors": errors or None,
    }


def _persist_realm_languages(realm, payload: dict) -> Optional[str]:
    from core.realm_locales import apply_realm_languages

    _langs, _primary, error = apply_realm_languages(
        realm,
        payload.get("languages"),
        payload.get("primary_language"),
        replace_languages="languages" in payload,
    )
    return error


def _languages_payload_from_draft(draft: dict) -> Optional[dict]:
    languages = draft.get("languages")
    if isinstance(languages, dict) and (
        "languages" in languages or "primary_language" in languages
    ):
        return languages
    identity = draft.get("identity")
    if isinstance(identity, dict) and (
        "languages" in identity or "primary_language" in identity
    ):
        return {
            key: identity[key]
            for key in ("languages", "primary_language")
            if key in identity
        }
    return None


def _launch_phase_apply_identity(realm, draft: dict) -> dict:
    identity = draft.get("identity") or {}
    if not isinstance(identity, dict):
        identity = {}

    if "manifesto" in identity:
        realm.manifesto = identity["manifesto"] or ""
    if "welcome_message" in identity:
        realm.welcome_message = identity["welcome_message"] or ""

    languages_payload = _languages_payload_from_draft(draft)
    if languages_payload:
        persist_err = _persist_realm_languages(realm, languages_payload)
        if persist_err:
            return {"success": False, "error": persist_err}

    if not identity and not languages_payload:
        return {"success": True, "skipped": True}

    if identity:
        existing_identity = get_setup_config(realm).get("identity") or {}
        if isinstance(existing_identity, dict):
            merged_identity = dict(existing_identity)
            merged_identity.update(identity)
        else:
            merged_identity = identity
        update_setup_config(realm, {"identity": merged_identity})
        return {"success": True, "identity": merged_identity}

    return {"success": True, "identity": identity}


def _launch_phase_complete(realm, draft: dict) -> Async[dict]:
    from core.realm_currency import realm_currency
    from ggg.governance.realm import RealmStatus

    if not is_setup_stage(realm):
        return {"success": True, "skipped": True, "status": effective_realm_status(realm)}

    setup = get_setup_config(realm)
    codex = setup.get("codex") or draft.get("codex")
    if not isinstance(codex, dict) or not (codex.get("package") or codex.get("version")):
        return {"success": False, "error": "A codex must be installed before completing setup"}

    if not realm_currency():
        return _treasury_token_refused()

    identity = draft.get("identity")
    if isinstance(identity, dict):
        if "manifesto" in identity:
            realm.manifesto = identity["manifesto"] or ""
        if "welcome_message" in identity:
            realm.welcome_message = identity["welcome_message"] or ""

    languages_payload = _languages_payload_from_draft(draft)
    if languages_payload:
        persist_err = _persist_realm_languages(realm, languages_payload)
        if persist_err:
            return {"success": False, "error": persist_err}

    completed_at = str(ic.time())
    update_setup_config(
        realm,
        {
            "setup_completed_at": completed_at,
            "codex": codex,
        },
    )
    realm.status = RealmStatus.ALPHA

    registry_id = get_realm_registry_canister_id(realm)
    if registry_id:
        set_realm_registry_canister_id(realm, registry_id)
        yield from notify_registry_setup_completed(registry_id)

    return {
        "success": True,
        "status": RealmStatus.ALPHA,
        "setup_completed_at": completed_at,
        "registry_notified": bool(registry_id),
    }


def effective_realm_status(realm) -> str:
    from core.setup import effective_realm_status as _effective

    return _effective(realm)


def _may_refresh_fleet(caller: str) -> bool:
    from core.access import _check_access
    from ggg.system.user_profile import Operations

    return ic.is_controller(Principal.from_str(caller)) or _check_access(caller, Operations.REALM_ADMIN)


def setup_fleet(args_json: str) -> Async[str]:
    """Pinned fleet ids, discovered on first use. ``{"refresh": true}`` (admin) looks again."""
    try:
        params = json.loads(args_json) if args_json else {}
    except json.JSONDecodeError:
        return json.dumps({"success": False, "error": "Invalid JSON"})
    refresh = params.get("refresh") is True
    if refresh and not _may_refresh_fleet(ic.caller().to_str()):
        return json.dumps({"success": False, "error": "Access denied: refresh requires a realm admin"})

    realm = _load_realm()
    if not realm:
        return json.dumps({"success": False, "error": "Realm not found"})

    from core.fleet_registry import discover_fleet

    try:
        fleet = yield from discover_fleet(realm, refresh=refresh)
    except (ValueError, RuntimeError) as exc:
        return json.dumps({"success": False, "error": str(exc)})
    if refresh:
        _SETUP_CATALOG_CACHE.remove(_SETUP_CATALOG_CACHE_KEY)
    return json.dumps({"success": True, "fleet": fleet})


def list_available_codices() -> Async[str]:
    from ggg import Realm

    realm = Realm.load("1")
    if not realm:
        return json.dumps({"success": False, "error": "Realm not found"})

    from core.fleet_registry import resolve_catalog_registry_id

    try:
        registry_id = yield from resolve_catalog_registry_id(realm)
    except (ValueError, RuntimeError) as exc:
        return json.dumps({"success": False, "error": str(exc)})
    if not registry_id:
        return json.dumps(
            {"success": False, "error": "file_registry_canister_id not configured"}
        )

    registry = FileRegistryService(Principal.from_str(registry_id))
    codex_res: CallResult = yield registry.list_codices()
    raw = _unwrap_call_result(codex_res)
    entries = json.loads(raw) if raw else []

    catalog = []
    for entry in entries:
        codex_id = entry.get("codex_id") or entry.get("id") or ""
        if not codex_id or codex_id in _SETUP_HIDDEN_CODICES:
            continue
        versions = entry.get("versions") or []
        name = codex_id
        description = ""
        repository = ""
        latest = entry.get("latest") or (versions[-1] if versions else "")
        if latest:
            try:
                manifest_res: CallResult = yield registry.get_extension_manifest(
                    json.dumps({"ext_id": codex_id, "version": latest})
                )
                manifest = json.loads(_unwrap_call_result(manifest_res))
                if isinstance(manifest, dict) and not manifest.get("error"):
                    name = manifest.get("name") or name
                    description = manifest.get("description") or ""
                    repository = manifest.get("repository") or ""
            except Exception as manifest_err:
                logger.debug(
                    "Could not load manifest for codex %s@%s: %s",
                    codex_id,
                    latest,
                    manifest_err,
                )
        catalog.append(
            {
                "id": codex_id,
                "versions": versions,
                "name": name,
                "description": description,
                "repository": repository,
            }
        )

    envelope = {"success": True, "codices": catalog}
    _write_catalog_cache(envelope)
    return json.dumps(envelope)


def setup_install_codex(args_json: str) -> Async[str]:
    auth_err = require_setup_authorized()
    if auth_err:
        return json.dumps(auth_err)

    try:
        params = json.loads(args_json) if args_json else {}
    except json.JSONDecodeError:
        return json.dumps({"success": False, "error": "Invalid JSON"})

    package = (params.get("package") or params.get("codex_id") or "").strip()
    version = params.get("version")
    if version is not None:
        version = str(version).strip() or None
    extra_params = params.get("params")

    if not package:
        return json.dumps({"success": False, "error": "package is required"})

    from ggg import Realm

    realm = Realm.load("1")
    if not realm:
        return json.dumps({"success": False, "error": "Realm not found"})

    already: set[str] = set()
    try:
        from core.runtime_extensions import list_installed as list_ext

        already.update(list_ext())
    except Exception:
        pass
    try:
        from core.runtime_codex import list_installed as list_cdx

        already.update(list_cdx())
    except Exception:
        pass
    job_state = {}
    try:
        raw_job = getattr(realm, "codex_install_state", "") or ""
        if raw_job:
            parsed_job = json.loads(raw_job)
            if isinstance(parsed_job, dict):
                job_state = parsed_job
    except (json.JSONDecodeError, TypeError):
        job_state = {}
    job_in_progress = (job_state.get("ext_id") or "").strip() == package

    if package in already and not job_in_progress:
        resolved_version = (version or "installed").strip()
        extra_params = params.get("params")
        codex_record: Dict[str, Any] = {
            "package": package,
            "version": resolved_version,
        }
        if isinstance(extra_params, dict) and extra_params:
            codex_record["params"] = extra_params
        update_setup_config(realm, {"codex": codex_record})
        return json.dumps(
            {
                "success": True,
                "skipped": True,
                "setup_codex": codex_record,
            }
        )

    from core.fleet_registry import resolve_catalog_registry_id

    try:
        registry_id = yield from resolve_catalog_registry_id(realm)
    except (ValueError, RuntimeError) as exc:
        return json.dumps({"success": False, "error": str(exc)})
    if not registry_id:
        return json.dumps(
            {"success": False, "error": "file_registry_canister_id not configured"}
        )

    from api.file_registry import install_codex_from_registry as _install

    frontend_id = (getattr(realm, "frontend_canister_id", "") or "").strip() or None
    result_raw = yield from _install(
        registry_id,
        package,
        version,
        False,
        frontend_canister_id=frontend_id,
        install_dependencies=False,
    )
    try:
        result = json.loads(result_raw) if isinstance(result_raw, str) else result_raw
    except json.JSONDecodeError:
        return json.dumps({"success": False, "error": "Unexpected install response"})

    if not result.get("success"):
        return json.dumps(result)

    if result.get("status") == "in_progress":
        return json.dumps(result)

    resolved_version = (result.get("version") or version or "").strip()
    if result.get("status") != "complete":
        return json.dumps(result)

    from api.file_registry import run_codex_init as _run_init
    from _cdk import ic as _ic

    def _run_init_later():
        _run_init(package)

    _ic.set_timer(1, _run_init_later)

    codex_record: Dict[str, Any] = {
        "package": package,
        "version": resolved_version,
    }
    if isinstance(extra_params, dict) and extra_params:
        codex_record["params"] = extra_params

    update_setup_config(realm, {"codex": codex_record})
    result["setup_codex"] = codex_record
    return json.dumps(result)


def setup_configure_token(args_json: str) -> Async[str]:
    auth_err = require_setup_authorized()
    if auth_err:
        return json.dumps(auth_err)

    try:
        params = json.loads(args_json) if args_json else {}
    except json.JSONDecodeError:
        return json.dumps({"success": False, "error": "Invalid JSON"})

    token_canister_id = (params.get("token_canister_id") or "").strip()
    if not token_canister_id:
        return json.dumps({"success": False, "error": "token_canister_id is required"})

    # TODO(setup-wizard): support provisioning a new token canister from the wizard.
    from ggg import Realm

    realm = Realm.load("1")
    if not realm:
        return json.dumps({"success": False, "error": "Realm not found"})

    result = yield from _apply_configured_token(realm, params)
    return json.dumps(result)


def setup_set_branding(args_json: str) -> str:
    auth_err = require_setup_authorized()
    if auth_err:
        return json.dumps(auth_err)

    try:
        params = json.loads(args_json) if args_json else {}
    except json.JSONDecodeError:
        return json.dumps({"success": False, "error": "Invalid JSON"})

    branding = {
        key: params[key]
        for key in ("logo_data_url", "background_data_url", "colors")
        if key in params
    }
    identity = {
        key: params[key]
        for key in ("manifesto", "welcome_message")
        if key in params
    }
    if not branding and not identity:
        return json.dumps({"success": False, "error": "No branding fields provided"})

    if branding:
        branding_err = validate_branding_payload(branding)
        if branding_err:
            return json.dumps({"success": False, "error": branding_err})

    if identity:
        identity_err = validate_identity_payload(identity)
        if identity_err:
            return json.dumps({"success": False, "error": identity_err})

    from ggg import Realm

    realm = Realm.load("1")
    if not realm:
        return json.dumps({"success": False, "error": "Realm not found"})

    response: Dict[str, Any] = {"success": True}

    if identity:
        if "manifesto" in identity:
            realm.manifesto = identity["manifesto"] or ""
        if "welcome_message" in identity:
            realm.welcome_message = identity["welcome_message"] or ""

        existing_identity = get_setup_config(realm).get("identity") or {}
        if isinstance(existing_identity, dict):
            merged_identity = dict(existing_identity)
            merged_identity.update(identity)
        else:
            merged_identity = identity

        try:
            update_setup_config(realm, {"identity": merged_identity})
            response["identity"] = merged_identity
        except ValueError as exc:
            logger.warning(
                "setup_set_branding: could not persist identity to manifest_data: %s",
                exc,
            )
            response["identity"] = merged_identity

    if branding:
        existing = get_setup_config(realm).get("branding") or {}
        if isinstance(existing, dict):
            merged = dict(existing)
            merged.update(branding)
        else:
            merged = branding

        try:
            update_setup_config(realm, {"branding": merged})
            response["branding"] = merged
        except ValueError as exc:
            if identity:
                logger.warning(
                    "setup_set_branding: could not persist branding to manifest_data: %s",
                    exc,
                )
            else:
                return json.dumps({"success": False, "error": str(exc)})

    return json.dumps(response)


def complete_setup() -> Async[str]:
    auth_err = require_setup_authorized()
    if auth_err:
        return json.dumps(auth_err)

    from ggg import Realm

    realm = Realm.load("1")
    if not realm:
        return json.dumps({"success": False, "error": "Realm not found"})

    if not is_setup_stage(realm):
        return json.dumps(
            {"success": False, "error": "Realm is not in setup stage"}
        )

    setup = get_setup_config(realm)
    codex = setup.get("codex")
    if not isinstance(codex, dict) or not (codex.get("package") or codex.get("version")):
        return json.dumps({"success": False, "error": "A codex must be installed before completing setup"})

    from core.realm_currency import no_treasury_token_error, realm_currency

    if not realm_currency():
        return json.dumps({"success": False, **no_treasury_token_error()})

    identity = setup.get("identity")
    if isinstance(identity, dict):
        if "manifesto" in identity:
            realm.manifesto = identity["manifesto"] or ""
        if "welcome_message" in identity:
            realm.welcome_message = identity["welcome_message"] or ""

    completed_at = str(ic.time())
    update_setup_config(
        realm,
        {
            "setup_completed_at": completed_at,
            "codex": codex,
        },
    )
    realm.status = RealmStatus.ALPHA

    registry_id = get_realm_registry_canister_id(realm)
    if registry_id:
        set_realm_registry_canister_id(realm, registry_id)
        yield from notify_registry_setup_completed(registry_id)
    else:
        logger.warning(
            "complete_setup: no realm_registry_canister_id configured; skipping registry notify"
        )

    return json.dumps(
        {
            "success": True,
            "status": RealmStatus.ALPHA,
            "setup_completed_at": completed_at,
            "codex": codex,
            "registry_notified": bool(registry_id),
        }
    )
