"""Canonical JSON and the id of a codex (docs/codices/layered-codex.md §9.1), as the tools write them.

The realm computes the same in ``src/realm_backend/core/layered_codex.py``; this copy keeps the CLI free of the
canister's modules, and ``tests/ggg`` holds the two equal. ``realms legislation resolve`` prints the id, and
``propose`` writes a change list as these bytes, so the checksum a proposal pins is the checksum of the file.
"""

import json
from hashlib import sha256


def canonical(value) -> bytes:
    """Sorted keys, no spaces, UTF-8."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def codex_id(plan) -> str:
    """The id of the codex ``plan`` describes: ``sha256:`` and the hex sha256 of the canonical JSON of its layers,
    layer paths and chapters (a calendar is the chapter ``<source>/calendar``). It is what a change list's
    ``replaces`` names."""
    chapters = {f"{c['source']}/{c['domain']}": c["sha256"] for c in plan.chapters}
    chapters.update({f"{c['source']}/calendar": c["sha256"] for c in plan.calendars})
    legislation = {
        "layers": plan.layers,
        "layer_paths": plan.paths,
        "chapters": chapters,
    }
    return "sha256:" + sha256(canonical(legislation)).hexdigest()
