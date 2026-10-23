"""
Extension command for managing Realm extensions.

Provides functionality for packaging, installing, and managing extensions
in the Smart Social Contracts platform.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import List, Optional
from pprint import pformat

import typer
from rich.console import Console
from rich.table import Table

console = Console()

# Allowlisted extensions under frontend-rt/dist/ (recursive). Source maps excluded.
_FRONTEND_DIST_SUFFIXES = (
    ".html",
    ".js",
    ".css",
    ".png",
    ".svg",
    ".woff",
    ".woff2",
    ".ttf",
    ".json",
    ".webmanifest",
)


def _iter_frontend_dist_uploads(dist_dir: str) -> list[tuple[str, str]]:
    """Return (registry_path, local_path) pairs for allowlisted dist artifacts."""
    if not os.path.isdir(dist_dir):
        return []
    specs: list[tuple[str, str]] = []
    for root, _dirs, files in os.walk(dist_dir):
        for fname in sorted(files):
            if fname.endswith(".map"):
                continue
            if not fname.endswith(_FRONTEND_DIST_SUFFIXES):
                continue
            local = os.path.join(root, fname)
            rel = os.path.relpath(local, dist_dir).replace(os.sep, "/")
            specs.append((f"frontend/dist/{rel}", local))
    return specs


def _remember_uploaded_hash(existing_hashes, registry_path: str, local_sha: str) -> None:
    """The snapshot is taken once per publish; a later upload to the same path
    must compare against this write, not the pre-publish hash."""
    if existing_hashes is not None:
        existing_hashes[registry_path] = local_sha


def _frontend_shell_uploads(
    legacy_dist_dir: str,
    runtime_dist_dir: str,
    bundle_path: Optional[str] = None,
) -> list[tuple[str, str]]:
    """Files to store under ``frontend/dist/``.

    Both dists map to the same registry paths. ``frontend-rt/dist`` is what
    ``realms files build`` produces, so it owns every path it ships; a
    ``frontend/dist`` file is kept only where the runtime dist has none.
    """
    runtime_specs = _iter_frontend_dist_uploads(runtime_dist_dir)
    if bundle_path and os.path.isfile(bundle_path):
        runtime_specs = [
            spec for spec in runtime_specs if spec[0] != "frontend/dist/index.js"
        ]
        runtime_specs.insert(0, ("frontend/dist/index.js", bundle_path))
    runtime_paths = {path for path, _local in runtime_specs}
    legacy_specs = [
        spec
        for spec in _iter_frontend_dist_uploads(legacy_dist_dir)
        if spec[0] not in runtime_paths
    ]
    return legacy_specs + runtime_specs


def validate_extension_id(extension_id):
    """Validate extension ID (no hyphens allowed)"""
    if "-" in extension_id:
        raise ValueError(
            f"Extension ID '{extension_id}' contains hyphens. Only underscores are allowed."
        )
    return True


def get_project_paths():
    """Get standard project paths for extension management"""
    # Use current working directory as project root
    # This allows the CLI to work from any Realms project directory
    project_root = Path.cwd()

    return {
        "project_root": str(project_root),
        "backend_dir": str(project_root / "src" / "realm_backend"),
        "frontend_dir": str(project_root / "src" / "realm_frontend"),
        "extensions_dir": str(project_root / "extensions"),
    }


def get_extension_paths(extension_id, source_dir=None):
    """Get all paths for an extension"""
    validate_extension_id(extension_id)
    paths = get_project_paths()
    
    if source_dir:
        # When packaging from source, use the source directory structure
        return {
            "backend": os.path.join(source_dir, "backend"),
            "frontend_lib": os.path.join(source_dir, "frontend/lib/extensions", extension_id),
            "frontend_route": os.path.join(source_dir, "frontend/routes/(sidebar)/extensions", extension_id),
            "i18n": os.path.join(source_dir, "frontend/i18n/locales/extensions", extension_id),
            "source": source_dir,
        }
    
    # Default paths in realm project structure
    return {
        "backend": os.path.join(
            paths["backend_dir"], "extension_packages", extension_id
        ),
        "frontend_lib": os.path.join(
            paths["frontend_dir"], "src/lib/extensions", extension_id
        ),
        "frontend_route": os.path.join(
            paths["frontend_dir"], "src/routes/(sidebar)/extensions", extension_id
        ),
        "i18n": os.path.join(
            paths["frontend_dir"], "src/lib/i18n/locales/extensions", extension_id
        ),
        "source": os.path.join(paths["extensions_dir"], extension_id),
    }


def find_extension_locations(extension_id, source_dir=None):
    """Find where an extension is located"""
    paths = get_extension_paths(extension_id, source_dir)
    locations = {}

    # Check each location
    for location_type, path in paths.items():
        if os.path.exists(path) and os.listdir(path):
            locations[location_type] = path

    return locations


def list_extensions_command():
    """List all installed extensions"""
    paths = get_project_paths()
    backend_ext_dir = os.path.join(paths["backend_dir"], "extension_packages")
    frontend_ext_dir = os.path.join(paths["frontend_dir"], "src/lib/extensions")

    extensions = {}

    # Check backend extensions
    if os.path.exists(backend_ext_dir):
        for item in os.listdir(backend_ext_dir):
            if os.path.isdir(
                os.path.join(backend_ext_dir, item)
            ) and not item.startswith("__"):
                try:
                    validate_extension_id(item)
                    manifest_path = os.path.join(backend_ext_dir, item, "manifest.json")

                    if os.path.exists(manifest_path):
                        try:
                            with open(manifest_path, "r") as f:
                                manifest = json.load(f)
                            extensions[item] = {
                                "id": item,
                                "version": manifest.get("version", "unknown"),
                                "description": manifest.get("description", ""),
                                "has_backend": True,
                                "has_frontend": False,
                            }
                        except Exception:
                            extensions[item] = {
                                "id": item,
                                "version": "unknown",
                                "description": "",
                                "has_backend": True,
                                "has_frontend": False,
                            }
                except ValueError:
                    console.print(
                        f"[yellow]Skipping extension with invalid ID: {item}[/yellow]"
                    )

    # Check frontend extensions
    if os.path.exists(frontend_ext_dir):
        for item in os.listdir(frontend_ext_dir):
            if os.path.isdir(
                os.path.join(frontend_ext_dir, item)
            ) and not item.startswith("__"):
                try:
                    validate_extension_id(item)

                    if item in extensions:
                        extensions[item]["has_frontend"] = True
                    else:
                        extensions[item] = {
                            "id": item,
                            "version": "unknown",
                            "description": "",
                            "has_backend": False,
                            "has_frontend": True,
                        }
                except ValueError:
                    console.print(
                        f"[yellow]Skipping extension with invalid ID: {item}[/yellow]"
                    )

    # Check i18n-only extensions
    i18n_ext_dir = os.path.join(paths["frontend_dir"], "src/lib/i18n/locales/extensions")
    if os.path.exists(i18n_ext_dir):
        for item in os.listdir(i18n_ext_dir):
            if os.path.isdir(
                os.path.join(i18n_ext_dir, item)
            ) and not item.startswith("__"):
                try:
                    validate_extension_id(item)
                    
                    # Only add if not already discovered
                    if item not in extensions:
                        extensions[item] = {
                            "id": item,
                            "version": "unknown",
                            "description": "",
                            "has_backend": False,
                            "has_frontend": False,
                        }
                except ValueError:
                    console.print(
                        f"[yellow]Skipping extension with invalid ID: {item}[/yellow]"
                    )

    if not extensions:
        console.print("[yellow]No extensions installed[/yellow]")
        return

    # Create table
    table = Table(title="Installed Extensions")
    table.add_column("ID", style="cyan")
    table.add_column("Version", style="green")
    table.add_column("Components", style="magenta")
    table.add_column("Description", style="white")

    for ext_id, ext in extensions.items():
        components = []
        if ext["has_backend"]:
            components.append("backend")
        if ext["has_frontend"]:
            components.append("frontend")
        
        # If no backend or frontend, it's i18n-only
        if not components:
            components.append("i18n")

        table.add_row(
            ext_id,
            ext["version"],
            ", ".join(components),
            (
                ext["description"][:50] + "..."
                if len(ext["description"]) > 50
                else ext["description"]
            ),
        )

    console.print(table)


def update_extension_imports(extension_id, action="add"):
    """Update the extension_imports.py file"""
    paths = get_project_paths()
    imports_file = os.path.join(
        paths["backend_dir"], "extension_packages", "extension_imports.py"
    )

    if not os.path.exists(imports_file):
        os.makedirs(os.path.dirname(imports_file), exist_ok=True)
        with open(imports_file, "w") as f:
            f.write("")
        console.print(f"[blue]Created extension imports file: {imports_file}[/blue]")

    with open(imports_file, "r") as f:
        content = f.read()

    import_line = f"import extension_packages.{extension_id}.entry"

    if action == "add":
        if import_line in content:
            console.print(
                f"[yellow]Import for {extension_id} already exists in extension_imports.py[/yellow]"
            )
            return True

        if content and not content.endswith("\n"):
            import_line = "\n" + import_line

        if content:
            import_line += "\n"

        with open(imports_file, "a") as f:
            f.write(import_line)
        console.print(
            f"[green]Added import for {extension_id} to extension_imports.py[/green]"
        )

    elif action == "remove":
        if import_line not in content:
            console.print(
                f"[yellow]No import for {extension_id} found in extension_imports.py[/yellow]"
            )
            return True

        lines = content.splitlines()
        new_lines = [line for line in lines if line.strip() != import_line]
        new_content = "\n".join(new_lines)

        if new_content and not new_content.endswith("\n"):
            new_content += "\n"

        with open(imports_file, "w") as f:
            f.write(new_content)
        console.print(
            f"[green]Removed import for {extension_id} from extension_imports.py[/green]"
        )

    return True


def package_extension_command(extension_id: str, output_dir: Optional[str] = None, output_path: Optional[str] = None, source_dir: Optional[str] = None):
    """Package an extension into a zip file
    
    Args:
        extension_id: ID of the extension to package
        output_dir: Directory to save the zip file (used if output_path not specified)
        output_path: Full path for the output zip file (overrides output_dir)
        source_dir: Source directory containing the extension
    """
    try:
        validate_extension_id(extension_id)
    except ValueError as e:
        console.print(f"[red]Error: {str(e)}[/red]")
        return False

    locations = find_extension_locations(extension_id, source_dir)

    paths = get_project_paths()
    
    # Determine output path
    if output_path:
        zip_path = output_path
    else:
        if not output_dir:
            output_dir = paths["project_root"]
        zip_filename = f"{extension_id}.zip"
        zip_path = os.path.join(output_dir, zip_filename)

    console.print(f"[blue]Packaging extension {extension_id}[/blue]")

    custom_route_path = os.path.join(paths["frontend_dir"], "src/routes", extension_id)
    if os.path.exists(custom_route_path):
        locations["frontend_custom_routes"] = paths["frontend_dir"]
        console.print(f"[blue]Detected custom route: {custom_route_path}[/blue]")

    if not locations:
        console.print(
            f"[red]Extension '{extension_id}' not found or has no components[/red]"
        )
        return False

    manifest = None
    manifest_path = None
    
    # First try to load root manifest.json from source_dir
    if source_dir:
        root_manifest_path = os.path.join(source_dir, "manifest.json")
        if os.path.exists(root_manifest_path):
            manifest_path = root_manifest_path
            try:
                with open(manifest_path, "r") as f:
                    manifest = json.load(f)
            except Exception as e:
                console.print(f"[red]Failed to load manifest: {e}[/red]")
                return False
    
    # Fall back to backend manifest.json if root manifest not found
    if not manifest and "backend" in locations:
        backend_manifest_path = os.path.join(locations["backend"], "manifest.json")
        if os.path.exists(backend_manifest_path):
            manifest_path = backend_manifest_path
            try:
                with open(manifest_path, "r") as f:
                    manifest = json.load(f)
            except Exception as e:
                console.print(f"[red]Failed to load manifest: {e}[/red]")
                return False

    if not manifest:
        manifest = {
            "name": extension_id,
            "version": "1.0.0",
            "description": f"{extension_id} extension",
            "author": "Smart Social Contracts",
            "permissions": [],
        }

    with tempfile.TemporaryDirectory() as temp_dir:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            if manifest_path and os.path.exists(manifest_path):
                zipf.write(manifest_path, "manifest.json")
                console.print("[blue]Added manifest.json[/blue]")
            else:
                temp_manifest_path = os.path.join(temp_dir, "manifest.json")
                with open(temp_manifest_path, "w") as f:
                    json.dump(manifest, f, indent=2)
                zipf.write(temp_manifest_path, "manifest.json")
                console.print("[blue]Added generated manifest.json[/blue]")

            if "backend" in locations:
                backend_src = locations["backend"]
                for root, _, files in os.walk(backend_src):
                    for file in files:
                        if file.endswith(".py") or file == "manifest.json":
                            file_path = os.path.join(root, file)
                            rel_path = os.path.relpath(file_path, backend_src)
                            zipf.write(file_path, os.path.join("backend", rel_path))
                            console.print(f"[blue]Added backend/{rel_path}[/blue]")

            # frontend/ and i18n/ are no longer bundled into packages;
            # extensions are loaded dynamically via frontend-rt at runtime.

    console.print(f"[green]Extension packaged successfully: {zip_path}[/green]")
    return True


def install_extension_command(package_path: str):
    """Deprecated: extensions are now installed dynamically at runtime."""
    console.print(
        "[yellow]install (from package) is deprecated. "
        "Extensions are now installed dynamically via the file registry at runtime.[/yellow]"
    )
    return True


def uninstall_extension_command(extension_id: str):
    """Uninstall an extension"""
    try:
        validate_extension_id(extension_id)
    except ValueError as e:
        console.print(f"[red]Error: {str(e)}[/red]")
        return False

    locations = find_extension_locations(extension_id)
    paths = get_extension_paths(extension_id)

    if not locations and not any(os.path.exists(path) for path in paths.values()):
        console.print(
            f"[red]Extension {extension_id} not found or already uninstalled[/red]"
        )
        return False

    if "backend" in locations:
        try:
            shutil.rmtree(locations["backend"])
            console.print(f"[green]Removed backend files for {extension_id}[/green]")

            update_extension_imports(extension_id, "remove")
        except Exception as e:
            console.print(f"[red]Failed to remove backend files: {e}[/red]")

    if "frontend_lib" in locations or os.path.exists(paths["frontend_lib"]):
        try:
            if os.path.exists(paths["frontend_lib"]):
                shutil.rmtree(paths["frontend_lib"])
                console.print(
                    f"[green]Removed frontend library files for {extension_id}[/green]"
                )
        except Exception as e:
            console.print(f"[red]Failed to remove frontend library files: {e}[/red]")

    if "frontend_route" in locations or os.path.exists(paths["frontend_route"]):
        try:
            if os.path.exists(paths["frontend_route"]):
                shutil.rmtree(paths["frontend_route"])
                console.print(
                    f"[green]Removed frontend route files for {extension_id}[/green]"
                )
        except Exception as e:
            console.print(f"[red]Failed to remove frontend route files: {e}[/red]")

    if "i18n" in locations or os.path.exists(paths["i18n"]):
        try:
            if os.path.exists(paths["i18n"]):
                shutil.rmtree(paths["i18n"])
                console.print(f"[green]Removed i18n files for {extension_id}[/green]")
        except Exception as e:
            console.print(f"[red]Failed to remove i18n files: {e}[/red]")

    if "frontend_custom_routes" in locations:
        try:
            shutil.rmtree(locations["frontend_custom_routes"])
            console.print(
                f"[green]Removed custom route files for {extension_id}[/green]"
            )
        except Exception as e:
            console.print(f"[red]Failed to remove custom route files: {e}[/red]")

    console.print(f"[green]Extension {extension_id} uninstalled successfully[/green]")
    return True


def uninstall_all_extensions_command():
    """Uninstall all installed extensions"""
    paths = get_project_paths()
    backend_ext_dir = os.path.join(paths["backend_dir"], "extension_packages")
    frontend_ext_dir = os.path.join(paths["frontend_dir"], "src/lib/extensions")

    extensions = set()

    # Find all backend extensions
    if os.path.exists(backend_ext_dir):
        for item in os.listdir(backend_ext_dir):
            if os.path.isdir(
                os.path.join(backend_ext_dir, item)
            ) and not item.startswith("__"):
                try:
                    validate_extension_id(item)
                    extensions.add(item)
                except ValueError:
                    pass

    # Find all frontend extensions
    if os.path.exists(frontend_ext_dir):
        for item in os.listdir(frontend_ext_dir):
            if os.path.isdir(
                os.path.join(frontend_ext_dir, item)
            ) and not item.startswith("__"):
                try:
                    validate_extension_id(item)
                    extensions.add(item)
                except ValueError:
                    pass

    if not extensions:
        console.print("[yellow]No extensions found to uninstall[/yellow]")
        return True

    console.print(f"[blue]Found {len(extensions)} extension(s) to uninstall[/blue]")
    
    success_count = 0
    failed_count = 0
    
    for ext_id in sorted(extensions):
        console.print(f"\n[blue]Uninstalling {ext_id}...[/blue]")
        if uninstall_extension_command(ext_id):
            success_count += 1
        else:
            failed_count += 1

    console.print(
        f"\n[green]Uninstallation complete: {success_count} succeeded, {failed_count} failed[/green]"
    )
    
    # Regenerate manifests and registry after uninstalling all
    if success_count > 0:
        generate_extension_manifests()
        generate_extension_registry()
    
    return failed_count == 0


def generate_extension_registry():
    """Generate registry.py that maps extension functions for the backend"""
    paths = get_project_paths()
    backend_ext_dir = os.path.join(paths["backend_dir"], "extension_packages")
    registry_file = os.path.join(backend_ext_dir, "registry.py")
    
    # Get list of installed extensions
    extensions = []
    if os.path.exists(backend_ext_dir):
        for item in os.listdir(backend_ext_dir):
            ext_path = os.path.join(backend_ext_dir, item)
            if os.path.isdir(ext_path) and not item.startswith("__"):
                entry_file = os.path.join(ext_path, "entry.py")
                if os.path.exists(entry_file):
                    extensions.append(item)
    
    # Generate the registry file
    content = '''"""
