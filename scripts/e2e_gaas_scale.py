#!/usr/bin/env python3
"""Prepare the scaling e2e on a local GaaS stack (`scripts/local_up.sh --gaas`) and write its GAAS_JSON.

Through the GaaS multisig (the deployer is its signer on a local stack) it turns the portal's test mode on
(Internet Identity bypass: deterministic test identities), and issues a voucher (50 credits, ten realms: the creator identity is the same every run), which the
spec redeems as the founder before launching the realm from the portal.

    python3 scripts/e2e_gaas_scale.py --out /tmp/e2e/gaas.json
    GAAS_JSON=/tmp/e2e/gaas.json npx playwright test -c playwright.gaas.config.ts   (src/realm_frontend)

Inputs: ``--casals-home`` (default ``$CASALS_HOME`` or ``~/casals-home``), ``--identity`` (default local-dev).
"""
import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
from pathlib import Path

# skip_terms is off, explicitly (the registry keeps the flags of an earlier run): members accept the Terms to join.
FLAGS = {"test_mode": True, "ii_bypass": True, "user_self_registration": True, "skip_terms": False,
         "disable_card_billing": True}


def bindings(home: Path, orchestra: str) -> dict:
    export = home / orchestra / f"{orchestra}.local.export.json"
    if export.exists():
        found = json.loads(export.read_text()).get("bindings") or {}
        if found:
            return found
    local = json.loads((home / orchestra / f"{orchestra}.local.json").read_text())
    return {k: v for k, v in local.items() if isinstance(v, str)} | (local.get("canisters") or {})


def call(project: Path, identity: str, canister: str, method: str, arg: str, query=False) -> str:
    cmd = ["icp", "canister", "call", canister, method, arg, "-e", "local", "--identity", identity]
    out = subprocess.run(cmd + (["--query"] if query else []), cwd=project, capture_output=True, text=True,
                         timeout=300)
    if out.returncode:
        raise SystemExit(f"{method} on {canister}: {(out.stderr or out.stdout).strip()[-400:]}")
    return out.stdout


def through_multisig(project: Path, identity: str, multisig: str, canister: str, method: str, payload: dict):
    arg_json = json.dumps(json.dumps(payload))[1:-1]
    call(project, identity, multisig, "propose",
         f'(variant {{ CallCanister = record {{ canister = principal "{canister}"; method = "{method}"; '
         f'arg_json = "{arg_json}" }} }}, null)')


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--casals-home", default=os.environ.get("CASALS_HOME") or str(Path.home() / "casals-home"))
    p.add_argument("--identity", default="local-dev")
    p.add_argument("--out", default="/tmp/e2e/gaas.json")
    args = p.parse_args()
    home = Path(args.casals_home)
    project = home / "gaas" / "projects" / "gaas.local"
    gaas, product = bindings(home, "gaas"), bindings(home, "realms-product")
    network_url = re.search(r"url: (\S+)", (project / "icp.yaml").read_text()).group(1)
    port = network_url.rsplit(":", 1)[-1].rstrip("/")
    registry, multisig = gaas["realm-registry-backend"], gaas["multisig"]

    through_multisig(project, args.identity, multisig, registry, "set_canister_config_json",
                     {"network": "local", "test_flags": FLAGS})
    flags = call(project, args.identity, registry, "get_runtime_flags", "()", query=True)
    if '\\"test_mode_ii_bypass\\":true' not in flags:
        raise SystemExit(f"the portal's test mode did not turn on: {flags}")

    code = secrets.token_hex(6).upper()
    checksum = "sha256:" + hashlib.sha256(code.encode()).hexdigest()
    through_multisig(project, args.identity, multisig, registry, "issue_voucher", {"checksum": checksum, "credits": 50})

    out = {
        "network_url": network_url,
        "portal_url": f"http://{gaas['realm-registry-frontend']}.localhost:{port}",
        "registry": registry,
        "installer": gaas["realm-installer"],
        "conductor": gaas.get("casals-backend", ""),
        "token_ledger": product.get("token-backend", ""),
        "voucher_code": code,
        "realm_name": os.environ.get("SCALE_REALM", f"scale-{secrets.token_hex(2)}"),
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
