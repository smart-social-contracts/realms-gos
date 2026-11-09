"""The storage of a layered codex (docs/codices/layered-codex.md §9.1). Data only: the rules are in
core.layered_codex."""

from ic_python_db import Integer, String

from core.db import Json, Row


class LayeredCodex(Row):
    """The legislation a realm runs, built from the chapters of several sources. Its ``id`` names the legislation, not
    this realm's copy of it: ``sha256:`` and the checksum of its layers, layer paths and chapters' checksums."""

    __alias__ = "id"
    __version__ = 2  # 2: voting_rule
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
    # The realm's rule over the votes cast and its voting window as they were when this codex became active: what decides
    # a vote to revert to it (§10.1). {} for a codex that became active before the realm recorded them.
    voting_rule = Json({})
    # {chapter file: {method: {count, error, at}}}: the methods failing now, cleared as each succeeds again (§9.3)
    failures = Json({})
    # The operations a forbid of this legislation names, as the last load found them (§9.4): while it does not load,
    # the realm refuses these, reads among them, as it would have judged them.
    forbids = Json([])

    @classmethod
    def migrate(cls, obj, from_version, to_version):
        obj.setdefault("voting_rule", {})  # a Json field a stored row lacks would read as the text of its default
        return obj


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
    proposal = String(default="", indexed=True)
    change_list = Json({})  # {url, checksum}
    step = String(default="")
    # What the steps so far have produced, for the next one: checksums, where the bytes came from, and the fields a
    # re-installed legislation's row had before, to put back on failure (its chapters alone can fill a Json's default).
    staged = Json({}, max_length=1 << 18)
    status = String(default="running", indexed=True)  # running | paused | failed | done
    error = String(default="")
    started_at = Integer(default=0)
    finished_at = Integer(default=0)
