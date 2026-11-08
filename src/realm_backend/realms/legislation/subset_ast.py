"""The readable subset of Python (``subset``), checked on the syntax tree for authors: the same
rules, with the line of each problem, plus a whitelist of statement and expression kinds.

``realms legislation check`` runs this before loading a chapter; the realm cannot (``ast`` is a stub
there) and runs ``subset`` on the bytecode, which is the check that counts. The tests keep the two
in agreement on what they refuse.
"""

from realms.legislation.errors import Refused
from realms.legislation.subset import (
    CLASS_BODY,
    FORMAT_METHODS,
    FRAME_ATTRIBUTES,
    GGG,
    SDK,
    sdk_names,
)

MARKS = ("fixed", "extension", "overridable")
NO_PLAIN_BOOL = "use &, | and ~ in a forbid; see legislation/README.md"


def _comparisons():
    import ast

    return (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)


COMPARISONS_AST = _comparisons()


class Check:
    def __init__(self, text, file):
        import ast

        self.ast, self.file = ast, file
        try:
            self.tree = ast.parse(text, file)
        except SyntaxError as e:
            raise Refused(e.msg, e.lineno, file)
        self.imported = set()

    def refuse(self, node, reason):
        raise Refused(reason, getattr(node, "lineno", None), self.file)

    def run(self):
        ast, body = self.ast, self.tree.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body = body[1:]
        classes = [n for n in body if isinstance(n, ast.ClassDef)]
        if len(classes) != 1:
            self.refuse(
                self.tree.body[0] if self.tree.body else self.tree,
                "one class per file, extending above(...)",
            )
        for node in body:
            if isinstance(node, ast.ImportFrom):
                self.import_statement(node)
            elif isinstance(node, ast.Assign):
                self.constant_assignment(node)
            elif isinstance(node, ast.ClassDef):
                self.chapter_class(node)
            else:
                self.refuse(
                    node,
                    "nothing else at module level except imports from the SDK, constants and the one class",
                )

    def import_statement(self, node):
        names = [alias.name for alias in node.names]
        if node.level or node.module not in (SDK, GGG) or "*" in names:
            self.refuse(
                node,
                f"import {node.module}: a chapter imports only from {SDK} and {GGG}, as from ... import ...",
            )
        for alias in node.names:
            self.identifier(node, alias.asname or alias.name, "module")
        if node.module == SDK:
            unknown = [n for n in names if n not in sdk_names()]
            if unknown:
                self.refuse(
                    node,
                    f"from {SDK} import {', '.join(unknown)}: a chapter imports only {', '.join(sdk_names())}",
                )
        self.imported.update(alias.asname or alias.name for alias in node.names)

    def constant_assignment(self, node):
        ast = self.ast
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            self.refuse(node, "a module-level assignment names one constant")
        self.identifier(node, node.targets[0].id, "module")
        self.constant(node.value)

    def constant(self, node, member=False):
        """A constant expression: literals, containers of them, SDK calls on them, imported SDK names.
        A class ``member`` may also hold an ``F`` tree (layered-codex.md §6): ``F.x`` and ``S.x``,
        their methods (``F.rate.is_some()``), one comparison at a time, and ``~``."""
        ast = self.ast
        if isinstance(node, ast.Constant):
            self.literal(node)
        elif isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            for item in node.elts:
                self.constant(item, member)
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                self.constant(key, member)
                self.constant(value, member)
        elif isinstance(node, ast.UnaryOp) and isinstance(
            node.op, (ast.USub, ast.UAdd)
        ):
            self.constant(node.operand, member)
        elif isinstance(node, ast.BinOp):
            self.constant(node.left, member)
            self.constant(node.right, member)
        elif isinstance(node, ast.Name):
            self.identifier(node, node.id, "module")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            self.identifier(node, node.func.id, "module")
            for arg in node.args:
                self.constant(arg, member)
            for keyword in node.keywords:
                self.keyword(keyword)
                if isinstance(keyword.value, ast.Lambda):
                    self.function(keyword.value, [])
                else:
                    self.constant(keyword.value, member)
        elif member and self.fact_expression(node):
            pass
        elif member and isinstance(node, (ast.BoolOp, ast.Compare)):
            self.refuse(node, NO_PLAIN_BOOL)  # and, or, a chained comparison (rule 2)
        elif member and isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            self.refuse(node, NO_PLAIN_BOOL)
        elif member and isinstance(node, ast.IfExp):
            self.refuse(node, "write when(c, a, b) in a forbid, not a if c else b")
        else:
            if (
                member
            ):  # the dunder message first, for authors: above('x').__annotations__...
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Attribute):
                        self.attribute(sub, sub.attr)
            self.refuse(
                node,
                "a constant expression: literals, lists and dicts of them, and SDK calls on them",
            )

    def fact_expression(self, node):
        """An ``F`` tree's own forms: ``F.x``, ``F.x.method(...)``, one of the six comparisons, ``~x``.
        No attribute read in a class body starts with ``_``: the dunder names a class body may hold
        are the ones CPython writes there, as names, never attributes of another class.
        """
        ast = self.ast
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            self.identifier(node, node.value.id, "class body")
            self.attribute(node, node.attr)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and not node.keywords
            and self.fact_expression(node.func.value)
        ):
            self.attribute(node, node.func.attr)
            for arg in node.args:
                self.constant(arg, True)
        elif (
            isinstance(node, ast.Compare)
            and len(node.ops) == 1
            and isinstance(node.ops[0], COMPARISONS_AST)
        ):
            self.constant(node.left, True)
            self.constant(node.comparators[0], True)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Invert):
            self.constant(node.operand, True)
        else:
            return False
        return True

    def chapter_class(self, node):
        ast = self.ast
        bases = node.bases
        if node.decorator_list or node.keywords or len(bases) != 1:
            self.refuse(
                node,
                f"{node.name} extends above(...) and nothing else, with no decorator or keyword",
            )
        base = bases[0]
        if not (
            isinstance(base, ast.Call)
            and isinstance(base.func, ast.Name)
            and base.func.id == "above"
            and not base.keywords
            and len(base.args) == 1
            and isinstance(base.args[0], ast.Constant)
            and isinstance(base.args[0].value, str)
        ):
            self.refuse(node, f'{node.name} extends above("<domain>") and nothing else')
        self.identifier(node, node.name, "class body")
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body = body[1:]
        for member in body:
            if isinstance(member, ast.Pass):
                continue
            if isinstance(member, ast.Assign):
                if len(member.targets) != 1 or not isinstance(
                    member.targets[0], ast.Name
                ):
                    self.refuse(member, "a member is one name")
                self.identifier(member, member.targets[0].id, "class body")
                self.constant(member.value, member=True)
            elif isinstance(member, ast.FunctionDef):
                self.method(member)
            else:
                self.refuse(
                    member,
                    "a chapter's class holds settings, forbids and methods, nothing else",
                )

    def method(self, node):
        ast = self.ast
        self.identifier(node, node.name, "class body")
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Name) and decorator.id in MARKS:
                continue
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Name)
                and decorator.func.id in MARKS
                and not decorator.args
            ):
                for keyword in decorator.keywords:
                    self.keyword(keyword)
                    self.constant(keyword.value)
                continue
            self.refuse(
                decorator,
                "a method is decorated with a mark (fixed, extension, overridable) and nothing else",
            )
        self.function(node, [])

    # ── methods and lambdas ──

    def function(self, node, outer_locals):
        ast = self.ast
        args = node.args
        if args.defaults or any(d is not None for d in args.kw_defaults):
            self.refuse(
                node,
                "a default argument is state shared by every call: pass the value instead",
            )
        for arg in (
            args.posonlyargs
            + args.args
            + args.kwonlyargs
            + [a for a in (args.vararg, args.kwarg) if a]
        ):
            self.identifier(arg, arg.arg, "method")
        bound = {a.arg for a in args.posonlyargs + args.args + args.kwonlyargs}
        bound.update(a.arg for a in (args.vararg, args.kwarg) if a)
        if isinstance(node, ast.Lambda):
            self.expression(node.body, bound)
            return
        for statement in node.body:
            bound.update(self.assigned(statement))
        for statement in node.body:
            self.statement(statement, bound)

    def assigned(self, node):
        """Names a statement binds in its function, so attribute stores on them are on locals."""
        ast = self.ast
        names = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                names.add(child.id)
            elif isinstance(child, ast.FunctionDef):
                names.add(child.name)
        return names

    def statement(self, node, local):
        ast = self.ast
        if isinstance(node, ast.Expr):
            self.expression(node.value, local)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                self.target(target, local)
            self.expression(node.value, local)
        elif isinstance(node, ast.AugAssign):
            self.target(node.target, local)
            self.expression(node.value, local)
        elif isinstance(node, ast.Return):
            if node.value is not None:
                self.expression(node.value, local)
        elif isinstance(node, (ast.If, ast.While)):
            self.expression(node.test, local)
            for child in node.body + node.orelse:
                self.statement(child, local)
        elif isinstance(node, ast.For):
            self.target(node.target, local)
            self.expression(node.iter, local)
            for child in node.body + node.orelse:
                self.statement(child, local)
        elif isinstance(node, (ast.Break, ast.Continue, ast.Pass)):
            pass
        elif isinstance(node, ast.Raise):
            for value in (node.exc, node.cause):
                if value is not None:
                    self.expression(value, local)
        elif isinstance(node, ast.Assert):
            self.expression(node.test, local)
            if node.msg is not None:
                self.expression(node.msg, local)
        elif isinstance(node, ast.Delete):
            for target in node.targets:
                if isinstance(target, ast.Attribute):
                    self.refuse(
                        node, "deleting an attribute is not part of the readable subset"
                    )
                self.target(target, local)
        elif isinstance(node, ast.FunctionDef):
            self.identifier(node, node.name, "method")
            if node.decorator_list:
                self.refuse(node, "a nested function has no decorator")
            self.function(node, local)
        else:
            self.refuse(
                node,
                f"a {type(node).__name__.lower()} statement is not part of the readable subset",
            )

    def target(self, node, local):
        ast = self.ast
        if isinstance(node, ast.Name):
            self.identifier(node, node.id, "method")
        elif isinstance(node, ast.Attribute):
            self.identifier(node, node.attr, "method")
            if not (isinstance(node.value, ast.Name) and node.value.id in local):
                self.refuse(
                    node,
                    f".{node.attr} = ...: a chapter sets attributes on self, or on what it was given, never on a class or a call's result",
                )
        elif isinstance(node, ast.Subscript):
            self.expression(node, local)
        elif isinstance(node, (ast.Tuple, ast.List)):
            for item in node.elts:
                self.target(item, local)
        elif isinstance(node, ast.Starred):
            self.target(node.value, local)
        else:
            self.refuse(
                node,
                "an assignment target is a name, an attribute of a local, or a subscript",
            )

    def expression(self, node, local):
        ast = self.ast
        simple = (ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp, ast.List, ast.Tuple, ast.Set, ast.Dict,
                  ast.Subscript, ast.Slice, ast.Starred, ast.JoinedStr, ast.FormattedValue)  # fmt: skip
        if isinstance(node, ast.Constant):
            self.literal(node)
        elif isinstance(node, ast.Name):
            self.identifier(node, node.id, "method")
        elif isinstance(node, ast.Attribute):
            self.identifier(node, node.attr, "method")
            if node.attr in FORMAT_METHODS:
                self.refuse(
                    node,
                    f"{node.attr}: write an f-string, str.format reads attributes named in the template",
                )
            self.expression(node.value, local)
        elif isinstance(node, ast.Call):
            self.expression(node.func, local)
            for arg in node.args:
                self.expression(arg, local)
            for keyword in node.keywords:
                self.keyword(keyword)
                self.expression(keyword.value, local)
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp)):
            for generator in node.generators:
                if generator.is_async:
                    self.refuse(node, "no async code in a chapter")
                self.target(generator.target, local | self.assigned(generator.target))
                self.expression(generator.iter, local)
                for cond in generator.ifs:
                    self.expression(cond, local)
            inner = local | {
                n for g in node.generators for n in self.assigned(g.target)
            }
            for part in (
                (node.elt,)
                if not isinstance(node, ast.DictComp)
                else (node.key, node.value)
            ):
                self.expression(part, inner)
        elif isinstance(node, ast.Lambda):
            self.function(node, local)
        elif isinstance(node, ast.NamedExpr):
            self.target(node.target, local)
            self.expression(node.value, local)
        elif isinstance(node, simple):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.expr):
                    self.expression(child, local)
        else:
            self.refuse(
                node,
                f"a {type(node).__name__.lower()} expression is not part of the readable subset",
            )

    # ── names and strings ──

    def keyword(self, keyword):
        if keyword.arg is not None:
            if keyword.arg == "metaclass":
                self.refuse(keyword, "'metaclass': not allowed in a chapter")
            self.identifier(keyword, keyword.arg, "method")

    def attribute(self, node, name):
        if name.startswith("_"):
            self.refuse(
                node, f".{name}: no private or dunder attribute is read in a class body"
            )
        self.identifier(node, name, "method")

    def identifier(self, node, name, scope):
        if name in FRAME_ATTRIBUTES:
            self.refuse(node, f"{name}: no frame or generator attributes in a chapter")
        if name.startswith("__") and not (scope == "class body" and name in CLASS_BODY):
            self.refuse(node, f"{name}: no dunder names in a chapter")

    def literal(self, node):
        value = node.value
        if isinstance(value, str):
            if "__" in value:
                self.refuse(
                    node, f"{value!r}: no dunder names in a chapter, in strings either"
                )
            if value in FRAME_ATTRIBUTES or value == "metaclass":
                self.refuse(node, f"{value!r}: not allowed in a chapter")


def check(text, file):
    """Refuse the chapter ``text`` of ``file`` unless its syntax tree stays in the readable subset."""
    Check(text, file).run()
