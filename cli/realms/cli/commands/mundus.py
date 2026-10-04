"""Mundus deployment commands.

Implements the deployment pipeline described in REWRITE.md:
1. Parse descriptor YAML (deployment-descriptors/*.yml)
2. For each realm: resolve artifacts, upload to deployer, submit manifest, poll
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Optional

import typer
import yaml
from rich.console import Console

from ..descriptor_flags import TEST_PARAM_MAP
from ..utils import console, get_project_root

DEPLOYER_URL = os.environ.get("DEPLOYER_URL", "https://deploy.realmsgos.dev")
POLL_INTERVAL_S = 10
POLL_TIMEOUT_S = 3600

# Which GOS queue a descriptor deploys through is the descriptor's business:
# its ``infra`` block names ``registry_canister_id`` and ``installer_canister_id``
# (the realm-registry / realm-installer of the environment, as `casals export`
# lists them). There is no per-network table in the CLI.
_QUEUE_KEYS = ("registry_canister_id", "installer_canister_id")


def _queue_ids(infra: dict | None) -> tuple[str, str]:
    """(registry_id, installer_id) from a descriptor's ``infra`` block; fails loud."""
    infra = infra or {}
    missing = [k for k in _QUEUE_KEYS if not str(infra.get(k, "")).strip()]
    if missing:
        raise typer.BadParameter(
            "descriptor infra block must name " + ", ".join(missing)
            + " (the environment's realm-registry / realm-installer ids, see `casals export`)"
        )
    return str(infra["registry_canister_id"]).strip(), str(infra["installer_canister_id"]).strip()


def _dfx_call(canister_id: str, method: str, arg: str, network: str, *, query: bool = False) -> str:
    cmd = ["dfx", "canister", "call", canister_id, method, arg, "--network", network, "--output", "json"]
    if query:
        cmd.append("--query")
    project_root = get_project_root()
    env = {**os.environ, "TERM": "xterm", "DFX_WARNING": "-mainnet_plaintext_identity"}
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=project_root, env=env)
    if result.returncode != 0:
        raise RuntimeError(f"dfx call failed: {result.stderr}")
    return result.stdout.strip()


def _dfx_call_file(canister_id: str, method: str, arg: str, network: str,
                    *, candid_file: str = None) -> str:
    """Like _dfx_call but writes the argument to a temp file (for large blobs)."""
    import re as _re
    with tempfile.NamedTemporaryFile(mode="w", suffix=".candid", delete=False) as f:
        f.write(arg)
        f.flush()
        try:
            cmd = ["dfx", "canister", "call", canister_id, method,
                   "--argument-file", f.name, "--network", network]
            if candid_file:
                cmd += ["--candid", candid_file]
            project_root = get_project_root()
            env = {**os.environ, "TERM": "xterm", "DFX_WARNING": "-mainnet_plaintext_identity"}
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    cwd=project_root, env=env, timeout=300)
            if result.returncode != 0:
                raise RuntimeError(f"dfx {method} failed: {result.stderr}")
            return result.stdout.strip()
        finally:
            os.unlink(f.name)


def _upload_file(filepath: Path) -> str:
    """Upload a file to the deployer and return its URL."""
    import urllib.request
    import urllib.parse

    url = f"{DEPLOYER_URL}/upload-file"
    boundary = "----RealmsUploadBoundary"
    filename = filepath.name
    data = filepath.read_bytes()

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read())
    return f"{DEPLOYER_URL}{result['url']}"


_build_cache: dict[str, str] = {}
_BUILD_VARIANTS = ("production", "test")


def _default_build_variant(network: str) -> str:
    """Test IC environments need the test WASM/frontend; production is the default."""
    return "test" if (network or "").strip().lower() == "test" else "production"


