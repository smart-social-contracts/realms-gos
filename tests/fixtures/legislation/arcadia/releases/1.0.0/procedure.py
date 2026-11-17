"""Arcadia: administrative procedure. Synthetic legislation for tests."""

from realms.legislation import above, months, overridable


class Procedure(above("procedure")):
    default_decision_deadline = overridable(months(3), legislation="Arcadia Procedure Act Art. 4")
