"""A release from a sync source, installed (docs/codices/layered-codex.md §8, §9.2 steps 3 and 4; realms-gos#491), in
process: the sync source's capital is a scripted peer that answers ``legislation_releases`` and ``legislation_chapter``,
a stand-in record is served over HTTPS. The layer is ``arcadia`` (tests/fixtures/legislation) and the realm is ``town``.
tests/deployed/test_release_installs.py runs the same between real realms."""

import hashlib
import json

import fake_chain
import pytest
import world
from core import layered_codex as store
from fake_chain import peer, rejects
from fixtures import legislation as arcadia
from ggg import SourceRecord, SyncSource
from kit import ok
from test_installs import WEB, active_id, chapter_text, failing, install, job, status
from test_installs import change_list as listing

SRC, OTHER = "src", "other"
POINTER = "https://legislation.example.test/arcadia/record.json"
NORTH_TAX = 'from realms.legislation import above\n\n\nclass Tax(above("tax")):\n    pass\n'
TOWN_WATER = 'from realms.legislation import above\n\n\nclass Water(above("water")):\n    pass\n'
BOTH = ("tax", "procedure")


def sha(text):
    return hashlib.sha256(text if isinstance(text, bytes) else text.encode()).hexdigest()


def entry(version, source="arcadia"):
    """Release ``version`` of arcadia as a record lists it: the times are seconds."""
    return {**arcadia.layer(version), "source": source, "published": 5, "effective_from": 6}


NORTH = {"source": "arcadia/north", "version": "1.0.0", "published": 5, "effective_from": 6, "commit": "c" * 40,
         "chapters": {"tax": "sha256:" + sha(NORTH_TAX)}, "calendar": None, "notes": {}}
TEXTS = {"sha256:" + sha(NORTH_TAX): NORTH_TAX}
EAST_TAX = 'from realms.legislation import above\n\n\nclass Tax(above("tax")):\n    pass\n'
EAST = {**NORTH, "source": "arcadia/east", "chapters": {"tax": "sha256:" + sha(EAST_TAX)}}


def text_of(checksum):
    return TEXTS.get(checksum) or arcadia.chapter(checksum).decode()


def capital(*releases, source="src/town", cid=SRC, chapter=None):
    """The capital ``cid``, whose record is ``releases`` and which serves any chapter by its checksum (``chapter``
    answers for it instead)."""
    return peer(cid, legislation_releases={"ok": True, "releases": list(releases), "next": None, "source": source},
                legislation_chapter=chapter or (lambda a: {"ok": True, "text": text_of(a["chapter"])}))


def release_list(version="1.0.0", source="arcadia", domains=BOTH, **kw):
    """The change list that installs ``source``'s release ``version`` whole, with the chapters served by URL as well."""
    chapters = {f"{source}/{d}": chapter_text(d, version) for d in domains}
    kw.setdefault("url", WEB + f"changes/{source}-{version}.json")
    return listing(chapters, realm="town", release={"source": source, "version": version}, **kw)


def north_list(**kw):
    return listing({"arcadia/north/tax": NORTH_TAX.encode()}, realm="town", release={"source": "arcadia/north", "version": "1.0.0"}, **kw)


def run_both_layers():
    """The realm runs arcadia above arcadia/north, from the one record that relays both."""
    capital(entry("1.0.0"), NORTH)
    install(north_list())
    install(release_list(replaces=active_id()))
    assert status()["codex"]["layers"] == ["arcadia", "arcadia/north", "town"]


def link(cid=SRC, status_="active"):
    SyncSource(id=cid, name=cid, status=status_)


@pytest.fixture(autouse=True)
def realm():
    world.build()
    ok("governance_update", "ctl", voting_window=0)
    link()


def installed(seen=None):
    return (seen or status())["codex"]["chapters"]


def codex_id(version="1.0.0"):
    sums = {f"arcadia/{d}": {"sha256": "sha256:" + sha(chapter_text(d, version))} for d in BOTH}
    return store.codex_id(["arcadia", "town"], {"procedure": ["arcadia"], "tax": ["arcadia"]}, sums)


def https_calls():
    return [url for kind, url, _ in fake_chain.state["calls"] if kind == "https"]


