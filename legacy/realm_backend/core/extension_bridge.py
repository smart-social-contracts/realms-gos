"""Capability bridge for sandboxed extensions.

``core.codex_bridge`` gave codex *hooks* a way to reach the realm without host
imports. This is the same idea for extension *entry points*, and it is what
makes sandboxing a non-core extension possible at all: a subinterpreter has no
``ggg``/``core``/``basilisk``, so before this module the only sandboxable
extension was one that needed nothing from the realm.

The decisive difference from the codex bridge is **the caller**. An extension
entry point is invoked by an authenticated user, and the host injects that
identity into every verb:

    handler = make_rpc_handler(ext_id, capabilities, caller=ic.caller().to_str())

``caller`` is a closure variable. Sandboxed code cannot set it, override it, or
pass it as a kwarg — :func:`make_rpc_handler` refuses any request carrying an
identity-shaped argument. This is what retires the bug class fixed in phase 0,
where four extensions decided ownership by comparing against a ``user_id`` the
client supplied. Under this bridge an extension cannot *name* a user, so the
comparison it used to get wrong is not expressible.

Shape of the API, per the hybrid design:

**Reads are generic and gated.** ``entity.list`` / ``entity.get`` over a
registry of :class:`EntityPolicy`. Each policy fixes the exposed fields and how
rows are scoped to the caller, and the host applies that scope *before*
returning rows. This is the single place per-record read authorization is
enforced, and it retires the ``X.instances()`` full-scan the extensions do
today.

**Writes are typed.** A generic ``entity.update`` would let an extension write
any field — including reassigning ``user`` to hand itself someone else's row —
and gives nowhere to express invariants like "one territory zone per H3 cell,
realm-wide". So each write is a named verb that owns its field allowlist and
its invariants, host-side.

Unlike codex effects, extension writes execute live over ``rpc`` rather than as
a post-hoc batch. Codex hooks batch because a governance hook's mutations
should reach the host as one ordered, reviewable unit. An extension entry point
is request/response — it needs the id it just created in order to answer — and
the operation-level gate in ``core.extension_access`` has already run by the
time any verb is reachable. Each write is still individually authorized here.

Trust model, unchanged from the codex bridge: everything in this module is
host-side and is the only thing enforcing anything. The in-sandbox ``ggg_sdk``
``ctx`` facade is convenience and is assumed to be fully rewritable by a
hostile extension.
"""

from typing import Any, Callable, Dict, List, Optional

from core import console_bridge as _console_bridge
from core import dept_docs_bridge as _dept_docs_bridge
from core import extension_grants as _extension_grants
from core import justice as _justice
from core import land_bridge as _land_bridge
from core import notification_bridge as _notification_bridge
from core import procurement as _procurement
from core import tax_bridge as _tax_bridge
from core import treasury_bridge as _treasury_bridge
from core.bridge_core import (  # noqa: F401
    BridgeSerializationError,
    check_capability,
    to_plain,
)
from core.call_origin import dispatch, extension_call
from ic_python_logging import get_logger

logger = get_logger("core.extension_bridge")

# Version of the extension-facing bridge contract. An extension declaring
# ``capabilities`` also declares ``ggg_api_version``, so a package built
# against a surface this host no longer offers is refused at install time
# rather than failing mid-call.
EXTENSION_API_VERSION = 1
SUPPORTED_EXTENSION_API_VERSIONS = frozenset({1})


# ---------------------------------------------------------------------------
# Identity: the one thing the sandbox may never supply
# ---------------------------------------------------------------------------

# Kwargs that would let sandboxed code assert who is acting. A verb that needs
# an identity gets it from the host-injected ``caller``; anything arriving
# under one of these names is a bug or an attack, so it is refused loudly
# rather than silently dropped.
RESERVED_KWARGS = frozenset(
    {
        "caller",
        "caller_principal",
        "user_id",
        "user_principal",
        "principal",
        "owner_id",
        "requester",
        "requester_id",
        "acting_user",
    }
)

# Admin verbs still need to *address* a member, which is a different claim
# from *being* one. Those take ``subject``, deliberately outside the set above:
# no exception is carved into the reserved names, and the two ideas stay
# lexically distinct at every call site. A ``subject`` is only ever honoured
# after the caller's operation has been checked.


