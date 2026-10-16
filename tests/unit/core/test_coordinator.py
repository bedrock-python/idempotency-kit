"""Unit tests for async idempotency coordinator."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from fakeredis import FakeAsyncRedis as AsyncRedisClient

from idempotency_kit.core.adapters import JsonResultAdapter
from idempotency_kit.core.constants import InFlightMode
from idempotency_kit.core.exceptions import IdempotencyKeyCollisionError, IdempotencyValidationError
from idempotency_kit.core.models.entities import IdempotencyRecord
from idempotency_kit.core.services.aio.coordinator import AsyncIdempotencyCoordinator
from idempotency_kit.core.services.domain import IdempotencyDomainService
from idempotency_kit.infra.storage.redis.aio import RedisAsyncIdempotencyRepository


@pytest.mark.asyncio
async def test__coordinator__cache_miss__executes_action_and_saves(
    coordinator: AsyncIdempotencyCoordinator, mock_repo: AsyncMock, mock_adapter: MagicMock
) -> None:
    """Test that cache miss executes action and saves result."""
    # Arrange
    operation = "test.op"
    key = "test-key"
    expected_result = {"data": "ok"}
    action = AsyncMock(return_value=expected_result)

    mock_repo.get.return_value = None
    mock_repo.save.return_value = None

    # Act
    result: dict[str, str] = await coordinator.coordinate(
        operation,
        key,
        60,
        mock_adapter,
        action,
    )

    # Assert
    assert result == expected_result
    action.assert_called_once()
    mock_repo.get.assert_called_once_with(operation, key)
    mock_repo.save.assert_called_once()
    mock_adapter.encode.assert_called_once_with(expected_result)


@pytest.mark.asyncio
async def test__coordinator__cache_hit__returns_cached_result(
    coordinator: AsyncIdempotencyCoordinator, mock_repo: AsyncMock, mock_adapter: MagicMock
) -> None:
    """Test that cache hit returns cached result without executing action."""
    # Arrange
    operation = "test.op"
    key = "test-key"
    cached_data = {"data": "cached"}
    record = IdempotencyRecord(
        operation=operation,
        idempotency_key=key,
        result=cached_data,
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC),
    )
    action = AsyncMock()

    mock_repo.get.return_value = record

    # Act
    result: dict[str, str] = await coordinator.coordinate(
        operation,
        key,
        60,
        mock_adapter,
        action,
    )

    # Assert
    assert result == cached_data
    action.assert_not_called()
    mock_adapter.decode.assert_called_once_with(cached_data)


@pytest.mark.asyncio
async def test__coordinator__decode_error__executes_action(
    coordinator: AsyncIdempotencyCoordinator, mock_repo: AsyncMock, mock_adapter: MagicMock
) -> None:
    """Test that decode error causes action execution instead of failure."""
    # Arrange
    operation = "test.op"
    key = "test-key"
    record = IdempotencyRecord(
        operation=operation,
        idempotency_key=key,
        result={"bad": "data"},
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC),
    )
    expected_result = {"data": "fresh"}
    action = AsyncMock(return_value=expected_result)

    mock_repo.get.return_value = record
    mock_adapter.decode.side_effect = Exception("decode failed")

    # Act
    result: dict[str, str] = await coordinator.coordinate(
        operation,
        key,
        60,
        mock_adapter,
        action,
    )

    # Assert
    assert result == expected_result
    action.assert_called_once()


@pytest.mark.asyncio
async def test__coordinator__storage_get_error__executes_action(
    coordinator: AsyncIdempotencyCoordinator, mock_repo: AsyncMock, mock_adapter: MagicMock
) -> None:
    """Test that storage error on get causes graceful degradation to action execution."""
    # Arrange
    action = AsyncMock(return_value={"data": "ok"})
    mock_repo.get.side_effect = Exception("redis down")

    # Act
    result: dict[str, str] = await coordinator.coordinate(
        "op",
        "key",
        60,
        mock_adapter,
        action,
    )

    # Assert
    assert result == {"data": "ok"}
    action.assert_called_once()


@pytest.mark.asyncio
async def test__coordinator__collision_on_save__fetches_winner_result(
    coordinator: AsyncIdempotencyCoordinator, mock_repo: AsyncMock, mock_adapter: MagicMock
) -> None:
    """Test that collision on save fetches and returns winner's result."""
    # Arrange
    operation = "test.op"
    key = "test-key"
    winner_result = {"data": "winner"}
    winner_record = IdempotencyRecord(
        operation=operation,
        idempotency_key=key,
        result=winner_result,
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC),
    )
    action = AsyncMock(return_value={"data": "mine"})

    mock_repo.get.side_effect = [None, winner_record]  # miss first, then found on collision
    mock_repo.save.side_effect = IdempotencyKeyCollisionError(operation, key)

    # Act
    result: dict[str, str] = await coordinator.coordinate(
        operation,
        key,
        60,
        mock_adapter,
        action,
    )

    # Assert
    assert result == winner_result
    action.assert_called_once()


