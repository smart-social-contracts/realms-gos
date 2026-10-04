"""
Runtime Extension Loader for the Realm architecture.

Extensions are loaded dynamically from the persistent filesystem.
They are installed via the installer canister which pulls extension
files from the on-chain File Registry.

Extensions are stored at: /extensions/{ext_id}/
  - manifest.json   — extension metadata
  - entry.py        — backend entry point (defines functions callable via the registry)

See: https://github.com/smart-social-contracts/realms-gos/issues/168
"""

import json
import os
import sys
import traceback
from typing import Any, Callable, Dict, List, Optional

from ic_python_logging import get_logger

logger = get_logger("core.runtime_extensions")

# Base directory for runtime-installed extensions
EXTENSIONS_DIR = "/extensions"

# In-memory cache of loaded extension modules
_loaded_modules: Dict[str, Any] = {}

# In-memory cache of extension manifests
_loaded_manifests: Dict[str, dict] = {}


def _ensure_extensions_dir():
    """Create the extensions directory if it doesn't exist."""
    os.makedirs(EXTENSIONS_DIR, exist_ok=True)


def _ext_dir(ext_id: str) -> str:
    """Return the filesystem path for an extension."""
    return os.path.join(EXTENSIONS_DIR, ext_id)


def _load_module(ext_id: str, force=False) -> Optional[Any]:
    """Load (or reload) an extension's entry.py as a Python module.

    Treats the extension directory as a *package* so that ``entry.py`` can
    use relative imports like ``from .models import X`` to pull in sibling
    .py files. Uses exec/compile instead of importlib.util which is not
    available in the CPython WASM environment used by basilisk.

    Returns None if the extension has no ``entry.py`` (i.e. it's a
    frontend-only extension); callers must treat that as a separate
    state from a load *failure*.
    """
    if ext_id in _loaded_modules and not force:
        return _loaded_modules[ext_id]

    ext_path = _ext_dir(ext_id)
    entry_path = os.path.join(ext_path, "entry.py")
    if not os.path.exists(entry_path):
        logger.info(f"Extension {ext_id}: no entry.py at {entry_path} (frontend-only)")
        return None

    package_name = f"_runtime_ext_{ext_id}"
    try:
        ModuleType = type(sys)

        # 1. Build a package shell so relative imports resolve.
        package = ModuleType(package_name)
        package.__file__ = os.path.join(ext_path, "__init__.py")
        package.__name__ = package_name
        package.__path__ = [ext_path]
        package.__package__ = package_name
        sys.modules[package_name] = package

        # 2. Pre-load every sibling .py as <package>.<stem> so subsequent
        #    relative imports inside entry.py find them in sys.modules.
        for fname in sorted(os.listdir(ext_path)):
            if not fname.endswith(".py"):
                continue
            if fname in ("entry.py", "__init__.py"):
                continue
            sib_path = os.path.join(ext_path, fname)
            stem = fname[:-3]
            sib_full = f"{package_name}.{stem}"
            sib_module = ModuleType(sib_full)
            sib_module.__file__ = sib_path
            sib_module.__name__ = sib_full
            sib_module.__package__ = package_name
            try:
                with open(sib_path, "r") as f:
                    sib_source = f.read()
                sib_code = compile(sib_source, sib_path, "exec")
                exec(sib_code, sib_module.__dict__)
                sys.modules[sib_full] = sib_module
                setattr(package, stem, sib_module)
            except Exception as e:
                logger.warning(
                    f"Extension {ext_id}: sibling module {fname} failed to "
                    f"pre-load ({e}); relative imports against it will fail."
                )

        # 3. Load entry.py as the package's main entry point.
        entry_full = f"{package_name}.entry"
        module = ModuleType(entry_full)
        module.__file__ = entry_path
        module.__name__ = entry_full
        module.__package__ = package_name

        with open(entry_path, "r") as f:
            source = f.read()
        code = compile(source, entry_path, "exec")
        exec(code, module.__dict__)

        sys.modules[entry_full] = module
        # Public name (what get_func() looks up) points at the entry module
        # but is also reachable via the package alias for older callers.
        sys.modules[package_name + "_entry_alias"] = module
        _loaded_modules[ext_id] = module
        # Mirror entry's public symbols on the package so legacy code that
        # imports `_runtime_ext_<id>` still finds them.
        for attr_name, attr_val in module.__dict__.items():
            if attr_name.startswith("_"):
                continue
            setattr(package, attr_name, attr_val)
        logger.info(f"Extension {ext_id}: loaded from {entry_path}")
        return module
    except Exception as e:
        logger.error(f"Extension {ext_id}: failed to load — {e}\n{traceback.format_exc()}")
        return None


