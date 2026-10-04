"""Sandboxed extension / codex-hook execution — Basilisk subinterpreter integration.

Routes extension backend calls (and, when compatible, codex hooks) into an
isolated CPython subinterpreter (``_basilisk_sandbox``, ic-basilisk >= 0.14.2)
according to a realm-admin configurable policy. See
https://github.com/smart-social-contracts/realms-gos/issues/245

Policy file (persistent FS, survives upgrades): /sandbox_config.json

    {
        "enabled": true,               # master switch — default on
        "default_mode": "sandbox",     # mode for non-system extensions when enabled
        "extensions": {"ext_id": "sandbox" | "in_process"},
        "codex_hooks": {
            "default_mode": "sandbox",
            "hooks": {"role_assign_prehook": "sandbox" | "in_process", ...}
        },
        "budget": 10000000             # per-spawn instruction budget (0 = unmetered)
    }

**There is no in-process fallback.** An extension or hook that resolves to
``sandbox`` either runs in the subinterpreter or fails; it is never retried
with full host access. Silently degrading to in-process turned every spawn
failure into a privilege escalation and made the sandbox unfalsifiable — you
could not tell from the outside whether isolation was in effect.

Running in-process is therefore a *declaration*, never a runtime discovery.

**Privilege is granted by the host, never claimed by the package.** An installed
manifest may *request* in-process execution, but the request is honoured only for
an id the host already trusts (``core.privileged_extensions.may_run_in_process``:
``CORE_EXTENSION_IDS`` plus the audited, shrinking exemption list). Anything else
requesting in-process is resolved to ``sandbox``. A manifest's ``"system": true``
is ignored entirely. Otherwise the code being sandboxed would be deciding whether
it gets sandboxed, and the only real boundary would be the permission check on
installing an extension.

* Core extensions (``CORE_EXTENSION_IDS``) are never sandboxed — they are the
  trusted platform surface and depend on host modules (``ggg``, ``core``) that do
  not exist inside a sandbox.
* An extension manifest may declare ``"runtime": "in_process"``, which is required
  for any extension that imports host modules. Admins cannot override such an
  extension to ``sandbox``: the spawn is known in advance to fail.
* An admin (or governance proposal) may set ``extensions: {id: "sandbox"}`` to
  narrow privilege. Widening it to ``in_process`` is refused unless the host
  already grants that id, so host access cannot be handed out by whoever holds
  an admin session.

``get_status`` reports the resolved mode and its reason for every installed
extension, so the trusted set is auditable in Realm Settings.

Certain codex hooks are structurally forced in-process (async / broad seeding).
Other hooks prefer sandbox per config but only actually run sandboxed once
marked sandbox-compatible (plain-data contract); until then the legacy
in-process ``exec()`` path is kept.

All data crossing the boundary is deep-copied plain data. So:

* **Extensions** run fresh-per-use (spawn, one call, close) as pure compute over
  their JSON args — no host reads/writes. Extensions that import host modules
  cannot spawn and must declare ``"runtime": "in_process"`` in their manifest.
* **Codex hooks** use the *gather → compute → apply-effects* bridge
  (``core.codex_bridge``): the host injects a plain-data ``context`` of
  pre-gathered reads, the hook may additionally make live *read* calls back into
  the host via ``rpc`` (restricted to its declared read capabilities), and it
  returns a plain-data list of intended writes that the host authorizes and
  applies after the hook returns. See issue #265.

Spawns are metered: the policy ``budget`` is a deterministic bytecode-instruction
count enforced inside the interpreter loop (0 disables it). Images predating the
extended spawn signature run unmetered — see ``supports_capabilities()``.
"""

import json
import os
from typing import Any, Dict, List, Optional

from ic_python_logging import get_logger

logger = get_logger("core.runtime_sandbox")

CONFIG_PATH = "/sandbox_config.json"

VALID_MODES = ("sandbox", "in_process")


class CodexHookError(RuntimeError):
    """A sandboxed codex hook ran and reported failure.

    Distinct from an infrastructure failure: the codex's own code executed, so
    re-running it in-process would run that code a second time *with full host
    access*. Callers therefore treat this as handled and must not fall back.
    """

# Hooks that cannot cross a plain-data subinterpreter boundary.
FORCE_IN_PROCESS_HOOKS = frozenset({
    # Federation handlers create/read ggg entities (issue #263).
    "on_federation_message",
})

# Well-known hook names shown in Realm Settings (desired mode is configurable
# for all of these except FORCE_IN_PROCESS_HOOKS, which stay locked).
KNOWN_CODEX_HOOKS = (
    "role_assign_prehook",
    "role_assign_posthook",
    "role_revoke_prehook",
    "role_revoke_posthook",
    "get_governance_params",
    "get_federal_governance_params",
    "get_config",
    "get_dashboard_config",
    "get_extension_overrides",
    "on_user_register",
    "on_invoice_accounting",
    "on_federation_message",
    "on_stage_change",
    "check_lifecycle_transition",
    "init",
    "seed",
    "on_treasury_send",
)

# Hooks that reach the realm exclusively through the capability bridge
# (``rpc`` / ``ggg_sdk``) and can therefore run inside a subinterpreter with a
# real (non-empty) capability. A hook here only actually runs sandboxed when the
# active codex is bridge-aware (declares ``capabilities``); legacy codices keep
# the in-process exec() path regardless (issue #265, Workstream C).
SANDBOX_COMPATIBLE_HOOKS = frozenset({
    "on_user_register",
    "on_treasury_send",
    "init",
    "seed",
    "role_assign_prehook",
    "role_assign_posthook",
    "role_revoke_prehook",
    "role_revoke_posthook",
    "get_governance_params",
})

