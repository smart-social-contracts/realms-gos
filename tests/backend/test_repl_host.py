"""Host-dispatch REPL: api/ext RPC hits the same Candid surface as the UI.

See realms#313.
"""

import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

_MOCKED_PREFIXES = (
    "ic_basilisk_toolkit",
    "ic_python_db",
    "ic_python_logging",
)
for _name in list(sys.modules):
    if any(
        _name == prefix or _name.startswith(prefix + ".")
        for prefix in _MOCKED_PREFIXES
    ):
        if type(sys.modules[_name]).__name__ == "MagicMock":
            del sys.modules[_name]
for _name in ("core.cedar_authz", "core.cedar_entities", "core.repl_host", "main"):
    sys.modules.pop(_name, None)

BACKEND = os.path.join(os.path.dirname(__file__), "..", "..", "src", "realm_backend")
TOOLKIT = os.path.join(os.path.dirname(__file__), "..", "..", "..", "ic-basilisk-toolkit")
sys.path.insert(0, BACKEND)
if os.path.isdir(TOOLKIT) and TOOLKIT not in sys.path:
    sys.path.insert(0, TOOLKIT)

from ic_basilisk_toolkit.secure_orm import RpcError  # noqa: E402

import core.repl_host as _repl_host  # noqa: E402
from core.repl_host import (  # noqa: E402
    BLOCKED_METHODS,
    HOST_ACTIONS,
    HOST_STUB_APPENDIX,
    HostSecureORM,
    json_args,
    load_allowed_methods,
    parse_candid_methods,
    resolve_host_secure_orm,
    _default_did_path,
    _module_attr,
    _resolve_host_module,
)


def _patch_candid_globals(get_sandbox_config, extension_sync_call):
    """Leftover Candid ``get_global(name)`` lives in leftover-executed globals."""
    saved = (
        getattr(_repl_host, "get_sandbox_config", None),
        getattr(_repl_host, "extension_sync_call", None),
    )
    _repl_host.get_sandbox_config = get_sandbox_config
    _repl_host.extension_sync_call = extension_sync_call
    return saved


def _restore_candid_globals(saved):
    gsc, esc = saved
    if gsc is None:
        try:
            del _repl_host.get_sandbox_config
        except AttributeError:
            pass
    else:
        _repl_host.get_sandbox_config = gsc
    if esc is None:
        try:
            del _repl_host.extension_sync_call
        except AttributeError:
            pass
    else:
        _repl_host.extension_sync_call = esc


def _leftover_inspect_stub():
    """Live leftover WASI inspect is a stub: leftover leftover-safe attrs only.

    Leftover dispatch that leftover-calls leftover ``inspect.X`` fails this
    leftover reconstruction. No leftover ``signature``, leftover
    ``isgenerator``, leftover ``getfullargspec``, leftover ``isfunction``.
    """

    class _LeftoverInspect:
        def __getattr__(self, name):
            raise AttributeError(f"module 'inspect' has no attribute {name!r}")

    stub = _LeftoverInspect()
    for name in ("signature", "isgenerator", "getfullargspec", "isfunction", "Parameter"):
        assert not hasattr(stub, name)
        with pytest.raises(AttributeError, match=name):
            getattr(stub, name)
    return stub


DID_PATH = Path(BACKEND) / "realm_backend.did"


class AccessDenied(Exception):
    pass


class DummyHost:
    def ping(self):
        return "pong"

    def echo(self, x, y=1):
        return {"x": x, "y": y}

    def boom(self):
        raise AccessDenied("nope")

    def extension_sync_call(self, extension_name, function_name, args):
        return {
            "ok": True,
            "extension_name": extension_name,
            "function_name": function_name,
            "args": args,
        }

    def extension_async_call(self, extension_name, function_name, args):
        return {"async": True, "function_name": function_name}

    def __shell__(self, code):
        return "should never run"

    def status(self):
        return {"ok": True}


def _orm(**overrides):
    allowed = overrides.pop(
        "allowed_methods",
        [
            "ping",
            "echo",
            "boom",
            "status",
            "extension_sync_call",
            "extension_async_call",
            "__shell__",
        ],
    )
    host = overrides.pop("host_module", DummyHost())
    return HostSecureORM(
        engine=SimpleNamespace(status=lambda: {}),
        namespace="Realm",
        entities=[],
        schema={},
        host_module=host,
        allowed_methods=allowed,
        **overrides,
    )


