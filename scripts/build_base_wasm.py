#!/usr/bin/env python3
"""
Build a true *base* `realm_backend` WASM with **zero bundled extensions**.

Background — Issue #168 (Layered Realm)
---------------------------------------
A "layered" realm installs its WASM, extensions, and codices from
`file_registry` at runtime. For that to work, the base WASM must not contain
any pre-baked extension code. Historically this was achieved by inline shell
heredocs in `.github/workflows/publish-base-wasm.yml`, which made it
impossible to reproduce exactly the same build locally.

This script centralizes that recipe so CI and any operator on a workstation
produce a byte-identical base WASM.

What the script does
--------------------
1. Snapshots the existing `src/realm_backend/extension_packages/` tree (if any)
   to a temp dir so a follow-up build with bundled extensions can restore it.
2. Replaces it with a minimal stub tree — just enough to make `main.py`'s
   `from extension_packages.* import ...` lines succeed at import time:

       extension_packages/
         __init__.py
         registry.py             # raises ValueError on get_func()
         extension_manifests.py  # EXTENSION_MANIFESTS = {}
         extension_imports.py    # comment only — no imports

   Same shape as `core.runtime_extensions.get_all_extension_manifests`'s
   "no baked-in" path expects.
3. Packs via the leftover-free Cedar path (`scripts/pack_realm_backend.py`
   equivalent: ``python -m basilisk`` with ``BASILISK_TEMPLATE_WASM`` forced
   to ``cpython_canister_template_cedar.wasm``).
4. Optionally gzips the resulting WASM.
5. Optionally restores the original `extension_packages/` tree on success.

The script never deletes anything outside `src/realm_backend/extension_packages/`
and always restores the original tree on failure (try/finally).

Usage
-----
    # From the realms/ repo root:
    python3 scripts/build_base_wasm.py --gzip
    # → writes .basilisk/realm_backend/realm_backend.wasm[.gz]

    # Keep the stubs in place after build (e.g. for a local follow-up deploy):
    python3 scripts/build_base_wasm.py --keep-stubs

    # Build but don't actually run basilisk (for CI debugging):
    python3 scripts/build_base_wasm.py --dry-run
"""

from __future__ import annotations

import argparse
import gzip
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from basilisk_cedar_template import apply_cedar_template_env, require_cedar_build  # noqa: E402


# ── Stub source — must stay in sync with what ─────────────────────────────────
# .github/workflows/publish-base-wasm.yml previously injected inline.
# When we change one, we change the other in the same PR.

INIT_PY = '"""Extension packages directory (empty for base WASM — Issue #168)."""\n'

REGISTRY_PY = '''"""Extension registry stub for base WASM (Issue #168 — Layered Realm).

A base WASM intentionally ships no bundled extension code. Any caller that
tries to look up a function on a non-installed extension hits this stub,
which raises a ValueError with a clear message pointing the operator to the
runtime install path (`install_extension`/`install_extension_from_registry`).
"""


def get_func(extension_name, function_name):
    raise ValueError(
        f"Extension {extension_name!r} not available in base WASM. "
        f"Install extensions at runtime via install_extension or "
        f"install_extension_from_registry."
    )
'''

EXTENSION_MANIFESTS_PY = '''"""Extension manifests stub for base WASM (Issue #168 — Layered Realm).

The `core.runtime_extensions.get_all_extension_manifests` function tries to
import this module to merge any baked-in manifests on top of runtime ones.
A base WASM has none, so the stub returns an empty dict. Runtime-installed
extension manifests come from `EXTENSIONS_DIR/<id>/manifest.json` and are
loaded by `core.runtime_extensions._load_manifest`.
"""

EXTENSION_MANIFESTS = {}


def get_all_extension_manifests():
    return EXTENSION_MANIFESTS
'''

EXTENSION_IMPORTS_PY = (
    "# No baked-in extensions in base WASM (Issue #168 — Layered Realm).\n"
)


def _detect_repo_root(start: Path) -> Path:
    """Walk upward looking for the realms repo (must contain `src/realm_backend`)."""
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / "src" / "realm_backend" / "main.py").is_file():
            return candidate
    raise SystemExit(
        "Could not locate realm_backend repo root from "
        f"{start}. Run this script from inside the realms repo."
    )


def _snapshot_dir(src: Path, dest_root: Path) -> Optional[Path]:
    """Snapshot `src` (if it exists) under a temp `dest_root` and return that path."""
    if not src.exists():
        return None
    snap = dest_root / src.name
    shutil.copytree(src, snap, symlinks=True)
    return snap


def _restore_dir(snap: Optional[Path], target: Path) -> None:
    if snap is None:
        if target.exists():
            shutil.rmtree(target)
        return
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(snap, target, symlinks=True)


def _write_stubs(target: Path) -> None:
    """Replace `target` with the minimal extension_packages stub tree."""
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    (target / "__init__.py").write_text(INIT_PY, encoding="utf-8")
    (target / "registry.py").write_text(REGISTRY_PY, encoding="utf-8")
    (target / "extension_manifests.py").write_text(
        EXTENSION_MANIFESTS_PY, encoding="utf-8"
    )
    (target / "extension_imports.py").write_text(EXTENSION_IMPORTS_PY, encoding="utf-8")


