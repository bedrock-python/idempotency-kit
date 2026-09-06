"""The in-flight reservation: what a second caller gets while the first one's action is still running.

Regression cover for issue #26: two ``coordinate()`` calls with the same key, the second starting
while the first's action was still running, both ran the action -- the check was a ``GET`` and the
write a ``SET NX`` after the action, and nothing marked the key as taken in between.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import orjson
import pytest
from fakeredis import FakeAsyncRedis as AsyncRedisClient

from idempotency_kit import (
    AsyncIdempotencyCoordinator,
    IdempotencyDomainService,
    IdempotencyInProgressError,
    IdempotencyKeyCollisionError,
    IdempotencyMetricsProtocol,
    IdempotencyRecord,
    IdempotencyValidationError,
    JsonResultAdapter,
)
from idempotency_kit.core.constants import InFlightMode
from idempotency_kit.infra.storage.redis.aio import RedisAsyncIdempotencyRepository

COORDINATOR_MODULE = "idempotency_kit.core.services.aio.coordinator"


class _Provider:
    """A payment provider that remembers every charge it made, the way the reporter's script does."""

    def __init__(self, delay: float = 0.2) -> None:
        self.charges: list[str] = []
        self._delay = delay

    async def charge(self, amount: int) -> dict[str, Any]:
        await asyncio.sleep(self._delay)
        self.charges.append(f"ch_{len(self.charges) + 1}")
        return {"charge_id": self.charges[-1], "amount": amount}


async def _two_callers_50ms_apart(
    coordinator: AsyncIdempotencyCoordinator, provider: _Provider
) -> list[dict[str, Any] | BaseException]:
    first = asyncio.create_task(
        coordinator.coordinate("payment.charge", "order-42", 3600, JsonResultAdapter(), provider.charge, 1999)
    )
    await asyncio.sleep(0.05)
    second = asyncio.create_task(
        coordinator.coordinate("payment.charge", "order-42", 3600, JsonResultAdapter(), provider.charge, 1999)
    )
    return await asyncio.gather(first, second, return_exceptions=True)


@pytest.mark.asyncio
async def test__coordinator__second_caller_while_the_first_is_in_flight__waits_and_replays_without_running_the_action(
    redis_repository: RedisAsyncIdempotencyRepository,
) -> None:
    """The reporter's scenario: the card is charged once and both callers get that charge."""
    # Arrange
    coordinator = AsyncIdempotencyCoordinator(repository=redis_repository, domain_service=IdempotencyDomainService())
    provider = _Provider()

    # Act
    results = await _two_callers_50ms_apart(coordinator, provider)

    # Assert
    assert provider.charges == ["ch_1"]
    assert results == [{"charge_id": "ch_1", "amount": 1999}, {"charge_id": "ch_1", "amount": 1999}]
    stored = await redis_repository.get("payment.charge", "order-42")
    assert stored is not None
    assert stored.status == "completed"


@pytest.mark.asyncio
async def test__coordinator__in_flight_raise__second_caller_is_refused_and_the_action_runs_once(
    redis_repository: RedisAsyncIdempotencyRepository,
) -> None:
    """The 409 shape: the retry is told the original is still running instead of waiting for it."""
    # Arrange
    coordinator = AsyncIdempotencyCoordinator(
        repository=redis_repository, domain_service=IdempotencyDomainService(), in_flight="raise"
    )
    provider = _Provider()

    # Act
    results = await _two_callers_50ms_apart(coordinator, provider)

    # Assert
    assert provider.charges == ["ch_1"]
    assert results[0] == {"charge_id": "ch_1", "amount": 1999}
    assert isinstance(results[1], IdempotencyInProgressError)
    assert (results[1].operation, results[1].key) == ("payment.charge", "order-42")


