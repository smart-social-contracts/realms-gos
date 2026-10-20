"""``realms new extension`` / ``realms new codex``: scaffold a package project.

The extension template is the same directory ``npm create @realmsgos/extension``
copies (``packages/create-extension/template``), so both entry points produce
the same project. The codex template is a minimal Agora-shaped codex.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console

console = Console()

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"
ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,31}$")
TEXT_SUFFIXES = {".css", ".html", ".js", ".json", ".md", ".mjs", ".py", ".svelte", ".ts"}
MIN_NODE_MAJOR = 20


def _split_words(name: str) -> List[str]:
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    return [p for p in re.split(r"[^a-zA-Z0-9]+", spaced) if p]


def id_from_name(name: str) -> str:
    """``my-hello-world-extension`` → ``my_hello_world_extension``."""
    return "_".join(p.lower() for p in _split_words(name))


def display_name_from_name(name: str) -> str:
    """``my-hello-world-extension`` → ``My Hello World Extension``."""
    return " ".join(p[:1].upper() + p[1:].lower() for p in _split_words(name))


def validate_id(kind: str, value: str) -> None:
    if not ID_PATTERN.match(value):
        console.print(
            f"Error: {kind} id {value!r} must match {ID_PATTERN.pattern} "
            "(a lowercase letter, then 2-31 lowercase letters, digits or underscores).",
            style="red",
            markup=False,
        )
        raise typer.Exit(1)


def copy_template(src: Path, dest: Path, replacements: dict) -> None:
    for path in sorted(src.rglob("*")):
        rel = path.relative_to(src)
        if "node_modules" in rel.parts or "dist" in rel.parts or "__pycache__" in rel.parts:
            continue
        target = dest / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix.lower() in TEXT_SUFFIXES:
            text = path.read_text(encoding="utf-8")
            for key, value in replacements.items():
                text = text.replace(key, value)
            target.write_text(text, encoding="utf-8")
        else:
            shutil.copy2(path, target)


def _prepare_target(target: str) -> Path:
    dest = Path(target).expanduser().resolve()
    if dest.exists() and any(dest.iterdir()):
        console.print(f"[red]Error: target directory is not empty: {dest}[/red]")
        raise typer.Exit(1)
    return dest


def _template(name: str) -> Path:
    path = TEMPLATES_DIR / name
    if not (path / "manifest.json").is_file():
        console.print(f"[red]Error: {name} template missing from the realms CLI: {path}[/red]")
        raise typer.Exit(1)
    return path


def node_major_version() -> Optional[int]:
    if shutil.which("node") is None:
        return None
    try:
        out = subprocess.run(
            ["node", "--version"], capture_output=True, text=True, timeout=30
        ).stdout.strip()
        return int(out.lstrip("v").split(".")[0])
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def require_node(skip_hint: str = "rerun with --no-install") -> None:
    """Fail early when Node.js / npm cannot build extension frontends."""
    major = node_major_version()
    install = (
        f"  Install Node.js {MIN_NODE_MAJOR}+ (https://nodejs.org/en/download; on Ubuntu,\n"
        "  Ubuntu's own nodejs package is too old: use NodeSource or nvm), or "
        f"{skip_hint}."
    )
    if major is None or shutil.which("npm") is None:
        console.print(
            f"[red]Error: Node.js {MIN_NODE_MAJOR}+ and npm are needed to build "
            f"extension frontends.[/red]\n{install}"
        )
        raise typer.Exit(1)
    if major < MIN_NODE_MAJOR:
        console.print(
            f"[red]Error: Node.js {major} is too old; the frontend toolchain needs "
            f"Node.js {MIN_NODE_MAJOR}+.[/red]\n{install}"
        )
        raise typer.Exit(1)


def run_npm(args: List[str], cwd: Path) -> None:
    console.print(f"[dim]$ npm {' '.join(args)}  (in {cwd})[/dim]")
    result = subprocess.run(["npm", *args], cwd=str(cwd))
    if result.returncode != 0:
        console.print(f"[red]Error: npm {' '.join(args)} failed (exit {result.returncode})[/red]")
        raise typer.Exit(result.returncode)


def new_extension_command(
    target: str,
    extension_id: Optional[str] = None,
    display_name: Optional[str] = None,
    description: Optional[str] = None,
    install: bool = True,
) -> Path:
    dest = _prepare_target(target)
    ext_id = extension_id or id_from_name(dest.name)
    validate_id("extension", ext_id)
    name = display_name or display_name_from_name(dest.name)
    desc = description or f"{name} — a sandboxed Realms extension"
    if install:
        require_node()

    copy_template(
        _template("extension"),
        dest,
        {
            "__EXTENSION_ID__": ext_id,
            "__EXTENSION_NAME__": name,
            "__EXTENSION_DESCRIPTION__": desc,
        },
    )
    console.print(f"[green]✓ Created extension '{ext_id}' in {dest}[/green]")

    if install:
        run_npm(["install", "--no-audit", "--no-fund"], dest)
        run_npm(["run", "build"], dest)
        console.print(f"[green]✓ Built {dest / 'frontend' / 'dist'}[/green]")

    console.print("\nNext steps:")
    console.print(f"  cd {dest}")
    if not install:
        console.print("  npm install && npm run build")
    console.print("  realms local start            # a realm on your machine")
    console.print("  realms local publish .        # publish this extension to it")
    return dest


def new_codex_command(
    target: str,
    codex_id: Optional[str] = None,
    display_name: Optional[str] = None,
    description: Optional[str] = None,
    extensions: Optional[List[str]] = None,
) -> Path:
    dest = _prepare_target(target)
    cid = codex_id or id_from_name(dest.name)
    validate_id("codex", cid)
    deps = [e.strip() for e in (extensions or []) if e.strip()]
    for dep in deps:
        validate_id("extension", dep)
    name = display_name or display_name_from_name(dest.name)
    desc = description or f"{name} — a Realms codex"

    copy_template(
        _template("codex"),
        dest,
        {
            "__CODEX_ID__": cid,
            "__CODEX_NAME__": name,
            "__CODEX_DESCRIPTION__": desc,
        },
    )
    manifest_path = dest / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["dependencies"] = deps
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    console.print(f"[green]✓ Created codex '{cid}' in {dest}[/green]")
    if deps:
        console.print(f"  depends on: {', '.join(deps)}")
    console.print("\nNext steps:")
    console.print("  realms local start")
    console.print(f"  realms local publish <extension dirs…> {dest}")
    console.print(f"  realms local setup --codex {cid}")
    return dest
