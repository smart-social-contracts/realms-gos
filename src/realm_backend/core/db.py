import json

from ic_python_db import Database, Entity, ManyToMany, ManyToOne, OneToOne, String

PAGE, PAGE_MAX = 100, 500
JSON_MAX = 65536  # characters of a Json field, unless it says otherwise
DRAFTS = 10  # how many drafts a read view lists, newest first
# convert_legacy_indexes. The key prefixes of ic-python-db's field and relation indexes. The longest key it looks for:
# the map ignores the max_key_size main.py declares, so nothing stops a longer one, but the realm's are far shorter and
# a list under a longer key is still converted, by the first update that reads it. The ids one call converts, and the
# index entries it looks at, at most (about 245 thousand instructions per id: 2.5 billion; about 40 thousand per entry:
# 0.8 billion). What one call may spend on a single list: it is converted whole, in an update of 40 billion.
INDEX_PREFIXES = ("_fi:", "_ri:")
MAX_KEY = 256
CONVERT_IDS, CONVERT_ROWS, CONVERT_PAGE = 10_000, 20_000, 500
CONVERT_COST_PER_ID, CONVERT_BUDGET = 250_000, 35_000_000_000


class Row(Entity):
    """An entity whose indexed fields are indexed from creation, defaults included."""

    # Many-to-many sides left out of serialize() and so of view() and the export: they can hold as many rows as the
    # realm has members, and ic-python-db refuses to serialize a relation of more than MAX_UNPAGED rows. The other side
    # lists the same links (a User lists its profiles).
    unlisted = ()

    def __init__(self, **kw):
        super().__init__(**kw)
        if not kw.get("_loaded"):
            for name in type(self)._indexed_properties():
                value = getattr(self, name)
                if value is not None:
                    self.db().field_index_add(self._type, name, str(value), self._id)

    def serialize(self):
        """What Entity.serialize gives, without the many-to-many sides in ``unlisted``."""
        if not self.unlisted:
            return super().serialize()
        data = self._serialize_base()
        for cls in reversed(type(self).__mro__):
            for name, prop in vars(cls).items():
                if name.startswith("_") or name in self.unlisted:
                    continue
                if isinstance(prop, (ManyToOne, OneToOne)):
                    ref = self.__dict__.get(f"_rel_{name}")
                    if ref is not None:
                        data[name] = self._resolve_ref_with_alias(ref, prop._get_allowed_types())
                elif isinstance(prop, ManyToMany):
                    ids = self.db().reverse_index_get(self._type, self._id, name)
                    if ids:
                        data[name] = [self._resolve_ref_with_alias(i, prop._get_allowed_types()) for i in ids]
        return data


class Fail(Exception):
    """A refusal the caller should see: raised before anything is written. ``kind`` is GGG's error for it, and
    the reply carries it: Fail itself is a bad argument, the subclasses below the other reasons."""

    kind = "invalid_argument"


class NotFound(Fail):
    """A record the call needs does not exist."""

    kind = "not_found"


class Forbidden(Fail):
    """The caller may not do this, beyond what the permission check decides: not theirs, not a party."""

    kind = "forbidden"


class Conflict(Fail):
    """The call contradicts the current state: already done, closed, still open, in the wrong status."""

    kind = "conflict"


class Unavailable(Fail):
    """The realm cannot do this yet: something it needs is not set up."""

    kind = "unavailable"


class Json(String):
    # Read gives a copy; write by assignment, in-place edits are not saved.
    def __init__(self, default=None, **kw):
        super().__init__(default=json.dumps(default), max_length=kw.pop("max_length", JSON_MAX), **kw)

    def __get__(self, obj, t=None):
        return self if obj is None else json.loads(super().__get__(obj, t) or "null")

    def __set__(self, obj, value):
        super().__set__(obj, json.dumps(value))


def find(cls, key):
    row = cls[str(key)] if key not in (None, "") else None
    if row is None:
        raise NotFound(f"unknown {cls.__name__} '{key}'")
    return row


def next_id(cls, prefix):
    return f"{prefix}-{cls.max_id() + 1}"


def each(cls, field, value):
    """Every row whose indexed ``field`` equals ``value``, a page at a time: only where the matches are bounded."""
    cursor = 1
    while cursor is not None:
        rows, cursor = cls.find_by(field, value, from_id=cursor, count=PAGE)
        yield from rows


def window(args, most=PAGE_MAX):
    """``from`` (an id, default 1) and ``count`` (default PAGE, at most ``most``) of a paginated call."""
    return max(1, int(args.get("from") or 1)), max(1, min(int(args.get("count") or PAGE), most))


def page(cls, args, keep=lambda row: True, most=PAGE_MAX):
    """One id range of ``cls`` (``from``, ``count``, at most ``most``), filtered by ``keep``; ``next`` continues it.

    ``load_some`` skips the ids that were deleted and takes the next rows in their place, so a full page ends at its
    last row's id, not at ``from + count - 1``: ``next`` is the id after that row, or None when the page is not full
    or nothing follows the row."""
    start, count = window(args, most)
    loaded = cls.load_some(start, count)
    following = int(loaded[-1]._id) + 1 if len(loaded) == count else None
    more = following is not None and following <= cls.max_id()
    return [r for r in loaded if keep(r)], (following if more else None)


