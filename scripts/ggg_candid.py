#!/usr/bin/env python3
"""Generate src/realm_backend/ggg_did.py, the basilisk types of ggg.did (the ggg submodule), and
src/realm_backend/ggg_methods.py, its methods, each a one-line call into ggg_api.

Each named type becomes a ``Record`` or ``Variant`` class, one line each to stay inside the line budget.
Inline records and variants get a name from where they appear, and one class serves every copy of the same shape.
A reply ``variant { Ok : T; Err : GggError }`` is ``TReply`` (``GovernedReply``); any other is named after
its method or field (``Icrc10SupportedStandardsResult``, ``GggErrorForbidden``). Tuples stay inline.

basilisk reads the classes when it builds the canister; at run time they carry no types, so ggg_did also holds
``SCHEMA``: each type's fields as type expressions (``"opt vec Ref"``, ``"(text, Value)"``), which ggg_api reads.

    python scripts/ggg_candid.py           # rewrite the files
    python scripts/ggg_candid.py --check   # fail if they are out of date
"""

import keyword
import pathlib
import textwrap
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DID = ROOT / "ggg" / "ggg.did"
OUT = ROOT / "src" / "realm_backend" / "ggg_did.py"
METHODS = ROOT / "src" / "realm_backend" / "ggg_methods.py"
ALIASES = ("Timestamp", "Duration")
PRIMITIVES = {"nat": "nat", "nat8": "nat8", "nat16": "nat16", "nat32": "nat32", "nat64": "nat64", "int": "int",
              "bool": "bool", "text": "text", "blob": "blob", "principal": "Principal", "null": "null"}
TOKEN = re.compile(r'"[^"]*"|\w+|->|[{}();:=,]')


def tokens(source):
    return TOKEN.findall(re.sub(r"//[^\n]*", "", source))


class Parser:
    def __init__(self, toks):
        self.toks, self.i = toks, 0

    def peek(self, k=0):
        return self.toks[self.i + k] if self.i + k < len(self.toks) else None

    def take(self, want=None):
        tok = self.toks[self.i]
        if want is not None and tok != want:
            raise SyntaxError(f"expected {want!r}, got {tok!r} at token {self.i}")
        self.i += 1
        return tok

    def type(self):
        tok = self.take()
        if tok in ("opt", "vec"):
            return (tok, self.type())
        if tok in ("record", "variant"):
            return (tok, self.fields(tok))
        return ("name", tok)

    def fields(self, kind):
        self.take("{")
        out = []
        while self.peek() != "}":
            if self.peek(1) == ":" or (kind == "variant" and self.peek(1) in (";", "}")):
                name = self.take().strip('"')
                bare = self.peek() != ":"
                if not bare:
                    self.take(":")
                out.append((name, ("name", "null") if bare else self.type()))
            else:
                out.append((None, self.type()))
            if self.peek() == ";":
                self.take()
        self.take("}")
        return out

    def args(self):
        self.take("(")
        out = []
        while self.peek() != ")":
            out.append(self.type())
            if self.peek() == ",":
                self.take()
        self.take(")")
        return out

    def did(self):
        types, methods = {}, []
        while self.peek() == "type":
            self.take()
            name = self.take()
            self.take("=")
            types[name] = self.type()
            self.take(";")
        self.take("service")
        self.take(":")
        if self.peek() == "(":  # init arguments
            self.args()
            self.take("->")
        self.take("{")
        while self.peek() != "}":
            name = self.take().strip('"')
            self.take(":")
            params = self.args()
            self.take("->")
            returns = self.args()
            query = self.peek() == "query" and self.take()
            self.take(";")
            methods.append((name, params, returns, bool(query)))
        return types, methods


def camel(name):
    return "".join(part[:1].upper() + part[1:] for part in name.split("_"))


class Emitter:
    def __init__(self, types):
        self.types, self.classes, self.inline, self.schema = types, {}, {}, {}

    def field(self, name):
        return name + "_" if keyword.iskeyword(name) else name

    def ref(self, t, hint):
        """The annotation for ``t``, hoisting an inline record or variant into a class named ``hint``."""
        kind, body = t
        if kind == "opt":
            return f"Opt[{self.ref(body, hint)}]"
        if kind == "vec":
            return f"Vec[{self.ref(body, hint)}]"
        if kind == "name":
            if body in PRIMITIVES:
                return PRIMITIVES[body]
            if body in ALIASES:
                return body
            if body not in self.types:
                raise SyntaxError(f"unknown type {body}")
            target = self.types[body]
            return f'"{body}"' if target[0] in ("record", "variant") else self.ref(target, body)
        if all(name is None for name, _ in body):
            return "Tuple[" + ", ".join(self.ref(ft, f"{hint}{i}") for i, (_, ft) in enumerate(body)) + "]"
        if repr(t) in self.inline:
            return f'"{self.inline[repr(t)]}"'
        cases = dict(body)
        if kind == "variant" and set(cases) == {"Ok", "Err"} and cases["Err"] == ("name", "GggError"):
            hint = re.sub(r"\W", "", self.ref(cases["Ok"], hint + "Ok").replace("Opt[", "Opt").replace("Vec[", "Vec")
                          .replace("null", "Null")) + "Reply"
            hint = hint[0].upper() + hint[1:]
        self.inline[repr(t)] = hint
        self.define(hint, t)
        return f'"{hint}"'

    def expr(self, t):
        """``t`` as a SCHEMA type expression, once ``ref`` has named its inline types."""
        kind, body = t
        if kind in ("opt", "vec"):
            return f"{kind} {self.expr(body)}"
        if kind == "name":
            if body in PRIMITIVES or body in ALIASES or self.types[body][0] in ("record", "variant"):
                return body
            return self.expr(self.types[body])
        if all(name is None for name, _ in body):
            return "(" + ", ".join(self.expr(ft) for _, ft in body) + ")"
        return self.inline[repr(t)]

    def define(self, name, t):
        kind, body = t
        if name in self.classes:
            raise SyntaxError(f"two types would be named {name}")
        self.classes[name] = None
        fields = "; ".join(f"{self.field(f)}: {self.ref(ft, name + camel(f))}" for f, ft in body)
        base = "Record" if kind == "record" else "Variant, total=False"
        self.classes[name] = f"class {name}({base}): {fields}" if fields else f"class {name}({base}): pass"
        self.schema[name] = f'SCHEMA["{name}"] = ("{kind}", {{{", ".join(f"{f!r}: {self.expr(ft)!r}" for f, ft in body)}}})'.replace("'", '"')


