"""A key prepared by a delegate on the real wasm (realms-gos#633, phase 7c): the delegate prepares a ``crypto_grant`` to its
own principal through GGG's ``ggg_call_for``, the realm says in its own words what it gives, nothing is stored until the
member approves it with ``ggg_action_approve``, and then the delegate holds that scope's key and no other.
"""

import pytest
from ic.candid import Types, decode, encode
from test_delegation_groups import launch, ok, refused
from test_pending_actions import ACTION, APPROVE, BLOB, CALL_FOR, CALL_FOR_REPLY, READ_FOR, TEXT, plain

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
    lines = {line["label"]: plain(line["value"]) for line in action["summary"]}
    assert lines["Keys go to"] == claude
    assert lines["What changes"] == f'{claude} will be able to read "{scope}", and nothing else of yours'
    assert realm.shell(ENVELOPES).strip() == "[]"  # nothing is stored yet

    # a second delegate of the member, holding "See my affairs", reads no card of another delegate's
    mallory = people["mallory"]
    ok(realm, "delegation_grant", alice, delegate=mallory, groups=["see"], expires_in=30 * DAY)
    peek = {"method": "ggg_action_get", "arg": encode([{"type": TEXT, "value": action["id"]}]), "on_behalf_of": alice}
    read = realm.ggg("ggg_read_for", READ_FOR, peek, BLOB, mallory, query=True)
    assert decode(bytes(read["Ok"]))[0]["value"] is None  # a null opt, which an untyped decode reads as None

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
    assert other == {"Ok": []}  # the scope it was not given has no envelope for it: GGG's opt turns not found into none


def test_text_that_only_looks_like_a_principal_is_refused_when_the_action_is_prepared_and_a_real_principal_is_not(realm, people):
    alice, claude, bob = people["alice"], people["claude-conn"], people["bob"]
    on = dict(on_behalf_of=alice, scope=f"user:{alice}:more")
    phrase = "no-one-else-will-read-it-you-keep-it"  # lower-case words joined by dashes, but no checksummed principal
    assert "is not a principal" in refused(realm, "crypto_grant", claude, keys={phrase: "x"}, **on)
    waiting = ok(realm, "crypto_grant", claude, keys={bob: "x"}, **on)["pending"]
    card = ok(realm, "action_get", alice, query=True, action=waiting)["action"]
    assert {line["label"]: line["value"] for line in card["summary"]}["Keys go to"].startswith(bob)
