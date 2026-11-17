"""València: procediment administratiu."""

from realms.legislation import above, months


class Procedure(above("procedure")):
    default_decision_deadline = months(2)
