"""``on_storage_error``: what a call gets when the store fails before the action runs.

Regression cover for issue #53: with the store unreachable, every call ran the action
unprotected, and there was no way to refuse instead.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from idempotency_kit import (
    AsyncIdempotencyCoordinator,
    IdempotencyDomainService,
    IdempotencyError,
    IdempotencyKeyCollisionError,
    IdempotencyMetricsProtocol,
    IdempotencyRecord,
    IdempotencyStorageError,
    IdempotencyValidationError,
    JsonResultAdapter,
)
from idempotency_kit.core.constants import InFlightMode
from idempotency_kit.infra.storage.redis.aio import RedisAsyncIdempotencyRepository


@pytest.mark.asyncio
async def test__coordinator__on_storage_error_raise__unreachable_redis__raises_and_never_runs_the_action() -> None:
    """The reporter's scenario: two calls with the same key and Redis down charge nobody."""
    # Arrange
    unreachable = Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.2, retry=Retry(NoBackoff(), 0))
    coordinator = AsyncIdempotencyCoordinator(
        RedisAsyncIdempotencyRepository(unreachable),
        IdempotencyDomainService(),
        in_flight="raise",
        on_storage_error="raise",
    )
    action = AsyncMock(return_value={"charge_id": "ch_1"})

    # Act & Assert
    try:
        for _ in range(2):
            with pytest.raises(IdempotencyStorageError) as raised:
                await coordinator.coordinate("payment.charge", "same-key", 3600, JsonResultAdapter(), action)
            assert raised.value.operation == "payment.charge"
            assert raised.value.original_error is not None
    finally:
        await unreachable.aclose()
    action.assert_not_called()


@pytest.mark.asyncio
async def test__coordinator__on_storage_error_default__unreachable_redis__runs_the_action() -> None:
    """The default stays fail-open: availability over exactly-once."""
    # Arrange
    unreachable = Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.2, retry=Retry(NoBackoff(), 0))
    coordinator = AsyncIdempotencyCoordinator(RedisAsyncIdempotencyRepository(unreachable), IdempotencyDomainService())
    action = AsyncMock(return_value={"charge_id": "ch_1"})

    # Act
    try:
        for _ in range(2):
            await coordinator.coordinate("payment.charge", "same-key", 3600, JsonResultAdapter(), action)
    finally:
        await unreachable.aclose()

    # Assert
    assert action.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("in_flight", ["wait", "raise"])
async def test__coordinator__on_storage_error_raise__read_after_a_contested_reservation_fails__raises(
    mock_repo: AsyncMock, mock_adapter: MagicMock, in_flight: InFlightMode
) -> None:
    """The key is taken and the store fails on the read that would say by what: nothing runs."""
    # Arrange
    coordinator = AsyncIdempotencyCoordinator(
        repository=mock_repo, domain_service=IdempotencyDomainService(), in_flight=in_flight, on_storage_error="raise"
    )
    mock_repo.save.side_effect = IdempotencyKeyCollisionError("op", "key")
    failure = IdempotencyStorageError("redis down", operation="op")
    mock_repo.get.side_effect = failure
    action = AsyncMock()

    # Act & Assert
    with pytest.raises(IdempotencyStorageError) as raised:
        await coordinator.coordinate("op", "key", 60, mock_adapter, action)
    assert raised.value is failure
    action.assert_not_called()
    mock_repo.delete.assert_not_called()


@pytest.mark.asyncio
async def test__coordinator__on_storage_error_raise__in_flight_run__failed_read_raises(
    mock_repo: AsyncMock, mock_adapter: MagicMock
) -> None:
    """The unreserved flow's first touch of the store is the read, and that is where it refuses."""
    # Arrange
    coordinator = AsyncIdempotencyCoordinator(
        repository=mock_repo, domain_service=IdempotencyDomainService(), in_flight="run", on_storage_error="raise"
    )
    mock_repo.get.side_effect = IdempotencyStorageError("redis down", operation="op")
    action = AsyncMock()

    # Act & Assert
    with pytest.raises(IdempotencyStorageError):
        await coordinator.coordinate("op", "key", 60, mock_adapter, action)
    action.assert_not_called()


