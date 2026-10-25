#!/usr/bin/env python3
"""Build the realm backend wasm on the Cedar template and check it exports ``read`` and ``run``.

    python3 scripts/pack_realm_backend.py [--version X.Y.Z]
"""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "realm_backend"
WASM = ROOT / ".basilisk" / "realm_backend" / "realm_backend.wasm"
ENDPOINTS = ('"read" : (text, text) -> (text) query;', '"run" : (text, text) -> (text);')
sys.path.insert(0, str(ROOT / "scripts"))

from basilisk_cedar_template import apply_cedar_template_env  # noqa: E402


def pack(version=None):
    setup = SRC / "api" / "setup.py"
    original = setup.read_text()
    if version:
        setup.write_text(original.replace('VERSION = "VERSION_PLACEHOLDER"', f'VERSION = "{version}"'))
    try:
        env = apply_cedar_template_env()
        env["CANISTER_CANDID_PATH"] = str(SRC / "realm_backend.did")
        subprocess.run([sys.executable, "-m", "basilisk", "realm_backend", str(SRC / "main.py")],
                       cwd=ROOT, env=env, check=True)
    finally:
        setup.write_text(original)
    did = (SRC / "realm_backend.did").read_text()
    missing = [e for e in ENDPOINTS if e not in did]
    if missing:
        sys.exit(f"not exported: {missing}")
    return WASM


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version")
    print(pack(parser.parse_args().version))
