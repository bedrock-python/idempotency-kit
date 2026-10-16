"""Reservation renewal against real Redis, with a lease short enough to run out during the test.

Regression cover for issue #52: the pending record expired a lease after the owner started,
however alive the owner was, and a duplicate that arrived meanwhile reserved the key and ran
the action a second time.
"""

import asyncio
import subprocess
import sys
import textwrap
from typing import Any
from uuid import uuid4

import pytest
from redis.asyncio import Redis as AsyncRedisClient
from testcontainers.redis import RedisContainer

from idempotency_kit import (
    AsyncIdempotencyCoordinator,
    IdempotencyDomainService,
    IdempotencyInProgressError,
    JsonResultAdapter,
)
from idempotency_kit.core.constants import InFlightMode
from idempotency_kit.infra.storage.redis.aio import RedisAsyncIdempotencyRepository

LEASE_SECONDS = 1

# A process that reserves a key with a one-second lease and runs an action far longer than
# that. It prints a line once the action has started, then either sleeps until killed or
# returns from main() while the action is still running, so that asyncio.run() cancels it.
_OWNER_SCRIPT = textwrap.dedent(
    """
    import asyncio
    import sys

    from redis.asyncio import Redis

    from idempotency_kit import AsyncIdempotencyCoordinator, IdempotencyDomainService, JsonResultAdapter
    from idempotency_kit.infra.storage.redis.aio import RedisAsyncIdempotencyRepository


    async def main(host, port, key, mode):
        redis = Redis(host=host, port=int(port))
        coordinator = AsyncIdempotencyCoordinator(
            RedisAsyncIdempotencyRepository(redis), IdempotencyDomainService(), in_flight_lease_seconds=1
        )
        started = asyncio.Event()

        async def charge():
            started.set()
            await asyncio.sleep(30)
            return "owner"

        task = asyncio.create_task(coordinator.coordinate("payment.charge", key, 3600, JsonResultAdapter(), charge))
        await started.wait()
        print("started", flush=True)
        if mode == "crash":
            await asyncio.sleep(30)
        else:
            await asyncio.sleep(0.5)  # past one renewal, then shut down with the action running


    asyncio.run(main(*sys.argv[1:]))
    """
)


