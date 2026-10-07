"""`realms domains`: sheet → hosts → canister ids → what to check and write."""

from __future__ import annotations

import json
import os

import pytest

from realms.cli.commands import domains as d
from realms.cli.dns import ICP_ACME_GATEWAY


def _sheet(**env_over):
    env = {
        "network": "ic",
        "dns": {"provider": "cloudflare", "zone": "realmsgos.org", "ttl": 60},
        "marketplace_host": "realmsgos.org",
        "docs_host": "",
    }
    env.update(env_over)
    return {
        "name": "realms-product",
        "environments": {"production": env, "local": {"network": "local", "dns": {"provider": "none"}, "marketplace_host": "", "docs_host": ""}},
        "domains": [
            {"host": "$env.marketplace_host", "canister": "marketplace-frontend"},
            {"host": "$env.docs_host", "canister": "docs-frontend"},
        ],
    }


class TestTargets:
    def test_hosts_resolve_through_the_environment(self):
        targets = d.domain_targets(_sheet(), "production")
        assert [(t.host, t.canister_name) for t in targets] == [("realmsgos.org", "marketplace-frontend")]

    def test_environment_without_a_host_has_no_targets(self):
        assert d.domain_targets(_sheet(), "local") == []

    def test_unknown_environment_is_refused(self):
        with pytest.raises(ValueError, match="not in the sheet"):
            d.domain_targets(_sheet(), "staging")

    def test_unresolvable_env_ref_is_refused(self):
        sheet = _sheet()
        sheet["domains"] = [{"host": "$env.nope", "canister": "x"}]
        with pytest.raises(ValueError, match="environments.<env>.nope"):
            d.domain_targets(sheet, "production")


class TestCanisterIds:
    def test_export_file_wins_over_the_conductor(self, tmp_path, monkeypatch):
        export = tmp_path / "export.json"
        export.write_text(json.dumps({"ok": True, "bindings": {"marketplace-frontend": "aaaaa-aa"}}))
        monkeypatch.setattr(d, "export_from_conductor", lambda *a, **k: pytest.fail("conductor queried"))
        targets = d.domain_targets(_sheet(), "production")
        d.resolve_canister_ids(targets, sheet=_sheet(), env="production", export_path=export, conductor="", overrides={})
        assert targets[0].canister_id == "aaaaa-aa"

    def test_override_wins_over_everything(self, monkeypatch):
        monkeypatch.setattr(d, "export_from_conductor", lambda *a, **k: pytest.fail("conductor queried"))
        targets = d.domain_targets(_sheet(), "production")
        d.resolve_canister_ids(targets, sheet=_sheet(), env="production", export_path=None, conductor="",
                               overrides={"marketplace-frontend": "bbbbb-bb"})
        assert targets[0].canister_id == "bbbbb-bb"

    def test_conductor_is_found_in_casals_home(self, tmp_path, monkeypatch):
        (tmp_path / "realms-product.production.json").write_text(json.dumps({"backend_id": "cond-id"}))
        monkeypatch.setenv("CASALS_HOME", str(tmp_path))
        seen = {}

        def fake_export(cid, network, identity=""):
            seen["cid"], seen["network"] = cid, network
            return {"bindings": {"marketplace-frontend": "ccccc-cc"}}

        monkeypatch.setattr(d, "export_from_conductor", fake_export)
        targets = d.domain_targets(_sheet(), "production")
        d.resolve_canister_ids(targets, sheet=_sheet(), env="production", export_path=None, conductor="", overrides={})
        assert seen == {"cid": "cond-id", "network": "ic"}
        assert targets[0].canister_id == "ccccc-cc"

    def test_nothing_to_resolve_from_is_a_clear_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CASALS_HOME", str(tmp_path))
        targets = d.domain_targets(_sheet(), "production")
        with pytest.raises(RuntimeError, match="--export"):
            d.resolve_canister_ids(targets, sheet=_sheet(), env="production", export_path=None, conductor="", overrides={})

    @staticmethod
    def _fake_icp(monkeypatch, payload: dict) -> list:
        text = json.dumps(payload).encode()
        reply = b"DIDL\x00\x01\x71" + bytes([len(text)]) + text
        calls = []

        def run(cmd, **_kw):
            pin = cmd[cmd.index("--identity-password-file") + 1] if "--identity-password-file" in cmd else None
            calls.append((cmd, pin and open(pin).read()))
            return type("P", (), {"returncode": 0, "stdout": json.dumps({"response_bytes": reply.hex()}), "stderr": ""})()

        monkeypatch.setattr(d.subprocess, "run", run)
        return calls

    def test_the_conductor_is_read_as_the_selected_identity(self, monkeypatch):
        monkeypatch.delenv("ICP_IDENTITY_PASSWORD_FILE", raising=False)
        monkeypatch.setenv("DFX_HSM_PIN", "pin-1")
        calls = self._fake_icp(monkeypatch, {"ok": True, "bindings": {"x": "aaaaa-aa"}})
        assert d.export_from_conductor("cond-id", "ic")["bindings"] == {"x": "aaaaa-aa"}
        (cmd, pin), = calls
        assert "--identity" not in cmd and pin == "pin-1"
        pin_path = cmd[cmd.index("--identity-password-file") + 1]
        assert not os.path.exists(pin_path)

    def test_anonymous_needs_no_pin(self, monkeypatch):
        monkeypatch.setenv("DFX_HSM_PIN", "pin-1")
        calls = self._fake_icp(monkeypatch, {"ok": True, "bindings": {}})
        d.export_from_conductor("cond-id", "ic", "anonymous")
        (cmd, pin), = calls
        assert cmd[cmd.index("--identity") + 1] == "anonymous" and pin is None

    def test_a_private_conductor_refusal_says_who_to_run_as(self, monkeypatch):
        self._fake_icp(monkeypatch, {"ok": False, "error": "unauthorized: caller is not a commander"})
        with pytest.raises(RuntimeError, match="refused anonymous: unauthorized.*operator"):
            d.export_from_conductor("cond-id", "ic", "anonymous")

    def test_unbound_canister_names_the_stand(self, tmp_path):
        export = tmp_path / "export.json"
        export.write_text(json.dumps({"bindings": {"other": "x"}}))
        targets = d.domain_targets(_sheet(), "production")
        with pytest.raises(RuntimeError, match="marketplace-frontend"):
            d.resolve_canister_ids(targets, sheet=_sheet(), env="production", export_path=export, conductor="", overrides={})


