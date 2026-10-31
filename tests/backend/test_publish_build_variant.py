"""Publish pipeline must label test-variant realm builds distinctly."""

import argparse
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
PUBLISH_BUILD = REPO_ROOT / "scripts" / "publish_build.py"


def _load_publish_build():
    spec = importlib.util.spec_from_file_location("_publish_build", PUBLISH_BUILD)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(REPO_ROOT / "cli"))
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def publish_build():
    return _load_publish_build()


def test_default_variant_is_production(publish_build):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--variant",
        choices=publish_build.BUILD_VARIANTS,
        default=publish_build.DEFAULT_BUILD_VARIANT,
    )
    args = parser.parse_args([])
    assert args.variant == "production"


def test_test_variant_main_snapshot_label_is_not_main_channel(publish_build):
    base = "main.1749254400.abc1234"
    labeled = publish_build.apply_build_variant_label(base, "test")
    assert labeled == "main-test.1749254400.abc1234"
    assert labeled != base
    assert not labeled.startswith("main.")


def test_test_variant_semver_label_is_distinguishable(publish_build):
    base = "0.4.0"
    labeled = publish_build.apply_build_variant_label(base, "test")
    assert labeled == "0.4.0+test"
    assert labeled != base


def test_production_label_is_unchanged(publish_build):
    for base in ("main.1749254400.abc1234", "0.4.0"):
        assert publish_build.apply_build_variant_label(base, "production") == base


def test_build_variant_label_round_trips(publish_build):
    cases = (
        ("main.1749254400.abc1234", "test"),
        ("0.4.0", "test"),
        ("main.100.deadbeef", "production"),
        ("1.2.3", "production"),
    )
    for base, variant in cases:
        labeled = publish_build.apply_build_variant_label(base, variant)
        parsed_base, parsed_variant = publish_build.parse_build_variant_label(labeled)
        assert parsed_base == base
        assert parsed_variant == variant


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_the_test_variant_is_never_published_to_staging_or_production(publish_build, monkeypatch, environment):
    argv = ["publish_build.py", "--environment", environment, "--family", "realm", "--version", "1.0.0",
            "--variant", "test", "--file-registry", "registry"]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit, match="never published"):
        publish_build.main()