# Hooks with no in-process implementation left to fall back to: the role and
# governance hooks, whose source lives in a ``Codex.code`` column rather than a
# package. They are the gates that decide who may hold admin or treasurer, so
# the break-glass override that other hooks keep does not apply here — there is
# nothing to switch them back to (issue #265).
ALWAYS_SANDBOXED_HOOKS = frozenset({
    "role_assign_prehook",
    "role_assign_posthook",
    "role_revoke_prehook",
    "role_revoke_posthook",
    "get_governance_params",
})

DEFAULT_CONFIG = {
    "enabled": True,
    "default_mode": "sandbox",
    "extensions": {},
    "codex_hooks": {
        "default_mode": "sandbox",
        "hooks": {},
    },
    "budget": 10_000_000,
}

_config_cache: Optional[dict] = None


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


def is_sandbox_available() -> bool:
    """True when the running WASM image ships the sandbox primitive."""
    try:
        import _basilisk_sandbox
    except ImportError:
        return False
    return callable(getattr(_basilisk_sandbox, "sha256", None))


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _normalize_codex_hooks(raw: Any) -> dict:
    """Return a validated codex_hooks block (defaults filled in)."""
    block = {
        "default_mode": DEFAULT_CONFIG["codex_hooks"]["default_mode"],
        "hooks": {},
    }
    if not isinstance(raw, dict):
        return block
    mode = raw.get("default_mode", block["default_mode"])
    if mode in VALID_MODES:
        block["default_mode"] = mode
    hooks = raw.get("hooks") or {}
    if isinstance(hooks, dict):
        for name, hook_mode in hooks.items():
            if hook_mode in VALID_MODES:
                block["hooks"][str(name)] = hook_mode
    return block


def get_config() -> dict:
    """Load the sandbox policy, merged over defaults. Cached in memory."""
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    config = {
        "enabled": DEFAULT_CONFIG["enabled"],
        "default_mode": DEFAULT_CONFIG["default_mode"],
        "extensions": {},
        "codex_hooks": _normalize_codex_hooks(DEFAULT_CONFIG["codex_hooks"]),
        "budget": DEFAULT_CONFIG["budget"],
    }
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r") as f:
                stored = json.loads(f.read())
            if isinstance(stored, dict):
                if "enabled" in stored:
                    config["enabled"] = bool(stored["enabled"])
                if stored.get("default_mode") in VALID_MODES:
                    config["default_mode"] = stored["default_mode"]
                if isinstance(stored.get("extensions"), dict):
                    config["extensions"] = {
                        str(k): v
                        for k, v in stored["extensions"].items()
                        if v in VALID_MODES
                    }
                if "codex_hooks" in stored:
                    config["codex_hooks"] = _normalize_codex_hooks(stored["codex_hooks"])
                if (
                    isinstance(stored.get("budget"), int)
                    and not isinstance(stored.get("budget"), bool)
                    and stored["budget"] >= 0
                ):
                    config["budget"] = stored["budget"]
    except Exception as e:
        logger.warning(f"Could not read {CONFIG_PATH} ({e}); using defaults")

    _config_cache = config
    return config


def update_config(patch: dict) -> dict:
    """Merge ``patch`` into the stored policy after validation.

    Returns the new effective config. Raises ValueError on invalid input.
    """
    if not isinstance(patch, dict):
        raise ValueError("sandbox config must be a JSON object")

    allowed = set(DEFAULT_CONFIG.keys())
    unknown = set(patch.keys()) - allowed
    if unknown:
        raise ValueError(f"unknown sandbox config keys: {sorted(unknown)}")

    config = dict(get_config())
    config["extensions"] = dict(config.get("extensions", {}))
    config["codex_hooks"] = _normalize_codex_hooks(config.get("codex_hooks"))

    if "enabled" in patch:
        if not isinstance(patch["enabled"], bool):
            raise ValueError("'enabled' must be a boolean")
        config["enabled"] = patch["enabled"]

    if "default_mode" in patch:
        if patch["default_mode"] not in VALID_MODES:
            raise ValueError(f"'default_mode' must be one of {VALID_MODES}")
        config["default_mode"] = patch["default_mode"]

    if "budget" in patch:
        if (
            not isinstance(patch["budget"], int)
            or isinstance(patch["budget"], bool)
            or patch["budget"] < 0
        ):
            raise ValueError("'budget' must be a non-negative integer")
        config["budget"] = patch["budget"]

    if "extensions" in patch:
        if not isinstance(patch["extensions"], dict):
            raise ValueError("'extensions' must be an object of {ext_id: mode}")
        for ext_id, mode in patch["extensions"].items():
            if mode is None or mode == "":
                config["extensions"].pop(ext_id, None)
                continue
            if mode not in VALID_MODES:
                raise ValueError(
                    f"extension '{ext_id}': mode must be one of {VALID_MODES} "
                    f"(or null to clear the override)"
                )
            if mode == "sandbox" and is_system_extension(ext_id):
                raise ValueError(
                    f"extension '{ext_id}' is a core/system extension and "
                    f"cannot be sandboxed"
                )
            if mode == "sandbox" and _is_codex_package(ext_id):
                raise ValueError(
                    f"'{ext_id}' is a codex — its hooks are isolated per-hook by "
                    f"the capability bridge, not by this setting"
                )
            if mode == "sandbox" and manifest_runtime_mode(ext_id) == "in_process":
                raise ValueError(
                    f"extension '{ext_id}' declares \"runtime\": \"in_process\" "
                    f"and cannot be sandboxed — it imports host modules, so the "
                    f"spawn would fail and there is no in-process fallback"
                )
            config["extensions"][ext_id] = mode

    if "codex_hooks" in patch:
        raw = patch["codex_hooks"]
        if not isinstance(raw, dict):
            raise ValueError("'codex_hooks' must be an object")
        hooks_block = _normalize_codex_hooks(config["codex_hooks"])
        if "default_mode" in raw:
            if raw["default_mode"] not in VALID_MODES:
                raise ValueError(
                    f"codex_hooks.default_mode must be one of {VALID_MODES}"
                )
            hooks_block["default_mode"] = raw["default_mode"]
        if "hooks" in raw:
            if not isinstance(raw["hooks"], dict):
                raise ValueError("codex_hooks.hooks must be an object")
            for name, mode in raw["hooks"].items():
                name = str(name)
                if mode is None or mode == "":
                    hooks_block["hooks"].pop(name, None)
                    continue
                if mode not in VALID_MODES:
                    raise ValueError(
                        f"codex hook '{name}': mode must be one of {VALID_MODES} "
                        f"(or null to clear the override)"
                    )
                if name in FORCE_IN_PROCESS_HOOKS and mode == "sandbox":
                    raise ValueError(
                        f"codex hook '{name}' cannot be sandboxed "
                        f"(async / seeding hooks are always in-process)"
                    )
                hooks_block["hooks"][name] = mode
        config["codex_hooks"] = hooks_block

    _save_config(config)
    return config


