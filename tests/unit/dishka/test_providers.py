"""Unit tests for the shipped Dishka providers."""

import pytest
from dishka import Provider, Scope, make_async_container, provide
from fakeredis.aioredis import FakeRedis
from prometheus_client import REGISTRY
from redis.asyncio import Redis as AsyncRedisClient

from idempotency_kit import (
    AsyncIdempotencyCoordinator,
    AsyncIdempotencyRepository,
    IdempotencyMetricsProtocol,
    NoOpIdempotencyMetrics,
)
from idempotency_kit.dishka import (
    AsyncIdempotencyCoordinatorProvider,
    AsyncRedisIdempotencyProvider,
    IdempotencyProvider,
    IdempotencySettingsProtocol,
)
from idempotency_kit.infra.metrics.prometheus import PrometheusIdempotencyMetrics
from idempotency_kit.settings import BaseIdempotencySettings


class _AppProvider(Provider):
    """Application-side provider supplying what the shipped providers depend on."""

    scope = Scope.APP

    def __init__(self, *, metrics_enabled: bool) -> None:
        super().__init__()
        self._metrics_enabled = metrics_enabled

    @provide
    def settings(self) -> IdempotencySettingsProtocol:
        """Provide idempotency settings."""
        return BaseIdempotencySettings(key_prefix="probe:", metrics_enabled=self._metrics_enabled)

    @provide
    def redis(self) -> AsyncRedisClient:
        """Provide a fake Redis client (FakeRedis subclasses redis.asyncio.Redis)."""
        return FakeRedis()


@pytest.mark.asyncio
async def test__shipped_providers__metrics_disabled__container_resolves_coordinator() -> None:
    """Test that the shipped providers alone can build a container and resolve the coordinator."""
    # Arrange
    container = make_async_container(
        _AppProvider(metrics_enabled=False),
        IdempotencyProvider(),
        AsyncRedisIdempotencyProvider(),
        AsyncIdempotencyCoordinatorProvider(),
    )

    # Act
    try:
        coordinator = await container.get(AsyncIdempotencyCoordinator)

        # Assert
        assert isinstance(coordinator, AsyncIdempotencyCoordinator)
    finally:
        await container.close()


@pytest.mark.asyncio
async def test__idempotency_provider__metrics_disabled__provides_noop_metrics() -> None:
    """Test that metrics_enabled=False wires the no-op metrics collector."""
    # Arrange
    container = make_async_container(
        _AppProvider(metrics_enabled=False),
        IdempotencyProvider(),
        AsyncRedisIdempotencyProvider(),
        AsyncIdempotencyCoordinatorProvider(),
    )

    # Act
    try:
        metrics = await container.get(IdempotencyMetricsProtocol)

        # Assert
        assert isinstance(metrics, NoOpIdempotencyMetrics)
    finally:
        await container.close()


@pytest.mark.asyncio
async def test__idempotency_provider__metrics_enabled__provides_prometheus_metrics() -> None:
    """Test that metrics_enabled=True wires the Prometheus metrics collector."""
    # Arrange
    container = make_async_container(
        _AppProvider(metrics_enabled=True),
        IdempotencyProvider(),
        AsyncRedisIdempotencyProvider(),
        AsyncIdempotencyCoordinatorProvider(),
    )
    metrics: IdempotencyMetricsProtocol | None = None

    # Act
    try:
        metrics = await container.get(IdempotencyMetricsProtocol)

        # Assert
        assert isinstance(metrics, PrometheusIdempotencyMetrics)
    finally:
        # The collectors live in the global prometheus_client REGISTRY, so drop them again —
        # otherwise a second PrometheusIdempotencyMetrics in this process hits a duplicate-name error.
        if isinstance(metrics, PrometheusIdempotencyMetrics):
            REGISTRY.unregister(metrics._operations_total)
            REGISTRY.unregister(metrics._duration_seconds)
        await container.close()


@pytest.mark.asyncio
async def test__shipped_providers__metrics_disabled__repository_and_coordinator_share_metrics() -> None:
    """Test that the repository and the coordinator receive the same metrics instance."""
    # Arrange
    container = make_async_container(
        _AppProvider(metrics_enabled=False),
        IdempotencyProvider(),
        AsyncRedisIdempotencyProvider(),
        AsyncIdempotencyCoordinatorProvider(),
    )

    # Act
    try:
        repository = await container.get(AsyncIdempotencyRepository)
        coordinator = await container.get(AsyncIdempotencyCoordinator)

        # Assert
        assert repository._metrics is coordinator._metrics
    finally:
        await container.close()


@pytest.mark.asyncio
async def test__idempotency_provider__without_redis_provider__resolves_metrics() -> None:
    """Test that the metrics factory does not depend on Redis."""
    # Arrange
    container = make_async_container(
        _AppProvider(metrics_enabled=False),
        IdempotencyProvider(),
    )

    # Act
    try:
        metrics = await container.get(IdempotencyMetricsProtocol)

        # Assert
        assert isinstance(metrics, NoOpIdempotencyMetrics)
    finally:
        await container.close()
