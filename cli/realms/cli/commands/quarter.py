"""Quarter management commands for realms."""

import json
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .create import create_command
from .deploy import _deploy_realm_internal
from ..realm_api import call_op
from ..utils import (
    console,
    get_effective_network_and_canister,
    resolve_realm_ref_to_canister_id,
)


def _call_op(canister_id: str, name: str, args: Optional[dict], network: str, read: bool = False) -> dict:
    """Run realm operation ``name`` and return its reply; RuntimeError when the realm refuses it."""
    reply = call_op(canister_id, name, args, network=network, read=read)
    if not reply.get("ok"):
        raise RuntimeError(reply.get("error") or f"{name} failed")
    if reply.get("requires_vote"):
        console.print(f"  [yellow]{name} needs a vote: filed as proposal {reply['proposal']}[/yellow]")
    return reply


def _quarters(canister_id: str, network: str) -> list:
    """The quarters in ``canister_id``'s federation directory, without the realm itself."""
    reply = _call_op(canister_id, "quarter_directory", None, network, read=True)
    return [q for q in reply["quarters"] if q["id"] != canister_id]


def quarter_create_command(
    realm_ref: str,
    quarter_name: str,
    network: str,
    identity: Optional[str],
    mode: str,
    output_dir: str,
    deploy: bool,
    manifest: Optional[str],
    bare: bool,
    plain_logs: bool,
    capital: bool = False,
) -> None:
    """Create a new quarter backend and register it with the parent realm."""

    console.print(f"[bold cyan]📦 Creating Quarter: {quarter_name}[/bold cyan]\n")

    # Resolve parent realm canister ID
    effective_network, _ = get_effective_network_and_canister(network, None, quiet=True)
    try:
        parent_canister_id, parent_name = resolve_realm_ref_to_canister_id(
            realm_ref, effective_network
        )
    except ValueError as e:
        console.print(f"[red]❌ {e}[/red]")
        raise typer.Exit(1)

    console.print(f"  Parent realm: [cyan]{parent_name}[/cyan] ({parent_canister_id})")
    console.print(f"  Network: [dim]{effective_network}[/dim]\n")

    # Step 1: Create the quarter realm using the standard create flow
    console.print("[bold]Step 1/3:[/bold] Creating quarter backend...\n")
    create_command(
        output_dir=output_dir,
        realm_name=quarter_name,
        manifest=manifest,
        random=False,
        members=None,
        organizations=None,
        transactions=None,
        disputes=None,
        seed=None,
        network=network,
        deploy=deploy,
        identity=identity,
        mode=mode,
        bare=bare,
        plain_logs=plain_logs,
    )

    if not deploy:
        console.print("\n[yellow]⚠️  Quarter created but not deployed (--deploy not set).[/yellow]")
        console.print("[dim]Deploy the quarter first, then register it with the parent realm manually.[/dim]")
        return

    # Step 2: Find the deployed quarter's canister ID
    # The most recently created realm directory contains the deployment info
    console.print("\n[bold]Step 2/3:[/bold] Detecting quarter canister ID...\n")
    quarter_canister_id = None
    output_path = Path(output_dir)
    if output_path.exists():
        realm_dirs = sorted(output_path.glob("realm_*"), key=lambda p: p.stat().st_mtime)
        if realm_dirs:
            latest = realm_dirs[-1]
            deployment_json = latest / "deployment.json"
            canister_ids_json = latest / ".dfx" / effective_network / "canister_ids.json"

            # Try deployment.json first
            if deployment_json.exists():
                try:
                    with open(deployment_json) as f:
                        dep = json.load(f)
                    quarter_canister_id = dep.get("realm_backend", {}).get("canister_id")
                except Exception:
                    pass

            # Fallback to canister_ids.json
            if not quarter_canister_id and canister_ids_json.exists():
                try:
                    with open(canister_ids_json) as f:
                        cids = json.load(f)
                    for name, info in cids.items():
                        if "backend" in name:
                            quarter_canister_id = info.get(effective_network)
                            break
                except Exception:
                    pass

    if not quarter_canister_id:
        console.print("[yellow]⚠️  Could not auto-detect quarter canister ID.[/yellow]")
        console.print("[dim]Register manually: realms realm quarter register <realm> --canister-id <id> --quarter-name <name>[/dim]")
        return

    console.print(f"  Quarter canister ID: [green]{quarter_canister_id}[/green]\n")

    # Step 3: Configure quarter and register with parent
    console.print("[bold]Step 3/3:[/bold] Registering quarter with parent realm...\n")
    try:
        # Configure the quarter's own realm entity
        _call_op(quarter_canister_id, "quarter_join_federation", {"capital": parent_canister_id}, effective_network)
        console.print(f"  ✅ Quarter configured (parent: {parent_canister_id})")

        if capital:
            console.print(
                "  [yellow]⚠️  --capital is not supported by this realm version: "
                "the capital is the realm its quarters register with.[/yellow]"
            )

        # Register quarter on parent realm
        _call_op(
            parent_canister_id,
            "quarter_register",
            {"quarter": quarter_canister_id, "name": quarter_name},
            effective_network,
        )
        console.print(f"  ✅ Quarter registered on parent realm")
    except RuntimeError as e:
        console.print(f"[red]❌ Registration failed: {e}[/red]")
        console.print("[dim]You can retry with: realms realm quarter register ...[/dim]")
        raise typer.Exit(1)

    console.print(f"\n[green]✅ Quarter '{quarter_name}' created and registered successfully![/green]")


