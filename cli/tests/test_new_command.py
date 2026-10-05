"""Unit tests for ``realms new`` (issue #389). No replica / live IC."""

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest
import typer
from typer.testing import CliRunner

from realms.cli.commands.new import (
    BRANDING_MAX_BYTES,
    DEFAULT_TOKEN_SYMBOLS,
    DEPLOYMENT_COST_CREDITS,
    GAAS_STAGES,
    WIZARD_CHROME_EXTENSIONS,
    STANDALONE_STAGES,
    StageError,
    catalog_token_draft,
    catalog_token_symbol,
    default_catalog_symbol,
    _format_installer_job_failure,
    _local_manifest_dependencies,
    _parse_jsonish,
    _token_draft,
    _unwrap_ok_json,
    _identity_kind_is_web,
    assert_credits_sufficient,
    build_portal_manifest,
    check_ic_members_policy,
    classify_identity,
    format_stage_catalog,
    identity_refuse_message,
    load_gaas_config,
    merge_spec_and_flags,
    new_command,
    normalize_deploy_mode,
    parse_from_stage,
    parse_subnet,
    portal_url_for_slug,
    resolve_gos_queue,
    run_setup_stage,
    slugify,
    stage_ids_to_run,
    standalone_artifact_urls,
    validate_branding_size,
    validate_merged_spec,
)
from realms.cli.main import app


runner = CliRunner()


# A shared-ledger catalog as get_setup_state().shared_tokens reports it (fixture ids).
_CATALOG = {
    "REALMS": {"ledger": "rrrrr-rr", "decimals": 8},
    "ckBTC": {"ledger": "bbbbb-bb", "indexer": "bbbbb-ix", "decimals": 8},
    "ckUSDC": {"ledger": "ccccc-cc", "decimals": 6},
    "ckEURC": {"ledger": "eeeee-ee", "decimals": 6},
}


def _merged(**overrides):
    spec = {
        "name": "Acme Realm",
        "slug": "acme",
        "gos": "realms-gos",
        "version": "main",
        "subnet": {"choice": "automatic"},
        "codex": {"package": "agora", "version": None},
        "members": 0,
        "open_registration": False,
    }
    spec.update(overrides)
    return merge_spec_and_flags(spec, spec_dir=None)


class TestSlugify:
    def test_basic(self):
        assert slugify("Acme Realm") == "acme-realm"

    def test_strips_punctuation_and_caps_length(self):
        assert slugify("Hello, World!!!") == "hello-world"
        long_name = "a" * 80
        assert len(slugify(long_name)) == 48

    def test_empty_falls_back(self):
        assert slugify("") == "realm"
        assert slugify("---") == "realm"


class TestSpecFlagMerge:
    def test_flags_override_spec(self, tmp_path: Path):
        spec = {
            "name": "FromSpec",
            "codex": {"package": "dominion", "version": "1.0.0"},
            "members": 2,
        }
        merged = merge_spec_and_flags(
            spec,
            spec_dir=tmp_path,
            name="FromFlag",
            codex="agora",
            members=0,
        )
        assert merged["name"] == "FromFlag"
        assert merged["codex"]["package"] == "agora"
        assert merged["members"] == 0
        assert merged["slug"] == "fromflag"

    def test_slug_defaults_from_name(self):
        merged = merge_spec_and_flags({"name": "Hello World"}, spec_dir=None)
        assert merged["slug"] == "hello-world"

    def test_codex_package_object_and_top_level_copy(self):
        merged = merge_spec_and_flags(
            {
                "name": "Acme Realm",
                "manifesto": "A new realm",
                "welcome_message": "Hello",
                "codex": {"package": {"name": "syntropia", "version": "latest"}},
            },
            spec_dir=None,
        )
        assert merged["codex"]["package"] == "syntropia"
        assert merged["codex"]["version"] == "latest"
        assert merged["branding"]["manifesto"] == "A new realm"
        assert merged["branding"]["welcome_message"] == "Hello"

    def test_paths_relative_to_spec_file(self, tmp_path: Path):
        logo = tmp_path / "logo.png"
        logo.write_bytes(b"\x89PNG")
        spec = {"name": "Acme Realm", "branding": {"logo": "./logo.png"}, "codex": {"package": "agora"}}
        merged = merge_spec_and_flags(spec, spec_dir=tmp_path)
        assert merged["branding"]["logo"] == str(logo.resolve())

    def test_missing_required_fields(self):
        merged = merge_spec_and_flags({}, spec_dir=None)
        with pytest.raises(StageError) as exc:
            validate_merged_spec(merged, network="staging", identity="alice")
        assert "name" in exc.value.message.lower()
        assert "codex" in exc.value.message.lower()

    def test_gos_must_be_realms_gos(self):
        merged = _merged()
        merged["gos"] = "monad-gos"
        with pytest.raises(StageError) as exc:
            validate_merged_spec(merged, network="staging", identity="alice")
        assert "realms-gos" in exc.value.message