class TestDidAllowlist:
    def test_parses_quoted_service_methods(self):
        methods = parse_candid_methods(DID_PATH.read_text())
        assert "extension_sync_call" in methods
        assert "extension_async_call" in methods
        assert "status" in methods
        assert "__shell__" in methods
        assert "http_request" in methods
        assert "__get_candid_interface_tmp_hack" in methods

    def test_parses_inline_one_line_service_block(self):
        did = 'service : { "status" : () -> (text) query; "__shell__" : (text) -> (text); }'
        names = parse_candid_methods(did)
        assert "status" in names
        assert "__shell__" in names

    def test_blocked_names_are_the_recursion_and_http_surface(self):
        assert BLOCKED_METHODS == {
            "__shell__",
            "http_request",
            "http_request_update",
            "http_transform",
            "__get_candid_interface_tmp_hack",
        }

    def test_missing_did_uses_injected_candid_hack(self, tmp_path):
        did_text = (
            "service : {\n"
            '  "status" : () -> (text) query;\n'
            '  "__shell__" : (text) -> (text);\n'
            "}\n"
        )
        host = SimpleNamespace(__get_candid_interface_tmp_hack=lambda: did_text)
        names = load_allowed_methods(tmp_path / "missing.did", host_module=host)
        assert "status" in names
        assert "__shell__" not in names

    def test_missing_did_and_hack_raises(self, tmp_path):
        with pytest.raises(RpcError, match="Candid interface not found"):
            load_allowed_methods(tmp_path / "missing.did", host_module=SimpleNamespace())

    def test_did_path_without_file(self, monkeypatch):
        import core.repl_host as rh

        monkeypatch.delitem(rh.__dict__, "__file__", raising=False)
        path = _default_did_path()
        assert path.name == "realm_backend.did"


class TestHostDispatch:
    def test_call_host_without_leftover_inspect(self, monkeypatch):
        """Live leftover WASI inspect is a stub. Leftover dispatch must not leftover-call leftover inspect."""
        import inspect as real

        for fn in (HostSecureORM._call_host, _repl_host.drive_result):
            src = real.getsource(fn)
            assert "inspect.isgenerator" not in src
            assert "inspect.signature" not in src
            assert "inspect.getfullargspec" not in src
        monkeypatch.setattr(_repl_host, "inspect", _leftover_inspect_stub())
        saved = _patch_candid_globals(
            lambda: {"available": True, "default_mode": "sandbox"},
            lambda extension_name, function_name, args: {
                "success": True,
                "extension_name": extension_name,
                "function_name": function_name,
                "args": args,
            },
        )
        try:
            orm = _orm(
                allowed_methods=[
                    "get_sandbox_config",
                    "extension_sync_call",
                    "__shell__",
                ]
            )
            assert orm.handle_rpc(
                "alice", "host.call", {"method": "get_sandbox_config"}
            ) == {"available": True, "default_mode": "sandbox"}
            assert orm.handle_rpc(
                "alice",
                "host.ext_sync",
                {
                    "extension_name": "department_docs",
                    "function_name": "list_documents",
                    "args": {},
                },
            ) == {
                "success": True,
                "extension_name": "department_docs",
                "function_name": "list_documents",
                "args": "{}",
            }
            with pytest.raises(PermissionError, match="__shell__"):
                orm.handle_rpc(
                    "alice", "host.call", {"method": "__shell__", "args": ["1+1"]}
                )
        finally:
            _restore_candid_globals(saved)

    def test_call_positional_and_keyword(self):
        orm = _orm()
        assert orm.handle_rpc("alice", "host.call", {"method": "ping"}) == "pong"
        assert orm.handle_rpc(
            "alice", "host.call", {"method": "echo", "args": ["hi"]}
        ) == {"x": "hi", "y": 1}
        assert orm.handle_rpc(
            "alice",
            "host.call",
            {"method": "echo", "args": ["hi"], "kwargs": {"y": 9}},
        ) == {"x": "hi", "y": 9}

    def test_list_methods_hides_blocked(self):
        orm = _orm()
        names = orm.handle_rpc("alice", "host.list_methods", {})
        assert "ping" in names
        assert "__shell__" not in names

    def test_blocked_shell_is_permission_error(self):
        orm = _orm()
        with pytest.raises(PermissionError, match="__shell__"):
            orm.handle_rpc(
                "alice", "host.call", {"method": "__shell__", "args": ["1+1"]}
            )

    def test_unknown_method_is_permission_error(self):
        orm = _orm()
        with pytest.raises(PermissionError, match="allowlist"):
            orm.handle_rpc("alice", "host.call", {"method": "not_a_method"})

    def test_access_denied_becomes_permission_error(self):
        orm = _orm()
        with pytest.raises(PermissionError, match="nope"):
            orm.handle_rpc("alice", "host.call", {"method": "boom"})

    def test_ext_sync_json_encodes_dict_like_the_spa(self):
        orm = _orm()
        result = orm.handle_rpc(
            "alice",
            "host.ext_sync",
            {
                "extension_name": "voting",
                "function_name": "cast_vote",
                "args": {"proposal_id": "p1"},
            },
        )
        assert result["extension_name"] == "voting"
        assert result["function_name"] == "cast_vote"
        assert result["args"] == '{"proposal_id": "p1"}'

    def test_ext_sync_passes_through_json_string(self):
        orm = _orm()
        result = orm.handle_rpc(
            "alice",
            "host.ext_sync",
            {
                "extension_name": "voting",
                "function_name": "cast_vote",
                "args": '{"proposal_id": "p1"}',
            },
        )
        assert result["args"] == '{"proposal_id": "p1"}'

    def test_same_args_host_call_and_direct_function(self):
        host = DummyHost()
        orm = _orm(host_module=host)
        via_rpc = orm.handle_rpc(
            "alice",
            "host.call",
            {
                "method": "extension_sync_call",
                "args": ["voting", "cast_vote", json_args({"proposal_id": "p1"})],
            },
        )
        direct = host.extension_sync_call(
            "voting", "cast_vote", json_args({"proposal_id": "p1"})
        )
        assert via_rpc == direct

    def test_unknown_host_action(self):
        orm = _orm()
        with pytest.raises(RpcError, match="unknown action"):
            orm.handle_rpc("alice", "host.explode", {})


