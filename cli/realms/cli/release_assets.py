"""Asset names on realms-gos GitHub releases.

From v0.6.1 on, the release workflow stamps the tag on every asset name:
``realm_backend.wasm.gz`` ships as ``realm-backend-v0.6.1.wasm.gz``. v0.6.0 and
older releases keep the unversioned names they were published with. Because the
stamped name carries the tag, ``releases/latest/download/<name>`` cannot address
it; resolve the latest tag first.
"""

from __future__ import annotations

import re
import urllib.request

REALMS_RELEASE_REPO = "smart-social-contracts/realms-gos"
FIRST_STAMPED_RELEASE = (0, 6, 1)


def _semver(tag: str):
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", tag.strip())
    return tuple(int(x) for x in m.groups()) if m else None


def normalize_tag(version: str) -> str:
    v = version.strip()
    return v if v.startswith("v") else f"v{v}"


def asset_name(name: str, tag: str) -> str:
    """The file name ``name`` (e.g. ``realm_backend.wasm.gz``) has on release ``tag``."""
    version = _semver(tag)
    if version is None or version < FIRST_STAMPED_RELEASE:
        return name
    stem, dot, ext = name.partition(".")
    return f"{stem.replace('_', '-')}-{normalize_tag(tag)}{dot}{ext}"


def asset_url(name: str, tag: str, *, repo: str = REALMS_RELEASE_REPO) -> str:
    tag = normalize_tag(tag)
    return f"https://github.com/{repo}/releases/download/{tag}/{asset_name(name, tag)}"


def latest_tag(*, repo: str = REALMS_RELEASE_REPO, timeout: int = 30) -> str:
    """Tag of the latest release, read from the ``releases/latest`` redirect (no API rate limit)."""
    req = urllib.request.Request(f"https://github.com/{repo}/releases/latest", method="HEAD")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        final = resp.geturl()
    tag = final.rstrip("/").rsplit("/", 1)[-1]
    if "/releases/tag/" not in final or _semver(tag) is None:
        raise ValueError(f"no latest release for {repo} (resolved to {final})")
    return tag
