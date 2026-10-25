from ic_python_db import Boolean, Entity, ManyToOne, String, TimestampedMixin
from ic_python_logging import get_logger

logger = get_logger("entity.notification")

# Visibility — who is allowed to read a notification.
VISIBILITY_PRIVATE = "private"  # only the addressed audience may read
VISIBILITY_PUBLIC = "public"  # readable by anyone

# Audience — who a notification is addressed to.
AUDIENCE_USER = "user"  # a single user (a direct message)
AUDIENCE_DEPARTMENT = "department"  # every member of a department
AUDIENCE_REALM = "realm"  # every registered user of the realm


class Notification(Entity, TimestampedMixin):
    __owner_field__ = "recipient"  # realms#282 — SecureORM ownership stamp/protect
    """Notification (a.k.a. message) with visibility + audience semantics.

    Two orthogonal dimensions decide delivery:

      * ``visibility`` (``private`` | ``public``) — who may *read* it. Public
        notifications are readable by anyone; private ones only by their
        audience.
      * ``audience_type`` (``user`` | ``department`` | ``realm``) — who it is
        *addressed* to.

    Broadcasts (department / realm) are stored as a **single** record and the
    set of recipients is resolved at read time, rather than fanned out into one
    row per user. This keeps realm-wide and public messages cheap.

    Read state:
      * ``user`` notifications use the ``read`` boolean.
      * broadcasts track per-user read state in ``read_by`` (comma-separated
        principals), so one reader marking it read does not affect others.
    """

    topic = String(max_length=64)
    title = String(max_length=256)
    message = String(max_length=2048)
    sender = String(max_length=128)
    recipient = String(max_length=128)
    visibility = String(max_length=16, default=VISIBILITY_PRIVATE)
    audience_type = String(max_length=16, default=AUDIENCE_USER)
    # Sending realm's canister id, set for inter-realm messages (always public).
    origin_realm = String(max_length=64, default="")
    user = ManyToOne("User", "notifications")  # set when audience_type == "user"
    department = ManyToOne("Department", "notifications")  # set when audience_type == "department"
    read = Boolean()
    # Comma-separated principals who have read a broadcast notification.
    read_by = String(max_length=8192, default="")
    metadata = String(max_length=256)
    icon = String(max_length=64)
    href = String(max_length=256)
    color = String(max_length=32)