Extension Function Registry

This file provides a centralized registry for all extension functions.
It maps extension names and function names to their actual implementations.

Auto-generated by the realms CLI extension command.
DO NOT EDIT MANUALLY - your changes will be overwritten.
"""

from typing import Callable, Optional
from ic_python_logging import get_logger

logger = get_logger("extension_packages.registry")

'''
    
    # Import all extension entry modules
    for ext_id in extensions:
        content += f'import extension_packages.{ext_id}.entry as {ext_id}_entry\n'
    
    content += '''

def get_func(extension_name: str, function_name: str) -> Optional[Callable]:
    """
    Get a function from an extension by name.
    
    Args:
        extension_name: The ID of the extension
        function_name: The name of the function to retrieve
        
    Returns:
        The function object if found, None otherwise
    """
    logger.debug(f"Looking up function '{function_name}' in extension '{extension_name}'")
    
    # Map extension names to their entry modules
    extension_modules = {
'''
    
    for ext_id in extensions:
        content += f'        "{ext_id}": {ext_id}_entry,\n'
    
    content += '''    }
    
    # Get the extension module
    module = extension_modules.get(extension_name)
    if not module:
        logger.error(f"Extension '{extension_name}' not found in registry")
        raise ValueError(f"Extension '{extension_name}' not found")
    
    # Get the function from the module
    if not hasattr(module, function_name):
        logger.error(f"Function '{function_name}' not found in extension '{extension_name}'")
        raise AttributeError(f"Extension '{extension_name}' has no function '{function_name}'")
    
    func = getattr(module, function_name)
    logger.debug(f"Found function '{function_name}' in extension '{extension_name}'")
    return func
'''
    
    # Write the file
    os.makedirs(backend_ext_dir, exist_ok=True)
    with open(registry_file, "w") as f:
        f.write(content)
    
    console.print(f"[green]Generated registry.py with {len(extensions)} extensions[/green]")
    return True


def generate_extension_manifests():
    """Generate extension_manifests.py file from installed extension manifests"""
    paths = get_project_paths()
    backend_ext_dir = os.path.join(paths["backend_dir"], "extension_packages")
    manifests_file = os.path.join(backend_ext_dir, "extension_manifests.py")
    
    manifests = {}
    
    # Scan for installed extensions with manifests
    if os.path.exists(backend_ext_dir):
        for item in os.listdir(backend_ext_dir):
            ext_path = os.path.join(backend_ext_dir, item)
            if os.path.isdir(ext_path) and not item.startswith("__"):
                manifest_path = os.path.join(ext_path, "manifest.json")
                if os.path.exists(manifest_path):
                    try:
                        with open(manifest_path, "r") as f:
                            manifest_data = json.load(f)
                            manifests[item] = manifest_data
                    except Exception as e:
                        console.print(f"[yellow]Warning: Could not load manifest for {item}: {e}[/yellow]")
    
    # Generate the Python file
    content = '''"""
