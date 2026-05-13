"""Integration tests configuration.

Automatically applies pytest.mark.integration to all tests in integration/ directory.
"""

import pytest


def pytest_collection_modifyitems(items):
    """Automatically add integration marker to tests in the integration/ directory."""
    for item in items:
        # Check if the test file is under tests/integration/
        if "tests/integration" in str(item.fspath) or "tests\\integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)