@pytest.mark.asyncio
async def test__coordinator__in_flight_run__both_callers_run_the_action_and_the_loser_adopts_the_winner_result(
    redis_repository: RedisAsyncIdempotencyRepository,
) -> None:
    """The flow before reservations existed, kept for actions that are safe to repeat."""
    # Arrange
    coordinator = AsyncIdempotencyCoordinator(
        repository=redis_repository, domain_service=IdempotencyDomainService(), in_flight="run"
    )
    provider = _Provider()

    # Act
    results = await _two_callers_50ms_apart(coordinator, provider)

    # Assert
    assert provider.charges == ["ch_1", "ch_2"]
    assert results == [{"charge_id": "ch_1", "amount": 1999}, {"charge_id": "ch_1", "amount": 1999}]


@pytest.mark.asyncio
async def test__coordinator__in_flight_wait__one_miss_one_collision_one_hit(
    redis_repository: RedisAsyncIdempotencyRepository,
) -> None:
    """The runner is a miss; the waiter records a collision on finding the marker and a hit on getting the record."""
    # Arrange
    metrics = MagicMock(spec=IdempotencyMetricsProtocol)
    coordinator = AsyncIdempotencyCoordinator(
        repository=redis_repository, domain_service=IdempotencyDomainService(), metrics=metrics
    )
    provider = _Provider()

    # Act
    await _two_callers_50ms_apart(coordinator, provider)

    # Assert
    assert metrics.record_miss.call_args_list == [(("payment.charge",),)]
    assert metrics.record_collision.call_args_list == [(("payment.charge",),)]
    assert metrics.record_hit.call_args_list == [(("payment.charge",),)]
    assert metrics.record_error.call_count == 0
    assert {call.args[1] for call in metrics.record_latency.call_args_list} == {"reserve", "save", "get"}


@pytest.mark.asyncio
async def test__coordinator__action_raises__releases_the_reservation_so_the_retry_runs_again(
    redis_repository: RedisAsyncIdempotencyRepository, fake_redis: AsyncRedisClient
) -> None:
    """Failures are not cached, and neither is the reservation of a failed action."""
    # Arrange
    coordinator = AsyncIdempotencyCoordinator(repository=redis_repository, domain_service=IdempotencyDomainService())
    calls = 0

    async def flaky() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("provider down")
        return "charged"

    # Act
    with pytest.raises(RuntimeError, match="provider down"):
        await coordinator.coordinate("op", "key", 600, JsonResultAdapter(), flaky)
    left_behind = await fake_redis.get("probe:op:key")
    retried = await coordinator.coordinate("op", "key", 600, JsonResultAdapter(), flaky)

    # Assert
    assert left_behind is None
    assert retried == "charged"
    assert calls == 2


@pytest.mark.asyncio
async def test__coordinator__action_cancelled__releases_the_reservation(
    redis_repository: RedisAsyncIdempotencyRepository, fake_redis: AsyncRedisClient
) -> None:
    """A cancelled action did not complete as far as anyone knows; the key must not stay taken."""
    # Arrange
    coordinator = AsyncIdempotencyCoordinator(repository=redis_repository, domain_service=IdempotencyDomainService())

    async def slow() -> str:
        await asyncio.sleep(10)
        return "never"

    # Act
    task = asyncio.create_task(coordinator.coordinate("op", "key", 600, JsonResultAdapter(), slow))
    await asyncio.sleep(0.05)
    assert await fake_redis.get("probe:op:key") is not None
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Assert
    assert await fake_redis.get("probe:op:key") is None


@pytest.mark.asyncio
async def test__coordinator__holder_fails_while_a_caller_waits__the_waiter_runs_the_action(
    redis_repository: RedisAsyncIdempotencyRepository,
) -> None:
    """When the marker disappears, the waiter does not give up: it takes the key and runs the action itself."""
    # Arrange
    coordinator = AsyncIdempotencyCoordinator(repository=redis_repository, domain_service=IdempotencyDomainService())
    calls = 0

    async def action() -> str:
        nonlocal calls
        calls += 1
        attempt = calls
        await asyncio.sleep(0.1)
        if attempt == 1:
            raise RuntimeError("first attempt failed")
        return "second attempt"

    # Act
    first = asyncio.create_task(coordinator.coordinate("op", "key", 600, JsonResultAdapter(), action))
    await asyncio.sleep(0.05)
    second = asyncio.create_task(coordinator.coordinate("op", "key", 600, JsonResultAdapter(), action))
    results = await asyncio.gather(first, second, return_exceptions=True)

    # Assert
    assert isinstance(results[0], RuntimeError)
    assert results[1] == "second attempt"
    assert calls == 2


