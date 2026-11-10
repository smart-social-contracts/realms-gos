"""Links between realms (docs/codices/layered-codex.md §8, §11): the sync sources a realm takes legislation from, the
followers that take it from the realm, and the releases it publishes and relays. Data only: the rules are in
api.links, and core.records reads the sync sources' records.

A link has the same fields on both sides: its ``status`` (``requested`` after this side's vote, ``active`` once the
other side's is in too, ``ended``), who ended it and when, ``unsent``, whether the other side still has to be told
(api.links sends the notice again until it is answered), and ``checking``, while the vote is still being checked for a
cycle. A row exists only once this side has voted."""

from ic_python_db import Boolean, Integer, String

from core.db import Json, Row


class SyncSource(Row):
    """A realm this realm takes legislation from, or a stand-in record of a source that runs no realm."""

    __alias__ = "id"
    id = String()  # the sync source's capital, or the url of a stand-in record
    checksum = String(default="")  # of a stand-in record's content: the pin
    name = String(default="")
    status = String(default="requested", indexed=True)  # requested | active | ended
    ended_by = String(default="")
    ended_at = Integer(default=0)
    unsent = Boolean(default=False, indexed=True)
    checking = Boolean(default=False)  # this side's vote is still being checked for a cycle: the other side's notice waits


RECORD_MAX = 1 << 19  # characters of a sync source's record as JSON


class SourceRecord(Row):
    """The published record of a sync source as this realm last read it, and when. It has a row of its own, not a field
    of ``SyncSource``: a record can be hundreds of kilobytes, and a row is read whole by every message that touches it
    (the realm lists its sync sources in ``realm_info``)."""

    __alias__ = "id"
    id = String()  # the sync source's: its capital, or the url of its stand-in record
    source = String(default="")  # the source id of the record's own layer, "" when it has none yet
    releases = Json([], max_length=RECORD_MAX)  # the layers it relays, top to bottom, then its own, oldest first
    checked_at = Integer(default=0)


class Follower(Row):
    """A realm that takes legislation from this one, and what it last reported (its own claims)."""

    __alias__ = "id"
    id = String()  # the follower's capital
    status = String(default="requested", indexed=True)  # requested | active | ended
    ended_by = String(default="")
    ended_at = Integer(default=0)
    unsent = Boolean(default=False, indexed=True)
    checking = Boolean(default=False)  # this side's vote is still being checked for a cycle: the other side's notice waits
    layers = Json([])  # [{version, checksum}]
    compliance = String(default='"unknown"')  # the flag as JSON text (core.compliance)
    population = Integer(default=0)
    reported_at = Integer(default=0)


class Release(Row):
    """One release of one layer, as a record lists it (§3.2): one the realm published of its own layer, or one it
    relays, as it installed it from the record of the sync source that vouches for it."""

    __alias__ = "id"
    id = String()  # <source>@<version>
    source = String(default="")
    version = String(default="")
    relayed = Boolean(default=False, indexed=True)
    rank = Integer(default=0)  # among the relayed releases, top layer first
    published = Integer(default=0)
    effective_from = Integer(default=0)
    commit = String(default="")
    chapters = Json({})  # {domain: sha256:<hex>}
    calendar = String(default="")
    notes = Json({})  # {language: text}
