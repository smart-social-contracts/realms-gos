from ic_python_db import Integer, String

from core.db import Json, Row


class Dispatch(Row):
    """One notice to an External, queued by the realm and confirmed by whoever delivered it. A ``body`` that holds
    personal data is the id of an encrypted Document, never the data itself (I-PRV-1)."""

    __alias__ = "id"
    id = String()
    to = String(default="", indexed=True)  # an External
    topic = String(default="")
    title = String(default="")
    body = Json({})  # a map: {"text"} for a notice, the clear copy's {"ref", "hash"} for a publication
    document = String(default="")  # the document it carries
    codex = String(default="")  # the codex it concerns
    queued_at = Integer(default=0)
    status = String(default="queued", indexed=True)  # queued | confirmed | failed
    outcome_at = Integer(default=0)  # when it was delivered, once confirmed
    receipt = String(default="")  # the bulletin number or filing reference that proves it
    failure = String(default="")  # why it failed
