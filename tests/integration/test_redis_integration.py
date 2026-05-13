"""Integration tests with real Redis."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from redis.asyncio import Redis as AsyncRedisClient

from idempotency_kit import IdempotencyDomainService, IdempotencyKeyCollisionError, IdempotencyRecord
from idempotency_kit.infra.storage.redis.aio import RedisAsyncIdempotencyRepository


@pytest.mark.asyncio
async def test__redis_repository__save_and_get__retrieves_saved_record(redis_client: AsyncRedisClient) -> None:
    """Test that saved record can be retrieved from Redis."""
    # Arrange
    repo = RedisAsyncIdempotencyRepository(redis_client)
    service = IdempotencyDomainService()

    record = service.create_record(
        operation="user.create",
        idempotency_key="test-key-123",
        result={"user_id": str(uuid4()), "username": "testuser"},
    )

    # Act
    await repo.save(record)
    retrieved = await repo.get("user.create", "test-key-123")

    # Assert
    assert retrieved is not None
    assert retrieved.idempotency_key == "test-key-123"
    assert retrieved.result["username"] == "testuser"


@pytest.mark.asyncio
async def test__redis_repository__delete__removes_record(redis_client: AsyncRedisClient) -> None:
    """Test that delete removes record from Redis."""
    # Arrange
    repo = RedisAsyncIdempotencyRepository(redis_client)
    service = IdempotencyDomainService()
    key = str(uuid4())

    record = service.create_record(operation="test", idempotency_key=key, result={})
    await repo.save(record)

    # Act
    deleted = await repo.delete("test", key)

    # Assert
    assert deleted is True
    assert await repo.get("test", key) is None


@pytest.mark.asyncio
async def test__redis_repository__bulk_operations__save_get_delete_many(redis_client: AsyncRedisClient) -> None:
    """Test that bulk operations work correctly with Redis."""
    # Arrange
    repo = RedisAsyncIdempotencyRepository(redis_client)
    service = IdempotencyDomainService()
    op = f"test-bulk-{uuid4()}"
    key1, key2 = str(uuid4()), str(uuid4())

    record1 = service.create_record(operation=op, idempotency_key=key1, result={"id": 1})
    record2 = service.create_record(operation=op, idempotency_key=key2, result={"id": 2})

    # Act
    await repo.save_many([record1, record2])
    results = await repo.get_many(op, [key1, key2, "missing"])
    deleted = await repo.delete_many(op, [key1, key2, "missing"])

    # Assert
    assert len(results) == 2
    assert results[key1].result == {"id": 1}
    assert results[key2].result == {"id": 2}
    assert deleted == 2
    assert await repo.get(op, key1) is None
    assert await repo.get(op, key2) is None


@pytest.mark.asyncio
async def test__redis_repository__ttl_expiration__removes_expired_key(redis_client: AsyncRedisClient) -> None:
    """Test that Redis TTL causes key expiration."""
    # Arrange
    repo = RedisAsyncIdempotencyRepository(redis_client)
    key = str(uuid4())

    now = datetime.now(UTC)
    record = IdempotencyRecord(
        operation="ttl-test",
        idempotency_key=key,
        result={"v": 1},
        created_at=now,
        expires_at=now + timedelta(seconds=1),
    )

    await repo.save(record)

    # Act - Should be there initially
    initial_get = await repo.get("ttl-test", key)
    assert initial_get is not None

    # Wait for expiration with tolerance
    for _ in range(5):
        if await repo.get("ttl-test", key) is None:
            break
        await asyncio.sleep(0.5)

    # Assert - Should be gone
    assert await repo.get("ttl-test", key) is None


@pytest.mark.asyncio
async def test__redis_repository__duplicate_save__raises_collision_error(redis_client: AsyncRedisClient) -> None:
    """Test that saving duplicate key raises IdempotencyKeyCollisionError."""
    # Arrange
    repo = RedisAsyncIdempotencyRepository(redis_client)
    service = IdempotencyDomainService()

    record = service.create_record(
        operation="user.create",
        idempotency_key="duplicate-key",
        result={"user_id": str(uuid4())},
    )

    await repo.save(record)

    # Act & Assert
    with pytest.raises(IdempotencyKeyCollisionError):
        await repo.save(record)
