"""Federation message layer: generic inter-quarter transport (issue #263).

One update endpoint (``federation_message``) carries every inter-quarter
conversation; the *semantics* live in the codex, dispatched through the
``on_federation_message`` hook. This is the codex-extensible complement to
the typed Candid services in ``api/cross_quarter.py``, which codices cannot
extend (they cannot add Candid endpoints).

Wire format (JSON)::

    {"msg_id": "<unique id>", "topic": "<namespace.action>",
     "source": "<sender canister id>", "body": {...}}

Guarantees provided here (GOS level):
  - **Auth**: only federation members are accepted — the capital accepts its
    registered quarters, a quarter accepts its capital. Same trust model as
    ``register_demo_citizens`` (sibling canisters under one controller).
  - **Idempotency**: duplicate ``msg_id`` deliveries replay the stored
    response instead of re-dispatching (``FederationMessage`` inbox rows).
  - **Reserved topics** (``gos.*``) handled by core, not the codex:
      gos.ping               liveness echo
      gos.directory.upsert   record a resident's home quarter (capital)
      gos.directory.resolve  look up a resident's home quarter (capital)
      gos.federal.propose    originate a realm-wide federal vote (capital)
      gos.federal.open       mirror a federal vote on a quarter (capital → quarter)
      gos.federal.tally      report a quarter leg outcome (quarter → capital)
      gos.federal.result     broadcast aggregated result (capital → quarters)
      gos.federal.executed   execution receipt from a quarter (quarter → capital)

Reserved ``justice.transfer`` / ``justice.restitution`` have a host default
(issue #325) when the Codex hook does not handle them. Everything else goes
to the active codex's ``on_federation_message`` hook.

Design rule (issue #263): federation messages carry claims/orders/receipts
only — value always moves on the shared ICRC-1 ledgers, never between
canisters.
"""

import json
from typing import Any, Dict, Optional, Tuple

from ic_python_logging import get_logger

logger = get_logger("core.federation")

GOS_TOPIC_PREFIX = "gos."

_MSG_ID_MAX = 128
_TOPIC_MAX = 128
_STORED_BODY_MAX = 4096

# Monotonic suffix so several messages sent in one consensus round (same
# ic.time()) still get distinct ids.
_send_counter = 0


# ---------------------------------------------------------------------------
# Pure helpers (unit-testable without a canister)
# ---------------------------------------------------------------------------


