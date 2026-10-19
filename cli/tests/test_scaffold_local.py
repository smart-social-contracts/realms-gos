"""`realms new extension|codex` scaffolding and `realms local` helpers."""

import ast
import json
from pathlib import Path
from unittest.mock import patch

import pytest
import typer
from typer.testing import CliRunner

from realms.cli.commands import local, scaffold
from realms.cli.commands.extension import _candid_unescape, _parse_candid_string
from realms.cli.main import app


def _no_placeholders(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            for marker in ("__EXTENSION_", "__CODEX_"):
                assert marker not in text, f"{path} still contains {marker}"


def test_ids_and_names_from_directory():
    assert scaffold.id_from_name("my-hello-world") == "my_hello_world"
    assert scaffold.id_from_name("MyHelloWorld") == "my_hello_world"
    assert scaffold.display_name_from_name("my-hello-world") == "My Hello World"


@pytest.mark.parametrize("bad", ["x1", "1abc", "Hello", "a-b-c", "a" * 40])
def test_invalid_ids_are_rejected(bad):
    with pytest.raises(typer.Exit):
        scaffold.validate_id("extension", bad)


def test_new_extension_project(tmp_path):
    dest = scaffold.new_extension_command(str(tmp_path / "my-hello-world"), install=False)
    manifest = json.loads((dest / "manifest.json").read_text())
    assert manifest["name"] == "my_hello_world"
    assert manifest["runtime"] == "sandboxed"
    assert manifest["entry_access"]["functions"]["greet"] == "member"
    root_pkg = json.loads((dest / "package.json").read_text())
    assert root_pkg["workspaces"] == ["frontend"]
    assert "build" in root_pkg["scripts"]
    app_svelte = (dest / "frontend" / "src" / "App.svelte").read_text()
    assert 'data-testid="hello-button"' in app_svelte
    assert 'data-testid="hello-result"' in app_svelte
    assert "callExtension<string>('greet', {})" in app_svelte
    _no_placeholders(dest)


def test_extension_backend_says_hello_world(tmp_path):
    dest = scaffold.new_extension_command(str(tmp_path / "hello-ext"), install=False)
    namespace: dict = {}
    exec((dest / "backend" / "entry.py").read_text(), namespace)
    assert json.loads(namespace["greet"]("{}")) == "Hello, World!"
    assert json.loads(namespace["greet"]('{"name": "Realm"}')) == "Hello, Realm!"


def test_new_codex_project(tmp_path):
    dest = scaffold.new_codex_command(
        str(tmp_path / "my-hello-codex"), extensions=["my_hello_world"]
    )
    manifest = json.loads((dest / "manifest.json").read_text())
    assert manifest["id"] == "my_hello_codex"
    assert manifest["kind"] == "codex"
    assert manifest["codex_api_version"] == 1
    assert manifest["dependencies"] == ["my_hello_world"]
    assert manifest["onboarding"]["registration"]["open_registration"] is True
    source = (dest / "backend" / "entry.py").read_text()
    functions = {n.name for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef)}
    assert {"get_config", "init"} <= functions
    _no_placeholders(dest)


def test_scaffold_refuses_non_empty_directory(tmp_path):
    (tmp_path / "taken").mkdir()
    (tmp_path / "taken" / "file.txt").write_text("x")
    with pytest.raises(typer.Exit):
        scaffold.new_codex_command(str(tmp_path / "taken"))


def test_cli_routes_new_extension_and_keeps_realm_form(tmp_path):
    runner = CliRunner()
    result = runner.invoke(app, ["new", "codex", str(tmp_path / "cdx"), "--extension", "my_ext"])
    assert result.exit_code == 0, result.output
    assert json.loads((tmp_path / "cdx" / "manifest.json").read_text())["dependencies"] == ["my_ext"]

    result = runner.invoke(app, ["new"])
    assert result.exit_code == 1
    assert "--identity is required" in result.output


@pytest.mark.parametrize(
    "installed,tag",
    [("0.7.0rc3", "v0.7.0-rc3"), ("0.7.0", "v0.7.0"), ("1.2.3b1", "v1.2.3-b1")],
)
def test_cli_release_tag(installed, tag):
    with patch("importlib.metadata.version", return_value=installed):
        assert local.cli_release_tag() == tag


def test_candid_unescape():
    assert _candid_unescape(r"it\'s") == "it's"
    assert _candid_unescape(r"say \"hi\"") == 'say "hi"'
    assert _candid_unescape(r"a\nb") == "a\nb"
    assert _candid_unescape(r"literal \\n") == "literal \\n"
    assert _candid_unescape(r"caf\u{e9}") == "café"
    assert _candid_unescape(r"caf\c3\a9") == "café"


def test_parse_candid_string_with_apostrophe():
    raw = (
        '(\n  "{\\"launch\\":{\\"status\\":\\"failed\\",'
        '\\"error\\":\\"No versions found for extension \\\'role_manager\\\'\\"}}",\n)\n'
    )
    data = json.loads(_parse_candid_string(raw))
    assert data["launch"]["error"] == "No versions found for extension 'role_manager'"


def test_ext_call_reply_parsing():
    reply = 'record {\n  response = "\\"Hello, World!\\"";\n  success = true;\n}\n'
    text = _candid_unescape(local._RESPONSE_FIELD.search(reply).group(1))
    assert json.loads(text) == "Hello, World!"
    assert local._SUCCESS_FIELD.search(reply).group(1) == "true"
