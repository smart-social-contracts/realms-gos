"""The operation registry must match the operation tables of docs/ggg/spec.md, both ways."""

import pathlib
import re

from core import op

SPEC = pathlib.Path(__file__).resolve().parents[2] / "docs" / "ggg" / "spec.md"
FLAGS = ("read", "delegable", "governed", "signing")


def spec_ops():
    ops, wildcards = {}, {}
    rows = [line for line in SPEC.read_text().splitlines()
            if line.startswith("| `") and line.count("|") == 5]
    for row in rows:
        names_cell, perm_cell, flag_cell = [c.strip() for c in row.split("|")[1:4]]
        names = re.findall(r"`([a-z_*]+)`", names_cell)
        if not names or ":" in names_cell.split("`")[1]:
            continue
        perms = re.findall(r"`([a-z_]+(?:\.[a-z_*]+)+)`", perm_cell)
        flags = {f: f in flag_cell for f in FLAGS}
        flags["public"] = "(public)" in perm_cell
        for i, name in enumerate(names):
            if len(perms) == len(names):
                allowed = {perms[i]}
            elif any(p.endswith(".*") for p in perms):
                allowed = {perms[0][:-1] + name.split("_", 1)[1]}
            else:
                allowed = set(perms)
            target = wildcards if name.endswith("*") else ops
            target[name.rstrip("*")] = (allowed, flags)
    return ops, wildcards


def registered():
    return {n: s for n, s in op.OPS.items() if not n.startswith("t_")}


def flags_of(s):
    return {"read": s["read"], "delegable": s["delegable"], "governed": s["governed"] is not None,
            "signing": s["signing"], "public": s["public"]}


def test_every_spec_operation_is_registered_as_specified():
    ops, wildcards = spec_ops()
    reg = registered()
    missing = sorted(set(ops) - set(reg))
    assert not missing, f"in the GGG spec but not registered: {missing}"
    for name, (allowed, flags) in ops.items():
        s = reg[name]
        assert flags_of(s) == flags, f"{name}: flags {flags_of(s)} != spec {flags}"
        assert flags["public"] or s["perm"] in allowed, f"{name}: permission {s['perm']} not in {allowed}"
    for prefix in wildcards:
        assert any(n.startswith(prefix) for n in reg), f"no operation for {prefix}*"


def test_every_registered_operation_is_specified():
    ops, wildcards = spec_ops()
    extra = sorted(n for n in registered() if n not in ops and not any(n.startswith(w) for w in wildcards))
    assert not extra, f"registered but not in the GGG spec: {extra}"
