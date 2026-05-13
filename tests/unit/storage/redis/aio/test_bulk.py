"""Unit tests for Redis repository bulk operations."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import orjson
import pytest
from fakeredis import FakeAsyncRedis as AsyncRedisClient

from idempotency_kit import (
    IdempotencyDomainService,
    IdempotencyError,
    IdempotencyKeyCollisionError,
    IdempotencyRecord,
    IdempotencyStorageError,
    IdempotencyValidationError,
)
from idempotency_kit.infra.storage.redis.aio import RedisAsyncIdempotencyRepository


@pytest.mark.asyncio
async def test_redis_bulk_ops(fake_redis: AsyncRedisClient) -> None:
    """Test get_many, save_many, delete_many in Redis."""
    repo = RedisAsyncIdempotencyRepository(fake_redis)
    service = IdempotencyDomainService()

    record1 = service.create_record(operation="bulk", idempotency_key="key1", result={"id": 1})
    record2 = service.create_record(operation="bulk", idempotency_key="key2", result={"id": 2})

    # Save many
    await repo.save_many([record1, record2])

    # Get many
    results = await repo.get_many("bulk", ["key1", "key2", "key3"])
    assert len(results) == 2
    assert results["key1"].result == {"id": 1}
    assert results["key2"].result == {"id": 2}
    assert "key3" not in results

    # Delete many
    deleted = await repo.delete_many("bulk", ["key1", "key2", "key4"])
    assert deleted == 2
    assert await repo.get("bulk", "key1") is None
    assert await repo.get("bulk", "key2") is None


@pytest.mark.asyncio
async def test_get_many_with_expired_only(fake_redis: AsyncRedisClient) -> None:
    """Test that get_many deletes expired entries from Redis."""
    repo = RedisAsyncIdempotencyRepository(fake_redis)

    now = datetime.now(UTC)
    expired_record = IdempotencyRecord(
        operation="test",
        idempotency_key="expired",
        result={"e": 1},
        created_at=now - timedelta(minutes=20),
        expires_at=now - timedelta(minutes=10),
    )

    await fake_redis.set("idempotency:test:expired", orjson.dumps(expired_record.model_dump(mode="json")))

    # Initial state: key exists
    assert await fake_redis.get("idempotency:test:expired") is not None

    # Get many: should skip expired AND delete it
    results = await repo.get_many("test", ["expired"])
    assert "expired" not in results
    assert await fake_redis.get("idempotency:test:expired") is None


@pytest.mark.asyncio
async def test_save_many_with_duplicate_keys_in_input(fake_redis: AsyncRedisClient) -> None:
    """Test save_many with duplicate keys in the same batch."""
    repo = RedisAsyncIdempotencyRepository(fake_redis)
    service = IdempotencyDomainService()

    record1 = service.create_record("op", "key", {"v": 1})
    record2 = service.create_record("op", "key", {"v": 2})

    # The first one should succeed, second one should be a collision
    # Our implementation uses pipeline.set(..., nx=True) for each record.
    # So second SET will return False.

    with pytest.raises(IdempotencyKeyCollisionError):
        await repo.save_many([record1, record2])

    # Check which one survived (first one in pipeline order)
    retrieved = await repo.get("op", "key")
    assert retrieved is not None
    assert retrieved.result == {"v": 1}


@pytest.mark.asyncio
async def test_save_many_pipeline_execute_failure(fake_redis: AsyncRedisClient) -> None:
    """Test save_many when pipeline.execute() raises an exception."""

    repo = RedisAsyncIdempotencyRepository(fake_redis)
    service = IdempotencyDomainService()
    record = service.create_record("op", "key", {})

    # Mock pipeline and its execute method using AsyncMock
    pipeline_mock = AsyncMock()
    pipeline_mock.set = MagicMock()  # set is not awaited in pipeline
    pipeline_mock.execute.side_effect = Exception("Redis error")
    pipeline_mock.__aenter__.return_value = pipeline_mock
    pipeline_mock.__aexit__.return_value = None

    with (
        patch.object(fake_redis, "pipeline", return_value=pipeline_mock),
        pytest.raises(IdempotencyStorageError, match="Bulk save failure"),
    ):
        await repo.save_many([record])


@pytest.mark.asyncio
async def test_save_many_mixed_operations_raises(fake_redis: AsyncRedisClient) -> None:
    """Test that save_many raises ValidationError if records have different operations."""
    repo = RedisAsyncIdempotencyRepository(fake_redis)
    service = IdempotencyDomainService()

    record1 = service.create_record("op1", "key1", {})
    record2 = service.create_record("op2", "key2", {})

    with pytest.raises(IdempotencyValidationError, match="All records in batch must have the same operation"):
        await repo.save_many([record1, record2])


@pytest.mark.asyncio
async def test_save_many_collision_and_errors(fake_redis: AsyncRedisClient) -> None:
    """Test that save_many prioritizes IdempotencyKeyCollisionError even if other errors occurred."""
    repo = RedisAsyncIdempotencyRepository(fake_redis)
    service = IdempotencyDomainService()

    record1 = service.create_record("op", "key1", {"v": 1})
    record2 = service.create_record("op", "key2", {"v": 2})

    # Pre-save key1 to cause collision
    await repo.save(record1)

    # Force serialization error for record2
    with (
        patch("orjson.dumps", side_effect=[b"data1", Exception("Serialize error")]),
        pytest.raises(IdempotencyKeyCollisionError),
    ):
        await repo.save_many([record1, record2])


@pytest.mark.asyncio
async def test_save_many_all_invalid(fake_redis: AsyncRedisClient) -> None:
    """Test save_many when all records fail validation."""
    repo = RedisAsyncIdempotencyRepository(fake_redis)
    # We can't use service.create_record for invalid record, so we manually create it
    # and then use a mock to bypass constructor validation or just test repository validation

    now = datetime.now(UTC)
    invalid_record = IdempotencyRecord.model_construct(
        operation="op:invalid",
        idempotency_key="key",
        result={},
        created_at=now,
        expires_at=now + timedelta(minutes=10),
    )

    with pytest.raises(IdempotencyValidationError, match="cannot contain ':'"):
        await repo.save_many([invalid_record])


@pytest.mark.asyncio
async def test_get_many_expired_delete_failure(fake_redis: AsyncRedisClient) -> None:
    """Test get_many() when deleting expired keys fails."""
    repo = RedisAsyncIdempotencyRepository(fake_redis)

    now = datetime.now(UTC)
    expired_record = IdempotencyRecord(
        operation="test",
        idempotency_key="expired",
        result={"e": 1},
        created_at=now - timedelta(minutes=20),
        expires_at=now - timedelta(minutes=10),
    )

    await fake_redis.set("idempotency:test:expired", orjson.dumps(expired_record.model_dump(mode="json")))

    with (
        patch.object(fake_redis, "delete", side_effect=Exception("Redis down")),
        patch("idempotency_kit.infra.storage.redis.aio.repository.logger.warning") as mock_log,
    ):
        results = await repo.get_many("test", ["expired"])
        assert "expired" not in results
        mock_log.assert_called_with(
            "Failed to batch delete expired records from Redis during get_many",
            extra={"operation": "test", "expired_count": 1},
            exc_info=True,
        )


@pytest.mark.asyncio
async def test_save_many_rollback_failure(fake_redis: AsyncRedisClient) -> None:
    """Test save_many() when rollback delete fails."""
    repo = RedisAsyncIdempotencyRepository(fake_redis)
    service = IdempotencyDomainService()

    record1 = service.create_record("test", "key1", {"v": 1})
    record2 = service.create_record("test", "key2", {"v": 2})

    # Pre-save key2 to cause collision
    await repo.save(record2)

    with (
        patch.object(fake_redis, "delete", side_effect=Exception("Redis down")),
        patch("idempotency_kit.infra.storage.redis.aio.repository.logger.exception") as mock_log,
        pytest.raises(IdempotencyKeyCollisionError),
    ):
        await repo.save_many([record1, record2], rollback_on_error=True)

    mock_log.assert_called_with("Failed to rollback saved keys in save_many", extra={"operation": "test", "count": 1})


@pytest.mark.asyncio
async def test_save_many_collision(fake_redis: AsyncRedisClient) -> None:
    """Test that save_many raises IdempotencyKeyCollisionError if any key exists."""
    repo = RedisAsyncIdempotencyRepository(fake_redis)
    service = IdempotencyDomainService()

    record1 = service.create_record("test", "key1", {"v": 1})
    record2 = service.create_record("test", "key2", {"v": 2})

    # Save first one
    await repo.save(record1)

    # Now save both - should fail because key1 exists
    with pytest.raises(IdempotencyKeyCollisionError, match=r"keys \['key1'\]"):
        await repo.save_many([record1, record2])

    # key2 should STILL be saved because we use non-transactional pipeline!
    assert await repo.get("test", "key2") is not None


@pytest.mark.asyncio
async def test_save_many_rollback_on_error(fake_redis: AsyncRedisClient) -> None:
    """Test save_many with rollback_on_error=True."""
    repo = RedisAsyncIdempotencyRepository(fake_redis)
    service = IdempotencyDomainService()

    record1 = service.create_record("test", "key1", {"v": 1})
    record2 = service.create_record("test", "key2", {"v": 2})

    # Pre-save key2 to cause collision
    await repo.save(record2)

    # Save both with rollback - should fail and key1 should NOT be in Redis
    with pytest.raises(IdempotencyKeyCollisionError):
        await repo.save_many([record1, record2], rollback_on_error=True)

    assert await repo.get("test", "key1") is None
    # key2 remains as it was already there
    assert await repo.get("test", "key2") is not None


@pytest.mark.asyncio
async def test_save_many_multiple_collisions(fake_redis: AsyncRedisClient) -> None:
    """Test save_many with multiple colliding keys."""
    repo = RedisAsyncIdempotencyRepository(fake_redis)
    service = IdempotencyDomainService()

    record1 = service.create_record("test", "key1", {"v": 1})
    record2 = service.create_record("test", "key2", {"v": 2})

    await repo.save(record1)
    await repo.save(record2)

    with pytest.raises(IdempotencyKeyCollisionError, match=r"keys \['key1', 'key2'\]"):
        await repo.save_many([record1, record2])


@pytest.mark.asyncio
async def test_save_many_serialization_error_rollback(fake_redis: AsyncRedisClient) -> None:
    """Test save_many with serialization error and rollback."""
    repo = RedisAsyncIdempotencyRepository(fake_redis)

    record1 = IdempotencyRecord.create("op", "key1", {"v": 1}, 60)
    record2 = IdempotencyRecord.create("op", "key2", {"v": 2}, 60)

    # Force serialization error for record2 only
    with (
        patch("orjson.dumps", side_effect=[b"data1", Exception("Serialize error")]),
        pytest.raises(IdempotencyError, match="Bulk save completed with issues"),
    ):
        await repo.save_many([record1, record2], rollback_on_error=True)

    # key1 should be rolled back
    assert await fake_redis.get("idempotency:op:key1") is None


@pytest.mark.asyncio
async def test_save_many_expired_raises(fake_redis: AsyncRedisClient) -> None:
    """Test that save_many raises IdempotencyValidationError for already expired records."""
    repo = RedisAsyncIdempotencyRepository(fake_redis)

    now = datetime.now(UTC)

    expired_record = IdempotencyRecord(
        operation="test",
        idempotency_key="expired",
        result={},
        created_at=now - timedelta(minutes=20),
        expires_at=now - timedelta(minutes=10),
    )
    valid_record = IdempotencyRecord(
        operation="test",
        idempotency_key="valid",
        result={},
        created_at=now,
        expires_at=now + timedelta(minutes=10),
    )

    with pytest.raises(IdempotencyValidationError, match="Cannot save already expired record"):
        await repo.save_many([expired_record, valid_record], rollback_on_error=True)

    assert await repo.get("test", "expired") is None
    assert await repo.get("test", "valid") is None  # Should not be saved due to error


@pytest.mark.asyncio
async def test_get_many_validation_error_on_one_record(fake_redis: AsyncRedisClient) -> None:
    """Test get_many() when one record fails validation."""
    repo = RedisAsyncIdempotencyRepository(fake_redis)

    await fake_redis.set(
        "idempotency:test:valid",
        orjson.dumps(
            {
                "operation": "test",
                "idempotency_key": "valid",
                "result": {},
                "created_at": "2024-01-01T00:00:00Z",
                "expires_at": "2024-01-01T01:00:00Z",
            }
        ),
    )
    await fake_redis.set("idempotency:test:invalid", b'{"operation": "test"}')

    with pytest.raises(IdempotencyValidationError, match="Invalid record data"):
        await repo.get_many("test", ["valid", "invalid"])


@pytest.mark.asyncio
async def test_save_many_mixed_validation_errors(fake_redis: AsyncRedisClient) -> None:
    """Test save_many() with different types of validation errors."""
    repo = RedisAsyncIdempotencyRepository(fake_redis)

    now = datetime.now(UTC)
    invalid_op = IdempotencyRecord.model_construct(
        operation="op:invalid",
        idempotency_key="key1",
        result={},
        created_at=now,
        expires_at=now + timedelta(minutes=10),
    )
    invalid_key = IdempotencyRecord.model_construct(
        operation="op", idempotency_key="key:invalid", result={}, created_at=now, expires_at=now + timedelta(minutes=10)
    )

    with pytest.raises(IdempotencyValidationError, match="Bulk validation failed"):
        await repo.save_many([invalid_op, invalid_key])
