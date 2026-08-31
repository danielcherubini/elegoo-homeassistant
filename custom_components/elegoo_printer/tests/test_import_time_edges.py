"""
Import-time edge probes: annotation-only modules must not drag in the transport stacks.

The package ``__init__.py`` pre-shadows both client stacks, so these probes
pre-register the parent packages in ``sys.modules`` (via ``find_spec``) before
importing the module under test.  A naive ``import module; assert "…client" not
in sys.modules`` is meaningless without this — and because the package
``conftest.py`` (loaded by pytest together with the package ``__init__.py``)
also imports a transport client, the target client module is dropped from
``sys.modules`` first so the probe measures the module-under-test's own
runtime imports.
"""

import importlib.util
import sys


def _pre_register_parents(module: str, *, drop: str) -> None:
    """
    Register every ancestor package in sys.modules without executing them.

    Also drops ``module`` and ``drop`` from ``sys.modules`` so the import of
    the module under test genuinely re-gets its imports, and the target
    client module is not pre-loaded by the conftest / package init.
    """
    sys.modules.pop(module, None)
    sys.modules.pop(drop, None)
    parts = module.split(".")
    for i in range(2, len(parts) + 1):
        parent = ".".join(parts[:i])
        if parent in sys.modules:
            continue
        spec = importlib.util.find_spec(parent)
        assert spec is not None, f"spec for {parent} not found"
        sys.modules[parent] = importlib.util.module_from_spec(spec)


def test_definitions_import_does_not_pull_ws_client():
    _pre_register_parents(
        "custom_components.elegoo_printer.definitions",
        drop="custom_components.elegoo_printer.websocket.client",
    )
    import custom_components.elegoo_printer.definitions  # noqa: F401, PLC0415

    assert "custom_components.elegoo_printer.websocket.client" not in sys.modules


def test_coordinator_import_does_not_pull_cc2_client():
    _pre_register_parents(
        "custom_components.elegoo_printer.coordinator",
        drop="custom_components.elegoo_printer.cc2.client",
    )
    import custom_components.elegoo_printer.coordinator  # noqa: F401, PLC0415

    assert "custom_components.elegoo_printer.cc2.client" not in sys.modules
