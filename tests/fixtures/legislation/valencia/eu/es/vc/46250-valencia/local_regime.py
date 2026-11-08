"""València: règim local."""

from ggg import Member
from realms.legislation import Ref, above


class LocalRegime(above("local_regime")):

    def on_codex_install(self, event):
        title = "Nova ordenança" + (" (traducció pendent)" if event.languages_missing else "")
        for m in Member.each():
            self.ggg.notification_send(recipient=Ref("user", m.id), topic="ordinance",
                title=title, body=event.result["codex"])
