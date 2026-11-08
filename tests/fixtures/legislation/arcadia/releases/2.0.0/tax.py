"""Arcadia: property tax, second release: the rate rises. Synthetic legislation for tests."""

from realms.legislation import above, overridable, pct


class Tax(above("tax")):
    property_tax_rate = overridable(pct("0.45"), legislation="Arcadia Fiscal Code Art. 12, as amended")