def _load_manifest(ext_id: str, force=False) -> Optional[dict]:
    """Load an extension's manifest.json."""
    if ext_id in _loaded_manifests and not force:
        return _loaded_manifests[ext_id]

    manifest_path = os.path.join(_ext_dir(ext_id), "manifest.json")
    if not os.path.exists(manifest_path):
        logger.warning(f"Extension {ext_id}: manifest.json not found")
        return None

    try:
        with open(manifest_path, "r") as f:
            manifest = json.loads(f.read())
        _loaded_manifests[ext_id] = manifest
        return manifest
    except Exception as e:
        logger.error(f"Extension {ext_id}: failed to load manifest — {e}")
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_extension_id(ext_id: str) -> str:
    """Resolve an extension id through codex overrides (issue #242).

    Resolution order:
      1. the codex override extension, when the active codex declares one
         (hook API or manifest, issue #244) *and* the override is actually
         installed;
      2. the extension id as given (default / system extension).
    """
    try:
        from core.codex_hooks import get_extension_overrides

        override = get_extension_overrides().get(ext_id)
        if override and override in set(list_installed()):
            return override
    except Exception:
        pass
    return ext_id


def get_func(extension_name: str, function_name: str) -> Optional[Callable]:
    """Get a function from a runtime-loaded extension.

    The extension id is resolved through codex overrides first, so backend
    calls addressed to a system extension (e.g. ``member_dashboard``) are
    routed to the codex-specific replacement when one is installed.
    """
    extension_name = resolve_extension_id(extension_name)
    module = _load_module(extension_name)

    if module is None:
        raise ValueError(f"Extension '{extension_name}' not found")

    func = getattr(module, function_name, None)
    if func is None:
        raise AttributeError(
            f"Extension '{extension_name}' has no function '{function_name}'"
        )
    return func


def get_all_extension_manifests() -> dict:
    """Get all extension manifests from runtime-installed extensions."""
    manifests = {}

    _ensure_extensions_dir()
    if os.path.exists(EXTENSIONS_DIR):
        for item in os.listdir(EXTENSIONS_DIR):
            item_path = os.path.join(EXTENSIONS_DIR, item)
            if os.path.isdir(item_path) and not item.startswith("."):
                manifest = _load_manifest(item)
                if manifest:
                    manifests[item] = manifest

    return manifests


def list_installed() -> List[str]:
    """Return list of runtime-installed extension IDs."""
    _ensure_extensions_dir()
    installed = []
    if os.path.exists(EXTENSIONS_DIR):
        for item in os.listdir(EXTENSIONS_DIR):
            if os.path.isdir(os.path.join(EXTENSIONS_DIR, item)) and not item.startswith("."):
                installed.append(item)
    return sorted(installed)


def get_extension_source(ext_id: str) -> Optional[dict]:
    """Return the registry info recorded when this extension was installed.

    Returns a dict like {"registry_canister_id": "...", "version": "..."}
    or None if no _source.json exists (e.g. baked-in or manually installed).
    """
    src_path = os.path.join(_ext_dir(ext_id), "_source.json")
    if not os.path.exists(src_path):
        return None
    try:
        with open(src_path, "r") as f:
            return json.loads(f.read())
    except Exception as e:
        logger.warning(f"Extension {ext_id}: could not read _source.json — {e}")
        return None


