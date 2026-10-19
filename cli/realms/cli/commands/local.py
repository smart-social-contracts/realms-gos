"""``realms local``: a realm on this machine — icp-cli + PocketIC, no Casals, no dfx.

``start`` deploys the canisters of a Realms GitHub release (realm_backend,
realm_frontend) plus a file_registry onto icp-cli's managed local network,
``publish`` uploads extension / codex directories to that registry, and
``setup`` runs the founder setup (codex, treasury token, launch) the browser
wizard would run.

Everything lives in ``~/.realms/local`` (override: ``REALMS_LOCAL_HOME``). The
identity is a throwaway plaintext icp-cli identity that only exists for this
local network.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

import typer
from rich.console import Console

console = Console()

REALMS_REPO = "smart-social-contracts/realms-gos"
FILE_REGISTRY_REPO = "smart-social-contracts/file-registry"
FILE_REGISTRY_VERSION = "v0.1.1"
ICP_CLI_NPM = "@icp-sdk/icp-cli@1.3.0"
IC_WASM_NPM = "@icp-sdk/ic-wasm"
LOCAL_IDENTITY = "realms-local"
DEFAULT_PORT = 8000
LOCAL_ICP_LEDGER = "ryjl3-tyaaa-aaaaa-aaaba-cai"
CANISTERS = ("realm_backend", "realm_frontend", "file_registry")
POLL_INTERVAL_S = 3
CODEX_INSTALL_TIMEOUT_S = 900
LAUNCH_TIMEOUT_S = 900


def realms_home() -> Path:
    return Path(os.environ.get("REALMS_HOME", "~/.realms")).expanduser()


def local_home() -> Path:
    return Path(os.environ.get("REALMS_LOCAL_HOME", str(realms_home() / "local"))).expanduser()


def tools_dir() -> Path:
    return realms_home() / "tools"


def _fail(message: str) -> None:
    console.print(f"Error: {message}", style="red", markup=False)
    raise typer.Exit(1)


# ── tools ────────────────────────────────────────────────────────────────


def ensure_icp(yes: bool = False) -> str:
    """Path to an ``icp`` binary; offers to npm-install one into ~/.realms/tools."""
    bin_dir = tools_dir() / "node_modules" / ".bin"
    if (bin_dir / "icp").exists():
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
    found = shutil.which("icp")
    if found:
        return found

    if shutil.which("npm") is None:
        _fail(
            "icp-cli is not installed and npm is not available to install it.\n"
            "  Install Node.js 20+ (https://nodejs.org), then rerun, or install icp-cli\n"
            "  yourself: https://github.com/dfinity/icp-cli"
        )
    console.print(
        f"icp-cli is not installed. realms can install {ICP_CLI_NPM} and {IC_WASM_NPM}\n"
        f"into {tools_dir()} (npm, no sudo)."
    )
    if not yes and not typer.confirm("Install now?", default=True):
        _fail("icp-cli is required for realms local")
    tools_dir().mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["npm", "install", "--no-audit", "--no-fund", "--prefix", str(tools_dir()),
         ICP_CLI_NPM, IC_WASM_NPM]
    )
    if result.returncode != 0 or not (bin_dir / "icp").exists():
        _fail("npm install of icp-cli failed")
    os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
    return str(bin_dir / "icp")


def icp(
    args: List[str],
    *,
    check: bool = True,
    capture: bool = True,
    timeout: int = 600,
    cwd: Optional[Path] = None,
) -> subprocess.CompletedProcess:
    cmd = ["icp", *args]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd or local_home()),
            capture_output=capture,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        _fail(f"timed out after {timeout}s: {' '.join(cmd)}")
    if check and proc.returncode != 0:
        detail = ((proc.stderr or "") + (proc.stdout or "")).strip() if capture else ""
        _fail(f"{' '.join(cmd)} failed (exit {proc.returncode})\n{detail}")
    return proc


def ensure_identity() -> str:
    """Principal of the throwaway local identity, creating it if needed."""
    proc = icp(["identity", "principal", "--identity", LOCAL_IDENTITY], check=False, cwd=Path.home())
    if proc.returncode != 0:
        local_home().mkdir(parents=True, exist_ok=True)
        icp(
            ["identity", "new", LOCAL_IDENTITY, "--storage", "plaintext",
             "--output-seed", str(local_home() / f"{LOCAL_IDENTITY}.seed")],
            cwd=Path.home(),
        )
        proc = icp(["identity", "principal", "--identity", LOCAL_IDENTITY], cwd=Path.home())
    return proc.stdout.strip()


# ── release artifacts ────────────────────────────────────────────────────


def cli_release_tag() -> str:
    """The release matching this CLI: 0.7.0rc3 → v0.7.0-rc3."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        raw = version("realms-gos")
    except PackageNotFoundError:
        return "latest"
    m = re.match(r"^(\d+\.\d+\.\d+)(?:(a|b|rc)(\d+))?", raw)
    if not m:
        return "latest"
    tag = f"v{m.group(1)}"
    if m.group(2):
        tag += f"-{m.group(2)}{m.group(3)}"
    return tag


