#!/usr/bin/env python3
"""Deploy one standalone realm (a realm_backend and a realm_frontend) on the replica of a local
`scripts/up.sh -e local` product install, the way an installer does, without Casals.

The script plays the installer: it creates the two canisters, writes the frontend's
``canister_ids.js``, lets the backend publish into the frontend, then runs the installer's setup
steps: ``install`` (network ``local``, the founder), ``realm`` (the name), ``infrastructure`` (the
product's file registry and marketplace) and ``trust_policy``. The founder is a fresh Ed25519 key:
its 32-byte seed goes into the output file so the browser test can sign as the founder.

Then it installs the extensions, as an operator does with `realms extension install`: the core ones
and the ``--codex``'s dependencies, each at its newest published version (``extension_install``),
with their registry ``frontend/`` files served from the frontend under ``/ext/<id>/<version>/``.
``extension_install`` keeps only the manifest and backend; the bundles are the installer's job.

    python3 scripts/e2e_standalone_realm.py --out /tmp/e2e/realm.json

Inputs (all optional): ``--casals-home`` (default ``$CASALS_HOME`` or ``~/casals-home``), the
``--identity`` that pays (default ``local-dev``, the one `up.sh -e local` funds), ``--name``.
Needs the build `up.sh` leaves behind: ``.basilisk/realm_backend/realm_backend.wasm`` and
``src/realm_frontend/dist``.
"""

import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zlib
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "cli"))

from realms.cli.commands.extension import _dfx_call, latest_version  # noqa: E402
from realms.cli.realm_api import call_op  # noqa: E402

ORCHESTRA = "realms-product"
CORE_EXTENSIONS = ("public_dashboard", "member_dashboard", "realm_settings", "extensions_manager", "voting",
                   "census", "admin_dashboard", "vault", "codex_viewer")
ED25519_DER_PREFIX = bytes.fromhex("302a300506032b6570032100")
ICP_YAML = """\
# Written by scripts/e2e_standalone_realm.py: a standalone realm on the product's local replica.
networks:
  - name: local
    mode: connected
    url: {url}
    root-key: fetch

environments:
  - name: local
    network: local

canisters:
  - name: realm_backend
    recipe:
      type: "@dfinity/prebuilt@v2.0.0"
      configuration:
        path: artifacts/realm_backend.wasm
    init_args: '("")'
  - name: realm_frontend
    recipe:
      type: "@dfinity/asset-canister@v2.2.0"
      configuration:
        dir: frontend
"""


def principal_text(blob: bytes) -> str:
    raw = zlib.crc32(blob).to_bytes(4, "big") + blob
    text = base64.b32encode(raw).decode().lower().rstrip("=")
    return "-".join(text[i:i + 5] for i in range(0, len(text), 5))


def founder_key():
    key = Ed25519PrivateKey.generate()
    seed = key.private_bytes_raw()
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    principal = principal_text(hashlib.sha224(ED25519_DER_PREFIX + public).digest() + b"\x02")
    return seed.hex(), principal