@pytest.mark.asyncio
async def test__coordinator__on_storage_error_raise__failed_reservation__counts_and_raises(
    mock_repo: AsyncMock, mock_adapter: MagicMock
) -> None:
    """The refusal is still a storage error on the dashboards."""
    # Arrange
    metrics = MagicMock(spec=IdempotencyMetricsProtocol)
    coordinator = AsyncIdempotencyCoordinator(
        repository=mock_repo, domain_service=IdempotencyDomainService(), metrics=metrics, on_storage_error="raise"
    )
    mock_repo.save.side_effect = IdempotencyStorageError("redis down", operation="op")
    action = AsyncMock()

    # Act & Assert
    with pytest.raises(IdempotencyStorageError):
        await coordinator.coordinate("op", "key", 60, mock_adapter, action)
    action.assert_not_called()
    metrics.record_error.assert_called_once_with("op", "storage_reserve_error")


@pytest.mark.asyncio
async def test__coordinator__on_storage_error_raise__driver_error_from_a_custom_repository__is_wrapped(
    mock_repo: AsyncMock, mock_adapter: MagicMock
) -> None:
    """A repository that lets the driver's exception through still fails closed, as IdempotencyStorageError."""
    # Arrange
    coordinator = AsyncIdempotencyCoordinator(
        repository=mock_repo, domain_service=IdempotencyDomainService(), on_storage_error="raise"
    )
    failure = ConnectionError("connection refused")
    mock_repo.save.side_effect = failure
    action = AsyncMock()

    # Act & Assert
    with pytest.raises(IdempotencyStorageError) as raised:
        await coordinator.coordinate("op", "key", 60, mock_adapter, action)
    assert raised.value.original_error is failure
    assert raised.value.__cause__ is failure
    action.assert_not_called()


@pytest.mark.asyncio
async def test__coordinator__on_storage_error_raise__unreadable_record__runs_the_action_and_replaces_it(
    mock_repo: AsyncMock, mock_adapter: MagicMock
) -> None:
    """A store that answered with a corrupted record is not an outage: the key heals instead of failing every retry."""
    # Arrange
    coordinator = AsyncIdempotencyCoordinator(
        repository=mock_repo, domain_service=IdempotencyDomainService(), on_storage_error="raise"
    )
    mock_repo.save.side_effect = IdempotencyKeyCollisionError("op", "key")
    mock_repo.get.side_effect = IdempotencyError("Corrupted idempotency record for op:key")
    action = AsyncMock(return_value={"data": "fresh"})

    # Act
    result = await coordinator.coordinate("op", "key", 60, mock_adapter, action)

    # Assert
    assert result == {"data": "fresh"}
    action.assert_awaited_once()
    mock_repo.replace.assert_awaited_once()


@pytest.mark.asyncio
async def test__coordinator__on_storage_error_raise__save_fails_after_the_action__returns_the_result(
    mock_repo: AsyncMock, mock_adapter: MagicMock
) -> None:
    """The effect has happened; raising now would make the client retry a completed operation."""
    # Arrange
    coordinator = AsyncIdempotencyCoordinator(
        repository=mock_repo, domain_service=IdempotencyDomainService(), on_storage_error="raise"
    )
    mock_repo.replace.side_effect = IdempotencyStorageError("redis down", operation="op")
    mock_repo.delete.side_effect = IdempotencyStorageError("redis down", operation="op")
    action = AsyncMock(return_value={"data": "done"})

    # Act
    result = await coordinator.coordinate("op", "key", 60, mock_adapter, action)

    # Assert
    assert result == {"data": "done"}
    action.assert_awaited_once()


@pytest.mark.asyncio
async def test__coordinator__on_storage_error_raise__healthy_store__replays_as_usual(
    redis_repository: RedisAsyncIdempotencyRepository,
) -> None:
    """The mode changes nothing while the store answers."""
    # Arrange
    coordinator = AsyncIdempotencyCoordinator(
        repository=redis_repository, domain_service=IdempotencyDomainService(), on_storage_error="raise"
    )
    action = AsyncMock(return_value={"charge_id": "ch_1"})

    # Act
    first = await coordinator.coordinate("payment.charge", "order-42", 3600, JsonResultAdapter(), action)
    second = await coordinator.coordinate("payment.charge", "order-42", 3600, JsonResultAdapter(), action)

    # Assert
    assert first == second == {"charge_id": "ch_1"}
    action.assert_awaited_once()
    stored = await redis_repository.get("payment.charge", "order-42")
    assert isinstance(stored, IdempotencyRecord)


def test__coordinator__unknown_on_storage_error__raises_validation_error(mock_repo: AsyncMock) -> None:
    # Arrange
    kwargs: dict[str, Any] = {"on_storage_error": "fail"}

    # Act & Assert
    with pytest.raises(IdempotencyValidationError, match="on_storage_error must be one of"):
        AsyncIdempotencyCoordinator(repository=mock_repo, domain_service=IdempotencyDomainService(), **kwargs)
