"""``realms marketplace publish`` — list every first-party package on a marketplace.

For each extension in ``extensions/extensions/*`` and codex in
``codices/codices/*`` (the same set ``realms files publish`` uploads) this
creates or updates the marketplace listing pointing at the package's file
registry namespace, then — as a reviewer — approves it, which stamps the
approval on the registry and marks the listing ``verified``.

Authorization comes from the Casals sheet, not from being a controller: the
``admin_grant_publisher`` config row licenses the operator and makes it a
reviewer. Everything here is a plain ingress call signed by ``--identity``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import typer
from rich.console import Console
from rich.table import Table

from .extension import _dfx_call

console = Console()


# ── candid text encoding ─────────────────────────────────────────────────────


def candid_text(value: str) -> str:
    """A candid ``text`` literal: only backslash and double quote need escaping."""
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def candid_record(fields: dict) -> str:
    """``record { k = v; … }`` with str → text, int → nat64, bool → bool."""
    parts = []
    for key, val in fields.items():
        if isinstance(val, bool):
            lit = "true" if val else "false"
        elif isinstance(val, int):
            lit = f"{val} : nat64"
        else:
            lit = candid_text(val)
        parts.append(f"{key} = {lit}")
    return "(record { " + "; ".join(parts) + " })"


# ── package discovery ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Listing:
    kind: str  # extension | codex
    item_id: str
    fields: dict  # ExtensionInput / CodexInput minus the registry columns
    # Registry namespace prefix the package was published under. `realms files
    # publish` uploads every extension, and every unified codex (manifest
    # `kind: codex` with a backend/ dir — issue #244), to `ext/<id>/<version>`;
    # only a legacy loose-files codex still lands under the deprecated `codex/`.
    namespace_prefix: str = "ext"


def _display_name(manifest: dict, fallback: str) -> str:
    label = manifest.get("sidebar_label") or manifest.get("title") or manifest.get("display_name")
    if isinstance(label, dict):
        label = label.get("en") or next(iter(label.values()), "")
    if isinstance(label, str) and label.strip():
        return label.strip()
    return fallback.replace("_", " ").replace("-", " ").title()


def _text(value) -> str:
    if isinstance(value, dict):
        value = value.get("en") or next(iter(value.values()), "")
    return str(value or "").strip()


def _categories(manifest: dict) -> str:
    cats = manifest.get("categories") or []
    if isinstance(cats, str):
        return cats
    return ",".join(str(c) for c in cats)


def _screenshots(manifest: dict) -> str:
    """Package-relative paths, comma-joined as the listing stores them."""
    shots = manifest.get("screenshots") or []
    if not isinstance(shots, list):
        return ""
    return ",".join(s.strip() for s in shots if isinstance(s, str) and s.strip())


def extension_listing(manifest: dict, dir_name: str) -> Listing:
    ext_id = str(manifest.get("name") or dir_name).strip()
    version = str(manifest.get("version") or "").strip()
    if not version:
        raise ValueError(f"{dir_name}: manifest has no version")
    return Listing("extension", ext_id, {
        "extension_id": ext_id,
        "name": _display_name(manifest, ext_id),
        "description": _text(manifest.get("description")),
        "version": version,
        "price_e8s": 0,
        "icon": _text(manifest.get("icon")),
        "categories": _categories(manifest),
        "screenshots": _screenshots(manifest),
        "download_url": _text(manifest.get("doc_url") or manifest.get("repository")),
    })


def codex_listing(manifest: dict, dir_name: str, *, unified: bool = True) -> Listing:
    codex_id = str(manifest.get("id") or manifest.get("name") or dir_name).strip()
    version = str(manifest.get("version") or "").strip()
    if not version:
        raise ValueError(f"{dir_name}: manifest has no version")
    return Listing("codex", codex_id, {
        "codex_id": codex_id,
        "realm_type": _text(manifest.get("realm_type")),
        "name": _display_name(manifest, codex_id),
        "description": _text(manifest.get("description")),
        "version": version,
        "price_e8s": 0,
        "icon": _text(manifest.get("icon")),
        "categories": _categories(manifest),
    }, namespace_prefix="ext" if unified else "codex")


def _is_unified_codex(manifest: dict, source_dir: Path) -> bool:
    """Same test as ``publish_codex_command``: kind codex + a backend/ dir."""
    return manifest.get("kind") == "codex" and (source_dir / "backend").is_dir()


def discover(root: Path, *, extensions: Optional[set[str]] = None, codices: Optional[set[str]] = None,
             extensions_only: bool = False, codices_only: bool = False) -> list[Listing]:
    """Same walk as ``realms files publish``: manifest-bearing dirs, ``_``-prefixed skipped."""
    out: list[Listing] = []
    if not extensions_only:
        codex_root = root / "codices" / "codices"
        if codex_root.is_dir():
            for d in sorted(codex_root.iterdir()):
                if not d.is_dir() or d.name.startswith("_") or not (d / "manifest.json").exists():
                    continue
                if codices is not None and d.name not in codices:
                    continue
                manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
                out.append(codex_listing(manifest, d.name, unified=_is_unified_codex(manifest, d)))
    if not codices_only:
        ext_root = root / "extensions" / "extensions"
        if ext_root.is_dir():
            for d in sorted(ext_root.iterdir()):
                if not d.is_dir() or d.name.startswith("_") or not (d / "manifest.json").exists():
                    continue
                if extensions is not None and d.name not in extensions:
                    continue
                out.append(extension_listing(json.loads((d / "manifest.json").read_text(encoding="utf-8")), d.name))
    return out


def item_kind_for(listing: Listing) -> str:
    """The marketplace's ``item_kind`` vocabulary (``review_listing``,
    approval APIs): ``ext`` / ``codex`` — the listing's kind, not the
    registry namespace prefix (a unified codex is listed as a codex but
    stored under ``ext/``)."""
    return "ext" if listing.kind == "extension" else "codex"


def namespace_for(listing: Listing) -> str:
    """The registry namespace ``realms files publish`` uploaded the package to."""
    return f"{listing.namespace_prefix}/{listing.item_id}/{listing.fields['version']}"


def listing_input(listing: Listing, registry: str) -> str:
    fields = dict(listing.fields)
    fields["file_registry_canister_id"] = registry
    fields["file_registry_namespace"] = namespace_for(listing)
    return candid_record(fields)


# ── result parsing ───────────────────────────────────────────────────────────


# Candid field-id hashes of the Result variant tags: icp prints them instead of
# the names when it cannot fetch the canister's candid interface.
_OK_TAGS = ("Ok", "17_724", "17724")
_ERR_TAGS = ("Err", "3_456_837", "3456837")


def parse_generic_result(raw: str) -> tuple[bool, str]:
    """``(variant { Ok = "…" })`` / ``(variant { Err = "…" })`` → (ok, message)."""
    text = (raw or "").strip()
    head = text.split("=")[0] if "=" in text else text
    ok = any(tag in head for tag in _OK_TAGS) and not any(tag in head for tag in _ERR_TAGS)
    msg = text
    if "=" in text:
        msg = text.split("=", 1)[1].strip().rstrip("})").strip().strip('"')
    return ok, msg


def parse_review_result(raw: str) -> tuple[bool, str]:
    """``review_listing`` returns JSON text."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return False, str(raw)[:200]
    if isinstance(data, dict):
        return bool(data.get("success")), str(data.get("error") or data.get("verification_status") or "")
    return False, str(raw)[:200]


