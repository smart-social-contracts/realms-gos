"""València: impost sobre béns immobles."""

from ggg import Attribute, Human
from realms.legislation import above, pct


class Tax(above("tax")):
    capital = True
    surface_public_transport = True
    large_family_bonus = {"general": pct("60"), "special": pct("90")}      # 60 % and 90 % of the quota

    def ibi_bonus(self, owner):
        """The share of the quota forgiven for ``owner`` (a parcel's owner, "User:<id>"): the verified large-family title's."""
        kind, _, user = owner.partition(":")
        if kind != "User":
            return 0
        for human in Human.find("user", user):
            for attribute in Attribute.find("human", human.id):
                if attribute.kind == "large_family" and attribute.status == "verified":
                    return self.large_family_bonus.get(attribute.value, 0)
        return 0