def caller_has_operation(caller: str, operation: str) -> bool:
    """True when *caller* holds *operation* under the realm's RBAC.

    Wraps ``core.access._check_access`` so a verb can widen what it returns for
    a privileged caller without importing the access module itself.
    """
    if not caller or not operation:
        return False
    try:
        from core.access import _check_access

        return bool(_check_access(caller, operation))
    except Exception as e:
        # Fail closed: an unavailable access layer must not read as "allowed".
        logger.warning(f"caller_has_operation({operation}) failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Read policy — what a type exposes, and how its rows are scoped to the caller
# ---------------------------------------------------------------------------


class EntityPolicy:
    """How one entity type may be read across the bridge.

    ``fields``
        The exact projection returned. Anything not listed never crosses the
        boundary, so adding a field to an entity does not silently widen the
        extension-visible surface.

    ``scope``
        ``"owner"``  — the caller sees only rows they own.
        ``"realm"``  — any holder of the read capability sees every row
                       (realm-public data such as the zoning map).

    ``owner_field``
        Relation naming the owning user. Required for ``scope="owner"`` and for
        the ``mine`` filter.

    ``unscoped_operation``
        Operation that lifts ``scope="owner"``, for registrar/admin tooling.
    """

    def __init__(
        self,
        fields,
        scope="owner",
        owner_field=None,
        unscoped_operation=None,
        filters=(),
    ):
        self.fields = tuple(fields)
        self.scope = scope
        self.owner_field = owner_field
        self.unscoped_operation = unscoped_operation
        self.filters = frozenset(filters)

    @property
    def read_capability(self) -> str:
        return f"entity.read:{self.type_name}"


def _entity_class(type_name: str):
    import ggg

    cls = getattr(ggg, type_name, None)
    if cls is None:
        raise ValueError(f"unknown entity type '{type_name}'")
    return cls


def _owner_id(row, owner_field: Optional[str]) -> Optional[str]:
    if not owner_field:
        return None
    owner = getattr(row, owner_field, None)
    return getattr(owner, "id", None) if owner is not None else None


def _project(row, policy: "EntityPolicy") -> dict:
    """Row -> plain dict, restricted to the policy's declared fields."""
    out: Dict[str, Any] = {}
    for field in policy.fields:
        value = getattr(row, field, None)
        # Relations collapse to their id; a live entity must never cross.
        if (
            value is not None
            and hasattr(value, "id")
            and not isinstance(value, (str, int, float, bool))
        ):
            value = getattr(value, "id", None)
        if value is None or isinstance(value, (str, int, float, bool)):
            out[field] = value
        else:
            out[field] = str(value)
    if policy.owner_field:
        out["owner_id"] = _owner_id(row, policy.owner_field)
    return out


# Registered types. Each entry is a deliberate widening of what sandboxed
# extensions can see, so keep additions small and reviewed.
ENTITY_POLICIES: Dict[str, EntityPolicy] = {
    "Zone": EntityPolicy(
        fields=("id", "h3_index", "name", "description", "zone_type", "metadata"),
        # Territory zones (land-use plan) are realm-public. Parcel geometry
        # lives on Land and is not exposed through the Zone read surface.
        scope="realm",
        owner_field="user",
        filters=("mine", "h3_index", "zone_type"),
    ),
    "Land": EntityPolicy(
        fields=("id", "name", "description", "land_type", "status", "metadata"),
        scope="realm",
        owner_field="owner",
        filters=("mine", "status", "land_type"),
    ),
    "Codex": EntityPolicy(
        fields=("id", "name", "description", "url", "checksum"),
        scope="realm",
        filters=("name",),
    ),
}

for _name, _policy in ENTITY_POLICIES.items():
    _policy.type_name = _name


def read_capabilities() -> List[str]:
    """Every ``entity.read:<Type>`` capability the bridge can grant."""
    return sorted(p.read_capability for p in ENTITY_POLICIES.values())


def _policy_for(type_name: str, capabilities: List[str]) -> EntityPolicy:
    policy = ENTITY_POLICIES.get(type_name)
    if policy is None:
        raise PermissionError(
            f"entity type '{type_name}' is not readable across the bridge"
        )
    if policy.read_capability not in (capabilities or ()):
        raise PermissionError(
            f"capability '{policy.read_capability}' not granted to this "
            f"extension (declare it in the manifest 'capabilities' list)"
        )
    return policy


def _visible_rows(policy: EntityPolicy, caller: str, where: dict):
    """Rows of *policy*'s type the caller may see, after host-applied scoping."""
    cls = _entity_class(policy.type_name)
    rows = list(cls.instances())

    unscoped = policy.scope == "realm" or (
        policy.unscoped_operation
        and caller_has_operation(caller, policy.unscoped_operation)
    )
    if not unscoped:
        rows = [r for r in rows if _owner_id(r, policy.owner_field) == caller]

    # `mine` is resolved against the authenticated caller, never against a
    # value the extension chose.
    if where.get("mine"):
        rows = [r for r in rows if _owner_id(r, policy.owner_field) == caller]

    for key, value in where.items():
        if key == "mine":
            continue
        if key not in policy.filters:
            raise PermissionError(
                f"'{key}' is not a filterable field of {policy.type_name}"
            )
        rows = [r for r in rows if getattr(r, key, None) == value]

    if policy.type_name == "Zone":
        territory = []
        for row in rows:
            try:
                if row.land is not None:
                    continue
            except Exception:
                pass
            territory.append(row)
        rows = territory
    return rows


# ---------------------------------------------------------------------------
# Read verbs
# ---------------------------------------------------------------------------


def _v_entity_list(
    caller="", capabilities=(), type="", where=None, limit=1000, **kwargs
) -> dict:
    """List rows of *type* visible to the caller."""
    policy = _policy_for(type, list(capabilities))
    where = where or {}
    if not isinstance(where, dict):
        raise ValueError("entity.list: 'where' must be an object")
    rows = _visible_rows(policy, caller, where)
    limit = max(1, min(int(limit or 1000), 5000))
    return {
        "rows": [_project(r, policy) for r in rows[:limit]],
        "total": len(rows),
        "truncated": len(rows) > limit,
    }


def _v_entity_get(caller="", capabilities=(), type="", id="", **kwargs):
    """One row of *type* by id, or ``None`` when absent or not visible.

    Absent and forbidden deliberately answer the same, so probing ids cannot
    confirm a row exists.
    """
    policy = _policy_for(type, list(capabilities))
    for row in _visible_rows(policy, caller, {}):
        if getattr(row, "id", None) == id:
            return _project(row, policy)
    return None


def _v_caller_get(caller="", **kwargs) -> dict:
    """The authenticated caller: principal, display name, and coarse role.

    An extension asks the host who is calling rather than being told by its
    own arguments.
    """
    from ggg import User

    user = None
    try:
        user = User[caller]
    except Exception:
        user = None
    return {
        "id": caller,
        "name": getattr(user, "name", None) if user else None,
        "registered": user is not None,
        "is_admin": caller_has_operation(caller, "realm.admin"),
    }


def _v_time_now(**kwargs) -> dict:
    """Consensus time, in nanoseconds and seconds.

    The subinterpreter has no clock of its own, and an extension that computed
    time locally would be non-deterministic across replicas.
    """
    from basilisk import ic

    nanos = int(ic.time())
    return {"nanos": nanos, "seconds": nanos // 1_000_000_000}


def _v_log_write(message="", ext_id="", **kwargs) -> dict:
    """Write a line to the canister log, tagged with the extension id.

    The tag is host-supplied, so an extension cannot forge log lines
    attributed to another one.
    """
    from basilisk import ic

    text = str(message)
    if len(text) > 2000:
        text = text[:2000] + "…"
    ic.print(f"[ext:{ext_id}] {text}")
    return {"logged": True}


# Must match the realm frontend's /identities scope.
PRIVATE_DATA_SCOPE = "user:{principal}:private"


def _require(caller: str, operation: str, verb: str) -> None:
    if not caller_has_operation(caller, operation):
        raise PermissionError(f"{verb} requires the '{operation}' operation")


def _notification_row(n) -> dict:
    """A notification as plain data, with a millisecond timestamp.

    Timestamp parsing used to live in each extension, which is how two of them
    ended up with subtly different date arithmetic.
    """
    from core.time_utils import parse_timestamp_ms

    stamp = 0
    for attr in ("timestamp_created", "timestamp_updated"):
        value = getattr(n, attr, None)
        if value and str(value) != "None":
            stamp = parse_timestamp_ms(str(value))
            if stamp:
                break
    return {
        "id": getattr(n, "_id", None),
        "topic": getattr(n, "topic", "") or "",
        "title": getattr(n, "title", "") or "",
        "message": getattr(n, "message", "") or "",
        "sender": getattr(n, "sender", "") or "",
        "timestamp_ms": stamp,
        "read": bool(getattr(n, "read", False)),
        "icon": getattr(n, "icon", "bell") or "bell",
        "color": getattr(n, "color", "blue") or "blue",
    }


def _notification_owner(n):
    try:
        user = n.user
    except Exception:
        return None
    if not user:
        return None
    return getattr(user, "id", None) or getattr(user, "_id", None)


def _v_member_list(caller="", **kwargs) -> dict:
    """Roster summary for every user. Admin-gated; no private data."""
    _require(caller, "user.view", "member.list")
    from ggg import User

    members = []
    for user in User.instances():
        member = getattr(user, "member", None)
        human = getattr(user, "human", None)
        try:
            profiles = [p.name for p in user.profiles]
        except Exception:
            profiles = []
        members.append(
            {
                "principal": user.id,
                "nickname": getattr(user, "nickname", "") or "",
                "avatar": getattr(user, "avatar", "") or "",
                "human_name": (getattr(human, "name", "") or "") if human else "",
                "profiles": profiles,
                "has_member": member is not None,
                "identity_verification": (
                    (getattr(member, "identity_verification", "") or "")
                    if member
                    else ""
                ),
                "tax_compliance": (
                    (getattr(member, "tax_compliance", "") or "") if member else ""
                ),
                "is_active": (
                    bool(member.is_active())
                    if member and hasattr(member, "is_active")
                    else False
                ),
            }
        )
    return {"members": members, "total": len(members)}


def _v_member_profile(caller="", subject="", **kwargs) -> dict:
    """Full profile for one member.

    ``private_data_ciphertext`` is opaque to the canister: it is only
    decryptable by a client that also holds a key envelope for the scope, so
    returning it here discloses nothing on its own.
    """
    _require(caller, "user.view", "member.profile")
    from ggg import Notification, User

    if not subject:
        raise ValueError("subject is required")
    user = User[subject]
    if not user:
        raise ValueError(f"User '{subject}' not found")

    try:
        profiles = [p.name for p in user.profiles]
    except Exception:
        profiles = []

    member = getattr(user, "member", None)
    member_data = None
    if member:
        record = getattr(member, "criminal_record", "") or ""
        member_data = {
            "id": getattr(member, "_id", None),
            "identity_verification": getattr(member, "identity_verification", "") or "",
            "tax_compliance": getattr(member, "tax_compliance", "") or "",
            "residence_permit": getattr(member, "residence_permit", "") or "",
            "voting_eligibility": getattr(member, "voting_eligibility", "") or "",
            "public_benefits_eligibility": getattr(
                member, "public_benefits_eligibility", ""
            )
            or "",
            "has_zk_passport": record.startswith("clean|zk:") or "|zk:" in record,
            "is_active": (
                bool(member.is_active()) if hasattr(member, "is_active") else False
            ),
        }

    human = getattr(user, "human", None)
    human_data = None
    identities = []
    if human:
        human_data = {
            "name": getattr(human, "name", "") or "",
            "date_of_birth": getattr(human, "date_of_birth", "") or "",
            "latitude": getattr(human, "latitude", None),
            "longitude": getattr(human, "longitude", None),
        }
        try:
            identities = [
                {"type": getattr(i, "type", "") or ""} for i in human.identities
            ]
        except Exception:
            identities = []

    count = sum(
        1 for n in Notification.instances() if _notification_owner(n) == subject
    )

    return {
        "principal": user.id,
        "nickname": getattr(user, "nickname", "") or "",
        "avatar": getattr(user, "avatar", "") or "",
        "home_quarter": getattr(user, "home_quarter", "") or "",
        "profiles": profiles,
        "member": member_data,
        "human": human_data,
        "identities": identities,
        "notification_count": count,
        "private_data_ciphertext": getattr(user, "private_data", "") or "",
        "private_data_scope": PRIVATE_DATA_SCOPE.format(principal=subject),
    }


def _v_member_notifications(caller="", subject="", **kwargs) -> dict:
    """One member's notification history, newest first. Admin-gated."""
    _require(caller, "user.view", "member.notifications")
    from ggg import Notification

    if not subject:
        raise ValueError("subject is required")
    rows = [
        _notification_row(n)
        for n in Notification.instances()
        if _notification_owner(n) == subject
    ]
    rows.sort(key=lambda r: r["timestamp_ms"], reverse=True)
    return {"notifications": rows, "total": len(rows)}


def _v_crypto_envelope(caller="", scope="", **kwargs) -> dict:
    """The *caller's* key envelope for a scope, if one was shared with them.

    Scoped to the caller by construction: the principal passed to
    ``get_envelope`` is the authenticated one, so this cannot fetch another
    admin's envelope.
    """
    if not scope:
        raise ValueError("scope is required")
    from api.crypto import get_envelope

    result = get_envelope(caller, scope) or {}
    wrapped = result.get("wrapped_dek") if result.get("success") else None
    return {
        "has_access": bool(wrapped),
        "wrapped_dek": wrapped,
        "scope": scope,
    }


def _v_system_snapshot(caller="", sections=None, **kwargs) -> dict:
    """Operational diagnostics: cycles, memory, entity counts, file counts.

    Realm-wide operational state rather than member data, so it is gated by a
    single admin operation instead of per-record scoping. Gathering happens
    host-side (see :mod:`core.system_snapshot`) so extension code never gets
    to walk the filesystem itself.
    """
    if not caller_has_operation(caller, "realm.admin"):
        raise PermissionError("system.snapshot requires the 'realm.admin' operation")
    from core import system_snapshot

    return system_snapshot.snapshot(sections)


def _v_realm_info(caller="", **kwargs) -> dict:
    """How this realm is addressed from outside: its own principal, the registry
    it is registered with, and the running version.

    All of it is on-chain or otherwise public, so the gate is the general
    read-the-realm operation rather than an admin one. It is a verb at all
    because ``ic.id()`` is a host call and the registry principal lives in the
    realm's own DB, neither of which a subinterpreter can reach.
    """
    if not caller_has_operation(caller, "realm.data_view"):
        raise PermissionError("realm.info requires the 'realm.data_view' operation")

    canister_id = ""
    try:
        from _cdk import ic

        canister_id = str(ic.id())
    except Exception:
        pass

    registry_id = ""
    try:
        from api.registry import get_registry_info

        for entry in get_registry_info().get("registries") or []:
            principal = (entry.get("principal_id") or "").strip()
            if principal:
                registry_id = principal
                break
    except Exception:
        pass

    version = ""
    try:
        from api.status import get_status

        version = str((get_status() or {}).get("version", ""))
    except Exception:
        pass

    return {
        "canister_id": canister_id,
        "registry_canister_id": registry_id,
        "version": version,
    }


def _v_schema_entities(**kwargs) -> dict:
    """The realm's entity-relationship schema as plain data.

    Field names and relationships only — never rows — so this is realm
    metadata rather than member data, and needs no caller scoping.

    The reflection has to happen host-side: it inspects live ORM descriptors,
    which by design cannot cross into a subinterpreter.
    """
    import ggg
    from ic_python_db import (
        Boolean,
        Entity,
        Float,
        Integer,
        ManyToMany,
        ManyToOne,
        OneToMany,
        OneToOne,
        String,
        TimestampedMixin,
    )

    scalars = (String, Integer, Boolean, Float)
    relations = (OneToOne, OneToMany, ManyToOne, ManyToMany)

    entities: Dict[str, Any] = {}
    for name in dir(ggg):
        if name.startswith("_"):
            continue
        cls = getattr(ggg, name, None)
        if not isinstance(cls, type) or not issubclass(cls, Entity) or cls is Entity:
            continue

        fields, rels = [], {}
        for attr_name in dir(cls):
            if attr_name.startswith("_"):
                continue
            try:
                attr = getattr(cls, attr_name)
            except Exception:
                continue
            if isinstance(attr, scalars):
                fields.append(attr_name)
            elif isinstance(attr, relations):
                target = getattr(attr, "entity_types", None) or "Unknown"
                if isinstance(target, (list, tuple)) and target:
                    target = target[0]
                rels[attr_name] = {
                    "type": type(attr).__name__,
                    "target": str(getattr(target, "__name__", target)),
                    "field": getattr(attr, "reverse_name", None),
                }

        if issubclass(cls, TimestampedMixin):
            fields.extend(["created_at", "updated_at"])
        if "id" not in fields:
            fields.insert(0, "id")

        entities[cls.__name__] = {"fields": sorted(set(fields)), "relationships": rels}

    return {"entities": entities}


def _v_schema_describe(capabilities=(), **kwargs) -> dict:
    """The entity types and fields this extension may read.

    Lets a schema explorer render what it can actually fetch instead of
    reflecting over live ORM classes.
    """
    caps = set(capabilities or ())
    return {
        name: {
            "fields": list(p.fields),
            "scope": p.scope,
            "filters": sorted(p.filters),
        }
        for name, p in sorted(ENTITY_POLICIES.items())
        if p.read_capability in caps
    }


# ---------------------------------------------------------------------------
# Typed write verbs — field allowlists and invariants stay host-side
# ---------------------------------------------------------------------------

ZONE_TYPES = (
    "unassigned",
    "residential",
    "commercial",
    "agricultural",
    "industrial",
    "public",
    "mixed",
)

# Writable through zone.create / zone.update. `user` is absent on purpose:
# ownership is set from the caller and can never be reassigned by a verb.
_ZONE_WRITABLE = ("name", "description", "zone_type", "metadata")


def _is_territory_zone(zone) -> bool:
    """Territory zones classify realm land; parcel geometry belongs to a Land."""
    try:
        return zone.land is None
    except Exception:
        return True


def _require_zone_type(value: str) -> str:
    zone_type = (value or "unassigned").strip().lower()
    if zone_type not in ZONE_TYPES:
        raise ValueError(f"zone_type must be one of {', '.join(ZONE_TYPES)}")
    return zone_type


def _owned_zone_or_denied(h3_index: str, caller: str):
    """The territory zone at *h3_index*, once the caller is allowed to change it.

    The ownership comparison that four extensions got wrong now happens here,
    against the host's own idea of who is calling.
    """
    from ggg import Zone

    zone = None
    for z in Zone.instances():
        if z.h3_index == h3_index and _is_territory_zone(z):
            zone = z
            break
    if zone is None:
        raise ValueError("Zone not found")

    owner_id = _owner_id(zone, "user")
    if owner_id != caller and not caller_has_operation(caller, "realm.admin"):
        raise PermissionError("You don't have permission to modify this zone")
    return zone


def _v_zone_create(caller="", h3_index="", **kwargs) -> dict:
    """Create a territory zone owned by the caller."""
    from ggg import User, Zone

    if not h3_index:
        raise ValueError("h3_index is required")
    zone_type = _require_zone_type(kwargs.get("zone_type"))

    user = User[caller]
    if user is None:
        raise ValueError("caller is not a registered user")

    # Invariant: one territory zone per cell, realm-wide. It has to live here —
    # an extension enforcing it in the sandbox could simply not.
    for existing in Zone.instances():
        if existing.h3_index == h3_index and _is_territory_zone(existing):
            raise ValueError("A zone already exists at this location")

    leftover = None
    try:
        leftover = Zone[h3_index]
    except Exception:
        leftover = None
    if leftover is not None:
        try:
            if leftover.land is not None:
                leftover.land = None
                leftover.name = kwargs.get("name") or "Zone"
                leftover.description = kwargs.get("description") or ""
                leftover.zone_type = zone_type
                leftover.metadata = kwargs.get("metadata") or "{}"
                leftover.user = user
                _land_bridge.sync_parcels_covering([h3_index])
                return _project(leftover, ENTITY_POLICIES["Zone"])
        except Exception:
            pass

    zone = Zone(
        h3_index=h3_index,
        name=kwargs.get("name") or "Zone",
        description=kwargs.get("description") or "",
        zone_type=zone_type,
        metadata=kwargs.get("metadata") or "{}",
        user=user,
    )
    # Return the projection so the caller needs no follow-up read. A Zone's
    # entity ``id`` is not its h3_index, so a round-trip through entity.get
    # would not find it.
    _land_bridge.sync_parcels_covering([h3_index])
    return _project(zone, ENTITY_POLICIES["Zone"])


def _v_zone_update(caller="", h3_index="", **kwargs) -> dict:
    """Update a territory zone the caller owns (or any, for a realm admin)."""
    zone = _owned_zone_or_denied(h3_index, caller)
    old_zone_type = getattr(zone, "zone_type", None)

    updated = []
    for field in _ZONE_WRITABLE:
        if field not in kwargs:
            continue
        value = kwargs[field]
        if field == "zone_type":
            value = _require_zone_type(value)
        setattr(zone, field, value)
        updated.append(field)

    if "zone_type" in updated and getattr(zone, "zone_type", None) != old_zone_type:
        _land_bridge.sync_parcels_covering([h3_index])
    return dict(_project(zone, ENTITY_POLICIES["Zone"]), updated_fields=updated)


def _v_zone_delete(caller="", h3_index="", **kwargs) -> dict:
    """Delete a territory zone the caller owns (or any, for a realm admin)."""
    zone = _owned_zone_or_denied(h3_index, caller)
    zone.delete()
    _land_bridge.sync_parcels_covering([h3_index])
    return {"id": h3_index, "deleted": True}


# ---------------------------------------------------------------------------
# Extension-owned entities
# ---------------------------------------------------------------------------
#
# Five extensions (passport_verification, department_docs, procurement,
# managed_services, justice_litigation) declare their own storage with
# ``create_extension_entity_class``, which returns a live ORM class. A live
# class obviously cannot cross into a subinterpreter, and that looked like it
# might block sandboxing them outright.
#
# It does not, because the extension never needed the class — it needed two
# separable things:
#
#   1. *Declaring* the schema. That is pure declarative metadata (field names,
#      types, lengths, the alias), so it moves out of ``entry.py`` and into the
#      manifest, where the host registers it at install time. No sandbox
#      involvement at all.
#
#   2. *CRUD over its own rows*. That is the verbs below.
#
# What makes this safe is namespacing. ``create_extension_entity_class`` stores
# under ``ext_<extension>::<Class>``, so an extension's own tables are already
# disjoint from every other extension's and from ``ggg``. Authorization is
# therefore structural: the host derives the namespace from the *calling
# extension id*, never from an argument, so generic CRUD here cannot reach
# another extension's data. That is precisely why generic writes are safe for
# extension-owned types while shared ``ggg`` types need typed verbs.

_FIELD_TYPES = ("String", "Integer", "Float", "Boolean")


def declared_entities(manifest: dict) -> Dict[str, Any]:
    """The ``entities`` block of a manifest, validated into plain schema data.

    Shape::

        "entities": {
          "AppConfig": {
            "alias": "key",
            "fields": {"key": {"type": "String", "max_length": 256},
                       "value": {"type": "String"}}
          }
        }
    """
    # Absent is fine; present-but-wrong-type is not. `or {}` alone would let a
    # list quietly read as "declares nothing".
    raw = (manifest or {}).get("entities")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("manifest 'entities' must be an object")

    out: Dict[str, Any] = {}
    for name, spec in raw.items():
        if not isinstance(name, str) or not name.isidentifier():
            raise ValueError(f"invalid entity name '{name}'")
        fields = (spec or {}).get("fields") or {}
        if not isinstance(fields, dict) or not fields:
            raise ValueError(f"entity '{name}' declares no fields")
        clean = {}
        for field, fspec in fields.items():
            if not isinstance(field, str) or not field.isidentifier():
                raise ValueError(f"invalid field name '{field}' on '{name}'")
            ftype = (fspec or {}).get("type", "String")
            if ftype not in _FIELD_TYPES:
                raise ValueError(
                    f"'{name}.{field}': type must be one of "
                    f"{', '.join(_FIELD_TYPES)}"
                )
            clean[field] = {"type": ftype}
            if "max_length" in (fspec or {}):
                clean[field]["max_length"] = int(fspec["max_length"])
        out[name] = {"alias": (spec or {}).get("alias"), "fields": clean}
    return out


def register_declared_entities(ext_id: str, manifest: dict) -> List[str]:
    """Build real ORM classes for an extension's declared entities.

    Runs host-side at install/boot, turning the manifest's plain schema into
    the same namespaced classes ``create_extension_entity_class`` produced —
    so storage layout is unchanged and existing rows keep working.
    """
    from core.extensions import create_extension_entity_class
    from ic_python_db import Boolean, Float, Integer, String

    kinds = {"String": String, "Integer": Integer, "Float": Float, "Boolean": Boolean}
    base = create_extension_entity_class(ext_id)
    registered = []
    for name, spec in declared_entities(manifest).items():
        attrs: Dict[str, Any] = {}
        for field, fspec in spec["fields"].items():
            kind = kinds[fspec["type"]]
            attrs[field] = (
                kind(max_length=fspec["max_length"])
                if "max_length" in fspec
                else kind()
            )
        if spec.get("alias"):
            attrs["__alias__"] = spec["alias"]
        _EXT_ENTITY_CLASSES[(ext_id, name)] = type(name, (base,), attrs)
        registered.append(name)
    return registered


# (ext_id, class_name) -> live class. Populated host-side only.
_EXT_ENTITY_CLASSES: Dict[Any, Any] = {}


def _own_entity_class(ext_id: str, name: str):
    """The calling extension's own entity class.

    ``ext_id`` comes from the host's dispatch, not from the sandbox, so this
    cannot be pointed at another extension's namespace.

    Rebuilds from the manifest on a miss: the class map is in-memory, so it is
    empty after a canister upgrade even though the extension is still
    installed and its rows still exist.
    """
    cls = _EXT_ENTITY_CLASSES.get((ext_id, name))
    if cls is None:
        try:
            from core.runtime_extensions import get_all_extension_manifests

            manifest = (get_all_extension_manifests() or {}).get(ext_id)
            if manifest and name in declared_entities(manifest):
                register_declared_entities(ext_id, manifest)
                cls = _EXT_ENTITY_CLASSES.get((ext_id, name))
        except Exception:
            cls = None
    if cls is None:
        raise PermissionError(
            f"'{name}' is not an entity declared by extension '{ext_id}'"
        )
    return cls


def own_entity_class(ext_id: str, name: str):
    """A declared extension-owned entity class, for typed verbs that need to
    apply their own authorization to the rows (see :mod:`core.dept_docs_bridge`).
    """
    return _own_entity_class(ext_id, name)


def _own_fields(ext_id: str, name: str) -> List[str]:
    from core.runtime_extensions import get_all_extension_manifests

    manifest = (get_all_extension_manifests() or {}).get(ext_id) or {}
    return list(declared_entities(manifest).get(name, {}).get("fields", {}))


def _project_own(row, fields: List[str]) -> dict:
    out = {"id": getattr(row, "id", None)}
    for field in fields:
        value = getattr(row, field, None)
        out[field] = (
            value
            if isinstance(value, (str, int, float, bool)) or value is None
            else str(value)
        )
    return out


def _v_ext_entity_create(ext_id="", type="", values=None, **kwargs) -> dict:
    cls = _own_entity_class(ext_id, type)
    fields = _own_fields(ext_id, type)
    values = values or {}
    unknown = sorted(set(values) - set(fields))
    if unknown:
        raise ValueError(f"'{type}' has no field(s): {', '.join(unknown)}")
    row = cls(**{k: v for k, v in values.items()})
    return _project_own(row, fields)


def _v_ext_entity_list(ext_id="", type="", where=None, limit=1000, **kwargs) -> dict:
    cls = _own_entity_class(ext_id, type)
    fields = _own_fields(ext_id, type)
    rows = list(cls.instances())
    for key, value in (where or {}).items():
        if key not in fields:
            raise ValueError(f"'{type}' has no field '{key}'")
        rows = [r for r in rows if getattr(r, key, None) == value]
    limit = max(1, min(int(limit or 1000), 5000))
    return {
        "rows": [_project_own(r, fields) for r in rows[:limit]],
        "total": len(rows),
    }


def _v_ext_entity_get(ext_id="", type="", id="", **kwargs):
    cls = _own_entity_class(ext_id, type)
    fields = _own_fields(ext_id, type)
    row = cls[id]
    return _project_own(row, fields) if row is not None else None


def _v_ext_entity_update(ext_id="", type="", id="", values=None, **kwargs) -> dict:
    cls = _own_entity_class(ext_id, type)
    fields = _own_fields(ext_id, type)
    row = cls[id]
    if row is None:
        raise ValueError(f"'{type}' row '{id}' not found")
    updated = []
    for key, value in (values or {}).items():
        if key not in fields:
            raise ValueError(f"'{type}' has no field '{key}'")
        setattr(row, key, value)
        updated.append(key)
    return dict(_project_own(row, fields), updated_fields=updated)


def _v_ext_entity_delete(ext_id="", type="", id="", **kwargs) -> dict:
    cls = _own_entity_class(ext_id, type)
    row = cls[id]
    if row is None:
        raise ValueError(f"'{type}' row '{id}' not found")
    row.delete()
    return {"id": id, "deleted": True}


# name -> implementation. Every entry widens the extension-facing API, so keep
# additions small and reviewed.
VERBS: Dict[str, Callable[..., Any]] = {
    "time.now": _v_time_now,
    "log.write": _v_log_write,
    "system.snapshot": _v_system_snapshot,
    "member.list": _v_member_list,
    "member.profile": _v_member_profile,
    "member.notifications": _v_member_notifications,
    "crypto.envelope": _v_crypto_envelope,
    **_land_bridge.VERBS,
    **_extension_grants.VERBS,
    **_notification_bridge.VERBS,
    **_console_bridge.VERBS,
    **_dept_docs_bridge.VERBS,
    **_treasury_bridge.VERBS,
    **_tax_bridge.VERBS,
    **_procurement.VERBS,
    **_justice.VERBS,
    "ext_entity.create": _v_ext_entity_create,
    "ext_entity.list": _v_ext_entity_list,
    "ext_entity.get": _v_ext_entity_get,
    "ext_entity.update": _v_ext_entity_update,
    "ext_entity.delete": _v_ext_entity_delete,
    "entity.list": _v_entity_list,
    "entity.get": _v_entity_get,
    "caller.get": _v_caller_get,
    "schema.describe": _v_schema_describe,
    "schema.entities": _v_schema_entities,
    "realm.info": _v_realm_info,
    "zone.create": _v_zone_create,
    "zone.update": _v_zone_update,
    "zone.delete": _v_zone_delete,
}

# Verbs that only read. Kept explicit rather than inferred so a new write verb
# cannot become readable by accident.
READ_VERBS = (
    frozenset(
        {
            "entity.list",
            "entity.get",
            "caller.get",
            "schema.describe",
            "schema.entities",
            "realm.info",
            "ext_entity.list",
            "ext_entity.get",
            "time.now",
            "log.write",
            "system.snapshot",
            "member.list",
            "member.profile",
            "member.notifications",
            "crypto.envelope",
            "land.list",
            "land.get",
            "land.map",
            "extension_access.list",
            "notification.list",
            "notification.departments",
            "notification.email_settings",
            "notification.pending_emails",
            "console.overview",
            "console.list_citizen_invites",
        }
    )
    | _dept_docs_bridge.READ_VERBS
    | _treasury_bridge.READ_VERBS
    | _tax_bridge.READ_VERBS
    | _procurement.READ_VERBS
    | _justice.READ_VERBS
)

WRITE_VERBS = frozenset(VERBS) - READ_VERBS

# Writes that are bookkeeping rather than something a person did.
_NOT_ACTIVITY = frozenset({"log.write"})

# Verbs whose behaviour depends on which entity types the extension declared,
# so they receive the capability list itself.
_CAPABILITY_AWARE = frozenset({"entity.list", "entity.get", "schema.describe"})

# Verbs scoped to the calling extension's own namespace. The id is supplied by
# the host's dispatch, so these can never be aimed at another extension.
_EXT_SCOPED = frozenset(
    {
        "ext_entity.create",
        "ext_entity.list",
        "ext_entity.get",
        "ext_entity.update",
        "ext_entity.delete",
        "log.write",
    }
)


def known_verbs() -> List[str]:
    """Every capability name the extension bridge recognises.

    Includes ``service.call:*``, which is not an rpc verb — sandboxed code
    cannot invoke an outcall directly, it asks for one and the host performs it
    between rounds (see :mod:`core.async_bridge`). It is a declared capability
    all the same, because it is authority the manifest has to ask for.
    """
    from core.async_bridge import service_capabilities

    return sorted(set(VERBS) | set(read_capabilities()) | set(service_capabilities()))


def authorize(action: str, capabilities: List[str]) -> Optional[str]:
    """Return an error string if *action* is not permitted, else ``None``."""
    return check_capability(action, capabilities, frozenset(VERBS), subject="extension")


# ---------------------------------------------------------------------------
# The rpc handler — one per extension call, closed over the real caller
# ---------------------------------------------------------------------------


def _cedar_check(action: str, caller: str, kwargs: dict) -> None:
    """Ask the realm's policies about this call, if policies are enforcing.

    A second gate, not a replacement for the capability check above. The
    capability check answers "did this extension declare it wants this verb";
    this answers "may this caller do it to this row, given what the realm
    forbids". An extension can hold a capability it may not exercise.

    Only the generic entity verbs are gated here. They name a core resource
    directly, which is what the guardrails turn on: G1 refuses extension
    writes to core state and G2 refuses reads of authorization data, both
    keying on the resource *type* the verb carries.

    Named RPC verbs (``justice.file_case``, ``procurement.award``, …) and the
    extension-namespaced ``ext_entity.*`` verbs are NOT gated here: they are
    capability-checked above and run their authorization host-side in Python,
    which is where G1's own commentary says those writes live. Collapsing
    them to ``write`` made G1 forbid every extension write — including the
    first-party extensions the realm ships — the moment enforcement became
    active (found at the 10k E2E rung: ``sue_user`` denied before ever
    reaching the host verb).
    """
    from core import cedar_authz

    if not cedar_authz.enabled():
        return

    if action not in (
        "entity.get",
        "entity.list",
        "entity.create",
        "entity.update",
        "entity.delete",
    ):
        return

    resource_type = str(kwargs.get("type") or "")
    # A listing names no row, but it must still reach Cedar as a resource of
    # that *type*, or a rule refusing to expose profiles would let
    # `entity.list(type="UserProfile")` past — the case it most needs to
    # catch. The placeholder id stands for "any row of this type".
    resource_id = str(kwargs.get("id") or "") or ("*" if resource_type else "")

    cedar_authz.check(
        caller,
        cedar_authz.action_for(action, action in READ_VERBS),
        resource_type,
        resource_id,
    )


def make_rpc_handler(
    ext_id: str,
    capabilities: List[str],
    caller: str,
    allow_writes: bool = True,
):
    """Build the host-side ``rpc`` handler for one sandboxed extension call.

    Basilisk invokes this synchronously as ``handler(context_id, action,
    kwargs)`` whenever sandboxed code calls ``rpc(...)``. Every request is
    checked for a registered verb, a declared capability, and an absent
    identity kwarg, then run with the host's *own* ``caller``.

    Raising propagates into the sandbox as an exception the extension may
    catch; it can never turn into an unauthorized read or write.

    *allow_writes* is ``False`` for effect-driven (async) calls, whose body the
    host replays once per outcall round. A write there would be applied once per
    round with no way to roll back, so it is refused at the handler rather than
    only omitted from ``allowed_actions`` — the C-level gate and this one should
    not be the same single point of failure.
    """
    caps = list(capabilities or ())

    def handler(ctx_id: str, action: str, kwargs: Any) -> Any:
        if not isinstance(action, str):
            raise PermissionError("rpc action must be a string")

        auth_error = authorize(action, caps)
        if auth_error:
            logger.warning(
                f"extension_bridge[{ext_id}]: denied '{action}': {auth_error}"
            )
            raise PermissionError(f"rpc '{action}' denied: {auth_error}")

        if not allow_writes and action in WRITE_VERBS:
            from core.async_bridge import ASYNC_WRITE_RULE

            raise PermissionError(
                f"rpc '{action}' denied: {ASYNC_WRITE_RULE}. Move the write "
                f"into a separate synchronous entry point."
            )

        safe_kwargs = to_plain(kwargs or {})
        if not isinstance(safe_kwargs, dict):
            raise PermissionError(f"rpc '{action}' kwargs must be an object")

        # Identity is the host's to state. Refuse rather than drop, so an
        # extension author sees the mistake instead of silently acting as
        # someone else's request would have.
        supplied = sorted(set(safe_kwargs) & RESERVED_KWARGS)
        if supplied:
            raise PermissionError(
                f"rpc '{action}' may not carry {', '.join(supplied)}: the "
                f"caller is supplied by the host, never by the extension"
            )

        if action in _CAPABILITY_AWARE:
            safe_kwargs["capabilities"] = caps
        if action in _EXT_SCOPED:
            # Overwrites any ``ext_id`` the sandbox tried to set, so an
            # extension cannot address another extension's namespace.
            safe_kwargs["ext_id"] = ext_id

        # The origin travels with the call so a Cedar decision downstream can
        # tell an extension apart from host code. Without it the guardrails
        # keyed on `context.extension` silently pass.
        # Asked inside an origin, because the guardrails only fire when the
        # context says the call came from an extension. Asked *before* the verb
        # runs, so a refusal costs nothing and reads nothing. Each `with` needs
        # its own context manager — these are single-use.
        with extension_call(ext_id):
            _cedar_check(action, caller, safe_kwargs)

        result = dispatch(
            VERBS, action, extension_call(ext_id), caller=caller, **safe_kwargs
        )
        if action in WRITE_VERBS and action not in _NOT_ACTIVITY:
            try:
                from core.activity import record

                record(caller)
            except Exception as e:
                logger.warning(f"extension_bridge[{ext_id}]: activity not recorded: {e}")
        return to_plain(result)

    return handler
