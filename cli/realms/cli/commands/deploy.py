"""Deploy command for deploying realms to different networks."""

import json
import os
import subprocess
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from ..constants import REALM_FOLDER
from ..utils import (
    get_realms_logger, 
    set_log_dir, 
    run_command, 
    run_command_with_progress,
    display_canister_urls_json,
    DeploymentProgress,
    set_current_realm,
    set_current_realm_folder,
    set_current_network,
)

console = Console()


def _query_cycles_balance(network: str, cwd: str = ".") -> Optional[float]:
    """Query the deployer's cycles balance in TC. Returns None if query fails."""
    if network in ("local",):
        return None
    try:
        env = os.environ.copy()
        env["DFX_WARNING"] = "-mainnet_plaintext_identity"
        result = subprocess.run(
            ["dfx", "cycles", "balance", "--network", network],
            capture_output=True, text=True, timeout=30, cwd=cwd, env=env,
        )
        if result.returncode == 0:
            # Parse e.g. "14.796 TC (trillion cycles)."
            m = re.search(r'([\d.]+)\s*TC', result.stdout)
            if m:
                return float(m.group(1))
    except Exception:
        pass
    return None


def _query_canister_cycles(canister_id: str, network: str, cwd: str = ".") -> Optional[int]:
    """Query cycles balance for a specific canister. Returns raw cycles count or None."""
    if network in ("local",):
        return None
    try:
        env = os.environ.copy()
        env["DFX_WARNING"] = "-mainnet_plaintext_identity"
        result = subprocess.run(
            ["dfx", "canister", "status", canister_id, "--network", network],
            capture_output=True, text=True, timeout=30, cwd=cwd, env=env,
        )
        if result.returncode == 0:
            m = re.search(r'Balance:\s+([\d_]+)\s+Cycles', result.stdout)
            if m:
                return int(m.group(1).replace("_", ""))
    except Exception:
        pass
    return None


