"""Unit tests for Redis repository."""

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
from idempotency_kit.core.constants import MAX_KEY_LENGTH, MAX_OPERATION_LENGTH
from idempotency_kit.core.protocols.metrics import IdempotencyMetricsProtocol
from idempotency_kit.infra.storage.redis.aio import RedisAsyncIdempotencyRepository


@pytest.mark.asyncio
async def test_redis_save_and_get(fake_redis: AsyncRedisClient) -> None:
    """Test saving and getting a record from Redis."""
    repo = RedisAsyncIdempotencyRepository(fake_redis)
    service = IdempotencyDomainService()

    record = service.create_record(operation="test", idempotency_key="key", result={"foo": "bar"})

    await repo.save(record)

    retrieved = await repo.get("test", "key")
    assert retrieved is not None
    assert retrieved.result == {"foo": "bar"}
    assert retrieved.operation == "test"
    assert retrieved.idempotency_key == "key"


@pytest.mark.asyncio
async def test_redis_get_not_found(fake_redis: AsyncRedisClient) -> None:
    """Test getting a non-existent record."""
    repo = RedisAsyncIdempotencyRepository(fake_redis)
    assert await repo.get("none", "none") is None


@pytest.mark.asyncio
async def test_redis_delete(fake_redis: AsyncRedisClient) -> None:
    """Test deleting a record."""
    repo = RedisAsyncIdempotencyRepository(fake_redis)
    service = IdempotencyDomainService()

    record = service.create_record(operation="test", idempotency_key="key", result={})
    await repo.save(record)

    assert await repo.delete("test", "key") is True
    assert await repo.get("test", "key") is None
    assert await repo.delete("test", "key") is False


@pytest.mark.asyncio
async def test_redis_corrupted_data(fake_redis: AsyncRedisClient) -> None:
    """Test behavior when data in Redis is corrupted."""
    repo = RedisAsyncIdempotencyRepository(fake_redis)

    # Set invalid JSON directly to Redis
    await fake_redis.set("idempotency:test:key", "invalid-json")

    with pytest.raises(IdempotencyError, match="Corrupted idempotency record"):
        await repo.get("test", "key")


@pytest.mark.asyncio
async def test_redis_validation_error_on_get(fake_redis: AsyncRedisClient) -> None:
    """Test behavior when data in Redis is valid JSON but fails Pydantic validation."""
    repo = RedisAsyncIdempotencyRepository(fake_redis)

    # Set JSON with missing required fields
    await fake_redis.set("idempotency:test:key", '{"operation": "test"}')

    with pytest.raises(IdempotencyValidationError, match="Invalid record data"):
        await repo.get("test", "key")


@pytest.mark.asyncio
async def test_redis_save_expired_record(fake_redis: AsyncRedisClient) -> None:
    """Test that saving an already expired record raises an error."""
    repo = RedisAsyncIdempotencyRepository(fake_redis)

    now = datetime.now(UTC)
    record = IdempotencyRecord(
        operation="test",
        idempotency_key="key",
        result={},
        created_at=now - timedelta(minutes=20),
        expires_at=now - timedelta(minutes=10),
    )

    with pytest.raises(IdempotencyValidationError, match="Cannot save already expired record"):
        await repo.save(record)
    assert await repo.get("test", "key") is None


@pytest.mark.asyncio
async def test_redis_save_collision(fake_redis: AsyncRedisClient) -> None:
    """Test that saving duplicate key raises IdempotencyKeyCollisionError."""
    repo = RedisAsyncIdempotencyRepository(fake_redis)
    service = IdempotencyDomainService()

    record = service.create_record(operation="test", idempotency_key="key", result={})
    await repo.save(record)

    with pytest.raises(IdempotencyKeyCollisionError):
        await repo.save(record)


@pytest.mark.asyncio
async def test_redis_get_exception(fake_redis: AsyncRedisClient) -> None:
    """Test get() when Redis raises an exception."""
    repo = RedisAsyncIdempotencyRepository(fake_redis)
    with (
        patch.object(fake_redis, "get", side_effect=Exception("Redis down")),
        pytest.raises(IdempotencyStorageError, match="Redis storage failure"),
    ):
        await repo.get("test", "key")


@pytest.mark.asyncio
async def test_redis_save_exception(fake_redis: AsyncRedisClient) -> None:
    """Test save() when Redis raises an exception."""
    repo = RedisAsyncIdempotencyRepository(fake_redis)
    service = IdempotencyDomainService()
    record = service.create_record("test", "key", {})
    with (
        patch.object(fake_redis, "set", side_effect=Exception("Redis down")),
        pytest.raises(IdempotencyStorageError, match="Redis storage failure"),
    ):
        await repo.save(record)


