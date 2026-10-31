"""Realm URI — the universal cross-canister address.

A ``RealmRef`` is the canonical way to point at any entity living in any
canister (quarter or realm) in the network::

    realm://<canister_id>/<EntityType>/<id>

Examples::

    realm://abc123-dead-beef-cai/User/alice-principal
    realm://abc123-dead-beef-cai/Proposal/42
    realm://token-canister-id/Transfer/tx_001

Design notes (see issue #156):

* The same format addresses both **cross-quarter** and **cross-realm**
  targets — a canister id is a canister id. Only the *locator fallback*
  differs by scope (gossip vs registry), not the address.
* People are normally referenced by their **IC principal**, not by a
  ``User`` ref, because users are mobile and principals are global.
* References are always stored **absolute** (with the canister id). Bare
  local ids are ambiguous and forbidden in ``*_ref`` fields.

This module is intentionally dependency-free (stdlib only, no ``re``) so it
runs unchanged inside the restricted canister Python runtime and in plain
CPython unit tests.
"""

SCHEME = "realm://"


class InvalidRealmRef(ValueError):
    """Raised when a string is not a well-formed ``realm://`` URI."""


class RealmRef:
    """An immutable, parsed ``realm://<canister>/<Type>/<id>`` reference."""

    __slots__ = ("canister_id", "entity_type", "entity_id")

    def __init__(self, canister_id, entity_type, entity_id):
        if not canister_id:
            raise InvalidRealmRef("canister_id is required")
        if not entity_type:
            raise InvalidRealmRef("entity_type is required")
        if entity_id is None or entity_id == "":
            raise InvalidRealmRef("entity_id is required")
        # ``/`` would break round-tripping of the canister/type segments.
        if "/" in canister_id or "/" in entity_type:
            raise InvalidRealmRef("canister_id and entity_type may not contain '/'")
        self.canister_id = canister_id
        self.entity_type = entity_type
        self.entity_id = entity_id

    @classmethod
    def parse(cls, uri):
        """Parse a ``realm://`` URI into a :class:`RealmRef`.

        Raises :class:`InvalidRealmRef` on any malformed input.
        """
        if not isinstance(uri, str):
            raise InvalidRealmRef("ref must be a string")
        text = uri.strip()
        if not text.startswith(SCHEME):
            raise InvalidRealmRef(
                "ref must start with '%s' (got %r)" % (SCHEME, uri)
            )
        rest = text[len(SCHEME):]
        # Split into exactly 3 segments: canister / type / id.
        # The id may itself contain '/', so split at most twice.
        parts = rest.split("/", 2)
        if len(parts) != 3:
            raise InvalidRealmRef(
                "ref must be realm://<canister>/<Type>/<id> (got %r)" % uri
            )
        canister_id, entity_type, entity_id = parts
        return cls(canister_id, entity_type, entity_id)

    @classmethod
    def try_parse(cls, uri):
        """Like :meth:`parse` but returns ``None`` instead of raising."""
        try:
            return cls.parse(uri)
        except InvalidRealmRef:
            return None

    @classmethod
    def is_ref(cls, uri):
        """Return True if ``uri`` is a well-formed realm ref."""
        return cls.try_parse(uri) is not None

    def format(self):
        """Serialize back to the canonical ``realm://`` string."""
        return "%s%s/%s/%s" % (
            SCHEME,
            self.canister_id,
            self.entity_type,
            self.entity_id,
        )

    def is_local(self, self_canister_id):
        """True if this ref points at ``self_canister_id`` (this canister)."""
        return bool(self_canister_id) and self.canister_id == self_canister_id

    def with_canister(self, canister_id):
        """Return a new ref for the same entity on a different canister."""
        return RealmRef(canister_id, self.entity_type, self.entity_id)

    def __eq__(self, other):
        return (
            isinstance(other, RealmRef)
            and self.canister_id == other.canister_id
            and self.entity_type == other.entity_type
            and self.entity_id == other.entity_id
        )

    def __hash__(self):
        return hash((self.canister_id, self.entity_type, self.entity_id))

    def __str__(self):
        return self.format()

    def __repr__(self):
        return "RealmRef(%r, %r, %r)" % (
            self.canister_id,
            self.entity_type,
            self.entity_id,
        )


def make_ref(canister_id, entity_type, entity_id):
    """Convenience constructor returning the canonical URI string."""
    return RealmRef(canister_id, entity_type, entity_id).format()
