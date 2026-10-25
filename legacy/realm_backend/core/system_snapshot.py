"""Host-side diagnostics snapshot for the ``system.snapshot`` bridge verb.

This is the whole of what the ``system_info`` extension used to do for itself:
walk the filesystem, enumerate every ORM entity type, read canister cycles and
stable memory, list wallet tokens. All of it is host access, which is why that
extension could not be sandboxed by porting alone — there was nothing left
once you removed the host imports.

Moving the gathering here inverts that. The extension becomes a presenter of a
fixed, admin-gated blob, and marketplace-updatable extension code loses the
ability to walk ``/`` or touch arbitrary ORM classes. The data returned is
realm-wide operational metadata, never member data, so it is scoped by a
single admin operation rather than per record.
"""

import os
import sys

SKIP_ROOTS = ("/proc", "/sys", "/dev")


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def _runtime() -> dict:
    info = {
        "python": {
            "version": sys.version,
            "platform": getattr(sys, "platform", "wasm32"),
        }
    }
    try:
        import basilisk

        info["basilisk"] = {
            "version": getattr(basilisk, "__version__", "unknown"),
            "rust_version": getattr(basilisk, "__rust_version__", "unknown"),
        }
    except Exception as e:
        info["basilisk"] = {"error": str(e)}
    return info


def db_stats() -> dict:
    """Per-entity-type row counts across the whole database."""
    from ic_python_db import Database

    db = Database.get_instance()
    counts = {}
    total = 0
    for cls in db._entity_types.values():
        name = cls.__name__
        if name in counts:
            continue
        count = _safe(cls.count, -1)
        counts[name] = count
        if count > 0:
            total += count
    return {
        "entity_types": len(counts),
        "total_entities": total,
        "counts": counts,
    }


def canister_stats() -> dict:
    """Cycles, stable memory, and the realm's known canister ids."""
    from basilisk import ic

    canister_id = _safe(lambda: ic.id().to_str(), "unknown")
    cycles = _safe(ic.canister_balance128, 0) or 0
    pages = _safe(ic.stable_size, 0) or 0
    stable_bytes = pages * 65536

    canisters = {"backend": canister_id}
    try:
        from ggg import Realm

        realm = Realm.load("1")
        for key, attr in (
            ("frontend", "frontend_canister_id"),
            ("token", "token_canister_id"),
            ("nft", "nft_canister_id"),
        ):
            value = getattr(realm, attr, None) if realm else None
            if value:
                canisters[key] = value
    except Exception:
        pass

    return {
        "canister_id": canister_id,
        "cycles": cycles,
        "cycles_tc": round(cycles / 1_000_000_000_000, 4),
        "time_ns": _safe(ic.time, 0),
        "stable_memory_pages": pages,
        "stable_memory_bytes": stable_bytes,
        "stable_memory_mb": round(stable_bytes / (1024 * 1024), 2),
        "canisters": canisters,
    }


def token_balances() -> dict:
    """Cached wallet balances. No inter-canister calls, so safe in a query."""
    tokens = []
    try:
        from basilisk.os.entities import Token
        from basilisk.os.wallet import Wallet

        wallet = Wallet()
        for token in Token.instances():
            wallet.register_token(
                name=token.name,
                ledger=token.ledger,
                indexer=getattr(token, "indexer", "") or "",
                decimals=getattr(token, "decimals", 8) or 8,
                fee=getattr(token, "fee", 10) or 10,
            )
        for entry in wallet.list_tokens():
            name = entry["name"]
            tokens.append({
                "name": name,
                "ledger": entry.get("ledger", ""),
                "balance_raw": _safe(lambda: wallet.cached_balance(name), 0),
                "decimals": entry.get("decimals", 8),
            })
    except Exception:
        pass
    return {"tokens": tokens, "count": len(tokens)}


def file_stats() -> dict:
    """File counts and sizes, by extension.

    Walking ``/`` is exactly the kind of ambient authority that should not sit
    in extension code, which is the point of it living here.
    """
    total_files = 0
    total_size = 0
    by_extension = {}
    try:
        for root, _dirs, files in os.walk("/"):
            if root.startswith(SKIP_ROOTS):
                continue
            for name in files:
                total_files += 1
                suffix = os.path.splitext(name)[1].lower() or "(no ext)"
                by_extension[suffix] = by_extension.get(suffix, 0) + 1
                total_size += _safe(
                    lambda: os.path.getsize(os.path.join(root, name)), 0
                ) or 0
    except Exception:
        pass

    top = dict(sorted(by_extension.items(), key=lambda kv: kv[1],
                      reverse=True)[:10])
    return {
        "total_files": total_files,
        "total_size_bytes": total_size,
        "total_size_mb": round(total_size / (1024 * 1024), 2),
        "top_extensions": top,
    }


def extensions_info() -> dict:
    extensions = []
    try:
        from core.runtime_extensions import get_all_extension_manifests

        for name, manifest in (get_all_extension_manifests() or {}).items():
            extensions.append({
                "name": name,
                "version": manifest.get("version", ""),
                "description": manifest.get("description", ""),
                "sandboxed": manifest.get("runtime") != "in_process",
            })
    except Exception:
        pass
    return {"extensions": sorted(extensions, key=lambda e: e["name"]),
            "count": len(extensions)}


SECTIONS = {
    "runtime": _runtime,
    "db": db_stats,
    "canister": canister_stats,
    "tokens": token_balances,
    "files": file_stats,
    "extensions": extensions_info,
}


def snapshot(sections=None) -> dict:
    """Gather the requested sections, defaulting to all of them.

    A failing section degrades to an error entry rather than sinking the whole
    snapshot — this is a diagnostics panel, and it is most wanted precisely
    when something is broken.
    """
    wanted = [s for s in (sections or SECTIONS) if s in SECTIONS]
    out = {}
    for name in wanted:
        try:
            out[name] = SECTIONS[name]()
        except Exception as e:
            out[name] = {"error": str(e)}
    return out
