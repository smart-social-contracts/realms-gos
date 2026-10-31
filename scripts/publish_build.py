#!/usr/bin/env python3
"""Build and publish a realm/infra artifact into an environment's file_registry.

The installer serves realms from the file_registry (``realm-backend@<version>``,
``realm-assets@<version>``), so a release publishes there. Which registry is the
caller's business: ``--file-registry <id>`` or the operator inventory
(``canister_ids.json``, written by ``casals export``) keyed by ``--environment``.
No per-network table lives here.

Two version modes:

  **Release** — explicit semver (``0.4.0``), for tagged releases::

      python3 scripts/publish_build.py --environment production --family realm \\
          --component both --version 0.4.0 --identity deployer

  **Main snapshot** — current git checkout, no release bump::

      python3 scripts/publish_build.py --environment production --family realm \\
          --component both --from-main --identity deployer

Main snapshots are published as ``main.<unix_ts>.<git_sha>``.
"""

import argparse
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

# Patched certified-assets WASM used by all Realms frontend canisters.
# Only needs to be re-downloaded when the certified-assets version itself changes.
_CERTIFIED_ASSETS_WASM_URL = (
    "https://github.com/smart-social-contracts/certified-assets"
    "/releases/download/v0.3.0/assetstorage.wasm.gz"
)
_CERTIFIED_ASSETS_WASM_CACHE = Path("/tmp/realms-assetstorage.wasm.gz")

from realms.cli.casals_versions import git_short_sha, main_build_version  # noqa: E402
from realms.cli.commands.files import file_registry_id_for  # noqa: E402

# Keep test-variant labels out of the production ``main.*`` channel (``-v main``).
TEST_MAIN_CHANNEL_PREFIX = "main-test"
TEST_VERSION_SUFFIX = "+test"
BUILD_VARIANTS = ("production", "test")
DEFAULT_BUILD_VARIANT = "production"
# Environments that only ever run the production variant.
PRODUCTION_LIKE_ENVIRONMENTS = ("staging", "production")

# GaaS stack (realm_registry_backend, realm_installer, realm_registry_frontend)
# is published from github.com/smart-social-contracts/gos-as-a-service releases.
# family -> {"backend": (canister_name, main_py) | None, "frontend": dir | None}
FAMILIES = {
    "realm": {
        "backend": ("realm_backend", "src/realm_backend/main.py"),
        "frontend": "src/realm_frontend",
    },
    # file_registry backend and management frontend moved to gos-as-a-service.
    "file-registry": {
        "backend": None,
        "frontend": None,
    },
    "dashboard": {
        "backend": None,
        "frontend": "src/platform_dashboard_frontend",
    },
    "marketplace": {
        "backend": ("marketplace_backend", "src/marketplace_backend/main.py"),
        "frontend": "src/marketplace_frontend",
    },
}

_BASILISK_REQUIREMENTS = [
    "ic-basilisk==0.15.1",
    "ic-basilisk-toolkit==0.5.3",
    "ic-python-db==0.12.0",
    "ic-python-logging==0.3.4",
]


def _root() -> Path:
    p = Path.cwd()
    while p != p.parent:
        if (p / "dfx.json").exists():
            return p
        p = p.parent
    return Path.cwd()


def _run(cmd, cwd=None, env=None):
    print(f"\n$ {' '.join(str(c) for c in cmd)}", flush=True)
    r = subprocess.run(cmd, cwd=cwd, env=env)
    if r.returncode != 0:
        sys.exit(f"command failed ({r.returncode}): {' '.join(str(c) for c in cmd)}")


