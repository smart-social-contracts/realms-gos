"""Multi-message Codex install job (issue #244 / IC0522).

Every caller (installer, setup wizard, launch tick, quarter bootstrap) starts
or continues the same realm-owned job via ``continue_codex_install``. One
bounded phase runs per update; callers poll by calling again with the same
args when the response has ``status: in_progress``.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

from _cdk import Async, Principal, ic
from ic_python_logging import get_logger

logger = get_logger("core.codex_install_job")

STAGING_ROOT = "/codex_install_staging"
STATE_MAX_LEN = 8192

# Incremental pull budget — one update must stay under the 40B instruction limit.
MAX_PULL_FILES = 3
MAX_PULL_BYTES = 150_000

PHASES = (
    "resolve",
    "pull_backend",
    "scan",
    "pull_frontend",
    "copy_frontend",
    "apply_backend",
    "overlay",
    "init",
)


def is_in_progress(result: Any) -> bool:
    return (
        isinstance(result, dict)
        and result.get("success") is True
        and result.get("status") == "in_progress"
    )


def is_complete(result: Any) -> bool:
    return (
        isinstance(result, dict)
        and result.get("success") is True
        and (result.get("status") == "complete" or result.get("status") is None)
    )


def _phase_index(phase: str) -> int:
    try:
        return PHASES.index(phase)
    except ValueError:
        return 0


def _progress_payload(phase: str, done: int, total: int, extra: Optional[dict] = None) -> dict:
    out = {
        "success": True,
        "status": "in_progress",
        "phase": phase,
        "done": done,
        "total": total,
    }
    if extra:
        out.update(extra)
    return out


def _staging_dir(ext_id: str) -> str:
    return os.path.join(STAGING_ROOT, ext_id)


def _clear_staging(ext_id: str) -> None:
    root = _staging_dir(ext_id)
    if not os.path.exists(root):
        return
    try:
        from core.codex_overlay import _rmtree

        _rmtree(root)
    except Exception as exc:
        logger.warning(f"codex install: could not clear staging for {ext_id}: {exc}")


def _write_staged_file(ext_id: str, path: str, content: str) -> None:
    root = _staging_dir(ext_id)
    dest = os.path.join(root, path)
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest, "w") as handle:
        handle.write(content)


def _read_staged_file(ext_id: str, path: str) -> str:
    with open(os.path.join(_staging_dir(ext_id), path), "r") as handle:
        return handle.read()


def _list_staged_paths(ext_id: str, prefix: str = "") -> List[str]:
    root = _staging_dir(ext_id)
    if not os.path.exists(root):
        return []
    out: List[str] = []

    def walk(rel_dir: str) -> None:
        base = os.path.join(root, rel_dir) if rel_dir else root
        try:
            names = os.listdir(base)
        except OSError:
            return
        for name in names:
            rel = f"{rel_dir}/{name}" if rel_dir else name
            full = os.path.join(root, rel)
            if os.path.isdir(full):
                walk(rel)
            else:
                if not prefix or rel.startswith(prefix):
                    out.append(rel)

    walk("")
    return sorted(out)


def _load_staged_backend_files(ext_id: str, legacy: bool) -> Dict[str, str]:
    files: Dict[str, str] = {}
    for path in _list_staged_paths(ext_id):
        if legacy:
            if path == "manifest.json" or path.startswith("backend/"):
                files[path] = _read_staged_file(ext_id, path)
            continue
        if path.startswith("backend/") or path == "manifest.json":
            clean = path.removeprefix("backend/") if path.startswith("backend/") else path
            if (
                clean == "manifest.json"
                and path != "manifest.json"
                and "manifest.json" in files
            ):
                continue
            files[clean] = _read_staged_file(ext_id, path)
    return files


def _load_staged_frontend_files(ext_id: str) -> Dict[str, str]:
    files: Dict[str, str] = {}
    for path in _list_staged_paths(ext_id, "frontend/"):
        files[path] = _read_staged_file(ext_id, path)
    return files


def _load_job_state(realm) -> Optional[dict]:
    raw = getattr(realm, "codex_install_state", "") or ""
    if not raw:
        return None
    try:
        state = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return state if isinstance(state, dict) else None


def _save_job_state(realm, state: dict) -> None:
    realm.codex_install_state = json.dumps(state, separators=(",", ":"))[:STATE_MAX_LEN]


def _clear_job(realm, ext_id: str) -> None:
    realm.codex_install_state = ""
    _clear_staging(ext_id)


def _job_matches(
    state: dict,
    ext_id: str,
    registry_canister_id: str,
    version: Optional[str],
) -> bool:
    if not state:
        return False
    if (state.get("ext_id") or "").strip() != (ext_id or "").strip():
        return False
    if (state.get("registry_canister_id") or "").strip() != (
        registry_canister_id or ""
    ).strip():
        return False
    req = (version or "").strip()
    saved = (state.get("requested_version") or "").strip()
    return saved == req


def _new_job(
    ext_id: str,
    registry_canister_id: str,
    version: Optional[str],
    frontend_canister_id: Optional[str],
    install_dependencies: bool,
    owner: Optional[str],
    run_init: bool,
    category: str,
) -> dict:
    return {
        "ext_id": ext_id,
        "registry_canister_id": registry_canister_id,
        "requested_version": (version or "").strip(),
        "frontend_canister_id": (frontend_canister_id or "").strip(),
        "install_dependencies": bool(install_dependencies),
        "owner": owner,
        "run_init": bool(run_init),
        "category": category,
        "phase": "resolve",
        "legacy": False,
        "namespace": "",
        "resolved_version": "",
        "backend_paths": [],
        "backend_index": 0,
        "frontend_paths": [],
        "frontend_index": 0,
        "copy_frontend_index": 0,
        "installed_deps": [],
        "manifest": {},
    }


def _yield_maybe(value):
    if hasattr(value, "send"):
        return (yield from value)
    return value


def _call_phase(phase_fn, *args):
    outcome = phase_fn(*args)
    if hasattr(outcome, "send"):
        return (yield from outcome)
    return outcome


def continue_codex_install(
    registry_canister_id: str,
    ext_id: str,
    version: str = None,
    frontend_canister_id: str = None,
    install_dependencies: bool = True,
    owner: str = None,
    run_init: bool = True,
    category: str = "ext",
) -> Async[str]:
    """Start or advance the realm's Codex install job by one phase."""
    from ggg import Realm

    ext_id = (ext_id or "").strip()
    if not ext_id:
        return json.dumps({"success": False, "error": "extension_id is required"})

    realm = Realm.load("1")
    if not realm:
        return json.dumps({"success": False, "error": "Realm not found"})

    state = _load_job_state(realm)
    if state and not _job_matches(state, ext_id, registry_canister_id, version):
        _clear_job(realm, state.get("ext_id") or ext_id)
        state = None

    if not state:
        _clear_staging(ext_id)
        state = _new_job(
            ext_id,
            registry_canister_id,
            version,
            frontend_canister_id,
            install_dependencies,
            owner,
            run_init,
            category,
        )
        _save_job_state(realm, state)

    phase = (state.get("phase") or "resolve").strip()
    total = len(PHASES)
    done = _phase_index(phase)

    if phase == "resolve":
        raw = yield from _call_phase(_phase_resolve, realm, state)
    elif phase == "pull_backend":
        raw = yield from _call_phase(_phase_pull_backend, realm, state)
    elif phase == "scan":
        raw = yield from _call_phase(_phase_scan, realm, state)
    elif phase == "pull_frontend":
        raw = yield from _call_phase(_phase_pull_frontend, realm, state)
    elif phase == "copy_frontend":
        raw = yield from _call_phase(_phase_copy_frontend, realm, state)
    elif phase == "apply_backend":
        raw = yield from _call_phase(_phase_apply_backend, realm, state)
    elif phase == "overlay":
        raw = yield from _call_phase(_phase_overlay, realm, state)
    elif phase == "init":
        raw = yield from _call_phase(_phase_init, realm, state)
    else:
        return json.dumps({"success": False, "error": f"unknown codex install phase: {phase}"})

    try:
        result = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return json.dumps({"success": False, "error": "invalid phase result"})

    if not isinstance(result, dict):
        return json.dumps({"success": False, "error": str(result)})

    if result.get("success") is False:
        _clear_job(realm, ext_id)
        return json.dumps(result)

    if result.get("status") == "complete":
        _clear_job(realm, ext_id)
        return json.dumps(result)

    if result.get("status") == "in_progress":
        next_state = result.pop("_state", None)
        if isinstance(next_state, dict):
            _save_job_state(realm, next_state)
        return json.dumps(
            _progress_payload(
                result.get("phase") or state.get("phase") or phase,
                int(result.get("done", done)),
                int(result.get("total", total)),
                {k: v for k, v in result.items() if k not in ("phase", "done", "total")},
            )
        )

    return json.dumps(result)


