"""The sandboxed shell publish must not let a stale frontend/dist win."""

import hashlib
from pathlib import Path
from unittest.mock import patch

from realms.cli.commands.extension import (
    _frontend_shell_uploads,
    _upload_one_file,
)


def _touch(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_runtime_index_html_replaces_legacy_shell(tmp_path):
    legacy = tmp_path / "frontend" / "dist"
    runtime = tmp_path / "frontend-rt" / "dist"
    _touch(legacy / "index.html", "<html>old</html>\n")
    _touch(legacy / "assets" / "index-old.js", "old();\n")
    _touch(runtime / "index.html", "<html>new</html>\n")
    _touch(runtime / "assets" / "index-new.js", "new();\n")

    plan = dict(_frontend_shell_uploads(str(legacy), str(runtime)))

    assert plan["frontend/dist/index.html"].endswith("frontend-rt/dist/index.html")
    assert "frontend/dist/assets/index-old.js" in plan
    assert plan["frontend/dist/assets/index-new.js"].endswith("index-new.js")
    assert Path(plan["frontend/dist/index.html"]).read_text(encoding="utf-8") == "<html>new</html>\n"


def test_legacy_shell_used_when_runtime_dist_is_absent(tmp_path):
    legacy = tmp_path / "frontend" / "dist"
    _touch(legacy / "index.html", "<html>only</html>\n")

    plan = dict(
        _frontend_shell_uploads(str(legacy), str(tmp_path / "frontend-rt" / "dist"))
    )

    assert plan["frontend/dist/index.html"].endswith("frontend/dist/index.html")


def test_bundle_path_replaces_runtime_index_js(tmp_path):
    runtime = tmp_path / "frontend-rt" / "dist"
    _touch(runtime / "index.js", "export default {};\n")
    _touch(runtime / "index.html", "<html></html>\n")
    bundle = tmp_path / "override.js"
    _touch(bundle, "export default { override: true };\n")

    plan = dict(
        _frontend_shell_uploads(str(tmp_path / "missing"), str(runtime), str(bundle))
    )

    assert Path(plan["frontend/dist/index.js"]).read_text(encoding="utf-8").startswith(
        "export default { override"
    )
    assert "frontend/dist/index.html" in plan


def test_upload_updates_hash_snapshot(tmp_path):
    shell = tmp_path / "index.html"
    shell.write_text("<html>new</html>\n", encoding="utf-8")
    expected = hashlib.sha256(shell.read_bytes()).hexdigest()
    seen = {"frontend/dist/index.html": "stale-snapshot"}

    with patch(
        "realms.cli.commands.extension._dfx_call",
        return_value='{"ok": true, "done": true}',
    ):
        result = _upload_one_file(
            "registry",
            "ext/member_dashboard/1.1.20",
            "frontend/dist/index.html",
            str(shell),
            "ic",
            None,
            existing_hashes=seen,
        )

    assert result == "uploaded"
    assert seen["frontend/dist/index.html"] == expected