Static extension manifest registry for Basilisk canister environment.
This file contains all extension manifests as Python dictionaries since
Basilisk canisters don't have filesystem access.

This file is auto-generated by the realms CLI extension command.
DO NOT EDIT MANUALLY - your changes will be overwritten.
"""

# Extension manifests embedded as Python dictionaries
EXTENSION_MANIFESTS = {
'''
    
    for ext_id, manifest in manifests.items():
        # Use pformat for readable formatting, then indent properly
        formatted = pformat(manifest, width=100, compact=False)
        # Add proper indentation (4 spaces for extension level)
        indented = '\n'.join('    ' + line for line in formatted.split('\n'))
        content += f'    "{ext_id}": {indented},\n\n'
    
    content += '''}\n\n
def get_all_extension_manifests() -> dict:
    """Get all extension manifests"""
    return EXTENSION_MANIFESTS
'''
    
    # Write the file
    os.makedirs(backend_ext_dir, exist_ok=True)
    with open(manifests_file, "w") as f:
        f.write(content)
    
    console.print(f"[green]Generated extension_manifests.py with {len(manifests)} extensions[/green]")
    return True


def install_from_source_command(source_dir: str = "extensions"):
    """Deprecated: extensions are now loaded dynamically at runtime."""
    console.print(
        "[yellow]install-from-source is deprecated. "
        "Extensions are now loaded dynamically via the file registry at runtime.[/yellow]"
    )
    return True


def _parse_candid_string(raw: str) -> str:
    """Parse a Candid text response from dfx canister call."""
    raw = raw.strip()
    if raw.startswith("(") and raw.endswith(")"):
        raw = raw[1:-1].strip()
    if raw.endswith(","):
        raw = raw[:-1].strip()
    if raw.startswith('"') and raw.endswith('"'):
        raw = raw[1:-1]
    return _candid_unescape(raw)


_CANDID_SIMPLE_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "'": "'", "\\": "\\"}
_HEX_DIGITS = set("0123456789abcdefABCDEF")


def _candid_unescape(text: str) -> str:
    """Undo Candid text escapes in one pass: \\n \\t \\r \\" \\' \\\\ \\u{X} and \\HH bytes."""
    buf = bytearray()
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c != "\\" or i + 1 >= n:
            buf += c.encode("utf-8")
            i += 1
            continue
        nxt = text[i + 1]
        if nxt in _CANDID_SIMPLE_ESCAPES:
            buf += _CANDID_SIMPLE_ESCAPES[nxt].encode("utf-8")
            i += 2
        elif nxt == "u" and i + 2 < n and text[i + 2] == "{":
            end = text.find("}", i + 3)
            try:
                buf += chr(int(text[i + 3 : end], 16)).encode("utf-8")
                i = end + 1
            except ValueError:
                buf += b"\\"
                i += 1
        elif i + 2 < n and text[i + 1] in _HEX_DIGITS and text[i + 2] in _HEX_DIGITS:
            buf.append(int(text[i + 1 : i + 3], 16))
            i += 3
        else:
            buf += b"\\"
            i += 1
    return buf.decode("utf-8", errors="replace")


# dfx → icp translation notes:
# - This toolchain deprecates dfx in favour of icp; use icp when present.
# - The dfx network aliases test/staging/demo all point at mainnet (icp0.io);
#   icp addresses mainnet as "ic".
# - The dfx identity "deployer" exists in the icp store as "my_dev_identity_1"
#   (same principal). Unknown identity names are passed through unchanged.
_ICP_NETWORK_ALIASES = {"test": "ic", "staging": "ic", "demo": "ic", "production": "ic"}
_ICP_IDENTITY_ALIASES = {"deployer": "my_dev_identity_1"}

_PIN_FILES: list = []


def _hsm_pin_file():
    """PIN file for a hardware-key identity, or None.

    icp ignores ``DFX_HSM_PIN`` and cannot prompt while we capture its output.
    ``ICP_IDENTITY_PASSWORD_FILE`` is used as-is; otherwise a 0600 tempfile is
    written from ``DFX_HSM_PIN`` once per process (same convention as Casals)."""
    import atexit
    import tempfile as _tempfile

    explicit = (os.environ.get("ICP_IDENTITY_PASSWORD_FILE") or "").strip()
    if explicit:
        return explicit
    pin = os.environ.get("DFX_HSM_PIN") or ""
    if not pin:
        return None
    if _PIN_FILES:
        return _PIN_FILES[0]
    tmp = _tempfile.NamedTemporaryFile("w", prefix="realms-hsm-pin-", delete=False)
    os.chmod(tmp.name, 0o600)
    tmp.write(pin)
    tmp.close()
    _PIN_FILES.append(tmp.name)
    atexit.register(_cleanup_pin_files)
    return tmp.name


def _cleanup_pin_files():
    for path in _PIN_FILES:
        try:
            os.unlink(path)
        except OSError:
            pass
    _PIN_FILES.clear()


def _dfx_call(canister, method, arg, network, identity, is_query=False, timeout=120, raise_on_error=True):
    """Run a canister call (via icp when available, else dfx) and return parsed output.

    For large candid arguments (≥ 100 KiB) the argument is written to a
    temporary file and passed via ``--args-file``/``--argument-file``, otherwise
    Linux's per-argument size limit (MAX_ARG_STRLEN, 128 KiB) blows up before
    the call ever reaches the replica.
    """
    import tempfile as _tempfile

    use_icp = shutil.which("icp") is not None
    if use_icp:
        cmd = ["icp", "canister", "call"]
        icp_identity = _ICP_IDENTITY_ALIASES.get(identity, identity)
        if icp_identity:
            cmd.extend(["--identity", icp_identity])
            pin_file = _hsm_pin_file()
            if pin_file:
                cmd.extend(["--identity-password-file", pin_file])
        icp_network = _ICP_NETWORK_ALIASES.get(network, network or "ic")
        cmd.extend(["--network", icp_network])
        if icp_network.startswith(("http://", "https://")):
            # A replica by URL (the Casals e2e harness's local network): icp
            # needs to be told where the root key comes from.
            cmd.extend(["--root-key", "fetch"])
        if is_query:
            cmd.append("--query")
    else:
        cmd = ["dfx", "canister", "call"]
        if identity:
            cmd.extend(["--identity", identity])
        if network:
            cmd.extend(["--network", network])
        if is_query:
            cmd.append("--query")

    arg_file = None
    arg_size = len(arg.encode("utf-8")) if isinstance(arg, str) else len(arg)
    if arg_size >= 100 * 1024:
        fd, arg_file = _tempfile.mkstemp(prefix="call-arg-", suffix=".did")
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(arg)
            cmd.extend([canister, method, "--args-file" if use_icp else "--argument-file", arg_file])
        except Exception:
            try:
                os.unlink(arg_file)
            except OSError:
                pass
            raise
    else:
        cmd.extend([canister, method, arg])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip()
            if not raise_on_error:
                return err
            console.print(f"[red]Error: {err}[/red]")
            raise typer.Exit(1)
        return _parse_candid_string(result.stdout)
    except subprocess.TimeoutExpired:
        console.print(f"[red]Error: canister call timed out ({timeout}s)[/red]")
        raise typer.Exit(1)
    except FileNotFoundError:
        console.print("[red]Error: neither icp nor dfx found. Install the IC toolchain.[/red]")
        raise typer.Exit(1)
    finally:
        if arg_file:
            try:
                os.unlink(arg_file)
            except OSError:
                pass


def _ic_safe_text(content: str) -> str:
    """Strip astral Unicode (emoji) for IC narrow-unicode filesystem writes.

    ``open(path, 'w')`` on the canister raises ``UnicodeEncodeError: surrogates
    not allowed`` when extension sources contain emoji. Registry installs hit
    the same path; direct runtime-install sanitizes at upload time.
    """
    return "".join(c if ord(c) < 0x10000 else "?" for c in content)