def _advance(state: dict, next_phase: str, extra: Optional[dict] = None) -> dict:
    state = dict(state)
    state["phase"] = next_phase
    payload = _progress_payload(
        next_phase,
        _phase_index(next_phase),
        len(PHASES),
        extra,
    )
    payload["_state"] = state
    return payload


def _phase_resolve(realm, state: dict) -> Async[str]:
    from api.file_registry import (
        FileRegistryService,
        _check_marketplace_approval,
        _entity_method_override_error,
        _format_failed_deps,
        _get_realm_frontend_canister_id,
        _install_codex_dependencies,
        _resolve_codex_dependencies,
        _resolve_registry_namespace,
    )
    from core import codex_hooks

    ext_id = state["ext_id"]
    registry = FileRegistryService(Principal.from_str(state["registry_canister_id"]))
    category = state.get("category") or "ext"
    version = state.get("requested_version") or None

    namespace = ""
    resolved_version = ""
    err_json = None

    if category in ("ext", "auto"):
        namespace, resolved_version, err_json = yield from _resolve_registry_namespace(
            registry, "ext", ext_id, version or ""
        )
        if err_json and category == "auto" and _is_missing_namespace(err_json):
            state["category"] = "codex"
            state["legacy"] = True
            namespace, resolved_version, err_json = yield from _resolve_registry_namespace(
                registry, "codex", ext_id, version or ""
            )
        elif not err_json:
            state["category"] = "ext"
            state["legacy"] = False
    else:
        state["legacy"] = True
        namespace, resolved_version, err_json = yield from _resolve_registry_namespace(
            registry, "codex", ext_id, version or ""
        )

    if err_json:
        return json.dumps({"success": False, "error": _err_text(err_json)})

    refusal = yield from _check_marketplace_approval(registry, namespace)
    if refusal:
        return json.dumps({"success": False, "error": refusal})

    state["namespace"] = namespace
    state["resolved_version"] = resolved_version

    if state.get("legacy"):
        paths = yield from _list_paths(registry, namespace, lambda _p: True)
    else:
        paths = yield from _list_paths(
            registry,
            namespace,
            lambda p: p.startswith("backend/") or p == "manifest.json",
        )

    if not paths:
        label = f"{state.get('category')}/{ext_id}"
        return json.dumps(
            {"success": False, "error": f"No backend files found for {label}"}
        )

    state["backend_paths"] = paths

    manifest = {}
    manifest_path = "manifest.json"
    if manifest_path not in paths and "backend/manifest.json" in paths:
        manifest_path = "backend/manifest.json"
    if manifest_path in paths:
        try:
            manifest_text = yield from _pull_path(registry, namespace, manifest_path)
            manifest = json.loads(manifest_text)
            if not isinstance(manifest, dict):
                manifest = {}
        except (json.JSONDecodeError, TypeError):
            manifest = {}
    state["manifest"] = manifest

    if not state.get("legacy"):
        try:
            from core.access import _check_access
            from ggg.system.user_profile import Operations

            caller = ic.caller().to_str()
            is_internal = caller == ic.id().to_str() or caller == "2vxsx-fae"
            if not is_internal and not _check_access(caller, Operations.CODEX_INSTALL):
                return json.dumps(
                    {
                        "success": False,
                        "error": (
                            f"'{ext_id}' is a codex package; installing it requires "
                            f"the {Operations.CODEX_INSTALL} permission"
                        ),
                    }
                )
        except ImportError:
            pass

        version_error = codex_hooks.unsupported_api_version(manifest)
        if version_error:
            return json.dumps(
                {"success": False, "error": f"Codex '{ext_id}': {version_error}"}
            )
        ggg_version_error = codex_hooks.unsupported_ggg_api_version(manifest)
        if ggg_version_error:
            return json.dumps(
                {"success": False, "error": f"Codex '{ext_id}': {ggg_version_error}"}
            )
        override_error = _entity_method_override_error(ext_id, manifest)
        if override_error:
            return json.dumps({"success": False, "error": override_error})

        conflict = codex_hooks.singleton_violation(ext_id)
        if conflict:
            return json.dumps({"success": False, "error": conflict})

        if state.get("install_dependencies"):
            dependencies = _resolve_codex_dependencies(manifest, ext_id)
            fe = (
                state.get("frontend_canister_id")
                or _get_realm_frontend_canister_id()
                or None
            )
            from api.file_registry import install_extension_from_registry
            from core.runtime_extensions import list_installed

            already = set(list_installed())
            installed_deps = list(state.get("installed_deps") or [])
            for dep, pin in dependencies.items():
                if dep in already:
                    continue
                dep_raw = yield from install_extension_from_registry(
                    state["registry_canister_id"], dep, pin or None, fe
                )
                try:
                    dep_result = json.loads(dep_raw)
                except (json.JSONDecodeError, TypeError):
                    dep_result = {"success": False, "error": dep_raw}
                if dep_result.get("status") == "in_progress":
                    payload = _progress_payload(
                        "resolve",
                        0,
                        len(PHASES),
                        {"waiting_on_dependency": dep},
                    )
                    payload["_state"] = state
                    return json.dumps(payload)
                if dep_result.get("success"):
                    installed_deps.append(
                        {"extension": dep, "version": dep_result.get("version", "")}
                    )
                    already.add(dep)
                else:
                    return json.dumps(
                        {
                            "success": False,
                            "error": _format_failed_deps(
                                ext_id,
                                [
                                    {
                                        "extension": dep,
                                        "pin": pin,
                                        "error": dep_result.get("error", "unknown"),
                                    }
                                ],
                            ),
                            "dependency_warnings": [
                                {
                                    "extension": dep,
                                    "pin": pin,
                                    "error": dep_result.get("error", "unknown"),
                                }
                            ],
                        }
                    )
            state["installed_deps"] = installed_deps
    else:
        conflict = codex_hooks.singleton_violation(
            ext_id.split("/")[-1] if "/" in ext_id else ext_id
        )
        if conflict:
            return json.dumps({"success": False, "error": conflict})
        override_error = _entity_method_override_error(ext_id, manifest)
        if override_error:
            return json.dumps({"success": False, "error": override_error})
        init_py_error = legacy_init_py_error(ext_id, {"manifest.json": json.dumps(manifest)})
        if init_py_error:
            return json.dumps({"success": False, "error": init_py_error})
        if state.get("install_dependencies"):
            dependencies = _resolve_codex_dependencies(manifest, ext_id)
            fe = (
                state.get("frontend_canister_id")
                or _get_realm_frontend_canister_id()
                or None
            )
            from api.file_registry import install_extension_from_registry
            from core.runtime_extensions import list_installed

            already = set(list_installed())
            installed_deps = list(state.get("installed_deps") or [])
            for dep, pin in dependencies.items():
                if dep in already:
                    continue
                dep_raw = yield from install_extension_from_registry(
                    state["registry_canister_id"], dep, pin or None, fe
                )
                try:
                    dep_result = json.loads(dep_raw)
                except (json.JSONDecodeError, TypeError):
                    dep_result = {"success": False, "error": dep_raw}
                if dep_result.get("status") == "in_progress":
                    payload = _progress_payload(
                        "resolve",
                        0,
                        len(PHASES),
                        {"waiting_on_dependency": dep},
                    )
                    payload["_state"] = state
                    return json.dumps(payload)
                if dep_result.get("success"):
                    installed_deps.append(
                        {"extension": dep, "version": dep_result.get("version", "")}
                    )
                    already.add(dep)
                else:
                    return json.dumps(
                        {
                            "success": False,
                            "error": _format_failed_deps(
                                ext_id,
                                [
                                    {
                                        "extension": dep,
                                        "pin": pin,
                                        "error": dep_result.get("error", "unknown"),
                                    }
                                ],
                            ),
                        }
                    )
            state["installed_deps"] = installed_deps

    fe_paths = yield from _list_paths(
        registry, namespace, lambda p: p.startswith("frontend/")
    )
    state["frontend_paths"] = fe_paths
    state["backend_index"] = 0
    state["frontend_index"] = 0
    state["copy_frontend_index"] = 0

    return json.dumps(_advance(state, "pull_backend"))


