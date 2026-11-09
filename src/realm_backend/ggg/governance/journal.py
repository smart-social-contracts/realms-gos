"""The storage of the realm's official journal (docs/codices/layered-codex.md §9.6). Data only: the rules are in
core.journal."""

from ic_python_db import Integer, String

from core.db import Json, Row


class JournalEntry(Row):
    """One report of the realm's official journal, appended when the realm learns of what it reports and never
    changed. ``day`` is the realm's local day on the capital's clock at that moment (``YYYY-MM-DD``): an issue is the
    entries of one day."""

    __alias__ = "id"
    id = String()
    day = String(default="", indexed=True)
    at = Integer(default=0)  # seconds, UTC: when the realm learned of it
    kind = String(default="")  # codex_installed (codex_reverted comes with the revert hook)
    details = Json({})  # only the fields the journal's personal-data rules allow (core.journal.record)
