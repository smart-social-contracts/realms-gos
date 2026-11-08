"""The storage of a layered codex (docs/codices/layered-codex.md §9.1). Data only: the rules are in
core.layered_codex."""

from ic_python_db import Integer, String

from core.db import Json, Row


class LayeredCodex(Row):
    """The legislation a realm runs, built from the chapters of several sources. Its ``id`` names the legislation, not
    this realm's copy of it: ``sha256:`` and the checksum of its layers, layer paths and chapters' checksums."""

    __alias__ = "id"
    id = String()
    layers = Json([])  # every source with a chapter or a calendar, top to bottom
    layer_paths = Json({})  # {domain: [source, ...]}, top to bottom
    # {chapter id: {source, version, sha256, vouched_by, route, fetched_at, url, size}}; a calendar is <source>/calendar
    chapters = Json({})
    previous = String(default="")  # the codex this one replaced, of either kind
    status = String(default="pending", indexed=True)  # pending | active | inactive | reverted
    activates_at = Integer(default=0)  # seconds: when a pending codex takes effect
    installed_at = Integer(default=0)
    proposal = String(default="")  # the proposal that adopted it


class ChapterSource(Row):
    """The text of a chapter, or of a ``calendar.json``, stored once under its checksum."""

    __alias__ = "sha256"
    # Rows realm_export returns at most in a page: one row can be 256 KB, and a few of them pass a query's instruction
    # limit or the reply's size.
    export_most = 1
    sha256 = String()  # sha256:<hex> of the text's UTF-8 bytes
    source = String(default="")


class InstallJob(Row):
    """One install of a layered codex: where it has got to, and why it stopped if it did."""

    __alias__ = "id"
    id = String()
    proposal = String(default="")
    change_list = Json({})  # {url, checksum}
    step = String(default="")
    staged = Json({})  # what the steps so far have produced, for the next one
    status = String(default="running", indexed=True)  # running | paused | failed | done
    error = String(default="")
    started_at = Integer(default=0)
    finished_at = Integer(default=0)