class TestSubnet:
    def test_automatic_default(self):
        assert parse_subnet(None, None) == {"choice": "automatic"}

    def test_european_flag(self):
        assert parse_subnet({"choice": "automatic"}, "european")["choice"] == "european"

    def test_other_requires_id(self):
        with pytest.raises(StageError) as exc:
            parse_subnet({"choice": "other"}, None)
        assert "subnet.id" in exc.value.message

    def test_subnet_id_flag(self):
        subnet_id = "o57es-2iaaa-aaaaa-qaaaa-yai"
        block = parse_subnet(None, subnet_id)
        assert block == {"choice": "other", "id": subnet_id}

    def test_casals_block_automatic_omits_subnet(self):
        manifest = build_portal_manifest(
            name="Acme",
            slug="acme",
            network="staging",
            version="main",
            founder="aaaaa-aa",
            subnet={"choice": "automatic"},
        )
        assert "subnet" not in manifest["casals"]
        assert "subnet_type" not in manifest["casals"]

    def test_casals_block_european_and_other(self):
        eu = build_portal_manifest(
            name="Acme",
            slug="acme",
            network="staging",
            version="v0.4.0",
            founder="aaaaa-aa",
            subnet={"choice": "european"},
        )
        assert eu["casals"]["subnet_type"] == "european"
        assert eu["casals"]["backend_wasm_key"] == "realm-backend@0.4.0"
        other = build_portal_manifest(
            name="Acme",
            slug="acme",
            network="test",
            version="main",
            founder="aaaaa-aa",
            subnet={"choice": "other", "id": "o57es-2iaaa-aaaaa-qaaaa-yai"},
        )
        assert other["casals"]["subnet"] == "o57es-2iaaa-aaaaa-qaaaa-yai"


class TestPortalManifest:
    def test_includes_casals_federation_founder_not_github_urls(self):
        manifest = build_portal_manifest(
            name="Acme",
            slug="acme",
            network="staging",
            version="main",
            founder="2eqns-rmzes-7npxw-dxpw2-qdy2s-mw6ix-svdo2-oya7o-a6ldc-sqgwh-bqe",
            subnet={"choice": "automatic"},
        )
        assert manifest["casals"]["backend_wasm_key"] == "realm-backend@main"
        assert manifest["casals"]["frontend_wasm_key"] == "realm-assets@main"
        assert manifest["federation"]["slug"] == "acme"
        assert manifest["federation"]["portal_url"] == "https://staging.gos.earth/r/acme"
        assert manifest["founder"].startswith("2eqns-")
        assert "artifacts" not in manifest
        assert "github.com" not in json.dumps(manifest)
        assert manifest["gos"]["implementation"] == "realms-gos"
        assert "test_flags" not in manifest
        assert "can_test_mode" not in manifest

    def test_uses_gaas_config_flags_not_network(self):
        manifest = build_portal_manifest(
            name="Acme",
            slug="acme",
            network="demo",
            version="main",
            founder="aaaaa-aa",
            subnet={"choice": "automatic"},
            can_test_mode=False,
            test_flags={
                "test_mode": False,
                "user_self_registration": False,
                "demo_data": False,
                "ii_bypass": False,
                "skip_terms": False,
            },
        )
        assert manifest["can_test_mode"] is False
        assert manifest["test_flags"]["test_mode"] is False
        assert manifest["test_flags"]["ii_bypass"] is False

        on = build_portal_manifest(
            name="Acme",
            slug="acme",
            network="demo",
            version="main",
            founder="aaaaa-aa",
            subnet={"choice": "automatic"},
            can_test_mode=True,
            test_flags={
                "test_mode": True,
                "user_self_registration": True,
                "demo_data": True,
                "ii_bypass": True,
                "skip_terms": True,
            },
        )
        assert on["can_test_mode"] is True
        assert on["test_flags"]["ii_bypass"] is True

    def test_ic_portal_host_and_no_test_flags(self):
        manifest = build_portal_manifest(
            name="Acme",
            slug="acme",
            network="ic",
            version="latest",
            founder="aaaaa-aa",
            subnet={"choice": "automatic"},
        )
        assert manifest["federation"]["portal_url"] == "https://realmsgos.org/r/acme"
        assert manifest["deploy_version"] == "main"
        assert "test_flags" not in manifest

    def test_includes_codex_for_installer(self):
        manifest = build_portal_manifest(
            name="Acme",
            slug="acme",
            network="test",
            version="main",
            founder="aaaaa-aa",
            subnet={"choice": "automatic"},
            codex_package="agora",
            codex_version="1.0.0",
        )
        assert manifest["realm"]["codex"] == {
            "package": {"name": "agora", "version": "1.0.0"}
        }
        assert list(manifest["realm"]["extensions"]) == list(WIZARD_CHROME_EXTENSIONS)

    def test_codex_defaults_version_to_latest(self):
        manifest = build_portal_manifest(
            name="Acme",
            slug="acme",
            network="test",
            version="main",
            founder="aaaaa-aa",
            subnet={"choice": "automatic"},
            codex_package="syntropia",
        )
        assert manifest["realm"]["codex"]["package"]["version"] == "latest"


