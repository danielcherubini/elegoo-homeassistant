"""
Import-time check for sdcp/transport — static import-line inspection.

The component ``__init__.py`` imports aiohttp (and the ws client) at
runtime, so a ``sys.modules`` assertion would be guaranteed red for
unrelated reasons — the STATIC import-line check IS the spec: the
top-level import statements of ``sdcp/transport/base.py`` and
``discovery.py`` must reference none of aiohttp / aiomqtt / paho.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_TRANSPORT_DIR = Path(__file__).parent.parent / "sdcp" / "transport"
_FORBIDDEN_STEMS = ("aiohttp", "aiomqtt", "paho")
_MODULES = [
    path.name for path in _TRANSPORT_DIR.glob("*.py") if path.name != "__init__.py"
]


def _top_level_imported_modules(
    path: Path,
) -> list[str]:
    """Return the module names of a file's top-level import statements."""
    tree = ast.parse(path.read_text())
    modules: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def test_transport_modules_are_present() -> None:
    """The transport package must contain base.py and discovery.py."""
    assert {"base.py", "discovery.py"} <= set(_MODULES)


@pytest.mark.parametrize("module", _MODULES)
def test_transport_module_top_level_imports_forbid_wire_libs(
    module: str,
) -> None:
    """No top-level aiohttp / aiomqtt / paho import in transport modules."""
    committed = _top_level_imported_modules(_TRANSPORT_DIR / module)
    offending = [
        name
        for name in committed
        if any(name == stem or name.startswith(f"{stem}.") for stem in _FORBIDDEN_STEMS)
    ]
    assert not offending, f"{module} imports forbidden module(s): {offending}"