def _basilisk_python(root: Path, canister: str) -> str:
    """Isolated venv Python — basilisk rejects native packages in the host env.

    A canister that pins its own dependencies (``src/<canister>/requirements.txt``,
    e.g. the marketplace on a newer ic-python-db) builds in a venv of its own.
    """
    pinned = root / "src" / canister / "requirements.txt"
    if pinned.is_file():
        venv = root / f".venv-basilisk-{canister}"
        requirements = ["-r", str(pinned)]
    else:
        venv = root / ".venv-basilisk"
        requirements = _BASILISK_REQUIREMENTS
    py = venv / "bin" / "python"
    if not py.is_file():
        print(f"  creating basilisk venv at {venv}")
        _run([sys.executable, "-m", "venv", str(venv)])
        _run([str(venv / "bin" / "pip"), "install", "-q", "--upgrade", "pip"])
        _run([str(venv / "bin" / "pip"), "install", "-q", *requirements])
    return str(py)


def _stamp_build(root: Path, canister: str, release_tag: str | None) -> None:
    """Bake build provenance (status() and GET /version) into the backend's
    sources before its WASM build.

    Run by the venv that builds the canister, so the dependency versions are
    the packed ones. ``version`` is the release tag only — omitted honestly for
    ``--from-main`` snapshots.
    """
    cmd = [_basilisk_python(root, canister), "scripts/stamp_build.py", canister]
    if release_tag:
        cmd += ["--version", release_tag]
    _run(cmd, cwd=root)


def _build_realm_backend(
    root: Path, variant: str = DEFAULT_BUILD_VARIANT, release_tag: str | None = None
) -> Path:
    """The realm_backend wasm of ``variant``, gzipped, with the release tag in its status()."""
    cmd = [
        _basilisk_python(root, "realm_backend"),
        "scripts/pack_realm_backend.py",
        "--variant",
        variant,
        "--gzip",
    ]
    if release_tag:
        cmd += ["--version", release_tag]
    _run(cmd, cwd=root)
    gz = root / ".basilisk" / "realm_backend" / "realm_backend.wasm.gz"
    print(f"  backend wasm: {gz} ({gz.stat().st_size:,} bytes)")
    return gz


def _build_backend(
    root: Path,
    canister: str,
    main_py: str,
    variant: str = DEFAULT_BUILD_VARIANT,
    release_tag: str | None = None,
) -> Path:
    if canister == "realm_backend":
        return _build_realm_backend(root, variant=variant, release_tag=release_tag)
    bpy = _basilisk_python(root, canister)
    did = root / "src" / canister / f"{canister}.did"
    env = {**os.environ, "CANISTER_CANDID_PATH": str(did)}
    _run([bpy, "-m", "basilisk", canister, main_py], cwd=root, env=env)
    wasm = root / ".basilisk" / canister / f"{canister}.wasm"
    if not wasm.exists():
        sys.exit(f"basilisk did not produce {wasm}")
    if not did.is_file():
        did = wasm.with_suffix(".did")
    sys.path.insert(0, str(root / "scripts"))
    from embed_candid_metadata import embed_candid_service_metadata

    embed_candid_service_metadata(wasm, did)
    gz = root / f"{canister}.wasm.gz"
    with open(wasm, "rb") as fi:
        import gzip as _gz

        with _gz.open(gz, "wb") as fo:
            fo.write(fi.read())
    print(f"  backend wasm: {gz} ({gz.stat().st_size:,} bytes)")
    return gz


def _sync_declarations(root: Path, fe_dir: str) -> None:
    """Copy committed candid declarations into the frontend's ``$lib/declarations``.

    The realm frontend imports ``$lib/declarations/realm_backend`` at build time,
    but ``declarations`` is gitignored everywhere except the committed
    ``src/declarations/`` snapshot. Mirror the proven deploy flow (release.yml /
    deploy_canisters.sh) by copying that snapshot into place before ``vite build``;
    a no-op for frontends that don't import declarations.
    """
    src = root / "src" / "declarations"
    if not src.is_dir():
        return
    dst = root / fe_dir / "src" / "lib" / "declarations"
    dst.mkdir(parents=True, exist_ok=True)
    for entry in src.iterdir():
        if not entry.is_dir():
            continue
        shutil.copytree(entry, dst / entry.name, dirs_exist_ok=True)
    print(f"  synced declarations -> {dst}")