def _sha256_file(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _download_and_hash(url: str) -> str:
    """Download a URL to a temp file, return its SHA-256 hex digest."""
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        urllib.request.urlretrieve(url, tmp_path)
        return hashlib.sha256(tmp_path.read_bytes()).hexdigest()
    finally:
        tmp_path.unlink(missing_ok=True)


def _build_artifacts(
    scope: str = "both", variant: str = "production"
) -> dict[str, Path]:
    """Build backend WASM and/or frontend tarball from source.

    scope: "both" (default), "frontend_only", or "backend_only".
    When scope is "frontend_only", skips backend compilation and uses existing
    declarations from the source tree — significantly faster for frontend-only deploys.

    variant: ``production`` (default) or ``test``. Test IC sheet realms need the
    test variant so II bypass and other test-only code paths are compiled in.
    """
    import gzip
    import tarfile

    if variant not in _BUILD_VARIANTS:
        raise ValueError(f"unknown build variant {variant!r}; expected {_BUILD_VARIANTS}")

    project_root = get_project_root()

    build_env = {
        **os.environ,
        "CANISTER_CANDID_PATH": str(project_root / "src" / "realm_backend" / "realm_backend.did"),
        "REALMS_BUILD_VARIANT": variant,
    }

    artifacts = {}

    if scope != "frontend_only":
        console.print("  Building backend WASM...")
        pack_script = project_root / "scripts" / "pack_realm_backend.py"
        result = subprocess.run(
            [sys.executable, str(pack_script), "--variant", variant],
            cwd=project_root, capture_output=True, text=True, env=build_env,
        )
        if result.returncode != 0:
            console.print(f"[red]  Backend build failed (exit code {result.returncode})[/red]")
            console.print(f"[red]  stdout:[/red]\n{result.stdout or '(empty)'}")
            console.print(f"[red]  stderr:[/red]\n{result.stderr or '(empty)'}")
            raise RuntimeError(
                f"Backend build failed (exit code {result.returncode}):\n"
                f"--- stdout ---\n{result.stdout or '(empty)'}\n"
                f"--- stderr ---\n{result.stderr or '(empty)'}"
            )

        wasm_path = project_root / ".basilisk" / "realm_backend" / "realm_backend.wasm"
        if not wasm_path.exists():
            raise FileNotFoundError(f"WASM not found at {wasm_path}")

        wasm_gz = Path(tempfile.mktemp(suffix=".wasm.gz", prefix="realm_backend_"))
        with open(wasm_path, "rb") as f_in, gzip.open(wasm_gz, "wb") as f_out:
            f_out.write(f_in.read())
        console.print(f"  Backend WASM built ({wasm_gz.stat().st_size // 1024} KB)")
        artifacts["realm_backend"] = wasm_gz

        console.print("  Generating Candid declarations...")
        result = subprocess.run(
            ["dfx", "generate", "realm_backend"],
            cwd=project_root, capture_output=True, text=True, env=build_env,
        )
        if result.returncode != 0:
            console.print(f"  [yellow]dfx generate warning: {result.stderr[:200]}[/yellow]")

    if scope != "backend_only":
        import shutil
        decl_src = project_root / "src" / "declarations" / "realm_backend"
        decl_dst = project_root / "src" / "realm_frontend" / "src" / "lib" / "declarations" / "realm_backend"
        if decl_src.exists():
            decl_dst.parent.mkdir(parents=True, exist_ok=True)
            if decl_dst.exists():
                shutil.rmtree(decl_dst)
            shutil.copytree(decl_src, decl_dst)
            console.print("  Declarations copied to frontend")

        console.print("  Building frontend...")
        fe_dir = project_root / "src" / "realm_frontend"
        # Workspace frontends hoist shared deps from the repo root; installing
        # only in the subdirectory leaves vite/svelte-kit unresolved.
        result = subprocess.run(
            ["npm", "install", "--legacy-peer-deps"],
            cwd=project_root,
            capture_output=True,
            text=True,
            env=build_env,
        )
        if result.returncode != 0:
            raise RuntimeError(f"npm install failed:\n{result.stderr}")
        result = subprocess.run(["npm", "run", "build"], cwd=fe_dir, capture_output=True, text=True, env=build_env)
        if result.returncode != 0:
            raise RuntimeError(f"Frontend build failed:\n{result.stderr}")

        dist_dir = fe_dir / "dist"
        if not dist_dir.exists():
            dist_dir = fe_dir / "build"
        if not dist_dir.exists():
            raise FileNotFoundError("Frontend dist/build directory not found")

        tarball = Path(tempfile.mktemp(suffix=".tar.gz", prefix="realm_frontend_"))
        with tarfile.open(tarball, "w:gz") as tar:
            for item in dist_dir.iterdir():
                tar.add(item, arcname=item.name)
        console.print(f"  Frontend tarball built ({tarball.stat().st_size // 1024} KB)")
        artifacts["realm_frontend"] = tarball

    return artifacts


def _resolve_artifact(
    ref: str, artifact_type: str, network: str, variant: str = "production"
) -> tuple[str, str]:
    """Resolve an artifact reference to a (URL, SHA-256 hash) tuple.

    The CLI computes the hash locally so it can be included in the
    manifest as the pre-approved expected hash.  The on-chain installer
    will later verify the deployed canister's module_hash against this
    value, independent of what the off-chain deployer reports.

    Supported formats:
      - URL (https://...) -> downloaded, hashed, returned
      - version (e.g. "0.3.2" or "v0.3.2") -> GitHub release URL
      - "latest" -> latest GitHub release
      - "build" -> compile from source, upload to deployer
      - local path -> upload to deployer
    """
    if ref.startswith("http://") or ref.startswith("https://"):
        console.print(f"  Hashing remote artifact: {ref}")
        h = _download_and_hash(ref)
        return ref, h

    if ref == "build":
        cache_key = f"build:{variant}:{artifact_type}"
        if cache_key in _build_cache:
            return _build_cache[cache_key]
        if artifact_type == "realm_frontend":
            scope = "frontend_only"
        elif artifact_type == "realm_backend":
            scope = "backend_only"
        else:
            scope = "both"
        artifacts = _build_artifacts(scope=scope, variant=variant)
        for atype, path in artifacts.items():
            h = _sha256_file(path)
            console.print(f"  Uploading {atype}: {path.name} (sha256={h[:16]}...)")
            url = _upload_file(path)
            _build_cache[f"build:{variant}:{atype}"] = (url, h)
            path.unlink(missing_ok=True)
        return _build_cache[cache_key]

    repo = "smart-social-contracts/realms-gos"
    if ref == "latest":
        gh_url = f"https://api.github.com/repos/{repo}/releases/latest"
        with urllib.request.urlopen(gh_url, timeout=30) as resp:
            release = json.loads(resp.read())
        version = release["tag_name"].lstrip("v")
    elif ref.lstrip("v").replace(".", "").isdigit():
        version = ref.lstrip("v")
    else:
        local_path = Path(ref)
        if local_path.exists():
            h = _sha256_file(local_path)
            console.print(f"  Uploading local artifact: {local_path.name} (sha256={h[:16]}...)")
            url = _upload_file(local_path)
            return url, h
        raise ValueError(f"Unknown artifact reference: {ref}")

    if artifact_type == "realm_backend":
        url = f"https://github.com/{repo}/releases/download/v{version}/realm_backend.wasm.gz"
    elif artifact_type == "realm_frontend":
        url = f"https://github.com/{repo}/releases/download/v{version}/realm_frontend.tar.gz"
    else:
        raise ValueError(f"Unknown artifact type: {artifact_type}")

    console.print(f"  Hashing release artifact: {url}")
    h = _download_and_hash(url)
    return url, h


def _build_manifest(realm_entry: dict, network: str, deploy_mode: str,
                    backend_url: str, frontend_url: str,
                    canister_filter: str = "", infra: dict | None = None,
                    expected_hashes: dict | None = None,
                    skip_extensions: bool = False,
                    extension_names: list[str] | None = None,
                    codex_names: list[str] | None = None) -> dict:
    """Build a deployment manifest for a single realm.

    canister_filter: "" = both, "backend" = backend only, "frontend" = frontend only.
    infra: shared infrastructure canister IDs from the descriptor's ``infra`` section.
    expected_hashes: CLI-computed SHA-256 hashes for integrity verification.
    skip_extensions: if True, strip extensions/codex from manifest so installer skips that phase.
    extension_names: if provided, only include these extension IDs (empty list = none).
    codex_names: if provided, only include these codex IDs (empty list = none).
    """
    registry_id, installer_id = _queue_ids(infra)
    project_root = get_project_root()
    manifest_path = realm_entry.get("manifest", "")
    realm_manifest = {}
    manifest_dir = None
    if manifest_path:
        full_path = project_root / manifest_path
        if full_path.exists():
            realm_manifest = json.loads(full_path.read_text())
            manifest_dir = full_path.parent

    branding = {}
    if manifest_dir:
        for name, key in [("logo.png", "logo"), ("background.png", "background")]:
            img = manifest_dir / name
            if img.exists():
                console.print(f"  Uploading branding: {name}")
                branding[key] = _upload_file(img)

    artifacts = {}

    if canister_filter != "frontend":
        artifacts["realm_backend"] = backend_url
    if canister_filter != "backend":
        artifacts["realm_frontend"] = frontend_url

    # Always include full canister IDs for registration, regardless of deploy scope
    canister_ids = {
        "backend": realm_entry.get("canister_id", ""),
        "frontend": realm_entry.get("frontend_canister_id", ""),
    }

    # Determine deploy_scope for the installer/deployer
    if canister_filter == "frontend":
        deploy_scope = "frontend_only"
    elif canister_filter == "backend":
        deploy_scope = "backend_only"
    else:
        deploy_scope = "both"

    realm_data = {
        "name": realm_entry.get("display_name", realm_entry.get("name", "")),
        **{k: v for k, v in realm_manifest.items() if k not in ("name",)},
    }

    if skip_extensions:
        realm_data.pop("extensions", None)
        realm_data.pop("codex", None)
    else:
        if extension_names is not None:
            if len(extension_names) == 0:
                realm_data.pop("extensions", None)
            elif "extensions" in realm_data:
                allowed = set(extension_names)
                realm_data["extensions"] = [
                    e for e in realm_data["extensions"] if e in allowed
                ]
        if codex_names is not None:
            if len(codex_names) == 0:
                realm_data.pop("codex", None)
            elif "codex" in realm_data and isinstance(realm_data["codex"], dict):
                pkg = realm_data["codex"].get("package", {})
                pkg_name = pkg.get("name", "")
                if pkg_name not in codex_names:
                    realm_data.pop("codex", None)

    result = {
        "name": realm_entry.get("display_name", realm_entry.get("name", "unknown")),
        "network": network,
        "deploy_mode": deploy_mode,
        "deploy_scope": deploy_scope,
        "artifacts": artifacts,
        "canister_ids": canister_ids,
        "realm": realm_data,
        "registry_canister_id": registry_id,
        "installer_canister_id": installer_id,
    }
    if branding:
        result["branding"] = branding
    if infra:
        result["infra"] = infra
    if expected_hashes:
        result["expected_hashes"] = expected_hashes
    return result


def _format_elapsed(seconds: int) -> str:
    """Format seconds as human-readable duration."""
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    return f"{minutes}m{secs:02d}s"


def _poll_status_line(elapsed: int, status: str, detail: str = "") -> str:
    """Build a formatted poll status line."""
    ts = _format_elapsed(elapsed)
    line = f"  [{ts}] {status}"
    if detail:
        line += f" — {detail}"
    return line


_ASSET_COMMIT_DID = """
type BatchOperationKind = variant {
    CreateAsset : record { key : text; content_type : text; max_age : opt nat64;
        headers : opt vec record { text; text }; enable_aliasing : opt bool;
        allow_raw_access : opt bool; };
    SetAssetContent : record { key : text; content_encoding : text;
        chunk_ids : vec nat; sha256 : opt blob; };
    UnsetAssetContent : record { key : text; content_encoding : text; };
    DeleteAsset : record { key : text; };
    Clear : record {};
};
service : { commit_batch : (record { batch_id : nat; operations : vec BatchOperationKind }) -> (); }
"""

_BRANDING_STORE_LIMIT = 1_900_000  # files under this use the simpler store() call
_BRANDING_CHUNK_SIZE = 1_500_000


def _parse_nat(output: str) -> int:
    """Extract a nat from Candid text or JSON output."""
    import re as _re
    # Candid text: (record { 1_309_252_224 = 3 : nat })
    m = _re.search(r'=\s*(\d[\d_]*)\s*:\s*nat', output)
    if m:
        return int(m.group(1).replace('_', ''))
    # JSON: {"batch_id": 3} or just a number
    try:
        import json as _json
        obj = _json.loads(output)
        if isinstance(obj, int):
            return obj
        if isinstance(obj, dict):
            for v in obj.values():
                if isinstance(v, int):
                    return v
        if isinstance(obj, list) and obj and isinstance(obj[0], dict):
            for v in obj[0].values():
                if isinstance(v, int):
                    return v
    except (ValueError, TypeError):
        pass
    raise ValueError(f"Cannot parse nat from: {output}")


def _upload_branding_to_canister(frontend_id: str, manifest_dir: Path, network: str) -> None:
    """Upload logo.png and background.png from manifest_dir to /custom/ on the frontend canister."""
    files = [("logo.png", "/custom/logo.png"), ("background.png", "/custom/background.png")]
    uploaded_any = False

    for filename, asset_key in files:
        src = manifest_dir / filename
        if not src.exists():
            continue

        data = src.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        console.print(f"  🖼️  Uploading {filename} ({len(data)} bytes)")

        try:
            if len(data) < _BRANDING_STORE_LIMIT:
                blob_hex = "".join(f"\\{b:02x}" for b in data)
                arg = (f'(record {{ key="{asset_key}"; content_type="image/png"; '
                       f'content=blob "{blob_hex}"; content_encoding="identity"; sha256=null }})')
                _dfx_call_file(frontend_id, "store", arg, network)
            else:
                # Chunked upload for large files
                result = _dfx_call_file(frontend_id, "create_batch", "(record {})", network)
                batch_id = _parse_nat(result)
                chunk_ids = []
                for i in range(0, len(data), _BRANDING_CHUNK_SIZE):
                    chunk = data[i:i + _BRANDING_CHUNK_SIZE]
                    blob_hex = "".join(f"\\{b:02x}" for b in chunk)
                    arg = f'(record {{ batch_id = {batch_id} : nat; content = blob "{blob_hex}" }})'
                    result = _dfx_call_file(frontend_id, "create_chunk", arg, network)
                    chunk_ids.append(_parse_nat(result))

                sha_bytes = bytes.fromhex(sha)
                sha_blob = "".join(f"\\{b:02x}" for b in sha_bytes)
                chunk_ids_str = "; ".join(f"{c} : nat" for c in chunk_ids)

                did_path = tempfile.mktemp(suffix=".did")
                Path(did_path).write_text(_ASSET_COMMIT_DID)
                try:
                    commit_arg = (
                        f'(record {{ batch_id = {batch_id} : nat; operations = vec {{'
                        f' variant {{ CreateAsset = record {{ key = "{asset_key}"; '
                        f'content_type = "image/png"; max_age = opt (31536000 : nat64); '
                        f'headers = opt vec {{}}; enable_aliasing = opt false; '
                        f'allow_raw_access = opt true; }} }};'
                        f' variant {{ SetAssetContent = record {{ key = "{asset_key}"; '
                        f'content_encoding = "identity"; '
                        f'chunk_ids = vec {{ {chunk_ids_str} }}; '
                        f'sha256 = opt blob "{sha_blob}"; }} }};'
                        f' }}; }})')
                    _dfx_call_file(frontend_id, "commit_batch", commit_arg, network,
                                   candid_file=did_path)
                finally:
                    Path(did_path).unlink(missing_ok=True)

            uploaded_any = True
            console.print(f"       ✅ {asset_key}")
        except Exception as e:
            console.print(f"  [yellow]⚠ Upload {filename} failed: {e}[/yellow]")

    if not uploaded_any:
        console.print("  [dim]No branding files found in manifest directory[/dim]")


def _staging_test_identity_js(network: str, infra: dict | None = None) -> str:
    """Append test-identity roster globals for the II-bypass picker.

    Only when the descriptor's ``infra.test_identities_config`` names a roster;
    no environment gets one by network name."""
    config_rel = (infra or {}).get("test_identities_config") or ""
    if not config_rel:
        return ""
    config_path = get_project_root() / config_rel
    if not config_path.is_file():
        return ""
    script = get_project_root() / "scripts" / "staging_test_identities.py"
    if not script.is_file():
        return ""
    try:
        return subprocess.check_output(
            [sys.executable, str(script), "--print-js", "--config", str(config_path)],
            text=True,
        ).strip()
    except subprocess.CalledProcessError as e:
        console.print(f"  [yellow]⚠ test identity JS build failed: {e}[/yellow]")
        return ""


def _normalize_slug(raw: str) -> str:
    """Mirror the registry's slug normalization (api/slugs.py normalize_slug)."""
    s = (raw or "").strip().lower()
    out: list[str] = []
    prev_hyphen = False
    for ch in s:
        if "a" <= ch <= "z" or "0" <= ch <= "9":
            out.append(ch)
            prev_hyphen = False
        elif not prev_hyphen and out:
            out.append("-")
            prev_hyphen = True
    return "".join(out).strip("-")[:48]


def _resolve_portal_url(realm: dict, network: str, derivation_origin: str, registry_id: str = "") -> str:
    """Canonical portal page for this realm (drives the raw-origin → portal redirect).

    Asks the registry to resolve the realm's slug; falls back to
    ``<derivation_origin>/r/<slug>`` when the lookup fails. Empty when there is
    no portal origin (e.g. local dev).
    """
    slug = _normalize_slug(realm.get("display_name") or realm.get("name") or "")
    if not slug or not derivation_origin:
        return ""
    if registry_id:
        try:
            raw = _dfx_call(registry_id, "resolve_slug", f'("{slug}")', network)
            payload = json.loads(raw)
            inner = json.loads(payload.get("Ok", "{}"))
            if inner.get("success") and inner.get("portal_url"):
                return str(inner["portal_url"]).rstrip("/")
        except Exception as e:
            console.print(f"  [dim]resolve_slug({slug}) failed, using fallback portal_url: {e}[/dim]")
    return f"{derivation_origin.rstrip('/')}/r/{slug}"


def _canister_ids_bool_fields(network: str, parameters: dict | None = None) -> dict[str, bool]:
    """Sync hints written into /canister_ids.js for portal iframe boot."""
    fields: dict[str, bool] = {}
    if parameters and "TEST_MODE_II_BYPASS" in parameters:
        # Only what the descriptor declares; no environment gets a bypass by name.
        fields["test_mode_ii_bypass"] = bool(parameters["TEST_MODE_II_BYPASS"])
    return fields


def _store_canister_ids(frontend_id: str, backend_id: str, network: str,
                         file_registry_id: str = "", derivation_origin: str = "",
                         portal_url: str = "",
                         infra: dict | None = None,
                         parameters: dict | None = None) -> None:
    """(Re)write /canister_ids.js onto the frontend canister.

    This file is NOT part of the built asset bundle — it carries the realm's
    backend canister id at runtime (read by the SPA as globalThis.__CANISTER_IDS).
    The off-chain asset deploy clears+replaces the bundle, so if the installer's
    registration step doesn't re-inject it last, the SPA loads with no backend id
    ("Actor not initialized" → every extension/sidebar call fails). We re-assert
    it here as the final post-deploy step so off-chain deploys are self-healing.

    ``derivation_origin`` pins the Internet Identity ``derivationOrigin`` so this
    realm resolves to the same principal as the registry + other realms (one human
    → one principal). It must be the canonical origin listing this frontend in its
    ``/.well-known/ii-alternative-origins`` (the registry). See issue #233.
    """
    if not frontend_id or not backend_id:
        return
    fields = ['realm_backend:"' + backend_id + '"',
              'internet_identity:"https://identity.ic0.app"']
    if file_registry_id:
        fields.append('file_registry:"' + file_registry_id + '"')
    if derivation_origin:
        fields.append('derivation_origin:"' + derivation_origin + '"')
    if portal_url:
        # Drives the raw-origin → portal redirect in the SPA (auth.js
        # getPortalRedirectUrl); without it a direct icp0.io visit tries II
        # login on the raw origin and fails with "Unverified origin".
        fields.append('portal_url:"' + portal_url + '"')
    for key, value in _canister_ids_bool_fields(network, parameters).items():
        fields.append(f"{key}:{'true' if value else 'false'}")
    js = 'globalThis.__CANISTER_IDS={' + ",".join(fields) + '};'
    js += _staging_test_identity_js(network, infra)
    escaped = js.replace('\\', '\\\\').replace('"', '\\"')
    arg = ('(record { key = "/canister_ids.js"; content_type = "application/javascript"; '
           'content_encoding = "identity"; content = blob "' + escaped + '"; sha256 = null })')
    try:
        _dfx_call_file(frontend_id, "store", arg, network)
        console.print("  📍 canister_ids.js re-stored on frontend")
    except Exception as e:
        console.print(f"  [yellow]⚠ canister_ids.js store failed: {e}[/yellow]")


def _should_resync_extension_frontends(canister_filter: str) -> bool:
    """Frontend (re)installs wipe ``/ext/``; backend-only deploys do not."""
    return canister_filter != "backend"


def _resync_frontends_payload(frontend_id: str, registry_id: str = "") -> str:
    payload: dict[str, str] = {"frontend_canister_id": frontend_id}
    if registry_id:
        payload["registry_canister_id"] = registry_id
    return '("' + json.dumps(payload).replace("\\", "\\\\").replace('"', '\\"') + '")'


def _resync_extension_frontends_after_deploy(
    realm: dict, network: str, infra: dict | None = None,
) -> None:
    """Copy installed extension bundles back onto the realm frontend.

    Frontend asset deploys replace certified assets and drop ``/ext/{id}/{ver}/...``.
    Sandboxed extensions then 404 on ``index.html`` (plain "not found" in the iframe).
    Casals rollout already resyncs; mundus must do the same.
    """
    from .extension import _dfx_call as _icp_call
    from .files import file_registry_id_for

    backend_id = (realm.get("canister_id") or "").strip()
    frontend_id = (realm.get("frontend_canister_id") or "").strip()
    if not backend_id or not frontend_id:
        return

    registry = (
        (infra or {}).get("file_registry_canister_id")
        or file_registry_id_for(network)
        or ""
    ).strip()

    try:
        _icp_call(
            frontend_id,
            "grant_permission",
            f'(record {{ to_principal = principal "{backend_id}"; '
            f"permission = variant {{ Commit }} }})",
            network,
            None,
        )
        console.print("  Granted Commit on frontend to realm backend")
    except Exception as e:
        console.print(f"  [yellow]⚠ grant_permission Commit (non-fatal): {e}[/yellow]")

    candid = _resync_frontends_payload(frontend_id, registry)
    console.print("  Resyncing extension frontends onto the new asset canister...")
    raw = _icp_call(
        backend_id, "resync_extension_frontends", candid, network, None, timeout=600,
    )
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"resync_extension_frontends: unreadable response: {raw}") from e
    if not result.get("success"):
        errors = result.get("errors") or []
        detail = result.get("error") or errors or raw
        raise RuntimeError(f"resync_extension_frontends failed: {detail}")
    synced = result.get("synced") or []
    console.print(f"  ✓ Resynced {len(synced)} extension frontend bundle(s)")


