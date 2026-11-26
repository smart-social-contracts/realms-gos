"""The realms of a local mundus: minted through its GaaS portal, launched by their founders, given a swarm.

Each realm is born the way a person makes one: ``request_deployment`` on the realm registry, which the portal's wizard
sends, signed by the founder. The founder's key is geister's key for (seed, "founder"), so the swarm's administration
is the realm's administrator. Then the founder does what the wizard does at launch (extensions, branding, setup steps,
launch) and opens the realm to a browser with two invite codes.

What each realm became is kept in ``<state>/realms/<name>.json``.

Moved here from the some-repos-4 workspace's ``.cto/gaas-local-sim/sim.py`` (18 Nov 2026), where it was first written
and proven with four realms and their swarm.
"""

import base64
import io
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

FAILED_FOR = 120  # seconds an installer job must stay failed before a mint gives up: one read `failed` for 27 s
MINT_TIMEOUT = 1500
IMAGE_MAX = 650_000  # bytes of a PNG kept as it is; the realm keeps an image of at most 960,000 characters
# Test sign-in: the realm shows its picker of test identities instead of sending the visitor to Internet Identity.
TEST_FLAGS = {"ii_bypass": True, "user_self_registration": True, "skip_terms": True}
DEFAULT_CURRENCY = "GST"


# -- records


def record_path(m, name: str) -> Path:
    return m.state / "realms" / f"{name}.json"


def load(m, name: str) -> dict:
    return json.loads(record_path(m, name).read_text())


def save(m, record: dict) -> None:
    path = record_path(m, record["name"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=1) + "\n")


def records(m) -> list:
    """The records of the realms of the mundus file that exist, in its order."""
    return [load(m, r["name"]) for r in m.spec["realms"] if record_path(m, r["name"]).exists()]


def entry(m, name: str) -> dict:
    return next(r for r in m.spec["realms"] if r["name"] == name)


# -- the platform: GaaS's own e2e helpers, and geister's keys


def platform(m):
    """GaaS's ``tests/e2e/deploy_realm.py``: the orchestra's bindings and the installer's jobs, through icp. It reads its
    environment when imported."""
    helper = m.sources["gos-as-a-service"] / "tests" / "e2e" / "deploy_realm.py"
    if not helper.exists():
        sys.exit(f"{helper} is missing: the mundus mints realms through that helper of gos-as-a-service")
    sys.path.insert(0, str(helper.parent))
    import deploy_realm

    return deploy_realm


def founder_key(seed: int):
    from geister.identity import identity

    return identity(seed, "founder")


def network_url(m) -> str:
    return json.loads((m.casals_home / "gaas" / "gaas.local.json").read_text())["network_url"]


def port(m) -> str:
    return network_url(m).rsplit(":", 1)[-1]


def transport(m, record: dict, keys: dict):
    """Calls to the realm signed with ``keys`` (principal -> identity), through the network's gateway."""
    from geister.transport import AgentTransport

    return AgentTransport(network_url(m), record["backend"], keys.__getitem__)


def as_founder(m, record: dict):
    """``call(op, args, read=False)`` signed by the realm's founder."""
    realm = transport(m, record, {record["founder"]: founder_key(record["seed"])})
    return lambda op, args=None, read=False: realm.call(record["founder"], op, args or {}, read=read)


def publish_packages(m) -> None:
    """The checkout's extension and codex packages into the platform's file registry, where the realms install from:
    a version published already is kept, a new one is added."""
    dr = platform(m)
    registry = dr.bindings(dr.conductor_backend())["file-registry"]
    m.run("realms", "files", "publish", "--network", network_url(m), "--registry", registry, "--identity", m.identity,
          cwd=m.realms_dir)


def published(m) -> dict:
    """{extension or codex id: [versions]} in the platform's file registry."""
    dr = platform(m)
    found: dict = {}
    for n in dr.call_json(dr.bindings(dr.conductor_backend())["file-registry"], "list_namespaces", query=True):
        hit = re.match(r"ext/([^/]+)/(.+)$", n["namespace"])
        if hit:
            found.setdefault(hit.group(1), []).append(hit.group(2))
    return found