def _save_config(config: dict) -> None:
    global _config_cache
    with open(CONFIG_PATH, "w") as f:
        f.write(json.dumps(config))
    _config_cache = config
    logger.info(f"Sandbox config updated: {config}")


# ---------------------------------------------------------------------------
# Policy resolution
# ---------------------------------------------------------------------------


def is_system_extension(ext_id: str) -> bool:
    """True for core extensions — the trusted platform surface, never sandboxed.

    Membership is decided by the host list in ``core.core_extensions``. A
    manifest's ``"system": true`` is *not* consulted: an installed package
    claiming to be part of the platform is a request, not a grant.
    """
    try:
        from core.core_extensions import is_core_extension

        return is_core_extension(ext_id)
    except Exception:
        return False


def _is_codex_package(ext_id: str) -> bool:
    """True for ``kind: codex`` packages.

    A codex's isolation is decided per *hook* by the capability bridge
    (``sandbox_module`` + declared capabilities), not by this switch. Its
    ``entry.py`` is the legacy in-process implementation and imports host
    modules, so it must never be routed through ``call_in_sandbox``.
    """
    try:
        from core.runtime_extensions import _load_manifest

        manifest = _load_manifest(ext_id) or {}
    except Exception:
        return False
    return manifest.get("kind") == "codex"


def manifest_runtime_mode(ext_id: str) -> Optional[str]:
    """The execution mode an extension's manifest declares, if any.

    An extension whose ``entry.py`` imports host modules (``ggg``, ``core``,
    ``basilisk``) cannot spawn in a subinterpreter, and with no fallback that
    is a hard failure at call time. Such extensions declare
    ``"runtime": "in_process"`` so the trusted set is known from the manifest
    rather than discovered from a stack trace.
    """
    try:
        from core.runtime_extensions import _load_manifest

        manifest = _load_manifest(ext_id) or {}
    except Exception:
        return None
    mode = manifest.get("runtime")
    return mode if mode in VALID_MODES else None


def resolve_mode(ext_id: str) -> tuple:
    """Resolve one extension's execution mode. Returns ``(mode, reason)``.

    Reason is a short human string for the admin UI; the mode is authoritative
    and is never revised at call time.
    """
    config = get_config()
    if is_system_extension(ext_id):
        return "in_process", "core/system extension"
    if _is_codex_package(ext_id):
        return "in_process", "codex (hooks isolated via capability bridge)"
    if not config.get("enabled"):
        return "in_process", "sandboxing disabled"

    from core.privileged_extensions import may_run_in_process

    host_grants_privilege = may_run_in_process(ext_id)
    declared = manifest_runtime_mode(ext_id)
    if declared == "in_process":
        if host_grants_privilege:
            return "in_process", "requested by manifest, granted by host"
        return "sandbox", "manifest requested in_process; not granted by host"

    override = config.get("extensions", {}).get(ext_id)
    if override in VALID_MODES:
        if override == "in_process" and not host_grants_privilege:
            # An admin may narrow privilege but not widen it. Granting host
            # access to an arbitrary package is a build-time decision, so that
            # it cannot be made by whoever currently holds an admin session.
            return "sandbox", "admin requested in_process; not granted by host"
        return override, "admin override"
    if declared == "sandbox":
        return "sandbox", "declared by manifest"
    return config.get("default_mode"), "realm default"


def should_sandbox(ext_id: str) -> bool:
    """Decide the execution mode for one (override-resolved) extension id."""
    return resolve_mode(ext_id)[0] == "sandbox"


def _desired_hook_mode(hook_name: str, config: Optional[dict] = None) -> str:
    config = config or get_config()
    hooks_block = config.get("codex_hooks") or {}
    overrides = hooks_block.get("hooks") or {}
    if hook_name in overrides and overrides[hook_name] in VALID_MODES:
        return overrides[hook_name]
    default = hooks_block.get("default_mode") or DEFAULT_CONFIG["codex_hooks"]["default_mode"]
    return default if default in VALID_MODES else "sandbox"


def resolve_hook_mode(hook_name: str) -> str:
    """Human-readable effective mode for one codex hook."""
    config = get_config()
    if hook_name in FORCE_IN_PROCESS_HOOKS:
        return "in_process (forced)"
    if hook_name in ALWAYS_SANDBOXED_HOOKS:
        return "sandbox (always)"
    if not config.get("enabled"):
        return "in_process (sandboxing disabled)"
    desired = _desired_hook_mode(hook_name, config)
    if desired != "sandbox":
        return "in_process"
    if not is_sandbox_available():
        return "in_process (sandbox unavailable)"
    if hook_name not in SANDBOX_COMPATIBLE_HOOKS:
        return "in_process (not sandbox-compatible)"
    return "sandbox"


