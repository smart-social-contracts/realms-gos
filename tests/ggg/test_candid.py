"""The GGG methods (ggg_methods, over ggg_api) must have the Candid types of ggg.did, as basilisk builds them."""

import ast
import importlib.util
import pathlib

import fake_chain
import ggg_methods
import world
from api import setup
from basilisk.wasm_manipulator import extract_methods_from_python, generate_candid_from_methods
from ggg import Realm
from kit import ok

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("ggg_candid", ROOT / "scripts" / "ggg_candid.py")
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)


def parse(did):
    # basilisk adds its own func and service types, which ggg.did doesn't use.
    return gen.Parser(gen.tokens("\n".join(line for line in did.splitlines() if "= func" not in line
                                           and "= service" not in line))).did()


def canon(t, env, seen=()):
    """``t`` with every named type expanded, so two files that name their types differently compare equal."""
    kind, body = t
    if kind in ("opt", "vec"):
        return kind, canon(body, env, seen)
    if kind == "name":
        if body not in env:
            return "prim", body
        return ("rec", body) if body in seen else canon(env[body], env, seen + (body,))
    return kind, tuple(sorted((str(i) if f is None else f, canon(ft, env, seen)) for i, (f, ft) in enumerate(body)))


def signatures(did):
    env, methods = parse(did)
    return {n: ([canon(p, env) for p in ps], [canon(r, env) for r in rs], q) for n, ps, rs, q in methods}


def built():
    """The canister's GGG methods, as basilisk turns realm_backend's sources into Candid."""
    src = ROOT / "src" / "realm_backend"
    source = "\n".join(p.read_text() for p in sorted(src.rglob("*.py")) if "__pycache__" not in p.parts)
    methods, types, lifecycle = extract_methods_from_python(source)
    methods = [m for m in methods if m["name"].startswith(("ggg_", "icrc"))]
    return signatures(generate_candid_from_methods(methods, types, lifecycle))


def test_ggg_did_and_ggg_methods_are_generated_from_the_submodule():
    for path, text in gen.generate().items():
        assert path.read_text() == text, "run scripts/ggg_candid.py"


def test_every_method_of_ggg_did_is_built_with_its_types():
    want, got = signatures(gen.DID.read_text()), built()
    assert set(got) == set(want), f"missing {sorted(set(want) - set(got))}, extra {sorted(set(got) - set(want))}"
    for name, sig in got.items():
        assert sig == want[name], f"{name}: built as {sig}, ggg.did says {want[name]}"


def test_main_loads_every_method():
    """basilisk looks a method up among main's globals; main star-imports ggg_methods, which lists them all."""
    tree = ast.parse((ROOT / "src" / "realm_backend" / "main.py").read_text())
    assert any(isinstance(n, ast.ImportFrom) and n.module == "ggg_methods" and n.names[0].name == "*"
               for n in ast.walk(tree))
    assert set(ggg_methods.__all__) == set(built())


def test_icrc10_lists_icrc10_and_the_ggg_did_implemented():
    names = {s["name"]: s["url"] for s in ggg_methods.icrc10_supported_standards()}
    assert set(names) == {"ICRC-10", "GGG"} and names["GGG"].endswith("/blob/v0.1.0/ggg.did")


def test_realm_info_converts_to_ggg():
    world.build()
    ok("setup_step", "ctl", step="install", network="ic", founder="frank")
    ok("setup_step", "frank", step="realm", name="Agora", open_registration=True)
    ok("setup_step", "frank", step="tokens", accounting_currency="ckUSDC")
    Realm["1"].trusted_principals = [fake_chain.ANONYMOUS]
    Realm["1"].manifest_data = {**Realm["1"].manifest_data, "governance": {"threshold": 60, "quorum_percent": 25}}
    r = ggg_methods.ggg_realm_info()
    assert r["name"] == "Agora" and r["status"] == {"Setup": None} and r["open_registration"]
    assert r["accounting_currency"] == "ckUSDC" and r["accounting_currency_decimals"] == 8
    assert r["calendar"] == "1" and r["federation_realm_id"] is None and r["logo_url"] is None
    assert r["trusted_principals"] == [fake_chain.ANONYMOUS]
    assert (r["voting_threshold"], r["voting_quorum"]) == (6000, 2500)
    assert r["installed_version"] == setup.VERSION and r["network"] == "ic"
    ok("setup_launch", "frank")
    assert ggg_methods.ggg_realm_info()["status"] == {"Alpha": None}


def test_every_operation_names_where_its_result_is():
    """A GGG reply that is a record, a page or a value comes from the key its @op ``returns`` names: ggg_api
    does not search the reply for it."""
    import ggg_api
    from core.op import ops

    unnamed = []
    for name, (_, reply_t) in ggg_api.METHODS.items():
        if name in ggg_api.LOCAL or name in ggg_api.BUILD:
            continue
        t = ggg_api.unwrap(ggg_api.fields(reply_t)["Ok"]) if ggg_api.is_variant(reply_t) else reply_t
        page = ggg_api.is_record(t) and {"items", "next"} <= set(ggg_api.fields(t))
        if (ggg_api.entity(t) is not None or page or ggg_api.kind(t)[0] in ("prim", "vec")) \
                and not ops()[name]["returns"]:
            unnamed.append(f"{name} -> {t}")
    assert not unnamed, "give these @op a returns=:\n" + "\n".join(unnamed)