@pytest.mark.asyncio
async def test__coordinator__pending_record_past_its_lease__counts_as_absent_and_the_action_runs(
    redis_repository: RedisAsyncIdempotencyRepository, fake_redis: AsyncRedisClient
) -> None:
    """A worker that crashed mid-action cannot wedge the key beyond its lease."""
    # Arrange
    coordinator = AsyncIdempotencyCoordinator(repository=redis_repository, domain_service=IdempotencyDomainService())
    now = datetime.now(UTC)
    abandoned = IdempotencyRecord(
        operation="op",
        idempotency_key="key",
        result=None,
        created_at=now - timedelta(seconds=60),
        expires_at=now - timedelta(seconds=30),
        status="pending",
    )
    await fake_redis.set("probe:op:key", orjson.dumps(abandoned.model_dump(mode="json")))
    action = AsyncMock(return_value="fresh")

    # Act
    result = await coordinator.coordinate("op", "key", 600, JsonResultAdapter(), action)

    # Assert
    assert result == "fresh"
    action.assert_awaited_once()
    stored = await redis_repository.get("op", "key")
    assert stored is not None
    assert (stored.status, stored.result) == ("completed", "fresh")


@pytest.mark.asyncio
async def test__coordinator__in_flight_wait__still_pending_after_a_whole_lease__raises_in_progress(
    mock_repo: AsyncMock, mock_adapter: MagicMock
) -> None:
    """The wait is bounded by the lease; past it the caller is refused rather than kept forever."""
    # Arrange
    coordinator = AsyncIdempotencyCoordinator(
        repository=mock_repo, domain_service=IdempotencyDomainService(), in_flight_lease_seconds=30
    )
    mock_repo.save.side_effect = IdempotencyKeyCollisionError("op", "key")
    mock_repo.get.return_value = IdempotencyRecord.pending("op", "key", lease_seconds=3600)
    action = AsyncMock()

    # Act & Assert
    with (
        patch(f"{COORDINATOR_MODULE}.time.monotonic", side_effect=[0.0, 10.0, 31.0]),
        patch(f"{COORDINATOR_MODULE}.asyncio.sleep", new=AsyncMock()) as sleep,
        pytest.raises(IdempotencyInProgressError),
    ):
        await coordinator.coordinate("op", "key", 60, mock_adapter, action)
    action.assert_not_called()
    sleep.assert_awaited_once()


@pytest.mark.asyncio
async def test__coordinator__storage_error_on_reserve__runs_the_action_unreserved(
    mock_repo: AsyncMock, mock_adapter: MagicMock
) -> None:
    """Availability over exactly-once, as on every other storage failure; and nothing is released that was not taken."""
    # Arrange
    metrics = MagicMock(spec=IdempotencyMetricsProtocol)
    coordinator = AsyncIdempotencyCoordinator(
        repository=mock_repo, domain_service=IdempotencyDomainService(), metrics=metrics
    )
    mock_repo.save.side_effect = Exception("redis down")
    action = AsyncMock(return_value={"data": "ok"})

    # Act
    result = await coordinator.coordinate("op", "key", 60, mock_adapter, action)

    # Assert
    assert result == {"data": "ok"}
    action.assert_awaited_once()
    mock_repo.replace.assert_awaited_once()
    mock_repo.delete.assert_not_called()
    metrics.record_error.assert_called_once_with("op", "storage_reserve_error")


