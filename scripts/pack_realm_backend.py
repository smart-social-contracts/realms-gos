#!/usr/bin/env python3
"""Build the realm backend wasm on the Cedar template and check it exports ``read`` and ``run``.

    python3 scripts/pack_realm_backend.py [--version X.Y.Z] [--variant production|test] [--gzip]

basilisk packs the whole ``src/realm_backend`` tree, so test-only code left in that tree would ship to
production even if no setting could reach it. The two variants therefore live in ``scripts/build_variants/``,
outside the packed tree, and exactly one is copied over ``src/realm_backend/core/build_variant.py`` before packing.
Production is the default; a test build has to be asked for. The finished wasm is byte-scanned for the test-only
symbols, so the variant is checked against the artifact rather than assumed from the flags.
"""

import argparse
import gzip
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "realm_backend"
WASM = ROOT / ".basilisk" / "realm_backend" / "realm_backend.wasm"
ENDPOINTS = ('service : (text) -> {', '"read" : (text, text) -> (text) query;', '"run" : (text, text) -> (text);')
VARIANTS = ("production", "test")
DEFAULT_VARIANT = "production"
ACTIVE_VARIANT = SRC / "core" / "build_variant.py"
# Defined only in scripts/build_variants/test.py: a wasm that contains them is a test build.
TEST_ONLY_SYMBOLS = (b"REALMS_TEST_BUILD_AUTH_BYPASS", b"NON_PRODUCTION_NETWORKS")
sys.path.insert(0, str(ROOT / "scripts"))

from basilisk_cedar_template import apply_cedar_template_env  # noqa: E402
from embed_candid_metadata import embed_candid_service_metadata  # noqa: E402


def select_build_variant(variant):
    """Copy the chosen variant over the module the canister imports."""
    shutil.copyfile(ROOT / "scripts" / "build_variants" / f"{variant}.py", ACTIVE_VARIANT)


def verify_build_variant(wasm, variant):
    """Fail unless the wasm holds the test-only symbols exactly when it is the test variant.

    Both directions are checked: a scan that could only ever pass would prove nothing."""
    code = Path(wasm).read_bytes()
    found = [s.decode() for s in TEST_ONLY_SYMBOLS if s in code]
    expected = [s.decode() for s in TEST_ONLY_SYMBOLS] if variant == "test" else []
    if found != expected:
        sys.exit(f"a {variant} wasm must contain the test-only symbols {expected}, but contains {found}")


def pack(version=None, variant=DEFAULT_VARIANT):
    setup = SRC / "api" / "setup.py"
    original = setup.read_text()
    if version:
        setup.write_text(original.replace('VERSION = "VERSION_PLACEHOLDER"', f'VERSION = "{version}"'))
    select_build_variant(variant)
    try:
        env = apply_cedar_template_env()
        env["CANISTER_CANDID_PATH"] = str(SRC / "realm_backend.did")
        subprocess.run([sys.executable, "-m", "basilisk", "realm_backend", str(SRC / "main.py")],
                       cwd=ROOT, env=env, check=True)
    finally:
        setup.write_text(original)
        select_build_variant(DEFAULT_VARIANT)
    did = (SRC / "realm_backend.did").read_text()
    missing = [e for e in ENDPOINTS if e not in did]
    if missing:
        sys.exit(f"not exported: {missing}")
    verify_build_variant(WASM, variant)
    return WASM


def release_gzip(wasm):
    """What a release publishes: the wasm with its candid:service metadata, gzipped next to it."""
    embed_candid_service_metadata(wasm, SRC / "realm_backend.did")
    gz = wasm.with_suffix(".wasm.gz")
    with open(wasm, "rb") as plain, gzip.open(gz, "wb", compresslevel=9) as packed:
        shutil.copyfileobj(plain, packed)
    return gz


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version")
    parser.add_argument("--variant", choices=VARIANTS, default=DEFAULT_VARIANT)
    parser.add_argument("--gzip", action="store_true", help="embed the candid metadata and gzip, as a release does")
    args = parser.parse_args()
    wasm = pack(args.version, args.variant)
    print(release_gzip(wasm) if args.gzip else wasm)
