"""Estado: protección de datos personales. Ley Orgánica 3/2018."""

from realms.legislation import above, extension


class DataProtection(above("data_protection")):
    @extension(legislation="Ley Orgánica 3/2018 Art. 8")
    def check_document_share(self, args):
        basis = args.basis
        if isinstance(basis, type(args)) and basis.legal_basis in ("gdpr-6-1-c", "gdpr-6-1-e") \
                and basis.legislation is None:      # a basis that is no record is for the realm to refuse
            return self.deny("name the legislation that sets this public task")
