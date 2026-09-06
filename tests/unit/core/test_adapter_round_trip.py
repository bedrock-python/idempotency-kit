"""Every shipped ResultAdapter must survive a real coordinator + repository round trip.

Regression cover for issue #6: ``VoidResultAdapter`` could never be stored (``None`` failed the
record's ``Mapping`` type) and a stored ``null`` read back as a cache miss, so the guarded
operation re-executed on every call.
"""

from typing import Any
from unittest.mock import MagicMock

import pytest
from fakeredis import FakeAsyncRedis as AsyncRedisClient
from pydantic import BaseModel

from idempotency_kit import (
    AsyncIdempotencyCoordinator,
    IdempotencyDomainService,
    IdempotencyMetricsProtocol,
    JsonResultAdapter,
    PydanticResultAdapter,
    ResultAdapter,
    VoidResultAdapter,
)
from idempotency_kit.infra.storage.redis.aio import RedisAsyncIdempotencyRepository


class _Order(BaseModel):
    id: int
    status: str


class _Ack(BaseModel):
    """A model that dumps to an empty mapping -- an acknowledgement carrying no fields."""


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
        (PydanticResultAdapter(_Ack), _Ack()),
    ],
    ids=[
        "void",
        "json-mapping",
        "json-list",
        "json-null",
        "json-string",
        "pydantic-model",
        "pydantic-empty-model",
    ],
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


@pytest.mark.asyncio
async def test__coordinator__pydantic_adapter_on_a_none_result__stores_nothing_and_reports_it(
    fake_redis: AsyncRedisClient,
) -> None:
    """A None the adapter cannot represent must not become a record that never decodes again."""
    # Arrange
    metrics = MagicMock(spec=IdempotencyMetricsProtocol)
    repository = RedisAsyncIdempotencyRepository(fake_redis, key_prefix="probe:")
    coordinator = AsyncIdempotencyCoordinator(
        repository=repository, domain_service=IdempotencyDomainService(), metrics=metrics
    )
    calls = 0

    async def action() -> Any:
        nonlocal calls
        calls += 1
        return None

    # Act
    first = await coordinator.coordinate("op.absent", "key-1", 600, PydanticResultAdapter(_Order), action)
    second = await coordinator.coordinate("op.absent", "key-1", 600, PydanticResultAdapter(_Order), action)

    # Assert
    assert (first, second) == (None, None)
    assert calls == 2
    assert await fake_redis.get("probe:op.absent:key-1") is None
    assert metrics.record_error.call_args_list == [
        (("op.absent", "record_validation_error"),),
        (("op.absent", "record_validation_error"),),
    ]


def test__pydantic_adapter__null_payload__raises() -> None:
    """A stored null is the one payload this adapter cannot turn back into a model."""
    # Arrange
    adapter: PydanticResultAdapter[_Order] = PydanticResultAdapter(_Order)

    # Act & Assert
    with pytest.raises(ValueError, match="null idempotency payload"):
        adapter.decode(None)
