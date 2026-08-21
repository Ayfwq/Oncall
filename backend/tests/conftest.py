"""Common test-suite policy.

Tests which do not declare a service marker are treated as ``offline``.  This
keeps the offline command useful for newly added pure tests while requiring
service-backed tests to opt into an explicit layer.

Service-backed fixtures (PostgreSQL/Milvus/Docker/live API) skip when the
underlying resource is unreachable so individual layers stay usable on a bare
machine.  Pass ``--require-services`` (scripts/test.ps1 does automatically for
the integration/rag/all layers) to turn those skips into hard failures — a
green gate must never be produced by tests that silently did nothing.
"""
from __future__ import annotations

import pytest

SERVICE_MARKERS = ("local", "integration", "rag")


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--require-services",
        action="store_true",
        default=False,
        help="fail instead of skip when a required service layer is unreachable",
    )


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if not any(item.get_closest_marker(name) for name in SERVICE_MARKERS):
            item.add_marker(pytest.mark.offline)


@pytest.fixture(scope="session")
def require_services(pytestconfig: pytest.Config) -> bool:
    """True when --require-services is active."""
    return bool(pytestconfig.getoption("--require-services"))


@pytest.fixture(scope="session")
def service_gate(require_services: bool):
    """Skip when a required service is unavailable; fail under --require-services."""
    def gate(available: bool, reason: str) -> None:
        if available:
            return
        if require_services:
            pytest.fail(f"required service unavailable: {reason}")
        pytest.skip(reason)
    return gate
