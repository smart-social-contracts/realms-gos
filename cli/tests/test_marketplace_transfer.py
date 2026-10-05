"""`realms marketplace transfer`: owned or named listings → transfer_listing calls."""

from __future__ import annotations

import pytest
import typer

from realms.cli.commands import marketplace_transfer as mt


def test_idl_hash_matches_the_tags_icp_prints():
    assert mt.idl_hash("Ok") == 17_724
    assert mt.idl_hash("Err") == 3_456_837


def test_field_values_by_name_and_by_hash():
    h = mt.idl_hash("extension_id")
    named = '(vec { record { extension_id = "voting"; name = "Voting" }; record { extension_id = "vault" } })'
    hashed = f'(vec {{ record {{ {h:,} = "voting"; }}; record {{ {h} = "vault"; }} }})'.replace(",", "_")
    assert mt.field_values(named, "extension_id") == ["voting", "vault"]
    assert mt.field_values(hashed, "extension_id") == ["voting", "vault"]
    assert mt.field_values("(vec {})", "extension_id") == []


class FakeCanister:
    def __init__(self, owned=None, refuse=()):
        self.owned = owned or {}
        self.refuse = set(refuse)
        self.transfers = []

    def __call__(self, canister, method, arg, network, identity, is_query=False, timeout=120, raise_on_error=True):
        if method.startswith("get_my_"):
            field = {"get_my_extensions": "extension_id", "get_my_codices": "codex_id",
                     "get_my_assistants": "assistant_id"}[method]
            ids = self.owned.get(method, [])
            return "(vec { " + "; ".join(f'record {{ {field} = "{i}" }}' for i in ids) + " })"
        assert method == "transfer_listing"
        self.transfers.append(arg)
        if any(f'"{r}"' in arg for r in self.refuse):
            return '(variant { Err = "Not the owner" })'
        return '(variant { Ok = "transferred" })'


def test_moves_everything_the_signer_owns():
    fake = FakeCanister(owned={"get_my_extensions": ["voting", "vault"], "get_my_codices": ["agora"]})
    mt.marketplace_transfer_command("mkt", "oztxw-new", "ic", "dev-deploy", call=fake)
    assert fake.transfers == [
        '("ext", "voting", "oztxw-new")',
        '("ext", "vault", "oztxw-new")',
        '("codex", "agora", "oztxw-new")',
    ]


def test_named_ids_skip_the_owned_lookup():
    fake = FakeCanister(owned={"get_my_extensions": ["voting"]})
    mt.marketplace_transfer_command("mkt", "p", "ic", None, codices="agora", assistants="sc/ashoka", call=fake)
    assert fake.transfers == ['("codex", "agora", "p")', '("assistant", "sc/ashoka", "p")']


def test_a_refused_transfer_fails_the_command():
    fake = FakeCanister(owned={"get_my_extensions": ["voting", "vault"]}, refuse=["vault"])
    with pytest.raises(typer.Exit) as exc:
        mt.marketplace_transfer_command("mkt", "p", "ic", None, call=fake)
    assert exc.value.exit_code == 1
    assert len(fake.transfers) == 2


def test_nothing_owned_is_an_error():
    with pytest.raises(typer.Exit) as exc:
        mt.marketplace_transfer_command("mkt", "p", "ic", None, call=FakeCanister())
    assert exc.value.exit_code == 1


def test_to_is_required():
    with pytest.raises(typer.Exit) as exc:
        mt.marketplace_transfer_command("mkt", " ", "ic", None, call=FakeCanister())
    assert exc.value.exit_code == 2
