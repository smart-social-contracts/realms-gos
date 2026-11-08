"""Entitat Metropolitana de Serveis Hidràulics: l'aigua. Llei 2/2001."""

from ggg import TaxPolicy
from realms.legislation import Ref, above, fixed


class Water(above("water")):
    bulk_supplier = fixed(Ref("external", "EMSHI"), legislation="Llei 2/2001; EMSHI statutes (illustrative)")

    @fixed(legislation="EMSHI statutes (illustrative)")
    def on_tax_policy_propose(self, event):
        for entry in TaxPolicy.get(event.args["policy"]).taxes:     # the saved policy's entries
            if entry["code"] == "water_supply":
                self.ggg.notification_send(recipient=self.bulk_supplier, topic="tariff",
                                           title="Water tariff proposed", body=entry["code"])
