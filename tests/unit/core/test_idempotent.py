"""Unit tests for async idempotent decorator."""

from unittest.mock import ANY, MagicMock

import pytest

from idempotency_kit.core.decorators.aio.idempotent import async_idempotent
from idempotency_kit.core.services.aio.coordinator import AsyncIdempotencyCoordinator


@pytest.mark.asyncio
async def test__decorator__coordinator_from_params__calls_coordinator(
    mock_coordinator: MagicMock, mock_adapter: MagicMock
) -> None:
    """Test that decorator identifies coordinator from function parameters."""
    # Arrange
    operation = "test.op"
    key = "test-key"
    expected_result = "ok"
    mock_coordinator.coordinate.return_value = expected_result

    @async_idempotent(operation=operation, adapter=mock_adapter)
    async def my_func(idempotency_key: str | None, coord: AsyncIdempotencyCoordinator) -> str:
        return "not used"

    # Act
    result = await my_func(idempotency_key=key, coord=mock_coordinator)

    # Assert
    assert result == expected_result
    mock_coordinator.coordinate.assert_called_once_with(
        operation,
        key,
        None,
        mock_adapter,
        ANY,  # the original function
        idempotency_key=key,
        coord=mock_coordinator,
    )


@pytest.mark.asyncio
async def test__decorator__custom_infra_param__calls_coordinator(
    mock_coordinator: MagicMock, mock_adapter: MagicMock
) -> None:
    """Test that decorator identifies coordinator by custom infra_param name."""
    # Arrange
    operation = "test.op"
    key = "test-key"
    mock_coordinator.coordinate.return_value = "ok"

    @async_idempotent(operation=operation, adapter=mock_adapter, infra_param="my_coord")
    async def my_func(idempotency_key: str | None, my_coord: AsyncIdempotencyCoordinator) -> str:
        return "not used"

    # Act
    result = await my_func(idempotency_key=key, my_coord=mock_coordinator)

    # Assert
    assert result == "ok"
    mock_coordinator.coordinate.assert_called_once()


@pytest.mark.asyncio
async def test__decorator__coordinator_from_self__calls_coordinator(
    mock_coordinator: MagicMock, mock_adapter: MagicMock
) -> None:
    """Test that decorator identifies coordinator from self attribute (DI pattern)."""
    # Arrange
    operation = "test.op"
    key = "test-key"
    mock_coordinator.coordinate.return_value = "ok"

    class MyService:
        def __init__(self, coord: AsyncIdempotencyCoordinator) -> None:
            self.coord = coord

        @async_idempotent(operation=operation, adapter=mock_adapter)
        async def my_method(self, idempotency_key: str | None) -> str:
            return "not used"

    service = MyService(mock_coordinator)

    # Act
    result = await service.my_method(idempotency_key=key)

    # Assert
    assert result == "ok"
    mock_coordinator.coordinate.assert_called_once()


@pytest.mark.asyncio
async def test__decorator__no_key__executes_directly(mock_coordinator: MagicMock, mock_adapter: MagicMock) -> None:
    """Test that missing idempotency key bypasses coordinator."""
    # Arrange
    operation = "test.op"

    @async_idempotent(operation=operation, adapter=mock_adapter)
    async def my_func(idempotency_key: str | None) -> str:
        return "direct"

    # Act
    result = await my_func(idempotency_key=None)

    # Assert
    assert result == "direct"
    mock_coordinator.coordinate.assert_not_called()


@pytest.mark.asyncio
async def test__decorator__no_coordinator__executes_directly(mock_adapter: MagicMock) -> None:
    """Test that missing coordinator causes direct function execution."""
    # Arrange
    operation = "test.op"
    key = "test-key"

    @async_idempotent(operation=operation, adapter=mock_adapter)
    async def my_func(idempotency_key: str | None) -> str:
        return "direct"

    # Act
    result = await my_func(idempotency_key=key)

    # Assert
    assert result == "direct"
