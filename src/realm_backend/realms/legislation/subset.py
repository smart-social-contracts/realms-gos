"""The readable subset of Python a chapter is written in, checked on the compiled code before any of
it runs (layered-codex.md §4 rule 5, §12.2; docs/architecture/design-choices.md).

This is the install-time check the realm runs inside its subinterpreter sandbox (§9.3,
docs/architecture/sandboxing.md): the sandbox stays the barrier around the realm's data and code;
this check keeps a chapter to the constructs a reader can follow and the loader can reason about.
``ast`` is a stub in the canister, so the check walks the code objects and decodes their bytecode:
a whitelist of instructions per scope (module, class body, method), no generator, coroutine or
pattern-matching code, attribute stores only on a local variable, no dunder name anywhere but the
ones CPython itself writes in a class body, no frame or traceback attribute, no string holding a
dunder, no ``metaclass`` keyword, and a module that holds nothing but imports, constant
assignments and the one class. ``subset_ast`` applies the same rules to the syntax tree, for the
clear errors ``realms legislation check`` prints; the bytecode check is the one the realm runs.

The instruction names cover CPython 3.10 (the laptop and CI) and 3.13 (the realm). The 3.13 table
is written out because the canister may lack the ``opcode`` module.
"""

import sys

from realms.legislation.errors import Refused

SDK, GGG = "realms.legislation", "ggg"
# What CPython itself reads or writes in a class body: the only dunder names a chapter's code may hold,
# and only there.
CLASS_BODY = frozenset(
    (
        "__name__",
        "__module__",
        "__qualname__",
        "__doc__",
        "__annotations__",
        "__annotate__",
        "__classcell__",
        "__classdictcell__",
        "__firstlineno__",
        "__static_attributes__",
    )
)
# Attributes of generators, coroutines, frames and tracebacks: a way out of a chapter's own scope.
FRAME_ATTRIBUTES = frozenset(
    (
        "gi_frame",
        "gi_code",
        "gi_yieldfrom",
        "cr_frame",
        "cr_code",
        "cr_await",
        "ag_frame",
        "ag_code",
        "ag_await",
        "f_back",
        "f_globals",
        "f_locals",
        "f_builtins",
        "f_code",
        "f_trace",
        "tb_frame",
        "tb_next",
    )
)
# str.format and str.format_map walk attributes named in the template: a chapter writes f-strings.
FORMAT_METHODS = frozenset(("format", "format_map"))
# Money is an integer in the token's smallest unit and a rate an integer in hundred-millionths (rule 12): a chapter
# computes with whole numbers and Money, and a float on money would round where GGG's one rule does not. So no float or
# complex number, no float() or complex(), and no `/`, which makes a float of two integers. Nor any exponentiation:
# `x ** n` and pow(x, n) are floats for a negative n, a complex number for a negative base, and n can come at run time,
# so no check of a constant exponent would hold; a chapter needs no power. The names are refused wherever a chapter binds
# or reads them, as a name, an attribute, a keyword or a string, so that the two checkers agree on one list.
FLOAT_NAMES = frozenset(("float", "complex", "pow"))
NO_FLOAT = "no floats in a chapter: money is whole units of the token, a rate is pct(\"1.10\"), a share of money is Money.times(rate)"
NO_DIVISION = "no `/` in a chapter, it makes a float: use // on whole numbers, or Money.times(rate) for a share of money"
NO_POWER = "no `**` or pow() in a chapter, a negative power is a float: multiply, or use Money.times(rate)"
GENERATOR_FLAGS = (
    0x20 | 0x80 | 0x100 | 0x200
)  # CO_GENERATOR, CO_COROUTINE, CO_ITERABLE_COROUTINE, CO_ASYNC_GENERATOR