_PINNED_FRONTEND_PREFIXES = ("/custom/", "/ext/")


def _pin_frontend_directories(frontend_id: str, network: str) -> None:
    for prefix in _PINNED_FRONTEND_PREFIXES:
        try:
            _dfx_call(frontend_id, "pin_directory", f'(record {{ prefix = "{prefix}" }})', network)
            console.print(f"  📌 Pinned {prefix} on frontend canister")
        except Exception as e:
            console.print(f"  [yellow]⚠ pin_directory {prefix} failed (non-fatal): {e}[/yellow]")


def _post_deploy_config(realm: dict, network: str, version: str, parameters: dict = None,
                         infra: dict | None = None, resync_frontends: bool = False) -> None:
    """Call set_canister_config on a realm backend after successful deployment.

    Wires frontend_canister_id, installed_version, network, and test flags into
    the realm's DB. After a frontend deploy, recopies ``/ext/`` bundles that the
    asset canister reinstall wiped.
    """
    backend_id = realm.get("canister_id", "")
    frontend_id = realm.get("frontend_canister_id", "")
    if not backend_id:
        return

    def opt(v):
        if not v:
            return "null"
        escaped = str(v).replace('\\', '\\\\').replace('"', '\\"')
        return f'opt "{escaped}"'

    # Build test_flags_json from deployment descriptor parameters
    test_flags_json = ""
    if parameters:
        flags = {}
        for param_name, flag_key in TEST_PARAM_MAP.items():
            if param_name in parameters:
                flags[flag_key] = bool(parameters[param_name])
        if flags:
            test_flags_json = json.dumps(flags)

    arg = f'({opt(frontend_id)}, null, null, null, null, {opt(version)}, {opt(network)}, {opt(test_flags_json)})'

    parts = []
    if frontend_id:
        parts.append(f"frontend={frontend_id}")
    if version:
        parts.append(f"version={version}")
    if network:
        parts.append(f"network={network}")
    if test_flags_json:
        parts.append(f"test_flags={test_flags_json}")
    if not parts:
        return

    console.print(f"  Configuring: {', '.join(parts)}")
    try:
        _dfx_call(backend_id, "set_canister_config", arg, network)
    except Exception as e:
        console.print(f"  [yellow]⚠ set_canister_config failed: {e}[/yellow]")

    # Upload branding and pin /custom/ on the frontend canister
    if frontend_id:
        manifest_path = realm.get("manifest", "")
        if manifest_path:
            manifest_dir = get_project_root() / Path(manifest_path).parent
            _upload_branding_to_canister(frontend_id, manifest_dir, network)

        _pin_frontend_directories(frontend_id, network)

        # Final step: re-assert /canister_ids.js so the SPA can always resolve its
        # backend id, even though the asset bundle commit doesn't carry it.
        fr_id = (infra or {}).get("file_registry_canister_id", "")
        # Pin the canonical II derivationOrigin so this realm shares one principal
        # with the registry + other realms (issue #233). The descriptor's `infra`
        # sets `ii_derivation_origin` (the environment's portal origin); empty
        # keeps legacy per-origin principals. No host table lives in the CLI.
        deriv = str((infra or {}).get("ii_derivation_origin") or "")
        portal_url = _resolve_portal_url(realm, network, deriv, (infra or {}).get("registry_canister_id", ""))
        _store_canister_ids(frontend_id, backend_id, network,
                            file_registry_id=fr_id, derivation_origin=deriv,
                            portal_url=portal_url,
                            infra=infra,
                            parameters=parameters)

        if resync_frontends:
            _resync_extension_frontends_after_deploy(realm, network, infra)