def _start_owner(redis_container: RedisContainer, key: str, mode: str) -> subprocess.Popen[str]:
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    owner = subprocess.Popen(  # noqa: S603 -- our own interpreter and script
        [sys.executable, "-c", _OWNER_SCRIPT, host, str(port), key, mode],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert owner.stdout is not None
    assert owner.stdout.readline().strip() == "started"
    return owner


def _coordinator(redis_client: AsyncRedisClient, in_flight: InFlightMode = "wait") -> AsyncIdempotencyCoordinator:
    return AsyncIdempotencyCoordinator(
        RedisAsyncIdempotencyRepository(redis_client),
        IdempotencyDomainService(),
        in_flight=in_flight,
        in_flight_lease_seconds=LEASE_SECONDS,
    )


@pytest.mark.asyncio
async def test__coordinator__waiter_outlives_the_first_lease_on_real_redis__is_refused_and_the_action_runs_once(
    redis_client: AsyncRedisClient,
) -> None:
    """The reporter's scenario: a one-second lease, a three-second charge, and a duplicate 0.1 s in."""
    # Arrange
    coordinator = _coordinator(redis_client)
    key = str(uuid4())
    charges: list[str] = []

    async def charge(tag: str, hold: float) -> dict[str, str]:
        charges.append(tag)
        await asyncio.sleep(hold)
        return {"charge_id": tag}

    # Act
    first = asyncio.create_task(
        coordinator.coordinate("payment.charge", key, 3600, JsonResultAdapter(), charge, "first", 3)
    )
    await asyncio.sleep(0.1)
    with pytest.raises(IdempotencyInProgressError):
        await coordinator.coordinate("payment.charge", key, 3600, JsonResultAdapter(), charge, "second", 0)
    result = await first
    replay = await coordinator.coordinate("payment.charge", key, 3600, JsonResultAdapter(), charge, "third", 0)

    # Assert
    assert charges == ["first"]
    assert result == replay == {"charge_id": "first"}


@pytest.mark.asyncio
async def test__coordinator__callers_after_the_first_lease_on_real_redis__find_the_key_still_reserved(
    redis_client: AsyncRedisClient,
) -> None:
    """Each lease the owner outlives is renewed: callers at 1.5 s and 2.5 s still see it in flight."""
    # Arrange
    owner = _coordinator(redis_client)
    latecomer = _coordinator(redis_client, in_flight="raise")
    key = str(uuid4())
    runs: list[str] = []

    async def action(tag: str, hold: float) -> str:
        runs.append(tag)
        await asyncio.sleep(hold)
        return tag

    # Act
    first = asyncio.create_task(owner.coordinate("op", key, 3600, JsonResultAdapter(), action, "owner", 3))
    await asyncio.sleep(1.5)
    with pytest.raises(IdempotencyInProgressError):
        await latecomer.coordinate("op", key, 3600, JsonResultAdapter(), action, "at 1.5 s", 0)
    await asyncio.sleep(1)
    with pytest.raises(IdempotencyInProgressError):
        await latecomer.coordinate("op", key, 3600, JsonResultAdapter(), action, "at 2.5 s", 0)
    result = await first

    # Assert
    assert runs == ["owner"]
    assert result == "owner"
    assert await redis_client.ttl(f"idempotency:op:{key}") > 60  # the result's TTL, not a lease


@pytest.mark.asyncio
async def test__coordinator__owner_process_killed_on_real_redis__frees_the_key_within_a_lease(
    redis_client: AsyncRedisClient, redis_container: RedisContainer
) -> None:
    """Renewal lives and dies with the owner: kill it, and the next caller runs the action a lease later."""
    # Arrange
    key = str(uuid4())
    owner = _start_owner(redis_container, key, "crash")
    coordinator = _coordinator(redis_client, in_flight="raise")
    action_ran = False

    async def action() -> str:
        nonlocal action_ran
        action_ran = True
        return "recovered"

    # Act
    await asyncio.sleep(1.5)  # past the first lease: the owner is renewing
    with pytest.raises(IdempotencyInProgressError):
        await coordinator.coordinate("payment.charge", key, 3600, JsonResultAdapter(), action)
    owner.kill()
    owner.wait()
    await asyncio.sleep(LEASE_SECONDS + 0.2)
    result = await coordinator.coordinate("payment.charge", key, 3600, JsonResultAdapter(), action)

    # Assert
    assert action_ran
    assert result == "recovered"


@pytest.mark.asyncio
async def test__coordinator__owner_process_shuts_down_mid_action_on_real_redis__releases_the_key_cleanly(
    redis_client: AsyncRedisClient, redis_container: RedisContainer
) -> None:
    """asyncio.run() cancelling the action and the renewal together: the key is freed and nothing leaks."""
    # Arrange
    key = str(uuid4())

    # Act
    owner = _start_owner(redis_container, key, "shutdown")
    _, stderr = owner.communicate(timeout=10)
    await asyncio.sleep(LEASE_SECONDS / 3 + 0.2)  # long enough for a stray renewal to land

    # Assert
    assert owner.returncode == 0, stderr
    assert "Task was destroyed" not in stderr
    assert "never retrieved" not in stderr
    assert await redis_client.exists(f"idempotency:payment.charge:{key}") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["returns", "raises", "is_cancelled"])
async def test__coordinator__action_ends_on_real_redis__no_renewal_lands_afterwards(
    redis_client: AsyncRedisClient, outcome: str
) -> None:
    """The renewal is over before the result is written or the key released, whichever way the action ends."""
    # Arrange
    coordinator = _coordinator(redis_client)
    key = str(uuid4())
    storage_key = f"idempotency:op:{key}"

    async def action() -> str:
        await asyncio.sleep(LEASE_SECONDS * 1.2)  # several renewals
        if outcome == "raises":
            raise RuntimeError("provider down")
        return "done"

    # Act
    task = asyncio.create_task(coordinator.coordinate("op", key, 3600, JsonResultAdapter(), action))
    if outcome == "is_cancelled":
        await asyncio.sleep(LEASE_SECONDS * 0.8)
        task.cancel()
    outcomes: list[Any] = await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(LEASE_SECONDS / 3 + 0.2)

    # Assert
    if outcome == "returns":
        assert outcomes == ["done"]
        stored = await RedisAsyncIdempotencyRepository(redis_client).get("op", key)
        assert stored is not None
        assert stored.status == "completed"
    else:
        assert isinstance(outcomes[0], RuntimeError | asyncio.CancelledError)
        assert await redis_client.exists(storage_key) == 0
