"""A release from a sync source, installed on the real wasm (docs/codices/layered-codex.md §8, §9.2 steps 3 and 4,
realms-gos#491): realms of one wasm on one PocketIC instance. ``source`` relays a release of ``arcadia`` (tests/fixtures/
legislation) and publishes a layer of its own; ``town`` links to it and installs the release from its capital, ``behind``
from the chapter URLs when the capital cannot serve them, ``plain`` from a stand-in record. They refuse what the record
that vouches for a release does not cover. tests/ggg/test_release_installs.py runs the same rules in process.

The realms are built once for the module and the tests below use them in order.
"""

import hashlib
import json

import pytest
from fixtures import legislation as arcadia
from network import Network
from test_installs import WEB, Site, active_id, chapter_text, failing, install, status
from test_sync_sources import RELAY, STORE

STAND_IN = "https://legislation.example.test/arcadia/record.json"
OWN_TAX = "town tax 1.0.0"
BOTH = ("tax", "procedure")
FORGET = "import ggg\nggg.ChapterSource[%(sha)r].delete()"
ALTER = "import ggg\nrow = ggg.ChapterSource[%(sha)r]\nrow.source = row.source.replace('pct(', 'pcx(')"
PUBLISH = {"version": "1.0.0", "effective_from": 1_900_000_000, "commit": "c" * 40,
           "chapters": [{"domain": "tax", "checksum": "sha256:" + hashlib.sha256(OWN_TAX.encode()).hexdigest()}]}


def entry(version):
    """Release ``version`` of arcadia as a record lists it: the times are seconds."""
    return {**arcadia.layer(version), "published": 5, "effective_from": 6}


def release_args(site, version, **kw):
    chapters = {f"arcadia/{d}": chapter_text(d, version) for d in BOTH}
    return site.change_list(chapters, realm="town", release={"source": "arcadia", "version": version}, **kw)


def relay(realm, version, order):
    """The record of ``realm`` lists release ``version`` of arcadia, as an install vouched for by the record of its own
    sync source would have left it, and the realm stores its chapters."""
    texts = [chapter_text(d, version).decode() for d in BOTH]
    out = realm.shell(STORE % {"texts": texts} + RELAY % {"release": entry(version), "order": order})
    assert "Traceback" not in out, out


def routes(realm):
    """``{chapter: (route, vouched_by, version)}`` of the arcadia chapters the realm runs."""
    chapters = status(realm)["codex"]["chapters"]
    return {c: (v["route"], v["vouched_by"], v["version"]) for c, v in chapters.items() if c.startswith("arcadia/")}


def refusals(realm):
    """The chapters a capital served with other bytes than its record names, as the audit log noted them."""
    return [e for e in realm.ok("events", "ctl", query=True)["events"] if e["op"] == "install_chapter_refused"]


def link(follower, *sources):
    for source in sources:
        follower.govern("sync_source_add", {"source": {"Realm": source.cid}, "name": source.name})
        source.govern("follower_accept", {"follower": follower.cid})


def source_with(net, name, version, own):
    """A realm that relays release ``version`` of arcadia and publishes a layer of its own, ``own``."""
    realm = net.realm(name)
    relay(realm, version, ["arcadia", own])
    realm.shell(STORE % {"texts": [OWN_TAX]})
    realm.govern("legislation_release_publish", {"source": own, **PUBLISH})
    return realm


@pytest.fixture(scope="module")
def net(wasm_of, people):
    return Network(wasm_of("production"), people)


@pytest.fixture(scope="module")
def site(net):
    return Site(net.web)


@pytest.fixture(scope="module")
def source(net):
    return source_with(net, "source", "1.0.0", "x/source")


@pytest.fixture(scope="module")
def other(net):
    return source_with(net, "other", "2.0.0", "x/other")


@pytest.fixture(scope="module")
def town(net):
    return net.realm("town")


