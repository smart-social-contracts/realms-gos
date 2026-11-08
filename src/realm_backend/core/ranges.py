"""Range enforcement: a number outside the bounds GGG gives it is refused with InvalidArgument, before anything is
written. The bounds are ggg_ranges.RANGES, generated from the ggg submodule's facts.json by scripts/ggg_candid.py."""
from core.db import Fail
from ggg_ranges import RANGES


def check(op, args):
    """Refuse a number in the arguments ``args`` of ``op`` that lies outside its range."""
    for name, bound in RANGES.get(op, {}).items():
        if args.get(name) is not None:
            within(name, bound, args[name])


def within(path, bound, value):
    """``value`` against its ``bound``: ``(min, max)`` for a number, a dict of the bounds of a record's fields, or
    ``[element]`` for a list. Where Realms GOS keeps a list of records as a map (ggg_api.KEYED), the map's values are the
    elements. A value that is not the shape a bound expects is left to the operation, which refuses it."""
    if isinstance(bound, tuple):
        number = numeral(value)
        if number is not None and not bound[0] <= number <= bound[1]:
            raise Fail(f"{path} is {value}: it must be between {bound[0]} and {bound[1]}")
    elif isinstance(bound, dict):
        if isinstance(value, dict):
            for name, inner in bound.items():
                if value.get(name) is not None:
                    within(f"{path}.{name}", inner, value[name])
    elif isinstance(value, (list, dict)):
        for key, item in (enumerate(value) if isinstance(value, list) else value.items()):
            within(f"{path}[{key}]", bound[0], item)


def numeral(value):
    """The number an argument holds: a number, or the text of an integer, which the operations read with ``int``."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None
