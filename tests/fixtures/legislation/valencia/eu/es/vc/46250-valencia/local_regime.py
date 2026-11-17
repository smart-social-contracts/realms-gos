"""València: règim local."""

from ggg import Member
from realms.legislation import Ref, above

TITLES = {"ca-valencia": "Nova ordenança", "es": "Nueva ordenanza"}


class LocalRegime(above("local_regime")):

    def on_codex_install(self, event):
        codex = event.result.get("codex")     # an install that only starts its job has no codex to name yet
        if codex is None:
            return
        notice = [{"language": language, "title": TITLES[language], "body": codex}
                  for language in event.languages if language in TITLES]
        for m in Member.each():
            self.ggg.notification_send(recipient=Ref("user", m.id), topic="ordinance",
                title=TITLES["ca-valencia"], body=codex, translations=notice)