def _seed_extension_entity(ext_id: str, manifest: dict):
    """Create or update the Extension DB entity and link profile-level access."""
    try:
        from ggg import Extension, UserProfile

        ext = Extension[ext_id]
        if not ext:
            ext = Extension(
                name=ext_id,
                description=manifest.get("description", ""),
            )
            logger.info(f"Extension {ext_id}: created DB entity")
        else:
            ext.description = manifest.get("description", "")

        profile_names = manifest.get("profiles", [])
        for pname in profile_names:
            profile = UserProfile[pname]
            if profile and profile not in ext.profiles:
                ext.profiles.add(profile)
                logger.info(f"Extension {ext_id}: linked to profile '{pname}'")
    except Exception as e:
        logger.warning(f"Extension {ext_id}: failed to seed DB entity — {e}")


def install_extension(
    ext_id: str,
    files: Dict[str, str],
    source_registry_id: Optional[str] = None,
    source_version: Optional[str] = None,
    owner: Optional[str] = None,
    bypass_lock: bool = False,
) -> bool:
    """Install an extension from a dict of {filename: content}.

    Re-install of the same id is replace, not merge (issue #351): leftover
    files and unclaimed declared-entity rows are pruned. A locked package
    rejects replace unless the caller is the owner or root / Congress /
    ``codex.revert``.

    Args:
        ext_id: Extension identifier (e.g. "voting", "vault")
        files: Dict mapping relative filenames to their text content.
               Must include at minimum "manifest.json" and "entry.py".
        source_registry_id: (optional) Canister ID of the file_registry the
            extension was pulled from. When set, written to ``_source.json``
            so the realm can discover where to pull frontend assets from.
        source_version: (optional) Resolved version string from the registry.
        owner: Optional transfer of package ownership on this install.
        bypass_lock: Skip the lock gate (codex revert / overlay apply).

    Returns:
        True if installation succeeded.
    """
    if not bypass_lock:
        try:
            from core.package_manager import replace_denied

            denied = replace_denied(ext_id)
            if denied:
                logger.error(f"Extension {ext_id}: {denied}")
                return False
        except Exception as exc:
            logger.warning(f"Extension {ext_id}: lock check failed — {exc}")

    _ensure_extensions_dir()
    ext_path = _ext_dir(ext_id)
    os.makedirs(ext_path, exist_ok=True)

    for filename, content in files.items():
        filepath = os.path.join(ext_path, filename)
        # Create subdirectories if needed
        dirpath = os.path.dirname(filepath)
        if dirpath and dirpath != ext_path:
            os.makedirs(dirpath, exist_ok=True)
        with open(filepath, "wb") as f:
            f.write(content.encode("utf-8"))
        logger.info(f"Extension {ext_id}: wrote {filename} ({len(content)} bytes)")

    if source_registry_id:
        try:
            with open(os.path.join(ext_path, "_source.json"), "w") as f:
                f.write(json.dumps({
                    "registry_canister_id": source_registry_id,
                    "version": source_version or "",
                }))
        except Exception as e:
            logger.warning(f"Extension {ext_id}: could not write _source.json — {e}")

    _loaded_modules.pop(ext_id, None)
    _loaded_manifests.pop(ext_id, None)
    try:
        from core.codex_hooks import invalidate_cache

        invalidate_cache()
    except Exception:
        pass

    manifest = _load_manifest(ext_id, force=True)
    if manifest is None:
        logger.error(f"Extension {ext_id}: installed but missing manifest.json")
        return False

    # Keep _source.json version in sync with manifest after runtime-install
    # (direct install skips source_registry_id but frontend loader needs
    # the current version for same-origin bundle paths).
    manifest_version = str(manifest.get("version") or "")
    if manifest_version:
        src_path = os.path.join(ext_path, "_source.json")
        src_data = {}
        if os.path.exists(src_path):
            try:
                with open(src_path, "r") as f:
                    src_data = json.loads(f.read())
            except Exception:
                src_data = {}
        if src_data.get("version") != manifest_version:
            src_data["version"] = manifest_version
            try:
                with open(src_path, "wb") as f:
                    f.write(json.dumps(src_data).encode("utf-8"))
            except Exception as e:
                logger.warning(f"Extension {ext_id}: could not sync _source.json — {e}")

    _seed_extension_entity(ext_id, manifest)

    # Entities an extension declares in its manifest are registered here, on
    # the host, rather than by the extension calling
    # create_extension_entity_class at import time — a sandboxed extension has
    # no way to build an ORM class. Failure is fatal: the extension's storage
    # would silently not exist.
    try:
        from core.extension_bridge import register_declared_entities

        registered = register_declared_entities(ext_id, manifest)
        if registered:
            logger.info(
                f"Extension {ext_id}: registered declared entities "
                f"({', '.join(registered)})"
            )
    except Exception as e:
        logger.error(f"Extension {ext_id}: invalid 'entities' declaration — {e}")
        return False

    try:
        from core.codex_overlay import package_hash
        from core.package_manager import (
            claimed_for_files,
            prune_unclaimed_extension_entities,
            prune_unclaimed_files,
            record_install,
        )

        prune_unclaimed_files(ext_path, list(files.keys()) + ["_source.json"])
        kind = "codex" if manifest.get("kind") == "codex" else "extension"
        claimed = claimed_for_files(files, manifest)
        if kind != "codex":
            prune_unclaimed_extension_entities(ext_id, claimed)
        record_install(
            ext_id,
            kind=kind,
            version=str(manifest.get("version") or source_version or ""),
            package_hash=package_hash(files),
            claimed=claimed,
            transfer_owner=owner,
        )
    except Exception as exc:
        logger.warning(f"Extension {ext_id}: package record/prune failed — {exc}")

    has_entry = os.path.exists(os.path.join(ext_path, "entry.py"))
    if has_entry:
        # Backend-bearing extension — entry.py MUST load cleanly, otherwise
        # subsequent extension_sync_call will fail in confusing ways.
        module = _load_module(ext_id, force=True)
        if module is None:
            logger.error(
                f"Extension {ext_id}: installed but failed to load entry.py"
            )
            return False
        logger.info(
            f"Extension {ext_id}: installed successfully (backend, {len(files)} files)"
        )
    else:
        # Frontend-only extension — no entry.py, so nothing to import. The
        # manifest is enough for sidebar/manifest queries; the bundle is
        # served separately by the realm_frontend pulling from file_registry.
        logger.info(
            f"Extension {ext_id}: installed successfully (frontend-only, {len(files)} files)"
        )
    return True


