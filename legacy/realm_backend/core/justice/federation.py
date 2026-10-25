"""Inter-quarter Justice pipe (issue #325).

Host owns ``federation_message`` transport. These handlers are the default
meaning of ``justice.transfer`` and ``justice.restitution`` when no Codex
``on_federation_message`` hook claims the topic.

* **Transfer** is a judge act on the origin docket: freeze + dest canister
  pointer, then send ciphertext (never plaintext title/description). Dest
  creates the live Case. Case is not an EntityMigration subject.
* **Restitution** is dest-as-bailiff: collect from the defendant, then ask
  the plaintiff's home quarter to credit them. Fine stays in dest treasury.
  No P2P. No paying from an empty treasury. No ack → penalty stays pending.

A sender is injectable so unit tests do not need IC. The live send is
``send_justice_message`` (async generator).
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional

from ic_python_logging import get_logger

logger = get_logger("core.justice.federation")

TOPIC_TRANSFER = "justice.transfer"
TOPIC_RESTITUTION = "justice.restitution"

# Tests (and leftover-free hosts) inject a sync ``(target, topic, body) -> dict``.
_outbound_sender: Optional[Callable[..., Dict[str, Any]]] = None
# Collection seam: ``(penalty) -> bool``. Default: no funds / not collected.
_collect_fn: Optional[Callable[[Any], bool]] = None


def set_outbound_sender(fn: Optional[Callable[..., Dict[str, Any]]]) -> None:
    global _outbound_sender
    _outbound_sender = fn


def set_collect_fn(fn: Optional[Callable[[Any], bool]]) -> None:
    global _collect_fn
    _collect_fn = fn


def deliver(target_canister_id: str, topic: str, body: dict) -> Dict[str, Any]:
    """Sync send seam. Returns the dest response, or a queued/failure dict."""
    target = (target_canister_id or "").strip()
    if not target:
        return {"success": False, "error": "dest canister id is required"}
    if _outbound_sender is not None:
        return _outbound_sender(target, topic, body or {})
    return {
        "success": False,
        "error": "no federation sender",
        "queued": True,
        "target": target,
        "topic": topic,
        "body": body or {},
    }


def send_justice_message(target_canister_id: str, topic: str, body: dict):
    """Live IC send. Drive with ``yield from``."""
    from core.federation import send_federation_message

    return (yield from send_federation_message(target_canister_id, topic, body))


def handle_justice_topic(topic: str, source: str, body: dict) -> Optional[Dict[str, Any]]:
    """Host default for the two Justice topics. ``None`` → not ours."""
    if topic == TOPIC_TRANSFER:
        return accept_transfer(source, body or {})
    if topic == TOPIC_RESTITUTION:
        return accept_restitution(source, body or {})
    return None


# ---------------------------------------------------------------------------
# Dest pointer / addresses
# ---------------------------------------------------------------------------


def dest_canister_id(dest) -> str:
    """Judge-supplied dest canister id. Not a filer venue picker.

    Filer address fields (``defendant_ref``, ``defendant_quarter_id``,
    leftover ``lives_in``) are ignored. A dest *string* may be a canister
    id or a ``realm://`` dest the judge typed.
    """
    if dest is None:
        return ""
    if isinstance(dest, dict):
        for key in ("id", "canister_id", "dest_quarter_id"):
            value = dest.get(key)
            if value:
                return dest_canister_id(value)
        return ""
    text = str(dest).strip()
    if not text:
        return ""
    try:
        from core.realm_ref import RealmRef

        ref = RealmRef.try_parse(text)
        if ref is not None:
            return (ref.canister_id or "").strip()
    except Exception:
        pass
    return text


def parse_user_address(value) -> Dict[str, str]:
    """``realm://<canister>/User/<principal>`` is an address, not a venue.

    Local principal paste still works. No federated autocomplete.
    """
    text = str(value or "").strip()
    if not text:
        return {"principal": "", "canister_id": "", "ref": ""}
    try:
        from core.realm_ref import RealmRef

        ref = RealmRef.try_parse(text)
        if ref is not None and ref.entity_type == "User":
            return {
                "principal": str(ref.entity_id),
                "canister_id": (ref.canister_id or "").strip(),
                "ref": text,
            }
    except Exception:
        pass
    return {"principal": text, "canister_id": "", "ref": ""}


def _self_id() -> str:
    try:
        from _cdk import ic

        return ic.id().to_str()
    except Exception:
        return ""


def _parse_json(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _write_json(entity, data: dict, field: str = "metadata") -> None:
    setattr(entity, field, json.dumps(data))


# ---------------------------------------------------------------------------
# Transfer body (origin) / accept (dest)
# ---------------------------------------------------------------------------


def transfer_body(
    case,
    *,
    ciphertext: str = "",
    wrapped_deks=None,
    origin_scope: str = "",
) -> Dict[str, Any]:
    """Ciphertext + parties snapshot. Never plaintext title/description."""
    from core.justice import content, projections, roles

    meta = projections.parse_metadata(case)
    filer = roles.principal_of(getattr(case, "plaintiff", None)) or ""
    plaintiff = filer
    defendant = roles.principal_of(getattr(case, "defendant", None)) or str(
        meta.get("defendant_principal") or ""
    )
    blob = ciphertext
    scope = origin_scope
    if not blob or not scope:
        try:
            record = content.find(getattr(case, "_id", None))
        except Exception:
            record = None
        if record is not None:
            blob = blob or (getattr(record, "ciphertext", None) or "")
            scope = scope or (getattr(record, "scope", None) or "")
    if not scope and filer:
        try:
            scope = content.scope_for(filer, case._id)
        except Exception:
            scope = ""

    body: Dict[str, Any] = {
        "origin_case_id": str(getattr(case, "_id", "") or ""),
        "origin_case_number": getattr(case, "case_number", "") or "",
        "ciphertext": blob or "",
        "origin_scope": scope or "",
        "plaintiff_id": plaintiff,
        "defendant_id": defendant,
        "filer_id": filer,
    }
    if wrapped_deks:
        body["wrapped_deks"] = wrapped_deks
    ref = meta.get("defendant_ref") or ""
    if ref:
        body["defendant_ref"] = ref
    quarter = meta.get("defendant_quarter_id") or ""
    if quarter:
        body["defendant_quarter_id"] = quarter
    return body


def find_live_case(origin_canister: str, origin_case_id: str):
    from core.justice.projections import parse_metadata
    from ggg import Case

    origin_canister = (origin_canister or "").strip()
    origin_case_id = str(origin_case_id or "").strip()
    if not origin_canister or not origin_case_id:
        return None
    try:
        rows = list(Case.instances())
    except Exception:
        return None
    for case in rows:
        meta = parse_metadata(case)
        if (
            str(meta.get("origin_canister") or "") == origin_canister
            and str(meta.get("origin_case_id") or "") == origin_case_id
        ):
            return case
    return None


def _ensure_user(principal: str):
    from ggg import User

    principal = (principal or "").strip()
    if not principal:
        return None
    try:
        existing = User[principal]
    except Exception:
        existing = None
    if existing is not None:
        return existing
    try:
        return User(id=principal)
    except Exception as e:
        logger.error(f"accept_transfer: could not create User {principal}: {e}")
        return None


def _attach_ciphertext(case, filer: str, ciphertext: str, wrapped_deks) -> None:
    from core.justice import content

    if not ciphertext and not wrapped_deks:
        return
    try:
        record = content.find(case._id)
        if record is None:
            content.create(case._id, filer or "")
            record = content.find(case._id)
        if record is not None:
            if ciphertext:
                record.ciphertext = str(ciphertext)
            if wrapped_deks:
                extra = _parse_json(getattr(record, "metadata", None))
                extra["wrapped_deks"] = wrapped_deks
                extra["rewrap_for"] = ["justice", "filer"]
                if hasattr(record, "metadata"):
                    _write_json(record, extra)
    except Exception as e:
        logger.warning(f"accept_transfer: ciphertext attach skipped: {e}")


def accept_transfer(source: str, body: dict) -> Dict[str, Any]:
    """Dest: create the live Case. Origin stays frozen; do not execute there."""
    from core.justice import courts
    from core.justice.projections import parse_metadata
    from ggg import case_file

    origin_case_id = str(body.get("origin_case_id") or "").strip()
    if not origin_case_id:
        return {"success": False, "error": "origin_case_id is required"}

    existing = find_live_case(source, origin_case_id)
    if existing is not None:
        return {
            "success": True,
            "live_case_id": str(existing._id),
            "live_case_number": existing.case_number or "",
            "duplicate": True,
        }

    court = courts.preferred_court() or courts.ensure_default_court()
    if court is None:
        court = courts.preferred_court()
    if court is None:
        return {"success": False, "error": "No court available to accept transfer"}

    plaintiff = _ensure_user(str(body.get("plaintiff_id") or body.get("filer_id") or ""))
    defendant = _ensure_user(str(body.get("defendant_id") or ""))
    if plaintiff is None:
        return {"success": False, "error": "plaintiff_id is required"}

    meta = {
        "origin_canister": (source or "").strip(),
        "origin_case_id": origin_case_id,
        "origin_case_number": str(body.get("origin_case_number") or ""),
        "origin_scope": str(body.get("origin_scope") or ""),
        "transferred_in": True,
    }
    if body.get("defendant_ref"):
        meta["defendant_ref"] = body["defendant_ref"]
    if body.get("defendant_quarter_id"):
        meta["defendant_quarter_id"] = body["defendant_quarter_id"]
    if body.get("wrapped_deks"):
        meta["wrapped_deks"] = body["wrapped_deks"]

    live = case_file(
        court=court,
        plaintiff=plaintiff,
        defendant=defendant,
        title="",
        description="",
        metadata=json.dumps(meta),
    )
    _attach_ciphertext(
        live,
        str(body.get("filer_id") or body.get("plaintiff_id") or ""),
        str(body.get("ciphertext") or ""),
        body.get("wrapped_deks"),
    )
    logger.info(
        f"Accepted justice.transfer from {source}: "
        f"origin {origin_case_id} -> live {live.case_number}"
    )
    return {
        "success": True,
        "live_case_id": str(live._id),
        "live_case_number": live.case_number or "",
        "duplicate": False,
    }


def record_transfer_ack(case, dest_canister: str, response: dict) -> None:
    from core.justice.projections import parse_metadata

    data = parse_metadata(case)
    pointer = data.get("transfer_dest")
    if not isinstance(pointer, dict):
        pointer = {}
    pointer["id"] = dest_canister or pointer.get("id") or ""
    if response.get("success"):
        if response.get("live_case_id"):
            pointer["live_case_id"] = str(response["live_case_id"])
        if response.get("live_case_number"):
            pointer["live_case_number"] = str(response["live_case_number"])
        data["transfer_acked"] = True
    else:
        data["transfer_acked"] = False
        if response.get("error"):
            data["transfer_send_error"] = str(response["error"])[:200]
    data["transfer_dest"] = pointer
    case.metadata = json.dumps(data)


def notify_transfer(
    case,
    dest,
    *,
    ciphertext: str = "",
    wrapped_deks=None,
    origin_scope: str = "",
) -> Dict[str, Any]:
    """After freeze: send ``justice.transfer``. Dest creates the live Case."""
    target = dest_canister_id(dest)
    if not target:
        return {"success": False, "error": "dest canister id is required"}
    if target == _self_id():
        return {"success": False, "error": "dest must be another canister"}

    body = transfer_body(
        case,
        ciphertext=ciphertext,
        wrapped_deks=wrapped_deks,
        origin_scope=origin_scope,
    )
    response = deliver(target, TOPIC_TRANSFER, body)
    record_transfer_ack(case, target, response)
    return response


# ---------------------------------------------------------------------------
# Restitution: collect on dest, credit on plaintiff's home
# ---------------------------------------------------------------------------


def _penalty_meta(penalty) -> dict:
    return _parse_json(getattr(penalty, "metadata", None))


def _write_penalty_meta(penalty, data: dict) -> None:
    _write_json(penalty, data)


def collect_from_defendant(penalty) -> bool:
    """True when dest has collected from the defendant.

    Does not debit the Justice treasury. No P2P. No gift from empty pocket.
    """
    meta = _penalty_meta(penalty)
    if meta.get("collected") is True:
        return True
    if _collect_fn is not None:
        try:
            return bool(_collect_fn(penalty))
        except Exception as e:
            logger.error(f"collect_from_defendant: {e}")
            return False
    return False


def plaintiff_home_quarter(case) -> str:
    from core.federation import resolve_home_quarter
    from core.justice import roles

    plaintiff = roles.principal_of(getattr(case, "plaintiff", None)) if case else ""
    home = resolve_home_quarter(plaintiff) if plaintiff else ""
    return (home or "").strip()


def credit_plaintiff_locally(body: dict) -> Dict[str, Any]:
    """Record a restitution credit. Does not pay from this quarter's treasury."""
    plaintiff = str(body.get("plaintiff_id") or "").strip()
    amount = body.get("amount")
    if not plaintiff:
        return {"success": False, "error": "plaintiff_id is required"}
    user = _ensure_user(plaintiff)
    if user is None:
        return {"success": False, "error": f"plaintiff {plaintiff} is not a user"}
    extra = _parse_json(getattr(user, "metadata", None)) if hasattr(user, "metadata") else {}
    credits = list(extra.get("restitution_credits") or [])
    key = str(body.get("origin_penalty_id") or body.get("penalty_id") or "")
    if key and any(c.get("penalty_id") == key for c in credits if isinstance(c, dict)):
        return {"success": True, "credited": True, "duplicate": True}
    credits.append({
        "penalty_id": key,
        "amount": amount,
        "currency": str(body.get("currency") or ""),
        "source": str(body.get("source_canister") or ""),
    })
    extra["restitution_credits"] = credits
    if hasattr(user, "metadata"):
        _write_json(user, extra)
    logger.info(f"Recorded restitution credit for {plaintiff} amount={amount}")
    return {"success": True, "credited": True, "duplicate": False}


