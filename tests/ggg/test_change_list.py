"""A change list (docs/codices/layered-codex.md §7) and the records that vouch for it (§8): the rules of
core.change_list on plain data, as the install task's steps 2 and 4 apply them."""

import json
import pathlib

import pytest
from core import change_list
from core.db import Fail

ROOT = pathlib.Path(__file__).resolve().parents[2]
SHA = {name: "sha256:" + hex(10 + n)[2:].rjust(64, "0") for n, name in enumerate(
    ("tax", "procedure", "local_regime", "water", "realm", "calendar", "other", "codex"))}
URL = "https://legislation.example.test/"


def entry(chapter, sha256=None, size=100, mirrors=()):
    return {"chapter": chapter, "url": URL + chapter + ".py", "mirrors": list(mirrors),
            "sha256": (sha256 or SHA.get(chapter.rpartition("/")[2], SHA["other"]))[7:], "size": size}


def text(realm="town", replaces=None, set=(), remove=(), release=None, **more):
    return json.dumps({"kind": change_list.KIND, "format": change_list.FORMAT, "realm": realm, "replaces": replaces,
                       "set": list(set), "remove": list(remove), "release": release, **more})


def refused(fn, *args, match, **kw):
    with pytest.raises(Fail, match=match):
        fn(*args, **kw)


# ---- parse


def test_a_change_list_is_parsed_into_chapters_with_their_source_domain_urls_and_checksum():
    parsed = change_list.parse(text(set=[entry("town/tax", mirrors=[URL + "mirror/tax.py"])], summary={"en": "x"}))
    assert parsed["realm"] == "town" and parsed["replaces"] is None and parsed["summary"] == {"en": "x"}
    assert parsed["set"] == [{"chapter": "town/tax", "source": "town", "domain": "tax", "sha256": SHA["tax"],
                              "urls": [URL + "town/tax.py", URL + "mirror/tax.py"], "size": 100}]
    calendar = change_list.parse(text(set=[entry("eu/es/calendar")], release={"source": "eu/es", "version": "1"}))
    assert calendar["set"][0]["domain"] == "calendar" and calendar["release"] == {"source": "eu/es", "version": "1"}


@pytest.mark.parametrize("bad, match", [
    ("not json", "not JSON"),
    (json.dumps({"kind": "other", "format": 1}), "not {\"kind\""),
    (json.dumps({"kind": change_list.KIND, "format": 2, "realm": "t"}), "not {\"kind\""),
    (text(realm=""), "realm names"),
    (text(replaces=5), "replaces is"),
    (text(set=[entry(f"town/tax", mirrors=[]) for _ in range(51)]), "at most 50 chapters"),
    (text(set=[entry("town/tax"), entry("town/tax")]), "twice"),
    (text(set=[{**entry("town/tax"), "url": "http://x"}]), "over https"),
    (text(set=[{**entry("town/tax"), "mirrors": ["ftp://x"]}]), "over https"),
    (text(set=[{**entry("town/tax"), "sha256": "sha256:" + "0" * 64}]), "64 hex digits"),
    (text(set=[{**entry("town/tax"), "sha256": "0" * 63}]), "64 hex digits"),
    (text(set=[entry("town/tax", size=0)]), "size is between"),
    (text(set=[entry("town/tax", size=256 * 1024 + 1)]), "size is between"),
    (text(set=[entry("town/parking")]), "not a GGG domain"),
    (text(set=[entry("Town/tax")]), "not a chapter id"),
    (text(set=[entry("tax")]), "not a chapter id"),
    (text().replace('"remove": []', '"remove": "town/tax"'), "remove is a list"),
    (text(release={"source": "eu/es"}), "release is"),
    (text(), "changes nothing"),
])
def test_what_parse_refuses(bad, match):
    refused(change_list.parse, bad, match=match)


def test_the_domain_list_is_gggs():
    listed = json.load(open(ROOT / "ggg" / "domains.json"))["domains"]
    assert tuple(d["id"] for d in listed) == change_list.DOMAINS


# ---- apply (step 2)

OWN = {"chapters": {"tax": SHA["tax"], "procedure": SHA["procedure"]}, "calendar": SHA["calendar"]}


def applied(**kw):
    kw.setdefault("realm", "town")
    return change_list.apply(change_list.parse(text(**kw)), "town", SHA["codex"], OWN)


