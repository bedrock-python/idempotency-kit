"""Unit tests for the shipped Dishka providers."""

import pytest
from dishka import AsyncContainer, Provider, Scope, make_async_container, provide
from fakeredis.aioredis import FakeRedis
from redis.asyncio import Redis, RedisCluster
from redis_client_kit.config import RedisSettingsProtocol
from redis_client_kit.providers import AsyncRedisProvider
from redis_client_kit.settings import BaseRedisSettings

from idempotency_kit import (
    AsyncIdempotencyCoordinator,
    AsyncIdempotencyRepository,
    IdempotencyMetricsProtocol,
    JsonResultAdapter,
    NoOpIdempotencyMetrics,
)
from idempotency_kit.core.constants import InFlightMode
from idempotency_kit.dishka import (
    AsyncIdempotencyCoordinatorProvider,
    AsyncRedisIdempotencyProvider,
    IdempotencyProvider,
    IdempotencySettingsProtocol,
)
from idempotency_kit.infra.metrics.prometheus import PrometheusIdempotencyMetrics, get_idempotency_metrics
from idempotency_kit.infra.storage.redis.aio import RedisAsyncIdempotencyRepository
from idempotency_kit.settings import BaseIdempotencySettings


class _AppProvider(Provider):
    """Application-side provider supplying what the shipped providers depend on."""

    scope = Scope.APP

    def __init__(
        self,
        *,
        metrics_enabled: bool,
        enabled: bool = True,
        in_flight: InFlightMode = "wait",
        in_flight_lease_seconds: int = 30,
    ) -> None:
        super().__init__()
        self._metrics_enabled = metrics_enabled
        self._enabled = enabled
        self._in_flight = in_flight
        self._in_flight_lease_seconds = in_flight_lease_seconds

    @provide
    def settings(self) -> IdempotencySettingsProtocol:
        """Provide idempotency settings."""
        return BaseIdempotencySettings(
            key_prefix="probe:",
            metrics_enabled=self._metrics_enabled,
            enabled=self._enabled,
            in_flight=self._in_flight,
            in_flight_lease_seconds=self._in_flight_lease_seconds,
        )

    @provide
    def redis(self) -> Redis | RedisCluster:
        """Provide a fake Redis client (FakeRedis subclasses redis.asyncio.Redis) under the shipped provider's key."""
        return FakeRedis()


class _RedisClientKitAppProvider(Provider):
    """Application-side provider for the redis-client-kit wiring: its settings object and ours."""

    scope = Scope.APP

    @provide
    def redis_settings(self) -> RedisSettingsProtocol:
        """Provide redis-client-kit settings; nothing connects while the startup health check is off."""
        return BaseRedisSettings(key_prefix="probe")

    @provide
    def settings(self) -> IdempotencySettingsProtocol:
        """Provide idempotency settings."""
        return BaseIdempotencySettings(key_prefix="probe:", metrics_enabled=False)


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

    # Act
    try:
        metrics = await container.get(IdempotencyMetricsProtocol)

        # Assert
        assert isinstance(metrics, PrometheusIdempotencyMetrics)
    finally:
        await container.close()


@pytest.mark.asyncio
async def test__shipped_providers__metrics_enabled__coordinator_receives_prometheus_metrics() -> None:
    """The collector the coordinator is built with is the Prometheus one, not only the resolvable key."""
    # Arrange
    container = make_async_container(
        _AppProvider(metrics_enabled=True),
        IdempotencyProvider(),
        AsyncRedisIdempotencyProvider(),
        AsyncIdempotencyCoordinatorProvider(),
    )

    # Act
    try:
        coordinator = await container.get(AsyncIdempotencyCoordinator)

        # Assert
        assert isinstance(coordinator._metrics, PrometheusIdempotencyMetrics)
        assert coordinator._metrics is get_idempotency_metrics()
    finally:
        await container.close()


@pytest.mark.asyncio
async def test__shipped_providers__metrics_enabled__second_container_in_a_process__does_not_raise() -> None:
    """A container rebuilt per test must not register the Prometheus series a second time."""
    # Arrange
    providers = (IdempotencyProvider(), AsyncRedisIdempotencyProvider(), AsyncIdempotencyCoordinatorProvider())
    first_container = make_async_container(_AppProvider(metrics_enabled=True), *providers)
    second_container = make_async_container(_AppProvider(metrics_enabled=True), *providers)

    # Act
    try:
        first = await container_coordinator(first_container)
        second = await container_coordinator(second_container)

        # Assert
        assert isinstance(second._metrics, PrometheusIdempotencyMetrics)
        assert first._metrics is second._metrics
    finally:
        await first_container.close()
        await second_container.close()


async def container_coordinator(container: AsyncContainer) -> AsyncIdempotencyCoordinator:
    """Resolve the coordinator, which is where the duplicate-series error used to surface."""
    return await container.get(AsyncIdempotencyCoordinator)


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


@pytest.mark.asyncio
async def test__shipped_providers__settings_disabled__coordinator_is_a_pass_through() -> None:
    """settings.enabled=False has to reach the coordinator the providers build."""
    # Arrange
    container = make_async_container(
        _AppProvider(metrics_enabled=False, enabled=False),
        IdempotencyProvider(),
        AsyncRedisIdempotencyProvider(),
        AsyncIdempotencyCoordinatorProvider(),
    )
    calls = 0

    async def action() -> str:
        nonlocal calls
        calls += 1
        return "ran"

    # Act
    try:
        coordinator = await container.get(AsyncIdempotencyCoordinator)
        await coordinator.coordinate("op.disabled", "key", 600, JsonResultAdapter(), action)
        await coordinator.coordinate("op.disabled", "key", 600, JsonResultAdapter(), action)

        # Assert
        assert calls == 2
        repository = await container.get(AsyncIdempotencyRepository)
        assert await repository.get("op.disabled", "key") is None
    finally:
        await container.close()


@pytest.mark.asyncio
async def test__shipped_providers__in_flight_settings__reach_the_coordinator() -> None:
    """The mode and the lease on the settings object are what the provided coordinator runs with."""
    # Arrange
    container = make_async_container(
        _AppProvider(metrics_enabled=False, in_flight="raise", in_flight_lease_seconds=5),
        IdempotencyProvider(),
        AsyncRedisIdempotencyProvider(),
        AsyncIdempotencyCoordinatorProvider(),
    )

    # Act
    try:
        coordinator = await container.get(AsyncIdempotencyCoordinator)

        # Assert
        assert coordinator._in_flight == "raise"
        assert coordinator._in_flight_lease_seconds == 5
    finally:
        await container.close()


@pytest.mark.asyncio
async def test__shipped_providers__redis_client_kit_client__container_builds_and_resolves_repository() -> None:
    """The client redis-client-kit provides is the one the Redis provider asks for: no adapter in between (#32)."""
    # Arrange
    container = make_async_container(
        AsyncRedisProvider(check_health_on_startup=False),
        _RedisClientKitAppProvider(),
        IdempotencyProvider(),
        AsyncRedisIdempotencyProvider(),
        AsyncIdempotencyCoordinatorProvider(),
    )

    # Act
    try:
        repository = await container.get(AsyncIdempotencyRepository)

        # Assert
        assert isinstance(repository, RedisAsyncIdempotencyRepository)
        assert isinstance(repository._redis, Redis)
    finally:
        await container.close()