def latest_release_tag(repo: str) -> str:
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/releases/latest",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "realms-cli"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())["tag_name"]


def _download(url: str, dest: Path) -> Path:
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    console.print(f"  download {url}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "realms-cli"})
    try:
        with urllib.request.urlopen(req, timeout=300) as resp, open(tmp, "wb") as fh:
            shutil.copyfileobj(resp, fh)
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        _fail(f"download failed: {url}: {exc}")
    tmp.rename(dest)
    return dest


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _verify_checksums(files: List[Path], checksums: Path) -> None:
    sums: Dict[str, str] = {}
    for line in checksums.read_text().splitlines():
        parts = line.split()
        if len(parts) == 2:
            sums[parts[1].lstrip("*")] = parts[0].lower()
    for path in files:
        expected = sums.get(path.name)
        if not expected:
            continue
        if _sha256(path) != expected:
            path.unlink(missing_ok=True)
            _fail(f"checksum mismatch for {path.name} (deleted; rerun to download again)")


def fetch_release(tag: str) -> Dict[str, Path]:
    """Download (and cache) the realm canisters of ``tag`` and the file registry."""
    cache = realms_home() / "cache"
    rel = cache / "realms-gos" / tag
    base = f"https://github.com/{REALMS_REPO}/releases/download/{tag}"
    backend = _download(f"{base}/realm-backend-{tag}.wasm.gz", rel / f"realm-backend-{tag}.wasm.gz")
    frontend = _download(f"{base}/realm-frontend-{tag}.tar.gz", rel / f"realm-frontend-{tag}.tar.gz")
    try:
        checksums = _download(f"{base}/checksums-{tag}.txt", rel / f"checksums-{tag}.txt")
        _verify_checksums([backend, frontend], checksums)
    except typer.Exit:
        raise
    except Exception:
        console.print("  [yellow]no checksums file in this release; skipping verification[/yellow]")

    fr = cache / "file-registry" / FILE_REGISTRY_VERSION
    fr_base = f"https://github.com/{FILE_REGISTRY_REPO}/releases/download/{FILE_REGISTRY_VERSION}"
    registry = _download(f"{fr_base}/file_registry.wasm.gz", fr / "file_registry.wasm.gz")
    return {"realm_backend": backend, "realm_frontend": frontend, "file_registry": registry}


# ── core extensions ──────────────────────────────────────────────────────
#
# Every codex install also installs the realm's core extensions (dashboards,
# settings, voting, vault, …), so the local registry needs them. They are not
# release assets: they come from the realms-extensions commit the release tag
# pins as its `extensions` submodule, and their frontend-rt bundles are built
# here once and cached.


def _github_get(url: str) -> bytes:
    headers = {"User-Agent": "realms-cli", "Accept": "application/vnd.github+json"}
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=300) as resp:
        return resp.read()


def core_extension_ids(tag: str) -> List[str]:
    """CORE_EXTENSION_IDS of the realm_backend at ``tag``."""
    import ast

    url = (
        f"https://raw.githubusercontent.com/{REALMS_REPO}/{tag}/"
        "src/realm_backend/core/core_extensions.py"
    )
    source = _github_get(url).decode("utf-8")
    for node in ast.walk(ast.parse(source)):
        targets = getattr(node, "targets", None) or [getattr(node, "target", None)]
        if any(getattr(t, "id", "") == "CORE_EXTENSION_IDS" for t in targets if t is not None):
            return sorted(
                n.value for n in ast.walk(node.value)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
            )
    _fail(f"CORE_EXTENSION_IDS not found in {url}")
    return []


