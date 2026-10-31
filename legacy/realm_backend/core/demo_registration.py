"""Capital-orchestrated demo citizen registration (test/staging only).

Registers synthetic principals on joinable quarters so population pushes and
quarter auto-scaling follow the same path as real ``join_realm`` quarter joins.

Gated by ``is_demo_data_active()`` (``test_mode`` + ``test_mode_demo_data``) on
the capital. Quarters accept registrations only from their federation capital
canister id (quarters intentionally do not carry ``test_mode_demo_data``).
"""

import json
from typing import Any, Dict, List

from ic_python_logging import get_logger

logger = get_logger("core.demo_registration")


def pick_target_quarter_canister(self_canister_id: str) -> str:
    """Return the canister id where the next demo user should register.

    Mirrors ``get_join_targets`` / ``pick_default_join_quarter``: least-populated
    active sub-quarter, or *self* when this capital has no peer quarters yet.
    """
    from core.join_targets import pick_default_join_quarter
    from ggg import Quarter

    self_id = (self_canister_id or "").strip()
    subs = []
    for q in Quarter.instances():
        cid = (getattr(q, "canister_id", "") or "").strip()
        if not cid or cid == self_id:
            continue
        if (getattr(q, "status", "") or "active") != "active":
            continue
        subs.append({
            "canister_id": cid,
            "population": int(getattr(q, "population", 0) or 0),
            "index": int(getattr(q, "index", 0) or 0),
        })

    if not subs:
        return self_id
    return pick_default_join_quarter(subs, self_id) or self_id


def _realm_context():
    from _cdk import ic
    from ggg import Realm

    self_id = ic.id().to_str()
    realm = Realm.load("1")
    return self_id, realm


def authorize_capital_demo_registration() -> Dict[str, Any]:
    """Capital-local gate: both test_mode flags must be on."""
    from core.runtime_flags import is_demo_data_active

    if not is_demo_data_active():
        return {
            "ok": False,
            "error": "Demo registration requires test_mode and test_mode_demo_data",
        }
    self_id, realm = _realm_context()
    if realm and bool(getattr(realm, "is_quarter", False)):
        return {"ok": False, "error": "Demo registration must be orchestrated from the capital"}
    return {"ok": True, "self_id": self_id, "realm": realm}


def authorize_quarter_demo_registration(caller_id: str) -> Dict[str, Any]:
    """Quarter gate: only the federation capital may register demo citizens."""
    self_id, realm = _realm_context()
    if not realm:
        return {"ok": False, "error": "Realm not found"}
    if not bool(getattr(realm, "is_quarter", False)):
        return authorize_capital_demo_registration()

    capital_id = (getattr(realm, "federation_realm_id", "") or "").strip()
    if not capital_id:
        return {"ok": False, "error": "Quarter has no federation capital id"}
    if (caller_id or "").strip() != capital_id:
        return {
            "ok": False,
            "error": "Only the federation capital may register demo citizens on a quarter",
        }
    return {"ok": True, "self_id": self_id, "realm": realm, "capital_id": capital_id}


def register_one_demo_citizen(citizen: dict, *, home_quarter: str = "") -> dict:
    """Register a single synthetic citizen on *this* canister."""
    from ggg import Human, Member, User
    from ggg.system.user import user_register

    principal = (citizen.get("principal") or "").strip()
    if not principal or principal == "2vxsx-fae":
        raise ValueError("Invalid demo principal")

    was_new = False
    try:
        was_new = User[principal] is None
    except Exception:
        was_new = True

    profile = (citizen.get("profile") or "member").strip() or "member"
    user_register(principal, profile)

    user = User[principal]
    if user:
        if citizen.get("profile_picture_url"):
            user.profile_picture_url = citizen["profile_picture_url"]
        if home_quarter:
            user.home_quarter = home_quarter

    human = citizen.get("human") or {}
    if human and was_new:
        Human(
            name=human.get("name") or principal,
            date_of_birth=human.get("date_of_birth") or "",
            user_id=principal,
            latitude=float(human.get("latitude") or 0),
            longitude=float(human.get("longitude") or 0),
        )

    member = citizen.get("member") or {}
    if member and was_new and user:
        Member(
            id=member.get("id") or f"demo_mem_{principal}",
            user=user,
            residence_permit=member.get("residence_permit") or "valid",
            tax_compliance=member.get("tax_compliance") or "compliant",
            identity_verification=member.get("identity_verification") or "verified",
            voting_eligibility=member.get("voting_eligibility") or "eligible",
            public_benefits_eligibility=member.get("public_benefits_eligibility") or "eligible",
            criminal_record=member.get("criminal_record") or "clean",
        )

    return {"principal": principal, "was_new_user": was_new}


