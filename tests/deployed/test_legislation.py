"""The canister's copy of ``realms.legislation`` on PocketIC: a layered-codex chapter loads inside the realm
wasm, through the module the canister bundles, which proves the library needs nothing the canister lacks
(no CLI dependency, no stubbed stdlib). See docs/codices/layered-codex.md §12 and realms-gos#483.
"""

import json

from ic.candid import Types, encode
from ic.principal import Principal
from pic import CONTROLLER

CODE = '''
import json
from realms.legislation import Codex, Event, Refused, working_days

ES = """
from realms.legislation import Ref, above, extension, fixed, months, overridable, pct, working_days

class Procedure(above("procedure")):
    default_decision_deadline = overridable(months(3), legislation="Ley 39/2015 Art. 21.3")
    comment_period = overridable(working_days(30), legislation="Ley 7/1985 Art. 49", check=lambda v: v >= working_days(30))
    bonus = fixed({"general": pct("60")}, legislation="TRLRHL Art. 74")

    @fixed(legislation="Ley 39/2015 Art. 30")
    def working_days_after(self, day, n):
        return self.calendar.add(day, working_days(n))

    @extension(legislation="Ley 7/1985 Art. 65.2")
    def on_codex_install(self, event):
        event.order = ["es"]
        self.ggg.notification_send(recipient=Ref("external", "AGE"), topic="ordinance", title="Comunicació", body=event.result["codex"])
"""
TOWN = """
from realms.legislation import above, days, working_days

class Procedure(above("procedure")):
    default_decision_deadline = days(60)
    comment_period = working_days(45)

    def on_codex_install(self, event):
        self.ggg.notification_send(recipient="members", topic="ordinance", title="Nova ordenança", body=str(event.order))
"""
REFUSED = """
from realms.legislation import above
class Procedure(above("procedure")):
    def working_days_after(self, day, n):
        pass
"""
codex = Codex(["eu/es", "x/town"], realm="x/town", operations=["codex_install", "notification_send"])
codex.load("eu/es", "procedure", ES)
codex.load("x/town", "procedure", TOWN)
codex.calendar_layer("eu/es", {"weekend": ["sat", "sun"], "years": {"2027": [{"date": "2027-01-01", "name": {"es": "Año Nuevo"}}]}})
codex.check()
context = codex.context(now=7)
chapter = codex.instance("procedure", context)
chapter.on_codex_install(Event("alice", {"document": "D-1"}, {"codex": "C-1"}, 7))
other = Codex(["eu/es", "x/town"], realm="x/town")
other.load("eu/es", "procedure", ES)
try:
    other.load("x/town", "procedure", REFUSED)
    refusal = None
except Refused as e:
    refusal = str(e)
print(json.dumps({"deadline": chapter.default_decision_deadline.to_json(), "bonus": chapter.bonus,
                  "day": chapter.working_days_after("2026-12-31", 2), "effects": context.effects.queued, "refusal": refusal}))
'''


def test_a_chapter_loads_through_the_canisters_copy_of_the_library(realm):
    realm.pic.set_sender(Principal.from_str(CONTROLLER))
    out = realm.raw(
        "__shell__", encode([{"type": Types.Text, "value": CODE}]), query=False
    )[0]["value"]
    assert out.startswith("{"), out  # a traceback otherwise
    got = json.loads(out)
    assert got["deadline"] == {"count": 60, "unit": "Day"}
    assert got["bonus"] == {"general": 60_000_000}
    assert got["day"] == "2027-01-05"
    assert got["effects"] == [
        [
            "notification_send",
            {
                "recipient": {"kind": "external", "id": "AGE"},
                "topic": "ordinance",
                "title": "Comunicació",
                "body": "C-1",
            },
        ],
        [
            "notification_send",
            {
                "recipient": "members",
                "topic": "ordinance",
                "title": "Nova ordenança",
                "body": "['es']",
            },
        ],
    ]
    assert (
        got["refusal"]
        == "x/town/procedure.py:4: working_days_after is fixed by eu/es/procedure.py (Ley 39/2015 Art. 30)"
    )
