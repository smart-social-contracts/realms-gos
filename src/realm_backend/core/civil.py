"""Dates as day numbers: days since 1970-01-01 in the Gregorian calendar.

Howard Hinnant's algorithms (howardhinnant.github.io/date_algorithms.html). Plain arithmetic, because a canister
has no ``datetime.date`` or ``calendar``. Working-day counting builds on these.
"""


def days_from_civil(year, month, day):
    year -= month <= 2
    era = year // 400
    year_of_era = year - era * 400
    day_of_year = (153 * (month - 3 if month > 2 else month + 9) + 2) // 5 + day - 1
    day_of_era = year_of_era * 365 + year_of_era // 4 - year_of_era // 100 + day_of_year
    return era * 146097 + day_of_era - 719468


def civil_from_days(days):
    """``(year, month, day)`` of a day number."""
    days += 719468
    era = days // 146097
    day_of_era = days - era * 146097
    year_of_era = (day_of_era - day_of_era // 1460 + day_of_era // 36524 - day_of_era // 146096) // 365
    day_of_year = day_of_era - (365 * year_of_era + year_of_era // 4 - year_of_era // 100)
    shifted_month = (5 * day_of_year + 2) // 153  # March is 0
    day = day_of_year - (153 * shifted_month + 2) // 5 + 1
    month = shifted_month + 3 if shifted_month < 10 else shifted_month - 9
    return year_of_era + era * 400 + (month <= 2), month, day


def weekday(days):
    """0 for Sunday to 6 for Saturday, the way POSIX numbers them. 1970-01-01 was a Thursday."""
    return (days + 4) % 7
