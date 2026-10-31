#!/usr/bin/env python3
"""Write the GaaS sheet for the e2e: local builds in place of the releases `casals.json` pins.

gos-as-a-service's `tests/e2e/local_sheet.py` swaps the realm backend and frontend, the installer and the Casals
conductor; this adds the registry backend and the portal frontend. The pinned releases predate each other's APIs
(the installer calls a realm's `enter_setup`, which the slim realm no longer has), so the e2e runs them all at main.

    GAAS_DIR=../gos-as-a-service REALM_BACKEND_WASM=... REALM_FRONTEND=... INSTALLER_WASM=... \\
      CASALS_BACKEND_WASM=... REGISTRY_WASM=... REGISTRY_FRONTEND=... python3 scripts/e2e_gaas_sheet.py out.json

Paths outside this checkout need their directory in CASALS_LOCAL_ROOTS when `casals up` reads them.
"""
import json
import os
import sys
from pathlib import Path

gaas = Path(os.environ["GAAS_DIR"])
sys.path.insert(0, str(gaas / "tests" / "e2e"))
import local_sheet  # noqa: E402

sheet = local_sheet.build_sheet(json.loads((gaas / "casals.json").read_text()), dict(os.environ))

registry = local_sheet.wasm_row(sheet, "realm-registry-backend")
registry["source"] = local_sheet.local_source(os.environ["REGISTRY_WASM"])
registry.pop("sha256", None)

portal = next(b for b in sheet["registry"]["bundles"] if b["path"].startswith("frontend/realm-registry-frontend/"))
portal["source"] = local_sheet.local_source(os.environ["REGISTRY_FRONTEND"])
portal.pop("sha256", None)

Path(sys.argv[1]).write_text(json.dumps(sheet, indent=2) + "\n")