# CPython 3.13's instruction numbers (Lib/_opcode_metadata.py), the realm's interpreter.
OPNAMES_313 = {
    0: "CACHE", 1: "BEFORE_ASYNC_WITH", 2: "BEFORE_WITH", 4: "BINARY_SLICE", 5: "BINARY_SUBSCR",
    6: "CHECK_EG_MATCH", 7: "CHECK_EXC_MATCH", 8: "CLEANUP_THROW", 9: "DELETE_SUBSCR", 10: "END_ASYNC_FOR",
    11: "END_FOR", 12: "END_SEND", 13: "EXIT_INIT_CHECK", 14: "FORMAT_SIMPLE", 15: "FORMAT_WITH_SPEC",
    16: "GET_AITER", 17: "RESERVED", 18: "GET_ANEXT", 19: "GET_ITER", 20: "GET_LEN", 21: "GET_YIELD_FROM_ITER",
    22: "INTERPRETER_EXIT", 23: "LOAD_ASSERTION_ERROR", 24: "LOAD_BUILD_CLASS", 25: "LOAD_LOCALS",
    26: "MAKE_FUNCTION", 27: "MATCH_KEYS", 28: "MATCH_MAPPING", 29: "MATCH_SEQUENCE", 30: "NOP",
    31: "POP_EXCEPT", 32: "POP_TOP", 33: "PUSH_EXC_INFO", 34: "PUSH_NULL", 35: "RETURN_GENERATOR",
    36: "RETURN_VALUE", 37: "SETUP_ANNOTATIONS", 38: "STORE_SLICE", 39: "STORE_SUBSCR", 40: "TO_BOOL",
    41: "UNARY_INVERT", 42: "UNARY_NEGATIVE", 43: "UNARY_NOT", 44: "WITH_EXCEPT_START", 45: "BINARY_OP",
    46: "BUILD_CONST_KEY_MAP", 47: "BUILD_LIST", 48: "BUILD_MAP", 49: "BUILD_SET", 50: "BUILD_SLICE",
    51: "BUILD_STRING", 52: "BUILD_TUPLE", 53: "CALL", 54: "CALL_FUNCTION_EX", 55: "CALL_INTRINSIC_1",
    56: "CALL_INTRINSIC_2", 57: "CALL_KW", 58: "COMPARE_OP", 59: "CONTAINS_OP", 60: "CONVERT_VALUE", 61: "COPY",
    62: "COPY_FREE_VARS", 63: "DELETE_ATTR", 64: "DELETE_DEREF", 65: "DELETE_FAST", 66: "DELETE_GLOBAL",
    67: "DELETE_NAME", 68: "DICT_MERGE", 69: "DICT_UPDATE", 70: "ENTER_EXECUTOR", 71: "EXTENDED_ARG",
    72: "FOR_ITER", 73: "GET_AWAITABLE", 74: "IMPORT_FROM", 75: "IMPORT_NAME", 76: "IS_OP", 77: "JUMP_BACKWARD",
    78: "JUMP_BACKWARD_NO_INTERRUPT", 79: "JUMP_FORWARD", 80: "LIST_APPEND", 81: "LIST_EXTEND", 82: "LOAD_ATTR",
    83: "LOAD_CONST", 84: "LOAD_DEREF", 85: "LOAD_FAST", 86: "LOAD_FAST_AND_CLEAR", 87: "LOAD_FAST_CHECK",
    88: "LOAD_FAST_LOAD_FAST", 89: "LOAD_FROM_DICT_OR_DEREF", 90: "LOAD_FROM_DICT_OR_GLOBALS", 91: "LOAD_GLOBAL",
    92: "LOAD_NAME", 93: "LOAD_SUPER_ATTR", 94: "MAKE_CELL", 95: "MAP_ADD", 96: "MATCH_CLASS",
    97: "POP_JUMP_IF_FALSE", 98: "POP_JUMP_IF_NONE", 99: "POP_JUMP_IF_NOT_NONE", 100: "POP_JUMP_IF_TRUE",
    101: "RAISE_VARARGS", 102: "RERAISE", 103: "RETURN_CONST", 104: "SEND", 105: "SET_ADD",
    106: "SET_FUNCTION_ATTRIBUTE", 107: "SET_UPDATE", 108: "STORE_ATTR", 109: "STORE_DEREF", 110: "STORE_FAST",
    111: "STORE_FAST_LOAD_FAST", 112: "STORE_FAST_STORE_FAST", 113: "STORE_GLOBAL", 114: "STORE_NAME",
    115: "SWAP", 116: "UNPACK_EX", 117: "UNPACK_SEQUENCE", 118: "YIELD_VALUE", 149: "RESUME",
}  # fmt: skip

# The instructions of the subset, by name, across 3.10 and 3.13. Anything else refuses the chapter.
_BOOKKEEPING = ("CACHE", "NOP", "EXTENDED_ARG", "RESUME", "PUSH_NULL", "PRECALL", "COPY", "SWAP", "DUP_TOP",
                "DUP_TOP_TWO", "ROT_TWO", "ROT_THREE", "ROT_FOUR", "ROT_N", "TO_BOOL", "POP_TOP")  # fmt: skip
