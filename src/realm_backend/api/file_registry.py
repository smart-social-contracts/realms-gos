"""
File Registry client — inter-canister interface to the file registry.

Provides async functions for realm canisters to pull extension backend files,
frontend bundles, and codex packages from a file registry canister.

Usage from main.py (async/generator pattern):
    result = yield from install_extension_from_registry(registry_id, ext_id, version)

Refs: https://github.com/smart-social-contracts/realms-gos/issues/168
"""

import base64
import json
from typing import Dict, List, Optional, Tuple

from _cdk import (
    Async,
    CallResult,
    Opt,
    Principal,
    Record,
    Service,
    blob,
    ic,
    nat,
    service_query,
    service_update,
    text,
    void,
)
from ic_python_logging import get_logger

logger = get_logger("api.file_registry")


class FileRegistryService(Service):
    @service_query
    def get_extension_manifest(self, args: text) -> text: ...

    @service_query
    def latest_version(self, args: text) -> text: ...

    @service_query
    def list_extensions(self) -> text: ...

    @service_query
    def list_codices(self) -> text: ...

    @service_query
    def list_files_icc(self, namespace: text) -> text: ...

    @service_query
    def get_file_size_icc(self, namespace: text, path: text) -> text: ...

    @service_query
    def get_file_chunk_icc(
        self, namespace: text, path: text, offset: text, length: text
    ) -> text: ...

    @service_update
    def get_namespace_approval_icc(self, namespace: text) -> text: ...


class _AssetStoreArg(Record):
    key: text
    content_type: text
    content_encoding: text
    content: blob
    sha256: Opt[blob]


class _ListAssetsArg(Record):
    start: Opt[nat]
    length: Opt[nat]


class _DeleteAssetArg(Record):
    key: text


class AssetCanisterService(Service):
    """Minimal client for the DFINITY certified-assets canister `store`."""

    @service_update
    def store(self, arg: _AssetStoreArg) -> void: ...

    @service_query
    def list(self, arg: _ListAssetsArg) -> list: ...

    @service_update
    def delete_asset(self, arg: _DeleteAssetArg) -> void: ...


def _entity_method_override_error(codex_id: str, manifest: dict) -> str:
    """Refusal message when a codex manifest declares ``entity_method_overrides``.

    That mechanism let a codex monkey-patch core GGG methods with ``exec()``'d
    code running as the host; it was removed in issue #265. Installation is
    refused rather than the declaration ignored, so an operator never ends up
    believing a governance policy is in force when it silently is not.
    """
    overrides = manifest.get("entity_method_overrides") or []
    if not overrides:
        return ""
    methods = ", ".join(
        f"{o.get('entity')}.{o.get('method')}()"
        for o in overrides
        if isinstance(o, dict)
    )
    return (
        f"Codex '{codex_id}' declares entity_method_overrides ({methods}), a "
        f"mechanism removed in issue #265 because it runs codex code in-process "
        f"with full host access. Port these to sandboxed hooks — see "
        f"docs/reference/ENTITY_METHOD_OVERRIDES.md."
    )


def _resolve_codex_dependencies(manifest: dict, codex_id: str) -> Dict[str, str]:
    """Full dependency set of a codex manifest (issues #241/#242/#244).

    A codex is a distro-style package: its manifest declares required
    extensions (optionally version-pinned), its extension overrides are
    implicit dependencies (a codex replacing a system extension must install
    its replacement), and core/system extensions ship with every standard
    realm so the codex never has to declare them.

    Manifest forms:
      "dependencies": ["voting", "vault"]                      (latest)
      "dependencies": {"voting": "1.1.x", "vault": "^2.0.0"}   (pinned)
    """
    dependencies: Dict[str, str] = {}
    raw_deps = manifest.get("dependencies", []) or []
    if isinstance(raw_deps, dict):
        dependencies = {str(k): (str(v) if v else "") for k, v in raw_deps.items()}
    else:
        dependencies = {str(d): "" for d in raw_deps}

    overrides = manifest.get("extension_overrides") or {}
    if isinstance(overrides, dict):
        for override_ext in overrides.values():
            if override_ext and str(override_ext) not in dependencies:
                dependencies[str(override_ext)] = ""

    try:
        from core.core_extensions import CORE_EXTENSION_IDS

        for core_ext in CORE_EXTENSION_IDS:
            if core_ext not in dependencies:
                dependencies[core_ext] = ""
    except Exception as e:
        logger.error(f"Codex '{codex_id}': could not add core extensions: {e}")

    return dependencies


def _parse_semver(version: str) -> tuple:
    parts = []
    for p in (version or "0").split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def _is_range_spec(spec: str) -> bool:
    s = (spec or "").strip().lower()
    return s.startswith(("^", "~")) or "x" in s.split(".") or "*" in s


def _spec_matches(spec: str, version: str) -> bool:
    spec = (spec or "").strip()
    vt = _parse_semver(version)
    if spec.startswith("^") or spec.startswith("~"):
        base = _parse_semver(spec[1:])
        if vt < base or vt[0] != base[0]:
            return False
        if spec.startswith("~") and (len(vt) < 2 or len(base) < 2 or vt[1] != base[1]):
            return False
        return True
    if "x" in spec.lower() or "*" in spec:
        spec_parts = spec.replace("*", "x").lower().split(".")
        version_parts = version.split(".")
        for i, sp in enumerate(spec_parts):
            if sp == "x":
                return True
            if i >= len(version_parts) or sp != version_parts[i]:
                return False
        return len(spec_parts) == len(version_parts)
    return spec == version


def _resolve_version_from_published(published: list, spec: str) -> str:
    spec = (spec or "").strip()
    if not spec or spec.lower() == "latest":
        if not published:
            return ""
        return max(published, key=_parse_semver)
    if not _is_range_spec(spec):
        return spec
    best = ""
    best_tuple = (-1,)
    for version in published:
        if _spec_matches(spec, version):
            vt = _parse_semver(version)
            if vt > best_tuple:
                best_tuple = vt
                best = version
    return best


def _unwrap_call_result(result) -> str:
    """Normalize a basilisk inter-canister query result to its text payload."""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        ok = result.get("Ok", result.get("ok"))
        if ok is not None:
            return ok
        err = result.get("Err", result.get("err"))
        if err is not None:
            raise RuntimeError(f"inter-canister call rejected: {err}")
        return str(result)
    if hasattr(result, "Ok"):
        return result.Ok
    return str(result)