@pytest.mark.asyncio
async def test_redis_delete_exception(fake_redis: AsyncRedisClient) -> None:
    """Test delete() when Redis raises an exception."""
    repo = RedisAsyncIdempotencyRepository(fake_redis)
    with (
        patch.object(fake_redis, "delete", side_effect=Exception("Redis down")),
        pytest.raises(IdempotencyStorageError, match="Redis storage failure"),
    ):
        await repo.delete("test", "key")


@pytest.mark.asyncio
async def test_redis_get_many_exception(fake_redis: AsyncRedisClient) -> None:
    """Test get_many() when Redis raises an exception."""
    repo = RedisAsyncIdempotencyRepository(fake_redis)
    with (
        patch.object(fake_redis, "mget", side_effect=Exception("Redis down")),
        pytest.raises(IdempotencyStorageError, match="Redis storage failure"),
    ):
        await repo.get_many("test", ["key"])


@pytest.mark.asyncio
async def test_redis_save_many_exception(fake_redis: AsyncRedisClient) -> None:
    """Test save_many() when Redis raises an exception."""
    repo = RedisAsyncIdempotencyRepository(fake_redis)
    service = IdempotencyDomainService()
    record = service.create_record("test", "key", {})
    with (
        patch.object(fake_redis, "pipeline", side_effect=Exception("Redis down")),
        pytest.raises(IdempotencyStorageError, match="Bulk save failure"),
    ):
        await repo.save_many([record])


@pytest.mark.asyncio
async def test_redis_delete_many_exception(fake_redis: AsyncRedisClient) -> None:
    """Test delete_many() when Redis raises an exception."""
    repo = RedisAsyncIdempotencyRepository(fake_redis)
    with (
        patch.object(fake_redis, "delete", side_effect=Exception("Redis down")),
        pytest.raises(IdempotencyStorageError, match="Redis storage failure"),
    ):
        await repo.delete_many("test", ["key"])


@pytest.mark.asyncio
async def test_redis_validate_inputs(fake_redis: AsyncRedisClient) -> None:
    """Test input validation in repository."""
    repo = RedisAsyncIdempotencyRepository(fake_redis)

    with pytest.raises(IdempotencyValidationError, match="operation"):
        await repo.get("", "key")

    with pytest.raises(IdempotencyValidationError, match="idempotency_key"):
        await repo.get("op", " ")


@pytest.mark.asyncio
async def test_validate_inputs_max_length(fake_redis: AsyncRedisClient) -> None:
    """Test input validation for maximum lengths."""

    repo = RedisAsyncIdempotencyRepository(fake_redis)

    with pytest.raises(IdempotencyValidationError, match="at most 100 characters"):
        await repo.get("a" * (MAX_OPERATION_LENGTH + 1), "key")

    with pytest.raises(IdempotencyValidationError, match="at most 255 characters"):
        await repo.get("op", "a" * (MAX_KEY_LENGTH + 1))


@pytest.mark.asyncio
async def test_validate_inputs_colon(fake_redis: AsyncRedisClient) -> None:
    """Test input validation for colons."""
    repo = RedisAsyncIdempotencyRepository(fake_redis)

    with pytest.raises(IdempotencyValidationError, match="cannot contain ':'"):
        await repo.get("op:with:colon", "key")

    with pytest.raises(IdempotencyValidationError, match="cannot contain ':'"):
        await repo.get("op", "key:with:colon")


@pytest.mark.asyncio
async def test_redis_get_expired_record(fake_redis: AsyncRedisClient) -> None:
    """Test get() with an expired record in Redis."""
    repo = RedisAsyncIdempotencyRepository(fake_redis)

    now = datetime.now(UTC)
    record = IdempotencyRecord(
        operation="test",
        idempotency_key="key",
        result={},
        created_at=now - timedelta(minutes=20),
        expires_at=now - timedelta(minutes=10),
    )

    await fake_redis.set("idempotency:test:key", orjson.dumps(record.model_dump(mode="json")))

    # Should return None AND delete from Redis
    assert await repo.get("test", "key") is None
    assert await fake_redis.get("idempotency:test:key") is None


