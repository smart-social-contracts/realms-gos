"""Realm-local time (realms-gos#503): the zone file reader, the date arithmetic, and the day boundaries.

This runs on CPython, where ``zoneinfo`` exists and is the reference. A canister has none of it, so
tests/deployed/test_local_time.py runs the same code in the real wasm.
"""

import io
import os
import sys
import zoneinfo
from datetime import datetime, timedelta, timezone

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "src", "realm_backend"))

from core import civil, localtime, tzif  # noqa: E402

ZONES = os.path.join(HERE, "..", "fixtures", "tzif")
NS = 10**9
HOUR = 3600


def zone_file(name):
    with open(os.path.join(ZONES, name), "rb") as f:
        return f.read()


def utc(*when):
    """UTC nanoseconds of a UTC wall-clock time."""
    return int(datetime(*when, tzinfo=timezone.utc).timestamp()) * NS


def date_at(ns):
    day, offset = localtime.local_day(ns)
    return civil.civil_from_days(day), offset // HOUR


@pytest.fixture
def realm_zone():
    """Load a zone file as the realm's, and go back to UTC afterwards."""
    def use(name):
        localtime.load(zone_file(name))

    yield use
    localtime.load(None)


def test_days_are_numbered_from_the_epoch():
    assert civil.days_from_civil(1970, 1, 1) == 0
    assert civil.civil_from_days(0) == (1970, 1, 1)
    assert civil.civil_from_days(11017) == (2000, 3, 1)
    assert civil.weekday(0) == 4  # a Thursday
    assert civil.weekday(civil.days_from_civil(2026, 11, 8)) == 0  # a Sunday


def test_day_numbers_agree_with_datetime_over_four_centuries():
    day = datetime(1800, 1, 1).date()
    while day.year < 2200:
        days = civil.days_from_civil(day.year, day.month, day.day)
        assert days == day.toordinal() - 719163
        assert civil.civil_from_days(days) == (day.year, day.month, day.day)
        assert civil.weekday(days) == (day.weekday() + 1) % 7
        day += timedelta(days=1)


@pytest.mark.parametrize("name", ["Europe_Madrid.slim", "Europe_Madrid.fat", "Australia_Sydney.slim",
                                  "America_Santiago.slim", "America_Havana.slim", "Europe_Dublin.slim",
                                  "Asia_Kolkata.slim"])
def test_offsets_are_those_of_zoneinfo_around_every_switch_for_decades(name):
    data = zone_file(name)
    zone, reference = tzif.parse(data), zoneinfo.ZoneInfo.from_file(io.BytesIO(data))
    start = datetime(1995, 1, 1, tzinfo=timezone.utc)
    previous = start.astimezone(reference).utcoffset()
    for day in range(0, 365 * 80):
        noon = start + timedelta(days=day, hours=12)
        offset = noon.astimezone(reference).utcoffset()
        assert zone.offset(int(noon.timestamp())) == offset.total_seconds(), (name, noon)
        if offset != previous:  # a switch since the day before: check every half hour in between
            for minutes in range(-24 * 60, 1, 30):
                at = noon + timedelta(minutes=minutes)
                expected = at.astimezone(reference).utcoffset().total_seconds()
                assert zone.offset(int(at.timestamp())) == expected, (name, at)
        previous = offset


@pytest.mark.parametrize("name", ["Europe_Madrid.fat", "Europe_Madrid.slim"])
def test_madrid_keeps_its_summer_time_after_the_file_lists_no_more_switches(realm_zone, name):
    realm_zone(name)
    assert date_at(utc(2040, 7, 1, 12)) == ((2040, 7, 1), 2)
    assert date_at(utc(2040, 1, 1, 12)) == ((2040, 1, 1), 1)
    assert date_at(utc(2040, 3, 25, 0, 59)) == ((2040, 3, 25), 1)  # the last Sunday of March, 01:00 UTC
    assert date_at(utc(2040, 3, 25, 1)) == ((2040, 3, 25), 2)
    assert date_at(utc(2040, 10, 28, 0, 59)) == ((2040, 10, 28), 2)  # the last Sunday of October
    assert date_at(utc(2040, 10, 28, 1)) == ((2040, 10, 28), 1)
    assert date_at(utc(2100, 8, 15, 12)) == ((2100, 8, 15), 2)