@pytest.mark.asyncio
async def test__coordinator__collision_decode_error__returns_own_result(
    coordinator: AsyncIdempotencyCoordinator, mock_repo: AsyncMock, mock_adapter: MagicMock
) -> None:
    """Test that decode error on winner's result returns own result."""
    # Arrange
    operation = "test.op"
    key = "test-key"
    my_result = {"data": "mine"}
    winner_record = IdempotencyRecord(
        operation=operation,
        idempotency_key=key,
        result={"bad": "data"},
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC),
    )
    action = AsyncMock(return_value=my_result)

    mock_repo.get.side_effect = [None, winner_record]
    mock_repo.save.side_effect = IdempotencyKeyCollisionError(operation, key)
    mock_adapter.decode.side_effect = Exception("decode failed")

    # Act
    result: dict[str, str] = await coordinator.coordinate(
        operation,
        key,
        60,
        mock_adapter,
        action,
    )

    # Assert
    assert result == my_result


@pytest.mark.asyncio
async def test__coordinator__storage_save_error__returns_result(
    coordinator: AsyncIdempotencyCoordinator, mock_repo: AsyncMock, mock_adapter: MagicMock
) -> None:
    """Test that storage error on save returns result successfully."""
    # Arrange
    expected_result = {"data": "ok"}
    action = AsyncMock(return_value=expected_result)
    mock_repo.get.return_value = None
    mock_repo.save.side_effect = Exception("save failed")

    # Act
    result: dict[str, str] = await coordinator.coordinate(
        "op",
        "key",
        60,
        mock_adapter,
        action,
    )

    # Assert
    assert result == expected_result
    action.assert_called_once()


@pytest.mark.asyncio
async def test__coordinator__no_key__executes_directly(
    coordinator: AsyncIdempotencyCoordinator, mock_repo: AsyncMock, mock_adapter: MagicMock
) -> None:
    """Test that missing idempotency key bypasses coordinator logic."""
    # Arrange
    action = AsyncMock(return_value="ok")

    # Act
    result: str = await coordinator.coordinate(
        "op",
        None,
        60,
        mock_adapter,
        action,
    )

    # Assert
    assert result == "ok"
    mock_repo.get.assert_not_called()
    action.assert_called_once()


@pytest.mark.asyncio
async def test__coordinator__cached_none_result__is_a_hit_not_a_miss(
    coordinator: AsyncIdempotencyCoordinator, mock_repo: AsyncMock, mock_adapter: MagicMock
) -> None:
    """A stored ``null`` (a void result) must replay, not re-execute the action."""
    # Arrange
    record = IdempotencyRecord(
        operation="op.void",
        idempotency_key="key",
        result=None,
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC),
    )
    action = AsyncMock(return_value=None)
    mock_repo.get.return_value = record

    # Act
    result = await coordinator.coordinate("op.void", "key", 60, mock_adapter, action)

    # Assert
    assert result is None
    action.assert_not_called()
    mock_repo.save.assert_not_called()


@pytest.mark.asyncio
async def test__coordinator__record_validation_error__returns_result_and_reports_a_contract_violation(
    mock_repo: AsyncMock, mock_adapter: MagicMock
) -> None:
    """An unstorable record is a programming error: reported as such, never turned into a retry."""
    # Arrange
    domain_service = MagicMock()
    domain_service.create_record.side_effect = IdempotencyValidationError("result is not JSON")
    metrics = MagicMock()
    coordinator = AsyncIdempotencyCoordinator(repository=mock_repo, domain_service=domain_service, metrics=metrics)
    mock_repo.get.return_value = None
    action = AsyncMock(return_value={"data": "ok"})

    # Act
    result = await coordinator.coordinate("op", "key", 60, mock_adapter, action)

    # Assert
    assert result == {"data": "ok"}
    mock_repo.replace.assert_not_called()
    mock_repo.delete.assert_called_once_with("op", "key")  # the reservation goes with the result that never came
    metrics.record_error.assert_called_once_with("op", "record_validation_error")