def _pull_file_bytes(
    registry: "FileRegistryService", namespace: str, path: str
) -> Async[bytes]:
    """Pull a stored file from the registry via chunked base64 reads."""
    data = bytearray()
    offset = 0
    for _ in range(4096):
        res: CallResult = yield registry.get_file_chunk_icc(
            namespace, path, str(offset), ""
        )
        raw = _unwrap_call_result(res)
        obj = json.loads(raw)
        if "error" in obj:
            raise Exception(obj["error"])
        chunk_b64 = obj.get("content_b64", "")
        chunk = base64.b64decode(chunk_b64) if chunk_b64 else b""
        data.extend(chunk)
        offset += len(chunk)
        if obj.get("eof") or len(chunk) == 0:
            break
    return bytes(data)


def _pull_namespace_file_text(
    registry: "FileRegistryService", namespace: str, path: str
) -> Async[str]:
    data = yield from _pull_file_bytes(registry, namespace, path)
    return data.decode("utf-8", errors="replace")


def _resolve_registry_namespace(
    registry: "FileRegistryService",
    category: str,
    item_id: str,
    version: str,
) -> Async[tuple]:
    """Resolve (namespace, resolved_version). Returns (ns, ver, error_json)."""
    version = (version or "").strip()
    if category == "ext":
        published = []
        if _is_range_spec(version):
            ext_res: CallResult = yield registry.list_extensions()
            for entry in json.loads(_unwrap_call_result(ext_res)):
                if entry.get("ext_id") == item_id:
                    published = entry.get("versions") or []
                    break
            resolved = _resolve_version_from_published(published, version)
            if not resolved:
                return (
                    "",
                    "",
                    json.dumps(
                        {
                            "error": f"No published version of ext/{item_id} matches '{version}'",
                        }
                    ),
                )
            ns = f"ext/{item_id}/{resolved}".rstrip("/")
            return ns, resolved, None

        manifest_res: CallResult = yield registry.get_extension_manifest(
            json.dumps({"ext_id": item_id, "version": version or None})
        )
        manifest = json.loads(_unwrap_call_result(manifest_res))
        if manifest.get("error"):
            return "", "", json.dumps({"error": manifest["error"]})
        ns = (
            manifest.get("_namespace")
            or f"ext/{item_id}/{manifest.get('_version', '')}"
        ).rstrip("/")
        resolved = manifest.get("_version") or version or "unknown"
        return ns, resolved, None

    if category == "codex":
        codex_res: CallResult = yield registry.list_codices()
        codices = json.loads(_unwrap_call_result(codex_res))
        published = []
        for entry in codices:
            if entry.get("codex_id") == item_id:
                published = entry.get("versions") or []
                break
        if not version or version.lower() == "latest":
            resolved = _resolve_version_from_published(published, "")
            if not resolved:
                return (
                    "",
                    "",
                    json.dumps({"error": f"No versions found for codex/{item_id}"}),
                )
        elif _is_range_spec(version):
            resolved = _resolve_version_from_published(published, version)
            if not resolved:
                return (
                    "",
                    "",
                    json.dumps(
                        {
                            "error": f"No published version of codex/{item_id} matches '{version}'",
                        }
                    ),
                )
        else:
            resolved = version
        ns = f"codex/{item_id}/{resolved}".rstrip("/")
        return ns, resolved, None

    return "", "", json.dumps({"error": f"Unknown category: {category}"})


def _list_namespace_paths(
    registry: "FileRegistryService", namespace: str
) -> Async[list]:
    raw: CallResult = yield registry.list_files_icc(namespace)
    listing = json.loads(_unwrap_call_result(raw))
    if isinstance(listing, dict) and listing.get("error"):
        raise Exception(listing["error"])
    if not isinstance(listing, list):
        return []
    return [entry["path"] for entry in listing if entry.get("path")]


def _pull_namespace_files(
    registry: "FileRegistryService",
    namespace: str,
    path_filter,
) -> Async[Dict[str, str]]:
    files = {}
    paths = yield from _list_namespace_paths(registry, namespace)
    for path in paths:
        if not path_filter(path):
            continue
        try:
            size_res: CallResult = yield registry.get_file_size_icc(namespace, path)
            size_obj = json.loads(_unwrap_call_result(size_res))
            if size_obj.get("error"):
                continue
            files[path] = yield from _pull_namespace_file_text(
                registry, namespace, path
            )
        except Exception as e:
            logger.warning(f"Skip {namespace}/{path}: {e}")
    return files


def _trust_policy() -> tuple:
    """This realm's (require_approval, trusted_approver_ids) policy.

    Unreadable policy is treated as "enforce": not being able to tell whether
    the realm opted out is not a reason to assume it did.
    """
    try:
        from ggg import Realm

        _realms = Realm.instances()
        if not _realms:
            return True, []
        realm = _realms[0]
        require = bool(getattr(realm, "require_marketplace_approval", True))
        raw = (getattr(realm, "trusted_approvers", "") or "").strip()
        approvers = [p.strip() for p in raw.split(",") if p.strip()]
        if not approvers:
            # Default: trust only the marketplace this realm was configured with.
            marketplace = (getattr(realm, "marketplace_canister_id", "") or "").strip()
            approvers = [marketplace] if marketplace else []
        return require, approvers
    except Exception as e:
        logger.error(f"trust policy unreadable ({e}); enforcing approval")
        return True, []


def _is_founding_setup() -> bool:
    """True while the realm is still in the creator-only setup stage."""
    try:
        from ggg import Realm

        realms = Realm.instances()
        if not realms:
            return False
        return str(getattr(realms[0], "status", "") or "") == "setup"
    except Exception:
        return False


def _remember_trusted_approver(approver: str) -> None:
    """Seed trusted_approvers so later installs keep working after setup."""
    if not approver:
        return
    try:
        from ggg import Realm

        realms = Realm.instances()
        if not realms:
            return
        realm = realms[0]
        raw = (getattr(realm, "trusted_approvers", "") or "").strip()
        existing = [p.strip() for p in raw.split(",") if p.strip()]
        if approver in existing:
            return
        existing.append(approver)
        realm.trusted_approvers = ",".join(existing)
        logger.info(f"setup: trusting approver {approver}")
    except Exception as e:
        logger.warning(f"could not persist trusted approver: {e}")


