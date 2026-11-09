"""A key prepared by a delegate on the real wasm (realms-gos#633, phase 7c): the delegate prepares a ``crypto_grant`` to its
own principal through GGG's ``ggg_call_for``, the realm says in its own words what it gives, nothing is stored until the
member approves it with ``ggg_action_approve``, and then the delegate holds that scope's key and no other.
"""

import pytest
from ic.candid import Types, encode
from test_delegation_groups import launch, ok
from test_pending_actions import ACTION, APPROVE, BLOB, CALL_FOR, CALL_FOR_REPLY, TEXT

DAY = 86400
WRAPPED = Types.Record({"principal": Types.Principal, "wrapped_dek": BLOB})
ENVELOPE = Types.Record({"id": TEXT, "scope": TEXT, "principal": Types.Principal, "wrapped_dek": BLOB,
                         "granted_by": Types.Principal, "at": Types.Nat64})
KEY_GRANT = Types.Record({"scope": TEXT, "keys": Types.Vec(WRAPPED)})
ENVELOPES = "from ggg import KeyEnvelope\nprint(sorted((e.scope, e.principal) for e in KeyEnvelope.instances()))"


@pytest.fixture(scope="module")
def realm(wasm_of, people):
    from pic import Canister

    realm = Canister(wasm_of("production"), installer=people["installer"])
    launch(realm, people)
    return realm


def test_a_key_prepared_for_the_assistant_alone_is_stored_only_when_the_member_approves_it(realm, people):
    alice, claude = people["alice"], people["claude-conn"]
    scope = f"user:{alice}:notes"
    ok(realm, "delegation_grant", alice, delegate=claude, groups=["keys"], expires_in=30 * DAY)
    grant = {"scope": scope, "keys": [{"principal": claude, "wrapped_dek": b"wrapped-for-claude"}]}
    arg = encode([{"type": KEY_GRANT, "value": grant}])
    out = realm.ggg("ggg_call_for", CALL_FOR, {"method": "ggg_crypto_grant", "arg": arg, "on_behalf_of": alice,
                                               "note": ["to read my notes"]}, CALL_FOR_REPLY, claude)
    action = out["Ok"]["Pending"]
    assert next(iter(action["status"])) == "Pending"
    lines = {line["label"]: line["value"] for line in action["summary"]}
    assert lines["Keys go to"] == claude
    assert lines["What changes"] == f'{claude} will be able to read "{scope}", and nothing else of yours'
    assert realm.shell(ENVELOPES).strip() == "[]"  # nothing is stored yet

    tap = {"action": action["id"], "hash": action["hash"], "on_behalf_of": []}
    assert "Err" in realm.ggg("ggg_action_approve", APPROVE, tap, ACTION, claude)  # the delegate's own tap is refused
    assert realm.shell(ENVELOPES).strip() == "[]"
    done = realm.ggg("ggg_action_approve", APPROVE, tap, ACTION, alice)["Ok"]
    assert next(iter(done["status"])) == "Done"
    assert realm.shell(ENVELOPES).strip() == f"[('{scope}', '{claude}')]"  # that scope, for the delegate, and no other
    # the delegate, an AI connection with no profile, reads back the envelope it was given, and not another scope's
    own = realm.ggg("ggg_crypto_envelope", TEXT, scope, Types.Opt(ENVELOPE), claude, query=True)["Ok"][0]
    assert bytes(own["wrapped_dek"]) == b"wrapped-for-claude" and str(own["principal"]) == claude
    other = realm.ggg("ggg_crypto_envelope", TEXT, f"user:{alice}:old", Types.Opt(ENVELOPE), claude, query=True)
    assert "Err" in other or other["Ok"] == []
