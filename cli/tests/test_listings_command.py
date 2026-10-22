"""realms registry listing: zones generated from a spec, published through dfx."""

import json
import re
import subprocess
from pathlib import Path

import pytest
import typer

from realms.cli.commands import listings

h3 = pytest.importorskip("h3")

LISTING = {
    "id": "harbour-commons", "name": "Harbour Commons", "center": [45.07, 7.69],
    "cells": 12, "users": 8420, "stage": "beta", "active_days_ago": 2,
    "districts": ["North Quay", "Old Town"], "manifesto": "A city that decides together.",
}


def test_zones_are_resolution_6_and_connected():
    zones = listings.generate_zones(LISTING)
    cells = [z["h3_index"] for z in zones]
    assert len(cells) == 12 == len(set(cells))
    assert all(h3.get_resolution(c) == 6 for c in cells)
    origin = h3.latlng_to_cell(45.07, 7.69, 6)
    assert cells[0] == origin
    for cell in cells[1:]:
        assert any(n in cells for n in h3.grid_ring(cell, 1))


def test_zones_are_the_same_every_time():
    assert listings.generate_zones(LISTING) == listings.generate_zones(dict(LISTING))


def test_members_add_up_and_the_busiest_cells_get_district_names():
    zones = listings.generate_zones(LISTING)
    assert sum(z["user_count"] for z in zones) == 8420
    busiest = sorted(zones, key=lambda z: -z["user_count"])
    assert [z["location_name"] for z in busiest[:2]] == ["North Quay", "Old Town"]
    assert busiest[-1]["location_name"] == "Harbour Commons"


def test_payload():
    payload = listings.build_payload(LISTING, now=1_800_000_000)
    assert payload["id"] == "harbour-commons"
    assert payload["last_activity_at"] == 1_800_000_000 - 2 * 86400
    assert payload["users_count"] == 8420 and "logo" not in payload


def test_zones_are_valid_for_the_registry():
    payload = listings.build_payload(LISTING, now=0)
    assert all(re.match(r"^[0-9a-f]{15,16}$", z["h3_index"]) for z in payload["zones"])
    assert all(len(z["name"]) <= 80 for z in payload["zones"])


def test_spec_ids_must_be_distinct(tmp_path):
    spec = tmp_path / "s.json"
    spec.write_text(json.dumps({"listings": [LISTING, LISTING]}))
    with pytest.raises(typer.Exit):
        listings.load_spec(str(spec))


def _fake_dfx(monkeypatch, replies):
    calls = []

    def run(cmd, capture_output, text, timeout):
        args = Path(cmd[cmd.index("--argument-file") + 1]).read_text()
        calls.append((cmd, args))
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(replies.pop(0)), stderr="")

    monkeypatch.setattr(listings.subprocess, "run", run)
    return calls


def test_publish_sends_one_call_per_listing(monkeypatch, tmp_path):
    spec = tmp_path / "s.json"
    spec.write_text(json.dumps({"listings": [LISTING, dict(LISTING, id="quay-commons")]}))
    ok = json.dumps({"success": True, "revision": 1})
    calls = _fake_dfx(monkeypatch, [ok, ok])
    listings.publish_command(str(spec), "ic", "e56by-5iaaa-aaaas-qg72a-cai", "dev-identity")

    cmd, args = calls[0]
    assert cmd[-2:] == ["e56by-5iaaa-aaaas-qg72a-cai", "listing_publish"]
    assert cmd[cmd.index("--identity") + 1] == "dev-identity"
    payload = json.loads(json.loads(args[1:-1]))
    assert payload["id"] == "harbour-commons" and len(payload["zones"]) == 12


def test_publish_fails_when_the_registry_refuses(monkeypatch, tmp_path):
    spec = tmp_path / "s.json"
    spec.write_text(json.dumps({"listings": [LISTING]}))
    _fake_dfx(monkeypatch, [json.dumps({"success": False, "error": "not a publisher"})])
    with pytest.raises(typer.Exit):
        listings.publish_command(str(spec), "ic", "reg", None)


def test_list_follows_the_cursor(monkeypatch):
    entry = {"id": "harbour-commons", "name": "Harbour Commons", "canister_id": "", "stage": "beta",
             "users_count": 8420, "revision": 1}
    pages = [json.dumps({"success": True, "listings": [entry], "next": 2}),
             json.dumps({"success": True, "listings": [entry], "next": None})]
    calls = _fake_dfx(monkeypatch, pages)
    listings.list_command("ic", "reg", None)
    assert [c[1] for c in calls] == ["(1 : nat, 50 : nat)", "(2 : nat, 50 : nat)"]
    assert all("--query" in c[0] for c in calls)


def test_remove_by_id(monkeypatch):
    calls = _fake_dfx(monkeypatch, [{"Ok": "Realm 'harbour-commons' removed"}])
    listings.remove_command(None, "harbour-commons", "ic", "reg", None)
    assert calls[0][0][-1] == "remove_realm" and calls[0][1] == '("harbour-commons")'


