"""Generate the realm Cedar schema from ggg entity definitions at runtime.

The schema describes every entity type the ORM stores so Cedar can validate
policies and entity slices. It is derived from ``build_schema()`` output rather
than committed as a hand-written file, so entity changes cannot silently drift
from what the authorizer expects.
"""

from typing import Any, Dict, List, Optional, Tuple, Type

_SCHEMA_CACHE: Optional[str] = None

NAMESPACE = "Realm"
PRINCIPAL_TYPE = "User"

# Relations that become Cedar's ``in`` hierarchy — the two the realm's Python
# checks already treat as membership (departments, profiles).
MEMBERSHIPS = {
    "User": ["departments", "profiles"],
}

# Generic read/write plus named actions for verbs Cedar must distinguish.
ACTIONS = {
    "read": "read",
    "write": "write",
    "entity.get": "read",
    "entity.list": "read",
    "entity.create": "write",
    "entity.update": "write",
    "entity.delete": "write",
    "appeal.decide": "write",
}

# Facts about the request rather than the data.
CONTEXT = {
    "extension": "String",
    "repl": "Bool",
}


def _log_warning(message: str) -> None:
    try:
        from ic_python_logging import get_logger

        get_logger(__name__).warning(message)
    except Exception:
        print(message)


def _ensure_schema_deps():
    """Re-import ggg and ic_python_db when test-suite MagicMocks poison sys.modules.

    No-op on WASI, where ``unittest.mock`` is absent and MagicMock pollution
    cannot occur.
    """
    import sys

    try:
        from unittest.mock import MagicMock
    except ImportError:
        return

    if not isinstance(sys.modules.get("ic_python_db"), MagicMock):
        return

    for prefix in (
        "ic_python_db",
        "ic_basilisk_toolkit",
        "ic_python_logging",
        "ggg",
    ):
        for name in list(sys.modules):
            if name == prefix or name.startswith(prefix + "."):
                del sys.modules[name]


def collect_ggg_schema_entities() -> Tuple[List[Type], Dict[str, Any]]:
    """Entity classes and a Cedar-safe schema dict keyed by ``Class.__name__``.

    ``ic_python_db.schema.build_schema`` keys rows by ``get_full_type_name()``,
    which is ``namespace::Class`` when ``__namespace__`` is set. Cedar treats
    ``::`` as its own namespace separator, so those keys cannot go into the
    realm schema. They are dropped here instead of failing the whole REPL.
    """
    _ensure_schema_deps()
    import ggg
    from ic_python_db import Entity
    from ic_python_db.schema import build_schema

    names = list(ggg.__all__)
    if "User" not in names:
        names = ["User", *names]

    candidates: List[Type] = []
    seen = set()
    for name in names:
        cls = getattr(ggg, name, None)
        if not isinstance(cls, type) or not issubclass(cls, Entity):
            continue
        if cls in seen:
            continue
        seen.add(cls)
        candidates.append(cls)

    included: List[Type] = []
    for cls in candidates:
        try:
            build_schema({cls.__name__: cls})
        except Exception as exc:
            _log_warning(
                f"cedar_schema: excluding {cls.__name__} from schema: {exc}"
            )
            continue
        included.append(cls)

    user_cls = getattr(ggg, "User", None)
    if (
        isinstance(user_cls, type)
        and issubclass(user_cls, Entity)
        and user_cls not in included
    ):
        included.insert(0, user_cls)

    raw = build_schema({cls.__name__: cls for cls in included})
    schema_dict: Dict[str, Any] = {}
    kept: List[Type] = []
    for cls in included:
        full_name = cls.get_full_type_name()
        if "::" in full_name:
            _log_warning(
                f"cedar_schema: dropping namespaced type {full_name!r}"
            )
            continue
        desc = raw.get(full_name) or raw.get(cls.__name__)
        if desc is None:
            continue
        schema_dict[cls.__name__] = desc
        kept.append(cls)
    return kept, schema_dict


def generate_realm_cedar_schema() -> str:
    """Build Cedar schema text from the current ggg entity definitions."""
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is not None:
        return _SCHEMA_CACHE

    from ic_basilisk_toolkit.cedar_schema import generate_cedar_schema

    _kept, schema_dict = collect_ggg_schema_entities()
    if PRINCIPAL_TYPE not in schema_dict:
        raise ValueError(
            f"principal type {PRINCIPAL_TYPE!r} is not among the entity types"
        )
    known = set(schema_dict)
    memberships = {
        typ: list(rels)
        for typ, rels in MEMBERSHIPS.items()
        if typ in known
    }
    text, _report = generate_cedar_schema(
        schema_dict,
        namespace=NAMESPACE,
        principal_type=PRINCIPAL_TYPE,
        memberships=memberships,
        actions=ACTIONS,
        context=CONTEXT,
    )
    _SCHEMA_CACHE = text
    return text


def reset_for_tests() -> None:
    global _SCHEMA_CACHE
    _SCHEMA_CACHE = None
