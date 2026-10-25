"""
Runtime Codex Manager — install/uninstall/reload codex packages at runtime.

.. deprecated:: issue #244
    Legacy compat shim. Codices are now privileged system extensions
    (manifest ``"kind": "codex"``) installed through the unified extension
    pipeline (``api.file_registry.install_extension_from_registry``) and
    integrated via the hook API (``core.codex_hooks``) instead of exec'd
    ``init.py`` files. The ``entity_method_overrides`` monkey-patching this
    module used to apply was removed in issue #265.
    This module keeps already-deployed /codex_packages realms working for
    one release and will then be removed.

Codex packages are directories of Python files (using the ggg library) stored
in the file registry.  This module handles:

  - Installing codex packages from a dict of {filename: content}
  - Creating/updating Codex entities in ic-python-db for each .py file
  - Refusing packages that still ship init.py (removed in issue #265;
    use the ``init`` hook via ``core.codex_hooks.run_init`` instead)
  - Uninstalling codex packages (removes files + Codex entities)
  - Listing installed codex packages

Storage layout on the realm's persistent filesystem:
  /codex_packages/{codex_id}/              package staging area
  /codex_packages/{codex_id}/manifest.json
  /codex_packages/{codex_id}/*.py
  /{codex_name}                            Codex entity code (managed by Codex.code setter)

Refs: https://github.com/smart-social-contracts/realms-gos/issues/168
"""

import json
import os
import traceback
from typing import Dict, List, Optional

from ic_python_logging import get_logger

logger = get_logger("core.runtime_codex")

# Base directory for codex package staging
CODEX_PACKAGES_DIR = "/codex_packages"

# In-memory cache of installed package manifests
_installed_manifests: Dict[str, dict] = {}


def _ensure_packages_dir():
    """Create the codex packages directory if it doesn't exist."""
    os.makedirs(CODEX_PACKAGES_DIR, exist_ok=True)


def _pkg_dir(codex_id: str) -> str:
    """Return the filesystem path for a codex package."""
    return os.path.join(CODEX_PACKAGES_DIR, codex_id)


def _load_manifest(codex_id: str, force=False) -> Optional[dict]:
    """Load a codex package's manifest.json."""
    if codex_id in _installed_manifests and not force:
        return _installed_manifests[codex_id]

    manifest_path = os.path.join(_pkg_dir(codex_id), "manifest.json")
    if not os.path.exists(manifest_path):
        return None

    try:
        with open(manifest_path, "r") as f:
            manifest = json.loads(f.read())
        _installed_manifests[codex_id] = manifest
        return manifest
    except Exception as e:
        logger.error(f"Codex package {codex_id}: failed to load manifest — {e}")
        return None


# Governance modules live at modules/<stem>.py after the unified ext/ flatten,
# or at backend/modules/<stem>.py on the legacy catalog pull (codex/ namespace
# does not strip backend/). Both must become Codex entities.
_CODEX_MODULE_PREFIXES = ("backend/modules/", "modules/")


def _codex_module_stem(path: str) -> Optional[str]:
    """Return the file stem if ``path`` is a top-level governance module.

    Accepts flattened ``modules/<stem>.py`` and unflattened
    ``backend/modules/<stem>.py``. Nested paths are ignored.
    """
    if not path.endswith(".py"):
        return None
    for prefix in _CODEX_MODULE_PREFIXES:
        if path.startswith(prefix):
            name = path[len(prefix) : -3]
            if name and "/" not in name:
                return name
    return None


def _codex_module_file(pkg_path: str, name: str) -> Optional[str]:
    """Resolve ``<name>.py`` at package root, ``modules/``, or ``backend/modules/``."""
    candidates = (
        os.path.join(pkg_path, f"{name}.py"),
        os.path.join(pkg_path, "modules", f"{name}.py"),
        os.path.join(pkg_path, "backend", "modules", f"{name}.py"),
    )
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return None


def _codex_names_for_package(codex_id: str) -> List[str]:
    """Return list of codex names (filenames without .py) for a package."""
    pkg_path = _pkg_dir(codex_id)
    if not os.path.exists(pkg_path):
        return []

    names = []
    for item in os.listdir(pkg_path):
        if item.endswith(".py") and item != "init.py" and item != "__init__.py":
            names.append(item[:-3])  # Strip .py
    for rel_dir in ("modules", os.path.join("backend", "modules")):
        module_dir = os.path.join(pkg_path, rel_dir)
        if not os.path.isdir(module_dir):
            continue
        for item in os.listdir(module_dir):
            if not item.endswith(".py") or item in ("init.py", "__init__.py"):
                continue
            stem = item[:-3]
            if stem and stem not in names:
                names.append(stem)
    return sorted(names)


