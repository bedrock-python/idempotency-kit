"""Unit tests for idempotency entities."""

from collections.abc import Mapping
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
