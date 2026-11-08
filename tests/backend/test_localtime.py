"""Realm-local time (realms-gos#503): the zone file reader, the date arithmetic, and the day boundaries.

This runs on CPython, where ``zoneinfo`` exists and is the reference. A canister has none of it, so
tests/deployed/test_local_time.py runs the same code in the real wasm.

Counting working days (realms-gos#505) is the library's ``Calendar``; what these tests check is the bridge from a UTC
instant to a local day and back.
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
from realms.legislation import Calendar, LegislationError, days, working_days  # noqa: E402

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
                                  "America_Santiago.slim", "America_Havana.slim", "America_Ojinaga.slim",
                                  "Europe_Dublin.slim", "Asia_Kolkata.slim"])
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


def test_the_footer_rule_holds_only_after_the_last_listed_transition():
    # Ojinaga's last transition, 2022-10-30T08:00Z, goes from Mountain summer time (-6 h) to Central standard time
    # (-6 h), while its footer, CST6CDT, would still have summer time (-5 h) until 6 November.
    data = zone_file("America_Ojinaga.slim")
    zone, reference = tzif.parse(data), zoneinfo.ZoneInfo.from_file(io.BytesIO(data))
    last = int(datetime(2022, 10, 30, 8, tzinfo=timezone.utc).timestamp())
    assert zone.times[-1] == last
    for at in (last - 1, last, last + 1):
        expected = datetime.fromtimestamp(at, timezone.utc).astimezone(reference).utcoffset().total_seconds()
        assert zone.offset(at) == expected, at
    assert zone.offset(last) == -6 * HOUR
    assert zone.offset(last + 1) == -5 * HOUR


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


MADRID = zone_file("Europe_Madrid.slim")
FOOTER = b"\nCET-1CEST,M3.5.0,M10.5.0/3\n"
assert MADRID.endswith(FOOTER)


@pytest.mark.parametrize("data", [
    pytest.param(b"", id="empty"),
    pytest.param(b"not a zone file", id="not tzif"),
    pytest.param(b"TZif" + bytes(40), id="version 1"),
    pytest.param(MADRID[:60], id="cut in the first block"),
    pytest.param(MADRID[:500], id="cut in the second block"),
    pytest.param(MADRID[: -len(FOOTER)], id="no footer"),
    pytest.param(MADRID[: -len(FOOTER) + 1], id="only the newline that opens the footer"),
    pytest.param(MADRID[:-1], id="no newline closing the footer"),
    # The end of summer time would move from 03:00 to 02:00.
    pytest.param(MADRID[:-3], id="footer cut to CET-1CEST,M3.5.0,M10.5.0"),
    pytest.param(MADRID + b"\n", id="something after the footer"),
])
def test_a_file_that_is_not_tzif_version_2_or_later_is_refused(data):
    with pytest.raises(ValueError):
        tzif.parse(data)


# ── Working days (realms-gos#505) ─────────────────────────────────────────────

# A calendar that knows 2026 only: the holidays of the example of #505 (illustrative dates).
CALENDAR = Calendar([{"weekend": ["sat", "sun"], "years": {"2026": [{"date": "2026-10-09"}, {"date": "2026-10-12"}]}}])


def test_two_working_days_from_thursday_8_october_2026_end_on_wednesday_the_14th(realm_zone):
    realm_zone("Europe_Madrid.slim")
    # Fri 9 Oct is a holiday (Valencian Community Day), Sat 10 and Sun 11 the weekend, Mon 12 Oct a holiday.
    day, start = localtime.add_period(utc(2026, 10, 8, 10), working_days(2), CALENDAR)
    assert day == "2026-10-14"
    assert start == utc(2026, 10, 13, 22)  # Wednesday starts at midnight in Madrid, two hours ahead of UTC


def test_a_count_across_the_end_of_summer_time_skips_the_25_hour_sunday_and_moves_the_start(realm_zone):
    realm_zone("Europe_Madrid.slim")
    # Thu 22 Oct + 5 working days: Fri 23, Mon 26, Tue 27, Wed 28, Thu 29. Thursday starts an hour later in UTC than
    # it would have a week before, as the clocks went back on Sunday 25 Oct at 03:00.
    thursday = localtime.add_period(utc(2026, 10, 22, 10), working_days(5), CALENDAR)
    assert thursday == ("2026-10-29", utc(2026, 10, 28, 23))
    # Both 02:30s of that Sunday are the same day.
    for at in (utc(2026, 10, 25, 0, 30), utc(2026, 10, 25, 1, 30)):
        assert localtime.add_period(at, working_days(1), CALENDAR) == ("2026-10-26", utc(2026, 10, 25, 23))
    # And the clocks going forward, on Sun 29 March.
    assert localtime.add_period(utc(2026, 3, 27, 10), working_days(1), CALENDAR) == ("2026-03-30", utc(2026, 3, 29, 22))


def test_a_count_that_reaches_a_year_with_no_entries_fails_closed_naming_the_year(realm_zone):
    realm_zone("Europe_Madrid.slim")
    tuesday = utc(2026, 12, 29, 10)
    assert localtime.add_period(tuesday, working_days(2), CALENDAR)[0] == "2026-12-31"
    with pytest.raises(LegislationError, match="entries for 2027"):
        localtime.add_period(tuesday, working_days(3), CALENDAR)
    assert localtime.add_period(tuesday, days(5), CALENDAR)[0] == "2027-01-03"  # calendar days need no entries


def test_the_same_instant_counted_on_two_clocks_can_end_on_two_days(realm_zone):
    realm_zone("Europe_Madrid.slim")
    madrid, sydney = tzif.parse(zone_file("Europe_Madrid.slim")), tzif.parse(zone_file("Australia_Sydney.slim"))
    at = utc(2026, 10, 13, 20)  # Tuesday 22:00 in Madrid, Wednesday 07:00 in Sydney
    assert localtime.add_period(at, working_days(1), CALENDAR, madrid) == ("2026-10-14", utc(2026, 10, 13, 22))
    assert localtime.add_period(at, working_days(1), CALENDAR, sydney) == ("2026-10-15", utc(2026, 10, 14, 13))
    assert localtime.day_bounds(civil.days_from_civil(2026, 10, 15), sydney)[0] == utc(2026, 10, 14, 13)
    # The realm's own clock is the default, and a count on another leaves it alone.
    assert localtime.add_period(at, working_days(1), CALENDAR) == ("2026-10-14", utc(2026, 10, 13, 22))
