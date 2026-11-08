"""``realms legislation``: the tools of the layered codex (docs/codices/layered-codex.md §12).

``check`` loads a ``legislation/`` tree the way the realm does, with the same ``realms.legislation``
library, and prints every refusal as ``<source>/<domain>.py:<line>: <reason>``. Each chapter first
passes the readable subset on its syntax tree (``subset_ast``, the clear errors for authors), then
loads, which runs the realm's own bytecode check and the marks. ``from ggg import ...`` resolves to
inert placeholders named after GGG's entities: the check loads chapters, it never runs their hooks.
This is the S1 stub: the full checker (the ``F`` rules, ``uses`` cycles, cases, Python versus Cedar)
comes with S2 and C2.

The layers of a source are its ancestor folders, top to bottom: ``eu/es/vc/46250-valencia`` runs
``eu``, ``eu/es``, ``eu/es/vc`` and itself. The links between realms (§3.3, the resolver of S3) are not
read here, so a body beside the line, such as EMSHI, is checked as a realm of its own.
"""

import json
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console

from realms.legislation import Codex, Refused, subset_ast

console = Console()
legislation_app = typer.Typer(
    name="legislation", help="Tools of the layered codex: check a legislation/ tree"
)


@legislation_app.callback()
def legislation_group() -> None:
    """Tools of the layered codex (docs/codices/layered-codex.md §12)."""


# Operations the layered codex design names that ggg.did does not carry yet: ggg#2 (procedure_define) and ggg#3
# (the links). Remove each as G2 and G3 land in ggg_did.
INTERIM_OPERATIONS = frozenset(
    {
        "procedure_define",
        "sync_source_add",
        "sync_source_remove",
        "follower_accept",
        "follower_remove",
        "follower_report",
        "follower_list",
        "link_notify",
        "legislation_releases",
        "legislation_chapter",
    }
)


def ggg_operations() -> frozenset:
    """The GGG operation names: the canister's generated ggg_did.METHODS plus the interim list."""
    from realms.src.realm_backend import ggg_did

    return frozenset(ggg_did.METHODS) | INTERIM_OPERATIONS


def check_view():
    """``ggg`` as the check sees it: a placeholder class per entity the canister's ``ggg`` package exports.
    The names come from ``realms/ggg/__init__.py`` (the canister tree, reached through the package).
    """
    import ast
    import types

    import realms

    source = Path(realms.__file__).resolve().parent / "ggg" / "__init__.py"
    view = types.ModuleType("ggg")
    for node in ast.parse(source.read_text()).body:
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                setattr(
                    view,
                    alias.name,
                    type(
                        alias.name,
                        (),
                        {"__doc__": "a check-time placeholder: no reads here"},
                    ),
                )
    return view


def sources(tree: Path) -> List[str]:
    """Every source id under ``tree``: a folder with a source.json, as ``eu/es/vc``."""
    return sorted(
        p.parent.relative_to(tree).as_posix() for p in tree.rglob("source.json")
    )


def layers_of(source: str, known: List[str]) -> List[str]:
    """The source's ancestor sources and itself, top to bottom."""
    parts = source.split("/")
    path = ["/".join(parts[: i + 1]) for i in range(len(parts))]
    return [s for s in path if s in known]


def chapters_of(tree: Path, source: str) -> List[str]:
    """The domains the source has a chapter for: its ``<domain>.py`` files."""
    return sorted(p.stem for p in (tree / source).glob("*.py"))


def check_realm(tree: Path, realm: str, known: List[str], operations) -> List[str]:
    """Load the realm's codex from the tree; the refusals, as the checker prints them."""
    layers = layers_of(realm, known)
    codex = Codex(
        layers, realm=realm, operations=operations, modules={"ggg": check_view()}
    )
    problems = []
    for source in layers:
        calendar = tree / source / "calendar.json"
        if calendar.exists():
            try:
                codex.calendar_layer(source, json.loads(calendar.read_text()))
            except Refused as e:
                problems.append(str(e))
            except ValueError as e:
                problems.append(f"{source}/calendar.json: {e}")
    domains = []
    for source in layers:
        for domain in chapters_of(tree, source):
            if domain not in domains:
                domains.append(domain)
    for domain in domains:
        for source in layers:
            file = tree / source / (domain + ".py")
            if not file.exists():
                continue
            text = file.read_text()
            try:
                subset_ast.check(text, f"{source}/{domain}.py")
                codex.load(source, domain, text)
            except Refused as e:
                problems.append(str(e))
                break  # the layers below build on this one
    try:
        codex.check()
    except Refused as e:
        problems.append(str(e))
    return problems


@legislation_app.command("check")
def check_command(
    tree: Path = typer.Argument(
        Path("legislation"),
        help="The legislation/ folder: <source>/<domain>.py chapters",
    ),
    realm: Optional[str] = typer.Option(
        None,
        "--realm",
        help="Check this realm's codex only (its source id, eu/es/vc/46250-valencia)",
    ),
) -> None:
    """Load every realm's chapters as the realm does and report what it refuses."""
    if not tree.is_dir():
        console.print(f"[red]{tree} is not a folder[/red]")
        raise typer.Exit(2)
    known = sources(tree)
    realms = [realm] if realm else known
    if realm and realm not in known:
        console.print(f"[red]{realm} has no source.json under {tree}[/red]")
        raise typer.Exit(2)
    try:
        operations = ggg_operations()
    except ImportError as e:
        console.print(f"[red]the GGG operation list is unavailable: {e}[/red]")
        raise typer.Exit(2)
    problems = []
    for one in realms:
        problems += check_realm(tree, one, known, operations)
    for problem in problems:
        typer.echo(problem)
    if problems:
        console.print(f"[red]{len(problems)} refused[/red]")
        raise typer.Exit(1)
    console.print(f"[green]ok: {len(realms)} realm(s) checked[/green]")
