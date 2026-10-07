"""``realms domains`` — custom domains for a Casals sheet.

A sheet's ``domains`` block names hosts and the frontend canister behind each
(``{"host": "$env.marketplace_host", "canister": "marketplace-frontend"}``);
``environments.<env>.dns`` names the provider and zone. ``casals up`` leaves
that block alone ("dns provider not implemented in CLI"): the product CLI owns
it, for this sheet and for the GaaS portal's alike.

``check``  — read-only: what DNS, the IC registration and HTTP say vs the sheet.
``apply``  — put the three records in Cloudflare, register (or re-point) the
             domain with the IC gateways, wait for it, verify over HTTP.

Canister ids are never read from a table in this repo. They come from the
output of ``casals export`` (``--export FILE``) or, failing that, from the
conductor itself (``export_sheet`` query, as the operator), located through the ``CASALS_HOME``
bindings file that ``casals up`` writes.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

import requests
import typer
from rich.console import Console
from rich.table import Table

from realms.cli import domain_reg
from realms.cli.cloudflare import CloudflareError, apply_records, token_from_env
from realms.cli.dns import ICP_ACME_GATEWAY, DnsRecord, render_dns_records

console = Console()

_DNS_PROVIDERS = ("none", "manual", "cloudflare")
DOH = "https://cloudflare-dns.com/dns-query"
HTTP_TIMEOUT = 30.0


# ── sheet ────────────────────────────────────────────────────────────────────


def _dns_settings(env_cfg: dict) -> tuple[str, str, str, int]:
    """(provider, zone, token_env, ttl) from the environment's optional ``dns`` block.

    ``none``/``manual`` both mean: render and check, never write."""
    dns = env_cfg.get("dns") or {}
    if not isinstance(dns, dict):
        dns = {}
    provider = (dns.get("provider") or "manual").strip().lower()
    if provider not in _DNS_PROVIDERS:
        raise ValueError(
            f"dns.provider must be one of {', '.join(_DNS_PROVIDERS)}, got {dns.get('provider')!r}"
        )
    zone = (dns.get("zone") or "").strip()
    token_env = (dns.get("token_env") or "CLOUDFLARE_API_TOKEN").strip()
    ttl_raw = dns.get("ttl", 60)
    try:
        ttl = int(ttl_raw)
    except (TypeError, ValueError):
        raise ValueError(f"dns.ttl must be an integer, got {ttl_raw!r}")
    if ttl != 1 and ttl < 60:
        raise ValueError("dns.ttl must be 1 (automatic) or at least 60 seconds")
    return provider, zone, token_env, ttl


def _check_dns_credentials(env_cfg: dict, host: str) -> None:
    """Fail before touching anything when the provider needs a token we lack."""
    provider, _zone, token_env, _ttl = _dns_settings(env_cfg)
    if provider != "cloudflare":
        return
    if not token_from_env(token_env):
        raise RuntimeError(
            f"{token_env} is not set; {host} is on Cloudflare and needs a token with "
            f"Zone:Read + DNS:Edit on its zone (export it in this shell, never commit it)"
        )


def _resolve_env_ref(value: str, env_cfg: dict) -> str:
    """``$env.marketplace_host`` → ``environments.<env>.marketplace_host``."""
    value = (value or "").strip()
    if value.startswith("$env."):
        key = value[len("$env."):]
        resolved = env_cfg.get(key)
        if not isinstance(resolved, str):
            raise ValueError(f"{value}: environments.<env>.{key} is missing or not a string")
        return resolved.strip()
    return value


@dataclass
class DomainTarget:
    host: str
    canister_name: str
    canister_id: str = ""


def load_sheet(path: Path) -> dict:
    try:
        sheet = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read sheet {path}: {exc}") from exc
    if not isinstance(sheet, dict) or "environments" not in sheet:
        raise ValueError(f"{path} is not a Casals sheet (no environments block)")
    return sheet


def env_block(sheet: dict, env: str) -> dict:
    envs = sheet.get("environments") or {}
    if env not in envs:
        raise ValueError(f"environment {env!r} is not in the sheet (have: {', '.join(sorted(envs))})")
    return envs[env] or {}


def domain_targets(sheet: dict, env: str) -> list[DomainTarget]:
    """Every ``domains[]`` row with its host resolved; rows whose host resolves
    to '' (an environment without a custom domain) are dropped."""
    env_cfg = env_block(sheet, env)
    out: list[DomainTarget] = []
    for row in sheet.get("domains") or []:
        if not isinstance(row, dict):
            continue
        host = _resolve_env_ref(str(row.get("host") or ""), env_cfg).rstrip(".").lower()
        name = str(row.get("canister") or "").strip()
        if not name:
            raise ValueError(f"domains[]: row {row!r} has no canister")
        if host:
            out.append(DomainTarget(host=host, canister_name=name))
    return out


# ── canister ids ─────────────────────────────────────────────────────────────


def _bindings_from_export(payload: dict) -> dict[str, str]:
    bindings = payload.get("bindings") if isinstance(payload, dict) else None
    if not isinstance(bindings, dict):
        raise ValueError("export has no bindings (expected the JSON `casals export` prints)")
    return {str(k): str(v) for k, v in bindings.items() if v}


def _casals_home() -> Path:
    return Path(os.environ.get("CASALS_HOME") or (Path.home() / ".casals"))


def conductor_from_casals_home(sheet_name: str, env: str) -> str:
    """The conductor id from ``$CASALS_HOME/<sheet>.<env>.json`` (written by ``casals up``)."""
    path = _casals_home() / f"{sheet_name}.{env}.json"
    if not path.is_file():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(data.get("backend_id") or (data.get("conductor") or {}).get("casals-backend") or "")


@contextlib.contextmanager
def _hsm_pin_file() -> Iterator[Optional[str]]:
    """``--identity-password-file`` for a hardware-key identity: ``ICP_IDENTITY_PASSWORD_FILE``
    as-is, else a 0600 file written from ``DFX_HSM_PIN`` and removed after the call."""
    explicit = (os.environ.get("ICP_IDENTITY_PASSWORD_FILE") or "").strip()
    if explicit:
        yield explicit
        return
    pin = os.environ.get("DFX_HSM_PIN") or ""
    if not pin:
        yield None
        return
    fd, path = tempfile.mkstemp(prefix="realms-hsm-pin-")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(pin)
        yield path
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def export_from_conductor(conductor_id: str, network: str, identity: str = "") -> dict:
    """``export_sheet`` straight from the conductor — the same JSON ``casals export`` prints.

    A conductor answers only its commanders and controllers unless its sheet sets
    ``public_read``, so this runs as the selected icp identity (the operator) unless
    ``identity`` names another. A hardware-key identity needs ``DFX_HSM_PIN``."""
    with tempfile.NamedTemporaryFile("w", suffix=".candid", delete=False) as fh:
        fh.write("()")
        args_file = fh.name
    try:
        cmd = ["icp", "canister", "call", conductor_id, "export_sheet", "--query", "--json",
               "--args-file", args_file, "--args-format", "candid"]
        cmd += ["-n", "ic"] if network == "ic" else ["-e", network]
        if identity:
            cmd += ["--identity", identity]
        with contextlib.ExitStack() as stack:
            pin_file = stack.enter_context(_hsm_pin_file()) if identity != "anonymous" else None
            if pin_file:
                cmd += ["--identity-password-file", pin_file]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    finally:
        os.unlink(args_file)
    if proc.returncode != 0:
        raise RuntimeError(f"export_sheet on {conductor_id} failed: {(proc.stderr or proc.stdout).strip()[:400]}")
    try:
        raw = bytes.fromhex(json.loads(proc.stdout.strip().splitlines()[-1])["response_bytes"])
        payload = json.loads(candid_single_text(raw))
    except (ValueError, KeyError, IndexError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"export_sheet on {conductor_id} returned something that is not JSON: {exc}") from exc
    if isinstance(payload, dict) and payload.get("ok") is False:
        who = identity or "the selected icp identity"
        raise RuntimeError(
            f"export_sheet on {conductor_id} refused {who}: {payload.get('error')} — "
            "run as the operator (or a commander), or pass --export <casals export JSON>"
        )
    return payload


def candid_single_text(raw: bytes) -> str:
    """Decode a candid message carrying exactly one ``text`` value:
    ``DIDL`` · empty type table · one arg of type text (0x71) · leb128 length · utf-8."""
    if raw[:4] != b"DIDL":
        raise ValueError("not a candid message")
    pos = 4

    def leb(p: int) -> tuple[int, int]:
        value, shift = 0, 0
        while True:
            byte = raw[p]
            value |= (byte & 0x7F) << shift
            p += 1
            if not byte & 0x80:
                return value, p
            shift += 7

    n_types, pos = leb(pos)
    if n_types != 0:
        raise ValueError("expected a primitive-only candid message")
    n_args, pos = leb(pos)
    if n_args != 1 or raw[pos] != 0x71:
        raise ValueError("expected exactly one text argument")
    pos += 1
    length, pos = leb(pos)
    return raw[pos: pos + length].decode("utf-8")


def resolve_canister_ids(
    targets: list[DomainTarget],
    *,
    sheet: dict,
    env: str,
    export_path: Optional[Path],
    conductor: str,
    overrides: dict[str, str],
    identity: str = "",
) -> None:
    """Fill ``canister_id`` on every target, in this order: ``--canister``
    overrides, ``--export`` file, then the live conductor (``--conductor`` or
    the CASALS_HOME bindings file)."""
    missing = [t for t in targets if not overrides.get(t.canister_name)]
    for t in targets:
        if t.canister_name in overrides:
            t.canister_id = overrides[t.canister_name]
    if not missing:
        return
    bindings: dict[str, str] = {}
    if export_path is not None:
        bindings = _bindings_from_export(json.loads(export_path.read_text(encoding="utf-8")))
    else:
        network = str(env_block(sheet, env).get("network") or "ic")
        cid = conductor or conductor_from_casals_home(str(sheet.get("name") or ""), env)
        if not cid:
            raise RuntimeError(
                "no way to resolve canister ids: pass --export <casals export JSON>, "
                "--conductor <id>, or run with CASALS_HOME pointing at the bindings `casals up` wrote"
            )
        bindings = _bindings_from_export(export_from_conductor(cid, network, identity))
    for t in missing:
        cid = bindings.get(t.canister_name, "")
        if not cid:
            raise RuntimeError(
                f"{t.canister_name} (for {t.host}) has no canister id in the bindings — "
                f"has `casals up` converged on {env}?"
            )
        t.canister_id = cid


# ── observation ──────────────────────────────────────────────────────────────


def doh_lookup(name: str, rtype: str, *, session: requests.Session | None = None) -> list[str]:
    """Resolve ``name``/``rtype`` over DNS-over-HTTPS; TXT values come back unquoted."""
    http = session or requests.Session()
    resp = http.get(DOH, params={"name": name, "type": rtype},
                    headers={"accept": "application/dns-json"}, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    answers = resp.json().get("Answer") or []
    wanted = {"TXT": 16, "CNAME": 5, "A": 1, "AAAA": 28}.get(rtype.upper())
    out = []
    for a in answers:
        if wanted is not None and a.get("type") != wanted:
            continue
        data = str(a.get("data") or "").strip()
        if rtype.upper() == "TXT":
            data = data.strip('"')
        out.append(data.rstrip("."))
    return out


def http_probe(url: str, *, session: requests.Session | None = None) -> tuple[int, str]:
    http = session or requests.Session()
    try:
        resp = http.get(url, timeout=HTTP_TIMEOUT, allow_redirects=True)
        return resp.status_code, resp.text[:200]
    except requests.RequestException as exc:
        return 0, str(exc)[:200]


@dataclass
class Observation:
    target: DomainTarget
    txt: list[str] = field(default_factory=list)
    acme: list[str] = field(default_factory=list)
    apex: list[str] = field(default_factory=list)
    registration: dict[str, Any] | None = None
    ic_domains: tuple[int, str] = (0, "")
    site: tuple[int, str] = (0, "")

    @property
    def problems(self) -> list[str]:
        t = self.target
        out = []
        if self.txt != [t.canister_id]:
            out.append(f"_canister-id TXT is {self.txt or 'absent'}, want [{t.canister_id}]")
        want_acme = f"_acme-challenge.{t.host}.{ICP_ACME_GATEWAY}"
        if want_acme not in self.acme:
            out.append(f"_acme-challenge CNAME is {self.acme or 'absent'}, want {want_acme}")
        if not self.apex:
            out.append("host has no A/AAAA/CNAME record")
        reg = self.registration or {}
        status = str(reg.get("registration_status") or "").lower()
        if not reg:
            out.append("not registered with the IC gateways")
        elif str(reg.get("canister_id") or "").lower() != t.canister_id.lower():
            out.append(f"IC registration points at {reg.get('canister_id')} ({status})")
        elif status != "registered":
            out.append(f"IC registration is {status}")
        if self.ic_domains[0] != 200 or t.host not in self.ic_domains[1]:
            out.append(f"{t.canister_id}.icp0.io/.well-known/ic-domains does not list {t.host} (HTTP {self.ic_domains[0]})")
        if self.site[0] != 200:
            out.append(f"https://{t.host}/ answers HTTP {self.site[0] or 'error'}")
        return out


def observe(target: DomainTarget, *, session: requests.Session | None = None) -> Observation:
    http = session or requests.Session()
    obs = Observation(target)
    host = target.host
    try:
        obs.txt = doh_lookup(f"_canister-id.{host}", "TXT", session=http)
        obs.acme = doh_lookup(f"_acme-challenge.{host}", "CNAME", session=http)
        obs.apex = (doh_lookup(host, "CNAME", session=http) or doh_lookup(host, "A", session=http)
                    or doh_lookup(host, "AAAA", session=http))
    except requests.RequestException as exc:
        obs.txt = [f"lookup failed: {exc}"]
    try:
        obs.registration = domain_reg.registration_status(host, session=http)
    except (domain_reg.DomainRegistrationError, requests.RequestException) as exc:
        obs.registration = {"registration_status": f"lookup failed: {exc}"}
    obs.ic_domains = http_probe(f"https://{target.canister_id}.icp0.io/.well-known/ic-domains", session=http)
    obs.site = http_probe(f"https://{host}/", session=http)
    return obs


def print_observations(observations: list[Observation]) -> None:
    table = Table(title="Custom domains")
    table.add_column("host")
    table.add_column("canister")
    table.add_column("_canister-id TXT")
    table.add_column("IC registration")
    table.add_column("https://host/")
    for obs in observations:
        t = obs.target
        reg = obs.registration or {}
        reg_txt = (f"{reg.get('registration_status', '?')} → {reg.get('canister_id', '?')}" if reg else "none")
        ok = not obs.problems
        table.add_row(
            t.host, f"{t.canister_name}\n{t.canister_id}",
            ", ".join(obs.txt) or "absent", reg_txt,
            f"[green]{obs.site[0]}[/green]" if ok else f"[red]{obs.site[0] or 'error'}[/red]",
        )
    console.print(table)
    for obs in observations:
        for p in obs.problems:
            console.print(f"  [red]✗[/red] {obs.target.host}: {p}")
        if not obs.problems:
            console.print(f"  [green]✓[/green] {obs.target.host} → {obs.target.canister_id}")


# ── apply ────────────────────────────────────────────────────────────────────


def print_records(records: list[DnsRecord]) -> None:
    table = Table(title="DNS records the IC gateway needs")
    table.add_column("type")
    table.add_column("host")
    table.add_column("value")
    table.add_column("purpose")
    for r in records:
        table.add_row(r.record_type, r.host, r.value, r.notes)
    console.print(table)


def apply_target(
    target: DomainTarget,
    env_cfg: dict,
    *,
    skip_registration: bool,
    registration_timeout: float,
    session: requests.Session | None = None,
) -> bool:
    """Records → registration → HTTP for one host. Returns True when it ends healthy."""
    provider, zone, token_env, ttl = _dns_settings(env_cfg)
    records = render_dns_records(target.host, target.canister_id)
    http = session or requests.Session()

    console.print(f"\n[bold]{target.host}[/bold] → {target.canister_name} {target.canister_id}")
    if provider == "cloudflare":
        token = token_from_env(token_env)
        if not token:
            raise RuntimeError(f"{token_env} is not set")
        outcomes = apply_records(records, token=token, zone=zone, domain=target.host, ttl=ttl, session=http)
        for o in outcomes:
            detail = f" ({o.detail})" if o.detail else ""
            console.print(f"  cloudflare {o.action:9} {o.record.record_type:11} {o.record.host} = {o.record.value}{detail}")
    else:
        console.print(f"  dns.provider is {provider!r}: set these records yourself, then rerun")
        print_records(records)

    if skip_registration:
        return True

    console.print("  IC gateway registration:")
    # DNS has to be visible to the gateways before validate passes; a fresh TXT
    # can take a minute even at ttl=60.
    deadline = time.monotonic() + registration_timeout
    while True:
        try:
            validation = domain_reg.validate_domain(target.host, session=http)
        except domain_reg.DomainRegistrationError as exc:
            validation = {"validation_status": "error", "detail": str(exc)}
        seen = str(validation.get("canister_id") or "").lower()
        if seen == target.canister_id.lower():
            break
        if time.monotonic() > deadline:
            console.print(f"  [red]gateway still sees {seen or 'nothing'} for {target.host}: {json.dumps(validation)[:300]}[/red]")
            return False
        console.print(f"    waiting for DNS to reach the gateway (sees {seen or 'nothing'}) …")
        time.sleep(15)

    current = domain_reg.registration_status(target.host, session=http) or {}
    if (str(current.get("canister_id") or "").lower() == target.canister_id.lower()
            and str(current.get("registration_status") or "").lower() == "registered"):
        console.print(f"    already registered → {target.canister_id}")
    else:
        verb = "PATCH (re-point)" if current else "POST (new)"
        console.print(f"    {verb} …")
        # The gateway re-reads the TXT record for the request and some nodes
        # still cache the previous canister for a TTL; that shows up as a 400
        # naming the old canister. Retry while DNS settles.
        while True:
            try:
                domain_reg.register_domain(target.host, session=http)
                break
            except domain_reg.DomainRegistrationError as exc:
                if "HTTP 400" not in str(exc) or time.monotonic() > deadline:
                    raise
                reason = str(exc).split("errors")[-1].strip(': "}')[:120]
                console.print(f"    gateway not ready ({reason}); retrying in 30s …")
                time.sleep(30)
        final = domain_reg.poll_domain_registration(
            target.host, expected_canister=target.canister_id,
            timeout=max(60.0, deadline - time.monotonic()), session=http,
        )
        console.print(f"    {final.get('registration_status')} → {final.get('canister_id')}")

    # The gateways switch over shortly after the registration flips.
    for _ in range(12):
        status, _body = http_probe(f"https://{target.host}/", session=http)
        if status == 200:
            console.print(f"  [green]https://{target.host}/ → 200[/green]")
            return True
        time.sleep(10)
    console.print(f"  [yellow]https://{target.host}/ still answers {status}; the gateways may need a few more minutes[/yellow]")
    return False


# ── typer commands ───────────────────────────────────────────────────────────

domains_app = typer.Typer(name="domains", help="Custom domains of a Casals sheet (Cloudflare + IC gateway registration)")


def _common(sheet_path: Path, env: str, export: Optional[Path], conductor: str, canister: list[str], identity: str):
    sheet = load_sheet(sheet_path)
    env_cfg = env_block(sheet, env)
    targets = domain_targets(sheet, env)
    if not targets:
        console.print(f"[yellow]{sheet_path}: no custom domain on environment {env!r}[/yellow]")
        raise typer.Exit(code=0)
    overrides: dict[str, str] = {}
    for item in canister:
        if "=" not in item:
            raise typer.BadParameter(f"--canister expects name=id, got {item!r}")
        name, cid = item.split("=", 1)
        overrides[name.strip()] = cid.strip()
    resolve_canister_ids(targets, sheet=sheet, env=env, export_path=export, conductor=conductor,
                         overrides=overrides, identity=identity)
    return sheet, env_cfg, targets


_SHEET_ARG = typer.Argument(..., help="Casals sheet (casals.json) with a domains block")
_ENV_OPT = typer.Option("production", "--env", "-e", help="Environment in the sheet")
_EXPORT_OPT = typer.Option(None, "--export", help="JSON printed by `casals export` (name → canister id)")
_CONDUCTOR_OPT = typer.Option("", "--conductor", help="Conductor canister id to read the bindings from (default: CASALS_HOME bindings file)")
_CANISTER_OPT = typer.Option([], "--canister", help="name=id override, repeatable")
_IDENTITY_OPT = typer.Option("", "--identity", help="icp identity for the export_sheet query (default: the selected icp identity)")


@domains_app.command("check")
def domains_check(
    sheet_path: Path = _SHEET_ARG,
    env: str = _ENV_OPT,
    export: Optional[Path] = _EXPORT_OPT,
    conductor: str = _CONDUCTOR_OPT,
    canister: list[str] = _CANISTER_OPT,
    identity: str = _IDENTITY_OPT,
) -> None:
    """Read-only: DNS, IC registration and HTTP for every domain in the sheet. Exit 1 on any mismatch."""
    try:
        _sheet, _env_cfg, targets = _common(sheet_path, env, export, conductor, canister, identity)
        observations = [observe(t) for t in targets]
    except (ValueError, RuntimeError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2)
    print_observations(observations)
    if any(o.problems for o in observations):
        raise typer.Exit(code=1)


@domains_app.command("apply")
def domains_apply(
    sheet_path: Path = _SHEET_ARG,
    env: str = _ENV_OPT,
    export: Optional[Path] = _EXPORT_OPT,
    conductor: str = _CONDUCTOR_OPT,
    canister: list[str] = _CANISTER_OPT,
    identity: str = _IDENTITY_OPT,
    skip_registration: bool = typer.Option(False, "--skip-registration", help="Only set DNS records"),
    registration_timeout: float = typer.Option(900.0, "--timeout", help="Seconds to wait for the IC gateways"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask before writing DNS records"),
) -> None:
    """Point every domain in the sheet at its canister: Cloudflare records, IC gateway registration, HTTP check."""
    try:
        _sheet, env_cfg, targets = _common(sheet_path, env, export, conductor, canister, identity)
        for t in targets:
            _check_dns_credentials(env_cfg, t.host)
    except (ValueError, RuntimeError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2)

    provider, zone, _token_env, _ttl = _dns_settings(env_cfg)
    console.print(f"{len(targets)} domain(s) on {env}; dns.provider={provider}" + (f", zone={zone}" if zone else ""))
    for t in targets:
        console.print(f"  {t.host} → {t.canister_name} {t.canister_id}")
    if provider == "cloudflare" and not yes and not typer.confirm("Write these records to Cloudflare?", default=False):
        raise typer.Exit(code=1)

    healthy = True
    try:
        for t in targets:
            healthy &= apply_target(
                t, env_cfg, skip_registration=skip_registration, registration_timeout=registration_timeout,
            )
    except (CloudflareError, domain_reg.DomainRegistrationError, RuntimeError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2)
    if not healthy:
        raise typer.Exit(code=1)