def test_logo_is_an_svg_with_the_initials_and_the_same_every_time():
    import xml.etree.ElementTree as ET

    svg = listings.generate_logo(LISTING)
    assert svg == listings.generate_logo(dict(LISTING))
    text = ET.fromstring(svg).find("{http://www.w3.org/2000/svg}text")
    assert text.text == "HC"
    assert svg != listings.generate_logo(dict(LISTING, id="quay-commons"))
    assert listings._initials("Riverside") == "RI"
    assert listings._initials("Água Branca") == "ÁB"


@pytest.mark.parametrize("licence, free", [
    ("CC BY-SA 4.0", True), ("CC BY 2.0", True), ("CC0", True), ("Public domain", True),
    ("CC BY-SA 3.0 ee", True), ("CC BY-NC-SA 4.0", False), ("CC BY-ND 4.0", False),
    ("All rights reserved", False), ("", False),
])
def test_only_free_licences_are_accepted(licence, free):
    assert listings._is_free(licence) is free


def _commons(licence="CC BY-SA 4.0", photos=None):
    """A fake Commons: the API answers with *licence*; a thumbnail at width w is photos[w] bytes."""
    photos = photos or {}
    asked = []

    def fetch(url):
        asked.append(url)
        if url.startswith(listings.COMMONS_API):
            width = int(re.search(r"iiurlwidth=(\d+)", url).group(1))
            info = {"thumburl": f"https://upload.wikimedia.org/thumb/{width}.jpg", "thumbmime": "image/jpeg",
                    "descriptionurl": "https://commons.wikimedia.org/wiki/File:Harbour.jpg",
                    "extmetadata": {"LicenseShortName": {"value": licence}}}
            return json.dumps({"query": {"pages": {"1": {"imageinfo": [info]}}}}).encode()
        width = int(url.rsplit("/", 1)[1].split(".")[0])
        return photos.get(width, b"jpeg")

    return fetch, asked


def test_a_photo_that_is_too_big_is_fetched_smaller():
    fetch, asked = _commons(photos={1280: b"x" * (listings.PHOTO_MAX_BYTES + 1)})
    photo = listings.commons_photo("File:Harbour.jpg", fetch)
    assert photo["content"] == b"jpeg" and photo["license"] == "CC BY-SA 4.0"
    assert photo["source"] == "https://commons.wikimedia.org/wiki/File:Harbour.jpg"
    assert asked[-1].endswith("/960.jpg")


def test_a_photo_that_is_not_free_is_refused_before_download():
    fetch, asked = _commons(licence="CC BY-NC 4.0")
    with pytest.raises(ValueError, match="free licence"):
        listings.commons_photo("File:Harbour.jpg", fetch)
    assert all(url.startswith(listings.COMMONS_API) for url in asked)


def test_a_long_credit_still_names_the_licence():
    credit = listings.photo_credit("A. " * 120, "CC BY-SA 4.0")
    assert len(credit) <= 200 and credit.endswith(", CC BY-SA 4.0, Wikimedia Commons")


def test_publish_with_a_file_registry_stores_images_once(monkeypatch, tmp_path):
    listing = dict(LISTING, background={"commons": "File:Harbour.jpg", "author": "A. Photographer",
                                        "license": "CC BY-SA 4.0"})
    spec = tmp_path / "s.json"
    spec.write_text(json.dumps({"listings": [listing]}))
    logo = listings.generate_logo(listing).encode()
    logo_sha = listings.hashlib.sha256(logo).hexdigest()
    jpeg_sha = listings.hashlib.sha256(b"jpeg").hexdigest()
    fetch, _ = _commons()
    monkeypatch.setattr(listings, "_http_get", fetch)
    calls = _fake_dfx(monkeypatch, [
        json.dumps([{"path": "harbour-commons/logo.svg", "sha256": logo_sha}]),
        json.dumps({"success": True}),
        json.dumps({"success": True, "revision": 1}),
    ])
    files = "epywb-ryaaa-aaaas-qg7za-cai"
    listings.publish_command(str(spec), "ic", "reg", "dev-identity", files)

    assert [c[0][-1] for c in calls] == ["list_files", "store_file", "listing_publish"]
    stored = json.loads(json.loads(calls[1][1][1:-1]))
    assert stored["namespace"] == "listings" and stored["path"] == "harbour-commons/background.jpg"
    payload = json.loads(json.loads(calls[2][1][1:-1]))
    base = f"https://{files}.icp0.io/listings/harbour-commons"
    assert payload["logo"] == f"{base}/logo.svg?v={logo_sha[:12]}"
    assert payload["background"] == f"{base}/background.jpg?v={jpeg_sha[:12]}"
    assert payload["background_credit"] == "Photo: A. Photographer, CC BY-SA 4.0, Wikimedia Commons"
    assert payload["background_source"] == "https://commons.wikimedia.org/wiki/File:Harbour.jpg"


def test_images_are_skipped_off_the_ic_network(monkeypatch, tmp_path):
    spec = tmp_path / "s.json"
    spec.write_text(json.dumps({"listings": [dict(LISTING, background={"commons": "File:x.jpg"})]}))
    calls = _fake_dfx(monkeypatch, [json.dumps({"success": True, "revision": 1})])
    listings.publish_command(str(spec), "local", "reg", None, "files")
    payload = json.loads(json.loads(calls[0][1][1:-1]))
    assert [c[0][-1] for c in calls] == ["listing_publish"] and "logo" not in payload
