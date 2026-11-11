"""Comunitat Valenciana: règim local. Llei 4/1983 i Llei 8/2010."""

from realms.legislation import above, extension


class LocalRegime(above("local_regime")):
    uses = ["language"]

    @extension(legislation="Llei 4/1983; Llei 8/2010")
    def on_codex_install(self, event):
        # The notice of an ordinance goes out in every co-official language: the layers below read it from the event.
        event.languages = list(self.legislation.language.co_official)