def _inject_version_placeholders(folder_path: Path, logger) -> None:
    """Replace version/commit/dependency placeholders in source files before build.

    This mirrors the sed replacements done in ci-main.yml so that local
    deployments via ``realms deploy`` also get correct version info baked
    into the canister WASM.
    """
    # --- Gather values -------------------------------------------------------
    # Git commit hash & datetime (from the realms project root)
    project_root = folder_path
    # Walk up to find .git directory (project root)
    for parent in [folder_path] + list(folder_path.parents):
        if (parent / ".git").exists():
            project_root = parent
            break

    commit_hash = ""
    commit_datetime = ""
    commit_hash_short = ""
    built_at_iso = ""
    try:
        commit_hash = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(project_root),
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        commit_hash_short = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(project_root),
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        commit_datetime = subprocess.check_output(
            ["git", "log", "--format=%cd", "--date=iso8601", "-1"],
            cwd=str(project_root),
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception as e:
        logger.warning(f"Could not get git info for placeholders: {e}")
    try:
        from datetime import datetime, timezone
        built_at_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        built_at_iso = ""

    # Version from version.txt (project root)
    version = ""
    for candidate in [folder_path / "version.txt", project_root / "version.txt"]:
        if candidate.exists():
            version = candidate.read_text().strip()
            break

    # Dependency versions from the realm folder's own requirements.txt
    dep_versions = {}
    req_path = folder_path / "requirements.txt"
    if not req_path.exists():
        req_path = project_root / "requirements.txt"
    if req_path.exists():
        for line in req_path.read_text().splitlines():
            line = line.strip()
            if "==" in line:
                pkg, ver = line.split("==", 1)
                dep_versions[pkg.strip()] = ver.strip()

    # Resolve versions: prefer requirements.txt pins, fall back to installed package metadata
    def _get_dep_version(pkg_name):
        ver = dep_versions.get(pkg_name, "")
        if not ver:
            try:
                from importlib.metadata import version as pkg_version
                ver = pkg_version(pkg_name)
            except Exception:
                pass
        return ver

    basilisk_version = _get_dep_version("ic-basilisk")
    ic_basilisk_toolkit_version = _get_dep_version("ic-basilisk-toolkit")
    ic_python_db_version = _get_dep_version("ic-python-db")
    ic_python_logging_version = _get_dep_version("ic-python-logging")

    # --- Build replacement map -----------------------------------------------
    # Order matters: specific placeholders that contain "VERSION_PLACEHOLDER"
    # as a substring must be replaced BEFORE the generic "VERSION_PLACEHOLDER"
    replacements = [
        ("COMMIT_HASH_PLACEHOLDER", commit_hash),
        ("COMMIT_DATETIME_PLACEHOLDER", commit_datetime),
        ("SHA_PLACEHOLDER", commit_hash_short),
        ("BUILT_AT_ISO_PLACEHOLDER", built_at_iso),
        ("BASILISK_VERSION_PLACEHOLDER", basilisk_version),
        ("IC_BASILISK_TOOLKIT_VERSION_PLACEHOLDER", ic_basilisk_toolkit_version),
        ("IC_PYTHON_DB_VERSION_PLACEHOLDER", ic_python_db_version),
        ("IC_PYTHON_LOGGING_VERSION_PLACEHOLDER", ic_python_logging_version),
        ("VERSION_PLACEHOLDER", version),
    ]

    # --- Target files (relative to realm folder) -----------------------------
    target_files = [
        "src/realm_backend/api/status.py",
        "src/realm_backend/api/version_http.py",
        "src/marketplace_backend/api/status.py",
        "src/marketplace_backend/api/version_http.py",
        "src/realm_registry_backend/api/status.py",
        "src/realm_frontend/src/app.html",
        "src/realm_registry_frontend/src/app.html",
    ]

    replaced_count = 0
    for rel_path in target_files:
        fpath = folder_path / rel_path
        if not fpath.exists():
            continue
        content = fpath.read_text()
        original = content
        for placeholder, value in replacements:
            if value:
                content = content.replace(placeholder, value)
        if content != original:
            fpath.write_text(content)
            replaced_count += 1
            logger.info(f"Injected version placeholders in {rel_path}")

    if replaced_count:
        logger.info(
            f"Replaced placeholders in {replaced_count} file(s): "
            f"version={version}, commit={commit_hash[:12]}, "
            f"basilisk={basilisk_version}, db={ic_python_db_version}, "
            f"logging={ic_python_logging_version}"
        )
    else:
        logger.info("No placeholder files found to update (may already be replaced)")


def _deploy_realm_internal(
    config_file: Optional[str],
    folder: str,
    network: str,
    clean: bool,
    identity: Optional[str],
    mode: str = "auto",
    bare: bool = False,
    plain_logs: bool = False,
    registry: Optional[str] = None,
    no_demo_data: bool = False,
) -> None:
    """Internal deployment logic (can be called directly from Python).
    
    Args:
        bare: If True, only deploy canisters (skip extensions, data, post-deploy)
        plain_logs: If True, show full verbose output instead of progress UI
        no_demo_data: If True, skip demo/fake data seeding in post-deploy
    """
    log_dir = Path(folder).absolute()
    
    # Set up logging directory for this deployment
    set_log_dir(log_dir)
    
    # Create logger for capturing script output in the realm folder
    logger = get_realms_logger(log_dir=log_dir)
    logger.info("=" * 60)
    logger.info(f"Starting deployment to {network}")
    logger.info(f"Realm folder: {folder}")
    logger.info(f"Deploy mode: {mode}")
    logger.info(f"CLI version: realms-gos 0.2.8 (auto-mode fix)")
    if identity:
        logger.info(f"Using identity: {identity}")
    logger.info("=" * 60)
    
    # Only print header in plain logs mode (progress display has its own header)
    if plain_logs:
        console.print(f"[bold blue]🚀 Deploying Realm to {network}[/bold blue]\n")
        console.print(f"📁 Realm folder: {folder}")
        console.print(f"📝 Log: {log_dir / 'realms.log'}")

    folder_path = Path(folder).resolve()
    if not folder_path.exists():
        console.print(f"[red]❌ Folder not found: {folder}[/red]")
        raise typer.Exit(1)

    scripts_dir = folder_path / "scripts"
    if not scripts_dir.exists():
        console.print(f"[red]❌ Scripts directory not found: {scripts_dir}[/red]")
        raise typer.Exit(1)

    # Run the scripts in sequence
    if bare:
        # Bare deployment: only deploy canisters (no extensions, no data)
        scripts = [
            "2-deploy-canisters.sh",
        ]
        if plain_logs:
            console.print("[yellow]ℹ️  Bare deployment mode: skipping extensions and data upload[/yellow]\n")
    else:
        # Full deployment: extensions + canisters + data + post-deploy
        scripts = [
            "1-install-extensions.sh",
            "2-deploy-canisters.sh",
            "3-upload-data.sh",
            "4-post-deploy.py",
        ]

    # Prepare environment with identity and registry if specified
    env = os.environ.copy() if identity or registry or not plain_logs else None
    if identity:
        if not env:
            env = os.environ.copy()
        env["DFX_IDENTITY"] = identity
    if registry:
        if not env:
            env = os.environ.copy()
        env["REGISTRY_CANISTER_ID"] = registry
        logger.info(f"Registry canister: {registry}")
    
    # Set REALMS_VERBOSE for bash scripts when using plain logs
    if plain_logs:
        if not env:
            env = os.environ.copy()
        env["REALMS_VERBOSE"] = "1"
    
    # Set NO_DEMO_DATA for post_deploy.py to skip demo data seeding
    if no_demo_data:
        if not env:
            env = os.environ.copy()
        env["NO_DEMO_DATA"] = "1"
        logger.info("Demo data seeding disabled (--no-demo-data)")

    # Inject version/commit/dependency placeholders into source files before build
    _inject_version_placeholders(folder_path, logger)

    # Validate all scripts exist before starting
    for script_name in scripts:
        script_path = scripts_dir / script_name
        if not script_path.exists():
            console.print(f"[red]❌ Required script not found: {script_path}[/red]")
            console.print(f"[yellow]   The realm folder may be corrupted or incomplete.[/yellow]")
            console.print(f"[yellow]   Try recreating with: realms create --realm-name <name>[/yellow]")
            raise typer.Exit(1)

    # Query cycles balance before deployment
    cycles_before = _query_cycles_balance(network, cwd=str(folder_path))
    deploy_start_time = time.time()
    if cycles_before is not None:
        logger.info(f"Cycles balance before deployment: {cycles_before} TC")

    if plain_logs:
        # Plain logs mode: show full verbose output
        _run_deployment_plain(scripts, scripts_dir, folder_path, network, mode, env, logger, log_dir)
    else:
        # Default: Rich Live progress display
        _run_deployment_with_progress(scripts, scripts_dir, folder_path, network, mode, env, logger, log_dir)
    
    deploy_end_time = time.time()
    deploy_duration_s = round(deploy_end_time - deploy_start_time, 1)

    display_canister_urls_json(folder_path, network, "Realm Deployment Summary")
    
    # Query cycles balance after deployment and print summary
    cycles_after = _query_cycles_balance(network, cwd=str(folder_path))
    cycles_summary = _build_cycles_summary(
        folder_path, network, cycles_before, cycles_after, deploy_duration_s, logger
    )
    if cycles_summary:
        console.print(f"\n[bold cyan]💰 Cycles Summary[/bold cyan]")
        console.print(json.dumps(cycles_summary, indent=2))
        logger.info(f"Cycles summary: {json.dumps(cycles_summary)}")
        # Also save to deployment directory for the management service to pick up
        summary_path = folder_path / "_cycles_summary.json"
        with open(summary_path, "w") as f:
            json.dump(cycles_summary, f, indent=2)

    # Update context to point to the deployed realm
    realm_name = folder_path.name
    relative_folder = str(folder_path.relative_to(Path.cwd())) if folder_path.is_relative_to(Path.cwd()) else str(folder_path)
    set_current_realm_folder(relative_folder)
    set_current_realm(realm_name)
    set_current_network(network)
    console.print(f"[dim]📍 Context set to: {realm_name} ({network})[/dim]")
    logger.info(f"Set context to realm: {realm_name} on {network}")


def _run_deployment_plain(
    scripts: list,
    scripts_dir: Path,
    folder_path: Path,
    network: str,
    mode: str,
    env: Optional[dict],
    logger,
    log_dir: Path,
) -> None:
    """Run deployment with plain verbose output."""
    for script_name in scripts:
        script_path = scripts_dir / script_name
        console.print(f"🔧 Running {script_name}...")

        cmd = _build_script_command(script_path, script_name, network, mode)
        script_path.chmod(0o755)
        result = run_command(cmd, cwd=str(folder_path), use_project_venv=True, logger=logger, env=env)

        if result.returncode != 0:
            console.print(f"[red]❌ {script_name} failed with exit code {result.returncode}[/red]")
            console.print(f"[yellow]   Check {log_dir}/realms.log for details[/yellow]")
            logger.error(f"{script_name} failed with exit code {result.returncode}")
            raise typer.Exit(1)
        
        console.print(f"[green]✅ {script_name} completed[/green]\n")
        logger.info(f"{script_name} completed successfully")

    console.print(f"[green]🎉 Deployment completed successfully![/green]")
    logger.info("Deployment completed successfully")


def _run_deployment_with_progress(
    scripts: list,
    scripts_dir: Path,
    folder_path: Path,
    network: str,
    mode: str,
    env: Optional[dict],
    logger,
    log_dir: Path,
) -> None:
    """Run deployment with Rich Live progress display."""
    # Human-readable step names
    step_names = {
        "1-install-extensions.sh": "Installing extensions",
        "2-deploy-canisters.sh": "Deploying canisters",
        "3-upload-data.sh": "Uploading data",
        "4-post-deploy.py": "Running post-deploy",
    }
    
    log_path = str(log_dir / "realms.log")
    progress = DeploymentProgress(
        total_steps=len(scripts),
        title=f"Deploying to {network}",
        log_path=log_path,
    )
    
    with progress:
        for script_name in scripts:
            script_path = scripts_dir / script_name
            step_display = step_names.get(script_name, script_name)
            
            progress.start_step(step_display)
            
            cmd = _build_script_command(script_path, script_name, network, mode)
            script_path.chmod(0o755)
            
            result = run_command_with_progress(
                cmd, 
                cwd=str(folder_path), 
                use_project_venv=True, 
                logger=logger, 
                env=env,
                progress=progress,
            )

            if result.returncode != 0:
                progress.fail_step(f"Exit code {result.returncode}")
                progress.stop()
                console.print(f"\n[red]❌ {script_name} failed with exit code {result.returncode}[/red]")
                console.print(f"[yellow]   Check {log_dir}/realms.log for details[/yellow]")
                console.print(f"[yellow]   Run with --plain-logs to see full output[/yellow]")
                logger.error(f"{script_name} failed with exit code {result.returncode}")
                raise typer.Exit(1)
            
            progress.complete_step(step_display)
            logger.info(f"{script_name} completed successfully")
    
    console.print(f"\n[green]🎉 Deployment completed successfully![/green]")
    logger.info("Deployment completed successfully")


def _build_cycles_summary(
    folder_path: Path,
    network: str,
    cycles_before: Optional[float],
    cycles_after: Optional[float],
    deploy_duration_s: float,
    logger,
) -> Optional[dict]:
    """Build a cycles consumption summary after deployment."""
    if network in ("local",):
        return None

    from ..utils import get_canister_urls
    canisters = get_canister_urls(folder_path, network)

    summary = {
        "network": network,
        "deploy_duration_seconds": deploy_duration_s,
    }

    if cycles_before is not None and cycles_after is not None:
        consumed = round(cycles_before - cycles_after, 6)
        summary["deployer_cycles_before_tc"] = cycles_before
        summary["deployer_cycles_after_tc"] = cycles_after
        summary["total_cycles_consumed_tc"] = consumed

    # Query per-canister cycles
    canister_balances = {}
    for name, info in canisters.items():
        cid = info.get("id", "")
        if cid:
            balance = _query_canister_cycles(cid, network, cwd=str(folder_path))
            if balance is not None:
                canister_balances[name] = {
                    "canister_id": cid,
                    "cycles_balance": balance,
                }
    if canister_balances:
        summary["canister_balances"] = canister_balances

    return summary if len(summary) > 2 else None


def _build_script_command(script_path: Path, script_name: str, network: str, mode: str) -> list:
    """Build command list for a deployment script."""
    if script_name.endswith(".py"):
        return ["python", str(script_path.resolve()), network, mode]
    elif script_name == "2-deploy-canisters.sh":
        return [str(script_path.resolve()), ".", network, mode]
    else:
        return [str(script_path.resolve()), network, mode]


def deploy_command(
    config_file: Optional[str] = typer.Option(
        None, "--file", "-f", help="Path to realm configuration file"
    ),
    folder: Optional[str] = typer.Option(
        None, "--folder", help="Path to generated realm folder with scripts"
    ),
    network: str = typer.Option(
        "local", "--network", "-n", help="Target network for deployment"
    ),
    clean: bool = typer.Option(False, "--clean", help="Clean deployment (restart dfx)"),
    identity: Optional[str] = typer.Option(
        None, "--identity", help="Path to identity PEM file or identity name for dfx"
    ),
    mode: str = typer.Option(
        "auto", "--mode", "-m", help="Deploy mode: 'auto', 'upgrade' or 'reinstall' (auto picks install/upgrade)"
    ),
    plain_logs: bool = typer.Option(
        False, "--plain-logs", help="Show full verbose output instead of progress UI"
    ),
    registry: Optional[str] = None,
) -> None:
    """dfx-deploy a generated realm folder (local development).

    Fleet environments are not deployed from here: `casals up` builds the
    sheet, `casals upgrade` ships a new build (docs/deployment/README.md) and
    `realms new --gaas-config` creates realms through the GOS queue.
    """
    # Auto-detect folder and deploy
    if not folder:
        realm_base = Path(REALM_FOLDER)
        
        if not realm_base.exists():
            console.print(f"[red]❌ No realm folder specified and no realms found.[/red]")
            console.print(f"[yellow]   Create a realm first with: realms create --realm-name <name>[/yellow]")
            raise typer.Exit(1)
        
        # Find all realm_* directories (realms are created directly in REALM_FOLDER)
        realm_dirs = [d for d in realm_base.iterdir() if d.is_dir() and d.name.startswith("realm_")]
        
        if len(realm_dirs) == 0:
            console.print(f"[red]❌ No realm folders found in {realm_base}[/red]")
            console.print(f"[yellow]   Create a realm first with: realms create --realm-name <name>[/yellow]")
            raise typer.Exit(1)
        
        elif len(realm_dirs) == 1:
            folder = str(realm_dirs[0])
            console.print(f"[dim]📁 Auto-detected single realm folder: {folder}[/dim]")
        
        else:
            # Multiple realms found - show error with list
            console.print(f"[red]❌ Multiple realm folders found. Please specify which one to deploy:[/red]")
            console.print("")
            for realm_dir in sorted(realm_dirs, key=lambda x: x.stat().st_mtime, reverse=True):
                mtime = realm_dir.stat().st_mtime
                time_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
                console.print(f"   • {realm_dir} [dim]({time_str})[/dim]")
            console.print("")
            console.print(f"[yellow]   Usage: realms deploy --folder <path>[/yellow]")
            raise typer.Exit(1)
    
    _deploy_realm_internal(config_file, folder, network, clean, identity, mode, bare=False, plain_logs=plain_logs, registry=registry)


if __name__ == "__main__":
    typer.run(deploy_command)