def newest(versions: list):
    numbered = [v for v in versions if re.match(r"\d", v)]
    return max(numbered, key=lambda v: [int(x) for x in re.findall(r"\d+", v)]) if numbered else None


# -- what a realm looks like


def looks(m, name: str) -> dict:
    """The realm's texts, and the folder of its images: from the mundus file, else from the realms-gos demo realm it
    names (``demo``)."""
    e, found = entry(m, name), {}
    if e.get("demo"):
        folder = m.realms_dir / e["demo"]
        manifest = json.loads((folder / "manifest.json").read_text())
        found = {"folder": folder, "manifesto": manifest.get("manifesto", ""),
                 "welcome_message": manifest.get("welcome_message", "")}
    for key in ("manifesto", "welcome_message"):
        if e.get(key):
            found[key] = e[key]
    return found


def branding(folder: Path) -> dict:
    """``logo_url`` and ``background_image_url`` as data URLs, as the wizard uploads them: a large PNG as JPEG."""
    from PIL import Image

    values = {}
    for field, file in (("logo_url", "logo.png"), ("background_image_url", "background.png")):
        path = folder / file
        if not path.exists():
            continue
        data, mime = path.read_bytes(), "image/png"
        if len(data) > IMAGE_MAX:
            smaller = io.BytesIO()
            Image.open(path).convert("RGB").save(smaller, "JPEG", quality=85)
            data, mime = smaller.getvalue(), "image/jpeg"
        values[field] = f"data:{mime};base64,{base64.b64encode(data).decode()}"
    return values


# -- mint


def mint(m, name: str) -> None:
    """A realm through the portal path: ``request_deployment`` signed by its founder, then the installer's job to its
    end. A job can read ``failed`` for a moment and complete afterwards, so a failure counts only once it has lasted."""
    from ic.agent import Agent
    from ic.candid import Types, encode
    from geister.transport import patient_client, reply

    dr, e = platform(m), entry(m, name)
    founder = founder_key(e["seed"])
    b = dr.bindings(dr.conductor_backend())
    registry, installer = b["realm-registry-backend"], b["realm-installer"]
    portal = f"http://{b['realm-registry-frontend']}.localhost:{port(m)}"
    texts = looks(m, name)
    display = e.get("display_name") or name
    manifest = {  # as deploy_realm.py builds it, which is what the wizard sends
        "name": name, "network": "local", "deploy_mode": "install", "deploy_scope": "both", "deploy_version": "main",
        "gos": {"implementation": "realms-gos", "version": "main"},
        "realm": {"name": name, "display_name": display,
                  "manifesto": texts.get("manifesto") or f"Welcome to {display}.",
                  "welcome_message": texts.get("welcome_message") or f"Welcome to {display}!",
                  "open_registration": False, "extensions": []},
        "casals": {"section": "Deployments", "stand": name},
        "federation": {"slug": name, "portal_url": f"{portal}/r/{name}"},
    }
    if e.get("codex"):
        manifest["realm"]["codex"] = {"package": e["codex"]}
    started = time.time()
    raw = Agent(founder, patient_client(network_url(m))).update_raw(
        registry, "request_deployment", encode([{"type": Types.Text, "value": json.dumps(manifest)}]), [Types.Text])
    res = reply(raw)
    if not res.get("success"):
        sys.exit(f"{name}: request_deployment: {res}")
    job_id = res["job_id"]
    print(f"  {name}: job {job_id}, founder {founder.sender().to_str()}", flush=True)
    last, view, failed_since = None, {}, None
    while time.time() - started < MINT_TIMEOUT:
        view = dr.job_status(installer, job_id)
        if view != last:
            print(f"  {name} +{time.time() - started:4.0f}s job {view.get('status')}"
                  + (f" error={view['error']}" if view.get("error") else ""), flush=True)
            last = view
        if view.get("status") in ("completed", "registering"):
            break
        if view.get("status") == "cancelled":
            sys.exit(f"{name}: job cancelled: {view.get('error')}")
        if view.get("status", "").startswith("failed"):
            failed_since = failed_since or time.time()
            if time.time() - failed_since > FAILED_FOR:
                sys.exit(f"{name}: job ended in {view.get('status')}: {view.get('error')}")
        else:
            failed_since = None
        time.sleep(10)
    else:
        sys.exit(f"{name}: job still {view.get('status')} after {MINT_TIMEOUT}s")
    save(m, {"name": name, "display_name": display, "seed": e["seed"], "codex": e.get("codex", ""), "job_id": job_id,
             "founder": founder.sender().to_str(), "backend": view["backend_canister_id"],
             "frontend": view["frontend_canister_id"], "mint_seconds": round(time.time() - started),
             "frontend_url": f"http://{view['frontend_canister_id']}.localhost:{port(m)}",
             "portal_url": f"{portal}/r/{name}"})