def _phase_pull_backend(realm, state: dict) -> Async[str]:
    from api.file_registry import FileRegistryService

    ext_id = state["ext_id"]
    registry = FileRegistryService(Principal.from_str(state["registry_canister_id"]))
    namespace = state["namespace"]
    paths = state.get("backend_paths") or []
    index = int(state.get("backend_index") or 0)

    pulled = 0
    pulled_bytes = 0
    while index < len(paths) and pulled < MAX_PULL_FILES:
        if pulled_bytes >= MAX_PULL_BYTES:
            break
        path = paths[index]
        try:
            content = yield from _yield_maybe(
                _pull_path(registry, namespace, path)
            )
        except Exception as exc:
            return json.dumps(
                {"success": False, "error": f"pull {path}: {exc}"}
            )
        _write_staged_file(ext_id, path, content)
        pulled_bytes += len(content.encode("utf-8"))
        pulled += 1
        index += 1

    state["backend_index"] = index
    if index < len(paths):
        state["phase"] = "pull_backend"
        payload = _progress_payload(
            "pull_backend",
            index,
            len(paths),
            {"files_pulled": index},
        )
        payload["_state"] = state
        return json.dumps(payload)

    return json.dumps(_advance(state, "scan", {"backend_files": len(paths)}))


def _phase_scan(realm, state: dict) -> Async[str]:
    from core import codex_hooks, codex_scan
    from core.runtime_codex import legacy_init_py_error

    ext_id = state["ext_id"]
    manifest = state.get("manifest") or {}
    legacy = bool(state.get("legacy"))
    files = _load_staged_backend_files(ext_id, legacy)

    init_py_error = legacy_init_py_error(ext_id, files)
    if init_py_error:
        return json.dumps({"success": False, "error": init_py_error})

    if not legacy:
        try:
            scan_error = codex_scan.check_codex_imports(
                ext_id, files, enforce=codex_hooks.declares_ggg_api(manifest)
            )
        except Exception as scan_err:
            logger.error(f"codex_scan[{ext_id}] failed, skipping scan: {scan_err}")
            scan_error = ""
        if scan_error:
            return json.dumps({"success": False, "error": scan_error})

    next_phase = "pull_frontend" if (state.get("frontend_paths") or []) else "apply_backend"
    return json.dumps(_advance(state, next_phase))