@pytest.mark.asyncio
async def test_redis_get_expired_record_delete_failure(fake_redis: AsyncRedisClient) -> None:
    """Test get() with an expired record when delete from Redis fails."""
    repo = RedisAsyncIdempotencyRepository(fake_redis)

    now = datetime.now(UTC)
    record = IdempotencyRecord(
        operation="test",
        idempotency_key="key",
        result={},
        created_at=now - timedelta(minutes=20),
        expires_at=now - timedelta(minutes=10),
    )

    await fake_redis.set("idempotency:test:key", orjson.dumps(record.model_dump(mode="json")))

    with (
        patch.object(fake_redis, "delete", side_effect=Exception("Redis down")),
        patch("idempotency_kit.infra.storage.redis.aio.repository.logger.warning") as mock_log,
    ):
        # Should return None even if delete fails
        assert await repo.get("test", "key") is None
        mock_log.assert_called_with(
            "Failed to delete expired record from Redis",
            extra={"operation": "test", "key": "key"},
            exc_info=True,
        )


@pytest.mark.asyncio
async def test_repository_import_error() -> None:
    """Test that RedisAsyncIdempotencyRepository raises ImportError when redis package is missing."""
    import idempotency_kit.infra.storage.redis.aio.repository as repo_module  # noqa: PLC0415

    with (
        patch.object(repo_module, "_HAS_REDIS", False),
        pytest.raises(ImportError, match="requires redis"),
    ):
        RedisAsyncIdempotencyRepository(MagicMock())


@pytest.mark.asyncio
async def test_storage_error_original_error_all_methods(fake_redis: AsyncRedisClient) -> None:
    """Test that IdempotencyStorageError contains the original exception for all methods."""
    repo = RedisAsyncIdempotencyRepository(fake_redis)
    original_exc = Exception("Redis is down")
    service = IdempotencyDomainService()
    record = service.create_record("test", "key", {})

    # save
    with (
        patch.object(fake_redis, "set", side_effect=original_exc),
        pytest.raises(IdempotencyStorageError) as exc_info,
    ):
        await repo.save(record)
    assert exc_info.value.original_error is original_exc

    # delete
    with (
        patch.object(fake_redis, "delete", side_effect=original_exc),
        pytest.raises(IdempotencyStorageError) as exc_info,
    ):
        await repo.delete("test", "key")
    assert exc_info.value.original_error is original_exc

    # get_many
    with (
        patch.object(fake_redis, "mget", side_effect=original_exc),
        pytest.raises(IdempotencyStorageError) as exc_info,
    ):
        await repo.get_many("test", ["key"])
    assert exc_info.value.original_error is original_exc

    # delete_many
    with (
        patch.object(fake_redis, "delete", side_effect=original_exc),
        pytest.raises(IdempotencyStorageError) as exc_info,
    ):
        await repo.delete_many("test", ["key"])
    assert exc_info.value.original_error is original_exc


@pytest.mark.asyncio
async def test_redis_save_serialization_error(fake_redis: AsyncRedisClient) -> None:
    """Test save() with serialization error."""
    repo = RedisAsyncIdempotencyRepository(fake_redis)
    service = IdempotencyDomainService()
    record = service.create_record("test", "key", {"foo": "bar"})

    with (
        patch("orjson.dumps", side_effect=Exception("orjson error")),
        pytest.raises(IdempotencyError, match="Serialization failed"),
    ):
        await repo.save(record)


@pytest.mark.asyncio
async def test_redis_get_many_mixed(fake_redis: AsyncRedisClient) -> None:
    """Test get_many with valid, expired and corrupted records."""
    repo = RedisAsyncIdempotencyRepository(fake_redis)

    now = datetime.now(UTC)
    valid_record = IdempotencyRecord(
        operation="test",
        idempotency_key="valid",
        result={"v": 1},
        created_at=now,
        expires_at=now + timedelta(minutes=10),
    )
    expired_record = IdempotencyRecord(
        operation="test",
        idempotency_key="expired",
        result={"e": 1},
        created_at=now - timedelta(minutes=20),
        expires_at=now - timedelta(minutes=10),
    )

    await fake_redis.set("idempotency:test:valid", orjson.dumps(valid_record.model_dump(mode="json")))
    await fake_redis.set("idempotency:test:expired", orjson.dumps(expired_record.model_dump(mode="json")))
    await fake_redis.set("idempotency:test:corrupted", b"invalid-json")

    with pytest.raises(IdempotencyError, match="Corrupted idempotency record"):
        await repo.get_many("test", ["valid", "expired", "corrupted", "missing"])


@pytest.mark.asyncio
async def test_redis_save_many_serialization_error(fake_redis: AsyncRedisClient) -> None:
    """Test save_many with serialization error for some records."""
    repo = RedisAsyncIdempotencyRepository(fake_redis)
    service = IdempotencyDomainService()
    record1 = service.create_record("test", "key1", {"v": 1})
    record2 = service.create_record("test", "key2", {"v": 2})

    valid_data = orjson.dumps(record1.model_dump(mode="json"))

    with (
        patch("orjson.dumps", side_effect=[valid_data, Exception("orjson error")]),
        pytest.raises(IdempotencyError, match="Bulk save completed with issues"),
    ):
        await repo.save_many([record1, record2])

    assert await repo.get("test", "key1") is not None
    assert await repo.get("test", "key2") is None


