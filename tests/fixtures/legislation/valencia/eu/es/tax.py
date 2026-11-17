"""Estado: impuesto sobre bienes inmuebles. TRLRHL."""

from realms.legislation import F, S, above, fixed, forbid, overridable, pct, when


class Tax(above("tax")):
    capital = overridable(False, legislation="TRLRHL Art. 72.3")
    surface_public_transport = overridable(False, legislation="TRLRHL Art. 72.3")
    services_beyond_art26 = overridable(False, legislation="TRLRHL Art. 72.3")
    rustic_over_80pct = overridable(False, legislation="TRLRHL Art. 72.3")

    ibi_rate_required = forbid("tax_policy_propose",
        F.code.in_(["ibi_urban", "ibi_rustic"]) & F.rate.is_none(),
        each="taxes", legislation="TRLRHL Art. 72")

    ibi_urban_range = forbid("tax_policy_propose",
        (F.code == "ibi_urban") & F.rate.is_some() & ~F.rate.between(pct("0.40"), pct("1.30")),
        each="taxes", legislation="TRLRHL Art. 72.1, 72.3")

    ibi_urban_cap = forbid("tax_policy_propose",
        (F.code == "ibi_urban") & F.rate.is_some() & (F.rate > pct("1.10")
            + when(S.capital, pct("0.07")) + when(S.surface_public_transport, pct("0.07"))
            + when(S.services_beyond_art26, pct("0.06"))),
        each="taxes", legislation="TRLRHL Art. 72.3")

    ibi_rustic_cap = forbid("tax_policy_propose",
        (F.code == "ibi_rustic") & F.rate.is_some() & (F.rate > pct("0.90")
            + when(S.capital, pct("0.06")) + when(S.surface_public_transport, pct("0.05"))
            + when(S.services_beyond_art26, pct("0.06")) + when(S.rustic_over_80pct, pct("0.15"))),
        each="taxes", legislation="TRLRHL Art. 72.3")

    @fixed(legislation="TRLRHL Arts. 61, 65, 71 and 74")
    def compute_tax_assess(self, entry, subject):
        """GGG asks for the invoice items of each subject of each entry."""
        if entry.code != "ibi_urban" or subject.assessed_value is None:     # a parcel never assessed owes no IBI yet
            return []
        # Each invoice line is rounded once: the quota, then the bonus on the rounded quota (Art. 74 forgives a share of
        # the quota as it is billed), so the two lines add up to what the bonus leaves.
        gross = subject.assessed_value.times(entry.rate)            # nearest cent, half away from zero
        net = gross - gross.times(self.ibi_bonus(subject.owner))
        items = [{"concept": entry.code, "amount": gross, "legislation": "TRLRHL Art. 71.1"}]
        if net < gross:
            items.append({"concept": entry.code + "_bonus", "amount": net - gross,
                          "legislation": "TRLRHL Art. 74"})
        return items

    @overridable(legislation="TRLRHL Art. 74")
    def ibi_bonus(self, owner):
        """The share of the quota forgiven for ``owner`` (a parcel's owner, "User:<id>"); a lower layer decides who."""
        return 0