def should_sandbox_hook(hook_name: str) -> bool:
    """True only when the hook will actually execute in a subinterpreter."""
    return resolve_hook_mode(hook_name).startswith("sandbox")


def describe_config_patch(patch: dict, current: Optional[dict] = None) -> str:
    """One-line human summary of a sandbox config change for proposal UIs."""
    current = current or get_config()
    parts: List[str] = []
    if "enabled" in patch and patch["enabled"] != current.get("enabled"):
        parts.append(
            "enable sandboxing" if patch["enabled"] else "disable sandboxing"
        )
    if "default_mode" in patch and patch["default_mode"] != current.get("default_mode"):
        parts.append(f"extension default → {patch['default_mode']}")
    if "budget" in patch and patch["budget"] != current.get("budget"):
        parts.append(f"budget → {patch['budget']}")
    if isinstance(patch.get("extensions"), dict) and patch["extensions"]:
        for ext_id, mode in sorted(patch["extensions"].items()):
            label = "clear" if mode in (None, "") else mode
            parts.append(f"ext {ext_id} → {label}")
    if isinstance(patch.get("codex_hooks"), dict):
        ch = patch["codex_hooks"]
        cur_ch = current.get("codex_hooks") or {}
        if "default_mode" in ch and ch["default_mode"] != cur_ch.get("default_mode"):
            parts.append(f"hook default → {ch['default_mode']}")
        hooks = ch.get("hooks") or {}
        if isinstance(hooks, dict):
            for name, mode in sorted(hooks.items()):
                label = "clear" if mode in (None, "") else mode
                parts.append(f"hook {name} → {label}")
    if not parts:
        return "Update sandbox policy (no effective changes)"
    return "Sandbox policy: " + "; ".join(parts)


def build_proposal_code(patch: dict) -> str:
    """Inline code that reapplies *patch* when a governance proposal executes."""
    return (
        "from core import runtime_sandbox\n"
        f"runtime_sandbox.update_config({json.dumps(patch)})\n"
        "logger.info('Governance: applied sandbox config patch')\n"
    )


# ---------------------------------------------------------------------------
# Execution — real ``_basilisk_sandbox`` primitive
# ---------------------------------------------------------------------------
#
# API (basilisk_sandbox.c):
#   sha256(text) -> hex                    approve_hash(hex) / revoke_hash(hex)
#   spawn_subinterpreter(source, hash, context_id="", allowed_actions=(),
#                        rpc_handler=None, budget=10_000_000) -> handle
#   call_in_subinterpreter(handle, fn, kwargs=None) -> plain data
#   close_subinterpreter(handle)           wasm_memory_pages()
#
# ``budget`` is a deterministic bytecode-instruction count decremented in the
# interpreter's dispatch loop (never wall-clock, so it is replica-consistent);
# 0 disables metering. ``rpc_handler`` is a main-interpreter callable invoked
# synchronously as ``handler(context_id, action, kwargs)`` when sandboxed code
# calls the injected ``rpc()`` builtin, restricted to ``allowed_actions``.
#
# Older images ship a two-argument ``spawn_subinterpreter``. The first spawn
# probes for the extended form and every later spawn reuses the answer.

_extended_spawn: Optional[bool] = None


def supports_capabilities() -> Optional[bool]:
    """Whether this image's sandbox accepts capability/budget spawn arguments.

    ``None`` until the first spawn has probed for it.
    """
    return _extended_spawn


def _content_hash(source: str) -> str:
    """SHA-256 hex digest of *source* for ``approve_hash`` / spawn.

    Uses Basilisk C ``_basilisk_sandbox.sha256`` only. Missing ``sha256``
    raises ``AttributeError`` — it is not a missing hook.
    """
    import _basilisk_sandbox

    return _basilisk_sandbox.sha256(source)


def _spawn_subinterpreter(
    source: str,
    content_hash: str,
    context_id: str,
    allowed_actions: Any,
    rpc_handler: Any,
    budget: int,
):
    """Spawn a subinterpreter, using the capability/budget arguments when the
    running image supports them and falling back to the legacy two-argument
    form when it does not."""
    global _extended_spawn
    import _basilisk_sandbox

    if _extended_spawn is not False:
        try:
            handle = _basilisk_sandbox.spawn_subinterpreter(
                source,
                content_hash,
                context_id,
                tuple(allowed_actions or ()),
                rpc_handler,
                budget,
            )
            _extended_spawn = True
            return handle
        except TypeError:
            # Once the extended form is known to work, a TypeError means a
            # genuinely bad argument and must not be masked by the retry.
            if _extended_spawn:
                raise
            _extended_spawn = False
            logger.warning(
                "This image's _basilisk_sandbox.spawn_subinterpreter takes no "
                "capability/budget arguments; sandboxed code runs unmetered "
                "and without rpc on this image"
            )
    return _basilisk_sandbox.spawn_subinterpreter(source, content_hash)


def _run_in_subinterpreter(
    source: str,
    function_name: str,
    kwargs: dict,
    context_id: str = "",
    allowed_actions: Optional[List[str]] = None,
    rpc_handler: Any = None,
) -> Any:
    """Spawn a fresh subinterpreter for *source*, call ``function_name`` with
    *kwargs* (plain data), tear it down, and return its plain-data result.

    The instruction budget comes from the sandbox policy (0 = unmetered).
    """
    import _basilisk_sandbox

    budget = get_config().get("budget", DEFAULT_CONFIG["budget"])
    content_hash = _content_hash(source)
    _basilisk_sandbox.approve_hash(content_hash)
    handle = _spawn_subinterpreter(
        source, content_hash, context_id, allowed_actions, rpc_handler, budget
    )
    try:
        return _basilisk_sandbox.call_in_subinterpreter(
            handle, function_name, kwargs
        )
    finally:
        _basilisk_sandbox.close_subinterpreter(handle)
        try:
            _basilisk_sandbox.revoke_hash(content_hash)
        except Exception:
            pass