def _check_marketplace_approval(
    registry: "FileRegistryService",
    namespace: str,
) -> Async[str]:
    """Empty string if `namespace` may be installed, else the reason it may not.

    Fails closed. A registry too old to answer the question, an approval issued
    by a principal this realm does not trust, and content that changed after it
    was approved are all refusals — otherwise "approved" would mean whatever the
    least trustworthy party in the chain wanted it to mean.
    """
    require, trusted = _trust_policy()
    if not require:
        logger.info(f"approval enforcement off; installing '{namespace}' unchecked")
        return ""

    hint = (
        "A realm operator with the realm.configure.trust_policy right can allow "
        "unapproved content from realm settings."
    )
    try:
        raw: CallResult = yield registry.get_namespace_approval_icc(namespace)
        payload = _unwrap_call_result(raw)
        approval = json.loads(payload)
    except Exception as e:
        err = str(e)
        # GOS-vendored file_registry has no approval API (IC0536). Founding
        # setup cannot install realm_settings to flip the trust policy first.
        if _is_founding_setup() and (
            "IC0536" in err or "has no update method" in err or "has no query method" in err
        ):
            logger.warning(
                f"file_registry has no approval API during setup; installing '{namespace}'"
            )
            return ""
        return (
            f"cannot verify marketplace approval for '{namespace}': the file "
            f"registry did not answer ({e}). {hint}"
        )

    if not isinstance(approval, dict):
        return f"cannot verify marketplace approval for '{namespace}': unexpected response. {hint}"
    if approval.get("error"):
        return (
            f"cannot verify marketplace approval for '{namespace}': "
            f"{approval['error']}. {hint}"
        )

    if not approval.get("approved"):
        status = approval.get("status") or "unapproved"
        if status == "approved" and not approval.get("content_matches"):
            return (
                f"'{namespace}' was approved, but its files have changed since "
                f"the review — refusing to install unreviewed content. {hint}"
            )
        return (
            f"'{namespace}' has not been approved by the marketplace "
            f"(status: {status}). {hint}"
        )

    approver = (approval.get("approver") or "").strip()
    if _is_founding_setup():
        _remember_trusted_approver(approver)
        logger.info(
            f"'{namespace}' approved by {approver}; accepted during founding setup"
        )
        return ""

    if not trusted:
        return (
            f"'{namespace}' is approved by {approver}, but this realm trusts no "
            f"approver (no marketplace configured). {hint}"
        )
    if approver not in trusted:
        return (
            f"'{namespace}' is approved by {approver}, which this realm does not "
            f"trust (trusted: {', '.join(trusted)}). {hint}"
        )

    logger.info(f"'{namespace}' approved by {approver}")
    return ""


def _pull_extension_backend_files(
    registry: "FileRegistryService",
    category: str,
    item_id: str,
    version: str,
) -> Async[tuple]:
    """Returns (files dict, resolved_version, error_json)."""
    namespace, resolved_version, err = yield from _resolve_registry_namespace(
        registry, category, item_id, version
    )
    if err:
        return {}, "", err

    refusal = yield from _check_marketplace_approval(registry, namespace)
    if refusal:
        return {}, resolved_version, json.dumps({"error": refusal})

    if category == "ext":

        def _filter(path: str) -> bool:
            return path.startswith("backend/") or path == "manifest.json"

    else:

        def _filter(path: str) -> bool:
            return True

    files = yield from _pull_namespace_files(registry, namespace, _filter)
    if not files:
        label = f"{category}/{item_id}"
        return (
            {},
            resolved_version,
            json.dumps({"error": f"No backend files found for {label}"}),
        )
    return files, resolved_version, None


def _pull_extension_frontend_files(
    registry: "FileRegistryService",
    ext_id: str,
    version: str,
) -> Async[tuple]:
    """Returns (files dict, resolved_version, error_json)."""
    namespace, resolved_version, err = yield from _resolve_registry_namespace(
        registry, "ext", ext_id, version
    )
    if err:
        return {}, "", err

    # Also checked on the backend pull during a normal install; repeated here
    # because resync_extension_frontends re-copies bundles on its own.
    refusal = yield from _check_marketplace_approval(registry, namespace)
    if refusal:
        return {}, resolved_version, json.dumps({"error": refusal})

    files = yield from _pull_namespace_files(
        registry,
        namespace,
        lambda path: path.startswith("frontend/"),
    )
    return files, resolved_version, None


def _get_realm_frontend_canister_id() -> str:
    try:
        from ggg import Realm

        _realms = Realm.instances()
        if _realms:
            return (getattr(_realms[0], "frontend_canister_id", "") or "").strip()
    except Exception:
        pass
    return ""


def _format_failed_deps(codex_id: str, failed_deps: list) -> str:
    parts = []
    for item in failed_deps[:5]:
        if isinstance(item, dict):
            parts.append(
                f"{item.get('extension', '?')}: {item.get('error', 'unknown')}"
            )
        else:
            parts.append(str(item))
    summary = "; ".join(parts)
    if len(failed_deps) > 5:
        summary += f" (+{len(failed_deps) - 5} more)"
    return f"Codex '{codex_id}': {len(failed_deps)} dependency install(s) failed: {summary}"


def _install_codex_dependencies(
    registry_canister_id: str,
    codex_id: str,
    dependencies: Dict[str, str],
    frontend_canister_id: str = None,
) -> Async[tuple]:
    """Install a codex's missing dependency extensions from the registry.

    Returns (installed: [{extension, version}], failed: [{extension, pin, error}]).
    """
    installed_deps = []
    failed_deps = []
    if not dependencies:
        return installed_deps, failed_deps

    from core.runtime_extensions import list_installed

    already = set(list_installed())
    missing = {d: pin for d, pin in dependencies.items() if d not in already}
    logger.info(
        f"Codex '{codex_id}' dependencies: {dependencies} "
        f"(missing: {list(missing) or 'none'})"
    )

    fe_canister = (
        (frontend_canister_id or "").strip()
        or _get_realm_frontend_canister_id()
        or None
    )

    for dep, pin in missing.items():
        dep_raw = yield from install_extension_from_registry(
            registry_canister_id, dep, pin or None, fe_canister
        )
        try:
            dep_result = json.loads(dep_raw)
        except (json.JSONDecodeError, TypeError):
            dep_result = {"success": False, "error": dep_raw}
        if dep_result.get("success"):
            installed_deps.append(
                {"extension": dep, "version": dep_result.get("version", "")}
            )
        else:
            failed_deps.append(
                {
                    "extension": dep,
                    "pin": pin,
                    "error": dep_result.get("error", "unknown"),
                }
            )
            logger.error(
                f"Codex '{codex_id}': dependency '{dep}' (pin={pin or 'latest'}) "
                f"failed to install: {dep_result}"
            )
    return installed_deps, failed_deps


