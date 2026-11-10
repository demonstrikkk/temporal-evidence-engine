"""A publication date may never become an event date.

`event_date` is when the fact changed. `published_date` is when the page was
published. They coincide often enough to be tempting and differ often enough to
make `changed` wrong — an article published on 9 November about a price change
on 2 November would mark every answer generated on 3 November as stale.

The rule is therefore structural: no module under `app/verify` or
`app/research` may assign `event_date` from anything named `published*`.
Text that states its own date is the only source, which is what `temporal.py`
implements and what its runtime tests cover.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "app"
PACKAGES = ("verify", "research")


def _mentions_published(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and "publish" in child.id.lower():
            return True
        if isinstance(child, ast.Attribute) and "publish" in child.attr.lower():
            return True
        if isinstance(child, ast.Constant) and isinstance(child.value, str) \
                and "publish" in child.value.lower():
            return True
    return False


def _offenders(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if (isinstance(key, ast.Constant) and key.value == "event_date"
                        and _mentions_published(value)):
                    found.append((node.lineno, "dict literal"))
            continue
        targets: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            name = ""
            if isinstance(target, ast.Name):
                name = target.id
            elif isinstance(target, ast.Attribute):
                name = target.attr
            elif isinstance(target, ast.Subscript) and isinstance(
                    target.slice, ast.Constant):
                name = str(target.slice.value)
            if name == "event_date" and _mentions_published(
                    getattr(node, "value", ast.Constant(None))):
                found.append((node.lineno, "assignment"))
    return found


def test_no_module_derives_an_event_date_from_a_publication_date():
    offenders: list[str] = []
    for package in PACKAGES:
        for path in sorted((ROOT / package).rglob("*.py")):
            for lineno, kind in _offenders(path):
                offenders.append(f"{path.relative_to(ROOT).as_posix()}:{lineno} "
                                 f"({kind})")
    assert not offenders, (
        "these write an event date from a publication date, which makes "
        "`changed` depend on when an article was written rather than when the "
        "fact changed:\n  " + "\n  ".join(offenders))


def test_the_guard_catches_the_mistake(tmp_path):
    sample = tmp_path / "bad.py"
    sample.write_text(
        "def a(row):\n"
        "    return {'event_date': row['published_date']}\n"
        "def b(row):\n"
        "    event_date = row.published_date\n"
        "def c(row):\n"
        "    return {'event_date': row['effective_date']}\n",
        encoding="utf-8")
    assert len(_offenders(sample)) == 2


def test_the_missing_date_sentinel_is_explicit():
    from app.verify.temporal import MISSING

    assert MISSING.value is None
    assert MISSING.status == "missing"
    assert "does not state" in MISSING.detail
