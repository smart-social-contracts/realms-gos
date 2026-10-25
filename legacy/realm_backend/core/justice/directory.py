"""Local realm directory (issue #325).

``directory_list`` is this canister only. Do not federate defendant
autocomplete across quarters — that saturates gossip (issue #156).
Cross-Mundus defendants are a ``realm://`` address, not a directory walk.
"""

from typing import Any, Dict, List

from ic_python_logging import get_logger

logger = get_logger("core.justice.directory")


def list_local_entries() -> List[Dict[str, Any]]:
    """Users and departments on **this** canister. Never walks federation members."""
    from ggg import Department, User

    entries: List[Dict[str, Any]] = []
    try:
        users = User.instances()
    except Exception:
        users = []
    for u in users:
        principal = getattr(u, "id", None)
        if not principal:
            continue
        human = getattr(u, "human", None)
        human_name = ""
        if human is not None:
            human_name = (
                getattr(human, "name", None)
                or getattr(human, "full_name", None)
                or ""
            )
        entries.append(
            {
                "kind": "user",
                "principal": str(principal),
                "label": human_name or (getattr(u, "nickname", "") or "") or str(principal),
            }
        )

    try:
        depts = Department.instances()
    except Exception:
        depts = []
    for d in depts:
        name = getattr(d, "name", "") or ""
        if not name:
            continue
        head = getattr(d, "head", None)
        head_principal = str(getattr(head, "id", "")) if head is not None else ""
        entries.append(
            {
                "kind": "department",
                "principal": head_principal,
                "label": name,
                "id": str(getattr(d, "_id", "") or ""),
            }
        )
    return entries