def asked(cid, method):
    return [c for c in fake_chain.state["calls"] if c[:2] == (cid, method)]


# ---- the route and who vouches


def test_a_follower_installs_a_relayed_release_from_the_capital_and_its_record_lists_it():
    capital(entry("1.0.0"))
    args = release_list()
    install(args)
    seen = status()
    assert job()["status"] == "done" and seen["codex"]["id"] == codex_id() and seen["codex"]["layers"] == ["arcadia", "town"]
    for chapter in ("arcadia/tax", "arcadia/procedure"):
        row = installed(seen)[chapter]
        assert (row["route"], row["url"], row["vouched_by"], row["version"]) == ("capital", "", "src/town", "1.0.0")
        assert row["fetched_at"] == fake_chain.now() and row["size"] == len(chapter_text(chapter.split("/")[1]))
    assert https_calls() == [args["url"]]  # the change list; no chapter by URL
    assert len(asked(SRC, "legislation_chapter")) == 2 and len(asked(SRC, "legislation_releases")) == 1
    kept = SourceRecord[SRC]
    assert (kept.source, kept.checked_at, [r["version"] for r in kept.releases]) == ("src/town", fake_chain.now(), ["1.0.0"])
    # the realm's own record lists the release as it installed it, under its own source id
    record = ok("legislation_releases", "alice")
    assert record["source"] == "town" and [(r["source"], r["version"]) for r in record["releases"]] == [("arcadia", "1.0.0")]
    assert record["releases"][0]["chapters"] == entry("1.0.0")["chapters"]


def test_the_publishers_stand_in_record_vouches_and_the_chapters_come_by_url():
    text = json.dumps({"source": "arcadia", "releases": [entry("1.0.0"), entry("2.0.0")]})
    fake_chain.serve(POINTER, text.encode())
    SyncSource[SRC].delete()
    SyncSource(id=POINTER, checksum="sha256:" + sha(text), name="Arcadia", status="active")
    install(release_list("2.0.0"))
    row = installed()["arcadia/tax"]
    assert (job()["status"], row["vouched_by"], row["route"], row["version"]) == ("done", "arcadia", "https", "2.0.0")
    assert row["url"] == WEB + "arcadia/tax.py" and asked(SRC, "legislation_chapter") == []
    assert [r["version"] for r in ok("legislation_releases", "alice")["releases"]] == ["2.0.0"]


def test_the_publishers_record_vouches_before_the_record_that_relays_the_layer():
    capital(entry("1.0.0"))
    capital(entry("1.0.0"), entry("2.0.0"), source="arcadia", cid=OTHER)
    link(OTHER)
    install(release_list("1.0.0"))
    row = installed()["arcadia/tax"]
    assert (job()["status"], row["vouched_by"], row["route"]) == ("done", "arcadia", "capital")
    assert asked(OTHER, "legislation_chapter") and not asked(SRC, "legislation_chapter")  # the publisher's capital is asked


def test_a_record_that_relays_two_layers_installs_them_one_release_at_a_time_in_either_order():
    capital(entry("1.0.0"), NORTH)
    install(north_list())
    assert job()["status"] == "done" and status()["codex"]["layers"] == ["arcadia/north", "town"]
    install(release_list(replaces=active_id()))
    seen = status()
    assert job()["status"] == "done" and seen["codex"]["layers"] == ["arcadia", "arcadia/north", "town"]
    assert seen["codex"]["layer_paths"]["tax"] == ["arcadia", "arcadia/north"]
    assert {c["vouched_by"] for c in installed(seen).values()} == {"src/town"}
    assert [(r["source"], r["version"]) for r in ok("legislation_releases", "alice")["releases"]] == [
        ("arcadia", "1.0.0"), ("arcadia/north", "1.0.0")]  # the order of the record that vouches for them


def test_a_newer_release_of_a_layer_replaces_the_one_the_record_lists():
    capital(entry("1.0.0"))
    install(release_list("1.0.0"))
    capital(entry("2.0.0"))
    install(release_list("2.0.0", replaces=active_id()))
    assert job()["status"] == "done" and installed()["arcadia/tax"]["version"] == "2.0.0"
    assert [r["version"] for r in ok("legislation_releases", "alice")["releases"]] == ["2.0.0"]


