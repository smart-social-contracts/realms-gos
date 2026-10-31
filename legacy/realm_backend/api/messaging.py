"""Inter-realm messaging API.

Lets one realm send a *public* message to another realm. Cross-realm messages
are public-only by design: the receiving realm hard-codes ``visibility=public``
and ``audience_type=realm`` regardless of what the sender claims, so a private
cross-realm message is impossible.

The transport is a plain inter-canister call (mirrors ``api/registry.py``):
the sender holds a :class:`RealmMessagingService` handle to the target realm
backend and invokes its ``receive_realm_message`` method.
"""

from typing import Dict

from _cdk import Async, CallResult, Principal, Service, ic, service_update, text
from ic_python_logging import get_logger

logger = get_logger("api.messaging")


class RealmMessagingService(Service):
    """Remote interface of a target realm backend's inbound message endpoint."""

    @service_update
    def receive_realm_message(
        self,
        title: text,
        message: text,
        topic: text,
        origin_name: text,
    ) -> text:
        ...


def send_realm_message(
    target_canister_id: str,
    title: str,
    message: str,
    topic: str = "",
    origin_name: str = "",
) -> Async[Dict]:
    """Send a public message from this realm to ``target_canister_id``.

    Returns a dict with ``success`` and either ``result`` or ``error``.
    """
    origin = str(ic.id())
    logger.info(
        f"Sending inter-realm message from {origin} ({origin_name!r}) "
        f"to {target_canister_id}: {title!r}"
    )

    try:
        service = RealmMessagingService(Principal.from_str(target_canister_id))
        result: CallResult[text] = yield service.receive_realm_message(
            title, message, topic, origin_name
        )
        logger.info(f"Inter-realm message delivered to {target_canister_id}: {result}")
        return {"success": True, "result": str(result)}
    except Exception as e:
        logger.error(f"Error sending inter-realm message: {e}")
        return {"success": False, "error": str(e)}
