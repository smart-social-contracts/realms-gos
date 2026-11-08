from ic_python_db import String

from core.db import Row


class External(Row):
    """A body outside the realm and outside the Internet Computer: an authority, a journal, a lender."""

    __alias__ = "id"
    id = String()
    name = String(default="")
    kind = String(default="")  # authority, journal, lender, ...
    channel = String(default="manual")  # "manual": an operator delivers a dispatch; an https URL: a bridge does
