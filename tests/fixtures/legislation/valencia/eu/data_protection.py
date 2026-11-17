"""Unión Europea: protección de datos personales. GDPR."""

from realms.legislation import F, above, extension, forbid


class DataProtection(above("data_protection")):
    no_basis = forbid("document_share", F.basis.is_none(), legislation="GDPR Art. 6")

    @extension(legislation="GDPR Art. 5-6")
    def check_document_share(self, args):
        basis = args.basis
        if isinstance(basis, type(args)) and basis.legal_basis == "gdpr-6-1-a":     # a basis that is no record is for the realm to refuse
            return self.deny("consent is not a basis between public authorities")