def call_in_sandbox(ext_id: str, function_name: str, args: str) -> Any:
    """Run ``entry.py::function_name(args)`` of an installed extension in a
    fresh subinterpreter and return its (plain data) result.

    Pure compute over the JSON ``args`` string — no host reads/writes, since a
    subinterpreter has no ``ggg``/``core``/``basilisk``. Extensions needing host
    data go through the capability bridge instead.

    Raises on any failure (missing entry.py, spawn/import failure, non-plain-data
    result). Failures are terminal: there is no in-process fallback, because one
    that silently restored full privilege would make the sandbox advisory.
    """
    from core.runtime_extensions import EXTENSIONS_DIR

    entry_path = os.path.join(EXTENSIONS_DIR, ext_id, "entry.py")
    if not os.path.exists(entry_path):
        raise FileNotFoundError(f"extension '{ext_id}' has no entry.py")

    with open(entry_path, "r") as f:
        source = f.read()

    logger.debug(f"Sandboxing {ext_id}.{function_name} ({len(source)} bytes)")
    return _run_in_subinterpreter(source, function_name, {"args": args})


# ---------------------------------------------------------------------------
# Codex hooks: sandboxed via the capability bridge (issue #265, Workstream C)
# ---------------------------------------------------------------------------


def _ggg_sdk_source() -> str:
    """Source text of the in-sandbox ``ggg_sdk`` module.

    In the canister the realm_backend modules are frozen to bytecode (no
    ``__file__``, no ``loader.get_source``), so the SDK keeps its own source as
    the ``GGG_SDK_SOURCE`` string constant — values survive freezing.
    """
    import ggg_sdk

    return ggg_sdk.GGG_SDK_SOURCE


def _extension_capabilities(ext_id: str) -> List[str]:
    """Bridge capabilities the extension manifest declares.

    Absent or malformed means an empty list: an extension gets nothing it did
    not ask for in writing.
    """
    try:
        manifest = _codex_manifest(ext_id)
        declared = manifest.get("capabilities") or []
        return [c for c in declared if isinstance(c, str)]
    except Exception as e:
        logger.warning(f"_extension_capabilities({ext_id}) failed: {e}")
        return []


def extension_capabilities(ext_id: str) -> List[str]:
    """Public accessor for an extension's declared capabilities."""
    return _extension_capabilities(ext_id)


def call_extension_in_sandbox(
    ext_id: str, function_name: str, args: str, caller: str = ""
) -> Any:
    """Run ``entry.py::function_name(args)`` sandboxed, over the capability
    bridge, and return its plain-data result.

    Same contract as :func:`call_in_sandbox` — the extension keeps its
    ``f(args: str) -> str`` signature — but the subinterpreter also gets the
    ``ggg_sdk`` module and an ``rpc`` channel into
    :mod:`core.extension_bridge`, so it can reach realm data without importing
    ``ggg``.

    *caller* is the authenticated principal, injected here and closed over by
    the handler. Sandboxed code has no way to influence it.

    Raises on any failure; there is no in-process fallback.
    """
    from core import extension_bridge

    capabilities = _extension_capabilities(ext_id)
    source = _build_codex_sandbox_source(
        _extension_source(ext_id), _extension_package_modules(ext_id)
    )

    logger.debug(
        f"Sandboxing extension {ext_id}.{function_name} "
        f"(caller={caller}, capabilities={capabilities})"
    )
    return _run_in_subinterpreter(
        source,
        function_name,
        {"args": args},
        context_id=ext_id,
        allowed_actions=sorted(
            set(capabilities) & set(extension_bridge.VERBS)
        ),
        rpc_handler=extension_bridge.make_rpc_handler(
            ext_id, capabilities, caller
        ),
    )


def _extension_source(ext_id: str) -> str:
    from core.runtime_extensions import EXTENSIONS_DIR

    entry_path = os.path.join(EXTENSIONS_DIR, ext_id, "entry.py")
    if not os.path.exists(entry_path):
        raise FileNotFoundError(f"extension '{ext_id}' has no entry.py")
    with open(entry_path, "r") as f:
        return f.read()


# Package name the bundled sibling modules are registered under. Fixed rather
# than derived from the extension id: a subinterpreter serves one extension, so
# there is nothing to collide with, and an id with a hyphen would not be a valid
# module name.
SANDBOX_PACKAGE = "_ext"


def _module_dependencies(source: str) -> List[str]:
    """Sibling modules a module imports, from ``from . import x`` and
    ``from .x import y``.

    Scanned by hand rather than with a module-level ``re.compile``: some
    execution contexts on this platform ship a gutted ``re`` module with no
    ``compile``, and a pattern compiled at module scope would take the whole
    (lazy-loaded) module down with it.
    """
    deps = []
    for line in source.splitlines():
        text = line.strip()
        if not text.startswith("from"):
            continue
        rest = text[4:].lstrip()
        if not rest.startswith("."):
            continue
        rest = rest[1:].lstrip()
        i = 0
        while i < len(rest) and (rest[i].isalnum() or rest[i] == "_"):
            i += 1
        module = rest[:i]
        tail = rest[i:].strip()
        if not tail.startswith("import "):
            continue
        names = tail[len("import "):]
        if module:
            deps.append(module)
        else:
            # ``from . import a, b as c`` — the names are modules.
            for part in names.split(","):
                name = part.strip().split(" as ")[0].strip()
                if name and name.isidentifier():
                    deps.append(name)
    return deps