def _build_frontend(
    root: Path, fe_dir: str, environment: str, variant: str = "production"
) -> Path:
    d = root / fe_dir
    # The realm frontend gates its Internet Identity bypass on a compile-time
    # constant, so the variant has to be chosen here rather than at runtime.
    env = {**os.environ, "DFX_NETWORK": environment, "REALMS_BUILD_VARIANT": variant}
    _sync_declarations(root, fe_dir)
    # Workspace frontends (registry, dashboard, …) hoist shared deps from the repo
    # root; installing only in the subdirectory leaves @sveltejs/kit at the root
    # without svelte and breaks vite build.
    _run(["npm", "install", "--legacy-peer-deps"], cwd=root, env=env)
    _run(["npm", "run", "build"], cwd=d, env=env)
    dist = d / "dist"
    if not dist.is_dir():
        sys.exit(f"frontend build did not produce {dist}")
    if fe_dir == "src/realm_frontend":
        sys.path.insert(0, str(root / "scripts"))
        from check_frontend_variant import verify_frontend_variant

        verify_frontend_variant(dist, variant)
    print(f"  frontend dist: {dist}")
    return dist


def _find_assets_wasm(root: Path, environment: str) -> str:
    candidates = (
        list((root / ".dfx" / environment / "canisters").glob("*/assetstorage.wasm.gz"))
        if (root / ".dfx" / environment / "canisters").is_dir()
        else []
    )
    if candidates:
        return str(candidates[0])
    try:
        cache = subprocess.check_output(["dfx", "cache", "show"], text=True).strip()
        for c in Path(cache).rglob("assetstorage.wasm.gz"):
            return str(c)
    except Exception:
        pass
    # Fall back to the known patched certified-assets release used by all Realms
    # frontend canisters. Cache to /tmp so repeated publishes skip the download.
    if not _CERTIFIED_ASSETS_WASM_CACHE.exists():
        print(f"  downloading assetstorage.wasm.gz from {_CERTIFIED_ASSETS_WASM_URL}")
        urllib.request.urlretrieve(
            _CERTIFIED_ASSETS_WASM_URL, _CERTIFIED_ASSETS_WASM_CACHE
        )
        print(f"  saved to {_CERTIFIED_ASSETS_WASM_CACHE}")
    else:
        print(f"  using cached assetstorage.wasm.gz at {_CERTIFIED_ASSETS_WASM_CACHE}")
    return str(_CERTIFIED_ASSETS_WASM_CACHE)


def _resolve_version(from_main: bool, explicit: str | None, root: Path) -> str:
    if from_main:
        v = main_build_version(root)
        print(f"  main snapshot: {v} (commit {git_short_sha(root)})")
        return v
    if not explicit:
        sys.exit("provide --version or use --from-main")
    return explicit.strip()


def apply_build_variant_label(version: str, variant: str) -> str:
    """Return a publish label that cannot be rolled out as production."""
    if variant == DEFAULT_BUILD_VARIANT:
        return version
    if variant != "test":
        raise ValueError(f"unknown build variant {variant!r}")
    if version.startswith("main."):
        # Separate channel prefix — ``main.<ts>.<sha>+test`` would still match
        # ``-v main`` because ``is_main_channel`` treats any ``main.*`` as main.
        return f"{TEST_MAIN_CHANNEL_PREFIX}.{version[len('main.'):]}"
    return f"{version}{TEST_VERSION_SUFFIX}"


