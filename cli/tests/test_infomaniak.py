"""Unit tests for the Infomaniak DNS applier. No network."""

from __future__ import annotations

import pytest

from realms.cli.commands.domains import (
    DomainTarget,
    _check_dns_credentials,
    _dns_settings,
    apply_target,
)
from realms.cli.dns import render_dns_records
from realms.cli.infomaniak import (
    APEX_ADDRESSES,
    InfomaniakDns,
    InfomaniakError,
    apply_records,
)

CANISTER = "kv5e2-yiaaa-aaaak-qze3a-cai"
ZONE = "realmsgos.org"


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _FakeSession:
    """An Infomaniak zone: lists, creates and updates records, two per page."""

    def __init__(self, records=()):
        self.records = [dict(r, id=i) for i, r in enumerate(records, start=1)]
        self.calls: list[tuple[str, str, dict]] = []
        self.failure: _Response | None = None

    def request(self, method, url, headers=None, timeout=None, **kwargs):
        path = url.split("api.infomaniak.com", 1)[1]
        self.calls.append((method, path, kwargs))
        self.last_headers = headers or {}
        if self.failure is not None and method != "GET":
            return self.failure

        if method == "GET":
            page = kwargs["params"]["page"]
            data = self.records[(page - 1) * 2 : page * 2]
            pages = max(1, -(-len(self.records) // 2))
            return _Response(
                {"result": "success", "data": data, "page": page, "pages": pages}
            )
        if method == "POST":
            self.records.append(dict(kwargs["json"], id=len(self.records) + 1))
            return _Response({"result": "success", "data": self.records[-1]})
        record = next(
            r for r in self.records if r["id"] == int(path.rsplit("/", 1)[-1])
        )
        record.update(kwargs["json"])
        return _Response({"result": "success", "data": record})

    def writes(self):
        return [(m, p, kw["json"]) for m, p, kw in self.calls if m != "GET"]


def _zone_with(host="test.realmsgos.org", canister=CANISTER, ttl=60):
    """The three records of ``host`` as the zone would hold them after an apply."""
    source = host[: -len(ZONE) - 1]
    return [
        {"type": "CNAME", "source": source, "target": f"{host}.icp1.io.", "ttl": ttl},
        {
            "type": "TXT",
            "source": f"_canister-id.{source}",
            "target": canister,
            "ttl": ttl,
        },
        {
            "type": "CNAME",
            "source": f"_acme-challenge.{source}",
            "target": f"_acme-challenge.{host}.icp2.io.",
            "ttl": ttl,
        },
    ]


def _apply(session, host="test.realmsgos.org", canister=CANISTER, **kwargs):
    return apply_records(
        render_dns_records(host, canister),
        token="tok",
        zone=ZONE,
        session=session,
        **kwargs,
    )


class _Failing(_FakeSession):
    """Answers every call with an error of the given HTTP status."""

    def __init__(self, status):
        super().__init__()
        self.status = status

    def request(self, method, url, **kwargs):
        error = {"code": "not_authorized", "description": "Not authorized."}
        return _Response({"result": "error", "error": error}, status_code=self.status)


class TestCreate:
    def test_empty_zone_gets_the_three_records(self):
        session = _FakeSession()

        outcomes = _apply(session)

        assert [o.action for o in outcomes] == ["created"] * 3
        assert session.writes() == [
            (
                "POST",
                f"/2/zones/{ZONE}/records",
                {
                    "type": "CNAME",
                    "source": "test",
                    "target": "test.realmsgos.org.icp1.io.",
                    "ttl": 60,
                },
            ),
            (
                "POST",
                f"/2/zones/{ZONE}/records",
                {
                    "type": "TXT",
                    "source": "_canister-id.test",
                    "target": CANISTER,
                    "ttl": 60,
                },
            ),
            (
                "POST",
                f"/2/zones/{ZONE}/records",
                {
                    "type": "CNAME",
                    "source": "_acme-challenge.test",
                    "target": "_acme-challenge.test.realmsgos.org.icp2.io.",
                    "ttl": 60,
                },
            ),
        ]

    def test_token_is_sent_as_a_bearer(self):
        session = _FakeSession()
        _apply(session)
        assert session.last_headers["Authorization"] == "Bearer tok"

    def test_a_multi_label_host_is_relative_to_the_zone(self):
        session = _FakeSession()
        _apply(session, host="casals.test.realmsgos.org")
        sources = [body["source"] for _, _, body in session.writes()]
        assert sources == [
            "casals.test",
            "_canister-id.casals.test",
            "_acme-challenge.casals.test",
        ]


class TestUpdate:
    def test_a_stale_canister_id_is_updated_in_place(self):
        session = _FakeSession(_zone_with(canister="old-cai"))

        outcomes = _apply(session)

        assert [o.action for o in outcomes] == ["unchanged", "updated", "unchanged"]
        assert outcomes[1].detail == f"old-cai -> {CANISTER}"
        assert session.writes() == [
            ("PUT", f"/2/zones/{ZONE}/records/2", {"target": CANISTER, "ttl": 60}),
        ]

    def test_a_different_ttl_is_realigned(self):
        session = _FakeSession(_zone_with(ttl=3600))

        outcomes = _apply(session)

        assert [o.action for o in outcomes] == ["updated"] * 3
        assert outcomes[0].detail == "ttl 3600 -> 60"
        assert {m for m, _, _ in session.writes()} == {"PUT"}

    def test_a_record_on_a_later_page_is_found(self):
        session = _FakeSession(_zone_with())
        session.records.insert(
            0,
            {"id": 90, "type": "MX", "source": ".", "target": "mx.example", "ttl": 60},
        )

        outcomes = _apply(session)

        assert [o.action for o in outcomes] == ["unchanged"] * 3
        assert [p for m, p, _ in session.calls if m == "GET"] == [
            f"/2/zones/{ZONE}/records"
        ] * 2


class TestNoOp:
    def test_a_converged_zone_is_not_written(self):
        session = _FakeSession(_zone_with())

        outcomes = _apply(session)

        assert [o.action for o in outcomes] == ["unchanged"] * 3
        assert session.writes() == []

    def test_a_quoted_txt_and_unquoted_cname_targets_still_match(self):
        zone = _zone_with()
        zone[0]["target"] = "test.realmsgos.org.icp1.io"
        zone[1]["target"] = f'"{CANISTER}"'

        assert [o.action for o in _apply(_FakeSession(zone))] == ["unchanged"] * 3


class TestApex:
    def test_the_apex_gets_the_gateway_addresses_instead_of_a_cname(self):
        session = _FakeSession()

        outcomes = _apply(session, host=ZONE)

        assert [(o.record.record_type, o.record.value) for o in outcomes[:2]] == [
            ("A", APEX_ADDRESSES[0]),
            ("A", APEX_ADDRESSES[1]),
        ]
        bodies = [body for _, _, body in session.writes()]
        assert bodies[0] == {
            "type": "A",
            "source": ".",
            "target": "195.5.171.242",
            "ttl": 60,
        }
        assert bodies[1] == {
            "type": "A",
            "source": ".",
            "target": "212.71.124.188",
            "ttl": 60,
        }
        assert bodies[2]["source"] == "_canister-id"
        assert not any(b["type"] == "CNAME" and b["source"] == "." for b in bodies)

    def test_an_apex_with_one_address_only_gets_the_other(self):
        existing = [
            {"type": "A", "source": ".", "target": APEX_ADDRESSES[0], "ttl": 60}
        ]
        session = _FakeSession(existing)

        outcomes = _apply(session, host=ZONE)

        assert [o.action for o in outcomes[:2]] == ["unchanged", "created"]

    def test_a_foreign_apex_address_is_taken_over_not_deleted(self):
        existing = [{"type": "A", "source": ".", "target": "1.2.3.4", "ttl": 60}]
        session = _FakeSession(existing)

        outcomes = _apply(session, host=ZONE)

        assert [o.action for o in outcomes[:2]] == ["updated", "created"]
        assert outcomes[0].detail == f"1.2.3.4 -> {APEX_ADDRESSES[0]}"


class TestNeverDeletes:
    def test_a_second_value_is_left_alone_and_reported(self):
        zone = _zone_with()
        zone.append(
            {
                "type": "TXT",
                "source": "_canister-id.test",
                "target": "someone-elses-cai",
                "ttl": 60,
            }
        )
        session = _FakeSession(zone)

        outcomes = _apply(session)

        assert session.writes() == []
        assert "someone-elses-cai" in outcomes[1].warning
        assert outcomes[1].detail == ""
        assert "DELETE" not in {m for m, _, _ in session.calls}

    def test_an_aaaa_record_on_the_name_is_reported_not_touched(self):
        zone = _zone_with() + [
            {"type": "AAAA", "source": "test", "target": "2001:db8::1", "ttl": 60}
        ]
        session = _FakeSession(zone)

        outcomes = _apply(session)

        assert session.writes() == []
        assert "2001:db8::1" in outcomes[0].warning
        assert outcomes[1].warning == ""

    def test_unrelated_records_are_not_touched(self):
        zone = _zone_with() + [
            {"type": "TXT", "source": "test", "target": "v=spf1 -all", "ttl": 60}
        ]
        session = _FakeSession(zone)

        _apply(session)

        assert session.writes() == []


class TestDryRun:
    def test_it_reports_the_changes_and_writes_nothing(self):
        session = _FakeSession(_zone_with(canister="old-cai")[:2])

        outcomes = _apply(session, dry_run=True)

        assert [o.action for o in outcomes] == ["unchanged", "updated", "created"]
        assert session.writes() == []
        assert len(session.records) == 2


class TestErrors:
    def test_an_api_error_names_the_code_and_the_call(self):
        session = _FakeSession()
        session.failure = _Response(
            {
                "result": "error",
                "error": {"code": "dns_record_already_exists", "description": "exists"},
            },
            status_code=400,
        )

        with pytest.raises(
            InfomaniakError,
            match=r"POST .*records failed \(HTTP 400\): dns_record_already_exists: exists",
        ):
            _apply(session)

    def test_a_rejected_token_is_named_as_such(self):
        with pytest.raises(
            InfomaniakError, match="token rejected: check \\$INFOMANIAK_API_TOKEN"
        ):
            _apply(_Failing(401))

    def test_a_token_without_scopes_says_which_it_needs(self):
        with pytest.raises(InfomaniakError, match="dns:read and dns:write"):
            _apply(_Failing(403))

    @pytest.mark.parametrize("status", [400, 401, 403, 502])
    def test_the_token_never_appears_in_an_error(self, status):
        with pytest.raises(InfomaniakError) as raised:
            apply_records(
                render_dns_records("test.realmsgos.org", CANISTER),
                token="s3cret-token",
                zone=ZONE,
                session=_Failing(status),
            )
        assert "s3cret-token" not in str(raised.value)

    def test_a_non_json_body_is_an_error(self):
        class Html(_FakeSession):
            def request(self, method, url, **kwargs):
                return _Response(None, status_code=502)

        with pytest.raises(InfomaniakError, match="non-JSON body"):
            _apply(Html())

    def test_a_host_outside_the_zone_is_refused(self):
        with pytest.raises(InfomaniakError, match="not inside the zone"):
            _apply(_FakeSession(), host="test.gos.earth")

    def test_empty_token_refused_up_front(self):
        with pytest.raises(InfomaniakError, match="empty Infomaniak API token"):
            InfomaniakDns("")

    def test_a_ttl_below_the_api_floor_is_refused(self):
        with pytest.raises(InfomaniakError, match="at least 60"):
            InfomaniakDns("tok", ttl=30)


class TestSheetSettings:
    def test_the_token_variable_defaults_to_the_infomaniak_one(self):
        provider, zone, token_env, _ttl = _dns_settings(
            {"dns": {"provider": "Infomaniak", "zone": ZONE}}
        )
        assert (provider, zone, token_env) == (
            "infomaniak",
            ZONE,
            "INFOMANIAK_API_TOKEN",
        )

    def test_the_zone_is_required(self):
        with pytest.raises(ValueError, match="dns.zone is required"):
            _dns_settings({"dns": {"provider": "infomaniak"}})

    def test_a_missing_token_stops_the_run_before_the_deploy(self, monkeypatch):
        monkeypatch.delenv("INFOMANIAK_API_TOKEN", raising=False)
        with pytest.raises(RuntimeError, match="INFOMANIAK_API_TOKEN is not set"):
            _check_dns_credentials(
                {"dns": {"provider": "infomaniak", "zone": ZONE}}, "test.realmsgos.org"
            )

    def test_a_cloudflare_token_is_not_needed(self, monkeypatch):
        monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
        monkeypatch.setenv("INFOMANIAK_API_TOKEN", "tok")
        _check_dns_credentials(
            {"dns": {"provider": "infomaniak", "zone": ZONE}}, "test.realmsgos.org"
        )


class TestApplyTarget:
    def test_a_dry_run_plans_the_records_and_stops_before_registration(
        self, monkeypatch
    ):
        monkeypatch.setenv("INFOMANIAK_API_TOKEN", "tok")
        session = _FakeSession()
        target = DomainTarget("test.realmsgos.org", "marketplace-frontend", CANISTER)
        env_cfg = {"dns": {"provider": "infomaniak", "zone": ZONE}}

        healthy = apply_target(
            target,
            env_cfg,
            skip_registration=False,
            registration_timeout=1,
            dry_run=True,
            session=session,
        )

        assert healthy
        assert session.writes() == []
        assert {m for m, _, _ in session.calls} == {"GET"}