def _order_modules(sources: Dict[str, str]) -> List[str]:
    """Dependency order for the sibling modules.

    ``from .constants import VALID_TRANSITIONS`` needs ``constants`` already
    executed, so import order is load-bearing and cannot just be alphabetical.
    A cycle is reported rather than guessed at: the remaining modules go last in
    a stable order, which is correct for ``from . import x`` (the module object
    is populated in place) and will fail loudly for a ``from .x import name``
    that genuinely cannot be satisfied.
    """
    remaining = dict(sources)
    ordered: List[str] = []

    while remaining:
        ready = sorted(
            name for name, source in remaining.items()
            if all(
                dep in ordered or dep not in sources
                for dep in _module_dependencies(source)
            )
        )
        if not ready:
            leftover = sorted(remaining)
            logger.warning(
                f"sandbox bundle: import cycle among {leftover}; loading in "
                f"name order, which works for 'from . import x' but not for "
                f"'from .x import name'"
            )
            ordered.extend(leftover)
            break
        ordered.extend(ready)
        for name in ready:
            remaining.pop(name)

    return ordered


def _extension_package_modules(ext_id: str) -> List[tuple]:
    """``(module_name, source)`` for an extension's sibling modules, in load order.

    The in-process loader imports the extension directory as a package so
    ``entry.py`` can do ``from . import roles``. A subinterpreter has no
    filesystem and ``sys.path == []``, so the same modules are read here and
    registered in the sandbox's ``sys.modules`` instead — the mechanism
    ``ggg_sdk`` already uses.
    """
    from core.runtime_extensions import EXTENSIONS_DIR

    ext_path = os.path.join(EXTENSIONS_DIR, ext_id)
    if not os.path.isdir(ext_path):
        return []

    sources: Dict[str, str] = {}
    for filename in sorted(os.listdir(ext_path)):
        if not filename.endswith(".py") or filename in ("entry.py", "__init__.py"):
            continue
        try:
            with open(os.path.join(ext_path, filename), "r") as f:
                sources[filename[:-3]] = f.read()
        except OSError as e:
            logger.warning(f"{ext_id}: cannot read {filename}: {e}")

    return [(name, sources[name]) for name in _order_modules(sources)]


def is_async_extension_function(ext_id: str, function_name: str) -> bool:
    """Whether the manifest declares this entry point as effect-driven."""
    from core import async_bridge

    try:
        functions = async_bridge.declared_async_functions(_codex_manifest(ext_id))
    except ValueError as e:
        logger.warning(f"{ext_id}: bad async_functions declaration: {e}")
        return False
    return function_name in functions


def call_extension_round(
    ext_id: str,
    function_name: str,
    args: str,
    caller: str = "",
    resolved: Optional[dict] = None,
) -> Any:
    """One round of an effect-driven extension call.

    Returns the dispatcher's status dict: ``{"status": "ok", "value": ...}`` or
    ``{"status": "effect", "request": ...}``. Driven by
    :func:`core.async_bridge.run_with_effects`, which owns the loop and the
    outcall.

    Write verbs are refused for the whole call. The body replays once per round,
    so a write here would be applied once per round, and there is no transaction
    to roll back — see ``async_bridge.ASYNC_WRITE_RULE``.
    """
    from core import extension_bridge

    capabilities = _extension_capabilities(ext_id)
    source = _build_codex_sandbox_source(
        _extension_source(ext_id), _extension_package_modules(ext_id)
    )

    logger.debug(
        f"Sandboxing async extension round {ext_id}.{function_name} "
        f"(caller={caller}, resolved={sorted((resolved or {}).keys())})"
    )
    return _run_in_subinterpreter(
        source,
        "__ext_async_round__",
        {
            "args": args,
            "__fn__": function_name,
            "__resolved__": dict(resolved or {}),
        },
        context_id=ext_id,
        allowed_actions=sorted(
            set(capabilities) & set(extension_bridge.READ_VERBS)
        ),
        rpc_handler=extension_bridge.make_rpc_handler(
            ext_id, capabilities, caller, allow_writes=False
        ),
    )


def _build_codex_sandbox_source(
    codex_source: str, package_modules: Optional[List[tuple]] = None
) -> str:
    """Prepend a loader that installs ``ggg_sdk`` as an importable module inside
    the subinterpreter, then the codex source.

    *package_modules* is ``(name, source)`` for an extension's sibling modules,
    bundled in so a multi-file extension can be sandboxed at all — see
    :func:`_extension_package_modules`.

    The subinterpreter spawns from a single source string, so the SDK is
    embedded and registered in ``sys.modules`` before the codex runs. The codex
    may then ``from ggg_sdk import hook, realm`` exactly as authored; reads are
    served from the injected ``context`` and writes are collected as effects
    (no host callback).
    """
    # The sandbox stdlib is minimal (no ``types`` module), so the module type is
    # taken from ``sys`` itself.
    loader = (
        "import sys as _sys\n"
        "_ggg_sdk = type(_sys)('ggg_sdk')\n"
        "_GGG_SDK_SOURCE = " + repr(_ggg_sdk_source()) + "\n"
        "exec(compile(_GGG_SDK_SOURCE, 'ggg_sdk.py', 'exec'), _ggg_sdk.__dict__)\n"
        "_sys.modules['ggg_sdk'] = _ggg_sdk\n"
    )
    if package_modules:
        loader += _package_loader(package_modules)
    return loader + "\n" + codex_source + "\n" + _ASYNC_DISPATCHER


