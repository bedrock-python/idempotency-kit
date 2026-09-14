"""Dishka provider for Redis-backed async idempotency repository."""

from dishka import Provider, Scope, provide
from redis.asyncio import Redis, RedisCluster

from idempotency_kit import AsyncIdempotencyRepository, IdempotencyMetricsProtocol
from idempotency_kit.infra.storage.redis.aio import RedisAsyncIdempotencyRepository

from ..protocols import IdempotencySettingsProtocol


class AsyncRedisIdempotencyProvider(Provider):
    """Provider for async Redis-backed idempotency repository.

    The client is requested as ``Redis | RedisCluster`` -- the key
    ``redis_client_kit.AsyncRedisClient`` names, so redis-client-kit's
    ``AsyncRedisProvider`` satisfies it as is. Dishka matches keys exactly:
    a provider of your own has to use that annotation, not ``Redis`` alone.
    """

    scope = Scope.APP

    @provide
    def get_repository(
        self,
        redis: Redis | RedisCluster,
        settings: IdempotencySettingsProtocol,
        metrics: IdempotencyMetricsProtocol,
    ) -> AsyncIdempotencyRepository:
        """Provide idempotency repository."""
        return RedisAsyncIdempotencyRepository(
            redis=redis,
            key_prefix=settings.key_prefix,
            metrics=metrics,
        )