def page_by(cls, field, value, args, most=PAGE_MAX):
    """One page of the rows whose indexed ``field`` is ``value`` (``from``, ``count``); ``next`` continues it."""
    start, count = window(args, most)
    return cls.find_by(field, value, from_id=start, count=count)


def indexed_ids(cls, field, value, start, count):
    """The ids, ascending, of up to ``count`` rows from id ``start`` whose indexed ``field`` is ``value``: a read of the
    index, no row. ic-python-db cuts its read at ``count`` keys and only then drops the keys that are not entries of the
    list, so a read can come back short with more to follow: it is read again, from after the last id, until ``count``
    ids are in or a read comes back empty, which is the end."""
    ids = []
    while len(ids) < count:
        more = cls.db().field_index_page(cls.get_full_type_name(), field, str(value), start, count - len(ids))
        if not more:
            break
        ids += [int(i) for i in more]
        start = ids[-1] + 1
    return ids


def has_indexed(cls, field, value, row_id):
    """Whether the row ``row_id`` has ``value`` in its indexed ``field``, from the index alone: the row is not read."""
    return indexed_ids(cls, field, value, row_id, 1) == [row_id]


def latest(cls, count=PAGE):
    """The newest ``count`` rows of ``cls``, newest first."""
    last = cls.max_id()
    return cls.load_some(max(1, last - count + 1), count)[::-1] if last else []


def first(cls, field, value):
    """The row with the lowest id whose indexed ``field`` is ``value``, or None."""
    rows, _ = cls.find_by(field, value, count=1)
    return rows[0] if rows else None


def ref(row):
    return f"{type(row).__name__}:{row.id}" if row is not None else ""


def deref(text):
    import ggg

    kind, _, key = (text or "").partition(":")
    cls = getattr(ggg, kind, None)
    if not (isinstance(cls, type) and issubclass(cls, Entity)):
        raise Fail(f"unknown reference '{text}'")
    return find(cls, key)


def view(row, *fields):
    """A row as plain data: the named fields, or every serializable one."""
    data = row.serialize()
    data.pop("_type", None)
    data.pop("_id", None)
    for k, v in list(data.items()):
        if isinstance(v, str) and v[:1] in "[{" and isinstance(getattr(type(row), k, None), Json):
            data[k] = json.loads(v)
    return {k: data.get(k) for k in fields} if fields else data


def index_ranges(after):
    """The (start, end) key ranges that hold index keys, in the order ``convert_legacy_indexes`` walks them, from the
    one holding ``after``. Storage orders keys by length first, so each length of each prefix is a range of its own."""
    here = (after[:4], len(after.encode())) if after else ("", 0)
    for prefix in INDEX_PREFIXES:
        for length in range(len(prefix) + 1, MAX_KEY + 1):
            if (prefix, length) < here:
                continue
            fill = "\x00" * (length - len(prefix))
            yield (after if (prefix, length) == here else prefix + fill), prefix[:-1] + chr(ord(prefix[-1]) + 1) + fill


def convert_legacy_indexes(after="", ids=CONVERT_IDS):
    """Convert, at most ``ids`` ids at a time, the index lists saved as one JSON array each (ic-python-db before 0.13).

    ic-python-db converts such a list when something first reads its index. A query's conversion is discarded with the
    rest of its writes, so every query pays for it again: 2.7 billion instructions for a list of 12,000 ids, and past
    about 20,000 the query traps. An update keeps the conversion, so this is run until ``next`` is None after an upgrade
    from such a release. A list is converted whole, so one longer than ``ids`` is converted alone in its call, and the
    call ends there. ``ids`` is held to what a call can convert (CONVERT_BUDGET at CONVERT_COST_PER_ID, about 140,000).
    A list longer than that is skipped and named in ``refused``: no call converts it, and ic-python-db would run out of
    instructions converting it wherever it is read, so the rest is converted and the walk still ends.

    Returns ``lists`` and ``ids`` converted, the keys ``refused``, and ``next``: the ``after`` of the call that
    continues, None at the end. A converted list is not found again, so a second run converts nothing.

    This belongs in ic-python-db as a public ``Database.convert_legacy_indexes``; it is here until that release.
    """
    db = Database.get_instance()
    most = CONVERT_BUDGET // CONVERT_COST_PER_ID
    ids = min(ids, most)
    lists = converted = seen = 0
    refused = []
    last = after
    for start, end in index_ranges(after):
        while True:
            rows = db._scan(start, end, CONVERT_PAGE)
            for key, value in rows:
                if key == last:  # a range starts at its last key
                    continue
                # An entry of a converted list holds an id: only a custom id, under "|h" and 24 hex digits, can look
                # like an array
                legacy = value.startswith("[") and key[-26:-24] != "|h"
                size = value.count(",") + 1 if legacy else 0
                if seen >= CONVERT_ROWS or (legacy and converted and converted + size > ids):
                    return {"lists": lists, "ids": converted, "refused": refused, "next": last}
                seen += 1
                last = key
                if size > most:
                    refused.append(key)
                elif legacy:
                    db._ix_count(key)
                    lists += 1
                    converted += size
                    if converted >= ids:  # a list longer than ``ids`` has used the call
                        return {"lists": lists, "ids": converted, "refused": refused, "next": last}
            if len(rows) < CONVERT_PAGE:
                break
            start = rows[-1][0]
    return {"lists": lists, "ids": converted, "refused": refused, "next": None}
