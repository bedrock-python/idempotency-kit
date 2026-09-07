"""The request fingerprint: a reused key with a different payload is a caller error, not a replay.

Regression cover for issue #27: after a charge of 1999 was recorded under a key, the same key came
back with amount 5 and got the first charge back, with no signal that anything was off.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import orjson
import pytest
from fakeredis import FakeAsyncRedis as AsyncRedisClient
from pydantic import BaseModel

from idempotency_kit import (
    AsyncIdempotencyCoordinator,
    IdempotencyDomainService,
    IdempotencyKeyCollisionError,
    IdempotencyKeyReuseError,
    IdempotencyMetricsProtocol,
    IdempotencyRecord,
    JsonResultAdapter,
    fingerprint_of,
)
from idempotency_kit.infra.storage.redis.aio import RedisAsyncIdempotencyRepository


class _Charge(BaseModel):
    amount: int
    currency: str = "EUR"


async def _charge_card(amount: int) -> dict[str, int]:
    return {"amount": amount}


@pytest.mark.asyncio
async def test__coordinator__reused_key_with_a_different_fingerprint__raises_key_reuse_without_running_the_action(
    redis_repository: RedisAsyncIdempotencyRepository,
) -> None:
    """The reporter's scenario: 1999 was charged under the key, and the key comes back for 5."""
    # Arrange
    coordinator = AsyncIdempotencyCoordinator(repository=redis_repository, domain_service=IdempotencyDomainService())
    action = AsyncMock(side_effect=_charge_card)
    await coordinator.coordinate(
        "payment.charge",
        "order-42",
        3600,
        JsonResultAdapter(),
        action,
        1999,
        idempotency_fingerprint=fingerprint_of(amount=1999),
    )

    # Act
    with pytest.raises(IdempotencyKeyReuseError) as exc_info:
        await coordinator.coordinate(
            "payment.charge",
            "order-42",
            3600,
            JsonResultAdapter(),
            action,
            5,
            idempotency_fingerprint=fingerprint_of(amount=5),
        )

    # Assert
    assert action.await_count == 1
    assert (exc_info.value.operation, exc_info.value.key) == ("payment.charge", "order-42")
    assert exc_info.value.stored_fingerprint == fingerprint_of(amount=1999)
    assert exc_info.value.fingerprint == fingerprint_of(amount=5)
    stored = await redis_repository.get("payment.charge", "order-42")
    assert stored is not None
    assert stored.result == {"amount": 1999}


@pytest.mark.asyncio
async def test__coordinator__reused_key_with_the_same_fingerprint__replays(
    redis_repository: RedisAsyncIdempotencyRepository,
) -> None:
    # Arrange
    coordinator = AsyncIdempotencyCoordinator(repository=redis_repository, domain_service=IdempotencyDomainService())
    action = AsyncMock(side_effect=_charge_card)
    fingerprint = fingerprint_of(amount=1999)

    # Act
    first = await coordinator.coordinate(
        "payment.charge", "order-42", 3600, JsonResultAdapter(), action, 1999, idempotency_fingerprint=fingerprint
    )
    second = await coordinator.coordinate(
        "payment.charge", "order-42", 3600, JsonResultAdapter(), action, 1999, idempotency_fingerprint=fingerprint
    )

    # Assert
    assert first == second == {"amount": 1999}
    assert action.await_count == 1


@pytest.mark.asyncio
async def test__coordinator__no_fingerprint__replays_on_the_key_alone_as_before(
    redis_repository: RedisAsyncIdempotencyRepository,
) -> None:
    """Nothing changes for a caller who does not opt in: the key is the whole identity."""
    # Arrange
    coordinator = AsyncIdempotencyCoordinator(repository=redis_repository, domain_service=IdempotencyDomainService())
    action = AsyncMock(side_effect=_charge_card)

    # Act
    await coordinator.coordinate("payment.charge", "order-42", 3600, JsonResultAdapter(), action, 1999)
    replay = await coordinator.coordinate("payment.charge", "order-42", 3600, JsonResultAdapter(), action, 5)

    # Assert
    assert replay == {"amount": 1999}
    assert action.await_count == 1


