"""Static import scanner for codex packages (issue #265, Workstream A).

A codex may call the realm only through the public ``ggg`` facade. Reaching
into ``core.*`` or a private ``ggg`` submodule couples legislation to host
internals that can change without notice, and — once codices run sandboxed
(issue #265, Workstream C) — such imports fail outright. This scanner finds
those imports at install time by reading each ``.py`` file's import statements.

Enforcement policy (issue #265):

* **Warn mode** (Phase 1, current): violations are logged, never rejected.
  Legacy codices (no ``ggg_api_version`` in the manifest) always run in warn
  mode so existing packages keep installing unchanged.
* **Hard-fail mode** (later phase): the same violations become an install
  error. A codex that declares ``ggg_api_version`` opts in to enforcement.

The scanner is intentionally conservative: it flags only *imports*, never
executes codex code, and ignores relative imports (a codex's own sibling
modules).

Parsed with plain string operations rather than ``ast``: the frozen basilisk
stdlib in the canister ships only stub ``ast`` and ``re`` modules (importing
them succeeds but they expose no attributes), so ``ast.parse`` raised
``AttributeError`` and failed every codex install. String literals and
comments are stripped first so text that merely mentions an import is not
mistaken for one.
"""

from typing import Dict, List, NamedTuple, Optional, Tuple

from ic_python_logging import get_logger

logger = get_logger("core.codex_scan")

# Top-level packages a codex must never import from directly.
_FORBIDDEN_ROOTS = ("core",)

# Only the public ``ggg`` facade is allowed: ``import ggg`` / ``from ggg import
# X``. A dotted path (``ggg.system.user_profile``) reaches private internals.
_GGG_ROOT = "ggg"

# A *sandboxed extension* is stricter than a codex: a subinterpreter has no
# host modules at all, so importing any of these is not a style problem but a
# guaranteed failure at call time. The bridge is reached through ``ggg_sdk``.
_SANDBOX_FORBIDDEN_ROOTS = ("core", "ggg", "basilisk", "_cdk")

_OPENERS = "([{"
_CLOSERS = ")]}"

# Scan policies.
CODEX_POLICY = "codex"          # public ``ggg`` allowed, ``core.*`` is not
SANDBOX_POLICY = "sandbox"      # no host modules whatsoever


class Violation(NamedTuple):
    filename: str
    lineno: int
    statement: str
    reason: str


def _module_forbidden(module: str, policy: str = CODEX_POLICY) -> str:
    """Return a human reason if importing *module* is disallowed, else ''."""
    if not module:
        return ""
    root = module.split(".")[0]

    if policy == SANDBOX_POLICY:
        if root in _SANDBOX_FORBIDDEN_ROOTS:
            return (
                f"imports host module '{module}', which does not exist inside a "
                f"subinterpreter; use 'from ggg_sdk import ctx' and the "
                f"capability bridge instead"
            )
        return ""

    if root in _FORBIDDEN_ROOTS:
        return (
            f"imports host-internal '{module}'; call the realm through the "
            f"public 'ggg' API instead"
        )
    if root == _GGG_ROOT and "." in module:
        return (
            f"imports private '{module}'; use the public 'ggg' facade "
            f"(e.g. 'from ggg import ...') instead"
        )
    return ""


def _strip_strings_and_comments(source: str) -> str:
    """Blank out string literals and comments, preserving line numbering.

    Keeps every newline that was inside a literal or comment so a violation's
    reported line still matches the original file.
    """
    out: List[str] = []
    delim: Optional[str] = None
    i = 0
    n = len(source)
    while i < n:
        ch = source[i]
        if delim is None:
            if ch == "#":
                while i < n and source[i] != "\n":
                    i += 1
                continue
            if ch == '"' or ch == "'":
                delim = ch * 3 if source.startswith(ch * 3, i) else ch
                i += len(delim)
                continue
            out.append(ch)
            i += 1
            continue
        # Inside a literal: a backslash escapes the next character (including a
        # quote), which is what stops the literal from ending early.
        if ch == "\\" and i + 1 < n:
            if source[i + 1] == "\n":
                out.append("\n")
            i += 2
            continue
        if source.startswith(delim, i):
            i += len(delim)
            delim = None
            continue
        if ch == "\n":
            out.append("\n")
        i += 1
    return "".join(out)


def _first_word(text: str) -> str:
    """Return the leading identifier of *text* (empty if it starts otherwise)."""
    stripped = text.lstrip()
    i = 0
    while i < len(stripped) and (stripped[i].isalnum() or stripped[i] == "_"):
        i += 1
    return stripped[:i]


def _paren_delta(text: str) -> int:
    depth = 0
    for ch in text:
        if ch in _OPENERS:
            depth += 1
        elif ch in _CLOSERS:
            depth -= 1
    return depth


def _join_continuations(lines: List[str], start: int) -> Tuple[str, int]:
    """Join a statement's bracket and backslash continuations.

    Returns the single-line statement text and the index of its last line.
    """
    stmt = lines[start]
    depth = _paren_delta(stmt)
    i = start
    while i + 1 < len(lines) and (depth > 0 or stmt.rstrip().endswith("\\")):
        i += 1
        stmt = stmt.rstrip()
        if stmt.endswith("\\"):
            stmt = stmt[:-1]
        stmt = f"{stmt} {lines[i].strip()}"
        depth += _paren_delta(lines[i])
    return stmt, i


