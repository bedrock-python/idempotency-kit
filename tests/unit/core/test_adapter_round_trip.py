"""Every shipped ResultAdapter must survive a real coordinator + repository round trip.

Regression cover for issue #6: ``VoidResultAdapter`` could never be stored (``None`` failed the
record's ``Mapping`` type) and a stored ``null`` read back as a cache miss, so the guarded
operation re-executed on every call.
"""

from typing import Any

import pytest
from fakeredis import FakeAsyncRedis as AsyncRedisClient
from pydantic import BaseModel

from idempotency_kit import (
    AsyncIdempotencyCoordinator,
    IdempotencyDomainService,
    JsonResultAdapter,
    PydanticResultAdapter,
    ResultAdapter,
    VoidResultAdapter,
)
from idempotency_kit.infra.storage.redis.aio import RedisAsyncIdempotencyRepository


class _Order(BaseModel):
    id: int
    status: str


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("adapter", "value"),
    [
        (VoidResultAdapter(), None),
        (JsonResultAdapter(), {"data": "ok"}),
        (JsonResultAdapter(), [1, "two", None]),
        (JsonResultAdapter(), None),
        (JsonResultAdapter(), "plain string"),
        (PydanticResultAdapter(_Order), _Order(id=1, status="paid")),
    ],
    ids=["void", "json-mapping", "json-list", "json-null", "json-string", "pydantic-model"],
)
async def test__coordinator__shipped_adapter_round_trip__executes_once_and_replays_the_stored_result(
    fake_redis: AsyncRedisClient, adapter: ResultAdapter[Any], value: Any
) -> None:
    # Arrange
    repository = RedisAsyncIdempotencyRepository(fake_redis, key_prefix="probe:")
    coordinator = AsyncIdempotencyCoordinator(repository=repository, domain_service=IdempotencyDomainService())
    calls = 0

    async def action() -> Any:
        nonlocal calls
        calls += 1
        return value

    # Act
    first = await coordinator.coordinate("op.round-trip", "key-1", 600, adapter, action)
    second = await coordinator.coordinate("op.round-trip", "key-1", 600, adapter, action)

    # Assert
    assert calls == 1
    assert first == value
    assert second == value
    assert await fake_redis.get("probe:op.round-trip:key-1") is not None
