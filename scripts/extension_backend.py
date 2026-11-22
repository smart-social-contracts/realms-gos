"""Write the backend an extension needs to reach the realm: one function per operation its manifest declares, named
after it, so that a screen's ``read("treasury_view")`` reaches its backend's ``treasury_view``, which calls the realm's.

    python3 scripts/extension_backend.py extensions/extensions/vault [<extension dir> ...]

A screen reaches the realm only through its extension's backend, which the realm runs as the member, in a sandbox that
lets it call only the declared operations (api/extensions.py, ``extension_call`` and ``extension_read``). A read
operation is called at once (``rpc``), so the function also runs in a query; any other waits for the operation
(``yield wait``), which may call other canisters. The file is overwritten; the manifest gets ``"backend": true``.

Run it with the venv of the unit tests: it loads the realm's operations with tests/ggg's stand-in for the chain.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "tests", "ggg"), os.path.join(ROOT, "src", "realm_backend")]

import core  # noqa: E402
import fake_chain  # noqa: E402

sys.modules["core.chain"] = core.chain = fake_chain

from ic_python_db import Database  # noqa: E402

Database.init()

from core.op import ops  # noqa: E402

HEADER = '''"""{name}'s backend: the only way its screen reaches the realm. The realm runs it as the member who calls it, in a
sandbox that lets it call only the operations the manifest declares. Each function below is one of them, by the same
name: the screen's ``read(name, args)`` runs a read one as a query, free; its ``run(name, args)`` runs one that changes
the realm, which waits for the operation (it may call other canisters) and returns its reply.

Written by realms-gos scripts/extension_backend.py from the manifest; functions of the extension's own go below them.
"""
'''


def backend(name, operations):
    known = ops()
    unknown = [o for o in operations if o not in known]
    if unknown:
        raise SystemExit(f"{name}: the realm has no operation {', '.join(unknown)}")
    parts = [HEADER.format(name=name)]
    for o in operations:
        if known[o]["read"]:
            parts.append(f'\ndef {o}(**args):\n    return rpc("{o}", **args)\n')
        else:
            parts.append(f'\ndef {o}(**args):\n    return (yield wait("{o}", **args))\n')
    return "\n".join(parts)


def main(dirs):
    for d in dirs:
        path = os.path.join(d, "manifest.json")
        with open(path) as f:
            raw = f.read()
        manifest = json.loads(raw)
        name = manifest.get("name") or os.path.basename(os.path.normpath(d))
        os.makedirs(os.path.join(d, "backend"), exist_ok=True)
        with open(os.path.join(d, "backend", "entry.py"), "w") as f:
            f.write(backend(name, manifest.get("operations") or []))
        if manifest.get("backend") is not True:
            manifest["backend"] = True
            first = raw.split("\n")[1]  # the first key's line gives the file's indent
            indent = len(first) - len(first.lstrip(" "))
            with open(path, "w") as f:
                f.write(json.dumps(manifest, indent=indent, ensure_ascii=False) + "\n")
        print(f"{name}: {len(manifest.get('operations') or [])} operations")


if __name__ == "__main__":
    main(sys.argv[1:])
