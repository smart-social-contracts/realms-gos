"""The synthetic source ``arcadia`` (tests/fixtures/legislation): its record is consistent and its chapters are valid
legislation, and a scripted sync source can serve it the way a follower will ask for it."""

import hashlib
import json

import fake_chain
from core import op
from fixtures import legislation
from realms.legislation import Codex, plan


def test_two_releases_with_effective_dates_name_chapters_that_exist():
    first, second = legislation.releases()
    assert (first["version"], second["version"]) == ("1.0.0", "2.0.0")
    assert first["published"] < first["effective_from"] < second["published"] < second["effective_from"]
    for release in (first, second):
        assert set(release["chapters"]) == {"procedure", "tax"}
        for checksum in release["chapters"].values():
            assert "sha256:" + hashlib.sha256(legislation.chapter(checksum)).hexdigest() == checksum
    unchanged = {d for d in first["chapters"] if first["chapters"][d] == second["chapters"][d]}
    assert unchanged == {"procedure"}  # a release lists every chapter, changed or not (§3.2)


def test_every_chapter_loads_and_the_record_is_what_the_resolver_takes():
    for release in legislation.releases():
        codex = Codex([legislation.SOURCE])
        for domain, checksum in release["chapters"].items():
            codex.load(legislation.SOURCE, domain, legislation.chapter(checksum).decode())
        codex.check()
    follower = plan("follower", [{"source": legislation.SOURCE, "layers": [legislation.layer("2.0.0")]}])
    assert follower.layers == [legislation.SOURCE, "follower"]
    assert {c["domain"]: c["sha256"] for c in follower.chapters} == legislation.releases()[1]["chapters"]


def test_a_scripted_sync_source_answers_its_record_and_the_bytes_of_a_chapter():
    source = fake_chain.peer("arcadia-capital", legislation_releases=legislation.releases(),
                             legislation_chapter=lambda sent: legislation.chapter(sent["sha256"]).decode())
    releases = json.loads(op.run(fake_chain.call_op("arcadia-capital", "legislation_releases", {})))
    checksum = releases[0]["chapters"]["tax"]
    text = op.run(fake_chain.call_op("arcadia-capital", "legislation_chapter", {"sha256": checksum}))
    assert "sha256:" + hashlib.sha256(text.encode()).hexdigest() == checksum
    assert source.sent("legislation_chapter") == [{"sha256": checksum}]
