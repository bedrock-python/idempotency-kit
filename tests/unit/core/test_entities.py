"""Unit tests for idempotency entities."""

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from idempotency_kit import IdempotencyRecord


def test__idempotency_record__future_expiration__is_expired_false() -> None:
    """Test that record with future expiration is not expired."""
    # Arrange
    now = datetime.now(UTC)
    record = IdempotencyRecord(
        operation="test", idempotency_key="key", result={}, created_at=now, expires_at=now + timedelta(minutes=10)
    )

    # Act & Assert
    assert not record.is_expired
    assert record.ttl_seconds > 0


def test__idempotency_record__past_expiration__is_expired_true() -> None:
    """Test that record with past expiration is expired."""
    # Arrange
    now = datetime.now(UTC)
    expired_record = IdempotencyRecord(
        operation="test",
        idempotency_key="key",
        result={},
        created_at=now - timedelta(minutes=20),
        expires_at=now - timedelta(minutes=10),
    )

    # Act & Assert
    assert expired_record.is_expired
    assert expired_record.ttl_seconds == 0


def test__idempotency_record__past_expiration__ttl_is_zero() -> None:
    """Test that expired record has TTL of 0."""
    # Arrange
    now = datetime.now(UTC)
    record = IdempotencyRecord(
        operation="test",
        idempotency_key="key",
        result={},
        created_at=now - timedelta(minutes=10),
        expires_at=now - timedelta(minutes=5),
    )

    # Act & Assert
    assert record.is_expired
    assert record.ttl_seconds == 0.0


def test__idempotency_record_create__valid_params__creates_record() -> None:
    """Test factory method creates record with correct attributes."""
    # Arrange
    operation = "user.create"
    key = "test-key"
    result: Mapping[str, str] = {"id": "123"}
    ttl = 60.0

    # Act
    record = IdempotencyRecord.create(
        operation=operation,
        idempotency_key=key,
        result=result,
        ttl_seconds=ttl,
    )

    # Assert
    assert record.operation == operation
    assert record.idempotency_key == key
    assert record.result == result
    assert not record.is_expired
    assert 0 < record.ttl_seconds <= ttl


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("operation", "test:op"),
        ("idempotency_key", "key:123"),
    ],
    ids=["operation_with_colon", "key_with_colon"],
)
def test__idempotency_record_create__colon_in_field__raises_validation_error(field: str, value: str) -> None:
    """Test that colon in operation or key raises ValidationError."""
    # Arrange
    params = {"operation": "op", "idempotency_key": "key", "result": {}, "ttl_seconds": 60}
    params[field] = value

    # Act & Assert
    with pytest.raises(ValidationError, match="cannot contain ':'"):
        IdempotencyRecord.create(**params)


@pytest.mark.parametrize(
    "result",
    [None, [], [1, "two", None], "plain", 0, {"nested": {"list": [1, 2]}}],
    ids=["null", "empty-list", "list", "string", "int", "nested-mapping"],
)
def test__idempotency_record__any_json_result__is_accepted(result: object) -> None:
    # Act
    record = IdempotencyRecord.create(operation="op", idempotency_key="key", result=result, ttl_seconds=60)

    # Assert
    assert record.result == result


def test__idempotency_record__built_without_a_status__is_completed() -> None:
    """A record is a stored result unless it says otherwise."""
    # Act
    record = IdempotencyRecord.create(operation="op", idempotency_key="key", result={"id": 1}, ttl_seconds=60)

    # Assert
    assert record.status == "completed"
    assert not record.is_pending


def test__idempotency_record__json_written_before_the_status_existed__still_decodes_as_completed() -> None:
    """Records already in Redis carry no ``status``; they must read back as the results they are."""
    # Arrange
    stored = (
        '{"operation": "op", "idempotency_key": "key", "result": {"id": 1}, '
        '"created_at": "2026-09-06T00:00:00Z", "expires_at": "2126-09-06T00:00:00Z"}'
    )

    # Act
    record = IdempotencyRecord.model_validate_json(stored)

    # Assert
    assert record.status == "completed"
    assert record.result == {"id": 1}
    assert not record.is_pending


def test__idempotency_record_pending__lease__creates_an_in_flight_reservation() -> None:
    """The reservation is the record in its pending state, with the lease as its expiry."""
    # Act
    record = IdempotencyRecord.pending(operation="op", idempotency_key="key", lease_seconds=30)

    # Assert
    assert record.is_pending
    assert record.status == "pending"
    assert record.result is None
    assert not record.is_expired
    assert 29 < record.ttl_seconds <= 30
    assert record.model_dump(mode="json")["status"] == "pending"


def test__idempotency_record__json_written_before_the_fingerprint_existed__decodes_with_none() -> None:
    """Records already in Redis carry no ``fingerprint``; they read back as records that never raise for one."""
    # Arrange
    stored = (
        '{"operation": "op", "idempotency_key": "key", "result": {"id": 1}, '
        '"created_at": "2026-09-06T00:00:00Z", "expires_at": "2126-09-06T00:00:00Z", "status": "completed"}'
    )

    # Act
    record = IdempotencyRecord.model_validate_json(stored)

    # Assert
    assert record.fingerprint is None
    assert record.result == {"id": 1}


@pytest.mark.parametrize(
    "factory",
    [
        lambda: IdempotencyRecord.create("op", "key", {"id": 1}, ttl_seconds=60, fingerprint="abc"),
        lambda: IdempotencyRecord.pending("op", "key", lease_seconds=30, fingerprint="abc"),
    ],
    ids=["create", "pending"],
)
def test__idempotency_record__fingerprint__is_carried_by_both_factories(
    factory: Callable[[], IdempotencyRecord],
) -> None:
    # Act
    record = factory()

    # Assert
    assert record.fingerprint == "abc"
    assert record.model_dump(mode="json")["fingerprint"] == "abc"