def fetch_extensions_source(tag: str) -> Path:
    """realms-extensions source at the commit ``tag`` pins (cached)."""
    meta = json.loads(_github_get(
        f"https://api.github.com/repos/{REALMS_REPO}/contents/extensions?ref={tag}"
    ))
    sha = meta["sha"]
    dest = realms_home() / "cache" / "realms-extensions" / sha
    if (dest / "extensions").is_dir():
        return dest / "extensions"
    console.print(f"  download realms-extensions@{sha[:7]} (pinned by {tag})")
    data = _github_get(f"https://codeload.github.com/smart-social-contracts/realms-extensions/tar.gz/{sha}")
    with tempfile.TemporaryDirectory(prefix="realms-extensions-") as tmp:
        tarball = Path(tmp) / "src.tar.gz"
        tarball.write_bytes(data)
        with tarfile.open(tarball) as tf:
            tf.extractall(tmp)
        top = next(p for p in Path(tmp).iterdir() if p.is_dir())
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(top), str(dest))
    return dest / "extensions"


def _build_frontend_rt(ext_dir: Path) -> Optional[str]:
    """Build ``frontend-rt/dist/index.js`` if the extension has one. Returns an error or None."""
    fe = ext_dir / "frontend-rt"
    if not (fe / "package.json").is_file() or (fe / "dist" / "index.js").is_file():
        return None
    for args in (["install", "--no-audit", "--no-fund"], ["run", "build"]):
        proc = subprocess.run(["npm", *args], cwd=str(fe), capture_output=True, text=True)
        if proc.returncode != 0:
            return f"npm {args[0]} failed: {(proc.stderr or proc.stdout).strip()[-400:]}"
    if not (fe / "dist" / "index.js").is_file():
        return "dist/index.js not produced"
    return None


def seed_core_extensions(state: Dict[str, Any], build_ui: bool) -> List[str]:
    from concurrent.futures import ThreadPoolExecutor

    from .extension import publish_extension_command

    tag = state["version"]
    ids = core_extension_ids(tag)
    src = fetch_extensions_source(tag)
    missing = [e for e in ids if not (src / e / "manifest.json").is_file()]
    if missing:
        _fail(f"core extensions missing from realms-extensions source: {', '.join(missing)}")

    if build_ui and shutil.which("npm"):
        todo = [e for e in ids if (src / e / "frontend-rt" / "package.json").is_file()
                and not (src / e / "frontend-rt" / "dist" / "index.js").is_file()]
        if todo:
            console.print(f"  build frontend-rt bundles for {len(todo)} core extensions (cached afterwards)")
            with ThreadPoolExecutor(max_workers=4) as pool:
                for ext_id, err in zip(todo, pool.map(lambda e: _build_frontend_rt(src / e), todo)):
                    if err:
                        _fail(f"core extension {ext_id}: {err}")
    elif build_ui:
        console.print("  [yellow]npm not found: core extensions are published without UI bundles[/yellow]")

    console.print(f"  publish {len(ids)} core extensions to the local registry")
    for ext_id in ids:
        publish_extension_command(
            state["canisters"]["file_registry"],
            str(src / ext_id),
            network=network_url(state),
            identity=LOCAL_IDENTITY,
        )
    return ids


# ── project ──────────────────────────────────────────────────────────────


ICP_YAML = """\
# Generated by `realms local start`. Recreated on every start.
networks:
  - name: local
    mode: managed
    gateway:
      bind: 127.0.0.1
      port: {port}
    ii: true

environments:
  - name: local
    network: local

canisters:
  - name: realm_backend
    recipe:
      type: "@dfinity/prebuilt@v2.0.0"
      configuration:
        path: artifacts/realm_backend.wasm.gz
    init_args: '("")'
  - name: realm_frontend
    recipe:
      type: "@dfinity/asset-canister@v2.2.0"
      configuration:
        dir: frontend
  - name: file_registry
    recipe:
      type: "@dfinity/prebuilt@v2.0.0"
      configuration:
        path: artifacts/file_registry.wasm.gz
"""