def _package_loader(modules: List[tuple]) -> str:
    """Loader for an extension's sibling modules.

    Registers a package and each submodule in the sandbox's ``sys.modules``, then
    sets ``__name__``/``__package__`` on the spawned source so that ``from .
    import roles`` in ``entry.py`` resolves. Nothing here touches ``sys.path``
    (which is empty) — the relative import finds the already-registered module.
    """
    pkg = SANDBOX_PACKAGE
    out = [
        f"_ext_pkg = type(_sys)({pkg!r})",
        # An empty __path__ makes it a package for the import machinery without
        # giving it anywhere on disk to search.
        "_ext_pkg.__path__ = []",
        f"_sys.modules[{pkg!r}] = _ext_pkg",
    ]
    for name, source in modules:
        full = f"{pkg}.{name}"
        out += [
            f"_m = type(_sys)({full!r})",
            f"_m.__package__ = {pkg!r}",
            f"_sys.modules[{full!r}] = _m",
            f"exec(compile({source!r}, {name + '.py'!r}, 'exec'), _m.__dict__)",
            f"setattr(_ext_pkg, {name!r}, _m)",
        ]
    # Set last: until now the loader itself is running at top level, and a
    # __package__ in scope would make any relative import here resolve oddly.
    out += [f"__package__ = {pkg!r}", f"__name__ = {pkg + '.entry'!r}"]
    return "\n".join(out) + "\n"


# Entry point for one round of an async extension call (issue #279). Appended to
# the spawned source rather than living in ``ggg_sdk`` because it has to resolve
# the extension's own functions, which are in this module's globals.
#
# It returns a status dict instead of the function's value so the host can tell
# "here is the answer" from "I need an outcall first" without inspecting types.
# ``_ggg_sdk`` is the module object the loader built, used directly rather than
# looked up in ``sys.modules``: ``NeedEffect`` is a distinct class per exec of
# the SDK source, so resolving it late could compare against a different class
# than the one the extension raised and let the request escape as an error.
_ASYNC_DISPATCHER = '''
def __ext_async_round__(args, __fn__, __resolved__):
    _ggg_sdk.ctx.services._resolved = __resolved__ or {}
    _fn = globals().get(__fn__)
    if _fn is None:
        raise AttributeError("extension has no function " + str(__fn__))
    try:
        return {"status": "ok", "value": _fn(args)}
    except _ggg_sdk.NeedEffect as _need:
        return {"status": "effect", "request": _need.request}
'''


def _codex_manifest(codex_id: str) -> dict:
    from core.runtime_extensions import get_all_extension_manifests

    return get_all_extension_manifests().get(codex_id) or {}


def _codex_capabilities(codex_id: str) -> List[str]:
    """Verb capabilities the codex manifest declares (issue #265)."""
    try:
        from core import codex_hooks

        return codex_hooks.codex_capabilities(_codex_manifest(codex_id))
    except Exception as e:
        logger.warning(f"_codex_capabilities({codex_id}) failed: {e}")
        return []


def _codex_hook_module_file(codex_id: str) -> str:
    """Filename to spawn for a codex's sandboxed hooks: the manifest-declared
    ``sandbox_module`` (self-contained SDK hooks), else ``entry.py``."""
    try:
        from core import codex_hooks

        module = codex_hooks.codex_sandbox_module(_codex_manifest(codex_id))
        return module or "entry.py"
    except Exception:
        return "entry.py"


# Context keys the host pre-gathers for a hook, and the verb that produces
# each. Anything a hook needs beyond its spec it fetches live over ``rpc``
# (subject to its declared read capabilities), so this list is an optimization
# — it saves a round-trip for the reads a hook almost certainly wants — not a
# limit on what the hook can see.
_CONTEXT_VERBS = {
    "config": "_v_config_get",
    "currency": "_v_currency_get",
    "now": "_v_time_now",
    "realm": "_v_realm_get",
}

_DEFAULT_CONTEXT_KEYS = ("config", "currency", "now", "realm")

# Per-hook overrides of the default key set.
#
# ``get_config`` must not receive ``config``: gathering it calls
# ``codex_hooks.get_config()``, which is the very thing dispatching this hook.
# (``codex_hooks`` also guards re-entry, so a hook reading ``config.get`` over
# rpc is safe too; omitting it here avoids the pointless round-trip.)
_HOOK_CONTEXT_KEYS = {
    "get_config": ("currency", "now", "realm"),
}


def _gather_hook_context(hook_name: str, args: str) -> dict:
    """Build the plain-data reads to inject into a sandboxed hook.

    Pre-projects the reads *this* hook is likely to want (per
    ``_HOOK_CONTEXT_KEYS``) plus the user referenced in ``args``, so the common
    case needs no host round-trip. Anything else the hook reads goes through
    ``rpc``.
    """
    from core import codex_bridge

    keys = _HOOK_CONTEXT_KEYS.get(hook_name, _DEFAULT_CONTEXT_KEYS)
    context: dict = {"users": {}}
    for key in keys:
        verb = _CONTEXT_VERBS.get(key)
        if not verb:
            continue
        try:
            context[key] = getattr(codex_bridge, verb)()
        except Exception as e:
            # A read that fails to gather is simply absent; the hook can still
            # request it over rpc and get a real error there.
            logger.warning(f"context gather '{key}' for {hook_name} failed: {e}")

    try:
        params = json.loads(args) if args else {}
    except Exception:
        params = {}
    user_id = params.get("user_id") if isinstance(params, dict) else None
    if user_id:
        context["users"][user_id] = codex_bridge._v_user_get(user_id=user_id)
    return context