def _collect_backend_package_files(source_dir: str) -> dict:
    """Collect manifest.json plus backend/*.py and backend/**/*.json (flattened).

    Matches what ``install_extension`` expects on the canister: ``entry.py`` at
    the extension root and ``data/*.json`` beside it (``backend/`` prefix
    stripped), same as ``install_extension_from_registry``.
    """
    files = {}

    manifest_path = os.path.join(source_dir, "manifest.json")
    if not os.path.exists(manifest_path):
        console.print(f"[red]Error: manifest.json not found in {source_dir}[/red]")
        raise typer.Exit(1)
    with open(manifest_path, "r") as f:
        files["manifest.json"] = _ic_safe_text(f.read())

    backend_dir = os.path.join(source_dir, "backend")
    if os.path.isdir(backend_dir):
        for root, _dirs, filenames in os.walk(backend_dir):
            for fname in filenames:
                if not fname.endswith((".py", ".json")):
                    continue
                full_path = os.path.join(root, fname)
                rel = os.path.relpath(full_path, backend_dir).replace(os.sep, "/")
                with open(full_path, "r") as f:
                    files[rel] = _ic_safe_text(f.read())
    else:
        entry_path = os.path.join(source_dir, "entry.py")
        if os.path.exists(entry_path):
            with open(entry_path, "r") as f:
                files["entry.py"] = _ic_safe_text(f.read())

    if "entry.py" not in files:
        console.print(
            f"[red]Error: entry.py not found in {source_dir}/backend/ or {source_dir}/[/red]"
        )
        raise typer.Exit(1)

    return files


def _collect_runtime_extension_files(source_dir: str) -> dict:
    """Collect extension backend files for ``install_extension``."""
    return _collect_backend_package_files(source_dir)


def _upload_extension_frontend_bundle(
    frontend_canister: str,
    ext_id: str,
    version: str,
    bundle_path: str,
    network: str,
    identity: Optional[str] = None,
) -> None:
    """Push ``frontend-rt/dist/index.js`` to the realm asset canister (same-origin)."""
    if not os.path.isfile(bundle_path):
        console.print(f"  [yellow]![/yellow] frontend bundle not found, skipping: {bundle_path}")
        return

    with open(bundle_path, "rb") as f:
        data = f.read()

    asset_key = f"/ext/{ext_id}/{version}/frontend/dist/index.js"
    console.print(f"  Uploading frontend bundle ({len(data):,} bytes) → {asset_key}")

    blob_hex = "".join(f"\\{b:02x}" for b in data)
    arg = (
        f'(record {{ key="{asset_key}"; content_type="application/javascript"; '
        f'content=blob "{blob_hex}"; content_encoding="identity"; sha256=null }})'
    )
    _dfx_call(frontend_canister, "store", arg, network, identity, timeout=120)
    console.print(f"  [green]✓[/green] frontend bundle uploaded")


def _extension_sync_call(
    backend_canister: str,
    ext_id: str,
    method: str,
    args: str,
    network: str,
    identity: Optional[str] = None,
) -> None:
    """Best-effort post-install hook (``initialize``, codex ``init``, …)."""
    escaped_args = args.replace("\\", "\\\\").replace('"', '\\"')
    candid = f'("{ext_id}", "{method}", "{escaped_args}")'
    try:
        raw = _dfx_call(
            backend_canister, "extension_sync_call", candid, network, identity, timeout=120
        )
        console.print(f"  [dim]{method}(): {raw[:200]}[/dim]")
    except Exception as e:
        console.print(f"  [yellow]⚠ {method}() hook: {e}[/yellow]")


def runtime_install_command(
    canister: str,
    source_dir: str,
    network: str = "local",
    identity: Optional[str] = None,
    frontend_canister: Optional[str] = None,
):
    """Install an extension on a deployed realm canister at runtime.

    Reads backend files from the source directory and uploads them to the
    canister's persistent filesystem via the install_extension endpoint.
    """
    source_dir = os.path.abspath(source_dir)
    if not os.path.isdir(source_dir):
        console.print(f"[red]Error: {source_dir} is not a directory[/red]")
        raise typer.Exit(1)

    manifest_path = os.path.join(source_dir, "manifest.json")
    with open(manifest_path, "r") as f:
        manifest = json.loads(f.read())
    ext_id = manifest.get("name") or os.path.basename(source_dir)
    version = str(manifest.get("version") or "0.0.0")

    console.print(f"[blue]Installing extension '{ext_id}' on {canister} ({network})...[/blue]")

    files = _collect_runtime_extension_files(source_dir)
    total_bytes = sum(len(v) for v in files.values())
    console.print(f"  Files: {len(files)} ({total_bytes:,} bytes)")
    for fname, content in sorted(files.items()):
        console.print(f"    {fname} ({len(content):,} bytes)")

    payload = json.dumps({"extension_id": ext_id, "files": files})
    candid_arg = '("' + payload.replace("\\", "\\\\").replace('"', '\\"') + '")'

    console.print("  Uploading backend to canister...")
    raw = _dfx_call(canister, "install_extension", candid_arg, network, identity, timeout=300)

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        console.print(f"  Response: {raw}")
        return

    if not result.get("success"):
        console.print(f"[red]  ✗ Installation failed: {result.get('error', 'unknown error')}[/red]")
        raise typer.Exit(1)

    console.print(f"[green]  ✓ Extension '{ext_id}' backend installed[/green]")

    if frontend_canister:
        bundle = os.path.join(source_dir, "frontend-rt", "dist", "index.js")
        _upload_extension_frontend_bundle(
            frontend_canister, ext_id, version, bundle, network, identity
        )

    _extension_sync_call(canister, ext_id, "initialize", "{}", network, identity)


def runtime_uninstall_command(canister: str, extension_id: str, network: str = "local", identity: Optional[str] = None):
    """Uninstall a runtime extension from a deployed realm canister."""
    console.print(f"[blue]Uninstalling extension '{extension_id}' from {canister} ({network})...[/blue]")

    payload = json.dumps({"extension_id": extension_id})
    candid_arg = '("' + payload.replace("\\", "\\\\").replace('"', '\\"') + '")'

    raw = _dfx_call(canister, "uninstall_extension", candid_arg, network, identity)

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        console.print(f"  Response: {raw}")
        return

    if result.get("success"):
        console.print(f"[green]  ✓ Extension '{extension_id}' uninstalled[/green]")
    else:
        console.print(f"[red]  ✗ Uninstall failed: {result.get('error', 'unknown error')}[/red]")
        raise typer.Exit(1)


def runtime_list_command(canister: str, network: str = "local", identity: Optional[str] = None, raw_json: bool = False):
    """List runtime-installed extensions on a deployed realm canister."""
    raw = _dfx_call(canister, "list_runtime_extensions", "()", network, identity, is_query=True)

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        console.print(raw)
        return

    if raw_json:
        console.print(json.dumps(result, indent=2))
        return

    if not result.get("success"):
        console.print(f"[red]Error: {result.get('error', 'unknown')}[/red]")
        raise typer.Exit(1)

    extensions = result.get("runtime_extensions", [])
    manifests = result.get("all_manifests", {})

    if not extensions and not manifests:
        console.print("[yellow]No extensions installed.[/yellow]")
        return

    if extensions:
        table = Table(title=f"Runtime Extensions ({len(extensions)})")
        table.add_column("ID", style="cyan")
        table.add_column("Version", style="green")
        table.add_column("Description", style="white")

        for ext_id in extensions:
            m = manifests.get(ext_id, {})
            version = m.get("version", "?")
            desc = m.get("description", "")
            table.add_row(ext_id, version, desc[:60] + "..." if len(desc) > 60 else desc)

        console.print(table)

    baked = [k for k in manifests if k not in extensions]
    if baked:
        table = Table(title=f"Baked-in Extensions ({len(baked)})")
        table.add_column("ID", style="cyan")
        table.add_column("Version", style="green")
        table.add_column("Description", style="white")

        for ext_id in baked:
            m = manifests[ext_id]
            version = m.get("version", "?")
            desc = m.get("description", "")
            table.add_row(ext_id, version, desc[:60] + "..." if len(desc) > 60 else desc)

        console.print(table)


def registry_install_command(canister: str, registry: str, ext_id: str, version: Optional[str] = None, network: str = "local", identity: Optional[str] = None):
    """Install an extension from the file registry via the realm canister."""
    console.print(f"[blue]Installing extension '{ext_id}' from registry {registry} on {canister} ({network})...[/blue]")
    if version:
        console.print(f"  Version: {version}")
    else:
        console.print("  Version: latest")

    payload = json.dumps({
        "registry_canister_id": registry,
        "ext_id": ext_id,
        "version": version,
    })
    candid_arg = '("' + payload.replace("\\", "\\\\").replace('"', '\\"') + '")'

    raw = _dfx_call(canister, "install_extension_from_registry", candid_arg, network, identity, timeout=300)

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        console.print(f"  Response: {raw}")
        return

    if result.get("success"):
        console.print(f"[green]  ✓ Extension '{ext_id}' installed (version {result.get('version', '?')})[/green]")
    else:
        console.print(f"[red]  ✗ Installation failed: {result.get('error', 'unknown error')}[/red]")
        raise typer.Exit(1)


def resync_frontends_command(
    canister: str,
    registry: Optional[str] = None,
    frontend: Optional[str] = None,
    network: str = "local",
    identity: Optional[str] = None,
):
    """Re-copy installed extensions' frontend bundles onto the realm frontend."""
    console.print(
        f"[blue]Resyncing extension frontends on {canister} ({network})...[/blue]"
    )
    payload = {}
    if registry:
        payload["registry_canister_id"] = registry
    if frontend:
        payload["frontend_canister_id"] = frontend
    candid_arg = '("' + json.dumps(payload).replace("\\", "\\\\").replace('"', '\\"') + '")'
    raw = _dfx_call(
        canister, "resync_extension_frontends", candid_arg, network, identity, timeout=600,
    )
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        console.print(f"  Response: {raw}")
        return
    if result.get("success"):
        synced = result.get("synced") or []
        console.print(f"[green]  ✓ Resynced {len(synced)} extension frontend bundle(s)[/green]")
        for item in synced:
            console.print(
                f"    - {item.get('extension_id')}@{item.get('version', '?')}"
            )
        skipped = result.get("skipped") or []
        if skipped:
            console.print(f"[yellow]  Skipped {len(skipped)} (no version/registry)[/yellow]")
    else:
        console.print(f"[red]  ✗ Resync failed[/red]")
        for err in result.get("errors") or []:
            console.print(f"    - {err.get('extension_id')}: {err.get('error')}")
        if result.get("error"):
            console.print(f"  {result['error']}")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# Codex CLI commands
