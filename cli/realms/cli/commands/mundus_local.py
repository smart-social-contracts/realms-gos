"""``realms mundus up``: a whole mundus on this machine, from one file.

A mundus file (``examples/mundi/*.json``) names the realms of a mundus: each one's codex or legislation layer, the
realms it follows, its quarters and the simulated people who live in it. ``up`` builds and starts everything they
need, from nothing:

1. the sources: this realms-gos checkout, and gos-as-a-service, Casals, file-registry and geister, cloned at the refs
   the file names (or used where the file points);
2. a Python environment for all of them, which the mundus commands then run in;
3. the builds: every canister and frontend of the checkouts;
4. a local network of its own, with a local Internet Identity, the Realms product orchestra and the GaaS orchestra
   (``scripts/local_up.sh --gaas``);
5. the realms, each minted through the GaaS portal and launched by its founder (``mundus_realms``);
6. a capital's quarters, then the links, layers and publications between the realms (``mundus_layers``);
7. a browser check of the portal and every realm, and the swarm (geister) living in them.

A second ``up`` does only what is missing: a realm added to the file is minted, the rest is left alone.

Everything a mundus keeps is under ``~/.realms/mundi`` (``REALMS_MUNDI_HOME``): ``sources/`` and ``venv/`` shared by
every mundus, and one folder per mundus with its network, bindings, realms, logs and swarm. ``current`` points at the
mundus the commands act on.

Moved here from the some-repos-4 workspace's ``.cto/gaas-local-sim/`` (``mundus``, ``env.sh``, ``build.sh``;
18 Nov 2026), where it was first written and proven.
"""

import glob
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

console = Console(soft_wrap=True)

REPOS = ("gos-as-a-service", "Casals", "file-registry", "geister")
GITHUB = "https://github.com/smart-social-contracts"
IDENTITY = "realms-mundus"  # a throwaway plaintext key, local network only
PORTS = range(8001, 8100)
LAUNCHERS = "~/.local/share/icp-cli/pkg/network-launcher/*/icp-cli-network-launcher"
MAINNET_II = "https://identity.ic0.app"


def mundi_home() -> Path:
    realms_home = Path(os.environ.get("REALMS_HOME", "~/.realms")).expanduser()
    return Path(os.environ.get("REALMS_MUNDI_HOME", str(realms_home / "mundi"))).expanduser()


def realms_checkout() -> Path:
    """The realms-gos checkout this CLI was installed from: a mundus builds it as it is."""
    root = Path(__file__).resolve().parents[4]
    if not (root / "scripts" / "local_up.sh").exists():
        fail(f"realms mundus runs from a realms-gos checkout (pip install -e cli); this CLI is installed at {root}")
    return root


def fail(message: str):
    console.print(f"Error: {message}", style="red", markup=False)
    raise SystemExit(1)


def step(message: str) -> None:
    console.print(f"\n==> [{time.strftime('%H:%M:%S')}] {message}", style="bold blue", markup=False)


