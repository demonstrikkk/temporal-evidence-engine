"""An edition is data the core reads, never code the core imports.

That sentence is only true if something enforces it. This file is that
something, and it works the same way `test_name_binding.py` does: parse the
source, never import it, so a violation is reported instead of executed.

Two directions are checked:

* `backend/app/**` may not name an edition package. If core reaches for a
  vertical, an upgrade to one breaks the other.
* `editions/**` may import exactly one thing from the core: `app.domain.api`.
  Everything else under `app` is an internal that is free to move, and an
  edition that imports it has silently become part of the engine.

The scanner is tested against synthetic sources, so a future refactor of the
scanner cannot make it vacuously pass.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "backend" / "app"
EDITIONS = ROOT / "editions"

CORE_ALLOWED = "app.domain.api"


def _modules(path: Path) -> list[tuple[str, int]]:
    """Every module a source file imports, with the line it was written on."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                found.append((node.module, node.lineno))
            for alias in node.names:
                if node.module:
                    found.append((f"{node.module}.{alias.name}", node.lineno))
    return found


def _violations(source: str, *, direction: str) -> list[str]:
    """The rule, as a pure function of source text. Shared by both directions."""
    tree = ast.parse(source)
    problems: list[str] = []
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module] + [f"{node.module}.{alias.name}"
                                     for alias in node.names]
        for name in names:
            if direction == "core":
                if name == "editions" or name.startswith(("editions.", "datagoblin_")):
                    problems.append(f"core imports an edition: {name}")
            else:
                if name == "app" or name.startswith("app."):
                    if not (name == CORE_ALLOWED or name.startswith(CORE_ALLOWED + ".")):
                        problems.append(f"edition imports core internals: {name}")
    return problems


def test_core_never_imports_an_edition() -> None:
    bad: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        for module, line in _modules(path):
            if module == "editions" or module.startswith(("editions.", "datagoblin_")):
                bad.append(f"{path.relative_to(ROOT)}:{line} imports {module}")
    assert not bad, "the core reached for a domain edition:\n" + "\n".join(bad)


def test_editions_only_import_the_published_surface() -> None:
    if not EDITIONS.is_dir():
        return
    bad: list[str] = []
    for path in sorted(EDITIONS.rglob("*.py")):
        for module, line in _modules(path):
            if module == "app" or module.startswith("app."):
                if not (module == CORE_ALLOWED
                        or module.startswith(CORE_ALLOWED + ".")):
                    bad.append(f"{path.relative_to(ROOT)}:{line} imports {module}")
    assert not bad, ("an edition imported core internals instead of "
                     f"{CORE_ALLOWED}:\n" + "\n".join(bad))


def _string_literals(path: Path) -> list[tuple[int, str]]:
    """Every string literal in a file that is not a docstring.

    Docstrings are excluded on purpose: a comment explaining *why* the core
    stays domain-agnostic is allowed to name the words it refuses to bake in.
    What is not allowed is the words themselves, in code, deciding something.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))
    return [(node.lineno, node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in docstrings]


def test_the_core_carries_no_editions_vocabulary() -> None:
    """The core must not know what a legal word means.

    A shared service that hardcodes one vertical's words is that vertical
    wearing the engine's clothes. `corpus_import` once decided that a boolean
    was supported when the quote contained "eligible", "entitled" or "free
    legal aid" -- legal vocabulary, in the core, deciding what every other
    edition's booleans mean. The words now travel with the corpus file, so
    the core only checks that a declared term is present.

    Only string literals are checked. A docstring may name the word it
    refuses to bake in; a literal may not.
    """
    vocabulary = (
        "free legal aid",
        "legal aid",
        "eligible",
        "entitled",
        "high court",
        "supreme court",
        "indiacode",
        "delhi",
        "district court",
    )
    bad: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        for line, literal in _string_literals(path):
            low = literal.lower()
            for word in vocabulary:
                if word in low:
                    bad.append(f"{path.relative_to(ROOT)}:{line} has {word!r} "
                               f"in a string literal")
    assert not bad, ("the core carries an edition's vocabulary; it should "
                     "come from the edition as data:\n" + "\n".join(bad))


def test_the_scanner_notices_a_core_import() -> None:
    assert _violations("import editions.legal", direction="core")
    assert _violations("from datagoblin_legal import profile", direction="core")
    assert not _violations("from app.domain.api import DomainProfile", direction="core")


def test_the_scanner_notices_an_edition_reaching_into_services() -> None:
    assert _violations("from app.services import planner", direction="edition")
    assert _violations("import app.core.config", direction="edition")
    assert not _violations(
        "from app.domain.api import DomainProfile, check_profile",
        direction="edition")
    assert not _violations("import json", direction="edition")