# -- launch


def install_extensions(m, call, codex: str, extra: list) -> list:
    """What the setup wizard installs first at launch: the core extensions, the codex's dependencies and the realm's
    own extras, each at its newest version in the platform file registry; an installed one older than that is
    upgraded. A realm's pages are extensions, the home screen included."""
    wanted = list(json.loads((m.realms_dir / "core-extensions.json").read_text())["core_extensions"])
    manifest = m.realms_dir / "codices" / "codices" / codex / "manifest.json"
    if codex and manifest.exists():
        wanted += json.loads(manifest.read_text()).get("dependencies", [])
    wanted += extra
    have = {x["id"]: x.get("version") for x in call("extension_list", {"count": 500}, read=True).get("extensions", [])}
    available, results = published(m), []
    for ext in dict.fromkeys(wanted):
        version = newest(available.get(ext, []))
        if ext in have and (not version or newest([have[ext] or "0", version]) == have[ext]):
            continue  # installed, and no newer version is published
        if not version:
            results.append({"extension": ext, "ok": False, "error": "not published in the file registry"})
            continue
        out = call("extension_install", {"extension": ext, "version": version})
        ok = bool(out.get("ok")) and not out.get("requires_vote")
        results.append({"extension": ext, "version": version, "ok": ok,
                        "error": None if ok else out.get("error") or f"filed as proposal {out.get('proposal')}"})
    for r in results:
        print(f"    extension {r['extension']} {r.get('version', '')}: {'ok' if r['ok'] else r['error']}", flush=True)
    return results


def currency(m, name: str) -> str:
    """The symbol realm ``name`` keeps its accounts in: the mundus file's ``currency``, else the one its codex bills in
    (its manifest), else GST."""
    if entry(m, name).get("currency"):
        return entry(m, name)["currency"]
    codex = load(m, name)["codex"]
    manifest = m.realms_dir / "codices" / "codices" / codex / "manifest.json"
    symbol = json.loads(manifest.read_text()).get("currency", {}).get("symbol") if codex and manifest.exists() else None
    return symbol or DEFAULT_CURRENCY


BANK_YAML = """\
# Written by realms mundus: one test ledger per realm (ic-python-token in test mode, where anyone mints).
networks:
  - name: local
    mode: connected
    url: {url}
    root-key: fetch

environments:
  - name: local
    network: local

canisters:
{canisters}"""
LEDGER_YAML = """\
  - name: ledger_{realm}
    recipe:
      type: "@dfinity/prebuilt@v2.0.0"
      configuration:
        path: ledger.wasm
    init_args: '(record {{ name = "{symbol} of {realm}"; symbol = "{symbol}"; decimals = 8 : nat8; total_supply = 0 : nat; fee = 10_000 : nat; test = opt true }})'
"""