@pytest.mark.asyncio
async def test__coordinator__record_without_a_fingerprint__replays_for_a_caller_with_one(
    redis_repository: RedisAsyncIdempotencyRepository, fake_redis: AsyncRedisClient
) -> None:
    """A record from before fingerprints existed, or from a caller without one, never raises."""
    # Arrange
    coordinator = AsyncIdempotencyCoordinator(repository=redis_repository, domain_service=IdempotencyDomainService())
    now = datetime.now(UTC)
    legacy = {
        "operation": "payment.charge",
        "idempotency_key": "order-42",
        "result": {"amount": 1999},
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=1)).isoformat(),
    }
    await fake_redis.set("probe:payment.charge:order-42", orjson.dumps(legacy))
    action = AsyncMock(side_effect=_charge_card)

    # Act
    replay = await coordinator.coordinate(
        "payment.charge",
        "order-42",
        3600,
        JsonResultAdapter(),
        action,
        5,
        idempotency_fingerprint=fingerprint_of(amount=5),
    )

    # Assert
    assert replay == {"amount": 1999}
    action.assert_not_awaited()


@pytest.mark.asyncio
async def test__coordinator__caller_without_a_fingerprint__replays_a_record_that_has_one(
    redis_repository: RedisAsyncIdempotencyRepository,
) -> None:
    # Arrange
    coordinator = AsyncIdempotencyCoordinator(repository=redis_repository, domain_service=IdempotencyDomainService())
    action = AsyncMock(side_effect=_charge_card)
    await coordinator.coordinate(
        "payment.charge",
        "order-42",
        3600,
        JsonResultAdapter(),
        action,
        1999,
        idempotency_fingerprint=fingerprint_of(amount=1999),
    )

    # Act
    replay = await coordinator.coordinate("payment.charge", "order-42", 3600, JsonResultAdapter(), action, 5)

    # Assert
    assert replay == {"amount": 1999}
    assert action.await_count == 1


@pytest.mark.asyncio
async def test__coordinator__pending_record_with_a_different_fingerprint__refuses_the_waiter_at_once(
    redis_repository: RedisAsyncIdempotencyRepository,
) -> None:
    """A second caller with another payload is told now, not handed someone else's result after waiting."""
    # Arrange
    coordinator = AsyncIdempotencyCoordinator(repository=redis_repository, domain_service=IdempotencyDomainService())
    calls = 0

    async def slow_charge(amount: int) -> dict[str, int]:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.2)
        return {"amount": amount}

    # Act
    first = asyncio.create_task(
        coordinator.coordinate(
            "payment.charge",
            "order-42",
            3600,
            JsonResultAdapter(),
            slow_charge,
            1999,
            idempotency_fingerprint=fingerprint_of(amount=1999),
        )
    )
    await asyncio.sleep(0.05)
    with pytest.raises(IdempotencyKeyReuseError):
        await coordinator.coordinate(
            "payment.charge",
            "order-42",
            3600,
            JsonResultAdapter(),
            slow_charge,
            5,
            idempotency_fingerprint=fingerprint_of(amount=5),
        )

    # Assert
    assert await first == {"amount": 1999}
    assert calls == 1


@pytest.mark.asyncio
async def test__coordinator__key_reuse__is_counted_as_an_error_and_the_key_is_left_alone(
    redis_repository: RedisAsyncIdempotencyRepository,
) -> None:
    """A client reusing keys is worth an alert; the record it collided with is untouched."""
    # Arrange
    metrics = MagicMock(spec=IdempotencyMetricsProtocol)
    coordinator = AsyncIdempotencyCoordinator(
        repository=redis_repository, domain_service=IdempotencyDomainService(), metrics=metrics
    )
    await coordinator.coordinate(
        "op", "key", 3600, JsonResultAdapter(), _charge_card, 1999, idempotency_fingerprint=fingerprint_of(amount=1999)
    )

    # Act
    with pytest.raises(IdempotencyKeyReuseError):
        await coordinator.coordinate(
            "op", "key", 3600, JsonResultAdapter(), _charge_card, 5, idempotency_fingerprint=fingerprint_of(amount=5)
        )

    # Assert
    metrics.record_error.assert_called_once_with("op", "key_reuse")
    metrics.record_hit.assert_not_called()
    stored = await redis_repository.get("op", "key")
    assert stored is not None
    assert stored.fingerprint == fingerprint_of(amount=1999)