def quarter_register_command(
    realm_ref: str,
    quarter_name: str,
    quarter_canister_id: str,
    network: str,
) -> None:
    """Register an existing deployed canister as a quarter of a realm."""

    effective_network, _ = get_effective_network_and_canister(network, None, quiet=True)
    try:
        parent_canister_id, parent_name = resolve_realm_ref_to_canister_id(
            realm_ref, effective_network
        )
    except ValueError as e:
        console.print(f"[red]❌ {e}[/red]")
        raise typer.Exit(1)

    console.print(f"[bold cyan]🔗 Registering Quarter[/bold cyan]\n")
    console.print(f"  Parent: [cyan]{parent_name}[/cyan] ({parent_canister_id})")
    console.print(f"  Quarter: [cyan]{quarter_name}[/cyan] ({quarter_canister_id})")
    console.print(f"  Network: [dim]{effective_network}[/dim]\n")

    try:
        # Configure the quarter
        _call_op(quarter_canister_id, "quarter_join_federation", {"capital": parent_canister_id}, effective_network)
        console.print("  ✅ Quarter configured")

        # Register on parent
        _call_op(
            parent_canister_id,
            "quarter_register",
            {"quarter": quarter_canister_id, "name": quarter_name},
            effective_network,
        )
        console.print("  ✅ Quarter registered on parent realm")
    except RuntimeError as e:
        console.print(f"[red]❌ {e}[/red]")
        raise typer.Exit(1)

    console.print(f"\n[green]✅ Quarter '{quarter_name}' registered successfully![/green]")


def quarter_list_command(
    realm_ref: str,
    network: str,
) -> None:
    """List all quarters under a realm."""

    effective_network, _ = get_effective_network_and_canister(network, None, quiet=True)
    try:
        parent_canister_id, parent_name = resolve_realm_ref_to_canister_id(
            realm_ref, effective_network
        )
    except ValueError as e:
        console.print(f"[red]❌ {e}[/red]")
        raise typer.Exit(1)

    console.print(f"[bold blue]📋 Quarters of {parent_name}[/bold blue]\n")

    try:
        quarters = _quarters(parent_canister_id, effective_network)
    except RuntimeError as e:
        console.print(f"[red]❌ Could not query realm: {e}[/red]")
        raise typer.Exit(1)

    if not quarters:
        console.print("[dim]No quarters registered. This realm runs as a single backend.[/dim]")
        console.print(f"\n[dim]💡 Create a quarter: realms realm quarter create {realm_ref} --quarter-name <name> --deploy[/dim]")
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("#", style="dim", width=3)
    table.add_column("Name", style="white")
    table.add_column("Canister ID", style="cyan")
    table.add_column("Population", justify="right")
    table.add_column("Status", style="green")

    for i, q in enumerate(quarters, 1):
        status = q.get("status", "unknown")
        status_style = {
            "active": "[green]active[/green]",
            "suspended": "[yellow]suspended[/yellow]",
            "splitting": "[blue]splitting[/blue]",
            "merging": "[blue]merging[/blue]",
        }.get(status, f"[dim]{status}[/dim]")

        table.add_row(
            str(i),
            q.get("name", ""),
            q.get("id", ""),
            str(q.get("population", 0)),
            status_style,
        )

    console.print(table)
    console.print(f"\n[dim]Total: {len(quarters)} quarter(s)[/dim]")


