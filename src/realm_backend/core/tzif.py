"""Time zone files without ``zoneinfo``, an empty stub in a canister (realms-gos#503).

``parse`` reads the bytes of one TZif file (RFC 8536, version 2 or later), a zone file of the IANA tz database,
and ``Zone.offset`` gives the UTC offset at an instant. The explicit transitions of a file end early, even in a
"fat" one (2037 for Europe/Madrid), so after the last one the POSIX TZ string at the end of the file decides.
Only the ``Mm.w.d[/time]`` form of its switch dates is read, the one the tz database writes; any other fails.
"""

from core.civil import civil_from_days, days_from_civil, weekday

LETTERS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
CLOCK = "+-:0123456789"


class Zone:
    """The offsets (seconds east of UTC) of one time zone.

    ``times`` are the UTC seconds of its transitions and ``offsets`` the offset each switches to; ``first`` holds
    before the first one and ``rule`` (a Rule, or None) from the last one on.
    """

    def __init__(self, times, offsets, first, rule):
        self.times, self.offsets, self.first, self.rule = times, offsets, first, rule

    def offset(self, seconds):
        """The offset at UTC instant ``seconds``. ``bisect`` is a stub in a canister, so the search is by hand."""
        times = self.times
        if self.rule and (not times or seconds >= times[-1]):
            return self.rule.offset(seconds)
        low, high = 0, len(times)
        while low < high:
            middle = (low + high) // 2
            if times[middle] <= seconds:
                low = middle + 1
            else:
                high = middle
        return self.offsets[low - 1] if low else self.first


class Rule:
    """A POSIX TZ string: the standard offset, and when there is daylight saving time its offset and the two
    switches, ``(month, week, weekday, seconds)`` in the clock then in force. Week 5 is the last."""

    def __init__(self, standard, daylight=None, start=None, end=None):
        self.standard, self.daylight, self.start, self.end = standard, daylight, start, end

    def offset(self, seconds):
        if self.daylight is None:
            return self.standard
        year = civil_from_days((seconds + self.standard) // 86400)[0]
        begins = _switch(year, self.start) - self.standard
        ends = _switch(year, self.end) - self.daylight
        # In the southern hemisphere (and Dublin's winter time) the period runs over the new year.
        in_period = begins <= seconds < ends if begins < ends else not ends <= seconds < begins
        return self.daylight if in_period else self.standard


UTC = Zone([], [], 0, Rule(0))


def parse(data):
    """The Zone of a TZif file's bytes. The first block, with 32-bit times, is for old readers and is skipped."""
    if data[:4] != b"TZif" or data[4:5] < b"2":
        raise ValueError("not a TZif file of version 2 or later")
    utc_flags, std_flags, leaps, transitions, types, chars = _counts(data, 0)
    block = 44 + transitions * 5 + types * 6 + chars + leaps * 8 + std_flags + utc_flags
    utc_flags, std_flags, leaps, transitions, types, chars = _counts(data, block)
    at = block + 44
    times = [_number(data, at + 8 * i, 8) for i in range(transitions)]
    at += 8 * transitions
    kinds = data[at : at + transitions]
    at += transitions
    offsets = [_number(data, at + 6 * i, 4) for i in range(types)]  # each type: offset, daylight flag, name index
    at += 6 * types + chars + 12 * leaps + std_flags + utc_flags
    footer = data[at:].decode().strip()
    return Zone(times, [offsets[kind] for kind in kinds], offsets[0], _posix_rule(footer) if footer else None)


def _counts(data, at):
    return [_number(data, at + 20 + 4 * i, 4, signed=False) for i in range(6)]


def _number(data, at, size, signed=True):
    return int.from_bytes(data[at : at + size], "big", signed=signed)


def _posix_rule(text):
    """``CET-1CEST,M3.5.0,M10.5.0/3`` as a Rule."""
    head, *switches = text.split(",")
    offset, rest = _split(_after_name(head), CLOCK)
    standard = -_seconds(offset)  # POSIX counts west of Greenwich, the files east
    if not rest:
        return Rule(standard)
    offset, rest = _split(_after_name(rest), CLOCK)
    if rest or len(switches) != 2:
        raise ValueError("unsupported time zone rule " + text)
    daylight = -_seconds(offset) if offset else standard + 3600  # an hour ahead unless it says otherwise
    return Rule(standard, daylight, _switch_date(switches[0]), _switch_date(switches[1]))


def _after_name(text):
    """What follows the abbreviation at the start of ``text``: ``CET`` or, with signs or digits, ``<+03>``."""
    return text[text.index(">") + 1 :] if text.startswith("<") else text.lstrip(LETTERS)


def _split(text, chars):
    """``text`` cut after its longest prefix made of ``chars``."""
    rest = text.lstrip(chars)
    return text[: len(text) - len(rest)], rest


def _seconds(text):
    """``[+-]hh[:mm[:ss]]``. Hours may pass 24: Santiago switches at ``24``, Gaza at ``50``."""
    hours, minutes, seconds = (text.lstrip("+-").split(":") + ["0", "0"])[:3]
    return (-1 if text.startswith("-") else 1) * (int(hours) * 3600 + int(minutes) * 60 + int(seconds))


def _switch_date(text):
    """``M3.5.0/3``: month, week, weekday and the clock time, 02:00 when left out."""
    date, _, clock = text.partition("/")
    if not date.startswith("M"):
        raise ValueError("unsupported time zone rule " + text)
    month, week, day = (int(n) for n in date[1:].split("."))
    return month, week, day, (_seconds(clock) if clock else 7200)


def _switch(year, date):
    """The local clock seconds since the epoch of a switch ``date`` in ``year``."""
    month, week, day, seconds = date
    first = days_from_civil(year, month, 1)
    found = first + (day - weekday(first)) % 7 + 7 * (week - 1)
    if found >= days_from_civil(year + month // 12, month % 12 + 1, 1):  # week 5 is the last, not a fifth
        found -= 7
    return found * 86400 + seconds
