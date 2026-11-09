"""``realms legislation``: the tools of the layered codex (docs/codices/layered-codex.md §12).

``check`` first holds the tree to the layout of §3 (``legislation_layout``, the one place those rules live) and
stops there if it breaks any. It then loads the tree the way the realm does, with the same ``realms.legislation``
library, and prints every refusal as ``<source>/<domain>.py:<line>: <reason>``. Each chapter first
passes the readable subset on its syntax tree (``subset_ast``, the clear errors for authors), then
loads, which runs the realm's own bytecode check and the marks. ``from ggg import ...`` resolves to
inert placeholders named after GGG's entities: the check loads chapters, it never runs their hooks.
This is the S1 stub: the full checker (the ``F`` rules, ``uses`` cycles, cases, Python versus Cedar)
comes with S2 and C2.

``resolve`` and ``propose`` (§12.4, §12.5) have their logic in ``legislation_resolve`` and ``legislation_propose``;
the commands below only read the options and print.

The layers of a source are its ancestor folders, top to bottom: ``eu/es/vc/46250-valencia`` runs
``eu``, ``eu/es``, ``eu/es/vc`` and itself. The links between realms (§3.3, the resolver of S3) are not
read here, so a body beside the line, such as EMSHI, is checked as a realm of its own. A realm loads only its
own ``realm.py``: the one a source above keeps beside its releases is that source's, as ``plan`` skips a relayed one.
"""

from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console

from realms.cli.commands.legislation_analysis import Analysis, MissingTools
from realms.cli.commands.legislation_cases import case_problems
from realms.cli.commands.legislation_layout import commit_of, layout_problems
from realms.cli.commands.legislation_load import WorkingFiles, load_realm
from realms.cli.commands.legislation_propose import (
    change_list,
    chapter_entry,
    code_to_submit,
    https,
    parse_summary,
    replaced_codex,
    report_entry,
)
from realms.cli.commands.legislation_resolve import (
    file_record,
    read_json,
    report_lines,
    resolve_realm,
    tree_record,
)
from realms.legislation import LegislationError
from realms.legislation.resolver import check_uses

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


def fact_schema() -> dict:
    """GGG's fact schema as the forbids read it: the canister's generated ``ggg_facts`` (scripts/ggg_candid.py)."""
    from realms.src.realm_backend import ggg_facts

    return ggg_facts.FACTS


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


def check_realm(
    files: WorkingFiles,
    realm: str,
    operations,
    facts: dict,
    analysis: Analysis,
) -> List[str]:
    """Load the realm's codex from the tree and check it; the refusals, as the checker prints them."""
    codex, problems = load_realm(files, realm, operations, facts, check_view())

    def reload(ggg):
        return load_realm(files, realm, operations, facts, ggg)[0]

    try:
        codex.check()
        if (
            not problems
        ):  # read from a codex that loaded whole: a chapter that did not load would be a false "no such domain"
            check_uses(codex)
            problems += analysis.problems(codex) or case_problems(
                files, codex, realm, reload
            )
    except LegislationError as e:
        problems.append(str(e))
    return problems


def refuse(problems: List[str]) -> None:
    """Print ``problems``, one a line, and stop with exit code 1; nothing to print, carry on."""
    for problem in problems:
        typer.echo(problem)
    if problems:
        console.print(f"[red]{len(problems)} refused[/red]")
        raise typer.Exit(1)


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
    no_analysis: bool = typer.Option(
        False,
        "--no-analysis",
        help="Do not run the Cedar analysis of the forbids (agreement with Python, never errors), which needs "
        "cedarpy, the Cedar CLI and cvc5 (scripts/cedar_tools.sh)",
    ),
) -> None:
    """Hold the tree to the layout of §3, then load every realm's chapters as the realm does and report what it refuses."""
    if not tree.is_dir():
        console.print(f"[red]{tree} is not a folder[/red]")
        raise typer.Exit(2)
    refuse(layout_problems(tree))
    files = WorkingFiles(tree)
    known = files.sources()
    realms = [realm] if realm else known
    if realm and realm not in known:
        console.print(f"[red]{realm} has no source.json under {tree}[/red]")
        raise typer.Exit(2)
    try:
        operations = ggg_operations()
    except ImportError as e:
        console.print(f"[red]the GGG operation list is unavailable: {e}[/red]")
        raise typer.Exit(2)
    try:
        facts = fact_schema()
    except ImportError as e:
        console.print(f"[red]GGG's fact schema is unavailable: {e}[/red]")
        raise typer.Exit(2)
    problems, analysis = [], Analysis(skip=no_analysis)
    try:
        for one in realms:
            problems += check_realm(files, one, operations, facts, analysis)
    except MissingTools as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(2)
    refuse(
        list(dict.fromkeys(problems))
    )  # a layer's refusal shows once, though every realm below it runs the layer
    console.print(f"[green]ok: {len(realms)} realm(s) checked[/green]")


