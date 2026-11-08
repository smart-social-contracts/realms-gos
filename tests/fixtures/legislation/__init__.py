"""A synthetic source of legislation, ``arcadia``, with two releases: the public record of a sync source in the shape of
docs/codices/layered-codex.md §3.2 and §8, and the chapter files its checksums name. It is invented; the repo is public.

``tests/ggg`` scripts a sync source's ``legislation_releases`` and ``legislation_chapter`` with it, and
``tests/deployed`` serves the same bytes over HTTPS.
"""

import hashlib
import json
from pathlib import Path

SOURCE = "arcadia"
FOLDER = Path(__file__).parent / SOURCE


def releases():
    """The entries of ``releases.json``, newest last: what ``legislation_releases`` answers."""
    return json.loads((FOLDER / "releases.json").read_text())


def layer(version):
    """Release ``version`` as an entry of the ``layers`` of a sync source's record, which ``plan`` takes (§8)."""
    return {"source": SOURCE, **next(r for r in releases() if r["version"] == version)}


def chapter(checksum):
    """The bytes of the chapter whose checksum is ``checksum`` (``sha256:<hex>``)."""
    for path in (FOLDER / "releases").glob("*/*.py"):
        if "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() == checksum:
            return path.read_bytes()
    raise KeyError(checksum)
