"""Export command for extracting data and codexes from Realms."""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer

from ..utils import (
    console,
    display_error_panel,
    display_success_panel,
    get_project_root,
)
from .db import CursorDatabaseExplorer


def export_data_command(
    output_dir: str = "exported_realm",
    entity_types: Optional[str] = None,
    network: str = "local",
    identity: Optional[str] = None,
    include_codexes: bool = True,
) -> None:
    """Export data from the realm (``realm_export``, page by page). Saves JSON data and codex sources."""

    console.print(
        f"[bold blue]📤 Exporting data to {output_dir}[/bold blue]\n"
    )

    try:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        explorer = CursorDatabaseExplorer(network, "realm_backend", cwd=str(get_project_root()), identity=identity)
        types = entity_types.split(",") if entity_types else [cls.__name__ for cls in explorer._ggg_classes]

        console.print("📊 Fetching data from backend...")
        entities = [row for t in types if t != "Codex" for row in explorer.all_entities(t)]
        codexes = [{"name": row["id"], "code": row.get("source", "")}
                   for row in explorer.all_entities("Codex")] if include_codexes else []

        console.print(f"[green]✓[/green] Retrieved {len(entities)} entities and {len(codexes)} codexes")

        # Save entities to JSON file
        if entities:
            entities_file = output_path / "realm_data.json"
            with open(entities_file, "w") as f:
                json.dump(entities, f, indent=2)
            console.print(f"[green]✓[/green] Saved entities to {entities_file}")

        # Save codexes to individual Python files
        if codexes:
            codex_dir = output_path / "codexes"
            codex_dir.mkdir(exist_ok=True)

            for codex in codexes:
                codex_name = codex["name"]

                # Sanitize filename
                safe_name = "".join(c if c.isalnum() or c in "_-" else "_" for c in codex_name)
                codex_file = codex_dir / f"{safe_name}.py"

                with open(codex_file, "w") as f:
                    f.write(codex["code"])

                console.print(f"[green]✓[/green] Saved codex '{codex_name}' to {codex_file}")

        # Create a summary file
        summary = {
            "export_date": datetime.now().isoformat(),
            "network": network,
            "total_entities": len(entities),
            "total_codexes": len(codexes),
            "entity_types": {},
        }

        # Count entities by type
        for entity in entities:
            entity_type = entity.get("_type", "Unknown")
            summary["entity_types"][entity_type] = summary["entity_types"].get(entity_type, 0) + 1

        summary_file = output_path / "export_summary.json"
        with open(summary_file, "w") as f:
            json.dump(summary, f, indent=2)

        console.print(f"[green]✓[/green] Saved export summary to {summary_file}")

        display_success_panel(
            "Export Complete! 🎉",
            f"Successfully exported {len(entities)} entities and {len(codexes)} codexes to {output_dir}",
        )

    except Exception as e:
        display_error_panel("Export Failed", str(e))
        raise typer.Exit(1)
