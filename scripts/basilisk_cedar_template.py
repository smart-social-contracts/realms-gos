"""Pinned Cedar basilisk template — the only realm_backend pack path.

The plain CPython template (``cpython_canister_template.wasm``) has no
``_basilisk_sandbox`` and is not selectable. basilisk honours
``BASILISK_TEMPLATE_WASM`` over its default cache file; every leftover-free
or layered pack must set that variable to the Cedar image
(``cpython_canister_template_cedar.wasm`` from the pinned ic-basilisk release,
see ``CEDAR_TEMPLATE_BASILISK_VERSION``).

This module does not inspect a finished realm WASM. It only pins the
template used to pack one, by version and by sha256: the template is checked
against ``CEDAR_TEMPLATE_SHA256`` every time it is used, downloaded or cached,
and a mismatch stops the pack.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Mapping, MutableMapping, Optional, Union

PathLike = Union[str, os.PathLike]

CEDAR_TEMPLATE_NAME = "cpython_canister_template_cedar.wasm"
PLAIN_TEMPLATE_NAME = "cpython_canister_template.wasm"

# ic-basilisk release whose Cedar template realm backends pack with. Keep in
# sync with the ``ic-basilisk==`` pin in requirements.txt (a test enforces it).
# Versioned releases carry the Cedar asset from v0.15.1 on; the rolling
# ``cpython-wasm-3.13.0-ic1`` asset changes under us on every basilisk main
# build, which is why it is no longer used here.
CEDAR_TEMPLATE_BASILISK_VERSION = "0.15.1"
CEDAR_TEMPLATE_URL = (
    "https://github.com/smart-social-contracts/basilisk/releases/download/"
    f"v{CEDAR_TEMPLATE_BASILISK_VERSION}/{CEDAR_TEMPLATE_NAME}"
)

# sha256 of that release's Cedar template. basilisk checks nothing named by
# BASILISK_TEMPLATE_WASM, and v0.15.1 ships no checksums.txt and no signature,
# so this pin is the only check the template gets. Its provenance: the asset is
# unchanged since github-actions uploaded it on 16 Sep 2026 (updated_at equals
# created_at) and is byte-identical to a copy cached on 19 Sep (realms-gos#793).
# Change it only with CEDAR_TEMPLATE_BASILISK_VERSION, taking the new value
# from that release's signed checksums.txt.
CEDAR_TEMPLATE_SHA256 = "fe33dc809ca8e936945196fce57b31ec081507ae41d6e693fa75f0a59af2a3ef"


def is_plain_cpython_template(path: PathLike) -> bool:
    """True if *path* is the default/plain CPython template (no sandbox)."""
    return Path(path).name == PLAIN_TEMPLATE_NAME


def cedar_template_cache_path() -> Path:
    # Scoped by basilisk version so bumping the pin can never reuse a stale
    # cached template from an older release.
    return (
        Path.home()
        / ".cache"
        / "realms"
        / "templates"
        / f"v{CEDAR_TEMPLATE_BASILISK_VERSION}"
        / CEDAR_TEMPLATE_NAME
    )


def _require_cedar_template(template_path: Path) -> None:
    if is_plain_cpython_template(template_path):
        raise SystemExit(
            f"plain CPython template {template_path} is not a realm_backend "
            f"pack path. Use {CEDAR_TEMPLATE_NAME} "
            f"(ic-basilisk {CEDAR_TEMPLATE_BASILISK_VERSION})."
        )
    if template_path.name != CEDAR_TEMPLATE_NAME:
        raise SystemExit(
            f"realm_backend must pack with {CEDAR_TEMPLATE_NAME}, not "
            f"{template_path.name}"
        )
    if not template_path.is_file():
        raise SystemExit(f"Cedar template not found: {template_path}")
    _require_pinned_sha256(template_path)


def _require_pinned_sha256(path: Path) -> None:
    got = _sha256(path)
    if got != CEDAR_TEMPLATE_SHA256:
        raise SystemExit(
            f"Cedar template {path} has sha256 {got}, not the pinned "
            f"{CEDAR_TEMPLATE_SHA256} of ic-basilisk "
            f"{CEDAR_TEMPLATE_BASILISK_VERSION}: refusing to pack with it. "
            f"Delete it to fetch it again. If a fresh download does not match "
            f"either, the published asset has changed: stop and report it."
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def ensure_cedar_template() -> str:
    """Fetch the pinned Cedar template into the local cache and return its path.

    The file is checked against ``CEDAR_TEMPLATE_SHA256`` on every call, so a
    cached copy that changed is refused like a download that does not match.
    A download goes to a temporary name and reaches the cache path only once
    it matches: an interrupted or wrong one never lands there.
    """
    dest = cedar_template_cache_path()
    if not dest.exists():
        import urllib.request

        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        print(f"   ⬇️  fetching Cedar template: {CEDAR_TEMPLATE_URL}")
        urllib.request.urlretrieve(CEDAR_TEMPLATE_URL, part)
        try:
            _require_pinned_sha256(part)
        except SystemExit:
            part.unlink()
            raise
        part.replace(dest)
    _require_cedar_template(dest)
    return str(dest)


def apply_cedar_template_env(
    env: Optional[Mapping[str, str]] = None,
) -> MutableMapping[str, str]:
    """Copy *env* and force ``BASILISK_TEMPLATE_WASM`` to the Cedar template.

    An existing value — including the plain CPython template — is ignored.
    The old template is not selectable.
    """
    out: MutableMapping[str, str] = dict(os.environ if env is None else env)
    out["BASILISK_TEMPLATE_WASM"] = ensure_cedar_template()
    return out