def test_the_record_that_vouches_for_a_newer_release_is_the_one_that_vouches_for_the_layer_from_then_on():
    capital(entry("1.0.0"))
    install(release_list("1.0.0"))
    assert installed()["arcadia/tax"]["vouched_by"] == "src/town"
    text = json.dumps({"source": "arcadia", "releases": [entry("1.0.0"), entry("2.0.0")]})  # the publisher joins
    fake_chain.serve(POINTER, text.encode())
    SyncSource(id=POINTER, checksum="sha256:" + sha(text), name="Arcadia", status="active")
    capital(entry("1.0.0"))  # the relay still lists 1.0.0: a release the publisher alone lists is not in conflict with it
    SyncSource[SRC].status = "ended"
    install(release_list("2.0.0", replaces=active_id()))
    assert job()["status"] == "done"
    assert {installed()[c]["vouched_by"] for c in ("arcadia/tax", "arcadia/procedure")} == {"arcadia"}


def test_a_sync_source_cannot_reorder_the_layers_the_realm_runs_by_listing_them_the_other_way_round():
    run_both_layers()
    first, fetched = active_id(), len(asked(SRC, "legislation_chapter"))
    capital(NORTH, entry("2.0.0"))  # north above arcadia, where the realm runs arcadia above north
    failing(release_list("2.0.0", replaces=first),
            "the record of src/town puts arcadia/north above arcadia, and this realm runs arcadia above arcadia/north")
    assert active_id() == first and status()["codex"]["layers"] == ["arcadia", "arcadia/north", "town"]
    assert len(asked(SRC, "legislation_chapter")) == fetched  # refused before any chapter was fetched


def test_a_release_of_a_layer_the_realm_runs_keeps_the_order_the_realm_runs_the_layers_in():
    run_both_layers()
    capital(entry("2.0.0"), NORTH)
    install(release_list("2.0.0", replaces=active_id()))
    seen = status()
    assert job()["status"] == "done" and seen["codex"]["layers"] == ["arcadia", "arcadia/north", "town"]
    assert seen["codex"]["layer_paths"]["tax"] == ["arcadia", "arcadia/north"]
    assert (installed(seen)["arcadia/tax"]["version"], installed(seen)["arcadia/north/tax"]["version"]) == ("2.0.0", "1.0.0")
    assert [(r["source"], r["version"]) for r in ok("legislation_releases", "alice")["releases"]] == [
        ("arcadia", "2.0.0"), ("arcadia/north", "1.0.0")]


def test_a_layer_whose_link_ended_is_not_reordered_when_a_related_layer_is_released_from_another_record():
    run_both_layers()
    SyncSource[SRC].status = "ended"
    text = json.dumps({"source": "arcadia/east", "releases": [entry("1.0.0"), EAST]})  # the publisher of east
    fake_chain.serve(POINTER, text.encode())
    SyncSource(id=POINTER, checksum="sha256:" + sha(text), name="East", status="active")
    install(listing({"arcadia/east/tax": EAST_TAX.encode()}, realm="town", replaces=active_id(),
                    release={"source": "arcadia/east", "version": "1.0.0"}, url=WEB + "changes/east.json"))
    seen = status()
    assert job()["status"] == "done" and seen["codex"]["layers"] == ["arcadia", "arcadia/east", "arcadia/north", "town"]
    assert seen["codex"]["layer_paths"]["tax"] == ["arcadia", "arcadia/east", "arcadia/north"]
    assert {c: installed(seen)[c]["vouched_by"] for c in ("arcadia/tax", "arcadia/east/tax", "arcadia/north/tax")} == {
        "arcadia/tax": "src/town", "arcadia/east/tax": "arcadia/east", "arcadia/north/tax": "src/town"}


def test_a_new_layer_that_its_record_does_not_place_among_the_layers_the_realm_runs_is_refused():
    run_both_layers()
    SyncSource[SRC].status = "ended"
    text = json.dumps({"source": "arcadia/east", "releases": [EAST]})  # relays nothing the realm runs
    fake_chain.serve(POINTER, text.encode())
    SyncSource(id=POINTER, checksum="sha256:" + sha(text), name="East", status="active")
    failing(listing({"arcadia/east/tax": EAST_TAX.encode()}, realm="town", replaces=active_id(),
                    release={"source": "arcadia/east", "version": "1.0.0"}, url=WEB + "changes/east.json"),
            "the record of arcadia/east lists arcadia/east, and none of the layers this realm runs")