@pytest.mark.asyncio
async def test__coordinator__undecodable_record_under_the_key__runs_the_action_and_replaces_it(
    mock_repo: AsyncMock, mock_adapter: MagicMock
) -> None:
    """A record this adapter cannot read is a miss, and the fresh result heals it instead of re-running until expiry."""
    # Arrange
    coordinator = AsyncIdempotencyCoordinator(repository=mock_repo, domain_service=IdempotencyDomainService())
    mock_repo.save.side_effect = IdempotencyKeyCollisionError("op", "key")
    mock_repo.get.return_value = IdempotencyRecord.create("op", "key", {"old": "shape"}, ttl_seconds=600)
    mock_adapter.decode.side_effect = Exception("decode failed")
    action = AsyncMock(return_value={"data": "fresh"})

    # Act
    result = await coordinator.coordinate("op", "key", 60, mock_adapter, action)

    # Assert
    assert result == {"data": "fresh"}
    action.assert_awaited_once()
    mock_repo.replace.assert_awaited_once()
    mock_repo.delete.assert_not_called()


@pytest.mark.asyncio
async def test__coordinator__release_fails__the_action_error_still_propagates(
    mock_repo: AsyncMock, mock_adapter: MagicMock
) -> None:
    """Storage trouble on the way out is logged and counted; the caller sees the action's own failure."""
    # Arrange
    metrics = MagicMock(spec=IdempotencyMetricsProtocol)
    coordinator = AsyncIdempotencyCoordinator(
        repository=mock_repo, domain_service=IdempotencyDomainService(), metrics=metrics
    )
    mock_repo.delete.side_effect = Exception("redis down")
    action = AsyncMock(side_effect=RuntimeError("provider down"))

    # Act & Assert
    with pytest.raises(RuntimeError, match="provider down"):
        await coordinator.coordinate("op", "key", 60, mock_adapter, action)
    metrics.record_error.assert_called_once_with("op", "storage_release_error")


@pytest.mark.asyncio
async def test__coordinator__in_flight_run__pending_record_from_a_reserving_peer__runs_the_action(
    mock_repo: AsyncMock, mock_adapter: MagicMock
) -> None:
    """A coordinator in ``run`` mode meeting a marker (a mixed rollout) runs and keeps its own result."""
    # Arrange
    coordinator = AsyncIdempotencyCoordinator(
        repository=mock_repo, domain_service=IdempotencyDomainService(), in_flight="run"
    )
    mock_repo.get.return_value = IdempotencyRecord.pending("op", "key", lease_seconds=30)
    mock_repo.save.side_effect = IdempotencyKeyCollisionError("op", "key")
    action = AsyncMock(return_value={"data": "mine"})

    # Act
    result = await coordinator.coordinate("op", "key", 60, mock_adapter, action)

    # Assert
    assert result == {"data": "mine"}
    action.assert_awaited_once()
    mock_adapter.decode.assert_not_called()


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"in_flight": "block"}, "in_flight must be one of"),
        ({"in_flight_lease_seconds": 0}, "in_flight_lease_seconds must be >= 1"),
    ],
    ids=["unknown_mode", "zero_lease"],
)
def test__coordinator__invalid_in_flight_settings__raise_validation_error(
    mock_repo: AsyncMock, kwargs: dict[str, Any], match: str
) -> None:
    # Act & Assert
    with pytest.raises(IdempotencyValidationError, match=match):
        AsyncIdempotencyCoordinator(repository=mock_repo, domain_service=IdempotencyDomainService(), **kwargs)


@pytest.mark.parametrize("in_flight", ["wait", "raise"])
def test__coordinator__repository_without_replace__raises_type_error_unless_in_flight_is_run(
    in_flight: InFlightMode,
) -> None:
    """A repository written against the old protocol fails at construction, not as a 30 s hang on every retry."""
    # Arrange
    legacy_repository = MagicMock(spec=["get", "save", "delete", "get_many", "save_many", "delete_many"])

    # Act & Assert
    with pytest.raises(TypeError, match="has no replace"):
        AsyncIdempotencyCoordinator(
            repository=legacy_repository, domain_service=IdempotencyDomainService(), in_flight=in_flight
        )
    AsyncIdempotencyCoordinator(
        repository=legacy_repository, domain_service=IdempotencyDomainService(), in_flight="run"
    )
