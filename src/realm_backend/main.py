"""The realm canister. Every operation is called by name: ``read`` for queries, ``run`` for changes.

``read("ops_list", "{}")`` lists them all, with their permission, flags and description.
"""
from basilisk import Async, StableBTreeMap, init, post_upgrade, query, text, update, void
from ic_python_db import Database

__basilisk_features__ = ["shell", "browse"]

Database.init(db_storage=StableBTreeMap[str, str](memory_id=1, max_key_size=256, max_value_size=100_000))

from api import setup  # noqa: E402
from core.op import dispatch, run as drive  # noqa: E402


@init
def init_(installer: text) -> void:
    setup.start(installer)


@post_upgrade
def post_upgrade_() -> void:
    setup.start()


@query
def read(name: text, args: text) -> text:
    return drive(dispatch(name, args, query=True))


@update
def run(name: text, args: text) -> Async[text]:
    return (yield from dispatch(name, args))