def _create_or_update_codex_entity(name: str, code: str):
    """Create or update a Codex entity with the given name and code.

    The Codex entity stores code at /{name} on the persistent filesystem
    and is accessible via Codex[name].
    """
    if (name or "").startswith("proposal_"):
        raise ValueError(
            f"Codex name '{name}' is reserved for voting code-execution proposals"
        )
    try:
        from ggg import Codex

        existing = Codex[name]
        if existing:
            existing.code = code
            logger.info(f"Codex '{name}': updated ({len(code)} chars)")
        else:
            codex = Codex(name=name, code=code)
            logger.info(f"Codex '{name}': created ({len(code)} chars)")
    except Exception as e:
        logger.error(f"Codex '{name}': failed to create/update entity — {e}")
        raise


def _delete_codex_entity(name: str):
    """Delete a Codex entity by name."""
    try:
        from ggg import Codex

        existing = Codex[name]
        if existing:
            existing.delete()
            logger.info(f"Codex '{name}': deleted entity")
        else:
            logger.warning(f"Codex '{name}': entity not found for deletion")
    except Exception as e:
        logger.error(f"Codex '{name}': failed to delete entity — {e}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def install_codex_package(codex_id: str, files: Dict[str, str]) -> bool:
    """Install a codex package from a dict of {filename: content}.

    Args:
        codex_id: Package identifier (e.g. "syntropia", "dominion")
        files: Dict mapping relative filenames to their text content.
               Should include manifest.json and *.py files.

    Returns:
        True if installation succeeded.
    """
    try:
        from core.package_manager import replace_denied

        denied = replace_denied(codex_id)
        if denied:
            logger.error(f"Codex package {codex_id}: {denied}")
            return False
    except Exception as exc:
        logger.warning(f"Codex package {codex_id}: lock check failed — {exc}")

    _ensure_packages_dir()
    pkg_path = _pkg_dir(codex_id)
    os.makedirs(pkg_path, exist_ok=True)

    try:
        from core.codex_overlay import preserve_current_as_previous

        preserve_current_as_previous()
    except Exception as exc:
        logger.warning(f"Codex package {codex_id}: overlay snapshot failed — {exc}")

    # Write all files to the package directory
    for filename, content in files.items():
        filepath = os.path.join(pkg_path, filename)
        dirpath = os.path.dirname(filepath)
        if dirpath and dirpath != pkg_path:
            os.makedirs(dirpath, exist_ok=True)
        with open(filepath, "w") as f:
            f.write(content)
        logger.info(f"Codex package {codex_id}: wrote {filename} ({len(content)} bytes)")

    # Create/update Codex entities for each governance module (except init.py)
    for name in _codex_names_for_package(codex_id):
        py_path = _codex_module_file(pkg_path, name)
        if not py_path:
            continue
        with open(py_path, "r") as f:
            code = f.read()
        _create_or_update_codex_entity(name, code)

    # Clear manifest cache
    _installed_manifests.pop(codex_id, None)
    manifest = _load_manifest(codex_id, force=True)
    try:
        from core.codex_hooks import invalidate_cache

        invalidate_cache()
    except Exception:
        pass

    modules = list(_codex_names_for_package(codex_id))
    for path in files:
        stem = _codex_module_stem(path)
        if stem and stem not in modules:
            modules.append(stem)
    try:
        from core.codex_overlay import commit_current

        commit_current(codex_id, files, modules)
    except Exception as exc:
        logger.warning(f"Codex package {codex_id}: overlay commit failed — {exc}")

    logger.info(
        f"Codex package {codex_id}: installed ({len(files)} files, "
        f"{len(modules)} codexes)"
    )

    return True


def legacy_init_py_error(codex_id: str, files: Optional[dict] = None) -> str:
    """Refusal message when a package still ships a legacy ``init.py``.

    That file used to be ``exec()``'d in-process with the host's ``ic`` after
    install (issue #265). Modern packages use the ``init`` hook in
    ``backend/entry.py`` instead (``core.codex_hooks.run_init``). Installation
    is refused rather than the file ignored, so an operator never ends up
    believing post-install setup ran when it silently did not.
    """
    if files is not None:
        present = any(
            name == "init.py" or str(name).endswith("/init.py") for name in files
        )
    else:
        present = os.path.exists(os.path.join(_pkg_dir(codex_id), "init.py"))
    if not present:
        return ""
    return (
        f"Codex '{codex_id}' ships init.py, a mechanism removed in issue #265 "
        f"because it ran codex code in-process with full host access. Move "
        f"setup into the init hook in backend/entry.py "
        f"(see docs/reference/EXTENSION_ARCHITECTURE.md)."
    )


def run_codex_init(codex_id: str) -> Optional[str]:
    """Legacy entry point retained so callers compile; never executes code.

    Returns None when no ``init.py`` is present. Returns an error when one is,
    so a reload/admin path cannot quietly re-introduce the old exec.
    """
    error = legacy_init_py_error(codex_id)
    if error:
        logger.error(error)
        return error
    logger.info(f"Codex package {codex_id}: no init.py found, skipping")
    return None


def uninstall_codex_package(codex_id: str) -> bool:
    """Remove a codex package and its Codex entities.

    Args:
        codex_id: Package identifier to remove.

    Returns:
        True if removal succeeded.
    """
    pkg_path = _pkg_dir(codex_id)
    if not os.path.exists(pkg_path):
        logger.warning(f"Codex package {codex_id}: not installed")
        return False

    # Delete Codex entities for each .py file
    for name in _codex_names_for_package(codex_id):
        _delete_codex_entity(name)

    # Remove all files (os.walk not available on CPython WASM)
    def _rmtree(path):
        for item in os.listdir(path):
            item_path = os.path.join(path, item)
            if os.path.isdir(item_path):
                _rmtree(item_path)
            else:
                os.remove(item_path)
        os.rmdir(path)

    _rmtree(pkg_path)

    # Clear cache
    _installed_manifests.pop(codex_id, None)
    try:
        from core.codex_hooks import invalidate_cache

        invalidate_cache()
    except Exception:
        pass

    logger.info(f"Codex package {codex_id}: uninstalled")
    return True


def reload_codex_package(codex_id: str) -> bool:
    """Reload all Codex entities from a codex package's files on disk.

    Reads the .py files from the package directory and updates the
    corresponding Codex entities. Does NOT re-run init.py.

    Returns:
        True if reload succeeded.
    """
    pkg_path = _pkg_dir(codex_id)
    if not os.path.exists(pkg_path):
        logger.warning(f"Codex package {codex_id}: not installed")
        return False

    for name in _codex_names_for_package(codex_id):
        py_path = _codex_module_file(pkg_path, name)
        if not py_path:
            continue
        with open(py_path, "r") as f:
            code = f.read()
        _create_or_update_codex_entity(name, code)

    _installed_manifests.pop(codex_id, None)
    _load_manifest(codex_id, force=True)

    logger.info(f"Codex package {codex_id}: reloaded")
    return True


def list_installed() -> List[str]:
    """Return list of installed codex package IDs."""
    _ensure_packages_dir()
    installed = []
    if os.path.exists(CODEX_PACKAGES_DIR):
        for item in os.listdir(CODEX_PACKAGES_DIR):
            if os.path.isdir(os.path.join(CODEX_PACKAGES_DIR, item)) and not item.startswith("."):
                installed.append(item)
    return sorted(installed)


def get_all_codex_manifests() -> dict:
    """Return manifests for all installed codex packages."""
    manifests = {}
    for codex_id in list_installed():
        manifest = _load_manifest(codex_id)
        if manifest:
            manifests[codex_id] = manifest
    return manifests


def get_extension_overrides() -> Dict[str, str]:
    """Merged ``extension_overrides`` from all installed codex manifests.

    A codex may replace a *system extension* (e.g. ``member_dashboard``) with
    its own variant (issue #242)::

        "extension_overrides": {"member_dashboard": "agora_member_dashboard"}

    Returns {base_extension_id: override_extension_id}. Later-installed
    packages win when several codices override the same extension.
    """
    overrides: Dict[str, str] = {}
    for codex_id in list_installed():
        manifest = _load_manifest(codex_id)
        if not manifest:
            continue
        raw = manifest.get("extension_overrides") or {}
        if not isinstance(raw, dict):
            logger.warning(
                f"Codex package {codex_id}: extension_overrides must be a dict, got {type(raw).__name__}"
            )
            continue
        for base, override in raw.items():
            if base and override and isinstance(override, str):
                overrides[str(base)] = override
    from core.codex_overlay import filter_protected_overrides

    return filter_protected_overrides(overrides)


