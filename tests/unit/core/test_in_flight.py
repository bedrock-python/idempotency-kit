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
    IdempotencyStorageError,
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


class _RenewalFailingRepository(RedisAsyncIdempotencyRepository):
    """Fails the first ``fail`` renewals -- a ``replace`` of a pending record; completions go through."""

    def __init__(self, redis: AsyncRedisClient, *, fail: int) -> None:
        super().__init__(redis, key_prefix="probe:")
        self.renewals = 0
        self._fail = fail

    async def replace(self, record: IdempotencyRecord) -> None:
        if record.is_pending:
            self.renewals += 1
            if self.renewals <= self._fail:
                raise IdempotencyStorageError("redis down", operation=record.operation)
        await super().replace(record)


async def _owner_and_a_latecomer(
    repository: RedisAsyncIdempotencyRepository, *, hold: float, late_by: float, metrics: Any = None
) -> tuple[list[str], list[str | BaseException]]:
    """An owner with a one-second lease holding the key for ``hold`` seconds, and a caller arriving ``late_by`` in."""
    owner = AsyncIdempotencyCoordinator(
        repository=repository, domain_service=IdempotencyDomainService(), in_flight_lease_seconds=1, metrics=metrics
    )
    latecomer = AsyncIdempotencyCoordinator(
        repository=repository, domain_service=IdempotencyDomainService(), in_flight="raise", in_flight_lease_seconds=1
    )
    runs: list[str] = []

    async def action(tag: str, seconds: float) -> str:
        runs.append(tag)
        await asyncio.sleep(seconds)
        return tag

    first = asyncio.create_task(owner.coordinate("op", "key", 600, JsonResultAdapter(), action, "owner", hold))
    await asyncio.sleep(late_by)
    second = asyncio.create_task(latecomer.coordinate("op", "key", 600, JsonResultAdapter(), action, "latecomer", 0))
    results: list[str | BaseException] = await asyncio.gather(first, second, return_exceptions=True)
    return runs, results


@pytest.mark.asyncio
async def test__coordinator__action_outlives_its_lease__the_reservation_is_renewed_and_a_latecomer_is_refused(
    redis_repository: RedisAsyncIdempotencyRepository,
) -> None:
    """Issue #52: a live owner's reservation no longer expires a lease after it started."""
    # Act
    runs, results = await _owner_and_a_latecomer(redis_repository, hold=1.6, late_by=1.3)

    # Assert
    assert runs == ["owner"]
    assert results[0] == "owner"
    assert isinstance(results[1], IdempotencyInProgressError)


@pytest.mark.asyncio
async def test__coordinator__a_renewal_fails__it_is_counted_and_the_next_one_keeps_the_reservation(
    fake_redis: AsyncRedisClient,
) -> None:
    """One lost renewal is absorbed: the next comes a third of a lease later, well before the expiry."""
    # Arrange
    repository = _RenewalFailingRepository(fake_redis, fail=1)
    metrics = MagicMock(spec=IdempotencyMetricsProtocol)

    # Act
    runs, results = await _owner_and_a_latecomer(repository, hold=1.6, late_by=1.3, metrics=metrics)

    # Assert
    assert runs == ["owner"]
    assert results[0] == "owner"
    assert isinstance(results[1], IdempotencyInProgressError)
    metrics.record_error.assert_called_once_with("op", "storage_renew_error")


@pytest.mark.asyncio
async def test__coordinator__every_renewal_fails__the_reservation_lapses_and_the_owner_still_completes(
    fake_redis: AsyncRedisClient,
) -> None:
    """A store down for a whole lease loses the reservation, as before renewal; the owner's action is not cut short."""
    # Arrange
    repository = _RenewalFailingRepository(fake_redis, fail=100)

    # Act
    runs, results = await _owner_and_a_latecomer(repository, hold=1.6, late_by=1.3)

    # Assert
    assert runs == ["owner", "latecomer"]
    assert results == ["owner", "latecomer"]
    assert repository.renewals >= 3


class _SlowRenewalRepository(RedisAsyncIdempotencyRepository):
    """A renewal that is already on the wire: it lands 0.3 s later even if the caller stops waiting for it."""

    async def replace(self, record: IdempotencyRecord) -> None:
        if record.is_pending:
            await asyncio.shield(self._land_later(record))
            return
        await super().replace(record)

    async def _land_later(self, record: IdempotencyRecord) -> None:
        await asyncio.sleep(0.3)
        await super().replace(record)


@pytest.mark.asyncio
async def test__coordinator__renewal_in_flight_when_the_action_ends__lands_before_the_result_not_over_it(
    fake_redis: AsyncRedisClient,
) -> None:
    """Cancelling the renewal would let its write land after the completed record and put the key back in flight."""
    # Arrange
    repository = _SlowRenewalRepository(fake_redis, key_prefix="probe:")
    coordinator = AsyncIdempotencyCoordinator(
        repository=repository, domain_service=IdempotencyDomainService(), in_flight_lease_seconds=1
    )

    async def action() -> str:
        await asyncio.sleep(0.5)  # ends while the renewal sent at 1/3 s is still on its way
        return "done"

    # Act
    result = await coordinator.coordinate("op", "key", 600, JsonResultAdapter(), action)
    await asyncio.sleep(0.4)

    # Assert
    assert result == "done"
    stored = await repository.get("op", "key")
    assert stored is not None
    assert (stored.status, stored.result) == ("completed", "done")
