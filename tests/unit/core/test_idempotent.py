"""Unit tests for async idempotent decorator."""

import logging
from typing import Any
from unittest.mock import ANY, MagicMock, patch

import pytest

from idempotency_kit.core.decorators.aio.idempotent import async_idempotent
from idempotency_kit.core.exceptions import IdempotencyInProgressError
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


@pytest.mark.asyncio
async def test__decorator__positional_key__calls_coordinator(
    mock_coordinator: MagicMock, mock_adapter: MagicMock
) -> None:
    """A key the caller passes positionally must still reach the coordinator."""
    # Arrange
    operation = "test.op"
    key = "test-key"
    mock_coordinator.coordinate.return_value = "ok"

    @async_idempotent(operation=operation, adapter=mock_adapter)
    async def my_func(idempotency_key: str | None, coord: AsyncIdempotencyCoordinator) -> str:
        return "not used"

    # Act
    result = await my_func(key, mock_coordinator)

    # Assert
    assert result == "ok"
    mock_coordinator.coordinate.assert_called_once_with(
        operation,
        key,
        None,
        mock_adapter,
        ANY,  # the original function
        key,
        mock_coordinator,
    )


@pytest.mark.asyncio
async def test__decorator__keyword_only_key__is_not_read_from_positional_arguments(
    mock_coordinator: MagicMock, mock_adapter: MagicMock
) -> None:
    """A keyword-only parameter cannot arrive positionally, so nothing else may be read as the key."""
    # Arrange
    mock_coordinator.coordinate.return_value = "ok"

    @async_idempotent(operation="test.op", adapter=mock_adapter)
    async def my_func(payload: str, *, idempotency_key: str | None = None) -> str:
        return "direct"

    # Act
    result = await my_func("payload")

    # Assert
    assert result == "direct"
    mock_coordinator.coordinate.assert_not_called()


@pytest.mark.asyncio
async def test__decorator__no_coordinator__warns(mock_adapter: MagicMock, caplog: pytest.LogCaptureFixture) -> None:
    """Running unprotected is a fallback, not a silent one."""
    # Arrange
    operation = "test.op"

    @async_idempotent(operation=operation, adapter=mock_adapter)
    async def my_func(*, idempotency_key: str | None = None) -> str:
        return "direct"

    # Act
    with caplog.at_level(logging.WARNING, logger="idempotency_kit.core.decorators.aio.idempotent"):
        result = await my_func(idempotency_key="test-key")

    # Assert
    assert result == "direct"
    assert [record.message for record in caplog.records] == [
        "No idempotency coordinator found; running the operation without idempotency"
    ]
    assert caplog.records[0].operation == operation  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test__decorator__infra_param_on_self__calls_coordinator(
    mock_coordinator: MagicMock, mock_adapter: MagicMock
) -> None:
    """infra_param names an attribute of the instance, not only a keyword argument."""
    # Arrange
    mock_coordinator.coordinate.return_value = "ok"

    class MyService:
        def __init__(self, coordinator: AsyncIdempotencyCoordinator) -> None:
            self._idempotency = coordinator

        @async_idempotent(operation="test.op", adapter=mock_adapter, infra_param="_idempotency")
        async def my_method(self, *, idempotency_key: str | None = None) -> str:
            return "not used"

    service = MyService(mock_coordinator)

    # Act
    result = await service.my_method(idempotency_key="test-key")

    # Assert
    assert result == "ok"
    mock_coordinator.coordinate.assert_called_once()


@pytest.mark.asyncio
async def test__decorator__uninspectable_signature__still_reads_the_key_from_kwargs(
    mock_coordinator: MagicMock, mock_adapter: MagicMock
) -> None:
    """A callable inspect cannot describe keeps the keyword-argument lookup."""
    # Arrange
    mock_coordinator.coordinate.return_value = "ok"

    with patch("inspect.signature", side_effect=ValueError("no signature found")):

        @async_idempotent(operation="test.op", adapter=mock_adapter)
        async def my_func(**kwargs: Any) -> str:
            return "not used"

    # Act
    result = await my_func(idempotency_key="test-key", coord=mock_coordinator)

    # Assert
    assert result == "ok"
    mock_coordinator.coordinate.assert_called_once()


@pytest.mark.asyncio
async def test__decorator__key_in_flight__lets_the_refusal_through(
    mock_coordinator: MagicMock, mock_adapter: MagicMock
) -> None:
    """A second caller refused by the coordinator is refused by the decorated function too; it is the caller's 409."""
    # Arrange
    mock_coordinator.coordinate.side_effect = IdempotencyInProgressError("test.op", "test-key")

    @async_idempotent(operation="test.op", adapter=mock_adapter)
    async def my_func(*, idempotency_key: str | None = None, coord: AsyncIdempotencyCoordinator) -> str:
        return "not used"

    # Act & Assert
    with pytest.raises(IdempotencyInProgressError):
        await my_func(idempotency_key="test-key", coord=mock_coordinator)
