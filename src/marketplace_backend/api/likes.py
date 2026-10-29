"""Like / unlike — works for both extensions and codices.

Like is idempotent (repeat calls return Ok), and the listing entity
carries a denormalised counter that mirrors the row count in the
``LikeEntity`` table for fast ranking queries.
"""

from typing import Any, Dict, List

from _cdk import ic
from core.models import (
    AssistantListingEntity,
    CodexListingEntity,
    ExtensionListingEntity,
    LikeEntity,
    likes_of,
)
from ic_python_logging import get_logger

logger = get_logger("api.likes")


VALID_KINDS = ("ext", "codex", "assistant")


def _now() -> float:
    return float(ic.time())


def _like_id(principal: str, item_kind: str, item_id: str) -> str:
    return f"{principal}|{item_kind}|{item_id}"


def _safe_codex_alias(codex_id: str) -> str:
    return (codex_id or "").replace("/", "__")


def _safe_assistant_alias(assistant_id: str) -> str:
    return (assistant_id or "").replace("/", "__")


def _adjust_counter(item_kind: str, item_id: str, delta: int) -> None:
    if item_kind == "ext":
        ext = ExtensionListingEntity[item_id]
        if ext is not None:
            new_val = max(0, int(ext.likes or 0) + delta)
            ext.likes = new_val
    elif item_kind == "codex":
        c = CodexListingEntity[_safe_codex_alias(item_id)]
        if c is not None:
            new_val = max(0, int(c.likes or 0) + delta)
            c.likes = new_val
    elif item_kind == "assistant":
        a = AssistantListingEntity[_safe_assistant_alias(item_id)]
        if a is not None:
            new_val = max(0, int(a.likes or 0) + delta)
            a.likes = new_val


def like_item(principal: str, item_kind: str, item_id: str) -> Dict:
    if item_kind not in VALID_KINDS:
        return {"success": False, "error": f"item_kind must be one of {VALID_KINDS}"}
    if not item_id or not item_id.strip():
        return {"success": False, "error": "item_id is required"}

    lid = _like_id(principal, item_kind, item_id)
    existing = LikeEntity[lid]
    if existing is not None:
        return {"success": True, "like_id": lid, "action": "exists"}

    LikeEntity(
        like_id=lid,
        principal=principal,
        item_kind=item_kind,
        item_id=item_id,
        created_at=_now(),
    )
    _adjust_counter(item_kind, item_id, +1)
    logger.info(f"like {item_kind}:{item_id} by {principal}")
    return {"success": True, "like_id": lid, "action": "created"}


def unlike_item(principal: str, item_kind: str, item_id: str) -> Dict:
    if item_kind not in VALID_KINDS:
        return {"success": False, "error": f"item_kind must be one of {VALID_KINDS}"}

    lid = _like_id(principal, item_kind, item_id)
    existing = LikeEntity[lid]
    if existing is None:
        return {"success": True, "like_id": lid, "action": "noop"}

    existing.delete()
    _adjust_counter(item_kind, item_id, -1)
    logger.info(f"unlike {item_kind}:{item_id} by {principal}")
    return {"success": True, "like_id": lid, "action": "deleted"}


def has_liked(principal: str, item_kind: str, item_id: str) -> bool:
    return LikeEntity[_like_id(principal, item_kind, item_id)] is not None


def my_likes(principal: str) -> List[Dict[str, Any]]:
    out = []
    for like in likes_of(principal):
        out.append({
            "item_kind": str(like.item_kind),
            "item_id": str(like.item_id),
            "created_at": float(like.created_at or 0),
        })
    out.sort(key=lambda r: r.get("created_at", 0), reverse=True)
    return out


def recount_listing_likes() -> Dict:
    """Controller-only: rebuild denormalised counters from the LikeEntity table.

    Useful after manual data edits or migrations.
    """
    by_key: Dict[str, int] = {}
    for like in LikeEntity.instances():
        key = f"{like.item_kind}|{like.item_id}"
        by_key[key] = by_key.get(key, 0) + 1

    for ext in ExtensionListingEntity.instances():
        ext.likes = by_key.get(f"ext|{ext.extension_id}", 0)
    for c in CodexListingEntity.instances():
        c.likes = by_key.get(f"codex|{c.codex_id}", 0)
    for a in AssistantListingEntity.instances():
        a.likes = by_key.get(f"assistant|{a.assistant_id}", 0)

    return {"success": True, "counters": by_key, "items": len(by_key)}
