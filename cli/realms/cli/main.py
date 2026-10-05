"""Main CLI application for Realms."""

from typing import List, Optional
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .commands.create import create_command
from .commands.db import db_command, db_find_command, db_get_command, db_schema_command
from .commands.deploy import deploy_command
from .commands.domains import domains_app
from .commands.import_data import import_data_command
from .commands.export_data import export_data_command
from .commands.extension import extension_command, codex_command
from .commands.wasm_registry import wasm_command
from .commands.installer import installer_health_command
from .commands.marketplace import (
    marketplace_call_command,
    marketplace_deploy_command,
    marketplace_status_command,
)
from .commands.mundus import mundus_deploy_descriptor_command
from .commands.files import (
    files_build_command,
    files_publish_assistant_command,
    files_publish_branding_command,
    files_publish_command,
    files_publish_release_command,
    files_reset_command,
)
from .commands.quarter import (
    quarter_create_command,
    quarter_list_command,
    quarter_register_command,
    quarter_remove_command,
    quarter_status_command,
)
from .commands.registry import (
    billing_add_credits_command,
    billing_balance_command,
    billing_deduct_credits_command,
    billing_redeem_voucher_command,
    billing_status_command,
    realm_deploy_realm_command,
    realm_deploy_status_command,
    registry_count_command,
    registry_create_command,
    registry_deploy_command,
    registry_get_command,
    registry_list_command,
    registry_remove_command,
    registry_search_command,
    registry_status_command,
)
from .commands.test import test_command
from .commands.new import new_command
from .constants import MAX_BATCH_SIZE, REALM_FOLDER
from .runlog import print_log_path, start_run_log, stop_run_log
from .utils import (
    IC_CANDID_UI,
    check_dependencies,
    display_info_panel,
    get_current_network,
    get_current_realm,
    get_current_realm_folder,
    get_effective_cwd,
    get_effective_network_and_canister,
    get_project_root,
    get_registry_canister_id,
    is_canister_id,
    list_realm_folders,
    resolve_realm_by_id,
    resolve_realm_details,
    resolve_realm_ref_to_canister_id,
    set_current_network,
    set_current_realm,
    set_current_realm_folder,
    unset_current_network,
    unset_current_realm,
    unset_current_realm_folder,
)

console = Console()

app = typer.Typer(
    name="realms",
    help="CLI tool for deploying and managing Realms",
    add_completion=False,
    rich_markup_mode="rich",
    invoke_without_command=True,
)




@app.command("extension", hidden=True)
def extension(
    action: str = typer.Argument(
        ...,
        help="Action to perform (list, install-from-source, package, install, uninstall, "
        "generate-manifests, runtime-install, runtime-uninstall, runtime-list, "
        "registry-install, publish)",
    ),
    extension_id: Optional[str] = typer.Option(None, "--extension-id", help="Extension ID"),
    package_path: Optional[str] = typer.Option(None, "--package-path", help="Package path"),
    source_dir: str = typer.Option("extensions", "--source-dir", help="Source directory"),
    all_extensions: bool = typer.Option(False, "--all", help="All extensions"),
    canister: Optional[str] = typer.Option(None, "--canister", "-c", help="Target realm canister ID"),
    network: str = typer.Option("local", "--network", "-n", help="Network: local, ic"),
    identity: Optional[str] = typer.Option(None, "--identity", help="dfx identity to use"),
    raw_json: bool = typer.Option(False, "--json", help="Output raw JSON (for runtime-list)"),
    registry: Optional[str] = typer.Option(
        None, "--registry", "-r",
        help="File registry canister ID (registry-install or publish)",
    ),
    version: Optional[str] = typer.Option(
        None, "--version", "-v",
        help="Version to install/publish (default: registry-install→latest, "
        "publish→manifest.json)",
    ),
    bundle_path: Optional[str] = typer.Option(
        None, "--bundle-path",
        help="Pre-built ESM frontend bundle (override <source>/frontend-rt/dist/index.js for publish)",
    ),
    namespace_prefix: str = typer.Option(
        "ext", "--namespace-prefix",
        help="Registry namespace prefix for publish (default: ext)",
    ),
    skip_publish_marker: bool = typer.Option(
        False, "--skip-publish",
        help="Upload files but do not call publish_namespace (publish action only)",
    ),
    frontend_canister: Optional[str] = typer.Option(
        None, "--frontend-canister",
        help="Realm frontend asset canister (runtime-install: upload frontend-rt bundle same-origin)",
    ),
) -> None:
    """Manage Realm extensions."""
    extension_command(
        action,
        extension_id,
        package_path,
        source_dir,
        all_extensions,
        canister,
        network,
        identity,
        raw_json,
        registry,
        version,
        bundle_path,
        namespace_prefix,
        skip_publish_marker,
        frontend_canister,
    )


@app.command("codex", hidden=True)
def codex(
    action: str = typer.Argument(
        ...,
        help="Action: runtime-install, runtime-uninstall, runtime-list, registry-install, publish",
    ),
    codex_id: Optional[str] = typer.Option(None, "--codex-id", help="Codex package ID"),
    source_dir: str = typer.Option(".", "--source-dir", help="Source directory for codex package"),
    canister: Optional[str] = typer.Option(None, "--canister", "-c", help="Target realm canister ID"),
    network: str = typer.Option("local", "--network", "-n", help="Network: local, ic"),
    identity: Optional[str] = typer.Option(None, "--identity", help="dfx identity to use"),
    raw_json: bool = typer.Option(False, "--json", help="Output raw JSON (for runtime-list)"),
    registry: Optional[str] = typer.Option(
        None, "--registry", "-r",
        help="File registry canister ID (registry-install or publish)",
    ),
    version: Optional[str] = typer.Option(
        None, "--version", "-v",
        help="Version to install/publish (default: registry-install→latest, "
        "publish→manifest.json)",
    ),
    run_init: bool = typer.Option(True, "--run-init/--no-init", help="Run init.py after install"),
    namespace_prefix: str = typer.Option(
        "codex", "--namespace-prefix",
        help="Registry namespace prefix for publish (default: codex)",
    ),
    skip_publish_marker: bool = typer.Option(
        False, "--skip-publish",
        help="Upload files but do not call publish_namespace (publish action only)",
    ),
) -> None:
    """Manage Realm codex packages."""
    codex_command(
        action,
        codex_id,
        source_dir,
        canister,
        network,
        identity,
        raw_json,
        registry,
        version,
        run_init,
        namespace_prefix,
        skip_publish_marker,
    )


@app.command("wasm", hidden=True)
def wasm(
    action: str = typer.Argument(..., help="Action: list, pull, install, hash"),
    registry: Optional[str] = typer.Option(None, "--registry", "-r", help="File registry canister ID"),
    installer: Optional[str] = typer.Option(
        None, "--installer", "-I",
        help="realm_installer canister ID (required for install/hash)",
    ),
    target: Optional[str] = typer.Option(
        None, "--target", "-t",
        help="Target canister ID to install onto (required for install)",
    ),
    version: Optional[str] = typer.Option(None, "--version", "-v", help="Version to pull/install (default: latest)"),
    wasm_path: Optional[str] = typer.Option(
        None, "--wasm-path",
        help="Override default 'realm-base-{version}.wasm.gz' path in the registry",
    ),
    namespace: str = typer.Option(
        "wasm", "--namespace", help="Registry namespace for the WASM (default: wasm)"
    ),
    mode: str = typer.Option(
        "upgrade", "--mode", "-m",
        help="Install mode for install action: install | reinstall | upgrade",
    ),
    init_arg_b64: str = typer.Option(
        "", "--init-arg-b64",
        help="Optional base64-encoded candid blob to pass as init/post-upgrade arg",
    ),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output file path"),
    network: str = typer.Option("ic", "--network", "-n", help="Network: local, ic"),
    identity: Optional[str] = typer.Option(None, "--identity", help="dfx identity to use"),
) -> None:
    """Manage realm base WASM (Layer 1).

    Subcommands:
      list     — list base WASMs available in the file registry
      pull     — download a base WASM from the registry to disk
      install  — stream a base WASM from the registry to a target canister
                 via the realm_installer (no local download)
      hash     — compute the sha256 of a registry-stored WASM through the
                 realm_installer (smoke test before install)

    Examples:
      realms wasm list -r <registry> --network staging
      realms wasm pull -r <registry> --network staging
      realms wasm install -r <registry> -I <installer> -t <target> \\
          --version 0.1.0 --mode upgrade --network staging
      realms wasm hash -r <registry> -I <installer> --version 0.1.0 \\
          --network staging
    """
    wasm_command(
        action,
        registry,
        installer,
        target,
        version,
        wasm_path,
        namespace,
        mode,
        init_arg_b64,
        output,
        network,
        identity,
    )


# ---------------------------------------------------------------------------
# `realms installer health` — quick check against realm_installer
# (end-to-end on-chain deploy_realm was removed; use the registry queue).
# ---------------------------------------------------------------------------
installer_app = typer.Typer(
    name="installer",
    help="realm_installer utilities (e.g. health).",
)
app.add_typer(installer_app, name="installer", rich_help_panel="Lifecycle")


@installer_app.command("health")
def installer_health(
    installer: str = typer.Option(
        ..., "--installer", "-I",
        help="realm_installer canister ID",
    ),
    network: str = typer.Option(
        "ic", "--network", "-n", help="Network: local, staging, demo, test, ic"
    ),
    identity: Optional[str] = typer.Option(
        None, "--identity", help="dfx identity to use"
    ),
) -> None:
    """Call realm_installer.health (liveness + chunk limits)."""
    installer_health_command(
        installer=installer, network=network, identity=identity,
    )