class TestSandboxSurface:
    def test_actions_stay_under_cap(self):
        orm = _orm()
        actions = orm.actions()
        assert set(HOST_ACTIONS) <= set(actions)
        assert set(
            ["orm.create", "orm.list", "orm.get", "orm.update", "orm.delete", "orm.count"]
        ) <= set(actions)
        assert len(actions) <= 32

    def test_stub_defines_api_ext_and_wraps_eval_repl(self):
        orm = _orm()
        src = orm.stub_source()
        assert "class api:" in src
        assert "class ext:" in src
        assert "host.call" in src
        assert HOST_STUB_APPENDIX.strip() in src
        assert "_eval_repl_inner = eval_repl" in src
        assert 'b["api"] = api' in src
        assert 'b["ext"] = ext' in src

    def test_host_appendix_execs_with_rpc_injected(self):
        import builtins as _builtins

        calls = []

        def rpc(*a, **k):
            calls.append((a, k))
            return ["status"]

        b = dict(vars(_builtins))
        b["rpc"] = rpc
        ns = {"eval_repl": lambda code: "", "__builtins__": b}
        exec(HOST_STUB_APPENDIX, ns)
        assert ns["api"].methods() == ["status"]
        assert calls[0][0] == ("host.list_methods",)
        assert ns["ext"].call("voting", "cast_vote", {"proposal_id": "p1"}) == ["status"]

    def test_api_works_when_eval_repl_does_not_wrap(self):
        """Basilisk may bind the first eval_repl; api still lives on builtins."""
        import builtins as _builtins

        def rpc(*a, **k):
            return ["status", "extension_sync_call"]

        b = dict(vars(_builtins))
        b["rpc"] = rpc
        inner_ns = {"rpc": None, "__builtins__": b}

        def inner_eval_repl(code):
            return eval(code, inner_ns, inner_ns)

        g = {"eval_repl": inner_eval_repl, "__builtins__": b}
        exec(HOST_STUB_APPENDIX, g)
        assert eval("api.methods()", inner_ns, inner_ns) == [
            "status",
            "extension_sync_call",
        ]
        assert eval("ext.call('system_info', 'get_public_info', {})", inner_ns, inner_ns)

    def test_json_args_matches_spa(self):
        assert json_args(None) == "{}"
        assert json_args({"a": 1}) == '{"a": 1}'
        assert json_args('{"a": 1}') == '{"a": 1}'


# Basilisk ``_LazyMod`` (see tests/backend/test_federal_vote_runtime.py).
# ``__getattr__`` always ``_bload``s; ``_bload`` re-execs source. The canister
# entry is often exec'd as ``__main__`` *without* setting ``_bloaded``, so a
# later ``getattr`` for a missing name re-runs ``Database.init``.
_LAZY_MAIN_SRC = """
class DatabaseAlreadyExists(RuntimeError):
    pass

raise DatabaseAlreadyExists("Database instance already exists")
"""


def _make_lazy_mod_class():
    _bMT = type(sys)

    class _LazyMod(_bMT):
        def __init__(self, name, source, already=None):
            super().__init__(name)
            self.__dict__["_bsrc"] = source
            self.__dict__["_bloaded"] = False
            self.__dict__["_bloading"] = False
            self.__dict__["_bload_count"] = 0
            if already:
                self.__dict__.update(already)

        def _bload(self):
            self.__dict__["_bload_count"] = self.__dict__.get("_bload_count", 0) + 1
            if self._bloading or self._bloaded:
                return
            self.__dict__["_bloading"] = True
            try:
                if self._bsrc:
                    exec(
                        compile(
                            self._bsrc,
                            self.__name__.replace(".", "/") + ".py",
                            "exec",
                        ),
                        self.__dict__,
                    )
                self.__dict__["_bloaded"] = True
            finally:
                self.__dict__["_bloading"] = False

        def __getattr__(self, name):
            self._bload()
            try:
                return self.__dict__[name]
            except KeyError:
                raise AttributeError(
                    f"module '{self.__name__}' has no attribute '{name}'"
                )

    return _LazyMod