@dataclass
class Mundus:
    name: str
    spec: dict
    state: Path
    realms_dir: Path
    sources: dict

    identity: str = IDENTITY

    @property
    def casals_home(self) -> Path:
        return self.state / "home"

    @property
    def port(self) -> int:
        return self.instance().get("port")

    def instance(self) -> dict:
        path = self.state / "instance.json"
        return json.loads(path.read_text()) if path.exists() else {}

    def remember(self, **values) -> None:
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "instance.json").write_text(json.dumps({**self.instance(), **values}, indent=1) + "\n")

    def ii_url(self) -> str:
        """The launcher's Internet Identity: its sign-in page is this frontend canister (the backend is rdmx6-...)."""
        return f"http://uqzsh-gqaaa-aaaaq-qaada-cai.localhost:{self.port}"

    def env(self) -> dict:
        """The environment every build and stack script of this mundus runs in."""
        dirs = {"REALMS_DIR": self.realms_dir, "CASALS_DIR": self.sources["Casals"],
                "GAAS_DIR": self.sources["gos-as-a-service"], "FILE_REGISTRY_DIR": self.sources["file-registry"]}
        env = {**os.environ, **{k: str(v) for k, v in dirs.items()},
               "CASALS_LOCAL_ROOTS": ":".join(str(dirs[k]) for k in ("REALMS_DIR", "CASALS_DIR", "GAAS_DIR")),
               # Our own bindings and replica state: never ~/.casals (production bindings) nor ~/casals-home.
               "CASALS_HOME": str(self.casals_home), "LOCAL_UP_LOG_DIR": str(self.state / "logs"),
               "GAAS_SHEET": str(self.state / "e2e" / "casals.local-build.json"),
               "CASALS_E2E_IDENTITY": self.identity, "IDENTITY": self.identity, "ENV": "local",
               "ICP_CLI_NETWORK_LAUNCHER_PATH": str(self.realms_dir / "scripts" / "mundus" / "launcher-with-ii.sh"),
               "MUNDUS_LAUNCHER": launcher(),
               "TERM": os.environ.get("TERM") or "xterm", "DFX_WARNING": "-mainnet_plaintext_identity",
               "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1",
               # The scripts call python3, realms, basilisk and geister by name: the mundus' environment's.
               "VIRTUAL_ENV": str(venv()), "PATH": f"{venv() / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}"}
        if self.port:
            env.update(CASALS_REPLICA_PORT=str(self.port), CASALS_NETWORK_URL=f"http://127.0.0.1:{self.port}",
                       MUNDUS_II_URL=self.ii_url())
        return env

    def run(self, *cmd, cwd: Path = None, **extra) -> None:
        """A step's command, in this mundus' environment, its output on the console."""
        out = subprocess.run([str(c) for c in cmd], cwd=str(cwd) if cwd else None, env={**self.env(), **extra})
        if out.returncode:
            fail(f"{' '.join(str(c) for c in cmd)} failed (exit {out.returncode})")


# -- which mundus


def mundus_file(name_or_path: str) -> Path:
    """A mundus file by path, or by name among the examples of this checkout (``examples/mundi/<name>.json``)."""
    path = Path(name_or_path).expanduser()
    if path.suffix == ".json" and path.exists():
        return path.resolve()
    named = realms_checkout() / "examples" / "mundi" / f"{name_or_path}.json"
    if not named.exists():
        known = ", ".join(sorted(p.stem for p in named.parent.glob("*.json")))
        fail(f"no mundus file {name_or_path}: give a path, or one of: {known}")
    return named


def current_name() -> str:
    link = mundi_home() / "current"
    if not link.is_symlink():
        fail("no mundus yet: realms mundus up <name>")
    return os.readlink(link)


def open_mundus(name_or_path: str = "") -> Mundus:
    """The mundus a command acts on: the one named, or the current one. Its file is remembered at ``up``."""
    if name_or_path:
        path = mundus_file(name_or_path)
        spec = json.loads(path.read_text())
        name = spec.get("name") or path.stem
    else:
        name = current_name()
        remembered = json.loads((mundi_home() / name / "instance.json").read_text())
        path = Path(remembered["file"])
        spec = json.loads(path.read_text())
    state = mundi_home() / name
    m = Mundus(name=name, spec=spec, state=state, realms_dir=realms_checkout(),
               sources={repo: source_dir(spec, repo) for repo in REPOS})
    # GaaS's helper (mundus_realms.platform) reads where the bindings are when it is imported.
    if m.port:
        os.environ.update({k: v for k, v in m.env().items() if k.startswith("CASALS_") or k in ("IDENTITY", "ENV")})
    return m


def source_dir(spec: dict, repo: str) -> Path:
    """Where ``repo`` is checked out for the mundus: the path the file gives, else the shared clone."""
    ref = (spec.get("sources") or {}).get(repo, "main")
    if ref.startswith(("/", "~", ".")):
        return Path(ref).expanduser().resolve()
    return mundi_home() / "sources" / repo


