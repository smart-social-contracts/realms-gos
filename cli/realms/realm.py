"""
Realms SDK - realm module

Provides async functions for interacting with realms from workstation-side Python code.

Usage:
    from realms import realm
    
    folder = await realm.create(deploy=True)
    await realm.call("realm_join", {"code": "X7K-29Q"}, realm="Dominion", network="staging")
    invoices = await realm.db.get("Invoice", folder=folder)
"""

from __future__ import annotations

import asyncio
import subprocess
import json
from typing import Optional, Union, Any, List


async def _run_command(cmd: list[str], cwd: Optional[str] = None) -> dict:
    """Run a command asynchronously and return parsed JSON output or raw result."""
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd
    )
    stdout, stderr = await process.communicate()
    
    if process.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\nError: {stderr.decode()}")
    
    output = stdout.decode().strip()
    
    # Try to parse as JSON
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return {"raw": output}


async def create(
    deploy: bool = False,
    network: str = "local",
    realm_name: str = "Generated Realm",
    manifest: Optional[str] = None,
    output_dir: Optional[str] = None,
) -> str:
    """
    Create a new realm.
    
    Args:
        deploy: If True, deploy the realm after creation
        network: Target network (local, ic, staging)
        realm_name: Name of the realm
        manifest: Path to manifest.json
        output_dir: Output directory for the realm
        
    Returns:
        Path to the created realm folder
    """
    cmd = ["realms", "realm", "create", "--realm-name", realm_name, "--network", network]
    
    if deploy:
        cmd.append("--deploy")
    if manifest:
        cmd.extend(["--manifest", manifest])
    if output_dir:
        cmd.extend(["--output-dir", output_dir])
    
    result = await _run_command(cmd)
    
    # Extract folder path from output
    if "folder" in result:
        return result["folder"]
    return result.get("raw", "")


async def call(
    op: str,
    args: dict,
    realm: str,
    network: str = "local",
) -> dict:
    """
    Call an operation of a realm.
    
    Args:
        op: Operation name (e.g., "realm_join", "proposal_vote"; "ops_list" lists them)
        args: The operation's arguments
        realm: Realm canister ID or registry name
        network: Target network
        
    Returns:
        The realm's reply: {"ok": true, ...} or {"ok": false, "error": ...}
    """
    cmd = ["realms", "realm", "call", realm, op, json.dumps(args), "-n", network]
    return await _run_command(cmd)


async def call_extension(
    extension: str,
    function: str,
    args: dict,
    realm: str,
    network: str = "local",
) -> dict:
    """
    Call an extension function on a realm (the extension_call operation).
    
    Args:
        extension: Extension name (e.g., "member_dashboard")
        function: Function name (e.g., "check_invoice_payment")
        args: Arguments as a dictionary (will be JSON-encoded)
        realm: Realm canister ID or registry name
        network: Target network
        
    Returns:
        The realm's reply; the function's return value is under "result"
    """
    cmd = [
        "realms", "realm", "call", realm, "extension",
        extension, function, json.dumps(args),
        "-n", network,
    ]
    return await _run_command(cmd)


async def status(realm: str, network: str = "local") -> dict:
    """
    Get the status of a realm (the status operation).
    
    Args:
        realm: Realm canister ID or registry name
        network: Target network
        
    Returns:
        The realm's version, status, name, canister id and number of operations
    """
    return await call("status", {}, realm, network)


class db:
    """Database operations for a realm."""
    
    @staticmethod
    async def get(
        entity_type: str,
        entity_id: Optional[str] = None,
        folder: Optional[str] = None,
        network: str = "local"
    ) -> Union[list, dict]:
        """
        Get entities from the realm database.
        
        Args:
            entity_type: Entity type (e.g., "Invoice", "User", "Transfer")
            entity_id: Optional specific entity ID
            folder: Path to the realm folder
            network: Target network
            
        Returns:
            List of entities or single entity if entity_id provided
        """
        cmd = ["realms", "db", "-f", folder, "-n", network, "get", entity_type]
        if entity_id:
            cmd.append(entity_id)
        return await _run_command(cmd)


class icw:
    """ICW (ICRC Wallet) operations."""
    
    @staticmethod
    async def balance(
        principal: Optional[str] = None,
        subaccount: Optional[str] = None,
        ledger: Optional[str] = None,
        network: str = "local"
    ) -> dict:
        """
        Get token balance.
        
        Args:
            principal: Principal to check balance for (default: current identity)
            subaccount: Optional subaccount
            ledger: Ledger canister ID
            network: Target network
            
        Returns:
            Balance information
        """
        cmd = ["icw", "-n", network, "balance"]
        if principal:
            cmd.extend(["-p", principal])
        if subaccount:
            cmd.extend(["-s", subaccount])
        if ledger:
            cmd.extend(["--ledger", ledger])
        return await _run_command(cmd)
    
    @staticmethod
    async def transfer(
        to: str,
        amount: float,
        subaccount: Optional[str] = None,
        ledger: Optional[str] = None,
        fee: int = 0,
        network: str = "local"
    ) -> dict:
        """
        Transfer tokens.
        
        Args:
            to: Recipient principal
            amount: Amount to transfer
            subaccount: Optional recipient subaccount
            ledger: Ledger canister ID
            fee: Transaction fee
            network: Target network
            
        Returns:
            Transfer result
        """
        cmd = ["icw", "-n", network, "transfer", to, str(amount)]
        if subaccount:
            cmd.extend(["-s", subaccount])
        if ledger:
            cmd.extend(["--ledger", ledger])
        cmd.extend(["--fee", str(fee)])
        return await _run_command(cmd)