# ── the command ──────────────────────────────────────────────────────────────


def publish_listings(
    listings: Iterable[Listing],
    *,
    marketplace: str,
    registry: str,
    network: str,
    identity: Optional[str],
    approve: bool,
    call=_dfx_call,
) -> tuple[int, int]:
    """Create/update and (optionally) approve each listing. Returns (ok, failed)."""
    ok = failed = 0
    table = Table(title=f"Marketplace {marketplace} ({network})")
    table.add_column("kind")
    table.add_column("id")
    table.add_column("version")
    table.add_column("listing")
    table.add_column("review")
    for lst in listings:
        method = "create_extension" if lst.kind == "extension" else "create_codex"
        raw = call(marketplace, method, listing_input(lst, registry), network, identity, raise_on_error=False)
        created, msg = parse_generic_result(str(raw))
        review = "-"
        if created and approve:
            args = f'({candid_text(item_kind_for(lst))}, {candid_text(lst.item_id)}, true, {candid_text("first-party package")})'
            raw2 = call(marketplace, "review_listing", args, network, identity, raise_on_error=False)
            approved, detail = parse_review_result(str(raw2))
            review = f"[green]{detail or 'verified'}[/green]" if approved else f"[red]{detail}[/red]"
            created = approved
        table.add_row(lst.kind, lst.item_id, lst.fields["version"],
                      f"[green]{msg}[/green]" if created or msg.startswith(("created", "updated")) else f"[red]{msg}[/red]",
                      review)
        if created:
            ok += 1
        else:
            failed += 1
    console.print(table)
    return ok, failed


def marketplace_publish_command(
    marketplace: str,
    registry: str,
    network: str,
    identity: Optional[str],
    extensions_only: bool,
    codices_only: bool,
    extensions_filter: str,
    codices_filter: str,
    approve: bool,
    project_root: Optional[Path] = None,
) -> None:
    from .files import _find_project_root

    root = project_root or _find_project_root()
    ext_names = {s.strip() for s in extensions_filter.split(",") if s.strip()} if extensions_filter else None
    cdx_names = {s.strip() for s in codices_filter.split(",") if s.strip()} if codices_filter else None
    try:
        listings = discover(root, extensions=ext_names, codices=cdx_names,
                            extensions_only=extensions_only, codices_only=codices_only)
    except (ValueError, json.JSONDecodeError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2)
    if not listings:
        console.print("[yellow]nothing to list[/yellow]")
        raise typer.Exit(code=1)
    console.print(f"{len(listings)} listing(s) → registry {registry}")
    ok, failed = publish_listings(listings, marketplace=marketplace, registry=registry, network=network,
                                  identity=identity, approve=approve)
    if failed:
        console.print(f"[red]{failed} listing(s) failed, {ok} ok[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]{ok} listing(s) live[/green]")
