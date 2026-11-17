"""Unión Europea: protección de datos personales. GDPR."""

from realms.legislation import F, above, extension, forbid


class DataProtection(above("data_protection")):
    no_basis = forbid("document_share", F.basis.is_none(), legislation="GDPR Art. 6")

    @extension(legislation="GDPR Art. 5-6")
    def check_document_share(self, args):
        if args.basis is not None and args.basis.legal_basis == "gdpr-6-1-a":
            return self.deny("consent is not a basis between public authorities")