def icp(project: Path, *args: str, timeout: int = 900) -> str:
    proc = subprocess.run(["icp", *args], cwd=project, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        sys.exit(f"icp {' '.join(args)} failed:\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
    return proc.stdout


def codex_dependencies(codex: str) -> list:
    manifest = ROOT / "codices" / "codices" / codex / "manifest.json"
    if not manifest.exists():
        sys.exit(f"{manifest} is missing: check out the codices submodule")
    return list(json.loads(manifest.read_text()).get("dependencies") or [])


def copy_frontend(registry: str, ext: str, version: str, dest: Path, url: str, identity: str) -> int:
    """Write the registry's ``ext/<ext>/<version>/frontend/**`` files under ``dest``; return the count."""
    namespace = f"ext/{ext}/{version}"
    query = json.dumps(json.dumps({"namespace": namespace}))
    files = json.loads(_dfx_call(registry, "list_files", f"({query})", url, identity, is_query=True))
    count = 0
    for entry in files:
        path = entry["path"]
        if not path.startswith("frontend/"):
            continue
        data, offset, eof = b"", 0, False
        while not eof:  # get_file runs out of instructions on large bundles; read in chunks
            arg = json.dumps(json.dumps({"namespace": namespace, "path": path, "offset": offset,
                                         "length": 512 * 1024}))
            reply = json.loads(_dfx_call(registry, "get_file_chunk", f"({arg})", url, identity, is_query=True))
            if "error" in reply:
                sys.exit(f"{namespace}/{path}: {reply['error']}")
            data += base64.b64decode(reply["content_b64"])
            offset, eof = offset + reply["length"], reply["eof"] or reply["length"] == 0
        target = dest / "ext" / ext / version / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--casals-home", default=os.environ.get("CASALS_HOME") or str(Path.home() / "casals-home"))
    parser.add_argument("--identity", default="local-dev")
    parser.add_argument("--name", default="E2E Realm")
    parser.add_argument("--project", default="/tmp/e2e/standalone-realm", help="icp project directory (recreated)")
    parser.add_argument("--out", default="/tmp/e2e/realm.json")
    parser.add_argument("--codex", default="syntropia", help="install this codex's dependencies ('' for none)")
    parser.add_argument("--no-extensions", action="store_true", help="leave the realm without extensions")
    opts = parser.parse_args()

    orch = Path(opts.casals_home) / ORCHESTRA
    bindings = json.loads((orch / f"{ORCHESTRA}.local.json").read_text())
    payload = json.loads((orch / f"{ORCHESTRA}.local.export.json").read_text())
    export = payload.get("bindings") or {
        c["name"]: c["canister_id"]
        for section in payload.get("sections", []) for stand in section.get("stands", [])
        for c in stand.get("canisters", []) if c.get("canister_id")}
    url = bindings.get("network_url") or "http://127.0.0.1:8000"
    port = url.rsplit(":", 1)[-1]
    registry, marketplace, ledger = (export.get(n, "") for n in
                                     ("fleet-file-registry", "marketplace-backend", "token-backend"))
    if not registry or not marketplace:
        sys.exit(f"the product export has no fleet-file-registry / marketplace-backend: {sorted(export)}")

    wasm = ROOT / ".basilisk" / "realm_backend" / "realm_backend.wasm"
    dist = ROOT / "src" / "realm_frontend" / "dist"
    for path in (wasm, dist / "index.html"):
        if not path.exists():
            sys.exit(f"{path} is missing: run scripts/up.sh -e local (or its build) first")

    project = Path(opts.project)
    shutil.rmtree(project, ignore_errors=True)
    (project / "artifacts").mkdir(parents=True)
    shutil.copy2(wasm, project / "artifacts" / "realm_backend.wasm")
    shutil.copytree(dist, project / "frontend")
    (project / "icp.yaml").write_text(ICP_YAML.format(url=url))

    ids = {}
    for name in ("realm_backend", "realm_frontend"):
        out = icp(project, "canister", "create", name, "-e", "local", "--identity", opts.identity, "-q")
        ids[name] = out.strip().splitlines()[-1].strip()
    print(f"created {ids}")

    canister_ids = {
        "realm_backend": ids["realm_backend"],
        "file_registry": registry,
        "internet_identity": f"http://id.ai.localhost:{port}",
        "derivation_origin": "",
        "portal_url": "",
        "test_mode_ii_bypass": False,
    }
    (project / "frontend" / "canister_ids.js").write_text(
        f"globalThis.__CANISTER_IDS = {json.dumps(canister_ids)};\n")

    print("icp deploy realm_backend realm_frontend")
    icp(project, "deploy", "-e", "local", "--identity", opts.identity, "--yes")

    # The backend writes installed extension frontends into the asset canister.
    icp(project, "canister", "call", ids["realm_frontend"], "grant_permission",
        f'(record {{ to_principal = principal "{ids["realm_backend"]}"; permission = variant {{ Commit }} }})',
        "-e", "local", "--identity", opts.identity)

    seed, founder = founder_key()

    def step(**values):
        reply = call_op(ids["realm_backend"], "setup_step", values, network=url, identity=opts.identity,
                        timeout=300)
        if not reply.get("ok"):
            sys.exit(f"setup_step {values.get('step')}: {reply.get('error')}")
        print(f"setup_step {values['step']}: {reply.get('done')}")

    step(step="install", network="local", founder=founder)
    step(step="realm", name=opts.name)
    step(step="infrastructure", frontend_canister_id=ids["realm_frontend"], file_registry_canister_id=registry,
         marketplace_canister_id=marketplace)
    # The fleet registry has no namespace-approval API; installs must not wait on marketplace approval.
    step(step="trust_policy", require_marketplace_approval=False)

    installed = {}
    if not opts.no_extensions:
        wanted = list(CORE_EXTENSIONS) + [e for e in (codex_dependencies(opts.codex) if opts.codex else [])
                                          if e not in CORE_EXTENSIONS]
        for ext in wanted:
            version = latest_version(registry, ext, url, opts.identity)
            reply = call_op(ids["realm_backend"], "extension_install", {"extension": ext, "version": version},
                            network=url, identity=opts.identity, timeout=300)
            if not reply.get("ok") or reply.get("proposal"):
                sys.exit(f"extension_install {ext} {version}: {reply}")
            files = copy_frontend(registry, ext, version, project / "frontend", url, opts.identity)
            installed[ext] = version
            print(f"extension {ext} {version}: installed, {files} frontend files")
        print("icp deploy realm_frontend (extension frontends)")
        icp(project, "deploy", "realm_frontend", "-e", "local", "--identity", opts.identity, "--yes")

    out = {
        "network_url": url,
        "realm_url": f"http://{ids['realm_frontend']}.localhost:{port}",
        "realm_backend": ids["realm_backend"],
        "realm_frontend": ids["realm_frontend"],
        "file_registry": registry,
        "marketplace": marketplace,
        "token_ledger": ledger,
        "founder": founder,
        "founder_seed": seed,
        "name": opts.name,
        "identity": opts.identity,
        "codex": opts.codex,
        "extensions": installed,
    }
    Path(opts.out).parent.mkdir(parents=True, exist_ok=True)
    Path(opts.out).write_text(json.dumps(out, indent=2) + "\n")
    print(f"standalone realm {out['realm_url']}  (founder {founder}) -> {opts.out}")


if __name__ == "__main__":
    main()