def parse_message(payload: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """Validate an incoming payload. Returns ``(message, "")`` or ``(None, error)``."""
    try:
        data = json.loads(payload) if payload else {}
    except (json.JSONDecodeError, TypeError):
        return None, "payload is not valid JSON"
    if not isinstance(data, dict):
        return None, "payload must be a JSON object"

    msg_id = (data.get("msg_id") or "").strip() if isinstance(data.get("msg_id"), str) else ""
    topic = (data.get("topic") or "").strip() if isinstance(data.get("topic"), str) else ""
    if not msg_id:
        return None, "msg_id is required"
    if len(msg_id) > _MSG_ID_MAX:
        return None, f"msg_id exceeds {_MSG_ID_MAX} chars"
    if not topic:
        return None, "topic is required"
    if len(topic) > _TOPIC_MAX:
        return None, f"topic exceeds {_TOPIC_MAX} chars"

    body = data.get("body")
    if body is None:
        body = {}
    if not isinstance(body, dict):
        return None, "body must be a JSON object"

    return {"msg_id": msg_id, "topic": topic, "body": body}, ""


def new_msg_id() -> str:
    """Unique message id: sender canister + IC time + monotonic counter."""
    global _send_counter
    from _cdk import ic

    _send_counter += 1
    return f"{ic.id().to_str()}:{ic.time()}:{_send_counter}"


# ---------------------------------------------------------------------------
# Membership / auth
# ---------------------------------------------------------------------------


def federation_members() -> set:
    """Canister ids this canister accepts federation messages from.

    Union of both roles so the check needs no capital/quarter branching:
    the capital knows its quarters (``Quarter`` rows), a quarter knows its
    capital (``Realm.federation_realm_id``).
    """
    members = set()
    try:
        from ggg import Quarter, Realm

        realm = Realm.load("1")
        capital_id = (getattr(realm, "federation_realm_id", "") or "").strip() if realm else ""
        if capital_id:
            members.add(capital_id)
        for q in Quarter.instances():
            cid = (getattr(q, "canister_id", "") or "").strip()
            if cid:
                members.add(cid)
    except Exception as e:
        logger.error(f"federation_members: {e}")
    return members


def authorize_source(caller_id: str) -> bool:
    caller = (caller_id or "").strip()
    return bool(caller) and caller in federation_members()


# ---------------------------------------------------------------------------
# Home-quarter directory (capital side)
# ---------------------------------------------------------------------------


def record_resident(principal: str, quarter_canister_id: str) -> bool:
    """Upsert ``principal -> quarter`` in the capital's directory."""
    principal = (principal or "").strip()
    quarter_canister_id = (quarter_canister_id or "").strip()
    if not principal or not quarter_canister_id:
        return False
    try:
        from ggg import QuarterResident

        row = QuarterResident[principal]
        if row:
            row.quarter_canister_id = quarter_canister_id
        else:
            QuarterResident(principal=principal, quarter_canister_id=quarter_canister_id)
        return True
    except Exception as e:
        logger.error(f"record_resident({principal}): {e}")
        return False


def resolve_home_quarter(principal: str) -> str:
    """Home quarter canister id for ``principal``, or "".

    Checks the local ``User`` row first (capital residents carry
    ``home_quarter`` directly), then the federation directory.
    """
    principal = (principal or "").strip()
    if not principal:
        return ""
    try:
        from ggg import User

        user = User[principal]
        if user:
            home = (getattr(user, "home_quarter", "") or "").strip()
            if home:
                return home
            # A local row without home_quarter means the user lives here.
            from _cdk import ic

            return ic.id().to_str()
    except Exception:
        pass
    try:
        from ggg import QuarterResident

        row = QuarterResident[principal]
        if row:
            return (getattr(row, "quarter_canister_id", "") or "").strip()
    except Exception as e:
        logger.error(f"resolve_home_quarter({principal}): {e}")
    return ""


# ---------------------------------------------------------------------------
# Incoming dispatch
# ---------------------------------------------------------------------------


def _handle_gos_topic(topic: str, source: str, body: dict) -> Dict[str, Any]:
    """Core-reserved topics; never forwarded to the codex."""
    if topic == "gos.ping":
        from _cdk import ic

        return {"success": True, "pong": ic.id().to_str()}

    if topic == "gos.directory.upsert":
        principal = body.get("principal") or ""
        # The sender vouches for its own residents only: the home quarter is
        # the *source* canister, not caller-supplied data.
        ok = record_resident(principal, source)
        if ok:
            return {"success": True, "principal": principal, "quarter": source}
        return {"success": False, "error": "principal is required"}

    if topic == "gos.directory.resolve":
        principal = body.get("principal") or ""
        if not principal:
            return {"success": False, "error": "principal is required"}
        return {"success": True, "principal": principal, "quarter": resolve_home_quarter(principal)}

    if topic.startswith("gos.federal."):
        from core.federal_vote_runtime import handle_federal_topic

        return handle_federal_topic(topic, source, body)

    return {"success": False, "error": f"Unknown reserved topic '{topic}'"}


def dispatch_message(topic: str, source: str, body: dict) -> Dict[str, Any]:
    """Route one authenticated, deduplicated message to its handler."""
    if topic.startswith(GOS_TOPIC_PREFIX):
        return _handle_gos_topic(topic, source, body)

    from core.codex_hooks import dispatch_federation_message

    result = dispatch_federation_message(topic, source, body)
    if result is None and topic.startswith("justice."):
        from core.justice.federation import handle_justice_topic

        result = handle_justice_topic(topic, source, body)
    if result is None:
        return {
            "success": False,
            "error": f"No codex handler for federation topic '{topic}'",
        }
    if isinstance(result, dict):
        return result
    return {"success": True, "result": result}


def handle_incoming(payload: str, caller_id: str) -> str:
    """Body of the ``federation_message`` endpoint. Returns a JSON string."""
    message, error = parse_message(payload)
    if message is None:
        return json.dumps({"success": False, "error": error})

    if not authorize_source(caller_id):
        logger.error(f"federation_message: rejected caller {caller_id}")
        return json.dumps(
            {"success": False, "error": "Caller is not a member of this federation"}
        )

    msg_id, topic, body = message["msg_id"], message["topic"], message["body"]

    # Idempotency: replay the stored response for a known msg_id.
    try:
        from ggg import FederationMessage

        existing = FederationMessage[msg_id]
        if existing:
            stored = getattr(existing, "response", "") or "{}"
            try:
                replay = json.loads(stored)
            except (json.JSONDecodeError, TypeError):
                replay = {"success": True}
            if isinstance(replay, dict):
                replay["duplicate"] = True
                return json.dumps(replay)
            return json.dumps({"success": True, "duplicate": True})
    except Exception as e:
        logger.error(f"federation_message: dedupe lookup failed for {msg_id}: {e}")

    logger.info(f"federation_message: {topic} from {caller_id} ({msg_id})")
    try:
        response = dispatch_message(topic, caller_id, body)
    except Exception as e:
        logger.error(f"federation_message: handler for {topic} raised: {e}")
        response = {"success": False, "error": str(e)}
    response_json = json.dumps(response)

    try:
        from ggg import FederationMessage

        FederationMessage(
            msg_id=msg_id,
            topic=topic,
            source=(caller_id or "")[:64],
            body=json.dumps(body)[:_STORED_BODY_MAX],
            response=response_json[:_STORED_BODY_MAX],
        )
    except Exception as e:
        logger.error(f"federation_message: inbox record failed for {msg_id}: {e}")

    return response_json


# ---------------------------------------------------------------------------
# Outgoing client
# ---------------------------------------------------------------------------


def send_federation_message(target_canister_id: str, topic: str, body: dict, msg_id: str = ""):
    """Send one federation message; async generator (call with ``yield from``).

    Returns the target's parsed response dict, or ``{"success": False,
    "error": ...}`` on transport failure. Callers that need at-least-once
    delivery should retry with the *same* msg_id — dedupe on the receiver
    makes retries safe.
    """
    from _cdk import CallResult, Principal, text
    from api.cross_quarter import FederationService, _unwrap_call_text

    payload = json.dumps(
        {
            "msg_id": msg_id or new_msg_id(),
            "topic": topic,
            "body": body or {},
        }
    )
    logger.info(f"send_federation_message: {topic} -> {target_canister_id}")
    try:
        service = FederationService(Principal.from_str(target_canister_id))
        result: CallResult[text] = yield service.federation_message(payload)
        raw = _unwrap_call_text(result)
        try:
            parsed = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, TypeError):
            return {"success": False, "error": f"Unparseable federation response: {raw[:200]}"}
        if isinstance(parsed, dict):
            return parsed
        return {"success": True, "result": parsed}
    except Exception as e:
        logger.error(f"send_federation_message to {target_canister_id} failed: {e}")
        return {"success": False, "error": str(e)}
