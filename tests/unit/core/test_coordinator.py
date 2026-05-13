"""Unit tests for async idempotency coordinator."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from idempotency_kit.core.exceptions import IdempotencyKeyCollisionError
from idempotency_kit.core.models.entities import IdempotencyRecord
from idempotency_kit.core.services.aio.coordinator import AsyncIdempotencyCoordinator


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