def parse_build_variant_label(labeled: str) -> tuple[str, str]:
    """Inverse of :func:`apply_build_variant_label`."""
    if labeled.startswith(f"{TEST_MAIN_CHANNEL_PREFIX}."):
        return f"main.{labeled[len(TEST_MAIN_CHANNEL_PREFIX) + 1:]}", "test"
    if labeled.endswith(TEST_VERSION_SUFFIX):
        return labeled[: -len(TEST_VERSION_SUFFIX)], "test"
    return labeled, DEFAULT_BUILD_VARIANT


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--environment",
        required=True,
        help="environment / dfx network name (e.g. production, local); also the "
        "canister_ids.json key used when --file-registry is not given",
    )
    ap.add_argument(
        "--file-registry",
        default=None,
        help="file_registry canister id (default: canister_ids.json[file_registry][<environment>])",
    )
    ap.add_argument("--family", required=True, help=f"one of: {', '.join(FAMILIES)}")
    ap.add_argument(
        "--component", default="both", choices=["backend", "frontend", "both"]
    )
    ap.add_argument(
        "--version", default=None, help="semver release label (omit with --from-main)"
    )
    ap.add_argument(
        "--from-main",
        action="store_true",
        help="publish current checkout as main.<ts>.<sha> (no release bump)",
    )
    ap.add_argument("--identity", default=None)
    ap.add_argument(
        "--assets-wasm", default=None, help="override certified-assets WASM path"
    )
    ap.add_argument(
        "--variant",
        choices=BUILD_VARIANTS,
        default=DEFAULT_BUILD_VARIANT,
        help="realm backend build variant (default: production)",
    )
    args = ap.parse_args()

    if args.family not in FAMILIES:
        sys.exit(
            f"unsupported family '{args.family}'. Buildable: {', '.join(FAMILIES)}."
        )

    root = _root()
    version = _resolve_version(args.from_main, args.version, root)
    build_variant = args.variant

    env = args.environment
    if build_variant == "test" and env in PRODUCTION_LIKE_ENVIRONMENTS:
        sys.exit(f"the test build variant is never published to {env}")
    file_registry = (args.file_registry or "").strip() or file_registry_id_for(env)
    if not file_registry:
        sys.exit(
            f"no file_registry for '{env}': pass --file-registry <id> or add it to "
            "canister_ids.json (casals export)"
        )

    spec = FAMILIES[args.family]
    want_backend = args.component in ("backend", "both") and spec["backend"]
    want_frontend = args.component in ("frontend", "both") and spec["frontend"]

    if args.component == "backend" and not spec["backend"]:
        sys.exit(f"family '{args.family}' has no backend component")
    if args.component == "frontend" and not spec["frontend"]:
        sys.exit(f"family '{args.family}' has no frontend component")

    backend_wasm = None
    frontend_dist = None
    assets_wasm = args.assets_wasm

    # The variant applies to the realm as a whole: the backend WASM drops the
    # test code paths and the frontend drops the login UI that would call them.
    # Publishing one half as "test" and the other as "production" would ship a
    # realm whose two sides disagree about whether test mode exists.
    realm_variant = (
        build_variant if args.family == "realm" else DEFAULT_BUILD_VARIANT
    )
    if realm_variant == "test":
        version = apply_build_variant_label(version, "test")
        print(f"  test-variant publish label: {version}")

    if want_backend:
        canister, main_py = spec["backend"]
        base_version, _ = parse_build_variant_label(version)
        release_tag = None if args.from_main else f"v{base_version}"
        if canister != "realm_backend":  # the realm's packer stamps its own version
            _stamp_build(root, canister, release_tag)
        backend_wasm = _build_backend(
            root, canister, main_py, variant=realm_variant, release_tag=release_tag
        )
    if want_frontend:
        frontend_dist = _build_frontend(
            root, spec["frontend"], env, variant=realm_variant
        )
        if not assets_wasm:
            assets_wasm = _find_assets_wasm(root, env)

    cmd = [
        "realms",
        "files",
        "publish-release",
        "--network",
        env,
        "--family",
        args.family,
        "--version",
        version,
        "--registry",
        file_registry,
    ]
    if backend_wasm:
        cmd += ["--backend-wasm", str(backend_wasm)]
    if frontend_dist:
        cmd += ["--frontend-dist", str(frontend_dist)]
        if assets_wasm:
            cmd += ["--assets-wasm", str(assets_wasm)]
    if args.identity:
        cmd += ["--identity", args.identity]

    _run(cmd, cwd=root)
    print(f"\npublish_build complete (version={version}, file_registry={file_registry}).")


if __name__ == "__main__":
    main()