@app.command("new", rich_help_panel="Lifecycle")
def new(
    spec_file: Optional[str] = typer.Argument(
        None,
        help="Optional spec JSON (paths relative to the spec file). Flags override spec fields.",
    ),
    identity: str = typer.Option(
        ...,
        "--identity",
        "-i",
        help=(
            "Identity that pays credits, is manifest.founder, and runs setup. "
            "II-linked, or a dfx key (e.g. deployer) together with --co-admin."
        ),
    ),
    network: Optional[str] = typer.Option(
        None,
        "--network",
        "-n",
        help=(
            "Environment: test | staging | demo | ic. "
            "Default: `name` from --gaas-config."
        ),
    ),
    name: Optional[str] = typer.Option(None, "--name", help="Realm name (≥3 chars)"),
    slug: Optional[str] = typer.Option(
        None, "--slug", help="Federation slug (default: slugify name, ≥3)"
    ),
    gos: Optional[str] = typer.Option(
        None, "--gos", help="GOS implementation (v1: realms-gos only)"
    ),
    version: Optional[str] = typer.Option(
        None, "--version", help="GOS deploy version (default: main)"
    ),
    subnet: Optional[str] = typer.Option(
        None,
        "--subnet",
        help="automatic | european | <subnet-id> (default: automatic)",
    ),
    codex: Optional[str] = typer.Option(None, "--codex", help="Codex package id (e.g. agora)"),
    codex_version: Optional[str] = typer.Option(
        None, "--codex-version", help="Codex version (omit for registry latest)"
    ),
    logo: Optional[str] = typer.Option(None, "--logo", help="Logo image path (≤ 1.5 MiB)"),
    background: Optional[str] = typer.Option(
        None, "--background", help="Background image path (≤ 1.5 MiB)"
    ),
    primary: Optional[str] = typer.Option(None, "--primary", help="Primary color #RRGGBB"),
    manifesto: Optional[str] = typer.Option(
        None, "--manifesto", help="Realm manifesto (max 256 chars)"
    ),
    welcome: Optional[str] = typer.Option(
        None, "--welcome", help="Welcome message (max 1024 chars)"
    ),
    token_symbol: Optional[str] = typer.Option(
        None,
        "--token-symbol",
        help="Treasury token symbol: REALMS, ckBTC, ckUSDC, ckEURC, or any "
        "symbol together with --token-canister (default: REALMS)",
    ),
    token_canister: Optional[str] = typer.Option(
        None, "--token-canister", help="Existing token canister id"
    ),
    config: Optional[str] = typer.Option(
        None, "--config", help="Path to JSON config object (scaling / auto_scale_enabled)"
    ),
    data: Optional[str] = typer.Option(
        None, "--data", help="Path to GGG entity JSON (same as realms db import)"
    ),
    members: Optional[int] = typer.Option(
        None, "--members", help="Geister agents to generate and join_realm (default 0)"
    ),
    open_registration: bool = typer.Option(
        False,
        "--open-registration",
        help="Allow join without invite (overrides spec to true)",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    resume: Optional[str] = typer.Option(
        None, "--resume", help="Continue from an existing installer job_id"
    ),
    from_stage: Optional[str] = typer.Option(
        None,
        "--from-stage",
        help=(
            "Resume from this stage (skip earlier ones). validate always runs. "
            "GaaS: validate, credits, confirm, deploy, poll, setup, co-admin, "
            "config, data, members. Standalone: validate, confirm, deploy, "
            "setup, co-admin, config, data, members. Accepts a name or "
            "1-based index (like gaas new --from-phase)."
        ),
    ),
    co_admin: Optional[str] = typer.Option(
        None,
        "--co-admin",
        help=(
            "II principal to register as a second admin after launch "
            "(admin profile + root org). Required when --identity is a dfx "
            "deploy key (deployer, my_dev_identity_1)."
        ),
    ),
    log_file: Optional[Path] = typer.Option(
        None,
        "--log-file",
        help=(
            "Write the full run transcript (console + dfx/icp output) to this path. "
            "A file path gets _YYYYMMDD_HHMMSS before the extension; a directory "
            "gets realms-new-<env>_YYYYMMDD_HHMMSS.log. "
            "Default: <repo>/logs/realms-new-<env>_YYYYMMDD_HHMMSS.log"
        ),
    ),
    gaas_config: Optional[Path] = typer.Option(
        None,
        "--gaas-config",
        help=(
            "GaaS config JSON from `gaas new` "
            "(registry + installer canister IDs, domain, env name). "
            "Required for --deploy-mode=gaas. Default write path is "
            "`<gos-repo>/logs/gaas-config-<env>_YYYYMMDD_HHMMSS.json`."
        ),
    ),
    deploy_mode: str = typer.Option(
        "gaas",
        "--deploy-mode",
        help=(
            "gaas: enqueue via the GaaS registry/installer (needs --gaas-config). "
            "standalone: mint realm_backend + realm_frontend from a GitHub "
            "release; not registered with any GaaS."
        ),
    ),
) -> None:
    """Create a live realm.

    --deploy-mode=gaas (default) mirrors the *.gos.earth wizard: charges 5
    registry credits, enqueues request_deployment, then completes founder
    setup (codex / token / branding / launch). --from-stage resumes after a
    named stage (same idea as gaas new --from-phase).

    --deploy-mode=standalone deploys two canisters from the Realms GitHub
    release (or --version latest). No credits, no installer, not listed on
    any GaaS portal.

    When --identity is a dfx key, pass --co-admin <your-II-principal> (copy it
    from the browser after logging into *.gos.earth). You log in as that II
    user with real admin rights; deployer remains the founder User.

    Example:
      gaas new environments/demo.json --identity deployer --network ic --yes
      realms new --gaas-config logs/gaas-config-demo_YYYYMMDD_HHMMSS.json \\
        --identity deployer --co-admin <ii-principal> --codex agora --name Acme --yes
      realms new --deploy-mode standalone --network demo --identity deployer \\
        --codex agora --name Acme --yes
      realms new spec.json --gaas-config environments/test.json \\
        --identity deployer --slug acme --from-stage setup --yes
    """
    start_run_log(
        network or (gaas_config.stem if gaas_config else "new"),
        log_file=log_file,
        command="realms new",
    )
    print_log_path()
    try:
        new_command(
            spec_file=spec_file,
            identity=identity,
            network=network or "",
            name=name,
            slug=slug,
            gos=gos,
            version=version,
            subnet=subnet,
            codex=codex,
            codex_version=codex_version,
            logo=logo,
            background=background,
            primary=primary,
            manifesto=manifesto,
            welcome=welcome,
            token_symbol=token_symbol,
            token_canister=token_canister,
            config=config,
            data=data,
            members=members,
            open_registration=True if open_registration else None,
            yes=yes,
            resume=resume,
            from_stage=from_stage,
            co_admin=co_admin,
            gaas_config=str(gaas_config) if gaas_config else None,
            deploy_mode=deploy_mode,
        )
    finally:
        print_log_path()
        stop_run_log()


@app.command("deploy", rich_help_panel="Lifecycle")
def deploy(
    network: Optional[str] = typer.Option(
        None, "--network", "-n",
        help="dfx network (default: local)",
    ),
    mode: Optional[str] = typer.Option(
        None, "--mode", "-m",
        help="Deploy mode: auto, upgrade, reinstall",
    ),
    identity: Optional[str] = typer.Option(
        None, "--identity", "-i",
        help="Identity name or PEM file path for deployment",
    ),
    folder: Optional[str] = typer.Option(
        None, "--folder", "-f",
        help="Path to a generated realm folder (auto-detected under the realms folder)",
    ),
    clean: bool = typer.Option(
        False, "--clean",
        help="Clean deployment (wipes state)",
    ),
    plain_logs: bool = typer.Option(
        False, "--plain-logs",
        help="Show full verbose output instead of progress UI",
    ),
    registry: Optional[str] = typer.Option(
        None, "--registry",
        help="Registry canister ID for realm registration",
    ),
) -> None:
    """dfx-deploy a generated realm folder to a local replica.

    \b
      realms deploy --folder ./my_realms/realm_dominion
      realms deploy --folder ./my_realms/realm_dominion --mode reinstall

    \b
    Fleet environments are converged by `casals up` from casals.json
    (docs/OPERATIONS.md); new realms on a live GOS go through
    `realms new --gaas-config`.
    """
    deploy_command(
        config_file=None,
        folder=folder,
        network=network or "local",
        clean=clean,
        identity=identity,
        mode=mode or "auto",
        plain_logs=plain_logs,
        registry=registry,
    )


@app.command("test", rich_help_panel="Development")
def test(
    path: str = typer.Argument(".", help="Path to codices directory, realm directory, or test file"),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show verbose output including passing test details"
    ),
) -> None:
    """Run codex tests locally using the mock ggg framework.

    Examples:
        realms test codices/agora
        realms test codices/
        realms test codices/agora/tests/test_financial_setup.py
    """
    test_command(path, verbose)


@app.command("status", rich_help_panel="Utility")
def status(
    network: Optional[str] = typer.Option(
        None, "--network", "-n", help="Network to use (overrides context)"
    ),
    canister: Optional[str] = typer.Option(
        None, "--canister", "-c", help="Canister name to check (overrides context)"
    ),
) -> None:
    """Show status of current Realms project."""
    console.print("[bold blue]📊 Realms Project Status[/bold blue]\n")

    # Get effective network and canister from context
    effective_network, effective_canister = get_effective_network_and_canister(
        network, canister
    )

    # Show current context
    current_realm = get_current_realm()
    if current_realm:
        console.print(f"[dim]Using realm context: {current_realm}[/dim]")
    console.print(
        f"[dim]Network: {effective_network}, Canister: {effective_canister}[/dim]\n"
    )

    # Check dependencies
    console.print("[bold]Dependencies:[/bold]")
    if check_dependencies():
        console.print("  ✅ All required tools are available")
    else:
        console.print("  ❌ Some dependencies are missing")
        return

    # Try to call backend canister status
    console.print("\n[bold]Canister Status:[/bold]")
    try:
        import subprocess

        cmd = ["dfx", "canister", "call", effective_canister, "status"]
        if effective_network != "local":
            cmd.extend(["--network", effective_network])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            console.print("  ✅ Backend canister is responding")
            # Parse the response if needed
            if "success = true" in result.stdout:
                console.print("  ✅ Backend status: healthy")
        else:
            console.print("  ❌ Backend canister not responding")
            if result.stderr:
                console.print(f"      Error: {result.stderr.strip()}")
    except Exception as e:
        console.print(f"  ❌ Could not check backend status: {e}")

    # Check dfx replica status
    console.print("\n[bold]dfx Replica:[/bold]")
    try:
        import subprocess

        cmd = ["dfx", "ping", effective_network]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            console.print("  ✅ dfx replica is running")
        else:
            console.print("  ❌ dfx replica is not running")
    except Exception:
        console.print("  ❌ dfx replica is not running")


# Create mundus subcommand group
mundus_app = typer.Typer(name="mundus", help="Mundus deployment operations")
app.add_typer(mundus_app, name="mundus", rich_help_panel="Lifecycle")


