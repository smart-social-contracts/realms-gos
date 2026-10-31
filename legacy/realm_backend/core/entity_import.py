"""Entity bulk import ordering — topological sort by ORM relations (issue #14).

Import records are sorted so referents exist before referees:
  1. Type-level ordering from ManyToOne metadata on registered entity classes.
  2. Record-level ordering when a batch references other records in the same import
     (e.g. Department.parent, Vote.proposal).

Consumers: import_export ``import_data``, CLI ``realms import``.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from ic_python_db import Database, ManyToMany, ManyToOne, OneToOne
from ic_python_db.properties import Relation
from ic_python_logging import get_logger

logger = get_logger("core.entity_import")

RecordKey = Tuple[str, str]  # (_type, id)


def _entity_classes(db: Optional[Database] = None) -> Dict[str, type]:
    db = db or Database.get_instance()
    seen: Dict[str, type] = {}
    for cls in db._entity_types.values():
        seen[cls.__name__] = cls
    return seen


def build_type_dependency_graph(db: Optional[Database] = None) -> Dict[str, List[str]]:
    """Return ``{dependent_type: [referenced_types...]}`` from ManyToOne fields."""
    classes = _entity_classes(db)
    graph: Dict[str, Set[str]] = {name: set() for name in classes}

    for type_name, cls in classes.items():
        for _attr, prop in _relation_fields(cls).items():
            if not isinstance(prop, ManyToOne):
                continue
            for target in _allowed_types(prop):
                if target in classes and target != type_name:
                    graph[type_name].add(target)

    return {k: sorted(v) for k, v in graph.items() if v}


def build_field_dependency_graph(
    db: Optional[Database] = None,
) -> Dict[str, Dict[str, List[str]]]:
    """Return ``{type: {field_name: [target_types...]}}`` for relation fields."""
    classes = _entity_classes(db)
    graph: Dict[str, Dict[str, List[str]]] = {}

    for type_name, cls in classes.items():
        fields: Dict[str, List[str]] = {}
        for attr, prop in _relation_fields(cls).items():
            if isinstance(prop, (ManyToOne, OneToOne, ManyToMany)):
                targets = [t for t in _allowed_types(prop) if t in classes]
                if targets:
                    fields[attr] = targets
        if fields:
            graph[type_name] = fields

    return graph


def get_import_type_graph(db: Optional[Database] = None) -> Dict[str, Any]:
    """API-friendly dependency graph for frontend import ordering."""
    type_deps = build_type_dependency_graph(db)
    field_deps = build_field_dependency_graph(db)
    return {
        "dependencies": type_deps,
        "fields": field_deps,
        "types": sorted(set(type_deps.keys()) | set(field_deps.keys())),
    }


def _relation_fields(cls: type) -> Dict[str, Relation]:
    fields: Dict[str, Relation] = {}
    for base in reversed(getattr(cls, "__mro__", ())):
        for name, val in base.__dict__.items():
            if name.startswith("_"):
                continue
            if isinstance(val, (ManyToOne, OneToOne, ManyToMany)):
                fields[name] = val
    return fields


def _allowed_types(prop: Relation) -> List[str]:
    if hasattr(prop, "_get_allowed_types"):
        return list(prop._get_allowed_types())
    raw = getattr(prop, "entity_types", None)
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    return list(raw)


def _record_id(record: dict) -> Optional[str]:
    raw = record.get("_id")
    if raw is None or raw == "":
        return None
    return str(raw)


def _record_key(record: dict) -> Optional[RecordKey]:
    entity_type = record.get("_type")
    entity_id = _record_id(record)
    if not entity_type or entity_id is None:
        return None
    return str(entity_type), entity_id


def _ref_value_to_id(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, dict):
        if value.get("_id") is not None:
            return str(value["_id"])
        return None
    if isinstance(value, (list, tuple)):
        return None
    return str(value)


def _batch_index(records: Iterable[dict]) -> Dict[RecordKey, int]:
    index: Dict[RecordKey, int] = {}
    for i, record in enumerate(records):
        key = _record_key(record)
        if key and key not in index:
            index[key] = i
    return index


def _record_dependencies(
    record: dict,
    batch_keys: Set[RecordKey],
    classes: Dict[str, type],
) -> Set[RecordKey]:
    entity_type = record.get("_type")
    if not entity_type:
        return set()

    cls = classes.get(str(entity_type))
    if not cls:
        return set()

    deps: Set[RecordKey] = set()
    for field_name, prop in _relation_fields(cls).items():
        if field_name not in record:
            continue
        value = record[field_name]
        if value is None:
            continue

        if isinstance(prop, ManyToMany):
            items = value if isinstance(value, list) else [value]
            for item in items:
                ref_id = _ref_value_to_id(item)
                if not ref_id:
                    continue
                for target_type in _allowed_types(prop):
                    key = (target_type, ref_id)
                    if key in batch_keys:
                        deps.add(key)
            continue

        if not isinstance(prop, (ManyToOne, OneToOne)):
            continue

        ref_id = _ref_value_to_id(value)
        if not ref_id:
            continue
        for target_type in _allowed_types(prop):
            key = (target_type, ref_id)
            if key in batch_keys:
                deps.add(key)

    return deps


def _topological_sort_types(
    types_present: Set[str],
    type_graph: Dict[str, List[str]],
) -> List[str]:
    """Kahn's algorithm — referenced types before dependents."""
    deps: Dict[str, Set[str]] = {
        t: {p for p in type_graph.get(t, []) if p in types_present}
        for t in types_present
    }
    reverse: Dict[str, Set[str]] = defaultdict(set)
    for dependent, parents in deps.items():
        for parent in parents:
            reverse[parent].add(dependent)

    ready = deque(sorted(t for t in types_present if not deps[t]))
    order: List[str] = []

    while ready:
        node = ready.popleft()
        order.append(node)
        for child in sorted(reverse.get(node, ())):
            deps[child].discard(node)
            if not deps[child]:
                ready.append(child)

    if len(order) < len(types_present):
        remaining = sorted(types_present - set(order))
        logger.warning(
            f"Type dependency cycle detected; appending in stable order: {remaining}"
        )
        order.extend(remaining)

    return order


