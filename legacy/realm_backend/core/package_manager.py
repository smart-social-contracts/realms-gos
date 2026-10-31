"""Installed package records: owner, lock, and replace-on-install (issue #351).

#328 shipped Codex current/previous + revert. This module is the first
slice of a general package manager:

  * One record per installed package id: id, kind (codex|extension),
    version, hash, owner, locked, installed_at.
  * Re-installing an already-installed id **replaces**: leftover files
    and table rows/stems the new package does not claim are deleted.
  * Locked + non-owner (and not root / Congress / ``codex.revert``)
    denies replace. Unlocked: the existing installer permission is enough.
  * Owner can lock, unlock, and transfer (principal or department).
  * One Codex per realm (enforced elsewhere). Extensions replace per id.

User / Organization / Case (and other host types) are never pruned.
WASM leftover-free / rebuild is out of scope.

Callable from System and from ``__shell__`` via ``api.call`` or
``from core.package_manager import ...``.
"""

from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Optional

from ic_python_logging import get_logger

logger = get_logger("core.package_manager")

PACKAGES_DIR = "/packages"
INDEX_NAME = "index.json"

KINDS = frozenset({"codex", "extension"})
KEEP_RUNTIME_FILES = frozenset({"_source.json", "_slot.json"})

# Host brain types. Replace-prune must never delete these rows, even if a
# package lists a colliding stem. Codex rows are pruned only by the
# overlay allow-list (issue #328), never through this set.
PROTECTED_HOST_TYPES = frozenset(
    {
        "User",
        "Organization",
        "Case",
        "Department",
        "Human",
        "Identity",
        "Member",
        "Realm",
        "Proposal",
        "Vote",
        "Court",
        "Judge",
        "Verdict",
        "Appeal",
        "Penalty",
        "Dispute",
    }
)

CONGRESS_DEPT_NAMES = frozenset({"congress"})


def _index_path() -> str:
    return os.path.join(PACKAGES_DIR, INDEX_NAME)


def _ensure_dir() -> None:
    os.makedirs(PACKAGES_DIR, exist_ok=True)


def _load_index() -> dict:
    path = _index_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as handle:
            raw = json.loads(handle.read())
        return raw if isinstance(raw, dict) else {}
    except Exception as exc:
        logger.warning(f"package manager: failed to load index — {exc}")
        return {}


def _save_index(index: dict) -> None:
    _ensure_dir()
    with open(_index_path(), "w") as handle:
        handle.write(json.dumps(index))