@mundus_app.command("deploy")
def mundus_deploy(
    descriptor: str = typer.Argument(
        ..., help="Path to a mundus descriptor YAML (network, infra ids, parameters, realms)"
    ),
    network: str = typer.Option(
        "", "--network", "-n", help="Override network from descriptor"
    ),
    deploy_mode: str = typer.Option(
        "upgrade", "--mode", "-m", help="Deploy mode: upgrade, reinstall, install"
    ),
    artifact_version: str = typer.Option(
        "latest", "--version", "-v", help="Artifact version: 'latest', semver (e.g. 0.3.2), or local path"
    ),
    realm_filter: str = typer.Option(
        "", "--realm", "-r", help="Deploy only this realm (by name or display_name)"
    ),
    canister_filter: str = typer.Option(
        "", "--canister", "-c", help="Deploy only 'backend' or 'frontend' (default: both)"
    ),
    skip_extensions: bool = typer.Option(
        False, "--skip-extensions", help="Skip all extension and codex installation"
    ),
    extensions_filter: str = typer.Option(
        "", "--extensions", help="Extensions to install: comma-separated IDs, or 'none' to skip (default: all)"
    ),
    codices_filter: str = typer.Option(
        "", "--codices", help="Codices to install: comma-separated IDs, or 'none' to skip (default: all)"
    ),
    build_variant: str = typer.Option(
        "",
        "--variant",
        help="Realm build variant: production or test (default: test on test network, production otherwise)",
    ),
) -> None:
    """Redeploy existing realms through the GOS queue from a mundus descriptor.

    \b
    The descriptor carries everything environment-specific — the CLI holds no
    per-network table:
      network: ic
      infra:
        registry_canister_id: <realm-registry>     # from `casals export`
        installer_canister_id: <realm-installer>
        file_registry_canister_id: <file-registry>
        ii_derivation_origin: https://gos.earth    # optional
      parameters: {TEST_MODE: false}               # optional test flags
      mundus:
        - name: agora
          canister_id: <backend>
          frontend_canister_id: <frontend>
          manifest: realms/agora/manifest.json

    \b
    Examples:
      realms mundus deploy mundus.yml
      realms mundus deploy mundus.yml --realm agora --canister backend
      realms mundus deploy mundus.yml --canister frontend --skip-extensions
      realms mundus deploy mundus.yml --extensions voting,vault --codices none
    """
    ext_names = None
    codex_names = None
    if skip_extensions:
        ext_names = []
        codex_names = []
    else:
        if extensions_filter:
            ext_names = [] if extensions_filter.strip().lower() == "none" else [
                s.strip() for s in extensions_filter.split(",") if s.strip()
            ]
        if codices_filter:
            codex_names = [] if codices_filter.strip().lower() == "none" else [
                s.strip() for s in codices_filter.split(",") if s.strip()
            ]
    mundus_deploy_descriptor_command(
        descriptor, network, deploy_mode, artifact_version,
        realm_filter=realm_filter, canister_filter=canister_filter,
        skip_extensions=skip_extensions,
        extension_names=ext_names, codex_names=codex_names,
        build_variant=build_variant,
    )


# Create files subcommand group
files_app = typer.Typer(name="files", help="File registry operations")
app.add_typer(files_app, name="files", rich_help_panel="Lifecycle")
app.add_typer(domains_app, name="domains", rich_help_panel="Lifecycle")


@files_app.command("publish")
def files_publish(
    network: str = typer.Option(
        "ic", "--network", "-n", help="dfx network the file registry lives on (ic, local)"
    ),
    registry: Optional[str] = typer.Option(
        None, "--registry", "-r", help="File registry canister ID (auto-resolved from network)"
    ),
    identity: Optional[str] = typer.Option(
        None, "--identity", help="dfx identity to use"
    ),
    extensions_only: bool = typer.Option(
        False, "--extensions-only", help="Only publish extensions"
    ),
    codices_only: bool = typer.Option(
        False, "--codices-only", help="Only publish codices"
    ),
    extensions_filter: str = typer.Option(
        "", "--extensions", help="Specific extensions to publish, comma-separated (default: all)"
    ),
    codices_filter: str = typer.Option(
        "", "--codices", help="Specific codices to publish, comma-separated (default: all)"
    ),
) -> None:
    """Publish extensions and codices to the file registry."""
    ext_names = [s.strip() for s in extensions_filter.split(",") if s.strip()] if extensions_filter else None
    codex_names = [s.strip() for s in codices_filter.split(",") if s.strip()] if codices_filter else None
    files_publish_command(network, registry, identity, extensions_only, codices_only,
                          extension_names=ext_names, codex_names=codex_names)


@files_app.command("publish-assistant")
def files_publish_assistant(
    source_dir: str = typer.Option(
        ..., "--source-dir", help="Path to assistant package (manifest.json + prompts/)"
    ),
    network: str = typer.Option(
        "ic", "--network", "-n", help="dfx network the file registry lives on (ic, local)"
    ),
    registry: Optional[str] = typer.Option(
        None, "--registry", "-r", help="File registry canister ID (auto-resolved from network)"
    ),
    identity: Optional[str] = typer.Option(
        None, "--identity", help="dfx identity to use"
    ),
    version: Optional[str] = typer.Option(
        None, "--version", "-v", help="Override version from manifest.json"
    ),
) -> None:
    """Publish an AI assistant package to the file registry."""
    files_publish_assistant_command(network, registry, identity, source_dir, version)


@files_app.command("publish-branding")
def files_publish_branding(
    network: str = typer.Option(
        "ic", "--network", "-n", help="dfx network the file registry lives on (ic, local)"
    ),
    registry: Optional[str] = typer.Option(
        None, "--registry", "-r", help="File registry canister ID (auto-resolved from network)"
    ),
    identity: Optional[str] = typer.Option(
        None, "--identity", help="dfx identity to use"
    ),
    realms_filter: str = typer.Option(
        "", "--realms", help="Specific realms to publish branding for, comma-separated (default: all)"
    ),
) -> None:
    """Publish per-realm branding images (logo, background) to the file registry."""
    realm_names = [s.strip() for s in realms_filter.split(",") if s.strip()] if realms_filter else None
    files_publish_branding_command(network, registry, identity, realm_names=realm_names)


@files_app.command("publish-release")
def files_publish_release(
    network: str = typer.Option(
        "ic", "--network", "-n", help="dfx network the file registry lives on (ic, local)"
    ),
    family: str = typer.Option(
        "realm", "--family", help="Artifact family base name (default: realm)"
    ),
    version: str = typer.Option(
        ..., "--version", "-v", help="Release version, e.g. 1.4.0"
    ),
    backend_wasm: Optional[str] = typer.Option(
        None, "--backend-wasm", help="Path to the backend WASM (.wasm.gz)"
    ),
    frontend_dist: Optional[str] = typer.Option(
        None, "--frontend-dist", help="Path to the built frontend dist/ directory"
    ),
    registry: Optional[str] = typer.Option(
        None, "--registry", "-r", help="file_registry canister ID (auto-resolved from network)"
    ),
    identity: Optional[str] = typer.Option(
        None, "--identity", help="dfx identity to use"
    ),
    casals: Optional[str] = typer.Option(
        None, "--casals", help="Casals canister ID (authorize WASMs after upload)"
    ),
    assets_wasm: Optional[str] = typer.Option(
        None, "--assets-wasm", help="Certified-assets canister WASM (needed to authorize the frontend in Casals)"
    ),
    registry_backend: Optional[str] = typer.Option(
        None, "--registry-backend", help="realm_registry_backend canister ID (publish_version)"
    ),
) -> None:
    """Publish a realm release (backend WASM + frontend bundle) fully on-chain to
    file_registry, then optionally authorize the WASMs in Casals and record the
    version in the realm catalog."""
    files_publish_release_command(
        network=network, family=family, version=version,
        backend_wasm=backend_wasm, frontend_dist=frontend_dist,
        registry=registry, identity=identity, casals=casals,
        assets_wasm=assets_wasm, registry_backend=registry_backend,
    )


@files_app.command("reset")
def files_reset(
    network: str = typer.Option(
        "ic", "--network", "-n", help="dfx network the file registry lives on (ic, local)"
    ),
    registry: Optional[str] = typer.Option(
        None, "--registry", "-r", help="File registry canister ID (auto-resolved from network)"
    ),
    identity: Optional[str] = typer.Option(
        None, "--identity", help="dfx identity to use"
    ),
) -> None:
    """Wipe and reinstall the file registry canister."""
    files_reset_command(network, registry, identity)


@files_app.command("build")
def files_build(
    extensions_filter: str = typer.Option(
        "", "--extensions", help="Specific extensions to build, comma-separated (default: all)"
    ),
) -> None:
    """Build frontend-rt bundles for extensions (npm ci && npm run build)."""
    ext_names = [s.strip() for s in extensions_filter.split(",") if s.strip()] if extensions_filter else None
    files_build_command(extension_names=ext_names)


# Create realm subcommand group
realm_app = typer.Typer(name="realm", help="Realm-specific operations")
app.add_typer(realm_app, name="realm", rich_help_panel="Lifecycle")


@realm_app.command("create")
def realm_create(
    output_dir: str = typer.Option(
        REALM_FOLDER, "--output-dir", help="Output directory"
    ),
    realm_name: str = typer.Option(
        "Generated Demo Realm", "--realm-name", help="Name of the realm"
    ),
    manifest: Optional[str] = typer.Option(
        "examples/demo/realm1/manifest.json", "--manifest", help="Path to realm manifest.json (defaults to realm1 template)"
    ),
    random: bool = typer.Option(
        False, "--random/--no-random", help="Generate random realm data"
    ),
    members: Optional[int] = typer.Option(
        None, "--members", help="Number of members to generate (overrides manifest)"
    ),
    organizations: Optional[int] = typer.Option(
        None, "--organizations", help="Number of organizations to generate (overrides manifest)"
    ),
    transactions: Optional[int] = typer.Option(
        None, "--transactions", help="Number of transactions to generate (overrides manifest)"
    ),
    disputes: Optional[int] = typer.Option(
        None, "--disputes", help="Number of disputes to generate (overrides manifest)"
    ),
    seed: Optional[int] = typer.Option(
        None, "--seed", help="Random seed for reproducible generation (overrides manifest)"
    ),
    network: str = typer.Option(
        "local", "--network", help="Target network for deployment"
    ),
    deploy: bool = typer.Option(
        False, "--deploy", help="Deploy the realm after creation"
    ),
    identity: Optional[str] = typer.Option(
        None, "--identity", help="Path to identity PEM file or identity name for dfx"
    ),
    mode: str = typer.Option(
        "auto", "--mode", "-m", help="Deploy mode: 'auto', 'upgrade' or 'reinstall' (auto picks install/upgrade)"
    ),
    bare: bool = typer.Option(
        False, "--bare", help="Create minimal realm (canisters only, no extensions or data)"
    ),
    no_demo_data: bool = typer.Option(
        False, "--no-demo-data", help="Skip generating demo/fake data (users, orgs, accounting). Extensions and codex files are still included."
    ),
    plain_logs: bool = typer.Option(
        False, "--plain-logs", help="Show full verbose output instead of progress UI during deployment"
    ),
    registry: Optional[str] = typer.Option(
        None, "--registry", help="Registry canister ID for realm registration during deployment"
    ),
) -> None:
    """Create a new realm. Use --manifest for template or flags for custom configuration."""
    create_command(
        output_dir,
        realm_name,
        manifest,
        random,
        members,
        organizations,
        transactions,
        disputes,
        seed,
        network,
        deploy,
        identity,
        mode,
        bare,
        no_demo_data,
        plain_logs,
        registry=registry,
    )