@pytest.mark.asyncio
async def test__coordinator__in_flight_run__record_with_a_different_fingerprint__raises_before_the_action(
    mock_repo: AsyncMock, mock_adapter: MagicMock
) -> None:
    """The check is on the read, so the unreserved flow refuses the call before any side effect."""
    # Arrange
    coordinator = AsyncIdempotencyCoordinator(
        repository=mock_repo, domain_service=IdempotencyDomainService(), in_flight="run"
    )
    mock_repo.get.return_value = IdempotencyRecord.create("op", "key", {"amount": 1999}, 600, fingerprint="a")
    action = AsyncMock()

    # Act & Assert
    with pytest.raises(IdempotencyKeyReuseError):
        await coordinator.coordinate("op", "key", 60, mock_adapter, action, idempotency_fingerprint="b")
    action.assert_not_awaited()


@pytest.mark.asyncio
async def test__coordinator__in_flight_run__collision_with_a_different_fingerprint__keeps_own_result(
    mock_repo: AsyncMock, mock_adapter: MagicMock
) -> None:
    """After the action has run, a mismatch is logged and counted; raising would make the caller retry a done deed."""
    # Arrange
    metrics = MagicMock(spec=IdempotencyMetricsProtocol)
    coordinator = AsyncIdempotencyCoordinator(
        repository=mock_repo, domain_service=IdempotencyDomainService(), metrics=metrics, in_flight="run"
    )
    winner = IdempotencyRecord.create("op", "key", {"amount": 1999}, 600, fingerprint="a")
    mock_repo.get.side_effect = [None, winner]
    mock_repo.save.side_effect = IdempotencyKeyCollisionError("op", "key")
    action = AsyncMock(return_value={"amount": 5})

    # Act
    result = await coordinator.coordinate("op", "key", 60, mock_adapter, action, idempotency_fingerprint="b")

    # Assert
    assert result == {"amount": 5}
    metrics.record_error.assert_called_once_with("op", "key_reuse")


def test__fingerprint_of__equal_values_in_another_order__agree() -> None:
    # Act & Assert
    assert fingerprint_of(a={"x": 1, "y": 2}, b=[1, 2]) == fingerprint_of(b=[1, 2], a={"y": 2, "x": 1})


def test__fingerprint_of__different_values__differ() -> None:
    # Act & Assert
    assert fingerprint_of(amount=1999) != fingerprint_of(amount=5)
    assert fingerprint_of(amount=5) != fingerprint_of(amount="5")


@pytest.mark.parametrize(
    ("value", "same_as"),
    [
        (_Charge(amount=5), {"amount": 5, "currency": "EUR"}),
        (UUID("12345678-1234-5678-1234-567812345678"), "12345678-1234-5678-1234-567812345678"),
        (Decimal("19.99"), "19.99"),
        (datetime(2026, 9, 7, tzinfo=UTC), "2026-09-07T00:00:00Z"),
    ],
    ids=["pydantic-model", "uuid", "decimal", "datetime"],
)
def test__fingerprint_of__non_json_values__hash_as_their_json_form(value: Any, same_as: Any) -> None:
    # Act & Assert
    assert fingerprint_of(v=value) == fingerprint_of(v=same_as)


def test__fingerprint_of__is_a_sha256_hex_digest() -> None:
    # Act
    fingerprint = fingerprint_of(amount=1999)

    # Assert
    assert len(fingerprint) == 64
    assert int(fingerprint, 16) >= 0
