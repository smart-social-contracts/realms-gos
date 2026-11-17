"""València: protecció de dades personals."""

from ggg import External
from realms.legislation import above


class DataProtection(above("data_protection")):
    def check_document_share(self, args):
        # the recipient is "External:<id>" as the realm takes it; its kind is read from GGG's entity, never from the args
        kind, _, who = args.recipient.partition(":")
        recipient = External.get(who) if kind == "External" else None
        if recipient is not None and recipient.kind != "authority" \
                and not self.approved_by("Junta de Govern Local"):
            return self.deny("a recipient that is not an authority needs the Junta de Govern Local")