class TestLazyModReimport:
    """api.call / ext.call must not re-import ``__main__`` / re-init Database."""

    def test_module_attr_does_not_bload_executed_entry(self):
        Lazy = _make_lazy_mod_class()

        def get_sandbox_config():
            return {"available": True, "default_mode": "sandbox"}

        executed = Lazy(
            "__main__",
            _LAZY_MAIN_SRC,
            already={"get_sandbox_config": get_sandbox_config},
        )
        assert _module_attr(executed, "get_sandbox_config") is get_sandbox_config
        assert _module_attr(executed, "__get_candid_interface_tmp_hack") is None
        assert executed._bload_count == 0
        # The naive getattr the host used to use re-execs and crashes.
        with pytest.raises(RuntimeError, match="Database instance already exists"):
            getattr(executed, "__get_candid_interface_tmp_hack", None)
        assert executed._bload_count >= 1

    def test_host_call_uses_bound_fn_without_reimport(self):
        Lazy = _make_lazy_mod_class()

        def get_sandbox_config():
            return {"available": True, "default_mode": "sandbox"}

        def extension_sync_call(extension_name, function_name, args):
            return {
                "success": True,
                "extension_name": extension_name,
                "function_name": function_name,
                "args": args,
            }

        executed = Lazy(
            "__main__",
            _LAZY_MAIN_SRC,
            already={
                "get_sandbox_config": get_sandbox_config,
                "extension_sync_call": extension_sync_call,
            },
        )
        orm = _orm(
            host_module=executed,
            allowed_methods=["get_sandbox_config", "extension_sync_call"],
        )
        assert orm.handle_rpc(
            "alice", "host.call", {"method": "get_sandbox_config"}
        ) == {"available": True, "default_mode": "sandbox"}
        assert orm.handle_rpc(
            "alice",
            "host.ext_sync",
            {
                "extension_name": "department_docs",
                "function_name": "list_documents",
                "args": {},
            },
        ) == {
            "success": True,
            "extension_name": "department_docs",
            "function_name": "list_documents",
            "args": "{}",
        }
        assert executed._bload_count == 0

    def test_missing_did_hack_lookup_does_not_reimport(self, tmp_path):
        Lazy = _make_lazy_mod_class()
        did_text = (
            "service : {\n"
            '  "get_sandbox_config" : () -> (text) query;\n'
            '  "__shell__" : (text) -> (text);\n'
            "}\n"
        )
        executed = Lazy(
            "__main__",
            _LAZY_MAIN_SRC,
            already={"__get_candid_interface_tmp_hack": lambda: did_text},
        )
        names = load_allowed_methods(tmp_path / "missing.did", host_module=executed)
        assert "get_sandbox_config" in names
        assert "__shell__" not in names
        assert executed._bload_count == 0

    def test_missing_did_and_hack_uses_instance_verbs(self, tmp_path):
        """No DID / no Candid hack: leftover-executed instance verbs *are* the allowlist."""
        Lazy = _make_lazy_mod_class()
        executed = Lazy("__main__", _LAZY_MAIN_SRC, already={"ping": lambda: "pong"})
        names = load_allowed_methods(tmp_path / "missing.did", host_module=executed)
        assert "ping" in names
        assert executed._bload_count == 0

    def test_resolve_prefers_executed_main_and_never_imports(self, monkeypatch):
        Lazy = _make_lazy_mod_class()

        def get_sandbox_config():
            return {"available": True}

        executed = Lazy(
            "__main__",
            _LAZY_MAIN_SRC,
            already={"get_sandbox_config": get_sandbox_config},
        )
        unloaded = Lazy("main", _LAZY_MAIN_SRC)
        monkeypatch.setitem(sys.modules, "__main__", executed)
        monkeypatch.setitem(sys.modules, "main", unloaded)

        resolved = _resolve_host_module()
        assert resolved is executed
        assert executed._bload_count == 0
        assert unloaded._bload_count == 0

        orm = _orm(
            host_module=None,
            allowed_methods=["get_sandbox_config"],
        )
        # Stored host is None; dispatch must pick executed __main__, not
        # ``import main`` / getattr on the unloaded LazyMod.
        orm._host_module = None
        assert orm.handle_rpc(
            "alice", "host.call", {"method": "get_sandbox_config"}
        ) == {"available": True}
        assert executed._bload_count == 0
        assert unloaded._bload_count == 0

    def test_blocked_shell_still_denied_on_lazy_host(self):
        Lazy = _make_lazy_mod_class()

        def __shell__(code):
            return "should never run"

        executed = Lazy("__main__", _LAZY_MAIN_SRC, already={"__shell__": __shell__})
        orm = _orm(host_module=executed, allowed_methods=["__shell__"])
        with pytest.raises(PermissionError, match="__shell__"):
            orm.handle_rpc(
                "alice", "host.call", {"method": "__shell__", "args": ["1+1"]}
            )
        assert executed._bload_count == 0

    def test_leftover_layout_allowlist_lives_on_lazymod(self, tmp_path):
        """Optional path: if leftover *does* store the Candid hack on ``_LazyMod``.

        Live ``0aea1ede`` did not. Keep this so a leftover image that still
        injects the hack on the class stays leftover-safe. The honest live
        reconstruction is ``test_allowlist_from_leftover_executed_instance_verbs``.
        """
        _bMT = type(sys)
        did_text = (
            "service : {\n"
            '  "get_sandbox_config" : () -> (text) query;\n'
            '  "extension_sync_call" : (text, text, text) -> (text);\n'
            '  "__shell__" : (text) -> (text);\n'
            "}\n"
        )

        class _LazyMod(_bMT):
            def __init__(self, name, source):
                super().__init__(name)
                self.__dict__["_bsrc"] = source
                self.__dict__["_bloaded"] = False
                self.__dict__["_bloading"] = False
                self.__dict__["_bload_count"] = 0

            def _bload(self):
                self.__dict__["_bload_count"] = self.__dict__.get("_bload_count", 0) + 1
                if self._bloading or self._bloaded:
                    return
                self.__dict__["_bloading"] = True
                try:
                    if self._bsrc:
                        exec(
                            compile(
                                self._bsrc,
                                self.__name__.replace(".", "/") + ".py",
                                "exec",
                            ),
                            self.__dict__,
                        )
                    self.__dict__["_bloaded"] = True
                finally:
                    self.__dict__["_bloading"] = False

            def __getattr__(self, name):
                self._bload()
                try:
                    return self.__dict__[name]
                except KeyError:
                    raise AttributeError(
                        f"module '{self.__name__}' has no attribute '{name}'"
                    )

            def __get_candid_interface_tmp_hack(self):
                return did_text

            def get_sandbox_config(self):
                return {"available": True, "default_mode": "sandbox"}

            def extension_sync_call(self, extension_name, function_name, args):
                return {
                    "success": True,
                    "extension_name": extension_name,
                    "function_name": function_name,
                    "args": args,
                }

            def __shell__(self, code):
                return "should never run"

        executed = _LazyMod("__main__", _LAZY_MAIN_SRC)
        assert "__get_candid_interface_tmp_hack" not in executed.__dict__
        assert "get_sandbox_config" not in executed.__dict__
        assert "extension_sync_call" not in executed.__dict__

        missing_did = tmp_path / "missing.did"
        names = load_allowed_methods(missing_did, host_module=executed)
        assert "get_sandbox_config" in names
        assert "extension_sync_call" in names
        assert "__shell__" not in names
        assert executed._bload_count == 0

        orm = _orm(
            host_module=executed,
            allowed_methods=None,
            did_path=missing_did,
        )
        saved = _patch_candid_globals(
            lambda: {"available": True, "default_mode": "sandbox"},
            lambda extension_name, function_name, args: {
                "success": True,
                "extension_name": extension_name,
                "function_name": function_name,
                "args": args,
            },
        )
        try:
            assert orm.handle_rpc(
                "alice", "host.call", {"method": "get_sandbox_config"}
            ) == {"available": True, "default_mode": "sandbox"}
            assert orm.handle_rpc(
                "alice",
                "host.ext_sync",
                {
                    "extension_name": "department_docs",
                    "function_name": "list_documents",
                    "args": {},
                },
            ) == {
                "success": True,
                "extension_name": "department_docs",
                "function_name": "list_documents",
                "args": "{}",
            }
            with pytest.raises(PermissionError, match="__shell__"):
                orm.handle_rpc(
                    "alice", "host.call", {"method": "__shell__", "args": ["1+1"]}
                )
        finally:
            _restore_candid_globals(saved)
        assert executed._bload_count == 0
        # A name that is not on the class still _bload-crashes.
        with pytest.raises(RuntimeError, match="Database instance already exists"):
            getattr(executed, "not_on_lazymod")
        assert executed._bload_count >= 1

    def test_allowlist_when_instance_dict_is_not_a_dict(self, tmp_path, monkeypatch):
        """Live leftover after ``#342``: instance ns is mappingproxy, not dict.

        ``#342`` required ``isinstance(ns, dict)``. Leftover ``__dict__`` is
        mapping-like, so the instance-dict fallback stayed empty and leftover
        still raised ``Candid interface not found; host RPC disabled``.
        Verbs are leftover ``@query`` wrappers (not ``callable``) plus leftover
        ``_bsrc`` ``def`` names. No planted hack. No repo DID.
        """
        class Query:
            """Live leftover Query: not callable and no leftover slots."""

        class _ProxyHost:
            _bsrc = (
                "def get_sandbox_config():\n    return {}\n"
                "def extension_sync_call(extension_name, function_name, args):\n"
                "    return {}\n"
                "def __shell__(code):\n    return 'no'\n"
            )
            _bloaded = True
            get_sandbox_config = Query()
            extension_sync_call = Query()
            __shell__ = Query()

        executed = _ProxyHost
        ns = object.__getattribute__(executed, "__dict__")
        assert not isinstance(ns, dict)
        assert "get_sandbox_config" in ns
        leftover_query = ns["get_sandbox_config"]
        assert not callable(leftover_query)
        for slot in ("func", "fn", "_fn", "handler"):
            with pytest.raises(AttributeError):
                object.__getattribute__(leftover_query, slot)
        assert "__get_candid_interface_tmp_hack" not in ns
        missing_did = tmp_path / "missing.did"
        names = load_allowed_methods(missing_did, host_module=executed)
        assert "get_sandbox_config" in names
        assert "extension_sync_call" in names
        assert "__shell__" not in names

        orm = _orm(
            host_module=executed,
            allowed_methods=None,
            did_path=missing_did,
        )
        import builtins as _builtins

        def rpc(action, **kwargs):
            return orm.handle_rpc("2eqns", action, kwargs)

        b = dict(vars(_builtins))
        b["rpc"] = rpc
        ns = {"eval_repl": lambda _c: "", "__builtins__": b, "rpc": rpc}
        exec(HOST_STUB_APPENDIX, ns)
        monkeypatch.setattr(_repl_host, "inspect", _leftover_inspect_stub())
        saved = _patch_candid_globals(
            lambda: {"available": True, "default_mode": "sandbox"},
            lambda extension_name, function_name, args: {
                "success": True,
                "extension_name": extension_name,
                "function_name": function_name,
                "args": args,
            },
        )
        try:
            assert ns["api"].call("get_sandbox_config") == {
                "available": True,
                "default_mode": "sandbox",
            }
            assert ns["ext"].call("department_docs", "list_documents", {}) == {
                "success": True,
                "extension_name": "department_docs",
                "function_name": "list_documents",
                "args": "{}",
            }
            with pytest.raises(PermissionError, match="__shell__"):
                ns["api"].call("__shell__", "1")
        finally:
            _restore_candid_globals(saved)

    def test_allowlist_from_type_dict_when_instance_has_no_verbs(self, tmp_path, monkeypatch):
        """Live leftover: instance dict may omit ``get_sandbox_config``."""
        _bMT = type(sys)

        class Query:
            """Live leftover Query: not callable and no leftover slots."""

        class _LazyMod(_bMT):
            def __init__(self, name):
                super().__init__(name)
                self.__dict__["_bsrc"] = (
                    "def get_sandbox_config():\n    pass\n"
                    "def extension_sync_call(a, b, c):\n    pass\n"
                )
                self.__dict__["_bloaded"] = True
                self.__dict__["_bloading"] = False
                self.__dict__["_bload_count"] = 0

            def _bload(self):
                self.__dict__["_bload_count"] = self.__dict__.get("_bload_count", 0) + 1
                raise RuntimeError("Database instance already exists")

            def __getattr__(self, name):
                self._bload()

            get_sandbox_config = Query()
            extension_sync_call = Query()
            __shell__ = Query()

        executed = _LazyMod("__main__")
        assert "get_sandbox_config" not in executed.__dict__
        leftover_query = type(executed).__dict__["get_sandbox_config"]
        assert not callable(leftover_query)
        for slot in ("func", "fn", "_fn", "handler"):
            with pytest.raises(AttributeError):
                object.__getattribute__(leftover_query, slot)
        assert "__get_candid_interface_tmp_hack" not in executed.__dict__
        assert not any(
            str(key).endswith("__get_candid_interface_tmp_hack")
            for key in type(executed).__dict__
        )
        missing_did = tmp_path / "missing.did"
        names = load_allowed_methods(missing_did, host_module=executed)
        assert "get_sandbox_config" in names
        assert "extension_sync_call" in names
        assert "__shell__" not in names
        assert executed._bload_count == 0

        orm = _orm(
            host_module=executed,
            allowed_methods=None,
            did_path=missing_did,
        )
        import builtins as _builtins

        def rpc(action, **kwargs):
            return orm.handle_rpc("2eqns", action, kwargs)

        b = dict(vars(_builtins))
        b["rpc"] = rpc
        ns = {"eval_repl": lambda _c: "", "__builtins__": b, "rpc": rpc}
        exec(HOST_STUB_APPENDIX, ns)
        monkeypatch.setattr(_repl_host, "inspect", _leftover_inspect_stub())
        saved = _patch_candid_globals(
            lambda: {"available": True, "default_mode": "sandbox"},
            lambda extension_name, function_name, args: {
                "success": True,
                "extension_name": extension_name,
                "function_name": function_name,
                "args": args,
            },
        )
        try:
            assert ns["api"].call("get_sandbox_config") == {
                "available": True,
                "default_mode": "sandbox",
            }
            assert ns["ext"].call("department_docs", "list_documents", {}) == {
                "success": True,
                "extension_name": "department_docs",
                "function_name": "list_documents",
                "args": "{}",
            }
            with pytest.raises(PermissionError, match="__shell__"):
                ns["api"].call("__shell__", "1")
        finally:
            _restore_candid_globals(saved)
        assert executed._bload_count == 0