def _inject_codex_context(context: dict, context_id: str) -> dict:
    """Attach ``codex_id`` for init/seed hooks when *context_id* is a package id."""
    if context_id and not context_id.startswith("proposal:"):
        context = dict(context)
        context["codex_id"] = context_id
    return context


def call_codex_hook_in_sandbox(codex_id: str, hook_name: str, args: str) -> Any:
    """Run ``hook_name`` of a bridge-native codex in a fresh subinterpreter using
    the *gather → compute → apply-effects* bridge, and return its plain-data
    result.

    The module spawned is the codex's declared ``sandbox_module`` (a
    self-contained ``ggg_sdk`` hook module), falling back to ``entry.py``. The
    host injects a plain-data ``context`` (pre-projected reads); the hook runs as
    pure compute and returns an envelope ``{"ok", "effects", "result"}``; the host
    then authorizes and applies the effects against the codex's declared
    capabilities via ``core.codex_bridge.apply_effects``. Raises on any failure,
    which is terminal — there is no in-process fallback.
    """
    from core import codex_bridge
    from core.runtime_extensions import EXTENSIONS_DIR

    module_file = _codex_hook_module_file(codex_id)
    module_path = os.path.join(EXTENSIONS_DIR, codex_id, module_file)
    if not os.path.exists(module_path):
        raise FileNotFoundError(f"codex '{codex_id}' has no {module_file}")

    with open(module_path, "r") as f:
        codex_source = f.read()

    capabilities = _codex_capabilities(codex_id)
    context = _gather_hook_context(hook_name, args)

    # Args cross the boundary pre-parsed: the sandbox stdlib has no ``json``.
    try:
        params = json.loads(args) if args else {}
    except Exception:
        params = {}
    if not isinstance(params, dict):
        params = {}

    return run_bridge_hook(
        codex_id, codex_source, hook_name, params, capabilities, context
    )


def run_bridge_hook(
    context_id: str,
    codex_source: str,
    hook_name: str,
    params: dict,
    capabilities: List[str],
    context: Optional[dict] = None,
    defer_async: bool = False,
):
    """Run *hook_name* from *codex_source* over the capability bridge.

    The shared path behind every sandboxed codex hook, whatever its source: a
    package's ``sandbox_module`` file, or the ``code`` column of a ``Codex``
    entity (the role-management hooks). Spawns the SDK plus the source, calls
    the hook with plain-data ``args``/``context``, then authorizes and applies
    whatever effects it returned.

    When *defer_async* is True, async effects (``treasury.transfer``) are
    authorized but returned for the caller to apply; the return value is
    ``(result, deferred)``.

    Raises ``CodexHookError`` when the hook itself failed, ``PermissionError``
    when it overstepped its capabilities; both mean the codex ran, so callers
    must not retry it in-process.
    """
    from core import codex_bridge

    source = _build_codex_sandbox_source(codex_source)
    logger.debug(
        f"Sandboxing {context_id}.{hook_name} (capabilities={capabilities})"
    )
    payload = _run_in_subinterpreter(
        source,
        hook_name,
        {
            "args": params,
            "context": _inject_codex_context(context or {}, context_id),
        },
        context_id=context_id,
        allowed_actions=codex_bridge.readable_capabilities(capabilities),
        rpc_handler=codex_bridge.make_rpc_handler(context_id, capabilities),
    )

    if not isinstance(payload, dict):
        raise CodexHookError(
            f"'{context_id}' hook '{hook_name}' returned "
            f"{type(payload).__name__}, expected an envelope dict"
        )
    if not payload.get("ok"):
        raise CodexHookError(payload.get("error", "codex hook failed"))

    if defer_async:
        results, deferred = codex_bridge.apply_effects(
            context_id,
            capabilities,
            payload.get("effects") or [],
            defer_async=True,
        )
        return codex_bridge.resolve_result(payload.get("result"), results), deferred

    results = codex_bridge.apply_effects(
        context_id, capabilities, payload.get("effects") or []
    )
    return codex_bridge.resolve_result(payload.get("result"), results)


def get_status() -> dict:
    """Effective status for the admin API: config + availability + the
    resolved mode of every installed extension and known codex hook.

    ``extensions`` carries the structured per-extension view; ``resolved_modes``
    is the human-readable form kept for existing callers.
    """
    from core.runtime_extensions import list_installed

    config = get_config()
    resolved = {}
    ext_meta = []
    for ext_id in list_installed():
        mode, reason = resolve_mode(ext_id)
        resolved[ext_id] = mode if mode == "sandbox" else f"{mode} ({reason})"
        ext_meta.append({
            "id": ext_id,
            "resolved_mode": mode,
            "reason": reason,
            # Locked extensions cannot be sandboxed at all, so the admin UI
            # offers no override for them.
            "locked": is_system_extension(ext_id)
            or _is_codex_package(ext_id)
            or manifest_runtime_mode(ext_id) == "in_process",
        })

    hook_modes = {name: resolve_hook_mode(name) for name in KNOWN_CODEX_HOOKS}
    hook_meta = []
    for name in KNOWN_CODEX_HOOKS:
        hook_meta.append({
            "name": name,
            "desired_mode": (
                "in_process"
                if name in FORCE_IN_PROCESS_HOOKS
                else _desired_hook_mode(name, config)
            ),
            "resolved_mode": hook_modes[name],
            "forced_in_process": name in FORCE_IN_PROCESS_HOOKS,
            "sandbox_compatible": name in SANDBOX_COMPATIBLE_HOOKS,
        })

    return {
        "available": is_sandbox_available(),
        "config": config,
        "resolved_modes": resolved,
        "extensions": ext_meta,
        "hook_modes": hook_modes,
        "hooks": hook_meta,
        "caller_can_configure": None,  # filled by callers that know the principal
    }