def _codex_module_stem(path: str) -> Optional[str]:
    """Return the file stem if ``path`` is a top-level governance module.

    Registry/runtime install flattens ``backend/``, so package path
    ``backend/modules/<name>.py`` arrives as ``modules/<name>.py``. The
    legacy ``codex/`` catalog pull does not strip ``backend/``, so this
    also accepts ``backend/modules/<name>.py``. Nested paths are ignored
    — only the top-level stem becomes a Codex entity.
    """
    if not path.endswith(".py"):
        return None
    for prefix in ("backend/modules/", "modules/"):
        if path.startswith(prefix):
            name = path[len(prefix) : -3]
            if name and "/" not in name:
                return name
    return None


def _codex_modules_to_seed(
    files: Dict[str, str], manifest: Optional[dict] = None
) -> List[Tuple[str, str]]:
    """Pick which ``modules/*.py`` stems become Codex entities.

    Optional manifest key ``codex_modules`` is an allow-list of file stems:
    - absent: seed every top-level ``modules/*.py`` or
      ``backend/modules/*.py`` (legacy default so Dominion and unflattened
      ``codex/`` catalog pulls keep working).
    - present list: seed only those stems; each must exist as
      ``modules/<name>.py`` or ``backend/modules/<name>.py``.
    - empty list ``[]``: seed none.

    Listed stems that are missing from the package are skipped (logged).
    A non-list ``codex_modules`` value is treated as an empty allow-list so
    leftover helper files are never auto-seeded by accident.
    """
    available: Dict[str, str] = {}
    for path, content in files.items():
        name = _codex_module_stem(path)
        if name is not None:
            available[name] = content

    if not isinstance(manifest, dict) or "codex_modules" not in manifest:
        return [(name, available[name]) for name in available]

    allow = manifest.get("codex_modules")
    if not isinstance(allow, list):
        logger.warning(
            "codex_modules must be a list of module stems; "
            f"got {type(allow).__name__}, seeding none"
        )
        return []

    selected: List[Tuple[str, str]] = []
    for stem in allow:
        if not isinstance(stem, str) or not stem:
            continue
        content = available.get(stem)
        if content is None:
            logger.warning(
                f"codex_modules lists '{stem}' but "
                f"modules/{stem}.py (or backend/modules/{stem}.py) is missing"
            )
            continue
        selected.append((stem, content))
    return selected


def _seed_codex_module_entities(
    ext_id: str, files: Dict[str, str], manifest: Optional[dict] = None
) -> list:
    """Create/update Codex DB entities from a codex package's module files.

    Governance modules (proposal targets, TaskManager shims) ship under
    ``backend/modules/`` in the package (flattened to ``modules/`` at
    install); selected stems become a ``Codex`` entity named after the
    file — same contract the legacy /codex_packages path provided, so
    ``codex_change`` proposals and scheduled tasks keep working.

    Seeding is opt-in via manifest ``codex_modules`` when that key is
    present; see ``_codex_modules_to_seed``.
    """
    seeded = []
    try:
        from ggg import Codex
    except Exception as e:
        logger.warning(f"Codex {ext_id}: cannot seed module entities — {e}")
        return seeded

    for name, content in _codex_modules_to_seed(files, manifest):
        try:
            existing = Codex[name]
            if existing:
                existing.code = content
            else:
                Codex(name=name, code=content)
            seeded.append(name)
        except Exception as e:
            logger.error(f"Codex {ext_id}: failed to seed module entity '{name}' — {e}")
    if seeded:
        logger.info(f"Codex {ext_id}: seeded module entities: {seeded}")
    return seeded