def method(name, params, returns, query, em):
    """One method, as a line: the decorator, then a def calling ggg_api with the types it converts."""
    stem, op = camel(name.removeprefix("ggg_")), name.removeprefix("ggg_")
    arg = em.ref(params[0], f"{stem}Args").replace('"', "") if params else None
    reply = em.ref(returns[0], f"{stem}Result").replace('"', "")
    sig, a = (f"a: {arg}", "a") if arg else ("", "None")
    types = f"\"{em.expr(params[0])}\"" if params else "None"
    call = f'"{op}", {types}, {a}, "{em.expr(returns[0])}"'
    if query:
        return f"@query\ndef {name}({sig}) -> {reply}: return read({call})"
    return f"@update\ndef {name}({sig}) -> Async[{reply}]: return (yield from run({call}))"


def generate():
    types, methods = Parser(tokens(DID.read_text())).did()
    em = Emitter(types)
    for name, t in types.items():
        if t[0] in ("record", "variant"):
            em.define(name, t)
    defs = [method(*m, em) for m in methods]
    signatures = [f'"{n.removeprefix("ggg_")}": ({em.expr(ps[0])!r}, {em.expr(rs[0])!r})'.replace("'", '"')
                  if ps else f'"{n.removeprefix("ggg_")}": (None, "{em.expr(rs[0])}")' for n, ps, rs, _ in methods]
    # basilisk merges same-named Record classes found anywhere under src/, so a clash would change both.
    for path in (ROOT / "src").rglob("*.py"):
        if path not in (OUT, METHODS) and "node_modules" not in path.parts:
            for name in re.findall(r"^class (\w+)\((?:Record|Variant)", path.read_text(), re.M):
                if name in em.classes:
                    raise SyntaxError(f"{path.relative_to(ROOT)} already defines {name}")
    version = re.search(r"GGG (\d+\.\d+\.\d+)", DID.read_text()).group(1)
    lines = [
        f'"""The types of ggg.did (GGG {version}), for basilisk. Generated by scripts/ggg_candid.py: do not edit."""',
        "# fmt: off",
        "# flake8: noqa",
        "from basilisk import Alias, Opt, Principal, Record, Tuple, Variant, Vec, blob, nat, nat8, nat16, nat32, nat64, null, text  # noqa: F401",
        "",
        f'VERSION = "{version}"',
        *(f"{alias} = Alias[nat64]" for alias in ALIASES),
        "",
        *em.classes.values(),
        "",
        "# The same types, for ggg_api: a record's or variant's fields, each a type expression.",
        "SCHEMA = {}",
        *em.schema.values(),
        "",
        "# Each method, by its operation's name: its argument type (None for none) and its reply type.",
        "METHODS = {",
        *textwrap.wrap(", ".join(signatures), 116, initial_indent="    ", subsequent_indent="    ",
                       break_on_hyphens=False),
        "}",
        "",
    ]
    names = [m[0] for m in methods]
    methods_lines = [
        '"""The methods of ggg.did. Generated by scripts/ggg_candid.py: do not edit; ggg_api does the work."""',
        "# fmt: off",
        "# flake8: noqa",
        "from basilisk import Async, Opt, Principal, Vec, blob, query, text, update  # noqa: F401",
        "from ggg_api import read, run",
        "from ggg_did import *  # noqa: F401, F403",
        "",
        f"__all__ = {names!r}",
        "",
        *defs,
        "",
    ]
    return {OUT: "\n".join(lines), METHODS: "\n".join(methods_lines)}


def main():
    for path, text in generate().items():
        if "--check" in sys.argv:
            if not path.exists() or path.read_text() != text:
                sys.exit(f"{path.relative_to(ROOT)} is out of date: run scripts/ggg_candid.py")
            continue
        path.write_text(text)
        print(f"wrote {path.relative_to(ROOT)}: {len(text.splitlines())} lines")


if __name__ == "__main__":
    main()