@realm_app.command("deploy")
def realm_deploy(
    folder: Optional[str] = typer.Option(
        None, "--folder", "-f", help="Path to realm folder to deploy"
    ),
    config_file: Optional[str] = typer.Option(
        None, "--config-file", help="Path to custom dfx.json"
    ),
    network: str = typer.Option(
        "local", "--network", "-n", help="Network to deploy to"
    ),
    clean: bool = typer.Option(
        False, "--clean", help="Clean deployment (wipes state)"
    ),
    identity: Optional[str] = typer.Option(
        None, "--identity", help="Identity file or name for IC deployment"
    ),
    mode: str = typer.Option(
        "auto", "--mode", "-m", help="Deployment mode (auto, upgrade, reinstall)"
    ),
    plain_logs: bool = typer.Option(
        False, "--plain-logs", help="Show full verbose output instead of progress UI"
    ),
    registry: Optional[str] = typer.Option(
        None, "--registry", help="Registry canister ID for realm registration"
    ),
) -> None:
    """dfx-deploy a generated realm folder: realms realm deploy --folder <path> --network <net>."""
    deploy_command(config_file, folder, network, clean, identity, mode, plain_logs, registry=registry)


@realm_app.command("call")
def realm_call(
    realm_ref: str = typer.Argument(help="Realm canister ID or name (e.g., 'Dominion' or 'xxxxx-xxxxx-xxxxx-xxxxx-cai')"),
    method: str = typer.Argument(help="Method name or 'extension' for extension calls"),
    args: str = typer.Argument("()", help="Candid arguments (e.g., '(\"admin\")') or extension name for extension calls"),
    function_name: Optional[str] = typer.Argument(None, help="Function name (only for extension calls)"),
    function_args: Optional[str] = typer.Argument(None, help="JSON arguments (only for extension calls)"),
    network: Optional[str] = typer.Option(
        None, "--network", "-n", help="Network to use (overrides context)"
    ),
    output: str = typer.Option(
        "json", "--output", "-o", help="Output format: json or candid"
    ),
    async_call: bool = typer.Option(
        False, "--async", "-a", help="Use async extension call (for extension calls only)"
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show verbose output"
    ),
) -> None:
    """
    Call a backend method or extension function on a realm.
    
    Examples:
        # Call backend method on a realm by name
        realms realm call Dominion status --network staging
        realms realm call Dominion join_realm '("admin")' --network staging
        
        # Call backend method on a realm by canister ID
        realms realm call xxxxx-xxxxx-xxxxx-xxxxx-cai status --network staging
        
        # Call extension function
        realms realm call Dominion extension member_dashboard check_invoice_payment '{"invoice_id": "x"}' --network staging
        
        # Output format (default: json)
        realms realm call Dominion status --output candid --network staging
    """
    import json as json_module
    import subprocess
    import sys
    
    # Validate output format
    if output not in ("json", "candid"):
        console.print(f"[red]❌ Invalid output format: {output}. Use 'json' or 'candid'[/red]")
        raise typer.Exit(1)
    
    # Get effective network
    effective_network, _ = get_effective_network_and_canister(network, None, quiet=not verbose)
    
    # Resolve realm reference to canister ID
    try:
        backend_canister_id, realm_name = resolve_realm_ref_to_canister_id(
            realm_ref, effective_network
        )
    except ValueError as e:
        console.print(f"[red]❌ {e}[/red]")
        raise typer.Exit(1)
    
    effective_canister = backend_canister_id
    
    if verbose:
        console.print(f"[dim]Realm: {realm_name}[/dim]")
        console.print(f"[dim]Backend Canister: {backend_canister_id}[/dim]")
        console.print(f"[dim]Network: {effective_network}[/dim]\n")
    
    # Check if this is an extension call
    if method == "extension":
        if not function_name or not function_args:
            console.print("[red]❌ Extension calls require: extension <ext_name> <function> <json_args>[/red]")
            console.print("Example: realms realm call Dominion extension member_dashboard check_invoice_payment '{\"invoice_id\": \"x\"}' --network staging")
            raise typer.Exit(1)
        
        extension_name = args  # args is actually the extension name in this case
        
        if verbose:
            console.print("[bold blue]🔧 Calling Extension Function[/bold blue]\n")
            console.print(f"Extension: [cyan]{extension_name}[/cyan]")
            console.print(f"Function: [cyan]{function_name}[/cyan]")
            console.print(f"Args: [dim]{function_args}[/dim]")
            console.print(f"Async: [dim]{async_call}[/dim]\n")
        
        try:
            # Validate JSON args
            json_module.loads(function_args)
        except json_module.JSONDecodeError as e:
            console.print(f"[red]❌ Invalid JSON arguments: {e}[/red]")
            raise typer.Exit(1)
        
        # Build extension call
        escaped_args = function_args.replace('"', '\\"')
        call_record = f"""(record {{ extension_name = "{extension_name}"; function_name = "{function_name}"; args = "{escaped_args}"; }})"""
        
        call_method = "extension_async_call" if async_call else "extension_sync_call"
        cmd = ["dfx", "canister", "call", "--network", effective_network, effective_canister, call_method, call_record]
        if output == "json":
            cmd.extend(["--output", "json"])
    else:
        # Regular backend method call
        if verbose:
            console.print("[bold blue]📞 Calling Backend Method[/bold blue]\n")
            console.print(f"Method: [cyan]{method}[/cyan]")
            console.print(f"Args: [dim]{args}[/dim]\n")
        
        cmd = ["dfx", "canister", "call", "--network", effective_network, effective_canister, method, args]
        if output == "json":
            cmd.extend(["--output", "json"])
    
    try:
        if verbose:
            console.print("[dim]Executing...[/dim]")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        
        if result.returncode == 0:
            if verbose:
                console.print("[green]✅ Call successful[/green]\n")
                console.print("[bold]Response:[/bold]")
            
            print(result.stdout.strip())
        else:
            if result.stderr:
                sys.stderr.write(f"Error: {result.stderr}\n")
            if result.stdout:
                sys.stderr.write(f"Output: {result.stdout}\n")
            raise typer.Exit(1)
    except subprocess.TimeoutExpired:
        sys.stderr.write("Error: Call timed out\n")
        raise typer.Exit(1)
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        raise typer.Exit(1)


# Create quarter subcommand group under realm
quarter_app = typer.Typer(name="quarter", help="Manage quarters within a realm")
realm_app.add_typer(quarter_app, name="quarter")


@quarter_app.command("create")
def realm_quarter_create(
    realm_ref: str = typer.Argument(help="Parent realm canister ID or name"),
    quarter_name: str = typer.Option(..., "--quarter-name", help="Name for the new quarter"),
    output_dir: str = typer.Option(
        REALM_FOLDER, "--output-dir", help="Output directory for quarter files"
    ),
    network: str = typer.Option("local", "--network", "-n", help="Network to deploy to"),
    deploy: bool = typer.Option(
        False, "--deploy", help="Deploy the quarter after creation"
    ),
    identity: Optional[str] = typer.Option(
        None, "--identity", help="Identity for IC deployment"
    ),
    mode: str = typer.Option(
        "auto", "--mode", "-m", help="Deploy mode: auto, upgrade, or reinstall"
    ),
    manifest: Optional[str] = typer.Option(
        None, "--manifest", help="Path to realm manifest.json for the quarter"
    ),
    bare: bool = typer.Option(
        False, "--bare", help="Create minimal quarter (canisters only, no extensions or data)"
    ),
    plain_logs: bool = typer.Option(
        False, "--plain-logs", help="Show full verbose output instead of progress UI"
    ),
    capital: bool = typer.Option(
        False, "--capital", help="Designate this quarter as the federation capital"
    ),
) -> None:
    """Create a new quarter backend and register it with a parent realm."""
    quarter_create_command(
        realm_ref, quarter_name, network, identity, mode,
        output_dir, deploy, manifest, bare, plain_logs, capital,
    )


@quarter_app.command("register")
def realm_quarter_register(
    realm_ref: str = typer.Argument(help="Parent realm canister ID or name"),
    quarter_name: str = typer.Option(..., "--quarter-name", help="Name for the quarter"),
    canister_id: str = typer.Option(..., "--canister-id", help="Canister ID of the quarter backend"),
    network: str = typer.Option("local", "--network", "-n", help="Network to use"),
) -> None:
    """Register an existing deployed canister as a quarter of a realm."""
    quarter_register_command(realm_ref, quarter_name, canister_id, network)


@quarter_app.command("list")
def realm_quarter_list(
    realm_ref: str = typer.Argument(help="Realm canister ID or name"),
    network: str = typer.Option("local", "--network", "-n", help="Network to use"),
) -> None:
    """List all quarters under a realm."""
    quarter_list_command(realm_ref, network)


@quarter_app.command("status")
def realm_quarter_status(
    realm_ref: str = typer.Argument(help="Realm canister ID or name"),
    quarter_ref: str = typer.Argument(help="Quarter name or canister ID"),
    network: str = typer.Option("local", "--network", "-n", help="Network to use"),
) -> None:
    """Show detailed status of a specific quarter."""
    quarter_status_command(realm_ref, quarter_ref, network)


@quarter_app.command("remove")
def realm_quarter_remove(
    realm_ref: str = typer.Argument(help="Realm canister ID or name"),
    quarter_ref: str = typer.Argument(help="Quarter name or canister ID to remove"),
    network: str = typer.Option("local", "--network", "-n", help="Network to use"),
) -> None:
    """Remove a quarter from a realm."""
    quarter_remove_command(realm_ref, quarter_ref, network)


@quarter_app.command("secede")
def realm_quarter_secede(
    quarter_ref: str = typer.Argument(help="Quarter canister ID to declare independence"),
    network: str = typer.Option("local", "--network", "-n", help="Network to use"),
) -> None:
    """Declare independence — secede a quarter from its federation."""
    from .commands.quarter import quarter_secede_command
    quarter_secede_command(quarter_ref, network)


@quarter_app.command("join-federation")
def realm_quarter_join_federation(
    quarter_ref: str = typer.Argument(help="Quarter canister ID to join a federation"),
    capital_canister_id: str = typer.Option(..., "--capital", help="Capital canister ID of the target federation"),
    as_capital: bool = typer.Option(False, "--as-capital", help="Join as the capital of the federation"),
    network: str = typer.Option("local", "--network", "-n", help="Network to use"),
) -> None:
    """Join an existing federation as a quarter."""
    from .commands.quarter import quarter_join_federation_command
    quarter_join_federation_command(quarter_ref, capital_canister_id, as_capital, network)


# Create registry subcommand group
registry_app = typer.Typer(name="registry", help="Realm registry operations")
app.add_typer(registry_app, name="registry", rich_help_panel="Lifecycle")

# Create registry realm subgroup
registry_realm_app = typer.Typer(name="realm", help="Manage realms in registry")
registry_app.add_typer(registry_realm_app, name="realm")

# Create registry billing subgroup
registry_billing_app = typer.Typer(name="billing", help="Manage credits and billing")
registry_app.add_typer(registry_billing_app, name="billing")