def topological_sort_records(
    records: List[dict],
    db: Optional[Database] = None,
) -> Tuple[List[dict], List[str]]:
    """Sort import records so referents are deserialized first.

    Returns ``(sorted_records, warnings)``.
    Records without ``_type`` / ``_id`` keep their relative order at the end.
    """
    if not records:
        return [], []

    db = db or Database.get_instance()
    classes = _entity_classes(db)
    type_graph = build_type_dependency_graph(db)

    keyed: List[Tuple[int, dict, RecordKey]] = []
    unkeyed: List[dict] = []
    for i, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        key = _record_key(record)
        if key:
            keyed.append((i, record, key))
        else:
            unkeyed.append(record)

    if not keyed:
        warnings: List[str] = []
        if unkeyed:
            warnings.append(
                f"{len(unkeyed)} record(s) missing _type/_id — cannot import as entities"
            )
        return unkeyed, warnings

    batch_keys = {key for _i, _r, key in keyed}
    types_present = {key[0] for _i, _r, key in keyed}
    type_order = _topological_sort_types(types_present, type_graph)
    type_rank = {t: i for i, t in enumerate(type_order)}

    n = len(keyed)
    prereqs: Dict[int, Set[int]] = {idx: set() for idx in range(n)}
    key_to_idx = {key: idx for idx, (_i, _r, key) in enumerate(keyed)}

    warnings: List[str] = []

    for idx, (_orig_i, record, key) in enumerate(keyed):
        for dep_key in _record_dependencies(record, batch_keys, classes):
            dep_idx = key_to_idx.get(dep_key)
            if dep_idx is not None and dep_idx != idx:
                prereqs[idx].add(dep_idx)

    remaining_in = {idx: len(prereqs[idx]) for idx in range(n)}
    reverse: Dict[int, Set[int]] = defaultdict(set)
    for idx, parents in prereqs.items():
        for p in parents:
            reverse[p].add(idx)

    def _sort_key(idx: int) -> Tuple[int, int]:
        orig_i, _record, key = keyed[idx]
        return (type_rank.get(key[0], len(type_rank)), orig_i)

    ready = deque(
        sorted(
            [idx for idx, count in remaining_in.items() if count == 0], key=_sort_key
        )
    )
    sorted_idx: List[int] = []

    while ready:
        idx = ready.popleft()
        sorted_idx.append(idx)
        for child in sorted(reverse.get(idx, ()), key=_sort_key):
            remaining_in[child] -= 1
            if remaining_in[child] == 0:
                ready.append(child)

    if len(sorted_idx) < n:
        unsorted = sorted(set(range(n)) - set(sorted_idx), key=_sort_key)
        warnings.append(
            f"Record dependency cycle detected; {len(unsorted)} record(s) appended in stable order"
        )
        sorted_idx.extend(unsorted)

    if unkeyed:
        warnings.append(
            f"{len(unkeyed)} record(s) missing _type/_id — appended last in original order"
        )

    sorted_records = [keyed[i][1] for i in sorted_idx] + unkeyed
    return sorted_records, warnings