class TestObservation:
    def _obs(self, **over):
        t = d.DomainTarget(host="realmsgos.org", canister_name="marketplace-frontend", canister_id="new-id")
        obs = d.Observation(
            t,
            txt=["new-id"],
            acme=[f"_acme-challenge.realmsgos.org.{ICP_ACME_GATEWAY}"],
            apex=["1.2.3.4"],
            registration={"registration_status": "registered", "canister_id": "new-id"},
            ic_domains=(200, "realmsgos.org\n"),
            site=(200, "<html>"),
        )
        for k, v in over.items():
            setattr(obs, k, v)
        return obs

    def test_healthy_domain_has_no_problems(self):
        assert self._obs().problems == []

    def test_stale_txt_and_registration_are_both_named(self):
        obs = self._obs(txt=["old-id"], registration={"registration_status": "registered", "canister_id": "old-id"}, site=(404, ""))
        problems = " | ".join(obs.problems)
        assert "_canister-id TXT is ['old-id']" in problems
        assert "registration points at old-id" in problems
        assert "HTTP 404" in problems

    def test_missing_ic_domains_file_is_a_problem(self):
        obs = self._obs(ic_domains=(404, ""))
        assert any("ic-domains" in p for p in obs.problems)

    def test_unregistered_domain_is_a_problem(self):
        obs = self._obs(registration=None)
        assert "not registered with the IC gateways" in obs.problems


class TestCandidText:
    def test_decodes_a_single_text_reply(self):
        payload = '{"ok": true}'.encode()
        raw = b"DIDL\x00\x01\x71" + bytes([len(payload)]) + payload
        assert d.candid_single_text(raw) == '{"ok": true}'

    def test_long_text_uses_multibyte_leb128(self):
        payload = b"x" * 300  # 300 = 0xAC 0x02
        raw = b"DIDL\x00\x01\x71\xac\x02" + payload
        assert d.candid_single_text(raw) == "x" * 300

    def test_rejects_other_shapes(self):
        with pytest.raises(ValueError):
            d.candid_single_text(b"DIDL\x00\x01\x7c\x01")