_CALLS = (
    "CALL",
    "CALL_KW",
    "KW_NAMES",
    "CALL_FUNCTION",
    "CALL_FUNCTION_KW",
    "CALL_METHOD",
    "LOAD_METHOD",
)
_ARITHMETIC = ("BINARY_OP", "UNARY_NEGATIVE", "UNARY_POSITIVE", "UNARY_NOT", "UNARY_INVERT", "BINARY_ADD",
               "BINARY_SUBTRACT", "BINARY_MULTIPLY", "BINARY_TRUE_DIVIDE", "BINARY_FLOOR_DIVIDE", "BINARY_MODULO",
               "BINARY_POWER", "BINARY_AND", "BINARY_OR", "BINARY_XOR", "BINARY_LSHIFT", "BINARY_RSHIFT",
               "INPLACE_ADD", "INPLACE_SUBTRACT", "INPLACE_MULTIPLY", "INPLACE_TRUE_DIVIDE", "INPLACE_FLOOR_DIVIDE",
               "INPLACE_MODULO", "INPLACE_POWER", "INPLACE_AND", "INPLACE_OR", "INPLACE_XOR", "INPLACE_LSHIFT",
               "INPLACE_RSHIFT")  # fmt: skip
_CONTAINERS = ("BUILD_LIST", "BUILD_TUPLE", "BUILD_SET", "BUILD_MAP", "BUILD_CONST_KEY_MAP", "LIST_EXTEND",
               "SET_UPDATE", "DICT_UPDATE", "DICT_MERGE", "LIST_APPEND", "SET_ADD", "MAP_ADD",
               "LIST_TO_TUPLE")  # fmt: skip
_RETURNS = ("RETURN_VALUE", "RETURN_CONST")

MODULE_INSTRUCTIONS = frozenset(_BOOKKEEPING + _CALLS + _ARITHMETIC + _CONTAINERS + _RETURNS + (
    "LOAD_CONST", "LOAD_NAME", "STORE_NAME", "IMPORT_NAME", "IMPORT_FROM", "LOAD_BUILD_CLASS", "MAKE_FUNCTION",
    "SET_FUNCTION_ATTRIBUTE"))  # fmt: skip
# A class body also reads attributes and compares, for the F trees of forbids (F.code == "ibi_urban");
# it still stores none (STORE_ATTR), and the dunder rule still applies to every name.
CLASS_INSTRUCTIONS = frozenset(_BOOKKEEPING + _CALLS + _ARITHMETIC + _CONTAINERS + _RETURNS + (
    "LOAD_CONST", "LOAD_NAME", "STORE_NAME", "MAKE_FUNCTION", "SET_FUNCTION_ATTRIBUTE", "MAKE_CELL",
    "COPY_FREE_VARS", "LOAD_CLOSURE", "LOAD_FAST", "LOAD_ATTR", "COMPARE_OP"))  # fmt: skip