class TestInstallerFailure:
    def test_access_denied_includes_controller_hint(self):
        msg = _format_installer_job_failure(
            {"error": "set_canister_config_json: AccessDenied"},
            installer_id="jmgc7-2aaaa-aaaai-ax5qa-cai",
        )
        assert "AccessDenied" in msg
        assert "stand_template controllers" in msg
        assert "no installer allowlist" in msg
        assert "jmgc7-2aaaa-aaaai-ax5qa-cai" in msg
        assert "CLI bootstrap" in msg


class TestCreditsFail:
    def test_assert_credits_shortfall(self):
        with pytest.raises(StageError) as exc:
            assert_credits_sufficient(2, "aaaaa-aa", "staging")
        assert exc.value.stage == "credits"
        assert "add_credits" in exc.value.message
        assert "aaaaa-aa" in exc.value.message
        assert str(DEPLOYMENT_COST_CREDITS) in exc.value.message

    def test_command_credit_fail_mocked(self, tmp_path):
        cfg = tmp_path / "gaas.json"
        cfg.write_text(
            json.dumps(
                {
                    "name": "staging",
                    "domain": "staging.gos.earth",
                    "canisters": {
                        "realm_registry_backend": "7wzxh-wyaaa-aaaau-aggyq-cai",
                        "realm_installer": "lusjm-wqaaa-aaaau-ago7q-cai",
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        with patch("realms.cli.commands.new.assert_identity_is_ii_linked", return_value="web"), patch(
            "realms.cli.commands.new.resolve_identity_principal", return_value="aaaaa-aa"
        ), patch("realms.cli.commands.new.query_credit_balance", return_value=1):
            with pytest.raises(typer.Exit) as exc:
                new_command(
                    spec_file=None,
                    identity="alice",
                    network="staging",
                    name="Acme Realm",
                    codex="agora",
                    yes=True,
                    gaas_config=str(cfg),
                )
            assert exc.value.exit_code == 1


class TestIcMembersRefused:
    def test_ic_members_without_open_registration(self):
        err = check_ic_members_policy("ic", 3, False, [])
        assert err is not None
        assert "open-registration" in err

    def test_ic_members_allowed_with_flag_or_codes(self):
        assert check_ic_members_policy("ic", 3, True, []) is None
        assert check_ic_members_policy("ic", 3, False, ["deadbeef"]) is None
        assert check_ic_members_policy("staging", 3, False, []) is None
        assert check_ic_members_policy("ic", 0, False, []) is None

    def test_validate_rejects_ic_members(self):
        merged = _merged()
        merged["members"] = 2
        with pytest.raises(StageError) as exc:
            validate_merged_spec(merged, network="ic", identity="alice")
        assert "open-registration" in exc.value.message


class TestBrandingSize:
    def test_reject_oversize_file(self, tmp_path: Path):
        huge = tmp_path / "bg.png"
        huge.write_bytes(b"x" * (BRANDING_MAX_BYTES + 1))
        with pytest.raises(StageError) as exc:
            validate_branding_size(str(huge), "background")
        assert "1.5 MiB" in exc.value.message

    def test_accept_small_file(self, tmp_path: Path):
        small = tmp_path / "logo.png"
        small.write_bytes(b"\x89PNG")
        validate_branding_size(str(small), "logo")

    def test_validate_merged_rejects_huge_logo(self, tmp_path: Path):
        huge = tmp_path / "logo.png"
        huge.write_bytes(b"x" * (BRANDING_MAX_BYTES + 8))
        merged = _merged()
        merged["branding"]["logo"] = str(huge)
        with pytest.raises(StageError) as exc:
            validate_merged_spec(merged, network="staging", identity="alice")
        assert "1.5 MiB" in exc.value.message


class TestBrandingReachesTheDraft:
    """Images must travel the wizard's route: setup_save_draft, not setup_set_branding.

    setup_launch's upload phase reads the draft asset store; anything written by
    setup_set_branding lands under a key it never reads, so branding passed to
    ``realms new`` used to be accepted and then silently dropped.
    """

    @staticmethod
    def _drive(tmp_path: Path, branding: dict) -> list:
        merged = _merged()
        merged["branding"] = branding
        calls: list = []

        with patch(
            "realms.cli.commands.new._setup_json_call",
            side_effect=lambda backend, method, payload, *a, **k: (
                calls.append((method, payload)) or {"success": True}
            ),
        ), patch(
            "realms.cli.commands.new._setup_state_has_token", return_value=True
        ), patch(
            "realms.cli.commands.new.fetch_shared_token_catalog", return_value={}
        ), patch(
            "realms.cli.commands.new.preinstall_codex_dependencies"
        ), patch(
            "realms.cli.commands.new.call_setup_install_codex"
        ), patch(
            "realms.cli.commands.new.poll_setup_launch"
        ):
            run_setup_stage(merged, "be-1", "test", "deployer")
        return calls

    def test_images_go_out_as_save_draft_branding(self, tmp_path: Path):
        logo = tmp_path / "logo.png"
        logo.write_bytes(b"\x89PNG-logo")
        bg = tmp_path / "bg.png"
        bg.write_bytes(b"\x89PNG-bg")

        calls = self._drive(
            tmp_path,
            {"logo": str(logo), "background": str(bg), "primary": "#123456"},
        )

        assert "setup_set_branding" not in [method for method, _ in calls]

        branding = [
            payload["branding"]
            for method, payload in calls
            if method == "setup_save_draft" and "branding" in (payload or {})
        ]
        assert [sorted(part) for part in branding] == [
            ["logo_data_url"],
            ["background_data_url"],
            ["colors"],
        ]
        assert branding[0]["logo_data_url"].startswith("data:image/png;base64,")
        assert branding[2]["colors"] == {"primary": "#123456"}

    def test_branding_precedes_launch(self, tmp_path: Path):
        logo = tmp_path / "logo.png"
        logo.write_bytes(b"\x89PNG")

        methods = [method for method, _ in self._drive(tmp_path, {"logo": str(logo)})]
        assert methods.index("setup_save_draft") < methods.index("setup_launch")

    def test_text_only_branding_sends_no_branding_draft(self):
        calls = self._drive(Path("."), {"manifesto": "We build together."})

        assert "setup_set_branding" not in [method for method, _ in calls]
        assert not [
            payload
            for method, payload in calls
            if method == "setup_save_draft" and "branding" in (payload or {})
        ]
        identity = [
            payload["identity"]
            for method, payload in calls
            if method == "setup_save_draft" and "identity" in (payload or {})
        ]
        assert identity == [{"manifesto": "We build together."}]


class TestIdentityClassification:
    def test_known_pem_deploy_keys(self):
        assert classify_identity("deployer") == "pem_deploy"
        assert classify_identity("my_dev_identity_1") == "pem_deploy"

    def test_web_linked(self):
        assert classify_identity("alice", web_linked=True) == "web"

    def test_imported_ii_session_pem_is_not_refused(self):
        """Cloud agents import ``demo_identity1`` as a plaintext PEM + delegation."""
        assert classify_identity("demo_identity1", pem_file_exists=True) == "unknown"
        assert classify_identity("demo_identity1", web_linked=False) == "unknown"
        assert classify_identity("demo_identity1", web_linked=True) == "web"

    def test_web_auth_kind_counts_as_linked(self):
        assert _identity_kind_is_web("web-auth") is True
        assert _identity_kind_is_web("web") is True
        assert _identity_kind_is_web("pem") is False

    def test_refuse_message_shows_link_web(self):
        msg = identity_refuse_message("deployer", "staging")
        assert "icp identity link web deployer --app https://staging.realmsgos.org" in msg
        assert "--co-admin" in msg

    def test_command_refuses_pem_deploy_identity(self):
        with pytest.raises(typer.Exit) as exc:
            new_command(
                spec_file=None,
                identity="deployer",
                network="staging",
                name="Acme Realm",
                codex="agora",
                yes=True,
            )
        assert exc.value.exit_code == 1


class TestCoAdmin:
    SAMPLE = "2eqns-rmzes-7npxw-dxpw2-qdy2s-mw6ix-svdo2-oya7o-a6ldc-sqgwh-bqe"

    def test_deployer_refused_without_co_admin(self):
        merged = _merged()
        with pytest.raises(StageError) as exc:
            validate_merged_spec(merged, network="demo", identity="deployer")
        assert "co-admin" in exc.value.message.lower()

    def test_deployer_ok_with_co_admin(self):
        merged = _merged()
        merged["co_admin"] = self.SAMPLE
        validate_merged_spec(merged, network="demo", identity="deployer")

    def test_flag_overrides_spec(self):
        merged = merge_spec_and_flags(
            {"name": "Acme Realm", "codex": {"package": "agora"}, "co_admin": "old-id"},
            spec_dir=None,
            co_admin=self.SAMPLE,
        )
        assert merged["co_admin"] == self.SAMPLE

    def test_rejects_anonymous(self):
        merged = _merged()
        merged["co_admin"] = "2vxsx-fae"
        with pytest.raises(StageError) as exc:
            validate_merged_spec(merged, network="demo", identity="deployer")
        assert "anonymous" in exc.value.message.lower()

    def test_rejects_short_principal(self):
        merged = _merged()
        merged["co_admin"] = "not-a-principal"
        with pytest.raises(StageError) as exc:
            validate_merged_spec(merged, network="demo", identity="deployer")
        assert "principal" in exc.value.message.lower()


class TestHelp:
    def test_realms_new_help(self):
        result = runner.invoke(app, ["new", "--help"])
        assert result.exit_code == 0
        output = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
        assert "--identity" in output
        assert "--network" in output
        assert "--codex" in output
        assert "--resume" in output
        assert "--co-admin" in output
        assert "--log-file" in output
        assert "--gaas-config" in output
        assert "--deploy-mode" in output
        assert "wizard" in output.lower() or "live" in output.lower() or "standalone" in output.lower()


class TestGaasConfig:
    def _write(self, tmp_path: Path, **overrides) -> Path:
        payload = {
            "name": "demo",
            "domain": "demo.gos.earth",
            "canisters": {
                "realm_registry_backend": "5ocwl-eiaaa-aaaah-av2bq-cai",
                "realm_installer": "53fhg-faaaa-aaaah-av2ca-cai",
            },
        }
        payload.update(overrides)
        path = tmp_path / "demo-gaas.json"
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return path

    def test_load_extracts_queue_ids(self, tmp_path: Path):
        path = self._write(tmp_path)
        gaas = load_gaas_config(path)
        assert gaas.env_name == "demo"
        assert gaas.registry_id == "5ocwl-eiaaa-aaaah-av2bq-cai"
        assert gaas.installer_id == "53fhg-faaaa-aaaah-av2ca-cai"
        assert gaas.portal_host == "https://demo.gos.earth"
        assert gaas.can_test_mode is None
        assert gaas.test_flags == {}

    def test_load_reads_test_flag_config(self, tmp_path: Path):
        path = self._write(
            tmp_path,
            flags={"can_test_mode": False},
            test_flags={"test_mode": False, "ii_bypass": True},
        )
        gaas = load_gaas_config(path)
        assert gaas.can_test_mode is False
        assert gaas.test_flags["test_mode"] is False
        assert gaas.test_flags["ii_bypass"] is True

    def test_resolve_infers_network_from_name(self, tmp_path: Path):
        path = self._write(tmp_path)
        gaas, network = resolve_gos_queue(gaas_config=str(path), network="")
        assert network == "demo"
        assert gaas.registry_id.endswith("-cai")

    def test_resolve_rejects_network_mismatch(self, tmp_path: Path):
        path = self._write(tmp_path)
        with pytest.raises(StageError) as exc:
            resolve_gos_queue(gaas_config=str(path), network="staging")
        assert "does not match" in exc.value.message

    def test_missing_file_is_required(self):
        with pytest.raises(StageError) as exc:
            resolve_gos_queue(gaas_config=None, network="demo")
        assert "--gaas-config is required" in exc.value.message

    def test_command_requires_gaas_config(self):
        with pytest.raises(typer.Exit):
            new_command(
                spec_file=None,
                identity="alice",
                network="demo",
                name="Acme Realm",
                codex="agora",
                yes=True,
            )

    def test_portal_url_uses_config_domain(self, tmp_path: Path):
        path = self._write(tmp_path)
        gaas = load_gaas_config(path)
        url = portal_url_for_slug("acme", "demo", portal_host=gaas.portal_host)
        assert url == "https://demo.gos.earth/r/acme"


class TestDeployMode:
    def test_normalize_defaults_to_gaas(self):
        assert normalize_deploy_mode(None) == "gaas"
        assert normalize_deploy_mode("GAAS") == "gaas"

    def test_normalize_rejects_unknown(self):
        with pytest.raises(StageError) as exc:
            normalize_deploy_mode("casals")
        assert "gaas" in exc.value.message
        assert "standalone" in exc.value.message

    def test_standalone_latest_urls(self, monkeypatch):
        from realms.cli import release_assets

        monkeypatch.setattr(release_assets, "latest_tag", lambda **_: "v0.6.1")
        backend, frontend = standalone_artifact_urls("latest")
        assert backend.endswith("/releases/download/v0.6.1/realm-backend-v0.6.1.wasm.gz")
        assert frontend.endswith("/releases/download/v0.6.1/realm-frontend-v0.6.1.tar.gz")

    def test_standalone_latest_unresolvable(self, monkeypatch):
        from realms.cli import release_assets

        def offline(**_):
            raise OSError("offline")

        monkeypatch.setattr(release_assets, "latest_tag", offline)
        with pytest.raises(StageError) as exc:
            standalone_artifact_urls("latest")
        assert "latest" in exc.value.message

    def test_standalone_semver_urls(self):
        backend, frontend = standalone_artifact_urls("0.3.5")
        assert "/releases/download/v0.3.5/realm_backend.wasm.gz" in backend
        assert "/releases/download/v0.3.5/realm_frontend.tar.gz" in frontend

    def test_standalone_stamped_semver_urls(self):
        backend, frontend = standalone_artifact_urls("v0.6.1")
        assert backend.endswith("/releases/download/v0.6.1/realm-backend-v0.6.1.wasm.gz")
        assert frontend.endswith("/releases/download/v0.6.1/realm-frontend-v0.6.1.tar.gz")

    def test_standalone_rejects_build(self):
        with pytest.raises(StageError) as exc:
            standalone_artifact_urls("build")
        assert "build" in exc.value.message.lower()

    def test_standalone_rejects_gaas_config(self, tmp_path: Path):
        path = tmp_path / "demo-gaas.json"
        path.write_text("{}", encoding="utf-8")
        with pytest.raises(typer.Exit):
            new_command(
                spec_file=None,
                identity="alice",
                network="demo",
                name="Acme Realm",
                codex="agora",
                yes=True,
                gaas_config=str(path),
                deploy_mode="standalone",
            )

    @patch("realms.cli.commands.new.join_geister_members", return_value=[])
    @patch("realms.cli.commands.new.import_data_overlay")
    @patch("realms.cli.commands.new.apply_runtime_config")
    @patch("realms.cli.commands.new.register_co_admin_principal")
    @patch("realms.cli.commands.new.run_setup_stage")
    @patch("realms.cli.commands.new.setup_already_complete", return_value=True)
    @patch("realms.cli.commands.new.apply_bootstrap_canister_config")
    @patch("realms.cli.commands.new.ensure_founder_enter_setup")
    @patch(
        "realms.cli.commands.new.deploy_standalone_realm",
        return_value=("aaaaa-aaaaa-aaaaa-aaaaa-aaa", "bbbbb-bbbbb-bbbbb-bbbbb-bbb"),
    )
    @patch(
        "realms.cli.commands.new.resolve_identity_principal",
        return_value="aaaaa-aaaaa-aaaaa-aaaaa-aaa",
    )
    @patch("realms.cli.commands.new.assert_identity_is_ii_linked")
    def test_standalone_skips_credits_and_installer(
        self,
        _linked,
        _principal,
        mock_deploy,
        mock_enter,
        _wait,
        _complete,
        _setup,
        _co_admin,
        _config,
        _data,
        _members,
        capsys,
    ):
        new_command(
            spec_file=None,
            identity="alice",
            network="demo",
            name="Acme Realm",
            slug="acme",
            codex="agora",
            yes=True,
            deploy_mode="standalone",
        )
        mock_deploy.assert_called_once()
        mock_enter.assert_called_once()
        out = capsys.readouterr().out
        assert "standalone" in out
        assert "bbbbb-bbbbb-bbbbb-bbbbb-bbb" in out


class TestGaasFlow:
    def _gaas_config(self, tmp_path: Path) -> str:
        path = tmp_path / "test-gaas.json"
        path.write_text(
            json.dumps(
                {
                    "name": "test",
                    "domain": "test.gos.earth",
                    "canisters": {
                        "realm_registry_backend": "mq5y2-riaaa-aaaai-ax5pq-cai",
                        "realm_installer": "jmgc7-2aaaa-aaaai-ax5qa-cai",
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return str(path)

    @patch("realms.cli.commands.new.join_geister_members", return_value=[])
    @patch("realms.cli.commands.new.import_data_overlay")
    @patch("realms.cli.commands.new.apply_runtime_config")
    @patch("realms.cli.commands.new.register_co_admin_principal")
    @patch("realms.cli.commands.new.run_setup_stage")
    @patch("realms.cli.commands.new.setup_already_complete", return_value=False)
    @patch("realms.cli.commands.new.apply_bootstrap_canister_config")
    @patch("realms.cli.commands.new.ensure_founder_enter_setup")
    @patch(
        "realms.cli.commands.new.poll_installer_until_ready",
        return_value={
            "status": "completed",
            "backend_canister_id": "aaaaa-aaaaa-aaaaa-aaaaa-aaa",
            "frontend_canister_id": "bbbbb-bbbbb-bbbbb-bbbbb-bbb",
        },
    )
    @patch("realms.cli.commands.new.request_deployment", return_value="job-123")
    @patch("realms.cli.commands.new.query_credit_balance", return_value=10)
    @patch(
        "realms.cli.commands.new.resolve_identity_principal",
        return_value="aaaaa-aaaaa-aaaaa-aaaaa-aaa",
    )
    @patch("realms.cli.commands.new.assert_identity_is_ii_linked")
    def test_gaas_skips_cli_bootstrap_after_installer(
        self,
        _linked,
        _principal,
        _credits,
        mock_request,
        mock_poll,
        mock_enter,
        mock_config,
        _setup_complete,
        mock_setup,
        _co_admin,
        _runtime,
        _data,
        _members,
        tmp_path: Path,
        capsys,
    ):
        new_command(
            spec_file=None,
            identity="alice",
            network="test",
            name="Acme Realm",
            slug="acme",
            codex="agora",
            yes=True,
            gaas_config=self._gaas_config(tmp_path),
        )
        mock_request.assert_called_once()
        manifest = mock_request.call_args[0][0]
        assert manifest["realm"]["codex"]["package"]["name"] == "agora"
        mock_poll.assert_called_once()
        mock_enter.assert_not_called()
        mock_config.assert_not_called()
        mock_setup.assert_called_once()
        out = capsys.readouterr().out
        assert "test.gos.earth/r/acme" in out
        assert "job-123" in out

    @patch("realms.cli.commands.new.join_geister_members", return_value=[])
    @patch("realms.cli.commands.new.import_data_overlay")
    @patch("realms.cli.commands.new.apply_runtime_config")
    @patch("realms.cli.commands.new.register_co_admin_principal")
    @patch("realms.cli.commands.new.run_setup_stage")
    @patch("realms.cli.commands.new.setup_already_complete", return_value=False)
    @patch(
        "realms.cli.commands.new.resolve_registered_slug",
        return_value={
            "backend_canister_id": "aaaaa-aaaaa-aaaaa-aaaaa-aaa",
            "frontend_canister_id": "bbbbb-bbbbb-bbbbb-bbbbb-bbb",
            "portal_url": "https://test.gos.earth/r/acme",
        },
    )
    @patch("realms.cli.commands.new.poll_installer_until_ready")
    @patch("realms.cli.commands.new.request_deployment")
    @patch("realms.cli.commands.new.query_credit_balance")
    @patch(
        "realms.cli.commands.new.resolve_identity_principal",
        return_value="aaaaa-aaaaa-aaaaa-aaaaa-aaa",
    )
    @patch("realms.cli.commands.new.assert_identity_is_ii_linked")
    def test_gaas_from_stage_setup_resolves_slug(
        self,
        _linked,
        _principal,
        mock_credits,
        mock_request,
        mock_poll,
        mock_resolve,
        _setup_complete,
        mock_setup,
        _co_admin,
        _runtime,
        _data,
        _members,
        tmp_path: Path,
        capsys,
    ):
        new_command(
            spec_file=None,
            identity="alice",
            network="test",
            name="Acme Realm",
            slug="acme",
            codex="agora",
            yes=True,
            gaas_config=self._gaas_config(tmp_path),
            from_stage="setup",
        )
        mock_credits.assert_not_called()
        mock_request.assert_not_called()
        mock_poll.assert_not_called()
        mock_resolve.assert_called_once()
        mock_setup.assert_called_once()
        out = capsys.readouterr().out
        assert "from-stage setup" in out
        assert "test.gos.earth/r/acme" in out


class TestFromStage:
    def test_parse_index_and_id(self):
        assert parse_from_stage(None, GAAS_STAGES) == 0
        assert parse_from_stage("setup", GAAS_STAGES) == 5
        assert parse_from_stage("co-admin", GAAS_STAGES) == 6
        assert parse_from_stage("co_admin", GAAS_STAGES) == 6
        assert parse_from_stage("6", GAAS_STAGES) == 5

    def test_rejects_unknown(self):
        with pytest.raises(StageError) as exc:
            parse_from_stage("not-a-stage", GAAS_STAGES)
        assert "unknown --from-stage" in exc.value.message
        assert "setup" in exc.value.message

    def test_stage_ids_keep_validate(self):
        ids = stage_ids_to_run(GAAS_STAGES, "setup")
        assert "validate" in ids
        assert "setup" in ids
        assert "credits" not in ids
        assert "deploy" not in ids
        assert "poll" not in ids
        assert "members" in ids

    def test_standalone_catalog_has_no_poll(self):
        catalog = format_stage_catalog(STANDALONE_STAGES)
        assert "poll" not in catalog
        assert "deploy" in catalog

    def test_catalog_symbol_does_not_need_canister(self):
        # Resolved against the realm's own catalog at setup time, not here.
        merged = _merged(token={"symbol": "ckEURC", "canister": ""})
        validate_merged_spec(merged, network="test", identity="alice")

    def test_token_draft_resolves_catalog_ledger(self):
        assert _token_draft({"symbol": "ckEURC"}, _CATALOG) == {
            "symbol": "ckEURC",
            "token_canister_id": "eeeee-ee",
            "decimals": 6,
        }
        assert _token_draft({"canister": "aaaaa-aa", "symbol": "ckEURC"}, _CATALOG) == {
            "token_canister_id": "aaaaa-aa",
            "symbol": "ckEURC",
        }
        assert _token_draft({}, _CATALOG) is None
        # Not in this realm's catalog and no canister: nothing to invent.
        assert _token_draft({"symbol": "MYTOKEN"}, _CATALOG) == {"symbol": "MYTOKEN"}
        assert _token_draft({"symbol": "ckEURC"}, {}) == {"symbol": "ckEURC"}

    def test_default_is_the_mundus_token_whatever_its_symbol(self):
        # setup/+page.svelte preselects the first catalog card; the CLI prefers
        # the mundus token (REALMS locally, RLM in production).
        assert "REALMS" in DEFAULT_TOKEN_SYMBOLS and "RLM" in DEFAULT_TOKEN_SYMBOLS
        assert default_catalog_symbol(_CATALOG) == "REALMS"
        assert default_catalog_symbol({"RLM": {"ledger": "rrrrr-rr"}, "ckBTC": {"ledger": "b"}}) == "RLM"
        assert default_catalog_symbol({"ckBTC": {"ledger": "bbbbb-bb"}}) == "ckBTC"
        assert default_catalog_symbol({}) is None
        assert catalog_token_draft("REALMS", _CATALOG) == {
            "symbol": "REALMS",
            "token_canister_id": "rrrrr-rr",
            "decimals": 8,
        }

    def test_catalog_symbol_is_case_insensitive(self):
        assert catalog_token_symbol("ckeurc", _CATALOG) == "ckEURC"
        assert catalog_token_symbol("realms", _CATALOG) == "REALMS"
        assert catalog_token_symbol("mytoken", _CATALOG) is None

    def test_ckbtc_carries_its_indexer(self):
        draft = catalog_token_draft("ckBTC", _CATALOG)
        assert draft["indexer_canister_id"] == "bbbbb-ix"
        assert catalog_token_draft("ckEURC", {"ckEURC": {"ledger": ""}}) is None

    def test_unwrap_ok_json(self):
        inner = {"success": True, "backend_canister_id": "abc"}
        assert _unwrap_ok_json({"Ok": json.dumps(inner)}) == inner
        assert _unwrap_ok_json(inner) == inner

    @patch("realms.cli.commands.new.time.sleep")
    @patch("realms.cli.commands.new._setup_json_call")
    def test_setup_install_codex_retries_in_progress(self, mock_call, _sleep):
        from realms.cli.commands.new import call_setup_install_codex

        mock_call.side_effect = [
            {"success": True, "status": "in_progress", "phase": "pull_backend"},
            {"success": True, "status": "complete", "version": "0.9.6"},
        ]
        raw = call_setup_install_codex("be", "agora", None, "test", "alice")
        assert raw["status"] == "complete"
        assert mock_call.call_count == 2


def test_new_help_lists_from_stage():
    result = runner.invoke(app, ["new", "--help"])
    assert result.exit_code == 0
    text = result.output.replace("\x1b[0m", "").replace("\x1b[1m", "")
    assert "from-stage" in text


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_wizard_chrome_is_the_shell_not_a_codex():
    agora_deps = _local_manifest_dependencies("agora")
    for chrome in WIZARD_CHROME_EXTENSIONS:
        assert chrome not in agora_deps
    # The realm shell needs its own settings page; no codex declares it.
    assert "realm_settings" in WIZARD_CHROME_EXTENSIONS


def test_cli_and_wizard_carry_no_token_ledger_table():
    """Both read the catalog from the realm (get_setup_state().shared_tokens)."""
    wizard = (
        _repo_root() / "src/realm_frontend/src/lib/setup/sharedTokens.ts"
    ).read_text(encoding="utf-8")
    cli = (_repo_root() / "cli/realms/cli/commands/new.py").read_text(encoding="utf-8")
    for text in (wizard, cli):
        assert "SHARED_TOKEN_CATALOG" not in text
        assert "CATALOG_TOKENS" not in text
        assert not re.search(r"['\"][a-z0-9]{5}(-[a-z0-9]{5}){3}-cai['\"]", text)


def test_local_manifest_dependencies_lists_syntropia_extensions():
    deps = _local_manifest_dependencies("syntropia")
    assert "access_manager" in deps
    assert "zone_selector" in deps
    assert deps[-1] == "syntropia"


def test_parse_jsonish_unwraps_dfx_text_json_string():
    raw = json.dumps(json.dumps({"success": True, "status": "setup", "creator": None}))
    parsed = _parse_jsonish(raw)
    assert parsed["success"] is True
    assert parsed["status"] == "setup"