def token_ledger(m, name: str) -> str:
    """The realm's own token ledger, made on first use. The product orchestra's ledger is not in test mode, so nobody
    but its owner can mint there, and a simulated member needs an income."""
    sys.path.insert(0, str(m.realms_dir / "scripts"))
    import token_ledger as pinned  # the release of the ledger and its checksum

    bank = m.state / "bank"
    ids_file = bank / "ledgers.json"
    ids = json.loads(ids_file.read_text()) if ids_file.exists() else {}
    if name in ids:
        return ids[name]
    bank.mkdir(exist_ok=True)
    source = m.realms_dir / pinned.LEDGER_WASM
    pinned.fetch_ledger_wasm(source)  # downloads it once, and checks its sha256
    shutil.copy(source, bank / "ledger.wasm")
    (bank / "icp.yaml").write_text(BANK_YAML.format(url=network_url(m), canisters="".join(
        LEDGER_YAML.format(realm=r, symbol=currency(m, r)) for r in sorted(set(ids) | {name}))))

    def icp(*args):
        out = subprocess.run(["icp", *args, "-e", "local", "--identity", m.identity], cwd=bank,
                             capture_output=True, text=True, timeout=900)
        if out.returncode:
            sys.exit(f"icp {' '.join(args)}: {(out.stderr or out.stdout).strip()[-600:]}")
        return out.stdout

    created = icp("canister", "create", f"ledger_{name}", "-q").strip().splitlines()[-1].strip()
    icp("deploy", f"ledger_{name}", "--yes")
    ids[name] = created
    ids_file.write_text(json.dumps(ids, indent=1) + "\n")
    return created


def launch(m, name: str) -> bool:
    """What the founder does in the wizard: trust policy, tokens, extensions, branding, then launch; then the short
    voting window a live network needs, the sign-in the mundus file asks for, and two invite codes."""
    record, e = load(m, name), entry(m, name)
    call, steps = as_founder(m, record), []

    def do(op, read=False, **args):
        out = call(op, args, read=read)
        steps.append({"op": op, "args": {k: v for k, v in args.items() if not str(v).startswith("data:")},
                      "ok": bool(out.get("ok")), "error": out.get("error")})
        print(f"    {name} {op} {args.get('step', '')}: {'ok' if out.get('ok') else out.get('error')}", flush=True)
        return out

    todo = do("setup_state").get("todo") or []
    if "trust_policy" in todo:
        do("setup_step", step="trust_policy", require_marketplace_approval=False)
    symbol = currency(m, name)
    if "tokens" in todo:
        do("setup_step", step="tokens", accounting_currency=symbol, accounting_currency_decimals=8)
    # A codex bills in its own token (dominion: DOM): unregistered, its hooks fail ("unknown Token 'DOM'").
    do("token_register", symbol=symbol, ledger=token_ledger(m, name), decimals=8, fee=10_000)
    record["extensions"] = install_extensions(m, call, record["codex"], e.get("extensions", []))
    texts = looks(m, name)
    if texts.get("folder"):
        do("setup_step", step="branding", **branding(texts["folder"]))
    # A week of voting cannot pass on a live network: the founder shortens it while configuration is not governed.
    do("governance_update", voting_window=m.spec.get("voting_window", 180))
    if m.spec.get("test_sign_in", True):
        do("realm_configure", test_mode=True, test_flags=TEST_FLAGS)
    out = do("setup_launch")
    invites = {}
    for profile, uses in (("member", 50), ("admin", 5)):
        made = do("invite_create", profile=profile, code=f"{name}-{profile}", max_uses=uses)
        invites[profile] = (made.get("invite") or {}).get("code", "")
    status = call("status", read=True)
    record.update(launch_steps=steps, launched=bool(out.get("ok")), status=status.get("status"),
                  browser_invites=invites)
    save(m, record)
    print(f"    {name} status: {status.get('status')}")
    return bool(out.get("ok"))