def test_a_follower_installs_a_relayed_release_from_the_capital_and_reports_who_vouched_and_the_route(
        net, source, town, site):
    link(town, source)
    args = release_args(site, "1.0.0")
    asked = len(net.web.asked)
    job = install(net, town, args)
    assert (job["status"], job["error"]) == ("done", "")
    seen = status(town)
    assert seen["codex"]["layers"] == ["arcadia", "town"] and seen["codex"]["status"] == "active"
    assert routes(town) == {"arcadia/tax": ("capital", "x/source", "1.0.0"), "arcadia/procedure": ("capital", "x/source", "1.0.0")}
    assert [url for _, url in net.web.asked[asked:]] == [args["url"]]  # the change list, and no chapter by URL
    record = town.ok("legislation_releases", None, query=True)
    assert record["source"] == "town" and [(r["source"], r["version"]) for r in record["releases"]] == [("arcadia", "1.0.0")]
    assert record["releases"][0]["chapters"] == entry("1.0.0")["chapters"]


def test_the_urls_give_the_same_checksum_when_the_capital_has_not_the_chapter(net, source, town, site):
    behind = net.realm("behind")
    link(behind, source)
    tax = entry("1.0.0")["chapters"]["tax"]
    source.shell(FORGET % {"sha": tax})
    job = install(net, behind, release_args(site, "1.0.0"))
    assert (job["status"], job["error"]) == ("done", "")
    assert routes(behind) == {"arcadia/tax": ("https", "x/source", "1.0.0"), "arcadia/procedure": ("capital", "x/source", "1.0.0")}
    assert status(behind)["codex"]["id"] == status(town)["codex"]["id"]  # the legislation is the same, whichever the route
    assert refusals(behind) == []  # a capital that has not the chapter is no capital that serves other bytes
    source.shell(STORE % {"texts": [chapter_text("tax").decode()]})


def test_a_release_the_record_does_not_cover_is_refused_and_the_old_codex_keeps_running(net, source, town, site):
    first = active_id(town)
    failing(net, town, site.change_list({f"arcadia/{d}": chapter_text(d, "2.0.0") for d in BOTH}, realm="town",
                                        replaces=first, release={"source": "arcadia", "version": "3.0.0"}),
            "relays arcadia 1.0.0, not 3.0.0")
    mixed = {"arcadia/tax": chapter_text("tax", "2.0.0"), "arcadia/procedure": chapter_text("procedure")}
    failing(net, town, site.change_list(mixed, realm="town", replaces=first, release={"source": "arcadia", "version": "1.0.0"}),
            "arcadia/tax: the record says " + entry("1.0.0")["chapters"]["tax"])
    failing(net, town, site.change_list({"arcadia/tax": chapter_text("tax")}, realm="town", replaces=first,
                                        release={"source": "arcadia", "version": "1.0.0"}),
            "a release is installed whole: arcadia 1.0.0 is arcadia/procedure, arcadia/tax")
    failing(net, town, site.change_list({"arcadia/south/tax": chapter_text("tax")}, realm="town", replaces=first,
                                        release={"source": "arcadia/south", "version": "1.0.0"}),
            "no sync source relays arcadia/south")


def test_two_sync_sources_that_relay_different_releases_of_a_layer_are_refused(net, source, other, town, site):
    link(town, other)  # source relays arcadia 1.0.0, other 2.0.0
    first = active_id(town)
    job = failing(net, town, release_args(site, "2.0.0", replaces=first), "relay different releases (1.0.0 and 2.0.0)")
    assert job["step"] == "fetch_chapters"  # before any chapter was fetched


def test_a_source_that_alters_a_chapter_fails_the_install_and_the_old_codex_keeps_running(net, source, other, town, site):
    first = active_id(town)
    relay(source, "2.0.0", ["arcadia", "x/source"])  # both sync sources relay 2.0.0 now
    source.shell(ALTER % {"sha": entry("2.0.0")["chapters"]["tax"]})
    args = release_args(site, "2.0.0", replaces=first)
    net.web.serve(WEB + "arcadia/tax.py", chapter_text("tax", "2.0.0").replace(b"pct(", b"pcx("))  # a mirror altered too
    failing(net, town, args, "arcadia/tax: ")
    assert active_id(town) == first
    assert len(refusals(town)) == 1 and refusals(town)[0]["by"] == source.cid
    assert refusals(town)[0]["args"] == {"source": source.cid, "chapter": "arcadia/tax", "sha256": entry("2.0.0")["chapters"]["tax"]}

    net.web.serve(WEB + "arcadia/tax.py", chapter_text("tax", "2.0.0"))  # the URL serves the right bytes: the install goes on
    job = install(net, town, args)
    assert (job["status"], job["error"]) == ("done", "")
    assert routes(town) == {"arcadia/tax": ("https", "x/source", "2.0.0"), "arcadia/procedure": ("capital", "x/source", "2.0.0")}
    assert len(refusals(town)) == 2  # noted again, this time before the fallback that worked
    assert [(r["source"], r["version"]) for r in town.ok("legislation_releases", None, query=True)["releases"]] == [("arcadia", "2.0.0")]