def test_the_first_install_sets_the_realms_source_id_and_every_later_one_must_name_it():
    first = change_list.parse(text(realm="eu/es/vc/46250-valencia", set=[entry("eu/es/vc/46250-valencia/tax")]))
    realm, own = change_list.apply(first, None, None, {"chapters": {}, "calendar": None})
    assert realm == "eu/es/vc/46250-valencia" and own == {"chapters": {"tax": SHA["tax"]}, "calendar": None}
    refused(change_list.apply, first, "town", None, {"chapters": {}, "calendar": None},
            match="for eu/es/vc/46250-valencia; this realm is town")


def test_replaces_must_name_the_active_codex_or_none():
    refused(applied, replaces=None, set=[entry("town/tax")], match="stale change list: it replaces no codex, the active codex is sha256")
    refused(change_list.apply, change_list.parse(text(replaces=SHA["codex"], set=[entry("town/tax")])), "town", None,
            {"chapters": {}, "calendar": None}, match="the active codex is none")


def test_set_and_remove_give_the_realms_own_layer_after_the_change():
    _, own = applied(replaces=SHA["codex"], set=[entry("town/water"), entry("town/tax", SHA["other"]),
                                                 entry("town/calendar", SHA["other"])],
                     remove=["town/procedure"])
    assert own == {"chapters": {"tax": SHA["other"], "water": SHA["water"]}, "calendar": SHA["other"]}
    _, own = applied(replaces=SHA["codex"], remove=["town/calendar"])
    assert own == {"chapters": OWN["chapters"], "calendar": None}


@pytest.mark.parametrize("kw, match", [
    (dict(remove=["eu/es/tax"]), "only the realm's own layer, town, may be emptied"),
    (dict(remove=["town/water"]), "no such chapter"),
    (dict(set=[entry("eu/es/tax")]), "layer from above: it is installed whole, as a release"),
    (dict(set=[entry("eu/es/tax")], release={"source": "town", "version": "1"}), "own layer has no releases"),
    (dict(set=[entry("eu/es/tax")], remove=["town/tax"], release={"source": "eu/es", "version": "1"}), "remove is empty"),
    (dict(set=[entry("eu/es/tax"), entry("eu/tax")], release={"source": "eu/es", "version": "1"}), "eu/tax is not part of the release of eu/es"),
])
def test_what_apply_refuses(kw, match):
    refused(applied, replaces=SHA["codex"], match=match, **kw)


def test_a_release_may_set_the_realms_own_chapters_too_and_only_those_of_its_source():
    # The realm's own layer changes with every change list; a release brings one layer from above with it.
    _, own = applied(replaces=SHA["codex"], set=[entry("eu/es/tax"), entry("town/water")],
                     release={"source": "eu/es", "version": "1"})
    assert own["chapters"]["water"] == SHA["water"]


# ---- vouch (step 4)


def release(source, version, chapters, calendar=None):
    return {"source": source, "version": version, "chapters": {d: SHA[d] for d in chapters}, "calendar": calendar}


ES = release("eu/es", "1.0.0", ["tax", "procedure"], SHA["calendar"])
CV = release("eu/es/vc", "2.0.0", ["local_regime"])
TOWN_OWN = {"chapters": {"tax": SHA["other"], "water": SHA["water"]}, "calendar": None}
THROUGH_CV = [{"source": "eu/es/vc", "layers": [ES, CV]}]


def vouched(set=(), release=None, relays=THROUGH_CV, own=TOWN_OWN):
    listed = change_list.parse(text(realm="town", set=set or [entry("town/tax", SHA["other"])], release=release))
    return change_list.vouch(listed, "town", relays, own)


def test_the_relaying_sync_source_vouches_for_the_layers_it_relays_and_the_realm_for_its_own():
    planned, vouched_by = vouched()
    assert planned.layers == ["eu/es", "eu/es/vc", "town"]
    assert planned.paths == {"local_regime": ["eu/es/vc"], "procedure": ["eu/es"], "tax": ["eu/es", "town"],
                             "water": ["town"]}
    assert vouched_by == {"eu/es/tax": "eu/es/vc", "eu/es/procedure": "eu/es/vc", "eu/es/vc/local_regime": "eu/es/vc",
                          "town/tax": "town", "town/water": "town", "eu/es/calendar": "eu/es/vc"}