def _submit_and_poll(manifest: dict, network: str) -> bool:
    """Submit deployment request and poll for completion."""
    realm_name = manifest.get("name", "unknown")
    registry_id = manifest.get("registry_canister_id", "")
    if not registry_id:
        console.print("[red]  manifest names no registry_canister_id[/red]")
        return False

    manifest_json = json.dumps(manifest)
    candid_arg = '("' + manifest_json.replace("\\", "\\\\").replace('"', '\\"') + '")'

    console.print(f"  Submitting to registry ({registry_id})...")
    try:
        raw = _dfx_call(registry_id, "request_deployment", candid_arg, network)
        result = json.loads(json.loads(raw) if raw.startswith('"') else raw)
    except Exception as e:
        console.print(f"[red]  request_deployment failed: {e}[/red]")
        return False

    if not result.get("success"):
        console.print(f"[red]  Rejected: {result.get('error', 'unknown')}[/red]")
        return False

    job_id = result.get("job_id", "")
    console.print(f"  Job enqueued: [bold]{job_id}[/bold]")

    installer_id = manifest.get("installer_canister_id", "")
    if not installer_id:
        console.print("[yellow]  manifest names no installer_canister_id — cannot poll[/yellow]")
        return True

    console.print(f"  Polling installer ({installer_id})...\n")

    start = time.time()
    prev_status = ""
    prev_detail = ""
    same_status_count = 0

    while time.time() - start < POLL_TIMEOUT_S:
        time.sleep(POLL_INTERVAL_S)
        elapsed = int(time.time() - start)
        try:
            raw = _dfx_call(installer_id, "get_deployment_job_status", f'("{job_id}")', network, query=True)
            data = json.loads(raw)
            ok = data.get("Ok") or (data if "job_id" in data else None)
            if ok:
                status = ok.get("status", "unknown")
                detail = ""

                # Build contextual detail per phase
                if status == "pending":
                    detail = "waiting for installer to pick up job"
                elif status == "allocating":
                    be = ok.get("backend_canister_id", "")
                    fe = ok.get("frontend_canister_id", "")
                    if be or fe:
                        detail = f"backend={be or '...'} frontend={fe or '...'}"
                    else:
                        detail = "allocating canisters"
                elif status == "installing_wasm":
                    be = ok.get("backend_canister_id", "")
                    detail = f"uploading WASM to {be}" if be else "installing WASM"
                elif status == "verifying":
                    wasm_ok = ok.get("wasm_verified", 0)
                    assets_ok = ok.get("assets_verified", 0)
                    parts = []
                    if wasm_ok:
                        parts.append("wasm [green]✓[/green]")
                    else:
                        expected = ok.get("expected_wasm_hash", "")[:12]
                        actual = ok.get("actual_wasm_hash", "")[:12]
                        if actual:
                            parts.append(f"wasm hash {actual}{'==' if actual == expected[:12] else '≠'}{expected}")
                        else:
                            parts.append("verifying wasm hash")
                    if assets_ok:
                        parts.append("assets [green]✓[/green]")
                    else:
                        parts.append("verifying assets")
                    detail = " | ".join(parts)
                elif status == "deploying_frontend":
                    fe = ok.get("frontend_canister_id", "")
                    assets_ok = ok.get("assets_verified", 0)
                    detail = f"uploading assets to {fe}" if fe else "deploying frontend"
                    if assets_ok:
                        detail += " (verified)"
                elif status == "extensions":
                    task_id = ok.get("ext_deploy_task_id", "")
                    if task_id:
                        detail = f"installing extensions (task: {task_id[:16]}…)"
                    else:
                        detail = "installing extensions & codices"
                elif status == "registering":
                    detail = "registering realm in registry"
                elif status == "completed":
                    completed_at = ok.get("completed_at", 0)
                    be = ok.get("backend_canister_id", "")
                    fe = ok.get("frontend_canister_id", "")
                    error = ok.get("error", "")
                    duration = _format_elapsed(elapsed)
                    console.print(f"  [{duration}] [green bold]completed[/green bold]")
                    if be:
                        console.print(f"         backend:  {be}")
                    if fe:
                        console.print(f"         frontend: {fe}")
                    if error:
                        console.print(f"  [yellow]⚠ {error}[/yellow]")
                    console.print(f"  [green]Deployment succeeded ({duration} total)[/green]")
                    return True
                elif status in ("failed", "failed_verification", "cancelled"):
                    error = ok.get("error", "")
                    be = ok.get("backend_canister_id", "")
                    wasm_ok = ok.get("wasm_verified", 0)
                    assets_ok = ok.get("assets_verified", 0)
                    console.print(f"  [{_format_elapsed(elapsed)}] [red bold]{status}[/red bold]")
                    if error:
                        console.print(f"  [red]  Error: {error}[/red]")
                    if be:
                        console.print(f"         backend:  {be}")
                    if status == "failed_verification":
                        console.print(f"         wasm_verified={wasm_ok} assets_verified={assets_ok}")
                        expected_w = ok.get("expected_wasm_hash", "")
                        actual_w = ok.get("actual_wasm_hash", "")
                        if expected_w or actual_w:
                            console.print(f"         wasm expected: {expected_w}")
                            console.print(f"         wasm actual:   {actual_w}")
                    return False

                # Suppress repeated identical lines; show phase transitions prominently
                if status != prev_status:
                    same_status_count = 0
                    color = {"pending": "dim", "extensions": "cyan", "verifying": "yellow",
                             "registering": "magenta", "allocating": "blue",
                             "installing_wasm": "blue", "deploying_frontend": "blue"}.get(status, "white")
                    console.print(f"  [{_format_elapsed(elapsed)}] [{color}]{status}[/{color}] — {detail}")
                    prev_status = status
                    prev_detail = detail
                elif detail != prev_detail:
                    same_status_count = 0
                    console.print(_poll_status_line(elapsed, status, detail))
                    prev_detail = detail
                else:
                    same_status_count += 1
                    if same_status_count % 6 == 0:
                        console.print(f"  [{_format_elapsed(elapsed)}] still {status} ({_format_elapsed(elapsed)} elapsed)")
            else:
                err = data.get("Err", {})
                msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                console.print(f"  [{_format_elapsed(elapsed)}] [yellow]poll error: {msg}[/yellow]")
        except Exception as e:
            console.print(f"  [{_format_elapsed(elapsed)}] [yellow]poll error: {e}[/yellow]")

    console.print(f"[red]  Timeout after {_format_elapsed(POLL_TIMEOUT_S)} — job may still be running[/red]")
    console.print(f"[red]  Job ID: {job_id}[/red]")
    return False


