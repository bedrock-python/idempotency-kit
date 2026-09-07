"""Fixtures for core unit tests."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fakeredis import FakeAsyncRedis as AsyncRedisClient

from idempotency_kit.core.models.entities import IdempotencyRecord
from idempotency_kit.core.protocols.adapter import ResultAdapter
from idempotency_kit.core.services.aio.coordinator import AsyncIdempotencyCoordinator
from idempotency_kit.infra.storage.redis.aio import RedisAsyncIdempotencyRepository


@pytest.fixture
def mock_repo() -> AsyncMock:
    """Create mock repository."""
    return AsyncMock()


@pytest.fixture
def mock_domain_service() -> MagicMock:
    """Create mock domain service."""
    service = MagicMock()
    service.create_record.side_effect = lambda operation, idempotency_key, result, ttl_minutes, fingerprint=None: (
        IdempotencyRecord(
            operation=operation,
            idempotency_key=idempotency_key,
            result=result,
            created_at=datetime.now(UTC),
            expires_at=datetime.now(UTC),
            fingerprint=fingerprint,
        )
    )
    return service


@pytest.fixture
def mock_adapter() -> MagicMock:
    """Create mock result adapter."""
    adapter = MagicMock(spec=ResultAdapter)
    adapter.encode.side_effect = lambda x: x
    adapter.decode.side_effect = lambda x: x
    return adapter


@pytest.fixture
def coordinator(mock_repo: AsyncMock, mock_domain_service: MagicMock) -> AsyncIdempotencyCoordinator:
    """Create coordinator with mocked dependencies.

    It runs the unreserved flow -- read, run, ``SET NX`` -- which is the choreography the
    mock-based tests describe; the reservation has its own tests against a fake Redis.
    """
    return AsyncIdempotencyCoordinator(
        repository=mock_repo,
        domain_service=mock_domain_service,
        in_flight="run",
    )


@pytest.fixture
def redis_repository(fake_redis: AsyncRedisClient) -> RedisAsyncIdempotencyRepository:
    """Create the shipped repository on a fake Redis, for tests that need real storage semantics."""
    return RedisAsyncIdempotencyRepository(fake_redis, key_prefix="probe:")


@pytest.fixture
def mock_coordinator() -> MagicMock:
    """Create mock coordinator for decorator tests."""
    coordinator = MagicMock(spec=AsyncIdempotencyCoordinator)
    coordinator.coordinate = AsyncMock()
    return coordinator