@registry_realm_app.command("add")
def registry_add(
    realm_name: str = typer.Option(..., "--realm-name", help="Human-readable realm name"),
    frontend_url: str = typer.Option("", "--frontend-url", help="Frontend canister URL (optional, will auto-derive)"),
    backend_url: str = typer.Option("", "--backend-url", help="Backend canister URL for status fetching"),
    network: str = typer.Option("local", "--network", "-n", help="Network to use"),
    registry_canister_id: str = typer.Option(
        "realm_registry_backend",
        "--registry-canister",
        help="Registry canister ID or name"
    ),
    realm_backend_canister: str = typer.Option(
        "realm_backend",
        "--realm-canister",
        help="This realm's backend canister ID or name"
    ),
) -> None:
    """
    Register this realm with the central registry.
    
    Calls the realm backend's register_realm_with_registry function which makes
    a secure inter-canister call to the registry. The registry uses the calling
    canister's principal as the realm ID.
    
    Example:
        realms registry realm add \\
            --realm-name "My Demo Governance Realm" \\
            --network local
    """
    import json
    import subprocess
    
    console.print(Panel.fit(
        "[bold cyan]🌐 Registering Realm with Central Registry[/bold cyan]",
        border_style="cyan"
    ))
    
    # Get canister IDs for registration
    frontend_canister_id = ""
    token_canister_id = ""
    nft_canister_id = ""
    
    # Auto-derive frontend canister ID and URL
    try:
        result = subprocess.run(
            ["dfx", "canister", "id", "realm_frontend", "--network", network],
            capture_output=True, text=True, check=True, timeout=5
        )
        frontend_canister_id = result.stdout.strip()
        
        if not frontend_url:
            # Format URL based on network
            if network == "ic":
                frontend_url = f"{frontend_canister_id}.ic0.app"
            elif network in ("staging", "demo", "test"):
                frontend_url = f"{frontend_canister_id}.icp0.io"
            else:  # local
                frontend_url = f"{frontend_canister_id}.localhost:8000"
            console.print(f"[dim]Auto-derived frontend URL: {frontend_url}[/dim]")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        console.print("[yellow]⚠️  Could not get frontend canister ID[/yellow]")
    
    # Try to get optional token_backend canister ID
    try:
        result = subprocess.run(
            ["dfx", "canister", "id", "token_backend", "--network", network],
            capture_output=True, text=True, check=True, timeout=5
        )
        token_canister_id = result.stdout.strip()
        console.print(f"[dim]Found token_backend: {token_canister_id}[/dim]")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass  # Optional canister
    
    # Try to get optional nft_backend canister ID
    try:
        result = subprocess.run(
            ["dfx", "canister", "id", "nft_backend", "--network", network],
            capture_output=True, text=True, check=True, timeout=5
        )
        nft_canister_id = result.stdout.strip()
        console.print(f"[dim]Found nft_backend: {nft_canister_id}[/dim]")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass  # Optional canister
    
    console.print(f"\n[cyan]Realm Name:[/cyan] {realm_name}")
    console.print(f"[cyan]Frontend URL:[/cyan] {frontend_url}")
    if frontend_canister_id:
        console.print(f"[cyan]Frontend Canister:[/cyan] {frontend_canister_id}")
    if backend_url:
        console.print(f"[cyan]Backend URL:[/cyan] {backend_url}")
    console.print(f"[dim]Network:[/dim] {network}\n")
    
    # Call realm backend's register_realm_with_registry (secure inter-canister call)
    # Pack canister IDs as pipe-delimited string: frontend_id|token_id|nft_id
    # NOTE: Cannot use JSON — basilisk's Candid encoder parses {} as record syntax
    canister_ids_packed = f"{frontend_canister_id}|{token_canister_id}|{nft_canister_id}"
    
    args = f'("{registry_canister_id}", "{realm_name}", "{frontend_url}", "{canister_ids_packed}")'
    cmd = ["dfx", "canister", "call", "--network", network, realm_backend_canister, "register_realm_with_registry", args]
    
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=True, timeout=60
        )
        
        # Parse the JSON response
        output = result.stdout.strip()
        if output.startswith("(") and output.endswith(")"):
            output = output[1:-1].strip()
        if output.startswith('"') and output.endswith('"'):
            output = output[1:-1]
        
        # Unescape the JSON string
        output = output.replace('\\"', '"').replace('\\n', '\n')
        
        try:
            response = json.loads(output)
            if response.get("success"):
                console.print(Panel(
                    f"[green]✅ Successfully registered realm with registry![/green]\n\n"
                    f"[cyan]Realm ID:[/cyan] {response.get('realm_id', 'N/A')}\n"
                    f"[cyan]Realm Name:[/cyan] {realm_name}\n"
                    f"[cyan]Frontend URL:[/cyan] {frontend_url}",
                    border_style="green",
                    title="Registration Complete"
                ))
            else:
                console.print(Panel(
                    f"[red]❌ Registration failed[/red]\n\n"
                    f"[yellow]Error:[/yellow] {response.get('error', 'Unknown error')}",
                    border_style="red",
                    title="Registration Failed"
                ))
                raise typer.Exit(1)
        except json.JSONDecodeError:
            # Check for Ok/Err in raw output
            if "success" in output.lower() or "Ok" in output:
                console.print(Panel(
                    f"[green]✅ Successfully registered realm with registry![/green]",
                    border_style="green",
                    title="Registration Complete"
                ))
            else:
                console.print(f"[yellow]⚠️  Unexpected response:[/yellow] {output}")
            
    except subprocess.CalledProcessError as e:
        console.print(f"[red]❌ Error: {e.stderr.strip()}[/red]")
        raise typer.Exit(1)
    except subprocess.TimeoutExpired:
        console.print("[red]❌ Command timed out[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]❌ Unexpected error: {str(e)}[/red]")
        raise typer.Exit(1)


@registry_realm_app.command("list")
def registry_list(
    network: str = typer.Option("local", "--network", "-n", help="Network to use"),
    canister_id: Optional[str] = typer.Option(
        None, "--canister-id", help="Registry canister ID"
    ),
) -> None:
    """List all realms in the registry."""
    registry_list_command(network, canister_id)


@registry_realm_app.command("get")
def registry_get(
    realm_id: str = typer.Option(..., "--id", help="Realm ID to retrieve"),
    network: str = typer.Option("local", "--network", "-n", help="Network to use"),
    canister_id: Optional[str] = typer.Option(
        None, "--canister-id", help="Registry canister ID"
    ),
) -> None:
    """Get a specific realm by ID."""
    registry_get_command(realm_id, network, canister_id)


@registry_realm_app.command("remove")
def registry_remove(
    realm_id: str = typer.Option(..., "--id", help="Realm ID to remove"),
    network: str = typer.Option("local", "--network", "-n", help="Network to use"),
    canister_id: Optional[str] = typer.Option(
        None, "--canister-id", help="Registry canister ID"
    ),
) -> None:
    """Remove a realm from the registry."""
    registry_remove_command(realm_id, network, canister_id)


@registry_realm_app.command("search")
def registry_search(
    query: str = typer.Option(..., "--query", help="Search query"),
    network: str = typer.Option("local", "--network", "-n", help="Network to use"),
    canister_id: Optional[str] = typer.Option(
        None, "--canister-id", help="Registry canister ID"
    ),
) -> None:
    """Search realms by name or ID."""
    registry_search_command(query, network, canister_id)


@registry_realm_app.command("count")
def registry_count(
    network: str = typer.Option("local", "--network", "-n", help="Network to use"),
    canister_id: Optional[str] = typer.Option(
        None, "--canister-id", help="Registry canister ID"
    ),
) -> None:
    """Get the total number of realms."""
    registry_count_command(network, canister_id)


@registry_app.command("create")
def registry_create(
    registry_name: Optional[str] = typer.Option(None, "--name", help="Registry name"),
    output_dir: str = typer.Option(".realms", "--output-dir", "-o", help="Base output directory"),
    network: str = typer.Option("local", "--network", "-n", help="Network to deploy to"),
    deploy: bool = typer.Option(
        False, "--deploy", help="Deploy the registry after creation"
    ),
    identity: Optional[str] = typer.Option(
        None, "--identity", help="Path to identity PEM file or identity name for dfx"
    ),
    mode: str = typer.Option(
        "auto", "--mode", "-m", help="Deploy mode: 'auto', 'upgrade' or 'reinstall' (auto picks install/upgrade)"
    ),
) -> None:
    """Create a new registry instance."""
    registry_create_command(registry_name, output_dir, network, deploy, identity, mode)


@registry_app.command("deploy")
def registry_deploy(
    folder: str = typer.Option(..., "--folder", "-f", help="Path to registry directory"),
    network: str = typer.Option("local", "--network", "-n", help="Network to deploy to"),
    mode: str = typer.Option("auto", "--mode", "-m", help="Deployment mode (auto, upgrade, reinstall)"),
    identity: Optional[str] = typer.Option(None, "--identity", help="Identity file for IC deployment"),
) -> None:
    """Deploy a registry instance."""
    registry_deploy_command(folder, network, mode, identity)


@registry_app.command("status")
def registry_status(
    network: str = typer.Option("local", "--network", "-n", help="Network to use"),
    canister_id: Optional[str] = typer.Option(
        None, "--canister-id", help="Registry canister ID"
    ),
) -> None:
    """Get the status of the registry backend canister."""
    registry_status_command(network, canister_id)


@registry_app.command("call")
def registry_call(
    method: str = typer.Argument(help="Backend method to call (e.g. status, list_realms)"),
    args: str = typer.Argument("()", help="Candid arguments for the method"),
    network: str = typer.Option("local", "--network", "-n", help="Network to use"),
    canister_id: Optional[str] = typer.Option(
        None, "--canister-id", help="Registry canister ID (auto-detected if not provided)"
    ),
    output: str = typer.Option("json", "--output", "-o", help="Output format: json or candid"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show verbose output"),
) -> None:
    """Call a method on the registry backend canister directly."""
    import subprocess
    import sys

    if output not in ("json", "candid"):
        console.print(f"[red]❌ Invalid output format: {output}. Use 'json' or 'candid'[/red]")
        raise typer.Exit(1)

    # Resolve registry canister ID
    effective_canister = canister_id or get_registry_canister_id(network)
    if not effective_canister:
        console.print("[red]❌ Could not determine registry canister ID. Use --canister-id.[/red]")
        raise typer.Exit(1)

    if verbose:
        console.print(f"[dim]Registry Canister: {effective_canister}[/dim]")
        console.print(f"[dim]Network: {network}[/dim]")
        console.print(f"[dim]Method: {method}[/dim]")
        console.print(f"[dim]Args: {args}[/dim]\n")

    cmd = ["dfx", "canister", "call", "--network", network, effective_canister, method, args]
    if output == "json":
        cmd.extend(["--output", "json"])

    try:
        import os
        env = os.environ.copy()
        env["DFX_WARNING"] = "-mainnet_plaintext_identity"
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env)
        if result.returncode == 0:
            print(result.stdout.strip())
        else:
            if result.stderr:
                sys.stderr.write(f"Error: {result.stderr}\n")
            raise typer.Exit(1)
    except subprocess.TimeoutExpired:
        sys.stderr.write("Error: Call timed out\n")
        raise typer.Exit(1)
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        raise typer.Exit(1)


# ============== Marketplace Commands ==============

marketplace_app = typer.Typer(name="marketplace", help="Extension marketplace operations")
app.add_typer(marketplace_app, name="marketplace", rich_help_panel="Lifecycle")


