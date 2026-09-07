"""Unit tests for async idempotent decorator."""

import logging
from typing import Any
from unittest.mock import ANY, MagicMock, patch

import pytest
from pydantic import BaseModel

from idempotency_kit.core.decorators.aio.idempotent import async_idempotent
from idempotency_kit.core.exceptions import IdempotencyInProgressError
from idempotency_kit.core.fingerprint import fingerprint_of
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


class _ChargeDTO(BaseModel):
    amount: int
    currency: str = "EUR"


@pytest.mark.asyncio
async def test__decorator__fingerprint_params__hands_the_coordinator_the_fingerprint_of_the_named_arguments(
    mock_coordinator: MagicMock, mock_adapter: MagicMock
) -> None:
    """The named parameters, bound to the call, are what the request is."""
    # Arrange
    mock_coordinator.coordinate.return_value = "ok"
    dto = _ChargeDTO(amount=1999)

    @async_idempotent(operation="payment.charge", adapter=mock_adapter, fingerprint_params=("dto", "note"))
    async def charge(
        dto: _ChargeDTO, note: str = "", *, idempotency_key: str | None, coord: AsyncIdempotencyCoordinator
    ) -> str:
        return "not used"

    # Act
    result = await charge(dto, idempotency_key="order-42", coord=mock_coordinator)

    # Assert
    assert result == "ok"
    mock_coordinator.coordinate.assert_called_once_with(
        "payment.charge",
        "order-42",
        None,
        mock_adapter,
        ANY,
        dto,
        idempotency_fingerprint=fingerprint_of(dto=dto, note=""),
        idempotency_key="order-42",
        coord=mock_coordinator,
    )


@pytest.mark.asyncio
async def test__decorator__fingerprint_params__an_argument_at_its_default_and_one_left_out_agree(
    mock_coordinator: MagicMock, mock_adapter: MagicMock
) -> None:
    """Two identical requests must not disagree over how the call was spelled."""
    # Arrange
    mock_coordinator.coordinate.return_value = "ok"

    @async_idempotent(operation="payment.charge", adapter=mock_adapter, fingerprint_params=("amount", "currency"))
    async def charge(amount: int, currency: str = "EUR", *, idempotency_key: str | None, coord: Any) -> str:
        return "not used"

    # Act
    await charge(5, idempotency_key="k", coord=mock_coordinator)
    await charge(amount=5, currency="EUR", idempotency_key="k", coord=mock_coordinator)

    # Assert
    first, second = mock_coordinator.coordinate.call_args_list
    assert first.kwargs["idempotency_fingerprint"] == second.kwargs["idempotency_fingerprint"]


def test__decorator__fingerprint_params__unknown_parameter__raises_at_decoration(mock_adapter: MagicMock) -> None:
    """A programming error fails at import, not on the first request."""
    # Act & Assert
    with pytest.raises(TypeError, match=r"does not have: \['amout'\]"):

        @async_idempotent(operation="payment.charge", adapter=mock_adapter, fingerprint_params=("amout",))
        async def charge(amount: int, *, idempotency_key: str | None = None) -> str:
            return "not used"


def test__decorator__fingerprint_params__uninspectable_signature__raises_at_decoration(
    mock_adapter: MagicMock,
) -> None:
    # Act & Assert
    with (
        patch("inspect.signature", side_effect=ValueError("no signature found")),
        pytest.raises(TypeError, match="needs a signature"),
    ):

        @async_idempotent(operation="payment.charge", adapter=mock_adapter, fingerprint_params=("amount",))
        async def charge(**kwargs: Any) -> str:
            return "not used"
