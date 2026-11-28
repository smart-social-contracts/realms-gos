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


TIMEOUT = 60  # seconds one read may wait: a connection the network dropped (a laptop changing networks) never ends
TRIES = 3


def download(path: Path) -> None:
    """LEDGER_URL into ``path``, through ``path.part``: only a whole download that is LEDGER_SHA256 is moved there,
    so an interrupted one leaves nothing at ``path`` for the next run to trust."""
    part = path.with_name(path.name + ".part")
    for attempt in range(1, TRIES + 1):
        try:
            with urllib.request.urlopen(LEDGER_URL, timeout=TIMEOUT) as reply, part.open("wb") as out:
                while chunk := reply.read(1 << 16):
                    out.write(chunk)
        except OSError as error:  # URLError, a timeout and a reset connection are all OSError
            part.unlink(missing_ok=True)
            if attempt == TRIES:
                sys.exit(f"{LEDGER_URL}: {error} ({TRIES} tries)")
            print(f"{LEDGER_URL}: {error}; trying again", file=sys.stderr, flush=True)
            continue
        found = hashlib.sha256(part.read_bytes()).hexdigest()
        if found != LEDGER_SHA256:
            part.unlink()
            sys.exit(f"{LEDGER_URL} is sha256 {found}, not {LEDGER_SHA256}")
        part.replace(path)
        return


def fetch_ledger_wasm(path: Path) -> None:
    """Download the ledger wasm unless the file is there, and refuse (and delete) one that is not LEDGER_SHA256."""
    if not path.exists():
        path.parent.mkdir(exist_ok=True)
        download(path)
    found = hashlib.sha256(path.read_bytes()).hexdigest()
    if found != LEDGER_SHA256:
        path.unlink()  # a truncated or replaced file must not stay for the next run
        sys.exit(f"{path} is sha256 {found}, not {LEDGER_SHA256}: deleted it, run again")
