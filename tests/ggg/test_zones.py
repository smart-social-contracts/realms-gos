"""The tz database a release bundles (realms-gos#511, scripts/build_zones.py): the package is whole, and every zone in
it reads with the realm's own TZif reader."""

import pathlib
import re
from datetime import datetime, timezone

import zonedata
from core import tzif, zones

PACKAGE = pathlib.Path(zonedata.__file__).parent
AREAS = {"Africa", "America", "Antarctica", "Arctic", "Asia", "Atlantic", "Australia", "Europe", "Indian", "Pacific", "Etc"}
HOUR = 3600


def test_the_package_is_whole():
    modules = {p.stem for p in PACKAGE.glob("*.py")} - {"__init__", "_bundle"}
    assert set(zonedata.MODULE.values()) == modules
    bundle = (PACKAGE / "_bundle.py").read_text()
    assert all(f"import zonedata.{m}  #" in bundle for m in modules) and bundle.count("import zonedata.") == len(modules)
    assert re.fullmatch(r"\d{4}[a-z]", zonedata.VERSION)


def test_the_names_are_those_ggg_takes():
    assert all(name.partition("/")[0] in AREAS and "/" in name for name in zonedata.MODULE)
    assert zones.known("UTC") and zones.known("Europe/Madrid") and zones.known("America/Argentina/Buenos_Aires")
    assert not any(zones.known(name) for name in ("CET", "EST5EDT", "US/Eastern", "Europe/Mardid", "utc", "", None, 5))


def test_every_zone_reads_and_gives_an_offset_of_this_world():
    for name in zonedata.MODULE:
        zone = tzif.parse(zones.zone_file(name))
        for seconds in (0, 1_000_000_000, 1_782_943_200, 2_500_000_000, 4_000_000_000):  # the 70s to 2096
            assert -12 * HOUR <= zone.offset(seconds) <= 14 * HOUR, (name, seconds)


def utc(*when):
    return int(datetime(*when, tzinfo=timezone.utc).timestamp())


def test_madrid_changes_its_clock_when_it_should():
    madrid = tzif.parse(zones.zone_file("Europe/Madrid"))
    assert [madrid.offset(utc(2026, 3, 29, 0, 59, 59)), madrid.offset(utc(2026, 3, 29, 1))] == [HOUR, 2 * HOUR]
    assert [madrid.offset(utc(2026, 10, 25, 0, 59, 59)), madrid.offset(utc(2026, 10, 25, 1))] == [2 * HOUR, HOUR]
    assert [madrid.offset(utc(2040, 1, 1)), madrid.offset(utc(2040, 7, 1))] == [HOUR, 2 * HOUR]  # by the footer rule


def test_a_link_has_the_zone_file_of_its_target():
    assert zones.zone_file("Asia/Calcutta") == zones.zone_file("Asia/Kolkata")
    assert zones.zone_file("America/Buenos_Aires") == zones.zone_file("America/Argentina/Buenos_Aires")
