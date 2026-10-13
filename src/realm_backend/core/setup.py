"""In-realm setup wizard helpers (issue #8 / GaaS setup flow)."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from _cdk import Async, ic
from ic_python_logging import get_logger

logger = get_logger("core.setup")

SETUP_ERROR = "This realm is being set up and is not yet open to members."
DEFAULT_PRIMARY_COLOR = "#3b82f6"
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
BRANDING_DATA_URL_MAX_BYTES = 1_572_864  # ~1.5 MiB per asset
MANIFESTO_MAX_CHARS = 256
WELCOME_MESSAGE_MAX_CHARS = 1024
SETUP_LAUNCH_TASK_NAME = "setup_launch"
SETUP_LAUNCH_TICK_SECONDS = 1
SETUP_LAUNCH_STALE_NANOS = 180 * 1_000_000_000
SETUP_DRAFT_STEPS = frozenset(
    {"welcome", "codex", "token", "branding", "languages", "review"}
)
SETUP_LAUNCH_PHASES: List[tuple[str, str]] = [
    ("install_codex", "Install codex"),
    ("configure_token", "Configure token"),
    ("upload_branding", "Upload branding"),
    ("apply_identity", "Apply identity"),
    ("complete", "Complete setup"),
]
SETUP_LAUNCH_STEP_CODE = (
    "def async_task():\n"
    "    from core.setup import advance_setup_launch\n"
    "    res = yield from advance_setup_launch()\n"
    "    return res\n"
)


def effective_realm_status(realm) -> str:
    return str(getattr(realm, "status", None) or "setup")


def is_setup_stage(realm) -> bool:
    return effective_realm_status(realm) == "setup"


def _load_manifest(realm) -> dict:
    try:
        return json.loads(getattr(realm, "manifest_data", "") or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}


def _save_manifest(realm, manifest: dict) -> None:
    serialized = json.dumps(manifest)
    if len(serialized) > 4096:
        raise ValueError(
            f"manifest_data would exceed 4096 chars ({len(serialized)})"
        )
    realm.manifest_data = serialized


def get_setup_config(realm) -> dict:
    return dict(_load_manifest(realm).get("setup") or {})


def get_gos_environment(realm) -> str:
    """Logical GaaS environment (``staging``, ``production``, …), not the IC network."""
    return (get_setup_config(realm).get("gos_environment") or "").strip().lower()


def set_gos_environment(realm, name: str) -> None:
    cleaned = (name or "").strip().lower()
    if cleaned:
        update_setup_config(realm, {"gos_environment": cleaned})


def update_setup_config(realm, updates: dict) -> dict:
    manifest = _load_manifest(realm)
    setup = dict(manifest.get("setup") or {})
    setup.update(updates)
    manifest["setup"] = setup
    _save_manifest(realm, manifest)
    return setup


def get_creator_principal(realm) -> str:
    setup = get_setup_config(realm)
    creator = (setup.get("creator_principal") or "").strip()
    if creator:
        return creator
    return (getattr(realm, "principal_id", "") or "").strip()


def set_creator_principal(realm, principal: str) -> None:
    principal = (principal or "").strip()
    if not principal:
        return
    update_setup_config(realm, {"creator_principal": principal})


def set_realm_registry_canister_id(realm, registry_id: str) -> None:
    registry_id = (registry_id or "").strip()
    if not registry_id:
        return
    update_setup_config(realm, {"realm_registry_canister_id": registry_id})


def can_enter_setup(caller: str, is_controller: bool = False) -> bool:
    """Whether the caller may run first-boot enter_setup: a controller (the
    installer is one, from the Casals stand template), the recorded installer,
    or a trusted principal. No baked-in list of installer ids."""
    caller = (caller or "").strip()
    if not caller:
        return False
    if is_controller:
        return True
    from ggg import Realm

    realm = Realm.load("1")
    if not realm:
        return False
    installer_id = str(getattr(realm, "installer_canister_id", "") or "").strip()
    if installer_id and installer_id == caller:
        return True
    trusted = [
        p.strip()
        for p in str(getattr(realm, "trusted_principals", "") or "").split(",")
        if p.strip()
    ]
    return caller in trusted


def record_installer_principal(installer_id: str) -> None:
    """Store the GaaS installer named in the realm's init argument.

    Sole hand-off leaves the stand baton as the only IC controller. The
    installer id is recorded here so later admin calls from that canister
    still pass. It is not added to trusted_principals.
    """
    from ggg import Realm

    installer_id = (installer_id or "").strip()
    if not installer_id:
        return
    realm = Realm.load("1")
    if not realm:
        return
    if not str(getattr(realm, "installer_canister_id", "") or "").strip():
        realm.installer_canister_id = installer_id


def record_bootstrap_caller(realm, caller: str) -> None:
    """Remember the first-boot installer so later @require(realm.admin) passes."""
    caller = (caller or "").strip()
    if not caller:
        return
    if not str(getattr(realm, "installer_canister_id", "") or "").strip():
        realm.installer_canister_id = caller
    trusted = [
        p.strip()
        for p in str(getattr(realm, "trusted_principals", "") or "").split(",")
        if p.strip()
    ]
    if caller not in trusted:
        trusted.append(caller)
        realm.trusted_principals = ",".join(trusted)


def enter_setup(
    creator: str, registry_id: str, environment: str = "", caller: str = ""
) -> dict:
    """Record founding creator and registry link when GOS enters in-realm setup."""
    from ggg import Realm

    realm = Realm.load("1")
    if not realm:
        return {"ok": False, "error": "realm not initialized"}

    setup = get_setup_config(realm)
    if setup.get("setup_completed_at") or effective_realm_status(realm) != "setup":
        return {"ok": False, "error": "setup already completed"}

    creator = (creator or "").strip()
    existing = (setup.get("creator_principal") or "").strip()
    if existing and existing != creator:
        return {"ok": False, "error": "setup already entered by another creator"}

    set_creator_principal(realm, creator)
    set_realm_registry_canister_id(realm, registry_id)
    network = (environment or "").strip()
    if network:
        realm.network = network
    if caller:
        record_bootstrap_caller(realm, caller)
    return {"ok": True}


def get_realm_registry_canister_id(realm) -> str:
    setup = get_setup_config(realm)
    stored = (setup.get("realm_registry_canister_id") or "").strip()
    if stored:
        return stored
    try:
        from api.upgrade import _get_registry_canister_id

        return (_get_registry_canister_id() or "").strip()
    except Exception:
        return ""


def is_setup_authorized(caller: str) -> bool:
    from ggg import Realm
    from ggg.system.user_profile import Operations

    from core.access import _check_access

    realm = Realm.load("1")
    if not realm or not is_setup_stage(realm):
        return True

    creator = get_creator_principal(realm)
    if creator and caller == creator:
        return True
    if _check_access(caller, Operations.REALM_ADMIN):
        return True
    return False


def require_setup_authorized() -> Optional[dict]:
    """Return an error payload when the caller may not run setup mutations."""
    caller = ic.caller().to_str()
    if is_setup_authorized(caller):
        return None
    return {
        "success": False,
        "error_code": "setup_forbidden",
        "error": "Access denied: setup wizard requires creator or realm admin",
    }


def setup_gate_error(caller: str) -> Optional[str]:
    """Block member/social mutations while the realm is in setup."""
    from ggg import Realm

    realm = Realm.load("1")
    if not realm or not is_setup_stage(realm):
        return None
    if is_setup_authorized(caller):
        return None
    return SETUP_ERROR


def validate_identity_payload(identity: dict) -> Optional[str]:
    if not isinstance(identity, dict):
        return "identity must be an object"
    manifesto = identity.get("manifesto")
    if manifesto is not None:
        if not isinstance(manifesto, str):
            return "manifesto must be a string"
        if len(manifesto) > MANIFESTO_MAX_CHARS:
            return f"manifesto exceeds maximum length ({MANIFESTO_MAX_CHARS} chars)"
    welcome_message = identity.get("welcome_message")
    if welcome_message is not None:
        if not isinstance(welcome_message, str):
            return "welcome_message must be a string"
        if len(welcome_message) > WELCOME_MESSAGE_MAX_CHARS:
            return (
                f"welcome_message exceeds maximum length "
                f"({WELCOME_MESSAGE_MAX_CHARS} chars)"
            )
    if "languages" in identity or "primary_language" in identity:
        from core.realm_locales import normalize_languages

        _langs, _primary, error = normalize_languages(
            identity.get("languages"),
            identity.get("primary_language"),
            require_primary="primary_language" in identity
            or "languages" in identity,
        )
        if error:
            return error
    return None


def normalize_primary_color(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    if len(trimmed) != 7 or trimmed[0] != "#":
        return None
    if not all(ch in _HEX_DIGITS for ch in trimmed[1:]):
        return None
    return trimmed.lower()


def get_primary_color(realm) -> str:
    setup = get_setup_config(realm)
    branding = setup.get("branding")
    if isinstance(branding, dict):
        colors = branding.get("colors")
        if isinstance(colors, dict):
            normalized = normalize_primary_color(colors.get("primary"))
            if normalized:
                return normalized
    return DEFAULT_PRIMARY_COLOR


def set_primary_color(realm, hex_color: str) -> Optional[str]:
    normalized = normalize_primary_color(hex_color)
    if normalized is None:
        return "primary_color must be a valid #RRGGBB hex color"
    setup = get_setup_config(realm)
    branding = dict(setup.get("branding") or {}) if isinstance(setup.get("branding"), dict) else {}
    colors = dict(branding.get("colors") or {}) if isinstance(branding.get("colors"), dict) else {}
    colors["primary"] = normalized
    branding["colors"] = colors
    try:
        update_setup_config(realm, {"branding": branding})
    except ValueError as exc:
        return str(exc)
    return None


def validate_branding_payload(branding: dict) -> Optional[str]:
    if not isinstance(branding, dict):
        return "branding must be an object"
    for key in ("logo_data_url", "background_data_url"):
        value = branding.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            return f"{key} must be a string"
        if len(value.encode("utf-8")) > BRANDING_DATA_URL_MAX_BYTES:
            return f"{key} exceeds maximum size (~1.5MB)"
    colors = branding.get("colors")
    if colors is not None and not isinstance(colors, dict):
        return "colors must be an object"
    if isinstance(colors, dict) and "primary" in colors:
        if normalize_primary_color(colors.get("primary")) is None:
            return "colors.primary must be a valid #RRGGBB hex color"
    return None


def get_setup_draft(realm) -> dict:
    setup = get_setup_config(realm)
    draft = setup.get("draft")
    return dict(draft) if isinstance(draft, dict) else {}


def draft_branding_for_response(branding: Any) -> Optional[dict]:
    if not isinstance(branding, dict):
        return None
    out: Dict[str, Any] = {}
    if branding.get("logo"):
        out["logo"] = True
        if branding.get("logo_size") is not None:
            out["logo_size"] = branding["logo_size"]
    if branding.get("background"):
        out["background"] = True
        if branding.get("background_size") is not None:
            out["background_size"] = branding["background_size"]
    colors = branding.get("colors")
    if isinstance(colors, dict) and colors:
        out["colors"] = dict(colors)
    return out or None


def draft_for_response(draft: dict) -> dict:
    if not isinstance(draft, dict):
        return {}
    out = dict(draft)
    branding = draft_branding_for_response(draft.get("branding"))
    if branding is not None:
        out["branding"] = branding
    elif "branding" in out:
        out.pop("branding", None)
    return out


def merge_setup_draft(realm, partial: dict) -> dict:
    draft = get_setup_draft(realm)
    if "step" in partial:
        step = (partial.get("step") or "").strip()
        if step:
            if step not in SETUP_DRAFT_STEPS:
                raise ValueError(f"invalid draft step: {step}")
            draft["step"] = step
    for key in ("codex", "token", "identity", "languages"):
        if key not in partial:
            continue
        value = partial[key]
        if value is None:
            draft.pop(key, None)
            continue
        if not isinstance(value, dict):
            raise ValueError(f"{key} must be an object")
        existing = draft.get(key)
        merged = dict(existing) if isinstance(existing, dict) else {}
        merged.update(value)
        draft[key] = merged
    if "branding" in partial:
        branding = partial["branding"]
        if branding is None:
            draft.pop("branding", None)
        elif not isinstance(branding, dict):
            raise ValueError("branding must be an object")
        else:
            existing = draft.get("branding")
            merged = dict(existing) if isinstance(existing, dict) else {}
            for marker_key in ("logo", "background", "logo_size", "background_size"):
                if marker_key in branding:
                    merged[marker_key] = branding[marker_key]
            colors = branding.get("colors")
            if isinstance(colors, dict):
                existing_colors = merged.get("colors")
                if isinstance(existing_colors, dict):
                    merged_colors = dict(existing_colors)
                    merged_colors.update(colors)
                    merged["colors"] = merged_colors
                else:
                    merged["colors"] = dict(colors)
            draft["branding"] = merged
    update_setup_config(realm, {"draft": draft})
    return draft


def default_launch_state() -> dict:
    return {
        "status": "idle",
        "phase": None,
        "steps": [
            {"name": name, "status": "pending", "error": None}
            for name, _label in SETUP_LAUNCH_PHASES
        ],
        "updated_at": None,
    }


def get_launch_state(realm) -> dict:
    setup = get_setup_config(realm)
    launch = setup.get("launch")
    if not isinstance(launch, dict):
        return default_launch_state()
    steps = launch.get("steps")
    if not isinstance(steps, list) or len(steps) != len(SETUP_LAUNCH_PHASES):
        return default_launch_state()
    return dict(launch)


def save_launch_state(realm, launch: dict) -> dict:
    update_setup_config(realm, {"launch": launch})
    return launch


def launch_state_for_response(realm) -> dict:
    return get_launch_state(realm)


def validate_draft_for_launch(draft: dict) -> Optional[str]:
    codex = draft.get("codex")
    if not isinstance(codex, dict) or not (codex.get("package") or "").strip():
        return "A codex must be chosen in the draft before launch"
    return None


def _first_failed_launch_step(launch: dict) -> Optional[dict]:
    for step in launch.get("steps") or []:
        if step.get("status") == "failed":
            return step
    return None


def begin_setup_launch(realm) -> Optional[dict]:
    draft = get_setup_draft(realm)
    err = validate_draft_for_launch(draft)
    if err:
        return {"success": False, "error": err}

    launch = get_launch_state(realm)
    status = (launch.get("status") or "idle").strip()

    if status == "completed":
        return {"success": False, "error": "setup launch already completed"}

    # Explicit Retry must always reset a failed step — even if the parent
    # status is still "running" or the launch is not stale. A no-op here
    # leaves the old configure_token Settings error in place (Valencia:
    # draft already had pe5t5, updated_at never moved).
    failed_step = _first_failed_launch_step(launch)
    if failed_step is not None:
        failed_step["status"] = "pending"
        failed_step["error"] = None
        launch["status"] = "running"
        launch["phase"] = None
        launch["updated_at"] = str(ic.time())
        save_launch_state(realm, launch)
        return None

    if status == "running":
        if not _launch_is_stale(launch):
            return None
        resumed = False
        for step in launch.get("steps") or []:
            if step.get("status") in ("running", "failed"):
                step["status"] = "pending"
                step["error"] = None
                resumed = True
                break
        if not resumed:
            launch = default_launch_state()

    elif status == "failed":
        launch = default_launch_state()
    else:
        launch = default_launch_state()

    launch["status"] = "running"
    launch["phase"] = None
    launch["updated_at"] = str(ic.time())
    save_launch_state(realm, launch)
    return None


def _find_launch_step(launch: dict, name: str) -> Optional[dict]:
    for step in launch.get("steps") or []:
        if step.get("name") == name:
            return step
    return None


def _launch_is_stale(launch: dict) -> bool:
    updated_at_raw = launch.get("updated_at")
    if updated_at_raw is not None:
        try:
            if ic.time() - int(updated_at_raw) > SETUP_LAUNCH_STALE_NANOS:
                return True
        except (TypeError, ValueError):
            pass

    phase = (launch.get("phase") or "").strip()
    if phase == "install_codex":
        step = _find_launch_step(launch, "install_codex")
        if step and step.get("status") == "running":
            return True
    return False


def _next_pending_launch_step(launch: dict) -> Optional[dict]:
    for step in launch.get("steps") or []:
        if step.get("status") in ("pending", "failed"):
            return step
    return None


def _all_launch_steps_completed(launch: dict) -> bool:
    for step in launch.get("steps") or []:
        if step.get("status") != "completed":
            return False
    return True


def advance_setup_launch() -> Async[dict]:
    from core.quarter_bootstrap import disable_recurring_task

    try:
        from ggg import Realm
    except ImportError:
        from realm_backend.ggg import Realm

    realm = Realm.load("1")
    if not realm:
        return {"success": False, "error": "Realm not found"}

    launch = get_launch_state(realm)
    status = (launch.get("status") or "idle").strip()
    if status != "running":
        if status in ("completed", "failed"):
            disable_recurring_task(SETUP_LAUNCH_TASK_NAME)
        return {"success": True, "status": status, "launch": launch}

    step = _next_pending_launch_step(launch)
    if step is None:
        launch["status"] = "completed"
        launch["phase"] = None
        launch["updated_at"] = str(ic.time())
        save_launch_state(realm, launch)
        disable_recurring_task(SETUP_LAUNCH_TASK_NAME)
        return {"success": True, "status": "completed", "launch": launch}

    phase_name = step.get("name") or ""
    launch["phase"] = phase_name
    step["status"] = "running"
    step["error"] = None
    launch["updated_at"] = str(ic.time())
    save_launch_state(realm, launch)

    from api.setup import run_setup_launch_phase

    try:
        outcome = run_setup_launch_phase(realm, phase_name)
        if hasattr(outcome, "send"):
            result = yield from outcome
        else:
            result = outcome
        ok = isinstance(result, dict) and result.get("success") is not False
    except Exception as exc:
        logger.error(f"setup launch phase {phase_name} failed: {exc}")
        result = {"success": False, "error": str(exc)}
        ok = False

    realm = Realm.load("1")
    launch = get_launch_state(realm)
    step = _find_launch_step(launch, phase_name)
    if step is None:
        return {"success": False, "error": f"launch step {phase_name} missing"}

    if ok:
        step["status"] = "completed"
        step["error"] = None
        if _all_launch_steps_completed(launch):
            launch["status"] = "completed"
            launch["phase"] = None
            disable_recurring_task(SETUP_LAUNCH_TASK_NAME)
        else:
            launch["status"] = "running"
            launch["phase"] = None
    else:
        step["status"] = "failed"
        step["error"] = (
            (result or {}).get("error") if isinstance(result, dict) else str(result)
        )
        launch["status"] = "failed"
        launch["phase"] = phase_name
        disable_recurring_task(SETUP_LAUNCH_TASK_NAME)

    launch["updated_at"] = str(ic.time())
    save_launch_state(realm, launch)
    return {
        "success": ok,
        "status": launch["status"],
        "phase": phase_name,
        "result": result,
        "launch": launch,
    }


def get_setup_state_payload() -> dict:
    from ggg import Realm

    realm = Realm.load("1")
    if not realm:
        return {"success": False, "error": "Realm not found"}

    setup = get_setup_config(realm)
    caller = ic.caller().to_str()
    codex = setup.get("codex")
    token = setup.get("token")
    branding = setup.get("branding")
    identity = setup.get("identity")
    draft = get_setup_draft(realm)
    if isinstance(token, str) and token.strip():
        token = {"symbol": token.strip()}

    return {
        "success": True,
        "status": effective_realm_status(realm),
        "creator": get_creator_principal(realm) or None,
        "is_caller_authorized": is_setup_authorized(caller),
        "codex": codex if isinstance(codex, dict) else None,
        "token": token if isinstance(token, dict) else None,
        "branding": branding if isinstance(branding, dict) else None,
        "identity": identity if isinstance(identity, dict) else None,
        "draft": draft_for_response(draft) or None,
        "launch": launch_state_for_response(realm),
        "realm_name": getattr(realm, "name", None) or None,
        "realm_manifesto": getattr(realm, "manifesto", None) or "",
        "realm_welcome_message": getattr(realm, "welcome_message", None) or "",
        "realm_token_canister_id": (getattr(realm, "token_canister_id", "") or "").strip() or None,
        "gos_environment": get_gos_environment(realm) or None,
        "fleet": setup.get("fleet") if isinstance(setup.get("fleet"), dict) else None,
        "shared_tokens": _shared_token_catalog(),
        "setup_completed_at": setup.get("setup_completed_at"),
        **_setup_language_fields(realm, identity if isinstance(identity, dict) else None, draft),
    }


def _shared_token_catalog() -> dict:
    try:
        from api.tokens import shared_token_catalog
    except ImportError:
        return {}
    return shared_token_catalog()


def _setup_language_fields(realm, identity: Optional[dict], draft: dict) -> dict:
    from core.realm_locales import get_realm_languages

    languages, primary = get_realm_languages(realm)
    if isinstance(identity, dict):
        if isinstance(identity.get("languages"), list) and identity.get("languages"):
            languages = list(identity["languages"])
        if identity.get("primary_language"):
            primary = identity["primary_language"]
    draft_langs = draft.get("languages") if isinstance(draft, dict) else None
    if isinstance(draft_langs, dict):
        if isinstance(draft_langs.get("languages"), list) and draft_langs.get("languages"):
            languages = list(draft_langs["languages"])
        if draft_langs.get("primary_language"):
            primary = draft_langs["primary_language"]
    return {
        "languages": languages,
        "primary_language": primary,
    }


def _safe_log(level: str, message: str, *args: Any) -> None:
    """Best-effort logging that must never fail setup completion."""
    try:
        getattr(logger, level)(message, *args)
    except Exception:
        pass


def notify_registry_setup_completed(registry_canister_id: str) -> Async[None]:
    from _cdk import CallResult, Principal, Service, service_update, text

    class RealmRegistrySetupService(Service):
        @service_update
        def realm_setup_completed(self, args: text) -> text: ...

    backend_id = str(ic.id())
    payload = json.dumps({"realm_backend_canister_id": backend_id})
    try:
        registry = RealmRegistrySetupService(Principal.from_str(registry_canister_id))
        result: CallResult = yield registry.realm_setup_completed(payload)
        _safe_log(
            "info",
            "realm_setup_completed notify to %s: %s",
            registry_canister_id,
            result,
        )
    except Exception as exc:
        _safe_log(
            "warning",
            "realm_setup_completed notify to %s failed (non-fatal): %s",
            registry_canister_id,
            exc,
        )