def test_the_publishers_own_record_vouches_when_the_realm_syncs_from_it_directly():
    direct = THROUGH_CV + [{"source": "eu/es", "layers": [ES]}]
    _, vouched_by = vouched(relays=direct)
    assert vouched_by["eu/es/tax"] == "eu/es" and vouched_by["eu/es/vc/local_regime"] == "eu/es/vc"
    _, vouched_by = vouched(relays=direct[::-1])  # whichever order the links come in
    assert vouched_by["eu/es/tax"] == "eu/es"


def test_a_release_is_installed_whole_as_its_record_lists_it():
    whole = [entry("eu/es/tax"), entry("eu/es/procedure"), entry("eu/es/calendar")]
    planned, vouched_by = vouched(set=whole, release={"source": "eu/es", "version": "1.0.0"})
    assert [c["sha256"] for c in planned.calendars] == [SHA["calendar"]] and vouched_by["eu/es/calendar"] == "eu/es/vc"
    refused(vouched, whole[:2], {"source": "eu/es", "version": "1.0.0"},
            match="installed whole: eu/es 1.0.0 is eu/es/calendar, eu/es/procedure, eu/es/tax; the change list sets eu/es/procedure, eu/es/tax")
    refused(vouched, whole[:2] + [entry("eu/es/calendar", SHA["other"])], {"source": "eu/es", "version": "1.0.0"},
            match="eu/es/calendar: the record says sha256:.*, the change list sha256:")
    refused(vouched, whole, {"source": "eu/es", "version": "1.1.0"}, match="relays eu/es 1.0.0, not 1.1.0")
    refused(vouched, [entry("eu/tax")], {"source": "eu", "version": "1"}, match="no sync source relays eu")


def test_a_realm_chapter_a_release_lists_is_skipped():
    with_realm = release("eu/es/vc", "2.0.0", ["local_regime", "realm"])
    relays = [{"source": "eu/es/vc", "layers": [ES, with_realm]}]
    for listed in ([entry("eu/es/vc/local_regime")], [entry("eu/es/vc/local_regime"), entry("eu/es/vc/realm")]):
        planned, _ = vouched(set=listed, release={"source": "eu/es/vc", "version": "2.0.0"}, relays=relays)
        assert planned.skipped == [{"source": "eu/es/vc", "domain": "realm", "sha256": SHA["realm"]}]
        assert "eu/es/vc/realm" not in dict(change_list.named(planned))


def test_two_sync_sources_relaying_different_releases_of_one_layer_are_refused():
    newer = release("eu/es", "1.1.0", ["tax", "procedure"], SHA["calendar"])
    relays = THROUGH_CV + [{"source": "eu/es/vc/emshi", "layers": [newer, release("eu/es/vc/emshi", "1", ["water"])]}]
    refused(vouched, (), None, relays, match="relay different releases \\(1.0.0 and 1.1.0\\)")


def test_paths_that_are_not_one_chain_are_refused():
    emshi = release("eu/es/vc/emshi", "1", ["tax"])  # EMSHI writes tax beside CV, not below it
    cv_tax = release("eu/es/vc", "2.0.0", ["local_regime", "tax"])
    beside = [{"source": "eu/es/vc", "layers": [ES, cv_tax]}, {"source": "eu/es/vc/emshi", "layers": [ES, emshi]}]
    refused(vouched, (), None, beside, match="Paths that differ are refused unless one extends the other")
    untied = THROUGH_CV + [{"source": "eu/es/vc/emshi", "layers": [ES, emshi]}]  # no domain puts CV and EMSHI in order
    refused(vouched, (), None, untied, match="neither above the other")
    below = THROUGH_CV + [{"source": "eu/es/vc/emshi", "layers": [ES, CV, release("eu/es/vc/emshi", "1", ["water"])]}]
    planned, vouched_by = vouched(relays=below)
    assert planned.paths["water"] == ["eu/es/vc/emshi", "town"] and vouched_by["eu/es/vc/emshi/water"] == "eu/es/vc/emshi"


def test_the_realm_cannot_be_a_layer_of_its_sync_sources():
    refused(vouched, (), None, [{"source": "eu/es/vc", "layers": [ES, release("town", "1", ["tax"])]}],
            match="relays town, this realm's own layer")


def test_a_realm_with_no_sync_sources_runs_its_own_layer_alone():
    planned, vouched_by = vouched(relays=[])
    assert planned.layers == ["town"] and planned.paths == {"tax": ["town"], "water": ["town"]}
    assert vouched_by == {"town/tax": "town", "town/water": "town"}