class TestWasiCollectionsAbc:
    def test_import_without_collections_abc_mapping(self, monkeypatch, tmp_path):
        """Basilisk WASI stub has no ``collections.abc.Mapping``.

        ``#336`` imported ``Mapping`` at module load, so leftover-free
        ``__shell__`` died before any ``api.call``. Import must succeed and
        leftover instance-dict allowlist reads must still work.
        """
        import collections.abc as abc_mod
        import types as _types

        stub = _types.ModuleType("collections.abc")
        stub.__dict__.update(
            {name: value for name, value in vars(abc_mod).items() if name != "Mapping"}
        )
        monkeypatch.setitem(sys.modules, "collections.abc", stub)

        def _import_mapping():
            from collections.abc import Mapping  # noqa: F401

            return Mapping

        with pytest.raises(ImportError, match="Mapping"):
            _import_mapping()

        sys.modules.pop("core.repl_host", None)
        imported = __import__("core.repl_host", fromlist=["load_allowed_methods"])

        assert imported.BLOCKED_METHODS == BLOCKED_METHODS
        assert callable(imported.load_allowed_methods)

        _bMT = type(sys)

        class _LazyMod(_bMT):
            def __init__(self, name):
                super().__init__(name)
                self.__dict__["_bsrc"] = _LAZY_MAIN_SRC
                self.__dict__["_bloaded"] = True
                self.__dict__["_bloading"] = False
                self.__dict__["_bload_count"] = 0

            def _bload(self):
                self.__dict__["_bload_count"] = self.__dict__.get("_bload_count", 0) + 1
                raise RuntimeError("Database instance already exists")

            def __getattr__(self, name):
                self._bload()

        executed = _LazyMod("__main__")
        executed.__dict__["get_sandbox_config"] = lambda: {"available": True}
        executed.__dict__["__shell__"] = lambda code: "no"
        names = imported.load_allowed_methods(
            tmp_path / "missing.did", host_module=executed
        )
        assert "get_sandbox_config" in names
        assert "__shell__" not in names
        assert executed._bload_count == 0


