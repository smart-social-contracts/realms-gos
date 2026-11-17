"""The records of the sync sources, read (docs/codices/layered-codex.md §8; core.records): a realm's by its capital's
``legislation_releases``, a stand-in record by HTTPS, pinned by the checksum of its bytes. The capital is a scripted
peer. tests/deployed/test_release_installs.py reads records between real realms."""

import hashlib
import json

import fake_chain
import pytest
import world
from core import records
from core import op
from core.db import Fail
from fake_chain import peer, rejects
from ggg import SourceRecord, SyncSource

SRC = "src"
URL = "https://legislation.example.test/eu/record.json"
SUM = "sha256:" + "a" * 64


def entry(source="eu", version="1.0.0", **over):
    return {"source": source, "version": version, "published": 5, "effective_from": 6, "commit": "c" * 40,
            "chapters": {"tax": SUM}, "calendar": None, "notes": {"en": "First."}, **over}


def capital(*releases, source="eu/es"):
    """A capital that answers ``legislation_releases`` a page at a time, as links.legislation_releases does."""
    def answer(args):
        start, count = args["from"], args["count"]
        page = list(releases[start - 1:start - 1 + count])
        end = start - 1 + count
        return {"ok": True, "releases": page, "next": end + 1 if end < len(releases) else None, "source": source}
    return peer(SRC, legislation_releases=answer)


def read(key):
    return op.run(records.read_record(key))


def failing(key, reason):
    with pytest.raises(Fail, match=reason):
        read(key)


def stand_in(text, pin=None, url=URL):
    body = text if isinstance(text, bytes) else text.encode()
    fake_chain.serve(url, body)
    SyncSource(id=url, checksum=pin or "sha256:" + hashlib.sha256(body).hexdigest(), name="EU", status="active")


@pytest.fixture(autouse=True)
def realm():
    world.build()
    SyncSource(id=SRC, name="The source", status="active")


def test_a_realms_record_is_read_checked_and_kept_with_its_time():
    capital(entry("eu"), entry("eu/es", "2.4.0"), entry("eu/es", "2.5.0"))
    found = read(SRC)
    assert found["source"] == "eu/es" and found["checked_at"] == fake_chain.now()
    assert [(r["source"], r["version"]) for r in found["releases"]] == [("eu", "1.0.0"), ("eu/es", "2.4.0"), ("eu/es", "2.5.0")]
    row = SourceRecord[SRC]
    assert (row.source, row.checked_at, row.releases) == ("eu/es", fake_chain.now(), found["releases"])
    fake_chain.state["now"] += 60
    capital(entry("eu"))
    read(SRC)
    assert SourceRecord.count() == 1 and SourceRecord[SRC].checked_at == fake_chain.now()
    assert len(SourceRecord[SRC].releases) == 1


def test_a_record_longer_than_a_page_is_read_page_by_page():
    pages = capital(*[entry("eu/es", f"1.{n}.0") for n in range(250)])
    assert len(read(SRC)["releases"]) == 250
    assert [a["from"] for a in pages.sent("legislation_releases")] == [1, 101, 201]


def test_a_record_of_more_than_a_thousand_releases_is_refused():
    capital(*[entry("eu/es", f"1.{n}.0") for n in range(1001)])
    failing(SRC, "lists at most 1000 releases")


def test_a_capital_that_never_ends_its_pages_is_given_up_on():
    peer(SRC, legislation_releases={"ok": True, "releases": [], "next": 1, "source": "eu/es"})
    failing(SRC, "lists at most 1000 releases")


def test_a_realm_without_a_layer_of_its_own_has_an_empty_record():
    capital(source=None)
    found = read(SRC)
    assert found["source"] is None and found["releases"] == []


@pytest.mark.parametrize("releases, source, reason", [
    ([entry(chapters={"tax": "sha256:short"})], "eu/es", "eu/tax is sha256:<64 hex digits>"),
    ([entry(chapters={"calendar": SUM})], "eu/es", "lists its calendar as calendar"),
    ([entry(chapters={"nonsense": SUM})], "eu/es", "is not a GGG domain"),
    ([entry(calendar="sha256:short")], "eu/es", "calendar is sha256"),
    ([entry(version="")], "eu/es", "version is 1 to 64 characters"),
    ([entry(commit=7)], "eu/es", "commit is 1 to 64 characters"),
    ([entry(version="1.0@2")], "eu/es", "hold no '@'"),
    ([entry(published="2026-01-15")], "eu/es", "published is a time in seconds"),
    ([entry(effective_from=-1)], "eu/es", "effective_from is a time in seconds"),
    ([entry(published=2**64)], "eu/es", "published is a time in seconds"),
    ([entry(effective_from=records.TIME_MAX + 1)], "eu/es", "effective_from is a time in seconds"),
    ([entry(published=True)], "eu/es", "published is a time in seconds"),
    ([entry(notes=["x"])], "eu/es", "notes are at most"),
    ([entry(source="EU")], "eu/es", "is not a source id"),
    ([{**entry(), "extra": 1}], "eu/es", "a release has calendar, chapters"),
    (["eu"], "eu/es", "a release has"),
    ([entry(chapters={f"d{n}": SUM for n in range(65)})], "eu/es", "lists at most 64 chapters"),
    ([entry("eu/es", "1"), entry("eu", "2")], "eu/es", "lists its own layer last"),
    ([entry("eu"), entry("eu", "2")], "eu/es", "relayed twice"),
    ([entry("eu/es", "1"), entry("eu/es", "1")], "eu/es", "relayed twice, or a version of its own layer twice"),
    ([entry("eu")], None, "names the source id of its own layer"),
    ([entry("eu")], "EU/ES", "is not a source id"),
])
def test_what_a_record_must_look_like(releases, source, reason):
    capital(*releases, source=source)
    failing(SRC, reason)