FUNCTION_INSTRUCTIONS = frozenset(_BOOKKEEPING + _CALLS + _ARITHMETIC + _CONTAINERS + _RETURNS + (
    "LOAD_CONST", "LOAD_GLOBAL", "LOAD_ATTR", "LOAD_SUPER_ATTR", "STORE_ATTR", "LOAD_FAST", "LOAD_FAST_CHECK",
    "LOAD_FAST_AND_CLEAR", "LOAD_FAST_LOAD_FAST", "STORE_FAST", "STORE_FAST_LOAD_FAST", "STORE_FAST_STORE_FAST",
    "DELETE_FAST", "LOAD_DEREF", "STORE_DEREF", "DELETE_DEREF", "LOAD_CLOSURE", "MAKE_CELL", "COPY_FREE_VARS",
    "MAKE_FUNCTION", "SET_FUNCTION_ATTRIBUTE", "CALL_FUNCTION_EX", "CALL_INTRINSIC_1", "BINARY_SUBSCR",
    "STORE_SUBSCR", "DELETE_SUBSCR", "BINARY_SLICE", "STORE_SLICE", "BUILD_SLICE", "BUILD_STRING",
    "FORMAT_VALUE", "FORMAT_SIMPLE", "FORMAT_WITH_SPEC", "CONVERT_VALUE", "COMPARE_OP", "IS_OP", "CONTAINS_OP",
    "UNPACK_SEQUENCE", "UNPACK_EX", "GET_ITER", "FOR_ITER", "END_FOR", "JUMP_FORWARD", "JUMP_BACKWARD",
    "JUMP_BACKWARD_NO_INTERRUPT", "JUMP_ABSOLUTE", "JUMP_IF_TRUE_OR_POP", "JUMP_IF_FALSE_OR_POP",
    "POP_JUMP_IF_FALSE", "POP_JUMP_IF_TRUE", "POP_JUMP_IF_NONE", "POP_JUMP_IF_NOT_NONE",
    "POP_JUMP_FORWARD_IF_FALSE", "POP_JUMP_FORWARD_IF_TRUE", "POP_JUMP_FORWARD_IF_NONE",
    "POP_JUMP_FORWARD_IF_NOT_NONE", "POP_JUMP_BACKWARD_IF_FALSE", "POP_JUMP_BACKWARD_IF_TRUE",
    "POP_JUMP_BACKWARD_IF_NONE", "POP_JUMP_BACKWARD_IF_NOT_NONE", "LOAD_ASSERTION_ERROR", "RAISE_VARARGS",
    "RERAISE"))  # fmt: skip
# CALL_INTRINSIC_1's harmless uses: unary plus and the tuple of a call's *args.
_INTRINSICS = (5, 6)  # INTRINSIC_UNARY_POSITIVE, INTRINSIC_LIST_TO_TUPLE
_LOADS_LOCAL = ("LOAD_FAST", "LOAD_FAST_CHECK", "LOAD_FAST_LOAD_FAST")


def opnames():
    """Instruction number to name for this interpreter: the written table on 3.13, ``opcode`` elsewhere."""
    if sys.version_info[:2] == (3, 13):
        return OPNAMES_313
    import opcode  # the laptop and CI; the canister never reaches this line

    return dict(enumerate(opcode.opname))


def instructions(code, names):
    """``(name, arg)`` per instruction, EXTENDED_ARG folded in, CACHE entries dropped."""
    out, extended = [], 0
    raw = code.co_code
    for i in range(0, len(raw), 2):
        name = names.get(raw[i], f"OPCODE_{raw[i]}")
        arg = raw[i + 1] | extended
        if name == "EXTENDED_ARG":
            extended = arg << 8
            continue
        extended = 0
        if name != "CACHE":
            out.append((name, arg))
    return out


def strings(const):
    """Every string inside a constant: a string, or the strings of a tuple or frozenset."""
    if isinstance(const, str):
        return [const]
    if isinstance(const, (tuple, frozenset)):
        return [s for item in const for s in strings(item)]
    return []


def inexact(const):
    """Every float or complex number inside a constant: itself, or in a tuple or frozenset."""
    if isinstance(const, (float, complex)):
        return [const]
    if isinstance(const, (tuple, frozenset)):
        return [number for item in const for number in inexact(item)]
    return []


def operator_refusal(name, arg):
    """Why an instruction is refused, if it is `/`, `/=`, `**` or `**=`: BINARY_OP's arguments 11 and 24, 8 and 21 on
    3.11 and later; their own instructions before."""
    if name in ("BINARY_TRUE_DIVIDE", "INPLACE_TRUE_DIVIDE") or (name == "BINARY_OP" and arg in (11, 24)):
        return NO_DIVISION
    if name in ("BINARY_POWER", "INPLACE_POWER") or (name == "BINARY_OP" and arg in (8, 21)):
        return NO_POWER
    return None


def sdk_names():
    import realms.legislation as sdk

    return sdk.__all__


def base_names():
    """The names ``Chapter`` defines, the self API among them. Read when a chapter is checked: chapter.py imports this
    module."""
    from realms.legislation.chapter import Chapter

    return frozenset(vars(Chapter))


