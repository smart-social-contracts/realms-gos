import pytest

from realms.cli import release_assets


@pytest.mark.parametrize(
    "name,tag,expected",
    [
        ("realm_backend.wasm.gz", "v0.6.0", "realm_backend.wasm.gz"),
        ("realm_backend.wasm.gz", "v0.5.2", "realm_backend.wasm.gz"),
        ("realm_backend.wasm.gz", "v0.6.1", "realm-backend-v0.6.1.wasm.gz"),
        ("realm_frontend.tar.gz", "0.6.1", "realm-frontend-v0.6.1.tar.gz"),
        ("marketplace_frontend.manifest.json", "v0.7.0", "marketplace-frontend-v0.7.0.manifest.json"),
        ("realm_backend.did", "v1.0.0", "realm-backend-v1.0.0.did"),
        ("realm_backend.wasm.gz", "main", "realm_backend.wasm.gz"),
    ],
)
def test_asset_name(name, tag, expected):
    assert release_assets.asset_name(name, tag) == expected


def test_asset_url_normalizes_tag():
    assert release_assets.asset_url("realm_backend.wasm.gz", "0.6.1") == (
        "https://github.com/smart-social-contracts/realms-gos/releases/download/v0.6.1/realm-backend-v0.6.1.wasm.gz"
    )


class _Resp:
    def __init__(self, url):
        self._url = url

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_latest_tag_reads_redirect(monkeypatch):
    monkeypatch.setattr(
        release_assets.urllib.request,
        "urlopen",
        lambda req, timeout: _Resp("https://github.com/smart-social-contracts/realms-gos/releases/tag/v0.6.1"),
    )
    assert release_assets.latest_tag() == "v0.6.1"


def test_latest_tag_without_releases(monkeypatch):
    monkeypatch.setattr(
        release_assets.urllib.request,
        "urlopen",
        lambda req, timeout: _Resp("https://github.com/smart-social-contracts/realms-gos/releases"),
    )
    with pytest.raises(ValueError):
        release_assets.latest_tag()