def test_the_layers_from_above_stay_as_installed_when_the_realm_changes_its_own_layer_and_no_record_is_read():
    capital(entry("1.0.0"))
    install(release_list("1.0.0"))
    before = installed()
    capital(entry("2.0.0"))  # the record moves on; the realm has not installed it
    reads = len(fake_chain.state["calls"])
    install(listing({"town/water": TOWN_WATER.encode()}, realm="town", replaces=active_id(), url=WEB + "changes/water.json"))
    seen = status()
    assert job()["status"] == "done" and seen["codex"]["layers"] == ["arcadia", "town"]
    assert {c: before[c] for c in before} == {c: installed(seen)[c] for c in before}  # source, version, voucher, route, time
    assert seen["codex"]["layer_paths"]["water"] == ["town"] and installed(seen)["town/water"]["vouched_by"] == "town"
    assert [c for c in fake_chain.state["calls"][reads:] if c[0] == SRC] == []  # nobody was asked


def test_a_layer_whose_link_has_ended_stays_across_later_installs():
    capital(entry("1.0.0"))
    install(release_list("1.0.0"))
    SyncSource[SRC].status = "ended"
    fake_chain.state["peers"].clear()
    install(listing({"town/water": TOWN_WATER.encode()}, realm="town", replaces=active_id(), url=WEB + "changes/water.json"))
    seen = status()
    assert job()["status"] == "done" and seen["codex"]["layers"] == ["arcadia", "town"]
    assert installed(seen)["arcadia/tax"]["vouched_by"] == "src/town" and installed(seen)["arcadia/tax"]["route"] == "capital"


# ---- what the record that vouches does not cover


def test_a_release_no_sync_source_lists_is_refused_and_the_old_codex_keeps_running():
    capital(entry("1.0.0"))
    install(release_list("1.0.0"))
    first = active_id()
    failing(release_list("2.0.0", replaces=first), "relays arcadia 1.0.0, not 2.0.0")
    failing(release_list("1.0.0", source="arcadia/south", replaces=first, url=WEB + "changes/south.json"),
            "no sync source relays arcadia/south")
    assert active_id() == first


def test_a_change_list_whose_chapters_are_not_the_ones_the_record_names_is_refused_before_anything_is_fetched():
    capital(entry("1.0.0"))
    chapters = {"arcadia/tax": chapter_text("tax", "2.0.0"), "arcadia/procedure": chapter_text("procedure")}
    args = listing(chapters, realm="town", release={"source": "arcadia", "version": "1.0.0"})
    failing(args, "arcadia/tax: the record says sha256:")
    assert asked(SRC, "legislation_chapter") == [] and https_calls() == [args["url"]]
    assert active_id() is None


def test_a_partial_release_is_refused():
    capital(entry("1.0.0"))
    failing(release_list(domains=("tax",)), "a release is installed whole: arcadia 1.0.0 is arcadia/procedure, arcadia/tax")
    assert asked(SRC, "legislation_chapter") == []


def test_two_sync_sources_that_relay_different_releases_of_a_layer_are_refused():
    capital(entry("1.0.0"))
    capital(entry("2.0.0"), cid=OTHER, source="other/town")
    link(OTHER)
    failing(release_list("1.0.0"), "relay different releases (1.0.0 and 2.0.0)")
    assert active_id() is None and asked(SRC, "legislation_chapter") == []
    assert SourceRecord.count() == 2  # both were read, and kept


def test_a_stand_in_record_whose_bytes_are_not_its_pin_is_refused():
    text = json.dumps({"source": "arcadia", "releases": [entry("1.0.0")]})
    fake_chain.serve(POINTER, text.encode() + b" ")
    SyncSource(id=POINTER, checksum="sha256:" + sha(text), name="Arcadia", status="active")
    SyncSource[SRC].status = "ended"
    failing(release_list("1.0.0"), f"{POINTER}: the record is sha256:{sha(text + ' ')}, the pin is sha256:{sha(text)}")
    assert SourceRecord.count() == 0 and active_id() is None


