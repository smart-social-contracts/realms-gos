from ic_python_db import Integer, String

from core.db import Json, Row


class Request(Row):
    """A request to the realm by a person or a body. A ``body`` or ``reply`` that holds personal data is the id of an
    encrypted Document, never the data itself (I-PRV-1)."""

    __alias__ = "id"
    id = String()
    kind = String(default="")
    by = String(default="")  # "User:alice" or "Organization:clinic-b"
    body = Json(None)
    filed_at = Integer(default=0)
    due_at = Integer(default=0)
    extension_limit = Integer(default=0)  # set once; due_at never moves past it
    status = String(default="filed")  # filed | decided
    decision = String(default="")  # the realm's word: granted, refused, refused_by_silence
    reply = String(default="")
    decided_at = Integer(default=0)