def test_the_local_date_can_differ_from_the_utc_date(realm_zone):
    realm_zone("Europe_Madrid.slim")
    assert date_at(utc(2026, 7, 1, 21, 59)) == ((2026, 7, 1), 2)
    assert date_at(utc(2026, 7, 1, 22)) == ((2026, 7, 2), 2)  # midnight in Madrid
    assert date_at(utc(2026, 12, 31, 23)) == ((2027, 1, 1), 1)
    assert date_at(utc(1969, 12, 31, 23, 30)) == ((1970, 1, 1), 1)


def test_madrid_on_both_sides_of_the_switches_of_2026(realm_zone):
    realm_zone("Europe_Madrid.fat")
    assert date_at(utc(2026, 3, 29, 0, 59, 59)) == ((2026, 3, 29), 1)
    assert date_at(utc(2026, 3, 29, 1)) == ((2026, 3, 29), 2)
    assert date_at(utc(2026, 10, 25, 0, 59, 59)) == ((2026, 10, 25), 2)
    assert date_at(utc(2026, 10, 25, 1)) == ((2026, 10, 25), 1)


def test_sydney_has_summer_time_over_the_new_year(realm_zone):
    realm_zone("Australia_Sydney.slim")
    assert date_at(utc(2026, 1, 15, 12)) == ((2026, 1, 15), 11)
    assert date_at(utc(2026, 7, 15, 12)) == ((2026, 7, 15), 10)
    assert date_at(utc(2026, 4, 4, 15, 59)) == ((2026, 4, 5), 11)  # summer time ends on the first Sunday of April
    assert date_at(utc(2026, 4, 4, 16)) == ((2026, 4, 5), 10)
    assert date_at(utc(2026, 10, 3, 15, 59)) == ((2026, 10, 4), 10)  # and starts on the first Sunday of October
    assert date_at(utc(2026, 10, 3, 16)) == ((2026, 10, 4), 11)
    assert date_at(utc(2040, 12, 25, 14)) == ((2040, 12, 26), 11)


def test_ireland_summer_time_is_read_and_a_half_hour_zone_is_too(realm_zone):
    realm_zone("Europe_Dublin.slim")
    assert date_at(utc(2040, 7, 1, 12)) == ((2040, 7, 1), 1)
    assert date_at(utc(2040, 1, 1, 12)) == ((2040, 1, 1), 0)
    realm_zone("Asia_Kolkata.slim")
    assert localtime.local_day(utc(2026, 1, 1, 18, 30))[1] == 5 * HOUR + 1800
    assert date_at(utc(2026, 1, 1, 18, 30)) == ((2026, 1, 2), 5)


def test_without_a_zone_file_the_realm_is_in_utc():
    assert localtime.local_day(utc(2026, 7, 1, 23, 59)) == (civil.days_from_civil(2026, 7, 1), 0)


def test_a_day_with_a_clock_change_is_23_or_25_hours_long(realm_zone):
    realm_zone("Europe_Madrid.slim")
    day = civil.days_from_civil

    def hours(*date):
        start, end = localtime.day_bounds(day(*date))
        return start, (end - start) // NS / HOUR

    assert hours(2026, 3, 28) == (utc(2026, 3, 27, 23), 24)
    assert hours(2026, 3, 29) == (utc(2026, 3, 28, 23), 23)
    assert hours(2026, 3, 30) == (utc(2026, 3, 29, 22), 24)
    assert hours(2026, 10, 25) == (utc(2026, 10, 24, 22), 25)
    assert hours(2040, 3, 25) == (utc(2040, 3, 24, 23), 23)


