"""Smoke tests for the dark_factory package layout."""

import importlib
from pathlib import Path

import dark_factory

SUBPACKAGES = (
    "dark_factory.adapters",
    "dark_factory.agents",
    "dark_factory.changes",
    "dark_factory.context",
    "dark_factory.execution",
    "dark_factory.orchestration",
    "dark_factory.quality",
    "dark_factory.ports",
)


def test_root_package_imports() -> None:
    assert dark_factory.__doc__ is not None


def test_all_hld_subpackages_import() -> None:
    for name in SUBPACKAGES:
        module = importlib.import_module(name)
        assert module.__doc__, f"{name} must define a module docstring"


def test_py_typed_marker_present() -> None:
    marker = Path(dark_factory.__file__).parent / "py.typed"
    assert marker.is_file()
