"""Dishka provider for Redis-backed async idempotency repository."""

from dishka import Provider, Scope, provide
from redis.asyncio import Redis as AsyncRedisClient

from idempotency_kit import AsyncIdempotencyRepository, IdempotencyMetricsProtocol
from idempotency_kit.infra.storage.redis.aio import RedisAsyncIdempotencyRepository

from ..protocols import IdempotencySettingsProtocol


class AsyncRedisIdempotencyProvider(Provider):
    """Provider for async Redis-backed idempotency repository."""

    scope = Scope.APP

    @provide
    def get_repository(
        self,
        redis: AsyncRedisClient,
        settings: IdempotencySettingsProtocol,
        metrics: IdempotencyMetricsProtocol | None = None,
    ) -> AsyncIdempotencyRepository:
        """Provide idempotency repository."""
        return RedisAsyncIdempotencyRepository(
            redis=redis,
            key_prefix=settings.key_prefix,
            metrics=metrics,
        )
