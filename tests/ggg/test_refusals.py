"""A refusal says why in its class (core.db.Fail), and GGG callers read that: ggg_api no longer guesses from the
words. A plain Fail is a bad argument, so a plain Fail whose message talks about state or ownership is a mistake."""

import ast
import pathlib
import re

API = pathlib.Path(__file__).parents[2] / "src" / "realm_backend" / "api"
# Words of refusals because of a record's state (Conflict) or because it is someone else's (Forbidden).
STATE_OR_OWNER = re.compile(r"already| exists|still |\.status\}|not yours|not your |you are not|you do not")


def message(node, src):
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "{" + ast.get_source_segment(src, v.value) + "}"
                       for v in node.values)
    return ast.get_source_segment(src, node)


def test_a_plain_fail_is_a_bad_argument():
    wrong = []
    for path in sorted(API.glob("*.py")):
        src = path.read_text()
        for n in ast.walk(ast.parse(src)):
            if isinstance(n, ast.Raise) and isinstance(n.exc, ast.Call) and getattr(n.exc.func, "id", "") == "Fail":
                text = message(n.exc.args[0], src)
                if STATE_OR_OWNER.search(text):
                    wrong.append(f"{path.name}:{n.lineno} {text}")
    assert not wrong, "raise Conflict, Forbidden or NotFound instead:\n" + "\n".join(wrong)
