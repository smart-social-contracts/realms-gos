"""Codex overlay slots, safe mode, and revert (issue #328).

A realm has one codex. An update (official version or an ad-hoc fork) is
replace, not merge: the ``Codex`` table is brought in line with the new
package allow-list, and leftovers are deleted.

Two filesystem slots hold the overlay packages (files + manifest + hash):

  /codex_slots/current/
  /codex_slots/previous/

Before activate, current is copied to previous. ``get_active_codex()``
points at the current slot when one exists.

This is the overlay recovery layer only. WASM unbrick stays ICP snapshot /
leftover-free and is not handled here.

Safe mode stops the host from calling codex hooks. It does not wipe users,
departments, or cases.

Unbrick is a system operation: ``revert`` / ``set_safe_mode`` are callable
from the core System UI and from ``__shell__`` via ``api.call`` or
``from core.codex_overlay import revert, set_safe_mode``.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Dict, List, Optional

from ic_python_logging import get_logger

logger = get_logger("core.codex_overlay")

SLOTS_DIR = "/codex_slots"
STATE_NAME = "state.json"
SLOT_META_NAME = "_slot.json"
KEEP_RUNTIME_FILES = frozenset({"_source.json"})
PROPOSAL_CODEX_PREFIX = "proposal_"

# Voting and System are core. A codex must not replace them via
# extension_overrides (issue #328). ``system`` is the core System UI id;
# ``system_info`` is the closest extension name and is also reserved.
PROTECTED_OVERRIDE_BASES = frozenset({"voting", "system", "system_info"})

CONGRESS_DEPT_NAMES = frozenset({"congress"})


def _state_path() -> str:
    return os.path.join(SLOTS_DIR, STATE_NAME)


def _slot_dir(slot: str) -> str:
    return os.path.join(SLOTS_DIR, slot)


def _ensure_slots_dir() -> None:
    os.makedirs(SLOTS_DIR, exist_ok=True)


def _rmtree(path: str) -> None:
    """Remove a directory tree. ``os.walk`` is unavailable on CPython WASM."""
    if not os.path.exists(path):
        return
    if os.path.isfile(path):
        os.remove(path)
        return
    for item in os.listdir(path):
        item_path = os.path.join(path, item)
        if os.path.isdir(item_path):
            _rmtree(item_path)
        else:
            os.remove(item_path)
    os.rmdir(path)


def _read_dir_files(root: str) -> Dict[str, str]:
    files: Dict[str, str] = {}

    def walk(prefix: str, dirpath: str) -> None:
        try:
            names = os.listdir(dirpath)
        except OSError:
            return
        for item in names:
            if item in (SLOT_META_NAME,):
                continue
            path = os.path.join(dirpath, item)
            rel = f"{prefix}{item}" if prefix else item
            if os.path.isdir(path):
                walk(rel + "/", path)
            else:
                try:
                    with open(path, "r") as handle:
                        files[rel] = handle.read()
                except Exception as exc:
                    logger.warning(f"codex overlay: skip unreadable {rel}: {exc}")

    if os.path.exists(root):
        walk("", root)
    return files


def _write_dir_files(root: str, files: Dict[str, str]) -> None:
    _rmtree(root)
    os.makedirs(root, exist_ok=True)
    for filename, content in files.items():
        filepath = os.path.join(root, filename)
        dirpath = os.path.dirname(filepath)
        if dirpath and dirpath != root:
            os.makedirs(dirpath, exist_ok=True)
        with open(filepath, "w") as handle:
            handle.write(content if content is not None else "")


def _load_state() -> dict:
    path = _state_path()
    if not os.path.exists(path):
        return {"safe_mode": False}
    try:
        with open(path, "r") as handle:
            raw = json.loads(handle.read())
        return raw if isinstance(raw, dict) else {"safe_mode": False}
    except Exception as exc:
        logger.warning(f"codex overlay: failed to load state — {exc}")
        return {"safe_mode": False}


def _save_state(state: dict) -> None:
    _ensure_slots_dir()
    with open(_state_path(), "w") as handle:
        handle.write(json.dumps(state))


def package_hash(files: Dict[str, str]) -> str:
    """Stable sha256 of a package file map (paths + contents)."""
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update((files.get(path) or "").encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _manifest_of(files: Dict[str, str]) -> dict:
    try:
        raw = json.loads(files.get("manifest.json") or "{}")
        return raw if isinstance(raw, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _slot_files(slot: str) -> Dict[str, str]:
    return _read_dir_files(_slot_dir(slot))


def _slot_meta(slot: str) -> dict:
    path = os.path.join(_slot_dir(slot), SLOT_META_NAME)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as handle:
            raw = json.loads(handle.read())
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _write_slot(slot: str, ext_id: str, files: Dict[str, str], modules: List[str]) -> dict:
    stored = {path: content for path, content in files.items() if path != SLOT_META_NAME}
    _write_dir_files(_slot_dir(slot), stored)
    manifest = _manifest_of(stored)
    meta = {
        "id": ext_id,
        "hash": package_hash(stored),
        "modules": list(modules),
        "version": str(manifest.get("version") or ""),
        "name": str(manifest.get("name") or ext_id),
    }
    with open(os.path.join(_slot_dir(slot), SLOT_META_NAME), "w") as handle:
        handle.write(json.dumps(meta))
    return meta


def _copy_slot(src: str, dest: str) -> bool:
    files = _slot_files(src)
    if not files:
        return False
    meta = _slot_meta(src)
    _write_dir_files(_slot_dir(dest), files)
    if meta:
        with open(os.path.join(_slot_dir(dest), SLOT_META_NAME), "w") as handle:
            handle.write(json.dumps(meta))
    return True


def _live_codex_snapshot() -> tuple:
    """``(ext_id, files)`` from a live kind=codex install, or ``("", {})``."""
    for base in ("/extensions", "/codex_packages"):
        if not os.path.exists(base):
            continue
        try:
            names = os.listdir(base)
        except OSError:
            continue
        for item in names:
            item_path = os.path.join(base, item)
            if not os.path.isdir(item_path) or item.startswith("."):
                continue
            manifest_path = os.path.join(item_path, "manifest.json")
            if not os.path.exists(manifest_path):
                continue
            try:
                with open(manifest_path, "r") as handle:
                    manifest = json.loads(handle.read())
            except Exception:
                continue
            if not isinstance(manifest, dict):
                continue
            if manifest.get("kind") != "codex" and base != "/codex_packages":
                continue
            files = _read_dir_files(item_path)
            files.pop("_source.json", None)
            files.pop(SLOT_META_NAME, None)
            if files:
                return item, files
    return "", {}


def wipe_runtime_package(ext_id: str) -> None:
    """Remove leftover files under ``/extensions/{id}`` before a replace."""
    if not ext_id:
        return
    _rmtree(f"/extensions/{ext_id}")


def _invalidate_hooks() -> None:
    try:
        from core.codex_hooks import invalidate_cache

        invalidate_cache()
    except Exception:
        pass


def filter_protected_overrides(overrides: dict) -> dict:
    """Drop overrides that would replace voting or System."""
    if not isinstance(overrides, dict):
        return {}
    cleaned = {}
    for base, override in overrides.items():
        key = str(base)
        if key in PROTECTED_OVERRIDE_BASES:
            logger.warning(
                f"Ignoring extension_overrides[{key!r}] — voting/System are core"
            )
            continue
        if base and override and isinstance(override, str):
            cleaned[key] = override
    return cleaned


def is_safe_mode() -> bool:
    return bool(_load_state().get("safe_mode"))


def active_codex_id() -> Optional[str]:
    """Extension id of the current overlay slot, or None when empty."""
    files = _slot_files("current")
    if not files:
        return None
    meta = _slot_meta("current")
    ext_id = str(meta.get("id") or "").strip()
    return ext_id or None


def current_modules() -> List[str]:
    meta = _slot_meta("current")
    raw = meta.get("modules") if meta else None
    if not isinstance(raw, list):
        return []
    return [str(name) for name in raw if isinstance(name, str) and name]


def status() -> dict:
    """JSON-ready overlay status for System UI / shell."""
    state = _load_state()
    current = _slot_meta("current") if _slot_files("current") else None
    previous = _slot_meta("previous") if _slot_files("previous") else None
    return {
        "success": True,
        "safe_mode": bool(state.get("safe_mode")),
        "active": "current" if current else None,
        "current": current,
        "previous": previous,
        "has_previous": bool(previous),
    }


def _is_task_manager_shim(name: str) -> bool:
    """TaskManager stores per-step scripts as ``_<task>_step_<idx>_<ts>`` Codex rows."""
    return bool(name) and name.startswith("_") and "_step_" in name


def prune_codex_table(claimed: List[str]) -> List[str]:
    """Delete Codex rows the new package does not claim.

    ``proposal_*`` rows are reserved for voting code-execution and are kept.
    TaskManager shim rows (``_…_step_…``) are never deleted here.
    An empty claimed list is treated as a failed/empty seed, not as
    "claim nothing and wipe the table" — skip prune entirely.
    Users / departments / cases are not touched.
    """
    deleted = []
    claimed_set = {name for name in claimed if name}
    if not claimed_set:
        logger.warning(
            "codex overlay: skip Codex prune — claimed module list is empty"
        )
        return deleted

    try:
        from ggg import Codex
    except Exception as exc:
        logger.warning(f"codex overlay: cannot prune Codex table — {exc}")
        return deleted

    try:
        rows = list(Codex.instances())
    except Exception as exc:
        logger.warning(f"codex overlay: Codex.instances failed — {exc}")
        return deleted

    for row in rows:
        name = getattr(row, "name", None) or ""
        if not name or name.startswith(PROPOSAL_CODEX_PREFIX):
            continue
        if _is_task_manager_shim(name):
            continue
        if name in claimed_set:
            continue
        try:
            row.delete()
            deleted.append(name)
            logger.info(f"Codex '{name}': pruned (not in new allow-list)")
        except Exception as exc:
            logger.error(f"Codex '{name}': prune failed — {exc}")
    return deleted


def _seed_claimed_modules(files: Dict[str, str], modules: List[str]) -> List[str]:
    """Create/update Codex rows for the claimed stems only."""
    seeded = []
    try:
        from ggg import Codex
    except Exception as exc:
        logger.warning(f"codex overlay: cannot seed Codex rows — {exc}")
        return seeded

    from core.runtime_codex import _codex_module_stem

    available = {}
    for path, content in files.items():
        name = _codex_module_stem(path)
        if name:
            available[name] = content

    for name in modules:
        content = available.get(name)
        if content is None:
            continue
        try:
            existing = Codex[name]
            if existing:
                existing.code = content
            else:
                Codex(name=name, code=content)
            seeded.append(name)
        except Exception as exc:
            logger.error(f"Codex '{name}': seed-on-revert failed — {exc}")
    return seeded


def preserve_current_as_previous() -> bool:
    """Copy current → previous. Call *before* a new package overwrites disk.

    If the current slot is empty (first overlay-aware install), snapshot a
    live kind=codex install so revert still has a previous package.
    """
    if not _slot_files("current"):
        live_id, live_files = _live_codex_snapshot()
        if live_id and live_files:
            from core.runtime_codex import _codex_module_stem

            modules = [
                name
                for name in (_codex_module_stem(path) for path in live_files)
                if name
            ]
            _write_slot("current", live_id, live_files, modules)
            logger.info(f"codex overlay: captured live '{live_id}' as current before replace")
    if not _slot_files("current"):
        return False
    copied = _copy_slot("current", "previous")
    if copied:
        logger.info("codex overlay: current copied to previous")
    return copied


def commit_current(ext_id: str, files: Dict[str, str], modules: List[str]) -> dict:
    """Record the newly installed package as current and prune leftovers."""
    meta = _write_slot("current", ext_id, files, list(modules))
    deleted = prune_codex_table(modules)
    try:
        from core.package_manager import record_install

        record_install(
            ext_id,
            kind="codex",
            version=str(meta.get("version") or ""),
            package_hash=str(meta.get("hash") or ""),
            claimed=list(modules),
        )
    except Exception as exc:
        logger.warning(f"codex overlay: package record failed — {exc}")
    _invalidate_hooks()
    logger.info(
        f"codex overlay: current={ext_id} hash={meta.get('hash', '')[:12]} "
        f"modules={modules} pruned={deleted}"
    )
    return {"meta": meta, "pruned": deleted}


def activate(ext_id: str, files: Dict[str, str], modules: List[str]) -> dict:
    """Snapshot current → previous, then commit ``files`` as current.

    Used by unit tests and any activate path that does not go through
    ``install_extension`` (which must snapshot *before* overwriting).
    """
    preserve_current_as_previous()
    return commit_current(ext_id, files, modules)


def _caller_may_revert() -> bool:
    """True when the live caller holds ``codex.revert`` (or a bypass)."""
    try:
        from _cdk import ic
        from core.access import _check_access
        from ggg.system.user_profile import Operations

        return bool(_check_access(ic.caller().to_str(), Operations.CODEX_REVERT))
    except Exception as exc:
        logger.warning(f"codex overlay: access check failed — {exc}")
        return False


def _denied(error: str) -> dict:
    return {"success": False, "error": error}


def revert(*, authorized_by_vote: bool = False) -> dict:
    """Flip the overlay to the previous package.

    Requires ``codex.revert`` unless ``authorized_by_vote`` (passed core
    vote, same pattern as upgrade → ``codex.install``). Does not call
    codex hooks.
    """
    if not authorized_by_vote and not _caller_may_revert():
        return _denied("codex.revert permission is required")

    previous_files = _slot_files("previous")
    previous_meta = _slot_meta("previous")
    if not previous_files or not previous_meta.get("id"):
        return _denied("no previous codex to revert to")

    # Swap slots so the restored package is current and the failed one
    # becomes previous (revert-the-revert).
    tmp = os.path.join(SLOTS_DIR, "_swap")
    _rmtree(tmp)
    if os.path.exists(_slot_dir("current")):
        os.rename(_slot_dir("current"), tmp)
    if os.path.exists(_slot_dir("previous")):
        os.rename(_slot_dir("previous"), _slot_dir("current"))
    if os.path.exists(tmp):
        os.rename(tmp, _slot_dir("previous"))

    restored_id = str(previous_meta.get("id") or "")
    restored_modules = [
        str(name)
        for name in (previous_meta.get("modules") or [])
        if isinstance(name, str) and name
    ]
    apply_error = _apply_to_runtime(restored_id, previous_files)
    _seed_claimed_modules(previous_files, restored_modules)
    pruned = prune_codex_table(restored_modules)
    try:
        from core.package_manager import record_install

        record_install(
            restored_id,
            kind="codex",
            version=str(previous_meta.get("version") or ""),
            package_hash=str(previous_meta.get("hash") or ""),
            claimed=list(restored_modules),
        )
    except Exception as exc:
        logger.warning(f"codex overlay: package record on revert failed — {exc}")
    _invalidate_hooks()
    result = {
        "success": apply_error is None,
        "codex_id": restored_id,
        "hash": previous_meta.get("hash"),
        "modules": restored_modules,
        "pruned": pruned,
    }
    if apply_error:
        result["error"] = apply_error
        result["success"] = False
    else:
        logger.info(f"codex overlay: reverted to '{restored_id}'")
    return result


def _apply_to_runtime(ext_id: str, files: Dict[str, str]) -> Optional[str]:
    """Install slot files onto the runtime extension path. No hook calls."""
    if not ext_id:
        return "previous slot is missing a codex id"
    try:
        from core.runtime_extensions import (
            get_all_extension_manifests,
            install_extension,
            uninstall_extension,
        )
    except Exception as exc:
        return f"runtime install unavailable: {exc}"

    try:
        for other, manifest in get_all_extension_manifests().items():
            if other == ext_id:
                continue
            if isinstance(manifest, dict) and manifest.get("kind") == "codex":
                uninstall_extension(other)
    except Exception as exc:
        logger.warning(f"codex overlay: could not remove other codex — {exc}")

    wipe_runtime_package(ext_id)
    ok = install_extension(ext_id, files, bypass_lock=True)
    if not ok:
        return f"failed to load restored codex '{ext_id}'"
    return None


def set_safe_mode(enabled: bool, *, authorized_by_vote: bool = False) -> dict:
    """Enable or disable hook-skipping safe mode. Same permission as revert."""
    if not authorized_by_vote and not _caller_may_revert():
        return _denied("codex.revert permission is required")
    state = _load_state()
    state["safe_mode"] = bool(enabled)
    _save_state(state)
    _invalidate_hooks()
    logger.info(f"codex overlay: safe_mode={bool(enabled)}")
    return {"success": True, "safe_mode": bool(enabled)}


def ensure_codex_revert_grants() -> None:
    """Host grant path: attach ``codex.revert`` to root and Congress.

    Profile baselines (admin = all, legislator = listed) are the other
    grant path. Syntropia/Agora department JSON lives in realms-codices
    and should list this permission there as a follow-up; this function
    still grants when a department named Congress (or root) exists.
    """
    try:
        from ggg import Department, Permission
        from ggg.system.user_profile import OPERATIONS_CATALOG
    except Exception as exc:
        logger.warning(f"codex overlay: cannot grant codex.revert — {exc}")
        return

    perm = None
    try:
        perm = Permission["codex.revert"]
    except Exception:
        perm = None
    if not perm:
        meta = OPERATIONS_CATALOG.get("codex.revert") or {}
        perm = Permission(
            name="codex.revert",
            description=meta.get("description", "Revert the realm codex overlay"),
            category=meta.get("category", "Codex"),
        )

    try:
        departments = list(Department.instances())
    except Exception:
        return

    for dept in departments:
        name = (getattr(dept, "name", "") or "").strip().lower()
        if not (
            getattr(dept, "is_root", False)
            or name == "root"
            or name in CONGRESS_DEPT_NAMES
        ):
            continue
        try:
            existing = list(getattr(dept, "permissions", None) or [])
            if perm in existing:
                continue
            dept.permissions.add(perm)
            logger.info(f"Granted codex.revert to department '{dept.name}'")
        except Exception as exc:
            logger.warning(
                f"codex overlay: grant to department {getattr(dept, 'name', '?')!r} failed — {exc}"
            )
