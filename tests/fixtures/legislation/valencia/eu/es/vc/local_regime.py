"""Comunitat Valenciana: règim local. Llei 4/1983 i Llei 8/2010."""

from ggg import Document
from realms.legislation import above, extension


class LocalRegime(above("local_regime")):
    uses = ["language"]

    @extension(legislation="Llei 4/1983; Llei 8/2010")
    def on_codex_install(self, event):
        languages = Document.get(event.args["document"]).languages
        event.languages_missing = [l for l in self.legislation.language.co_official
                                   if l not in languages]