def test_a_stand_in_record_whose_bytes_are_not_its_pin_is_refused_and_pinned_again_it_vouches(net, site):
    plain = net.realm("plain")
    record = json.dumps({"source": "arcadia", "releases": [entry("1.0.0"), entry("2.0.0")]}).encode()
    pin = "sha256:" + hashlib.sha256(record).hexdigest()
    net.web.serve(STAND_IN, record + b" ")
    plain.govern("sync_source_add", {"source": {"Record": {"url": STAND_IN, "checksum": pin}}, "name": "Arcadia"})
    job = failing(net, plain, release_args(site, "1.0.0"), f"{STAND_IN}: the record is sha256:")
    assert f"the pin is {pin}" in job["error"] and job["step"] == "fetch_chapters"

    net.web.serve(STAND_IN, record)
    job = install(net, plain, release_args(site, "1.0.0"))
    assert (job["status"], job["error"]) == ("done", "")
    assert routes(plain) == {"arcadia/tax": ("https", "arcadia", "1.0.0"), "arcadia/procedure": ("https", "arcadia", "1.0.0")}
    assert [(r["source"], r["version"]) for r in plain.ok("legislation_releases", None, query=True)["releases"]] == [("arcadia", "1.0.0")]


def test_an_upgrade_in_the_middle_of_a_later_install_changes_nothing_in_the_layers_from_above(net, town, site):
    first = active_id(town)
    args = site.change_list({"town/water": b'from realms.legislation import above\n\n\nclass Water(above("water")):\n    pass\n'},
                            realm="town", replaces=first)
    town.govern("codex_install", args)
    net.upgrade_when(town, lambda: (status(town)["job"] or {}).get("step") == "fetch_chapters", rounds=1)
    job = status(town)["job"]
    assert job["status"] == "paused"
    town.ok("codex_install_resume", "ctl", job=job["id"])
    net.wait_until(lambda: status(town)["job"]["status"] in ("done", "failed"), rounds=2)
    assert status(town)["job"]["status"] == "done"
    assert routes(town) == {"arcadia/tax": ("https", "x/source", "2.0.0"), "arcadia/procedure": ("capital", "x/source", "2.0.0")}


# ---- costs: reading a record, run by hand in a realm of its own

RECORD = '''
domains = ("tax", "procedure", "register", "local_regime", "water", "data_protection", "language", "treasury", "federation")
releases = [{"source": "eu/es", "version": f"1.{n}.0", "published": n, "effective_from": n, "commit": "c" * 40,
             "chapters": {d: "sha256:" + f"{n % 256:02x}" * 32 for d in domains}, "calendar": None,
             "notes": {"en": "x" * 200}} for n in range(COUNT)]
'''


@pytest.fixture(scope="module")
def lab(net):
    return net.realm("lab", members=("alice",))


@pytest.mark.parametrize("count", [60, 400])
def test_what_reading_a_record_costs(lab, costs, count):
    """The checks and the row of a record of ``count`` releases (about 1.2 KB each); the calls that fetch it are not
    counted, a message's instructions end at each await. The first check also compiles the library (about 0.6 billion,
    once per upgrade), which step 2 of an install has paid already."""
    out = lab.shell(RECORD.replace("COUNT", str(count)))
    assert "Traceback" not in out, out
    size = int(lab.shell("import json\nprint(len(json.dumps(releases)))").strip().splitlines()[-1])
    label = f"record of {count} releases ({size // 1024} KB)"
    costs.measure(lab, f"{label}: check", "from api import links\nrecord = links.checked_record('k', 'eu/es', releases)")
    costs.measure(lab, f"{label}: keep (write the row)", "links.keep_record(record)")
    costs.measure(lab, f"{label}: read the row back", "import ggg\nlen(ggg.SourceRecord['k'].releases)")
    costs.measure(lab, f"{label}: cut to the layers the new codex has",
                  "from core import change_list\nchange_list.relays_for([], [record], {'source': 'eu/es', 'version': '1.0.0'})")