class TestWasiTypes:
    def test_import_without_types_moduletype(self, monkeypatch, tmp_path):
        """Basilisk WASI stub has no ``types.ModuleType``.

        ``#339`` imported ``types.ModuleType`` at leftover ``__main__``
        bind, so ``post_upgrade`` trapped before the new image went live.
        Import must succeed and leftover allowlist / HostSecureORM bind
        must still work.
        """
        import types as types_mod

        stub = type(sys)("types")
        stub.__dict__.update(
            {
                name: value
                for name, value in vars(types_mod).items()
                if name != "ModuleType"
            }
        )
        monkeypatch.setitem(sys.modules, "types", stub)

        def _import_moduletype():
            from types import ModuleType  # noqa: F401

            return ModuleType

        with pytest.raises(ImportError, match="ModuleType"):
            _import_moduletype()

        main_src = Path(BACKEND, "main.py").read_text()
        assert "types.ModuleType" not in main_src
        assert "_host_types.ModuleType" not in main_src
        assert "import types as _host_types" not in main_src

        sys.modules.pop("core.repl_host", None)
        imported = __import__(
            "core.repl_host", fromlist=["load_allowed_methods", "HostSecureORM"]
        )

        assert imported.BLOCKED_METHODS == BLOCKED_METHODS
        assert callable(imported.load_allowed_methods)
        assert imported._SKIP_TYPE_MRO == frozenset({object, type, type(sys)})

        _bMT = type(sys)

        class _LazyMod(_bMT):
            def __init__(self, name):
                super().__init__(name)
                self.__dict__["_bsrc"] = _LAZY_MAIN_SRC
                self.__dict__["_bloaded"] = True
                self.__dict__["_bloading"] = False
                self.__dict__["_bload_count"] = 0

            def _bload(self):
                self.__dict__["_bload_count"] = self.__dict__.get("_bload_count", 0) + 1
                raise RuntimeError("Database instance already exists")

            def __getattr__(self, name):
                self._bload()

        executed = _LazyMod("__main__")
        executed.__dict__["get_sandbox_config"] = lambda: {"available": True}
        executed.__dict__["__shell__"] = lambda code: "no"
        names = imported.load_allowed_methods(
            tmp_path / "missing.did", host_module=executed
        )
        assert "get_sandbox_config" in names
        assert "__shell__" not in names
        assert executed._bload_count == 0

        host = _bMT("__main__")
        host.HostSecureORM = imported.HostSecureORM
        saved_main = sys.modules.get("main")
        saved_dunder = sys.modules.get("__main__")
        leftover = _LazyMod("core.repl_host")
        leftover.__dict__["_bloaded"] = True
        saved_rh = sys.modules.get("core.repl_host")
        try:
            sys.modules["core.repl_host"] = leftover
            sys.modules["__main__"] = host
            sys.modules["main"] = host
            assert imported.resolve_host_secure_orm() is imported.HostSecureORM
            assert leftover._bload_count == 0
        finally:
            if saved_rh is None:
                sys.modules.pop("core.repl_host", None)
            else:
                sys.modules["core.repl_host"] = saved_rh
            if saved_main is None:
                sys.modules.pop("main", None)
            else:
                sys.modules["main"] = saved_main
            if saved_dunder is None:
                sys.modules.pop("__main__", None)
            else:
                sys.modules["__main__"] = saved_dunder


