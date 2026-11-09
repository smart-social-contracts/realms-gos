"""The guards and hooks of the legislation get the calls an operation carries without the fields that call keeps secret
(realms-gos#633): a proposal's action, the steps of a task, a scheduled operation, what ``call_for`` forwards and the
card of a pending action. What the carried call does not keep secret still crosses, and so does its personal data
(I-PRV-1); only ``secret=`` is left out."""
import pytest

import fake_chain
import keys_people
import world
from kit import call, ok
from test_delegation_affairs import lend
from test_legislation_review import notices, register, t_poke  # noqa: F401  t_poke keeps a ``code`` secret
from test_legislation_runtime import TOWN, activate, layered
from test_legislation_runtime import fresh_realm  # noqa: F401  the autouse fixture
from test_pending_actions import approved, card, confirm_ops, grant  # noqa: F401  confirm_ops: a direct operation with a secret

CARRIED = {"op": "t_poke", "args": {"what": "w", "code": "S3CRET", "more": [{"code": "S3CRET2", "keep": 1}]}}
SEEN = "seen"


def watching(*names):
    """A chapter whose hook ``on_<name>`` reports the event's arguments and result, for each of ``names``."""
    return register("".join(f'''    def on_{n}(self, event):
        self.ggg.notification_send(recipient="User:mallory", title="{SEEN}:{n}", body=str(event.args) + "|" + str(event.result))

''' for n in names))


def seen(name):
    return " ".join(notices(f"{SEEN}:{name}"))


def active(*names):
    activate(layered({(TOWN, "register"): watching(*names)}, layers=[TOWN]))


def test_a_proposal_carries_its_action_to_the_hook_without_its_secrets():
    world.build()
    active("proposal_create")
    ok("proposal_create", "alice", title="x", action=CARRIED)
    body = seen("proposal_create")
    assert "S3CRET" not in body and "S3CRET2" not in body
    assert "'what': 'w'" in body and "'keep': 1" in body  # what the call does not keep secret still crosses


def test_a_proposal_carries_its_action_to_the_guard_without_its_secrets():
    world.build()
    deny = register('''    def check_proposal_create(self, args):
        return self.deny(str(args))
''')
    activate(layered({(TOWN, "register"): deny}, layers=[TOWN]))
    error = call("proposal_create", "alice", title="x", action=CARRIED)["error"]
    assert "S3CRET" not in error and "'what': 'w'" in error


def test_the_steps_of_a_task_and_a_scheduled_operation_reach_the_hook_without_their_secrets():
    world.build()
    active("task_create", "task_schedule")
    ok("task_create", "ctl", name="later", steps=[CARRIED])
    ok("task_schedule", "ctl", op="t_poke", args=CARRIED["args"], at=fake_chain.now() + 100)
    for name in ("task_create", "task_schedule"):
        assert "S3CRET" not in seen(name) and "'what': 'w'" in seen(name), name


@pytest.mark.parametrize("how", ("approve", "decline", "withdraw"))
def test_a_pending_action_reaches_the_hook_without_the_secrets_of_the_call_it_holds(how):
    cases = keys_people.build()
    lend("alice", "membership", "keys")
    active("action_approve", "action_decline", "action_withdraw")
    join = ok("realm_join", "claude-conn", on_behalf_of="alice", **cases["realm_join"][2])["pending"]
    derive = ok("crypto_derive_key", "claude-conn", on_behalf_of="alice", **cases["crypto_derive_key"][2])["pending"]
    if how == "approve":
        approved(join, "alice")
        approved(derive, "alice")
    elif how == "decline":
        ok("action_decline", "alice", action=join)
        ok("action_decline", "alice", action=derive)
    else:
        ok("action_withdraw", "claude-conn", action=join)
        ok("action_withdraw", "claude-conn", action=derive)
    body = seen(f"action_{how}")
    assert "JOIN-1" not in body and keys_people.DERIVED not in body
    # the rest of the card still crosses, and so do the lines the realm wrote
    assert "realm_join" in body and "Profile you take" in body
    # the card the member reads is untouched
    assert ok("action_get", "alice", action=join)["action"]["args"] == {"code": "JOIN-1"}
    if how == "approve":
        assert card(derive)["result"]["encrypted_key"] == keys_people.DERIVED


def test_the_reply_of_a_call_forwarded_for_a_member_reaches_the_hook_without_the_secrets_of_the_call(confirm_ops):  # noqa: F811
    world.build()
    grant("profile")
    active("call_for")
    out = ok("call_for", "claude-conn", method="t_direct_secret", args={}, grantor="alice")
    assert out["token"] == "S3CRET-TOKEN"  # the delegate gets its reply
    assert "S3CRET-TOKEN" not in seen("call_for") and "'n': 1" in seen("call_for")
