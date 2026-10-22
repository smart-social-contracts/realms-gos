"""Registry listings with no canister behind them, published from a spec file.

The spec is a JSON object with a ``listings`` list. Each listing has an ``id``
(lowercase words joined by dashes), ``name``, ``stage``, ``users``,
``manifesto``, a ``center`` [lat, lng], a ``cells`` count, optional
``districts`` (names for the busiest cells), optional ``logo`` (https URL),
optional ``background`` and optional ``active_days_ago``.

``background`` names a photo on Wikimedia Commons and who took it::

    "background": {"commons": "File:Harbour at dawn.jpg",
                   "author": "A. Photographer", "license": "CC BY-SA 4.0"}

Zones are H3 cells at resolution 6 grown outward from the centre in a shape
fixed by the listing id, so publishing twice gives the same map. Members are
spread over the cells, more of them near the centre.

With ``--file-registry`` the listing also gets images, stored in that file
registry under ``listings/<id>/``: a logo drawn from the id and name (unless
the spec gives one), and the Commons photo, downloaded after checking that its
licence is free. The listing records the photo's credit and its Commons page.
A file that is already stored with the same content is not uploaded again.
"""

import base64
import hashlib
import json
import math
import re
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Optional
from xml.sax.saxutils import escape

import typer
from rich.table import Table

from ..utils import console, get_registry_canister_id

RESOLUTION = 6
PAGE = 50

ASSET_NAMESPACE = "listings"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
# Wikimedia refuses requests without a descriptive User-Agent.
USER_AGENT = "realms-gos-listings/1.0 (https://github.com/smart-social-contracts/realms)"
PHOTO_WIDTHS = (1280, 960, 640)
# The file registry stores a file in one message, and a 400 KB one runs out of instructions.
PHOTO_MAX_BYTES = 250_000
FREE_LICENCES = ("cc0", "cc by", "cc-by", "public domain", "pd")


def _h3():
    try:
        import h3
    except ImportError:
        console.print("[red]❌ Generating zones needs the h3 package: pip install 'realms-gos[listings]'[/red]")
        raise typer.Exit(1)
    return h3


