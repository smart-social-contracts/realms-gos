"""The test token ledger realms are tried with: ic-python-token, the token realms use. Its test mode lets anyone mint
into any account, so a script or a test can give a member money to pay with. Its release is pinned here, by its sha256,
for everything that runs one: scripts/e2e_standalone_realm.py, `realms mundus` and tests/deployed. To test another
ledger, change where its wasm is downloaded from, the path it is kept at (under the repository root) and its sha256.

This module imports nothing of the repository: tests/deployed reads it without putting cli/ on the path, which would make
the realm's packer bundle cli/realms for the realm's own ``realms`` package.
"""

import hashlib
import sys
import urllib.request
from pathlib import Path

LEDGER_URL = "https://github.com/smart-social-contracts/ic-python-token/releases/download/v0.6.0/token_backend.wasm"
LEDGER_WASM = ".wasm/ic-python-token-v0.6.0.wasm"
LEDGER_SHA256 = "4944db4ed1b5670e51912a1fff71a46f905a60da4faede10ac6cf47e33e10950"


def fetch_ledger_wasm(path: Path) -> None:
    """Download the ledger wasm unless the file is there, and refuse (and delete) one that is not LEDGER_SHA256."""
    if not path.exists():
        path.parent.mkdir(exist_ok=True)
        urllib.request.urlretrieve(LEDGER_URL, path)
    found = hashlib.sha256(path.read_bytes()).hexdigest()
    if found != LEDGER_SHA256:
        path.unlink()  # a truncated or replaced download must not stay for the next run
        sys.exit(f"{LEDGER_URL} is sha256 {found}, not {LEDGER_SHA256}: deleted {path}, run again")