def mundus_deploy_descriptor_command(
    descriptor: str,
    network: str,
    deploy_mode: str = "upgrade",
    artifact_version: str = "latest",
    realm_filter: str = "",
    canister_filter: str = "",
    skip_extensions: bool = False,
    extension_names: list[str] | None = None,
    codex_names: list[str] | None = None,
    build_variant: str = "",
) -> None:
    """Deploy realms from a mundus descriptor YAML file.

    Optional filters:
      realm_filter    — deploy only the realm matching this name/display_name
      canister_filter — "backend", "frontend", or "" (both)
      skip_extensions — if True, skip all extension/codex installation
      extension_names — if provided, only install these extensions (empty list = none)
      codex_names     — if provided, only install these codices (empty list = none)
    """
    descriptor_path = Path(descriptor)
    if not descriptor_path.is_absolute():
        descriptor_path = get_project_root() / descriptor_path

    if not descriptor_path.exists():
        console.print(f"[red]Descriptor not found: {descriptor_path}[/red]")
        raise typer.Exit(1)

    if canister_filter and canister_filter not in ("backend", "frontend"):
        console.print(f"[red]Invalid canister filter '{canister_filter}'. Use 'backend' or 'frontend'.[/red]")
        raise typer.Exit(1)

    with open(descriptor_path) as f:
        desc = yaml.safe_load(f)

    desc_network = desc.get("network", network)
    if network and network != desc_network:
        console.print(f"[yellow]Warning: overriding descriptor network '{desc_network}' with '{network}'[/yellow]")
    else:
        network = desc_network

    if build_variant:
        if build_variant not in _BUILD_VARIANTS:
            console.print(
                f"[red]Invalid build variant '{build_variant}'. "
                f"Use 'production' or 'test'.[/red]"
            )
            raise typer.Exit(1)
        variant = build_variant
    else:
        variant = _default_build_variant(network)

    parameters = desc.get("parameters") or {}
    infra = desc.get("infra") or {}

    realms = [e for e in desc.get("mundus", []) if e.get("type", "realm") == "realm"]

    if realm_filter:
        realms = [
            e for e in realms
            if realm_filter.lower() in (
                e.get("name", "").lower(),
                e.get("display_name", "").lower(),
            )
        ]
        if not realms:
            console.print(f"[red]No realm matching '{realm_filter}' in descriptor[/red]")
            raise typer.Exit(1)

    if not realms:
        console.print("[yellow]No realm entries in descriptor[/yellow]")
        return

    scope = f"{len(realms)} realm(s)"
    if canister_filter:
        scope += f" ({canister_filter} only)"
    if skip_extensions:
        scope += " [skip extensions]"
    elif extension_names is not None and len(extension_names) == 0:
        scope += " [no extensions]"
    elif extension_names is not None:
        scope += f" [extensions: {','.join(extension_names)}]"
    if codex_names is not None and len(codex_names) == 0:
        scope += " [no codices]"
    elif codex_names is not None:
        scope += f" [codices: {','.join(codex_names)}]"
    console.print(
        f"Deploying {scope} to {network} (mode={deploy_mode}, variant={variant})"
    )
    if parameters:
        console.print(f"Parameters: {parameters}")
    console.print()

    backend_url = ""
    backend_hash = ""
    frontend_url = ""
    frontend_hash = ""
    if canister_filter != "frontend":
        backend_url, backend_hash = _resolve_artifact(
            artifact_version, "realm_backend", network, variant=variant
        )
        console.print(f"Artifacts resolved:")
        console.print(f"  backend:  {backend_url} (sha256={backend_hash[:16]}...)")
    if canister_filter != "backend":
        frontend_url, frontend_hash = _resolve_artifact(
            artifact_version, "realm_frontend", network, variant=variant
        )
        if not backend_url:
            console.print(f"Artifacts resolved:")
        console.print(f"  frontend: {frontend_url} (sha256={frontend_hash[:16]}...)")
    console.print()

    expected_hashes = {}
    if backend_hash:
        expected_hashes["backend_wasm"] = backend_hash

    # Resolve deployed version for post-deploy config
    deployed_version = ""
    if artifact_version and artifact_version not in ("build",):
        deployed_version = artifact_version.lstrip("v")
    elif artifact_version == "build":
        version_file = get_project_root() / "version.txt"
        if version_file.exists():
            deployed_version = version_file.read_text().strip()

    results = []
    for realm in realms:
        name = realm.get("display_name", realm.get("name", "?"))
        console.print(f"--- {name} ---")
        manifest = _build_manifest(
            realm, network, deploy_mode, backend_url, frontend_url,
            canister_filter, infra=infra, expected_hashes=expected_hashes,
            skip_extensions=skip_extensions,
            extension_names=extension_names, codex_names=codex_names,
        )
        ok = _submit_and_poll(manifest, network)
        if ok:
            try:
                _post_deploy_config(
                    realm, network, deployed_version,
                    parameters=parameters, infra=infra,
                    resync_frontends=_should_resync_extension_frontends(canister_filter),
                )
            except Exception as e:
                console.print(f"[red]  post-deploy failed: {e}[/red]")
                ok = False
        results.append((name, ok))
        console.print()

    console.print("Summary:")
    all_ok = True
    for name, ok in results:
        symbol = "[green]OK[/green]" if ok else "[red]FAIL[/red]"
        console.print(f"  {name}: {symbol}")
        if not ok:
            all_ok = False

    if not all_ok:
        raise typer.Exit(1)