def _unit(seed: str, *parts) -> float:
    """A number in [0, 1) fixed by *seed* and *parts*."""
    digest = hashlib.sha256("|".join([seed, *map(str, parts)]).encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def generate_zones(listing: dict) -> list:
    h3 = _h3()
    seed = listing["id"]
    lat, lng = listing["center"]
    target = max(1, int(listing.get("cells", 20)))
    origin = h3.latlng_to_cell(lat, lng, RESOLUTION)

    chosen, taken = [origin], {origin}
    k = 1
    while len(chosen) < target and k <= 40:
        for cell in sorted(h3.grid_ring(origin, k), key=lambda c: _unit(seed, c)):
            if len(chosen) == target:
                break
            touches = any(n in taken for n in h3.grid_ring(cell, 1))
            if touches and _unit(seed, "keep", cell) < 0.75:
                chosen.append(cell)
                taken.add(cell)
        k += 1

    weights = []
    for cell in chosen:
        steps = h3.grid_distance(origin, cell)
        weights.append(math.exp(-steps / 1.6) * (0.6 + 0.8 * _unit(seed, "w", cell)))
    total = sum(weights)
    users = int(listing.get("users", 0))
    counts = [int(users * w / total) for w in weights]
    for i in sorted(range(len(counts)), key=lambda i: -weights[i])[: users - sum(counts)]:
        counts[i] += 1

    districts = list(listing.get("districts") or [])
    order = sorted(range(len(chosen)), key=lambda i: -counts[i])
    names = {i: districts[rank] for rank, i in enumerate(order[: len(districts)])}
    return [
        {
            "h3_index": cell,
            "user_count": counts[i],
            "name": names.get(i, listing["name"]),
            "location_name": names.get(i, listing["name"]),
        }
        for i, cell in enumerate(chosen)
    ]


def build_payload(listing: dict, now: Optional[float] = None) -> dict:
    now = time.time() if now is None else now
    payload = {
        "id": listing["id"],
        "name": listing["name"],
        "stage": listing.get("stage", "alpha"),
        "users_count": int(listing.get("users", 0)),
        "manifesto": listing.get("manifesto", ""),
        "zones": generate_zones(listing),
    }
    if listing.get("logo"):
        payload["logo"] = listing["logo"]
    if listing.get("active_days_ago") is not None:
        payload["last_activity_at"] = int(now - float(listing["active_days_ago"]) * 86400)
    return payload


def _initials(name: str) -> str:
    words = [w for w in name.split() if any(ch.isalpha() for ch in w)]
    letters = [next(ch for ch in w if ch.isalpha()) for w in words[:2]]
    if len(letters) == 1:
        letters += [ch for ch in words[0] if ch.isalpha()][1:2]
    return "".join(letters).upper() or "?"


def generate_logo(listing: dict) -> str:
    """A square SVG: the listing's initials on a hexagon, in colours fixed by its id."""
    seed = listing["id"]
    hue = int(_unit(seed, "hue") * 360)
    second = (hue + 35 + int(_unit(seed, "hue2") * 50)) % 360
    hexagon = " ".join(
        f"{128 + 96 * math.cos(math.radians(60 * i - 90)):.1f},{128 + 96 * math.sin(math.radians(60 * i - 90)):.1f}"
        for i in range(6)
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256">'
        '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="hsl({hue},62%,46%)"/>'
        f'<stop offset="1" stop-color="hsl({second},58%,30%)"/>'
        "</linearGradient></defs>"
        '<rect width="256" height="256" rx="48" fill="url(#g)"/>'
        f'<polygon points="{hexagon}" fill="none" stroke="#fff" stroke-opacity="0.35" stroke-width="8"/>'
        '<text x="128" y="128" text-anchor="middle" dominant-baseline="central" fill="#fff" '
        'font-family="Inter, system-ui, -apple-system, Segoe UI, Roboto, sans-serif" '
        f'font-size="88" font-weight="700">{escape(_initials(listing["name"]))}</text>'
        "</svg>"
    )


def _http_get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def _is_free(licence: str) -> bool:
    name = licence.strip().lower()
    words = set(re.split(r"[^a-z0-9]+", name))
    return name.startswith(FREE_LICENCES) and not words & {"nc", "nd"}


def commons_photo(title: str, fetch: Optional[Callable[[str], bytes]] = None) -> dict:
    """Download a Commons photo, small enough to store, once its licence is known to be free."""
    fetch = fetch or _http_get
    for width in PHOTO_WIDTHS:
        query = urllib.parse.urlencode({
            "action": "query", "format": "json", "titles": title, "prop": "imageinfo",
            "iiprop": "url|mime|extmetadata", "iiurlwidth": width,
        })
        pages = json.loads(fetch(f"{COMMONS_API}?{query}"))["query"]["pages"]
        info = next(iter(pages.values())).get("imageinfo")
        if not info:
            raise ValueError(f"{title} is not on Wikimedia Commons")
        info = info[0]
        licence = info.get("extmetadata", {}).get("LicenseShortName", {}).get("value", "")
        if not _is_free(licence):
            raise ValueError(f"{title} is not under a free licence ({licence or 'none given'})")
        content = fetch(info["thumburl"])
        if len(content) <= PHOTO_MAX_BYTES:
            return {
                "content": content,
                "content_type": info.get("thumbmime") or info.get("mime") or "image/jpeg",
                "license": licence,
                "source": info["descriptionurl"],
            }
    raise ValueError(f"{title} is larger than {PHOTO_MAX_BYTES} bytes even at {PHOTO_WIDTHS[-1]}px")


def photo_credit(author: str, licence: str) -> str:
    tail = f", {licence}, Wikimedia Commons"
    author = " ".join(author.split()) or "unknown author"
    return "Photo: " + author[: 200 - len("Photo: ") - len(tail)] + tail


class FileRegistry:
    """Stores listing images in a file registry and returns the URLs that serve them."""

    def __init__(self, canister: str, network: str, identity: Optional[str]):
        self.canister, self.network, self.identity = canister, network, identity
        self._stored = None

    def stored(self) -> dict:
        if self._stored is None:
            reply = _call("list_files", _text_arg(json.dumps({"namespace": ASSET_NAMESPACE})),
                          self.network, self.canister, self.identity, query=True)
            files = json.loads(reply)
            self._stored = {f["path"]: f["sha256"] for f in files} if isinstance(files, list) else {}
        return self._stored

    def put(self, path: str, content: bytes, content_type: str) -> str:
        sha = hashlib.sha256(content).hexdigest()
        if self.stored().get(path) != sha:
            reply = json.loads(_call("store_file", _text_arg(json.dumps({
                "namespace": ASSET_NAMESPACE,
                "path": path,
                "content_b64": base64.b64encode(content).decode(),
                "content_type": content_type,
            })), self.network, self.canister, self.identity))
            if reply.get("error"):
                raise RuntimeError(f"storing {path}: {reply['error']}")
            self._stored[path] = sha
        return f"https://{self.canister}.icp0.io/{ASSET_NAMESPACE}/{path}?v={sha[:12]}"


def add_images(listing: dict, payload: dict, files: FileRegistry,
               fetch: Optional[Callable[[str], bytes]] = None) -> None:
    """Store the listing's logo and background and put their URLs in *payload*."""
    if not listing.get("logo"):
        payload["logo"] = files.put(f"{listing['id']}/logo.svg", generate_logo(listing).encode(), "image/svg+xml")
    background = listing.get("background")
    if not background:
        return
    photo = commons_photo(background["commons"], fetch)
    expected = background.get("license")
    if expected and expected.strip().lower() != photo["license"].strip().lower():
        console.print(f"[yellow]⚠️  {listing['id']}: Commons gives the licence as {photo['license']}, "
                      f"not {expected}; crediting {photo['license']}[/yellow]")
    extension = {"image/png": "png", "image/webp": "webp"}.get(photo["content_type"], "jpg")
    payload["background"] = files.put(f"{listing['id']}/background.{extension}",
                                      photo["content"], photo["content_type"])
    payload["background_credit"] = photo_credit(background.get("author", ""), photo["license"])
    payload["background_source"] = photo["source"]


def load_spec(path: str) -> list:
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    listings = spec.get("listings") if isinstance(spec, dict) else None
    if not isinstance(listings, list) or not listings:
        console.print(f"[red]❌ {path} has no 'listings' list[/red]")
        raise typer.Exit(1)
    ids = [entry.get("id") for entry in listings]
    if len(set(ids)) != len(ids) or not all(ids):
        console.print(f"[red]❌ Every listing in {path} needs a distinct id[/red]")
        raise typer.Exit(1)
    return listings


def _call(method: str, candid_args: str, network: str, canister: str,
          identity: Optional[str], query: bool = False) -> str:
    """Call the registry with dfx and return the text it replies with."""
    with tempfile.NamedTemporaryFile("w", suffix=".args", delete=False) as f:
        f.write(candid_args)
        args_file = f.name
    cmd = ["dfx", "canister", "call", "--network", network, "--output", "json",
           "--argument-file", args_file]
    if identity:
        cmd += ["--identity", identity]
    if query:
        cmd.append("--query")
    cmd += [canister, method]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    finally:
        Path(args_file).unlink(missing_ok=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    reply = json.loads(result.stdout)
    return reply[0] if isinstance(reply, list) else reply


def _text_arg(value: str) -> str:
    return "(" + json.dumps(value) + ")"


def _registry(network: str, canister_id: Optional[str]) -> str:
    registry = canister_id or get_registry_canister_id(network)
    if not registry:
        console.print("[red]❌ Could not determine the registry canister; pass --canister-id[/red]")
        raise typer.Exit(1)
    return registry


def publish_command(spec: str, network: str, canister_id: Optional[str], identity: Optional[str],
                    file_registry: Optional[str] = None) -> None:
    registry = _registry(network, canister_id)
    files = None
    if not file_registry:
        console.print("[dim]No --file-registry: logos and backgrounds are left as they are[/dim]")
    elif network != "ic":
        console.print("[yellow]⚠️  Listing images need https URLs, which only the ic network gives; skipping them[/yellow]")
    else:
        files = FileRegistry(file_registry, network, identity)
    failures = 0
    for listing in load_spec(spec):
        payload = build_payload(listing)
        try:
            if files:
                add_images(listing, payload, files)
            answer = json.loads(_call("listing_publish", _text_arg(json.dumps(payload)),
                                      network, registry, identity))
        except (RuntimeError, ValueError, OSError) as e:
            answer = {"success": False, "error": str(e)}
        if answer.get("success"):
            images = " with images" if files else ""
            console.print(f"✅ {listing['id']}: {len(payload['zones'])} zones{images}, revision {answer.get('revision')}")
        else:
            failures += 1
            console.print(f"[red]❌ {listing['id']}: {answer.get('error')}[/red]")
    if failures:
        raise typer.Exit(1)


def remove_command(spec: Optional[str], listing_id: Optional[str], network: str,
                   canister_id: Optional[str], identity: Optional[str]) -> None:
    if bool(spec) == bool(listing_id):
        console.print("[red]❌ Pass either a spec file or --id[/red]")
        raise typer.Exit(1)
    registry = _registry(network, canister_id)
    ids = [listing_id] if listing_id else [entry["id"] for entry in load_spec(spec)]
    failures = 0
    for one in ids:
        try:
            reply = _call("remove_realm", _text_arg(one), network, registry, identity)
        except RuntimeError as e:
            reply = {"Err": str(e)}
        if isinstance(reply, dict) and "Ok" in reply:
            console.print(f"🗑️  {one} removed")
        else:
            failures += 1
            console.print(f"[red]❌ {one}: {reply.get('Err') if isinstance(reply, dict) else reply}[/red]")
    if failures:
        raise typer.Exit(1)


def list_command(network: str, canister_id: Optional[str], identity: Optional[str]) -> None:
    registry = _registry(network, canister_id)
    table = Table(title="Registry listings")
    for column in ("id", "name", "canister", "stage", "members", "revision"):
        table.add_column(column)
    cursor = 1
    while cursor is not None:
        page = json.loads(_call("list_listings", f"({cursor} : nat, {PAGE} : nat)",
                                network, registry, identity, query=True))
        if not page.get("success"):
            console.print(f"[red]❌ {page.get('error')}[/red]")
            raise typer.Exit(1)
        for entry in page["listings"]:
            table.add_row(entry["id"], entry["name"], entry["canister_id"] or "—",
                          entry["stage"] or "—", f"{entry['users_count']:,}", str(entry["revision"]))
        cursor = page.get("next")
    console.print(table)
