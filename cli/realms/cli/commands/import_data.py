"""Import command: loads JSON records and codices into a realm through the operations that create them."""

import json
from pathlib import Path
from typing import Optional

import typer

from ..constants import MAX_BATCH_SIZE
from ..utils import (
    console,
    display_error_panel,
    display_success_panel,
    get_effective_cwd,
)
from .db import realm_op

# Entity types in the order they are imported (profiles before users, departments before funds), each with the
# operation that creates one and its arguments from a record (as `realms db export` writes it). Users and
# departments are handled in ``_operations``.
CREATORS = {
    "UserProfile": ("profile_create", lambda r: {
        "profile": r["id"], "description": r.get("description", ""), "allowed_to": r.get("allowed_to", [])}),
    "User": None,
    "Department": None,
    "Organization": ("organization_register", lambda r: {
        "organization": r["id"], "description": r.get("description", "")}),
    "Fund": ("fund_create", lambda r: {
        "fund": r["id"], "name": r.get("name") or r["id"], "kind": r.get("kind", "general"),
        **({"department": r["department"]} if r.get("department") else {})}),
    "Land": ("land_register", lambda r: {
        k: r[k] for k in ("x", "y", "width", "height", "kind", "owner", "metadata") if k in r}),
}


def _parents_first(departments):
    known, placed, ordered = {d["id"] for d in departments}, set(), []
    pending = list(departments)
    while pending:
        ready = [d for d in pending if d.get("parent") not in known or d["parent"] in placed]
        if not ready:
            raise ValueError(f"departments with circular parents: {', '.join(d['id'] for d in pending)}")
        ordered += ready
        placed |= {d["id"] for d in ready}
        pending = [d for d in pending if d["id"] not in placed]
    return ordered


def _operations(by_type, batch_size):
    """``(operation, args)`` creating every record, in import order."""
    for kind, creator in CREATORS.items():
        rows = by_type.get(kind, [])
        if kind == "User":
            for i in range(0, len(rows), batch_size):
                yield "user_import", {"users": [{"user": r["id"], "nickname": r.get("nickname", "")}
                                                for r in rows[i:i + batch_size]]}
        elif kind == "Department":
            for d in _parents_first(rows):
                if not d.get("is_root"):
                    yield "department_create", {"department": d["id"], "description": d.get("description", ""),
                                                **({"parent": d["parent"]} if d.get("parent") else {})}
        else:
            name, args = creator
            for r in rows:
                yield name, args(r)


def import_data_command(
    file_path: str,
    entity_type: Optional[str] = None,
    format: str = "json",
    batch_size: int = MAX_BATCH_SIZE,
    dry_run: bool = False,
    network: str = "local",
    identity: Optional[str] = None,
    canister: str = "realm_backend",
    folder: Optional[str] = None,
) -> None:
    """Import data into the realm. Supports JSON data and codex packages."""

    # Handle codex files separately
    if entity_type == "codex":
        return import_codex_command(
            file_path,
            dry_run=dry_run,
            network=network,
            identity=identity,
            canister=canister,
            folder=folder,
        )

    console.print(f"[bold blue]📥 Importing data from {file_path}[/bold blue]\n")

    try:
        data_file = Path(file_path)
        if not data_file.exists():
            raise FileNotFoundError(f"Data file not found: {file_path}")

        with open(data_file, "r") as f:
            data = json.load(f)

        if not isinstance(data, list):
            raise ValueError("JSON data must be an array of objects")

        by_type = {}
        for record in data:
            by_type.setdefault(record.get("_type") or entity_type, []).append(record)
        unsupported = sorted(str(t) for t in by_type if t not in CREATORS)
        if unsupported:
            raise ValueError(
                f"importing {', '.join(unsupported)} is not supported by this realm version "
                f"(importable: {', '.join(CREATORS)})"
            )
        calls = list(_operations(by_type, batch_size))
        console.print(f"📊 Found {len(data)} records to import ({len(calls)} operations)")

        if dry_run:
            console.print("[yellow]🔍 Dry run mode - nothing will be imported[/yellow]")
            for name, args in calls:
                console.print(f"  {name} {json.dumps(args)[:120]}")
            display_success_panel("Dry Run Complete", f"Would import {len(data)} records from {file_path}")
            return

        effective_cwd = get_effective_cwd(folder)
        done = present = 0
        for i, (name, args) in enumerate(calls, 1):
            console.print(f"📊 {i}/{len(calls)} {name}")
            reply = realm_op(canister, name, args, network, identity, cwd=effective_cwd, timeout=120)
            error = str(reply.get("error", ""))
            if reply.get("ok"):
                done += 1
                if reply.get("proposal"):
                    console.print(f"[yellow]  filed as proposal {reply['proposal']}: applies once approved[/yellow]")
            elif "exists" in error or "already" in error:
                present += 1
                console.print(f"[yellow]  skipped: {error}[/yellow]")
            else:
                raise RuntimeError(f"{name}: {error}")

        display_success_panel(
            "Import Data Complete! 🎉",
            f"Successfully imported {len(data)} records from {file_path} "
            f"({done} operations, {present} skipped as already present)",
        )

    except Exception as e:
        display_error_panel("Backend Import Data Failed", str(e))
        raise typer.Exit(1)


def import_codex_command(
    file_path: str,
    codex_name: Optional[str] = None,
    dry_run: bool = False,
    network: str = "local",
    identity: Optional[str] = None,
    canister: str = "realm_backend",
    folder: Optional[str] = None,
) -> None:
    """Install a codex package (``codex_install``): its directory, or its ``backend/hooks.py`` with the
    ``manifest.json`` beside or above it."""

    console.print(f"[bold blue]📜 Importing codex from {file_path}[/bold blue]\n")

    try:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Codex file not found: {file_path}")
        hooks = path / "backend" / "hooks.py" if path.is_dir() else path
        manifest_path = next((d / "manifest.json" for d in (hooks.parent, hooks.parent.parent)
                              if (d / "manifest.json").is_file()), None)
        if not hooks.is_file() or manifest_path is None:
            raise FileNotFoundError(
                f"{file_path}: a codex installs as a package, manifest.json with backend/hooks.py"
            )
        manifest = json.loads(manifest_path.read_text())
        codex_name = manifest.get("id") or codex_name or hooks.stem

        console.print(f"📜 Importing codex: {codex_name}")

        if dry_run:
            console.print(
                "[yellow]🔍 Dry run mode - no codex will be imported[/yellow]"
            )
            display_success_panel(
                "Dry Run Complete",
                f"Would import codex '{codex_name}' from {file_path}",
            )
            return

        reply = realm_op(canister, "codex_install", {"manifest": manifest, "source": hooks.read_text()},
                         network, identity, cwd=get_effective_cwd(folder), timeout=300)
        if not reply.get("ok"):
            raise RuntimeError(reply.get("error"))

        display_success_panel(
            "Codex Import Complete! 🎉",
            f"Filed codex '{codex_name}' as proposal {reply['proposal']}" if reply.get("proposal")
            else f"Successfully imported codex '{codex_name}' from {file_path}",
        )

    except Exception as e:
        display_error_panel("Codex Import Failed", str(e))
        raise typer.Exit(1)
