#!/usr/bin/env python3
"""Build the realm backend wasm: regenerate main.py, pack it on the Cedar template, check the exports.

    python3 scripts/pack_realm_backend.py [--version X.Y.Z]
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "realm_backend"
WASM = ROOT / ".basilisk" / "realm_backend" / "realm_backend.wasm"
sys.path.insert(0, str(ROOT / "scripts"))

from basilisk_cedar_template import apply_cedar_template_env  # noqa: E402
from gen_main import registry, render  # noqa: E402


def pack(version=None):
    (SRC / "main.py").write_text(render())
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
    exported = set(re.findall(r"^\s*\"?(\w+)\"? *: *\(text\)", (SRC / "realm_backend.did").read_text(), re.M))
    missing = set(registry()) - exported
    if missing:
        sys.exit(f"not exported: {sorted(missing)}")
    return WASM


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version")
    print(pack(parser.parse_args().version))
