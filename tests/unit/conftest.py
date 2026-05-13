"""Unit tests configuration.

Automatically applies pytest.mark.unit to all tests in unit/ directory.
"""

import pytest


def pytest_collection_modifyitems(items):
    """Automatically add unit marker to tests in the unit/ directory."""
    for item in items:
        # Check if the test file is under tests/unit/
        if "tests/unit" in str(item.fspath) or "tests\\unit" in str(item.fspath):
            item.add_marker(pytest.mark.unit)