def accept_restitution(source: str, body: dict) -> Dict[str, Any]:
    """Plaintiff's home quarter: record the credit. Do not gift from treasury."""
    payload = dict(body or {})
    payload["source_canister"] = source
    return credit_plaintiff_locally(payload)


def restitution_body(penalty, case) -> Dict[str, Any]:
    from core.justice import roles

    plaintiff = roles.principal_of(getattr(case, "plaintiff", None)) if case else ""
    defendant = roles.principal_of(getattr(penalty, "target_user", None)) or (
        roles.principal_of(getattr(case, "defendant", None)) if case else ""
    )
    return {
        "penalty_id": str(getattr(penalty, "id", "") or getattr(penalty, "_id", "") or ""),
        "origin_penalty_id": str(getattr(penalty, "id", "") or getattr(penalty, "_id", "") or ""),
        "case_id": str(getattr(case, "_id", "") or "") if case else "",
        "case_number": (getattr(case, "case_number", "") or "") if case else "",
        "plaintiff_id": plaintiff,
        "defendant_id": defendant,
        "amount": getattr(penalty, "amount", None),
        "currency": getattr(penalty, "currency", "") or "",
    }


def notify_restitution(penalty, case) -> Dict[str, Any]:
    """After a successful collect: ask the plaintiff's home to credit them."""
    home = plaintiff_home_quarter(case)
    self_id = _self_id()
    body = restitution_body(penalty, case)
    if not home or home == self_id:
        result = credit_plaintiff_locally(body)
        return result

    result = deliver(home, TOPIC_RESTITUTION, body)
    return result
