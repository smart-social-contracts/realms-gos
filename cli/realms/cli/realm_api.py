"""A realm's operations, called by name: ``read`` for queries, ``run`` for changes.

    call_op(canister, "proposal_vote", {"proposal": "P-7", "choice": "yes"}, network="staging", identity="dev-identity")
    call_op(canister, "me", read=True)

Replies are the realm's JSON: ``{"ok": true, ...}`` or ``{"ok": false, "error": ...}``.
``read_ops(canister)`` lists every operation (``ops_list``) with its permission, flags and description.
"""

import json
from typing import Optional

from .commands.extension import _dfx_call


def quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def op_arg(name: str, args: Optional[dict] = None) -> str:
    """The candid argument of ``read``/``run``: the operation's name and its JSON arguments."""
    return f"({quote(name)}, {quote(json.dumps(args or {}))})"


def call_op(canister: str, name: str, args: Optional[dict] = None, network: str = "local",
            identity: Optional[str] = None, read: bool = False, timeout: int = 120) -> dict:
    """Run operation ``name`` on the realm ``canister`` and return its decoded reply."""
    out = _dfx_call(canister, "read" if read else "run", op_arg(name, args), network, identity,
                    is_query=read, timeout=timeout)
    return json.loads(out)


def read_ops(canister: str, network: str = "local", identity: Optional[str] = None) -> dict:
    """Every operation of the realm by name: ``{name: {perm, read, delegable, governed, signing, public, doc}}``."""
    reply = call_op(canister, "ops_list", network=network, identity=identity, read=True)
    return {o["name"]: o for o in reply.get("operations", [])}


def call_any(canister: str, name: str, args: Optional[dict] = None, network: str = "local",
             identity: Optional[str] = None, timeout: int = 120) -> dict:
    """Like ``call_op``, choosing ``read`` or ``run`` from the realm's own ``ops_list``."""
    spec = read_ops(canister, network, identity).get(name)
    if spec is None:
        return {"ok": False, "error": f"unknown operation '{name}'"}
    return call_op(canister, name, args, network, identity, read=spec["read"], timeout=timeout)