def _phase_pull_frontend(realm, state: dict) -> Async[str]:
    from api.file_registry import FileRegistryService

    ext_id = state["ext_id"]
    paths = state.get("frontend_paths") or []
    if not paths:
        return json.dumps(_advance(state, "apply_backend"))

    registry = FileRegistryService(Principal.from_str(state["registry_canister_id"]))
    namespace = state["namespace"]
    index = int(state.get("frontend_index") or 0)

    pulled = 0
    pulled_bytes = 0
    while index < len(paths) and pulled < MAX_PULL_FILES:
        if pulled_bytes >= MAX_PULL_BYTES:
            break
        path = paths[index]
        try:
            content = yield from _yield_maybe(
                _pull_path(registry, namespace, path)
            )
        except Exception as exc:
            return json.dumps(
                {"success": False, "error": f"pull {path}: {exc}"}
            )
        _write_staged_file(ext_id, path, content)
        pulled_bytes += len(content.encode("utf-8"))
        pulled += 1
        index += 1

    state["frontend_index"] = index
    if index < len(paths):
        payload = _progress_payload(
            "pull_frontend",
            index,
            len(paths),
            {"files_pulled": index},
        )
        payload["_state"] = state
        return json.dumps(payload)

    return json.dumps(_advance(state, "copy_frontend"))


