"""Unit tests for domain service."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from idempotency_kit import (
    IdempotencyDomainService,
    IdempotencyInvalidTTLError,
    IdempotencyRecord,
    IdempotencyRecordExpiredError,
    IdempotencyValidationError,
)
from idempotency_kit.core.protocols.metrics import NoOpIdempotencyMetrics


def test__domain_service__valid_input__creates_record() -> None:
    """Test that valid input creates record successfully."""
    # Arrange
    service = IdempotencyDomainService()

    # Act
    record = service.create_record(
        operation="user.create",
        idempotency_key="test-key-123",
        result={"user_id": str(uuid4()), "username": "testuser"},
    )

    # Assert
    assert record.operation == "user.create"
    assert record.idempotency_key == "test-key-123"
    assert not record.is_expired
    assert record.ttl_seconds > 0


def test__domain_service__custom_ttl__applies_custom_ttl() -> None:
    """Test that custom TTL is applied to created record."""
    # Arrange
    service = IdempotencyDomainService()

    # Act
    record = service.create_record(
        operation="user.create",
        idempotency_key="test-key-123",
        result={"user_id": str(uuid4())},
        ttl_minutes=60,
    )

    # Assert
    assert record.ttl_seconds > 3500  # Should be close to 60 minutes


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("operation", "  "),
        ("idempotency_key", "  "),
    ],
    ids=["empty_operation", "empty_key"],
)
def test__domain_service__empty_field__raises_validation_error(field: str, value: str) -> None:
    """Test that empty operation or key raises ValidationError."""
    # Arrange
    service = IdempotencyDomainService()
    params = {"operation": "test", "idempotency_key": "key", "result": {}}
    params[field] = value

    # Act & Assert
    with pytest.raises(IdempotencyValidationError, match=field):
        service.create_record(**params)


@pytest.mark.parametrize(
    ("ttl_minutes", "reason"),
    [
        (0, "below_minimum"),
        (2000, "above_maximum"),
    ],
    ids=["ttl_zero", "ttl_too_large"],
)
def test__domain_service__invalid_ttl__raises_invalid_ttl_error(ttl_minutes: int, reason: str) -> None:
    """Test that TTL outside allowed range raises IdempotencyInvalidTTLError."""
    # Arrange
    service = IdempotencyDomainService()

    # Act & Assert
    with pytest.raises(IdempotencyInvalidTTLError):
        service.create_record(
            operation="test",
            idempotency_key="key",
            result={},
            ttl_minutes=ttl_minutes,
        )


def test__domain_service__expired_record__raises_expired_error() -> None:
    """Test that validating expired record raises IdempotencyRecordExpiredError."""
    # Arrange
    service = IdempotencyDomainService()
    now = datetime.now(UTC)
    record = IdempotencyRecord(
        operation="test",
        idempotency_key="key",
        result={},
        created_at=now - timedelta(minutes=20),
        expires_at=now - timedelta(minutes=10),
    )

    # Act & Assert
    with pytest.raises(IdempotencyRecordExpiredError):
        service.validate_record(record)


def test__domain_service__valid_record__validates_successfully() -> None:
    """Test that valid record passes validation without error."""
    # Arrange
    service = IdempotencyDomainService()
    record = service.create_record("test", "key", {})

    # Act & Assert
    service.validate_record(record)  # Should not raise


@pytest.mark.parametrize(
    ("init_param", "init_value", "error_match"),
    [
        ("default_ttl_minutes", 0, "default_ttl_minutes must be >= 1"),
        ("min_ttl_seconds", 0, "min_ttl_seconds must be >= 1"),
    ],
    ids=["zero_default_ttl", "zero_min_ttl"],
)
def test__domain_service__invalid_init_param__raises_validation_error(
    init_param: str, init_value: int, error_match: str
) -> None:
    """Test that invalid initialization parameters raise ValidationError."""
    # Arrange
    params = {init_param: init_value}

    # Act & Assert
    with pytest.raises(IdempotencyValidationError, match=error_match):
        IdempotencyDomainService(**params)


def test__domain_service__min_greater_than_max__raises_validation_error() -> None:
    """Test that min_ttl_seconds > max_ttl_seconds raises ValidationError."""
    # Arrange & Act & Assert
    with pytest.raises(IdempotencyValidationError, match=r"max_ttl_seconds .* must be >= min_ttl_seconds"):
        IdempotencyDomainService(min_ttl_seconds=100, max_ttl_seconds=50)


@pytest.mark.parametrize(
    ("default_ttl_minutes", "min_ttl_seconds", "max_ttl_seconds"),
    [
        (10, 1200, None),  # default 600s < min 1200s
        (60, None, 1800),  # default 3600s > max 1800s
    ],
    ids=["default_below_min", "default_above_max"],
)
def test__domain_service__default_ttl_out_of_bounds__raises_validation_error(
    default_ttl_minutes: int, min_ttl_seconds: int | None, max_ttl_seconds: int | None
) -> None:
    """Test that default TTL outside min/max range raises ValidationError."""
    # Arrange
    params = {"default_ttl_minutes": default_ttl_minutes}
    if min_ttl_seconds is not None:
        params["min_ttl_seconds"] = min_ttl_seconds
    if max_ttl_seconds is not None:
        params["max_ttl_seconds"] = max_ttl_seconds

    # Act & Assert
    with pytest.raises(IdempotencyValidationError, match=r"Default TTL .* is outside allowed range"):
        IdempotencyDomainService(**params)


def test__domain_service__operation_too_long__raises_validation_error() -> None:
    """Test that operation exceeding max length raises ValidationError."""
    # Arrange
    service = IdempotencyDomainService()

    # Act & Assert
    with pytest.raises(IdempotencyValidationError, match="operation"):
        service.create_record("a" * 101, "key", {})


@pytest.mark.parametrize(
    ("operation", "key"),
    [
        ("test:op", "key"),
        ("op", "key:123"),
    ],
    ids=["operation_with_colon", "key_with_colon"],
)
def test__domain_service__colon_in_field__raises_validation_error(operation: str, key: str) -> None:
    """Test that colon in operation or key raises ValidationError."""
    # Arrange
    service = IdempotencyDomainService()

    # Act & Assert
    with pytest.raises(IdempotencyValidationError, match="cannot contain ':'"):
        service.create_record(operation, key, {})


def test__domain_service__minimum_ttl__creates_record() -> None:
    """Test that minimum TTL (1 minute) creates valid record."""
    # Arrange
    service = IdempotencyDomainService(min_ttl_seconds=1)

    # Act
    record = service.create_record("test", "key", {}, ttl_minutes=1)

    # Assert
    assert record.ttl_seconds > 0


def test__domain_service__whitespace_in_fields__strips_whitespace() -> None:
    """Test that whitespace is stripped from operation and key."""
    # Arrange
    service = IdempotencyDomainService()

    # Act
    record = service.create_record("  test  ", "  key  ", {})

    # Assert
    assert record.operation == "test"
    assert record.idempotency_key == "key"


def test__domain_service__custom_default_ttl__applies_custom_default() -> None:
    """Test that custom default TTL is used when not specified."""
    # Arrange
    service = IdempotencyDomainService(default_ttl_minutes=120)

    # Act
    record = service.create_record("test", "key", {})

    # Assert
    assert 7100 < record.ttl_seconds <= 7200  # Should be around 120 minutes


def test__noop_metrics__all_methods__do_not_raise() -> None:
    """Test that NoOpIdempotencyMetrics methods execute without errors."""
    # Arrange
    metrics = NoOpIdempotencyMetrics()

    # Act & Assert - Should not raise any exceptions
    metrics.record_hit("op")
    metrics.record_miss("op")
    metrics.record_collision("op")
    metrics.record_error("op", "type")
    metrics.record_latency("op", "method", 0.1)
    metrics.record_bulk_hit("op", 5)
    metrics.record_bulk_miss("op", 5)