class TestLeftoverReplHostSecureORM:
    def test_resolve_when_from_import_unknown_location(self):
        """Leftover ``core.repl_host`` has no class; ``__main__`` leftover already does."""
        _bMT = type(sys)
        live = HostSecureORM

        class _LazyMod(_bMT):
            def __init__(self, name):
                super().__init__(name)
                self.__dict__["_bsrc"] = _LAZY_MAIN_SRC
                self.__dict__["_bloaded"] = True
                self.__dict__["_bloading"] = False
                self.__dict__["_bload_count"] = 0

            def _bload(self):
                self.__dict__["_bload_count"] = self.__dict__.get("_bload_count", 0) + 1
                if self.__dict__.get("_bloading") or self.__dict__.get("_bloaded"):
                    return
                raise RuntimeError("Database instance already exists")

            def __getattribute__(self, name):
                ns = object.__getattribute__(self, "__dict__")
                if name in ns:
                    return ns[name]
                if name in {"__getattr__", "__getattribute__", "_bload"} or (
                    name.startswith("__") and name.endswith("__")
                ):
                    return object.__getattribute__(self, name)
                return object.__getattribute__(self, "__getattr__")(name)

            def __getattr__(self, name):
                object.__getattribute__(self, "_bload")()
                ns = object.__getattribute__(self, "__dict__")
                try:
                    return ns[name]
                except KeyError:
                    raise AttributeError(
                        f"module '{self.__name__}' has no attribute '{name}'"
                    )

        leftover = _LazyMod("core.repl_host")
        executed = types.ModuleType("__main__")
        executed.HostSecureORM = live
        saved = sys.modules.get("core.repl_host")
        saved_main = sys.modules.get("main")
        saved_dunder = sys.modules.get("__main__")
        try:
            sys.modules["core.repl_host"] = leftover
            sys.modules["__main__"] = executed
            sys.modules["main"] = executed
            assert "HostSecureORM" not in leftover.__dict__
            assert "HostSecureORM" not in type(leftover).__dict__
            with pytest.raises(ImportError, match="unknown location"):
                exec(
                    "from core.repl_host import HostSecureORM",
                    {"__name__": "leftover_import"},
                )
            leftover.__dict__["_bloaded"] = False
            with pytest.raises(RuntimeError, match="Database instance already exists"):
                leftover._bload()
            leftover.__dict__["_bloaded"] = True
            leftover.__dict__["_bload_count"] = 0
            assert resolve_host_secure_orm() is live
            assert leftover._bload_count == 0
            del executed.HostSecureORM
            with pytest.raises(ImportError, match="unknown location"):
                resolve_host_secure_orm()
            assert leftover._bload_count == 0
        finally:
            if saved is None:
                sys.modules.pop("core.repl_host", None)
            else:
                sys.modules["core.repl_host"] = saved
            if saved_main is None:
                sys.modules.pop("main", None)
            else:
                sys.modules["main"] = saved_main
            if saved_dunder is None:
                sys.modules.pop("__main__", None)
            else:
                sys.modules["__main__"] = saved_dunder
