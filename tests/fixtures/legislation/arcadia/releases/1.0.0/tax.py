"""Arcadia: property tax, first release. Synthetic legislation for tests."""

from realms.legislation import above, overridable, pct


class Tax(above("tax")):
    property_tax_rate = overridable(pct("0.40"), legislation="Arcadia Fiscal Code Art. 12")