# ---------------------------------------------------------------------------


def _collect_codex_files(source_dir: str) -> dict:
    """Collect codex package files for direct install (includes JSON data files)."""
    return _collect_backend_package_files(source_dir)


def codex_runtime_install_command(canister: str, source_dir: str, codex_id: Optional[str] = None, run_init: bool = True, network: str = "local", identity: Optional[str] = None):
    """Install a codex package on a deployed realm canister at runtime."""
    source_dir = os.path.abspath(source_dir)
    if not os.path.isdir(source_dir):
        console.print(f"[red]Error: {source_dir} is not a directory[/red]")
        raise typer.Exit(1)

    manifest_path = os.path.join(source_dir, "manifest.json")
    with open(manifest_path, "r") as f:
        manifest = json.loads(f.read())

    if not codex_id:
        codex_id = manifest.get("id") or os.path.basename(source_dir)

    is_hook_codex = manifest.get("kind") == "codex"

    console.print(f"[blue]Installing codex '{codex_id}' on {canister} ({network})...[/blue]")

    files = _collect_codex_files(source_dir)
    total_bytes = sum(len(v) for v in files.values())
    console.print(f"  Files: {len(files)} ({total_bytes:,} bytes)")
    for fname, content in sorted(files.items()):
        console.print(f"    {fname} ({len(content):,} bytes)")

    if is_hook_codex:
        payload = json.dumps({"extension_id": codex_id, "files": files})
        candid_arg = '("' + payload.replace("\\", "\\\\").replace('"', '\\"') + '")'
        method = "install_extension"
    else:
        payload = json.dumps({"codex_id": codex_id, "files": files, "run_init": run_init})
        candid_arg = '("' + payload.replace("\\", "\\\\").replace('"', '\\"') + '")'
        method = "install_codex"

    console.print(f"  Uploading via {method}...")
    raw = _dfx_call(canister, method, candid_arg, network, identity, timeout=300)

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        console.print(f"  Response: {raw}")
        return

    if not result.get("success"):
        console.print(f"[red]  ✗ Installation failed: {result.get('error', 'unknown error')}[/red]")
        raise typer.Exit(1)

    console.print(f"[green]  ✓ Codex '{codex_id}' installed[/green]")

    if is_hook_codex and run_init:
        _extension_sync_call(canister, codex_id, "init", "{}", network, identity)
    elif result.get("init_warning"):
        console.print(f"[yellow]  ⚠ init warning: {result['init_warning']}[/yellow]")


def codex_runtime_uninstall_command(canister: str, codex_id: str, network: str = "local", identity: Optional[str] = None):
    """Uninstall a codex package from a deployed realm canister."""
    console.print(f"[blue]Uninstalling codex '{codex_id}' from {canister} ({network})...[/blue]")

    payload = json.dumps({"codex_id": codex_id})
    candid_arg = '("' + payload.replace("\\", "\\\\").replace('"', '\\"') + '")'

    raw = _dfx_call(canister, "uninstall_codex", candid_arg, network, identity)

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        console.print(f"  Response: {raw}")
        return

    if result.get("success"):
        console.print(f"[green]  ✓ Codex '{codex_id}' uninstalled[/green]")
    else:
        console.print(f"[red]  ✗ Uninstall failed: {result.get('error', 'unknown error')}[/red]")
        raise typer.Exit(1)


def codex_runtime_list_command(canister: str, network: str = "local", identity: Optional[str] = None, raw_json: bool = False):
    """List codex packages installed on a deployed realm canister."""
    raw = _dfx_call(canister, "list_codex_packages", "()", network, identity, is_query=True)

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        console.print(raw)
        return

    if raw_json:
        console.print(json.dumps(result, indent=2))
        return

    if not result.get("success"):
        console.print(f"[red]Error: {result.get('error', 'unknown')}[/red]")
        raise typer.Exit(1)

    packages = result.get("codex_packages", [])
    manifests = result.get("manifests", {})

    if not packages:
        console.print("[yellow]No codex packages installed.[/yellow]")
        return

    table = Table(title=f"Codex Packages ({len(packages)})")
    table.add_column("ID", style="cyan")
    table.add_column("Version", style="green")
    table.add_column("Description", style="white")

    for pkg_id in packages:
        m = manifests.get(pkg_id, {})
        version = m.get("version", "?")
        desc = m.get("description", "")
        table.add_row(pkg_id, version, desc[:60] + "..." if len(desc) > 60 else desc)

    console.print(table)


# ---------------------------------------------------------------------------
# Publish commands — upload extensions / codices to file_registry
# ---------------------------------------------------------------------------

# Each store_file_chunk JSON-parses + base64-decodes its payload inside the WASI
# Python runtime; ~200 KiB/chunk blows the 40B per-message instruction budget, so
# keep raw chunks small (64 KiB raw -> ~88 KiB base64 payload).
_PUBLISH_CHUNK_SIZE_BYTES = 64 * 1024
# Chunks concatenated per finalize_chunked_file_step call. Concatenation is cheap
# (the expensive on-chain SHA-256 is skipped — we pass the local hash), so several
# 64 KiB chunks per message stay well under budget.
_PUBLISH_FINALIZE_BATCH = 8


def _content_type_for(name: str) -> str:
    name_lower = name.lower()
    if name_lower.endswith(".json"):
        return "application/json"
    if name_lower.endswith(".js") or name_lower.endswith(".mjs"):
        return "application/javascript"
    if name_lower.endswith(".css"):
        return "text/css"
    if name_lower.endswith(".html"):
        return "text/html"
    if name_lower.endswith(".wasm") or name_lower.endswith(".wasm.gz"):
        return "application/wasm"
    if name_lower.endswith(".py"):
        return "text/x-python"
    if name_lower.endswith(".svg"):
        return "image/svg+xml"
    if name_lower.endswith(".png"):
        return "image/png"
    if name_lower.endswith(".jpg") or name_lower.endswith(".jpeg"):
        return "image/jpeg"
    if name_lower.endswith(".webp"):
        return "image/webp"
    if name_lower.endswith(".md") or name_lower.endswith(".txt"):
        return "text/plain"
    return "application/octet-stream"


def _local_file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _fetch_namespace_hashes(
    registry: str, namespace: str, network: str, identity: Optional[str],
) -> dict:
    """Query list_files for a namespace and return {path: sha256} dict.

    Uses --query (free, no cycles burned). Returns empty dict on any error
    so callers fall through to uploading everything.
    """
    try:
        arg = json.dumps({"namespace": namespace})
        candid_arg = '("' + arg.replace("\\", "\\\\").replace('"', '\\"') + '")'
        raw = _dfx_call(
            registry, "list_files", candid_arg, network, identity,
            is_query=True, timeout=30,
        )
        files = json.loads(raw)
        if isinstance(files, list):
            return {f["path"]: f.get("sha256", "") for f in files}
    except Exception:
        pass
    return {}


