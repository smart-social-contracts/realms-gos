"""Top-N rankings of extensions, codices and assistants, by installs and likes.

Read from sorted indexes (Entity.top_by): a ranking is one range read plus
the listings it returns, however many listings exist. Ties in the first
counter are broken by the second, then by the newer listing.
"""

from typing import Dict, List

from api.assistants import _to_dict as _assistant_to_dict
from api.codices import _to_dict as _codex_to_dict
from api.extensions import _to_dict as _ext_to_dict
from core.models import (
    AssistantListingEntity,
    CodexListingEntity,
    ExtensionListingEntity,
)


def _clamp_n(n: int) -> int:
    n = int(n or 0)
    if n <= 0:
        n = 20
    return min(n, 100)


# Listings read per ranking call, matching or not: about 1M instructions each,
# far below a query's 5B budget
_SCAN_LIMIT = 500


def _top(entity_cls, fields, n: int, verified_only: bool) -> list:
    def visible(listing) -> bool:
        if not (listing.is_active if listing.is_active is not None else True):
            return False
        return not verified_only or str(listing.verification_status) == "verified"

    listings, _ = entity_cls.top_by(
        fields, count=_clamp_n(n), where=visible, scan_limit=_SCAN_LIMIT
    )
    return listings


def top_extensions_by_downloads(n: int, verified_only: bool = False) -> List[Dict]:
    items = _top(ExtensionListingEntity, ("installs", "likes"), n, verified_only)
    return [_ext_to_dict(e) for e in items]


def top_extensions_by_likes(n: int, verified_only: bool = False) -> List[Dict]:
    items = _top(ExtensionListingEntity, ("likes", "installs"), n, verified_only)
    return [_ext_to_dict(e) for e in items]


def top_codices_by_downloads(n: int, verified_only: bool = False) -> List[Dict]:
    items = _top(CodexListingEntity, ("installs", "likes"), n, verified_only)
    return [_codex_to_dict(c) for c in items]


def top_codices_by_likes(n: int, verified_only: bool = False) -> List[Dict]:
    items = _top(CodexListingEntity, ("likes", "installs"), n, verified_only)
    return [_codex_to_dict(c) for c in items]


def top_assistants_by_downloads(n: int, verified_only: bool = False) -> List[Dict]:
    items = _top(AssistantListingEntity, ("installs", "likes"), n, verified_only)
    return [_assistant_to_dict(a) for a in items]


def top_assistants_by_likes(n: int, verified_only: bool = False) -> List[Dict]:
    items = _top(AssistantListingEntity, ("likes", "installs"), n, verified_only)
    return [_assistant_to_dict(a) for a in items]