def quarter_status_command(
    realm_ref: str,
    quarter_ref: str,
    network: str,
) -> None:
    """Show detailed status of a specific quarter."""

    effective_network, _ = get_effective_network_and_canister(network, None, quiet=True)
    try:
        parent_canister_id, parent_name = resolve_realm_ref_to_canister_id(
            realm_ref, effective_network
        )
    except ValueError as e:
        console.print(f"[red]❌ {e}[/red]")
        raise typer.Exit(1)

    # First get the quarter's canister ID from the parent
    try:
        quarters = _quarters(parent_canister_id, effective_network)
    except RuntimeError as e:
        console.print(f"[red]❌ Could not query realm: {e}[/red]")
        raise typer.Exit(1)

    # Find matching quarter by name or canister ID
    quarter_info = None
    for q in quarters:
        if (q.get("name", "").lower() == quarter_ref.lower()
                or q.get("id", "") == quarter_ref):
            quarter_info = q
            break

    if not quarter_info:
        console.print(f"[red]❌ Quarter '{quarter_ref}' not found in realm '{parent_name}'[/red]")
        if quarters:
            console.print(f"[dim]Available quarters: {', '.join(q.get('name', '') for q in quarters)}[/dim]")
        raise typer.Exit(1)

    quarter_canister_id = quarter_info["id"]
    console.print(f"[bold blue]📊 Quarter Status: {quarter_info['name']}[/bold blue]\n")
    console.print(f"  Parent realm: [cyan]{parent_name}[/cyan] ({parent_canister_id})")
    console.print(f"  Canister ID:  [cyan]{quarter_canister_id}[/cyan]")
    console.print(f"  Population:   {quarter_info.get('population', 0)}")
    console.print(f"  Status:       {quarter_info.get('status', 'unknown')}")

    # Query the quarter's own backend for detailed status
    try:
        monitor = _call_op(quarter_canister_id, "realm_monitor", None, effective_network, read=True)
        parent_id = _call_op(quarter_canister_id, "quarter_directory", None, effective_network, read=True)["capital"]
        rows = monitor.get("rows", {})
        console.print(f"\n  [bold]Backend details:[/bold]")
        console.print(f"    Users:     {rows.get('User', 0)}")
        console.print(f"    Proposals: {rows.get('Proposal', 0)}")
        console.print(f"    Version:   {monitor.get('version', 'unknown')}")
        if parent_id != quarter_canister_id:
            console.print(f"    Mode:      [green]quarter[/green] (parent: {parent_id})")
        else:
            console.print(f"    Mode:      [yellow]standalone (not yet configured as quarter)[/yellow]")
    except RuntimeError as e:
        console.print(f"\n  [yellow]⚠️  Could not query quarter backend: {e}[/yellow]")


