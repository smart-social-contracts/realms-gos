"""``realms new`` — live realm create.

``--deploy-mode=gaas`` (default) mirrors the ``*.gos.earth`` wizard: registry
``request_deployment``, poll the installer until it completes on-chain, then
finish founder setup (codex / token / branding / launch). Use ``--from-stage``
to resume after a named stage (same idea as ``gaas new --from-phase``).
No CLI ``enter_setup`` / ``set_canister_config_json``.

``--deploy-mode=standalone`` mints ``realm_backend`` + ``realm_frontend`` from a
Realms GitHub release. No credits, no installer, not listed on any GaaS portal.

Do not confuse with ``realms mundus deploy-new`` (GitHub artifact URLs into an
existing descriptor) or ``realms realm create`` (local ``.realms/`` scaffold).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import tarfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import typer

from .. import release_assets
from ..utils import console, get_project_root
from ..runlog import get_run_log

# ---------------------------------------------------------------------------
# Constants (portal / GOS)
# ---------------------------------------------------------------------------

DEPLOYMENT_COST_CREDITS = 5
ALLOWED_GOS = "realms-gos"
ALLOWED_NETWORKS = ("test", "staging", "demo", "ic")
ALLOWED_DEPLOY_MODES = ("gaas", "standalone")
REALMS_RELEASE_REPO = "smart-social-contracts/realms-gos"
STANDALONE_CREATE_CYCLES = 1_000_000_000_000
PEM_DEPLOY_IDENTITY_NAMES = frozenset({"deployer", "my_dev_identity_1"})
ANONYMOUS_PRINCIPAL = "2vxsx-fae"
_PRINCIPAL_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)+$")

BRANDING_MAX_BYTES = 1_572_864  # ~1.5 MiB (matches core.setup.BRANDING_DATA_URL_MAX_BYTES)
MANIFESTO_MAX_CHARS = 256
WELCOME_MAX_CHARS = 1024
NAME_MIN_CHARS = 3
SLUG_MIN_CHARS = 3

GOS_BACKEND_WASM_KEY = "realm-backend"
GOS_FRONTEND_WASM_KEY = "realm-assets"
GOS_GGG_CONFORMANCE = "1.0"
GOS_LOADER_PROFILE = "realms-iframe-v1"
CASALS_SECTION = "Deployments"

PORTAL_HOSTS = {
    "staging": "https://staging.gos.earth",
    "demo": "https://demo.gos.earth",
    "test": "https://test.gos.earth",
    "ic": "https://realmsgos.org",
    "production": "https://realmsgos.org",
}

# Realm SPA II derivation origin (``icp identity link web --app``).
DERIVATION_ORIGINS = {
    "staging": "https://staging.realmsgos.org",
    "demo": "https://demo.realmsgos.org",
    "test": "https://test.realmsgos.org",
    "ic": "https://realmsgos.org",
    "production": "https://realmsgos.org",
}

# Live GOS queue IDs come from ``--gaas-config`` (``gaas new --output-file``).
# Do not hardcode registry/installer principals here — they change on rebuild.

GEISTER_INSTALL_HINT = (
    "Install geister (do not vendor it into Realms): "
    "pip install geister  "
    "https://github.com/smart-social-contracts/geister"
)
GEISTER_AGENT_PREFIX = "swarm_agent"
# Realm shell extensions every wizard realm needs after launch. No codex
# manifest lists these; sheet realms get them from the Casals arrangements'
# LITE_EXTENSIONS (casals-config/_gen_arrangements.py). This mirrors that set
# minus demo_simulator / hello_world, which are demo-only.
WIZARD_CHROME_EXTENSIONS: Tuple[str, ...] = (
    "public_dashboard",
    "member_dashboard",
    "welcome",
    "realm_settings",
    "extensions_manager",
    "admin_dashboard",
    "codex_viewer",
    "vault",
    "voting",
    "census",
)
# Shared treasury tokens, mirroring the founder setup wizard catalog in
# src/realm_frontend/src/lib/setup/sharedTokens.ts. That wizard pre-selects
# REALMS, so a spec with no token gets REALMS here too.
# Preferred default treasury token when the spec names none: the mundus token,
# whichever symbol the environment gives it.
DEFAULT_TOKEN_SYMBOLS = ("REALMS", "RLM")
# The shared-ledger catalog ({symbol: {ledger, indexer, decimals}}) is the
# realm's: get_setup_state().shared_tokens, handed to it by the installer from
# the environment's casals.json. The CLI carries no per-network table.
SharedTokenCatalog = Dict[str, Dict[str, Any]]

POLL_INTERVAL_S = 10
POLL_TIMEOUT_S = 3600
LAUNCH_POLL_TIMEOUT_S = 1800
CODEX_INSTALL_TIMEOUT_S = 40 * 60

# Ordered pipeline stages. ``--from-stage`` accepts the id or a 1-based index.
GAAS_STAGES: Tuple[Tuple[str, str], ...] = (
    ("validate", "Validate spec and identity"),
    ("credits", "Check registry credits"),
    ("confirm", "Confirm before deploy"),
    ("deploy", "Enqueue installer job"),
    ("poll", "Wait for the installer"),
    ("setup", "Complete founder wizard (codex / token / branding / launch)"),
    ("co-admin", "Register co-admin principal"),
    ("config", "Apply runtime config"),
    ("data", "Import data overlay"),
    ("members", "Join geister members"),
)
STANDALONE_STAGES: Tuple[Tuple[str, str], ...] = (
    ("validate", "Validate spec and identity"),
    ("confirm", "Confirm before deploy"),
    ("deploy", "Mint standalone canisters"),
    ("setup", "Complete founder wizard (codex / token / branding / launch)"),
    ("co-admin", "Register co-admin principal"),
    ("config", "Apply runtime config"),
    ("data", "Import data overlay"),
    ("members", "Join geister members"),
)
POST_DEPLOY_STAGES = frozenset({"setup", "co-admin", "config", "data", "members"})

_IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}


class StageError(Exception):
    """Pipeline failed at a named stage. ``realms new`` prints the stage and exits."""

    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage
        self.message = message


def stages_for_mode(mode: str) -> Tuple[Tuple[str, str], ...]:
    return STANDALONE_STAGES if mode == "standalone" else GAAS_STAGES


def format_stage_catalog(stages: Sequence[Tuple[str, str]]) -> str:
    lines = ["valid --from-stage values:"]
    for index, (stage_id, title) in enumerate(stages, start=1):
        lines.append(f"  {index:>2}  {stage_id}  {title}")
    return "\n".join(lines)


def parse_from_stage(
    from_stage: Optional[str],
    stages: Sequence[Tuple[str, str]],
) -> int:
    """Return a 0-based start index. ``from_stage`` is a 1-based index or stage id."""
    if from_stage is None:
        return 0
    raw = str(from_stage).strip()
    if not raw:
        return 0
    ids = [stage_id for stage_id, _title in stages]
    if raw.isdigit():
        number = int(raw)
        if number < 1 or number > len(stages):
            raise StageError(
                "validate",
                f"--from-stage {raw} is out of range 1..{len(stages)}\n"
                + format_stage_catalog(stages),
            )
        return number - 1
    key = raw.replace("_", "-")
    normalized = [stage_id.replace("_", "-") for stage_id in ids]
    if key in normalized:
        return normalized.index(key)
    raise StageError(
        "validate",
        f"unknown --from-stage {raw!r}\n" + format_stage_catalog(stages),
    )


def stage_ids_to_run(
    stages: Sequence[Tuple[str, str]],
    from_stage: Optional[str],
    *,
    always: Sequence[str] = ("validate",),
) -> set[str]:
    """Stages to execute. ``validate`` always stays, like ``gaas new --from-phase``."""
    start = parse_from_stage(from_stage, stages)
    run_ids = {stage_id for stage_id, _title in stages[start:]}
    for keep in always:
        if any(stage_id == keep for stage_id, _title in stages):
            run_ids.add(keep)
    return run_ids


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested, no replica)
# ---------------------------------------------------------------------------

def slugify(name: str) -> str:
    """Port of gos-as-a-service ``deployment-manifest-core.js`` ``slugify``."""
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "realm").lower())
    slug = slug.strip("-")[:48]
    return slug or "realm"


def normalize_deploy_version(version: Optional[str]) -> str:
    """Port of ``normalizeDeployVersion``: empty/latest → main; strip leading v."""
    v = (version or "").strip()
    if not v or v == "latest":
        return "main"
    if v == "main":
        return "main"
    return v[1:] if v.startswith("v") else v


def portal_url_for_slug(
    slug: str, network: str, *, portal_host: Optional[str] = None
) -> str:
    """Port of ``portalUrlForSlug`` (production host = JS helper's ``ic`` entry)."""
    if portal_host:
        base = portal_host.rstrip("/")
        if not base.startswith("http://") and not base.startswith("https://"):
            base = f"https://{base}"
        return f"{base}/r/{slugify(slug)}"
    base = PORTAL_HOSTS.get((network or "").lower(), PORTAL_HOSTS["staging"])
    return f"{base.rstrip('/')}/r/{slugify(slug)}"


def derivation_origin_for_network(network: str) -> str:
    return DERIVATION_ORIGINS.get((network or "").lower(), DERIVATION_ORIGINS["staging"])


def looks_like_subnet_id(value: str) -> bool:
    text = (value or "").strip()
    if not text or text.lower() in ("automatic", "european", "other"):
        return False
    return bool(re.match(r"^[a-z0-9-]+$", text)) and "-" in text and len(text) >= 20


def parse_subnet(
    spec_subnet: Any,
    flag_subnet: Optional[str],
) -> Dict[str, str]:
    """Merge spec ``subnet`` object with ``--subnet`` flag.

    Flag values: ``automatic`` | ``european`` | ``<subnet-id>``.
    Spec: ``{choice: automatic|european|other, id?: ...}``. Flag wins.
    """
    choice = "automatic"
    subnet_id = ""
    if isinstance(spec_subnet, dict):
        choice = str(spec_subnet.get("choice") or "automatic").strip().lower() or "automatic"
        subnet_id = str(spec_subnet.get("id") or "").strip()
    elif isinstance(spec_subnet, str) and spec_subnet.strip():
        choice = spec_subnet.strip().lower()

    if flag_subnet and flag_subnet.strip():
        raw = flag_subnet.strip()
        lower = raw.lower()
        if lower in ("automatic", "european", "other"):
            choice = lower
            if lower != "other":
                subnet_id = ""
        elif looks_like_subnet_id(raw):
            choice = "other"
            subnet_id = raw
        else:
            raise StageError(
                "validate",
                f"Invalid --subnet '{raw}'. Use automatic, european, or a subnet id.",
            )

    if choice not in ("automatic", "european", "other"):
        raise StageError(
            "validate",
            f"Invalid subnet.choice '{choice}'. Use automatic, european, or other.",
        )
    if choice == "other" and not subnet_id:
        raise StageError(
            "validate",
            "subnet.choice is 'other' but subnet.id is missing. "
            "Pass --subnet <subnet-id> or set subnet.id in the spec.",
        )
    result: Dict[str, str] = {"choice": choice}
    if subnet_id:
        result["id"] = subnet_id
    return result


def build_casals_block(
    realm_name: str,
    deploy_version: str,
    subnet: Dict[str, str],
) -> Dict[str, Any]:
    """Port of ``buildCasalsBlock`` (realms-gos wasm keys only)."""
    version_key = normalize_deploy_version(deploy_version)
    block: Dict[str, Any] = {
        "section": CASALS_SECTION,
        "stand": slugify(realm_name),
        "backend_wasm_key": f"{GOS_BACKEND_WASM_KEY}@{version_key}",
        "frontend_wasm_key": f"{GOS_FRONTEND_WASM_KEY}@{version_key}",
    }
    choice = (subnet.get("choice") or "automatic").lower()
    if choice == "european":
        block["subnet_type"] = "european"
    elif choice == "other":
        subnet_id = (subnet.get("id") or "").strip()
        if subnet_id:
            block["subnet"] = subnet_id
    return block


def build_gos_manifest_block(deploy_version: str) -> Dict[str, Any]:
    """Port of ``buildGosManifestBlock`` for realms-gos only."""
    return {
        "implementation": ALLOWED_GOS,
        "version": normalize_deploy_version(deploy_version),
        "ggg_conformance": GOS_GGG_CONFORMANCE,
        "loader_profile": GOS_LOADER_PROFILE,
    }


def build_portal_manifest(
    *,
    name: str,
    slug: str,
    network: str,
    version: str,
    founder: str,
    subnet: Dict[str, str],
    codex_package: str = "",
    codex_version: Optional[str] = None,
    open_registration: bool = False,
    manifesto: str = "",
    welcome_message: str = "",
    portal_host: Optional[str] = None,
    can_test_mode: Optional[bool] = None,
    test_flags: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Portal wizard manifest (useCasals: true). No GitHub artifact URLs.

    ``can_test_mode`` / ``test_flags`` come from GaaS config (``gaas new``
    descriptor). This function does not invent flags from ``--network``.
    """
    normalized = normalize_deploy_version(version)
    realm_block: Dict[str, Any] = {
        "name": name,
        "display_name": name,
        "manifesto": manifesto or f"Welcome to {name}.",
        "welcome_message": welcome_message or f"Welcome to {name}!",
        "open_registration": bool(open_registration),
        "extensions": list(WIZARD_CHROME_EXTENSIONS),
    }
    package = (codex_package or "").strip()
    if package:
        pkg: Dict[str, Any] = {"name": package}
        version_text = (codex_version or "").strip()
        pkg["version"] = version_text or "latest"
        realm_block["codex"] = {"package": pkg}
    manifest: Dict[str, Any] = {
        "name": name,
        "network": network,
        "deploy_mode": "install",
        "deploy_scope": "both",
        "deploy_version": normalized,
        "gos": build_gos_manifest_block(normalized),
        "realm": realm_block,
        "casals": build_casals_block(name, normalized, subnet),
        "federation": {
            "slug": slug,
            "portal_url": portal_url_for_slug(slug, network, portal_host=portal_host),
        },
        "founder": founder,
    }
    deriv = derivation_origin_for_network(network)
    if deriv:
        manifest["infra"] = {"ii_derivation_origin": deriv}
    if can_test_mode is not None:
        manifest["can_test_mode"] = bool(can_test_mode)
    if test_flags:
        manifest["test_flags"] = {
            str(k): bool(v) for k, v in test_flags.items()
        }
    return manifest


def _normalize_codex_package(
    *,
    cli_codex: Optional[str],
    spec_package: Any,
    cli_version: Optional[str],
    spec_version: Any,
) -> Tuple[str, Optional[str]]:
    """Return ``(package_name, version)``. Spec may use ``{name, version}``."""
    version = cli_version if cli_version is not None else spec_version
    if cli_codex is not None:
        package: Any = cli_codex
    else:
        package = spec_package
    if isinstance(package, dict):
        name = str(package.get("name") or package.get("package") or "").strip()
        if version is None:
            version = package.get("version")
        package = name
    else:
        package = str(package or "").strip()
    if version is not None:
        version = str(version).strip() or None
    return package, version


def _deep_get(data: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    cur: Any = data
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def load_spec_file(path: Optional[str]) -> Tuple[Dict[str, Any], Optional[Path]]:
    """Load spec JSON. Paths inside the spec are relative to the spec file."""
    if not path:
        return {}, None
    spec_path = Path(path).expanduser().resolve()
    if not spec_path.is_file():
        raise StageError("validate", f"Spec file not found: {path}")
    try:
        data = json.loads(spec_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StageError("validate", f"Spec file is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise StageError("validate", "Spec file must be a JSON object")
    return data, spec_path


def resolve_spec_path(value: Optional[str], spec_dir: Optional[Path]) -> Optional[str]:
    if not value:
        return None
    p = Path(value).expanduser()
    if p.is_absolute():
        return str(p)
    base = spec_dir if spec_dir is not None else Path.cwd()
    return str((base / p).resolve())


def merge_spec_and_flags(
    spec: Dict[str, Any],
    *,
    spec_dir: Optional[Path],
    name: Optional[str] = None,
    slug: Optional[str] = None,
    gos: Optional[str] = None,
    version: Optional[str] = None,
    subnet_flag: Optional[str] = None,
    codex: Optional[str] = None,
    codex_version: Optional[str] = None,
    logo: Optional[str] = None,
    background: Optional[str] = None,
    primary: Optional[str] = None,
    manifesto: Optional[str] = None,
    welcome: Optional[str] = None,
    token_symbol: Optional[str] = None,
    token_canister: Optional[str] = None,
    config_path: Optional[str] = None,
    data_path: Optional[str] = None,
    members: Optional[int] = None,
    open_registration: Optional[bool] = None,
    co_admin: Optional[str] = None,
) -> Dict[str, Any]:
    """Merge spec JSON with CLI flags. Flags win. Does not validate yet."""
    branding_spec = spec.get("branding") if isinstance(spec.get("branding"), dict) else {}
    token_spec = spec.get("token") if isinstance(spec.get("token"), dict) else None
    codex_spec = spec.get("codex") if isinstance(spec.get("codex"), dict) else {}
    config_spec = spec.get("config") if isinstance(spec.get("config"), dict) else None

    merged_name = (name if name is not None else spec.get("name") or "") or ""
    merged_name = str(merged_name).strip()
    merged_slug = (slug if slug is not None else spec.get("slug") or "") or ""
    merged_slug = str(merged_slug).strip()
    if not merged_slug and merged_name:
        merged_slug = slugify(merged_name)

    merged_gos = str(gos if gos is not None else spec.get("gos") or ALLOWED_GOS).strip() or ALLOWED_GOS
    merged_version = str(
        version if version is not None else spec.get("version") or "main"
    ).strip() or "main"

    subnet = parse_subnet(spec.get("subnet"), subnet_flag)

    package, cv = _normalize_codex_package(
        cli_codex=codex,
        spec_package=codex_spec.get("package"),
        cli_version=codex_version,
        spec_version=codex_spec.get("version"),
    )

    logo_path = resolve_spec_path(
        logo if logo is not None else branding_spec.get("logo"), spec_dir
    )
    bg_path = resolve_spec_path(
        background if background is not None else branding_spec.get("background"),
        spec_dir,
    )
    primary_color = (
        primary if primary is not None else branding_spec.get("primary") or ""
    ) or ""
    primary_color = str(primary_color).strip()
    merged_manifesto = (
        manifesto
        if manifesto is not None
        else branding_spec.get("manifesto") or spec.get("manifesto") or ""
    ) or ""
    merged_welcome = (
        welcome
        if welcome is not None
        else branding_spec.get("welcome_message")
        or spec.get("welcome_message")
        or ""
    ) or ""

    token: Optional[Dict[str, str]] = None
    if token_spec:
        token = {
            "symbol": str(token_spec.get("symbol") or "").strip(),
            "canister": str(
                token_spec.get("canister")
                or token_spec.get("token_canister_id")
                or ""
            ).strip(),
        }
    if token_symbol or token_canister:
        token = token or {"symbol": "", "canister": ""}
        if token_symbol:
            token["symbol"] = token_symbol.strip()
        if token_canister:
            token["canister"] = token_canister.strip()
    if token and not token.get("symbol") and not token.get("canister"):
        token = None

    config_obj = config_spec
    if config_path:
        cfg_file = Path(resolve_spec_path(config_path, spec_dir) or config_path)
        if not cfg_file.is_file():
            raise StageError("validate", f"Config file not found: {config_path}")
        try:
            loaded = json.loads(cfg_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise StageError("validate", f"Config file is not valid JSON: {exc}") from exc
        if not isinstance(loaded, dict):
            raise StageError("validate", "--config must be a JSON object")
        config_obj = loaded

    data_value: Any = spec.get("data")
    if data_path is not None:
        data_value = resolve_spec_path(data_path, spec_dir) or data_path
    elif isinstance(data_value, str):
        data_value = resolve_spec_path(data_value, spec_dir)

    invite_codes = spec.get("invite_codes")
    if invite_codes is not None and not isinstance(invite_codes, list):
        raise StageError("validate", "spec.invite_codes must be an array of strings")

    merged_members = spec.get("members", 0) if members is None else members
    try:
        merged_members = int(merged_members or 0)
    except (TypeError, ValueError) as exc:
        raise StageError("validate", "--members must be an integer") from exc

    if open_registration is None:
        merged_open = bool(spec.get("open_registration", False))
    else:
        merged_open = bool(open_registration)

    merged_co_admin = (
        co_admin if co_admin is not None else spec.get("co_admin") or ""
    )
    merged_co_admin = str(merged_co_admin or "").strip()

    return {
        "name": merged_name,
        "slug": merged_slug,
        "gos": merged_gos,
        "version": merged_version,
        "subnet": subnet,
        "codex": {"package": package, "version": cv},
        "token": token,
        "branding": {
            "logo": logo_path,
            "background": bg_path,
            "primary": primary_color,
            "manifesto": str(merged_manifesto),
            "welcome_message": str(merged_welcome),
        },
        "config": config_obj,
        "data": data_value,
        "members": merged_members,
        "open_registration": merged_open,
        "invite_codes": [str(c).strip() for c in (invite_codes or []) if str(c).strip()],
        "co_admin": merged_co_admin,
    }


def validate_branding_size(path: str, label: str) -> None:
    file_path = Path(path)
    if not file_path.is_file():
        raise StageError("validate", f"{label} file not found: {path}")
    size = file_path.stat().st_size
    if size > BRANDING_MAX_BYTES:
        raise StageError(
            "validate",
            f"{label} exceeds 1.5 MiB ({size} bytes). Each image must be ≤ 1.5 MiB.",
        )


def check_ic_members_policy(
    network: str,
    members: int,
    open_registration: bool,
    invite_codes: Sequence[str],
) -> Optional[str]:
    """Return an error message if ic + members is refused, else None."""
    if members <= 0:
        return None
    net = (network or "").lower()
    if net != "ic":
        return None
    if open_registration or invite_codes:
        return None
    return (
        "On ic, --members requires --open-registration or spec invite_codes "
        "so geister agents can join_realm."
    )


def validate_merged_spec(
    merged: Dict[str, Any],
    *,
    network: str,
    identity: str,
) -> None:
    errors: List[str] = []
    if not identity or not str(identity).strip():
        errors.append("--identity is required (II-linked identity name)")
    net = (network or "").strip().lower()
    if net not in ALLOWED_NETWORKS:
        errors.append(f"--network must be one of: {', '.join(ALLOWED_NETWORKS)}")
    name = merged.get("name") or ""
    if len(name) < NAME_MIN_CHARS:
        errors.append(f"--name is required and must be at least {NAME_MIN_CHARS} characters")
    slug = merged.get("slug") or ""
    if len(slug) < SLUG_MIN_CHARS:
        errors.append(f"slug must be at least {SLUG_MIN_CHARS} characters (got '{slug}')")
    gos = merged.get("gos") or ALLOWED_GOS
    if gos != ALLOWED_GOS:
        errors.append(f"v1 GOS is {ALLOWED_GOS} only (got '{gos}')")
    package = _deep_get(merged, "codex", "package", default="") or ""
    if not package:
        errors.append("--codex is required (or spec.codex.package)")
    manifesto = _deep_get(merged, "branding", "manifesto", default="") or ""
    if len(manifesto) > MANIFESTO_MAX_CHARS:
        errors.append(f"manifesto exceeds maximum length ({MANIFESTO_MAX_CHARS} chars)")
    welcome = _deep_get(merged, "branding", "welcome_message", default="") or ""
    if len(welcome) > WELCOME_MAX_CHARS:
        errors.append(
            f"welcome_message exceeds maximum length ({WELCOME_MAX_CHARS} chars)"
        )
    primary = _deep_get(merged, "branding", "primary", default="") or ""
    if primary and not re.fullmatch(r"#[0-9A-Fa-f]{6}", primary):
        errors.append("--primary must be a #RRGGBB hex color")
    members = int(merged.get("members") or 0)
    if members < 0:
        errors.append("--members must be ≥ 0")
    ic_err = check_ic_members_policy(
        net, members, bool(merged.get("open_registration")), merged.get("invite_codes") or []
    )
    if ic_err:
        errors.append(ic_err)
    # A token given as a bare symbol is resolved against the realm's own
    # shared-ledger catalog once the realm exists (setup stage).
    if errors:
        raise StageError("validate", "\n".join(errors))

    co_admin = str(merged.get("co_admin") or "").strip()
    if co_admin:
        validate_ic_principal(co_admin, "--co-admin")
    pem_err = pem_deploy_requires_co_admin(identity, co_admin)
    if pem_err:
        raise StageError("validate", pem_err)

    for key, label in (("logo", "logo"), ("background", "background")):
        path = _deep_get(merged, "branding", key, default="")
        if path:
            validate_branding_size(str(path), label)


def validate_ic_principal(principal: str, label: str = "principal") -> None:
    """Reject empty, anonymous, or obviously malformed IC principals."""
    p = (principal or "").strip()
    if not p:
        raise StageError("validate", f"{label} is required")
    if p == ANONYMOUS_PRINCIPAL:
        raise StageError("validate", f"{label} cannot be the anonymous principal")
    if not _PRINCIPAL_RE.fullmatch(p) or len(p) < 20:
        raise StageError(
            "validate",
            f"{label} does not look like an IC principal: {p}",
        )


def pem_deploy_requires_co_admin(identity: str, co_admin: str) -> Optional[str]:
    """Error if a known dfx deploy key is used without --co-admin."""
    if classify_identity(identity) != "pem_deploy":
        return None
    if (co_admin or "").strip():
        return None
    origin = "https://demo.gos.earth"
    return (
        f"--identity '{identity}' is a dfx deploy key. Browser Internet Identity "
        f"login would be a different user.\n"
        f"Pass --co-admin <your-II-principal> (copy it from the browser console "
        f"after logging into {origin} / the realm portal).\n"
        f"After deploy, that principal is registered as a second admin "
        f"(admin profile + root org). {identity} stays the founder User."
    )


def credits_shortfall_message(principal: str, balance: int, network: str) -> str:
    need = DEPLOYMENT_COST_CREDITS
    return (
        f"Insufficient registry credits for founder {principal}: "
        f"balance {balance}, need {need}.\n"
        f"Top up (no auto-top-up):\n"
        f"  realms registry billing add_credits --principal {principal} "
        f"--amount {need} --network {network}"
    )


def assert_credits_sufficient(balance: int, principal: str, network: str) -> None:
    if balance < DEPLOYMENT_COST_CREDITS:
        raise StageError("credits", credits_shortfall_message(principal, balance, network))


def grant_registry_credits(
    registry_id: str,
    principal: str,
    amount: int,
    network: str,
    identity: str,
) -> None:
    """Credit the founder through the registry's admin billing endpoint.

    A registry that was just deployed holds no credits for anybody, so the first
    realm on a fresh environment would always stop for a manual add_credits.
    ``add_credits`` is admin-gated on the registry, so this only succeeds for an
    operator identity; for anyone else the caller falls back to reporting the
    shortfall.
    """
    arg = f'("{principal}", {amount} : nat64, "", "realms new: founder deployment")'
    _canister_call(registry_id, "add_credits", arg, network, identity)


def classify_identity(
    name: str,
    *,
    web_linked: Optional[bool] = None,
    pem_file_exists: bool = False,
) -> str:
    """Classify ``--identity`` as ``web``, ``pem_deploy``, or ``unknown``.

    Only known deploy-key *names* are refused. Cloud agents import an II
    session as a plaintext PEM plus delegation JSON; that still looks like a
    PEM on disk and must be allowed (e.g. ``demo_identity1``).
    """
    del pem_file_exists  # kept for call-site compat; not a refuse signal
    if (name or "").strip() in PEM_DEPLOY_IDENTITY_NAMES:
        return "pem_deploy"
    if web_linked is True:
        return "web"
    return "unknown"


def identity_refuse_message(name: str, network: str) -> str:
    origin = derivation_origin_for_network(network)
    return (
        f"--identity '{name}' looks like an unlinked dfx/PEM deploy key, not an "
        f"Internet Identity. The installer would record that principal as founder, "
        f"and browser II login would be a different user.\n"
        f"Either pass --co-admin <your-II-principal> so that principal is "
        f"registered as a second admin after launch, or link II:\n"
        f"  icp identity link web {name} --app {origin}\n"
        f"Confirm against the realm's /canister_ids.js derivation_origin after deploy."
    )


def file_to_data_url(path: str) -> str:
    file_path = Path(path)
    validate_branding_size(path, file_path.name)
    mime = _IMAGE_MIME.get(file_path.suffix.lower(), "application/octet-stream")
    encoded = base64.b64encode(file_path.read_bytes()).decode("ascii")
    data_url = f"data:{mime};base64,{encoded}"
    if len(data_url.encode("utf-8")) > BRANDING_MAX_BYTES:
        raise StageError(
            "validate",
            f"{file_path.name} data URL exceeds 1.5 MiB after base64 encoding. "
            "Use a smaller image.",
        )
    return data_url


def invite_code_to_checksum(code: str) -> str:
    text = (code or "").strip()
    if re.fullmatch(r"[0-9a-fA-F]{64}", text):
        return text.lower()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def geister_agent_name(index: int) -> str:
    return f"{GEISTER_AGENT_PREFIX}_{index:03d}"


# ---------------------------------------------------------------------------
# Identity / canister I/O
# ---------------------------------------------------------------------------

def _icp_available() -> bool:
    return shutil.which("icp") is not None


def _dfx_env() -> Dict[str, str]:
    env = os.environ.copy()
    env.pop("NO_COLOR", None)
    env.pop("FORCE_COLOR", None)
    env["TERM"] = env.get("TERM") if env.get("TERM") not in (None, "", "dumb") else "xterm"
    env["DFX_WARNING"] = "-mainnet_plaintext_identity"
    return env


def _icp_replica_args() -> List[str]:
    """Cloud/operator hosts have no project icp.yaml — pin the mainnet replica."""
    if Path("icp.yaml").is_file() or Path("/workspace/icp.yaml").is_file():
        return ["--network", "ic"]
    return ["-n", "https://icp0.io", "--root-key", "mainnet"]


def _run(cmd: List[str], *, timeout: int = 120, env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
    log = get_run_log()
    if log is not None:
        log.log_command(cmd)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    if log is not None:
        if result.stdout:
            log.log_output(result.stdout)
        if result.stderr:
            log.log_output(result.stderr)
        if result.returncode != 0:
            log.write(f"exit {result.returncode}")
    return result


def resolve_identity_principal(identity: str) -> str:
    """``icp identity principal --identity NAME`` (dfx fallback)."""
    if _icp_available():
        proc = _run(["icp", "identity", "principal", "--identity", identity], timeout=30)
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip().splitlines()[-1].strip()
    env = _dfx_env()
    proc = _run(
        ["dfx", "identity", "get-principal", "--identity", identity],
        timeout=30,
        env=env,
    )
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip().splitlines()[-1].strip()
    err = (proc.stderr or proc.stdout or "identity not found").strip()
    raise StageError("validate", f"Could not resolve principal for --identity {identity}: {err}")


def _identity_kind_is_web(kind: str) -> bool:
    k = (kind or "").lower()
    return k.startswith("web") or k in ("ii", "internet-identity", "linked")


def _identity_has_delegation(name: str) -> bool:
    """II ``link web`` / ``import --delegation`` writes this JSON next to the key."""
    roots = (
        Path.home() / ".local/share/icp-cli/identity/delegations",
        Path.home() / ".config/icp/identity/delegations",
    )
    return any((root / f"{name}.json").is_file() for root in roots)


def _icp_identity_web_linked(name: str) -> Optional[bool]:
    """Best-effort: True if icp reports a web/II-linked identity, else None.

    A plaintext PEM on disk is *not* evidence of a deploy key — cloud VMs import
    II sessions that way. Only ``classify_identity`` refuses known deploy names.
    """
    if _identity_has_delegation(name):
        return True
    list_paths = [
        Path.home() / ".local/share/icp-cli/identity/identity_list.json",
        Path.home() / ".config/icp/identity_list.json",
    ]
    for path in list_paths:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        entries: Any = data
        if isinstance(data, dict):
            entries = data.get("identities") or data.get("list") or data
        if isinstance(entries, dict) and name in entries:
            info = entries[name]
            if isinstance(info, dict):
                kind = str(info.get("type") or info.get("kind") or info.get("storage") or "")
                if _identity_kind_is_web(kind):
                    return True
        if isinstance(entries, list):
            for item in entries:
                if not isinstance(item, dict):
                    continue
                if str(item.get("name") or "") != name:
                    continue
                kind = str(item.get("type") or item.get("kind") or "")
                if _identity_kind_is_web(kind):
                    return True
    if _icp_available():
        proc = _run(["icp", "identity", "list"], timeout=30)
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                if name in line.split() or line.strip().rstrip("*").strip() == name:
                    lower = line.lower()
                    if "web" in lower or "ii" in lower or "linked" in lower:
                        return True
    return None


def assert_identity_is_ii_linked(
    identity: str, network: str, co_admin: str = ""
) -> str:
    """Refuse known PEM deploy keys unless --co-admin is set. Warn if unconfirmed."""
    web_linked = _icp_identity_web_linked(identity)
    pem_path = Path.home() / ".config/dfx/identity" / identity / "identity.pem"
    kind = classify_identity(
        identity,
        web_linked=web_linked,
        pem_file_exists=pem_path.is_file() and web_linked is not True,
    )
    if kind == "pem_deploy":
        if (co_admin or "").strip():
            console.print(
                f"[yellow]Warning:[/yellow] --identity '{identity}' is a dfx deploy "
                f"key (founder on-chain). Browser II login will use --co-admin "
                f"{co_admin.strip()} as a second admin, not as the founder User."
            )
            return kind
        raise StageError("validate", identity_refuse_message(identity, network))
    if kind == "unknown":
        origin = derivation_origin_for_network(network)
        console.print(
            f"[yellow]Warning:[/yellow] could not confirm --identity '{identity}' is "
            f"II-linked. If this is a dfx PEM key, abort and pass --co-admin "
            f"<II-principal>, or run:\n"
            f"  icp identity link web {identity} --app {origin}"
        )
    return kind


def _candid_text_arg(payload: str) -> str:
    escaped = payload.replace("\\", "\\\\").replace('"', '\\"')
    return f'("{escaped}")'


def _parse_jsonish(stdout: str) -> Any:
    raw = (stdout or "").strip()
    if not raw:
        raise ValueError("empty canister response")
    # icp --json wraps the candid text in a JSON envelope
    try:
        envelope = json.loads(raw)
        if isinstance(envelope, dict) and "response_candid" in envelope:
            candid = envelope["response_candid"]
            if candid is not None:
                return _parse_candid_text(candid)
            # Fall back to response_bytes hex decode if needed
            if "response_bytes" in envelope:
                return envelope
        # dfx --output json for a Candid ``text`` is a JSON string of JSON.
        if isinstance(envelope, str):
            try:
                return json.loads(envelope)
            except json.JSONDecodeError:
                return envelope
        return envelope
    except json.JSONDecodeError:
        pass
    return _parse_candid_text(raw)


def _parse_candid_text(raw: str) -> Any:
    if raw.startswith("(") and raw.endswith(")"):
        raw = raw[1:-1].strip().rstrip(",")
    if raw.startswith('"') and raw.endswith('"'):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = raw[1:-1].replace('\\"', '"').replace("\\n", "\n")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return raw


def parse_credits_balance(payload: Any) -> int:
    """Extract nat64 balance from get_credits JSON or candid-ish dict."""
    data = payload
    if isinstance(data, str):
        # Try JSON first
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            pass
    if isinstance(data, str):
        # Candid text: look for balance = N : nat64 or the field hash for "balance"
        # Field hash 2_329_005_965 is "balance" in candid
        match = re.search(r"2_329_005_965\s*=\s*(\d+)", data)
        if match:
            return int(match.group(1))
        match = re.search(r"balance\s*=\s*(\d+)", data)
        if match:
            return int(match.group(1))
        raise StageError("credits", f"Could not parse get_credits response: {data[:200]}")
    if isinstance(data, dict):
        if "Err" in data:
            err = data["Err"]
            raise StageError("credits", f"get_credits error: {err}")
        inner = data.get("Ok") if "Ok" in data else data
        if isinstance(inner, dict) and "balance" in inner:
            return int(inner["balance"])
        if "balance" in data:
            return int(data["balance"])
    raise StageError("credits", f"Could not parse credit balance from: {payload!r}")


def _canister_call(
    canister: str,
    method: str,
    arg: str,
    network: str,
    identity: str,
    *,
    query: bool = False,
    timeout: int = 180,
) -> Any:
    """Call a canister as ``identity``. Prefer dfx for JSON output; icp fallback."""
    arg_file = None
    try:
        use_file = len(arg.encode("utf-8")) >= 100 * 1024
        # Prefer dfx for clean --output json; icp --json wraps candid in an envelope
        if shutil.which("dfx") is not None:
            cmd = [
                "dfx",
                "canister",
                "call",
                canister,
                method,
                "--network",
                network,
                "--identity",
                identity,
                "--output",
                "json",
            ]
            if query:
                cmd.append("--query")
            if use_file:
                fd, arg_file = tempfile.mkstemp(prefix="realms-new-", suffix=".did")
                with os.fdopen(fd, "w") as fh:
                    fh.write(arg)
                cmd.extend(["--argument-file", arg_file])
            else:
                cmd.append(arg)
            proc = _run(cmd, timeout=timeout, env=_dfx_env())
        elif _icp_available():
            cmd = ["icp", "canister", "call", canister, method]
            cmd.extend(_icp_replica_args())
            cmd.extend(["--identity", identity])
            cmd.append("--json")
            if query:
                cmd.append("--query")
            if use_file:
                fd, arg_file = tempfile.mkstemp(prefix="realms-new-", suffix=".did")
                with os.fdopen(fd, "w") as fh:
                    fh.write(arg)
                cmd.extend(["--args-file", arg_file])
            else:
                cmd.append(arg)
            proc = _run(cmd, timeout=timeout)
        else:
            raise RuntimeError("Neither dfx nor icp found")
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "canister call failed").strip()
            raise RuntimeError(err)
        return _parse_jsonish(proc.stdout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{method} timed out after {timeout}s") from exc
    finally:
        if arg_file:
            try:
                os.unlink(arg_file)
            except OSError:
                pass


def _unwrap_ok_json(raw: Any) -> Dict[str, Any]:
    """Unwrap a Candid ``Result`` / GenericResult JSON envelope to a dict."""
    data = raw
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return {}
    if not isinstance(data, dict):
        return {}
    if "Err" in data and "Ok" not in data:
        raise StageError("setup", f"registry error: {data['Err']}")
    if "Ok" in data:
        inner = data["Ok"]
        if isinstance(inner, str):
            try:
                inner = json.loads(inner)
            except json.JSONDecodeError:
                return {"value": inner}
        if isinstance(inner, dict):
            return inner
        return {}
    return data


def resolve_registered_slug(
    registry_id: str,
    slug: str,
    network: str,
    identity: str,
) -> Dict[str, str]:
    """Look up backend/frontend IDs. ``resolve_slug`` is an UPDATE — do not query."""
    raw = _canister_call(
        registry_id,
        "resolve_slug",
        f'("{slug}")',
        network,
        identity,
        query=False,
        timeout=120,
    )
    payload = _unwrap_ok_json(raw)
    if payload.get("success") is False:
        raise StageError(
            "setup",
            payload.get("error") or f"Unknown slug {slug!r}",
        )
    backend = str(
        payload.get("backend_canister_id") or payload.get("realm_id") or ""
    ).strip()
    frontend = str(payload.get("frontend_canister_id") or "").strip()
    if not backend:
        raise StageError(
            "setup",
            f"resolve_slug({slug}) returned no backend_canister_id",
        )
    return {
        "backend_canister_id": backend,
        "frontend_canister_id": frontend,
        "portal_url": str(payload.get("portal_url") or ""),
    }


def query_credit_balance(registry_id: str, principal: str, network: str, identity: str) -> int:
    payload = _canister_call(
        registry_id,
        "get_credits",
        f'("{principal}")',
        network,
        identity,
        query=True,
        timeout=60,
    )
    return parse_credits_balance(payload)


# ---------------------------------------------------------------------------
# Pipeline stages
# ---------------------------------------------------------------------------

def _fail(stage: str, message: str) -> None:
    raise StageError(stage, message)


def _job_fields(info: Any) -> Dict[str, Any]:
    if not isinstance(info, dict):
        return {}
    if "Ok" in info and isinstance(info["Ok"], dict):
        return info["Ok"]
    if info.get("success") is False:
        return info
    return info


INSTALLER_ACCESS_DENIED_HINT = (
    "The live installer must be an IC controller of the new realm canisters "
    "(the stand_template controllers in casals.json). The realm carries no "
    "installer allowlist, so there is nothing to bake into a new realm-backend "
    "WASM. CLI bootstrap (enter_setup / set_canister_config_json) "
    "is not used on the GaaS path."
)


def _format_installer_job_failure(
    info: Dict[str, Any], *, installer_id: str = ""
) -> str:
    err = str(info.get("error") or info.get("status") or "installer job failed").strip()
    low = err.lower()
    if "accessdenied" in low or "access denied" in low:
        hint = INSTALLER_ACCESS_DENIED_HINT
        if installer_id:
            hint = f"{hint} Installer: {installer_id}."
        return f"{err}\n{hint}"
    return err


def poll_installer_until_ready(
    installer_id: str,
    job_id: str,
    network: str,
    identity: str,
    *,
    timeout_s: int = POLL_TIMEOUT_S,
) -> Dict[str, Any]:
    """Poll until the installer job reaches ``completed`` (or a terminal failure)."""
    start = time.time()
    last_status = ""
    while time.time() - start < timeout_s:
        try:
            raw = _canister_call(
                installer_id,
                "get_deployment_job_status",
                f'("{job_id}")',
                network,
                identity,
                query=True,
                timeout=60,
            )
        except Exception as exc:
            console.print(f"[dim]  poll error: {exc}[/dim]")
            time.sleep(POLL_INTERVAL_S)
            continue
        info = _job_fields(raw)
        if info.get("success") is False and info.get("error"):
            _fail("poll", _format_installer_job_failure(info, installer_id=installer_id))
        status = str(info.get("status") or "unknown")
        if status != last_status:
            console.print(f"  installer: {status}")
            last_status = status
        if status in ("failed", "failed_verification", "cancelled"):
            _fail(
                "poll",
                _format_installer_job_failure(info, installer_id=installer_id),
            )
        if status == "completed":
            if info.get("error"):
                _fail(
                    "poll",
                    _format_installer_job_failure(info, installer_id=installer_id),
                )
            backend = str(info.get("backend_canister_id") or "").strip()
            frontend = str(info.get("frontend_canister_id") or "").strip()
            if not (backend and frontend):
                _fail(
                    "poll",
                    "installer reported completed but backend/frontend ids are missing",
                )
            return info
        time.sleep(POLL_INTERVAL_S)
    _fail("poll", f"Timed out after {timeout_s}s waiting for installer job {job_id}")
    raise AssertionError("unreachable")


def _as_setup_state(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}


def get_setup_state(
    backend_id: str, network: str, identity: str
) -> Dict[str, Any]:
    raw = _canister_call(
        backend_id,
        "get_setup_state",
        "()",
        network,
        identity,
        query=True,
        timeout=60,
    )
    return _as_setup_state(raw)


def wait_for_enter_setup(
    backend_id: str,
    network: str,
    identity: str,
    *,
    timeout_s: int = 90,
) -> Dict[str, Any]:
    """Poll until the installer recorded a founder. Default ``setup`` is not enough."""
    start = time.time()
    last_err = ""
    last: Dict[str, Any] = {}
    while time.time() - start < timeout_s:
        try:
            state = get_setup_state(backend_id, network, identity)
            last = state
            if state.get("success") and state.get("creator"):
                return state
        except Exception as exc:
            last_err = str(exc)
        time.sleep(POLL_INTERVAL_S)
    if last_err and not last:
        console.print(f"  enter_setup poll: {last_err}")
    return last


def ensure_founder_enter_setup(
    backend_id: str,
    founder: str,
    registry_id: str,
    environment: str,
    network: str,
    identity: str,
    *,
    timeout_s: int = 90,
) -> Dict[str, Any]:
    """Wait for installer enter_setup; if the founder is still missing, call it."""
    state = wait_for_enter_setup(
        backend_id, network, identity, timeout_s=timeout_s
    )
    if state.get("creator"):
        return state
    console.print("  installer did not record founder — calling enter_setup")
    call_enter_setup(
        backend_id, founder, registry_id, environment, network, identity
    )
    state = get_setup_state(backend_id, network, identity)
    if not state.get("creator"):
        _fail(
            "setup",
            f"enter_setup did not record founder on {backend_id}",
        )
    return state


def _canister_id_from_inventory(name: str, network: str) -> str:
    try:
        from .env import _read_canister_ids

        cid = (_read_canister_ids(get_project_root()).get(name) or {}).get(network)
        return str(cid or "").strip()
    except Exception:
        return ""


def apply_bootstrap_canister_config(
    backend_id: str,
    frontend_id: str,
    network: str,
    identity: str,
    *,
    founder: str = "",
    gaas: Optional["GaasConfig"] = None,
) -> None:
    """Write live file_registry / frontend IDs. Installer often cannot (no realm.admin)."""
    file_registry = _canister_id_from_inventory(
        "file_registry", network
    ) or _file_registry_id_or_empty(network)
    marketplace = _canister_id_from_inventory("marketplace_backend", network)
    payload: Dict[str, Any] = {}
    if frontend_id:
        payload["frontend_canister_id"] = frontend_id
    if file_registry:
        payload["file_registry_canister_id"] = file_registry
    if marketplace:
        payload["marketplace_canister_id"] = marketplace
    if gaas is not None:
        if gaas.registry_id:
            payload["realm_registry_canister_id"] = gaas.registry_id
        if gaas.can_test_mode is not None:
            payload["can_test_mode"] = bool(gaas.can_test_mode)
        if gaas.test_flags:
            payload["test_flags"] = dict(gaas.test_flags)
        if gaas.portal_host:
            payload["portal_origin"] = gaas.portal_host
    if founder:
        payload["creator_principal"] = founder
    if not payload.get("file_registry_canister_id"):
        _fail(
            "setup",
            "file_registry_canister_id not configured (missing from "
            "canister_ids.json / installer bootstrap)",
        )
    console.print(
        f"  set_canister_config_json file_registry={payload['file_registry_canister_id']}"
    )
    _setup_json_call(
        backend_id,
        "set_canister_config_json",
        payload,
        network,
        identity,
        timeout=180,
        stage="setup",
    )
    if gaas is not None and gaas.can_test_mode:
        _disable_marketplace_approval_gate(backend_id, network, identity)


def _disable_marketplace_approval_gate(
    backend_id: str, network: str, identity: str
) -> None:
    """GOS file_registry has no ``get_namespace_approval_icc``; setup cannot install."""
    console.print("  require_marketplace_approval=false (registry has no approval API)")
    raw = _setup_json_call(
        backend_id,
        "update_realm_config",
        {"require_marketplace_approval": False},
        network,
        identity,
        timeout=180,
        stage="setup",
    )
    if raw.get("success") is False:
        _fail(
            "setup",
            raw.get("error") or "update_realm_config require_marketplace_approval failed",
        )


def _setup_json_call(
    backend_id: str,
    method: str,
    payload: Any,
    network: str,
    identity: str,
    *,
    query: bool = False,
    timeout: int = 300,
    stage: str = "setup",
) -> Dict[str, Any]:
    if payload is None:
        arg = "()"
    elif isinstance(payload, str) and payload == "()":
        arg = "()"
    else:
        arg = _candid_text_arg(json.dumps(payload))
    raw = _canister_call(
        backend_id, method, arg, network, identity, query=query, timeout=timeout
    )
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            _fail(stage, f"{method} returned non-JSON: {raw[:300]}")
    if not isinstance(raw, dict):
        _fail(stage, f"{method} returned unexpected payload: {raw!r}")
    if raw.get("success") is False:
        _fail(stage, raw.get("error") or f"{method} failed")
    return raw


_CO_ADMIN_SHELL = """
from api.user import user_register
from core.membership import add_department_member, user_in_department
from core.org_policy import ensure_root_org, grant_root_authority_over_local_orgs
from ggg import User
p = {principal!r}
user_register(p, "admin")
root = ensure_root_org()
grant_root_authority_over_local_orgs()
u = User[p]
if u and not user_in_department(u, root):
    add_department_member(root, u)
print("ok")
"""


def _co_admin_method_missing(err: str) -> bool:
    lower = (err or "").lower()
    return "register_co_admin" in lower and (
        "no update method" in lower
        or "has no method" in lower
        or "not found" in lower
        or "unknown method" in lower
        or "ic0536" in lower
    )


def register_co_admin_principal(
    merged: Dict[str, Any],
    backend_id: str,
    network: str,
    identity: str,
) -> None:
    """After launch, register --co-admin as a second admin User. Idempotent."""
    register_admin_principal(
        str(merged.get("co_admin") or "").strip(), backend_id, network, identity
    )


def register_admin_principal(
    principal: str,
    backend_id: str,
    network: str,
    identity: str,
) -> None:
    """Give ``principal`` an admin profile in the root org. Idempotent.

    Prefers ``register_co_admin``. If this realm WASM does not have that
    method yet, falls back to founder ``__shell__`` using existing APIs.
    """
    principal = (principal or "").strip()
    if not principal:
        return
    console.print(f"  register_co_admin {principal}")
    try:
        raw = _canister_call(
            backend_id,
            "register_co_admin",
            f'("{principal}")',
            network,
            identity,
            timeout=180,
        )
    except RuntimeError as exc:
        err = str(exc)
        if not _co_admin_method_missing(err):
            _fail("co-admin", err)
        console.print(
            "  register_co_admin not on this WASM — falling back to __shell__"
        )
        code = _CO_ADMIN_SHELL.format(principal=principal)
        raw = _canister_call(
            backend_id,
            "__shell__",
            _candid_text_arg(code),
            network,
            identity,
            timeout=180,
        )
        text = raw if isinstance(raw, str) else json.dumps(raw)
        if "ok" not in text.lower() and "success" not in text.lower():
            _fail("co-admin", f"__shell__ co-admin failed: {text[:400]}")
        console.print(f"  co-admin {principal} (admin profile + root org, via __shell__)")
        return
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            _fail("co-admin", f"register_co_admin returned non-JSON: {raw[:300]}")
    if not isinstance(raw, dict):
        _fail("co-admin", f"register_co_admin unexpected payload: {raw!r}")
    if raw.get("success") is False:
        _fail("co-admin", raw.get("error") or "register_co_admin failed")
    console.print(f"  co-admin {principal} (admin profile + root org)")


def test_identity_creator_principal() -> str:
    """Index-0 ("Identity 1 (Creator)") principal of the II-bypass roster."""
    path = get_project_root() / "config" / "deterministic-test-identity-principals.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    for entry in data.get("identities") or []:
        if entry.get("index") == 0:
            return str(entry.get("principal") or "").strip()
    return ""


def register_test_mode_admin(
    gaas: Optional["GaasConfig"],
    backend_id: str,
    network: str,
    identity: str,
) -> None:
    """On an II-bypass environment, make Identity 1 an admin of the new realm.

    A browser on such an environment is auto-logged-in as the deterministic
    Identity 1, while a CLI deploy's founder is the deploy key — a principal no
    browser can ever present. Without this, a realm created by `realms new` has
    no admin anyone can reach through the UI.
    """
    if gaas is None:
        return
    bypass = bool(gaas.test_flags.get("ii_bypass")) or bool(
        gaas.test_flags.get("test_mode")
    )
    if not (gaas.can_test_mode and bypass):
        return
    principal = test_identity_creator_principal()
    if not principal:
        console.print(
            "[yellow]⚠️  II-bypass env but no Identity 1 principal in "
            "config/deterministic-test-identity-principals.json[/yellow]"
        )
        return
    console.print("  test-mode env: granting Identity 1 admin (browser login)")
    register_admin_principal(principal, backend_id, network, identity)


def _local_manifest_dependencies(package: str) -> List[str]:
    """Post-order dependency ids from local extension/codex manifests (package last omitted)."""
    root = get_project_root()
    search = [
        root / "codices" / "codices",
        root / "extensions" / "extensions",
    ]
    ordered: List[str] = []
    seen: set[str] = set()

    def walk(name: str) -> None:
        key = (name or "").strip()
        if not key or key in seen:
            return
        seen.add(key)
        manifest: Dict[str, Any] = {}
        for folder in search:
            path = folder / key / "manifest.json"
            if path.is_file():
                try:
                    loaded = json.loads(path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    loaded = {}
                if isinstance(loaded, dict):
                    manifest = loaded
                break
        for dep in manifest.get("dependencies") or []:
            if isinstance(dep, str):
                walk(dep)
            elif isinstance(dep, dict):
                walk(str(dep.get("id") or dep.get("name") or ""))
        ordered.append(key)

    walk(package)
    return ordered


def _list_runtime_extension_ids(
    backend_id: str, network: str, identity: str
) -> set[str]:
    try:
        raw = _canister_call(
            backend_id,
            "list_runtime_extensions",
            "()",
            network,
            identity,
            query=True,
            timeout=60,
        )
    except Exception:
        return set()
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return set()
    if not isinstance(raw, dict):
        return set()
    ids = raw.get("runtime_extensions") or raw.get("extensions") or []
    return {str(x).strip() for x in ids if str(x).strip()}


def _already_installed(err: str) -> bool:
    low = (err or "").lower()
    return "already" in low and ("install" in low or "exist" in low)


def preinstall_codex_dependencies(
    backend_id: str,
    package: str,
    network: str,
    identity: str,
) -> None:
    """Install extension deps one-by-one so setup_install_codex stays under the ingress window."""
    deps = _local_manifest_dependencies(package)
    for chrome in WIZARD_CHROME_EXTENSIONS:
        if chrome not in deps:
            deps.append(chrome)
    if not deps:
        return
    installed = _list_runtime_extension_ids(backend_id, network, identity)
    pending = [name for name in deps if name not in installed]
    skipped = len(deps) - len(pending)
    if skipped:
        console.print(f"  already installed: {skipped} of {len(deps)}")
    if not pending:
        return
    registry_id = _canister_id_from_inventory(
        "file_registry", network
    ) or _file_registry_id_or_empty(network)
    if not registry_id:
        _fail("setup", "cannot preinstall codex dependencies: no file_registry id")
    console.print(f"  preinstall {len(pending)} remaining packages for {package}")
    for ext_id in pending:
        payload = {
            "registry_canister_id": registry_id,
            "ext_id": ext_id,
            "version": "latest",
        }
        console.print(f"  install_extension_from_registry {ext_id}")
        try:
            _setup_json_call(
                backend_id,
                "install_extension_from_registry",
                payload,
                network,
                identity,
                timeout=300,
                stage="setup",
            )
        except StageError as exc:
            if _already_installed(exc.message):
                console.print(f"  {ext_id} already installed")
                continue
            raise


def catalog_token_symbol(symbol: str, catalog: SharedTokenCatalog) -> Optional[str]:
    """Canonical catalog symbol for a case-insensitive match, or None."""
    wanted = (symbol or "").strip().upper()
    if not wanted:
        return None
    for known in catalog or {}:
        if known.upper() == wanted:
            return known
    return None


def default_catalog_symbol(catalog: SharedTokenCatalog) -> Optional[str]:
    """The mundus token if the catalog has it, else its first entry, else None."""
    for preferred in DEFAULT_TOKEN_SYMBOLS:
        known = catalog_token_symbol(preferred, catalog)
        if known:
            return known
    return next(iter(catalog), None) if catalog else None


def catalog_token_draft(symbol: str, catalog: SharedTokenCatalog) -> Optional[Dict[str, Any]]:
    """Fill ledger / decimals / indexer for a symbol from the realm's catalog."""
    known = catalog_token_symbol(symbol, catalog)
    if not known:
        return None
    entry = catalog.get(known) or {}
    ledger = str(entry.get("ledger") or "").strip()
    if not ledger:
        return None
    draft: Dict[str, Any] = {
        "symbol": known,
        "token_canister_id": ledger,
        "decimals": int(entry.get("decimals", 8)),
    }
    indexer = str(entry.get("indexer") or "").strip()
    if indexer:
        draft["indexer_canister_id"] = indexer
    return draft


def _token_draft(token: Any, catalog: SharedTokenCatalog) -> Optional[Dict[str, Any]]:
    """Normalize spec.token for setup_save_draft, resolving catalog symbols."""
    if not isinstance(token, dict):
        return None
    canister = str(token.get("canister") or token.get("token_canister_id") or "").strip()
    symbol = str(token.get("symbol") or "").strip()
    if not canister:
        resolved = catalog_token_draft(symbol, catalog)
        if resolved:
            return resolved
    out: Dict[str, Any] = {}
    if canister:
        out["token_canister_id"] = canister
    if symbol:
        out["symbol"] = symbol
    return out or None


def _fetch_setup_state(backend_id: str, network: str, identity: str) -> Dict[str, Any]:
    try:
        state = _canister_call(
            backend_id,
            "get_setup_state",
            "()",
            network,
            identity,
            query=True,
            timeout=60,
        )
        if isinstance(state, str):
            state = json.loads(state)
        return state if isinstance(state, dict) else {}
    except Exception:
        return {}


def fetch_shared_token_catalog(backend_id: str, network: str, identity: str) -> SharedTokenCatalog:
    """The realm's shared-ledger catalog (get_setup_state().shared_tokens)."""
    catalog = _fetch_setup_state(backend_id, network, identity).get("shared_tokens")
    return catalog if isinstance(catalog, dict) else {}


def _setup_state_has_token(backend_id: str, network: str, identity: str) -> bool:
    try:
        state = _fetch_setup_state(backend_id, network, identity)
        token = state.get("token")
        if isinstance(token, dict) and (
            token.get("token_canister_id")
            or token.get("symbol")
            or token.get("ledger")
        ):
            return True
    except Exception:
        return False
    return False


def call_setup_install_codex(
    backend_id: str,
    package: str,
    version: Optional[str],
    network: str,
    identity: str,
) -> Dict[str, Any]:
    """Drive the resumable Codex job until ``complete`` / already installed."""
    install_args: Dict[str, Any] = {"package": package}
    if version:
        install_args["version"] = version
    console.print(f"  setup_install_codex {package}")
    start = time.time()
    last = ""
    while time.time() - start < CODEX_INSTALL_TIMEOUT_S:
        raw = _setup_json_call(
            backend_id,
            "setup_install_codex",
            install_args,
            network,
            identity,
            timeout=600,
        )
        status = str(raw.get("status") or "")
        phase = str(raw.get("phase") or raw.get("step") or "")
        label = f"{status} {phase}".strip() or (
            "skipped" if raw.get("skipped") else "ok"
        )
        if label != last:
            console.print(f"  setup_install_codex: {label}")
            last = label
        if raw.get("skipped") or raw.get("setup_codex"):
            return raw
        if status in ("", "complete", "completed"):
            return raw
        if status == "in_progress":
            time.sleep(POLL_INTERVAL_S)
            continue
        return raw
    _fail(
        "setup",
        f"Timed out waiting for setup_install_codex ({package}) after "
        f"{CODEX_INSTALL_TIMEOUT_S}s",
    )
    return {}


def run_setup_stage(
    merged: Dict[str, Any],
    backend_id: str,
    network: str,
    identity: str,
) -> None:
    branding = merged.get("branding") or {}
    catalog = fetch_shared_token_catalog(backend_id, network, identity)
    spec_token = merged.get("token")
    token_draft = _token_draft(spec_token, catalog)
    spec_symbol = str((spec_token or {}).get("symbol") or "").strip() if isinstance(spec_token, dict) else ""
    if spec_symbol and not token_draft:
        known = ", ".join(sorted(catalog)) or "none"
        _fail(
            "setup",
            f"token symbol {spec_symbol!r} is not in this realm's shared-ledger catalog "
            f"({known}). Pass --token-canister for an existing ledger, or a catalog symbol.",
        )
    if not token_draft and not _setup_state_has_token(backend_id, network, identity):
        default_symbol = default_catalog_symbol(catalog)
        token_draft = catalog_token_draft(default_symbol or "", catalog)
        if not token_draft:
            _fail(
                "setup",
                "setup_launch needs a treasury token and this realm's shared-ledger "
                "catalog is empty. Pass --token-symbol / --token-canister.",
            )
        console.print(
            f"  no token in spec: defaulting to {default_symbol} "
            f"({token_draft['token_canister_id']}), as the setup wizard does"
        )
    draft: Dict[str, Any] = {
        "codex": {
            "package": merged["codex"]["package"],
        }
    }
    if merged["codex"].get("version"):
        draft["codex"]["version"] = merged["codex"]["version"]
    identity_block: Dict[str, str] = {}
    if branding.get("manifesto"):
        identity_block["manifesto"] = branding["manifesto"]
    if branding.get("welcome_message"):
        identity_block["welcome_message"] = branding["welcome_message"]
    if identity_block:
        draft["identity"] = identity_block
    if token_draft:
        draft["token"] = token_draft

    console.print("  setup_save_draft (codex / identity)")
    _setup_json_call(backend_id, "setup_save_draft", draft, network, identity)

    package = str(merged["codex"]["package"])
    version = merged["codex"].get("version")
    preinstall_codex_dependencies(backend_id, package, network, identity)
    call_setup_install_codex(backend_id, package, version, network, identity)

    if token_draft and token_draft.get("token_canister_id"):
        console.print(f"  setup_configure_token {token_draft['token_canister_id']}")
        token_args: Dict[str, Any] = {
            "token_canister_id": token_draft["token_canister_id"]
        }
        if token_draft.get("symbol"):
            token_args["symbol"] = token_draft["symbol"]
        if token_draft.get("decimals") is not None:
            token_args["decimals"] = token_draft["decimals"]
        if token_draft.get("indexer_canister_id"):
            token_args["indexer_canister_id"] = token_draft["indexer_canister_id"]
        _setup_json_call(
            backend_id, "setup_configure_token", token_args, network, identity, timeout=300
        )
    elif token_draft:
        _fail(
            "setup",
            f"treasury token {token_draft.get('symbol') or '?'} has no ledger for "
            f"{network!r}: pass --token-canister <ledger canister id>.",
        )
    else:
        console.print("  treasury token already configured; leaving it as is")

    # setup_save_draft is what the wizard uses (saveSetupDraft in
    # src/realm_frontend/src/lib/setup/api.ts): it puts the images in the draft
    # asset store, which is where setup_launch's upload phase reads them from
    # before writing /custom/logo.png. setup_set_branding stores them under a
    # different key that the launch phase never looks at.
    #
    # One call per asset: a data URL may be up to 1.5 MiB, and two of them plus
    # envelope would exceed the ~2 MiB ingress limit. The backend merges draft
    # partials, so splitting is equivalent.
    branding_parts: List[Dict[str, Any]] = []
    if branding.get("logo"):
        branding_parts.append({"logo_data_url": file_to_data_url(branding["logo"])})
    if branding.get("background"):
        branding_parts.append(
            {"background_data_url": file_to_data_url(branding["background"])}
        )
    if branding.get("primary"):
        branding_parts.append({"colors": {"primary": branding["primary"]}})
    for part in branding_parts:
        console.print(f"  setup_save_draft (branding: {', '.join(part)})")
        _setup_json_call(
            backend_id,
            "setup_save_draft",
            {"branding": part},
            network,
            identity,
            timeout=300,
        )

    console.print("  setup_launch")
    _setup_json_call(backend_id, "setup_launch", None, network, identity, timeout=300)
    poll_setup_launch(backend_id, network, identity)


def poll_setup_launch(backend_id: str, network: str, identity: str) -> None:
    start = time.time()
    last = ""
    while time.time() - start < LAUNCH_POLL_TIMEOUT_S:
        raw = _canister_call(
            backend_id,
            "get_setup_launch_status",
            "()",
            network,
            identity,
            query=True,
            timeout=60,
        )
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = {}
        launch = (raw or {}).get("launch") if isinstance(raw, dict) else {}
        if not isinstance(launch, dict):
            launch = {}
        status = str(launch.get("status") or "unknown")
        phase = launch.get("phase") or ""
        label = f"{status} {phase}".strip()
        if label != last:
            console.print(f"  launch: {label}")
            last = label
        if status == "completed":
            return
        if status == "failed":
            _fail("setup", launch.get("error") or "setup_launch failed")
        time.sleep(POLL_INTERVAL_S)
    _fail("setup", f"Timed out waiting for setup_launch to complete ({backend_id})")


def setup_already_complete(backend_id: str, network: str, identity: str) -> bool:
    try:
        raw = _canister_call(
            backend_id,
            "get_setup_launch_status",
            "()",
            network,
            identity,
            query=True,
            timeout=60,
        )
        if isinstance(raw, str):
            raw = json.loads(raw)
        launch = (raw or {}).get("launch") if isinstance(raw, dict) else {}
        if isinstance(launch, dict) and launch.get("status") == "completed":
            return True
        state = _canister_call(
            backend_id, "get_setup_state", "()", network, identity, query=True, timeout=60
        )
        if isinstance(state, dict) and state.get("setup_completed_at"):
            return True
        status = str((state or {}).get("status") or "") if isinstance(state, dict) else ""
        if status and status not in ("setup", "unknown"):
            return True
    except Exception:
        return False
    return False


def apply_runtime_config(
    merged: Dict[str, Any],
    backend_id: str,
    network: str,
    identity: str,
) -> None:
    config = merged.get("config")
    open_reg = bool(merged.get("open_registration"))
    realm_cfg: Dict[str, Any] = {}
    if open_reg:
        realm_cfg["open_registration"] = True
    if realm_cfg:
        console.print("  update_realm_config (open_registration)")
        _setup_json_call(
            backend_id, "update_realm_config", realm_cfg, network, identity, stage="config"
        )

    if not isinstance(config, dict) or not config:
        return
    auto_scale = config.get("auto_scale_enabled")
    scaling = config.get("scaling") if isinstance(config.get("scaling"), dict) else {}
    quarter_capacity = scaling.get("quarter_capacity")
    if auto_scale is None and quarter_capacity is None:
        return

    auto_lit = "True" if (True if auto_scale is None else bool(auto_scale)) else "False"
    cap_stmt = ""
    if quarter_capacity is not None:
        cap_stmt = (
            "md.setdefault('scaling', {})\n"
            f"md['scaling']['quarter_capacity'] = {int(quarter_capacity)}\n"
            "realm.manifest_data = json.dumps(md)\n"
        )
    code = (
        "import json\n"
        "from ggg import Realm\n"
        "realm = Realm.load('1')\n"
        "md = json.loads(getattr(realm, 'manifest_data', '') or '{}')\n"
        "if not isinstance(md, dict):\n"
        "    md = {}\n"
        f"{cap_stmt}"
        f"realm.auto_scale_enabled = {auto_lit}\n"
        "print('ok')\n"
    )
    console.print("  apply config (scaling / auto_scale_enabled) via __shell__")
    try:
        raw = _canister_call(
            backend_id,
            "__shell__",
            _candid_text_arg(code),
            network,
            identity,
            timeout=120,
        )
        text = raw if isinstance(raw, str) else json.dumps(raw)
        if "ok" not in str(text).lower() and isinstance(raw, dict) and raw.get("success") is False:
            _fail("config", raw.get("error") or str(raw))
    except Exception as exc:
        _fail("config", f"Failed to apply config as founder: {exc}")


def import_data_overlay(
    merged: Dict[str, Any],
    backend_id: str,
    network: str,
    identity: str,
) -> None:
    data = merged.get("data")
    if data in (None, "", []):
        return
    from .import_data import import_data_command

    tmp_path = None
    try:
        if isinstance(data, list):
            fd, tmp_path = tempfile.mkstemp(prefix="realms-new-data-", suffix=".json")
            with os.fdopen(fd, "w") as fh:
                json.dump(data, fh)
            file_path = tmp_path
        elif isinstance(data, str):
            file_path = data
            if not Path(file_path).is_file():
                _fail("data", f"Data file not found: {file_path}")
        else:
            _fail("data", "spec.data must be a path or an array of GGG records")
        console.print(f"  realms db import overlay → {backend_id}")
        import_data_command(
            file_path,
            entity_type=None,
            format="json",
            batch_size=50,
            dry_run=False,
            network=network,
            identity=identity,
            canister=backend_id,
            folder=None,
        )
    except StageError:
        raise
    except typer.Exit as exc:
        _fail("data", f"db import failed (exit {exc.exit_code})")
    except Exception as exc:
        _fail("data", str(exc))
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _existing_geister_indexes() -> List[int]:
    indexes: List[int] = []
    if shutil.which("geister"):
        proc = _run(["geister", "agent", "ls"], timeout=60)
        text = (proc.stdout or "") + "\n" + (proc.stderr or "")
        for match in re.finditer(rf"{GEISTER_AGENT_PREFIX}_(\d+)", text):
            indexes.append(int(match.group(1)))
    if _icp_available():
        proc = _run(["icp", "identity", "list"], timeout=30)
        for match in re.finditer(rf"{GEISTER_AGENT_PREFIX}_(\d+)", proc.stdout or ""):
            indexes.append(int(match.group(1)))
    env = _dfx_env()
    proc = _run(["dfx", "identity", "list"], timeout=30, env=env)
    for match in re.finditer(rf"{GEISTER_AGENT_PREFIX}_(\d+)", proc.stdout or ""):
        indexes.append(int(match.group(1)))
    return indexes


def join_geister_members(
    merged: Dict[str, Any],
    backend_id: str,
    network: str,
    identity: str,
) -> List[Dict[str, str]]:
    count = int(merged.get("members") or 0)
    if count <= 0:
        return []
    if not shutil.which("geister"):
        _fail("members", f"geister not found on PATH. {GEISTER_INSTALL_HINT}")

    ic_err = check_ic_members_policy(
        network, count, bool(merged.get("open_registration")), merged.get("invite_codes") or []
    )
    if ic_err:
        _fail("members", ic_err)

    existing = _existing_geister_indexes()
    start = (max(existing) + 1) if existing else 1
    console.print(f"  geister agent generate {count} (start index {start})")
    proc = _run(
        ["geister", "agent", "generate", str(count), "--start", str(start)],
        timeout=300,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "geister agent generate failed").strip()
        _fail(
            "members",
            f"{err}\nAgent generation requires local geister mode "
            f"(`geister mode local`). {GEISTER_INSTALL_HINT}",
        )

    invite_codes = [invite_code_to_checksum(c) for c in (merged.get("invite_codes") or [])]
    created: List[Dict[str, str]] = []
    for i in range(count):
        agent_name = geister_agent_name(start + i)
        checksum = ""
        if invite_codes:
            checksum = invite_codes[i % len(invite_codes)]
        arg = f'("member", "", "{checksum}")'
        console.print(f"  join_realm as {agent_name}")
        try:
            _canister_call(
                backend_id,
                "join_realm",
                arg,
                network,
                agent_name,
                timeout=120,
            )
        except Exception as exc:
            _fail("members", f"join_realm failed for {agent_name}: {exc}")
        principal = ""
        try:
            principal = resolve_identity_principal(agent_name)
        except StageError:
            principal = ""
        created.append({"identity": agent_name, "principal": principal})
    return created


@dataclass(frozen=True)
class GaasConfig:
    env_name: str
    domain: str
    registry_id: str
    installer_id: str
    canisters: Dict[str, str]
    path: Path
    can_test_mode: Optional[bool] = None
    test_flags: Dict[str, bool] = field(default_factory=dict)

    @property
    def portal_host(self) -> str:
        host = (self.domain or "").strip()
        if not host:
            return ""
        if host.startswith("http://") or host.startswith("https://"):
            return host
        return f"https://{host}"


def load_gaas_config(path: str | Path) -> GaasConfig:
    """Read the pretty JSON written by ``gaas new --output-file``."""
    config_path = Path(path).expanduser()
    if not config_path.is_file():
        raise StageError("validate", f"--gaas-config not found: {config_path}")
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StageError("validate", f"--gaas-config is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise StageError("validate", "--gaas-config must be a JSON object")
    env_name = str(data.get("name") or "").strip().lower()
    domain = str(data.get("domain") or "").strip()
    canisters = data.get("canisters") or {}
    if not isinstance(canisters, dict):
        raise StageError("validate", "--gaas-config canisters must be an object")
    registry_id = str(canisters.get("realm_registry_backend") or "").strip()
    installer_id = str(canisters.get("realm_installer") or "").strip()
    missing: List[str] = []
    if not env_name:
        missing.append("name")
    if not registry_id:
        missing.append("canisters.realm_registry_backend")
    if not installer_id:
        missing.append("canisters.realm_installer")
    if missing:
        raise StageError(
            "validate",
            f"--gaas-config {config_path} missing {', '.join(missing)} "
            "(write it with `gaas new --output-file`).",
        )
    flags_block = data.get("flags") if isinstance(data.get("flags"), dict) else {}
    can_test_mode: Optional[bool] = None
    if "can_test_mode" in flags_block:
        can_test_mode = bool(flags_block["can_test_mode"])
    elif "open_mode" in flags_block:
        can_test_mode = bool(flags_block["open_mode"])
    raw_test_flags = data.get("test_flags")
    test_flags: Dict[str, bool] = {}
    if isinstance(raw_test_flags, dict):
        test_flags = {str(k): bool(v) for k, v in raw_test_flags.items()}
    return GaasConfig(
        env_name=env_name,
        domain=domain,
        registry_id=registry_id,
        installer_id=installer_id,
        canisters={str(k): str(v) for k, v in canisters.items()},
        path=config_path,
        can_test_mode=can_test_mode,
        test_flags=test_flags,
    )


def resolve_gos_queue(
    *,
    gaas_config: Optional[str],
    network: str,
) -> Tuple[GaasConfig, str]:
    """Return (config, environment name). ``--gaas-config`` is required."""
    if not gaas_config:
        raise StageError(
            "validate",
            "--gaas-config is required: pass the pretty JSON written by "
            "`gaas new --output-file` (needs canisters.realm_registry_backend "
            "and canisters.realm_installer).",
        )
    gaas = load_gaas_config(gaas_config)
    net = (network or "").strip().lower()
    if net and net != gaas.env_name:
        raise StageError(
            "validate",
            f"--network {net} does not match gaas config name {gaas.env_name!r}",
        )
    return gaas, gaas.env_name


def normalize_deploy_mode(value: Optional[str]) -> str:
    mode = (value or "gaas").strip().lower()
    if mode not in ALLOWED_DEPLOY_MODES:
        raise StageError(
            "validate",
            f"--deploy-mode must be 'gaas' or 'standalone', got {value!r}",
        )
    return mode


def standalone_artifact_urls(
    version: str,
    *,
    repo: str = REALMS_RELEASE_REPO,
) -> Tuple[str, str]:
    """GitHub URLs for the release's realm backend wasm and frontend bundle."""
    ref = (version or "latest").strip()
    if ref in ("build",):
        raise StageError(
            "deploy",
            "standalone --version build is not supported; use latest or a "
            "GitHub semver tag (e.g. v0.3.5)",
        )
    if ref in ("main", "latest", ""):
        try:
            tag = release_assets.latest_tag(repo=repo)
        except Exception as exc:
            raise StageError("deploy", f"Could not resolve the latest {repo} release: {exc}") from exc
    else:
        tag = release_assets.normalize_tag(ref)
    return (
        release_assets.asset_url("realm_backend.wasm.gz", tag, repo=repo),
        release_assets.asset_url("realm_frontend.tar.gz", tag, repo=repo),
    )


def _download_release_file(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    console.print(f"  download {url}")
    try:
        urllib.request.urlretrieve(url, dest)
    except Exception as exc:
        raise StageError("deploy", f"Failed to download {url}: {exc}") from exc
    if not dest.is_file() or dest.stat().st_size < 100:
        size = dest.stat().st_size if dest.is_file() else 0
        raise StageError("deploy", f"Download too small ({size} B): {url}")
    return dest


def _extract_frontend_archive(archive: Path, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(dest)
    # Release tarballs are Casals asset bundles (Casals/docs/BUNDLES.md): the
    # metadata file inside is for the bundle hash, not for the asset canister.
    (dest / ".casals-bundle.json").unlink(missing_ok=True)
    nested = dest / "dist"
    if nested.is_dir() and any(nested.iterdir()):
        return nested
    return dest


def _write_standalone_canister_ids_js(
    dist: Path,
    *,
    backend_id: str,
    frontend_id: str,
    network: str,
    file_registry_id: str,
) -> None:
    origin = DERIVATION_ORIGINS.get(network, "")
    payload = {
        "realm_backend": backend_id,
        "file_registry": file_registry_id,
        "derivation_origin": origin,
        "portal_url": f"https://{frontend_id}.icp0.io/",
        "test_mode_ii_bypass": False,
    }
    (dist / "canister_ids.js").write_text(
        "globalThis.__CANISTER_IDS = " + json.dumps(payload) + ";\n",
        encoding="utf-8",
    )


def _run_in_dir(
    cmd: List[str],
    *,
    cwd: Path,
    timeout: int = 300,
) -> subprocess.CompletedProcess:
    log = get_run_log()
    if log is not None:
        log.log_command(cmd)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_dfx_env(),
        cwd=str(cwd),
    )
    if log is not None:
        if result.stdout:
            log.log_output(result.stdout)
        if result.stderr:
            log.log_output(result.stderr)
        if result.returncode != 0:
            log.write(f"exit {result.returncode}")
    return result


def ensure_conductor_cycles_for_realm(
    gaas: Optional["GaasConfig"],
    network: str,
    identity: str,
) -> None:
    """Fund the Casals conductor before asking it to mint a realm.

    The conductor pays for the realm's backend, frontend and baton out of its own
    balance. Without this the deploy fails half-way — the realm canisters exist
    and the extensions install, then the baton hand-off dies with IC0504 and the
    realm is never registered.
    """
    # Casals v2: the conductor keeps itself funded from the sheet's `cycles`
    # block (min_balance_tc / budget_tc), so there is no CLI-side top-up here.
    del gaas, network, identity


def _file_registry_id_or_empty(network: str) -> str:
    try:
        from .files import _resolve_registry

        return _resolve_registry(network, None)
    except Exception:
        return ""


def call_enter_setup(
    backend_id: str,
    founder: str,
    registry_id: str,
    environment: str,
    network: str,
    identity: str,
) -> None:
    arg = f'(principal "{founder}", "{registry_id}", "{environment}")'
    console.print(f"  enter_setup {backend_id}")
    try:
        _canister_call(backend_id, "enter_setup", arg, network, identity, timeout=180)
    except Exception as exc:
        raise StageError("deploy", f"enter_setup failed: {exc}") from exc


def deploy_standalone_realm(
    *,
    version: str,
    network: str,
    identity: str,
    slug: str,
) -> Tuple[str, str]:
    """Mint realm_backend + realm_frontend from a GitHub release. Returns IDs."""
    project_root = get_project_root()
    backend_url, frontend_url = standalone_artifact_urls(version)
    work = Path(tempfile.mkdtemp(prefix=f"realms-standalone-{slug}-"))
    console.print(f"  standalone work {work}")
    wasm = _download_release_file(backend_url, work / "realm_backend.wasm.gz")
    archive = _download_release_file(frontend_url, work / "realm_frontend.tar.gz")
    dist = _extract_frontend_archive(archive, work / "frontend")

    dfx_src = project_root / "dfx.json"
    networks = {}
    if dfx_src.is_file():
        networks = json.loads(dfx_src.read_text(encoding="utf-8")).get("networks") or {}
    candid = project_root / "src" / "realm_backend" / "realm_backend.did"
    dfx_json = {
        "version": 1,
        "canisters": {
            "realm_backend": {
                "type": "custom",
                "candid": str(candid) if candid.is_file() else "",
                "wasm": str(wasm),
                "gzip": True,
            },
            "realm_frontend": {
                "type": "assets",
                "source": [str(dist)],
            },
        },
        "networks": networks,
    }
    (work / "dfx.json").write_text(json.dumps(dfx_json, indent=2) + "\n", encoding="utf-8")

    for name in ("realm_backend", "realm_frontend"):
        cmd = [
            "dfx",
            "canister",
            "create",
            name,
            "--network",
            network,
            "--identity",
            identity,
            "--no-wallet",
            "--with-cycles",
            str(STANDALONE_CREATE_CYCLES),
        ]
        console.print(f"  create {name}")
        proc = _run_in_dir(cmd, cwd=work, timeout=180)
        if proc.returncode != 0:
            raise StageError(
                "deploy",
                f"dfx canister create {name} failed: "
                f"{(proc.stderr or proc.stdout or '').strip()[:400]}",
            )

    ids_path = work / "canister_ids.json"
    if not ids_path.is_file():
        raise StageError("deploy", f"missing {ids_path} after canister create")
    ids = json.loads(ids_path.read_text(encoding="utf-8"))
    backend_id = str((ids.get("realm_backend") or {}).get(network) or "").strip()
    frontend_id = str((ids.get("realm_frontend") or {}).get(network) or "").strip()
    if not backend_id or not frontend_id:
        raise StageError("deploy", f"could not read canister IDs from {ids_path}")

    file_registry_id = _file_registry_id_or_empty(network)
    _write_standalone_canister_ids_js(
        dist,
        backend_id=backend_id,
        frontend_id=frontend_id,
        network=network,
        file_registry_id=file_registry_id,
    )

    install_backend = [
        "dfx",
        "canister",
        "install",
        "realm_backend",
        "--wasm",
        str(wasm),
        "--mode",
        "install",
        "--network",
        network,
        "--identity",
        identity,
        "--yes",
    ]
    console.print(f"  install realm_backend {backend_id}")
    proc = _run_in_dir(install_backend, cwd=work, timeout=300)
    if proc.returncode != 0:
        raise StageError(
            "deploy",
            f"backend install failed: {(proc.stderr or proc.stdout or '').strip()[:400]}",
        )

    deploy_frontend = [
        "dfx",
        "deploy",
        "realm_frontend",
        "--network",
        network,
        "--identity",
        identity,
        "--yes",
        "--mode",
        "install",
    ]
    console.print(f"  deploy realm_frontend {frontend_id}")
    proc = _run_in_dir(deploy_frontend, cwd=work, timeout=600)
    if proc.returncode != 0:
        raise StageError(
            "deploy",
            f"frontend deploy failed: {(proc.stderr or proc.stdout or '').strip()[:400]}",
        )
    return backend_id, frontend_id


def request_deployment(
    manifest: Dict[str, Any],
    registry_id: str,
    network: str,
    identity: str,
) -> str:
    arg = _candid_text_arg(json.dumps(manifest))
    console.print(f"  request_deployment → {registry_id}")
    try:
        raw = _canister_call(
            registry_id, "request_deployment", arg, network, identity, timeout=180
        )
    except Exception as exc:
        _fail("deploy", str(exc))
    info = raw if isinstance(raw, dict) else {}
    if isinstance(raw, str):
        try:
            info = json.loads(raw)
        except json.JSONDecodeError:
            _fail("deploy", f"Could not parse request_deployment response: {raw[:300]}")
    if not info.get("success") and not info.get("job_id"):
        _fail("deploy", info.get("error") or f"request_deployment failed: {info}")
    job_id = str(info.get("job_id") or "").strip()
    if not job_id:
        _fail("deploy", "request_deployment succeeded but returned no job_id")
    return job_id


# ---------------------------------------------------------------------------
# Command entry
# ---------------------------------------------------------------------------

def new_command(
    spec_file: Optional[str],
    identity: str,
    network: str,
    name: Optional[str] = None,
    slug: Optional[str] = None,
    gos: Optional[str] = None,
    version: Optional[str] = None,
    subnet: Optional[str] = None,
    codex: Optional[str] = None,
    codex_version: Optional[str] = None,
    logo: Optional[str] = None,
    background: Optional[str] = None,
    primary: Optional[str] = None,
    manifesto: Optional[str] = None,
    welcome: Optional[str] = None,
    token_symbol: Optional[str] = None,
    token_canister: Optional[str] = None,
    config: Optional[str] = None,
    data: Optional[str] = None,
    members: Optional[int] = None,
    open_registration: Optional[bool] = None,
    yes: bool = False,
    resume: Optional[str] = None,
    co_admin: Optional[str] = None,
    gaas_config: Optional[str] = None,
    deploy_mode: Optional[str] = None,
    from_stage: Optional[str] = None,
) -> None:
    """Run the live wizard-path create pipeline. Never hits IC from unit tests."""
    stage = "validate"
    try:
        spec, spec_dir = load_spec_file(spec_file)
        merged = merge_spec_and_flags(
            spec,
            spec_dir=spec_dir,
            name=name,
            slug=slug,
            gos=gos,
            version=version,
            subnet_flag=subnet,
            codex=codex,
            codex_version=codex_version,
            logo=logo,
            background=background,
            primary=primary,
            manifesto=manifesto,
            welcome=welcome,
            token_symbol=token_symbol,
            token_canister=token_canister,
            config_path=config,
            data_path=data,
            members=members,
            open_registration=open_registration,
            co_admin=co_admin,
        )
        network = (network or "").strip().lower()
        identity = (identity or "").strip()
        mode = normalize_deploy_mode(deploy_mode)
        gaas: Optional[GaasConfig] = None
        if mode == "standalone":
            if gaas_config:
                raise StageError(
                    "validate",
                    "--gaas-config is not used with --deploy-mode=standalone",
                )
            if not network:
                raise StageError(
                    "validate",
                    "--network is required with --deploy-mode=standalone",
                )
            if (resume or "").strip():
                raise StageError(
                    "validate",
                    "--resume is only valid with --deploy-mode=gaas",
                )
        elif gaas_config:
            gaas, network = resolve_gos_queue(gaas_config=gaas_config, network=network)
        elif not network:
            raise StageError(
                "validate",
                "--gaas-config is required: pass the pretty JSON written by "
                "`gaas new --output-file` (needs canisters.realm_registry_backend "
                "and canisters.realm_installer).",
            )
        validate_merged_spec(merged, network=network, identity=identity)
        if mode == "gaas" and gaas is None:
            raise StageError(
                "validate",
                "--gaas-config is required: pass the pretty JSON written by "
                "`gaas new --output-file` (needs canisters.realm_registry_backend "
                "and canisters.realm_installer).",
            )
        assert_identity_is_ii_linked(identity, network, merged.get("co_admin") or "")
        founder = resolve_identity_principal(identity)
        merged["founder"] = founder

        stages = stages_for_mode(mode)
        run_ids = stage_ids_to_run(stages, from_stage)
        start_index = parse_from_stage(from_stage, stages)
        if start_index > 0:
            skipped = [sid for sid, _title in stages[:start_index] if sid not in run_ids]
            console.print(
                f"  from-stage {stages[start_index][0]} "
                f"(skipping {', '.join(skipped) or 'none'})"
            )

        if mode == "standalone":
            if "confirm" in run_ids and not yes:
                console.print(
                    f"Create standalone realm?\n"
                    f"  name:     {merged['name']}\n"
                    f"  slug:     {merged['slug']}\n"
                    f"  network:  {network}\n"
                    f"  version:  {merged['version']}\n"
                    f"  codex:    {merged['codex']['package']}\n"
                    f"  founder:  {founder}"
                )
                if not typer.confirm("Proceed?"):
                    console.print("[yellow]Aborted.[/yellow]")
                    raise typer.Exit(0)
            backend_id = ""
            frontend_id = ""
            portal = ""
            if "deploy" in run_ids:
                stage = "deploy"
                backend_id, frontend_id = deploy_standalone_realm(
                    version=merged["version"],
                    network=network,
                    identity=identity,
                    slug=merged["slug"],
                )
                portal = f"https://{frontend_id}.icp0.io/"
                ensure_founder_enter_setup(
                    backend_id,
                    founder,
                    _file_registry_id_or_empty(network),
                    network,
                    network,
                    identity,
                    timeout_s=15,
                )
                apply_bootstrap_canister_config(
                    backend_id,
                    frontend_id,
                    network,
                    identity,
                    founder=founder,
                )
                console.print(f"  portal {portal}")
            elif run_ids & POST_DEPLOY_STAGES:
                raise StageError(
                    "validate",
                    "standalone --from-stage cannot skip deploy (no registry "
                    "slug). Use --deploy-mode=gaas --from-stage "
                    f"{stages[start_index][0]} --slug <slug>",
                )
            if "setup" in run_ids:
                stage = "setup"
                if setup_already_complete(backend_id, network, identity):
                    console.print("  setup already complete — skipping")
                else:
                    run_setup_stage(merged, backend_id, network, identity)
            if "co-admin" in run_ids:
                stage = "co-admin"
                register_co_admin_principal(merged, backend_id, network, identity)
                register_test_mode_admin(gaas, backend_id, network, identity)
            if "config" in run_ids:
                stage = "config"
                apply_runtime_config(merged, backend_id, network, identity)
            if "data" in run_ids:
                stage = "data"
                import_data_overlay(merged, backend_id, network, identity)
            member_rows: List[Dict[str, str]] = []
            if "members" in run_ids:
                stage = "members"
                member_rows = join_geister_members(merged, backend_id, network, identity)
            result = {
                "deploy_mode": "standalone",
                "slug": merged["slug"],
                "portal_url": portal,
                "backend_id": backend_id,
                "frontend_id": frontend_id,
                "founder": founder,
                "co_admin": merged.get("co_admin") or "",
                "members": member_rows,
            }
            console.print(json.dumps(result, indent=2))
            return

        assert gaas is not None
        registry_id = gaas.registry_id
        installer_id = gaas.installer_id
        console.print(f"  gaas-config {gaas.path}")
        console.print(f"  registry {registry_id}")
        console.print(f"  installer {installer_id}")

        if "credits" in run_ids:
            stage = "credits"
            console.print(f"[bold]credits[/bold] founder {founder} on {network}")
            try:
                balance = query_credit_balance(registry_id, founder, network, identity)
            except StageError:
                raise
            except Exception as exc:
                _fail("credits", f"get_credits failed: {exc}")
            if balance < DEPLOYMENT_COST_CREDITS:
                shortfall = DEPLOYMENT_COST_CREDITS - balance
                console.print(
                    f"  balance {balance} < {DEPLOYMENT_COST_CREDITS}; "
                    f"granting {shortfall} as registry admin"
                )
                try:
                    grant_registry_credits(
                        registry_id, founder, shortfall, network, identity
                    )
                    balance = query_credit_balance(
                        registry_id, founder, network, identity
                    )
                except Exception as exc:
                    console.print(f"[yellow]  auto top-up failed: {exc}[/yellow]")
            assert_credits_sufficient(balance, founder, network)
            console.print(f"  balance {balance} (need {DEPLOYMENT_COST_CREDITS})")

        if "confirm" in run_ids and not yes:
            stage = "confirm"
            subnet_label = merged["subnet"].get("choice")
            if merged["subnet"].get("id"):
                subnet_label = f"{subnet_label} ({merged['subnet']['id']})"
            console.print(
                f"Create realm?\n"
                f"  name:     {merged['name']}\n"
                f"  slug:     {merged['slug']}\n"
                f"  network:  {network}\n"
                f"  subnet:   {subnet_label}\n"
                f"  codex:    {merged['codex']['package']}\n"
                f"  members:  {merged['members']}\n"
                f"  founder:  {founder}\n"
                f"  co-admin: {merged.get('co_admin') or '(none)'}"
            )
            if not typer.confirm("Proceed?"):
                console.print("[yellow]Aborted.[/yellow]")
                raise typer.Exit(0)

        job_id = (resume or "").strip()
        backend_id = ""
        frontend_id = ""
        portal = portal_url_for_slug(
            merged["slug"], network, portal_host=gaas.portal_host or None
        )

        if "deploy" in run_ids and not job_id:
            stage = "deploy"
            manifest = build_portal_manifest(
                name=merged["name"],
                slug=merged["slug"],
                network=network,
                version=merged["version"],
                founder=founder,
                subnet=merged["subnet"],
                codex_package=merged["codex"]["package"],
                codex_version=merged["codex"].get("version"),
                open_registration=bool(merged.get("open_registration")),
                manifesto=merged["branding"].get("manifesto") or "",
                welcome_message=merged["branding"].get("welcome_message") or "",
                portal_host=gaas.portal_host or None,
                can_test_mode=gaas.can_test_mode,
                test_flags=gaas.test_flags,
            )
            if "artifacts" in manifest or "expected_hashes" in manifest:
                _fail("deploy", "internal error: GitHub artifact URLs must not appear in the portal manifest")
            infra = manifest.setdefault("infra", {})
            live_fr = _canister_id_from_inventory("file_registry", network)
            live_mp = _canister_id_from_inventory("marketplace_backend", network)
            if live_fr:
                infra["file_registry_canister_id"] = live_fr
            if live_mp:
                infra["marketplace_canister_id"] = live_mp
            ensure_conductor_cycles_for_realm(gaas, network, identity)
            job_id = request_deployment(manifest, registry_id, network, identity)
            console.print(f"  job_id {job_id}")

        if "poll" in run_ids:
            if not job_id:
                raise StageError(
                    "poll",
                    "--from-stage poll needs --resume <job_id>. "
                    "To continue an already-installed realm, use --from-stage setup.",
                )
            stage = "poll"
            if (resume or "").strip():
                console.print(f"[bold]resume[/bold] job {job_id}")
            info = poll_installer_until_ready(
                installer_id, job_id, network, identity
            )
            backend_id = str(info.get("backend_canister_id") or "").strip()
            frontend_id = str(info.get("frontend_canister_id") or "").strip()
        elif run_ids & POST_DEPLOY_STAGES:
            stage = "setup"
            resolved = resolve_registered_slug(
                registry_id, merged["slug"], network, identity
            )
            backend_id = resolved["backend_canister_id"]
            frontend_id = resolved["frontend_canister_id"]
            if resolved.get("portal_url"):
                portal = resolved["portal_url"]
            console.print(f"  resolved {merged['slug']} → {backend_id}")

        console.print(f"  portal {portal}")

        if "setup" in run_ids:
            stage = "setup"
            if setup_already_complete(backend_id, network, identity):
                console.print("  setup already complete — skipping")
            else:
                run_setup_stage(merged, backend_id, network, identity)

        if "co-admin" in run_ids:
            stage = "co-admin"
            register_co_admin_principal(merged, backend_id, network, identity)
            register_test_mode_admin(gaas, backend_id, network, identity)

        if "config" in run_ids:
            stage = "config"
            apply_runtime_config(merged, backend_id, network, identity)

        if "data" in run_ids:
            stage = "data"
            import_data_overlay(merged, backend_id, network, identity)

        member_rows = []
        if "members" in run_ids:
            stage = "members"
            member_rows = join_geister_members(merged, backend_id, network, identity)

        result = {
            "job_id": job_id,
            "slug": merged["slug"],
            "portal_url": portal,
            "backend_id": backend_id,
            "frontend_id": frontend_id,
            "founder": founder,
            "co_admin": merged.get("co_admin") or "",
            "members": member_rows,
        }
        console.print(json.dumps(result, indent=2))
    except StageError as exc:
        console.print(f"[red]realms new failed at stage: {exc.stage}[/red]")
        console.print(f"[red]{exc.message}[/red]")
        log = get_run_log()
        if log is not None:
            log.write(f"FAILED stage={exc.stage}")
            log.write(exc.message)
        raise typer.Exit(1) from exc
    except typer.Exit:
        raise
    except Exception as exc:
        console.print(f"[red]realms new failed at stage: {stage}[/red]")
        console.print(f"[red]{exc}[/red]")
        log = get_run_log()
        if log is not None:
            log.write(f"FAILED stage={stage}")
            log.write(str(exc))
        raise typer.Exit(1) from exc