def _upload_one_file(
    registry: str,
    namespace: str,
    registry_path: str,
    local_path: str,
    network: str,
    identity: Optional[str],
    existing_hashes: Optional[dict] = None,
) -> str:
    """Upload a single file to file_registry, picking single-shot or chunked upload.

    If existing_hashes is provided and the local file's SHA-256 matches
    the on-chain hash, the upload is skipped entirely (no update call).

    Returns "uploaded", "skipped", or "failed".
    """
    import base64 as _b64

    size = os.path.getsize(local_path)

    if existing_hashes and registry_path in existing_hashes:
        local_hash = _local_file_sha256(local_path)
        if local_hash == existing_hashes[registry_path]:
            console.print(f"  [dim]⊜[/dim] {namespace}/{registry_path} [dim]unchanged[/dim]")
            return "skipped"

    content_type = _content_type_for(local_path)

    total_chunks = max(1, (size + _PUBLISH_CHUNK_SIZE_BYTES - 1) // _PUBLISH_CHUNK_SIZE_BYTES)
    if total_chunks > 1:
        console.print(
            f"  [blue]…[/blue] chunked upload of {namespace}/{registry_path} "
            f"({size:,} bytes, {total_chunks} chunks)"
        )
    with open(local_path, "rb") as fh:
        for chunk_index in range(total_chunks):
            blob = fh.read(_PUBLISH_CHUNK_SIZE_BYTES)
            payload = json.dumps({
                "namespace": namespace,
                "path": registry_path,
                "chunk_index": chunk_index,
                "total_chunks": total_chunks,
                "data_b64": _b64.b64encode(blob).decode("ascii"),
                "content_type": content_type,
            })
            candid_arg = '("' + payload.replace("\\", "\\\\").replace('"', '\\"') + '")'
            raw = _dfx_call(
                registry,
                "store_file_chunk",
                candid_arg,
                network,
                identity,
                timeout=600,
            )
            try:
                res = json.loads(raw)
            except json.JSONDecodeError:
                res = {"raw": raw}
            if not (isinstance(res, dict) and (res.get("ok") is True or res.get("success") is True)):
                console.print(
                    f"  [red]✗[/red] chunk {chunk_index + 1}/{total_chunks} of "
                    f"{registry_path} failed: {res}"
                )
                return "failed"

    local_sha = _local_file_sha256(local_path)
    while True:
        finalize_payload = json.dumps({
            "namespace": namespace,
            "path": registry_path,
            "expected_sha256": local_sha,
            "batch_size": _PUBLISH_FINALIZE_BATCH,
        })
        candid_arg = '("' + finalize_payload.replace("\\", "\\\\").replace('"', '\\"') + '")'
        raw = _dfx_call(
            registry,
            "finalize_chunked_file_step",
            candid_arg,
            network,
            identity,
            timeout=600,
            raise_on_error=False,
        )
        try:
            res = json.loads(raw)
        except json.JSONDecodeError:
            res = {"raw": raw}
        missing_step = (
            "no update method" in str(raw).lower()
            or "ic0536" in str(raw).lower()
        )
        if missing_step:
            oneshot = json.dumps({
                "namespace": namespace,
                "path": registry_path,
                "sha256": local_sha,
            })
            oneshot_arg = '("' + oneshot.replace("\\", "\\\\").replace('"', '\\"') + '")'
            console.print(
                f"  [dim]{registry} has no finalize_chunked_file_step; "
                "using finalize_chunked_file[/dim]"
            )
            raw = _dfx_call(
                registry,
                "finalize_chunked_file",
                oneshot_arg,
                network,
                identity,
                timeout=600,
            )
            try:
                res = json.loads(raw)
            except json.JSONDecodeError:
                res = {"raw": raw}
            if isinstance(res, dict) and (res.get("ok") is True or res.get("success") is True):
                label = "chunked" if total_chunks > 1 else "stored"
                console.print(f"  [green]✓[/green] {namespace}/{registry_path} ({size:,} bytes, {label})")
                _remember_uploaded_hash(existing_hashes, registry_path, local_sha)
                return "uploaded"
        if not (isinstance(res, dict) and res.get("ok") is True):
            console.print(f"  [red]✗[/red] finalize failed for {registry_path}: {res}")
            return "failed"
        if res.get("done") is True:
            label = "chunked" if total_chunks > 1 else "stored"
            console.print(f"  [green]✓[/green] {namespace}/{registry_path} ({size:,} bytes, {label})")
            _remember_uploaded_hash(existing_hashes, registry_path, local_sha)
            return "uploaded"


_SCREENSHOT_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


def _is_safe_package_relative_path(path: str) -> bool:
    normalized = path.replace("\\", "/").strip()
    if not normalized or os.path.isabs(normalized) or normalized.startswith("/"):
        return False
    return ".." not in normalized.split("/")


def _iter_manifest_screenshot_uploads(
    source_dir: str,
    manifest: dict,
) -> tuple[list[tuple[str, str]], list[str]]:
    """Return (registry_path, local_path) pairs and publish-fatal warnings.

    Driven by ``manifest["screenshots"]`` so a missing or unsafe path fails
    the release instead of shipping a broken marketplace listing.
    """
    declared = manifest.get("screenshots") or []
    if not isinstance(declared, list):
        return [], [
            "  [yellow]![/yellow] manifest screenshots must be an array"
        ]

    specs: list[tuple[str, str]] = []
    warnings: list[str] = []

    for entry in sorted(declared, key=lambda x: str(x)):
        if not isinstance(entry, str) or not entry.replace("\\", "/").strip():
            warnings.append(
                f"  [yellow]![/yellow] screenshot entry must be a non-empty string: {entry!r}"
            )
            continue
        rel = entry.replace("\\", "/").strip()
        if not _is_safe_package_relative_path(rel):
            warnings.append(
                f"  [yellow]![/yellow] rejected unsafe screenshot path: {entry}"
            )
            continue
        if not rel.lower().endswith(_SCREENSHOT_IMAGE_SUFFIXES):
            warnings.append(
                f"  [yellow]![/yellow] unsupported screenshot format (expected "
                f"{', '.join(_SCREENSHOT_IMAGE_SUFFIXES)}): {rel}"
            )
            continue
        local = os.path.join(source_dir, rel)
        if not os.path.isfile(local):
            warnings.append(f"  [yellow]![/yellow] screenshot not found: {rel}")
            continue
        specs.append((rel, local))

    return specs, warnings


def _publish_namespace(
    registry: str,
    namespace: str,
    network: str,
    identity: Optional[str],
):
    payload = json.dumps({"namespace": namespace})
    candid_arg = '("' + payload.replace("\\", "\\\\").replace('"', '\\"') + '")'
    raw = _dfx_call(
        registry, "publish_namespace", candid_arg, network, identity, timeout=120,
        raise_on_error=False,
    )
    # The standalone file-registry canister (smart-social-contracts/file-registry)
    # has no staging step: a stored file is live. Only the retired conductor-owned
    # registry exposed publish_namespace, so its absence is success, not failure.
    if "no update method" in str(raw).lower() or "ic0536" in str(raw).lower():
        console.print(
            f"  [dim]{registry} has no publish_namespace; stored files are live[/dim]"
        )
        return True
    try:
        res = json.loads(raw)
    except json.JSONDecodeError:
        res = {"raw": raw}
    if isinstance(res, dict) and (res.get("ok") is True or res.get("success") is True):
        console.print(f"  [green]✓[/green] published namespace {namespace}")
        return True
    console.print(f"  [red]✗[/red] publish_namespace({namespace}) failed: {res}")
    return False


def publish_extension_command(
    registry: str,
    source_dir: str,
    extension_id: Optional[str] = None,
    version: Optional[str] = None,
    bundle_path: Optional[str] = None,
    namespace_prefix: str = "ext",
    skip_publish: bool = False,
    network: str = "ic",
    identity: Optional[str] = None,
):
    """Publish an extension (manifest, backend/, optional frontend-rt bundle, i18n)
    to a file_registry canister under ``{namespace_prefix}/<id>/<version>/``.

    Layout uploaded:
      manifest.json
      backend/<py files>
      frontend/dist/index.html, index.js, assets/…   (frontend-rt/dist; frontend/dist only for paths the runtime build does not ship)
      frontend/i18n/locales/<locale>.json         (if frontend/i18n/ exists)
      screenshots/<image files>                   (manifest ``screenshots`` list)

    Then, unless ``skip_publish`` is set, marks the namespace as published so
    realm canisters can install it via ``install_extension_from_registry``.
    """
    source_dir = os.path.abspath(source_dir)
    if not os.path.isdir(source_dir):
        console.print(f"[red]Error: source dir not found: {source_dir}[/red]")
        raise typer.Exit(1)

    manifest_path = os.path.join(source_dir, "manifest.json")
    if not os.path.exists(manifest_path):
        console.print(f"[red]Error: manifest.json not found in {source_dir}[/red]")
        raise typer.Exit(1)
    with open(manifest_path, "r") as fh:
        manifest = json.load(fh)

    # Registry key: machine id first ("id", lowercase — matches wizard and
    # installer requests), then legacy "name" (older extension manifests use
    # it as the id), then the directory name. Never a capitalized display
    # name — that registers the package under a different namespace (#244).
    ext_id = (
        extension_id
        or manifest.get("id")
        or manifest.get("name")
        or os.path.basename(source_dir)
    )
    ver = version or manifest.get("version") or "0.0.0"
    namespace = f"{namespace_prefix}/{ext_id}/{ver}"

    console.print(
        f"[blue]Publishing extension '{ext_id}' v{ver} → {namespace} on "
        f"{registry} ({network})…[/blue]"
    )

    existing = _fetch_namespace_hashes(registry, namespace, network, identity)
    failed = 0
    uploaded = 0

    def _upload(reg_path, local):
        nonlocal failed, uploaded
        result = _upload_one_file(
            registry, namespace, reg_path, local, network, identity,
            existing_hashes=existing,
        )
        if result == "failed":
            failed += 1
        elif result == "uploaded":
            uploaded += 1

    # Manifest
    _upload("manifest.json", manifest_path)

    # Backend Python files (flattened — backend/foo.py → backend/foo.py).
    # JSON data files ship too (codex packages reference them via
    # manifest.data_files — departments.json, zones.json, …; issue #244).
    backend_dir = os.path.join(source_dir, "backend")
    if os.path.isdir(backend_dir):
        for root, _dirs, files in os.walk(backend_dir):
            for fname in sorted(files):
                if not fname.endswith((".py", ".json")):
                    continue
                local = os.path.join(root, fname)
                rel = os.path.relpath(local, backend_dir).replace(os.sep, "/")
                _upload(f"backend/{rel}", local)

    if bundle_path and not os.path.isfile(bundle_path):
        console.print(
            f"  [yellow]![/yellow] bundle not found, skipping: {bundle_path}"
        )
        bundle_path = None
    for reg_path, local in _frontend_shell_uploads(
        os.path.join(source_dir, "frontend", "dist"),
        os.path.join(source_dir, "frontend-rt", "dist"),
        bundle_path,
    ):
        _upload(reg_path, local)

    # Frontend i18n must land at ext/<id>/<ver>/frontend/i18n/<locale>.json
    # (realm_frontend fetch URL). Monorepo layout stores files under
    # frontend/i18n/locales/extensions/<ext_id>/ — upload those with paths
    # relative to that folder only. If only a shared locales/ tree exists,
    # rewrite extensions/<this_ext_id>/... → ... and skip other extensions.
    i18n_ext_dir = os.path.join(
        source_dir, "frontend", "i18n", "locales", "extensions", ext_id
    )
    locales_root = os.path.join(source_dir, "frontend", "i18n", "locales")
    i18n_legacy_root = os.path.join(source_dir, "i18n")

    def _publish_i18n_files(i18n_root: str, registry_rel_from_locale_rel) -> None:
        for walk_root, _dirs, files in os.walk(i18n_root):
            for fname in sorted(files):
                if not fname.endswith(".json"):
                    continue
                local = os.path.join(walk_root, fname)
                rel = os.path.relpath(local, i18n_root).replace(os.sep, "/")
                registry_rel = registry_rel_from_locale_rel(rel)
                if registry_rel is None:
                    continue
                _upload(f"frontend/i18n/{registry_rel}", local)

    if os.path.isdir(i18n_ext_dir):
        _publish_i18n_files(i18n_ext_dir, lambda r: r)
    elif os.path.isdir(locales_root):
        ext_prefix = f"extensions/{ext_id}/"

        def _map_shared_locales(rel: str):
            if rel.startswith(ext_prefix):
                return rel[len(ext_prefix) :]
            if rel.startswith("extensions/"):
                return None
            return rel

        _publish_i18n_files(locales_root, _map_shared_locales)
    elif os.path.isdir(i18n_legacy_root):
        _publish_i18n_files(i18n_legacy_root, lambda r: r)

    # Marketplace card + detail gallery — paths come from manifest.screenshots so
    # a typo or missing capture fails publish instead of a broken listing.
    screenshot_specs, screenshot_warnings = _iter_manifest_screenshot_uploads(
        source_dir, manifest
    )
    for warning in screenshot_warnings:
        console.print(warning)
        failed += 1
    for reg_path, local in screenshot_specs:
        _upload(reg_path, local)

    if uploaded == 0 and failed == 0:
        console.print(
            f"[green]✓ {ext_id}@{ver} already up-to-date on {registry}[/green]"
        )
        return

    if not skip_publish and uploaded > 0:
        if not _publish_namespace(registry, namespace, network, identity):
            failed += 1

    if failed:
        console.print(f"[red]Publish completed with {failed} failures[/red]")
        raise typer.Exit(1)

    console.print(
        f"[green]✓ Published {ext_id}@{ver} to {registry}:{namespace}[/green]"
    )


def publish_codex_command(
    registry: str,
    source_dir: str,
    codex_id: Optional[str] = None,
    version: Optional[str] = None,
    namespace_prefix: str = "codex",
    skip_publish: bool = False,
    network: str = "ic",
    identity: Optional[str] = None,
):
    """Publish a codex package to a file_registry canister.

    Unified pipeline (issue #244): a codex whose manifest declares
    ``"kind": "codex"`` and ships a ``backend/`` directory is an extension
    package — it is published through ``publish_extension_command`` under
    ``ext/<id>/<version>``, exactly like any other extension.

    Legacy layout (loose ``*.py`` at the package root, no ``kind``) still
    publishes to the deprecated ``codex/<id>/<version>`` namespace; this
    path is kept for one release and will be removed.
    """
    source_dir = os.path.abspath(source_dir)
    if not os.path.isdir(source_dir):
        console.print(f"[red]Error: source dir not found: {source_dir}[/red]")
        raise typer.Exit(1)

    manifest_path = os.path.join(source_dir, "manifest.json")
    if os.path.exists(manifest_path):
        with open(manifest_path, "r") as fh:
            manifest = json.load(fh)
    else:
        manifest = {}

    if manifest.get("kind") == "codex" and os.path.isdir(
        os.path.join(source_dir, "backend")
    ):
        # Setup wizard / install_codex_from_registry resolve ``ext/<id>`` first.
        # Default CLI prefix is ``codex`` (legacy); unified packages must land
        # under ``ext/`` or ``setup_install_codex`` reports no versions.
        prefix = "ext" if namespace_prefix == "codex" else namespace_prefix
        return publish_extension_command(
            registry=registry,
            source_dir=source_dir,
            extension_id=codex_id or manifest.get("id"),
            version=version,
            namespace_prefix=prefix,
            skip_publish=skip_publish,
            network=network,
            identity=identity,
        )

    console.print(
        "[yellow]⚠ legacy codex layout (no kind: codex / backend/): publishing to "
        "the deprecated codex/ namespace[/yellow]"
    )

    # The codex id must be the machine id (lowercase, matches the wizard's
    # package id and the directory name) — never the display "name", which is
    # capitalized and would register the package under a different namespace.
    cid = (
        codex_id
        or manifest.get("id")
        or os.path.basename(source_dir)
    )
    ver = version or manifest.get("version") or "0.0.0"
    namespace = f"{namespace_prefix}/{cid}/{ver}"

    console.print(
        f"[blue]Publishing codex '{cid}' v{ver} → {namespace} on "
        f"{registry} ({network})…[/blue]"
    )

    existing = _fetch_namespace_hashes(registry, namespace, network, identity)
    failed = 0
    uploaded = 0

    def _track(result):
        nonlocal failed, uploaded
        if result == "failed":
            failed += 1
        elif result == "uploaded":
            uploaded += 1

    if os.path.exists(manifest_path):
        _track(_upload_one_file(
            registry, namespace, "manifest.json", manifest_path, network, identity,
            existing_hashes=existing,
        ))
    else:
        synthetic = json.dumps(
            {"name": cid, "version": ver, "description": f"Codex package {cid}"}
        )
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as tmp:
            tmp.write(synthetic)
            tmp_path = tmp.name
        try:
            _track(_upload_one_file(
                registry, namespace, "manifest.json", tmp_path, network, identity,
                existing_hashes=existing,
            ))
        finally:
            os.unlink(tmp_path)

    for root, _dirs, files in os.walk(source_dir):
        for fname in sorted(files):
            # Include JSON data files (departments.json, zones.json, …) referenced
            # by manifest.data_files — init.py reads them at install time.
            if not (fname.endswith(".py") or fname.endswith(".json")):
                continue
            local = os.path.join(root, fname)
            if fname == "manifest.json" and os.path.dirname(local) == source_dir:
                continue
            rel = os.path.relpath(local, source_dir).replace(os.sep, "/")
            _track(_upload_one_file(
                registry, namespace, rel, local, network, identity,
                existing_hashes=existing,
            ))

    if uploaded == 0 and failed == 0:
        console.print(
            f"[green]✓ {cid}@{ver} already up-to-date on {registry}[/green]"
        )
        return

    if not skip_publish and uploaded > 0:
        if not _publish_namespace(registry, namespace, network, identity):
            failed += 1

    if failed:
        console.print(f"[red]Publish completed with {failed} failures[/red]")
        raise typer.Exit(1)

    console.print(
        f"[green]✓ Published codex {cid}@{ver} to {registry}:{namespace}[/green]"
    )


def publish_assistant_command(
    registry: str,
    source_dir: str,
    assistant_id: Optional[str] = None,
    version: Optional[str] = None,
    namespace_prefix: str = "assistant",
    skip_publish: bool = False,
    network: str = "ic",
    identity: Optional[str] = None,
):
    """Publish an assistant package (manifest, prompts/, persona.yaml, etc.) to file_registry."""
    source_dir = os.path.abspath(source_dir)
    if not os.path.isdir(source_dir):
        console.print(f"[red]Error: source dir not found: {source_dir}[/red]")
        raise typer.Exit(1)

    manifest_path = os.path.join(source_dir, "manifest.json")
    if not os.path.exists(manifest_path):
        console.print(f"[red]Error: manifest.json not found in {source_dir}[/red]")
        raise typer.Exit(1)
    with open(manifest_path, "r") as fh:
        manifest = json.load(fh)

    aid = assistant_id or manifest.get("assistant_id") or os.path.basename(source_dir)
    ver = version or manifest.get("version") or "0.0.0"
    namespace = f"{namespace_prefix}/{aid}/{ver}"

    console.print(
        f"[blue]Publishing assistant '{aid}' v{ver} → {namespace} on "
        f"{registry} ({network})…[/blue]"
    )

    existing = _fetch_namespace_hashes(registry, namespace, network, identity)
    failed = 0
    uploaded = 0

    def _track(result):
        nonlocal failed, uploaded
        if result == "failed":
            failed += 1
        elif result == "uploaded":
            uploaded += 1

    skip_names = {"manifest.json"}
    for root, _dirs, files in os.walk(source_dir):
        for fname in sorted(files):
            if fname.startswith("."):
                continue
            local = os.path.join(root, fname)
            rel = os.path.relpath(local, source_dir).replace(os.sep, "/")
            if rel in skip_names:
                continue
            _track(_upload_one_file(
                registry, namespace, rel, local, network, identity,
                existing_hashes=existing,
            ))

    _track(_upload_one_file(
        registry, namespace, "manifest.json", manifest_path, network, identity,
        existing_hashes=existing,
    ))

    if uploaded == 0 and failed == 0:
        console.print(f"[green]✓ {aid}@{ver} already up-to-date on {registry}[/green]")
        return

    if not skip_publish and uploaded > 0:
        if not _publish_namespace(registry, namespace, network, identity):
            failed += 1

    if failed:
        console.print(f"[red]Publish completed with {failed} failures[/red]")
        raise typer.Exit(1)

    console.print(
        f"[green]✓ Published assistant {aid}@{ver} to {registry}:{namespace}[/green]"
    )


def codex_registry_install_command(canister: str, registry: str, codex_id: str, version: Optional[str] = None, run_init: bool = True, network: str = "local", identity: Optional[str] = None):
    """Install a codex package from the file registry via the realm canister."""
    console.print(f"[blue]Installing codex '{codex_id}' from registry {registry} on {canister} ({network})...[/blue]")
    if version:
        console.print(f"  Version: {version}")
    else:
        console.print("  Version: latest")

    payload = json.dumps({
        "registry_canister_id": registry,
        "codex_id": codex_id,
        "version": version,
        "run_init": run_init,
    })
    candid_arg = '("' + payload.replace("\\", "\\\\").replace('"', '\\"') + '")'

    raw = _dfx_call(canister, "install_codex_from_registry", candid_arg, network, identity, timeout=300)

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        console.print(f"  Response: {raw}")
        return

    if result.get("success"):
        console.print(f"[green]  ✓ Codex '{codex_id}' installed (version {result.get('version', '?')})[/green]")
        if result.get("init_warning"):
            console.print(f"[yellow]  ⚠ init.py warning: {result['init_warning']}[/yellow]")
    else:
        console.print(f"[red]  ✗ Installation failed: {result.get('error', 'unknown error')}[/red]")
        raise typer.Exit(1)


def extension_command(
    action: str = typer.Argument(
        ...,
        help="Action to perform: list, install-from-source, package, install, uninstall, generate-manifests, runtime-install, runtime-uninstall, runtime-list, registry-install, resync-frontends, publish",
    ),
    extension_id: Optional[str] = typer.Option(
        None, "--extension-id", help="Extension ID for package/uninstall operations"
    ),
    package_path: Optional[str] = typer.Option(
        None, "--package-path", help="Path for package file (full output path for package action, or package file to install for install action)"
    ),
    source_dir: str = typer.Option(
        "extensions", "--source-dir", help="Source directory for extensions"
    ),
    all_extensions: bool = typer.Option(
        False, "--all", help="Uninstall all extensions (only for uninstall action)"
    ),
    canister: Optional[str] = typer.Option(
        None, "--canister", "-c", help="Target realm canister ID (for runtime-*/registry-* actions)"
    ),
    network: str = typer.Option(
        "local", "--network", "-n", help="Network: local, ic (for runtime-*/registry-* actions)"
    ),
    identity: Optional[str] = typer.Option(
        None, "--identity", help="dfx identity to use (for runtime-*/registry-* actions)"
    ),
    raw_json: bool = typer.Option(
        False, "--json", help="Output raw JSON (for runtime-list)"
    ),
    registry: Optional[str] = typer.Option(
        None, "--registry", "-r", help="File registry canister ID (for registry-install or publish)"
    ),
    version: Optional[str] = typer.Option(
        None, "--version", "-v", help="Version to install/publish (default: registry-install→latest, publish→manifest.json)"
    ),
    bundle_path: Optional[str] = typer.Option(
        None,
        "--bundle-path",
        help="Path to a pre-built ESM frontend bundle (overrides <source>/frontend-rt/dist/index.js for publish)",
    ),
    namespace_prefix: str = typer.Option(
        "ext",
        "--namespace-prefix",
        help="Registry namespace prefix for publish (default: ext)",
    ),
    skip_publish_marker: bool = typer.Option(
        False,
        "--skip-publish",
        help="Upload files but do not call publish_namespace (publish action only)",
    ),
    frontend_canister: Optional[str] = typer.Option(
        None,
        "--frontend-canister",
        help="Realm frontend asset canister (runtime-install / resync-frontends)",
    ),
) -> None:
    """Manage Realm extensions."""

    if action == "list":
        list_extensions_command()
    elif action == "install-from-source":
        install_from_source_command(source_dir)
    elif action == "generate-manifests":
        console.print(
            "[yellow]generate-manifests is deprecated. "
            "Extensions are now loaded dynamically via the file registry at runtime.[/yellow]"
        )
    elif action == "package":
        if not extension_id:
            console.print(
                "[red]Error: --extension-id is required for package action[/red]"
            )
            raise typer.Exit(1)
        package_extension_command(extension_id, output_path=package_path, source_dir=source_dir)
    elif action == "install":
        if not package_path:
            console.print(
                "[red]Error: --package-path is required for install action[/red]"
            )
            raise typer.Exit(1)
        install_extension_command(package_path)
    elif action == "uninstall":
        if all_extensions:
            uninstall_all_extensions_command()
        elif extension_id:
            uninstall_extension_command(extension_id)
        else:
            console.print(
                "[red]Error: Either --extension-id or --all is required for uninstall action[/red]"
            )
            raise typer.Exit(1)
    elif action == "runtime-install":
        if not canister:
            console.print("[red]Error: --canister is required for runtime-install[/red]")
            raise typer.Exit(1)
        runtime_install_command(canister, source_dir, network, identity, frontend_canister)
    elif action == "runtime-uninstall":
        if not canister:
            console.print("[red]Error: --canister is required for runtime-uninstall[/red]")
            raise typer.Exit(1)
        if not extension_id:
            console.print("[red]Error: --extension-id is required for runtime-uninstall[/red]")
            raise typer.Exit(1)
        runtime_uninstall_command(canister, extension_id, network, identity)
    elif action == "runtime-list":
        if not canister:
            console.print("[red]Error: --canister is required for runtime-list[/red]")
            raise typer.Exit(1)
        runtime_list_command(canister, network, identity, raw_json)
    elif action == "registry-install":
        if not canister:
            console.print("[red]Error: --canister is required for registry-install[/red]")
            raise typer.Exit(1)
        if not registry:
            console.print("[red]Error: --registry is required for registry-install[/red]")
            raise typer.Exit(1)
        if not extension_id:
            console.print("[red]Error: --extension-id is required for registry-install[/red]")
            raise typer.Exit(1)
        registry_install_command(canister, registry, extension_id, version, network, identity)
    elif action == "resync-frontends":
        if not canister:
            console.print("[red]Error: --canister is required for resync-frontends[/red]")
            raise typer.Exit(1)
        resync_frontends_command(canister, registry, frontend_canister, network, identity)
    elif action == "publish":
        if not registry:
            console.print("[red]Error: --registry is required for publish[/red]")
            raise typer.Exit(1)
        # For publish, --source-dir points to a single extension dir (containing manifest.json),
        # not the parent directory. We accept either the typer default ('extensions') or an
        # explicit path. Prefer the explicit path if it has a manifest.json.
        candidate = source_dir
        if extension_id and os.path.isdir(os.path.join(source_dir, extension_id)):
            candidate = os.path.join(source_dir, extension_id)
        publish_extension_command(
            registry=registry,
            source_dir=candidate,
            extension_id=extension_id,
            version=version,
            bundle_path=bundle_path,
            namespace_prefix=namespace_prefix,
            skip_publish=skip_publish_marker,
            network=network,
            identity=identity,
        )
    else:
        console.print(f"[red]Unknown action: {action}[/red]")
        console.print(
            "[yellow]Available actions: list, install-from-source, generate-manifests, package, install, uninstall, runtime-install, runtime-uninstall, runtime-list, registry-install, resync-frontends, publish[/yellow]"
        )
        raise typer.Exit(1)


def codex_command(
    action: str = typer.Argument(
        ...,
        help="Action to perform: runtime-install, runtime-uninstall, runtime-list, registry-install, publish",
    ),
    codex_id: Optional[str] = typer.Option(
        None, "--codex-id", help="Codex package ID"
    ),
    source_dir: str = typer.Option(
        ".", "--source-dir", help="Source directory for codex package"
    ),
    canister: Optional[str] = typer.Option(
        None, "--canister", "-c", help="Target realm canister ID"
    ),
    network: str = typer.Option(
        "local", "--network", "-n", help="Network: local, ic"
    ),
    identity: Optional[str] = typer.Option(
        None, "--identity", help="dfx identity to use"
    ),
    raw_json: bool = typer.Option(
        False, "--json", help="Output raw JSON (for runtime-list)"
    ),
    registry: Optional[str] = typer.Option(
        None, "--registry", "-r", help="File registry canister ID (for registry-install or publish)"
    ),
    version: Optional[str] = typer.Option(
        None, "--version", "-v", help="Version to install/publish (default: registry-install→latest, publish→manifest.json)"
    ),
    run_init: bool = typer.Option(
        True, "--run-init/--no-init", help="Run init.py after install (default: true)"
    ),
    namespace_prefix: str = typer.Option(
        "codex",
        "--namespace-prefix",
        help="Registry namespace prefix for publish (default: codex)",
    ),
    skip_publish_marker: bool = typer.Option(
        False,
        "--skip-publish",
        help="Upload files but do not call publish_namespace (publish action only)",
    ),
) -> None:
    """Manage Realm codex packages."""

    if action == "runtime-install":
        if not canister:
            console.print("[red]Error: --canister is required for runtime-install[/red]")
            raise typer.Exit(1)
        codex_runtime_install_command(canister, source_dir, codex_id, run_init, network, identity)
    elif action == "runtime-uninstall":
        if not canister:
            console.print("[red]Error: --canister is required for runtime-uninstall[/red]")
            raise typer.Exit(1)
        if not codex_id:
            console.print("[red]Error: --codex-id is required for runtime-uninstall[/red]")
            raise typer.Exit(1)
        codex_runtime_uninstall_command(canister, codex_id, network, identity)
    elif action == "runtime-list":
        if not canister:
            console.print("[red]Error: --canister is required for runtime-list[/red]")
            raise typer.Exit(1)
        codex_runtime_list_command(canister, network, identity, raw_json)
    elif action == "registry-install":
        if not canister:
            console.print("[red]Error: --canister is required for registry-install[/red]")
            raise typer.Exit(1)
        if not registry:
            console.print("[red]Error: --registry is required for registry-install[/red]")
            raise typer.Exit(1)
        if not codex_id:
            console.print("[red]Error: --codex-id is required for registry-install[/red]")
            raise typer.Exit(1)
        codex_registry_install_command(canister, registry, codex_id, version, run_init, network, identity)
    elif action == "publish":
        if not registry:
            console.print("[red]Error: --registry is required for publish[/red]")
            raise typer.Exit(1)
        candidate = source_dir
        if codex_id and os.path.isdir(os.path.join(source_dir, codex_id)):
            candidate = os.path.join(source_dir, codex_id)
        publish_codex_command(
            registry=registry,
            source_dir=candidate,
            codex_id=codex_id,
            version=version,
            namespace_prefix=namespace_prefix,
            skip_publish=skip_publish_marker,
            network=network,
            identity=identity,
        )
    else:
        console.print(f"[red]Unknown action: {action}[/red]")
        console.print(
            "[yellow]Available actions: runtime-install, runtime-uninstall, runtime-list, registry-install, publish[/yellow]"
        )
        raise typer.Exit(1)