def _phase_copy_frontend(realm, state: dict) -> Async[str]:
    from api.file_registry import _copy_frontend_to_asset_canister, _get_realm_frontend_canister_id

    ext_id = state["ext_id"]
    fe_paths = state.get("frontend_paths") or []
    if not fe_paths:
        return json.dumps(_advance(state, "apply_backend"))

    from ggg import Realm as _Realm

    _realm = _Realm.load("1")
    if bool(getattr(_realm, "is_quarter", False)):
        return json.dumps(_advance(state, "apply_backend"))

    fe_canister = (
        state.get("frontend_canister_id") or _get_realm_frontend_canister_id() or ""
    ).strip()
    if not fe_canister:
        return json.dumps(
            {
                "success": False,
                "error": (
                    f"Extension '{ext_id}' has frontend files but no frontend_canister_id "
                    f"is configured on this realm"
                ),
            }
        )

    files = _load_staged_frontend_files(ext_id)
    paths = sorted(files.keys())
    index = int(state.get("copy_frontend_index") or 0)
    if index >= len(paths):
        return json.dumps(_advance(state, "apply_backend"))

    # Copy one frontend asset per update to stay under the instruction budget.
    batch = {paths[index]: files[paths[index]]}
    copy_err = yield from _copy_frontend_to_asset_canister(
        state["registry_canister_id"],
        ext_id,
        state.get("resolved_version") or "",
        fe_canister,
        files=batch,
    )
    if copy_err:
        return json.dumps({"success": False, "error": copy_err})

    state["copy_frontend_index"] = index + 1
    if index + 1 < len(paths):
        payload = _progress_payload(
            "copy_frontend",
            index + 1,
            len(paths),
            {"files_copied": index + 1},
        )
        payload["_state"] = state
        return json.dumps(payload)

    return json.dumps(_advance(state, "apply_backend", {"frontend_files_copied": len(paths)}))