@pytest.mark.asyncio
async def test__coordinator__expired_record_from_repository__is_a_miss(
    mock_repo: AsyncMock, mock_adapter: MagicMock
) -> None:
    """Expiry is the domain's rule, so a repository that hands back a stale record must not replay it."""
    # Arrange
    now = datetime.now(UTC)
    stale = IdempotencyRecord(
        operation="op",
        idempotency_key="key",
        result={"data": "stale"},
        created_at=now - timedelta(hours=2),
        expires_at=now - timedelta(hours=1),
    )
    metrics = MagicMock()
    coordinator = AsyncIdempotencyCoordinator(
        repository=mock_repo,
        domain_service=IdempotencyDomainService(),
        metrics=metrics,
    )
    mock_repo.get.return_value = stale
    action = AsyncMock(return_value={"data": "fresh"})

    # Act
    result = await coordinator.coordinate("op", "key", 600, mock_adapter, action)

    # Assert
    assert result == {"data": "fresh"}
    action.assert_called_once()
    mock_adapter.decode.assert_not_called()
    metrics.record_miss.assert_called_once_with("op")


@pytest.mark.asyncio
async def test__coordinator__disabled__runs_the_action_without_touching_storage(
    mock_repo: AsyncMock, mock_domain_service: MagicMock, mock_adapter: MagicMock
) -> None:
    """enabled=False is a kill switch: no read, no write, no metric."""
    # Arrange
    metrics = MagicMock()
    coordinator = AsyncIdempotencyCoordinator(
        repository=mock_repo,
        domain_service=mock_domain_service,
        metrics=metrics,
        enabled=False,
    )
    action = AsyncMock(return_value="fresh")

    # Act
    result = await coordinator.coordinate("op", "key", 60, mock_adapter, action)

    # Assert
    assert result == "fresh"
    action.assert_called_once()
    mock_repo.get.assert_not_called()
    mock_repo.save.assert_not_called()
    metrics.record_miss.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("in_flight", ["wait", "raise", "run"])
@pytest.mark.parametrize(
    "idempotency_key", ["", "order:42", "x" * 256, "   "], ids=["empty", "colon", "too_long", "whitespace"]
)
async def test__coordinator__invalid_key__raises_validation_error_on_every_retry_and_never_runs_the_action(
    redis_repository: RedisAsyncIdempotencyRepository,
    fake_redis: AsyncRedisClient,
    idempotency_key: str,
    in_flight: InFlightMode,
) -> None:
    """Issue #51: a key the store cannot hold used to run the action unprotected, so every retry repeated it."""
    # Arrange
    coordinator = AsyncIdempotencyCoordinator(
        repository=redis_repository, domain_service=IdempotencyDomainService(), in_flight=in_flight
    )
    action = AsyncMock(return_value={"charge_id": "ch_1"})

    # Act & Assert
    for _ in range(2):
        with pytest.raises(IdempotencyValidationError, match="idempotency_key"):
            await coordinator.coordinate("payment.charge", idempotency_key, 3600, JsonResultAdapter(), action)
    action.assert_not_called()
    assert await fake_redis.keys("*") == []


@pytest.mark.asyncio
async def test__coordinator__invalid_operation__raises_validation_error_before_the_action(
    redis_repository: RedisAsyncIdempotencyRepository,
) -> None:
    """The operation is half of the storage key and is held to the same rules."""
    # Arrange
    coordinator = AsyncIdempotencyCoordinator(repository=redis_repository, domain_service=IdempotencyDomainService())
    action = AsyncMock()

    # Act & Assert
    with pytest.raises(IdempotencyValidationError, match="operation"):
        await coordinator.coordinate("payment:charge", "order-42", 3600, JsonResultAdapter(), action)
    action.assert_not_called()


@pytest.mark.asyncio
async def test__coordinator__disabled_with_an_invalid_key__runs_the_action_without_validating(
    mock_repo: AsyncMock, mock_domain_service: MagicMock, mock_adapter: MagicMock
) -> None:
    """enabled=False stays a pass-through: the switch does not start refusing requests."""
    # Arrange
    coordinator = AsyncIdempotencyCoordinator(repository=mock_repo, domain_service=mock_domain_service, enabled=False)
    action = AsyncMock(return_value="ran")

    # Act
    result = await coordinator.coordinate("op", "", 60, mock_adapter, action)

    # Assert
    assert result == "ran"
    action.assert_awaited_once()