def mint_and_launch(m) -> bool:
    """Every realm of the mundus file this network does not have yet: minted, then launched, one after the other. Two
    jobs provisioned together took longer than two in a row, and one read ``failed`` on its way."""
    dr = platform(m)
    live, ok = set(dr.bindings(dr.conductor_backend()).values()), True
    for r in m.spec["realms"]:
        name = r["name"]
        if not (record_path(m, name).exists() and load(m, name)["backend"] in live):
            mint(m, name)
        record = load(m, name)
        if record.get("launched"):
            print(f"  {name}: launched already; its extensions brought up to date")
            install_extensions(m, as_founder(m, record), record["codex"], entry(m, name).get("extensions", []))
            continue
        ok = launch(m, name) and ok
    return ok


# -- how people sign in


def sign_in(m, test: bool) -> None:
    """The picker of test identities, or this network's Internet Identity, on the portal and on every realm. The
    realms are set by their founders; the portal's flags only through the GaaS multisig, whose one signer on a local
    network is the mundus identity."""
    sys.path.insert(0, str(m.realms_dir / "scripts"))
    import e2e_gaas_scale as portal

    for record in records(m):
        out = as_founder(m, record)("realm_configure", {"test_mode": test, "test_flags": {k: test for k in TEST_FLAGS}})
        print(f"  {record['name']}: {'ok' if out.get('ok') and not out.get('requires_vote') else out}")
    dr = platform(m)
    b = dr.bindings(dr.conductor_backend())
    portal.through_multisig(Path(dr.PROJECT_DIR), m.identity, b["multisig"], b["realm-registry-backend"],
                            "set_canister_config_json", {"network": "local", "test_flags": {
                                **portal.FLAGS, "test_mode": test, "ii_bypass": test}})
    print(f"  portal: test sign-in {'on' if test else 'off'}")


# -- the swarm


def swarm_config(m) -> Path:
    """``swarm.yaml`` for ``geister swarm``: this mundus' realms and the people the mundus file gives each."""
    import yaml

    versions = published(m)
    path = m.state / "swarm.yaml"
    config = {"url": network_url(m), "day_seconds": m.spec.get("day_seconds", 60),
              "realms": [{"name": r["name"], "canister": r["backend"], "seed": r["seed"],
                          "population": entry(m, r["name"]).get("population", {}),
                          "codex_versions": sorted(versions.get(r["codex"], []))}
                         for r in records(m) if entry(m, r["name"]).get("population")]}
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    return path


# -- where to look


def urls(m) -> str:
    import requests

    dr = platform(m)
    b = dr.bindings(dr.conductor_backend())
    try:
        has_ii = requests.get(m.ii_url() + "/", timeout=10).status_code == 200
    except requests.RequestException:
        has_ii = False
    lines = [f"Network            {network_url(m)}",
             f"Realm registry     http://{b['realm-registry-frontend']}.localhost:{port(m)}/   (the GaaS portal)",
             f"Internet Identity  {m.ii_url()}" + ("" if has_ii else "   NOT on this network"), ""]
    join = "/join?ti=0&test_mode=1&skip_ii=1   (ti=0 is test Identity 1)" if m.spec.get("test_sign_in", True) else "/join"
    for r in records(m):
        codes = r.get("browser_invites") or {}
        layer = entry(m, r["name"]).get("layer")
        lines += [f"{r['display_name']} ({r['name']}: {layer or r['codex'] or 'no codex'}, {r.get('status', 'not launched')})",
                  f"  open     {r['frontend_url']}/",
                  f"  join     {r['frontend_url']}{join}",
                  f"  invites  member {codes.get('member', '-')}   admin {codes.get('admin', '-')}",
                  f"  backend  {r['backend']}   founder {r['founder']}"]
        for q in r.get("quarters", []):
            lines.append(f"  quarter  {q['name']}  http://{q['frontend']}.localhost:{port(m)}/   backend {q['backend']}"
                         if q.get("frontend") else f"  quarter  {q['name']}  backend {q['backend']}")
        lines.append("")
    return "\n".join(lines)
