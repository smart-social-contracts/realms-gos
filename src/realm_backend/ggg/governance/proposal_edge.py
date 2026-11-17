from ic_python_db import String

from core.db import Row


class ProposalEdge(Row):
    """One link of a proposal to another, kept by the proposal it points to, so that the links *to* a proposal can be
    read a page at a time (ggg I-GOV-11). The links of a proposal itself are in its ``links``."""

    __alias__ = "id"
    id = String()
    source = String(default="")  # the proposal that carries the link
    target = String(indexed=True)  # the proposal it names
    kind = String(default="")  # replaces | relates_to