def install_extension_from_registry(
    registry_canister_id: str,
    ext_id: str,
    version: str = None,
    frontend_canister_id: str = None,
    install_dependencies: bool = True,
    owner: str = None,
    run_init: bool = True,
) -> Async[str]:
    """Pull extension backend files from the file registry and install them.
    Frontend bundles are copied to the realm's frontend asset canister before
    the backend is installed. Install fails if any frontend file cannot be
    copied (same-origin loading has no file_registry fallback).

    Codex packages (manifest ``"kind": "codex"``, issue #244) install through
    this same path with three extra steps: privileged-caller check, singleton
    enforcement, and dependency-extension installation before the codex's
    ``init`` hook runs.

    Args:
        registry_canister_id: Canister ID of the file registry
        ext_id: Extension identifier (e.g. "voting")
        version: Specific version or None for latest
        frontend_canister_id: Frontend asset canister for same-origin UI bundles
            (required when the package includes frontend/ files; defaults to the
            realm's configured frontend_canister_id)

    Returns (via yield):
        JSON string with result
    """
    logger.info(
        f"Installing extension '{ext_id}' (version={version or 'latest'}) "
        f"from registry {registry_canister_id}"
    )

    try:
        from core.package_manager import replace_denied

        denied = replace_denied(ext_id)
        if denied:
            return json.dumps({"success": False, "error": denied})
    except Exception as exc:
        logger.warning(f"Extension '{ext_id}': lock check failed — {exc}")

    registry = FileRegistryService(Principal.from_str(registry_canister_id))

    # Route codex packages through the multi-message install job (IC0522).
    try:
        manifest_res: CallResult = yield registry.get_extension_manifest(
            json.dumps({"ext_id": ext_id, "version": version or None})
        )
        peek = json.loads(_unwrap_call_result(manifest_res))
        if isinstance(peek, dict) and peek.get("kind") == "codex" and not peek.get("error"):
            from core.codex_install_job import continue_codex_install

            return (
                yield from continue_codex_install(
                    registry_canister_id,
                    ext_id,
                    version,
                    frontend_canister_id,
                    install_dependencies,
                    owner,
                    run_init,
                    "ext",
                )
            )
    except Exception as peek_err:
        logger.info(
            f"Extension '{ext_id}': manifest peek for codex routing failed ({peek_err})"
        )

    raw_files, resolved_version, err = yield from _pull_extension_backend_files(
        registry, "ext", ext_id, version or ""
    )
    if err:
        err_obj = json.loads(err)
        return json.dumps({"success": False, "error": err_obj.get("error", err)})

    if not raw_files:
        return json.dumps(
            {
                "success": False,
                "error": f"No backend files found for extension '{ext_id}'",
            }
        )

    # Strip "backend/" prefix — registry stores as backend/entry.py but
    # runtime_extensions expects entry.py at the extension root.
    files = {}
    for path, content in raw_files.items():
        clean = path.removeprefix("backend/") if path.startswith("backend/") else path
        # Legacy packages ship a minimal backend/manifest.json stub; never let
        # it clobber the authoritative root manifest.json (categories, labels).
        if (
            clean == "manifest.json"
            and path != "manifest.json"
            and "manifest.json" in raw_files
        ):
            continue
        files[clean] = content

    logger.info(f"Got {len(files)} files from registry (version {resolved_version})")

    # --- Codex package classification (issue #244) ---
    try:
        manifest = json.loads(files.get("manifest.json", "{}"))
        if not isinstance(manifest, dict):
            manifest = {}
    except (json.JSONDecodeError, TypeError):
        manifest = {}
    is_codex = manifest.get("kind") == "codex"

    fe_canister = (
        frontend_canister_id or _get_realm_frontend_canister_id() or ""
    ).strip()

    installed_deps = []
    failed_deps = []
    if is_codex:
        from core import codex_hooks

        # 1. Privileged classification: codex install requires the codex.install
        #    permission. Controllers (e.g. the realm installer) bypass via
        #    _check_access. Internal contexts are trusted: self-calls and timer
        #    executions (quarter self-bootstrap) originate from this canister's
        #    own code — external callers can only reach this function through
        #    @require-gated endpoints, where anonymous is already rejected.
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
            pass  # test harness without access-control stack

        # 2. Hook API version gate.
        version_error = codex_hooks.unsupported_api_version(manifest)
        if version_error:
            return json.dumps(
                {"success": False, "error": f"Codex '{ext_id}': {version_error}"}
            )

        # 2b. GGG API version gate (issue #265). Missing => legacy, accepted.
        ggg_version_error = codex_hooks.unsupported_ggg_api_version(manifest)
        if ggg_version_error:
            return json.dumps(
                {"success": False, "error": f"Codex '{ext_id}': {ggg_version_error}"}
            )

        # 2c. Legacy integration mechanisms removed in issue #265.
        override_error = _entity_method_override_error(ext_id, manifest)
        if override_error:
            return json.dumps({"success": False, "error": override_error})

        from core.runtime_codex import legacy_init_py_error

        init_py_error = legacy_init_py_error(ext_id, files)
        if init_py_error:
            return json.dumps({"success": False, "error": init_py_error})

        # 2d. Import policy scan (issue #265). A codex may import only the
        #     public ``ggg`` API, never ``core.*`` or private ``ggg`` submodules.
        #     Enforced (hard reject) once the codex opts in via ``ggg_api_version``;
        #     legacy packages are scanned in warn mode so nothing breaks yet.
        #     A crash in the scanner must never block an install: only a policy
        #     violation it actually found is grounds for rejection.
        from core import codex_scan

        try:
            scan_error = codex_scan.check_codex_imports(
                ext_id, files, enforce=codex_hooks.declares_ggg_api(manifest)
            )
        except Exception as scan_err:
            logger.error(f"codex_scan[{ext_id}] failed, skipping scan: {scan_err}")
            scan_error = ""
        if scan_error:
            return json.dumps({"success": False, "error": scan_error})

        # 3. Singleton: exactly one codex per realm (same-id upgrade allowed).
        conflict = codex_hooks.singleton_violation(ext_id)
        if conflict:
            return json.dumps({"success": False, "error": conflict})

        # 4. Distro behavior: install dependency extensions first so the codex
        #    init hook can rely on them (and the realm is never left with a
        #    half-working codex). Skipped when callers already list deps as
        #    separate plan items (quarter bootstrap with extensions first).
        if install_dependencies:
            dependencies = _resolve_codex_dependencies(manifest, ext_id)
            installed_deps, failed_deps = yield from _install_codex_dependencies(
                registry_canister_id, ext_id, dependencies, fe_canister or None
            )
            if failed_deps:
                return json.dumps(
                    {
                        "success": False,
                        "error": _format_failed_deps(ext_id, failed_deps),
                        "dependency_warnings": failed_deps,
                    }
                )

    # Copy frontend bundles before installing backend so we never mark an
    # extension installed without its same-origin UI assets.
    frontend_files, _fe_version, fe_pull_err = (
        yield from _pull_extension_frontend_files(registry, ext_id, resolved_version)
    )
    if fe_pull_err:
        err_obj = json.loads(fe_pull_err)
        return json.dumps(
            {"success": False, "error": err_obj.get("error", fe_pull_err)}
        )

    frontend_copied = 0
    if frontend_files:
        # Quarters are backend-only by design: they never serve UI, the
        # shared frontend already carries the bundle from the capital's
        # install, and the quarter holds no Commit permission on that
        # frontend (a copy attempt fails with "Caller does not have Commit
        # permission"). Skip unconditionally on quarters — even when a
        # frontend_canister_id is handed down via bootstrap args.
        from ggg import Realm as _Realm

        _realm = _Realm.load("1")
        if bool(getattr(_realm, "is_quarter", False)):
            logger.warning(
                f"Extension '{ext_id}': skipping frontend bundle copy — "
                f"quarter is backend-only (UI served by the shared frontend)"
            )
            frontend_files = []
        elif not fe_canister:
            # On a capital, UI-carrying extensions without a configured
            # frontend are a real misconfiguration worth failing.
            return json.dumps(
                {
                    "success": False,
                    "error": (
                        f"Extension '{ext_id}' has frontend files but no frontend_canister_id "
                        f"is configured on this realm"
                    ),
                }
            )
        if frontend_files:
            copy_err = yield from _copy_frontend_to_asset_canister(
                registry_canister_id,
                ext_id,
                resolved_version,
                fe_canister,
                files=frontend_files,
            )
            if copy_err:
                return json.dumps({"success": False, "error": copy_err})
            frontend_copied = len(frontend_files)

    from core.runtime_extensions import install_extension as _install

    if is_codex:
        from core.codex_overlay import preserve_current_as_previous, wipe_runtime_package

        # Snapshot *before* install_extension overwrites /extensions/{id}.
        preserve_current_as_previous()
        wipe_runtime_package(ext_id)

    ok = _install(
        ext_id,
        files,
        source_registry_id=registry_canister_id,
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

    init_error = None
    seeded_modules = []
    if is_codex:
        from core import codex_hooks

        # Governance module files become Codex DB entities (proposal targets).
        # Honor optional manifest ``codex_modules`` allow-list when present.
        seeded_modules = _seed_codex_module_entities(ext_id, files, manifest)
        from core.codex_overlay import commit_current, ensure_codex_revert_grants

        commit_current(ext_id, files, seeded_modules)
        # Agora (and other heavy codices) seed orgs/courts in init. Doing that
        # in the same 40B-instruction message as the package pull + overlay
        # copy hits IC0522. Callers that already installed deps as separate
        # steps pass run_init=False and follow up with run_codex_init.
        if run_init:
            init_error = codex_hooks.run_init(ext_id)
        try:
            ensure_codex_revert_grants()
        except Exception:
            pass

    result = {
        "success": True,
        "extension_id": ext_id,
        "version": resolved_version,
        "files_count": len(files),
        "frontend_files_copied": frontend_copied,
        "source": "registry",
        "registry_canister_id": registry_canister_id,
    }
    if is_codex:
        result["kind"] = "codex"
        result["dependencies_installed"] = installed_deps
        result["codex_modules"] = seeded_modules
        result["init_ran"] = bool(run_init)
        if failed_deps:
            result["dependency_warnings"] = failed_deps
        if init_error:
            result["init_warning"] = init_error
    return json.dumps(result)


def install_codex_from_registry(
    registry_canister_id: str,
    codex_id: str,
    version: str = None,
    run_init: bool = True,
    frontend_canister_id: str = None,
    install_dependencies: bool = True,
) -> Async[str]:
    """Install a codex via the realm-owned multi-message install job."""
    logger.info(
        f"Installing codex '{codex_id}' (version={version or 'latest'}) "
        f"from registry {registry_canister_id}"
    )
    from core.codex_install_job import continue_codex_install

    return (
        yield from continue_codex_install(
            registry_canister_id,
            codex_id,
            version,
            frontend_canister_id,
            install_dependencies,
            None,
            run_init,
            "auto",
        )
    )


def run_codex_init(codex_id: str) -> str:
    """Run a already-installed codex ``init`` hook in its own update.

    Split out of ``install_*_from_registry`` so Agora-sized packages stay
    under the 40B instruction limit (IC0522).
    """
    cid = (codex_id or "").strip()
    if not cid:
        return json.dumps({"success": False, "error": "codex_id is required"})
    from core import codex_hooks

    err = codex_hooks.run_init(cid)
    if err:
        return json.dumps({"success": False, "error": err, "codex_id": cid})
    return json.dumps({"success": True, "codex_id": cid, "init_ran": True})


_CONTENT_TYPES = {
    ".js": "application/javascript",
    ".json": "application/json",
    ".css": "text/css",
    ".html": "text/html",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".woff2": "font/woff2",
}


def _guess_content_type(path: str) -> str:
    for ext, ct in _CONTENT_TYPES.items():
        if path.endswith(ext):
            return ct
    return "application/octet-stream"


def _pin_frontend_prefix(frontend_principal: Principal, prefix: str) -> Async[None]:
    """Pin a directory prefix on the frontend asset canister. Non-fatal on failure."""
    pin_arg = f'(record {{ prefix = "{prefix}" }})'
    try:
        pin_result: CallResult = yield ic.call_raw(
            frontend_principal,
            "pin_directory",
            ic.candid_encode(pin_arg),
            0,
        )
        if isinstance(pin_result, dict) and pin_result.get("Err") is not None:
            logger.warning(f"pin_directory {prefix!r} failed: {pin_result['Err']}")
        elif hasattr(pin_result, "Err") and pin_result.Err is not None:
            logger.warning(f"pin_directory {prefix!r} failed: {pin_result.Err}")
    except Exception as e:
        logger.warning(f"pin_directory {prefix!r} exception: {e}")


def _unpin_frontend_prefix(frontend_principal: Principal, prefix: str) -> Async[None]:
    """Unpin a directory prefix on the frontend asset canister. Non-fatal on failure."""
    unpin_arg = f'(record {{ prefix = "{prefix}" }})'
    try:
        unpin_result: CallResult = yield ic.call_raw(
            frontend_principal,
            "unpin_directory",
            ic.candid_encode(unpin_arg),
            0,
        )
        if isinstance(unpin_result, dict) and unpin_result.get("Err") is not None:
            logger.warning(f"unpin_directory {prefix!r} failed: {unpin_result['Err']}")
        elif hasattr(unpin_result, "Err") and unpin_result.Err is not None:
            logger.warning(f"unpin_directory {prefix!r} failed: {unpin_result.Err}")
    except Exception as e:
        logger.warning(f"unpin_directory {prefix!r} exception: {e}")


def _extract_asset_key(entry) -> str:
    if isinstance(entry, dict):
        return str(entry.get("key") or "")
    if hasattr(entry, "key"):
        return str(getattr(entry, "key") or "")
    return ""


# The asset canister caps every `list` page at 100 entries, whatever `length`
# asks for. A single unpaginated call therefore returned only the first 100 keys
# — on a realm frontend that is all base bundle assets, so every `/ext/` key was
# invisible and callers concluded the extension bundles were absent.
_ASSET_LIST_PAGE = 100
_ASSET_LIST_MAX_PAGES = 200


def _list_frontend_asset_keys(frontend_principal: Principal) -> Async[list]:
    """Return all asset keys from the frontend canister, across every page."""
    asset = AssetCanisterService(frontend_principal)
    keys = []
    start = 0
    for _ in range(_ASSET_LIST_MAX_PAGES):
        list_res: CallResult = yield asset.list(
            {"start": start, "length": _ASSET_LIST_PAGE}
        )
        entries = _unwrap_call_result(list_res)
        if not isinstance(entries, list) or not entries:
            break
        keys.extend(k for k in (_extract_asset_key(e) for e in entries) if k)
        if len(entries) < _ASSET_LIST_PAGE:
            break
        start += len(entries)
    else:
        logger.warning(
            f"asset key listing hit the {_ASSET_LIST_MAX_PAGES}-page cap; "
            f"treating {len(keys)} keys as the full set"
        )
    return keys


# Sentinel from _copy_frontend_to_asset_canister: the package carries no
# frontend bundle at all, which is normal for codices and backend-only
# extensions. Distinct from a copy that was attempted and failed.
NO_FRONTEND_FILES = "__no_frontend_files__"


def _frontend_bundle_present(existing_keys: list, ext_id: str, version: str) -> bool:
    """True when this extension's bundle is already on the asset canister.

    The entry point is required, not just any key under the prefix: a bundle
    that was cut off mid-copy must still be re-copied. Sandboxed extensions
    serve ``index.html`` plus hashed assets, others a plain ``index.js``, so
    either entry point counts.
    """
    if not (ext_id and version):
        return False
    prefix = f"/ext/{ext_id}/{version}/"
    for key in existing_keys or []:
        if key.startswith(prefix) and key.rsplit("/", 1)[-1] in (
            "index.js",
            "index.html",
        ):
            return True
    return False


def _delete_frontend_asset_key(frontend_principal: Principal, key: str) -> Async[None]:
    """Delete a single asset key. Non-fatal on failure."""
    escaped = key.replace("\\", "\\\\").replace('"', '\\"')
    delete_arg = f'(record {{ key = "{escaped}" }})'
    try:
        delete_result: CallResult = yield ic.call_raw(
            frontend_principal,
            "delete_asset",
            ic.candid_encode(delete_arg),
            0,
        )
        if isinstance(delete_result, dict) and delete_result.get("Err") is not None:
            logger.warning(f"delete_asset {key!r} failed: {delete_result['Err']}")
        elif hasattr(delete_result, "Err") and delete_result.Err is not None:
            logger.warning(f"delete_asset {key!r} failed: {delete_result.Err}")
    except Exception as e:
        logger.warning(f"delete_asset {key!r} exception: {e}")


def cleanup_extension_frontend_on_uninstall(
    ext_id: str,
    version: str,
    frontend_canister_id: str,
) -> Async[None]:
    """Best-effort removal of ``/ext/{ext_id}/`` assets after extension uninstall."""
    frontend_id = (frontend_canister_id or "").strip()
    if not frontend_id:
        return

    frontend_principal = Principal.from_str(frontend_id)
    prefix = "/ext/"
    ext_prefix = f"/ext/{ext_id}/"

    try:
        yield from _unpin_frontend_prefix(frontend_principal, prefix)

        keys_to_delete = []
        try:
            all_keys = yield from _list_frontend_asset_keys(frontend_principal)
            keys_to_delete = [k for k in all_keys if k.startswith(ext_prefix)]
        except Exception as e:
            logger.warning(
                f"Extension '{ext_id}' uninstall: frontend list failed ({e}); "
                f"falling back to index.js delete"
            )
            if version:
                keys_to_delete = [
                    f"/ext/{ext_id}/{version}/frontend/dist/index.js"
                ]

        for key in keys_to_delete:
            yield from _delete_frontend_asset_key(frontend_principal, key)
    finally:
        yield from _pin_frontend_prefix(frontend_principal, prefix)


def _copy_frontend_to_asset_canister(
    registry_canister_id: str,
    ext_id: str,
    version: str,
    frontend_canister_id: str,
    files: dict = None,
) -> Async[Opt[str]]:
    """Fetch frontend files from the file registry and upload them to the
    realm's frontend asset canister under /ext/{ext_id}/{version}/...

    Returns None on success, or an error message string on failure.
    """
    logger.info(
        f"Copying frontend files for {ext_id}@{version} "
        f"from registry {registry_canister_id} to frontend {frontend_canister_id}"
    )

    pulled_from_registry = files is None
    if files is None:
        registry = FileRegistryService(Principal.from_str(registry_canister_id))
        files, resolved_version, err = yield from _pull_extension_frontend_files(
            registry, ext_id, version or ""
        )
        if err:
            err_obj = json.loads(err)
            return err_obj.get("error", err)
        version = resolved_version or version

    if not files:
        if pulled_from_registry:
            namespace, _, ns_err = yield from _resolve_registry_namespace(
                registry, "ext", ext_id, version or ""
            )
            if ns_err:
                err_obj = json.loads(ns_err)
                return err_obj.get("error", ns_err)
            # Backend-only packages (codices, extensions with no UI) legitimately
            # ship no bundle, and the install path treats that as nothing to copy.
            # Returning a hard error here made every resync of such a package fail.
            logger.info(
                f"{ext_id}@{version}: no frontend files in registry "
                f"namespace {namespace}"
            )
            return NO_FRONTEND_FILES
        return None

    resolved_version = version
    copied = 0
    errors = []
    frontend_principal = Principal.from_str(frontend_canister_id)

    for path, content in files.items():
        asset_key = f"/ext/{ext_id}/{resolved_version}/{path}"
        content_type = _guess_content_type(path)

        escaped = content.replace("\\", "\\\\").replace('"', '\\"')
        candid_arg = (
            f'(record {{ key = "{asset_key}"; '
            f'content_type = "{content_type}"; '
            f'content_encoding = "identity"; '
            f'content = blob "{escaped}"; '
            f"sha256 = null }})"
        )

        try:
            store_result: CallResult = yield ic.call_raw(
                frontend_principal,
                "store",
                ic.candid_encode(candid_arg),
                0,
            )
            if isinstance(store_result, dict) and "Err" in store_result:
                msg = f"{asset_key}: {store_result['Err']}"
                errors.append(msg)
                logger.error(f"Frontend store failed for {msg}")
            else:
                copied += 1
        except Exception as e:
            msg = f"{asset_key}: {e}"
            errors.append(msg)
            logger.error(f"Frontend store exception for {msg}")

    logger.info(
        f"Copied {copied}/{len(files)} frontend files for {ext_id}@{resolved_version}"
    )
    if errors:
        preview = "; ".join(errors[:3])
        if len(errors) > 3:
            preview += f"; ... and {len(errors) - 3} more"
        return (
            f"Failed to copy frontend files for '{ext_id}@{resolved_version}' "
            f"({copied}/{len(files)} succeeded): {preview}"
        )
    if copied > 0:
        yield from _pin_frontend_prefix(frontend_principal, "/ext/")
    return None


def resync_extension_frontends(
    registry_canister_id: str = "",
    frontend_canister_id: str = None,
    extension_ids: list = None,
    force: bool = False,
) -> Async[str]:
    """Re-copy installed extensions' frontend bundles to the realm frontend.

    Frontend asset canister reinstalls (and some wizard deploy paths before
    ``frontend_canister_id`` was configured) leave ``/ext/{id}/{ver}/...``
    missing. The SPA fallback then serves ``text/html`` for those URLs and
    runtime extension loading fails with a MIME type error.

    Bundles already present are left alone: ``install_extension_from_registry``
    copies them at install time, so a re-copy costs ~a minute per extension to
    write bytes that are already there. Pass ``force`` to re-copy regardless.
    """
    from core.runtime_extensions import (
        get_extension_source,
        list_installed,
        _load_manifest,
    )

    fe_canister = (
        frontend_canister_id or _get_realm_frontend_canister_id() or ""
    ).strip()
    if not fe_canister:
        return json.dumps(
            {
                "success": False,
                "error": "no frontend_canister_id configured on this realm",
            }
        )

    yield from _pin_frontend_prefix(Principal.from_str(fe_canister), "/ext/")

    default_registry = (registry_canister_id or "").strip()
    if not default_registry:
        try:
            from ggg import Realm

            realms = Realm.instances()
            if realms:
                default_registry = (
                    getattr(realms[0], "file_registry_canister_id", "") or ""
                ).strip()
        except Exception:
            pass

    ext_ids = extension_ids or list_installed()
    synced = []
    skipped = []
    errors = []

    # One list call covers every extension. If it fails we simply copy
    # everything, which is the old behaviour.
    existing_keys = []
    if not force:
        try:
            existing_keys = yield from _list_frontend_asset_keys(
                Principal.from_str(fe_canister)
            )
        except Exception as e:
            logger.warning(f"could not list frontend assets, copying all: {e}")
            existing_keys = []

    for ext_id in ext_ids:
        src = get_extension_source(ext_id) or {}
        manifest = _load_manifest(ext_id) or {}
        version = str(manifest.get("version") or src.get("version") or "")
        reg_id = (src.get("registry_canister_id") or default_registry or "").strip()

        if not version:
            skipped.append({"extension_id": ext_id, "reason": "no installed version"})
            continue
        if not reg_id:
            skipped.append(
                {"extension_id": ext_id, "reason": "no registry_canister_id"}
            )
            continue

        if _frontend_bundle_present(existing_keys, ext_id, version):
            skipped.append(
                {
                    "extension_id": ext_id,
                    "version": version,
                    "reason": "already present",
                }
            )
            continue

        copy_err = yield from _copy_frontend_to_asset_canister(
            reg_id,
            ext_id,
            version,
            fe_canister,
        )
        if copy_err == NO_FRONTEND_FILES:
            skipped.append(
                {
                    "extension_id": ext_id,
                    "version": version,
                    "reason": "no frontend bundle",
                }
            )
        elif copy_err:
            errors.append(
                {"extension_id": ext_id, "version": version, "error": copy_err}
            )
        else:
            synced.append({"extension_id": ext_id, "version": version})

    return json.dumps(
        {
            "success": len(errors) == 0,
            "synced": synced,
            "skipped": skipped,
            "errors": errors,
        }
    )


def install_branding_from_registry(
    registry_canister_id: str,
    namespace: str,
    files_map: dict,
    frontend_canister_id: str,
) -> Async[str]:
    """Pull per-realm branding images from the file registry and upload them to
    the realm's frontend asset canister (e.g. /custom/logo.png,
    /custom/background.png) so they are served same-origin after a reinstall.

    Args:
        registry_canister_id: file registry canister ID
        namespace: registry namespace holding the branding files (e.g. "branding")
        files_map: {asset_key: registry_path}, e.g.
            {"/custom/logo.png": "dominion/logo.png",
             "/custom/background.png": "dominion/background.png"}
        frontend_canister_id: the realm's frontend asset canister

    Returns (via yield): JSON string with {success, uploaded, errors}.
    """
    if not registry_canister_id:
        return json.dumps(
            {"success": False, "error": "registry_canister_id is required"}
        )
    if not frontend_canister_id:
        return json.dumps(
            {"success": False, "error": "frontend_canister_id is required"}
        )
    if not files_map:
        return json.dumps({"success": False, "error": "files is required"})

    registry = FileRegistryService(Principal.from_str(registry_canister_id))
    asset = AssetCanisterService(Principal.from_str(frontend_canister_id))

    uploaded = []
    errors = {}
    for asset_key, reg_path in files_map.items():
        try:
            data = yield from _pull_file_bytes(registry, namespace, reg_path)
        except Exception as e:
            errors[asset_key] = f"pull failed: {e}"
            logger.warning(f"branding pull failed for {namespace}/{reg_path}: {e}")
            continue
        if not data:
            errors[asset_key] = "empty file"
            continue

        content_type = _guess_content_type(asset_key)
        try:
            store_res: CallResult = yield asset.store(
                {
                    "key": asset_key,
                    "content_type": content_type,
                    "content_encoding": "identity",
                    "content": data,
                    "sha256": None,
                }
            )
            if isinstance(store_res, dict) and "Err" in store_res:
                errors[asset_key] = f"store failed: {store_res['Err']}"
                logger.warning(
                    f"branding store failed for {asset_key}: {store_res['Err']}"
                )
            else:
                uploaded.append(asset_key)
                logger.info(f"branding uploaded {asset_key} ({len(data)} bytes)")
        except Exception as e:
            errors[asset_key] = f"store exception: {e}"
            logger.warning(f"branding store exception for {asset_key}: {e}")

    return json.dumps(
        {
            "success": len(errors) == 0,
            "uploaded": uploaded,
            "errors": errors,
            "namespace": namespace,
            "registry_canister_id": registry_canister_id,
            "frontend_canister_id": frontend_canister_id,
        }
    )
