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
from ggg import Extension, JournalEntry, LayeredCodex, Proposal, Realm

EXTENSION = "journal"
STATE = "journal"  # in Realm.manifest_data: {"first_day": "YYYY-MM-DD", "failure": the last entry not recorded, why}
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
    extension's ``config.personal_data.entry_fields``. With no extension there are no rules, and nothing is kept; rules
    that are not a list of names are read as none, so that a malformed config keeps nothing rather than fail."""
    extension = Extension[EXTENSION]
    if extension is None:
        return []
    try:
        fields = extension.manifest["config"]["personal_data"]["entry_fields"]
    except (KeyError, TypeError):
        return []
    return [name for name in fields if isinstance(name, str)] if isinstance(fields, list) else []


def record(kind, **details):
    """Append an entry of ``kind``, stamped now, and return it. Of ``details`` it keeps only the fields the personal-data
    rules allow (``allowed_fields``), so that what they leave out is never stored. A quarter records nothing and returns
    None."""
    if not publishes():
        return None
    allowed = allowed_fields()
    return append(kind, {name: value for name, value in details.items() if name in allowed})


def append(kind, details):
    """Append an entry of ``kind`` with ``details`` as they are, stamped now, and return it."""
    at = chain.now()
    return JournalEntry(id=next_id(JournalEntry, "E"), day=local_day(at), at=at, kind=kind, details=details)


def layers_of(codex):
    """The sources a layered ``codex`` runs, top to bottom, each with the release versions of its chapters (the
    realm's own layer has none)."""
    return [{"source": source, "versions": sorted({entry["version"] for entry in codex.chapters.values()
                                                   if entry["source"] == source and entry.get("version")})}
            for source in codex.layers]


def reported(kind, change, details):
    """Record an entry of ``kind`` for something the realm has already done or learned: ``change`` names it, by its
    codices or by a release's source and version, and the function ``details`` gives the rest, so that working it out
    is guarded too. The journal only reports: whatever goes wrong here, the change stands (a revert above all, the
    realm's floor, §10; a check goes on), and ``not_recorded`` says so."""
    try:
        if not publishes():
            return
        record(kind, **change, **details())
    except Exception as e:  # noqa: BLE001  whatever it is, the switch or the revert that called it goes on
        not_recorded(kind, change, e)


def not_recorded(kind, change, error):
    """The entry of ``kind`` for ``change`` could not be recorded. The issue of the day says which change it misses, in
    an entry ``not_recorded`` that holds only the kind and what names it, the codices' ids or a release's source and
    version: they name no person, and the personal-data rules may be what failed. The ``error`` is kept for the IT admin
    team, who read it in codex_status (``failure``). Each is guarded on its own, writing the error out too (an
    exception's text is its own code): neither may stop the change, and one still goes in when the other fails."""
    try:
        append("not_recorded", {"kind": kind, **change})
    except Exception:  # noqa: BLE001  the error below is still kept
        pass
    try:
        realm = Realm["1"]
        failure = {"kind": kind, **change, "at": chain.now(), "error": f"{type(error).__name__}: {error}"[:500]}
        realm.manifest_data = {**realm.manifest_data, STATE: {**realm.manifest_data.get(STATE, {}), "failure": failure}}
    except Exception:  # noqa: BLE001  nothing more can be done, and the change stands
        pass


def failure():
    """The last entry the journal could not record, which change it was for, when and why; None if there is none."""
    return Realm["1"].manifest_data.get(STATE, {}).get("failure")


def decision(proposal):
    """The decision behind a change: the id of the ``proposal`` that carried it ("" for a change nobody proposed) and,
    when the realm still has that proposal, the ``vote`` it ended with: the votes for, against and abstaining, and how
    many could vote. Counts only: who voted is not for the journal."""
    row = Proposal[proposal] if proposal else None
    if row is None or not row.tally:
        return {"proposal": proposal}
    return {"proposal": proposal, "vote": {name: row.tally.get(name) for name in ("yes", "no", "abstain", "eligible")}}


def codex_installed(codex):
    """The layered ``codex`` has just become the realm's legislation (api.codex.switch)."""
    reported("codex_installed", {"codex": codex.id},
             lambda: {"layers": layers_of(codex), "activates_at": codex.activates_at, **decision(codex.proposal)})


def codex_reverted(restored, reverted, proposal=""):
    """A revert has made the earlier codex ``restored`` the realm's legislation again in place of ``reverted``
    (api.codex.restore), by the accepted ``proposal`` that carried it, if any. Either codex may be a legacy codex, which
    has no layers."""
    reported("codex_reverted", {"codex": restored.id, "reverted": reverted.id},
             lambda: {"layers": layers_of(restored) if isinstance(restored, LayeredCodex) else [],
                      **decision(proposal)})


def release_found(found):
    """A check of the sync sources has found ``found`` (a ``FoundRelease``), a release no check had found before
    (api.sync_checks.keep_found): it is stamped with the check's time, the moment the realm learned of it."""
    reported("release_found", named(found), lambda: {"effective_from": found.effective_from})


def release_published(release):
    """The realm, a sync source itself, has published ``release`` (a ``Release``) of its own layer
    (api.links.legislation_release_publish)."""
    reported("release_published", named(release), lambda: {"effective_from": release.effective_from})


def named(release):
    """What names a release in an entry, and in the notice of one that could not be recorded."""
    return {"source": release.source, "version": release.version}


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