@marketplace_app.command("deploy")
def marketplace_deploy(
    network: str = typer.Option("local", "--network", "-n", help="Network to deploy to"),
    mode: str = typer.Option("auto", "--mode", "-m", help="Deploy mode: auto, install, upgrade, reinstall"),
    identity: Optional[str] = typer.Option(None, "--identity", help="dfx identity name or PEM file"),
    with_registry: Optional[bool] = typer.Option(
        None,
        "--with-registry/--no-with-registry",
        help="Also deploy file_registry alongside the marketplace (default: True for local, False elsewhere).",
    ),
    file_registry_canister_id: Optional[str] = typer.Option(
        None,
        "--file-registry-canister-id",
        help="file_registry canister id to wire into the marketplace (defaults to dfx-resolved id).",
    ),
    billing_service_principal: Optional[str] = typer.Option(
        None,
        "--billing-service-principal",
        help="Off-chain billing service principal allowed to call record_license_payment.",
    ),
) -> None:
    """Deploy the marketplace canisters from src/marketplace_*."""
    marketplace_deploy_command(
        network=network,
        mode=mode,
        identity=identity,
        with_registry=with_registry,
        file_registry_canister_id=file_registry_canister_id,
        billing_service_principal=billing_service_principal,
    )


@marketplace_app.command("call")
def marketplace_call(
    method: str = typer.Argument(help="Backend method to call (e.g. status, list_marketplace_extensions)"),
    args: str = typer.Argument("()", help="Candid arguments for the method"),
    network: str = typer.Option("local", "--network", "-n", help="Network to use"),
    canister_id: Optional[str] = typer.Option(None, "--canister-id", help="Override marketplace canister ID"),
    output: str = typer.Option("json", "--output", "-o", help="Output format: json or candid"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show verbose output"),
) -> None:
    """Call a method on the marketplace_backend canister directly."""
    marketplace_call_command(method, args, network, canister_id, output, verbose)


@marketplace_app.command("status")
def marketplace_status(
    network: str = typer.Option("local", "--network", "-n", help="Network to query"),
) -> None:
    """Pretty-print the marketplace_backend status()."""
    marketplace_status_command(network=network)


@marketplace_app.command("publish")
def marketplace_publish(
    marketplace: str = typer.Option(..., "--marketplace", "-m", help="marketplace_backend canister id"),
    registry: str = typer.Option(..., "--registry", "-r", help="File registry the packages were published to (`realms files publish --registry`)"),
    network: str = typer.Option("ic", "--network", "-n", help="Network (ic, local)"),
    identity: Optional[str] = typer.Option(None, "--identity", help="Identity licensed + reviewer on the marketplace (the sheet's operator)"),
    extensions_only: bool = typer.Option(False, "--extensions-only"),
    codices_only: bool = typer.Option(False, "--codices-only"),
    extensions_filter: str = typer.Option("", "--extensions", help="Comma-separated extension ids (default: all)"),
    codices_filter: str = typer.Option("", "--codices", help="Comma-separated codex ids (default: all)"),
    approve: bool = typer.Option(True, "--approve/--no-approve", help="Review-approve each listing after creating it"),
) -> None:
    """Create/update and approve a marketplace listing for every first-party extension and codex."""
    from .commands.marketplace_publish import marketplace_publish_command

    marketplace_publish_command(
        marketplace=marketplace, registry=registry, network=network, identity=identity,
        extensions_only=extensions_only, codices_only=codices_only,
        extensions_filter=extensions_filter, codices_filter=codices_filter, approve=approve,
    )


@marketplace_app.command("transfer")
def marketplace_transfer(
    marketplace: str = typer.Option(..., "--marketplace", "-m", help="marketplace_backend canister id"),
    to: str = typer.Option(..., "--to", help="Principal that becomes the owner"),
    network: str = typer.Option("ic", "--network", "-n", help="Network (ic, local)"),
    identity: Optional[str] = typer.Option(None, "--identity", help="Current owner, or a controller of the marketplace"),
    extensions: str = typer.Option("", "--extensions", help="Comma-separated extension ids (default: every listing the signer owns)"),
    codices: str = typer.Option("", "--codices", help="Comma-separated codex ids"),
    assistants: str = typer.Option("", "--assistants", help="Comma-separated assistant ids"),
) -> None:
    """Hand marketplace listings to another principal, keeping version and review state."""
    from .commands.marketplace_transfer import marketplace_transfer_command

    marketplace_transfer_command(
        marketplace=marketplace, to=to, network=network, identity=identity,
        extensions=extensions, codices=codices, assistants=assistants,
    )


# ============== Billing Commands ==============

@registry_billing_app.command("balance")
def billing_balance(
    principal_id: str = typer.Option(..., "--principal", "-p", help="User principal ID"),
    network: str = typer.Option("local", "--network", "-n", help="Network to use"),
    canister_id: Optional[str] = typer.Option(
        None, "--canister-id", help="Registry canister ID"
    ),
) -> None:
    """Get a user's credit balance."""
    billing_balance_command(principal_id, network, canister_id)


@registry_billing_app.command("add_credits")
def billing_add_credits(
    principal_id: str = typer.Option(..., "--principal", "-p", help="User principal ID"),
    amount: int = typer.Option(..., "--amount", "-a", help="Amount of credits to add"),
    stripe_session_id: str = typer.Option("", "--stripe-session", help="Stripe session ID"),
    description: str = typer.Option("Manual top-up", "--description", "-d", help="Transaction description"),
    network: str = typer.Option("local", "--network", "-n", help="Network to use"),
    canister_id: Optional[str] = typer.Option(
        None, "--canister-id", help="Registry canister ID"
    ),
) -> None:
    """Add credits to a user's balance."""
    billing_add_credits_command(principal_id, amount, stripe_session_id, description, network, canister_id)


@registry_billing_app.command("deduct_credits")
def billing_deduct_credits(
    principal_id: str = typer.Option(..., "--principal", "-p", help="User principal ID"),
    amount: int = typer.Option(..., "--amount", "-a", help="Amount of credits to deduct"),
    description: str = typer.Option("Manual deduction", "--description", "-d", help="Transaction description"),
    network: str = typer.Option("local", "--network", "-n", help="Network to use"),
    canister_id: Optional[str] = typer.Option(
        None, "--canister-id", help="Registry canister ID"
    ),
) -> None:
    """Deduct credits from a user's balance."""
    billing_deduct_credits_command(principal_id, amount, description, network, canister_id)


@registry_billing_app.command("status")
def billing_status(
    network: str = typer.Option("local", "--network", "-n", help="Network to use"),
    canister_id: Optional[str] = typer.Option(
        None, "--canister-id", help="Registry canister ID"
    ),
) -> None:
    """Get overall billing status across all users."""
    billing_status_command(network, canister_id)


@registry_billing_app.command("redeem_voucher")
def billing_redeem_voucher(
    principal_id: str = typer.Option(..., "--principal", "-p", help="User principal ID"),
    code: str = typer.Option(..., "--code", "-c", help="Voucher code to redeem"),
    billing_url: str = typer.Option(
        "https://billing.realmsgos.dev", "--billing-url", help="Billing service URL"
    ),
) -> None:
    """Redeem a voucher code to add credits to a user's balance."""
    billing_redeem_voucher_command(principal_id, code, billing_url)


@registry_realm_app.command("deploy-realm")
def registry_deploy_realm(
    realm_name: str = typer.Option(..., "--name", "-n", help="Name for the new realm"),
    network: str = typer.Option(
        "ic",
        "--network",
        help="dfx network the registry lives on (ic, local, …)",
    ),
    registry_canister: Optional[str] = typer.Option(
        None,
        "--registry-canister",
        help="realm_registry_backend canister id (required; see `casals export`)",
    ),
) -> None:
    """Enqueue a realm deploy via realm_registry_backend.request_deployment (uses dfx identity)."""
    realm_deploy_realm_command(realm_name, network, registry_canister)


@registry_realm_app.command("deploy-status")
def registry_deploy_status(
    job_id: str = typer.Option(..., "--job-id", "-j", help="Queue job id from deploy-realm"),
    network: str = typer.Option(
        "ic",
        "--network",
        help="dfx network the installer lives on (ic, local, …)",
    ),
    installer_canister: Optional[str] = typer.Option(
        None,
        "--installer-canister",
        help="realm_installer canister id (required; see `casals export`)",
    ),
    wait: bool = typer.Option(False, "--wait", "-w", help="Wait until job completes (polls periodically)"),
    poll_interval: int = typer.Option(10, "--poll-interval", help="Seconds between polls (with --wait)"),
    max_wait: int = typer.Option(900, "--max-wait", help="Maximum seconds to wait (with --wait)"),
) -> None:
    """Poll realm_installer for a deployment job status."""
    realm_deploy_status_command(job_id, network, installer_canister, wait, poll_interval, max_wait)


# Realm context management commands
@realm_app.command("ls")
def realm_ls(
    base_dir: Optional[str] = typer.Option(
        None, "--dir", "-d", help="Base directory to search for realms"
    ),
) -> None:
    """List all available realm folders."""
    from rich.table import Table
    
    console.print("[bold blue]📁 Available Realms[/bold blue]\n")
    
    realms = list_realm_folders(base_dir)
    
    if not realms:
        console.print(f"[yellow]No realm folders found in {base_dir or REALM_FOLDER}[/yellow]")
        console.print(f"\n[dim]💡 Create a realm with: realms create --realm-name <name>[/dim]")
        return
    
    # Get current realm folder to highlight it
    current_folder = get_current_realm_folder()
    
    # Create table
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("#", style="dim", width=3)
    table.add_column("Name", style="white")
    table.add_column("Network", style="cyan", width=10)
    table.add_column("Status", style="green", width=10)
    table.add_column("Canisters", justify="right", width=10)
    table.add_column("Modified", style="dim", width=20)
    
    for i, realm in enumerate(realms, 1):
        is_current = current_folder and realm["path"] == current_folder
        
        # Format status with color
        status = realm["status"]
        if status == "deployed":
            status_str = "[green]deployed[/green]"
        else:
            status_str = "[yellow]created[/yellow]"
        
        # Format name with indicator if current
        name = realm["name"]
        if is_current:
            name = f"[bold green]► {name}[/bold green]"
        
        # Format date
        date_str = ""
        if realm["created"]:
            date_str = realm["created"].strftime("%Y-%m-%d %H:%M")
        
        table.add_row(
            str(i),
            name,
            realm["network"],
            status_str,
            str(realm["canister_count"]) if realm["canister_count"] else "-",
            date_str,
        )
    
    console.print(table)
    
    if current_folder:
        console.print(f"\n[dim]Current realm: {current_folder}[/dim]")
    
    console.print(f"\n[dim]💡 Set active realm: realms realm set <# or name>[/dim]")


@realm_app.command("set")
def realm_set(
    realm_id: str = typer.Argument(help="Realm index number or folder name"),
    base_dir: Optional[str] = typer.Option(
        None, "--dir", "-d", help="Base directory to search for realms"
    ),
) -> None:
    """Set the current realm context by folder."""
    console.print("[bold blue]🏛️  Setting Realm Context[/bold blue]\n")

    realm = resolve_realm_by_id(realm_id, base_dir)
    
    if not realm:
        console.print(f"[red]❌ Realm not found: {realm_id}[/red]")
        console.print(f"\n[dim]💡 List available realms with: realms realm ls[/dim]")
        raise typer.Exit(1)
    
    # Set both the realm folder and network
    set_current_realm_folder(realm["path"])
    set_current_realm(realm["name"])
    if realm["network"] != "unknown":
        set_current_network(realm["network"])

    console.print(
        f"[green]✅ Realm context set to: [bold]{realm['name']}[/bold][/green]"
    )
    console.print(f"[dim]   Folder: {realm['path']}[/dim]")
    console.print(f"[dim]   Network: {realm['network']}[/dim]")
    console.print(f"[dim]   Status: {realm['status']}[/dim]")
    if realm["canister_count"]:
        console.print(f"[dim]   Canisters: {realm['canister_count']}[/dim]")


@realm_app.command("current")
def realm_current() -> None:
    """Show the current realm and network context."""
    console.print("[bold blue]📍 Current Context[/bold blue]\n")

    current_realm = get_current_realm()
    current_folder = get_current_realm_folder()
    current_network = get_current_network()

    if current_folder:
        console.print(f"[green]📁 Folder: [bold]{current_folder}[/bold][/green]")
        
        # Check if folder still exists and get current status
        from pathlib import Path
        folder_path = Path(current_folder)
        if folder_path.exists():
            realms = list_realm_folders()
            for r in realms:
                if r["path"] == current_folder:
                    console.print(f"[dim]   Name: {r['name']}[/dim]")
                    console.print(f"[dim]   Status: {r['status']}[/dim]")
                    if r["canister_count"]:
                        console.print(f"[dim]   Canisters: {r['canister_count']}[/dim]")
                    break
        else:
            console.print(f"[yellow]   ⚠️  Folder no longer exists[/yellow]")
    elif current_realm:
        # Legacy: realm set via registry
        try:
            realm_network, realm_canister = resolve_realm_details(current_realm)
            console.print(f"[green]🏛️  Realm: [bold]{current_realm}[/bold][/green]")
            console.print(f"[dim]   Network: {realm_network}[/dim]")
            console.print(f"[dim]   Canister: {realm_canister}[/dim]")
        except ValueError as e:
            console.print(f"[red]🏛️  Realm: [bold]{current_realm}[/bold] (⚠️  {e})[/red]")
    else:
        console.print("[dim]📁 Realm: Not set[/dim]")

    console.print(f"[cyan]🌐 Network: [bold]{current_network}[/bold][/cyan]")

    if not current_folder and not current_realm and current_network == "local":
        console.print(
            "\n[dim]💡 Set a realm with: realms realm ls && realms realm set <#>[/dim]"
        )


@realm_app.command("unset")
def realm_unset() -> None:
    """Clear the current realm context."""
    console.print("[bold blue]🔄 Clearing Realm Context[/bold blue]\n")

    current_folder = get_current_realm_folder()
    current_realm = get_current_realm()
    
    if current_folder or current_realm:
        if current_folder:
            unset_current_realm_folder()
            console.print(
                f"[green]✅ Cleared realm folder: [bold]{current_folder}[/bold][/green]"
            )
        if current_realm:
            unset_current_realm()
            console.print(
                f"[green]✅ Cleared realm context: [bold]{current_realm}[/bold][/green]"
            )
        console.print("[dim]Will use network context or defaults[/dim]")
    else:
        console.print("[yellow]No realm context set[/yellow]")


@realm_app.command("status")
def realm_status(
    realm_ref: Optional[str] = typer.Argument(
        None, help="Realm canister ID or name (e.g., 'Dominion' or 'xxxxx-xxxxx-xxxxx-xxxxx-cai')"
    ),
    network: Optional[str] = typer.Option(
        None, "--network", "-n", help="Network to use (overrides context)"
    ),
) -> None:
    """Show canister IDs and URLs for a realm.
    
    Examples:
        realms realm status Dominion --network staging
        realms realm status xxxxx-xxxxx-xxxxx-xxxxx-cai --network staging
        realms realm status  # Uses local folder context
    """
    console.print("[bold blue]🏛️  Realm Status[/bold blue]\n")

    # Get effective network
    effective_network, _ = get_effective_network_and_canister(network, None)
    
    # If a realm reference is provided, resolve it to canister ID
    if realm_ref:
        try:
            backend_canister_id, realm_name = resolve_realm_ref_to_canister_id(
                realm_ref, effective_network
            )
            console.print(f"[dim]Realm: {realm_name}[/dim]")
            console.print(f"[dim]Backend Canister: {backend_canister_id}[/dim]")
            console.print(f"[dim]Network: {effective_network}[/dim]\n")
            
            # For remote realms, show info from the registry
            _show_remote_realm_status(backend_canister_id, realm_name, effective_network)
            return
            
        except ValueError as e:
            console.print(f"[red]❌ {e}[/red]")
            raise typer.Exit(1)
    
    # No realm_ref provided - use local folder context (existing behavior)
    console.print(f"[dim]Network: {effective_network}[/dim]\n")

    # Load canister IDs
    try:
        import json
        import subprocess
        from pathlib import Path

        # Use effective cwd which respects realm folder context
        effective_cwd = get_effective_cwd()
        if effective_cwd:
            project_root = Path(effective_cwd)
            console.print(f"[dim]Realm folder: {project_root}[/dim]\n")
        else:
            project_root = get_project_root()
        
        # Initialize variables for local network
        local_port = "8000"  # Default port
        candid_ui_id = None
        
        # For local network, get canister IDs dynamically from dfx
        if effective_network == "local":
            # Get list of canisters from dfx.json
            dfx_json_path = project_root / "dfx.json"
            if not dfx_json_path.exists():
                console.print(
                    "[yellow]⚠️  dfx.json not found in project root[/yellow]"
                )
                console.print(
                    "[dim]Make sure you're in a Realms project directory[/dim]"
                )
                raise typer.Exit(1)
            
            with open(dfx_json_path, "r") as f:
                dfx_config = json.load(f)
            
            canister_names = list(dfx_config.get("canisters", {}).keys())
            
            # Get network configuration for port
            networks_config = dfx_config.get("networks", {})
            local_network_config = networks_config.get("local", {})
            bind_address = local_network_config.get("bind", "127.0.0.1:8000")
            # Extract port from bind address (format: "127.0.0.1:8000")
            local_port = bind_address.split(":")[-1] if ":" in bind_address else "8000"
            
            # Fetch canister IDs dynamically
            canister_ids = {}
            for canister_name in canister_names:
                try:
                    result = subprocess.run(
                        ["dfx", "canister", "id", canister_name],
                        capture_output=True,
                        text=True,
                        check=True,
                        timeout=5,
                        cwd=str(project_root)
                    )
                    canister_id = result.stdout.strip()
                    canister_ids[canister_name] = {"local": canister_id}
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                    # Canister not deployed yet, skip it
                    continue
            
            # Get Candid UI canister ID for backend URLs
            candid_ui_id = None
            try:
                result = subprocess.run(
                    ["dfx", "canister", "id", "__Candid_UI"],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=5,
                    cwd=str(project_root)
                )
                candid_ui_id = result.stdout.strip()
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                # Candid UI not available, will fall back to default
                pass
            
            if not canister_ids:
                console.print(
                    "[yellow]⚠️  No canisters deployed on local network[/yellow]"
                )
                console.print(
                    "[dim]Deploy canisters first with: dfx deploy[/dim]"
                )
                raise typer.Exit(1)
        else:
            # For non-local networks, read from canister_ids.json
            canister_ids_path = project_root / "canister_ids.json"

            if not canister_ids_path.exists():
                console.print(
                    "[yellow]⚠️  canister_ids.json not found in project root[/yellow]"
                )
                console.print(
                    "[dim]Make sure you're in a Realms project directory or deploy first[/dim]"
                )
                raise typer.Exit(1)

            with open(canister_ids_path, "r") as f:
                canister_ids = json.load(f)

            # Check if network has any canisters
            has_canisters = False
            for canister_name in canister_ids:
                if effective_network in canister_ids[canister_name]:
                    has_canisters = True
                    break

            if not has_canisters:
                console.print(
                    f"[yellow]⚠️  No canisters found for network: {effective_network}[/yellow]"
                )
                console.print("[dim]Available networks:[/dim]")
                networks = set()
                for canister_data in canister_ids.values():
                    networks.update(canister_data.keys())
                for net in sorted(networks):
                    console.print(f"  - {net}")
                raise typer.Exit(1)

        # Create table
        table = Table(title=f"Realm Canisters on {effective_network.upper()}")
        table.add_column("Canister", style="cyan", no_wrap=True)
        table.add_column("Canister ID", style="green")
        table.add_column("URL", style="blue")

        # Add rows for each canister
        for canister_name, network_ids in sorted(canister_ids.items()):
            if effective_network in network_ids:
                canister_id = network_ids[effective_network]

                # Determine if this is a backend or frontend canister
                is_backend = "backend" in canister_name.lower()

                # Construct URL based on network and canister type
                if is_backend:
                    # Backend canisters use Candid UI
                    if effective_network == "local":
                        # For local, use dynamically fetched Candid UI and port
                        if candid_ui_id:
                            url = f"http://127.0.0.1:{local_port}/?canisterId={candid_ui_id}&id={canister_id}"
                        else:
                            # Fallback if Candid UI not found
                            url = f"http://127.0.0.1:{local_port}/?canisterId=<candid-ui>&id={canister_id}"
                    else:
                        # Every other network alias is mainnet
                        url = f"https://{IC_CANDID_UI}.icp0.io/?id={canister_id}"
                else:
                    # Frontend canisters use direct URLs
                    if effective_network == "local":
                        # Use recommended format for local
                        url = f"http://{canister_id}.localhost:{local_port}/"
                    else:
                        url = f"https://{canister_id}.icp0.io"

                table.add_row(canister_name, canister_id, url)

        console.print(table)

        # Add helpful notes
        console.print(
            f"\n[dim]💡 Frontend canisters can be accessed directly via their URLs[/dim]"
        )
        console.print(
            f"[dim]💡 Use 'realms realm call <realm> <method>' to call backend methods[/dim]"
        )

    except Exception as e:
        console.print(f"[red]❌ Error reading canister IDs: {e}[/red]")
        raise typer.Exit(1)


def _show_remote_realm_status(backend_canister_id: str, realm_name: str, network: str) -> None:
    """Show status for a remote realm (queried from registry)."""
    import subprocess
    
    # Construct URLs based on network
    if network != "local":
        frontend_url = f"https://{backend_canister_id}.icp0.io"  # Actually need frontend ID
        backend_url = f"https://{IC_CANDID_UI}.icp0.io/?id={backend_canister_id}"
    else:
        frontend_url = f"http://{backend_canister_id}.localhost:8000/"
        backend_url = f"http://127.0.0.1:8000/?canisterId=<candid-ui>&id={backend_canister_id}"
    
    # Create table
    table = Table(title=f"Realm '{realm_name}' on {network.upper()}")
    table.add_column("Component", style="cyan", no_wrap=True)
    table.add_column("Canister ID", style="green")
    table.add_column("URL", style="blue")
    
    table.add_row("Backend", backend_canister_id, backend_url)
    
    # Try to get realm info from backend to find frontend canister
    try:
        cmd = [
            "dfx", "canister", "call",
            "--network", network,
            backend_canister_id,
            "status"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            console.print(f"[dim]Backend is responding[/dim]\n")
    except Exception:
        pass
    
    console.print(table)
    
    console.print(f"\n[dim]💡 Use 'realms realm call {realm_name} <method> --network {network}' to call backend methods[/dim]")


# Create db subcommand group
db_app = typer.Typer(name="db", help="Database exploration and querying", invoke_without_command=True)
app.add_typer(db_app, name="db", rich_help_panel="Development")


@db_app.callback()
def db_callback(
    ctx: typer.Context,
    network: Optional[str] = typer.Option(
        None, "--network", "-n", help="Network to use (overrides context)"
    ),
    canister: Optional[str] = typer.Option(
        None, "--canister", "-c", help="Canister name to connect to (overrides context)"
    ),
    folder: Optional[str] = typer.Option(
        None, "--folder", "-f", help="Realm folder containing dfx.json (uses current realm context if not specified)"
    ),
) -> None:
    """Explore the Realm database. Use subcommands for specific operations or run without subcommand for interactive mode."""
    # Store network, canister, and folder in context for subcommands
    ctx.obj = {"network": network, "canister": canister, "folder": folder}
    
    # If no subcommand is provided, run interactive explorer
    if ctx.invoked_subcommand is None:
        db_command(network, canister, folder)


@db_app.command("get")
def db_get(
    ctx: typer.Context,
    entity_type: str = typer.Argument(help="Entity type (e.g., User, Transfer, Mandate)"),
    entity_id: Optional[str] = typer.Argument(None, help="Optional entity ID to retrieve specific entity"),
) -> None:
    """Get entities from the database and output as JSON.
    
    Examples:
        realms db get User              # Get all users
        realms db get User user1        # Get specific user by ID
        realms db get Transfer          # Get all transfers
    """
    # Get network, canister, and folder from context
    network = ctx.obj.get("network") if ctx.obj else None
    canister = ctx.obj.get("canister") if ctx.obj else None
    folder = ctx.obj.get("folder") if ctx.obj else None
    
    db_get_command(entity_type, entity_id, network, canister, folder)


@db_app.command("find")
def db_find(
    ctx: typer.Context,
    entity_type: str = typer.Argument(help="Entity type (e.g., User, Transfer, Mandate)"),
    filters: List[str] = typer.Argument(help="Field=value filters (e.g., id=system status=active)"),
) -> None:
    """Find entities matching given field criteria.
    
    Searches for entities where all specified field values match.
    
    Examples:
        realms db find User id=system
        realms db find Transfer status=completed
        realms db find Invoice recipient=user123 status=Pending
    """
    network = ctx.obj.get("network") if ctx.obj else None
    canister = ctx.obj.get("canister") if ctx.obj else None
    folder = ctx.obj.get("folder") if ctx.obj else None
    
    db_find_command(entity_type, filters, network, canister, folder)


@db_app.command("schema")
def db_schema(
    ctx: typer.Context,
) -> None:
    """Get the database schema showing all entity types, fields, and relationships.
    
    Outputs JSON with entity types, field definitions, and relationship mappings.
    This is useful for discovering what data types are available in the realm.
    
    Examples:
        realms db schema                    # Get full schema
        realms db schema -n staging         # Get schema from staging network
    """
    # Get network, canister, and folder from context
    network = ctx.obj.get("network") if ctx.obj else None
    canister = ctx.obj.get("canister") if ctx.obj else None
    folder = ctx.obj.get("folder") if ctx.obj else None
    
    db_schema_command(network, canister, folder)


@db_app.command("import")
def db_import(
    ctx: typer.Context,
    file_path: str = typer.Argument(..., help="Path to JSON data file or Python codex file"),
    entity_type: Optional[str] = typer.Option(
        None, "--type", help="Entity type (codex for Python files, or json entity type)"
    ),
    format: str = typer.Option("json", "--format", help="Data format (json)"),
    batch_size: int = typer.Option(
        MAX_BATCH_SIZE, "--batch-size", help="Batch size for import"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Preview import without making changes"
    ),
    identity: Optional[str] = typer.Option(
        None, "--identity", "-i", help="Identity to use for import"
    ),
) -> None:
    """Import data into the realm. Supports JSON data and Python codex files."""
    network = ctx.obj.get("network") if ctx.obj else None
    canister = ctx.obj.get("canister") if ctx.obj else None
    folder = ctx.obj.get("folder") if ctx.obj else None
    effective_network = network or "local"
    effective_canister = canister or "realm_backend"
    import_data_command(file_path, entity_type, format, batch_size, dry_run, effective_network, identity, effective_canister, folder)


@db_app.command("export")
def db_export(
    ctx: typer.Context,
    output_dir: str = typer.Option(
        "exported_realm", "--output-dir", help="Output directory for exported data"
    ),
    entity_types: Optional[str] = typer.Option(
        None, "--entity-types", help="Comma-separated list of entity types to export (default: all)"
    ),
    identity: Optional[str] = typer.Option(
        None, "--identity", help="Path to identity PEM file or identity name for dfx"
    ),
    include_codexes: bool = typer.Option(
        True, "--include-codexes/--no-codexes", help="Include codexes in export (default: True)"
    ),
) -> None:
    """Export data from the realm. Saves JSON data and Python codex files."""
    network = ctx.obj.get("network") if ctx.obj else None
    effective_network = network or "local"
    export_data_command(output_dir, entity_types, effective_network, identity, include_codexes)


# Create network subcommand group
network_app = typer.Typer(name="network", help="Network context management")
app.add_typer(network_app, name="network", rich_help_panel="Utility")


@network_app.command("set")
def network_set(
    network_name: str = typer.Argument(
        help="Network name to set as current context (local, ic, testnet, etc.)"
    ),
) -> None:
    """Set the current network context."""
    console.print("[bold blue]🌐 Setting Network Context[/bold blue]\n")

    set_current_network(network_name)
    console.print(
        f"[green]✅ Network context set to: [bold]{network_name}[/bold][/green]"
    )

    # Show warning if realm context overrides network
    current_realm = get_current_realm()
    if current_realm:
        console.print(
            f"[yellow]⚠️  Note: Realm context '[bold]{current_realm}[/bold]' may override network setting[/yellow]"
        )


@network_app.command("current")
def network_current() -> None:
    """Show the current network context."""
    console.print("[bold blue]📍 Current Network Context[/bold blue]\n")

    current_network = get_current_network()
    console.print(f"[cyan]🌐 Network: [bold]{current_network}[/bold][/cyan]")

    if current_network == "local":
        console.print("[dim]💡 This is the default network[/dim]")


@network_app.command("unset")
def network_unset() -> None:
    """Clear the current network context (reverts to 'local')."""
    console.print("[bold blue]🔄 Clearing Network Context[/bold blue]\n")

    current_network = get_current_network()
    if current_network != "local":
        unset_current_network()
        console.print(
            f"[green]✅ Cleared network context: [bold]{current_network}[/bold][/green]"
        )
        console.print("[dim]Reverted to default: local[/dim]")
    else:
        console.print("[yellow]Already using default network: local[/yellow]")


@app.command("clean", rich_help_panel="Utility")
def clean(
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip confirmation prompt"
    ),
) -> None:
    """Clean up dfx state and .realms directory.
    
    Runs scripts/clean_dfx.sh and removes the .realms directory.
    """
    import shutil
    import subprocess
    from pathlib import Path
    
    if not yes:
        confirm = typer.confirm(
            "⚠️  This will stop dfx, clean dfx state, and remove .realms directory. Continue?"
        )
        if not confirm:
            console.print("[yellow]Aborted.[/yellow]")
            raise typer.Exit(0)
    
    console.print("[bold blue]🧹 Cleaning up...[/bold blue]\n")
    
    # Run scripts/clean_dfx.sh
    scripts_dir = Path("scripts")
    clean_script = scripts_dir / "clean_dfx.sh"
    
    if clean_script.exists():
        console.print("Running scripts/clean_dfx.sh...")
        result = subprocess.run(
            ["bash", str(clean_script)],
            capture_output=False
        )
        if result.returncode == 0:
            console.print("[green]✅ clean_dfx.sh completed[/green]")
        else:
            console.print(f"[yellow]⚠️  clean_dfx.sh exited with code {result.returncode}[/yellow]")
    else:
        console.print(f"[yellow]⚠️  {clean_script} not found, skipping[/yellow]")
    
    # Remove .realms directory
    realms_dir = Path(".realms")
    if realms_dir.exists():
        console.print("Removing .realms directory...")
        shutil.rmtree(realms_dir)
        console.print("[green]✅ .realms directory removed[/green]")
    else:
        console.print("[dim].realms directory does not exist[/dim]")
    
    console.print("\n[green]🎉 Cleanup complete![/green]")


@app.command("version", hidden=True)
def version() -> None:
    """Show version information."""
    import subprocess
    from pathlib import Path
    from . import __version__
    
    try:
        from . import __commit__, __commit_date__
        print(f"{__version__}+{__commit__}")
        print(__commit_date__)
        return
    except ImportError:
        pass
    
    try:
        cli_dir = Path(__file__).parent.parent.parent
        commit_hash = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=cli_dir,
            stderr=subprocess.DEVNULL
        ).decode().strip()
        commit_date = subprocess.check_output(
            ["git", "log", "-1", "--format=%cI"],
            cwd=cli_dir,
            stderr=subprocess.DEVNULL
        ).decode().strip()
        print(f"{__version__}+{commit_hash}")
        print(commit_date)
    except Exception:
        print(__version__)


@app.callback()
def main(
    ctx: typer.Context,
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Enable verbose output"
    ),
    version_flag: bool = typer.Option(False, "--version", help="Show version and exit"),
) -> None:
    """
    Realms CLI - Deploy and manage Realms projects.

    🏛️ Build and deploy digital government platforms on the Internet Computer.

    Quick start:
    1. realms realm create --random --deploy
    2. realms status
    """
    if version_flag:
        version()
        raise typer.Exit()

    if verbose:
        console.print("[dim]Verbose mode enabled[/dim]")
    
    # If no command was provided, show error and help suggestion
    if ctx.invoked_subcommand is None:
        console.print("[red]Error: No command provided.[/red]")
        console.print("\n💡 Try running [cyan]realms --help[/cyan] to see available commands.")
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