def _now() -> int:
    try:
        from _cdk import ic

        ns = ic.time()
        return int(ns // 1_000_000_000)
    except Exception:
        return int(time.time())


def _caller_principal() -> str:
    try:
        from _cdk import ic

        return ic.caller().to_str()
    except Exception:
        return ""


def _public(record: dict) -> dict:
    """The issue #351 package record fields (plus claimed for prune)."""
    return {
        "id": str(record.get("id") or ""),
        "kind": str(record.get("kind") or ""),
        "version": str(record.get("version") or ""),
        "hash": str(record.get("hash") or ""),
        "owner": str(record.get("owner") or ""),
        "locked": bool(record.get("locked")),
        "installed_at": int(record.get("installed_at") or 0),
        "claimed": list(record.get("claimed") or []),
    }


def get_package(package_id: str) -> Optional[dict]:
    if not package_id:
        return None
    raw = _load_index().get(package_id)
    if not isinstance(raw, dict):
        return None
    record = dict(raw)
    record["id"] = package_id
    return record


def _write_package(record: dict) -> dict:
    package_id = str(record.get("id") or "").strip()
    if not package_id:
        raise ValueError("package id is required")
    index = _load_index()
    stored = _public(record)
    stored["id"] = package_id
    index[package_id] = stored
    _save_index(index)
    return stored


def drop_package(package_id: str) -> bool:
    """Remove a record (extension uninstall). Codex is not uninstalled."""
    if not package_id:
        return False
    index = _load_index()
    if package_id not in index:
        return False
    index.pop(package_id, None)
    _save_index(index)
    return True


def _is_owner(record: dict, principal: Optional[str] = None) -> bool:
    owner = (record.get("owner") or "").strip()
    principal = (principal if principal is not None else _caller_principal()).strip()
    if not owner or not principal:
        return False
    if principal == owner:
        return True
    try:
        from core.membership import user_department_names, user_in_department
        from ggg import User

        user = User[principal]
        if not user:
            return False
        if user_in_department(user, owner):
            return True
        return owner.lower() in {name.lower() for name in user_department_names(user)}
    except Exception:
        return False


def _caller_in_root_or_congress(principal: str) -> bool:
    if not principal:
        return False
    try:
        from ggg import User

        user = User[principal]
        if not user:
            return False
        for dept in user.departments:
            name = (getattr(dept, "name", "") or "").strip().lower()
            if getattr(dept, "is_root", False) or name == "root" or name in CONGRESS_DEPT_NAMES:
                return True
    except Exception:
        return False
    return False


def _caller_is_bypass() -> bool:
    """Root / Congress / ``codex.revert`` (same bypass as issue #328)."""
    principal = _caller_principal()
    try:
        from core.access import _check_access
        from ggg.system.user_profile import Operations

        if principal and _check_access(principal, Operations.CODEX_REVERT):
            return True
    except Exception as exc:
        logger.warning(f"package manager: bypass access check failed — {exc}")
    return _caller_in_root_or_congress(principal)


def _may_manage(record: dict) -> bool:
    return _is_owner(record) or _caller_is_bypass()


def replace_denied(package_id: str) -> Optional[str]:
    """Error string when a replace of ``package_id`` must be refused.

    First install (no record) and unlocked packages are allowed — the
    Candid ``@require`` installer permission is the other gate.
    Locked: only owner or root / Congress / ``codex.revert``.
    """
    record = get_package(package_id)
    if record is None:
        return None
    if not record.get("locked"):
        return None
    if _is_owner(record) or _caller_is_bypass():
        return None
    return (
        f"Package '{package_id}' is locked; only the owner "
        f"(or root / Congress / codex.revert) may replace it"
    )


def record_install(
    package_id: str,
    *,
    kind: str,
    version: str = "",
    package_hash: str = "",
    claimed: Optional[List[str]] = None,
    transfer_owner: Optional[str] = None,
) -> dict:
    """Upsert the package record after a successful install.

    Keeps owner + lock unless ``transfer_owner`` is set and the caller
    may manage the package (or this is the first install).
    """
    if not package_id:
        raise ValueError("package id is required")
    kind = "codex" if kind == "codex" else "extension"
    existing = get_package(package_id)
    owner = (existing or {}).get("owner") or ""
    locked = bool((existing or {}).get("locked"))
    if existing is None:
        locked = False
    if not owner.strip():
        owner = _caller_principal()
    new_owner = (transfer_owner or "").strip()
    if new_owner:
        if existing is None or not (existing.get("owner") or "").strip() or _may_manage(existing):
            owner = new_owner
        else:
            logger.warning(
                f"package manager: ignoring transfer_owner on '{package_id}' "
                f"(caller is not owner/bypass)"
            )
    record = {
        "id": package_id,
        "kind": kind,
        "version": str(version or ""),
        "hash": str(package_hash or ""),
        "owner": owner,
        "locked": locked,
        "installed_at": _now(),
        "claimed": [str(name) for name in (claimed or []) if name],
    }
    stored = _write_package(record)
    logger.info(
        f"package manager: recorded {kind} '{package_id}' "
        f"v={stored['version']} locked={stored['locked']} "
        f"owner={stored['owner'][:16]}"
    )
    return stored


def lock_package(package_id: str) -> dict:
    record = get_package(package_id)
    if not record:
        return {"success": False, "error": f"package '{package_id}' is not installed"}
    if not _may_manage(record):
        return {
            "success": False,
            "error": "only the owner (or root / Congress / codex.revert) may lock",
        }
    record["locked"] = True
    return {"success": True, "package": _write_package(record)}


def unlock_package(package_id: str) -> dict:
    record = get_package(package_id)
    if not record:
        return {"success": False, "error": f"package '{package_id}' is not installed"}
    if not _may_manage(record):
        return {
            "success": False,
            "error": "only the owner (or root / Congress / codex.revert) may unlock",
        }
    record["locked"] = False
    return {"success": True, "package": _write_package(record)}


def transfer_package(package_id: str, new_owner: str) -> dict:
    record = get_package(package_id)
    if not record:
        return {"success": False, "error": f"package '{package_id}' is not installed"}
    if not _may_manage(record):
        return {
            "success": False,
            "error": "only the owner (or root / Congress / codex.revert) may transfer",
        }
    owner = (new_owner or "").strip()
    if not owner:
        return {"success": False, "error": "new owner (principal or department) is required"}
    record["owner"] = owner
    return {"success": True, "package": _write_package(record)}


def _hydrate_missing() -> None:
    """Register live installs that predate package records (unlocked, no owner)."""
    index = _load_index()
    changed = False

    def _ensure(package_id: str, kind: str, version: str, package_hash: str, claimed: list) -> None:
        nonlocal changed
        if not package_id or package_id in index:
            return
        index[package_id] = {
            "id": package_id,
            "kind": kind,
            "version": str(version or ""),
            "hash": str(package_hash or ""),
            "owner": "",
            "locked": False,
            "installed_at": 0,
            "claimed": list(claimed),
        }
        changed = True

    try:
        from core.codex_overlay import status as overlay_status

        current = (overlay_status() or {}).get("current") or {}
        ext_id = str(current.get("id") or "").strip()
        if ext_id:
            _ensure(
                ext_id,
                "codex",
                str(current.get("version") or ""),
                str(current.get("hash") or ""),
                list(current.get("modules") or []),
            )
    except Exception:
        pass

    try:
        from core.runtime_extensions import get_all_extension_manifests

        for ext_id, manifest in (get_all_extension_manifests() or {}).items():
            if not ext_id or ext_id in index:
                continue
            if not isinstance(manifest, dict):
                continue
            kind = "codex" if manifest.get("kind") == "codex" else "extension"
            _ensure(
                ext_id,
                kind,
                str(manifest.get("version") or ""),
                "",
                claimed_for_manifest(manifest),
            )
    except Exception:
        pass

    if changed:
        _save_index(index)


def list_packages() -> dict:
    """JSON-ready list for System UI / shell."""
    try:
        _hydrate_missing()
    except Exception as exc:
        logger.warning(f"package manager: hydrate failed — {exc}")
    index = _load_index()
    packages = [_public({**raw, "id": package_id}) for package_id, raw in sorted(index.items())]
    return {"success": True, "packages": packages}


def claimed_for_manifest(manifest: Optional[dict]) -> List[str]:
    """Stems / declared entity names this package claims."""
    if not isinstance(manifest, dict):
        return []
    claimed: List[str] = []
    raw_modules = manifest.get("codex_modules")
    if isinstance(raw_modules, list):
        claimed.extend(str(name) for name in raw_modules if isinstance(name, str) and name)
    try:
        from core.extension_bridge import declared_entities

        claimed.extend(declared_entities(manifest).keys())
    except Exception:
        entities = manifest.get("entities")
        if isinstance(entities, dict):
            claimed.extend(str(name) for name in entities if name)
    # Dedup, keep order.
    seen = set()
    ordered = []
    for name in claimed:
        if name in seen or name in PROTECTED_HOST_TYPES:
            continue
        seen.add(name)
        ordered.append(name)
    return ordered


def claimed_for_files(files: Dict[str, str], manifest: Optional[dict] = None) -> List[str]:
    """Codex module stems + manifest claims.

    When ``codex_modules`` is absent, every top-level ``modules/*.py`` or
    ``backend/modules/*.py`` stem is claimed (legacy Dominion / unflattened
    catalog pull). When present, only the allow-list.
    """
    claimed = claimed_for_manifest(manifest)
    if isinstance(manifest, dict) and "codex_modules" in manifest:
        return claimed
    from core.runtime_codex import _codex_module_stem

    for path in files or {}:
        name = _codex_module_stem(path)
        if name and name not in claimed and name not in PROTECTED_HOST_TYPES:
            claimed.append(name)
    return claimed


def list_rel_files(root: str) -> List[str]:
    """Relative file paths under ``root``. ``os.walk`` is unavailable on WASM."""
    files: List[str] = []

    def walk(prefix: str, dirpath: str) -> None:
        try:
            names = os.listdir(dirpath)
        except OSError:
            return
        for item in names:
            path = os.path.join(dirpath, item)
            rel = f"{prefix}{item}" if prefix else item
            if os.path.isdir(path):
                walk(rel + "/", path)
            else:
                files.append(rel)

    if os.path.exists(root):
        walk("", root)
    return files


def prune_unclaimed_files(root: str, claimed_paths: List[str]) -> List[str]:
    """Delete files under ``root`` the new package did not write.

    Keeps ``_source.json`` / ``_slot.json``. Does not touch host data.
    """
    claimed = set(claimed_paths) | KEEP_RUNTIME_FILES
    deleted: List[str] = []
    if not root or not os.path.isdir(root):
        return deleted
    for rel in list_rel_files(root):
        if rel in claimed:
            continue
        path = os.path.join(root, rel)
        try:
            os.remove(path)
            deleted.append(rel)
        except Exception as exc:
            logger.warning(f"package manager: could not prune file {rel}: {exc}")
    _rm_empty_dirs(root)
    if deleted:
        logger.info(f"package manager: pruned leftover files under {root}: {deleted}")
    return deleted


def _rm_empty_dirs(root: str) -> None:
    if not os.path.isdir(root):
        return
    try:
        names = list(os.listdir(root))
    except OSError:
        return
    for item in names:
        path = os.path.join(root, item)
        if os.path.isdir(path):
            _rm_empty_dirs(path)
            try:
                if not os.listdir(path):
                    os.rmdir(path)
            except OSError:
                pass


def prune_unclaimed_extension_entities(ext_id: str, claimed: List[str]) -> List[str]:
    """Delete extension-owned table rows whose type the new package dropped.

    Never touches User / Organization / Case. Host ``Codex`` rows are
    pruned by ``core.codex_overlay.prune_codex_table``, not here.
    """
    if not ext_id:
        return []
    claimed_set = {name for name in claimed if name}
    previous = get_package(ext_id)
    previous_claimed = list((previous or {}).get("claimed") or [])
    to_drop = []
    seen = set()
    for name in previous_claimed:
        if name in claimed_set or name in PROTECTED_HOST_TYPES or name in seen:
            continue
        seen.add(name)
        to_drop.append(name)
    try:
        from core.extension_bridge import _EXT_ENTITY_CLASSES

        for (eid, name), _cls in list(_EXT_ENTITY_CLASSES.items()):
            if eid != ext_id or name in claimed_set or name in PROTECTED_HOST_TYPES:
                continue
            if name not in seen:
                seen.add(name)
                to_drop.append(name)
    except Exception:
        pass

    deleted = []
    for name in to_drop:
        if _delete_extension_entity_rows(ext_id, name):
            deleted.append(name)
    if deleted:
        logger.info(
            f"package manager: pruned unclaimed entities for '{ext_id}': {deleted}"
        )
    return deleted


def _delete_extension_entity_rows(ext_id: str, name: str) -> bool:
    if name in PROTECTED_HOST_TYPES:
        return False
    cls = None
    try:
        from core.extension_bridge import _EXT_ENTITY_CLASSES

        cls = _EXT_ENTITY_CLASSES.get((ext_id, name))
    except Exception:
        cls = None
    if cls is None:
        try:
            from core.extensions import create_extension_entity_class

            base = create_extension_entity_class(ext_id)
            cls = type(name, (base,), {})
        except Exception as exc:
            logger.warning(
                f"package manager: cannot rebuild '{ext_id}::{name}' for prune — {exc}"
            )
            return False
    try:
        rows = list(cls.instances())
    except Exception as exc:
        logger.warning(f"package manager: {ext_id}::{name} instances() failed — {exc}")
        return False
    dropped = False
    for row in rows:
        try:
            row.delete()
            dropped = True
        except Exception as exc:
            logger.error(f"package manager: failed to delete {ext_id}::{name} row — {exc}")
    try:
        from core.extension_bridge import _EXT_ENTITY_CLASSES

        _EXT_ENTITY_CLASSES.pop((ext_id, name), None)
    except Exception:
        pass
    return dropped or True


def prune_codex_stems(claimed: List[str]) -> List[str]:
    """Proxy to overlay Codex-table prune (User/Org/Case untouched)."""
    from core.codex_overlay import prune_codex_table

    return prune_codex_table(claimed)
