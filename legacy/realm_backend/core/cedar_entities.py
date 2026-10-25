"""Assemble the entity slice a single Cedar decision needs.

Cedar evaluates against an entity store, and the obvious implementation hands it
the whole realm. That does not survive contact with real data: a measured call
costs ~10.1M instructions to parse a store of *twelve* entities, and that cost is
paid before any decision is reached. Handing over thousands would exhaust a query
budget on parsing alone.

So this builds the smallest store that can answer one question: the principal,
the groups it belongs to (which is how role rules are expressed), and the
resource being acted on. Everything else in the realm is irrelevant to that
decision and costs instructions to include.

Since ic-basilisk-toolkit 0.5.1 the projection machinery lives in the toolkit's
``Slicer`` (extracted from this module); what remains here is the realm-specific
part: the ``Realm`` namespace and schema, and a principal entity that loads the
caller's ``ggg.User`` row and its ``UserProfile`` memberships.

This is the temporary shape of it. The intended end state is Cedar reading
entities straight out of the stable map in Rust, with no Python projection and no
JSON at all — see smart-social-contracts/ic-python-db#13. Until the storage format
supports field-level reads, projecting a handful of entities here is affordable:
a decision costs a fraction of a percent of an update call's budget. What would
not be affordable is projecting everything, which is why the slice is narrow
rather than convenient.
"""

from typing import Any, Dict, List, Optional

try:  # pragma: no cover - client-side / partial installs
    from ic_basilisk_toolkit.cedar_slicing import Slicer
except ImportError:  # pragma: no cover
    Slicer = None

# Attributes Cedar must never see, whatever a policy asks for. Encrypted values
# and password material have no business in an authorization decision, and
# including them would put them in a store that policies can read freely.
# (Kept as an alias of the Slicer's rule so existing imports keep working.)
_NEVER_PROJECT = frozenset({"password", "secret", "private_key", "ciphertext"})

_slicer: Optional["Slicer"] = None


def _get_slicer() -> "Slicer":
    global _slicer
    if _slicer is None:
        if Slicer is None:
            raise RuntimeError("ic-basilisk-toolkit Cedar modules unavailable")
        from core.cedar_authz import schema

        schema_text = schema()
        _slicer = Slicer("Realm", schema_text, "User")
    return _slicer


def reset_for_tests() -> None:
    global _slicer
    _slicer = None


def uid(entity_type: str, entity_id: str) -> str:
    """A Cedar entity uid as text, e.g. ``Realm::User::"abc"``.

    This is the form a *request* takes. Entities inside the store use the
    structured form below; Cedar does not accept this one there.
    """
    return _get_slicer().uid(entity_type, entity_id)


def uid_json(entity_type: str, entity_id: str) -> Dict[str, str]:
    """A Cedar entity uid as JSON, which is what the entity store expects."""
    return _get_slicer().uid_json(entity_type, entity_id)


def declared_types() -> frozenset:
    """Entity type names the generated schema declares.

    Used to decide whether a relation can be projected as a reference. A
    reference to a type Cedar never heard of makes it reject the whole store,
    which this module turns into a denial — so an unknown type is dropped
    instead, and a policy reading it simply does not match.
    """
    return _get_slicer().declared_types()


def declared_attrs(entity_type: str) -> Optional[frozenset]:
    """Attribute names the schema declares for *entity_type*.

    ``None`` when the type itself is undeclared. Cedar parses the store
    schema-aware: an entity whose type declares attributes is validated
    against exactly that set, so projecting mixin bookkeeping fields the
    schema never declared (``creator``, ``owner``, ``timestamp_created``, …)
    makes the whole store fail deserialization — and this module turns a
    store error into a denial. Found at the 10k rung: every member write
    denied once policy loading started enforcing (P12).
    """
    return _get_slicer().declared_attrs(entity_type)


def principal_entity(principal_id: str) -> List[Dict[str, Any]]:
    """The calling user plus the profiles it belongs to.

    Profiles are *parents*, not attributes, so a policy says
    ``principal in Realm::UserProfile::"admin"`` and Cedar resolves the
    membership itself. That is the whole reason the generated schema declares
    ``entity User in [Department, UserProfile]``.

    A caller with no matching user still yields an entity. Cedar cannot decide
    about a principal that is absent from the store, and an unknown caller must
    reach the policies as an ordinary principal with no memberships — which
    denies by default — rather than as an error that some caller might learn to
    trigger deliberately.
    """
    parents: List[Dict[str, str]] = []

    try:
        from ggg import User

        user = User[principal_id]
    except Exception:
        user = None

    if user is not None:
        for profile in _profiles_of(user):
            profile_id = getattr(profile, "name", None) or getattr(profile, "id", "")
            if not profile_id:
                continue
            parents.append(uid_json("UserProfile", profile_id))

    return _get_slicer().principal_entity(principal_id, parents, row=user)


def _profiles_of(user: Any) -> List[Any]:
    """The user's profiles, tolerant of how the relation is exposed."""
    for attribute in ("profiles", "user_profiles", "profile"):
        try:
            value = getattr(user, attribute, None)
        except Exception:
            continue
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            return list(value)
        # A to-one relation, or a lazy collection that iterates.
        try:
            return list(value)
        except TypeError:
            return [value]
    return []


def resource_entity(
    entity_type: str, entity_id: str, row: Any = None
) -> List[Dict[str, Any]]:
    """The resource being acted on.

    ``row`` is optional: a decision made *before* loading the row — which is the
    cheap order, since a denial means never loading it — gets an entity with no
    attributes. Policies that only match on type still work; policies reading an
    attribute will not match, which fails closed.
    """
    return _get_slicer().resource_entity(entity_type, entity_id, row)


def slice_for(
    principal_id: str,
    resource_type: str = "",
    resource_id: str = "",
    resource_row: Any = None,
) -> List[Dict[str, Any]]:
    """The complete entity store for one decision."""
    entities = principal_entity(principal_id)
    entities.extend(resource_entity(resource_type, resource_id, resource_row))
    return entities