def test_a_sync_source_that_cannot_be_read_fails_the_install():
    peer(SRC, legislation_releases=rejects("Canister is stopped"))
    failing(release_list("1.0.0"), "src: legislation_releases on src failed: Canister is stopped")
    peer(SRC, legislation_releases=["not", "an", "object"])
    failing(release_list("1.0.0", url=WEB + "changes/third.json"), "src: no reply")
    capital(source=None)
    failing(release_list("1.0.0", url=WEB + "changes/second.json"), "no sync source relays arcadia")


# ---- the capital serves other bytes, or none


def appended(a):
    """The chapter with a line more: not the size the change list gives."""
    return {"ok": True, "text": text_of(a["chapter"]) + "# altered\n"}


def swapped(a):
    """The chapter with a letter changed: the size is right and the checksum is not."""
    return {"ok": True, "text": text_of(a["chapter"]).replace("pct(", "pcx(")}


def test_a_capital_that_serves_other_bytes_is_noted_and_the_urls_are_tried():
    capital(entry("1.0.0"), chapter=lambda a: appended(a) if a["chapter"].endswith(sha(chapter_text("tax"))) else {
        "ok": True, "text": text_of(a["chapter"])})
    install(release_list())
    seen = status()
    assert job()["status"] == "done" and seen["codex"]["id"] == codex_id()  # the same checksum, whichever the route
    assert [installed(seen)[c]["route"] for c in ("arcadia/tax", "arcadia/procedure")] == ["https", "capital"]
    noted = [e for e in ok("events", "ctl")["events"] if e["op"] == "install_chapter_refused"]
    assert len(noted) == 1 and noted[0]["args"] == {"source": SRC, "chapter": "arcadia/tax", "sha256": "sha256:" + sha(chapter_text("tax"))}
    assert noted[0]["by"] == SRC and "bytes, the change list says" in noted[0]["result"]["error"]


def test_a_capital_that_serves_the_right_size_and_the_wrong_bytes_is_noted_too():
    capital(entry("1.0.0"), chapter=swapped)
    install(release_list())
    assert job()["status"] == "done"
    noted = [e for e in ok("events", "ctl")["events"] if e["op"] == "install_chapter_refused"]
    assert [e["args"]["chapter"] for e in noted] == ["arcadia/tax"] and "the text is sha256:" in noted[0]["result"]["error"]


def test_a_source_that_alters_a_chapter_everywhere_fails_the_install_and_the_old_codex_keeps_running():
    capital(entry("1.0.0"))
    install(release_list("1.0.0"))
    first = active_id()
    capital(entry("2.0.0"), chapter=swapped)
    args = release_list("2.0.0", replaces=first)
    fake_chain.serve(WEB + "arcadia/tax.py", chapter_text("tax", "2.0.0").replace(b"pct(", b"pcx("))
    failing(args, "arcadia/tax: src: the text is sha256:")
    assert f"{WEB}arcadia/tax.py: the text is sha256:" in job()["error"]  # the capital's bytes, then the URL's
    assert active_id() == first and status()["pending"] is None


def test_a_capital_that_is_stopped_or_has_not_the_chapter_leaves_the_urls_without_a_note():
    capital(entry("1.0.0"), chapter=rejects("Canister is stopped"))
    install(release_list())
    assert [installed()[c]["route"] for c in ("arcadia/tax", "arcadia/procedure")] == ["https", "https"]
    assert status()["codex"]["id"] == codex_id()
    capital(entry("2.0.0"), chapter={"ok": False, "kind": "not_found", "error": "unknown ChapterSource"})
    install(release_list("2.0.0", replaces=active_id()))
    assert job()["status"] == "done" and installed()["arcadia/tax"]["route"] == "https"
    assert not [e for e in ok("events", "ctl")["events"] if e["op"] == "install_chapter_refused"]


def test_a_chapter_the_realm_stores_already_is_not_asked_for_again():
    capital(entry("1.0.0"))
    install(release_list("1.0.0"))
    capital(entry("2.0.0"))
    install(release_list("2.0.0", replaces=active_id()))
    assert len(asked(SRC, "legislation_chapter")) == 3  # tax twice (two versions) and procedure once: it did not change