@pytest.mark.asyncio
async def test_redis_error_on_redis_down() -> None:
    """Test behavior when Redis is down."""

    # Create a mock client that raises an exception
    mock_redis = AsyncMock(spec=AsyncRedisClient)
    mock_redis.get.side_effect = Exception("Connection refused")
    mock_redis.set.side_effect = Exception("Connection refused")

    repo = RedisAsyncIdempotencyRepository(mock_redis)
    service = IdempotencyDomainService()
    record = service.create_record("test", "key", {})

    # get should raise IdempotencyStorageError
    with pytest.raises(IdempotencyStorageError, match="Redis storage failure") as exc_info:
        await repo.get("test", "key")
    assert exc_info.value.operation == "test"
    assert exc_info.value.original_error is not None

    # save should raise IdempotencyStorageError
    with pytest.raises(IdempotencyStorageError, match="Redis storage failure") as exc_info:
        await repo.save(record)
    assert exc_info.value.operation == "test"
    assert exc_info.value.original_error is not None


@pytest.mark.asyncio
async def test_metrics_comprehensive(fake_redis: AsyncRedisClient) -> None:
    """Test all metrics methods are called correctly."""
    metrics_mock = MagicMock(spec=IdempotencyMetricsProtocol)
    repo = RedisAsyncIdempotencyRepository(fake_redis, metrics=metrics_mock)
    service = IdempotencyDomainService()

    # record_hit on get
    record = service.create_record("op", "hit", {"r": 1})
    await repo.save(record)
    metrics_mock.record_latency.reset_mock()
    await repo.get("op", "hit")
    metrics_mock.record_hit.assert_called_with("op")
    metrics_mock.record_latency.assert_called()

    # record_miss on get
    await repo.get("op", "miss")
    metrics_mock.record_miss.assert_called_with("op")

    # record_collision on save
    with pytest.raises(IdempotencyKeyCollisionError):
        await repo.save(record)
    metrics_mock.record_collision.assert_called_with("op")

    # record_error on serialization failure
    with (
        patch("orjson.dumps", side_effect=Exception("err")),
        pytest.raises(IdempotencyError),
    ):
        await repo.save(service.create_record("op", "err", {}))
    metrics_mock.record_error.assert_called_with("op", "serialization_error")

    # record_error on Redis failure
    with (
        patch.object(fake_redis, "get", side_effect=Exception("RedisError")),
        pytest.raises(IdempotencyStorageError),
    ):
        await repo.get("op", "redis_err")
    metrics_mock.record_error.assert_called_with("op", "Exception")

    # record_bulk_hit and record_bulk_miss on get_many
    # First, save a record under "bulk" operation to get a hit
    await repo.save(service.create_record("bulk", "hit", {"r": 1}))
    metrics_mock.record_bulk_hit.reset_mock()
    metrics_mock.record_bulk_miss.reset_mock()

    await repo.get_many("bulk", ["hit", "miss", "miss2"])
    metrics_mock.record_bulk_hit.assert_called_with("bulk", 1)
    metrics_mock.record_bulk_miss.assert_called_with("bulk", 2)


@pytest.mark.asyncio
async def test_redis_save_subsecond_ttl(fake_redis: AsyncRedisClient) -> None:
    """Test that sub-second TTL is rounded up to 1 second."""
    repo = RedisAsyncIdempotencyRepository(fake_redis)

    now = datetime.now(UTC)
    # TTL is 0.1s
    record = IdempotencyRecord(
        operation="test",
        idempotency_key="subsecond",
        result={},
        created_at=now,
        expires_at=now + timedelta(milliseconds=100),
    )

    # Mock set to verify ex argument
    with patch.object(fake_redis, "set", wraps=fake_redis.set) as mock_set:
        await repo.save(record)
        # Should be called with ex=1
        mock_set.assert_called_once()
        _, kwargs = mock_set.call_args
        assert kwargs["ex"] == 1
    custom_prefix = "custom:prefix:"
    repo = RedisAsyncIdempotencyRepository(fake_redis, key_prefix=custom_prefix)
    service = IdempotencyDomainService()

    record = service.create_record(operation="test", idempotency_key="key", result={"foo": "bar"})
    await repo.save(record)

    # Assert Redis key starts with custom_prefix
    keys = await fake_redis.keys("*")  # type: ignore[attr-defined]
    # fake-redis might return bytes
    key_found = any(k.decode() == f"{custom_prefix}test:key" for k in keys)
    assert key_found is True