def uninstall_extension(ext_id: str) -> bool:
    """Remove a runtime-installed extension.

    Args:
        ext_id: Extension identifier to remove.

    Returns:
        True if removal succeeded.
    """
    ext_path = _ext_dir(ext_id)
    if not os.path.exists(ext_path):
        logger.warning(f"Extension {ext_id}: not installed")
        return False

    # Remove all files (os.walk not available on CPython WASM)
    def _rmtree(path):
        for item in os.listdir(path):
            item_path = os.path.join(path, item)
            if os.path.isdir(item_path):
                _rmtree(item_path)
            else:
                os.remove(item_path)
        os.rmdir(path)

    _rmtree(ext_path)

    try:
        from core.package_manager import drop_package

        drop_package(ext_id)
    except Exception:
        pass

    # Clear caches
    _loaded_modules.pop(ext_id, None)
    _loaded_manifests.pop(ext_id, None)
    try:
        from core.codex_hooks import invalidate_cache

        invalidate_cache()
    except Exception:
        pass

    # Remove from sys.modules
    module_name = f"_runtime_ext_{ext_id}"
    sys.modules.pop(module_name, None)

    logger.info(f"Extension {ext_id}: uninstalled")
    return True


def reload_extension(ext_id: str) -> bool:
    """Force-reload an extension's code from the filesystem."""
    module = _load_module(ext_id, force=True)
    _load_manifest(ext_id, force=True)
    return module is not None
