"""AST-based import boundary checks for the dark_factory package (ADR-015 p.3).

Rule A: core modules (everything outside ``dark_factory.adapters``) must not import
``dark_factory.adapters`` or its subpackages.
Rule B: ``dark_factory.adapters`` may import from the core only ``dark_factory.ports``
(plus its own subpackages).
"""

import ast
from dataclasses import dataclass
from pathlib import Path

PACKAGE = "dark_factory"
ADAPTERS = f"{PACKAGE}.adapters"
PORTS = f"{PACKAGE}.ports"
SRC_DIR = Path(__file__).resolve().parents[1] / "src" / PACKAGE


@dataclass(frozen=True)
class Violation:
    """A single boundary violation found in module source."""

    module: str
    lineno: int
    imported: str
    rule: str


def _matches(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(f"{prefix}.")


def _module_and_package(path: Path, root: Path) -> tuple[str, str]:
    """Return dotted (module, package) names for a python file under ``root``."""
    rel = path.relative_to(root)
    parts = [PACKAGE, *rel.with_suffix("").parts]
    is_package = rel.name == "__init__.py"
    if is_package:
        parts = parts[:-1]
    module = ".".join(parts)
    package = module if is_package else module.rpartition(".")[0]
    return module, package


def _resolve_relative(package: str, level: int, base: str | None) -> str:
    """Resolve a relative import to an absolute dotted module path."""
    parts = package.split(".") if package else []
    drop = level - 1
    if drop:
        if drop > len(parts):
            return ""
        parts = parts[: len(parts) - drop]
    prefix = ".".join(parts)
    if base:
        return f"{prefix}.{base}" if prefix else base
    return prefix


def _imported_names(node: ast.AST, package: str) -> list[tuple[int, str]]:
    """Return (lineno, absolute dotted name) pairs imported by one AST node."""
    if isinstance(node, ast.Import):
        return [(node.lineno, alias.name) for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        if node.level == 0:
            base = node.module or ""
        else:
            base = _resolve_relative(package, node.level, node.module)
        if not base:
            return []
        if base == PACKAGE:
            # "from dark_factory import adapters" really imports dark_factory.adapters.
            return [(node.lineno, f"{PACKAGE}.{alias.name}") for alias in node.names]
        return [(node.lineno, base)]
    return []


def _violated_rule(package: str, imported: str) -> str | None:
    """Return "A" or "B" if ``package`` importing ``imported`` breaks a boundary rule."""
    if not _matches(package, ADAPTERS):
        return "A" if _matches(imported, ADAPTERS) else None
    if not _matches(imported, PACKAGE):
        return None
    if _matches(imported, PORTS) or _matches(imported, ADAPTERS):
        return None
    return "B"


def check_source(module: str, package: str, source: str) -> list[Violation]:
    """Check one module's source against the boundary rules."""
    tree = ast.parse(source)
    violations: list[Violation] = []
    for node in ast.walk(tree):
        for lineno, imported in _imported_names(node, package):
            rule = _violated_rule(package, imported)
            if rule is not None:
                violations.append(Violation(module, lineno, imported, rule))
    return violations


def scan_source_tree(root: Path) -> list[Violation]:
    """Check every ``*.py`` file under ``root`` against the boundary rules."""
    violations: list[Violation] = []
    for path in sorted(root.rglob("*.py")):
        module, package = _module_and_package(path, root)
        source = path.read_text(encoding="utf-8")
        violations.extend(check_source(module, package, source))
    return violations


def test_source_tree_respects_boundaries() -> None:
    assert scan_source_tree(SRC_DIR) == []


def test_rule_a_flags_core_importing_adapters() -> None:
    source = "import dark_factory.adapters\nfrom dark_factory import adapters as adap\n"
    violations = check_source("dark_factory.changes", "dark_factory.changes", source)
    assert [v.rule for v in violations] == ["A", "A"]


def test_rule_a_allows_external_and_core_imports() -> None:
    source = "import json\nfrom dark_factory.ports import Agent\nfrom . import sibling\n"
    violations = check_source("dark_factory.changes", "dark_factory.changes", source)
    assert violations == []


def test_rule_b_flags_adapters_importing_core() -> None:
    source = (
        "from dark_factory.orchestration import runner\n"
        "import dark_factory\n"
        "from dark_factory import helper\n"
    )
    violations = check_source("dark_factory.adapters.gitlab", "dark_factory.adapters", source)
    assert [v.rule for v in violations] == ["B", "B", "B"]


def test_rule_b_allows_ports_and_own_subpackages() -> None:
    source = (
        "from dark_factory.ports import WorkflowEngine\n"
        "from . import helper\n"
        "import dark_factory.adapters.gitlab\n"
        "from .. import helper\n"
        "from ...ports import Agent\n"
    )
    # Module and package agree: gitlab is a subpackage of dark_factory.adapters.
    violations = check_source(
        "dark_factory.adapters.gitlab", "dark_factory.adapters.gitlab", source
    )
    assert violations == []