def register_demo_citizens_local(citizens: List[dict]) -> dict:
    """Register citizens on this canister (capital with no peers, or quarter body)."""
    self_id, realm = _realm_context()
    home = self_id if realm and bool(getattr(realm, "is_quarter", False)) else ""

    created = []
    any_new = False
    for citizen in citizens:
        if not isinstance(citizen, dict):
            continue
        result = register_one_demo_citizen(citizen, home_quarter=home)
        created.append(result["principal"])
        any_new = any_new or bool(result.get("was_new_user"))

    return {
        "success": True,
        "target": self_id,
        "created": created,
        "any_new_user": any_new,
    }


def complete_quarter_population_push(any_new_user: bool):
    """Quarter → capital population push (mirrors ``join_realm`` tail)."""
    from api.cross_quarter import report_population_to_capital
    from ggg import Realm, User

    _, realm = _realm_context()
    if not (any_new_user and realm and bool(getattr(realm, "is_quarter", False))):
        return {"skipped": True}

    capital_id = (getattr(realm, "federation_realm_id", "") or "").strip()
    if not capital_id:
        return {"skipped": True, "reason": "no capital id"}

    pop = int(User.count())
    result = yield from report_population_to_capital(capital_id, pop)
    return result


def register_demo_citizens_impl(payload: str):
    """Body for the ``register_demo_citizens`` canister endpoint."""
    from _cdk import ic

    caller = ic.caller().to_str()
    _, realm = _realm_context()
    is_quarter = bool(realm and getattr(realm, "is_quarter", False))

    if is_quarter:
        auth = authorize_quarter_demo_registration(caller)
    else:
        auth = authorize_capital_demo_registration()

    if not auth.get("ok"):
        return {"success": False, "error": auth.get("error", "forbidden")}

    try:
        params = json.loads(payload) if payload else {}
    except json.JSONDecodeError:
        return {"success": False, "error": "payload is not valid JSON"}

    citizens = params.get("citizens")
    if not isinstance(citizens, list) or not citizens:
        return {"success": False, "error": "citizens must be a non-empty list"}

    local = register_demo_citizens_local(citizens)
    push = {}
    if is_quarter and local.get("any_new_user"):
        push = yield from complete_quarter_population_push(True)

    local["population_push"] = push
    return local


def register_demo_citizens_routed(citizens: List[dict]):
    """Capital orchestrator: register on self or forward to a joinable quarter."""
    from _cdk import Principal
    from api.cross_quarter import DemoRegistrationService, _unwrap_call_text

    auth = authorize_capital_demo_registration()
    if not auth.get("ok"):
        return {"success": False, "error": auth.get("error", "forbidden")}

    self_id = auth["self_id"]
    target = pick_target_quarter_canister(self_id)
    payload = json.dumps({"citizens": citizens})

    if target == self_id:
        return register_demo_citizens_local(citizens)

    logger.info(f"Routing {len(citizens)} demo citizens to quarter {target}")
    service = DemoRegistrationService(Principal.from_str(target))
    result = yield service.register_demo_citizens(payload)
    raw = _unwrap_call_text(result)
    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {"success": False, "error": f"Unparseable quarter response: {raw[:200]}"}
    if isinstance(parsed, dict):
        parsed["routed_from"] = self_id
        parsed["target"] = target
        # Home-quarter directory (issue #263): the capital routed these
        # principals itself, so record their home quarter locally without an
        # extra round-trip.
        if parsed.get("success"):
            try:
                from core.federation import record_resident

                for principal in parsed.get("created") or []:
                    record_resident(principal, target)
            except Exception as e:
                logger.error(f"Demo directory record failed for {target}: {e}")
        return parsed
    return {"success": True, "target": target, "result": parsed}
