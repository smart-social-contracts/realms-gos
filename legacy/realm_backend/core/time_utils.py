"""Timestamp parsing for entity ``timestamp_created`` / ``timestamp_updated``.

Those columns are stored as ``"YYYY-MM-DD HH:MM:SS[.mmm]"`` strings, and the
canister has no ``datetime``, so the conversion is done by hand. Three
extensions had each grown their own copy of this arithmetic; they now share
one, which is also the only copy with tests.
"""

import time

DAYS_IN_MONTH = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def is_leap(year: int) -> bool:
    return (year % 4 == 0 and year % 100 != 0) or year % 400 == 0


def days_from_epoch(year: int, month: int, day: int) -> int:
    """Whole days from 1970-01-01 to the given date."""
    days = 0
    step = 1 if year >= 1970 else -1
    for y in range(1970, year, step):
        days += step * (366 if is_leap(y) else 365)
    for m in range(1, month):
        days += DAYS_IN_MONTH[m - 1] + (1 if m == 2 and is_leap(year) else 0)
    return days + day - 1


def civil_from_days(days: int) -> tuple:
    """Whole days from 1970-01-01 → (year, month, day) in UTC.

    Howard Hinnant's civil_from_days algorithm; exact for all Gregorian dates.
    """
    z = int(days) + 719_468
    era = (z if z >= 0 else z - 146_096) // 146_097
    doe = z - era * 146_097
    yoe = (doe - doe // 1_460 + doe // 36_524 - doe // 146_096) // 365
    year = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    day = doy - (153 * mp + 2) // 5 + 1
    month = mp + 3 if mp < 10 else mp - 9
    if month <= 2:
        year += 1
    return (year, month, day)


def now_ms() -> int:
    """Current Unix time in milliseconds, canister-safe.

    ``time.time()`` returns 0.0 under Kybra/WASM; the IC exposes a
    nanosecond clock via ``ic.time()``. Falls back to ``time.time()`` for
    local/test runs.
    """
    try:
        from kybra import ic as _ic  # noqa: PLC0415

        t = _ic.time()
        if t and t > 0:
            return int(t) // 1_000_000
    except Exception:
        pass
    t = time.time()
    return int(t * 1000) if t and t > 0 else 0


def format_timestamp_ms(ms: int) -> str:
    """Format epoch milliseconds as ``YYYY-MM-DD HH:MM:SS.mmm`` UTC.

    Inverse of ``parse_timestamp_ms``. No datetime. Return "" if ms <= 0.
    """
    if ms <= 0:
        return ""
    seconds, millis = divmod(int(ms), 1000)
    days, rem = divmod(seconds, 86400)
    hour, rem = divmod(rem, 3600)
    minute, second = divmod(rem, 60)
    year, month, day = civil_from_days(days)
    return (
        f"{year:04d}-{month:02d}-{day:02d} "
        f"{hour:02d}:{minute:02d}:{second:02d}.{millis:03d}"
    )


def parse_timestamp_ms(value) -> int:
    """Epoch milliseconds for a stored timestamp string.

    Returns 0 for anything unparseable — callers treat these as "unknown" and
    sort them last, which is preferable to failing a whole listing over one
    malformed row.
    """
    try:
        text = str(value).strip()
        if not text:
            return 0
        date_part, _, time_part = text.partition(" ")
        time_part = time_part or "00:00:00"

        year, month, day = (int(p) for p in date_part.split("-")[:3])
        if not (1 <= month <= 12 and 1 <= day <= 31):
            return 0

        millis = 0
        if "." in time_part:
            time_part, _, frac = time_part.partition(".")
            millis = int(frac.ljust(3, "0")[:3])

        pieces = (time_part.split(":") + ["0", "0", "0"])[:3]
        hour, minute, second = (int(p) for p in pieces)

        seconds = (
            days_from_epoch(year, month, day) * 86400
            + hour * 3600 + minute * 60 + second
        )
        return seconds * 1000 + millis
    except Exception:
        return 0
