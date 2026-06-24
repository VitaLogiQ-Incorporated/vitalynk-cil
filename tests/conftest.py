"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from cil.api.app import create_app
from cil.config import Settings


@pytest.fixture
def client() -> Iterator[TestClient]:
    """A TestClient over a freshly built app, telemetry loop disabled.

    Ops/health tests don't need the background ingest loop (which would write a
    DB); telemetry wiring is covered separately in test_app_telemetry.
    """
    settings = Settings(
        telemetry_enabled=False,
        app_monitoring_enabled=False,
        data_platform_enabled=False,
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client