def _phase_apply_backend(realm, state: dict) -> Async[str]:
    ext_id = state["ext_id"]
    resolved_version = state.get("resolved_version") or ""
    registry_id = state["registry_canister_id"]
    owner = state.get("owner")

    if state.get("legacy"):
        from core.runtime_codex import install_codex_package

        files = _load_staged_backend_files(ext_id, True)
        package_name = ext_id.split("/")[-1] if "/" in ext_id else ext_id
        ok = install_codex_package(package_name, files)
        if not ok:
            return json.dumps(
                {
                    "success": False,
                    "error": f"Failed to install codex package '{package_name}'",
                }
            )
        state["package_name"] = package_name
        return json.dumps(_advance(state, "overlay"))

    from core.codex_overlay import preserve_current_as_previous, wipe_runtime_package
    from core.runtime_extensions import install_extension as _install

    files = _load_staged_backend_files(ext_id, False)
    preserve_current_as_previous()
    wipe_runtime_package(ext_id)
    ok = _install(
        ext_id,
        files,
        source_registry_id=registry_id,
        source_version=resolved_version,
        owner=owner,
    )
    if not ok:
        return json.dumps(
            {
                "success": False,
                "error": f"Failed to load extension '{ext_id}' after install",
            }
        )
    return json.dumps(_advance(state, "overlay"))