@legislation_app.command("resolve")
def resolve_command(
    tree: Path = typer.Argument(
        Path("legislation"),
        help="The legislation/ folder, inside a git checkout of realms-codices",
    ),
    realm: str = typer.Option(
        ..., "--realm", help="The realm's own source id, eu/es/vc/46250-valencia"
    ),
    commit: str = typer.Option(
        "HEAD",
        "--commit",
        help="The commit the realm's own chapters, and the tree's releases.json, are read at",
    ),
    sync_sources: List[str] = typer.Option(
        [],
        "--sync-source",
        help="A sync source whose record is the tree's releases.json: the newest release of it and of each source above it",
    ),
    records: List[Path] = typer.Option(
        [],
        "--record",
        exists=True,
        dir_okay=False,
        help="A sync source's record, a file in the shape of §8: {source, layers}",
    ),
    own: Optional[Path] = typer.Option(
        None,
        "--own",
        exists=True,
        dir_okay=False,
        help="The realm's own layer, {chapters: {domain: sha256}, calendar: sha256}; its folder at --commit by default",
    ),
    facts: Optional[Path] = typer.Option(
        None,
        "--facts",
        exists=True,
        dir_okay=False,
        help="The fact schema as JSON in the shape the Cedar generator reads (cedar.FactSchema); without it no Cedar is written",
    ),
) -> None:
    """Print a realm's codex id, layer paths, flattened view (§5.9) and generated Cedar (§5.8), as the realm works them out.

    Reads the tree with git and calls no network.
    """
    try:
        operations = ggg_operations()
        at = commit_of(tree, commit)
        relays = [tree_record(tree, at, s) for s in sync_sources]
        relays += [file_record(path) for path in records]
        resolution = resolve_realm(
            tree,
            realm,
            at,
            relays,
            read_json(own) if own else None,
            operations,
            {"ggg": check_view()},
            read_json(facts) if facts else None,
        )
        lines = report_lines(resolution, facts is not None)
    except ImportError as e:
        console.print(f"[red]the GGG operation list is unavailable: {e}[/red]")
        raise typer.Exit(2)
    except LegislationError as e:
        refuse([str(e)])
    for line in lines:
        typer.echo(line)


@legislation_app.command("propose")
def propose_command(
    tree: Path = typer.Argument(
        Path("legislation"),
        help="The legislation/ folder, inside a git checkout of realms-codices",
    ),
    realm: str = typer.Option(
        ..., "--realm", help="The realm's own source id, eu/es/vc/46250-valencia"
    ),
    commit: str = typer.Option(
        ...,
        "--commit",
        help="The commit that holds the chapters: their URLs name it, so it must be one the realm can fetch",
    ),
    set_chapters: List[str] = typer.Option(
        [],
        "--set",
        help="A chapter of the realm's own layer to set, <realm>/<domain> or <realm>/calendar; repeat for more",
    ),
    remove: List[str] = typer.Option(
        [],
        "--remove",
        help="A chapter of the realm's own layer to empty; repeat for more",
    ),
    replaces: Optional[str] = typer.Option(
        None,
        "--replaces",
        help="The id of the realm's active codex, or none when it has none; or read it with --network, --canister and --identity",
    ),
    network: Optional[str] = typer.Option(
        None, "--network", help="The network to read the active codex from (no default)"
    ),
    canister: Optional[str] = typer.Option(
        None,
        "--canister",
        help="The realm's canister id, to read the active codex from",
    ),
    identity: Optional[str] = typer.Option(
        None,
        "--identity",
        help="The identity that reads the active codex: required with --network and --canister, never a default",
    ),
    summary: List[str] = typer.Option(
        [], "--summary", help="LANG=TEXT shown in the proposal; repeat per language"
    ),
    report: Optional[Path] = typer.Option(
        None,
        "--report",
        exists=True,
        dir_okay=False,
        help="The analysis report to attach (§12.3), with --report-url",
    ),
    report_url: Optional[str] = typer.Option(
        None, "--report-url", help="Where the report is served (https)"
    ),
    url: str = typer.Option(
        ...,
        "--url",
        help="Where the change list will be served (https): the proposal pins this address and the file's checksum",
    ),
    out: Path = typer.Option(..., "--out", help="Where to write the change list"),
    repo: str = typer.Option(
        "smart-social-contracts/realms-codices",
        "--repo",
        help="The GitHub repository the chapters are served from",
    ),
) -> None:
    """Write the change list of a proposal (§7) as canonical JSON and print the Code { url; checksum } to submit.

    Reads the chapters with git. Reads the realm only when --network, --canister and --identity are all given,
    and says which before it does.
    """
    try:
        https(url)
        if bool(report) != bool(report_url):
            raise LegislationError("--report and --report-url go together")
        at = commit_of(tree, commit)
        data = change_list(
            realm,
            replaced_codex(replaces, network, canister, identity),
            [chapter_entry(tree, at, repo, realm, c) for c in set_chapters],
            remove,
            report_entry(report, report_url) if report else None,
            parse_summary(summary),
        )
    except LegislationError as e:
        refuse([str(e)])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    typer.echo(f"wrote {out} ({len(data)} bytes)")
    typer.echo(code_to_submit(url, data))
