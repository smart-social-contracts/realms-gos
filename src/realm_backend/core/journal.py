"""The realm's official journal (docs/codices/layered-codex.md §9.6): one issue a day, each the entries of one day.

An entry is appended when the realm learns of what it reports, stamped with the instant and with the realm's local day
on its own clock (core.localtime), which on a capital is the realm's official time (§4.5). Stamping the day, instead of
working it out when an issue is read, keeps a past issue as it was when the realm changes its zone. An issue is not
stored: it is the entries of its day, and it exists for every day from the journal's first day to today, with or
without entries, so no timer makes it and an upgrade loses nothing. A quarter issues nothing: its capital does.

The journal only reports. Nothing here reads or writes a codex's ``activates_at``: legislation applies when the
realm's operations run it, never when the journal prints it.
"""

from core import chain, civil, localtime
from core.db import Fail, NotFound, each, next_id, view
from ggg import Extension, JournalEntry, LayeredCodex, Realm

EXTENSION = "journal"
STATE = "journal"  # in Realm.manifest_data: {"first_day": "YYYY-MM-DD"}
SHOWN = ("id", "at", "kind", "details")


def publishes():
    """Whether this canister issues a journal: a capital or an independent realm, not a quarter (§9.6)."""
    realm = Realm["1"]
    return not (realm.is_quarter and not realm.is_capital)


# ---- Days


def day_text(number):
    """``YYYY-MM-DD`` of a day number (core.civil)."""
    return "%04d-%02d-%02d" % civil.civil_from_days(number)


def day_number(text):
    """The day number of ``YYYY-MM-DD``. A date that does not exist (2026-02-30) or is written another way is a Fail."""
    try:
        number = civil.days_from_civil(*(int(part) for part in text.split("-")))
    except (AttributeError, TypeError, ValueError):
        number = None
    if number is None or day_text(number) != text:
        raise Fail(f"day must be a date written YYYY-MM-DD, not {repr(text)[:30]}")
    return number


def local_day(seconds):
    """The realm's local day of the UTC time ``seconds``, as ``YYYY-MM-DD``. It follows the zone in memory, which
    ``setup.start`` and every change of the realm's zone set; it is never loaded here, a query could not keep it."""
    return day_text(localtime.local_day(seconds * localtime.NS)[0])


def fix_first_day():
    """Fix the journal's first day: the realm's local day when it first starts with the journal (an upgraded realm
    begins on the day of its upgrade, with no earlier entries). It runs at every start of the realm and at every change
    of its zone, and only ever moves the day back. A realm starts on UTC and its founder sets the zone later: for a
    zone west of UTC, today there is a day earlier than the one the journal began on, and without this today would
    have no issue and the entries recorded since would sit in no issue."""
    realm = Realm["1"]
    data, today = realm.manifest_data, local_day(chain.now())
    first = data.get(STATE, {}).get("first_day")
    if first is None or day_number(today) < day_number(first):
        realm.manifest_data = {**data, STATE: {"first_day": today}}


def first_day():
    return Realm["1"].manifest_data[STATE]["first_day"]


# ---- Entries


def allowed_fields():
    """The fields of an entry's ``details`` that the journal's personal-data rules let it keep: the journal
    extension's ``config.personal_data.entry_fields``. With no extension there are no rules, and nothing is kept."""
    extension = Extension[EXTENSION]
    if extension is None:
        return []
    rules = ((extension.manifest or {}).get("config") or {}).get("personal_data") or {}
    return rules.get("entry_fields") or []


def record(kind, **details):
    """Append an entry of ``kind``, stamped now, and return it. Of ``details`` it keeps only the fields the personal-data
    rules allow (``allowed_fields``), so that what they leave out is never stored. A quarter records nothing and returns
    None."""
    if not publishes():
        return None
    allowed = allowed_fields()
    at = chain.now()
    return JournalEntry(id=next_id(JournalEntry, "E"), day=local_day(at), at=at, kind=kind,
                        details={name: value for name, value in details.items() if name in allowed})


def layers_of(codex):
    """The sources a layered ``codex`` runs, top to bottom, each with the release versions of its chapters (the
    realm's own layer has none)."""
    return [{"source": source, "versions": sorted({entry["version"] for entry in codex.chapters.values()
                                                   if entry["source"] == source and entry.get("version")})}
            for source in codex.layers]


def codex_installed(codex):
    """The layered ``codex`` has just become the realm's legislation (api.codex.switch)."""
    record("codex_installed", codex=codex.id, layers=layers_of(codex), proposal=codex.proposal,
           activates_at=codex.activates_at)


def codex_reverted(restored, reverted):
    """A revert has made the earlier codex ``restored`` the realm's legislation again in place of ``reverted``
    (api.codex.restore). Either may be a legacy codex, which has no layers."""
    record("codex_reverted", codex=restored.id, reverted=reverted.id,
           layers=layers_of(restored) if isinstance(restored, LayeredCodex) else [])


# ---- Issues


def issue(day=None):
    """The issue of ``day`` (``YYYY-MM-DD``, default today) of the realm named ``realm``: its ``number``, counted
    from the journal's first day, and the day's ``entries`` in the order they were recorded. A day after today or
    before the first day is not found."""
    today, first = local_day(chain.now()), first_day()
    day = today if day is None else day
    number = day_number(day) - day_number(first) + 1
    if day_number(day) > day_number(today):
        raise NotFound(f"there is no issue for {day}: today is {today}, on the realm's clock")
    if number < 1:
        raise NotFound(f"there is no issue for {day}: the journal begins on {first}")
    realm = Realm["1"]
    return {"realm": realm.name, "day": day, "number": number, "first_day": first, "today": today,
            "timezone": realm.timezone, "entries": [view(entry, *SHOWN) for entry in each(JournalEntry, "day", day)]}
