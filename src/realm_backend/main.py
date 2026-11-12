"""The realm canister. Every operation is called by name: ``read`` for queries, ``run`` for changes.

``read("ops_list", "{}")`` lists them all, with their permission, flags and description. The methods of the
GGG standard (ggg.did) are in ggg_methods.
"""
from basilisk import Async, StableBTreeMap, init, post_upgrade, query, text, update, void
from basilisk.canisters.management import HttpResponse, HttpTransformArgs
from ic_python_db import Database

__basilisk_features__ = ["shell", "browse"]

Database.init(db_storage=StableBTreeMap[str, str](memory_id=1, max_key_size=256, max_value_size=100_000))

# Before anything reads a row: ic-python-db parses each one with json.loads, which is slow in a canister.
from core import fastjson  # noqa: E402

fastjson.install()

from api import setup  # noqa: E402
from core import journal_signing  # noqa: E402
from core.op import dispatch, run as drive  # noqa: E402
# basilisk calls a method by looking its name up among main's globals. Reading ``__all__`` loads the module.
from ggg_methods import *  # noqa: E402, F401, F403

# basilisk bundles what main imports: the layered codex library ships with the realm, which runs its own copy
# (docs/codices/layered-codex.md §12). Nothing runs until the runtime loads a chapter through it. The same goes for
# the storage rules of a layered codex, until the install task imports them.
import realms.legislation  # noqa: E402, F401
import core.layered_codex  # noqa: E402, F401


@init
def init_(installer: text) -> void:
    setup.start(installer)
    journal_signing.resume()


@post_upgrade
def post_upgrade_() -> void:
    setup.start()
    journal_signing.resume()  # timers do not survive an upgrade


@query
def read(name: text, args: text) -> text:
    return drive(dispatch(name, args, query=True))


@update
def run(name: text, args: text) -> Async[text]:
    return (yield from dispatch(name, args))


@query
def http_transform(args: HttpTransformArgs) -> HttpResponse:
    """What every node's reply to an HTTPS outcall (core.chain.http_get) is passed through before the replica
    compares them: the headers differ from node to node, the body must not."""
    response = args["response"]
    response["headers"] = []
    return response