# -- sources and python


def fetch_sources(m: Mundus) -> None:
    """Clone each source the mundus file names by ref, and check that ref out (detached: a build tree, not a branch
    anyone works on). A source the file points at by path is used as it is."""
    for repo in REPOS:
        ref = (m.spec.get("sources") or {}).get(repo, "main")
        path = m.sources[repo]
        if ref.startswith(("/", "~", ".")):
            if not path.exists():
                fail(f"{repo}: no checkout at {path}")
            continue
        if not (path / ".git").exists():
            step(f"Sources: {repo}")
            subprocess.run(["git", "clone", "--quiet", f"{GITHUB}/{repo}.git", str(path)], check=True)
        subprocess.run(["git", "-C", str(path), "fetch", "--quiet", "origin"], check=True)
        target = ref if ref_exists(path, ref) and not ref_exists(path, f"origin/{ref}") else f"origin/{ref}"
        subprocess.run(["git", "-C", str(path), "checkout", "--quiet", "--detach", target], check=True)
        subprocess.run(["git", "-C", str(path), "submodule", "update", "--init", "--recursive", "--quiet"], check=True)
    for sub in ("extensions", "codices", "ggg"):
        if not any((m.realms_dir / sub).iterdir()):
            subprocess.run(["git", "-C", str(m.realms_dir), "submodule", "update", "--init", "--recursive", sub],
                           check=True)