def _imported_names(raw: str) -> List[str]:
    """Split an import clause into bare module/attribute names, dropping aliases."""
    clause = raw.strip()
    if clause.startswith("("):
        clause = clause[1:]
    if clause.endswith(")"):
        clause = clause[:-1]
    names = []
    for piece in clause.split(","):
        parts = piece.split()
        if parts:
            names.append(parts[0])
    return names


def _split_from(statement: str) -> Optional[Tuple[str, str]]:
    """Split ``from <module> import <names>`` into its module and names."""
    rest = statement.lstrip()[len("from") :].lstrip()
    i = 0
    while i < len(rest) and (rest[i].isalnum() or rest[i] in "._"):
        i += 1
    module = rest[:i]
    after = rest[i:].lstrip()
    if not after.startswith("import"):
        return None
    return module, after[len("import") :].strip()


def _scan_statement(
    filename: str, lineno: int, statement: str, violations: List[Violation],
    policy: str = CODEX_POLICY,
) -> None:
    text = statement.strip()
    keyword = _first_word(text)

    if keyword == "import":
        for name in _imported_names(text.lstrip()[len("import") :]):
            reason = _module_forbidden(name, policy)
            if reason:
                violations.append(Violation(filename, lineno, f"import {name}", reason))
        return

    if keyword == "from":
        parsed = _split_from(text)
        if not parsed:
            return
        module, raw_names = parsed
        # Relative imports (``from . import x``) are a codex's own siblings.
        if module.startswith("."):
            return
        reason = _module_forbidden(module, policy)
        if reason:
            names = ", ".join(_imported_names(raw_names))
            violations.append(
                Violation(filename, lineno, f"from {module} import {names}", reason)
            )


def scan_source(
    filename: str, source: str, policy: str = CODEX_POLICY
) -> List[Violation]:
    """Read one source string and return its import-policy violations."""
    violations: List[Violation] = []
    if not source:
        return violations

    lines = _strip_strings_and_comments(source).split("\n")
    i = 0
    while i < len(lines):
        if _first_word(lines[i]) not in ("import", "from"):
            i += 1
            continue
        statement, last = _join_continuations(lines, i)
        for part in statement.split(";"):
            _scan_statement(filename, i + 1, part, violations, policy)
        i = last + 1
    return violations


def scan_codex_files(
    files: Dict[str, str], policy: str = CODEX_POLICY
) -> List[Violation]:
    """Scan every ``.py`` file in a package's ``{filename: content}`` map."""
    violations: List[Violation] = []
    for filename, content in (files or {}).items():
        if not isinstance(filename, str) or not filename.endswith(".py"):
            continue
        if not isinstance(content, str):
            continue
        violations.extend(scan_source(filename, content, policy))
    return violations


def check_extension_imports(
    ext_id: str, files: Dict[str, str], enforce: bool
) -> str:
    """Scan an extension destined for the sandbox and report host imports.

    Same warn-then-enforce shape as :func:`check_codex_imports`, but against
    the stricter sandbox policy: inside a subinterpreter there is no ``ggg``
    either, so an extension that still imports it cannot run there at all.

    Warn mode exists so the scanner can be pointed at the whole extension
    catalogue and report the distance left to travel without blocking anyone.
    """
    violations = scan_codex_files(files, SANDBOX_POLICY)
    for v in violations:
        logger.warning(
            f"extension_scan[{ext_id}]: {v.filename}:{v.lineno}: "
            f"{v.statement} — {v.reason}"
        )
    if not violations:
        return ""
    if enforce:
        first = violations[0]
        return (
            f"Extension '{ext_id}' cannot run sandboxed: "
            f"{first.filename}:{first.lineno}: {first.statement} — {first.reason} "
            f"({len(violations)} violation(s) total)."
        )
    return ""


def check_codex_imports(ext_id: str, files: Dict[str, str], enforce: bool) -> str:
    """Scan *files* and report violations.

    Logs every violation. When ``enforce`` is True and any violation exists,
    returns an error string (the install gate turns this into a hard reject);
    otherwise returns "" (warn mode).
    """
    violations = scan_codex_files(files)
    for v in violations:
        logger.warning(
            f"codex_scan[{ext_id}]: {v.filename}:{v.lineno}: {v.statement} — {v.reason}"
        )
    if not violations:
        return ""
    if enforce:
        first = violations[0]
        return (
            f"Codex '{ext_id}' violates the GGG import policy: "
            f"{first.filename}:{first.lineno}: {first.statement} — {first.reason} "
            f"({len(violations)} violation(s) total). Codices may import only the "
            f"public 'ggg' API."
        )
    logger.warning(
        f"codex_scan[{ext_id}]: {len(violations)} import-policy warning(s); these "
        f"become hard errors once the codex declares 'ggg_api_version' or sandbox "
        f"enforcement is enabled (issue #265)"
    )
    return ""