def state_path() -> Path:
    return local_home() / "state.json"


def load_state() -> Dict[str, Any]:
    try:
        return json.loads(state_path().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: Dict[str, Any]) -> None:
    local_home().mkdir(parents=True, exist_ok=True)
    state_path().write_text(json.dumps(state, indent=2) + "\n")


def require_state() -> Dict[str, Any]:
    state = load_state()
    if not state.get("canisters"):
        _fail("no local realm yet: run `realms local start` first")
    ensure_icp(yes=False)
    return state


def network_url(state: Dict[str, Any]) -> str:
    return f"http://127.0.0.1:{state['port']}"


def frontend_url(state: Dict[str, Any]) -> str:
    return f"http://{state['canisters']['realm_frontend']}.localhost:{state['port']}"


def network_running() -> bool:
    if not (local_home() / "icp.yaml").exists():
        return False
    return icp(["network", "ping", "local"], check=False, timeout=30).returncode == 0


def _extract_frontend(tarball: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    with tempfile.TemporaryDirectory(prefix="realms-frontend-") as tmp:
        with tarfile.open(tarball) as tf:
            tf.extractall(tmp)
        roots = sorted(Path(tmp).rglob("index.html"), key=lambda p: len(p.parts))
        if not roots:
            _fail(f"{tarball.name} has no index.html")
        shutil.copytree(roots[0].parent, dest)


def _write_canister_ids_js(state: Dict[str, Any]) -> None:
    ids = state["canisters"]
    payload = {
        "realm_backend": ids["realm_backend"],
        "file_registry": ids["file_registry"],
        "internet_identity": f"http://id.ai.localhost:{state['port']}",
        "derivation_origin": "",
        "portal_url": "",
        "test_mode_ii_bypass": False,
    }
    (local_home() / "frontend" / "canister_ids.js").write_text(
        f"globalThis.__CANISTER_IDS = {json.dumps(payload)};\n"
    )


# ── calls ────────────────────────────────────────────────────────────────


def call(
    state: Dict[str, Any],
    method: str,
    arg: str = "()",
    *,
    canister: str = "realm_backend",
    identity: str = LOCAL_IDENTITY,
    query: bool = False,
    timeout: int = 300,
) -> Any:
    from .extension import _dfx_call

    target = state["canisters"].get(canister, canister)
    return _dfx_call(target, method, arg, network_url(state), identity, is_query=query, timeout=timeout)


def json_call(state: Dict[str, Any], method: str, payload: Any = None, **kw) -> Dict[str, Any]:
    if payload is None:
        arg = "()"
    else:
        arg = "(" + json.dumps(json.dumps(payload)) + ")"
    raw = call(state, method, arg, **kw)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            _fail(f"{method} returned non-JSON: {raw[:300]}")
    if not isinstance(raw, dict):
        _fail(f"{method} returned unexpected payload: {raw!r}")
    if raw.get("success") is False or raw.get("ok") is False:
        _fail(f"{method}: {raw.get('error') or raw}")
    return raw


# ── commands ─────────────────────────────────────────────────────────────


def start_command(
    port: int, version: str, yes: bool, fresh: bool, core_ui: bool = True
) -> Dict[str, Any]:
    ensure_icp(yes=yes)
    home = local_home()
    state = load_state()

    if fresh:
        stop_command(quiet=True)
        if home.exists():
            shutil.rmtree(home)
        state = {}
    elif state.get("canisters") and (home / "icp.yaml").exists():
        if network_running():
            console.print(f"[green]✓ Local realm already running[/green] ({network_url(state)})")
            print_status(state)
            return state
        _fail(
            f"a local realm exists in {home} but its network is not running.\n"
            "  icp-cli does not keep canister state across restarts: rerun with --fresh."
        )

    principal = ensure_identity()
    tag = version or cli_release_tag()
    if tag == "latest":
        tag = latest_release_tag(REALMS_REPO)
    console.print(f"[bold]Local realm[/bold] {tag} on port {port}  (state: {home})")

    artifacts = fetch_release(tag)
    (home / "artifacts").mkdir(parents=True, exist_ok=True)
    shutil.copy2(artifacts["realm_backend"], home / "artifacts" / "realm_backend.wasm.gz")
    shutil.copy2(artifacts["file_registry"], home / "artifacts" / "file_registry.wasm.gz")
    _extract_frontend(artifacts["realm_frontend"], home / "frontend")
    (home / "icp.yaml").write_text(ICP_YAML.format(port=port))

    console.print("  icp network start")
    icp(["network", "start", "-d"], timeout=300)

    ids: Dict[str, str] = {}
    for name in CANISTERS:
        ids[name] = icp(
            ["canister", "create", name, "-e", "local", "--identity", LOCAL_IDENTITY, "-q"]
        ).stdout.strip().splitlines()[-1]
    state = {
        "version": tag,
        "port": port,
        "identity": LOCAL_IDENTITY,
        "principal": principal,
        "file_registry_version": FILE_REGISTRY_VERSION,
        "canisters": ids,
    }
    save_state(state)
    _write_canister_ids_js(state)

    console.print("  icp deploy (realm_backend, realm_frontend, file_registry)")
    icp(["deploy", "-e", "local", "--identity", LOCAL_IDENTITY, "--yes"], timeout=900)

    # The backend writes installed extension frontends into the asset canister.
    call(
        state,
        "grant_permission",
        f'(record {{ to_principal = principal "{ids["realm_backend"]}"; permission = variant {{ Commit }} }})',
        canister="realm_frontend",
    )
    console.print("  enter_setup")
    raw = call(state, "enter_setup", f'(principal "{principal}", "", "local")', timeout=180)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = {}
    if isinstance(raw, dict) and raw.get("ok") is False:
        _fail(f"enter_setup: {raw.get('error')}")
    json_call(
        state,
        "set_canister_config_json",
        {
            "frontend_canister_id": ids["realm_frontend"],
            "file_registry_canister_id": ids["file_registry"],
            "creator_principal": principal,
        },
    )
    # file-registry has no namespace approval API, so registry installs must not wait on it.
    json_call(state, "update_realm_config", {"require_marketplace_approval": False})

    state["core_extensions"] = seed_core_extensions(state, build_ui=core_ui)
    save_state(state)

    console.print("[green]✓ Local realm deployed (in setup)[/green]")
    print_status(state)
    return state


def print_status(state: Dict[str, Any]) -> None:
    ids = state.get("canisters") or {}
    console.print(f"  realm:          {frontend_url(state)}/")
    console.print(f"  realm_backend:  {ids.get('realm_backend')}")
    console.print(f"  file_registry:  {ids.get('file_registry')}")
    console.print(f"  identity:       {state.get('identity')} ({state.get('principal')})")
    console.print(f"  release:        {state.get('version')}")


def status_command(as_json: bool) -> None:
    state = require_state()
    running = network_running()
    launch = None
    if running:
        try:
            raw = call(state, "get_setup_launch_status", query=True, timeout=60)
            launch = (json.loads(raw) if isinstance(raw, str) else raw or {}).get("launch", {}).get("status")
        except Exception:
            launch = None
    if as_json:
        out = dict(state)
        out.update({"running": running, "url": frontend_url(state), "launch": launch})
        print(json.dumps(out, indent=2))
        return
    console.print(f"network: {'running' if running else 'stopped'}  ({network_url(state)})")
    if launch:
        console.print(f"setup launch: {launch}")
    print_status(state)


def stop_command(quiet: bool = False) -> None:
    if not (local_home() / "icp.yaml").exists():
        if not quiet:
            console.print("no local realm network to stop")
        return
    ensure_icp(yes=False)
    proc = icp(["network", "stop", "local"], check=False, timeout=120)
    if not quiet:
        if proc.returncode == 0:
            console.print("[green]✓ Local network stopped[/green] (canister state is discarded; "
                          "`realms local start --fresh` deploys a new realm)")
        else:
            console.print((proc.stderr or proc.stdout or "").strip())


def _package_kind(path: Path) -> str:
    manifest_path = path / "manifest.json"
    if not manifest_path.is_file():
        _fail(f"{path}: no manifest.json")
    manifest = json.loads(manifest_path.read_text())
    return "codex" if manifest.get("kind") == "codex" else "extension"


def _package_id(manifest: Dict[str, Any], path: Path) -> str:
    # Same precedence as publish_extension_command: extension manifests carry
    # their id in the legacy "name" field.
    return str(manifest.get("id") or manifest.get("name") or path.name)


def publish_command(paths: List[str], install: bool = False) -> None:
    from .extension import publish_codex_command, publish_extension_command

    state = require_state()
    registry = state["canisters"]["file_registry"]
    published = state.setdefault("published", {})
    # Extensions first: a codex lists them as dependencies.
    ordered = sorted((Path(p).expanduser().resolve() for p in paths), key=lambda p: _package_kind(p) == "codex")
    for path in ordered:
        kind = _package_kind(path)
        manifest = json.loads((path / "manifest.json").read_text())
        deps = manifest.get("dependencies") or []
        published[_package_id(manifest, path)] = {
            "kind": kind,
            "version": manifest.get("version"),
            "dependencies": list(deps.keys()) if isinstance(deps, dict) else list(deps),
            "path": str(path),
        }
        if kind == "extension":
            if manifest.get("runtime") == "sandboxed" and (path / "frontend").is_dir() \
                    and not (path / "frontend" / "dist" / "index.html").is_file():
                _fail(f"{path}: frontend/dist is missing; run `npm install && npm run build` first")
            console.print(f"[bold]publish extension[/bold] {path}")
            publish_extension_command(
                registry, str(path), network=network_url(state), identity=LOCAL_IDENTITY
            )
        else:
            console.print(f"[bold]publish codex[/bold] {path}")
            publish_codex_command(
                registry, str(path), network=network_url(state), identity=LOCAL_IDENTITY
            )
        save_state(state)
    console.print("[green]✓ Published[/green]")
    if install:
        for path in ordered:
            ext_id = _package_id(json.loads((path / "manifest.json").read_text()), path)
            if _package_kind(path) == "codex":
                console.print(f"  {ext_id} is a codex: use `realms local setup --codex {ext_id}`")
                continue
            console.print(f"  install_extension_from_registry {ext_id}")
            _install_extension(state, ext_id)
        console.print("[green]✓ Installed in the local realm[/green]")


def _install_extension(state: Dict[str, Any], ext_id: str) -> None:
    payload = {
        "registry_canister_id": state["canisters"]["file_registry"],
        "ext_id": ext_id,
        "version": "latest",
    }
    raw = call(state, "install_extension_from_registry", "(" + json.dumps(json.dumps(payload)) + ")", timeout=600)
    data = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {}
    if isinstance(data, dict) and data.get("success") is False:
        err = str(data.get("error") or data)
        if "already" in err.lower():
            return
        _fail(f"install_extension_from_registry {ext_id}: {err}")


def install_codex_dependencies(state: Dict[str, Any], codex: str) -> None:
    """Install the codex's extensions and the core extensions one call each.

    ``setup_install_codex`` only installs the codex package; installing all
    dependencies inside one call would not fit a single message's budget.
    """
    deps = list((state.get("published", {}).get(codex) or {}).get("dependencies") or [])
    for core in state.get("core_extensions") or []:
        if core not in deps:
            deps.append(core)
    try:
        raw = call(state, "list_runtime_extensions", query=True, timeout=60)
        installed = set((json.loads(raw) if isinstance(raw, str) else raw or {}).get("runtime_extensions") or [])
    except Exception:
        installed = set()
    pending = [d for d in deps if d not in installed]
    if not pending:
        return
    console.print(f"  install {len(pending)} extensions for {codex}")
    for ext_id in pending:
        console.print(f"  install_extension_from_registry {ext_id}")
        _install_extension(state, ext_id)


def setup_command(codex: str, codex_version: Optional[str], token_canister: str) -> None:
    state = require_state()
    raw = call(state, "get_setup_launch_status", query=True, timeout=60)
    launch = (json.loads(raw) if isinstance(raw, str) else raw or {}).get("launch") or {}
    if launch.get("status") == "completed":
        install_codex_dependencies(state, codex)
        console.print("[green]✓ Setup already completed[/green]")
        return

    draft: Dict[str, Any] = {"codex": {"package": codex}}
    if codex_version:
        draft["codex"]["version"] = codex_version
    draft["token"] = {"token_canister_id": token_canister}
    console.print(f"  setup_save_draft codex={codex}")
    json_call(state, "setup_save_draft", draft)
    install_codex_dependencies(state, codex)

    install_args: Dict[str, Any] = {"package": codex}
    if codex_version:
        install_args["version"] = codex_version
    started, last = time.time(), ""
    while True:
        res = json_call(state, "setup_install_codex", install_args, timeout=600)
        status = str(res.get("status") or "")
        label = f"{status} {res.get('phase') or res.get('step') or ''}".strip() or "ok"
        if label != last:
            console.print(f"  setup_install_codex: {label}")
            last = label
        if status != "in_progress":
            break
        if time.time() - started > CODEX_INSTALL_TIMEOUT_S:
            _fail(f"setup_install_codex {codex} did not finish in {CODEX_INSTALL_TIMEOUT_S}s")
        time.sleep(POLL_INTERVAL_S)
    installed_version = str(res.get("version") or (res.get("codex") or {}).get("version") or "")
    if installed_version and not codex_version:
        # Pin the draft to what was installed so launch's install phase skips it.
        json_call(state, "setup_save_draft", {"codex": {"package": codex, "version": installed_version}})

    console.print(f"  setup_configure_token {token_canister}")
    json_call(state, "setup_configure_token", {"token_canister_id": token_canister})

    console.print("  setup_launch")
    json_call(state, "setup_launch")
    started, last = time.time(), ""
    while True:
        raw = call(state, "get_setup_launch_status", query=True, timeout=60)
        launch = (json.loads(raw) if isinstance(raw, str) else raw or {}).get("launch") or {}
        status = str(launch.get("status") or "unknown")
        label = f"{status} {launch.get('phase') or ''}".strip()
        if label != last:
            console.print(f"  launch: {label}")
            last = label
        if status == "completed":
            break
        if status == "failed":
            _fail(f"setup_launch failed: {launch.get('error') or launch}")
        if time.time() - started > LAUNCH_TIMEOUT_S:
            _fail(f"setup_launch did not complete in {LAUNCH_TIMEOUT_S}s")
        time.sleep(POLL_INTERVAL_S)

    state["codex"] = codex
    save_state(state)
    console.print(f"[green]✓ Realm launched with codex {codex}[/green]")
    print_status(state)


def add_admin_command(principal: str) -> None:
    state = require_state()
    raw = call(state, "register_co_admin", f'("{principal}")', timeout=180)
    data = json.loads(raw) if isinstance(raw, str) else raw
    if isinstance(data, dict) and data.get("success") is False:
        _fail(f"register_co_admin: {data.get('error')}")
    console.print(f"[green]✓ {principal} is an admin of the local realm[/green]")


_RESPONSE_FIELD = re.compile(r'response\s*=\s*"((?:[^"\\]|\\.)*)"', re.S)
_SUCCESS_FIELD = re.compile(r"success\s*=\s*(true|false)")


def ext_call_command(extension: str, function: str, args: str, as_json: bool) -> Dict[str, Any]:
    """``extension_async_call`` → ``{"success": bool, "response": <decoded JSON or text>}``."""
    from .extension import _candid_unescape

    state = require_state()
    arg = f"({json.dumps(extension)}, {json.dumps(function)}, {json.dumps(args)})"
    target = state["canisters"]["realm_backend"]
    proc = subprocess.run(
        ["icp", "canister", "call", target, "extension_async_call", arg,
         "--network", network_url(state), "--root-key", "fetch",
         "--identity", LOCAL_IDENTITY, "-o", "candid"],
        capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        _fail(f"extension_async_call failed: {(proc.stderr or proc.stdout).strip()}")
    resp_m = _RESPONSE_FIELD.search(proc.stdout)
    ok_m = _SUCCESS_FIELD.search(proc.stdout)
    if not resp_m or not ok_m:
        _fail(f"unexpected extension_async_call reply: {proc.stdout.strip()[:300]}")
    text = _candid_unescape(resp_m.group(1))
    try:
        response: Any = json.loads(text)
    except json.JSONDecodeError:
        response = text
    result = {"success": ok_m.group(1) == "true", "response": response}
    if as_json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        style = "green" if result["success"] else "red"
        console.print(response if isinstance(response, str) else json.dumps(response, indent=2),
                      style=style, markup=False)
    if not result["success"]:
        raise typer.Exit(1)
    return result
