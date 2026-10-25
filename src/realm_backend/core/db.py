import json

from ic_python_db import Entity, String

PAGE, PAGE_MAX = 100, 500


class Row(Entity):
    """An entity whose indexed fields are indexed from creation, defaults included."""

    def __init__(self, **kw):
        super().__init__(**kw)
        if not kw.get("_loaded"):
            for name in type(self)._indexed_properties():
                value = getattr(self, name)
                if value is not None:
                    self.db().field_index_add(self._type, name, str(value), self._id)


class Fail(Exception):
    """A refusal the caller should see: raised before anything is written."""


class Json(String):
    # Read gives a copy; write by assignment, in-place edits are not saved.
    def __init__(self, default=None, **kw):
        super().__init__(default=json.dumps(default), max_length=kw.pop("max_length", 65536), **kw)

    def __get__(self, obj, t=None):
        return self if obj is None else json.loads(super().__get__(obj, t) or "null")

    def __set__(self, obj, value):
        super().__set__(obj, json.dumps(value))


def find(cls, key):
    row = cls[str(key)] if key not in (None, "") else None
    if row is None:
        raise Fail(f"unknown {cls.__name__} '{key}'")
    return row


def next_id(cls, prefix):
    return f"{prefix}-{cls.max_id() + 1}"


def each(cls, field=None, value=None):
    """Every row, or every row whose indexed ``field`` equals ``value``, a page at a time."""
    cursor = 1
    while cursor is not None:
        if field is None:
            rows = cls.load_some(cursor, PAGE)
            cursor = cursor + PAGE if cursor + PAGE <= cls.max_id() else None
        else:
            rows, cursor = cls.find_by(field, value, from_id=cursor, count=PAGE)
        yield from rows


def page(cls, args, keep=lambda row: True):
    start = max(1, int(args.get("from") or 1))
    count = min(int(args.get("count") or PAGE), PAGE_MAX)
    rows = [r for r in cls.load_some(start, count) if keep(r)]
    more = start + count <= cls.max_id()
    return rows, (start + count if more else None)


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
