import json

import fake_chain
from core import op


def call(name, caller="ctl", /, **args):
    """Run an operation as ``caller``; returns the decoded reply."""
    fake_chain.state["caller"] = caller
    return json.loads(op.run(op.dispatch(name, json.dumps(args))))


def ok(name, caller="ctl", /, **args):
    out = call(name, caller, **args)
    assert out["ok"], out
    return out


def denied(name, caller, /, **args):
    out = call(name, caller, **args)
    assert not out["ok"], out
    return out["error"]


def later(seconds):
    fake_chain.state["now"] += seconds
