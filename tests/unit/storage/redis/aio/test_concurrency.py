"""Unit tests for Redis repository concurrency."""

import asyncio

import pytest
from redis.asyncio import Redis as AsyncRedisClient

from idempotency_kit import IdempotencyDomainService, IdempotencyKeyCollisionError
from idempotency_kit.infra.storage.redis.aio import RedisAsyncIdempotencyRepository


@pytest.mark.asyncio
async def test_sequential_save_collision(fake_redis: AsyncRedisClient) -> None:
    """Test that sequential saves result in exactly one success and one collision.

    Note: This uses asyncio.gather which in a single-threaded event loop
    still executes tasks sequentially but is a good smoke test for collision.
    """
    repo = RedisAsyncIdempotencyRepository(fake_redis)
    service = IdempotencyDomainService()

    record = service.create_record(
        operation="concurrent_test", idempotency_key="shared_key", result={"status": "processing"}
    )

    # Simulate concurrent saves
    results = await asyncio.gather(repo.save(record), repo.save(record), return_exceptions=True)

    # One should be None (success), one should be IdempotencyKeyCollisionError
    success_count = sum(1 for r in results if r is None)
    collision_count = sum(1 for r in results if isinstance(r, IdempotencyKeyCollisionError))

    assert success_count == 1
    assert collision_count == 1