def test_the_latest_time_a_record_may_give_is_the_latest_the_typed_methods_encode():
    import ggg_api

    assert ggg_api.to_ggg(records.TIME_MAX, "Timestamp") < 2**64 <= ggg_api.to_ggg(records.TIME_MAX + 1, "Timestamp")
    capital(entry("eu/es", published=records.TIME_MAX, effective_from=records.TIME_MAX))
    assert read(SRC)["releases"][0]["published"] == records.TIME_MAX


def test_a_record_above_the_size_the_realm_keeps_is_refused_after_the_page_that_passes_it(monkeypatch):
    monkeypatch.setattr(records, "RECORD_MAX", 2048)
    pages = capital(*[entry("eu/es", f"1.{n}.0") for n in range(250)])
    failing(SRC, "a record is at most 2 KB")
    assert len(pages.sent("legislation_releases")) == 1  # the first page of 100 passed it: no more were asked for


def test_a_record_above_the_size_the_realm_keeps_is_refused(monkeypatch):
    monkeypatch.setattr(records, "RECORD_MAX", 2048)
    capital(*[entry("eu/es", f"1.{n}.0") for n in range(20)])
    failing(SRC, "a record is at most 2 KB")


def test_a_capital_that_is_stopped_or_refuses_is_a_failure_naming_it():
    peer(SRC, legislation_releases=rejects("Canister is stopped"))
    failing(SRC, "src: legislation_releases on src failed: Canister is stopped")
    peer(SRC, legislation_releases={"ok": False, "error": "no such record", "kind": "forbidden"})
    failing(SRC, "src: no such record")
    peer(SRC, legislation_releases={"ok": True, "releases": "none", "next": None})
    failing(SRC, "answers its releases and where the next page starts")
    assert SourceRecord.count() == 0


def test_only_an_active_sync_source_is_read():
    SyncSource[SRC].status = "requested"
    failing(SRC, "is not an active sync source")
    failing("nobody", "is not an active sync source")


def test_a_stand_in_record_is_read_when_its_bytes_are_the_pin():
    text = json.dumps({"source": "eu", "releases": [entry("eu", "1.0.0"), entry("eu", "1.1.0")]})
    stand_in(text)
    found = read(URL)
    assert found["source"] == "eu" and [r["version"] for r in found["releases"]] == ["1.0.0", "1.1.0"]
    assert SourceRecord[URL].releases == found["releases"]
    assert [call for call in fake_chain.state["calls"] if call[0] == "https"] == [("https", URL, records.RECORD_MAX + 8192)]


def test_a_stand_in_record_whose_bytes_are_not_its_pin_is_refused():
    text = json.dumps({"source": "eu", "releases": [entry("eu")]})
    stand_in(text, pin="sha256:" + "0" * 64)
    failing(URL, f"{URL}: the record is sha256:[0-9a-f]+, the pin is sha256:0+")
    assert SourceRecord.count() == 0


@pytest.mark.parametrize("text, reason", [
    ("not json", "the record is not JSON"),
    ('{"source": "eu"}', "a stand-in record is"),
    ('{"source": "eu", "releases": [], "x": 1}', "a stand-in record is"),
    ('{"source": "eu", "source": "es", "releases": []}', "the record at https://.* repeats the key 'source'"),
    ("[" * 100000, "the record is not JSON"),
    ('{"source": "eu", "releases": {}}', "at most 1000 releases"),
])
def test_what_a_stand_in_record_must_look_like(text, reason):
    stand_in(text)
    failing(URL, reason)


def test_a_stand_in_record_nobody_serves_or_above_the_limit_is_a_failure_naming_its_url():
    SyncSource(id=URL, checksum=SUM, name="EU", status="active")
    failing(URL, f"{URL}: HTTP 404")
    fake_chain.serve(URL, b"x" * (records.RECORD_MAX + 1))
    failing(URL, f"{URL}: {records.RECORD_MAX + 1} bytes is above 512 KB")
    fake_chain.serve(URL, fake_chain.rejects("connection refused"))
    failing(URL, "connection refused")


def test_every_active_sync_source_is_read_in_the_order_of_the_links():
    capital(entry("eu"), source="eu/es")
    text = json.dumps({"source": "eu", "releases": [entry("eu")]})
    stand_in(text)
    SyncSource(id="gone", name="Gone", status="ended")
    found = op.run(records.read_active_records())
    assert [r["key"] for r in found] == [SRC, URL]
