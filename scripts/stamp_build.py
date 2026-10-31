#!/usr/bin/env python3
"""Stamp a backend's build provenance into its sources before packing the WASM.

A canister can't read git or its own package metadata at runtime, so
``status()`` and ``GET /version`` carry placeholders (``SHA_PLACEHOLDER``,
``IC_PYTHON_DB_VERSION_PLACEHOLDER``, ...) that this script replaces.

Run it with the Python that builds the canister: dependency versions are read
from that environment, so they are the ones packed into the WASM.

    python3 scripts/stamp_build.py realm_backend --version v0.8.0
    /tmp/marketplace-venv/bin/python scripts/stamp_build.py marketplace_backend --version v0.8.0
    python3 scripts/stamp_build.py marketplace_backend      # a snapshot: no version

A value that isn't known (no --version, a package not installed, no git
checkout) keeps its placeholder, and the canister leaves it out of what it
reports.
"""

import argparse
import re
import subprocess
import sys
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Dict, Optional

ROOT = Path(__file__).resolve().parent.parent

# The files of each backend that carry placeholders
FILES = ("api/status.py", "api/version_http.py")

# Placeholder → the package whose installed version replaces it
DEPENDENCIES = {
    "BASILISK_VERSION_PLACEHOLDER": "ic-basilisk",
    "IC_BASILISK_TOOLKIT_VERSION_PLACEHOLDER": "ic-basilisk-toolkit",
    "IC_PYTHON_DB_VERSION_PLACEHOLDER": "ic-python-db",
    "IC_PYTHON_LOGGING_VERSION_PLACEHOLDER": "ic-python-logging",
}

# A whole placeholder token, so VERSION_PLACEHOLDER never matches inside
# IC_PYTHON_DB_VERSION_PLACEHOLDER
TOKEN = re.compile(r"\b[A-Z][A-Z_]*_PLACEHOLDER\b")


def _git(repo: Path, *args: str) -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return out or None


def _package_version(name: str) -> Optional[str]:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def stamp_values(version: Optional[str]) -> Dict[str, str]:
    values = {
        "SHA_PLACEHOLDER": _git(ROOT, "rev-parse", "--short", "HEAD"),
        "COMMIT_HASH_PLACEHOLDER": _git(ROOT, "rev-parse", "HEAD"),
        "COMMIT_DATETIME_PLACEHOLDER": _git(ROOT, "log", "-1", "--format=%cI"),
        "BUILT_AT_ISO_PLACEHOLDER": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "VERSION_PLACEHOLDER": version,
    }
    # The extensions submodule, when it is checked out as its own repository
    extensions = ROOT / "extensions"
    if (extensions / ".git").exists():
        values["EXTENSIONS_COMMIT_HASH_PLACEHOLDER"] = _git(
            extensions, "rev-parse", "HEAD"
        )
        values["EXTENSIONS_COMMIT_DATETIME_PLACEHOLDER"] = _git(
            extensions, "log", "-1", "--format=%cI"
        )
    for placeholder, package in DEPENDENCIES.items():
        values[placeholder] = _package_version(package)
    return {k: v for k, v in values.items() if v}


def stamp(canister: str, version: Optional[str]) -> Dict[str, str]:
    """Replace the known placeholders in ``canister``'s sources, in place."""
    values = stamp_values(version)
    for rel in FILES:
        path = ROOT / "src" / canister / rel
        if not path.is_file():
            continue
        text = path.read_text()
        stamped = TOKEN.sub(lambda m: values.get(m.group(0), m.group(0)), text)
        if stamped != text:
            path.write_text(stamped)
        left = sorted(set(TOKEN.findall(stamped)))
        print(
            f"  stamped {path.relative_to(ROOT)}"
            + (f" (left unstamped: {', '.join(left)})" if left else "")
        )
    return values


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("canister", help="e.g. realm_backend, marketplace_backend")
    p.add_argument("--version", help="the release tag, e.g. v0.8.0")
    args = p.parse_args()
    if not (ROOT / "src" / args.canister).is_dir():
        print(f"no such canister: src/{args.canister}", file=sys.stderr)
        return 2
    values = stamp(args.canister, args.version)
    for placeholder in sorted(values):
        print(f"    {placeholder} = {values[placeholder]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