def chunk_records(records: List[dict], batch_size: int = 200) -> List[List[dict]]:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    return [records[i : i + batch_size] for i in range(0, len(records), batch_size)]


def plan_import_batches(
    records: List[dict],
    batch_size: int = 200,
    db: Optional[Database] = None,
) -> Dict[str, Any]:
    """Sort records topologically, then split into fixed-size batches."""
    sorted_records, warnings = topological_sort_records(records, db=db)
    batches = chunk_records(sorted_records, batch_size=batch_size)
    return {
        "total_records": len(sorted_records),
        "batch_count": len(batches),
        "batch_size": batch_size,
        "warnings": warnings,
        "batches": batches,
    }


def merge_import_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate per-batch ``process_bulk_import`` reports."""
    total_records = 0
    successful = 0
    failed = 0
    errors: List[str] = []

    for result in results:
        total_records += int(result.get("total_records") or 0)
        successful += int(result.get("successful") or 0)
        failed += int(result.get("failed") or 0)
        errors.extend(result.get("errors") or [])

    return {
        "total_records": total_records,
        "successful": successful,
        "failed": failed,
        "errors": errors[:20],
    }


def apply_import_records(records: List[dict]) -> Dict[str, Any]:
    """Deserialize records, honouring ``_action: delete`` on Department rows."""
    from ic_python_db import Entity

    successful = 0
    failed = 0
    errors: List[str] = []

    for record in records:
        if not isinstance(record, dict):
            failed += 1
            errors.append("skipped non-object record")
            continue
        action = (
            str(record.get("_action") or record.get("action") or "").strip().lower()
        )
        entity_type = str(record.get("_type") or "")
        if action in {"delete", "destroy"} and entity_type == "Department":
            from core.department_admin import destroy_department

            name = str(record.get("name") or record.get("_id") or "").strip()
            result = destroy_department(name)
            if result.get("success"):
                successful += 1
            else:
                failed += 1
                errors.append(
                    f"Department#{name}: {result.get('error') or 'delete failed'}"
                )
            continue
        try:
            Entity.deserialize(record, level=1)
            successful += 1
        except Exception as e:
            logger.error(f"Error creating entity: {e}")
            failed += 1
            errors.append(f"{record.get('_type', '?')}#{record.get('_id', '?')}: {e}")

    return {"successful": successful, "failed": failed, "errors": errors[:10]}
