import chain
from db import view
from ggg import Calendar, Department, Realm, Treasury, UserProfile
from op import OPS, op

VERSION = "VERSION_PLACEHOLDER"

PROFILES = {
    "admin": ["all"],
    "member": [
        "self.join", "self.update_public_profile", "self.update_private_data", "self.change_quarter",
        "self.invoice_refresh", "self.data_view", "self.data_manage", "self.delegate", "realm.data_view",
        "proposal.create", "proposal.vote", "federal_vote.propose", "dispute.create", "dispute.view",
        "identity.submit", "license.apply", "tax.declare", "bid.submit", "extension.call",
    ],
}


def start():
    if Realm["1"] is None:
        realm = Realm(id="1", principal_id=chain.self_id())
        realm.calendar, realm.treasury = Calendar(id="1"), Treasury(id="1")
        Department(id="Administration", is_root=True, description="The realm's root department")
    for name, ops in PROFILES.items():
        if UserProfile[name] is None:
            UserProfile(id=name, allowed_to=ops)


@op(None, read=True, public=True)
def status(c, args):
    realm = Realm["1"]
    return {"version": VERSION, "status": realm.status if realm else "", "name": realm.name if realm else "",
            "canister": chain.self_id(), "operations": len(OPS)}


@op(None, read=True, public=True)
def version(c, args):
    return {"version": VERSION}


@op(None, read=True, public=True)
def realm_info(c, args):
    realm = Realm["1"]
    return {"realm": view(realm, "name", "manifesto", "welcome_message", "status", "logo_url",
                          "background_image_url", "primary_color", "accounting_currency",
                          "open_registration", "is_capital") if realm else {}}