def quarter_remove_command(
    realm_ref: str,
    quarter_ref: str,
    network: str,
) -> None:
    """Remove a quarter from a realm."""

    effective_network, _ = get_effective_network_and_canister(network, None, quiet=True)
    try:
        parent_canister_id, parent_name = resolve_realm_ref_to_canister_id(
            realm_ref, effective_network
        )
    except ValueError as e:
        console.print(f"[red]❌ {e}[/red]")
        raise typer.Exit(1)

    # Find the quarter canister ID
    try:
        quarters = _quarters(parent_canister_id, effective_network)
    except RuntimeError as e:
        console.print(f"[red]❌ Could not query realm: {e}[/red]")
        raise typer.Exit(1)

    quarter_info = None
    for q in quarters:
        if (q.get("name", "").lower() == quarter_ref.lower()
                or q.get("id", "") == quarter_ref):
            quarter_info = q
            break

    if not quarter_info:
        console.print(f"[red]❌ Quarter '{quarter_ref}' not found in realm '{parent_name}'[/red]")
        raise typer.Exit(1)

    quarter_canister_id = quarter_info["id"]
    quarter_name = quarter_info["name"]

    console.print(f"[bold red]🗑️  Removing Quarter: {quarter_name}[/bold red]\n")
    console.print(f"  Canister ID: {quarter_canister_id}")
    console.print(f"  From realm:  {parent_name}\n")

    try:
        _call_op(parent_canister_id, "quarter_deregister", {"quarter": quarter_canister_id}, effective_network)
        console.print(f"[green]✅ Quarter '{quarter_name}' removed from realm '{parent_name}'[/green]")
        console.print(f"[dim]Note: The quarter canister ({quarter_canister_id}) still exists. Delete it manually with dfx if needed.[/dim]")
    except RuntimeError as e:
        console.print(f"[red]❌ Failed to remove quarter: {e}[/red]")
        raise typer.Exit(1)


def quarter_secede_command(
    quarter_ref: str,
    network: str,
) -> None:
    """Declare independence — secede a quarter from its federation."""

    effective_network, _ = get_effective_network_and_canister(network, None, quiet=True)

    console.print(f"[bold red]🏴 Declaring Independence[/bold red]\n")
    console.print(f"  Quarter: [cyan]{quarter_ref}[/cyan]")
    console.print(f"  Network: [dim]{effective_network}[/dim]\n")

    try:
        _call_op(quarter_ref, "quarter_secede", None, effective_network)
        console.print(f"[green]✅ Quarter has seceded and is now an independent realm.[/green]")
        console.print(f"[dim]The canister retains all users, data, and governance. Deploy a frontend to complete independence.[/dim]")
    except RuntimeError as e:
        console.print(f"[red]❌ Failed: {e}[/red]")
        raise typer.Exit(1)


def quarter_join_federation_command(
    quarter_ref: str,
    capital_canister_id: str,
    as_capital: bool,
    network: str,
) -> None:
    """Join an existing federation as a quarter."""

    effective_network, _ = get_effective_network_and_canister(network, None, quiet=True)

    if as_capital:
        console.print(
            "[red]❌ --as-capital is not supported by this realm version: "
            "the capital is the realm its quarters register with.[/red]"
        )
        raise typer.Exit(1)

    console.print(f"[bold cyan]🤝 Joining Federation as quarter[/bold cyan]\n")
    console.print(f"  Quarter:    [cyan]{quarter_ref}[/cyan]")
    console.print(f"  Capital:    [cyan]{capital_canister_id}[/cyan]")
    console.print(f"  Network:    [dim]{effective_network}[/dim]\n")

    try:
        _call_op(quarter_ref, "quarter_join_federation", {"capital": capital_canister_id}, effective_network)
        console.print(f"[green]✅ Joined federation as quarter![/green]")

        # Also register with the capital
        try:
            # Get realm name from the quarter's own status
            quarter_name = _call_op(quarter_ref, "status", None, effective_network, read=True)["name"] or quarter_ref[:12]
            _call_op(
                capital_canister_id,
                "quarter_register",
                {"quarter": quarter_ref, "name": quarter_name},
                effective_network,
            )
            console.print(f"  ✅ Registered on capital's quarter list")
        except RuntimeError as e:
            console.print(f"  [yellow]⚠️  Could not auto-register on capital: {e}[/yellow]")
            console.print(f"  [dim]Register manually: realms realm quarter register <capital> --quarter-name <name> --canister-id {quarter_ref}[/dim]")
    except RuntimeError as e:
        console.print(f"[red]❌ Failed: {e}[/red]")
        raise typer.Exit(1)
