"""The operations of keys, joining, power over people and power of attorney onward (phase 7c), each with a call that works.

``build()`` sets up Agora with what every one of them needs, and says for each: who prepares it for whom, the group a grant
names for it, the arguments, and ``ran()``, which tells whether the call has taken effect."""
import json

import fake_chain
import world
from api.members import digest
from ggg import Appointment, Delegation, Document, Invite, KeyEnvelope, Organization, User
from kit import ok, rows
from test_crypto import REPORT
from test_orgs import org_admin

DAY = 86400
NAMES = ("crypto_derive_key", "crypto_grant", "crypto_revoke", "private_data_update", "document_store", "document_share",
         "document_revoke", "document_delete", "document_publish", "realm_join", "invite_create", "representative_add",
         "organization_edit", "delegation_grant", "delegation_accept", "delegation_request_decide",
         "orchestration_decide")
SCOPE = "user:alice:notes"  # a scope of Alice's that only the realm's key-giving cases touch
VETKD_KEY = '(record { 2_345 = blob "\\de\\ad\\be\\ef\\00\\11\\22\\33" })'  # as the management canister replies
DERIVED = "deadbeef00112233"


def called(method):
    return any(sent[1] == method for sent in fake_chain.state["calls"])


def build():
    depts = world.build()
    world.seat("health.inspector", "carla", depts["Health"], "document.manage")
    org_admin("dr-ana")
    world.profile("reader", "user.view")
    ok("invite_create", "ctl", profile="reader", code="JOIN-1")
    ok("crypto_grant", "alice", scope="user:alice:old", keys={"alice": "env:a", "bob": "env:b"})
    keys = {"carla": "env:carla", "dr-ana": "env:ana"}
    documents = [ok("document_store", "carla", department="Health", keys=keys, **REPORT)["document"] for _ in range(4)]
    kept, revoked, deleted, published = documents
    ok("delegation_grant", "dana", delegate="alice", groups=["vote"], expires_in=DAY)  # D-1, waiting for Alice
    ok("delegation_request", "bob", grantor="alice", groups=["vote"], expires_in=DAY)  # DR-1
    ok("realm_configure_infrastructure", "ctl", baton_canister_id="baton")
    fake_chain.state["replies"][("baton", "submit_approval")] = json.dumps({"ok": True, "status": "APPROVED"})
    fake_chain.state["replies"][("aaaaa-aa", "vetkd_derive_key")] = VETKD_KEY

    def key_of(document, principal):
        return KeyEnvelope[f"dept:Health:doc:{document}|{principal}"]

    return {
        "crypto_derive_key": ("alice", "keys", dict(transport_public_key="beef"), lambda: called("vetkd_derive_key")),
        "crypto_grant": ("alice", "keys", dict(scope=SCOPE, keys={"claude-conn": "env:c"}),
                         lambda: KeyEnvelope[f"{SCOPE}|claude-conn"] is not None),
        "crypto_revoke": ("alice", "keys", dict(scope="user:alice:old", principals=["bob"]),
                          lambda: KeyEnvelope["user:alice:old|bob"] is None),
        "private_data_update": ("alice", "keys", dict(private_data="cipher:new"),
                                lambda: User["alice"].private_data == "cipher:new"),
        "document_store": ("carla", "office:Health",
                           dict(department="Health", keys={"bob": "env:bob"}, **{**REPORT, "title": "A new report"}),
                           lambda: any(d.title == "A new report" for d in rows(Document))),
        "document_share": ("carla", "office:Health",
                           dict(document=kept, recipient="User:bob", keys={"bob": "env:bob"}),
                           lambda: key_of(kept, "bob") is not None),
        "document_revoke": ("carla", "office:Health", dict(document=revoked, principals=["dr-ana"]),
                            lambda: key_of(revoked, "dr-ana") is None),
        "document_delete": ("carla", "office:Health", dict(document=deleted), lambda: Document[deleted] is None),
        "document_publish": ("carla", "office:Health", dict(document=published, ref="files/health/report.pdf",
                                                            hash="sha256:cd"),
                             lambda: Document[published].public is not None),
        "realm_join": ("alice", "membership", dict(code="JOIN-1"),
                       lambda: "reader" in [p.id for p in User["alice"].profiles]),
        "invite_create": ("dana", "office:Civil Registry", dict(profile="reader", code="NEW-1"),
                          lambda: Invite[digest("NEW-1")] is not None),
        "representative_add": ("dr-ana", "orgs:clinic-b",
                               dict(organization="clinic-b", position="clinic-b.nurse", user="bob", title="Nurse"),
                               lambda: any(a.user == "bob" and a.position.id == "clinic-b.nurse"
                                           for a in rows(Appointment))),
        "organization_edit": ("dr-ana", "orgs:clinic-b", dict(organization="clinic-b", admins=["dr-ana", "bob"]),
                              lambda: Organization["clinic-b"].admins == ["dr-ana", "bob"]),
        "delegation_grant": ("alice", "poa", dict(delegate="bob", groups=["vote"], expires_in=DAY),
                             lambda: any((d.grantor, d.delegate) == ("alice", "bob") for d in rows(Delegation))),
        "delegation_accept": ("alice", "poa", dict(delegation="D-1"), lambda: Delegation["D-1"].status == "active"),
        "delegation_request_decide": ("alice", "poa", dict(request="DR-1", approve=True),
                                      lambda: any((d.grantor, d.delegate, d.status) == ("alice", "bob", "active")
                                                  for d in rows(Delegation))),
        "orchestration_decide": ("ctl", "admin", dict(action_id=7, decision="approve"),
                                 lambda: called("submit_approval")),
    }