class Check:
    """One chapter's compiled code against the subset. ``run`` raises ``Refused`` at the first problem."""

    def __init__(self, text, file):
        self.text, self.file = text, file
        self.names = opnames()
        self.imported = set()  # the names the module's import statements bind
        self.taken = frozenset()  # the names a store on self may not use: the class's own members and the base class's

    def refuse(self, reason, token=None):
        line = None
        if token:
            for number, row in enumerate(self.text.splitlines(), 1):
                if token in row:
                    line = number
                    break
        raise Refused(reason, line, self.file)

    def run(self, code):
        self.module(code)
        return self.imported

    # ── scopes ──

    def module(self, code):
        self.common(code, "module", MODULE_INSTRUCTIONS)
        ops = instructions(code, self.names)
        classes = sum(1 for name, _ in ops if name == "LOAD_BUILD_CLASS")
        if classes != 1:
            self.refuse("one class per file, extending above(...)", "class ")
        in_class = False
        for i, (name, arg) in enumerate(ops):
            if name == "LOAD_BUILD_CLASS":
                in_class = True
            elif name == "STORE_NAME":
                stored = code.co_names[arg]
                if stored == "__doc__" and (i == 0 or ops[i - 1][0] != "LOAD_CONST"):
                    self.refuse(
                        "__doc__ is the docstring, not an assignment", "__doc__"
                    )
                if stored != "__doc__" and stored.startswith("__"):
                    self.refuse(f"{stored}: no dunder names in a chapter", stored)
                in_class = False
            elif name == "MAKE_FUNCTION" and not in_class:
                self.refuse(
                    "nothing else at module level except imports from the SDK, constants and the one class",
                    "def ",
                )
            elif name == "IMPORT_NAME":
                self.import_statement(code, ops, i)
            elif (
                name == "IMPORT_FROM"
                and i + 1 < len(ops)
                and ops[i + 1][0] == "STORE_NAME"
            ):
                self.imported.add(code.co_names[ops[i + 1][1]])
        for const in code.co_consts:
            if type(const) is type(code):
                if const.co_name[:1].isupper():
                    self.class_body(const)
                else:
                    self.function(const)

    def import_statement(self, code, ops, i):
        """``from realms.legislation import <names in __all__>`` or ``from ggg import ...``, nothing else."""
        module = code.co_names[ops[i][1]]
        fromlist = (
            code.co_consts[ops[i - 1][1]]
            if i >= 1 and ops[i - 1][0] == "LOAD_CONST"
            else None
        )
        if module not in (SDK, GGG) or not isinstance(fromlist, tuple) or not fromlist:
            self.refuse(
                f"import {module}: a chapter imports only from {SDK} and {GGG}, as from ... import ...",
                "import",
            )
        if module == SDK:
            unknown = [n for n in fromlist if n not in sdk_names()]
            if unknown:
                self.refuse(
                    f"from {SDK} import {', '.join(unknown)}: a chapter imports only {', '.join(sdk_names())}",
                    "import",
                )

    def no_defaults(self, ops):
        """A default argument is one object for every call of the method, and the chapter's class lives as long as
        the codex runs: none, on a method, a nested function or a lambda."""
        for name, arg in ops:
            if (name == "MAKE_FUNCTION" and arg and arg & 0x03) or (name == "SET_FUNCTION_ATTRIBUTE" and arg in (1, 2)):
                self.refuse("a default argument is state shared by every call: pass the value instead", "=")

    def class_body(self, code):
        self.common(code, "class body", CLASS_INSTRUCTIONS)
        ops = instructions(code, self.names)
        self.no_defaults(ops)
        self.taken = base_names() | {code.co_names[arg] for name, arg in ops if name == "STORE_NAME"}
        for i, (name, arg) in enumerate(ops):
            if name in ("LOAD_ATTR", "LOAD_METHOD"):
                # The dunder names a class body may hold (CLASS_BODY) are the ones CPython itself
                # writes, as names; read as attributes they reach another class (`__annotations__`).
                attr = code.co_names[self.attribute_index(name, arg)]
                if attr.startswith("_"):
                    self.refuse(
                        f".{attr}: no private or dunder attribute is read in a class body",
                        attr,
                    )
            if name == "STORE_NAME":
                stored = code.co_names[arg]
                if stored == "__doc__" and (i == 0 or ops[i - 1][0] != "LOAD_CONST"):
                    self.refuse(
                        "__doc__ is the docstring, not an assignment", "__doc__"
                    )
        for const in code.co_consts:
            if type(const) is type(code):
                self.function(const, event_of(const))

    def function(self, code, event=None, nested=False):
        """A method, or a function nested in one (``nested``); ``event`` names the event the enclosing hook was given."""
        self.common(code, "method", FUNCTION_INSTRUCTIONS)
        ops = instructions(code, self.names)
        self.no_defaults(ops)
        self.bound_once(code, ops, {"self", event} - {None}, nested)
        for i, (name, arg) in enumerate(ops):
            if name == "STORE_ATTR":
                attr, target = code.co_names[arg], self.stored_on(code, ops, i)
                if target not in {"self", event} - {None}:
                    self.refuse(
                        f".{attr} = ...: a chapter sets attributes on self, or on the event a hook was given, never on anything else",
                        f".{attr}",
                    )
                if target == "self" and attr in self.taken:
                    self.refuse(
                        f".{attr} = ...: {attr} is a member of the chapter or of the self API: a store on self takes a new name",
                        f".{attr}",
                    )
            if name == "CALL_INTRINSIC_1" and arg not in _INTRINSICS:
                self.refuse("an instruction outside the readable subset", code.co_name)
        for const in code.co_consts:
            if type(const) is type(code):
                self.function(const, event, nested=True)

    def bound_once(self, code, ops, names, nested):
        """``self`` and the event a hook was given are bound once, as the method's own parameters, and never again:
        a store on either would otherwise be a store on whatever the name was rebound to. So no assignment to them
        (plain, augmented, a walrus, a loop or comprehension target, nonlocal), no del, and no parameter of a
        nested function or lambda that reuses the names."""
        if nested:
            count = code.co_argcount + code.co_kwonlyargcount + bool(code.co_flags & 4) + bool(code.co_flags & 8)
            for param in code.co_varnames[:count]:
                if param in names:
                    self.refuse(f"{param} is bound once, as the method's parameter: a nested function has no parameter of that name", param)
        for name, arg in ops:
            rebound = ()
            if name in ("STORE_FAST", "STORE_FAST_LOAD_FAST", "DELETE_FAST"):
                rebound = (code.co_varnames[arg >> 4 if name == "STORE_FAST_LOAD_FAST" else arg],)
            elif name == "STORE_FAST_STORE_FAST":
                rebound = (code.co_varnames[arg >> 4], code.co_varnames[arg & 15])
            elif name in ("STORE_DEREF", "DELETE_DEREF"):
                rebound = (self.deref_name(code, arg),)
            for variable in rebound:
                if variable in names:
                    self.refuse(f"{variable} is bound once, as the method's parameter, and never rebound or deleted", variable)

    @staticmethod
    def attribute_index(name, arg):
        """The ``co_names`` index a LOAD_ATTR carries: from 3.12 its low bit says "method call"."""
        if name == "LOAD_ATTR" and sys.version_info >= (3, 12):
            return arg >> 1
        return arg

    def stored_on(self, code, ops, i):
        """The variable the object an attribute is stored on was just loaded from, or None. A chapter may store on
        ``self`` and on the event a hook was given only: the two objects a call gets fresh, and so the only ones a store
        cannot turn into state kept between calls. Anything else, a parameter included, may outlive the call: an SDK
        function, the calendar, a view, another chapter."""
        if i == 0:
            return None
        name, arg = ops[i - 1]
        if name in ("LOAD_FAST", "LOAD_FAST_CHECK"):
            return code.co_varnames[arg]
        if name == "LOAD_FAST_LOAD_FAST":
            return code.co_varnames[arg & 15]
        if name == "LOAD_DEREF":  # a parameter a nested function also reads is a cell on 3.10
            return self.deref_name(code, arg)
        return None

    @staticmethod
    def deref_name(code, index):
        """The variable a LOAD_DEREF index names: cells then frees on 3.10, the whole locals-plus on 3.11+."""
        if sys.version_info[:2] <= (3, 10):
            names = code.co_cellvars + code.co_freevars
        else:
            names = (
                code.co_varnames
                + tuple(c for c in code.co_cellvars if c not in code.co_varnames)
                + code.co_freevars
            )
        return names[index] if index < len(names) else None

    # ── every scope ──

    def common(self, code, scope, allowed):
        # Strings first: a dunder spelled in a format template or a getattr argument is the escape to catch.
        for const in code.co_consts:
            for s in strings(const):
                if "__" in s:
                    self.refuse(
                        f"{s!r}: no dunder names in a chapter, in strings either", s
                    )
                if s in FRAME_ATTRIBUTES or s == "metaclass":
                    self.refuse(f"{s!r}: not allowed in a chapter", s)
                if s in FLOAT_NAMES:  # a keyword's name, or a name looked up as a string
                    self.refuse(f"{s}: {NO_FLOAT}", s)
            for number in inexact(const):
                self.refuse(f"{number!r}: {NO_FLOAT}", repr(number))
        for name in code.co_names:
            if name in FRAME_ATTRIBUTES:
                self.refuse(
                    f"{name}: no frame or generator attributes in a chapter", name
                )
            if name in FORMAT_METHODS:
                self.refuse(
                    f"{name}: write an f-string, str.format reads attributes named in the template",
                    name,
                )
            if (
                name.startswith("__")
                and not (scope == "class body" and name in CLASS_BODY)
                and not (scope == "module" and name == "__doc__")
            ):
                self.refuse(f"{name}: no dunder names in a chapter", name)
        # Dunder names are refused where the code reads them (co_names) and in strings, not among the variables it binds
        # (co_varnames, co_cellvars, co_freevars): a local is only a name and reaches nothing. Refusing them there would
        # also refuse super(): a method that calls it holds the implicit ``__class__`` cell in co_freevars. That cell is
        # the class that defines the method, which type(self) and above() give already. (subset_ast refuses a dunder
        # variable the author writes; super() writes none.)
        # The names the code binds as well as those it reads (co_names holds the global and attribute names only).
        for name in (code.co_name, *code.co_names, *code.co_varnames, *code.co_cellvars, *code.co_freevars):
            if name in FLOAT_NAMES:
                self.refuse(f"{name}: {NO_FLOAT}", name)
        if code.co_flags & GENERATOR_FLAGS:
            self.refuse(
                f"{code.co_name}: no generators, coroutines or async code in a chapter",
                code.co_name,
            )
        for name, arg in instructions(code, self.names):
            if name not in allowed:
                self.refuse(
                    f"{self.what(name, arg)} is not part of the readable subset in a {scope}",
                    code.co_name,
                )
            reason = operator_refusal(name, arg)
            if reason:
                self.refuse(reason, code.co_name)
        for const in code.co_consts:
            if type(const) is type(code) and const.co_flags & GENERATOR_FLAGS:
                self.refuse(
                    f"{const.co_name}: no generators, coroutines or async code in a chapter",
                    const.co_name,
                )

    @staticmethod
    def what(name, arg=0):
        """A word for the refused instruction, for people."""
        if name == "IMPORT_STAR" or (name == "CALL_INTRINSIC_1" and arg == 2):
            return "an import"  # import *: INTRINSIC_IMPORT_STAR on 3.12+, IMPORT_STAR on 3.10
        if name.startswith("MATCH_") or name == "GET_LEN":
            return "a match statement"
        if name in (
            "YIELD_VALUE",
            "RETURN_GENERATOR",
            "GET_YIELD_FROM_ITER",
            "YIELD_FROM",
            "GEN_START",
        ):
            return "a generator"
        if name in (
            "PUSH_EXC_INFO",
            "POP_EXCEPT",
            "CHECK_EXC_MATCH",
            "SETUP_FINALLY",
            "POP_BLOCK",
            "JUMP_IF_NOT_EXC_MATCH",
            "CHECK_EG_MATCH",
        ):
            return "a try statement"
        if name in ("BEFORE_WITH", "WITH_EXCEPT_START", "SETUP_WITH"):
            return "a with statement"
        if name in ("DELETE_NAME", "DELETE_GLOBAL", "STORE_GLOBAL"):
            return "a del or global statement at this level"
        if name == "DELETE_ATTR":
            return "deleting an attribute"
        if name == "STORE_ATTR":
            return "an attribute assignment"
        if name in ("LOAD_ATTR", "LOAD_METHOD"):
            return "an attribute access"
        if name in ("IMPORT_NAME", "IMPORT_FROM", "IMPORT_STAR"):
            return "an import"
        if name == "LOAD_BUILD_CLASS":
            return "a nested class"
        return f"an instruction ({name})"


def event_of(code):
    """The name of the event a hook's code object is given: its second parameter, for an ``on_<op>`` method."""
    if code.co_name.startswith("on_") and code.co_argcount >= 2:
        return code.co_varnames[1]
    return None


def check(code, text, file):
    """Refuse ``code`` (the compiled chapter ``text`` of ``file``) unless it stays in the readable subset.
    Returns the names its import statements bind."""
    return Check(text, file).run(code)
