"""Tests for Realms CLI models."""

import pytest
from pydantic import ValidationError

from realms.cli.models import (
    DeploymentConfig,
    Extension,
    PostDeploymentAction,
    RealmConfig,
    RealmMetadata,
)


class TestRealmMetadata:
    """Test RealmMetadata model."""

    def test_valid_realm_metadata(self):
        """Test valid realm metadata creation."""
        metadata = RealmMetadata(
            id="test_realm",
            name="Test Realm",
            manifesto="A test realm",
            admin_principal="2vxsx-fae",
        )
        assert metadata.id == "test_realm"
        assert metadata.name == "Test Realm"
        assert metadata.version == "1.0.0"  # default

    def test_invalid_realm_id(self):
        """Test invalid realm ID validation."""
        with pytest.raises(ValidationError):
            RealmMetadata(
                id="test-realm",  # hyphens not allowed
                name="Test Realm",
                manifesto="A test realm",
                admin_principal="2vxsx-fae",
            )

    def test_valid_realm_id_patterns(self):
        """Test valid realm ID patterns."""
        valid_ids = ["test_realm", "realm123", "my_gov_realm_v2"]

        for realm_id in valid_ids:
            metadata = RealmMetadata(
                id=realm_id,
                name="Test Realm",
                manifesto="A test realm",
                admin_principal="2vxsx-fae",
            )
            assert metadata.id == realm_id


class TestDeploymentConfig:
    """Test DeploymentConfig model."""

    def test_valid_deployment_config(self):
        """Test valid deployment configuration."""
        config = DeploymentConfig(network="local")
        assert config.network == "local"
        assert config.clean_deploy is True  # default

    def test_invalid_network(self):
        """Test invalid network validation."""
        with pytest.raises(ValidationError):
            DeploymentConfig(network="invalid_network")

    def test_port_validation(self):
        """Test port validation."""
        # Valid port
        config = DeploymentConfig(network="local", port=8080)
        assert config.port == 8080

        # Invalid ports
        with pytest.raises(ValidationError):
            DeploymentConfig(network="local", port=4942)  # too low

        with pytest.raises(ValidationError):
            DeploymentConfig(network="local", port=8101)  # too high


class TestExtension:
    """Test Extension model."""

    def test_valid_extension(self):
        """Test valid extension creation."""
        ext = Extension(name="test_extension")
        assert ext.name == "test_extension"
        assert ext.source == "local"  # default
        assert ext.enabled is True  # default

    def test_extension_with_params(self):
        """Test extension with initialization parameters."""
        ext = Extension(
            name="test_extension", init_params={"theme": "dark", "features": ["a", "b"]}
        )
        assert ext.init_params["theme"] == "dark"
        assert ext.init_params["features"] == ["a", "b"]


class TestPostDeploymentAction:
    """Test PostDeploymentAction model."""

    def test_wait_action(self):
        """Test wait action."""
        action = PostDeploymentAction(type="wait", duration=5)
        assert action.type == "wait"
        assert action.duration == 5

    def test_extension_call_action(self):
        """Test extension call action."""
        action = PostDeploymentAction(
            type="extension_call",
            extension_name="demo_loader",
            function_name="load",
            args={"step": "base_setup"},
        )
        assert action.type == "extension_call"
        assert action.extension_name == "demo_loader"
        assert action.function_name == "load"
        assert action.args["step"] == "base_setup"

    def test_script_action(self):
        """Test script action."""
        action = PostDeploymentAction(type="script", script_path="./setup.sh")
        assert action.type == "script"
        assert action.script_path == "./setup.sh"


class TestRealmConfig:
    """Test complete RealmConfig model."""

    def test_valid_realm_config(self):
        """Test valid complete realm configuration."""
        config_data = {
            "realm": {
                "id": "test_realm",
                "name": "Test Realm",
                "manifesto": "A test realm",
                "admin_principal": "2vxsx-fae",
            },
            "deployment": {"network": "local"},
            "extensions": {"initial": [{"name": "demo_loader", "enabled": True}]},
        }

        config = RealmConfig(**config_data)
        assert config.realm.id == "test_realm"
        assert config.deployment.network == "local"
        assert len(config.extensions["initial"]) == 1

    def test_invalid_extension_phase(self):
        """Test invalid extension phase validation."""
        config_data = {
            "realm": {
                "id": "test_realm",
                "name": "Test Realm",
                "manifesto": "A test realm",
                "admin_principal": "2vxsx-fae",
            },
            "deployment": {"network": "local"},
            "extensions": {
                "invalid_phase": [  # invalid phase name
                    {"name": "demo_loader", "enabled": True}
                ]
            },
        }

        with pytest.raises(ValidationError):
            RealmConfig(**config_data)

    def test_valid_extension_phases(self):
        """Test valid extension phase names."""
        valid_phases = ["q1", "q2", "q3", "q4", "initial", "phase_1", "phase_10"]

        for phase in valid_phases:
            config_data = {
                "realm": {
                    "id": "test_realm",
                    "name": "Test Realm",
                    "manifesto": "A test realm",
                    "admin_principal": "2vxsx-fae",
                },
                "deployment": {"network": "local"},
                "extensions": {phase: [{"name": "demo_loader", "enabled": True}]},
            }

            config = RealmConfig(**config_data)
            assert phase in config.extensions
