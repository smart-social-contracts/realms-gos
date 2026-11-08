"""València: protecció de dades personals."""

from ggg import External
from realms.legislation import above


class DataProtection(above("data_protection")):
    def check_document_share(self, args):
        # the recipient's kind is read from GGG's entity, never from the args
        recipient = External.get(args.recipient.id) if args.recipient.kind == "external" else None
        if recipient is not None and recipient.kind != "authority" \
                and not self.approved_by("Junta de Govern Local"):
            return self.deny("a recipient that is not an authority needs the Junta de Govern Local")