def test_a_day_that_starts_when_the_clocks_change_at_midnight(realm_zone):
    realm_zone("America_Santiago.slim")
    day = civil.days_from_civil
    # Clocks go forward at 24:00 on Saturday 5 September 2026: Sunday starts at 01:00, Saturday has 24 hours.
    assert localtime.day_bounds(day(2026, 9, 6))[0] == utc(2026, 9, 6, 4)
    assert date_at(utc(2026, 9, 6, 4)) == ((2026, 9, 6), -3)
    assert date_at(utc(2026, 9, 6, 3, 59)) == ((2026, 9, 5), -4)
    # Clocks go back at 24:00 on Saturday 4 April 2026: 23:00 happens twice, and Sunday starts after the second.
    assert localtime.day_bounds(day(2026, 4, 5)) == (utc(2026, 4, 5, 4), utc(2026, 4, 6, 4))
    assert localtime.day_bounds(day(2026, 4, 4)) == (utc(2026, 4, 4, 3), utc(2026, 4, 5, 4))


def test_a_day_that_starts_at_the_first_of_two_midnights(realm_zone):
    realm_zone("America_Havana.slim")
    day = civil.days_from_civil
    # Clocks go back at 01:00 on Sunday 1 November 2026, so 00:00 to 01:00 happens twice: the day is 25 hours long.
    start, end = localtime.day_bounds(day(2026, 11, 1))
    assert (start, end) == (utc(2026, 11, 1, 4), utc(2026, 11, 2, 5))


@pytest.mark.parametrize("name", ["Europe_Madrid.slim", "Australia_Sydney.slim", "America_Santiago.slim",
                                  "America_Havana.slim", "Europe_Dublin.slim"])
def test_days_tile_time_without_a_gap_or_overlap(realm_zone, name):
    realm_zone(name)
    for hours in range(0, 24 * 365 * 3, 5):  # three years from 2026, every five hours
        at = utc(2026, 1, 1) + hours * HOUR * NS
        day, _ = localtime.local_day(at)
        start, end = localtime.day_bounds(day)
        assert start <= at < end
        assert localtime.day_bounds(day + 1)[0] == end
        assert localtime.local_day(start)[0] == day and localtime.local_day(start - NS)[0] == day - 1


def test_the_posix_rules_of_the_tz_database_are_read():
    assert vars(tzif._posix_rule("CET-1CEST,M3.5.0,M10.5.0/3")) == {
        "standard": HOUR, "daylight": 2 * HOUR, "start": (3, 5, 0, 7200), "end": (10, 5, 0, 3 * HOUR)}
    assert vars(tzif._posix_rule("<-04>4<-03>,M9.1.6/24,M4.1.6/24")) == {
        "standard": -4 * HOUR, "daylight": -3 * HOUR, "start": (9, 1, 6, 24 * HOUR), "end": (4, 1, 6, 24 * HOUR)}
    assert vars(tzif._posix_rule("<+1245>-12:45<+1345>,M9.5.0/2:45,M4.1.0/3:45"))["standard"] == 12 * HOUR + 2700
    assert vars(tzif._posix_rule("EET-2EEST,M3.4.4/50,M10.4.4/50"))["start"] == (3, 4, 4, 50 * HOUR)
    assert vars(tzif._posix_rule("<+03>-3")) == {"standard": 3 * HOUR, "daylight": None, "start": None, "end": None}


@pytest.mark.parametrize("text", ["EST5EDT,J60,J300", "EST5EDT,60,300", "EST5EDT", "EST5EDT,M3.2.0"])
def test_a_rule_in_another_form_is_refused_rather_than_misread(text):
    with pytest.raises(ValueError):
        tzif._posix_rule(text)


@pytest.mark.parametrize("data", [b"", b"not a zone file", b"TZif" + bytes(40)])
def test_a_file_that_is_not_tzif_version_2_or_later_is_refused(data):
    with pytest.raises(ValueError):
        tzif.parse(data)