def _run_basilisk(repo_root: Path, *, dry_run: bool) -> Path:
    """Invoke basilisk to build realm_backend.wasm. Returns the wasm path."""
    main_py = repo_root / "src" / "realm_backend" / "main.py"
    if not main_py.is_file():
        raise SystemExit(f"realm_backend main.py not found at {main_py}")

    cmd = [sys.executable, "-m", "basilisk", "realm_backend", str(main_py)]
    print(f"   🐍 {' '.join(cmd)}")
    env = apply_cedar_template_env()
    print(f"   🌲 template: {env['BASILISK_TEMPLATE_WASM']}")
    if dry_run:
        print("   (dry-run: skipping basilisk invocation)")
        # Fake the path so callers can keep going.
        return repo_root / ".basilisk" / "realm_backend" / "realm_backend.wasm"

    rc = subprocess.run(cmd, cwd=str(repo_root), env=env).returncode
    if rc != 0:
        raise SystemExit(f"basilisk build failed (rc={rc})")

    wasm_path = repo_root / ".basilisk" / "realm_backend" / "realm_backend.wasm"
    if not wasm_path.is_file():
        raise SystemExit(f"basilisk did not produce {wasm_path}")
    require_cedar_build(wasm_path)

    did_path = Path(env.get("CANISTER_CANDID_PATH") or wasm_path.with_suffix(".did"))
    if not did_path.is_file():
        did_path = wasm_path.with_suffix(".did")
    if not did_path.is_file():
        raise SystemExit(f"basilisk did not produce candid file at {did_path}")

    # Candid UI reads candid:service WASM metadata (not __get_candid_interface_tmp_hack).
    sys.path.insert(0, str(repo_root / "scripts"))
    from embed_candid_metadata import embed_candid_service_metadata

    embed_candid_service_metadata(wasm_path, did_path)
    return wasm_path


def _gzip(wasm_path: Path) -> Path:
    """Gzip `wasm_path` to `<wasm_path>.gz` and return the new path.

    The base WASM tends to live in a few-hundred-KB to a few-MB range,
    well within the IC inter-canister 2 MiB hop, so this is mostly a
    bandwidth optimization for the publish-to-file_registry step."""
    gz_path = wasm_path.with_suffix(wasm_path.suffix + ".gz")
    with wasm_path.open("rb") as fin, gzip.open(gz_path, "wb", compresslevel=9) as fout:
        shutil.copyfileobj(fin, fout)
    return gz_path


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Override repo root detection (default: walk up from this script)",
    )
    p.add_argument(
        "--gzip",
        action="store_true",
        help="Also produce realm_backend.wasm.gz next to the WASM",
    )
    p.add_argument(
        "--keep-stubs",
        action="store_true",
        help="Leave the stub extension_packages/ in place after build",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Inject stubs and report what would happen, but skip basilisk",
    )
    args = p.parse_args(argv)

    repo_root = (
        args.repo_root.resolve()
        if args.repo_root
        else _detect_repo_root(Path(__file__).parent)
    )
    ext_pkgs_dir = repo_root / "src" / "realm_backend" / "extension_packages"

    print(f"📦 Building base realm_backend WASM (Issue #168)")
    print(f"   repo root:        {repo_root}")
    print(f"   extension_packages: {ext_pkgs_dir}  (will be stubbed)")
    print(f"   keep stubs?       {args.keep_stubs}")
    print(f"   gzip?             {args.gzip}")

    snapshot_root = Path(tempfile.mkdtemp(prefix="realm-base-wasm-"))
    snapshot_path: Optional[Path] = None
    success = False
    try:
        snapshot_path = _snapshot_dir(ext_pkgs_dir, snapshot_root)
        if snapshot_path is None:
            print("   (no existing extension_packages/ to snapshot — fresh stub it is)")
        else:
            print(f"   snapshot:         {snapshot_path}")

        _write_stubs(ext_pkgs_dir)
        wasm_path = _run_basilisk(repo_root, dry_run=args.dry_run)

        if args.gzip and not args.dry_run:
            gz_path = _gzip(wasm_path)
            print(f"   ✅ wasm:    {wasm_path}  ({wasm_path.stat().st_size} bytes)")
            print(f"   ✅ wasm.gz: {gz_path}    ({gz_path.stat().st_size} bytes)")
        elif not args.dry_run:
            print(f"   ✅ wasm:    {wasm_path}  ({wasm_path.stat().st_size} bytes)")

        success = True
        return 0
    finally:
        if args.keep_stubs:
            print("   (--keep-stubs set; stub extension_packages/ left in place)")
        else:
            print("   ↩️  restoring original extension_packages/ tree...")
            _restore_dir(snapshot_path, ext_pkgs_dir)
        try:
            shutil.rmtree(snapshot_root)
        except OSError:
            pass
        if not success:
            print("   ⚠️  build did not complete successfully")


if __name__ == "__main__":
    sys.exit(main())
