"""``realms marketplace transfer`` — hand marketplace listings to another principal.

Without ids it moves every listing the signer owns (``get_my_*``). With
``--extensions`` / ``--codices`` / ``--assistants`` it moves just those, which is
also how a controller moves listings it does not own. The canister's
``transfer_listing`` enforces owner-or-controller and keeps each listing's
version and review state.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

import typer
from rich.console import Console
from rich.table import Table

from .extension import _dfx_call
from .marketplace_publish import candid_text, parse_generic_result

console = Console()

# item_kind → (get_my_* query, id field)
KINDS = {
    "ext": ("get_my_extensions", "extension_id"),
    "codex": ("get_my_codices", "codex_id"),
    "assistant": ("get_my_assistants", "assistant_id"),
}


def idl_hash(name: str) -> int:
    """Candid field id; icp prints it in place of the name when it cannot fetch
    the canister's interface."""
    h = 0
    for b in name.encode("utf-8"):
        h = (h * 223 + b) % (1 << 32)
    return h


def field_values(raw: str, field: str) -> list[str]:
    """Every text value of ``field`` in candid text output, by name or by id."""
    h = idl_hash(field)
    keys = "|".join(re.escape(k) for k in (field, str(h), f"{h:,}".replace(",", "_")))
    return re.findall(rf'(?<![\w])(?:{keys})\s*=\s*"((?:[^"\\]|\\.)*)"', raw or "")


def owned_ids(marketplace: str, network: str, identity: Optional[str], call=_dfx_call) -> list[tuple[str, str]]:
    out = []
    for kind, (query, field) in KINDS.items():
        raw = call(marketplace, query, "()", network, identity, is_query=True, raise_on_error=False)
        out.extend((kind, item_id) for item_id in field_values(str(raw), field))
    return out


def transfer_listings(items: Iterable[tuple[str, str]], *, marketplace: str, to: str, network: str,
                      identity: Optional[str], call=_dfx_call) -> tuple[int, int]:
    ok = failed = 0
    table = Table(title=f"Transfer to {to} on {marketplace} ({network})")
    table.add_column("kind")
    table.add_column("id")
    table.add_column("result")
    for kind, item_id in items:
        args = f"({candid_text(kind)}, {candid_text(item_id)}, {candid_text(to)})"
        raw = call(marketplace, "transfer_listing", args, network, identity, raise_on_error=False)
        done, msg = parse_generic_result(str(raw))
        table.add_row(kind, item_id, f"[green]{msg}[/green]" if done else f"[red]{msg}[/red]")
        if done:
            ok += 1
        else:
            failed += 1
    console.print(table)
    return ok, failed


def _ids(csv: str) -> list[str]:
    return [s.strip() for s in (csv or "").split(",") if s.strip()]


def marketplace_transfer_command(marketplace: str, to: str, network: str, identity: Optional[str],
                                 extensions: str = "", codices: str = "", assistants: str = "",
                                 call=_dfx_call) -> None:
    to = (to or "").strip()
    if not to:
        console.print("[red]--to is required[/red]")
        raise typer.Exit(code=2)
    named = [("ext", i) for i in _ids(extensions)] + [("codex", i) for i in _ids(codices)] \
        + [("assistant", i) for i in _ids(assistants)]
    items = named or owned_ids(marketplace, network, identity, call=call)
    if not items:
        console.print("[yellow]the signer owns no listings on this marketplace; name them with --extensions/--codices/--assistants[/yellow]")
        raise typer.Exit(code=1)
    ok, failed = transfer_listings(items, marketplace=marketplace, to=to, network=network, identity=identity, call=call)
    if failed:
        console.print(f"[red]{failed} listing(s) failed, {ok} transferred[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]{ok} listing(s) now owned by {to}[/green]")
