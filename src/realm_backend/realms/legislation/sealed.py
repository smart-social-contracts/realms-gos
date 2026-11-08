"""What never changes once made: the SDK's classes, and the values a layer above set."""

from realms.legislation.errors import LegislationError

MappingProxyType = type(
    type.__dict__
)  # a read-only view of a dict; the canister's ``types`` is a stub


class Sealed(type):
    """The metaclass of every SDK class: no attribute of the class can be set or deleted after it is
    made, so no chapter can patch what every other chapter relies on."""

    def __setattr__(cls, name, value):
        raise LegislationError(f"{cls.__name__}.{name}: the SDK's classes never change")

    def __delattr__(cls, name):
        raise LegislationError(f"{cls.__name__}.{name}: the SDK's classes never change")


def freeze(value):
    """``value`` with every list a tuple, every set a frozenset and every dict a read-only view,
    recursively. Only the exact types: a subclass with methods of its own is not data, and
    ``is_data`` refuses it."""
    kind = type(value)
    if kind in (dict, MappingProxyType):
        return MappingProxyType({k: freeze(v) for k, v in value.items()})
    if kind in (list, tuple):
        return tuple(freeze(v) for v in value)
    if kind in (set, frozenset):
        return frozenset(value)
    return value
