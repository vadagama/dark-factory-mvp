"""AST-based import boundary checks for the dark_factory package (ADR-015 p.3).

Rule A: core modules (everything outside ``dark_factory.adapters``) must not import
``dark_factory.adapters`` or its subpackages — except ``dark_factory.runtime``, the
named composition root ADR-024 p.5 carves out: it is the one layer allowed to bind
core to adapters, and the allowlist is exactly that name, not a weakened rule.
Rule B: ``dark_factory.adapters`` may import from the core only ``dark_factory.ports``
(plus its own subpackages) — so no adapter may import the runtime either.
Rule C: core modules must not import external-system SDKs; providers are reached
only through adapters (explicit denylist; infrastructure drivers such as
sqlalchemy/psycopg/pydantic are deliberately not on it).
"""

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Final

PACKAGE = "dark_factory"
ADAPTERS = f"{PACKAGE}.adapters"
PORTS = f"{PACKAGE}.ports"
RUNTIME = f"{PACKAGE}.runtime"
SRC_DIR = Path(__file__).resolve().parents[1] / "src" / PACKAGE

EXTERNAL_SDK_MODULES: Final[tuple[str, ...]] = (
    "pydantic_ai",  # agent harness SDK (T-010)
    "githubkit",  # GitHub SDK
    "gidgethub",  # GitHub SDK
    "pygithub",  # GitHub SDK
    "gitlab",  # GitLab SDK (python-gitlab)
    "plane",  # Plane tracker SDK
    "kubernetes",  # Kubernetes client
    "opentelemetry",  # OpenTelemetry SDK (T-060)
    "boto3",  # S3-compatible object storage
    "minio",  # MinIO object storage
)


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


def _matches_external_sdk(name: str) -> bool:
    """True when ``name`` is (inside) one of the denylisted external SDK modules."""
    return any(_matches(name, sdk) for sdk in EXTERNAL_SDK_MODULES)


def _violated_rule(package: str, imported: str) -> str | None:
    """Return "A", "B" or "C" if ``package`` importing ``imported`` breaks a rule."""
    if not _matches(package, ADAPTERS):
        if _matches(imported, ADAPTERS):
            # ADR-024 p.5: the composition root is the one named exception; any
            # other core module importing adapters stays a violation.
            if _matches(package, RUNTIME):
                return None
            return "A"
        if _matches_external_sdk(imported):
            return "C"
        return None
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


def test_rule_a_allows_the_runtime_composition_root() -> None:
    # ADR-024 p.5: dark_factory.runtime is the one core layer allowed to bind
    # core to adapters, and the allowlist covers its subpackages too.
    source = (
        "from dark_factory.adapters.harness import PydanticAIHarness\n"
        "import dark_factory.adapters\n"
        "from dark_factory.adapters.scm.github import GitHubAdapter\n"
    )
    assert check_source("dark_factory.runtime", "dark_factory.runtime", source) == []
    assert check_source("dark_factory.runtime.composition", "dark_factory.runtime", source) == []


def test_rule_a_still_flags_core_neighbours_of_runtime() -> None:
    # Only the named layer is exempt: a module that merely looks like it (a
    # sibling of the package, or a different name) keeps the old rule.
    source = "from dark_factory.adapters.harness import PydanticAIHarness\n"
    assert [
        v.rule for v in check_source("dark_factory.runtimes", "dark_factory.runtimes", source)
    ] == ["A"]
    assert [v.rule for v in check_source("dark_factory.cli", "dark_factory.cli", source)] == ["A"]


def test_rule_b_flags_adapters_importing_runtime() -> None:
    # ADR-024 p.5: there is no reverse edge — an adapter importing the
    # composition root (or any core beyond ports) is still a violation.
    source = "from dark_factory.runtime import build_runtime\n"
    assert [
        v.rule
        for v in check_source("dark_factory.adapters.github", "dark_factory.adapters", source)
    ] == ["B"]


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


def test_rule_c_flags_core_importing_external_sdks() -> None:
    source = (
        "import pydantic_ai\nfrom gitlab.v4.objects import ProjectMergeRequest\nimport githubkit\n"
        "from opentelemetry.sdk.trace import TracerProvider\n"
    )
    violations = check_source("dark_factory.orchestration", "dark_factory.orchestration", source)
    assert [v.rule for v in violations] == ["C", "C", "C", "C"]


def test_rule_c_allows_adapters_and_core_infrastructure() -> None:
    # Only adapters may import external SDKs; infrastructure drivers that core
    # legitimately uses (sqlalchemy, psycopg, pydantic) are not on the denylist.
    source = "import pydantic_ai\nfrom gitlab import Gitlab\nimport opentelemetry.sdk.trace\n"
    violations = check_source(
        "dark_factory.adapters.github", "dark_factory.adapters.github", source
    )
    assert violations == []
    core_source = "import sqlalchemy\nimport psycopg\nimport pydantic\n"
    assert check_source("dark_factory.changes", "dark_factory.changes", core_source) == []