def _phase_overlay(realm, state: dict) -> Async[str]:
    from api.file_registry import _seed_codex_module_entities
    from core.codex_overlay import commit_current, ensure_codex_revert_grants

    ext_id = state["ext_id"]
    manifest = state.get("manifest") or {}
    legacy = bool(state.get("legacy"))
    files = _load_staged_backend_files(ext_id, legacy)
    package_name = state.get("package_name") or ext_id

    seeded_modules = _seed_codex_module_entities(package_name, files, manifest)
    state["seeded_modules"] = seeded_modules

    if not legacy:
        commit_current(ext_id, files, seeded_modules)
        try:
            ensure_codex_revert_grants()
        except Exception:
            pass

    next_phase = "init" if state.get("run_init") else "complete"
    if next_phase == "complete":
        return json.dumps(_complete_result(state))
    state["phase"] = "init"
    payload = _advance(state, "init")
    return json.dumps(payload)


def _phase_init(realm, state: dict) -> Async[str]:
    from core import codex_hooks

    ext_id = state.get("package_name") or state["ext_id"]
    init_error = codex_hooks.run_init(ext_id)
    result = _complete_result(state)
    if init_error:
        result["init_warning"] = init_error
    result["init_ran"] = True
    return json.dumps(result)


def _complete_result(state: dict) -> dict:
    ext_id = state["ext_id"]
    resolved_version = state.get("resolved_version") or ""
    legacy = bool(state.get("legacy"))
    files = _load_staged_backend_files(ext_id, legacy)
    result = {
        "success": True,
        "status": "complete",
        "version": resolved_version,
        "files_count": len(files),
        "source": "registry",
        "registry_canister_id": state["registry_canister_id"],
        "kind": "codex",
        "dependencies_installed": state.get("installed_deps") or [],
        "codex_modules": state.get("seeded_modules") or [],
        "init_ran": bool(state.get("run_init")),
    }
    if legacy:
        result["codex_id"] = ext_id
        result["package_name"] = state.get("package_name") or ext_id
    else:
        result["extension_id"] = ext_id
        result["frontend_files_copied"] = len(state.get("frontend_paths") or [])
    return result


# ── registry helpers (thin wrappers to keep ICC in this module) ─────────────


def _err_text(err_json: str) -> str:
    try:
        obj = json.loads(err_json)
        if isinstance(obj, dict):
            return str(obj.get("error") or err_json)
    except (json.JSONDecodeError, TypeError):
        pass
    return str(err_json)


def _is_missing_namespace(err_json: str) -> bool:
    err = _err_text(err_json).lower()
    return (
        "no published version" in err
        or "not found" in err
        or "no versions found" in err
        or "no backend files" in err
    )


def _list_paths(registry, namespace: str, path_filter) -> Async[List[str]]:
    from api.file_registry import _list_namespace_paths

    paths = yield from _list_namespace_paths(registry, namespace)
    return [p for p in paths if path_filter(p)]


def _pull_path(registry, namespace: str, path: str) -> Async[str]:
    from api.file_registry import _pull_namespace_file_text

    return (yield from _pull_namespace_file_text(registry, namespace, path))
