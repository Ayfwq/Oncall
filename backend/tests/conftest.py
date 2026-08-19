"""Common test-suite policy.

Tests which do not declare a service marker are treated as ``offline``.  This
keeps the offline command useful for newly added pure tests while requiring
service-backed tests to opt into an explicit layer.
"""
from __future__ import annotations

import pytest


SERVICE_MARKERS = ("local", "integration", "rag")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if not any(item.get_closest_marker(name) for name in SERVICE_MARKERS):
            item.add_marker(pytest.mark.offline)