def ref_exists(path: Path, ref: str) -> bool:
    return subprocess.run(["git", "-C", str(path), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
                          capture_output=True).returncode == 0


def venv() -> Path:
    return mundi_home() / "venv"


def requirements(m: Mundus) -> list:
    """What the mundus' Python environment installs, in order: Casals's, GaaS's, then realms-gos's last (the three pin
    different ic-basilisk versions, and the canisters are built with realms-gos's), this CLI, and geister."""
    return [["-r", m.sources["Casals"] / "requirements-dev.txt"],
            ["-r", m.sources["gos-as-a-service"] / "requirements-dev.txt"],
            ["-r", m.realms_dir / "requirements.txt", "ic-py>=1.0.1", "pillow>=10", "pyyaml", "requests"],
            ["-e", m.realms_dir / "cli"],
            ["-e", m.sources["geister"]]]


def ensure_venv(m: Mundus) -> bool:
    """The mundus' Python environment, made or brought up to date when what it installs changed. Returns whether it
    installed anything: a process already running in it does not see what was installed (Python reads a package's
    .pth file at start)."""
    wanted = requirements(m)
    stamp = hashlib.sha256(json.dumps([[str(x) for x in w] for w in wanted] + [
        Path(w[1]).read_text() for w in wanted if w[0] == "-r"]).encode()).hexdigest()
    marker = venv() / ".installed"
    if marker.exists() and marker.read_text() == stamp:
        return False
    step(f"Python: {venv()}")
    if not (venv() / "bin" / "python").exists():
        subprocess.run(["/usr/bin/env", "-u", "VIRTUAL_ENV", "python3", "-m", "venv", str(venv())], check=True)
    pip = [str(venv() / "bin" / "python"), "-m", "pip", "install", "-q"]
    subprocess.run(pip + ["--upgrade", "pip"], check=True)
    for w in wanted:
        subprocess.run(pip + [str(x) for x in w], check=True)
    marker.write_text(stamp)
    return True


def in_venv() -> bool:
    return Path(sys.prefix).resolve() == venv().resolve()


def enter_venv(argv: list, again: bool = False) -> None:
    """Run this same command again inside the mundus' environment, unless it already is (and ``again`` is not
    asked)."""
    if in_venv() and not again:
        return
    os.execv(str(venv() / "bin" / "realms"), [str(venv() / "bin" / "realms"), *argv])


# -- the network


def launcher() -> str:
    """The network launcher icp-cli cached on this machine, newest first. Naming it keeps ``icp network start`` from
    asking GitHub for the latest release, which answers 403 without a token."""
    found = sorted(glob.glob(os.path.expanduser(LAUNCHERS)), key=os.path.getmtime)
    if not found:
        fail("no icp-cli network launcher on this machine: run `icp network start` in any icp project once")
    return found[-1]


def free_port() -> int:
    for port in PORTS:
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    fail(f"no free port in {PORTS.start}-{PORTS.stop - 1}")


def stack_is_up(m: Mundus) -> bool:
    """Whether both orchestras answer on this mundus' network."""
    if not (m.casals_home / "gaas" / "gaas.local.json").exists():
        return False
    from . import mundus_realms

    try:
        dr = mundus_realms.platform(m)
        return "realm-registry-backend" in dr.bindings(dr.conductor_backend())
    except Exception:  # a network that is down answers nothing: the stack is not up
        return False


def built(m: Mundus) -> bool:
    r, c, g = m.realms_dir, m.sources["Casals"], m.sources["gos-as-a-service"]
    return all(p.exists() for p in (
        r / ".basilisk/realm_backend/realm_backend.wasm", r / "src/realm_frontend/dist/index.html",
        c / ".basilisk/casals_backend/casals_backend.wasm", g / ".basilisk/realm_installer/realm_installer.wasm",
        g / "node_modules"))


def build_all(m: Mundus) -> None:
    """Every canister and frontend of the checkouts (steps 1 and 2 of scripts/e2e_gaas.sh)."""
    step("realms-gos: realm backend and frontend, marketplace, fleet file registry")
    m.run(m.realms_dir / "scripts" / "up.sh", "-e", "local", "--build-only", "--identity", m.identity)
    step("realms-gos: extension and codex bundles")
    m.run("realms", "files", "build", cwd=m.realms_dir)
    step("Casals: conductor backend")
    m.run("make", "-C", m.sources["Casals"], "build-backend")
    step("gos-as-a-service: installer, registry")
    gaas = m.sources["gos-as-a-service"]
    for canister in ("realm_installer", "realm_registry_backend"):
        m.run(sys.executable, "-m", "basilisk", canister, f"src/{canister}/main.py", cwd=gaas,
              CANISTER_CANDID_PATH=str(gaas / "src" / canister / f"{canister}.did"))
    m.run("npm", "ci", "--legacy-peer-deps", cwd=gaas)


def build_portal(m: Mundus) -> None:
    """What depends on this mundus' port: the portal, and the GaaS sheet whose realm frontends sign in at the local
    Internet Identity instead of the mainnet one."""
    gaas, r = m.sources["gos-as-a-service"], m.realms_dir
    step(f"gos-as-a-service: portal, signing in at {m.ii_url()}")
    m.run("npm", "run", "build", "--workspace=realm_registry_frontend", cwd=gaas,
          VITE_INTERNET_IDENTITY_URL=m.ii_url() + "/")
    sheet = Path(m.env()["GAAS_SHEET"])
    sheet.parent.mkdir(parents=True, exist_ok=True)
    m.run(sys.executable, r / "scripts" / "e2e_gaas_sheet.py", sheet, REALM_VERSION="e2e",
          REALM_BACKEND_WASM=str(r / ".basilisk/realm_backend/realm_backend.wasm"),
          REALM_FRONTEND=str(r / "src/realm_frontend/dist"),
          CASALS_BACKEND_WASM=str(m.sources["Casals"] / ".basilisk/casals_backend/casals_backend.wasm"),
          INSTALLER_WASM=str(gaas / ".basilisk/realm_installer/realm_installer.wasm"),
          REGISTRY_WASM=str(gaas / ".basilisk/realm_registry_backend/realm_registry_backend.wasm"),
          REGISTRY_FRONTEND=str(gaas / "src/realm_registry_frontend/dist"))
    text = sheet.read_text()
    if MAINNET_II not in text:
        fail(f"{sheet}: no {MAINNET_II} to replace with the local Internet Identity; the stand template changed")
    sheet.write_text(text.replace(MAINNET_II, m.ii_url()))


def network_and_orchestras(m: Mundus) -> None:
    step("Network (with Internet Identity), product orchestra, GaaS orchestra, catalogs")
    m.run(m.realms_dir / "scripts" / "local_up.sh", "--gaas", "--no-realm", "--skip-build",
          f"--replica-port={m.port}")
    try:
        import requests

        requests.get(m.ii_url() + "/", timeout=20).raise_for_status()
    except Exception as exc:
        fail(f"the local Internet Identity does not answer at {m.ii_url()}: {exc}")


def portal_test_sign_in(m: Mundus) -> None:
    step(f"Portal: test sign-in, through the local GaaS multisig whose only signer is {m.identity}")
    m.run(sys.executable, m.realms_dir / "scripts" / "e2e_gaas_scale.py", "--identity", m.identity,
          "--out", m.state / "e2e" / "gaas.json")


# -- the commands


def up(name_or_path: str, rebuild: bool, swarm: bool) -> None:
    started = time.time()
    m = open_mundus(name_or_path)
    fetch_sources(m)
    installed = ensure_venv(m)
    m.remember(file=str(mundus_file(name_or_path)))
    enter_venv(["mundus", "up", name_or_path] + (["--rebuild"] if rebuild else []) + ([] if swarm else ["--no-swarm"]),
               again=installed)
    from . import mundus_layers, mundus_realms

    os.environ.update({k: v for k, v in m.env().items() if k in ("CASALS_HOME", "IDENTITY", "ENV")})
    if not m.port:
        m.remember(port=free_port())
    step(f"Mundus {m.name}: port {m.port}, state {m.state}")
    if stack_is_up(m):
        step("Network and both orchestras are up already")
        if rebuild:
            build_all(m)
        step("Catalog: the checkout's extension and codex packages, into the portal's file registry")
        os.environ.update({k: v for k, v in m.env().items() if k.startswith("CASALS_")})
        mundus_realms.publish_packages(m)
    else:
        if rebuild or not built(m):
            build_all(m)
        build_portal(m)
        network_and_orchestras(m)
    os.environ.update({k: v for k, v in m.env().items() if k.startswith("CASALS_")})
    if m.spec.get("test_sign_in", True):
        portal_test_sign_in(m)
    step("Realms: minted through the portal, extensions, launch")
    ok = mundus_realms.mint_and_launch(m)
    # Quarters before layers: a capital gives its quarters a codex when it installs one, and a quarter added later
    # gets none until the next.
    step("Quarters")
    mundus_layers.add_quarters(m)
    step("Links, layers and publications")
    mundus_layers.set_up_layers(m)
    link = mundi_home() / "current"
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(m.name)
    mundus_realms.swarm_config(m)
    step("Browser check")
    check(m)
    if swarm:
        start_swarm(m)
    step(f"Ready in {(time.time() - started) / 60:.0f} min")
    print(mundus_realms.urls(m))
    print(memory(m))
    if not ok:
        fail("a realm did not launch: see above")


def start_swarm(m: Mundus) -> None:
    if not any(r.get("population") for r in m.spec["realms"]):
        return
    step("Swarm: geister members living in the realms")
    m.run(venv() / "bin" / "geister", "swarm", "start", cwd=m.state)


def geister_hint(m: Mundus) -> str:
    return (f"Simulated members (geister), from {m.state}:\n"
            "  geister swarm start | status | stop | dashboard\n"
            "  geister agent ls | show NAME | act NAME OPERATION [JSON]   (--realm NAME)")


def check(m: Mundus) -> None:
    """The portal and every realm (and quarter) as a headless Chromium shows them; a page that fails fails the check."""
    from . import mundus_realms

    pages = []
    for r in mundus_realms.records(m):
        pages.append(f"{r['name']}={r['frontend_url']}/")
        portal = r["portal_url"].rsplit("/r/", 1)[0]
    if not pages:
        return
    pages.append(f"portal={portal}/")
    shots = m.state / "shots"
    shots.mkdir(parents=True, exist_ok=True)
    out = subprocess.run(["node", str(m.realms_dir / "scripts" / "mundus" / "look.mjs"), *pages],
                         capture_output=True, text=True, env={**m.env(), "MUNDUS_SHOTS": str(shots)})
    (m.state / "logs").mkdir(exist_ok=True)
    (m.state / "logs" / "check.json").write_text(out.stdout)
    bad = 0
    decoder, text, at = json.JSONDecoder(), out.stdout, 0
    while at < len(text):
        while at < len(text) and text[at].isspace():
            at += 1
        if at >= len(text):
            break
        page, at = decoder.raw_decode(text, at)
        ok = page["status"] == 200 and page["text"] and "Failed to load" not in page["text"]
        bad += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {page['name']:10} {page['url'][:70]:70} {page['text'][:60]!r}")
    if out.returncode or bad:
        fail(f"browser check: {bad} page(s) failed {out.stderr[-400:]}; screenshots in {shots}")


def memory(m: Mundus) -> str:
    found = subprocess.run(["pgrep", "-f", f"icp-cli-network-launcher.*{m.casals_home}/"], capture_output=True,
                           text=True).stdout.split()
    if not found:
        return "no network of this mundus is running"
    tree, todo = [], [found[0]]
    while todo:
        pid = todo.pop()
        tree.append(pid)
        todo += subprocess.run(["pgrep", "-P", pid], capture_output=True, text=True).stdout.split()
    rss = subprocess.run(["ps", "-o", "rss=", "-p", ",".join(tree)], capture_output=True, text=True).stdout.split()
    return f"network: {len(tree)} processes, {sum(int(x) for x in rss) / 1024:.0f} MB resident"


def status(name: str = "") -> None:
    m = open_mundus(name)
    from . import mundus_realms

    print(memory(m))
    if stack_is_up(m):
        print(mundus_realms.urls(m))
        print(geister_hint(m))


def urls(name: str = "") -> None:
    from . import mundus_realms

    m = open_mundus(name)
    print(mundus_realms.urls(m))
    print(geister_hint(m))


def sign_in(mode: str, name: str = "") -> None:
    from . import mundus_realms

    m = open_mundus(name)
    mundus_realms.sign_in(m, mode == "test")


def clock(name: str = "") -> None:
    """The network's time beside the wall clock. They must agree: a signed call carries an expiry by the wall clock."""
    from .mundus_held import Network

    m = open_mundus(name)
    shown = Network(m.casals_home).now() / 1e9
    ahead = shown - time.time()
    print(f"network  {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(shown))}   ({ahead:+.0f} s from the wall clock"
          + ("; a signed call is refused once it is minutes ahead" if ahead > 60 else "") + ")")


def publish(realm: str, version: str, name: str = "") -> None:
    """A source realm adopts another of its releases (an ordinance, when a layer above asks for one) and publishes it."""
    from . import mundus_layers

    m = open_mundus(name)
    mundus_layers.publish_release(m, realm, version or None)


def install(realm: str, name: str = "") -> None:
    """A realm installs the releases its sync sources relay and it does not run yet."""
    from . import mundus_layers

    m = open_mundus(name)
    mundus_layers.install_relayed(m, realm)


def down(name: str = "") -> None:
    m = open_mundus(name)
    if (m.state / ".swarm" / "pid").exists():
        subprocess.run([str(venv() / "bin" / "geister"), "swarm", "stop"], cwd=m.state)
    subprocess.run([str(m.realms_dir / "scripts" / "local_up.sh"), "--down"], env=m.env())
    # The realms and their ledgers lived on that network: their records name canisters that are gone.
    for gone in ("realms", "bank", "swarm.yaml", ".swarm"):
        path = m.state / gone
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    m.remember(port=None)
    print(f"mundus {m.name} is down; its builds and sources are kept")
